import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from device_profiles import profile, preflight, BASE

def reject(f):
    try: f()
    except ValueError: return
    raise AssertionError('expected rejection')

assert profile('native-small')['xml'] == BASE.read_text()
assert profile('ann-2g-v1')['raw_bytes'] == 2*1024**3
assert profile('ann-16g-v1')['raw_bytes'] == 16*1024**3
for name,n,d in [('ann-2g-v1',1000000,128),('ann-16g-v1',10000000,96)]:
    p = preflight(name,n,d,64)
    assert p['end_lba']*512 == n*1024
reject(lambda:profile('typo'))
reject(lambda:preflight('ann-2g-v1',10000000,128,64))
reject(lambda:preflight('ann-16g-v1',100,128,64,resource_budget_bytes=1))
reject(lambda:preflight('ann-16g-v1',2**32-1,2**32-1,64))
print('PASS profile XML/capacity/overflow/resource preflight')

# Legacy v1 simulation validation no longer expands the base to Python float rows.
import tempfile
import json
import struct
from ann_data import generate, validate_dataset, digest
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)
    m=generate(path,n=32,d=96,degree=4,queries=4)
    assert validate_dataset(path)==m
    with (path/'vectors.f32').open('r+b') as f:f.write(struct.pack('<f',float('nan')))
    m['sha256']['vectors.f32']=digest(path/'vectors.f32')
    (path/'dataset.json').write_text(json.dumps(m))
    reject(lambda:validate_dataset(path))
print('PASS bounded v1 validation rejects nonfinite content with otherwise valid hash')
