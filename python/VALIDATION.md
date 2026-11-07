# Layered evidence validation (P15)

Use an installed `ndpsim verify --spec validation.json --output new-directory`.
The command does not build, collect, fit predictions, or run benchmarks. It
checks existing evidence, runs the requested validator in a budgeted child, and
records the command, exit code, logs, hashes and resource preflight. It may
rebuild a measured table from already captured samples to verify table quality.
A successful layer report always has `full_acceptance_passed=false`.
Complete release aggregation and the twelve AC decisions belong to P21.

A spec has exactly these fields:

```json
{
  "format": "ndpsim-validation/1",
  "layer": "simulate",
  "scope": "fixture",
  "budget": {
    "output_bytes": 134217728,
    "scratch_bytes": 67108864,
    "rss_bytes": 805306368,
    "timeout_seconds": 90,
    "min_disk_free_bytes": 1048576
  },
  "limits": {"input_bytes": 134217728, "records": 100000},
  "inputs": {
    "run": {"path": "run/status.json", "sha256": "<actual SHA256>"}
  }
}
```

Paths resolve relative to the spec, including `inputs.package`. Every file
reference below has exactly `path` and `sha256`. A package uses its IR/2 identity,
not a directory checksum. CLI arguments resolve relative to the invocation cwd.
The `limits` bound the total distinct input bytes and records per JSONL file;
records are at most 16 MiB, individual JSON documents at most 64 MiB. The outer
budget bounds child time and checks RSS at exit, with disk limits polled by the
CLI. These are failure gates, not a hard memory/filesystem sandbox. This
validator retains stage metadata and trace records in memory: budget for those,
not merely the source byte count. It makes no untested full-scale RAM promise.

`scope` is `fixture`, `development` or `formal-point`. `quick`, historical
acceptance documents, unknown formats/layers and incomplete evidence fail.
Synthetic provenance requires fixture scope. A formal point additionally requires
the SIFT1M 1M×128 object shape and tuning32/evaluation128 query count; strict
accuracy requires evaluation128. This does not replace the locked query/input
checks of the producer, or accept a point as the entire formal matrix.

## Capture semantics and Work

Use `layer: "capture"` and these inputs:

```json
{
  "package": "capture/package",
  "package_sha256": "<IR/2 package identity>",
  "registry": {"path": "capture/registry.json", "sha256": "<hash>"},
  "semantic": {"path": "capture/semantic.json", "sha256": "<hash>"},
  "reference": {"path": "capture/reference.json", "sha256": "<hash>"}
}
```

First generate the independent full-query semantic/Work receipt using the
workload adapter (`ndpsim check --adapter ... --semantic-config ... --reference
...`, or a full `capture`). The layer rechecks IR schemas, package/index/tail
identity, counts, dependency rank/closure, registry identity, receipt provenance,
all-query coverage and reference identity. It preserves semantic Work and labels
reference results as native evidence. It never compares two executions of the
same functional implementation as an independent oracle.

The semantic receipt is a **trusted independent producer's evidence**; its hash
binds bytes, not truth or authorship. This layer does not reopen business payload
or claim a new semantic recomputation. A structural-only receipt fails. The
existing workload oracle remains responsible for exact graph semantics, ports,
state versions, numeric results, and Work. This boundary allows later checks to
run after payload is removed.

## Simulation scheduling and ledger

Use `layer: "simulate"` and the example's `inputs.run`. Reference the successful
`status.json` from an actual unified `simulate`, not a standalone report. The
validator verifies the run inventory (excluding derived SQLite scratch), command
exit, metadata package and effective input/configuration snapshot. It then
checks every stable `(original query ID, op, stage)` exactly once, four kinds of
dependency completion, query/terminal visibility, interval lengths, compute
slots, worker/admission quotas, drain, service source and report totals.

