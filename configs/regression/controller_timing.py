import os
import sst
sst.setProgramOption('timebase','1ns')
c=sst.Component('controller','ndpsim_controller_validation.Controller')
c.addParam('mode',os.environ.get('NDPSIM_CONTROLLER_MODE',''))
