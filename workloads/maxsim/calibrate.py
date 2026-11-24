"""Measure an explicit MaxSim device with ordinary NVMe reads under SST time."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import sys
from xml.etree import ElementTree as ET

from ndpsim.execution import budget, launch, preflight
from ndpsim.static_ir import file_hash, require

from config import load_config
from generate import write
from run import HERE, backend_digest, json_file

# Micron 9550 PRO 7.68 TB, 4 KiB LBA, Technical Product Specification Rev. A.
CASES = {
    "rand4k-qd1": (4, 1, 4096, True, {"p50_us": 60, "p99_us": 72}),
    "seq128k-qd32": (128, 32, 4096, False, {"GBps": 14}),
    "rand4k-qd512": (4, 512, 16384, True, {"IOPS": 3_300_000}),
    "rand64k-qd64": (64, 64, 8192, True, {}),
}
WINDOW_SECTORS = 512 * 1024**2 // 512


def verify(stdout, requests, qd, xml):
    lines = stdout.read_text().splitlines()
    (device,) = [json.loads(line.removeprefix("CAL_DEVICE ")) for line in lines if line.startswith("CAL_DEVICE ")]
    require(device["source_sha256"] == file_hash(xml), "calibration device identity mismatch")
    (drain,) = [line.split()[1:] for line in lines if line.startswith("CAL_DRAIN ")]
    start, end, count = map(int, drain)
    records = sorted(tuple(map(int, line.split()[1:])) for line in lines if line.startswith("CAL_READ "))
    require(count == len(records) == len(requests) and end > start, "ordinary read calibration count")
    require([row[1:3] for row in records] == requests and
            len({row[0] for row in records}) == count, "ordinary read calibration request identity")
    require(min(row[3] for row in records) == start and max(row[4] for row in records) == end,
            "ordinary read calibration window")
    depth = Counter()
    for _, _, _, submitted, completed in records:
        require(start <= submitted < completed <= end, "ordinary read calibration timestamp")
        depth[submitted] += 1
        depth[completed] -= 1
    in_flight = peak = 0
    for timestamp in sorted(depth):
        in_flight += depth[timestamp]
        require(0 <= in_flight <= qd, "ordinary read calibration outstanding cap")
        peak = max(peak, in_flight)
    require(in_flight == 0 and peak == qd, "ordinary read calibration peak outstanding or drain")
    values = sorted(row[4] - row[3] for row in records)
    return dict(requests=count, peak_qd=peak, measured_ns=end - start, bytes=sum(row[2] * 512 for row in records),
                IOPS=count * 1e9 / (end - start),
                GBps=sum(row[2] * 512 for row in records) / (end - start),
                **{f"p{percent}_us": values[math.ceil(percent * count / 100) - 1] / 1000
                   for percent in (50, 95, 99)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--fetch", type=int, action="append", help="override Queue_Fetch_Size")
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()

    output, build = args.output.resolve(), args.build.resolve()
    config = load_config(args.config)
    source = Path(config["hardware"]["mqsim_config_path"])
    tree = ET.parse(source)
    fetches = args.fetch or [int(tree.findtext(".//Queue_Fetch_Size"))]
    queue_depth = int(tree.findtext(".//IO_Queue_Depth"))
    require(all(1 <= fetch < queue_depth for fetch in fetches), "fetch must be below device queue depth")
    execution_budget = budget(json_file(args.budget))
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    element = library / "libndpsim_maxsim.so"
    require(sst.is_file() and element.is_file(), "build the MaxSim SST module first")
    output.mkdir()
    preflight(output, execution_budget)
    write(output / "inputs.json", dict(command=sys.argv, config_sha256=file_hash(args.config),
          xml_sha256=file_hash(source), sst_sha256=file_hash(sst), element_sha256=file_hash(element),
          backend_source_sha256=backend_digest(), seed=args.seed, window_sectors=WINDOW_SECTORS,
          benchmark_scope="single NVMe queue; deterministic page preload; no device data cache"))
    matrix = []
    for fetch in dict.fromkeys(fetches):
        for case in dict.fromkeys(args.case or CASES):
            kib, qd, count, random_lba, targets = CASES[case]
            folder = output / f"{case}-fetch{fetch}"
            folder.mkdir()
            xml = folder / "device.xml"
            tree.find(".//Queue_Fetch_Size").text = str(fetch)
            with xml.open("xb") as stream:
                tree.write(stream, encoding="utf-8", xml_declaration=True)
            step = kib * 2
            rng = random.Random(args.seed)
            requests = [(step * (rng.randrange(WINDOW_SECTORS // step) if random_lba else index), step)
                        for index in range(count)]
            manifest = folder / "requests.txt"
            manifest.write_text("ndpsim-maxsim-nvme-reads/1\n" +
                                "".join(f"{lba} {sectors}\n" for lba, sectors in requests))
            params = dict(device=str(xml), requests=str(manifest), preload_sectors=WINDOW_SECTORS, qd=qd)
            spec = folder / "run.json"
            write(spec, params)
            command = [str(sst), f"--add-lib-path={library}", str(HERE / "nvme_sst.py"), "--",
                       json.dumps(str(spec))]
            launch(command, folder, execution_budget, cwd="/tmp")
            stdout = folder / "stdout.log"
            metrics = verify(stdout, requests, qd, xml)
            result = dict(case=case, fetch=fetch, configured_unvalidated=True, **metrics,
                          targets=targets, percent_difference={name: 100 * (metrics[name] / target - 1)
                                                               for name, target in targets.items()},
                          device_sha256=file_hash(xml), requests_sha256=file_hash(manifest),
                          stdout_sha256=file_hash(stdout))
            write(folder / "metrics.json", result)
            matrix.append(result)
            print(case, fetch, round(metrics["GBps"], 3), round(metrics["IOPS"]), metrics["p50_us"], flush=True)
    write(output / "matrix.json", matrix)


if __name__ == "__main__":
    main()
