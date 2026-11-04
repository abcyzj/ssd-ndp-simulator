"""Exercise preflight, actual replay causality, re-costing and failure drain through SST."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))

from native_observation import Budget, binding_for, build_identity, seal
from replay_input import prepare
from v04_contract import STAGES, canonical, digest, package_hash
from test_native_search import fixture as native_fixture, check as check_native

FIXTURE = ROOT/'examples/v04-contract'


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_bytes(canonical(value) + b'\n')


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def modified_package(source, path, field):
    shutil.copytree(source, path)
    ops = rows(path/'operations.jsonl')
    target = None
    index = rows(path/'index.jsonl')
    if field == 'reorder':
        index.reverse()
    elif field == 'dependency':
        ops[1]['deps'].pop()
    elif field == 'state':
        ops[1]['state']['after'] += 1
    else:
        target = next(row for row in ops if row['operator'] == ('SCORE_L2' if field == 'value'
                                                                 else 'DEDUP_NEIGHBORS'))
        if field == 'value':
            target['expected']['output']['sha256'] = 'f'*64
        else:
            target['expected']['work']['comparisons'] += 1
    offset = 0
    with (path/'operations.jsonl').open('wb') as stream:
        for row in index:
            shard = b''.join(canonical(op) + b'\n' for op in ops if op['query'] == row['query'])
            row.update(offset=offset, length=len(shard), sha256=digest(shard),
                       decision_sha256=digest(shard))
            offset += len(shard)
            stream.write(shard)
    (path/'index.jsonl').write_bytes(b''.join(canonical(row) + b'\n' for row in index))
    tail = read(path/'tail.json')
    for name in ('index', 'operations'):
        tail[name+'_sha256'] = digest((path/(name+'.jsonl')).read_bytes())
    tail['package_sha256'] = package_hash(tail)
    write(path/'tail.json', tail)
    return target


def run(build, out, ns, field=None, backend='mmap', binding_path=FIXTURE/'binding.json',
        package=FIXTURE/'package', stale=False, expected_operations=38, expected_reads=10,
        workers=1, inflight=1, profile_workers=1, invalid=None):
    out.mkdir()
    binding = read(binding_path)
    if field:
        modified = out/'modified-package'
        target = modified_package(package, modified, field)
        package = modified
    profile = read(FIXTURE/'cpu-profile.json')
    base = binding['identity']['objects']['base']
    profile['applicability'].update(build=binding['build'], workers=profile_workers,
                                    dimensions=base['width'], records=base['records'])
    exemplar = profile['models'][0]
    profile['models'] = [
        dict(exemplar, operator=op, stage=stage, features=[], model=dict(kind='constant', ns=ns))
        for op, stages in STAGES.items() for stage in stages if stage not in ('state', 'merge', 'dedup')
    ]
    profile['models'].extend([
        dict(exemplar, operator='MERGE_CANDIDATES', stage=phase,
             features=[dict(name='dimensions', unit='count', minimum=base['width'], maximum=base['width'])],
             model=dict(kind='linear', intercept_ns=ns, coefficients=[0]))
        for phase in ('merge_append', 'merge_finalize')])
    profile['models'].append(dict(
        exemplar, operator='DEDUP_NEIGHBORS', stage='dedup',
        features=[dict(name='dimensions', unit='count', minimum=base['width'], maximum=base['width'])],
        model=dict(kind='log_gbdt', initial=math.log(max(1, ns) - .25), rate=1., trees=[[0.]])))
    # A constant model still needs a declared, bounded feature.
    for model in profile['models']:
        model['features'] = [dict(name='dimensions', unit='count', minimum=base['width'], maximum=base['width'])]
    profile_path = out/'profile.json'
    write(profile_path, profile)
    paths = {name: str(binding_path.parent / file) for name, file in binding['files'].items()}
    host = {k: profile['applicability'][k] for k in
            ('hardware', 'os', 'memory_bytes', 'frequency', 'affinity')}
    config = dict(placement='host', data_provider='host_memory', order_policy='worker-fifo/1',
                  native_workers=workers, allow_replay_fixture=True, fixture_host=host,
                  ir_binding=str(binding_path),
                  ir_input=dict(path=str(package), sha256=read(package/'tail.json')['package_sha256']),
                  cpu_profile=dict(path=str(profile_path), sha256=digest(profile_path.read_bytes())))
    objects = binding['identity']['objects']
    ids = binding['identity']['query_ids']
    search = binding['identity']['search']
    params = dict(execution_engine='replay', count=objects['base']['records'],
                  dimensions=objects['base']['width'], degree=objects['graph']['width'],
                  queries=len(ids), entry=search['entry'], k=search['k'], width=search['L'],
                  batch=search['B'], budget=search['E'], chunk=search['chunk'],
                  slots=workers, max_inflight=inflight,
                  vector_file=paths['base'], graph_file=paths['graph'], query_file=paths['queries'],
                  content_backend=backend, output_budget_bytes=2**20, event_log=1,
                  records=str(out/'records.jsonl'), output=str(out/'events.jsonl'))
    snapshot = out/'snapshot'
    snapshot.mkdir()
    if invalid == 'quota':
        params['slots'] = workers - 1
        try:
            prepare(config, ROOT, out, snapshot, params, ids, 30)
        except ValueError as error:
            assert 'worker/slot/admission mismatch' in str(error)
            write(out/'preflight-failure.json', dict(detail=str(error)))
            return
        raise AssertionError('mismatched quota accepted')
    if stale:
        try:
            prepare(config, ROOT, out, snapshot, params, ids, 30)
        except ValueError as error:
            assert 'bound object mismatch: graph' in str(error)
            write(out/'preflight-failure.json', dict(detail=str(error)))
            return
        raise AssertionError('stale content was admitted')
    prepare(config, ROOT, out, snapshot, params, ids, 30)
    write(out/'params.json', params)
    paths = read(build/'sst-paths.json')
    env = dict(os.environ, NDPSIM_M31_PARAMS=str(out/'params.json'))
    executable = paths['sst']
    if runtime := os.environ.get('NDPSIM_TEST_ASAN_RUNTIME'):
        executable = str(Path(paths['prefix'])/'libexec/sstsim.x')
        env.update(SST_ROOT=paths['prefix'], DYLD_INSERT_LIBRARIES=runtime,
                   ASAN_OPTIONS='detect_leaks=0:halt_on_error=1')
    argv = [executable, '--add-lib-path='+str(build), str(ROOT/'configs/sst/graph.py')]
    write(out/'command.json', dict(argv=argv))
    process = subprocess.run(argv, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
    (out/'sst.log').write_bytes(process.stdout)
    write(out/'command.json', dict(argv=argv, exit_code=process.returncode))
    if invalid == 'profile':
        assert process.returncode != 0 and b'applicability mismatch: workers' in process.stdout
        return
    records = rows(out/'records.jsonl')
    events = rows(out/'events.jsonl')
    if field in ('value', 'work'):
        assert process.returncode != 0, process.stdout
        failure = next(row for row in records if row['type'] == 'replay_failure')
        assert failure['drained'] and ('output sha256' if field == 'value' else 'Work') in failure['detail']
        assert failure['workers_idle'] == workers
        assert all(failure[k] == 0 for k in ('cpu_busy_slots', 'cpu_queued', 'pending_admissions'))
        submissions = [r for r in events if r['type'] == 'replay_submit']
        assert max(r['op'] for r in submissions if r['query'] == 1) == target['op']
        assert set(r['query'] for r in submissions) == set(range(1, workers+1))
        assert not any(r['type'] == 'query_result' for r in records)
        return
    assert process.returncode == 0, process.stdout
    resident = next(row for row in records if row['type'] == 'resident_summary')
    assert resident['ssd_requests'] == resident['active_sessions'] == 0
    assert resident['peak_sessions'] == min(len(ids), workers)
    if expected_reads is not None:
        assert resident['reads'] == expected_reads
    assert not any(row['type'] in ('read_submit', 'link', 'device_profile') for row in records + events)
    lifecycle = next(row for row in records if row['type'] == 'lifecycle')
    assert all(lifecycle[k] == 0 for k in ('runtime_tasks', 'waiters', 'leases'))
    services = {row['task']: row for row in records if row['type'] == 'service'}
    visible = {(row['query'], row['op']): row['time_ns'] for row in events
               if row['type'] == 'replay_verified'}
    submitted = {(row['query'], row['op']): row['time_ns'] for row in events
                 if row['type'] == 'replay_submit'}
    dedup_count = 0
    for row in events:
        if row['type'] == 'replay_stage':
            service = services[row['task']]
            key = row['query'], row['op']
            assert submitted[key] <= service['start_ns'] <= service['end_ns'] <= visible[key]
            is_dedup = row['stage'] == 'dedup'
            dedup_count += is_dedup
            assert service['end_ns'] - service['start_ns'] == (max(1, ns) if is_dedup else ns)
        if row['type'] == 'replay_read' and row['op'] > 1:
            assert row['time_ns'] >= visible[row['query'], row['op']-1]
    assert len(visible) == len(submitted) == expected_operations
    results = sorted((row for row in records if row['type'] == 'query_result'),
                     key=lambda row: row['query'])
    summary = next(row for row in records if row['type'] == 'worker_summary')
    assert (summary['workers'], summary['cpu_slots'], summary['max_inflight']) == (workers, workers, inflight)
    assert summary['profile_workers'] == 1
    assert summary['profile_use'] == ('matched' if workers == 1 else 'diagnostic')
    assert summary['cpu_peak_slots'] == summary['peak_workers'] == min(workers, len(ids))
    assert summary['workers_idle'] == workers
    assert all(summary[k] == 0 for k in ('cpu_busy_slots', 'cpu_queued', 'pending_admissions',
                                        'cpu_queue_wait_ns', 'cpu_dependency_wait_ns'))
    assert summary['cpu_busy_ns'] == len(services)*ns + (dedup_count if ns == 0 else 0)
    assert summary['worker_wait_ns'] == sum(r['worker_wait_ns'] for r in results)
    final = next(row for row in records if row['type'] == 'summary')
    assert final['peak_inflight'] == min(len(ids), inflight)
    assert [r['original_query_id'] for r in results] == ids
    assert [r['worker_start_ns'] for r in results] == sorted(r['worker_start_ns'] for r in results)
    completions = sorted(r['visible_ns'] for r in results)
    for i, result in enumerate(results):
        assert result['admission_ns'] == (0 if i < inflight else completions[i-inflight])
        assert result['worker_wait_ns'] == result['worker_start_ns'] - result['admission_ns']
        assert result['latency_ns'] == result['visible_ns'] - result['admission_ns']
        spans = sorted((s['start_ns'], s['end_ns']) for s in services.values() if s['query'] == i+1)
        # No service gaps or overlap within the entire query, including between chunks.
        assert spans[0][0] == result['worker_start_ns'] and spans[-1][1] == result['visible_ns']
        assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))
    for worker in range(workers):
        assigned = [r for r in results if r['worker'] == worker]
        assert all(a['visible_ns'] <= b['worker_start_ns'] for a, b in zip(assigned, assigned[1:]))

    return [r['top_k'] for r in results], [r['latency_ns'] for r in results], len(services)



def recapture(build, output):
    out = output/'recapture'
    out.mkdir()
    for name in ('base.bin', 'queries.bin', 'graph.bin'):
        shutil.copyfile(FIXTURE/name, out/name)
    stale = read(FIXTURE/'binding.json')
    write(out/'stale-binding.json', stale)
    # Remove the only edge 0 -> 1. Both queries now expand and score only entry 0.
    (out/'graph.bin').write_bytes(bytes(16))
    run(build, output/'stale-content', 2, binding_path=out/'stale-binding.json', stale=True)
    (out/'truth.bin').write_bytes(struct.pack('<II', 0, 1))
    (out/'ids.bin').write_bytes(struct.pack('<II', 7, 42))
    files = [(out/name, digest((out/name).read_bytes())) for name in
             ('base.bin', 'queries.bin', 'graph.bin', 'truth.bin', 'ids.bin')]
    binary = build/'ndpsim_native'
    build_id = build_identity(binary, digest(binary.read_bytes()))
    shape = dict(count=2, dimensions=1, queries=2, degree=1)
    search = dict(entry=0, k=1, width=2, batch=2, budget=2, chunk=1)
    binding = binding_for(build_id, files, shape, search)
    binding['source'] = 'synthetic-fixture'
    shards = out/'shards'
    shards.mkdir()
    argv = [str(binary), '2', '1', '2', '1', '1', '0', '1', '2', '2', '2', '1', '1', '1',
            *(str(item) for pair in files for item in pair),
            'static_capture', str(shards), str(2**20), str(2**16), '1']
    process = subprocess.run(argv, capture_output=True, timeout=30)
    write(out/'command.json', dict(argv=argv, exit_code=process.returncode))
    (out/'native.log').write_bytes(process.stdout + process.stderr)
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    package = seal(out, shards, binding, report, Budget(2**20))
    n = read(package/'tail.json')['operations']
    top, _, _ = run(build, output/'new-content', 2, binding_path=out/'binding.json',
                    package=package, expected_operations=n, expected_reads=6)
    assert top == [row['top_k'] for row in report['queries']]
    assert top == [[{'id': 0, 'distance': 0}], [{'id': 0, 'distance': 1}]]


def worker_replay(build, output):
    out = output/'worker-capture'
    out.mkdir()
    files, expected, truth, ids = native_fixture(out)
    binary = build/'ndpsim_native'
    shape = dict(count=6, dimensions=2, queries=11, degree=4)
    search = dict(entry=0, k=4, width=4, batch=2, budget=3, chunk=1)
    binding = binding_for(build_identity(binary, digest(binary.read_bytes())),
                          list(zip(map(Path, files[::2]), files[1::2])), shape, search)
    binding['source'] = 'synthetic-fixture'
    shards = out/'shards'
    shards.mkdir()
    argv = [str(binary), *map(str, (6, 2, 11, 4, 4, 0, 4, 4, 2, 3, 1, 4, 5)), *files,
            'static_capture', str(shards), str(2**20), str(2**16), '2']
    process = subprocess.run(argv, capture_output=True, timeout=30)
    write(out/'command.json', dict(argv=argv, exit_code=process.returncode))
    (out/'native.log').write_bytes(process.stdout + process.stderr)
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    check_native(report, expected, truth, ids, 4, 5)
    package = seal(out, shards, binding, report, Budget(2**20))
    kwargs = dict(binding_path=out/'binding.json', package=package,
                  expected_operations=read(package/'tail.json')['operations'], expected_reads=None)
    serial_top, serial_ns, count = run(build, output/'worker-serial', 2, **kwargs)
    assert serial_top == [r['top_k'] for r in report['queries']]
    assert serial_ns == [56] * len(ids) and count == 28 * len(ids)
    for name, workers, limit, ns, field in (
            ('two', 2, 3, 2, None), ('four', 4, 5, 2, None),
            ('reordered', 4, 5, 2, 'reorder'), ('zero-four', 4, 5, 0, None)):
        dest = output/name
        top, _, services = run(build, dest, ns, field=field, workers=workers, inflight=limit, **kwargs)
        assert top == serial_top and services == count
        actual = sorted((r for r in rows(dest/'records.jsonl') if r['type'] == 'query_result'),
                        key=lambda r: r['query'])
        # Independent earliest-free-worker schedule with the serial service totals.
        # The GBDT has a 1 ns floor, even in the zero-duration fixture.
        dedup_per_query = {i + 1: 0 for i in range(len(ids))}
        for event in rows(dest/'events.jsonl'):
            if event['type'] == 'replay_stage' and event['stage'] == 'dedup':
                dedup_per_query[event['query']] += 1
        free = [0] * workers
        for i, (row, serial) in enumerate(zip(actual, serial_ns)):
            assert row['worker'] == i % workers  # This fixture completes each wave at one tick.
            start = min(free)
            assert row['worker_start_ns'] == start == free[row['worker']]
            assert row['visible_ns'] == start + (serial if ns else dedup_per_query[i + 1])
            free[row['worker']] = row['visible_ns']
    for name in ('records.jsonl', 'events.jsonl'):
        assert (output/'four'/name).read_bytes() == (output/'reordered'/name).read_bytes()
    run(build, output/'parallel-failure', 2, field='value', workers=4, inflight=5, **kwargs)
    run(build, output/'quota-mismatch', 2, workers=2, inflight=3, invalid='quota', **kwargs)
    run(build, output/'profile-mismatch', 2, workers=2, inflight=3, profile_workers=2,
        invalid='profile', **kwargs)
    return dict(native_capture_workers=4, replay_workers=[1, 2, 4], queries=len(ids),
                hand_timeline=True, reordered_identical=True, zero_duration=True,
                concurrent_failure_drained=True, quota_and_profile_mismatch_rejected=True)


def verify(build, output):
    top, fast, service_count = run(build, output/'fast', 2)
    slow_top, slow, slow_count = run(build, output/'slow', 7, backend='memory')
    assert top == slow_top == [[{'id': 0, 'distance': 0}], [{'id': 1, 'distance': 0}]]
    zero_top, zero, zero_count = run(build, output/'zero', 0)
    zero_dedup = [sum(row['type'] == 'replay_stage' and row['stage'] == 'dedup' and row['query'] == query
                      for row in rows(output/'zero/events.jsonl')) for query in (1, 2)]
    assert zero_top == top and zero == zero_dedup and zero_count == service_count
    assert slow_count == service_count and [x * 7 for x in fast] == [x * 2 for x in slow]
    run(build, output/'wrong-value', 2, 'value')
    run(build, output/'wrong-work', 2, 'work')
    recapture(build, output)
    workers = worker_replay(build, output)
    write(output/'checks.json', dict(top_k=top, fast_ns=fast, slow_ns=slow,
                                    cpu_services=service_count, value_and_work_fail_drained=True, workers=workers))


if __name__ == '__main__':
    build = Path(sys.argv[1]).resolve()
    if len(sys.argv) == 3:
        output = Path(sys.argv[2]).resolve()
        output.mkdir(parents=True)
        verify(build, output)
    else:
        with tempfile.TemporaryDirectory(prefix='ndpsim-replay-') as tmp:
            verify(build, Path(tmp))
