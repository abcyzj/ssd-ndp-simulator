"""Run one placement of the paired SST/MQSim graph experiment."""
import argparse
import json
from ann_data import write_json
from compare_placements import compare
from pathlib import Path
from run_graph import run, ROOT

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'configs/archive/v0.1/m3.4/host.json')
    p.add_argument('--build', type=Path, default=ROOT/'build/m3.4')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--data', type=Path)
    p.add_argument('--pair', action='store_true', help='Run both placements against exactly the same dataset and compare')
    a = p.parse_args()
    if a.pair:
        a.output.mkdir(parents=True, exist_ok=True)
        config=json.loads(a.config.read_text())
        shared=a.data
        for placement in ('host', 'controller'):
            config['placement']=placement
            effective=a.output/(placement+'-config.json')
            write_json(effective, config)
            run(effective, a.build, a.output/placement, shared, paired=True)
            if shared is None:shared=a.output/'host/data'
        report=compare(a.output/'host', a.output/'controller')
        write_json(a.output/'comparison.json', report)
        print('M3.4 paired PASS', json.dumps(report))
    else:
        run(a.config, a.build, a.output, a.data, paired=True)
