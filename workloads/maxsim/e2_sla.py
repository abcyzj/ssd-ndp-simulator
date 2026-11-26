"""Run the §7.3 fixed-SLA MaxSim capacity experiment on the 9550 PRO candidate."""

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
import csv
from itertools import islice, product
import math
from pathlib import Path
import subprocess
import sys

from ndpsim.execution import identities, preflight as execution_preflight
from ndpsim.static_ir import decode, file_hash, require

from config import derive, load_config, preflight as generation_preflight
from e2 import CONCURRENCY_LIMIT, PATH_A, run_point
from generate import generate, sha_map, write
from run import HERE, ROOT, backend_digest, checked_inputs, json_file
from sampling import arrivals


SEEDS = (11, 23, 47)
PLACEMENTS = ("host", PATH_A)
COARSE = (0, 131, 524, 2097)
OFFERED_QPS = 220
MEASURED = 512
P99_NS = 40_000_000
TIMEOUT_NS = 100_000_000
TIMEOUT_PER_MILLE = 1
MAX_BACKLOG_GROWTH = max(4, math.ceil((MEASURED // 2) * .05))
BACKFILL = dict(policy="frequency", history_window_accesses=10_000, initial_history_oldest_first=[],
                budget_window_ns=1_000_000, budget_bytes=5_242_880)
GIB = 1024**3
RUN_BUDGET = dict(rss_bytes=GIB, output_bytes=GIB // 4, scratch_bytes=GIB // 2,
                  min_disk_free_bytes=4 * GIB, timeout_seconds=180)
DEVICE = HERE / "micron-9550-pro.json"
PLATFORM = HERE / "e0-run.json"


def prepare_seed(output, prior, seed, source, reuse):
    group = output / f"seed{seed}"
    group.mkdir()
    previous = prior / f"zipf-0.8-seed{seed}"
    old_bundle = previous / "bundle"
    old = json_file(old_bundle / "effective-config.json")["config"]
    overrides = dict(workload=dict(distribution="zipf", zipf_alpha=.8,
                                   model_revision=old["workload"]["model_revision"]),
                     generation=dict(queries=MEASURED + 1),
                     load=dict(mode="open_loop", open_loop_qps=OFFERED_QPS))
    config = load_config(DEVICE, overrides, seed)
    old_settings = dict(old, generation=dict(old["generation"], queries=MEASURED + 1), load=config["load"])
    require(old_settings == config, "configuration drift relative to E1")
    offsets = [row["offered_ns"] for row in arrivals(config)]
    horizon = offsets[MEASURED - 1] + TIMEOUT_NS
    # Extend the same arrival RNG stream in blocks until the next unoffered
    # arrival is known. No arrival at or before the observation horizon is lost.
    while offsets[-1] <= horizon:
        config["generation"]["queries"] += 64
        offsets = [row["offered_ns"] for row in arrivals(config)]
    guard_end = bisect_right(offsets, horizon)
    config["generation"]["queries"] = guard_end
    write(group / "arrivals.json", dict(measurement_cutoff_ns=offsets[MEASURED - 1],
                                         observation_cutoff_ns=horizon,
                                         first_unoffered_ns=offsets[guard_end], offsets_ns=offsets[:guard_end]))
    snapshots = json_file(previous / "warmup.json")["oldest_first"]
    largest = snapshots[str(COARSE[-1])]
    require(len(largest) == COARSE[-1] and len(set(largest)) == len(largest) and
            all(snapshots[str(size)] == largest[len(largest) - size:] for size in COARSE),
            "E1 caches do not match the LRU stack")
    write(group / "warmup.json", dict(e1_snapshot_sha256=file_hash(previous / "warmup.json"),
                                      oldest_first=largest))
    bundle = group / "bundle"
    derived = derive(config)
    if reuse is None:
        write(group / "generation-preflight.json", generation_preflight(config, derived, bundle))
        generate(config, bundle, sys.argv, override_path=DEVICE)
        _, _, backend, platform = checked_inputs(bundle, PLATFORM, source)
    else:
        previous_seed = reuse / group.name
        original = previous_seed / "bundle"
        receipt_path = original / "receipt.json"
        receipt = json_file(receipt_path)
        require(receipt["passed"] and receipt["artifacts"] == sha_map(original) and
                json_file(original / "effective-config.json")["config"] == config,
                "reused bundle config or artifact identity mismatch")
        require((previous_seed / "arrivals.json").read_bytes() == (group / "arrivals.json").read_bytes() and
                (previous_seed / "warmup.json").read_bytes() == (group / "warmup.json").read_bytes(),
                "reused arrivals or warmup identity mismatch")
        backend = json_file(previous_seed / "backend.json")
        require(backend["head_commit"] == config["hardware"]["backend_commit"], "reused MQSim identity mismatch")
        platform = json_file(PLATFORM)
        bundle.symlink_to(original, target_is_directory=True)
        write(group / "reused-bundle.json", dict(path=str(original), sha256=file_hash(receipt_path)))
    for name in ("candidates.jsonl", "logical-reads.jsonl"):
        with (old_bundle / name).open("rb") as reference, (bundle / name).open("rb") as extended:
            require(list(islice(extended, old["generation"]["queries"])) == list(reference),
                    f"extended package changed E1 {name}")
    require(file_hash(bundle / "layout.json") == file_hash(old_bundle / "layout.json"), "E1 layout drift")
    require((bundle / "logical-reads.jsonl").stat().st_size <= 128 * 1024**2, "SST read ledger limit")
    write(group / "backend.json", backend)
    return group, offsets[:guard_end], largest, derived["document_bytes"], platform


def prepare(args, output):
    calibration = json_file(args.calibration / "nvme/inputs.json")
    reference = json_file(args.calibration / "verification.json")
    require(reference["passed"], "E0 calibration and execution evidence must pass")
    prior = json_file(args.e1 / "sweep.json")
    sst = Path(json_file(args.build / "sst-paths.json")["sst"])
    library = args.build / "workloads/maxsim"
    source = backend_digest()
    identity = dict(backend_source_sha256=source, sst_sha256=file_hash(sst),
                    element_sha256=file_hash(library / "libndpsim_maxsim.so"))
    require(all(value == calibration[key] for key, value in identity.items()) and
            all(prior[key] == identity[key] for key in ("backend_source_sha256", "sst_sha256")) and
            (prior["element_sha256"] == identity["element_sha256"] or
             (reference.get("original_element_sha256") == prior["element_sha256"] and
              reference.get("element_sha256") == identity["element_sha256"] and
              reference.get("calibration_inputs_sha256") == file_hash(args.calibration / "nvme/inputs.json"))) and
            file_hash(DEVICE) == calibration["config_sha256"] and
            file_hash(DEVICE.with_name("micron-9550-pro-device.xml")) == calibration["xml_sha256"],
            "NVMe calibration, E1 and actual device/build must match")
    output.mkdir()
    resources = execution_preflight(output, RUN_BUDGET)
    require(args.workers * RUN_BUDGET["rss_bytes"] <= resources["available_ram_estimate_bytes"],
            "aggregate SST RSS budget exceeds available RAM")
    require(resources["disk_free_bytes"] >= 12 * GIB, "capacity experiment needs 12 GiB of free storage")
    write(output / "experiment.json", dict(
        hardware_status="configured_unvalidated", command=sys.argv,
        simulator_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        source_sha256={p.name: file_hash(p) for p in sorted(HERE.iterdir())
                       if p.suffix in (".py", ".cc", ".h", ".json", ".xml")},
        nvme_calibration_sha256=file_hash(args.calibration / "nvme/inputs.json"),
        e0_verification_sha256=file_hash(args.calibration / "verification.json"),
        e1_sweep_sha256=file_hash(args.e1 / "sweep.json"), device_sha256=file_hash(DEVICE),
        e1_element_sha256=prior["element_sha256"], observer_sha256=file_hash(ROOT / "src/backend/observation.h"),
        reused_bundle_batch=str(args.reuse_bundles) if args.reuse_bundles else None,
        qps=OFFERED_QPS, distribution="zipf", zipf_alpha=.8, admission_limit=CONCURRENCY_LIMIT,
        seeds=SEEDS, placements=PLACEMENTS, coarse_documents=COARSE, measured_arrivals_per_seed=MEASURED,
        p99_ns=P99_NS, timeout_ns=TIMEOUT_NS, timeout_per_mille=TIMEOUT_PER_MILLE,
        max_second_half_backlog_growth=MAX_BACKLOG_GROWTH, capacity_pass_rule="all seeds pass",
        backfill=BACKFILL, **identity))
    with ThreadPoolExecutor(max_workers=min(args.workers, len(SEEDS))) as pool:
        seed_inputs = dict(zip(SEEDS, pool.map(
            lambda seed: prepare_seed(output, args.e1, seed, source, args.reuse_bundles), SEEDS)))
    return seed_inputs, source, sst, library


def cohort_metrics(trace, offsets):
    with trace.open("rb") as stream:
        rows = [decode(line) for line in stream]
    total = rows[-1]
    origin = total["origin_ns"]
    mid_ns = origin + offsets[MEASURED // 2 - 1]
    cutoff_ns = origin + offsets[MEASURED - 1]
    horizon = cutoff_ns + TIMEOUT_NS
    cohort = sorted((row for row in rows if row["type"] == "query" and row["query"] < MEASURED),
                    key=lambda row: row["query"])
    require([row["query"] for row in cohort] == list(range(MEASURED)), "SLA cohort coverage")
    require(all(row["offered_ns"] - origin == offsets[row["query"]] for row in cohort),
            "SLA cohort arrival identity")
    complete = [row for row in cohort if row["visible_ns"] <= horizon]
    latencies = [row["visible_ns"] - row["offered_ns"] for row in complete]
    # Censored queries have latency strictly greater than this lower bound.
    # If the observed rank lies below every bound, the empirical P99 is exact.
    censored_bounds = [horizon - row["offered_ns"] for row in cohort if row["visible_ns"] > horizon]
    rank = math.ceil(MEASURED * .99)
    bounds = sorted(latencies + censored_bounds)
    actual = sorted(latencies)
    p99 = actual[rank - 1] if len(actual) >= rank else None
    if p99 is not None and censored_bounds and p99 > min(censored_bounds):
        p99 = None
    p99_misses = len(censored_bounds) + sum(latency > P99_NS for latency in latencies)
    timeouts = len(censored_bounds) + sum(latency > TIMEOUT_NS for latency in latencies)
    completed_mid = sum(row["visible_ns"] <= mid_ns for row in cohort)
    completed_cutoff = sum(row["visible_ns"] <= cutoff_ns for row in cohort)
    incomplete = MEASURED - completed_cutoff
    queued = sum(row["admitted_ns"] > cutoff_ns for row in cohort)
    growth = incomplete - (MEASURED // 2 - completed_mid)
    checks = dict(p99_pass=p99_misses <= MEASURED - rank,
                  timeout_pass=timeouts * 1000 <= MEASURED * TIMEOUT_PER_MILLE,
                  backlog_pass=growth <= MAX_BACKLOG_GROWTH)
    return dict(measured_queries=MEASURED, guard_arrivals=len(offsets) - MEASURED,
                measurement_cutoff_ns=cutoff_ns - origin, observation_cutoff_ns=horizon - origin,
                observed_by_horizon=len(complete), p99_ms=p99 / 1e6 if p99 is not None else None,
                p99_lower_ms=bounds[rank - 1] / 1e6, p99_misses=p99_misses,
                timeouts=timeouts, timeout_pct=100 * timeouts / MEASURED,
                completed_by_cutoff=completed_cutoff, incomplete_at_cutoff=incomplete,
                queued_at_cutoff=queued, in_flight_at_cutoff=incomplete - queued,
                backlog_growth=growth, completed_second_half_qps=(completed_cutoff - completed_mid) * 1e9 /
                (cutoff_ns - mid_ns), drained=total["all_resources_drained"], **checks, passed=all(checks.values()))


def run_case(source, sst, library, inputs, output, placement, documents, seed):
    group, offsets, largest, doc_bytes, platform = inputs[seed]
    case = output / f"{placement}-docs{documents}-seed{seed}"
    case.mkdir()
    snapshot = largest[len(largest) - documents:]
    capacity = documents * doc_bytes
    diagnostic = run_point(case, group / "bundle", platform, source, sst, library,
                           capacity, snapshot, OFFERED_QPS, offsets, placement,
                           local_read_die_admission=True, nvme_read_die_admission=True,
                           backfill=BACKFILL if placement == PATH_A else None, execution_budget=RUN_BUDGET)
    stem = f"qps{OFFERED_QPS}-{placement}"
    trace = case / f"{stem}.jsonl"
    result = dict(placement=placement, documents=documents, seed=seed, capacity_bytes=capacity,
                  capacity_gib=capacity / GIB, **cohort_metrics(trace, offsets),
                  trace_sha256=diagnostic["trace_sha256"], case=str(case.relative_to(output.parent)))
    write(case / "sla.json", result)
    print(placement, documents, seed, result["p99_ms"], result["timeouts"], result["backlog_growth"],
          result["passed"], flush=True)
    return result


def write_matrix(path, rows):
    ordered = sorted(rows, key=lambda row: (row["placement"], row["documents"], row["seed"]))
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ordered[0]))
        writer.writeheader()
        writer.writerows(ordered)


def passed(rows):
    return tuple(row["seed"] for row in rows) == SEEDS and all(row["passed"] for row in rows)


def bracket(points):
    feasible = [docs for docs, rows in points.items() if passed(rows)]
    if not feasible:
        return None, None
    high = min(feasible)
    return max((docs for docs in points if docs < high), default=None), high


def scan(output, inputs, source, sst, library, workers):
    cases = output / "cases"
    cases.mkdir()
    points = {placement: {} for placement in PLACEMENTS}

    def probe(pool, requests):
        tasks = {(placement, docs): [pool.submit(run_case, source, sst, library, inputs, cases,
                                                 placement, docs, seed) for seed in SEEDS]
                 for placement, docs in requests}
        for (placement, docs), runs in tasks.items():
            points[placement][docs] = [run.result() for run in runs]
        write_matrix(output / "matrix.csv", [row for by_docs in points.values()
                                             for batch in by_docs.values() for row in batch])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        probe(pool, list(product(PLACEMENTS, COARSE)))
        while True:
            requests = []
            for placement, by_docs in points.items():
                low, high = bracket(by_docs)
                if low is not None and high - low > 1:
                    requests.append((placement, (low + high) // 2))
            if not requests:
                break
            probe(pool, requests)
    summary = {}
    for placement, by_docs in points.items():
        low, high = bracket(by_docs)
        summary[placement] = dict(minimum_passing_documents=high, adjacent_failing_documents=low,
                                  minimum_passing_runs=by_docs.get(high),
                                  tested_passing_documents=sorted(docs for docs in by_docs if passed(by_docs[docs])),
                                  tested_failing_documents=sorted(docs for docs in by_docs if not passed(by_docs[docs])),
                                  tested_range_documents=[min(by_docs), max(by_docs)])
    host, hybrid = (summary[placement]["minimum_passing_documents"] for placement in PLACEMENTS)
    write(output / "summary.json", dict(placements=summary,
                                        cache_saving_documents=host - hybrid if host is not None and
                                        hybrid is not None else None,
                                        cache_saving_pct=100 * (host - hybrid) / host if host and
                                        hybrid is not None else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--e1", type=Path, required=True, help="prewarmed §7.3 E1 sweep directory")
    parser.add_argument("--calibration", type=Path, required=True, help="verified §7.3 E0 evidence directory")
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--reuse-bundles", type=Path, help="reuse the sealed inputs from an earlier SLA batch")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    for field in ("output", "e1", "calibration", "build"):
        setattr(args, field, getattr(args, field).resolve())
    if args.reuse_bundles is not None:
        args.reuse_bundles = args.reuse_bundles.resolve()
    require(args.workers > 0, "worker count must be positive")
    output = args.output
    inputs, source, sst, library = prepare(args, output)
    pilot = output / "pilot"
    pilot.mkdir()
    for placement in PLACEMENTS:
        run_case(source, sst, library, inputs, pilot, placement, COARSE[-1], SEEDS[0])
    scan(output, inputs, source, sst, library, args.workers)
    write(output / "sha256.json", identities(output))


if __name__ == "__main__":
    main()
