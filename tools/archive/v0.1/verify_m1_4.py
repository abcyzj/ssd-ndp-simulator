"""M1.4 independent endpoint-sweep union and observation neutrality acceptance."""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import hashlib,json,os,subprocess
from pathlib import Path
from source_snapshot import mqsim_source_snapshot
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/evidence/m1.4'; OUT.mkdir(parents=True,exist_ok=True)
commands=[]; assertions=[]
def run(name,args,env=None):
    p=subprocess.run(list(map(str,args)),cwd=ROOT,env=dict(os.environ,**(env or {})),capture_output=True,text=True,timeout=600)
    (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
    commands.append(dict(name=name,argv=list(map(str,args)),env=env,exit_code=p.returncode))
    if p.returncode: raise RuntimeError(name)
    return p.stdout
def union(v):
    # Independent of production's sorted-interval merge: sweep signed endpoints.
    points={}
    for a,b in v:
        assert b>=a
        points[a]=points.get(a,0)+1;points[b]=points.get(b,0)-1
    depth=0;total=0;last=0
    for t,change in sorted(points.items()):
        if depth: total+=t-last
        depth+=change;last=t
    assert depth==0
    return total
assert union([(1,5),(2,4),(5,8),(10,12)])==9
try:
    lock=json.loads((ROOT/'deps.lock.json').read_text()); cmake=lock['tools']['cmake']['path'];build=ROOT/'build/m1.4'
    run('configure',[cmake,'-S',ROOT,'-B',build]);run('build',[cmake,'--build',build,'-j4'])
    run('ctest',[Path(cmake).with_name('ctest'),'--test-dir',build,'--output-on-failure'])
    synthetic=run('synthetic',[build/'observation_probe'])
    synthetic_stats=[json.loads(l[5:]) for l in synthetic.splitlines() if l.startswith('STAT ')]
    assert next(s['union_ns'] for s in synthetic_stats if s['kind']=='bus')==14
    assert sorted(s['union_ns'] for s in synthetic_stats if s['kind']=='wait_data_out')==[5,8]
    assertions.append('synthetic: clipped overlapping bus union=14 ns; independent die wait=5/8 ns')
    sst=json.loads((build/'sst-paths.json').read_text())['sst']
    for name,qd,miss in [('qd1',1,0),('concurrent',32,0),('cmt-miss',32,1)]:
        outputs=[]
        for mode in [0,1,1]:
            outputs.append(run(f'{name}-{len(outputs)}',[sst,'--add-lib-path='+str(build),ROOT/'configs/regression/ordinary.py'],{'NDPSIM_OBSERVE':str(mode),'NDPSIM_QD':str(qd),'NDPSIM_MISS':str(miss)}))
        def storage(s):return [l for l in s.splitlines() if l.startswith(('IO ','INITIAL ','MAP ','CMT ','CACHE ','FINAL '))]
        assert storage(outputs[0])==storage(outputs[1])==storage(outputs[2])
        assert [l for l in outputs[1].splitlines() if l.startswith(('OBS ','STAT '))]==[l for l in outputs[2].splitlines() if l.startswith(('OBS ','STAT '))]
        assert not any(l.startswith(('OBS ','STAT ')) for l in outputs[0].splitlines())
        events=[json.loads(l[4:]) for l in outputs[1].splitlines() if l.startswith('OBS ')]
        stats=[json.loads(l[5:]) for l in outputs[1].splitlines() if l.startswith('STAT ')]
        requests={}
        for e in events:
            assert e['device']==0 and e['time_ns']>=0
            if e['event_kind'].startswith('request_'):requests.setdefault(e['request_id'],[]).append(e)
        assert len(requests)==qd
        for pair in requests.values():
            assert [e['event_kind'] for e in pair]==['request_submit','request_complete']
            assert pair[0]['time_ns']<pair[1]['time_ns'] and pair[0]['bytes']==pair[1]['bytes']
        intervals=[e for e in events if e['event_kind']=='interval']
        assert {'command_bus','array','data_out_bus','wait_data_out'}<={e['kind'] for e in intervals}
        assert all(e['end_ns']>=e['start_ns'] for e in intervals if e['kind']=='wait_data_out')
        for s in stats:
            selected=[(e['start_ns'],e['end_ns']) for e in intervals if e['channel']==s['channel'] and ((s['kind']=='bus' and 'bus' in e['kind']) or (e['kind']==s['kind'] and e['chip']==s['chip'] and e['die']==s['die']))]
            assert union(selected)==s['union_ns']<=s['window_end_ns']-s['window_start_ns']
        assertions.append(f'{name}: {len(intervals)} intervals / {len(stats)} unions independently verified; {qd} request lifecycles; observation off/on/repeat identical storage behavior')
    status='passed'
finally:
    (OUT/'manifest.json').write_text(json.dumps(dict(status=locals().get('status','failed'),commands=commands,assertions=assertions,mqsim_source=mqsim_source_snapshot(),inputs_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'src/backend/observation.h',ROOT/'src/backend/external_flow.h',ROOT/'src/sst/ordinary_io.cc',ROOT/'configs/regression/ordinary.py',Path(__file__)]},logs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.log')}),indent=2)+'\n')
