"""Fail-closed evidence and preregistration gates for V03-0."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from freeze_direct_baseline import verify_evidence, verify_hashes, freeze_budget
from verify_m0 import sha


class BaselineEvidenceTest(unittest.TestCase):
    def test_evidence_missing_failed_or_tampered(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); artifact=root/'assertions.json'; artifact.write_text('{"ok":true}')
            manifest=dict(schema='ndpsim-direct-baseline-v1',passed=True,
                          artifacts_sha256={'assertions.json':sha(artifact)})
            path=root/'manifest.json'; path.write_text(json.dumps(manifest))
            self.assertTrue(verify_evidence(root)['passed'])
            artifact.write_text('{"ok":false}')
            with self.assertRaises(ValueError): verify_evidence(root)
            artifact.unlink()
            with self.assertRaises(ValueError): verify_evidence(root)
            manifest['passed']=False; path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): verify_evidence(root)

    def test_empty_and_escaping_inventory_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): verify_hashes(Path(d),{})
            with self.assertRaises(ValueError): verify_hashes(Path(d),{'../outside':'invalid'})

    def test_budget_requires_ram_and_small_measurements_fit(self):
        small={p:{'peak_rss_bytes':64*1024**2} for p in ('host','controller')}
        b=freeze_budget(small,16*1024**3)
        self.assertEqual(b['query_wall_ratio_ir_direct_max'],1.5)
        self.assertEqual(b['q_gradient'],[32,128,512])
        with self.assertRaises(ValueError): freeze_budget(small,None)
        with self.assertRaises(ValueError): freeze_budget({},16*1024**3)
        small['host']['peak_rss_bytes']=5*1024**3
        with self.assertRaises(ValueError): freeze_budget(small,16*1024**3)


if __name__=='__main__': unittest.main()
