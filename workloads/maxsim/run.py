"""Launch the E0 MaxSim SST element from an independently audited generator bundle."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from ndpsim.static_ir import canonical, decode, digest_bytes, file_hash, require
from ndpsim.execution import budget, enforce, launch, preflight

from config import MAIN, derive, uint, validate
from generate import check, write

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from source_snapshot import mqsim_source_snapshot

PLACEMENTS = ("host", "host_compute_free", "device_all")
HYBRID = "hybrid_miss_only"
LOCAL_PLACEMENTS = ("device_all", HYBRID)
NVME_PLACEMENTS = ("host", "host_compute_free")
SUPPORTED_PLACEMENTS = (*PLACEMENTS, HYBRID)
BACKFILL_PLACEMENTS = ("hybrid_backfill_a", "hybrid_backfill_b")
EXECUTION_PLACEMENTS = (*SUPPORTED_PLACEMENTS, *BACKFILL_PLACEMENTS)


def die_admission(placement, local, nvme):
    return {key: True for key, selected in (
        ("local_read_die_admission", local and placement in (*LOCAL_PLACEMENTS, *BACKFILL_PLACEMENTS)),
        ("nvme_read_die_admission", nvme and placement in (*NVME_PLACEMENTS, "hybrid_backfill_a")),
    ) if selected}


def scoring_metrics(summary, span):
    return {f"{side}_{key}": value for side, scoring in summary["scoring_by_side"].items()
            for key, value in dict(scoring, scoring_utilization=scoring["compute_ns"] / span).items()}


def verify_hybrid(bundle, snapshot, header, summary, completed):
    """Reconcile hybrid routes and bytes against the original candidate IDs and fixed snapshot."""
    if header["placement"] != HYBRID:
        return
    cached = set(snapshot)
    with (bundle / "candidates.jsonl").open("rb") as stream:
        misses = [sum(doc not in cached for doc in decode(line)["document_ids"]) for line in stream]
    require(len(misses) == len(completed) and all(
        row["offloaded_candidates"] == misses[row.get("query", 0)] for row in completed
    ), "hybrid route differs from fixed cache snapshot")
    config = header["effective_config"]
    protocol = config["protocol"]
    derived = derive(config)
    count = sum(misses)
    commands = sum(m > 0 for m in misses)
    command_bytes = commands * derived["query_bytes"] + count * protocol["document_id_bytes"]
    result_bytes = count * (protocol["document_id_bytes"] + protocol["score_bytes"])
    require(summary["ordinary_nvme_reads"] == summary["document_d2h_payload_bytes"] == 0 and
            summary["mqsim_local_reads"] == count * derived["logical_reads_per_document"] and
            summary["controller_staging_bytes"] == count * derived["document_bytes"] and
            summary["cache_hits"] == len(misses) * config["workload"]["candidate_count"] - count and
            summary["scoring_by_side"]["host"]["scores"] == summary["cache_hits"] and
            summary["scoring_by_side"]["controller"]["scores"] == count and
            summary["protocol_messages"] == 2 * commands and
            summary["command_payload_bytes"] == command_bytes and summary["result_payload_bytes"] == result_bytes,
            "hybrid route or protocol ledger disagrees")
    for direction, payload, header_key in (("H2D", command_bytes, "command_header_bytes"),
                                           ("D2H", result_bytes, "result_header_bytes")):
        link = summary["pcie"].get(direction, {})
        require(link.get("messages", 0) == commands and
                link.get("payload_bytes", 0) == payload + commands * header["effective_platform"][header_key],
                "hybrid PCIe ledger disagrees")



def json_file(path):
    return decode(path.read_bytes())


def backend_digest():
    source = ROOT / "third_party/MQSim"
    entries = sorted(p.relative_to(source).as_posix() for p in (source / "src").rglob("*") if p.is_file())
    return hashlib.sha256("".join(f"{name}:{file_hash(source / name)}\n" for name in entries).encode()).hexdigest()


def checked_inputs(bundle, platform_path, expected_backend_source=None):
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
    if expected_backend_source is None:
        require(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                            cwd=backend["source_directory"], text=True).strip(),
                "MaxSim requires the pinned MQSim worktree or an explicit experiment source hash")
    else:
        require(backend_digest() == expected_backend_source, "experimental MQSim source hash mismatch")
    platform = json_file(platform_path)
    return config, audited, backend, platform


def execute(command, output, name):
    stdout_path, stderr_path = (output / f"{name}.{suffix}" for suffix in ("stdout", "stderr"))
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        try:
            process = subprocess.run(command, cwd="/tmp", stdout=stdout, stderr=stderr, timeout=180)
            exit_code, timed_out = process.returncode, False
        except subprocess.TimeoutExpired:
            exit_code, timed_out = None, True
    write(output / f"{name}.process.json", dict(command=command, cwd="/tmp",
                                                 exit_code=exit_code, timed_out=timed_out))
    require(exit_code == 0, f"{name}: SST failed; see {stderr_path}")


def run(bundle, build, output, placements=PLACEMENTS, platform_path=HERE / "e0-run.json", *,
        device_limits=None, execution_budget=None, preflight_only=False, expected_backend_source=None,
        local_read_die_admission=False, nvme_read_die_admission=False, cache_snapshot=None,
        backfill=None, closed_concurrency=None):
    bundle, build, output, platform_path = (p.resolve() for p in (bundle, build, output, platform_path))
    require(placements and len(placements) == len(set(placements)) and all(p in EXECUTION_PLACEMENTS for p in placements),
            "invalid MaxSim placements")
    require(not local_read_die_admission or any(p in (*LOCAL_PLACEMENTS, *BACKFILL_PLACEMENTS) for p in placements),
            "local read die admission requires selecting a controller placement")
    require(not nvme_read_die_admission or any(p in (*NVME_PLACEMENTS, "hybrid_backfill_a") for p in placements),
            "NVMe read die admission requires selecting an ordinary-read placement")
    require((backfill is not None) == any(p in BACKFILL_PLACEMENTS for p in placements),
            "backfill configuration required when selecting path A or B")
    if closed_concurrency is not None:
        uint(closed_concurrency, "closed_concurrency", 1)
    if cache_snapshot is not None:
        require(type(cache_snapshot) is dict and cache_snapshot.keys() == {"host_capacity_bytes", "cache_oldest_first"}
                and type(cache_snapshot["cache_oldest_first"]) is list, "invalid cache snapshot fields")
        uint(cache_snapshot["host_capacity_bytes"], "host_capacity_bytes", 0)
        for doc in cache_snapshot["cache_oldest_first"]:
            uint(doc, "cache document ID", 0)
    require(bundle != output and bundle not in output.parents, "output must be outside the immutable input bundle")
    if device_limits is not None:
        require(type(device_limits) is dict and device_limits.keys() == {"xml_bytes", "metadata_bytes", "preload_pages"},
                "DeviceLimits require xml_bytes, metadata_bytes and preload_pages")
        for key, value in device_limits.items():
            uint(value, key, 1)
        require(preflight_only or execution_budget is not None, "custom DeviceLimits require an execution budget")
    if execution_budget is not None:
        execution_budget = budget(execution_budget)
    config, audit, backend, platform = checked_inputs(bundle, platform_path, expected_backend_source)
    if closed_concurrency is not None:
        require(closed_concurrency <= config["generation"]["queries"], "closed concurrency exceeds query count")
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library_dir = build / "workloads/maxsim"
    modules = [p for p in library_dir.glob("libndpsim_maxsim.*") if p.suffix in (".so", ".dylib")]
    require(sst.is_file() and len(modules) == 1, "build the ndpsim_maxsim SST element first")
    require(shutil.disk_usage(output.parent).free >= 32 * 1024**2 * (1 if preflight_only else len(placements)),
            "insufficient output space for E0 traces")
    output.mkdir()
    source_digest = backend_digest()
    sources = [*HERE.glob("*.cc"), *HERE.glob("*.h"), *HERE.glob("*.h.in"), *HERE.glob("*.py"),
               HERE / "CMakeLists.txt", ROOT / "CMakeLists.txt", ROOT / "src/backend/event_bridge.h",
               ROOT / "src/backend/device_config.cc", ROOT / "src/backend/device_config.h",
               ROOT / "src/layout/layout.h", ROOT / "src/layout/layout.cc", ROOT / "python/ndpsim/execution.py",
               ROOT / "src/timing/messages.h", ROOT / "src/timing/messages.cc"]
    write(output / "inputs.json", {
        "bundle": str(bundle), "generation_receipt_sha256": file_hash(bundle / "receipt.json"),
        "requested_device_limits": device_limits, "execution_budget": execution_budget,
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
    def prepare(name, placement, **extra):
        run_name = output / f"{name}.json"
        trace = output / f"{name}.jsonl"
        spec = {
            "format": "ndpsim-maxsim-run/1", "placement": placement, "bundle": str(bundle),
            "receipt_sha256": file_hash(bundle / "receipt.json"), "mqsim_source_sha256": source_digest,
            "scratch": f"{name}.sqlite", "output": trace.name, "platform": platform, **extra,
        }
        spec.update(die_admission(placement, local_read_die_admission, nvme_read_die_admission))
        snapshot = cache_snapshot if placement != "device_all" else None
        if closed_concurrency is not None:
            spec["closed_loop"] = dict(snapshot or {"host_capacity_bytes": 0, "cache_oldest_first": []},
                                       concurrency=closed_concurrency)
            spec["detailed_trace"] = True
        elif snapshot is not None:
            spec["cache_snapshot"] = snapshot
        if placement in BACKFILL_PLACEMENTS:
            spec["backfill"] = backfill
        if device_limits is not None:
            spec["device_limits"] = device_limits
        write(run_name, spec)
        command = [str(sst), f"--add-lib-path={library_dir}", str(HERE / "sst.py"),
                   "--", json.dumps(str(run_name), ensure_ascii=False)]
        return command, trace

    if preflight_only or execution_budget is not None:
        name = "device-preflight"
        command, trace = prepare(name, placements[0], preflight_only=True)
        execute(command, output, name)
        with trace.open("rb") as stream:
            header, result = map(decode, stream)
        require(result["type"] == "device_preflight" and result["success"] and not result["allocated"],
                "missing non-allocating device preflight report")
        report = dict(result, device=header["device"], package_sha256=audit["package_sha256"],
                      layout_sha256=header["layout_sha256"], trace_sha256=file_hash(trace))
        write(output / "device-preflight-report.json", report)
        if preflight_only:
            return report
        require(header["device"]["metadata_estimate_bytes"] <= execution_budget["rss_bytes"],
                "device metadata estimate exceeds execution RAM budget")
        preflight(output, execution_budget)
        enforce(output, execution_budget)

    summaries = {}
    for placement in placements:
        command, trace = prepare(placement, placement)
        if execution_budget is None:
            execute(command, output, placement)
        else:
            launch(command, output, execution_budget, cwd="/tmp", prefix=f"{placement}.")
        summary = {}
        with trace.open("rb") as stream:
            header = decode(next(stream))
            completed = []
            for line in stream:
                summary = decode(line)
                if summary["type"] == "query":
                    completed.append(summary)
        require(summary.get("type") == "summary" and summary["success"] and summary["all_resources_drained"],
                f"{placement}: no successful drained summary")
        verify_hybrid(bundle, (cache_snapshot or {}).get("cache_oldest_first", []), header, summary, completed)
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
    parser.add_argument("--placement", action="append", choices=EXECUTION_PLACEMENTS)
    parser.add_argument("--cache-snapshot", type=Path, help="JSON with host_capacity_bytes and cache_oldest_first")
    parser.add_argument("--backfill", type=Path, help="admission, access history and byte budget JSON for A/B")
    parser.add_argument("--closed-concurrency", type=int, help="run a directed closed loop with a detailed trace")
    parser.add_argument("--device-limits", type=Path, help="explicit DeviceLimits JSON")
    parser.add_argument("--budget", type=Path, help="RAM, disk and wall-time budget JSON for execution")
    parser.add_argument("--preflight-only", action="store_true", help="validate device and layout without allocating MQSim")
    parser.add_argument("--backend-source-sha256", help="bind an experimental in-tree MQSim source revision")
    parser.add_argument("--local-read-die-admission", action="store_true",
                        help="enable experimental die admission on placements using local reads")
    parser.add_argument("--nvme-read-die-admission", action="store_true",
                        help="enable experimental die admission on placements using ordinary reads")
    args = parser.parse_args()
    try:
        summary = run(args.input, args.build, args.output, args.placement or PLACEMENTS, args.platform,
                      device_limits=json_file(args.device_limits) if args.device_limits else None,
                      execution_budget=json_file(args.budget) if args.budget else None,
                      preflight_only=args.preflight_only, expected_backend_source=args.backend_source_sha256,
                      local_read_die_admission=args.local_read_die_admission,
                      nvme_read_die_admission=args.nvme_read_die_admission,
                      cache_snapshot=json_file(args.cache_snapshot) if args.cache_snapshot else None,
                      backfill=json_file(args.backfill) if args.backfill else None,
                      closed_concurrency=args.closed_concurrency)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"MaxSim E0: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
