"""Fail-closed V04-5b gates. These tests do not fabricate simulation results."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from verify_v04_regression import REQUIRED, acceptance, assess_impact, validate_inherited


class RegressionTests(unittest.TestCase):
    def evidence(self):
        checks = dict.fromkeys(REQUIRED, True)
        covered = ['R01', 'R02', 'R03', 'R04']
        impact = assess_impact(['src/model/host_cpu.cc', 'src/search/replay.cc'], True, True)
        v01 = dict(scope='full M4.2', status='passed',
                   checks={f'AC-{i:02}': [dict(status='passed')] for i in range(1, 12)})
        inherited = {s: dict(passed=True) for s in ('t3', 't4', 't5')}
        return [checks, covered, impact, v01, inherited]

    def test_local_complete_scope(self):
        report = acceptance(*self.evidence())
        self.assertEqual(report['status'], 'passed')
        self.assertIn('V04-5b only', report['scope'])
        self.assertFalse(self.evidence()[2]['deep_regression_executed'])

    def test_missing_or_failed_gate(self):
        for key in REQUIRED:
            for value in (None, False):
                args = self.evidence()
                if value is None:
                    del args[0][key]
                else:
                    args[0][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    acceptance(*args)

    def test_missing_duplicate_or_extra_matrix_point(self):
        for covered in (['R01', 'R02', 'R03'], ['R01', 'R02', 'R03', 'R03'],
                        ['R01', 'R02', 'R03', 'R04', 'O01']):
            args = self.evidence()
            args[1] = covered
            with self.assertRaises(ValueError):
                acceptance(*args)

    def test_quick_or_incomplete_inherited(self):
        for mutate in (lambda v: v.update(scope='quick subset'),
                       lambda v: v['checks'].pop('AC-08'),
                       lambda v: v['checks'].update({'AC-10': []}),
                       lambda v: v['checks']['AC-01'][0].update(status='failed')):
            args = self.evidence()
            mutate(args[3])
            with self.assertRaises(ValueError):
                acceptance(*args)
        for stage in ('t3', 't4', 't5'):
            args = self.evidence()
            args[4].pop(stage)
            with self.assertRaises(ValueError):
                validate_inherited(args[3], args[4])

    def test_trigger_blocks_without_running_deep_suite(self):
        for changed, same, compatible in [
            (['src/content/layout.cc'], True, True),
            (['src/model/controller.cc'], False, True),
            ([], True, False),
        ]:
            impact = assess_impact(changed, same, compatible)
            self.assertTrue(impact['deep_regression_required'])
            self.assertFalse(impact['deep_regression_executed'])
            args = self.evidence()
            args[2] = impact
            with self.assertRaisesRegex(ValueError, 'deep regression'):
                acceptance(*args)
        args = self.evidence()
        args[2]['triggers'].pop()
        with self.assertRaisesRegex(ValueError, 'impact decision'):
            acceptance(*args)


if __name__ == '__main__':
    unittest.main()
