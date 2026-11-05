"""V0.4 release coverage and failure gates, using structural evidence only."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_v04 import acceptance, core_plan, summarize_level_a


class DeliveryTests(unittest.TestCase):
    def evidence(self):
        plan = core_plan()
        points = []
        for expected in plan['configurations']:
            row = dict(id=expected['id'],parameters=expected,status='passed',
                       runs=[dict(directory=expected['id']+str(i),queries=128,
                                  actual={k:v for k,v in expected.items() if k not in ('id','repeats')})
                             for i in range(expected['repeats'])])
            if expected['id'][0] in 'AD':
                row['native'] = [dict(directory=expected['id']+'native'+str(i)) for i in range(3)]
            if expected['id'][0] in 'ADO':
                row.update(package_verified=True,complete_queries=128)
            points.append(row)
        ac = {f'AC-04-{i:02}':dict(status='passed',evidence=['structural gate fixture']) for i in range(1,13)}
        impact = dict(deep_regression_required=False,deep_regression_executed=False,
                      triggers=[dict(triggered=False) for _ in range(3)])
        return [plan,points,ac,impact]

    def test_complete_core12_scope(self):
        result = acceptance(*self.evidence())
        self.assertEqual((result['configurations'],result['core_simulations'],result['ac_passed']),(12,16,12))

    def test_missing_duplicate_extra_or_failed_configuration(self):
        for mutate in (lambda rows: rows.pop(),lambda rows: rows.append(copy.deepcopy(rows[0])),
                       lambda rows: rows[1].update(id='A01'),lambda rows: rows[0].update(status='failed')):
            args = self.evidence()
            mutate(args[1])
            with self.assertRaises(ValueError):
                acceptance(*args)

    def test_repetition_full_package_and_effective_parameters(self):
        for mutate in (lambda r: r['runs'].pop(),lambda r: r['runs'][1].update(directory=r['runs'][0]['directory']),
                       lambda r: r['native'].pop(),lambda r: r.update(complete_queries=8),
                       lambda r: r.update(package_verified=False),lambda r: r['runs'][0].update(queries=8),
                       lambda r: r['runs'][0]['actual'].update(provider='ssd'),
                       lambda r: r['parameters'].update(L=128)):
            args = self.evidence()
            mutate(args[1][0])
            with self.assertRaises(ValueError):
                acceptance(*args)

    def test_ac_and_deep_gates(self):
        for mutate in (lambda a: a.pop('AC-04-03'),lambda a: a['AC-04-03'].update(status='diagnostic'),
                       lambda a: a['AC-04-12'].update(evidence=[])):
            args = self.evidence()
            mutate(args[2])
            with self.assertRaises(ValueError):
                acceptance(*args)
        for key in ('deep_regression_required','deep_regression_executed'):
            args = self.evidence()
            args[3][key] = True
            with self.assertRaises(ValueError):
                acceptance(*args)

    def test_level_a_disclosure_is_required_and_numeric_failure_still_blocks(self):
        report = dict(scope_points=['l32-e64','l64-e128'],numeric_gate='passed',table_gate='passed',
                      stage_sources=dict(merge_append='measured_table'),evaluation_disclosure='prior exposure recorded',
                      measured_merge_append={str(i):dict(status='table_verified',replay_billing='passed') for i in range(4)})
        self.assertEqual(summarize_level_a(copy.deepcopy(report))['ac_04_04'],'passed')
        for key,value in [('numeric_gate','failed'),('table_gate','failed'),('evaluation_disclosure','')]:
            bad = copy.deepcopy(report)
            bad[key] = value
            with self.assertRaises(ValueError):
                summarize_level_a(bad)


if __name__ == '__main__':
    unittest.main()
