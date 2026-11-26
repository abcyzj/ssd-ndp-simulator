"""Prepare, run and audit the §7.3 hot/cold open-loop MaxSim matrix."""

import argparse
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import csv
from itertools import product
import json
import math
from pathlib import Path
from statistics import median
import subprocess
import sys
import time

from ndpsim.execution import launch, preflight
from ndpsim.static_ir import file_hash, require

from config import derive, load_config, preflight as generation_preflight
from e2 import CONCURRENCY_LIMIT, PATH_A, summarize
from e2_sla import BACKFILL, DEVICE, GIB, PLACEMENTS, PLATFORM, RUN_BUDGET, SEEDS, TIMEOUT_NS
from generate import generate, write
from run import HERE, backend_digest, die_admission, json_file
from sampling import Candidates, stream
from verify import rows

CAPACITY = 2097
HOT_POOL = 1024
HOT_RATES = (100, 220)
COLD_RATES = (50, 100, 200)
P99_NS = 70_000_000
SHORT_NS = 2_000_000_000
LONG_NS = 3_100_000_000


def groups(batch):
    if batch == "pilot":
        return [(100, 100), (100, 0), (0, 100)]
    if batch == "long":
        return [(220, 200), (220, 0), (0, 200)]
    return [*product(HOT_RATES, COLD_RATES), *((hot, 0) for hot in HOT_RATES),
            *((0, cold) for cold in COLD_RATES)]


def group_path(output, seed, hot, cold):
    return output / f"seed{seed}-hot{hot}-cold{cold}"


def candidate_sampler(config, label, seed, pool):
    settings = dict(config, workload=dict(config["workload"], corpus_documents=len(pool),
                                          zipf_alpha=.8 if label == "hot" else 0,
                                          randomize_rank_to_document=False))
    sampler = Candidates(settings)
    sampler.rng = stream(label, seed)
    sampler.rank_to_id = pool
    return sampler


def traffic_stream(config, pools, label, rate, seed, horizon):
    if rate == 0:
        return
    arrival_rng = stream(f"mixed/{label}/arrivals", seed)
    sampler = candidate_sampler(config, label, seed, pools[label])
    seconds = 0
    sequence = 0
    while True:
        seconds += arrival_rng.expovariate(rate)
        offset = math.ceil(seconds * 1e9)
        if offset > horizon:
            return
        yield dict(traffic_class=label, request_id=f"{label}-{sequence}", offered_ns=offset,
                   document_ids=sampler.draw())
        sequence += 1


