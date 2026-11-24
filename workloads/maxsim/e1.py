"""Run the MaxSim E1 closed-loop matrix on the 10,000-page MQSim device."""

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from ndpsim.static_ir import decode, file_hash, require

from config import load_config
from generate import generate, write
from run import HERE, PLACEMENTS, ROOT, backend_digest, checked_inputs, execute, json_file
from sampling import Candidates
from scan import points


MODEL_REVISION = "c397ea7ab9eda41efecac64659ba46d9399bad38"


def prewarm(config, capacities, window, max_windows):
    """Replay a separate seeded prefix. Stop once three full windows agree."""
    warmed = deepcopy(config)
    warmed["generation"]["candidate_seed"] += 2**32
    sampler = Candidates(warmed)
    caches = {size: OrderedDict() for size in capacities if size}
    history = {size: [] for size in capacities}
    for _ in range(max_windows):
        hits = dict.fromkeys(caches, 0)
        for _ in range(window):
            for document in sampler.draw():
                for size, cache in caches.items():
                    if document in cache:
                        hits[size] += 1
                        cache.move_to_end(document)
                    else:
                        if len(cache) == size:
                            cache.popitem(last=False)
                        cache[document] = None
        for size, rates in history.items():
            rates.append(hits.get(size, 0) / (window * sampler.count))
        if all(len(rates) >= 3 and max(rates[-3:]) - min(rates[-3:]) < .01
               for rates in history.values()) and all(len(cache) == size for size, cache in caches.items()):
            break
    else:
        raise ValueError("cache prewarm did not converge")
    return dict(candidate_seed=warmed["generation"]["candidate_seed"], window_queries=window,
                window_hit_rates=history,
                oldest_first={size: list(caches.get(size, [])) for size in capacities})


def summarize(trace, queries, concurrency):
    with trace.open("rb") as stream:
        records = [decode(line) for line in stream]
    header, summary = records[0], records[-1]
    require(summary["type"] == "summary" and summary["success"] and summary["all_resources_drained"],
            "missing successful MaxSim report")
    completed = sorted((row for row in records if row["type"] == "query"), key=lambda row: row["visible_ns"])
    require(len(completed) == queries and {row["query"] for row in completed} == set(range(queries)) and
            all(row["success"] for row in completed), "query coverage")
    require(summary["peak_active_queries"] == concurrency, "closed-loop concurrency was not reached")
    require(summary["flop"] == queries * 3_145_728_000 and
            summary["effective_document_bytes"] + summary["cache_hits"] * 655_360 == queries * 98_304_000,
            "MaxSim work or cache/miss byte conservation")
    require(summary["logical_reads"] * 65_536 == summary["effective_document_bytes"] ==
            summary["nand_user_read_bytes"], "MaxSim I/O and NAND user-byte conservation")
    schedule = header["effective_config"]["execution"]
    require(summary["peak_outstanding_reads"] <= schedule["global_outstanding_reads"] and
            summary["peak_read_window_documents"] <= schedule["read_window_documents_per_query"] and
            summary["peak_input_buffer_bytes"] <= schedule["input_buffer_bytes_per_side"],
            "resource limit violation")
    begin = summary["origin_ns"]
    end = completed[-1]["visible_ns"]
    offers = sorted(row["offered_ns"] for row in completed)
    drains = sorted(row["drained_ns"] for row in completed)
    require(offers[:concurrency] == [begin] * concurrency and offers[concurrency:] == drains[:queries - concurrency],
            "closed-loop starts must replace drained queries at the same simulation time")
    # Query completions can occur in bursts at high concurrency. Use the full
    # finite closed-loop makespan so the QPS estimate covers those bursts.
    latencies = sorted(row["visible_ns"] - row["offered_ns"] for row in completed)
    require(end > begin and min(latencies) > 0, "invalid measurement window")

    def quantile(fraction):
        return latencies[math.ceil(len(latencies) * fraction) - 1] / 1e6

    span = summary["drained_ns"] - summary["origin_ns"]
    bus = [x["mqsim"]["union_ns"] for x in records if x["type"] == "nand_interval_union"
           and x["mqsim"]["kind"] == "bus"]
    array = [x["mqsim"]["union_ns"] for x in records if x["type"] == "nand_interval_union"
             and x["mqsim"]["kind"] == "array"]
    return dict(simulated_queries=queries, latency_samples=len(latencies),
                qps=queries * 1e9 / (end - begin), mean_ms=sum(latencies) / len(latencies) / 1e6,
                p50_ms=quantile(.5), p95_ms=quantile(.95), p99_ms=quantile(.99),
                cache_hit_rate=summary["cache_hit_rate"],
                effective_read_GBps=summary["effective_document_bytes"] / span,
                nand_read_bytes=summary["nand_read_bytes"], nand_amplification=summary["nand_read_amplification"],
                controller_staging_bytes=summary["controller_staging_bytes"],
                document_d2h_bytes=summary["document_d2h_payload_bytes"],
                pcie_d2h_payload_bytes=summary["pcie"].get("D2H", {}).get("payload_bytes", 0),
                pcie_h2d_payload_bytes=summary["pcie"].get("H2D", {}).get("payload_bytes", 0),
                flops=summary["flop"],
                logical_reads=summary["logical_reads"],
                scoring_utilization=summary["compute_ns"] / span, scoring_batches=summary["batches"],
                average_batch_documents=queries * 150 / summary["batches"],
                channel_utilization=sum(bus) / (16 * span),
                die_array_busy_ns=sum(array),
                peak_qd=summary["peak_outstanding_reads"], peak_buffer_bytes=summary["peak_input_buffer_bytes"],
                backpressure_ns=summary["backpressure_ns"], measurement_start_ns=begin, measurement_end_ns=end,
                entry_wait_ns=0, incomplete_queries=0,
                unavailable=["plane_utilization", "physical_host_dram", "topk_hardware_cost",
                             "independent_reduction_cost"], sha256=file_hash(trace))


