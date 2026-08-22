import asyncio
import unittest
from async_cache import AsyncCache, CacheKey, RecordingBackend

class VisibleTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_value_is_reused(self) -> None:
        async def load(key: str) -> str:
            return key.upper()
        backend = RecordingBackend(load)
        cache = AsyncCache(backend)
        key = CacheKey(" Users ", "42")
        self.assertEqual(await cache.get(key), "USERS:42")
        self.assertEqual(await cache.get(key), "USERS:42")
        self.assertEqual(backend.calls, ["users:42"])

    async def test_concurrent_misses_are_coalesced(self) -> None:
        release = asyncio.Event()
        async def load(key: str) -> str:
            await release.wait()
            return f"value:{key}"
        backend = RecordingBackend(load)
        cache = AsyncCache(backend)
        calls = [asyncio.create_task(cache.get(CacheKey("n", "1"))) for _ in range(8)]
        await asyncio.sleep(0.02)
        release.set()
        self.assertEqual(await asyncio.gather(*calls), ["value:n:1"] * 8)
        self.assertEqual(backend.calls, ["n:1"])

