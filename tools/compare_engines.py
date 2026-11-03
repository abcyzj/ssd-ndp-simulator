"""Independent, strict direct/IR and statistics/detail comparison (semantic-v1).

No simulator or frontend implementation is imported. Resource stream order is
preserved. IDs are renamed by typed semantic anchors, never simply deleted.
"""
import argparse
import copy
import itertools
import json
from pathlib import Path
import sqlite3
import tempfile
from ann_data import digest, write_json
from stream_statistics import read_statistics

VERSION = 'ndpsim-equivalence/1'
MANAGEMENT = {'ir_operation', 'ir_binding', 'ir_task_edge', 'ir_runtime', 'ir_summary', 'ir_session'}
# Exact, versioned whitelist. All other fields, including new unknown fields,
# participate in comparison and fail closed.
WHITELIST = {
    'statistics.wall_seconds': 'host wall clock',
    'lifecycle.runtime_peak_tasks': 'IR management tasks included in high water',
    'lifecycle_sample.runtime_tasks': 'IR management tasks still in flight',
    'lifecycle_sample.waiters': 'IR management continuations still in flight',
    'resources.ir_summary': 'IR-only management counts, separately validated',
    'events.'+','.join(sorted(MANAGEMENT)): 'enumerated IR-only diagnostic records, separately validated',
}

def load(p): return json.loads(Path(p).read_text())
DIAGNOSTICS = load(Path(__file__).resolve().parents[1]/'schemas/ndpsim-ir-diagnostics-v1.schema.json')
def validate_record(row):
    # This closed record envelope is shared with the machine-readable schema.
    spec=next(s for s in DIAGNOSTICS['oneOf'] if s['properties']['type']['const']==row['type'])
    if set(row)!=set(spec['required']): raise ValueError('diagnostic fields: '+row['type'])
    for key, rule in spec['properties'].items():
        v=row[key]
        if 'const' in rule and v!=rule['const'] or 'enum' in rule and v not in rule['enum']:
            raise ValueError('diagnostic version/enum: '+key)
        if rule.get('type')=='integer' and (type(v)!=int or not rule.get('minimum',0)<=v<=rule.get('maximum',2**64-1)):
            raise ValueError('diagnostic integer: '+key)

def rows(p):
    with Path(p).open() as f:
        for line in f: yield json.loads(line)

def difference(a, b, path='$'):
    if path.endswith('.distance') and type(a) in (int,float) and type(b) in (int,float):
        return None if float(a)==float(b) else dict(path=path,left=a,right=b)
    if type(a) != type(b): return dict(path=path, left=a, right=b)
    if isinstance(a, dict):
        if a.keys() != b.keys(): return dict(path=path, left_keys=sorted(a), right_keys=sorted(b))
        for k in a:
            d = difference(a[k], b[k], path+'.'+k)
            if d: return d
    elif isinstance(a, list):
        if len(a) != len(b): return dict(path=path, left_length=len(a), right_length=len(b))
        for i, (x,y) in enumerate(zip(a,b)):
            d = difference(x,y,f'{path}[{i}]')
            if d: return d
    elif a != b: return dict(path=path, left=a, right=b)
    return None

