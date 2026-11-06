"""Unified V0.4 delivery. Fresh core12 execution and explicit immutable inputs.

No deep-regression execution is available. A triggered deep requirement or any
missing precision, semantic, identity, repetition or inherited gate fails closed.
"""
import argparse
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import sys

from ann_data import write_json
from collect_v04_training import aba, signature, resources
from evaluate_v04_level_a import evaluate as evaluate_level_a, PROFILE_SHA, START_SHA
from freeze_direct_baseline import verify_hashes
from freeze_v04_start import dependency_snapshot, input_snapshot
from native_observation import build_identity
from validate_v04 import command_ok
from v04_contract import require, validate_package
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import DiskMonitor, GIB, MATRIX

SCHEMA = 'ndpsim-v04-delivery/1'
IDS = ('A01','A02','D01','D02','R01','R02','R03','R04','O01','O02','O03','O04')
MINIMUM = {name: (2 if name[0] in 'AD' else 1) for name in IDS}
REQUIRED_AC = {f'AC-04-{number:02}' for number in range(1,13)}
NATIVE_SOURCES = ('workloads/graph-search/src/search/native.cc','workloads/graph-search/src/search/native_observation.h','workloads/graph-search/src/search/core.cc',
                  'workloads/graph-search/src/search/core.h','workloads/graph-search/src/operators/operators.cc','workloads/graph-search/src/operators/operators.h',
                  'src/content/sha256.cc','workloads/graph-search/src/content/value.h',
                  'workloads/graph-search/src/content/value.cc')


def load(path):
    return json.loads(Path(path).read_text())


def core_plan():
    rows = []
    for name, L, E, workers in [('A01',32,64,1),('A02',64,128,1),('D01',64,128,2),('D02',64,128,4)]:
        rows.append(dict(id=name, engine='replay', provider='host_memory', placement='host',
                         L=L, E=E, workers=workers, slots=workers,
                         concurrency=1 if workers == 1 else 32, repeats=2, rate=None))
    for name, engine, placement in [('R01','direct','host'),('R02','direct','controller'),
                                    ('R03','ir','host'),('R04','ir','controller')]:
        rows.append(dict(id=name, engine=engine, provider='ssd', placement=placement,
                         L=64, E=128, workers=None, slots=1, concurrency=1, repeats=1,
                         rate=16 if placement == 'controller' else None))
    for name, placement, rate in [('O01','host',None),('O02','controller',8),
                                 ('O03','controller',16),('O04','controller',32)]:
        rows.append(dict(id=name, engine='replay', provider='ssd', placement=placement,
                         L=64, E=128, workers=1, slots=1, concurrency=1, repeats=1, rate=rate))
    return dict(schema='v04-core12/1', configurations=rows, core_simulations_min=16,
                native_repetitions=3, evaluation_queries=128, dimensions=128, base_count=1000000,
                search=dict(k=10,B=1,chunk=16),
                companions=['three tuning32 A/B/A groups at 64/128',
                            'independent sampling and complete IR for both L/E and both splits',
                            'detail8, native-only isolation, current semantic/Work/oracle references',
                            'Level A per-call billing, full inherited AC/oracle/slow/ASan/T3/T4/T5'],
                deep_regression_execution='disabled_by_user; triggered requirement blocks release')


def check_coverage(plan, configurations):
    require(plan == core_plan(), 'core12 plan drift')
    require(len(configurations) == 12 and {r['id'] for r in configurations} == set(IDS),
            'missing, duplicate or extra core12 configuration')
    for expected in plan['configurations']:
        row = next(r for r in configurations if r['id'] == expected['id'])
        require(row['parameters'] == expected and row['status'] == 'passed', 'failed/mismatched configuration')
        runs = row['runs']
        require(len(runs) >= MINIMUM[row['id']] and len(set(r['directory'] for r in runs)) == len(runs),
                'missing or duplicate simulation repetition')
        wanted = {key:value for key,value in expected.items() if key not in ('id','repeats')}
        require(all(run.get('actual') == wanted and run.get('queries') == 128 for run in runs),
                'recorded/effective core12 parameters or query coverage differ')
        if row['id'][0] in 'AD':
            require(len(row.get('native', [])) >= 3 and
                    len({r['directory'] for r in row['native']}) == len(row['native']),
                    'missing or duplicate native repetition')
        if row['id'][0] in 'ADO':
            require(row.get('complete_queries') == 128 and row.get('package_verified') is True,
                    'incomplete static IR coverage')


