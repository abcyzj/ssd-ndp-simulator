"""Expand the explicit experiment axes, with reproducible point IDs."""

from copy import deepcopy
from itertools import chain, product

from ndpsim.static_ir import decode

from config import ROOT


def definition():
    return decode((ROOT / "scan.json").read_bytes())


def points():
    for batch, spec in definition().items():
        for block in spec["blocks"]:
            axes = []
            for axis in block.get("axes", []):
                (section, keys), = axis.items()
                if not keys or len({len(values) for values in keys.values()}) != 1:
                    raise ValueError(f"{batch}: invalid paired scan axis")
                axes.append([{section: dict(zip(keys, values))}
                             for values in zip(*keys.values())])
            if block.get("mode") == "product":
                variants = product(*axes)
            else:
                variants = ((value,) for value in chain.from_iterable(axes)) if axes else [()]
            for index, variant in enumerate(variants, 1):
                config = deepcopy(block.get("base", {}))
                for axis in variant:
                    for section, values in axis.items():
                        config.setdefault(section, {}).update(values)
                yield f"{batch}-{block['name']}-{index:02d}", config
