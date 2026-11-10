"""Prepare Host and Controller experiment specs for the small graph teaching fixture."""
import argparse
from pathlib import Path

from ndpsim.cost_models import schema_hash
from ndpsim.execution import load, save
from ndpsim.layout import packed_layout
from ndpsim.static_ir import file_hash, require


def prepare(capture, device, output):
    capture = capture.resolve()
    output = output.resolve()
    package = capture/'package'
    manifest = load(package/'manifest.json')
    provenance = manifest['provenance']
    require(provenance['kind'] == 'synthetic-fixture' and len(provenance['query_ids']) == 11,
            'expected the small synthetic graph teaching fixture')
    receipt = load(capture/'structural.json')
    registry = load(capture/'registry.json')
    target = dict(id='graph-teaching', hardware='hypothetical', os='none',
                  frequency='fixed', affinity='one', memory_bytes=8192)
    output.mkdir(parents=True, exist_ok=False)
    save(output/'layout.json', packed_layout(manifest['objects'], capacity_sectors=512))
    limits = dict(output_bytes=64*1024**2, scratch_bytes=32*1024**2,
                  rss_bytes=512*1024**2, timeout_seconds=60, min_disk_free_bytes=1024**2)
    for placement in ('host', 'controller'):
        context = 'ssd_host_return/1' if placement == 'host' else 'ssd_controller_command/1'
        rules = []
        for operator in registry['operators']:
            for stage in operator['stages']:
                kind = stage['kind']
                location = 'controller' if placement == 'controller' and kind == 'score_l2' else 'host'
                rules.append(dict(operator=operator['id'], operator_version=operator['version'],
                                  stage_kind=kind, context=context, location=location,
                                  target_id=target['id'], work_units=stage['work_units'],
                                  feature_ranges=[dict(name=k, unit=v, minimum=0, maximum=100000)
                                                  for k, v in stage['feature_units'].items()],
                                  source='prediction', formula='constant/1',
                                  parameter_schema_sha256=schema_hash('constant/1'),
                                  parameters=dict(ns=0 if kind == 'join' else 2),
                                  measurement_sha256=['1'*64], training_query_ids=[1],
                                  fit_report_sha256='2'*64))
        profile = output/(placement+'-profile.json')
        save(profile, dict(format='ndpsim-cost-profile/1', fixture=True, target=target, rules=rules))
        run = dict(format='ndpsim-graph-ssd-run/1', placement=placement, target=target,
                   context=context, workers=1, slots=1, admission=1, allow_fixture=True,
                   package=str(package), package_sha256=receipt['package_sha256'],
                   profile=profile.name, profile_sha256=file_hash(profile),
                   layout='layout.json', device=str(device.resolve()),
                   scratch=placement+'.sqlite', output=placement+'.jsonl',
                   resources=dict(capacity_bytes=4096, query_bound=16, block_bound=2160,
                                  staging_bytes_per_ns=16, compute_slots=1))
        for key, name, hash_key in (('registry', 'registry.json', 'registry_sha256'),
                                    ('report', 'structural.json', 'report_sha256'),
                                    ('semantic_report', 'semantic.json', 'semantic_sha256')):
            path = capture/name
            run[key] = str(path)
            run[hash_key] = file_hash(path)
        run_path = placement+'.json'
        save(output/run_path, run)
        save(output/(placement+'-experiment.json'),
             dict(format='ndpsim-experiment/1', run=run_path, budget=limits))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'device', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    prepare(args.capture, args.device, args.output)
