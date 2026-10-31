"""Simulation boundary rejects mismatched v2 artifacts before invoking SST."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_benchmark_artifacts import fixture
from ann_data import write_json
from benchmark_artifacts import exact, record, graph_check, binding, LOCK, VERSION, PARAMETERS
from verify_m0 import sha
from simulation_input import load_v2, truth_ids, decoded


class InputTests(unittest.TestCase):
    def test_no_silent_synthetic_fallback(self):
        from run_graph import run, ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=json.loads((ROOT/'configs/experiments/host.json').read_text())
            config['format']='ndpsim-experiment-v2'
            write_json(root/'config.json',config)
            with self.assertRaisesRegex(ValueError,'explicit prepared'):
                run(root/'config.json',root/'absent-build',root/'output')
            self.assertFalse((root/'output/data').exists())
            config['format']='unknown'
            write_json(root/'config.json',config)
            with self.assertRaisesRegex(ValueError,'unsupported experiment'):
                run(root/'config.json',root/'absent-build',root/'output')

    def test_binding_paths_entry_and_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); m,base,queries=fixture(root)
            index=root/'index'; index.mkdir(); graph=index/'graph.u32'
            graph.write_bytes(struct.pack('<8I',1,1,1,2,1,3,1,0))
            mapping=index/'nodes.u32'; mapping.write_bytes((root/'data/base.ids.u32').read_bytes())
            lock=json.loads(LOCK.read_text())
            x=dict(format='ndpsim-index-v1',validator=VERSION,binding=binding(m,('base',)),max_degree=1,entry=2,
                graph=record(graph),mapping=record(mapping),validation=graph_check(graph,4,1,2),
                builder=dict(lock_sha256=sha(LOCK),commit=lock['faiss_commit'],parameters=PARAMETERS,rng=lock['seed_policy'],threads=1))
            write_json(index/'index.json',x); exact(root/'data',root/'truth',3)
            config=dict(format='ndpsim-experiment-v2',index='index',truth='truth',search=dict(entry=2,k=2))
            shape,paths,identity=load_v2(root/'data',config,root)
            self.assertEqual(decoded(paths,shape),(base,queries))
            self.assertEqual(len(list(truth_ids(identity))[0]),3)
            for change in ('entry','k','format','binding','graph'):
                bad=copy.deepcopy(config); badx=copy.deepcopy(x)
                if change=='entry': bad['search']['entry']=0
                if change=='k': bad['search']['k']=4
                if change=='format': bad['format']='unknown'
                if change=='binding': badx['binding']['files']['base']['sha256']='bad'
                if change=='graph': graph.write_bytes(b'broken')
                write_json(index/'index.json',badx)
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_v2(root/'data',bad,root)
                graph.write_bytes(struct.pack('<8I',1,1,1,2,1,3,1,0))

if __name__=='__main__': unittest.main()
