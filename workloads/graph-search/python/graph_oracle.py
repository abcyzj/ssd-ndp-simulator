"""Independent search/Work/DAG reconstruction. No native calls or capture parsing.

Uses bounded positional reads, Python sets/sorting, rank and inversion formulas. Only metadata schema
and contract declarations are shared with the producer.
"""
import bisect
import hashlib
import os
import struct
from pathlib import Path
from ndpsim.static_ir import require
from graph_contract import WORK_UNITS

EMPTY=hashlib.sha256(b'').hexdigest()
def packed_scores(values):return b''.join(struct.pack('<Id',i,d) for i,d in values)
def packed_ids(values):return b''.join(struct.pack('<I',i) for i in values)
def quantities(values=None):return {k:dict(value=(values or {}).get(k,0),unit=u) for k,u in WORK_UNITS.items()}
def work(**kw):return quantities(kw)

class Payload:
    def __init__(self,config):
        self.config=config;self.files={};self.sizes={}
        try:
            for k in ('vectors','queries','graph','truth','query_ids'):
                f=Path(config['files'][k]['path']).open('rb',buffering=0);self.files[k]=f;self.sizes[k]=os.fstat(f.fileno()).st_size
        except BaseException:self.close();raise
    def close(self):
        for f in self.files.values():f.close()
    def read(self,k,offset,size):
        # Random mmap accesses retain an ever-growing resident payload working
        # set across queries. Read just the requested vector/row instead.
        require(0<=offset<=self.sizes[k] and 0<=size<=self.sizes[k]-offset,'oracle read range')
        data=os.pread(self.files[k].fileno(),size,offset)
        require(len(data)==size,'oracle truncated payload')
        return data
    def vector(self,k,i):
        d=self.config['shape']['dimensions'];return struct.unpack('<'+'f'*d,self.read(k,i*d*4,d*4))
    def row(self,i):
        r=self.config['shape']['degree'];off=i*(r+1)*4;n,=struct.unpack('<I',self.read('graph',off,4))
        require(n<=r,'oracle adjacency degree');v=list(struct.unpack('<'+'I'*n,self.read('graph',off+4,n*4)));require(all(x<self.config['shape']['count'] for x in v),'oracle adjacency range');return v

