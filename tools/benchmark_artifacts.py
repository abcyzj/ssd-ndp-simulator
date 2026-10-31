"""T2 offline graph/truth artifacts; no imports from simulator operators."""
import argparse
from collections import deque
from contextlib import contextmanager
import heapq
import json
import math
from pathlib import Path
import shutil
import struct
import tempfile
from ann_data import integer, write_json
from benchmark_data import check as check_dataset
from verify_m0 import sha, measure, ROOT

VERSION = 'ndpsim-offline-validator-v1'
LOCK = ROOT/'configs/benchmarks/m0-builder.lock.json'
PARAMETERS = dict(R=64, GK=64, L=200, C=500, build_type=1, search_L=256)


def binding(m, roles=('base', 'query')):
    return dict(metric=m['metric'], preprocessing=m['preprocessing'], files={
        role: {key: m['files'][role][key] for key in
               ('sha256','mapping_sha256','count','dimensions','dtype','byte_order')}
        for role in roles})


@contextmanager
def atomic(output):
    output = Path(output)
    if output.exists():
        raise ValueError('output exists; validate for reuse or choose a new output')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.'+output.name+'-', dir=output.parent))
    try:
        yield stage
        stage.rename(output)
    except Exception:
        # Failure evidence is distinct from a reusable completed artifact.
        failed=output.with_name(output.name+'.failed')
        if stage.exists() and not failed.exists(): stage.rename(failed)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def artifact(directory, record, size):
    name = record['path']
    if Path(name).name != name:
        raise ValueError('artifact path must be a filename')
    p = Path(directory)/name
    if p.stat().st_size != size or sha(p) != record['sha256']:
        raise ValueError('artifact size/hash mismatch')
    return p


def record(path):
    return dict(path=Path(path).name, sha256=sha(path))


def graph_check(path, n, degree, entry):
    integer(n,'nodes'); integer(degree,'max degree',1,n-1); integer(entry,'entry',0,n-1)
    width=(degree+1)*4
    if Path(path).stat().st_size != n*width:
        raise ValueError('graph size/coverage mismatch')
    histogram=[0]*(degree+1); edges=duplicates=self_edges=0
    # Validate every row, then traverse from entry without a Python adjacency copy.
    with Path(path).open('rb') as f:
        for node in range(n):
            row=struct.unpack('<'+'I'*(degree+1),f.read(width)); count=row[0]
            if count>degree or any(v>=n for v in row[1:count+1]):
                raise ValueError('graph degree/ID out of range')
            if any(row[count+1:]):
                raise ValueError('nonzero unused slots')
            ids=row[1:count+1]; histogram[count]+=1; edges+=count
            duplicates+=count-len(set(ids)); self_edges+=ids.count(node)
        seen=bytearray(n); seen[entry]=1; todo=deque([entry]); reached=0
        while todo:
            node=todo.popleft(); reached+=1; f.seek(node*width)
            row=struct.unpack('<'+'I'*(degree+1),f.read(width))
            for v in row[1:row[0]+1]:
                if not seen[v]: seen[v]=1; todo.append(v)
    if reached != n:
        raise ValueError('entry cannot reach all nodes')
    return dict(nodes=n, reachable=reached, edges=edges, degree_histogram=histogram,
                duplicate_edges=duplicates, self_edges=self_edges,
                adjacency_policy='preserve_order_duplicates_and_self_edges')


def check_index(dataset, directory):
    m=check_dataset(dataset); directory=Path(directory)
    x=json.loads((directory/'index.json').read_text())
    if x['format']!='ndpsim-index-v1' or x['binding']!=binding(m,('base',)) or x['validator']!=VERSION:
        raise ValueError('index schema/identity mismatch')
    n=m['files']['base']['count']; r=integer(x['max_degree'],'degree',1,n-1)
    p=artifact(directory,x['graph'],n*(r+1)*4)
    artifact(directory,x['mapping'],n*4)
    if x['mapping']['sha256']!=m['files']['base']['mapping_sha256']:
        raise ValueError('index node mapping mismatch')
    report=graph_check(p,n,r,x['entry'])
    if report!=x['validation']:
        raise ValueError('graph validation report mismatch')
    b=x['builder']; lock=json.loads(LOCK.read_text())
    if b['lock_sha256']!=sha(LOCK) or b['commit']!=lock['faiss_commit'] or b['parameters']!=PARAMETERS or b['rng']!=lock['seed_policy']:
        raise ValueError('builder identity mismatch')
    integer(b['threads'],'threads',1,4)
    return x


