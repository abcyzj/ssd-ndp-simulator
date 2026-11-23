"""Acceptance checks for the synthetic MaxSim generator and E0 inputs."""

import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ndpsim.static_ir import PackageWriter, ValidationError, canonical, decode

from config import ROOT, derive, load_config
from generate import generate
from ir import registry
from sampling import Candidates, arrivals
from verify import verify


def read(path):
    return decode(path.read_bytes())


def lines(path):
    return [decode(line) for line in path.read_bytes().splitlines()]


def small_overrides():
    return {"workload": {"model_preset": "custom", "embedding_dim": 19, "document_vectors": 109,
                         "template_token_increment": 1, "query_vectors": 2, "candidate_count": 5,
                         "top_k": 5, "corpus_documents": 5, "storage_dtype": "fp16", "zipf_alpha": 1.2},
            "cache": {"host_capacity_bytes": 5 * 4180, "initial_state": "prewarmed"},
            "layout": {"logical_read_bytes": 4096, "namespace_capacity_sectors": 80, "namespace_page_bytes": 4096},
            "execution": {"input_buffer_bytes_per_side": 4180, "overlap_read_compute": False},
            "load": {"mode": "open_loop"}, "generation": {"queries": 4}}


class MaxSimAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ndpsim-maxsim-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, success=True):
        environment = os.environ.copy()
        sdk = str(ROOT.parents[1] / "python")
        environment["PYTHONPATH"] = sdk
        command = [sys.executable, str(ROOT / "generate.py"), *map(str, args)]
        call = subprocess.run(command, env=environment, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(call.returncode, 0 if success else 1, call.stderr)
        return json.loads(call.stdout) if success else call.stderr

    def config_file(self, override):
        config = self.root / "overrides.json"
        config.write_bytes(canonical(override))
        return config

    def test_main_ledger_and_two_independent_generations(self):
        one, two = self.root / "first output", self.root / "second output"
        left = self.cli("generate", "--output", one)
        right = self.cli("generate", "--output", two)
        self.assertEqual(left["package_sha256"], right["package_sha256"])
        self.assertEqual(left["artifacts"], right["artifacts"])
        inspection = self.cli("check", "--input", two)
        self.assertTrue(inspection["structural_passed"])
        tally = inspection["accounting"]
        expected = {"document_effective_bytes": 98_304_000, "all_miss_logical_requests": 1500,
                    "all_miss_request_bytes": 98_304_000, "dot_product_flop": 3_145_728_000,
                    "downlink_bytes_per_query": 21_680, "uplink_bytes_per_query": 1800}
        self.assertEqual({name: tally[name] for name in expected}, expected)
        layout = read(one / "logical-layout.json")
        self.assertEqual(layout["minimum_capacity_sectors"], 12_800_000)
        self.assertEqual(layout["corpus_effective_bytes"], 6_553_600_000)
        self.assertEqual(read(one / "effective-config.json")["config"]["cache"]["host_capacity_bytes"],
                         343_597_383)
        self.assertFalse((one / "layout.json").exists())
        self.assertFalse((one / "arrivals.jsonl").exists())
        structure = read(one / "structure.json")
        self.assertFalse(structure["independent_semantic_passed"])
        self.assertEqual(structure["counts"], dict(queries=1, operations=302, stages=302, dependencies=600))
        self.assertIn("output already exists", self.cli("generate", "--output", one, success=False))

    def test_e0_bundle_matches_local_acceptance(self):
        output = self.root / "e0"
        receipt = self.cli("generate", "--config", ROOT / "e0.json", "--point", "E0-single-01",
                           "--output", output)
        inspection = self.cli("check", "--input", output)
        self.assertEqual(inspection["package_sha256"], receipt["package_sha256"])
        record = read(output / "effective-config.json")
        cfg, derived = record["config"], record["derived"]
        self.assertEqual(cfg["workload"]["corpus_documents"], 1000)
        self.assertEqual(cfg["load"]["closed_loop_concurrency"], 1)
        self.assertEqual(cfg["cache"]["host_capacity_bytes"], 0)
        self.assertEqual(cfg["cache"]["device_extra_capacity_bytes"], 0)
        self.assertEqual(cfg["experiment"]["schemes"], ["host", "host_compute_free", "device_all"])
        self.assertEqual([cfg["generation"][f"{domain}_seed"]
                          for domain in ("layout", "candidate", "arrival")], [11, 11, 11])
        self.assertEqual(record["hardware_fields_pending"], [])
        self.assertEqual(Path(cfg["hardware"]["mqsim_config_path"]), ROOT / "e0-device.xml")
        document_ids = lines(output / "candidates.jsonl")[0]["document_ids"]
        self.assertEqual(len(set(document_ids)), 150)
        self.assertTrue(all(0 <= doc < 1000 for doc in document_ids))
        layout = read(output / "layout.json")
        self.assertEqual(layout["namespace"]["capacity_sectors"], 3_900_700)
        self.assertEqual(layout["namespace"]["page_bytes"], 16_384)
        self.assertEqual(layout["objects"][0]["extents"][0]["length_bytes"], 655_360_000)
        self.assertEqual(derived["minimum_capacity_sectors"], 1_280_000)
        self.assertEqual(inspection["accounting"]["all_miss_logical_requests"], 1500)
        self.assertEqual(inspection["accounting"]["dot_product_flop"], 3_145_728_000)

    def test_full_candidate_coverage_tail_padding_and_arrival_order(self):
        output = self.root / "small"
        cfg = self.config_file(small_overrides())
        self.cli("generate", "--config", cfg, "--output", output)
        self.assertTrue(self.cli("check", "--input", output)["accounting"]["passed"])
        for row in lines(output / "candidates.jsonl"):
            self.assertEqual(sorted(row["document_ids"]), list(range(5)))
        ledger = lines(output / "logical-reads.jsonl")
        for doc in ledger[0]["documents"]:
            start = doc["document_id"] * 8192
            self.assertEqual(doc["requests"], [{"offset_bytes": start, "valid_bytes": 4096, "request_bytes": 4096},
                                               {"offset_bytes": start + 4096, "valid_bytes": 84, "request_bytes": 4096}])
        self.assertEqual(read(output / "layout.json")["objects"][0]["extents"][0]["length_bytes"], 40960)
        record = read(output / "effective-config.json")
        self.assertEqual(record["config"]["execution"]["input_buffer_bytes_per_side"], 4180)
        self.assertEqual(record["config"]["cache"]["host_capacity_bytes"], 5 * 4180)
        offered = [row["offered_ns"] for row in lines(output / "arrivals.jsonl")]
        self.assertEqual(len(offered), 4)
        self.assertEqual(offered, sorted(offered))

    def test_runtime_only_changes_share_ir_and_candidate_stream(self):
        base = small_overrides()
        left = load_config(override=base)
        first = self.root / "open"
        right = load_config(override={**base, "load": {"mode": "closed_loop"},
                                      "cache": {"host_capacity_bytes": 0},
                                      "compute": {"host_effective_tflops": 16},
                                      "hardware": {"external_effective_GBps": 28}})
        second = self.root / "closed"
        a, b = generate(left, first, ["test"]), generate(right, second, ["test"])
        self.assertEqual(a["package_sha256"], b["package_sha256"])
        self.assertEqual(a["artifacts"]["candidates.jsonl"], b["artifacts"]["candidates.jsonl"])
        self.assertNotEqual(a["artifacts"]["effective-config.json"], b["artifacts"]["effective-config.json"])
        self.assertFalse((second / "arrivals.jsonl").exists())

    def test_scan_and_model_presets(self):
        points = self.cli("scan")
        self.assertEqual(len({point["id"] for point in points}), len(points))
        self.assertEqual(sum(point["id"].startswith("E1-") for point in points), 48)
        self.assertEqual({point["id"].split("-")[0] for point in points}, {f"E{i}" for i in range(6)})
        expected_cache = {0: 0, 0.0131072: 85_899_345, 0.0524288: 343_597_383,
                          0.2097152: 1_374_389_534}
        reference = self.config_file({"workload": {"corpus_documents": 1_000_000}})
        reference_points = self.cli("scan", "--batch", "E1", "--config", reference)
        self.assertEqual({point["host_capacity_bytes"] for point in reference_points},
                         {0, 8 * 1024**3, 32 * 1024**3, 128 * 1024**3})
        for point in points:
            if point["id"].startswith("E1-"):
                fraction = point["override"]["cache"]["host_capacity_fraction"]
                self.assertEqual(point["host_capacity_bytes"], expected_cache[fraction])
        comparisons = {"colqwen2.5": (128, 755, 28_992_000, 927_744_000),
                       "colpali": (128, 1030, 39_552_000, 1_265_664_000)}
        for point in points:
            preset = point["override"].get("workload", {}).get("model_preset")
            if preset in comparisons:
                record = self.cli("describe", "--point", point["id"])
                work = record["config"]["workload"]
                totals = record["derived"]
                self.assertEqual((work["embedding_dim"], work["document_vectors"],
                                  totals["query_document_bytes"], totals["query_flop"]), comparisons.pop(preset))
        self.assertFalse(comparisons)
        fp32 = load_config(override={"workload": {"storage_dtype": "fp32", "template_token_increment": 64}})
        result = derive(fp32)
        self.assertEqual(result["document_bytes"], 1_392_640)
        self.assertEqual(result["query_bytes"], 20_480)
        self.assertEqual(result["query_flop"], 3_342_336_000)
        self.assertEqual(fp32["cache"]["host_capacity_bytes"], 730_144_440)
        large = max(point["derived"]["corpus_effective_bytes"] for point in points)
        self.assertEqual(large, 65_536_000_000)
        corpus_sizes = [point["override"]["workload"]["corpus_documents"] for point in points
                        if "corpus_documents" in point["override"].get("workload", {})]
        self.assertEqual(corpus_sizes, [100_000])

    def test_distribution_and_rng_isolation(self):
        base = {"corpus_documents": 100, "candidate_count": 1, "top_k": 1, "randomize_rank_to_document": False}
        uniform = load_config(override={"workload": {**base, "distribution": "uniform", "zipf_alpha": 0}})
        weighted = load_config(override={"workload": {**base, "zipf_alpha": 1.2}})
        uniform_sampler, zipf_sampler = Candidates(uniform), Candidates(weighted)
        self.assertGreater(sum(zipf_sampler.draw()[0] < 5 for _ in range(400)),
                           sum(uniform_sampler.draw()[0] < 5 for _ in range(400)) + 130)
        root = small_overrides()
        a = load_config(override=root)
        root["generation"].update(arrival_seed=47)
        b = load_config(override=root)
        self.assertEqual(Candidates(a).draw(), Candidates(b).draw())
        self.assertNotEqual(list(arrivals(a)), list(arrivals(b)))
        root["generation"].update(layout_seed=47, arrival_seed=11)
        c = load_config(override=root)
        self.assertEqual(list(arrivals(a)), list(arrivals(c)))
        self.assertNotEqual(Candidates(a).draw(), Candidates(c).draw())
        root["generation"].update(candidate_seed=47, layout_seed=11)
        d = load_config(override=root)
        self.assertEqual(list(arrivals(a)), list(arrivals(d)))
        self.assertNotEqual(Candidates(a).draw(), Candidates(d).draw())

    def test_single_candidate_fp32_with_fp16_query(self):
        override = small_overrides()
        override["workload"].update(candidate_count=1, top_k=1, storage_dtype="fp32",
                                    distribution="uniform", zipf_alpha=0)
        override["layout"]["namespace_capacity_sectors"] = 120
        override["execution"]["input_buffer_bytes_per_side"] = 8360
        override["generation"]["queries"] = 1
        output = self.root / "single-fp32"
        config = load_config(override=override)
        generate(config, output, ["test"])
        report = read(output / "accounting.json")
        self.assertEqual(report["document_effective_bytes"], 8360)
        self.assertEqual(report["downlink_bytes_per_query"], 84)
        self.assertEqual(report["all_miss_logical_requests"], 3)
        self.assertEqual(report["topk_bytes_per_query"], 12)

    def test_invalid_configs_and_budgets_reject_before_output(self):
        errors = [
            ({"workload": {"candidate_count": 1}}, "top_k"),
            ({"workload": {"storage_dtype": "fp32", "bytes_per_component": 2}}, "dtype/byte"),
            ({"workload": {"scoring": "pruning"}}, "unsupported"),
            ({"workload": {"zipf_alpha": True}}, "finite number"),
            ({"workload": {"document_vectors": 2**64 - 1}}, "document_bytes"),
            ({"workload": {"unknown": 0}}, "unknown configuration key"),
            ({"cache": {"host_capacity_fraction": 1.01}}, "host_capacity_fraction"),
            ({"cache": {"host_capacity_fraction": 0.5, "host_capacity_bytes": 1}},
             "host cache bytes do not match"),
            ({"layout": {"namespace_capacity_sectors": 1, "namespace_page_bytes": 4096}}, "namespace capacity"),
            ({"execution": {"input_buffer_bytes_per_side": 1}}, "one complete document"),
            ({"generation": {"package_budget_bytes": 1024}}, "package budget"),
            ({"generation": {"scratch_budget_bytes": 1024}}, "SQLite budget"),
            ({"generation": {"sampling_memory_budget_bytes": 1024}}, "sampling memory budget"),
            ({"generation": {"queries": 10000}}, "package budget"),
        ]
        for n, (override, message) in enumerate(errors):
            with self.subTest(message=message):
                output = self.root / f"failure-{n}"
                error = self.cli("generate", "--config", self.config_file(override), "--output", output,
                                 success=False)
                self.assertIn(message, error)
                self.assertFalse(output.exists())

    def test_structural_and_independent_audit_failures(self):
        override = small_overrides()
        override["generation"]["queries"] = 1
        config = load_config(override=override)
        output = self.root / "audit"
        generate(config, output, ["test"])
        package = output / "package"
        operations = package / "operations.jsonl"
        original = operations.read_bytes()
        for name, change in (("output size", lambda op: op["stages"][0]["outputs"][0].update(logical_bytes=99)),
                             ("access range", lambda op: op["stages"][0]["accesses"][0].update(offset_bytes=1 << 62))):
            with self.subTest(name=name):
                manifest = read(package / "manifest.json")
                payload = lines(operations)
                change(payload[1])
                with self.assertRaisesRegex(ValidationError, "output-size" if name == "output size" else "access range"):
                    with PackageWriter(self.root / name, manifest["provenance"], registry(), allow_fixture=True) as writer:
                        writer.declare_object(manifest["objects"][0])
                        for operation in payload:
                            writer.record(operation)
                self.assertFalse((self.root / name).exists())
        for key, mutate in (("dependency", lambda data: data[-1]["stages"][0]["dependencies"].pop()),
                            ("Work", lambda data: data[2]["stages"][0]["work"]["dot_product_flop"].update(value=99))):
            with self.subTest(key=key):
                data = lines(operations)
                mutate(data)
                operations.write_bytes(b"".join(map(canonical, data)))
                with self.assertRaisesRegex(ValidationError, key):
                    verify(output, config)
                operations.write_bytes(original)
        (output / "logical-layout.json").write_text("{}")
        self.assertIn("artifact identity", self.cli("check", "--input", output, success=False))

    def test_optional_hardware_file_is_hashed_not_assumed(self):
        xml = self.root / "test.xml"
        xml.write_text("<fixture/>")
        override = self.config_file({"hardware": {"mqsim_config_path": "test.xml"}})
        record = self.cli("describe", "--config", override)
        self.assertEqual(record["hardware_fields_pending"], ["backend_commit"])
        self.assertEqual(len(record["config"]["hardware"]["mqsim_config_hash"]), 64)
        self.assertEqual(record["hardware_validation"], "not_performed")
        config = self.config_file({"hardware": {"mqsim_config_path": "test.xml", "mqsim_config_hash": "a" * 64}})
        self.assertIn("SHA256 mismatch", self.cli("describe", "--config", config, success=False))

    def test_failed_audit_does_not_publish_bundle(self):
        output = self.root / "failed audit"
        config = load_config(override=small_overrides())
        with patch("generate.verify", side_effect=ValidationError("independent audit failure")):
            with self.assertRaisesRegex(ValidationError, "independent audit failure"):
                generate(config, output, ["test"])
        self.assertFalse(output.exists())
        failed = self.root / "failed audit.failed"
        self.assertTrue((failed / "package" / "tail.json").is_file())
        self.assertFalse((failed / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
