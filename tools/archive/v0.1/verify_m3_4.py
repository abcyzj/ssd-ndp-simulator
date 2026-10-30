"""M3.4 reproducible acceptance, real SST/MQSim for both placements."""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from ann_data import write_json, digest, generate, exact_truth
from source_snapshot import mqsim_source_snapshot

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/evidence/m3.4'
OUT.mkdir(parents=True, exist_ok=True)
commands, assertions, comparisons=[], [], {}


def command(name, argv):
    argv=list(map(str, argv))
    p=subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode))
    if p.returncode:raise RuntimeError(f'{name} exit={p.returncode}; see {OUT}')
    return p.stdout


def pair(name, config, data=None):
    path=OUT/(name+'-config.json');write_json(path, config)
    argv=[sys.executable, ROOT/'tools/run_pair.py', '--pair', '--config', path,
          '--build', ROOT/'build/m3.4', '--output', OUT/name]
    if data:argv+=['--data', data]
    command(name, argv)
    comparisons[name]=json.loads((OUT/name/'comparison.json').read_text())
    return {p:[json.loads(l) for l in (OUT/name/p/'events.jsonl').read_text().splitlines()] for p in ('host', 'controller')}


try:
    lock=json.loads((ROOT/'deps.lock.json').read_text())
    cmake=Path(lock['tools']['cmake']['path']);ctest=cmake.with_name('ctest');build=ROOT/'build/m3.4'
    command('configure', [cmake, '-S', ROOT, '-B', build])
    command('build', [cmake, '--build', build, '-j4'])
    command('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
    assertions.append('root CMake default SST preparation/cache; complete CTest including paired graph replay and existing resource/storage regressions')
    base=json.loads((ROOT/'configs/regression/placements-branch.json').read_text())
    branch=pair('branch', base)
    changed=json.loads(json.dumps(base));changed['generate']['fixture']='mutated'
    mutation=pair('mutated', changed)
    for placement in ('host','controller'):
        for events, node, lba in [(branch[placement],1,6),(mutation[placement],2,7)]:
            ds=[e for e in events if e['type']=='decision']
            assert ds[0]['expand']==[0] and ds[1]['expand']==[node]
            reads=[e for e in events if e['type']=='read_submit' and e['kind']=='adjacency']
            assert [(e['id'],e['lba']) for e in reads]==[(0,5),(node,lba)]
            assert reads[1]['time_ns']>=ds[1]['time_ns']
        assert json.loads((OUT/'branch'/placement/'results.json').read_text())['recall_at_k']==1
        assert json.loads((OUT/'mutated'/placement/'results.json').read_text())['recall_at_k']==0.5
    a=OUT/'branch/host/data';b=OUT/'mutated/host/data'
    assert (a/'adjacency.u32').read_bytes()==(b/'adjacency.u32').read_bytes()
    assert (a/'queries.f32').read_bytes()==(b/'queries.f32').read_bytes()
    va,vb=(a/'vectors.f32').read_bytes(),(b/'vectors.f32').read_bytes()
    assert va[:4]==vb[:4] and va[8:]==vb[8:] and struct.unpack_from('<f',va,4)==(1.0,) and struct.unpack_from('<f',vb,4)==(5.0,)
    assertions.append('AC-02 both placements: only x1=1->5; second expansion 1->2, independent adjacency LBA 6->7, Recall@2=1/0.5')
    smoke=json.loads((ROOT/'configs/archive/v0.1/m3.4/host.json').read_text())
    events=pair('smoke', smoke)
    repeated=pair('smoke-repeat', smoke, OUT/'smoke/host/data')
    assert events==repeated
    assertions.append('AC-01/05 default N2048/d32/R16/Q32/k10/L32/B1/E64/chunk16: shared input, independent offline batch replay, identical candidates/expansions/top-k and tolerant distances; per-placement events exactly repeat')
    batched=json.loads(json.dumps(base));batched['search'].update(batch=2, budget=5, chunk=16, width=5, k=5)
    batched['controller']['capacity_bytes']=1124 # q=4 + block(64 + 2*528); deterministic chunk=2
    batch=pair('capacity-batch', batched)
    assert [e['expand'] for e in batch['controller'] if e['type']=='decision'][1]==[1,2]
    assertions.append('real B2 and capacity-limited controller chunks; host configured chunk16 and controller chunk2 preserve full semantic batches')
    split=json.loads(json.dumps(smoke));split['generate'].update(n=33,d=32,degree=8,queries=2)
    split['search'].update(k=5,width=8,batch=2,budget=8,chunk=16)
    split['controller']['capacity_bytes']=1248 # query128 + one maximum 1120-byte two-vector block
    split_events=pair('capacity-split', split)
    assert any(len(e['scores'])>2 for e in split_events['controller'] if e['type']=='score_visible')
    assert all(e['useful']<=8 for e in split_events['controller'] if e['type']=='message' and e['kind']==3)
    assertions.append('semantic batches larger than capacity chunk2 really split into multiple replies; one-block capacity, host chunk16; all candidates/top-k remain equal')
    cross=json.loads(json.dumps(smoke));cross['generate'].update(n=17,d=260,degree=4,queries=2)
    cross['search'].update(k=4,width=8,batch=2,budget=8,chunk=3)
    cross['controller'].update(vector_sector_bound=1536,capacity_bytes=10488,controller_slots=2)
    cross_events=pair('cross-page', cross)
    assert any(len(e['transactions'])>1 for e in cross_events['controller'] if e['type']=='local_request')
    assertions.append('cross-page vectors d260/17 nodes/2 queries/B2/chunk3/two arithmetic slots; actual local request sectors, transaction bytes/times and ordinary adjacency extents reconciled')
    ties=OUT/'ties-data';shutil.copytree(a,ties,dirs_exist_ok=True)
    values=(ties/'vectors.f32').read_bytes();(ties/'vectors.f32').write_bytes(values[:4]+struct.pack('<f',3)+values[8:])
    meta=json.loads((ties/'dataset.json').read_text());meta['sha256']['vectors.f32']=digest(ties/'vectors.f32');write_json(ties/'dataset.json',meta)
    exact_truth(ties,base['search']['k']);tie_events=pair('ties', base, ties)
    for ev in tie_events.values():assert [e['expand'] for e in ev if e['type']=='decision'][1]==[1]
    assertions.append('equal-distance tie resolves by uint32 ID in both placements')
    assertions.append('AC-06 all pairs: controller ordinary vector reads=0 and vector D2H=0; query once, Score IDs and packed results, open/close ACKs, adjacency DMA/SQ/CQ/doorbells all counted; one enqueue/start/arrival fee per link transfer; local request/transaction and storage/message/task associations; credit conservation and zero live handles')
    # Check that differential validation actually rejects wrong distances and missing fee records.
    from compare_placements import compare
    tampered=OUT/'tampered';shutil.copytree(OUT/'branch/controller',tampered,dirs_exist_ok=True)
    original=(tampered/'events.jsonl').read_text();bad=[json.loads(l) for l in original.splitlines()]
    next(e for e in bad if e['type']=='decision')['candidates'][0]['distance']+=1
    (tampered/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in bad))
    try:compare(OUT/'branch/host',tampered);raise RuntimeError('bad distance accepted')
    except AssertionError:pass
    bad=[json.loads(l) for l in original.splitlines()];bad.pop(next(i for i,e in enumerate(bad) if e['type']=='link' and e['phase']=='arrive'))
    (tampered/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in bad))
    try:compare(OUT/'branch/host',tampered);raise RuntimeError('missing wire arrival accepted')
    except AssertionError:pass
    assertions.append('negative differential checks reject modified distance and missing PCIe accounting event')
    paths=json.loads((build/'sst-paths.json').read_text());asan=ROOT/'build/m3.4-asan'
    command('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DSST_ROOT='+paths['prefix'],'-DNDPSIM_SANITIZE=ON'])
    command('asan-build',[cmake,'--build',asan,'--target','graph_probe','controller_probe','controller_resource_probe','-j4'])
    command('asan-ctest',[ctest,'--test-dir',asan,'-R','^(graph_search_semantics|controller_.*)$','--output-on-failure'])
    assertions.append('ASan graph/controller/resource probes; sanitizer scope is isolated probes, not full SST')
    command('diff-check',['git','diff','--check'])
    assert not command('sst-status',['git','-C','third_party/sst-core','status','--porcelain'])
    assert not command('mqsim-status',['git','-C','third_party/MQSim','status','--porcelain'])
    status='passed'
finally:
    files=[ROOT/'AGENTS.md',ROOT/'CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'docs/prd.md',ROOT/'docs/archive/v0.1/implementation-plan.md',ROOT/'docs/archive/v0.1/v0.1-tasks.md',
           ROOT/'docs/archive/v0.1/m3.4-placements.md',*sorted((ROOT/'src').rglob('*.cc')),*sorted((ROOT/'src').rglob('*.h')),
           ROOT/'tools/run_graph.py',ROOT/'tools/ann_data.py',ROOT/'tools/run_pair.py',ROOT/'tools/compare_placements.py',Path(__file__),*sorted((ROOT/'configs').rglob('*.json')), ROOT/'configs/sst/graph.py']
    write_json(OUT/'manifest.json',dict(status=locals().get('status','failed'),commands=commands,assertions=assertions,comparisons=comparisons,
        mqsim_source=mqsim_source_snapshot(),input_sha256={str(p.relative_to(ROOT)):digest(p) for p in files if p.is_file()},
        log_sha256={p.name:digest(p) for p in OUT.glob('*.log')}))
print(f'M3.4 {status}: {len(assertions)} assertion groups; {OUT}')
