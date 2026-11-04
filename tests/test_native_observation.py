"""Check the native observation path with independent search and corruption oracles."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from test_native_search import check, fixture
from test_search_core import oracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from native_observation import Budget, binding_for, build_identity, compare_inventory, measurement, seal
from v04_contract import validate_package


def rejects(action, error):
    try:
        action()
    except ValueError as exc:
        assert error in str(exc), exc
    else:
        raise AssertionError(f'expected rejection: {error}')


def check_operations(path, files, original_ids):
    vectors, queries = [list(struct.iter_unpack('<ff', Path(files[i]).read_bytes()))
                        for i in (0, 2)]
    graph = [list(row[1:1+row[0]]) for row in struct.iter_unpack('<IIIII', Path(files[4]).read_bytes())]
    per_query = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        per_query.setdefault(row['query'], []).append(row)
    assert list(per_query) == original_ids
    names = dict(score='SCORE_L2', merge='MERGE_CANDIDATES', select='SELECT_FRONTIER',
                 dedup='DEDUP_NEIGHBORS', result='FINISH')
    for q, query_id in zip(queries, original_ids):
        want = []
        for stage, packed, *work in oracle(vectors, graph, q, 4, 4, 2, 3, 1):
            if stage == 'duplicate':
                break
            payload = b'' if packed == '-' else bytes.fromhex(packed)
            if stage == 'score' and want and want[-1][0] == 'SCORE_L2':
                _, prior, counters = want.pop()
                payload = prior + payload
                work = [a+b for a, b in zip(counters, work)]
            want.append((names[stage], payload, work))
        actual = [r for r in per_query[query_id] if r['operator'] in names.values()]
        assert len(actual) == len(want)
        keys = 'elements flops read_bytes write_bytes output_bytes candidate_checks comparisons'.split()
        for row, (name, payload, work) in zip(actual, want):
            assert row['operator'] == name
            assert row['expected']['output']['sha256'] == hashlib.sha256(payload).hexdigest()
            assert row['expected']['work'] == dict(zip(keys, work))
        for row in per_query[query_id]:
            if row['operator'] == 'READ_ADJACENCY':
                node, = row['access']['ids']
                edges = graph[node]
                payload = struct.pack('<' + 'I'*(1+len(edges)), len(edges), *edges)
                assert row['access']['bytes'] == len(payload)
                assert row['expected']['output']['sha256'] == hashlib.sha256(payload).hexdigest()


def main():
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        files, expected, truth, original_ids = fixture(root)
        cmd = [str(binary), *map(str, (6, 2, 11, 4, 4, 0, 4, 4, 2, 3, 1, 2, 4)), *files]
        build = build_identity(binary, hashlib.sha256(binary.read_bytes()).hexdigest())
        pairs = [(Path(p), h) for p, h in zip(files[::2], files[1::2])]
        binding = binding_for(build, pairs, dict(count=6, dimensions=2, degree=4, queries=11),
                              dict(entry=0, k=4, width=4, batch=2, budget=3, chunk=1))
        for mode in ('uninstrumented', 'benchmark', 'cpu_sample', 'static_capture'):
            out = root/mode
            shards = out/'shards'
            shards.mkdir(parents=True)
            extra = []
            stride = 1
            if mode in ('cpu_sample', 'static_capture'):
                stride = 3 if mode == 'cpu_sample' else 1
                extra = [mode, str(shards), '4000000', '1000000', str(stride)]
            elif mode == 'uninstrumented':
                extra = [mode]
            run = subprocess.run(cmd+extra+['--detail8'], check=True, capture_output=True,
                                 text=True, timeout=20)
            report = json.loads(run.stdout)
            check(report, expected, truth, original_ids, 2, 4)
            assert [i for i, r in enumerate(report['queries']) if 'score_trace' in r] == list(range(8))
            measurement(out, shards, binding, report, mode, stride, 'fixture', Budget(4_000_000))
            if mode == 'cpu_sample':
                damaged = shards/'0'
                lines = damaged.read_bytes().splitlines(keepends=True)
                damaged.write_bytes(b''.join(lines[1:]))
                rejects(lambda: measurement(out, shards, binding, report, mode, stride,
                                            'fixture', Budget(4_000_000)), 'sampling plan')
            if mode == 'static_capture':
                stage = seal(out, shards, binding, report, Budget(4_000_000))
                cpu = json.loads((root/'cpu_sample'/'measurement.json').read_text())
                compare_inventory(stage/'operations.jsonl', binding['identity'], cpu)
                cpu['samples'][0]['op'] += 1
                rejects(lambda: compare_inventory(stage/'operations.jsonl', binding['identity'], cpu),
                        'sampling positions')
                check_operations(stage/'operations.jsonl', files, original_ids)
                stage.rename(out/'package')
                package = out/'package'
                with (package/'operations.jsonl').open('ab') as stream:
                    stream.write(b'\n')
                rejects(lambda: validate_package(package, out/'binding.json'), 'extra index/operation')
                fail_out = out/'failed'
                fail_out.mkdir()
                rejects(lambda: seal(fail_out, shards, binding, report, Budget(2000)), 'budget exceeded')
                assert not (fail_out/'package').exists()
                last = shards/'10'
                last.write_bytes(last.read_bytes()[:-1])
                fail_out = out/'truncated'
                fail_out.mkdir()
                rejects(lambda: seal(fail_out, shards, binding, report, Budget(4_000_000)), 'truncated')
                assert not (fail_out/'package').exists()
                last.unlink()
                fail_out = out/'missing'
                fail_out.mkdir()
                try:
                    seal(fail_out, shards, binding, report, Budget(4_000_000))
                except FileNotFoundError:
                    assert not (fail_out/'package').exists()
                else:
                    raise AssertionError('missing query accepted')
        rejects(lambda: Budget(1000, deadline=0).check(), 'timeout')
        for total, buffer, error in ((1, 100000, 'budget exceeded'), (100000, 1, 'budget exceeded')):
            run = subprocess.run(cmd+['static_capture', str(root), str(total), str(buffer), '1'],
                                 capture_output=True, text=True, timeout=20)
            assert run.returncode != 0 and not run.stdout and error in run.stderr
    print('V04-2b PASS: four modes, independent packed outputs/Work, detail8, sampling, failure closure')


if __name__ == '__main__':
    main()
