"""M4.2 AC matrix, isolated parameter equations, replay and immutable native regression.

--quick is a small CTest subset, never reported as full AC acceptance.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from ann_data import digest, write_json
from compare_m3_4 import compare, inspect
from run_m3_1 import ROOT
from verify_m1_1 import mqsim_source_snapshot


def rows(s, prefixes):
    return [l for l in s.splitlines() if l.startswith(prefixes)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--quick', action='store_true')
    p.add_argument('--build', type=Path, default=ROOT/'build/m4.2')
    p.add_argument('--output', type=Path, default=ROOT/'docs/evidence/m4.2')
    a=p.parse_args(); build=a.build.resolve(); out=a.output.resolve(); out.mkdir(parents=True,exist_ok=True)
    lock=json.loads((ROOT/'deps.lock.json').read_text()); cmake=Path(lock['tools']['cmake']['path'])
    commands=[]; matrix={}; parameters={}; before=mqsim_source_snapshot()
    def command(name, argv, env=None):
        argv=list(map(str,argv));start=time.perf_counter()
        result=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=600,env=dict(os.environ,**(env or {})))
        (out/(name+'.log')).write_text(result.stdout+result.stderr)
        commands.append(dict(name=name,argv=argv,env=env or {},exit_code=result.returncode,wall_seconds=time.perf_counter()-start))
        assert result.returncode==0,(name,result.stderr[-1000:])
        return result.stdout+result.stderr
    def passed(ac, assertion, evidence):
        matrix.setdefault(ac,[]).append(dict(assertion=assertion,evidence=evidence,status='passed'))
    def graph(name, config, target=build, data=None):
        path=out/(name+'-config.json');write_json(path,config)
        argv=[sys.executable,ROOT/'tools/run_m4_1.py','--config',path,'--build',target,'--output',out/name]
        if data:argv+=['--data',data]
        command(name,argv)
        return json.loads((out/name/'results.json').read_text())
    def events(name):
        return [json.loads(l) for l in (out/name/'events.jsonl').read_text().splitlines()]
    try:
        if not a.quick:
            command('configure',[cmake,'-S',ROOT,'-B',build,'-DNDPSIM_SLOW_FUNCTIONAL=OFF'])
            command('build',[cmake,'--build',build,'-j4'])
            command('ctest',[cmake.with_name('ctest'),'--test-dir',build,'--output-on-failure'])
        paths=json.loads((build/'sst-paths.json').read_text())
        # Isolated chain driven by real SST events: no shared CPU/link bottleneck.
        for stage in ('base','read','compute','return'):
            env={f'NDPSIM_{s.upper()}_DELAY':str(100 if s==stage else 0) for s in ('read','compute','return')}
            s=command('causal-'+stage,[paths['sst'],'--add-lib-path='+str(build),ROOT/'configs/m2.2/runtime.py'],env)
            expected=55+(stage!='base')*100
            assert f'probes=4 independent=7 successor={expected} live=0' in s
            parameters['causal-'+stage]=dict(independent_ns=7,successor_ns=expected,extra_ns=expected-55)
        passed('AC-03','Input/compute/return +100ns each shifts successor 55->155ns; independent query stays at 7ns','causal-*.log')
        passed('AC-04','Four real SST try_get probes per case reject private results during service and return; successor checks real byte 43','causal-*.log')
        nand=[]
        for extra in (0,100):
            text=command('nand-'+str(extra),[paths['sst'],'--add-lib-path='+str(build),ROOT/'configs/m1.3/ordinary.py'],
                {'NDPSIM_QD':'1','NDPSIM_MISS':'0','NDPSIM_OBSERVE':'1','NDPSIM_SINGLE_READ':'1','NDPSIM_NAND_READ_DELAY':str(extra)})
            observations=[json.loads(l[4:]) for l in text.splitlines() if l.startswith('OBS ')]
            submitted=[e for e in observations if e['event_kind']=='request_submit']
            complete=[e for e in observations if e['event_kind']=='request_complete']
            array=[e for e in observations if e['event_kind']=='interval' and e['kind']=='array']
            assert len(submitted)==len(complete)==len(array)==1
            assert submitted[0]['lba']==0 and submitted[0]['bytes']==512 and 'read_cmt_misses=0' in text
            nand.append(dict(extra_ns=extra,latency_ns=complete[0]['time_ns']-submitted[0]['time_ns'],array_ns=array[0]['end_ns']-array[0]['start_ns']))
        assert nand[1]['latency_ns']-nand[0]['latency_ns']==nand[1]['array_ns']-nand[0]['array_ns']==100
        parameters['nand']=nand
        passed('AC-03','Real MQSim one-sector data-cache-off/CMT-hit read: NAND +100ns adds exactly 100ns to array and NVMe completion latency','nand-0.log, nand-100.log')
        # Existing isolated probes have active C++ assertions even in release builds.
        operator=command('host-formula',[build/'operator_probe'])
        assert 'distance=3+ceil(27/4)=10' in operator and 'doubled_rate=7' in operator
        walls=re.findall(r'WALL repeats=(\d+) slots=(\d+) ns=(\d+)',operator)
        assert len(walls)==3 and int(walls[1][2])>int(walls[0][2])
        link=command('link-formula',[build/'pcie_contract_probe'])
        for rate in (1,2,1000000):
            h=25//rate;d=19//rate
            assert f'ISOLATED rate={rate} h2d={h},{2*h} d2h={d} wire=100,100,76' in link
        parameters['isolated']=dict(host_flops=27,host_setup_ns=3,rate4_ns=10,rate8_ns=7,
            pcie='native floor(wire/(4*lane_GBps)); rates 1/2/1000000, FIFO and duplex asserted',functional_wall_ns=walls)
        passed('AC-11','Isolated host rate 4->8 uses setup+ceil(work/rate); PCIe lane bandwidth 1/2/1000000 preserves native truncation incl zero-ns FIFO','host-formula.log, link-formula.log')
        passed('AC-10','20000 equivalent evaluations increase wall time with exactly equal service schedule and score bytes','host-formula.log')
        base=json.loads((ROOT/'configs/m3.4/branch.json').read_text());base['event_log']=True
        for fixture in ('branch','mutated'):
            c=copy.deepcopy(base);c['generate']['fixture']=fixture
            for mode in ('host','controller'):
                c['placement']=mode;name=fixture+'-'+mode;graph(name,c)
                decisions=[e for e in events(name) if e['type']=='decision']
                reads=[e for e in events(name) if e['type']=='read_submit' and e['kind']=='adjacency']
                node,lba=(1,6) if fixture=='branch' else (2,7)
                assert decisions[1]['expand']==[node] and [(e['id'],e['lba']) for e in reads]==[(0,5),(node,lba)]
            report=compare(out/(fixture+'-host'),out/(fixture+'-controller'));write_json(out/(fixture+'-comparison.json'),report)
        va=(out/'branch-host/data/vectors.f32').read_bytes();vb=(out/'mutated-host/data/vectors.f32').read_bytes()
        assert va[:4]==vb[:4] and va[8:]==vb[8:] and va[4:8]!=vb[4:8]
        for name in ('adjacency.u32','queries.f32'):
            assert (out/'branch-host/data'/name).read_bytes()==(out/'mutated-host/data'/name).read_bytes()
        passed('AC-01','Both CLI placements emit results, distances, independently generated Recall, latency and config snapshot','branch-*/')
        passed('AC-02','Only vector x1 changes 1->5; both placements expand 1->2 and adjacency LBA 6->7','branch-*/, mutated-*/')
        passed('AC-05','Independent offline replay compares every candidate, decision and top-k with abs1e-9+rel1e-7','*-comparison.json')
        passed('AC-06','Offline ledger asserts zero controller vector DMA, query/IDs/results/ACKs and native NVMe metadata fees, each transfer charged once','*-comparison.json')
        # Sequential real graph isolates additional model cost from contention.
        for mode in ('host','controller'):
            c=copy.deepcopy(base);c['placement']=mode
            c['generate'].update(fixture='smoke',n=16,d=32,degree=3,queries=1)
            c['search'].update(k=3,width=8,budget=4)
            reference=graph(mode+'-rate-base',c)
            if mode=='host':c['host']['distance_rate']=8
            else:c['controller']['flops_per_ns']=32
            name=mode+'-rate';changed=graph(name,c,data=out/(mode+'-rate-base')/'data');inspect(out/name)
            ev0=events(mode+'-rate-base');ev1=events(name)
            kind='service' if mode=='host' else 'controller_service'
            select=lambda ev:[e for e in ev if e['type']==kind and (e.get('kind')=='distance' if mode=='host' else e['stage']=='arithmetic')]
            old,new=select(ev0),select(ev1);assert len(old)==len(new)>0
            delta=sum((x['end_ns']-x['start_ns'])-(y['end_ns']-y['start_ns']) for x,y in zip(old,new))
            assert delta>0 and reference['queries'][0]['latency_ns']-changed['queries'][0]['latency_ns']==delta
            assert reference['queries'][0]['top_k']==changed['queries'][0]['top_k']
            parameters[mode+'-graph-rate']=dict(old_rate=4 if mode=='host' else 16,new_rate=8 if mode=='host' else 32,service_tasks=len(old),latency_reduction_ns=delta)
        passed('AC-11','Single-query graph host/controller rate sweep: every service follows ceil formula; total latency delta equals summed distance service delta','host-rate/, controller-rate/, parameters.json')
        if not a.quick:
            command('concurrency',[sys.executable,ROOT/'tools/verify_m4_1.py','--matrix-only','--build',build,'--output',out/'concurrency'])
            concurrency=json.loads((out/'concurrency/manifest.json').read_text());assert concurrency['status']=='passed'
            for ac,claim in [('AC-07','Two placements concurrency1/2/4 complete; one-block credit wait positive, bounded peak, admission throttling; too-small rejected; credits/handles zero'),('AC-09','Independent SSD channel/die interval unions and link/request conservation; JSON/CSV quantile samples and Unsupported plane/latch'),('AC-10','Default N2048/d32/Q32 both placements: concurrency1/2/4 top-k invariant; on/off/repeat identical results and all simulation resource statistics')]:
                passed(ac,claim,'concurrency/manifest.json')
            # Full sequential default semantics/path ledger, plus split/cross-page cases.
            c=json.loads((ROOT/'configs/m4.1/host.json').read_text());c['event_log']=True
            for fixture in ('default','split','cross'):
                if fixture!='default':
                    c['generate'].update(n=33 if fixture=='split' else 17,d=32 if fixture=='split' else 260,degree=4,queries=2)
                    c['search'].update(k=4,width=8,batch=2,budget=8,chunk=16 if fixture=='split' else 3)
                    c['controller'].update(capacity_bytes=1248 if fixture=='split' else 10488,vector_sector_bound=512 if fixture=='split' else 1536,controller_slots=1 if fixture=='split' else 2)
                for mode in ('host','controller'):
                    c['placement']=mode;graph(fixture+'-'+mode,c,data=out/(fixture+'-host/data') if mode=='controller' else None)
                write_json(out/(fixture+'-comparison.json'),compare(out/(fixture+'-host'),out/(fixture+'-controller')))
                if fixture=='cross':assert any(len(e['transactions'])>1 for e in events('cross-controller') if e['type']=='local_request')
                if fixture=='split':assert any(len(e['scores'])>2 for e in events('split-controller') if e['type']=='score_visible')
            passed('AC-05','Default 32-query complete semantic/path replay plus real split chunks and d260 cross-page/two slots','default-comparison.json, split-comparison.json, cross-comparison.json')
            passed('AC-06','Full data path and byte ledger rechecked for default, chunk split, and cross-page fixtures','*-comparison.json')
            # Safe controller failures and oversized request checks are active in each scenario.
            for mode in ('','command','staged','evaluate','result','open-command','query-staged'):
                s=command('failure-'+(mode or 'capacity'),[build/'controller_probe',mode]);assert 'live=0' in s
            passed('AC-07','Oversized request atomically rejected; six injected safe failures drain all credits/handles/slots/protocol state','failure-*.log')
            slow=build.with_name(build.name+'-slow')
            command('slow-configure',[cmake,'-S',ROOT,'-B',slow,'-DSST_ROOT='+paths['prefix'],'-DNDPSIM_SLOW_FUNCTIONAL=ON'])
            command('slow-build',[cmake,'--build',slow,'--target','ndpsim','-j4'])
            for mode in ('host','controller'):
                c=copy.deepcopy(base);c['placement']=mode
                # Larger functional input makes the implementation-speed difference observable.
                c['generate'].update(fixture='smoke',n=64,d=32,degree=5,queries=4);c['search'].update(k=3,width=8,budget=6)
                graph('fast-'+mode,c)
                graph('slow-'+mode,c,target=slow,data=out/('fast-'+mode)/'data')
                assert 'NDPSIM_FUNCTIONAL_VARIANT repeated_l2=2048' in (out/('slow-'+mode)/'sst.log').read_text()
                assert 'NDPSIM_FUNCTIONAL_VARIANT' not in (out/('fast-'+mode)/'sst.log').read_text()
                assert events('fast-'+mode)==events('slow-'+mode)
                fast=json.loads((out/('fast-'+mode)/'statistics.json').read_text());slower=json.loads((out/('slow-'+mode)/'statistics.json').read_text())
                fw=fast.pop('wall_seconds');sw=slower.pop('wall_seconds');assert sw>fw and fast==slower
                assert json.loads((out/('fast-'+mode)/'results.json').read_text())==json.loads((out/('slow-'+mode)/'results.json').read_text())
                parameters['slow-'+mode]=dict(fast_wall_seconds=fw,slow_wall_seconds=sw,events_equal=True,statistics_equal_except_wall=True)
            passed('AC-10','Separate compiled repeated-L2 implementation is measurably slower in both real SST placements; entire event stream/results/resources equal','fast-*/, slow-*/, slow-configure.log')
            native=Path(tempfile.mkdtemp(prefix='m4.2-native-',dir=build));archive=native/'source.tar';commit=lock['dependencies']['MQSim']['commit']
            command('native-export',['git','-C',ROOT/'third_party/MQSim','archive','-o',archive,commit])
            with tarfile.open(archive) as t:t.extractall(native,filter='data')
            sources=sorted(x for x in (native/'src').rglob('*.cpp') if x.name!='main.cpp')
            parameters['native_oracle']=dict(commit=commit,archive_sha256=digest(archive),directory=str(native),source_sha256={str(x.relative_to(native)):digest(x) for x in (native/'src').rglob('*') if x.is_file()})
            command('native-build',[lock['tools']['cxx']['path'],'-isysroot',lock['target']['sdk_path'],'-std=c++17','-O1','-Xclang','-mconstructor-aliases','-DNDPSIM_NATIVE_ORACLE','-I'+str(native/'src'),'-I'+str(ROOT/'src'),'-I'+str(ROOT/'tests'),'-I'+str(build/'generated'),ROOT/'tests/ordinary_native.cc',*sources,'-o',native/'probe'])
            prefix=('IO ','INITIAL ','MAP ','CMT ','CACHE ','FINAL ')
            for name,args,qd,miss in [('qd1',[],1,0),('qd32',['normal','concurrent'],32,0),('cmt-miss',['miss','concurrent'],32,1)]:
                expected=rows(command(name+'-native',[native/'probe',*args]),prefix);assert any(l.startswith('IO ') for l in expected)
                for i in (1,2):
                    actual=command(f'{name}-sst-{i}',[paths['sst'],'--add-lib-path='+str(build),ROOT/'configs/m1.3/ordinary.py'],{'NDPSIM_QD':str(qd),'NDPSIM_MISS':str(miss),'NDPSIM_SINGLE_READ':'0','NDPSIM_NAND_READ_DELAY':'0','NDPSIM_OBSERVE':'0'})
                    assert rows(actual,prefix)==expected
            passed('AC-08','Unmodified locked native export/build vs real SST twice for QD1/QD32/small-CMT; exact request order/LBA/size/completion and mapping/cache/initial state','native-*.log, qd*-*.log, cmt-miss-*.log')
            asan=build.with_name(build.name+'-asan')
            command('asan-configure',[cmake,'-S',ROOT,'-B',asan,'-DSST_ROOT='+paths['prefix'],'-DNDPSIM_SANITIZE=ON'])
            command('asan-build',[cmake,'--build',asan,'--target','runtime_probe','operator_probe','controller_probe','controller_resource_probe','pcie_contract_probe','ordinary_io_probe','-j4'])
            command('asan-ctest',[cmake.with_name('ctest'),'--test-dir',asan,'-R','^(runtime_dependencies_visibility|operators_host_cost|controller_.*|shared_pcie_contract|ordinary_.*)$','--output-on-failure'])
            assert set(matrix)=={f'AC-{i:02}' for i in range(1,12)}
        command('diff-check',['git','diff','--check'])
        assert not command('sst-status',['git','-C',ROOT/'third_party/sst-core','status','--porcelain'])
        assert mqsim_source_snapshot()==before
        status='passed'
    finally:
        write_json(out/'parameters.json',parameters);write_json(out/'ac-matrix.json',dict(scope='quick subset' if a.quick else 'full M4.2',status=locals().get('status','failed'),checks=matrix))
        inputs=[ROOT/'AGENTS.md',ROOT/'CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'docs/prd.md',ROOT/'docs/implementation-plan.md',ROOT/'docs/m4.2-acceptance-regression.md',*sorted((ROOT/'src').rglob('*.cc')),*sorted((ROOT/'src').rglob('*.h')),*sorted((ROOT/'tools').glob('*.py')),*sorted((ROOT/'tests').glob('*')), *sorted((ROOT/'configs').rglob('*'))]
        write_json(out/'manifest.json',dict(status=locals().get('status','failed'),scope='quick subset' if a.quick else 'full M4.2',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),element_sha256=digest(build/'libndpsim.so'),commands=commands,mqsim_source=mqsim_source_snapshot(),oracle_commit=lock['dependencies']['MQSim']['commit'],sst_commit=subprocess.check_output(['git','-C',str(ROOT/'third_party/sst-core'),'rev-parse','HEAD'],text=True).strip(),inputs_sha256={str(x.relative_to(ROOT)):digest(x) for x in inputs if x.is_file()},artifacts_sha256={str(x.relative_to(out)):digest(x) for x in out.rglob('*') if x.is_file() and x.name!='manifest.json'}))
    print('M4.2 PASS', 'quick subset' if a.quick else 'AC-01..AC-11',out)


if __name__=='__main__':main()
