"""Independent graph capture/sample/semantic validation entry point (P04).

Uses installed ndpsim metadata tools; never imports historical tools or a simulator.
Payload paths occur only in native input configuration and external reference evidence.
"""
import argparse
import hashlib
import json
import os
import resource
import shutil
from pathlib import Path
import platform
import shlex
import struct
import subprocess
import sys
import time
import sqlite3
import tempfile
from collections import Counter
from ndpsim.static_ir import (Limits, PackageWriter, canonical, check_schema, decode,
    file_hash, require, validate_package, _lines)
from graph_contract import registry, ZERO_KINDS
from graph_oracle import Payload, expected_query

FILES=('vectors','queries','graph','truth','query_ids')
SHAPE=('count','dimensions','queries','degree','truth_k')
SEARCH=('entry','k','width','batch','budget','chunk')

def save(path,v):Path(path).write_bytes(canonical(v))
def load(path):return decode(Path(path).read_bytes())
def signature(report):
    names=('original_query_id','top_k','work','expanded','scored','score_batches','score_chunks','expansion_batches','recall_at_k','one_recall_at_r','insufficient_results')
    return [[r[k] for k in names] for r in report['queries']]

def configuration(path):
    path=Path(path).resolve();v=load(path)
    require(set(v) in ({'format','shape','search','files','split','target','budget'},{'format','shape','search','files','split','target','budget','query_selection'}),'graph config fields')
    require(v['format']=='ndpsim-graph-capture-config/2','graph config version')
    require(set(v['shape'])==set(SHAPE) and set(v['search'])==set(SEARCH),'shape/search coverage')
    require(all(type(x) is int and 0<x<2**32 for x in v['shape'].values()),'invalid shape')
    s=v['search'];require(all(type(x) is int and 0<=x<2**32 for x in s.values()),'invalid search parameters')
    require(s['entry']<v['shape']['count'] and 0<s['k']<=min(v['shape']['truth_k'],s['width'],v['shape']['count']) and min(s['batch'],s['budget'],s['chunk'])>0,'invalid search shape')
    require(set(v['files'])==set(FILES),'input file coverage')
    sizes=dict(vectors=4*v['shape']['count']*v['shape']['dimensions'],queries=4*v['shape']['queries']*v['shape']['dimensions'],graph=4*v['shape']['count']*(v['shape']['degree']+1),truth=4*v['shape']['queries']*v['shape']['truth_k'],query_ids=4*v['shape']['queries'])
    for name in FILES:
        row=v['files'][name];require(set(row)=={'path','sha256'},'input reference fields')
        p=Path(row['path']);p=(path.parent/p).resolve() if not p.is_absolute() else p.resolve();row['path']=str(p)
        require(p.is_file() and p.stat().st_size==sizes[name] and file_hash(p)==row['sha256'],'input size/hash: '+name)
    require(sizes['query_ids']<=16*1024**2,'query ID metadata budget')
    ids=[x[0] for x in struct.iter_unpack('<I',Path(v['files']['query_ids']['path']).read_bytes())]
    require(len(set(ids))==len(ids),'duplicate query ID')
    require(v['split'] in ('fixture','tuning32','evaluation128'),'unknown graph split')
    # Formal IDs are supplied by the caller's locked config; pilot is never formal acceptance.
    require(v['split']=='fixture' or len(ids)==(32 if v['split']=='tuning32' else 128),'formal split size')
    if v['split']!='fixture':
        require('query_selection' in v,'formal split requires explicit locked query selection')
        lock=v['query_selection'];require(set(lock)=={'path','sha256'},'query selection reference')
        selection_path=(path.parent/lock['path']).resolve();require(file_hash(selection_path)==lock['sha256'],'query selection hash')
        selection=load(selection_path)['datasets']['sift1m'];require(not set(selection['tuning_ids']) & set(selection['evaluation_ids']),'query split overlap')
        require(ids==selection['tuning_ids' if v['split']=='tuning32' else 'evaluation_ids'],'locked original query order')
        lock['path']=str(selection_path)
    check_schema(v['target'],'target')
    b=v['budget'];require(set(b)=={'native_rss_bytes','postprocess_rss_bytes','output_bytes','query_bytes','timeout_seconds','min_disk_free_bytes'},'budget fields')
    require(all(type(x) is int and x>0 for x in b.values()),'invalid budget')
    require(b['output_bytes']<=2*1024**3 and b['query_bytes']<=b['output_bytes'],'output budget above supported limit')
    return v,ids

