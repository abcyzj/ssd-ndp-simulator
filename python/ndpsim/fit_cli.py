"""P06 standalone developer entry point; unified CLI integration belongs to P14."""
import argparse
import sys
from pathlib import Path
from .static_ir import canonical, decode, file_hash
from .fitting import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fit', 'table', 'table-check', 'evaluate', 'check'))
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--allow-fixture', action='store_true')
    args = parser.parse_args()
    owned = not Path(args.output).exists()
    code = 2
    try:
        report = execute(args.action, args.manifest, args.output, allow_fixture=args.allow_fixture)
        print(f"{args.action}: passed; formal acceptance not performed; {len(report['quality'])} independent runs")
        code = 0
        return code
    except Exception as exc:
        print(str(exc), file=sys.stderr); return code
    finally:
        output = Path(args.output)
        if owned and (output/'status.json').is_file():
            (output/'command.json').write_bytes(canonical(dict(argv=[sys.executable, '-m', 'ndpsim.fit_cli', *sys.argv[1:]], cwd=str(Path.cwd()), exit_code=code, python=sys.version)))
            status = decode((output/'status.json').read_bytes())
            if status.get('passed'):
                status['artifacts_sha256']['command.json'] = file_hash(output/'command.json')
                (output/'status.json').write_bytes(canonical(status))


if __name__ == '__main__': sys.exit(main())
