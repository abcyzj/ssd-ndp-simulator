"""V03-0 only: fresh direct reference runs, identities and preregistered budgets.

This does not implement IR or execute the 52-run V0.3 acceptance matrix.
"""
import argparse
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from ann_data import write_json
from verify_m0 import ROOT, inventory, measure, sha
from verify_t6 import (BUDGET, LOCK, MATRIX, SELECTION, DiskMonitor, GIB, load,
                       validate_plan, validate_prepared, verify_cached_run)


def verify_hashes(root, hashes):
    if not hashes:
        raise ValueError('empty artifact inventory')
    for name, digest in hashes.items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha(path) != digest:
            raise ValueError('artifact identity mismatch: '+name)


def verify_evidence(out):
    m = load(out/'manifest.json')
    if m.get('schema') != 'ndpsim-direct-baseline-v1' or m.get('passed') is not True:
        raise ValueError('baseline failed or incomplete')
    verify_hashes(out, m['artifacts_sha256'])
    return m


class BaselineDiskMonitor(DiskMonitor):
    def __exit__(self, *args):
        self.stop.set(); self.thread.join(); self.sample()
        write_json(self.output/'disk-measurement.json', dict(
            roots=list(map(str, self.roots)), sampled_peak_file_bytes=self.peak,
            minimum_sampled_volume_free_bytes=self.minimum_free, samples=self.samples,
            method='1s recursive logical file sizes; may miss subsecond peaks; excludes build/cache except raw SIFT',
            error=self.error, budget_bytes=25*GIB, reserve_bytes=20*GIB,
            passed=self.error is None and self.peak <= 25*GIB and self.minimum_free >= 20*GIB))


