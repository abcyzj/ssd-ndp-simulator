"""T6 only: locked full SIFT1M preparation and preregistered closed-loop matrix."""
import argparse
import copy
import csv
import json
import hashlib
import re
import resource
import shutil
import struct
import sys
import threading
from pathlib import Path
from ann_data import write_json
from benchmark_artifacts import check_index, check_truth, quality
from benchmark_data import check
from verify_m0 import ROOT, measure, sha

MATRIX = ROOT/'configs/experiments/v0.2/sift1m.json'
BUDGET = ROOT/'configs/benchmarks/v0.2-budgets.json'
SELECTION = ROOT/'configs/benchmarks/v0.2-query-selection.json'
LOCK = ROOT/'configs/benchmarks/datasets.lock.json'
GIB = 1024**3


def load(path):
    return json.loads(Path(path).read_text())


def query_groups():
    q = load(SELECTION)['datasets']['sift1m']
    return dict(tuning=q['tuning_ids'], evaluation=q['evaluation_ids'],
                concurrency=q['evaluation_ids'][:32], detail=q['evaluation_ids'][:8])


def points():
    """Complete fixed plan; evaluation results never choose a configuration."""
    result = []
    def pair(group, L, E, concurrency=1, detail=False, suffix=''):
        for mode in ('host', 'controller'):
            result.append(dict(name=f'{group}-l{L}-e{E}-c{concurrency}{suffix}-{mode}',
                               group=group, L=L, E=E, concurrency=concurrency,
                               detail=detail, placement=mode))
    for group in ('tuning', 'evaluation'):
        for L, E in load(BUDGET)['search_matrix']['L_E']:
            pair(group, L, E)
    pair('evaluation', 128, 256, suffix='-repeat')
    for concurrency in (2, 4):
        pair('concurrency', 128, 256, concurrency)
    pair('detail', 128, 256)
    pair('detail', 128, 256, detail=True, suffix='-logged')
    return result


class DiskMonitor:
    """Sample file sizes and volume reserve; retain sampling limits explicitly."""
    def __init__(self, output, roots):
        self.output = Path(output)
        self.roots = list(dict.fromkeys(Path(r).resolve() for r in roots))
        self.stop = threading.Event()
        self.peak = self.samples = 0
        self.minimum_free = shutil.disk_usage(output).free
        self.error = None
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def sample(self):
        total = 0
        for root in self.roots:
            for path in ([root] if root.is_file() else root.rglob('*')):
                try:
                    if path.is_file(): total += path.stat().st_size
                except FileNotFoundError: pass  # atomic artifact rename
        self.peak = max(self.peak, total)
        self.minimum_free = min(self.minimum_free, shutil.disk_usage(self.output).free)
        self.samples += 1

    def loop(self):
        try:
            while not self.stop.wait(1): self.sample()
        except Exception as exc:
            self.error = str(exc)

    def __enter__(self):
        self.sample(); self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set(); self.thread.join(); self.sample()
        b = load(BUDGET)
        write_json(self.output/'disk-measurement.json', dict(
            roots=list(map(str, self.roots)), sampled_peak_file_bytes=self.peak,
            minimum_sampled_volume_free_bytes=self.minimum_free, samples=self.samples,
            method='1s recursive logical file-size samples; can miss subsecond peaks; not allocated blocks',
            error=self.error, passed=self.error is None and self.peak <= b['disk_peak_budget_gib']['sift']*GIB
            and self.minimum_free >= b['disk_reserve_bytes']))


def command(out, name, argv, stage, seconds=None):
    b = load(BUDGET)
    gib, timeout = b['stage_limits'][stage]['sift']
    cap = min(int(gib*GIB), b['effective_process_cap_bytes'])
    r = measure(list(map(str, argv)), out/(name+'.json'), seconds or timeout, cap)
    print(f'{name}: exit={r["exit_code"]} wall={r["wall_seconds"]:.2f}s RSS={r["peak_rss_bytes"]/GIB:.3f}GiB', flush=True)
    if not r['passed']: raise RuntimeError('failed command: '+name)
    return r


