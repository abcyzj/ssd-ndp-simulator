"""V04-0: input/reference verification, preregistration and fresh default smoke.

No native runner, CPU calibration, full-scale collection or next task is run.
Use locked Python; output must be a new directory, including after failure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
from ann_data import write_json
from freeze_direct_baseline import BaselineDiskMonitor, verify_hashes, verify_evidence as direct_evidence
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import (GIB, LOCK, SELECTION, MATRIX, BUDGET, load, query_groups,
                       validate_plan, validate_prepared, statistics)

SCHEMA = 'ndpsim-v04-start-v1'
REQUIRED = {'inputs', 'references', 'matrix', 'environment', 'budget', 'build',
            'host-default', 'controller-default', 'host-frozen', 'controller-frozen',
            'host-equivalence', 'controller-equivalence', 'independent-direct',
            'independent-default', 'source-unchanged', 'dependencies-unchanged', 'disk'}


def matrix_plan():
    rows = []
    def row(id_, path, engine, placement, cost, L=128, E=256, workers=None,
            slots=1, concurrency=1, rate=None, repeats=1, native=0):
        rows.append(dict(id=id_, data_provider=path, engine=engine, placement=placement,
            cpu_cost=cost, L=L, E=E, k=10, B=1, chunk=16, workers=workers,
            slots=slots, concurrency=concurrency, controller_flops_per_ns=rate,
            simulated_repeats_min=repeats, native_repeats_min=native,
            base_count=1000000, dimensions=128, query_group='evaluation128'))
    for i,(L,E) in enumerate(((32,64),(64,128),(128,256),(256,512)),1):
        row(f'A{i:02}', 'host_memory','ir','host','frozen_profile',L,E,1,repeats=2,native=3)
    for i,w in enumerate((2,4),1):
        row(f'D{i:02}','host_memory','ir','host','frozen_profile',workers=w,slots=w,concurrency=32,repeats=2,native=3)
    for i,(engine,placement) in enumerate((('direct','host'),('direct','controller'),('ir','host'),('ir','controller')),1):
        row(f'R{i:02}','ssd',engine,placement,'legacy',rate=16 if placement=='controller' else None)
    row('O01','ssd','ir','host','ssd_applicable_profile')
    for i,rate in enumerate((8,16,32),2):
        row(f'O{i:02}','ssd','ir','controller','ssd_applicable_profile',rate=rate)
    return dict(schema='v04-core14/1', status='frozen_plan_not_execution', configurations=rows,
        simulated_runs_min=20, native_performance_runs_min=18,
        companion_schedule=[
            dict(stage='training',queries='tuning32',points=['32/64','64/128','128/256','256/512'],mode='sampled_profile',sample_rate='freeze in V04-2a/2c before collection'),
            dict(stage='timing_quality',queries='tuning32',order=['uninstrumented_A','benchmark_B','uninstrumented_A'],groups_min=3,profile_and_trace_overhead='reported separately'),
            dict(stage='model',order=['training_only_fit','profile_freeze','evaluation128_Level_A','A01-A04_Level_B_C','D01-D02_diagnostic','O01-O04_conditional_prediction']),
            dict(stage='detail8',configurations=['A03','D01','D02','R01','R02','R03','R04','O01','O02','O03','O04'],purpose='semantic/resource linkage; separate from performance; same-identity traces reusable'),
            dict(stage='direct_other_LE',scope='four-point fixtures/real subsets and independent oracle; no extra full-base cross product'),
            dict(stage='inherited',scope='root build/CTest; full V0.1 AC/oracle/slow/ASan; V0.2 T3/T4/T5; IR causal/lifecycle/log/resource checks'),
            dict(stage='deep',scope='52-point V0.3 + alternating efficiency + Q gradient only if triggered',decision_required='V04-5b regression-impact.json')],
        controller_scan=dict(multipliers=[.5,1,2],baseline_flops_per_ns=16,explicit_rates=[8,16,32],other_controller_parameters='unchanged locked MATRIX experiment'),
        constraints=['Trace never drives simulation','all repetitions retain original query split','new profile/provider explicitly enabled','A never denominator of B/C benefit'],
        specification_sha256=sha(ROOT/'docs/validation-matrix-v0.4.md'))


def validate_matrix(plan):
    expected = matrix_plan()
    if plan != expected:
        raise ValueError('matrix parameters, coverage or companion schedule changed')
    return True


def verify_evidence(out):
    m = load(out/'manifest.json')
    if m.get('schema') != SCHEMA or m.get('task') != 'V04-0' or m.get('passed') is not True:
        raise ValueError('failed/incomplete V04-0 evidence')
    checks = load(out/'assertions.json')
    if set(checks) != REQUIRED or any(v is not True for v in checks.values()):
        raise ValueError('missing/failed V04-0 assertions')
    commands=m.get('commands',[])
    expected={'configure','build','ctest','evidence-tests','diff-check',
              'direct-host','direct-controller','default-host','default-controller',
              'equivalence-host','equivalence-controller','independent-direct','independent-default'}
    if {c['name'] for c in commands} != expected or len(commands)!=len(expected) or any(c.get('exit_code')!=0 or c.get('passed') is not True for c in commands):
        raise ValueError('missing/failed command receipts')
    artifacts=m['artifacts_sha256']
    required_files={'source-start.json','source-end.json','workspace.patch','dependencies.json',
                    'inputs.json','environment.json','budget-frozen.json','matrix-plan.json','build-identity.json','assertions.json'}
    if not required_files <= set(artifacts): raise ValueError('required artifacts omitted')
    verify_hashes(out, artifacts)
    validate_matrix(load(out/'matrix-plan.json'))
    return m


def git(*args):
    return subprocess.check_output(['git',*map(str,args)],cwd=ROOT)


def dependency_snapshot(out):
    lock=load(ROOT/'deps.lock.json'); result={}
    for name in ('MQSim','sst-core'):
        root=ROOT/'third_party'/name
        names=git('-C',root,'ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0')
        files={n:sha(root/n) for n in sorted(set(filter(None,names))) if (root/n).is_file()}
        base=lock['dependencies'][name]['commit']
        diff=git('-C',root,'diff','--binary',base,'--')
        (out/(name+'-relative-native.patch')).write_bytes(diff)
        result[name]=dict(commit=git('-C',root,'rev-parse','HEAD').decode().strip(),
            status=git('-C',root,'status','--short').decode(),locked_native_commit=base,
            relative_native_diff_sha256=hashlib.sha256(diff).hexdigest(),files_sha256=files)
    if result['sst-core']['status']:raise ValueError('SST Core must remain unmodified')
    return result


def input_snapshot(prepared, raw, builder_evidence):
    validate_plan(); validate_prepared(prepared)
    saved=load(prepared/'prepared.json'); verify_hashes(prepared,saved['files_sha256'])
    for key,path in [('query_selection_sha256',SELECTION),('dataset_lock_sha256',LOCK)]:
        if saved[key]!=sha(path):raise ValueError('prepared lock mismatch: '+key)
    raw_files={}
    for role,info in load(LOCK)['datasets']['sift1m']['files'].items():
        path=raw/('sift_'+role+('.ivecs' if role=='groundtruth' else '.fvecs'))
        if path.stat().st_size!=info['bytes'] or sha(path)!=info['sha256']:raise ValueError('raw input mismatch: '+role)
        raw_files[role]=dict(path=str(path),bytes=info['bytes'],sha256=info['sha256'])
    receipt=load(prepared/'index/index.json')['builder']['receipt']
    if load(builder_evidence/'index-builder-receipt.json') != receipt or sha(builder_evidence/'index-builder-receipt.json') != saved['receipt_sha256']:
        raise ValueError('builder preparation receipt mismatch')
    if sha(builder_evidence/'manifest.json') != receipt['build_manifest_sha256']:
        raise ValueError('builder build manifest mismatch')
    builder_manifest=load(builder_evidence/'manifest.json')
    if builder_manifest.get('passed') is not True or not builder_manifest.get('commands'):
        raise ValueError('builder build failed/incomplete')
    for i,command in enumerate(builder_manifest['commands']):
        if command.get('passed') is not True or command.get('exit_code') != 0 or sha(builder_evidence/f'command-{i}.log') != command['log_sha256']:
            raise ValueError('builder command/log mismatch')
    paths=[(ROOT/'configs/benchmarks/m0-builder.lock.json',receipt['lock_sha256']),
           (ROOT/'tools/offline/index_builder.cc',receipt['adapter_sha256']),
           (Path(receipt['executable']),receipt['executable_sha256']),
           (Path(receipt['executable']).parent.parent/'openmp/lib/libomp.dylib',receipt['openmp_sha256'])]
    for path,digest in paths:
        if sha(path)!=digest:raise ValueError('builder mismatch: '+str(path))
    groups=query_groups()
    for group,ids in groups.items():
        actual=list(struct.unpack('<'+'I'*len(ids),(prepared/group/'query.ids.u32').read_bytes()))
        if actual!=ids:raise ValueError('query ID/order mismatch: '+group)
    return dict(raw=raw_files,prepared=str(prepared),preparation=saved,builder=receipt,
        builder_build_evidence=dict(path=str(builder_evidence),files_sha256={f.name:sha(f) for f in sorted(builder_evidence.iterdir()) if f.is_file()}),
        query_groups=groups,query_split='original IDs; tuning32 disjoint evaluation128; detail8=evaluation[:8]; concurrency=evaluation[:32]',
        validation='all prepared payload hashes and full dataset/index/truth validation, raw full hashes, builder executable/runtime/adapter/lock',
        locks={str(p.relative_to(ROOT)):sha(p) for p in (LOCK,SELECTION,BUDGET,MATRIX)})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--prepared',type=Path,default=ROOT/'results/v0.2-t6/preparation/prepared')
    p.add_argument('--source-dir',type=Path,default=ROOT/'.cache/v0.2-m0/data/sift')
    p.add_argument('--builder-evidence',type=Path,default=ROOT/'results/v0.2-t2/builder')
    p.add_argument('--hardware-evidence',type=Path,required=True,help='fresh sysctl stdout: hw.memsize/model, machdep.cpu.brand_string, hw.ncpu/physicalcpu/logicalcpu, kern.osrelease/osversion')
    a=p.parse_args();lock=load(ROOT/'deps.lock.json')
    if sys.flags.optimize or Path(sys.executable).resolve()!=Path(lock['tools']['python']['path']).resolve():p.error('use locked Python without -O')
    out=a.output.resolve();build=a.build.resolve();prepared=a.prepared.resolve();raw=a.source_dir.resolve()
    if out.exists():p.error('output must not exist')
    out.mkdir(parents=True);commands=[];checks={};error=None
    before=inventory();write_json(out/'source-start.json',before)
    (out/'workspace.patch').write_bytes(git('diff','--binary','HEAD'))
    for name in git('ls-files','--others','--exclude-standard','-z').decode().split('\0'):
        if name and (ROOT/name).is_file():
            target=out/'untracked-source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    def command(name,argv,timeout=3600):
        r=measure(list(map(str,argv)),out/(name+'.json'),timeout)
        commands.append(dict(name=name,**r));print(f'{name}: exit={r["exit_code"]} wall={r["wall_seconds"]:.2f}s',flush=True)
        if not r['passed']:raise ValueError('command failed: '+name)
    try:
        with BaselineDiskMonitor(out,[raw,prepared,ROOT/'results/v0.4']):
            deps=dependency_snapshot(out)
            refs={}
            for label,path in [('direct',ROOT/'results/v0.3/direct-baseline-final'),('delivery',ROOT/'results/v0.3/delivery-v037/final1')]:
                anchor={'direct':'d7f78b1076bc003043866174442d9cf2ef91452ce994a93c68c322033162dc11',
                        'delivery':'94f580f1fc400356a33dbb24e9a58ec93e31963e575ef785d4ee347ee0b923fa'}
                if sha(path/'manifest.json') != anchor[label]:raise ValueError('published historical reference identity mismatch: '+label)
                m=direct_evidence(path) if label=='direct' else load(path/'manifest.json')
                if m.get('passed') is not True:raise ValueError('historical dependency failed')
                verify_hashes(path,m['artifacts_sha256'])
                if label=='direct':
                    verify_hashes(path,load(path/'build-identity.json')['files_sha256'])
                refs[label]=dict(manifest=str(path/'manifest.json'),sha256=sha(path/'manifest.json'),artifacts_verified=len(m['artifacts_sha256']),purpose='historical dependency verified completely, not new V0.4 acceptance',
                    supplemental_files_sha256={str(f.relative_to(path)):sha(f) for f in sorted(path.rglob('*')) if f.is_file() and str(f.relative_to(path)) not in m['artifacts_sha256']})
                print('verified historical '+label,flush=True)
            write_json(out/'dependencies.json',dict(sources=deps,references=refs));checks['references']=True
            write_json(out/'inputs.json',input_snapshot(prepared,raw,a.builder_evidence.resolve()));checks['inputs']=True
            print('verified complete inputs and builder',flush=True)
            plan=matrix_plan();validate_matrix(plan);write_json(out/'matrix-plan.json',plan);checks['matrix']=True
            hardware=a.hardware_evidence.read_text();(out/'hardware.txt').write_text(hardware)
            hw=dict(line.split(': ',1) for line in hardware.strip().splitlines())
            required_hw={'hw.memsize','hw.model','machdep.cpu.brand_string','hw.ncpu','hw.physicalcpu','hw.logicalcpu','kern.osrelease','kern.osversion'}
            if not required_hw<=set(hw) or int(hw['hw.memsize'])<4*GIB:raise ValueError('missing/insufficient hardware evidence')
            observations={}
            for name,argv in [('vm_stat',['/usr/bin/vm_stat']),('pressure',['/usr/bin/memory_pressure','-Q']),('os',['/usr/bin/sw_vers']),('cpu_frequency',['/usr/sbin/sysctl','hw.cpufrequency'])]:
                r=subprocess.run(argv,capture_output=True,text=True)
                observations[name]=dict(command=argv,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr)
            write_json(out/'environment.json',dict(platform=platform.platform(),hardware=hw,observations=observations,
                hardware_evidence=str(a.hardware_evidence.resolve()),hardware_evidence_sha256=sha(a.hardware_evidence),
                hardware_capture='fresh sysctl outside restricted process; direct restricted query failure in source-start.json',
                frequency='Unsupported / uncontrolled',affinity='Unsupported / unbound',residency='future native load/touch/warmup outside window; record per-run faults and pressure; anomalous paging invalidates sample',
                parameter_sources=dict(cpu='not yet calibrated',SSD='configured_unvalidated',PCIe='configured_unvalidated',Controller='configured_unvalidated')))
            checks['environment']=True
            cmake=lock['tools']['cmake']['path']
            command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON','-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
            command('build',[cmake,'--build',build,'-j2'])
            command('ctest',['ctest','--test-dir',build,'--output-on-failure'])
            command('evidence-tests',[sys.executable,ROOT/'tests/test_v04_start.py'])
            paths=load(build/'sst-paths.json');cache=Path(paths['prefix']).parent
            files=[build/'libndpsim.so',build/'CMakeCache.txt',build/'sst-paths.json',Path(paths['sst']),cache/'receipt.json']
            files+=sorted((build/'CMakeFiles').glob('*.dir/flags.make'))
            files+=sorted((build/'CMakeFiles').glob('*/CMake*Compiler.cmake'))
            compiler=subprocess.run([lock['tools']['cxx']['path'],'--version'],capture_output=True,text=True,check=True).stdout
            write_json(out/'build-identity.json',dict(files_sha256={str(f):sha(f) for f in files},sst_cache=str(cache),compiler_version=compiler,method='actual binaries, CMake cache, all target flags and SST receipt; cached build, not clean rebuild'))
            checks['build']=True
            small={}
            for mode in ('host','controller'):
                frozen=ROOT/'results/v0.3/direct-baseline-final'/('small-'+mode)
                for label,engine in [('direct','direct'),('default',None)]:
                    c=load(frozen/'config.json');c.pop('execution_engine',None)
                    if engine:c['execution_engine']=engine
                    cfg=out/(label+'-'+mode+'-config.json');write_json(cfg,c)
                    target=out/(label+'-'+mode)
                    command(label+'-'+mode,[sys.executable,ROOT/'tools/run_config.py','--build',build,'--config',cfg,'--data',frozen/'data','--output',target])
                    verify_hashes(target,load(target/'manifest.json')['artifacts_sha256'])
                    if label=='default':
                        checks[mode+'-default']=all(load(target/f)['execution_engine']=='ir' for f in ('manifest.json','config.json','observation.json')) and (target/'ir-manifest.json').is_file()
                    small[label+'-'+mode]=load(target/'sst-measurement.json')
                direct=out/('direct-'+mode)
                checks[mode+'-frozen']=load(direct/'results.json')==load(frozen/'results.json') and statistics(direct)==statistics(frozen) and all(sha(direct/f)==sha(frozen/f) for f in ('events.jsonl','service.csv'))
                report=out/('equivalence-'+mode+'-report.json')
                command('equivalence-'+mode,[sys.executable,ROOT/'tools/compare_engines.py',direct,out/('default-'+mode),'--output',report])
                checks[mode+'-equivalence']=load(report)['passed']
            for label in ('direct','default'):
                report=out/('independent-'+label+'-report.json')
                command('independent-'+label,[sys.executable,ROOT/'tools/compare_placements.py',out/(label+'-host'),out/(label+'-controller'),'--output',report])
                checks['independent-'+label]=load(report)['status']=='passed'
            ram=int(hw['hw.memsize']);free=shutil.disk_usage(out).free
            budget=dict(schema='ndpsim-v04-budget-v1',frozen_before_full_scale_collection=True,
                physical_ram_bytes=ram,volume_free_bytes=free,small_measurements=small,
                query_rss_bytes_max=min(4*GIB,int(.6*ram)),postprocess_rss_bytes_max=512*1024**2,
                initialization_seconds_max=3600,query_seconds_max=7200,postprocessing_seconds_max=600,
                aggregate_disk_bytes_max=25*GIB,volume_free_bytes_min=20*GIB,output_bytes_per_run_max=2*GIB,
                disk_scope='raw SIFT + shared prepared + all results/v0.4 including failures; excludes V0.3 historical evidence/build/cache',
                trace_policy='detail8 only; 2GiB per-run combined required/trace; measure pilot size then freeze sampling in V04-2a/2c; no silent truncation',
                future_preflight='mandatory fresh input/hash/RAM/pressure/fault/disk/output/timeout check before each full-scale collection; this freeze is not future permission based on stale resources',
                current_scope='four fresh synthetic smoke runs only; no full-scale collection',
                limitations='RSS checked after completion, disk 1s logical-size sampling; no hard isolation/private or simultaneous process-tree RSS; CPU frequency/affinity uncontrolled')
            if free<20*GIB or any(m['peak_rss_bytes']>budget['query_rss_bytes_max'] for m in small.values()):raise ValueError('resource preflight failed')
            write_json(out/'budget-frozen.json',budget);checks['budget']=True
        checks['disk']=load(out/'disk-measurement.json')['passed']
        after=inventory();write_json(out/'source-end.json',after)
        checks['source-unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies-unchanged']=dependency_snapshot(out)==deps
        command('diff-check',['git','diff','--check'])
        if set(checks)!=REQUIRED or not all(checks.values()):raise ValueError('V04-0 assertions failed')
    except Exception as exc:error=str(exc)
    write_json(out/'assertions.json',checks)
    write_json(out/'manifest.json',dict(schema=SCHEMA,task='V04-0',passed=error is None,error=error,
        scope='start freeze; 4 fresh synthetic smoke runs, complete input/reference verification; no full-scale acceptance or CPU accuracy',commands=commands,
        artifacts_sha256={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file() and f!=out/'manifest.json'},
        limitations='historical dependency verification is not rerun acceptance; 14 configurations are a plan; full-scale preflight must be repeated; SSD/PCIe/Controller configured_unvalidated'))
    if error is None:verify_evidence(out)
    print('V04-0 '+('PASS; no next task started' if error is None else 'FAIL: '+error),flush=True)
    return int(error is not None)


if __name__=='__main__':sys.exit(main())
