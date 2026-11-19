"""Independent metadata audit. No dependencies on the emission or sampling code."""

from itertools import zip_longest

from ndpsim.static_ir import decode, file_hash, require


def rows(path):
    with path.open("rb") as source:
        for line in source:
            yield decode(line)


def values(mapping):
    return {name: row["value"] for name, row in mapping.items()}


def edges(stage):
    return [(edge["kind"], edge["input"], edge["source"]["key"]["query"],
             edge["source"]["key"]["op"], edge["source"]["key"]["stage"], edge["source"]["port"])
            for edge in stage["dependencies"]]


def verify(directory, config):
    work = config["workload"]
    protocol = config["protocol"]
    count = work["candidate_count"]
    top_k = work["top_k"]
    corpus = work["corpus_documents"]
    doc_vectors = work["document_vectors"] + work["template_token_increment"]
    width = {"fp16": 2, "fp32": 4}[work["storage_dtype"]]
    dim = work["embedding_dim"]
    doc_bytes = doc_vectors * dim * width
    stride = 4096 * ((doc_bytes - 1) // 4096 + 1)
    io_bytes = config["layout"]["logical_read_bytes"]
    query_bytes = work["query_vectors"] * dim * protocol["query_component_bytes"]
    dots = work["query_vectors"] * dim * doc_vectors
    collection = directory / "package"
    manifest = decode((collection / "manifest.json").read_bytes())
    objects = manifest["objects"]
    require(len(objects) == 1 and objects[0]["id"] == "document-corpus" and objects[0]["version"] == 0
            and objects[0]["size_bytes"] == corpus * stride and objects[0]["shape"] == [corpus, stride],
            "corpus slot layout mismatch")

    compact = decode((directory / "logical-layout.json").read_bytes())
    require(compact["document_bytes"] == doc_bytes and compact["document_stride_bytes"] == stride
            and compact["minimum_capacity_sectors"] * 512 == corpus * stride
            and compact["corpus_allocated_bytes"] == corpus * stride
            and compact["corpus_effective_bytes"] == corpus * doc_bytes
            and compact["logical_read_bytes"] == io_bytes,
            "logical layout accounting mismatch")
    if config["layout"]["namespace_capacity_sectors"] is not None:
        external = decode((directory / "layout.json").read_bytes())
        require(external["namespace"]["capacity_sectors"] == config["layout"]["namespace_capacity_sectors"]
                and external["namespace"]["page_bytes"] == config["layout"]["namespace_page_bytes"]
                and external["alignment_bytes"] == 4096
                and external["objects"] == [{"object": "document-corpus", "version": 0, "extents": [{
                    "logical_offset_bytes": 0, "physical_offset_bytes": 0, "length_bytes": corpus * stride
                }]}], "physical layout extent or namespace mismatch")

    operations = iter(rows(collection / "operations.jsonl"))

    def stage_for(query, op_id, kind, counters, dependencies, output_sizes, rank):
        op = next(operations, None)
        require(op is not None and (op["query"], op["id"], op["operator"], op["operator_version"])
                == (query, op_id, f"maxsim.{kind}", "1"), f"query {query}: missing or unexpected {op_id}")
        require(len(op["stages"]) == 1, f"{op_id}: one stage required")
        (stage,) = op["stages"]
        require(stage["kind"] == kind and stage["id"] == "s0" and stage["topological_rank"] == rank,
                f"{op_id}: stage identity/rank")
        require(values(op["semantic_work"]) == counters and values(stage["work"]) == counters,
                f"{op_id}: Work disagreement")
        require(edges(stage) == dependencies, f"{op_id}: dependency disagreement")
        require({row["port"]: row["logical_bytes"] for row in stage["outputs"]} == output_sizes,
                f"{op_id}: output size disagreement")
        return stage

    total_requests = total_read = total_doc = total_flop = query_count = 0
    for query, (candidates, ledger) in enumerate(zip_longest(rows(directory / "candidates.jsonl"),
                                                              rows(directory / "logical-reads.jsonl"))):
        require(candidates is not None and ledger is not None and candidates["query"] == ledger["query"] == query,
                "candidate/ledger query coverage")
        doc_ids = candidates["document_ids"]
        documents = ledger["documents"]
        require(len(doc_ids) == len(set(doc_ids)) == len(documents) == count and
                all(type(doc) is int and 0 <= doc < corpus for doc in doc_ids), "candidate count, uniqueness or range")
        require(ledger["protocol"] == {"downlink_bytes": query_bytes + 8 * count, "uplink_bytes": 12 * count},
                "protocol payload disagreement")
        inputs = stage_for(query, "input", "input", {"query_bytes": query_bytes, "candidates": count}, [],
                           {"query": query_bytes, "candidates": 8 * count}, 0)
        require(not inputs["accesses"], "query input is already ready at the IR boundary")
        score_edges = []
        for index, (doc_id, doc_reads) in enumerate(zip(doc_ids, documents)):
            start = doc_id * stride
            expected_reads = []
            position, remaining = start, doc_bytes
            while remaining > 0:
                length = min(remaining, io_bytes)
                expected_reads.append({"offset_bytes": position, "valid_bytes": length,
                                       "request_bytes": 4096 * ((length - 1) // 4096 + 1)})
                position, remaining = position + length, remaining - length
            require(doc_reads == {"candidate_index": index, "document_id": doc_id, "requests": expected_reads},
                    f"query {query}, candidate {index}: logical request disagreement")
            count_reads = len(expected_reads)
            read_id, score_id = f"read-{index}", f"score-{index}"
            read = stage_for(query, read_id, "read", {"document_bytes": doc_bytes, "logical_reads": count_reads},
                             [("ADDRESS", "candidates", query, "input", "s0", "candidates")],
                             {"document": doc_bytes}, 2 * index + 1)
            expected_accesses = [{"object": "document-corpus", "version": 0,
                                  "offset_bytes": row["offset_bytes"], "length_bytes": row["valid_bytes"]}
                                 for row in expected_reads]
            require(read["accesses"] == expected_accesses, f"{read_id}: IR access disagreement")
            score = stage_for(query, score_id, "score",
                              {"document_bytes": doc_bytes, "query_bytes": query_bytes, "dot_product_flop": 2 * dots},
                              [("DATA", "query", query, "input", "s0", "query"),
                               ("DATA", "document", query, read_id, "s0", "document")],
                              {"score": 12}, 2 * index + 2)
            candidate_features = {"candidate_index": index, "document_id": doc_id}
            require(values(read["features"]) == values(score["features"]) == candidate_features
                    and not score["accesses"], f"{score_id}: candidate identity or duplicate access")
            score_edges.append(("DATA", "scores", query, score_id, "s0", "score"))
            total_requests += count_reads
            total_read += sum(row["request_bytes"] for row in expected_reads)
            total_doc += doc_bytes
            total_flop += 2 * dots
        last = stage_for(query, "topk", "topk", {"candidates": count, "top_k": top_k},
                         score_edges, {"topk": 12 * top_k, "done": 0}, 2 * count + 1)
        require(not last["accesses"], "Top-K has no document accesses")
        query_count += 1
    require(next(operations, None) is None and query_count == config["generation"]["queries"],
            "unexpected extra IR operation or missing query")
    queries = list(range(query_count))
    require(manifest["provenance"]["query_ids"] == queries and manifest["terminals"] ==
            [{"query": query, "op": "topk", "stage": "s0"} for query in queries], "terminal coverage")
    if config["load"]["mode"] == "open_loop":
        previous = 0
        seen = 0
        for query, row in enumerate(rows(directory / "arrivals.jsonl")):
            offered = row["offered_ns"]
            require(row["query"] == query and type(offered) is int and previous <= offered < 2**64,
                    "offered sequence identity, order or unit")
            previous, seen = offered, seen + 1
        require(seen == query_count, "offered sequence coverage")
    else:
        require(not (directory / "arrivals.jsonl").exists(), "closed loop must use completion-driven offers")
    return dict(format="ndpsim-maxsim-metadata-audit/1", passed=True, source="synthetic-fixture",
                scope="synthetic_structure_and_accounting",
                queries=query_count, candidates=query_count * count, document_effective_bytes=total_doc,
                all_miss_logical_requests=total_requests, all_miss_request_bytes=total_read,
                dot_product_flop=total_flop, downlink_bytes_per_query=query_bytes + 8 * count,
                uplink_bytes_per_query=12 * count, topk_bytes_per_query=12 * top_k,
                candidate_sha256=file_hash(directory / "candidates.jsonl"))
