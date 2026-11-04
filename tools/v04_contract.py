"""V04-2a offline contracts. Validation grants no runtime access or service credit."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from functools import lru_cache

from jsonschema import Draft202012Validator, validators

StrictValidator = validators.extend(
    Draft202012Validator, type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int))

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 16 * 1024 * 1024
U64 = 2**64 - 1
WORK = ('elements', 'flops', 'read_bytes', 'write_bytes', 'output_bytes',
        'candidate_checks', 'comparisons')
OUTPUT = dict(INIT='state', MARK_SCORED='state', SCORE_L2='scores_id_f64',
              JOIN='adjacency_batch', MERGE_CANDIDATES='scores_id_f64', SELECT_FRONTIER='ids_u32',
              MARK_EXPANDED='state', READ_ADJACENCY='adjacency', DEDUP_NEIGHBORS='ids_u32',
              FINISH='scores_id_f64')
STAGES = {
    'INIT': ('init',), 'MARK_SCORED': ('state',), 'MARK_EXPANDED': ('state',),
    'SCORE_L2': ('score_prepare', 'score_l2'), 'MERGE_CANDIDATES': ('merge',),
    'SELECT_FRONTIER': ('select',), 'READ_ADJACENCY': ('adjacency_prepare',),
    'DEDUP_NEIGHBORS': ('dedup',), 'FINISH': ('finish',),
}
FEATURE_UNITS = dict.fromkeys(WORK + tuple(
    'vectors dimensions objects allocations old_scores new_scores expanded scored L B E edges frees'.split()), 'count')
FEATURE_UNITS.update(flops='flop', **dict.fromkeys(
    ('read_bytes', 'write_bytes', 'output_bytes', 'copied_bytes', 'decoded_bytes'), 'B'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key {key}')
        result[key] = value
    return result


def finite(value):
    require(math.isfinite(value), 'nonfinite JSON number')
    return value


def decode(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=strict_pairs,
                      parse_constant=lambda token: finite(float(token)),
                      parse_float=lambda token: finite(float(token)))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def bounded_read(path, limit=LIMIT):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, f'{path}: metadata budget exceeded')
    return raw


@lru_cache(maxsize=None)
def schema(kind, definition=None):
    path = ROOT / 'schemas' / f'ndpsim-{kind}-v1.schema.json'
    spec = json.loads(path.read_text())
    return StrictValidator(spec['$defs'][definition] if definition else spec)


def check(kind, value, definition=None):
    error = next(schema(kind, definition).iter_errors(value), None)
    if error:
        raise ValueError(f'{definition or kind}{error.json_path[1:]}: {error.message}')


def packed_ids(ids):
    return b''.join(struct.pack('<I', node) for node in ids)


def counts(**fields):
    return {name: fields.get(name, 0) for name in WORK}


def edge(row, kind):
    return dict(kind=kind, producer=row['op'], port='out', type=row['expected']['output']['type'])


def access(identity, name, ids, size):
    return dict(object=name, content_sha256=identity['objects'][name]['sha256'], ids=ids, bytes=size)


def read_line(stream, kind, limit):
    raw = stream.readline(limit + 1)
    require(raw.endswith(b'\n') and len(raw) <= limit, f'{kind}: missing, truncated or oversized record')
    value = decode(raw)
    check('static-ir', value, kind)
    return raw, value


def check_identity(identity):
    objects = identity['objects']
    base, queries, graph = (objects[name] for name in ('base', 'queries', 'graph'))
    search = identity['search']
    require(base['records'] == graph['records'] <= 2**32
            and queries['records'] == len(identity['query_ids'])
            and base['width'] == queries['width'] and search['entry'] < base['records']
            and search['k'] <= min(base['records'], search['L']),
            'invalid object shape, query coverage or search parameters')


class QueryPreflight:
    """Consume the ranked-expansion grammar in order, retaining only live metadata."""

    def __init__(self, stream, index, identity, limit, whole_hash):
        self.stream, self.index, self.identity, self.limit = stream, index, identity, limit
        self.whole_hash = whole_hash
        self.search = identity['search']
        self.n = identity['objects']['base']['records']
        self.d = identity['objects']['base']['width']
        self.degree = identity['objects']['graph']['width']
        self.iteration = self.version = self.ops = self.deps = 0
        self.shard_hash = hashlib.sha256()
        self.decision = hashlib.sha256()

    def take(self, operator, deps=(), *, state=None, logical_access=None, ids=None,
             count=None, size=None, work=None, payload=None):
        self.ops += 1
        require(self.ops <= self.index['operations'], 'operation count exceeded')
        remaining = self.index['offset'] + self.index['length'] - self.stream.tell()
        raw, row = read_line(self.stream, 'operation', min(self.limit, remaining))
        self.shard_hash.update(raw)
        self.whole_hash.update(raw)
        self.deps += len(row['deps'])
        require((row['op'], row['query'], row['iteration'], row['batch'], row['operator']) ==
                (self.ops, self.index['query'], self.iteration, self.iteration, operator),
                f'expected {operator} at batch {self.iteration}, got invalid op, query or scope')
        require(row['deps'] == list(deps), 'invalid dependency kind, producer, port or order')
        require(row['state'] == state, 'invalid state transition/version')
        require(operator == 'READ_ADJACENCY' or row['access'] == logical_access, 'invalid logical access, identity, IDs or bytes')
        exp = row['expected']
        output = exp['output']
        require(output['type'] == OUTPUT[operator], 'invalid output port type')
        require(ids is None or exp['ids'] == ids, 'unexpected ordered IDs')
        if operator != 'INIT':
            require(all(node < self.n for node in exp['ids']), 'node ID out of range')
        require(count is None or output['count'] == count, 'invalid output shape')
        require(size is None or output['bytes'] == size, 'invalid output bytes')
        require(work is None or exp['work'] == work, 'inconsistent expected Work')
        if payload is not None:
            require(output['sha256'] == digest(payload), 'invalid output digest')
        self.decision.update(canonical(row) + b'\n')
        return row

    def state(self, operator, deps, ids, logical_access=None):
        previous = None if operator == 'INIT' else self.version
        if previous is not None:
            self.version += 1
        return self.take(operator, deps, state=dict(before=previous, after=self.version),
                         logical_access=logical_access, ids=ids, count=1, size=8,
                         work=counts(), payload=struct.pack('<Q', self.version))

    def verify(self):
        search = self.search
        q = self.index['query']
        init = self.state('INIT', (), [], access(self.identity, 'queries', [q], 4 * self.d))
        state = permit = init
        fresh, ranked, scored, expanded = [search['entry']], None, set(), set()
        while True:
            if fresh:
                mark_deps = [edge(state, 'ORDER'), edge(permit, 'CONTROL')]
                if permit is not init:
                    mark_deps.append(edge(permit, 'DATA'))
                state = self.state('MARK_SCORED', mark_deps, fresh)
                scored.update(fresh)
                n = len(fresh)
                chunks = (n + search['chunk'] - 1) // search['chunk']
                score_deps = [dict(kind='DATA', producer=init['op'], port='query', type='fp32'),
                              edge(state, 'CONTROL')]
                if permit is not init:
                    score_deps.append(edge(permit, 'ADDRESS'))
                score = self.take('SCORE_L2', score_deps, ids=fresh, count=n, size=12*n,
                                  logical_access=access(self.identity, 'base', fresh, 4*self.d*n),
                                  work=counts(elements=n*self.d, flops=3*n*self.d,
                                              read_bytes=4*self.d*(n+chunks), write_bytes=12*n,
                                              output_bytes=12*n))
                merge_deps = [edge(score, 'DATA')]
                if ranked is not None:
                    merge_deps.append(edge(ranked, 'DATA'))
                ranked = self.take('MERGE_CANDIDATES', merge_deps, count=len(scored), size=12*len(scored))
                merged = ranked['expected']
                require(set(merged['ids']) == scored, 'merged rank omits or adds scored IDs')
                w = merged['work']
                total = len(scored)
                duplicate_scan = total * (total - 1) // 2
                require(duplicate_scan + total - 1 <= w['comparisons'] <= 2*duplicate_scan,
                        'merge comparison count outside structural bounds')
                require(w == counts(elements=total, read_bytes=12*total,
                                    write_bytes=12*len(scored), output_bytes=12*len(scored),
                                    candidate_checks=len(scored), comparisons=w['comparisons']),
                        'inconsistent merge Work')
            chosen = self.take('SELECT_FRONTIER',
                               [edge(ranked, 'DATA'), edge(state, 'ORDER'), edge(permit, 'CONTROL')])
            ids = chosen['expected']['ids']
            front = ranked['expected']['ids'][:search['L']]
            want = [node for node in front if node not in expanded][:min(search['B'], search['E']-len(expanded))]
            self.check_ids(chosen, want, counts(candidate_checks=len(front),
                                                write_bytes=4*len(want), output_bytes=4*len(want)))
            if not ids:
                top = ranked['expected']['ids'][:search['k']]
                finish = self.take('FINISH', [edge(chosen, 'CONTROL'), edge(ranked, 'DATA'),
                                              edge(state, 'ORDER')], ids=top, count=len(top),
                                   size=12*len(top), work=counts())
                if len(top) == len(scored):
                    require(finish['expected']['output']['sha256'] == ranked['expected']['output']['sha256'],
                            'finish digest disagrees with complete rank')
                terminal = 'budget' if len(expanded) == search['E'] else 'exhausted'
                return terminal, finish['expected']['output']['sha256']
            state = self.state('MARK_EXPANDED', [edge(state, 'ORDER'), edge(chosen, 'CONTROL'),
                                                 edge(chosen, 'DATA')], ids)
            expanded.update(ids)
            reads = []
            edges = 0
            for node in ids:
                row = self.take('READ_ADJACENCY', [edge(chosen, 'ADDRESS'), edge(state, 'CONTROL')],
                                ids=[node], work=counts())
                output = row['expected']['output']
                require(output['count'] <= self.degree and output['bytes'] == 4*(1+output['count']),
                        'invalid adjacency shape/bytes')
                require(row['access'] == access(self.identity, 'graph', [node], output['bytes']),
                        'invalid adjacency logical access')
                edges += output['count']
                reads.append(row)
            joined = self.take('JOIN', [edge(row, 'DATA') for row in reads], ids=ids,
                               count=len(ids), size=4*(len(ids)+edges), work=counts())
            if len(reads) == 1:
                require(joined['expected']['output']['sha256'] == reads[0]['expected']['output']['sha256'],
                        'single-row join digest mismatch')
            permit = self.take('DEDUP_NEIGHBORS', [edge(joined, 'DATA'), edge(state, 'ORDER')])
            fresh = permit['expected']['ids']
            require(fresh == sorted(fresh) and not scored.intersection(fresh) and len(fresh) <= edges,
                    'invalid dedup IDs')
            self.check_ids(permit, fresh, counts(candidate_checks=edges, write_bytes=4*len(fresh),
                                                output_bytes=4*len(fresh),
                                                comparisons=permit['expected']['work']['comparisons']))
            self.iteration += 1

    @staticmethod
    def check_ids(row, expected, work):
        exp = row['expected']
        out = exp['output']
        require(exp['ids'] == expected and exp['work'] == work and
                (out['count'], out['bytes'], out['sha256']) ==
                (len(expected), 4*len(expected), digest(packed_ids(expected))),
                'inconsistent expected IDs, shape, digest or Work')


def package_hash(tail):
    return digest(b'ndpsim-static-ir/1\0' + b''.join(
        bytes.fromhex(tail[key]) for key in
        ('manifest_sha256', 'index_sha256', 'operations_sha256')))


def validate_package(path, binding_path, limit=LIMIT, *, checkpoint=None):
    manifest_raw = bounded_read(path/'manifest.json', limit)
    manifest = decode(manifest_raw)
    check('static-ir', manifest)
    tail = decode(bounded_read(path/'tail.json', limit))
    check('static-ir', tail, 'tail')
    require(tail['manifest_sha256'] == digest(manifest_raw) and
            tail['package_sha256'] == package_hash(tail), 'manifest/package hash mismatch')
    binding = decode(bounded_read(binding_path, limit))
    check('static-ir', binding, 'binding')
    for key in ('source', 'build', 'identity'):
        require(manifest[key] == binding[key], f'package binding mismatch: {key}')
    identity = binding['identity']
    check_identity(identity)
    for name, descriptor in identity['objects'].items():
        if checkpoint is not None:
            checkpoint()
        file = binding_path.parent / binding['files'][name]
        width = descriptor['width'] + (name == 'graph')
        require(file.stat().st_size == 4 * width * descriptor['records']
                and file_hash(file) == descriptor['sha256'], f'bound object mismatch: {name}')

    index_hash, operations_hash = hashlib.sha256(), hashlib.sha256()
    operations = dependencies = 0
    with (path/'index.jsonl').open('rb') as index, (path/'operations.jsonl').open('rb') as stream:
        remaining = set(identity['query_ids'])
        for _ in identity['query_ids']:
            query = None
            if checkpoint is not None:
                checkpoint()
            try:
                raw, entry = read_line(index, 'index', limit)
                query = entry['query']
                require(query in remaining and entry['offset'] == stream.tell(),
                        'incorrect query index/coverage or offset')
                remaining.remove(query)
                index_hash.update(raw)
                reader = QueryPreflight(stream, entry, identity, limit, operations_hash)
                try:
                    terminal, result = reader.verify()
                except (ValueError, OverflowError) as error:
                    raise ValueError(f'batch {reader.iteration} op {reader.ops}: {error}') from error
                require(stream.tell() == entry['offset'] + entry['length']
                        and reader.ops == entry['operations'] and reader.deps == entry['dependencies']
                        and reader.shard_hash.hexdigest() == entry['sha256']
                        and reader.decision.hexdigest() == entry['decision_sha256']
                        and terminal == entry['terminal'] and result == entry['result_sha256'],
                        'query length, counts, terminal or hash mismatch')
                operations += reader.ops
                dependencies += reader.deps
            except ValueError as error:
                scope = f'query {query}' if query is not None else f'query {min(remaining)} (coverage)'
                raise ValueError(f'{scope}: {error}') from error
        require(not index.read(1) and not stream.read(1), 'extra index/operation records')
    require((tail['queries'], tail['operations'], tail['dependencies'],
             tail['index_sha256'], tail['operations_sha256']) ==
            (len(identity['query_ids']), operations, dependencies,
             index_hash.hexdigest(), operations_hash.hexdigest()), 'invalid completeness tail')
    if checkpoint is not None:
        checkpoint()
    return dict(package_sha256=tail['package_sha256'], source=manifest['source'],
                queries=tail['queries'], operations=operations, dependencies=dependencies)


def validate_measurement(record):
    check('measurement', record)
    identity = record['identity']
    check_identity(identity)
    windows = record['windows']
    require([w['query'] for w in windows] == identity['query_ids'], 'measurement query coverage/order mismatch')
    require(record['split'] == 'fixture' or len(windows) ==
            {'tuning32': 32, 'evaluation128': 128}[record['split']], 'split query count mismatch')
    workers = record['workers']
    require(record['max_inflight'] >= workers, 'worker/admission quota mismatch')
    by_query = {w['query']: w for w in windows}
    last_admission = last_start = 0
    last_worker = {}
    events = []
    for w in windows:
        a, start, end = (w[key] for key in ('admission_ns', 'worker_start_ns', 'visible_ns'))
        require(last_admission <= a <= start <= end <= record['drain_end_ns'] and
                last_start <= start and w['worker'] < workers and w['cpu_start_ns'] <= w['cpu_end_ns'],
                'invalid admission/worker/visible/CPU timeline')
        wall_end, cpu_end = last_worker.get(w['worker'], (0, 0))
        require(wall_end <= start and cpu_end <= w['cpu_start_ns'], 'worker owns overlapping queries')
        last_worker[w['worker']] = end, w['cpu_end_ns']
        last_admission, last_start = a, start
        if end > a:
            events.extend(((a, 1), (end, -1)))
    require(windows[0]['admission_ns'] < max(w['visible_ns'] for w in windows), 'empty QPS window')
    active = 0
    for _, change in sorted(events):
        active += change
        require(active <= record['max_inflight'], 'admission quota exceeded')

    sampled = record['mode'] == 'cpu_sample'
    require(sampled == bool(record['samples']) and
            (sampled or not record['score_audits']), 'measurement mode/sample mismatch')
    require(not sampled or record['empty_probe_count'] > 0, 'missing empty clock probes')
    resolution = record['cpu_clock']['resolution_ns']
    samples, score_groups = {}, {}
    seen = set()
    operators = {}
    planned = {}
    for entry in record['coverage']:
        key = entry['operator'], entry['bucket']
        require(key not in planned and key[1] in STAGES.get(key[0], ()), 'duplicate/unknown coverage bucket')
        require(entry['planned_calls'] == (entry['eligible_calls']-1)//record['sample_stride']+1,
                'incorrect planned sample coverage')
        planned[key] = entry['planned_calls']
    observed = Counter()
    for row in record['samples']:
        q, op, bucket = row['query'], row['op'], row['bucket']
        key = q, op, row['chunk'], row['segment'], bucket
        require(operators.setdefault((q, op), row['operator']) == row['operator'],
                'CPU samples disagree about operator identity')
        require(key not in seen and bucket in STAGES.get(row['operator'], ()), 'duplicate/unknown CPU attribution')
        require((row['chunk'] is not None) == (row['operator'] == 'SCORE_L2'), 'invalid CPU chunk scope')
        if record['preparation_context'] == 'resident_random':
            require(row['operator'] not in ('MARK_SCORED', 'MARK_EXPANDED'),
                    'resident preparation already owns scored/expanded insertion')
        seen.add(key)
        observed[row['operator'], bucket] += row['calls']
        require(row['features'].keys() <= FEATURE_UNITS.keys() - set(WORK), 'unknown sample feature')
        validate_interval(row, by_query)
        raw = row['end_ns'] - row['start_ns']
        corrected = raw - row['overhead_ns']
        overhead = (record['empty_probe_ns'] // record['empty_probe_count']) * row['calls']
        quality = 'usable' if corrected >= resolution * row['calls'] else 'low_signal'
        require((row['raw_ns'], row['corrected_ns'], row['quality'], row['overhead_ns']) ==
                (raw, corrected, quality, overhead), 'invalid raw/corrected CPU time, overhead or quality')
        samples.setdefault(q, []).append((row['start_ns'], row['end_ns']))
        if row['operator'] == 'SCORE_L2':
            group = score_groups.setdefault((q, op, row['chunk']), {})
            require(bucket not in group and row['segment'] == 0, 'duplicate score chunk CPU bucket')
            group[bucket] = row
    require(observed == planned, 'missing/unplanned CPU samples')
    for q, intervals in samples.items():
        intervals.sort()
        require(all(end <= start for (_, end), (start, _) in zip(intervals, intervals[1:])),
                f'query {q}: overlapping exclusive CPU intervals')
    audit_raw = audit_gap = 0
    for audit in record['score_audits']:
        validate_interval(audit, by_query)
        group = score_groups.pop((audit['query'], audit['op'], audit['chunk']), {})
        require(set(group) == {'score_prepare', 'score_l2'}, 'missing/duplicate score audit or sample')
        prepare, l2 = group['score_prepare'], group['score_l2']
        require(audit['start_ns'] <= prepare['start_ns'] <= prepare['end_ns'] <= l2['start_ns'] <=
                l2['end_ns'] <= audit['end_ns'] and prepare['calls'] == l2['calls'],
                'score audit does not enclose ordered exclusive stages')
        require(not any(start < audit['end_ns'] and end > audit['start_ns'] and
                        (start, end) not in ((prepare['start_ns'], prepare['end_ns']),
                                             (l2['start_ns'], l2['end_ns']))
                        for start, end in samples[audit['query']]), 'score audit encloses unrelated CPU work')
        duration = audit['end_ns'] - audit['start_ns']
        gap = duration - prepare['raw_ns'] - l2['raw_ns']
        require(gap <= max(duration//20, 2*resolution*prepare['calls']), 'score CPU closure gap exceeds tolerance')
        audit_raw += duration
        audit_gap += gap
    require(not score_groups, 'missing enclosing score audit')
    return dict(queries=len(windows), samples=len(seen),
                low_signal=sum(row['quality'] == 'low_signal' for row in record['samples']),
                attributed_raw_ns=sum(end-start for intervals in samples.values() for start, end in intervals),
                score_audit_raw_ns=audit_raw, score_audit_gap_ns=audit_gap,
                thread_cpu_ns=sum(w['cpu_end_ns']-w['cpu_start_ns'] for w in windows))


def validate_interval(row, windows):
    w = windows.get(row['query'])
    require(w and row['worker'] == w['worker'] and w['cpu_start_ns'] <=
            row['start_ns'] <= row['end_ns'] <= w['cpu_end_ns'], 'CPU interval outside query/worker scope')


def validate_profile(profile):
    check('cpu-profile', profile)
    training = profile['training']
    require(training['split'] == 'fixture' or len(training['query_ids']) == 32, 'wrong training split size')
    seen = set()
    has_gbdt = False
    split_keys = {('MERGE_CANDIDATES', stage) for stage in ('merge_append', 'merge_finalize')}
    for entry in profile['models']:
        op, stage = entry['operator'], entry['stage']
        key = op, stage
        require(key not in seen and (key in split_keys or (stage != 'merge' and stage in STAGES.get(op, ()))),
                'duplicate or invalid model key')
        require(profile['applicability']['preparation_context'] != 'resident_random'
                or stage != 'state', 'resident preparation already owns scored/expanded insertion')
        seen.add(key)
        features, model = entry['features'], entry['model']
        require(key not in split_keys or model['kind'] == 'linear', 'split merge requires linear models')
        require(len({f['name'] for f in features}) == len(features), 'duplicate model feature')
        for feature in features:
            require(FEATURE_UNITS[feature['name']] == feature['unit']
                    and feature['minimum'] <= feature['maximum'], 'invalid feature unit or range')
        if model['kind'] == 'linear':
            require(len(features) == len(model['coefficients']), 'coefficient/feature count mismatch')
            # Runtime uses ordered binary64 multiply/add without contraction.
            upper = float(model['intercept_ns'])
            for feature, coefficient in zip(features, model['coefficients']):
                upper += float(feature['maximum']) * float(coefficient)
        elif model['kind'] == 'table':
            require(len(features) == 1, 'table needs one feature')
            edges = [b['upper'] for b in model['bins']]
            require(edges == sorted(set(edges)) and edges[0] >= features[0]['minimum']
                    and edges[-1] == features[0]['maximum'], 'table must cover exact feature range')
            upper = max(b['ns'] for b in model['bins'])
        elif model['kind'] == 'log_gbdt':
            require((op, stage) == ('DEDUP_NEIGHBORS', 'dedup'), 'unsupported GBDT stage')
            has_gbdt = True
            require(model['rate'] > 0, 'nonpositive GBDT learning rate')
            magnitude = 0.
            for tree in model['trees']:
                leaves = []

                def visit(index, depth):
                    require(index < len(tree), 'incomplete tree')
                    node = tree[index]
                    if not isinstance(node, dict):
                        leaves.append(abs(node))
                        return index + 1
                    feature, threshold, right = (node[k] for k in ('feature', 'threshold', 'right'))
                    require(depth < 3 and feature < len(features)
                            and features[feature]['minimum'] <= threshold < features[feature]['maximum'],
                            'invalid tree split')
                    require(visit(index + 1, depth + 1) == right, 'invalid right child')
                    return visit(right, depth + 1)

                require(visit(0, 0) == len(tree), 'trailing tree nodes')
                magnitude += max(leaves)
            log_upper = model['initial'] + model['rate'] * magnitude
            require(log_upper <= math.log(2**64), 'service time overflow')
            upper = math.exp(log_upper)
        else:
            upper = model['ns']
        require(math.isfinite(upper) and math.ceil(upper) <= U64, 'service time overflow')
    require(has_gbdt, 'missing dedup GBDT model')
    require(split_keys <= seen, 'split merge requires both linear models')
    return dict(models=len(seen))


def validate_config(config):
    check('effective-config', config)
    replay = config['execution_engine'] == 'replay'
    resident = config['data_provider'] == 'host_memory'
    workers = config['native_workers']
    require(replay == (config['ir_input'] is not None), 'static package requires explicit replay')
    require(not resident or (replay and config['placement'] == 'host' and config['cpu_profile']),
            'host memory requires replay, Host placement and an explicit CPU Profile')
    require(config['order_policy'] == ('worker-fifo/1' if resident else 'legacy-ssd/1'),
            'invalid order policy for data provider')
    require((resident and config['cpu_slots'] == workers <= config['max_inflight']) or
            (not resident and workers == 1), 'worker/slot/admission mismatch')
    require(replay or config['cpu_profile'] is None, 'CPU Profile requires explicit replay')
    return dict(engine=config['execution_engine'], order_policy=config['order_policy'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('package', 'measurement', 'cpu-profile', 'effective-config'))
    parser.add_argument('path', type=Path)
    parser.add_argument('--binding', type=Path, help='independent expected binding, required for package')
    args = parser.parse_args()
    if (args.kind == 'package') != (args.binding is not None):
        parser.error('--binding is required only for package')
    try:
        if args.kind == 'package':
            report = validate_package(args.path, args.binding)
        else:
            checkers = {'measurement': validate_measurement, 'cpu-profile': validate_profile,
                        'effective-config': validate_config}
            report = checkers[args.kind](decode(bounded_read(args.path)))
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        print(json.dumps(dict(valid=False, error=str(error))), file=sys.stderr)
        return 1
    print(json.dumps(dict(valid=True, **report), sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