def prepare(out, source, receipt):
    locked = load(LOCK)['datasets']['sift1m']
    groups = query_groups()
    for group, ids in groups.items():
        files = {}
        for role, name in [('base','sift_base.fvecs'), ('query','sift_query.fvecs'), ('groundtruth','sift_groundtruth.ivecs')]:
            raw = locked['files'][role]
            files[role] = dict(path=str(source/name), format=raw['format'], count=raw['count'],
                dimensions=raw['dimensions'], sha256=raw['sha256'],
                source=dict(url=locked['download_url'], usage_conditions=locked['usage_conditions']))
            if role != 'base': files[role]['selection'] = dict(kind='ids', ids=ids)
        spec = out/(group+'-spec.json')
        write_json(spec, dict(metric='squared_l2', preprocessing=locked['preprocessing'], files=files))
        command(out, 'import-'+group, [sys.executable, ROOT/'tools/benchmark_data.py', 'import', spec,
                                     '--output', out/group], 'import')
        command(out, 'truth-'+group, [sys.executable, ROOT/'tools/benchmark_artifacts.py', 'official',
                                    out/group, out/(group+'-truth')], 'truth')
        if group == 'tuning':
            command(out, 'build-index', [sys.executable, ROOT/'tools/benchmark_artifacts.py', 'build',
                    out/group, out/'index', '--receipt', receipt, '--threads', 4, '--timeout', 28800], 'builder')
    validate_prepared(out)
    x = check_index(out/'tuning', out/'index')
    diagnostic = x['diagnostic']
    t = check_truth(out/'tuning', out/'tuning-truth', 10)
    if diagnostic['query_binding'] != t['binding']: raise ValueError('diagnostic query binding mismatch')
    got = list(struct.iter_unpack('<10I', (out/'index'/diagnostic['ids']['path']).read_bytes()))
    truth = list(struct.iter_unpack('<100I', (out/'tuning-truth'/t['ids']['path']).read_bytes()))
    per_query = [quality(list(g), list(e), 10, 10) for g,e in zip(got,truth)]
    recall = sum(r['recall_at_k'] for r in per_query)/32
    write_json(out/'builder-quality.json', dict(recall_at_10=recall, per_query=per_query,
        passed=recall >= load(BUDGET)['quality_gate']['builder_recall_at_10'],
        scope='native Faiss search_L=256 on tuning only; official ties unresolved'))
    write_json(out/'prepared.json', dict(scope='T6 preparation only; matrix pending',
        files_sha256={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p.suffix in ('.json','.u32','.f32','.i32')},
        query_selection_sha256=sha(SELECTION), dataset_lock_sha256=sha(LOCK), receipt_sha256=sha(receipt)))


def validate_prepared(prepared):
    for group, ids in query_groups().items():
        m = check(prepared/group)
        base = m['files']['base']
        if (base['count'],base['dimensions'],base['selection'],base['raw']['sha256']) != (
                1000000,128,{'kind':'all'},load(LOCK)['datasets']['sift1m']['files']['base']['sha256']):
            raise ValueError('T6 requires locked complete SIFT1M base')
        if m['files']['query']['selection'] != dict(kind='ids',ids=ids):
            raise ValueError('preregistered query order mismatch')
        check_truth(prepared/group, prepared/(group+'-truth'),10)
    check_index(prepared/'tuning', prepared/'index')


def validate_plan():
    m = load(MATRIX); c = m['experiment']; b = load(BUDGET)['search_matrix']
    if (m['schema'], m['dataset'], m['base_count']) != ('ndpsim-sift1m-matrix-v1','sift1m',1000000):
        raise ValueError('wrong SIFT1M matrix identity')
    if (c['search']['k'],c['search']['batch'],c['search']['chunk'],c['max_inflight']) != (b['k'],b['B'],b['chunk'],b['max_inflight']):
        raise ValueError('matrix changes preregistered search parameters')
    if m['query_selection'] != str(SELECTION.relative_to(ROOT)) or m['budget'] != str(BUDGET.relative_to(ROOT)):
        raise ValueError('matrix lock reference mismatch')
    if c['device_profile'] != 'ann-2g-v1' or c['content_backend'] != 'mmap':
        raise ValueError('matrix changes T6 profile/backend')
    groups = query_groups()
    if len(groups['tuning']) != 32 or len(groups['evaluation']) != 128 or set(groups['tuning']) & set(groups['evaluation']):
        raise ValueError('invalid tuning/evaluation split')


def result_signature(result):
    return [(r['original_query_id'], r['top_k'], r['expanded'],
             r['decision_digest_fnv1a64']) for r in result['queries']]


