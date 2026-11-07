"""P15 independent layers: real installed SST workflows and resealed negatives."""
import copy
import os
from pathlib import Path
import shutil
import sys
import unittest
# P14 helpers install from python/ alone and generate explicit independent fixtures.
import test_v045_cli as cli
from ndpsim.execution import load, save, identities
from ndpsim.static_ir import canonical, file_hash, digest_bytes
from ndpsim.validation import Inputs, simulate, capture, accuracy, execute

ROOT=cli.ROOT; BUILD=cli.BUILD
BUDGET=dict(output_bytes=128*1024**2,scratch_bytes=64*1024**2,rss_bytes=768*1024**2,timeout_seconds=90,min_disk_free_bytes=1024**2)
LIMITS=dict(input_bytes=128*1024**2,records=100000)

def ref(p):return dict(path=str(p.resolve()),sha256=file_hash(p))

def reseal(directory):
    status=load(directory/'status.json')
    field='artifacts_sha256' if 'artifacts_sha256' in status else 'files_sha256'
    status[field]={n:file_hash(directory/n) for n in status[field]}
    save(directory/'status.json',status)

class Validation(unittest.TestCase):
 def test_metadata_context_actual_sst_and_tamper(self):
    from ndpsim.cost_models import schema_hash
    c=self.ssd.Ssd().config('host')
    c={k:v for k,v in c.items() if k not in ('placement','layout','device','resources')}
    c.update(format='ndpsim-memory-run/1',context='resident_random/2')
    registry=load(Path(c['registry']));rules=[]
    for op in registry['operators']:
      for st in op['stages']:
        r=dict(operator=op['id'],operator_version=op['version'],stage_kind=st['kind'],context=c['context'],location='host',target_id=c['target']['id'],feature_ranges=[dict(name=n,unit=u,minimum=0,maximum=100000) for n,u in st['feature_units'].items()],work_units=st['work_units'],source='prediction',formula='constant/1',parameter_schema_sha256=schema_hash('constant/1'),parameters=dict(ns=0 if st['kind'] in ('state','join') else 2),measurement_sha256=['1'*64],training_query_ids=[7],fit_report_sha256='2'*64)
        if st['kind']=='finish':
          r['metadata_features']=[dict(name='init_bytes',unit='B',rule='query-prefix-sum/1',operator='graph.INIT',operator_version='2',stage_kind='init',feature='copied_bytes')]
          r['feature_ranges'].append(dict(name='init_bytes',unit='B',minimum=8,maximum=8))
          r.update(formula='nonnegative-linear/1',parameter_schema_sha256=schema_hash('nonnegative-linear/1'),parameters=dict(intercept_ns=2,coefficients=[0,1]))
        rules.append(r)
    root=self.output();root.mkdir();p=root/'context-profile.json';save(p,dict(format='ndpsim-cost-profile/1',fixture=True,target=c['target'],rules=rules));c.update(profile=str(p),profile_sha256=file_hash(p))
    cp=root/'run.json';save(cp,c);ep=root/'experiment.json';save(ep,dict(format='ndpsim-experiment/1',run=cp.name,budget=BUDGET));out=self.output();self.h.invoke(['simulate','--config',ep,'--build',BUILD,'--output',out])
    self.validate('simulate',dict(run=ref(out/'status.json')))
    damaged=self.clone(out)
    def change(rows):
      r=next(r for r in rows if r['type']=='demand' and r['stage_kind']=='finish');r['metadata_features']['init_bytes']['value']+=1
    self.mutate_trace(damaged,change);self.validate('simulate',dict(run=ref(damaged/'status.json')),False)

 @classmethod
 def setUpClass(cls):
    evidence=os.environ.get('NDPSIM_VALIDATION_EVIDENCE')
    if evidence:
        os.environ['NDPSIM_CLI_EVIDENCE']=str(Path(evidence)/'cli')
        os.environ['NDPSIM_MEMORY_EVIDENCE']=str(Path(evidence)/'memory-fixture')
    cli.Cli.setUpClass();cls.h=cli.Cli();cls.root=cli.Cli.root;cls.n=0
    cls.runs=[]
    for i in range(2):
        ep,c=cls.h.experiment();out=cls.root/('resident-'+str(i))
        cls.h.invoke(['simulate','--config',ep,'--build',BUILD,'--output',out]);cls.runs.append(out)
    # P13 creates a new graph capture and removes every payload before simulation.
    sys.argv.insert(1,str(BUILD))
    import test_v045_graph_ssd as ssd
    cls.ssd=ssd
    if evidence:os.environ['NDPSIM_SSD_EVIDENCE']=str(Path(evidence)/'graph-fixture')
    ssd.Ssd.setUpClass(semantic_chunk=1);cls.graph=[]
    for placement in ('host','controller'):
        c=ssd.Ssd().config(placement,block=592)
        if placement == 'controller':
            profile_path = ssd.Ssd.root/c['profile']
            profile = load(profile_path)
            for rule in profile['rules']:
                for field in ('measurement_sha256', 'training_query_ids', 'fit_report_sha256'):
                    rule.pop(field)
                rule.update(source='configured', parameter_source='fixture scenario')
            save(profile_path, profile)
            c['profile_sha256'] = file_hash(profile_path)
        cp=ssd.Ssd.root/(placement+'.json');save(cp,c)
        ep=ssd.Ssd.root/(placement+'-experiment.json');save(ep,dict(format='ndpsim-experiment/1',run=cp.name,budget=BUDGET))
        out=cls.root/('ssd-'+placement);cls.h.invoke(['simulate','--config',ep,'--build',BUILD,'--output',out]);cls.graph.append(out)
 @classmethod
 def tearDownClass(cls):
    cls.ssd.Ssd.tearDownClass();cli.Cli.tearDownClass()
 def output(self):
    type(self).n+=1;p=self.root/('validation-'+str(self.n));return p
 def inputs(self):return Inputs(Path.cwd(),LIMITS)
 def validate(self,layer,values,good=True,scope='fixture'):
    path=self.output().with_suffix('.json');save(path,dict(format='ndpsim-validation/1',layer=layer,scope=scope,budget=BUDGET,limits=LIMITS,inputs=values));out=self.output()
    self.h.invoke(['verify','--spec',path,'--output',out],good)
    status=load(out/'status.json');self.assertEqual(status['passed'],good);self.assertFalse(status['full_acceptance_passed'])
    if good:self.assertTrue(load(out/'report.json')['layer_passed'])
    else:self.assertFalse((out/'report.json').exists())
    return out
 def clone(self,source):
    out=self.output();shutil.copytree(source,out);return out
 def mutate_trace(self,directory,fn):
    p=directory/'trace.jsonl';rows=[cli.json.loads(s) for s in p.read_text().splitlines()];fn(rows);p.write_bytes(b''.join(canonical(r) for r in rows));reseal(directory)
 def test_real_resident_and_ssd_reports(self):
    self.assertTrue(any(sum(stage['kind'] == 'score_prepare' for stage in op['stages']) > 1
                        for op in self.ssd.Ssd.ops))
    for directory in self.runs+self.graph:
        out=self.validate('simulate',dict(run=ref(directory/'status.json')))
        r=load(out/'report.json');self.assertGreater(len(r['queries']),0)
        if directory.name == 'ssd-controller':
            self.assertEqual(r['ledger']['ordinary_requests'], load(directory/'report.json')['drain']['ordinary_requests'])
            self.assertEqual(r['ledger']['vector_d2h'], 0)
            self.assertGreater(r['ledger']['physical_chunks'], 33)
            self.assertEqual(set(r['service_sources']), {'configured'})

 def test_configured_profile_provenance_is_required_and_exclusive(self):
    directory = self.graph[1]
    for change in ('missing', 'mixed'):
        config = load(directory/'run-input.json')
        profile_path = Path(config['profile'])
        profile = load(profile_path if profile_path.is_absolute() else self.ssd.Ssd.root/profile_path)
        if change == 'missing':
            profile['rules'][0].pop('parameter_source')
        else:
            profile['rules'][0]['measurement_sha256'] = ['1'*64]
        path = self.output().with_suffix('.json')
        from ndpsim.cost_models import profile_schema
        with self.assertRaisesRegex(ValueError, 'Profile schema'):
            profile_schema(profile)
        save(path, profile)
        original = load(directory/'effective-config.json')
        for key in ('output', 'scratch'):
            original.pop(key)
        original.update(profile=str(path), profile_sha256=file_hash(path))
        cp = self.output().with_suffix('.json')
        save(cp, original)
        ep = self.output().with_suffix('.json')
        save(ep, dict(format='ndpsim-experiment/1', run=str(cp), budget=BUDGET))
        out = self.output()
        self.h.invoke(['simulate', '--config', ep, '--build', BUILD, '--output', out], False)
        self.assertIn('schema alternative', (out/'stderr.log').read_text())

 def test_resident_fifo_window(self):
    # Hand calculated resident FIFO: 4 queries, each has four 3ns services.
    r=load(self.runs[0]/'report.json');self.assertEqual(r['latency_ns'],dict(p50=12,p95=12,p99=12));self.assertEqual(r['offered_latency_ns']['p50'],24)
 def test_capture_receipt_reference_and_full_coverage(self):
    d=self.ssd.Ssd.capture;m=load(d/'package/manifest.json')
    spec=dict(package=str(d/'package'),package_sha256=load(d/'structural.json')['package_sha256'],registry=ref(d/'registry.json'),semantic=ref(d/'semantic.json'),reference=ref(d/'reference.json'))
    self.validate('capture',spec)
    for change in ('reference','semantic','coverage'):
        value=copy.deepcopy(spec);p=self.output().with_suffix('.json');r=load(d/('reference.json' if change=='reference' else 'semantic.json'))
        if change=='reference':r['queries'][0]['work']['flops']+=1;value['reference']=ref_save(p,r)
        elif change=='semantic':r['independent_semantic_passed']=False;value['semantic']=ref_save(p,r)
        else:r['query_ids'].pop();value['semantic']=ref_save(p,r)
        self.validate('capture',value,False)
    self.validate('capture',spec,False,scope='development')
 def test_resealed_missing_duplicate_and_dependency_service(self):
    changes=[lambda r:r.pop(next(i for i,x in enumerate(r) if x['type']=='stage')),
             lambda r:r.insert(1,copy.deepcopy(next(x for x in r if x['type']=='service'))),
             lambda r:next(x for x in r if x['type']=='stage' and x['op']=='left').update(submitted=0),
             lambda r:next(x for x in r if x['type']=='service').update(end=999),
             lambda r:next(x for x in r if x['type']=='stage' and x['query']==0).update(query=False),
             lambda r:next(x for x in r if x['type']=='demand')['work']['elements'].update(value=16.0),
             lambda r:r[-1].update(pins=1),lambda r:r[-1].update(success=False)]
    for change in changes:
        d=self.clone(self.runs[0]);self.mutate_trace(d,change);self.validate('simulate',dict(run=ref(d/'status.json')),False)
 def test_resealed_ssd_ledgers_and_return_gate(self):
    for kind,field in [('pcie','wire'),('protocol','payload'),('credit','used'),('local_read','sectors'),('nand_transaction','bytes'),('demand','work'),('buffer_access','bytes')]:
        d=self.clone(self.graph[1])
        def change(rows):
            r=next(r for r in rows if r['type']==kind and (kind!='demand' or r.get('physical')))
            if field=='work':r['detail']['work']['flops']['value']+=1
            else:r[field]+=1
        self.mutate_trace(d,change);self.validate('simulate',dict(run=ref(d/'status.json')),False)
    d=self.clone(self.graph[1]);self.mutate_trace(d,lambda rows:rows.pop(next(i for i,r in enumerate(rows) if r['type']=='buffer_access')));self.validate('simulate',dict(run=ref(d/'status.json')),False)
    d=self.clone(self.graph[1]);self.mutate_trace(d,lambda rows:next(r for r in rows if r['type']=='protocol' and r['kind']==3).update(arrived=2**40));self.validate('simulate',dict(run=ref(d/'status.json')),False)
    d = self.clone(self.graph[1])
    def reverse_data_packet(rows):
        packet = next(r['id'] for r in rows if r['type'] == 'pcie' and r['layer'] == 'data')
        for row in rows:
            if row['type'] == 'pcie' and row['id'] == packet:
                row['destination'] = 1
    self.mutate_trace(d, reverse_data_packet)
    self.validate('simulate', dict(run=ref(d/'status.json')), False)
 def test_hash_quick_budget_and_failed_runs(self):
    d=self.clone(self.runs[0]);p=d/'trace.jsonl';p.write_bytes(p.read_bytes()+b'{}\n');self.validate('simulate',dict(run=ref(d/'status.json')),False)
    d=self.clone(self.runs[0]);s=load(d/'status.json');s['passed']=False;save(d/'status.json',s);self.validate('simulate',dict(run=ref(d/'status.json')),False)
    path=self.output().with_suffix('.json');save(path,dict(format='ndpsim-validation/1',layer='simulate',scope='quick',budget=BUDGET,limits=LIMITS,inputs=dict(run=ref(self.runs[0]/'status.json'))));self.h.invoke(['verify','--spec',path,'--output',self.output()],False)
    p=self.output();p.mkdir()
    with self.assertRaisesRegex(ValueError,'budget'):simulate(dict(run=ref(self.runs[0]/'status.json')),Inputs(Path.cwd(),dict(input_bytes=1,records=1)),p)
 def test_zero_services_and_worker_quotas(self):
    for ns,workers in [(0,2),(3,2),(3,4)]:
        c=self.h.fixture.config(ns,workers);base=cli.memory.Memory.root
        cp=base/('quota'+str(workers)+'-'+str(ns)+'.json');save(cp,c)
        ep=cp.with_name(cp.stem+'-experiment.json');save(ep,dict(format='ndpsim-experiment/1',run=cp.name,budget=BUDGET))
        out=self.output();self.h.invoke(['simulate','--config',ep,'--build',BUILD,'--output',out])
        self.validate('simulate',dict(run=ref(out/'status.json')))
 def test_accuracy_repetition_gates(self):
    spec=dict(native=[],simulated=[ref(self.runs[0]/'status.json')],capture={},stage_evaluation={})
    self.validate('accuracy',spec,False)
    spec['native']=[{}, {}, {}];spec['simulated']=[ref(self.runs[0]/'status.json')]*2
    self.validate('accuracy',spec,False)
 def test_accuracy_full_fixture_and_deleted_sample_table(self):
    import test_v045_fitting as fitting
    from ndpsim.fitting import execute as fit
    root=self.output();root.mkdir();m,recipe,datasets=fitting.fixture(root,context='resident_random/2')
    fit('fit',root/'manifest.json',root/'frozen',allow_fixture=True)
    m.update(frozen_profile=ref(root/'frozen/profile.json'),frozen_report=ref(root/'frozen/fit-report.json'),datasets=[datasets['evaluation']]);mp=root/'evaluation.json';save(mp,m)
    fit('table',mp,root/'table',allow_fixture=True)
    ds=datasets['evaluation'];package=Path(ds['package']['path']);prov=load(package/'manifest.json')['provenance']
    # Explicit synthetic oracle receipt for the known independent fixture, not graph results.
    reference=dict(completed=8,queries=[dict(original_query_id=q) for q in prov['query_ids']]);rp=root/'capture-reference.json';save(rp,reference)
    receipt=dict(structural_passed=True,independent_semantic_passed=True,semantic_validation='independent-full-query',package_sha256=ds['package']['sha256'],provenance=prov,reference_report_sha256=file_hash(rp),query_ids=prov['query_ids'],counts=load(package/'manifest.json')['counts'],semantic_work={'flops':864})
    sp=root/'capture-semantic.json';save(sp,receipt)
    registry_path=root/'registry.json'
    # Fitting fixture's registered zero-size rule is explicit and independent.
    reg=dict(format='ndpsim-operator-registry/1',units=['count','flop'],operators=recipe['operators'],rules=[dict(id='zero/1',kind='fixed-outputs/1',bytes={'out':0})]);save(registry_path,reg)
    config=dict(format='ndpsim-memory-run/1',package=str(package),package_sha256=ds['package']['sha256'],registry=str(registry_path),registry_sha256=file_hash(registry_path),report=ds['structural_report']['path'],report_sha256=ds['structural_report']['sha256'],profile=str(root/'table/profile.json'),profile_sha256=file_hash(root/'table/profile.json'),target=fitting.TARGET,context='resident_random/2',workers=1,slots=1,admission=1,allow_fixture=True,output='unused',scratch='unused')
    cp=root/'run.json';save(cp,config);ep=root/'experiment.json';save(ep,dict(format='ndpsim-experiment/1',run=cp.name,budget=BUDGET))
    runs=[]
    for i in range(2):
        out=root/('sim-'+str(i));self.h.invoke(['simulate','--config',ep,'--build',BUILD,'--output',out]);runs.append(ref(out/'status.json'))
    checked=root/'checked';checked.mkdir();sr=simulate(dict(run=runs[0]),self.inputs(),checked)
    native=[]
    for i in range(3):
        d=root/('benchmark-'+str(i));d.mkdir()
        queries=[dict(original_query_id=q['query'],admission_ns=q['admission_ns'],visible_ns=q['visible_ns'],latency_ns=q['latency_ns'],thread_cpu_ns=q['latency_ns']) for q in sr['queries']]
        save(d/'reference.json',dict(run_id='synthetic-'+str(i),workers=1,concurrency=1,major_faults=0,empty_probe_count=0,exit_code=0,completed=8,queries=queries,thread_cpu_ns=sum(q['thread_cpu_ns'] for q in queries),window_ns=sr['measurement_window_ns']))
        save(d/'config.json',dict(format='ndpsim-native-benchmark-config/1',binding=dict(provenance=prov,target=fitting.TARGET,context='resident_random/2')))
        save(d/'build.json',prov['capture_build']);save(d/'commands.json',[dict(argv=['synthetic-fixture'],exit_code=0)])
        save(d/'status.json',dict(passed=True,mode='benchmark',files_sha256=identities(d)));native.append(ref(d/'status.json'))
    spec=dict(native=native,simulated=runs,capture=dict(package=str(package),package_sha256=ds['package']['sha256'],registry=ref(registry_path),semantic=ref(sp),reference=ref(rp)),stage_evaluation=ref(mp))
    out=self.validate('accuracy',spec);self.assertEqual(load(out/'report.json')['errors'],dict(p50=0,p95=0,qps=0,cpu=0))
    bad_native=self.clone(root/'benchmark-0');r=load(bad_native/'reference.json')
    for q in r['queries']: q['latency_ns']*=2;q['visible_ns']=q['admission_ns']+q['latency_ns'];q['thread_cpu_ns']*=2
    r['window_ns']=max(q['visible_ns'] for q in r['queries']);r['thread_cpu_ns']=sum(q['thread_cpu_ns'] for q in r['queries']);save(bad_native/'reference.json',r);reseal(bad_native)
    bad_spec=copy.deepcopy(spec);bad_spec['native']=[ref(bad_native/'status.json')]+native[1:]
    # One outlier is allowed by the frozen median; two bad independent runs fail.
    bad2=self.clone(bad_native);r['run_id']='synthetic-bad-second';save(bad2/'reference.json',r);reseal(bad2);bad_spec['native'][1]=ref(bad2/'status.json')
    self.validate('accuracy',bad_spec,False)
    # Freshly resealed table tamper must disagree with actual service, not just its checksum.
    bad=self.clone(root/'sim-0');p=bad/'inputs/profile.json';pr=load(p);next(r for r in pr['rules'] if r['source']=='measured-table')['rows'][0]['duration_ns']+=1;save(p,pr)
    config=load(bad/'effective-config.json');config['profile_sha256']=file_hash(p);save(bad/'effective-config.json',config)
    report=load(bad/'report.json');report['profile_sha256']=file_hash(p);save(bad/'report.json',report)
    def update_snapshot(rows):
        rows[0]['snapshot'].update(profile=pr,config=config,config_sha256=file_hash(bad/'effective-config.json'))
    self.mutate_trace(bad,update_snapshot)
    self.validate('simulate',dict(run=ref(bad/'status.json')),False)
    # Delete a Measurement sample, update status+manifest hashes: P06 quality still rejects.
    sample=m['datasets'][0]['runs'][0];p=Path(sample['measurement']['path']);mdata=load(p);mdata['samples'].pop();save(p,mdata);sample['measurement']=ref(p);sd=p.parent;reseal(sd);sample['status']=ref(sd/'status.json');save(mp,m);spec['stage_evaluation']=ref(mp)
    self.validate('accuracy',spec,False)

def ref_save(path,value):save(path,value);return ref(path)
if __name__=='__main__':unittest.main(verbosity=2)
