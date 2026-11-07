"""P06 synthetic service fixtures, strict negative cases, independent C++ consumer."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'python'))
from ndpsim.static_ir import OperatorRegistry, PackageWriter, canonical, decode, digest_bytes, file_hash
from ndpsim.fitting import execute
from ndpsim.cost_models import PARAMETER_SCHEMAS, estimate, fit, schema_hash, validate_model

PROBE = sys.argv.pop(1) if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None
TARGET = dict(id='fixture-cpu', hardware='hypothetical', os='fixture', memory_bytes=8192, frequency='fixed', affinity='one')
TUNING = list(range(8)); EVALUATION = list(range(100, 108))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical(value))
    return dict(path=str(path), sha256=file_hash(path))


def fixture(root, formula='nonnegative-linear/1', context='resident/1'):
    port = dict(name='out', type='token', unit='count', required=True, cardinality='one')
    stages = [dict(kind=kind, inputs=[], outputs=[port], work_units={'flops': 'flop'}, feature_units={'items': 'count'}, contexts=[context], decomposition_rules=['none/1'], output_size_rule='zero/1') for kind in ('compute', 'append', 'token')]
    spec = dict(id='example.work', version='1', locations=['host'], stages=stages, semantic_validator='fixture-oracle/1')
    registry = OperatorRegistry(dict(format='ndpsim-operator-registry/1', units=['count', 'flop'], operators=[spec], rules=[dict(id='zero/1', kind='fixed-outputs/1', bytes={'out': 0})]))
    rules = [dict(operator=spec['id'], operator_version='1', stage_kind=kind, context=context, location='host', source=source, features=['items'], formula=formula if source == 'prediction' else None, options={}) for kind, source in [('compute', 'prediction'), ('append', 'measured-table'), ('token', 'declared-zero')]]
    if formula == 'log-gbdt/1': rules[0]['options'] = dict(trees=12, depth=3, leaf=2, rate=.5)
    recipe = dict(format='ndpsim-fit-recipe/1', id='fixture-recipe/1', operators=[spec], rules=rules, training=dict(folds=4, strides=[1], min_runs=1, max_low_signal_fraction=.05, min_holdout_fraction=1, max_relative_p50=.1, max_relative_p90=.2), sampling_audits=[], training_points=[dict(id='p1', stride=1, parameters={'width': 1})], formal_partitions=None, provenance_splits=dict(tuning='training', evaluation='test'))
    recipe_ref = save(root/'recipe.json', recipe)
    config_ref = save(root/'semantic-config.json', dict(width=1))
    datasets = {}
    for split, ids in [('tuning', TUNING), ('evaluation', EVALUATION)]:
        directory = root/split; directory.mkdir()
        provenance = dict(kind='synthetic-fixture', capture_build=dict(source_sha256='1'*64, binary_sha256='2'*64, compiler='fixture', flags=['-fno-fast-math']), input_content_sha256='3'*64, semantic_version='fixture/1', extension_sha256=registry.sha256, split='fixture', query_ids=ids, workload='example.work', numeric_version='integer/1', work_version='counts/1', semantic_config_sha256=config_ref['sha256'])
        planned = []
        with PackageWriter(directory/'package', provenance, registry, allow_fixture=True) as writer:
            for q in ids:
                rank = 0
                for i in range(1, 9):
                    for j, kind in enumerate(('compute', 'append', 'token')):
                        op_id = f'call-{i}-{kind}'; work = dict(flops=dict(value=i*3, unit='flop')); features = dict(items=dict(value=i, unit='count'))
                        st = dict(id='s0', kind=kind, topological_rank=rank, dependencies=[], work=work, features=features, context=context, chunk=i, segment=0, accesses=[], outputs=[dict(port='out', type='token', logical_bytes=0, version=0, digest='4'*64)], state_versions=[], decomposition_rule='none/1')
                        op = dict(query=q, id=op_id, operator='example.work', operator_version='1', semantic_work=work, stages=[st]); writer.record(op)
                        row = dict(key=dict(query=q, op=op_id, stage='s0'), chunk=i, segment=0, stage_kind=kind, work=work, features=features)
                        if kind != 'token': planned.append(row)
                        rank += 1
            terminals = [dict(query=q, op=f'call-{i}-{kind}', stage='s0') for q in ids for i in range(1, 9) for kind in ('compute', 'append', 'token')]
            structural = writer.seal(terminals)
        structural_ref = save(directory/'structural.json', structural)
        package = structural['package_sha256']; binding = dict(package_sha256=package, provenance=provenance, target=TARGET, context=context, workers=1, slots=1, admission=1)
        runs = []
        for run in range(3):
            rd = directory/f'run-{run}'; rd.mkdir()
            ref = dict(cpu_clock='CLOCK_THREAD_CPUTIME_ID', cpu_resolution_ns=1, empty_probe_ns=20, empty_probe_count=10, paging_eligible=True, major_faults=0, completed=len(ids), workers=1, concurrency=1, independent_run=run, queries=[dict(original_query_id=q, cpu_start_ns=1, thread_cpu_ns=2**64-2) for q in ids])
            reference_ref = save(rd/'reference.json', ref)
            semantic_ref = save(rd/'semantic.json', dict(structural_passed=True, independent_semantic_passed=True, package_sha256=package, provenance=provenance, query_ids=ids, reference_report_sha256=reference_ref['sha256'], fixture=True))
            rows = []; clock = 100+run
            for s in planned:
                row = copy.deepcopy(s); q = row['key']['query']; i = row['features']['items']['value']
                ns = ((40 if formula == 'constant/1' else 20+4*i) if row['stage_kind'] == 'compute' else 30+2*i) + run-1
                start = clock; end = start+ns+2; clock = end+10
                row.update(calls=1, start_cpu_ns=start, end_cpu_ns=end, raw_ns=ns+2, corrected_ns=ns, low_signal=False); rows.append(row)
            measurement_ref = save(rd/'measurement.json', dict(format='ndpsim-measurement/2', binding=binding, mode='cpu_sample', clock=dict(source='CLOCK_THREAD_CPUTIME_ID', resolution_ns=1, empty_probe_ns=20, empty_probe_count=10), sampling=dict(stride=1, eligible=len(rows), planned=len(rows), observed=len(rows), lost=0), samples=rows, reference_report_sha256=reference_ref['sha256']))
            status_ref = save(rd/'status.json', dict(passed=True, mode='sample', files_sha256={'measurement.json': measurement_ref['sha256'], 'reference.json': reference_ref['sha256'], 'semantic.json': semantic_ref['sha256']}))
            runs.append(dict(run_id=f'{split}-{run}', measurement=measurement_ref, reference=reference_ref, semantic_report=semantic_ref, status=status_ref))
        datasets[split] = dict(split=split, point='p1', package=dict(path=str(directory/'package'), sha256=package), structural_report=structural_ref, semantic_config=config_ref, runs=runs)
    manifest = dict(format='ndpsim-fit-manifest/1', fixture=True, recipe=recipe_ref, target=TARGET, partitions=dict(tuning=TUNING, evaluation=EVALUATION), datasets=[datasets['tuning']], budget=dict(input_bytes=32*1024**2, samples=10000, stages=10000, output_bytes=16*1024**2, timeout_seconds=60))
    save(root/'manifest.json', manifest)
    return manifest, recipe, datasets


class Fitting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='P06 fit '); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.manifest, self.recipe, self.datasets = fixture(self.root)
        self.count = 0

    def run_action(self, action='fit', manifest=None):
        self.count += 1; path = self.root/f'manifest-{self.count}.json'; save(path, manifest or self.manifest)
        out = self.root/f'out-{self.count}'
        result = execute(action, path, out, allow_fixture=True)
        return out, result

    def rewrite_run(self, dataset, index, field, change):
        run = dataset['runs'][index]; path = Path(run[field]['path']); value = decode(path.read_bytes()); change(value)
        run[field] = save(path, value)
        status_path = Path(run['status']['path']); status = decode(status_path.read_bytes())
        status['files_sha256'][path.name] = run[field]['sha256']; run['status'] = save(status_path, status)

    def rewrite_reference(self, dataset, index, change):
        self.rewrite_run(dataset, index, 'reference', change)
        h = dataset['runs'][index]['reference']['sha256']
        for field in ('measurement', 'semantic_report'):
            self.rewrite_run(dataset, index, field, lambda row:row.update(reference_report_sha256=h))

    def freeze(self):
        out, report = self.run_action(); return dict(path=str(out/'profile.json'), sha256=file_hash(out/'profile.json')), dict(path=str(out/'fit-report.json'), sha256=file_hash(out/'fit-report.json'))

    def table_manifest(self):
        p, r = self.freeze(); m = copy.deepcopy(self.manifest); m.update(frozen_profile=p, frozen_report=r, datasets=[self.datasets['evaluation']]); return m

    def bad(self, action, manifest):
        self.count += 1; path = self.root/f'bad-manifest-{self.count}.json'; save(path, manifest); out = self.root/f'bad-out-{self.count}'
        with self.assertRaises((ValueError, KeyError, TypeError, OverflowError)):
            execute(action, path, out, allow_fixture=True)
        status = decode((out/'status.json').read_bytes()); self.assertFalse(status['passed']); self.assertFalse((out/'profile.json').exists())

    def consume(self, profile, inputs):
        if not PROBE: self.skipTest('C++ consumer supplied by root CTest')
        req = dict(operators=self.recipe['operators'], profile=profile, target=TARGET, inputs=inputs)
        result = subprocess.run([PROBE], input=json.dumps(req)+'\n', capture_output=True, text=True, check=True)
        result = json.loads(result.stdout)
        if 'schema_sha256' in result:
            self.assertEqual(result['schema_sha256'], {f: schema_hash(f) for f in PARAMETER_SCHEMAS})
        return result

    def test_fit_determinism_grouped_folds_and_cpp_consumer(self):
        out, report = self.run_action(); out2, _ = self.run_action()
        self.assertEqual((out/'profile.json').read_bytes(), (out2/'profile.json').read_bytes())
        self.assertEqual((out/'fit-report.json').read_bytes(), (out2/'fit-report.json').read_bytes())
        profile = decode((out/'profile.json').read_bytes()); r = profile['rules'][0]
        held = set()
        for f in report['rules'][0]['folds']:
            self.assertFalse(set(f['training_query_ids'])&set(f['held_out_query_ids'])); held.update(f['held_out_query_ids'])
        self.assertEqual(held, set(TUNING)); self.assertLess(report['rules'][0]['holdout']['relative_p90'], .1)
        inputs = [dict(key=dict(query=0, op='a', stage='s0'), operator='example.work', operator_version='1', stage_kind='compute', context='resident/1', location='host', work=dict(flops=dict(value=i*3, unit='flop')), features=dict(items=dict(value=i, unit='count'))) for i in range(1, 9)]
        result = self.consume(profile, inputs)
        self.assertEqual(result['ns'], [estimate(r['formula'], r['parameters'], [i]) for i in range(1, 9)])

    def test_table_median_and_full_binding_cpp(self):
        m = self.table_manifest(); out, report = self.run_action('table', m); p = decode((out/'profile.json').read_bytes())
        rule = next(r for r in p['rules'] if r['source'] == 'measured-table')
        self.assertEqual(len(rule['rows']), 64); self.assertEqual(report['rules'][0]['prediction_error'], 'not-applicable')
        inputs = [dict(copy.deepcopy(row), operator='example.work', operator_version='1', stage_kind='append', context='resident/1', location='host', binding=rule['binding']) for row in rule['rows']]
        self.assertEqual(self.consume(p, inputs)['ns'], [30+2*row['features']['items']['value'] for row in rule['rows']])
        inputs[0]['work']['flops']['value'] += 1; self.assertIn('error', self.consume(p, inputs))
        p['rules'][-1]['rows'].pop(); self.assertIn('error', self.consume(p, [inputs[-1]]))

    def test_evaluate_frozen_no_refit(self):
        m = self.table_manifest(); out, report = self.run_action('evaluate', m)
        self.assertTrue(report['passed']); self.assertFalse((out/'profile.json').exists())

    def test_training_evaluation_split_and_ids(self):
        for mutate in (lambda m: m.update(datasets=[self.datasets['evaluation']]), lambda m: m['partitions']['evaluation'].append(0), lambda m: m['partitions']['tuning'].reverse(), lambda m: m['partitions']['tuning'].pop()):
            m = copy.deepcopy(self.manifest); mutate(m); self.bad('fit', m)

    def test_missing_duplicate_and_work_samples(self):
        for change in (lambda m: m['samples'].pop(), lambda m: m['samples'].append(copy.deepcopy(m['samples'][0])), lambda m: m['samples'][0]['work']['flops'].update(value=999), lambda m: m['samples'][0]['features']['items'].update(unit='B'), lambda m: m['samples'][0].update(segment=9), lambda m: m['samples'][0]['work']['flops'].update(value=3.0)):
            m = copy.deepcopy(self.manifest); original = Path(m['datasets'][0]['runs'][0]['measurement']['path']).read_bytes()
            self.rewrite_run(m['datasets'][0], 0, 'measurement', change); self.bad('fit', m)
            Path(m['datasets'][0]['runs'][0]['measurement']['path']).write_bytes(original)
            # Restore status used by subsequent cases.
            status = self.datasets['tuning']['runs'][0]['status']; value = decode(Path(status['path']).read_bytes()); value['files_sha256']['measurement.json'] = digest_bytes(original); save(Path(status['path']), value)

    def test_time_overlap_low_signal_clock_and_paging(self):
        changes = [lambda m: m['samples'][0].update(corrected_ns=-1), lambda m: m['samples'][0].update(low_signal=True), lambda m: m['clock'].update(source='wall'), lambda m: m['sampling'].update(lost=1), lambda m: m['samples'][1].update(start_cpu_ns=102, end_cpu_ns=136, raw_ns=34, corrected_ns=32)]
        for change in changes:
            m = copy.deepcopy(self.manifest); source = Path(m['datasets'][0]['runs'][0]['measurement']['path']); raw = source.read_bytes(); status = Path(m['datasets'][0]['runs'][0]['status']['path']); sr = status.read_bytes()
            self.rewrite_run(m['datasets'][0], 0, 'measurement', change); self.bad('fit', m); source.write_bytes(raw); status.write_bytes(sr)
        m = copy.deepcopy(self.manifest); self.rewrite_reference(m['datasets'][0], 0, lambda r:r.update(major_faults=1)); self.bad('fit', m)

    def test_table_requires_three_distinct_runs_and_complete_calls(self):
        m = self.table_manifest()
        for mutate in (lambda x:x['datasets'][0]['runs'].pop(), lambda x:x['datasets'][0]['runs'][1].update(run_id=x['datasets'][0]['runs'][0]['run_id']), lambda x:x['datasets'][0]['runs'].__setitem__(1, dict(x['datasets'][0]['runs'][0], run_id='distinct-name'))):
            bad = copy.deepcopy(m); mutate(bad); self.bad('table', bad)
        self.rewrite_run(m['datasets'][0], 0, 'measurement', lambda r:r['samples'].pop()); self.bad('table', m)

    def test_strict_reference_and_partition_scalar_types(self):
        m = copy.deepcopy(self.manifest)
        self.rewrite_reference(m['datasets'][0], 0, lambda r:r['queries'][0].update(original_query_id=False))
        self.bad('fit', m)
        recipe = copy.deepcopy(self.recipe); recipe['formal_partitions'] = dict(tuning=[False], evaluation=[100])
        m = copy.deepcopy(self.manifest); m['recipe'] = save(self.root/'bad-id-recipe.json', recipe)
        self.bad('fit', m)

    def test_rejected_holdout_retains_metrics_without_profile(self):
        m = copy.deepcopy(self.manifest)
        recipe = copy.deepcopy(self.recipe)
        recipe['training']['max_relative_p50'] = 0
        recipe['training']['max_relative_p90'] = 0
        m['recipe'] = save(self.root/'strict-recipe.json', recipe)
        # Query-dependent cost cannot be explained by the items feature. Keep
        # all interval arithmetic/Work/coverage valid so the accuracy gate fires.
        def noise(record):
            clock = 100
            for row in record['samples']:
                ns = 10 + row['key']['query'] * 100 if row['stage_kind'] == 'compute' else 40
                row.update(start_cpu_ns=clock, end_cpu_ns=clock+ns+2,
                           raw_ns=ns+2, corrected_ns=ns, low_signal=False)
                clock += ns+12
        for index in range(3): self.rewrite_run(m['datasets'][0], index, 'measurement', noise)
        path = self.root/'rejected-training.json'; save(path, m)
        out = self.root/'rejected-fit'
        with self.assertRaisesRegex(ValueError, 'prediction holdout accuracy gate'):
            execute('fit', path, out, allow_fixture=True)
        failure = decode((out/'failure-report.json').read_bytes())
        self.assertFalse(failure['passed'])
        self.assertFalse(failure['full_acceptance_passed'])
        self.assertEqual(failure['manifest_sha256'], file_hash(path))
        self.assertEqual(failure['rejection']['stage'][:3], ['example.work', '1', 'compute'])
        self.assertGreater(failure['rejection']['holdout']['relative_p90'], 0)
        self.assertEqual(len(failure['rejection']['folds']), 4)
        self.assertFalse((out/'profile.json').exists())
        self.assertFalse((out/'fit-report.json').exists())
        self.assertFalse(decode((out/'status.json').read_bytes())['passed'])

    def test_v2_censored_tables_keep_raw_values_and_positive_replicas(self):
        recipe = copy.deepcopy(self.recipe); recipe['format'] = 'ndpsim-fit-recipe/2'
        recipe['id'] = 'fixture-censored/2'
        recipe['rules'][1]['low_signal_policy'] = dict(kind='censored-zero/1', reference_ns=200)
        m = copy.deepcopy(self.manifest); m['recipe'] = save(self.root/'recipe2.json', recipe)
        _, _ = self.run_action('fit', m)
        frozen = self.root/f'out-{self.count}'
        table = copy.deepcopy(m); table.update(datasets=[self.datasets['evaluation']], frozen_profile=save(self.root/'frozen-copy.json', decode((frozen/'profile.json').read_bytes())), frozen_report=dict(path=str(frozen/'fit-report.json'), sha256=file_hash(frozen/'fit-report.json')))
        def low(record):
            row = record['samples'][1]
            row.update(end_cpu_ns=row['start_cpu_ns'], raw_ns=0, corrected_ns=-2, low_signal=True)
        # Two low replicas yield zero median; one positive replica stays intact.
        for i in range(2): self.rewrite_run(table['datasets'][0], i, 'measurement', low)
        raw_before = Path(table['datasets'][0]['runs'][0]['measurement']['path']).read_bytes()
        zero, report = self.run_action('table', table)
        profile = decode((zero/'profile.json').read_bytes()); rule = next(r for r in profile['rules'] if r['source']=='measured-table')
        self.assertEqual(rule['rows'][0]['duration_ns'], 0)
        self.assertEqual(rule['approximation']['censored_samples'], 2)
        self.assertEqual(rule['approximation']['zero_calls'], 1)
        self.assertEqual(Path(table['datasets'][0]['runs'][0]['measurement']['path']).read_bytes(), raw_before)
        self.assertTrue(any(r.get('censored') for r in report['rules']))
        if PROBE:
            first = rule['rows'][0]
            request = dict(key=first['key'], operator='example.work', operator_version='1', stage_kind='append', context='resident/1', location='host', work=first['work'], features=first['features'], binding=rule['binding'])
            self.assertEqual(self.consume(profile,[request])['ns'],[0])
        table['table_scenario'] = 'reference'
        reference, _ = self.run_action('table', table)
        ref_rule = next(r for r in decode((reference/'profile.json').read_bytes())['rules'] if r['source']=='measured-table')
        self.assertEqual(ref_rule['rows'][0]['duration_ns'], 200)
        check = copy.deepcopy(table); check.pop('frozen_profile'); check.pop('frozen_report')
        out, receipt = self.run_action('table-check', check)
        self.assertTrue(receipt['passed']); self.assertFalse(receipt['profile_published'])
        self.assertFalse((out/'profile.json').exists())
        bad = copy.deepcopy(check); self.rewrite_run(bad['datasets'][0], 2, 'measurement', lambda v:v['samples'].pop())
        self.bad('table-check', bad)

    def test_v2_metadata_prefix_features_python_and_cpp(self):
        m = copy.deepcopy(self.manifest); recipe = copy.deepcopy(self.recipe)
        recipe.update(format='ndpsim-fit-recipe/2', id='fixture-metadata/2')
        descriptor = dict(name='prior_items', unit='count', rule='query-prefix-sum/1', operator='example.work', operator_version='1', stage_kind='compute', feature='items')
        recipe['rules'][0].update(features=['items','prior_items'], metadata_features=[descriptor], candidates=[dict(formula='nonnegative-linear/1', options={}, features=['items','prior_items'])])
        m['recipe'] = save(self.root/'metadata-recipe.json', recipe)
        def duration(record):
            clock=100
            for row in record['samples']:
                i=row['features']['items']['value']; ns=70+4*i+6*i*(i-1)//2 if row['stage_kind']=='compute' else row['corrected_ns']
                row.update(start_cpu_ns=clock,end_cpu_ns=clock+ns+2,raw_ns=ns+2,corrected_ns=ns,low_signal=False);clock+=ns+12
        for i in range(3):self.rewrite_run(m['datasets'][0],i,'measurement',duration)
        out, report = self.run_action('fit',m)
        self.assertTrue(report['passed']); profile=decode((out/'profile.json').read_bytes())
        r=profile['rules'][0]; self.assertEqual(r['metadata_features'],[descriptor])
        row=dict(key=dict(query=0,op='a',stage='s0'),operator='example.work',operator_version='1',stage_kind='compute',context='resident/1',location='host',work=dict(flops=dict(value=12,unit='flop')),features=dict(items=dict(value=4,unit='count')))
        row['metadata_features']=dict(prior_items=dict(value=6,unit='count'))
        if PROBE:
            self.assertEqual(self.consume(profile,[row])['ns'],[estimate(r['formula'],r['parameters'],[4,6])])
            del row['metadata_features']
            self.assertIn('error',self.consume(profile,[row]))
        bad = copy.deepcopy(m); recipe['rules'][0]['metadata_features'][0]['unit']='B'
        bad['recipe']=save(self.root/'wrong-metadata-unit.json',recipe);self.bad('fit',bad)

    def test_v2_failed_grid_reports_all_stages_without_profile(self):
        m=copy.deepcopy(self.manifest);recipe=copy.deepcopy(self.recipe)
        recipe.update(format='ndpsim-fit-recipe/2',id='fixture-failed-grid/2')
        recipe['training'].update(max_relative_p50=0,max_relative_p90=0)
        recipe['rules'][0]['candidates']=[dict(formula=f,options={},features=['items']) for f in ['constant/1','nonnegative-linear/1']]
        m['recipe']=save(self.root/'failed-grid-recipe.json',recipe)
        path=self.root/'failed-grid-manifest.json';save(path,m);out=self.root/'failed-grid'
        with self.assertRaisesRegex(ValueError,'prediction holdout accuracy gate'):
            execute('fit',path,out,allow_fixture=True)
        report=decode((out/'fit-report.json').read_bytes())
        self.assertFalse(report['passed']);self.assertEqual(len(report['rules']),3)
        self.assertEqual(len(report['rules'][0]['candidates']),2)
        self.assertFalse((out/'profile.json').exists())
        for candidate in report['rules'][0]['candidates']:
            for fold in candidate['folds']:
                self.assertFalse(set(fold['training_query_ids'])&set(fold['held_out_query_ids']))

    def test_hash_target_build_and_quality_report(self):
        for mutate in (lambda m:m['target'].update(hardware='other'), lambda m:m['recipe'].update(sha256='0'*64), lambda m:m['datasets'][0]['package'].update(sha256='0'*64), lambda m:m['datasets'][0]['structural_report'].update(sha256='0'*64)):
            m = copy.deepcopy(self.manifest); mutate(m); self.bad('fit', m)
        m = copy.deepcopy(self.manifest); self.rewrite_run(m['datasets'][0], 0, 'semantic_report', lambda r:r.update(independent_semantic_passed=False)); self.bad('fit', m)

    def test_budget_output_new_directory_and_fixture(self):
        for name, value in [('input_bytes', 100), ('samples', 1), ('stages', 1), ('output_bytes', 1)]:
            m = copy.deepcopy(self.manifest); m['budget'][name] = value; self.bad('fit', m)
        out, _ = self.run_action()
        with self.assertRaises(ValueError): execute('fit', self.root/'manifest.json', out, allow_fixture=True)
        with self.assertRaises(ValueError): execute('fit', self.root/'manifest.json', self.root/'no-fixture')

    def test_standalone_cli_relative_paths_and_failed_exit(self):
        m = copy.deepcopy(self.manifest)
        def relativize(v):
            if isinstance(v, dict):
                if set(v) == {'path', 'sha256'}: v['path'] = os.path.relpath(v['path'], self.root)
                else:
                    for child in v.values(): relativize(child)
            elif isinstance(v, list):
                for child in v: relativize(child)
        relativize(m); manifest = self.root/'relative.json'; save(manifest, m)
        env = dict(os.environ, PYTHONPATH=str(ROOT/'python'))
        args = [sys.executable, '-m', 'ndpsim.fit_cli', 'fit', '--manifest', str(manifest), '--output', str(self.root/'cli'), '--allow-fixture']
        run = subprocess.run(args, cwd='/tmp', env=env, capture_output=True, text=True); self.assertEqual(run.returncode, 0, run.stderr)
        failed = subprocess.run(args, cwd='/tmp', env=env, capture_output=True, text=True); self.assertEqual(failed.returncode, 2)

    def test_algorithms_constant_table_and_gbdt(self):
        rows = [([i % 8], 20+4*(i % 8)) for i in range(64)]
        for formula, options in [('constant/1', {}), ('upper-bound-table/1', {}), ('log-gbdt/1', dict(trees=20, depth=3, leaf=2, rate=.5))]:
            p = fit(formula, rows, options); validate_model(formula, p, [dict(minimum=0, maximum=7)])
            self.assertEqual(p, fit(formula, rows, options))
            for i in range(8): self.assertGreaterEqual(estimate(formula, p, [i]), 1)
        self.assertEqual(fit('constant/1', [([], 11), ([], 13)], {}), dict(ns=11))

    def test_export_each_formula_to_cpp(self):
        for formula in ('constant/1', 'upper-bound-table/1', 'log-gbdt/1'):
            root = self.root/formula.split('/')[0]; root.mkdir()
            manifest, recipe, datasets = fixture(root, formula)
            save(root/'manifest.json', manifest)
            execute('fit', root/'manifest.json', root/'fit', allow_fixture=True)
            profile = decode((root/'fit/profile.json').read_bytes()); r = profile['rules'][0]
            inputs = [dict(key=dict(query=0, op='a', stage='s0'), operator='example.work', operator_version='1', stage_kind='compute', context='resident/1', location='host', work=dict(flops=dict(value=3*i, unit='flop')), features=dict(items=dict(value=i, unit='count'))) for i in range(1, 9)]
            self.assertEqual(self.consume(profile, inputs)['ns'], [estimate(r['formula'], r['parameters'], [i]) for i in range(1, 9)])

    def test_truthful_low_signal_exclusion_and_table_rejection(self):
        table_manifest = self.table_manifest()
        m = copy.deepcopy(self.manifest)
        def low(record, index):
            row = record['samples'][index]
            row.update(end_cpu_ns=row['start_cpu_ns'], raw_ns=0, corrected_ns=-2, low_signal=True)
        self.rewrite_run(m['datasets'][0], 0, 'measurement', lambda r:low(r, 0))
        out, report = self.run_action('fit', m)
        self.assertEqual(report['rules'][0]['low_signal_excluded'], 1)
        m = table_manifest
        self.rewrite_run(m['datasets'][0], 0, 'measurement', lambda r:low(r, 1))
        self.bad('table', m)

    def test_accuracy_point_and_frozen_model_tampering(self):
        m = copy.deepcopy(self.manifest)
        recipe = copy.deepcopy(self.recipe); recipe['training_points'][0]['parameters']['width'] = 2
        m['recipe'] = save(self.root/'wrong-point-recipe.json', recipe); self.bad('fit', m)
        m = self.table_manifest()
        profile = decode(Path(m['frozen_profile']['path']).read_bytes())
        profile['rules'][0]['parameters']['intercept_ns'] += 5
        m['frozen_profile'] = save(self.root/'altered-profile.json', profile); self.bad('evaluate', m)
        m = copy.deepcopy(self.manifest)
        def noise(record):
            clock = 100
            for row in record['samples']:
                ns = (1 if row['key']['query'] % 2 else 10000) if row['stage_kind'] == 'compute' else row['corrected_ns']
                row.update(start_cpu_ns=clock, end_cpu_ns=clock+ns+2, raw_ns=ns+2, corrected_ns=ns, low_signal=False)
                clock += ns+12
        for index in range(3): self.rewrite_run(m['datasets'][0], index, 'measurement', noise)
        self.bad('fit', m)

    def test_bundled_schema_and_no_old_tools_import(self):
        self.assertEqual((ROOT/'schemas/ndpsim-cost-profile-v1.schema.json').read_bytes(), (ROOT/'python/ndpsim/schemas/ndpsim-cost-profile-v1.schema.json').read_bytes())
        for name in ('fitting.py', 'cost_models.py', 'fit_cli.py'):
            text = (ROOT/'python/ndpsim'/name).read_text()
            self.assertNotIn('tools.', text); self.assertNotIn('v04_', text); self.assertNotIn('graph.', text)


if __name__ == '__main__': unittest.main()
