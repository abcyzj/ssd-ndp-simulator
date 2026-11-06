"""P01 design witnesses only. Not a production package validator or scheduler."""
import copy
import heapq
import json
import math
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, validators
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'examples/p01-contract'
U64 = 2**64-1


def unique(pairs):
    result = {}
    for k,v in pairs:
        if k in result: raise ValueError('duplicate JSON key')
        result[k] = v
    return result


def load(path):
    def nonfinite(value): raise ValueError('nonfinite JSON number: '+value)
    return json.loads(Path(path).read_text(),object_pairs_hook=unique,parse_constant=nonfinite)


SCHEMAS = {s['$id']:s for s in (load(p) for p in [ROOT/'schemas'/n for n in ('ndpsim-contract-v2.schema.json','ndpsim-static-ir-v2.schema.json','ndpsim-measurement-v2.schema.json','ndpsim-cost-profile-v1.schema.json','ndpsim-effective-config-v2.schema.json')])}
REGISTRY = Registry().with_resources((k,Resource.from_contents(s)) for k,s in SCHEMAS.items())
StrictValidator = validators.extend(Draft202012Validator,type_checker=
    Draft202012Validator.TYPE_CHECKER.redefine('integer',lambda checker,x:type(x) is int))


def schema(value, id='urn:ndpsim:contract:2', definition=None):
    spec=SCHEMAS[id] if definition is None else {'$ref':id+'#/$defs/'+definition}
    StrictValidator(spec,registry=REGISTRY).validate(value)


def key(value): return value['query'],value['op'],value['stage']


def check_design(operations, operators):
    """Relational assertions for these in-memory specimens; no package/file IO validation."""
    specs={(s['id'],s['version']):s for s in operators}
    for s in operators:schema(s,definition='operator_spec')
    if len(specs)!=len(operators):raise ValueError('duplicate operator registration')
    nodes={}; ranks={}; stage_specs={}
    for op in operations:
        schema(op,definition='operation')
        spec=specs[(op['operator'],op['operator_version'])]
        for st in op['stages']:
            k=(op['query'],op['id'],st['id']);rank=(op['query'],st['topological_rank'])
            if k in nodes or rank in ranks:raise ValueError('duplicate stable key or rank')
            candidates=[s for s in spec['stages'] if s['kind']==st['kind']]
            if len(candidates)!=1:raise ValueError('unknown stage')
            ss=candidates[0];stage_specs[k]=ss;nodes[k]=st;ranks[rank]=k
            if st['context'] not in ss['contexts'] or st['decomposition_rule'] not in ss['decomposition_rules']:raise ValueError('context/decomposition')
            if set(st['features']) != set(ss['feature_units']):raise ValueError('feature coverage')
            for field,decl in [('work','work_units'),('features','feature_units')]:
                for name,q in st[field].items():
                    if name not in ss[decl] or q['unit']!=ss[decl][name]:raise ValueError('unknown counter or unit')
            outs={o['name']:o for o in ss['outputs']}
            if len({o['port'] for o in st['outputs']})!=len(st['outputs']):raise ValueError('duplicate output')
            for o in st['outputs']:
                if o['port'] not in outs or o['type']!=outs[o['port']]['type']:raise ValueError('output port type')
    for k,st in nodes.items():
        inputs={p['name']:p for p in stage_specs[k]['inputs']};bound=set();edges=set()
        for d in st['dependencies']:
            src=key(d['source']['key']);port=d['source']['port'];dest=d['input']
            edge=(src,port,dest,d['kind'])
            if edge in edges:raise ValueError('duplicate dependency')
            edges.add(edge)
            if src[0]!=k[0] or src not in nodes:raise ValueError('cross-query or missing source')
            if nodes[src]['topological_rank']>=st['topological_rank']:raise ValueError('cycle or invalid rank')
            actual=[o for o in nodes[src]['outputs'] if o['port']==port]
            decl=[p for p in stage_specs[src]['outputs'] if p['name']==port]
            if len(actual)!=1 or len(decl)!=1 or dest not in inputs or (dest in bound and inputs[dest]['cardinality']=='one'):raise ValueError('missing/duplicate port edge')
            if (actual[0]['type'],decl[0]['unit']) != (inputs[dest]['type'],inputs[dest]['unit']):raise ValueError('port type/unit')
            bound.add(dest)
        if any(p['required'] and p['name'] not in bound for p in inputs.values()):raise ValueError('required input')
    versions=set()
    for k,st in sorted(nodes.items(),key=lambda r:r[1]['topological_rank']):
        for v in st['state_versions']:
            signature=(k[0],v['name'],v['after'])
            if v['after']!=v['before']+1 or signature in versions:raise ValueError('state version duplicate/nonmonotone')
            if not any(o['port']==v['output'] and o['version']==v['after'] for o in st['outputs']):raise ValueError('state output version')
            if v['input'] is None:
                if v['before']!=0:raise ValueError('state initial version')
            else:
                ds=[d for d in st['dependencies'] if d['input']==v['input']]
                if len(ds)!=1:raise ValueError('state input edge')
                src=nodes[key(ds[0]['source']['key'])]
                if not any(o['port']==ds[0]['source']['port'] and o['version']==v['before'] for o in src['outputs']):raise ValueError('state before version')
                if (k[0],v['name'],v['before']) not in versions:raise ValueError('missing state producer')
            versions.add(signature)
    for q in {k[0] for k in nodes}:
        if sorted(r for query,r in ranks if query==q)!=list(range(sum(k[0]==q for k in nodes))):raise ValueError('rank gap')
    return nodes


