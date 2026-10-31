"""T3 real small-input closed-loop acceptance; never starts T4 or scale work."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from ann_data import write_json
from benchmark_artifacts import binding
from benchmark_data import check
from verify_m0 import ROOT, measure, sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True,help='T2 directory with small-data/index/exact')
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); out=a.output.resolve(); prepared=a.prepared.resolve()
    if out.exists(): p.error('output must not exist')
    out.mkdir(parents=True)
    runs=[]; assertions={}
    def save(passed=False):
        write_json(out/'manifest.json',dict(scope='T3/M1 small real SIFT closed loop; no scale or full V0.1 AC',passed=passed,
            commands=runs,assertions=assertions,source_sha256={str(f.relative_to(ROOT)):sha(f) for f in [
            ROOT/'tools/verify_t3.py',ROOT/'tools/run_graph.py',ROOT/'tools/simulation_input.py',ROOT/'tools/compare_placements.py',ROOT/'src/sst/graph_host.cc']},
            disk_bytes_snapshot=sum(f.stat().st_size for f in out.rglob('*') if f.is_file()),disk_peak='Unsupported: snapshot only'))
    def run(label,args):
        r=measure([sys.executable,*map(str,args)],out/(label+'.json'),3600,4*1024**3)
        runs.append(r); save()
        if not r['passed']: raise RuntimeError('failed stage: '+label)
    c=json.loads((ROOT/'configs/experiments/host.json').read_text())
    x=json.loads((prepared/'index/index.json').read_text())
    c.update(format='ndpsim-experiment-v2',index=str(prepared/'index'),truth=str(prepared/'exact'),event_log=True)
    c['search']['entry']=x['entry']
    write_json(out/'real-config.json',c)
    run('real-pair',[ROOT/'tools/run_pair.py','--pair','--config',out/'real-config.json','--data',prepared/'small-data','--build',a.build,'--output',out/'real'])
    comparison=json.loads((out/'real/comparison.json').read_text())
    assertions.update(real_pair=comparison['status']=='passed',vector_d2h_zero=comparison['controller']['vector_d2h_bytes']==0)
    host=json.loads((out/'real/host/results.json').read_text())
    assertions['query_identity_and_quality']=all('original_query_id' in q and 'one_recall_at_r' in q for q in host['queries'])
    # Swap two vectors actually scored in the first expansion, preserving graph, IDs,
    # query bytes and costs. Rebind as an explicit counterfactual, not a rebuilt NSG.
    events=[json.loads(l) for l in (out/'real/host/events.jsonl').read_text().splitlines()]
    decisions=[e for e in events if e['type']=='decision' and e['query']==1]
    ranked=[v['id'] for v in decisions[1]['candidates'] if v['id']!=x['entry']]
    first=next(v for v in decisions[1]['expand'] if v!=x['entry'])
    other=next(v for v in reversed(ranked) if v!=first)
    data=out/'perturbed-data'; index=out/'perturbed-index'; truth=out/'perturbed-truth'
    shutil.copytree(prepared/'small-data',data); shutil.copytree(prepared/'index',index)
    m=json.loads((data/'dataset.json').read_text()); b=m['files']['base']; path=data/b['path']; width=4*b['dimensions']
    with path.open('r+b') as f:
        f.seek(first*width); v=f.read(width); f.seek(other*width); w=f.read(width)
        f.seek(first*width); f.write(w); f.seek(other*width); f.write(v)
    b['sha256']=sha(path)
    m['counterfactual']=dict(method='swap two scored base vectors; graph unchanged',local_ids=[first,other],original_dataset_sha256=sha(prepared/'small-data/dataset.json'))
    write_json(data/'dataset.json',m); check(data)
    x['binding']=binding(m,('base',)); x['counterfactual']=m['counterfactual']
    write_json(index/'index.json',x)
    run('perturbed-exact',[ROOT/'tools/benchmark_artifacts.py','exact',data,truth,'--k',10,'--block-rows',128])
    c.update(index=str(index),truth=str(truth)); write_json(out/'perturbed-config.json',c)
    run('perturbed-pair',[ROOT/'tools/run_pair.py','--pair','--config',out/'perturbed-config.json','--data',data,'--build',a.build,'--output',out/'perturbed'])
    changed=[json.loads(l) for l in (out/'perturbed/host/events.jsonl').read_text().splitlines()]
    after=[e for e in changed if e['type']=='decision' and e['query']==1]
    assertions['content_changes_later_expansion']=decisions[0]['expand']==after[0]['expand'] and decisions[1]['expand']!=after[1]['expand']
    reads=lambda es:[(e['kind'],e['id']) for e in es if e['type']=='read_submit' and e['query']==1]
    assertions['content_changes_actual_reads']=reads(events)!=reads(changed)
    assertions['perturbed_pair']=json.loads((out/'perturbed/comparison.json').read_text())['status']=='passed'
    write_json(out/'perturbation.json',dict(local_ids=[first,other],before=decisions[1],after=after[1],graph_sha256=x['graph']['sha256'],query_sha256=m['files']['query']['sha256']))
    write_json(out/'assertions.json',assertions)
    write_json(out/'ac-matrix.json',{'AC-02-05':'passed small real SIFT, both placements, independent batch replay, controlled content perturbation',
        'AC-02-06':'existing ledger/visibility checks exercised; new content backend/profile deferred to T4'})
    save(all(assertions.values()))
    if not all(assertions.values()): raise RuntimeError('T3 assertions failed')
    print('T3/M1 passed; T4 and later tasks not started')

if __name__=='__main__': main()
