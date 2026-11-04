"""Turn bounded native observation shards into V04-2a artifacts."""
from collections import Counter
import hashlib
import json
import shlex
import struct
import subprocess
from time import monotonic

from v04_contract import (LIMIT, OUTPUT, STAGES, WORK, access, canonical, counts, decode,
                          digest, edge, file_hash, package_hash, require, validate_measurement,
                          validate_package)


def build_identity(binary, binary_sha):
    source, compiler, flags = subprocess.check_output(
        [str(binary.resolve()), '--build-info'], text=True, timeout=10).splitlines()
    return dict(source_sha256=source, binary_sha256=binary_sha, compiler=compiler,
                flags=shlex.split(flags))


def binding_for(build, files, shape, search):
    names = ('base', 'queries', 'graph')
    objects = {
        name: dict(sha256=pair[1], records=shape['queries' if name == 'queries' else 'count'],
                   width=shape['degree' if name == 'graph' else 'dimensions'],
                   encoding='degree-u32-le+padded-ids-u32-le' if name == 'graph' else 'fp32-le')
        for name, pair in zip(names, files)
    }
    query_ids = [row[0] for row in struct.iter_unpack('<I', files[4][0].read_bytes())]
    identity = dict(algorithm='ndpsim-ranked-expansion-v1', numeric='fp32-le-ordered-fp64-l2-v1',
                    work='ndpsim-work-v1', objects=objects, query_ids=query_ids,
                    search={target: search[key] for target, key in zip(
                        ('entry', 'k', 'L', 'B', 'E', 'chunk'),
                        ('entry', 'k', 'width', 'batch', 'budget', 'chunk'))})
    return dict(format='ndpsim-static-ir-binding/1', source='native-capture',
                build=build, identity=identity, files={n: str(p[0]) for n, p in zip(names, files)})


def records(shard):
    with shard.open('rb') as stream:
        while raw := stream.readline(LIMIT + 1):
            require(len(raw) <= LIMIT and raw.endswith(b'\n'), 'truncated or oversized native event')
            yield decode(raw)


def graph(shard, identity, query):
    """Bind live boundary records to the frozen per-query grammar."""
    init = state = permit = ranked = mark = score = chosen = joined = None
    reads = []
    version = iteration = 0
    for op, (operator, ids, count, size, sha, work) in enumerate(records(shard), 1):
        def deps(*pairs):
            return [edge(row, kind) for row, kind in pairs]

        logical = transition = None
        if operator == 'INIT':
            parents = []
            logical = access(identity, 'queries', [query], 4 * identity['objects']['queries']['width'])
        elif operator == 'MARK_SCORED':
            parents = deps((state, 'ORDER'), (permit, 'CONTROL'))
            if permit is not init:
                parents += deps((permit, 'DATA'))
        elif operator == 'SCORE_L2':
            parents = [dict(kind='DATA', producer=init['op'], port='query', type='fp32')]
            parents += deps((mark, 'CONTROL'))
            if permit is not init:
                parents += deps((permit, 'ADDRESS'))
            logical = access(identity, 'base', ids, len(ids) * 4 * identity['objects']['base']['width'])
        elif operator == 'MERGE_CANDIDATES':
            parents = deps((score, 'DATA'))
            if ranked is not None:
                parents += deps((ranked, 'DATA'))
        elif operator == 'SELECT_FRONTIER':
            parents = deps((ranked, 'DATA'), (state, 'ORDER'), (permit, 'CONTROL'))
        elif operator == 'MARK_EXPANDED':
            parents = deps((state, 'ORDER'), (chosen, 'CONTROL'), (chosen, 'DATA'))
        elif operator == 'READ_ADJACENCY':
            parents = deps((chosen, 'ADDRESS'), (state, 'CONTROL'))
            logical = access(identity, 'graph', ids, size)
        elif operator == 'JOIN':
            parents = [edge(row, 'DATA') for row in reads]
            reads.clear()
        elif operator == 'DEDUP_NEIGHBORS':
            parents = deps((joined, 'DATA'), (state, 'ORDER'))
        elif operator == 'FINISH':
            parents = deps((chosen, 'CONTROL'), (ranked, 'DATA'), (state, 'ORDER'))
        else:
            raise ValueError(f'unknown native operator {operator}')
        if OUTPUT[operator] == 'state':
            previous = None if operator == 'INIT' else version
            version += previous is not None
            transition = dict(before=previous, after=version)
            count, size, sha = 1, 8, digest(struct.pack('<Q', version))
        row = dict(query=query, op=op, operator=operator, operator_version=1,
                   iteration=iteration, batch=iteration, deps=parents, state=transition,
                   access=logical, expected=dict(ids=ids, work=dict(zip(WORK, work, strict=True)),
                       output=dict(port='out', type=OUTPUT[operator], count=count, bytes=size, sha256=sha)))
        yield row
        if operator == 'INIT':
            init = state = permit = row
        elif operator == 'MARK_SCORED':
            mark = state = row
        elif operator == 'SCORE_L2':
            score = row
        elif operator == 'MERGE_CANDIDATES':
            ranked = row
        elif operator == 'SELECT_FRONTIER':
            chosen = row
        elif operator == 'MARK_EXPANDED':
            state = row
        elif operator == 'READ_ADJACENCY':
            reads.append(row)
        elif operator == 'JOIN':
            joined = row
        elif operator == 'DEDUP_NEIGHBORS':
            permit = row
            iteration += 1


