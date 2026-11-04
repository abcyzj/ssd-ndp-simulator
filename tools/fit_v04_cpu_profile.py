"""Fit and freeze the V04 resident, single-worker CPU Profile from V04-2c evidence."""
import argparse
from collections import defaultdict
import math
from pathlib import Path
import platform
from statistics import mean, median
import sys

from ann_data import write_json
from v04_contract import (FEATURE_UNITS, ROOT, STAGES, decode, digest, file_hash, require,
                          validate_measurement, validate_profile)

FEATURES = {
    'init': ('copied_bytes',),
    'score_prepare': ('vectors', 'dimensions'),
    'score_l2': ('vectors', 'dimensions'),
    'merge': ('comparisons', 'old_scores', 'new_scores'),
    'select': ('candidate_checks', 'old_scores'),
    'adjacency_prepare': ('edges',),
    'dedup': ('comparisons', 'edges', 'scored'),
    'finish': ('objects',),
}
POINTS = ((32, 64, 8), (64, 128, 8), (128, 256, 8), (256, 512, 8), (16, 32, 1))
KINDS = ('constant', 'linear', 'table')
RULE = ('Four folds by frozen original ID order modulo four. Minimize pooled held-out MAE '
        'on common in-range usable rows. Tie order constant, linear, table. Refit on all usable rows.')
SELECTION = 'configs/benchmarks/v0.2-query-selection.json'


def read(path, expected=None):
    raw = path.read_bytes()
    if expected is not None:
        require(digest(raw) == expected, f'{path}: SHA256 mismatch')
    return decode(raw)


def split_ids(selection, groups, query_ids):
    tuning, evaluation = (selection[name + '_ids'] for name in ('tuning', 'evaluation'))
    require(tuning == groups['tuning'] == query_ids and evaluation == groups['evaluation'] and
            len(set(tuning)) == 32 == len(tuning) and len(set(evaluation)) == 128 == len(evaluation)
            and not set(tuning).intersection(evaluation), 'invalid original query ID split')
    return tuning, evaluation


