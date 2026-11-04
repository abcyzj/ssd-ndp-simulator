"""Freeze the V04-3b CPU Profile directly from locked tuning measurements."""
import argparse
import math
import platform
import sys
from pathlib import Path

from fit_v04_cpu_profile import FEATURES, RULE as BASE_RULE, estimate, fit_stage, linear, metrics, ranges, read_training
from v04_contract import FEATURE_UNITS, ROOT, file_hash, require, validate_profile
from ann_data import write_json

OPERATOR = 'MERGE_CANDIDATES'
PHASES = ('merge_append', 'merge_finalize')
RULE = ('Fit one nonnegative linear model per native merge interval on tuning32. '
        'Use four original-query-ID folds and compare on their common in-range holdout.')


def cart(x, residual, indices, depth, leaf):
    mean = math.fsum(residual[i] for i in indices) / len(indices)
    if depth == 0 or len(indices) < 2 * leaf:
        return mean
    best = None
    best_gain = 1e-16
    for feature in range(len(x[0])):
        ordered = sorted(indices, key=lambda i: x[i][feature])
        left_sum = 0.
        for position, i in enumerate(ordered[:-1], 1):
            left_sum += residual[i]
            right_count = len(indices) - position
            if position < leaf or right_count < leaf or x[i][feature] == x[ordered[position]][feature]:
                continue
            gain = (left_sum - position * mean)**2 * len(indices) / (position * right_count)
            if gain > best_gain:
                best_gain = gain
                best = (feature, x[i][feature], ordered[:position], ordered[position:])
    if best is None:
        return mean
    feature, threshold, left, right = best
    return [feature, threshold, cart(x, residual, left, depth-1, leaf),
            cart(x, residual, right, depth-1, leaf)]

def predict_tree(tree, x):
    while isinstance(tree, list):
        feature, threshold, left, right = tree
        tree = left if x[feature] <= threshold else right
    return tree

def fit_dedup(rows):
    depth, leaf, count, rate = 3, 12, 180, .08
    x = [r['values'] for r in rows]
    y = [math.log(r['ns']) for r in rows]
    initial = math.fsum(y) / len(y)
    predictions = [initial] * len(y)
    trees = []
    for _ in range(count):
        residual = [actual - predicted for actual, predicted in zip(y, predictions)]
        tree = cart(x, residual, list(range(len(y))), depth, leaf)
        trees.append(tree)
        predictions = [p + rate * predict_tree(tree, v) for p, v in zip(predictions, x)]
    return dict(kind='log_gbdt', initial=initial, rate=rate,
                trees=[flatten_tree(tree, []) for tree in trees])

def flatten_tree(tree, nodes):
    if isinstance(tree, list):
        feature, threshold, left, right = tree
        split = dict(feature=feature, threshold=threshold)
        nodes.append(split)
        flatten_tree(left, nodes)
        split['right'] = len(nodes)
        flatten_tree(right, nodes)
    else:
        nodes.append(tree)
    return nodes


