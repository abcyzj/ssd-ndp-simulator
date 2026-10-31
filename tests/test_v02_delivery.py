"""Fail-closed delivery mapping and immutable artifact evidence checks."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from verify_v02 import acceptance_matrix, verify_hashes
from verify_m0 import sha


class DeliveryTests(unittest.TestCase):
    def baseline(self):
        return dict(scope='full M4.2', status='passed', checks={
            f'AC-{i:02}': [dict(status='passed')] for i in range(1,12)})

    def test_all_applicable_and_cancellation_is_not_pass(self):
        result = acceptance_matrix(self.baseline(), *[dict(passed=True)]*4, dict(results=True, statistics=True))
        self.assertEqual(len(result['checks']), 12)
        self.assertEqual(sum(r['status']=='passed' for r in result['checks'].values()), 11)
        self.assertEqual(result['checks']['AC-02-10']['status'], 'N/A')

    def test_quick_missing_failed_and_incomplete_are_rejected(self):
        for change in ('quick','missing','failed','empty','status'):
            data = copy.deepcopy(self.baseline())
            if change == 'quick': data['scope']='quick subset'
            if change == 'missing': del data['checks']['AC-08']
            if change == 'failed': data['checks']['AC-08'][0]['status']='failed'
            if change == 'empty': data['checks']['AC-08']=[]
            if change == 'status': data['status']='failed'
            with self.subTest(change=change), self.assertRaises(ValueError):
                acceptance_matrix(data, *[dict(passed=True)]*4, dict(results=True))
        for evidence in ({}, dict(passed=False)):
            with self.assertRaises(ValueError):
                acceptance_matrix(self.baseline(), evidence, *[dict(passed=True)]*3, dict(results=True))
        for reproduction in ({}, dict(results=True,statistics=False)):
            with self.assertRaises(ValueError):
                acceptance_matrix(self.baseline(), *[dict(passed=True)]*4, reproduction)

    def test_missing_empty_and_changed_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'result'; path.write_text('original')
            hashes={'result':sha(path)}
            verify_hashes(root,hashes)
            path.write_text('tampered')
            with self.assertRaises(ValueError): verify_hashes(root,hashes)
            path.unlink()
            with self.assertRaises(ValueError): verify_hashes(root,hashes)
            with self.assertRaises(ValueError): verify_hashes(root,{})


if __name__ == '__main__': unittest.main()
