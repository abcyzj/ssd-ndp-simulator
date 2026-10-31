"""Independent batch replay and storage/PCIe accounting for paired graph runs."""
from contextlib import ExitStack
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import struct
from ann_data import read_dataset, write_json


def scores_equal(actual, expected):
    assert [v['id'] for v in actual] == [v['id'] for v in expected], 'score IDs/order differ'
    for a, b in zip(actual, expected):
        assert abs(a['distance']-b['distance']) <= 1e-9+1e-7*abs(b['distance']), 'distance tolerance'


def event_rows(path):
    with (Path(path)/'events.jsonl').open() as stream:
        for line in stream:
            yield json.loads(line)


def inspect(path):
    with ExitStack() as files:
        return _inspect(path, files)


def _inspect(path, files):
    path = Path(path)
    c = json.loads((path/'config.json').read_text())
    manifest = json.loads((path/'manifest.json').read_text())
    data = Path(manifest['data'])
    identity = manifest.get('input_identity')
    graph_path = data/'adjacency.u32'
    if identity:
        from simulation_input import load_v2, FileRows
        m, paths, current = load_v2(data, c, path)
        assert current == identity, 'input identity changed'
        vectors = files.enter_context(FileRows(paths['vector_file'], m['dimensions']))
        queries = files.enter_context(FileRows(paths['query_file'], m['dimensions']))
        graph_path = Path(paths['graph_file'])
    else:
        m, vectors, queries = read_dataset(data)
    grouped = defaultdict(list)
    keep = {'query_result','score_visible','decision','summary','path_summary','service',
            'message','local_request','local_storage','controller_service','credit','read_submit'}
    link_pending = {}; closed_links = set(); platforms = {}; native = Counter()
    link_totals = Counter()
    for e in event_rows(path):
        if e['type']=='link':
            assert e['id'] not in closed_links, 'reused completed link ID'
            phases = link_pending.setdefault(e['id'], {})
            assert e['phase'] not in phases
            phases[e['phase']] = e
            if e['phase']=='arrive':
                assert set(phases)=={'enqueue','start','arrive'}
                en, st, ar = [phases[k] for k in ('enqueue','start','arrive')]
                assert en['time_ns']<=st['time_ns']<=ar['time_ns']
                assert ar['time_ns']-st['time_ns']==st['service_ns']==(st['wire']+3)//4
                wire=32 if st['message_type']==0 else st['payload']+28*((st['payload']+127)//128)
                assert st['wire']==wire and ar['wire']==wire
                if ar['message_type']==3:
                    assert ar['platform_id'] not in platforms
                    platforms[ar['platform_id']]=ar
                    link_totals['platform_wire']+=ar['wire']
                else:
                    native[(ar['message_type'],ar['destination'],ar['payload'])]+=1
                    link_totals['native_wire']+=ar['wire']
                if ar['message_type']==1 and ar['destination']==0 and ar['payload']>=512:
                    link_totals['ordinary_dma']+=ar['payload']
                link_totals['wire']+=ar['wire'];link_totals['transfers']+=1
                closed_links.add(e['id']);del link_pending[e['id']]
        elif e['type'] in keep:
            grouped[e['type']].append(e)
    assert not link_pending, 'incomplete link transfer'
    # Completion order across queries is resource dependent; preserve within-query order.
    grouped['query_result'].sort(key=lambda e:e['query'])
    by = lambda kind: grouped[kind]
    visible_times = {(e['query'], s['id']):e['time_ns'] for e in by('score_visible') for s in e['scores']}
    assert len(by('query_result'))==m['queries']
    assert [e['query'] for e in by('query_result')]==list(range(1,m['queries']+1))
    assert all(1<=e['query']<=m['queries'] for kind in ('message','local_request','local_storage') for e in by(kind))
    from simulation_input import FileRows
    graph = files.enter_context(FileRows(graph_path, m['degree']+1, adjacency=True))
    cfg, decisions, expected_nodes, chunks = c['search'], [], {}, {}
    for q, query in enumerate(queries, 1):
        seen, expanded = {}, set()
        batches = [[cfg['entry']]]
        def score(nodes):
            for node in nodes:
                distance = 0.0
                for a, b in zip(query, vectors[node]):
                    delta = a-b
                    distance = distance+delta*delta
                assert node not in seen
                seen[node] = distance
        score(batches[0])
        while True:
            ranking = [dict(id=i, distance=d) for i, d in sorted(seen.items(), key=lambda x:(x[1], x[0]))]
            chosen = [v['id'] for v in ranking[:cfg['width']] if v['id'] not in expanded][:min(cfg['batch'], cfg['budget']-len(expanded))]
            decisions.append(dict(query=q, candidates=ranking[:cfg['width']], expand=chosen))
            if not chosen:
                result = next(e for e in by('query_result') if e['query']==q)
                scores_equal(result['top_k'], ranking[:cfg['k']])
                assert result['expanded']==len(expanded) and result['insufficient']==(len(seen)<cfg['k'])
                assert result['latency_ns']==result['end_ns']-result['start_ns']>0
                break
            expanded.update(chosen)
            fresh = sorted({i for node in chosen for i in graph[node]}-seen.keys())
            if fresh:
                batches.append(fresh)
                score(fresh)
        expected_nodes[q] = [n for batch in batches for n in batch]
        if c['placement']=='controller':
            cc = c['controller']
            per = cc['vector_sector_bound']+16
            size = min(cfg['chunk'], (cc['capacity_bytes']-4*m['dimensions']-64-cc['scratch_bytes'])//per)
            assert size>0
            chunks[q] = [batch[i:i+size] for batch in batches for i in range(0, len(batch), size)]
            visible = [e for e in by('score_visible') if e['query']==q]
            assert len(visible)==len(batches)
            for e, batch in zip(visible, batches):
                scores_equal(e['scores'], [dict(id=n, distance=seen[n]) for n in batch])
    actual = sorted(by('decision'), key=lambda e:e['query'])
    assert len(actual)==len(decisions)
    for a, b in zip(actual, decisions):
        assert a['query']==b['query'] and a['expand']==b['expand']
        scores_equal(a['candidates'], b['candidates'])
    # Read associations, address ranges, payload and completion causality.
    vs = (m['dimensions']*4+511)//512
    ads = (4+4*m['degree']+511)//512
    pending, ordinary, useful = {}, 0, 0
    issued_adj, expected_adj = [], [(d['query'], n) for d in decisions for n in d['expand']]
    host_vectors = defaultdict(list)
    read_times = defaultdict(list)
    for e in event_rows(path):
        if e['type']=='read_submit':
            assert e['request'] not in pending
            pending[e['request']] = e
            n = e['id']
            base = n*vs if e['kind']=='vector' else m['count']*vs+n*ads
            length = m['dimensions']*4 if e['kind']=='vector' else 4+4*len(graph[n])
            assert base<=e['lba'] and e['lba']+e['sectors']<=base+(length+511)//512
            ordinary += 512*e['sectors']
            if e['lba']==base:
                useful += length
                if e['kind']=='adjacency':issued_adj.append((e['query'], n))
                else:host_vectors[e['query']].append(n)
        elif e['type']=='read_complete':
            r = pending.pop(e['request'])
            assert (r['query'], r['task'], r['lba'], r['sectors'])==(e['query'], e['task'], e['lba'], e['sectors'])
            assert e['time_ns']>=r['time_ns']
            read_times[(e['query'], r['kind'], r['id'])].append(e['time_ns'])
    assert not pending and sorted(issued_adj, key=lambda x:x[0])==expected_adj
    for a in actual:
        for v in a['candidates']:
            if c['placement']=='host':
                assert max(read_times[(a['query'], 'vector', v['id'])])<=a['time_ns']
            else:
                assert visible_times[(a['query'], v['id'])]<=a['time_ns']
    summary = by('summary')[0]
    assert summary['live_results']==summary['live_content']==0
    assert summary['sector_bytes']==ordinary and summary['useful_bytes']==useful
    assert by('path_summary')[0]['ordinary_dma_bytes']==ordinary
    for e in by('service'):
        h = c['host']; units = e['flops'] if e['kind']=='distance' else e['checks']*h['check_units']+e['comparisons']*h['comparison_units']
        rate = h[e['kind']+'_rate']
        assert e['end_ns']-e['start_ns']==h[e['kind']+'_setup']+(units+rate-1)//rate
    # Every actual link transfer has precisely one enqueue/start/arrival and one fee.
    assert len(platforms)==len(by('message'))
    local_sectors = 0
    if c['placement']=='host':
        assert not by('message') and not by('local_request')
        assert dict(host_vectors)==expected_nodes
    else:
        assert not host_vectors, 'controller issued ordinary vector DMA'
        assert not any(e['kind']=='distance' for e in by('service'))
        all_chunks = [(q, nodes) for q, batches in chunks.items() for nodes in batches]
        msgs = by('message')
        for q in range(1, len(queries)+1):
            qm = [e for e in msgs if e['query']==q]
            assert Counter(e['kind'] for e in qm)==Counter({1:1, 2:1, 3:len(chunks[q]), 4:len(chunks[q]), 5:1, 6:1})
            assert next(e for e in qm if e['kind']==1)['payload']==64+4*m['dimensions']
            for msg, nodes in zip([e for e in qm if e['kind']==3], chunks[q]):
                assert msg['payload']==64+4*len(nodes) and msg['useful']==4*len(nodes)
                reply = next(e for e in qm if e['kind']==4 and e['task']==msg['task'])
                assert reply['payload']==56+12*len(nodes) and reply['useful']==12*len(nodes)
                storage = next(e for e in by('local_storage') if e['task']==msg['task'])
                requests = [e for e in by('local_request') if e['task']==msg['task']]
                observed = Counter(lba for e in requests for lba in range(e['lba'], e['lba']+e['sectors']))
                expected = Counter(lba for n in nodes for lba in range(n*vs, (n+1)*vs))
                assert observed==expected and storage['sector_bytes']==sum(observed.values())*512
                assert storage['requests']==len(requests) and storage['transactions']==sum(len(e['transactions']) for e in requests)
                assert msg['arrived_ns']<=storage['submitted_ns']<=storage['completed_ns']<=reply['submitted_ns']
                for e in requests:
                    assert storage['submitted_ns']<=e['submitted_ns']<=e['completed_ns']<=storage['completed_ns']
                    assert sum(t['bytes'] for t in e['transactions'])==512*e['sectors']
                    assert all(e['submitted_ns']<=t['time_ns']<=e['completed_ns'] for t in e['transactions'])
                arithmetic = next(e for e in by('controller_service') if e['task']==msg['task'] and e['stage']=='arithmetic')
                assert arithmetic['bytes']==len(nodes)*m['dimensions']*3
                assert any(e['query']==q and e['time_ns']>=reply['arrived_ns'] and set(nodes)<={s['id'] for s in e['scores']} for e in by('score_visible'))
            result = next(e for e in by('query_result') if e['query']==q)
            close = next(e for e in qm if e['kind']==5)
            assert close['submitted_ns']>=result['end_ns']
        for msg in msgs:
            ar = platforms[msg['id']]
            assert ar['payload']==msg['payload'] and ar['time_ns']==msg['arrived_ns']>=msg['submitted_ns']
            assert ar['destination']==(1 if msg['kind'] in (1,3,5) else 0)
            if msg['kind'] in (2,5,6):assert msg['payload']==48 and msg['useful']==0
            if msg['kind']==1:assert msg['useful']==4*m['dimensions']
        used, peak, credits = 0, 0, Counter()
        for e in by('credit'):
            amount = e['bytes'] if e['reserve'] else -e['bytes']
            credits[e['id']] += amount; used += amount
            assert used==e['used'] and 0<=used<=c['controller']['capacity_bytes'] and credits[e['id']]>=0
            peak=max(peak, used)
        assert used==0 and not any(credits.values())
        for e in by('controller_service'):
            cc=c['controller'];rate=cc['flops_per_ns'] if e['stage']=='arithmetic' else cc['staging_bytes_per_ns']
            setup=cc['launch_ns'] if e['stage']=='arithmetic' else 0
            assert e['end_ns']-e['start_ns']==setup+(e['bytes']+rate-1)//rate
        local_sectors=sum(e['sector_bytes'] for e in by('local_storage'))
        assert local_sectors==sum(len(v)*vs*512 for v in expected_nodes.values())
    # Native NVMe uses WRITE_REQ for both DMA and CQ. All command/CQ fees remain in native_wire.
    assert link_totals['ordinary_dma']==ordinary
    count=len(by('read_submit'))
    expected=Counter({(1,1,2):2*count, (0,0,4):count, (2,1,72):count, (1,0,16):count})
    expected.update((1,0,e['sectors']*512) for e in by('read_submit'))
    assert native==+expected, 'native NVMe DMA/command/CQ accounting'
    return dict(placement=c['placement'], decisions=actual, results=by('query_result'), dataset=identity or m, config=c,
                ledger=dict(ordinary_data_bytes=ordinary, local_vector_bytes=local_sectors,
                            vector_d2h_bytes=0 if c['placement']=='controller' else sum(len(v)*vs*512 for v in expected_nodes.values()),
                            platform_payload=sum(e['payload'] for e in by('message')), platform_useful=sum(e['useful'] for e in by('message')),
                            platform_wire=link_totals['platform_wire'],
                            native_wire=link_totals['native_wire'],
                            total_wire=link_totals['wire'], link_transfers=link_totals['transfers']))


def compare(host, controller):
    a, b = inspect(host), inspect(controller)
    assert a['placement']=='host' and b['placement']=='controller'
    assert a['dataset']==b['dataset'] and a['config']['search']==b['config']['search'] and a['config']['host']==b['config']['host']
    assert len(a['decisions'])==len(b['decisions'])
    for x, y in zip(a['decisions'], b['decisions']):
        assert x['query']==y['query'] and x['expand']==y['expand']
        scores_equal(x['candidates'], y['candidates'])
    for x, y in zip(a['results'], b['results']):
        assert x['query']==y['query'] and x['expanded']==y['expanded']
        scores_equal(x['top_k'], y['top_k'])
    return dict(status='passed', batches=len(a['decisions']), queries=len(a['results']), distance_tolerance=dict(abs=1e-9, rel=1e-7),
                host=a['ledger'], controller=b['ledger'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('host', type=Path);p.add_argument('controller', type=Path);p.add_argument('--output', type=Path)
    a=p.parse_args();result=compare(a.host, a.controller)
    if a.output:write_json(a.output, result)
    print(json.dumps(result, indent=2))
