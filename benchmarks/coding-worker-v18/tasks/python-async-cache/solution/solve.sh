#!/bin/sh
set -eu
python - <<'PY'
from pathlib import Path
models = Path('/workspace/async_cache/models.py')
models.write_text(models.read_text().replace('.lower()', '.casefold()'))
cache = Path('/workspace/async_cache/cache.py')
text = cache.read_text().replace(
    'from collections import OrderedDict',
    'import asyncio\nfrom collections import OrderedDict',
).replace(
    '        self._values: OrderedDict[str, str] = OrderedDict()\n',
    '        self._values: OrderedDict[str, str] = OrderedDict()\n'
    '        self._inflight: dict[str, asyncio.Task[str]] = {}\n',
)
old = '''        value = await self._backend.fetch(normalized)\n        self._values[normalized] = value\n        self._values.move_to_end(normalized)\n        while len(self._values) > self._capacity:\n            self._values.popitem(last=False)\n        return value\n'''
new = '''        task = self._inflight.get(normalized)\n        if task is None:\n            task = asyncio.create_task(self._backend.fetch(normalized))\n            self._inflight[normalized] = task\n        try:\n            value = await asyncio.shield(task)\n        finally:\n            if task.done():\n                self._inflight.pop(normalized, None)\n        if normalized not in self._values:\n            self._values[normalized] = value\n            self._values.move_to_end(normalized)\n            while len(self._values) > self._capacity:\n                self._values.popitem(last=False)\n        return value\n'''
cache.write_text(text.replace(old, new))
PY