def acceptance(plan, configurations, ac, impact):
    check_coverage(plan, configurations)
    require(set(ac) == REQUIRED_AC and all(r.get('status') == 'passed' and r.get('evidence')
                                           for r in ac.values()), 'missing or failed V0.4 AC')
    require(impact.get('deep_regression_required') is False and
            impact.get('deep_regression_executed') is False and
            len(impact.get('triggers', [])) == 3 and
            all(r.get('triggered') is False for r in impact['triggers']),
            'unresolved or triggered deep regression requirement')
    return dict(schema=SCHEMA, status='passed', scope='V0.4 core12 delivery',
                configurations=12, core_simulations=sum(len(r['runs']) for r in configurations),
                ac_passed=12, deep_regression='not_required_this_revision; not_run')


def verified_run(directory, build=None):
    directory = Path(directory)
    manifest = load(directory/'manifest.json')
    verify_hashes(directory, manifest['artifacts_sha256'])
    if build is not None:
        require(manifest['element_sha256'] == sha(build/'libndpsim.so'), 'simulation build identity mismatch')
        for name, expected in manifest['source_sha256'].items():
            require(sha(ROOT/name) == expected, 'simulation execution source changed: '+name)
        command_ok(load(directory/'sst-measurement.json'), directory)
    else:
        require(manifest.get('passed') is True and manifest.get('exit_code') == 0,
                'failed native evidence')
        command_ok(manifest['command'], directory)
    row = dict(directory=str(directory.resolve()), manifest_sha256=sha(directory/'manifest.json'))
    if build is not None:
        config = load(directory/'config.json')
        row.update(queries=len(load(directory/'results.json')['queries']),
                   actual=dict(engine=manifest['execution_engine'],provider=config.get('data_provider','ssd'),
                               placement=config['placement'],L=config['search']['width'],E=config['search']['budget'],
                               workers=config.get('native_workers'),slots=config['host']['slots'],
                               concurrency=config['max_inflight'],
                               rate=config['controller']['flops_per_ns'] if config['placement']=='controller' else None))
    return row


