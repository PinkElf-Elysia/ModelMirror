#!/bin/sh
set -eu
cat > /workspace/index_builder/catalog.py <<'PY'
ENTRIES = [('beta', 'pkg.beta'), ('alpha', 'pkg.alpha'), ('alpha', 'pkg.alpha')]
def canonical_entries() -> list[tuple[str, str]]:
    return sorted(set(ENTRIES))
PY
cat > /workspace/index_builder/builder.py <<'PY'
import json
import os
from pathlib import Path
from .catalog import canonical_entries
def build_index(root: Path) -> None:
    output = root / 'generated/index.json'; output.parent.mkdir(parents=True, exist_ok=True)
    payload = {'entries': [{'name': name, 'module': module} for name, module in canonical_entries()]}
    temporary = output.with_suffix('.json.tmp'); temporary.write_text(json.dumps(payload, sort_keys=True) + '\n'); os.replace(temporary, output)
    count = root / 'generated/build-count.txt'; count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
PY
cd /workspace
python -m build_index

