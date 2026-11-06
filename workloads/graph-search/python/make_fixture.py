"""Create reproducible synthetic graph inputs. Never a SIFT1M acceptance input."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import random
import re
import struct
import subprocess
from graph_capture import save

def make(output,pilot=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    n,d,q,r,k=(1024,128,32,16,10) if pilot else (12,4,11,6,4)
    rng=random.Random(4504)
    vectors=[[rng.randint(-16,16)/4 for _ in range(d)] for _ in range(n)]
    queries=[[rng.randint(-8,8)/4 for _ in range(d)] for _ in range(q)]
    graph=[[i,(i+1)%n,*[rng.randrange(n) for _ in range(r-2)]] for i in range(n)]
    truth=[sorted(range(n),key=lambda i:(sum((a-b)**2 for a,b in zip(query,vectors[i])),i))[:k] for query in queries]
    files={}
    for name,code,rows in (('vectors','f',vectors),('queries','f',queries),('graph','I',[[len(v),*v] for v in graph]),('truth','I',truth),('query_ids','I',[[i*2+7 for i in range(q)]])):
        data=b''.join(struct.pack('<'+code*len(row),*row) for row in rows);p=output/name;p.write_bytes(data);files[name]=dict(path=name,sha256=hashlib.sha256(data).hexdigest())
    if platform.system()=='Darwin':
        text=subprocess.check_output(['memory_pressure','-Q'],text=True);ram=int(re.search(r'The system has (\d+)',text)[1])
    else:
        import os
        ram=os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    cfg=dict(format='ndpsim-graph-capture-config/2',shape=dict(count=n,dimensions=d,queries=q,degree=r,truth_k=k),search=dict(entry=0,k=k,width=128 if pilot else 8,batch=1 if pilot else 2,budget=256 if pilot else 6,chunk=16 if pilot else 2),files=files,split='fixture',target=dict(id=platform.node() or 'local-host',hardware=platform.machine(),os=platform.platform(),memory_bytes=ram,frequency='Unsupported',affinity='uncontrolled'),budget=dict(native_rss_bytes=min(4*1024**3,ram*3//5),postprocess_rss_bytes=512*1024**2,output_bytes=256*1024**2,query_bytes=16*1024**2,timeout_seconds=600,min_disk_free_bytes=20*1024**3))
    save(output/'config.json',cfg);save(output/'fixture.json',dict(synthetic=True,seed=4504,pilot=pilot,formal_acceptance=False));return output/'config.json'
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--pilot',action='store_true');a=p.parse_args();print(make(a.output,a.pilot))
