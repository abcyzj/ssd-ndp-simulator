"""Run paired MaxSim NAND-to-controller page-read probes with SST time."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import sys
import subprocess
from xml.etree import ElementTree as ET

from ndpsim.execution import budget, enforce, launch, preflight
from ndpsim.static_ir import file_hash, require

from config import load_config
from generate import write
from run import HERE, ROOT, backend_digest, json_file

PAGE_BYTES, PAGE_SECTORS = 16384, 32
CHANNELS, CHIPS, DIES, PLANES = 16, 4, 2, 4
STRIPE = CHANNELS * CHIPS * DIES * PLANES
PRELOAD_PAGES = 4096
FULL = [(channel, chip, die) for die in range(DIES) for chip in range(CHIPS)
        for channel in range(CHANNELS)]
CASES = {
    "same-die-qd1": (1, [(0, 0, 0)]),
    "same-die-qd2": (2, [(0, 0, 0)]),
    "same-chip-two-dies": (2, [(0, 0, 0), (0, 0, 1)]),
    "two-chips": (2, [(0, 0, 0), (0, 1, 0)]),
    **{f"all-qd{qd}": (qd, FULL) for qd in (16, 64, 128, 256)},
}


def page_manifest(targets, count):
    """CWDP striping. Rotate through the preloaded slots, always on plane zero."""
    for index in range(count):
        c, h, d = targets[index % len(targets)]
        stripe = index // len(targets) % (PRELOAD_PAGES // STRIPE)
        lpa = c + CHANNELS * (h + CHIPS * d) + STRIPE * stripe
        yield (lpa * PAGE_SECTORS, c, h, d, 0)


def union(spans):
    merged = []
    for begin, end in sorted(spans):
        if end <= begin:
            continue
        if merged and begin <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], end)
        else:
            merged.append((begin, end))
    return merged


def intersections(spans, begin, end):
    """spans is a sorted union of disjoint half-open intervals."""
    if begin >= end:
        return
    index = bisect_right(spans, (begin, math.inf))
    for position in range(max(0, index - 1), len(spans)):
        start, stop = spans[position]
        if start >= end:
            break
        if max(start, begin) < min(stop, end):
            yield max(start, begin), min(stop, end)


def length(spans):
    return sum(end - begin for begin, end in spans)


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def verify_staging(stages, reads, rate, begin, end, stop):
    duration = (PAGE_BYTES + rate - 1) // rate
    require(len(stages) == len(reads), "staging did not drain every NAND page")
    prior_finish = busy = 0
    latencies, waits = [], []
    for (index, arrival, start, finish), (read_index, _, _, _, complete) in zip(stages, reads):
        require(index == read_index and arrival == complete and start == max(arrival, prior_finish) and
                finish == start + duration, "staging identity, FIFO order or service time")
        prior_finish = finish
        busy += max(0, min(end, finish) - max(begin, start))
        if begin < finish <= end:
            latencies.append(finish - arrival)
            waits.append(start - arrival)
    require(prior_finish == stop and latencies, "staging drain or measurement coverage")
    return dict(staging_GBps=len(latencies) * PAGE_BYTES / (end - begin),
                staging_measured_pages=len(latencies), staging_drained_bytes=len(stages) * PAGE_BYTES,
                staging_service_ns_per_page=duration, staging_utilization=busy / (end - begin),
                staging_wait_p95_ns=percentile(waits, .95), staging_wait_max_ns=max(waits),
                staging_arrival_to_done_p95_ns=percentile(latencies, .95))


def verify(stdout, requests, qd, warmup, identity, staging_rate=0, seed_gap=0):
    records = defaultdict(list)
    for line in stdout.read_text().splitlines():
        label, _, row = line.partition(" ")
        if label.startswith("LOCAL_"):
            records[label.removeprefix("LOCAL_")].append(row)
    device, = map(json.loads, records["DEVICE"])
    build, = records["BUILD"]
    require(device["source_sha256"] == identity["xml_sha256"] and
            build.split() == [identity["backend_commit"], identity["backend_source_sha256"]],
            "local probe device/build identity mismatch")
    drain, = records["DRAIN"]
    origin, begin, end, stop, count, pcie = map(int, drain.split())
    require(origin < begin < end < stop and count == len(requests) and pcie == 0,
            "local probe window, count or PCIe traffic")
    duration = end - begin

    def rows(kind):
        return [tuple(map(int, row.split())) for row in records[kind]]

    physical = {lba: tuple(address) for lba, *address in rows("MAP")}
    expected = {lba: tuple(target) for lba, *target in requests}
    require(len(physical) == len(records["MAP"]) and physical.keys() == expected.keys() and
            all(address[:4] == expected[lba] for lba, address in physical.items()),
            "physical preload coverage or target disagreement")
    read_order = rows("READ")
    reads = sorted(read_order)
    queued = {tid: rest for tid, *rest in rows("QUEUE")}
    dispatched = {tid: rest for tid, *rest in rows("DISPATCH")}
    require(count == len(reads) == len(queued) == len(dispatched) == len(records["QUEUE"]) ==
            len(records["DISPATCH"]) and queued.keys() == dispatched.keys() ==
            {row[1] for row in reads} and 0 not in queued, "page/TSU identity or background NAND reads")

    groups = defaultdict(list)
    channel_bus = defaultdict(list)
    for row in records["INTERVAL"]:
        kind, start, finish, channel, chip, die = row.split()
        a, b, c, h, d = map(int, (start, finish, channel, chip, die))
        require(kind in {"array", "command_bus", "data_out_bus"} and origin <= a < b <= stop,
                "unexpected NAND interval kind/time")
        groups[kind, (c, h, d)].append((a, b))
        if kind.endswith("_bus"):
            channel_bus[c].append((a, b))
    for spans in channel_bus.values():
        spans.sort()
        require(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), "overlapping channel commands/data")
    intervals = {(kind, target): dict(spans) for (kind, target), spans in groups.items()}
    require(all(sum(len(spans) for (k, _), spans in intervals.items() if k == kind) == count and
                sum(len(spans) for (k, _), spans in groups.items() if k == kind) == count
                for kind in ("array", "command_bus", "data_out_bus")), "NAND page interval count")

    depth_changes = Counter()
    per_die = defaultdict(list)
    transfer_spans = defaultdict(list)
    completed_at = []
    busy = defaultdict(list)
    data_wait, latencies, phases = [], [], []
    measured_per_channel = Counter()
    for ordinal, (index, tid, lba, submit, complete) in enumerate(reads):
        require(index == ordinal and lba == requests[index][0] and origin <= submit < complete <= stop,
                "local request order or timestamp")
        queue_time, *metadata = queued[tid]
        dispatch_time, *dispatch_metadata = dispatched[tid]
        target = physical[lba][:3]
        require(metadata == dispatch_metadata == [lba // PAGE_SECTORS, PAGE_BYTES, *physical[lba]] and
                submit <= queue_time <= dispatch_time < complete, "local read actual NAND address/bytes")
        cmd_end = intervals["command_bus", target].pop(dispatch_time)
        array_end = intervals["array", target].pop(cmd_end)
        # Data-out for a die is ordered with its array commands. Match by completion timestamp.
        transfer_spans[target].append((complete, array_end, cmd_end - dispatch_time))
        busy[target].append((dispatch_time, complete))
        per_die[target].append((queue_time, dispatch_time))
        completed_at.append(complete)
        depth_changes[submit] += 1
        depth_changes[complete] -= 1
        if begin < complete <= end:
            latencies.append(complete - submit)
            measured_per_channel[target[0]] += 1

    bus = {c: union(spans) for c, spans in channel_bus.items()}
    blocked = []
    measured_blocked, measured_blocked_idle = 0, 0
    data_ns_by_channel = Counter()
    transfers_by_channel = defaultdict(list)
    pair_overlap = Counter()
    for target, transfers in transfer_spans.items():
        c, h, d = target
        busy[target].sort()
        require(all(a[1] <= b[0] for a, b in zip(busy[target], busy[target][1:])),
                "die reissued before data-out")
        data = sorted((finish, start) for start, finish in intervals["data_out_bus", target].items())
        require(len(data) == len(transfers), "per-die NAND transfer count")
        for (finish, start), (complete, array_end, command_ns) in zip(data, sorted(transfers)):
            require(array_end <= start < finish == complete, "NAND data-out vs controller completion")
            data_ns_by_channel[c] += max(0, min(end, finish) - max(begin, start))
            transfers_by_channel[c].append((start, finish))
            if begin < complete <= end:
                data_wait.append(start - array_end)
                phases.append((command_ns, finish - start))

    require(not any(spans for (kind, _), spans in intervals.items() if kind != "data_out_bus"),
            "unmatched command or array intervals")
    nand_stop = max(completed_at)
    require(sorted(completed_at)[warmup - 1] == begin and nand_stop <= stop and
            max(row[3] for row in reads) == end and all(
                left[3] <= right[3] for left, right in zip(reads, reads[1:])), "window/refill boundaries")
    stages = rows("STAGE")
    if staging_rate:
        stage_metrics = verify_staging(stages, read_order, staging_rate, begin, end, stop)
    else:
        require(not stages and nand_stop == stop, "unexpected staging")
        stage_metrics = {}
    require(all(row[3] == origin + (index // CHANNELS) * seed_gap for index, row in enumerate(reads[:qd])),
            "initial page seeding timestamps")
    filled = reads[qd - 1][3]
    require(filled < min(completed_at), "initial QD must fill before the first completion")
    depth = peak = 0
    for timestamp, delta in sorted(depth_changes.items()):
        depth += delta
        require(0 <= depth <= qd and (not filled <= timestamp < end or depth == qd),
                "closed-loop window must stay at configured QD through final submission")
        peak = max(peak, depth)
    require(depth == 0 and peak == qd and latencies, "closed-loop drain/coverage")

    occupied = {key: union(spans) for key, spans in busy.items()}
    for (c, h, d), requests_on_die in per_die.items():
        own = occupied[c, h, d]
        other = occupied.get((c, h, 1 - d), [])
        for queued_at, issued_at in requests_on_die:
            a, b = max(queued_at, begin), min(issued_at, end)
            for lo, hi in intersections(other, a, b):
                # Strip out times when the destination die is itself unavailable.
                start = lo
                for own_start, own_end in intersections(own, lo, hi):
                    if start < own_start:
                        blocked.append((start, own_start, c))
                    start = own_end
                if start < hi:
                    blocked.append((start, hi, c))
    for a, b, c in blocked:
        measured_blocked += b - a
        measured_blocked_idle += b - a - length(intersections(bus[c], a, b))

    def overlap(a, b):
        return length(span for lo, hi in a for span in intersections(b, lo, hi))

    targets = sorted(per_die)
    arrays = {target: union(intersections(union(groups["array", target]), begin, end)) for target in targets}
    for left, right in combinations(targets, 2):
        if left[0] == right[0]:
            kind = "same_chip" if left[1] == right[1] else "different_chip"
            pair_overlap[kind] += overlap(arrays[left], arrays[right])

    active_channels = {target[0] for target in targets}
    require(measured_per_channel.keys() == active_channels, "channel coverage in measured window")
    burst_pages, burst_gaps = {}, {}
    for c in sorted(active_channels):
        transfers = sorted(transfers_by_channel[c])
        bursts = [(a, b) for a, b in union(transfers) if begin <= a < b <= end]
        finishes = [b for _, b in transfers]
        sizes = [bisect_right(finishes, b) - bisect_right(finishes, a) for a, b in bursts]
        gaps = [next_start - finish for (_, finish), (next_start, _) in zip(bursts, bursts[1:])]
        burst_pages[str(c)] = percentile(sizes, .5) if sizes else None
        burst_gaps[str(c)] = percentile(gaps, .5) if gaps else None
    ledger = {kind: records[kind] for kind in ("MAP", "READ", "QUEUE", "DISPATCH", "INTERVAL")}
    nand_hash = hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()
    return dict(
        origin_ns=origin, start_ns=begin, end_ns=end, drain_ns=stop, nand_drain_ns=nand_stop, measured_ns=duration,
        nand_ledger_sha256=nand_hash, **stage_metrics,
        warmup_completions=warmup, requests=count, drained_nand_pages=count, drained_bytes=count * PAGE_BYTES,
        measured_pages=len(latencies), measured_bytes=len(latencies) * PAGE_BYTES, peak_qd=peak,
        host_pcie_events=pcie, host_pcie_wire_bytes=0, channel_bus_overlap_ns=0,
        GBps=len(latencies) * PAGE_BYTES / duration, read_latency_p95_ns=percentile(latencies, .95),
        data_out_ns_per_page=percentile([data for _, data in phases], .5),
        command_ns_per_page=percentile([phase for phase, _ in phases], .5),
        array_ns_per_page=percentile([b - a for target in targets for a, b in groups["array", target]], .5),
        wait_data_out_p50_ns=percentile(data_wait, .5), wait_data_out_p95_ns=percentile(data_wait, .95),
        wait_data_out_mean_ns=sum(data_wait) / len(data_wait),
        same_chip_array_overlap_pair_ns=pair_overlap["same_chip"],
        different_chip_array_overlap_pair_ns=pair_overlap["different_chip"],
        free_target_busy_other_die_page_ns=measured_blocked,
        free_target_busy_other_die_idle_bus_page_ns=measured_blocked_idle,
        # In admission B this is opportunity/remaining wait, not a chip gate.
        data_out_fraction_by_channel={str(c): data_ns_by_channel[c] / duration for c in range(CHANNELS)},
        bus_fraction_by_channel={str(c): length(intersections(bus.get(c, []), begin, end)) / duration
                                 for c in range(CHANNELS)},
        measured_pages_by_channel=dict(sorted(measured_per_channel.items())),
        data_out_burst_p50_pages_by_channel=burst_pages,
        between_data_out_bursts_p50_ns_by_channel=burst_gaps,
        mean_active_channel_data_out_fraction=sum(data_ns_by_channel.values()) / (duration * len(active_channels)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=Path, required=True)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--staging", action="store_true", help="use the candidate controller staging FIFO")
    parser.add_argument("--seed-gap-ns", type=int, action="append", default=[],
                        help="extra QD128 die mode with phased initial fill")
    args = parser.parse_args()
    require(all(1 <= gap <= 8000 for gap in args.seed_gap_ns) and
            (not args.seed_gap_ns or not args.case or "all-qd128" in args.case),
            "seed gaps require all-qd128 and must be in [1, 8000] ns")
    config = load_config(args.config)
    xml = Path(config["hardware"]["mqsim_config_path"])
    staging_profile = HERE / "e0-run.json"
    staging_rate = json_file(staging_profile)["controller_staging_bytes_per_ns"] if args.staging else 0
    require(not args.staging or (type(staging_rate) is int and 0 < staging_rate < 2**64),
            "MaxSim staging rate must be a positive integer in B/ns")
    tree = ET.parse(xml)
    required = dict(Plane_Allocation_Scheme="CWDP", Transaction_Scheduling_Policy="PRIORITY_OUT_OF_ORDER",
                    Flash_Channel_Count=str(CHANNELS), Chip_No_Per_Channel=str(CHIPS),
                    Die_No_Per_Chip=str(DIES), Plane_No_Per_Die=str(PLANES), Page_Capacity=str(PAGE_BYTES))
    require(all(tree.findtext(f".//{key}") == value for key, value in required.items()),
            "expected 9550 PRO candidate CWDP geometry and scheduler")
    output, build = args.output.resolve(), args.build.resolve()
    execution_budget = budget(json_file(args.budget))
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    element = library / "libndpsim_maxsim.so"
    output.mkdir()
    preflight(output, execution_budget)
    lock = ROOT / "deps.lock.json"
    native_commit = json_file(lock)["dependencies"]["MQSim"]["commit"]
    source = ROOT / "third_party/MQSim"
    native_diff = subprocess.check_output(["git", "-C", str(source), "diff", "--binary", native_commit, "--"])
    identity = dict(
        command=sys.argv, configured_unvalidated=True, config_sha256=file_hash(args.config),
        xml_sha256=file_hash(xml), sst_sha256=file_hash(sst), element_sha256=file_hash(element),
        backend_commit=config["hardware"]["backend_commit"], backend_source_sha256=backend_digest(),
        backend_native_commit=native_commit, backend_native_diff_sha256=hashlib.sha256(native_diff).hexdigest(),
        dependency_lock_sha256=file_hash(lock),
        main_repo_commit=subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        sources_sha256={name: file_hash(HERE / name) for name in (
            "CMakeLists.txt", "local_read_probe.cc", "local_read_probe.py", "local_read_sst.py")},
        budget_sha256=file_hash(args.budget), preload_pages=PRELOAD_PAGES,
        staging_profile_sha256=file_hash(staging_profile) if args.staging else None,
    )
    write(output / "inputs.json", identity)
    (output / "device.xml").write_bytes(xml.read_bytes())
    (output / "candidate.json").write_bytes(args.config.read_bytes())
    (output / "budget.json").write_bytes(args.budget.read_bytes())
    if args.staging:
        (output / "staging-profile.json").write_bytes(staging_profile.read_bytes())
    matrix = []
    for name in dict.fromkeys(args.case or CASES):
        qd, targets = CASES[name]
        warmup, count = (1024, 8192) if targets is FULL else (128, 1024)
        requests = list(page_manifest(targets, count))
        case = output / name
        case.mkdir()
        manifest = case / "pages.txt"
        manifest.write_text("ndpsim-maxsim-local-pages/1\n" +
                            "".join(" ".join(map(str, row)) + "\n" for row in requests))
        variants = [("chip", 0), ("die", 0)]
        if name == "all-qd128":
            variants.extend(("die", gap) for gap in dict.fromkeys(args.seed_gap_ns))
        case_results = []
        for mode, gap in variants:
            variant = f"{mode}-seed{gap}ns" if gap else mode
            folder = case / variant
            folder.mkdir()
            spec = folder / "run.json"
            write(spec, dict(device=str(output / "device.xml"), requests=str(manifest),
                             preload_sectors=PRELOAD_PAGES * PAGE_SECTORS, qd=qd, warmup=warmup,
                             die_admission=mode == "die", staging_bytes_per_ns=staging_rate,
                             seed_gap_ns=gap))
            command = [str(sst), f"--add-lib-path={library}", str(HERE / "local_read_sst.py"), "--",
                       json.dumps(str(spec))]
            run = launch(command, folder, execution_budget, cwd="/tmp")
            stdout = folder / "stdout.log"
            metrics = verify(stdout, requests, qd, warmup, identity, staging_rate, gap)
            result = dict(case=name, qd=qd, admission=mode, seed_gap_ns=gap,
                          staging_bytes_per_ns=staging_rate, configured_unvalidated=True, **metrics,
                          manifest_sha256=file_hash(manifest), run_sha256=file_hash(spec),
                          stdout_sha256=file_hash(stdout), peak_child_rss_bytes=run["peak_child_rss_bytes"])
            write(folder / "metrics.json", result)
            matrix.append(result)
            case_results.append(result)
            enforce(output, execution_budget)
            print(name, variant, round(metrics["GBps"], 3), metrics["read_latency_p95_ns"],
                  metrics.get("staging_GBps"), flush=True)
        chip, die = case_results[:2]
        chip_overlap = chip["same_chip_array_overlap_pair_ns"]
        die_overlap = die["same_chip_array_overlap_pair_ns"]
        require(chip_overlap == 0, "chip A unexpectedly allowed same-chip array overlap")
        if name in {"same-die-qd1", "same-die-qd2", "two-chips"}:
            require(die_overlap == 0 and chip["stdout_sha256"] == die["stdout_sha256"],
                    "negative control changed")
        if name == "same-chip-two-dies":
            require(die_overlap > 0 and die["GBps"] > chip["GBps"] and
                    chip["free_target_busy_other_die_idle_bus_page_ns"] > 0,
                    "same-chip two-die admission did not remove the expected gate")
        if name == "two-chips":
            require(all(r["different_chip_array_overlap_pair_ns"] > 0 for r in (chip, die)),
                    "positive control lacks different-chip array overlap")
    write(output / "matrix.json", matrix)


if __name__ == "__main__":
    main()
