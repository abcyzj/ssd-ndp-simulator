import json
from pathlib import Path
import sys

import sst

sst.setProgramOption("timebase", "1ns")
device = sst.Component("nvme", "ndpsim_maxsim.NvmeProbe")
device.addParams(json.loads(Path(sys.argv[1]).read_text()))
