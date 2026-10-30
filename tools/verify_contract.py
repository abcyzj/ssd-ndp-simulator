#!/usr/bin/env python3
"""Recompute the frozen manual ledger; this does not simulate ANNS or PCIe."""
import argparse
import copy
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(fixture, abi):
    require(fixture['contract'] == 'ndpsim-minimal/1', 'contract version')
    require(fixture['abi'] == abi, 'backend ABI differs from frozen ledger')
    params = fixture['input']
    d, b = params['dimension'], len(params['neighbor_ids'])
    require(d == 32 and b == 2 and params['degree'] == 2, 'manual input changed')
    require(params['vector_lbas'] == [7, 8] and params['adjacency_lba'] == 32,
            'manual extents changed')
    require(params['sector_bytes'] == 512 and params['page_sectors'] == 8,
            'layout units changed')
    require(len(set(fixture['service_owners'])) == 13, 'resource coverage')
    require(all(isinstance(v, str) and v for v in fixture['service_owners'].values()),
            'each service needs exactly one owner')
    require(fixture['service_owners']['pcie'] == 'MQSim.PCIe_Link', 'PCIe owner')
    require(fixture['service_owners']['onfi_data_out'] == 'MQSim', 'ONFI owner')
    require(fixture['service_owners']['controller_arithmetic'] == 'ComputeModel', 'compute owner')
    pcie = fixture['pcie']
    require(pcie == dict(max_payload=128, packet_overhead=28, lanes=4, lane_GBps=1),
            'manual link configuration changed')
    # Encode independently of the fixture lengths: no native struct padding.
    header = struct.pack('<4sHHIIQQQII', b'NDPS', 1, 1, 64+4*d, 0, 1, 1, 0, d, 0)
    require(len(header) == 48, 'header width')
    bodies = {
        'QueryOpen': struct.pack('<IHHQ', d, 1, 1, 1) + struct.pack('<'+'f'*d, *([0.0]*d)),
        'QueryOpenAck': b'',
        'Score': struct.pack('<QII', 1, d, 0) + struct.pack('<'+'I'*b, *params['neighbor_ids']),
        'ScoreResult': struct.pack('<Q', 1) + b''.join(struct.pack('<Id', i, float(i)) for i in params['neighbor_ids']),
        'QueryClose': b'', 'QueryCloseAck': b'', 'Error': b'',
    }
    results = {}
    for mode in ('host', 'controller'):
        entry = fixture[mode]
        totals = {direction: dict(payload=0, wire=0) for direction in ('H2D', 'D2H')}
        events = set()
        expected_ids = set()
        reads = ('adj', 'vector') if mode == 'host' else ('adj',)
        for read in reads:
            for name in ('sq_doorbell', 'sq_fetch', 'sqe', 'data', 'cqe', 'cq_doorbell'):
                expected_ids.add(read+'.'+name)
        if mode == 'controller':
            expected_ids.update(('open', 'open_ack', 'score', 'result', 'close', 'close_ack'))
        require({m['id'] for m in entry['messages']} == expected_ids, mode+' missing/extra messages')
        for message in entry['messages']:
            event = message['resource_event']
            require(event not in events, 'duplicate billing event: '+event)
            events.add(event)
            require(message['id'] == event, 'one message per billing event')
            require(message['owner'] == 'MQSim.PCIe_Link', 'non-authoritative link charge')
            kind, direction, size = message['kind'], message['direction'], message['payload_bytes']
            require(type(size) is int and size > 0, 'invalid payload')
            if kind in bodies:
                require(mode == 'controller', 'host unexpectedly uses platform envelope')
                require(size == len(header)+len(bodies[kind]), kind+' serialized size')
                require(direction == ('H2D' if kind in ('QueryOpen', 'Score', 'QueryClose') else 'D2H'), 'message direction')
            else:
                read, name = message['id'].split('.')
                ordinary = {
                    'sq_doorbell': ('H2D', 'WRITE_REQ', abi['doorbell_bytes']),
                    'sq_fetch': ('D2H', 'READ_REQ', 4),
                    'sqe': ('H2D', 'READ_COMP', abi['sqe_bytes']),
                    'data': ('D2H', 'WRITE_REQ', 512 if read == 'adj' else 1024),
                    'cqe': ('D2H', 'WRITE_REQ', abi['cqe_bytes']),
                    'cq_doorbell': ('H2D', 'WRITE_REQ', abi['doorbell_bytes']),
                }
                require((direction, kind, size) == ordinary[name], 'ordinary message mismatch')
            wire = (pcie['packet_overhead']+4 if kind == 'READ_REQ' else
                    size + ((size+pcie['max_payload']-1)//pcie['max_payload'])*pcie['packet_overhead'])
            totals[direction]['payload'] += size
            totals[direction]['wire'] += wire
        require(totals == entry['expected'], mode+' byte conservation')
        require(entry['storage_sector_bytes'] == 3*512, mode+' storage coverage')
        require(entry['vector_D2H_bytes'] == (1024 if mode == 'host' else 0), 'vector route')
        results[mode] = totals
    controller = fixture['controller']
    expected_access = dict(query_write=4*d, input_staging_write=1024,
                           compute_input_query_read=1024+4*d, result_write=12*b, result_read=12*b)
    require(controller['buffer_access_bytes'] == expected_access, 'buffer service coverage')
    require(sum(expected_access.values()) == controller['expected_buffer_access_bytes'], 'buffer bytes')
    expected_credit = dict(query=4*d, input=1024, result=12*b, command_scratch=64+4*b)
    require(controller['credit_bytes'] == expected_credit, 'credit coverage')
    require(sum(expected_credit.values()) == controller['expected_peak_credit_bytes'], 'credit peak')
    require(len(fixture['manual_review']) == 7 and all(r['result'] == 'pass' and r['basis'] for r in fixture['manual_review']),
            'manual review incomplete')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ctest-build', type=Path, help='Optional existing build regression; no new platform implementation')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(scope='M0.4 static contract + manual round audit; not AC or performance simulation',
                  status='running', commands=[], assertions=[],
                  invocation=[sys.executable]+sys.argv,
                  time_utc=datetime.now(timezone.utc).isoformat(),
                  environment=dict(python=sys.version, platform=platform.platform()))

    def run(label, argv):
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, capture_output=True)
        (output/(label+'.stdout.log')).write_bytes(result.stdout)
        (output/(label+'.stderr.log')).write_bytes(result.stderr)
        report['commands'].append(dict(label=label, argv=list(map(str, argv)), cwd=str(ROOT), exit_code=result.returncode))
        require(result.returncode == 0, label+' failed; see logs')
        return result.stdout

    def check(label, condition=True):
        require(condition, label)
        report['assertions'].append(label)

    try:
        run('python-syntax', [sys.executable, '-c',
                             "from pathlib import Path; p=Path('tools/verify_contract.py'); compile(p.read_text(), str(p), 'exec')"])
        check('Python source compiles without writing system cache')
        run('git-diff-check', ['git', 'diff', '--check'])
        check('tracked changes pass git diff --check')
        fixture_path = ROOT/'tests/fixtures/m0.4-round.json'
        fixture = json.loads(fixture_path.read_text())
        lock = json.loads((ROOT/'deps.lock.json').read_text())
        compiler = lock['tools']['cxx']['path']
        run('abi-build', [compiler, '-std=c++17', '-Wall', '-Wextra', '-pedantic',
                          '-I'+str(ROOT/'third_party/MQSim/src'), ROOT/'tests/contracts_abi_probe.cc',
                          '-o', output/'contracts_abi_probe'])
        abi = json.loads(run('abi-run', [output/'contracts_abi_probe']))
        check('native ABI measured: SQE=72, CQE=16, doorbell=2 bytes', abi == fixture['abi'])
        report['ledger'] = validate(fixture, abi)
        check('independent encoding, ordinary messages and directional payload/wire totals agree')
        check('buffer service bytes=2352 and peak credit=1248 agree with manual audit')
        for review in fixture['manual_review']:
            check('manual review: '+review['check'])
        # Deliberately corrupt the ledger: the audit must detect double billing,
        # omitted control traffic, wrong ABI, header padding and vector bypass.
        corruptions = {
            'duplicate resource charge': lambda x: x['host']['messages'].append(copy.deepcopy(x['host']['messages'][0])),
            'missing CQ doorbell': lambda x: x['controller']['messages'].pop(5),
            'standard SQE assumption': lambda x: x['abi'].update(sqe_bytes=64),
            'padded score result': lambda x: x['controller']['messages'][9].update(payload_bytes=88),
            'controller full vector return': lambda x: x['controller'].update(vector_D2H_bytes=1024),
            'two service owners': lambda x: x['service_owners'].update(onfi_data_out=['MQSim', 'BufferModel']),
        }
        for label, mutate in corruptions.items():
            altered = copy.deepcopy(fixture)
            mutate(altered)
            try:
                validate(altered, abi)
            except ValueError:
                check('negative ledger rejected: '+label)
            else:
                raise ValueError('audit accepted '+label)
        report['dependencies'] = {}
        for name in ('MQSim', 'sst-core'):
            path = ROOT/lock['dependencies'][name]['path']
            head = run(name+'-head', ['git', '-C', path, 'rev-parse', 'HEAD']).decode().strip()
            diff = run(name+'-diff', ['git', '-C', path, 'diff', '--binary', lock['dependencies'][name]['commit'], '--'])
            status = run(name+'-status', ['git', '-C', path, 'status', '--porcelain']).decode()
            source_hashes = {str(p.relative_to(path)): digest(p) for p in sorted((path/'src').rglob('*'))
                             if p.is_file() and p.suffix in ('.h', '.hpp', '.cc', '.cpp', '.c')}
            report['dependencies'][name] = dict(head=head, native_locked_commit=lock['dependencies'][name]['commit'],
                                               status=status, diff_sha256=hashlib.sha256(diff).hexdigest(),
                                               source_files_sha256=source_hashes)
            if name == 'sst-core':
                check('SST fixed commit and clean workspace', head == lock['dependencies'][name]['commit'] and not status)
        for name in ('m0.3a', 'm0.3b'):
            evidence = ROOT/f'docs/evidence/{name}/acceptance-2/manifest.json'
            # Historical evidence is local/ignored; fresh checkouts use the
            # documented E1/E2 reproduction commands instead of invented proof.
            if evidence.exists():
                manifest = json.loads(evidence.read_text())
                check(name+' dependency archived acceptance passed', manifest['status'] == 'passed')
            else:
                report.setdefault('limitations', []).append(name+' historical local manifest absent; task docs are the dependency record')
        docpaths = ['docs/contracts.md', 'docs/archive/v0.1/m0.4-minimal-contract.md', 'docs/archive/v0.1/v0.1-tasks.md', 'docs/archive/v0.1/implementation-plan.md', 'README.md']
        for name in docpaths:
            document = ROOT/name
            for target in re.findall(r'\]\(([^)]+)\)', document.read_text()):
                if ':' not in target and not target.startswith('#'):
                    resolved = document.parent/target.split('#')[0]
                    if not resolved.exists() and 'evidence' in resolved.parts:
                        report.setdefault('limitations', []).append('generated evidence absent: '+target)
                    else:
                        require(resolved.exists(), name+' broken link '+target)
        check('all touched documentation relative links resolve locally')
        check('PRD FR-02/04/07/08 and planning basis remain present', all((ROOT/p).exists() for p in ('AGENTS.md', 'docs/prd-v0.1.md', 'docs/archive/v0.1/implementation-plan.md')))
        if args.ctest_build:
            run('ctest-regression', ['ctest', '--test-dir', args.ctest_build.resolve(), '--output-on-failure'])
            check('existing platform CTest regression passed (E1/E2 scope only)')
        report['inputs_sha256'] = {p: digest(ROOT/p) for p in docpaths + [
            'AGENTS.md', 'docs/prd-v0.1.md', 'deps.lock.json', 'tools/verify_contract.py',
            'tests/contracts_abi_probe.cc', 'tests/fixtures/m0.4-round.json']}
        report['status'] = 'passed'
        print(json.dumps(dict(status='passed', assertions=len(report['assertions']), ledger=report['ledger']), indent=2))
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        (output/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
