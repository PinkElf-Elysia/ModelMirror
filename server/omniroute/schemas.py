from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


RouterStatus = Literal["online", "stale", "offline", "disabled"]
Availability = Literal["live", "degraded", "offline", "disabled"]


class ModelCandidate(BaseModel):
    profile_id: str
    invocation_id: str
    root: str | None = None
    name: str
    provider: str
    type: str = "chat"
    context_length: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    capabilities: list[str] = Field(default_factory=list)
    source: Literal["omniroute", "bundled"] = "omniroute"
    invocable: bool = True
    availability: Availability = "live"
    free: bool | None = None


class RouteCandidate(BaseModel):
    id: str
    name: str
    description: str
    channel: str
    candidate_count: int | None = None
    reachable_count: int | None = None
    invocable: bool = False
    availability: Availability = "disabled"


class ModelCatalogResponse(BaseModel):
    source: Literal["omniroute", "bundled"]
    router_status: RouterStatus
    stale: bool
    synced_at: str | None
    catalog_version: str
    models: list[ModelCandidate] = Field(default_factory=list)
    routes: list[RouteCandidate] = Field(default_factory=list)


class RouterStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    status: RouterStatus
    version: str | None = None
    candidate_count: int
    route_count: int
    synced_at: str | None = None
    stale: bool = False
    redacted: bool = True
