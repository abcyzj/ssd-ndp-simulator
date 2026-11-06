"""Independent IR/2 capture, measurement and semantic corruption checks."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

BINARY=Path(sys.argv.pop(1)).resolve();SDK_PYTHON=Path(sys.argv.pop(1)).resolve();OPERATORS=Path(sys.argv.pop(1)).resolve()
sys.path.insert(0,str(SDK_PYTHON));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from graph_capture import run,configuration,semantic_check,measurement,load,save,signature
from graph_contract import registry
from ndpsim.static_ir import PackageWriter,validate_package,Limits
from test_native_search import fixture

class CaptureV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix='graph-v2-tests-'));cls.reg=registry(OPERATORS)
        files,*_=fixture(cls.root)
        cls.config=dict(format='ndpsim-graph-capture-config/2',shape=dict(count=6,dimensions=2,queries=11,degree=4,truth_k=4),search=dict(entry=0,k=4,width=4,batch=2,budget=3,chunk=1),files={k:dict(path=p,sha256=h) for k,p,h in zip(('vectors','queries','graph','truth','query_ids'),files[::2],files[1::2])},split='fixture',target=dict(id='test-host',hardware='local-fixture',os=sys.platform,memory_bytes=17179869184,frequency='Unsupported',affinity='uncontrolled'),budget=dict(native_rss_bytes=512*1024**2,postprocess_rss_bytes=512*1024**2,output_bytes=64*1024**2,query_bytes=4*1024**2,timeout_seconds=120,min_disk_free_bytes=1024**3))
        cls.path=cls.root/'config.json';save(cls.path,cls.config)
        cls.ref=run(cls.path,BINARY,OPERATORS,cls.root/'capture','capture');cls.package=cls.root/'capture/package'
        cls.sample_ref=run(cls.path,BINARY,OPERATORS,cls.root/'sample','sample',cls.package)
        cls.ops=[json.loads(line) for line in (cls.package/'operations.jsonl').read_text().splitlines()]
        cls.manifest=load(cls.package/'manifest.json');cls.counter=0
    @classmethod
    def out(cls,name):
        cls.counter+=1;return cls.root/(name+str(cls.counter))
    def reseal(self,ops):
        out=self.out('resealed')
        with PackageWriter(out,self.manifest['provenance'],self.reg,allow_fixture=True) as writer:
            for obj in self.manifest['objects']:writer.declare_object(obj)
            for op in ops:writer.record(op)
            writer.seal(self.manifest['terminals'])
        return out
    def test_all_modes_independent_reference(self):
        for mode in ('benchmark','uninstrumented'):
            r=run(self.path,BINARY,OPERATORS,self.out(mode),mode)
            self.assertEqual(signature(self.ref),signature(r))
        self.assertEqual(signature(self.ref),signature(self.sample_ref))
        report=load(self.root/'capture/semantic.json');self.assertTrue(report['independent_semantic_passed']);self.assertFalse(report['full_acceptance_passed'])
        self.assertEqual((report['counts']['operations'],report['counts']['stages']),(253,385))
    def test_raw_stream_and_graph_spec(self):
        for q in self.manifest['provenance']['query_ids']:
            m=load(self.root/'capture/raw'/str(q)/'capture.json');self.assertEqual(m['operators'],self.reg.descriptor['operators']);self.assertTrue(m['complete'])
        self.assertEqual({s['kind'] for o in self.ops for s in o['stages']},{'init','state','score_prepare','score_l2','merge_append','merge_finalize','select','adjacency_prepare','join','dedup','finish'})
        self.assertEqual({d['kind'] for o in self.ops for s in o['stages'] for d in s['dependencies']},{'DATA','ADDRESS','CONTROL','ORDER'})
    def test_all_query_sampling_and_stride(self):
        m=load(self.root/'sample/measurement.json');self.assertEqual(m['sampling']['eligible'],308);self.assertEqual(m['sampling']['observed'],308)
        overhead=m['clock']['empty_probe_ns']//m['clock']['empty_probe_count']
        self.assertTrue(all(s['corrected_ns']==s['raw_ns']-overhead and s['low_signal']==(s['corrected_ns']<m['clock']['resolution_ns']) for s in m['samples']))
        out=self.out('stride');r=run(self.path,BINARY,OPERATORS,out,'sample',self.package,3)
        fake=copy.deepcopy(self.sample_ref);fake['empty_probe_count']=1;fake['empty_probe_ns']=10**12
        signed=measurement(self.root/'sample/raw',self.package,self.config,fake,1,self.config['target'])
        self.assertTrue(all(s['corrected_ns']<0 and s['low_signal'] for s in signed['samples']))
        sparse=load(out/'measurement.json');self.assertLess(len(sparse['samples']),len(m['samples']));self.assertEqual(sparse['sampling']['observed'],sparse['sampling']['planned'])
    def test_rehashed_semantic_corruptions(self):
        for mode in ('semantic-work','stage-work','feature','digest','access-order','state-attribution','control-kind'):
            with self.subTest(mode=mode):
                ops=copy.deepcopy(self.ops);score=next(o for o in ops if o['operator']=='graph.SCORE_L2' and len(o['stages'])>3)
                if mode=='semantic-work':score['semantic_work']['flops']['value']+=1
                if mode=='stage-work':score['stages'][1]['work']['comparisons']['value']+=1
                if mode=='feature':score['stages'][2]['features']['released_vectors']['value']+=1
                if mode=='digest':score['stages'][1]['outputs'][1]['digest']='1'*64
                if mode=='access-order':score['stages'][0]['accesses'][0]['offset_bytes']=0
                if mode=='state-attribution':score['stages'][0]['features']['state_objects']['value']=0
                if mode=='control-kind':
                    mark=next(o for o in ops if o['operator']=='graph.MARK_EXPANDED');next(d for d in mark['stages'][0]['dependencies'] if d['kind']=='CONTROL')['kind']='DATA'
                package=self.reseal(ops);self.assertTrue(validate_package(package,self.reg,allow_fixture=True)['structural_passed'])
                with self.assertRaises(ValueError):semantic_check(package,self.config,OPERATORS,self.ref)
    def test_legal_physical_reorder(self):
        package=self.reseal(list(reversed(self.ops)));self.assertTrue(semantic_check(package,self.config,OPERATORS,self.ref)['independent_semantic_passed'])
    def test_sampling_on_reordered_package_and_adjacency_branches(self):
        package=self.reseal(list(reversed(self.ops)))
        m=measurement(self.root/'sample/raw',package,self.config,self.sample_ref,1,self.config['target'])
        self.assertEqual(m['sampling']['observed'],308)
        first=[o for o in self.ops if o['query']==self.manifest['provenance']['query_ids'][0]]
        reads=[o for o in first if o['operator']=='graph.READ_ADJACENCY']
        # The second expansion chooses two rows: both depend on the same batch mark,
        # not on each other. JOIN still requires all row outputs.
        self.assertEqual(reads[1]['stages'][0]['dependencies'][0]['source'],reads[2]['stages'][0]['dependencies'][0]['source'])
        self.assertEqual(__import__('hashlib').sha256((self.root/'capture/reference.json').read_bytes()).hexdigest(),load(self.root/'capture/semantic.json')['reference_report_sha256'])
    def test_missing_truncated_corrupt_package(self):
        for mode in ('missing','truncated','hash'):
            path=self.out('broken');shutil.copytree(self.package,path)
            if mode=='missing':(path/'index.jsonl').unlink()
            elif mode=='truncated':p=path/'operations.jsonl';p.write_bytes(p.read_bytes()[:-1])
            else:p=path/'operations.jsonl';p.write_bytes(p.read_bytes().replace(b'"comparisons"',b'"compxrisons"',1))
            with self.assertRaises((ValueError,FileNotFoundError)):validate_package(path,self.reg,allow_fixture=True)
    def test_reference_tampering(self):
        for field in ('top_k','work','expanded','recall_at_k'):
            r=copy.deepcopy(self.ref)
            if field=='top_k':r['queries'][0][field][0]['distance']+=1
            elif field=='work':r['queries'][0][field]['flops']+=1
            else:r['queries'][0][field]+=1
            with self.assertRaises(ValueError):semantic_check(self.package,self.config,OPERATORS,r)
    def test_measurement_missing_duplicate_corrupt(self):
        q=self.manifest['provenance']['query_ids'][0]
        for mode in ('missing','duplicate','work','float-work','boolean-work','timestamp','raw-correction'):
            raw=self.out('raw');shutil.copytree(self.root/'sample/raw',raw);p=raw/(str(q)+'.jsonl');rows=[json.loads(x) for x in p.read_text().splitlines()]
            if mode=='missing':rows.pop()
            elif mode=='duplicate':rows.append(rows[0])
            elif mode=='work':rows[0]['work']['flops']['value']+=1
            elif mode=='float-work':rows[0]['work']['flops']['value']=float(rows[0]['work']['flops']['value'])
            elif mode=='boolean-work':rows[0]['work']['flops']['value']=False
            elif mode=='timestamp':rows[1]['start_cpu_ns']=rows[0]['start_cpu_ns']
            else:rows[0]['corrected_ns']-=1
            p.write_bytes(b''.join(__import__('ndpsim.static_ir',fromlist=['canonical']).canonical(r) for r in rows))
            with self.assertRaises(ValueError):measurement(raw,self.package,self.config,self.sample_ref,1,self.config['target'])
    def test_budget_sampling_and_publish_failure(self):
        for mode in ('sample-capture','output','post-rss'):
            v=copy.deepcopy(self.config);out=self.out('failure');cfg=self.out('config')
            if mode=='output':v['budget']['output_bytes']=v['budget']['query_bytes']=1
            if mode=='post-rss':v['budget']['postprocess_rss_bytes']=1
            save(cfg,v)
            with self.assertRaises(ValueError):run(cfg,BINARY,OPERATORS,out,'capture',stride=2 if mode=='sample-capture' else 1)
            self.assertFalse(load(out/'status.json')['passed']);self.assertFalse((out/'package').exists())
        with self.assertRaises(ValueError):run(self.path,BINARY,OPERATORS,self.root/'capture','capture')
    def test_payload_free_package_and_formal_split_gate(self):
        text=(self.package/'manifest.json').read_text()
        self.assertNotIn(str(self.root),text);self.assertNotIn('path',json.dumps(self.manifest['objects']))
        path=self.out('config');v=copy.deepcopy(self.config);v['split']='tuning32';save(path,v)
        with self.assertRaises(ValueError):configuration(path)
    @classmethod
    def tearDownClass(cls):print('P04 test artifacts:',cls.root)

if __name__=='__main__':unittest.main(verbosity=2)
