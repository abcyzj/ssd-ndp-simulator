"""Migration shim: canonical functional oracle lives in the independent workload."""
from pathlib import Path
import runpy
_namespace = runpy.run_path(str(Path(__file__).resolve().parents[1]/'workloads/graph-search/tests/test_native_search.py'))
globals().update({k:v for k,v in _namespace.items() if not k.startswith('__')})
if __name__ == '__main__': main()
