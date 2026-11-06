"""P07 metadata-only SST fixture; no dataset, payload, Layout or SSD device."""
import os
import sst

node = sst.Component('runtime', 'ndpsim_timing_validation.Runtime')
node.addParams({name + '_ns': int(os.environ.get('NDPSIM_TIMING_' + name.upper(), default))
                for name, default in [('input', '3'), ('service', '5'), ('return', '7')]})
