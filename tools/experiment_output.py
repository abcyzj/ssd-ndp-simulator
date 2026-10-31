"""M4.1 output reduction. Statistics never read the optional event log."""
import csv
import math
import json
import sqlite3
import tempfile
from pathlib import Path
from ann_data import write_json


def quantiles(values):
    ordered = sorted(values)
    return dict(samples=len(ordered), method='nearest_rank',
                **{name: ordered[max(0, math.ceil(p*len(ordered))-1)] if ordered else None
                   for name, p in [('p50', .5), ('p95', .95), ('p99', .99)]})


def csv_rows(path, rows, fields=None):
    fields = fields or sorted({key for r in rows for key in r})
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)


class RecordStream:
    def __init__(self, path): self.path = path
    def __iter__(self):
        with self.path.open() as f:
            for line in f: yield json.loads(line)


class ResourceRows:
    def __init__(self, db, kind): self.db, self.kind = db, kind
    def __iter__(self):
        for (row,) in self.db.execute('select row from records where kind=? order by id', (self.kind,)):
            yield json.loads(row)


def stream_json(f, value):
    if isinstance(value, dict):
        f.write('{')
        for i, (key, v) in enumerate(value.items()):
            if i: f.write(',')
            f.write(json.dumps(key)+':'); stream_json(f, v)
        f.write('}')
    elif isinstance(value, (ResourceRows, list, tuple)):
        f.write('[')
        for i, v in enumerate(value):
            if i: f.write(',')
            stream_json(f, v)
        f.write(']')
    else: f.write(json.dumps(value, allow_nan=False))


def export(output, results, records, wall_seconds):
    # Disk-backed grouping keeps historical resource arrays out of Python memory.
    with tempfile.TemporaryDirectory(prefix='ndpsim-output-', dir=output) as tmp:
        db = sqlite3.connect(str(Path(tmp)/'resources.sqlite'))
        try:
            db.execute('pragma cache_size=-2048')
            db.execute('create table records(id integer primary key, kind text, row text)')
            db.execute('create index resource_kind on records(kind,id)')
            return _export(output, results, records, wall_seconds, db)
        finally: db.close()


def _export(output, results, records, wall_seconds, db):
    summary = next(r for r in records if r['type'] == 'summary')
    resources = {}
    window = summary['end_ns'] - summary['origin_ns']
    pending = {}
    for row in records:
        kind = row['type']
        if kind in ('query_result', 'summary'): continue
        if kind == 'link_summary': row['direction'] = {0:'D2H', 1:'H2D'}[row['destination']]
        if kind == 'ssd_stat': row['utilization'] = row['union_ns']/window if window else None
        if kind in ('service', 'controller_service'):
            row['service_ns'] = row['end_ns']-row['start_ns']
            row['queue_wait_ns'] = row['start_ns']-row.get('ready_ns', row['submitted_ns'])
            if kind == 'service': row['dependency_wait_ns'] = row['ready_ns']-row['submitted_ns']
        if kind == 'credit':
            if row['reserve']: pending[row['id']] = row['bytes']
            else:
                pending[row['id']] -= row['bytes']
                if pending[row['id']] == 0: del pending[row['id']]
        resources.setdefault(kind, ResourceRows(db, kind))
        db.execute('insert into records(kind,row) values(?,?)', (kind, json.dumps(row)))
    db.commit()
    window = summary['end_ns'] - summary['origin_ns']
    stats = dict(queries=len(results), measurement_window_ns=window,
                 throughput_queries_per_second=len(results)*1e9/window if window else None,
                 latency_ns=quantiles([r['latency_ns'] for r in results]),
                 admission_wait_ns=quantiles([r['admission_wait_ns'] for r in results]),
                 offered_latency_ns=quantiles([r['offered_latency_ns'] for r in results]),
                 wall_seconds=wall_seconds, resources=resources,
                 host_wait_definition='dependency_wait=ready-submit; queue_wait=start-ready',
                 ssd_definition='half-open clipped interval union per entity; bus excludes array; utilization=union/window',
                 memory_usage=dict(status='Collected', artifact='sst-measurement.json', scope='wait4 SST child peak RSS; not simultaneous process-tree residency', unit='bytes'))
    stats['credit_bytes_outstanding'] = sum(pending.values())
    with (output/'statistics.json').open('w') as f: stream_json(f, stats)
    csv_rows(output/'queries.csv', [{k:v for k,v in r.items() if k != 'top_k'} for r in results])
    csv_rows(output/'top-k.csv', (dict(query=r['query'], rank=i+1, **v) for r in results for i,v in enumerate(r['top_k'])), ['query','rank','id','distance'])
    csv_rows(output/'statistics.csv', [dict(metric=key, **stats[key]) for key in ('latency_ns','admission_wait_ns','offered_latency_ns')])
    for name, rows in resources.items():
        # Nested local request transaction details belong in JSON/event logs.
        csv_rows(output/(name+'.csv'), ({k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in rows),
                 sorted({k for r in rows for k,v in r.items() if not isinstance(v,(dict,list))}))
    capabilities = dict(device='Extended', request='Extended; query/task linked ordinary/local read events',
                        channel='Extended; actual bus interval union', die='Extended; array/data-out/wait intervals',
                        plane='Unsupported', latch='Unsupported', shared_dram='Unsupported',
                        internal_request_transaction_association='Unsupported; command hook lacks request/transaction participants',
                        algorithm_internal_state_access='Unsupported; research observation only')
    write_json(output/'capabilities.json', capabilities)
    return {key:value for key,value in stats.items() if key != "resources"}