def read_training(freeze_path, freeze_sha, start_path, start_sha):
    freeze = read(freeze_path, freeze_sha)
    require(freeze['format'] == 'ndpsim-v04-training-freeze/1', 'wrong training freeze version')
    evidence = freeze_path.parent / freeze['evidence_dir']
    capture = read(evidence/'manifest.json', freeze['capture_manifest_sha256'])
    require(capture['task'] == 'V04-2c' and capture['passed'] is True and
            capture['exit_code'] == 0, 'training capture failed')
    artifacts = capture['artifacts_sha256']
    dataset = read(evidence/freeze['dataset'], freeze['dataset_sha256'])
    classification = read(freeze_path.parent/freeze['classification'], freeze['classification_sha256'])
    inputs = read(evidence/'inputs.json', dataset['inputs_sha256'])
    start = read(start_path, start_sha)
    require(start['passed'] is True, 'V04-0 freeze failed')
    start_files = start['artifacts_sha256']
    frozen_inputs = read(start_path.parent/'inputs.json', start_files['inputs.json'])
    environment = read(start_path.parent/'environment.json', start_files['environment.json'])
    selection = read(ROOT/SELECTION, inputs['locks'][SELECTION])['datasets']['sift1m']
    tuning, evaluation = split_ids(selection, inputs['query_groups'], dataset['query_ids'])
    require(all(inputs[k] == frozen_inputs[k] for k in ('locks', 'query_groups')) and
            inputs['preparation']['files_sha256'] == frozen_inputs['preparation']['files_sha256'],
            'capture input differs from V04-0')
    require(dataset['format'] == 'ndpsim-v04-training-dataset/1' and dataset['split'] == 'tuning32'
            and artifacts[freeze['dataset']] == freeze['dataset_sha256']
            and artifacts['inputs.json'] == dataset['inputs_sha256']
            and artifacts['classification.json'] == dataset['classification_sha256'] ==
            freeze['original_classification_sha256'], 'dataset provenance mismatch')
    require([(*p['point'], p['stride']) for p in dataset['points']] == list(POINTS),
            'missing, duplicated or unexpected training point')

    aba_hash = dataset['aba_sha256']
    require(artifacts['aba.json'] == aba_hash and read(evidence/'aba.json', aba_hash)['passed'] is True,
            'missing passing A/B/A capture evidence')
    by_stage = defaultdict(list)
    applicability = None
    receipts = []
    payloads = inputs['preparation']['files_sha256']
    for point, (width, budget, stride) in zip(dataset['points'], POINTS):
        label = f'l{width}-e{budget}'
        directory = label + '-cpu'
        path = directory + '/measurement.json'
        require(point['measurement'] == path and point['measurement_sha256'] == artifacts[path]
                and point['config_sha256'] == artifacts[label + '.json']
                and point['supplement'] == (stride == 1), 'training point identity mismatch')
        manifest_path = directory + '/manifest.json'
        run = read(evidence/manifest_path, artifacts[manifest_path])
        record = read(evidence/path, artifacts[path])
        validation = validate_measurement(record)
        require(all(point['validation'][k] == v for k, v in validation.items()),
                f'{label}: measurement validation differs from capture')
        identity = record['identity']
        config = read(evidence/(label + '.json'), point['config_sha256'])['search']
        require(identity['query_ids'] == tuning and identity['search'] ==
                dict(L=width, E=budget, B=1, k=10, chunk=16, entry=config['entry'])
                and (config['width'], config['budget'], config['batch'], config['k'], config['chunk'])
                == (width, budget, 1, 10, 16), f'{label}: wrong query/search identity')
        require({k: obj['sha256'] for k, obj in identity['objects'].items()} ==
                {k: payloads[v] for k, v in (('base', 'tuning/base.f32'),
                 ('queries', 'tuning/query.f32'), ('graph', 'index/adjacency.u32'))},
                f'{label}: wrong payload identity')
        require(record['complete'] is True and record['dropped'] == 0 and
                record['split'] == 'tuning32' and record['mode'] == 'cpu_sample' and
                record['workers'] == record['max_inflight'] == 1 and
                record['preparation_context'] == 'resident_random' and
                record['sample_stride'] == stride and record['build'] == dataset['build'] ==
                run['build'] and record['cpu_clock']['source'] == 'CLOCK_THREAD_CPUTIME_ID' and
                run['passed'] is True and run['exit_code'] == 0 and run['paging_eligible'] is True
                and run['observation_complete'] is True and run['config_sha256'] == point['config_sha256']
                and run['artifacts_sha256']['measurement.json'] == artifacts[path],
                f'{label}: incomplete or incompatible CPU measurement')
        hardware = environment['hardware']
        base = identity['objects']['base']
        conditions = dict(hardware=hardware['machdep.cpu.brand_string'], os=run['platform'],
                          memory_bytes=int(hardware['hw.memsize']), frequency=run['frequency'],
                          affinity=run['affinity'], build=record['build'], dimensions=base['width'],
                          records=base['records'], preparation_context=record['preparation_context'],
                          workers=record['workers'])
        require(run['platform'] == environment['platform'] and
                (conditions['dimensions'], conditions['records']) == (128, 1_000_000) and
                (applicability is None or applicability == conditions),
                f'{label}: incompatible applicability')
        applicability = conditions
        receipts.append(dict(point=label, measurement=path, measurement_sha256=artifacts[path],
                             config_sha256=point['config_sha256'],
                             manifest_sha256=artifacts[manifest_path], validation=validation,
                             stride=stride, supplement=point['supplement'],
                             cpu_clock=record['cpu_clock'], empty_probe_count=record['empty_probe_count'],
                             empty_probe_ns=record['empty_probe_ns']))
        for row in record['samples']:
            stage = row['bucket']
            require(stage in FEATURES and row['calls'] == 1, 'unsupported training sample shape')
            features = row['work'] | row['features']
            values = tuple(features[name] for name in FEATURES[stage])
            threshold = 4 * max(record['cpu_clock']['resolution_ns'], row['overhead_ns'])
            by_stage[stage].append(dict(values=values, ns=row['corrected_ns'], point=label,
                                        query=row['query'], usable=row['quality'] == 'usable',
                                        short=row['corrected_ns'] < threshold,
                                        operator=row['operator']))
    require(by_stage.keys() == FEATURES.keys() == classification['buckets'].keys(),
            'incomplete resident stage coverage')
    identity_hashes = {name: freeze[name] for name in
                       ('capture_manifest_sha256', 'dataset_sha256', 'classification_sha256')}
    identity_hashes.update(inputs_sha256=dataset['inputs_sha256'], aba_sha256=aba_hash,
                           query_selection_sha256=inputs['locks'][SELECTION])
    return applicability, tuning, evaluation, receipts, by_stage, classification, identity_hashes


def quantile(values, fraction):
    """Nearest-rank quantile of a nonempty sample."""
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def linear(rows):
    """Fixed-order projected coordinate descent on a scaled least-squares Gram matrix."""
    scale = [max(1, max(r['values'][i] for r in rows)) for i in range(len(rows[0]['values']))]
    design = [(1.0, *(v/s for v, s in zip(r['values'], scale))) for r in rows]
    columns = list(zip(*design))
    target = [r['ns'] for r in rows]
    gram = [[math.fsum(x*y for x, y in zip(a, b)) for b in columns] for a in columns]
    rhs = [math.fsum(x*y for x, y in zip(a, target)) for a in columns]
    coefficients = [0.0] * len(columns)
    for _ in range(512):
        for i, column in enumerate(gram):
            if column[i]:
                residual = rhs[i] - sum(column[j] * coefficients[j]
                                        for j in range(len(columns)) if j != i)
                coefficients[i] = max(0.0, residual / column[i])
    return dict(kind='linear', intercept_ns=coefficients[0],
                coefficients=[c/s for c, s in zip(coefficients[1:], scale)])


