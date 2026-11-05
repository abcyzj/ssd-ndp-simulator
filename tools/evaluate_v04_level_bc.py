"""Evaluate a V0.4 resident precision or worker diagnostic point from raw runs."""
import argparse
import csv
from itertools import chain
from pathlib import Path
from statistics import median
import sys

from ann_data import write_json
from evaluate_v04_level_a import START_SHA, checked, verify_raw_samples, verify_raw_timing
from fit_v04_cpu_profile import quantile
from native_observation import graph
from validate_v04 import (artifact, command_ok, level_c_thresholds_met, native_run,
                          read, rows, same, search_config, validate, worker_timeline)
from v04_contract import ROOT, file_hash, require

METRICS = ('qps', 'cpu_ns', 'p50_ns', 'p95_ns', 'window_ns', 'occupancy')
DETERMINISTIC = (
    'effective-config.json', 'replay-preflight.json', 'events.jsonl',
    'results.json', 'queries.csv', 'service.csv', 'top-k.csv',
    'resident_summary.csv', 'worker_summary.csv', 'path_summary.csv', 'lifecycle.csv',
)
BUILD = ('code_commit', 'element_sha256', 'source_sha256', 'mqsim_commit', 'sst_commit',
         'mqsim_native_base', 'mqsim_diff_from_base_sha256', 'mqsim_source_sha256')
NATIVE_TIMING = {
    'worker', 'offered_ns', 'admission_ns', 'worker_start_ns', 'visible_ns',
    'cpu_start_ns', 'thread_cpu_ns', 'latency_ns', 'admission_wait_ns', 'worker_wait_ns',
    'offered_latency_ns',
}


def native_metrics(rows, workers=1):
    window = max(r['visible_ns'] for r in rows) - rows[0]['admission_ns']
    cpu = sum(r['thread_cpu_ns'] for r in rows)
    require(window > 0 and cpu > 0, 'native: nonpositive window or CPU')
    latencies = [r['latency_ns'] for r in rows]
    return dict(qps=len(rows)*1e9/window, cpu_ns=cpu, window_ns=window, occupancy=cpu/(window*workers),
                p50_ns=quantile(latencies, .50), p95_ns=quantile(latencies, .95))


def reduce_metrics(native, replay, *, diagnostic=False):
    center = {key: median(r[key] for r in native) for key in METRICS}
    compared = ('qps', 'cpu_ns', 'p50_ns', 'p95_ns')
    require(all(center[key] > 0 and replay[key] > 0 for key in compared), 'nonpositive comparison metric')
    errors = {key: abs(replay[key]-center[key])/center[key] for key in compared}
    result = dict(native_median=center, replay=replay, relative_error=errors,
                  native_range={key: [min(r[key] for r in native), max(r[key] for r in native)]
                                for key in METRICS},
                  occupancy_delta=replay['occupancy']-center['occupancy'])
    if diagnostic:
        result['replay_minus_native_fraction'] = {key: replay[key]/center[key]-1 for key in compared}
    else:
        result.update(B='passed' if errors['p50_ns'] <= .15 and errors['p95_ns'] <= .20 else 'failed',
                      C='passed' if level_c_thresholds_met(errors) else 'failed')
    return result


def check_repeats(replays, report):
    first = read(replays[0]/'manifest.json')
    first_hashes = {name: file_hash(artifact(replays[0], first, name)) for name in DETERMINISTIC}
    statistics = read(artifact(replays[0], first, 'statistics.json'))
    del statistics['wall_seconds']
    admission = read(artifact(replays[0], first, 'replay-admission.json'))
    del admission['package']  # per-process private snapshot path
    seen = set()
    for directory in replays:
        manifest = read(directory/'manifest.json')
        command = read(artifact(directory, manifest, 'sst-measurement.json'))
        command_ok(command, str(directory))
        same(file_hash(directory/'sst-measurement.log'), command['log_sha256'], directory, 'SST process log')
        require(directory.resolve() not in seen, f'{directory}: repeated replay directory')
        seen.add(directory.resolve())
        fields = (*BUILD, 'config_sha256', 'input_identity', 'replay_tool_sha256', 'replay_config_sha256')
        same([manifest[k] for k in fields], [first[k] for k in fields], directory, 'replay identity')
        same({name: file_hash(artifact(directory, manifest, name)) for name in DETERMINISTIC},
             first_hashes, directory, 'deterministic execution artifacts')
        observed = read(artifact(directory, manifest, 'statistics.json'))
        del observed['wall_seconds']
        same(observed, statistics, directory, 'simulated statistics')
        observed = read(artifact(directory, manifest, 'replay-admission.json'))
        del observed['package']
        same(observed, admission, directory, 'replay admission')
        report['replay_runs'].append(dict(directory=str(directory), process=command,
                                          manifest_sha256=file_hash(directory/'manifest.json')))
    report['deterministic_artifacts_sha256'] = first_hashes


