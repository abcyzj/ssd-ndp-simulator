"""Exercise the production binary, independently checking result, Work and admission."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from test_search_core import oracle


def fixture(root):
    vectors = [[10., 0.], [-1., 2.], [1., 2.], [0., 1.], [2., 0.], [0., 0.]]
    graph = [[0, 1, 1, 2], [3, 2], [4, 0], [2, 3], [1], []]
    queries = [[float(i % 4), float(i - 5)] for i in range(11)]
    original_ids = list(range(31, 9, -2))
    truth = [sorted(range(len(vectors)), key=lambda i: (
        sum((a-b)**2 for a, b in zip(q, vectors[i])), i))[:4] for q in queries]
    graph_slots = [[len(row), *row, *([0]*(4-len(row)))] for row in graph]
    files = []
    for name, code, rows in (('vectors', 'f', vectors), ('queries', 'f', queries),
                             ('graph', 'I', graph_slots), ('truth', 'I', truth),
                             ('query-ids', 'I', [original_ids])):
        payload = b''.join(struct.pack('<'+code*len(row), *row) for row in rows)
        path = root/name
        path.write_bytes(payload)
        files.extend((str(path), hashlib.sha256(payload).hexdigest()))
    expected = []
    for q in queries:
        stages = oracle(vectors, graph, q, k=4, width=4, batch=2, budget=3, chunk=1)
        stages = stages[:next(i for i, row in enumerate(stages) if row[0] == 'result')+1]
        work = [sum(row[i] for row in stages) for i in range(2, 9)]
        top = list(struct.iter_unpack('<Id', bytes.fromhex(stages[-1][1])))
        selected = [row[1] for row in stages if row[0] == 'select' and row[1] != '-']
        scores = [row[1] for row in stages if row[0] == 'score']
        expected.append((top, work, sum(len(h)//8 for h in selected), len(scores), len(selected)))
    return files, expected, truth, original_ids


def check(report, expected, truth, original_ids, workers, concurrency):
    rows = report['queries']
    n = len(expected)
    assert len(rows) == report['completed'] == n
    assert report['exit_code'] == 0
    assert report['workers'] == workers and report['concurrency'] == concurrency
    completions = sorted(r['visible_ns'] for r in rows)
    assert report['window_ns'] == report['drain_end_ns'] == completions[-1]
    assert report['qps'] == n * 1e9 / completions[-1]
    assert report['thread_cpu_ns'] == sum(r['thread_cpu_ns'] for r in rows)
    latencies = sorted(r['latency_ns'] for r in rows)
    for p in (50, 95):
        assert report[f'p{p}_ns'] == latencies[(p*n + 99)//100 - 1]
    assert sorted(rows, key=lambda r: r['worker_start_ns']) == rows
    for i, (r, (top, work, expansions, chunks, rounds)) in enumerate(zip(rows, expected)):
        assert r['query_id'] == i and r['original_query_id'] == original_ids[i]
        assert [(x['id'], x['distance']) for x in r['top_k']] == top
        assert list(r['work'].values()) == work
        assert (r['expanded'], r['score_chunks'], r['scored'], r['expansion_batches']) == (
            expansions, chunks, chunks, rounds)
        found = {node for node, _ in top}
        assert r['recall_at_k'] == len(found.intersection(truth[i]))/4
        assert r['one_recall_at_r'] == int(truth[i][0] in found)
        assert r['insufficient_results'] == (len(found) < 4)
        admission = 0 if i < concurrency else completions[i-concurrency]
        assert 0 <= r['worker'] < workers
        assert 0 == r['offered_ns'] <= r['admission_ns'] == admission <= r['worker_start_ns'] <= r['visible_ns']
        assert r['latency_ns'] == r['visible_ns'] - admission
        assert r['worker_wait_ns'] == r['worker_start_ns'] - admission
    for worker in range(workers):
        spans = [r for r in rows if r['worker'] == worker]
        assert all(a['visible_ns'] <= b['worker_start_ns'] for a, b in zip(spans, spans[1:]))


def main():
    binary = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        files, expected, truth, original_ids = fixture(Path(tmp))
        shape_and_search = [6, 2, 11, 4, 4, 0, 4, 4, 2, 3, 1]
        for workers, concurrency in ((1, 1), (2, 4), (4, 32), (4, 4)):
            cmd = [binary, *map(str, (*shape_and_search, workers, concurrency)), *files]
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=20)
            check(json.loads(proc.stdout), expected, truth, original_ids, workers, concurrency)
        invalid = [
            (12, '3', 'invalid search'),
            (13, '1', 'invalid search'),
            (15, '0'*64, 'SHA256 mismatch'),
            (14, files[2], 'size mismatch'),
        ]
        # Corrupt an adjacency row while keeping a valid hash.
        graph = Path(files[4])
        bad = struct.pack('<I', 5) + graph.read_bytes()[4:]
        graph.write_bytes(bad)
        invalid.append((19, hashlib.sha256(bad).hexdigest(), 'invalid adjacency degree'))
        for index, arg, error in invalid:
            rejected = cmd.copy()
            rejected[index] = arg
            proc = subprocess.run(rejected, capture_output=True, text=True, timeout=20)
            assert proc.returncode == 1 and not proc.stdout and error in proc.stderr, proc
        print('V04-1b PASS: 4 worker/concurrency configurations, independent top-k/Recall/Work, FIFO and 5 failures')


if __name__ == '__main__':
    main()
