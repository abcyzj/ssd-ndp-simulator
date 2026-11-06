"""P02 public API, adversarial packages and independent C++/Python byte oracles."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tracemalloc
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from ndpsim.static_ir import (Limits, OperatorRegistry, PackageWriter, ValidationError,
    canonical, check_schema, decode, file_hash, seal_capture, validate_package)

BINARY=ROOT/'build/p02-sdk/capture_fixture'


def fixture(registry,queries=(7,)):
    h=hashlib.sha256(b'fixture').hexdigest()
    p=dict(kind='synthetic-fixture',capture_build=dict(source_sha256=h,binary_sha256=h,compiler='fixture',flags=[]),input_content_sha256=h,semantic_version='fixture/1',extension_sha256=registry.sha256,split='fixture',query_ids=list(queries),workload='fixture',numeric_version='metadata/1',work_version='counts/1',semantic_config_sha256=h)
    obj=dict(id='block',version=0,type='u32-le',size_bytes=64,shape=[16],content_sha256=h)
    ops=[]
    for q in queries:
        for i,name in enumerate(('root','left','right','join')):
            deps=[]
            if i in (1,2):deps=[dict(kind='DATA' if i==1 else 'ADDRESS',source=dict(key=dict(query=q,op='root',stage='s0'),port='out'),input='in0')]
            if i==3:deps=[dict(kind=kind,source=dict(key=dict(query=q,op=src,stage='s0'),port='out'),input=dest) for src,dest,kind in [('left','in0','CONTROL'),('right','in1','ORDER')]]
            work=dict(elements=dict(value=16,unit='count'))
            stage=dict(id='s0',kind='reduce',topological_rank=i,dependencies=deps,work=work,features={},context='fixture/1',chunk=None,segment=0,accesses=[dict(object='block',version=0,offset_bytes=0,length_bytes=64)],outputs=[dict(port='out',type='fixture.u64',logical_bytes=8,version=0,digest=h)],state_versions=[],decomposition_rule='none/1')
            ops.append(dict(query=q,id=name,operator='fixture.reduce',operator_version='1',semantic_work=work,stages=[stage]))
    terms=[dict(query=q,op='join',stage='s0') for q in queries]
    return p,obj,ops,terms


def write_package(path,registry,ops=None,terms=None,limits=Limits(),queries=(7,)):
    p,obj,base,t=fixture(registry,queries)
    with PackageWriter(path,p,registry,allow_fixture=True,limits=limits) as w:
        w.declare_object(obj)
        for op in base if ops is None else ops:w.record(op)
        report=w.seal(t if terms is None else terms)
    return report


def rewrite_identity(package):
    """Independent hashing oracle, used to test relationships beyond file checksums."""
    manifest=json.loads((package/'manifest.json').read_text())
    for row in manifest['files']:
        b=(package/row['path']).read_bytes();row.update(bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
    (package/'manifest.json').write_bytes(canonical(manifest))
    pkg=hashlib.sha256(b'ndpsim-static-ir/2\0'+b''.join(hashlib.sha256((package/n).read_bytes()).digest() for n in ('manifest.json','index.jsonl','operations.jsonl'))).hexdigest()
    tail=json.loads((package/'tail.json').read_text());tail.update(package_sha256=pkg,counts=manifest['counts'],terminals=manifest['terminals'])
    (package/'tail.json').write_bytes(canonical(tail));return pkg


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.registry=OperatorRegistry.load(ROOT/'examples/p02-capture/registry.json')

    def test_checked_in_synthetic_package(self):
        report=validate_package(ROOT/'examples/p02-capture/package',self.registry,allow_fixture=True)
        self.assertEqual(report['source'],'synthetic-fixture')
        self.assertEqual(report['counts'],dict(queries=1,operations=4,stages=4,dependencies=4))
        self.assertFalse(report['independent_semantic_passed'])

    def test_roundtrip_and_independent_bytes(self):
        out=self.root/'pkg';r=write_package(out,self.registry)
        m=json.loads((out/'manifest.json').read_text());idx=[json.loads(s) for s in (out/'index.jsonl').read_text().splitlines()]
        raw=(out/'operations.jsonl').read_bytes()
        for item in idx:
            op=json.loads(raw[item['offset_bytes']:item['offset_bytes']+item['length_bytes']])
            st=next(s for s in op['stages'] if s['id']==item['key']['stage'])
            self.assertEqual(item['stage_sha256'],hashlib.sha256((json.dumps(st,sort_keys=True,ensure_ascii=True,separators=(',',':'))+'\n').encode()).hexdigest())
        expected=hashlib.sha256(b'ndpsim-static-ir/2\0'+b''.join(hashlib.sha256((out/n).read_bytes()).digest() for n in ('manifest.json','index.jsonl','operations.jsonl'))).hexdigest()
        self.assertEqual(expected,r['package_sha256'])
        checked=validate_package(out,self.registry,allow_fixture=True,expected_package_sha256=expected,expected_provenance=m['provenance'])
        self.assertTrue(checked['structural_passed']);self.assertTrue(checked['external_package_identity_bound'])
        self.assertFalse(checked['independent_semantic_passed']);self.assertEqual(checked['semantic_validation'],'not-performed')
        with self.assertRaises(ValidationError):validate_package(out,self.registry)
        with self.assertRaises(ValidationError):validate_package(out,self.registry,allow_fixture=True,expected_package_sha256='f'*64)
        p=copy.deepcopy(m['provenance']);p['input_content_sha256']='f'*64
        with self.assertRaises(ValidationError):validate_package(out,self.registry,allow_fixture=True,expected_provenance=p)

    def test_cpp_capture_unicode_hash_and_isolated_seal(self):
        self.assertTrue(BINARY.is_file(),'build SDK with NDPSIM_CAPTURE_TESTING=ON')
        sample_raw=subprocess.check_output([BINARY,'--sample']);sample=decode(sample_raw)
        check_schema(sample,'sample');self.assertEqual(sample_raw,canonical(sample))
        self.assertEqual(sample['corrected_ns'],-2);self.assertTrue(sample['low_signal'])
        hashes=subprocess.check_output([BINARY,'--hash'],text=True).splitlines()
        for line in hashes:
            n,h=line.split();self.assertEqual(h,hashlib.sha256(b'a'*int(n)).hexdigest())
        for mode in ('normal','unicode'):
            src=self.root/mode;args=[str(BINARY),str(src),self.registry.sha256]
            if mode=='unicode':args.append('unicode')
            subprocess.run(args,check=True,capture_output=True)
            for line in (src/'operations.jsonl').read_bytes().splitlines(keepends=True):self.assertEqual(line,canonical(decode(line)))
            out=self.root/(mode+'-pkg');r=seal_capture(src,out,self.registry,allow_fixture=True)
            self.assertEqual(r['counts'],dict(queries=1,operations=4,stages=4,dependencies=4))
            idx=[decode(s) for s in (out/'index.jsonl').read_bytes().splitlines()]
            self.assertEqual([x['topological_rank'] for x in idx],[0,1,2,3])
            self.assertEqual(idx[0]['key']['op'],'root' if mode=='normal' else '根😀\x7f')
        src=self.root/'failed-cpp'
        r=subprocess.run([BINARY,str(src),self.registry.sha256,'budget-failure'],capture_output=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse((src/'capture.json').exists())
        with self.assertRaises(ValidationError):seal_capture(src,self.root/'invalid',self.registry,allow_fixture=True)

    def test_relational_negative_matrix(self):
        for change in ('cycle','missing-source','missing-required','duplicate-op','duplicate-stage','duplicate-rank','duplicate-edge','unknown-op','unknown-stage','unknown-port','wrong-type','work-unit','work-missing','extra-feature','context','decomposition','cross-query','access-range','access-overflow','object-version','output-size','terminal-missing','terminal-extra','rank-gap','segment-gap'):
            with self.subTest(change=change):
                _,_,ops,terms=fixture(self.registry);reg=self.registry
                if change=='cycle':ops[0]['stages'][0]['dependencies']=copy.deepcopy(ops[3]['stages'][0]['dependencies'])
                if change=='missing-source':ops[1]['stages'][0]['dependencies'][0]['source']['key']['op']='absent'
                if change=='missing-required':
                    desc=copy.deepcopy(reg.descriptor);desc['operators'][0]['stages'][0]['inputs'][0]['required']=True;reg=OperatorRegistry(desc)
                if change=='duplicate-op':ops.append(copy.deepcopy(ops[0]))
                if change=='duplicate-stage':ops[0]['stages']*=2
                if change=='duplicate-rank':ops[1]['stages'][0]['topological_rank']=0
                if change=='duplicate-edge':ops[1]['stages'][0]['dependencies']*=2
                if change=='unknown-op':ops[0]['operator']='unknown'
                if change=='unknown-stage':ops[0]['stages'][0]['kind']='unknown'
                if change=='unknown-port':ops[1]['stages'][0]['dependencies'][0]['source']['port']='unknown'
                if change=='wrong-type':ops[0]['stages'][0]['outputs'][0]['type']='different'
                if change=='work-unit':ops[0]['stages'][0]['work']['elements']['unit']='B'
                if change=='work-missing':ops[0]['stages'][0]['work']={}
                if change=='extra-feature':ops[0]['stages'][0]['features']={'unexpected':dict(value=1,unit='count')}
                if change=='context':ops[0]['stages'][0]['context']='unknown'
                if change=='decomposition':ops[0]['stages'][0]['decomposition_rule']='unknown'
                if change=='cross-query':ops[1]['stages'][0]['dependencies'][0]['source']['key']['query']=8
                if change=='access-range':ops[0]['stages'][0]['accesses'][0]['length_bytes']=65
                if change=='access-overflow':ops[0]['stages'][0]['accesses'][0].update(offset_bytes=2**64-1,length_bytes=1)
                if change=='object-version':ops[0]['stages'][0]['accesses'][0]['version']=1
                if change=='output-size':ops[0]['stages'][0]['outputs'][0]['logical_bytes']=9
                if change=='terminal-missing':terms=[]
                if change=='terminal-extra':terms.append(dict(query=7,op='left',stage='s0'))
                if change=='rank-gap':ops[3]['stages'][0]['topological_rank']=4
                if change=='segment-gap':ops[0]['stages'][0]['segment']=1
                out=self.root/change
                with self.assertRaises(ValidationError):write_package(out,reg,ops,terms)
                self.assertFalse(out.exists());self.assertTrue(Path(str(out)+'.failed').is_dir())

    def test_file_tail_and_index_negative_matrix(self):
        base=self.root/'base';write_package(base,self.registry)
        for change in ('truncate-ops','truncate-index','tamper','extra-file','missing-tail','bad-tail','counts','index-offset','index-stage-hash','index-edge','index-duplicate','file-path','symlink'):
            with self.subTest(change=change):
                out=self.root/change;shutil.copytree(base,out)
                if change in ('truncate-ops','truncate-index'):
                    p=out/('operations.jsonl' if change=='truncate-ops' else 'index.jsonl');p.write_bytes(p.read_bytes()[:-1]);rewrite_identity(out)
                if change=='tamper':(out/'operations.jsonl').write_bytes((out/'operations.jsonl').read_bytes()+b'\n')
                if change=='extra-file':(out/'payload.bin').write_bytes(b'payload')
                if change=='missing-tail':(out/'tail.json').unlink()
                if change=='bad-tail':p=out/'tail.json';v=decode(p.read_bytes());v['complete']=False;p.write_bytes(canonical(v))
                if change=='counts':p=out/'manifest.json';v=decode(p.read_bytes());v['counts']['operations']+=1;p.write_bytes(canonical(v));rewrite_identity(out)
                if change.startswith('index-'):
                    p=out/'index.jsonl';rows=[decode(s) for s in p.read_bytes().splitlines()]
                    if change=='index-offset':rows[0]['offset_bytes']+=1
                    if change=='index-stage-hash':rows[0]['stage_sha256']='f'*64
                    if change=='index-edge':rows[1]['dependencies']=[]
                    if change=='index-duplicate':rows.append(rows[0])
                    p.write_bytes(b''.join(map(canonical,rows)));rewrite_identity(out)
                if change=='file-path':p=out/'manifest.json';v=decode(p.read_bytes());v['files'][0]['path']='../outside';p.write_bytes(canonical(v))
                if change=='symlink':p=out/'operations.jsonl';b=p.read_bytes();p.unlink();(self.root/'outside').write_bytes(b);p.symlink_to(self.root/'outside')
                with self.assertRaises(ValidationError):validate_package(out,self.registry,allow_fixture=True)

    def test_duplicate_nonfinite_float_and_payload_rejection(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b'{"a":1e999}',b'{"a":"\\ud800"}',b'\xff'):
            with self.assertRaises(ValidationError):decode(raw)
        for change,value in [('query',1.0),('query',True),('query',-1),('query',2**64),('payload_path','base.bin'),('capture_time_ns',42)]:
            _,_,ops,terms=fixture(self.registry);ops[0][change]=value
            with self.assertRaises(ValidationError):write_package(self.root/str(len(list(self.root.iterdir()))),self.registry,ops,terms)

    def test_budget_and_poisoned_writer(self):
        for i,limits in enumerate((Limits(line_bytes=100),Limits(query_index_bytes=50),Limits(active_stages=2),Limits(total_bytes=100),Limits(scratch_bytes=1))):
            out=self.root/str(i)
            with self.assertRaises(ValidationError):write_package(out,self.registry,limits=limits)
            self.assertFalse(out.exists())
        p,obj,ops,terms=fixture(self.registry);w=PackageWriter(self.root/'poison',p,self.registry,allow_fixture=True);w.declare_object(obj)
        bad=copy.deepcopy(ops[0]);bad['stages'][0]['outputs'][0]['logical_bytes']=9
        with self.assertRaises(ValidationError):w.record(bad)
        with self.assertRaises(ValidationError):w.record(ops[0])
        with self.assertRaises(ValidationError):w.seal(terms)
        self.assertFalse((self.root/'poison').exists())
        out=self.root/'exists';out.mkdir()
        with self.assertRaises(ValidationError):PackageWriter(out,p,self.registry,allow_fixture=True)
        self.assertFalse(Path(str(out)+'.lock').exists())

    def test_registry_trust_and_native_provenance(self):
        for change in ('operator','unit','rule','duplicate','unknown-rule'):
            desc=copy.deepcopy(self.registry.descriptor)
            if change=='operator':desc['operators'].append(copy.deepcopy(desc['operators'][0]))
            if change=='unit':desc['units']=['B']
            if change=='rule':desc['rules'][0]['kind']='execute-python'
            if change=='duplicate':desc['operators'][0]['stages'][0]['outputs']*=2
            if change=='unknown-rule':desc['operators'][0]['stages'][0]['output_size_rule']='unknown'
            with self.assertRaises(ValidationError):OperatorRegistry(desc)
        p,_,_,_=fixture(self.registry);p['extension_sha256']='f'*64
        with self.assertRaises(ValidationError):PackageWriter(self.root/'bad-reg',p,self.registry,allow_fixture=True)
        p,_,_,_=fixture(self.registry);p.update(kind='native-capture',split='tuning32');p['capture_build']['binary_sha256']='0'*64
        with self.assertRaises(ValidationError):PackageWriter(self.root/'bad-native',p,self.registry)

    def test_multiple_stages_states_and_empty_access(self):
        _,_,ops,terms=fixture(self.registry)
        # Merge first two operations into one semantic operation with distinct stage IDs.
        ops[0]['stages'][0]['id']='first';ops[1]['stages'][0]['id']='second';ops[1]['stages'][0]['segment']=1
        ops[1]['stages'][0]['dependencies'][0]['source']['key'].update(op='root',stage='first')
        ops[0]['stages'][0]['outputs'][0]['version']=1;ops[1]['stages'][0]['outputs'][0]['version']=2
        ops[0]['stages'][0]['state_versions']=[dict(name='state',before=0,after=1,input=None,output='out')]
        ops[1]['stages'][0]['state_versions']=[dict(name='state',before=1,after=2,input='in0',output='out')]
        ops[0]['stages'].append(ops[1]['stages'][0]);ops.pop(1)
        for op in ops[1:]:
            for st in op['stages']:
                for dep in st['dependencies']:
                    k=dep['source']['key']
                    if k['op']=='left':k.update(op='root',stage='second')
                    elif k['op']=='root':k['stage']='first'
        ops[0]['stages'][0]['accesses'][0].update(offset_bytes=64,length_bytes=0)
        write_package(self.root/'ok',self.registry,ops,terms)
        ops[0]['stages'][1]['state_versions'][0]['before']=0
        with self.assertRaises(ValidationError):write_package(self.root/'bad-state',self.registry,ops,terms)

    def test_many_queries_and_uint64_identity(self):
        queries=list(range(40))+[2**64-1]
        out=self.root/'many';r=write_package(out,self.registry,queries=queries)
        tracemalloc.start()
        checked=validate_package(out,self.registry,allow_fixture=True)
        _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        self.assertEqual(checked['counts']['stages'],4*len(queries))
        self.assertEqual(checked['metrics']['max_query_stages'],4)
        self.assertLess(peak,32*1024**2)
        self.assertEqual(checked['package_sha256'],r['package_sha256'])

    def test_variadic_ports_and_custom_rule(self):
        desc=copy.deepcopy(self.registry.descriptor);desc['operators'][0]['stages'][0]['inputs'][0]['cardinality']='many'
        desc['rules']=[dict(id='fixture.fixed-eight/1',kind='registered-metadata/1',implementation_sha256='f'*64)]
        registry=OperatorRegistry(desc,{'fixture.fixed-eight/1':lambda stage:{'out':8}})
        _,_,ops,terms=fixture(registry);ops[3]['stages'][0]['dependencies'][1]['input']='in0'
        write_package(self.root/'many-input',registry,ops,terms)
        ops[3]['stages'][0]['dependencies'].append(copy.deepcopy(ops[3]['stages'][0]['dependencies'][0]))
        with self.assertRaises(ValidationError):write_package(self.root/'duplicate-input',registry,ops,terms)

    def test_capture_checksum_and_missing_close(self):
        src=self.root/'src';subprocess.run([BINARY,str(src),self.registry.sha256],check=True,capture_output=True)
        p=src/'operations.jsonl';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValidationError):seal_capture(src,self.root/'bad',self.registry,allow_fixture=True)
        self.assertFalse((self.root/'bad').exists())
        (src/'capture.json').unlink()
        with self.assertRaises(ValidationError):seal_capture(src,self.root/'bad2',self.registry,allow_fixture=True)

    def test_cli_new_reports_and_external_cwd(self):
        src=self.root/'capture with spaces';subprocess.run([BINARY,str(src),self.registry.sha256],check=True,capture_output=True)
        env=dict(os.environ,PYTHONPATH=str(ROOT/'python'))
        out=self.root/'package with spaces';report=self.root/'seal-report.json'
        command=[sys.executable,'-m','ndpsim.capture_cli','seal','--registry',str(ROOT/'examples/p02-capture/registry.json'),'--input',str(src),'--output',str(out),'--report',str(report),'--allow-fixture']
        result=subprocess.run(command,env=env,cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        identity=json.loads(report.read_text())['package_sha256']
        check=[sys.executable,'-m','ndpsim.capture_cli','check','--registry',str(ROOT/'examples/p02-capture/registry.json'),'--input',str(out),'--report',str(self.root/'check-report.json'),'--allow-fixture','--expected-package-sha256',identity]
        self.assertEqual(subprocess.run(check,env=env,cwd=self.root,capture_output=True).returncode,0)
        self.assertNotEqual(subprocess.run(check,env=env,cwd=self.root,capture_output=True).returncode,0)
        check[check.index('--report')+1]=str(out/'inside.json')
        self.assertNotEqual(subprocess.run(check,env=env,cwd=self.root,capture_output=True).returncode,0)
        self.assertFalse((out/'inside.json').exists())
        self.assertTrue(validate_package(out,self.registry,allow_fixture=True)['structural_passed'])

    def test_failed_writer_cannot_damage_later_writer(self):
        p,obj,ops,terms=fixture(self.registry);out=self.root/'same'
        first=PackageWriter(out,p,self.registry,allow_fixture=True);first.declare_object(obj)
        bad=copy.deepcopy(ops[0]);bad['operator']='unknown'
        with self.assertRaises(ValidationError):first.record(bad)
        second=PackageWriter(out,p,self.registry,allow_fixture=True);second.declare_object(obj)
        with self.assertRaises(ValidationError):first.record(ops[0])
        self.assertTrue(Path(str(out)+'.lock').exists())
        for op in ops:second.record(op)
        second.seal(terms)
        self.assertTrue(validate_package(out,self.registry,allow_fixture=True)['structural_passed'])

    def test_missing_entire_queries_and_symbolic_metadata(self):
        out=self.root/'queries';p,obj,ops,terms=fixture(self.registry,(7,8))
        with PackageWriter(out,p,self.registry,allow_fixture=True) as w:
            w.declare_object(obj)
            for op in ops[:4]:w.record(op)
            with self.assertRaises(ValidationError):w.seal(terms)
        self.assertFalse(out.exists())
        src=self.root/'symlink-src';subprocess.run([BINARY,str(src),self.registry.sha256],check=True,capture_output=True)
        raw=(src/'operations.jsonl').read_bytes();(self.root/'external').write_bytes(raw)
        (src/'operations.jsonl').unlink();(src/'operations.jsonl').symlink_to(self.root/'external')
        with self.assertRaises(ValidationError):seal_capture(src,self.root/'symlink-out',self.registry,allow_fixture=True)

    def test_schema_bundle_matches_frozen_sources(self):
        for name in ('ndpsim-contract-v2.schema.json','ndpsim-static-ir-v2.schema.json'):
            self.assertEqual((ROOT/'schemas'/name).read_bytes(),(ROOT/'python/ndpsim/schemas'/name).read_bytes())


if __name__=='__main__':
    if len(sys.argv)>1 and not sys.argv[1].startswith('-'):BINARY=Path(sys.argv.pop(1)).resolve()
    unittest.main(verbosity=2)
