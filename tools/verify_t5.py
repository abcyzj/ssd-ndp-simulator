"""T5 observation/lifecycle verification; fixed small base, never T6 scale work."""
import argparse
import copy
import json
import sys
from pathlib import Path
from ann_data import write_json
from verify_m0 import ROOT, measure, sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--prepared',type=Path,help='Optional T2 real small-data/index/exact')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); out=a.output.resolve(); build=a.build.resolve()
    if out.exists(): p.error('output must not exist')
    out.mkdir(parents=True); commands=[]; assertions={}; resources=[]; passed=False
    load=lambda path:json.loads(path.read_text())
    def command(name,argv):
        r=measure(list(map(str,argv)),out/(name+'.json'),3600,4*1024**3)
        commands.append(r)
        assert r['passed'],name
    base=load(ROOT/'configs/experiments/host.json')
    base['generate'].update(n=256,d=128,degree=8,queries=512)
    base['search'].update(k=10,width=16,budget=8)
    base.update(max_inflight=4)
    def run(name,c,data=None):
        cfg=out/(name+'-config.json');write_json(cfg,c)
        target=out/name
        argv=[sys.executable,ROOT/'tools/run_config.py','--config',cfg,'--build',build,'--output',target]
        if data:argv+=['--data',data]
        command(name,argv)
        result=load(target/'results.json'); stats=load(target/'statistics.json');stats.pop('wall_seconds')
        life=stats['resources']['lifecycle'][0]
        for key in ('runtime_tasks','waiters','leases','transitions','releases','host_history','protocol_tasks','transport_history','resource_history'):
            assert life[key]==0,(name,key,life)
        assert life['protocol_ranges']<=2
        # Fixed E/R/concurrency bound independent of total Q; includes complete
        # tasks retained within an active controller query until CloseAck.
        bound=c['max_inflight']*(c['search']['budget']+1)*(c['generate']['degree']+8)*4
        assert life['runtime_peak_tasks']<=bound
        samples=stats['resources']['lifecycle_sample']
        assert len(samples)==len(result['queries'])
        assert max(r['runtime_tasks'] for r in samples)<=bound
        assert max(r['sessions'] for r in samples)<=c['max_inflight']
        assert stats['credit_bytes_outstanding']==0
        resources.append(dict(name=name,queries=len(result['queries']),lifecycle=life,
            peak_rss_bytes=load(target/'sst-measurement.json')['peak_rss_bytes'],
            sample_task_max=max(r['runtime_tasks'] for r in samples),
            necessary_result_storage_bytes=(target/'results.json').stat().st_size,
            query_input_bytes=c['generate']['queries']*c['generate']['d']*4))
        if c['event_log']:
            digests={r['query']:14695981039346656037 for r in result['queries']}
            with (target/'events.jsonl').open() as f:
                for line in f:
                    row=json.loads(line)
                    if row['type']!='decision':continue
                    text='['+','.join('{"id":'+str(v['id'])+',"distance":'+format(v['distance'],'.17g')+'}' for v in row['candidates'])+']'
                    text+=''.join(':'+str(i) for i in row['expand'])
                    digest=digests[row['query']]
                    for byte in text.encode():digest=((digest^byte)*1099511628211)&((1<<64)-1)
                    digests[row['query']]=digest
            assert all(r['decision_digest_fnv1a64']==digests[r['query']] for r in result['queries'])
        return result,stats
    try:
        command('unit',[build/'observation_lifecycle_probe'])
        # One generated immutable base and query prefix per Q, reused for all
        # repeats/placements/log modes. Each Q holds N/d/R/E/concurrency fixed.
        for q in (32,128,512):
            shared=None
            for mode in ('host','controller'):
                baseline=None
                for repeat in range(3):
                    c=copy.deepcopy(base);c['generate']['queries']=q;c.update(placement=mode,event_log=False)
                    name=f'{mode}-q{q}-r{repeat}'
                    result=run(name,c,shared)
                    if shared is None: shared=out/name/'data'
                    if baseline is None:baseline=result
                    else:assert result==baseline
                if q==32:
                    c['event_log']=True
                    assert run(mode+'-q32-detail',c,shared)==baseline
            assertions[f'Q{q}_three_repeats_both_placements_bounded_state']=True
        assertions['statistics_detail_identical_all_results_and_simulated_statistics']=True
        for mode in ('host','controller'):
            small=[r['peak_rss_bytes'] for r in resources if r['name'].startswith(mode+'-q32-r')]
            large=[r['peak_rss_bytes'] for r in resources if r['name'].startswith(mode+'-q512-r')]
            delta=max(large)-min(small);limit=max(64*1024**2,.1*min(small))
            assert delta<=limit,(mode,delta,limit)
            assertions[mode+'_rss_growth_within_frozen_threshold']=dict(delta_bytes=delta,limit_bytes=limit,
                scope='wait4 total SST child RSS includes mapped pages/allocator; private RSS not isolated')
        if a.prepared:
            prep=a.prepared.resolve();c=copy.deepcopy(base)
            c.update(format='ndpsim-experiment-v2',index=str(prep/'index'),truth=str(prep/'exact'),event_log=True,max_inflight=1)
            c['search'].update(entry=load(prep/'index/index.json')['entry'],width=32,budget=64)
            cfg=out/'real-config.json';write_json(cfg,c)
            command('real-pair',[sys.executable,ROOT/'tools/run_pair.py','--pair','--config',cfg,'--data',prep/'small-data','--build',build,'--output',out/'real-pair'])
            assert load(out/'real-pair/comparison.json')['status']=='passed'
            assertions['real_sift_10k_detail_decisions_distances_flow_credit_independent_comparison']=True
        c=copy.deepcopy(base);c['generate'].update(n=32,queries=1);c.update(output_budget_bytes=128,event_log=True)
        cfg=out/'budget-config.json';write_json(cfg,c)
        r=measure(list(map(str,[sys.executable,ROOT/'tools/run_config.py','--config',cfg,'--build',build,'--output',out/'budget'])),out/'budget-failure.json',120)
        commands.append(dict(r,expected_failure=True))
        assert r['exit_code']!=0 and not (out/'budget/manifest.json').exists()
        assert 'budget exhausted' in (out/'budget/sst.log').read_text()
        assertions['explicit_output_budget_exhaustion_fails_without_success_manifest']=True
        passed=True
    finally:
        write_json(out/'assertions.json',assertions)
        write_json(out/'manifest.json',dict(passed=passed,scope='T5 fixed small N256/d128/R8/E8/Q32,128,512/concurrency4, three repeats; optional real T3 fixture',
            commands=commands,resources=resources,source_sha256={str(f.relative_to(ROOT)):sha(f) for f in [ROOT/'tools/verify_t5.py',ROOT/'tools/run_graph.py',ROOT/'tools/experiment_output.py',*sorted((ROOT/'src').rglob('*.cc')),*sorted((ROOT/'src').rglob('*.h'))]},
            rss_method='wait4 SST child peak, macOS bytes/Linux KiB converted; not private memory or simultaneous process tree',
            disk_bytes_snapshot=sum(f.stat().st_size for f in out.rglob('*') if f.is_file())))
        write_json(out/'ac-matrix.json',{'AC-02-08':dict(status='passed' if passed else 'failed',scope='fixed small base, lifecycle counts and Q gradient; no full dataset matrix'),'AC-02-12':'see separate CTest/full V0.1 acceptance'})
    print('T5 PASS; no next task started')

if __name__=='__main__':main()
