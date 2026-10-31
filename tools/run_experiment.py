"""Run ANNS experiments using an existing build; no build, tests or acceptance checks."""
import argparse
import json
from pathlib import Path

from ann_data import write_json
from run_graph import ROOT, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=ROOT/'build/delivery',
                        help='Existing build containing sst-paths.json and libndpsim.so')
    parser.add_argument('--output', type=Path, default=ROOT/'results/experiment',
                        help='New experiment output directory')
    parser.add_argument('--config', type=Path, default=ROOT/'configs/experiments/host.json')
    parser.add_argument('--mode', choices=('pair', 'host', 'controller'), default='pair',
                        help='Placement override; pair runs both with the same data, without comparison')
    parser.add_argument('--data', type=Path,
                        help='Existing dataset including truth.json; omitted generates configured data')
    parser.add_argument('--event-log', action='store_true',
                        help='Export JSONL events (also enabled by event_log in the config)')
    args = parser.parse_args()
    build, output = args.build.resolve(), args.output.resolve()
    for name in ('sst-paths.json', 'libndpsim.so'):
        if not (build/name).is_file():
            parser.error(f'missing {build/name}; supply an existing build with --build')
    if output.exists():
        parser.error('choose a new --output directory; experiment artifacts are never overwritten')
    config = json.loads(args.config.read_text())
    for key in ('index', 'truth'):
        if key in config: config[key] = str((args.config.resolve().parent/config[key]).resolve())
    if args.event_log:
        config['event_log'] = True
    data = args.data.resolve() if args.data else None
    output.mkdir(parents=True)
    for mode in (('host', 'controller') if args.mode == 'pair' else (args.mode,)):
        config['placement'] = mode
        effective = output/(mode+'-config.json')
        write_json(effective, config)
        run(effective, build, output/mode, data, paired=True)
        if data is None:
            data = output/mode/'data'
    print('Experiment completed:', output, flush=True)


if __name__ == '__main__':
    main()
