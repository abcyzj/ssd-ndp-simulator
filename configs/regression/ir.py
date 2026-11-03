"""V03-2 synthetic dependency/visibility probes, driven by SST time."""
import os
from pathlib import Path
import sst
sst.setProgramOption('timebase', '1ns')
probe = sst.Component('ir', 'ndpsim.IrValidation')
probe.addParams({'contract': str(Path(__file__).resolve().parents[2] / 'examples/ir/graph-contract.json'),
                 **{key: int(os.environ.get('NDPSIM_IR_' + key.upper(), '0'))
                    for key in ('input_delay', 'compute_delay', 'return_delay')}})
