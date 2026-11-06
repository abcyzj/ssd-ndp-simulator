"""Streaming timing reports. Reductions are descriptive, never an accuracy/AC validator."""
import csv
import math
import sqlite3
import time
from collections import Counter
from .execution import save, enforce
from .static_ir import decode, require, _lines


def reduce_trace(output, config, b, deadline):
    db = sqlite3.connect(str(output/'report.sqlite'))
    db.execute('pragma cache_size=-2048')
    db.execute('create table requests(query text primary key, latency integer, admission integer)')
    counts = Counter(); sources = Counter(); flow = Counter(); peaks = Counter()
    total_services = Counter(); summary = None; snapshot = None
    try:
        from pathlib import Path
        for i, (_, _, op) in enumerate(_lines(Path(config['package'])/'operations.jsonl',16*1024**2,b['output_bytes'])):
            for st in op['stages']:
                flow['logical_stage_output_bytes'] += sum(o['logical_bytes'] for o in st['outputs'])
                flow['logical_access_reference_bytes'] += sum(a['length_bytes'] for a in st['accesses'])
            if i % 512 == 0:
                require(time.monotonic() <= deadline, 'report timeout budget')
        with (output/'stages.csv').open('w', newline='') as sf, (output/'services.csv').open('w', newline='') as cf:
            stages = csv.writer(sf); services = csv.writer(cf)
            stages.writerow(['query','op','stage','submitted_ns','completed_ns','dependency_and_resource_elapsed_ns'])
            services.writerow(['resource','key','submitted_ns','start_ns','end_ns','queue_wait_ns','service_ns'])
            with (output/'trace.jsonl').open('rb') as f:
                for line in iter(lambda: f.readline(16*1024**2+1), b''):
                    require(len(line) <= 16*1024**2, 'trace record budget')
                    r = decode(line); k = r['type']; counts[k] += 1
                    require(summary is None, 'records after terminal summary')
                    if k == 'configuration': snapshot = r
                    elif k == 'summary': summary = r
                    elif k == 'request':
                        require(r['success'], 'failed request')
                        offered = r.get('offered', 0)  # SSD contract freezes offered=0.
                        db.execute('insert into requests values(?,?,?)', (str(r['query']), r['visible']-offered, r['admitted']-offered))
                    elif k == 'stage':
                        require(r['success'], 'failed stage')
                        stages.writerow([r[x] for x in ('query','op','stage','submitted','completed')]+[r['completed']-r['submitted']])
                    elif k in ('service', 'cpu', 'controller_compute') and all(x in r for x in ('submitted','start','end')):
                        services.writerow([k,r.get('key',r.get('id')),r['submitted'],r['start'],r['end'],r['start']-r['submitted'],r['end']-r['start']])
                    elif k == 'demand':
                        d = r.get('detail', r)
                        sources[d.get('source','unspecified')] += 1
                        total_services[d.get('location','host')] += d['duration_ns']
                    elif k == 'pcie' and r['event'] == 'arrive':
                        direction = 'D2H' if r['destination'] == 0 else 'H2D'
                        for name in ('payload','wire'): flow['pcie_'+direction+'_'+r['layer']+'_'+name+'_bytes'] += r[name]
                    elif k == 'protocol':
                        for name in ('logical','payload','wire'): flow['protocol_kind'+str(r['kind'])+'_'+name+'_bytes'] += r[name]
                    elif k == 'ordinary_plan': flow['ordinary_sector_bytes'] += r['sector_bytes']
                    elif k == 'local_plan': flow['local_sector_bytes'] += r['sector_bytes']
                    elif k == 'nand_transaction': flow['local_nand_transaction_bytes'] += r['bytes']
                    elif k == 'credit': peaks['buffer_bytes'] = max(peaks['buffer_bytes'],r['used'])
                    if sum(counts.values()) % 512 == 0:
                        enforce(output, b)
                        require(time.monotonic() <= deadline, 'report timeout budget')
        require(summary is not None and summary.get('success') is True and snapshot is not None, 'missing successful terminal summary/configuration')
        for key in ('runtime_tasks','pins','buffer_used','sessions','active_stages','outstanding','resource_pending'):
            require(summary.get(key,0) == 0, 'undrained '+key)
        n = db.execute('select count(*) from requests').fetchone()[0]
        require(n > 0, 'no completed requests')
        def quantiles(column):
            return {name: db.execute('select '+column+' from requests order by '+column+' limit 1 offset ?', (max(0,math.ceil(p*n)-1),)).fetchone()[0] for name,p in [('p50',.5),('p95',.95),('p99',.99)]}
        result = dict(format='ndpsim-run-report/1', execution_succeeded=True, full_acceptance_passed=False,
            mode=config['format'], placement=config.get('placement','host'), package_sha256=config['package_sha256'],
            profile_sha256=config['profile_sha256'], target_hardware=config['target'], context=config['context'],
            service_sources=dict(sources), service_demand_ns_by_location=dict(total_services),
            interpretation='prediction and measured-table demands are distinct sources; neither proves target hardware accuracy',
            reference_results=dict(status='Unsupported', reason='timing-only does not recompute top-k; capture evidence remains external'),
            semantic_validation='trusted-independent-receipt' if 'semantic_report' in config else 'structure-only',
            requests=n, latency_ns=quantiles('latency'), admission_wait_ns=quantiles('admission'), quantile_method='nearest_rank',
            measurement_window_ns=summary['drained'], throughput_queries_per_second=n*1e9/summary['drained'] if summary['drained'] else None,
            throughput_status='defined' if summary['drained'] else 'Unsupported: zero measurement window',
            traffic=dict(flow), traffic_note='logical access references can repeat; stage outputs are descriptors, not recomputed values; separate layers, do not sum protocol/PCIe or sector/NAND bytes',
            peaks=dict(peaks, **{k:v for k,v in summary.items() if k.startswith('peak_')}),
            unsupported_resources=['plane','latch','shared_dram','hardware_calibration'],
            hardware_validation='configured_unvalidated', drain=summary, record_counts=dict(counts),
            details=dict(stages='stages.csv', services='services.csv', all_resources='trace.jsonl', effective_backend=snapshot))
        result['wait_definitions'] = dict(resource_queue='start-submitted in services.csv',stage_elapsed='completed-submitted includes input, resource service and return; not pure CPU time',dependency_ready_wait='Unsupported: no separate ready timestamp in current trace')
        save(output/'report.json',result)
        return result
    finally:
        db.close()
