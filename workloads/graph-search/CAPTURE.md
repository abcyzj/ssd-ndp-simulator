# Graph IR/2 capture and independent validation (P04)

The graph workload builds independently with an explicit C++ Capture SDK. Python
capture tools require the separately installed `ndpsim-tools` package (including
`jsonschema` and `referencing`). No SST, MQSim, historical tools or results are
required. Python imports come from the selected installation or `PYTHONPATH`.

```sh
cmake -S workloads/graph-search -B build/graph -DNDPSIM_SDK_ROOT="$PWD/sdk"
cmake --build build/graph -j2
# For development, select the Python metadata SDK explicitly:
export PYTHONPATH="$PWD/python"
python3 workloads/graph-search/python/make_fixture.py --output /tmp/graph-input-new
python3 workloads/graph-search/python/graph_capture.py capture \
  --binary build/graph/ndpsim_native --config /tmp/graph-input-new/config.json \
  --operators extensions/graph/operators.json --output /tmp/graph-capture-new
python3 workloads/graph-search/python/graph_capture.py sample \
  --binary build/graph/ndpsim_native --config /tmp/graph-input-new/config.json \
  --operators extensions/graph/operators.json --package /tmp/graph-capture-new/package \
  --stride 1 --output /tmp/graph-sample-new
python3 workloads/graph-search/python/graph_capture.py benchmark \
  --binary build/graph/ndpsim_native --config /tmp/graph-input-new/config.json \
  --operators extensions/graph/operators.json --output /tmp/graph-benchmark-new
python3 workloads/graph-search/python/graph_capture.py check \
  --config /tmp/graph-input-new/config.json --operators extensions/graph/operators.json \
  --package /tmp/graph-capture-new/package --reference /tmp/graph-capture-new/reference.json \
  --output /tmp/graph-check-new
```

Use new output directories every time. Failures retain logs/raw records and a failed
status; a package failing semantic/resource checks is renamed `package.failed`.
A structural package alone does not establish semantic correctness. The independent
semantic report is external, binds the package ID and reference hash, and must be
required by later simulation entry points. Generic SDK reports still say semantic
validation was not performed. The graph report does not grant full AC acceptance.

`make_fixture.py` creates deterministic **synthetic** inputs, not SIFT1M or a second
formal workload. `--pilot` creates N=1024, d=128, Q=32, R=16, L/E=128/256, B=1,
chunk=16; it remains a fixture. Full-scale original graph recovery and acceptance
belong to later tasks. `split=fixture` is explicit permission for fixture packages.
`tuning32`/`evaluation128` require the exact query count and an explicit
`query_selection: {path, sha256}` reference to the locked SIFT query-selection file;
the original IDs/order and disjoint splits are checked. Payload hashes, shapes and
search parameters must be correct. File and selection paths resolve relative to the
configuration file. An effective config snapshot is retained outside the package.

The new native modes are `graph_benchmark`, `graph_uninstrumented`, `graph_capture`
and `graph_sample`. They share one actual search implementation. The Python entry
point invokes them with explicit registry/binary/config identities, resident Host
worker/admission=1/1, and resource budgets. It does not build or run acceptance.
Legacy `static_capture` and `cpu_sample` remain migration references and cannot be
sealed as graph IR/2. Legacy detail flags are rejected by the new native modes.

Capture records every query, semantic operation and stage using the C++ SDK.
`SCORE_L2` has ordered prepare/L2/append stages for every semantic chunk. Segment
numbers are contiguous within the semantic operation. Per-item identities and order
are represented by the prepare-stage vector accesses, dimensions and the registered
`graph-l2-items/1` rule; each item is one complete vector. L2 arithmetic/bytes are
independently reconstructed. State marks are zero-service intents: physical insertions
are charged exactly once to the corresponding resident preparation stages. Explicit
ORDER paths prevent consumers from bypassing those stages. JOIN describes a zero-copy
control concatenation; capture hashes are observation overhead, not simulated work.

