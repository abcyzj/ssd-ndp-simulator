"""Rebuild the locked M0 builder from local archives; never downloads or installs globally."""
import argparse
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_m0 import ROOT, measure, sha, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archives', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True, help='unused local dependency build directory')
    p.add_argument('--output', type=Path, required=True, help='unused evidence directory')
    a = p.parse_args()
    if a.work.exists() or a.output.exists():
        p.error('work/output must not exist')
    lock = json.loads((ROOT/'configs/benchmarks/m0-builder.lock.json').read_text())
    for name, record in lock['archives'].items():
        archive = a.archives/name
        if archive.stat().st_size != record['bytes'] or sha(archive) != record['sha256']:
            raise ValueError('locked archive mismatch: '+name)
    a.work.mkdir(parents=True)
    a.output.mkdir(parents=True)
    src = a.work.resolve()/'src'
    src.mkdir()
    for name in lock['archives']:
        with tarfile.open(a.archives/name) as archive:
            archive.extractall(src, filter='data')
    (src/'cmake-20.1.8.src').rename(src/'cmake')
    prefix = a.work.resolve()/'openmp'
    omp_build = a.work.resolve()/'openmp-build'
    faiss_build = a.work.resolve()/'faiss-build'
    sdk = '/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk'
    if sha(Path(sdk)/'SDKSettings.json') != lock['system']['sdk_settings_sha256']:
        raise ValueError('SDK differs from lock; evaluate and explicitly relock, do not silently reuse')
    argv_list = [
        ['cmake', '-S', str(src/'openmp-20.1.8.src'), '-B', str(omp_build), '-DCMAKE_BUILD_TYPE=Release',
         '-DCMAKE_INSTALL_PREFIX='+str(prefix), '-DCMAKE_OSX_SYSROOT='+sdk,
         '-DOPENMP_ENABLE_LIBOMPTARGET=OFF', '-DOPENMP_ENABLE_OMPT_TOOLS=OFF', '-DLIBOMP_ENABLE_SHARED=ON'],
        ['cmake', '--build', str(omp_build), '-j2'],
        ['cmake', '--install', str(omp_build)],
        ['cmake', '-S', str(ROOT/'tools/m0'), '-B', str(faiss_build), '-DCMAKE_BUILD_TYPE=Release',
         '-DNDPSIM_FAISS_SOURCE='+str(src/('faiss-'+lock['faiss_commit'])),
         '-DCMAKE_OSX_SYSROOT='+sdk, '-DBLA_VENDOR=Apple',
         '-DOpenMP_C_FLAGS=-Xpreprocessor -fopenmp', '-DOpenMP_CXX_FLAGS=-Xpreprocessor -fopenmp',
         '-DOpenMP_C_LIB_NAMES=omp', '-DOpenMP_CXX_LIB_NAMES=omp',
         '-DOpenMP_omp_LIBRARY='+str(prefix/'lib/libomp.dylib'),
         '-DOpenMP_C_INCLUDE_DIR='+str(prefix/'include'), '-DOpenMP_CXX_INCLUDE_DIR='+str(prefix/'include')],
        ['cmake', '--build', str(faiss_build), '--target', 'm0_faiss_probe', 'ndpsim_index_builder', '-j2'],
    ]
    runs = []
    for i, argv in enumerate(argv_list):
        result = measure(argv, a.output/f'command-{i}.json', 1800)
        runs.append(result)
        write(a.output/'manifest.json', dict(lock_sha256=sha(ROOT/'configs/benchmarks/m0-builder.lock.json'),
                                           commands=runs, passed=all(r['passed'] for r in runs)))
        if not result['passed']:
            return 1
    write(a.output/'artifacts.json', {str(f): sha(f) for f in
                                    (faiss_build/'m0_faiss_probe', prefix/'lib/libomp.dylib')})
    write(a.output/'index-builder-receipt.json', dict(
        executable=str(faiss_build/'ndpsim_index_builder'),
        executable_sha256=sha(faiss_build/'ndpsim_index_builder'),
        adapter_sha256=sha(ROOT/'tools/offline/index_builder.cc'),
        lock_sha256=sha(ROOT/'configs/benchmarks/m0-builder.lock.json'),
        build_manifest_sha256=sha(a.output/'manifest.json'),
        openmp_sha256=sha(prefix/'lib/libomp.dylib')))
    print(faiss_build/'m0_faiss_probe')
    return 0


if __name__ == '__main__':
    sys.exit(main())