def summarize_level_a(report):
    require(report['scope_points'] == ['l32-e64','l64-e128'] and
            report['numeric_gate'] == report['table_gate'] == 'passed' and
            report['stage_sources']['merge_append'] == 'measured_table' and
            report.get('evaluation_disclosure'), 'Level A technical gates or disclosure missing')
    require(len(report['measured_merge_append']) == 4 and
            all(row['status'] == 'table_verified' and row['replay_billing'] == 'passed'
                for row in report['measured_merge_append'].values()), 'incomplete Level A call tables')
    report.update(status='passed', ac_04_04='passed', exit_code=0,
                  acceptance_rule='current PRD permits disclosed prior evaluation exposure; models remain tuning-only')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, default=ROOT/'build/delivery')
    p.add_argument('--prepared', type=Path, default=ROOT/'results/v0.2-t6/preparation/prepared')
    p.add_argument('--native-binary', type=Path, default=ROOT/'build/v042c-native/ndpsim_native')
    p.add_argument('--evidence-root', type=Path, default=ROOT/'results/v0.4')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    lock = load(ROOT/'deps.lock.json')
    require(not sys.flags.optimize and Path(sys.executable).resolve() == Path(lock['tools']['python']['path']).resolve(),
            'use locked Python without -O')
    out, build, prepared, evidence, binary = (getattr(args, key).resolve()
                                             for key in ('output','build','prepared','evidence_root','native_binary'))
    out.mkdir(parents=True)
    commands, configurations, ac, error, final = [], [], {}, None, None
    plan = core_plan()
    write_json(out/'plan.json', plan)
    start = inventory()
    write_json(out/'source-start.json', start)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    cmake = lock['tools']['cmake']['path']
    inputs = evidence/'level-a-v044b/v4-collection-host'
    profile_root = evidence/'level-a-v044b/v4-report'
    fit = evidence/'cpu-fit-v043b/v3-only/final/fit-report.json'
    frozen_profile = ROOT/'configs/profiles/v04-merge-split.json'
    start_manifest = evidence/'start-v040/final1/manifest.json'

    def command(name, argv, timeout=3600, rss=None, env=None):
        row = measure(list(map(str,argv)), out/(name+'.json'), timeout, rss, env=env)
        commands.append(dict(name=name, **row))
        write_json(out/'commands.json', commands)
        print(f'{name}: exit={row["exit_code"]} wall={row["wall_seconds"]:.2f}s', flush=True)
        require(row['passed'], 'delivery command failed: '+name)
        return row

    def native(name, config, group, mode='benchmark', workers=1):
        target = out/name
        command(name, [sys.executable,'-B',ROOT/'tools/run_native.py','--config',config,
                '--data',prepared/group,'--binary',binary,'--output',target,'--mode',mode,
                '--split','tuning32' if group == 'tuning' else 'evaluation128',
                '--workers',workers,'--concurrency',1 if workers == 1 else 32,
                '--timeout',300,'--rss-limit',4*GIB,'--output-budget',GIB],420,4*GIB)
        verified_run(target)
        require(load(target/'manifest.json')['build'] == frozen_build, 'native benchmark capture build mismatch')
        return target

    def simulate(name, config, group):
        cfg = out/(name+'-config.json')
        write_json(cfg, config)
        target = out/name
        command(name, [sys.executable,'-B',ROOT/'tools/run_config.py','--config',cfg,'--build',build,
                       '--data',prepared/group,'--output',target],11400,4*GIB)
        verified_run(target, build)
        return target

    def passed(number, evidence_paths, detail):
        ac[f'AC-04-{number:02}'] = dict(status='passed', evidence=list(map(str,evidence_paths)), detail=detail)

    try:
        require(start['physical_ram_bytes'] and start['physical_ram_bytes'] >= 8*GIB,
                'physical RAM preflight unavailable')
        write_json(out/'resource-preflight.json', resources(out, 16*GIB))
        write_json(out/'budget-frozen.json', dict(task_new_output_bytes=16*GIB,
            minimum_free_bytes=20*GIB, native_and_sst_process_bytes=4*GIB,
            native_seconds=300, simulation_seconds=10800, deep_regression='disabled_by_user'))
        write_json(out/'dependencies.json', dependency_snapshot(out))
        write_json(out/'inputs.json', input_snapshot(prepared, ROOT/'.cache/v0.2-m0/data/sift',
                                                    ROOT/'results/v0.2-t2/builder'))
        require(sha(start_manifest) == START_SHA and sha(frozen_profile) == PROFILE_SHA, 'frozen model/start mismatch')
        capture64 = inputs/'evaluation-l64-e128-capture'
        frozen_build = load(capture64/'binding.json')['build']
        require(build_identity(binary,sha(binary)) == frozen_build, 'calibrated native executable identity changed')
        # Calibration inputs retain their own immutable build. Verify every
        # algorithm/operator/native input against the source snapshot that built it.
        trained = load(evidence/'training-v042c/run2/sources.json')['source_sha256']
        require(all(trained[name] == sha(ROOT/name) for name in NATIVE_SOURCES),
                'native numerical/search implementation changed; recapture/refit required')
        write_json(out/'calibration-provenance.json', dict(
            native_build=frozen_build, executable=str(binary), source_checks={name:sha(ROOT/name) for name in NATIVE_SOURCES},
            policy='frozen capture/measurement binary remains an explicit calibration input; fresh baselines use this exact binary'))
        with DiskMonitor(out,[out]):
            command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON','-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
            command('build',[cmake,'--build',build,'-j2'])
            command('ctest',['ctest','--test-dir',build,'--output-on-failure'])
            element = sha(build/'libndpsim.so')
            shutil.copyfile(build/'compile_commands.json',out/'compile_commands.json')
            shutil.copyfile(build/'sst-paths.json',out/'sst-paths.json')
            # A clean native-only build proves current root build isolation.
            isolated = out/'native-source'
            isolated.mkdir()
            shutil.copyfile(ROOT/'CMakeLists.txt',isolated/'CMakeLists.txt')
            shutil.copytree(ROOT/'src',isolated/'src')
            native_build = out/'native-only-build'
            command('native-only-configure',[cmake,'-S',isolated,'-B',native_build,'-DNDPSIM_NATIVE_ONLY=ON','-DBUILD_TESTING=OFF'])
            command('native-only-build',[cmake,'--build',native_build,'-j2'])
            linkage = subprocess.check_output(['otool','-L',native_build/'ndpsim_native'],text=True)
            require(not any(name in linkage for name in ('sst','mqsim','ndpsim.so')), 'native links simulator')
            (out/'native-linkage.txt').write_text(linkage)
            command('native-oracle',[sys.executable,'-B',ROOT/'tests/test_native_search.py',native_build/'ndpsim_native'])
            passed(1,[out/'native-only-configure.json',out/'native-only-build.json',out/'native-oracle.json'],
                    'isolated root native-only build with no third_party tree and independent native oracle')
            # Complete current-main timing perturbation, independent of sampling.
            groups, timing = [], []
            tuning_config = inputs/'tuning-l64-e128.json'
            expected = None
            for repeat in range(1,4):
                rates, group = [], []
                for suffix, mode in [('a-before','uninstrumented'),('b','benchmark'),('a-after','uninstrumented')]:
                    target = native(f'aba-{repeat}-{suffix}',tuning_config,'tuning',mode)
                    batch = load(target/'batch.json')
                    if expected is None:
                        expected = signature(batch)
                    require(signature(batch) == expected and batch['major_faults'] == 0,
                            'A/B/A semantics/Work or paging mismatch')
                    rates.append(batch['qps'])
                    group.append(dict(directory=str(target),mode=mode,qps=batch['qps'],manifest_sha256=sha(target/'manifest.json')))
                groups.append(rates)
                timing.append(group)
            timing_report = aba(groups)
            require(timing_report['passed'], 'current-main A/B/A perturbation exceeds 5%')
            write_json(out/'aba.json',dict(timing_report,point=[64,128],split='tuning32',ordered_samples=timing))
            passed(3,[out/'aba.json',inputs/'collection.json'], '64/128 tuning32 three-group A/B/A and frozen exclusive sampling contracts')
            # Independent native baseline measurements precede simulation workload.
            natives, plains, configs = {}, {}, {}
            for label,L,E,workers in [('A01',32,64,1),('A02',64,128,1),('D01',64,128,2),('D02',64,128,4)]:
                config = inputs/f'evaluation-l{L}-e{E}.json'
                configs[label] = config
                natives[label] = [native(f'{label}-native{repeat}',config,'evaluation',workers=workers) for repeat in range(1,4)]
                if label[0] == 'A':
                    plains[label] = native(label+'-uninstrumented',config,'evaluation','uninstrumented')
            # Existing regression runner provides fresh R01–R04 plus current
            # full/default/detail references and every inherited exit.
            command('regression',[sys.executable,'-B',ROOT/'tools/verify_v04_regression.py',
                    '--build',build,'--prepared',prepared,'--semantic-host','--output',out/'regression'],21600)
            regression = out/'regression'
            rm = load(regression/'manifest.json')
            verify_hashes(regression,rm['artifacts_sha256'])
            impact = load(regression/'regression-impact.json')
            require(rm['passed'] and not impact['deep_regression_required'] and not impact['deep_regression_executed'],
                    'inherited or deep-regression gate not closed')
            passed(11,[regression/'manifest.json',regression/'regression-impact.json'],
                    'fresh R01–R04/default/detail, full V0.1 AC/oracle/slow/ASan, T3/T4/T5 and targeted IR/static ASan')
            for label in ('R01','R02','R03','R04'):
                expected_point = next(r for r in plan['configurations'] if r['id'] == label)
                configurations.append(dict(id=label,parameters=expected_point,status='passed',
                    runs=[verified_run(regression/label,build)]))
            # A01 uses detail8 direct/IR as prescribed. A02/D references are
            # the current full R Host runs plus their detail8 companions.
            reference32 = {}
            for engine in ('direct','ir'):
                config = dict(load(MATRIX)['experiment'], **load(configs['A01']))
                config.update(execution_engine=engine,placement='host',semantic_log=True,event_log=True,
                              truth=str(prepared/'detail-truth'))
                reference32[engine] = simulate('A01-detail8-'+engine,config,'detail')
            replays = {}
            for label,L,E,workers in [('A01',32,64,1),('A02',64,128,1),('D01',64,128,2),('D02',64,128,4)]:
                capture = inputs/f'evaluation-l{L}-e{E}-capture'
                package = validate_package(capture/'package',capture/'binding.json')
                profile = profile_root/f'evaluation-l{L}-e{E}-profile.json'
                config = dict(load(MATRIX)['experiment'], **load(configs[label]))
                config.update(execution_engine='replay',data_provider='host_memory',placement='host',
                              order_policy='worker-fifo/1',native_workers=workers,max_inflight=1 if workers == 1 else 32,
                              event_log=True,ir_binding=str(capture/'binding.json'),
                              ir_input=dict(path=str(capture/'package'),sha256=package['package_sha256']),
                              cpu_profile=dict(path=str(profile),sha256=sha(profile)),simulation_timeout_seconds=600)
                config['host']['slots'] = workers
                replays[label] = [simulate(label+f'-replay{repeat}',config,'evaluation') for repeat in (1,2)]
                refs = reference32 if label == 'A01' else dict(direct=regression/'R01',ir=regression/'R03')
                argv = [sys.executable,'-B',ROOT/'tools/evaluate_v04_level_bc.py','--start-manifest',start_manifest,
                        '--capture',capture,'--profile',profile,'--samples',inputs/f'evaluation-l{L}-e{E}-r1',
                        '--fit-report',fit,'--merge-append-measurement',
                        *[inputs/f'evaluation-l{L}-e{E}-r{repeat}/measurement.json' for repeat in (1,2,3)],
                        '--native',*natives[label],'--uninstrumented',plains['A01' if label == 'A01' else 'A02'],
                        '--replays',*replays[label],'--direct',refs['direct'],'--ir',refs['ir'],
                        '--workers',workers,'--output',out/('validation-'+label)]
                if label != 'A01':
                    argv += ['--detail8-references',regression/'R01-detail8',regression/'R03-detail8']
                command('validate-'+label,argv,3600)
                report_path = out/('validation-'+label)/('workers.json' if workers > 1 else 'level-bc.json')
                report = load(report_path)
                require(report['status'] == 'passed', 'precision/semantic/worker report failed')
                expected_point = next(r for r in plan['configurations'] if r['id'] == label)
                configurations.append(dict(id=label,parameters=expected_point,status='passed',
                    runs=[verified_run(path,build) for path in replays[label]],
                    native=[verified_run(path) for path in natives[label]],complete_queries=package['queries'],
                    package_verified=True,package_sha256=package['package_sha256'],
                    validator_sha256=sha(report_path),validator=str(report_path)))
                write_json(out/'configurations.json',configurations)
            passed(2,[out/'validation-A01/level-bc.json',out/'validation-A02/level-bc.json',
                      regression/'full-equivalence.json'],'full static/native/reference semantics and independent search/Work oracle')
            passed(5,[out/'validation-A01/level-bc.json',out/'validation-A02/level-bc.json'],'both points pass Level B latency thresholds')
            passed(6,[out/'validation-A01/level-bc.json',out/'validation-A02/level-bc.json'],'both points pass QPS and CPU service thresholds')
            passed(7,[out/'validation-D01/workers.json',out/'validation-D02/workers.json'],'2/4 worker diagnostics, repeats, quotas and complete Work')
            # Recompute Level A from the unchanged frozen raw measurements.
            level_dir = out/'level-a'
            level_dir.mkdir()
            level_args = SimpleNamespace(profile=frozen_profile,fit_report=fit,start_manifest=start_manifest,
                prepared=prepared,collection=evidence/'level-a-v044b/run2',measured_collection=inputs,
                output=level_dir,replay_build=build,capture_retry=None)
            level_report = dict(format='ndpsim-v04-level-a-release/1',points={},status='failed')
            evaluate_level_a(level_args,level_report)
            summarize_level_a(level_report)
            write_json(level_dir/'level-a.json',level_report)
            passed(4,[level_dir/'level-a.json'],'two-point frozen-model errors, both-split measured table quality/coverage and current per-call billing')
            command('offload',[sys.executable,'-B',ROOT/'tools/evaluate_v04_offload.py',
                    '--prepared',prepared,'--capture',capture64,
                    '--profile',evidence/'cpu-fit-v043b/split-merge-v3/frozen/cpu-profile.json',
                    '--fit-report',evidence/'cpu-fit-v043b/split-merge-v3/frozen/fit-report.json',
                    '--level-a',level_dir/'level-a.json','--level-bc',
                    out/'validation-A01/level-bc.json',out/'validation-A02/level-bc.json',
                    '--build',build,'--output',out/'offload'],14400)
            offload = out/'offload'
            offload_report = load(offload/'offload.json')
            require(offload_report['status'] == 'passed' and offload_report['interpretation'] == 'conditional_prediction',
                    'offload must establish current CPU prerequisite')
            for label in ('O01','O02','O03','O04'):
                run = offload/label
                expected_point = next(r for r in plan['configurations'] if r['id'] == label)
                configurations.append(dict(id=label,parameters=expected_point,status='passed',
                    runs=[verified_run(run,build)],complete_queries=128,package_verified=True,
                    package_sha256=load(run/'config.json')['ir_input']['sha256']))
            passed(10,[offload/'offload.json'],'all B/C points, physical chunk/credit/return accounting; configured hardware and CPU-preparation assumptions')
            passed(8,[out/'validation-A02/level-bc.json',regression/'ctest.json',regression/'asan-static.json'],
                    'resident no-SSD, dependency/value/Work gates, delayed SSD returns, same-mapping old-cost parity and recapture fixtures')
            passed(9,[regression/'ctest.json',regression/'asan-static.json',level_dir/'level-a.json'],
                    'package, Profile, raw sampling, identity, feature domain and lifecycle negative tests retained')
            require(sha(build/'libndpsim.so') == element, 'candidate simulator changed during delivery')
        disk = load(out/'disk-measurement.json')
        require(disk['passed'] and disk['sampled_peak_file_bytes'] <= 16*GIB,'delivery disk budget exceeded')
        after = inventory()
        write_json(out/'source-end.json',after)
        require(start['source_sha256'] == after['source_sha256'] and start['mqsim'] == after['mqsim'] and
                start['sst_head'] == after['sst_head'] and not after['sst_status'],'candidate source or dependencies changed')
        passed(12,[out/'plan.json',out/'configurations.json',out/'commands.json',out/'source-end.json'],
                    'all core12 identities, repeats, current validations, immutable calibration inputs, inherited and conditional-prediction boundaries')
        write_json(out/'configurations.json',configurations)
        write_json(out/'ac-matrix.json',ac)
        write_json(out/'regression-impact.json',impact)
        final = acceptance(plan,configurations,ac,impact)
    except Exception as exc:
        error = str(exc)
        print('V0.4 delivery failed: '+error,flush=True)
    finally:
        write_json(out/'configurations.json',configurations)
        write_json(out/'ac-matrix.json',ac)
        write_json(out/'report.json',final or dict(schema=SCHEMA,status='failed',error=error,
            completed_configurations=[r['id'] for r in configurations]))
        markdown = ['# V0.4 delivery','',f'Status · {final["status"] if final else "failed"}','',
                    'Deep regression · not executed','']
        if error:
            markdown += [error,'']
        markdown += ['| AC | Status | Evidence |','| --- | --- | --- |']
        markdown += [f'| {key} | {row["status"]} | '+', '.join(row['evidence'])+' |' for key,row in sorted(ac.items())]
        (out/'report.md').write_text('\n'.join(markdown)+'\n')
        write_json(out/'manifest.json',dict(schema=SCHEMA,task='V04-5c',passed=final is not None,error=error,
            code_commit=start['commit'],commands=commands,deep_regression_executed=False,
            artifacts_sha256={str(path.relative_to(out)):sha(path) for path in out.rglob('*')
                              if path.is_file() and path != out/'manifest.json'}))
    if final is None:
        raise SystemExit(1)
    print('V0.4 core12 delivery passed.',flush=True)


if __name__ == '__main__':
    main()
