"""Validate a complete local T0 evidence bundle; never infers later-stage AC passes."""
import argparse
import json
import mmap
from pathlib import Path
import shutil
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_m0 import ROOT, sha, write


def summarize(evidence):
    checks = []
    def check(name, passed, **detail):
        checks.append(dict(name=name, passed=bool(passed), **detail))
    def read(path):
        return json.loads(Path(path).read_text())
    lock = read(ROOT/'configs/benchmarks/datasets.lock.json')
    for dataset, roles in [('sift', ['base', 'query', 'learn', 'groundtruth']), ('deep', ['base', 'query'])]:
        for role in roles:
            report = read(evidence/f'{dataset}-{role}.json')
            frozen = lock['datasets']['sift1m' if dataset == 'sift' else 'deep10m']['files'][role]
            check(f'{dataset}-{role}-content', report['sha256'] == frozen['sha256'] == sha(report['path'])
                  and all(report[k] == frozen[k] for k in ('bytes', 'count', 'dimensions', 'format')))
    check('query-selection-bound-to-lock', sha(ROOT/lock['query_selection_file']) == lock['query_selection_sha256'])
    builder = read(evidence/'builder-rebuild/manifest.json')
    check('locked-builder-rebuild', builder['passed'] and len(builder['commands']) == 5
          and builder['lock_sha256'] == sha(ROOT/'configs/benchmarks/m0-builder.lock.json'))
    groups = {
        'faiss-single': (1, [10000]), 'faiss-deep-single': (1, [10000]),
        'faiss-sift-final': (3, [10000, 100000, 1000000]), 'faiss-deep': (3, [10000, 100000, 1000000]),
        'init-2g': (3, [64*1024**2, 256*1024**2, 1024**3]),
        'init-16g': (3, [64*1024**2, 256*1024**2, 1024**3]),
        'init-sift-layout': (3, [1024000000]), 'init-deep-layout': (3, [10240000000]),
    }
    summaries = {}
    for name, (reps, sizes) in groups.items():
        directory = evidence/name
        manifest = read(directory/'manifest.json')
        expected = {f'{size}-{i}.json' for size in sizes for i in range(reps)}
        check(name+'-complete-matrix', {Path(r['evidence']).name for r in manifest['runs']} == expected
              and manifest['passed'] and all(r['passed'] for r in manifest['runs']))
        rows, degrees = [], []
        for filename in sorted(expected):
            p = directory/filename
            row = read(p)
            valid = row['passed'] and row['exit_code'] == 0 and not row['timed_out'] and row['rss_budget_pass']
            valid = valid and sha(p.with_suffix('.log')) == row['log_sha256']
            valid = valid and all(sha(artifact) == digest for artifact, digest in row.get('artifacts', {}).items())
            if name.startswith('faiss'):
                graph = read(directory/(p.stem+'.graph.json'))
                valid = valid and graph['n'] == int(p.stem.split('-')[0]) and graph['query_rows'] == 32
                valid = valid and graph['d'] == (96 if 'deep' in name else 128)
                valid = valid and graph['threads'] == (1 if 'single' in name else 4)
                valid = valid and graph['reachable'] == graph['n'] and graph['recall_at_10'] >= .95
                valid = valid and graph['parameters'] == dict(R=64, GK=64, L=200, C=500, build_type=1, search_L=256)
                histogram = [0]*65
                with (directory/(p.stem+'.u32')).open('rb') as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
                        valid = valid and len(data) == graph['n']*65*4
                        for offset in range(0, len(data), 65*4):
                            degree = struct.unpack_from('<I', data, offset)[0]
                            if degree > 64:
                                valid = False
                                break
                            histogram[degree] += 1
                valid = valid and sum(histogram) == graph['n'] and sum(i*n for i,n in enumerate(histogram)) == graph['edges']
                degrees.append(dict(n=graph['n'], degree_histogram=histogram,
                                    mean_degree=graph['edges']/graph['n'], **{k: graph[k] for k in
                                    ('degree_min', 'degree_max', 'recall_at_10', 'reachable')}))
            check(name+'/'+filename, valid)
            rows.append(row)
        summaries[name] = dict(samples=len(rows), wall_seconds=[r['wall_seconds'] for r in rows],
                               peak_rss_bytes=[r['peak_rss_bytes'] for r in rows], degrees=degrees)
    ctest = (evidence/'ctest-last.log').read_text()
    check('ctest-53', '100% tests passed out of 53' in ctest)
    negative = read(evidence/'init-invalid-geometry.json')
    check('invalid-preload-refused', negative['exit_code'] != 0 and not negative['timed_out']
          and 'invalid M0 probe geometry/layout' in (evidence/'init-invalid-geometry.log').read_text())
    budgets = read(ROOT/'configs/benchmarks/v0.2-budgets.json')
    hardware = read(evidence/'hardware.json')
    check('physical-ram-confirmed', hardware['physical_ram']['exit_code'] == 0
          and hardware['physical_ram']['bytes'] == budgets['physical_ram_bytes'])
    check('disk-reserve-snapshot', shutil.disk_usage(ROOT).free >= budgets['disk_reserve_bytes'])
    result = dict(scope='T0 / M0 only; no V0.2 scale query AC or full V0.1 AC',
                  passed=all(c['passed'] for c in checks), assertions=checks, samples=summaries,
                  disk_peak_bytes=None, disk_peak_note='not continuously monitored; hardware.json contains free-space snapshots',
                  limitations=['Deep10M full graph resource gate remains open on 16GiB host',
                               'some builder samples overlapped other M0 work; wall time is observed preparation cost, not isolated CPU benchmark',
                               'no formal T1 schema/conversion or T2 index adapter; no T3+ implementation',
                               'no redistribution license for benchmark data inferred'])
    write(evidence/'assertions.json', result)
    write(evidence/'ac-matrix.json', dict(scope='prerequisites only', items={
        'V0.2-AC03': 'M0 builder/export feasibility evidence; formal T2 binding/quality validation still required',
        'V0.2-AC07': 'M0 layout preload resource probe only; formal input/profile/query integration still required',
        'V0.2-AC09': 'not executed: full SIFT query matrix',
        'V0.2-AC10': 'not executed: full Deep10M build and query matrix',
        'V0.1-AC': 'CTest includes quick subsets; full AC not rerun'}))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence', type=Path, default=ROOT/'results/v0.2-m0')
    a = p.parse_args()
    result = summarize(a.evidence)
    print(json.dumps(dict(passed=result['passed'], checks=len(result['assertions']))))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
