"""Independent evidence validators. No functional simulation or historical tools.

Reports prove their named layer only. Release aggregation belongs to P21.
"""
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from .execution import load, save, resolve
from .static_ir import canonical, decode, digest_bytes, file_hash, require, check_schema
from .cost_models import estimate, profile_schema, schema_hash, validate_model


def key(r):
    uint(r['query'])
    require(type(r['op']) is str and type(r['stage']) is str, 'stable key types')
    return (r['query'], r['op'], r['stage'])


def unique(rows, label, get=key):
    result = {}
    for r in rows:
        k = get(r)
        require(k not in result, 'duplicate '+label+': '+str(k))
        result[k] = r
    return result


def uint(n):
    require(type(n) is int and 0 <= n < 2**64, 'expected uint64')
    return n


class Inputs:
    def __init__(self, base, limits):
        self.base = base
        self.hashes = {}
        self.bytes = 0
        self.limit = limits['input_bytes']
        self.records = limits['records']
        require(set(limits) == {'input_bytes', 'records'} and all(type(x) is int and x > 0 for x in limits.values()), 'validation limits')

    def track(self, p):
        p = Path(p).resolve()
        if str(p) not in self.hashes:
            self.bytes += p.stat().st_size
            require(self.bytes <= self.limit, 'validation input budget')
            self.hashes[str(p)] = file_hash(p)
        return p

    def ref(self, r):
        require(set(r) == {'path', 'sha256'}, 'hash-bound artifact reference required')
        p = self.track(resolve(self.base, r['path']))
        require(self.hashes[str(p)] == r['sha256'], 'artifact hash mismatch: '+str(p))
        return p

    def lines(self, p):
        p = self.track(p)
        with p.open('rb') as f:
            for n, line in enumerate(iter(lambda: f.readline(16*1024**2+1), b''), 1):
                require(n <= self.records and len(line) <= 16*1024**2, 'record budget')
                yield decode(line)

    def run(self, ref, native=False):
        p = self.ref(ref)
        require(p.name == 'status.json', 'run reference must name status.json')
        s = load(p)
        require(s.get('passed') is True and s.get('exit_code', 0) == 0, 'failed evidence run')
        if native:
            require(s.get('mode') == 'benchmark', 'independent unprobed benchmark required')
        else:
            require(s.get('command') == 'simulate', 'simulation run required')
        hashes = s.get('files_sha256' if native else 'artifacts_sha256', {})
        require(hashes, 'missing artifact inventory')
        # SQLite caches are derived scratch; all evidence and raw logs are checked.
        for name, h in hashes.items():
            q = Path(name)
            require(not q.is_absolute() and '..' not in q.parts, 'artifact path escape')
            if '.sqlite' not in name:
                self.ref(dict(path=str(p.parent/q), sha256=h))
        needed = ['reference.json', 'config.json', 'build.json', 'commands.json'] if native else ['report.json', 'trace.jsonl', 'effective-config.json', 'input-identities.json', 'command.json']
        require(all(n in hashes for n in needed), 'missing run evidence')
        return p.parent

    def unchanged(self):
        require(all(file_hash(p) == h for p, h in self.hashes.items()), 'evidence changed during validation')


