# Unified CLI (P14)

Install with `python3 -m pip install ./python`; `ndpsim --help` and
`python3 -m ndpsim --help` expose the same commands. The package contains the SST
entry scripts. Installation, check and fitting require no simulator build.
Build explicitly using root CMake before simulate; nothing builds or runs
acceptance implicitly. macOS arm64 is tested; other platforms remain unverified.

```sh
ndpsim capture --workload graph --adapter workloads/graph-search/python/graph_capture.py \
  --native build/graph/ndpsim_native --operators extensions/graph/operators.json \
  --config capture.json --output new-capture
ndpsim check --package new-capture/capture/package \
  --registry new-capture/capture/registry.json \
  --adapter workloads/graph-search/python/graph_capture.py \
  --allow-fixture --report new-structural.json
ndpsim fit --manifest training.json --budget execution-budget.json --output new-fit
ndpsim simulate --config experiment.json --build build/delivery --output new-run
ndpsim compare --spec comparison.json --output new-comparison
```

Capture config and native preparation follow the independent workload's
[CAPTURE.md](../workloads/graph-search/CAPTURE.md). `--mode sample --package ...`
uses a **separately captured** package; `--mode benchmark` runs a separate native
benchmark; `--mode uninstrumented` runs the perturbation baseline. Default capture
never samples CPU. `--stride` must be 1 for capture. The adapter is an explicit
trusted independent program, not an automatically discovered plugin. Its native
and semantic reports live under `capture/`; native top-k is reference evidence.

`check` without an adapter supports generic fixed-size metadata rules. Graph
registered size rules require the explicit graph adapter and full captured
registry. Structure check reads no payload. Add `--semantic-config capture.json
--reference native-reference.json` to independently recheck graph semantics/Work;
only this explicit mode opens payload. Neither structural success nor a hash
proves business correctness. `--expected-package-sha256` binds external identity.
Synthetic packages/models require `--allow-fixture` (for simulation this flag is
in the run config). Reports must be new and outside the package.

Fit uses the [P06 manifest/recipe](FITTING.md), with `--action fit` (default),
`table`, `evaluate`, or `check`. Output is under `fit/`. `--budget` supplies the
execution budget below; the manifest's independent sample/input/quality budgets
also apply. Evaluation does not refit or publish a new profile. Predicted and
measured-table sources remain separate.

## Simulation inputs

`experiment.json` is a closed record. `run` is relative to this file:

```json
{
  "format": "ndpsim-experiment/1",
  "run": "config/run.json",
  "budget": {
    "output_bytes": 268435456,
    "scratch_bytes": 268435456,
    "rss_bytes": 536870912,
    "timeout_seconds": 120,
    "min_disk_free_bytes": 1073741824
  }
}
```

The budget object alone is also `execution-budget.json`. All fields are positive
integers; RAM and available disk are checked anew. `run.json` is the actual
[P11 resident](../docs/memory-timing-v0.4.5.md) `ndpsim-memory-run/1` or
[P13 SSD](../docs/graph-ssd-v0.4.5.md) `ndpsim-graph-ssd-run/1` configuration.
Package, registry, profile, receipts, Layout and XML resolve relative to that run
file; command-line paths resolve relative to cwd. Input `output` and `scratch`
are replaced with new paths inside the CLI output directory. No business data,
truth, legacy engine or historical results are needed by simulate. A missing
build fails with instructions to build explicitly. Layout/XML/resources can be
changed without rebuilding; the backend still validates every supported field.

Legacy direct, dynamic IR and Replay configurations must be migrated. Capture
a complete IR/2 package from the workload and supply a new run configuration
and compatible Profile. The simulator rejects legacy formats before launch.
Historical commands are documented in the [V0.4 release](../docs/releases/v0.4.md).

The new output directory contains exact input snapshots (`inputs/`, including
the metadata package), `run-input.json`, `experiment-input.json`, resolved
`effective-config.json`, `input-identities.json`, `preflight.json`, invocation,
subprocess command/exit/RSS/wall time, stdout/stderr, `trace.jsonl`, `stages.csv`,
`services.csv`, `report.json` and artifact hashes in `status.json`. Target hardware
is separate from the current execution host. Reports label predictions, tables,
reference evidence, Unsupported resources, configured_unvalidated hardware and
terminal drain. Stage elapsed includes input/return; service queue wait is
start-submitted. The raw trace retains every resource event and size layer;
protocol/PCIe/sector/NAND totals must not be summed together. No top-k is
recomputed by simulation. Unsupported metrics are explicit, never invented 0.

Disk, scratch and wall time are polled at 50ms during the child; timeout/budget
failure kills its process group and reaps it. Child peak RSS is checked at exit;
CLI report peak RSS/time and disk are checked too. These are measured limits,
not a hard RSS/process-tree or filesystem sandbox. Short bursts can overshoot;
SQLite temporary files outside the output tree and OS caches are not included.
Failed outputs and logs are retained. Diagnostic records may exceed a very small
failed budget. Keep inputs immutable during invocation. Snapshots are metadata,
not authentication against a malicious input producer or concurrent path attack.

## Comparison and acceptance boundary

Comparison spec is `{"format":"ndpsim-report-comparison/1","runs":[
{"path":"run-a/report.json","sha256":"<actual hash>"},
{"path":"run-b/report.json","sha256":"<actual hash>"}]}`. References resolve
relative to the spec; duplicate reports, hash mismatch, failed or unbound run status, incomplete runs and
package/target mismatches fail. The output compares latency, window and source
mix descriptively. It gives no accuracy or speedup verdict.

P15 implements `ndpsim verify --spec validation.json --output new-directory`
for separate capture, simulation scheduling/ledger and native-vs-sim accuracy
reports. See [VALIDATION.md](VALIDATION.md) for the three input contracts,
budgets, identities, repetitions and strict thresholds. Unknown/full-release
specs return 3; full release aggregation belongs to P21. Every command keeps
`full_acceptance_passed=false`; no quick/fixture/layer check is total acceptance.

Exit codes: 0 executed successfully within the stated scope; 2 usage/input,
budget or subprocess failure; 3 unsupported acceptance. Existing outputs are
never overwritten. Run failures get `error.json` and `status.json`; a report
written before a later budget failure is renamed `report.failed.json`.
Old public scripts remain migration-only until their removal in P20.
