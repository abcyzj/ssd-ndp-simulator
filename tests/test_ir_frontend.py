"""Production SST frontend regression; scoped equivalence assertions, not V03-5 comparator."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from ann_data import write_json

ROOT=Path(__file__).resolve().parents[1]

def events(path):
    return [json.loads(line) for line in (path/'events.jsonl').read_text().splitlines()]

def normalize(row):
    # Only opaque identities; vector/node IDs, times, bytes, Work and ordering remain.
    row=copy.deepcopy(row)
    for key in ('task', 'request', 'platform_id'):
        row.pop(key, None)
    if row.get('type') in ('link', 'message', 'transaction'):
        row.pop('id', None)
    if row.get('type')=='lifecycle':
        assert all(v == 0 for k,v in row.items() if k not in ('type', 'runtime_peak_tasks', 'protocol_ranges')), 'resources did not drain'
        row.pop('runtime_peak_tasks')
    if row.get('type')=='lifecycle_sample':
        row.pop('runtime_tasks')
        row.pop('waiters')
    if row.get('type')=='local_request':
        for transaction in row['transactions']:
            transaction.pop('id', None)
    return row

def compare(direct, ir):
    assert json.loads((direct/'results.json').read_text())==json.loads((ir/'results.json').read_text()), 'query result/timing/digest mismatch'
    a,b=(json.loads((p/'statistics.json').read_text()) for p in (direct,ir))
    for s in (a,b):
        s.pop('wall_seconds')
        for key, rows in s['resources'].items():
            s['resources'][key]=[normalize(r) for r in rows]
    assert a==b, 'simulated statistics/ledger mismatch'
    a,b=([normalize(r) for r in events(p) if r['type']!='score_visible'] for p in (direct,ir))
    # Service completion interleaving with management callbacks may differ; compare
    # each resource/event stream in its original order, including every time field.
    for kind in sorted({r['type'] for r in a+b}):
        assert [r for r in a if r['type']==kind]==[r for r in b if r['type']==kind], 'event stream mismatch: '+kind
    manifest=json.loads((ir/'manifest.json').read_text())
    assert manifest['execution_engine']=='ir' and manifest['ir_contract_sha256']
    assert json.loads((ir/'ir-manifest.json').read_text())['operations']==[]
    return dict(passed=True, decisions=sum(r['type']=='decision' for r in a),
                queries=json.loads((direct/'results.json').read_text())['queries'].__len__())

def run_one(config, build, output, data=None):
    argv=[sys.executable,str(ROOT/'tools/run_config.py'),'--config',str(config),'--build',str(build),'--output',str(output)]
    if data is not None: argv+=['--data',str(data)]
    r=subprocess.run(argv,capture_output=True,text=True)
    (output.parent/(output.name+'.log')).write_text(r.stdout+r.stderr)
    assert r.returncode==0, r.stdout+r.stderr

def matrix(c, build, out, data=None):
    out.mkdir(parents=True)
    reports={}
    for placement in ('host','controller'):
        for engine in ('direct','ir'):
            cfg=copy.deepcopy(c);cfg.update(placement=placement,execution_engine=engine,event_log=True)
            path=out/(engine+'-'+placement+'.json');write_json(path,cfg)
            dest=out/(engine+'-'+placement)
            run_one(path,build,dest,data)
            if data is None: data=dest/'data'
        reports[placement]=compare(out/('direct-'+placement),out/('ir-'+placement))
    write_json(out/'comparison.json',reports)
    return reports

def synthetic(build, out):
    c=json.loads((ROOT/'configs/regression/placements-branch.json').read_text())
    out.mkdir(parents=True, exist_ok=True)
    bad=copy.deepcopy(c);bad['execution_engine']='unknown'
    write_json(out/'invalid-engine.json',bad)
    argv=[sys.executable,str(ROOT/'tools/run_config.py'),'--config',str(out/'invalid-engine.json'),'--build',str(build),'--output',str(out/'invalid-engine')]
    rejected=subprocess.run(argv,capture_output=True,text=True)
    assert rejected.returncode != 0 and 'Unsupported execution_engine' in rejected.stderr
    assert not (out/'invalid-engine/sst-params.json').exists()
    (out/'invalid-engine.log').write_text(rejected.stdout+rejected.stderr)
    for fixture in ('branch','mutated'):
        c['generate']['fixture']=fixture
        matrix(c,build,out/fixture)
    for engine in ('direct','ir'):
        for placement in ('host','controller'):
            before,after=(events(out/f/(engine+'-'+placement)) for f in ('branch','mutated'))
            reads=lambda es:[(r['id'],r['lba']) for r in es if r['type']=='read_submit' and r['kind']=='adjacency']
            assert reads(before)!=reads(after), 'real vector perturbation did not alter successor reads'
    # A valid contract paired with different SST search parameters must fail before backend creation.
    params=json.loads((out/'branch/ir-host/sst-params.json').read_text())
    params['width']+=1
    write_json(out/'invalid-contract-params.json',params)
    paths=json.loads((build/'sst-paths.json').read_text())
    rejected=subprocess.run([paths['sst'],'--add-lib-path='+str(build),str(ROOT/'configs/sst/graph.py')],
        env=dict(os.environ,NDPSIM_M31_PARAMS=str(out/'invalid-contract-params.json')),capture_output=True,text=True)
    assert rejected.returncode != 0 and 'IR contract differs' in rejected.stdout+rejected.stderr
    (out/'invalid-contract.log').write_text(rejected.stdout+rejected.stderr)
    c['generate']=dict(n=32,d=8,degree=4,queries=4,seed=42,fixture='smoke')
    c['search']=dict(entry=0,k=3,width=8,batch=2,budget=7,chunk=2)
    c['max_inflight']=2
    matrix(c,build,out/'concurrent')
    c['search'].update(batch=4,budget=1)
    matrix(c,build,out/'budget')
    print('V03-4 synthetic PASS: 16 production SST runs, perturbation, concurrent B2/chunk2, B>E')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--output',type=Path);a=p.parse_args()
    if a.output:
        assert not a.output.exists();synthetic(a.build.resolve(),a.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='ndpsim-ir-front-') as tmp:
            synthetic(a.build.resolve(),Path(tmp))
