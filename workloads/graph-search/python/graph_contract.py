"""Graph-specific trusted metadata rules, separate from generic SDK validation."""
import json
from pathlib import Path
from ndpsim.static_ir import OperatorRegistry, canonical, file_hash, require

WORK_UNITS=dict(elements='count',flops='flop',read_bytes='B',write_bytes='B',output_bytes='B',candidate_checks='count',comparisons='count')
ZERO={k:dict(value=0,unit=u) for k,u in WORK_UNITS.items()}
ZERO_KINDS={'state','join'}

def registry(operators):
    specs=json.loads(Path(operators).read_text())['operators']
    rules=[];callbacks={}
    for op in specs:
        for spec in op['stages']:
            kind=spec['kind'];rid=spec['output_size_rule']
            def sizes(s,kind=kind):
                f={k:q['value'] for k,q in s['features'].items()};w={k:q['value'] for k,q in s['work'].items()}
                out={'out':0}
                if kind=='init':out.update(query=f['copied_bytes'],state=0)
                elif kind=='state':out['state']=0
                elif kind=='score_prepare':out['vectors']=4*f['vectors']*f['dimensions']
                elif kind=='score_l2':out['scores']=12*f['vectors']
                elif kind=='merge_append':out['batch']=12*(f['old_scores']+f['new_scores'])
                elif kind=='merge_finalize':out['candidates']=w['output_bytes']
                elif kind=='select':out['frontier']=w['output_bytes']
                elif kind=='adjacency_prepare':out['neighbors']=4*(1+f['edges'])
                elif kind=='join':out['neighbors']=sum(a['length_bytes'] for a in s['accesses'])
                elif kind=='dedup':out['fresh']=w['output_bytes']
                elif kind=='finish':out['topk']=12*f['objects']
                return out
            callbacks[rid]=sizes
            rules.append(dict(id=rid,kind='registered-metadata/1',implementation_sha256=file_hash(__file__)))
    return OperatorRegistry(dict(format='ndpsim-operator-registry/1',units=['B','count','flop'],operators=specs,rules=rules),callbacks)
