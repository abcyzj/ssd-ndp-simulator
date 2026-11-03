"""V03-6 plan completeness and fail-closed efficiency/resource gates."""
import copy
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from stream_statistics import read_statistics
from verify_ir_matrix import schedule, efficiency, gradient_report, gradient_inputs
from verify_t6 import load, points
from verify_m0 import ROOT, sha

class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.b=load(ROOT/'results/v0.3/direct-baseline-final/budget-decision.json') if (ROOT/'results/v0.3/direct-baseline-final/budget-decision.json').exists() else dict(
            query_wall_ratio_ir_direct_max=1.5,ir_rss_increment_bytes_floor=128*1024**2,ir_rss_increment_fraction=.2,
            q_gradient=[32,128,512],q_repeats=3,q_rss_increment_bytes_floor=64*1024**2,q_rss_increment_fraction=.1)
    def test_complete_plan(self):
        plan=schedule();matrix=[(r['engine'],r['name']) for r in plan if r['matrix']]
        self.assertEqual(len(set(matrix)),52)
        self.assertEqual(set(matrix),{(e,p['name']) for e in ('direct','ir') for p in points()})
        self.assertEqual(len(plan),56)
        for mode in ('host','controller'):
            self.assertEqual([r['engine'] for r in plan if r['placement']==mode and r['group']=='evaluation' and r['L']==128],['direct','ir']*3)
    def test_efficiency_fails_closed(self):
        samples=[dict(placement=m,engine=e,query_wall_seconds=10,sst_peak_rss_bytes=800*1024**2)
                 for m in ('host','controller') for _ in range(3) for e in ('direct','ir')]
        self.assertTrue(all(r['passed'] for r in efficiency(samples,self.b).values()))
        with self.assertRaises(ValueError):efficiency(samples[:-1],self.b)
        bad=copy.deepcopy(samples)
        for r in bad:
            if r['engine']=='ir':r['query_wall_seconds']=16
        self.assertFalse(efficiency(bad,self.b)['host']['passed'])
        bad=copy.deepcopy(samples);bad[1]['sst_peak_rss_bytes']+=200*1024**2
        self.assertFalse(efficiency(bad,self.b)['host']['passed'])
    def test_gradient_leak_and_growth(self):
        rows=[dict(placement=m,Q=q,sst_peak_rss_bytes=40*1024**2,
                   ir=dict(peak_ops=5,peak_values=7,active_ops=0,active_values=0))
              for m in ('host','controller') for q in (32,128,512) for _ in range(3)]
        self.assertTrue(gradient_report(rows,self.b)['host']['passed'])
        with self.assertRaises(ValueError):gradient_report(rows[:-1],self.b)
        bad=copy.deepcopy(rows);bad[6]['ir']['active_values']=1
        self.assertFalse(gradient_report(bad,self.b)['host']['passed'])
        bad=copy.deepcopy(rows);bad[6]['ir']['peak_ops']=6
        self.assertFalse(gradient_report(bad,self.b)['host']['passed'])
        bad=copy.deepcopy(rows)
        for r in bad:
            if r['Q']==512:r['sst_peak_rss_bytes']+=65*1024**2
        self.assertFalse(gradient_report(bad,self.b)['host']['passed'])
    def test_streaming_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'statistics.json';db=sqlite3.connect(':memory:')
            expected=dict(queries=3,resources=dict(service=[dict(type='service',task=i,text='字\"\\'*40000) for i in range(3)],empty=[]),unknown=dict(cost=2))
            path.write_text(json.dumps(expected,ensure_ascii=False))
            got=read_statistics(path,db,'s')
            got['resources']={k:list(v) for k,v in got['resources'].items()}
            self.assertEqual(got,expected)
            for i,text in enumerate(['{"resources":{},"queries":1,"queries":2}',
                                     '{"resources":{"x":[{"a":1,"a":2}]}}',
                                     '{"resources":{"x":[{}]}}junk',
                                     '{"resources":{"x":[{}}',
                                     '{"resources":{"x":[{},]}}']):
                path.write_text(text)
                with self.assertRaises(ValueError):read_statistics(path,db,'bad'+str(i))

    def test_fixed_graph_and_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);gradient_inputs(out)
            full=out/'q512'
            for q in (32,128):
                for name in ('vectors.f32','adjacency.u32'):
                    self.assertEqual(sha(full/name),sha(out/f'q{q}'/name))
                self.assertEqual((out/f'q{q}'/'queries.f32').read_bytes(),(full/'queries.f32').read_bytes()[:q*128])
                self.assertEqual(load(out/f'q{q}'/'truth.json')['top_k'],load(full/'truth.json')['top_k'][:q])

if __name__=='__main__':unittest.main()
