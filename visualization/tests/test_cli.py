import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from ndpsim_viz.cli import main
from ndpsim_viz.reader import Package
from test_reader import QUERY, encode, make_package, make_run


class CliTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.source = self.root / "package"
        make_package(self.source)

    def invoke(self, output, *args):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = main(["--package", str(self.source), "--query", str(QUERY),
                           "--output", str(output), *map(str, args)])
        return status, stderr.getvalue()

    def test_capture_and_existing_output(self):
        out = self.root / "out"
        self.assertEqual(self.invoke(out), (0, ""))
        manifest_path = out / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual((manifest["query"], manifest["mode"]), (str(QUERY), "capture-only"))
        self.assertEqual(json.loads((out / "timeline.json").read_text())["traceEvents"], [])
        self.assertTrue((out / "dag.html").is_file())
        before = manifest_path.read_bytes()
        self.assertEqual(self.invoke(out)[0], 2)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_single_run_and_invalid_comparison(self):
        identity = Package(self.source, self.root / "index").identity
        report = make_run(self.root / "run", identity)
        single = self.root / "single"
        self.assertEqual(self.invoke(single, "--report", report), (0, ""))
        manifest = json.loads((single / "manifest.json").read_text())
        self.assertEqual(manifest["mode"], "single-run")
        self.assertEqual(manifest["coverage"]["runs"]["host"]["admission_ns"], "7")
        bad = self.root / "bad"
        status, message = self.invoke(bad, "--report", report, "--report", report)
        self.assertEqual(status, 2)
        self.assertIn("one Host report and one Controller report", message)
        self.assertFalse((bad / "manifest.json").exists())

        controller = make_run(self.root / "controller", identity)
        data = json.loads(controller.read_bytes())
        data["placement"] = "controller"
        controller.write_bytes(encode(data))
        trace = controller.with_name("trace.jsonl")
        header, *records = map(json.loads, trace.read_bytes().splitlines())
        header["snapshot"]["config"]["placement"] = "controller"
        trace.write_bytes(b"".join(map(encode, [header, *records])))
        out = self.root / "comparison"
        self.assertEqual(self.invoke(out, "--report", controller, "--report", report), (0, ""))
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertEqual(manifest["mode"], "comparison")
        self.assertEqual(set(manifest["runs"]), {"host", "controller"})

    def test_cannot_write_within_inputs(self):
        status, message = self.invoke(self.source / "out")
        self.assertEqual(status, 2)
        self.assertIn("outside the package and report directories", message)
        self.assertFalse((self.source / "out").exists())
