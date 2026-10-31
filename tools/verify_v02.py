"""T8/M4 delivery: current regressions, identity-checked SIFT matrix and reproduction.

No historical pass flag is used as delivery acceptance. Preparation is reusable;
--reuse-runs revalidates each of 26 runs through verify_t6's strict identity gate.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from ann_data import write_json
from source_snapshot import mqsim_source_snapshot
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import DiskMonitor, load, statistics, verify_cached_run, GIB


def verify_hashes(root, hashes):
    if not hashes:
        raise ValueError('empty artifact inventory')
    for name, expected in hashes.items():
        path = root/name
        if not path.is_file() or sha(path) != expected:
            raise ValueError('artifact identity mismatch: '+str(path))


def acceptance_matrix(v01, t3, t4, t5, sift, reproduction):
    """Require complete current suites before mapping individually named ACs."""
    expected = {f'AC-{i:02}' for i in range(1, 12)}
    if v01.get('scope') != 'full M4.2' or v01.get('status') != 'passed' or set(v01.get('checks', {})) != expected:
        raise ValueError('complete V0.1 acceptance required')
    if any(not rows or any(r.get('status') != 'passed' for r in rows) for rows in v01['checks'].values()):
        raise ValueError('V0.1 assertion failed')
    if not all(m.get('passed') is True for m in (t3, t4, t5, sift)) or not reproduction or not all(reproduction.values()):
        raise ValueError('current V0.2 evidence incomplete')
    evidence = {
        1: ['v01/ctest.log: benchmark_data_schema'],
        2: ['v01/ctest.log: benchmark_artifact_schema, simulation_input_schema', 'sift/manifest.json'],
        3: ['sift/manifest.json', 'sift/assertions.json: quality_gates', 'dependencies.json'],
        4: ['v01/ctest.log: benchmark_artifact_schema', 't3/assertions.json', 'sift/quality-matched.json'],
        5: ['t3/assertions.json', 'sift/detail-comparison-report.json'],
        6: ['t4/assertions.json', 'sift/assertions.json'],
        7: ['sift/assertions.json', 'sift/*-initial-state.json'],
        8: ['t5/assertions.json', 't5/manifest.json: resources', 'sift/assertions.json'],
        9: ['sift/scan.json', 'sift/manifest.json', 'disk-measurement.json'],
        11: ['reproduction.json', 'sift/report.md', 'sift/quality-matched.json'],
        12: ['v01/ac-matrix.json', 'v01/parameters.json: native_oracle', 'v01/asan-ctest.log'],
    }
    result = {f'AC-02-{i:02}': dict(status='passed', evidence=paths) for i, paths in evidence.items()}
    result['AC-02-10'] = dict(status='N/A', reason='Deep10M removed from V0.2 scope on 2026-10-31; not counted as pass')
    return dict(scope='T8/M4 full V0.2 delivery', status='passed', applicable_passed=11,
                not_applicable=1, checks=dict(sorted(result.items())))


def dependencies(prepared, small, source):
    """Verify actual frozen inputs and builder, preserve historical trace separately."""
    locked = load(ROOT/'configs/benchmarks/datasets.lock.json')['datasets']['sift1m']
    raw = {}
    for role, info in locked['files'].items():
        path = source/('sift_'+role+('.ivecs' if role == 'groundtruth' else '.fvecs'))
        if path.stat().st_size != info['bytes'] or sha(path) != info['sha256']:
            raise ValueError('raw SIFT identity mismatch: '+role)
        raw[role] = dict(path=str(path), sha256=info['sha256'], bytes=info['bytes'])
    from benchmark_data import check
    from benchmark_artifacts import check_index, check_truth
    check(small/'small-data'); check_index(small/'small-data', small/'index')
    check_truth(small/'small-data', small/'exact', 10)
    index = load(prepared/'index/index.json')
    receipt = index['builder']['receipt']
    verify_hashes(ROOT, {'configs/benchmarks/m0-builder.lock.json': receipt['lock_sha256'],
                        'tools/offline/index_builder.cc': receipt['adapter_sha256']})
    exe = Path(receipt['executable'])
    verify_hashes(exe.parent, {exe.name: receipt['executable_sha256']})
    # Historical closeouts are traceability, never a current regression pass.
    trace = {str(p.relative_to(ROOT)): sha(p) for p in [ROOT/'docs/m0-closeout.md',
        *(ROOT/f'docs/t{i}-closeout.md' for i in range(1, 7))]}
    return dict(raw=raw, builder=receipt, small_prepared=str(small), prepared=str(prepared),
                historical_closeout_sha256=trace,
                policy='T0-T6 closeouts retained as historical trace; current tests and strict matrix revalidation decide delivery')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--all', action='store_true', required=True, help='Full delivery only; no quick pass')
    p.add_argument('--prepared', type=Path, required=True, help='Validated T6 preparation')
    p.add_argument('--small-prepared', type=Path, required=True, help='T2 small-data/index/exact')
    p.add_argument('--source-dir', type=Path, required=True, help='Locked original SIFT files')
    p.add_argument('--reuse-runs', type=Path, help='Optional strict reuse of 26 completed T6 runs')
    p.add_argument('--build', type=Path, default=ROOT/'build/delivery')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if sys.flags.optimize: p.error('assertions must be enabled (no python -O)')
    lock = load(ROOT/'deps.lock.json')
    if Path(sys.executable).resolve() != Path(lock['tools']['python']['path']).resolve():
        p.error('use locked Python: '+lock['tools']['python']['path'])
    out = a.output.resolve(); build = a.build.resolve()
    prepared = a.prepared.resolve(); small = a.small_prepared.resolve(); source = a.source_dir.resolve()
    if out.exists(): p.error('output must not exist')
    out.mkdir(parents=True)
    commands = []; assertions = {}; passed = False; error = None
    before = inventory(); write_json(out/'source-start.json', before)
    def command(name, argv, timeout=3600, rss=None):
        r = measure(list(map(str, argv)), out/(name+'.json'), timeout, rss)
        commands.append(dict(name=name, **r))
        print(f'{name}: exit={r["exit_code"]} wall={r["wall_seconds"]:.2f}s', flush=True)
        if not r['passed']: raise RuntimeError('delivery command failed: '+name)
    roots = [out, prepared, small, source]
    if a.reuse_runs: roots.append(a.reuse_runs.resolve())
    try:
        with DiskMonitor(out, roots):
            write_json(out/'dependencies.json', dependencies(prepared, small, source))
            cmake = lock['tools']['cmake']['path']
            command('delivery-unit', [sys.executable, ROOT/'tests/test_v02_delivery.py'])
            command('configure', [cmake, '-S', ROOT, '-B', build, '-DBUILD_TESTING=ON', '-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
            command('build', [cmake, '--build', build, '-j2'])
            # Full acceptance includes another configure/build, full CTest,
            # independent locked native QD1/QD32/CMT oracle, slow L2 and ASan.
            command('v01', [sys.executable, ROOT/'tools/verify_acceptance.py', '--build', build, '--output', out/'v01'], 7200)
            for stage in ('t3', 't4', 't5'):
                command(stage, [sys.executable, ROOT/f'tools/verify_{stage}.py', '--build', build,
                    '--prepared', small, '--output', out/stage], 7200)
            argv = [sys.executable, ROOT/'tools/verify_t6.py', '--build', build, '--prepared', prepared,
                    '--source-dir', source, '--output', out/'sift']
            if a.reuse_runs: argv += ['--reuse-runs', a.reuse_runs.resolve()]
            command('sift', argv, 14400, 512*1024**2)
            # Always run new full-base evaluation128 main-point experiments;
            # compare every result and simulated statistic against checked matrix.
            reproduced = {}
            for mode in ('host', 'controller'):
                name = f'evaluation-l128-e256-c1-{mode}'
                old = out/'sift'/name; target = out/('reproduce-'+mode)
                command('reproduce-'+mode, [sys.executable, ROOT/'tools/run_config.py', '--config', old/'config.json',
                    '--data', prepared/'evaluation', '--build', build, '--output', target], 11400)
                verify_cached_run(target, load(old/'config.json'), prepared, 'evaluation', build)
                reproduced[mode+'_results'] = load(old/'results.json') == load(target/'results.json')
                reproduced[mode+'_statistics'] = statistics(old) == statistics(target)
                m = load(target/'sst-measurement.json')
                reproduced[mode+'_budget'] = (m['peak_rss_bytes'] <= 4*GIB and
                    m['python_parent_peak_rss_bytes'] <= .5*GIB and m['postprocessing_wall_seconds'] <= 600 and
                    m['phases_wall_seconds']['initialization_wall_seconds'] <= 3600 and
                    m['phases_wall_seconds']['query_wall_seconds'] <= 7200)
            write_json(out/'reproduction.json', reproduced)
            matrix = acceptance_matrix(load(out/'v01/ac-matrix.json'), *(load(out/stage/'manifest.json') for stage in ('t3','t4','t5')),
                                       load(out/'sift/manifest.json'), reproduced)
            assertions.update(reproduction=all(reproduced.values()), current_suites=matrix['status']=='passed')
        assertions['disk_budget'] = load(out/'disk-measurement.json')['passed']
        after = inventory(); write_json(out/'source-end.json', after)
        assertions['source_unchanged'] = before['source_sha256'] == after['source_sha256']
        assertions['mqsim_unchanged'] = before['mqsim'] == after['mqsim']
        assertions['sst_clean_unchanged'] = before['sst_head'] == after['sst_head'] and not before['sst_status'] and not after['sst_status']
        command('diff-check', ['git', 'diff', '--check'])
        if not all(assertions.values()): raise RuntimeError('delivery gates failed')
        write_json(out/'ac-matrix.json', matrix)
        passed = True
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        write_json(out/'assertions.json', assertions)
        if not passed:
            write_json(out/'ac-matrix.json', dict(status='failed_or_incomplete', checks={
                f'AC-02-{i:02}': dict(status='N/A' if i == 10 else 'failed_or_incomplete') for i in range(1, 13)}))
        write_json(out/'manifest.json', dict(schema='ndpsim-v02-delivery-v1', stage='T8/M4', passed=passed,
            error=error, scope='full current V0.1 acceptance + T3/T4/T5 reruns + SIFT matrix revalidation + fresh evaluation128 pair',
            commands=commands, mqsim=mqsim_source_snapshot(), prepared=str(prepared),
            reuse_runs=str(a.reuse_runs.resolve()) if a.reuse_runs else None,
            source_inventory='source-start.json',
            artifacts_sha256={str(f.relative_to(out)): sha(f) for f in out.rglob('*') if f.is_file()},
            limits='configured_unvalidated; official tie boundary unresolved; fixed query subsets; sampled disk; no private/phase/simultaneous tree RSS; existing SST cache, not clean rebuild'))
    print('T8/M4 delivery passed; no next task started', flush=True)


if __name__ == '__main__': main()
