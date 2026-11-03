"""Build the trusted in-process graph contract from validated run inputs.

The contract contains no future operations or trace. The C++ frontend allocates
operations only from visible results. File content is opened only by GraphHost.
"""
import hashlib
import json
import subprocess
from pathlib import Path
from ann_data import digest

ROOT = Path(__file__).resolve().parents[1]

def contract(c, m, params, data, identity):
    def hashed(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    def ident(name, sha):
        return dict(name=name, version='1', sha256=sha)
    source = {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT/'src').rglob('*')) if p.suffix in ('.h', '.cc')}
    source_id = ident('working-source', hashed(source))
    def dependency(path):
        commit = subprocess.check_output(['git', '-C', str(ROOT/path), 'rev-parse', 'HEAD'], text=True).strip()
        diff = subprocess.check_output(['git', '-C', str(ROOT/path), 'diff', '--binary', 'HEAD'])
        return ident('git-'+commit, hashlib.sha256(commit.encode()+diff).hexdigest())
    files = dict(base=params.get('vector_file', str(data/'vectors.f32')),
                 graph=params.get('graph_file', str(data/'adjacency.u32')),
                 queries=params.get('query_file', str(data/'queries.f32')),
                 truth=str(Path(identity['directories']['truth'])/'truth.json' if identity else data/'truth.json'))
    content = {k: ident(k, digest(Path(v))) for k, v in files.items()}
    catalog = json.loads((ROOT/'schemas/ndpsim-ir-v1.capabilities.json').read_text())
    catalog_hash = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    operators = [dict(operator=o['operator'], version=o['operator_version']) for o in catalog['operators']]
    search = dict(strategy='ndpsim-ranked-expansion-v1', entry=c['search']['entry'], k=c['search']['k'],
                  L=c['search']['width'], B=c['search']['batch'], E=c['search']['budget'], chunk=c['search']['chunk'])
    manifest = dict(ir_version='ndpsim-ir/1', execution_engine='ir', execution_mode='dynamic',
        workload_type='graph_ann', frontend=ident('graph-search', source['src/search/ir_graph.h']),
        search=search, operator_set=dict(catalog_sha256=catalog_hash, operators=operators),
        mapping=dict(placement=c['placement'], **{key: ident(key, hashed(value)) for key, value in
            dict(policy='existing_services_v1', layout=[m['count'], m['dimensions'], m['degree']],
                 profile=c['device_profile'], initial_state='preload-drained-CMT-retained',
                 cost=dict(host=c['host'], controller=c.get('controller', {}))).items()}),
        execution_scope=dict(run='run-'+hashed([c, source, content]), query_identity='original-id-and-run-ordinal',
                             batch_rule='query-iteration-semantic-batch-v1'), source=source_id,
        dependencies=dict(mqsim=dependency(Path('third_party/MQSim')), sst=dependency(Path('third_party/sst-core')),
                          toolchain=ident('build-receipt', digest(Path(params['_build'])/'sst-paths.json')), working_tree=source_id),
        diagnostics=dict(mode='detail' if c.get('event_log', False) else 'statistics',
                         schema='ndpsim-ir-diagnostics/1', budget_bytes=c.get('output_budget_bytes', 2*1024**3)))
    mapping_hash = hashed(identity if identity else m)
    for key, obj, shape in [('dataset', 'base', [m['count'], m['dimensions']]),
                            ('index', 'graph', [m['count'], m['degree']]),
                            ('queries', 'queries', [m['queries'], m['dimensions']]),
                            ('truth', 'truth', [m['queries'], c['search']['k']])]:
        manifest[key] = dict(identity=content[obj], shape=shape,
                             id_mapping_sha256=mapping_hash, order_sha256=mapping_hash)
    dataset_id = ident('dataset-base-and-queries', hashed([content['base'], content['queries']]))
    manifest['dataset']['identity'] = dataset_id
    objects = []
    for name, kind, count, width, dtype, record in [
        ('base', 'base_vectors', m['count'], m['dimensions'], 'fp32', 'fp32-vector-v1'),
        ('queries', 'query_vectors', m['queries'], m['dimensions'], 'fp32', 'fp32-vector-v1'),
        ('graph', 'adjacency', m['count'], m['degree'], 'u32', 'degree-ids-u32-v1')]:
        objects.append(dict(object_id=name, object_type=kind, content_identity=content[name] if name == 'graph' else dataset_id, content_version=1,
            element_count=count, element_type=dtype, shape=[width], byte_order='little', record_schema=record,
            logical_layout='element-field-v1', mutability='readonly', content_ref=name+'-trusted-provider'))
    return dict(manifest=manifest, objects=objects, operations=[])
