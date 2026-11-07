"""P16 standalone locked-native MQSim vs SST ordinary-device verification.

Explicitly builds only an isolated native oracle; never imports/runs legacy AC.
The integrated backend must already be built. No business payload is used.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET

from source_snapshot import mqsim_source_snapshot

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('APPLIED ', 'CONFIG ', 'NAMESPACE ', 'SUBMIT ', 'IO ', 'INITIAL ', 'CMT ', 'CACHE ',
            'MAP ', 'STATS ', 'DRAIN ', 'FINAL ')
APPLIED = ('PCIe_Lane_Bandwidth PCIe_Lane_Count Seed IO_Queue_Depth Queue_Fetch_Size '
           'CMT_Capacity Flash_Channel_Count Chip_No_Per_Channel Overprovisioning_Ratio '
           'Die_No_Per_Chip Plane_No_Per_Die Block_No_Per_Plane Page_No_Per_Block '
           'Page_Capacity Page_Read_Latency_LSB Page_Read_Latency_CSB Page_Read_Latency_MSB').split()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def cases(base):
    """Frozen request matrix. QD denotes offered outstanding requests, not SQ size."""
    result = []
    for name, count, edits in [
        ('qd1', 1, {}), ('qd32', 32, {}), ('small-cmt', 32, {'CMT_Capacity':128}),
        ('modified-xml', 32, {'PCIe_Lane_Bandwidth':2, 'PCIe_Lane_Count':2,
                            'Page_Read_Latency_LSB':90000, 'Page_Read_Latency_CSB':90000,
                            'Page_Read_Latency_MSB':90000, 'Seed':123,
                            'IO_Queue_Depth':32, 'Queue_Fetch_Size':2})]:
        root = ET.fromstring(base)
        for key, value in edits.items():
            root.find('.//'+key).text = str(value)
        xml = ET.tostring(root, encoding='unicode')+'\n'
        requests = [(7+(i % 16)*16, 10) for i in range(count)]
        result.append((name, xml, requests))
    return result


def request_text(requests):
    return 'ndpsim-mqsim-requests/1\n'+''.join(f'read {lba} {n}\n' for lba, n in requests)


def fields(line):
    return dict(part.split('=', 1) for part in line.split() if '=' in part)


def common(text):
    return [line for line in text.splitlines() if line.startswith(PREFIXES)]


def inspect(text, xml, requests, small_cmt=False):
    """Independent input/state ledger; does not call fixture/config/mapping code."""
    rows = common(text)
    def group(prefix):
        return [line for line in rows if line.startswith(prefix)]
    def one(prefix):
        value = group(prefix)
        need(len(value)==1, 'missing/duplicate '+prefix)
        return fields(value[0])
    root = ET.fromstring(xml)
    leaves = {e.tag:e.text for e in root.iter() if len(e)==0}
    applied = {}
    for line in group('APPLIED '):
        pair = fields(line)
        need(len(pair)==1 and not (applied.keys() & pair.keys()), 'duplicate applied field')
        applied.update(pair)
    need(set(applied)==set(APPLIED), 'applied field coverage')
    need(all(float(applied[k])==float(leaves[k]) for k in APPLIED), 'XML was not applied')
    integer = lambda key: int(leaves[key])
    page = integer('Page_Capacity')//512
    planes = math.prod(integer(k) for k in ('Flash_Channel_Count','Chip_No_Per_Channel',
                                          'Die_No_Per_Chip','Plane_No_Per_Die'))
    pp = integer('Block_No_Per_Plane')*integer('Page_No_Per_Block')
    capacity = int(pp*page*(1-float(leaves['Overprovisioning_Ratio'])))*planes
    config = one('CONFIG ')
    need(config==dict(sha256=hashlib.sha256(xml.encode()).hexdigest(),
                      namespace_sectors=str(capacity),page_sectors=str(page)), 'config identity/geometry')
    need(one('NAMESPACE ')==dict(count=str(capacity),end_lba=str(capacity-1)), 'actual namespace/flow bounds')
    pages = 512//page
    need(512 % page==0, 'preload alignment')
    expected = [(i+1, i*page, page, 1) for i in range(pages)]
    expected += [(pages+i+1, lba, n, 0) for i,(lba,n) in enumerate(requests)]
    submits = [tuple(map(int,line.split()[1:])) for line in group('SUBMIT ')]
    completed = [tuple(map(int,line.split()[1:])) for line in group('IO ')]
    need(all(len(row)==5 for row in submits+completed), 'invalid IO record')
    need([row[:4] for row in submits]==expected, 'request submission order/LBA/size/type')
    need(len(completed)==len(expected) and sorted(row[:4] for row in completed)==expected,
         'missing/duplicate/incorrect completion')
    start = {row[0]:row for row in submits}
    need(all(row[4]>=start[row[0]][4] for row in completed), 'completion before submission')
    initial = one('INITIAL ')
    origin = int(initial['time'])
    need(initial==dict(seed=leaves['Seed'],preload='0:512',data_cache='off',pending='0',time=str(origin)),
         'initial state')
    need(all(row[4]<=origin for row in completed if row[3]) and
         all(row[4]==origin for row in submits if not row[3]), 'preload/measurement window')
    mapping = [tuple(map(int,line.split()[1:])) for line in group('MAP ')]
    need(len(mapping)==pages and [r[0] for r in mapping]==list(range(pages)), 'mapping coverage/order')
    need(all(0<=r[2]<=(1<<page)-1 for r in mapping) and len({r[1] for r in mapping})==pages,
         'mapping written bitmap/physical uniqueness')
    cmt = one('CMT ')
    physical_pages = pp*planes
    address_bits = (physical_pages-1).bit_length()
    entry_bytes = (2*address_bits+page+7)//8  # Logical+physical addresses and sector bitmap.
    cmt_capacity = integer('CMT_Capacity')//entry_bytes
    need(int(cmt['capacity'])==cmt_capacity and cmt['preconditioning_entries']=='0', 'CMT capacity/initial policy')
    cache_rows = group('CACHE ')
    need(len(cache_rows)==1, 'cache snapshot coverage')
    cache = [part.split(':') for part in cache_rows[0][6:].split(',') if part]
    need(0<len(cache)<=cmt_capacity and len({r[0] for r in cache})==len(cache), 'CMT contents/unique keys')
    need(all(len(r)==3 and r[1]=='2' and r[2] in ('0','1') and
             0<=int(r[0])<pages and mapping[int(r[0])][2]==(1<<page)-1 for r in cache),
         'resident CMT status/bitmap')
    stats = {}
    for phase in ('initial','final'):
        value = one('STATS '+phase+' ')
        stats[phase] = {k:int(v) for k,v in value.items()}
        need(set(value)==set(('IssuedReadCMD IssuedProgramCMD IssuedEraseCMD Total_flash_reads_for_mapping Total_flash_writes_for_mapping CMT_hits CMT_miss total_CMT_queries readTR_CMT_hits readTR_CMT_miss total_readTR_CMT_queries writeTR_CMT_hits writeTR_CMT_miss total_writeTR_CMT_queries Total_gc_executions Total_wl_executions').split()), 'statistics coverage')
        for prefix in ('readTR_','writeTR_'):
            need(stats[phase][prefix+'CMT_hits']+stats[phase][prefix+'CMT_miss']==
                 stats[phase]['total_'+prefix+'CMT_queries'], 'CMT statistics conservation')
        # Upstream global CMT_miss counts only immediately resolved mapping misses.
        # Deferred misses still increment the typed read/write miss counters.
        t=stats[phase]
        need(t['total_CMT_queries']==t['total_readTR_CMT_queries']+t['total_writeTR_CMT_queries'] and
             t['CMT_hits']==t['readTR_CMT_hits']+t['writeTR_CMT_hits'] and
             t['CMT_miss']<=t['readTR_CMT_miss']+t['writeTR_CMT_miss'], 'CMT global/typed statistics')
    need(stats['initial'].keys()==stats['final'].keys() and
         all(stats['final'][k]>=v for k,v in stats['initial'].items()), 'nonmonotone counters')
    read_pages=sum((lba+n-1)//page-lba//page+1 for lba,n in requests)
    need(stats['initial']['total_readTR_CMT_queries']==0 and
         stats['final']['total_readTR_CMT_queries']==read_pages and
         stats['initial']['total_writeTR_CMT_queries']==pages and
         stats['initial']['IssuedProgramCMD']==pages, 'independent page/transaction ledger')
    final = one('FINAL ')
    need(final['reads']==str(len(requests)) and final['origin']==str(origin), 'final count/origin')
    misses = stats['final']['readTR_CMT_miss']-stats['initial']['readTR_CMT_miss']
    need(int(final['read_cmt_misses'])==misses and (not small_cmt or misses>0), 'CMT miss coverage')
    drain = one('DRAIN ')
    need(drain['pending']==drain['events']=='0' and int(drain['preload'])==pages and
         int(drain['time'])>=max(row[4] for row in completed), 'incomplete drain')
    # NAND command counts include translation traffic; never equate these to logical bytes.
    return dict(requests=len(requests), preload_pages=pages, origin_ns=origin,
                logical_read_bytes=sum(n*512 for _,n in requests), preload_bytes=512*512,
                read_cmt_misses=misses, statistics=stats, common_rows=len(rows))


def compare(native, integrated, xml, requests, small_cmt=False):
    ledger = inspect(native, xml, requests, small_cmt)
    need(inspect(integrated, xml, requests, small_cmt)==ledger, 'independent ledgers differ')
    need(common(native)==common(integrated), 'common event/order/time/state fields differ')
    return ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=600, help='Per-command seconds, including oracle compilation')
    args = parser.parse_args()
    build, out = args.build.resolve(), args.output.resolve()
    if out.exists(): parser.error('output must be a new directory')
    if not 1<=args.timeout<=3600: parser.error('timeout must be 1..3600')
    out.mkdir(parents=True)
    commands, checks = [], {}
    report = dict(schema='ndpsim-mqsim-verification/1', status='failed', task='P16',
                  scope='common ordinary NVMe device behavior; not platform offload or hardware calibration',
                  full_acceptance_passed=False, repeats_per_side=2, checks=checks, commands=commands,
                  comparison=dict(exact=list(PREFIXES), exclusions={
                      'stdout_other_lines':'MQSim diagnostics and SST startup/finish text are not common device events',
                      'native_DMA_content_validation':'upstream lacks fork data-arrival callback; no payload correctness claim',
                      'controller_local_read_compute_platform_messages':'not upstream capabilities; P12/P19 validate these',
                      'unreported_MQSim_counters':'only the 16 named STATS fields are part of this frozen matrix; no exhaustive backend state claim',
                      'host_wall_clock_RSS':'execution environment diagnostics, not simulated time'}))
    def command(name, argv, env=None, cwd=ROOT):
        argv = list(map(str, argv)); start = time.monotonic()
        record = dict(name=name, argv=argv, cwd=str(cwd), env=env or {}, exit_code=None)
        commands.append(record)
        try:
            p = subprocess.run(argv, cwd=cwd, env=dict(os.environ,**(env or {})),
                               capture_output=True, timeout=args.timeout)
            record['exit_code'] = p.returncode
            (out/(name+'.stdout')).write_bytes(p.stdout)
            (out/(name+'.stderr')).write_bytes(p.stderr)
            need(p.returncode==0, name+' failed: '+p.stderr.decode(errors='replace')[-1200:])
            return p.stdout.decode()
        except subprocess.TimeoutExpired as exc:
            (out/(name+'.stdout')).write_bytes(exc.stdout or b'')
            (out/(name+'.stderr')).write_bytes(exc.stderr or b'')
            record['timeout']=True
            raise
        finally:
            record['wall_seconds']=time.monotonic()-start
            save(out/'commands.json',commands)
    try:
        lock = json.loads((ROOT/'deps.lock.json').read_text())
        before = mqsim_source_snapshot()
        report['integrated_mqsim'] = before
        report['sst_commit'] = command('sst-identity',['git','-C',ROOT/'third_party/sst-core','rev-parse','HEAD']).strip()
        paths = json.loads((build/'sst-paths.json').read_text())
        report['build_sha256'] = {str(p):sha(p) for p in
                                  (build/'libndpsim.so', build/'CMakeCache.txt', build/'sst-paths.json', Path(paths['sst']))}
        source_inputs = [ROOT/'CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'tools/verify_mqsim.py',
                         ROOT/'tools/source_snapshot.py',ROOT/'tests/ordinary_native.cc',
                         ROOT/'tests/ordinary_fixture.h',ROOT/'src/backend/external_flow.h',
                         ROOT/'src/backend/preload_window.h', ROOT/'src/backend/device_config.cc',
                         ROOT/'src/backend/device_config.h',ROOT/'src/sst/ordinary_io.cc',
                         ROOT/'src/backend/event_bridge.h',ROOT/'configs/regression/ordinary.py',
                         ROOT/'configs/devices/native-small.xml',ROOT/'tests/device_fixture.h']
        report['inputs_sha256'] = {str(p):sha(p) for p in source_inputs}
        native = out/'native';native.mkdir()
        commit = lock['dependencies']['MQSim']['commit']
        archive = native/'source.tar'
        command('native-export',['git','-C',ROOT/'third_party/MQSim','archive','-o',archive,commit])
        with tarfile.open(archive) as tar: tar.extractall(native,filter='data')
        original = {str(p.relative_to(native)):sha(p) for p in (native/'src').rglob('*') if p.is_file()}
        report['native_oracle'] = dict(commit=commit, archive_sha256=sha(archive), source_sha256=original)
        compiler = [lock['tools']['cxx']['path'], '-std=c++17','-O1','-fno-fast-math','-ffp-contract=off']
        if lock['target']['os']=='Darwin': compiler += ['-isysroot',lock['target']['sdk_path']]
        compiler += shlex.split(lock['build'].get('mqsim_extra_cxxflags',''))
        command('compiler-identity',[compiler[0],'--version'])
        command('native-build', [*compiler,'-DNDPSIM_NATIVE_ORACLE',
            '-DNDPSIM_TEST_DEVICE_DIR='+json.dumps(str(ROOT/'configs/devices')),
            *['-I'+str(p) for p in (native/'src',ROOT/'src',ROOT/'tests',
                                    ROOT/'sdk/include',ROOT/'third_party/sst-core/external')],
            ROOT/'tests/ordinary_native.cc',ROOT/'src/backend/device_config.cc',ROOT/'sdk/src/sha256.cc',
            *sorted(p for p in (native/'src').rglob('*.cpp') if p.name!='main.cpp'),'-o',native/'probe'])
        report['native_oracle']['binary_sha256']=sha(native/'probe')
        for name, xml, requests in cases((ROOT/'configs/devices/native-small.xml').read_text()):
            folder=out/name;folder.mkdir()
            (folder/'device.xml').write_text(xml)
            (folder/'requests.txt').write_text(request_text(requests))
            declaring=folder/'experiment.json'
            save(declaring,dict(device_xml='device.xml',requests='requests.txt',preload_sectors=512,
                                offered_outstanding=len(requests),preload_policy='serial whole-page writes; drain before reads'))
            outputs=[]
            for repeat in (1,2):
                native_text=command(f'{name}-native-{repeat}',[native/'probe','--device-xml','device.xml',
                    '--declaring-file',declaring,'--qd',len(requests),'--requests',folder/'requests.txt'],cwd=out)
                integrated=command(f'{name}-sst-{repeat}',[paths['sst'],'--add-lib-path='+str(build),
                    ROOT/'configs/regression/ordinary.py'],dict(NDPSIM_DEVICE_XML='device.xml',
                    NDPSIM_DECLARING_FILE=str(declaring),NDPSIM_REQUESTS=str(folder/'requests.txt'),
                    NDPSIM_QD=str(len(requests)),NDPSIM_MISS='0',NDPSIM_SINGLE_READ='0',
                    NDPSIM_NAND_READ_DELAY='0',NDPSIM_OBSERVE='0'),cwd=out)
                ledger=compare(native_text,integrated,xml,requests,name=='small-cmt')
                outputs.append(common(native_text))
            need(outputs[0]==outputs[1], name+' nondeterministic repeats')
            checks[name]=dict(status='passed',native_runs=2,sst_runs=2,ledger=ledger)
            save(folder/'ledger.json',checks[name])
        need(checks['qd32']['ledger']['origin_ns']!=checks['modified-xml']['ledger']['origin_ns'],
             'modified XML had no timing effect')
        need(original=={str(p.relative_to(native)):sha(p) for p in (native/'src').rglob('*') if p.is_file()},
             'native export modified')
        need(before==mqsim_source_snapshot(), 'integrated MQSim sources changed during verification')
        need(all(sha(p)==value for p,value in report['inputs_sha256'].items()), 'inputs changed during run')
        need(all(sha(p)==value for p,value in report['build_sha256'].items()), 'build changed during run')
        report['status']='passed'
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        report['artifacts_sha256']={str(p.relative_to(out)):sha(p) for p in out.rglob('*')
                                    if p.is_file() and p!=out/'manifest.json'}
        save(out/'manifest.json',report)
    print('P16',report['status'],out)
    if report['status']!='passed':
        print(report.get('error','verification failed'))
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
