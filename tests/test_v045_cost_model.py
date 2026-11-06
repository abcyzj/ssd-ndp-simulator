"""Independent service arithmetic and fail-closed checks; no graph algorithms."""
import copy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

PROBE=sys.argv.pop(1)
U64=2**64-1
TARGET=dict(id='target-cpu',hardware='hypothetical-cpu',os='model-os',memory_bytes=8192,frequency='fixed',affinity='one')
ROOT=Path(__file__).resolve().parents[1]

def q(n,u): return dict(value=n,unit=u)

class Models(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proc=subprocess.Popen([PROBE],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        cls.hashes=json.loads(cls.proc.stdout.readline())
        cls.cases=0
    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close(); cls.proc.wait(timeout=10)
        cls.proc.stdout.close()
        assert cls.proc.returncode==0
        print(f"Checked {cls.cases} model requests against the C++ library.")
    def request(self,formula='constant/1',parameters=None):
        rule=dict(operator='example.kernel',operator_version='1',stage_kind='compute',context='resident/1',location='host',target_id=TARGET['id'],feature_ranges=[dict(name='items',unit='count',minimum=0,maximum=100)],work_units=dict(flops='flop',bytes='B'),source='prediction',formula=formula,parameter_schema_sha256=self.hashes.get(formula,'f'*64),parameters=parameters if parameters is not None else dict(ns=3),measurement_sha256=['1'*64],training_query_ids=[1,2],fit_report_sha256='2'*64)
        profile=dict(format='ndpsim-cost-profile/1',fixture=True,target=copy.deepcopy(TARGET),rules=[rule])
        inp=dict(key=dict(query=7,op='op1',stage='s1'),operator='example.kernel',operator_version='1',stage_kind='compute',context='resident/1',location='host',work=dict(flops=q(13,'flop'),bytes=q(5,'B')),features=dict(items=q(7,'count')),target=copy.deepcopy(TARGET))
        return dict(profile=profile,target=copy.deepcopy(TARGET),input=inp,allow_fixture=True)
    def run_case(self,req):
        type(self).cases+=1
        self.proc.stdin.write(json.dumps(req,separators=(',',':'))+'\n'); self.proc.stdin.flush()
        line=self.proc.stdout.readline(); self.assertTrue(line,'probe terminated')
        return json.loads(line)
    def ns(self,req,n): self.assertEqual(self.run_case(req),dict(ns=n))
    def bad(self,req,match=None):
        result=self.run_case(req); self.assertIn('error',result)
        if match: self.assertIn(match,result['error'])
    def rule(self,r): return r['profile']['rules'][0]
    def test_constant_exact_and_zero(self):
        for n in (0,1,2**53+1,U64): self.ns(self.request(parameters=dict(ns=n)),n)
    def test_binary64_linear_order_and_rounding(self):
        r=self.request('nonnegative-linear/1',dict(intercept_ns=.125,coefficients=[.25,.5]))
        self.rule(r)['feature_ranges'].append(dict(name='flops',unit='flop',minimum=0,maximum=1000))
        for x in range(30):
            r['input']['features']['items']['value']=x
            expected=int((Decimal('0.125')+Decimal('.25')*x+Decimal('.5')*13).to_integral_value(rounding=ROUND_CEILING))
            self.ns(r,expected)
        self.ns(self.request('nonnegative-linear/1',dict(intercept_ns=0,coefficients=[0])),0)
    def test_table_boundaries(self):
        r=self.request('upper-bound-table/1',dict(bins=[dict(upper=0,ns=0),dict(upper=7,ns=U64),dict(upper=100,ns=9)]))
        for value,expected in ((0,0),(1,U64),(7,U64),(8,9),(100,9)):
            r['input']['features']['items']['value']=value; self.ns(r,expected)
    def test_gbdt_independent_expected(self):
        trees=[[dict(feature=0,threshold=7,right=2),-2.0,3.0],[dict(feature=0,threshold=50,right=4),dict(feature=0,threshold=4,right=3),2.0,1.0,-1.0]]
        r=self.request('log-gbdt/1',dict(initial=1.5,rate=.25,trees=trees))
        for x in range(101):
            r['input']['features']['items']['value']=x
            a=-2 if x<=7 else 3
            b=(2 if x<=4 else 1) if x<=50 else -1
            self.ns(r,max(1,math.ceil(math.exp(1.5+.25*(a+b)))))
        self.ns(self.request('log-gbdt/1',dict(initial=-1000,rate=1,trees=[[0]])),1)
    def test_exact_integer_host_controller_buffer(self):
        r=self.request('integer-rate/1',dict(setup_ns=3,units_per_ns=7,weights=[2,5]))
        self.rule(r)['feature_ranges'].append(dict(name='flops',unit='flop',minimum=0,maximum=1000))
        self.ns(r,3+(2*7+5*13+6)//7)
        r=self.request('integer-rate/1',dict(setup_ns=0,units_per_ns=U64,weights=[1]))
        self.rule(r)['feature_ranges'][0]['maximum']=U64; r['input']['features']['items']['value']=U64
        self.ns(r,1)
    def test_registered_external_formula(self): self.ns(self.request('example-double/1',{}),14)
    def test_unknown_duplicate_registration(self):
        for flag in ('duplicate_operator','duplicate_formula','bad_schema'):
            r=self.request();r[flag]=True;self.bad(r)
        # The probe remains registered once; failed duplicate operations are atomic.
        self.bad(self.request('missing/1',{}),'unknown formula')
    def test_units_ranges_and_counter_sets(self):
        for mutation in (
            lambda r:r['input']['work']['flops'].update(unit='count'),
            lambda r:r['input']['features']['items'].update(unit='B'),
            lambda r:r['input']['features']['items'].update(value=101),
            lambda r:r['input']['work'].pop('bytes'),
            lambda r:r['input']['features'].update(extra=q(1,'count')),
            lambda r:self.rule(r)['feature_ranges'][0].update(minimum=101),
            lambda r:self.rule(r)['feature_ranges'].append(copy.deepcopy(self.rule(r)['feature_ranges'][0])),
            lambda r:self.rule(r)['feature_ranges'].clear(),
            lambda r:self.rule(r)['work_units'].update(flops='count'),
            lambda r:self.rule(r)['feature_ranges'][0].update(name='unregistered')):
            r=self.request();mutation(r);self.bad(r)
    def test_eager_domain_overflow_and_parameters(self):
        for formula,p in (
            ('nonnegative-linear/1',dict(intercept_ns=2**64,coefficients=[0])),
            ('nonnegative-linear/1',dict(intercept_ns=0,coefficients=[1e308])),
            ('nonnegative-linear/1',dict(intercept_ns=-1,coefficients=[1])),
            ('nonnegative-linear/1',dict(intercept_ns=0,coefficients=[])),
            ('nonnegative-linear/1',dict(intercept_ns=0,coefficients=[-.1])),
            ('integer-rate/1',dict(setup_ns=U64,units_per_ns=1,weights=[1])),
            ('integer-rate/1',dict(setup_ns=0,units_per_ns=1,weights=[U64])),
            ('integer-rate/1',dict(setup_ns=0,units_per_ns=0,weights=[0])),
            ('constant/1',dict(ns=1.0)),('constant/1',dict(ns=True)),('constant/1',dict(ns=-1)),
            ('constant/1',dict(ns=2**64)),('constant/1',dict(ns=0,wait_ns=5)),
            ('upper-bound-table/1',dict(bins=[])),
            ('upper-bound-table/1',dict(bins=[dict(upper=99,ns=1)])),
            ('upper-bound-table/1',dict(bins=[dict(upper=100,ns=1),dict(upper=100,ns=2)])),
            ('log-gbdt/1',dict(initial=1000,rate=1,trees=[[0]])),
            ('log-gbdt/1',dict(initial=0,rate=0,trees=[[0]]))):
            self.bad(self.request(formula,p))
    def test_bad_gbdt_structure(self):
        for tree in ([],[1,2],[dict(feature=0,threshold=7,right=1),0,0],[dict(feature=0,threshold=100,right=2),0,0],[dict(feature=1,threshold=7,right=2),0,0],[dict(feature=0,threshold=7,right=9),0,0]):
            self.bad(self.request('log-gbdt/1',dict(initial=0,rate=.1,trees=[tree])))
        self.bad(self.request('log-gbdt/1',dict(initial=0,rate=.1,trees=[[0]]*181)))
        # Complete depth-four tree is rejected even before semantic parsing.
        def tree(d):
            if not d:return [0]
            left=tree(d-1); right=tree(d-1)
            def shift(nodes,offset):return [dict(n,right=n['right']+offset) if isinstance(n,dict) else n for n in nodes]
            return [dict(feature=0,threshold=7,right=1+len(left))]+shift(left,1)+shift(right,1+len(left))
        self.bad(self.request('log-gbdt/1',dict(initial=0,rate=.1,trees=[tree(4)])))
    def test_target_and_rule_identity(self):
        for mutation in (
            lambda r:r['target'].update(hardware='other'),
            lambda r:r['input']['target'].update(memory_bytes=16384),
            lambda r:r['input'].update(operator_version='2'),
            lambda r:r['input'].update(context='ssd/1'),
            lambda r:r['input'].update(location='controller'),
            lambda r:self.rule(r).update(target_id='other'),
            lambda r:self.rule(r).update(operator='missing'),
            lambda r:self.rule(r).update(stage_kind='unknown'),
            lambda r:self.rule(r).update(location='gpu'),
            lambda r:self.rule(r).update(parameter_schema_sha256='a'*64),
            lambda r:r['profile']['rules'].append(copy.deepcopy(self.rule(r))),
            lambda r:self.rule(r).update(measurement_sha256=['1'*64]*2),
            lambda r:self.rule(r).update(training_query_ids=[1,1])):
            r=self.request();mutation(r);self.bad(r)
        # No current-host hardware is queried. A hypothetical target works here.
        self.ns(self.request(),3)
    def test_profile_integrity_json_and_fixture(self):
        r=self.request(); r['allow_fixture']=False; self.bad(r,'fixture')
        r=self.request(); r['expected_sha256']='0'*64; self.bad(r,'hash')
        for raw in ('{"format":1,"format":2}', '{"ns":NaN}', '{"ns":1e999}', '{'):
            r=self.request(); r['raw']=raw; self.bad(r)
        r=self.request();r['profile']['unexpected']=1;self.bad(r)
        r=self.request();self.rule(r)['feature_ranges'][0]['maximum']=100.0;self.bad(r)
    def measured(self):
        r=self.request(); rule=self.rule(r)
        for k in ('formula','parameters','parameter_schema_sha256','fit_report_sha256','training_query_ids'):rule.pop(k)
        binding=json.loads((ROOT/'examples/p01-contract/measured-profile.json').read_text())['rules'][0]['binding']
        binding.update(target=copy.deepcopy(TARGET),context='resident/1')
        rule.update(source='measured-table',binding=binding,measurement_sha256=['1'*64,'2'*64,'3'*64],quality_report_sha256='4'*64,rows=[dict(key=copy.deepcopy(r['input']['key']),work=copy.deepcopy(r['input']['work']),features=copy.deepcopy(r['input']['features']),duration_ns=8)])
        r['input']['binding']=copy.deepcopy(binding)
        return copy.deepcopy(r)
    def test_measured_full_key_work_and_binding(self):
        self.ns(self.measured(),8)
        r=self.measured();r['profile']['fixture']=False
        # Distinct, nonzero hashes cannot turn synthetic provenance into native.
        raw=json.dumps(r).replace('0'*64,'9'*64);r=json.loads(raw)
        self.bad(r,'synthetic binding')
        for mutation in (
            lambda r:r['input']['key'].update(stage='s2'),
            lambda r:r['input']['key'].update(op='op2'),
            lambda r:r['input']['key'].update(query=8),
            lambda r:r['input']['work']['flops'].update(value=14),
            lambda r:r['input']['features']['items'].update(value=8),
            lambda r:r['input']['binding'].update(slots=2),
            lambda r:r['input']['binding'].update(package_sha256='5'*64),
            lambda r:r['input']['binding']['provenance']['capture_build'].update(binary_sha256='5'*64),
            lambda r:r['input']['binding']['provenance'].update(input_content_sha256='5'*64),
            lambda r:self.rule(r)['rows'].append(copy.deepcopy(self.rule(r)['rows'][0])),
            lambda r:self.rule(r)['rows'][0]['features']['items'].update(value=101),
            lambda r:self.rule(r).update(measurement_sha256=['1'*64]*3),
            lambda r:self.rule(r).update(measurement_sha256=['1'*64,'2'*64]),
            lambda r:self.rule(r)['binding'].update(context='ssd/1'),
            lambda r:self.rule(r)['binding']['target'].update(hardware='other'),
            lambda r:self.rule(r)['binding']['provenance'].update(query_ids=[8])):
            r=self.measured();mutation(r);self.bad(r)
    def test_ordered_binary64_sum_and_depth_limit(self):
        r=self.request('nonnegative-linear/1',dict(intercept_ns=2**53,coefficients=[1,1]))
        self.rule(r)['feature_ranges'].append(dict(name='flops',unit='flop',minimum=0,maximum=1))
        r['input']['work']['flops']['value']=1
        r['input']['features']['items']['value']=1
        # Two ordered binary64 additions each round away the unit increment.
        self.ns(r,2**53)
        r=self.request();r['raw']='['*65+'0'+']'*65;self.bad(r,'nesting')
        r=self.request();r['profile']['fixture']=False
        self.rule(r)['measurement_sha256']=['0'*64];self.bad(r,'placeholder')
    def test_schema_fingerprint_is_independent_sha256(self):
        s=dict(type='object',properties=dict(ns=dict(type='integer',minimum=0,maximum=U64)),required=['ns'],additionalProperties=False)
        raw=(json.dumps(s,sort_keys=True,separators=(',',':'))+'\n').encode()
        self.assertEqual(self.hashes['constant/1'],hashlib.sha256(raw).hexdigest())
        s=dict(type='object',properties={},additionalProperties=False,description='扩展')
        raw=(json.dumps(s,sort_keys=True,ensure_ascii=True,separators=(',',':'))+'\n').encode()
        self.assertEqual(self.hashes['example-double/1'],hashlib.sha256(raw).hexdigest())

if __name__=='__main__':unittest.main()
