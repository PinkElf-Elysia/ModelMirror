"""Mock world provider — no API key, no network, for dev/test/demo.

Simulates the full lifecycle: a job is created, polled a few times as
``processing``, then flips to ``succeeded`` with a fixed set of example
assets (a panorama PNG and a GLB). Use for development, tests, and the
demo flow; switch to the real provider via ``WORLD_PROVIDER=marble``.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import (
    GeneratedAsset,
    GeneratedWorld,
    WorldInput,
    WorldJob,
    WorldStatus,
)
from ..provider import WorldProvider
from ..registry import register_provider

if TYPE_CHECKING:
    from ..models import GeneratedAsset as _GA

# Number of polls before the mock job reports success.
_MOCK_STEPS = 4
# Time (seconds) between polls before flipping to succeeded.
_MOCK_STEP_SECONDS = 1.5
# Example asset URLs returned by the mock. These must be REAL, publicly
# accessible, CORS-enabled assets so the frontend viewer can actually load
# them (a fake URL like cdn.marble.../mock-world/... returns 404 and the
# viewer reports a load failure).
#   pano: three.js official equirectangular panorama (CORS: *)
#   glb:  Khronos glTF sample model Duck (CORS: *)
_MOCK_PANO_URL = (
    "https://threejs.org/examples/textures/2294472375_24a3b8ef46_o.jpg"
)
_MOCK_GLB_URL = (
    "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Models/main/2.0/Duck/glTF-Binary/Duck.glb"
)


@register_provider(name="mock", priority=-100)
class MockWorldProvider(WorldProvider):
    """Deterministic in-process provider that never touches the network."""

    def __init__(
        self,
        steps: int | None = None,
        step_seconds: float | None = None,
    ) -> None:
        self._started_at: dict[str, float] = {}
        self._steps = steps if steps is not None else _MOCK_STEPS
        self._step_seconds = step_seconds if step_seconds is not None else _MOCK_STEP_SECONDS

    async def create_world(self, input_: WorldInput, files: list[Path]) -> WorldJob:
        job_id = f"mock-{uuid.uuid4().hex[:12]}"
        provider_job_id = f"op-{uuid.uuid4().hex[:12]}"
        self._started_at[provider_job_id] = time.monotonic()
        return WorldJob(
            job_id=job_id,
            provider_job_id=provider_job_id,
            status="processing",
        )

    async def get_job_status(self, provider_job_id: str) -> WorldStatus:
        started = self._started_at.get(provider_job_id)
        if started is None:
            return "failed"
        elapsed = time.monotonic() - started
        step = int(elapsed // self._step_seconds)
        if step >= self._steps:
            return "succeeded"
        return "processing"

    async def get_world(self, provider_world_id: str) -> GeneratedWorld:
        world_id = provider_world_id or f"world-mock-{uuid.uuid4().hex[:8]}"
        return GeneratedWorld(
            id=world_id,
            provider="mock",
            provider_world_id=world_id,
            model="marble-1.1",
            status="succeeded",
            preview_url=_MOCK_PANO_URL,
            caption="Mock world generated for development/testing.",
            assets=[
                GeneratedAsset(
                    id=f"asset-{uuid.uuid4().hex[:8]}",
                    kind="panorama",
                    format="png",
                    url=_MOCK_PANO_URL,
                ),
                GeneratedAsset(
                    id=f"asset-{uuid.uuid4().hex[:8]}",
                    kind="textured_mesh",
                    format="glb",
                    url=_MOCK_GLB_URL,
                ),
            ],
            credits=0.0,
            estimated_cost_usd=0.0,
        )

    async def list_assets(self, provider_world_id: str) -> list["_GA"]:
        world = await self.get_world(provider_world_id)
        return world.assets
