"""Reproducible native capture + independent oracle + size-only dual mapping.

Run from any cwd; Python imports only the installed public ndpsim package.
"""
import argparse
import os
import json
from pathlib import Path
import struct
import subprocess
import hashlib
from ndpsim.static_ir import OperatorRegistry, canonical, decode, file_hash, seal_capture, validate_package, require
from ndpsim.cost_models import schema_hash
from ndpsim.layout import packed_layout
HERE=Path(__file__).resolve().parent
TARGET=dict(id='example-target',hardware='hypothetical',os='none',frequency='fixed',affinity='two-slots',memory_bytes=8192)
def save(p,v):Path(p).write_bytes(canonical(v))
def registry():return OperatorRegistry.load(HERE/'operators.json')
def semantic(package, raw, registry):
    """Independent Python integer oracle; never calls the native function or simulator."""
    report=validate_package(package,registry)
    manifest=decode((package/'manifest.json').read_bytes())
    ops=[decode(line) for line in (package/'operations.jsonl').read_bytes().splitlines()]
    require(len(raw)==32,'oracle input size')
    values=struct.unpack('<8I',raw)
    expected=dict(left=sum(values[:4]),right=sum(values[4:]),join=sum(values))
    require(manifest['provenance']['input_content_sha256']==hashlib.sha256(raw).hexdigest(),'oracle input identity')
    require(manifest['objects']==[dict(id='block',version=0,type='u32-le',size_bytes=32,shape=[8],content_sha256=hashlib.sha256(raw).hexdigest())],'oracle object')
    require(manifest['terminals']==[dict(query=0,op='join',stage='sum')],'oracle terminal')
    require(len(ops)==3 and {o['id'] for o in ops}==set(expected),'oracle operation set')
    for op in ops:
        name=op['id'];join=name=='join';require(len(op['stages'])==1,'oracle stage count');s=op['stages'][0]
        work=dict(elements=dict(value=2 if join else 4,unit='count'))
        require(op['query']==0 and op['operator']=='example.BLOCK_SUM' and op['operator_version']=='1' and op['semantic_work']==work,'oracle operator/Work')
        require(s['id']=='sum' and s['kind']=='sum' and s['work']==work and s['features']=={} and s['context']=='example-readonly/1','oracle stage/Work')
        require(s['topological_rank']=={'left':0,'right':1,'join':2}[name] and s['decomposition_rule']=='none/1' and not s['state_versions'],'oracle rank/state')
        require(s['outputs']==[dict(port='out',type='example.u64',logical_bytes=8,version=0,digest=hashlib.sha256(str(expected[name]).encode()).hexdigest())],'oracle numerical output')
        deps=[dict(kind='DATA',source=dict(key=dict(query=0,op=n,stage='sum'),port='out'),input=n) for n in ('left','right')] if join else []
        require(s['dependencies']==deps,'oracle fork/join dependencies')
        accesses=[] if join else [dict(object='block',version=0,offset_bytes=0 if name=='left' else 16,length_bytes=16)]
        require(s['accesses']==accesses,'oracle accesses')
    return dict(format='ndpsim-block-sum-semantic/1',independent_semantic_passed=True,structural_passed=True,package_sha256=report['package_sha256'],provenance=manifest['provenance'],query_ids=[0],expected=expected)
def prepare(native,device,output,values=None):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    values=values or [1,2,3,4,5,6,7,8]
    raw=struct.pack('<8I',*values);payload=output/'input.u32';payload.write_bytes(raw)
    reg=registry();save(output/'registry.json',reg.descriptor)
    source=hashlib.sha256(b''.join(p.read_bytes() for p in (HERE/'native.cc',HERE/'contract.h',HERE/'CMakeLists.txt'))).hexdigest()
    command=[str(Path(native).resolve()),str(payload),str(output/'capture'),reg.sha256,source]
    p=subprocess.run(command,capture_output=True,text=True,timeout=30)
    save(output/'native-command.json',dict(command=command,cwd=str(Path.cwd()),exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr))
    require(p.returncode==0,p.stderr)
    seal_capture(output/'capture',output/'package',reg)
    report=validate_package(output/'package',reg);save(output/'structural.json',report)
    checked=semantic(output/'package',raw,reg);save(output/'semantic.json',checked)
    require(json.loads(p.stdout)==checked['expected'],'native/oracle output')
    manifest=decode((output/'package/manifest.json').read_bytes())
    save(output/'layout.json',packed_layout(manifest['objects'],capacity_sectors=512))
    # Explicit analytic teaching costs, not a fit or a hardware calibration.
    rules=[]
    for location in ('host','controller'):
        rules.append(dict(operator='example.BLOCK_SUM',operator_version='1',stage_kind='sum',context='example-readonly/1',location=location,target_id=TARGET['id'],feature_ranges=[dict(name='elements',unit='count',minimum=2,maximum=4)],work_units=dict(elements='count'),source='prediction',formula='nonnegative-linear/1',parameter_schema_sha256=schema_hash('nonnegative-linear/1'),parameters=dict(intercept_ns=3,coefficients=[2]),measurement_sha256=['1'*64],training_query_ids=[1],fit_report_sha256='2'*64))
    save(output/'profile.json',dict(format='ndpsim-cost-profile/1',fixture=True,target=TARGET,rules=rules))
    for placement in ('host','controller'):
        c=dict(format='ndpsim-block-sum-run/1',placement=placement,target=TARGET,profile='profile.json',profile_sha256=file_hash(output/'profile.json'),package='package',package_sha256=report['package_sha256'],registry_sha256=reg.sha256,report='structural.json',report_sha256=file_hash(output/'structural.json'),semantic_report='semantic.json',semantic_sha256=file_hash(output/'semantic.json'),scratch=placement+'.sqlite',output=placement+'.jsonl',device=str(Path(device).resolve()))
        save(output/(placement+'.json'),c)
    # Remove the actual input. Simulation has no path or API through which to open it.
    payload.unlink()
    return output

