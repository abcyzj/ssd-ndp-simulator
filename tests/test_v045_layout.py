"""Independent byte enumeration oracle for metadata-only Layout (no payload)."""
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'python'))
from ndpsim.layout import packed_layout
PROBE = Path(sys.argv.pop(1)).resolve()
U64 = 2**64-1


def obj(name, size, version=0):
    return dict(id=name, version=version, size_bytes=size)


def access(name, offset, length, version=0):
    return dict(object=name, version=version, offset_bytes=offset, length_bytes=length)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='layout 空格 ')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config = self.base/'nested config'
        self.config.mkdir()
        self.objects = [obj('vectors', 5000), obj('adjacency', 55), obj('queries', 0)]
        self.layout = packed_layout(self.objects, capacity_sectors=64, alignment_bytes=1, start_lba=1)

    def run_probe(self, accesses=(), layout=None, objects=None, success=True, raw=None, **extra):
        layout = self.layout if layout is None else layout
        path = self.config/'layout.json'
        path.write_text(json.dumps(layout) if raw is None else raw)
        request = self.base/'request.json'
        request.write_text(json.dumps(dict(objects=self.objects if objects is None else objects,
                                          accesses=accesses, **extra)))
        p = subprocess.run([str(PROBE), 'layout.json', str(self.config/'experiment.json'), str(request)],
                           cwd=self.base, capture_output=True, text=True)
        artifact_root = os.environ.get('NDPSIM_LAYOUT_ARTIFACTS')
        if artifact_root:
            folder = Path(artifact_root)/('%04d' % len(list(Path(artifact_root).glob('case-*'))))
            folder = folder.with_name('case-'+folder.name)
            folder.mkdir(parents=True)
            shutil.copy2(path, folder/'layout.json')
            shutil.copy2(request, folder/'request.json')
            (folder/'result.json').write_text(json.dumps(dict(command=p.args, cwd=str(self.base),
                expected_success=success, exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr), indent=2))
        if not success:
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertTrue(p.stderr)
            return
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual(result['source_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result['source_bytes'], path.read_text())
        self.assertEqual(result['source_path'], str(path.resolve()))
        self.assertEqual(result['effective'], layout)
        self.oracle(result, accesses, layout)
        return result

    def oracle(self, result, accesses, layout):
        # Deliberately enumerate each logical byte rather than copying production
        # interval intersection/sector-splitting arithmetic.
        physical, expected = set(), []
        rows = {(r['object'], r['version']): r['extents'] for r in layout['objects']}
        for i, a in enumerate(accesses):
            for byte in range(a['offset_bytes'], a['offset_bytes']+a['length_bytes']):
                e, = [e for e in rows[(a['object'], a['version'])]
                      if e['logical_offset_bytes'] <= byte < e['logical_offset_bytes']+e['length_bytes']]
                p = e['physical_offset_bytes'] + byte-e['logical_offset_bytes']
                physical.add(p//512)
                expected.append((i, byte, p))
        actual, sectors = [], []
        page = layout['namespace']['page_bytes']//512
        for r in result['reads']:
            self.assertGreater(r['sectors'], 0)
            self.assertEqual(r['lba']//page, (r['lba']+r['sectors']-1)//page)
            sectors.extend(range(r['lba'], r['lba']+r['sectors']))
            for s in r['slices']:
                self.assertLessEqual(s['request_offset_bytes']+s['length_bytes'], r['sectors']*512)
                for b in range(s['length_bytes']):
                    actual.append((s['access_index'], s['object_offset_bytes']+b,
                                   r['lba']*512+s['request_offset_bytes']+b))
        self.assertEqual(sectors, sorted(physical))
        self.assertEqual(sorted(actual), sorted(expected))
        self.assertEqual(result['logical_bytes'], sum(a['length_bytes'] for a in accesses))
        self.assertEqual(result['sector_bytes'], len(physical)*512)

    def test_boundaries_duplicates_shared_sector_empty(self):
        for accesses in ([], [access('queries', 0, 0)], [access('vectors', 5000, 0)],
                         [access('vectors', 0, 512)], [access('vectors', 511, 2)],
                         [access('vectors', 3583, 1026)], [access('vectors', 0, 5000)],
                         [access('vectors', 4999, 1), access('adjacency', 0, 55)],
                         [access('vectors', 3, 900)]*2):
            with self.subTest(accesses=accesses): self.run_probe(accesses)

    def test_hand_calculated_page_crossing(self):
        result = self.run_probe([access('vectors', 3583, 1026)])
        self.assertEqual(result['logical_bytes'], 1026)
        self.assertEqual(result['sector_bytes'], 2048)
        self.assertEqual([(r['lba'], r['sectors']) for r in result['reads']], [(7, 1), (8, 3)])

    def test_discontiguous_extent_and_unordered_rows(self):
        self.layout['objects'][0]['extents'] = [
            dict(logical_offset_bytes=3000, physical_offset_bytes=20000, length_bytes=2000),
            dict(logical_offset_bytes=0, physical_offset_bytes=1, length_bytes=3000)]
        self.layout['objects'].reverse()
        self.run_probe([access('vectors', 2800, 700)])

    def test_randomized_independent_oracle(self):
        rng = random.Random(945)
        for _ in range(60):
            objects = [obj('v', rng.randrange(0, 8000)), obj('a', rng.randrange(0, 1000))]
            layout = packed_layout(objects, capacity_sectors=128, alignment_bytes=rng.choice([1, 4, 512, 4096]),
                                   page_bytes=rng.choice([512, 1024, 4096, 8192]))
            accesses = []
            for _ in range(8):
                o = rng.choice(objects); start = rng.randrange(o['size_bytes']+1)
                accesses.append(access(o['id'], start, rng.randrange(o['size_bytes']-start+1)))
            self.run_probe(accesses, layout=layout, objects=objects)

    def test_reconfigure_without_rebuild(self):
        a = [access('vectors', 0, 50)]
        before = hashlib.sha256(PROBE.read_bytes()).hexdigest()
        first = self.run_probe(a)
        self.layout['objects'][0]['extents'][0]['physical_offset_bytes'] = 10000
        second = self.run_probe(a)
        self.assertNotEqual(first['reads'], second['reads'])
        self.assertNotEqual(first['source_sha256'], second['source_sha256'])
        self.assertEqual(before, hashlib.sha256(PROBE.read_bytes()).hexdigest())

    def test_invalid_layouts(self):
        changes = [
            lambda j: j.update(format='old-layout/0'), lambda j: j.update(payload_path='file'),
            lambda j: j.update(alignment_bytes=3), lambda j: j.update(alignment_bytes=4096),
            lambda j: j['namespace'].update(sector_bytes=4096),
            lambda j: j['namespace'].update(page_bytes=513),
            lambda j: j['namespace'].update(capacity_sectors=1),
            lambda j: j['namespace'].update(capacity_sectors=U64),
            lambda j: j['namespace'].update(capacity_sectors=True),
            lambda j: j['namespace'].update(capacity_sectors=64.0),
            lambda j: j['objects'].pop(), lambda j: j['objects'].append(j['objects'][0]),
            lambda j: j['objects'][0].update(version=1),
            lambda j: j['objects'][0]['extents'][0].update(physical_offset_bytes=U64),
            lambda j: j['objects'][0]['extents'][0].update(logical_offset_bytes=U64),
            lambda j: j['objects'][0]['extents'][0].update(length_bytes=0),
            lambda j: j['objects'][0]['extents'][0].update(length_bytes=5001),
            lambda j: j['objects'][0]['extents'][0].update(length_bytes=4999),
            lambda j: j['objects'][1]['extents'][0].update(physical_offset_bytes=512),
            lambda j: j['objects'][0]['extents'].append(j['objects'][0]['extents'][0]),
        ]
        for change in changes:
            j = copy.deepcopy(self.layout); change(j)
            with self.subTest(j=j): self.run_probe(layout=j, success=False)
        for value in (-1, U64+1, '512', None):
            j = copy.deepcopy(self.layout); j['namespace']['capacity_sectors'] = value
            self.run_probe(layout=j, success=False)
        self.run_probe(raw='{"format":0,"format":1}', success=False)
        self.run_probe(raw='['*40+']'*40, success=False)

    def test_invalid_access_and_budgets(self):
        for a in [access('vectors', 5001, 0), access('vectors', 5000, 1), access('vectors', U64, 1),
                  access('missing', 0, 0), access('vectors', 0, 0, 1)]:
            self.run_probe([a], success=False)
        self.run_probe([access('vectors', 0, 513)], plan_budget=1, success=False)
        self.run_probe(file_budget=10, success=False)
        self.run_probe(extent_budget=1, success=False)
        self.run_probe(objects=self.objects+[self.objects[0]], success=False)

    def test_uint64_end_empty_and_huge_logical_object_no_allocation(self):
        size = (U64//512)*512
        objects = [obj('large', size)]
        layout = packed_layout(objects, capacity_sectors=U64//512)
        result = self.run_probe([access('large', size, 0)], layout=layout, objects=objects)
        self.assertEqual(result['reads'], [])
        self.run_probe([access('large', size-1, 1)], layout=layout, objects=objects)
        self.run_probe([access('large', 0, size)], layout=layout, objects=objects,
                       plan_budget=4, success=False)

    def test_no_cwd_fallback_or_relative_declarer(self):
        (self.base/'fallback.json').write_text(json.dumps(self.layout))
        request = self.base/'request.json'
        request.write_text(json.dumps(dict(objects=self.objects, accesses=[])))
        for ref, declaring in [('fallback.json', str(self.config/'experiment.json')),
                               (str(self.base/'fallback.json'), 'experiment.json')]:
            p = subprocess.run([str(PROBE), ref, declaring, str(request)], cwd=self.base,
                               capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0, p.stdout)

    def test_generator_fail_closed(self):
        for kwargs in [dict(capacity_sectors=0), dict(capacity_sectors=U64),
                       dict(capacity_sectors=64, alignment_bytes=0),
                       dict(capacity_sectors=64, start_lba=U64), dict(capacity_sectors=64, page_bytes=3)]:
            with self.assertRaises(ValueError): packed_layout(self.objects, **kwargs)
        with self.assertRaises(ValueError): packed_layout(self.objects*2, capacity_sectors=64)

    def test_schema_and_graph_example(self):
        import jsonschema
        schema = json.loads((ROOT/'schemas/ndpsim-layout-v1.schema.json').read_text())
        example = json.loads((ROOT/'configs/layouts/graph-example.json').read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(example, schema)
        for field in ('format', 'namespace', 'objects', 'alignment_bytes'):
            bad = copy.deepcopy(example); del bad[field]
            with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(bad, schema)
        objects = [obj(r['object'], sum(e['length_bytes'] for e in r['extents']), r['version']) for r in example['objects']]
        self.run_probe([access(o['id'], 0, o['size_bytes']) for o in objects], layout=example, objects=objects)

if __name__ == '__main__': unittest.main()