def fit(kind, rows):
    if kind == 'constant':
        return dict(kind=kind, ns=median(r['ns'] for r in rows))
    if kind == 'linear':
        return linear(rows)
    values = [r['values'][0] for r in rows]
    edges = sorted({quantile(values, fraction) for fraction in (.25, .5, .75, 1)})
    groups = defaultdict(list)
    for row in rows:
        groups[next(edge for edge in edges if row['values'][0] <= edge)].append(row['ns'])
    return dict(kind=kind, bins=[dict(upper=edge, ns=median(groups[edge])) for edge in edges])


def estimate(model, values):
    kind = model['kind']
    if kind == 'constant':
        ns = model['ns']
    elif kind == 'table':
        ns = next(b['ns'] for b in model['bins'] if values[0] <= b['upper'])
    else:
        ns = model['intercept_ns']
        for value, coefficient in zip(values, model['coefficients']):
            ns += float(value) * coefficient
    return math.ceil(ns)


def ranges(rows):
    return [(min(values), max(values)) for values in zip(*(r['values'] for r in rows))]


def metrics(predictions):
    if not predictions:
        return dict(samples=0)
    residual = [pred - row['ns'] for row, pred in predictions]
    absolute = [abs(err) for err in residual]
    relative = [err / row['ns'] for err, (row, _) in zip(absolute, predictions)]
    return dict(samples=len(residual), mae_ns=mean(absolute),
                absolute_p50_ns=quantile(absolute, .5), absolute_p90_ns=quantile(absolute, .9),
                absolute_p95_ns=quantile(absolute, .95),
                relative_p50=quantile(relative, .5), relative_p90=quantile(relative, .9),
                relative_p95=quantile(relative, .95), mean_signed_residual_ns=mean(residual))


def fit_stage(stage, rows, tuning, classification):
    usable = [r for r in rows if r['usable']]
    folds = {qid: i % 4 for i, qid in enumerate(tuning)}
    held_out = {kind: [] for kind in KINDS}
    fold_reports = []
    for fold in range(4):
        train = [r for r in usable if folds[r['query']] != fold]
        test = [r for r in usable if folds[r['query']] == fold]
        require(train and test, f'{stage}: empty training/holdout fold {fold}')
        limits = ranges(train)
        in_domain = [r for r in test if all(lo <= v <= hi for v, (lo, hi) in zip(r['values'], limits))]
        fold_reports.append(dict(fold=fold, held_out_query_ids=[q for q in tuning if folds[q] == fold],
                                 train_samples=len(train), test_samples=len(test),
                                 in_range=len(in_domain), out_of_range=len(test)-len(in_domain),
                                 train_ranges=dict(zip(FEATURES[stage], limits))))
        for kind in KINDS:
            model = fit(kind, train)
            held_out[kind].extend((row, estimate(model, row['values'])) for row in in_domain)
    scores = {kind: metrics(predictions) for kind, predictions in held_out.items()}
    require(scores['constant']['samples'] > 0, f'{stage}: no common in-range holdout samples')
    selected = min(KINDS, key=lambda kind: scores[kind]['mae_ns'])
    model = fit(selected, usable)
    features = [dict(name=name, unit=FEATURE_UNITS[name], minimum=lo, maximum=hi)
                for name, (lo, hi) in zip(FEATURES[stage], ranges(usable))]
    predictions = [(r, estimate(model, r['values'])) for r in usable]
    errors = metrics(predictions)
    bins = sorted({quantile([r['values'][0] for r in usable], p) for p in (.25, .5, .75, 1)})
    by_point, by_shape = defaultdict(list), defaultdict(list)
    for row, pred in predictions:
        by_point[row['point']].append((row, pred))
        by_shape[next(edge for edge in bins if row['values'][0] <= edge)].append((row, pred))
    quality = dict(total=len(rows), usable=len(usable), low_signal=len(rows)-len(usable),
                   short_total=sum(r['short'] for r in rows))
    short_errors = metrics([(r, pred) for r, pred in predictions if r['short']])
    short_errors = {k: v for k, v in short_errors.items() if not k.startswith('relative_')}
    report = dict(stage=stage, operator=classification['operator'], classification=classification,
                  candidate_features=features, selected=selected, model=model, quality=quality,
                  folds=fold_reports, holdout=scores, training=errors, short_training=short_errors,
                  by_point={point: dict(metrics(by_point[point]),
                                        low_signal=sum(not r['usable'] for r in rows if r['point'] == point))
                            for point in dict.fromkeys(r['point'] for r in rows)},
                  by_shape=[dict(upper=edge, **metrics(by_shape[edge])) for edge in bins])
    require({r['operator'] for r in rows} == {report['operator']} and
            stage in STAGES[report['operator']], f'{stage}: classification operator mismatch')
    entry = dict(operator=report['operator'], stage=stage, features=features[:1] if selected == 'table'
                 else features, model=model, samples=len(usable),
                 error={k: errors[k] for k in ('mae_ns', 'relative_p50', 'relative_p95')},
                 selection_rule=RULE)
    return entry, report


