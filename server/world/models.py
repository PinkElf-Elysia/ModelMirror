"""Data models for the world-generation feature.

These are the project-internal unified formats. Every provider
(Mock / Marble / future vendors) must convert its raw API response
into these types so the business layer never depends on a vendor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


WorldStatus = Literal[
    "created",
    "uploading",
    "submitted",
    "processing",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
]

WorldInputType = Literal["image", "multi_image", "video"]

AssetKind = Literal[
    "gaussian_splat",
    "textured_mesh",
    "panorama",
    "preview",
    "other",
]

AssetFormat = Literal["spz", "ply", "glb", "gltf", "png", "unknown"]

ProviderName = Literal["mock", "marble"]


class WorldInput(BaseModel):
    """What the user submitted for a world generation."""

    type: WorldInputType
    source_file_ids: list[str] = Field(default_factory=list)
    prompt: str | None = Field(default=None, max_length=2000)


class WorldJob(BaseModel):
    """A generation task handle, returned when a task is created."""

    job_id: str
    provider_job_id: str
    status: WorldStatus = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GeneratedAsset(BaseModel):
    """One downloadable/rendereable asset produced by a world model."""

    id: str
    kind: AssetKind
    format: AssetFormat
    url: str
    size_bytes: int | None = None


class GeneratedWorld(BaseModel):
    """A completed (or in-flight) world and its assets."""

    id: str
    provider: ProviderName
    provider_world_id: str | None = None
    model: str = "marble-1.1"
    status: WorldStatus = "processing"

    input: WorldInput = Field(
        default_factory=lambda: WorldInput(type="image", source_file_ids=[])
    )

    preview_url: str | None = None
    caption: str | None = None

    assets: list[GeneratedAsset] = Field(default_factory=list)

    credits: float | None = None
    estimated_cost_usd: float | None = None

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
