"""Archive M0.3b E2 checks, ASan probes, and an independent native read oracle."""

# Historical stage runner; shared maintained helpers live in tools/.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET
from source_snapshot import mqsim_source_snapshot, sha

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, default=ROOT/'build/m0.3b')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build, output = args.build_root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    lock = json.loads((ROOT/'deps.lock.json').read_text())
    cmake = lock['tools']['cmake']['path']
    ctest = str(Path(cmake).with_name('ctest'))
    env = dict(os.environ, LC_ALL='C', TZ='UTC')
    manifest = dict(scope='M0.3b E2 PoC only; not AC-06/08 or calibrated performance',
                    status='running', platform=platform.platform(), commands=[], assertions=[],
                    environment={k: env[k] for k in ('PATH','LC_ALL','TZ')},
                    mqsim_source=mqsim_source_snapshot(), dependencies=lock['dependencies'])

    def inputs():
        paths = [ROOT/'AGENTS.md', ROOT/'CMakeLists.txt', ROOT/'deps.lock.json', Path(__file__),
                 ROOT/'tools/archive/v0.1/verify_m1_1.py', ROOT/'tools/prepare_sst.py',
                 *sorted((ROOT/'src').rglob('*')), *sorted((ROOT/'tests').glob('*.cc')),
                 *sorted((ROOT/'configs').rglob('*')), ROOT/'docs/prd-v0.1.md',
                 ROOT/'docs/archive/v0.1/implementation-plan.md', ROOT/'docs/archive/v0.1/v0.1-tasks.md', ROOT/'docs/archive/v0.1/m0.3b-local-read.md']
        return {str(p.relative_to(ROOT)): sha(p) for p in paths if p.is_file()}

    manifest['inputs_sha256'] = inputs()

    def save():
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')

    def check(label, passed):
        manifest['assertions'].append(dict(assertion=label, passed=bool(passed)))
        save()
        if not passed:
            raise RuntimeError(label)

    def run(name, argv, cwd=ROOT, extra_env=None, timeout=600):
        argv = list(map(str,argv))
        record = dict(name=name, argv=argv, command=shlex.join(argv), cwd=str(cwd),
                      stdin='/dev/null', extra_environment=extra_env or {}, timeout_seconds=timeout,
                      stdout=name+'.stdout.log', stderr=name+'.stderr.log')
        manifest['commands'].append(record)
        print(record['command'], flush=True)
        start = time.monotonic()
        with (output/record['stdout']).open('wb') as out, (output/record['stderr']).open('wb') as err:
            try:
                result = subprocess.run(argv, cwd=cwd, env=dict(env, **(extra_env or {})),
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout)
                record['exit_code'] = result.returncode
            except subprocess.TimeoutExpired:
                record.update(exit_code=-1, timed_out=True)
        record['wall_seconds'] = time.monotonic()-start
        record['stdout_sha256'] = sha(output/record['stdout'])
        record['stderr_sha256'] = sha(output/record['stderr'])
        check(name+' exit 0', record['exit_code'] == 0)
        return (output/record['stdout']).read_text()+(output/record['stderr']).read_text()

    try:
        manifest['platform_commit'] = run('platform-head',['git','rev-parse','HEAD']).strip()
        sst_head = run('sst-head',['git','-C','third_party/sst-core','rev-parse','HEAD']).strip()
        check('SST locked and unmodified', sst_head == lock['dependencies']['sst-core']['commit']
              and not run('sst-status',['git','-C','third_party/sst-core','status','--porcelain']).strip())
        run('compiler',[lock['tools']['cxx']['path'],'--version'])
        run('cmake-version',[cmake,'--version'])
        run('python-version',[lock['tools']['python']['path'],'--version'])
        run('configure',[cmake,'-S',ROOT,'-B',build])
        run('build',[cmake,'--build',build,'-j2'])
        text = run('ctest',[ctest,'--test-dir',build,'--output-on-failure'])
        check('CTest 13/13', '100% tests passed out of 13' in text)
        reverse = run('reverse',[build/'local_read_probe'])
        forward = run('forward',[build/'local_read_probe','forward'])
        reverse_repeat = run('reverse-repeat',[build/'local_read_probe'])
        check('reverse sequence repeats exactly', reverse == reverse_repeat)
        for name, text, pages in [('reverse',reverse,[1,0,1,0]),('forward',forward,[0,1,0,1])]:
            check(name+' transaction order', [int(p) for p in re.findall(r'E2 transaction token=\d+ page=(\d+)',text)] == pages)
            check(name+' publish only after both transactions and cleanup',
                  re.findall(r'E2 publish token=\d+ time=(\d+)',text) == ['30','70']
                  and 'E2 PASS requests=2 transactions=4 completions=2 releases=2 vector_d2h=0' in text)
        text = run('device',[build/'local_read_device_probe'])
        check('real FTL/NAND completion without PCIe', 'E2 NAND PASS completions=1 no PCIe attachment' in text)
        run('unsupported',[build/'local_read_probe','unsupported'])
        sanitized = Path(str(build)+'-asan')
        run('asan-configure',[cmake,'-S',ROOT,'-B',sanitized,'-DNDPSIM_SANITIZE=ON'])
        run('asan-build',[cmake,'--build',sanitized,'--target','local_read_probe','local_read_device_probe',
                          'event_bridge_probe','lifecycle_probe','-j2'])
        text = run('asan-ctest',[ctest,'--test-dir',sanitized,'--output-on-failure'],
                   extra_env={'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1'})
        check('ASan backend 10/10', '100% tests passed out of 10' in text)
        # Export and build the native oracle independently; never from the modified workspace.
        archive = output/'native-MQSim.tar'
        base = lock['dependencies']['MQSim']['commit']
        run('native-export',['git','-C',ROOT/'third_party/MQSim','archive','--format=tar','-o',archive,base])
        native = build/'native-oracle'
        if native.exists():
            raise RuntimeError('Native build directory exists; use a fresh --build-root')
        native.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(native, filter='data')
        flags = lock['build']['cxxflags']+' '+lock['build']['mqsim_extra_cxxflags']+' -isysroot '+lock['target']['sdk_path']
        run('native-build',[lock['tools']['make']['path'],'-j2', 'CC='+lock['tools']['cxx']['path'],
                           'LD='+lock['tools']['cxx']['path']+' -isysroot '+lock['target']['sdk_path'], 'CC_FLAGS='+flags],cwd=native)
        manifest['oracle'] = dict(commit=base, archive_sha256=sha(archive), binary_sha256=sha(native/'MQSim'),
                                  compiler_sha256=sha(lock['tools']['cxx']['path']), flags=flags)
        hashes=[]
        for name,binary in [('native',native/'MQSim'),('ordinary',build/'mqsim_development_cli')]:
            directory=output/name
            directory.mkdir()
            for fixture in (ROOT/'configs/native').iterdir():
                if fixture.is_file(): shutil.copy2(fixture,directory/fixture.name)
            text=run(name,[binary,'-i','ssdconfig.xml','-w','workload.xml'],cwd=directory)
            check(name+' serviced 4 requests', 'total requests generated: 4 total requests serviced:4' in text)
            result=directory/'workload_scenario_1.xml'
            values={e.tag:e.text for e in ET.parse(result).getroot().find('.//Host.IO_Flow')}
            check(name+' 4 reads 13312 bytes zero writes',all(float(values[k])==v for k,v in {
                'Request_Count':4,'Read_Request_Count':4,'Write_Request_Count':0,
                'Bytes_Transferred_Read':13312,'Bytes_Transferred_Write':0}.items()))
            hashes.append(sha(result))
        check('ordinary route XML identical to independent native oracle',hashes[0]==hashes[1])
        manifest['ordinary_xml_sha256']=hashes
        manifest['binaries_sha256']={str(p):sha(p) for p in [build/'local_read_probe',build/'local_read_device_probe',
                                      build/'mqsim_development_cli',build/'libndpsim.so']}
        check('inputs unchanged during verification',inputs()==manifest['inputs_sha256'])
        check('MQSim source unchanged during verification',mqsim_source_snapshot()==manifest['mqsim_source'])
        manifest['status']='passed'
    except Exception as e:
        manifest.update(status='failed',error=str(e))
        raise
    finally:
        save()
    print(f'PASS: {len(manifest["assertions"])} assertions; {output}/manifest.json')


if __name__=='__main__':
    main()
