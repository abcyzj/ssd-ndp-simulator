"""Run the preregistered M0 feasibility samples sequentially, with evidence.

Use verified full raw inputs. These prefix probes do not fulfill T1/T2/T6/T7.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_m0 import measure, sha, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=['builder', 'init'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--build', type=Path, default=Path('build/delivery'))
    p.add_argument('--builder', type=Path, default=Path('build/m0-faiss/m0_faiss_probe'))
    p.add_argument('--base', type=Path)
    p.add_argument('--query', type=Path)
    p.add_argument('--format', choices=['fvecs', 'fbin'])
    p.add_argument('--dimensions', type=int, choices=[96, 128])
    p.add_argument('--sizes', type=int, nargs='+')
    p.add_argument('--repetitions', type=int, default=3)
    p.add_argument('--blocks', type=int, choices=[2048, 16384], default=2048)
    p.add_argument('--threads', type=int, choices=[1, 4], default=4)
    a = p.parse_args()
    if a.repetitions < 1 or a.output.exists():
        p.error('positive repetitions and unused output directory required')
    if a.kind == 'builder' and any(v is None for v in (a.base, a.query, a.format, a.dimensions)):
        p.error('builder requires base/query/format/dimensions')
    a.output.mkdir(parents=True)
    runs = []
    metadata = dict(scope='M0 probe only', repetitions=a.repetitions, runs=runs)
    if a.kind == 'builder':
        metadata['inputs'] = {str(f.resolve()): sha(f) for f in (a.base, a.query, a.builder)}
    else:
        paths = json.loads((a.build/'sst-paths.json').read_text())
        metadata['inputs'] = {str(f.resolve()): sha(f) for f in (a.build/'libndpsim.so', Path(paths['sst']))}
    write(a.output/'manifest.json', metadata)
    for size in a.sizes or ([10000, 100000, 1000000] if a.kind == 'builder' else [64*1024**2, 256*1024**2, 1024**3]):
        for repeat in range(a.repetitions):
            stem = a.output/f'{size}-{repeat}'
            if a.kind == 'builder':
                argv = [str(a.builder.resolve()), str(a.base.resolve()), str(a.query.resolve()), a.format,
                        str(size), str(a.dimensions), str(a.threads), str(stem)+'.u32', str(stem)+'.graph.json']
                r = measure(argv, str(stem)+'.json', 8*3600, 8*1024**3,
                            {'OMP_NUM_THREADS': str(a.threads), 'VECLIB_MAXIMUM_THREADS': '1'})
                if r['passed']:
                    graph = json.loads(Path(str(stem)+'.graph.json').read_text())
                    r['assertions'] = dict(reachable=graph['reachable'] == size,
                                           degree=graph['degree_max'] <= 64,
                                           quality=graph['recall_at_10'] >= .95)
                    r['artifacts'] = {str(f): sha(f) for f in (Path(str(stem)+'.u32'), Path(str(stem)+'.graph.json'))}
            else:
                argv = [paths['sst'], '--add-lib-path='+str(a.build.resolve()), 'configs/regression/m0_preload.py']
                r = measure(argv, str(stem)+'.json', 3600, 4*1024**3,
                            {'NDPSIM_M0_PRELOAD_BYTES': str(size), 'NDPSIM_M0_BLOCKS': str(a.blocks)})
                if r['passed']:
                    lines = Path(str(stem)+'.log').read_text().splitlines()
                    rows = [json.loads(line.removeprefix('M0_PRELOAD ')) for line in lines if line.startswith('M0_PRELOAD ')]
                    r['assertions'] = dict(single_summary=len(rows) == 1)
                    if len(rows) == 1:
                        row = rows[0]
                        r['assertions'].update(bytes=row['bytes'] == size, writes=row['writes'] == size//4096,
                                               drained=row['drained'], time=row['origin_ns'] > 0,
                                               capacity=row['namespace_sectors']*512 >= size)
                        r['preload'] = row
            r['passed'] = r['passed'] and all(r.get('assertions', {}).values())
            write(str(stem)+'.json', r)
            runs.append(dict(evidence=str(stem)+'.json', passed=r['passed']))
            metadata['passed'] = all(x['passed'] for x in runs)
            write(a.output/'manifest.json', metadata)
            print(f'{size} repeat={repeat}: {r["passed"]}, {r["wall_seconds"]:.3f}s, RSS={r["peak_rss_bytes"]/1024**2:.1f}MiB', flush=True)
            if not r['passed']:
                return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