class Run:
    def __init__(self, path, db, prefix):
        self.path=Path(path); self.db=db; self.table=prefix
        self.manifest=load(self.path/'manifest.json'); self.config=load(self.path/'config.json')
        # Verify every registered artifact before comparing any success output.
        for name, sha in self.manifest['artifacts_sha256'].items():
            if digest(self.path/name) != sha: raise ValueError('artifact hash mismatch: '+str(self.path/name))
        for required in ('config.json','results.json','statistics.json','observation.json'):
            if required not in self.manifest['artifacts_sha256']: raise ValueError('unregistered required artifact: '+required)
        engine=self.manifest['execution_engine']
        observation=load(self.path/'observation.json')
        if engine not in ('ir','direct') or self.config.get('execution_engine','direct')!=engine or observation['execution_engine']!=engine:
            raise ValueError('execution engine identity mismatch')
        if observation['schema']!='ndpsim-observation/1': raise ValueError('observation schema mismatch')
        if engine=='ir':
            if digest(self.path/'ir-manifest.json')!=self.manifest['ir_contract_sha256']: raise ValueError('IR contract hash mismatch')
            contract=load(self.path/'ir-manifest.json')
            if contract['operations'] or contract['manifest']['execution_engine']!='ir': raise ValueError('invalid dynamic IR contract')
            for key in ('ir_version','operator_set','mapping'):
                if observation[key]!=contract['manifest'][key]: raise ValueError('IR metadata mismatch: '+key)
        self.stats=read_statistics(self.path/'statistics.json',db,prefix+'_stats'); self.results=load(self.path/'results.json')
        if engine=='ir' and self.stats['resources'].get('ir_summary')!=[observation['ir']]: raise ValueError('IR observation count mismatch')
        self.ids={}; self.id_counts={}; self.tasks={}; self.counts={}; self.task_context={}
        db.execute(f'create table {prefix}(seq integer primary key, kind text, row text)')
        db.execute(f'create index {prefix}_kind on {prefix}(kind,seq)')
        self.detail=self.config.get('event_log',False)
        self.query_identity={r['query']:r.get('original_query_id',r['query']-1) for r in self.results['queries']}
        if self.detail and 'events.jsonl' not in self.manifest['artifacts_sha256']: raise ValueError('unregistered detail log')
        if self.detail:
            for r in rows(self.path/'events.jsonl'):
                if r['type'] in MANAGEMENT: validate_record(r)
                db.execute(f'insert into {prefix}(kind,row) values(?,?)',(r['type'],json.dumps(r)))
        db.commit()
        # Ordered semantic anchors: actual reads, CPU service roles and protocol
        # messages. Query IDs are retained, not renamed. Per-role ordinal is the
        # element/chunk index; IR scope links it to the batch below.
        for kind in ('service','message','local_storage','controller_service','read_submit','local_request'):
            source = iter(self.stats['resources'].get(kind, [])) if kind in self.stats['resources'] else self.events(kind)
            for r in source:
                task=r.get('task')
                if task and task not in self.tasks:
                    role=(r.get('query',0),kind,r.get('kind',r.get('stage','')))
                    self.counts[role]=self.counts.get(role,0)+1
                    self.tasks[task]=[*role,self.counts[role]]
        for kind, field, domain in [('read_submit','request','ordinary'),('local_request','request','local'),
                                     ('message','id','message'),('link','id','link'),('credit','id','credit')]:
            source=self.events(kind) if self.detail else iter(self.stats['resources'].get(kind,[]))
            for r in source: self.identity(domain,r[field])
        if self.detail: self.validate_ir()
        ir=self.stats['resources'].get('ir_summary',[])
        if self.manifest['execution_engine']=='ir':
            if len(ir)!=1: raise ValueError('missing IR counts')
            s=ir[0]
            validate_record(s)
            if s['active_ops'] or s['active_values'] or s['failed'] or s['submitted']!=s['completed']:
                raise ValueError('IR not drained/successful')
            if any(o['failed'] or o['submitted']!=o['completed'] for o in s['operators']) or sum(o['submitted'] for o in s['operators'])!=s['submitted']: raise ValueError('IR count mismatch')
    def events(self,kind):
        for (s,) in self.db.execute(f'select row from {self.table} where kind=? order by seq',(kind,)): yield json.loads(s)
    def identity(self,domain,value):
        if value==0: return 0
        key=(domain,value)
        if key not in self.ids:
            self.id_counts[domain]=self.id_counts.get(domain,0)+1
            self.ids[key]=self.id_counts[domain]
        return self.ids[key]
    def normalize(self,r):
        r=copy.deepcopy(r); kind=r.get('type')
        if 'task' in r:
            # Statistics-only runs have no detailed anchors; assign per resource
            # semantic first use. Each mapping stays bijective across references.
            t=r['task']
            if t and t not in self.tasks: self.tasks[t]=['statistics',self.identity('task',t)]
            r['task']=self.tasks.get(t,0)
        if 'request' in r: r['request']=self.identity('local' if kind=='local_request' else 'ordinary',r['request'])
        if kind in ('message','link','credit'): r['id']=self.identity(kind,r['id'])
        if kind=='link': r['platform_id']=self.identity('message',r['platform_id'])
        if kind=='local_request':
            for t in r['transactions']: t['id']=self.identity('transaction',t['id'])
        if kind=='lifecycle':
            if any(v for k,v in r.items() if k not in ('type','runtime_peak_tasks','protocol_ranges')): raise ValueError('lifecycle not drained')
            r.pop('runtime_peak_tasks')
        if kind=='lifecycle_sample':
            r.pop('runtime_tasks'); r.pop('waiters')
        return r
    def validate_ir(self):
        if self.manifest['execution_engine']!='ir': return
        # Disk-backed historical associations; never used by simulation.
        self.db.execute(f"create index {self.table}_runtime_task on {self.table}(json_extract(row,'$.task')) where kind='ir_runtime'")
        self.db.execute(f'create table {self.table}_ops(id integer primary key, task integer, row text, phase text)')
        ops=self.table+'_ops'; bound=set(); edges={}
        for r in self.events('ir_operation'):
            o=r['operation']; oid=o['op_id']; phase=r['phase']
            found=self.db.execute(f'select task,row,phase from {ops} where id=?',(oid,)).fetchone()
            if phase=='submitted':
                if found: raise ValueError('duplicate IR submission')
                for d in o['deps']:
                    if d['producer_op']>=oid or not self.db.execute(f'select 1 from {ops} where id=?',(d['producer_op'],)).fetchone(): raise ValueError('invalid IR dependency')
                self.db.execute(f'insert into {ops} values(?,?,?,?)',(oid,r['task'],json.dumps(o),'submitted'))
            else:
                if not found or found[0]!=r['task'] or json.loads(found[1])!=o: raise ValueError('IR identity changed')
                if (found[2],phase) not in (('submitted','visible'),('submitted','failed'),('visible','reclaimed'),('failed','reclaimed')): raise ValueError('IR lifecycle order')
                self.db.execute(f'update {ops} set phase=? where id=?',(phase,oid))
        if self.db.execute(f"select count(*) from {ops} where phase!='reclaimed'").fetchone()[0]: raise ValueError('IR history incomplete')
        submitted=self.db.execute(f'select count(*) from {ops}').fetchone()[0]
        summary=self.stats['resources'].get('ir_summary',[])
        if len(summary)!=1 or summary[0]['submitted']!=submitted: raise ValueError('IR log/count mismatch')
        for task, text in self.db.execute(f'select task,row from {ops}'):
            op=json.loads(text)
            transitions=[json.loads(r[0]) for r in self.db.execute(
                f"select row from {self.table} where kind='ir_runtime' and json_extract(row,'$.task')=? order by seq",(task,))]
            if not transitions or transitions[0]['state']!=0 or transitions[-1]['state']!=6:
                raise ValueError('missing IR runtime lifecycle')
            if any(r['query']!=op['scope']['query'] for r in transitions): raise ValueError('IR runtime query mismatch')
            if any(x['time_ns']>y['time_ns'] for x,y in zip(transitions,transitions[1:])): raise ValueError('IR runtime time reversal')
        for r in self.events('ir_binding'):
            if not self.db.execute(f'select 1 from {ops} where id=?',(r['op'],)).fetchone(): raise ValueError('missing IR parent')
            bound.add(r['task'])
            op=json.loads(self.db.execute(f'select row from {ops} where id=?',(r['op'],)).fetchone()[0])
            context=dict(op['scope'], operator=op['operator'], original_query_id=self.query_identity[op['scope']['query']])
            if r['task'] in self.task_context and self.task_context[r['task']]!=context: raise ValueError('conflicting IR parent binding')
            self.task_context[r['task']]=context
        for r in self.events('ir_task_edge'):
            edges[r['task']]=r['parent_task']
            if r['parent_task'] in self.task_context: self.task_context[r['task']]=self.task_context[r['parent_task']]
        for r in self.events('ir_session'):
            if r['role'] not in ('open','close'): raise ValueError('unknown session role')
            bound.add(r['task'])
            self.task_context[r['task']]=dict(query=r['query'],operator='SESSION_'+r['role'])
        for kind in ('read_submit','service','local_request','local_storage','controller_service','message'):
            for r in self.events(kind):
                t=r['task']
                if t not in bound and edges.get(t) not in bound:
                    raise ValueError(f'unbound IR backend task: {kind} {r}')
                context=self.task_context[t]
                if context['query']!=r['query']: raise ValueError('IR backend query mismatch')
                operator=context['operator']
                allowed={'SCORE_L2'}
                if kind=='read_submit': allowed={'SCORE_L2'} if r['kind']=='vector' else {'READ_ADJACENCY'}
                elif kind=='service': allowed={'SCORE_L2'} if r['kind']=='distance' else {'MERGE_CANDIDATES','SELECT_FRONTIER','DEDUP_NEIGHBORS'}
                elif kind=='message' and r['kind'] in (1,2): allowed={'SESSION_open'}
                elif kind=='message' and r['kind'] in (5,6): allowed={'SESSION_close'}
                elif kind=='controller_service': allowed={'SCORE_L2','SESSION_open','SESSION_close'}
                if operator not in allowed: raise ValueError('IR backend operator role mismatch')
        self.db.commit()

