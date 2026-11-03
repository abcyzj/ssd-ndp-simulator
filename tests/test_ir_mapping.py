"""Real MQSim four-combination operator probe; no production IR graph frontend."""
import argparse
import json
from pathlib import Path
import subprocess
p=argparse.ArgumentParser();p.add_argument('--probe',required=True);p.add_argument('--output',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1]
reports={}
if a.output and a.output.exists():
    p.error("output must not exist")
def run_probe(engine, placement, mode=''):
    cmd=[a.probe,str(root/'examples/ir/graph-contract.json'),engine,placement]+([mode] if mode else [])
    run=subprocess.run(cmd,capture_output=True,text=True)
    if a.output:
        a.output.mkdir(parents=True,exist_ok=True)
        (a.output/f'{engine}-{placement}-{mode or "base"}.log').write_text(run.stdout+run.stderr)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads(next(s.removeprefix('MAPPING_REPORT ') for s in run.stdout.splitlines() if s.startswith('MAPPING_REPORT ')))
    if a.output:
        (a.output/f'{engine}-{placement}-{mode or "base"}.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
for placement in ('host','controller'):
    for engine in ('direct','ir'):
        reports[engine,placement]=run_probe(engine,placement)
    direct,ir=(reports[e,placement] for e in ('direct','ir'))
    for key in direct:
        assert direct[key]==ir[key],f'{placement} {key}:\n{direct[key]}\n{ir[key]}'
    assert direct['decisions'][0]['ids']==[0]
    assert direct['scores'][0]=={'id':3,'distance':0.0}
    assert len(direct['scores'])==5
    if placement=='controller':
        assert all(r['kind']==2 for r in direct['reads']), 'controller ordinary vector read'
        assert len(direct['storage'])==3, 'missing multiple capacity chunks'
        assert direct['dma']==512*len(direct['reads']), 'vector bytes reached ordinary host DMA'
        assert sum(r[0]==1 for r in direct['messages'])==1, 'query uploaded more than once'
        assert sum(r[0]==2 for r in direct['messages'])==1, 'missing OpenAck'
        assert sum(r[0]==5 for r in direct['messages'])==1, 'missing Close'
        assert sum(r[0]==6 for r in direct['messages'])==1, 'missing CloseAck' 
print('V03-3 PASS four combinations: results, batches, timing, Work, services, reads, messages, credits, resource ledger; live=0')
if a.output:
    (a.output/'comparison.json').write_text(json.dumps({p:reports['ir',p] for p in ('host','controller')},indent=2)+'\n')

for placement in ('host','controller'):
    for mode in ('earlyclose','duplicate') + (('failure','oversize','openfailure') if placement=='controller' else ('partial',)):
        report=run_probe('ir',placement,mode)
        if mode in ('duplicate','oversize'):
            assert not report['reads'] and not report['host']
            assert not report.get('storage',[]), 'invalid batch reached device'
        if mode=='partial':
            assert len(report['reads'])==2, 'partial adapter failure did not exercise live siblings'
    direct=run_probe('direct',placement,'tight')
    ir=run_probe('ir',placement,'tight')
    assert direct==ir, 'single-block capacity mapping differs'
print('V03-3 PASS early close, duplicate/oversize atomic rejection, partial submission and controller failure drain, single-block capacity')

for placement in ('host','controller'):
    direct=run_probe('direct',placement,'rival')
    ir=run_probe('ir',placement,'rival')
    assert direct==ir, 'same-time resource arbitration differs'
print('V03-3 PASS same-time independent resource contender')