Service samples use the same stable query/op/stage/chunk/segment keys as capture.
Sampling stride starts at zero **per original query and stage kind**. Mark/JOIN tokens
are explicit zero-service stages, excluded from CPU sampling eligibility. Empty
terminal SELECT still has a sample. Prepare/L2 boundaries are contiguous; append and
vector/score destruction begin at the L2 boundary. Query-held data/state destruction
is included in FINISH; selected/fresh containers are freed in declared service scopes.
The outer score audit is not an additional charged service. New sampling positions
and source identities invalidate old measurements/models.

`measurement.json` conforms to Measurement/2. Empty-probe overhead is floor(ns/count)
per call; corrected values retain negative durations. Low signal is corrected_ns <
clock resolution; these samples remain visible and cannot be treated as fit/table
eligible. The quality report separates complete coverage from formal quality, which
is not granted here. No interval is filled with QPS or whole-query residuals. The
reported sample-run query CPU includes instrumentation and is not an independent
benchmark or a sum of predicted service demands. P06 must freeze fitting/aggregation
rules; P18 must run the formal repeated schedule.

The independent Python checker opens original payloads only on the native side. It
uses bounded positional payload reads, fixed-order binary64 L2, sets/sorting and rank/inversion formulas to
reconstruct top-k, Recall, counters, every stage Work/feature, digest, logical access,
state version and dependency. It never calls the C++ search or historical replay.
Operations are joined by stable keys in a disk-backed SQLite index, so legal physical
record reorder is accepted. Missing, extra or rehashed semantic mutations fail.

Budgets are explicit: native RSS, Python postprocessing RSS, total output bytes,
per-query native output bytes, timeout and minimum free disk. Before execution the
launcher checks actual host RAM/pressure (independent of declared target), native
budget <=60% of physical RAM, input size and disk reserve including temporary index
allowance. Native RSS is measured by wait4, Python RSS by getrusage, then checked. The resource receipt is
retained when the Python RSS gate rejects a run.
RSS budgets are failure gates, not hard OS memory limits; postprocessing timeout is
checked at checkpoints, not a preemptive sandbox. Do not claim arbitrary-size bounded
execution. SDK per-record/index/stage limits also apply. Frequency and affinity are
reported as Unsupported/uncontrolled when not measured.

`resident_random/2` is the only captured/measured execution context here. SSD-return
and Controller-command preparation timings are unsupported by this collector;
P13 must supply compatible models or reject the mapping. They must not reuse these
resident measurements silently. No real SSD/PCIe/Controller calibration is implied.

To enable independent CMake capture tests, additionally supply:

```sh
-DNDPSIM_GRAPH_CAPTURE_TESTING=ON \
-DNDPSIM_GRAPH_PYTHON_ROOT=/absolute/python-sdk-or-installation \
-DNDPSIM_GRAPH_OPERATORS=/absolute/extensions/graph/operators.json
```

Only copy this workload, the SDK/Python package, and the explicitly supplied operator
contract for isolated reproduction. The root build also registers the same tests as
`graph_capture_v2`, preserving the old functional and resource regressions until P20.

Row reads in one expansion are explicit sibling branches from the batch mark; JOIN
requires every row. Physical record order does not add an ORDER edge between those
reads. Sampling plans also use logical ranks and stable keys, so a legally reordered
package does not change eligibility or sample matching. The postprocessor retains
only sample metadata from operations and shares already checked Work/feature objects.

`native-reference.json` preserves native stdout bytes; `reference.json` is its
canonical JSON normalization. Reference hashes in semantic/measurement reports bind
the normalized file; the status manifest separately hashes the raw native file.
Preflight records the actual measured host separately from the declared target.
For non-fixture native runs, hardware/OS/physical RAM must match the measurement
target; this does not restrict replaying a later Profile on a different simulator host.
