"""Fail-closed V0.3 delivery gates; no simulated evidence is fabricated here."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_v03 import acceptance_matrix, require_suite, validate_matrix
from ann_data import write_json
from verify_m0 import sha


class DeliveryTests(unittest.TestCase):
    def baseline(self):
        v01=dict(scope='full M4.2',status='passed',checks={f'AC-{i:02}':[dict(status='passed')] for i in range(1,12)})
        inherited={s:dict(passed=True) for s in ('t3','t4','t5')}
        matrix=dict(schema='ndpsim-ir-matrix-v036-v1',passed=True,completed_matrix_points=52,expected_matrix_points=52)
        compatibility={f'{m}-{k}':True for m in ('host','controller') for k in ('default_ir','direct','equivalence','frozen','fresh_full')}
        checks={k:True for k in ('matrix_identity','matrix_gates','ir_asan','dependencies','source_unchanged','dependencies_unchanged','disk')}
        return [v01,inherited,matrix,compatibility,checks]

    def test_complete_ac_and_inherited_na(self):
        m=acceptance_matrix(*self.baseline())
        self.assertEqual(set(m['checks']),{f'AC-03-{i:02}' for i in range(1,13)})
        self.assertEqual(m['applicable_passed'],12)
        self.assertIn('AC-02-10',m['inherited_v02_not_applicable'])

    def test_every_missing_and_false_gate_is_rejected(self):
        for index in (1,3,4):
            for key in self.baseline()[index]:
                for mode in ('missing','false'):
                    args=self.baseline()
                    if mode=='missing':del args[index][key]
                    else:args[index][key]=dict(passed=False) if index==1 else False
                    with self.subTest(index=index,key=key,mode=mode),self.assertRaises(ValueError):acceptance_matrix(*args)

    def test_quick_incomplete_and_failed_ac(self):
        for mode in ('quick','missing','failed','empty','status'):
            args=self.baseline();v=args[0]
            if mode=='quick':v['scope']='quick subset'
            if mode=='missing':del v['checks']['AC-08']
            if mode=='failed':v['checks']['AC-08'][0]['status']='failed'
            if mode=='empty':v['checks']['AC-08']=[]
            if mode=='status':v['status']='failed'
            with self.subTest(mode=mode),self.assertRaises(ValueError):acceptance_matrix(*args)
        for field,value in [('passed',False),('completed_matrix_points',51),('expected_matrix_points',26),('schema','quick subset')]:
            args=self.baseline();args[2][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):acceptance_matrix(*args)

    def test_artifact_missing_changed_failed_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'evidence').write_text('original')
            manifest=dict(passed=True,artifacts_sha256={'evidence':sha(root/'evidence')})
            write_json(root/'manifest.json',manifest);require_suite(root)
            (root/'evidence').write_text('changed')
            with self.assertRaises(ValueError):require_suite(root)
            (root/'evidence').unlink()
            with self.assertRaises(ValueError):require_suite(root)
            for m in (dict(passed=False,artifacts_sha256={}),dict(passed=True,artifacts_sha256={})):
                write_json(root/'manifest.json',m)
                with self.assertRaises(ValueError):require_suite(root)

    def test_historical_matrix_identity_rejected(self):
        # Existing V03-6 evidence predates the default switch and these validators.
        # It must fail source identity even though all original outputs are intact.
        from verify_m0 import ROOT
        root=ROOT/'results/v0.3/matrix-v036/final5'
        if not root.exists():self.skipTest('local immutable historical evidence unavailable')
        with self.assertRaisesRegex(ValueError,'source identity mismatch'):
            validate_matrix(root,ROOT/'build/delivery',ROOT/'results/v0.2-t6/preparation/prepared')


if __name__=='__main__':unittest.main()
