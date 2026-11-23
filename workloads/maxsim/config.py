"""Configuration and resource preflight for the metadata generator."""

import math
import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from ndpsim.static_ir import Limits, U64, decode, file_hash, require

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.json"
PRESETS = {
    "colqwen3": ("TomoroAI/tomoro-colqwen3-embed-4b", 320, 1024),
    "colqwen2.5": ("vidore/colqwen2.5-v0.2", 128, 755),
    "colpali": ("ColPali", 128, 1030),
}
DTYPES = {"fp16": 2, "fp32": 4}
SCHEMES = ["host", "host_compute_free", "device_all"]
SEEDS = ("layout_seed", "candidate_seed", "arrival_seed")
SOURCES = {
    "workload.model_reference": "public_reference",
    "workload.model_revision": "public_reference_if_supplied",
    "workload.embedding_dim": "public_reference_for_named_presets",
    "workload.document_vectors": "scenario_assumption_or_public_example_for_comparison",
    "workload.top_k": "public_example_for_default",
    "workload.rerank_hnsw_m": "application_context",
    "hardware.mqsim_config_path": "inherited_if_supplied",
    "hardware.mqsim_config_hash": "derived_from_inherited_file_if_supplied",
    "hardware.backend_commit": "inherited_if_supplied",
    "hardware.target_internal_GBps_reference_only": "analytical_reference",
    "derived": "derived",
}


def uint(value, label, minimum=0):
    require(type(value) is int and minimum <= value <= U64, f"{label}: expected uint64 >= {minimum}")
    return value


def number(value, label, minimum=0, *, exclusive=False):
    require(type(value) in (int, float) and abs(value) <= U64 and math.isfinite(value)
            and (value > minimum if exclusive else value >= minimum),
            f"{label}: expected finite number {'>' if exclusive else '>='} {minimum}")


def choice(value, allowed, label):
    require(any(type(value) is type(option) and value == option for option in allowed),
            f"{label}: unsupported value {value!r}")


def align(size, alignment):
    return (size + alignment - 1) // alignment * alignment


def merge(base, override):
    require(type(override) is dict and set(override) <= set(base), "unknown configuration section")
    for section, values in override.items():
        current = base[section]
        require(type(values) is dict and set(values) <= set(current), f"{section}: unknown configuration key")
        if section == "workload":
            if "model_preset" in values and values["model_preset"] != current["model_preset"]:
                selected = values["model_preset"]
                choice(selected, (*PRESETS, "custom"), "model_preset")
                if selected in PRESETS:
                    model, dim, length = PRESETS[selected]
                    current.update(model_reference=model, embedding_dim=dim, document_vectors=length,
                                   model_revision=None)
                else:
                    current.update(model_reference="custom", model_revision=None)
            if "storage_dtype" in values:
                choice(values["storage_dtype"], DTYPES, "storage_dtype")
                current["bytes_per_component"] = DTYPES[values["storage_dtype"]]
        if section == "cache":
            capacity_keys = {"host_capacity_bytes", "host_capacity_fraction"}
            if len(capacity_keys & values.keys()) == 1:
                current.update(dict.fromkeys(capacity_keys - values.keys()))
        current.update(values)


def load_config(path=None, override=None, seed=None):
    config = decode(MAIN.read_bytes())
    if path is not None:
        merge(config, decode(path.read_bytes()))
    if override is not None:
        merge(config, override)
    if seed is not None:
        config["generation"].update(dict.fromkeys(SEEDS, seed))
    validate(config, resolve_cache=True)
    hardware = config["hardware"]
    if hardware["mqsim_config_path"] is not None:
        path = path or MAIN
        reference = (path.resolve().parent / hardware["mqsim_config_path"]).resolve(strict=True)
        require(reference.is_file(), "MQSim configuration must be a file")
        digest = file_hash(reference)
        require(hardware["mqsim_config_hash"] in (None, digest), "MQSim configuration SHA256 mismatch")
        hardware.update(mqsim_config_path=str(reference), mqsim_config_hash=digest)
    return config


