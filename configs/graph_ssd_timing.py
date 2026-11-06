"""P13 graph SSD entry. sst --add-lib-path=<build> this.py -- <run.json>"""
import sys
import sst
if len(sys.argv) != 2:
    raise ValueError('expected one graph SSD configuration path')
node = sst.Component('host', 'ndpsim_graph_ssd.Host')
node.addParams({'config': sys.argv[1]})
