"""Extend the §7.3 capacity scan when a placement fails at the E1 upper endpoint."""

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
import sys

from ndpsim.execution import identities, preflight
from ndpsim.static_ir import file_hash, require

from e1 import prewarm
from e2_sla import (COARSE, DEVICE, GIB, PLATFORM, PLACEMENTS, RUN_BUDGET, SEEDS,
                    bracket, passed, run_case, write_matrix)
from generate import write
from run import backend_digest, json_file


UPPER = (4096, 10_000)


def extend_snapshot(seed, batch, e1, output):
    group = batch / f"seed{seed}"
    bundle = group / "bundle"
    config = json_file(bundle / "effective-config.json")["config"]
    require(config["workload"]["corpus_documents"] == UPPER[-1], "unexpected E2 corpus size")
    receipt = json_file(bundle / "receipt.json")
    require(receipt["passed"] and receipt["inputs"]["override_config"]["sha256"] == file_hash(DEVICE),
            "extended scan requires a verified 9550 PRO bundle")
    old = json_file(e1 / f"zipf-0.8-seed{seed}/warmup.json")
    windows = len(old["window_hit_rates"][str(COARSE[-1])])
    snapshot = prewarm(config, (COARSE[-1], *UPPER), old["window_queries"], windows)
    require(snapshot["candidate_seed"] == old["candidate_seed"] and
            snapshot["window_hit_rates"][COARSE[-1]] == old["window_hit_rates"][str(COARSE[-1])],
            "extension must replay the same E1 prewarm windows")
    largest = snapshot["oldest_first"][UPPER[-1]]
    batch_cache = json_file(group / "warmup.json")["oldest_first"]
    require(largest[-COARSE[-1]:] == batch_cache and snapshot["oldest_first"][COARSE[-1]] == batch_cache and
            all(snapshot["oldest_first"][size] == largest[-size:] for size in UPPER),
            "extended LRU snapshot differs from base experiment")
    write(output / f"seed{seed}-warmup.json", snapshot)
    arrivals = json_file(group / "arrivals.json")["offsets_ns"]
    doc_bytes = json_file(bundle / "effective-config.json")["derived"]["document_bytes"]
    return group, arrivals, largest, doc_bytes, json_file(PLATFORM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True, help="E2 SLA batch with sealed input bundles")
    parser.add_argument("--failed-point", type=Path, required=True,
                        help="SLA result for a failed seed at the upper E1 endpoint")
    parser.add_argument("--placement", required=True, choices=PLACEMENTS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--e1", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    output, batch, e1, build, fail_file = (p.resolve() for p in
                                           (args.output, args.batch, args.e1, args.build, args.failed_point))
    manifest = json_file(batch / "experiment.json")
    failing = json_file(fail_file)
    require(batch in fail_file.parents and failing["placement"] == args.placement and
            failing["documents"] == COARSE[-1] and not failing["passed"], "invalid lower capacity failure")
    source = backend_digest()
    sst = Path(json_file(build / "sst-paths.json")["sst"])
    library = build / "workloads/maxsim"
    require(source == manifest["backend_source_sha256"] and file_hash(sst) == manifest["sst_sha256"] and
            file_hash(library / "libndpsim_maxsim.so") == manifest["element_sha256"] and
            file_hash(e1 / "sweep.json") == manifest["e1_sweep_sha256"], "base experiment identity mismatch")
    output.mkdir()
    resources = preflight(output, RUN_BUDGET)
    require(3 * GIB <= resources["available_ram_estimate_bytes"], "extension RAM budget")
    write(output / "extension.json", dict(command=sys.argv, placement=args.placement, coarse_upper=UPPER,
                                           base_manifest=str(batch / "experiment.json"),
                                           base_sha256=file_hash(batch / "experiment.json"),
                                           extension_source_sha256=file_hash(Path(__file__)),
                                           lower_failure=str(fail_file), lower_failure_sha256=file_hash(fail_file),
                                           hardware_status=manifest["hardware_status"]))
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(extend_snapshot, seed, batch, e1, output) for seed in SEEDS]
        inputs = dict(zip(SEEDS, (future.result() for future in futures)))
    cases = output / "cases"
    cases.mkdir()
    points = {COARSE[-1]: [failing]}

    def probe(pool, documents):
        jobs = [pool.submit(run_case, source, sst, library, inputs, cases, args.placement, documents, seed)
                for seed in SEEDS]
        points[documents] = [job.result() for job in jobs]
        write_matrix(output / "matrix.csv", [row for size, rows in points.items()
                                             if size != COARSE[-1] for row in rows])
        return passed(points[documents])

    with ThreadPoolExecutor(max_workers=3) as pool:
        for documents in UPPER:
            if probe(pool, documents):
                break
        low, high = bracket(points)
        while high is not None and high - low > 1:
            probe(pool, (low + high) // 2)
            low, high = bracket(points)
    write(output / "summary.json", dict(placement=args.placement, minimum_passing_documents=high,
                                        adjacent_failing_documents=low, minimum_passing_runs=points.get(high),
                                        tested_passing_documents=sorted(size for size, rows in points.items() if passed(rows)),
                                        tested_failing_documents=sorted(size for size, rows in points.items() if not passed(rows)),
                                        tested_range_documents=[min(points), max(points)]))
    write(output / "sha256.json", identities(output))


if __name__ == "__main__":
    main()
