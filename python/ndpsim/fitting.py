"""Independent Measurement/2 quality checks, four-fold fitting and measured tables.

Inputs are explicit hash-bound manifests. Business/Work verification is delegated
only to separately supplied, package-bound independent semantic reports.
"""
from collections import Counter, defaultdict
import math
from pathlib import Path
import re
import sqlite3
import sys
from statistics import median_low
import time

from .static_ir import (U64, FILES, _lines, _package_id, canonical, check_schema,
                        decode, digest_bytes, file_hash, require)
from .cost_models import fit, estimate, metrics, profile_schema, schema_hash, validate_model


def fields(value, required, optional=()):
    require(type(value) is dict and set(required) <= set(value) <= set(required)|set(optional), 'missing/unknown fields: '+str(required))


def key(row): return row['key']['query'], row['key']['op'], row['key']['stage']


def rule_key(r): return tuple(r[k] for k in ('operator', 'operator_version', 'stage_kind', 'context', 'location'))


def uint(value, minimum=0):
    require(type(value) is int and minimum <= value <= U64, 'expected uint64')
    return value


def unique_ids(ids):
    require(type(ids) is list and ids and all(type(q) is int and 0 <= q <= U64 for q in ids) and len(set(ids)) == len(ids), 'query ID list')


def feature_values(stage, names):
    counters = dict(stage['work']); require(not set(counters)&set(stage['features']), 'ambiguous Work/feature')
    counters.update(stage['features'])
    require(all(n in counters for n in names), 'missing model feature')
    return [counters[n]['value'] for n in names]


def ranges(rows, names, units):
    require(rows, 'no model domain')
    values = [feature_values(r, names) for r in rows]
    return [dict(name=n, unit=units[n], minimum=min(v[i] for v in values), maximum=max(v[i] for v in values)) for i, n in enumerate(names)]


def in_range(values, domain):
    return all(r['minimum'] <= v <= r['maximum'] for v, r in zip(values, domain))


