from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable

class RecordingBackend:
    def __init__(self, loader: Callable[[str], Awaitable[str]]) -> None:
        self._loader = loader
        self.calls: list[str] = []

    async def fetch(self, key: str) -> str:
        self.calls.append(key)
        await asyncio.sleep(0)
        return await self._loader(key)

