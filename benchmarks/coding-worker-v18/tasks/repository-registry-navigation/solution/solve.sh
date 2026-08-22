#!/bin/sh
set -eu
cat > /workspace/registry/graph.py <<'PY'
class AliasCycleError(ValueError):
    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        super().__init__('alias cycle: ' + ' -> '.join(path))

def follow_aliases(name: str, aliases: dict[str, str]) -> str:
    path = []
    current = name
    while current in aliases:
        if current in path:
            start = path.index(current)
            raise AliasCycleError(tuple(path[start:] + [current]))
        path.append(current)
        current = aliases[current]
    return current
PY
python - <<'PY'
from pathlib import Path
path = Path('/workspace/registry/loader.py')
text = path.read_text().replace('from .graph import AliasCycleError', 'from .graph import AliasCycleError, follow_aliases')
text = text.replace('canonical = self.aliases.get(name, name)', 'canonical = follow_aliases(name, self.aliases)')
path.write_text(text)
PY