def freeze(args):
    applicability, tuning, evaluation, receipts, rows, classification, hashes = read_training(
        args.training_freeze, args.training_sha256, args.start_manifest, args.start_sha256)
    models, reports = zip(*(fit_stage(stage, rows[stage], tuning, classification['buckets'][stage])
                            for stage in FEATURES))
    tool_files = ['tools/fit_v04_cpu_profile.py', 'tools/v04_contract.py', 'tools/ann_data.py',
                  'schemas/ndpsim-measurement-v1.schema.json', 'schemas/ndpsim-cpu-profile-v1.schema.json',
                  'deps.lock.json']
    provenance = dict(training_freeze_sha256=args.training_sha256, start_manifest_sha256=args.start_sha256,
                      python=dict(version=platform.python_version(), binary_sha256=file_hash(Path(sys.executable))),
                      training_inputs=hashes,
                      tool_files_sha256={name: file_hash(ROOT/name) for name in tool_files})
    report = dict(format='ndpsim-v04-fit-report/1', provenance=provenance, applicability=applicability,
                  split=dict(tuning_ids=tuning, evaluation_ids=evaluation, overlap=[]),
                  input_measurements=receipts, selection_rule=RULE,
                  candidate_rules=dict(constant='median corrected ns',
                                       linear='512 scaled projected coordinate sweeps, squared loss',
                                       table='median corrected ns by first feature nearest-rank quartile bins'),
                  classification_rule=classification['rule'], stages=reports)
    write_json(args.output/'fit-report.json', report)
    profile = dict(format='ndpsim-cpu-profile/1', unit='ns', rounding='ceil-u64',
                   applicability=applicability, models=list(models),
                   training=dict(split='tuning32', query_ids=tuning,
                                 measurement_sha256=[p['measurement_sha256'] for p in receipts],
                                 fit_report_sha256=file_hash(args.output/'fit-report.json')))
    validation = validate_profile(profile)
    write_json(args.output/'cpu-profile.json', profile)
    lines = ['# V04-3b tuning32 CPU Profile', '', RULE, '',
             'Training errors below are measured on the final refit. Evaluation128 IDs were used only for split checks.',
             '', '| Stage | Model | Usable / total | Holdout outside common range | Training MAE (ns) | Relative P50 / P90 | Short MAE (ns) |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for row in reports:
        quality, error = row['quality'], row['training']
        short = row['short_training'].get('mae_ns')
        short_display = f'{short:.1f}' if short is not None else '—'
        outside = sum(f['out_of_range'] for f in row['folds'])
        lines.append(f"| {row['stage']} | {row['selected']} | {quality['usable']} / {quality['total']} | "
                     f"{outside} | {error['mae_ns']:.1f} | {error['relative_p50']:.1%} / "
                     f"{error['relative_p90']:.1%} | {short_display} |")
    lines.extend(['', 'See fit-report.json for per-point and per-shape residuals, sample quality and candidate errors.',
                  'Level A and AC-04-04 remain open. Runtime applicability and observed feature ranges are binding.', ''])
    (args.output/'fit-report.md').write_text('\n'.join(lines))
    write_json(args.output/'fit-freeze.json', dict(format='ndpsim-v04-profile-freeze/1', provenance=provenance,
               artifacts_sha256={name: file_hash(args.output/name) for name in
                                 ('cpu-profile.json', 'fit-report.json', 'fit-report.md')},
               assertions=dict(profile_validation=validation, training_evaluation_overlap=0,
                               measurement_validations=len(receipts)), level_a='not_run'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('training-freeze', 'start-manifest', 'output'):
        parser.add_argument('--' + option, type=Path, required=True)
    for option in ('training-sha256', 'start-sha256'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    try:
        args.output.mkdir(parents=True)
        freeze(args)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'fit failed: {exc}\n')


if __name__ == '__main__':
    main()
