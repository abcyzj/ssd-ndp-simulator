"""Bounded explicit subprocess execution and reproducibility records, no build side effects."""
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
from .static_ir import canonical, decode, file_hash, require

TRACE_RECORD_BYTES = 16 * 1024**2
TRACE_CONFIGURATION_BYTES = 64 * 1024**2


def trace_records(path):
    """Allow the leading configuration to embed the effective input snapshots."""
    with path.open('rb') as stream:
        first = stream.readline(TRACE_CONFIGURATION_BYTES + 1)
        if not first:
            return
        require(len(first) <= TRACE_CONFIGURATION_BYTES, 'trace record budget')
        configuration = decode(first)
        require(configuration['type'] == 'configuration', 'trace must start with configuration')
        yield configuration
        for line in iter(lambda: stream.readline(TRACE_RECORD_BYTES + 1), b''):
            require(len(line) <= TRACE_RECORD_BYTES, 'trace record budget')
            yield decode(line)


def save(path, value):
    Path(path).write_bytes(canonical(value))


def load(path):
    path = Path(path)
    require(path.stat().st_size <= 64*1024**2, 'JSON input exceeds 64 MiB')
    return decode(path.read_bytes())


def resolve(base, value):
    return (Path(base)/value).resolve()


def size(directory):
    return sum(p.stat().st_size for p in Path(directory).rglob('*') if p.is_file())


def budget(value):
    keys = {'output_bytes', 'scratch_bytes', 'rss_bytes', 'timeout_seconds', 'min_disk_free_bytes'}
    require(set(value) == keys and all(type(v) is int and v > 0 for v in value.values()), 'budget requires positive integer '+str(sorted(keys)))
    return value


def preflight(output, b, input_bytes=0):
    if sys.platform == 'darwin':
        physical = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'], text=True, timeout=10))
        pressure = subprocess.check_output(['/usr/bin/memory_pressure', '-Q'], text=True, timeout=10)
        import re
        percent = int(re.search(r'System-wide memory free percentage: (\d+)%', pressure)[1])
        available = physical*percent//100
    else:
        physical = os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        available = os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    free = shutil.disk_usage(output).free
    require(b['rss_bytes'] <= physical and b['rss_bytes'] <= available, 'RAM budget exceeds currently available memory')
    require(free > b['min_disk_free_bytes']+b['output_bytes']+b['scratch_bytes'], 'disk reserve budget')
    require(input_bytes <= b['output_bytes'], 'input snapshot output budget')
    result = dict(physical_ram_bytes=physical, available_ram_estimate_bytes=available, disk_free_bytes=free, budget=b,
                  host=dict(hardware=platform.machine(), os=platform.platform(), node=platform.node()),
                  limits='polled disk/time; child wait4 RSS checked at exit; not a process-tree RSS sandbox')
    save(output/'preflight.json', result)
    return result


def enforce(output, b):
    scratch = sum(p.stat().st_size for p in output.rglob('*') if p.is_file() and ('.sqlite' in p.name))
    require(scratch <= b['scratch_bytes'], 'scratch disk budget')
    require(size(output)-scratch <= b['output_bytes'], 'output disk budget')
    require(shutil.disk_usage(output).free >= b['min_disk_free_bytes'], 'disk free reserve')


def launch(argv, output, b, *, env=None):
    """Never uses a shell. A timeout kills the isolated process group and reaps the child."""
    start = time.monotonic()
    record = dict(argv=list(map(str, argv)), cwd=str(Path.cwd()), exit_code=None)
    save(output/'command.json', record)
    proc = None
    try:
        with (output/'stdout.log').open('wb') as stdout, (output/'stderr.log').open('wb') as stderr:
            proc = subprocess.Popen(record['argv'], stdout=stdout, stderr=stderr, env=env, start_new_session=True)
            while True:
                pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    record.update(exit_code=proc.returncode, peak_child_rss_bytes=usage.ru_maxrss*(1 if sys.platform == 'darwin' else 1024))
                    break
                enforce(output, b)
                require(time.monotonic()-start <= b['timeout_seconds'], 'subprocess timeout budget')
                time.sleep(.05)
        require(record['peak_child_rss_bytes'] <= b['rss_bytes'], 'child peak RSS budget')
        enforce(output, b)
        require(proc.returncode == 0, 'subprocess failed; see stderr.log (exit '+str(proc.returncode)+')')
        return record
    finally:
        if proc is not None and proc.returncode is None:
            os.killpg(proc.pid, signal.SIGKILL)
            _, status, usage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            record.update(exit_code=proc.returncode, peak_child_rss_bytes=usage.ru_maxrss*(1 if sys.platform == 'darwin' else 1024), terminated_by_budget=True)
        record['wall_seconds'] = time.monotonic()-start
        save(output/'command.json', record)


def identities(output):
    return {str(p.relative_to(output)): file_hash(p) for p in sorted(output.rglob('*')) if p.is_file() and p != output/'status.json'}
