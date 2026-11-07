"""Deletion and matrix review must fail closed on coverage loss."""
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from audit_v045_baseline import DIRECTORY, validate

class BaselineGates(unittest.TestCase):
    def setUp(self):
        self.files, self.tests, self.matrix = [json.loads((DIRECTORY/n).read_text())
            for n in ('files.json','tests.json','matrix.json')]
        self.names = [r['path'] for r in self.files['files']]
        self.registered = [{'name':r['name']} for r in self.tests['tests']]

    def check_plan(self):
        return validate(self.files,self.tests,self.names,self.registered,self.matrix)

    def test_baseline(self): self.assertTrue(self.check_plan())

    def test_lost_file_test_or_deletion_gate(self):
        for key in ('file','test','gate','duplicate'):
            with self.subTest(key=key):
                saved = copy.deepcopy((self.files,self.tests))
                if key=='file': self.files['files'].pop()
                if key=='test': self.tests['tests'].pop()
                if key=='gate': self.files['files'][0]['deletion_gate']=''
                if key=='duplicate': self.files['files'].append(self.files['files'][0])
                with self.assertRaises(ValueError): self.check_plan()
                self.files,self.tests=saved

    def test_retired_paths_and_replacement_tests(self):
        if not self.tests.get('removed'):
            self.skipTest('pre-cleanup inventory')
        row = self.tests['removed'][0]
        row['current_assertions'] = ['unregistered-replacement']
        with self.assertRaisesRegex(ValueError, 'replacement assertions'):
            self.check_plan()
        row['current_assertions'] = [self.registered[0]['name']]
        self.files['removed'][0]['path'] = self.names[0]
        with self.assertRaisesRegex(ValueError, 'retired file'):
            self.check_plan()

    def test_reduced_matrix_repeats_or_changed_queries(self):
        for key in ('point','repeat','query','strict','ssd-admission','ssd-offered'):
            with self.subTest(key=key):
                saved=copy.deepcopy(self.matrix)
                if key=='point': self.matrix['configurations'].pop()
                if key=='repeat': self.matrix['configurations'][0]['simulated_repeats_min']=1
                if key=='query': self.matrix['evaluation_ids'].reverse()
                if key=='strict': self.matrix['configurations'][0]['workers']=2
                if key=='ssd-admission': next(r for r in self.matrix['configurations'] if r['id']=='C01')['admission']=2
                if key=='ssd-offered': next(r for r in self.matrix['configurations'] if r['id']=='B01')['offered_ns']=1
                with self.assertRaises(ValueError): self.check_plan()
                self.matrix=saved

if __name__=='__main__': unittest.main()
