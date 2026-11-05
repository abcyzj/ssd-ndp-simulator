"""Collect the twelve V04-4b CPU sampling repeats on the frozen Mac and SIFT1M."""
import argparse
from pathlib import Path
import platform
import re
import subprocess
import sys

from ann_data import write_json
from collect_v04_training import GIB, MIB, POINTS, TIMEOUT, resources
from evaluate_v04_level_a import AC_POINTS, PROFILE_SHA, REPEATS, START_SHA, STRIDE, checked
from freeze_v04_start import dependency_snapshot, git, input_snapshot
from native_observation import build_identity
from v04_contract import ROOT, file_hash, require
from verify_m0 import inventory

RUN_BUDGET = 512*MIB
QUERY_BUFFER = 64*MIB
CAPTURE_TIMEOUT = 900


def collect(args, receipt):
    out = args.output
    profile = checked(args.profile, PROFILE_SHA)
    start = checked(args.start_manifest, START_SHA)
    frozen = checked(args.start_manifest.parent/'inputs.json', start['artifacts_sha256']['inputs.json'])
    run_budget = 1024*MIB if args.measured_append else RUN_BUDGET
    capture_budget = 2*GIB
    point_budget = 2*(REPEATS*run_budget+capture_budget) if args.measured_append else REPEATS*run_budget
    points = AC_POINTS if args.measured_append else POINTS
    write_json(out/'resources.json', resources(out, len(points)*point_budget))
    inputs = input_snapshot(args.prepared, args.raw, args.builder)
    require(all(inputs[k] == frozen[k] for k in ('preparation', 'locks', 'query_groups')),
            'input differs from V04-0 freeze')
    write_json(out/'inputs.json', inputs)
    write_json(out/'dependencies.json', dependency_snapshot(out))
    conditions = profile['applicability']
    build = build_identity(args.binary, file_hash(args.binary))
    require(build == conditions['build'] and platform.platform() == conditions['os'],
            'frozen build/OS mismatch')
    write_json(out/'build.json', build)
    entry = inputs['preparation']['files_sha256']['index/index.json']
    index = checked(args.prepared/'index/index.json', entry)
    jobs = []
    splits = [('tuning', 'tuning32'), ('evaluation', 'evaluation128')] if args.measured_append else [
        ('evaluation', 'evaluation128')]
    for data, split in splits:
        prefix = f'{data}-' if args.measured_append else ''
        for width, budget in points:
            point = f'{prefix}l{width}-e{budget}'
            config = out/f'{point}.json'
            write_json(config, dict(format='ndpsim-experiment-v2', index=str(args.prepared/'index'),
                                   truth=str(args.prepared/f'{data}-truth'),
                                   search=dict(entry=index['entry'], k=10, width=width,
                                               batch=1, budget=budget, chunk=16)))
            if args.measured_append:
                jobs.append((f'{point}-capture', config, data, split, 'static_capture', 1))
            jobs.extend((f'{point}-r{repeat}', config, data, split, 'cpu_sample',
                         1 if args.measured_append else STRIDE) for repeat in range(1, REPEATS+1))
    if not args.measured_append:
        # Preserve the historical round-robin order for diagnostic model samples.
        jobs.sort(key=lambda job: job[0].rsplit('-r', 1)[1])
    for label, config, data, split, mode, stride in jobs:
        budget = capture_budget if mode == 'static_capture' else run_budget
        timeout = CAPTURE_TIMEOUT if mode == 'static_capture' else TIMEOUT
        snapshot = resources(out, budget)
        hw = subprocess.run(['/usr/sbin/system_profiler', 'SPHardwareDataType'],
                            capture_output=True, text=True, check=True, timeout=10)
        chip = re.search(r'^\s*Chip: (.+)$', hw.stdout, re.MULTILINE)
        require(chip and chip[1] == conditions['hardware'] and
                snapshot['physical_ram_bytes'] == conditions['memory_bytes'], 'frozen hardware mismatch')
        write_json(out/f'{label}-resources.json', dict(snapshot, hardware=chip[1]))
        cmd = [sys.executable, '-B', str(ROOT/'tools/run_native.py'), '--binary', str(args.binary),
               '--config', str(config), '--data', str(args.prepared/data),
               '--output', str(out/label), '--mode', mode, '--split', split,
               '--workers', '1', '--concurrency', '1', '--sample-stride', str(stride),
               '--timeout', str(timeout), '--output-budget', str(budget),
               '--query-buffer', str(QUERY_BUFFER if args.measured_append else 16*MIB), '--rss-limit', str(4*GIB)]
        with (out/f'{label}.log').open('wb') as log:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        receipt['runs'].append(dict(label=label, argv=cmd, exit_code=proc.returncode))
        if proc.returncode:
            receipt['failed_runs'].append(label)
    require(not receipt['failed_runs'], 'one or more native runs failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profile', 'start-manifest', 'binary', 'prepared', 'raw', 'builder', 'output'):
        parser.add_argument('--'+name, type=lambda s: Path(s).resolve(), required=True)
    parser.add_argument('--measured-append', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    receipt = dict(argv=sys.argv, exit_code=1, runs=[], failed_runs=[], profile_sha256=PROFILE_SHA,
                   start_manifest_sha256=START_SHA)
    try:
        (args.output/'workspace.patch').write_bytes(git('diff', '--binary', 'HEAD'))
        write_json(args.output/'collection-sources.json', inventory())
        collect(args, receipt)
        receipt['exit_code'] = 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        receipt['error'] = str(exc)
        print(f'Level A collection: {exc}', file=sys.stderr)
    finally:
        receipt['artifacts_sha256'] = {
            str(p.relative_to(args.output)): file_hash(p) for p in args.output.rglob('*') if p.is_file()}
        write_json(args.output/'collection.json', receipt)
    return receipt['exit_code']


if __name__ == '__main__':
    sys.exit(main())
