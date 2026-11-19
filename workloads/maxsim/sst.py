import sys

import sst

sst.setProgramOption("timebase", "1ns")
host = sst.Component("maxsim", "ndpsim_maxsim.Host")
host.addParam("config", sys.argv[1])