def expected_query(payload,qi):
    c=payload.config;s=c['search'];shape=c['shape'];d=shape['dimensions'];n=shape['count'];stride=4*d
    qid,=struct.unpack('<I',payload.read('query_ids',qi*4,4))
    query=payload.vector('queries',qi);query_bytes=payload.read('queries',qi*stride,stride)
    operations=[];rank=0;previous=None;state=None;version=0;init=None;candidate=None
    def op(name):
        o=dict(query=qid,id='op'+str(len(operations)),operator='graph.'+name,operator_version='2',semantic_work=work(),stages=[]);operations.append(o);return o
    def key(o,t):return dict(query=qid,op=o['id'],stage=t['id'])
    def stage(o,kind,w=None,features=None,chunk=None):
        nonlocal rank,previous
        t=dict(id=kind+'.'+str(len(o['stages'])),kind=kind,topological_rank=rank,dependencies=[],work=w or work(),features={k:dict(value=v,unit='B' if 'bytes' in k else 'count') for k,v in (features or {}).items()},context='resident_random/2',chunk=chunk,segment=len(o['stages']),accesses=[],outputs=[dict(port='out',type='token',logical_bytes=0,version=0,digest=EMPTY)],state_versions=[],decomposition_rule='none/1')
        if previous:edge(t,previous,'out','in','ORDER')
        require(rank<100000,'oracle per-query stage budget');rank+=1;previous=key(o,t);o['stages'].append(t);return t
    def edge(t,k,port,dest,kind='DATA'):t['dependencies'].append(dict(kind=kind,source=dict(key=k,port=port),input=dest))
    def output(t,port,typ,data,ver=0):t['outputs'].append(dict(port=port,type=typ,logical_bytes=len(data),version=ver,digest=hashlib.sha256(data).hexdigest()))
    def access(obj,off,length):return dict(object=obj,version=0,offset_bytes=off,length_bytes=length)
    def state_output(t,before,after,inp):
        t['outputs'].append(dict(port='state',type='graph.state',logical_bytes=0,version=after,digest=EMPTY));t['state_versions']=[dict(name='query_state',before=before,after=after,input=inp,output='state')]
    o=op('INIT');t=stage(o,'init',features=dict(copied_bytes=stride));t['accesses']=[access('queries',qi*stride,stride)];output(t,'query','graph.query',query_bytes);state_output(t,0,1,None);version=1;state=init=key(o,t)
    seen=set();expanded=set();ranking=[];fresh=[s['entry']];distance={}
    total={k:0 for k in WORK_UNITS};rounds=chunks=batches=0
    def charge(w):
        for k,q in w.items():total[k]+=q['value']
    while True:
        if fresh:
            batches+=1;o=op('MARK_SCORED');t=stage(o,'state',features=dict(objects=len(fresh)));edge(t,state,'state','prior_state');state_output(t,version,version+1,'prior_state');version+=1;state=key(o,t)
            o=op('SCORE_L2');batch=[];score_total={k:0 for k in WORK_UNITS}
            for ci,start in enumerate(range(0,len(fresh),s['chunk'])):
                group=fresh[start:start+s['chunk']];b=len(group);old=len(batch)
                actual=b''.join(payload.read('vectors',i*stride,stride) for i in group)
                for i in group:
                    v=payload.vector('vectors',i);value=0.0
                    for a,z in zip(query,v):delta=a-z;square=delta*delta;value=value+square
                    distance[i]=value
                seen.update(group)
                t=stage(o,'score_prepare',work(read_bytes=b*stride,write_bytes=b*stride,output_bytes=b*stride),dict(vectors=b,dimensions=d,state_objects=b,copied_bytes=b*stride,decoded_bytes=0),ci)
                edge(t,init,'query','query');edge(t,state,'state','state');t['accesses']=[access('vectors',i*stride,stride) for i in group];output(t,'vectors','graph.vectors',actual);prep=key(o,t)
                w=work(elements=b*d,flops=3*b*d,read_bytes=stride*(b+1),write_bytes=12*b,output_bytes=12*b);charge(w)
                for k,q in w.items():score_total[k]+=q['value']
                t=stage(o,'score_l2',w,dict(vectors=b,dimensions=d),ci);edge(t,init,'query','query');edge(t,prep,'vectors','vectors');output(t,'scores','graph.scores',packed_scores([(i,distance[i]) for i in group]));t['decomposition_rule']='graph-l2-items/1';l2=key(o,t)
                batch.extend((i,distance[i]) for i in group)
                t=stage(o,'merge_append',work(read_bytes=12*b,write_bytes=12*b,output_bytes=12*(old+b)),dict(old_scores=old,new_scores=b,copied_bytes=12*b,released_vectors=b),ci);edge(t,l2,'scores','scores');output(t,'batch','graph.scores',packed_scores(batch));chunks+=1
            o['semantic_work']=quantities(score_total);batch_key=key(o,t)
            incoming=ranking+batch;keys=[(v,i) for i,v in incoming]
            comparisons=len(incoming)*(len(incoming)-1)//2
            ordered=[]
            for pos,k in enumerate(keys):
                insertion=bisect.bisect_right(ordered,k)
                if pos:comparisons+=pos-insertion+int(insertion>0)
                ordered.insert(insertion,k)
            old=len(ranking);ranking=sorted(incoming,key=lambda x:(x[1],x[0]))
            w=work(elements=len(incoming),read_bytes=12*len(incoming),write_bytes=12*len(incoming),output_bytes=12*len(incoming),candidate_checks=len(incoming),comparisons=comparisons);charge(w)
            o=op('MERGE_CANDIDATES');o['semantic_work']=w;t=stage(o,'merge_finalize',w,dict(old_scores=old,new_scores=len(fresh)));edge(t,batch_key,'batch','batch')
            if old:edge(t,candidate,'candidates','previous')
            output(t,'candidates','graph.candidates',packed_scores(ranking));candidate=key(o,t)
        chosen=[i for i,_ in ranking[:s['width']] if i not in expanded][:max(0,min(s['batch'],s['budget']-len(expanded)))]
        w=work(candidate_checks=min(s['width'],len(ranking)),write_bytes=4*len(chosen),output_bytes=4*len(chosen));charge(w)
        o=op('SELECT_FRONTIER');o['semantic_work']=w;t=stage(o,'select',w,dict(old_scores=len(ranking),expanded=len(expanded),L=s['width'],B=s['batch'],E=s['budget']));edge(t,candidate,'candidates','candidates');output(t,'frontier','graph.frontier',packed_ids(chosen));select=key(o,t)
        if not chosen:break
        rounds+=1;o=op('MARK_EXPANDED');t=stage(o,'state',features=dict(objects=len(chosen)));edge(t,state,'state','prior_state');edge(t,select,'frontier','frontier','CONTROL');state_output(t,version,version+1,'prior_state');version+=1;state=key(o,t)
        rows=[];reads=[];accesses=[];joined=b''
        for i in chosen:
            expanded.add(i);row=payload.row(i);rows.append(row);data=packed_ids([len(row),*row]);joined+=data
            o=op('READ_ADJACENCY');t=stage(o,'adjacency_prepare',work(read_bytes=len(data),write_bytes=4*len(row),output_bytes=len(data)),dict(edges=len(row),state_objects=1,copied_bytes=4*len(row),decoded_bytes=len(data)));t['dependencies'][0]['source']['key']=state;a=access('graph',i*(shape['degree']+1)*4,len(data));t['accesses']=[a];accesses.append(a);edge(t,select,'frontier','frontier','ADDRESS');output(t,'neighbors','graph.neighbors',data);reads.append(key(o,t))
        o=op('JOIN');t=stage(o,'join');t['accesses']=accesses
        for k in reads:edge(t,k,'neighbors','neighbors')
        output(t,'neighbors','graph.neighbors',joined);join=key(o,t)
        edges=[i for row in rows for i in row];fresh=[];comparisons=0;sorted_seen=sorted(seen)
        for i in edges:
            comparisons+=bisect.bisect_left(sorted_seen,i)+1 if i in seen else len(seen)
            if i not in seen:
                pos=bisect.bisect_left(fresh,i);comparisons+=min(pos+1,len(fresh))+int(pos<len(fresh));fresh=sorted(set(fresh)|{i})
        w=work(candidate_checks=len(edges),comparisons=comparisons,write_bytes=4*len(fresh),output_bytes=4*len(fresh));charge(w)
        o=op('DEDUP_NEIGHBORS');o['semantic_work']=w;t=stage(o,'dedup',w,dict(edges=len(edges),scored=len(seen)));edge(t,join,'neighbors','neighbors');edge(t,state,'state','state');output(t,'fresh','graph.frontier',packed_ids(fresh))
    o=op('FINISH');t=stage(o,'finish',features=dict(objects=min(s['k'],len(ranking))));edge(t,candidate,'candidates','candidates');top=ranking[:s['k']];output(t,'topk','graph.scores',packed_scores(top))
    return operations,dict(top_k=[dict(id=i,distance=v) for i,v in top],work=total,expanded=len(expanded),scored=len(seen),expansion_batches=rounds,score_batches=batches,score_chunks=chunks)
