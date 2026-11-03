"""V04-0 evidence must reject incomplete receipts, tampering and plan drift."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ann_data import write_json
from verify_m0 import sha
from freeze_v04_start import SCHEMA, REQUIRED, matrix_plan, validate_matrix, verify_evidence


class StartFreezeTest(unittest.TestCase):
    def fixture(self, root):
        for f in ('source-start.json','source-end.json','dependencies.json','inputs.json',
                  'environment.json','budget-frozen.json','build-identity.json'):
            write_json(root/f, {'fixture':True})
        (root/'workspace.patch').write_text('')
        write_json(root/'matrix-plan.json',matrix_plan())
        write_json(root/'assertions.json',{k:True for k in REQUIRED})
        commands=['configure','build','ctest','evidence-tests','diff-check',
                  'direct-host','direct-controller','default-host','default-controller',
                  'equivalence-host','equivalence-controller','independent-direct','independent-default']
        m=dict(schema=SCHEMA,task='V04-0',passed=True,
               commands=[dict(name=n,exit_code=0,passed=True) for n in commands])
        self.refresh(root,m)
        return m

    def refresh(self,root,m):
        m['artifacts_sha256']={f.name:sha(f) for f in root.iterdir() if f.name!='manifest.json'}
        write_json(root/'manifest.json',m)

    def test_matrix_and_repetition_contract(self):
        plan=matrix_plan();self.assertTrue(validate_matrix(plan))
        rows=plan['configurations']
        self.assertEqual(len({r['id'] for r in rows}),14)
        self.assertEqual(sum(r['simulated_repeats_min'] for r in rows),20)
        self.assertEqual(sum(r['native_repeats_min'] for r in rows),18)
        self.assertEqual([r['controller_flops_per_ns'] for r in rows[-3:]],[8,16,32])
        for mutation in ('missing','duplicate','repeat','schedule','worker'):
            bad=copy.deepcopy(plan)
            if mutation=='missing':bad['configurations'].pop()
            if mutation=='duplicate':bad['configurations'][1]=bad['configurations'][0]
            if mutation=='repeat':bad['configurations'][0]['simulated_repeats_min']=1
            if mutation=='schedule':bad['companion_schedule'].pop()
            if mutation=='worker':bad['configurations'][4]['concurrency']=2
            with self.assertRaises(ValueError):validate_matrix(bad)

    def test_tamper_missing_artifact_and_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=self.fixture(root);self.assertTrue(verify_evidence(root)['passed'])
            (root/'inputs.json').write_text('tampered')
            with self.assertRaises(ValueError):verify_evidence(root)
            self.refresh(root,m);(root/'inputs.json').unlink()
            with self.assertRaises(ValueError):verify_evidence(root)
            m['artifacts_sha256']['../outside']='bad';write_json(root/'manifest.json',m)
            with self.assertRaises(ValueError):verify_evidence(root)

    def test_missing_failed_and_duplicate_commands(self):
        for mode in ('missing','failed','duplicate'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);m=self.fixture(root)
                if mode=='missing':m['commands'].pop()
                if mode=='failed':m['commands'][0]['exit_code']=1
                if mode=='duplicate':m['commands'].append(m['commands'][0])
                write_json(root/'manifest.json',m)
                with self.assertRaises(ValueError):verify_evidence(root)

    def test_assertion_failure_or_omission_cannot_pass(self):
        for mode in ('false','missing'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);m=self.fixture(root);checks={k:True for k in REQUIRED}
                if mode=='false':checks['inputs']=False
                else:checks.pop('inputs')
                write_json(root/'assertions.json',checks);self.refresh(root,m)
                with self.assertRaises(ValueError):verify_evidence(root)

    def test_failed_or_wrong_scope_and_empty_inventory(self):
        for change in ({'passed':False},{'task':'V04-5c'},{'schema':'quick'},{'artifacts_sha256':{}}):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);m=self.fixture(root);m.update(change);write_json(root/'manifest.json',m)
                with self.assertRaises(ValueError):verify_evidence(root)


if __name__=='__main__':unittest.main()
