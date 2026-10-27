"""Build formal bridge, run E1/regressions, compare controlled requests to locked native engine."""
import json, subprocess, hashlib, tarfile, os
from pathlib import Path
from verify_m1_1 import mqsim_source_snapshot
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/m1.2'
OUT.mkdir(parents=True,exist_ok=True)
lock=json.loads((ROOT/'deps.lock.json').read_text())
records=[]
def run(name,args,extra_env=None):
    p=subprocess.run(list(map(str,args)),cwd=ROOT,capture_output=True,text=True,timeout=600,env=dict(os.environ,**(extra_env or {})))
    (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
    records.append(dict(name=name,argv=list(map(str,args)),exit_code=p.returncode))
    if p.returncode: raise RuntimeError(name)
    return p.stdout
try:
    cmake=lock['tools']['cmake']['path']; build=ROOT/'build/m1.2'
    run('configure',[cmake,'-S',ROOT,'-B',build])
    run('build',[cmake,'--build',build,'-j2'])
    run('ctest',[Path(cmake).with_name('ctest'),'--test-dir',build,'--output-on-failure'])
    native=build/'native-engine'; native.mkdir(exist_ok=True)
    archive=OUT/'native.tar'
    base=mqsim_source_snapshot()['native_base_commit']
    run('export',['git','-C',ROOT/'third_party/MQSim','archive','-o',archive,base])
    with tarfile.open(archive) as t: t.extractall(native)
    sources=[native/'src/sim/Engine.cpp',native/'src/sim/EventTree.cpp',native/'src/utils/Logical_Address_Partitioning_Unit.cpp']
    run('native-build',[lock['tools']['cxx']['path'],'-std=c++17','-I'+str(native/'src'),'-I'+str(ROOT/'tests'),ROOT/'tests/integer_native.cc',*sources,'-o',native/'probe'])
    expected=run('native',[native/'probe'])
    sst=json.loads((build/'sst-paths.json').read_text())
    traces=[]
    for i in (1,2):
        actual=run('sst-'+str(i),[sst['sst'],'--add-lib-path='+str(build),ROOT/'configs/m1.2/bridge.py'])
        rows=lambda text:[x for x in text.splitlines() if x.startswith('REQUEST ')]
        assert rows(actual)==rows(expected)==['REQUEST 1 17','REQUEST 2 17','REQUEST 3 27','REQUEST 4 27']
        traces.append(rows(actual))
    assert traces[0]==traces[1]
    run('formal-diagnostics',[build/'formal_bridge_probe'])
    assert not run('sst-status',['git','-C',ROOT/'third_party/sst-core','status','--porcelain']).strip()
    asan=ROOT/'build/m0.3b-final-asan'
    run('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DSST_ROOT='+sst['prefix'],'-DNDPSIM_SANITIZE=ON'])
    run('asan-build',[cmake,'--build',asan,'--target','formal_bridge_probe','-j2'])
    run('asan-probe',[asan/'formal_bridge_probe'],{'ASAN_OPTIONS':'detect_leaks=0'})
    status='passed'
finally:
    (OUT/'manifest.json').write_text(json.dumps(dict(status=locals().get('status','failed'),commands=records,mqsim_source=mqsim_source_snapshot(),oracle_commit=locals().get('base'),inputs_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'CMakeLists.txt',ROOT/'src/backend/event_bridge.h',ROOT/'src/sst/event_bridge.cc',ROOT/'tests/formal_bridge_probe.cc',ROOT/'tests/integer_requests.h',ROOT/'tests/integer_native.cc',Path(__file__)]},logs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.log')}),indent=2)+'\n')
