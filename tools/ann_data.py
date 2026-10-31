"""M3.1 binary dataset generator and independent offline exact L2 oracle.

No imports from the C++ search/operator implementation. Distances use explicitly
ordered Python binary64 subtraction/multiply/add on decoded binary32 inputs.
"""
import argparse
import hashlib
import json
import math
import random
import struct
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def integer(value, name, minimum=1, maximum=2**32-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def generate(directory, n=2048, d=32, degree=16, queries=32, seed=42, fixture='smoke'):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if fixture in ('branch', 'mutated'):
        n, d, degree, queries = 5, 1, 2, 1
        vectors = [[10.0], [1.0 if fixture == 'branch' else 5.0], [3.0], [2.0], [4.0]]
        query_rows = [[0.0]]
        graph = [[1, 2], [3], [4], [], []]
    else:
        for name, v in [('N', n), ('d', d), ('R', degree), ('queries', queries)]:
            integer(v, name)
        if degree >= n:
            raise ValueError('R must be less than N')
        rng = random.Random(seed)
        # Exactly representable binary32 fractions; no platform-dependent random float conversion.
        vectors = [[rng.randrange(-1024, 1025) / 128 for _ in range(d)] for _ in range(n)]
        query_rows = [[rng.randrange(-1024, 1025) / 128 for _ in range(d)] for _ in range(queries)]
        graph = []
        for i in range(n):
            adjacent = {(i + 1) % n}  # Directed ring guarantees reachability.
            while len(adjacent) < degree:
                j = rng.randrange(n)
                if j != i:
                    adjacent.add(j)
            graph.append(sorted(adjacent))
    pack_floats = lambda rows: b''.join(struct.pack('<' + 'f' * d, *row) for row in rows)
    (directory / 'vectors.f32').write_bytes(pack_floats(vectors))
    (directory / 'queries.f32').write_bytes(pack_floats(query_rows))
    (directory / 'adjacency.u32').write_bytes(b''.join(
        struct.pack('<' + 'I' * (degree + 1), len(row), *row, *([0] * (degree - len(row)))) for row in graph))
    meta = dict(format='ndpsim-ann-v1', count=n, dimensions=d, degree=degree, queries=queries,
                seed=seed, generator='python_random_integer_grid_ring_random_v1', fixture=fixture,
                sha256={name: digest(directory / name) for name in ('vectors.f32', 'queries.f32', 'adjacency.u32')})
    write_json(directory / 'dataset.json', meta)
    return meta


def read_dataset(directory):
    directory = Path(directory)
    m = json.loads((directory / 'dataset.json').read_text())
    if m['format'] != 'ndpsim-ann-v1':
        raise ValueError('Unsupported simulation dataset format; dataset-v2 is a preparation artifact; index/truth binding is required before simulation')
    n, d, r, q = [integer(m[key], key, 0 if key == 'degree' else 1) for key in ('count', 'dimensions', 'degree', 'queries')]
    if r >= n:
        raise ValueError('R must be less than N')
    raw = {}
    for name, size in [('vectors.f32', n*d*4), ('queries.f32', q*d*4), ('adjacency.u32', n*(r+1)*4)]:
        raw[name] = (directory / name).read_bytes()
        if len(raw[name]) != size or digest(directory / name) != m['sha256'][name]:
            raise ValueError(f'size/hash mismatch: {name}')
    def floats(name):
        rows = list(struct.iter_unpack('<' + 'f'*d, raw[name]))
        if any(not math.isfinite(x) for row in rows for x in row):
            raise ValueError('nonfinite FP32 input')
        return rows
    vectors, queries = floats('vectors.f32'), floats('queries.f32')
    for row in struct.iter_unpack('<' + 'I'*(r+1), raw['adjacency.u32']):
        degree = row[0]
        if degree > r or any(x >= n for x in row[1:degree+1]):
            raise ValueError('invalid adjacency degree/ID')
    return m, vectors, queries


def exact_truth(directory, k):
    m, vectors, queries = read_dataset(directory)
    integer(k, 'k', maximum=m['count'])
    truth = []
    for query in queries:
        scores = []
        for node_id, vector in enumerate(vectors):
            total = 0.0
            for x, y in zip(query, vector):
                delta = float(x) - float(y)
                total = total + delta * delta
            scores.append(dict(id=node_id, distance=total))
        truth.append(sorted(scores, key=lambda s: (s['distance'], s['id']))[:k])
    value = dict(method='independent_python_exhaustive_ordered_fp64_l2', k=k,
                 vectors_sha256=m['sha256']['vectors.f32'], queries_sha256=m['sha256']['queries.f32'], top_k=truth)
    write_json(Path(directory) / 'truth.json', value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['generate', 'truth'])
    p.add_argument('directory', type=Path)
    p.add_argument('--fixture', choices=['smoke', 'branch', 'mutated'], default='smoke')
    for name, default in [('n', 2048), ('d', 32), ('degree', 16), ('queries', 32), ('seed', 42), ('k', 10)]:
        p.add_argument('--' + name, type=int, default=default)
    a = p.parse_args()
    if a.action == 'generate':
        print(json.dumps(generate(a.directory, a.n, a.d, a.degree, a.queries, a.seed, a.fixture)))
    else:
        print(json.dumps(exact_truth(a.directory, a.k)))



def validate_dataset(directory):
    """v1 simulation validation with bounded decoding; legacy oracle stays separate."""
    directory = Path(directory)
    m = json.loads((directory/'dataset.json').read_text())
    if m['format'] != 'ndpsim-ann-v1': raise ValueError('Unsupported dataset format')
    n,d,r,q = [integer(m[k],k,0 if k=='degree' else 1) for k in ('count','dimensions','degree','queries')]
    if r >= n: raise ValueError('R must be less than N')
    for name, size in [('vectors.f32',n*d*4),('queries.f32',q*d*4),('adjacency.u32',n*(r+1)*4)]:
        path = directory/name
        if path.stat().st_size != size or digest(path) != m['sha256'][name]: raise ValueError('size/hash mismatch: '+name)
        with path.open('rb') as f:
            if name.endswith('.f32'):
                for block in iter(lambda:f.read(4*1024*1024),b''):
                    if any(not math.isfinite(x[0]) for x in struct.iter_unpack('<f',block)):
                        raise ValueError('nonfinite FP32 input')
            else:
                for _ in range(n):
                    degree = struct.unpack('<I',f.read(4))[0]
                    if degree > r: raise ValueError('invalid adjacency degree')
                    # Bound decoding even for deliberately huge fixed slots.
                    for start in range(0,r,262144):
                        width = min(262144,r-start)
                        for j,(node,) in enumerate(struct.iter_unpack('<I',f.read(width*4))):
                            if start+j < degree and node >= n: raise ValueError('invalid adjacency ID')
    return m


if __name__ == '__main__':
    main()
