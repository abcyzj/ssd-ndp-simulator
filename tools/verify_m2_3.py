"""M2.3 numerical, cost, FIFO, wall-clock independence and SST acceptance."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from verify_m1_1 import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/m2.3'
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
    build = ROOT / 'build/m2.3'
    run('configure', [cmake, '-S', ROOT, '-B', build])
    run('build', [cmake, '--build', build, '-j4'])
    run('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
    output = run('operators', [build / 'operator_probe'])
    repeated = run('operators-repeat', [build / 'operator_probe'])
    stable = lambda s: '\n'.join(l for l in s.splitlines() if not l.startswith('WALL '))
    assert stable(output) == stable(repeated)
    assert 'NUMERIC distances=25,5,0 elements=9 flops=27 read=48 output=36 control_checks=4 comparisons=7 output=24' in output
    assertions.append('hand distances 25/5/0; binary64 subtraction precision 281475010265089; fixed-order sum 1e16; extreme finite FP32; tie ID order/dedup; malformed/dimension/ID/nonfinite rejection; exact work/output counts')
    rows = [l.split()[1:] for l in output.splitlines() if l.startswith('SERVICE ')]
    for slots, submitted, start, end, service, kind in rows:
        assert int(end) - int(start) == int(service)
        assert int(service) == (3 + (27+3)//4 if kind == 'distance' else 2+(3+6+2)//3)
    assert rows[:3] == rows[3:6]
    assert [int(r[3]) for r in rows[:3]] == [10,15,25]
    assert [int(r[3]) for r in rows[6:]] == [10,5,15]
    assert 'FORMULA distance=3+ceil(27/4)=10 control=2+ceil((4*2+8*3)/3)=13 doubled_rate=7' in output
    assertions.append('independent integer ceil arithmetic; weighted control cost; zero/exact/remainder work; doubled rate; shared FIFO slots=1 ends 10/15/25ns; slots=2 ends 10/5/15ns; invalid/overflow configurations rejected')
    wall = re.findall(r'WALL repeats=(\d+) slots=(\d+) ns=(\d+)', output)
    assert int(wall[1][2]) > int(wall[0][2])
    assertions.append('20000 equivalent evaluations measurably slower in wall time; identical score bytes and simulated service rows; deterministic repeat excluding wall time')
    assert 'FAILURES evaluator/queued-cancel/cost-overflow cleanup=0' in output
    assertions.append('functional failure, queued cancellation, cost overflow release slots; independent success and all result payloads reclaimed')
    paths = json.loads((build / 'sst-paths.json').read_text())
    sst = run('sst-host', [paths['sst'], '--add-lib-path='+str(build), ROOT/'configs/m2.3/host.py'])
    assert 'M2.3 SST PASS input=5 distance=11 control=13 live=0' in sst
    assertions.append('real SST SelfLink: input arrives 5ns; score service 6ns -> 11ns; control 2ns -> 13ns; private score NotReady at 6ns; live=0')
    asan = ROOT/'build/m2.3-asan'
    run('asan-configure', [cmake, '-S', ROOT, '-B', asan, '-DSST_ROOT='+paths['prefix'], '-DNDPSIM_SANITIZE=ON'])
    run('asan-build', [cmake, '--build', asan, '--target', 'operator_probe', '-j4'])
    run('asan-ctest', [ctest, '--test-dir', asan, '-R', '^operators_host_cost$', '--output-on-failure'])
    assertions.append('AddressSanitizer operator/host probe passes; no full upstream leak audit claimed')
    run('diff-check', ['git','diff','--check'])
    assert not run('sst-status', ['git','-C','third_party/sst-core','status','--porcelain'])
    assert not run('mqsim-status', ['git','-C','third_party/MQSim','status','--porcelain'])
    status = 'passed'
finally:
    tracked = ['AGENTS.md','docs/prd.md','docs/implementation-plan.md','docs/v0.1-tasks.md',
               'docs/m2.3-operators-cost.md','docs/evidence/m2.3/README.md','deps.lock.json','CMakeLists.txt',
               'src/runtime/runtime.h','src/runtime/runtime.cc','src/model/host_cpu.h','src/model/host_cpu.cc',
               'src/operators/operators.h','src/operators/operators.cc','src/sst/host_validation.cc',
               'tests/operator_probe.cc','tests/host_scenario.h','configs/m2.3/host.py','tools/verify_m2_3.py']
    (OUT/'manifest.json').write_text(json.dumps(dict(status=locals().get('status','failed'),commands=commands,
        assertions=assertions,mqsim_source=mqsim_source_snapshot(),
        sst_commit=subprocess.check_output(['git','-C','third_party/sst-core','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        inputs_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in tracked if (ROOT/p).exists()},
        logs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.log')}
    ),indent=2)+'\n')
print(f'M2.3 {status}: {len(assertions)} acceptance groups; {OUT}')
