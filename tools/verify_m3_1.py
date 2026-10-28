"""Reproducible M3.1 acceptance: real SST/MQSim, independent oracle, causal branch."""
import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from ann_data import generate, exact_truth, read_dataset, digest, write_json
from verify_m1_1 import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/evidence/m3.1'
OUT.mkdir(parents=True, exist_ok=True)
commands, assertions = [], []


def run(name, argv):
    argv = list(map(str, argv))
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600)
    (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode))
    if p.returncode:
        raise RuntimeError(f'{name} exit={p.returncode}; see {OUT}')
    return p.stdout


def experiment(name, config, data=None):
    argv = [sys.executable, ROOT/'tools/run_m3_1.py', '--config', config, '--build', ROOT/'build/m3.1', '--output', OUT/name]
    if data:
        argv += ['--data', data]
    run(name, argv)
    return [json.loads(line) for line in (OUT/name/'events.jsonl').read_text().splitlines()]


def check_run(name, events, config):
    path = OUT/name
    m, vectors, queries = read_dataset(path/'data')
    search, host = config['search'], config['host']
    decisions = [e for e in events if e['type']=='decision']
    # Independent replay of decisions from only the vectors actually returned.
    # This does not provide an access trace to the simulator.
    scored, expanded, pending = {}, {}, {}
    issued_adjacency, expected_adjacency = [], []
    complete = {}
    sector_bytes, useful_bytes = 0, 0
    for e in events:
        q = e.get('query')
        if e['type']=='read_submit':
            assert e['request'] not in pending
            pending[e['request']] = e
            vs = (4*m['dimensions']+511)//512
            ads = (4+4*m['degree']+511)//512
            lba = e['id']*vs if e['kind']=='vector' else m['count']*vs+e['id']*ads
            assert e['lba']==lba and e['sectors']>0
            sector_bytes += e['sectors']*512
            if e['kind']=='adjacency':
                issued_adjacency.append((q,e['id']))
            else:
                assert e['id'] not in scored.setdefault(q,{})
        elif e['type']=='read_complete':
            r = pending.pop(e['request'])
            assert e['task']==r['task'] and e['query']==r['query']
            assert (e['lba'],e['sectors'])==(r['lba'],r['sectors']) and e['time_ns']>=r['time_ns']
            complete[e['request']] = e['time_ns']
            if r['kind']=='vector':
                total=0.0
                for a,b in zip(queries[q-1],vectors[r['id']]):
                    delta=a-b
                    total=total+delta*delta
                scored.setdefault(q,{})[r['id']]=total
                useful_bytes += m['dimensions']*4
            else:
                ab=(path/'data/adjacency.u32').read_bytes()
                degree=struct.unpack_from('<I',ab,r['id']*(m['degree']+1)*4)[0]
                useful_bytes += 4+degree*4
        elif e['type']=='decision':
            ranking = sorted(scored[q].items(),key=lambda v:(v[1],v[0]))[:search['width']]
            assert [(v['id'],v['distance']) for v in e['candidates']]==ranking
            seen=expanded.setdefault(q,set())
            expected=[node for node,_ in ranking if node not in seen][:min(search['batch'],search['budget']-len(seen))]
            assert e['expand']==expected
            expected_adjacency += [(q,node) for node in expected]
            seen.update(expected)
        elif e['type']=='query_result':
            ranking=sorted(scored[q].items(),key=lambda v:(v[1],v[0]))[:search['k']]
            assert [(v['id'],v['distance']) for v in e['top_k']]==ranking
            assert e['expanded']==len(expanded[q]) and e['expanded']<=search['budget']
            assert e['insufficient']==(len(ranking)<search['k'])
            assert e['end_ns']-e['start_ns']==e['latency_ns']>0
        elif e['type']=='service':
            units=e['flops'] if e['kind']=='distance' else e['checks']*host['check_units']+e['comparisons']*host['comparison_units']
            rate=host[e['kind']+'_rate']; setup=host[e['kind']+'_setup']
            assert e['end_ns']-e['start_ns']==setup+(units+rate-1)//rate
    assert not pending and issued_adjacency==expected_adjacency
    summary=next(e for e in events if e['type']=='summary')
    assert summary['live_results']==summary['live_content']==0
    assert summary['sector_bytes']==sector_bytes and summary['useful_bytes']==useful_bytes
    assert len(decisions)>=m['queries']


