"""Explicit block-sum adapter for the installed ndpsim CLI."""
import argparse
from pathlib import Path
import struct

from ndpsim.execution import budget, load, resolve, save
from ndpsim.static_ir import OperatorRegistry, require, validate_package

from tutorial import prepare, registry, semantic


def config(path):
    value = load(path)
    require(set(value) == {'format', 'values', 'device', 'budget'}
            and value['format'] == 'ndpsim-block-sum-capture/1', 'block-sum capture config')
    values = value['values']
    require(type(values) is list and len(values) == 8
            and all(type(v) is int and 0 <= v < 2**32 for v in values), 'expected eight u32 values')
    return values, resolve(path.parent, value['device']), budget(value['budget'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'check', 'structure'))
    for name in ('config', 'operators', 'binary', 'output', 'package', 'reference'):
        parser.add_argument('--'+name, type=Path, required=name in ('operators', 'output'))
    parser.add_argument('--stride', type=int, default=1)
    parser.add_argument('--allow-fixture', action='store_true')
    args = parser.parse_args()
    reg = OperatorRegistry.load(args.operators)
    require(reg.sha256 == registry().sha256, 'block-sum registry mismatch')
    if args.action == 'capture':
        values, device, limits = config(args.config)
        out = prepare(args.binary, device, args.output, values)
        for placement in ('host', 'controller'):
            save(out/(placement+'-experiment.json'),
                 dict(format='ndpsim-experiment/1', run=placement+'.json', budget=limits))
        save(out/'status.json', dict(passed=True))
    else:
        if args.action == 'check':
            values, _, _ = config(args.config)
            result = semantic(args.package, struct.pack('<8I', *values), reg)
            require(result == load(args.reference), 'block-sum reference mismatch')
            name = 'semantic.json'
        else:
            result = validate_package(args.package, reg)
            name = 'structural.json'
        args.output.mkdir()
        save(args.output/name, result)


if __name__ == '__main__':
    main()
