"""Abstract WorldProvider interface.

The business layer only depends on this interface, never on a specific
vendor. Adding a new world model vendor means implementing this interface
and registering it via ``@register_provider`` — nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import GeneratedAsset, GeneratedWorld, WorldInput, WorldJob, WorldStatus


class WorldProvider(ABC):
    """Base class for every world-generation backend.

    Providers receive already-uploaded local files (uploaded to the
    project server by the client) and are responsible for getting them
    into the vendor's system, kicking off generation, polling status,
    and returning the vendor's assets mapped into project formats.
    """

    provider_name: str = ""
    provider_priority: int = 0

    @abstractmethod
    async def create_world(
        self,
        input_: WorldInput,
        files: list[Path],
    ) -> WorldJob:
        """Upload files and submit a world-generation task.

        Returns a ``WorldJob`` carrying the vendor's operation id so the
        caller can poll later. Must not block until generation finishes.
        """

    @abstractmethod
    async def get_job_status(self, provider_job_id: str) -> WorldStatus:
        """Return the current status of a submitted job."""

    @abstractmethod
    async def get_world(self, provider_world_id: str) -> GeneratedWorld:
        """Return the full world object (assets, preview, caption)."""

    async def get_world_id(self, provider_job_id: str) -> str | None:
        """Resolve the provider world id from a finished job (optional).

        The world id differs from the job (operation) id for Marble.
        Default implementation treats the job id as the world id.
        """
        return provider_job_id

    @abstractmethod
    async def list_assets(self, provider_world_id: str) -> list[GeneratedAsset]:
        """Return the asset list for a world, mapped to project formats."""

    def __str__(self) -> str:
        return self.provider_name or self.__class__.__name__
