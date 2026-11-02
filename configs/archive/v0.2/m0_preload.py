import os
import sst
sst.setProgramOption('timebase', '1ns')
component = sst.Component('preload', 'ndpsim.M0Preload')
component.addParams({'bytes': os.environ.get('NDPSIM_M0_PRELOAD_BYTES', '67108864'),
                     'blocks': os.environ.get('NDPSIM_M0_BLOCKS', '2048')})
