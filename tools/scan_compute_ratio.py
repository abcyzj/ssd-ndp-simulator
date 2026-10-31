"""SIFT1M sensitivity scan: fixed Host rate=4, controller rates=1/2/4/8/16.

This varies only controller arithmetic rate, retaining the existing asymmetric
Host-loading/controller-buffer accounting. It is not a normalized hardware model.
"""
import argparse
import copy
import csv
import sys
from pathlib import Path

from ann_data import write_json
from verify_m0 import ROOT, measure, sha
from verify_t6 import (MATRIX, LOCK, SELECTION, load, validate_prepared,
                       result_signature, statistics_head, resource_rows,
                       load_credit_zero, query_groups)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--build', type=Path, default=ROOT/'build/delivery')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--control-rate', type=int, default=1,
                   help='Host control work units/ns for both placements (default: 1)')
    p.add_argument('--controller-rates', type=int, nargs='+', default=[1, 2, 4, 8, 16],
                   help='Controller FLOPs/ns points (default: 1 2 4 8 16)')
    a = p.parse_args()
    if a.control_rate <= 0 or any(rate <= 0 for rate in a.controller_rates):
        p.error('rates must be positive integers')
    if len(set(a.controller_rates)) != len(a.controller_rates):
        p.error('controller rates must be unique')
    prepared, build, out = a.prepared.resolve(), a.build.resolve(), a.output.resolve()
    if out.exists():
        p.error('output must not exist')
    out.mkdir(parents=True)
    write_json(out/'status.json', dict(status='running'))
    validate_prepared(prepared)
    saved = load(prepared/'prepared.json')
    assert saved['query_selection_sha256'] == sha(SELECTION)
    assert saved['dataset_lock_sha256'] == sha(LOCK)
    for name, digest in saved['files_sha256'].items():
        if sha(prepared/name) != digest:
            raise ValueError('prepared artifact changed: '+name)
    base = load(MATRIX)['experiment']
    base.update(index=str(prepared/'index'), truth=str(prepared/'evaluation-truth'),
                event_log=False, max_inflight=1)
    base['search'].update(entry=load(prepared/'index/index.json')['entry'],
                          width=128, budget=256)
    base['host']['control_rate'] = a.control_rate
    points = [('host', a.controller_rates[-1])] + [('controller', rate) for rate in a.controller_rates]
    write_json(out/'plan.json', dict(
        base_config=base, points=points, query_ids=query_groups()['evaluation'],
        interpretation='rate ratio only; Host rate includes loading; controller buffer separately charged',
        script_sha256=sha(Path(__file__)), prepared_manifest_sha256=sha(prepared/'prepared.json'),
        build_element_sha256=sha(build/'libndpsim.so')))
    rows, assertions, signature = [], {}, None
    for placement, rate in points:
        name = 'host' if placement == 'host' else f'controller-rate{rate}'
        target = out/name
        config = copy.deepcopy(base)
        config['placement'] = placement
        config['controller']['flops_per_ns'] = rate
        config_path = out/(name+'-config.json')
        write_json(config_path, config)
        print('Starting '+name, flush=True)
        receipt = measure([sys.executable, str(ROOT/'tools/run_config.py'),
                           '--config', str(config_path), '--build', str(build),
                           '--data', str(prepared/'evaluation'), '--output', str(target)],
                          out/(name+'-receipt.json'), 11400)
        if not receipt['passed']:
            raise RuntimeError('run failed: '+name)
        result = load(target/'results.json')
        current = result_signature(result)
        if signature is None:
            signature = current
        state = next(resource_rows(target, 'initial_state'))
        life = next(resource_rows(target, 'lifecycle'))
        paths = next(resource_rows(target, 'path_summary'))
        buffers = list(resource_rows(target, 'buffer_summary'))
        links = list(resource_rows(target, 'link_summary'))
        stats = statistics_head(target)
        measurement = load(target/'sst-measurement.json')
        expanded = sum(q['expanded'] for q in result['queries'])
        checks = dict(
            same_results_distances_decisions=current == signature,
            fixed_queries=[q['original_query_id'] for q in result['queries']] == query_groups()['evaluation'],
            full_preload=state['completed_preload_sectors'] == 2000000,
            drained=state['backend_drained'] and state['backend_pending'] == 0,
            initial_state=state['cmt_retained'] and not state['time_reset'],
            after_origin=all(q['start_ns'] >= state['origin_ns'] for q in result['queries']),
            no_vector_host_dma=placement == 'host' or paths['ordinary_dma_bytes'] == expanded*512,
            buffer_bounded=not buffers or buffers[0]['peak_bytes'] <= 16384,
            credit_zero=load_credit_zero(target),
            reclaimed=all(life[k] == 0 for k in ('runtime_tasks','waiters','leases','transitions','releases','host_history','protocol_tasks','transport_history','resource_history')),
            sst_rss_budget=measurement['peak_rss_bytes'] <= 4*1024**3,
            python_rss_budget=measurement['python_parent_peak_rss_bytes'] <= 512*1024**2,
            init_budget=measurement['phases_wall_seconds']['initialization_wall_seconds'] <= 3600,
            query_budget=measurement['phases_wall_seconds']['query_wall_seconds'] <= 7200,
            postprocess_budget=measurement['postprocessing_wall_seconds'] <= 600)
        assertions[name] = checks
        row = dict(placement=placement, host_rate=4, controller_rate=rate,
                   control_rate=a.control_rate,
                   ratio=rate/4 if placement == 'controller' else '',
                   recall=result['recall_at_k'],
                   mean_ms=sum(q['latency_ns'] for q in result['queries'])/128/1e6,
                   qps=stats['throughput_queries_per_second'],
                   wire_bytes=sum(link['wire_bytes'] for link in links),
                   sst_peak_rss_bytes=measurement['peak_rss_bytes'])
        row['speedup'] = row['qps']/rows[0]['qps'] if rows else 1.
        rows.append(row)
        write_json(out/'scan.json', rows)
        write_json(out/'assertions.json', assertions)
        print(f"Finished {name}: {row['mean_ms']:.6f} ms, {row['qps']:.6f} QPS", flush=True)
        if not all(checks.values()):
            raise RuntimeError('assertion failed: '+name)
    with (out/'scan.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = ['# SIFT1M compute-rate sensitivity', '',
              'Host 4 FLOPs/ns; L/E=128/256; evaluation128; concurrency1.',
              f'Host control rate={a.control_rate} work units/ns in all runs. Hardware configured_unvalidated.',
              'Host rate includes loading; controller buffer charged separately; rate=1 is not equal total compute cost.', '',
              '| Placement | Rate ratio | Mean ms | QPS | Speedup | Recall@10 |',
              '|---|---:|---:|---:|---:|---:|']
    for row in rows:
        report.append(f"| {row['placement']} | {row['ratio']} | {row['mean_ms']:.6f} | {row['qps']:.6f} | {row['speedup']:.6f} | {row['recall']:.8f} |")
    (out/'report.md').write_text('\n'.join(report)+'\n')
    write_json(out/'status.json', dict(status='passed', runs=len(rows),
        assertions=sum(len(c) for c in assertions.values()),
        scan_sha256=sha(out/'scan.json'), script_sha256=sha(Path(__file__))))


if __name__ == '__main__':
    main()
