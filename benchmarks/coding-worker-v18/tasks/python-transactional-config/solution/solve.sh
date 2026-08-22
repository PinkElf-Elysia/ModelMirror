#!/bin/sh
set -eu
cat > /workspace/config_tx/errors.py <<'PY'
class BatchConfigError(ValueError):
    def __init__(self, index: int, path: str) -> None:
        self.index = index
        self.path = path
        super().__init__(f'operation {index} failed at {path}')
PY
python - <<'PY'
from pathlib import Path
path = Path('/workspace/config_tx/store.py')
text = path.read_text()
old = '''    def apply_batch(self, operations: list[dict[str, Any]]) -> None:\n        for index, raw in enumerate(operations):\n            try:\n                action, path, value = validate_operation(raw)\n                self._apply_one(self._data, action, path, value)\n            except (KeyError, TypeError, ValueError) as exc:\n                raise BatchConfigError(f'operation {index} failed: {path}') from exc\n'''
new = '''    def apply_batch(self, operations: list[dict[str, Any]]) -> None:\n        staged = deepcopy(self._data)\n        for index, raw in enumerate(operations):\n            path = str(raw.get('path', '<missing>'))\n            try:\n                action, path, value = validate_operation(raw)\n                self._apply_one(staged, action, path, value)\n            except (KeyError, TypeError, ValueError) as exc:\n                raise BatchConfigError(index, path) from exc\n        self._data = staged\n'''
path.write_text(text.replace(old, new))
PY

