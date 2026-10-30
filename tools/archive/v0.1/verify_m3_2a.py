"""M3.2a storage-only local route, failure boundaries and locked native regression."""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from source_snapshot import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/evidence/m3.2a'
OUT.mkdir(parents=True, exist_ok=True)
commands, assertions = [], []
lock = json.loads((ROOT / 'deps.lock.json').read_text())
build = ROOT / 'build/m3.2a'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(name, args, expected=0, env=None):
    argv = list(map(str, args))
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, **(env or {})))
    (OUT / (name + '.log')).write_text(p.stdout + p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode, expected_exit_code=expected, env=env))
    if p.returncode != expected:
        raise RuntimeError(f'{name}: exit {p.returncode}; see {OUT}')
    return p.stdout + p.stderr


def rows(text, prefixes):
    return [line for line in text.splitlines() if line.startswith(prefixes)]


try:
    cmake = lock['tools']['cmake']['path']
    ctest = Path(cmake).with_name('ctest')
    run('configure', [cmake, '-S', ROOT, '-B', build])
    run('build', [cmake, '--build', build, '-j4'])
    run('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
    paths = json.loads((build / 'sst-paths.json').read_text())
    actual = []
    for i in (1, 2):
        actual.append(run(f'local-sst-{i}', [paths['sst'], '--add-lib-path=' + str(build), ROOT / 'configs/regression/local_read.py']))
    prefixes = ('LOCAL ', 'TRANSACTION ', 'LOCAL_DRAIN ', 'M3.2a PASS')
    assert rows(actual[0], prefixes) == rows(actual[1], prefixes)
    probe = run('local-probe', [build / 'formal_local_read_probe'])
    assert rows(probe, prefixes) == rows(actual[0], prefixes)
    requests = [list(map(int, line.split()[1:])) for line in rows(probe, ('LOCAL ',))]
    transactions = [list(map(int, line.split()[1:])) for line in rows(probe, ('TRANSACTION ',))]
    assert len(requests) == 4 and len(transactions) == 6
    assert len({r[3] for r in requests}) == 4 and len({t[1] for t in transactions}) == 6
    assert {(r[0], r[1], r[2]) for r in requests} == {(1, 1, 11), (2, 2, 12)}
    for r in requests:
        op, query, task, token, lba, sectors, size, submitted, completed = r
        assert (lba, sectors, size) in [(7, 3, 1536), (32, 1, 512)]
        ts = [t for t in transactions if t[0] == token]
        assert sum(t[3] for t in ts) == size
        assert all(submitted <= t[5] <= completed for t in ts)
        assert completed == max(t[5] for t in ts)
        assert [(t[2], t[3], t[4]) for t in ts] == ([(0, 512, 128), (1, 1024, 3)] if lba == 7 else [(4, 512, 1)])
    assert any(ts[0][5] > ts[1][5] for r in requests if len(ts := [t for t in transactions if t[0] == r[3]]) == 2)
    assert 'vector_d2h=0 ordinary_d2h=512 live=0' in probe
    assertions.append('real SST repeated exactly; standalone bridge matches; 2 parents/4 requests/6 stable transactions; exact cross-page LPA/bitmap/bytes; real reversed transaction finish; last transaction equals request finish; no local DMA; ordinary read after local returns 512 B; pending handle never published; all pins and active records reclaimed')
    reverse = run('local-reversed-requests', [build / 'formal_local_read_probe', 'reverse'])
    assert sorted(rows(reverse, ('LOCAL ',))) == sorted(rows(probe, ('LOCAL ',)))
    assert [int(line.split()[4]) for line in rows(reverse, ('LOCAL ',))] == [4, 3, 2, 1]
    assertions.append('controlled delivery of all four real completed request values in reverse order: same metadata and completion times, exactly two parent callbacks')
    for order in ('forward', 'reverse'):
        run('controlled-' + order, [build / 'local_read_probe', *(['forward'] if order == 'forward' else [])])
    run('unsupported', [build / 'local_read_probe', 'unsupported'])
    run('no-pcie', [build / 'local_read_device_probe'])
    for fault in ('fault', 'transaction', 'unknown', 'duplicate'):
        text = run('fatal-' + fault, [build / 'formal_local_read_probe', fault], expected=1)
        assert 'BackendFailure' in text and 'M3.2a PASS' not in text and 'LOCAL_DRAIN' not in text
        if fault != 'unknown':
            assert 'query=1' in text and 'task=11' in text and 'request=1' in text
    assertions.append('forward/reverse controlled PHY and late observers; no PCIe attachment; unsupported/invalid requests rejected; malformed request bytes, incomplete transaction, unknown token, duplicate completion each exit 1 with no successful parent result; known request failures preserve query/task/request')
    asan = ROOT / 'build/m3.2a-asan'
    run('asan-configure', [cmake, '-S', ROOT, '-B', asan, '-DSST_ROOT=' + paths['prefix'], '-DNDPSIM_SANITIZE=ON'])
    run('asan-build', [cmake, '--build', asan, '--target', 'formal_local_read_probe', 'local_read_probe', 'local_read_device_probe', 'ordinary_io_probe', '-j4'])
    run('asan-ctest', [ctest, '--test-dir', asan, '-R', '^(formal_local_read|backend_local_read|ordinary_)', '--output-on-failure'])
    for fault in ('fault', 'transaction', 'unknown', 'duplicate'):
        text = run('asan-fatal-' + fault, [asan / 'formal_local_read_probe', fault], expected=1)
        assert 'BackendFailure' in text and 'ERROR: AddressSanitizer' not in text
    assertions.append('AddressSanitizer local positive/negative and ordinary QD1/small-CMT tests; fatal paths explicitly exit 1 without sanitizer report; no claim of complete upstream leak audit')
    # Independent immutable native oracle, never exported from the modified workspace.
    native = build / 'native'
    native.mkdir(exist_ok=True)
    archive = build / 'native.tar'
    base = lock['dependencies']['MQSim']['commit']
    run('native-export', ['git', '-C', ROOT / 'third_party/MQSim', 'archive', '-o', archive, base])
    with tarfile.open(archive) as t:
        t.extractall(native)
    sources = sorted(p for p in (native / 'src').rglob('*.cpp') if p.name != 'main.cpp')
    run('native-build', [lock['tools']['cxx']['path'], '-isysroot', lock['target']['sdk_path'], '-std=c++17', '-O1', '-Xclang', '-mconstructor-aliases', '-DNDPSIM_NATIVE_ORACLE', '-I' + str(native / 'src'), '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'tests'), '-I' + str(build / 'generated'), ROOT / 'tests/ordinary_native.cc', *sources, '-o', native / 'probe'])
    for name, args, qd, miss in [('qd1', [], 1, 0), ('qd32', ['normal', 'concurrent'], 32, 0), ('cmt-miss', ['miss', 'concurrent'], 32, 1)]:
        expected = run(name + '-native', [native / 'probe', *args])
        for i in (1, 2):
            text = run(name + '-sst-' + str(i), [paths['sst'], '--add-lib-path=' + str(build), ROOT / 'configs/regression/ordinary.py'], env={'NDPSIM_QD': str(qd), 'NDPSIM_MISS': str(miss)})
            assert rows(expected, ('IO ', 'INITIAL ', 'MAP ', 'CMT ', 'CACHE ', 'FINAL ')) == rows(text, ('IO ', 'INITIAL ', 'MAP ', 'CMT ', 'CACHE ', 'FINAL '))
        assertions.append(name + ': locked unmodified native matches real SST twice for every request LBA/size/direction/completion time and initial mapping/CMT summary')
    run('diff-check', ['git', 'diff', '--check'])
    run('mqsim-diff-check', ['git', '-C', 'third_party/MQSim', 'diff', '--check'])
    assert not run('sst-status', ['git', '-C', 'third_party/sst-core', 'status', '--porcelain'])
    status = 'passed'
finally:
    inputs = ['AGENTS.md', 'docs/prd-v0.1.md', 'docs/archive/v0.1/implementation-plan.md', 'docs/archive/v0.1/v0.1-tasks.md', 'docs/archive/v0.1/m3.2a-local-read.md', 'CMakeLists.txt', 'deps.lock.json', 'configs/native/ssdconfig.xml', 'configs/regression/local_read.py', 'src/backend/local_read.h', 'tests/local_read_probe.cc', 'tests/local_read_scenario.h', 'tests/formal_local_read_probe.cc', 'src/sst/local_read_validation.cc', 'tools/archive/v0.1/verify_m3_2a.py', 'tests/ordinary_fixture.h', 'tests/ordinary_native.cc', 'src/backend/external_flow.h']
    snapshot = mqsim_source_snapshot()
    snapshot['diff_from_head_sha256'] = hashlib.sha256(subprocess.check_output(['git', '-C', str(ROOT / 'third_party/MQSim'), 'diff', '--binary', 'HEAD', '--'])).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps(dict(status=locals().get('status', 'failed'), commands=commands, assertions=assertions, mqsim_source=snapshot, oracle_commit=locals().get('base'), inputs_sha256={p: sha(ROOT / p) for p in inputs if (ROOT / p).exists()}, logs_sha256={p.name: sha(p) for p in OUT.glob('*.log')}), indent=2) + '\n')
print(f'M3.2a {status}: {len(assertions)} acceptance groups; {OUT}')
