"""Validate one V0.4 resident replay point against raw native and reference evidence."""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from pathlib import Path
import struct
import sys

from ann_data import write_json
from bind_v04_merge_append import build_table
from compare_reference_paths import (FileRows, check_chunks, check_sim, chunks, oracle, score_pairs,
                                     score_work, totals)
from fit_v04_cpu_profile import estimate, quantile, SELECTION
from native_observation import FEATURES, compare_inventory
from simulation_input import load_v2
from v04_contract import (ROOT, WORK, counts, decode, digest, file_hash,
                          packed_ids, require, validate_config, validate_measurement,
                          validate_package, validate_profile)

FORMAT = 'ndpsim-validator-report/1'
SEARCH_KEYS = ('entry', 'k', 'width', 'batch', 'budget', 'chunk')
IR_SEARCH_KEYS = ('entry', 'k', 'L', 'B', 'E', 'chunk')


def search_config(identity):
    return {key: identity['search'][field] for key, field in zip(SEARCH_KEYS, IR_SEARCH_KEYS)}


def level_c_thresholds_met(errors):
    return all(errors[key] is not None and errors[key] <= threshold
               for key, threshold in [('qps', .10), ('cpu_ns', .15)])


def read(path):
    return decode(path.read_bytes())


def rows(path):
    with path.open('rb') as stream:
        yield from map(decode, stream)


def same(actual, expected, scope, field):
    if actual != expected:
        raise ValueError(f'{scope}: {field} differs (actual {str(actual)[:240]}, '
                         f'expected {str(expected)[:240]})')


def grouped(path, key):
    result = defaultdict(list)
    for row in rows(path):
        result[row[key]].append(row)
    return result


def packed_scores(scores):
    return b''.join(struct.pack('<Id', *pair) for pair in scores)


def expected_operations(expected, graph, search, query, dim):
    """Project independent search results onto operation boundaries and service stages."""
    decisions, batches, control, top = expected
    scores, control = iter(batches), iter(control)
    version = expanded = 0
    rank, fresh = [], [search['entry']]
    yield 'INIT', [], struct.pack('<Q', version), 1, counts(), [('queries', query, 4*dim)], [
        ('init', dict(copied_bytes=4*dim), counts(), None, 0)]
    for _, chosen in decisions:
        if fresh:
            version += 1
            yield 'MARK_SCORED', fresh, struct.pack('<Q', version), 1, counts(), [], []
            batch = next(scores)
            score_chunks = chunks([batch], search['chunk'])
            stages = []
            for j, chunk in enumerate(score_chunks):
                n = len(chunk)
                features = dict(vectors=n, dimensions=dim)
                stages.extend([
                    ('score_prepare', features, counts(read_bytes=4*n*dim), j, 0),
                    ('score_l2', features, dict(zip(WORK, score_work(dim, n))), j, 0),
                    ('merge', dict(old_scores=0, new_scores=n), counts(), None, j)])
            yield 'SCORE_L2', fresh, packed_scores(batch), len(batch), totals(
                [score_work(dim, len(c)) for c in score_chunks]), [('base', n, 4*dim) for n in fresh], stages
            old = len(rank)
            rank = sorted(rank + batch, key=lambda s: (s[1], s[0]))
            work = dict(zip(WORK, next(control)))
            yield 'MERGE_CANDIDATES', [i for i, _ in rank], packed_scores(rank), len(rank), work, [], [
                ('merge', dict(old_scores=old, new_scores=len(batch)), work, None, len(score_chunks))]
        work = dict(zip(WORK, next(control)))
        yield 'SELECT_FRONTIER', chosen, packed_ids(chosen), len(chosen), work, [], [
            ('select', dict(old_scores=len(rank), expanded=expanded, L=search['width'],
                            B=search['batch'], E=search['budget']), work, None, 0)]
        if not chosen:
            break
        version += 1
        expanded += len(chosen)
        yield 'MARK_EXPANDED', chosen, struct.pack('<Q', version), 1, counts(), [], []
        adjacency = []
        for node in chosen:
            edges = graph[node]
            payload = packed_ids([len(edges), *edges])
            adjacency.append(payload)
            yield 'READ_ADJACENCY', [node], payload, len(edges), counts(), [('graph', node, len(payload))], [
                ('adjacency_prepare', dict(edges=len(edges)), counts(read_bytes=len(payload)), None, 0)]
        yield 'JOIN', chosen, b''.join(adjacency), len(chosen), counts(), [], []
        fresh = sorted({node for parent in chosen for node in graph[parent]} - {i for i, _ in rank})
        work = dict(zip(WORK, next(control)))
        yield 'DEDUP_NEIGHBORS', fresh, packed_ids(fresh), len(fresh), work, [], [
            ('dedup', dict(edges=work['candidate_checks'], scored=len(rank)), work, None, 0)]
    yield 'FINISH', [i for i, _ in top], packed_scores(top), len(top), counts(), [], [
        ('finish', dict(objects=len(top)), counts(), None, 0)]


