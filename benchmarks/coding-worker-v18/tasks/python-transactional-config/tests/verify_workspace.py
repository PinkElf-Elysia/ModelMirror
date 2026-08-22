import hashlib
import json
import os
from pathlib import Path

root = Path('/workspace')
policy = json.loads(Path('/tests/workspace-policy.json').read_text())
current = {}
for directory, names, files in os.walk(root, followlinks=False):
    base = Path(directory)
    for name in tuple(names) + tuple(files):
        path = base / name
        if path.is_symlink():
            raise SystemExit('workspace link rejected')
    for name in files:
        path = base / name
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        if b'\x00' in content and relative != policy['binary_canary']:
            raise SystemExit('unexpected binary output')
        current[relative] = hashlib.sha256(content).hexdigest()
baseline = policy['baseline']
changed = set(baseline) ^ set(current)
changed.update(path for path in set(baseline) & set(current) if baseline[path] != current[path])
if len(changed) < policy['required_modified_files']:
    raise SystemExit('insufficient multi-file change')
canary = policy['binary_canary']
if current.get(canary) != baseline.get(canary):
    raise SystemExit('binary canary changed')

