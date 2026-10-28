"""M4.1 real SST concurrency, admission, output and observation acceptance."""
import argparse
import copy
import csv
import json
import subprocess
import sys
from pathlib import Path
from ann_data import write_json, digest
from run_m3_1 import ROOT
from verify_m1_1 import mqsim_source_snapshot


def union(intervals):
    points = {}
    for a,b in intervals:
        assert b >= a
        points[a] = points.get(a,0)+1
        points[b] = points.get(b,0)-1
    total = last = depth = 0
    for t, change in sorted(points.items()):
        if depth: total += t-last
        depth += change
        last = t
    assert depth == 0
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Small runtime matrix for CTest; no recursive build')
    parser.add_argument('--matrix-only', action='store_true', help='Full matrix with an already built platform')
    parser.add_argument('--build', type=Path, default=ROOT/'build/m4.1')
    parser.add_argument('--output', type=Path, default=ROOT/'docs/evidence/m4.1')
    a = parser.parse_args(); out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    commands=[]; assertions=[]; runs={}
    def command(name, argv, expected=0):
        p=subprocess.run(list(map(str,argv)),cwd=ROOT,capture_output=True,text=True,timeout=600)
        (out/(name+'.log')).write_text(p.stdout+p.stderr)
        commands.append(dict(name=name,argv=list(map(str,argv)),exit_code=p.returncode,expected=expected))
        assert p.returncode == expected, (name,p.stdout[-1000:],p.stderr[-1000:])
        return p.stdout
    def run(name,c,success=True):
        config=out/(name+'-config.json');write_json(config,c)
        command(name,[sys.executable,ROOT/'tools/run_m4_1.py','--config',config,'--build',a.build,'--output',out/name],0 if success else 1)
        if not success:return
        result=json.loads((out/name/'results.json').read_text()); stats=json.loads((out/name/'statistics.json').read_text())
        q=result['queries'];summary=result['summary']
        assert summary['live_results']==summary['live_content']==0
        assert summary['peak_inflight']==min(len(q),summary['admission_limit'])
        assert summary['admission_limit']<=c['max_inflight']
        assert stats['credit_bytes_outstanding']==0
        for key in ('latency_ns','admission_wait_ns','offered_latency_ns'):
            assert stats[key]['samples']==len(q) and stats[key]['method']=='nearest_rank'
        assert all(r['end_ns']-r['start_ns']==r['latency_ns'] and r['start_ns']-r['offered_ns']==r['admission_wait_ns'] and r['end_ns']-r['offered_ns']==r['offered_latency_ns'] for r in q)
        assert len(list(csv.DictReader((out/name/'queries.csv').open())))==len(q)
        assert len(list(csv.DictReader((out/name/'top-k.csv').open())))==sum(len(r['top_k']) for r in q)
        assert (out/name/'events.jsonl').exists()==c['event_log']
        cap=json.loads((out/name/'capabilities.json').read_text());assert cap['plane']==cap['latch']=='Unsupported'
        assert stats['resources']['ssd_stat'] and stats['resources']['link_summary'] and stats['resources']['ordinary_storage']
        if c['placement']=='controller':
            b=stats['resources']['buffer_summary'][0];assert b['peak_bytes']<=b['capacity_bytes']
            assert stats['resources']['local_storage'] and stats['resources']['controller_service']
        if c['event_log']:
            events=[json.loads(l) for l in (out/name/'events.jsonl').read_text().splitlines()]
            intervals=[e for e in events if e['type']=='ssd_interval']
            assert intervals and all(e['device']==0 and e['time_ns']==e['end_ns'] for e in intervals)
            for stat in stats['resources']['ssd_stat']:
                selected=[(e['start_ns'],e['end_ns']) for e in intervals if e['channel']==stat['channel'] and ((stat['kind']=='bus' and 'bus' in e['kind']) or (stat['kind']==e['kind'] and stat['chip']==e['chip'] and stat['die']==e['die']))]
                assert union(selected)==stat['union_ns']<=stats['measurement_window_ns']
            submits={e['request']:e for e in events if e['type']=='read_submit'}
            completed=[e for e in events if e['type']=='read_complete']
            assert len(submits)==len(completed)
            for e in completed:
                start=submits[e['request']]; assert e['query']==start['query'] and e['task']==start['task'] and e['time_ns']>=start['time_ns']
            for r in stats['resources']['link_summary']:
                selected=[e for e in events if e['type']=='link' and e['phase']=='arrive' and e['destination']==r['destination']]
                assert len(selected)==r['transfers'] and sum(e['wire'] for e in selected)==r['wire_bytes'] and sum(e['payload'] for e in selected)==r['payload_bytes']
        stats.pop('wall_seconds');runs[name]=(result,stats)
        return result,stats
    try:
        if not a.quick and not a.matrix_only:
            cmake=json.loads((ROOT/'deps.lock.json').read_text())['tools']['cmake']['path']
            command('configure',[cmake,'-S',ROOT,'-B',a.build]);command('build',[cmake,'--build',a.build,'-j4'])
            command('ctest',[Path(cmake).with_name('ctest'),'--test-dir',a.build,'--output-on-failure'])
        base=json.loads((ROOT/'configs/m4.1/host.json').read_text())
        if a.quick:
            base['generate'].update(n=32,d=32,degree=5,queries=5)
            base['search'].update(k=3,width=8,budget=4)
        for mode in ('host','controller'):
            for concurrency in (1,2,4):
                c=copy.deepcopy(base);c.update(placement=mode,max_inflight=concurrency,event_log=True)
                run(f'{mode}-c{concurrency}',c)
            on=runs[mode+'-c4'];c.update(event_log=False)
            assert run(mode+'-off',c)==on
            assert run(mode+'-repeat',c)==on
            baseline=runs[mode+'-c1'][0]['queries']
            assert [[r['top_k'] for r in runs[f'{mode}-c{x}'][0]['queries']] for x in (2,4)]==[[r['top_k'] for r in baseline]]*2
            assertions.append(f'{mode}: concurrency 1/2/4 complete, query IDs and timing/CSV/quantiles checked; off/on/repeat identical including resources; independent SSD union and link/request conservation')
        assert [r['top_k'] for r in runs['host-c4'][0]['queries']]==[r['top_k'] for r in runs['controller-c4'][0]['queries']]
        c=copy.deepcopy(base);c.update(placement='controller',max_inflight=4,event_log=True)
        c['generate'].update(n=64,d=32,degree=5,queries=8);c['search'].update(k=3,width=8,budget=6,chunk=1)
        c['controller']['capacity_bytes']=1152
        _,stats=run('one-block',c);b=stats['resources']['buffer_summary'][0]
        assert b['admission_limit']==4 and b['credit_wait_ns']>0 and b['peak_bytes']<=1152
        c['search']['chunk']=2;c['controller']['capacity_bytes']=1376
        result,stats=run('admission-credit',c)
        assert result['summary']['admission_limit']==2 and stats['admission_wait_ns']['p99']>0
        assertions.append('one-block C=1152, chunk=1, inflight=4: positive credit wait and all 8 queries finish; C=1376 chunk=2: max_inflight=4 throttled to 2 with positive admission wait; credits reclaimed')
        c['controller']['capacity_bytes']=703;run('too-small',c,False)
        for value in (0,-1,True,1.5):
            c=copy.deepcopy(base);c['max_inflight']=value;run('invalid-'+str(value),c,False)
        assertions.append('too-small buffer and zero/negative/bool/fractional max_inflight fail with nonzero exit')
        command('diff-check',['git','diff','--check'])
        assert not command('sst-status',['git','-C','third_party/sst-core','status','--porcelain'])
        assert not command('mqsim-status',['git','-C','third_party/MQSim','status','--porcelain'])
        status='passed'
    finally:
        inputs=[ROOT/'AGENTS.md',ROOT/'CMakeLists.txt',ROOT/'deps.lock.json',*sorted((ROOT/'src').rglob('*.cc')),*sorted((ROOT/'src').rglob('*.h')),*[ROOT/'tools'/n for n in ('verify_m4_1.py','run_m4_1.py','run_m3_1.py','experiment_output.py','ann_data.py')]]
        write_json(out/'manifest.json',dict(status=locals().get('status','failed'),commands=commands,assertions=assertions,mqsim_source=mqsim_source_snapshot(),input_sha256={str(p.relative_to(ROOT)):digest(p) for p in inputs},log_sha256={p.name:digest(p) for p in out.glob('*.log')}))
    print('M4.1 PASS',len(assertions),'groups',out)

if __name__=='__main__':main()
