"""M0 source-file identity and evidence failure checks (no benchmark download)."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('verify_m0', Path(__file__).resolve().parents[1]/'tools/verify_m0.py')
m0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m0)


class M0Tests(unittest.TestCase):
    def test_builder_rejects_corrupt_archive_before_extracting(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root/'configs/benchmarks/m0-builder.lock.json').read_text())
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            archives = td/'archives'
            archives.mkdir()
            (archives/next(iter(lock['archives']))).write_bytes(b'corrupt')
            run = subprocess.run([sys.executable, str(root/'tools/m0/build_builder.py'),
                                  '--archives', str(archives), '--work', str(td/'work'),
                                  '--output', str(td/'output')], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('locked archive mismatch', run.stderr)
            self.assertFalse((td/'work').exists())

    def test_frozen_query_partitions(self):
        root = Path(__file__).resolve().parents[1]
        selection = json.loads((root/'configs/benchmarks/v0.2-query-selection.json').read_text())
        for data in selection['datasets'].values():
            tune, evaluate = data['tuning_ids'], data['evaluation_ids']
            self.assertEqual((len(tune), len(evaluate), len(set(tune+evaluate))), (32, 128, 160))
            self.assertFalse(set(tune+evaluate) & set(selection['excluded_query_ids']))
            self.assertTrue(all(0 <= i < data['raw_query_count'] for i in tune+evaluate))
            self.assertEqual(data['concurrency_ids'], evaluate[:32])
            self.assertEqual(data['detail_ids'], evaluate[:8])
        lock = json.loads((root/'configs/benchmarks/datasets.lock.json').read_text())
        self.assertEqual(lock['query_selection_sha256'], m0.sha(root/lock['query_selection_file']))

    def test_complete_source_validation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'source'
            for fmt in ('fvecs', 'ivecs', 'fbin', 'ibin'):
                integer = fmt.startswith('i')
                row = struct.pack('<ii' if integer else '<ff', 0, 1)
                raw = struct.pack('<II', 2, 2)+row*2 if fmt.endswith('bin') else (struct.pack('<i', 2)+row)*2
                p.write_bytes(raw)
                r = m0.inspect(p, fmt, 2, 2, 3 if integer else None)
                self.assertEqual(r['sha256'], m0.sha(p))
                for malformed in (raw[:-1], raw+b'x', b'\xff'*4+raw[4:]):
                    p.write_bytes(malformed)
                    with self.assertRaises(ValueError):
                        m0.inspect(p, fmt, 2, 2, 3 if integer else None)

    def test_bad_payload_and_later_row(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'source'
            for value in (float('nan'), float('inf'), -float('inf')):
                p.write_bytes(struct.pack('<IIff', 1, 2, value, 1))
                with self.assertRaisesRegex(ValueError, 'non-finite'):
                    m0.inspect(p, 'fbin', 1, 2)
            for value in (-1, 3):
                p.write_bytes(struct.pack('<IIii', 1, 2, value, 1))
                with self.assertRaisesRegex(ValueError, 'ID out'):
                    m0.inspect(p, 'ibin', 1, 2, 3)
            p.write_bytes(struct.pack('<ifif', 1, 0, 2, 0))
            with self.assertRaisesRegex(ValueError, 'row dimension'):
                m0.inspect(p, 'fvecs', 2, 1)

    def test_measure_failure_timeout_and_budget(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for name, code, timeout, limit in (
                ('success', 'print(42)', 5, None),
                ('exit', 'raise SystemExit(3)', 5, None),
                ('timeout', 'import time; time.sleep(10)', .1, None),
                ('rss', 'print(42)', 5, 1),
            ):
                result = m0.measure([sys.executable, '-c', code], td/(name+'.json'), timeout, limit)
                self.assertEqual(result['passed'], name == 'success')
                self.assertEqual(result['timed_out'], name == 'timeout')
                self.assertEqual(json.loads((td/(name+'.json')).read_text()), result)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                m0.measure([sys.executable, '-c', 'print(0)'], td/'success.json', 5)


if __name__ == '__main__':
    unittest.main()
