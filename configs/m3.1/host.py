"""Internal SST entry; tools/run_m3_1.py validates and snapshots its parameters."""
import json
import os
import sst
sst.setProgramOption('timebase', '1ns')
component = sst.Component('graph', 'ndpsim.GraphHost')
with open(os.environ['NDPSIM_M31_PARAMS']) as stream:
    component.addParams(json.load(stream))