def statistics(path):
    """Hash all simulated statistics bytes, removing only top-level wall time."""
    h = hashlib.sha256()
    with (path/'statistics.json').open('rb') as f:
        head = f.read(4096)
        head, count = re.subn(rb'"wall_seconds":[0-9.eE+\-]+', b'"wall_seconds":0', head, count=1)
        if count != 1: raise ValueError('statistics wall-clock field missing')
        h.update(head)
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def statistics_head(path):
    with (path/'statistics.json').open() as f: head=f.read(4096)
    if '"resources":' not in head: raise ValueError('statistics header too large')
    return json.loads(head.split('"resources":',1)[0].rstrip(',')+'}')


def resource_rows(path, kind):
    file = path/(kind+'.csv')
    if not file.exists(): return
    with file.open(newline='') as f:
        for row in csv.DictReader(f):
            for key, value in row.items():
                if value in ('True','False'): row[key]=value=='True'
                else:
                    try: row[key]=json.loads(value)
                    except (ValueError,TypeError): pass
            yield row


def load_credit_zero(path):
    # credit_bytes_outstanding follows the streamed resources object.
    with (path/'statistics.json').open('rb') as f:
        f.seek(max(0,(path/'statistics.json').stat().st_size-4096))
        tail=f.read().decode()
    match=re.search(r'"credit_bytes_outstanding":(\d+)',tail)
    return match is not None and int(match[1])==0


def verify_cached_run(target, config, prepared, group, build):
    """Reuse only complete individual runs, with full artifact/model identities.

    compare_placements is an offline validator absent from run_config's execution
    path. Its historical hash is preserved; the new analysis is separately hashed.
    All simulator/model sources, inputs and effective parameters must still match.
    """
    m=load(target/'manifest.json')
    if load(target/'config.json') != config: raise ValueError('cached effective config mismatch')
    if m['element_sha256'] != sha(build/'libndpsim.so'): raise ValueError('cached SST element mismatch')
    identity=m['input_identity']
    if (identity['dataset'],identity['index'],identity['truth']) != (
            load(prepared/group/'dataset.json'),load(prepared/'index/index.json'),load(prepared/(group+'-truth')/'truth.json')):
        raise ValueError('cached input identity mismatch')
    for name,digest in m['source_sha256'].items():
        if name != 'tools/compare_placements.py' and sha(ROOT/name)!=digest:
            raise ValueError('cached model/source mismatch: '+name)
    for name,digest in m['artifacts_sha256'].items():
        if sha(target/name)!=digest: raise ValueError('cached output mismatch: '+name)
    import subprocess
    if m['sst_commit'] != subprocess.check_output(['git','-C',str(ROOT/'third_party/sst-core'),'rev-parse','HEAD'],text=True).strip():
        raise ValueError('cached SST commit mismatch')
    from source_snapshot import mqsim_source_snapshot
    mq=mqsim_source_snapshot()
    if (m['mqsim_commit'],m['mqsim_diff_from_base_sha256'],m['mqsim_source_sha256']) != (
            mq['head_commit'],mq['diff_from_native_base_sha256'],mq['files_sha256']):
        raise ValueError('cached MQSim source mismatch')
    receipt=load(target.parent/(target.name+'.json'))
    if not receipt['passed'] or sha(target.parent/(target.name+'.log'))!=receipt['log_sha256']:
        raise ValueError('cached command receipt/log mismatch')
    return dict(reused_manifest=str(target/'manifest.json'),manifest_sha256=sha(target/'manifest.json'),
        original_command_receipt=receipt,offline_validator_old_sha256=m['source_sha256']['tools/compare_placements.py'],
        offline_validator_new_sha256=sha(ROOT/'tools/compare_placements.py'),
        policy='only standalone offline comparator may change; all simulator/model sources and artifacts match')


