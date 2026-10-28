"""M4.1 output reduction. Statistics never read the optional event log."""
import csv
import math
from pathlib import Path
from ann_data import write_json


def quantiles(values):
    ordered = sorted(values)
    return dict(samples=len(ordered), method='nearest_rank',
                **{name: ordered[max(0, math.ceil(p*len(ordered))-1)] if ordered else None
                   for name, p in [('p50', .5), ('p95', .95), ('p99', .99)]})


def csv_rows(path, rows, fields=None):
    fields = fields or sorted(set().union(*(r.keys() for r in rows)))
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)


def export(output, results, records, wall_seconds):
    summary = next(r for r in records if r['type'] == 'summary')
    resources = {}
    for row in records:
        if row['type'] not in ('query_result', 'summary'):
            resources.setdefault(row['type'], []).append(row)
    window = summary['end_ns'] - summary['origin_ns']
    stats = dict(queries=len(results), measurement_window_ns=window,
                 throughput_queries_per_second=len(results)*1e9/window if window else None,
                 latency_ns=quantiles([r['latency_ns'] for r in results]),
                 admission_wait_ns=quantiles([r['admission_wait_ns'] for r in results]),
                 offered_latency_ns=quantiles([r['offered_latency_ns'] for r in results]),
                 wall_seconds=wall_seconds, resources=resources,
                 host_wait_definition='dependency_wait=ready-submit; queue_wait=start-ready',
                 ssd_definition='half-open clipped interval union per entity; bus excludes array; utilization=union/window',
                 memory_usage=dict(status='Unsupported', reason='portable peak child RSS not collected'))
    for r in resources.get('link_summary', []):
        r['direction'] = {0: 'D2H', 1: 'H2D'}[r['destination']]
    for r in resources.get('ssd_stat', []):
        r['utilization'] = r['union_ns']/window if window else None
    for r in resources.get('service', []):
        r['service_ns'] = r['end_ns']-r['start_ns']
        r['dependency_wait_ns'] = r['ready_ns']-r['submitted_ns']
        r['queue_wait_ns'] = r['start_ns']-r['ready_ns']
    for r in resources.get('controller_service', []):
        r['service_ns'] = r['end_ns']-r['start_ns']
        r['queue_wait_ns'] = r['start_ns']-r['submitted_ns']
    credits = resources.get('credit', [])
    pending = {}
    for r in credits:
        if r['reserve']:
            pending[r['id']] = r['bytes']
        else:
            pending[r['id']] -= r['bytes']
    stats['credit_bytes_outstanding'] = sum(pending.values())
    write_json(output/'statistics.json', stats)
    csv_rows(output/'queries.csv', [{k:v for k,v in r.items() if k != 'top_k'} for r in results])
    csv_rows(output/'top-k.csv', [dict(query=r['query'], rank=i+1, **v) for r in results for i,v in enumerate(r['top_k'])], ['query','rank','id','distance'])
    csv_rows(output/'statistics.csv', [dict(metric=key, **stats[key]) for key in ('latency_ns','admission_wait_ns','offered_latency_ns')])
    for name, rows in resources.items():
        # Nested local request transaction details belong in JSON/event logs.
        csv_rows(output/(name+'.csv'), [{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in rows])
    capabilities = dict(device='Extended', request='Extended; query/task linked ordinary/local read events',
                        channel='Extended; actual bus interval union', die='Extended; array/data-out/wait intervals',
                        plane='Unsupported', latch='Unsupported', shared_dram='Unsupported',
                        internal_request_transaction_association='Unsupported; command hook lacks request/transaction participants',
                        algorithm_internal_state_access='Unsupported; research observation only')
    write_json(output/'capabilities.json', capabilities)
    return stats
