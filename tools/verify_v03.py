"""V03-7 full delivery: inherited AC, current 52-point IR matrix and default switch.

No quick mode. Matrix reuse requires complete immutable evidence and identical
execution/validation sources, element, dependencies, configuration and inputs.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from ann_data import write_json
from freeze_direct_baseline import BaselineDiskMonitor, verify_hashes
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import load, verify_cached_run, statistics, points
from verify_ir_matrix import schedule, efficiency, gradient_report


def require_suite(root):
    m=load(root/'manifest.json')
    if m.get('passed') is not True:
        raise ValueError('incomplete/failed suite: '+str(root))
    verify_hashes(root,m['artifacts_sha256'])
    return m


def acceptance_matrix(v01, inherited, matrix, compatibility, checks):
    expected={f'AC-{i:02}' for i in range(1,12)}
    if v01.get('scope')!='full M4.2' or v01.get('status')!='passed' or set(v01.get('checks',{}))!=expected:
        raise ValueError('full V0.1 AC required; quick/missing evidence rejected')
    if any(not rows or any(r.get('status')!='passed' for r in rows) for rows in v01['checks'].values()):
        raise ValueError('V0.1 assertions failed')
    if set(inherited)!={'t3','t4','t5'} or any(m.get('passed') is not True for m in inherited.values()):
        raise ValueError('inherited V0.2 suites incomplete')
    if matrix.get('schema')!='ndpsim-ir-matrix-v036-v1' or matrix.get('passed') is not True or matrix.get('completed_matrix_points')!=52 or matrix.get('expected_matrix_points')!=52:
        raise ValueError('complete current 52-point matrix required')
    required={'matrix_identity','matrix_gates','ir_asan','dependencies','source_unchanged','dependencies_unchanged','disk'}
    required_compat={f'{mode}-{key}' for mode in ('host','controller') for key in ('default_ir','direct','equivalence','frozen','fresh_full')}
    if set(checks)!=required or any(v is not True for v in checks.values()):
        raise ValueError('delivery gates missing/failed')
    if set(compatibility)!=required_compat or any(v is not True for v in compatibility.values()):
        raise ValueError('default/direct compatibility incomplete')
    evidence={
        1:['matrix/ctest.log: ir_schema_catalog, ir_schema_memory','dependencies.json'],
        2:['matrix/ctest.log: ir_graph_frontend, sst_ir_graph_frontend','matrix/independent-ir-report.json'],
        3:['matrix/ctest.log: sst_ir_graph_frontend','t3/assertions.json'],
        4:['matrix/ctest.log: sst_ir_*, ir_graph_frontend_causality','v01/ac-matrix.json: AC-03/04'],
        5:['matrix/independent-direct-report.json','matrix/independent-ir-report.json','t4/assertions.json'],
        6:['matrix/ctest.log: ir_operator_placements, ir_graph_frontend','t4/assertions.json','v01/ac-matrix.json: AC-07'],
        7:['matrix/gradient.json','ir-asan.log','t5/assertions.json'],
        8:['matrix/logging-*-report.json','matrix/ctest.log: ir_observation_comparator'],
        9:['matrix/manifest.json','matrix-validation.json','matrix/efficiency.json'],
        10:['compatibility.json','default-*/manifest.json','direct-*/manifest.json','fresh-full-*/manifest.json'],
        11:['v01/ac-matrix.json','v01/parameters.json: native_oracle','v01/asan-ctest.log','ir-asan.log','t3/manifest.json','t4/manifest.json','t5/manifest.json'],
        12:['manifest.json','assertions.json','matrix-validation.json','documentation.json'],
    }
    return dict(scope='full V0.3 delivery',status='passed',applicable_passed=12,
        inherited_v02_not_applicable={'AC-02-10':'Deep10M cancelled; not a pass. V0.1 AC-10 remains required.'},
        checks={f'AC-03-{i:02}':dict(status='passed',evidence=e) for i,e in evidence.items()})


def validate_matrix(root, build, prepared):
    m=require_suite(root)
    if m.get('schema')!='ndpsim-ir-matrix-v036-v1' or m.get('completed_matrix_points')!=52 or m.get('expected_matrix_points')!=52:
        raise ValueError('incomplete matrix coverage')
    assertions=load(root/'assertions.json')
    if not assertions or any(v is not True for v in assertions.values()) or not m.get('commands') or any(c.get('passed') is not True or c.get('exit_code')!=0 for c in m['commands']):
        raise ValueError('matrix assertions/commands failed')
    # Documentation closeouts may change; every execution/validation/build/test
    # file must match, including the matrix driver and this delivery driver.
    before=load(root/'source-start.json')['source_sha256'];now=inventory()['source_sha256']
    relevant=lambda n:not (n=='AGENTS.md' or n.endswith('.md'))
    if {n:h for n,h in before.items() if relevant(n)}!={n:h for n,h in now.items() if relevant(n)}:
        raise ValueError('matrix execution/validation source identity mismatch')
    rows=load(root/'scan.json');matrix_rows=[r for r in rows if r['matrix']]
    expected={(e,p['name']) for p in points() for e in ('direct','ir')}
    if len(matrix_rows)!=52 or {(r['engine'],r['point']['name']) for r in matrix_rows}!=expected:
        raise ValueError('duplicate/missing matrix point')
    if load(root/'schedule.json')!=schedule():raise ValueError('matrix schedule changed')
    for row in rows:
        target=root/row['name'];c=load(target/'config.json')
        if row['point'].get('group'):
            verify_cached_run(target,c,prepared,row['point']['group'],build)
        else:
            run=load(target/'manifest.json')
            if run['element_sha256']!=sha(build/'libndpsim.so') or run['execution_engine']!=row['engine'] or c['execution_engine']!=row['engine']:
                raise ValueError('anchor/gradient execution identity mismatch')
            verify_hashes(target,run['artifacts_sha256'])
            for name,digest in run['source_sha256'].items():
                if sha(ROOT/name)!=digest:raise ValueError('anchor/gradient source mismatch: '+name)
    budget=load(root/'budget-frozen.json')
    samples=[r for r in rows if r['point'].get('group')=='evaluation' and r['point']['L']==128]
    eff=efficiency(samples,budget);grad=gradient_report(load(root/'gradient-samples.json'),budget)
    if eff!=load(root/'efficiency.json') or grad!=load(root/'gradient.json') or not all(r['passed'] for r in [*eff.values(),*grad.values()]):
        raise ValueError('matrix efficiency/gradient gate failed')
    return dict(passed=True,manifest=str(root/'manifest.json'),sha256=sha(root/'manifest.json'),matrix_points=52,
        validated_runs=len(rows),policy='all execution and validation sources, artifacts, element, inputs and dependencies match; markdown closeouts excluded')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--all',action='store_true',required=True)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--prepared',type=Path,default=ROOT/'results/v0.2-t6/preparation/prepared')
    p.add_argument('--small-prepared',type=Path,default=ROOT/'results/v0.2-t2/real-final')
    p.add_argument('--source-dir',type=Path,default=ROOT/'.cache/v0.2-m0/data/sift')
    p.add_argument('--ram-evidence',type=Path)
    p.add_argument('--matrix-evidence',type=Path,help='Optional strictly identity-matched complete V03-6 evidence')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();lock=load(ROOT/'deps.lock.json')
    if sys.flags.optimize or Path(sys.executable).resolve()!=Path(lock['tools']['python']['path']).resolve():p.error('use locked Python without -O')
    out=a.output.resolve();build=a.build.resolve();prepared=a.prepared.resolve()
    if out.exists():p.error('output must not exist')
    out.mkdir(parents=True);commands=[];checks={};compat={};error=None;ac=None
    before=inventory();write_json(out/'source-start.json',before)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    def command(name,argv,timeout=7200,env=None):
        row=measure(list(map(str,argv)),out/(name+'.json'),timeout,env=env)
        commands.append(dict(name=name,**row));print(f'{name}: exit={row["exit_code"]} wall={row["wall_seconds"]:.2f}s',flush=True)
        if not row['passed']:raise RuntimeError('delivery command failed: '+name)
    try:
        with BaselineDiskMonitor(out,[a.source_dir.resolve(),prepared,ROOT/'results/v0.3']):
            deps={}
            for name,rel in [('direct','direct-baseline-final'),('schema','schema-v031/final'),('lifecycle','dynamic-v032/final3'),('mapping','mapping-v033/final4'),('frontend','frontend-v034/final2'),('observation','observation-v035/final2'),('matrix','matrix-v036/final5')]:
                path=ROOT/'results/v0.3'/rel;m=require_suite(path)
                deps[name]=dict(manifest=str(path/'manifest.json'),sha256=sha(path/'manifest.json'),policy='dependency evidence; current runs below decide acceptance')
            write_json(out/'dependencies.json',deps);checks['dependencies']=True
            command('delivery-tests',[sys.executable,ROOT/'tests/test_v03_delivery.py'])
            command('v02-delivery-tests',[sys.executable,ROOT/'tests/test_v02_delivery.py'])
            # The matrix driver performs root configure/build, all CTest, IR ASan,
            # locked raw/prepared input preflight and new 52+4+4+18 production runs.
            matrix=a.matrix_evidence.resolve() if a.matrix_evidence else out/'matrix'
            if not a.matrix_evidence:
                argv=[sys.executable,ROOT/'tools/verify_ir_matrix.py','--build',build,'--prepared',prepared,'--source-dir',a.source_dir,'--output',matrix]
                if a.ram_evidence:argv+=['--ram-evidence',a.ram_evidence]
                command('matrix',argv,14400)
            else:
                # Preserve a stable relative evidence path without copying gigabytes.
                (out/'matrix').symlink_to(matrix,target_is_directory=True)
                cmake=lock['tools']['cmake']['path']
                command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON','-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
                command('build',[cmake,'--build',build,'-j2'])
            write_json(out/'matrix-validation.json',validate_matrix(matrix,build,prepared))
            checks.update(matrix_identity=True,matrix_gates=True)
            command('v01',[sys.executable,ROOT/'tools/verify_acceptance.py','--build',build,'--output',out/'v01'])
            for stage in ('t3','t4','t5'):
                command(stage,[sys.executable,ROOT/f'tools/verify_{stage}.py','--build',build,'--prepared',a.small_prepared,'--output',out/stage])
            asan=build.with_name(build.name+'-asan');cmake=lock['tools']['cmake']['path']
            command('ir-asan-build',[cmake,'--build',asan,'--target','ir_executor_probe','ir_schema_probe','ir_frontend_probe','ir_frontend_causal_probe','ir_mapping_probe','-j2'])
            command('ir-asan',['ctest','--test-dir',asan,'--output-on-failure','-R','^(ir_dynamic_lifecycle|ir_schema_memory|ir_graph_frontend|ir_graph_frontend_causality|ir_operator_placements)$'],env={'ASAN_OPTIONS':'halt_on_error=1'})
            checks['ir_asan']=True
            for mode in ('host','controller'):
                frozen=ROOT/'results/v0.3/direct-baseline-final'/('small-'+mode)
                old=load(frozen/'config.json');old.pop('execution_engine',None)
                for label,engine in [('default',None),('direct','direct')]:
                    c=dict(old)
                    if engine:c['execution_engine']=engine
                    cfg=out/(label+'-'+mode+'-config.json');write_json(cfg,c)
                    target=out/(label+'-'+mode)
                    command(label+'-'+mode,[sys.executable,ROOT/'tools/run_config.py','--build',build,'--config',cfg,'--data',frozen/'data','--output',target])
                    effective=engine or 'ir';m=load(target/'manifest.json')
                    compat[mode+'-'+('default_ir' if engine is None else 'direct')]=m['execution_engine']==effective and load(target/'config.json')['execution_engine']==effective and load(target/'observation.json')['execution_engine']==effective and (target/'ir-manifest.json').exists()==(effective=='ir')
                direct=out/('direct-'+mode)
                compat[mode+'-frozen']=load(direct/'results.json')==load(frozen/'results.json') and statistics(direct)==statistics(frozen) and sha(direct/'events.jsonl')==sha(frozen/'events.jsonl') and sha(direct/'service.csv')==sha(frozen/'service.csv')
                report=out/('compatibility-'+mode+'-report.json')
                command('compatibility-'+mode,[sys.executable,ROOT/'tools/compare_engines.py',direct,out/('default-'+mode),'--output',report])
                compat[mode+'-equivalence']=load(report)['passed']
                # New full-base evaluation proves the omitted-engine daily path,
                # independently from explicit IR matrix entries.
                reference=matrix/f'ir-evaluation-l128-e256-c1-{mode}'
                c=load(reference/'config.json');c.pop('execution_engine');cfg=out/('fresh-full-'+mode+'-config.json');write_json(cfg,c)
                target=out/('fresh-full-'+mode)
                command('fresh-full-'+mode,[sys.executable,ROOT/'tools/run_config.py','--build',build,'--config',cfg,'--data',prepared/'evaluation','--output',target])
                report=out/('fresh-full-'+mode+'-report.json')
                command('fresh-full-compare-'+mode,[sys.executable,ROOT/'tools/compare_engines.py',reference,target,'--output',report])
                compat[mode+'-fresh_full']=load(report)['passed'] and load(target/'manifest.json')['execution_engine']=='ir'
            write_json(out/'compatibility.json',compat)
            from verify_delivery_docs import verify
            write_json(out/'documentation.json',verify())
        checks['disk']=load(out/'disk-measurement.json')['passed']
        after=inventory();write_json(out/'source-end.json',after)
        checks['source_unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies_unchanged']=before['mqsim']==after['mqsim'] and before['sst_head']==after['sst_head'] and not after['sst_status']
        command('diff-check',['git','diff','--check'])
        ac=acceptance_matrix(load(out/'v01/ac-matrix.json'),{s:load(out/s/'manifest.json') for s in ('t3','t4','t5')},load(matrix/'manifest.json'),compat,checks)
    except Exception as exc:error=str(exc)
    write_json(out/'assertions.json',checks)
    write_json(out/'ac-matrix.json',ac or dict(scope='full V0.3 delivery',status='failed_or_incomplete',checks={f'AC-03-{i:02}':dict(status='failed_or_incomplete') for i in range(1,13)}))
    write_json(out/'manifest.json',dict(schema='ndpsim-v03-delivery-v1',task='V03-7',passed=error is None,error=error,
        scope='full V0.3 delivery',commands=commands,matrix_reused=bool(a.matrix_evidence),
        artifacts_sha256={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file() and f!=out/'manifest.json'},
        limitations='configured_unvalidated; fixed query subsets; official ties unresolved; targeted ASan, not full SST/LSan; whole-process RSS, sampled disk; cached SST, not clean rebuild'))
    print('V03-7 '+('PASS; no next task started' if error is None else 'FAIL: '+error),flush=True)
    return int(error is not None)

if __name__=='__main__':sys.exit(main())
