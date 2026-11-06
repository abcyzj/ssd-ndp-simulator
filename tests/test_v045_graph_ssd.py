"""P13 actual SST dual placement; native capture then remove all payload inputs."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'workloads/graph-search/python'),str(ROOT/'workloads/graph-search/tests')]
from ndpsim.static_ir import canonical,decode,file_hash,OperatorRegistry,PackageWriter,validate_package
from ndpsim.cost_models import schema_hash
from ndpsim.layout import packed_layout
from graph_capture import run,save
from test_native_search import fixture
BUILD=Path(sys.argv.pop(1)).resolve()
TARGET=dict(id='ssd-test-target',hardware='hypothetical',os='none',frequency='fixed',affinity='one',memory_bytes=8192)
class Ssd(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
    evidence=os.environ.get('NDPSIM_SSD_EVIDENCE')
    cls.tmp=None if evidence else tempfile.TemporaryDirectory(prefix='p13 SSD 空格 ')
    cls.root=Path(evidence if evidence else cls.tmp.name).resolve()
    if evidence:cls.root.mkdir(parents=True,exist_ok=False)
    data=cls.root/'native-input';data.mkdir();files,*_=fixture(data)
    c=dict(format='ndpsim-graph-capture-config/2',shape=dict(count=6,dimensions=2,queries=11,degree=4,truth_k=4),search=dict(entry=0,k=4,width=4,batch=2,budget=3,chunk=3),files={k:dict(path=p,sha256=h) for k,p,h in zip(('vectors','queries','graph','truth','query_ids'),files[::2],files[1::2])},split='fixture',target=TARGET,budget=dict(native_rss_bytes=512*1024**2,postprocess_rss_bytes=512*1024**2,output_bytes=64*1024**2,query_bytes=4*1024**2,timeout_seconds=120,min_disk_free_bytes=1024**3))
    save(data/'capture.json',c);cls.capture=cls.root/'capture'
    run(data/'capture.json',BUILD/'ndpsim_native',ROOT/'extensions/graph/operators.json',cls.capture,'capture')
    shutil.rmtree(data)
    cls.ops=[json.loads(x) for x in (cls.capture/'package/operations.jsonl').read_text().splitlines()]
    cls.manifest=decode((cls.capture/'package/manifest.json').read_bytes())
    cls.report=decode((cls.capture/'structural.json').read_bytes())
    cls.registry=decode((cls.capture/'registry.json').read_bytes())
    save(cls.root/'layout.json',packed_layout(cls.manifest['objects'],capacity_sectors=512))
    cls.sst=decode((BUILD/'sst-paths.json').read_bytes())['sst'];cls.count=0
 @classmethod
 def tearDownClass(cls):
    if cls.tmp:cls.tmp.cleanup()
 def config(self,placement='host',block=2160,ns=2):
    type(self).count+=1;n=str(self.count);context='ssd_'+('host_return' if placement=='host' else 'controller_command')+'/1'
    rules=[]
    for op in self.registry['operators']:
     for st in op['stages']:
      features=st['feature_units']
      rule=dict(operator=op['id'],operator_version=op['version'],stage_kind=st['kind'],context=context,location='controller' if placement=='controller' and st['kind']=='score_l2' else 'host',target_id=TARGET['id'],feature_ranges=[dict(name=k,unit=u,minimum=0,maximum=100000) for k,u in features.items()],work_units=st['work_units'],source='prediction',formula='constant/1',parameter_schema_sha256=schema_hash('constant/1'),parameters=dict(ns=0 if st['kind']=='join' else ns),measurement_sha256=['1'*64],training_query_ids=[7],fit_report_sha256='2'*64)
      rules.append(rule)
    pp=self.root/('profile'+n+'.json');save(pp,dict(format='ndpsim-cost-profile/1',fixture=True,target=TARGET,rules=rules))
    return dict(format='ndpsim-graph-ssd-run/1',package=str(self.capture/'package'),scratch='scratch'+n+'.sqlite',report=str(self.capture/'structural.json'),report_sha256=file_hash(self.capture/'structural.json'),package_sha256=self.report['package_sha256'],registry=str(self.capture/'registry.json'),registry_sha256=file_hash(self.capture/'registry.json'),semantic_report=str(self.capture/'semantic.json'),semantic_sha256=file_hash(self.capture/'semantic.json'),allow_fixture=True,profile=pp.name,profile_sha256=file_hash(pp),target=TARGET,context=context,workers=1,slots=1,admission=1,output='trace'+n+'.jsonl',placement=placement,layout='layout.json',device=str(ROOT/'configs/devices/native-small.xml'),resources=dict(capacity_bytes=4096,query_bound=8,block_bound=block,staging_bytes_per_ns=16,compute_slots=1))
 def execute(self,c,good=True):
    cp=self.root/('config'+str(self.count)+'.json');save(cp,c)
    command=[self.sst,'--add-lib-path='+str(BUILD),str(ROOT/'configs/graph_ssd_timing.py'),'--',json.dumps(str(cp),ensure_ascii=False)]
    if os.environ.get('NDPSIM_SSD_NATIVE'):command=[str(BUILD/'graph_ssd_probe'),str(cp)]
    p=subprocess.run(command,cwd='/tmp',capture_output=True,text=True,timeout=60)
    save(cp.with_suffix('.command.json'),dict(command=p.args,cwd='/tmp',exit_code=p.returncode));cp.with_suffix('.stdout').write_text(p.stdout);cp.with_suffix('.stderr').write_text(p.stderr)
    self.assertEqual(p.returncode==0,good,p.stdout+p.stderr)
    out=self.root/c['output'];rows=[json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    if good:
      self.assertTrue(rows[-1]['success']);self.assertFalse(rows[-1]['full_acceptance_passed'])
      for k in ('runtime_tasks','pins','buffer_used','sessions'):self.assertEqual(rows[-1][k],0)
    else:self.assertFalse(any(r.get('type')=='summary' and r.get('success') for r in rows))
    return rows
 def test_two_placements_same_package(self):
    host=self.execute(self.config());ctrl=self.execute(self.config('controller'))
    logical=lambda rows:[r for r in rows if r['type']=='logical']
    self.assertEqual(logical(host),logical(ctrl))
    self.assertEqual(len(logical(host)),sum(len(o['stages']) for o in self.ops))
    self.assertGreater(host[-1]['vector_d2h'],0);self.assertEqual(ctrl[-1]['vector_d2h'],0)
    self.assertEqual(host[-1]['local_requests'],0);self.assertGreater(ctrl[-1]['local_requests'],0)
    # Each query has query-read and adjacency ordinary I/O in both mappings.
    for rows in (host,ctrl):
      ordinary=[r for r in rows if r['type']=='ordinary_read']
      self.assertEqual(sum(r['kind']=='init' for r in ordinary),11)
      self.assertEqual(sum(r['kind']=='adjacency_prepare' for r in ordinary),sum(s['kind']=='adjacency_prepare' for o in self.ops for s in o['stages']))
      stages={(r['query'],r['op'],r['stage']):r for r in rows if r['type']=='stage'}
      for op in self.ops:
       for s in op['stages']:
        for dep in s['dependencies']:
         k=dep['source']['key'];self.assertGreaterEqual(stages[op['query'],op['id'],s['id']]['submitted'],stages[k['query'],k['op'],k['stage']]['completed'])
    for rows in (host,ctrl):
      n=rows[-1]['ordinary_requests']
      arrivals=[r for r in rows if r['type']=='pcie' and r['event']=='arrive']
      layer=lambda name:[r for r in arrivals if r['layer']==name]
      self.assertEqual(len(layer('sq_fetch')),n)
      self.assertEqual(sum(r['payload'] for r in layer('sq_entry')),72*n)
      self.assertEqual(sum(r['payload'] for r in layer('cq_entry')),16*n)
      self.assertEqual(sum(r['payload'] for r in layer('doorbell')),4*n)
      self.assertEqual(sum(r['payload'] for r in layer('data')),sum(r['sector_bytes'] for r in rows if r['type']=='ordinary_plan'))
      for r in arrivals:
        self.assertEqual(r['wire'],32 if r['layer']=='sq_fetch' else r['payload']+28*((r['payload']+127)//128))
      self.assertTrue(all(r['cq_head_bytes']==2 for r in rows if r['type']=='ordinary_read'))
    self.assertEqual(sum(r['bytes'] for r in ctrl if r['type']=='nand_transaction'),sum(r['sector_bytes'] for r in ctrl if r['type']=='local_plan'))
    self.assertTrue(all(r['duration_ns']==(r['bytes']+15)//16 for r in ctrl if r['type']=='buffer_access'))
    results={r['chunk']:r for r in ctrl if r['type']=='protocol' and r['kind']==3}
    stages={(r['query'],r['op'],r['stage']):r for r in ctrl if r['type']=='stage'}
    for r in ctrl:
      if r['type']=='demand' and r['physical']:
        key=r['detail'];self.assertGreaterEqual(stages[key['query'],key['op'],key['stage']]['completed'],results[r['physical_chunk']]['arrived'])
    protocol=[r for r in ctrl if r['type']=='protocol']
    for r in protocol:
      # Open=64+8, ACK/Close=48; Execute=64+4b, Result=56+12b.
      if r['kind']==0:self.assertEqual((r['logical'],r['payload']),(8,72))
      elif r['kind']==2:self.assertEqual(r['payload'],64+r['logical'])
      elif r['kind']==3:self.assertEqual(r['payload'],56+r['logical'])
      else:self.assertEqual((r['logical'],r['payload']),(0,48))
      self.assertEqual(r['wire'],r['payload']+28*((r['payload']+127)//128))
    self.assertEqual(sum(r['kind']==0 for r in protocol),11)
    self.assertEqual(sum(r['kind']==5 for r in protocol),11)
 def test_capacity_refines_without_changing_logical_work(self):
    a=self.execute(self.config('controller',2160));b=self.execute(self.config('controller',592))
    self.assertEqual([r for r in a if r['type']=='logical'],[r for r in b if r['type']=='logical'])
    execute=lambda rows:[r for r in rows if r['type']=='protocol' and r['kind']==2]
    self.assertGreater(len(execute(b)),len(execute(a)))
    self.assertTrue(all(r['logical']==4 for r in execute(b)))
    work=lambda rows:sum(r['detail']['work']['flops']['value'] for r in rows if r['type']=='demand' and r['physical'])
    self.assertEqual(work(a),work(b))
    for rows in (a,b):
      for row in rows:
        if row['type']=='demand' and row['physical']:
          detail=row['detail'];n=detail['features']['vectors']['value'];d=detail['features']['dimensions']['value']
          expected=dict(elements=n*d,flops=3*n*d,read_bytes=4*n*d+4*d,write_bytes=12*n,output_bytes=12*n,candidate_checks=0,comparisons=0)
          self.assertEqual({k:v['value'] for k,v in detail['work'].items()},expected)
      self.assertLessEqual(max(r['used'] for r in rows if r['type']=='credit'),4096)
    reads=lambda rows:sum(r['detail']['work']['read_bytes']['value'] for r in rows if r['type']=='demand' and r['physical'])
    self.assertEqual(reads(b)-reads(a),8*(len(execute(b))-len(execute(a))))
    self.assertEqual(sum(r['logical'] for r in execute(a)),sum(r['logical'] for r in execute(b)))
 def test_repeat_is_deterministic(self):
    a=self.execute(self.config('controller'));b=self.execute(self.config('controller'))
    self.assertEqual([r for r in a if r['type']!='configuration'],[r for r in b if r['type']!='configuration'])
 def test_refuse_work_and_mapping_without_decomposition(self):
    for mode in ('none','work','unknown'):
      c=self.config('controller');ops=copy.deepcopy(self.ops);reg=copy.deepcopy(self.registry)
      if mode=='unknown':
        for op in ops:
          if op['operator']=='graph.JOIN':op['operator']='graph.UNKNOWN'
        for op in reg['operators']:
          if op['id']=='graph.JOIN':op['id']='graph.UNKNOWN'
      else:
        st=next(s for o in ops for s in o['stages'] if s['kind']=='score_l2')
        if mode=='none':st['decomposition_rule']='none/1'
        else:st['work']['flops']['value']+=1
      from graph_contract import registry as graph_registry
      spec=self.root/("spec"+str(self.count)+".json");save(spec,dict(operators=reg["operators"]))
      registry=graph_registry(spec);reg=registry.descriptor
      package=self.root/('mutated'+str(self.count))
      provenance=dict(self.manifest['provenance'],extension_sha256=registry.sha256)
      with PackageWriter(package,provenance,registry,allow_fixture=True) as w:
        for obj in self.manifest['objects']:w.declare_object(obj)
        for op in ops:w.record(op)
        w.seal(self.manifest['terminals'])
      report=validate_package(package,registry,allow_fixture=True)
      rp=self.root/('mutated-report'+str(self.count)+'.json');save(rp,report)
      regp=self.root/('mutated-registry'+str(self.count)+'.json');save(regp,reg)
      c.update(package=str(package),package_sha256=report['package_sha256'],report=str(rp),report_sha256=file_hash(rp),registry=str(regp),registry_sha256=registry.sha256)
      c.pop('semantic_report');c.pop('semantic_sha256')
      if mode=='unknown':
        pp=self.root/c['profile'];profile=decode(pp.read_bytes())
        for rule in profile['rules']:
          if rule['operator']=='graph.JOIN':rule['operator']='graph.UNKNOWN'
        save(pp,profile);c['profile_sha256']=file_hash(pp)
      self.execute(c,False)
      stderr=(self.root/('config'+str(self.count)+'.stderr')).read_text()
      self.assertIn({'none':'Unsupported Work decomposition','work':'L2 Work rule mismatch','unknown':'unknown graph mapping'}[mode],stderr)
 def test_measured_call_cannot_be_divided(self):
    c=self.config('controller',592);p=self.root/c['profile'];pr=decode(p.read_bytes())
    r=next(r for r in pr['rules'] if r['stage_kind']=='score_l2')
    for k in ('formula','parameter_schema_sha256','parameters','training_query_ids','fit_report_sha256'):r.pop(k)
    r.update(source='measured-table',quality_report_sha256='4'*64,measurement_sha256=['1'*64,'2'*64,'3'*64],binding=dict(package_sha256=c['package_sha256'],provenance=self.manifest['provenance'],target=TARGET,context=c['context'],workers=1,slots=1,admission=1),rows=[dict(key=dict(query=o['query'],op=o['id'],stage=s['id']),work=s['work'],features=s['features'],duration_ns=3) for o in self.ops for s in o['stages'] if s['kind']=='score_l2'])
    save(p,pr);c['profile_sha256']=file_hash(p);self.execute(c,False)
 def test_model_service_is_explicit(self):
    a=self.execute(self.config('controller',ns=0));b=self.execute(self.config('controller',ns=17))
    self.assertGreater(b[-1]['drained'],a[-1]['drained'])
    for row in b:
      if row['type']=='demand':self.assertEqual(row['detail']['duration_ns'],0 if row['detail']['stage_kind']=='join' else 17)
 def test_refuse_invalid_configuration_and_models(self):
    for field,value in [('placement','unknown'),('context','resident_random/2'),('profile_sha256','0'*64),('layout','missing.json'),('workers',2)]:
      c=self.config('controller');c[field]=value;self.execute(c,False)
    c=self.config('controller',block=575);self.execute(c,False)
    c=self.config('controller');p=self.root/c['profile'];j=decode(p.read_bytes());j['rules']=[r for r in j['rules'] if r['stage_kind']!='score_l2'];save(p,j);c['profile_sha256']=file_hash(p);self.execute(c,False)
if __name__=='__main__':unittest.main(verbosity=2)
