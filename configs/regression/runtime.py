import sst
sst.setProgramOption('timebase', '1ns')
import os
runtime = sst.Component('runtime', 'ndpsim.RuntimeValidation')
runtime.addParams({key: int(os.environ.get('NDPSIM_'+key.upper(), '0')) for key in ('read_delay', 'compute_delay', 'return_delay')})
