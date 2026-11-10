"""Export a query and its observed resources as Chrome Trace JSON."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import json
from pathlib import Path

from .reader import InputError


@dataclass
class Observation:
    track: str
    name: str
    start: int
    end: int | None
    args: dict


def _key(record):
    query, op, stage = (record.get(name) for name in ("query", "op", "stage"))
    return (query if type(query) is int else None,
            op if isinstance(op, str) else None,
            stage if isinstance(stage, str) else None)


def _stage_key(key):
    query, op, stage = key
    return json.dumps(dict(query=str(query), op=op, stage=stage),
                      ensure_ascii=False, separators=(",", ":"))


def _time(value, source):
    if type(value) is not int or value < 0:
        raise InputError(f"{source}: missing or invalid SST time")
    return value


def _add(rows, track, name, start, end=None, *, attribution, key=None, **args):
    start = _time(start, name)
    if end is not None and _time(end, name) < start:
        raise InputError(f"{name}: reversed SST endpoints")
    args["attribution"] = attribution
    if key is not None:
        args["stage_key"] = _stage_key(key)
    rows.append(Observation(track, name, start, end, args))


def _service(rows, track, name, submitted, start, end, **args):
    _add(rows, track + " queue", name + " queue", submitted, _time(start, name), **args)
    _add(rows, track + " service", name + " service", start, _time(end, name), **args)


def _pcie(rows, packet, lower, upper, coverage):
    if not any(lower <= event["time"] <= upper for event in packet.values()):
        if len(packet) != 3 or not packet["enqueue"]["time"] <= lower <= upper <= packet["arrive"]["time"]:
            return
    record = next(iter(packet.values()))
    if any((endpoint.get("layer"), endpoint.get("destination")) !=
           (record.get("layer"), record.get("destination")) for endpoint in packet.values()):
        raise InputError(f"pcie: inconsistent route for packet {record['id']}")
    track = f"PCIe {record['layer']} → {record['destination']} (window)"
    args = dict(attribution="window", packet_id=str(record["id"]),
                validation="configured_unvalidated")
    if len(packet) == 3:
        _service(rows, track, "PCIe", *(packet[edge]["time"] for edge in ("enqueue", "start", "arrive")),
                 **args)
        coverage["pcie_complete"] += 1
    else:
        for edge, endpoint in packet.items():
            _add(rows, track, "PCIe " + edge, endpoint["time"], status="incomplete", **args)
        coverage["pcie_incomplete"] += 1


def _resource_rows(run, query, keys, lower, upper):
    rows = []
    coverage = Counter()
    pending = defaultdict(dict)
    buffer_keys = defaultdict(list)
    for key in keys:
        _, op, stage = key
        for suffix in ("input-write", "compute-read", "result-write", "result-read"):
            buffer_keys[f"{query}:{op}:{stage}:{suffix}"].append(key)
    for event in run.events():
        kind = event.get("type")
        if kind == "pcie":
            edge = event.get("event")
            if edge not in ("enqueue", "start", "arrive"):
                coverage["pcie_unsupported_event"] += 1
                continue
            packet_id = event.get("id")
            if packet_id is None:
                raise InputError("pcie: missing packet identity")
            _time(event.get("time"), "pcie")
            packet = pending[packet_id]
            if edge in packet:
                raise InputError(f"pcie: duplicate {edge} for packet {packet_id}")
            packet[edge] = event
            if len(packet) == 3:
                _pcie(rows, pending.pop(packet_id), lower, upper, coverage)
        elif kind in ("cpu", "service", "controller_compute"):
            try:
                detail = json.loads(event.get("key", ""))
            except (ValueError, TypeError):
                detail = {}
            key = _key(detail) if isinstance(detail, dict) else ()
            if key not in keys:
                if len(key) == 3 and key[0] not in (None, query):
                    continue
                if lower <= _time(event.get("end"), kind) and _time(event.get("submitted"), kind) <= upper:
                    coverage[f"{kind}_window"] += 1
                else:
                    continue
            track = "Controller compute" if kind == "controller_compute" else "CPU"
            args = dict(attribution="stage", key=key) if key in keys else dict(attribution="window")
            if kind == "controller_compute":
                args.update(validation="configured_unvalidated", physical_chunk=detail.get("physical_chunk")
                            if isinstance(detail, dict) else None)
            _service(rows, track + (" (window)" if key not in keys else ""), track,
                     event.get("submitted"), event.get("start"), event.get("end"), **args)
            coverage[kind] += 1
        elif kind == "local_read" and _key(event) in keys:
            _add(rows, "SSD local read", "Local read completed", event.get("completed"),
                 attribution="stage", key=_key(event), validation="configured_unvalidated")
            coverage[kind] += 1
        elif kind in ("ordinary_read", "nand_transaction", "credit"):
            time = _time(event.get("completed" if kind == "ordinary_read" else "time"), kind)
            if lower <= time <= upper:
                track = "Controller credit" if kind == "credit" else "SSD " + kind.replace("_", " ")
                _add(rows, track + " (window)", kind, time, attribution="window",
                     validation="configured_unvalidated")
                coverage[kind] += 1
        elif kind == "protocol" and event.get("query") == query:
            _add(rows, "Controller protocol (query)", "Protocol transfer", event.get("submitted"),
                 _time(event.get("arrived"), kind), attribution="query", task=str(event.get("task")),
                 chunk=str(event.get("chunk")), validation="configured_unvalidated")
            coverage[kind] += 1
        elif kind == "buffer_access":
            name = event.get("key")
            if not isinstance(name, str):
                continue
            matches = buffer_keys.get(name, ())
            key = matches[0] if len(matches) == 1 else None
            query_write = name.startswith(f"query-write:{query}:")
            if len(matches) > 1:
                coverage["buffer_access_ambiguous_key"] += 1
            if key is not None or query_write:
                _add(rows, "Controller buffer" + (" (query)" if key is None else ""), "Buffer submitted",
                     event.get("submitted"), attribution="stage" if key else "query", key=key,
                     validation="configured_unvalidated", demand_ns=str(event.get("duration_ns")))
                coverage[kind] += 1
    for packet in pending.values():
        _pcie(rows, packet, lower, upper, coverage)
    return rows, coverage


def _run_rows(run, query):
    request = run.request(query)
    if request is None or request.get("admitted") is None:
        raise InputError(f"{run.report_path}: query {query} lacks an admission timestamp")
    lower = _time(request["admitted"], "request admission")
    succeeded = request.get("success") is True
    upper = _time(request["visible"], "request visible") if succeeded else lower
    if not succeeded and request.get("drained") is not None:
        upper = max(lower, _time(request["drained"], "request drained"))

    timings = run.stage_timings(query)
    rows, counts = _resource_rows(run, query, timings, lower, upper)
    _add(rows, "Query", f"Query {query}", lower, upper if succeeded else None,
         attribution="query", status="completed" if succeeded else "failed_or_incomplete")
    if request.get("drained") is not None and request["drained"] >= lower:
        _add(rows, "Query", "Query drained", request["drained"], attribution="query")
    statuses = Counter()
    for key, timing in timings.items():
        status = timing["status"]
        statuses[status] += 1
        if timing["submitted_ns"] is not None:
            _, op, stage = key
            _add(rows, "Stage", f"{op} / {stage}", timing["submitted_ns"], timing["completed_ns"],
                 attribution="stage", key=key, status=status)
    return rows, dict(
        placement=run.placement, profile_sha256=run.profile_sha256, context=run.context,
        config_sha256=run.config_sha256, admission_ns=str(lower), visible_ns=str(upper) if succeeded else None,
        drained_ns=str(request["drained"]) if request.get("drained") is not None else None,
        window_ns=[str(lower), str(upper)], stage_statuses=dict(statuses), resource_counts=dict(counts),
    )


def _trace_events(pid, label, rows, admission, offset):
    yield dict(ph="M", name="process_name", pid=pid, tid=0, args=dict(name=label))
    groups = defaultdict(list)
    for row in rows:
        groups[row.track].append(row)
    tid = 0
    for track, observations in sorted(groups.items()):
        lanes = []
        lane_count = 0
        for row in sorted(observations, key=lambda item: item.start):
            # Keep simultaneous instants and zero-length slices on separate lanes.
            if lanes and lanes[0][0] < row.start:
                _, lane = heapq.heappop(lanes)
            else:
                tid += 1
                lane = tid
                lane_count += 1
                name = track if lane_count == 1 else f"{track} {lane_count}"
                yield dict(ph="M", name="thread_name", pid=pid, tid=lane, args=dict(name=name))
            end = row.start if row.end is None else row.end
            heapq.heappush(lanes, (end, lane))
            relative_start = row.start - admission
            args = dict(row.args, sst_start_ns=str(row.start), relative_start_ns=str(relative_start),
                        display_offset_ns=str(offset))
            event = dict(name=row.name, cat=track, pid=pid, tid=lane, ts=(relative_start + offset) / 1000,
                         args=args)
            if row.end is None:
                event.update(ph="I", s="t")
            else:
                args.update(sst_end_ns=str(row.end), relative_end_ns=str(row.end - admission))
                event.update(ph="X", dur=(row.end - row.start) / 1000)
            yield event


def write_timeline(path, package, query_id, runs=()):
    """Write Chrome Trace JSON and return coverage for the caller's manifest."""
    package.query(query_id)
    query_id = int(query_id)
    coverage = dict(query=str(query_id), package_sha256=package.identity, display_offset_ns="0",
                    window_policy="Retain full observed intervals intersecting the query window; "
                                  "retain query and stage attributed events outside that window.",
                    unsupported=["dependency_ready_wait", "ssd_queue_and_service",
                                 "controller_buffer_queue_and_service"], runs={})
    collected = []
    offset = 0
    for label, run in runs:
        if label in coverage["runs"]:
            raise InputError(f"duplicate timeline run label {label}")
        if run.report["package_sha256"] != package.identity:
            raise InputError(f"{label}: run package does not match timeline package")
        rows, run_coverage = _run_rows(run, query_id)
        admission = int(run_coverage["admission_ns"])
        offset = max(offset, admission - min(row.start for row in rows))
        coverage["runs"][label] = run_coverage
        collected.append((label, rows, admission))
    coverage["display_offset_ns"] = str(offset)
    with Path(path).open("w", encoding="utf-8") as stream:
        stream.write('{"traceEvents":[')
        separator = ""
        for pid, (label, rows, admission) in enumerate(collected, 1):
            for event in _trace_events(pid, label, rows, admission, offset):
                stream.write(separator)
                json.dump(event, stream, ensure_ascii=True, separators=(",", ":"))
                separator = ",\n"
        stream.write('],"displayTimeUnit":"ns","ndpsim":')
        json.dump(coverage, stream, ensure_ascii=True, separators=(",", ":"))
        stream.write("}\n")
    return coverage
