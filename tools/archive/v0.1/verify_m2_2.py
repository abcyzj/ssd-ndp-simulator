"""M2.2 reproducible controlled timing, SST integration, cleanup and regressions."""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import hashlib
import json
import subprocess
from pathlib import Path
from source_snapshot import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/evidence/m2.2'
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

try:
    lock = json.loads((ROOT / 'deps.lock.json').read_text())
    cmake = Path(lock['tools']['cmake']['path'])
    ctest = cmake.with_name('ctest')
    build = ROOT / 'build/m2.2'
    run('configure', [cmake, '-S', ROOT, '-B', build])
    run('build', [cmake, '--build', build, '-j4'])
    run('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
    output = run('runtime', [build / 'runtime_probe'])
    assert output == run('runtime-repeat', [build / 'runtime_probe'])
    rows = [list(map(int, l.split()[1:])) for l in output.splitlines() if l.startswith('TIMES ')]
    # Independent arithmetic: read -> compute -> return -> dynamic successor.
    assert len(rows) == 4
    for read_delta, compute_delta, return_delta, independent, decision, successor in rows:
        expected = 10 + read_delta + 20 + compute_delta + 30 + return_delta
        assert independent == 7 and decision == expected and successor == expected + 5
    states = [l.split()[1:] for l in output.splitlines() if l.startswith('STATE ')]
    by_task = {}
    for time, query, task, state in states:
        by_task.setdefault(task, []).append((int(time), state))
    for history in by_task.values():
        names = [s for _, s in history]
        assert names[:5] == ['Submitted', 'WaitingDependencies', 'WaitingResource', 'Running', 'ServiceDone']
        assert names[-1] == 'Visible' and [t for t, _ in history] == sorted(t for t, _ in history)
        if 'WaitingReturn' in names:
            assert names == ['Submitted', 'WaitingDependencies', 'WaitingResource', 'Running', 'ServiceDone', 'WaitingReturn', 'Visible']
    assertions.append('four controlled chains: independent=7ns; baseline decision/successor=60/65ns; +100ns to read, compute, or return yields 160/165ns; runtime state sequences monotonic; deterministic repeat')
    for marker in ['FAILURE PASS', 'LIFETIME PASS', 'CLEANUP PASS', 'STALL PASS', 'M2.2 PASS']:
        assert marker in output
    assertions.append('C++ assertions: private result NotReady before service and return; all/empty/duplicate inputs restore once asynchronously; root failure preserved through two successors with zero evaluation; invalid/released/permission/duplicate completions rejected; caller release retains in-flight inputs; dependency-wait cancellation, return/service failure and zero-time completion all end live=0/private=0; stalled resource reports query/task/state/dependencies/owners/pins plus bridge history')
    paths = json.loads((build / 'sst-paths.json').read_text())
    sst = run('sst-runtime', [paths['sst'], '--add-lib-path=' + str(build), ROOT / 'configs/regression/runtime.py'])
    assert 'M2.2 SST PASS probes=4 independent=7 successor=55 live=0' in sst
    assertions.append('real SST SelfLink/bridge runtime: four private-result NotReady probes at 1/19/20/49ns; service=20ns return=30ns successor=55ns; independent=7ns; all result payloads reclaimed')
    asan = ROOT / 'build/m2.2-asan'
    run('asan-configure', [cmake, '-S', ROOT, '-B', asan, '-DSST_ROOT=' + paths['prefix'], '-DNDPSIM_SANITIZE=ON'])
    run('asan-build', [cmake, '--build', asan, '--target', 'runtime_probe', '-j4'])
    run('asan-ctest', [ctest, '--test-dir', asan, '-R', '^runtime_dependencies_visibility$', '--output-on-failure'])
    assertions.append('AddressSanitizer runtime probe passes with default checks; full upstream leak audit not claimed')
    run('diff-check', ['git', 'diff', '--check'])
    run('mqsim-diff-check', ['git', '-C', 'third_party/MQSim', 'diff', '--check'])
    assert not run('sst-status', ['git', '-C', 'third_party/sst-core', 'status', '--porcelain'])
    status = 'passed'
finally:
    tracked = ['AGENTS.md', 'docs/prd.md', 'docs/archive/v0.1/implementation-plan.md', 'docs/archive/v0.1/v0.1-tasks.md',
               'docs/archive/v0.1/m0.4-minimal-contract.md', 'docs/archive/v0.1/m2.2-async-visibility.md', 'docs/evidence/m2.2/README.md',
               'src/runtime/runtime.h', 'src/runtime/runtime.cc', 'src/sst/runtime_validation.cc',
               'src/backend/event_bridge.h', 'src/content/content.h', 'tests/runtime_probe.cc',
               'configs/regression/runtime.py', 'CMakeLists.txt', 'deps.lock.json', 'tools/archive/v0.1/verify_m2_2.py']
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        status=locals().get('status', 'failed'), commands=commands, assertions=assertions,
        mqsim_source=mqsim_source_snapshot(),
        sst_commit=subprocess.check_output(['git','-C','third_party/sst-core','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        inputs_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in tracked},
        logs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.log')}
    ), indent=2) + '\n')
print(f'M2.2 {status}: {len(assertions)} acceptance groups; {OUT}')