def validate(config, *, resolve_cache=False):
    exp, work, cache, layout, run, compute, protocol, hardware, load, gen = (
        config[key] for key in ("experiment", "workload", "cache", "layout", "execution",
                                "compute", "protocol", "hardware", "load", "generation")
    )
    for section, keys in (
        (exp, ("measured_queries_initial",)),
        (work, ("embedding_dim", "document_vectors", "query_vectors", "candidate_count", "top_k",
                "bytes_per_component", "corpus_documents")),
        (cache, ("warmup_window_queries", "warmup_stable_windows")),
        (run, ("read_window_documents_per_query", "global_outstanding_reads", "compute_batch_documents",
               "input_buffer_bytes_per_side")),
        (load, ("closed_loop_concurrency",)),
        (gen, ("queries", "package_budget_bytes", "scratch_budget_bytes", "sampling_memory_budget_bytes")),
    ):
        for key in keys:
            uint(section[key], key, 1)
    for section, keys in ((work, ("template_token_increment",)), (gen, SEEDS)):
        for key in keys:
            uint(section[key], key)

    fraction = cache.get("host_capacity_fraction")
    if fraction is not None:
        number(fraction, "host_capacity_fraction")
        require(fraction <= 1, "host_capacity_fraction must be within [0, 1]")
        numerator, denominator = Decimal(str(fraction)).as_integer_ratio()
        doc = uint(document_bytes(work), "document_bytes", 1)
        capacity = work["corpus_documents"] * doc * numerator // denominator
        if resolve_cache and cache["host_capacity_bytes"] is None:
            cache["host_capacity_bytes"] = capacity
        require(cache["host_capacity_bytes"] == capacity, "host cache bytes do not match the capacity fraction")
    uint(cache["host_capacity_bytes"], "host_capacity_bytes")

    for section, fixed in (
        (exp, {"scope": "reranking_only", "schemes": SCHEMES}),
        (work, {"scoring": "full_maxsim", "unique_candidates": True,
                "rerank_hnsw_m": 0, "inter_query_sampling": "independent"}),
        (cache, {"policy": "document_lru_bytes", "device_extra_capacity_bytes": 0,
                 "host_fill_on_miss": True, "cross_query_miss_coalescing": False}),
        (layout, {"alignment_bytes": 4096, "contiguous_document": True, "sort_candidates_by_lba": False}),
        (run, {"wait_for_full_batch": False, "cross_query_compute_batching": False,
               "scoring_engines_per_side": 1, "cross_query_read_arbitration": "round_robin"}),
        (compute, {"mode": "equivalent_full_maxsim", "host_compute_free_zeros_launch": True}),
        (protocol, {"return_mode": "all_candidate_scores", "document_id_bytes": 8, "score_bytes": 4}),
        (hardware, {"internal_bandwidth_mode": "simulated", "hbf_enabled": False}),
        (load, {"open_loop_arrival": "poisson"}),
    ):
        for key, expected in fixed.items():
            choice(section[key], [expected], key)
    choice(work["model_preset"], (*PRESETS, "custom"), "model_preset")
    for key, value in (("name", exp["name"]), ("model_reference", work["model_reference"])):
        require(type(value) is str and bool(value.strip()), f"{key}: expected nonempty text")
    revision = work["model_revision"]
    require(revision is None or (type(revision) is str and bool(revision.strip())), "model_revision")
    if work["model_preset"] in PRESETS:
        model, dim, length = PRESETS[work["model_preset"]]
        require((work["model_reference"], work["embedding_dim"]) == (model, dim),
                "named preset model or dimension mismatch; use model_preset=custom")
        if work["model_preset"] != "colqwen3":
            require(work["document_vectors"] == length, "comparison preset document length mismatch")

    choice(work["storage_dtype"], DTYPES, "storage_dtype")
    require(work["bytes_per_component"] == DTYPES[work["storage_dtype"]], "storage dtype/byte mismatch")
    require(work["top_k"] <= work["candidate_count"] <= work["corpus_documents"],
            "require 1 <= top_k <= candidate_count <= corpus_documents")
    choice(work["distribution"], ("uniform", "zipf"), "distribution")
    number(work["zipf_alpha"], "zipf_alpha")
    require(work["zipf_alpha"] <= 1.2, "zipf_alpha must be within the supported [0, 1.2] range")
    require(work["distribution"] != "uniform" or work["zipf_alpha"] == 0, "uniform distribution requires alpha=0")
    choice(work["randomize_rank_to_document"], (True, False), "randomize_rank_to_document")
    choice(cache["initial_state"], ("cold", "prewarmed"), "cache.initial_state")
    number(cache["warmup_max_hit_rate_delta"], "warmup_max_hit_rate_delta")
    require(cache["warmup_max_hit_rate_delta"] <= 1, "warmup_max_hit_rate_delta must be a fraction")
    choice(layout["logical_read_bytes"], (4096, 16384, 65536, 131072), "logical_read_bytes")
    choice(run["overlap_read_compute"], (True, False), "overlap_read_compute")
    choice(protocol["query_component_bytes"], (2, 4), "query_component_bytes")
    choice(load["mode"], ("closed_loop", "open_loop"), "load.mode")
    for key in ("local_memory_cost_model", "topk_cost_model"):
        choice(hardware[key], ("inherit", "explicit_upper_bound" if key.startswith("local") else "explicit_zero",
                               "inherit_or_explicit_upper_bound" if key.startswith("local") else "inherit_or_explicit_zero"), key)
    for section, keys in ((compute, ("host_effective_tflops", "device_effective_tflops")),
                          (hardware, ("external_effective_GBps", "target_internal_GBps_reference_only")),
                          (load, ("open_loop_qps",))):
        for key in keys:
            number(section[key], key, exclusive=True)
    for key in ("batch_launch_us", "offload_command_us"):
        number(compute[key], key)

    seeds = exp["seed_set"]
    require(type(seeds) is list and bool(seeds), "seed_set must be a nonempty list")
    for item in seeds:
        uint(item, "seed_set item")
    require(len(set(seeds)) == len(seeds), "duplicate seed_set item")

    capacity, page = layout["namespace_capacity_sectors"], layout["namespace_page_bytes"]
    require((capacity is None) == (page is None), "provide namespace capacity and page bytes together")
    if capacity is not None:
        uint(capacity, "namespace_capacity_sectors", 1)
        uint(capacity * 512, "namespace capacity bytes", 512)
        uint(page, "namespace_page_bytes", 512)
        require(page & (page - 1) == 0, "namespace page bytes must be a power of two")
    for key, length in (("mqsim_config_hash", 64), ("backend_commit", 40)):
        value = hardware[key]
        require(value is None or (type(value) is str and re.fullmatch(f"[0-9a-f]{{{length}}}", value)),
                f"{key}: expected lowercase hexadecimal identity")
    path = hardware["mqsim_config_path"]
    require(path is None or (type(path) is str and bool(path.strip())), "mqsim_config_path")
    require(path is not None or hardware["mqsim_config_hash"] is None, "MQSim hash requires a file path")