def check_capture(args, binding, identity, report):
    start = checked(args.start_manifest, START_SHA)
    frozen = checked(args.start_manifest.parent/'inputs.json', start['artifacts_sha256']['inputs.json'])
    locks = frozen['preparation']['files_sha256']
    datasets = identity['dataset']['files']
    same((datasets['base']['sha256'], datasets['base']['mapping_sha256'], datasets['query']['sha256'],
          datasets['query']['mapping_sha256'], identity['index']['graph']['sha256'],
          identity['truth']['ids']['sha256']),
         tuple(locks[name] for name in ('evaluation/base.f32', 'evaluation/base.ids.u32', 'evaluation/query.f32',
                                       'evaluation/query.ids.u32', 'index/adjacency.u32',
                                       'evaluation-truth/ids.u32')),
         'capture', 'frozen SIFT1M/NSG payloads')
    manifest = read(args.capture/'manifest.json')
    _, measurement = native_run(args.capture, 'static_capture', binding, identity, 'evaluation128', dict(inputs={}))
    verify_raw_timing(args.capture, manifest, measurement)
    bound = binding['identity']
    source = chain.from_iterable(graph(artifact(args.capture, manifest, f'native-shards/{i}'), bound, q)
                                 for i, q in enumerate(bound['query_ids']))
    count = 0
    for raw, sealed in zip(source, rows(args.capture/'package/operations.jsonl'), strict=True):
        same(raw, sealed, f'capture query {sealed["query"]} op {sealed["op"]}', 'native shard/IR')
        count += 1
    report['capture_provenance'] = dict(start_sha256=START_SHA, raw_operations=count,
                                        manifest_sha256=file_hash(args.capture/'manifest.json'))


