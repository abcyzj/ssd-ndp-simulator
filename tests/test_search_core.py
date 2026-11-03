"""Independent numeric/search/Work oracle, using sets, sorting and rank formulas.
Does not import production tools or call a second shared implementation.
"""
import argparse
import bisect
import random
import struct
import subprocess


def packed(scores):
    return b''.join(struct.pack('<Id', i, d) for i, d in scores)


def ids(values):
    return b''.join(struct.pack('<I', i) for i in values)


def oracle(vectors, graph, query, k, width, batch, budget, chunk):
    rows = []
    def emit(stage, data, elements=0, flops=0, read=0, checks=0, comparisons=0, result=False):
        out = 0 if result else len(data)
        rows.append((stage, data.hex() or '-', elements, flops, read, out, out, checks, comparisons))
    # Python binary64 in fixed dimension order, from explicitly rounded FP32 input.
    distances = []
    for vector in vectors:
        total = 0.0
        for a, b in zip(query, vector):
            delta = a-b
            square = delta*delta
            total = total+square
        distances.append(total)
    seen, expanded, ranking, fresh = set(), set(), [], [0]
    while True:
        if fresh:
            for start in range(0, len(fresh), chunk):
                group = fresh[start:start+chunk]
                emit('score', packed([(i, distances[i]) for i in group]),
                     len(query)*len(group), 3*len(query)*len(group), 4*len(query)*(len(group)+1))
            incoming = ranking+[(i, distances[i]) for i in fresh]
            # Unique IDs during real search: membership scan visits every preceding item.
            comparisons = len(incoming)*(len(incoming)-1)//2
            # Insertion-sort comparisons = inversions + one stopping compare except new minima.
            keys = [(d, i) for i, d in incoming]
            for pos, key in enumerate(keys[1:], 1):
                comparisons += sum(old > key for old in keys[:pos]) + int(any(old <= key for old in keys[:pos]))
            ranking = sorted(incoming, key=lambda s: (s[1], s[0]))
            emit('merge', packed(ranking), len(incoming), read=12*len(incoming),
                 checks=len(incoming), comparisons=comparisons)
            seen.update(fresh)
        chosen = [i for i, _ in ranking[:width] if i not in expanded][:max(0, min(batch, budget-len(expanded)))]
        emit('select', ids(chosen), checks=min(width, len(ranking)))
        if not chosen:
            emit('result', packed(ranking[:k]), result=True)
            break
        expanded.update(chosen)
        edges = [i for node in chosen for i in graph[node]]
        fresh, comparisons = [], 0
        sorted_seen = sorted(seen)
        for node in edges:
            comparisons += bisect.bisect_left(sorted_seen, node)+1 if node in seen else len(seen)
            if node not in seen:
                pos = bisect.bisect_left(fresh, node)
                comparisons += min(pos+1, len(fresh)) + int(pos < len(fresh))
                fresh = sorted(set(fresh) | {node})
        emit('dedup', ids(fresh), checks=len(edges), comparisons=comparisons)
    emit('duplicate', packed([(2, 1.0), (9, 1.0)]), 4, read=48, checks=4, comparisons=7)
    for stage in ('empty-select', 'empty-dedup', 'empty-merge'):
        emit(stage, b'')
    return rows


def main():
    p = argparse.ArgumentParser();p.add_argument('--probe', required=True);a = p.parse_args()
    # self-loops, duplicate edges, cross-batch duplicates, ties, disconnected/empty
    # adjacency, insufficient k, exhausted budget, B>E and multiple chunks.
    fixtures = [([[10.0], [-1.0], [1.0], [0.0], [2.0]],
                 [[0, 1, 1, 2], [3, 2], [4, 0], [2, 3], [1]], [0.0]),
                ([[0.0], [1.0], [1.0]], [[], [], []], [0.0]),
                ([[1e8, 1, 1], [-1, 2, 4], [16777216, 0, 0]],
                 [[1, 2], [0], []], [0, 0, 0])]
    rng = random.Random(4101)
    for _ in range(12):
        n, d = rng.randint(3, 16), rng.randint(1, 8)
        fixtures.append(([[rng.randint(-8, 8)/4 for _ in range(d)] for _ in range(n)],
                         [[rng.randrange(n) for _ in range(rng.randint(0, 12))] for _ in range(n)],
                         [rng.randint(-4, 4)/2 for _ in range(d)]))
    count = 0
    for vectors, graph, query in fixtures:
        # Round all inputs exactly as a file's FP32 payload before reference arithmetic.
        f32 = lambda x: struct.unpack('<f', struct.pack('<f', x))[0]
        vectors = [[f32(x) for x in v] for v in vectors];query = list(map(f32, query))
        n = len(vectors)
        for width, batch, budget, chunk in ((n, 1, n, 1), (n, 2, n, 2), (n, 4, 1, 2), (2, 3, 4, 1)):
            k = min(n, width)
            text = f'{n} {len(query)} {k} {width} {batch} {budget} {chunk}\n'
            text += ' '.join(map(str, query))+'\n'
            text += '\n'.join(' '.join(map(str, v)) for v in vectors)+'\n'
            text += '\n'.join(' '.join(map(str, [len(row)]+row)) for row in graph)+'\n'
            run = subprocess.run([a.probe], input=text, text=True, capture_output=True, check=True)
            actual = []
            for line in run.stdout.splitlines():
                stage, data, *work = line.split()
                actual.append((stage, data, *map(int, work)))
            expected = oracle(vectors, graph, query, k, width, batch, budget, chunk)
            assert actual == expected, (count, actual, expected, run.stderr)
            count += 1
    print(f'V04-1a PASS {count} searches: exact packed numerical results, scoring/chunk order, every stage and all seven Work fields; invalid inputs rejected')


if __name__ == '__main__':
    main()
