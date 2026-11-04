"""Exercise preflight, actual replay causality, re-costing and failure drain through SST."""
import json
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

FIXTURE = ROOT/'examples/v04-contract'


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_bytes(canonical(value) + b'\n')


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def modified_package(path, field):
    shutil.copytree(FIXTURE/'package', path)
    ops = rows(path/'operations.jsonl')
    target = next(row for row in ops if row['operator'] == ('SCORE_L2' if field == 'value'
                                                             else 'DEDUP_NEIGHBORS'))
    if field == 'value':
        target['expected']['output']['sha256'] = 'f'*64
    else:
        target['expected']['work']['comparisons'] += 1
    index = rows(path/'index.jsonl')
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
        package=FIXTURE/'package', stale=False, expected_operations=38, expected_reads=10):
    out.mkdir()
    binding = read(binding_path)
    if field:
        package = out/'bad-package'
        target = modified_package(package, field)
    profile = read(FIXTURE/'cpu-profile.json')
    profile['applicability']['build'] = binding['build']
    exemplar = profile['models'][0]
    profile['models'] = [
        dict(exemplar, operator=op, stage=stage, features=[], model=dict(kind='constant', ns=ns))
        for op, stages in STAGES.items() for stage in stages if stage != 'state'
    ]
    # A constant model still needs a declared, bounded feature.
    for model in profile['models']:
        model['features'] = [dict(name='dimensions', unit='count', minimum=1, maximum=1)]
    profile_path = out/'profile.json'
    write(profile_path, profile)
    paths = {name: str(binding_path.parent / file) for name, file in binding['files'].items()}
    host = {k: profile['applicability'][k] for k in
            ('hardware', 'os', 'memory_bytes', 'frequency', 'affinity')}
    config = dict(placement='host', data_provider='host_memory', order_policy='worker-fifo/1',
                  native_workers=1, allow_replay_fixture=True, fixture_host=host,
                  ir_binding=str(binding_path),
                  ir_input=dict(path=str(package), sha256=read(package/'tail.json')['package_sha256']),
                  cpu_profile=dict(path=str(profile_path), sha256=digest(profile_path.read_bytes())))
    params = dict(execution_engine='replay', count=2, dimensions=1, degree=1, queries=2,
                  entry=0, k=1, width=2, batch=2, budget=2, chunk=1, slots=1, max_inflight=1,
                  vector_file=paths['base'], graph_file=paths['graph'], query_file=paths['queries'],
                  content_backend=backend, output_budget_bytes=2**20, event_log=1,
                  records=str(out/'records.jsonl'), output=str(out/'events.jsonl'))
    snapshot = out/'snapshot'
    snapshot.mkdir()
    if stale:
        try:
            prepare(config, ROOT, out, snapshot, params, [7, 42], 30)
        except ValueError as error:
            assert 'bound object mismatch: graph' in str(error)
            write(out/'preflight-failure.json', dict(detail=str(error)))
            return
        raise AssertionError('stale content was admitted')
    prepare(config, ROOT, out, snapshot, params, [7, 42], 30)
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
    records = rows(out/'records.jsonl')
    events = rows(out/'events.jsonl')
    if field:
        assert process.returncode != 0, process.stdout
        failure = next(row for row in records if row['type'] == 'replay_failure')
        assert failure['drained'] and ('output sha256' if field == 'value' else 'Work') in failure['detail']
        assert max(row['op'] for row in events if row['type'] == 'replay_submit') == target['op']
        assert all(row.get('query') != 2 for row in events)
        return
    assert process.returncode == 0, process.stdout
    resident = next(row for row in records if row['type'] == 'resident_summary')
    assert resident['ssd_requests'] == resident['active_sessions'] == 0
    assert resident['peak_sessions'] == 1 and resident['reads'] == expected_reads
    assert not any(row['type'] in ('read_submit', 'link', 'device_profile') for row in records + events)
    lifecycle = next(row for row in records if row['type'] == 'lifecycle')
    assert all(lifecycle[k] == 0 for k in ('runtime_tasks', 'waiters', 'leases'))
    services = {row['task']: row for row in records if row['type'] == 'service'}
    visible = {(row['query'], row['op']): row['time_ns'] for row in events
               if row['type'] == 'replay_verified'}
    submitted = {(row['query'], row['op']): row['time_ns'] for row in events
                 if row['type'] == 'replay_submit'}
    for row in events:
        if row['type'] == 'replay_stage':
            service = services[row['task']]
            key = row['query'], row['op']
            assert submitted[key] <= service['start_ns'] <= service['end_ns'] <= visible[key]
            assert service['end_ns'] - service['start_ns'] == ns
        if row['type'] == 'replay_read' and row['op'] > 1:
            assert row['time_ns'] >= visible[row['query'], row['op']-1]
    assert len(visible) == len(submitted) == expected_operations
    results = [row for row in records if row['type'] == 'query_result']
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


def verify(build, output):
    top, fast, service_count = run(build, output/'fast', 2)
    slow_top, slow, slow_count = run(build, output/'slow', 7, backend='memory')
    assert top == slow_top == [[{'id': 0, 'distance': 0}], [{'id': 1, 'distance': 0}]]
    zero_top, zero, zero_count = run(build, output/'zero', 0)
    assert zero_top == top and zero == [0, 0] and zero_count == service_count
    assert slow_count == service_count and [x * 7 for x in fast] == [x * 2 for x in slow]
    run(build, output/'wrong-value', 2, 'value')
    run(build, output/'wrong-work', 2, 'work')
    recapture(build, output)
    write(output/'checks.json', dict(top_k=top, fast_ns=fast, slow_ns=slow,
                                    cpu_services=service_count, value_and_work_fail_drained=True))


if __name__ == '__main__':
    build = Path(sys.argv[1]).resolve()
    if len(sys.argv) == 3:
        output = Path(sys.argv[2]).resolve()
        output.mkdir(parents=True)
        verify(build, output)
    else:
        with tempfile.TemporaryDirectory(prefix='ndpsim-replay-') as tmp:
            verify(build, Path(tmp))
