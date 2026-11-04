"""Bind an independently selected workload and CPU Profile to resident replay."""
import platform
import shutil
import subprocess
from pathlib import Path
from time import monotonic

from ann_data import write_json
from v04_contract import (bounded_read, canonical, decode, digest, file_hash,
                          require, validate_config, validate_package)


def prepare(config, config_dir, output, snapshot, params, query_ids, timeout):
    def resolve(path):
        return (config_dir / path).resolve()

    effective = dict(format='ndpsim-effective-config/1', execution_engine='replay',
                     placement=config['placement'], content_backend=params['content_backend'],
                     data_provider=config['data_provider'], order_policy=config['order_policy'],
                     native_workers=config['native_workers'], max_inflight=params['max_inflight'],
                     cpu_slots=params['slots'], ir_input=dict(config['ir_input']),
                     cpu_profile=dict(config['cpu_profile']))
    params.update(data_provider=effective['data_provider'], native_workers=effective['native_workers'],
                  order_policy=effective['order_policy'])
    effective['mapping_sha256'] = digest(canonical({
        key: value for key, value in params.items()
        if key not in ('records', 'output', 'data', 'semantic_output', 'event_log')
    }))
    validate_config(effective)
    require(effective['data_provider'] == 'host_memory',
            'Unsupported: replay currently requires host_memory')
    fixture = config.get('allow_replay_fixture', False)
    require(type(fixture) is bool, 'allow_replay_fixture must be boolean')
    binding_path = resolve(config['ir_binding'])
    binding_raw = bounded_read(binding_path)
    binding = decode(binding_raw)
    require(binding['source'] == ('synthetic-fixture' if fixture else 'native-capture'),
            'replay source requires matching fixture permission')
    identity = binding['identity']
    require(identity['query_ids'] == query_ids, 'replay original query IDs/order mismatch')
    shape = (('base', 'vector_file', 'vectors.f32', 'count', 'dimensions'),
             ('graph', 'graph_file', 'adjacency.u32', 'count', 'degree'),
             ('queries', 'query_file', 'queries.f32', 'queries', 'dimensions'))
    payload_bytes = 0
    for name, key, default, count, width in shape:
        path = Path(params[key] if key in params else Path(params['data']) / default)
        obj = identity['objects'][name]
        require((obj['records'], obj['width']) == (params[count], params[width]) and
                path.resolve() == (binding_path.parent / binding['files'][name]).resolve(),
                f'replay actual object/shape mismatch: {name}')
        payload_bytes += path.stat().st_size
    require(payload_bytes < params.get('resource_budget_bytes', 4*1024**3),
            'replay input exceeds resident RSS budget')
    require(identity['search'] == {target: params[key] for target, key in
            zip(('entry', 'k', 'L', 'B', 'E', 'chunk'),
                ('entry', 'k', 'width', 'batch', 'budget', 'chunk'))}, 'replay search mismatch')
    ir = effective['ir_input']
    package = resolve(ir['path'])
    deadline = monotonic() + timeout

    def checkpoint():
        require(monotonic() <= deadline, 'replay preflight timeout')

    # Validate the exact copy SST will consume. The private directory is removed
    # after SST exits, leaving the original package and the preflight receipt.
    names = ('manifest.json', 'index.jsonl', 'operations.jsonl', 'tail.json')
    size = sum((package / name).stat().st_size for name in names)
    require(size <= params['output_budget_bytes'] and size < shutil.disk_usage(snapshot).free,
            'replay snapshot exceeds configured byte budget or free disk')
    for name in names:
        with (package / name).open('rb') as source, (snapshot / name).open('xb') as target:
            while block := source.read(1024 * 1024):
                checkpoint()
                size -= len(block)
                require(size >= 0, 'replay source grew during snapshot')
                target.write(block)
    receipt = validate_package(snapshot, binding_path, checkpoint=checkpoint)
    require(receipt['package_sha256'] == ir['sha256'], 'configured replay package hash mismatch')
    require(file_hash(binding_path) == digest(binding_raw), 'replay binding changed')
    profile = effective['cpu_profile']
    profile_path = resolve(profile['path'])
    if fixture:
        host = config['fixture_host']
    else:
        def sysctl(key):
            return subprocess.check_output(['sysctl', '-n', key], text=True, timeout=10).strip()
        host = dict(hardware=sysctl('machdep.cpu.brand_string'), os=platform.platform(),
                    memory_bytes=int(sysctl('hw.memsize')), frequency='Unsupported',
                    affinity='uncontrolled')
    admission = dict(package=str(snapshot), allow_fixture=fixture, profile_sha256=profile['sha256'],
                     query_ids=query_ids, order_policy=effective['order_policy'],
                     objects={name: obj['sha256'] for name, obj in identity['objects'].items()},
                     applicability=dict(host, build=binding['build']))
    write_json(output/'replay-admission.json', admission)
    write_json(output/'effective-config.json', effective)
    write_json(output/'replay-preflight.json', dict(receipt, binding_sha256=digest(binding_raw)))
    params.update(cpu_profile=str(profile_path),
                  replay_config=str(output/'replay-admission.json'))
    return receipt
