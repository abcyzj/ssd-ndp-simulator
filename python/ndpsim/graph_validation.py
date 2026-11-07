"""Independent graph mapping ledger formulas; no simulator imports.

The shared service formulas are checked against actual intervals; address, wire,
credit and Work decomposition rules below are independently implemented.
"""
import copy
import heapq
from collections import Counter, defaultdict
from .execution import load
from .static_ir import decode, file_hash, require


def effective(info, c, count=None):
    s = info['stage']; w = copy.deepcopy(s['work']); f = copy.deepcopy(s['features'])
    offload = c['placement'] == 'controller'
    if s['kind'] in ('score_prepare','adjacency_prepare'):
        f['state_objects']['value'] = 0
        if s['kind'] == 'score_prepare':
            n, d = f['vectors']['value'], f['dimensions']['value']
            f['copied_bytes']['value'] = f['decoded_bytes']['value'] = 0 if offload else 4*n*d
    if s['kind'] == 'merge_append' and offload: f['released_vectors']['value'] = 0
    if count is not None:
        d = f['dimensions']['value']; f['vectors']['value'] = count
        values = dict(elements=count*d, flops=3*count*d, read_bytes=4*d*(count+1), write_bytes=12*count, output_bytes=12*count, comparisons=0, candidate_checks=0)
        require(set(w) == set(values), 'L2 Work counters')
        for name, value in values.items(): w[name]['value'] = value
    return w, f


def plan(accesses, layout):
    objects = {(r['object'],r['version']):r['extents'] for r in layout['objects']}
    reads = []; sectors = set(); logical = 0
    page_sectors = layout['namespace']['page_bytes']//512
    for a in accesses:
        logical += a['length_bytes']; pos = a['offset_bytes']; end = pos+a['length_bytes']
        for e in objects[a['object'],a['version']]:
            lo = max(pos,e['logical_offset_bytes']); hi = min(end,e['logical_offset_bytes']+e['length_bytes'])
            if hi <= lo: continue
            physical = e['physical_offset_bytes']+lo-e['logical_offset_bytes']
            begin, stop = physical//512, (physical+hi-lo+511)//512
            sectors.update(range(begin, stop))
            pos = hi
        require(pos == end, 'layout access not covered')
    for sector in sorted(sectors):
        if reads and reads[-1][0]+reads[-1][1] == sector and reads[-1][0]//page_sectors == sector//page_sectors:
            begin,n = reads[-1]; reads[-1] = (begin,n+1)
        else: reads.append((sector,1))
    return reads, logical, len(sectors)*512


def input_gates(expected_intervals, actual_points):
    """Match completion points to owning stage intervals in O(N log N)."""
    require(set(expected_intervals)==set(actual_points), 'input completion coverage')
    for identity, intervals in expected_intervals.items():
        intervals=sorted(intervals); points=sorted(actual_points[identity]); ready=[]; i=0
        require(len(intervals)==len(points), 'input completion multiplicity')
        for point in points:
            while i<len(intervals) and intervals[i][0]<=point:
                heapq.heappush(ready,intervals[i][1]);i+=1
            require(ready and heapq.heappop(ready)>=point, 'input/compute gate')
        require(i==len(intervals) and not ready, 'missing input completion')


class PcieLedger:
    """Validate each packet lifecycle as the trace arrives, retaining only totals."""
    def __init__(self):
        self.pending = {}
        self.completed = set()
        self.payload_by_layer = Counter()
        self.platform_sizes = Counter()

    def add(self, row):
        packet = row['id']
        event = row['event']
        if event == 'enqueue':
            require(packet not in self.pending and packet not in self.completed, 'duplicate PCIe event')
            self.pending[packet] = (row, None)
            return
        require(packet in self.pending, 'PCIe event without enqueue')
        enqueue, start = self.pending[packet]
        for name in ('payload', 'wire', 'destination', 'layer', 'service'):
            require(enqueue[name] == row[name], 'PCIe packet identity')
        if event == 'start':
            require(start is None, 'duplicate PCIe event')
            require(enqueue['time'] <= row['time'], 'PCIe timing gate')
            self.pending[packet] = (enqueue, row['time'])
            return
        require(event == 'arrive' and start is not None, 'incomplete PCIe packet')
        require(row['time'] - start == row['service'], 'PCIe timing gate')
        layer, payload, wire = row['layer'], row['payload'], row['wire']
        expected = 32 if layer == 'sq_fetch' else payload + 28 * math_ceil(payload, 128)
        require(wire == expected, 'PCIe wire bytes')
        destination = row['destination']
        self.payload_by_layer[layer, destination] += payload
        if layer == 'platform':
            self.platform_sizes[destination, payload, wire] += 1
        del self.pending[packet]
        self.completed.add(packet)