class Session:
    def __init__(self, manifest_path, output, allow_fixture):
        self.path = Path(manifest_path).absolute(); self.output = Path(output)
        self.loaded = {}; self.input_bytes = 0; self.deadline = time.monotonic()+3600
        self.byte_limit = 512*1024**2; self.sample_limit = 1000000; self.stage_limit = 1000000
        self.manifest = self.load(self.path)
        m = self.manifest
        fields(m, ('format', 'fixture', 'recipe', 'target', 'partitions', 'datasets', 'budget'), ('frozen_profile', 'frozen_report'))
        require(m['format'] == 'ndpsim-fit-manifest/1' and type(m['fixture']) is bool, 'fit manifest version/fixture')
        require(not m['fixture'] or allow_fixture, 'fixture requires explicit permission')
        self.fixture = m['fixture']
        b = m['budget']; fields(b, ('input_bytes', 'samples', 'stages', 'output_bytes', 'timeout_seconds'))
        for v in b.values(): uint(v, 1)
        self.byte_limit = b['input_bytes']; self.sample_limit = b['samples']; self.stage_limit = b['stages']
        self.deadline = time.monotonic()+b['timeout_seconds']; self.output_limit = min(b['output_bytes'], 64*1024**2)
        check_schema(m['target'], 'target')
        fields(m['partitions'], ('tuning', 'evaluation'))
        for ids in m['partitions'].values(): unique_ids(ids)
        require(not set(m['partitions']['tuning'])&set(m['partitions']['evaluation']), 'training/evaluation ID overlap')
        self.recipe = self.ref(m['recipe']); self.recipe_hash = m['recipe']['sha256']
        self.specs, self.rules = self.check_recipe(self.recipe)
        require(type(m['datasets']) is list and m['datasets'], 'no datasets')
        self.db = sqlite3.connect(str(output/'stages.sqlite'))
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('CREATE TABLE stages(q TEXT,op TEXT,st TEXT,rank INTEGER,body BLOB,PRIMARY KEY(q,op,st))')
        self.run_ids = set(); self.measurement_hashes = set(); self.total_samples = 0
        self.quality = []; self.sources = []; self.metadata = defaultdict(list); self.samples = defaultdict(list)
        self.common_build = None; self.total_stages = 0

    def tick(self):
        require(time.monotonic() <= self.deadline, 'fit timeout budget')
        require(self.input_bytes <= self.byte_limit, 'input byte budget')

    def load(self, path):
        self.tick(); path = Path(path)
        require(path.is_file() and not path.is_symlink(), 'invalid metadata file: '+str(path))
        size = path.stat().st_size; require(size <= self.byte_limit-self.input_bytes, 'input byte budget')
        raw = path.read_bytes(); self.input_bytes += len(raw)
        self.loaded[str(path.absolute())] = digest_bytes(raw)
        return decode(raw)

    def ref(self, reference, base=None):
        fields(reference, ('path', 'sha256'))
        require(type(reference['sha256']) is str and re.fullmatch('[0-9a-f]{64}', reference['sha256']), 'reference SHA256')
        require(type(reference['path']) is str and reference['path'], 'reference path')
        path = (base or self.path.parent)/reference['path']
        result = self.load(path)
        require(self.loaded[str(path.absolute())] == reference['sha256'], 'reference hash mismatch: '+str(path))
        return result

    def check_recipe(self, recipe):
        fields(recipe, ('format', 'id', 'operators', 'rules', 'training', 'sampling_audits', 'training_points', 'formal_partitions', 'provenance_splits'))
        require(recipe['format'] == 'ndpsim-fit-recipe/1' and type(recipe['id']) is str and recipe['id'], 'recipe version/id')
        specs = {}; rules = {}
        require(type(recipe['operators']) is list and recipe['operators'], 'operator specs')
        for op in recipe['operators']:
            check_schema(op, 'operator_spec')
            for s in op['stages']:
                k = (op['id'], op['version'], s['kind'])
                require(k not in specs and not set(s['work_units'])&set(s['feature_units']), 'duplicate/ambiguous stage spec')
                specs[k] = (op, s)
        for r in recipe['rules']:
            fields(r, ('operator', 'operator_version', 'stage_kind', 'context', 'location', 'source', 'features', 'formula', 'options'))
            k = rule_key(r); require(k not in rules and k[:3] in specs, 'duplicate/unknown recipe stage')
            op, s = specs[k[:3]]
            require(r['context'] in s['contexts'] and r['location'] in op['locations'], 'recipe capability')
            require(r['source'] in ('prediction', 'measured-table', 'declared-zero'), 'recipe source')
            all_units = dict(s['work_units'], **s['feature_units'])
            require(type(r['features']) is list and len(set(r['features'])) == len(r['features']) and set(s['feature_units']) <= set(r['features']) <= set(all_units), 'recipe feature coverage')
            require(type(r['options']) is dict, 'formula options')
            if r['source'] == 'prediction':
                require(r['formula'] in ('constant/1', 'nonnegative-linear/1', 'upper-bound-table/1', 'log-gbdt/1'), 'unsupported fitter')
                require(r['formula'] == 'log-gbdt/1' or not r['options'], 'unknown fit options')
            else: require(r['formula'] is None and not r['options'], 'table/zero formula options')
            rules[k] = r
        require(rules, 'empty recipe')
        t = recipe['training']
        fields(t, ('folds', 'strides', 'min_runs', 'max_low_signal_fraction', 'min_holdout_fraction', 'max_relative_p50', 'max_relative_p90'))
        require(type(t['folds']) is int and t['folds'] == 4 and type(t['strides']) is list and t['strides'], 'four-fold/stride rule')
        for v in t['strides']: uint(v, 1)
        uint(t['min_runs'], 1)
        for n in ('max_low_signal_fraction', 'min_holdout_fraction', 'max_relative_p50', 'max_relative_p90'):
            require(type(t[n]) in (int, float) and math.isfinite(t[n]) and 0 <= t[n] <= 1, 'quality threshold')
        require(t['min_holdout_fraction'] > 0, 'empty holdout permission')
        for audit in recipe['sampling_audits']:
            fields(audit, ('stage_kinds',)); require(type(audit['stage_kinds']) is list and len(audit['stage_kinds']) > 1 and len(set(audit['stage_kinds'])) == len(audit['stage_kinds']), 'audit stages')
        fields(recipe['provenance_splits'], ('tuning', 'evaluation'))
        require(all(type(v) is str and v for v in recipe['provenance_splits'].values()), 'provenance split declarations')
        require(type(recipe['training_points']) is list, 'training point list')
        point_ids = set()
        for point in recipe['training_points']:
            fields(point, ('id', 'stride', 'parameters'))
            require(type(point['id']) is str and point['id'] and point['id'] not in point_ids and type(point['parameters']) is dict, 'training point declaration')
            point_ids.add(point['id']); uint(point['stride'], 1)
            require(point['stride'] in t['strides'], 'training point stride')
        if recipe['formal_partitions'] is not None:
            fields(recipe['formal_partitions'], ('tuning', 'evaluation'))
            for ids in recipe['formal_partitions'].values(): unique_ids(ids)
        if not self.fixture:
            require(recipe['formal_partitions'] == self.manifest['partitions'], 'formal partition freeze')
        return specs, rules

    def package(self, dataset):
        fields(dataset, ('split', 'point', 'package', 'structural_report', 'semantic_config', 'runs'))
        require(dataset['split'] in ('tuning', 'evaluation') and type(dataset['point']) is str, 'dataset split/point')
        ref = dataset['package']; fields(ref, ('path', 'sha256'))
        p = self.path.parent/ref['path']; require(p.is_dir() and not p.is_symlink() and {v.name for v in p.iterdir()} == FILES, 'package file set')
        manifest = self.load(p/'manifest.json'); check_schema(manifest)
        for f in manifest['files']:
            require(f['path'] in ('operations.jsonl', 'index.jsonl'), 'package path')
            path = p/f['path']; require(not path.is_symlink() and path.stat().st_size == f['bytes'], 'package bytes/path')
            self.input_bytes += path.stat().st_size; self.tick()
            require(file_hash(path) == f['sha256'], 'package file hash')
            self.loaded[str(path.absolute())] = f['sha256']
        require({f['path'] for f in manifest['files']} == {'operations.jsonl', 'index.jsonl'} and len(manifest['files']) == 2, 'package file coverage')
        pkg = _package_id(file_hash(p/'manifest.json'), file_hash(p/'index.jsonl'), file_hash(p/'operations.jsonl'))
        tail = self.load(p/'tail.json'); check_schema(tail, 'tail')
        require(tail['package_sha256'] == pkg == ref['sha256'] and tail['counts'] == manifest['counts'] and tail['terminals'] == manifest['terminals'], 'package tail/identity')
        report = self.ref(dataset['structural_report'])
        require(report.get('structural_passed') is True and report.get('package_sha256') == pkg and report.get('counts') == manifest['counts'], 'structural report binding')
        provenance = manifest['provenance']
        semantic_config = self.ref(dataset['semantic_config'])
        require(digest_bytes(canonical(semantic_config)) == provenance['semantic_config_sha256'], 'semantic configuration identity')
        if self.action == 'fit':
            points = [p for p in self.recipe['training_points'] if p['id'] == dataset['point']]
            require(len(points) == 1, 'unknown training point')
            self.point_stride = points[0]['stride']
            for field, expected in points[0]['parameters'].items():
                value = semantic_config
                for name in field.split('.'):
                    require(type(value) is dict and name in value, 'missing semantic point parameter'); value = value[name]
                require(value == expected and type(value) is type(expected), 'semantic training point mismatch')
        ids = provenance['query_ids']; unique_ids(ids)
        require(ids == self.manifest['partitions'][dataset['split']], 'split original query ID/order mismatch')
        require((provenance['kind'] == 'synthetic-fixture') == self.fixture, 'capture fixture identity')
        if not self.fixture: require(provenance['split'] == self.recipe['provenance_splits'][dataset['split']], 'capture split identity')
        require(report.get('registry_sha256') == provenance['extension_sha256'], 'registry/report identity')
        build = {k: provenance[k] for k in ('capture_build', 'semantic_version', 'numeric_version', 'work_version', 'extension_sha256', 'workload')}
        require(self.common_build is None or build == self.common_build, 'measurement build/semantic identity changed')
        self.common_build = build
        self.db.execute('DELETE FROM stages'); count = 0; operations = 0
        for _, _, op in _lines(p/'operations.jsonl', 16*1024**2, self.byte_limit):
            self.tick(); check_schema(op, 'operation'); operations += 1
            require(op['query'] in ids, 'package query outside split')
            for s in op['stages']:
                count += 1; self.total_stages += 1; require(self.total_stages <= self.stage_limit, 'stage budget')
                if count % 256 == 0:
                    require(self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0] <= self.byte_limit, 'SQLite scratch budget')
                k = (op['operator'], op['operator_version'], s['kind'], s['context'], 'host')
                # Location is a recipe choice; package semantics have no placement.
                candidates = [r for rk, r in self.rules.items() if rk[:4] == k[:4]]
                require(len(candidates) == 1, 'stage has missing/ambiguous service owner')
                r = candidates[0]; spec = self.specs[k[:3]][1]
                for attr, units_name in (('work', 'work_units'), ('features', 'feature_units')):
                    require(set(s[attr]) == set(spec[units_name]) and all(v['unit'] == spec[units_name][n] for n, v in s[attr].items()), 'stage Work/features units')
                row = dict(key=dict(query=op['query'], op=op['id'], stage=s['id']), chunk=s['chunk'], segment=s['segment'], stage_kind=s['kind'], context=s['context'], work=s['work'], features=s['features'], rule=list(rule_key(r)))
                try: self.db.execute('INSERT INTO stages VALUES(?,?,?,?,?)', (str(op['query']), op['id'], s['id'], s['topological_rank'], canonical(row)))
                except sqlite3.IntegrityError as e: raise ValueError('duplicate stable stage key') from e
                if r['source'] == 'declared-zero': self.metadata[rule_key(r)].append(row)
        require(count == manifest['counts']['stages'] and operations == manifest['counts']['operations'], 'package stage/operation counts')
        return pkg, provenance

    def measurement(self, run, package, provenance, action):
        fields(run, ('run_id', 'measurement', 'reference', 'semantic_report', 'status'))
        require(type(run['run_id']) is str and run['run_id'] and run['run_id'] not in self.run_ids, 'reused independent run ID')
        self.run_ids.add(run['run_id'])
        m = self.ref(run['measurement']); check_schema(m, 'measurement'); h = run['measurement']['sha256']
        require(h not in self.measurement_hashes, 'reused Measurement artifact'); self.measurement_hashes.add(h)
        reference = self.ref(run['reference']); semantic = self.ref(run['semantic_report']); status = self.ref(run['status'])
        require(status.get('passed') is True and status.get('mode') == 'sample', 'sample run status')
        for name in ('measurement', 'reference', 'semantic_report'):
            artifact = run[name]; relative = str(Path(artifact['path']).relative_to(Path(run['status']['path']).parent))
            require(status.get('files_sha256', {}).get(relative) == artifact['sha256'], 'run evidence artifact binding')
        require(m['reference_report_sha256'] == run['reference']['sha256'] == semantic.get('reference_report_sha256'), 'reference report identity')
        require(semantic.get('independent_semantic_passed') is True and semantic.get('structural_passed') is True and semantic.get('package_sha256') == package and semantic.get('provenance') == provenance and semantic.get('query_ids') == provenance['query_ids'], 'independent semantic/Work report binding')
        for name in ('major_faults', 'completed', 'workers', 'concurrency', 'cpu_resolution_ns', 'empty_probe_ns', 'empty_probe_count'):
            uint(reference.get(name))
        binding = m['binding']
        require(binding['package_sha256'] == package and binding['provenance'] == provenance and binding['target'] == self.manifest['target'], 'measurement binding/target')
        require(m['mode'] == 'cpu_sample' and reference.get('cpu_clock') == m['clock']['source'] == 'CLOCK_THREAD_CPUTIME_ID', 'unsupported measurement mode/clock')
        require(reference.get('paging_eligible') is True and reference.get('major_faults') == 0 and reference.get('completed') == len(provenance['query_ids']), 'native residency/coverage quality')
        require(reference.get('workers') == binding['workers'] == binding['slots'] == 1 and reference.get('concurrency') == binding['admission'] == 1, 'unsupported CPU quota')
        require(reference.get('cpu_resolution_ns') == m['clock']['resolution_ns'] and reference.get('empty_probe_ns') == m['clock']['empty_probe_ns'] and reference.get('empty_probe_count') == m['clock']['empty_probe_count'], 'clock protocol mismatch')
        queries = reference.get('queries')
        require(type(queries) is list, 'reference queries')
        unique_ids([q.get('original_query_id') for q in queries])
        require([q['original_query_id'] for q in queries] == provenance['query_ids'], 'reference query coverage/order')
        windows = {}
        for q in queries:
            start, duration = uint(q.get('cpu_start_ns')), uint(q.get('thread_cpu_ns'))
            require(start+duration <= U64, 'reference CPU window overflow')
            windows[q['original_query_id']] = (start, start+duration)
        plan = m['sampling']; stride = plan['stride']
        require(stride in self.recipe['training']['strides'] and (action != 'table' or stride == 1) and (action != 'fit' or stride == self.point_stride), 'sampling stride policy')
        self.total_samples += len(m['samples']); require(self.total_samples <= self.sample_limit, 'sample budget')
        expected = {}; counters = Counter(); eligible = 0
        def ordered_stages():
            for q in provenance['query_ids']:
                for body, in self.db.execute('SELECT body FROM stages WHERE q=? ORDER BY rank', (str(q),)):
                    yield decode(body)
        for row in ordered_stages():
            r = self.rules[tuple(row['rule'])]
            require(row['context'] == binding['context'], 'measurement preparation context mismatch')
            if r['source'] == 'declared-zero': continue
            eligible += 1; k = (row['key']['query'], row['stage_kind']); i = counters[k]; counters[k] += 1
            if i % stride == 0:
                require(len(expected) < self.sample_limit, 'planned sample budget')
                expected[key(row)] = row
        require(plan == dict(stride=stride, eligible=eligible, planned=len(expected), observed=len(expected), lost=0) and len(m['samples']) == len(expected), 'sampling plan coverage')
        seen = set(); intervals = defaultdict(list); low = Counter(); totals = Counter(); chunks = defaultdict(dict)
        overhead = m['clock']['empty_probe_ns']//m['clock']['empty_probe_count']; resolution = m['clock']['resolution_ns']
        for s in m['samples']:
            self.tick(); k = key(s); require(k in expected and k not in seen, 'missing/duplicate/unplanned sample'); seen.add(k)
            row = expected[k]
            require(all(s[n] == row[n] for n in ('key', 'chunk', 'segment', 'stage_kind', 'work', 'features')), 'sample key/Work/features mismatch')
            require(s['calls'] == 1 and s['end_cpu_ns'] >= s['start_cpu_ns'] > 0 and s['raw_ns'] == s['end_cpu_ns']-s['start_cpu_ns'], 'sample interval arithmetic')
            corrected = s['raw_ns']-overhead
            require(s['corrected_ns'] == corrected and s['low_signal'] == (corrected < resolution), 'corrected time/low-signal arithmetic')
            require(windows[k[0]][0] <= s['start_cpu_ns'] <= s['end_cpu_ns'] <= windows[k[0]][1], 'sample outside native query CPU window')
            intervals[k[0]].append((s['start_cpu_ns'], s['end_cpu_ns']))
            rk = tuple(row['rule']); totals[rk] += 1; low[rk] += s['low_signal']
            if action == 'table' and self.rules[rk]['source'] == 'measured-table': require(not s['low_signal'], 'low-signal measured call')
            sample = dict(row, ns=corrected, low_signal=s['low_signal'], measurement_sha256=h, run_id=run['run_id'], binding=binding)
            self.samples[rk].append(sample)
            chunks[(k[0], k[1], s['chunk'])][s['stage_kind']] = s
        values = sorted(v for group in intervals.values() for v in group)
        require(all(a[1] <= b[0] for a, b in zip(values, values[1:])), 'overlapping exclusive CPU buckets')
        for audit in self.recipe['sampling_audits']:
            kinds = audit['stage_kinds']
            for group in chunks.values():
                if not set(kinds)&set(group): continue
                require(all(kind in group for kind in kinds), 'audit stage coverage')
                require(all(group[a]['end_cpu_ns'] == group[b]['start_cpu_ns'] for a, b in zip(kinds, kinds[1:])), 'audit interval gap/overlap')
        for rk in totals:
            require(low[rk]/totals[rk] <= self.recipe['training']['max_low_signal_fraction'], 'low-signal fraction quality')
        self.sources.append(dict(run_id=run['run_id'], measurement_sha256=h, binding=binding))
        self.quality.append(dict(run_id=run['run_id'], samples=len(seen), eligible=eligible, low_signal=sum(low.values()), coverage_passed=True, exclusive_buckets_passed=True, audit_groups_passed=True, by_stage=[dict(key=list(rk), samples=totals[rk], low_signal=low[rk]) for rk in sorted(totals)], low_signal_policy={'prediction': 'exclude-with-fraction-gate', 'table': 'reject-every-low-call', 'aggregation': 'none'}, scope='fixture' if self.fixture else 'measurement-quality-only'))

    def collect(self, action):
        m = self.manifest; seen_points = set(); bindings = []; self.action = action
        if action == 'fit': require(all(d.get('split') == 'tuning' for d in m['datasets']), 'evaluation forbidden in prediction fitting')
        if action == 'evaluate': require(all(d.get('split') == 'evaluation' for d in m['datasets']), 'evaluation action requires evaluation split')
        for dataset in m['datasets']:
            self.tick(); package, provenance = self.package(dataset); seen_points.add(dataset['point'])
            require(type(dataset['runs']) is list and len(dataset['runs']) >= (3 if action == 'table' else self.recipe['training']['min_runs']), 'insufficient independent measurement runs')
            for run in dataset['runs']: self.measurement(run, package, provenance, action)
            bindings.append(package)
        if action == 'fit' and self.recipe['training_points']: require(seen_points == {p['id'] for p in self.recipe['training_points']} and len(bindings) == len(set(bindings)) == len(seen_points), 'training point/package coverage')
        if action == 'table': require(len(set(bindings)) == 1, 'table binds exactly one package')
        require(set(self.samples)|set(self.metadata) == set(self.rules), 'recipe stage coverage')

    def fit_profile(self):
        tuning = self.manifest['partitions']['tuning']; require(len(tuning) >= 4, 'at least four training query groups')
        folds = {q: i % 4 for i, q in enumerate(tuning)}; output = []; reports = []
        hashes = sorted(self.measurement_hashes)
        for rk, r in self.rules.items():
            if r['source'] == 'measured-table':
                reports.append(dict(key=list(rk), source='measured-table', prediction_error='not-applicable')); continue
            spec = self.specs[rk[:3]][1]; units = dict(spec['work_units'], **spec['feature_units'])
            if r['source'] == 'declared-zero':
                domain = ranges(self.metadata[rk], r['features'], units); params = dict(ns=0); formula = 'constant/1'
                report = dict(key=list(rk), source='declared-zero', prediction_error='not-applicable')
            else:
                all_rows = self.samples[rk]; rows = [s for s in all_rows if not s['low_signal']]
                require(rows and all(s['ns'] > 0 for s in rows), 'no usable training signal')
                formula = r['formula']; domain = ranges(rows, r['features'], units)
                held = []; fold_reports = []; count = 0
                for fold in range(4):
                    train = [s for s in rows if folds[s['key']['query']] != fold]; test = [s for s in rows if folds[s['key']['query']] == fold]
                    require(train and test, 'empty training/holdout query fold')
                    d = ranges(train, r['features'], units)
                    p = fit(formula, [(feature_values(s, r['features']), s['ns']) for s in train], r['options'], self.tick); validate_model(formula, p, d)
                    usable = [s for s in test if in_range(feature_values(s, r['features']), d)]
                    count += len(test)
                    held.extend((s['ns'], estimate(formula, p, feature_values(s, r['features']))) for s in usable)
                    fold_reports.append(dict(fold=fold, training_query_ids=[q for q in tuning if folds[q] != fold], held_out_query_ids=[q for q in tuning if folds[q] == fold], training_samples=len(train), feature_ranges=d, test_samples=len(test), in_range=len(usable), out_of_range=len(test)-len(usable)))
                require(len(held)/count >= self.recipe['training']['min_holdout_fraction'], 'insufficient in-domain grouped holdout')
                score = metrics(held)
                require(score['relative_p50'] <= self.recipe['training']['max_relative_p50'] and score['relative_p90'] <= self.recipe['training']['max_relative_p90'], 'prediction holdout accuracy gate')
                params = fit(formula, [(feature_values(s, r['features']), s['ns']) for s in rows], r['options'], self.tick)
                report = dict(key=list(rk), source='prediction', folds=fold_reports, holdout=score, training=metrics([(s['ns'], estimate(formula, params, feature_values(s, r['features']))) for s in rows]), low_signal_excluded=len(all_rows)-len(rows), prediction_quality_passed=True)
            validate_model(formula, params, domain)
            rule = {k: r[k] for k in ('operator', 'operator_version', 'stage_kind', 'context', 'location')}
            rule.update(target_id=self.manifest['target']['id'], work_units=spec['work_units'], feature_ranges=domain, source='prediction', formula=formula, parameters=params, parameter_schema_sha256=schema_hash(formula), measurement_sha256=hashes, training_query_ids=tuning)
            report['model'] = decode(canonical(rule))
            output.append(rule); reports.append(report)
        report = self.report('fit', reports)
        h = digest_bytes(canonical(report))
        for r in output: r['fit_report_sha256'] = h
        require(output, 'recipe has no predicted/declared-zero models')
        return dict(format='ndpsim-cost-profile/1', fixture=self.fixture, target=self.manifest['target'], rules=output), report

    def report(self, action, rules):
        return dict(format='ndpsim-fit-report/1', action=action, passed=True, fixture=self.fixture, recipe_sha256=self.recipe_hash, partitions=self.manifest['partitions'], target=self.manifest['target'], sources=self.sources, quality=self.quality, rules=rules, inputs_sha256=sorted(set(self.loaded.values())), python=dict(version=sys.version, platform=sys.platform, executable_sha256=file_hash(sys.executable)), implementation_sha256={n: file_hash(Path(__file__).with_name(n)) for n in ('fitting.py', 'cost_models.py', 'fit_cli.py')}, full_acceptance_passed=False)

    def frozen(self):
        require('frozen_profile' in self.manifest, 'frozen prediction Profile required')
        p = self.ref(self.manifest['frozen_profile']); profile_schema(p)
        require('frozen_report' in self.manifest, 'frozen fit report required')
        report = self.ref(self.manifest['frozen_report'])
        require(report.get('passed') is True and report.get('action') == 'fit' and report.get('recipe_sha256') == self.recipe_hash and report.get('partitions') == self.manifest['partitions'] and report.get('target') == self.manifest['target'], 'frozen fit report identity')
        require(all(r['fit_report_sha256'] == self.manifest['frozen_report']['sha256'] for r in p['rules']), 'frozen Profile/report hash binding')
        require(report.get('sources') and all(source['binding']['provenance']['query_ids'] == self.manifest['partitions']['tuning'] for source in report['sources']), 'frozen training identity')
        if self.common_build is not None:
            require(all({k: source['binding']['provenance'][k] for k in self.common_build} == self.common_build for source in report['sources']), 'frozen measurement build identity')
        require(p['target'] == self.manifest['target'] and p['fixture'] == self.fixture and all(r['source'] == 'prediction' and r['training_query_ids'] == self.manifest['partitions']['tuning'] for r in p['rules']), 'frozen Profile target/training/source mismatch')
        require({rule_key(r) for r in p['rules']} == {k for k, r in self.rules.items() if r['source'] != 'measured-table'}, 'frozen Profile recipe coverage')
        reported = {rule_key(row['model']): row['model'] for row in report['rules'] if 'model' in row}
        require(len(reported) == len(p['rules']), 'frozen report model coverage')
        for r in p['rules']:
            require({k: v for k, v in r.items() if k != 'fit_report_sha256'} == reported.get(rule_key(r)), 'frozen Profile model/report mismatch')
            require(r['parameter_schema_sha256'] == schema_hash(r['formula']), 'frozen formula schema identity')
            validate_model(r['formula'], r['parameters'], r['feature_ranges'])
        return p

    def table_profile(self):
        frozen = self.frozen(); output = []; reports = []
        for rk, r in self.rules.items():
            if r['source'] != 'measured-table': continue
            rows = self.samples[rk]; require(rows, 'no measured table rows')
            binding = rows[0]['binding']; require(all(s['binding'] == binding for s in rows), 'table binding mismatch')
            grouped = defaultdict(list)
            for s in rows: grouped[key(s)].append(s)
            results = []
            for k, calls in sorted(grouped.items()):
                require(len({s['run_id'] for s in calls}) == len(self.run_ids) >= 3 and len(calls) == len(self.run_ids), 'table missing independent call')
                require(all(s['work'] == calls[0]['work'] and s['features'] == calls[0]['features'] and not s['low_signal'] for s in calls), 'table Work/features/quality mismatch')
                results.append(dict(key=calls[0]['key'], work=calls[0]['work'], features=calls[0]['features'], duration_ns=median_low(s['ns'] for s in calls)))
            spec = self.specs[rk[:3]][1]; units = dict(spec['work_units'], **spec['feature_units'])
            rule = {k: r[k] for k in ('operator', 'operator_version', 'stage_kind', 'context', 'location')}
            rule.update(target_id=self.manifest['target']['id'], work_units=spec['work_units'], feature_ranges=ranges(rows, r['features'], units), source='measured-table', binding=binding, measurement_sha256=sorted(self.measurement_hashes), rows=results)
            output.append(rule); reports.append(dict(key=list(rk), source='measured-table', calls=len(results), independent_runs=len(self.run_ids), quality_passed=True, prediction_error='not-applicable'))
        require(output, 'recipe has no measured stage')
        report = self.report('table', reports); h = digest_bytes(canonical(report))
        for r in output: r['quality_report_sha256'] = h
        frozen['rules'].extend(output)
        return frozen, report

    def evaluate(self):
        profile = self.frozen(); rules = {rule_key(r): r for r in profile['rules']}; reports = []
        for rk, recipe in self.rules.items():
            if recipe['source'] != 'prediction':
                reports.append(dict(key=list(rk), source=recipe['source'], prediction_error='not-applicable')); continue
            r = rules[rk]; predictions = []; outside = 0
            for s in self.samples[rk]:
                if s['low_signal']: continue
                v = feature_values(s, [f['name'] for f in r['feature_ranges']])
                if not in_range(v, r['feature_ranges']): outside += 1; continue
                predictions.append((s['ns'], estimate(r['formula'], r['parameters'], v)))
            require(outside == 0, 'evaluation feature out of frozen domain')
            score = metrics(predictions)
            passed = score['relative_p50'] <= self.recipe['training']['max_relative_p50'] and score['relative_p90'] <= self.recipe['training']['max_relative_p90']
            reports.append(dict(key=list(rk), source='prediction', metrics=score, prediction_quality_passed=passed))
        report = self.report('evaluate', reports)
        report['passed'] = all(r.get('prediction_quality_passed', True) for r in reports)
        return report

    def close(self):
        self.db.close()
        (self.output/'stages.sqlite').unlink(missing_ok=True)


