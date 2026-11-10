import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ndpsim_viz.dag import dag_data, write_dag
from ndpsim_viz.reader import Package

from test_reader import make_package, QUERY


class DagTests(unittest.TestCase):
    def test_stable_keys_and_explicit_dependency(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            make_package(root / "package")
            package = Package(root / "package", root / "cache")
            graph = dag_data(package, QUERY)
        self.assertEqual(graph["query"], str(QUERY))
        nodes = {node["id"]: node for node in graph["nodes"]}
        edge, = graph["edges"]
        self.assertEqual(nodes[edge["source"]]["key"], {"query": str(QUERY), "op": "left", "stage": "sum"})
        self.assertEqual(nodes[edge["target"]]["key"], {"query": str(QUERY), "op": "join", "stage": "sum"})
        self.assertEqual((edge["kind"], edge["sourcePort"], edge["targetPort"]), ("DATA", "out", "in"))

    def test_embedded_data_cannot_escape_script(self):
        class Sample:
            identity = "test"

            def query(self, query_id):
                return [{"id": "</script><script>test()", "operator": "fixture",
                         "stages": [{"id": "stage", "work": {"units": QUERY}, "dependencies": []}]}]

        with TemporaryDirectory() as directory:
            html = Path(directory) / "dag.html"
            write_dag(html, Sample(), QUERY)
            document = html.read_text()
        self.assertEqual(document.lower().count("</script>"), 2)
        data = document.split('<script id="graph-data" type="application/json">')[1].split("</script>")[0]
        self.assertEqual(json.loads(data)["nodes"][1]["work"], {"units": str(QUERY)})
        self.assertIn("default-src 'none'", document)
