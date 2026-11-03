"""Regenerate the strict V03-1 structural schema and actual incremental capability catalog."""
import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def obj(props,optional=()):
 return dict(type='object',properties=props,required=[k for k in props if k not in optional],additionalProperties=False)
def arr(items,minimum=0):return dict(type='array',items=items,minItems=minimum)
def enum(*values):return dict(enum=list(values))
def ref(n):return {'$ref':'#/$defs/'+n}
def const(v):return {'const':v, **({'type':'integer'} if type(v) is int else {})}
u64=dict(type='integer',minimum=0,maximum=18446744073709551615)
pos=dict(type='integer',minimum=1,maximum=18446744073709551615)
u32=dict(type='integer',minimum=0,maximum=4294967295)
s=dict(type='string',minLength=1)
ident=dict(type='string',pattern='^[A-Za-z][A-Za-z0-9_.-]*$')
hashs=dict(type='string',pattern='^[a-f0-9]{64}$')
d={}
d['extensions']=obj({'non_semantic':const(True),'metadata':{'type':'object'}})
d['identity']=obj({'name':s,'version':s,'sha256':hashs})
d['artifact']=obj({'identity':ref('identity'),'shape':arr(pos,1),'id_mapping_sha256':hashs,'order_sha256':hashs})
d['type']=obj({'name':enum('u32','u64','bool','ids_u32','fp32','scores_id_f64','adjacency','query_state','token'), 'shape':arr(obj({'extent':u64,'dynamic':{'type':'boolean'}}))})
d['scope']=obj({'run':ident,'query':u64,'query_ordinal':u64,'iteration':u64,'semantic_batch':u64,'chunk':u64,'stage':ident},('chunk','stage'))
d['port']=obj({'name':ident,'type':ref('type'),'location':enum('HostMemory','DeviceBuffer','SSDStorage')})
d['object']=obj({'object_id':ident,'object_type':enum('query_vectors','base_vectors','adjacency'),'content_identity':ref('identity'),'content_version':pos,'element_count':pos,'element_type':enum('fp32','u32'),'shape':arr(pos,1),'byte_order':const('little'),'record_schema':enum('fp32-vector-v1','degree-ids-u32-v1'),'logical_layout':const('element-field-v1'),'mutability':const('readonly'),'content_ref':ident,'extensions':ref('extensions')},('extensions',))
d['output_ref']=obj({'producer_op':pos,'port':ident,'type':ref('type')})
d['slice']=obj({'object_id':ident,'content_version':pos,'indices':arr(u32),'indices_ref':ref('output_ref'),'field':enum('vector','neighbors'),'range':obj({'offset':u64,'length':pos})},('indices','indices_ref','range'))
d['value']= {'oneOf':[
 obj({'ref':const('object_slice'),'type':ref('type'),'slice':ref('slice')}),
 obj({'ref':const('op_output'),'type':ref('type'),'output':ref('output_ref')}),
 obj({'ref':const('literal'),'type':ref('type'),'value':{'oneOf':[u64,{'type':'boolean'},dict(type='array',items=u32,minItems=1,maxItems=1)]}}),
 obj({'ref':const('state_ref'),'type':ref('type'),'query':u64,'state_id':ident,'version':pos})]}
d['input']=obj({'port':ident,'value':ref('value')})
d['dependency']=obj({'producer_op':pos,'kind':enum('ADDRESS','DATA','CONTROL','ORDER'),'port':ident,'state_version':pos},('state_version',))
d['access']=obj({'slice':ref('slice'),'mode':const('readonly'),'order':const('input-order')})
d['workload']=obj({'rule':enum('none','ordered-fp64-l2-v1','candidates-v1','select-frontier-v1','dedup-neighbors-v1'),'counts':obj({k:u64 for k in ('elements','flops','read_bytes','write_bytes','output_bytes','candidate_checks','comparisons')})},('counts',))
params={
 'QUERY_READY':obj({}), 'READ_ADJACENCY':obj({}),
 'SCORE_L2':obj({'dimensions':pos,'chunk':pos}),
 'MERGE_CANDIDATES':obj({}), 'SELECT_FRONTIER':obj({'L':pos,'B':pos,'E':pos}),
 'DEDUP_NEIGHBORS':obj({}), 'JOIN':obj({})}
