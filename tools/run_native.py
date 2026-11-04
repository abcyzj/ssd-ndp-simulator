"""Run the standalone fixed-batch search against validated dataset/index/truth artifacts."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from ann_data import write_json
from simulation_input import load_v2
from verify_m0 import measure, sha

SEARCH_FIELDS = ('entry', 'k', 'width', 'batch', 'budget', 'chunk')


def pressure():
    if platform.system() != 'Darwin':
        return {'status': 'Unsupported'}
    proc = subprocess.run(['/usr/bin/memory_pressure', '-Q'], capture_output=True, text=True)
    return dict(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def run(args):
    out = args.output.resolve()
    out.mkdir(parents=True)
    manifest = dict(schema='ndpsim-native-run-v1', passed=False, exit_code=1,
                    launcher_argv=sys.argv, cwd=str(Path.cwd()), platform=platform.platform(),
                    residency='fully read and SHA256-scanned owned byte arrays; per-worker warmup before batch',
                    frequency='Unsupported', affinity='uncontrolled')
    try:
        config = json.loads(args.config.read_text())
        search = config['search']
        if set(search) != set(SEARCH_FIELDS) or any(type(search[k]) is not int for k in SEARCH_FIELDS):
            raise ValueError('expected integer entry/k/width/batch/budget/chunk')
        if not 1 <= args.workers <= args.concurrency or args.timeout <= 0 or args.rss_limit <= 0:
            raise ValueError('invalid concurrency or resource budget')
        manifest.update(config=config, config_sha256=sha(args.config),
                        data=str(args.data.resolve()), workers=args.workers, concurrency=args.concurrency,
                        executable_sha256=sha(args.binary),
                        free_disk_bytes=shutil.disk_usage(out).free)
        shape, paths, identity = load_v2(args.data.resolve(), config, args.config.resolve().parent)
        manifest.update(shape=shape, input=identity)
        base = identity['dataset']['files']['base']
        query = identity['dataset']['files']['query']
        graph, truth = identity['index']['graph'], identity['truth']
        files = [
            (paths['vector_file'], base['sha256']),
            (paths['query_file'], query['sha256']),
            (paths['graph_file'], graph['sha256']),
            (str(Path(identity['directories']['truth']) / truth['ids']['path']), truth['ids']['sha256']),
            (str(args.data.resolve() / query['mapping']), query['mapping_sha256']),
        ]
        manifest['resident_payload_bytes'] = sum(Path(path).stat().st_size for path, _ in files)
        if manifest['resident_payload_bytes'] >= args.rss_limit:
            raise ValueError('resident input exceeds RSS budget')
        argv = [str(args.binary.resolve()), *map(str, (
            shape['count'], shape['dimensions'], shape['queries'], shape['degree'], truth['k'],
            *(search[k] for k in SEARCH_FIELDS), args.workers, args.concurrency)),
            *(item for pair in files for item in pair)]
        if args.semantic_log:
            argv.append('--semantic-log')
        manifest['semantic_log'] = args.semantic_log
        manifest['pressure_before_process'] = pressure()
        manifest['command'] = measure(argv, out/'process.json', args.timeout, args.rss_limit, poll_seconds=.01)
        manifest['pressure_after_process'] = pressure()
        if not manifest['command']['passed']:
            raise ValueError('native command failed or exceeded its budget; see process.log')
        if sha(args.binary) != manifest['executable_sha256']:
            raise ValueError('native executable changed during the run')
        report = json.loads((out/'process.log').read_text())
        if (report['completed'], report['workers'], report['concurrency'], report['exit_code']) != (
                shape['queries'], args.workers, args.concurrency, 0):
            raise ValueError('incomplete or mismatched native report')
        write_json(out/'batch.json', report)
        manifest.update(passed=True, exit_code=0, paging_eligible=report['paging_eligible'])
    except Exception as exc:
        manifest['error'] = str(exc)
        print(f'run_native: {exc}', file=sys.stderr)
    finally:
        manifest['artifacts_sha256'] = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
        write_json(out/'manifest.json', manifest)
    return manifest['exit_code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('config', 'data', 'binary', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2, 4), default=1)
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--semantic-log', action='store_true', help='include reference search trace; timings are diagnostic')
    parser.add_argument('--timeout', type=int, default=600, help='native process timeout in seconds')
    parser.add_argument('--rss-limit', type=int, default=4*1024**3, help='native child peak RSS budget in bytes')
    args = parser.parse_args()
    try:
        return run(args)
    except OSError as exc:
        parser.exit(1, f'run_native: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
