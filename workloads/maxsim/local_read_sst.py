import json
from pathlib import Path
import sys

import sst

sst.setProgramOption("timebase", "1ns")
probe = sst.Component("local_pages", "ndpsim_maxsim.LocalReadProbe")
probe.addParams(json.loads(Path(sys.argv[1]).read_text()))
