import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ndpsim_viz.reader import InputError, Package, Run


QUERY = (1 << 64) - 1


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def make_package(directory, *, reverse=False, duplicate=False, dangling=False):
    directory.mkdir()
    dep = {"kind": "DATA", "input": "in",
           "source": {"key": {"query": QUERY, "op": "unknown" if dangling else "left", "stage": "sum"},
                      "port": "out"}}
    operations = [
        {"query": QUERY, "id": op, "operator": "fixture.sum", "operator_version": "1",
         "stages": [{"id": "sum", "topological_rank": rank, "dependencies": deps}]}
        for rank, (op, deps) in enumerate((("left", []), ("join", [dep])))
    ]
    if reverse:
        operations.reverse()
    if duplicate:
        operations[0]["stages"].append(operations[0]["stages"][0])
    data = b""
    rows = []
    for op in operations:
        raw = encode(op)
        for stage in op["stages"]:
            rows.append({"file": "operations.jsonl", "offset_bytes": len(data), "length_bytes": len(raw),
                         "key": {"query": QUERY, "op": op["id"], "stage": stage["id"]},
                         "topological_rank": stage["topological_rank"],
                         "stage_sha256": sha(encode(stage)), "dependencies": stage["dependencies"]})
        data += raw
    if not reverse:
        rows.reverse()
    inputs = {"operations.jsonl": data, "index.jsonl": b"".join(map(encode, rows))}
    counts = {"operations": 2, "queries": 1, "stages": len(rows), "dependencies": 1}
    terminals = [{"query": QUERY, "op": "join", "stage": "sum"}]
    inputs["manifest.json"] = encode({
        "format": "ndpsim-static-ir/2", "provenance": {"query_ids": [QUERY]},
        "counts": counts, "terminals": terminals,
        "files": [{"path": name, "bytes": len(content), "sha256": sha(content)}
                  for name, content in inputs.items()],
    })
    identity = sha(b"ndpsim-static-ir/2\0" + b"".join(
        hashlib.sha256(inputs[name]).digest()
        for name in ("manifest.json", "index.jsonl", "operations.jsonl")))
    inputs["tail.json"] = encode({
        "format": "ndpsim-static-ir-tail/2", "complete": True, "lost_records": 0,
        "package_sha256": identity, "counts": counts, "terminals": terminals,
    })
    for name, content in inputs.items():
        (directory / name).write_bytes(content)


def make_run(directory, identity, *, status="completed", reverse=False, duplicate=False, trace_identity=None):
    directory.mkdir()
    config = {"package_sha256": identity, "placement": "host",
              "context": "fixture/1", "profile_sha256": "b" * 64}
    report = dict(config, format="ndpsim-run-report/1", execution_succeeded=True,
                  details={"all_resources": "trace.jsonl", "stages": "stages.csv"})
    config["package_sha256"] = trace_identity or identity
    header = {"type": "configuration", "snapshot": {"config": config, "config_sha256": "c" * 64}}
    request = {"type": "request", "query": QUERY, "admitted": 7, "visible": 7,
               "drained": 9, "success": True}
    stage = {"type": "stage", "query": QUERY, "op": "left", "stage": "sum", "submitted": 7}
    if status == "completed":
        stage.update(completed=7, success=True)
    elif status == "failed":
        stage.update(completed=0, success=False)
    rows = [request, stage]
    if duplicate:
        rows.append(stage)
    if reverse:
        rows.reverse()
    (directory / "trace.jsonl").write_bytes(b"".join(map(encode, [
        header, *rows, {"type": "summary", "success": True},
    ])))
    with (directory / "stages.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("query", "op", "stage", "submitted_ns", "completed_ns",
                         "dependency_and_resource_elapsed_ns"))
        if status == "completed":
            writer.writerow((QUERY, "left", "sum", 7, 7, 0))
    path = directory / "report.json"
    path.write_bytes(encode(report))
    return path


class ReaderTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.cache = self.root / "cache"

    def package(self, name="package", **kwargs):
        directory = self.root / name
        make_package(directory, **kwargs)
        return Package(directory, self.cache)

    def test_row_order_and_uint64(self):
        one = self.package()
        two = self.package("other", reverse=True)
        self.assertEqual(one.query(str(QUERY)), two.query(QUERY))
        self.assertNotEqual(one.cache_path, two.cache_path)
        self.assertEqual(one.cache_path, Package(one.directory, self.cache).cache_path)
        self.assertEqual(one.query_ids, (QUERY,))
        with self.assertRaisesRegex(InputError, "unknown original query ID"):
            one.query(0)

    def test_duplicate_ir_stage(self):
        with self.assertRaisesRegex(InputError, "duplicate stage key"):
            self.package(duplicate=True)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_dangling_dependency(self):
        with self.assertRaisesRegex(InputError, "dangling dependency"):
            self.package(dangling=True)

    def test_modified_source_rejects_preexisting_cache(self):
        package = self.package()
        (package.directory / "operations.jsonl").write_text("modified")
        with self.assertRaisesRegex(InputError, "size/hash mismatch"):
            Package(package.directory, self.cache)

    def test_run_row_order_and_missing(self):
        package = self.package()
        one = Run(package, make_run(self.root / "a", package.identity), self.cache)
        two = Run(package, make_run(self.root / "b", package.identity, reverse=True), self.cache)
        self.assertNotEqual(one.cache_path, two.cache_path)
        self.assertEqual(one.request(QUERY), two.request(QUERY))
        self.assertEqual(one.stage_timings(QUERY), two.stage_timings(QUERY))
        self.assertEqual(one.stage_timings(QUERY)[QUERY, "left", "sum"],
                         {"status": "completed", "submitted_ns": 7, "completed_ns": 7})
        self.assertEqual(one.stage_timings(QUERY)[QUERY, "join", "sum"]["status"], "missing")
        self.assertEqual([event["type"] for event in one.events()],
                         ["configuration", "request", "stage", "summary"])

    def test_failed_and_incomplete(self):
        package = self.package()
        for status in ("failed", "incomplete"):
            with self.subTest(status=status):
                path = make_run(self.root / status, package.identity, status=status)
                run = Run(package, path, self.cache)
                self.assertEqual(run.stage_timings(QUERY)[QUERY, "left", "sum"],
                                 {"status": status, "submitted_ns": 7, "completed_ns": None})

    def test_identity_mismatch(self):
        package = self.package()
        with self.assertRaisesRegex(InputError, "report package identity"):
            Run(package, make_run(self.root / "a", "0" * 64), self.cache)
        with self.assertRaisesRegex(InputError, "trace package identity"):
            Run(package, make_run(self.root / "b", package.identity, trace_identity="0" * 64), self.cache)

    def test_duplicate_trace_stage(self):
        package = self.package()
        with self.assertRaisesRegex(InputError, "duplicate stage key"):
            Run(package, make_run(self.root / "a", package.identity, duplicate=True), self.cache)
