"""P11 resident timing-only SST entry; unified CLI is delivered separately in P14.
Run: sst --add-lib-path=<build> configs/memory_timing.py -- <run.json>
"""
import sys
import sst
if len(sys.argv) != 2:
    raise ValueError('expected one ndpsim-memory-run/1 configuration path: '+repr(sys.argv))
node = sst.Component('host', 'ndpsim_memory.Host')
node.addParams({'config': sys.argv[1]})