def service_demand(models, operator, stage, features, work, scope, *, append_key=None):
    key = operator, stage
    if stage == 'merge':
        phase = 'merge_append' if operator == 'SCORE_L2' else 'merge_finalize'
        key = 'MERGE_CANDIDATES', phase
        if phase == 'merge_append' and 'measured_append' in models:
            row = models['measured_append'].get(append_key)
            require(row is not None and features == dict(old_scores=0, new_scores=row['new_scores'])
                    and not any(work.values()), f'{scope}: missing or mismatched measured merge append call')
            return row['ns']
    require(key in models, f'{scope}: missing model {key}')
    model = models[key]
    values = []
    for bound in model['features']:
        name = bound['name']
        available = features | work
        require(name in available and bound['minimum'] <= available[name] <= bound['maximum'],
                f'{scope}: {stage} feature {name} missing or outside model range')
        values.append(available[name])
    return estimate(model['model'], values)


def validate_query(query, ops, events, services, samples, expected, graph, search, dim, models, result):
    scope = f'query {query}'
    by_op = defaultdict(list)
    # Align execution operations by semantic occurrence, then use each log's
    # local handle only to associate its own reads, stages and completion.
    submitted = [event['op'] for event in events if event['type'] == 'replay_submit']
    require(len(submitted) == len(set(submitted)) == len(ops), f'{scope}: submit coverage')
    for event in events:
        same(event['original_query_id'], query, scope, 'original query ID')
        same(event['query'], result['query'], scope, 'runtime query ID')
        by_op[event['op']].append(event)
    samples = {(r['op'], r['bucket'], r['chunk'], r['segment']): r for r in samples}
    require(len(services) == len({s['task'] for s in services}), f'{scope}: duplicate service task')
    tasks = {s['task']: s for s in services}
    require(len(ops) == len(by_op), f'{scope}: operation/event coverage')
    previous = result['worker_start_ns']
    cpu = reads = logical_bytes = 0
    work_total = Counter()
    for row, runtime_op, independent in zip(
            ops, submitted, expected_operations(expected, graph, search, query, dim), strict=True):
        operator, ids, payload, count, work, access, stages = independent
        local = row['op']
        where = f'{scope} batch {row["batch"]} op {local} {operator}'
        output = row['expected']['output']
        sha = digest(payload)
        same(row['operator'], operator, where, 'operator')
        same(row['expected']['ids'], ids, where, 'capture ordered IDs')
        same(row['expected']['work'], work, where, 'capture Work')
        same((output['sha256'], output['count'], output['bytes']), (sha, count, len(payload)),
             where, 'capture value/shape')
        observed = by_op.pop(runtime_op, [])
        read_stage = False
        for event in observed:
            if event['type'] == 'replay_stage':
                stage_time = event['time_ns']
                read_stage = event['stage'] in ('init', 'adjacency_prepare', 'score_prepare')
            elif event['type'] == 'replay_read':
                require(read_stage and event['time_ns'] == stage_time,
                        f'{where}: read outside preparation stage')
        require(all(e['operator'] == operator for e in observed), f'{where}: event operator')
        kinds = [e['type'] for e in observed]
        require(kinds and kinds[0] == 'replay_submit' and kinds[-1] == 'replay_verified' and
                set(kinds[1:-1]) <= {'replay_stage', 'replay_read'}, f'{where}: event sequence')
        verified = observed[-1]
        timestamps = [e['time_ns'] for e in observed]
        require(previous == timestamps[0] and timestamps == sorted(timestamps),
                f'{where}: submit/visibility order')
        same((verified['ids'], verified['work'], verified['sha256']), (ids, work, sha),
             where, 'actual ordered IDs/Work/value')
        read_events = [e for e in observed if e['type'] == 'replay_read']
        same([(e['object'], e['id'], e['bytes']) for e in read_events], access, where, 'actual logical access')
        reads += len(access)
        logical_bytes += sum(a[2] for a in access)
        work_total.update(work)
        stage_events = [e for e in observed if e['type'] == 'replay_stage']
        same([e['stage'] for e in stage_events], [s[0] for s in stages], where, 'service stage sequence')
        for event, (stage, features, stage_work, chunk, segment) in zip(stage_events, stages):
            service = tasks.pop(event['task'], None)
            detail = f'{where} stage {stage} chunk {chunk} segment {segment}'
            require(service is not None, f'{detail}: missing or reused service task')
            same(service['query'], result['query'], detail, 'task query')
            same(service['kind'], 'distance' if stage == 'score_l2' else 'control', detail, 'service kind')
            for field, key in [('flops', 'flops'), ('read_bytes', 'read_bytes'),
                               ('write_bytes', 'write_bytes'), ('checks', 'candidate_checks'),
                               ('comparisons', 'comparisons')]:
                same(service[field], stage_work[key], detail, f'service {field}')
            start, end = service['start_ns'], service['end_ns']
            same((service['submitted_ns'], service['ready_ns'], start, event['time_ns']),
                 (previous,)*4, detail, 'service start/order')
            duration = service_demand(models, operator, stage, features, stage_work, detail,
                                      append_key=(query, local+1, segment))
            same(end-start, duration, detail, 'CPU service demand (ns)')
            cpu += duration
            previous = end
            # Native chunk concatenation is keyed on the following MERGE operation.
            sample_op = local + int(operator == 'SCORE_L2' and stage == 'merge')
            sample = samples.pop((sample_op, stage, chunk, segment), None)
            require(sample is not None, f'{detail}: missing CPU sample')
            same(sample['calls'], 1, detail, 'sample calls')
            same(sample['work'], stage_work, detail, 'sample Work')
            same(sample['features'], {k: features[k] for k in FEATURES[stage]}, detail, 'sample features')
        same(previous, verified['time_ns'], where, 'result visibility')
    require(not by_op and not tasks and not samples, f'{scope}: extra events, service tasks or samples')
    same(previous, result['visible_ns'], scope, 'final visibility')
    return cpu, reads, logical_bytes, dict(work_total)


