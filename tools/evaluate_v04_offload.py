"""Run and independently audit the fixed V0.4 O01–O04 SSD scan."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from ann_data import digest, write_json
from compare_placements import event_rows, inspect
from run_graph import ROOT
from v04_contract import require, validate_profile
from verify_m0 import measure
from verify_t6 import MATRIX, query_groups, validate_prepared

GIB = 1024**3


def load(path):
    return json.loads(path.read_text())


def conditional_profile(source, config):
    profile = deepcopy(source)
    require(profile['format'] == 'ndpsim-cpu-profile/3' and
            profile['applicability']['preparation_context'] == 'resident_random' and
            profile['training']['split'] == 'tuning32' and
            profile['training']['query_ids'] == query_groups()['tuning'],
            'SSD scan requires the frozen tuning32 split-merge model')
    profile['applicability']['preparation_context'] = 'ssd_return_buffer'
    models = profile['models']
    # Configured CPU-only preparation demands. Storage already owns delivery.
    # Use one control unit per returned object/edge or inserted ID.
    host = config['host']
    new_stages = {
        ('SCORE_L2', 'score_prepare'): ('vectors', config['search']['chunk']),
        ('READ_ADJACENCY', 'adjacency_prepare'): ('edges', config['degree']),
        ('MARK_SCORED', 'state'): ('objects', config['degree'] * config['search']['batch']),
        ('MARK_EXPANDED', 'state'): ('objects', config['search']['batch']),
    }
    models[:] = [r for r in models if (r['operator'], r['stage']) not in new_stages]
    for (op, stage), (feature, limit) in new_stages.items():
        models.append(dict(operator=op, stage=stage,
                           samples=0, error=None, selection_rule='configured SSD CPU-only /1',
                           features=[dict(name=feature, unit='count', minimum=0, maximum=limit)],
                           model=dict(kind='linear', intercept_ns=host['control_setup'],
                                      coefficients=[1 / host['control_rate']])))
    validate_profile(profile)
    return profile


def audit(path):
    bill, services = Counter(), {}
    def check_host(rows):
        previous_end = 0
        for row in rows:
            require(previous_end <= row['start_ns'] and
                    row['submitted_ns'] <= row['ready_ns'] <= row['start_ns'] <= row['end_ns'],
                    'Host service window/slot overlap')
            previous_end = row['end_ns']
            require(row['task'] not in services, 'duplicate Host CPU task')
            services[row['task']] = row
            bill[row['kind'] + '_ns'] += row['end_ns'] - row['start_ns']
            bill['queue_ns'] += row['start_ns'] - row['ready_ns']
            bill['dependency_ns'] += row['ready_ns'] - row['submitted_ns']

    audited = inspect(path, check_host_service=check_host)
    result = load(path/'results.json')
    observation = load(path/'observation.json')
    ledger = observation['ssd_replay']
    require(ledger['cpu_busy_ns'] == bill['control_ns'] + bill['distance_ns'] and
            ledger['cpu_queue_wait_ns'] == bill['queue_ns'] and
            ledger['cpu_dependency_wait_ns'] == bill['dependency_ns'] and
            ledger['workers_idle'] == 1 and ledger['pending_admissions'] == 0 and
            ledger['cpu_queued'] == ledger['cpu_busy_slots'] == 0, 'Host ledger/drain mismatch')
    work = Counter()
    operations = 0
    metrics = Counter()
    state = None
    buffer = None
    stages, stage_ns = Counter(), Counter()
    shared_control = hashlib.sha256()
    semantic = hashlib.sha256()
    for row in event_rows(path):
        kind = row['type']
        if kind == 'replay_verified':
            operations += 1
            for name, count in row['work'].items():
                work[name] += count
            semantic.update(json.dumps([row[k] for k in
                            ('original_query_id', 'op', 'operator', 'ids', 'sha256', 'work')],
                            sort_keys=True).encode() + b'\n')
        elif kind == 'initial_state':
            require(state is None, 'duplicate initial state')
            state = row
        elif kind == 'buffer_summary':
            buffer = row
        elif kind == 'replay_stage':
            stage = row['stage']
            service = services.pop(row['task'])
            require(service['query'] == row['query'] and row['time_ns'] == service['start_ns'],
                    'Host CPU stage does not bind to its billed task')
            duration = service['end_ns'] - service['start_ns']
            stages[stage] += 1
            stage_ns[stage] += duration
            if stage not in ('score_prepare', 'score_l2'):
                shared_control.update(json.dumps([row['query'], row['op'], stage, duration]).encode() + b'\n')
        elif kind == 'replay_controller_work':
            metrics['controller_chunks'] += row['chunks']
            metrics['controller_physical_l2_read_bytes'] += row['physical_read_bytes']
        elif kind == 'read_submit':
            metrics['ordinary_requests'] += 1
        elif kind == 'local_storage':
            metrics['local_requests'] += row['requests']
            metrics['local_transactions'] += row['transactions']
            metrics['local_read_ns'] += row['completed_ns'] - row['submitted_ns']
        elif kind == 'link' and row['phase'] == 'arrive':
            metrics['link_service_ns'] += row['service_ns']
        elif kind == 'controller_service':
            metrics[row['stage']+'_ns'] += row['end_ns'] - row['start_ns']
            metrics['controller_service_queue_ns'] += row['start_ns'] - row['submitted_ns']
        elif kind == 'lifecycle':
            require(all(row[key] == 0 for key in ('runtime_tasks', 'waiters', 'leases', 'protocol_tasks',
                    'host_history', 'transport_history', 'resource_history')) and row['protocol_ranges'] <= 2,
                    'lifecycle ledger did not drain')
    require(not services, 'unbound Host CPU services')
    require(state and state['backend_drained'] and state['backend_pending'] == 0 and
            state['cmt_retained'] and not state['time_reset'] and
            state['completed_preload_sectors'] == 2_000_000, 'wrong SSD initial state')
    require(operations == observation['replay']['operations'], 'incomplete runtime operation coverage')
    summary = result['summary']
    queries = result['queries']
    latencies = sorted(row['latency_ns'] for row in queries)
    count = len(queries)
    window = summary['end_ns'] - state['origin_ns']
    require([row['original_query_id'] for row in queries] == query_groups()['evaluation'],
            'evaluation query inventory mismatch')
    return dict(initial_state=state, verified_operations=operations, semantic_sha256=semantic.hexdigest(),
                work=work, stage_calls=stages, stage_ns=stage_ns,
                shared_control_sha256=shared_control.hexdigest(),
                host=bill, storage_and_controller=metrics, buffer=buffer,
                ledger=audited['ledger'], queries=count, window_ns=window,
                drain_after_last_result_ns=summary['drain_end_ns']-summary['end_ns'],
                p50_ns=latencies[(50*count+99)//100-1], p95_ns=latencies[(95*count+99)//100-1],
                qps=count*1e9/window)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'capture', 'profile', 'fit-report', 'build', 'output'):
        p.add_argument('--'+name, type=lambda path: Path(path).resolve(), required=True)
    p.add_argument('--level-a', type=Path, help='Two-point V04-4b report')
    p.add_argument('--level-bc', type=Path, nargs=2, help='A01 and A02 V04-4c reports, in order')
    a = p.parse_args()
    a.output.mkdir(parents=True)
    validate_prepared(a.prepared)
    inputs = load(a.prepared/'prepared.json')
    for name, expected in inputs['files_sha256'].items():
        require(digest(a.prepared/name) == expected, f'prepared artifact changed: {name}')
    binding = load(a.capture/'binding.json')
    identity = binding['identity']
    config = deepcopy(load(MATRIX)['experiment'])
    config['search'].update(entry=identity['search']['entry'], width=64, budget=128)
    profile = load(a.profile)
    require(digest(a.fit_report) == profile['training']['fit_report_sha256'],
            'frozen fit report identity mismatch')
    degree = identity['objects']['graph']['width']
    ssd_profile = conditional_profile(profile, dict(config, degree=degree))
    profile_path = a.output/'ssd-profile.json'
    write_json(profile_path, ssd_profile)
    available = shutil.disk_usage(a.output).free
    memory = int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True))
    swap_before = subprocess.check_output(['sysctl', '-n', 'vm.swapusage'], text=True).strip()
    require(memory >= 8*GIB and available >= 20*GIB, 'insufficient scan RAM or free disk budget')
    cpu_prerequisite = None
    require(bool(a.level_a) == bool(a.level_bc), 'provide both Level A and Level B/C evidence')
    if a.level_a:
        level_a = load(a.level_a)
        require(level_a['numeric_gate'] == level_a['table_gate'] == 'passed' and
                set(level_a['points']) == {'l32-e64', 'l64-e128'}, 'Level A evidence gate')
        for label, path in zip(('A01', 'A02'), a.level_bc):
            report = load(path)
            require(report['point'] == label and report['status'] == 'passed' and report['exit_code'] == 0,
                    f'{label} Level B/C evidence gate')
        require(report['capture_provenance']['manifest_sha256'] == digest(a.capture/'manifest.json'),
                'A02 used a different capture')
        cpu_prerequisite = {str(path.resolve()): digest(path) for path in (a.level_a, *a.level_bc)}
    package = load(a.capture/'package-validation.json')
    budget = 3*GIB
    config.update(index=str(a.prepared/'index'), truth=str(a.prepared/'evaluation-truth'),
                  execution_engine='replay', data_provider='ssd', order_policy='legacy-ssd/1',
                  native_workers=1, event_log=True, output_budget_bytes=budget,
                  simulation_timeout_seconds=3600,
                  ir_binding=str(a.capture/'binding.json'),
                  ir_input=dict(path=str(a.capture/'package'), sha256=package['package_sha256']),
                  cpu_profile=dict(path=str(profile_path), sha256=digest(profile_path)))
    rate = config['controller']['flops_per_ns']
    require(rate % 2 == 0, 'Controller half-rate must be an exact integer')
    points = [('O01', 'host', rate), ('O02', 'controller', rate//2),
              ('O03', 'controller', rate), ('O04', 'controller', rate*2)]
    sources = dict(
        host_reused='tuning32 v3: init, L2, append/finalize, select, dedup, finish; conditional transfer',
        host_preparation='configured CPU-only per-object/edge costs for SSD buffer and state',
        ssd='configured_unvalidated MQSim ordinary/local read',
        pcie='configured_unvalidated shared MQSim transport',
        controller='configured_unvalidated Controller arithmetic, staging, slots and buffer')
    build_hash = digest(a.build/'libndpsim.so')
    write_json(a.output/'plan.json', dict(
        requirements=['FR-04-O01', 'FR-04-O02', 'FR-04-O03', 'FR-04-O04', 'AC-04-10'],
        config=config, points=points, preparation_sha256=digest(a.prepared/'prepared.json'),
        binding_sha256=digest(a.capture/'binding.json'), capture_manifest_sha256=digest(a.capture/'manifest.json'),
        source_profile_sha256=digest(a.profile), fit_report_sha256=digest(a.fit_report),
        build_sha256=build_hash, resident_cpu_prerequisite=cpu_prerequisite, sources=sources, platform=platform.platform(),
        hardware=profile['applicability']['hardware'], memory_bytes=memory, swap_before=swap_before,
        free_disk_bytes=available, max_output_bytes_per_run=budget, process_timeout_seconds=7800))
    rows = {}
    baseline_identity = None
    for label, placement, point_rate in points:
        require(digest(a.build/'libndpsim.so') == build_hash, 'simulator build changed during scan')
        point = deepcopy(config)
        point['placement'] = placement
        point['controller']['flops_per_ns'] = point_rate
        config_path = a.output/(label+'.json')
        write_json(config_path, point)
        target = a.output/label
        print('Running', label, flush=True)
        receipt = measure([sys.executable, '-B', str(ROOT/'tools/run_config.py'), '--config', str(config_path),
                           '--build', str(a.build), '--data', str(a.prepared/'evaluation'), '--output', str(target)],
                          a.output/(label+'-process.json'), 7800, rss_limit=4*GIB)
        require(receipt['passed'], f'{label} simulator failed')
        manifest = load(target/'manifest.json')
        require(manifest['element_sha256'] == build_hash and
                manifest['config_sha256'] == digest(config_path) and
                manifest['replay']['package_sha256'] == package['package_sha256'],
                f'{label} simulator identity mismatch')
        run_identity = {key: manifest[key] for key in (
            'input_identity', 'truth_sha256', 'device_preflight', 'source_sha256', 'element_sha256',
            'mqsim_native_base', 'mqsim_commit', 'mqsim_diff_from_base_sha256', 'mqsim_source_sha256',
            'sst_commit')}
        if baseline_identity is None:
            baseline_identity = run_identity
        require(run_identity == baseline_identity, f'{label} differs from O01 run identity')
        for name, expected in manifest['artifacts_sha256'].items():
            require(digest(target/name) == expected, f'{label} artifact changed: {name}')
        print('Auditing', label, flush=True)
        row = rows[label] = audit(target)
        baseline = rows['O01']
        for key in ('initial_state', 'work', 'semantic_sha256', 'shared_control_sha256'):
            require(row[key] == baseline[key], f'{label} differs from O01 in {key}')
        for key in ('init', 'state', 'merge', 'select', 'dedup', 'adjacency_prepare', 'finish'):
            require(row['stage_calls'][key] == baseline['stage_calls'][key], f'{label}: {key} call count')
        if label != 'O01':
            require(row['ledger']['vector_d2h_bytes'] == row['host']['distance_ns'] == 0,
                    f'{label} leaked Host vector traffic or distance service')
            for key in ('ledger', 'buffer'):
                require(row[key] == rows['O02'][key] if key == 'ledger' else
                        row[key]['chunk_size'] == rows['O02'][key]['chunk_size'],
                        f'{label} changed Controller physical mapping')
        row['qps_ratio_vs_b'] = row['qps']/baseline['qps']
        row['p50_ratio_vs_b'] = baseline['p50_ns']/row['p50_ns']
        row['p95_ratio_vs_b'] = baseline['p95_ns']/row['p95_ns']
        row['manifest_sha256'] = digest(target/'manifest.json')
        row['process_sha256'] = digest(a.output/(label+'-process.json'))
        write_json(a.output/(label+'-audit.json'), row)
    interpretation = 'conditional_prediction' if cpu_prerequisite else 'exploratory_cpu_prerequisite_unverified'
    write_json(a.output/'offload.json', dict(
        status='passed', interpretation=interpretation, sources=sources,
        assertions=['same source, device, workload, preload and measured window origin',
                    'all 128 queries and verified operation Work agree',
                    'shared Host control stages agree per call',
                    'independent numerical search, address, message, credit and service ledgers',
                    'Controller vector D2H is zero and CloseAck retires each query'],
        swap_after=subprocess.check_output(['sysctl', '-n', 'vm.swapusage'], text=True).strip(),
        resident_cpu_prerequisite=cpu_prerequisite,
        plan_sha256=digest(a.output/'plan.json'), points=rows,
        tools_sha256={name: digest(ROOT/'tools'/name) for name in
                      ('evaluate_v04_offload.py', 'compare_placements.py', 'replay_input.py', 'run_graph.py')}))
    title = '条件预测' if cpu_prerequisite else 'CPU 前提未建立的探索'
    lines = [f'# V04-5a SSD B/C {title}', '',
             '| 配置 | QPS | P50 (ns) | P95 (ns) | QPS / B |',
             '| --- | ---: | ---: | ---: | ---: |']
    for label, row in rows.items():
        lines.append(f"| {label} | {row['qps']:.4f} | {row['p50_ns']} | {row['p95_ns']} | {row['qps_ratio_vs_b']:.4f} |")
    lines += ['', '| 配置 | Host CPU (ns) | 盘控 L2 (ns) | Host 依赖等待 (ns) | 普通 DMA (B) | 盘控本地向量 (B) | PCIe 线速字节 (B) | 缓冲峰值 (B) |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for label, row in rows.items():
        host, controller, ledger = row['host'], row['storage_and_controller'], row['ledger']
        buffer_peak = row['buffer']['peak_bytes'] if row['buffer'] else 0
        lines.append(f"| {label} | {host['control_ns']+host['distance_ns']} | {controller.get('arithmetic_ns', 0)} | "
                     f"{host['dependency_ns']} | {ledger['ordinary_data_bytes']} | {ledger['local_vector_bytes']} | "
                     f"{ledger['total_wire']} | {buffer_peak} |")
    lines += ['', 'O01 为 SSD Host 参照。O02～O04 使用 0.5×、1×、2× Controller 算力。',
              '硬件和 SSD 返回缓冲 CPU 适用性均为配置条件，见 plan.json 及 offload.json。']
    (a.output/'offload.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
