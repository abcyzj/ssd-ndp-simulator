# Static IR metadata tools (P02)

Install with `python3 -m pip install ./python`. The package uses Python 3.9+,
`jsonschema>=4.18,<5`, `referencing>=0.30,<1`, and standard-library SQLite/hashlib.
No C++ compiler, simulator, business payload or historical results are needed for
validation. Schemas are bundled; a regression test requires byte equality with
the frozen root schemas. The C++ SDK has no JSON/hash third-party dependency.

The development module `python3 -m ndpsim.capture_cli` exposes `seal` and `check`.
It does not install or pretend to be the unified `ndpsim` CLI planned for P14.
Every output package/report must be new. Reports are outside input/output
package directories. Errors return nonzero; failed validation reports cannot
claim semantic success. `check --expected-package-sha256 <external-hash>` binds
an independently supplied identity. The Python API also accepts independently
supplied expected provenance; otherwise the report states that identity was not
externally bound.

API: `OperatorRegistry(descriptor, custom_rules=...)`,
`PackageWriter(output, provenance, registry, allow_fixture=..., limits=...)`,
`seal_capture(source, output, registry, ...)`, and `validate_package(...)`.
Call `declare_object` before `record`; dependency, Work and stage metadata come
from the caller. Call `seal(explicit_terminals)` to validate and publish. A failed
writer is permanently unusable, even if the caller catches the exception.

A registry is external trusted input, never inferred from the package. Its JSON
contains `format=ndpsim-operator-registry/1`, declared `units`, frozen OperatorSpec
`operators`, and `rules`. Built-in `fixed-outputs/1` rules map each port to uint64
bytes. In-process extensions may register `registered-metadata/1` callbacks with
an explicit implementation SHA256. The CLI loads no arbitrary code or dynamic
plugins. Callbacks receive stage metadata only; their purity and independent
semantic verifier remain the extension's responsibility. There is no graph
operator/loop whitelist or payload lookup in this package.

Scanning reads one bounded operation/index record at a time. A temporary SQLite
index holds cross-record topology, ports, versions and file locations on disk;
the global operation list is never copied into Python memory. SQLite uses a
2MiB cache and file-backed temporary storage. Per-query stage/index limits,
16MiB deployment ceilings, total package bytes and SQLite database pages are
checked. This is not hard process-memory or all-filesystem isolation; temporary
filesystem free space still needs preflight for formal captures. The many-query
fixture measures bounded Python allocations, not full-scale feasibility.

Packages contain exactly manifest/operations/index/tail; index entries are
ordered by declared query order and logical rank, while operation records can be
physically reordered. Empty ranges remain checked. Streaming hash, schema,
registered types/units/output sizes, required/variadic ports, all dependency
kinds, immutable query-local versions, contiguous ranks, terminal closure,
counts and index duplication/coverage are checked independently. Missing or
unknown rules/operators/versions, duplicate JSON keys, nonfinite values, uint64
float spellings, symlinks/escaping file lists, truncation and budget overruns fail
closed. Input/package files must remain immutable during validation/use; hashes
and file state are rechecked. No hashes authenticate an untrusted producer.

The sealer writes `<output>.pending`, verifies the full package, then renames it
into the requested new output. Failure retains `<output>.failed` (or pending if
a prior failure directory already exists). `<output>.lock` serializes cooperating
publishers. It does not promise crash-durable fsync or defend against an external
process deliberately replacing directories during publication. Failed directories
are not reusable package inputs. Uncommitted SQLite scratch is always discarded.

A successful structural report has `structural_passed=true`,
`semantic_validation="not-performed"`, and `independent_semantic_passed=false`.
Only P04/registered workload-side validation may provide business/Work evidence;
P02 never turns payload hashes or source labels into that evidence. Production
execution, hardware compatibility, CPU fitting and sampling remain later tasks.

P06 additionally provides `python -m ndpsim.fit_cli check|fit|table|evaluate`.
See [independent fitting and measured tables](FITTING.md) for the explicit
manifest/recipe contract, grouped training, quality gates and standalone commands.
This module needs only the installed Python package and declared metadata inputs;
it has no dependency on a compiler, simulator, old stage tools or historical runs.