def freeze(args):
    conditions, tuning, evaluation, receipts, stages, classification, hashes = read_training(
        args.training_freeze, args.training_sha256, args.start_manifest, args.start_sha256)
    rows = stages['merge']
    require({r['merge_phase'] for r in rows} == set(PHASES) and
            {r['operator'] for r in rows} == {OPERATOR}, 'incomplete merge phase coverage')
    fold_ids = {qid: index % 4 for index, qid in enumerate(tuning)}
    phase_models, stage_reports, held_out = [], [], []
    feature_names = FEATURES['merge']
    for phase in PHASES:
        all_phase = [row for row in rows if row['merge_phase'] == phase]
        usable = [row for row in all_phase if row['usable']]
        predictions, folds = [], []
        for fold in range(4):
            train = [r for r in usable if fold_ids[r['query']] != fold]
            test = [r for r in usable if fold_ids[r['query']] == fold]
            require(train and test, f'{phase}: empty fold {fold}')
            limits = ranges(train)
            eligible = [r for r in test if all(lo <= v <= hi for v, (lo, hi) in zip(r['values'], limits))]
            model = linear(train)
            predictions.extend((r, estimate(model, r['values'])) for r in eligible)
            folds.append(dict(fold=fold, held_out_query_ids=[q for q in tuning if fold_ids[q] == fold],
                              in_range=len(eligible), out_of_range=len(test) - len(eligible),
                              train_ranges=dict(zip(feature_names, limits))))
        held_out.extend(predictions)
        features = [dict(name=name, unit=FEATURE_UNITS[name], minimum=lo, maximum=hi)
                    for name, (lo, hi) in zip(feature_names, ranges(usable))]
        model = linear(usable)
        errors = metrics([(r, estimate(model, r['values'])) for r in usable])
        phase_models.append(dict(operator=OPERATOR, stage=phase, features=features, model=model,
                                 samples=len(usable), selection_rule=RULE,
                                 error={k: errors[k] for k in ('mae_ns', 'relative_p50', 'relative_p95')}))
        stage_reports.append(dict(operator=OPERATOR, stage=phase, candidate_features=features,
                                  selected='linear', model=model, folds=folds, training=errors,
                                  classification=classification['buckets']['merge'],
                                  holdout={'linear': metrics(predictions)},
                                  quality=dict(total=len(all_phase), usable=len(usable),
                                               low_signal=len(all_phase) - len(usable),
                                               short_total=sum(r['short'] for r in all_phase))))

    dedup = [r for r in stages['dedup'] if r['usable']]
    dedup_rule = 'Frozen shallow log CART boosting, 180 trees, depth 3, min leaf 12, rate 0.08.'
    dedup_features = [dict(name=name, unit=FEATURE_UNITS[name], minimum=lo, maximum=hi)
                      for name, (lo, hi) in zip(FEATURES['dedup'], ranges(dedup))]
    dedup_predictions, dedup_folds = [], []
    for fold in range(4):
        train = [r for r in dedup if fold_ids[r['query']] != fold]
        test = [r for r in dedup if fold_ids[r['query']] == fold]
        limits = ranges(train)
        eligible = [r for r in test if all(lo <= v <= hi for v, (lo, hi) in zip(r['values'], limits))]
        model = fit_dedup(train)
        dedup_predictions.extend((r, estimate(model, r['values'])) for r in eligible)
        dedup_folds.append(dict(fold=fold, held_out_query_ids=[q for q in tuning if fold_ids[q] == fold],
                                in_range=len(eligible), out_of_range=len(test) - len(eligible),
                                train_ranges=dict(zip(FEATURES['dedup'], limits))))
    model = fit_dedup(dedup)
    errors = metrics([(r, estimate(model, r['values'])) for r in dedup])
    entry = dict(operator='DEDUP_NEIGHBORS', stage='dedup', features=dedup_features, model=model,
                 samples=len(dedup), selection_rule=dedup_rule,
                 error={k: errors[k] for k in ('mae_ns', 'relative_p50', 'relative_p95')})
    dedup_report = dict(operator=entry['operator'], stage='dedup', candidate_features=dedup_features,
                        selected='log_gbdt', model=model, training=errors, folds=dedup_folds,
                        holdout={'log_gbdt': metrics(dedup_predictions)},
                        classification=classification['buckets']['dedup'],
                        quality=dict(total=len(stages['dedup']), usable=len(dedup),
                                     low_signal=len(stages['dedup']) - len(dedup),
                                     short_total=sum(r['short'] for r in stages['dedup'])))
    models, reports = [], []
    for stage in FEATURES:
        if stage == 'merge':
            models.extend(phase_models)
            reports.extend(stage_reports)
            continue
        if stage == 'dedup':
            fitted, stage_report = entry, dedup_report
        else:
            fitted, stage_report = fit_stage(stage, stages[stage], tuning, classification['buckets'][stage])
        models.append(fitted)
        reports.append(stage_report)
    tool_files = ('tools/fit_v04_merge_profile.py', 'tools/fit_v04_cpu_profile.py', 'tools/v04_contract.py',
                  'tools/ann_data.py', 'schemas/ndpsim-measurement-v1.schema.json',
                  'schemas/ndpsim-cpu-profile-v1.schema.json', 'deps.lock.json')
    provenance = dict(training_freeze_sha256=args.training_sha256, start_manifest_sha256=args.start_sha256,
                      python=dict(version=platform.python_version(), binary_sha256=file_hash(Path(sys.executable))),
                      training_inputs=hashes,
                      tool_files_sha256={name: file_hash(ROOT/name) for name in tool_files},
                      evaluation_disclosure='Prior evaluation128 results known before dedup model development; '
                                            'no evaluation measurements used by this freezer.')
    short = metrics([(r, p) for r, p in held_out if r['short']])
    report = dict(format='ndpsim-v04-fit-report/1', provenance=provenance, applicability=conditions,
                  split=dict(tuning_ids=tuning, evaluation_ids=evaluation, overlap=[]),
                  input_measurements=receipts, selection_rule=' '.join((BASE_RULE, RULE, dedup_rule)),
                  classification_rule=classification['rule'], stages=reports,
                  merge_holdout=dict(overall=metrics(held_out),
                                     short={k: v for k, v in short.items() if not k.startswith('relative_')}))
    profile = dict(format='ndpsim-cpu-profile/3', unit='ns', rounding='ceil-u64',
                   applicability=conditions, models=models,
                   training=dict(split='tuning32', query_ids=tuning,
                                 measurement_sha256=[r['measurement_sha256'] for r in receipts],
                                 fit_report_sha256='0' * 64))
    validate_profile(profile)
    args.output.mkdir(parents=True)
    report_path = args.output / 'fit-report.json'
    write_json(report_path, report)
    profile['training']['fit_report_sha256'] = file_hash(report_path)
    write_json(args.output / 'cpu-profile.json', profile)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('training-freeze', 'start-manifest', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('training', 'start'):
        parser.add_argument('--' + name + '-sha256', required=True)
    freeze(parser.parse_args())
