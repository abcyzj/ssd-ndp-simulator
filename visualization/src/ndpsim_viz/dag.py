"""Build a single-query, self-contained interactive DAG document."""
from html import escape
from importlib.resources import files
import json
from pathlib import Path

from .reader import InputError


def _browser_values(value):
    if type(value) is int and abs(value) >= 2**53:
        return str(value)
    if isinstance(value, dict):
        return {key: _browser_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_values(item) for item in value]
    return value


def dag_data(package, query_id, runs=()):
    """Create a browser-ready graph from a Package and labeled (name, Run) pairs."""
    operations = package.query(query_id)
    query = str(query_id)
    timings = []
    for label, run in runs:
        if run.report["package_sha256"] != package.identity:
            raise InputError(f"{label}: run package does not match DAG package")
        timings.append((label, run.stage_timings(query_id)))

    nodes = []
    edges = []
    stage_ids = {}
    for index, operation in enumerate(operations):
        op = operation["id"]
        op_id = f"op-{index}"
        operator = operation["operator"]
        nodes.append(dict(id=op_id, label=f"{op} · {operator}", op=op, operator=operator,
                          work=operation.get("semantic_work", {}), stageCount=len(operation["stages"])))
        for stage_index, stage in enumerate(operation["stages"]):
            name = stage["id"]
            stage_id = f"{op_id}-stage-{stage_index}"
            stage_ids[op, name] = stage_id
            timing_rows = []
            for label, timing in timings:
                row = timing[query_id, op, name]
                timing_rows.append({
                    "run": label,
                    "status": row["status"],
                    "submitted_ns": None if row["submitted_ns"] is None else str(row["submitted_ns"]),
                    "completed_ns": None if row["completed_ns"] is None else str(row["completed_ns"]),
                })
            nodes.append(dict(id=stage_id, label=f"{op} · {name}", op=op, operator=operator,
                              group=op_id, kind=stage.get("kind"), key=dict(query=query, op=op, stage=name),
                              work=stage.get("work", {}), features=stage.get("features", {}),
                              timings=timing_rows))

    for operation in operations:
        op = operation["id"]
        for stage in operation["stages"]:
            target = stage["id"]
            for dependency in stage["dependencies"]:
                source = dependency["source"]["key"]
                edges.append(dict(
                    source=stage_ids[source["op"], source["stage"]], target=stage_ids[op, target],
                    kind=dependency["kind"], sourcePort=dependency["source"]["port"],
                    targetPort=dependency["input"],
                ))

    return _browser_values(dict(query=query, package=package.identity, nodes=nodes, edges=edges))


def write_dag(path, package, query_id, runs=()):
    """Write the offline page using build artifacts bundled with the Python tool."""
    assets = files("ndpsim_viz").joinpath("assets")
    data = json.dumps(dag_data(package, query_id, runs), ensure_ascii=True, separators=(",", ":"))
    # Prevent captured text from terminating the JSON script element.
    data = data.replace("<", "\\u003c")
    template = assets.joinpath("dag.html").read_text(encoding="utf-8")
    bundle = assets.joinpath("dag.js").read_text(encoding="utf-8")
    licenses = escape(assets.joinpath("THIRD-PARTY-LICENSES.txt").read_text(encoding="utf-8"))
    head, marker, tail = template.partition("<!-- VIZ_PAYLOAD -->")
    if not marker:
        raise RuntimeError("DAG template is missing its payload marker")
    Path(path).write_text(
        head + f'<script id="graph-data" type="application/json">{data}</script>\n'
        + f"<script>{bundle}</script>\n"
        + f"<template id=\"third-party-licenses\">{licenses}</template>\n" + tail,
        encoding="utf-8",
    )
