"""V04-5b compatibility and inherited regression, without deep regression.

Fresh R01–R04, detail8 companions, frozen-reference comparisons, full inherited
AC/oracle/slow/ASan and T3/T4/T5. A triggered deep gate blocks this task.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from ann_data import write_json
from compare_engines import compare
from freeze_direct_baseline import verify_hashes
from freeze_v04_start import dependency_snapshot, input_snapshot
from verify_ir_matrix import run_checks
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import DiskMonitor, GIB, load, query_groups, statistics

SCHEMA = 'ndpsim-v04-regression/1'
POINTS = [('R01', 'direct', 'host'), ('R02', 'direct', 'controller'),
          ('R03', 'ir', 'host'), ('R04', 'ir', 'controller')]
REQUIRED = {'references', 'inputs', 'configure', 'build', 'ctest', 'small_default',
            'full_compatibility', 'detail8', 'v01', 't3', 't4', 't5',
            'ir_asan', 'static_asan', 'source_unchanged', 'dependencies_unchanged',
            'build_unchanged', 'disk'}
ANCHORS = {
    'start': ('results/v0.4/start-v040/final1',
              'f5f461cb2edc584d5e5b27bb89882c9c26cf7245aa333d7a47189de6e8606110'),
    'direct': ('results/v0.3/direct-baseline-final',
               'd7f78b1076bc003043866174442d9cf2ef91452ce994a93c68c322033162dc11'),
    'matrix': ('results/v0.3/matrix-v036/final5',
               '82b6f200a6f92b1893e3d690f7ffed58dc34a536515876d52879144172356c41'),
    'pre_profile': ('results/v0.4/semantic-v041c/run1',
                    '2c11cacc9a86d6816e6b2582227cda791056188aab97ea45bf14339fd362e9e6'),
}
# These changed adapters were reviewed against the V04-0 source snapshot.
# New, unreviewed changes to default code fail the deep-regression gate.
REVIEWED = {
    'src/backend/event_bridge.h', 'src/content/content.cc', 'src/content/content.h',
    'src/ir/mapping.cc', 'src/ir/schema.cc', 'src/model/controller.cc',
    'src/model/controller.h', 'src/model/host_cpu.cc', 'src/model/host_cpu.h',
}
NEW_PATHS = ('src/search/native', 'src/search/core.', 'src/search/replay.',
             'src/model/cpu_profile', 'src/serialization/')


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def validate_inherited(v01, inherited):
    require(v01.get('scope') == 'full M4.2' and v01.get('status') == 'passed' and
            set(v01.get('checks', {})) == {f'AC-{i:02}' for i in range(1, 12)},
            'full V0.1 AC-01..11 required')
    require(all(rows and all(r.get('status') == 'passed' for r in rows)
                for rows in v01['checks'].values()), 'inherited V0.1 assertion failed')
    require(set(inherited) == {'t3', 't4', 't5'} and
            all(m.get('passed') is True for m in inherited.values()),
            'complete T3/T4/T5 required')


def assess_impact(changed, same_dependencies, compatible):
    unreviewed = sorted(name for name in changed
                        if name.startswith('src/') and name not in REVIEWED and
                        name != 'src/sst/graph_host.cc' and not name.startswith(NEW_PATHS))
    triggers = [
        dict(condition='old default semantics, costs, ordering or SSD path changed or unresolved',
             triggered=not compatible or bool(unreviewed),
             evidence='frozen/pre-Profile full results and statistics; default smoke; all inherited tests',
             unreviewed_default_sources=unreviewed),
        dict(condition='unexplained cross-configuration result, timing, ledger or resource anomaly',
             triggered=not compatible,
             evidence='R01–R04 strict comparison, detail8 independent ledgers, CTest, T3/T4/T5 and ASan'),
        dict(condition='dependency or shared layout/device model changed',
             triggered=not same_dependencies or bool(unreviewed),
             evidence='MQSim/SST full identity; reviewed adapter diff; unchanged default model/layout sources'),
    ]
    return dict(deep_regression_required=any(row['triggered'] for row in triggers),
                deep_regression_executed=False, deep_regression_policy='disabled_by_user',
                deep_regression_status='not_run', triggers=triggers,
                changed_sources=sorted(changed),
                source_review={
                    'src/ir/mapping.cc': 'pure core extraction preserves algorithm, containers and Work; independent core oracle and frozen comparisons',
                    'src/model/host_cpu.cc': 'explicit optional Profile; absent Profile keeps original service_ns formula and FIFO',
                    'src/backend/event_bridge.h': 'PlatformOnly is explicit; MQSim default retains backend dispatch and phase ordering',
                    'src/content/content.cc': 'optional immutable file-hash admission for static replay; existing reads/permissions unchanged',
                    'src/ir/schema.cc': 'JSON schema utility extraction; existing validation and rejection assertions retained',
                    'src/model/controller.cc': 'optional per-batch Work observation; default absent callback creates no new service or requests',
                    'src/sst/graph_host.cc': 'replay/provider/Profile branches explicit; old direct/ir dispatch, resources and costs retained; passive semantic observations opt-in',
                })


def acceptance(checks, covered, impact, v01, inherited):
    require(set(checks) == REQUIRED and all(value is True for value in checks.values()),
            'missing or failed compatibility/regression gate')
    require(len(covered) == 4 and set(covered) == {row[0] for row in POINTS},
            'four unique R configurations required')
    validate_inherited(v01, inherited)
    require(impact.get('deep_regression_executed') is False and
            impact.get('deep_regression_required') is False,
            'deep regression is required but was not executed')
    require(len(impact.get('triggers', [])) == 3 and
            all(row.get('triggered') is False for row in impact['triggers']),
            'incomplete deep-regression impact decision')
    return dict(status='passed', ac='AC-04-11', configurations=covered,
                deep_regression='not_required_this_revision; not_run',
                scope='V04-5b only; V0.4 total acceptance remains pending')


def require_run(path):
    manifest = load(path/'manifest.json')
    verify_hashes(path, manifest['artifacts_sha256'])
    require({'results.json', 'statistics.json', 'config.json'} <=
            set(manifest['artifacts_sha256']), 'unregistered compatibility artifacts')
    return manifest


def historical_comparison(current, reference):
    """Compare immutable outputs, never reuse a historical run as current execution."""
    now, old = require_run(current), require_run(reference)
    for key in ('dataset', 'input_identity', 'truth_sha256', 'content_backend',
                'mqsim_commit', 'sst_commit', 'mqsim_source_sha256'):
        require(now[key] == old[key], 'historical workload/dependency mismatch: '+key)
    a, b = load(current/'config.json'), load(reference/'config.json')
    for key in ('search', 'host', 'controller', 'placement', 'device_profile', 'max_inflight'):
        require(a[key] == b[key], 'historical parameter mismatch: '+key)
    require(load(current/'results.json') == load(reference/'results.json'),
            'historical results/timing changed: '+str(reference))
    require(statistics(current) == statistics(reference),
            'historical simulated statistics changed: '+str(reference))
    return dict(passed=True, current=str(current), reference=str(reference),
                current_manifest_sha256=sha(current/'manifest.json'),
                reference_manifest_sha256=sha(reference/'manifest.json'),
                statistics_sha256=statistics(current),
                differences_allowed=['top-level statistics.wall_seconds'],
                historical_source_sha256=old['source_sha256'],
                policy='historical comparison reference only; all current simulations freshly run')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, default=ROOT/'build/delivery')
    p.add_argument('--prepared', type=Path, default=ROOT/'results/v0.2-t6/preparation/prepared')
    p.add_argument('--small-prepared', type=Path, default=ROOT/'results/v0.2-t2/real-final')
    p.add_argument('--source-dir', type=Path, default=ROOT/'.cache/v0.2-m0/data/sift')
    p.add_argument('--builder-evidence', type=Path, default=ROOT/'results/v0.2-t2/builder')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    lock = load(ROOT/'deps.lock.json')
    require(not sys.flags.optimize and Path(sys.executable).resolve() ==
            Path(lock['tools']['python']['path']).resolve(), 'use locked Python without -O')
    out, build, prepared = a.output.resolve(), a.build.resolve(), a.prepared.resolve()
    out.mkdir(parents=True)
    before = inventory()
    write_json(out/'source-start.json', before)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=ROOT))
    commands, checks, rows, refs, error, summary = [], {}, [], {}, None, None
    cmake = lock['tools']['cmake']['path']

    def command(name, argv, timeout=3600, rss=None, env=None):
        receipt = measure(list(map(str, argv)), out/(name+'.json'), timeout, rss, env=env)
        commands.append(dict(name=name, **receipt))
        write_json(out/'commands.json', commands)
        print(f'{name}: exit={receipt["exit_code"]} wall={receipt["wall_seconds"]:.2f}s', flush=True)
        require(receipt['passed'], 'command failed: '+name)
        return receipt

    def simulate(name, config, data):
        config_path = out/(name+'-config.json')
        write_json(config_path, config)
        target = out/name
        command(name, [sys.executable, '-B', ROOT/'tools/run_config.py', '--config', config_path,
                       '--build', build, '--data', data, '--output', target], 11400, 4*GIB)
        manifest = require_run(target)
        require(manifest['element_sha256'] == element_hash, 'simulation build changed')
        require(all(sha(ROOT/n) == h for n, h in manifest['source_sha256'].items()),
                'current simulation source mismatch')
        require(manifest['execution_engine'] == config.get('execution_engine', 'ir') and
                not load(target/'sst-params.json').get('cpu_profile') and
                load(target/'sst-params.json').get('data_provider', 'ssd') == 'ssd',
                'legacy engine/provider/cost defaults changed')
        return target

    try:
        require(before['physical_ram_bytes'] and before['physical_ram_bytes'] >= 8*GIB and
                before['disk_free_bytes'] >= 20*GIB, 'RAM or disk preflight failed')
        deps = dependency_snapshot(out)
        write_json(out/'dependencies.json', deps)
        # Monitor this task's new evidence. Existing inputs/results are separately inventoried.
        with DiskMonitor(out, [out]):
            for label, (relative, anchor) in ANCHORS.items():
                path = ROOT/relative
                require(sha(path/'manifest.json') == anchor, 'frozen reference anchor mismatch: '+label)
                manifest = load(path/'manifest.json')
                require(manifest.get('passed') is True, 'failed historical reference: '+label)
                verify_hashes(path, manifest['artifacts_sha256'])
                refs[label] = dict(path=str(path), manifest_sha256=anchor,
                                   verified_artifacts=len(manifest['artifacts_sha256']))
            write_json(out/'references.json', refs)
            checks['references'] = True
            write_json(out/'inputs.json', input_snapshot(prepared, a.source_dir.resolve(),
                                                        a.builder_evidence.resolve()))
            checks['inputs'] = True
            budget = dict(physical_ram_bytes=before['physical_ram_bytes'],
                          minimum_free_bytes=20*GIB, task_output_bytes_max=8*GIB,
                          sst_peak_rss_bytes_max=4*GIB, python_peak_rss_bytes_max=512*1024**2,
                          initialization_seconds_max=3600, query_seconds_max=7200,
                          postprocessing_seconds_max=600,
                          resource_scope='new task output; raw/prepared hashes verified, existing evidence retained',
                          deep_regression='disabled_by_user')
            write_json(out/'budget-frozen.json', budget)
            write_json(out/'plan.json', dict(task='V04-5b', configurations=POINTS,
                full_scale_runs=4, detail8_runs=4, small_default_runs=4,
                inherited=['full V0.1 AC including MQSim oracle, slow functional and ASan',
                           'T3', 'T4', 'T5', 'IR ASan', 'static replay ASan'],
                deep_regression='disabled_by_user; triggered requirement blocks acceptance'))
            command('configure', [cmake, '-S', ROOT, '-B', build, '-DBUILD_TESTING=ON',
                                  '-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
            checks['configure'] = True
            command('build', [cmake, '--build', build, '-j2'])
            checks['build'] = True
            command('ctest', ['ctest', '--test-dir', build, '--output-on-failure'])
            checks['ctest'] = True
            element_hash = sha(build/'libndpsim.so')
            shutil.copyfile(build/'compile_commands.json', out/'compile_commands.json')
            shutil.copyfile(build/'CMakeCache.txt', out/'CMakeCache.txt')
            shutil.copyfile(build/'sst-paths.json', out/'sst-paths.json')
            frozen = ROOT/ANCHORS['direct'][0]
            small = {}
            for mode in ('host', 'controller'):
                c = load(frozen/('small-'+mode)/'config.json')
                c.pop('execution_engine', None)
                omitted = simulate('default-'+mode, c, frozen/('small-'+mode)/'data')
                direct = simulate('direct-'+mode, dict(c, execution_engine='direct'),
                                  frozen/('small-'+mode)/'data')
                report = compare(direct, omitted)
                require(report['passed'], 'omitted engine differs from explicit direct')
                small[mode] = dict(strict=report, frozen=historical_comparison(direct, frozen/('small-'+mode)),
                    pre_refactor=historical_comparison(direct, ROOT/ANCHORS['start'][0]/('direct-'+mode)))
                require(sha(direct/'events.jsonl') == sha(frozen/('small-'+mode)/'events.jsonl'),
                        'frozen small event stream changed')
            write_json(out/'small-default.json', small)
            checks['small_default'] = True
            matrix = ROOT/ANCHORS['matrix'][0]
            details = {}
            for label, engine, mode in POINTS:
                old = matrix/f'{engine}-evaluation-l64-e128-c1-{mode}'
                c = load(old/'config.json')
                # R03/R04 exercise the actual omitted-engine daily entry.
                if engine == 'ir':
                    c.pop('execution_engine')
                full = simulate(label, c, prepared/'evaluation')
                gates, resources = run_checks(full, dict(engine=engine, placement=mode,
                    group='evaluation', concurrency=1), budget)
                require(all(gates.values()), 'full-scale resource/identity gate failed: '+label)
                row = dict(id=label, engine=engine, placement=mode,
                           original_reference=historical_comparison(full, old),
                           checks=gates, resources=resources)
                rows.append(row)
                detail_config = dict(c, event_log=True)
                detail_config['truth'] = str(prepared/'detail-truth')
                details[label] = simulate(label+'-detail8', detail_config, prepared/'detail')
                query_rows = load(full/'results.json')['queries'][:8]
                require(query_rows == load(details[label]/'results.json')['queries'],
                        'detail8 differs from full evaluation prefix')
                write_json(out/'compatibility-points.json', rows)
            full_reports = {mode: compare(out/f'R0{1 if mode == "host" else 2}',
                                         out/f'R0{3 if mode == "host" else 4}')
                            for mode in ('host', 'controller')}
            require(all(row['passed'] for row in full_reports.values()), 'R direct/IR strict mismatch')
            write_json(out/'full-equivalence.json', full_reports)
            checks['full_compatibility'] = True
            detail_reports = {mode: compare(details['R01' if mode == 'host' else 'R02'],
                                           details['R03' if mode == 'host' else 'R04'])
                              for mode in ('host', 'controller')}
            require(all(row['passed'] for row in detail_reports.values()), 'detail8 direct/IR mismatch')
            for engine, host_id, ctrl_id in [('direct', 'R01', 'R02'), ('ir', 'R03', 'R04')]:
                command('independent-detail8-'+engine,
                        [sys.executable, ROOT/'tools/compare_placements.py', details[host_id],
                         details[ctrl_id], '--output', out/('detail8-'+engine+'-ledger.json')], 3600, 4*GIB)
            write_json(out/'detail8-equivalence.json', detail_reports)
            checks['detail8'] = True
            command('v01', [sys.executable, '-B', ROOT/'tools/verify_acceptance.py',
                            '--build', build, '--output', out/'v01'], 10800)
            checks['v01'] = True
            for stage in ('t3', 't4', 't5'):
                command(stage, [sys.executable, '-B', ROOT/f'tools/verify_{stage}.py',
                                '--build', build, '--prepared', a.small_prepared.resolve(),
                                '--output', out/stage], 7200)
                checks[stage] = True
            asan = build.with_name(build.name+'-asan')
            command('asan-ir-build', [cmake, '--build', asan, '--target', 'ir_executor_probe',
                    'ir_schema_probe', 'ir_frontend_probe', 'ir_frontend_causal_probe',
                    'ir_mapping_probe', 'ndpsim', 'ndpsim_native', '-j2'])
            command('asan-ir', ['ctest', '--test-dir', asan, '--output-on-failure', '-R',
                    '^(ir_dynamic_lifecycle|ir_schema_memory|ir_graph_frontend|ir_graph_frontend_causality|ir_operator_placements)$'],
                    env={'ASAN_OPTIONS': 'halt_on_error=1'})
            checks['ir_asan'] = True
            runtime = subprocess.check_output([lock['tools']['cxx']['path'], '-print-file-name=libclang_rt.asan_osx_dynamic.dylib'], text=True).strip()
            require(Path(runtime).is_file(), 'ASan runtime unavailable')
            command('asan-static', [sys.executable, '-B', ROOT/'tests/test_static_replay.py', asan,
                                    out/'asan-static'], env={'NDPSIM_TEST_ASAN_RUNTIME': runtime})
            checks['static_asan'] = True
        disk = load(out/'disk-measurement.json')
        checks['disk'] = disk['passed'] and disk['sampled_peak_file_bytes'] <= budget['task_output_bytes_max']
        after = inventory()
        write_json(out/'source-end.json', after)
        checks['source_unchanged'] = before['source_sha256'] == after['source_sha256']
        checks['dependencies_unchanged'] = before['mqsim'] == after['mqsim'] and before['sst_head'] == after['sst_head'] and not after['sst_status']
        checks['build_unchanged'] = element_hash == sha(build/'libndpsim.so')
        origin = load(ROOT/ANCHORS['start'][0]/'source-start.json')
        changed = [name for name in set(after['source_sha256']) | set(origin['source_sha256'])
                   if name.startswith('src/') and
                   origin['source_sha256'].get(name) != after['source_sha256'].get(name)]
        same_dependencies = before['mqsim'] == origin['mqsim'] and before['sst_head'] == origin['sst_head']
        impact = assess_impact(changed, same_dependencies, all(checks.values()))
        write_json(out/'regression-impact.json', impact)
        summary = acceptance(checks, [r['id'] for r in rows], impact,
                             load(out/'v01/ac-matrix.json'),
                             {s: load(out/s/'manifest.json') for s in ('t3', 't4', 't5')})
    except Exception as exc:
        error = str(exc)
        print('V04-5b failed: '+error, flush=True)
        write_json(out/'regression-impact.json', assess_impact([], False, False))
    finally:
        write_json(out/'assertions.json', checks)
        write_json(out/'report.json', summary or dict(status='failed', ac='AC-04-11', error=error))
        write_json(out/'manifest.json', dict(schema=SCHEMA, task='V04-5b', passed=summary is not None,
            error=error, code_commit=before['commit'], element_sha256=locals().get('element_hash'),
            commands=commands, covered_configurations=[r['id'] for r in rows],
            deep_regression_executed=False, source_start_sha256=sha(out/'source-start.json'),
            artifacts_sha256={str(path.relative_to(out)): sha(path)
                              for path in out.rglob('*') if path.is_file() and path != out/'manifest.json'}))
    if summary is None:
        raise SystemExit(1)
    print('V04-5b passed; deep regression not run.', flush=True)


if __name__ == '__main__':
    main()
