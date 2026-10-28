"""Run bounded SST/MQSim graph queries, with independent JSON/CSV statistics."""
import argparse
from pathlib import Path
from run_m3_1 import run, ROOT

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'configs/m4.1/host.json')
    p.add_argument('--build', type=Path, default=ROOT/'build/m4.1')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--data', type=Path)
    a = p.parse_args()
    run(a.config, a.build, a.output, a.data, paired=True)
