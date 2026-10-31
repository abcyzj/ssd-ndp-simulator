"""T1 bounded-memory benchmark conversion and dataset-v2 validation.

A spec declares metric, preprocessing and source metadata, plus base/query and
optional learn/groundtruth files. Groundtruth conversion is ID data only; T2
must bind it to a validated truth artifact before simulation.
"""
import argparse
import json
import math
import random
import shutil
import struct
import tempfile
from pathlib import Path
from ann_data import integer, write_json
from verify_m0 import inspect, sha

VERSION = 'ndpsim-benchmark-converter-v1'


def selection(item, count):
    rule = item.get('selection', {'kind': 'all'})
    if rule == {'kind': 'all'}:
        return range(count)
    if rule.get('kind') == 'ids' and set(rule) == {'kind', 'ids'}:
        ids = rule['ids']
    elif rule.get('kind') == 'random' and set(rule) == {'kind', 'seed', 'count'}:
        integer(rule['seed'], 'seed', 0)
        integer(rule['count'], 'selection count', 1, count)
        ids = random.Random(rule['seed']).sample(range(count), rule['count'])
    else:
        raise ValueError('invalid selection rule')
    if not isinstance(ids, list) or not ids:
        raise ValueError('empty/invalid selection')
    for value in ids:
        integer(value, 'row ID', 0, count-1)
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate row ID')
    return ids


def convert(spec_path, output):
    spec_path, output = Path(spec_path), Path(output)
    spec = json.loads(spec_path.read_text())
    if spec['metric'] != 'squared_l2' or not isinstance(spec['preprocessing'], str) or not spec['preprocessing']:
        raise ValueError('metric/preprocessing must be explicit')
    files = spec['files']
    if not {'base', 'query'} <= files.keys() or files.keys() - {'base', 'query', 'learn', 'groundtruth'}:
        raise ValueError('invalid dataset roles')
    if output.exists():
        raise ValueError('output already exists; use check for reuse')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.'+output.name+'-', dir=output.parent))
    try:
        meta = dict(format='ndpsim-dataset-v2', metric=spec['metric'], preprocessing=spec['preprocessing'],
                    converter=VERSION, converter_sha256=sha(__file__), validator_sha256=sha(Path(__file__).with_name('verify_m0.py')),
                    spec_sha256=sha(spec_path), files={})
        for role, item in files.items():
            n = integer(item['count'], 'count')
            d = integer(item['dimensions'], 'dimensions', 1, 2**31-1)
            fmt = item['format']
            if fmt not in (('ivecs', 'ibin') if role == 'groundtruth' else ('fvecs', 'fbin')):
                raise ValueError('format/type does not match role')
            if not item.get('source'):
                raise ValueError('source metadata required')
            path = (spec_path.parent / item['path']).resolve()
            raw = inspect(path, fmt, n, d, files['base']['count'] if role == 'groundtruth' else None)
            if raw['sha256'] != item['sha256']:
                raise ValueError('raw content lock mismatch')
            ids = selection(item, n)
            name, mapping = role+'.'+('i32' if role == 'groundtruth' else 'f32'), role+'.ids.u32'
            width, header = (d + (fmt.endswith('vecs'))) * 4, 8 if fmt.endswith('bin') else 0
            with path.open('rb') as src, (staging/name).open('wb') as dst, (staging/mapping).open('wb') as mp:
                for row in ids:
                    src.seek(header + row*width + (4 if fmt.endswith('vecs') else 0))
                    remaining = d*4
                    while remaining:
                        block = src.read(min(remaining, 1024*1024))
                        if not block:
                            raise ValueError('source truncated during conversion')
                        dst.write(block)
                        remaining -= len(block)
                    mp.write(struct.pack('<I', row))
            if sha(path) != raw['sha256']:
                raise ValueError('source changed during conversion')
            meta['files'][role] = dict(path=name, mapping=mapping, count=len(ids), dimensions=d,
                dtype=raw['dtype'], byte_order='little', sha256=sha(staging/name), bytes=(staging/name).stat().st_size,
                mapping_sha256=sha(staging/mapping), original_count=n, selection=item.get('selection', {'kind':'all'}),
                source=item['source'], raw=raw)
        write_json(staging/'dataset.json', meta)
        check(staging)
        staging.rename(output)
        return meta
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def check(directory):
    directory = Path(directory)
    meta = json.loads((directory/'dataset.json').read_text())
    if meta['format'] != 'ndpsim-dataset-v2' or meta['metric'] != 'squared_l2' or not meta['preprocessing']:
        raise ValueError('unsupported dataset schema/metric/preprocessing')
    files = meta['files']
    if not {'base', 'query'} <= files.keys() or files.keys()-{'base','query','learn','groundtruth'}:
        raise ValueError('invalid dataset roles')
    for role, item in files.items():
        n = integer(item['count'], 'count')
        d = integer(item['dimensions'], 'dimensions', 1, 2**31-1)
        original = integer(item['original_count'], 'original count')
        if item['byte_order'] != 'little' or item['dtype'] != ('int32' if role=='groundtruth' else 'float32'):
            raise ValueError('unsupported dtype/endian')
        for key, size, digest_key in [('path',n*d*4,'sha256'), ('mapping',n*4,'mapping_sha256')]:
            name = item[key]
            if Path(name).name != name:
                raise ValueError('artifact path must be a filename')
            path = directory/name
            if path.stat().st_size != size or sha(path) != item[digest_key]:
                raise ValueError('artifact size/hash mismatch')
        if item['bytes'] != n*d*4:
            raise ValueError('declared byte count mismatch')
        expected = iter(selection(item, original))
        with (directory/item['mapping']).open('rb') as stream:
            for _ in range(n):
                if struct.unpack('<I', stream.read(4))[0] != next(expected, None):
                    raise ValueError('mapping/order mismatch')
            if next(expected, None) is not None:
                raise ValueError('selection count mismatch')
        with (directory/item['path']).open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                for (v,) in struct.iter_unpack('<i' if role=='groundtruth' else '<f', block):
                    if (v < 0 or v >= files['base']['original_count']) if role=='groundtruth' else not math.isfinite(v):
                        raise ValueError('invalid value/ID')
        if role in ('query','learn') and d != files['base']['dimensions']:
            raise ValueError('dimension mismatch')
    if 'groundtruth' in files:
        b, q, g = files['base'], files['query'], files['groundtruth']
        if b['selection'] != {'kind':'all'}:
            raise ValueError('official truth cannot be filtered for base subset; recompute in T2')
        if g['original_count'] != q['original_count'] or g['mapping_sha256'] != q['mapping_sha256']:
            raise ValueError('groundtruth query mapping mismatch')
    return meta


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['import', 'check'])
    p.add_argument('path', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.action == 'import' and a.output is None:
        p.error('--output required')
    print(json.dumps(convert(a.path, a.output) if a.action=='import' else check(a.path)))


if __name__ == '__main__':
    main()