def run_matrix(out, prepared, build, reuse_runs=None):
    validate_plan()
    validate_prepared(prepared)
    saved = load(prepared/'prepared.json')
    if saved['query_selection_sha256'] != sha(SELECTION) or saved['dataset_lock_sha256'] != sha(LOCK):
        raise ValueError('prepared lock identity changed')
    for name, digest in saved['files_sha256'].items():
        if sha(prepared/name) != digest: raise ValueError('prepared artifact changed: '+name)
    base = load(MATRIX)['experiment']; x = load(prepared/'index/index.json')
    rows = []
    assertions = {}
    for point in points():
        name = point['name']; group = point['group']; target = out/name
        c = copy.deepcopy(base)
        c.update(placement=point['placement'], index=str(prepared/'index'), truth=str(prepared/(group+'-truth')),
                 max_inflight=point['concurrency'], event_log=point['detail'])
        c['search'].update(entry=x['entry'], width=point['L'], budget=point['E'])
        config = out/(name+'-config.json'); write_json(config,c)
        if reuse_runs:
            cached=(reuse_runs/name).resolve()
            provenance=verify_cached_run(cached,c,prepared,group,build)
            write_json(out/(name+'-reuse.json'),provenance)
            target.symlink_to(cached,target_is_directory=True)
        else:
            command(out, name, [sys.executable,ROOT/'tools/run_config.py','--config',config,'--data',prepared/group,
                               '--build',build,'--output',target], 'query_per_point', 11400)
        result = load(target/'results.json'); stats = statistics_head(target)
        measurement = load(target/'sst-measurement.json'); phases = measurement['phases_wall_seconds']
        preflight = load(target/'preflight.json'); summary = result['summary']
        life = next(resource_rows(target,'lifecycle'))
        state = next(resource_rows(target,'initial_state'))
        expanded = sum(r['expanded'] for r in result['queries'])
        paths = next(resource_rows(target,'path_summary'))
        buffer = list(resource_rows(target,'buffer_summary'))
        links = {r['direction']:r for r in resource_rows(target,'link_summary')}
        checks = dict(
            drained_initialization=state['backend_drained'] and state['backend_pending']==0
                and state['completed_preload_sectors']==2000000 and state['origin_ns']==summary['origin_ns']
                and state['cmt_retained'] and not state['time_reset'],
            controller_no_vector_dma=point['placement']!='controller' or paths['ordinary_dma_bytes']==expanded*512,
            buffer_bounded=not buffer or buffer[0]['peak_bytes']<=c['controller']['capacity_bytes'],
            query_identity=[r['original_query_id'] for r in result['queries']] == query_groups()[group],
            whole_sst_peak_rss=measurement['peak_rss_bytes'] <= 4*GIB,
            initialization_wall=phases['initialization_wall_seconds'] <= 3600,
            query_wall=phases['query_wall_seconds'] <= 7200,
            postprocess_wall=measurement['postprocessing_wall_seconds'] <= 600,
            postprocess_peak_rss=measurement['python_parent_peak_rss_bytes'] <= .5*GIB,
            full_layout=preflight['actual']['preload_sectors'] == 2000000,
            actual_namespace=preflight['actual']['namespace_sectors'] >= 2000000,
            time_window=0 < summary['origin_ns'] < summary['end_ns'] <= summary['drain_end_ns'],
            bounded_admission=summary['admission_limit']==point['concurrency'] and 0<summary['peak_inflight']<=point['concurrency'],
            offered_at_origin=all(r['offered_ns']==summary['origin_ns'] for r in result['queries']),
            after_initialization=all(r['start_ns'] >= summary['origin_ns'] for r in result['queries']),
            credit_zero=load_credit_zero(target),
            reclaimed=all(life[k] == 0 for k in ('runtime_tasks','waiters','leases','transitions','releases','host_history','protocol_tasks','transport_history','resource_history')))
        # Python RUSAGE_SELF high-water bounds postprocessing; wait4 wrapper may include reaped descendant high-water.
        assertions[name] = checks
        write_json(out/(name+'-initial-state.json'), dict(
            method='deterministic full-layout page writes, backend drained before origin',
            actual=state, preload_sectors=2000000, page_sectors=8, writes=250000, origin_ns=summary['origin_ns'],
            cmt='retained', cache='SIMPLE/TURNED_OFF', cross_query_object_cache=False,
            time_reset=False, evidence=['preflight.json','results.json','sst-measurement.json','manifest.json']))
        rows.append(dict(**point, recall_at_10=result['recall_at_k'], one_recall_at_10=result['one_recall_at_r'],
            expanded_nodes=expanded, ordinary_dma_bytes=paths['ordinary_dma_bytes'],
            local_vector_bytes=sum(r['sector_bytes'] for r in resource_rows(target,'local_storage')),
            h2d_payload_bytes=links['H2D']['payload_bytes'], d2h_payload_bytes=links['D2H']['payload_bytes'],
            h2d_wire_bytes=links['H2D']['wire_bytes'], d2h_wire_bytes=links['D2H']['wire_bytes'],
            latency_p50_ns=stats['latency_ns']['p50'],latency_p95_ns=stats['latency_ns']['p95'],
            mean_latency_ns=sum(r['latency_ns'] for r in result['queries'])/len(result['queries']), simulated_qps=stats['throughput_queries_per_second'],
            measurement_window_ns=stats['measurement_window_ns'],
            initialization_wall_seconds=phases['initialization_wall_seconds'], query_wall_seconds=phases['query_wall_seconds'],
            python_parent_peak_rss_bytes=measurement['python_parent_peak_rss_bytes'],
            postprocessing_wall_seconds=measurement['postprocessing_wall_seconds'],
            sst_peak_rss_bytes=measurement['peak_rss_bytes']))
        write_json(out/'assertions.json',assertions); write_json(out/'scan.json',rows)
    for group in ('tuning','evaluation','concurrency','detail'):
        selected = [p for p in points() if p['group']==group]
        for p in selected:
            if p['placement'] != 'host': continue
            a = out/p['name']; b = out/(p['name'].removesuffix('host')+'controller')
            assertions[p['name']+'-placement'] = dict(same_results=result_signature(load(a/'results.json')) == result_signature(load(b/'results.json')))
    for mode in ('host','controller'):
        first = out/f'evaluation-l128-e256-c1-{mode}'; repeat = out/f'evaluation-l128-e256-c1-repeat-{mode}'
        assertions[mode+'-repeat'] = dict(results=load(first/'results.json')==load(repeat/'results.json'),
                                         statistics=statistics(first)==statistics(repeat))
        plain = out/f'detail-l128-e256-c1-{mode}'; detail = out/f'detail-l128-e256-c1-logged-{mode}'
        assertions[mode+'-detail'] = dict(results=load(plain/'results.json')==load(detail/'results.json'),
                                         statistics=statistics(plain)==statistics(detail))
        expected = result_signature(load(first/'results.json'))[:32]
        for concurrency in (2,4):
            assertions[f'{mode}-c{concurrency}'] = dict(results=expected == result_signature(load(out/f'concurrency-l128-e256-c{concurrency}-{mode}'/'results.json')))
    command(out,'detail-comparison',[sys.executable,ROOT/'tools/compare_placements.py',
        out/'detail-l128-e256-c1-logged-host',out/'detail-l128-e256-c1-logged-controller',
        '--output',out/'detail-comparison-report.json'],'postprocess')
    assertions['independent_detail'] = dict(passed=load(out/'detail-comparison-report.json')['status']=='passed')
    identities = [load(out/point['name']/'manifest.json') for point in points()]
    first_identity = identities[0]
    assertions['stable_runtime_identity'] = dict(
        source=all(m['source_sha256']==first_identity['source_sha256'] for m in identities),
        element=all(m['element_sha256']==first_identity['element_sha256'] for m in identities),
        dependencies=all((m['mqsim_commit'],m['sst_commit'],m['mqsim_diff_from_base_sha256'],m['mqsim_source_sha256']) ==
            (first_identity['mqsim_commit'],first_identity['sst_commit'],first_identity['mqsim_diff_from_base_sha256'],first_identity['mqsim_source_sha256']) for m in identities))
    assertions['quality_gates'] = dict(builder=load(prepared/'builder-quality.json')['passed'],
        project=all(r['recall_at_10'] >= .90 for r in rows if r['group'] in ('tuning','evaluation') and r['L']==256))
    matched = []
    for target in (.80,.90,.95):
        for mode in ('host','controller'):
            candidates = [r for r in rows if r['group']=='tuning' and r['placement']==mode and r['recall_at_10']>=target]
            choice = min(candidates,key=lambda r:r['mean_latency_ns']) if candidates else None
            evaluation = next((r for r in rows if choice and r['group']=='evaluation' and r['placement']==mode and r['L']==choice['L'] and not '-repeat-' in r['name']),None)
            matched.append(dict(target_recall=target, placement=mode, status='matched_on_tuning' if choice else 'unmatched', tuning=choice, independent_evaluation=evaluation,
                evaluation_meets_target=evaluation is not None and evaluation['recall_at_10']>=target))
    write_json(out/'quality-matched.json',matched)
    report=['# SIFT1M T6 matrix', '',
        'Full 1M base; NSG graph + ndpsim-ranked-expansion-v1. Hardware configured_unvalidated.',
        '32 tuning / 128 independent evaluation queries; fixed subsets, not all 10k queries. p99 is not a stable tail estimate.',
        'Official ID-only top-100 truth; strict FP64/ID tie boundary unresolved.', '',
        '| Group | L/E | Placement | Recall@10 | Mean admitted latency ns | Simulated QPS |',
        '| --- | --- | --- | ---: | ---: | ---: |']
    for r in rows:
        report.append(f"| {r['group']} c{r['concurrency']}{' detail' if r['detail'] else ''} | {r['L']}/{r['E']} | {r['placement']} | {r['recall_at_10']:.6f} | {r['mean_latency_ns']:.1f} | {r['simulated_qps']:.3f} |")
    report += ['', 'Quality-matched configurations are selected on tuning latency only; independent evaluation uses the same L/E.']
    for r in matched:
        choice=r['tuning']; evaluation=r['independent_evaluation']
        report.append(f"- Target {r['target_recall']:.2f}, {r['placement']}: " +
            (f"L/E={choice['L']}/{choice['E']}; tuning Recall={choice['recall_at_10']:.6f}; evaluation Recall={evaluation['recall_at_10']:.6f}; evaluation target {'met' if r['evaluation_meets_target'] else 'unmet'}" if choice else 'unmatched'))
    (out/'report.md').write_text('\n'.join(report)+'\n')
    with (out/'scan.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    write_json(out/'assertions.json',assertions)
    return all(all(checks.values()) for checks in assertions.values())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path)
    p.add_argument('--receipt',type=Path)
    p.add_argument('--reuse-runs',type=Path,help='Revalidate complete individual T6 runs and rerun final analysis; no directory-only cache')
    p.add_argument('--prepared',type=Path,help='Reuse validated T6 preparation, never M0 probes')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); out=a.output.resolve()
    if out.exists(): p.error('output must not exist')
    if not a.prepared and (not a.source_dir or not a.receipt): p.error('preparation requires --source-dir and --receipt')
    if a.reuse_runs and not a.prepared: p.error('--reuse-runs requires --prepared')
    if a.prepared and a.prepare_only: p.error('--prepare-only cannot be combined with --prepared')
    validate_plan()
    out.mkdir(parents=True)
    tool_started = sha(__file__)
    prepared=a.prepared.resolve() if a.prepared else out/'prepared'
    roots=[out]
    if a.prepared: roots.append(prepared)
    if a.reuse_runs: roots.append(a.reuse_runs.resolve())
    if a.source_dir: roots.append(a.source_dir.resolve())
    passed=False; error=None
    try:
        with DiskMonitor(out,roots):
            if not a.prepared:
                prepared.mkdir()
                prepare(prepared,a.source_dir.resolve(),a.receipt.resolve())
            if a.prepare_only:
                passed=True
            else:
                passed=run_matrix(out,prepared,a.build.resolve(),a.reuse_runs.resolve() if a.reuse_runs else None)
        passed=passed and load(out/'disk-measurement.json')['passed'] and tool_started==sha(__file__)
        passed=passed and resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)<=512*1024**2
    except Exception as exc:
        passed=False
        error=str(exc)
        raise
    finally:
        write_json(out/'manifest.json',dict(scope='T6 preparation only' if a.prepare_only else 'T6 full SIFT1M matrix; no T7/T8',
            passed=passed,error=error,prepared=str(prepared),reuse_runs=str(a.reuse_runs.resolve()) if a.reuse_runs else None,
            offline_validator_sha256=sha(ROOT/'tools/compare_placements.py'),matrix_sha256=sha(MATRIX),
            budget_sha256=sha(BUDGET),query_selection_sha256=sha(SELECTION),
            tool_sha256=sha(__file__), tool_sha256_started=tool_started,
            scanner_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
            outputs_sha256={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and
                (p.name in ('scan.json','scan.csv','report.md','quality-matched.json','detail-comparison-report.json','assertions.json','prepared.json','initial-state.json','disk-measurement.json','sst-measurement.json','config.json','results.json') or p.name.endswith(('-reuse.json','-initial-state.json')) or p.name=='manifest.json' and p.parent!=out)},
            limits='configured_unvalidated; wait4 child RSS, no private/phase/tree simultaneous RSS; sampled disk peak',
            commands=sorted(p.name for p in out.glob('*.json') if p.with_suffix('.log').exists())))
        if not a.prepare_only:
            write_json(out/'ac-matrix.json',{key:('passed' if passed else 'failed_or_incomplete') for key in ('AC-02-07','AC-02-09','AC-02-11')})
    if not passed: raise RuntimeError('T6 gates failed; inspect assertions and budgets; no next task started')
    print('T6 preparation passed; matrix pending' if a.prepare_only else 'T6 full matrix passed; stopped before T7',flush=True)


if __name__=='__main__': main()
