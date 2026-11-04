"""Collect V04-2c on the locked full SIFT1M input, using only tuning32 searches."""
import argparse
from collections import defaultdict
import math
import os
from pathlib import Path
import re
import shutil
from statistics import median
import sys

from ann_data import write_json
from freeze_v04_start import dependency_snapshot, input_snapshot
from native_observation import BUCKET_OP, build_identity, compare_inventory
from run_native import pressure
from v04_contract import LIMIT, require
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import GIB, load

MIB = 1024**2
POINTS = ((32, 64), (64, 128), (128, 256), (256, 512))
MAIN = (128, 256)
SUPPLEMENT = (16, 32)
TIMEOUT = 300
RULE = dict(short='at least half of corrected per-call samples below 4 × max(their capture clock resolution, empty-probe ns), or usable fraction < 90%',
            main='estimated corrected CPU >= 5% of measured query-thread CPU across four points, including mixed-duration buckets',
            estimate='per point mean of max(corrected_ns, 0) per sampled call × eligible calls; estimates are diagnostic',
            other='major and short-sample flags are independent; primary label uses major, then short, then secondary; exclude low_signal samples from fitting',
            unmeasured='state transitions are already charged in resident_random preparation')


def size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def resources(out, reserve):
    snapshot = pressure()
    require(snapshot.get('exit_code') == 0, 'macOS memory pressure unavailable')
    ram = re.search(r'The system has (\d+)', snapshot['stdout'])
    free = re.search(r'System-wide memory free percentage: (\d+)%', snapshot['stdout'])
    disk = shutil.disk_usage(out).free
    require(ram and int(ram[1]) >= 8*GIB and free and int(free[1]) >= 20,
            'insufficient RAM or elevated memory pressure')
    require(disk >= 20*GIB + reserve, 'insufficient disk for the collection and reserve')
    return dict(physical_ram_bytes=int(ram[1]), memory_free_percent=int(free[1]),
                pressure=snapshot, free_disk_bytes=disk, reserve_bytes=reserve)


def signature(batch):
    keys = ('original_query_id', 'top_k', 'recall_at_k', 'one_recall_at_r', 'expanded',
            'scored', 'work', 'expansion_batches', 'score_batches', 'score_chunks',
            'insufficient_results')
    return [[row[key] for key in keys] for row in batch['queries']]


def aba(groups):
    rows = []
    for a1, b, a2 in groups:
        require(all(math.isfinite(qps) and qps > 0 for qps in (a1, b, a2)), 'invalid A/B/A QPS')
        baseline = (a1+a2)/2
        perturbation = (b-baseline)/baseline
        rows.append(dict(a_before_qps=a1, b_qps=b, a_after_qps=a2,
                         a_mean_qps=baseline, signed_fraction=perturbation,
                         absolute_fraction=abs(perturbation),
                         a_drift_fraction=(a2-a1)/baseline))
    require(len(rows) >= 3, 'A/B/A requires at least three complete groups')
    value = median(row['absolute_fraction'] for row in rows)
    return dict(groups=rows, median_absolute_fraction=value, limit=0.05, passed=value <= 0.05)


def coverage(record, validation):
    buckets = defaultdict(list)
    for row in record['samples']:
        buckets[row['bucket']].append(row)
    clock = record['cpu_clock']['resolution_ns']
    probe = record['empty_probe_ns']//record['empty_probe_count']
    summary = {}
    for name, samples in sorted(buckets.items()):
        corrected = [s['corrected_ns'] for s in samples]
        usable = [s for s in samples if s['quality'] == 'usable']
        summary[name] = dict(
            operator=BUCKET_OP[name], samples=len(samples), usable=len(usable),
            eligible=validation['eligible_calls'][name], raw_ns=sum(s['raw_ns'] for s in samples),
            estimated_cpu_ns=sum(max(t, 0) for t in corrected)/len(samples)*validation['eligible_calls'][name],
            features_usable={key: [min(s['features'][key] for s in usable),
                                   max(s['features'][key] for s in usable)]
                             for key in samples[0]['features'] if usable},
            short_threshold_ns=4*max(clock, probe),
            below_threshold=sum(t < 4*max(clock, probe) for t in corrected))
    require(set(summary) == set(BUCKET_OP), 'training point missing a CPU bucket')
    return summary


