import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from fit_v04_cpu_profile import estimate, fit, fit_stage, read, split_ids


def sample(query, value, ns, usable=True):
    return dict(query=query, values=(value,), ns=ns, usable=usable,
                point='fixture', short=ns < 100, operator='INIT')


class CpuFitTest(unittest.TestCase):
    def test_three_candidates(self):
        rows = [sample(i, i, 2 + 3*i) for i in range(1, 9)]
        self.assertEqual(estimate(fit('constant', rows), (4,)), 16)
        model = fit('linear', rows)
        self.assertAlmostEqual(model['intercept_ns'], 2)
        self.assertAlmostEqual(model['coefficients'][0], 3)
        table = fit('table', rows)
        self.assertEqual([b['upper'] for b in table['bins']], [2, 4, 6, 8])
        self.assertEqual([estimate(table, (v,)) for v in (1, 2, 3, 8)], [7, 7, 13, 25])

    def test_grouped_selection_quality_and_range(self):
        ids = list(range(32))
        rows = [sample(q, value, 10 if value <= 2 else 80)
                for q in ids for value in range(1, 5)]
        rows.extend((sample(0, 100, 80), sample(0, 999, -1, False)))
        entry, report = fit_stage('init', rows, ids, dict(operator='INIT'))
        self.assertEqual(entry['model']['kind'], 'table')
        self.assertEqual(entry['features'][0]['maximum'], 100)
        self.assertEqual(entry['samples'], 129)
        self.assertEqual(report['quality']['low_signal'], 1)
        self.assertEqual(report['folds'][0]['out_of_range'], 1)
        self.assertEqual(report['folds'][0]['held_out_query_ids'], ids[::4])
        self.assertEqual(report['holdout']['table']['samples'], 128)
        self.assertEqual(report['short_training']['samples'], 129)
        self.assertNotIn('relative_p50', report['short_training'])

    def test_split_overlap_fails(self):
        tuning, evaluation = list(range(32)), list(range(32, 160))
        selection = dict(tuning_ids=tuning, evaluation_ids=evaluation)
        groups = dict(tuning=tuning, evaluation=evaluation)
        self.assertEqual(split_ids(selection, groups, tuning), (tuning, evaluation))
        evaluation[0] = tuning[0]
        with self.assertRaisesRegex(ValueError, 'original query ID split'):
            split_ids(selection, groups, tuning)

    def test_wrong_input_hash_fails(self):
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            read(Path(__file__), '0'*64)


if __name__ == '__main__':
    unittest.main()