def freeze_budget(small, ram):
    if set(small) != {'host','controller'} or not ram or ram < 4*GIB or any(m['peak_rss_bytes'] > min(4*GIB, int(.6*ram)) for m in small.values()):
        raise ValueError('physical RAM unavailable or small-run RSS exceeds budget')
    return dict(schema='ndpsim-v03-budget-v1', frozen_before_full_base_runs=True,
        physical_ram_bytes=ram, small_run_measurements=small,
        decision='Adopt PRD section 9 defaults; small direct probes fit limits. IR overhead remains unmeasured.',
        query_wall_ratio_ir_direct_max=1.5, evaluation_repeats_per_engine_placement=3,
        evaluation_order='direct/ir alternating, evaluation128 L128/E256 c1 statistics; retain all samples and median',
        ir_rss_increment_bytes_floor=128*1024**2, ir_rss_increment_fraction=.2,
        q_gradient=[32,128,512], q_repeats=3, q_rss_increment_bytes_floor=64*1024**2,
        q_rss_increment_fraction=.1, sst_peak_rss_bytes_max=min(4*GIB,int(.6*ram)),
        python_peak_rss_bytes_max=512*1024**2, initialization_seconds_max=3600,
        query_seconds_max=7200, postprocessing_seconds_max=600,
        disk_peak_bytes_max=25*GIB, volume_reserve_bytes_min=20*GIB,
        output_budget_bytes_per_run=2*GIB,
        future_ir_detail_policy='2GiB combined required+IR diagnostics per run; explicit budget failure, never truncate silently',
        scope='Raw locked SIFT + shared prepared inputs + all results/v0.3 evidence (including failed runs); exclude historical v0.2 runs, build and SST cache',
        comparison='PRD 7.1 exact same-toolchain results and all simulated statistics; semantic query/batch/resource keys; only enumerated identity/diagnostic/host-cost differences allowed',
        shared_code_policy='Compare future direct and ir separately to this immutable reference; shared refactoring cannot redefine reference values',
        limitations='No IR measurements yet; RSS checked after completion, not hard isolation; no phase/private/simultaneous process-tree RSS')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--build', type=Path, default=ROOT/'build/delivery')
    p.add_argument('--ram-evidence', type=Path, help='Fallback stdout file from sysctl -n hw.memsize when sandbox blocks sysctl; independently capture before running')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    lock = load(ROOT/'deps.lock.json')
    if sys.flags.optimize or Path(sys.executable).resolve() != Path(lock['tools']['python']['path']).resolve():
        p.error('use locked Python without -O')
    out, build, prepared, raw = (x.resolve() for x in (a.output,a.build,a.prepared,a.source_dir))
    if out.exists(): p.error('output must not exist')
    out.mkdir(parents=True)
    commands=[]; checks={}; passed=False; error=None
    before=inventory()
    if before['physical_ram_bytes'] is None and a.ram_evidence:
        ram_text=a.ram_evidence.read_text().strip()
        if not ram_text.isdecimal() or int(ram_text)<=0: p.error('invalid sysctl RAM evidence')
        before['physical_ram_bytes']=int(ram_text)
        before['physical_ram_evidence']=dict(command=['/usr/sbin/sysctl','-n','hw.memsize'],
            path=str(a.ram_evidence.resolve()),sha256=sha(a.ram_evidence),
            method='independently captured stdout; sandbox sysctl failure retained above')
        (out/'physical-ram.txt').write_bytes(a.ram_evidence.read_bytes())
    write_json(out/'source-start.json', before)
    # Preserve actual diff and full tracked/untracked source hashes, not HEAD alone.
    (out/'workspace.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    def command(name, argv, timeout=3600, rss=None):
        r=measure(list(map(str,argv)),out/(name+'.json'),timeout,rss)
        commands.append(dict(name=name,**r))
        print(f'{name}: exit={r["exit_code"]} wall={r["wall_seconds"]:.2f}s',flush=True)
        if not r['passed']: raise RuntimeError('command failed: '+name)
    try:
        with BaselineDiskMonitor(out,[raw,prepared,ROOT/'results/v0.3']):
            if before['sst_status']: raise ValueError('SST Core must be unmodified')
            if 'execution_engine' in (ROOT/'tools/run_graph.py').read_text():
                raise ValueError('runner engine selection changed; explicitly audit direct selection before freezing')
            validate_plan(); validate_prepared(prepared)
            saved=load(prepared/'prepared.json')
            verify_hashes(prepared,saved['files_sha256'])
            for key,path in [('query_selection_sha256',SELECTION),('dataset_lock_sha256',LOCK)]:
                if saved[key]!=sha(path): raise ValueError('prepared lock mismatch: '+key)
            raw_files={}
            for role,info in load(LOCK)['datasets']['sift1m']['files'].items():
                path=raw/('sift_'+role+('.ivecs' if role=='groundtruth' else '.fvecs'))
                if path.stat().st_size!=info['bytes'] or sha(path)!=info['sha256']:
                    raise ValueError('raw SIFT mismatch: '+role)
                raw_files[role]=dict(path=str(path),bytes=info['bytes'],sha256=info['sha256'])
            receipt=load(prepared/'index/index.json')['builder']['receipt']
            for path,digest in [(ROOT/'configs/benchmarks/m0-builder.lock.json',receipt['lock_sha256']),
                                (ROOT/'tools/offline/index_builder.cc',receipt['adapter_sha256']),
                                (Path(receipt['executable']),receipt['executable_sha256'])]:
                if sha(path)!=digest: raise ValueError('builder identity mismatch: '+str(path))
            write_json(out/'inputs.json',dict(raw=raw_files,prepared=str(prepared),preparation=saved,builder=receipt,
                locks={str(x.relative_to(ROOT)):sha(x) for x in (LOCK,SELECTION,BUDGET,MATRIX)}))
            checks['input_identity']=True
            cmake=lock['tools']['cmake']['path']
            command('toolchain',[sys.executable,'-c',
                'import json,subprocess; x=json.load(open("deps.lock.json")); '\
                '[(print(k,subprocess.check_output([v["path"],"--version"],text=True))) for k,v in x["tools"].items() if k!="python_config"]'])
            command('configure',[cmake,'-S',ROOT,'-B',build,'-DBUILD_TESTING=ON','-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
            command('build',[cmake,'--build',build,'-j2'])
            command('ctest',['ctest','--test-dir',build,'--output-on-failure'])
            small={}
            for mode in ('host','controller'):
                c=load(ROOT/'configs/experiments/host.json'); c.update(placement=mode,event_log=True)
                cfg=out/('small-'+mode+'-config.json'); write_json(cfg,c)
                command('small-'+mode,[sys.executable,ROOT/'tools/run_config.py','--config',cfg,'--build',build,'--output',out/('small-'+mode)])
                small[mode]=load(out/('small-'+mode)/'sst-measurement.json')
            command('small-comparison',[sys.executable,ROOT/'tools/compare_placements.py',out/'small-host',out/'small-controller','--output',out/'small-comparison-report.json'],600,512*1024**2)
            checks['small_independent']=load(out/'small-comparison-report.json')['status']=='passed'
            budget=freeze_budget(small,before['physical_ram_bytes']); write_json(out/'budget-decision.json',budget)
            budget_hash=sha(out/'budget-decision.json')
            if shutil.disk_usage(out).free < 20*GIB: raise ValueError('insufficient volume reserve')
            for group,detail in [('evaluation',False),('detail',True)]:
                for mode in ('host','controller'):
                    name=group+'-'+mode; c=copy.deepcopy(load(MATRIX)['experiment'])
                    c.update(placement=mode,index=str(prepared/'index'),truth=str(prepared/(group+'-truth')),
                             event_log=detail,output_budget_bytes=budget['output_budget_bytes_per_run'])
                    c['search'].update(entry=load(prepared/'index/index.json')['entry'],width=128,budget=256)
                    cfg=out/(name+'-config.json'); write_json(cfg,c)
                    command(name,[sys.executable,ROOT/'tools/run_config.py','--config',cfg,'--data',prepared/group,'--build',build,'--output',out/name],11400)
                    verify_cached_run(out/name,c,prepared,group,build)
                    m=load(out/name/'sst-measurement.json'); phases=m['phases_wall_seconds']
                    checks[name+'-budget']=(m['peak_rss_bytes']<=budget['sst_peak_rss_bytes_max'] and
                        m['python_parent_peak_rss_bytes']<=512*1024**2 and m['postprocessing_wall_seconds']<=600 and
                        phases['initialization_wall_seconds']<=3600 and phases['query_wall_seconds']<=7200)
            command('detail-comparison',[sys.executable,ROOT/'tools/compare_placements.py',out/'detail-host',out/'detail-controller','--output',out/'detail-comparison-report.json'],600,512*1024**2)
            checks['detail_independent']=load(out/'detail-comparison-report.json')['status']=='passed'
            from verify_t6 import result_signature
            checks['evaluation_pair']=result_signature(load(out/'evaluation-host/results.json'))==result_signature(load(out/'evaluation-controller/results.json'))
            checks['budget_unchanged']=sha(out/'budget-decision.json')==budget_hash
            verify_hashes(prepared,saved['files_sha256'])
        checks['disk_budget']=load(out/'disk-measurement.json')['passed']
        after=inventory(); write_json(out/'source-end.json',after)
        checks['source_unchanged']=before['source_sha256']==after['source_sha256'] and before['diff_sha256']==after['diff_sha256']
        checks['dependencies_unchanged']=before['mqsim']==after['mqsim'] and before['sst_head']==after['sst_head'] and not after['sst_status']
        command('diff-check',['git','diff','--check'])
        if not all(checks.values()): raise ValueError('baseline gates failed')
        passed=True
    except Exception as exc:
        error=str(exc)
        raise
    finally:
        write_json(out/'assertions.json',checks)
        write_json(out/'manifest.json',dict(schema='ndpsim-direct-baseline-v1',task='V03-0',passed=passed,error=error,
            execution_engine='direct',engine_selection='implicit existing GraphSearch service callbacks; no IR selector implemented',
            scope='fresh synthetic pair + complete SIFT1M evaluation128 statistics and detail8 pair; not full AC or 52-run matrix',
            source_inventory='source-start.json',commands=commands,
            artifacts_sha256={str(f.relative_to(out)):sha(f) for f in out.rglob('*') if f.is_file() and f!=out/'manifest.json'},
            limitations='configured_unvalidated; official FP64 tie boundary unresolved; fixed query subsets; cached SST build; sampled disk and whole-child RSS; no IR implemented'))
    verify_evidence(out)
    print('V03-0 passed; no next task started',flush=True)


if __name__=='__main__': main()
