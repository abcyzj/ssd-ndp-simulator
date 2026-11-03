"""V03-5 scoped observations/comparator acceptance. Stops before V03-6."""
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
from test_ir_observation import exercise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--prepared',type=Path,default=ROOT/'results/v0.2-t2/real-final')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();build=a.build.resolve()
    lock=json.loads((ROOT/'deps.lock.json').read_text())
    if sys.flags.optimize or Path(sys.executable).resolve()!=Path(lock['tools']['python']['path']).resolve(): p.error('use locked Python without -O')
    if out.exists(): p.error('output must not exist')
    out.mkdir(parents=True)
    before=inventory();write_json(out/'source-start.json',before)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    commands=[];checks={};error=None
    def command(name,argv):
        result=measure(list(map(str,argv)),out/(name+'.json'),3600)
        commands.append(dict(name=name,**result));print(name+': exit='+str(result['exit_code']),flush=True)
        if not result['passed']: raise RuntimeError('command failed: '+name)
    try:
        for name,rel in [('direct','direct-baseline-final'),('schema','schema-v031/final'),
                         ('lifecycle','dynamic-v032/final3'),('mapping','mapping-v033/final4'),('frontend','frontend-v034/final2')]:
            path=ROOT/'results/v0.3'/rel;m=json.loads((path/'manifest.json').read_text())
            if m.get('passed') is not True: raise ValueError('dependency failed: '+name)
            verify_hashes(path,m['artifacts_sha256'])
            checks['dependency-'+name]=dict(passed=True,manifest_sha256=sha(path/'manifest.json'),artifacts=len(m['artifacts_sha256']))
        cmake=lock['tools']['cmake']['path']
        command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON'])
        command('build',[cmake,'--build',build,'-j2'])
        command('ctest',['ctest','--test-dir',build,'--output-on-failure'])
        command('synthetic',[sys.executable,ROOT/'tests/test_ir_observation.py','--build',build,'--output',out/'synthetic'])
        prepared=a.prepared.resolve();c=json.loads((ROOT/'configs/experiments/host.json').read_text())
        idx=json.loads((prepared/'index/index.json').read_text())
        c.update(format='ndpsim-experiment-v2',index=str(prepared/'index'),truth=str(prepared/'exact'))
        c['search']['entry']=idx['entry']
        data=json.loads((prepared/'small-data/dataset.json').read_text())
        if data['files']['base']['count']!=10000 or data['files']['base']['dimensions']!=128 or data['files']['query']['count']!=32:
            raise ValueError('expected locked SIFT10k/Q32 input')
        # Run real matrix in a measured child; all eight runs are new.
        command('sift10k',[sys.executable,ROOT/'tools/verify_ir_observation.py','--matrix-child',str(out/'sift10k'),str(build),str(prepared)])
        checks['real-sift10k']=json.loads((out/'sift10k/comparison.json').read_text())
        for placement in ('host','controller'):
            frozen=ROOT/'results/v0.3/direct-baseline-final'/('small-'+placement)
            command('frozen-'+placement,[sys.executable,ROOT/'tools/run_config.py','--config',frozen.parent/('small-'+placement+'-config.json'),
                '--data',frozen/'data','--build',build,'--output',out/('frozen-'+placement)])
            new=out/('frozen-'+placement)
            check=dict(results=json.loads((new/'results.json').read_text())==json.loads((frozen/'results.json').read_text()),
                statistics=statistics(new)==statistics(frozen),events=sha(new/'events.jsonl')==sha(frozen/'events.jsonl'),
                service_csv=sha(new/'service.csv')==sha(frozen/'service.csv'))
            checks['frozen-'+placement]=check
            if not all(check.values()): raise ValueError('frozen direct changed: '+placement)
        command('generated',[sys.executable,ROOT/'tools/generate_ir_schema.py','--check'])
        command('baseline-tests',[sys.executable,ROOT/'tests/test_direct_baseline.py'])
        command('diff-check',['git','diff','--check'])
        after=inventory();write_json(out/'source-end.json',after)
        checks['source-unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies-unchanged']=before['mqsim']==after['mqsim'] and before['sst_head']==after['sst_head'] and not after['sst_status']
        if not checks['source-unchanged'] or not checks['dependencies-unchanged']: raise ValueError('source/dependencies changed')
    except Exception as exc:error=str(exc)
    write_json(out/'assertions.json',checks)
    artifacts={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file()}
    write_json(out/'manifest.json',dict(schema='ndpsim-ir-observation-v035-v1',task='V03-5',passed=error is None,error=error,
        commands=commands,artifacts_sha256=artifacts,
        limitations='Scoped V03-5 acceptance; no full AC/native oracle/ASan/full SIFT1M 52-run matrix/Q RSS efficiency budget acceptance. Native shared NAND command participant sets and ordinary NVMe transaction IDs remain Unsupported. Offline comparator RSS not budget-certified. Hardware configured_unvalidated.'))
    print('V03-5 '+('PASS' if error is None else 'FAIL: '+error),flush=True)
    return int(error is not None)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--matrix-child':
        out,build,prepared=map(Path,sys.argv[2:])
        c=json.loads((ROOT/'configs/experiments/host.json').read_text())
        c.update(format='ndpsim-experiment-v2',index=str(prepared/'index'),truth=str(prepared/'exact'))
        c['search']['entry']=json.loads((prepared/'index/index.json').read_text())['entry']
        exercise(c,build,out,prepared/'small-data')
    else:sys.exit(main())
