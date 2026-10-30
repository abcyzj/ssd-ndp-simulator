"""Generate/validate inputs, run real SST/MQSim host graph search, report Recall@k."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from experiment_output import export
from pathlib import Path
from ann_data import generate, read_dataset, exact_truth, digest, write_json, integer

ROOT = Path(__file__).resolve().parents[1]


def run(config, build, output, data=None, *, paired=False):
    config, build, output = Path(config).resolve(), Path(build).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    c = json.loads(config.read_text())
    if c['placement'] not in (('host', 'controller') if paired else ('host',)) or c['device_profile'] != 'native-small':
        raise ValueError('Unsupported: requires supported placement/native-small')
    integer(c['max_inflight'], 'max_inflight')
    event_log = c.get('event_log', False)
    if type(event_log) is not bool:
        raise ValueError('event_log must be boolean')
    # Remove stale artifacts when reusing an output directory.
    for name in ('events.jsonl', 'results.json', 'statistics.json', 'manifest.json'):
        (output/name).unlink(missing_ok=True)
    if not c['parameter_source']:
        raise ValueError('parameter_source is required')
    if data is None:
        data = output / 'data'
        generate(data, **c['generate'])
        exact_truth(data, c['search']['k'])
    data = Path(data).resolve()
    m, _, _ = read_dataset(data)
    s, h = c['search'], c['host']
    required_search = {'entry', 'k', 'width', 'batch', 'budget', 'chunk'}
    required_host = {'slots', 'distance_setup', 'distance_rate', 'control_setup', 'control_rate', 'check_units', 'comparison_units'}
    if set(s) != required_search or set(h) != required_host:
        raise ValueError('search/host configuration keys differ from schema')
    for key, val in s.items():
        integer(val, key, 0 if key == 'entry' else 1)
    for key, val in h.items():
        integer(val, key, 1 if key in ('slots', 'distance_rate', 'control_rate') else 0, 2**64-1)
    if s['entry'] >= m['count'] or s['k'] > m['count'] or s['width'] < s['k']:
        raise ValueError('invalid entry/k/L')
    truth = json.loads((data / 'truth.json').read_text())
    if truth['k'] != s['k'] or truth['vectors_sha256'] != m['sha256']['vectors.f32'] or truth['queries_sha256'] != m['sha256']['queries.f32']:
        raise ValueError('ground truth does not match data/query/k')
    if len(truth['top_k']) != m['queries']:
        raise ValueError('ground truth query count mismatch')
    for row in truth['top_k']:
        if len(row) != s['k'] or len({v['id'] for v in row}) != s['k']:
            raise ValueError('invalid truth cardinality')
        for v in row:
            integer(v['id'], 'truth ID', 0, m['count']-1)
            if not isinstance(v['distance'], (float, int)) or not 0 <= v['distance'] < float('inf'):
                raise ValueError('invalid truth distance')
    paths = json.loads((build / 'sst-paths.json').read_text())
    params = {key: m[key] for key in ('count', 'dimensions', 'degree', 'queries')}
    params.update(s)
    params.update(h)
    if paired:
        required = {'capacity_bytes', 'staging_bytes_per_ns', 'controller_slots', 'launch_ns', 'flops_per_ns', 'scratch_bytes', 'vector_sector_bound'}
        if set(c['controller']) != required:
            raise ValueError('controller configuration keys differ from schema')
        for key, val in c['controller'].items():
            integer(val, key, 0 if key in ('launch_ns', 'scratch_bytes') else 1, 2**64-1)
        params.update(c['controller'])
        params['placement'] = c['placement']
    params.update(data=str(data), output=str(output / 'events.jsonl'), records=str(output/'records.jsonl'),
                  event_log=int(event_log), max_inflight=c['max_inflight'])
    write_json(output / 'sst-params.json', params)
    write_json(output / 'config.json', c)
    vs = (4*m['dimensions']+511)//512*512
    ads = (4+4*m['degree']+511)//512*512
    write_json(output / 'layout.json', dict(sector_bytes=512, vector_base=0, vector_stride=vs,
               adjacency_base=m['count']*vs, adjacency_stride=ads,
               end_lba=m['count']*(vs+ads)//512, query_residency='HostMemory + DeviceBuffer until CloseAck' if c['placement']=='controller' else 'HostMemory', data_residency='SSDStorage'))
    shutil.copyfile(ROOT / 'configs/native/ssdconfig.xml', output / 'ssdconfig.xml')
    argv = [paths['sst'], '--add-lib-path='+str(build), str(ROOT / 'configs/sst/graph.py')]
    env = dict(os.environ, NDPSIM_M31_PARAMS=str(output / 'sst-params.json'))
    print('SST command:', ' '.join(argv), flush=True)
    wall_start = time.perf_counter()
    p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    (output / 'sst.log').write_text(p.stdout + p.stderr)
    write_json(output / 'command.json', dict(argv=argv, exit_code=p.returncode, params=str(output / 'sst-params.json')))
    if p.returncode:
        raise RuntimeError(f'SST exited {p.returncode}; see {output / "sst.log"}')
    wall_seconds = time.perf_counter()-wall_start
    events = [json.loads(line) for line in (output / 'records.jsonl').read_text().splitlines()]
    if event_log:
        with (output/'events.jsonl').open('a') as stream:
            for row in events:
                if row['type'] != 'query_result': stream.write(json.dumps(row)+'\n')
    (output/'records.jsonl').unlink()
    results = sorted([e for e in events if e['type'] == 'query_result'], key=lambda r:r['query'])
    if len(results) != m['queries'] or len([e for e in events if e['type'] == 'summary']) != 1:
        raise ValueError('incomplete SST results')
    for i, result in enumerate(results):
        if result['query'] != i+1:
            raise ValueError('out of order queries')
        expected = {v['id'] for v in truth['top_k'][i]}
        returned = {v['id'] for v in result['top_k']}
        if len(returned) != len(result['top_k']) or len(returned) > s['k']:
            raise ValueError('invalid result cardinality')
        result['recall_at_k'] = len(expected & returned)/s['k']
    report = dict(k=s['k'], recall_definition='mean |returned IDs intersect exact top-k IDs| / k; ties by ID',
                  recall_at_k=sum(r['recall_at_k'] for r in results)/len(results), queries=results,
                  summary=next(e for e in events if e['type'] == 'summary'))
    export(output, results, events, wall_seconds)
    write_json(output / 'results.json', report)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    mqsim = ROOT / 'third_party/MQSim'
    base = json.loads((ROOT/'deps.lock.json').read_text())['dependencies']['MQSim']['commit']
    names = subprocess.check_output(['git','-C',str(mqsim),'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
    source_files = [ROOT/'CMakeLists.txt', ROOT/'deps.lock.json', *sorted((ROOT/'src').rglob('*.cc')),
                    *sorted((ROOT/'src').rglob('*.h')), ROOT/'tools/ann_data.py', Path(__file__), ROOT/'configs/sst/graph.py', ROOT/'tools/run_pair.py', ROOT/'tools/compare_placements.py', ROOT/'tools/experiment_output.py', ROOT/'tools/run_experiment.py']
    write_json(output / 'manifest.json', dict(data=str(data), dataset=m, config_sha256=digest(config),
        truth_sha256=digest(data/'truth.json'), code_commit=git('rev-parse', 'HEAD'),
        source_sha256={str(p.relative_to(ROOT)):digest(p) for p in source_files},
        element_sha256=digest(build/'libndpsim.so'),
        mqsim_native_base=base,
        mqsim_diff_from_base_sha256=hashlib.sha256(subprocess.check_output(['git','-C',str(mqsim),'diff','--binary',base,'--'])).hexdigest(),
        mqsim_source_sha256={n:digest(mqsim/n) if (mqsim/n).is_file() else None for n in sorted(set(filter(None,names)))},
        code_diff_sha256=hashlib.sha256(subprocess.check_output(['git', 'diff', 'HEAD'], cwd=ROOT)).hexdigest(),
        mqsim_commit=git('-C', 'third_party/MQSim', 'rev-parse', 'HEAD'),
        sst_commit=git('-C', 'third_party/sst-core', 'rev-parse', 'HEAD'), parameter_source=c['parameter_source'],
        artifacts_sha256={name:digest(output/name) for name in ('config.json','layout.json','ssdconfig.xml','results.json','statistics.json','capabilities.json','sst-params.json', *sorted(p.name for p in output.glob('*.csv')), *(['events.jsonl'] if event_log else []))},
        assumptions=['single SSD/native-small/SIMPLE/TURNED_OFF; CMT retained',
                     'deterministic page preload drained before measurement; no cross-query object cache',
                     'all queries offered at measurement origin, FIFO bounded admission; slots retire after CloseAck; host control; distance placement='+c['placement'],
                     'MQSim owns ordinary PCIe; no platform transfer fee',
                     'configured_unvalidated hardware costs; simplified graph, no DiskANN claim']))
    print(f"{('M3.4 '+c['placement']) if paired else 'M3.1 host'} queries={len(results)} Recall@{s['k']}={report['recall_at_k']:.8f} output={output}")
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'configs/archive/v0.1/m3.1/host.json')
    p.add_argument('--build', type=Path, default=ROOT/'build/m3.1')
    p.add_argument('--output', type=Path, default=ROOT/'results/m3.1-host')
    p.add_argument('--data', type=Path, help='Existing dataset with independently generated truth.json')
    a = p.parse_args()
    run(a.config, a.build, a.output, a.data)
