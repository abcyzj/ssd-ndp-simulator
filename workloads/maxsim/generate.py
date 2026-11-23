"""Generate or check synthetic metadata for the V0.4.5 static IR SDK."""

import argparse
import copy
import platform
from pathlib import Path
import subprocess
import sys

from ndpsim.layout import packed_layout
from ndpsim.static_ir import Limits, PackageWriter, canonical, decode, digest_bytes, file_hash, require, validate_package

from config import MAIN, ROOT, SOURCES, derive, load_config, preflight
from ir import SEMANTICS, corpus_object, key, operations, registry, requests
from sampling import ALGORITHMS, Candidates, arrivals
from scan import definition, points
from verify import rows, verify

FORMAT = "ndpsim-maxsim-generation/1"
RECEIPT = "receipt.json"
SOURCE_FILES = ("config.py", "generate.py", "ir.py", "sampling.py", "scan.py", "verify.py")


def write(path, value):
    with path.open("xb") as output:
        output.write(canonical(value))


def write_rows(path, records):
    with path.open("xb") as output:
        for row in records:
            output.write(canonical(row))


def configuration_record(config, derived, budget):
    hardware = config["hardware"]
    missing = [key for key in ("mqsim_config_path", "mqsim_config_hash", "backend_commit") if hardware[key] is None]
    sources = {f"{section}.{key}": SOURCES.get(f"{section}.{key}", "scenario_assumption_or_operator_input")
               for section, values in config.items() for key in values}
    sources["derived"] = SOURCES["derived"]
    if config["cache"].get("host_capacity_fraction") is not None:
        sources["cache.host_capacity_bytes"] = SOURCES["derived"]
    load = config["load"]
    offer = "completion_driven" if load["mode"] == "closed_loop" else "arrivals.jsonl"
    return dict(format=FORMAT, config=config, derived=derived, estimated_resources=budget,
                parameter_sources=sources, algorithms=ALGORITHMS, offer_source=offer,
                hardware_fields_pending=missing, hardware_validation="not_performed", scope="reranking_only")


def provenance(config, derived, corpus, candidates, operators):
    work, proto = config["workload"], config["protocol"]
    semantic = dict(dim=work["embedding_dim"], document_vectors=derived["document_vectors_effective"],
                    query_vectors=work["query_vectors"], top_k=work["top_k"], dtype=work["storage_dtype"],
                    query_component_bytes=proto["query_component_bytes"], scoring=SEMANTICS,
                    logical_read_bytes=config["layout"]["logical_read_bytes"])
    source_hash = digest_bytes(canonical({name: file_hash(ROOT / name) for name in SOURCE_FILES}))
    return dict(kind="synthetic-fixture", split="fixture", workload="maxsim",
                semantic_version=SEMANTICS, work_version="dot-product-flop-and-bytes/1", numeric_version="metadata-only/1",
                semantic_config_sha256=digest_bytes(canonical(semantic)), extension_sha256=operators.sha256,
                input_content_sha256=digest_bytes(canonical([corpus["content_sha256"], file_hash(candidates)])),
                capture_build=dict(source_sha256=source_hash, binary_sha256=file_hash(Path(sys.executable)),
                                   compiler=f"{platform.python_implementation()} {platform.python_version()}", flags=[]),
                query_ids=list(range(config["generation"]["queries"])))


def ledger_row(config, derived, row):
    documents = [dict(candidate_index=index, document_id=doc_id, requests=list(requests(config, derived, doc_id)))
                 for index, doc_id in enumerate(row["document_ids"])]
    return dict(query=row["query"], documents=documents, protocol=dict(downlink_bytes=derived["query_downlink_bytes"],
                                                                       uplink_bytes=derived["query_uplink_bytes"]))


def artifacts(output):
    return sorted(path.relative_to(output).as_posix() for path in output.rglob("*")
                  if path.is_file() and path.name != RECEIPT)


def sha_map(output):
    return {name: file_hash(output / name) for name in artifacts(output)}


def git_head():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def generate(config, output, command, *, override_path=None):
    derived = derive(config)
    budget = preflight(config, derived, output)
    require(not output.exists() and not output.is_symlink(), "output already exists")
    pending = output.with_name(output.name + ".pending")
    pending.mkdir()
    try:
        receipt = build_bundle(config, pending, command, override_path, derived, budget)
        require(not output.exists() and not output.is_symlink(), "output appeared during generation")
        pending.rename(output)
        return receipt
    except BaseException:
        # A complete SDK package may precede the independent audit. Only a
        # successful audit can publish the requested output directory.
        receipt_path = pending / RECEIPT
        if receipt_path.exists():
            receipt_path.unlink()
        failed = output.with_name(output.name + ".failed")
        if not failed.exists() and not failed.is_symlink():
            pending.rename(failed)
        raise


