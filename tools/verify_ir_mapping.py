"""V03-3 operator mappings only: real backend probes, regressions and frozen direct anchor.

Does not implement or accept V03-4 frontend / V03-6 SIFT matrix / V03-7 full AC.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from ann_data import write_json
from freeze_direct_baseline import verify_evidence, verify_hashes
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import statistics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build/delivery')
    p.add_argument('--asan-build',type=Path,default=ROOT/'build/v033-asan')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    lock=json.loads((ROOT/'deps.lock.json').read_text())
    if sys.flags.optimize or Path(sys.executable).resolve()!=Path(lock['tools']['python']['path']).resolve():
        p.error('use locked Python without -O')
    out=a.output.resolve();build=a.build.resolve();asan=a.asan_build.resolve()
    if out.exists():p.error('output must not exist')
    out.mkdir(parents=True)
    before=inventory();write_json(out/'source-start.json',before)
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    commands=[];checks={};error=None
    def command(name,argv,env=None):
        r=measure(list(map(str,argv)),out/(name+'.json'),3600,env=env)
        commands.append(dict(name=name,**r))
        print(f'{name}: exit={r["exit_code"]}',flush=True)
        if not r['passed']:raise RuntimeError('command failed: '+name)
    try:
        for name,path in [('direct',ROOT/'results/v0.3/direct-baseline-final'),
                          ('schema',ROOT/'results/v0.3/schema-v031/final'),
                          ('lifecycle',ROOT/'results/v0.3/dynamic-v032/final3')]:
            m=json.loads((path/'manifest.json').read_text())
            if m.get('passed') is not True:raise ValueError('dependency not passed: '+name)
            verify_hashes(path,m['artifacts_sha256'])
            checks['dependency-'+name]=dict(manifest_sha256=sha(path/'manifest.json'),artifacts=len(m['artifacts_sha256']),passed=True)
        checks['direct-evidence']=verify_evidence(ROOT/'results/v0.3/direct-baseline-final')['passed']
        cmake=lock['tools']['cmake']['path']
        command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON'])
        command('build',[cmake,'--build',build,'-j2'])
        command('ctest',['ctest','--test-dir',build,'--output-on-failure'])
        command('placements',[sys.executable,ROOT/'tests/test_ir_mapping.py','--probe',build/'ir_mapping_probe','--output',out/'placements'])
        command('schema',[sys.executable,ROOT/'tests/test_ir_schema.py','--validator',build/'ir_validate'])
        command('generated',[sys.executable,ROOT/'tools/generate_ir_schema.py','--check'])
        command('baseline-tests',[sys.executable,ROOT/'tests/test_direct_baseline.py'])
        for mode in ('host','controller'):
            baseline=ROOT/'results/v0.3/direct-baseline-final'/('small-'+mode)
            cfg=out/('frozen-'+mode+'-config.json')
            write_json(cfg,dict(json.loads((baseline/'config.json').read_text()),execution_engine='direct'))
            command('frozen-'+mode,[sys.executable,ROOT/'tools/run_config.py','--config',cfg,
                '--data',baseline/'data','--build',build,'--output',out/('frozen-'+mode)])
            new=out/('frozen-'+mode)
            assertions=dict(results=json.loads((new/'results.json').read_text())==json.loads((baseline/'results.json').read_text()),
                simulated_statistics=statistics(new)==statistics(baseline),
                events=sha(new/'events.jsonl')==sha(baseline/'events.jsonl'),
                service_csv=sha(new/'service.csv')==sha(baseline/'service.csv'))
            checks['frozen-'+mode]=assertions
            if not all(assertions.values()):raise ValueError('frozen direct mismatch: '+mode)
        command('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DBUILD_TESTING=ON','-DNDPSIM_SANITIZE=ON'])
        command('asan-build',[cmake,'--build',asan,'--target','ir_mapping_probe','ir_executor_probe','runtime_probe','ir_schema_probe','-j2'])
        command('asan-tests',['ctest','--test-dir',asan,'--output-on-failure','-R',
            '^(ir_operator_placements|ir_dynamic_lifecycle|runtime_dependencies_visibility|ir_schema_memory)$'],env={'ASAN_OPTIONS':'halt_on_error=1'})
        command('diff-check',['git','diff','--check'])
        after=inventory();write_json(out/'source-end.json',after)
        checks['source-unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies-unchanged']=before['mqsim']==after['mqsim'] and before['sst_head']==after['sst_head'] and not after['sst_status']
        if not checks['source-unchanged'] or not checks['dependencies-unchanged']:raise ValueError('source/dependencies changed during verification')
    except Exception as exc:
        error=str(exc)
    write_json(out/'assertions.json',checks)
    artifacts={str(f.relative_to(out)):sha(f) for f in sorted(out.rglob('*')) if f.is_file()}
    write_json(out/'manifest.json',dict(schema='ndpsim-ir-mapping-v033-v1',task='V03-3',passed=error is None,error=error,
        input=dict(contract=str(ROOT/'examples/ir/graph-contract.json'),sha256=sha(ROOT/'examples/ir/graph-contract.json'),
                   fixture='N5 d260 R4; real MQSim preload128 sectors; query0; B2/E5/chunk2; cross-page and constrained controller buffer'),
        commands=commands,artifacts_sha256=artifacts,
        limitations='Test-only IR driver; no production engine/frontend, full AC, native oracle, full SIFT matrix or RSS gradient. ASan targets only. Hardware configured_unvalidated.'))
    print('V03-3 '+('PASS' if error is None else 'FAIL: '+error))
    return 0 if error is None else 1


if __name__=='__main__':sys.exit(main())
