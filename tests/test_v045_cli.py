"""P14 installed CLI: real SST, paths, snapshots, reports and failure closure."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
if '--evidence-dir' in sys.argv:
    index=sys.argv.index('--evidence-dir');os.environ['NDPSIM_CLI_EVIDENCE']=sys.argv[index+1];del sys.argv[index:index+2]
BUILD=Path(sys.argv.pop(1)).resolve()
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'tests')]
# Reuse fixture generation only; all execution goes through the unified installed CLI.
sys.argv.insert(1,str(BUILD))
import test_v045_memory as memory
from ndpsim.static_ir import ValidationError,canonical,file_hash
from ndpsim.execution import TRACE_RECORD_BYTES,launch,trace_records

class TraceRecords(unittest.TestCase):
 def test_only_leading_configuration_can_exceed_event_record_budget(self):
    with tempfile.TemporaryDirectory() as tmp:
      path=Path(tmp)/'trace.jsonl'
      configuration=dict(type='configuration',snapshot='x'*TRACE_RECORD_BYTES)
      summary=dict(type='summary',success=True)
      path.write_bytes(canonical(configuration)+canonical(summary))
      self.assertEqual(list(trace_records(path)),[configuration,summary])
      event=canonical(dict(type='stage',detail=configuration['snapshot']))
      path.write_bytes(canonical(configuration)+event)
      with self.assertRaisesRegex(ValidationError,'trace record budget'):
        list(trace_records(path))
      path.write_bytes(event)
      with self.assertRaisesRegex(ValidationError,'trace must start with configuration'):
        list(trace_records(path))

class Cli(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
    evidence=os.environ.get('NDPSIM_CLI_EVIDENCE')
    cls.tmp=None if evidence else tempfile.TemporaryDirectory(prefix='p14 CLI 中文 空格 ')
    cls.root=Path(evidence or cls.tmp.name).resolve()
    if evidence:cls.root.mkdir(parents=True,exist_ok=False)
    cls.install=cls.root/'installed tools';cls.install.mkdir()
    # Offline installation from python/ alone, no repo tools imported by installed CLI.
    source=cls.root/'isolated source';shutil.copytree(ROOT/'python',source,ignore=shutil.ignore_patterns('__pycache__','*.egg-info','build'))
    p=subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-build-isolation','--target',str(cls.install),str(source)],capture_output=True,text=True)
    (cls.root/'install.stdout').write_text(p.stdout);(cls.root/'install.stderr').write_text(p.stderr)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    cls.env=dict(os.environ,PYTHONPATH=str(cls.install))
    cls.cli=cls.install/'bin/ndpsim';assert cls.cli.is_file()
    memory.Memory.setUpClass();cls.fixture=memory.Memory();cls.count=0
 @classmethod
 def tearDownClass(cls):
    memory.Memory.tearDownClass()
    if cls.tmp:cls.tmp.cleanup()
 def invoke(self,command,good=True):
    p=subprocess.run([str(self.cli),*map(str,command)],cwd='/tmp',env=self.env,capture_output=True,text=True,timeout=90)
    type(self).count+=1
    (self.root/('cmd'+str(self.count)+'.json')).write_bytes(canonical(dict(argv=p.args,cwd='/tmp',exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr)))
    self.assertEqual(p.returncode==0,good,p.stdout+p.stderr);return p
 def experiment(self,**override):
    c=self.fixture.config()
    # Preserve relative paths in the declaring run config; outputs in input config are ignored.
    base=memory.Memory.root
    cp=base/('cli-run'+str(memory.Memory.count)+'.json');cp.write_bytes(canonical(c))
    ep=base/('cli-experiment'+str(memory.Memory.count)+'.json')
    b=dict(output_bytes=32*1024**2,scratch_bytes=32*1024**2,rss_bytes=512*1024**2,timeout_seconds=60,min_disk_free_bytes=1024**2);b.update(override)
    ep.write_bytes(canonical(dict(format='ndpsim-experiment/1',run=cp.name,budget=b)))
    return ep,c
 def output(self,name):return self.root/(name+' '+str(self.count))
 def test_installed_memory_reports_and_no_clobber(self):
    self.invoke(['--help']);self.invoke(['--version'])
    spec,c=self.experiment();out=self.output('memory output')
    relative=lambda p:os.path.relpath(p,Path('/tmp').resolve())
    self.invoke(['simulate','--config',relative(spec),'--build',relative(BUILD),'--output',relative(out)])
    r=json.loads((out/'report.json').read_text());self.assertEqual(r['requests'],4)
    self.assertEqual(r['measurement_window_ns'],48);self.assertEqual(r['service_sources'],{'prediction':16})
    self.assertEqual(r['service_demand_ns_by_location'],{'host':48});self.assertFalse(r['full_acceptance_passed'])
    self.assertTrue((out/'stages.csv').is_file());self.assertEqual(len((out/'services.csv').read_text().splitlines()),17)
    self.assertEqual(r['drain']['ssd_requests'],0);self.assertEqual(r['reference_results']['status'],'Unsupported')
    h=file_hash(out/'status.json');self.invoke(['simulate','--config',spec,'--build',BUILD,'--output',out],False);self.assertEqual(h,file_hash(out/'status.json'))
    effective=json.loads((out/'effective-config.json').read_text());self.assertTrue(Path(effective['package']).is_relative_to(out));self.assertTrue((out/'inputs/profile.json').is_file())
 def test_invalid_hash_budget_build_and_exit_codes(self):
    for field,val in [('output_bytes',1),('scratch_bytes',1),('rss_bytes',1),('min_disk_free_bytes',2**62)]:
      spec,c=self.experiment(**{field:val});out=self.output('bad budget')
      self.invoke(['simulate','--config',spec,'--build',BUILD,'--output',out],False)
      self.assertFalse(json.loads((out/'status.json').read_text())['passed'])
    spec,c=self.experiment();d=json.loads(spec.read_text());cp=spec.parent/d['run'];c['profile_sha256']='0'*64;cp.write_bytes(canonical(c));out=self.output('bad hash')
    self.invoke(['simulate','--config',spec,'--build',BUILD,'--output',out],False);self.assertFalse((out/'report.json').exists())
    self.assertIsInstance(json.loads((out/'command.json').read_text())['exit_code'],int)
    spec,c=self.experiment();out=self.output('no build');self.invoke(['simulate','--config',spec,'--build',out/'absent','--output',out],False)
    self.assertFalse((out/'report.json').exists());self.assertFalse((out/'absent').exists())
 def test_structure_check_and_rejection(self):
    out=self.output('check.json');self.invoke(['check','--package',memory.Memory.package,'--registry',memory.Memory.root/'registry.json','--allow-fixture','--report',out])
    r=json.loads(out.read_text());self.assertTrue(r['structural_passed']);self.assertFalse(r['independent_semantic_passed'])
    out=self.output('bad-check.json');self.invoke(['check','--package',memory.Memory.package,'--registry',memory.Memory.root/'registry.json','--allow-fixture','--report',out,'--expected-package-sha256','0'*64],False)
    self.assertFalse(json.loads(out.read_text())['structural_passed'])
 def test_compare_and_verify_fail_closed(self):
    reports=[]
    for i in range(2):
      spec,c=self.experiment();out=self.output('comparison run');self.invoke(['simulate','--config',spec,'--build',BUILD,'--output',out]);reports.append(out/'report.json')
    spec=self.root/'comparison.json';spec.write_bytes(canonical(dict(format='ndpsim-report-comparison/1',runs=[dict(path=str(p),sha256=file_hash(p)) for p in reports])))
    out=self.output('compare');self.invoke(['compare','--spec',spec,'--output',out]);self.assertFalse(json.loads((out/'report.json').read_text())['full_acceptance_passed'])
    original=json.loads(spec.read_text());duplicate=dict(original,runs=[original['runs'][0],original['runs'][0]]);spec.write_bytes(canonical(duplicate))
    self.invoke(['compare','--spec',spec,'--output',self.output('duplicate compare')],False)
    spec.write_bytes(canonical(original));status=reports[0].parent/'status.json';bad=json.loads(status.read_text());bad['passed']=False;status.write_bytes(canonical(bad))
    self.invoke(['compare','--spec',spec,'--output',self.output('failed compare')],False)
    out=self.output('verify');p=self.invoke(['verify','--spec',spec,'--output',out],False);self.assertEqual(p.returncode,3);self.assertFalse(json.loads((out/'status.json').read_text())['passed'])
 def test_timeout_kills_child_and_preserves_record(self):
    out=self.output('timeout');out.mkdir();b=dict(output_bytes=1024**2,scratch_bytes=1024**2,rss_bytes=512*1024**2,timeout_seconds=1,min_disk_free_bytes=1)
    with self.assertRaisesRegex(ValueError,'timeout'):launch([sys.executable,'-c','import time; time.sleep(20)'],out,b)
    self.assertTrue(json.loads((out/'command.json').read_text())['terminated_by_budget'])
 def test_unified_fit_table_evaluate_and_failure(self):
    import test_v045_fitting as fitting
    import copy
    root=self.output('fit inputs');root.mkdir();manifest,recipe,datasets=fitting.fixture(root)
    bp=root/'budget.json';bp.write_bytes(canonical(dict(output_bytes=32*1024**2,scratch_bytes=32*1024**2,rss_bytes=512*1024**2,timeout_seconds=60,min_disk_free_bytes=1024**2)))
    out=self.output('fit');self.invoke(['fit','--manifest',root/'manifest.json','--budget',bp,'--allow-fixture','--output',out])
    frozen=dict(frozen_profile=dict(path=str(out/'fit/profile.json'),sha256=file_hash(out/'fit/profile.json')),frozen_report=dict(path=str(out/'fit/fit-report.json'),sha256=file_hash(out/'fit/fit-report.json')))
    for action in ('table','evaluate'):
      m=copy.deepcopy(manifest);m.update(frozen,datasets=[datasets['evaluation']]);mp=root/(action+'.json');mp.write_bytes(canonical(m))
      target=self.output(action);self.invoke(['fit','--action',action,'--manifest',mp,'--budget',bp,'--allow-fixture','--output',target]);self.assertTrue(json.loads((target/'fit/status.json').read_text())['passed'])
    out=self.output('bad fit');self.invoke(['fit','--manifest',root/'manifest.json','--budget',bp,'--output',out],False);self.assertFalse(json.loads((out/'status.json').read_text())['passed']);self.assertFalse((out/'fit/profile.json').exists())
 def test_unified_capture_and_ssd_placements_without_payload(self):
    sys.path[:0]=[str(ROOT/'workloads/graph-search/python'),str(ROOT/'workloads/graph-search/tests')]
    from test_native_search import fixture
    from ndpsim.layout import packed_layout
    sys.argv.insert(1,str(BUILD))
    import test_v045_graph_ssd as ssd
    data=self.output('payload inputs');data.mkdir();files,*_=fixture(data)
    config=dict(format='ndpsim-graph-capture-config/2',shape=dict(count=6,dimensions=2,queries=11,degree=4,truth_k=4),search=dict(entry=0,k=4,width=4,batch=2,budget=3,chunk=3),files={k:dict(path=str(Path(p).relative_to(data)),sha256=h) for k,p,h in zip(('vectors','queries','graph','truth','query_ids'),files[::2],files[1::2])},split='fixture',target=ssd.TARGET,budget=dict(native_rss_bytes=512*1024**2,postprocess_rss_bytes=512*1024**2,output_bytes=64*1024**2,query_bytes=4*1024**2,timeout_seconds=120,min_disk_free_bytes=1024**2))
    cp=data/'capture config.json';cp.write_bytes(canonical(config));out=self.output('capture')
    command=['capture','--workload','graph','--adapter',ROOT/'workloads/graph-search/python/graph_capture.py','--native',BUILD/'ndpsim_native','--operators',ROOT/'extensions/graph/operators.json','--config',cp,'--output',out]
    self.invoke(command)
    self.assertIn('capture/status.json',json.loads((out/'status.json').read_text())['artifacts_sha256'])
    self.assertTrue(json.loads((out/'capture/semantic.json').read_text())['independent_semantic_passed'])
    check=self.output('graph semantic.json')
    self.invoke(['check','--adapter',ROOT/'workloads/graph-search/python/graph_capture.py','--package',out/'capture/package','--registry',out/'capture/registry.json','--semantic-config',cp,'--reference',out/'capture/reference.json','--report',check,'--allow-fixture'])
    self.assertTrue(json.loads(check.read_text())['independent_semantic_passed'])
    # benchmark remains a distinct explicitly requested native run.
    bench=self.output('benchmark');command[-1]=bench;self.invoke(command+['--mode','benchmark']);self.assertFalse((bench/'capture/package').exists())
    shutil.rmtree(data)
    check=self.output('graph structure.json')
    self.invoke(['check','--adapter',ROOT/'workloads/graph-search/python/graph_capture.py','--package',out/'capture/package','--registry',out/'capture/registry.json','--report',check,'--allow-fixture'])
    self.assertFalse(json.loads(check.read_text())['independent_semantic_passed'])
    helper=ssd.Ssd();ssd.Ssd.root=out;ssd.Ssd.capture=out/'capture';ssd.Ssd.count=0
    ssd.Ssd.registry=json.loads((out/'capture/registry.json').read_text());ssd.Ssd.report=json.loads((out/'capture/structural.json').read_text())
    manifest=json.loads((out/'capture/package/manifest.json').read_text());(out/'layout.json').write_bytes(canonical(packed_layout(manifest['objects'],capacity_sectors=512)))
    for placement in ('host','controller'):
      c=helper.config(placement);run=out/(placement+' run.json');run.write_bytes(canonical(c))
      spec=out/(placement+' experiment.json');spec.write_bytes(canonical(dict(format='ndpsim-experiment/1',run=run.name,budget=dict(output_bytes=64*1024**2,scratch_bytes=32*1024**2,rss_bytes=512*1024**2,timeout_seconds=60,min_disk_free_bytes=1024**2))))
      target=self.output('SSD '+placement);self.invoke(['simulate','--config',spec,'--build',BUILD,'--output',target])
      r=json.loads((target/'report.json').read_text());self.assertEqual(r['requests'],11);self.assertEqual(r['semantic_validation'],'trusted-independent-receipt');self.assertGreater(r['traffic']['ordinary_sector_bytes'],0)
      self.assertGreater(len((target/'services.csv').read_text().splitlines()),1)
      if placement=='controller':self.assertEqual(r['drain']['vector_d2h'],0);self.assertIn('controller',r['service_demand_ns_by_location'])

if __name__=='__main__':unittest.main(verbosity=2)
