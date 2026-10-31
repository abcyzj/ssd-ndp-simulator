"""T6 schedule, identity and bounded random-access oracle regressions."""
import copy
import json
import struct
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import verify_t6 as t6
import compare_placements as cmp
from simulation_input import FileRows


class T6Tests(unittest.TestCase):
    def test_frozen_matrix_and_no_evaluation_selection(self):
        t6.validate_plan()
        plan=t6.points()
        self.assertEqual(len(plan),26)
        self.assertEqual(len({p['name'] for p in plan}),26)
        for group in ('tuning','evaluation'):
            scan=[p for p in plan if p['group']==group and '-repeat-' not in p['name']]
            self.assertEqual({(p['L'],p['E'],p['placement']) for p in scan},
                {(L,E,mode) for L,E in [(32,64),(64,128),(128,256),(256,512)] for mode in ('host','controller')})
        q=t6.query_groups()
        self.assertEqual(q['concurrency'],q['evaluation'][:32])
        self.assertEqual(q['detail'],q['evaluation'][:8])
        original=t6.load
        def changed(path):
            data=original(path)
            if path==t6.MATRIX:
                data=copy.deepcopy(data);data['experiment']['search']['k']=1
            return data
        with patch.object(t6,'load',changed), self.assertRaisesRegex(ValueError,'preregistered'):
            t6.validate_plan()

    def test_statistics_comparison_streams_and_only_excludes_wall_time(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'statistics.json'
            report=dict(queries=1,wall_seconds=.1,resources=dict(service=[dict(bytes=2)]),credit_bytes_outstanding=0)
            path.write_text(json.dumps(report,separators=(',',':')))
            baseline=t6.statistics(root)
            self.assertEqual(t6.statistics_head(root)['queries'],1)
            self.assertTrue(t6.load_credit_zero(root))
            report['wall_seconds']=1.23456;path.write_text(json.dumps(report,separators=(',',':')))
            self.assertEqual(t6.statistics(root),baseline)
            report['resources']['service'][0]['bytes']=3
            path.write_text(json.dumps(report,separators=(',',':')))
            self.assertNotEqual(t6.statistics(root),baseline)
            report['credit_bytes_outstanding']=1
            path.write_text(json.dumps(report,separators=(',',':')))
            self.assertFalse(t6.load_credit_zero(root))

    def test_rows_are_bounded_and_close_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'base'
            with path.open('wb') as f:
                f.truncate(1000000*128*4)
                f.seek(999999*128*4);f.write(struct.pack('<128f',*range(128)))
            tracemalloc.start()
            with FileRows(path,128) as rows:
                self.assertEqual(len(rows),1000000)
                self.assertEqual(rows[999999],tuple(range(128)))
                self.assertEqual(rows[0],(0.,)*128)
                with self.assertRaises(IndexError):rows[1000000]
                self.assertLess(tracemalloc.get_traced_memory()[1],2*1024**2)
            tracemalloc.stop()
            self.assertTrue(rows.stream.closed)
            graph=Path(tmp)/'graph';graph.write_bytes(struct.pack('<8I',2,3,1,0,1,2,0,0))
            with FileRows(graph,4,True) as rows:
                self.assertEqual(list(rows),[(3,1),(2,)])
            graph.write_bytes(b'x')
            with self.assertRaisesRegex(ValueError,'truncated'):FileRows(graph,4,True)


class CacheTests(unittest.TestCase):
    def test_reuse_checks_config_inputs_model_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'cache/point';target.mkdir(parents=True)
            prepared=root/'prepared';build=root/'build';build.mkdir()
            model=root/'src/model.cc';model.parent.mkdir();model.write_text('same simulator')
            cmp=root/'tools/compare_placements.py';cmp.parent.mkdir();cmp.write_text('new offline validator')
            (build/'libndpsim.so').write_bytes(b'element')
            c={'k':10}; t6.write_json(target/'config.json',c)
            (target/'results.json').write_text('{}')
            records=[(prepared/'tuning/dataset.json',{'data':1}),
                (prepared/'index/index.json',{'graph':1}),
                (prepared/'tuning-truth/truth.json',{'truth':1})]
            for p,value in records:p.parent.mkdir(parents=True,exist_ok=True);t6.write_json(p,value)
            (target.parent/'point.log').write_text('completed')
            receipt={'passed':True,'log_sha256':t6.sha(target.parent/'point.log')}
            t6.write_json(target.parent/'point.json',receipt)
            mq=dict(head_commit='fork',diff_from_native_base_sha256='diff',files_sha256={'x':'hash'})
            m=dict(element_sha256=t6.sha(build/'libndpsim.so'),
                input_identity=dict(dataset=records[0][1],index=records[1][1],truth=records[2][1]),
                source_sha256={'src/model.cc':t6.sha(model),'tools/compare_placements.py':'historical'},
                artifacts_sha256={name:t6.sha(target/name) for name in ('config.json','results.json')},
                sst_commit='sst',mqsim_commit='fork',mqsim_diff_from_base_sha256='diff',mqsim_source_sha256={'x':'hash'})
            t6.write_json(target/'manifest.json',m)
            with patch.object(t6,'ROOT',root),patch('subprocess.check_output',return_value='sst\n'),patch('source_snapshot.mqsim_source_snapshot',return_value=mq):
                proof=t6.verify_cached_run(target,c,prepared,'tuning',build)
                self.assertEqual(proof['offline_validator_old_sha256'],'historical')
                for change in ('config','input','model','output','element'):
                    with self.subTest(change=change):
                        config=copy.deepcopy(c)
                        if change=='config':config['k']=1
                        path={'input':records[0][0],'model':model,'output':target/'results.json','element':build/'libndpsim.so'}.get(change)
                        if path:
                            original=path.read_bytes()
                            path.write_bytes(b'{}' if change=='input' else b'tampered')
                        with self.assertRaises(ValueError):t6.verify_cached_run(target,config,prepared,'tuning',build)
                        if path:path.write_bytes(original)


class StreamingAuditTests(unittest.TestCase):
    def test_link_reuse_missing_phase_and_wire_are_rejected(self):
        def row(phase,time):
            return dict(type='link',id=1,phase=phase,time_ns=time,wire=32,payload=4,
                        message_type=0,destination=0,service_ns=8)
        good=[row('enqueue',0),row('start',0),row('arrive',8)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config.json').write_text('{}')
            (root/'manifest.json').write_text(json.dumps(dict(data=tmp)))
            for kind in ('duplicate_phase','missing_start','wire','closed_id'):
                events=copy.deepcopy(good)
                if kind=='duplicate_phase':events.insert(1,copy.deepcopy(events[0]))
                if kind=='missing_start':del events[1]
                if kind=='wire':events[1]['wire']=99
                if kind=='closed_id':events.append(copy.deepcopy(events[-1]))
                def generated(_):
                    yield from events
                    raise RuntimeError('audit did not reject invalid link sequence')
                with self.subTest(kind=kind),patch.object(cmp,'read_dataset',return_value=(dict(queries=1),[],[])),patch.object(cmp,'event_rows',generated),self.assertRaises(AssertionError):
                    cmp.inspect(root)

    def test_event_reader_does_not_load_the_whole_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with (root/'events.jsonl').open('w') as f:
                for i in range(30000):f.write(json.dumps(dict(type='test',id=i,padding='x'*128))+'\n')
            tracemalloc.start()
            self.assertEqual(sum(1 for _ in cmp.event_rows(root)),30000)
            self.assertLess(tracemalloc.get_traced_memory()[1],2*1024**2)
            tracemalloc.stop()


if __name__=='__main__':unittest.main()
