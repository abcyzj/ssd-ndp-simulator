"""Native raw observation boundary. No simulation tools or package sealing."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from test_native_search import fixture, check

binary = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    files, expected, truth, original_ids = fixture(root)
    cmd = [binary, *map(str, (6,2,11,4,4,0,4,4,2,3,1,1,1)), *files]
    inventories = {}
    for mode in ('uninstrumented', 'benchmark', 'cpu_sample', 'static_capture'):
        out = root/mode
        out.mkdir()
        extra = [] if mode == 'benchmark' else [mode]
        if mode in ('cpu_sample', 'static_capture'):
            extra += [str(out), '4000000', '1000000', '1']
        run = subprocess.run(cmd+extra, capture_output=True, text=True, check=True, timeout=20)
        check(json.loads(run.stdout), expected, truth, original_ids, 1, 1)
        if mode in ('cpu_sample', 'static_capture'):
            assert sorted(int(p.name) for p in out.iterdir()) == list(range(11))
            rows = [[json.loads(line) for line in (out/str(i)).read_text().splitlines()] for i in range(11)]
            assert all(rows)
            inventories[mode] = rows
    assert all(len(q) > 1 for q in inventories['static_capture'])
    assert all(row[5] >= row[4] > 0 for q in inventories['cpu_sample'] for row in q)
    for total, buffer in ((1,100000), (100000,1)):
        out=root/f'failed-{total}-{buffer}'
        out.mkdir()
        run=subprocess.run(cmd+['static_capture',str(out),str(total),str(buffer),'1'], capture_output=True,text=True,timeout=20)
        assert run.returncode == 1 and not run.stdout and 'budget exceeded' in run.stderr
print('PASS: four separate modes, all-query top-k/Work oracle, raw shards, CPU timestamps, budget failures')
