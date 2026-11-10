"""Independently audit the recorded V01 source samples from the repository root."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def key(record):
    return record["query"], record["op"], record["stage"]


def package(path, query):
    manifest = load(path / "manifest.json")
    tail = load(path / "tail.json")
    hashes = {name: digest(path / name) for name in
              ("manifest.json", "index.jsonl", "operations.jsonl", "tail.json")}
    for entry in manifest["files"]:
        check(hashes[entry["path"]] == entry["sha256"] and
              (path / entry["path"]).stat().st_size == entry["bytes"], "manifest file mismatch")
    identity = hashlib.sha256(b"ndpsim-static-ir/2\0" + b"".join(
        bytes.fromhex(hashes[name]) for name in
        ("manifest.json", "index.jsonl", "operations.jsonl"))).hexdigest()
    check(manifest["format"] == "ndpsim-static-ir/2" and
          identity == tail["package_sha256"] and tail["complete"] is True and
          tail["lost_records"] == 0, "package identity or completeness")
    check(query in manifest["provenance"]["query_ids"], "original query missing")
    stages = {}
    with (path / "operations.jsonl").open("rb") as stream:
        while raw := stream.readline():
            offset = stream.tell() - len(raw)
            op = json.loads(raw)
            if op["query"] == query:
                for stage in op["stages"]:
                    stable = query, op["id"], stage["id"]
                    check(stable not in stages, f"duplicate stage {stable}")
                    stages[stable] = stage, offset, len(raw)
    indexed = set()
    edge_kinds = Counter()
    with (path / "index.jsonl").open("rb") as stream:
        for line in stream:
            index = json.loads(line)
            stable = key(index["key"])
            if stable[0] != query:
                continue
            check(stable not in indexed, f"duplicate index {stable}")
            indexed.add(stable)
            stage, offset, length = stages[stable]
            canonical = json.dumps(stage, sort_keys=True, ensure_ascii=True,
                                   separators=(",", ":")) + "\n"
            check((index["offset_bytes"], index["length_bytes"], index["stage_sha256"],
                   index["topological_rank"], index["dependencies"]) ==
                  (offset, length, hashlib.sha256(canonical.encode("ascii")).hexdigest(),
                   stage["topological_rank"], stage["dependencies"]), f"index mismatch {stable}")
            for dep in stage["dependencies"]:
                source = key(dep["source"]["key"])
                check(source in stages and stages[source][0]["topological_rank"] <
                      stage["topological_rank"], f"invalid dependency {source} -> {stable}")
                edge_kinds[dep["kind"]] += 1
    check(indexed == stages.keys(), "index coverage")
    return identity, hashes, stages, edge_kinds


def run(path, identity, query, stages):
    report = load(path / "report.json")
    check(report["format"] == "ndpsim-run-report/1" and
          report["execution_succeeded"] is True and
          report["package_sha256"] == identity, "run report identity")
    check(package_identity(path / "inputs/package") == identity, "run package snapshot mismatch")
    configuration = report["details"]["effective_backend"]
    check(configuration["snapshot"]["config"]["package_sha256"] == identity, "trace configuration identity")
    intervals = {}
    with (path / "stages.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            if int(row["query"]) != query:
                continue
            stable = query, row["op"], row["stage"]
            submitted, completed, elapsed = (int(row[name]) for name in
                ("submitted_ns", "completed_ns", "dependency_and_resource_elapsed_ns"))
            check(stable not in intervals and completed - submitted == elapsed >= 0,
                  f"CSV stage interval {stable}")
            intervals[stable] = submitted, completed
    check(intervals.keys() == stages.keys(), "stages.csv coverage")

    services = Counter()
    with (path / "services.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            stable = key(json.loads(row["key"]))
            if stable[0] != query:
                continue
            submitted, start, end, wait, duration = (int(row[name]) for name in
                ("submitted_ns", "start_ns", "end_ns", "queue_wait_ns", "service_ns"))
            check(stable in stages and start - submitted == wait >= 0 and end - start == duration >= 0,
                  f"CSV service interval {stable}")
            services[row["resource"], row["key"], submitted, start, end] += 1

    trace_stages = {}
    trace_services = Counter()
    request = None
    counts = Counter()
    trace_hash = hashlib.sha256()
    with (path / "trace.jsonl").open("rb") as stream:
        for line in stream:
            trace_hash.update(line)
            record = json.loads(line)
            kind = record["type"]
            counts[kind] += 1
            if kind == "configuration":
                check(record == configuration, "report/trace configuration mismatch")
            if kind in ("cpu", "controller_compute"):
                stable = key(json.loads(record["key"]))
                if stable[0] == query:
                    trace_services[kind, record["key"], record["submitted"],
                                   record["start"], record["end"]] += 1
            if record.get("query") != query:
                continue
            if kind == "stage":
                stable = key(record)
                check(stable not in trace_stages and record["success"] is True,
                      f"invalid trace stage {stable}")
                trace_stages[stable] = record["submitted"], record["completed"]
            if kind == "request":
                check(request is None and record["success"] is True, "request status")
                request = record
    check(counts == report["record_counts"], "report trace counts")
    check(intervals == trace_stages and services == trace_services, "CSV/trace parity")
    check(request is not None, "query admission missing")
    coverage = Counter()
    packets = {}
    first_packet = None
    buffer_stage_keys = {
        f"{q}:{op}:{stage}:{access}"
        for q, op, stage in stages
        for access in ("input-write", "compute-read", "result-write", "result-read")
    }
    with (path / "trace.jsonl").open("rb") as stream:
        for line in stream:
            record = json.loads(line)
            kind = record["type"]
            if kind in ("local_plan", "local_read", "logical") and record.get("query") == query:
                check(key(record) in stages, f"unknown explicit resource stage {record}")
                coverage[kind + ":stage"] += 1
            if kind == "protocol" and record.get("query") == query:
                coverage["protocol:query"] += 1
            if kind == "buffer_access":
                if record["key"] in buffer_stage_keys:
                    coverage["buffer_access:stage"] += 1
                elif record["key"].startswith(f"query-write:{query}:"):
                    coverage["buffer_access:query"] += 1
            timestamp = record.get("time", record.get("completed"))
            if (timestamp is None or timestamp < request["admitted"] or
                    timestamp > request["visible"]):
                continue
            if kind == "pcie":
                events = packets.setdefault(record["id"], {})
                check(record["event"] not in events, "duplicate PCIe endpoint")
                events[record["event"]] = record["time"]
            elif kind in ("credit", "nand_transaction", "ordinary_read"):
                coverage[kind + ":window"] += 1
    for packet, endpoints in packets.items():
        if endpoints.keys() != {"enqueue", "start", "arrive"}:
            coverage["pcie_partial:window"] += 1
            continue
        check(endpoints["enqueue"] <= endpoints["start"] <= endpoints["arrive"], "PCIe endpoints")
        coverage["pcie_complete:window"] += 1
        if first_packet is None:
            first_packet = {"id": packet, **endpoints}
    for resource, _, *_ in services.elements():
        coverage[resource + ":stage"] += 1
    return {
        "path": str(path),
        "sha256": {**{name: digest(path / name) for name in (
            "report.json", "stages.csv", "services.csv", "effective-config.json",
            "inputs/profile.json", "inputs/device.xml", "inputs/layout.json")},
            "trace.jsonl": trace_hash.hexdigest()},
        "placement": report["placement"],
        "profile_sha256": report["profile_sha256"],
        "config_sha256": configuration["snapshot"]["config_sha256"],
        "request": request,
        "first_score_intervals": {stable[2]: list(interval) for stable, interval in intervals.items()
                                  if stable[1] == "op2"},
        "first_pcie_packet_in_window": first_packet,
        "resource_coverage": dict(coverage),
        "trace_record_counts": dict(counts),
    }


def package_identity(path):
    manifest = load(path / "manifest.json")
    tail = load(path / "tail.json")
    hashes = {entry["path"]: digest(path / entry["path"]) for entry in manifest["files"]}
    identity = hashlib.sha256(b"ndpsim-static-ir/2\0" + b"".join(bytes.fromhex(value) for value in
        (digest(path / "manifest.json"), hashes["index.jsonl"], hashes["operations.jsonl"]))).hexdigest()
    check(identity == tail["package_sha256"], "run package tail identity")
    return identity


def main():
    teaching = Path("results/v0.4.5/p17/run2/tutorial 空格/package")
    formal = Path("results/v0.4.5/p19/run1")
    formal_package = formal / "B01/inputs/package"
    teaching_id, teaching_hashes, teaching_stages, teaching_edges = package(teaching, 0)
    joined = teaching_stages[(0, "join", "sum")][0]["dependencies"]
    check({key(dep["source"]["key"]) for dep in joined} ==
          {(0, "left", "sum"), (0, "right", "sum")} and teaching_edges == {"DATA": 2},
          "teaching fork/join")
    query = 2579
    identity, hashes, stages, edges = package(formal_package, query)
    runs = [run(formal / label, identity, query, stages) for label in ("B01", "C01")]
    check([result["placement"] for result in runs] == ["host", "controller"] and
          runs[0]["profile_sha256"] == runs[1]["profile_sha256"], "B/C placement and profile")
    print(json.dumps({
        "command": "python3.13 visualization/evidence/audit_v01.py",
        "exit_code": 0,
        "teaching": {"path": str(teaching), "package_sha256": teaching_id, "sha256": teaching_hashes,
                     "query": 0, "stages": len(teaching_stages), "edge_kinds": dict(teaching_edges),
                     "join_dependencies": joined},
        "formal": {"path": str(formal_package), "package_sha256": identity, "sha256": hashes,
                   "query": query, "stages": len(stages), "edge_kinds": dict(edges),
                   "first_score_dependencies": {stable[2]: stage["dependencies"]
                       for stable, (stage, _, _) in stages.items() if stable[1] == "op2"},
                   "runs": runs},
    }, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
