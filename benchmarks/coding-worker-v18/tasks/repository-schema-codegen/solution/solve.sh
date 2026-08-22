#!/bin/sh
set -eu
cat > /workspace/codegen/generator.py <<'PY'
from typing import Any
def generate(schema: dict[str, Any]) -> str:
    lines = ['# generated; do not edit', f'class {schema["title"]}:']
    for name in sorted(schema['properties']):
        lines.append(f'    {name}: {schema["properties"][name]["type"]}')
    return '\n'.join(lines) + '\n'
PY
cd /workspace
python -m scripts.regenerate
