"""Collect the twelve V04-4b CPU sampling repeats on the frozen Mac and SIFT1M."""
import argparse
from pathlib import Path
import platform
import re
import subprocess
import sys

from ann_data import write_json
from collect_v04_training import GIB, MIB, POINTS, TIMEOUT, resources
from evaluate_v04_level_a import PROFILE_SHA, REPEATS, START_SHA, STRIDE, checked
from freeze_v04_start import dependency_snapshot, git, input_snapshot
from native_observation import build_identity
from v04_contract import ROOT, file_hash, require
from verify_m0 import inventory

RUN_BUDGET = 512*MIB
QUERY_BUFFER = 16*MIB


def collect(args, receipt):
    out = args.output
    profile = checked(args.profile, PROFILE_SHA)
    start = checked(args.start_manifest, START_SHA)
    frozen = checked(args.start_manifest.parent/'inputs.json', start['artifacts_sha256']['inputs.json'])
    write_json(out/'resources.json', resources(out, len(POINTS)*REPEATS*RUN_BUDGET))
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
    configs = {}
    for width, budget in POINTS:
        label = f'l{width}-e{budget}'
        cfg = out/f'{label}.json'
        write_json(cfg, dict(format='ndpsim-experiment-v2', index=str(args.prepared/'index'),
                             truth=str(args.prepared/'evaluation-truth'),
                             search=dict(entry=index['entry'], k=10, width=width,
                                         batch=1, budget=budget, chunk=16)))
        configs[label] = cfg
    for repeat in range(1, REPEATS+1):
        for point, config in configs.items():
            label = f'{point}-r{repeat}'
            snapshot = resources(out, RUN_BUDGET)
            hw = subprocess.run(['/usr/sbin/system_profiler', 'SPHardwareDataType'],
                                capture_output=True, text=True, check=True, timeout=10)
            chip = re.search(r'^\s*Chip: (.+)$', hw.stdout, re.MULTILINE)
            require(chip and chip[1] == conditions['hardware'] and
                    snapshot['physical_ram_bytes'] == conditions['memory_bytes'], 'frozen hardware mismatch')
            write_json(out/f'{label}-resources.json', dict(snapshot, hardware=chip[1]))
            cmd = [sys.executable, '-B', str(ROOT/'tools/run_native.py'), '--binary', str(args.binary),
                   '--config', str(config), '--data', str(args.prepared/'evaluation'),
                   '--output', str(out/label), '--mode', 'cpu_sample', '--split', 'evaluation128',
                   '--workers', '1', '--concurrency', '1', '--sample-stride', str(STRIDE),
                   '--timeout', str(TIMEOUT), '--output-budget', str(RUN_BUDGET),
                   '--query-buffer', str(QUERY_BUFFER), '--rss-limit', str(4*GIB)]
            with (out/f'{label}.log').open('wb') as log:
                proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
            receipt['runs'].append(dict(label=label, argv=cmd, exit_code=proc.returncode))
            require(proc.returncode == 0, f'{label}: native CPU sampling failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profile', 'start-manifest', 'binary', 'prepared', 'raw', 'builder', 'output'):
        parser.add_argument('--'+name, type=lambda s: Path(s).resolve(), required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    receipt = dict(argv=sys.argv, exit_code=1, runs=[], profile_sha256=PROFILE_SHA,
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
