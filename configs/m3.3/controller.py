import os
import sst
sst.setProgramOption("timebase", "1ns")
c = sst.Component("controller", "ndpsim.ControllerValidation")
c.addParams({"mode": os.environ.get("NDPSIM_CONTROLLER_MODE", "")})