def package(inputs, directory, expected):
    """Recheck byte/index/tail binding; semantic/size rules remain capture-side."""
    directory = resolve(inputs.base, directory)
    files = {n: inputs.track(directory/n) for n in ('manifest.json', 'index.jsonl', 'operations.jsonl', 'tail.json')}
    m, t = load(files['manifest.json']), load(files['tail.json'])
    check_schema(m); check_schema(t, 'tail')
    h = digest_bytes(b'ndpsim-static-ir/2\0'+b''.join(bytes.fromhex(file_hash(files[n])) for n in ('manifest.json', 'index.jsonl', 'operations.jsonl')))
    require(m['format'] == 'ndpsim-static-ir/2' and h == expected == t['package_sha256'], 'package identity/version')
    require(t['complete'] is True and t['lost_records'] == 0 and t['counts'] == m['counts'] and t['terminals'] == m['terminals'], 'incomplete package')
    for row in m['files']:
        require(row['path'] in ('operations.jsonl', 'index.jsonl'), 'package file')
        p = files[row['path']]
        require(row['bytes'] == p.stat().st_size and row['sha256'] == file_hash(p), 'package file hash')
    require({p.name for p in directory.iterdir()} == set(files), 'package file set')
    stages = {}; operations = 0
    for op in inputs.lines(files['operations.jsonl']):
        check_schema(op, 'operation'); operations += 1
        for st in op['stages']:
            k = (op['query'], op['id'], st['id'])
            require(k not in stages, 'duplicate package stage')
            stages[k] = dict(stage=st, operator=op['operator'], operator_version=op['operator_version'])
    require(operations == m['counts']['operations'] and len(stages) == m['counts']['stages'], 'stage/operation coverage')
    require(sum(len(v['stage']['dependencies']) for v in stages.values()) == m['counts']['dependencies'], 'dependency coverage')
    sources = set()
    for k,info in stages.items():
        uint(info['stage']['topological_rank'])
        for dep in info['stage']['dependencies']:
            source = key(dep['source']['key'])
            require(source in stages and source[0] == k[0] and stages[source]['stage']['topological_rank'] < info['stage']['topological_rank'], 'invalid dependency/cycle/rank')
            require(dep['kind'] in ('DATA','ADDRESS','CONTROL','ORDER'), 'unknown dependency kind')
            sources.add(source)
    require({key(t) for t in m['terminals']} == set(stages)-sources, 'terminal closure')
    require({k[0] for k in stages} == set(m['provenance']['query_ids']), 'package query coverage')
    indices = unique(inputs.lines(files['index.jsonl']), 'index', lambda r: key(r['key']))
    require(set(indices) == set(stages), 'index coverage')
    for k, v in stages.items():
        require(indices[k]['stage_sha256'] == digest_bytes(canonical(v['stage'])), 'stage index hash')
    return m, stages


def capture(spec, inputs, output):
    require(set(spec) == {'package', 'package_sha256', 'registry', 'semantic', 'reference'}, 'capture layer fields')
    m, stages = package(inputs, spec['package'], spec['package_sha256'])
    reg = load(inputs.ref(spec['registry']))
    require(digest_bytes(canonical(reg)) == m['provenance']['extension_sha256'], 'capture registry identity')
    receipt = load(inputs.ref(spec['semantic']))
    ref = load(inputs.ref(spec['reference']))
    require(receipt.get('independent_semantic_passed') is True and receipt.get('structural_passed') is True, 'independent full-query semantic evidence required')
    require(receipt.get('semantic_validation') == 'independent-full-query', 'partial semantic evidence')
    require(receipt['package_sha256'] == spec['package_sha256'] and receipt['provenance'] == m['provenance'], 'semantic identity')
    require(receipt['reference_report_sha256'] == digest_bytes(canonical(ref)), 'reference binding')
    actual_counts = dict(Counter(v['stage']['kind'] for v in stages.values()), **m['counts'])
    require(all(actual_counts.get(n) == v for n,v in receipt['counts'].items()), 'semantic full count coverage')
    require(receipt['query_ids'] == m['provenance']['query_ids'] and receipt['counts']['stages'] == len(stages) and receipt['counts']['operations'] == m['counts']['operations'], 'semantic coverage')
    require([r['original_query_id'] for r in ref['queries']] == receipt['query_ids'] and ref['completed'] == len(receipt['query_ids']), 'reference query coverage')
    return dict(package_sha256=spec['package_sha256'], provenance=m['provenance'], counts=m['counts'],
                objects=m['objects'], semantic_work=receipt['semantic_work'], reference_results='native capture evidence; never simulated results',
                trust='hash-bound independent capture oracle receipt; not authentication or a new payload recomputation')


