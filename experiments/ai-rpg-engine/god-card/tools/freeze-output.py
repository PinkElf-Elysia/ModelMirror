"""Freeze the user-authored supplement separately; never edit the author's system."""
from pathlib import Path
import json, sys
from freeze import BASE, extract

source = Path(sys.argv[1]).resolve()
assert source.suffix.lower() == '.docx' and not source.name.startswith('~$')
original, text, extracted = extract(source)
payload = {'format': 1, 'cardId': 'god', 'authority': 'User-authored output supplement',
           'placement': 'Each current story user message suffix, including opening',
           'extraction': extracted['extraction'], 'source': extracted['source'],
           'textIntegrity': extracted['system'], 'text': text}
outputs = {BASE / 'resources' / 'output-suffix.json':
           (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
           BASE.parent / '.rpg04-work' / 'god-card' / 'originals' / source.name: original}
for path, data in outputs.items():
    assert not path.exists() or path.read_bytes() == data, 'Frozen supplement drift: ' + str(path)
for path, data in outputs.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open('xb') as f:
            f.write(data)
print(json.dumps({k: v for k, v in payload.items() if k != 'text'}, ensure_ascii=True))
