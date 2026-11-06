"""Exercise the generic GraphHost against independently hand-calculated DAG times."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from ndpsim.static_ir import OperatorRegistry,PackageWriter,canonical,decode,file_hash,validate_package
from test_v045_capture import fixture
BUILD=Path(sys.argv.pop(1)).resolve()
class SstDag(unittest.TestCase):
    def test_generic_host(self):
        evidence=os.environ.get('NDPSIM_DAG_SST_EVIDENCE')
        if evidence:root=Path(evidence);root.mkdir(parents=True,exist_ok=False)
        else:self.temp=tempfile.TemporaryDirectory(prefix='p08-sst-');root=Path(self.temp.name)
        registry=OperatorRegistry(decode((ROOT/'examples/p02-capture/registry.json').read_bytes()))
        provenance,obj,ops,terminals=fixture(registry,(0,1,2))
        package=root/'package'
        with PackageWriter(package,provenance,registry,allow_fixture=True) as writer:
            writer.declare_object(obj)
            for op in reversed(ops):writer.record(op)
            writer.seal(terminals)
        report=validate_package(package,registry,allow_fixture=True);rp=root/'report.json';rp.write_bytes(canonical(report))
        sst=json.loads((BUILD/'sst-paths.json').read_text())['sst']
        for variant,workers,zero in [('base',1,False),('parallel',2,False),('zero',2,True)]:
            with self.subTest(variant=variant):
                out=root/(variant+'.json');c=dict(package=str(package),report=str(rp),report_sha256=file_hash(rp),package_sha256=report['package_sha256'],registry_sha256=registry.sha256,scratch=str(root/(variant+'.sqlite')),durations=dict(root=2,left=5,right=11,join=3) if not zero else dict(root=0,left=0,right=0,join=0),returns=dict(right=7) if not zero else {},slots=workers*2,workers=workers,admission=workers,output=str(out))
                cp=root/(variant+'-config.json');cp.write_bytes(canonical(c))
                env={**os.environ,'NDPSIM_DAG_CONFIG':str(cp)}
                cmd=[sst,'--add-lib-path='+str(BUILD),str(ROOT/'configs/regression/static_dag.py')]
                run=subprocess.run(cmd,env=env,capture_output=True,text=True)
                (root/(variant+'.stdout')).write_text(run.stdout);(root/(variant+'.stderr')).write_text(run.stderr)
                self.assertEqual(run.returncode,0,run.stderr+run.stdout);self.assertIn('P08 SST PASS',run.stdout)
                r=json.loads(out.read_text());self.assertEqual(r['drained'],0 if zero else 23*((3+workers-1)//workers))
                for field in ('active_stages','outstanding','runtime_tasks','resource_pending'):self.assertEqual(r[field],0)
                self.assertEqual(r['peak_workers'],workers)
        if not evidence:self.temp.cleanup()
if __name__=='__main__':unittest.main(verbosity=2)
