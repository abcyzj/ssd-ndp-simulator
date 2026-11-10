import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from ndpsim_viz.reader import InputError
from ndpsim_viz.timeline import write_timeline


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.package = SimpleNamespace(query=lambda _: [], identity="package")
        self.key = (7, "op", "score")
        self.other = (7, "op", "prepare")

    def run_data(self, admission=100):
        event_key = json.dumps(dict(query=7, op="op", stage="score"))
        events = [
            dict(type="cpu", key=event_key, submitted=admission + 1, start=admission + 1, end=admission + 5),
            dict(type="ordinary_read", completed=admission + 5),
            dict(type="local_read", query=7, op="op", stage="score", completed=admission + 5),
            dict(type="protocol", query=7, task=1, chunk=1, submitted=admission + 5, arrived=admission + 6),
        ]
        for packet, points in ((2, (("arrive", 2), ("enqueue", -2), ("start", 0))),
                               (3, (("enqueue", 5), ("start", 6)))):
            events.extend(dict(type="pcie", id=packet, event=edge, time=admission + delta,
                               destination=1, layer="read") for edge, delta in points)
        return SimpleNamespace(
            request=lambda _: dict(admitted=admission, visible=admission + 10,
                                   drained=admission + 10, success=True),
            events=lambda: iter(events), report=dict(package_sha256="package"),
            placement="host", profile_sha256="profile", context="fixture", config_sha256="config",
            stage_timings=lambda _: {
                self.key: dict(status="completed", submitted_ns=admission + 1, completed_ns=admission + 5),
                self.other: dict(status="completed", submitted_ns=admission + 2, completed_ns=admission + 7),
                (7, "op", "zero"): dict(status="completed", submitted_ns=admission + 5,
                                       completed_ns=admission + 5),
                (7, "op", "missing"): dict(status="missing", submitted_ns=None, completed_ns=None),
            })

    def export(self, runs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "timeline.json")
            coverage = write_timeline(path, self.package, 7, runs)
            contents = json.loads(path.read_text())
            self.assertEqual(contents["ndpsim"], coverage)
            return contents

    def test_parallel_zero_and_source_alignment(self):
        trace = self.export([("B", self.run_data()), ("C", self.run_data(200))])
        slices = [event for event in trace["traceEvents"] if event["ph"] == "X"]
        stage = {(e["pid"], e["name"]): e for e in slices if e["cat"] == "Stage"}
        self.assertNotEqual(stage[1, "op / score"]["tid"], stage[1, "op / prepare"]["tid"])
        self.assertNotEqual(stage[1, "op / score"]["tid"], stage[1, "op / zero"]["tid"])
        self.assertEqual(stage[1, "op / zero"]["dur"], 0)
        self.assertEqual(stage[1, "op / score"]["ts"], stage[2, "op / score"]["ts"])
        self.assertEqual(stage[1, "op / score"]["args"]["sst_start_ns"], "101")
        self.assertEqual(stage[2, "op / score"]["args"]["sst_start_ns"], "201")
        self.assertEqual(stage[1, "op / score"]["args"]["stage_key"], '{"query":"7","op":"op","stage":"score"}')
        self.assertEqual(trace["ndpsim"]["display_offset_ns"], "2")
        self.assertEqual(trace["ndpsim"]["runs"]["B"]["stage_statuses"]["missing"], 1)
        self.assertEqual(trace["ndpsim"]["runs"]["B"]["resource_counts"]["pcie_incomplete"], 1)
        ordinary, = [e for e in trace["traceEvents"] if e["name"] == "ordinary_read" and e["pid"] == 1]
        self.assertEqual(ordinary["args"]["attribution"], "window")
        self.assertNotIn("stage_key", ordinary["args"])

    def test_capture_and_identity(self):
        self.assertEqual(self.export([])["traceEvents"], [])
        run = self.run_data()
        run.report["package_sha256"] = "other"
        with self.assertRaisesRegex(InputError, "does not match"):
            self.export([("B", run)])

    def test_bad_endpoints(self):
        run = self.run_data()
        run.events = lambda: iter([dict(type="cpu", submitted=101, start=100, end=103)])
        with self.assertRaisesRegex(InputError, "reversed"):
            self.export([("B", run)])
