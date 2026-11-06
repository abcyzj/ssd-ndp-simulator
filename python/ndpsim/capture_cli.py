"""Development P02 entry point. The unified user CLI belongs to P14."""
import argparse
import json
from pathlib import Path
import sys
from .static_ir import OperatorRegistry, seal_capture, validate_package


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['seal','check'])
    p.add_argument('--registry',required=True,type=Path)
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--output',type=Path,help='new package directory, required for seal')
    p.add_argument('--report',required=True,type=Path,help='new report file outside the package')
    p.add_argument('--expected-package-sha256',help='independently supplied package identity for check')
    p.add_argument('--allow-fixture',action='store_true')
    a=p.parse_args()
    if a.action=='seal' and a.output is None:p.error('seal requires --output')
    if a.action=='seal' and a.expected_package_sha256:p.error('expected package identity is for check')
    if a.action=='check' and a.output is not None:p.error('check does not accept --output')
    try:
        for directory in [a.input]+([a.output] if a.output else []):
            if a.report.resolve().is_relative_to(directory.resolve()):raise ValueError('report must be outside package/capture directory')
        with a.report.open('x') as report:
            try:
                registry=OperatorRegistry.load(a.registry)
                result=seal_capture(a.input,a.output,registry,allow_fixture=a.allow_fixture) if a.action=='seal' else validate_package(a.input,registry,allow_fixture=a.allow_fixture,expected_package_sha256=a.expected_package_sha256)
                json.dump(result,report,indent=2);report.write('\n')
            except Exception as exc:
                json.dump(dict(structural_passed=False,independent_semantic_passed=False,error=str(exc)),report,indent=2);report.write('\n');raise
    except Exception as exc:
        print(str(exc),file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
