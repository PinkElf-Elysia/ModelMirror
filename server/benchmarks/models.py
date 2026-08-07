from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


BenchmarkKind = Literal["agent_response", "knowledge_retrieval"]


class BenchmarkManifest(BaseModel):
    pack_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    kind: BenchmarkKind
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    locales: list[str] = Field(min_length=1, max_length=10)
    coverage: list[str] = Field(min_length=1, max_length=30)
    difficulty: Literal["basic", "intermediate", "advanced", "mixed"]
    metric_policy: dict[str, Any]
    target_requirements: dict[str, Any]
    source: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=120)
    case_count: int = Field(ge=1, le=500)
    checksum: str = Field(min_length=64, max_length=64)


class BenchmarkPack(BaseModel):
    manifest: BenchmarkManifest
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class InstantiateBenchmarkRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)