def critical_path(operations,services,fail=None):
    """Hand oracle: independent unlimited resources, explicit input/service/return delays.

    A longest-path calculation, not an SST substitute. P08 must test actual arbitration.
    """
    stages=sorted(((op['id'],st) for op in operations for st in op['stages']),key=lambda r:r[1]['topological_rank'])
    visible={}
    for name,st in stages:
        parents=[d['source']['key']['op'] for d in st['dependencies']]
        if name==fail or any(visible[p] is None for p in parents):visible[name]=None
        else:visible[name]=max([0]+[visible[p] for p in parents])+sum(services[name])
    return visible


def l2(d,chunks):
    if any(type(n) is not int or n<1 for n in chunks):raise ValueError('whole nonempty item partition')
    n=sum(chunks)
    w=dict(elements=d*n,flops=3*d*n,read_bytes=4*d*n+4*d*len(chunks),write_bytes=12*n,output_bytes=12*n,candidate_checks=0,comparisons=0)
    if any(v>U64 for v in w.values()):raise ValueError('overflow')
    return w


def check_table(rule,binding,stage):
    if rule['binding']!=binding:raise ValueError('measured binding')
    if len(set(rule['measurement_sha256']))<3:raise ValueError('three independent artifacts required')
    rows=[r for r in rule['rows'] if key(r['key'])==key(stage['key'])]
    if len(rows)!=1 or rows[0]['work']!=stage['work'] or rows[0]['features']!=stage['features']:raise ValueError('measured key/Work/features')
    return rows[0]['duration_ns']


