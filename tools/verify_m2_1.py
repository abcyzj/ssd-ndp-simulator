"""M2.1: independent byte/sector fixture, real backend correlation and ownership checks."""
import hashlib
import json
import subprocess
from pathlib import Path
from verify_m1_1 import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/m2.1'
OUT.mkdir(parents=True, exist_ok=True)
commands, assertions = [], []

def run(name, args):
    argv = list(map(str, args))
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600)
    (OUT / (name + '.log')).write_text(p.stdout + p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode))
    if p.returncode:
        raise RuntimeError(f'{name}: exit {p.returncode}; see {OUT}')
    return p.stdout

def lines(output, prefix):
    return [list(map(int, line.split()[1:])) for line in output.splitlines() if line.startswith(prefix + ' ')]

try:
    fixture = json.loads((ROOT / 'tests/fixtures/m2.1-layout.json').read_text())
    objects = {(o['kind'], o['id']): o for o in fixture['objects']}
    # Independent enumeration of each requested byte and covered sector, not production's span merge.
    sectors, logical, requested = set(), set(), []
    for s in fixture['selections']:
        o = objects[s['kind'], s['id']]
        assert 0 <= s['offset'] < s['offset'] + s['length'] <= o['bytes']
        for offset in range(s['offset'], s['offset'] + s['length']):
            sectors.add((o['byte_offset'] + offset) // 512)
            logical.add((s['kind'], s['id'], offset))
            requested.append((s['kind'], s['id'], offset))
    manual = fixture['manual']
    assert len(requested) == manual['useful_bytes_with_selection_multiplicity'] == 596
    assert len(logical) == manual['unique_logical_bytes'] == 592
    assert len(sectors) * 512 == manual['sector_bytes'] == 2048
    lock = json.loads((ROOT / 'deps.lock.json').read_text())
    cmake = Path(lock['tools']['cmake']['path'])
    ctest = cmake.with_name('ctest')
    build = ROOT / 'build/m2.1'
    run('configure', [cmake, '-S', ROOT, '-B', build])
    run('build', [cmake, '--build', build, '-j4'])
    run('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
    probe = run('content', [build / 'content_probe'])
    assert run('content-repeat', [build / 'content_probe']) == probe
    assert lines(probe, 'EXTENT') == manual['extents']
    assert lines(probe, 'DEFAULT') == [manual['default_layout']]
    actual_sectors, actual_slices = set(), []
    current = None
    for line in probe.splitlines():
        if line.startswith('EXTENT '):
            current, count = map(int, line.split()[1:])
            actual_sectors.update(range(current, current + count))
        elif line.startswith('SLICE '):
            kind, object_id, offset, length, extent_offset = map(int, line.split()[1:])
            o = objects[kind, object_id]
            assert o['byte_offset'] + offset == current * 512 + extent_offset
            actual_slices.extend((kind, object_id, i) for i in range(offset, offset + length))
    assert actual_sectors == sectors and sorted(actual_slices) == sorted(requested)
    digest = hashlib.sha256(bytes(i % 251 for i in range(1040))).hexdigest()
    assert 'HASH ' + digest in probe
    assert 'live=0' in probe and 'M2.1 PASS' in probe
    releases = lines(probe, 'RELEASE')
    assert len({r[1] for r in releases}) == len(releases)
    assert any(r[2:]==[0,1,0] for r in releases), 'in-flight pin must survive last caller release'
    assert sum(r[-1] for r in releases) == 6, 'six final reclaimed handle records'
    assertions.append('byte-by-byte extent/slice coverage; 596 requested/592 unique useful bytes, 2048 sector bytes; default independent regions; hashlib SHA256 match; deterministic repeat')
    assertions.append('ownership release ledger: unique leases, last caller release retains in-flight record, six handles fully reclaimed; boundary/invalid/version/permission/failure/overflow assertions in content probe')
    backend = run('backend', [build / 'content_backend_probe'])
    assert run('backend-repeat', [build / 'content_backend_probe']) == backend
    assert sorted(lines(backend, 'PAGE')) == manual['pages']
    assert sorted(lines(backend, 'PAGE_REQUEST')) == [[7,3,0,512], [7,3,1,1024], [32,1,4,512]]
    values = [l.split()[1:] for l in backend.splitlines() if l.startswith('VALUE ')]
    assert len(values) == len(fixture['selections'])
    for i, (value, selection) in enumerate(zip(values, fixture['selections'])):
        obj = objects[selection['kind'], selection['id']]
        content = bytes(j % obj['pattern_mod'] for j in range(obj['bytes']))
        part = content[selection['offset']:selection['offset']+selection['length']]
        assert value == [str(i), str(len(part)), hashlib.sha256(part).hexdigest()]
    bound = [list(map(int, l.split()[1:4])) for l in backend.splitlines() if l.startswith('BOUND ')]
    completed = lines(backend, 'ASSOCIATED')
    assert [b[1:] for b in bound] == manual['extents']
    assert sorted(b for b in bound) == sorted(c[:3] for c in completed)
    assert all(c[3] > 53811904 for c in completed)
    assert 'M2.1 BACKEND PASS' in backend and 'live=0' in backend
    assertions.append('real deterministic-preloaded MQSim ordinary read: two exact bound/completed ID/LBA/sector tuples; actual PHY LPAs 0/1/4 with 512/1024/512 B and exact parent extent; hashlib confirms all real content slices after data+CQ; repeat identical')
    prefix = json.loads((build / 'sst-paths.json').read_text())['prefix']
    asan = ROOT / 'build/m2.1-asan'
    run('asan-configure', [cmake, '-S', ROOT, '-B', asan, '-DSST_ROOT=' + prefix, '-DNDPSIM_SANITIZE=ON'])
    run('asan-build', [cmake, '--build', asan, '--target', 'content_probe', 'content_backend_probe', '-j4'])
    run('asan-ctest', [ctest, '--test-dir', asan, '-R', '^content_', '--output-on-failure'])
    assertions.append('AddressSanitizer: both content tests pass with default checks; no claim of complete upstream leak audit')
    run('diff-check', ['git', 'diff', '--check'])
    run('mqsim-diff-check', ['git', '-C', 'third_party/MQSim', 'diff', '--check'])
    assert not run('sst-status', ['git', '-C', 'third_party/sst-core', 'status', '--porcelain'])
    status = 'passed'
finally:
    paths = ['AGENTS.md', 'README.md', 'docs/prd.md', 'docs/implementation-plan.md',
             'docs/v0.1-tasks.md', 'docs/m2.1-content-layout.md', 'src/content/content.h', 'src/content/content.cc', 'src/content/sha256.cc',
             'tests/content_probe.cc', 'tests/content_backend_probe.cc', 'tests/ordinary_fixture.h',
             'tests/fixtures/m2.1-layout.json', 'src/backend/external_flow.h', 'CMakeLists.txt',
             'configs/native/ssdconfig.xml', 'deps.lock.json', 'tools/verify_m2_1.py']
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        status=locals().get('status', 'failed'), commands=commands, assertions=assertions,
        mqsim_source=mqsim_source_snapshot(),
        sst_commit=subprocess.check_output(['git','-C','third_party/sst-core','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        inputs_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        logs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.log')}
    ), indent=2) + '\n')
print(f'M2.1 {status}: {len(assertions)} acceptance groups; {OUT}')
