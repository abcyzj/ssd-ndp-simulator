"""Run MaxSim E3–E5 using an explicit device profile and the declared scan axes."""

import argparse
import csv
from itertools import product
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

from ndpsim.execution import budget, preflight
from ndpsim.static_ir import file_hash, require

from config import derive, load_config
from e1 import MODEL_REVISION, prewarm, run_point
from generate import generate, write
from run import HERE, PLACEMENTS, backend_digest, checked_inputs, json_file
from scan import points


SCENARIOS = {"default": (.8, .0524288), "strong": (1.2, .2097152)}


def device_variant(source, output, link_rate, depth):
    tree = ET.parse(source)
    lanes = int(tree.findtext(".//PCIe_Lane_Count"))
    lane_rate = tree.find(".//PCIe_Lane_Bandwidth")
    queue_depth = tree.find(".//IO_Queue_Depth")
    if link_rate is not None:
        lane_rate.text = f"{link_rate / lanes:.12f}".rstrip("0").rstrip(".")
    if depth is not None:
        queue_depth.text = str(depth)
    require(int(tree.findtext(".//Queue_Fetch_Size")) < int(queue_depth.text),
            "device queue depth must exceed Queue_Fetch_Size")
    with output.open("xb") as stream:
        tree.write(stream, encoding="utf-8", xml_declaration=True)
    return float(lane_rate.text) * lanes


def cases(batch, rate):
    yield "baseline", {}
    for name, override in points():
        if not name.startswith(batch + "-"):
            continue
        hardware = override.get("hardware")
        if hardware:
            reference = hardware["external_effective_GBps"]
            if reference == 14:
                continue  # The input profile is the link-rate reference point.
            hardware["external_effective_GBps"] = rate / 2 if reference == 7 else reference
        yield name, override
    if batch == "E5":
        yield "device-queue-256", {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=("E3", "E4", "E5"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--point", action="append")
    parser.add_argument("--seed", type=int, action="append", required=True)
    parser.add_argument("--queries", type=int, required=True)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--warmup-window", type=int, default=10_000)
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--budget", type=Path, required=True)
    parser.add_argument("--device-limits", type=Path)
    args = parser.parse_args()

    require(args.queries > 2 * args.concurrency > 0 and args.warmup_window > 0,
            "need positive concurrency, warmup window and more than two replacement waves")
    profile = load_config(args.config)
    baseline_rate = profile["hardware"]["external_effective_GBps"]
    matrix = dict(cases(args.batch, baseline_rate))
    requested = args.point if args.point else matrix
    require(set(requested) <= matrix.keys(), "unknown scan point")
    scenarios = args.scenario or (list(SCENARIOS) if args.batch == "E3" else ["default"])
    limits = (json_file(args.device_limits) if args.device_limits else
              dict(xml_bytes=1024**2, metadata_bytes=4 * 1024**3, preload_pages=4_000_000))
    execution_budget = budget(json_file(args.budget))
    source = backend_digest()
    output, build = args.output.resolve(), args.build.resolve()
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    element = library / "libndpsim_maxsim.so"
    require(sst.is_file() and element.is_file(), "build the MaxSim SST module first")
    platform_path = HERE / "e0-run.json"
    platform = json_file(platform_path)
    output.mkdir()
    preflight(output, execution_budget)
    write(output / "sweep.json", dict(command=sys.argv, config_sha256=file_hash(args.config),
          source_sha256={p.name: file_hash(p) for p in sorted(HERE.iterdir())
                         if p.suffix in (".py", ".cc", ".h", ".json", ".xml")},
          backend_source_sha256=source, element_sha256=file_hash(element), sst_sha256=file_hash(sst),
          platform_sha256=file_hash(platform_path), device_limits=limits, scenarios=scenarios,
          matrix={name: matrix[name] for name in requested}))
    warmed = {}
    rows = []
    for name in requested:
        override = matrix[name]
        group = output / name
        group.mkdir()
        device = group / "device.xml"
        link_rate = override.get("hardware", {}).get("external_effective_GBps")
        depth = 256 if name == "device-queue-256" else None
        rate = device_variant(profile["hardware"]["mqsim_config_path"], device, link_rate, depth)
        for scenario, seed in product(scenarios, args.seed):
            folder = group / f"{scenario}-seed{seed}"
            folder.mkdir()
            alpha, capacity_fraction = SCENARIOS[scenario]
            settings = dict(workload=dict(distribution="zipf", zipf_alpha=alpha),
                            cache=dict(host_capacity_fraction=0 if args.cold else capacity_fraction),
                            load=dict(mode="closed_loop", closed_loop_concurrency=args.concurrency),
                            generation=dict(queries=args.queries))
            for section, values in override.items():
                settings.setdefault(section, {}).update(values)
            if settings["workload"].get("model_preset", profile["workload"]["model_preset"]) == "colqwen3":
                settings["workload"]["model_revision"] = MODEL_REVISION
            settings["hardware"] = dict(mqsim_config_path=str(device), mqsim_config_hash=file_hash(device),
                                        external_effective_GBps=rate)
            config = load_config(args.config, settings, seed)
            derived = derive(config)
            capacity = config["cache"]["host_capacity_bytes"]
            pages = capacity // derived["document_bytes"]
            cache_key = json.dumps([config["workload"], config["generation"], pages], sort_keys=True)
            if pages and cache_key not in warmed:
                warmed[cache_key] = prewarm(config, [pages], args.warmup_window, 8)
            snapshot = warmed[cache_key]["oldest_first"][pages] if pages else []
            write(folder / "warmup.json", warmed[cache_key] if pages else {})
            bundle = folder / "bundle"
            generate(config, bundle, sys.argv, override_path=args.config)
            _, _, provenance, _ = checked_inputs(bundle, platform_path, source)
            write(folder / "backend.json", provenance)
            device_report = run_point(folder, "device-preflight", "host", bundle, platform, source, sst,
                                      library, args.concurrency, capacity, snapshot, args.queries,
                                      device_limits=limits, preflight_only=True)
            require(device_report["metadata_estimate_bytes"] <= execution_budget["rss_bytes"],
                    "device metadata exceeds the specified RAM budget")
            for placement in PLACEMENTS:
                device_capacity, initial = (0, []) if placement == "device_all" else (capacity, snapshot)
                metrics = run_point(folder, placement, placement, bundle, platform, source, sst, library,
                                    args.concurrency, device_capacity, initial, args.queries,
                                    device_limits=limits, execution_budget=execution_budget)
                result = dict(point=name, scenario=scenario, seed=seed, link_GBps=rate,
                              device_sha256=file_hash(device), **metrics)
                rows.append(result)
                print(name, scenario, seed, placement, round(metrics["qps"], 2), flush=True)
    with (output / "matrix.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
