"""Independent numeric oracle and malformed graph/truth identity regression."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ann_data import write_json
from benchmark_data import convert
from benchmark_artifacts import (exact, check_truth, graph_check, quality, official,
                                 binding, check_index, record, VERSION, LOCK, PARAMETERS)
from verify_m0 import sha


def fixture(root):
    # Reordered base IDs and queries, exact tie at K boundary.
    base=[(1.,0.),(-1.,0.),(0.,0.),(2.,0.)]; queries=[(0.,0.),(1.,0.)]
    files={}
    for role,rows in [('base',base),('query',queries)]:
        path=root/role
        path.write_bytes(struct.pack('<II',len(rows),2)+b''.join(struct.pack('<2f',*r) for r in rows))
        files[role]=dict(path=role,format='fbin',count=len(rows),dimensions=2,sha256=sha(path),source='fixture',
                         selection=dict(kind='ids',ids=[1,0,3,2] if role=='base' else [1,0]))
    write_json(root/'spec.json',dict(metric='squared_l2',preprocessing='none',files=files))
    m=convert(root/'spec.json',root/'data')
    return m,[base[i] for i in [1,0,3,2]],[queries[i] for i in [1,0]]


class ArtifactTests(unittest.TestCase):
    def test_numeric_ties_blocking_and_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); m,base,queries=fixture(root)
            for block in (1,2,16):
                out=root/str(block); t=exact(root/'data',out,3,block)
                ids=list(struct.iter_unpack('<3I',(out/'ids.u32').read_bytes()))
                distances=list(struct.iter_unpack('<3d',(out/'distances.f64').read_bytes()))
                for qi,q in enumerate(queries):
                    # Full sort small oracle, separate from the bounded heap implementation.
                    expected=sorted((sum((float(x)-float(y))**2 for x,y in zip(q,v)),i) for i,v in enumerate(base))[:3]
                    self.assertEqual(ids[qi],tuple(i for d,i in expected))
                    self.assertEqual(distances[qi],tuple(d for d,i in expected))
                check_truth(root/'data',out,2) # K >= k
                with self.assertRaises(ValueError): check_truth(root/'data',out,4)
            a=quality([2], [0,2,1],2,1)
            self.assertEqual(a['recall_at_k'],0.5)
            self.assertEqual(a['one_recall_at_r'],0)
            self.assertTrue(a['insufficient_results'])
            self.assertEqual(quality([], [0,1],2,10)['recall_at_k'],0)
            with self.assertRaises(ValueError): quality([0,0],[0,1],2,1)
            with self.assertRaises(ValueError): official(root/'data',root/'official')
            with self.assertRaises(ValueError): exact(root/'data',root/'1',2)

    def test_truth_corruption_and_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); fixture(root); out=root/'truth'; t=exact(root/'data',out,3,1)
            for mutate in ('version','base','query','mapping','metric','preprocessing','nan','negative','order','duplicate','oob','short','extra','tie','tied-order'):
                bad=copy.deepcopy(t)
                ids=(out/'ids.u32').read_bytes(); ds=(out/'distances.f64').read_bytes()
                if mutate=='version': bad['format']='unknown'
                if mutate in ('base','query'): bad['binding']['files'][mutate]['sha256']='0'*64
                if mutate=='mapping': bad['binding']['files']['base']['mapping_sha256']='0'*64
                if mutate=='metric': bad['binding']['metric']='cosine'
                if mutate=='preprocessing': bad['binding']['preprocessing']='normalize'
                if mutate in ('nan','negative','order'):
                    val={'nan':float('nan'),'negative':-1.,'order':1000.}[mutate]
                    (out/'distances.f64').write_bytes(struct.pack('<d',val)+ds[8:])
                    bad['distances']=record(out/'distances.f64')
                if mutate in ('duplicate','oob','short','extra'):
                    new=(ids[:4]+ids[:4]+ids[8:] if mutate=='duplicate' else
                         struct.pack('<I',4)+ids[4:] if mutate=='oob' else
                         ids[:-1] if mutate=='short' else ids+b'x')
                    (out/'ids.u32').write_bytes(new); bad['ids']=record(out/'ids.u32')
                if mutate=='tied-order':
                    (out/'ids.u32').write_bytes(ids[:4]+ids[8:12]+ids[4:8]+ids[12:])
                    bad['ids']=record(out/'ids.u32')
                if mutate=='tie': bad['tie_boundary']='unresolved'
                write_json(out/'truth.json',bad)
                with self.subTest(mutate=mutate), self.assertRaises(ValueError): check_truth(root/'data',out)
                (out/'ids.u32').write_bytes(ids); (out/'distances.f64').write_bytes(ds)
            write_json(out/'truth.json',t)
            check_truth(root/'data',out)

    def test_graph_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); m,_,_=fixture(root); out=root/'index';out.mkdir()
            graph=out/'adjacency.u32'
            # Duplicate and self edge retained, directed full coverage.
            good=struct.pack('<16I',3,1,1,0,1,2,0,0,1,3,0,0,0,0,0,0)
            graph.write_bytes(good)
            report=graph_check(graph,4,3,0)
            self.assertEqual(report['duplicate_edges'],1); self.assertEqual(report['self_edges'],1)
            self.assertEqual(report['reachable'],4)
            for bad in (good[:-1],good+b'x',struct.pack('<I',4)+good[4:],
                        good[:4]+struct.pack('<I',4)+good[8:], bytes(len(good))):
                graph.write_bytes(bad)
                with self.assertRaises(ValueError): graph_check(graph,4,3,0)
            graph.write_bytes(good)
            for entry in (-1,4):
                with self.assertRaises(ValueError): graph_check(graph,4,3,entry)
            (out/'nodes.u32').write_bytes((root/'data/base.ids.u32').read_bytes())
            lock=json.loads(LOCK.read_text())
            x=dict(format='ndpsim-index-v1',validator=VERSION,binding=binding(m,('base',)),max_degree=3,entry=0,
                   graph=record(graph),mapping=record(out/'nodes.u32'),validation=report,
                   builder=dict(lock_sha256=sha(LOCK),commit=lock['faiss_commit'],parameters=PARAMETERS,rng=lock['seed_policy'],threads=1))
            write_json(out/'index.json',x); check_index(root/'data',out)
            for field in ('binding','validation','mapping','builder'):
                bad=copy.deepcopy(x)
                if field=='binding': bad[field]['files']['base']['mapping_sha256']='x'
                if field=='validation': bad[field]['reachable']=3
                if field=='mapping': bad[field]['sha256']='x'
                if field=='builder': bad[field]['commit']='floating'
                write_json(out/'index.json',bad)
                with self.assertRaises(ValueError): check_index(root/'data',out)

if __name__=='__main__': unittest.main()
