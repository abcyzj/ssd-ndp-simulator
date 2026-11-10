"""Independent readers for Capture IR/2 and simulation report/1."""
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing


class InputError(ValueError):
    """An input artifact does not match its declared format or identity."""


def _require(condition, message):
    if not condition:
        raise InputError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _decode(raw, where):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise InputError(f"{where}: invalid JSON") from exc
    _require(isinstance(value, dict), f"{where}: expected JSON object")
    return value


def _records(path, *, configuration=False):
    with path.open("rb") as stream:
        number = 0
        while True:
            limit = (64 if configuration and number == 0 else 16) << 20
            offset = stream.tell()
            line = stream.readline(limit + 1)
            if not line:
                break
            number += 1
            where = f"{path}:{number}"
            _require(len(line) <= limit and line.endswith(b"\n"), f"{where}: invalid JSONL line size")
            yield offset, len(line), _decode(line, where)


def _file(root, name):
    _require(isinstance(name, str), f"{root}: missing artifact path")
    path = root / name
    _require(not Path(name).is_absolute() and path.resolve().is_relative_to(root),
             f"{root}: artifact path escapes input directory: {name}")
    return path


def _output(path, inputs):
    path = Path(path).resolve()
    _require(not any(path.is_relative_to(item) for item in inputs),
             f"{path}: cache must be outside input directories")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache(path, build):
    # Publish only a committed index. Concurrent builders for the same
    # identity each use a separate file and publish equivalent contents.
    if not path.exists():
        fd, name = tempfile.mkstemp(prefix=".index-", dir=path.parent)
        os.close(fd)
        try:
            with closing(sqlite3.connect(name)) as db, db:
                build(db)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    return path


def _open(path):
    return closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))


def _uint(value, where):
    if isinstance(value, str):
        _require(value.isascii() and value.isdecimal() and len(value) <= 20,
                 f"{where}: expected uint64")
        value = int(value)
    _require(type(value) is int and 0 <= value < 1 << 64, f"{where}: expected uint64")
    return value


def _key(record, where):
    query = _uint(record.get("query"), where)
    op, stage = record.get("op"), record.get("stage")
    _require(isinstance(op, str) and op and isinstance(stage, str) and stage,
             f"{where}: invalid stage key")
    return query, op, stage


def _sql_key(key):
    query, op, stage = key
    return str(query), op, stage


def _identity(digests):
    payload = b"ndpsim-static-ir/2\0" + b"".join(
        bytes.fromhex(digests[name]) for name in ("manifest.json", "index.jsonl", "operations.jsonl"))
    return hashlib.sha256(payload).hexdigest()


