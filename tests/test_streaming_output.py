"""Streaming reduction equals an independent small fixture and bounds Python heap."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from experiment_output import export, RecordStream


class StreamingOutput(unittest.TestCase):
    def fixture(self,out,n):
        path=out/'records.jsonl'
        with path.open('w') as f:
            for i in range(n):
                f.write(json.dumps(dict(type='service',task=i,query=1,submitted_ns=0,ready_ns=2,start_ns=3,end_ns=7))+'\n')
            f.write(json.dumps(dict(type='summary',origin_ns=0,end_ns=10))+'\n')
        results=[dict(query=1,latency_ns=10,admission_wait_ns=0,offered_latency_ns=10,top_k=[dict(id=0,distance=1.)])]
        tracemalloc.start()
        export(out,results,RecordStream(path),1.)
        peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
        # Read only after measuring streaming export, independently verify all rows.
        stats=json.loads((out/'statistics.json').read_text())
        self.assertEqual(len(stats['resources']['service']),n)
        self.assertTrue(all(r['service_ns']==4 and r['dependency_wait_ns']==2 and r['queue_wait_ns']==1 for r in stats['resources']['service']))
        self.assertEqual(stats['latency_ns']['p99'],10)
        self.assertEqual(len(list(csv.DictReader((out/'service.csv').open()))),n)
        return peak

    def test_bounded_history(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);small=out/'small';large=out/'large';small.mkdir();large.mkdir()
            a=self.fixture(small,1000);b=self.fixture(large,30000)
            self.assertLess(b-a,2*1024**2)


if __name__=='__main__':unittest.main()
