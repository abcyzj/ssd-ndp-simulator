"""Actual memory loader/lowering without SST, suitable for ASan."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
# The shared fixture consumes the build argument before unittest sees it.
import test_v045_memory as memory
class Plan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('NDPSIM_MEMORY_EVIDENCE'):
            os.environ['NDPSIM_MEMORY_EVIDENCE'] += '-plan'
        memory.Memory.setUpClass()
    @classmethod
    def tearDownClass(cls):memory.Memory.tearDownClass()
    def test_profile_lowering_and_hash_failure(self):
        fixture=memory.Memory()
        for ns in (0,3,17):
            c=fixture.config(ns);cp=memory.Memory.root/('plan'+str(memory.Memory.count)+'.json');cp.write_text(json.dumps(c))
            r=subprocess.run([str(memory.BUILD/'memory_plan_probe'),str(cp)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),dict(count=16,ns=16*ns,pending=0))
        c=fixture.config();c['profile_sha256']='0'*64;cp=memory.Memory.root/'bad-plan.json';cp.write_text(json.dumps(c))
        r=subprocess.run([str(memory.BUILD/'memory_plan_probe'),str(cp)],capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('hash',r.stderr)
if __name__=='__main__':unittest.main(verbosity=2)
