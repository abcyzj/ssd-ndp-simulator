"""Run the standalone fixed-batch search against validated dataset/index/truth artifacts."""
import argparse
import json
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
from time import monotonic

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
        explicit = args.mode is not None
        mode = args.mode or 'benchmark'
        if explicit:
            from native_observation import Budget, binding_for, build_identity, measurement, seal
        manifest.update(mode=mode, split=args.split, sample_stride=args.sample_stride,
                        output_budget_bytes=args.output_budget, query_buffer_bytes=args.query_buffer)
        if not 0 < args.query_buffer <= args.output_budget <= 2**32-1 or not 0 < args.sample_stride <= 2**32-1:
            raise ValueError('invalid observation budget or sample stride')
        if args.sample_stride != 1 and mode != 'cpu_sample':
            raise ValueError('sample stride applies only to CPU sampling')
        if explicit and args.detail8 and mode != 'static_capture':
            raise ValueError('detail8 is supported with static_capture; run timing modes separately')
        if args.semantic_log and explicit:
            raise ValueError('use --detail8 for explicit observation modes')
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
        shards = out/'native-shards'
        if explicit:
            build = build_identity(args.binary, manifest['executable_sha256'])
            binding = binding_for(build, [(Path(p), h) for p, h in files], shape, search)
            manifest['build'] = build
            if mode == 'cpu_sample' and args.split != 'fixture' and platform.system() == 'Darwin':
                manifest['cpu_host'] = dict(
                    hardware=subprocess.check_output(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'],
                                                     text=True, timeout=10).strip(),
                    memory_bytes=int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'],
                                                             text=True, timeout=10)),
                    os=manifest['platform'], frequency=manifest['frequency'], affinity=manifest['affinity'])
        if mode in ('static_capture', 'cpu_sample'):
            if manifest['free_disk_bytes'] < args.output_budget:
                raise ValueError('insufficient disk for observation budget')
            if manifest['resident_payload_bytes'] + args.workers * args.query_buffer >= args.rss_limit:
                raise ValueError('input and observation buffers exceed RSS budget')
            shards.mkdir()
            argv.extend((mode, str(shards), str(args.output_budget), str(args.query_buffer), str(args.sample_stride)))
        elif mode == 'uninstrumented':
            argv.append(mode)
        if args.semantic_log:
            argv.append('--semantic-log')
        if args.detail8:
            argv.append('--detail8')
        manifest['semantic_log'] = args.semantic_log
        deadline = monotonic() + args.timeout
        manifest['pressure_before_process'] = pressure()
        manifest['command'] = measure(argv, out/'process.json', args.timeout, args.rss_limit, poll_seconds=.01)
        manifest['pressure_after_process'] = pressure()
        if not manifest['command']['passed']:
            raise ValueError('native command failed or exceeded its budget; see process.log')
        if sha(args.binary) != manifest['executable_sha256']:
            raise ValueError('native executable changed during the run')
        if explicit and (out/'process.log').stat().st_size > args.output_budget:
            raise ValueError('native batch report exceeds output budget')
        report = json.loads((out/'process.log').read_text())
        if (report['completed'], report['workers'], report['concurrency'], report['exit_code']) != (
                shape['queries'], args.workers, args.concurrency, 0):
            raise ValueError('incomplete or mismatched native report')
        if explicit:
            if [row['original_query_id'] for row in report['queries']] != binding['identity']['query_ids']:
                raise ValueError('batch query identity mismatch')
            existing = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
            budget = Budget(args.output_budget - existing, deadline)
            budget.json(out/'batch.json', report, metadata=False)
            measurement(out, shards, binding, report, mode, args.sample_stride, args.split, budget)
            if mode == 'static_capture':
                staged = seal(out, shards, binding, report, budget)
                budget.check()
                staged.rename(out/'package')
            manifest['observation_complete'] = True
        else:
            write_json(out/'batch.json', report)
        manifest.update(passed=True, exit_code=0, paging_eligible=report['paging_eligible'])
    except Exception as exc:
        pending_tail = out/'package.partial'/'tail.json'
        if pending_tail.is_file():
            pending_tail.rename(out/'rejected-tail.json')
        manifest.update(error=str(exc), observation_complete=False if args.mode else None)
        print(f'run_native: {exc}', file=sys.stderr)
    finally:
        manifest['artifacts_sha256'] = {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
        if args.mode:
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            manifest['launcher_self_peak_rss_bytes'] = peak * (1 if platform.system() == 'Darwin' else 1024)
        write_json(out/'manifest.json', manifest)
    return manifest['exit_code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('config', 'data', 'binary', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2, 4), default=1)
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--semantic-log', action='store_true', help='include reference search trace; timings are diagnostic')
    parser.add_argument('--mode', choices=('uninstrumented', 'benchmark', 'cpu_sample', 'static_capture'))
    parser.add_argument('--split', choices=('fixture', 'tuning32', 'evaluation128'), default='fixture')
    parser.add_argument('--sample-stride', type=int, default=1)
    parser.add_argument('--detail8', action='store_true', help='extra trace for the first eight input queries')
    parser.add_argument('--output-budget', type=int, default=512*1024**2)
    parser.add_argument('--query-buffer', type=int, default=32*1024**2)
    parser.add_argument('--timeout', type=int, default=600, help='timeout in seconds for native process and observation publication')
    parser.add_argument('--rss-limit', type=int, default=4*1024**3, help='native child peak RSS budget in bytes')
    args = parser.parse_args()
    try:
        return run(args)
    except OSError as exc:
        parser.exit(1, f'run_native: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