def artifact(directory, manifest, name):
    path = directory/name
    require(name in manifest['artifacts_sha256'], f'{directory}: missing artifact registration {name}')
    same(file_hash(path), manifest['artifacts_sha256'][name], str(directory), f'{name} SHA256')
    return path


def command_ok(command, scope):
    require(command['passed'] is True and command['exit_code'] == 0 and command['argv'],
            f'{scope}: missing or failed process')


def peak_overlap(intervals):
    # End before start at the same tick. Zero-duration intervals consume no time.
    changes = [(tick, delta) for start, end in intervals if start < end
               for tick, delta in ((start, 1), (end, -1))]
    active = peak = 0
    for _, delta in sorted(changes):
        active += delta
        peak = max(peak, active)
    return peak


def worker_timeline(results, workers, concurrency, scope):
    ends = sorted(r['visible_ns'] for r in results)
    last_start = 0
    worker_end = [0]*workers
    for i, row in enumerate(results):
        offered, admission, start, end = (row[key] for key in
            ('offered_ns', 'admission_ns', 'worker_start_ns', 'visible_ns'))
        worker = row['worker']
        require(0 <= worker < workers and offered == 0 and
                max(admission, worker_end[worker], last_start) <= start <= end,
                f'{scope}: invalid worker ownership or FIFO start at query {i}')
        same(admission, ends[i-concurrency] if i >= concurrency else 0, scope, 'FIFO admission')
        same((row['worker_wait_ns'], row['latency_ns'], row['admission_wait_ns'],
              row['offered_latency_ns']), (start-admission, end-admission, admission, end),
             scope, 'query waiting and latency')
        last_start = start
        worker_end[worker] = end
    peak_admitted = peak_overlap((r['admission_ns'], r['visible_ns']) for r in results)
    peak_workers = peak_overlap((r['worker_start_ns'], r['visible_ns']) for r in results)
    require(peak_admitted <= concurrency and peak_workers <= workers, f'{scope}: query quota exceeded')
    return dict(peak_admitted=peak_admitted, peak_workers=peak_workers, drain_end_ns=ends[-1],
                worker_wait_ns=sum(r['worker_wait_ns'] for r in results),
                admission_wait_ns=sum(r['admission_wait_ns'] for r in results))


def native_run(directory, mode, binding, input_identity, split, report, *, stride=1, workers=1, concurrency=1):
    manifest = read(directory/'manifest.json')
    scope = f'native {mode}'
    report['inputs'][scope] = dict(manifest_sha256=file_hash(directory/'manifest.json'),
                                  command=manifest['command'], build=manifest['build'])
    command_ok(manifest['command'], scope)
    require(manifest['passed'] is True and manifest['exit_code'] == 0 and
            manifest['paging_eligible'] is True and manifest['observation_complete'] is True,
            f'{scope}: incomplete or paging-ineligible run')
    same((manifest['mode'], manifest['split'], manifest['build'], manifest['input']),
         (mode, split, binding['build'], input_identity), scope, 'mode/split/build/input')
    same(manifest['config']['search'], search_config(binding['identity']), scope, 'search configuration')
    if mode == 'static_capture':
        for name in ('binding.json', 'package/manifest.json', 'package/index.jsonl',
                     'package/operations.jsonl', 'package/tail.json'):
            artifact(directory, manifest, name)
    same(manifest['executable_sha256'], binding['build']['binary_sha256'], scope, 'binary identity')
    batch = read(artifact(directory, manifest, 'batch.json'))
    measurement = read(artifact(directory, manifest, 'measurement.json'))
    quality = validate_measurement(measurement)
    same((measurement['mode'], measurement['split'], measurement['build'], measurement['identity']),
         (mode, split, binding['build'], binding['identity']), scope, 'measurement identity')
    same((measurement['workers'], measurement['max_inflight'], batch['workers'], batch['concurrency']),
         (workers, concurrency, workers, concurrency), scope, 'worker/admission quota')
    require(measurement['complete'] is True and measurement['dropped'] == 0 and
            measurement['sample_stride'] == manifest['sample_stride'] == stride and
            measurement['preparation_context'] == 'resident_random' and
            measurement['cpu_clock']['source'] == 'CLOCK_THREAD_CPUTIME_ID' and
            measurement['wall_clock']['source'] == 'CLOCK_MONOTONIC' and batch['paging_eligible'] is True,
            f'{scope}: unsupported or incomplete measurement')
    same(batch['drain_end_ns'], measurement['drain_end_ns'], scope, 'drain end')
    for i, (window, row) in enumerate(zip(measurement['windows'], batch['queries'], strict=True)):
        where = f'{scope} query {window["query"]}'
        same((row['query_id'], row['original_query_id']), (i, window['query']), where, 'query identity')
        keys = ('worker', 'offered_ns', 'admission_ns', 'worker_start_ns', 'visible_ns', 'cpu_start_ns')
        same([row[k] for k in keys], [window[k] for k in keys], where, 'measurement window')
        same(row['thread_cpu_ns'], window['cpu_end_ns']-window['cpu_start_ns'], where, 'thread CPU')
        same(row['latency_ns'], window['visible_ns']-window['admission_ns'], where, 'latency')
    require(quality['attributed_raw_ns'] <= quality['thread_cpu_ns'], f'{scope}: invalid CPU attribution')
    report['inputs'][scope].update(measurement_sha256=file_hash(directory/'measurement.json'),
                                  batch_sha256=file_hash(directory/'batch.json'), sampling=quality)
    return batch['queries'], measurement