def semantic_hash(v):return hashlib.sha256(canonical(dict(shape=v['shape'],search=v['search']))).hexdigest()
def provenance(v,ids,build,reg):
    return dict(kind='synthetic-fixture' if v['split']=='fixture' else 'native-capture',capture_build=build,input_content_sha256=hashlib.sha256(''.join(v['files'][k]['sha256'] for k in ('vectors','queries','graph','query_ids')).encode()).hexdigest(),semantic_version='ndpsim-ranked-expansion-v1',extension_sha256=reg.sha256,split=v['split'],query_ids=ids,workload='graph',numeric_version='fp32-le-ordered-fp64-l2-v1',work_version='ndpsim-work-v1',semantic_config_sha256=semantic_hash(v))

def binary_identity(binary):
    source,compiler,flags=subprocess.check_output([str(binary),'--build-info'],text=True,timeout=10).splitlines()
    # Native SDK stores the exact full compiler flag string as one metadata item.
    return dict(source_sha256=source,binary_sha256=file_hash(binary),compiler=compiler,flags=[flags])

def semantic_check(package,config,operators,reference):
    reg=registry(operators);structural=validate_package(package,reg,allow_fixture=config['split']=='fixture');manifest=load(Path(package)/'manifest.json')
    ids=[x[0] for x in struct.iter_unpack('<I',Path(config['files']['query_ids']['path']).read_bytes())]
    expected_p=provenance(config,ids,manifest['provenance']['capture_build'],reg)
    require(manifest['provenance']==expected_p,'semantic provenance/config binding')
    expected_objects=[dict(id=k,version=0,type='degree-u32-le+padded-ids-u32-le' if k=='graph' else 'fp32-le',size_bytes=Path(config['files'][k]['path']).stat().st_size,shape=([config['shape']['count'],config['shape']['degree']+1] if k=='graph' else [config['shape']['queries' if k=='queries' else 'count'],config['shape']['dimensions']]),content_sha256=config['files'][k]['sha256']) for k in ('vectors','queries','graph')]
    require(manifest['objects']==expected_objects,'logical object directory identity')
    require(reference['completed']==len(ids) and [r['original_query_id'] for r in reference['queries']]==ids,'reference query coverage/order')
    scratch=tempfile.TemporaryDirectory(prefix='graph-semantic-')
    db=sqlite3.connect(str(Path(scratch.name)/'operations.sqlite'))
    db.execute('PRAGMA cache_size=-2048');db.execute('CREATE TABLE ops(q TEXT,id TEXT,body BLOB,PRIMARY KEY(q,id))')
    actual_count=0
    for _,raw,operation in _lines(Path(package)/'operations.jsonl',16*1024**2,config['budget']['output_bytes']):
        db.execute('INSERT INTO ops VALUES(?,?,?)',(str(operation['query']),operation['id'],raw));actual_count+=1
        require(db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]<=config['budget']['output_bytes']*2,'semantic scratch budget')
    counts=Counter();semantic_total=Counter();payload=Payload(config)
    try:
        for qi,qid in enumerate(ids):
            expected,result=expected_query(payload,qi)
            for op in expected:
                row=db.execute('SELECT body FROM ops WHERE q=? AND id=?',(str(qid),op['id'])).fetchone()
                require(row is not None,'missing semantic operation');actual=decode(row[0])
                require(actual==op,'independent semantic operation mismatch: '+str((qid,op['id'],op['operator'])))
                counts['operations']+=1;counts['stages']+=len(op['stages'])
                for st in op['stages']:counts[st['kind']]+=1
                for k,q in op['semantic_work'].items():semantic_total[k]+=q['value']
            row=reference['queries'][qi]
            require(all(row[k]==val for k,val in result.items()),'reference top-k/Work/count mismatch')
            truth=struct.unpack_from('<'+'I'*config['shape']['truth_k'],payload.maps['truth'],qi*4*config['shape']['truth_k'])[:config['search']['k']]
            require(all(i<config['shape']['count'] for i in truth),'truth ID out of range')
            found={r['id'] for r in row['top_k']}
            require(row['recall_at_k']==len(found.intersection(truth))/config['search']['k'] and row['one_recall_at_r']==int(truth[0] in found) and row['insufficient_results']==(len(found)<config['search']['k']),'independent reference recall')
        require(actual_count==counts['operations'],'extra semantic operation/query')
    finally:payload.close();db.close();scratch.cleanup()
    return dict(format='ndpsim-graph-semantic-report/2',structural_passed=True,independent_semantic_passed=True,semantic_validation='independent-full-query',package_sha256=structural['package_sha256'],provenance=manifest['provenance'],validator_source_sha256={p.name:file_hash(p) for p in (Path(__file__),Path(__file__).with_name('graph_oracle.py'),Path(__file__).with_name('graph_contract.py'))},reference_report_sha256=hashlib.sha256(canonical(reference)).hexdigest(),query_ids=ids,counts=dict(counts),semantic_work=dict(semantic_total),scope='pilot' if config['split']=='fixture' else 'capture-semantic-only',full_acceptance_passed=False)

