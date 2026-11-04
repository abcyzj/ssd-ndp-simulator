"""Positive fixtures and re-sealed structural mutations for V04-2a."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from v04_contract import (canonical, digest, package_hash, schema, validate_config,
                          validate_measurement, validate_package, validate_profile)
from jsonschema import Draft202012Validator

FIXTURE = ROOT/'examples/v04-contract'


def load(path):
    return json.loads(path.read_bytes())


def lines(rows):
    return b''.join(canonical(row)+b'\n' for row in rows)


class ContractTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/'fixture'
        shutil.copytree(FIXTURE, self.root)
        self.package = self.root/'package'
        self.binding = self.root/'binding.json'

    def validate(self):
        return validate_package(self.package, self.binding)

    def reseal(self, mutate):
        index = [json.loads(line) for line in (self.package/'index.jsonl').read_bytes().splitlines()]
        raw = (self.package/'operations.jsonl').read_bytes()
        shards = [[json.loads(line) for line in raw[e['offset']:e['offset']+e['length']].splitlines()]
                  for e in index]
        mutate(index, shards)
        raw = b''
        for entry, rows in zip(index, shards):
            shard = lines(rows)
            entry.update(offset=len(raw), length=len(shard), operations=len(rows),
                         dependencies=sum(len(r['deps']) for r in rows),
                         sha256=digest(shard), decision_sha256=digest(shard))
            raw += shard
        idx = lines(index)
        (self.package/'index.jsonl').write_bytes(idx)
        (self.package/'operations.jsonl').write_bytes(raw)
        tail = load(self.package/'tail.json')
        tail.update(queries=len(index), operations=sum(e['operations'] for e in index),
                    dependencies=sum(e['dependencies'] for e in index), index_sha256=digest(idx),
                    operations_sha256=digest(raw))
        tail['package_sha256'] = package_hash(tail)
        (self.package/'tail.json').write_bytes(canonical(tail))

    def test_legal_round_trip_and_schema(self):
        for kind, validator in [('measurement', validate_measurement), ('cpu-profile', validate_profile),
                                ('effective-config', validate_config)]:
            with self.subTest(kind=kind):
                Draft202012Validator.check_schema(schema(kind).schema)
                value = load(self.root/f'{kind}.json')
                validator(json.loads(canonical(value)))
        for definition in (None, 'operation', 'index', 'tail', 'binding'):
            Draft202012Validator.check_schema(schema('static-ir', definition).schema)
        report = self.validate()
        self.assertEqual((report['queries'], report['operations'], report['dependencies']), (2, 38, 82))
        self.reseal(lambda index, shards: None)
        self.assertEqual(self.validate(), report)

    def test_bound_content_changes(self):
        with (self.root/'base.bin').open('r+b') as stream:
            stream.write(b'\x01')
        with self.assertRaisesRegex(ValueError, 'bound object mismatch'):
            self.validate()

    def test_independent_binding(self):
        for key, mutate in [
            ('query IDs', lambda v: v['identity']['query_ids'].reverse()),
            ('search', lambda v: v['identity']['search'].update(chunk=2)),
            ('build', lambda v: v['build'].update(binary_sha256='0'*64)),
            ('source', lambda v: v.update(source='native-capture')),
        ]:
            with self.subTest(key=key):
                binding = load(FIXTURE/'binding.json')
                mutate(binding)
                self.binding.write_bytes(canonical(binding))
                with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                    self.validate()

    def test_resealed_semantic_mutations(self):
        mutations = [
            ('query', lambda r: r[0].update(query=42)),
            ('scope', lambda r: r[2].update(batch=1)),
            ('duplicate op', lambda r: r[2].update(op=2)),
            ('version', lambda r: r[0].update(operator_version=2)),
            ('missing address', lambda r: r[6]['deps'].pop(0)),
            ('address type', lambda r: r[6]['deps'][0].update(type='fp32')),
            ('address port', lambda r: r[6]['deps'][0].update(port='query')),
            ('wrong kind', lambda r: r[6]['deps'][0].update(kind='ORDER')),
            ('forward', lambda r: r[6]['deps'][0].update(producer=8)),
            ('cycle', lambda r: r[6]['deps'][0].update(producer=7)),
            ('state', lambda r: r[1]['state'].update(before=1)),
            ('unbound access', lambda r: r[2]['access'].update(ids=[1])),
            ('state output', lambda r: r[1]['expected']['output'].update(type='ids_u32')),
            ('work', lambda r: r[2]['expected']['work'].update(flops=0)),
            ('merge work bound', lambda r: r[3]['expected']['work'].update(comparisons=1)),
            ('join digest', lambda r: r[7]['expected']['output'].update(sha256='0'*64)),
            ('decision', lambda r: r[4]['expected'].update(ids=[])),
            ('missing terminal', lambda r: r.pop()),
            ('post-terminal', lambda r: r.append(copy.deepcopy(r[-1]))),
            ('injected time', lambda r: r[0].update(capture_ns=1)),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                for path in (FIXTURE/'package').iterdir():
                    shutil.copyfile(path, self.package/path.name)
                self.reseal(lambda index, shards: mutate(shards[0]))
                with self.assertRaisesRegex(ValueError, 'query 7'):
                    self.validate()

    def test_resealed_query_coverage_and_terminal(self):
        cases = [
            lambda index, shards: (index.pop(), shards.pop()),
            lambda index, shards: index[1].update(query=7),
            lambda index, shards: index[0].update(terminal='exhausted'),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                for path in (FIXTURE/'package').iterdir():
                    shutil.copyfile(path, self.package/path.name)
                self.reseal(mutate)
                with self.assertRaisesRegex(ValueError, 'query (7|42)'):
                    self.validate()

    def test_truncation_and_diagnostics_fail_cli(self):
        ops = self.package/'operations.jsonl'
        ops.write_bytes(ops.read_bytes()[:-1])
        cmd = [sys.executable, str(ROOT/'tools/v04_contract.py'), 'package', str(self.package),
               '--binding', str(self.binding)]
        result = subprocess.run(cmd, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stderr)['valid'])
        shutil.copyfile(ROOT/'schemas/ndpsim-ir-diagnostics-v1.schema.json', self.package/'manifest.json')
        result = subprocess.run(cmd, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stderr)['valid'])

    def test_measurement_fail_closed(self):
        cases = [
            ('unit', lambda r: r.update(unit='us')),
            ('version', lambda r: r.update(format='ndpsim-measurement/2')),
            ('loss', lambda r: r.update(dropped=1)),
            ('query', lambda r: r['samples'][0].update(query=2)),
            ('operator identity', lambda r: r['samples'][0].update(operator='INIT', bucket='init', chunk=None)),
            ('duplicate', lambda r: r['samples'].append(copy.deepcopy(r['samples'][0]))),
            ('overlap', lambda r: r['samples'][1].update(start_ns=4, raw_ns=6, corrected_ns=5)),
            ('negative interval', lambda r: r['samples'][0].update(end_ns=0, start_ns=1)),
            ('missing sample', lambda r: r['samples'].pop()),
            ('missing audit', lambda r: r['score_audits'].pop()),
            ('bad closure', lambda r: r['score_audits'][0].update(end_ns=24)),
            ('quality', lambda r: r['samples'][0].update(corrected_ns=0, quality='low_signal')),
            ('worker timeline', lambda r: r['windows'][1].update(worker_start_ns=24, admission_ns=24)),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                value = load(self.root/'measurement.json')
                mutate(value)
                with self.assertRaises(ValueError):
                    validate_measurement(value)
        value = load(self.root/'measurement.json')
        value['samples'][0].update(end_ns=0, raw_ns=0, corrected_ns=-1, quality='low_signal')
        value['samples'][1].update(start_ns=0, raw_ns=10, corrected_ns=9)
        self.assertEqual(validate_measurement(value)['low_signal'], 1)

    def test_profile_forms_and_legacy_config(self):
        value = load(self.root/'cpu-profile.json')
        for model in (dict(kind='linear', intercept_ns=0, coefficients=[4]),
                      dict(kind='table', bins=[dict(upper=1, ns=0)])):
            with self.subTest(model=model):
                value['models'][0]['model'] = model
                validate_profile(value)
        config = load(self.root/'effective-config.json')
        config.update(execution_engine='ir', data_provider='ssd', content_backend='memory',
                      order_policy='legacy-ssd/1', ir_input=None, cpu_profile=None)
        self.assertEqual(validate_config(config)['engine'], 'ir')

    def test_profile_and_config_fail_closed(self):
        for kind, validate, cases in [
            ('cpu-profile', validate_profile, [
                lambda v: v['models'][0]['features'][0].update(unit='B'),
                lambda v: v['models'][0]['features'][0].update(minimum=2),
                lambda v: v['models'][0].update(stage='merge'),
                lambda v: v['models'][0].update(operator='MARK_SCORED', stage='state'),
                lambda v: v['models'][0].update(model=dict(kind='constant', ns=2**64)),
                lambda v: v['models'][0].update(model=dict(kind='linear', intercept_ns=0, coefficients=[1, 2])),
                lambda v: v['models'][0].update(model=dict(kind='table', bins=[dict(upper=2, ns=1)])),
            ]),
            ('effective-config', validate_config, [
                lambda v: v.update(cpu_slots=2),
                lambda v: v.update(execution_engine='ir'),
                lambda v: v.update(placement='controller'),
                lambda v: v.update(cpu_profile=None),
                lambda v: v.update(ir_input=None),
                lambda v: v.update(order_policy='legacy-ssd/1'),
            ]),
        ]:
            for mutate in cases:
                with self.subTest(kind=kind, mutate=mutate):
                    value = load(self.root/f'{kind}.json')
                    mutate(value)
                    with self.assertRaises(ValueError):
                        validate(value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
