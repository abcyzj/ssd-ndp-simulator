"""Exercise the reference comparator's independent counts and mismatch gates."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from compare_reference_paths import check_scores, check_sim, chunks, oracle
from test_search_core import oracle as fixture_oracle


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.vectors = [[10., 0.], [-1., 2.], [1., 2.], [0., 1.], [2., 0.], [0., 0.]]
        self.graph = [[0, 1, 1, 2], [3, 2], [4, 0], [], [1], []]
        self.query = [0., 0.]
        self.config = dict(entry=0, k=4, width=4, batch=5, budget=3, chunk=1)

    def test_independent_counts(self):
        for budget in (1, 3, 8):
            config = dict(self.config, budget=budget)
            decisions, batches, work, top = oracle(self.vectors, self.graph, self.query, config)
            stages = fixture_oracle(self.vectors, self.graph, self.query,
                                    **{k: v for k, v in config.items() if k != 'entry'})
            self.assertEqual(work, [list(row[2:]) for row in stages
                                   if row[0] in ('merge', 'select', 'dedup')])
            self.assertEqual(decisions[-1][1], [])
            self.assertEqual(len(top), min(config['k'], sum(map(len, batches))))

    def test_missing_altered_and_out_of_order_observations(self):
        expected = oracle(self.vectors, self.graph, self.query, self.config)
        decisions, batches, work, top = expected
        pack = lambda scores: [dict(id=i, distance=d) for i, d in scores]
        records = [dict(type='decision', candidates=pack(c), expand=e) for c, e in decisions]
        adjacency = [i for _, e in decisions for i in e]
        records.extend(dict(type='read', kind='adjacency', id=i) for i in adjacency)
        scored = chunks(batches, 1)
        records.extend(dict(type='read', kind='vector', id=b[0][0]) for b in scored)
        records.extend(dict(type='score', scores=pack(b), work=[2, 6, 16, 12, 12, 0, 0]) for b in scored)
        records.extend(dict(type='control', work=w) for w in work)
        result = dict(expanded=len(adjacency), top_k=pack(top))
        check = lambda rows: check_sim(rows, result, expected, scored, 2, 'host', 'fixture', False)
        check(records)
        for key, change in (('decision', lambda r: r['expand'].append(99)),
                            ('control', lambda r: r['work'].__setitem__(4, 999)),
                            ('read', lambda r: r.update(id=99)),
                            ('score', lambda r: r['scores'][0].update(distance=999))):
            altered = copy.deepcopy(records)
            change(next(row for row in altered if row['type'] == key))
            with self.assertRaises(ValueError):
                check(altered)
        with self.assertRaises(ValueError):
            check(records[:-1])
        altered = copy.deepcopy(records)
        vector_reads = [r for r in altered if r['type'] == 'read' and r['kind'] == 'vector']
        vector_reads[0]['id'], vector_reads[1]['id'] = vector_reads[1]['id'], vector_reads[0]['id']
        with self.assertRaises(ValueError):
            check(altered)

    def test_tolerance_is_opt_in_and_limited_to_distance(self):
        check_scores([[1, 1.00000001]], [[1, 1.]], 'fixture', True)
        for rows, tolerant in (([[1, 1.00000001]], False), ([[2, 1.]], True),
                               ([[1, float('nan')]], True)):
            with self.assertRaises(ValueError):
                check_scores(rows, [[1, 1.]], 'fixture', tolerant)


if __name__ == '__main__':
    unittest.main()
