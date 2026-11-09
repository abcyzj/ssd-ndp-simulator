"""Aggregate the V0.4.5 formal run and an independent clean-build receipt."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from audit_v045_baseline import ROOT, digest, inventory
from source_snapshot import mqsim_source_snapshot


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_text())


def check_files(directory, files):
    require(isinstance(files, dict) and files, f'empty hash inventory: {directory}')
    for name, expected in files.items():
        path = Path(name)
        require(not path.is_absolute() and '..' not in path.parts, f'invalid evidence path: {name}')
        actual = directory / path
        require(actual.is_file() and digest(actual) == expected, f'missing or changed evidence: {actual}')


def git(directory, *args):
    return subprocess.check_output(['git', '-C', str(directory), *args], text=True).strip()


def commands_passed(commands, label):
    require(commands and all(c.get('argv') and type(c.get('exit_code')) is int
                             and c['exit_code'] == 0 for c in commands), f'failed or missing commands: {label}')


def formal_report(report, layer):
    require(report['format'] == 'ndpsim-layer-validation/1' and report['layer'] == layer
            and report['scope'] == 'formal-point' and report['layer_passed'] is True
            and report['provenance']['kind'] == 'native-capture', f'invalid formal {layer} report')
    return report


def ctest_passed(log, count):
    return re.search(rf'^100% tests passed(?:, 0 tests failed)? out of {count}$',
                     log.read_text(), re.MULTILINE) is not None


def source_identity(boundary, clean_source):
    files = boundary['source_sha256']
    require(set(files) == set(inventory()) - {'tools/verify_release.py'}, 'P20 source inventory differs')
    release_docs = {'examples/extension/README.md', 'python/CLI.md', 'python/VALIDATION.md'}
    check_files(ROOT, {name: expected for name, expected in files.items() if name not in release_docs})
    check_files(clean_source, files)
    deps = boundary['dependencies']
    require(set(deps['gitlinks']) == {'third_party/MQSim', 'third_party/sst-core'}, 'missing dependency')
    for name, commit in deps['gitlinks'].items():
        require(git(ROOT, 'rev-parse', f'HEAD:{name}') == commit, f'gitlink differs: {name}')
        for tree in (ROOT, clean_source):
            require(git(tree / name, 'rev-parse', 'HEAD') == commit
                    and not git(tree / name, 'status', '--porcelain'), f'dirty or changed dependency: {tree / name}')
    mqsim = mqsim_source_snapshot()
    for field in ('native_base_commit', 'head_commit', 'diff_from_native_base_sha256', 'files_sha256'):
        require(mqsim[field] == deps['mqsim'][field], f'MQSim {field} differs')
    check_files(clean_source / 'third_party/MQSim', mqsim['files_sha256'])
    sst = ROOT / 'third_party/sst-core'
    sst_files = {name: digest(sst / name) for name in git(sst, 'ls-files').splitlines()}
    check_files(clean_source / 'third_party/sst-core', sst_files)
    return dict(source_sha256=files, gitlinks=deps['gitlinks'], mqsim=mqsim,
                sst_files_sha256=sst_files, verifier_sha256=digest(Path(__file__)),
                release_docs_sha256={name: digest(ROOT / name) for name in sorted(release_docs)})


def verify(evidence, clean, workspace, formal_root, report):
    p20 = evidence / 'p20'
    p18 = evidence / 'p18/run3'
    clean_source = workspace / 'source'
    manifests = {}

    def manifest(directory, status=None):
        path = directory / 'manifest.json'
        data = load(path)
        require(not data.get('quick') and not data.get('fixture'), f'non-formal evidence: {path}')
        if status == 'passed':
            require(data.get('status') == status, f'failed evidence: {path}')
        elif status is True:
            require(data.get('passed') is True, f'failed evidence: {path}')
        files = dict(data['artifacts_sha256'])
        if directory == p20:
            # P20 hashed this redirected stdout before its own final print.
            require(files.pop('seal.log', None) ==
                    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
                    and (directory / 'seal.exit').read_text().strip() == '0'
                    and load(directory / 'seal.log') == data['summary'], 'invalid P20 seal receipt')
            report['post_seal_stdout_sha256'] = digest(directory / 'seal.log')
        check_files(directory, files)
        manifests[str(path.relative_to(evidence))] = digest(path)
        return data

    def accept(number, assertion, *paths):
        report['matrix'][f'AC-045-{number:02}'] = dict(status='passed', assertion=assertion,
            artifacts_sha256={str(path): digest(path) for path in paths})

    manifest(p20, True)
    boundary_path = p20 / 'boundary3/boundaries.json'
    boundary = load(boundary_path)
    report['identity'] = source_identity(boundary, clean_source)
    recorded_root = Path(boundary['dependencies']['mqsim']['source_directory']).parents[1]
    identities = load(p20 / 'formal2/identities.json')
    formal_builds = dict(boundary['builds'])
    for artifact in [identities['native'], *identities['build']]:
        path, expected = artifact['path'], artifact['sha256']
        require(formal_builds.get(path, expected) == expected, f'inconsistent formal build hash: {path}')
        formal_builds[path] = expected
    relative_builds = {str(Path(path).relative_to(recorded_root)): expected
                       for path, expected in formal_builds.items()}
    check_files(formal_root, relative_builds)
    report['identity']['formal_build_sha256'] = relative_builds
    for name in ('formal2', 'tuning1'):
        commands_passed(load(p20 / name / 'commands.json'), name)
        require(load(p20 / name / 'summary.json')['passed'] is True, f'failed {name}')
    for name in ('configure-current', 'build-current', 'ctest-current', 'product-build',
                 'asan-build', 'asan-address-only', 'dependency-gates'):
        require((p20 / f'run2/{name}.exit').read_text().strip() == '0', f'failed {name}')

    gates = load(p20 / 'run2/dependency-gates.json')
    for name in ('p17/run2', 'p18/run3', 'p19/run1'):
        manifest(evidence / name, True)
        require(manifests[f'{name}/manifest.json'] == gates[name]['manifest_sha256'], f'changed {name}')
    for index in (1, 2):
        name = f'p18/run{index}'
        manifest(evidence / name)
        expected = load(p18 / 'reuse.json')['previous'][index - 1]['sha256']
        require(manifests[f'{name}/manifest.json'] == expected, f'changed {name}')
    aba_path = evidence / 'p18/run1/aba.json'
    aba = load(aba_path)
    require(aba['passed'] is True and len(aba['groups']) == 3
            and all(len(g['qps']) == 3 for g in aba['groups'])
            and 0 <= aba['median_absolute_fraction'] <= .05, 'formal A/B/A failed')

    tuning = evidence / 'p18/run1'
    captures, accuracy_paths, ssd_paths, table_ledgers = [], [], [], []
    for point in ('32-64', '64-128'):
        for stage, base, queries, split in (
                ('tuning', tuning, 32, 'tuning32'), ('eval', p18, 128, 'evaluation128')):
            name = f'{stage}-{point}'
            path = base / f'{name}-capture/semantic.json'
            capture = load(path)
            require(capture['independent_semantic_passed'] is True and capture['structural_passed'] is True
                    and capture['semantic_validation'] == 'independent-full-query'
                    and capture['provenance']['split'] == split and len(set(capture['query_ids'])) == queries,
                    f'incomplete capture: {name}')
            captures.append(path)
            sample = f'{name}-table-sample' if stage == 'tuning' else f'{name}-sample'
            for repeat in range(3):
                status = load(base / f'{sample}-{repeat}/status.json')
                require(status['passed'] is True and status['mode'] == 'sample', f'incomplete sample: {sample}')
        path = p20 / f'formal2/eval-{point}-accuracy/report.json'
        accuracy = formal_report(load(path), 'accuracy')
        require(accuracy['package_sha256'] == capture['package_sha256']
                and accuracy['native_repeats'] >= 3 and accuracy['simulated_repeats'] >= 2,
                f'incomplete A-point repeats or package mismatch: {point}')
        for field, limit in dict(cpu=.15, p50=.15, p95=.20, qps=.10).items():
            require(0 <= accuracy['errors'][field] <= limit, f'{point}: failed {field}')
        stages = accuracy['stage_evaluation']
        require(stages['passed'] is True and stages['fixture'] is False
                and not set(stages['partitions']['tuning']) & set(stages['partitions']['evaluation']),
                f'{point}: invalid model evaluation')
        for source, count in (('prediction', 5), ('measured-table', 4)):
            rules = [r for r in stages['rules'] if r['source'] == source]
            require(len(rules) == count and all((r['prediction_quality_passed'] is True and 0 <= r['metrics']['relative_p50'] <= .10
                     and 0 <= r['metrics']['relative_p90'] <= .20)
                    if source == 'prediction' else r['prediction_error'] == 'not-applicable' for r in rules),
                    f'{point}: invalid {source} coverage')
        accuracy_paths.append(path)
        for directory, ledger in [('formal2', f'eval-{point}-reference-ledger'),
                                  ('tuning1', f'tuning-ledger-{point}-zero'),
                                  ('tuning1', f'tuning-ledger-{point}-reference')]:
            path = p20 / directory / ledger / 'report.json'
            formal_report(load(path), 'simulate')
            table_ledgers.append(path)

    for index, accuracy_path in enumerate(accuracy_paths, 1):
        configs = []
        for letter, placement in (('B', 'host'), ('C', 'controller')):
            name = f'{letter}{index:02}'
            path = p20 / 'formal2' / name / 'report.json'
            ledger_path = path.parent.with_name(name + '-ledger') / 'report.json'
            data = load(path)
            ledger = formal_report(load(ledger_path), 'simulate')
            require(data['requests'] == len(ledger['queries']) == 128 and data['execution_succeeded'] is True
                    and data['placement'] == placement and data['hardware_validation'] == 'configured_unvalidated'
                    and data['package_sha256'] == ledger['package_sha256'] == load(accuracy_path)['package_sha256']
                    and data['drain']['success'] is True and (letter == 'B' or data['drain']['vector_d2h'] == 0),
                    f'failed SSD ledger: {name}')
            configs.append(load(path.parent / 'run-input.json'))
            ssd_paths.extend((path, ledger_path))
        require({k for k in configs[0] | configs[1] if configs[0].get(k) != configs[1].get(k)}
                == {'placement', 'context'}, f'incomparable SSD configurations: {index}')
        ssd_paths.append(p20 / f'formal2/compare-{index:02}/report.json')

    oracle = manifest(p20 / 'oracle1', 'passed')
    commands_passed(oracle['commands'], 'MQSim oracle')
    require(oracle['schema'] == 'ndpsim-mqsim-verification/1'
            and oracle['sst_commit'] == boundary['dependencies']['gitlinks']['third_party/sst-core']
            and oracle['integrated_mqsim'] == boundary['dependencies']['mqsim']
            and set(oracle['checks']) == {'qd1', 'qd32', 'small-cmt', 'modified-xml'}
            and oracle['comparison']['exclusions']
            and all(c['status'] == 'passed' and min(c['native_runs'], c['sst_runs']) >= 2
                    for c in oracle['checks'].values()), 'incomplete MQSim oracle')

    fit_path = p18 / 'fit/fit/fit-report.json'
    fit = load(fit_path)
    require(fit['action'] == 'fit' and fit['fixture'] is False and fit['passed'] is True
            and load(p18 / 'summary.json')['tuning_sensitivities_passed'] is True, 'formal fit or sensitivity failed')
    for point in load(p18 / 'summary.json')['formal_points'].values():
        require(point['sensitivity']['passed'] is True, 'evaluation sensitivity failed')
    for name in ('clean', 'isolated'):
        require((clean / f'{name}.exit').read_text().strip() == '0', f'clean {name} workflow failed')
    root_log = clean / 'clean.log'
    isolated_log = clean / 'isolated.log'
    expected = {test['name'] for test in load(ROOT / 'docs/migration-v0.4.5/tests.json')['tests']}
    passed = set(re.findall(r'Test\s+#\d+: (\S+)\s+\.+\s+Passed', root_log.read_text()))
    require(expected == passed and ctest_passed(root_log, len(expected)),
            'clean CTest coverage missing')
    require(ctest_passed(isolated_log, 4)
            and 'graph_capture_v2' in isolated_log.read_text()
            and re.search(r'Ran \d+ tests? in .*\n\nOK(?: \(skipped=\d+\))?\n', isolated_log.read_text()), 'isolated graph/fitting checks missing')
    structural = workspace / 'check.json'
    require(load(structural)['structural_passed'] is True, 'installed CLI check failed')
    tutorial = workspace / 'tutorial/comparison.json'
    require(load(tutorial)['teaching_passed'] is True, 'teaching workflow failed')
    build = workspace / 'build'
    paths = load(build / 'sst-paths.json')
    require(paths['mode'] == 'bundled' and Path(paths['prefix']).is_relative_to(workspace.resolve()),
            'clean SST prefix is external')
    built = [build / name for name in ('CMakeCache.txt', 'sst-paths.json', 'libndpsim_memory.so',
             'libndpsim_graph_ssd.so', 'libndpsim.so')]
    built.extend([Path(paths['sst']), workspace / 'native/build/ndpsim_native', workspace / 'python/bin/ndpsim'])
    report['identity']['clean_build_sha256'] = {str(path): digest(path) for path in built}

    accept(1, 'Isolated native build, full capture, benchmark, measurement and independent Work', isolated_log, aba_path, *captures)
    accept(2, 'IR/DAG positive and negative regressions and complete formal semantic receipts', root_log, *captures)
    accept(3, 'Payload-free runs, production link audit and input/service/return/zero-time regressions', root_log, boundary_path)
    accept(4, 'Both formal CPU points meet latency, throughput, CPU and stage thresholds', *accuracy_paths)
    accept(5, 'Four locked native/integrated MQSim comparisons with two repetitions per side', p20 / 'oracle1/manifest.json')
    accept(6, 'Four formal SSD ledgers and two same-condition comparisons', *ssd_paths)
    accept(7, 'Layout/configuration boundaries, effective configurations and modified XML on the same build', root_log, p20 / 'oracle1/manifest.json')
    accept(8, 'Independent fit, held-out stages, separate table sources and all ledger/sensitivity checks', fit_path, *accuracy_paths, *table_ledgers)
    accept(9, 'Real teaching capture, independent semantic oracle and both SST mappings', tutorial, root_log)
    accept(10, 'Migration inventory, current regression, sanitizer and post-cleanup formal evidence', p20 / 'manifest.json', root_log)
    accept(11, 'Clean bundled simulation build, isolated native and fitting, installed CLI workflow', root_log, isolated_log, structural)
    accept(12, 'Required evidence, source, dependencies, build and artifact hashes verified', *(
        evidence / name for name in manifests), clean / 'clean.sh', clean / 'isolated.sh',
        clean / 'clean.exit', clean / 'isolated.exit', p20 / 'formal2/identities.json')
    report['manifests_sha256'] = manifests
    report['formal_configurations'] = ['A01', 'A02', 'B01', 'C01', 'B02', 'C02']
    report['formal_simulations'] = 8


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'clean', 'workspace', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--formal-root', type=Path, default=ROOT, help='root of the recorded formal build files')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(format='ndpsim-release-verification/1', version='0.4.5',
                  full_acceptance_passed=False, command=sys.argv,
                  matrix={f'AC-045-{n:02}': {'status': 'unverified'} for n in range(1, 13)},
                  limits=['CPU accuracy: recorded macOS arm64 host, SIFT1M/NSG and resident worker/slots/admission 1/1/1',
                          'SSD, PCIe and Controller: configured_unvalidated',
                          'MQSim oracle: common ordinary-device fields only',
                          'No ASan leak coverage; other OS/architectures unverified'])
    try:
        verify(args.evidence.resolve(), args.clean.resolve(), args.workspace.resolve(),
               args.formal_root.resolve(), report)
        require(all(row['status'] == 'passed' for row in report['matrix'].values()), 'incomplete AC matrix')
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.CalledProcessError) as error:
        report['failure'] = str(error)
        print(error, file=sys.stderr)
    else:
        report['full_acceptance_passed'] = True
    report['exit_code'] = 0 if report['full_acceptance_passed'] else 2
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
