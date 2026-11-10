"""Exercise all installed CLI modes from an input-only directory."""

import csv
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def main(work, processor):
    samples = json.loads(Path(__file__).with_name("v01-samples.json").read_text())
    isolated = work / "isolated"
    isolated.mkdir()
    venv = work / "venv"
    cli = venv / "bin/ndpsim-viz"
    env = {name: os.environ[name] for name in ("HOME", "XDG_DATA_HOME") if name in os.environ}
    env.update(PATH=f"{venv / 'bin'}:/usr/bin:/bin", PYTHONNOUSERSITE="1")

    def invoke(command):
        return subprocess.check_output(command, cwd=isolated, env=env, text=True)

    invoke([venv / "bin/python", "-I", "-c",
            "from importlib.util import find_spec; assert find_spec('ndpsim') is None"])
    for binary in ("sst", "MQSim", "ndpsim"):
        check(shutil.which(binary, path=env["PATH"]) is None, f"unexpected dependency: {binary}")
    for label in ("teaching", "formal"):
        source = Path(samples[label]["path"])
        target = isolated / label
        target.mkdir()
        for name, sha256 in samples[label]["sha256"].items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            path = Path(shutil.copyfile(source / name, target / name))
            check(digest(path) == sha256, f"input digest mismatch: {path}")
            path.chmod(0o444)
    for run in samples["formal"]["runs"]:
        source = Path(run["path"])
        target = isolated / source.name
        target.mkdir()
        for name, sha256 in run["sha256"].items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            path = Path(shutil.copyfile(source / name, target / name))
            check(digest(path) == sha256, f"input digest mismatch: {path}")
            path.chmod(0o444)

    measurements = {}
    for mode, package, reports in (
        ("capture-only", "teaching", ()), ("single-run", "formal", ("B01",)),
        ("comparison", "formal", ("C01", "B01")),
    ):
        query = str(samples[package]["query"])
        args = ["--package", package, "--query", query, "--output", mode]
        for report in reports:
            args += ["--report", f"{report}/report.json"]
        # Each measurement has a new parent so ru_maxrss belongs to this invocation.
        result = json.loads(invoke([venv / "bin/python", "-I", str(Path(__file__).resolve()),
                                    "--measure", cli, *args]))
        output = isolated / mode
        check(result["exit_code"] == 0, f"{mode}: conversion failed")
        manifest = json.loads((output / "manifest.json").read_text())
        check(manifest["mode"] == mode and manifest["query"] == query
              and manifest["package"]["sha256"] == samples[package]["package_sha256"]
              and len(manifest["runs"]) == len(reports), f"{mode}: incorrect manifest identity")
        files = {name: {"bytes": (output / name).stat().st_size, "sha256": digest(output / name)}
                 for name in ("dag.html", "timeline.json", "manifest.json")}
        check(all(files[name] == entry for name, entry in manifest["artifacts"].items()),
              f"{mode}: artifact digest mismatch")
        if mode == "capture-only":
            check(not json.loads((output / "timeline.json").read_text())["traceEvents"],
                  "capture-only timeline is not empty")
        measurements[mode] = dict(result, argv=args, files=files,
                                  index_bytes=sum(p.stat().st_size for p in (output / "cache").iterdir()))

    comparison = isolated / "comparison"
    start = (comparison / "dag.html").read_text().split('id="graph-data" type="application/json">', 1)
    # Use the published DAG data and its exact serialized stable key.
    dag = json.loads(start[1].split("</script>", 1)[0])
    stage_key = json.dumps(next(node["key"] for node in dag["nodes"]
                                if node.get("key") == {"query": "2579", "op": "op2", "stage": "score_l2.1"}),
                           separators=(",", ":"))
    key = stage_key.replace("'", "''")
    sql = ("SELECT p.name AS run, s.id, s.ts, s.dur FROM slice s "
           "JOIN thread_track tr ON s.track_id=tr.id JOIN thread th USING (utid) "
           "JOIN process p USING (upid) WHERE s.category='Stage' "
           f"AND EXTRACT_ARG(s.arg_set_id, 'args.stage_key')='{key}' ORDER BY p.name;")
    csv_result = invoke([processor, "query", comparison / "timeline.json", sql])
    slices = list(csv.DictReader(csv_result.splitlines()))
    check({row["run"] for row in slices} == {"host", "controller"} and len(slices) == 2,
          "expected one matching stage slice in each placement")
    for row in slices:
        source = next(run for run in samples["formal"]["runs"] if run["placement"] == row["run"])
        with (isolated / Path(source["path"]).name / "stages.csv").open(newline="") as stream:
            original = next(entry for entry in csv.DictReader(stream)
                            if (entry["query"], entry["op"], entry["stage"]) == ("2579", "op2", "score_l2.1"))
        check(int(row["ts"]) == int(original["submitted_ns"]) - source["request"]["admitted"]
              and int(row["dur"]) == int(original["completed_ns"]) - int(original["submitted_ns"]),
              "Perfetto slice does not match source stage")
    print(json.dumps({"tool": invoke([cli, "--version"]).strip(), "measurements": measurements,
                      "stage_key": stage_key, "perfetto_slices": slices}, indent=2, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1] == "--measure":
        start = time.monotonic()
        status = subprocess.run(sys.argv[2:], check=False).returncode
        rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        print(json.dumps({"exit_code": status, "elapsed_seconds": round(time.monotonic() - start, 3),
                          "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024}))
    else:
        main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