def compare(left,right):
    report=dict(schema=VERSION,left=str(left),right=str(right),passed=False,whitelist=WHITELIST,differences=[])
    def check(a,b,path):
        d=difference(a,b,path)
        if d: report['differences'].append(d)
    try:
        with tempfile.TemporaryDirectory(prefix='ndpsim-compare-') as tmp:
            db=sqlite3.connect(str(Path(tmp)/'comparison.sqlite')); db.execute('pragma cache_size=-2048')
            a,b=Run(left,db,'a'),Run(right,db,'b')
            ca,cb=copy.deepcopy(a.config),copy.deepcopy(b.config)
            for c in (ca,cb):
                c.pop('execution_engine',None);c.pop('event_log',None)
            check(ca,cb,'config')
            for k in ('dataset','input_identity','truth_sha256','device_preflight','content_backend','element_sha256','source_sha256','mqsim_commit','sst_commit','mqsim_source_sha256'):
                check(a.manifest[k],b.manifest[k],'identity.'+k)
            check(a.results,b.results,'results')
            for s in (a.stats,b.stats): s.pop('wall_seconds')
            ra,rb=(s.stats.pop('resources') for s in (a,b))
            check(a.stats,b.stats,'statistics')
            for kind in sorted(set(ra)|set(rb)):
                if kind=='ir_summary' and a.manifest['execution_engine']!=b.manifest['execution_engine']: continue
                if (kind in ra)!=(kind in rb):
                    check(kind in ra,kind in rb,'resources.'+kind+'.present')
                for i,(x,y) in enumerate(itertools.zip_longest(ra.get(kind,[]),rb.get(kind,[]))):
                    check(a.normalize(x) if x is not None else None,b.normalize(y) if y is not None else None,f'resources.{kind}[{i}]')
                    if len(report['differences'])>=100:break
            if a.detail and b.detail:
                kinds={k for (k,) in db.execute('select distinct kind from a union select distinct kind from b')}-MANAGEMENT
                for kind in sorted(kinds):
                    for i,(x,y) in enumerate(itertools.zip_longest(a.events(kind),b.events(kind))):
                        previous=len(report['differences'])
                        check(a.normalize(x) if x else None,b.normalize(y) if y else None,f'events.{kind}[{i}]')
                        if len(report['differences'])>previous:
                            report['differences'][-1]['semantic_key']=dict(resource=kind, ordinal=i,
                                query=(y or x).get('query'), original_query_id=b.query_identity.get((y or x).get('query')),
                                ir_scope=b.task_context.get((y or {}).get('task'),a.task_context.get((x or {}).get('task'))))
                        if len(report['differences'])>=100: break
            report['detail_compared']=a.detail and b.detail
            report['passed']=not report['differences']; db.close()
    except Exception as e: report['error']=str(e)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('left',type=Path);p.add_argument('right',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=compare(a.left,a.right);write_json(a.output,report);print(json.dumps(report));raise SystemExit(0 if report['passed'] else 1)