Profile estimates use the pure model formulas independently of the C++ service
execution. Work, features, units, domains, target/context and exact measured-table
binding/row keys are checked. Table quality and frozen prediction provenance
are revalidated with measurements in the accuracy layer; a simulation-only
check does not make a model accuracy or calibration claim.

The graph SSD checker independently derives sector coverage and page splitting
from accesses/Layout, Host/Controller effective features, indivisible L2 item
blocks and Work. It checks ordinary/local requests, NAND bytes, vector D2H,
PCIe enqueue/start/arrive and wire rounding, protocol sizes/chunk coverage,
command→compute→result visibility, buffer service, credit and final Close/drain.
It imports no C++ mapping or old validation tool. Device/PCIe/Controller remain
`configured_unvalidated`; the checker does not substitute for P16's independent
MQSim oracle or P19's full-scale dual-mapping accounting.

The report also writes `queries.json` and `stage-demands.json`. The latter
retains the logical stable key, service source/location and physical item count;
raw trace retains physical chunk IDs. Pure zero-time phase ordering cannot be
inferred from integer timestamps; the event bridge/runtime tests remain its
independent evidence.

## Native versus simulated accuracy

Use `layer: "accuracy"` and:

```json
{
  "native": [
    {"path": "benchmark-1/status.json", "sha256": "<hash>"},
    {"path": "benchmark-2/status.json", "sha256": "<hash>"},
    {"path": "benchmark-3/status.json", "sha256": "<hash>"}
  ],
  "simulated": [
    {"path": "simulation-1/status.json", "sha256": "<hash>"},
    {"path": "simulation-2/status.json", "sha256": "<hash>"}
  ],
  "capture": {"package": "...", "package_sha256": "...", "registry": {"path": "...", "sha256": "..."}, "semantic": {"path": "...", "sha256": "..."}, "reference": {"path": "...", "sha256": "..."}},
  "stage_evaluation": {"path": "evaluation-manifest.json", "sha256": "<hash>"}
}
```

Native statuses must name unprobed `benchmark`, with complete references,
configuration, build and successful commands. At least three distinct native
paths/artifacts and two distinct simulated runs are required. Both simulations
must be resident Host 1/1/1 with identical metadata/profile/target/context and
deterministic trace. Native build, semantic/input identities and all original
query IDs must match capture; paging, probes and failed commands reject.

The native median of each run's nearest-rank query latency P50/P95, batch QPS
and total thread CPU is compared with simulation, using relative error. Frozen
limits are 15%/20%/10%/15%. Latency is `visible-admitted`; QPS uses last visible
minus first admitted. Offered latency and final drain are separate. The validator
re-runs P06 `evaluate` on the explicit hash-bound evaluation manifest, checks
that the evaluated frozen prediction rules are exactly the simulated rules,
requires stage P50≤10%/P90≤20%, and rebuilds the table when present, comparing it
exactly with the simulated profile. Training isolation, missing/low-quality
samples and three independent table measurements remain P06 gates. No new
prediction model is fitted, and table stages never enter prediction error.

`query-comparison.json` joins native medians and simulated latencies by original
query ID; subdirectories retain each simulation layer report and phase quality
reports. `accuracy-errors.json` retains errors even when thresholds fail.
Generic extensions may provide `ndpsim-native-benchmark-config/1` with a `binding`
containing exact `provenance`, `target` and `context`; graph uses its native
capture configuration and cannot bypass those checks with a generic envelope.

A passing point does not claim A01+A02, capture A/B/A quality, the six-config
matrix, all twelve ACs, or hardware calibration. Failed runs, duplicate paths,
missing repeats, changed samples/tables, or quick evidence cannot generate total
acceptance. Hashes are reproducibility evidence from trusted producers, not a
proof that a renamed copy was an independent physical execution.

For Profile rules with `metadata_features`, the simulate layer independently
computes rank-based prefixes from the package and checks the traced context
before estimating service. Accuracy validation rebuilds Recipe/2 tables
using the scenario declared in the stage evaluation manifest. Use distinct
scenario evidence for sensitivity comparisons.