def build_bundle(config, output, command, override_path, derived, budget):
    config_record = configuration_record(config, derived, budget)
    write(output / "effective-config.json", config_record)
    write(output / "scan-definition.json", definition())
    operators = registry()
    write(output / "operators.json", operators.descriptor)
    corpus = corpus_object(config, derived)
    write(output / "logical-layout.json", dict(format="ndpsim-maxsim-logical-layout/1",
                                               corpus_documents=config["workload"]["corpus_documents"],
                                               alignment_bytes=config["layout"]["alignment_bytes"],
                                               logical_read_bytes=config["layout"]["logical_read_bytes"],
                                               layout_seed=config["generation"]["layout_seed"],
                                               rank_to_document=(ALGORITHMS["layout"] if
                                                                 config["workload"]["randomize_rank_to_document"]
                                                                 else "identity"),
                                               **{name: derived[name] for name in
                                                  ("document_bytes", "document_stride_bytes", "corpus_allocated_bytes",
                                                   "corpus_effective_bytes", "minimum_capacity_sectors")}))
    layout = config["layout"]
    if layout["namespace_capacity_sectors"] is not None:
        write(output / "layout.json", packed_layout([corpus], alignment_bytes=layout["alignment_bytes"],
                                                    capacity_sectors=layout["namespace_capacity_sectors"],
                                                    page_bytes=layout["namespace_page_bytes"]))

    candidate_path = output / "candidates.jsonl"
    sampling = Candidates(config)
    write_rows(candidate_path, (dict(query=query, document_ids=sampling.draw())
                                for query in range(config["generation"]["queries"])))
    del sampling
    if config["load"]["mode"] == "open_loop":
        write_rows(output / "arrivals.jsonl", arrivals(config))

    source = provenance(config, derived, corpus, candidate_path, operators)
    generation = config["generation"]
    limits = Limits(total_bytes=generation["package_budget_bytes"], scratch_bytes=generation["scratch_budget_bytes"])
    terminals = []
    with PackageWriter(output / "package", source, operators, allow_fixture=True, limits=limits) as writer:
        writer.declare_object(corpus)
        with (output / "logical-reads.jsonl").open("xb") as ledger:
            for row in rows(candidate_path):
                logical_reads = ledger_row(config, derived, row)
                ledger.write(canonical(logical_reads))
                identity = digest_bytes(canonical([row, source["semantic_config_sha256"], corpus["content_sha256"]]))
                for op in operations(row, logical_reads, config, derived, identity):
                    writer.record(op)
                terminals.append(key(row["query"], "topk"))
        structural = writer.seal(terminals)

    audit = verify(output, config)
    write(output / "structure.json", structural)
    write(output / "accounting.json", audit)
    inputs = {"default_config_sha256": file_hash(MAIN),
              "expanded_config_sha256": digest_bytes(canonical(config))}
    if override_path is not None:
        inputs["override_config"] = {"path": str(override_path.resolve()), "sha256": file_hash(override_path)}
    receipt = dict(format=FORMAT, passed=True, exit_code=0, command=command, cwd=str(Path.cwd()),
                   inputs=inputs, repository_commit=git_head(),
                   generator_source_sha256=source["capture_build"]["source_sha256"],
                   source="synthetic-fixture", package_sha256=structural["package_sha256"], artifacts=sha_map(output))
    write(output / RECEIPT, receipt)
    return receipt


def check(directory):
    receipt = decode((directory / RECEIPT).read_bytes())
    require(receipt.get("format") == FORMAT and receipt.get("passed") is True, "missing successful generation receipt")
    require(receipt["artifacts"] == sha_map(directory), "artifact identity mismatch")
    config = decode((directory / "effective-config.json").read_bytes())["config"]
    generation = config["generation"]
    operators = registry(directory / "operators.json")
    limits = Limits(total_bytes=generation["package_budget_bytes"], scratch_bytes=generation["scratch_budget_bytes"])
    structure = validate_package(directory / "package", operators, allow_fixture=True, limits=limits,
                                 expected_package_sha256=receipt["package_sha256"])
    audit = verify(directory, config)
    require(audit == decode((directory / "accounting.json").read_bytes()), "accounting report mismatch")
    return dict(package_sha256=structure["package_sha256"], structural_passed=True, accounting=audit)


def config_for(args):
    variants = dict(points())
    override = variants.get(args.point)
    require(args.point is None or override is not None, f"unknown scan point: {args.point}")
    override = copy.deepcopy(override or {})
    if args.queries is not None:
        override.setdefault("generation", {})["queries"] = args.queries
    return load_config(args.config, override, args.seed)


def main(argv=None):
    command = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    for action in ("generate", "describe"):
        entry = subs.add_parser(action)
        entry.add_argument("--config", type=Path, help=f"JSON override of {MAIN.name}")
        entry.add_argument("--point", help="point ID from the scan command")
        entry.add_argument("--queries", type=int, help="generated query count")
        entry.add_argument("--seed", type=int, help="group seed for the three independent streams")
        if action == "generate":
            entry.add_argument("--output", type=Path, required=True, help="new output directory")
    scanner = subs.add_parser("scan")
    scanner.add_argument("--batch", choices=list(definition()))
    scanner.add_argument("--config", type=Path)
    checker = subs.add_parser("check")
    checker.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(command)
    try:
        if args.action == "check":
            result = check(args.input)
        elif args.action == "scan":
            result = []
            for point, override in points():
                if args.batch is None or point.startswith(args.batch + "-"):
                    config = load_config(args.config, override)
                    result.append(dict(id=point, override=override, derived=derive(config),
                                       host_capacity_bytes=config["cache"]["host_capacity_bytes"],
                                       seed_set=config["experiment"]["seed_set"]))
        else:
            config = config_for(args)
            if args.action == "describe":
                derived = derive(config)
                result = configuration_record(config, derived, preflight(config, derived, None))
            else:
                result = generate(config, args.output.absolute(),
                                  [sys.executable, str(Path(__file__).resolve()), *command],
                                  override_path=args.config)
        sys.stdout.buffer.write(canonical(result))
    except (KeyError, OSError, ValueError) as error:
        print(f"maxsim: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
