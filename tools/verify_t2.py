"""Reproduce T2 on locked real SIFT input; preparation only, never starts T3."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
from ann_data import write_json
from benchmark_artifacts import check_index, check_truth, artifact, binding, quality
from verify_m0 import ROOT, sha, measure


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); out=a.output.resolve()
    if out.exists(): p.error('output must not exist')
    out.mkdir(parents=True)
    lockpath=ROOT/'configs/benchmarks/datasets.lock.json'
    lock=json.loads(lockpath.read_text())['datasets']['sift1m']
    runs=[]; assertions={}
    def run(label, args, rss=2*1024**3):
        result=measure([sys.executable,*map(str,args)],out/(label+'.json'),3600,rss)
        runs.append(result)
        write_json(out/'manifest.json',dict(scope='T2 real small graph + full-base official ID import; not M1/T3 or scale acceptance',
                   source_lock_sha256=sha(lockpath),commands=runs,passed=False))
        if not result['passed']: raise RuntimeError('failed stage: '+label)
    def spec(full):
        files={}
        for role,name in [('base','sift_base.fvecs'),('query','sift_query.fvecs'),('groundtruth','sift_groundtruth.ivecs')]:
            if role=='groundtruth' and not full: continue
            raw=lock['files'][role]
            files[role]=dict(path=str((a.source_dir/name).resolve()),format=raw['format'],count=raw['count'],dimensions=raw['dimensions'],
                             sha256=raw['sha256'],source=dict(url=lock['download_url'],usage_conditions=lock['usage_conditions']))
            if role=='base' and not full:
                files[role]['selection']=dict(kind='ids',ids=[1,0,*range(2,10000)])
            elif role!='base':
                files[role]['selection']=dict(kind='ids',ids=list(range(32)))
        return dict(metric='squared_l2',preprocessing=lock['preprocessing'],files=files)
    data=out/'small-data'; index=out/'index'; truth=out/'exact'
    write_json(out/'small-spec.json',spec(False))
    run('import-small',[ROOT/'tools/benchmark_data.py','import',out/'small-spec.json','--output',data],1024**3)
    run('build-index',[ROOT/'tools/benchmark_artifacts.py','build',data,index,'--receipt',a.receipt.resolve()])
    run('exact-truth',[ROOT/'tools/benchmark_artifacts.py','exact',data,truth,'--k',10,'--block-rows',128])
    run('check-index',[ROOT/'tools/benchmark_artifacts.py','check-index',data,index])
    run('check-truth',[ROOT/'tools/benchmark_artifacts.py','check-truth',data,truth])
    x=check_index(data,index); t=check_truth(data,truth)
    diag=x['diagnostic']
    if diag['query_binding']!=t['binding'] or diag['k']!=10: raise ValueError('diagnostic binding mismatch')
    di=artifact(index,diag['ids'],32*10*4)
    ids=list(struct.iter_unpack('<10I',di.read_bytes()))
    exact_ids=list(struct.iter_unpack('<10I',(truth/t['ids']['path']).read_bytes()))
    per_query=[quality(list(r),list(g),10,10) for r,g in zip(ids,exact_ids)]
    mean=sum(r['recall_at_k'] for r in per_query)/32
    write_json(out/'builder-quality.json',dict(scope='Faiss native search_L=256 diagnostic query rows 0..31; no project search',recall_at_10=mean,per_query=per_query))
    assertions.update(graph_coverage=x['validation']['nodes']==10000,entry_reachability=x['validation']['reachable']==10000,
                      builder_diagnostic_recall=mean>=0.95,exact_query_count=len(exact_ids)==32)
    write_json(out/'official-spec.json',spec(True))
    full=out/'full-base-data'; official=out/'official'
    run('import-official-data',[ROOT/'tools/benchmark_data.py','import',out/'official-spec.json','--output',full],1024**3)
    run('official-truth',[ROOT/'tools/benchmark_artifacts.py','official',full,official])
    run('official-k-subset',[ROOT/'tools/benchmark_artifacts.py','check-truth',full,official,'--k',10])
    o=check_truth(full,official,10)
    assertions.update(official_id_only='distances' not in o,official_K_100=o['k']==100,official_ties_unresolved=o['tie_boundary']=='unresolved')
    try:
        check_truth(data,official,10)
    except ValueError:
        assertions['base_subset_official_rejected']=True
    else:
        assertions['base_subset_official_rejected']=False
    write_json(out/'assertions.json',assertions)
    write_json(out/'ac-matrix.json',{'AC-02-02':'T2 identity/schema portion passed', 'AC-02-03':'small real graph preparation passed; full-scale deferred',
               'AC-02-04':'T2 exact/ID-only/quality fixtures passed; simulation output integration deferred to T3'})
    sources=['tools/benchmark_artifacts.py','tools/offline/index_builder.cc','tools/verify_t2.py','tools/m0/build_builder.py','tools/m0/CMakeLists.txt','tests/test_benchmark_artifacts.py']
    write_json(out/'manifest.json',dict(scope='T2 preparation only; no T3, full AC or scale acceptance',commands=runs,
               assertions=assertions,passed=all(assertions.values()),source_sha256={s:sha(ROOT/s) for s in sources},
               head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
               input_lock_sha256=sha(lockpath),receipt_sha256=sha(a.receipt),
               disk_bytes_snapshot=sum(f.stat().st_size for f in out.rglob('*') if f.is_file()),
               disk_peak='Unsupported: artifact size snapshot only; no continuous volume monitor'))
    if not all(assertions.values()): raise RuntimeError('T2 assertions failed')
    print('T2 real preparation assertions passed; M1/T3 and scale acceptance remain pending')

if __name__=='__main__': main()
