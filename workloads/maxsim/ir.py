"""MaxSim's placement-independent metadata DAG and output rules."""

from pathlib import Path

from ndpsim.static_ir import OperatorRegistry, canonical, decode, digest_bytes, file_hash, require

CONTEXT = "maxsim.logical/1"
STAGE = "s0"
OBJECT = "document-corpus"
SCORE_BYTES = 12
SEMANTICS = "full-maxsim/1"
RULE = "maxsim.variable-output/1"
LOCATIONS = ("host", "controller")


def port(name, data_type, many=False):
    return dict(name=name, type=f"maxsim.{data_type}", unit="B", required=True,
                cardinality="many" if many else "one")


PORTS = {
    "query": port("query", "query"),
    "candidates": port("candidates", "candidate-ids"),
    "document": port("document", "document"),
    "score": port("score", "id-score"),
    "scores": port("scores", "id-score", many=True),
    "topk": port("topk", "ranked-id-scores"),
    "done": port("done", "completion"),
}
CONTRACTS = {
    "input": ((), ("query", "candidates"),
              {"query_bytes": "B", "candidates": "count"}, {}, ("host",)),
    "read": (("candidates",), ("document",), {"document_bytes": "B", "logical_reads": "count"},
             {"candidate_index": "count", "document_id": "count"}, LOCATIONS),
    "score": (("query", "document"), ("score",), {"document_bytes": "B", "query_bytes": "B",
                                                  "dot_product_flop": "flop"},
              {"candidate_index": "count", "document_id": "count"}, LOCATIONS),
    "topk": (("scores",), ("topk", "done"), {"candidates": "count", "top_k": "count"}, {}, ("host",)),
}


def sizes(stage):
    work = {name: row["value"] for name, row in stage["work"].items()}
    kind = stage["kind"]
    if kind == "input":
        return {"query": work["query_bytes"], "candidates": 8 * work["candidates"]}
    if kind == "read":
        return {"document": work["document_bytes"]}
    if kind == "score":
        return {"score": SCORE_BYTES}
    if kind == "topk":
        return {"topk": SCORE_BYTES * work["top_k"], "done": 0}
    raise ValueError(f"unknown MaxSim stage {kind}")


def registry(path=None):
    rule_hash = file_hash(Path(__file__))
    operators = []
    for kind, (inputs, outputs, work, features, locations) in CONTRACTS.items():
        spec = dict(kind=kind, inputs=[PORTS[name] for name in inputs], outputs=[PORTS[name] for name in outputs],
                    work_units=work, feature_units=features, contexts=[CONTEXT],
                    decomposition_rules=["none/1"], output_size_rule=RULE)
        operators.append(dict(id=f"maxsim.{kind}", version="1", locations=list(locations),
                              semantic_validator="maxsim.metadata-audit/1", stages=[spec]))
    descriptor = dict(format="ndpsim-operator-registry/1", units=["B", "count", "flop"], operators=operators,
                      rules=[dict(id=RULE, kind="registered-metadata/1", implementation_sha256=rule_hash)])
    if path is not None:
        require(canonical(decode(path.read_bytes())) == canonical(descriptor),
                "operator registry descriptor or rule implementation identity mismatch")
    return OperatorRegistry(descriptor, custom_rules={RULE: sizes})


def corpus_object(config, derived):
    work = config["workload"]
    descriptor = dict(corpus_documents=work["corpus_documents"], embedding_dim=work["embedding_dim"],
                      document_vectors=derived["document_vectors_effective"], storage_dtype=work["storage_dtype"],
                      stride_bytes=derived["document_stride_bytes"])
    # The hash binds a synthetic slot descriptor. No vector payload exists.
    return dict(id=OBJECT, version=0, type="maxsim.padded-document-slots",
                shape=[work["corpus_documents"], derived["document_stride_bytes"]],
                size_bytes=derived["corpus_allocated_bytes"], content_sha256=digest_bytes(canonical(descriptor)))


def requests(config, derived, document_id):
    io = config["layout"]["logical_read_bytes"]
    alignment = config["layout"]["alignment_bytes"]
    size, start = derived["document_bytes"], derived["document_stride_bytes"] * document_id
    for relative in range(0, size, io):
        effective = min(io, size - relative)
        yield dict(offset_bytes=start + relative, valid_bytes=effective,
                   request_bytes=(effective + alignment - 1) // alignment * alignment)


def key(query, operation):
    return dict(query=query, op=operation, stage=STAGE)


def dependency(query, operation, output, target=None, kind="DATA"):
    return dict(kind=kind, input=target or output, source=dict(key=key(query, operation), port=output))


def counters(units, values):
    return {name: dict(unit=unit, value=values[name]) for name, unit in units.items()}


def operation(query, name, kind, rank, work, *, edges=(), accesses=(), features=None, identity):
    _, outputs, work_units, feature_units, _ = CONTRACTS[kind]
    stage = dict(id=STAGE, kind=kind, topological_rank=rank, segment=0, chunk=None,
                 context=CONTEXT, decomposition_rule="none/1", work=counters(work_units, work),
                 features=counters(feature_units, features or {}), dependencies=list(edges),
                 accesses=list(accesses), state_versions=[])
    sizes_by_port = sizes(stage)
    stage["outputs"] = [
        dict(port=port_id, type=PORTS[port_id]["type"], logical_bytes=sizes_by_port[port_id], version=0,
             digest=digest_bytes(canonical([identity, query, name, port_id])))
        for port_id in outputs
    ]
    return dict(id=name, operator=f"maxsim.{kind}", operator_version="1", query=query,
                semantic_work=stage["work"], stages=[stage])


def operations(candidate_row, ledger_row, config, derived, identity):
    query = candidate_row["query"]
    docs = candidate_row["document_ids"]
    yield operation(query, "input", "input", 0, {"query_bytes": derived["query_bytes"], "candidates": len(docs)},
                    identity=identity)
    scores = []
    for index, (doc_id, doc_reads) in enumerate(zip(docs, ledger_row["documents"])):
        read_id, score_id = f"read-{index}", f"score-{index}"
        features = dict(candidate_index=index, document_id=doc_id)
        reads = doc_reads["requests"]
        accesses = [dict(object=OBJECT, version=0, offset_bytes=row["offset_bytes"],
                         length_bytes=row["valid_bytes"]) for row in reads]
        yield operation(query, read_id, "read", 2 * index + 1,
                        dict(document_bytes=derived["document_bytes"], logical_reads=len(reads)),
                        edges=[dependency(query, "input", "candidates", kind="ADDRESS")],
                        accesses=accesses, features=features, identity=identity)
        yield operation(query, score_id, "score", 2 * index + 2,
                        dict(document_bytes=derived["document_bytes"], query_bytes=derived["query_bytes"],
                             dot_product_flop=derived["document_flop"]),
                        edges=[dependency(query, "input", "query"), dependency(query, read_id, "document")],
                        features=features, identity=identity)
        scores.append(dependency(query, score_id, "score", "scores"))
    yield operation(query, "topk", "topk", 2 * len(docs) + 1,
                    dict(candidates=len(docs), top_k=config["workload"]["top_k"]),
                    edges=scores, identity=identity)