def demand_ns(profile, c, m, info, k, work, features, location, cache):
    rules = [r for r in profile['rules'] if (r['operator'], r['operator_version'], r['stage_kind'], r['context'], r['location'], r['target_id']) ==
             (info['operator'], info['operator_version'], info['stage']['kind'], c['context'], location, c['target']['id'])]
    require(len(rules) == 1, 'missing/duplicate cost rule')
    r = rules[0]
    require({n:v['unit'] for n,v in work.items()} == r['work_units'], 'cost Work units')
    ranges = r['feature_ranges']
    require(set(features) <= {x['name'] for x in ranges} <= set(features)|set(work), 'cost feature coverage')
    all_features = dict(work, **features)
    values = []
    for f in ranges:
        v = all_features[f['name']]
        require(v['unit'] == f['unit'] and f['minimum'] <= uint(v['value']) <= f['maximum'], 'cost feature domain/unit')
        values.append(v['value'])
    if r['source'] == 'prediction':
        if id(r) not in cache:
            require(r['parameter_schema_sha256'] == schema_hash(r['formula']), 'formula schema identity')
            validate_model(r['formula'], r['parameters'], ranges)
            cache[id(r)] = True
        return estimate(r['formula'], r['parameters'], values), r['source']
    if location=='controller' and info['stage']['kind']=='score_l2':
        require(features['vectors'] == info['stage']['features']['vectors'], 'measured call cannot be divided')
    require(len(set(r['measurement_sha256'])) >= 3, 'table independent repeats')
    binding = dict(package_sha256=c['package_sha256'], provenance=m['provenance'], target=c['target'], context=c['context'], workers=c['workers'], slots=c['slots'], admission=c['admission'])
    require(r['binding'] == binding, 'table binding')
    if id(r) not in cache: cache[id(r)] = unique(r['rows'], 'table row', lambda x:key(x['key']))
    rows = cache[id(r)]
    require(k in rows and rows[k]['work'] == work and rows[k]['features'] == features, 'missing/mismatched measured call')
    return uint(rows[k]['duration_ns']), r['source']


