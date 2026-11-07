# Independent fitting and measured service tables (P06)

Install `python/` with Python 3.9+, jsonschema and referencing as described in
[README](README.md). No C++ compiler, SST/MQSim, business payload or historical
results directory is needed. The module entry point is available now; the unified
`ndpsim fit --manifest ... --budget ... --output ...` CLI is documented in [CLI.md](CLI.md).

```sh
python -m ndpsim.fit_cli check --manifest input.json --output new-quality-directory
python -m ndpsim.fit_cli fit --manifest training.json --output new-fit-directory
python -m ndpsim.fit_cli table --manifest table.json --output new-table-directory
python -m ndpsim.fit_cli evaluate --manifest evaluation.json --output new-evaluation-directory
```

Synthetic inputs need `--allow-fixture`. Every output must be new. Failures return
2, retain diagnostic status and remove the active `profile.json` name.
When collection or fitting fails after the session opens, `failure-report.json`
retains the hash-bound inputs, completed quality checks and, where available,
the rejected stage, fold coverage and holdout errors. It has `passed=false`; it
never substitutes for a frozen fit report or authorizes a Profile.

Successful status always says `full_acceptance_passed=false`. Python callers use
`ndpsim.fitting.execute(action, manifest_path, output, allow_fixture=False)`.

## Explicit inputs

A manifest is a closed JSON record:

- `format: ndpsim-fit-manifest/1`, `fixture: bool` and full contract/2 `target`.
- `recipe: {path,sha256}`; `partitions: {tuning:[original IDs],evaluation:[IDs]}`.
  ID lists are ordered, unique uint64 and disjoint. Four-fold prediction fitting
  needs at least four tuning IDs. Fitting rejects evaluation datasets before
  opening their measurements. Table binding can use either split; evaluation
  consumes the previously frozen prediction Profile and never refits it.
- `datasets`: records with `split` (`tuning` or `evaluation`), `point` (recipe ID),
  `package:{path,sha256}`, `structural_report:{path,sha256}`,
  `semantic_config:{path,sha256}` and `runs`.
- Each run has a unique `run_id` and file references `measurement`, `reference`,
  `semantic_report`, `status`. All file references are `{path,sha256}`. Package
  `sha256` is the IR/2 package ID, not a directory or manifest-file hash.
- `budget`: positive integers `input_bytes`, `samples`, `stages`, `output_bytes`,
  `timeout_seconds`. Bytes and counts bound the whole invocation. SQLite scratch
  is capped by `input_bytes`; query/stage metadata is indexed on disk. Samples
  used for fitting remain in memory. Limits are checks, not a hard RSS sandbox or
  preemptive timeout; perform a new resource preflight before formal operations.
- `table` and `evaluate` additionally require `frozen_profile:{path,sha256}` and
  `frozen_report:{path,sha256}` pointing to a successful `fit` output. The profile
  must match its report's exact models, recipe, training IDs, target and build.

Paths resolve relative to the declaring manifest, including relative paths with
spaces, independently of cwd. The manifest's file SHA256 references bind actual
bytes; semantic config must also match provenance's canonical semantic-config
hash. For P04 graph captures export just `{"shape":...,"search":...}` from the
capture config. Neither this metadata nor fitting requires opening payload files.

The package must be complete IR/2. The fitter rechecks file set, sizes, hashes,
manifest/tail/schema/counts and typed stage metadata against recipe operator
specs. It requires an externally supplied P02 structural report bound to package,
counts and extension identity; it does not rerun business-specific output rules
or claim to re-prove the DAG/functional oracle. Semantic reports must explicitly
pass independent full-query/Work checks and bind the package, provenance, query
IDs and the particular run's reference-report hash. These are trusted producer
attestations, not cryptographic authentication of an untrusted producer.

The run status must have `passed=true`, `mode=sample` and `files_sha256` entries
for the measurement, reference and semantic report, relative to its directory.
P04 `status.json`, `reference.json`, `semantic.json` and `measurement.json` already
provide this structure. Native references supply `cpu_clock`, `cpu_resolution_ns`,
`empty_probe_ns/count`, `workers`, `concurrency`, `paging_eligible`, `major_faults`,
`completed` and per-query `original_query_id/cpu_start_ns/thread_cpu_ns`.

## Recipe and quality policy

A recipe is a closed `ndpsim-fit-recipe/1` record with `id`, contract/2 `operators`,
`rules`, `training`, `sampling_audits`, `training_points`, `formal_partitions`,
and `provenance_splits` (maps the logical tuning/evaluation roles to workload
provenance split names). The generic package contains no graph stage whitelist.

Each rule specifies `operator`, `operator_version`, `stage_kind`, `context`,
`location`, `source`, ordered `features`, `formula` and `options`. Every captured
stage has exactly one service owner. Features may reference Work or extra
counters; all extra counters need ranges. Work/features retain exact units and
uint64 values. Sources are:

- `prediction`: fixed, preselected `constant/1`, `nonnegative-linear/1`,
  `upper-bound-table/1` or `log-gbdt/1`. No automatic model search or evaluation-side
  tuning. Linear fitting uses 512 deterministic projected coordinate-descent
  passes. GBDT uses deterministic CART with recipe options `trees/depth/leaf/rate`
  (at most 180 trees/depth3); other fitters have empty options.
