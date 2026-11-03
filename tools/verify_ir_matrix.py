"""V03-6 only: full locked 52-point matrix, alternating efficiency and Q gates.

Always writes a failed/incomplete manifest on error. No historical run reuse,
query resampling, graph rebuild, default-engine switch or V03-7 execution.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import statistics as mathstats
import subprocess
import sys
import signal
from ann_data import write_json, generate, exact_truth
from freeze_direct_baseline import BaselineDiskMonitor, verify_hashes
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import (GIB, LOCK, MATRIX, SELECTION, BUDGET, load, points,
                       query_groups, validate_plan, validate_prepared,
                       verify_cached_run, resource_rows, result_signature, load_credit_zero)


def schedule():
    # The two matrix repetitions supply groups 1/2; a third group supplements
    # them. Each placement is D,I,D,I,D,I in the actual sequential schedule.
    original = points()
    evaluation = [p for p in original if p['group']=='evaluation' and p['L']==128]
    extra = [dict(p, name=p['name'].replace('-repeat-', '-efficiency3-'), matrix=False)
             for p in evaluation if '-repeat-' in p['name']]
    ordered = evaluation + extra + [p for p in original if p not in evaluation]
    return [dict(p, matrix=p.get('matrix', True), engine=e) for p in ordered for e in ('direct','ir')]


def efficiency(samples, budget):
    result = {}
    for mode in ('host','controller'):
        rows = [s for s in samples if s['placement']==mode]
        if [r['engine'] for r in rows] != ['direct','ir']*3:
            raise ValueError('efficiency requires three alternating D/I groups per placement')
        d = [r for r in rows if r['engine']=='direct']; i = [r for r in rows if r['engine']=='ir']
        ratio = mathstats.median(r['query_wall_seconds'] for r in i)/mathstats.median(r['query_wall_seconds'] for r in d)
        increments = [b['sst_peak_rss_bytes']-a['sst_peak_rss_bytes'] for a,b in zip(d,i)]
        limits = [max(budget['ir_rss_increment_bytes_floor'],budget['ir_rss_increment_fraction']*r['sst_peak_rss_bytes']) for r in d]
        result[mode] = dict(samples=rows, median_wall_ratio=ratio, rss_increments_bytes=increments,
            rss_increment_limits_bytes=limits, passed=ratio<=budget['query_wall_ratio_ir_direct_max']
            and all(x<=y for x,y in zip(increments,limits)))
    return result


def run_checks(target, point, budget, full=True):
    r=load(target/'results.json'); m=load(target/'sst-measurement.json')
    life=next(resource_rows(target,'lifecycle')); ph=m['phases_wall_seconds']
    checks=dict(sst_rss=m['peak_rss_bytes']<=budget['sst_peak_rss_bytes_max'],
        python_rss=m['python_parent_peak_rss_bytes']<=budget['python_peak_rss_bytes_max'],
        initialization=ph['initialization_wall_seconds']<=budget['initialization_seconds_max'],
        query_wall=ph['query_wall_seconds']<=budget['query_seconds_max'],
        protocol_ranges_bounded=life['protocol_ranges']<=2,
        postprocess=m['postprocessing_wall_seconds']<=budget['postprocessing_seconds_max'],
        credit_zero=load_credit_zero(target),
        reclaimed=all(life[k]==0 for k in ('runtime_tasks','waiters','leases','transitions','releases',
            'host_history','protocol_tasks','transport_history','resource_history')))
    if point['engine']=='ir':
        ir=load(target/'observation.json')['ir']
        checks['ir_drained']=ir['active_ops']==ir['active_values']==ir['failed']==0 and ir['submitted']==ir['completed']
        checks['operator_conservation']=all(o['submitted']==o['completed'] and not o['failed'] for o in ir['operators']) and sum(o['submitted'] for o in ir['operators'])==ir['submitted']
    if full:
        initial=next(resource_rows(target,'initial_state')); summary=r['summary']
        paths=next(resource_rows(target,'path_summary')); buffers=list(resource_rows(target,'buffer_summary'))
        c=load(target/'config.json')
        checks.update(query_identity=[q['original_query_id'] for q in r['queries']]==query_groups()[point['group']],
            full_layout=load(target/'preflight.json')['actual']['preload_sectors']==2000000,
            initialized=initial['backend_drained'] and initial['backend_pending']==0 and initial['completed_preload_sectors']==2000000
                and initial['origin_ns']==summary['origin_ns'] and initial['cmt_retained'] and not initial['time_reset'],
            vector_zero_dma=point['placement']!='controller' or paths['ordinary_dma_bytes']==sum(q['expanded'] for q in r['queries'])*512,
            buffer_bounded=not buffers or buffers[0]['peak_bytes']<=c['controller']['capacity_bytes'],
            admission=summary['admission_limit']==point['concurrency'] and 0<summary['peak_inflight']<=point['concurrency'],
            time_window=0<summary['origin_ns']<summary['end_ns']<=summary['drain_end_ns'],
            offered=all(q['offered_ns']==summary['origin_ns'] and q['start_ns']>=summary['origin_ns'] for q in r['queries']))
    return checks, dict(name=target.name,engine=point['engine'],placement=point['placement'],
        query_wall_seconds=ph['query_wall_seconds'],sst_peak_rss_bytes=m['peak_rss_bytes'],
        measurement=m,recall_at_10=r['recall_at_k'],expanded=sum(q['expanded'] for q in r['queries']))


def gradient_inputs(out):
    """One graph/base and nested query prefixes; generator otherwise changes graph with Q."""
    source=out/'q512'; generate(source,n=2048,d=32,degree=16,queries=512,seed=42)
    exact_truth(source,10)
    for q in (32,128):
        target=out/f'q{q}'; target.mkdir()
        for name in ('vectors.f32','adjacency.u32'): shutil.copyfile(source/name,target/name)
        (target/'queries.f32').write_bytes((source/'queries.f32').read_bytes()[:q*32*4])
        m=load(source/'dataset.json');m['queries']=q;m['generator']+='; fixed Q512 graph, query prefix'
        m['sha256']['queries.f32']=sha(target/'queries.f32');write_json(target/'dataset.json',m)
        exact_truth(target,10)


def gradient_report(rows,budget):
    report={}
    for mode in ('host','controller'):
        selected=[r for r in rows if r['placement']==mode]
        byq={q:[r for r in selected if r['Q']==q] for q in budget['q_gradient']}
        if any(len(v)!=budget['q_repeats'] for v in byq.values()): raise ValueError('incomplete Q gradient')
        low=mathstats.median(r['sst_peak_rss_bytes'] for r in byq[32]); high=mathstats.median(r['sst_peak_rss_bytes'] for r in byq[512])
        limit=max(budget['q_rss_increment_bytes_floor'],budget['q_rss_increment_fraction']*low)
        baseline=byq[32][0]['ir']
        bounded=all(r['ir']['peak_ops']<=baseline['peak_ops'] and r['ir']['peak_values']<=baseline['peak_values']
            and not r['ir']['active_ops'] and not r['ir']['active_values'] for r in selected)
        report[mode]=dict(samples=selected,median_rss_q32=low,median_rss_q512=high,increment_bytes=high-low,
            increment_limit_bytes=limit,active_history_bounded=bounded,passed=bounded and high-low<=limit,
            necessary_storage='query input bytes and serialized terminal results reported per sample; not private heap attribution')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--asan-build',type=Path,default=ROOT/'build/v036-asan')
    p.add_argument('--prepared',type=Path,default=ROOT/'results/v0.2-t6/preparation/prepared')
    p.add_argument('--source-dir',type=Path,default=ROOT/'.cache/v0.2-m0/data/sift')
    p.add_argument('--ram-evidence',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();build=a.build.resolve();prepared=a.prepared.resolve();raw=a.source_dir.resolve()
    lock=load(ROOT/'deps.lock.json')
    if sys.flags.optimize or Path(sys.executable).resolve()!=Path(lock['tools']['python']['path']).resolve():p.error('use locked Python without -O')
    if out.exists():p.error('output must not exist')
    out.mkdir(parents=True); commands=[];checks={};runs=[];error=None
    def interrupted(signum, frame):raise RuntimeError('verification interrupted by signal '+str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    before=inventory();write_json(out/'source-start.json',before)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    def command(name,argv,timeout=600,rss=512*1024**2,env=None):
        r=measure(list(map(str,argv)),out/(name+'.json'),timeout,rss,env=env)
        commands.append(dict(name=name,**r));print(f'{name}: exit={r["exit_code"]} wall={r["wall_seconds"]:.2f}s RSS={r["peak_rss_bytes"]}',flush=True)
        if not r['passed']:raise RuntimeError('command failed: '+name)
    def compare(name,left,right):
        command(name,[sys.executable,ROOT/'tools/compare_engines.py',left,right,'--output',out/(name+'-report.json')])
        checks[name]=load(out/(name+'-report.json'))['passed']
    def run(name,c,data,point,full=True):
        cfg=out/(name+'-config.json');write_json(cfg,c);target=out/name
        command(name,[sys.executable,ROOT/'tools/run_config.py','--config',cfg,'--build',build,'--output',target,'--data',data],11400,None)
        if full:verify_cached_run(target,c,prepared,point['group'],build)
        check,row=run_checks(target,point,budget,full);checks[name]=all(check.values())
        write_json(out/(name+'-checks.json'),check)
        row.update(matrix=point.get('matrix',False),point=point)
        runs.append(row);write_json(out/'scan.json',runs)
        return row
    try:
        with BaselineDiskMonitor(out,[raw,prepared,ROOT/'results/v0.3']):
            for name,rel in [('direct','direct-baseline-final'),('schema','schema-v031/final'),('lifecycle','dynamic-v032/final3'),
                ('mapping','mapping-v033/final4'),('frontend','frontend-v034/final2'),('observation','observation-v035/final2')]:
                path=ROOT/'results/v0.3'/rel;m=load(path/'manifest.json')
                if m.get('passed') is not True:raise ValueError('dependency not passed: '+name)
                verify_hashes(path,m['artifacts_sha256']);checks['dependency-'+name]=True
                write_json(out/('dependency-'+name+'.json'),dict(manifest=str(path/'manifest.json'),sha256=sha(path/'manifest.json'),artifacts=len(m['artifacts_sha256'])))
            frozen=ROOT/'results/v0.3/direct-baseline-final';budget=load(frozen/'budget-decision.json')
            budget_hash=sha(frozen/'budget-decision.json');write_json(out/'budget-frozen.json',budget)
            ram=before['physical_ram_bytes']
            if ram is None and a.ram_evidence:
                ram=int(a.ram_evidence.read_text().strip());(out/'physical-ram.txt').write_bytes(a.ram_evidence.read_bytes())
            if ram!=budget['physical_ram_bytes'] or before['sst_status']:raise ValueError('RAM changed/unavailable or modified SST')
            monitor=BaselineDiskMonitor(out,[raw,prepared,ROOT/'results/v0.3']);monitor.sample()
            if monitor.peak>budget['disk_peak_bytes_max'] or monitor.minimum_free<budget['volume_reserve_bytes_min']:raise ValueError('disk preflight failed')
            validate_plan();validate_prepared(prepared);saved=load(prepared/'prepared.json');verify_hashes(prepared,saved['files_sha256'])
            if saved['query_selection_sha256']!=sha(SELECTION) or saved['dataset_lock_sha256']!=sha(LOCK):raise ValueError('prepared lock mismatch')
            inputs=load(frozen/'inputs.json')
            if saved!=inputs['preparation']:raise ValueError('prepared identity differs from frozen reference')
            for name,digest in inputs['locks'].items():
                if sha(ROOT/name)!=digest:raise ValueError('frozen input/config lock changed: '+name)
            for info in inputs['raw'].values():
                path=raw/Path(info['path']).name
                if path.stat().st_size!=info['bytes'] or sha(path)!=info['sha256']:raise ValueError('raw input mismatch')
            if load(prepared/'index/index.json')['builder']['receipt']!=inputs['builder']:raise ValueError('builder changed')
            for path,digest in [(ROOT/'configs/benchmarks/m0-builder.lock.json',inputs['builder']['lock_sha256']),
                (ROOT/'tools/offline/index_builder.cc',inputs['builder']['adapter_sha256']),
                (Path(inputs['builder']['executable']),inputs['builder']['executable_sha256'])]:
                if sha(path)!=digest:raise ValueError('builder identity mismatch')
            write_json(out/'inputs.json',inputs);write_json(out/'schedule.json',schedule());checks['preflight']=True
            cmake=lock['tools']['cmake']['path']
            command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON','-DNDPSIM_SLOW_FUNCTIONAL=OFF'],3600,None)
            command('build',[cmake,'--build',build,'-j2'],3600,None)
            command('ctest',['ctest','--test-dir',build,'--output-on-failure'],3600,None)
            asan=a.asan_build.resolve()
            command('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DBUILD_TESTING=ON','-DNDPSIM_SANITIZE=ON'],3600,None)
            command('asan-build',[cmake,'--build',asan,'--target','ir_executor_probe','ir_schema_probe','ir_frontend_probe','ir_frontend_causal_probe','-j2'],3600,None)
            command('asan-tests',['ctest','--test-dir',asan,'--output-on-failure','-R','^(ir_dynamic_lifecycle|ir_schema_memory|ir_graph_frontend|ir_graph_frontend_causality)$'],3600,None,env={'ASAN_OPTIONS':'halt_on_error=1'})
            # Recheck both direct and IR independently against immutable frozen
            # results, simulated stats and original resource streams.
            for mode in ('host','controller'):
                for engine in ('direct','ir'):
                    ref=frozen/('small-'+mode);c=load(ref/'config.json');c['execution_engine']=engine
                    name='anchor-'+engine+'-'+mode
                    run(name,c,ref/'data',dict(engine=engine,placement=mode),False)
                    # Frozen pre-observation files need results/statistics-only
                    # anchor verification; no weakening of frozen fields.
                    from verify_t6 import statistics as stat_hash
                    if engine=='direct':
                        checks[name+'-frozen']=load(out/name/'results.json')==load(ref/'results.json') and stat_hash(out/name)==stat_hash(ref) and sha(out/name/'events.jsonl')==sha(ref/'events.jsonl')
                    else:compare(name+'-equivalence',out/('anchor-direct-'+mode),out/name)
            base=load(MATRIX)['experiment']; entry=load(prepared/'index/index.json')['entry']
            for point in schedule():
                name=point['engine']+'-'+point['name'];c=copy.deepcopy(base)
                c.update(execution_engine=point['engine'],placement=point['placement'],index=str(prepared/'index'),truth=str(prepared/(point['group']+'-truth')),
                    max_inflight=point['concurrency'],event_log=point['detail'],output_budget_bytes=budget['output_budget_bytes_per_run'])
                c['search'].update(entry=entry,width=point['L'],budget=point['E'])
                run(name,c,prepared/point['group'],point)
                if point['engine']=='ir':compare('equivalence-'+point['name'],out/('direct-'+point['name']),out/name)
            samples=[r for r in runs if r['point'].get('group')=='evaluation' and r['point']['L']==128]
            eff=efficiency(samples,budget);write_json(out/'efficiency.json',eff);checks['efficiency']=all(r['passed'] for r in eff.values())
            # Complete repeated/mode/placement/concurrency checks for each engine.
            for engine in ('direct','ir'):
                for mode in ('host','controller'):
                    first=out/f'{engine}-evaluation-l128-e256-c1-{mode}'
                    for suffix in ('repeat','efficiency3'):
                        compare(f'repeat-{engine}-{mode}-{suffix}',first,out/f'{engine}-evaluation-l128-e256-c1-{suffix}-{mode}')
                    compare(f'logging-{engine}-{mode}',out/f'{engine}-detail-l128-e256-c1-{mode}',out/f'{engine}-detail-l128-e256-c1-logged-{mode}')
                    expected=result_signature(load(first/'results.json'))[:32]
                    for concurrency in (2,4):checks[f'concurrency-{engine}-{mode}-{concurrency}']=expected==result_signature(load(out/f'{engine}-concurrency-l128-e256-c{concurrency}-{mode}'/'results.json'))
                for point in points():
                    if point['placement']=='host':
                        stem=point['name'].removesuffix('host')
                        checks['placement-'+engine+'-'+stem]=result_signature(load(out/(engine+'-'+stem+'host')/'results.json'))==result_signature(load(out/(engine+'-'+stem+'controller')/'results.json'))
                command('independent-'+engine,[sys.executable,ROOT/'tools/compare_placements.py',out/f'{engine}-detail-l128-e256-c1-logged-host',out/f'{engine}-detail-l128-e256-c1-logged-controller','--output',out/('independent-'+engine+'-report.json')])
            quality=[dict(engine=r['engine'],placement=r['placement'],group=r['point']['group'],L=r['point']['L'],E=r['point']['E'],recall_at_10=r['recall_at_10']) for r in runs if r['matrix'] and r['point']['group'] in ('tuning','evaluation')]
            write_json(out/'quality.json',quality)
            matched=[]
            for engine in ('direct','ir'):
                for mode in ('host','controller'):
                    for target in (.80,.90,.95):
                        candidates=[r for r in runs if r['matrix'] and r['engine']==engine and r['placement']==mode
                            and r['point'].get('group')=='tuning' and r['recall_at_10']>=target]
                        choice=min(candidates,key=lambda r:sum(q['latency_ns'] for q in load(out/r['name']/'results.json')['queries'])) if candidates else None
                        evaluation=next((r for r in quality if choice and r['engine']==engine and r['placement']==mode and r['group']=='evaluation' and r['L']==choice['point']['L']),None)
                        matched.append(dict(engine=engine,placement=mode,target_recall=target,status='matched_on_tuning' if choice else 'unmatched',
                            tuning_run=choice['name'] if choice else None,independent_evaluation=evaluation,
                            evaluation_meets_target=bool(evaluation and evaluation['recall_at_10']>=target)))
            write_json(out/'quality-matched.json',matched)
            checks['quality']=load(prepared/'builder-quality.json')['passed'] and all(r['recall_at_10']>=.9 for r in quality if r['L']==256)
            gradient_inputs(out/'gradient-inputs');grad=[]
            for q in budget['q_gradient']:
                data=out/'gradient-inputs'/f'q{q}'
                for repeat in range(budget['q_repeats']):
                    for mode in ('host','controller'):
                        c=load(ROOT/'configs/experiments/host.json');c.update(execution_engine='ir',placement=mode,content_backend='mmap')
                        name=f'gradient-q{q}-r{repeat}-{mode}'
                        row=run(name,c,data,dict(engine='ir',placement=mode),False)
                        row=dict(row,Q=q,repeat=repeat,ir=load(out/name/'observation.json')['ir'],
                            query_input_bytes=(data/'queries.f32').stat().st_size,terminal_results_file_bytes=(out/name/'results.json').stat().st_size,
                            lifecycle=next(resource_rows(out/name,'lifecycle')))
                        grad.append(row);write_json(out/'gradient-samples.json',grad)
            gr=gradient_report(grad,budget);write_json(out/'gradient.json',gr);checks['gradient']=all(r['passed'] for r in gr.values())
            checks['matrix_52']=len([r for r in runs if r['matrix']])==52
            checks['frozen_budget_unchanged']=sha(frozen/'budget-decision.json')==budget_hash
            verify_hashes(prepared,saved['files_sha256'])
        checks['disk']=load(out/'disk-measurement.json')['passed']
        after=inventory();write_json(out/'source-end.json',after)
        checks['source_unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies_unchanged']=before['mqsim']==after['mqsim'] and before['sst_head']==after['sst_head'] and not after['sst_status']
        command('diff-check',['git','diff','--check'])
        if not all(checks.values()):raise ValueError('V03-6 gates failed; inspect assertions, efficiency and gradient')
    except Exception as exc:error=str(exc)
    write_json(out/'assertions.json',checks)
    write_json(out/'manifest.json',dict(schema='ndpsim-ir-matrix-v036-v1',task='V03-6',passed=error is None,error=error,
        commands=commands,completed_matrix_points=len([r for r in runs if r['matrix']]),expected_matrix_points=52,
        artifacts_sha256={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file() and f!=out/'manifest.json'},
        limitations='V03-6 only; no V03-7/default switch/full inherited AC/native oracle; ASan is targeted IR only. configured_unvalidated; official ties unresolved; fixed subsets; whole-process wait4 RSS, no phase/private/tree RSS; 1s logical disk samples.'))
    print('V03-6 '+('PASS' if error is None else 'FAIL: '+error),flush=True)
    return int(error is not None)

if __name__=='__main__':sys.exit(main())