def rows(path, d, block_rows):
    with Path(path).open('rb') as f:
        block_rows=min(block_rows,max(1,(8*1024*1024)//(d*4)))
        while data:=f.read(block_rows*d*4):
            yield from struct.iter_unpack('<'+'f'*d,data)


def exact(dataset, output, k, block_rows=1024):
    m=check_dataset(dataset); dataset=Path(dataset)
    b,q=m['files']['base'],m['files']['query']; integer(k,'K',1,b['count'])
    integer(block_rows,'block rows',1,65536); d=b['dimensions']
    with atomic(output) as stage:
        with (stage/'ids.u32').open('wb') as ids, (stage/'distances.f64').open('wb') as dist:
            for query in rows(dataset/q['path'],d,1):
                top=[]
                for node,vector in enumerate(rows(dataset/b['path'],d,block_rows)):
                    total=0.0
                    for x,y in zip(query,vector):
                        delta=float(x)-float(y); total=total+delta*delta
                    value=(-total,-node)
                    if len(top)<k: heapq.heappush(top,value)
                    elif value>top[0]: heapq.heapreplace(top,value)
                for distance,node in sorted((-v,-i) for v,i in top):
                    ids.write(struct.pack('<I',node)); dist.write(struct.pack('<d',distance))
        meta=dict(format='ndpsim-truth-v2',validator=VERSION,binding=binding(m),k=k,
                  method='independent_cpu_exhaustive_ordered_fp64',precision='ordered_binary64_no_fma',
                  tie_policy='distance_then_local_id',tie_boundary='resolved',block_rows=block_rows,
                  ids=record(stage/'ids.u32'),distances=record(stage/'distances.f64'),
                  tool_sha256=sha(__file__))
        write_json(stage/'truth.json',meta)
        check_truth(dataset,stage)
        if check_dataset(dataset)!=m: raise ValueError('dataset changed during truth generation')
    return meta


def official(dataset, output):
    m=check_dataset(dataset); dataset=Path(dataset); files=m['files']
    if 'groundtruth' not in files or files['base']['selection']!={'kind':'all'}:
        raise ValueError('official truth requires complete base and matching query mapping')
    # Only the M0 locked SIFT truth has an established full-base relationship.
    lock=json.loads((ROOT/'configs/benchmarks/datasets.lock.json').read_text())
    sift=lock['datasets']['sift1m']['files']
    for role, name in [('base','base'),('query','query'),('groundtruth','groundtruth')]:
        if files[role]['raw']['sha256']!=sift[name]['sha256']:
            raise ValueError('official SIFT source identity mismatch')
    g=files['groundtruth']
    with atomic(output) as stage:
        shutil.copyfile(dataset/g['path'],stage/'ids.u32')
        meta=dict(format='ndpsim-truth-v2',validator=VERSION,binding=binding(m),k=g['dimensions'],
                  method='official_sift_id_only',precision='official_not_declared',
                  tie_policy='official_order',tie_boundary='unresolved',ids=record(stage/'ids.u32'),
                  source=g,source_lock_sha256=sha(ROOT/'configs/benchmarks/datasets.lock.json'),tool_sha256=sha(__file__))
        write_json(stage/'truth.json',meta); check_truth(dataset,stage)
        if check_dataset(dataset)!=m: raise ValueError('dataset changed during import')
    return meta


def check_truth(dataset, directory, k=None):
    m=check_dataset(dataset); directory=Path(directory)
    t=json.loads((directory/'truth.json').read_text())
    if t['format']!='ndpsim-truth-v2' or t['validator']!=VERSION or t['binding']!=binding(m):
        raise ValueError('truth schema/identity mismatch')
    n=m['files']['base']['count']; q=m['files']['query']['count']; K=integer(t['k'],'K',1,n)
    if k is not None: integer(k,'requested k',1,K)
    ids=artifact(directory,t['ids'],q*K*4)
    exact_method=t['method']=='independent_cpu_exhaustive_ordered_fp64'
    if exact_method:
        if (t['precision'],t['tie_policy'],t['tie_boundary'])!=('ordered_binary64_no_fma','distance_then_local_id','resolved') or 'distances' not in t:
            raise ValueError('invalid exact truth semantics')
    elif t['method']!='official_sift_id_only' or t['tie_boundary']!='unresolved' or 'distances' in t:
        raise ValueError('unsupported truth method/semantics')
    else:
        lockpath=ROOT/'configs/benchmarks/datasets.lock.json'
        locked=json.loads(lockpath.read_text())['datasets']['sift1m']['files']
        if t['source_lock_sha256']!=sha(lockpath) or t['source']!=m['files'].get('groundtruth') or m['files']['base']['selection']!={'kind':'all'}:
            raise ValueError('official truth source/base mismatch')
        for role in ('base','query','groundtruth'):
            if m['files'][role]['raw']['sha256']!=locked[role]['sha256']:
                raise ValueError('official source lock mismatch')
        if t['ids']['sha256']!=t['source']['sha256'] or K!=t['source']['dimensions'] or (t['precision'],t['tie_policy'])!=('official_not_declared','official_order'):
            raise ValueError('official ID identity/semantics mismatch')
    distances=artifact(directory,t['distances'],q*K*8) if 'distances' in t else None
    with ids.open('rb') as f:
        df=distances.open('rb') if distances else None
        try:
            for _ in range(q):
                row=struct.unpack('<'+'I'*K,f.read(K*4))
                if any(v>=n for v in row) or len(set(row))!=K:
                    raise ValueError('truth ID out of range/duplicate')
                if df:
                    ds=struct.unpack('<'+'d'*K,df.read(K*8))
                    if any(not math.isfinite(v) or v<0 for v in ds) or list(zip(ds,row))!=sorted(zip(ds,row)):
                        raise ValueError('invalid truth distances/order')
        finally:
            if df: df.close()
    return t


def quality(returned, truth, k, result_r):
    integer(k,'k',1,len(truth)); integer(result_r,'result R')
    for node in [*returned,*truth]: integer(node,'result/truth ID',0)
    if len(set(returned))!=len(returned) or len(set(truth))!=len(truth):
        raise ValueError('duplicate result/truth IDs')
    return dict(recall_at_k=len(set(returned[:k]) & set(truth[:k]))/k,
                one_recall_at_r=int(truth[0] in returned[:result_r]),
                k=k,result_r=result_r,insufficient_results=len(returned)<k)


def build(dataset, output, receipt, threads=1, timeout=3600):
    m=check_dataset(dataset); dataset=Path(dataset).resolve()
    integer(threads,'threads',1,4)
    b,q=m['files']['base'],m['files']['query']
    integer(b['count'],'builder N',1000,10000000)
    integer(b['dimensions'],'builder dimensions',1,65536)
    budget=json.loads((ROOT/'configs/benchmarks/v0.2-budgets.json').read_text())
    cap=min(8*1024**3,budget['effective_process_cap_bytes'])
    lower_bound=b['count']*(4*b['dimensions']+64*12)+q['count']*q['dimensions']*4
    if lower_bound>cap:
        raise ValueError('builder storage lower bound exceeds frozen local resource cap; full Deep10M gate remains blocked')
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(Path(output).parent).free < budget['disk_reserve_bytes']+b['count']*264:
        raise ValueError('insufficient disk reserve for graph export')
    receipt=Path(receipt); rc=json.loads(receipt.read_text()); lock=json.loads(LOCK.read_text())
    exe=Path(rc['executable'])
    runtime=exe.parent.parent/'openmp/lib/libomp.dylib'
    if rc['lock_sha256']!=sha(LOCK) or sha(exe)!=rc['executable_sha256'] or sha(runtime)!=rc['openmp_sha256'] or rc['adapter_sha256']!=sha(ROOT/'tools/offline/index_builder.cc'):
        raise ValueError('builder receipt mismatch; rebuild locked offline tool')
    b,q=m['files']['base'],m['files']['query']
    with atomic(output) as stage:
        argv=[str(exe),str(dataset/b['path']),str(dataset/q['path']),str(b['count']),str(b['dimensions']),str(q['count']),str(threads),str((stage/'adjacency.u32').resolve()),str((stage/'builder-report.json').resolve()),str((stage/'diagnostic.u32').resolve())]
        run=measure(argv,stage/'build-run.json',timeout,cap)
        if not run['passed']: raise ValueError('builder failed; see process diagnostics')
        report=json.loads((stage/'builder-report.json').read_text())
        shutil.copyfile(dataset/b['mapping'],stage/'nodes.ids.u32')
        meta=dict(format='ndpsim-index-v1',validator=VERSION,binding=binding(m,('base',)),
                  entry=report['entry'],max_degree=64,graph=record(stage/'adjacency.u32'),mapping=record(stage/'nodes.ids.u32'),
                  builder=dict(commit=lock['faiss_commit'],lock_sha256=sha(LOCK),receipt=rc,
                               threads=threads,parameters=report['parameters'],rng=lock['seed_policy']),
                  resource_precheck=dict(lower_bound_bytes=lower_bound,process_cap_bytes=cap,not_a_peak_estimate=True),
                  validation=graph_check(stage/'adjacency.u32',b['count'],64,report['entry']),
                  diagnostic=dict(ids=record(stage/'diagnostic.u32'),k=10,query_binding=binding(m)),tool_sha256=sha(__file__))
        write_json(stage/'index.json',meta); check_index(dataset,stage)
        if check_dataset(dataset)!=m: raise ValueError('dataset changed during build')
    return meta


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['build','check-index','exact','official','check-truth'])
    p.add_argument('dataset',type=Path); p.add_argument('artifact',type=Path)
    p.add_argument('--receipt',type=Path); p.add_argument('--threads',type=int,default=1)
    p.add_argument('--k',type=int,default=10); p.add_argument('--block-rows',type=int,default=1024)
    a=p.parse_args()
    if a.action=='build':
        if not a.receipt: p.error('--receipt required')
        result=build(a.dataset,a.artifact,a.receipt,a.threads)
    elif a.action=='check-index': result=check_index(a.dataset,a.artifact)
    elif a.action=='exact': result=exact(a.dataset,a.artifact,a.k,a.block_rows)
    elif a.action=='official': result=official(a.dataset,a.artifact)
    else: result=check_truth(a.dataset,a.artifact,a.k)
    print(json.dumps(result))

if __name__=='__main__': main()
