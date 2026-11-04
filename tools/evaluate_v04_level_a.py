"""Evaluate the frozen V0.4 CPU model against separate native CPU sampling runs."""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import csv
import json
from math import ceil
from pathlib import Path
from statistics import mean, median
import sys

from ann_data import write_json
from collect_v04_training import POINTS
from native_observation import FEATURES, binding_for, records
from compare_reference_paths import chunks, oracle, score_pairs
from validate_v04 import (artifact, expected_operations, native_run, read,
                          same, search_config, service_demand)
from simulation_input import FileRows, load_v2
from v04_contract import ROOT, WORK, file_hash, require, validate_profile
from verify_m0 import inventory

PROFILE_SHA = '1f52e9333a501a2f078504cf4794099712dd8bdf79c2666e70d8d5b5ef3e66ec'
START_SHA = 'f5f461cb2edc584d5e5b27bb89882c9c26cf7245aa333d7a47189de6e8606110'
REPEATS = 3
STRIDE = 16
LIMITS = dict(relative_p50=.10, relative_p90=.20)


def checked(path, sha):
    same(file_hash(path), sha, str(path), 'SHA256')
    return read(path)


def distributions(rows, relative=False):
    if relative:
        errors = [abs(row['residual_ns']) / row['measured_ns'] for row in rows
                  if row['residual_ns'] is not None and not row['short']]
    else:
        errors = [row['residual_ns'] for row in rows if row['residual_ns'] is not None]
    if not errors:
        return dict(samples=0)
    absolute = sorted(map(abs, errors))
    prefix = 'relative' if relative else 'absolute'
    result = {f'{prefix}_p{p}': absolute[ceil(len(absolute)*p/100)-1] for p in (50, 90, 95)}
    result['samples'] = len(errors)
    if not relative:
        result.update(mae_ns=mean(absolute), mean_signed_residual_ns=mean(errors))
    return result


def summary(rows):
    short = [r for r in rows if r['short']]
    return dict(total=len(rows), low_signal=sum(r['low_signal'] for r in rows),
                short_samples=len(short), unsupported=sum(r['predicted_ns'] is None for r in rows),
                corrected_cpu_ns=sum(max(r['measured_ns'], 0) for r in rows),
                absolute=distributions(rows), short_absolute=distributions(short),
                relative=distributions(rows, relative=True))


def accuracy(group):
    return all(group['relative'].get(key, float('inf')) <= limit for key, limit in LIMITS.items())


def plan_query(vectors, graph, query, query_id, search, dim):
    expected = oracle(vectors, graph, query, search)
    decisions, batches, _, top = expected
    work = Counter()
    stages = []
    for op, (operator, _, _, _, op_work, _, entries) in enumerate(
            expected_operations(expected, graph, search, query_id, dim), 1):
        work.update(op_work)
        for bucket, features, stage_work, chunk, segment in entries:
            append = operator == 'SCORE_L2' and bucket == 'merge'
            stage = ('merge_append' if append else 'merge_finalize') if bucket == 'merge' else bucket
            sample_op = op + int(append)
            sample_operator = 'MERGE_CANDIDATES' if append else operator
            stages.append(((query_id, sample_op, bucket, chunk, segment), sample_operator,
                           operator, stage, {k: features[k] for k in FEATURES[bucket]}, stage_work))
    return stages, (top, sum(len(chosen) for _, chosen in decisions), sum(map(len, batches)),
                    dict(work), len(batches), len(chunks(batches, search['chunk'])), len(decisions)-1)


def sample_key(row):
    return tuple(row[k] for k in ('query', 'op', 'bucket', 'chunk', 'segment'))


def planned_sample(row, operator, features, work, key):
    require(row is not None, f'missing CPU sample {key}')
    same((row['operator'], row['features'], row['work'], row['calls']),
         (operator, features, work, 1), str(key), 'sample operator/features/Work/calls')


