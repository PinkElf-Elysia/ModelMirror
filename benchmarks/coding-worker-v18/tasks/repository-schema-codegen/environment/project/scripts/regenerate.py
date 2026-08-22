import json
from pathlib import Path
from codegen import generate

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / 'schemas/client.json').read_text())
(root / 'generated/client.py').write_text(generate(schema))

