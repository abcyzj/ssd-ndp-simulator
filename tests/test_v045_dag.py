"""P08 scheduling oracles, adversarial input, reordering and active metadata bounds."""
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from ndpsim.static_ir import (Limits,OperatorRegistry,PackageWriter,ValidationError,
    canonical,decode,file_hash,validate_package,_package_id)
from test_v045_capture import fixture
BINARY=Path(sys.argv.pop(1)).resolve() if len(sys.argv)>1 and not sys.argv[1].startswith('-') else ROOT/'build/delivery/dag_executor_probe'

class DagTests(unittest.TestCase):
    evidence=os.environ.get('NDPSIM_DAG_EVIDENCE')
    gradients=[]
    def setUp(self):
        if self.evidence:
            Path(self.evidence).mkdir(parents=True,exist_ok=True)
            self.root=Path(tempfile.mkdtemp(prefix=self._testMethodName+'-',dir=self.evidence))
        else:self.temp=tempfile.TemporaryDirectory(prefix='p08-dag-');self.root=Path(self.temp.name)
        self.registry=OperatorRegistry(decode((ROOT/'examples/p02-capture/registry.json').read_bytes()))
        self.serial=0
    def tearDown(self):
        if not self.evidence:self.temp.cleanup()
    @classmethod
    def tearDownClass(cls):
        if cls.evidence:(Path(cls.evidence)/'q-gradient.json').write_text(json.dumps(cls.gradients,indent=2)+'\n')
    def package(self,queries=(0,),name='package',multistage=False,multiple_terminals=False):
        provenance,obj,ops,terminals=fixture(self.registry,queries)
        if multistage:
            # root and left are distinct stages of the same operation/line.
            for q in queries:
                root=next(o for o in ops if o['query']==q and o['id']=='root')
                left=next(o for o in ops if o['query']==q and o['id']=='left')
                left['stages'][0]['id']='s1';left['stages'][0]['segment']=1;root['stages'].append(left['stages'][0])
                ops.remove(left)
                for o in ops:
                    for s in o['stages']:
                        for d in s['dependencies']:
                            k=d['source']['key']
                            if k['query']==q and k['op']=='left':k['op']='root';k['stage']='s1'
        if multiple_terminals:
            for q in queries:
                join=next(o for o in ops if o['query']==q and o['id']=='join')
                join['stages'][0]['dependencies'][1]['source']['key']['op']='left'
                terminals.append(dict(query=q,op='right',stage='s0'))
        p=self.root/name
        with PackageWriter(p,provenance,self.registry,allow_fixture=True) as writer:
            writer.declare_object(obj)
            for op in reversed(ops):writer.record(op)
            writer.seal(terminals)
        return p
    def receipt(self,p):
        report=validate_package(p,self.registry,allow_fixture=True)
        r=self.root/('report-'+str(self.serial)+'.json');r.write_bytes(canonical(report))
        return dict(package=str(p),report=str(r),report_sha256=file_hash(r),package_sha256=report['package_sha256'],registry_sha256=self.registry.sha256,allow_fixture=True)
    def execute(self,p,code=0,**changes):
        self.serial+=1;c=self.receipt(p)
        c.update(scratch=str(self.root/f'scratch-{self.serial}.sqlite'),durations=dict(root=2,left=5,right=11,join=3),returns=dict(right=7))
        c.update(changes);return self.run_config(c,code)
    def run_config(self,c,code=0):
        self.serial+=1;config=self.root/f'config-{self.serial}.json';config.write_bytes(canonical(c));out=self.root/f'result-{self.serial}.json'
        argv=[str(BINARY),str(config),str(out)]
        run=subprocess.run(argv,capture_output=True,text=True)
        (self.root/f'stdout-{self.serial}.log').write_text(run.stdout);(self.root/f'stderr-{self.serial}.log').write_text(run.stderr)
        self.assertEqual(run.returncode,code,run.stderr)
        if code==2:self.assertFalse(out.exists());return None
        result=json.loads(out.read_text())
        for k in ('active_queries','active_workers','active_stages','active_index_bytes','outstanding','ready_edges','runtime_tasks','runtime_pins','resource_pending'):self.assertEqual(result[k],0,k)
        return result
    def test_fork_join_all_edges_and_return(self):
        p=self.package();r=self.execute(p)
        by={s['op']:s for s in r['stages']}
        self.assertEqual({k:(s['submitted'],s['completed']) for k,s in by.items()},dict(root=(0,2),left=(2,7),right=(2,20),join=(20,23)))
        self.assertEqual(r['requests'][0]['visible'],23);self.assertEqual(r['drained'],23)
        self.assertEqual(r['detail_reads'],4)
        self.assertLessEqual(r['peak_detail_bytes'],max(map(len,(p/'operations.jsonl').read_bytes().splitlines(keepends=True))))
    def test_each_dependency_gates_visible(self):
        p=self.package()
        for returns,expected in [(dict(root=13,right=7),36),(dict(left=31,right=7),41),(dict(right=31),47)]:
            with self.subTest(returns=returns):self.assertEqual(self.execute(p,returns=returns)['drained'],expected)
    def test_unicode_keys_and_space_paths(self):
        provenance,obj,ops,terminals=fixture(self.registry,(0,))
        for op in ops:
            if op['id']=='root':op['id']='根🚀';op['stages'][0]['id']='阶段'
            for stage in op['stages']:
                for dep in stage['dependencies']:
                    if dep['source']['key']['op']=='root':dep['source']['key'].update(op='根🚀',stage='阶段')
        p=self.root/'package with spaces'
        with PackageWriter(p,provenance,self.registry,allow_fixture=True) as writer:
            writer.declare_object(obj)
            for op in reversed(ops):writer.record(op)
            writer.seal(terminals)
        r=self.execute(p,durations={'根🚀':2,'left':5,'right':11,'join':3})
        self.assertEqual(r['drained'],23)
        self.assertIn('根🚀',[s['op'] for s in r['stages']])
    def test_zero_service(self):
        r=self.execute(self.package(),durations=dict.fromkeys(('root','left','right','join'),0),returns={})
        self.assertEqual(r['drained'],0);self.assertEqual(r['services'],4)
    def test_fifo_workers_and_admission(self):
        p=self.package(tuple(range(5)))
        r=self.execute(p,admission=2,workers=1)
        self.assertEqual([q['worker_start'] for q in r['requests']],[0,23,46,69,92])
        self.assertEqual([q['admitted'] for q in r['requests']],[0,0,23,46,69])
        self.assertEqual(r['peak_queries'],2);self.assertEqual(r['peak_workers'],1)
        for workers in (2,4):
            r=self.execute(p,admission=workers,workers=workers,slots=workers*2)
            self.assertEqual(r['peak_workers'],workers)
            self.assertEqual([q['visible'] for q in r['requests']],[23*(i//workers+1) for i in range(5)])
    def test_offered_order_and_uint64_id(self):
        p=self.package((2**64-1,0,8))
        r=self.execute(p,admission=2,workers=1,offered_ns=[10,0,0])
        self.assertEqual([q['query'] for q in r['requests']],[0,8,2**64-1])
        self.assertEqual([q['visible'] for q in r['requests']],[23,46,69])
        self.assertEqual(r['requests'][2]['offered'],10)
    def resign(self,p):
        m=decode((p/'manifest.json').read_bytes())
        for row in m['files']:row.update(bytes=(p/row['path']).stat().st_size,sha256=file_hash(p/row['path']))
        (p/'manifest.json').write_bytes(canonical(m));t=decode((p/'tail.json').read_bytes())
        t['counts']=m['counts'];t['terminals']=m['terminals']
        t['package_sha256']=_package_id(file_hash(p/'manifest.json'),file_hash(p/'index.jsonl'),file_hash(p/'operations.jsonl'))
        (p/'tail.json').write_bytes(canonical(t))
    def reorder(self,p):
        rows=[decode(x) for x in (p/'operations.jsonl').read_bytes().splitlines()];random.Random(123).shuffle(rows)
        positions={};raw=b''
        for o in rows:
            b=canonical(o);positions[(o['query'],o['id'])]=(len(raw),len(b));raw+=b
        (p/'operations.jsonl').write_bytes(raw)
        indexes=[decode(x) for x in (p/'index.jsonl').read_bytes().splitlines()]
        for i in indexes:i['offset_bytes'],i['length_bytes']=positions[(i['key']['query'],i['key']['op'])]
        random.Random(321).shuffle(indexes);(p/'index.jsonl').write_bytes(b''.join(map(canonical,indexes)));self.resign(p)
    def test_physical_order_and_window_boundaries(self):
        p=self.package((0,1,2));base=self.execute(p,admission=2,workers=2,slots=4)
        self.reorder(p);other=self.execute(p,admission=2,workers=2,slots=4)
        self.assertEqual(base['stages'],other['stages']);self.assertEqual(base['requests'],other['requests'])
        # Details are read singly by byte offsets; distant file positions cannot block ready siblings.
        self.assertEqual(other['detail_reads'],12);self.assertEqual(other['requests'][0]['visible'],23)
    def test_multiple_stages_share_operation_line(self):
        p=self.package(multistage=True);r=self.execute(p)
        self.assertEqual(r['detail_reads'],4);self.assertEqual(len(r['stages']),4)
        self.assertEqual({s['stage'] for s in r['stages']},{'s0','s1'})
    def test_multi_terminal_and_same_source_ports(self):
        r=self.execute(self.package(multiple_terminals=True))
        by={s['op']:s for s in r['stages']}
        self.assertEqual(by['join']['completed'],10)
        self.assertEqual(by['right']['completed'],20)
        self.assertEqual(r['requests'][0]['visible'],20)
        self.assertEqual(r['drained'],20)
    def test_failure_stops_descendants_and_drains(self):
        p=self.package(tuple(range(3)))
        r=self.execute(p,code=3,admission=1,workers=1,fail_op='left',returns=dict(left=1,right=7))
        self.assertFalse(r['success']);self.assertEqual(r['services'],3);self.assertEqual(r['drained'],13)
        self.assertEqual(r['admitted'],1);self.assertEqual(r['skipped'],2)
        self.assertNotIn('join',[s['op'] for s in r['stages']]);self.assertEqual(r['failure']['code'],'Injected')
        # Failure after the other branch began its return must drain that return.
        r=self.execute(p,code=3,fail_op='left',returns=dict(left=11,right=7))
        self.assertEqual(r['drained'],20);self.assertEqual(r['services'],3)
    def test_failure_cancels_future_offered(self):
        p=self.package((0,1));r=self.execute(p,code=3,admission=2,workers=2,slots=4,offered_ns=[0,10**9],fail_op='root',returns=dict(root=1))
        self.assertEqual(r['drained'],3);self.assertEqual(r['skipped'],1);self.assertEqual(r['services'],1)
    def test_lowering_metadata_mismatch(self):
        r=self.execute(self.package(),code=3,mutate_work=True)
        self.assertEqual(r['services'],0);self.assertEqual(r['detail_reads'],1)
        r=self.execute(self.package(name='missing-lowering'),code=3,durations=dict(root=2))
        self.assertEqual(r['services'],1)
    def test_active_budget_refuses_serial_fallback(self):
        p=self.package((0,1))
        self.execute(p,code=2,admission=2,workers=2,max_active_stages=7)
        self.execute(p,code=2,max_active_index_bytes=1)
        self.execute(p,code=2,workers=2,admission=1)
        self.execute(p,code=2,offered_ns=[0])
        self.execute(p,code=2,scratch_bytes=4096)
    def test_receipt_and_input_mutations(self):
        p=self.package();c=self.receipt(p);c.update(scratch=str(self.root/'scratch.sqlite'),durations={})
        for field in ('report_sha256','package_sha256','registry_sha256'):
            bad=copy.deepcopy(c);bad[field]='0'*64;self.run_config(bad,2)
        bad=copy.deepcopy(c);bad['allow_fixture']=False;self.run_config(bad,2)
        original=(p/'tail.json').read_bytes()
        tail=decode(original);tail['extra']='unvalidated';(p/'tail.json').write_bytes(canonical(tail));self.run_config(c,2)
        tail=decode(original);tail['counts']['stages']=float(tail['counts']['stages']);(p/'tail.json').write_bytes(canonical(tail));self.run_config(c,2)
        (p/'tail.json').write_bytes(original)
        (p/'operations.jsonl').write_bytes((p/'operations.jsonl').read_bytes()+b' ');self.run_config(c,2)
    def test_cycles_and_missing_terminals_rejected(self):
        p=self.package();idx=[decode(x) for x in (p/'index.jsonl').read_bytes().splitlines()]
        idx[0]['dependencies']=[dict(kind='ORDER',source=dict(key=idx[-1]['key'],port='out'),input='in0')]
        (p/'index.jsonl').write_bytes(b''.join(map(canonical,idx)));self.resign(p)
        with self.assertRaises(ValidationError):self.receipt(p)
        # Even a forged trusted report cannot make admission accept a cyclic query.
        m=decode((p/'manifest.json').read_bytes());m['counts']['dependencies']+=1;(p/'manifest.json').write_bytes(canonical(m));self.resign(p)
        report=dict(format='ndpsim-package-validation/1',structural_passed=True,source='synthetic-fixture',package_sha256=decode((p/'tail.json').read_bytes())['package_sha256'],registry_sha256=self.registry.sha256,counts=m['counts'])
        rp=self.root/'forged.json';rp.write_bytes(canonical(report))
        c=dict(package=str(p),report=str(rp),report_sha256=file_hash(rp),package_sha256=report['package_sha256'],registry_sha256=self.registry.sha256,allow_fixture=True,scratch=str(self.root/'scratch.sqlite'),durations={})
        r=self.run_config(c,3);self.assertEqual(r['services'],0)
        p=self.package(name='missing-terminal');m=decode((p/'manifest.json').read_bytes());m['terminals']=[];(p/'manifest.json').write_bytes(canonical(m));self.resign(p)
        with self.assertRaises(ValidationError):self.receipt(p)
    def test_duplicate_index_record_rejected(self):
        p=self.package();(p/'index.jsonl').write_bytes((p/'index.jsonl').read_bytes()+(p/'index.jsonl').read_bytes().splitlines(keepends=True)[0]);self.resign(p)
        with self.assertRaises(ValidationError):self.receipt(p)
    def test_duplicate_start_rejected(self):self.execute(self.package(),code=2,duplicate_start=True)
    def test_q_gradient_reclaims_active_metadata(self):
        for q in (1,64,512):
            p=self.package(tuple(range(q)),name=f'package-{q}')
            r=self.execute(p,admission=2,workers=2,slots=4,statistics_only=True)
            self.assertEqual(r['completed'],q);self.assertEqual(r['peak_stages'],4*min(q,2));self.assertEqual(r['detail_reads'],4*q)
            self.assertEqual(r['requests'],[]);self.assertEqual(r['stages'],[])
            self.gradients.append(dict(queries=q,peak_active_stages=r['peak_stages'],peak_active_index_bytes=r['peak_index_bytes'],rss_bytes=r['rss_bytes'],scratch_bytes=r['scratch_bytes']))
        self.assertLessEqual(max(r['peak_active_index_bytes'] for r in self.gradients),4096)
        # Byte offsets/query IDs gain decimal digits as Q grows; graph count stays fixed.

if __name__=='__main__':unittest.main(verbosity=2)