def prepare_group(output, e1, seed, hot, cold, duration):
    group = group_path(output, seed, hot, cold)
    group.mkdir()
    reference = e1 / f"zipf-0.8-seed{seed}"
    old = json_file(reference / "bundle/effective-config.json")["config"]
    config = load_config(DEVICE, dict(
        workload=dict(distribution="zipf", zipf_alpha=.8, model_revision=old["workload"]["model_revision"]),
        load=dict(mode="open_loop", open_loop_qps=hot + cold)), seed)
    ranking = list(Candidates(config).rank_to_id)
    pools = {"hot": ranking[:HOT_POOL], "cold": ranking[HOT_POOL:]}
    traffic = sorted((row for label, rate in (("hot", hot), ("cold", cold))
                      for row in traffic_stream(config, pools, label, rate, seed, duration + TIMEOUT_NS)),
                     key=lambda row: (row["offered_ns"], row["traffic_class"]))
    for query, row in enumerate(traffic):
        row["query"] = query
    config["generation"]["queries"] = len(traffic)
    if duration == LONG_NS:
        config["generation"].update(package_budget_bytes=4 * GIB, scratch_budget_bytes=8 * GIB)

    warmup_path = reference / "warmup.json"
    lru = OrderedDict.fromkeys(json_file(warmup_path)["oldest_first"][str(CAPACITY)])
    hot_sampler = candidate_sampler(config, "hot", f"mixed/warmup/{seed}", pools["hot"])
    warmed = set()
    warm_queries = 0
    while len(warmed) < HOT_POOL:
        require(warm_queries < 10_000, "warmup did not populate the full hot pool")
        for doc in hot_sampler.draw():
            lru.pop(doc, None)
            lru[doc] = None
            if len(lru) > CAPACITY:
                lru.popitem(last=False)
            warmed.add(doc)
        warm_queries += 1
    require(len(lru) == CAPACITY and set(list(lru)[-HOT_POOL:]) == set(pools["hot"]),
            "snapshot must be full with the hot pool at the newest end")

    write(group / "input.json", dict(seed=seed, hot_qps=hot, cold_qps=cold, pools=pools,
          warm_queries=warm_queries, e1_snapshot_sha256=file_hash(warmup_path), snapshot=list(lru),
          duration_ns=duration, guard_ns=TIMEOUT_NS,
          tie_order=["cold", "hot"], stream_domains=["mixed/hot/arrivals", "mixed/cold/arrivals", "hot", "cold"],
          independent_warmup_seed=f"mixed/warmup/{seed}"))
    bundle = group / "bundle"
    budget = generation_preflight(config, derive(config), bundle)
    write(group / "generation-preflight.json", budget)
    generate(config, bundle, [sys.executable, *sys.argv], override_path=DEVICE, traffic=traffic)
    require(file_hash(bundle / "layout.json") == file_hash(reference / "bundle/layout.json"), "E1 layout drift")
    require((bundle / "logical-reads.jsonl").stat().st_size <= 256 * 1024**2, "mixed ledger limit")
    print(f"prepared {group.name}: {len(traffic)} queries", flush=True)


def percentile(cohort, numerator, horizon, value):
    known = sorted(value(row) for row in cohort if row["visible_ns"] <= horizon)
    bounds = [horizon - row["offered_ns"] for row in cohort if row["visible_ns"] > horizon]
    rank = math.ceil(len(cohort) * numerator / 100)
    if not rank:
        return None, None
    if len(known) >= rank and (not bounds or known[rank - 1] <= min(bounds)):
        return known[rank - 1] / 1e6, None
    return None, sorted(known + bounds)[rank - 1] / 1e6


def verify_transport(header, total, completed, ordinary_link):
    protocol = header["effective_config"]["protocol"]
    platform = header["effective_platform"]
    ordinary = total["ordinary_nvme_reads"]
    offloads = [row["offloaded_candidates"] for row in completed.values()
                if row.get("offloaded_candidates", 0)]
    require(total["nand_read_bytes"] == total["nand_user_read_bytes"] ==
            16_384 * total["nand_read_transactions"], "NAND page/byte reconciliation")
    for direction, layers in (("H2D", ("doorbell", "sq_entry", "doorbell")),
                              ("D2H", ("sq_fetch", "data", "cq_entry"))):
        payload_base, wire_base = map(sum, zip(*(ordinary_link[layer] for layer in layers)))
        if direction == "H2D":
            payloads = [platform["command_header_bytes"] + 20_480 + c * protocol["document_id_bytes"]
                        for c in offloads]
        else:
            payloads = [platform["result_header_bytes"] + c * (protocol["document_id_bytes"] + protocol["score_bytes"])
                        for c in offloads]
        ledger = total["pcie"].get(direction, {})
        require(ledger.get("messages", 0) == ordinary * 3 + len(payloads) and
                ledger.get("payload_bytes", 0) == ordinary * payload_base + sum(payloads) and
                ledger.get("wire_bytes", 0) == ordinary * wire_base + sum(p + 28 * math.ceil(p / 256)
                                                                          for p in payloads),
                f"{direction} messages, payload or wire ledger")


