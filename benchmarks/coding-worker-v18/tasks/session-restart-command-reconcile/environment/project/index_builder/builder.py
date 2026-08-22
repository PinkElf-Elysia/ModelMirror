import json
from pathlib import Path
from .catalog import ENTRIES

def build_index(root: Path) -> None:
    output = root / 'generated/index.json'
    existing = json.loads(output.read_text()) if output.exists() else {'entries': []}
    existing['entries'].extend({'name': name, 'module': module} for name, module in ENTRIES)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(existing))
    count = root / 'generated/build-count.txt'
    count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))

