"""V03-1 structural contract, semantic rejection and compiled catalog parity."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
VALIDATOR=Path(sys.argv[sys.argv.index('--validator')+1]) if '--validator' in sys.argv else ROOT/'build/delivery/ir_validate'
if '--validator' in sys.argv:
    i=sys.argv.index('--validator'); del sys.argv[i:i+2]

class Contract(unittest.TestCase):
    def setUp(self):
        self.doc=json.loads((ROOT/'examples/ir/graph-contract.json').read_text())
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def run_doc(self,doc):
        p=Path(self.temp.name)/'input.json'
        p.write_text(doc if isinstance(doc,str) else json.dumps(doc))
        return subprocess.run([str(VALIDATOR),str(p)],capture_output=True,text=True)

    def reject(self,doc):
        result=self.run_doc(doc)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        diagnostic=json.loads(result.stderr)
        self.assertIn(diagnostic['code'],['InvalidArgument','Unsupported','VersionMismatch','OutOfRange','Overflow'])
        self.assertTrue(diagnostic['detail'])

    def test_legal_and_export(self):
        p=self.run_doc(self.doc)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)['operations'],7)
        self.assertFalse(json.loads(p.stdout)['execution_supported'])
        for flag,path in [('--schema','ndpsim-ir-v1.schema.json'),('--capabilities','ndpsim-ir-v1.capabilities.json')]:
            actual=json.loads(subprocess.check_output([str(VALIDATOR),flag]))
            self.assertEqual(actual,json.loads((ROOT/'schemas'/path).read_text()))
        cat=json.loads((ROOT/'schemas/ndpsim-ir-v1.capabilities.json').read_text())
        h=hashlib.sha256(json.dumps(cat,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(subprocess.check_output([str(VALIDATOR),'--catalog-hash'],text=True).strip(),h)
        self.assertEqual(self.doc['manifest']['operator_set']['catalog_sha256'],h)
        self.assertEqual(len([op for op in cat['operators'] if op['execution_supported']]), 7)
        self.assertEqual(next(op for op in cat['operators'] if op['operator']=='JOIN')['mapping_status'], 'runtime_async')

    def test_empty_initial_merge(self):
        d=copy.deepcopy(self.doc)
        d['operations'][2]['inputs'][0]['value']={
            'ref':'literal','type':{'name':'scores_id_f64','shape':[{'extent':0,'dynamic':False}]},'value':[]}
        d['operations']=d['operations'][:3]
        d['operations'][2]['outputs'][0]['type']['shape'][0]['extent']=1
        self.assertEqual(self.run_doc(d).returncode,0)
        bad=copy.deepcopy(d);bad['operations'][2]['inputs'][0]['value']['value']=[0]
        self.reject(bad)
        bad=copy.deepcopy(d);bad['operations'][2]['inputs'][1]['value']=bad['operations'][2]['inputs'][0]['value']
        self.reject(bad)

    def test_controller_and_optional_metadata(self):
        d=copy.deepcopy(self.doc)
        d['manifest']['mapping']['placement']='controller'
        d['operations'][1]['placement']='controller'
        d['operations'][1]['access'][0]['slice']['range']={'offset':0,'length':4}
        # JOIN port names carry no implicit ADDRESS semantics.
        d['operations'][6]['inputs'][1]['port']='ids'
        d['operations'][1]['scope'].update(chunk=0,stage='score')
        d['operations'][1]['schedule']={'parallel_group':'diagnostic-only'}
        d['operations'][1]['provenance']={'source':'schema-test','record_offset':0}
        d['operations'][1]['extensions']={'non_semantic':True,'metadata':{'note':1}}
        d['objects'][0]['extensions']={'non_semantic':True,'metadata':{}}
        result=self.run_doc(d)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_schema_regeneration(self):
        files=[ROOT/'schemas'/f'ndpsim-ir-v1.{suffix}.json' for suffix in ['schema','capabilities']]
        before=[p.read_bytes() for p in files]
        subprocess.run([sys.executable,str(ROOT/'tools/generate_ir_schema.py'),'--check'],check=True)
        self.assertEqual(before,[p.read_bytes() for p in files])

    def test_unknown_required_semantics(self):
        mutations=[(['manifest','ir_version'],'ndpsim-ir/2'),(['manifest','execution_engine'],'direct'),(['operations',1,'operator'],'PQ'),(['operations',1,'operator_version'],2),(['operations',1,'placement'],'near-memory'),(['operations',1,'outputs',0,'location'],'Unknown'),(['operations',1,'outputs',0,'location'],'DeviceBuffer'),(['operations',1,'deps',0,'kind'],'MAYBE'),(['objects',0,'mutability'],'write'),(['manifest','extensions','non_semantic'],False)]
        for keys,value in mutations:
            with self.subTest(keys=keys,value=value):
                d=copy.deepcopy(self.doc); node=d
                for k in keys[:-1]:node=node[k]
                node[keys[-1]]=value; self.reject(d)
        for keys in [[],['manifest'],['objects',0],['operations',0],['operations',1,'params']]:
            d=copy.deepcopy(self.doc);node=d
            for k in keys:node=node[k]
            node['typo_required']=1;self.reject(d)
        d=copy.deepcopy(self.doc);del d['operations'][1]['deps'];self.reject(d)

    def test_identity_duplicates_and_numbers(self):
        mutations=[(['manifest','operator_set','catalog_sha256'],'a'*64),(['objects',0,'content_identity','sha256'],'a'*64),(['objects',0,'content_identity','sha256'],'bad'),(['objects',0,'element_count'],2**32+1),(['objects',0,'shape'],[2**64-1]),(['operations',1,'op_id'],1),(['operations',1,'op_id'],2**64),(['operations',1,'op_id'],2.0),(['operations',1,'op_id'],-1),(['operations',1,'op_id'],True),(['objects',0,'shape'],[1,2]),(['operations',1,'scope','run'],'another')]
        for keys,value in mutations:
            with self.subTest(keys=keys,value=value):
                d=copy.deepcopy(self.doc);node=d
                for k in keys[:-1]:node=node[k]
                node[keys[-1]]=value;self.reject(d)
        d=copy.deepcopy(self.doc);d['objects'].append(d['objects'][0]);self.reject(d)
        d=copy.deepcopy(self.doc);d['operations'][1]['outputs']*=2;self.reject(d)
        self.reject(json.dumps(self.doc).replace('"ir_version": "ndpsim-ir/1"','"ir_version":"ndpsim-ir/1","ir_version":"ndpsim-ir/1"'))

    def test_types_shapes_and_slices(self):
        mutations=[(['operations',1,'outputs',0,'type','name'],'fp32'),(['operations',1,'outputs',0,'type','shape'],[]),(['operations',1,'outputs',0,'type','shape'],[dict(extent=2**64-1,dynamic=True)]),(['operations',1,'access',0,'slice','content_version'],2),(['operations',1,'access',0,'slice','indices'],[4]),(['operations',1,'access',0,'slice','object_id'],'missing'),(['operations',1,'access',0,'slice','field'],'neighbors'),(['operations',1,'access',0,'slice','range'],dict(offset=2**64-1,length=2)),(['operations',1,'access',0,'slice','range'],dict(offset=3,length=2)),(['operations',1,'access',0,'slice','range'],dict(offset=0,length=3)),(['operations',1,'params','dimensions'],3),(['operations',1,'inputs',0,'value','type','shape'],[dict(extent=3,dynamic=False)]),(['operations',1,'inputs',1,'value','value'],[1]),(['operations',1,'inputs',1,'value','value'],[0,1]),(['operations',1,'workload','rule'],'none'),(['operations',1,'effects'],['pure_value']),(['operations',1,'workload','counts'],{k:0 for k in ['elements','flops','read_bytes','write_bytes','output_bytes','candidate_checks','comparisons']})]
        for keys,value in mutations:
            with self.subTest(keys=keys,value=value):
                d=copy.deepcopy(self.doc);node=d
                for k in keys[:-1]:node=node[k]
                node[keys[-1]]=value;self.reject(d)

    def test_document_reference_checks(self):
        mutations=[(['operations',1,'inputs',0,'value','output','producer_op'],2),(['operations',1,'inputs',0,'value','output','producer_op'],99),(['operations',1,'inputs',0,'value','output','port'],'missing'),(['operations',1,'deps'],[]),(['operations',4,'deps',0,'kind'],'ORDER'),(['operations',4,'access',0,'slice','indices_ref','producer_op'],3),(['operations',3,'inputs',1,'value','query'],8),(['operations',0,'scope','query'],9),(['operations',3,'inputs',1,'value','version'],0)]
        for keys,value in mutations:
            with self.subTest(keys=keys,value=value):
                d=copy.deepcopy(self.doc);node=d
                for k in keys[:-1]:node=node[k]
                node[keys[-1]]=value;self.reject(d)

    def test_diagnostic_context(self):
        self.doc['operations'][1]['inputs'][0]['value']['output']['producer_op']=99
        r=self.run_doc(self.doc)
        self.assertEqual(r.returncode,1)
        error=json.loads(r.stderr)
        self.assertEqual((error['query'],error['ir_op'],error['producer']),(7,2,99))
        self.assertIsNone(error['time_ns'])

    def test_nonsemantic_extensions_and_empty_stream(self):
        self.doc['manifest']['extensions']['metadata']['unknown-note']={'anything':[1,2,3]}
        self.doc['operations']=[]
        self.assertEqual(self.run_doc(self.doc).returncode,0)

if __name__=='__main__':unittest.main()