def simulate(spec, inputs, output):
    require(set(spec) == {'run'}, 'simulate layer fields')
    directory = inputs.run(spec['run'])
    c = load(directory/'effective-config.json')
    report = load(directory/'report.json')
    require(report.get('execution_succeeded') is True, 'failed simulation report')
    require(load(directory/'command.json')['exit_code'] == 0, 'simulation command failed')
    # Resolve snapshots within the run directory so evidence is relocatable.
    m, expected = package(inputs, str(directory/'inputs/package'), c['package_sha256'])
    profile_path = directory/'inputs/profile.json'; reg_path = directory/'inputs/registry.json'
    require(file_hash(profile_path) == c['profile_sha256'] and file_hash(reg_path) == c['registry_sha256'], 'run input identity')
    profile = load(profile_path); profile_schema(profile)
    require(profile['target'] == c['target'], 'profile target')
    rows = list(inputs.lines(directory/'trace.jsonl'))
    require(rows and rows[0]['type'] == 'configuration' and rows[-1]['type'] == 'summary', 'trace boundaries')
    require(sum(r['type'] == 'configuration' for r in rows) == sum(r['type'] == 'summary' for r in rows) == 1, 'trace configuration/summary multiplicity')
    snapshot = rows[0]['snapshot']
    require(snapshot['config_sha256'] == file_hash(directory/'effective-config.json'), 'trace config hash')
    require(snapshot['config'] == c and snapshot['profile'] == profile and snapshot['registry'] == load(reg_path), 'trace input snapshot mismatch')
    summary = rows[-1]
    require(summary.get('success') is True, 'trace failed')
    for n in ('runtime_tasks', 'pins', 'buffer_used', 'sessions', 'active_stages', 'outstanding', 'resource_pending'):
        require(summary.get(n, 0) == 0, 'undrained '+n)
    stages = unique((r for r in rows if r['type'] == 'stage'), 'stage')
    requests = unique((r for r in rows if r['type'] == 'request'), 'request', lambda r:r['query'])
    require(set(stages) == set(expected), 'stage coverage')
    require(set(requests) == set(m['provenance']['query_ids']), 'request coverage')
    for k, v in expected.items():
        s, r = v['stage'], stages[k]
        require(r['success'] is True and uint(r['completed']) >= uint(r['submitted']), 'stage failed/time')
        for dep in s['dependencies']:
            require(r['submitted'] >= stages[key(dep['source']['key'])]['completed'], 'dependency released before completion')
        q = requests[k[0]]
        require(q['admitted'] <= r['submitted'] <= r['completed'] <= q['visible'], 'request/stage gate')
    for t in m['terminals']:
        require(requests[t['query']]['visible'] >= stages[key(t)]['completed'], 'terminal visibility gate')
    for q in requests.values():
        uint(q['query'])
        require(q['success'] is True and uint(q['admitted']) <= uint(q['visible']) <= uint(q['drained']) <= uint(summary['drained']), 'request failed/time')
    ssd = c['format'] == 'ndpsim-graph-ssd-run/1'
    if ssd:
        from .graph_validation import check as graph_check
        calls, ledger = graph_check(rows, expected, c, directory, stages)
    else:
        require(c['format'] == 'ndpsim-memory-run/1', 'unknown simulation format')
        require(summary['ssd_requests'] == summary['device_instances'] == 0, 'resident SSD service')
        demands = unique((r for r in rows if r['type'] == 'demand'), 'demand')
        services = unique((r for r in rows if r['type'] == 'service'), 'service', lambda r:key(decode(r['key'].encode())))
        require(set(demands) == set(services) == set(expected), 'unique service/demand coverage')
        calls = [(k, demands[k], services[k], None) for k in expected]
        ledger = dict(ssd_requests=0)
    totals = Counter(); source_counts = Counter(); per_stage = []
    intervals = defaultdict(list); cost_cache = {}
    for k, d, service, physical in calls:
        info = expected[k]; st = info['stage']
        work, features = st['work'], st['features']
        if ssd:
            from .graph_validation import effective
            work, features = effective(info, c, physical)
        require(canonical(d['work']) == canonical(work) and canonical(d['features']) == canonical(features) and d['operator'] == info['operator'] and d['stage_kind'] == st['kind'], 'demand Work/features/identity mismatch')
        location = 'controller' if physical is not None else 'host'
        ns, source = demand_ns(profile, c, m, info, k, work, features, location, cost_cache)
        uint(d['duration_ns']); uint(service['duration_ns'])
        require(d['duration_ns'] == ns and d['source'] == source and d['location'] == location, 'cost service mismatch')
        require(uint(service['submitted']) <= uint(service['start']) <= uint(service['end']) and service['end']-service['start'] == ns == service['duration_ns'], 'service time/demand mismatch')
        require(stages[k]['submitted'] <= service['submitted'] and service['end'] <= stages[k]['completed'], 'service visibility gate')
        intervals[location].extend([(service['start'], 1), (service['end'], -1)] if ns else [])
        totals[location] += ns; source_counts[source] += 1
        per_stage.append(dict(query=k[0], op=k[1], stage=k[2], physical_items=physical, source=source, location=location, duration_ns=ns))
    for location, events in intervals.items():
        used = peak = 0
        for _, delta in sorted(events): used += delta; peak = max(peak, used)
        capacity = c['slots'] if location == 'host' else c['resources']['compute_slots']
        require(used == 0 and peak <= capacity, 'compute slots exceeded')
    if not ssd: require(summary['cpu_demand_ns'] == totals['host'], 'CPU total mismatch')
    require(report['package_sha256'] == c['package_sha256'] and report['profile_sha256'] == c['profile_sha256'] and report['target_hardware'] == c['target'] and report['context'] == c['context'], 'report identity mismatch')
    latency = {name:quantile([q['visible']-q['admitted'] for q in requests.values()],p) for name,p in [('p50',.5),('p95',.95),('p99',.99)]}
    window = max(q['visible'] for q in requests.values())-min(q['admitted'] for q in requests.values())
    require(report['latency_ns'] == latency and report['measurement_window_ns'] == window, 'report latency/window definition')
    require(report['requests'] == len(requests) and report['drain'] == summary and report['service_demand_ns_by_location'] == dict(totals) and report['service_sources'] == dict(source_counts), 'report totals mismatch')
    save(output/'stage-demands.json', per_stage)
    query_rows = [dict(query=q['query'], latency_ns=q['visible']-q['admitted'], offered_latency_ns=q['visible']-q.get('offered',0), admission_ns=q['admitted'], visible_ns=q['visible']) for q in requests.values()]
    save(output/'queries.json', query_rows)
    # Resource and admission quotas are separate from compute slots.
    for begin, end, cap in [('admitted', 'visible', c['admission']),('worker_start','visible',c['workers'])]:
        events = [(q.get(begin,q['admitted']),1) for q in requests.values() if q[end]>q.get(begin,q['admitted'])]+[(q[end],-1) for q in requests.values() if q[end]>q.get(begin,q['admitted'])]
        used = 0
        for _, delta in sorted(events): used += delta; require(used <= cap, 'admission quota exceeded')
    return dict(package_sha256=c['package_sha256'], profile_sha256=c['profile_sha256'], provenance=m['provenance'], target=c['target'], context=c['context'],
                objects=m['objects'], configuration={n:c[n] for n in ('format','workers','slots','admission')}, ledger=ledger, service_sources=dict(source_counts), service_demand_ns_by_location=dict(totals),
                queries=query_rows, measurement_window_ns=window,
                deterministic_sha256=digest_bytes(canonical(rows[1:])), reference_results='external capture evidence only')


