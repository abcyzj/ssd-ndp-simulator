"""Independent P16 ledger rejection tests using a fresh real-SST trace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from verify_mqsim import cases, compare, inspect, request_text
BUILD=Path(sys.argv.pop(1)).resolve()


class MQSimLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='p16 oracle 空格 ')
        cls.folder=Path(cls.tmp.name)
        cls.traces={}
        paths=json.loads((BUILD/'sst-paths.json').read_text())
        for name,xml,requests in cases((ROOT/'configs/devices/native-small.xml').read_text()):
            folder=cls.folder/name;folder.mkdir()
            (folder/'device.xml').write_text(xml)
            (folder/'requests.txt').write_text(request_text(requests))
            declaring=folder/'experiment.json';declaring.write_text('{}')
            env=dict(os.environ,NDPSIM_DEVICE_XML='device.xml',NDPSIM_DECLARING_FILE=str(declaring),
                     NDPSIM_REQUESTS=str(folder/'requests.txt'),NDPSIM_QD=str(len(requests)),
                     NDPSIM_MISS='0',NDPSIM_SINGLE_READ='0',NDPSIM_NAND_READ_DELAY='0',NDPSIM_OBSERVE='0')
            p=subprocess.run([paths['sst'],'--add-lib-path='+str(BUILD),str(ROOT/'configs/regression/ordinary.py')],
                             env=env,cwd=cls.folder,capture_output=True,text=True,timeout=30)
            if p.returncode: raise RuntimeError(p.stdout+p.stderr)
            cls.traces[name]=(p.stdout,xml,requests)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_matrix_ledger(self):
        for name,(text,xml,requests) in self.traces.items():
            with self.subTest(name=name):
                value=compare(text,text,xml,requests,name=='small-cmt')
                self.assertEqual(value['requests'],len(requests))
                self.assertEqual(value['logical_read_bytes'],len(requests)*5120)
        self.assertNotEqual(self.traces['qd32'][0],self.traces['modified-xml'][0])

    def test_missing_duplicate_records(self):
        text,xml,requests=self.traces['qd32']
        for prefix in ('APPLIED ','CONFIG ','NAMESPACE ','SUBMIT ','IO ','INITIAL ','MAP ','CMT ','CACHE ','STATS initial ','STATS final ','DRAIN ','FINAL '):
            line=next(l for l in text.splitlines() if l.startswith(prefix))
            for altered in (text.replace(line+'\n','',1),text+line+'\n'):
                with self.subTest(prefix=prefix):
                    with self.assertRaises(ValueError): inspect(altered,xml,requests)

    def test_identically_wrong_inputs_not_self_certified(self):
        text,xml,requests=self.traces['qd32']
        # A common driver error on BOTH sides must fail the independent ledger.
        mutations=[text.replace(' 7 10 0 ', ' 8 10 0 '),
                   text.replace('pending=0','pending=1'),
                   text.replace('data_cache=off','data_cache=on'),
                   text.replace('page_sectors=8','page_sectors=4'),
                   text.replace('APPLIED Seed=321','APPLIED Seed=123'),
                   text.replace('MAP 0 ', 'MAP 1 ',1),
                   text.replace('events=0','events=1'),
                   text.replace('preload=64','preload=63')]
        for altered in mutations:
            self.assertNotEqual(altered,text)
            with self.assertRaises(ValueError): compare(altered,altered,xml,requests)
        for bad_requests in (requests[:-1],[(8,10)]+requests[1:],list(reversed(requests))):
            with self.assertRaises(ValueError): compare(text,text,xml,bad_requests)

    def test_exact_time_and_order_no_tolerance(self):
        text,xml,requests=self.traces['qd32']
        rows=text.splitlines()
        indices=[i for i,l in enumerate(rows) if l.startswith('IO ') and ' 0 ' in l]
        i=indices[-1];parts=rows[i].split();parts[-1]=str(int(parts[-1])+1)
        changed=rows.copy();changed[i]=' '.join(parts)
        with self.assertRaises(ValueError): compare(text,'\n'.join(changed),xml,requests)
        changed=rows.copy();a,b=indices[:2];changed[a],changed[b]=changed[b],changed[a]
        with self.assertRaises(ValueError): compare(text,'\n'.join(changed),xml,requests)

    def test_cmt_statistics_and_required_miss(self):
        text,xml,requests=self.traces['qd32']
        with self.assertRaises(ValueError): inspect(text,xml,requests,True)
        line=next(l for l in text.splitlines() if l.startswith('STATS final '))
        import re
        bad=re.sub(r'total_CMT_queries=(\d+)',lambda m:'total_CMT_queries='+str(int(m[1])+1),line)
        with self.assertRaises(ValueError): inspect(text.replace(line,bad),xml,requests)

    def test_failed_runner_preserves_evidence_and_refuses_reuse(self):
        out=self.folder/'failed output'
        cmd=[sys.executable,str(ROOT/'tools/verify_mqsim.py'),'--build',str(self.folder/'missing build'),
             '--output',str(out)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(p.returncode,0)
        manifest=(out/'manifest.json').read_bytes()
        report=json.loads(manifest)
        self.assertEqual(report['status'],'failed')
        self.assertFalse(report['full_acceptance_passed'])
        self.assertTrue(report['error'])
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(p.returncode,0)
        self.assertEqual((out/'manifest.json').read_bytes(),manifest)

    def test_request_parser_rejects_invalid_before_device(self):
        folder=self.folder/'invalid';folder.mkdir()
        (folder/'device.xml').write_text(self.traces['qd1'][1])
        declaring=folder/'experiment.json';declaring.write_text('{}')
        for value in ('bad\nread 7 10\n','ndpsim-mqsim-requests/1\n',
                      'ndpsim-mqsim-requests/1\nread 510 10\n',
                      'ndpsim-mqsim-requests/1\nread 7 0\n',
                      'ndpsim-mqsim-requests/1\nread 7 10 extra\n'):
            manifest=folder/'requests.txt';manifest.write_text(value)
            p=subprocess.run([str(BUILD/'ordinary_io_probe'),'--device-xml','device.xml',
                '--declaring-file',str(declaring),'--qd','1','--requests',str(manifest)],
                cwd=self.folder,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(p.returncode,0)
            self.assertNotIn('CONFIG ',p.stdout)


if __name__=='__main__': unittest.main()
