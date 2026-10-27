"""Prepare a private SST installation; reuse valid installs and Make objects.

CMake calls this at configure time. No downloads, Git initialization or source
edits: autogen runs only on an archive of the commit in deps.lock.json.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def output(argv):
    return subprocess.check_output(list(map(str, argv)), text=True).strip()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--cc', required=True)
    parser.add_argument('--cxx', required=True)
    for name in ('cflags', 'cxxflags', 'ldflags', 'sdk', 'arch', 'deployment-target', 'python-config'):
        parser.add_argument('--'+name, default='')
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        raise RuntimeError('--jobs must be a positive integer')
    if ';' in args.arch:
        raise RuntimeError('SST supports a single architecture, not a universal build')
    source = ROOT/'third_party/sst-core'
    commit = json.loads((ROOT/'deps.lock.json').read_text())['dependencies']['sst-core']['commit']
    if not (source/'configure.ac').exists():
        raise RuntimeError('SST source missing; check out the pinned submodule first')
    if output(['git', '-C', source, 'rev-parse', 'HEAD']) != commit:
        raise RuntimeError('SST submodule commit differs from deps.lock.json')
    if output(['git', '-C', source, 'diff', '--binary', 'HEAD', '--']):
        raise RuntimeError('SST tracked source must be unmodified')

    # Put discovered tools and the selected Python ahead of unrelated versions.
    search = os.pathsep.join([str(Path(sys.executable).parent), '/opt/homebrew/opt/m4/bin',
        '/usr/local/opt/m4/bin', '/opt/homebrew/bin', '/usr/local/bin', os.environ.get('PATH', '')])
    tools = {}
    for name, choices in {
        'autoconf': ('autoconf',), 'automake': ('automake',), 'aclocal': ('aclocal',),
        'autoheader': ('autoheader',), 'autoreconf': ('autoreconf',),
        'libtool': ('glibtool', 'libtool'), 'libtoolize': ('glibtoolize', 'libtoolize'),
        'm4': ('m4',), 'make': ('gmake', 'make'), 'bash': ('bash',),
        'cc': (args.cc,), 'cxx': (args.cxx,),
    }.items():
        path = next((p for candidate in choices if (p := shutil.which(candidate, path=search))), None)
        if not path:
            raise RuntimeError(f'Missing build tool: {name}')
        tools[name] = {'path': path, 'sha256': sha(path)}
    python_bin = Path(sys.executable).parent
    candidates = [Path(args.python_config)] if args.python_config else [
        python_bin/(Path(sys.executable).name+'-config'), python_bin/'python3-config',
        Path(sys.base_prefix)/'bin/python3-config']
    python_config = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if not python_config or Path(output([python_config, '--prefix'])).resolve() != Path(sys.base_prefix).resolve():
        raise RuntimeError('Python interpreter and python-config must use the same prefix')
    libdir = Path(sysconfig.get_config_var('LIBDIR'))
    # Conda on macOS can report an absent .a while shipping the shared .dylib.
    libraries = [libdir/sysconfig.get_config_var('LDLIBRARY'),
                 *sorted(libdir.glob(f'libpython{sys.version_info.major}.{sys.version_info.minor}{sys.abiflags}.*'))]
    python_library = next((p for p in libraries if p.is_file()), None)
    if not python_library or not (Path(sysconfig.get_path('include'))/'Python.h').is_file():
        raise RuntimeError('Python development headers and embedding library are required')

    work, prefix = args.work_dir.resolve(), args.prefix.resolve()
    flags = {'c': args.cflags.strip(), 'cxx': args.cxxflags.strip()+' -std=c++17 -ffp-contract=off',
             'ld': args.ldflags.strip()+f' -L{shlex.quote(str(libdir))} -Wl,-rpath,{shlex.quote(str(libdir))}'}
    for flag, value in [('-arch', args.arch), ('-isysroot', args.sdk)]:
        if value:
            for key in flags:
                flags[key] += f' {flag} {shlex.quote(value)}'
    recipe = {'commit': commit, 'prefix': str(prefix), 'script_sha256': sha(Path(__file__)),
              'tools': tools, 'compiler_versions': {key: output([tools[key]['path'], '--version']) for key in ('cc', 'cxx')},
              'python': {'executable': sys.executable, 'version': sys.version,
                         'config': str(python_config), 'config_sha256': sha(python_config),
                         'library_sha256': sha(python_library)},
              'flags': flags, 'deployment_target': args.deployment_target,
              'sdk_realpath': str(Path(args.sdk).resolve()) if args.sdk else '',
              'configure_options': ['--disable-mpi', '--without-hdf5', '--without-ncurses']}
    if args.sdk and (Path(args.sdk)/'SDKSettings.json').exists():
        recipe['sdk_settings_sha256'] = sha(Path(args.sdk)/'SDKSettings.json')
    receipt_path = work/'receipt.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if receipt.get('recipe') == recipe and receipt.get('installed_files') and all(
            (prefix/name).is_file() and sha(prefix/name) == digest
            for name, digest in receipt['installed_files'].items()) and all(
            os.access(prefix/name, os.X_OK) for name in ('bin/sst', 'bin/sst-info', 'libexec/sstsim.x')):
        print(f'SST: cache hit; reusing {prefix}', flush=True)
        return

    # A toolchain/configuration change gets its own build tree. Make can safely
    # reuse objects when repairing an installation or returning to that config.
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:20]
    cache = work/key
    src, build, logs = cache/'src', cache/'build', cache/'logs'
    for directory in (src, build, logs):
        directory.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in ('CPPFLAGS', 'LIBS', 'CPATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH',
                 'PYTHONHOME', 'PYTHONPATH', 'MAKEFLAGS', 'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET'):
        env.pop(name, None)
    env.update(PATH=os.pathsep.join(dict.fromkeys([str(python_bin),
        *[str(Path(t['path']).parent) for t in tools.values()], search])),
        CC=tools['cc']['path'], CXX=tools['cxx']['path'], CFLAGS=flags['c'], CXXFLAGS=flags['cxx'],
        LDFLAGS=flags['ld'], LIBTOOL=tools['libtool']['path'], LIBTOOLIZE=tools['libtoolize']['path'],
        M4=tools['m4']['path'], CONFIG_SITE='/dev/null', PYTHONNOUSERSITE='1')
    if args.deployment_target:
        env['MACOSX_DEPLOYMENT_TARGET'] = args.deployment_target
    commands = []

    def run(name, argv, cwd):
        argv = list(map(str, argv))
        log = logs/(name+'.log')
        print(f'SST: {name}; log: {log}', flush=True)
        with log.open('wb') as stream:
            result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=subprocess.STDOUT)
        commands.append({'argv': argv, 'cwd': str(cwd), 'exit_code': result.returncode,
                         'log': str(log), 'log_sha256': sha(log)})
        write_json(cache/'commands.json', commands)
        if result.returncode:
            print('\n'.join(log.read_text(errors='replace').splitlines()[-30:]), file=sys.stderr)
            raise RuntimeError(f'SST {name} failed ({result.returncode}); see {log}')

    if not (cache/'configured.json').exists():
        archive = cache/'source.tar'
        run('archive', ['git', '-C', source, 'archive', '--format=tar', '-o', archive, commit], ROOT)
        with tarfile.open(archive) as tar:
            tar.extractall(src, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        run('autogen', [tools['bash']['path'], './autogen.sh'], src)
        run('configure', [src/'configure', '--prefix='+str(prefix), '--with-python='+str(python_config),
                          *recipe['configure_options']], build)
        write_json(cache/'configured.json', recipe)
    run('build', [tools['make']['path'], '-j'+str(args.jobs)], build)
    run('install', [tools['make']['path'], 'install'], build)
    if not all(os.access(prefix/name, os.X_OK) for name in ('bin/sst', 'bin/sst-info', 'libexec/sstsim.x')):
        raise RuntimeError('SST installation is incomplete; no successful receipt was written')
    installed = {str(p.relative_to(prefix)): sha(p) for p in sorted(prefix.rglob('*'))
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}
    write_json(receipt_path, {'recipe': recipe, 'cache_directory': str(cache),
                             'archive_sha256': sha(cache/'source.tar'), 'installed_files': installed})
    print(f'SST: ready at {prefix}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'SST preparation failed: {error}')
