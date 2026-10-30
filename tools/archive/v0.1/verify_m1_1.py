"""Build and verify M1.1 on the locked macOS baseline, recording real evidence.

Platform probes run with a live, unwritten stdin pipe. A terminal read
therefore hangs and fails the timeout instead of succeeding accidentally on EOF.
The unmodified native oracle retains its original EOF-based batch convention.
"""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from source_snapshot import mqsim_source_snapshot, sha
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]




def require(condition, message):
    if not condition:
        raise RuntimeError(message)




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, default=ROOT/'build/m1.1-acceptance')
    parser.add_argument('--native-build-root', type=Path, default=ROOT/'build/native-macos')
    parser.add_argument('--sst-root', type=Path, help='Reuse this SST prefix; default builds SST through CMake')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output, build, native = args.output.resolve(), args.build_root.resolve(), args.native_build_root.resolve()
    require(not output.exists(), 'Choose a new output directory')
    require(not build.exists(), 'Choose a new build directory for clean-build evidence')
    output.mkdir(parents=True)
    lock = json.loads((ROOT/'deps.lock.json').read_text())
    paths = {key: value['path'] for key, value in lock['tools'].items()}
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('SST_', 'DYLD_', 'PYTHON', 'ASAN_', 'UBSAN_')) or key in {
                'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LIBS', 'MAKEFLAGS',
                'CPATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH', 'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET'}:
            env.pop(key)
    env.update(PATH=f"{Path(paths['python']).parent}:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               LC_ALL='C', TZ='UTC', PYTHONNOUSERSITE='1',
               SDKROOT=lock['target']['sdk_path'],
               MACOSX_DEPLOYMENT_TARGET=lock['target']['deployment_target'])
    manifest = {'status': 'running', 'scope': 'M1.1 lifecycle only; no E1/E2 or AC acceptance',
                'build_root': str(build), 'commands': [], 'assertions': [],
                'environment': {key: env[key] for key in ('PATH', 'LC_ALL', 'TZ', 'PYTHONNOUSERSITE',
                    'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET')},
                'inputs_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [
                    ROOT/'AGENTS.md', ROOT/'deps.lock.json', ROOT/'CMakeLists.txt',
                    ROOT/'docs/prd-v0.1.md', ROOT/'docs/archive/v0.1/implementation-plan.md', ROOT/'docs/archive/v0.1/v0.1-tasks.md',
                    ROOT/'tools/archive/v0.1/macos_m0_2.py', Path(__file__),
                    ROOT/'tools/prepare_sst.py',
                    *sorted((ROOT/'src').rglob('*.cc')), *sorted((ROOT/'src').rglob('*.h')),
                    ROOT/'src/backend/profile.h.in', *sorted((ROOT/'tests').glob('*.cc')),
                    ROOT/'configs/regression/lifecycle.py', ROOT/'configs/native/ssdconfig.xml']}}

    def save():
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')

    def check(label, condition):
        manifest['assertions'].append({'assertion': label, 'passed': bool(condition)})
        save()
        require(condition, label)

    def run(name, argv, expected=0, timeout=60, extra_env=None, cwd=ROOT, live_stdin=True):
        argv = list(map(str, argv))
        record = {'name': name, 'argv': argv, 'command': shlex.join(argv), 'cwd': str(cwd),
                  'stdin': 'PIPE held open, no bytes written until child exits' if live_stdin else '/dev/null',
                  'timeout_seconds': timeout, 'extra_environment': extra_env or {},
                  'stdout': name+'.stdout.log', 'stderr': name+'.stderr.log'}
        manifest['commands'].append(record)
        print(f'[{name}] {shlex.join(argv)}', flush=True)
        start = time.perf_counter()
        with (output/record['stdout']).open('wb') as out, (output/record['stderr']).open('wb') as err:
            process = subprocess.Popen(argv, cwd=cwd, env=dict(env, **(extra_env or {})),
                                       stdin=subprocess.PIPE if live_stdin else subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            try:
                record['exit_code'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                record['exit_code'] = process.wait()
                record['timed_out'] = True
            finally:
                if process.stdin:
                    process.stdin.close()
        record['wall_seconds'] = time.perf_counter()-start
        record['stdout_sha256'], record['stderr_sha256'] = sha(output/record['stdout']), sha(output/record['stderr'])
        save()
        require(not record.get('timed_out'), f'{name} timed out')
        require((record['exit_code'] == expected) if expected is not None else record['exit_code'] > 0,
                f"{name}: unexpected exit {record['exit_code']}")
        return (output/record['stdout']).read_text(errors='replace') + (output/record['stderr']).read_text(errors='replace')

    try:
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Requires Darwin arm64')
        require(Path(sys.executable).resolve() == Path(paths['python']).resolve(), 'Use locked Python')
        manifest['platform_commit'] = run('platform-head', ['git', 'rev-parse', 'HEAD']).strip()
        run('platform-status', ['git', 'status', '--short'])
        for name, dependency in lock['dependencies'].items():
            head = run(name+'-head', ['git', '-C', ROOT/dependency['path'], 'rev-parse', 'HEAD']).strip()
            diff = run(name+'-diff', ['git', '-C', ROOT/dependency['path'], 'diff', '--binary', 'HEAD', '--'])
            if name == 'MQSim':
                run('MQSim-native-base', ['git', '-C', ROOT/dependency['path'], 'cat-file', '-e',
                                         dependency['commit']+'^{commit}'])
                snapshot = mqsim_source_snapshot()
                check('MQSim working source identity recorded', snapshot['head_commit'] == head)
                manifest['mqsim_source'] = snapshot
                (output/'mqsim-source-receipt.json').write_text(json.dumps(snapshot, indent=2)+'\n')
            else:
                check(name+' pinned commit', head == dependency['commit'])
                check(name+' pristine tracked source', not diff.strip())
        # Reuse the unchanged M0.2 receipt. Do not require time(1) RSS collection,
        # which is unavailable under some macOS sandbox profiles.
        receipt = json.loads((native/'build-receipt.json').read_text())
        check('Native build lock unchanged', receipt['lock_sha256'] == sha(ROOT/'deps.lock.json'))
        check('Native build binaries unchanged', all(sha(p) == digest for p, digest in receipt['binaries'].items()))
        check('Native tool executables unchanged', all(sha(tool['path']) == tool['sha256'] for tool in receipt['tools'].values()))
        check('Native Python library unchanged', sha(Path(lock['tools']['python_config']['prefix'])/'lib/libpython3.13.dylib') == receipt['python_library_sha256'])
        dependency = lock['dependencies']['MQSim']
        archive = output/'native-MQSim.tar'
        run('native-source-archive', ['git', '-C', ROOT/dependency['path'], 'archive', '--format=tar',
                                     '-o', archive, dependency['commit']])
        check('Native source archive ignores development edits',
              sha(archive) == receipt['dependencies']['MQSim']['archive_sha256'])
        run('system', ['/usr/bin/sw_vers'])
        run('compiler', [paths['cxx'], '--version'])
        run('cmake-version', [paths['cmake'], '--version'])
        native_results = []
        for repeat in (1, 2):
            directory = output/f'native-{repeat}'
            directory.mkdir()
            for fixture in sorted((ROOT/'configs/native').glob('*')):
                shutil.copy2(fixture, directory/fixture.name)
            text = run(f'native-{repeat}', [native/'src/MQSim/MQSim', '-i', 'ssdconfig.xml', '-w', 'workload.xml'],
                       cwd=directory, live_stdin=False)
            check(f'Native {repeat}: generated/serviced 4', 'total requests generated: 4 total requests serviced:4' in text)
            result = directory/'workload_scenario_1.xml'
            flow = ET.parse(result).getroot().find('.//Host.IO_Flow')
            require(flow is not None, 'Native flow results missing')
            values = {element.tag: element.text for element in flow}
            check(f'Native {repeat}: 4 reads / 13312 B / zero writes', all(float(values[key]) == expected for key, expected in {
                'Request_Count': 4, 'Read_Request_Count': 4, 'Write_Request_Count': 0,
                'Bytes_Transferred': 13312, 'Bytes_Transferred_Read': 13312, 'Bytes_Transferred_Write': 0}.items()))
            check(f'Native {repeat}: positive completion delay', float(values['Min_End_to_End_Request_Delay']) > 0)
            native_results.append(sha(result))
        check('Native XML results repeat exactly', native_results[0] == native_results[1])
        manifest['native_result_sha256'] = native_results
        shutil.copy2(native/'build-receipt.json', output/'native-build-receipt.json')
        manifest['oracle_receipt_sha256'] = sha(native/'build-receipt.json')
        manifest['oracle_binary_sha256'] = sha(native/'src/MQSim/MQSim')
        common = [f'-DCMAKE_CXX_COMPILER={paths["cxx"]}',
                  '-DCMAKE_OSX_ARCHITECTURES=arm64', f'-DCMAKE_OSX_SYSROOT={env["SDKROOT"]}',
                  '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']
        if args.sst_root:
            common.append('-DSST_ROOT='+str(args.sst_root.resolve()))
        run('configure', [paths['cmake'], '-S', ROOT, '-B', build,
                         '-DCMAKE_BUILD_TYPE=RelWithDebInfo', *common], timeout=600)
        run('build', [paths['cmake'], '--build', build, '-j2'], timeout=600)
        sst_paths = json.loads((build/'sst-paths.json').read_text())
        manifest['sst_paths'] = sst_paths
        run('ctest', [Path(paths['cmake']).with_name('ctest'), '--test-dir', build, '--output-on-failure'])
        commands = json.loads((build/'compile_commands.json').read_text())
        source = ROOT/'third_party/MQSim/src'
        mqsim = [item for item in commands if Path(item['file']).is_relative_to(source)]
        check('MQSim compiles directly from third_party/MQSim', bool(mqsim) and
              not any('/mqsim-source/' in item['file'] for item in commands))
        check('MQSim source target excludes main.cpp', bool(mqsim) and all(not item['file'].endswith('/main.cpp') for item in mqsim))
        check('All MQSim objects PIC / C++17, without constructor alias workaround', all(
            '-fPIC' in item['command'] and '-std=c++17' in item['command'] and
            'mconstructor-aliases' not in item['command'] for item in mqsim))
        module = build/'libndpsim.so'
        check('Element module arm64', run('module-arch', ['/usr/bin/lipo', '-archs', module]).strip() == 'arm64')
        run('module-linkage', ['/usr/bin/otool', '-L', module])
        symbols = run('library-symbols', ['/usr/bin/nm', '-g', build/'libmqsim_pic.a'])
        check('Library has engine initialization, no main', 'Initialize_simulation' in symbols and
              not any(line.endswith(' T _main') for line in symbols.splitlines()))
        info = run('element-info', [sst_paths['sst_info'], '--add-lib-path='+str(build), 'ndpsim.System'])
        check('SST ELI library and component load', 'ELEMENT LIBRARY 0 = ndpsim' in info and 'Component 0: System' in info)
        sst = [sst_paths['sst'], '--add-lib-path='+str(build)]
        config = ROOT/'configs/regression/lifecycle.py'
        for repeat in (1, 2):
            text = run('sst-lifecycle-'+str(repeat), [*sst, config])
            check('SST lifecycle phases and time, repeat '+str(repeat), all(marker in text for marker in [
                'init phase 0', 'setup initialized backend at 0 ns',
                'finish SST=1 ns MQSim=0 ns; device destroyed', 'simulated time: 1 ns']))
        cases = [
            ('zero-device', ['--device-count', '0'], 'device_count must be 1'),
            ('multi-device', ['--device-count', '2'], 'device_count must be 1'),
            ('bad-integer', ['--device-count', 'garbage'], 'device_count must be 1'),
            ('bad-profile', ['--profile', 'unsupported'], 'profile must be native-small'),
            ('duplicate-component', ['--components', '2'], 'one MQSim device lifetime'),
            ('unknown-param', ['--unknown-param'], 'Unknown ndpsim.System parameter')]
        for name, params, diagnostic in cases:
            text = run('reject-'+name, [*sst, config, '--', *params], expected=None, timeout=15)
            check(name+' diagnostic, nonzero exit, open stdin', diagnostic in text and 'device destroyed' not in text)
        text = run('reject-multithread', [*sst, '--num-threads=2', config], expected=None, timeout=15)
        check('Reject more than one thread', 'requires one rank and one thread' in text)
        for mode in ('error', 'invalid-mqsim'):
            text = run('open-stdin-'+mode, [build/'lifecycle_probe', mode], timeout=15)
            check(mode+' does not read stdin', 'PASS:' in text)
        sanitized = build/'asan'
        run('asan-configure', [paths['cmake'], '-S', ROOT, '-B', sanitized, '-DNDPSIM_SANITIZE=ON',
                              '-DCMAKE_BUILD_TYPE=Debug', *common, '-DSST_ROOT='+sst_paths['prefix']])
        run('asan-build', [paths['cmake'], '--build', sanitized, '--target', 'lifecycle_probe', '-j2'], timeout=600)
        for mode in ('normal', 'early', 'error', 'invalid-mqsim'):
            text = run('asan-'+mode, [sanitized/'lifecycle_probe', *([] if mode == 'normal' else [mode])],
                       extra_env={'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1'})
            check('ASan '+mode, 'PASS:' in text and 'ERROR: AddressSanitizer' not in text)
        manifest['binaries_sha256'] = {str(p): sha(p) for p in [module, build/'libmqsim_pic.a',
            build/'lifecycle_probe', sanitized/'lifecycle_probe']}
        check('MQSim source unchanged throughout build and tests', mqsim_source_snapshot() == snapshot)
        shutil.copy2(build/'compile_commands.json', output/'compile_commands.json')
        check('Oracle binary unchanged after platform builds', sha(native/'src/MQSim/MQSim') == manifest['oracle_binary_sha256'])
        manifest['status'] = 'passed'
    except Exception as error:
        manifest['status'] = 'failed'
        manifest['failure'] = str(error)
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