def document_bytes(work):
    vectors = work["document_vectors"] + work["template_token_increment"]
    return vectors * work["embedding_dim"] * work["bytes_per_component"]


def derive(config):
    work, layout, protocol = (config[key] for key in ("workload", "layout", "protocol"))
    count = work["candidate_count"]
    vectors = work["document_vectors"] + work["template_token_increment"]
    doc = document_bytes(work)
    query = work["query_vectors"] * work["embedding_dim"] * protocol["query_component_bytes"]
    stride = align(doc, layout["alignment_bytes"])
    corpus = work["corpus_documents"]
    requests = (doc + layout["logical_read_bytes"] - 1) // layout["logical_read_bytes"]
    flop = 2 * work["query_vectors"] * vectors * work["embedding_dim"]
    score = protocol["document_id_bytes"] + protocol["score_bytes"]
    result = dict(document_vectors_effective=vectors, document_bytes=doc, document_stride_bytes=stride,
                  corpus_effective_bytes=corpus * doc, corpus_allocated_bytes=corpus * stride,
                  minimum_capacity_sectors=corpus * stride // 512, query_bytes=query,
                  document_flop=flop, query_flop=count * flop,
                  query_document_bytes=count * doc, query_all_miss_read_bytes=count * stride,
                  logical_reads_per_document=requests, query_all_miss_reads=count * requests,
                  query_downlink_bytes=query + count * protocol["document_id_bytes"],
                  query_uplink_bytes=count * score, topk_bytes=work["top_k"] * score)
    for key, value in result.items():
        uint(value, key, 1)
    require(doc <= config["execution"]["input_buffer_bytes_per_side"],
            "input buffer cannot hold one complete document")
    cap = layout["namespace_capacity_sectors"]
    require(cap is None or result["minimum_capacity_sectors"] <= cap,
            f"namespace capacity needs {result['minimum_capacity_sectors']} sectors, supplied {cap}")
    return result


