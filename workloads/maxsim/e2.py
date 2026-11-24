"""Run the MaxSim E2 Poisson-load sweep using the verified E1 candidate bundles."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from ndpsim.static_ir import decode, file_hash, require

from generate import write
from run import HERE, PLACEMENTS, ROOT, backend_digest, checked_inputs, execute, json_file
from sampling import arrivals


# These E1 pairs have three seeds and capture the gain, default and crossover regimes.
SCENARIOS = {
    "uniform-0": (0, (6, 8, 24, 28, 32, 34, 36)),
    "zipf-0.8": (343_597_383, (8, 12, 14, 28, 32, 34, 36)),
    "zipf-1.2": (1_374_389_534, (28, 32, 34, 36, 40, 45)),
}
MAIN_RATES = (10, 30, 100, 300, 1000)
REPEAT_RATES = {
    "uniform-0": (6, 8, 10, 28, 30, 34),
    "zipf-0.8": (10, 12, 30, 34),
    "zipf-1.2": (10, 28, 30, 34, 36, 40),
}
CONCURRENCY_LIMIT = 16


def percentiles(values, prefix):
    samples = sorted(values)
    if not samples:
        return {}
    return {f"{prefix}_{label}_ms": samples[math.ceil(len(samples) * fraction) - 1] / 1e6
            for label, fraction in (("p50", .5), ("p95", .95), ("p99", .99))}


def summarize(trace, offsets, capacity):
    with trace.open("rb") as stream:
        rows = [decode(line) for line in stream]
    header, total = rows[0], rows[-1]
    count = len(offsets)
    origin = total["origin_ns"]
    assert_load = header["effective_config"]["load"]
    require(assert_load["mode"] == "open_loop" and assert_load["admission_limit"] == CONCURRENCY_LIMIT,
            "invalid open-loop configuration")
    require(total["type"] == "summary" and total["success"] and total["all_resources_drained"],
            "missing successful E2 drain")
    completed = sorted((row for row in rows if row["type"] == "query"), key=lambda row: row["query"])
    require(len(completed) == count, "E2 completion coverage")
    for query, (row, offset) in enumerate(zip(completed, offsets)):
        require(row["query"] == query and row["success"] and row["offered_ns"] == origin + offset and
                row["offered_ns"] <= row["admitted_ns"] <= row["visible_ns"] <= row["drained_ns"],
                "E2 arrival, admission or completion timestamp")
    derived = header["effective_config"]["execution"]
    require(total["peak_active_queries"] <= CONCURRENCY_LIMIT and
            total["peak_outstanding_reads"] <= derived["global_outstanding_reads"] and
            total["peak_read_window_documents"] <= derived["read_window_documents_per_query"] and
            total["peak_input_buffer_bytes"] <= derived["input_buffer_bytes_per_side"],
            "E2 resource ceiling exceeded")
    require(total["flop"] == count * 3_145_728_000 and
            total["effective_document_bytes"] + total["cache_hits"] * 655_360 == count * 98_304_000 and
            total["logical_reads"] * 65_536 == total["effective_document_bytes"] == total["nand_user_read_bytes"],
            "E2 Work or storage ledger disagrees")
    offload = header["placement"] == "device_all"
    require(total["document_d2h_payload_bytes"] == (0 if offload else total["effective_document_bytes"]) and
            total["controller_staging_bytes"] == (total["effective_document_bytes"] if offload else 0),
            "E2 placement byte ledger disagrees")

    # Maintain the same cutoff for all placements. The explicit final drain is
    # useful for accounting and completion diagnostics, but is outside offered load.
    middle = count // 2
    mid_ns, end_ns = origin + offsets[middle - 1], origin + offsets[-1]
    by_mid = sum(row["visible_ns"] <= mid_ns for row in completed)
    by_end = sum(row["visible_ns"] <= end_ns for row in completed)
    queued = sum(row["admitted_ns"] > end_ns for row in completed)
    late = count - by_end
    growth = late - (middle - by_mid)
    span = end_ns - mid_ns
    observed = [row for row in completed[middle:] if row["visible_ns"] <= end_ns]
    all_late_half = completed[middle:]
    waits = [row["admitted_ns"] - row["offered_ns"] for row in all_late_half]
    latencies = [row["visible_ns"] - row["offered_ns"] for row in observed]
    service_span = total["drained_ns"] - origin
    bus_ns = sum(row["mqsim"]["union_ns"] for row in rows if row["type"] == "nand_interval_union"
                 and row["mqsim"]["kind"] == "bus")
    array_ns = sum(row["mqsim"]["union_ns"] for row in rows if row["type"] == "nand_interval_union"
                   and row["mqsim"]["kind"] == "array")
    # An operational flag for this finite window. Near-zero growth or noisy
    # bursts at the knee are interpreted with the seed spread in the report.
    overload = growth > max(4, math.ceil((count - middle) * .05))
    return dict(
        queries=count, offered_qps=count * 1e9 / offsets[-1],
        completed_qps=by_end * 1e9 / offsets[-1],
        second_half_offered_qps=(count - middle) * 1e9 / span,
        second_half_completed_qps=(by_end - by_mid) * 1e9 / span,
        completed_by_cutoff=by_end, in_flight_at_cutoff=late - queued, queued_at_cutoff=queued,
        incomplete_at_cutoff=late, incomplete_after_drain=count - len(completed),
        backlog_growth_second_half=growth, overload_flag=overload,
        arrival_window_ns=offsets[-1], drain_after_cutoff_ns=total["drained_ns"] - end_ns,
        latency_samples=len(observed), within_cutoff_mean_ms=(sum(latencies) / len(latencies) / 1e6
                                                             if latencies else None),
        **percentiles(latencies, "within_cutoff"),
        **percentiles(waits, "entry_wait_including_drain"),
        cache_capacity_bytes=capacity, cache_hit_rate=total["cache_hit_rate"],
        logical_reads=total["logical_reads"], effective_read_bytes=total["effective_document_bytes"],
        nand_bytes=total["nand_read_bytes"], nand_amplification=total["nand_read_amplification"],
        staging_bytes=total["controller_staging_bytes"], vector_d2h_bytes=total["document_d2h_payload_bytes"],
        pcie_d2h_bytes=total["pcie"].get("D2H", {}).get("payload_bytes", 0),
        pcie_h2d_bytes=total["pcie"].get("H2D", {}).get("payload_bytes", 0),
        flops=total["flop"], scoring_ns=total["compute_ns"], scoring_batches=total["batches"],
        average_batch_documents=count * 150 / total["batches"],
        scoring_utilization=total["compute_ns"] / service_span,
        full_drain_effective_read_GBps=total["effective_document_bytes"] / service_span,
        full_drain_mean_channel_bus_utilization=bus_ns / (16 * service_span),
        full_drain_die_array_busy_ns=array_ns, peak_qd=total["peak_outstanding_reads"],
        peak_buffer_bytes=total["peak_input_buffer_bytes"], backpressure_ns=total["backpressure_ns"],
        trace_sha256=file_hash(trace))


def run_point(group, bundle, platform, source, sst, library, capacity, initial, rate, offsets, placement):
    name = f"qps{rate}-{placement}"
    spec_path = group / f"{name}.json"
    trace = spec_path.with_suffix(".jsonl")
    if placement == "device_all":
        capacity, initial = 0, []
    write(spec_path, dict(format="ndpsim-maxsim-run/1", placement=placement, bundle=str(bundle),
                          receipt_sha256=file_hash(bundle / "receipt.json"), mqsim_source_sha256=source,
                          scratch=f"{name}.sqlite", output=trace.name, platform=platform,
                          open_loop=dict(concurrency=CONCURRENCY_LIMIT, host_capacity_bytes=capacity,
                                         cache_oldest_first=initial, qps=rate, offered_offsets_ns=offsets)))
    command = [str(sst), f"--add-lib-path={library}", str(HERE / "sst.py"), "--", json.dumps(str(spec_path))]
    execute(command, group, name)
    result = dict(qps_target=rate, placement=placement, **summarize(trace, offsets, capacity))
    write(group / f"{name}.metrics.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--e1", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, action="append", choices=(11, 23, 47))
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--rate", type=int, action="append", help="override the selected scenarios' rates")
    args = parser.parse_args()
    output, e1, build = (path.resolve() for path in (args.output, args.e1, args.build))
    require(args.workers > 0 and shutil.disk_usage(output.parent).free >= 4 * 1024**3,
            "invalid worker limit or insufficient free space")
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    element = library / "libndpsim_maxsim.so"
    require(sst.is_file() and element.is_file(), "build the MaxSim SST module first")
    output.mkdir()
    source = backend_digest()
    write(output / "sweep.json", dict(command=sys.argv, backend_source_sha256=source,
                                    simulator_commit=subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                                            cwd=ROOT, text=True).strip(),
                                    source_sha256={p.name: file_hash(p) for p in sorted(HERE.iterdir())
                                                   if p.suffix in (".py", ".cc", ".h", ".json", ".xml")},
                                    sst_sha256=file_hash(sst), element_sha256=file_hash(element),
                                    e1_comparison_sha256=file_hash(e1 / "report/comparison.csv"),
                                    max_active_queries=CONCURRENCY_LIMIT, sst_wall_timeout_seconds=180,
                                    measurement_end="last offered arrival",
                                    unavailable=["plane_utilization", "physical_host_dram",
                                                 "independent_reduction_cost", "topk_hardware_cost"]))
    futures = {}
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        for scenario in args.scenario or SCENARIOS:
            capacity, near_knee = SCENARIOS[scenario]
            for seed in args.seed or (11, 23, 47):
                key = f"{scenario}-seed{seed}"
                previous = e1 / ("warm-c64" if seed == 11 else "extra-seeds-c64") / key
                group = output / key
                group.mkdir()
                bundle = previous / "bundle"
                config, _, backend, platform = checked_inputs(bundle, HERE / "e0-run.json", source)
                write(group / "backend.json", backend)
                initial = json_file(previous / "warmup.json")["oldest_first"][str(capacity // 655_360)]
                rates = set(args.rate or (near_knee + MAIN_RATES if seed == 11 else REPEAT_RATES[scenario]))
                for rate in sorted(rates):
                    config["load"].update(mode="open_loop", open_loop_qps=rate)
                    offsets = [row["offered_ns"] for row in arrivals(config)]
                    require(all(b >= a for a, b in zip(offsets, offsets[1:])) and offsets[-1] > 0,
                            "invalid Poisson arrival sequence")
                    for placement in PLACEMENTS:
                        future = workers.submit(run_point, group, bundle, platform, source, sst, library,
                                                capacity, initial, rate, offsets, placement)
                        futures[future] = (scenario, seed, rate, placement)
        errors = []
        for future in as_completed(futures):
            scenario, seed, rate, placement = futures[future]
            try:
                result = future.result()
                rows.append(dict(scenario=scenario, seed=seed, **result))
                print(scenario, seed, rate, placement, round(result["second_half_completed_qps"], 2),
                      result["incomplete_at_cutoff"], flush=True)
            except Exception as exc:
                errors.append(dict(scenario=scenario, seed=seed, rate=rate, placement=placement, error=str(exc)))
                print(errors[-1], file=sys.stderr, flush=True)
    if rows:
        fields = list(dict.fromkeys(field for row in rows for field in row))
        with (output / "matrix.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda row: (row["scenario"], row["seed"], row["qps_target"],
                                                           row["placement"])))
    if errors:
        write(output / "errors.json", errors)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
