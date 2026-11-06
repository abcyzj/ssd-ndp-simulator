import json
import sys
import sst
sst.setProgramOption('timebase','1ns')
c=sst.Component('block_sum','ndpsim_block_sum.Host')
c.addParams({'config':sys.argv[1]})
