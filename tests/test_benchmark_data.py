import copy
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from benchmark_data import convert, check
from ann_data import generate, read_dataset
from verify_m0 import sha


class ImportTests(unittest.TestCase):
    def test_formats_and_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for fmt in ('fvecs','fbin','ivecs','ibin'):
                integer = fmt[0]=='i'
                payload = struct.pack('<4i' if integer else '<4f', 0,1,1,0)
                raw = (struct.pack('<II',2,2)+payload if fmt.endswith('bin') else
                       struct.pack('<i',2)+payload[:8]+struct.pack('<i',2)+payload[8:])
                path = root/fmt
                path.write_bytes(raw)
                base = root/'base'
                base.write_bytes(struct.pack('<II4f',2,2,0,1,1,0))
                def item(p,f):
                    return dict(path=p.name, format=f, count=2, dimensions=2, sha256=sha(p), source={'url':'fixture','usage_conditions':'test'})
                spec = dict(metric='squared_l2',preprocessing='none',files=dict(base=item(base,'fbin'),query=item(base,'fbin')))
                if integer:
                    spec['files']['groundtruth']=item(path,fmt)
                else:
                    spec['files']['query']=item(path,fmt)
                spec['files']['query']['selection']={'kind':'ids','ids':[1,0]}
                if integer:
                    spec['files']['groundtruth']['selection']={'kind':'ids','ids':[1,0]}
                sp=root/'spec.json'
                sp.write_text(json.dumps(spec))
                out=root/(fmt+'-out')
                convert(sp,out)
                m=check(out)
                self.assertEqual((out/'query.f32').read_bytes(),struct.pack('<4f',1,0,0,1))
                self.assertEqual(m['files']['query']['count'],2)
                for mutation in ('metric','mapping','hash','version'):
                    bad=copy.deepcopy(m)
                    if mutation=='metric': bad['metric']='cosine'
                    if mutation=='mapping': bad['files']['query']['selection']['ids']=[0,1]
                    if mutation=='hash': bad['files']['base']['sha256']='0'*64
                    if mutation=='version': bad['format']='unknown'
                    (out/'dataset.json').write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): check(out)
                (out/'dataset.json').write_text(json.dumps(m))
                for suffix, broken in [('short',raw[:-1]),('extra',raw+b'x'),('endian',b'\xff\xff\xff\xff'+raw[4:]),('value',raw[:-4]+struct.pack('<i' if integer else '<f',-1 if integer else float('nan')))]:
                    path.write_bytes(broken)
                    role='groundtruth' if integer else 'query'
                    spec['files'][role]['sha256']=sha(path)
                    sp.write_text(json.dumps(spec))
                    target=root/(fmt+suffix)
                    with self.assertRaises(ValueError): convert(sp,target)
                    self.assertFalse(target.exists())

    def test_selection_and_identity(self):
        from benchmark_data import selection
        for rule in ({'kind':'ids','ids':[-1]}, {'kind':'ids','ids':[2]},
                     {'kind':'ids','ids':[0,0]}, {'kind':'random','seed':1,'count':3}):
            with self.assertRaises(ValueError): selection({'selection':rule},2)
        self.assertEqual(selection({'selection':{'kind':'random','seed':42,'count':2}},2), [0,1])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            raw=root/'raw'
            raw.write_bytes(struct.pack('<II4f',2,2,0,1,1,0))
            gt=root/'gt'
            gt.write_bytes(struct.pack('<II2i',2,1,0,1))
            item=dict(path='raw',format='fbin',count=2,dimensions=2,sha256=sha(raw),source='fixture')
            spec=dict(metric='squared_l2',preprocessing='none',files=dict(base=copy.deepcopy(item),query=copy.deepcopy(item),groundtruth=dict(path='gt',format='ibin',count=2,dimensions=1,sha256=sha(gt),source='fixture')))
            sp=root/'spec'
            for mode in ('base_subset','query_order','wrong_hash','dimensions'):
                bad=copy.deepcopy(spec)
                if mode=='base_subset': bad['files']['base']['selection']={'kind':'ids','ids':[0]}
                if mode=='query_order': bad['files']['query']['selection']={'kind':'ids','ids':[1,0]}
                if mode=='wrong_hash': bad['files']['base']['sha256']='0'*64
                if mode=='dimensions': bad['files']['query']['dimensions']=3
                sp.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): convert(sp,root/mode)
                self.assertFalse((root/mode).exists())

    def test_v1(self):
        with tempfile.TemporaryDirectory() as temp:
            generate(temp, fixture='branch')
            self.assertEqual(read_dataset(temp)[0]['format'],'ndpsim-ann-v1')


if __name__=='__main__': unittest.main()