def check(rows, expected, c, directory, stages, pcie):
    from .validation import key, unique
    offload = c['placement'] == 'controller'
    require(c['placement'] in ('host','controller'), 'unsupported graph placement')
    layout_path = directory/'inputs/layout.json'; layout = load(layout_path)
    require(file_hash(layout_path) == rows[0]['layout_sha256'], 'layout identity')
    require(file_hash(directory/'inputs/device.xml') == rows[0]['device_sha256'], 'device identity')
    logical = unique((r for r in rows if r['type']=='logical'), 'logical stage')
    require(set(logical) == set(expected), 'logical stage coverage')
    ordinary_plans = Counter(); ordinary_reads = Counter(); local_reads = Counter(); vector_bytes = 0
    max_item = 0
    anchors = {}
    for k, info in expected.items():
        s = info['stage']; require(logical[k]['work'] == s['work'] and logical[k]['features'] == s['features'], 'logical Work/features')
        if s['kind'] in ('init','finish'): anchors[k[0],s['kind']]=k
        if s['kind'] == 'score_prepare':
            for a in s['accesses']: max_item = max(max_item, plan([a],layout)[2])
    bound = c['resources']['block_bound']; per_block = (bound-64)//(max_item+16)
    require(per_block > 0 and rows[0]['items_per_block'] == per_block, 'capacity block decomposition')
    ordinary_gates = []; chunks = {}; calls = []; expected_service = set(); demand_groups = defaultdict(list)
    for r in rows:
        if r['type'] == 'demand': demand_groups[key(r['detail'])].append(r)
    require(set(demand_groups) == set(expected), 'demand coverage')
    services = {}
    for r in rows:
        if r['type'] in ('cpu','controller_compute'):
            k = decode(r['key'].encode()); ident = (*key(k),k.get('physical_chunk'))
            require(ident not in services, 'duplicate service'); services[ident] = r
    for k, info in expected.items():
        s = info['stage']; ds = demand_groups[k]
        if s['kind'] in ('init','adjacency_prepare') or (s['kind']=='score_prepare' and not offload):
            reads, logical_bytes, sector_bytes = plan(s['accesses'],layout)
            ordinary_plans[s['kind'],logical_bytes,sector_bytes] += 1
            ordinary_reads.update((s['kind'],l,n) for l,n in reads)
            ordinary_gates.extend((k,s['kind'],l,n) for l,n in reads)
            if s['kind']=='score_prepare': vector_bytes += sector_bytes
        if offload and s['kind']=='score_l2':
            vector_inputs = [d for d in s['dependencies'] if d['input'] == 'vectors']
            require(len(vector_inputs) == 1, 'L2 prepare binding')
            dep = vector_inputs[0]
            prepare_key = key(dep['source']['key'])
            require(dep['kind'] == 'DATA' and dep['source']['port'] == 'vectors' and
                    prepare_key[:2] == k[:2] and prepare_key in expected, 'L2 prepare dependency')
            prepare = expected[prepare_key]['stage']
            require(prepare['kind'] == 'score_prepare' and prepare['chunk'] == s['chunk'], 'L2 prepare chunk')
            accesses = prepare['accesses']
            n = s['features']['vectors']['value']; d = s['features']['dimensions']['value']
            require(len(accesses) == n and s['decomposition_rule']=='graph-l2-items/1', 'L2 indivisible item decomposition')
            require(all(a['object']=='vectors' and a['length_bytes']==4*d for a in accesses), 'complete vector accesses')
            require(effective(info,c,n)[0] == s['work'], 'captured L2 Work')
            require(len(ds) == math_ceil(n,per_block), 'physical chunk coverage')
            for begin, r in zip(range(0,n,per_block), ds):
                count = min(n-begin,per_block); reads, lb, sb = plan(accesses[begin:begin+count],layout)
                require(r.get('physical') is True and r['semantic_chunk'] == s['chunk'], 'physical/semantic chunk')
                chunk = r['physical_chunk']; require(chunk not in chunks, 'duplicate physical chunk')
                require(r['reads'] == [dict(lba=l,sectors=z) for l,z in reads], 'local physical address/size mismatch')
                require(sb+64+16*count <= bound, 'block buffer bound')
                ident = (*k,chunk); require(ident in services, 'missing controller service')
                require(services[ident]['type']=='controller_compute', 'wrong compute location')
                chunks[chunk] = dict(key=k,count=count,dimension=d,sector_bytes=sb,reads=reads)
                local_reads.update((*k,l,z) for l,z in reads)
                expected_service.add(ident); calls.append((k,r['detail'],services[ident],count))
        else:
            require(len(ds) == 1 and ds[0].get('physical') is False, 'unique logical demand')
            ident = (*k,None); require(ident in services and services[ident]['type']=='cpu', 'missing Host service')
            expected_service.add(ident); calls.append((k,ds[0]['detail'],services[ident],None))
    require(set(services) == expected_service, 'orphan compute service')
    require(Counter((r['kind'],r['logical_bytes'],r['sector_bytes']) for r in rows if r['type']=='ordinary_plan') == ordinary_plans, 'ordinary plan ledger')
    for kind in ('ordinary_submit','ordinary_read'):
        require(Counter((r['kind'],r['lba'],r['sectors']) for r in rows if r['type']==kind) == ordinary_reads, 'ordinary read ledger')
    require(Counter((*key(r),r['lba'],r['sectors']) for r in rows if r['type']=='local_read') == local_reads, 'local read ledger')
    require(sum(r['bytes'] for r in rows if r['type']=='nand_transaction') == sum(x['sector_bytes'] for x in chunks.values()), 'NAND transaction bytes')
    # Match completions without quadratic scans or assuming capture line order.
    gates=defaultdict(list); points=defaultdict(list)
    for k,kind,lba,sectors in ordinary_gates:
        gates[kind,lba,sectors].append((stages[k]['submitted'],services[(*k,None)]['start']))
    for r in rows:
        if r['type']=='ordinary_read':points[r['kind'],r['lba'],r['sectors']].append(r['completed'])
    input_gates(gates,points)
    gates=defaultdict(list);points=defaultdict(list)
    for chunk,x in chunks.items():
        k=x['key']
        for lba,sectors in x['reads']:
            gates[(*k,lba,sectors)].append((stages[k]['submitted'],services[(*k,chunk)]['start']))
    for r in rows:
        if r['type']=='local_read':points[(*key(r),r['lba'],r['sectors'])].append(r['completed'])
    input_gates(gates,points)
    summary = rows[-1]
    require(summary['ordinary_requests'] == sum(ordinary_reads.values()) and summary['local_requests'] == sum(local_reads.values()) and summary['vector_d2h'] == (0 if offload else vector_bytes), 'device totals/vector D2H ledger')
    require(not pcie.pending, 'incomplete PCIe packet')
    ordinary_count = sum(ordinary_reads.values())
    ordinary_payload = {('sq_entry', 1):72*ordinary_count, ('cq_entry', 0):16*ordinary_count,
                        ('doorbell', 1):4*ordinary_count, ('sq_fetch', 0):4*ordinary_count,
                        ('data', 0):sum(z*512*v for (_,_,z),v in ordinary_reads.items())}
    actual_payload = {k:v for k,v in pcie.payload_by_layer.items() if k[0] != 'platform'}
    require(Counter(actual_payload) == Counter(ordinary_payload), 'ordinary PCIe directional byte ledger')
    protocol = [r for r in rows if r['type']=='protocol']
    require(Counter((1-r['direction'],r['payload'],r['wire']) for r in protocol) == pcie.platform_sizes, 'protocol/PCIe ownership ledger')
    results = unique((r for r in protocol if r['kind']==3), 'result', lambda r:r['chunk'])
    commands = unique((r for r in protocol if r['kind']==2), 'execute', lambda r:r['chunk'])
    require(set(results)==set(commands)==set(chunks), 'command/result chunk coverage')
    for r in protocol:
        kind=r['kind']; require(kind in range(6), 'unknown protocol kind')
        require(r['direction'] == (0 if kind in (0,2,4) else 1), 'protocol direction')
        logical = 4*r['dimension'] if kind==0 else 4*r['count'] if kind==2 else 12*r['count'] if kind==3 else 0
        payload = (64 if kind in (0,2) else 56 if kind==3 else 48)+logical
        require((r['logical'],r['payload'],r['wire']) == (logical,payload,payload+28*math_ceil(payload,128)), 'protocol size ledger')
        require(r['submitted'] <= r['arrived'], 'protocol arrival gate')
    for chunk,x in chunks.items():
        command, result = commands[chunk], results[chunk]; k=x['key']
        require(command['count']==result['count']==x['count'] and command['dimension']==x['dimension'], 'protocol item count')
        service=services[(*k,chunk)]
        require(command['arrived'] <= service['submitted'] <= service['end'] <= result['submitted'] and result['arrived'] <= stages[k]['completed'], 'command/service/result gate')
    q = len({k[0] for k in expected})
    require(Counter(r['kind'] for r in protocol if r['kind'] not in (2,3)) == (Counter({0:q,1:q,4:q,5:q}) if offload else Counter()), 'Open/ACK/Close/drain coverage')
    buffer_calls = Counter(); credits = Counter()
    for chunk,x in chunks.items():
        k=x['key']; prefix=':'.join(map(str,k)); n=x['count']; sb=x['sector_bytes']; d=x['dimension']
        for suffix,amount in [('input-write',sb),('compute-read',sb+4*d+4*n),('result-write',12*n),('result-read',12*n)]:
            buffer_calls[prefix+':'+suffix,amount] += 1
        credits[True,sb+64+16*n,commands[chunk]['submitted']] += 1
        credits[False,sb+64+4*n,services[(*k,chunk)]['end']] += 1
        credits[False,12*n,results[chunk]['arrived']] += 1
    open_rows = unique((r for r in protocol if r['kind']==0),'Open',lambda r:r['query'])
    ack_rows = unique((r for r in protocol if r['kind']==5),'CloseAck',lambda r:r['query'])
    if offload:
        require(set(open_rows)==set(ack_rows)=={k[0] for k in expected}, 'session query coverage')
    for query,r in open_rows.items():
        amount=4*r['dimension']
        buffer_calls['query-write:'+str(query),amount] += 1
        credits[True,amount,r['submitted']] += 1
        credits[False,amount,ack_rows[query]['arrived']] += 1
        init=stages[anchors[query,'init']]
        finish=stages[anchors[query,'finish']]
        require(r['arrived'] <= init['completed'] and ack_rows[query]['arrived'] <= finish['completed'], 'session return/visibility gate')
    actual_buffer = Counter()
    for r in rows:
        if r['type']=='buffer_access':
            name = r['key']
            if name.startswith('query-write:'): name=':'.join(name.split(':')[:2])
            actual_buffer[name,r['bytes']] += 1
    require(actual_buffer==buffer_calls, 'buffer traffic/service coverage')
    require(Counter((r['reserve'],r['bytes'],r['time']) for r in rows if r['type']=='credit')==credits, 'credit ownership/lifetime ledger')
    used=peak=0
    for r in rows:
        if r['type']=='credit':
            used += r['bytes'] if r['reserve'] else -r['bytes']
            require(used == r['used'] and 0 <= used <= c['resources']['capacity_bytes'], 'buffer credit accounting');peak=max(peak,used)
        if r['type']=='buffer_access': require(r['duration_ns']==math_ceil(r['bytes'],c['resources']['staging_bytes_per_ns']), 'buffer service formula')
    require(used==0, 'buffer not drained')
    return calls, dict(ordinary_requests=ordinary_count,local_requests=sum(local_reads.values()),vector_d2h=summary['vector_d2h'],physical_chunks=len(chunks),buffer_peak_bytes=peak,protocol_messages=len(protocol),hardware_validation='configured_unvalidated')


def math_ceil(n,d):
    return (n+d-1)//d
