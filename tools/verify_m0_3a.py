"""Reproduce the M0.3a E1 PoC and archive commands, logs and source identities."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import time
from verify_m1_1 import mqsim_source_snapshot, sha

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, default=ROOT/'build/m0.3a')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output, build = args.output.resolve(), args.build_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    lock = json.loads((ROOT/'deps.lock.json').read_text())
    cmake = lock['tools']['cmake']['path']
    ctest = str(Path(cmake).with_name('ctest'))
    env = dict(os.environ)
    env.update(LC_ALL='C', TZ='UTC')
    manifest = {'scope': 'M0.3a E1 only, not AC-08 or I/O equivalence',
                'platform': platform.platform(), 'status': 'running',
                'commands': [], 'assertions': [],
                'environment': {k: env[k] for k in ('PATH', 'LC_ALL', 'TZ')},
                'dependencies': lock['dependencies'], 'mqsim_source': mqsim_source_snapshot()}

    def source_hashes():
        paths = [ROOT/'AGENTS.md', ROOT/'CMakeLists.txt', ROOT/'README.md', ROOT/'deps.lock.json', Path(__file__),
                 ROOT/'tools/verify_m1_1.py', ROOT/'tools/prepare_sst.py',
                 *sorted((ROOT/'src').rglob('*')), *sorted((ROOT/'tests').glob('*.cc')),
                 *sorted((ROOT/'configs').rglob('*')),
                 ROOT/'docs/prd.md', ROOT/'docs/implementation-plan.md', ROOT/'docs/v0.1-tasks.md',
                 ROOT/'docs/m0.3a-event-bridge.md']
        return {str(p.relative_to(ROOT)): sha(p) for p in paths if p.is_file()}

    manifest['inputs_sha256'] = source_hashes()

    def save():
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')

    def check(label, condition):
        manifest['assertions'].append({'assertion': label, 'passed': bool(condition)})
        save()
        if not condition:
            raise RuntimeError(label)

    def run(name, argv, extra_env=None, timeout=600):
        argv = list(map(str, argv))
        record = {'name': name, 'argv': argv, 'command': shlex.join(argv), 'cwd': str(ROOT),
                  'extra_environment': extra_env or {}, 'timeout_seconds': timeout,
                  'stdin': 'live unwritten PIPE', 'stdout': name+'.stdout.log', 'stderr': name+'.stderr.log'}
        manifest['commands'].append(record)
        print(record['command'], flush=True)
        start = time.monotonic()
        with (output/record['stdout']).open('wb') as out, (output/record['stderr']).open('wb') as err:
            p = subprocess.Popen(argv, cwd=ROOT, env=dict(env, **(extra_env or {})),
                                 stdin=subprocess.PIPE, stdout=out, stderr=err)
            try:
                record['exit_code'] = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                record['exit_code'] = p.wait()
                record['timed_out'] = True
            finally:
                p.stdin.close()
        record['wall_seconds'] = time.monotonic()-start
        record['stdout_sha256'] = sha(output/record['stdout'])
        record['stderr_sha256'] = sha(output/record['stderr'])
        save()
        check(name+' exit 0', record['exit_code'] == 0 and not record.get('timed_out'))
        return (output/record['stdout']).read_text()+(output/record['stderr']).read_text()

    try:
        manifest['platform_commit'] = run('platform-head', ['git', 'rev-parse', 'HEAD']).strip()
        manifest['sst_commit'] = run('sst-head', ['git', '-C', 'third_party/sst-core', 'rev-parse', 'HEAD']).strip()
        check('SST remains locked and unmodified', manifest['sst_commit'] == lock['dependencies']['sst-core']['commit']
              and not run('sst-status', ['git', '-C', 'third_party/sst-core', 'status', '--porcelain']).strip())
        run('compiler', [lock['tools']['cxx']['path'], '--version'])
        run('cmake-version', [cmake, '--version'])
        run('python-version', [lock['tools']['python']['path'], '--version'])
        run('configure', [cmake, '-S', ROOT, '-B', build])
        run('build', [cmake, '--build', build, '-j2'])
        text = run('ctest', [ctest, '--test-dir', build, '--output-on-failure'])
        check('CTest 9/9', '100% tests passed out of 9' in text)
        sst = json.loads((build/'sst-paths.json').read_text())
        traces = []
        expected = [(10,10,1), (10,10,2), (10,10,3), (10,10,4),
                    (20,20,5), (20,20,6), (25,25,7), (30,30,8)]
        for repeat in (1,2):
            text = run(f'e1-{repeat}', [sst['sst'], f'--add-lib-path={build}', ROOT/'configs/m0.3a/bridge.py'])
            actual = [tuple(map(int, row)) for row in re.findall(r'E1 backend SST=(\d+) MQ=(\d+) type=(\d+)', text)]
            check(f'E1 {repeat} exact expected sequence and clock equality', actual == expected)
            check(f'E1 {repeat} external injection before 30 ns', 'E1 inject SST=20 MQ=20 next=30' in text)
            check(f'E1 {repeat} earlier reservation and Ignore stale wake',
                  'target=22' in text and 'E1 stale SST=22 generation=4 backend=20' in text)
            check(f'E1 {repeat} replaced 30 ns wake cannot advance backend',
                  'E1 stale SST=30 generation=2 backend=25' in text)
            check(f'E1 {repeat} all events drained', 'E1 PASS events=8' in text and 'stale=3 final=30 ns' in text)
            traces.append([line for line in text.splitlines() if line.startswith('E1 ')])
        check('E1 repeated trace identical', traces[0] == traces[1])
        asan = build.with_name(build.name+'-asan')
        run('asan-configure', [cmake, '-S', ROOT, '-B', asan,
            f'-DSST_ROOT={sst["prefix"]}', '-DNDPSIM_SANITIZE=ON'])
        run('asan-build', [cmake, '--build', asan, '-j2'])
        text = run('asan-ctest', [ctest, '--test-dir', asan, '--output-on-failure'], {'ASAN_OPTIONS': 'detect_leaks=0'})
        check('ASan 6/6 backend probes (leak audit excluded)', '100% tests passed out of 6' in text)
        check('Platform inputs unchanged throughout verification', source_hashes() == manifest['inputs_sha256'])
        manifest['build_artifacts_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in
            [build/'CMakeCache.txt', build/'libndpsim.so', build/'event_bridge_probe', build/'sst-paths.json']}
        snapshot = mqsim_source_snapshot()
        check('MQSim source unchanged throughout verification', snapshot == manifest['mqsim_source'])
        (output/'mqsim-source.json').write_text(json.dumps(snapshot, indent=2)+'\n')
        manifest['mqsim_source_file_sha256'] = sha(output/'mqsim-source.json')
        for label, base in [('mqsim-vs-head', 'HEAD'), ('mqsim-vs-native', snapshot['native_base_commit'])]:
            diff = subprocess.check_output(['git', '-C', str(ROOT/'third_party/MQSim'), 'diff', '--binary', base, '--'])
            (output/(label+'.diff')).write_bytes(diff)
            manifest[label+'_sha256'] = hashlib.sha256(diff).hexdigest()
        manifest['status'] = 'passed'
    except Exception as error:
        manifest['status'] = 'failed'
        manifest['error'] = str(error)
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