class Package:
    """Verified package identity and disk-backed query lookup.

    query(id) returns original operation/stage metadata, in deterministic
    topological order. All dependencies remain on each stage.
    """

    def __init__(self, directory, cache_directory):
        self.directory = Path(directory).resolve()
        manifest_path = _file(self.directory, "manifest.json")
        manifest_bytes = manifest_path.read_bytes()
        self.manifest = _decode(manifest_bytes, manifest_path)
        _require(self.manifest.get("format") == "ndpsim-static-ir/2",
                 f"{manifest_path}: expected Capture IR/2")
        self.digests = {"manifest.json": hashlib.sha256(manifest_bytes).hexdigest()}
        for entry in self.manifest["files"]:
            name = entry["path"]
            path = _file(self.directory, name)
            digest = _digest(path)
            _require(name not in self.digests and path.stat().st_size == entry["bytes"]
                     and digest == entry["sha256"], f"{path}: manifest size/hash mismatch")
            self.digests[name] = digest
        _require({"index.jsonl", "operations.jsonl"} <= self.digests.keys(),
                 f"{manifest_path}: required operation or index file absent")
        self.identity = _identity(self.digests)
        tail_path = _file(self.directory, "tail.json")
        tail_bytes = tail_path.read_bytes()
        tail = _decode(tail_bytes, tail_path)
        self.digests["tail.json"] = hashlib.sha256(tail_bytes).hexdigest()
        _require(tail.get("format") == "ndpsim-static-ir-tail/2"
                 and tail.get("complete") is True and tail.get("lost_records") == 0
                 and tail.get("package_sha256") == self.identity
                 and all(tail.get(name) == self.manifest[name] for name in ("counts", "terminals")),
                 f"{tail_path}: incomplete or mismatched package tail")
        self.query_ids = tuple(_uint(q, manifest_path) for q in self.manifest["provenance"]["query_ids"])
        _require(len(set(self.query_ids)) == len(self.query_ids), f"{manifest_path}: duplicate query ID")
        self._operations = _file(self.directory, "operations.jsonl")
        self._index = _file(self.directory, "index.jsonl")
        output = _output(cache_directory, (self.directory,))
        self.cache_path = _cache(output / f"ir-v1-{self.identity}.sqlite", self._build)

    def _build(self, db):
        db.executescript("""
            create table ops (query text, op text, offset integer, length integer, primary key(query, op));
            create table stages (
                query text, op text, stage text, rank integer, digest text, deps text,
                indexed integer default 0, primary key(query, op, stage), unique(query, rank)
            );
            create table edges (query text, op text, stage text, source_op text, source_stage text);
        """)
        query_ids = set(self.query_ids)
        for offset, length, op in _records(self._operations):
            query = _uint(op.get("query"), self._operations)
            identifier = op.get("id")
            _require(query in query_ids and isinstance(identifier, str) and identifier,
                     f"{self._operations}: invalid operation identity {query}/{identifier}")
            try:
                db.execute("insert into ops values (?,?,?,?)", (str(query), identifier, offset, length))
            except sqlite3.IntegrityError as exc:
                raise InputError(f"{self._operations}: duplicate operation {(query, identifier)}") from exc
            stages = op.get("stages")
            _require(isinstance(stages, list) and stages,
                     f"{self._operations}: operation has no stages {(query, identifier)}")
            for stage in stages:
                key = _key(dict(query=query, op=identifier, stage=stage.get("id")), self._operations)
                deps = stage.get("dependencies")
                _require(isinstance(deps, list), f"{self._operations}: invalid dependencies {key}")
                rank = _uint(stage.get("topological_rank"), key)
                digest = hashlib.sha256((_canonical(stage) + "\n").encode("ascii")).hexdigest()
                try:
                    db.execute("insert into stages(query,op,stage,rank,digest,deps) values (?,?,?,?,?,?)",
                               (*_sql_key(key), rank, digest, _canonical(deps)))
                except (sqlite3.IntegrityError, OverflowError) as exc:
                    raise InputError(f"{self._operations}: duplicate stage key or invalid/duplicate rank {key}") from exc
                for dependency in deps:
                    _require(dependency.get("kind") in {"DATA", "ADDRESS", "CONTROL", "ORDER"},
                             f"{self._operations}: invalid dependency type at {key}")
                    source = _key(dependency["source"]["key"], key)
                    _require(source[0] == query, f"{self._operations}: cross-query dependency {source} -> {key}")
                    db.execute("insert into edges values (?,?,?,?,?)", (*_sql_key(key), *source[1:]))
        for _, _, row in _records(self._index):
            key = _key(row["key"], self._index)
            data = db.execute("""
                select s.rank, s.digest, s.deps, o.offset, o.length, s.indexed
                from stages s join ops o using(query, op)
                where s.query=? and s.op=? and s.stage=?
            """, _sql_key(key)).fetchone()
            _require(data is not None, f"{self._index}: index references missing stage {key}")
            _require(not data[-1], f"{self._index}: duplicate stage key {key}")
            expected = (row.get("topological_rank"), row.get("stage_sha256"),
                        _canonical(row.get("dependencies")), row.get("offset_bytes"), row.get("length_bytes"))
            _require(row.get("file") == "operations.jsonl" and expected == data[:-1],
                     f"{self._index}: index location, rank, digest or dependencies mismatch at {key}")
            db.execute("update stages set indexed=1 where query=? and op=? and stage=?", _sql_key(key))
        missing = db.execute("select query,op,stage from stages where not indexed limit 1").fetchone()
        _require(missing is None, f"{self._index}: missing index row {missing}")
        bad = db.execute("""
            select e.query,e.source_op,e.source_stage,e.op,e.stage,src.rank
            from edges e
            join stages dst on (dst.query,dst.op,dst.stage)=(e.query,e.op,e.stage)
            left join stages src on (src.query,src.op,src.stage)=(e.query,e.source_op,e.source_stage)
            where src.rank is null or src.rank >= dst.rank limit 1
        """).fetchone()
        _require(bad is None, f"{self._operations}: dangling dependency or invalid topological rank {bad}")
        _require(len(self.query_ids) == self.manifest["counts"]["queries"],
                 f"{self._operations}: incorrect query count")
        for terminal in self.manifest["terminals"]:
            key = _key(terminal, self._operations)
            _require(db.execute("select 1 from stages where query=? and op=? and stage=?",
                                _sql_key(key)).fetchone() is not None,
                     f"{self._operations}: dangling terminal {key}")
        for table, count in (("ops", "operations"), ("stages", "stages"), ("edges", "dependencies")):
            total = db.execute(f"select count(*) from {table}").fetchone()[0]
            _require(total == self.manifest["counts"][count], f"{self._operations}: incorrect {count} count")

    def query(self, query_id):
        query_id = _uint(query_id, "query ID")
        _require(query_id in self.query_ids, f"{self.directory}: unknown original query ID {query_id}")
        operations = []
        with _open(self.cache_path) as db, self._operations.open("rb") as stream:
            for offset, length in db.execute("select offset,length from ops where query=?", (str(query_id),)):
                stream.seek(offset)
                operation = _decode(stream.read(length), self._operations)
                operation["stages"].sort(key=lambda stage: stage["topological_rank"])
                operations.append(operation)
        return sorted(operations, key=lambda op: op["stages"][0]["topological_rank"])


