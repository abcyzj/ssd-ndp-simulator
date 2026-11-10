"""Generate V04 traces and verify Perfetto's imported slices."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from ndpsim_viz.reader import Package, Run
from ndpsim_viz.timeline import write_timeline
from test_timeline import TimelineTests


def slices(importer, trace):
    sql = """
        SELECT p.name AS run, s.name, s.category, s.ts, s.dur, s.track_id,
               HEX(EXTRACT_ARG(s.arg_set_id, 'args.stage_key')) AS stage_key,
               HEX(EXTRACT_ARG(s.arg_set_id, 'args.packet_id')) AS packet_id
        FROM slice s JOIN thread_track tr ON s.track_id=tr.id
        JOIN thread th USING (utid) JOIN process p USING (upid)
    """
    data = subprocess.run([str(importer), "--no-agent", "query", str(trace), sql], check=True, text=True,
                          stdout=subprocess.PIPE).stdout
    rows = list(csv.DictReader(data.splitlines()))
    for row in rows:
        for field in ("stage_key", "packet_id"):
            row[field] = bytes.fromhex(row[field]).decode("utf-8")
    return rows


def main(out, importer):
    out.mkdir(exist_ok=True)
    fixture = TimelineTests()
    fixture.setUp()
    fixture_file = out / "fixture.json"
    write_timeline(fixture_file, fixture.package, 7, [("Fixture", fixture.run_data())])
    records = slices(importer, fixture_file)
    stage = {row["name"]: row for row in records if row["category"] == "Stage"}
    assert {(name, int(row["ts"]), int(row["dur"])) for name, row in stage.items()} == {
        ("op / score", 3, 4), ("op / prepare", 4, 5), ("op / zero", 7, 0)}
    assert len({row["track_id"] for row in stage.values()}) == 3
    assert any(row["name"] == "ordinary_read" and int(row["ts"]) == 7 and
               row["stage_key"] in ("", "[NULL]") for row in records)

    evidence = json.loads(Path("visualization/evidence/v01-samples.json").read_text())["formal"]
    package_path = Path(evidence["path"])
    package = Package(package_path, out / "cache")
    query = evidence["query"]
    labels = ("B01", "C01")
    runs = [(label, Run(package, package_path.parents[2] / label / "report.json", out / "cache"))
            for label in labels]
    trace = out / "timeline.json"
    coverage = write_timeline(trace, package, query, runs)
    imported = slices(importer, trace)
    expected = {}
    offset = int(coverage["display_offset_ns"])
    for label, run in runs:
        admission = run.request(query)["admitted"]
        with run.stages_path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                if int(row["query"]) != query:
                    continue
                key = json.dumps(dict(query=row["query"], op=row["op"], stage=row["stage"]),
                                 separators=(",", ":"))
                start, end = int(row["submitted_ns"]), int(row["completed_ns"])
                expected[label, key] = (start - admission + offset, end - start)
    observed = {(row["run"], row["stage_key"]): (int(row["ts"]), int(row["dur"])) for row in imported
                if row["category"] == "Stage"}
    assert len(expected) == len(observed) == 1214, len(observed)
    differences = {key: (expected[key], observed[key]) for key in expected if expected[key] != observed[key]}
    assert not differences, list(differences.items())[:10]
    for label, source in zip(labels, evidence["runs"]):
        resource_counts = coverage["runs"][label]["resource_counts"]
        source_counts = source["resource_coverage"]
        for kind in ("cpu", "controller_compute", "credit", "local_read", "ordinary_read",
                     "nand_transaction", "protocol", "buffer_access"):
            count = sum(value for key, value in source_counts.items() if key.startswith(kind + ":"))
            assert resource_counts.get(kind, 0) == count, (label, kind, count, resource_counts)
        # V01 counts only packets wholly inside the window; V04 retains a crossing packet too.
        assert resource_counts["pcie_complete"] == (
            source_counts["pcie_complete:window"] + source_counts["pcie_partial:window"])
        packet = source["first_pcie_packet_in_window"]
        admission = source["request"]["admitted"]
        observed_packet = {(int(row["ts"]), int(row["dur"])) for row in imported
                           if row["run"] == label and row["packet_id"] == str(packet["id"])}
        assert observed_packet == {
            (packet["enqueue"] - admission + offset, packet["start"] - packet["enqueue"]),
            (packet["start"] - admission + offset, packet["arrive"] - packet["start"]),
        }
        assert any(row["run"] == label and row["category"].startswith("PCIe") for row in imported)
        assert any(row["run"] == label and row["category"].startswith("SSD") for row in imported)
    assert any(row["run"] == "C01" and row["category"] == "Controller compute service" for row in imported)
    print(json.dumps(dict(coverage=coverage, fixture_slices=len(records), imported_slices=len(imported),
                          source_stages=len(expected), files={
                              path.name: dict(bytes=path.stat().st_size,
                                              sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                              for path in (fixture_file, trace)}), indent=2, sort_keys=True))


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
