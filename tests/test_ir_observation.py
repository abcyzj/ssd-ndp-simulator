"""Production observations, strict comparator and adversarial difference tests."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ann_data import write_json, digest
from compare_engines import compare
from compare_placements import compare as independent_ledger
from test_ir_frontend import run_one
ROOT=Path(__file__).resolve().parents[1]

def require(report):
    if not report['passed']: raise AssertionError(report)
    return report

def schema_check(path):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    schema=json.loads((ROOT/'schemas/ndpsim-ir-diagnostics-v1.schema.json').read_text())
    ir=json.loads((ROOT/'schemas/ndpsim-ir-v1.schema.json').read_text())
    registry=Registry().with_resource(ir['$id'],Resource.from_contents(ir))
    validator=Draft202012Validator(schema,registry=registry)
    from compare_engines import MANAGEMENT, rows
    for row in rows(path/'events.jsonl'):
        if row['type'] in MANAGEMENT: validator.validate(row)

def exercise(c,build,out,data=None):
    out.mkdir(parents=True)
    reports={}
    for placement in ('host','controller'):
        for engine in ('direct','ir'):
            for detail in (False,True):
                cfg=copy.deepcopy(c);cfg.update(placement=placement,execution_engine=engine,event_log=detail)
                name=f'{engine}-{placement}-'+('detail' if detail else 'statistics')
                write_json(out/(name+'.json'),cfg)
                run_one(out/(name+'.json'),build,out/name,data)
                if data is None: data=out/name/'data'
            name=f'{engine}-{placement}'
            reports[name]=require(compare(out/(name+'-statistics'),out/(name+'-detail')))
            if engine=='ir': schema_check(out/(name+'-detail'))
        for mode in ('statistics','detail'):
            reports[placement+'-'+mode]=require(compare(out/f'direct-{placement}-{mode}',out/f'ir-{placement}-{mode}'))
    for engine in ('direct','ir'):
        reports[engine+'-ledger']=independent_ledger(out/f'{engine}-host-detail',out/f'{engine}-controller-detail')
    write_json(out/'comparison.json',reports)
    return reports

def mutations(left,right,out):
    reports={}
    from verify_t6 import verify_cached_run
    try: verify_cached_run(right,dict(json.loads((right/'config.json').read_text()),execution_engine='direct'),Path('/unused'), 'unused',Path('/unused'))
    except ValueError as exc:
        if 'engine mismatch' not in str(exc): raise
    else: raise AssertionError('cross-engine cache accepted')
    reports['cross-engine-cache-rejected']=dict(passed=True)
    def trial(name,filename,edit,passed=False):
        dest=out/name;shutil.copytree(right,dest)
        target=dest/filename
        if filename.endswith('jsonl'):
            obj=[json.loads(s) for s in target.read_text().splitlines()];edit(obj)
            target.write_text(''.join(json.dumps(r)+'\n' for r in obj))
        else:
            obj=json.loads(target.read_text());edit(obj);write_json(target,obj)
        m=json.loads((dest/'manifest.json').read_text())
        if filename!='manifest.json': m['artifacts_sha256'][filename]=digest(target)
        write_json(dest/'manifest.json',m)
        report=compare(left,dest)
        if report['passed']!=passed: raise AssertionError((name,report))
        reports[name]=report
    def row_edit(kind,key,value):
        def edit(rows): next(r for r in rows if r['type']==kind)[key]=value
        return edit
    trial('wall-allowed','statistics.json',lambda x:x.update(wall_seconds=987),True)
    trial('resource-time','events.jsonl',row_edit('service','start_ns',0))
    trial('bytes','events.jsonl',row_edit('read_submit','sectors',99))
    trial('task-relationship','events.jsonl',row_edit('read_complete','task',999999))
    trial('message-payload','events.jsonl',row_edit('message','payload',999))
    trial('unknown-field','statistics.json',lambda x:x.update(future_resource_cost=1))
    trial('result-distance','results.json',lambda x:x['queries'][0]['top_k'][0].update(distance=999))
    trial('control-work','statistics.json',lambda x:x['resources']['service'][0].update(checks=999))
    def wrong_parent(rows):
        binding=next(r for r in rows if r['type']=='ir_binding')
        binding['op']=next(r['operation']['op_id'] for r in rows if r['type']=='ir_operation' and r['operation']['scope']['query']!=1)
    trial('wrong-parent','events.jsonl',wrong_parent)
    trial('unregistered-events','manifest.json',lambda x:x['artifacts_sha256'].pop('events.jsonl'))
    trial('unknown-diagnostic-field','events.jsonl',lambda rows:next(r for r in rows if r['type']=='ir_binding').update(unknown_semantic=1))
    trial('no-runtime','events.jsonl',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r['type']!='ir_runtime']))
    trial('no-bindings','events.jsonl',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r['type']!='ir_binding']))
    trial('no-task-edges','events.jsonl',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r['type']!='ir_task_edge']))
    trial('no-sessions','events.jsonl',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r['type']!='ir_session']))
    trial('no-operations','events.jsonl',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r['type']!='ir_operation']))
    def reorder(rows):
        indices=[i for i,r in enumerate(rows) if r['type']=='read_submit'];a,b=indices[:2];rows[a],rows[b]=rows[b],rows[a]
    trial('read-fifo','events.jsonl',reorder)
    # Consistent opaque task renaming is allowed; all cross-references change.
    def rename(rows):
        def walk(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k in ('task','parent_task') and isinstance(x,int) and x: v[k]=x+100000
                    else: walk(x)
            elif isinstance(v,list):
                for x in v: walk(x)
        walk(rows)
    # statistics task references must be renamed too, so validate event-only
    # identity numbers through a standalone helper copy below.
    dest=out/'consistent-task-renaming';shutil.copytree(right,dest)
    events=[json.loads(s) for s in (dest/'events.jsonl').read_text().splitlines()];rename(events)
    (dest/'events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in events))
    stats=json.loads((dest/'statistics.json').read_text());rename([stats]);write_json(dest/'statistics.json',stats)
    m=json.loads((dest/'manifest.json').read_text())
    for f in ('events.jsonl','statistics.json'):m['artifacts_sha256'][f]=digest(dest/f)
    write_json(dest/'manifest.json',m);reports['consistent-task-renaming']=require(compare(left,dest))
    write_json(out/'mutation-report.json',reports)
    return dict(cases=len(reports),passed=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--output',type=Path);a=p.parse_args()
    def run(out):
        c=json.loads((ROOT/'configs/regression/placements-branch.json').read_text())
        c['generate']=dict(n=16,d=260,degree=4,queries=4,seed=42,fixture='smoke')
        c['search']=dict(entry=0,k=3,width=8,batch=2,budget=7,chunk=2);c['max_inflight']=2
        c['controller']['vector_sector_bound']=1536
        exercise(c,a.build.resolve(),out/'matrix')
        print(mutations(out/'matrix/direct-controller-detail',out/'matrix/ir-controller-detail',out/'mutations'))
        # Budget exhaustion must fail rather than silently truncate detail or keep
        # a successful run receipt. This uses the real IR/SST writer.
        import subprocess
        cfg=copy.deepcopy(c);cfg.update(execution_engine='ir',event_log=True,output_budget_bytes=2048)
        write_json(out/'budget.json',cfg)
        r=subprocess.run([sys.executable,str(ROOT/'tools/run_config.py'),'--config',str(out/'budget.json'),'--build',str(a.build.resolve()),'--output',str(out/'budget')],capture_output=True,text=True)
        (out/'budget.log').write_text(r.stdout+r.stderr)
        if not r.returncode or (out/'budget/manifest.json').exists():raise AssertionError('budget falsely succeeded')
        if 'budget exhausted' not in (out/'budget/sst.log').read_text():raise AssertionError('missing budget diagnostic')
        write_json(out/'budget-assertion.json',dict(passed=True,exit_code=r.returncode,successful_manifest=False))
    if a.output:
        if a.output.exists():p.error('output exists')
        run(a.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='ndpsim-observation-') as tmp:run(Path(tmp))
if __name__=='__main__':main()
