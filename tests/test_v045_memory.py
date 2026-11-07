"""P11 actual SST/Profile wiring: independent service sums, quotas and refusals."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from ndpsim.static_ir import OperatorRegistry,PackageWriter,canonical,decode,file_hash,validate_package
from ndpsim.cost_models import schema_hash
BUILD=Path(sys.argv.pop(1)).resolve()
from test_v045_capture import fixture
TARGET=dict(id='test-target',hardware='hypothetical',os='target-os',memory_bytes=8192,frequency='fixed',affinity='one')
class Memory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence=os.environ.get('NDPSIM_MEMORY_EVIDENCE')
        cls.temp=None if evidence else tempfile.TemporaryDirectory(prefix='p11 memory ')
        cls.root=Path(evidence if evidence else cls.temp.name).resolve()
        if evidence:cls.root.mkdir(parents=True,exist_ok=False)
        cls.reg=OperatorRegistry.load(ROOT/'examples/p02-capture/registry.json')
        (cls.root/'registry.json').write_bytes(canonical(cls.reg.descriptor))
        p,obj,ops,terms=fixture(cls.reg,(0,1,2,3))
        cls.package=cls.root/'package'
        with PackageWriter(cls.package,p,cls.reg,allow_fixture=True) as w:
            w.declare_object(obj)
            for op in reversed(ops):w.record(op)
            w.seal(terms)
        cls.report=validate_package(cls.package,cls.reg,allow_fixture=True)
        (cls.root/'report.json').write_bytes(canonical(cls.report))
        cls.sst=json.loads((BUILD/'sst-paths.json').read_text())['sst'];cls.count=0
    @classmethod
    def tearDownClass(cls):
        if cls.temp:cls.temp.cleanup()
    def config(self,ns=3,workers=1,slots=None,admission=None):
        type(self).count+=1;n=str(self.count)
        rule=dict(operator='fixture.reduce',operator_version='1',stage_kind='reduce',context='fixture/1',location='host',target_id=TARGET['id'],feature_ranges=[],work_units=dict(elements='count'),source='prediction',formula='constant/1',parameter_schema_sha256=schema_hash('constant/1'),parameters=dict(ns=ns),measurement_sha256=['1'*64],training_query_ids=[7],fit_report_sha256='2'*64)
        pr=dict(format='ndpsim-cost-profile/1',fixture=True,target=TARGET,rules=[rule])
        pp=self.root/('profile'+n+'.json');pp.write_bytes(canonical(pr))
        return dict(format='ndpsim-memory-run/1',package='package',scratch='scratch'+n+'.sqlite',report='report.json',report_sha256=file_hash(self.root/'report.json'),package_sha256=self.report['package_sha256'],registry='registry.json',registry_sha256=self.reg.sha256,allow_fixture=True,profile=pp.name,profile_sha256=file_hash(pp),target=TARGET,context='fixture/1',workers=workers,slots=slots or workers,admission=admission or workers,output='trace'+n+'.jsonl')
    def run_config(self,c,good=True):
        cp=self.root/('config'+str(self.count)+'.json');cp.write_bytes(canonical(c))
        run=subprocess.run([self.sst,'--add-lib-path='+str(BUILD),str(ROOT/'configs/memory_timing.py'),'--',json.dumps(str(cp),ensure_ascii=False)],cwd='/tmp',capture_output=True,text=True)
        (cp.with_suffix('.stdout')).write_text(run.stdout);(cp.with_suffix('.stderr')).write_text(run.stderr)
        self.assertEqual(run.returncode==0,good,run.stdout+run.stderr)
        out=self.root/c['output']
        rows=[json.loads(s) for s in out.read_text().splitlines()] if out.exists() else []
        if not good:
            self.assertFalse(any(r.get('type')=='summary' and r['success'] for r in rows));return rows
        summary=rows[-1];self.assertEqual(summary['type'],'summary');self.assertTrue(summary['success'])
        for k in ('ssd_requests','device_instances','active_stages','outstanding','runtime_tasks','pins','resource_pending'):self.assertEqual(summary[k],0)
        return rows
    def test_hand_time_workers_model_and_zero(self):
        for w in (1,2,4):
            for ns in (0,3,17):
                with self.subTest(workers=w,ns=ns):
                    rows=self.run_config(self.config(ns,w));s=rows[-1]
                    self.assertEqual(s['cpu_demand_ns'],16*ns)
                    self.assertEqual(s['drained'],16*ns//w)
                    self.assertEqual(s['peak_workers'],w);self.assertLessEqual(s['peak_slots'],w)
                    demand={(r['query'],r['op'],r['stage']):r for r in rows if r['type']=='demand'}
                    service=[r for r in rows if r['type']=='service'];self.assertEqual(len(service),16)
                    for r in service:
                        k=json.loads(r['key']);self.assertEqual(demand[(k['query'],k['op'],k['stage'])]['duration_ns'],r['duration_ns'])
                        self.assertEqual(r['end']-r['start'],ns)
                    stages={(r['query'],r['op']):r for r in rows if r['type']=='stage'}
                    for q in range(4):
                        for op in ('left','right'):self.assertGreaterEqual(stages[q,op]['submitted'],stages[q,'root']['completed'])
                        self.assertGreaterEqual(stages[q,'join']['submitted'],max(stages[q,o]['completed'] for o in ('left','right')))
    def test_slots_and_admission_are_separate(self):
        for w,slots,admission,expected in [(2,4,4,18),(4,1,4,48),(1,4,4,36)]:
            rows=self.run_config(self.config(3,w,slots,admission));self.assertEqual(rows[-1]['drained'],expected)
            self.assertLessEqual(rows[-1]['peak_workers'],min(w,admission));self.assertLessEqual(rows[-1]['peak_slots'],slots)
    def test_graph_capture_without_payload(self):
        sys.path.insert(0,str(ROOT/'workloads/graph-search/python'))
        sys.path.insert(0,str(ROOT/'workloads/graph-search/tests'))
        from graph_capture import run,save
        from test_native_search import fixture as graph_fixture
        data=self.root/'native-input';data.mkdir()
        files,*_=graph_fixture(data)
        native=dict(format='ndpsim-graph-capture-config/2',shape=dict(count=6,dimensions=2,queries=11,degree=4,truth_k=4),search=dict(entry=0,k=4,width=4,batch=2,budget=3,chunk=1),files={k:dict(path=p,sha256=h) for k,p,h in zip(('vectors','queries','graph','truth','query_ids'),files[::2],files[1::2])},split='fixture',target=TARGET,budget=dict(native_rss_bytes=512*1024**2,postprocess_rss_bytes=512*1024**2,output_bytes=64*1024**2,query_bytes=4*1024**2,timeout_seconds=120,min_disk_free_bytes=1024**3))
        nc=data/'capture.json';save(nc,native)
        capture=self.root/'graph-capture'
        run(nc,BUILD/'ndpsim_native',ROOT/'extensions/graph/operators.json',capture,'capture')
        # Delete every native payload file before launching the simulator.
        shutil.rmtree(data)
        c=self.config();c.update(package=str(capture/'package'),registry=str(capture/'registry.json'),registry_sha256=file_hash(capture/'registry.json'),report=str(capture/'structural.json'),report_sha256=file_hash(capture/'structural.json'),semantic_report=str(capture/'semantic.json'),semantic_sha256=file_hash(capture/'semantic.json'),package_sha256=decode((capture/'structural.json').read_bytes())['package_sha256'],context='resident_random/2')
        reg=decode((capture/'registry.json').read_bytes());ops=[json.loads(x) for x in (capture/'package/operations.jsonl').read_text().splitlines()]
        rules=[]
        for op in reg['operators']:
            for st in op['stages']:
                features={}
                for o in ops:
                    if o['operator']==op['id']:
                        for stage in o['stages']:
                            if stage['kind']==st['kind']:
                                features.update({k:v['unit'] for k,v in stage['features'].items()})
                rule=dict(operator=op['id'],operator_version=op['version'],stage_kind=st['kind'],context=c['context'],location='host',target_id=TARGET['id'],feature_ranges=[dict(name=k,unit=u,minimum=0,maximum=100000) for k,u in features.items()],work_units=st['work_units'],source='prediction',formula='constant/1',parameter_schema_sha256=schema_hash('constant/1'),parameters=dict(ns=0 if st['kind'] in ('state','join') else 2),measurement_sha256=['1'*64],training_query_ids=[7],fit_report_sha256='2'*64)
                rules.append(rule)
        pp=self.root/c['profile'];pp.write_bytes(canonical(dict(format='ndpsim-cost-profile/1',fixture=True,target=TARGET,rules=rules)));c['profile_sha256']=file_hash(pp)
        rows=self.run_config(c);expected=sum(0 if s['kind'] in ('state','join') else 2 for o in ops for s in o['stages'])
        self.assertEqual(rows[-1]['cpu_demand_ns'],expected);self.assertEqual(rows[-1]['drained'],expected)
        self.assertEqual(len([r for r in rows if r['type']=='demand']),385)
        # Pure metadata context must work after payload deletion, without
        # modifying stage Work/features or inserting target service.
        finish=next(r for r in rules if r['stage_kind']=='finish')
        finish['metadata_features']=[dict(name='init_bytes',unit='B',rule='query-prefix-sum/1',operator='graph.INIT',operator_version='2',stage_kind='init',feature='copied_bytes')]
        finish['feature_ranges'].append(dict(name='init_bytes',unit='B',minimum=8,maximum=8))
        finish.update(formula='nonnegative-linear/1',parameter_schema_sha256=schema_hash('nonnegative-linear/1'),parameters=dict(intercept_ns=2,coefficients=[0,1]))
        pp.write_bytes(canonical(dict(format='ndpsim-cost-profile/1',fixture=True,target=TARGET,rules=rules)))
        c2=dict(c,profile_sha256=file_hash(pp),output='context-events.jsonl',scratch='context-scratch.sqlite')
        type(self).count+=1;context=self.run_config(c2)
        self.assertEqual(context[-1]['cpu_demand_ns'],expected+8*11)
        for r in context:
            if r['type']=='demand' and r['stage_kind']=='finish':
                self.assertEqual(r['metadata_features'],dict(init_bytes=dict(value=8,unit='B')))
                self.assertEqual(r['duration_ns'],10)
        from graph_contract import registry as graph_registry
        reg=graph_registry(ROOT/'extensions/graph/operators.json');manifest=decode((capture/'package/manifest.json').read_bytes())
        reordered=self.root/'context-reordered'
        with PackageWriter(reordered,manifest['provenance'],reg,allow_fixture=True) as w:
            for obj in manifest['objects']:w.declare_object(obj)
            for op in reversed(ops):w.record(op)
            w.seal(manifest['terminals'])
        rp=self.root/'context-reordered-report.json';save(rp,validate_package(reordered,reg,allow_fixture=True))
        c3=dict(c2,package=str(reordered),package_sha256=decode(rp.read_bytes())['package_sha256'],report=str(rp),report_sha256=file_hash(rp),output='context-reordered-events.jsonl',scratch='context-reordered.sqlite')
        for name in ('semantic_report','semantic_sha256'):c3.pop(name,None)
        type(self).count+=1;other=self.run_config(c3)
        self.assertEqual(other[-1]['cpu_demand_ns'],context[-1]['cpu_demand_ns'])
    def test_measured_table_exact_binding(self):
        for bad in ('none','quota','missing','work'):
            c=self.config();pp=self.root/c['profile'];pr=decode(pp.read_bytes());rule=pr['rules'][0]
            for k in ('formula','parameter_schema_sha256','parameters','training_query_ids','fit_report_sha256'):rule.pop(k)
            binding=dict(package_sha256=c['package_sha256'],provenance=decode((self.package/'manifest.json').read_bytes())['provenance'],target=TARGET,context=c['context'],workers=1,slots=1,admission=1)
            table=[dict(key=dict(query=q,op=op,stage='s0'),work=dict(elements=dict(value=16,unit='count')),features={},duration_ns=3) for q in range(4) for op in ('root','left','right','join')]
            if bad=='quota':binding['workers']=2
            if bad=='missing':table.pop()
            if bad=='work':table[0]['work']['elements']['value']=17
            rule.update(source='measured-table',quality_report_sha256='4'*64,binding=binding,measurement_sha256=['1'*64,'2'*64,'3'*64],rows=table)
            pp.write_bytes(canonical(pr));c['profile_sha256']=file_hash(pp)
            rows=self.run_config(c,bad=='none')
            if bad=='none':self.assertEqual(rows[-1]['drained'],48)
    def test_refusals(self):
        for field,value in [('profile_sha256','0'*64),('registry_sha256','0'*64),('report_sha256','0'*64),('package_sha256','0'*64),('context','ssd_host_return/1'),('workers',True),('slots',0),('admission',1.0),('vectors','missing.fvecs'),('allow_fixture',False),('target',dict(TARGET,memory_bytes=True)),('target',dict(TARGET,extra='unknown')),('semantic_sha256','1'*64),('workers',5)]:
            with self.subTest(field=field):
                c=self.config();c[field]=value;self.run_config(c,False)
        for mutation in ('missing','range','target'):
            c=self.config();p=self.root/c['profile'];pr=decode(p.read_bytes())
            if mutation=='missing':pr['rules']=[]
            if mutation=='range':pr['rules'][0]['feature_ranges']=[dict(name='elements',unit='count',minimum=0,maximum=1)]
            if mutation=='target':c['target']=dict(TARGET,hardware='different')
            p.write_bytes(canonical(pr));c['profile_sha256']=file_hash(p);self.run_config(c,False)
    def test_host_burden_does_not_enter_service(self):
        c=self.config();a=self.run_config(c)
        # Extra metadata descriptions/hash parsing, identical semantic package and rules.
        c2=self.config();p=self.root/c2['profile'];pr=decode(p.read_bytes())
        pr['rules'][0]['measurement_sha256']=[hashlib.sha256(str(i).encode()).hexdigest() for i in range(2000)]
        p.write_bytes(canonical(pr));c2['profile_sha256']=file_hash(p)
        b=self.run_config(c2)
        self.assertEqual([r for r in a if r['type']!='configuration'],[r for r in b if r['type']!='configuration'])
        burn=subprocess.Popen([sys.executable,'-c',"import hashlib; print('ready',flush=True)\nwhile True: hashlib.sha256(b'x'*65536).digest()"],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(burn.stdout.readline().strip(),'ready')
            loaded=self.run_config(self.config())
        finally:
            burn.terminate();burn.wait(timeout=10);burn.stdout.close()
        self.assertEqual([r for r in a if r['type']!='configuration'],[r for r in loaded if r['type']!='configuration'])
if __name__=='__main__':unittest.main(verbosity=2)
