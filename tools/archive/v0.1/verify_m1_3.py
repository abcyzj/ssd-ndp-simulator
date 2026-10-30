"""M1.3 real NVMe differential matrix, independent locked native source."""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import json, subprocess, hashlib, tarfile, os
from pathlib import Path
from source_snapshot import mqsim_source_snapshot
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/evidence/m1.3'; OUT.mkdir(parents=True,exist_ok=True)
lock=json.loads((ROOT/'deps.lock.json').read_text()); records=[]; assertions=[]
def run(name,args,env=None):
    p=subprocess.run(list(map(str,args)),cwd=ROOT,capture_output=True,text=True,timeout=600,env=dict(os.environ,**(env or {})))
    (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
    records.append(dict(name=name,argv=list(map(str,args)),env=env,exit_code=p.returncode))
    if p.returncode: raise RuntimeError(name)
    return p.stdout
def rows(s): return [x for x in s.splitlines() if x.startswith(('IO ','INITIAL ','MAP ','CMT ','CACHE ','FINAL '))]
try:
    cmake=lock['tools']['cmake']['path']; build=ROOT/'build/m1.3'
    run('configure',[cmake,'-S',ROOT,'-B',build]);run('build',[cmake,'--build',build,'-j4'])
    run('ctest',[Path(cmake).with_name('ctest'),'--test-dir',build,'--output-on-failure'])
    native=build/'native'; native.mkdir(exist_ok=True)
    archive=build/'native.tar'; base=lock['dependencies']['MQSim']['commit']
    run('export',['git','-C',ROOT/'third_party/MQSim','archive','-o',archive,base])
    with tarfile.open(archive) as t:t.extractall(native)
    sources=[p for p in (native/'src').rglob('*.cpp') if p.name!='main.cpp']
    run('native-build',[lock['tools']['cxx']['path'],'-isysroot',lock['target']['sdk_path'],'-std=c++17','-O1','-Xclang','-mconstructor-aliases','-DNDPSIM_NATIVE_ORACLE','-I'+str(native/'src'),'-I'+str(ROOT/'src'),'-I'+str(ROOT/'tests'),'-I'+str(build/'generated'),ROOT/'tests/ordinary_native.cc',*sources,'-o',native/'probe'])
    sst=json.loads((build/'sst-paths.json').read_text())
    for name,args,qd,miss in [('qd1',[],1,0),('concurrent',['normal','concurrent'],32,0),('cmt-miss',['miss','concurrent'],32,1)]:
        expected=run(name+'-native',[native/'probe',*args])
        actual=[]
        for i in (1,2):
            actual.append(run(name+'-sst-'+str(i),[sst['sst'],'--add-lib-path='+str(build),ROOT/'configs/regression/ordinary.py'],{'NDPSIM_QD':str(qd),'NDPSIM_MISS':str(miss)}))
        assert rows(expected)==rows(actual[0])==rows(actual[1]), name
        assert len([x for x in rows(expected) if x.startswith('IO ')])==64+qd
        assertions.append(name+': all addresses, sizes, direction, CQ times, mapping/CMT summary identical; repeat identical')
    status='passed'
finally:
    (OUT/'manifest.json').write_text(json.dumps(dict(status=locals().get('status','failed'),commands=records,assertions=assertions,mqsim_source=mqsim_source_snapshot(),oracle_commit=locals().get('base'),inputs_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'src/backend/external_flow.h',ROOT/'src/backend/preload_window.h',ROOT/'configs/regression/ordinary.py',ROOT/'tests/ordinary_fixture.h',ROOT/'tests/ordinary_native.cc',ROOT/'src/sst/ordinary_io.cc',Path(__file__)]},logs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.log')}),indent=2)+'\n')
