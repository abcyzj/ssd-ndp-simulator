"""V03-4 frontend acceptance only. Does not start V03-5 or accept V0.3 delivery."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from ann_data import write_json
from freeze_direct_baseline import verify_hashes
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import statistics
sys.path.insert(0,str(ROOT/'tests'))
from test_ir_frontend import matrix, events

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--asan-build',type=Path,default=ROOT/'build/v034-asan')
    p.add_argument('--prepared',type=Path,default=ROOT/'results/v0.2-t2/real-final')
    p.add_argument('--counterfactual',type=Path,default=ROOT/'results/v0.2-t3/final')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();build=a.build.resolve();asan=a.asan_build.resolve()
    lock=json.loads((ROOT/'deps.lock.json').read_text())
    if sys.flags.optimize or Path(sys.executable).resolve()!=Path(lock['tools']['python']['path']).resolve():p.error('use locked Python without -O')
    if out.exists():p.error('output must not exist')
    out.mkdir(parents=True)
    before=inventory();write_json(out/'source-start.json',before)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    commands=[];checks={};error=None
    def command(name,argv,env=None):
        r=measure(list(map(str,argv)),out/(name+'.json'),3600,env=env)
        commands.append(dict(name=name,**r));print(name+': exit='+str(r['exit_code']),flush=True)
        if not r['passed']:raise RuntimeError('command failed: '+name)
    try:
        for name,rel in [('direct','direct-baseline-final'),('schema','schema-v031/final'),
                         ('lifecycle','dynamic-v032/final3'),('mapping','mapping-v033/final4')]:
            path=ROOT/'results/v0.3'/rel;m=json.loads((path/'manifest.json').read_text())
            if m.get('passed') is not True:raise ValueError('dependency not passed: '+name)
            verify_hashes(path,m['artifacts_sha256'])
            checks['dependency-'+name]=dict(manifest_sha256=sha(path/'manifest.json'),artifacts=len(m['artifacts_sha256']),passed=True)
        cmake=lock['tools']['cmake']['path']
        command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON'])
        command('build',[cmake,'--build',build,'-j2'])
        command('ctest',['ctest','--test-dir',build,'--output-on-failure'])
        command('synthetic',[sys.executable,ROOT/'tests/test_ir_frontend.py','--build',build,'--output',out/'synthetic'])
        command('causal',[build/'ir_frontend_causal_probe',ROOT/'examples/ir/graph-contract.json'])
        command('backend-probe',[sys.executable,ROOT/'tests/test_ir_mapping.py','--probe',build/'ir_frontend_probe','--output',out/'backend-probe'])
        prepared=a.prepared.resolve();counter=a.counterfactual.resolve()
        c=json.loads((ROOT/'configs/experiments/host.json').read_text())
        idx=json.loads((prepared/'index/index.json').read_text())
        c.update(format='ndpsim-experiment-v2',index=str(prepared/'index'),truth=str(prepared/'exact'),event_log=True)
        c['search']['entry']=idx['entry']
        dataset=json.loads((prepared/'small-data/dataset.json').read_text())
        assert dataset['files']['base']['count']==10000 and dataset['files']['base']['dimensions']==128
        checks['real-sift-baseline']=matrix(c,build,out/'sift10k',prepared/'small-data')
        d=json.loads((counter/'perturbed-config.json').read_text())
        d['index']=str(counter/'perturbed-index');d['truth']=str(counter/'perturbed-truth')
        checks['real-sift-counterfactual']=matrix(d,build,out/'sift10k-counterfactual',counter/'perturbed-data')
        original=json.loads((prepared/'small-data/dataset.json').read_text())
        changed=json.loads((counter/'perturbed-data/dataset.json').read_text())
        assert original['files']['base']['sha256'] != changed['files']['base']['sha256']
        assert original['files']['query']['sha256'] == changed['files']['query']['sha256']
        original_graph=prepared/'index'/idx['graph']['path']
        changed_index=json.loads((counter/'perturbed-index/index.json').read_text())
        changed_graph=counter/'perturbed-index'/changed_index['graph']['path']
        assert sha(original_graph)==sha(changed_graph)
        for engine in ('direct','ir'):
            for placement in ('host','controller'):
                x,y=(events(out/f/(engine+'-'+placement)) for f in ('sift10k','sift10k-counterfactual'))
                decisions=lambda es:[r for r in es if r['type']=='decision' and r['query']==1]
                reads=lambda es:[(r['id'],r['lba']) for r in es if r['type']=='read_submit' and r['kind']=='adjacency' and r['query']==1]
                assert decisions(x)[0]['expand']==decisions(y)[0]['expand'] and decisions(x)[1]['expand']!=decisions(y)[1]['expand']
                assert reads(x)!=reads(y)
        checks['real-perturbation']=dict(passed=True,graph_sha256=sha(original_graph),query_sha256=original['files']['query']['sha256'],method='reuse validated T3 vector-swap input; all simulation runs new')
        # Second real shape exercises semantic B2 and smaller host/capacity chunks.
        c['search'].update(batch=2,budget=16,chunk=2);c['max_inflight']=2
        checks['real-sift-batched']=matrix(c,build,out/'sift10k-batched',prepared/'small-data')
        for placement in ('host','controller'):
            frozen=ROOT/'results/v0.3/direct-baseline-final'/('small-'+placement)
            command('frozen-'+placement,[sys.executable,ROOT/'tools/run_config.py','--config',frozen.parent/('small-'+placement+'-config.json'),
                '--data',frozen/'data','--build',build,'--output',out/('frozen-'+placement)])
            new=out/('frozen-'+placement)
            assert json.loads((new/'results.json').read_text())==json.loads((frozen/'results.json').read_text())
            assert statistics(new)==statistics(frozen)
            assert sha(new/'events.jsonl')==sha(frozen/'events.jsonl') and sha(new/'service.csv')==sha(frozen/'service.csv')
            checks['frozen-'+placement]=True
        command('generated',[sys.executable,ROOT/'tools/generate_ir_schema.py','--check'])
        command('baseline-tests',[sys.executable,ROOT/'tests/test_direct_baseline.py'])
        command('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DBUILD_TESTING=ON','-DNDPSIM_SANITIZE=ON'])
        command('asan-build',[cmake,'--build',asan,'--target','ir_frontend_probe','ir_frontend_causal_probe','-j2'])
        command('asan-tests',['ctest','--test-dir',asan,'--output-on-failure','-R','^(ir_graph_frontend|ir_graph_frontend_causality)$'],env={'ASAN_OPTIONS':'halt_on_error=1'})
        command('diff-check',['git','diff','--check'])
        after=inventory();write_json(out/'source-end.json',after)
        checks['source-unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies-unchanged']=before['mqsim']==after['mqsim'] and before['sst_head']==after['sst_head'] and not after['sst_status']
        assert checks['source-unchanged'] and checks['dependencies-unchanged']
    except Exception as exc:error=str(exc)
    write_json(out/'assertions.json',checks)
    artifacts={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file()}
    write_json(out/'manifest.json',dict(schema='ndpsim-ir-frontend-v034-v1',task='V03-4',passed=error is None,error=error,
        commands=commands,artifacts_sha256=artifacts,limitations='Scoped frontend checks, not V03-5 full comparator, full AC, native oracle, 52-run SIFT1M matrix or RSS/efficiency acceptance. Synthetic causal service gates; ASan targets only. Hardware configured_unvalidated.'))
    print('V03-4 '+('PASS' if error is None else 'FAIL: '+error),flush=True)
    return int(error is not None)

if __name__=='__main__':sys.exit(main())
