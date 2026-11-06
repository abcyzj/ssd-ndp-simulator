"""Runtime XML validation and independent geometry/state checks; no payload."""
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(sys.argv.pop(1)).resolve()
BASE = (ROOT/'configs/devices/native-small.xml').read_text()


def edit(text, **values):
    root = ET.fromstring(text)
    for key, value in values.items():
        root.find('.//'+key).text = str(value)
    return ET.tostring(root, encoding='unicode')


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='device 外部 空格 ')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.declaring = self.path/'nested'/'experiment.json'
        self.declaring.parent.mkdir()

    def run_probe(self, xml=BASE, success=True, **request):
        config = self.declaring.parent/'device.xml'
        config.write_text(xml)
        req = dict(reads=[dict(lba=7, sectors=10)], preload_sectors=512)
        req.update(request)
        path = self.path/'request.json'
        path.write_text(json.dumps(req))
        proc = subprocess.run([str(PROBE), 'device.xml', str(self.declaring), str(path)],
                              cwd=self.path, capture_output=True, text=True, timeout=20)
        artifacts = os.environ.get('NDPSIM_DEVICE_ARTIFACTS')
        if artifacts:
            folder = Path(artifacts)/('case-%04d' % len(list(Path(artifacts).glob('case-*'))))
            folder.mkdir(parents=True)
            (folder/'device.xml').write_text(xml)
            (folder/'request.json').write_text(json.dumps(req, indent=2))
            (folder/'process.json').write_text(json.dumps(dict(command=proc.args, cwd=str(self.path),
                exit_code=proc.returncode, expected_success=success, stdout=proc.stdout, stderr=proc.stderr), indent=2))
            if 'layout' in req:
                (folder/'layout.json').write_text((self.declaring.parent/req['layout']).read_text())
        lines = proc.stdout.splitlines()
        result = json.loads(lines[-1]) if lines else {}
        if not success:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse(result.get('allocated'), proc.stdout)
            return result
        self.assertEqual(proc.returncode, 0, proc.stderr+'\n'+proc.stdout)
        effective = result['effective']
        self.assertEqual(effective['source_sha256'], hashlib.sha256(xml.encode()).hexdigest())
        self.assertEqual(effective['source_path'], str(config.resolve()))
        self.assertEqual(result['source_bytes'], xml)
        self.oracle(effective, xml)
        if result['allocated']:
            g = effective
            self.assertEqual(result['partition_end_lba']+1, g['partition']['capacity_sectors'])
            self.assertEqual(result['actual_namespace_sectors'], g['partition']['capacity_sectors'])
            # The upstream getter is populated with a count despite its name.
            self.assertEqual(result['host_interface_field'], g['partition']['capacity_sectors'])
            self.assertEqual(result['initial']['preload_pages'], req['preload_sectors']//(g['page_bytes']//512))
            self.assertEqual(result['initial']['cmt_entries'], g['cmt_entries'])
            self.assertEqual(result['initial']['preconditioning_entries'], 0)
            self.assertGreater(len(result['initial_cache'].split(','))-1, 0)
            self.assertEqual(result['initial']['pending'], 0)
            self.assertTrue(result['drained'])
            self.assertTrue(result['registry_empty'])
            self.assertCountEqual([(r['lba'], r['sectors']) for r in result['completions']],
                                  [(r['lba'], r['sectors']) for r in req['reads']])
            for r in result['completions']: self.assertGreater(r['time'], 0)
            fields = {e.tag:e.text for e in ET.fromstring(xml).iter() if len(e)==0}
            for key, tag in [('queue_depth','IO_Queue_Depth'),('queue_fetch','Queue_Fetch_Size'),
                             ('lanes','PCIe_Lane_Count'),('read_lsb_ns','Page_Read_Latency_LSB'),
                             ('read_msb_ns','Page_Read_Latency_MSB'),('program_lsb_ns','Page_Program_Latency_LSB'),
                             ('cmt_bytes','CMT_Capacity')]:
                self.assertEqual(result['applied'][key], int(fields[tag]))
            self.assertEqual(result['applied']['lane_bandwidth'], float(fields['PCIe_Lane_Bandwidth']))
        return result

    def oracle(self, effective, xml):
        fields = {e.tag:e.text for e in ET.fromstring(xml).iter() if len(e)==0}
        geometry = [int(fields[t]) for t in ('Flash_Channel_Count','Chip_No_Per_Channel','Die_No_Per_Chip',
                                             'Plane_No_Per_Die','Block_No_Per_Plane','Page_No_Per_Block','Page_Capacity')]
        c, chips, d, planes, blocks, pages, page = geometry
        raw = c*chips*d*planes*blocks*pages*page
        per_plane = int(blocks*pages*(page//512)*(1.0-float(fields['Overprovisioning_Ratio'])))
        self.assertEqual(effective['raw_sectors']*512, raw)
        self.assertEqual(effective['page_bytes'], page)
        self.assertEqual(effective['partition']['capacity_sectors'], per_plane*c*chips*d*planes)
        for tag, n in [('channels',c),('chips',chips),('dies',d),('planes',planes)]:
            self.assertEqual(effective['partition'][tag], list(range(n)))
        self.assertEqual(effective['partition']['data_cache'], 'TURNED_OFF')
        for group in effective['xml_fields'].values():
            for key, value in group.items():
                if isinstance(value, float): self.assertEqual(value, float(fields[key]))
                elif type(value) is int: self.assertEqual(value, int(fields[key]))
                else: self.assertEqual(value, fields[key])

    def test_default_preload_cmt_and_drain(self):
        self.run_probe()

    def test_change_xml_without_recompile_changes_service(self):
        binary = hashlib.sha256(PROBE.read_bytes()).hexdigest()
        base = self.run_probe()
        slow = self.run_probe(edit(BASE, Page_Read_Latency_LSB=175000, Page_Read_Latency_MSB=175000))
        self.assertGreater(slow['completions'][0]['time'], base['completions'][0]['time'])
        self.assertEqual(slow['completions'][0]['time']-base['completions'][0]['time'], 200000)
        link = self.run_probe(edit(BASE, PCIe_Lane_Bandwidth='0.1', PCIe_Lane_Count=1))
        self.assertGreater(link['completions'][0]['time'], base['completions'][0]['time'])
        self.assertNotEqual(base['effective']['source_sha256'], slow['effective']['source_sha256'])
        self.assertEqual(binary, hashlib.sha256(PROBE.read_bytes()).hexdigest())

    def test_geometry_page_queue_seed_and_partition(self):
        for kwargs in [dict(Flash_Channel_Count=1, Die_No_Per_Chip=1, Block_No_Per_Plane=128),
                       dict(Chip_No_Per_Channel=2, Plane_No_Per_Die=2, Page_Capacity=8192),
                       dict(IO_Queue_Depth=32, Queue_Fetch_Size=2, Seed=77),
                       dict(Overprovisioning_Ratio='0.10', Page_No_Per_Block=128)]:
            with self.subTest(kwargs=kwargs):
                self.run_probe(edit(BASE, **kwargs), reads=[dict(lba=7+(i%16)*16, sectors=10) for i in range(32)])

    def test_small_cmt_retained_and_read_misses(self):
        result = self.run_probe(edit(BASE, CMT_Capacity=128), reads=[dict(lba=7+i*16, sectors=10) for i in range(16)])
        self.assertGreater(result['read_cmt_misses'], 0)
        self.assertLess(result['initial']['cmt_entries'], 64)

    def test_parse_once_and_lifecycle(self):
        self.run_probe(remove_source=True)
        result = self.run_probe(lifecycle=True)
        self.assertEqual(result['lifecycle_time_ns'], 0)
        self.assertTrue(result['registry_empty'])

    def test_layout_geometry_capacity_preload_and_end(self):
        sys.path.insert(0, str(ROOT/'python'))
        from ndpsim.layout import packed_layout
        objects = [dict(id='vectors',version=0,size_bytes=7000)]
        config = packed_layout(objects, capacity_sectors=1024, start_lba=1)
        path = self.declaring.parent/'layout.json'
        path.write_text(json.dumps(config))
        self.run_probe(layout='layout.json', objects=objects, preload_sectors=16, reads=[])
        self.run_probe(layout='layout.json', objects=objects, preload_sectors=8, success=False)
        config['namespace']['page_bytes'] = 8192
        path.write_text(json.dumps(config))
        self.run_probe(layout='layout.json', objects=objects, success=False)
        config['namespace'].update(page_bytes=4096,capacity_sectors=131072)
        path.write_text(json.dumps(config))
        self.run_probe(layout='layout.json', objects=objects, success=False)

    def test_preallocation_budgets_and_bounds(self):
        for args in [dict(metadata_bytes=1), dict(xml_bytes=20), dict(preload_pages=1),
                     dict(page_bytes=8192), dict(preload_sectors=513), dict(capacity_sectors=131072),
                     dict(preload_sectors=131072), dict(preload_sectors=0)]:
            with self.subTest(args=args): self.run_probe(success=False, **args)

    def test_strict_xml_whitelist_and_types(self):
        for field, value in [('PCIe_Lane_Bandwidth','nan'),('PCIe_Lane_Bandwidth','0'),('PCIe_Lane_Count','-1'),
                             ('PCIe_Lane_Count','4garbage'),('IO_Queue_Depth',65536),('Queue_Fetch_Size',16),
                             ('Enabled_Preconditioning','true'),('Ideal_Mapping_Table','true'),('Caching_Mechanism','ADVANCED'),
                             ('Address_Mapping','HYBRID'),('HostInterface_Type','SATA'),('Memory_Type','PCM'),
                             ('Flash_Technology','TLC'),('Seed',2**64),('CMT_Capacity',0),('Page_Capacity',1000),
                             ('Flash_Channel_Count',3),('Overprovisioning_Ratio','1'),('Overprovisioning_Ratio','0.001'),
                             ('Block_No_Per_Plane',2**32),('Flash_Channel_Count',16)]:
            if field=='Flash_Channel_Count' and value==16:
                xml = edit(BASE, Flash_Channel_Count=16,Chip_No_Per_Channel=8,Die_No_Per_Chip=8,Plane_No_Per_Die=4,
                           Block_No_Per_Plane=65536,Page_No_Per_Block=256,Page_Capacity=16384)
            else: xml = edit(BASE, **{field:value})
            with self.subTest(field=field,value=value): self.run_probe(xml, success=False, validate_only=True)
        variants = [BASE.replace('<Seed>321</Seed>',''), BASE.replace('<Seed>321</Seed>','<Seed>1</Seed><Seed>2</Seed>'),
                    BASE.replace('<Seed>321</Seed>','<Unknown>321</Unknown><Seed>321</Seed>'),
                    BASE.replace('<Seed>','<Seed unit="ns">'), BASE.replace('321</Seed>','3.21</Seed>'),
                    BASE.replace('321</Seed>','&value;</Seed>'), BASE+'<Extra/>', BASE+'\0',
                    BASE.replace('<Seed>321</Seed>','<Seed><Nested>321</Nested></Seed>'),
                    '<!DOCTYPE root>'+BASE, BASE.replace('<Seed>321</Seed>','<!--hidden--><Seed>321</Seed>'),
                    BASE.replace('</Seed>','</Other>'), '<x>'*40+'</x>'*40]
        for xml in variants: self.run_probe(xml, success=False, validate_only=True)

    def test_random_small_geometry_metadata_only(self):
        rng = random.Random(4510)
        for _ in range(20):
            xml = edit(BASE, Flash_Channel_Count=rng.choice([1,2,4]), Chip_No_Per_Channel=rng.choice([1,2]),
                       Die_No_Per_Chip=rng.choice([1,2]),Plane_No_Per_Die=rng.choice([1,2]),
                       Block_No_Per_Plane=rng.choice([64,128,256]),Page_No_Per_Block=rng.choice([32,64,128]),
                       Page_Capacity=rng.choice([1024,4096,8192]),Overprovisioning_Ratio=rng.choice(['0.07','0.10','0.20']))
            self.run_probe(xml, validate_only=True)

    def test_no_cwd_fallback(self):
        (self.path/'device.xml').write_text(BASE)
        (self.path/'request.json').write_text(json.dumps(dict(validate_only=True)))
        for declaring in (str(self.declaring), 'experiment.json'):
            p = subprocess.run([str(PROBE),'device.xml',declaring,str(self.path/'request.json')],
                               cwd=self.path,capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)

    def test_all_external_samples_and_policy(self):
        for path in (ROOT/'configs/devices').glob('*.xml'):
            self.run_probe(path.read_text(), validate_only=True)
        policy = json.loads((ROOT/'schemas/ndpsim-device-policy-v1.json').read_text())
        expected = {leaf.tag for leaf in ET.fromstring(BASE).iter() if len(leaf)==0}
        self.assertEqual({r['key'] for r in policy['fields']}, expected)

if __name__ == '__main__': unittest.main()