def run_point(directory, name, placement, bundle, platform, source, sst, library, concurrency, capacity, snapshot,
              queries):
    spec_path = directory / f"{name}.json"
    trace = spec_path.with_suffix(".jsonl")
    spec = dict(format="ndpsim-maxsim-run/1", placement=placement, bundle=str(bundle),
                receipt_sha256=file_hash(bundle / "receipt.json"), mqsim_source_sha256=source,
                scratch=f"{name}.sqlite", output=trace.name, platform=platform,
                closed_loop=dict(concurrency=concurrency, host_capacity_bytes=capacity, cache_oldest_first=snapshot))
    write(spec_path, spec)
    cmd = [str(sst), f"--add-lib-path={library}", str(HERE / "sst.py"), "--", json.dumps(str(spec_path))]
    execute(cmd, directory, name)
    result = dict(run=name, placement=placement, concurrency=concurrency, capacity_bytes=capacity,
                  **summarize(trace, queries, concurrency))
    write(directory / f"{name}.metrics.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--backend-source-sha256", required=True)
    parser.add_argument("--queries", type=int, required=True)
    parser.add_argument("--seed", type=int, action="append", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--warmup-window", type=int, default=10_000)
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--point", action="append", help="restrict execution to named E1 points")
    parser.add_argument("--reuse-candidates", type=Path, help="use matching bundles and prewarm snapshots from an existing sweep")
    args = parser.parse_args()
    output, build = args.output.resolve(), args.build.resolve()
    matrix = [(name, point) for name, point in points()
              if name.startswith("E1-") and (args.point is None or name in args.point)]
    require(matrix and (args.point is None or set(args.point) == {name for name, _ in matrix}) and
            args.workers > 0 and args.warmup_window > 0 and shutil.disk_usage(output.parent).free >= 4 * 1024**3,
            "invalid matrix, worker count or available disk")
    require(args.queries > 2 * max(point["load"]["closed_loop_concurrency"] for _, point in matrix),
            "need more than two replacement waves per run")
    source = backend_digest()
    require(source == args.backend_source_sha256, "MQSim source hash mismatch")
    output.mkdir()
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    modules = list(library.glob("libndpsim_maxsim.so"))
    require(sst.is_file() and len(modules) == 1, "build the MaxSim SST module first")
    platform_path = HERE / "e0-run.json"
    platform = json_file(platform_path)
    write(output / "sweep.json", dict(command=sys.argv, backend_source_sha256=source,
                                    simulator_commit=subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                                            cwd=ROOT, text=True).strip(),
                                    sources_sha256={p.name: file_hash(p) for p in sorted(HERE.iterdir())
                                                    if p.suffix in (".py", ".cc", ".h", ".json", ".xml")},
                                    platform_sha256=file_hash(platform_path),
                                    model_revision=MODEL_REVISION, sst_sha256=file_hash(sst),
                                    element_sha256=file_hash(modules[0]), matrix=dict(matrix)))
    futures, pairs = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        for seed in args.seed:
            for distribution, alpha in dict.fromkeys((point["workload"]["distribution"], point["workload"]["zipf_alpha"])
                                                     for _, point in matrix):
                group = output / f"{distribution}-{alpha}-seed{seed}"
                group.mkdir()
                subset = [(name, point) for name, point in matrix if point["workload"]["zipf_alpha"] == alpha]
                overrides = dict(workload=dict(distribution=distribution, zipf_alpha=alpha, model_revision=MODEL_REVISION),
                                 generation=dict(queries=args.queries))
                configuration = load_config(HERE / "local-10k.json", overrides, seed)
                sizes = {name: load_config(override=point)["cache"]["host_capacity_bytes"] for name, point in subset}
                page_sizes = {size // 655_360 for size in sizes.values()}
                bundle = group / "bundle"
                if args.reuse_candidates:
                    previous = args.reuse_candidates.resolve() / group.name
                    bundle = previous / "bundle"
                    snapshot = json_file(previous / "warmup.json")
                    expected = json_file(bundle / "effective-config.json")["config"]
                    require(configuration == expected, "reused candidate bundle does not match configured inputs")
                else:
                    generate(configuration, bundle, sys.argv, override_path=HERE / "local-10k.json")
                    snapshot = prewarm(configuration, page_sizes, args.warmup_window, 8) if not args.cold else {}
                if snapshot:
                    snapshot["oldest_first"] = {int(k): v for k, v in snapshot["oldest_first"].items()}
                write(group / "warmup.json", snapshot)
                _, _, backend, _ = checked_inputs(bundle, platform_path, source)
                write(group / "backend.json", backend)
                for name, point in subset:
                    concurrency = point["load"]["closed_loop_concurrency"]
                    for placement in PLACEMENTS:
                        effective_capacity = sizes[name] if placement != "device_all" else 0
                        run = f"c{concurrency}-b{effective_capacity}-{placement}"
                        key = (str(group), run)
                        pairs.append((name, seed, distribution, alpha, sizes[name], concurrency, placement, key))
                        if key not in futures:
                            initial = (snapshot["oldest_first"][effective_capacity // 655_360]
                                       if snapshot and effective_capacity else [])
                            futures[key] = workers.submit(run_point, group, run, placement, bundle, platform, source,
                                                          sst, library, concurrency, effective_capacity, initial,
                                                          args.queries)
        reverse = {future: key for key, future in futures.items()}
        results = {}
        for future in as_completed(reverse):
            result = future.result()
            key = reverse[future]
            results[key] = result
            print(Path(key[0]).name, key[1], round(result["qps"], 2), flush=True)
    rows = []
    for name, seed, distribution, alpha, capacity, concurrency, placement, key in pairs:
        rows.append(dict(results[key], point=name, seed=seed, distribution=distribution, zipf_alpha=alpha,
                         capacity_bytes=capacity))
    with (output / "matrix.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
