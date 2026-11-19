"""Launch the E0 MaxSim SST element from an independently audited generator bundle."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from ndpsim.static_ir import canonical, decode, digest_bytes, file_hash, require

from config import MAIN, derive, validate
from generate import check, write

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from source_snapshot import mqsim_source_snapshot

PLACEMENTS = ("host", "host_compute_free", "device_all")


def json_file(path):
    return decode(path.read_bytes())


def backend_digest():
    source = ROOT / "third_party/MQSim"
    entries = sorted(p.relative_to(source).as_posix() for p in (source / "src").rglob("*") if p.is_file())
    return hashlib.sha256("".join(f"{name}:{file_hash(source / name)}\n" for name in entries).encode()).hexdigest()


def checked_inputs(bundle, platform_path):
    require((bundle / "layout.json").is_file(), "MaxSim E0 requires a physical Layout")
    audited = check(bundle)
    effective = json_file(bundle / "effective-config.json")
    config = effective["config"]
    receipt = json_file(bundle / "receipt.json")
    require(digest_bytes(canonical(config)) == receipt["inputs"]["expanded_config_sha256"],
            "expanded configuration identity mismatch")
    defaults = json_file(MAIN)
    require(config.keys() == defaults.keys()
            and all(config[key].keys() == defaults[key].keys() for key in defaults), "effective config fields")
    validate(config)
    require(derive(config) == effective["derived"], "effective derived values mismatch")
    hardware = config["hardware"]
    compute = config["compute"]
    require(hardware["local_memory_cost_model"] in ("inherit", "inherit_or_explicit_upper_bound"),
            "E0 assumes explicit zero Host DRAM service")
    for name, positive in (("host_effective_tflops", True), ("device_effective_tflops", True),
                           ("batch_launch_us", False), ("offload_command_us", False)):
        scaled = float(compute[name]) * 1000
        require(0 <= scaled < 2**64 and scaled.is_integer() and (not positive or scaled > 0),
                f"{name}: E0 requires integer ns and FLOP/ns")
    require(hardware["mqsim_config_path"] and
            file_hash(Path(hardware["mqsim_config_path"])) == hardware["mqsim_config_hash"],
            "MQSim XML hash mismatch")
    backend = mqsim_source_snapshot()
    gitlink = subprocess.check_output(["git", "rev-parse", "HEAD:third_party/MQSim"], cwd=ROOT, text=True).strip()
    require(hardware["backend_commit"] == backend["head_commit"] == gitlink, "MQSim fork commit mismatch")
    require(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                       cwd=backend["source_directory"], text=True).strip(),
            "E0 requires the pinned MQSim worktree")
    platform = json_file(platform_path)
    return config, audited, backend, platform


def run(bundle, build, output, placements=PLACEMENTS, platform_path=HERE / "e0-run.json"):
    bundle, build, output, platform_path = (p.resolve() for p in (bundle, build, output, platform_path))
    require(placements and len(placements) == len(set(placements)) and all(p in PLACEMENTS for p in placements),
            "invalid MaxSim placements")
    require(bundle != output and bundle not in output.parents, "output must be outside the immutable input bundle")
    config, audit, backend, platform = checked_inputs(bundle, platform_path)
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library_dir = build / "workloads/maxsim"
    modules = [p for p in library_dir.glob("libndpsim_maxsim.*") if p.suffix in (".so", ".dylib")]
    require(sst.is_file() and len(modules) == 1, "build the ndpsim_maxsim SST element first")
    require(shutil.disk_usage(output.parent).free >= 32 * 1024**2 * len(placements),
            "insufficient output space for E0 traces")
    output.mkdir()
    source_digest = backend_digest()
    sources = [*HERE.glob("*.cc"), *HERE.glob("*.h"), *HERE.glob("*.h.in"), *HERE.glob("*.py"),
               HERE / "CMakeLists.txt", ROOT / "CMakeLists.txt", ROOT / "src/backend/event_bridge.h",
               ROOT / "src/layout/layout.h", ROOT / "src/layout/layout.cc"]
    write(output / "inputs.json", {
        "bundle": str(bundle), "generation_receipt_sha256": file_hash(bundle / "receipt.json"),
        "platform_path": str(platform_path), "platform_sha256": file_hash(platform_path),
        "package_sha256": audit["package_sha256"], "mqsim": backend,
        "mqsim_src_sha256": source_digest,
        "xml_sha256": config["hardware"]["mqsim_config_hash"],
        "sst": {"path": str(sst), "sha256": file_hash(sst)},
        "element": {"path": str(modules[0]), "sha256": file_hash(modules[0])},
        "python": {"path": sys.executable, "sha256": file_hash(Path(sys.executable))},
        "sources_sha256": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted(sources)},
        "build_cache_sha256": file_hash(build / "CMakeCache.txt"),
    })
    summaries = {}
    for placement in placements:
        run_name = output / f"{placement}.json"
        trace = output / f"{placement}.jsonl"
        write(run_name, {
            "format": "ndpsim-maxsim-run/1", "placement": placement, "bundle": str(bundle),
            "receipt_sha256": file_hash(bundle / "receipt.json"), "mqsim_source_sha256": source_digest,
            "scratch": f"{placement}.sqlite", "output": trace.name, "platform": platform,
        })
        command = [str(sst), f"--add-lib-path={library_dir}", str(HERE / "sst.py"),
                   "--", json.dumps(str(run_name), ensure_ascii=False)]
        stdout_path, stderr_path = (output / f"{placement}.{suffix}" for suffix in ("stdout", "stderr"))
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            try:
                process = subprocess.run(command, cwd="/tmp", stdout=stdout, stderr=stderr, timeout=180)
                exit_code, timed_out = process.returncode, False
            except subprocess.TimeoutExpired:
                exit_code, timed_out = None, True
        write(output / f"{placement}.process.json", dict(command=command, cwd="/tmp",
                                                        exit_code=exit_code, timed_out=timed_out))
        require(exit_code == 0, f"{placement}: SST failed; see {stderr_path}")
        summary = {}
        with trace.open("rb") as stream:
            for line in stream:
                summary = decode(line)
        require(summary.get("type") == "summary" and summary["success"] and summary["all_resources_drained"],
                f"{placement}: no successful drained summary")
        summaries[placement] = summary
    write(output / "execution.json", dict(format="ndpsim-maxsim-execution/1", configured_unvalidated=True,
                                        package_sha256=audit["package_sha256"], placements=summaries,
                                        sha256={p.name: file_hash(p) for p in sorted(output.iterdir()) if p.is_file()}))
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", type=Path, default=HERE / "e0-run.json")
    parser.add_argument("--placement", action="append", choices=PLACEMENTS)
    args = parser.parse_args()
    try:
        summary = run(args.input, args.build, args.output, args.placement or PLACEMENTS, args.platform)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"MaxSim E0: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