MEASUREMENT_LIMIT = 512 * 1024**2


class Budget:
    def __init__(self, limit, deadline=float('inf')):
        self.remaining = limit
        self.deadline = deadline

    def check(self):
        require(monotonic() <= self.deadline, 'observation post-processing timeout exceeded')
        require(self.remaining >= 0, 'observation output budget exceeded')

    def write(self, stream, data):
        self.check()
        require(len(data) <= self.remaining, 'observation output budget exceeded')
        self.remaining -= len(data)
        stream.write(data)

    def json(self, path, data, metadata=True):
        encoded = (canonical(data) if metadata else json.dumps(data, allow_nan=False).encode()) + b'\n'
        require(not metadata or len(encoded) <= LIMIT, 'observation metadata exceeds contract limit')
        with path.open('xb') as stream:
            self.write(stream, encoded)


def seal(out, shards, binding, report, budget):
    stage = out/'package.partial'
    stage.mkdir()
    identity = binding['identity']
    binding_path = out/'binding.json'
    budget.json(binding_path, binding)
    budget.json(stage/'manifest.json', dict(format='ndpsim-static-ir/1', run_id=out.name,
                **{key: binding[key] for key in ('source', 'build', 'identity')}))
    n_ops = n_deps = 0
    with (stage/'operations.jsonl').open('wb') as ops, (stage/'index.jsonl').open('wb') as index:
        for i, result in enumerate(report['queries']):
            query = identity['query_ids'][i]
            offset, rows, links = ops.tell(), 0, 0
            shard_hash = hashlib.sha256()
            work = Counter()
            row = None
            for row in graph(shards/str(i), identity, query):
                encoded = canonical(row) + b'\n'
                require(len(encoded) <= LIMIT, 'static IR operation exceeds contract line limit')
                budget.write(ops, encoded)
                shard_hash.update(encoded)
                rows += 1
                links += len(row['deps'])
                work.update(row['expected']['work'])
            require(row is not None and row['operator'] == 'FINISH', 'missing native FINISH')
            packed = b''.join(struct.pack('<Id', item['id'], item['distance']) for item in result['top_k'])
            require(row['expected']['output']['sha256'] == digest(packed) and
                    dict(work) == result['work'], 'capture disagrees with actual batch result or Work')
            sha = shard_hash.hexdigest()
            terminal = 'budget' if result['expanded'] == identity['search']['E'] else 'exhausted'
            entry = dict(query=query, offset=offset, length=ops.tell()-offset, operations=rows,
                         dependencies=links, terminal=terminal, sha256=sha, decision_sha256=sha,
                         result_sha256=row['expected']['output']['sha256'])
            budget.write(index, canonical(entry) + b'\n')
            n_ops += rows
            n_deps += links
    tail = dict(format='ndpsim-static-ir-tail/1', complete=True, dropped=0,
                queries=len(report['queries']), operations=n_ops, dependencies=n_deps,
                manifest_sha256=file_hash(stage/'manifest.json'), index_sha256=file_hash(stage/'index.jsonl'),
                operations_sha256=file_hash(stage/'operations.jsonl'))
    tail['package_sha256'] = package_hash(tail)
    budget.json(stage/'tail.json', tail)
    validation = validate_package(stage, binding_path, checkpoint=budget.check)
    budget.json(out/'package-validation.json', validation)
    return stage