def reference(directory, engine, search, identity, element, report, *, subset=None):
    manifest = read(directory/'manifest.json')
    command = read(artifact(directory, manifest, 'sst-measurement.json'))
    command_ok(command, engine)
    same(file_hash(directory/'sst-measurement.log'), command['log_sha256'], engine, 'SST process log')
    report['inputs'][engine] = dict(manifest_sha256=file_hash(directory/'manifest.json'),
                                    command=command, element_sha256=manifest['element_sha256'])
    config = read(artifact(directory, manifest, 'config.json'))
    same((config['execution_engine'], manifest['execution_engine'], config['placement'],
          config['search'], config['max_inflight'], manifest['element_sha256']),
         (engine, engine, 'host', search, 1, element), engine, 'config/build identity')
    if subset is None:
        same(manifest['input_identity'], identity, engine, 'input identity')
        ids = None
    else:
        data = Path(manifest['data'])
        _, paths, ref_identity = load_v2(data, config, directory)
        same(ref_identity, manifest['input_identity'], engine, 'revalidated workload')
        same((ref_identity['dataset']['files']['base'], ref_identity['index']),
             (identity['dataset']['files']['base'], identity['index']), engine, 'base/graph identity')
        mapping = data/ref_identity['dataset']['files']['query']['mapping']
        ids = [q for q, in struct.iter_unpack('<I', mapping.read_bytes())]
        require(ids == list(subset)[:8], f'{engine}: detail8 IDs')
        vectors = Path(paths['query_file']).read_bytes()
        same(vectors, b''.join(subset[q] for q in ids), engine, 'detail8 query bytes')
        report['inputs'][engine]['subset_query_ids'] = ids
    results = read(artifact(directory, manifest, 'results.json'))['queries']
    trace = grouped(artifact(directory, manifest, 'semantic.jsonl'), 'query')
    if ids is None:
        ids = [r['original_query_id'] for r in results]
    same(len(results), len(set(ids)), engine, 'reference coverage')
    for i, (row, query) in enumerate(zip(results, ids, strict=True), 1):
        same((row['query'], row['original_query_id']), (i, query), engine, 'reference order')
    same(set(trace), set(range(1, len(ids)+1)), engine, 'reference trace coverage')
    return {q: (results[i-1], trace[i]) for i, q in enumerate(ids, 1)}


def singleton(records, kind):
    matches = [r for r in records if r['type'] == kind]
    require(len(matches) == 1, f'replay ledger: expected one {kind}')
    return matches[0]