class ContractDesign(unittest.TestCase):
    def setUp(self):
        self.fork=load(FIX/'fork-join.json');self.sum=load(FIX/'sum-operator.json')
        self.catalog=load(FIX/'graph-stage-catalog.json');self.cases=load(FIX/'hand-cases.json')

    def test_schemas_and_specimens(self):
        for s in SCHEMAS.values():Draft202012Validator.check_schema(s)
        for file,id in [('manifest.json','static-ir:2'),('measurement.json','measurement:2'),('profile.json','cost-profile:1'),('measured-profile.json','cost-profile:1'),('effective-config.json','effective-config:2')]:
            schema(load(FIX/file),'urn:ndpsim:'+id)
        check_design(self.fork['operations'],[self.sum])
        graph=load(FIX/'graph-stages.json');check_design(graph,self.catalog['operators'])
        self.assertEqual(12,sum(len(op['stages']) for op in graph))
        self.assertTrue(any(len(op['stages'])==3 for op in graph))
        self.assertEqual({r['stage'] for r in self.catalog['ledger']},{'init','state','score_prepare','score_l2','merge_append','merge_finalize','select','adjacency_prepare','join','dedup','finish'})

    def test_closed_metadata_and_numeric_negative_cases(self):
        for field,value in [('capture_time_ns',1),('payload_path','base.bin'),('payload',[0]*8),('unknown',0)]:
            op=copy.deepcopy(self.fork['operations'][0]);op['stages'][0][field]=value
            with self.subTest(field=field),self.assertRaises(Exception):schema(op,definition='operation')
        for n in (-1,U64+1,1.0,True):
            op=copy.deepcopy(self.fork['operations'][0]);op['query']=n
            with self.subTest(number=n),self.assertRaises(Exception):schema(op,definition='operation')
        for text in ('{"a":1,"a":2}','{"a":NaN}'):
            with self.assertRaises(ValueError):json.loads(text,object_pairs_hook=unique,parse_constant=lambda v:unique([('x',0),('x',1)]))
        p=load(FIX/'manifest.json');p['format']='ndpsim-static-ir/1'
        with self.assertRaises(Exception):schema(p,'urn:ndpsim:static-ir:2')

    def test_relational_negative_cases(self):
        for change in ('cross','missing','cycle','duplicate','port','unit','feature','required','unknown'):
            ops=copy.deepcopy(self.fork['operations']);spec=copy.deepcopy(self.sum)
            if change=='cross':ops[1]['stages'][0]['dependencies'][0]['source']['key']['query']=8
            if change=='missing':ops[1]['stages'][0]['dependencies'][0]['source']['key']['op']='absent'
            if change=='cycle':ops[0]['stages'][0]['dependencies']=[dict(kind='ORDER',source=dict(key=dict(query=7,op='join',stage='s0'),port='out'),input='in0')]
            if change=='duplicate':ops[1]['stages'][0]['topological_rank']=0
            if change=='port':ops[1]['stages'][0]['dependencies'][0]['source']['port']='absent'
            if change=='unit':ops[0]['stages'][0]['work']['flops']['unit']='B'
            if change=='feature':ops[0]['stages'][0]['features']={}
            if change=='required':spec['stages'][0]['inputs'][0]['required']=True
            if change=='unknown':ops[0]['operator']='missing'
            with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):check_design(ops,[spec])

    def test_parallel_join_and_explicit_order(self):
        f=self.fork
        self.assertEqual(critical_path(f['operations'],f['services']),f['expected_visible_ns'])
        self.assertEqual(critical_path(list(reversed(f['operations'])),f['services']),f['expected_visible_ns'])
        ops=copy.deepcopy(f['operations']);ops[2]['stages'][0]['dependencies'].append(dict(kind='ORDER',source=dict(key=dict(query=7,op='left',stage='s0'),port='out'),input='in1'))
        check_design(ops,[self.sum])
        self.assertEqual(critical_path(ops,f['services']),f['order_left_before_right_visible_ns'])

    def test_all_four_edges_gate_return_and_failure(self):
        for kind in ('DATA','ADDRESS','CONTROL','ORDER'):
            ops=copy.deepcopy(self.fork['operations']);ops[1]['stages'][0]['dependencies'][0]['kind']=kind
            services=copy.deepcopy(self.fork['services']);services['root'][2]+=7
            self.assertEqual(critical_path(ops,services)['join'],28)
            services['root'][0]+=3
            self.assertEqual(critical_path(ops,services)['join'],31)
            result=critical_path(ops,services,fail='root')
            self.assertTrue(all(v is None for v in result.values()))

    def test_zero_service_four_phase_delta_rule(self):
        queue=[];sequence=0;trace=[]
        def enqueue(current,phase,step):
            nonlocal sequence
            t,delta,p=current
            sequence+=1;heapq.heappush(queue,(t,delta+(phase<p),phase,sequence,step))
        enqueue((0,0,0),1,0)
        while queue:
            t,d,p,_,step=heapq.heappop(queue);trace.append([t,d,p])
            if p<3:enqueue((t,d,p),p+1,step)
            elif step==0:enqueue((t,d,p),1,1)
        self.assertEqual(trace,self.cases['zero_service_phases'])
        self.assertEqual([r[0] for r in trace if r[2]==2],self.cases['zero_service_visible_ns'])

    def test_l2_split_and_unsupported_work(self):
        c=self.cases['l2_split']
        self.assertEqual(l2(c['dimensions'],c['semantic_chunks']),c['expected_semantic'])
        self.assertEqual(l2(c['dimensions'],c['physical_chunks']),c['expected_physical'])
        self.assertEqual(sum(c['semantic_chunks']),sum(c['physical_chunks']))
        # A non-additive comparison count or observed duration cannot be distributed as L2.
        def split(rule,work):
            if rule!='graph-l2-items/1' or work.get('comparisons',0) or work.get('duration_ns',0):raise ValueError('undeclared Work decomposition')
        for rule,work in [('none/1',{}),('unknown',{}),('graph-l2-items/1',{'comparisons':7}),('graph-l2-items/1',{'duration_ns':8})]:
            with self.assertRaises(ValueError):split(rule,work)
        with self.assertRaises(ValueError):l2(2**63,[2])

    def test_protocol_and_buffer_independent_byte_oracle(self):
        c=self.cases['messages'];d,b,M,O,lanes,rate=[c[k] for k in ('dimensions','items','max_payload','overhead','lanes','lane_GBps')]
        wire=lambda p:p+((p+M-1)//M)*O if p else 0
        ordinary_h2d=[2,72,2];ordinary_d2h=[('request',4),('data',512),('cq',16)]
        one_h=(sum(ordinary_h2d),sum(wire(p) for p in ordinary_h2d))
        def read_d(size):return 4+size+16,(O+4)+wire(size)+wire(16)
        adj_d=read_d(512);vec_d=read_d(1024)
        host=dict(H2D_payload=2*one_h[0],H2D_wire=2*one_h[1],D2H_payload=adj_d[0]+vec_d[0],D2H_wire=adj_d[1]+vec_d[1])
        h2d=[64+4*d,64+4*b,48];d2h=[48,56+12*b,48]
        controller=dict(H2D_payload=one_h[0]+sum(h2d),H2D_wire=one_h[1]+sum(map(wire,h2d)),D2H_payload=adj_d[0]+sum(d2h),D2H_wire=adj_d[1]+sum(map(wire,d2h)),buffer_bytes=4*d+1024+(1024+4*d)+12*b+12*b,credit_peak=4*d+1024+12*b+64+4*b)
        self.assertEqual(host,c['expected_host']);self.assertEqual(controller,c['expected_controller'])
        self.assertEqual(math.floor(math.ceil(wire(48)/lanes)/rate),19)
        self.assertEqual(wire(0),0)
        self.assertEqual(math.floor(math.ceil(wire(1)/lanes)/16),0)

    def test_state_versions_and_variadic_join(self):
        graph=load(FIX/'graph-stages.json')
        states=sorted((st['state_versions'][0] for op in graph for st in op['stages'] if st['state_versions']),key=lambda s:s['after'])
        self.assertEqual([(s['before'],s['after']) for s in states],[(0,1),(1,2),(2,3)])
        damaged=copy.deepcopy(graph)
        damaged[1]['stages'][0]['state_versions'][0]['before']=0
        with self.assertRaises(ValueError):check_design(damaged,self.catalog['operators'])
        ops=copy.deepcopy(self.fork['operations']);spec=copy.deepcopy(self.sum)
        spec['stages'][0]['inputs'][0]['cardinality']='many'
        ops[-1]['stages'][0]['dependencies'][1]['input']='in0'
        check_design(ops,[spec])
        ops[-1]['stages'][0]['dependencies'].append(ops[-1]['stages'][0]['dependencies'][0])
        with self.assertRaises(ValueError):check_design(ops,[spec])

    def test_cost_rounding_witnesses_and_no_payload_interface(self):
        # Independent examples for the normative formulas; P05 must bind actual implementations.
        self.assertEqual(math.ceil(1+.5*3),3)
        self.assertEqual(next(ns for upper,ns in [(2,0),(5,7)] if 3<=upper),7)
        self.assertEqual(max(1,math.ceil(math.exp(0+.5*(1+1)))),3)
        self.assertEqual(math.ceil(0),0)
        message=dict(protocol='graph-readonly/1',kind='ScoreResult',direction='D2H',query=7,task=1,token=1,chunk=0,count=2,header_bytes=48,logical_bytes=24,payload_bytes=80,wire_bytes=108,size_rule='graph.score-result/1',status='OK')
        schema(message,definition='message')
        message['scores']=[0,0]
        with self.assertRaises(Exception):schema(message,definition='message')
        header=(ROOT/'docs/design-v0.4.5/interfaces.h').read_text()
        self.assertNotIn('content/content.h',header)
        self.assertNotIn('virtual Bytes',header)
        self.assertNotIn('evaluate(',header)
        self.assertIn('CompletionToken',header)

    def test_measurement_quality_and_strict_table_binding(self):
        m=load(FIX/'measurement.json');s=m['samples'][0];c=m['clock']
        self.assertEqual(s['raw_ns'],s['end_cpu_ns']-s['start_cpu_ns'])
        self.assertEqual(s['corrected_ns'],s['raw_ns']-(c['empty_probe_ns']//c['empty_probe_count'])*s['calls'])
        self.assertEqual(s['low_signal'],s['corrected_ns']<s['calls']*c['resolution_ns'])
        rule=load(FIX/'measured-profile.json')['rules'][0]
        # Schema specimen is intentionally unsealed; three equal placeholder hashes are not quality proof.
        with self.assertRaises(ValueError):check_table(rule,m['binding'],s)
        rule['measurement_sha256']=['1'*64,'2'*64,'3'*64]
        self.assertEqual(check_table(rule,m['binding'],s),8)
        for change in ('target','package','work','missing','duplicate'):
            r=copy.deepcopy(rule)
            if change=='target':r['binding']['target']['id']='other'
            if change=='package':r['binding']['package_sha256']='f'*64
            if change=='work':r['rows'][0]['work']['elements']['value']+=1
            if change=='missing':r['rows']=[]
            if change=='duplicate':r['rows']*=2
            with self.subTest(change=change),self.assertRaises(ValueError):check_table(r,m['binding'],s)
        config=load(FIX/'effective-config.json')
        self.assertNotEqual(config['target']['hardware'],config['execution_host']['hardware'])
        self.assertIsNone(config['device'])


if __name__=='__main__':unittest.main(verbosity=2)