def error(predicted, actual):
    require(actual > 0 and math.isfinite(actual) and math.isfinite(predicted), 'nonpositive/nonfinite comparison denominator')
    return abs(predicted-actual)/actual


def quantile(values, p):
    require(values, 'empty quantile')
    return sorted(values)[math.ceil(p*len(values))-1]


def graph_benchmark_identity(cfg, provenance):
    from hashlib import sha256
    # Same canonical semantic identity as capture, without reopening payload.
    require(digest_bytes(canonical(dict(shape=cfg['shape'], search=cfg['search']))) == provenance['semantic_config_sha256'], 'benchmark semantic config')
    require(sha256(''.join(cfg['files'][n]['sha256'] for n in ('vectors','queries','graph','query_ids')).encode()).hexdigest() == provenance['input_content_sha256'], 'benchmark input identity')

def accuracy(spec, inputs, output):
    require(set(spec) == {'native', 'simulated', 'capture', 'stage_evaluation'}, 'accuracy layer fields')
    require(len(spec['native']) >= 3 and len(spec['simulated']) >= 2, 'requires three native/two simulated repeats')
    seen_paths = set(); seen_artifacts = set(); native = []; simulated = []
    for ref in spec['simulated']:
        p = inputs.ref(ref)
        require(p not in seen_paths, 'duplicate simulated run'); seen_paths.add(p)
        sub = output/('simulate-'+str(len(simulated))); sub.mkdir()
        r = simulate(dict(run=ref), inputs, sub)
        require(r['configuration'] == dict(format='ndpsim-memory-run/1', workers=1, slots=1, admission=1), 'strict accuracy requires resident 1/1/1')
        save(sub/'report.json',dict(r,layer='simulate',layer_passed=True,full_acceptance_passed=False))
        simulated.append(r)
    first = simulated[0]
    require(all((r['package_sha256'],r['profile_sha256'],r['target'],r['context'],r['deterministic_sha256']) == (first['package_sha256'],first['profile_sha256'],first['target'],first['context'],first['deterministic_sha256']) for r in simulated), 'nondeterministic or incompatible simulated repeats')
    cr = capture(spec['capture'], inputs, output)
    save(output/'capture-report.json',dict(cr,layer='capture',layer_passed=True,full_acceptance_passed=False))
    require(cr['package_sha256'] == first['package_sha256'], 'accuracy capture identity')
    provenance = first['provenance']; ids = provenance['query_ids']
    for ref in spec['native']:
        directory = inputs.run(ref, native=True)
        require(directory not in seen_paths, 'duplicate native run'); seen_paths.add(directory)
        h = file_hash(directory/'reference.json')
        require(h not in seen_artifacts, 'duplicate native benchmark artifact'); seen_artifacts.add(h)
        r = load(directory/'reference.json'); cfg = load(directory/'config.json'); build = load(directory/'build.json')
        require(build == provenance['capture_build'], 'benchmark build identity')
        if cfg.get('format') == 'ndpsim-native-benchmark-config/1':
            require(provenance['workload'] != 'graph', 'graph benchmark requires native graph configuration')
            require(cfg['binding'] == dict(provenance=provenance,target=first['target'],context=first['context']), 'benchmark identity')
        else:
            require(cfg['target'] == first['target'] and cfg['split'] == provenance['split'], 'benchmark target/split identity')
            graph_benchmark_identity(cfg, provenance)
        commands = load(directory/'commands.json')
        require(commands and all(x.get('exit_code') == 0 for x in commands), 'benchmark command failure')
        require(r['workers'] == r['concurrency'] == 1 and r['major_faults'] == 0 and r['empty_probe_count'] == 0 and r['exit_code'] == 0, 'probed/paged/failed benchmark')
        nr = unique(r['queries'], 'native query', lambda x:x['original_query_id'])
        require(set(nr) == set(ids) and r['completed'] == len(ids), 'native query coverage')
        for q in nr.values():
            require(uint(q['visible_ns'])-uint(q['admission_ns']) == uint(q['latency_ns']), 'native latency/window')
            uint(q['thread_cpu_ns'])
        require(min(q['admission_ns'] for q in nr.values()) == 0, 'native window origin')
        require(r['window_ns'] == max(q['visible_ns'] for q in nr.values()) and r['thread_cpu_ns'] == sum(q['thread_cpu_ns'] for q in nr.values()), 'native total/window')
        native.append(r)
    # Admission-relative latency matches the frozen native definition, not offered latency.
    actual = {name:median(quantile([q['latency_ns'] for q in r['queries']], p) for r in native) for name,p in [('p50',.5),('p95',.95)]}
    predicted = {name:quantile([q['latency_ns'] for q in first['queries']], p) for name,p in [('p50',.5),('p95',.95)]}
    errors = {name:error(predicted[name], actual[name]) for name in actual}
    errors['qps'] = error(len(ids)*1e9/first['measurement_window_ns'], median(len(ids)*1e9/r['window_ns'] for r in native))
    errors['cpu'] = error(sum(first['service_demand_ns_by_location'].values()), median(r['thread_cpu_ns'] for r in native))
    # P06 evaluate independently enforces phase prediction errors, quality, table coverage,
    # training/evaluation isolation and frozen recipe. Re-run it, never trust summary booleans.
    from .fitting import execute
    evaluation = inputs.ref(spec['stage_evaluation'])
    er = execute('evaluate', evaluation, output/'stage-evaluation', allow_fixture=provenance['kind']=='synthetic-fixture')
    resolved = load(output/'stage-evaluation/resolved-inputs.json')
    for p,h in resolved.items(): inputs.ref(dict(path=p,sha256=h))
    require(er['target'] == first['target'] and er['sources'] and all(s['binding']['package_sha256'] == first['package_sha256'] for s in er['sources']), 'stage evaluation package/target mismatch')
    require(all(s['binding']['provenance'] == provenance for s in er['sources']), 'stage evaluation provenance mismatch')
    manifest = load(evaluation)
    frozen = load(resolve(evaluation.parent, manifest['frozen_profile']['path']))
    run_dir = resolve(inputs.base, spec['simulated'][0]['path']).parent
    simulated_profile = load(run_dir/'inputs/profile.json')
    require([r for r in simulated_profile['rules'] if r['source']=='prediction'] == frozen['rules'], 'evaluated model differs from simulated model')
    if any(r['source']=='measured-table' for r in simulated_profile['rules']):
        execute('table', evaluation, output/'table-quality', allow_fixture=provenance['kind']=='synthetic-fixture')
        require(load(output/'table-quality/profile.json') == simulated_profile, 'measured table differs from independently rebuilt quality table')
    require(all(r.get('metrics',{}).get('relative_p50',0) <= .10 and r.get('metrics',{}).get('relative_p90',0) <= .20 for r in er['rules']), 'stage prediction thresholds')
    comparisons = []
    for q in first['queries']:
        actual_ns = median(next(n['latency_ns'] for n in r['queries'] if n['original_query_id']==q['query']) for r in native)
        comparisons.append(dict(query=q['query'], native_median_latency_ns=actual_ns, simulated_latency_ns=q['latency_ns'], relative_error=error(q['latency_ns'],actual_ns)))
    save(output/'query-comparison.json', comparisons)
    save(output/'accuracy-errors.json', dict(errors=errors, actual=actual, predicted=predicted, stage_evaluation=er))
    require(errors['p50'] <= .15 and errors['p95'] <= .20 and errors['qps'] <= .10 and errors['cpu'] <= .15, 'native-vs-sim accuracy threshold')
    return dict(package_sha256=first['package_sha256'], profile_sha256=first['profile_sha256'], provenance=provenance, objects=first['objects'], errors=errors, native_repeats=len(native), simulated_repeats=len(simulated), stage_evaluation=er,
                reference_results='independently validated native capture evidence; no simulated top-k')