class Run:
    """Simulation observations bound to a verified Package.

    request(id) returns the source request record, or None if unobserved.
    stage_timings(id) maps (query, op, stage) to timing and status.
    events() streams the source trace for resource-specific filtering.
    """

    def __init__(self, package, report, cache_directory):
        self.package = package
        self.report_path = Path(report).resolve()
        directory = self.report_path.parent
        report_bytes = self.report_path.read_bytes()
        self.report = _decode(report_bytes, self.report_path)
        _require(self.report.get("format") == "ndpsim-run-report/1",
                 f"{report}: expected simulation run report/1")
        _require(self.report.get("package_sha256") == package.identity,
                 f"{report}: report package identity does not match {package.identity}")
        details = self.report.get("details")
        _require(isinstance(details, dict), f"{report}: missing report details")
        _require(type(self.report.get("execution_succeeded")) is bool,
                 f"{report}: missing execution status")
        self.trace_path = _file(directory, details.get("all_resources"))
        self.stages_path = _file(directory, details["stages"]) if details.get("stages") else None
        self.services_path = _file(directory, details["services"]) if details.get("services") else None
        self.digests = {"report": hashlib.sha256(report_bytes).hexdigest(),
                        "trace": _digest(self.trace_path)}
        for name, path in (("stages", self.stages_path), ("services", self.services_path)):
            if path is not None:
                self.digests[name] = _digest(path)
        self.identity = hashlib.sha256(_canonical(self.digests).encode("ascii")).hexdigest()
        records = self.events()
        try:
            config = next(records, {})
        finally:
            records.close()
        snapshot = config.get("snapshot", {})
        inputs = snapshot.get("config", {})
        _require(config.get("type") == "configuration" and bool(snapshot.get("config_sha256")),
                 f"{self.trace_path}: missing configuration snapshot")
        _require(inputs.get("package_sha256") == package.identity,
                 f"{self.trace_path}: trace package identity does not match {package.identity}")
        for name in ("context", "profile_sha256", "placement"):
            observed = inputs.get(name, "host" if name == "placement" else None)
            _require(self.report.get(name) == observed, f"{self.trace_path}: report {name} mismatch")
        _require(details.get("effective_backend", config) == config,
                 f"{self.trace_path}: report effective configuration mismatch")
        self.config_sha256 = snapshot["config_sha256"]
        self.placement = self.report["placement"]
        self.context = self.report["context"]
        self.profile_sha256 = self.report["profile_sha256"]
        output = _output(cache_directory, (directory, package.directory))
        self.cache_path = _cache(output / f"run-v1-{self.identity}.sqlite", self._build)

    def events(self):
        for _, _, record in _records(self.trace_path, configuration=True):
            yield record

    def _build(self, db):
        db.executescript("""
            create table timing (
                query text, op text, stage text, status text, submitted text, completed text,
                in_csv integer default 0, primary key(query, op, stage)
            );
            create table requests (query text primary key, record text);
        """)
        query_ids = set(self.package.query_ids)
        summary = None
        for number, record in enumerate(self.events(), 1):
            kind = record.get("type")
            where = f"{self.trace_path}:{number}"
            _require(summary is None, f"{where}: record after terminal summary")
            if kind == "configuration":
                _require(number == 1, f"{where}: duplicate configuration record")
            elif kind == "summary":
                summary = record
            elif kind in ("request", "stage"):
                query = _uint(record.get("query"), where)
                _require(query in query_ids, f"{where}: unknown original query {query}")
                success = record.get("success")
                _require(success is None or type(success) is bool, f"{where}: invalid success flag")
                if kind == "request":
                    self._request_row(db, record, query, where)
                else:
                    self._timing_row(db, record, success, where)
        _require(summary is not None or self.report.get("execution_succeeded") is False,
                 f"{self.trace_path}: missing terminal summary")
        if summary is not None:
            _require(summary.get("success") == self.report.get("execution_succeeded"),
                     f"{self.trace_path}: report execution status mismatch")

        with _open(self.package.cache_path) as ir:
            for key in db.execute("select query,op,stage from timing"):
                _require(ir.execute("select 1 from stages where query=? and op=? and stage=?", key).fetchone()
                         is not None, f"{self.trace_path}: trace stage missing from package {key}")
        if self.stages_path is not None:
            self._check_csv(db)

    @staticmethod
    def _request_row(db, record, query, where):
        times = {field: _uint(record[field], where) for field in ("admitted", "visible", "drained")
                 if record.get(field) is not None}
        if record.get("success") is True:
            _require(len(times) == 3 and times["admitted"] <= times["visible"] <= times["drained"],
                     f"{where}: missing or reversed request endpoints for {query}")
        try:
            db.execute("insert into requests values (?,?)", (str(query), _canonical(record)))
        except sqlite3.IntegrityError as exc:
            raise InputError(f"{where}: duplicate query request {query}") from exc

    @staticmethod
    def _timing_row(db, record, success, where):
        key = _key(record, where)
        submitted = _uint(record.get("submitted"), where)
        completed = record.get("completed")
        status = "failed" if success is False else "incomplete"
        if success is True and completed is not None:
            completed = _uint(completed, where)
            _require(completed >= submitted, f"{where}: reversed stage endpoints {key}")
            status = "completed"
        try:
            db.execute("insert into timing(query,op,stage,status,submitted,completed) values (?,?,?,?,?,?)",
                       (*_sql_key(key), status, str(submitted), str(completed) if status == "completed" else None))
        except sqlite3.IntegrityError as exc:
            raise InputError(f"{where}: duplicate stage key {key}") from exc

    def _check_csv(self, db):
        fields = {"query", "op", "stage", "submitted_ns", "completed_ns",
                  "dependency_and_resource_elapsed_ns"}
        with self.stages_path.open(encoding="utf-8", newline="") as stream:
            rows = csv.DictReader(stream)
            _require(rows.fieldnames is not None and fields == set(rows.fieldnames)
                     and len(rows.fieldnames) == len(fields), f"{self.stages_path}: invalid stages CSV header")
            for row in rows:
                where = f"{self.stages_path}:{rows.line_num}"
                key = _key(row, where)
                start, end, elapsed = (_uint(row.get(field), where) for field in (
                    "submitted_ns", "completed_ns", "dependency_and_resource_elapsed_ns"))
                expected = ("completed", str(start), str(end))
                stored = db.execute(
                    "select status,submitted,completed,in_csv from timing where query=? and op=? and stage=?",
                    _sql_key(key)).fetchone()
                _require(stored is None or not stored[-1], f"{where}: duplicate stage key {key}")
                _require(stored is not None and stored[:-1] == expected and elapsed == end - start,
                         f"{where}: CSV endpoints do not match trace stage {key}")
                db.execute("update timing set in_csv=1 where query=? and op=? and stage=?", _sql_key(key))

    def request(self, query_id):
        query = _uint(query_id, "query ID")
        with _open(self.cache_path) as db:
            row = db.execute("select record from requests where query=?", (str(query),)).fetchone()
        return json.loads(row[0]) if row else None

    def stage_timings(self, query_id):
        query = _uint(query_id, "query ID")
        _require(query in self.package.query_ids, f"unknown original query ID {query}")
        timings = {}
        with _open(self.cache_path) as run, _open(self.package.cache_path) as ir:
            for op, stage in ir.execute("select op,stage from stages where query=? order by rank", (str(query),)):
                key = query, op, stage
                row = run.execute(
                    "select status,submitted,completed from timing where query=? and op=? and stage=?",
                    _sql_key(key)).fetchone()
                status, submitted, completed = row or ("missing", None, None)
                timings[key] = {"status": status, "submitted_ns": int(submitted) if submitted is not None else None,
                                "completed_ns": int(completed) if completed is not None else None}
        return timings