def assess_point(binding, shape, paths, runs, models, classification, output):
    ids = binding['identity']['query_ids']
    search = search_config(binding['identity'])
    stride = runs[0][1]['sample_stride']
    dim = shape['dimensions']
    samples = [{sample_key(r): r for r in record['samples']} for _, record in runs]
    per_repeat = [[] for _ in runs]
    combined = []
    eligible = Counter()
    stage_calls = Counter()
    shape_calls = Counter()
    unsupported = Counter()
    with ExitStack() as stack:
        failure_stream = stack.enter_context((output/'unsupported.jsonl').open('w'))
        vectors, graph, queries = [
            stack.enter_context(FileRows(paths[key], width, adjacency=adjacency))
            for key, width, adjacency in [('vector_file', dim, False), ('graph_file', shape['degree']+1, True),
                                          ('query_file', dim, False)]]
        for i, query in enumerate(ids):
            stages, result = plan_query(vectors, graph, queries[i], query, search, dim)
            for repeat, (batch, _) in enumerate(runs, 1):
                row = batch[i]
                same((score_pairs(row['top_k']), row['expanded'], row['scored'], row['work'],
                      row['score_batches'], row['score_chunks'], row['expansion_batches']),
                     result, f'query {query} repeat {repeat}', 'independent top-k/counts/Work')
            for key, sample_operator, operator, stage, features, work in stages:
                bucket = key[2]
                sampled = eligible[bucket] % stride == 0
                eligible[bucket] += 1
                stage_calls[stage] += 1
                shape_calls[stage, tuple(features[k] for k in FEATURES[bucket])] += 1
                predicted = None
                try:
                    predicted = service_demand(models, operator, bucket, features, work, str(key))
                except ValueError as exc:
                    unsupported[stage] += 1
                    failure = dict(key=key, stage=stage, sampled=sampled, features=features, work=work, error=str(exc))
                    failure_stream.write(json.dumps(failure, allow_nan=False) + '\n')
                if not sampled:
                    continue
                entry = dict(zip(('query', 'op', 'bucket', 'chunk', 'segment'), key),
                             operator=sample_operator, stage=stage, predicted_ns=predicted)
                shape_keys = FEATURES[bucket]
                entry.update({name: features[name] for name in shape_keys})
                entry.update(comparisons=work['comparisons'], candidate_checks=work['candidate_checks'])
                group = []
                for repeat, ((_, record), by_key, rows) in enumerate(zip(runs, samples, per_repeat), 1):
                    sample = by_key.pop(key, None)
                    planned_sample(sample, sample_operator, features, work, key)
                    resolution = record['cpu_clock']['resolution_ns']
                    threshold = 4 * max(resolution, sample['overhead_ns'])
                    row = timed(entry, sample['corrected_ns'], threshold, sample['quality'] == 'low_signal')
                    rows.append(dict(row, repeat=repeat, raw_ns=sample['raw_ns'], overhead_ns=sample['overhead_ns']))
                    group.append(row)
                combined.append(timed(entry, median(r['measured_ns'] for r in group),
                                      median(r['short_threshold_ns'] for r in group),
                                      sum(r['low_signal'] for r in group) > len(group)//2))
    for repeat, (left, (_, record)) in enumerate(zip(samples, runs), 1):
        require(not left, f'repeat {repeat}: unexpected CPU samples, first {next(iter(left), None)}')
        same(dict(eligible), {r['bucket']: r['eligible_calls'] for r in record['coverage']},
             f'repeat {repeat}', 'independent eligible calls')
    write_residuals(output/'residuals.csv', [*per_repeat, [dict(r, repeat='median') for r in combined]])
    return summarize_point(combined, per_repeat, classification, stage_calls, shape_calls, unsupported)


def timed(entry, measured, threshold, low_signal):
    predicted = entry['predicted_ns']
    return dict(entry, measured_ns=measured, short_threshold_ns=threshold,
                low_signal=low_signal, short=low_signal or measured < threshold,
                residual_ns=None if predicted is None else predicted-measured)


def write_residuals(path, groups):
    fields = sorted({key for group in groups for row in group for key in row})
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for group in groups:
            writer.writerows(group)


def summarize_point(combined, repeats, classification, stage_calls, shape_calls, unsupported):
    stages, operators, shapes = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in combined:
        stages[row['stage']].append(row)
        operators[row['operator']].append(row)
        shapes[row['stage'], tuple(row[k] for k in FEATURES[row['bucket']])].append(row)
    stage_report = {}
    sampled_cpu = sum(max(r['measured_ns'], 0) for r in combined)
    for name, frozen in classification.items():
        result = summary(stages[name])
        status = 'not_applicable'
        if frozen['major'] and result['relative']['samples']:
            status = 'passed' if accuracy(result) else 'failed'
        if unsupported[name] or not stages[name]:
            status = 'failed'
        stage_report[name] = dict(result, classification=frozen, status=status,
                                  eligible_calls=stage_calls[name], unsupported_calls=unsupported[name],
                                  sampled_cpu_fraction=result['corrected_cpu_ns']/sampled_cpu if sampled_cpu else None,
                                  repeats=[summary([r for r in rows if r['stage'] == name]) for rows in repeats])
    operator_report = {}
    for name, rows in operators.items():
        result = summary(rows)
        members = {r['stage'] for r in rows}
        major = any(classification[stage]['major'] for stage in members)
        passed = all(stage_report[stage]['status'] != 'failed' for stage in members)
        status = ('passed' if passed and accuracy(result) else 'failed') if major else 'not_applicable'
        operator_report[name] = dict(result, major=major, status=status)
    failures = [name for name, row in {**stage_report, **operator_report}.items() if row['status'] == 'failed']
    shape_report = []
    for (stage, values), eligible in sorted(shape_calls.items()):
        bucket = 'merge' if stage in ('merge_append', 'merge_finalize') else stage
        rows = shapes[stage, values]
        shape_report.append(dict(stage=stage, features=dict(zip(FEATURES[bucket], values)),
                                 eligible_calls=eligible, sampled=bool(rows), **summary(rows)))
    return dict(stages=stage_report, operators=operator_report, failures=failures,
                shapes=shape_report,
                numeric_gate='failed' if failures else 'passed')


def verify_raw_samples(directory, manifest, record):
    observed = iter(record['samples'])
    for i, query in enumerate(record['identity']['query_ids']):
        path = artifact(directory, manifest, f'native-shards/{i}')
        for bucket, op, chunk, segment, start, end, a, b, work in records(path):
            sample = next(observed, None)
            key = (query, op, bucket, None if chunk < 0 else chunk, segment)
            require(sample is not None, f'{directory}: missing raw sample {key}')
            same((sample_key(sample), sample['start_ns'], sample['end_ns'], sample['features'], sample['work']),
                 (key, start, end, dict(zip(FEATURES[bucket], (a, b))), dict(zip(WORK, work, strict=True))),
                 str(directory), 'raw sample payload')
    require(next(observed, None) is None, f'{directory}: measurement has extra CPU samples')


def evaluate(args, report):
    profile = checked(args.profile, PROFILE_SHA)
    validate_profile(profile)
    training = profile['training']
    fit = checked(args.fit_report, training['fit_report_sha256'])
    start = checked(args.start_manifest, START_SHA)
    frozen = checked(args.start_manifest.parent/'inputs.json', start['artifacts_sha256']['inputs.json'])
    ids = frozen['query_groups']['evaluation']
    same(fit['split'], dict(tuning_ids=training['query_ids'], evaluation_ids=ids, overlap=[]),
         'fit', 'frozen split')
    require(training['query_ids'] == frozen['query_groups']['tuning'] and
            not set(ids) & set(training['query_ids']), 'training/evaluation original query ID overlap')
    report.update(profile_sha256=PROFILE_SHA, fit_report_sha256=training['fit_report_sha256'],
                  start_manifest_sha256=START_SHA, split=fit['split'],
                  evaluation_disclosure=fit['provenance']['evaluation_disclosure'],
                  classification_rule=fit['classification_rule'], applicability=profile['applicability'])
    # This frozen version carries prior exposure to this exact evaluation set.
    report['independence'] = 'diagnostic_prior_evaluation_exposure'
    report['frozen_classification'] = {row['stage']: row['classification'] for row in fit['stages']}
    classification = report['frozen_classification']
    models = {(row['operator'], row['stage']): row for row in profile['models']}
    prepared = args.prepared.resolve()
    locks = frozen['preparation']['files_sha256']
    for name, sha in locks.items():
        if name.startswith(('evaluation/', 'evaluation-truth/', 'index/')):
            same(file_hash(prepared/name), sha, name, 'frozen input SHA256')
    for width, budget in POINTS:
        label = f'l{width}-e{budget}'
        config_path = args.collection/f'{label}.json'
        config = read(config_path)
        same(config['search'], dict(entry=read(prepared/'index/index.json')['entry'],
                                    width=width, budget=budget, k=10, batch=1, chunk=16),
             label, 'search point')
        shape, paths, input_identity = load_v2(prepared/'evaluation', config, config_path.resolve().parent)
        same((shape['dimensions'], shape['count'], shape['queries']),
             (profile['applicability']['dimensions'], profile['applicability']['records'], len(ids)),
             label, 'full-scale shape')
        same(input_identity['directories'], {name: str(prepared/folder) for name, folder in
             [('index', 'index'), ('truth', 'evaluation-truth')]}, label, 'input directories')
        build = profile['applicability']['build']
        files = [(Path(paths[key]), locks[name]) for key, name in
                 [('vector_file', 'evaluation/base.f32'), ('query_file', 'evaluation/query.f32'),
                  ('graph_file', 'index/adjacency.u32')]]
        files.extend((prepared/name, locks[name]) for name in
                     ('evaluation-truth/ids.u32', 'evaluation/query.ids.u32'))
        binding = binding_for(build, files, shape, config['search'])
        same(binding['identity']['query_ids'], ids, label, 'ordered original query IDs')
        point = dict(inputs={}, config_sha256=file_hash(config_path), identity=binding['identity'])
        report['points'][label] = point
        runs, hashes = [], set()
        for repeat in range(1, REPEATS+1):
            directory = args.collection/f'{label}-r{repeat}'
            manifest = read(directory/'manifest.json')
            same(manifest['config_sha256'], point['config_sha256'], label, 'run config SHA256')
            same((manifest['platform'], manifest['frequency'], manifest['affinity']),
                 tuple(profile['applicability'][k] for k in ('os', 'frequency', 'affinity')), label, 'run platform')
            machine = read(args.collection/f'{directory.name}-resources.json')
            same((machine['hardware'], machine['physical_ram_bytes']),
                 tuple(profile['applicability'][k] for k in ('hardware', 'memory_bytes')), label, 'run hardware')
            run_report = dict(inputs={})
            batch, record = native_run(directory, 'cpu_sample', binding, input_identity,
                                       'evaluation128', run_report, stride=STRIDE)
            verify_raw_samples(directory, manifest, record)
            receipt = run_report['inputs']['native cpu_sample']
            require(record['run_id'] == directory.name and receipt['command']['log_sha256'] not in hashes,
                    f'{directory}: reused or mismatched repeat')
            same(file_hash(directory/'process.log'), receipt['command']['log_sha256'], label, 'process log SHA256')
            same(manifest['command'], read(directory/'process.json'), label, 'native process receipt')
            raw = read(directory/'process.log')
            same(raw, read(directory/'batch.json'), label, 'raw native batch')
            for kind in ('wall', 'cpu'):
                same(record[f'{kind}_clock'],
                     dict(source=raw[f'{kind}_clock'], resolution_ns=raw[f'{kind}_resolution_ns']),
                     label, f'raw {kind} clock')
            for key in ('empty_probe_ns', 'empty_probe_count'):
                same(record[key], raw[key], label, f'raw {key}')
            hashes.add(receipt['command']['log_sha256'])
            point['inputs'][directory.name] = dict(receipt, resource_sha256=file_hash(
                args.collection/f'{directory.name}-resources.json'))
            runs.append((batch, record))
        directory = args.output/label
        directory.mkdir()
        point.update(assess_point(binding, shape, paths, runs, models, classification, directory))
    report['numeric_gate'] = 'passed' if all(p['numeric_gate'] == 'passed'
                                             for p in report['points'].values()) else 'failed'


def markdown(report):
    lines = ['# V04-4b Level A', '',
             f"证据状态 **{report['status']}**。数值门槛 **{report['numeric_gate']}**。"
             f"AC-04-04 **{report['ac_04_04']}**。", '']
    if report.get('evaluation_disclosure'):
        lines.extend([report['evaluation_disclosure'], ''])
    if report.get('error'):
        lines.extend([f"失败原因 · {report['error']}", ''])
    for point, result in report['points'].items():
        lines.extend([f'## {point}', '',
                      '| 算子 | 相对 P50 | 相对 P90 | 短样本 | 状态 |',
                      '| --- | --- | --- | --- | --- |'])
        for op, row in result.get('operators', {}).items():
            relative = row['relative']
            p50, p90 = [f'{relative[key]:.2%}' if key in relative else '—' for key in LIMITS]
            lines.append(f"| {op} | {p50} | {p90} | {row['short_samples']} | {row['status']} |")
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('profile', 'fit-report', 'start-manifest', 'prepared', 'collection', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    report = dict(format='ndpsim-v04-level-a/1', argv=sys.argv, status='failed',
                  ac_04_04='not_passed', numeric_gate='not_run', exit_code=1,
                  thresholds=LIMITS, points={}, stride=STRIDE, repeats=REPEATS)
    try:
        write_json(args.output/'sources.json', inventory())
        evaluate(args, report)
        if report['numeric_gate'] == 'passed':
            report.update(status='diagnostic', exit_code=2)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report['error'] = str(exc)
        print(f'Level A: {exc}', file=sys.stderr)
    finally:
        (args.output/'level-a.md').write_text(markdown(report), encoding='utf-8')
        report['artifacts_sha256'] = {str(p.relative_to(args.output)): file_hash(p)
                                      for p in args.output.rglob('*') if p.is_file()}
        write_json(args.output/'level-a.json', report)
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
