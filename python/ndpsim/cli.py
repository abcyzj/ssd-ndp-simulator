"""Unified V0.4.5 tools. Normal commands never build or run acceptance implicitly."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import time
from .execution import budget, enforce, identities, launch, load, preflight, resolve, save, size
from .static_ir import OperatorRegistry, file_hash, require, validate_package
from .reporting import reduce_trace


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version='ndpsim 0.4.5.dev15')
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('simulate', help='run an already built timing-only SST simulator')
    s.add_argument('--config', required=True, type=Path)
    s.add_argument('--build', required=True, type=Path)
    s.add_argument('--output', required=True, type=Path)
    c = sub.add_parser('capture', help='explicit graph adapter: capture, sample or benchmark separately')
    c.add_argument('--workload', choices=['graph'], required=True)
    c.add_argument('--adapter', required=True, type=Path, help='independent graph-search/python/graph_capture.py')
    c.add_argument('--native', required=True, type=Path)
    c.add_argument('--operators', required=True, type=Path)
    c.add_argument('--config', required=True, type=Path)
    c.add_argument('--output', required=True, type=Path)
    c.add_argument('--mode', choices=['capture','sample','benchmark','uninstrumented'], default='capture')
    c.add_argument('--package', type=Path)
    c.add_argument('--stride', type=int, default=1)
    c = sub.add_parser('check', help='IR structural validation; semantic verification stays in workload adapter')
    c.add_argument('--package', required=True, type=Path)
    c.add_argument('--registry', required=True, type=Path)
    c.add_argument('--report', required=True, type=Path)
    c.add_argument('--expected-package-sha256')
    c.add_argument('--allow-fixture', action='store_true')
    c.add_argument('--adapter', type=Path, help='explicit graph_capture.py for graph metadata rules or semantic check')
    c.add_argument('--semantic-config', type=Path, help='native payload config; only for explicit semantic check')
    c.add_argument('--reference', type=Path, help='independent native reference, required with semantic-config')
    f = sub.add_parser('fit', help='independent fit/table/evaluate/measurement-quality tools')
    f.add_argument('--manifest', required=True, type=Path)
    f.add_argument('--action', choices=['fit','table','table-check','evaluate','check'], default='fit')
    f.add_argument('--allow-fixture', action='store_true')
    f.add_argument('--output', required=True, type=Path)
    f.add_argument('--budget', required=True, type=Path, help='execution budget JSON (RAM, disk and timeout); recipe budget still applies')
    for name, help_text in [('compare','descriptive comparison of completed reports (no accuracy verdict)'),('verify','validate capture, scheduling/ledger or native-vs-sim accuracy evidence')]:
        c = sub.add_parser(name, help=help_text)
        c.add_argument('--spec', required=True, type=Path)
        c.add_argument('--output', required=True, type=Path)
    return p


def simulate(args, output):
    start=time.monotonic()
    path = args.config.resolve(); spec = load(path)
    require(set(spec) == {'format','run','budget'} and spec['format'] == 'ndpsim-experiment/1', 'expected ndpsim-experiment/1 with run and budget')
    b = budget(spec['budget']); run_path = resolve(path.parent,spec['run']); c = load(run_path)
    require(c.get('format') in ('ndpsim-memory-run/1','ndpsim-graph-ssd-run/1'), 'unsupported run format; old engines/configurations require migration')
    save(output/'experiment-input.json', spec)
    save(output/'run-input.json', c)
    names = ['registry','profile','report']+[n for n in ('semantic_report','layout','device') if n in c]
    inputs = {n:resolve(run_path.parent,c[n]) for n in names}
    package = resolve(run_path.parent,c['package'])
    # Package itself contains metadata only; no original payload/truth paths are resolved.
    require(package.is_dir() and not any(p.is_symlink() for p in package.rglob('*')), 'package must be a regular metadata directory')
    input_size = sum(p.stat().st_size for p in inputs.values())+size(package)
    preflight(output,b,input_size)
    snap = output/'inputs'; snap.mkdir()
    before = {}
    for name,p in inputs.items():
        before[str(p)] = file_hash(p)
        dest = snap/(name+p.suffix);shutil.copyfile(p,dest);c[name]=str(dest)
    shutil.copytree(package,snap/'package');c['package']=str(snap/'package')
    package_hashes = {str(p.relative_to(package)):file_hash(p) for p in package.rglob('*') if p.is_file()}
    require(package_hashes == {str(p.relative_to(snap/'package')):file_hash(p) for p in (snap/'package').rglob('*') if p.is_file()}, 'package changed while snapshotting')
    c['scratch']=str(output/'execution.sqlite');c['output']=str(output/'trace.jsonl')
    save(output/'effective-config.json',c)
    build = args.build.resolve(); paths=load(build/'sst-paths.json')
    sst=resolve(build,paths['sst']); require(sst.is_file(), 'SST binary missing; build explicitly before simulate')
    module = 'memory' if c['format']=='ndpsim-memory-run/1' else 'ssd'
    libraries = sorted(build.glob('libndpsim_'+('memory' if module=='memory' else 'graph_ssd')+'.*'))
    require(libraries, 'timing-only SST module missing; build explicitly before simulate')
    entry = Path(__file__).with_name('sst')/(module+'.py')
    save(output/'input-identities.json', dict(files=before, package_files=package_hashes,package_sha256=c['package_sha256'],profile_sha256=c['profile_sha256'],
        build={str(p):file_hash(p) for p in [sst,build/'sst-paths.json',*libraries,entry]},
        implementation={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')}))
    # SST serializes model argv as shell-like text internally; JSON quoting preserves spaces/unicode.
    import json
    launch([str(sst),'--add-lib-path='+str(build),str(entry),'--',json.dumps(str(output/'effective-config.json'),ensure_ascii=False)],output,b)
    require(all(file_hash(p)==h for p,h in before.items()),'input changed during run')
    result = reduce_trace(output,c,b,start+b['timeout_seconds'])
    import resource
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    save(output/'cli-resources.json',dict(peak_cli_rss_bytes=peak,wall_seconds=time.monotonic()-start))
    require(peak <= b['rss_bytes'],'CLI peak RSS budget')
    require(time.monotonic()-start <= b['timeout_seconds'],'complete simulation timeout budget')
    enforce(output,b)
    return result


def capture(args, output):
    require(args.stride > 0 and (args.mode != 'capture' or args.stride == 1),'complete capture requires stride 1')
    require(args.mode != 'sample' or args.package is not None,'sample requires separately captured package')
    config=load(args.config)
    b=config['budget']
    # Adapter owns native and semantic RAM/time/disk enforcement and input snapshots.
    adapter=args.adapter.resolve(); require(adapter.name=='graph_capture.py' and adapter.is_file(),'explicit graph capture adapter required')
    limits=budget(dict(output_bytes=b['output_bytes']+1024**2,scratch_bytes=2*b['output_bytes'],rss_bytes=max(b['native_rss_bytes'],b['postprocess_rss_bytes']),timeout_seconds=b['timeout_seconds'],min_disk_free_bytes=b['min_disk_free_bytes']))
    preflight(output,limits)
    command=[sys.executable,str(adapter),args.mode,'--config',str(args.config.resolve()),'--operators',str(args.operators.resolve()),'--binary',str(args.native.resolve()),'--output',str(output/'capture'),'--stride',str(args.stride)]
    if args.package:command+=['--package',str(args.package.resolve())]
    save(output/'adapter-identities.json',{str(p):file_hash(p) for p in [adapter,*sorted(adapter.parent.glob('graph_*.py')),args.operators.resolve(),args.native.resolve()]})
    launch(command,output,limits)
    require(load(output/'capture/status.json')['passed'] is True,'capture failed')
    save(output/'report.json',dict(format='ndpsim-capture-report/1',mode=args.mode,reference_results='capture/reference.json: native reference evidence, not simulated results',full_acceptance_passed=False))


def compare(args, output):
    path=args.spec.resolve();spec=load(path)
    require(set(spec)=={'format','runs'} and spec['format']=='ndpsim-report-comparison/1','expected ndpsim-report-comparison/1')
    require(type(spec['runs']) is list and len(spec['runs'])>=2,'at least two report references required')
    rows=[]; seen=set()
    for ref in spec['runs']:
        require(set(ref)=={'path','sha256'},'comparison report reference')
        p=resolve(path.parent,ref['path']);require(p not in seen,'duplicate comparison report');seen.add(p)
        require(file_hash(p)==ref['sha256'],'comparison report hash')
        status=load(p.parent/'status.json')
        require(status.get('passed') is True and status.get('artifacts_sha256',{}).get(p.name)==ref['sha256'],'comparison requires successful hash-bound run status')
        r=load(p);require(r.get('format')=='ndpsim-run-report/1' and r.get('execution_succeeded') is True,'completed run report required')
        rows.append(dict(path=str(p),sha256=ref['sha256'],placement=r['placement'],package_sha256=r['package_sha256'],target_hardware=r['target_hardware'],latency_ns=r['latency_ns'],measurement_window_ns=r['measurement_window_ns'],service_sources=r['service_sources']))
    require(all(r['package_sha256']==rows[0]['package_sha256'] and r['target_hardware']==rows[0]['target_hardware'] for r in rows),'comparison package/target mismatch')
    save(output/'report.json',dict(format='ndpsim-report-comparison/1',runs=rows,scope='descriptive only; no native-vs-sim accuracy or SSD speedup verdict',full_acceptance_passed=False))


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command=='check':
        report=args.report.resolve()
        try:
            require(not report.is_relative_to(args.package.resolve()),'report must be outside package')
            with report.open('xb') as f:
                try:
                    if args.adapter:
                        import subprocess
                        import tempfile
                        require(args.adapter.name=='graph_capture.py','explicit graph adapter required')
                        require(bool(args.semantic_config)==bool(args.reference),'semantic-config and reference are required together')
                        with tempfile.TemporaryDirectory(prefix='ndpsim-check-') as tmp:
                            result=Path(tmp)/'result'
                            command=[sys.executable,str(args.adapter.resolve()),'check' if args.semantic_config else 'structure','--operators',str(args.registry.resolve()),'--package',str(args.package.resolve()),'--output',str(result)]
                            if args.semantic_config:command+=['--config',str(args.semantic_config.resolve()),'--reference',str(args.reference.resolve())]
                            if args.allow_fixture:command+=['--allow-fixture']
                            p= subprocess.run(command,capture_output=True,text=True,timeout=300)
                            require(p.returncode==0,'graph validation failed: '+p.stderr)
                            r=load(result/('semantic.json' if args.semantic_config else 'structural.json'))
                            require(args.allow_fixture or r.get('source',r.get('provenance',{}).get('kind'))!='synthetic-fixture','synthetic fixture requires --allow-fixture')
                            r['adapter_command']=dict(argv=command,exit_code=p.returncode,source_sha256=file_hash(args.adapter))
                            if args.expected_package_sha256:require(r['package_sha256']==args.expected_package_sha256,'expected package hash mismatch')
                    else:
                        require(not args.semantic_config and not args.reference,'semantic check requires explicit workload adapter')
                        r=validate_package(args.package,OperatorRegistry.load(args.registry),allow_fixture=args.allow_fixture,expected_package_sha256=args.expected_package_sha256)
                    r.update(full_acceptance_passed=False,semantic_validation='independent-full-query' if args.semantic_config else 'structure-only')
                    from .static_ir import canonical
                    f.write(canonical(r))
                except Exception as exc:
                    from .static_ir import canonical
                    f.write(canonical(dict(structural_passed=False,independent_semantic_passed=False,error=str(exc),full_acceptance_passed=False)));raise
            return 0
        except Exception as exc:print(str(exc),file=sys.stderr);return 2
    output=args.output.resolve();owned=False;start=time.monotonic();code=2
    try:
        output.mkdir(parents=True,exist_ok=False);owned=True
        save(output/'invocation.json',dict(argv=argv if argv is not None else sys.argv[1:],cwd=str(Path.cwd()),python=sys.version,executable=sys.executable))
        if args.command=='simulate':simulate(args,output)
        elif args.command=='capture':capture(args,output)
        elif args.command=='fit':
            b=budget(load(args.budget));preflight(output,b)
            command=[sys.executable,'-m','ndpsim.fit_cli',args.action,'--manifest',str(args.manifest.resolve()),'--output',str(output/'fit')]
            if args.allow_fixture:command+=['--allow-fixture']
            launch(command,output,b)
            require(load(output/'fit/status.json')['passed'] is True,'fitting failed')
        elif args.command=='compare':compare(args,output)
        elif args.command=='verify':
            spec=load(args.spec)
            if spec.get('format') != 'ndpsim-validation/1':
                raise NotImplementedError('expected ndpsim-validation/1 layer spec; full release aggregation belongs to P21')
            b=budget(spec['budget']);preflight(output,b)
            launch([sys.executable,'-m','ndpsim.validation',str(args.spec.resolve()),str(output)],output,b)
            require(load(output/'report.json').get('layer_passed') is True,'validation failed')
            enforce(output,b)
        code=0
    except NotImplementedError as exc:
        code=3;print(str(exc),file=sys.stderr)
        if owned:save(output/'error.json',dict(error=str(exc)))
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        if owned:
            if (output/'report.json').exists():(output/'report.json').rename(output/'report.failed.json')
            if (output/'fit/profile.json').exists():(output/'fit/profile.json').rename(output/'fit/profile.failed.json')
            save(output/'error.json',dict(error=str(exc)))
    finally:
        if owned:
            save(output/'status.json',dict(passed=code==0,exit_code=code,command=args.command,wall_seconds=time.monotonic()-start,full_acceptance_passed=False,artifacts_sha256=identities(output)))
    return code


if __name__=='__main__':raise SystemExit(main())
