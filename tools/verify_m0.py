"""T0 evidence helpers; no conversion, simulation shortcuts, or implicit downloads.

inspect validates complete source files, measure runs one isolated process,
inventory records the current checkout. Outputs are evidence, not stage approval.
"""
import argparse
import array
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def inspect(path, fmt, count, dimensions, id_bound=None):
    """Bounded-memory validation of the *entire* raw file, little endian only."""
    path = Path(path)
    if count <= 0 or dimensions <= 0 or dimensions > 2**31 - 1:
        raise ValueError('invalid expected shape')
    before = path.stat()
    width = (dimensions + (fmt in ('fvecs', 'ivecs'))) * 4
    header = 8 if fmt in ('fbin', 'ibin') else 0
    if before.st_size != header + count * width:
        raise ValueError('file size/shape mismatch (truncation or extra bytes)')
    integer = fmt in ('ivecs', 'ibin')
    if integer and (id_bound is None or id_bound <= 0):
        raise ValueError('ID bound required')
    h = hashlib.sha256()
    with path.open('rb') as f:
        if header:
            raw = f.read(8)
            h.update(raw)
            if struct.unpack('<II', raw) != (count, dimensions):
                raise ValueError('bin header/shape/endian mismatch')
        rows_per_block = max(1, (8 * 1024 * 1024) // width)
        for first in range(0, count, rows_per_block):
            rows = min(rows_per_block, count - first)
            raw = f.read(rows * width)
            if len(raw) != rows * width:
                raise ValueError('file changed/truncated during validation')
            h.update(raw)
            values = array.array('i' if integer else 'f')
            values.frombytes(raw)
            if sys.byteorder != 'little':
                values.byteswap()
            if header:
                payload = values
                if integer:
                    if any(x < 0 or x >= id_bound for x in payload):
                        raise ValueError('ID out of range')
                elif not all(map(math.isfinite, payload)):
                    raise ValueError('non-finite FP32')
            else:
                for row in range(rows):
                    offset = row * width
                    if struct.unpack_from('<i', raw, offset)[0] != dimensions:
                        raise ValueError('vecs row dimension/endian mismatch')
                    payload = values[row * (dimensions + 1) + 1:(row + 1) * (dimensions + 1)]
                    if integer:
                        if any(x < 0 or x >= id_bound for x in payload):
                            raise ValueError('ID out of range')
                    elif not all(map(math.isfinite, payload)):
                        raise ValueError('non-finite FP32')
        if f.read(1):
            raise ValueError('extra bytes')
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('source changed during validation')
    return dict(path=str(path.resolve()), format=fmt, count=count, dimensions=dimensions,
                bytes=before.st_size, sha256=h.hexdigest(), dtype='int32' if integer else 'float32',
                byte_order='little', validation='complete file', id_bound=id_bound)


def measure(argv, output, timeout, rss_limit=None, env=None, poll_seconds=0.1):
    """wait4 reports this child peak RSS (not cumulative RUSAGE_CHILDREN).

    Timeout kills its process group. RSS budget is checked after completion;
    this is not a hard memory limiter or a process-tree simultaneous RSS monitor.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.with_suffix('.log').exists():
        raise ValueError('measurement output already exists')
    start = time.monotonic()
    with output.with_suffix('.log').open('wb') as log:
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                os.dup2(log.fileno(), 1)
                os.dup2(log.fileno(), 2)
                os.execvpe(argv[0], argv, dict(os.environ, **(env or {})))
            except BaseException as exc:
                print(str(exc), file=sys.stderr, flush=True)
                os._exit(127)
        timed_out = False
        while True:
            done, status, usage = os.wait4(pid, os.WNOHANG)
            if done:
                break
            if time.monotonic() - start > timeout:
                timed_out = True
                os.killpg(pid, signal.SIGKILL)
                _, status, usage = os.wait4(pid, 0)
                break
            time.sleep(poll_seconds)
    rss = usage.ru_maxrss * (1 if platform.system() == 'Darwin' else 1024)
    code = os.waitstatus_to_exitcode(status)
    result = dict(argv=argv, cwd=str(Path.cwd()), exit_code=code,
                  wall_seconds=time.monotonic() - start, peak_rss_bytes=rss,
                  rss_method='wait4 child ru_maxrss; Darwin bytes / Linux KiB converted to bytes',
                  timeout_seconds=timeout, poll_seconds=poll_seconds, timed_out=timed_out, rss_limit_bytes=rss_limit,
                  rss_budget_pass=rss_limit is None or rss <= rss_limit,
                  environment_overrides=env or {}, log_sha256=sha(output.with_suffix('.log')))
    result['passed'] = code == 0 and not timed_out and result['rss_budget_pass']
    write(output, result)
    return result


def inventory():
    from source_snapshot import mqsim_source_snapshot
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    files = {n: sha(ROOT/n) for n in sorted(set(filter(None, names))) if (ROOT/n).is_file()}
    ram = subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True) if platform.system() == 'Darwin' else None
    return dict(schema='ndpsim-m0-inventory-v1', commit=git('rev-parse', 'HEAD'),
                status=git('status', '--short'), source_sha256=files,
                diff_sha256=hashlib.sha256(subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=ROOT)).hexdigest(),
                mqsim=mqsim_source_snapshot(), sst_head=git('-C', 'third_party/sst-core', 'rev-parse', 'HEAD'),
                sst_status=git('-C', 'third_party/sst-core', 'status', '--short'),
                platform=platform.platform(), python=sys.version, python_executable=sys.executable,
                disk_free_bytes=shutil.disk_usage(ROOT).free,
                physical_ram_bytes=int(ram.stdout) if ram and ram.returncode == 0 else None,
                physical_ram_command_exit=ram.returncode if ram else None,
                physical_ram_error=ram.stderr if ram else None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    i = sub.add_parser('inspect')
    i.add_argument('path', type=Path)
    i.add_argument('--format', choices=['fvecs', 'ivecs', 'fbin', 'ibin'], required=True)
    i.add_argument('--count', type=int, required=True)
    i.add_argument('--dimensions', type=int, required=True)
    i.add_argument('--id-bound', type=int)
    i.add_argument('--output', type=Path, required=True)
    m = sub.add_parser('measure')
    m.add_argument('--output', type=Path, required=True)
    m.add_argument('--timeout', type=float, required=True)
    m.add_argument('--rss-limit', type=int)
    m.add_argument('command', nargs=argparse.REMAINDER)
    inv = sub.add_parser('inventory')
    inv.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.action == 'inspect':
        write(a.output, inspect(a.path, a.format, a.count, a.dimensions, a.id_bound))
    elif a.action == 'inventory':
        write(a.output, inventory())
    else:
        command = a.command[1:] if a.command[:1] == ['--'] else a.command
        if not command or a.timeout <= 0:
            p.error('positive timeout and command required')
        result = measure(command, a.output, a.timeout, a.rss_limit)
        print(json.dumps(result))
        return 0 if result['passed'] else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
