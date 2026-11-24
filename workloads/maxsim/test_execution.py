"""Independent E0 event and accounting checks against real SST/MQSim runs."""

from collections import Counter, defaultdict
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
from ndpsim.static_ir import file_hash
from run import PLACEMENTS, json_file, run as execute_run
from generate import write
from verify import rows

BUILD = Path(sys.argv.pop(1)).resolve()
BACKEND_SOURCE = os.environ.get("NDPSIM_MAXSIM_BACKEND_SOURCE_SHA256")
SMALL_BUDGET = dict(output_bytes=64 * 1024**2, scratch_bytes=64 * 1024**2, rss_bytes=512 * 1024**2,
                    timeout_seconds=180, min_disk_free_bytes=32 * 1024**2)


def run(*args, **kwargs):
    return execute_run(*args, **kwargs, expected_backend_source=BACKEND_SOURCE)


def ceil_div(value, unit):
    return (value + unit - 1) // unit


def interval_union(spans):
    edges = Counter()
    for start, end in spans:
        edges[start] += 1
        edges[end] -= 1
    busy = total = previous = 0
    for time, delta in sorted(edges.items()):
        if busy:
            total += time - previous
        busy += delta
        previous = time
    return total


class Execution(unittest.TestCase):
    def setUp(self):
        evidence = os.environ.get("NDPSIM_MAXSIM_EVIDENCE")
        if evidence:
            self.directory = Path(evidence).resolve() / self._testMethodName
            self.directory.mkdir()
        else:
            temp = tempfile.TemporaryDirectory(prefix="MaxSim E0 ")
            self.addCleanup(temp.cleanup)
            self.directory = Path(temp.name)

    def command(self, name, args, *, success=True):
        process = subprocess.run(args, cwd=ROOT,
                                 env={**os.environ, "PYTHONPATH": str(ROOT / "python")},
                                 capture_output=True, text=True, timeout=180)
        write(self.directory / f"{name}.process.json",
              dict(command=args, cwd=str(ROOT), exit_code=process.returncode,
                   stdout=process.stdout, stderr=process.stderr))
        self.assertEqual(process.returncode == 0, success, process.stderr or process.stdout)
        return process

    def bundle(self, parameters=None, *, base="e0.json"):
        config = HERE / base
        if parameters is not None:
            override = json_file(config)
            override["hardware"]["mqsim_config_path"] = str(HERE / override["hardware"]["mqsim_config_path"])
            for name, fields in parameters.items():
                override[name].update(fields)
            config = self.directory / "override.json"
            write(config, override)
        bundle = self.directory / "bundle"
        self.command("generate", [sys.executable, str(HERE / "generate.py"), "generate", "--config",
                                  str(config), "--point", "E0-single-01", "--output", str(bundle)])
        return bundle

    def verify_execution(self, bundle, trace):
        log = list(rows(trace))
        by_type = defaultdict(list)
        for event in log:
            by_type[event["type"]].append(event)
        cfg = json_file(bundle / "effective-config.json")["config"]
        workload, protocol, schedule = (cfg[name] for name in ("workload", "protocol", "execution"))
        header = log[0]
        platform = json_file(HERE / "e0-run.json")
        s = log[-1]
        where = s["placement"]
        controller = where == "device_all"
        self.assertEqual(header["package_sha256"], json_file(bundle / "receipt.json")["package_sha256"])
        self.assertEqual(s["type"], "summary")
        self.assertTrue(s["success"] and s["all_resources_drained"] and s["configured_unvalidated"])
        self.assertEqual(header["effective_config"], cfg)
        self.assertEqual(header["effective_platform"], platform)
        self.assertEqual(header["layout_sha256"], file_hash(bundle / "layout.json"))
        self.assertEqual(header["device_sha256"], file_hash(Path(cfg["hardware"]["mqsim_config_path"])))
        xml = ElementTree.parse(cfg["hardware"]["mqsim_config_path"])
        lanes = int(xml.findtext("./Host_Parameter_Set/PCIe_Lane_Count"))
        lane_rate = Decimal(xml.findtext("./Host_Parameter_Set/PCIe_Lane_Bandwidth"))
        mps = int(xml.findtext("./Host_Parameter_Set/PCIe_Max_Payload_Bytes", "128"))
        self.assertEqual(header["device"]["pcie_max_payload_bytes"], mps)
        self.assertEqual(Decimal(str(header["mqsim_pcie_configured_GBps"])), lanes * lane_rate)
        origin = s["origin_ns"]
        (measurement,) = by_type["measurement_start"]
        self.assertEqual(measurement["time_ns"], origin)
        self.assertEqual(measurement["preload_pages"], ceil_div(measurement["preload_sectors"], 32))

        ids = json_file(bundle / "candidates.jsonl")["document_ids"]
        vectors = workload["document_vectors"] + workload["template_token_increment"]
        dimension, queries = workload["embedding_dim"], workload["query_vectors"]
        size = vectors * dimension * workload["bytes_per_component"]
        stride = ceil_div(size, 4096) * 4096
        read_size = cfg["layout"]["logical_read_bytes"]
        flop = 2 * queries * dimension * vectors
        self.assertEqual(measurement["preload_pages"], ceil_div(stride * workload["corpus_documents"], 16384))
        planned = {}
        for candidate, doc in enumerate(ids):
            for part, offset in enumerate(range(0, size, read_size)):
                valid = min(size - offset, read_size)
                planned[candidate, part] = (doc, (doc * stride + offset) // 512,
                                            8 * ceil_div(valid, 4096), valid)
        all_bytes = sum(x[2] * 512 for x in planned.values())
        self.assertEqual(s["logical_reads"], len(planned))
        self.assertEqual(s["logical_request_bytes"], all_bytes)
        self.assertEqual(s["effective_document_bytes"], len(ids) * size)
        self.assertEqual(s["flop"], len(ids) * flop)
        self.assertEqual(s["scores"], len(ids))
        self.assertEqual(s["topk_bytes"], workload["top_k"] * 12)
        self.assertEqual(s["topk_count"], 1)
        self.assertEqual(s["document_d2h_payload_bytes"], 0 if controller else all_bytes)
        self.assertEqual(s["controller_staging_bytes"], len(ids) * size if controller else 0)
        self.assertEqual(s["ordinary_nvme_reads"], 0 if controller else len(planned))
        self.assertEqual(s["mqsim_local_reads"], len(planned) if controller else 0)

        pending, open_docs, buffers = {}, set(), set()
        peaks = [0, 0, 0]
        submit_order = []
        completions = {}
        document_done = Counter()
        read_times = {}
        credits = {}
        blocked = {}
        recovered = Counter()
        for e in log:
            event = e["type"]
            if event not in ("input_buffer", "read_window", "read_submit", "read_complete", "backpressure"):
                continue
            candidate = e["candidate"]
            self.assertGreaterEqual(e["time_ns"], origin)
            if event in ("input_buffer", "read_window"):
                bucket = buffers if event == "input_buffer" else open_docs
                if e["reserve"]:
                    self.assertNotIn(candidate, bucket)
                    bucket.add(candidate)
                else:
                    bucket.remove(candidate)
                    self.assertEqual(document_done[candidate], ceil_div(size, read_size))
                    if event == "read_window":
                        self.assertEqual(e["time_ns"], read_times[candidate])
                if event == "input_buffer":
                    self.assertEqual(e["bytes"], size)
                    if e["reserve"]:
                        self.assertNotIn(e["credit"], credits)
                        credits[e["credit"]] = candidate
                    else:
                        self.assertEqual(credits.pop(e["credit"]), candidate)
                self.assertEqual(e["used"], len(bucket) * (size if event == "input_buffer" else 1))
            elif event == "backpressure":
                self.assertEqual(e["outstanding_reads"], len(pending))
                self.assertEqual(e["window_documents"], len(open_docs))
                self.assertEqual(e["input_buffer_bytes"], size * len(buffers))
                if e["reason"] == "qd":
                    self.assertEqual(len(pending), schedule["global_outstanding_reads"])
                elif e["reason"] == "window":
                    self.assertEqual(len(open_docs), schedule["read_window_documents_per_query"])
                    self.assertNotIn(candidate, open_docs)
                else:
                    self.assertEqual(e["reason"], "buffer")
                    self.assertGreater(size * (len(buffers) + 1), schedule["input_buffer_bytes_per_side"])
                    self.assertNotIn(candidate, buffers)
                blocked[e["reason"]] = (candidate, e["time_ns"])
            else:
                key = candidate, e["slice"]
                if event == "read_submit":
                    self.assertIn(candidate, buffers & open_docs)
                    self.assertNotIn(key, pending)
                    self.assertEqual(planned[key], (e["document_id"], e["lba"], e["sectors"], e["valid_bytes"]))
                    self.assertEqual(e["path"], "local" if controller else "ordinary")
                    for reason, (waiting, since) in blocked.items():
                        self.assertEqual(candidate, waiting)
                        self.assertGreater(e["time_ns"], since)
                        recovered[reason] += 1
                    blocked.clear()
                    submit_order.append(key)
                    pending[key] = e["time_ns"]
                else:
                    self.assertGreaterEqual(e["time_ns"], pending.pop(key))
                    self.assertNotIn(key, completions)
                    completions[key] = e["time_ns"]
                    document_done[candidate] += 1
                    read_times[candidate] = e["time_ns"]
                self.assertEqual(e["outstanding_reads"], len(pending))
            highs = [len(pending), len(open_docs), size * len(buffers)]
            peaks = [max(a, b) for a, b in zip(peaks, highs)]
            for observed, cap in zip(highs, ("global_outstanding_reads", "read_window_documents_per_query",
                                             "input_buffer_bytes_per_side")):
                self.assertLessEqual(observed, schedule[cap])
        self.assertEqual(submit_order, list(planned))
        self.assertFalse(pending or open_docs or buffers or credits or blocked)
        self.assertEqual(recovered, Counter(r["reason"] for r in by_type["backpressure"]))
        self.assertEqual(set(completions), set(planned))
        self.assertEqual(peaks, [s["peak_outstanding_reads"], s["peak_read_window_documents"],
                                 s["peak_input_buffer_bytes"]])

        staged = {}
        stage_finish = origin
        for row in by_type["controller_staging"]:
            key = tuple(map(int, row["key"].split("/")))
            self.assertNotIn(key, staged)
            self.assertEqual(row["submitted_ns"], completions[key])
            self.assertEqual(row["start_ns"], max(stage_finish, completions[key]))
            self.assertEqual(row["duration_ns"], ceil_div(planned[key][3],
                                                         platform["controller_staging_bytes_per_ns"]))
            self.assertEqual(row["end_ns"], row["start_ns"] + row["duration_ns"])
            staged[key] = stage_finish = row["end_ns"]
        self.assertEqual(set(staged), set(planned) if controller else set())
        stage = {r["op"]: r for r in by_type["stage"]}
        self.assertEqual(len(stage), len(by_type["stage"]))
        self.assertEqual(set(stage), {"input", "topk"} | {f"{op}-{i}" for i in range(len(ids))
                                                        for op in ("read", "score")})
        self.assertTrue(all(r["success"] for r in stage.values()))
        ready = {}
        moves = staged if controller else completions
        submissions = {(row["candidate"], row["slice"]): row["time_ns"] for row in by_type["read_submit"]}
        for r in by_type["score_ready"]:
            i = r["candidate"]
            self.assertNotIn(i, ready)
            self.assertEqual(r["time_ns"], max(t for (c, _), t in moves.items() if c == i))
            self.assertEqual(r["time_ns"], stage[f"read-{i}"]["visible_ns"])
            ready[i] = r["time_ns"]
        self.assertEqual(set(ready), set(range(len(ids))))

        released = {r["candidate"]: r["time_ns"] for r in by_type["input_buffer"] if not r["reserve"]}
        current, seen = origin, set()
        batches = by_type["score_batch"]
        compute = cfg["compute"]
        rate = int(1000 * Decimal(str(compute["device_effective_tflops" if controller else "host_effective_tflops"])))
        for number, batch in enumerate(batches, 1):
            self.assertEqual(batch["batch"], number)
            current = max(current, min(time for i, time in ready.items() if i not in seen))
            if not schedule["overlap_read_compute"]:
                self.assertGreaterEqual(batch["start_ns"], current)
                current = batch["start_ns"]
                self.assertTrue(all(not (start < batch["end_ns"] and moves[key] > current)
                                    for key, start in submissions.items()))
            self.assertEqual(batch["start_ns"], current)
            eligible = sorted((time, i) for i, time in ready.items() if i not in seen and time <= current)
            chosen = [i for _, i in eligible[:schedule["compute_batch_documents"]]]
            self.assertEqual(batch["candidates"], chosen)
            self.assertLessEqual(1, len(chosen))
            self.assertLessEqual(len(chosen), schedule["compute_batch_documents"])
            self.assertEqual(batch["flop"], flop * len(chosen))
            duration = (int(1000 * Decimal(str(compute["batch_launch_us"]))) +
                        ceil_div(flop * len(chosen), rate))
            if where == "host_compute_free":
                duration = 0
            current += duration
            self.assertEqual((batch["duration_ns"], batch["end_ns"]), (duration, current))
            for i in chosen:
                self.assertEqual(released[i], current)
                self.assertGreaterEqual(stage[f"score-{i}"]["visible_ns"], current)
            seen.update(chosen)
        self.assertEqual(seen, set(ready))
        self.assertEqual(s["compute_ns"], sum(b["duration_ns"] for b in batches))
        self.assertEqual(s["batches"], len(batches))
        self.assertEqual(s["peak_scoring_engines"], 1)

        nand = by_type["nand_read"]
        page_sectors = cfg["layout"]["namespace_page_bytes"] // 512
        expected_pages = Counter()
        for _, lba, sectors, _ in planned.values():
            for page in range(lba // page_sectors, ceil_div(lba + sectors, page_sectors)):
                page_start = page * page_sectors
                bytes_read = (min(lba + sectors, page_start + page_sectors) - max(lba, page_start)) * 512
                expected_pages[page, bytes_read] += 1
        self.assertEqual(Counter((r["lpa"], r["bytes"]) for r in nand if r["user_data"]), expected_pages)
        self.assertEqual(s["nand_read_transactions"], len(nand))
        self.assertEqual(s["nand_user_read_bytes"], sum(r["bytes"] for r in nand if r["user_data"]))
        self.assertEqual(s["nand_user_read_bytes"], all_bytes)
        self.assertEqual(s["nand_read_bytes"], sum(r["bytes"] for r in nand))
        self.assertAlmostEqual(s["nand_read_amplification"], s["nand_read_bytes"] / (len(ids) * size))
        self.assertTrue(all(origin <= r["time_ns"] <= s["drained_ns"] and r["transfer_ns"] > 0
                            for r in nand))
        nand_spans = defaultdict(list)
        for event in by_type["nand_interval"]:
            r = event["mqsim"]
            self.assertLessEqual(origin, r["start_ns"])
            self.assertLessEqual(r["start_ns"], r["end_ns"])
            self.assertLessEqual(r["end_ns"], s["drained_ns"])
            span = r["start_ns"], r["end_ns"]
            nand_spans[r["kind"], r["channel"], r["chip"], r["die"]].append(span)
            if r["kind"] in ("command_bus", "command_data_in_bus", "data_out_bus"):
                nand_spans["bus", r["channel"], -1, -1].append(span)
        unions = {}
        for event in by_type["nand_interval_union"]:
            r = event["mqsim"]
            key = r["kind"], r["channel"], r["chip"], r["die"]
            self.assertNotIn(key, unions)
            self.assertEqual((r["window_start_ns"], r["window_end_ns"]), (origin, s["drained_ns"]))
            unions[key] = r["union_ns"]
        self.assertTrue({"array", "data_out_bus", "bus"} <= {key[0] for key in unions})
        rate = int(xml.findtext("./Device_Parameter_Set/Channel_Transfer_Rate"))
        width = int(xml.findtext("./Device_Parameter_Set/Flash_Channel_Width"))
        if rate > 1000:
            transfers = Counter(b - a for key, spans in nand_spans.items() if key[0] == "data_out_bus"
                                for a, b in spans)
            self.assertEqual(transfers, Counter(ceil_div(r["bytes"] * 1000, rate * width) for r in nand))
        self.assertEqual(unions, {key: interval_union(spans) for key, spans in nand_spans.items()})

        pcie = defaultdict(dict)
        for row in by_type["pcie"]:
            self.assertNotIn(row["event"], pcie[row["id"]])
            pcie[row["id"]][row["event"]] = row
        arrivals = []
        last_arrival = {"H2D": origin, "D2H": origin}
        for _, transfers in sorted(pcie.items()):
            self.assertEqual(set(transfers), {"enqueue", "start", "arrive"})
            enqueued, start, arrive = (transfers[phase] for phase in ("enqueue", "start", "arrive"))
            for field in ("layer", "direction", "platform_id", "payload_bytes", "wire_bytes", "service_ns"):
                self.assertEqual(enqueued[field], start[field])
                self.assertEqual(start[field], arrive[field])
            wire = 32 if arrive["layer"] == "sq_fetch" else (
                arrive["payload_bytes"] + 28 * ceil_div(arrive["payload_bytes"], mps))
            self.assertEqual(arrive["wire_bytes"], wire)
            service = ceil_div(wire, lanes) * lanes // (lanes * lane_rate)
            self.assertEqual(arrive["service_ns"], service)
            self.assertGreaterEqual(enqueued["time_ns"], origin)
            direction = arrive["direction"]
            self.assertEqual(start["time_ns"], max(enqueued["time_ns"], last_arrival[direction]))
            self.assertEqual(arrive["time_ns"], start["time_ns"] + service)
            self.assertLessEqual(arrive["time_ns"], s["drained_ns"])
            last_arrival[direction] = arrive["time_ns"]
            arrivals.append(arrive)
        for direction in ("H2D", "D2H"):
            link = [x for x in arrivals if x["direction"] == direction]
            self.assertEqual(s["pcie"][direction], dict(messages=len(link),
                                **{key: sum(x[key] for x in link)
                                   for key in ("payload_bytes", "wire_bytes", "service_ns")}))
        self.assertEqual(s["protocol_messages"], 2 if controller else 0)
        if controller:
            command, result = by_type["protocol"]
            (fixed,) = by_type["offload_command_service"]
            command_ns = int(1000 * Decimal(str(compute["offload_command_us"])))
            self.assertEqual((fixed["duration_ns"], fixed["end_ns"]), (command_ns, origin + command_ns))
            self.assertEqual(command["submitted_ns"], fixed["end_ns"])
            self.assertEqual(command["effective_payload_bytes"],
                             queries * dimension * protocol["query_component_bytes"] + 8 * len(ids))
            self.assertEqual(result["effective_payload_bytes"], 12 * len(ids))
            self.assertEqual(command["arrived_ns"], stage["input"]["visible_ns"])
            self.assertGreaterEqual(min(r["time_ns"] for r in by_type["read_submit"]), command["arrived_ns"])
            self.assertEqual(result["submitted_ns"], current)
            self.assertTrue(all(stage[f"score-{i}"]["visible_ns"] == result["arrived_ns"] for i in seen))
            self.assertEqual({r["layer"] for r in arrivals}, {"platform"})
            for item, kind, direction in ((command, "command", "H2D"), (result, "result", "D2H")):
                header_bytes = platform[f"{kind}_header_bytes"]
                self.assertEqual((item["kind"], item["direction"], item["header_bytes"]),
                                 (kind, direction, header_bytes))
                (link,) = [r for r in arrivals if r["platform_id"] == item["link_platform_id"]]
                self.assertEqual(link["payload_bytes"], item["effective_payload_bytes"] + header_bytes)
                self.assertEqual(link["payload_bytes"], item["link_payload_bytes"])
                self.assertEqual(link["wire_bytes"], item["wire_bytes"])
                self.assertEqual(link["time_ns"], item["arrived_ns"])
                self.assertEqual(link["direction"], direction)
                self.assertEqual(pcie[link["id"]]["enqueue"]["time_ns"], item["submitted_ns"])
        else:
            self.assertFalse(by_type["offload_command_service"] or by_type["protocol"])
            counts = Counter(row["layer"] for row in arrivals)
            self.assertEqual(counts, {kind: len(planned) * copies for kind, copies in
                                      (("doorbell", 2), ("sq_fetch", 1), ("sq_entry", 1),
                                       ("data", 1), ("cq_entry", 1))})
            self.assertEqual(sum(x["payload_bytes"] for x in arrivals if x["layer"] == "data"), all_bytes)
            self.assertEqual(Counter(x["time_ns"] for x in arrivals if x["layer"] == "cq_entry"),
                             Counter(completions.values()))
        self.assertEqual(by_type["metadata_buffers"][-1], dict(type="metadata_buffers",
                         time_ns=stage["topk"]["visible_ns"], controller_query_ids_bytes=0,
                         controller_score_bytes=0, host_score_bytes=0))
        self.assertEqual(stage["topk"]["submitted_ns"], max(stage[f"score-{i}"]["visible_ns"] for i in seen))
        (topk,) = by_type["topk"]
        self.assertEqual(topk["descriptor_bytes"], workload["top_k"] * 12)
        self.assertEqual(s["visible_ns"], topk["time_ns"])
        self.assertEqual(s["query_latency_ns"], topk["time_ns"] - origin)
        self.assertGreaterEqual(s["drained_ns"], s["visible_ns"])
        (query,) = by_type["query"]
        self.assertTrue(query["success"])
        self.assertEqual(query["visible_ns"], s["visible_ns"])
        self.assertLessEqual(query["visible_ns"], query["drained_ns"])
        self.assertLessEqual(query["drained_ns"], s["drained_ns"])
        for field, expected in (("controller_query_ids_bytes", command["effective_payload_bytes"] if controller else 0),
                                ("controller_score_bytes", 12 * len(ids) if controller else 0),
                                ("host_score_bytes", 12 * len(ids))):
            self.assertEqual(max(r[field] for r in by_type["metadata_buffers"]), expected)
            self.assertEqual(s[f"peak_{field}"], expected)
        return s, by_type

    def test_main_and_replay(self):
        bundle = self.bundle()
        runs = [self.directory / name for name in ("first run", "replay")]
        for folder in runs:
            run(bundle, BUILD, folder)
        verified = {}
        for placement in PLACEMENTS:
            name = f"{placement}.jsonl"
            summary, events = self.verify_execution(bundle, runs[0] / name)
            self.assertEqual((runs[0] / name).read_bytes(), (runs[1] / name).read_bytes())
            self.assertEqual((summary["logical_reads"], summary["effective_document_bytes"],
                              summary["flop"], summary["topk_bytes"]), (1500, 98304000, 3145728000, 120))
            self.assertEqual(events["measurement_start"][0]["preload_pages"], 40000)
            self.assertEqual(summary["document_d2h_payload_bytes"], 0 if placement == "device_all" else 98304000)
            if placement == "device_all":
                self.assertEqual([p["effective_payload_bytes"] for p in events["protocol"]], [21680, 1800])
            verified[placement] = summary
        self.assertEqual(verified["host"]["origin_ns"], verified["device_all"]["origin_ns"])
        self.assertEqual(verified["host"]["origin_ns"], verified["host_compute_free"]["origin_ns"])
        baseline = [r for r in rows(runs[0] / "host.jsonl") if r["type"].startswith("read_")]
        free = [r for r in rows(runs[0] / "host_compute_free.jsonl") if r["type"].startswith("read_")]
        self.assertEqual(baseline, free)
        write(self.directory / "verified.json", dict(identical_replay=True, verified=verified))

    def pressure(self, reason, limits):
        bundle = self.bundle({"workload": {"corpus_documents": 16, "candidate_count": 8, "top_k": 4},
                              "execution": limits})
        output = self.directory / "run"
        run(bundle, BUILD, output)
        for where in PLACEMENTS:
            summary, events = self.verify_execution(bundle, output / f"{where}.jsonl")
            self.assertIn(reason, {r["reason"] for r in events["backpressure"]})
            self.assertTrue(summary["all_resources_drained"])
        write(self.directory / "verified.json", dict(trigger=reason, resumed_and_drained=list(PLACEMENTS)))

    def test_window_pressure(self):
        self.pressure("window", {"read_window_documents_per_query": 1})

    def test_qd_pressure(self):
        self.pressure("qd", {"global_outstanding_reads": 2})

    def test_buffer_pressure(self):
        self.pressure("buffer", {"input_buffer_bytes_per_side": 655360})

    def test_local_10k_nand_execution(self):
        bundle = self.bundle(base="local-10k.json")
        output = self.directory / "run"
        run(bundle, BUILD, output, execution_budget=SMALL_BUDGET)
        report = json_file(output / "device-preflight-report.json")
        self.assertEqual(report["preload_pages"], 400_000)
        self.assertEqual(report["mapped_end_sectors"], 12_800_000)
        self.assertEqual(report["device"]["raw_sectors"], 16_777_216)
        self.assertEqual(report["device"]["partition"]["capacity_sectors"], 15_602_784)
        for placement in PLACEMENTS:
            summary, events = self.verify_execution(bundle, output / f"{placement}.jsonl")
            self.assertEqual(events["measurement_start"][0]["preload_pages"], 400_000)
            self.assertEqual((summary["logical_reads"], summary["flop"]), (1500, 3_145_728_000))

    def test_million_document_preflight(self):
        bundle = self.bundle(base="e0.4.json")
        limits = json_file(HERE / "e0.4-device-limits.json")
        command = [sys.executable, str(HERE / "run.py"), "--input", str(bundle), "--build", str(BUILD)]
        if BACKEND_SOURCE is not None:
            command += ["--backend-source-sha256", BACKEND_SOURCE]
        metadata_only = self.directory / "metadata-only.json"
        write(metadata_only, dict(limits, preload_pages=1_000_000))
        for name, extra, expected in [
            ("default-limits", [], "metadata estimate exceeds allocation budget"),
            ("default-preload", ["--device-limits", str(metadata_only)], "preload page budget"),
        ]:
            output = self.directory / name
            self.command(name, command + ["--output", str(output), "--preflight-only", *extra], success=False)
            self.assertIn(expected, (output / "device-preflight.stderr").read_text())
            self.assertFalse((output / "device-preflight.jsonl").exists())

        output = self.directory / "preflight"
        report = run(bundle, BUILD, output, device_limits=limits, preflight_only=True)
        self.assertFalse(report["allocated"])
        device = report["device"]
        self.assertEqual(device["limits"], limits)
        self.assertEqual(device["page_bytes"], 16_384)
        self.assertEqual(device["raw_sectors"], 2**31)
        self.assertEqual(device["partition"]["capacity_sectors"], 1_997_159_776)
        self.assertEqual(device["metadata_estimate_bytes"], 18_257_870_848)
        self.assertEqual(report["mapped_end_sectors"], 1_280_000_000)
        self.assertEqual(report["preload_sectors"], 1_280_000_000)
        self.assertEqual(report["preload_pages"], 40_000_000)
        self.assertEqual(report["logical_reads"], 1500)
        self.assertEqual(report["layout_sha256"], file_hash(bundle / "layout.json"))
        self.assertEqual(json_file(bundle / "logical-layout.json")["corpus_allocated_bytes"], 655_360_000_000)
        self.assertEqual(json_file(bundle / "accounting.json")["document_effective_bytes"], 98_304_000)
        self.assertEqual([e["type"] for e in rows(output / "device-preflight.jsonl")],
                         ["configuration", "device_preflight"])
        budget_path = self.directory / "small-budget.json"
        write(budget_path, SMALL_BUDGET)
        output = self.directory / "ram-rejection"
        rejected = self.command("ram-rejection", command + ["--output", str(output), "--device-limits",
                                str(HERE / "e0.4-device-limits.json"), "--budget", str(budget_path)], success=False)
        self.assertIn("device metadata estimate exceeds execution RAM budget", rejected.stderr)
        self.assertFalse(any((output / f"{p}.json").exists() for p in PLACEMENTS))
        self.assertFalse((output / "execution.json").exists())
        write(self.directory / "verified.json", report)

    def test_scaled_million_device_nand_execution(self):
        # Preserve the large device's topology, 16 KiB page and 256-page block.
        xml = self.directory / "scaled-device.xml"
        xml.write_bytes((HERE / "e0.4-device.xml").read_bytes().replace(
            b"<Block_No_Per_Plane>8192<", b"<Block_No_Per_Plane>64<"))
        bundle = self.bundle({
            "workload": {"corpus_documents": 1000},
            "layout": {"namespace_capacity_sectors": 15_602_784},
            "hardware": {"mqsim_config_path": str(xml), "mqsim_config_hash": file_hash(xml)},
        }, base="e0.4.json")
        limits = dict(xml_bytes=1024**2, metadata_bytes=256 * 1024**2, preload_pages=40_000)
        output = self.directory / "run"
        run(bundle, BUILD, output, device_limits=limits, execution_budget=SMALL_BUDGET)
        report = json_file(output / "device-preflight-report.json")
        self.assertEqual(report["preload_pages"], 40_000)
        self.assertEqual(report["device"]["xml_fields"]["flash"]["Page_No_Per_Block"], 256)
        self.assertEqual(report["device"]["limits"], limits)
        verified = {}
        for placement in PLACEMENTS:
            trace = output / f"{placement}.jsonl"
            summary, _ = self.verify_execution(bundle, trace)
            self.assertEqual(next(rows(trace))["device"], report["device"])
            process = json_file(output / f"{placement}.command.json")
            self.assertEqual(process["exit_code"], 0)
            self.assertGreater(process["peak_child_rss_bytes"], 0)
            verified[placement] = dict(summary=summary, rss=process["peak_child_rss_bytes"],
                                       wall_seconds=process["wall_seconds"])
        write(self.directory / "verified.json", verified)

    def test_multiple_ready_documents_and_padded_tail(self):
        bundle = self.bundle({"workload": {"corpus_documents": 16, "candidate_count": 8, "top_k": 4,
                                           "document_vectors": 1023},
                              "compute": {"batch_launch_us": 100000}})
        output = self.directory / "run"
        run(bundle, BUILD, output, ("host", "device_all"))
        found = {}
        for where in ("host", "device_all"):
            summary, events = self.verify_execution(bundle, output / f"{where}.jsonl")
            sizes = [len(b["candidates"]) for b in events["score_batch"]]
            self.assertGreater(max(sizes), 1)
            self.assertGreater(summary["logical_request_bytes"], summary["effective_document_bytes"])
            found[where] = sizes
        write(self.directory / "verified.json", dict(batch_sizes=found, padded_tail=True))

    def test_serial_reads_and_scores_with_sixteen_document_batches(self):
        bundle = self.bundle({"workload": {"corpus_documents": 32, "candidate_count": 20},
                              "execution": {"overlap_read_compute": False, "compute_batch_documents": 16}})
        output = self.directory / "run"
        run(bundle, BUILD, output, ("host", "device_all"))
        for where in ("host", "device_all"):
            _, events = self.verify_execution(bundle, output / f"{where}.jsonl")
            self.assertEqual(max(len(batch["candidates"]) for batch in events["score_batch"]), 16)

    def test_preflight_refuses_invalid_run_and_artifacts(self):
        bundle = self.bundle({"workload": {"corpus_documents": 16, "candidate_count": 8, "top_k": 4}})
        output = self.directory / "baseline"
        run(bundle, BUILD, output, ("host",))
        record = json_file(output / "host.process.json")
        spec = json_file(output / "host.json")
        failing = self.directory / "failing"
        failing.mkdir()

        def reject_sst(name, expected, **changes):
            req = failing / f"{name}.json"
            config = dict(spec, scratch=f"{name}.sqlite", output=f"{name}.jsonl")
            config.update(changes)
            write(req, config)
            p = self.command(name, record["command"][:-1] + [json.dumps(str(req))], success=False)
            self.assertIn(expected, p.stderr)
            self.assertFalse((failing / config["output"]).exists())

        reject_sst("mapping", "unsupported MaxSim placement", placement="unknown")
        reject_sst("backend", "MQSim build and input identity mismatch", mqsim_source_sha256="0" * 64)
        for name, field, value, expected in [
            ("too-small", "input_buffer_bytes_per_side", 655359, "buffer cannot hold one document"),
            ("excess-qd", "global_outstanding_reads", 257, "QD exceeds device queue depth"),
        ]:
            modified = failing / name
            shutil.copytree(bundle, modified)
            effective = modified / "effective-config.json"
            altered = json_file(effective)
            altered["config"]["execution"][field] = value
            effective.unlink()
            write(effective, altered)
            receipt = modified / "receipt.json"
            data = json_file(receipt)
            data["artifacts"][effective.name] = file_hash(effective)
            receipt.unlink()
            write(receipt, data)
            reject_sst(name, expected, bundle=str(modified), receipt_sha256=file_hash(receipt))
        for name, artifact, field in [("altered-accounting", "accounting.json", "dot_product_flop"),
                                      ("altered-layout", "layout.json", "alignment_bytes")]:
            modified = failing / name
            shutil.copytree(bundle, modified)
            target = modified / artifact
            altered = json_file(target)
            altered[field] += 1
            target.unlink()
            write(target, altered)
            # Rehash the sidecar in the receipt so the independent audit catches it.
            receipt = modified / "receipt.json"
            data = json_file(receipt)
            data["artifacts"][target.name] = file_hash(target)
            receipt.unlink()
            write(receipt, data)
            destination = failing / f"{name}-output"
            p = self.command(name, [sys.executable, str(HERE / "run.py"), "--input", str(modified),
                                    "--build", str(BUILD), "--output", str(destination)], success=False)
            self.assertIn("mismatch", p.stderr)
            self.assertFalse(destination.exists())
        write(self.directory / "verified.json", dict(rejected_before_measurement=6))


if __name__ == "__main__":
    unittest.main(verbosity=2)
