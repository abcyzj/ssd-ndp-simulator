# Metadata capture SDK (P02)

This C++17 library records explicitly supplied operations, stages, ports, Work and
DATA/ADDRESS/CONTROL/ORDER edges. It has no SST/MQSim, Python, external JSON, or
cryptographic library build dependency. JSON serialization is local and bounded
per output record; incremental SHA256 is covered by independent Python/hashlib
vectors. Code is MIT licensed; SHA256 compression is adapted from the project's
MIT implementation, with attribution in the source and `LICENSE` retained.

```sh
cmake -S sdk -B build/capture-sdk -DNDPSIM_CAPTURE_TESTING=ON
cmake --build build/capture-sdk -j2
ctest --test-dir build/capture-sdk --output-on-failure
```

An external workload can explicitly `add_subdirectory(<sdk-source> <sdk-build>)`
and link `ndpsim::capture`. Alternatively install this project with CMake and use
`find_package(ndpsimCapture CONFIG REQUIRED)` and the same target. No root project
configuration or simulator checkout is needed. The SDK alone does not execute a
business algorithm, collect clocks automatically, or claim functional correctness.

See `tests/capture_fixture.cc` for the complete typed API example. Construct
`Recorder(new_directory, provenance, limits)`, declare operator/object metadata,
then call `record(Operation)` with every stage's explicit dependencies. Each
operation is one bounded canonical JSONL record; one operation may have multiple
stages. All records are serialized under a mutex. Physical record order may differ
from the explicitly assigned logical ranks. `close(terminals)` flushes and writes
`capture.json` only on success, binding raw stream size/hash. Exceptions poison
the writer; incomplete files remain and must not be reused as a complete capture.
Record-level type/unit/graph validation is performed by the independent sealer,
not asserted by the C++ serializer.

`MeasurementSample`, `ClockMetadata`, `SamplingPlan` and `encode_sample()` provide
shared metadata types. Negative corrected time is retained, including magnitudes
beyond int64 through a signed-magnitude field. Clock reading, sampling schedules,
overhead correction and measurement quality belong to the workload/P04/P06;
`encode_sample` does not secretly measure or advance any clock.

Closing a capture is deliberately separate from publishing an executable package:

```sh
# Once Python dependencies are installed, source-tree developer invocation:
PYTHONPATH=python python3 -m ndpsim.capture_cli seal \
  --registry examples/p02-capture/registry.json --input <capture-directory> \
  --output <new-package-directory> --report <new-report.json> --allow-fixture
PYTHONPATH=python python3 -m ndpsim.capture_cli check \
  --registry examples/p02-capture/registry.json --input <package-directory> \
  --report <new-check-report.json> --allow-fixture
```

`capture_fixture NEW_DIRECTORY REGISTRY_SHA256` is synthetic metadata, not a real
block-sum or graph capture. Obtain the registry fingerprint from Python's
`OperatorRegistry.load(...).sha256`; it hashes the canonical entire registry
contract, including size-rule descriptors and custom rule implementation hashes.
Production captures need actual source/input/build identities and a workload's
independent semantic validation. `--allow-fixture` is explicit; structure passing
always reports semantic validation as not performed. Do not rename a P01 specimen
or an old v1 package and treat it as a new capture.