def validate(args, report):
    replay, capture = args.replay, args.capture
    worker_count = getattr(args, 'workers', 1)
    diagnostic = worker_count > 1
    concurrency = 32 if diagnostic else 1
    binding_path = capture/'binding.json'
    package = capture/'package'
    receipt = validate_package(package, binding_path)
    binding = read(binding_path)
    identity = binding['identity']
    ids = identity['query_ids']
    search = search_config(identity)
    base = identity['objects']['base']
    dim = base['width']
    report['inputs']['static_ir'] = dict(receipt, binding_sha256=file_hash(binding_path),
                                         identity=identity, build=binding['build'])
    profile = read(args.profile)
    validate_profile(profile)
    profile_sha = file_hash(args.profile)
    report['inputs']['profile'] = dict(sha256=profile_sha, training=profile['training'],
                                       applicability=profile['applicability'])
    training = profile['training']
    require(not set(ids).intersection(training['query_ids']), 'training/evaluation original query ID overlap')
    if args.scope == 'evaluation':
        require('merge_append_table' in profile, 'evaluation requires measured merge_append Profile')
        selection = read(ROOT/SELECTION)['datasets']['sift1m']
        same((training['split'], training['query_ids'], ids, base['records'], dim),
             ('tuning32', selection['tuning_ids'], selection['evaluation_ids'], 1_000_000, 128),
             'evaluation', 'frozen split/shape')
        require(args.fit_report is not None, 'evaluation requires the frozen fit report')
        same(file_hash(args.fit_report), training['fit_report_sha256'], 'profile', 'fit report SHA256')
        fit = read(args.fit_report)
        same((fit['format'], fit['applicability'], fit['split'],
              [r['measurement_sha256'] for r in fit['input_measurements']]),
             ('ndpsim-v04-fit-report/1', profile['applicability'],
              dict(tuning_ids=training['query_ids'], evaluation_ids=ids, overlap=[]),
              training['measurement_sha256']), 'profile', 'fit provenance')
        stage_rows = {(r['operator'], r['stage']): r for r in fit['stages']}
        if 'merge_append_table' in profile:
            stage_rows.pop(('MERGE_CANDIDATES', 'merge_append'), None)
        same(len(stage_rows), len(profile['models']), 'profile', 'frozen stage coverage')
        for entry in profile['models']:
            key = entry['operator'], entry['stage']
            frozen = stage_rows[key]
            features = frozen['candidate_features']
            same((entry['model'], entry['features'], entry['samples']),
                 (frozen['model'], features[:1] if frozen['selected'] == 'table' else features,
                  frozen['quality']['usable']), f'profile {key}', 'frozen model')
        split = 'evaluation128'
    else:
        same(training['split'], 'fixture', 'quick', 'fixture model split')
        split = 'fixture'

    manifest = read(replay/'manifest.json')
    command = read(artifact(replay, manifest, 'sst-measurement.json'))
    report['inputs']['replay'] = dict(manifest_sha256=file_hash(replay/'manifest.json'), command=command,
                                     element_sha256=manifest['element_sha256'])
    command_ok(command, 'replay')
    config = read(artifact(replay, manifest, 'config.json'))
    effective_path = artifact(replay, manifest, 'effective-config.json')
    effective = read(effective_path)
    validate_config(effective)
    same(file_hash(effective_path), manifest['replay_config_sha256'], 'replay', 'effective config SHA256')
    same((effective['execution_engine'], manifest['execution_engine'], effective['placement'],
          effective['data_provider'], effective['native_workers'], effective['cpu_slots'],
          effective['max_inflight'], config['search']),
         ('replay', 'replay', 'host', 'host_memory', worker_count, worker_count, concurrency, search), 'replay', 'effective config')
    same((config['execution_engine'], config['placement'], config['data_provider'],
          config['native_workers'], config['max_inflight'], config['host']['slots'], config['order_policy'],
          config['ir_input'], config['cpu_profile']),
         ('replay', 'host', 'host_memory', worker_count, concurrency, worker_count, effective['order_policy'],
          effective['ir_input'], effective['cpu_profile']), 'replay', 'recorded/effective configuration')
    same((effective['ir_input']['sha256'], effective['cpu_profile']['sha256']),
         (receipt['package_sha256'], profile_sha), 'replay', 'package/Profile SHA256')
    same(read(artifact(replay, manifest, 'replay-preflight.json')),
         dict(receipt, binding_sha256=file_hash(binding_path)), 'replay', 'preflight receipt')
    admission = read(artifact(replay, manifest, 'replay-admission.json'))
    same((admission['profile_sha256'], admission['query_ids'], admission['objects'], admission['order_policy']),
         (profile_sha, ids, {k: v['sha256'] for k, v in identity['objects'].items()}, effective['order_policy']),
         'replay', 'admission identity')
    same(profile['applicability'], dict(admission['applicability'], dimensions=dim, records=base['records'],
                                        preparation_context='resident_random', workers=1),
         'profile', 'applicability')
    same(admission['applicability']['build'], binding['build'], 'replay', 'native build')
    if args.scope == 'evaluation':
        require(not admission['allow_fixture'] and binding['source'] == 'native-capture',
                'evaluation requires production admission')
    report['inputs']['replay']['effective_config'] = effective

    shape, paths, input_identity = load_v2(manifest['data'], config, replay)
    same(input_identity, manifest['input_identity'], 'replay', 'revalidated workload')
    for name, key in [('base', 'vector_file'), ('graph', 'graph_file'), ('queries', 'query_file')]:
        same((binding_path.parent/binding['files'][name]).resolve(), Path(paths[key]).resolve(),
             'workload', f'{name} path')
    mapping = Path(manifest['data'])/input_identity['dataset']['files']['query']['mapping']
    same([q for q, in struct.iter_unpack('<I', mapping.read_bytes())], ids, 'workload', 'original ID mapping')
    native = {}
    baseline_mode = getattr(args, 'baseline_mode', 'uninstrumented')
    for mode, path in [('static_capture', capture), (baseline_mode, args.baseline), ('cpu_sample', args.samples)]:
        quota = dict(workers=worker_count, concurrency=concurrency) if mode == baseline_mode else {}
        native[mode], measurement = native_run(path, mode, binding, input_identity, split, report, **quota)
    inventory = compare_inventory(package/'operations.jsonl', identity, measurement)
    report['checks'].append(dict(name='identity_and_contracts', status='passed', sampling_inventory=inventory))

    detail_refs = getattr(args, 'detail8_references', None)
    subset = None
    if getattr(args, 'reference_detail8', False) or detail_refs:
        raw = Path(paths['query_file']).read_bytes()
        subset = {q: raw[i*dim*4:(i+1)*dim*4] for i, q in enumerate(ids)}
    subset_only = getattr(args, 'reference_detail8', False)
    if subset_only:
        same((args.scope, search['width'], search['budget']), ('evaluation', 32, 64),
             'reference', 'subset applicability')
    refs = {engine: reference(path, engine, search, input_identity, manifest['element_sha256'], report,
                              subset=subset if subset_only else None)
            for engine, path in [('direct', args.direct), ('ir', args.ir)]}
    same(list(refs['direct']), list(refs['ir']), 'reference', 'matching query order')
    if not subset_only:
        same(list(refs['direct']), ids, 'reference', 'full query order')
    if detail_refs:
        detail = dict(inputs={})
        for engine, directory in zip(('direct', 'ir'), detail_refs, strict=True):
            detail_rows = reference(directory, engine, search, input_identity, manifest['element_sha256'],
                                    detail, subset=subset)
            for query, (result, trace) in detail_rows.items():
                full_result, full_trace = refs[engine][query]
                same(trace, full_trace, f'{engine} query {query}', 'detail8 semantic trace')
                for key in ('top_k', 'expanded', 'insufficient'):
                    same(result[key], full_result[key], f'{engine} query {query}', f'detail8 {key}')
        report['inputs']['detail8_references'] = detail['inputs']
    operations = grouped(package/'operations.jsonl', 'query')
    events = defaultdict(list)
    records = []
    replay_decisions = defaultdict(list)
    for event in rows(artifact(replay, manifest, 'events.jsonl')):
        kind = event['type']
        if kind in ('replay_submit', 'replay_verified', 'replay_read', 'replay_stage'):
            events[event['original_query_id']].append(event)
        elif kind == 'decision':
            replay_decisions[event['query']].append(event)
        else:
            records.append(event)
    services = defaultdict(list)
    for row in records:
        if row['type'] == 'service':
            services[row['query']].append(row)
    service_ids = [r['task'] for group in services.values() for r in group]
    require(len(service_ids) == len(set(service_ids)), 'replay ledger: duplicate global CPU task')
    samples = defaultdict(list)
    for row in measurement['samples']:
        samples[row['query']].append(row)
    result_doc = read(artifact(replay, manifest, 'results.json'))
    results = result_doc['queries']
    same(len(results), len(ids), 'replay', 'query coverage')
    same(set(events), set(ids), 'replay', 'event query coverage')
    same(set(services), set(range(1, len(ids)+1)), 'replay', 'service query coverage')
    models = {(r['operator'], r['stage']): r for r in profile['models']}
    if table := profile.get('merge_append_table'):
        require(table['split'] == split, 'merge append table split mismatch')
        same(build_table(profile, package, binding_path, args.merge_append_measurement), table,
             'profile', 'reproduced merge append table')
        models['measured_append'] = {(r['query'], r['op'], r['segment']): r for r in table['rows']}
        report['inputs']['profile']['merge_append_table'] = dict(
            package_sha256=table['package_sha256'], measurement_sha256=table['measurement_sha256'],
            calls=len(table['rows']), cpu_ns=sum(r['ns'] for r in table['rows']))
    query_report = report['queries']
    with ExitStack() as stack:
        vectors, graph, queries = [
            stack.enter_context(FileRows(paths[key], width, adjacency=adjacency))
            for key, width, adjacency in [('vector_file', dim, False),
                                          ('graph_file', shape['degree']+1, True),
                                          ('query_file', dim, False)]]
        for i, query in enumerate(ids):
            where = f'query {query}'
            result = results[i]
            same((result['query'], result['original_query_id']), (i+1, query), where, 'result identity')
            expected = oracle(vectors, graph, queries[i], search)
            decisions, batches, _, top = expected
            same([(score_pairs(r['candidates'])[:search['width']], r['expand'])
                  for r in replay_decisions.pop(i+1, [])], decisions, where, 'replay decisions')
            score_chunks = chunks(batches, search['chunk'])
            expanded = sum(len(chosen) for _, chosen in decisions)
            scored = sum(map(len, batches))
            cpu, reads, size, work = validate_query(query, operations[query], events[query], services[i+1],
                samples[query], expected, graph, search, dim, models, result)
            for mode, batch in native.items():
                n = batch[i]
                same((score_pairs(n['top_k']), n['expanded'], n['scored'], n['work'],
                      n['score_batches'], n['score_chunks'], n['expansion_batches'], n['insufficient_results']),
                     (top, expanded, scored, work, len(batches), len(score_chunks), len(decisions)-1,
                      len(top) < search['k']), f'{where} {mode}', 'top-k/counts/Work')
            captured = native['static_capture'][i]
            if 'score_trace' in captured or 'expansion_trace' in captured:
                check_chunks(captured['score_trace'], score_chunks, f'{where} capture detail', False)
                same(captured['expansion_trace'], [chosen for _, chosen in decisions],
                     where, 'capture detail expansion')
            reference_work = {}
            for engine, reference_rows in refs.items():
                if query in reference_rows:
                    r, trace = reference_rows.pop(query)
                    reference_work[engine] = check_sim(trace, r, expected, score_chunks, dim, 'host',
                                                       f'{where} {engine}', False)
                    same(reference_work[engine], work, f'{where} {engine}', 'reference Work')
            same((score_pairs(result['top_k']), result['expanded'], result['insufficient']),
                 (top, expanded, len(top) < search['k']), where, 'replay top-k/terminal')
            n = native[baseline_mode][i]
            same(result['recall_at_k'], n['recall_at_k'], where, 'recall')
            same(result['latency_ns'], result['visible_ns']-result['admission_ns'], where, 'latency')
            if not diagnostic:
                same((result['worker'], result['worker_wait_ns'], result['admission_ns']),
                     (0, 0, results[i-1]['visible_ns'] if i else 0), where, 'FIFO admission')
            query_report.append(dict(query=query, operations=len(operations[query]), reads=reads,
                logical_bytes=size, work=work, reference_work=reference_work, expanded=expanded, scored=scored,
                top_k=result['top_k'], native_cpu_ns=n['thread_cpu_ns'], replay_cpu_ns=cpu,
                cpu_delta_ns=cpu-n['thread_cpu_ns'], native_latency_ns=n['latency_ns'],
                replay_latency_ns=result['latency_ns'], latency_delta_ns=result['latency_ns']-n['latency_ns']))
    require(not replay_decisions and all(not reference_rows for reference_rows in refs.values()), 'extra decision/reference events')
    report['checks'].append(dict(name='independent_search_numeric_and_work', status='passed'))
    ledger = singleton(records, 'resident_summary')
    workers = singleton(records, 'worker_summary')
    lifecycle = singleton(records, 'lifecycle')
    summary = singleton(records, 'summary')
    path = singleton(records, 'path_summary')
    same((ledger['reads'], ledger['logical_bytes'], ledger['ssd_requests'], ledger['active_sessions']),
         (sum(r['reads'] for r in query_report), sum(r['logical_bytes'] for r in query_report), 0, 0),
         'replay ledger', 'resident reads/bytes/SSD/sessions')
    same([lifecycle[k] for k in ('runtime_tasks', 'waiters', 'leases')], [0, 0, 0], 'replay ledger', 'drain')
    same((path['ordinary_dma_bytes'], path['controller']), (0, False), 'replay ledger', 'device path')
    require(not any(r['type'] in {'ordinary_storage', 'local_storage', 'ssd_stat', 'link_summary', 'message',
                                  'replay_failure', 'device_profile'} for r in records),
            'replay ledger: unexpected device or failure record')
    replay_cpu = sum(r['replay_cpu_ns'] for r in query_report)
    same(workers['cpu_busy_ns'], replay_cpu, 'replay ledger', 'CPU busy time')
    worker_wait = 0
    if diagnostic:
        timeline = worker_timeline(results, worker_count, concurrency, 'replay')
        cpu_peak = peak_overlap((r['start_ns'], r['end_ns']) for group in services.values() for r in group)
        same((timeline['peak_admitted'], timeline['peak_workers'], cpu_peak),
             (summary['peak_inflight'], workers['peak_workers'], workers['cpu_peak_slots']),
             'replay ledger', 'independent resource peaks')
        require(0 < cpu_peak <= worker_count, 'replay: CPU slots exceeded or unused')
        same(summary['admission_limit'], concurrency, 'replay ledger', 'admission limit')
        worker_wait = timeline['worker_wait_ns']
        report['worker_resources'] = dict(timeline=timeline, ledger=workers)
        report['checks'].append(dict(name='worker_and_cpu_slot_quota', status='passed'))
    same([workers[k] for k in ('workers_idle', 'cpu_busy_slots', 'cpu_queued', 'pending_admissions',
                               'cpu_queue_wait_ns', 'cpu_dependency_wait_ns', 'worker_wait_ns')],
         [worker_count, 0, 0, 0, 0, 0, worker_wait], 'replay ledger', 'worker drain/wait')
    same((workers['workers'], workers['cpu_slots'], workers['max_inflight'], workers['profile_workers'],
          workers['profile_use'], workers['order_policy']),
         (worker_count, worker_count, concurrency, 1,
          'diagnostic' if diagnostic else 'matched', effective['order_policy']), 'replay ledger', 'worker configuration')
    same((summary['drain_end_ns'], summary['live_results'], summary['live_content'],
          summary['useful_bytes'], summary['sector_bytes']),
         (summary['end_ns'], 0, 0, 0, 0), 'replay ledger', 'drain and storage bytes')
    same(result_doc['summary'], summary, 'replay ledger', 'batch summary')
    same((summary['queries'], summary['origin_ns'], summary['end_ns'], summary['services']),
         (len(ids), 0, max(r['visible_ns'] for r in results), sum(map(len, services.values()))),
         'replay ledger', 'window/service count')
    report['checks'].append(dict(name='independent_access_service_and_drain_ledger', status='passed'))
    native_rows = native[baseline_mode]
    native_ns = max(r['visible_ns'] for r in native_rows)-min(r['admission_ns'] for r in native_rows)
    native_cpu = sum(r['native_cpu_ns'] for r in query_report)
    replay_ns = summary['end_ns']
    require(native_ns > 0 and (args.scope == 'quick' or min(native_cpu, replay_ns) > 0),
            'invalid CPU or QPS denominator')
    metrics = report['metrics']
    for label, window, cpu in [('native', native_ns, native_cpu), ('replay', replay_ns, replay_cpu)]:
        latencies = [r[label+'_latency_ns'] for r in query_report]
        metrics[label] = dict(qps=len(ids)*1e9/window if window else None, window_ns=window, cpu_ns=cpu,
                              occupancy=cpu/(window*worker_count) if window else None,
                              p50_ns=quantile(latencies, .5), p95_ns=quantile(latencies, .95))
    metrics['relative_error'] = {
        key: abs(metrics['replay'][key]/metrics['native'][key]-1)
        if metrics['native'][key] and metrics['replay'][key] is not None else None for key in ('qps', 'cpu_ns', 'p50_ns', 'p95_ns')}
    metrics['occupancy_delta'] = (metrics['replay']['occupancy']-metrics['native']['occupancy']
                                  if replay_ns else None)
    errors = metrics['relative_error']
    if not diagnostic:
        metrics['single_run_level_c_thresholds_met'] = level_c_thresholds_met(errors)
    metrics['timing_status'] = 'diagnostic' if native_cpu and replay_ns else 'not_applicable'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'baseline', 'samples', 'profile', 'replay', 'direct', 'ir', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--baseline-mode', choices=('uninstrumented', 'benchmark'), default='uninstrumented')
    parser.add_argument('--reference-detail8', action='store_true')
    parser.add_argument('--fit-report', type=Path)
    parser.add_argument('--merge-append-measurement', action='append', type=Path, default=[])
    parser.add_argument('--scope', choices=('quick', 'evaluation'), required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    report = dict(format=FORMAT, scope=args.scope, status='failed', exit_code=1, command=sys.argv,
                  inputs={}, checks=[], queries=[], metrics={},
                  levels=dict(A='not_run', B='not_run', C='not_run', workers='not_applicable',
                              offload='not_applicable'),
                  limitations=['One resident single-worker point per invocation.',
                               'Level A/B/C precision and full AC require their separate evaluation tasks.',
                               'SSD, PCIe and Controller remain configured_unvalidated.'])
    try:
        validate(args, report)
        report.update(status='diagnostic', exit_code=0)
    except (ValueError, OSError, KeyError, TypeError, IndexError, OverflowError, StopIteration) as error:
        report['checks'].append(dict(name='validation', status='failed', detail=str(error) or type(error).__name__))
    write_json(args.output/'report.json', report)
    lines = ['# V0.4 Validator', '', f'Scope `{args.scope}` · Status `{report["status"]}`',
             '', '## Assertions', '']
    lines.extend(f'- {row["name"]} · {row["status"]}' + (f' · {row["detail"]}' if 'detail' in row else '')
                 for row in report['checks'])
    if report['metrics']:
        lines.extend(['', '## Single-run diagnostics', '',
                      '| Metric | Native | Replay | Relative error |',
                      '| --- | ---: | ---: | ---: |'])
        for key in ('qps', 'cpu_ns', 'p50_ns', 'p95_ns'):
            metrics = report['metrics']
            error = metrics['relative_error'][key]
            display = f'{error:.2%}' if error is not None else 'N/A'
            lines.append(f'| {key} | {metrics["native"][key]} | {metrics["replay"][key]} | {display} |')
    lines.extend(['', '## Acceptance levels', ''])
    lines.extend(f'- {level} · {status}' for level, status in report['levels'].items())
    lines.extend(['', '## Limits', '', *(f'- {limit}' for limit in report['limitations']),
                  '', 'Per-query details, identities, commands and metrics are in report.json.', ''])
    (args.output/'report.md').write_text('\n'.join(lines))
    print(f'{report["status"]} {args.output/"report.json"}')
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
