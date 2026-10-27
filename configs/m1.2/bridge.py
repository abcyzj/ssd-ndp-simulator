import sst
sst.setProgramOption('timebase', '1ns')
sst.Component('bridge', 'ndpsim.EventBridge')
