import os
import sst
sst.setProgramOption('timebase','1ns')
component=sst.Component('ordinary','ndpsim.OrdinaryIO')
component.addParams({'miss':os.environ.get('NDPSIM_MISS','0'),'qd':os.environ.get('NDPSIM_QD','1')})
