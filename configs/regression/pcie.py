import sst
sst.setProgramOption("timebase", "1ns")
sst.Component("pcie", "ndpsim.PcieValidation")