def operations(package,limit):
    for _,_,op in _lines(Path(package)/'operations.jsonl',16*1024**2,limit):yield op

def measurement(raw,package,config,reference,stride,target):
    manifest=load(Path(package)/'manifest.json');tail=load(Path(package)/'tail.json');rows=[];eligible=0;planned=[];counts=Counter()
    order={q:i for i,q in enumerate(manifest['provenance']['query_ids'])};stage_rows=[]
    for op in operations(package,config['budget']['output_bytes']):
        for st in op['stages']:
            if st['kind'] in ZERO_KINDS:continue
            expected=dict(key=dict(query=op['query'],op=op['id'],stage=st['id']),chunk=st['chunk'],segment=st['segment'],stage_kind=st['kind'],work=st['work'],features=st['features'])
            stage_rows.append((order[op['query']],st['topological_rank'],expected))
    # Keep only sample metadata, not complete operations, outputs and dependencies.
    # Logical rank, not physical package order, determines the frozen sampling plan.
    stage_rows.sort(key=lambda r:(r[0],r[1]))
    for _,_,expected in stage_rows:
        eligible+=1;k=(expected['key']['query'],expected['stage_kind']);index=counts[k];counts[k]+=1
        if index%stride==0:planned.append(expected)
    del stage_rows
    raw=Path(raw)
    require({p.name for p in raw.iterdir()}=={str(q)+'.jsonl' for q in manifest['provenance']['query_ids']},'sample shard coverage')
    def samples():
        for q in manifest['provenance']['query_ids']:
            p=raw/(str(q)+'.jsonl');require(p.is_file(),'missing sample shard')
            with p.open('rb') as f:
                while True:
                    line=f.readline(16*1024**2+1)
                    if not line:break
                    require(len(line)<=16*1024**2 and line.endswith(b'\n'),'sample record limit/truncation');yield decode(line)
    probe_count=reference['empty_probe_count'];require(probe_count>0,'missing empty probes');overhead=reference['empty_probe_ns']//probe_count;resolution=reference['cpu_resolution_ns'];last={}
    from itertools import zip_longest
    for row,expected in zip_longest(samples(),planned):
        require(row is not None and expected is not None,'sample plan coverage')
        check_schema(row,'sample')
        require(set(row)==set(expected)|{'calls','start_cpu_ns','end_cpu_ns','raw_ns','corrected_ns','low_signal'},'sample record fields')
        require(all(row[k]==v for k,v in expected.items()),'sample key/Work/features binding')
        require(row['calls']==1 and row['end_cpu_ns']>=row['start_cpu_ns']>0 and row['raw_ns']==row['end_cpu_ns']-row['start_cpu_ns'],'sample interval arithmetic')
        q=row['key']['query'];require(row['start_cpu_ns']>=last.get(q,0),'overlapping CPU buckets');last[q]=row['end_cpu_ns']
        require(row['corrected_ns']==row['raw_ns'] and row['low_signal'] is False,'native raw sample fields')
        row['corrected_ns']=row['raw_ns']-overhead;row['low_signal']=row['corrected_ns']<resolution
        row['work']=expected['work'];row['features']=expected['features'];rows.append(row)
    del planned
    # The score audit closes only prepare + L2, without charging the outer
    # interval as an additional service. Append/destruction starts at L2 end.
    chunks={}
    for row in rows:
        if row['stage_kind'] in ('score_prepare','score_l2','merge_append'):
            chunks.setdefault((row['key']['query'],row['key']['op'],row['chunk']),{})[row['stage_kind']]=row
    for group in chunks.values():
        require(set(group)=={'score_prepare','score_l2','merge_append'},'score audit stage coverage')
        prep,l2,append=(group[k] for k in ('score_prepare','score_l2','merge_append'))
        require(prep['end_cpu_ns']==l2['start_cpu_ns'] and l2['end_cpu_ns']==append['start_cpu_ns'],'score outer audit gap/overlap')
    result=dict(format='ndpsim-measurement/2',binding=dict(package_sha256=tail['package_sha256'],provenance=manifest['provenance'],target=target,context='resident_random/2',workers=reference['workers'],slots=reference['workers'],admission=reference['concurrency']),mode='cpu_sample',clock=dict(source='CLOCK_THREAD_CPUTIME_ID',resolution_ns=resolution,empty_probe_ns=reference['empty_probe_ns'],empty_probe_count=probe_count),sampling=dict(stride=stride,eligible=eligible,planned=len(rows),observed=len(rows),lost=0),samples=rows,reference_report_sha256=hashlib.sha256(canonical(reference)).hexdigest())
    check_schema(result,'measurement');return result

