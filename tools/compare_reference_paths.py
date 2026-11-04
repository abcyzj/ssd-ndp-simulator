"""Check native/direct/dynamic IR against an independent ranked-expansion oracle."""
import argparse
from bisect import bisect_left, bisect_right, insort
from contextlib import ExitStack
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import struct

from ann_data import digest, write_json
from simulation_input import FileRows, load_v2

WORK = ('elements', 'flops', 'read_bytes', 'write_bytes', 'output_bytes',
        'candidate_checks', 'comparisons')
SIMULATORS = ('direct-host', 'ir-host', 'direct-controller', 'ir-controller')


def load(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def totals(rows):
    return dict(zip(WORK, map(sum, zip(*rows))))


def control_work(size, checks, comparisons=0, elements=0, read=0):
    return [elements, 0, read, size, size, checks, comparisons]


def score_work(dimensions, count):
    elements = dimensions * count
    return [elements, 3 * elements, 4 * (elements + dimensions), 12 * count, 12 * count, 0, 0]


def oracle(vectors, graph, query, search):
    """Independent set/sort search and rank-based count formulas."""
    seen, expanded, ranking = set(), set(), []
    decisions, batches, control = [], [], []
    fresh = [search['entry']]
    while True:
        if fresh:
            scores = []
            for node in fresh:
                distance = 0.0
                for a, b in zip(query, vectors[node]):
                    delta = a - b
                    distance = distance + delta * delta
                scores.append([node, distance])
            batches.append(scores)
            old = [(d, i) for i, d in ranking]
            inserted = []
            count = len(old) + len(scores)
            # Unique ID membership scan, plus the insertion sort's stopping
            # comparison for each item after the first in the sorted prefix.
            comparisons = count * (count - 1) // 2 + max(0, len(old) - 1)
            for i, d in scores:
                key = (d, i)
                smaller = bisect_right(old, key) + bisect_right(inserted, key)
                comparisons += len(old) + len(inserted) - smaller + int(smaller > 0)
                insort(inserted, key)
            ranking = sorted(ranking + scores, key=lambda s: (s[1], s[0]))
            control.append(control_work(12 * count, count, comparisons, count, 12 * count))
            seen.update(fresh)
        candidates = ranking[:search['width']]
        room = min(search['batch'], search['budget'] - len(expanded))
        chosen = [i for i, _ in candidates if i not in expanded][:room]
        decisions.append((candidates, chosen))
        control.append(control_work(4 * len(chosen), len(candidates)))
        if not chosen:
            return decisions, batches, control, ranking[:search['k']]
        expanded.update(chosen)
        edges = [i for node in chosen for i in graph[node]]
        ordered_seen = sorted(seen)
        fresh, comparisons = [], 0
        for node in edges:
            if node in seen:
                comparisons += bisect_left(ordered_seen, node) + 1
            else:
                pos = bisect_left(fresh, node)
                comparisons += len(seen) + min(pos + 1, len(fresh)) + int(pos < len(fresh))
                if pos == len(fresh) or fresh[pos] != node:
                    fresh.insert(pos, node)
        control.append(control_work(4 * len(fresh), len(edges), comparisons))


def chunks(batches, size):
    return [b[i:i+size] for b in batches for i in range(0, len(b), size)]


def score_pairs(rows):
    return [[r['id'], r['distance']] for r in rows]


def check_scores(actual, expected, where, cross_toolchain):
    require(len(actual) == len(expected), f'{where}: score count')
    for j, ((i, d), (ref_id, ref)) in enumerate(zip(actual, expected)):
        valid = math.isfinite(d) and (abs(d-ref) <= 1e-9 + 1e-7*abs(ref)
                                      if cross_toolchain else d == ref)
        require(i == ref_id and valid, f'{where}[{j}]: {(i, d)} != {(ref_id, ref)}')


def check_chunks(actual, expected, where, cross_toolchain):
    require(len(actual) == len(expected), f'{where}: chunk count')
    for i, (a, e) in enumerate(zip(actual, expected)):
        check_scores(a, e, f'{where} chunk {i}', cross_toolchain)


def check_sim(events, result, expected, score_chunks, dimensions, placement, where, cross_toolchain):
    decisions, batches, control, top = expected
    by = {kind: [] for kind in ('decision', 'read', 'score', 'control')}
    for row in events:
        by[row['type']].append(row)
    require(len(by['decision']) == len(decisions), f'{where}: decision count')
    for n, (row, (candidates, chosen)) in enumerate(zip(by['decision'], decisions)):
        require(row['expand'] == chosen, f'{where} batch {n}: expansions')
        check_scores(score_pairs(row['candidates']), candidates, f'{where} batch {n}', cross_toolchain)
    require(len(by['control']) == len(control), f'{where}: control service count')
    for i, (row, work) in enumerate(zip(by['control'], control)):
        require(row['work'] == work, f'{where}: control service {i} Work')
    require(all(r['kind'] in ('adjacency', 'vector') for r in by['read']), f'{where}: read kind')
    adjacency = [node for _, chosen in decisions for node in chosen]
    vectors = [node for batch in batches for node, _ in batch]
    require([r['id'] for r in by['read'] if r['kind'] == 'adjacency'] == adjacency,
            f'{where}: adjacency reads')
    require([r['id'] for r in by['read'] if r['kind'] == 'vector'] ==
            (vectors if placement == 'host' else []), f'{where}: ordinary vector reads')
    observed = by['score']
    if placement == 'controller':
        # Controller chunk numbers identify issuance order even if service overlaps.
        observed = sorted(observed, key=lambda r: r['chunk'])
        require([r['chunk'] for r in observed] == list(range(1, len(observed)+1)),
                f'{where}: controller chunk sequence')
    else:
        # The SSD may complete reads out of order. Ordinary reads above check
        # logical issuance; associate each evaluated chunk by its unique first ID.
        ordinal = {batch[0][0]: i for i, batch in enumerate(score_chunks)}
        observed = sorted(observed, key=lambda r: ordinal[r['scores'][0]['id']])
    check_chunks([score_pairs(r['scores']) for r in observed], score_chunks, where, cross_toolchain)
    expected_work = [score_work(dimensions, len(b)) for b in score_chunks]
    require([r['work'] for r in observed] == expected_work, f'{where}: ordered L2 Work')
    check_scores(score_pairs(result['top_k']), top, f'{where} top-k', cross_toolchain)
    require(result['expanded'] == len(adjacency), f'{where}: expansion total')
    return totals(control + expected_work)


def groups(stream):
    for query, rows in groupby(map(json.loads, stream), key=lambda r: r['query']):
        yield query, list(rows)


def require_artifact(path, manifest, name):
    require(digest(path/name) == manifest['artifacts_sha256'][name], f'{path}: {name} hash')


def compare(root, cross_toolchain=False):
    native = root/'native'
    n_manifest = load(native/'manifest.json')
    require(n_manifest['passed'] and n_manifest['semantic_log'] and
            n_manifest['workers'] == n_manifest['concurrency'] == 1,
            'single-worker, single-admission native diagnostic run required')
    require_artifact(native, n_manifest, 'batch.json')
    config = n_manifest['config']
    shape, paths, identity = load_v2(n_manifest['data'], config, root)
    require(identity == n_manifest['input'], 'native input identity')
    search = config['search']
    dim, count = shape['dimensions'], shape['queries']
    dataset = identity['dataset']['files']['query']
    original_ids = [i for (i,) in struct.iter_unpack('<I', (Path(n_manifest['data'])/dataset['mapping']).read_bytes())]
    native_rows = load(native/'batch.json')['queries']
    require(len(native_rows) == len(original_ids) == count, 'native query coverage')
    summary = []
    with ExitStack() as files:
        vectors = files.enter_context(FileRows(paths['vector_file'], dim))
        graph = files.enter_context(FileRows(paths['graph_file'], shape['degree']+1, adjacency=True))
        queries = files.enter_context(FileRows(paths['query_file'], dim))
        runs = {}
        elements = set()
        for label in SIMULATORS:
            path = root/label
            manifest = load(path/'manifest.json')
            elements.add(manifest['element_sha256'])
            for name in ('config.json', 'results.json', 'semantic.jsonl'):
                require_artifact(path, manifest, name)
            sim = load(path/'config.json')
            engine, placement = label.split('-')
            require(sim['search'] == search and sim['execution_engine'] == engine and
                    sim['placement'] == placement and sim['max_inflight'] == 1 and
                    manifest['input_identity'] == identity, f'{label}: configuration/input mismatch')
            rows = load(path/'results.json')['queries']
            require(len(rows) == count, f'{label}: query coverage')
            chunk = search['chunk']
            if placement == 'controller':
                c = sim['controller']
                chunk = min(chunk, (c['capacity_bytes'] - 4*dim - 64 - c['scratch_bytes']) //
                            (c['vector_sector_bound'] + 16))
            require(chunk > 0, f'{label}: chunk capacity')
            runs[label] = (rows, groups(files.enter_context((path/'semantic.jsonl').open())), chunk)
        require(len(elements) == 1, 'simulator build mismatch')
        for i, q in enumerate(queries):
            original = original_ids[i]
            where = f'original query {original}'
            expected = oracle(vectors, graph, q, search)
            decisions, batches, control, top = expected
            expansion = [chosen for _, chosen in decisions]
            expanded = sum(map(len, expansion))
            scored = sum(map(len, batches))
            native_chunks = chunks(batches, search['chunk'])
            n = native_rows[i]
            require((n['query_id'], n['original_query_id']) == (i, original), f'{where}: native identity')
            require(n['expansion_trace'] == expansion, f'{where}: native expansions')
            check_chunks(n['score_trace'], native_chunks, where+' native', cross_toolchain)
            check_scores(score_pairs(n['top_k']), top, where+' native top-k', cross_toolchain)
            native_work = totals(control + [score_work(dim, len(b)) for b in native_chunks])
            require(n['work'] == native_work and n['expanded'] == expanded and n['scored'] == scored
                    and n['score_chunks'] == len(native_chunks) and n['expansion_batches'] == len(expansion)-1
                    and n['insufficient_results'] == (len(top) < search['k']), f'{where}: native Work/terminal')
            sim_work = {}
            for label, (rows, stream, chunk) in runs.items():
                qid, observed = next(stream, (None, None))
                r = rows[i]
                require(qid == r['query'] == i+1 and r['original_query_id'] == original and
                        r['insufficient'] == (len(top) < search['k']) and
                        r['recall_at_k'] == n['recall_at_k'], f'{where} {label}: identity/terminal/Recall')
                sim_work[label] = check_sim(observed, r, expected, chunks(batches, chunk), dim,
                                            label.split('-')[1], where+' '+label, cross_toolchain)
            signature = hashlib.sha256(json.dumps(expected, separators=(',', ':')).encode()).hexdigest()
            summary.append(dict(original_query_id=original, expanded=expanded, scored=scored,
                                termination='budget' if expanded == search['budget'] else 'frontier_exhausted',
                                insufficient=len(top) < search['k'], recall_at_k=n['recall_at_k'],
                                native_work=native_work, simulation_work=sim_work, semantic_sha256=signature))
        for label, (_, stream, _) in runs.items():
            require(next(stream, None) is None, f'{label}: extra trace queries')
    return dict(passed=True, queries=summary, shape=shape, search=search, cross_toolchain=cross_toolchain)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cross-toolchain', action='store_true',
                        help='allow only distance tolerance of abs 1e-9 + rel 1e-7')
    args = parser.parse_args()
    try:
        report = compare(args.group.resolve(), args.cross_toolchain)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        report = dict(passed=False, error=str(exc))
    write_json(args.output, report)
    if not report['passed']:
        parser.exit(1, report['error']+'\n')


if __name__ == '__main__':
    main()
