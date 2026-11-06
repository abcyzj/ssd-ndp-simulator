"""P00 static migration coverage audit; no build, collection or task progression."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT/'docs/migration-v0.4.5'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8*1024**2), b''):
            h.update(block)
    return h.hexdigest()


def inventory():
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others',
                                     '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    return sorted({p for p in names if p and (ROOT/p).is_file() and (
        p.startswith(('src/', 'tools/', 'tests/', 'configs/', 'schemas/', 'examples/'))
        or p in ('CMakeLists.txt', 'deps.lock.json', '.gitmodules'))})


def validate(files, tests, actual_files, actual_tests, matrix):
    rows = files['files']
    names = [r['path'] for r in rows]
    if len(set(names)) != len(names) or set(names) != set(actual_files):
        raise ValueError('file inventory missing/duplicate/stale: '+str(set(names)^set(actual_files)))
    for row in rows + tests['tests']:
        if any(not row.get(k) for k in ('decision', 'tasks', 'replacement_assertions', 'deletion_gate', 'reason')):
            raise ValueError('incomplete migration decision')
    expected = [t['name'] for t in tests['tests']]
    if len(set(expected)) != len(expected) or set(expected) != {t['name'] for t in actual_tests}:
        raise ValueError('CTest inventory missing/duplicate/stale')
    rows = matrix['configurations']
    points = [(32, 64), (64, 128)]
    required = {f'{prefix}{i:02}': (L, E, placement, provider, repeats)
                for prefix, placement, provider, repeats in (
                    ('A', 'host', 'host_memory', 2), ('B', 'host', 'ssd', 1),
                    ('C', 'controller', 'ssd', 1))
                for i, (L, E) in enumerate(points, 1)}
    actual = {r['id']: (r['L'], r['E'], r['placement'], r['data_provider'],
                        r['simulated_repeats_min']) for r in rows}
    if len(rows) != 6 or actual != required or matrix['simulated_runs_min'] != 8:
        raise ValueError('formal six configuration/eight run matrix changed')
    for r in rows:
        if (r['base_count'], r['dimensions'], r['queries'], r['k'], r['B'], r['chunk']) != (1000000,128,128,10,1,16):
            raise ValueError('formal dataset/semantics changed')
        if r['id'].startswith('A') and (r['workers'], r['slots'], r['admission'], r['native_repeats_min']) != (1,1,1,3):
            raise ValueError('strict CPU point changed')
    if matrix.get('schedule_frozen_by') != 'P01/v045-contract/1':
        raise ValueError('missing P01 schedule freeze')
    for r in rows:
        if (r['workers'],r['slots'],r['admission'],r.get('offered_ns'),r.get('schedule_policy')) != (1,1,1,0,'fifo-query/1'):
            raise ValueError('P01 comparable resource/admission schedule changed')
    selection = json.loads((ROOT/'configs/benchmarks/v0.2-query-selection.json').read_text())['datasets']['sift1m']
    if matrix['tuning_ids'] != selection['tuning_ids'] or matrix['evaluation_ids'] != selection['evaluation_ids']:
        raise ValueError('original query order changed')
    if len(matrix['tuning_ids']) != 32 or len(matrix['evaluation_ids']) != 128 or set(matrix['tuning_ids']) & set(matrix['evaluation_ids']):
        raise ValueError('query split overlap/coverage')
    return True


def references(names):
    """Resolve local Python imports and literal file references; report dynamic sites separately.

    This is a deletion review input, not a proof that computed paths/imports are absent.
    """
    by_module = {}
    for name in names:
        if name.endswith('.py'):
            by_module.setdefault(Path(name).stem, []).append(name)
    edges = set(); dynamic = []
    for name in names:
        path = ROOT/name
        if path.suffix == '.py':
            tree = ast.parse(path.read_text(), filename=name)
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import): mods = [a.name for a in node.names]
                if isinstance(node, ast.ImportFrom): mods = [node.module or '']
                for mod in mods:
                    for target in by_module.get(mod.split('.')[0], []):
                        edges.add((name, target, node.lineno, 'python_import'))
                if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == '__import__' or
                    isinstance(node.func, ast.Attribute) and node.func.attr in ('import_module', 'run', 'Popen', 'check_call', 'check_output')):
                    dynamic.append(dict(source=name, line=node.lineno, expression=ast.unparse(node)[:240]))
        if path.suffix in ('.py', '.cc', '.h', '.in', '.txt', '.json', '.cmake') or path.name=='CMakeLists.txt':
            text = path.read_text()
            for i, line in enumerate(text.splitlines(), 1):
                for target in re.findall(r'(?:src|tests|tools|configs|schemas|examples)/[\w./-]+', line):
                    if target in names: edges.add((name, target, i, 'literal_reference'))
                for header in re.findall(r'#include\s+"([^"]+)"', line):
                    for candidate in (path.parent/header, ROOT/'src'/header, ROOT/'tests'/header):
                        try: target = str(candidate.resolve().relative_to(ROOT))
                        except ValueError: continue
                        if target in names: edges.add((name,target,i,'cpp_include')); break
    return dict(edges=[dict(source=s,target=t,line=n,kind=k) for s,t,n,k in sorted(edges)],
                dynamic_review_sites=dynamic,
                limitations='Static local imports/includes/literal paths only. Computed subprocess paths, runtime imports and CMake generator expressions require P20 review plus clean build/tests. No claim of deletion safety from this graph alone.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error('output must not exist')
    files, tests, matrix = [json.loads((DIRECTORY/n).read_text()) for n in ('files.json','tests.json','matrix.json')]
    command = ['ctest', '--test-dir', str(args.build.resolve()), '--show-only=json-v1']
    run = subprocess.run(command, capture_output=True, text=True, check=True)
    actual_tests = json.loads(run.stdout)['tests']; names=inventory()
    validate(files, tests, names, actual_tests, matrix)
    # Comparing command provenance catches a renamed/rebound test with an unchanged name.
    old = {t['name']:t['command'] for t in tests['tests']}
    def normalize(cmd):
        return [x.replace(str(args.build.resolve()), '${BUILD}').replace(str(ROOT), '${ROOT}') for x in cmd]
    if any(old[t['name']] != normalize(t['command']) for t in actual_tests):
        raise ValueError('CTest command changed; review migration binding')
    args.output.mkdir(parents=True)
    def save(name, value):
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    save('references.json', references(names))
    save('ctest.json', json.loads(run.stdout))
    save('audit.json', dict(passed=True, scope='P00 inventory/plan only, not migration equivalence or AC acceptance',
        files=len(names), tests=len(actual_tests), command=command, exit_code=run.returncode,
        files_sha256={name:digest(ROOT/name) for name in names},
        plans_sha256={n:digest(DIRECTORY/n) for n in ('files.json','tests.json','matrix.json','budget.json')}))
    print(f'P00 inventory PASS: {len(names)} files; {len(actual_tests)} CTests; 6 configurations/8 runs')


if __name__ == '__main__': main()