def execute(action, manifest_path, output, *, allow_fixture=False):
    require(action in ('fit', 'table', 'evaluate', 'check'), 'unknown fit action')
    output = Path(output); require(not output.exists(), 'output directory must be new'); output.mkdir(parents=True)
    session = None
    try:
        session = Session(manifest_path, output, allow_fixture)
        if action in ('table', 'evaluate'): session.frozen()
        session.collect(action)
        profile = None
        if action == 'fit': profile, report = session.fit_profile()
        elif action == 'table': profile, report = session.table_profile()
        elif action == 'evaluate': report = session.evaluate()
        else: report = session.report('check', [])
        if profile is not None: profile_schema(profile)
        # Inputs must remain immutable; references resolve against the manifest,
        # never against cwd. Save effective inputs and exact bytes, not payloads.
        for path, h in session.loaded.items(): require(file_hash(path) == h, 'input changed during fitting')
        (output/'manifest-input.json').write_bytes(canonical(session.manifest))
        (output/'recipe.json').write_bytes(canonical(session.recipe))
        (output/'resolved-inputs.json').write_bytes(canonical(session.loaded))
        report_name = 'fit-report.json' if action == 'fit' else action+'-report.json'
        report_raw = canonical(report); require(len(report_raw) <= session.output_limit, 'report output budget')
        (output/report_name).write_bytes(report_raw)
        if profile is not None:
            raw = canonical(profile); require(len(raw) <= session.output_limit, 'Profile output budget')
            (output/'profile.json').write_bytes(raw)
        require(report['passed'], 'prediction evaluation accuracy gate')
        require(sum(p.stat().st_size for p in output.iterdir() if p.is_file() and p.suffix != '.sqlite') <= session.manifest['budget']['output_bytes'], 'total output budget')
        session.tick()
        status = dict(passed=True, action=action, full_acceptance_passed=False, artifacts_sha256={p.name: file_hash(p) for p in output.iterdir() if p.is_file() and p.suffix != '.sqlite'})
        (output/'status.json').write_bytes(canonical(status)); return report
    except BaseException as exc:
        profile = output/'profile.json'
        if profile.exists(): profile.rename(output/'profile.failed.json')
        (output/'status.json').write_bytes(canonical(dict(passed=False, action=action, error=str(exc), full_acceptance_passed=False)))
        raise
    finally:
        if session is not None: session.close()
