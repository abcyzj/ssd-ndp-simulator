"""Validated v2 simulation binding; functional files never provide a search trace."""
import json
import struct
from pathlib import Path
from benchmark_data import check
from benchmark_artifacts import check_index, check_truth

SEARCH = dict(strategy='ndpsim-ranked-expansion-v1', control_cost='ndpsim-candidates-v1',
              layout='independent-sector-v1', distance='ordered_fp64_squared_l2', ties='distance_then_local_id')


def load_v2(data, config, config_dir):
    data = Path(data)
    if config.get('format') != 'ndpsim-experiment-v2':
        raise ValueError('dataset-v2 requires ndpsim-experiment-v2')
    dirs = {key: (Path(config_dir)/config[key]).resolve() for key in ('index', 'truth')}
    m = check(data)
    x = check_index(data, dirs['index'])
    t = check_truth(data, dirs['truth'], config['search']['k'])
    if config['search']['entry'] != x['entry']:
        raise ValueError('search entry must match bound index entry')
    b, q = m['files']['base'], m['files']['query']
    paths = dict(vector_file=str(data/b['path']), query_file=str(data/q['path']),
                 graph_file=str(dirs['index']/x['graph']['path']))
    shape = dict(count=b['count'], dimensions=b['dimensions'], queries=q['count'], degree=x['max_degree'])
    identity = dict(dataset=m, index=x, truth=t, directories={k:str(v) for k,v in dirs.items()}, search=SEARCH)
    return shape, paths, identity


def truth_ids(identity):
    t = identity['truth']
    path = Path(identity['directories']['truth'])/t['ids']['path']
    with path.open('rb') as f:
        for _ in range(identity['dataset']['files']['query']['count']):
            yield struct.unpack('<'+'I'*t['k'], f.read(t['k']*4))


def decoded(paths, shape):
    def rows(path, d):
        return list(struct.iter_unpack('<'+'f'*d, Path(path).read_bytes()))
    return rows(paths['vector_file'], shape['dimensions']), rows(paths['query_file'], shape['dimensions'])


class FileRows:
    """Read only requested fixed-width rows; no full-base Python expansion."""
    def __init__(self, path, columns, adjacency=False):
        self.stream = Path(path).open('rb')
        self.row = struct.Struct('<' + ('I' if adjacency else 'f') * columns)
        self.adjacency = adjacency
        size = Path(path).stat().st_size
        if size % self.row.size:
            self.stream.close()
            raise ValueError('truncated row file')
        self.count = size // self.row.size

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def __len__(self):
        return self.count

    def __getitem__(self, node):
        if not 0 <= node < self.count:
            raise IndexError(node)
        self.stream.seek(node * self.row.size)
        row = self.row.unpack(self.stream.read(self.row.size))
        return row[1:1+row[0]] if self.adjacency else row
