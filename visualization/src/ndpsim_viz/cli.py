"""Standalone command for offline visualization exports."""

import argparse
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

from .dag import write_dag
from .reader import InputError, Package, Run
from .timeline import write_timeline


def tool_version():
    try:
        return version("ndpsim-viz")
    except PackageNotFoundError:
        return "0.4.5"


def original_query(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 20 or int(value) >= 2**64:
        raise argparse.ArgumentTypeError("expected an unsigned 64-bit original query ID")
    return int(value)


def export(args):
    package_path = args.package.resolve()
    reports = [path.resolve() for path in args.report]
    output = args.output.resolve()
    if len(reports) > 2:
        raise InputError("provide at most two --report options")
    if any(output.is_relative_to(source) for source in (package_path, *(p.parent for p in reports))):
        raise InputError(f"{output}: output must be outside the package and report directories")
    output.mkdir(parents=True, exist_ok=False)
    cache = output / "cache"
    package = Package(package_path, cache)
    if args.query not in package.query_ids:
        raise InputError(f"{package_path}: unknown original query ID {args.query}")

    runs = [Run(package, report, cache) for report in reports]
    if len(runs) == 2 and {run.placement for run in runs} != {"host", "controller"}:
        raise InputError("comparison requires one Host report and one Controller report")
    labeled = sorted(((run.placement, run) for run in runs),
                     key=lambda pair: pair[0] != "host")

    with TemporaryDirectory(prefix=".export-", dir=output) as scratch:
        pending = Path(scratch)
        coverage = write_timeline(pending / "timeline.json", package, args.query, labeled)
        write_dag(pending / "dag.html", package, args.query, labeled)
        artifacts = {}
        for name in ("dag.html", "timeline.json"):
            path = pending / name
            with path.open("rb") as stream:
                artifacts[name] = {"bytes": path.stat().st_size,
                                   "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
        manifest = {
            "format": "ndpsim-viz-manifest/1",
            "tool_version": tool_version(),
            "mode": ("capture-only", "single-run", "comparison")[len(runs)],
            "query": str(args.query),
            "package": {"path": str(package.directory), "sha256": package.identity,
                        "files": package.digests},
            "runs": {
                label: {"identity_sha256": run.identity, "files": {
                    kind: {"path": str(getattr(run, f"{kind}_path")), "sha256": digest}
                    for kind, digest in run.digests.items()
                }, "execution_succeeded": run.report["execution_succeeded"],
                    "hardware_validation": run.report.get("hardware_validation")}
                for label, run in labeled
            },
            "coverage": coverage,
            "artifacts": artifacts,
        }
        (pending / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8")
        for name in ("dag.html", "timeline.json", "manifest.json"):
            (pending / name).replace(output / name)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export an offline DAG and Perfetto timeline for one query.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {tool_version()}")
    parser.add_argument("--package", required=True, type=Path, help="Capture IR/2 directory")
    parser.add_argument("--query", required=True, type=original_query, help="original query ID")
    parser.add_argument("--report", type=Path, action="append", default=[],
                        help="run report.json, repeat for Host/Controller comparison")
    parser.add_argument("--output", required=True, type=Path, help="new output directory")
    args = parser.parse_args(argv)
    try:
        export(args)
    except (InputError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(f"ndpsim-viz: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