def classify(points):
    result = {}
    total = sum(p['validation']['thread_cpu_ns'] for p in points)
    for name, op in BUCKET_OP.items():
        rows = [p['buckets'][name] for p in points]
        count = sum(r['samples'] for r in rows)
        usable = sum(r['usable'] for r in rows)/count
        # Compare each sample to the threshold of its own capture. Ties are short.
        short = 2*sum(r['below_threshold'] for r in rows) >= count or usable < .9
        fraction = sum(r['estimated_cpu_ns'] for r in rows)/total
        major = fraction >= .05
        result[name] = dict(operator=op, estimated_cpu_fraction=fraction, usable_fraction=usable,
                            major=major, short_sample_flag=short,
                            classification='major' if major else 'short' if short else 'secondary')
    return result


def collect(args, out):
    prepared, raw, builder, binary = (getattr(args, key).resolve()
                                      for key in ('prepared', 'raw', 'builder', 'binary'))
    disk_limit = 25*GIB
    evidence_root = ROOT/'results/v0.4'
    existing = size(evidence_root)
    require(existing + size(raw) + size(prepared) + 2*GIB <= disk_limit,
            'insufficient room in the frozen aggregate budget')
    write_json(out/'preflight.json', resources(out, 2*GIB))
    inputs = input_snapshot(prepared, raw, builder)
    frozen = load(args.frozen_inputs)
    require(inputs['preparation']['files_sha256'] == frozen['preparation']['files_sha256'] and
            inputs['locks'] == frozen['locks'] and inputs['query_groups'] == frozen['query_groups'],
            'inputs differ from the V04-0 freeze')
    write_json(out/'inputs.json', inputs)
    write_json(out/'input-freeze.json', dict(path=str(args.frozen_inputs), sha256=sha(args.frozen_inputs)))
    locked = inputs['preparation']['files_sha256']
    expected_objects = {name: locked[path] for name, path in
                        (('base', 'tuning/base.f32'), ('queries', 'tuning/query.f32'),
                         ('graph', 'index/adjacency.u32'))}
    dependencies = dependency_snapshot(out)
    write_json(out/'dependencies.json', dependencies)
    source = inventory()
    write_json(out/'sources.json', source)
    build = build_identity(binary, sha(binary))
    write_json(out/'build.json', build)
    ids = inputs['query_groups']['tuning']
    index = load(prepared/'index/index.json')
    configs = {}
    for width, budget in (*POINTS, SUPPLEMENT):
        path = out/f'l{width}-e{budget}.json'
        write_json(path, dict(format='ndpsim-experiment-v2', index=str(prepared/'index'),
                              truth=str(prepared/'tuning-truth'),
                              search=dict(entry=index['entry'], k=10, width=width, batch=1,
                                          budget=budget, chunk=16)))
        configs[width, budget] = path

    def run(label, point, mode, budget, buffer, stride=1):
        dest = out/label
        write_json(out/f'{label}-preflight.json', resources(out, budget))
        config = configs[point]
        cmd = [sys.executable, str(ROOT/'tools/run_native.py'), '--binary', str(binary),
               '--config', str(config), '--data', str(prepared/'tuning'), '--output', str(dest),
               '--mode', mode, '--split', 'tuning32', '--workers', '1', '--concurrency', '1',
               '--sample-stride', str(stride), '--timeout', str(TIMEOUT), '--rss-limit', str(4*GIB),
               '--output-budget', str(budget), '--query-buffer', str(buffer)]
        # The launcher also waits for the native child in its own process group.
        # Allow time outside its deadline for input verification and evidence hashing.
        receipt = measure(cmd, out/f'{label}-launcher.json', TIMEOUT+120, 4*GIB)
        require(receipt['passed'], f'{label}: launcher failed or exceeded its resource budget')
        manifest = load(dest/'manifest.json')
        batch = load(dest/'batch.json')
        record = load(dest/'measurement.json')
        require(manifest['passed'] and manifest['observation_complete'] and
                manifest['launcher_self_peak_rss_bytes'] <= 512*MIB and
                manifest['build'] == record['build'] == build and
                manifest['config_sha256'] == sha(config) and record['split'] == 'tuning32' and
                record['mode'] == mode and record['sample_stride'] == stride and
                record['identity']['query_ids'] == ids == [r['original_query_id'] for r in batch['queries']] and
                {name: obj['sha256'] for name, obj in record['identity']['objects'].items()} == expected_objects and
                manifest['input']['truth']['ids']['sha256'] == locked['tuning-truth/ids.u32'] and
                manifest['shape']['count'] == 1000000 and manifest['shape']['dimensions'] == 128 and
                manifest['paging_eligible'] and batch['major_faults'] == 0 and size(dest) <= budget,
                f'{label}: identity, paging or budget mismatch')
        return dest, batch, record

    pilot = {}
    for mode, stride in (('static_capture', 1), ('cpu_sample', 8)):
        dest, batch, _ = run('pilot-'+mode, POINTS[-1], mode, 512*MIB, 32*MIB, stride)
        pilot[mode] = dict(total_bytes=size(dest), shards_bytes=size(dest/'native-shards'),
                           max_shard_bytes=max(f.stat().st_size for f in (dest/'native-shards').iterdir()),
                           package_bytes=size(dest/'package'), index_bytes=(dest/'package/index.jsonl').stat().st_size
                           if mode == 'static_capture' else 0,
                           validation_scratch_files_bytes=0, measurement_bytes=(dest/'measurement.json').stat().st_size,
                           qps=batch['qps'])
    cap, cpu = pilot['static_capture'], pilot['cpu_sample']
    output_budget = max(64*MIB, math.ceil(1.25*max(cap['total_bytes'], cpu['total_bytes'])/MIB)*MIB)
    query_buffer = max(4*MIB, math.ceil(2*max(cap['max_shard_bytes'], cpu['max_shard_bytes'])/MIB)*MIB)
    require(output_budget <= 512*MIB and query_buffer <= 32*MIB and cpu['measurement_bytes'] < LIMIT,
            'pilot exceeds production budget')
    remaining_budget = (9 + 3*len(POINTS) + 2)*output_budget
    write_json(out/'production-preflight.json', resources(out, remaining_budget))
    require(size(raw) + size(prepared) + size(evidence_root) + remaining_budget <= disk_limit,
            'pilot projection exceeds aggregate disk budget')
    write_json(out/'plan.json', dict(pilot=pilot, output_budget_bytes=output_budget,
        query_buffer_bytes=query_buffer, metadata_limit_bytes=LIMIT, timeout_seconds=TIMEOUT,
        launcher_timeout_seconds=TIMEOUT+120, native_rss_bytes=4*GIB, launcher_rss_bytes=512*MIB,
        validator_scratch='streamed, staged package renamed in place; no second copy',
        points=POINTS, stride=8, supplement=dict(point=SUPPLEMENT, stride=1,
            purpose='additional low-work shapes and full-inventory unattributed CPU audit'),
        aba_point=MAIN, aba_groups=3, classification_rule=RULE))

    timing, reference = [], None
    for group in range(1, 4):
        rows = []
        for suffix, mode in (('a-before', 'uninstrumented'), ('b', 'benchmark'), ('a-after', 'uninstrumented')):
            label = f'aba-{group}-{suffix}'
            _, batch, _ = run(label, MAIN, mode, output_budget, query_buffer)
            if reference is None:
                reference = signature(batch)
            require(signature(batch) == reference, f'{label}: A/B/A results or Work differ')
            rows.append(dict(run=label, mode=mode, **{k: batch[k] for k in
                        ('qps', 'p50_ns', 'p95_ns', 'window_ns', 'minor_faults', 'major_faults')}))
        timing.append(rows)
    aba_report = aba([[r['qps'] for r in group] for group in timing])
    aba_report.update(point=MAIN, ordered_samples=timing)
    write_json(out/'aba.json', aba_report)

    points = []
    for width, budget in (*POINTS, SUPPLEMENT):
        point = width, budget
        label = f'l{width}-e{budget}'
        stride = 1 if point == SUPPLEMENT else 8
        baseline = median(g[1]['qps'] for g in timing)
        expected = reference
        if point != MAIN:
            _, batch, _ = run(label+'-benchmark', point, 'benchmark', output_budget, query_buffer)
            baseline, expected = batch['qps'], signature(batch)
        capture, batch, _ = run(label+'-static', point, 'static_capture', output_budget, query_buffer)
        require(signature(batch) == expected, f'{label}: static capture result or Work mismatch')
        capture_qps = batch['qps']
        sample, batch, record = run(label+'-cpu', point, 'cpu_sample', output_budget, query_buffer, stride)
        require(signature(batch) == expected, f'{label}: CPU sample result or Work mismatch')
        static = load(capture/'package/manifest.json')
        require(static['build'] == build, 'package build mismatch')
        matched = compare_inventory(capture/'package/operations.jsonl', static['identity'], record)
        validation = load(sample/'measurement-validation.json')
        require(validation['thread_cpu_ns'] == batch['thread_cpu_ns'] and
                validation['score_audit_gap_ns'] >= 0 and validation['unattributed_cpu_ns'] >= 0,
                f'{label}: CPU interval closure failed')
        points.append(dict(point=point, supplement=point == SUPPLEMENT, stride=stride,
            measurement=str((sample/'measurement.json').relative_to(out)),
            measurement_sha256=sha(sample/'measurement.json'),
            package=str((capture/'package').relative_to(out)),
            package_validation=load(capture/'package-validation.json'),
            config_sha256=sha(configs[point]), inventory=matched, validation=validation,
            buckets=coverage(record, validation),
            timing_diagnostic=dict(benchmark_qps=baseline, cpu_sample_qps=batch['qps'],
                static_capture_qps=capture_qps, cpu_sample_qps_ratio=batch['qps']/baseline,
                static_capture_qps_ratio=capture_qps/baseline)))
        write_json(out/'coverage.json', dict(points=points))
    write_json(out/'classification.json', dict(rule=RULE, buckets=classify(points[:len(POINTS)])))
    write_json(out/'measurement-dataset.json', dict(format='ndpsim-v04-training-dataset/1',
        split='tuning32', query_ids=ids, build=build, points=points,
        classification_sha256=sha(out/'classification.json'), plan_sha256=sha(out/'plan.json'),
        inputs_sha256=sha(out/'inputs.json'), aba_sha256=sha(out/'aba.json')))
    require(aba_report['passed'], 'A/B/A median absolute QPS perturbation exceeds 5%')
    require(size(raw) + size(prepared) + size(evidence_root) <= disk_limit, 'aggregate disk budget exceeded')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'raw', 'builder', 'binary', 'frozen-inputs', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    out = args.output.resolve()
    require(out.is_relative_to(ROOT/'results/v0.4'), 'output must be in the V0.4 evidence budget directory')
    out.mkdir(parents=True)
    result = dict(task='V04-2c', requirements=['FR-04-P04/06/07', 'FR-04-C02', 'FR-04-V02',
        'PRD §6.4.4/8.1/10', 'AC-04-03'], argv=sys.argv, passed=False, exit_code=1)
    try:
        collect(args, out)
        result.update(passed=True, exit_code=0)
    except Exception as exc:
        result['error'] = str(exc)
        print(f'collect_v04_training: {exc}', file=sys.stderr)
    finally:
        result['artifacts_sha256'] = {str(p.relative_to(out)): sha(p)
                                      for p in sorted(out.rglob('*')) if p.is_file()}
        write_json(out/'manifest.json', result)
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())
