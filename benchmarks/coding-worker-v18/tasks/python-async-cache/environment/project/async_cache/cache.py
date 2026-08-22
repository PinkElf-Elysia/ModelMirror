from __future__ import annotations
from collections import OrderedDict
from .backend import RecordingBackend
from .models import CacheKey

class AsyncCache:
    def __init__(self, backend: RecordingBackend, capacity: int = 32) -> None:
        self._backend = backend
        self._capacity = capacity
        self._values: OrderedDict[str, str] = OrderedDict()

    async def get(self, key: CacheKey) -> str:
        normalized = key.normalized()
        if normalized in self._values:
            self._values.move_to_end(normalized)
            return self._values[normalized]
        value = await self._backend.fetch(normalized)
        self._values[normalized] = value
        self._values.move_to_end(normalized)
        while len(self._values) > self._capacity:
            self._values.popitem(last=False)
        return value

    def __len__(self) -> int:
        return len(self._values)
