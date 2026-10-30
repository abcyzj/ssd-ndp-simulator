"""M3.3: real SST/MQSim controller credits, FIFO services and safe failure drain."""

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
OUT = ROOT / 'docs/evidence/m3.3'
OUT.mkdir(parents=True, exist_ok=True)
BUILD = ROOT / 'build/m3.3'
LOCK = json.loads((ROOT / 'deps.lock.json').read_text())
commands, assertions, ledgers = [], [], {}
MODES = ['', 'command', 'staged', 'evaluate', 'result', 'cross', 'compute', 'slots', 'zero', 'open-command', 'query-staged']
PREFIXES = ('CONFIG ', 'CREDIT ', 'RESOURCE ', 'STORAGE ', 'MESSAGE ', 'STATE ', 'M3.3 PASS')


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


def rows(text, prefixes=PREFIXES):
    return [r for r in text.splitlines() if r.startswith(prefixes)]


def ledger(text, mode):
    config = {k: int(v) for k, v in (x.split('=') for x in rows(text, ('CONFIG ',))[0].split()[1:])}
    states = {}
    for line in rows(text, ('STATE ',)):
        _, time, q, task, state = line.split()
        states.setdefault(int(task), {})[state] = int(time)
    resources = {}
    port_free = [0]
    compute_free = [0] * config['slots']
    port_bytes = compute_wait = 0
    for line in rows(text, ('RESOURCE ',)):
        _, stage, *values = line.split()
        q, task, submitted, start, end, amount = map(int, values)
        slots = compute_free if stage == 'arithmetic' else port_free
        first = min(range(len(slots)), key=slots.__getitem__)
        assert start == max(submitted, slots[first]), (mode, line, slots)
        slots[first] = end
        rate = config['flop_rate'] if stage == 'arithmetic' else config['staging_rate']
        duration = (amount + rate - 1) // rate
        if stage == 'arithmetic':
            duration += config['launch']
            compute_wait += start - submitted
            assert states[task]['Running'] == start
        else:
            port_bytes += amount
        assert end - start == duration, (mode, line)
        resources.setdefault(task, {})[stage] = (submitted, start, end, amount)
    messages = {}
    payload = useful = 0
    for line in rows(text, ('MESSAGE ',)):
        kind, q, task, size, valid, submitted, arrived = map(int, line.split()[1:])
        assert arrived >= submitted
        messages.setdefault(task, {})[kind] = (submitted, arrived, size, valid)
        payload += size
        useful += valid
    storage = {}
    for line in rows(text, ('STORAGE ',)):
        q, task, submitted, completed, sectors, requests, transactions = map(int, line.split()[1:])
        assert submitted >= messages[task][3][1]  # command has really arrived
        assert completed >= submitted and transactions >= requests >= 1
        r = resources[task]
        assert r['input-write'][0] == completed and r['input-write'][3] == sectors
        if 'compute-read' in r:
            assert r['compute-read'][0] == r['input-write'][2]
            assert r['compute-read'][3] == sectors + 4 * config['dimension']
            assert r['arithmetic'][0] == r['compute-read'][2]
        storage[task] = (submitted, completed, sectors, requests, transactions)
    live = {}
    used = peak = 0
    reserved = released = waits = 0
    credit_order = []
    for line in rows(text, ('CREDIT ',)):
        _, time, cid, q, task, size, expected_used, reserve, lifetime = line.split()
        time, cid, q, task, size, expected_used, reserve = map(int, (time, cid, q, task, size, expected_used, reserve))
        if reserve:
            assert cid not in live
            live[cid] = [size, task, lifetime]
            used += size
            reserved += size
            assert time <= messages[task][1 if lifetime == 'query' else 3][0]
            if lifetime == 'block':
                wait = time - states[task]['WaitingResource']
                assert wait >= 0
                waits += wait
                credit_order.append(task)
                assert size <= config['block']
        else:
            assert cid in live and live[cid][1:] == [task, lifetime] and 0 < size <= live[cid][0]
            live[cid][0] -= size
            used -= size
            released += size
            if not live[cid][0]:
                del live[cid]
                if lifetime == 'block':
                    reply = messages[task].get(4, messages[task].get(7))
                    assert reply and time == reply[1]
                else:
                    if 7 in messages[task]:  # failed Open
                        assert time == messages[task][7][1]
                    else:
                        closes = [m[6][1] for t, m in messages.items() if 6 in m and
                                  any(x.split()[3] == str(t) and x.split()[2] == str(q) for x in rows(text, ('STATE ',)))]
                        assert time in closes
            elif lifetime == 'block' and task in resources and 'result-write' in resources[task]:
                # Input/scratch cease to occupy capacity at the final arithmetic use.
                assert time == resources[task]['arithmetic'][2]
        peak = max(peak, used)
        assert used == expected_used and 0 <= used <= config['capacity']
    assert not live and used == 0 and reserved == released
    for task, m in messages.items():
        if 1 in m and 2 in m:
            assert resources[task]['query-write'][0] == m[1][1]
            assert m[2][0] == resources[task]['query-write'][2]
            assert states[task]['Visible'] == m[2][1]
        if 4 in m:
            r = resources[task]
            assert r['result-write'][0] == r['arithmetic'][2]
            assert r['result-read'][0] == r['result-write'][2]
            assert m[4][0] == r['result-read'][2]
            assert states[task]['WaitingReturn'] == m[4][0]
            assert states[task]['Visible'] == m[4][1]
        if 7 in m:
            assert 'Visible' not in states[task] and states[task]['Failed'] == m[7][1]
    if mode not in ('compute', 'slots'):
        assert waits > 0, (mode, 'no credit wait')
    if mode in ('compute', 'slots'):
        assert compute_wait > 0
    # Fixed semantic chunks [2,2,1] even while free capacity changes.
    by_query = {}
    for line in rows(text, ('MESSAGE ',)):
        kind, q, task, size, valid, submitted, arrived = map(int, line.split()[1:])
        if kind == 3:
            by_query.setdefault(q, []).append((size - 64) // 4)
    assert all(v == [2, 2, 1] for v in by_query.values())
    assert len(by_query) == (3 if mode in ('open-command', 'query-staged') else 4)
    if mode == 'cross':
        assert any(transactions > requests for _, _, _, requests, transactions in storage.values())
    if mode == '':
        assert peak == config['capacity'] == 1152 and config['admission'] == 2
        assert port_bytes == 21088  # 4 * (query 8 + input 2560 + reads 2584 + result writes/reads 120)
        assert payload == 2624 and useful == 352  # query + IDs + scores, including all open/close commands
    return dict(config=config, peak=peak, reserved=reserved, released=released,
                credit_wait_ns=waits, compute_wait_ns=compute_wait, port_bytes=port_bytes,
                envelope_payload_bytes=payload, envelope_useful_bytes=useful,
                storage_requests=sum(v[3] for v in storage.values()),
                storage_transactions=sum(v[4] for v in storage.values()))


try:
    before = mqsim_source_snapshot()
    cmake = LOCK['tools']['cmake']['path']
    ctest = Path(cmake).with_name('ctest')
    run('configure', [cmake, '-S', ROOT, '-B', BUILD])
    run('build', [cmake, '--build', BUILD, '-j4'])
    run('ctest', [ctest, '--test-dir', BUILD, '--output-on-failure'])
    paths = json.loads((BUILD / 'sst-paths.json').read_text())
    assert 'M3.3 RESOURCE PASS' in run('resources', [BUILD / 'controller_resource_probe'])
    assertions.append('isolated strict FIFO/head-of-line credits, partial release and exact conservation; oversized reservation immediate CapacityExceeded without allocation; duplicate release rejected; slots=1 ends 10/15/25 ns, slots=2 ends 10/5/15 ns; zero-ns deferred completion; checked overflow')
    for mode in MODES:
        name = mode or 'capacity'
        probe = run(name + '-probe', [BUILD / 'controller_probe', mode])
        actual = [run(name + f'-sst-{i}', [paths['sst'], '--add-lib-path=' + str(BUILD), ROOT / 'configs/regression/controller.py'],
                      env={'NDPSIM_CONTROLLER_MODE': mode}) for i in (1, 2)]
        assert rows(probe) == rows(actual[0]) == rows(actual[1]), name
        ledgers[name] = ledger(probe, mode)
        assertions.append(name + ': real SST twice equals bridge probe; every credit occupancy/release and service FIFO/time/byte formula independently recomputed; storage->staging->compute->result path and arrival visibility verified; four queries drain; all content/results/credits/messages/slots released')
    (OUT / 'ledgers.json').write_text(json.dumps(ledgers, indent=2) + '\n')
    asan = ROOT / 'build/m3.3-asan'
    run('asan-configure', [cmake, '-S', ROOT, '-B', asan, '-DSST_ROOT=' + paths['prefix'], '-DNDPSIM_SANITIZE=ON'])
    run('asan-build', [cmake, '--build', asan, '--target', 'controller_probe', 'controller_resource_probe', 'ordinary_io_probe', '-j4'])
    run('asan-ctest', [ctest, '--test-dir', asan, '-R', '^(controller_|ordinary_)', '--output-on-failure'])
    assertions.append('ASan controller capacity, cross-page, one/two slots, zero service, six safe failure points, isolated resources and ordinary QD1/CMT miss pass; not a full upstream leak audit')
    # Native oracle comes only from the immutable locked commit, never the modified tree.
    native = BUILD / 'native'
    native.mkdir(exist_ok=True)
    archive = BUILD / 'native.tar'
    base = LOCK['dependencies']['MQSim']['commit']
    run('native-export', ['git', '-C', ROOT / 'third_party/MQSim', 'archive', '-o', archive, base])
    with tarfile.open(archive) as t:
        t.extractall(native)
    sources = sorted(p for p in (native / 'src').rglob('*.cpp') if p.name != 'main.cpp')
    run('native-build', [LOCK['tools']['cxx']['path'], '-isysroot', LOCK['target']['sdk_path'], '-std=c++17', '-O1', '-Xclang', '-mconstructor-aliases', '-DNDPSIM_NATIVE_ORACLE', '-I' + str(native / 'src'), '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'tests'), '-I' + str(BUILD / 'generated'), ROOT / 'tests/ordinary_native.cc', *sources, '-o', native / 'probe'])
    for name, args, qd, miss in [('qd1', [], 1, 0), ('qd32', ['normal', 'concurrent'], 32, 0), ('cmt-miss', ['miss', 'concurrent'], 32, 1)]:
        expected = run(name + '-native', [native / 'probe', *args])
        for i in (1, 2):
            actual = run(name + '-sst-' + str(i), [paths['sst'], '--add-lib-path=' + str(BUILD), ROOT / 'configs/regression/ordinary.py'], env={'NDPSIM_QD': str(qd), 'NDPSIM_MISS': str(miss)})
            assert rows(expected, ('IO ', 'INITIAL ', 'MAP ', 'CMT ', 'CACHE ', 'FINAL ')) == rows(actual, ('IO ', 'INITIAL ', 'MAP ', 'CMT ', 'CACHE ', 'FINAL '))
        assertions.append(name + ': locked independent native oracle equals real SST twice, exact ordinary requests and initial mapping/CMT')
    run('diff-check', ['git', 'diff', '--check'])
    run('mqsim-diff-check', ['git', '-C', ROOT / 'third_party/MQSim', 'diff', '--check'])
    assert not run('sst-status', ['git', '-C', ROOT / 'third_party/sst-core', 'status', '--porcelain'])
    assert before == mqsim_source_snapshot()
    status = 'passed'
finally:
    inputs = ['README.md', 'tests/ordinary_native.cc', 'tests/ordinary_fixture.h', 'src/backend/ordinary_device.h', 'src/backend/event_bridge.h', 'src/runtime/runtime.h', 'AGENTS.md', 'docs/prd.md', 'docs/archive/v0.1/implementation-plan.md', 'docs/archive/v0.1/v0.1-tasks.md', 'docs/archive/v0.1/m0.4-minimal-contract.md', 'docs/archive/v0.1/m3.3-controller-buffer.md', 'CMakeLists.txt', 'deps.lock.json', 'configs/native/ssdconfig.xml', 'configs/regression/controller.py', 'src/model/controller.h', 'src/model/controller.cc', 'src/content/content.h', 'src/content/content.cc', 'src/backend/local_read.h', 'src/runtime/runtime.cc', 'src/transport/pcie.h', 'src/transport/conversation.h', 'src/operators/operators.cc', 'tests/controller_scenario.h', 'tests/controller_probe.cc', 'tests/controller_resource_probe.cc', 'src/sst/controller_validation.cc', 'tools/archive/v0.1/verify_m3_3.py']
    snapshot = mqsim_source_snapshot()
    snapshot['diff_from_head_sha256'] = hashlib.sha256(subprocess.check_output(['git', '-C', str(ROOT / 'third_party/MQSim'), 'diff', '--binary', 'HEAD', '--'])).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps(dict(status=locals().get('status', 'failed'), commands=commands, assertions=assertions, mqsim_source=snapshot, oracle_commit=locals().get('base'), inputs_sha256={p: sha(ROOT / p) for p in inputs if (ROOT / p).exists()}, logs_sha256={p.name: sha(p) for p in OUT.glob('*.log')}), indent=2) + '\n')
print(f'M3.3 {status}: {len(assertions)} acceptance groups; {OUT}')