def measure(trace, candidates, arrivals, info, ordinary_link):
    all_rows = list(rows(trace))
    total = all_rows[-1]
    origin = total["origin_ns"]
    cutoff = origin + info["duration_ns"]
    horizon = cutoff + TIMEOUT_NS
    middle = origin + info["duration_ns"] // 2
    completed = {row["query"]: row for row in all_rows if row["type"] == "query"}
    require(len(completed) == len(arrivals), "missing completion")
    verify_transport(all_rows[0], total, completed, ordinary_link)
    for candidate, arrival in zip(candidates, arrivals, strict=True):
        query = arrival["query"]
        key = ("traffic_class", "request_id")
        require(all(candidate[k] == arrival[k] == completed[query][k] for k in key) and
                completed[query]["offered_ns"] == origin + arrival["offered_ns"], "trace traffic identity")
    require(sum(row["cache_hits"] for row in completed.values()) == total["cache_hits"], "query hit ledger")

    hot_pool = set(info["pools"]["hot"])
    insertions = [row for row in all_rows if row["type"] == "cache_insert"]
    require(sum(row["evicted_document_id"] is not None for row in insertions) == total["cache_evictions"],
            "cache insertion/eviction ledger")
    for row in insertions:
        query, candidate = divmod(row["candidate"], 150)
        require(row["document_id"] == candidates[query]["document_ids"][candidate], "cache insertion owner")
    cold_evicts_hot = sum(row["evicted_document_id"] in hot_pool for row in insertions
                         if row["time_ns"] <= cutoff and candidates[row["candidate"] // 150]["traffic_class"] == "cold")

    measured = [row for row in completed.values() if row["offered_ns"] <= cutoff]
    pending = lambda time: sum(row["offered_ns"] <= time < row["visible_ns"] for row in measured)
    growth = pending(cutoff) - pending(middle)
    limit = max(4, math.ceil(.05 * sum(row["offered_ns"] > middle for row in measured)))
    result = dict(backlog_growth=growth, backlog_limit=limit, overloaded=growth > limit,
                  cutoff_incomplete=pending(cutoff), cold_evictions_of_hot=cold_evicts_hot,
                  **total["measurement"])
    require(result["window_cache_evictions"] ==
            sum(row["time_ns"] <= cutoff and row["evicted_document_id"] is not None for row in insertions),
            "measurement-window evictions disagree")
    require(result["window_nand_bytes"] <= total["nand_read_bytes"] and
            result["window_d2h_wire_bytes"] <= total["pcie"].get("D2H", {}).get("wire_bytes", 0) and
            result["window_h2d_wire_bytes"] <= total["pcie"].get("H2D", {}).get("wire_bytes", 0) and
            result["window_backfill_bytes"] <= total.get("backfill", {}).get("admitted", 0) * 655360 and
            all(0 <= result[f"{side}_scoring_pct"] <= 100 for side in ("host", "controller")),
            "window resource accounting exceeds full-run totals or capacity")
    if any(row["type"] == "score_batch" for row in all_rows):
        read_candidates = {row["candidate"] for row in all_rows if row["type"] == "read_submit"}
        require(all(q["cache_hits"] == 150 - sum(q["query"] * 150 + c in read_candidates for c in range(150))
                    for q in completed.values()), "detailed read/hit reconciliation")
        require(result["window_nand_bytes"] == sum(row["bytes"] for row in all_rows
                    if row["type"] == "nand_read" and row["time_ns"] <= cutoff), "detailed NAND reconciliation")
        for direction in ("d2h", "h2d"):
            require(result[f"window_{direction}_wire_bytes"] == sum(row["wire_bytes"] for row in all_rows
                        if row["type"] == "pcie" and row["event"] == "arrive" and
                        row["direction"] == direction.upper() and row["time_ns"] <= cutoff),
                    "detailed PCIe reconciliation")
        for side in ("host", "controller"):
            busy = sum(max(0, min(row["end_ns"], cutoff) - row["start_ns"]) for row in all_rows
                       if row["type"] == "score_batch" and row["side"] == side)
            require(math.isclose(result[f"{side}_scoring_pct"], 100 * busy / info["duration_ns"]),
                    "detailed scoring reconciliation")
    latency = lambda row: row["visible_ns"] - row["offered_ns"]
    for label in ("hot", "cold"):
        cohort = [row for row in measured if row["traffic_class"] == label]
        if not cohort:
            continue
        p50, p50_lower = percentile(cohort, 50, horizon, latency)
        p99, p99_lower = percentile(cohort, 99, horizon, latency)
        waits = sorted(row["admitted_ns"] - row["offered_ns"] for row in cohort)
        timeouts = sum(latency(row) > TIMEOUT_NS for row in cohort)
        p99_misses = sum(latency(row) > P99_NS for row in cohort)
        result.update({f"{label}_{key}": value for key, value in dict(
            arrived=len(cohort), completed=sum(row["visible_ns"] <= cutoff for row in cohort),
            observed=sum(row["visible_ns"] <= horizon for row in cohort),
            p50_ms=p50, p50_lower_ms=p50_lower, p99_ms=p99, p99_lower_ms=p99_lower,
            timeout_count=timeouts, timeout_pct=100 * timeouts / len(cohort),
            p99_70ms_pass=p99_misses <= len(cohort) - math.ceil(.99 * len(cohort)),
            entry_p99_ms=waits[math.ceil(.99 * len(waits)) - 1] / 1e6,
            hit_pct=100 * sum(row["cache_hits"] for row in cohort) / (150 * len(cohort))).items()})
    return result


def run_case(group, placement, sst, library, source, research, ordinary_link):
    case = group / placement
    case.mkdir()
    bundle = group / "bundle"
    info = json_file(group / "input.json")
    candidates = list(rows(bundle / "candidates.jsonl"))
    arrivals = list(rows(bundle / "arrivals.jsonl"))
    offsets = [row["offered_ns"] for row in arrivals]
    spec = dict(format="ndpsim-maxsim-run/1", placement=placement, bundle=str(bundle),
                receipt_sha256=file_hash(bundle / "receipt.json"), mqsim_source_sha256=source,
                scratch="execution.sqlite", output="trace.jsonl", platform=json_file(PLATFORM),
                **die_admission(placement, True, True), mixed_measurement_ns=info["duration_ns"],
                open_loop=dict(concurrency=CONCURRENCY_LIMIT, host_capacity_bytes=CAPACITY * 655360,
                               cache_oldest_first=info["snapshot"], qps=info["hot_qps"] + info["cold_qps"],
                               offered_offsets_ns=offsets))
    backfill = BACKFILL if placement == PATH_A else None
    if backfill:
        spec["backfill"] = backfill
    spec_path, trace = case / "execution.json", case / "trace.jsonl"
    budget = dict(RUN_BUDGET, timeout_seconds=600, rss_bytes=(3 if research else 2) * GIB,
                  scratch_bytes=2 * GIB)
    if info["duration_ns"] < SHORT_NS:
        spec["detailed_trace"] = True
        budget["output_bytes"] = GIB
    write(spec_path, spec)
    launch([sst, f"--add-lib-path={library}", HERE / "sst.py", "--", json.dumps(str(spec_path))],
           case, budget, cwd="/tmp")
    ledger = summarize(trace, offsets, CAPACITY * 655360, bundle, info["snapshot"], backfill=backfill)
    result = dict(seed=info["seed"], hot_qps=info["hot_qps"], cold_qps=info["cold_qps"], placement=placement,
                  trace_sha256=file_hash(trace), full_drain_ledger=ledger,
                  **measure(trace, candidates, arrivals, info, ordinary_link))
    write(case / "metrics.json", result)
    print(f"verified {group.name}/{placement}: hot P99 {result.get('hot_p99_ms')} ms", flush=True)
    return result


def verify_pairing(output, seeds, rates):
    for seed in seeds:
        index = {}
        snapshot = None
        for hot, cold in rates:
            group = group_path(output, seed, hot, cold)
            info = json_file(group / "input.json")
            if snapshot is None:
                snapshot = info["snapshot"]
            require(info["snapshot"] == snapshot, "snapshot drift across traffic settings")
            bundle = group / "bundle"
            for label, rate in (("hot", hot), ("cold", cold)):
                if not rate:
                    continue
                selected = [(c["request_id"], c["document_ids"], a["offered_ns"])
                            for c, a in zip(rows(bundle / "candidates.jsonl"), rows(bundle / "arrivals.jsonl"), strict=True)
                            if c["traffic_class"] == label]
                require(all(set(row[1]).issubset(info["pools"][label]) for row in selected), "candidate pool drift")
                require(index.setdefault((label, rate), selected) == selected, "single/mixed stream drift")


def report(output, results):
    by_key = {(r["seed"], r["placement"], r["hot_qps"], r["cold_qps"]): r for r in results}
    pairs = []
    for mix in results:
        hot, cold = mix["hot_qps"], mix["cold_qps"]
        if not hot or not cold:
            continue
        key = (mix["seed"], mix["placement"])
        record = {name: mix[name] for name in ("seed", "placement", "hot_qps", "cold_qps")}
        for label, single in (("hot", by_key[(*key, hot, 0)]), ("cold", by_key[(*key, 0, cold)])):
            require(mix[f"{label}_arrived"] == single[f"{label}_arrived"], "paired cohort count")
            for metric in ("p99_ms", "p99_lower_ms", "timeout_pct", "hit_pct", "entry_p99_ms"):
                field = f"{label}_{metric}"
                record[f"{field}_single"] = single[field]
                record[f"{field}_mixed"] = mix[field]
                record[f"{field}_delta"] = (mix[field] - single[field]
                                           if mix[field] is not None and single[field] is not None else None)
        pairs.append(dict(record, cold_evictions_of_hot=mix["cold_evictions_of_hot"], overloaded=mix["overloaded"]))
    for name, table in (("matrix", results), ("paired", pairs)):
        with (output / f"{name}.csv").open("x", newline="") as stream:
            fields = sorted(set().union(*(r.keys() for r in table)) - {"full_drain_ledger"})
            writer = csv.DictWriter(stream, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(table)
    summary = []
    for placement, hot, cold in sorted({(r["placement"], r["hot_qps"], r["cold_qps"]) for r in pairs}):
        points = [r for r in pairs if (r["placement"], r["hot_qps"], r["cold_qps"]) == (placement, hot, cold)]
        row = dict(placement=placement, hot_qps=hot, cold_qps=cold, overloaded_seeds=sum(p["overloaded"] for p in points))
        for field in points[0].keys() - row.keys() - {"seed", "overloaded"}:
            values = [p[field] for p in points if p[field] is not None]
            row[field] = dict(median=median(values), minimum=min(values), maximum=max(values), seeds=len(values)) if values else None
        summary.append(row)
    write(output / "summary.json", summary)
    write(output / "verification.json", dict(passed=True, runs=len(results), pairs=len(pairs),
        traces={f"{r['seed']}:{r['hot_qps']}:{r['cold_qps']}:{r['placement']}": r["trace_sha256"] for r in results}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "e1", "build", "calibration"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--batch", choices=("pilot", "short", "long"), default="short")
    parser.add_argument("--phase", choices=("prepare", "run"), required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    output, e1, build, calibration = [getattr(args, name).resolve() for name in ("output", "e1", "build", "calibration")]
    rates = groups(args.batch)
    seeds = SEEDS[:1] if args.batch == "pilot" else SEEDS
    duration = {"pilot": 200_000_000, "short": SHORT_NS, "long": LONG_NS}[args.batch]
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    group_keys = list(product(seeds, rates))
    if args.phase == "prepare":
        output.mkdir()
        resources = preflight(output, RUN_BUDGET)
        require(resources["disk_free_bytes"] > 24 * GIB and args.workers * GIB <= resources["available_ram_estimate_bytes"],
                "insufficient storage or generation RAM for requested workers")
        write(output / "plan.json", dict(command=sys.argv, seeds=seeds, rates=rates, duration_ns=duration,
              p99_ns=P99_NS, timeout_ns=TIMEOUT_NS,
              backlog_rule="net growth after midpoint > max(4, ceil(5% of second-half arrivals))",
              hardware_status="configured_unvalidated"))
        try:
            with ProcessPoolExecutor(args.workers) as pool:
                tasks = [pool.submit(prepare_group, output, e1, seed, *rate, duration) for seed, rate in group_keys]
                for task in as_completed(tasks):
                    task.result()
            verify_pairing(output, seeds, rates)
        except Exception as error:
            write(output / "preparation.json", dict(passed=False, error=str(error)))
            raise
        write(output / "preparation.json", dict(passed=True, groups=len(group_keys)))
        return

    source = backend_digest()
    plan = json_file(output / "plan.json")
    require(plan["seeds"] == list(seeds) and plan["rates"] == [list(rate) for rate in rates] and
            plan["duration_ns"] == duration, "run request differs from the prepared experiment plan")
    evidence = json_file(calibration / "verification.json")
    inputs = json_file(calibration / "nvme/inputs.json")
    identity = dict(backend_source_sha256=source, sst_sha256=file_hash(sst),
                    element_sha256=file_hash(library / "libndpsim_maxsim.so"))
    prior = json_file(e1 / "sweep.json")
    require(evidence["passed"] and evidence["element_sha256"] == identity["element_sha256"] and
            evidence["calibration_inputs_sha256"] == file_hash(calibration / "nvme/inputs.json") and
            all(inputs[key] == value for key, value in identity.items()) and
            all(prior[key] == identity[key] for key in ("backend_source_sha256", "sst_sha256")) and
            file_hash(DEVICE) == inputs["config_sha256"] and
            file_hash(DEVICE.with_name("micron-9550-pro-device.xml")) == inputs["xml_sha256"],
            "calibration, E1 and current simulator disagree")
    ordinary_link = {}
    host_e0 = calibration / "main/host.jsonl"
    require(file_hash(host_e0) == evidence["e0_traces"]["host.jsonl"], "E0 reference trace identity")
    for event in rows(host_e0):
        if event["type"] == "pcie" and event["event"] == "arrive":
            pair = event["payload_bytes"], event["wire_bytes"]
            require(ordinary_link.setdefault(event["layer"], pair) == pair, "E0 PCIe request framing differs")
    resources = preflight(output, RUN_BUDGET)
    workers = min(args.workers, resources["available_ram_estimate_bytes"] // ((3 if args.batch == "long" else 2) * GIB))
    require(workers >= 1, "insufficient execution RAM")
    write(output / "execution.json", dict(command=sys.argv, workers=workers, **identity,
          e1_sha256=file_hash(e1 / "sweep.json"), calibration_sha256=file_hash(calibration / "verification.json"),
          repository_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
          source_sha256={path.name: file_hash(path) for path in sorted(HERE.iterdir())
                         if path.suffix in (".py", ".cc", ".h", ".json", ".xml")}))
    with ThreadPoolExecutor(workers) as pool:
        pending = {group_path(output, seed, *rate) for seed, rate in group_keys}
        jobs = []
        last_sealed_input = time.monotonic()
        while pending:
            for group in sorted(pending):
                if not (group / "bundle/receipt.json").is_file():
                    continue
                pending.remove(group)
                last_sealed_input = time.monotonic()
                jobs.extend(pool.submit(run_case, group, placement, sst, library, source,
                                        args.batch == "long", ordinary_link) for placement in PLACEMENTS)
            if pending:
                status = output / "preparation.json"
                require(not status.exists() or json_file(status)["passed"], "input preparation failed")
                require(time.monotonic() - last_sealed_input < 3600, "no input package sealed in the last hour")
                time.sleep(2)
        results = [job.result() for job in jobs]
    verify_pairing(output, seeds, rates)
    report(output, results)


if __name__ == "__main__":
    main()