def execute(spec_path, output):
    spec_path = Path(spec_path).resolve(); spec = load(spec_path)
    require(set(spec) == {'format','layer','scope','budget','limits','inputs'}, 'validation spec fields')
    require(spec['format'] == 'ndpsim-validation/1' and spec['scope'] in ('fixture','development','formal-point'), 'validation format/scope')
    require(spec['layer'] in ('capture','simulate','accuracy'), 'validation layer')
    inputs = Inputs(spec_path.parent, spec['limits']); inputs.track(spec_path)
    result = {'capture':capture,'simulate':simulate,'accuracy':accuracy}[spec['layer']](spec['inputs'], inputs, output)
    if spec['scope'] != 'fixture':
        require(result['provenance']['kind'] != 'synthetic-fixture', 'fixture cannot pass nonfixture scope')
    if spec['scope'] == 'formal-point':
        p = result['provenance']
        require(p['workload']=='graph' and p['split'] in ('tuning32','evaluation128'), 'formal graph split')
        require(len(p['query_ids']) == (32 if p['split']=='tuning32' else 128), 'formal query coverage')
        vectors = next(o for o in result['objects'] if o['id']=='vectors')
        require(vectors['shape']==[1000000,128], 'formal SIFT1M shape')
        if spec['layer']=='accuracy': require(p['split']=='evaluation128', 'accuracy requires evaluation128')
    inputs.unchanged()
    result.update(format='ndpsim-layer-validation/1', layer=spec['layer'], scope=spec['scope'], layer_passed=True, full_acceptance_passed=False,
                  inputs_sha256=inputs.hashes, validator_source_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    save(output/'report.json', result)
    return result


if __name__ == '__main__':
    try:
        execute(sys.argv[1], Path(sys.argv[2])); sys.exit(0)
    except Exception as exc:
        save(Path(sys.argv[2])/'validation-error.json', dict(error=str(exc), layer_passed=False, full_acceptance_passed=False))
        print(str(exc), file=sys.stderr); sys.exit(2)
