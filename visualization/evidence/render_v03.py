"""Render the registered teaching and formal samples to a separate output directory."""
import argparse
import json
from pathlib import Path

from ndpsim_viz.dag import write_dag
from ndpsim_viz.reader import Package, Run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    output.mkdir(exist_ok=True)
    evidence = json.loads(Path(__file__).with_name("v01-samples.json").read_text())
    for name in ("teaching", "formal"):
        sample = evidence[name]
        package = Package(sample["path"], output / "cache")
        runs = [
            (label, Run(package, Path(sample["path"]).parents[2] / label / "report.json", output / "cache"))
            for label in ("B01", "C01")
        ] if name == "formal" else []
        write_dag(output / f"{name}.html", package, sample["query"], runs)


if __name__ == "__main__":
    main()