def host_resources(output,budget):
    if sys.platform=='darwin':
        import re
        pressure=subprocess.check_output(['memory_pressure','-Q'],text=True,timeout=10)
        physical=int(re.search(r'The system has (\d+)',pressure)[1])
        free=int(re.search(r'System-wide memory free percentage: (\d+)%',pressure)[1])
        require(free>=20,'memory pressure preflight')
    else:
        pressure='sysconf physical pages';physical=os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE');free=None
    require(budget['native_rss_bytes']<=physical*3//5,'native RAM budget above 60% physical memory')
    disk=shutil.disk_usage(output).free;require(disk>budget['min_disk_free_bytes']+3*budget['output_bytes'],'disk reserve budget')
    return dict(physical_ram_bytes=physical,memory_free_percent=free,memory_source=pressure,disk_free_bytes=disk,measured_host=dict(id=platform.node(),hardware=platform.machine(),os=platform.platform(),memory_bytes=physical))

def run(config_path,binary,operators,output,mode,package=None,stride=1):
    output=Path(output).absolute();require(not output.exists(),'output must not exist');output.mkdir(parents=True)
    start=time.monotonic();commands=[]
    try:
        v,ids=configuration(config_path);reg=registry(operators);binary=Path(binary).resolve();build=binary_identity(binary)
        save(output/'config.json',v);save(output/'registry.json',reg.descriptor);save(output/'build.json',build)
        save(output/'sources.json',{p.name:file_hash(p) for p in (Path(__file__),Path(__file__).with_name('graph_oracle.py'),Path(__file__).with_name('graph_contract.py'),Path(operators))})
        b=v['budget'];preflight=host_resources(output,b);save(output/'preflight.json',preflight)
        if v['split']!='fixture':require(all(v['target'][k]==preflight['measured_host'][k] for k in ('hardware','os','memory_bytes')),'measurement target differs from actual native host')
        require(sum(Path(v['files'][k]['path']).stat().st_size for k in FILES)*2<=b['native_rss_bytes'],'input/native RAM budget');require(__import__('shutil').disk_usage(output).free>b['min_disk_free_bytes']+b['output_bytes'],'disk reserve budget')
        args=[str(binary),*[str(v['shape'][k]) for k in SHAPE],*[str(v['search'][k]) for k in SEARCH], '1','1']
        for k in FILES:args.extend((v['files'][k]['path'],v['files'][k]['sha256']))
        # Config's search order is entry,k,width,batch,budget,chunk, matching native positional ABI.
        native_mode=dict(capture='graph_capture',sample='graph_sample',benchmark='graph_benchmark',uninstrumented='graph_uninstrumented')[mode]
        args.append(native_mode)
        raw=output/'raw'
        if mode in ('capture','sample'):
            require(mode!='capture' or stride==1,'IR capture cannot be sampled');raw.mkdir()
            args.extend(map(str,(raw,b['output_bytes'],b['query_bytes'],stride,reg.sha256,build['binary_sha256'],semantic_hash(v),v['split'])))
        # wait4 records this child's actual RSS without depending on historical tooling.
        with (output/'native.log').open('wb') as err,(output/'reference.json').open('wb') as stdout:
            proc=subprocess.Popen(args,stdout=stdout,stderr=err)
            deadline=time.monotonic()+b['timeout_seconds']
            while True:
                pid,status,usage=os.wait4(proc.pid,os.WNOHANG)
                if pid:break
                if time.monotonic()>deadline:
                    proc.kill();os.wait4(proc.pid,0);raise ValueError('native timeout budget')
                time.sleep(0.02)
            proc.returncode=os.waitstatus_to_exitcode(status)
        rss=usage.ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        commands.append(dict(argv=args,exit_code=proc.returncode,peak_rss_bytes=rss))
        require(proc.returncode==0,'native run failed');require(rss<=b['native_rss_bytes'],'native RSS budget')
        proc_stdout=(output/'reference.json').read_bytes()
        (output/'native-reference.json').write_bytes(proc_stdout)
        reference=decode(proc_stdout);save(output/'reference.json',reference);require(reference['major_faults']==0,'query window major faults')
        p=provenance(v,ids,build,reg)
        if mode=='capture':
            with PackageWriter(output/'package',p,reg,allow_fixture=v['split']=='fixture',limits=Limits(total_bytes=b['output_bytes'])) as writer:
                for i,q in enumerate(ids):
                    meta=load(raw/str(q)/'capture.json');require(meta['provenance']==dict(p,query_ids=[q]) and meta['operators']==reg.descriptor['operators'],'native provenance/spec binding')
                    stream=raw/str(q)/'operations.jsonl';require(meta['complete'] is True and meta['lost_records']==0 and stream.stat().st_size==meta['operations_bytes'] and file_hash(stream)==meta['operations_sha256'],'native capture completion/hash')
                    require({x.name for x in (raw/str(q)).iterdir()}=={'capture.json','operations.jsonl'},'capture file set')
                    if i==0:
                        for obj in meta['objects']:writer.declare_object(obj)
                    else:require(meta['objects']==objects,'native object identity')
                    objects=meta['objects']
                    for _,_,op in _lines(stream,16*1024**2,b['output_bytes']):writer.record(op)
                    if i==0:terminals=[]
                    terminals.extend(meta['terminals'])
                structural=writer.seal(terminals)
            require({x.name for x in raw.iterdir()}=={str(q) for q in ids},'capture query shard coverage')
            save(output/'structural.json',structural);semantic=semantic_check(output/'package',v,operators,reference);save(output/'semantic.json',semantic)
        elif mode=='sample':
            require(package is not None,'sample requires separately captured package')
            require(load(Path(package)/'manifest.json')['provenance']==p,'sample/capture provenance mismatch')
            semantic=semantic_check(package,v,operators,reference);save(output/'semantic.json',semantic)
            record=measurement(raw,package,v,reference,stride,v['target']);save(output/'measurement.json',record)
            save(output/'quality.json',dict(coverage_passed=True,low_signal=sum(r['low_signal'] for r in record['samples']),samples=len(record['samples']),training_eligible_samples=sum(not r['low_signal'] for r in record['samples']),zero_service_kinds=sorted(ZERO_KINDS),score_audit='prepare/l2 contiguous; append starts at l2 end; outer not charged',formal_quality_passed=False))
        else:
            payload=Payload(v)
            try:
                for qi,row in enumerate(reference['queries']):
                    _,expected=expected_query(payload,qi);require(all(row[k]==val for k,val in expected.items()),'benchmark independent oracle')
            finally:payload.close()
        post_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        require(post_rss<=b['postprocess_rss_bytes'],'postprocess RSS budget')
        save(output/'resources.json',dict(native_peak_rss_bytes=rss,postprocess_peak_rss_bytes=post_rss,disk_free_bytes=shutil.disk_usage(output).free,budget=b))
        require(time.monotonic()-start<=b['timeout_seconds'],'postprocess timeout budget')
        require(sum(p.stat().st_size for p in output.rglob('*') if p.is_file())<=b['output_bytes'],'total output budget')
        for k in FILES:require(file_hash(v['files'][k]['path'])==v['files'][k]['sha256'],'input changed during capture')
        require(binary_identity(binary)==build,'binary changed during capture')
        save(output/'commands.json',commands);save(output/'status.json',dict(passed=True,mode=mode,scope='pilot' if v['split']=='fixture' else 'capture-only',full_acceptance_passed=False,files_sha256={str(p.relative_to(output)):file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}))
        return reference
    except BaseException as exc:
        if (output/'package').exists():(output/'package').rename(output/'package.failed')
        save(output/'commands.json',commands);save(output/'status.json',dict(passed=False,error=str(exc),full_acceptance_passed=False));raise

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['capture','sample','benchmark','uninstrumented','check']);p.add_argument('--config',required=True);p.add_argument('--operators',required=True);p.add_argument('--output',required=True);p.add_argument('--binary');p.add_argument('--package');p.add_argument('--reference');p.add_argument('--stride',type=int,default=1);a=p.parse_args()
    require(a.stride>0,'stride must be positive')
    if a.mode=='check':
        v,_=configuration(a.config);out=Path(a.output);require(not out.exists(),'output must not exist');out.mkdir(parents=True)
        try:
            save(out/'semantic.json',semantic_check(a.package,v,a.operators,load(a.reference)))
            save(out/'status.json',dict(passed=True,full_acceptance_passed=False,semantic_report_sha256=file_hash(out/'semantic.json')))
        except BaseException as exc:save(out/'status.json',dict(passed=False,error=str(exc)));raise
    else:require(a.binary is not None,'binary required');run(a.config,a.binary,a.operators,a.output,a.mode,a.package,a.stride)
if __name__=='__main__':
    try:main()
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