def evaluate(args, report):
    args.scope = 'evaluation'
    args.baseline = args.native[0]
    args.baseline_mode = 'benchmark'
    args.replay = args.replays[0]
    binding = read(args.capture/'binding.json')
    search = search_config(binding['identity'])
    point = search['width'], search['budget']
    require(point in {(32, 64), (64, 128)} and
            (search['k'], search['batch'], search['chunk']) == (10, 1, 16), 'invalid formal search point')
    workers = getattr(args, 'workers', 1)
    diagnostic = workers > 1
    require(not diagnostic or point == (64, 128), 'D01/D02 require L/E=64/128')
    report['point'] = {2: 'D01', 4: 'D02'}[workers] if diagnostic else ('A01' if point == (32, 64) else 'A02')
    args.reference_detail8 = point == (32, 64)
    require(bool(args.detail8_references) == (point == (64, 128)),
            'A02/D01/D02 require companion detail8 direct/IR directories; A01 uses detail8 as its reference')
    report['semantic'] = semantic = dict(inputs={}, checks=[], queries=[], metrics={})
    check_repeats(args.replays, report)
    validate(args, semantic)
    identity = read(args.replay/'manifest.json')['input_identity']
    check_capture(args, binding, identity, report)
    sample_paths = set()
    report['table_sample_runs'] = []
    for path in args.merge_append_measurement:
        directory = path.parent
        same(path.name, 'measurement.json', directory, 'measurement filename')
        require(directory.resolve() not in sample_paths, f'{directory}: reused sample run')
        sample_paths.add(directory.resolve())
        evidence = dict(inputs={})
        _, measurement = native_run(directory, 'cpu_sample', binding, identity, 'evaluation128', evidence)
        manifest = read(directory/'manifest.json')
        verify_raw_timing(directory, manifest, measurement)
        verify_raw_samples(directory, manifest, measurement)
        report['table_sample_runs'].append(dict(directory=str(directory),
                                               **evidence['inputs']['native cpu_sample']))
    plain, measurement = native_run(args.uninstrumented, 'uninstrumented', binding, identity,
                                    'evaluation128', dict(inputs={}))
    verify_raw_timing(args.uninstrumented, read(args.uninstrumented/'manifest.json'), measurement)
    expected = [{k: v for k, v in row.items() if k not in NATIVE_TIMING} for row in plain]
    report['uninstrumented_manifest_sha256'] = file_hash(args.uninstrumented/'manifest.json')
    logs = set()
    native_runs = []
    applicability = semantic['inputs']['profile']['applicability']
    quota = dict(workers=workers, concurrency=32) if diagnostic else {}
    for directory in args.native:
        evidence = dict(inputs={})
        rows, measurement = native_run(directory, 'benchmark', binding, identity, 'evaluation128', evidence, **quota)
        manifest = read(directory/'manifest.json')
        same(manifest['platform'], applicability['os'], directory, 'native benchmark OS')
        verify_raw_timing(directory, manifest, measurement)
        log_hash = manifest['command']['log_sha256']
        require(log_hash not in logs, f'{directory}: reused native process log')
        logs.add(log_hash)
        signature = [{k: v for k, v in r.items() if k not in NATIVE_TIMING} for r in rows]
        same(signature, expected, directory, 'native query semantics across repetitions')
        native_runs.append(rows)
        run = dict(directory=str(directory), metrics=native_metrics(rows, workers),
                   evidence=evidence['inputs']['native benchmark'])
        if diagnostic:
            batch = read(directory/'batch.json')
            timeline = worker_timeline(rows, workers, 32, directory)
            same((timeline['drain_end_ns'], run['metrics']['cpu_ns'], run['metrics']['window_ns']),
                 (batch['drain_end_ns'], batch['thread_cpu_ns'], batch['window_ns']), directory, 'native totals')
            same(batch['major_faults'], 0, directory, 'major faults')
            require(0 < run['metrics']['occupancy'] <= 1, f'{directory}: native CPU capacity exceeded')
            timeline['worker_held_ns'] = sum(r['visible_ns']-r['worker_start_ns'] for r in rows)
            timeline['worker_held_minus_cpu_ns'] = timeline['worker_held_ns']-run['metrics']['cpu_ns']
            run.update(timeline=timeline, paging={k: batch[k] for k in ('minor_faults', 'major_faults')},
                       environment={k: manifest[k] for k in ('platform', 'frequency', 'affinity', 'residency',
                                    'pressure_before_process', 'pressure_after_process')},
                       worker_assignments=[r['worker'] for r in rows])
        report['native_runs'].append(run)
    for i, row in enumerate(semantic['queries']):
        for field, native_field in [('cpu', 'thread_cpu_ns'), ('latency', 'latency_ns')]:
            values = [run[i][native_field] for run in native_runs]
            center = median(values)
            row[f'native_{field}_repeats_ns'] = values
            row[f'native_{field}_median_ns'] = center
            delta = row[f'replay_{field}_ns'] - center
            row[f'{field}_delta_from_median_ns'] = delta
            row[f'{field}_relative_delta'] = delta/center if center else None
    report['metrics'] = reduce_metrics([r['metrics'] for r in report['native_runs']],
                                       semantic['metrics']['replay'], diagnostic=diagnostic)
    if diagnostic:
        result = read(args.replay/'results.json')
        for i, row in enumerate(semantic['queries']):
            replay_row = result['queries'][i]
            keys = ('worker', 'admission_ns', 'worker_start_ns', 'visible_ns', 'worker_wait_ns', 'admission_wait_ns')
            row['replay_timeline'] = {key: replay_row[key] for key in keys}
            row['native_timelines'] = [{key: run[i][key] for key in keys} for run in native_runs]
            row['decision_digest_fnv1a64'] = replay_row['decision_digest_fnv1a64']
        report['limitations'] = [
            'The replay uses frozen single-worker service demands and evaluation-bound merge_append calls.',
            'Cache and memory contention, OS preemption, frequency changes and heterogeneous core placement '
            'are not modeled. CPU deltas are aggregate observations without cache or scheduler attribution.',
            'Native and replay FIFO ownership assignments may differ. No precision thresholds apply.',
        ]
        return True
    return all(report['metrics'][key] == 'passed' for key in ('B', 'C'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'samples', 'profile', 'fit-report', 'start-manifest', 'uninstrumented', 'direct', 'ir', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name, count in [('native', 3), ('replays', 2), ('merge-append-measurement', 3)]:
        parser.add_argument('--'+name, type=Path, nargs=count, required=True)
    parser.add_argument('--detail8-references', nargs=2, type=Path, metavar=('DIRECT', 'IR'))
    parser.add_argument('--workers', type=int, choices=(1, 2, 4), default=1)
    args = parser.parse_args()
    diagnostic = args.workers > 1
    stem = 'workers' if diagnostic else 'level-bc'
    args.output.mkdir(parents=True)
    sources = ('tools/evaluate_v04_level_bc.py', 'tools/validate_v04.py', 'tools/compare_reference_paths.py',
               'tools/v04_contract.py', 'tools/native_observation.py', 'tools/bind_v04_merge_append.py',
               'tools/evaluate_v04_level_a.py', 'docs/prd-v0.4.md', 'docs/validation-matrix-v0.4.md')
    report = dict(format=f'ndpsim-v04-{stem}/1', command=sys.argv, native_runs=[], replay_runs=[],
                  source_sha256={name: file_hash(ROOT/name) for name in sources},
                  status='evidence_failed', exit_code=1,
                  evaluation_exposure='evaluation128 measured merge_append input and prior Level A exposure; '
                                      'frozen model fitted only on tuning32')
    if diagnostic:
        report.update(scope='AC-04-07 diagnostic', workers=args.workers, cpu_slots=args.workers, concurrency=32)
    try:
        passed = evaluate(args, report)
        detail = report['semantic']['queries'][:8]
        write_json(args.output/'detail8.json', dict(queries=detail, scope='subset of full evaluation'))
        with (args.output/'queries.csv').open('w', newline='') as stream:
            records = report['semantic']['queries']
            fields = [key for key in records[0] if key not in
                      ('top_k', 'work', 'reference_work', 'native_timelines', 'replay_timeline')]
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(records)
        report.update(status='passed' if passed else 'precision_failed', exit_code=int(not passed))
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        report['error'] = str(exc) or type(exc).__name__
    write_json(args.output/f'{stem}.json', report)
    title = 'Worker diagnostic' if diagnostic else 'Level B/C'
    lines = [f'# V0.4 {title} {report.get("point", "")}', '', f'Status `{report["status"]}`', '']
    if report.get('error'):
        lines.extend([report['error'], ''])
    if semantic := report.get('semantic'):
        lines.extend(f'- {check["name"]} · {check["status"]}' for check in semantic['checks'])
        lines.append('')
    if metrics := report.get('metrics'):
        if not diagnostic:
            lines.extend([f'Level B `{metrics["B"]}` · Level C `{metrics["C"]}`', ''])
        lines.extend([
                      '| Metric | Native median | Replay | Relative error |',
                      '| --- | ---: | ---: | ---: |'])
        for key, error in metrics['relative_error'].items():
            lines.append(f'| {key} | {metrics["native_median"][key]:.6g} | '
                         f'{metrics["replay"][key]:.6g} | {error:.2%} |')
        lines.extend(['', f'Occupancy delta {metrics["occupancy_delta"]:+.2%}', ''])
    if diagnostic and 'metrics' in report:
        lines.extend(['| Run | QPS | P50 ns | P95 ns | CPU ns | Occupancy | Worker wait ns | Admission wait ns |',
                      '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |'])
        runs = [(Path(r['directory']).name, r['metrics'], r['timeline']) for r in report['native_runs']]
        runs.append(('SST replay', metrics['replay'], semantic['worker_resources']['timeline']))
        for name, timing, timeline in runs:
            lines.append(f'| {name} | {timing["qps"]:.3f} | {timing["p50_ns"]} | {timing["p95_ns"]} | '
                         f'{timing["cpu_ns"]} | {timing["occupancy"]:.2%} | {timeline["worker_wait_ns"]} | '
                         f'{timeline["admission_wait_ns"]} |')
        lines.append('')
    lines.extend(f'- {limit}' for limit in report.get('limitations', []))
    lines.extend(['', report['evaluation_exposure'], '',
                  'SSD, PCIe and Controller remain configured_unvalidated.', ''])
    (args.output/f'{stem}.md').write_text('\n'.join(lines))
    print(report['status'], args.output/f'{stem}.json')
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