BUCKET_OP = {bucket: op for op, buckets in STAGES.items() for bucket in buckets if bucket != 'state'}
FEATURES = dict(init=('copied_bytes',), score_prepare=('vectors', 'dimensions'),
                score_l2=('vectors', 'dimensions'), merge=('old_scores', 'new_scores'),
                select=('old_scores', 'expanded'), adjacency_prepare=('edges',),
                dedup=('edges', 'scored'), finish=('objects',))


def measurement(out, shards, binding, report, mode, stride, split, budget):
    identity = binding['identity']
    windows, samples, audits = [], [], []
    sample_bytes = 0
    eligible, planned = Counter(), Counter()
    probe_count, probe_ns = report['empty_probe_count'], report['empty_probe_ns']
    overhead = probe_ns // probe_count if probe_count else 0
    resolution = report['cpu_resolution_ns']
    for i, result in enumerate(report['queries']):
        budget.check()
        query, worker = result['original_query_id'], result['worker']
        window = {key: result[key] for key in ('offered_ns', 'admission_ns', 'worker_start_ns',
                                                'visible_ns', 'cpu_start_ns')}
        windows.append(dict(query=query, worker=worker, **window,
                            cpu_end_ns=window['cpu_start_ns'] + result['thread_cpu_ns']))
        if mode != 'cpu_sample':
            continue
        chunks, rounds = result['score_chunks'], result['expansion_batches']
        query_eligible = dict(init=1, finish=1, score_prepare=chunks, score_l2=chunks,
                              merge=chunks+result['score_batches'], select=rounds+1,
                              dedup=rounds, adjacency_prepare=result['expanded'])
        query_planned = Counter()
        for bucket, n in query_eligible.items():
            previous = eligible[bucket]
            if n:
                eligible[bucket] += n
                query_planned[bucket] = ((previous+n+stride-1)//stride -
                                         (previous+stride-1)//stride)
        observed = Counter()
        prepare = None
        for bucket, op, chunk, segment, start, end, a, b, work in records(shards/str(i)):
            operator = BUCKET_OP[bucket]
            observed[bucket] += 1
            planned[bucket] += 1
            raw, corrected = end-start, end-start-overhead
            key = dict(query=query, worker=worker, op=op, chunk=None if chunk < 0 else chunk)
            sample = dict(**key, segment=segment, operator=operator, bucket=bucket,
                                start_ns=start, end_ns=end, calls=1, overhead_ns=overhead,
                                raw_ns=raw, corrected_ns=corrected, work=dict(zip(WORK, work, strict=True)),
                                features=dict(zip(FEATURES[bucket], (a, b))),
                                quality='usable' if corrected >= resolution else 'low_signal')
            sample_bytes += len(canonical(sample)) + 1
            require(sample_bytes <= min(MEASUREMENT_LIMIT, budget.remaining), 'CPU sample output exceeds budget')
            samples.append(sample)
            if bucket == 'score_prepare':
                require(prepare is None, 'missing L2 timing interval')
                prepare = key, start
            elif bucket == 'score_l2':
                require(prepare is not None and prepare[0] == key, 'unpaired score timing interval')
                audits.append(dict(**key, start_ns=prepare[1], end_ns=end))
                prepare = None
        require(prepare is None and observed == query_planned,
                f'query {query}: native CPU inventory disagrees with sampling plan')
    record = dict(format='ndpsim-measurement/1', unit='ns', mode=mode, run_id=out.name,
                  build=binding['build'], identity=identity, split=split,
                  wall_clock=dict(source=report['wall_clock'], resolution_ns=report['wall_resolution_ns']),
                  cpu_clock=dict(source=report['cpu_clock'], resolution_ns=resolution),
                  preparation_context='resident_random', workers=report['workers'],
                  max_inflight=report['concurrency'], drain_end_ns=report['drain_end_ns'], dropped=0,
                  complete=True, sample_stride=stride, empty_probe_count=probe_count, empty_probe_ns=probe_ns,
                  windows=windows, samples=samples, score_audits=audits,
                  coverage=[dict(operator=BUCKET_OP[b], bucket=b, eligible_calls=n, planned_calls=planned[b])
                            for b, n in sorted(eligible.items())])
    checked = validate_measurement(record)
    unattributed = checked['thread_cpu_ns'] - checked['attributed_raw_ns']
    require(unattributed >= 0, 'negative unattributed CPU')
    encoded = canonical(record) + b'\n'
    require(len(encoded) <= MEASUREMENT_LIMIT, 'CPU Measurement exceeds byte limit')
    with (out/'measurement.json').open('xb') as stream:
        budget.write(stream, encoded)
    budget.json(out/'measurement-validation.json', dict(**checked, unattributed_cpu_ns=unattributed,
        unattributed_fraction=unattributed / checked['thread_cpu_ns'] if checked['thread_cpu_ns'] else None,
        eligible_calls=dict(eligible), planned_calls=dict(planned),
        observed_calls=dict(Counter(row['bucket'] for row in samples)),
        unsampled_calls={b: n-planned[b] for b, n in eligible.items()},
        exclusive_unattributed_cpu_ns=unattributed if mode == 'cpu_sample' and stride == 1 else None,
        timing_use='baseline' if mode in ('benchmark', 'uninstrumented') else 'diagnostic'))


def compare_inventory(operations, capture_identity, record):
    """Rebuild the CPU sampling plan independently from full static operations."""
    require(capture_identity == record['identity'], 'static and CPU capture identities differ')
    stride = record['sample_stride']
    eligible = Counter()
    planned = set()
    with operations.open('rb') as stream:
        for raw in stream:
            row = decode(raw)
            name = row['operator']
            if name == 'SCORE_L2':
                chunk_size = capture_identity['search']['chunk']
                chunks = (len(row['expected']['ids']) + chunk_size - 1) // chunk_size
                stages = [('score_prepare', row['op'], j, 0) for j in range(chunks)]
                stages += [('score_l2', row['op'], j, 0) for j in range(chunks)]
                stages += [('merge', row['op']+1, None, j) for j in range(chunks+1)]
            else:
                stages = [(bucket, row['op'], None, 0) for bucket in STAGES.get(name, ())
                          if bucket not in ('merge', 'state')]
            for bucket, op, chunk, segment in stages:
                if eligible[bucket] % stride == 0:
                    planned.add((row['query'], bucket, op, chunk, segment))
                eligible[bucket] += 1
    observed = {(r['query'], r['bucket'], r['op'], r['chunk'], r['segment']) for r in record['samples']}
    require(observed == planned and len(observed) == len(record['samples']) and
            dict(eligible) == {r['bucket']: r['eligible_calls'] for r in record['coverage']},
            'CPU sampling positions disagree with static operation inventory')
    return dict(eligible_calls=dict(eligible), matched_positions=len(planned))
