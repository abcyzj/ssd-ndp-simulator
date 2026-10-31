"""T4 content/profile acceptance; small fixtures, never T5 or scale experiments."""
import argparse
import json
import sys
from pathlib import Path
from ann_data import write_json
from verify_m0 import ROOT, measure, sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True,help='T2 verified real small-data/index/exact')
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();prepared=a.prepared.resolve()
    if out.exists():p.error('output must not exist')
    out.mkdir(parents=True);commands=[];assertions={};passed=False
    def command(label,argv):
        r=measure(list(map(str,argv)),out/(label+'.json'),3600,4*1024**3)
        commands.append(r)
        if not r['passed']:raise RuntimeError('failed '+label)
    def load(path):return json.loads(path.read_text())
    def same(left,right):
        for name in ('results.json','statistics.json'):
            x,y=load(left/name),load(right/name)
            x.pop('wall_seconds',None);y.pop('wall_seconds',None)
            assert x==y,(name,left,right)
        assert (left/'events.jsonl').read_bytes()==(right/'events.jsonl').read_bytes()
    c=load(ROOT/'configs/experiments/host.json');c['event_log']=True
    try:
        for profile in ('native-small','ann-2g-v1','ann-16g-v1'):
            command('capacity-'+profile,[a.build.resolve()/'profile_probe',profile])
            command('namespace-overflow-'+profile,[a.build.resolve()/'profile_probe',profile,'overflow'])
        command('mapped-unit',[a.build.resolve()/'mapped_content_probe'])
        assertions['actual_2g_16g_namespace_and_preallocation_rejections']=True
        for label,d,profile in [('sift-d128',128,'ann-2g-v1'),('deep-d96',96,'ann-16g-v1'),('cross-page-d260',260,'ann-2g-v1')]:
            c.update(device_profile=profile,max_inflight=4)
            c['generate'].update(n=32,d=d,degree=8,queries=4)
            c['controller']['vector_sector_bound']=(4*d+511)//512*512
            shared=None
            for backend in ('memory','mmap'):
                c['content_backend']=backend
                cfg=out/(label+'-'+backend+'-config.json');write_json(cfg,c)
                target=out/(label+'-'+backend)
                argv=[sys.executable,ROOT/'tools/run_pair.py','--pair','--config',cfg,'--build',a.build,'--output',target]
                if shared:argv+=['--data',shared]
                command(label+'-'+backend,argv)
                shared=out/(label+'-memory/host/data')
            for placement in ('host','controller'):
                same(out/(label+'-memory')/placement,out/(label+'-mmap')/placement)
            assertions[label+'_both_placements_bytes_events_results_statistics_equal']=True
            report=load(out/(label+'-mmap/comparison.json'))
            assert report['controller']['vector_d2h_bytes']==0 and report['status']=='passed'
            stats=load(out/(label+'-mmap/controller/statistics.json'))
            credits=stats['resources']['credit']
            assert credits[-1]['used']==0 and max(r['used'] for r in credits)<=c['controller']['capacity_bytes']
            expected_admission = 2 if d == 260 else 4
            assert load(out/(label+'-mmap/controller/results.json'))['summary']['peak_inflight']==expected_admission
            assertions[label+'_ledger_credit_admission'+str(expected_admission)]=True
        # Real T3 functional baseline, same prepared graph/query/truth in both backends.
        c.update(format='ndpsim-experiment-v2',device_profile='ann-2g-v1',max_inflight=1,
                 index=str(prepared/'index'),truth=str(prepared/'exact'))
        c['search']['entry']=load(prepared/'index/index.json')['entry']
        c['controller']['vector_sector_bound']=512
        for backend in ('memory','mmap'):
            c['content_backend']=backend;cfg=out/('real-'+backend+'-config.json');write_json(cfg,c)
            command('real-'+backend,[sys.executable,ROOT/'tools/run_pair.py','--pair','--config',cfg,'--data',prepared/'small-data','--build',a.build,'--output',out/('real-'+backend)])
        for placement in ('host','controller'):same(out/'real-memory'/placement,out/'real-mmap'/placement)
        assertions['real_sift_10k_32_query_both_backends_placements_equivalent']=True
        passed=True
    finally:
        write_json(out/'assertions.json',assertions)
        write_json(out/'manifest.json',dict(scope='T4/M2 small content/profile acceptance; no full-capacity preload or scale query matrix',passed=passed,
            commands=commands,source_sha256={str(f.relative_to(ROOT)):sha(f) for f in [ROOT/'tools/verify_t4.py',ROOT/'tools/device_profiles.py',ROOT/'src/content/content.cc',ROOT/'src/sst/graph_host.cc']},
            disk_bytes_snapshot=sum(f.stat().st_size for f in out.rglob('*') if f.is_file())))
        write_json(out/'ac-matrix.json',{'AC-02-06':dict(status='passed' if passed else 'failed',scope='T4 profiles, small reads/credit/equivalence; full target preload deferred T6/T7'),'AC-02-12':'see separate full V0.1 acceptance evidence'})
    print('T4 PASS; no next task started')

if __name__=='__main__':main()