def simulate(build,output):
    build=Path(build).resolve();output=Path(output).resolve()
    sst=decode((build/'sst-paths.json').read_bytes())['sst']
    for placement in ('host','controller'):
        command=[sst,'--add-lib-path='+str(build/'examples/extension'),str(HERE/'simulate.py'),'--',json.dumps(str(output/(placement+'.json')),ensure_ascii=False)]
        if os.environ.get('NDPSIM_EXTENSION_NATIVE'):command=[str(build/'examples/extension/block_sum_probe'),str(output/(placement+'.json'))]
        p=subprocess.run(command,cwd='/tmp',capture_output=True,text=True,timeout=60)
        save(output/(placement+'-command.json'),dict(command=command,cwd='/tmp',exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr))
        require(p.returncode==0,p.stdout+p.stderr)
    return compare(output)
def compare(output):
    rows={k:[decode(x) for x in (output/(k+'.jsonl')).read_bytes().splitlines()] for k in ('host','controller')}
    for placement,rs in rows.items():
        summary=rs[-1];require(summary['type']=='summary' and summary['success'],'simulation failed')
        require(not summary['full_acceptance_passed'],'teaching cannot claim total acceptance')
        require(all(summary[k]==0 for k in ('runtime_tasks','buffer_used','sessions')),'not drained')
        stages={r['op']:r for r in rs if r['type']=='stage'}
        require(set(stages)=={'left','right','join'} and all(r['success'] for r in stages.values()),'stage coverage')
        require(stages['join']['submitted']>=max(stages[n]['completed'] for n in ('left','right')),'join before both returns')
        require(stages['left']['submitted']==stages['right']['submitted'],'branches spuriously ordered')
        demand=[r for r in rs if r['type']=='demand'];require(len(demand)==3,'unique service')
        require({r['op']:r['duration_ns'] for r in demand}==dict(left=11,right=11,join=7),'independent linear cost')
        require(all(r['output_bytes']==8 for r in demand),'output size')
        require(sum(r['logical_bytes'] for r in rs if r['type']=='read_plan')==32,'logical bytes')
    require(rows['host'][-1]['ordinary_requests']==2 and rows['host'][-1]['input_d2h_bytes']==1024,'ordinary sector/DMA ledger')
    require(rows['controller'][-1]['local_requests']==2 and rows['controller'][-1]['ordinary_requests']==0 and rows['controller'][-1]['input_d2h_bytes']==0,'local input path')
    protocol=[r for r in rows['controller'] if r['type']=='protocol']
    require(sorted(r['kind'] for r in protocol)==[0,1,2,2,3,3,4,5],'protocol coverage')
    for r in protocol:
        require(r['payload']==({2:48,3:40}.get(r['kind'],32)) and r['logical']==(8 if r['kind']==3 else 0),'independent protocol sizes')
        require(r['wire']==r['payload']+28,'independent PCIe packet size')
    result=dict(format='ndpsim-block-sum-comparison/1',teaching_passed=True,full_acceptance_passed=False,configured_unvalidated=True,host=rows['host'][-1],controller=rows['controller'][-1])
    save(output/'comparison.json',result);return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--native',required=True);p.add_argument('--device',required=True);p.add_argument('--build',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();out=prepare(a.native,a.device,a.output);print(json.dumps(simulate(a.build,out),indent=2))
