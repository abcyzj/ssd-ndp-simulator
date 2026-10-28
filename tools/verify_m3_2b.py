"""M3.2b shared MQSim PCIe, byte ledger, arrival gating and native baseline."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from verify_m1_1 import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/m3.2b'
OUT.mkdir(parents=True, exist_ok=True)
lock = json.loads((ROOT / 'deps.lock.json').read_text())
build = ROOT / 'build/m3.2b'
commands, assertions = [], []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(name, args, env=None):
    argv = list(map(str, args))
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, **(env or {})))
    (OUT / (name + '.log')).write_text(p.stdout + p.stderr)
    commands.append(dict(name=name, argv=argv, exit_code=p.returncode, env=env))
    if p.returncode:
        raise RuntimeError(f'{name}: exit {p.returncode}; see {OUT}')
    return p.stdout + p.stderr


def rows(text, prefixes):
    return [line for line in text.splitlines() if line.startswith(prefixes)]


def ledger(text):
    messages = {}
    for line in rows(text, ('PCIE ',)):
        _, stage, *values = line.split()
        mid, pid, dest, kind, time, payload, wire, service = map(int, values)
        m = messages.setdefault(mid, {})
        assert stage not in m
        m[stage] = (pid, dest, kind, time, payload, wire, service)
    queues = {0: [], 1: []}
    for mid, m in messages.items():
        assert set(m) == {'enqueue', 'start', 'arrive'}
        pid, dest, kind, enqueued, payload, wire, service = m['enqueue']
        expected_wire = 32 if kind == 0 else payload + ((payload + 127) // 128) * 28
        assert wire == expected_wire
        assert service == (wire + 3) // 4
        assert all(v[:3] == (pid, dest, kind) and v[4:] == (payload, wire, service) for v in m.values())
        start, arrive = m['start'][3], m['arrive'][3]
        assert enqueued <= start and arrive == start + service
        queues[dest].append((mid, pid, kind, enqueued, start, arrive, payload, wire))
    mixed_wait = []
    for dest, queue in queues.items():
        for previous, current in zip(queue, queue[1:]):
            assert current[4] == max(current[3], previous[5]), (previous, current)
            if bool(previous[1]) != bool(current[1]) and current[4] > current[3]:
                mixed_wait.append((dest, current[0], current[4] - current[3]))
    assert {d for d, mid, wait in mixed_wait} == {0, 1}
    envelopes = [list(map(int, line.split()[1:])) for line in rows(text, ('ENVELOPE ',))]
    assert len(envelopes) == 8 and {r[1] for r in envelopes} == set(range(1, 8))
    expected = {1: (192, 128), 2: (48, 0), 3: (72, 8), 4: (80, 24),
                5: (68, 4), 6: (48, 0), 7: (48, 0), 8: (48, 0)}
    for mid, kind, direction, query, task, payload, useful, submitted, arrived in envelopes:
        assert expected[mid] == (payload, useful) and query == 1
        native = [m for m in messages.values() if m['enqueue'][0] == mid]
        assert len(native) == 1
        m = native[0]
        assert (m['enqueue'][3], m['arrive'][3]) == (submitted, arrived)
        assert m['enqueue'][1] == (1 if direction == 0 else 0) and m['enqueue'][2] == 3
    totals = {}
    for dest, queue in queues.items():
        for platform in (False, True):
            selected = [r for r in queue if bool(r[1]) == platform]
            totals[f'{"D2H" if dest == 0 else "H2D"}_{"platform" if platform else "NVMe"}'] = {
                'messages': len(selected), 'payload': sum(r[6] for r in selected), 'wire': sum(r[7] for r in selected)}
    assert totals['D2H_NVMe']['payload'] == 2 * (512 + 16 + 4)
    assert totals['H2D_NVMe']['payload'] == 2 * (2 + 72 + 2)
    assert totals['H2D_platform']['payload'] == 380 and totals['D2H_platform']['payload'] == 224
    assert sum(r[6] for r in envelopes) == 164
    (OUT / 'ledger.json').write_text(json.dumps(dict(totals=totals, mixed_wait_ns=mixed_wait,
        envelopes=envelopes, source='MQSim PCIe_Link actual enqueue/start/arrive; payload and wire separate'), indent=2)+'\n')


try:
    before = mqsim_source_snapshot()
    cmake = lock['tools']['cmake']['path']
    ctest = Path(cmake).with_name('ctest')
    run('configure', [cmake, '-S', ROOT, '-B', build])
    run('build', [cmake, '--build', build, '-j4'])
    run('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
    paths = json.loads((build / 'sst-paths.json').read_text())
    contract = run('contract', [build / 'pcie_contract_probe'])
    assert 'zero_ns=pass duplex=pass reentry=pass' in contract
    assertions.append('explicit little-endian IEEE754 roundtrip; seven wire kinds; truncation/header/size/direction/status/reserved/nonfinite/duplicate-ID rejection; query/token/task/chunk/result-order validation; rate 1/2 and zero-ns FIFO/duplex exact formulas; same-direction reentrant append delivers exactly twice')
    actual = [run(f'mixed-sst-{i}', [paths['sst'], '--add-lib-path=' + str(build), ROOT / 'configs/m3.2b/pcie.py']) for i in (1, 2)]
    probe = run('mixed-probe', [build / 'pcie_probe'])
    prefix = ('PCIE ', 'ENVELOPE ', 'MIX_IO ', 'RESULT_VISIBLE ', 'M3.2b PASS')
    assert rows(actual[0], prefix) == rows(actual[1], prefix) == rows(probe, prefix)
    visible = rows(probe, ('RESULT_VISIBLE ',))
    assert len(visible) == 1
    values = dict(item.split('=') for item in visible[0].split()[1:])
    assert values['time'] == values['arrived']
    ledger(probe)
    assertions.append('real SST twice and standalone bridge match every event; actual FIFO equation independently recomputed for both directions and every native/platform message; positive mixed waiting in both directions; all payload/wire/semantic bytes conserved; 2 ordinary reads + 8 envelopes/seven kinds; 15 early completion/visibility checks rejected; runtime result only after ScoreResult arrives; no pending message/session/result remains')
    asan = ROOT / 'build/m3.2b-asan'
    run('asan-configure', [cmake, '-S', ROOT, '-B', asan, '-DSST_ROOT=' + paths['prefix'], '-DNDPSIM_SANITIZE=ON'])
    run('asan-build', [cmake, '--build', asan, '--target', 'pcie_probe', 'pcie_contract_probe', 'ordinary_io_probe', '-j4'])
    run('asan-ctest', [ctest, '--test-dir', asan, '-R', '^(shared_pcie_|ordinary_)', '--output-on-failure'])
    assertions.append('AddressSanitizer protocol/isolated/real mixed PCIe plus ordinary QD1 and concurrent CMT miss tests pass; no full upstream leak-audit claim')
    # Export the immutable oracle commit; never build native from the modified workspace.
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
            text = run(name + '-sst-' + str(i), [paths['sst'], '--add-lib-path=' + str(build), ROOT / 'configs/m1.3/ordinary.py'], env={'NDPSIM_QD': str(qd), 'NDPSIM_MISS': str(miss)})
            assert rows(expected, ('IO ', 'INITIAL ', 'MAP ', 'CMT ', 'CACHE ', 'FINAL ')) == rows(text, ('IO ', 'INITIAL ', 'MAP ', 'CMT ', 'CACHE ', 'FINAL '))
        assertions.append(name + ': independently exported locked native oracle equals real SST twice, exact per-request completion/address/size/order and initial mapping/CMT; no native quantization change')
    run('diff-check', ['git', 'diff', '--check'])
    run('mqsim-diff-check', ['git', '-C', 'third_party/MQSim', 'diff', '--check'])
    assert not run('sst-status', ['git', '-C', 'third_party/sst-core', 'status', '--porcelain'])
    assert before == mqsim_source_snapshot()
    status = 'passed'
finally:
    inputs = ['AGENTS.md', 'docs/prd.md', 'docs/implementation-plan.md', 'docs/v0.1-tasks.md', 'docs/m0.4-minimal-contract.md', 'docs/m3.2b-shared-pcie.md', 'CMakeLists.txt', 'deps.lock.json', 'configs/native/ssdconfig.xml', 'configs/m3.2b/pcie.py', 'src/transport/messages.h', 'src/transport/messages.cc', 'src/transport/pcie.h', 'src/transport/conversation.h', 'tests/pcie_scenario.h', 'tests/pcie_probe.cc', 'tests/pcie_contract_probe.cc', 'src/sst/pcie_validation.cc', 'tools/verify_m3_2b.py', 'tests/ordinary_fixture.h', 'tests/ordinary_native.cc']
    snapshot = mqsim_source_snapshot()
    snapshot['diff_from_head_sha256'] = hashlib.sha256(subprocess.check_output(['git', '-C', str(ROOT / 'third_party/MQSim'), 'diff', '--binary', 'HEAD', '--'])).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps(dict(status=locals().get('status', 'failed'), commands=commands, assertions=assertions, mqsim_source=snapshot, oracle_commit=locals().get('base'), inputs_sha256={p: sha(ROOT / p) for p in inputs if (ROOT / p).exists()}, logs_sha256={p.name: sha(p) for p in OUT.glob('*.log')}), indent=2) + '\n')
print(f'M3.2b {status}: {len(assertions)} acceptance groups; {OUT}')
