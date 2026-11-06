import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'python'),str(Path(__file__).parent)]
from tutorial import prepare,simulate,semantic,registry,save,decode,file_hash
BUILD=Path(sys.argv.pop(1)).resolve()
class Extension(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='P17 扩展 ');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def prepare(self,name='run',values=None):
        return prepare(BUILD/'examples/extension/block_sum_native',ROOT/'configs/devices/native-small.xml',self.root/name,values)
    def test_dual_mapping_and_independent_oracle(self):
        out=self.prepare();self.assertFalse((out/'input.u32').exists())
        r=simulate(BUILD,out);self.assertTrue(r['teaching_passed'])
        self.assertEqual(decode((out/'semantic.json').read_bytes())['expected'],dict(left=10,right=26,join=36))
        with self.assertRaises(FileExistsError):self.prepare()
    def test_nondefault_input_and_determinism(self):
        a=self.prepare('a',[0,2**32-1,7,11,13,17,19,23]);b=self.prepare('b',[0,2**32-1,7,11,13,17,19,23])
        ra=simulate(BUILD,a);rb=simulate(BUILD,b)
        self.assertEqual(ra,rb)
        self.assertEqual(decode((a/'semantic.json').read_bytes())['expected']['join'],2**32-1+90)
    def test_independent_oracle_rejects_changed_input_and_work(self):
        out=self.prepare();import struct
        raw=struct.pack('<8I',*range(1,9))
        with self.assertRaises(ValueError):semantic(out/'package',raw[:-1]+b'\xff',registry())
        # Re-seal valid structural metadata with deliberately incorrect Work.
        from ndpsim.static_ir import PackageWriter
        manifest=decode((out/'package/manifest.json').read_bytes())
        ops=[decode(x) for x in (out/'package/operations.jsonl').read_bytes().splitlines()]
        ops[0]['stages'][0]['work']['elements']['value']=3
        p=out/'wrong-work'
        with PackageWriter(p,manifest['provenance'],registry()) as w:
            for o in manifest['objects']:w.declare_object(o)
            for o in ops:w.record(o)
            w.seal(manifest['terminals'])
        with self.assertRaisesRegex(ValueError,'Work'):semantic(p,raw,registry())
    def test_reject_model_placement_and_receipt_before_success(self):
        for field,value in [('placement','unknown'),('profile_sha256','0'*64),('semantic_sha256','0'*64)]:
            out=self.prepare(field);p=out/'host.json';c=decode(p.read_bytes());c[field]=value;save(p,c)
            with self.assertRaises(ValueError):simulate(BUILD,out)
            self.assertFalse((out/'comparison.json').exists())
    def test_cost_change_changes_service_and_time(self):
        before=simulate(BUILD,self.prepare('baseline'))
        out=self.prepare();profile=decode((out/'profile.json').read_bytes())
        for r in profile['rules']:r['parameters']['intercept_ns']=100003
        save(out/'profile.json',profile)
        for placement in ('host','controller'):
            p=out/(placement+'.json');c=decode(p.read_bytes());c['profile_sha256']=file_hash(out/'profile.json');save(p,c)
        # Compare intentionally rejects the changed teaching formula; actual SST runs succeed.
        with self.assertRaisesRegex(ValueError,'linear cost'):simulate(BUILD,out)
        for placement in ('host','controller'):
            rows=[decode(x) for x in (out/(placement+'.jsonl')).read_bytes().splitlines()]
            self.assertTrue(rows[-1]['success']);self.assertGreater(rows[-1]['elapsed_ns'],before[placement]['elapsed_ns'])
            self.assertEqual(sorted(r['duration_ns'] for r in rows if r['type']=='demand'),[100007,100011,100011])
            if placement=='controller':self.assertEqual([r['duration_ns'] for r in rows if r['type']=='controller_compute'],[100011,100011])
if __name__=='__main__':unittest.main(verbosity=2)