def preflight(config, derived, output):
    work, gen = config["workload"], config["generation"]
    queries, candidates = gen["queries"], work["candidate_count"]
    reads = candidates * derived["logical_reads_per_document"]
    # Estimates for canonical JSON, indexes and SQLite metadata, plus hard SDK
    # limits checked by the writer. Budget the largest operation separately.
    stages = 2 * candidates + 2
    operation_bytes = max(4096 + 256 * derived["logical_reads_per_document"], 4096 + 384 * candidates)
    index_bytes = 4096 + 1600 * stages + 384 * candidates
    package = queries * (index_bytes + 4096 * stages + 256 * reads + 128) + 4096
    scratch = package * 2
    sidecars = queries * (4096 + 256 * candidates + 256 * reads)
    zipf = work["distribution"] == "zipf" and work["zipf_alpha"] > 0
    sampling = (8 * work["corpus_documents"] * (work["randomize_rank_to_document"] + zipf)
                + 512 * candidates + 256 * queries + 2048 * stages + 512 * reads)
    limits = Limits(total_bytes=gen["package_budget_bytes"], scratch_bytes=gen["scratch_budget_bytes"])
    require(stages <= limits.active_stages and operation_bytes <= limits.line_bytes
            and index_bytes <= limits.query_index_bytes, "estimated IR record or per-query stage/index budget exceeded")
    require(package <= limits.total_bytes,
            f"estimated package budget exceeded: need {package} bytes, configured {limits.total_bytes}")
    require(scratch <= limits.scratch_bytes,
            f"estimated SQLite budget exceeded: need {scratch} bytes, configured {limits.scratch_bytes}")
    require(sampling <= gen["sampling_memory_budget_bytes"], f"estimated sampling memory budget exceeded: {sampling} bytes")
    if output is not None:
        target = output.parent
        while not target.exists():
            target = target.parent
        temp = Path(tempfile.gettempdir())
        target_need = package + sidecars
        if target.stat().st_dev == temp.stat().st_dev:
            require(shutil.disk_usage(target).free >= target_need + scratch, "insufficient output and SQLite free space")
        else:
            require(shutil.disk_usage(target).free >= target_need, "insufficient output free space")
            require(shutil.disk_usage(temp).free >= scratch, "insufficient SQLite temporary free space")
    return dict(queries=queries, candidates=queries * candidates, logical_reads=queries * reads,
                stages=queries * stages, package_estimate_bytes=package, sqlite_estimate_bytes=scratch,
                sidecar_estimate_bytes=sidecars, sampling_estimate_bytes=sampling)
