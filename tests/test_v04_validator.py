"""Run the Validator through native capture, resident replay and both SSD references."""
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))

from ann_data import write_json
from benchmark_artifacts import binding, exact, graph_check, LOCK, PARAMETERS, record, VERSION
from test_benchmark_artifacts import fixture
from test_static_replay import modified_package
from validate_v04 import level_c_thresholds_met
from v04_contract import STAGES, file_hash, package_hash


def load(path):
    return json.loads(path.read_text())


def register(directory, *names):
    path = directory/'manifest.json'
    manifest = load(path)
    for name in names:
        manifest['artifacts_sha256'][name] = file_hash(directory/name)
    write_json(path, manifest)


def run(out, build):
    assert level_c_thresholds_met(dict(qps=.01, cpu_ns=.10))
    assert not level_c_thresholds_met(dict(qps=.01, cpu_ns=.16))
    assert not level_c_thresholds_met(dict(qps=.01, cpu_ns=None))
    commands = []

    def call(tool, *args, expected=0):
        argv = [sys.executable, str(ROOT/'tools'/tool), *map(str, args)]
        process = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        commands.append(dict(argv=argv, exit_code=process.returncode, stdout=process.stdout,
                             stderr=process.stderr))
        write_json(out/'commands.json', commands)
        assert process.returncode == expected, (argv, process.stdout, process.stderr)

    inputs = out/'inputs'
    inputs.mkdir()
    dataset, _, _ = fixture(inputs)
    data = inputs/'data'
    index = inputs/'index'
    index.mkdir()
    graph = index/'adjacency.u32'
    graph.write_bytes(struct.pack('<16I', 3, 1, 1, 0, 1, 2, 0, 0, 1, 3, 0, 0, 0, 0, 0, 0))
    mapping = index/'nodes.u32'
    mapping.write_bytes((data/'base.ids.u32').read_bytes())
    lock = load(LOCK)
    write_json(index/'index.json', dict(format='ndpsim-index-v1', validator=VERSION,
        binding=binding(dataset, ('base',)), max_degree=3, entry=0, graph=record(graph),
        mapping=record(mapping), validation=graph_check(graph, 4, 3, 0),
        builder=dict(lock_sha256=file_hash(LOCK), commit=lock['faiss_commit'], parameters=PARAMETERS,
                     rng=lock['seed_policy'], threads=1)))
    truth = inputs/'truth'
    exact(data, truth, 4)
    config = load(ROOT/'configs/archive/v0.1/m3.1/host.json')
    config.update(format='ndpsim-experiment-v2', index=str(index), truth=str(truth),
                  semantic_log=True, event_log=True, device_profile='native-small')
    config['search'].update(entry=0, k=4, width=4, batch=2, budget=8, chunk=1)
    cfg = out/'native-config.json'
    write_json(cfg, config)
    for mode in ('static_capture', 'uninstrumented', 'cpu_sample'):
        extra = ['--detail8'] if mode == 'static_capture' else []
        call('run_native.py', '--config', cfg, '--data', data, '--binary', build/'ndpsim_native',
             '--output', out/mode, '--mode', mode, *extra)

    capture = out/'static_capture'
    binding_path = capture/'binding.json'
    package = capture/'package'
    bound = load(binding_path)
    # Use explicit fixture admission so the test does not need hardware sysctls.
    for path in (binding_path, package/'manifest.json'):
        write_json(path, dict(load(path), source='synthetic-fixture'))
    tail = dict(load(package/'tail.json'), manifest_sha256=file_hash(package/'manifest.json'))
    tail['package_sha256'] = package_hash(tail)
    write_json(package/'tail.json', tail)
    register(capture, 'binding.json', 'package/manifest.json', 'package/tail.json')
    host = dict(hardware='fixture', os='fixture', memory_bytes=64,
                frequency='Unsupported', affinity='uncontrolled')
    profile = load(ROOT/'examples/v04-contract/cpu-profile.json')
    base = bound['identity']['objects']['base']
    profile['applicability'].update(host, build=bound['build'], dimensions=base['width'], records=base['records'])
    profile['training']['query_ids'] = [9999]
    exemplar = profile['models'][0]
    profile['models'] = [
        dict(exemplar, operator=op, stage=stage, model=dict(kind='constant', ns=2000),
             features=[dict(name='read_bytes', unit='B', minimum=0, maximum=100000)])
        for op, stages in STAGES.items() for stage in stages if stage != 'state']
    model = out/'profile.json'
    write_json(model, profile)
    for engine in ('replay', 'direct', 'ir'):
        settings = dict(config, execution_engine=engine)
        if engine == 'replay':
            settings.update(data_provider='host_memory', order_policy='worker-fifo/1', native_workers=1,
                            allow_replay_fixture=True, fixture_host=host, ir_binding=str(binding_path),
                            ir_input=dict(path=str(package), sha256=tail['package_sha256']),
                            cpu_profile=dict(path=str(model), sha256=file_hash(model)))
        cfg = out/(engine+'-config.json')
        write_json(cfg, settings)
        call('run_graph.py', '--config', cfg, '--data', data, '--build', build, '--output', out/engine)

    schema = load(ROOT/'schemas/ndpsim-validator-report-v1.schema.json')
    paths = dict(capture=capture, baseline=out/'uninstrumented', samples=out/'cpu_sample',
                 profile=model, replay=out/'replay', direct=out/'direct', ir=out/'ir')

    def check(name, error=None, **override):
        dest = out/name
        args = [item for key, value in (paths | override).items() for item in ('--'+key, value)]
        call('validate_v04.py', '--scope', 'quick', '--output', dest, *args, expected=int(error is not None))
        result = load(dest/'report.json')
        Draft202012Validator(schema).validate(result)
        assert result['status'] == ('failed' if error else 'diagnostic'), result
        assert set(result['levels'].values()) == {'not_run', 'not_applicable'}
        if error:
            assert error in result['checks'][-1]['detail'], result['checks']
        return result

    positive = check('valid')
    assert len(positive['queries']) == len(bound['identity']['query_ids'])
    assert all(c['status'] == 'passed' for c in positive['checks'])
    check('missing-package', 'manifest.json', capture=out/'absent')
    check('wrong-native-mode', 'mode/split/build/input', baseline=out/'static_capture')

    overlapping = out/'overlap-profile.json'
    profile['training']['query_ids'] = bound['identity']['query_ids']
    write_json(overlapping, profile)
    check('overlap', 'original query ID overlap', profile=overlapping)

    def rebind(label, altered, key, hash_value, **receipt_fields):
        dest = out/('rebind-'+label)
        shutil.copytree(paths['replay'], dest)
        for name in ('config.json', 'effective-config.json'):
            doc = load(dest/name)
            doc[key] = dict(path=str(altered), sha256=hash_value)
            write_json(dest/name, doc)
        for name, fields in receipt_fields.items():
            filename = name.replace('_', '-')+'.json'
            write_json(dest/filename, load(dest/filename) | fields)
        m = load(dest/'manifest.json')
        m['replay_config_sha256'] = file_hash(dest/'effective-config.json')
        write_json(dest/'manifest.json', m)
        register(dest, 'config.json', 'effective-config.json',
                 *(name.replace('_', '-')+'.json' for name in receipt_fields))
        return dest

    for field in ('value', 'work', 'dependency', 'state'):
        altered = out/('capture-'+field)
        shutil.copytree(capture, altered)
        shutil.rmtree(altered/'package')
        modified_package(package, altered/'package', field)
        if field in ('value', 'work'):
            register(altered, *(f'package/{f}' for f in
                                ('operations.jsonl', 'index.jsonl', 'tail.json')))
            sha = load(altered/'package/tail.json')['package_sha256']
            replay = rebind(field, altered/'package', 'ir_input', sha,
                            replay_preflight=dict(package_sha256=sha))
            # Change only the cross-artifact receipts. Actual replay events are
            # from the unmodified workload and must stay intact.
            check('package-'+field, 'capture value/shape' if field == 'value' else 'capture Work',
                  capture=altered, replay=replay)
        else:
            check('package-'+field, field, capture=altered)

    wrong_profile = load(model)
    wrong_profile['models'][0]['features'][0]['minimum'] = 1
    bounded = out/'bounded-profile.json'
    write_json(bounded, wrong_profile)
    check('wrong-profile-hash', 'package/Profile SHA256', profile=bounded)
    replay = rebind('profile', bounded, 'cpu_profile', file_hash(bounded),
                    replay_admission=dict(profile_sha256=file_hash(bounded)))
    check('out-of-model-range', 'outside model range', profile=bounded, replay=replay)

    wrong_build = out/'wrong-build-baseline'
    shutil.copytree(paths['baseline'], wrong_build)
    manifest = load(wrong_build/'manifest.json')
    manifest['build']['binary_sha256'] = '0'*64
    write_json(wrong_build/'manifest.json', manifest)
    check('wrong-build', 'mode/split/build/input', baseline=wrong_build)

    for name, kind, change, error in [
        ('cpu', 'service', lambda r: r.update(end_ns=r['end_ns']+1), 'CPU service demand'),
        ('work', 'replay_verified', lambda r: r['work'].update(flops=99), 'actual ordered IDs/Work/value'),
        ('read', 'replay_read', lambda r: r.update(bytes=99), 'actual logical access'),
        ('missing-event', 'replay_stage', None, 'read outside preparation stage'),
        ('drain', 'lifecycle', lambda r: r.update(leases=1), 'drain differs'),
    ]:
        altered = out/('replay-'+name)
        shutil.copytree(paths['replay'], altered)
        trace = altered/'events.jsonl'
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        event = next(r for r in events if r['type'] == kind)
        if change:
            change(event)
        else:
            events.remove(event)
        trace.write_text(''.join(json.dumps(r)+'\n' for r in events))
        register(altered, 'events.jsonl')
        check('event-'+name, error, replay=altered)

    altered = out/'samples-incomplete'
    shutil.copytree(paths['samples'], altered)
    measurement = altered/'measurement.json'
    content = load(measurement)
    content['samples'].pop()
    write_json(measurement, content)
    register(altered, 'measurement.json')
    check('missing-sample', 'missing/unplanned CPU samples', samples=altered)


if __name__ == '__main__':
    build = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2:
        output = Path(sys.argv[2]).resolve()
        output.mkdir(parents=True)
        run(output, build)
    else:
        with tempfile.TemporaryDirectory(prefix='ndpsim-validator-') as directory:
            run(Path(directory), build)