try:
    lock=json.loads((ROOT/'deps.lock.json').read_text())
    cmake=Path(lock['tools']['cmake']['path']);ctest=cmake.with_name('ctest');build=ROOT/'build/m3.1'
    run('configure',[cmake,'-S',ROOT,'-B',build])
    run('build',[cmake,'--build',build,'-j4'])
    run('ctest',[ctest,'--test-dir',build,'--output-on-failure'])
    assertions.append('root CMake default shared SST cache; full CTest including real SST graph and isolated graph semantics')
    base=json.loads((ROOT/'configs/m3.1/branch.json').read_text())
    branch=experiment('branch',ROOT/'configs/m3.1/branch.json')
    mutated=json.loads(json.dumps(base));mutated['generate']['fixture']='mutated'
    write_json(OUT/'mutated-config.json',mutated)
    changed=experiment('mutated',OUT/'mutated-config.json')
    check_run('branch',branch,base);check_run('mutated',changed,mutated)
    a=OUT/'branch/data';b=OUT/'mutated/data'
    assert (a/'adjacency.u32').read_bytes()==(b/'adjacency.u32').read_bytes()
    assert (a/'queries.f32').read_bytes()==(b/'queries.f32').read_bytes()
    va,vb=(a/'vectors.f32').read_bytes(),(b/'vectors.f32').read_bytes()
    assert va[:4]==vb[:4] and va[8:]==vb[8:] and struct.unpack_from('<f',va,4)==(1.0,) and struct.unpack_from('<f',vb,4)==(5.0,)
    for events,node,lba in [(branch,1,6),(changed,2,7)]:
        ds=[e for e in events if e['type']=='decision']
        assert ds[0]['expand']==[0] and ds[1]['expand']==[node]
        adjacency=[e for e in events if e['type']=='read_submit' and e['kind']=='adjacency']
        assert [(e['id'],e['lba']) for e in adjacency]==[(0,5),(node,lba)]
        assert adjacency[1]['time_ns']>=ds[1]['time_ns']
        assert all(e['time_ns']<=ds[1]['time_ns'] for e in events if e['type']=='read_complete' and e['task'] in [r['task'] for r in events if r['type']=='read_submit' and r['kind']=='vector' and r['id'] in (1,2)])
    ra=json.loads((OUT/'branch/results.json').read_text());rb=json.loads((OUT/'mutated/results.json').read_text())
    assert ra['recall_at_k']==1 and rb['recall_at_k']==0.5
    assert json.loads((b/'truth.json').read_text())['top_k'][0]==[dict(id=3,distance=4.0),dict(id=2,distance=9.0)]
    assertions.append('AC-02 host: only x1=1->5; second expansion 1->2; independent adjacency LBA 6->7; distance/decision/submit causal order; exact Recall@2=1/0.5')
    smoke=json.loads((ROOT/'configs/m3.1/host.json').read_text())
    events=experiment('smoke',ROOT/'configs/m3.1/host.json')
    repeat=experiment('smoke-repeat',ROOT/'configs/m3.1/host.json')
    check_run('smoke',events,smoke)
    assert events==repeat
    ma=json.loads((OUT/'smoke/data/dataset.json').read_text());mb=json.loads((OUT/'smoke-repeat/data/dataset.json').read_text())
    assert ma==mb
    assert (OUT/'smoke/data/truth.json').read_bytes()==(OUT/'smoke-repeat/data/truth.json').read_bytes()
    assertions.append('default N2048/d32/R16/Q32/k10/L32/B1/E64/chunk16/seed42: all decisions and top-k independently replayed; real read addresses/completions, useful/sector bytes and CPU formula checked; identical repeated events/data/truth; live payloads=0')
    # A real multi-expansion batch and one-vector scoring chunks, independent of completion order.
    batched=json.loads(json.dumps(base));batched['search'].update(batch=2,budget=5,chunk=1,width=5,k=5)
    write_json(OUT/'batched-config.json',batched)
    batch_events=experiment('batched',OUT/'batched-config.json');check_run('batched',batch_events,batched)
    assert [e['expand'] for e in batch_events if e['type']=='decision'][1]==[1,2]
    assertions.append('real SST B2/chunk1/two independent vector scores; five-node exhaustive output and modeled dedup/control')
    # Input guard checks before invoking SST.
    sys.path.insert(0,str(ROOT/'tools'))
    from run_m3_1 import run as host_run
    invalid=json.loads(json.dumps(base));invalid['placement']='controller';write_json(OUT/'invalid-config.json',invalid)
    try:
        host_run(OUT/'invalid-config.json',build,OUT/'invalid')
        raise AssertionError('controller accepted')
    except ValueError as e:
        assert 'Unsupported' in str(e)
    corrupt=OUT/'corrupt-data';shutil.copytree(a,corrupt,dirs_exist_ok=True)
    with (corrupt/'vectors.f32').open('r+b') as f:f.write(struct.pack('<f',float('nan')))
    try:
        read_dataset(corrupt)
        raise AssertionError('hash mismatch accepted')
    except ValueError as e:assert 'hash mismatch' in str(e)
    meta=json.loads((corrupt/'dataset.json').read_text());meta['sha256']['vectors.f32']=digest(corrupt/'vectors.f32');write_json(corrupt/'dataset.json',meta)
    try:
        read_dataset(corrupt)
        raise AssertionError('NaN accepted')
    except ValueError as e:assert 'nonfinite' in str(e)
    try:
        host_run(ROOT/'configs/m3.1/branch.json',build,OUT/'wrong-truth',OUT/'smoke/data')
        raise AssertionError('wrong ground truth accepted')
    except ValueError as e:assert 'ground truth' in str(e)
    assertions.append('explicit unsupported placement, data hash corruption, rehashed NaN, ground truth k mismatch rejected before SST')
    paths=json.loads((build/'sst-paths.json').read_text());asan=ROOT/'build/m3.1-asan'
    run('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DSST_ROOT='+paths['prefix'],'-DNDPSIM_SANITIZE=ON'])
    run('asan-build',[cmake,'--build',asan,'--target','graph_probe','-j4'])
    run('asan-ctest',[ctest,'--test-dir',asan,'-R','^graph_search_semantics$','--output-on-failure'])
    assertions.append('ASan graph probe: ties/cycles/visited, B1/B2, chunk1/16, reverse input arrivals, budget and disconnected insufficient output; zero live results')
    run('diff-check',['git','diff','--check'])
    assert not run('sst-status',['git','-C','third_party/sst-core','status','--porcelain'])
    assert not run('mqsim-status',['git','-C','third_party/MQSim','status','--porcelain'])
    status='passed'
finally:
    files=[ROOT/'AGENTS.md',ROOT/'CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'docs/prd.md',ROOT/'docs/implementation-plan.md',ROOT/'docs/v0.1-tasks.md',ROOT/'docs/m3.1-host-graph.md',
           *sorted((ROOT/'src/search').glob('*')),ROOT/'src/sst/graph_host.cc',ROOT/'src/backend/ordinary_device.h',ROOT/'tests/graph_probe.cc',
           ROOT/'tools/ann_data.py',ROOT/'tools/run_m3_1.py',Path(__file__),*sorted((ROOT/'configs/m3.1').glob('*'))]
    write_json(OUT/'manifest.json',dict(status=locals().get('status','failed'),commands=commands,assertions=assertions,
        mqsim_source=mqsim_source_snapshot(),input_sha256={str(p.relative_to(ROOT)):digest(p) for p in files if p.is_file()},
        log_sha256={p.name:digest(p) for p in OUT.glob('*.log')}))
print(f'M3.1 {status}: {len(assertions)} acceptance groups; {OUT}')
