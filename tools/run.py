"""Build and run the V0.1 delivery, optionally rerunning every acceptance check."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from ann_data import digest, write_json
from run_m3_1 import ROOT
from verify_m1_1 import mqsim_source_snapshot


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, default=ROOT/'build/delivery')
    p.add_argument('--output', type=Path, default=ROOT/'results/delivery')
    p.add_argument('--mode', choices=('pair', 'host', 'controller'), default='pair')
    p.add_argument('--config', type=Path, default=ROOT/'configs/m4.1/host.json')
    p.add_argument('--data', type=Path)
    p.add_argument('--sst-cache', type=Path, help='CMake SST cache; omitted uses its default')
    p.add_argument('--clean-build', action='store_true', help='Require absent build, ASan/slow builds, and any explicit SST cache')
    p.add_argument('--verify-ac', action='store_true', help='Run full M4.2 matrix, native oracle and ASan probes')
    p.add_argument('--event-log', action='store_true', help='Export JSONL and independently compare the pair ledger')
    p.add_argument('--jobs', type=int, default=2)
    a = p.parse_args()
    build, out = a.build.resolve(), a.output.resolve()
    if a.jobs < 1:
        p.error('--jobs must be positive')
    if out.exists():
        p.error('choose a new --output directory; delivery artifacts are never overwritten')
    if a.clean_build:
        candidates = [build, build.with_name(build.name+'-asan'), build.with_name(build.name+'-slow')]
        if a.sst_cache:
            candidates.append(a.sst_cache.resolve())
        if any(x.exists() for x in candidates):
            p.error('--clean-build requires new build/cache directories')
    if a.verify_ac and (platform.system(), platform.machine()) != ('Darwin', 'arm64'):
        p.error('full acceptance currently requires macOS / arm64')
    if a.verify_ac and sys.version_info < (3, 12):
        p.error('--verify-ac requires Python 3.12+ for safe native archive extraction; use the locked Python 3.13')
    cmake = shutil.which('cmake')
    if not cmake:
        p.error('cmake must be on PATH')
    out.mkdir(parents=True)
    manifest = dict(status='running', scope='M4.3 delivery', commands=[], assertions=[],
                    invocation=sys.argv, environment=dict(os=os.uname().sysname, machine=platform.machine(),
                    macos=platform.mac_ver()[0], python=sys.version, executable=sys.executable),
                    clean_build=a.clean_build, fresh_sst_cache=bool(a.clean_build and a.sst_cache))
    manifest['driver_sha256'] = digest(Path(__file__))
    before = mqsim_source_snapshot()
    manifest['mqsim_source'] = before
    def command(name, argv):
        argv = list(map(str, argv))
        print(name+': '+ ' '.join(argv), flush=True)
        with (out/(name+'.log')).open('w') as log:
            result = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        manifest['commands'].append(dict(name=name, argv=argv, exit_code=result.returncode))
        if result.returncode:
            raise RuntimeError(f'{name} exited {result.returncode}; see {out/(name+".log")}')
    try:
        command('environment', ['sw_vers'] if platform.system() == 'Darwin' else ['uname', '-a'])
        command('platform-version', ['git', 'rev-parse', 'HEAD'])
        command('platform-status', ['git', 'status', '--short'])
        command('submodules', ['git', 'submodule', 'status', '--recursive'])
        command('sst-status-before', ['git', '-C', ROOT/'third_party/sst-core', 'status', '--porcelain'])
        if (out/'sst-status-before.log').read_text().strip():
            raise RuntimeError('SST Core must be unmodified')
        configure = [cmake, '-S', ROOT, '-B', build, '-DBUILD_TESTING=ON',
                     '-DNDPSIM_SANITIZE=OFF', '-DNDPSIM_SLOW_FUNCTIONAL=OFF']
        if a.sst_cache:
            configure += ['-DNDPSIM_SST_CACHE_DIR='+str(a.sst_cache.resolve())]
        command('configure', configure)
        command('build', [cmake, '--build', build, '-j'+str(a.jobs)])
        command('ctest', [Path(cmake).with_name('ctest'), '--test-dir', build, '--output-on-failure'])
        config = json.loads(a.config.read_text())
        config['event_log'] = a.event_log or config.get('event_log', False)
        data = a.data
        for mode in (('host', 'controller') if a.mode == 'pair' else (a.mode,)):
            config['placement'] = mode
            path = out/(mode+'-config.json')
            write_json(path, config)
            argv = [sys.executable, ROOT/'tools/run_m4_1.py', '--config', path,
                    '--build', build, '--output', out/mode]
            if data:
                argv += ['--data', data]
            command(mode, argv)
            if data is None:
                data = out/mode/'data'
        if a.mode == 'pair':
            host, controller = [json.loads((out/m/'results.json').read_text()) for m in ('host', 'controller')]
            if [q['top_k'] for q in host['queries']] != [q['top_k'] for q in controller['queries']]:
                raise RuntimeError('pair top-k differs')
            manifest['assertions'].append('Same input bytes; exact top-k IDs/distances match in both modes')
            if config['event_log']:
                from compare_m3_4 import compare
                write_json(out/'comparison.json', compare(out/'host', out/'controller'))
                manifest['assertions'].append('Independent per-batch replay and complete path/byte ledger passed')
        if a.verify_ac:
            command('acceptance', [sys.executable, ROOT/'tools/verify_m4_2.py', '--build', build, '--output', out/'ac'])
            matrix = json.loads((out/'ac/ac-matrix.json').read_text())
            if matrix['status'] != 'passed' or matrix['scope'] != 'full M4.2' or set(matrix['checks']) != {f'AC-{i:02}' for i in range(1, 12)}:
                raise RuntimeError('full AC matrix missing or incomplete')
            if any(c['status'] != 'passed' for checks in matrix['checks'].values() for c in checks):
                raise RuntimeError('AC assertion failed')
            manifest['assertions'].append('Full AC-01..AC-11 rerun passed; see ac/ac-matrix.json')
        notices = out/'licenses'
        notices.mkdir()
        for source, target in [('LICENSE', 'LICENSE'),
                               ('third_party/MQSim/LICENSE', 'MQSim.txt'), ('third_party/sst-core/LICENSE.md', 'SST-Core.md')]:
            shutil.copyfile(ROOT/source, notices/target)
        command('diff-check', ['git', 'diff', '--check'])
        command('mqsim-diff-check', ['git', '-C', ROOT/'third_party/MQSim', 'diff', '--check'])
        command('sst-status-after', ['git', '-C', ROOT/'third_party/sst-core', 'status', '--porcelain'])
        if mqsim_source_snapshot() != before or (out/'sst-status-after.log').read_text().strip():
            raise RuntimeError('dependency source changed during delivery')
        manifest['assertions'].append('MQSim source hashes unchanged; SST Core clean before and after')
        manifest['status'] = 'passed'
    except BaseException as error:
        manifest['status'] = 'failed'
        manifest['error'] = str(error)
        raise
    finally:
        paths = build/'sst-paths.json'
        if paths.exists():
            manifest['sst_paths'] = json.loads(paths.read_text())
            prefix = Path(manifest['sst_paths']['prefix'])
            # Bundled SST receipts/logs accompany the delivery, not just a local path.
            cache = prefix.parent
            if (cache/'receipt.json').exists():
                target = out/'sst-build'
                target.mkdir(exist_ok=True)
                for name in ('receipt.json', 'commands.json'):
                    shutil.copyfile(cache/name, target/name)
                if (cache/'logs').exists():
                    shutil.copytree(cache/'logs', target/'logs', dirs_exist_ok=True)
        inputs = [ROOT/'AGENTS.md', ROOT/'CMakeLists.txt', ROOT/'deps.lock.json', ROOT/'README.md',
                  ROOT/'LICENSE', *sorted((ROOT/'docs').glob('*.md')),
                  *sorted((ROOT/'src').rglob('*')), *sorted((ROOT/'tools').glob('*.py')),
                  *sorted((ROOT/'tests').rglob('*')), *sorted((ROOT/'configs').rglob('*'))]
        manifest['inputs_sha256'] = {str(x.relative_to(ROOT)): digest(x) for x in inputs if x.is_file() and '__pycache__' not in x.parts and x.name != '.DS_Store'}
        manifest['artifacts_sha256'] = {str(x.relative_to(out)): digest(x) for x in out.rglob('*') if x.is_file()}
        if (build/'libndpsim.so').exists():
            manifest['element_sha256'] = digest(build/'libndpsim.so')
        write_json(out/'manifest.json', manifest)
    print('M4.3 delivery PASS', out, flush=True)


if __name__ == '__main__':
    main()
