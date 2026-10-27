#!/opt/homebrew/Caskroom/miniconda/base/bin/python3.13
"""Build and check the locked, unmodified macOS native M0.2 baselines.

No downloads, submodule initialization, source patches, or element loading.
MQSim development edits are allowed: the oracle always uses git archive of the
locked native commit, never the MQSim working tree.
Use a new --build-root for a clean rebuild; --verify-only reuses its binaries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, default=ROOT / 'build/native-macos')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    build = args.build_root.resolve()
    output = args.output.resolve()
    require(not output.exists(), f'Output already exists: {output}; choose a new run directory')
    output.mkdir(parents=True)
    lock = json.loads((ROOT / 'deps.lock.json').read_text())
    paths = {name: entry['path'] for name, entry in lock['tools'].items()}
    env = dict(os.environ)
    # Do not inherit foreign compiler/Python/module settings from an interactive shell.
    for key in list(env):
        if key.startswith(('SST_', 'DYLD_', 'PYTHON')) or key in {
            'CC', 'CXX', 'CPPFLAGS', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'LIBS',
            'CPATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH', 'CONFIG_SITE', 'MAKEFLAGS',
            'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET', 'ARCHFLAGS'}:
            env.pop(key)
    env.update(PATH=f"{Path(paths['python']).parent}:/opt/homebrew/opt/m4/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               LC_ALL='C', TZ='UTC', PYTHONNOUSERSITE='1', CC=paths['cc'], CXX=paths['cxx'],
               CFLAGS=lock['build']['cflags'], CXXFLAGS=lock['build']['cxxflags'],
               LDFLAGS=f"-arch arm64 -L{lock['tools']['python_config']['prefix']}/lib "
                       f"-Wl,-rpath,{lock['tools']['python_config']['prefix']}/lib",
               LIBTOOL=paths['libtool'], LIBTOOLIZE='/opt/homebrew/bin/glibtoolize', M4=paths['m4'],
               CONFIG_SITE='/dev/null', SDKROOT=lock['target']['sdk_path'],
               MACOSX_DEPLOYMENT_TARGET=lock['target']['deployment_target'])
    manifest = {'status': 'running', 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'build_root': str(build), 'output': str(output), 'commands': [],
                'environment': {k: env[k] for k in ('PATH', 'LC_ALL', 'TZ', 'CC', 'CXX', 'CFLAGS',
                    'CXXFLAGS', 'LDFLAGS', 'LIBTOOL', 'LIBTOOLIZE', 'M4', 'CONFIG_SITE', 'PYTHONNOUSERSITE',
                    'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET')},
                'input_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [
                    ROOT/'AGENTS.md', ROOT/'docs/prd.md', ROOT/'docs/implementation-plan.md',
                    ROOT/'docs/v0.1-tasks.md', ROOT/'deps.lock.json', Path(__file__),
                    *sorted((ROOT/'configs/native').glob('*'))]}}

    def save():
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

    def run(name, argv, cwd=ROOT, timeout=900):
        """Record argv, cwd, exit, output, elapsed and Darwin time(1) RSS."""
        stdout = output / f'{name}.stdout.log'
        stderr = output / f'{name}.stderr.log'
        command = ['/usr/bin/time', '-l', *map(str, argv)]
        record = {'name': name, 'argv': command, 'command': shlex.join(command) + ' < /dev/null',
                  'cwd': str(cwd), 'timeout_seconds': timeout, 'stdin': '/dev/null',
                  'stdout': stdout.name, 'stderr': stderr.name}
        manifest['commands'].append(record)
        print(f'[{name}] {shlex.join(list(map(str, argv)))}', flush=True)
        start = time.perf_counter()
        with stdout.open('wb') as out, stderr.open('wb') as err:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            try:
                record['exit_code'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                record['exit_code'] = process.wait()
                record['timed_out'] = True
        record['wall_seconds'] = time.perf_counter() - start
        errors = stderr.read_text(errors='replace')
        rss = re.search(r'(\d+)\s+maximum resident set size', errors)
        record['peak_rss_bytes'] = int(rss[1]) if rss else None
        record['stdout_sha256'] = sha(stdout)
        record['stderr_sha256'] = sha(stderr)
        save()
        require(record['exit_code'] == 0, f'{name} failed: see {stderr}')
        return stdout.read_text(errors='replace')

    try:
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Requires native Darwin arm64')
        require(Path(sys.executable).resolve() == Path(paths['python']).resolve(), 'Run using locked Python interpreter')
        manifest['platform_commit'] = run('platform-commit', ['/usr/bin/git', 'rev-parse', 'HEAD']).strip()
        run('platform-status', ['/usr/bin/git', 'status', '--short'])
        manifest['system'] = run('system', ['/usr/bin/sw_vers'])
        require(re.search(r'ProductVersion:\s+(\S+)', manifest['system'])[1] == lock['target']['macos'],
                'macOS version differs from lock')
        run('resources', ['/usr/sbin/sysctl', 'hw.ncpu', 'hw.memsize', 'hw.model'])
        run('vm-stat', ['/usr/bin/vm_stat'])
        run('disk-space', ['/bin/df', '-k', str(ROOT)])
        sdk = run('sdk-version', ['/usr/bin/xcrun', '--show-sdk-version']).strip()
        require(sdk == lock['target']['sdk'], 'SDK version differs from lock')
        run('sdk-path', ['/usr/bin/xcrun', '--show-sdk-path'])
        manifest['tools'] = {}
        for name, tool in lock['tools'].items():
            version = run('tool-' + name, [tool['path'], '--prefix' if name == 'python_config' else '--version'])
            require(tool.get('version_contains', tool.get('prefix')) in version, f'{name} differs from lock')
            manifest['tools'][name] = {'version': version.strip(), 'path': tool['path'],
                                        'sha256': sha(tool['path'])}
        prefix = lock['tools']['python_config']['prefix']
        run('python-pair', [paths['python'], '-c',
            f'import sys,sysconfig,platform; assert sys.prefix == {prefix!r}; assert platform.machine()=="arm64"; '
            'print(sys.version); print(sys.prefix); print(sysconfig.get_config_var("LIBDIR"))'])
        manifest['python_library_sha256'] = sha(Path(prefix)/'lib/libpython3.13.dylib')
        manifest['dependencies'] = {}
        manifest['source_checkouts'] = {}
        for name, dep in lock['dependencies'].items():
            source = ROOT / dep['path']
            head = run(name+'-head', ['/usr/bin/git', '-C', source, 'rev-parse', 'HEAD']).strip()
            if name != 'MQSim':
                require(head == dep['commit'], f'{name}: commit differs from lock')
            origin = run(name+'-origin', ['/usr/bin/git', '-C', source, 'remote', 'get-url', 'origin']).strip()
            require(origin == dep['url'], f'{name}: origin differs from lock')
            gitlink = run(name+'-gitlink', ['/usr/bin/git', 'ls-files', '--stage', dep['path']]).split()
            require(gitlink[:2] == ['160000', head], f'{name}: index gitlink differs from lock')
            diff = run(name+'-diff', ['/usr/bin/git', '-C', source, 'diff', '--binary', 'HEAD', '--'])
            if name != 'MQSim':
                require(not diff, f'{name}: requires pristine tracked source')
            # The exported commit, not the development checkout, is the oracle.
            run(name+'-native-base', ['/usr/bin/git', '-C', source, 'cat-file', '-e', dep['commit']+'^{commit}'])
            require(not dep['patches'] and dep['patch_sha256'] == hashlib.sha256(b'').hexdigest(),
                    'Native oracle must not apply patches')
            manifest['source_checkouts'][name] = {'head_commit': head,
                'working_tree_diff_sha256': hashlib.sha256(diff.encode()).hexdigest(),
                'used_for_native_build': False, 'native_archive_commit': dep['commit']}
            manifest['dependencies'][name] = dict(dep)

        prefix_sst = build / 'install/sst'
        mqsim = build / 'src/MQSim/MQSim'
        receipt = build / 'build-receipt.json'
        if not args.verify_only:
            require(not build.exists(), f'Build root exists: {build}; use --verify-only or a new directory')
            (build/'src').mkdir(parents=True)
            for name, dep in lock['dependencies'].items():
                archive = build / (name + '.tar')
                run(name+'-archive', ['/usr/bin/git', '-C', ROOT/dep['path'], 'archive', '--format=tar',
                                      '-o', archive, dep['commit']])
                manifest['dependencies'][name]['archive_sha256'] = sha(archive)
                destination = build/'src'/name
                destination.mkdir()
                with tarfile.open(archive) as tar:
                    tar.extractall(destination, filter='data')
            run('sst-autogen', ['/bin/bash', './autogen.sh'], build/'src/sst-core')
            sst_build = build/'sst-build'
            sst_build.mkdir()
            run('sst-configure', [build/'src/sst-core/configure', '--prefix='+str(prefix_sst),
                '--with-python='+paths['python_config'], *lock['build']['sst_configure']], sst_build)
            run('sst-build', [paths['make'], '-j'+str(lock['build']['jobs'])], sst_build, timeout=1800)
            run('sst-install', [paths['make'], 'install'], sst_build)
            shutil.copy2(sst_build/'config.log', output/'sst-config.log')
            run('mqsim-build', [paths['make'], '-j'+str(lock['build']['jobs']), 'CC='+paths['cxx'],
                'LD='+paths['cxx'], 'CC_FLAGS='+lock['build']['cxxflags']+' '+lock['build']['mqsim_extra_cxxflags']],
                build/'src/MQSim')
            shutil.copy2(ROOT/'deps.lock.json', build/'deps.lock.json')
            binaries = [prefix_sst/'bin/sst', prefix_sst/'bin/sst-config', prefix_sst/'libexec/sstsim.x', mqsim]
            receipt.write_text(json.dumps({'lock_sha256': sha(ROOT/'deps.lock.json'),
                'tools': manifest['tools'], 'dependencies': manifest['dependencies'],
                'python_library_sha256': manifest['python_library_sha256'],
                'binaries': {str(p): sha(p) for p in binaries}}, indent=2) + '\n')
        else:
            require((build/'deps.lock.json').read_bytes() == (ROOT/'deps.lock.json').read_bytes(),
                    'Build lock differs; perform a clean rebuild')
            built = json.loads(receipt.read_text())
            require(built['tools'] == manifest['tools'], 'Tool executables changed since build')
            require(built['python_library_sha256'] == manifest['python_library_sha256'], 'Python library changed')
            require(all(sha(p) == digest for p, digest in built['binaries'].items()), 'Built binary changed')

        shutil.copy2(receipt, output/'build-receipt.json')
        manifest['binaries'] = json.loads(receipt.read_text())['binaries']
        for name, binary in [('sst', prefix_sst/'libexec/sstsim.x'), ('mqsim', mqsim)]:
            arch = run(name+'-architecture', ['/usr/bin/lipo', '-archs', binary]).strip()
            require(arch == 'arm64', f'{name}: non-arm64 binary')
            run(name+'-linkage', ['/usr/bin/otool', '-L', binary])
        symbols = run('mqsim-symbols', ['/usr/bin/nm', '-g', mqsim])
        aliases = [re.search(r'^(\w+) T __ZN15Host_Components12IO_Flow_BaseD'+kind+r'Ev$',
                             symbols, re.MULTILINE) for kind in ('1', '2')]
        require(all(aliases) and aliases[0][1] == aliases[1][1], 'MQSim constructor alias option ineffective')
        version = run('sst-version', [prefix_sst/'bin/sst', '--version'], timeout=60)
        require('16.0.0' in version, 'Unexpected SST version')
        help_text = run('sst-usage', [prefix_sst/'bin/sst', '--usage'], timeout=60)
        help_text += (output/'sst-usage.stderr.log').read_text()
        require('Usage' in help_text or 'usage' in help_text, 'Missing SST help')
        run('sst-help', [prefix_sst/'bin/sst', '--help=timebase'], timeout=60)
        run('sst-config-cxx', [prefix_sst/'bin/sst-config', '--CXX'])
        run('sst-config-flags', [prefix_sst/'bin/sst-config', '--ELEMENT_CXXFLAGS'])

        # Exercise C++17 and the selected Python headers/library/runtime together.
        probe_source = output/'python-embed.cc'
        probe_source.write_text('#include <Python.h>\n#include <optional>\n'
            'int main() { std::optional<int> v=17; static_assert(__cplusplus>=201703L); '
            'Py_Initialize(); int rc=PyRun_SimpleString("import sys; print(sys.version)"); '
            'return Py_FinalizeEx()<0 ? 2 : rc + (*v-17); }\n')
        includes = run('python-includes', [paths['python_config'], '--includes']).strip()
        libs = run('python-embed-flags', [paths['python_config'], '--ldflags', '--embed']).strip()
        probe = build/'python-embed'
        run('python-embed-build', [paths['cxx'], *shlex.split(env['CXXFLAGS']), *shlex.split(includes),
            probe_source, '-o', probe, *shlex.split(env['LDFLAGS']), *shlex.split(libs)])
        run('python-embed-run', [probe], timeout=60)
        cmake_source = output/'cmake-probe'
        cmake_source.mkdir()
        (cmake_source/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.22)\n'
            'project(SSTExportCheck LANGUAGES CXX)\n'
            'find_package(SST 16.0.0 REQUIRED CONFIG PATHS "${SST_ROOT}/lib/cmake/SST" NO_DEFAULT_PATH)\n'
            'if(NOT TARGET SST::SSTCore OR NOT TARGET SST::SST)\n'
            '  message(FATAL_ERROR "Missing imported targets")\nendif()\n'
            'get_target_property(executable SST::SST IMPORTED_LOCATION)\n'
            'message(STATUS "SST executable: ${executable}")\n')
        run('sst-cmake-export', [paths['cmake'], '-S', cmake_source, '-B', build/'cmake-probes'/output.name,
            '-DSST_ROOT='+str(prefix_sst), '-DCMAKE_OSX_ARCHITECTURES=arm64',
            '-DCMAKE_OSX_SYSROOT='+env['SDKROOT']])

        results = []
        for repeat in (1, 2):
            run_dir = output / f'native-{repeat}'
            run_dir.mkdir()
            for fixture in (ROOT/'configs/native').glob('*'):
                shutil.copy2(fixture, run_dir/fixture.name)
            text = run(f'native-{repeat}', [mqsim, '-i', 'ssdconfig.xml', '-w', 'workload.xml'], run_dir, timeout=60)
            require(re.search(r'total requests generated: 4 total requests serviced:4\b', text),
                    'Not all four requests completed')
            require('ERROR' not in text.upper(), 'Native simulator reported an error')
            result_file = run_dir/'workload_scenario_1.xml'
            root = ET.parse(result_file).getroot()
            flow = root.find('.//Host.IO_Flow')
            require(flow is not None, 'Missing native flow result')
            attrs = {element.tag: element.text for element in flow}
            for key, expected in {'Request_Count': 4, 'Read_Request_Count': 4, 'Write_Request_Count': 0,
                                  'Bytes_Transferred': 13312, 'Bytes_Transferred_Read': 13312,
                                  'Bytes_Transferred_Write': 0}.items():
                require(float(attrs[key]) == expected, f'{key}: {attrs[key]} != {expected}')
            require(float(attrs['Min_End_to_End_Request_Delay']) > 0, 'Nonpositive service delay')
            # Native XML is deterministic and contains no wall-clock timestamp.
            results.append({'xml_sha256': sha(result_file), 'flow': attrs})
        require(results[0] == results[1], 'Fresh-process native results differ')
        manifest['native_results'] = results
        manifest['assertions'] = ['locked toolchain and initialized gitlinks match', 'dependency patches empty',
            'SST and MQSim arm64', 'SST version/help/config exit zero',
            'native abstract complete/base destructor symbols alias',
            'C++17 Python embedding compiles, links and runs', 'CMake 4 imports SST exported targets',
            '4 reads generated and serviced per process', '13312 read bytes, zero write bytes',
            'positive native completion delays', 'two fresh-process native XML outputs identical']
        manifest['status'] = 'passed'
    except Exception as exc:
        manifest['status'] = 'failed'
        manifest['error'] = str(exc)
        raise
    finally:
        save()
    print('M0.2 checks passed: ' + str(output/'manifest.json'))


if __name__ == '__main__':
    main()
