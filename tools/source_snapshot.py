"""Source identity shared by delivery and historical verification runners."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mqsim_source_snapshot():
    source = ROOT/'third_party/MQSim'
    def git(*argv):
        return subprocess.check_output(['git', '-C', str(source), *argv])
    base = json.loads((ROOT/'deps.lock.json').read_text())['dependencies']['MQSim']['commit']
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().split('\0')
    return {'source_directory': str(source), 'native_base_commit': base,
            'head_commit': git('rev-parse', 'HEAD').decode().strip(),
            'diff_from_native_base_sha256': hashlib.sha256(git('diff', '--binary', base, '--')).hexdigest(),
            'files_sha256': {name: sha(source/name) if (source/name).is_file() else None
                             for name in sorted(set(filter(None, names)))}}
