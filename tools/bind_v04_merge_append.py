"""Bind a frozen V04-3b Profile to measured merge_append calls for one static package."""
import argparse
import json
from pathlib import Path
from statistics import median_low

from native_observation import MEASUREMENT_LIMIT, compare_inventory
from v04_contract import (ROOT, WORK, bounded_read, decode, digest, require, validate_measurement,
                          validate_package, validate_profile)


def build_table(profile, package, binding_path, measurements):
    receipt = validate_package(package, binding_path)
    binding = decode(bounded_read(binding_path))
    identity = binding['identity']
    conditions = profile['applicability']
    base = identity['objects']['base']
    require(binding['build'] == conditions['build'] and
            (base['records'], base['width']) == (conditions['records'], conditions['dimensions']),
            'Profile and capture build/shape mismatch')
    require(len(measurements) >= 3, 'at least three independent CPU samples required')
    calls = {}
    with (package/'operations.jsonl').open('rb') as stream:
        for line in stream:
            row = decode(line)
            if row['operator'] == 'SCORE_L2':
                count = len(row['expected']['ids'])
                chunk = identity['search']['chunk']
                for segment, offset in enumerate(range(0, count, chunk)):
                    calls[row['query'], row['op']+1, segment] = min(chunk, count-offset)
    repeats = {key: [] for key in calls}
    hashes, run_ids = [], set()
    split = None
    resolution_ns = 0
    for path in measurements:
        raw = bounded_read(path, MEASUREMENT_LIMIT)
        record = decode(raw)
        validate_measurement(record)
        require(record['cpu_clock']['source'] == 'CLOCK_THREAD_CPUTIME_ID',
                f'{path}: unsupported CPU clock')
        resolution_ns = max(resolution_ns, record['cpu_clock']['resolution_ns'])
        require(record['mode'] == 'cpu_sample' and record['sample_stride'] == 1 and
                record['build'] == conditions['build'] and
                record['workers'] == record['max_inflight'] == conditions['workers'] == 1 and
                record['preparation_context'] == conditions['preparation_context'] == 'resident_random',
                f'{path}: incompatible CPU measurement')
        if record['split'] != 'fixture':
            manifest = decode(bounded_read(path.parent/'manifest.json'))
            host = {key: conditions[key] for key in ('hardware', 'os', 'memory_bytes', 'frequency', 'affinity')}
            require(manifest['passed'] and manifest['paging_eligible'] and manifest['observation_complete'] and
                    manifest['cpu_host'] == host and
                    manifest['artifacts_sha256'][path.name] == digest(raw),
                    f'{path}: run manifest, residency or host mismatch')
        compare_inventory(package/'operations.jsonl', identity, record)
        require(split is None or split == record['split'], 'mixed measurement splits')
        split = record['split']
        require(record['run_id'] not in run_ids, 'reused measurement run')
        run_ids.add(record['run_id'])
        hashes.append(digest(raw))
        samples = {(r['query'], r['op'], r['segment']): r for r in record['samples']
                   if r['bucket'] == 'merge'}
        for key, count in calls.items():
            sample = samples.get(key)
            require(sample is not None and sample['calls'] == 1 and
                    sample['features'] == dict(old_scores=0, new_scores=count) and
                    sample['work'] == dict.fromkeys(WORK, 0), f'{path}: merge_append {key} missing or mismatched')
            repeats[key].append(sample['corrected_ns'])
    training_ids = profile['training']['query_ids']
    ids = identity['query_ids']
    if split != 'fixture':
        selection = decode(bounded_read(ROOT/'configs/benchmarks/v0.2-query-selection.json'))
        expected = selection['tuning_ids' if split == 'tuning32' else 'evaluation_ids']
        require(training_ids == selection['tuning_ids'] and ids == expected,
                'measurement split IDs mismatch')
    rows = []
    for (query, op, segment), values in sorted(repeats.items()):
        # Keep signed short samples. Reject calls whose aggregate remains below one tick.
        ns = median_low(values)
        require(ns >= 0 and sum(values) >= resolution_ns,
                f'merge_append {(query, op, segment)}: insufficient aggregate timing signal')
        rows.append(dict(query=query, op=op, segment=segment, new_scores=calls[query, op, segment], ns=ns))
    return dict(package_sha256=receipt['package_sha256'], measurement_sha256=hashes,
                split=split, rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profile', 'package', 'binding', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--measurement', action='append', type=Path, required=True)
    args = parser.parse_args()
    profile = decode(bounded_read(args.profile))
    validate_profile(profile)
    require(profile['format'] == 'ndpsim-cpu-profile/3', 'expected frozen v3 source Profile')
    table = build_table(profile, args.package, args.binding, args.measurement)
    profile['format'] = 'ndpsim-cpu-profile/4'
    profile['merge_append_table'] = table
    profile['models'] = [row for row in profile['models'] if row['stage'] != 'merge_append']
    validate_profile(profile)
    with args.output.open('x') as stream:
        json.dump(profile, stream, separators=(',', ':'), allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