- `measured-table`: no prediction fitter (`formula:null`, `options:{}`). Training
  reports exclude this stage from prediction-error claims. Table publication needs
  three or more unique run IDs **and** distinct Measurement hashes for the same
  complete binding, stride1 and one qualified sample per call per run.
- `declared-zero`: explicit recipe declaration of a zero-service boundary,
  `formula:null`, empty options. It is not eligible for CPU sampling. The resulting
  constant/1 rule is tagged as a declaration in the fit report, without prediction
  accuracy claims. It does not authorize synchronous runtime completion.

`training` freezes `folds:4`, allowed `strides`, `min_runs`,
`max_low_signal_fraction`, `min_holdout_fraction`, `max_relative_p50` and
`max_relative_p90`. Quality fractions must be in [0,1], and holdout coverage must
be positive. Queries in declared tuning order map to fold index modulo4. All
calls and repetitions of an original query remain in one fold. Reports list
training/held-out IDs, domains, in-range/out-of-range coverage and errors per fold.
The final model refits all qualified tuning samples only after the holdout gate.
Evaluation requires every used feature within the frozen final domain and reports
prediction error separately from table/declaration stages.

`training_points` contains `{id,stride,parameters}`; dotted parameter keys select
exact typed values in the hash-bound semantic config. All points and their fixed
strides are mandatory for `fit`. `formal_partitions` freezes original ID lists for
non-fixture inputs. `sampling_audits` contains `{stage_kinds:[...]}` declarations
requiring complete, contiguous exclusive CPU intervals for each query/op/chunk.

Quality checks independently recompute the rank-based query/stage-kind stride
plan, sample keys, chunks/segments, Work/features, raw=end-start,
overhead=floor(empty_probe_ns/empty_probe_count), corrected=raw-overhead and
low_signal=(corrected<resolution). Single-call samples and CPU quotas1/1/1 are
supported. The source clock must be CLOCK_THREAD_CPUTIME_ID. Native query windows,
complete query coverage, no overlapping CPU buckets and zero major faults are
required. No QPS or wall-clock residual is used as a training target.

Low-signal samples are retained in counts but excluded from prediction fitting,
subject to the frozen per-run/per-stage fraction gate. Every table call must be
qualified in every run; any low-signal call rejects publication. No low-signal
window aggregation is implemented. Qualified call durations use the lower median
(`median_low`, deterministic integer ns, including even repeat counts). This
policy never turns negative/short samples into usable durations by clipping them.

## Outputs and extension boundary

`profile.json` is P05-compatible `ndpsim-cost-profile/1`; `fit-report.json` or
`table-report.json` binds sources, recipe, quality, fold errors, implementation
and Python identity. Table report hashes bind every measured rule; prediction
rules retain their original fit-report hashes. `evaluate` emits only a report.
The report and Profile are deterministic for identical inputs and tool identity;
`resolved-inputs.json` records actual paths separately. Effective manifest/recipe,
CLI argv/cwd/exit code and output hashes are retained. No report claims full AC.

`extensions/graph/fit-recipe.json` is the separate resident Host graph recipe:
all current stages and extra features, explicit state/JOIN zero service,
merge_append measured tables, and separate GBDT dedup. It freezes the existing
five L/E points (16/32 stride1, other four stride8), four original-ID folds,
1M×128/k10/B1/chunk16 and the locked tuning32/evaluation128 partitions. Resident
models do not apply to SSD-return/Controller-command preparation contexts.

This is the P06 tool/interface exit, not formal training or CPU calibration.
The original SIFT/NSG data gap, new formal repeated sampling, independent
benchmarks and all AC remain P18/P15/P21 work. Platform scheduling and unique
runtime charging must still be validated after P07/P08/P11 connect the models.

## Recipe/2

Recipe/2 can declare a finite ordered `candidates` list for a prediction
rule. Each candidate supplies `formula`, `options` and `features`. Fit
reports all four grouped folds and selects among passing candidates by P90,
then P50, then recipe order. These scores guide selection on tuning data.
A failed stage prevents Profile publication.

A prediction rule may declare `metadata_features`. Each descriptor supplies
`name`, `unit`, `rule` (`query-prefix-sum/1`), `operator`,
`operator_version`, `stage_kind` and `feature`. The value sums the
registered source feature in the same query at strictly earlier topological
ranks. Profile estimates consume the derived counters separately from raw
Work and features. The resident mapping and independent validator each
derive the counters from IR metadata.

A measured rule may declare `low_signal_policy` with
`kind: "censored-zero/1"` and a positive training-side `reference_ns`.
The manifest's `table_scenario` is `zero` by default or `reference`.
Only flagged low-signal replicas receive the scenario value before the
lower median. Coverage, binding, raw interval checks, and the recipe's
low-signal fraction gate apply to every run. Approximation metadata and
affected keys are recorded in the report. Recipe/1 retains strict rejection
of low-signal table replicas.

`ndpsim fit --action table-check` applies the same call validation and
aggregation without a frozen prediction Profile. It writes candidate table
rows for inspection. The `table` action still requires a frozen Profile
to publish a complete executable Profile.
