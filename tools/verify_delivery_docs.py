"""Static V0.3 delivery document/link and retired Word dependency checks."""
import re
from pathlib import Path
from verify_m0 import ROOT, sha


def verify():
    files=[ROOT/'README.md',ROOT/'AGENTS.md',ROOT/'tools/README.md',
        *(ROOT/'docs'/name for name in ('README.md','prd-v0.3.md','ir-v0.3.md','testing.md',
          'delivery.md','model-limitations.md','simulation-input.md','output.md',
          'archive/v0.3/implementation-plan.md','releases/v0.3.md'))]
    count=0
    for path in files:
        text=path.read_text()
        # Formal text may mention design history, but must not depend on a .docx
        # location, include its content or link an external Word-only definition.
        if re.search(r'\]\([^)]*\.docx?(?:[#?][^)]*)?\)',text,re.I):
            raise ValueError('retired Word dependency: '+str(path))
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if '://' in target or target.startswith('#'):continue
            relative=target.split('#',1)[0]
            if relative and not (path.parent/relative).exists():raise ValueError('broken document link: '+str(path)+' -> '+target)
            count+=1
    return dict(passed=True,relative_links=count,files_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},
        scope='formal delivery entry links and Word-file dependency check; not a simulation check')