kinds=['EXTERNAL','MEMORY','COMPUTE','CONTROL','CONTROL','CONTROL','SYNC']
ops=list(params)
rules=['none','none','ordered-fp64-l2-v1','candidates-v1','select-frontier-v1','dedup-neighbors-v1','none']
effects=['pure_value','logical_read','logical_read','query_state_transition','query_state_transition','pure_value','pure_value']
ports=[([], [('query','fp32'),('state','query_state'),('ready','token')]),([('ids','ids_u32')],[('adjacency','adjacency')]),([('query','fp32'),('ids','ids_u32')],[('scores','scores_id_f64')]),([('previous','scores_id_f64'),('batch','scores_id_f64')],[('scores','scores_id_f64')]),([('scores','scores_id_f64'),('expanded','query_state')],[('ids','ids_u32')]),([('adjacency','adjacency'),('scored','query_state')],[('ids','ids_u32')]),([], [('done','token')])]
d['operation']={'oneOf':[]}
catalog=[]
for name,kind,rule,effect,(ins,outs) in zip(ops,kinds,rules,effects,ports):
 props={'op_id':pos,'scope':ref('scope'),'kind':const(kind),'operator':const(name),'operator_version':const(1),'placement':enum(*(['host','controller'] if name=='SCORE_L2' else ['host'])),'inputs':arr(ref('input')),'outputs':arr(ref('port')),'deps':arr(ref('dependency')),'access':arr(ref('access')),'params':params[name],'workload':ref('workload'),'effects':arr(enum(effect),1),'schedule':obj({'parallel_group':ident}),'provenance':obj({'source':s,'record_offset':u64}),'extensions':ref('extensions')}
 d['operation']['oneOf'].append(obj(props,('schedule','provenance','extensions')))
 catalog.append(dict(operator=name,operator_version=1,kind=kind,schema_supported=True,execution_supported=name=='JOIN',mapping_status='runtime_async' if name=='JOIN' else 'not_implemented',placements=['host','controller'] if name=='SCORE_L2' else ['host'],inputs=[dict(port=p,type=t,locations=['HostMemory']) for p,t in ins],outputs=[dict(port=p,type=t,locations=['HostMemory']) for p,t in outs],effects=[effect],workload_rule=rule,functional_version={'SCORE_L2':'ordered-fp64-squared-l2-v1','MERGE_CANDIDATES':'ndpsim-candidates-v1','SELECT_FRONTIER':'ranked-frontier-v1','DEDUP_NEIGHBORS':'linear-dedup-v1'}.get(name,name.lower()+'-v1'),cost_version=rule,output_bound={'SCORE_L2':'12 * ids.extent bytes; fixed-order FP32 input / FP64 accumulation; 3*b*d FLOP','READ_ADJACENCY':'ids.extent records, each <= 4 + 4 * max_degree bytes; degree and valid IDs only','MERGE_CANDIDATES':'12 * (previous.extent + batch.extent) bytes','SELECT_FRONTIER':'4 * min(B,E) bytes','DEDUP_NEIGHBORS':'4 * adjacency.valid_ids bytes'}.get(name,'typed metadata/token; no wire payload'),scratch_bound=('controller per chunk b: 64 + 4*b + configured scratch_bytes; separate input sector coverage + 12*b output + 4*d resident query credit; host temporary storage bounded by declared batch shapes' if name=='SCORE_L2' else 'Host-only temporary storage bounded by input/output shapes; no device scratch credit'),notes='logical slices do not grant residency; controller SCORE_L2 uses internal DeviceBuffer and publishes HostMemory only'))
d['manifest']=obj({'ir_version':const('ndpsim-ir/1'),'execution_engine':const('ir'),'execution_mode':const('dynamic'),'workload_type':const('graph_ann'),'frontend':ref('identity'),**{k:ref('artifact') for k in ('dataset','index','truth','queries')},'search':obj({'strategy':const('ndpsim-ranked-expansion-v1'),'entry':u32,**{k:pos for k in ('k','L','B','E','chunk')}}),'operator_set':obj({'catalog_sha256':hashs,'operators':arr(obj({'operator':enum(*ops),'version':const(1)}),1)}),'mapping':obj({'placement':enum('host','controller'),'policy':ref('identity'),'layout':ref('identity'),'profile':ref('identity'),'initial_state':ref('identity'),'cost':ref('identity')}),'execution_scope':obj({'run':ident,'query_identity':const('original-id-and-run-ordinal'),'batch_rule':const('query-iteration-semantic-batch-v1')}),'source':ref('identity'),'dependencies':obj({k:ref('identity') for k in ('mqsim','sst','toolchain','working_tree')}),'diagnostics':obj({'mode':enum('statistics','detail'),'schema':const('ndpsim-ir-diagnostics/1'),'budget_bytes':pos}),'extensions':ref('extensions')},('extensions',))
schema={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:ndpsim:ir:1',**obj({'manifest':ref('manifest'),'objects':arr(ref('object'),1),'operations':arr(ref('operation'))}),'$defs':d}
import argparse
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--check',action='store_true',help='fail on stale checked-in output; do not write')
a=p.parse_args()
for path,value in [('schemas/ndpsim-ir-v1.schema.json',schema),('schemas/ndpsim-ir-v1.capabilities.json',dict(ir_version='ndpsim-ir/1',catalog_version=1,execution_supported=False,operators=catalog))]:
 text=json.dumps(value,indent=2)+'\n'
 if a.check:
  if (root/path).read_text()!=text: p.error('stale generated file: '+path)
 else: (root/path).write_text(text)
