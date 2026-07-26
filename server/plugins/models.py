from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

try:
    from server.prompts.models import ResolvedPromptProfile
except ModuleNotFoundError:
    from prompts.models import ResolvedPromptProfile


PluginStatus = Literal["draft", "published", "archived"]


class PluginToolsetReference(BaseModel):
    toolset_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    schema_hash: str = Field(min_length=1, max_length=128)


class PluginMiddlewarePreset(BaseModel):
    middleware_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=100, ge=0, le=10_000)
    config: dict[str, Any] = Field(default_factory=dict)


class PluginSkillDefinition(BaseModel):
    slug: str = Field(min_length=1, max_length=80)
    root: str = Field(min_length=1, max_length=300)
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)


class PluginVersion(BaseModel):
    version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    name: str
    slug: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    license: str = ""
    prompts: list[ResolvedPromptProfile] = Field(default_factory=list)
    skills: list[PluginSkillDefinition] = Field(default_factory=list)
    installed_skill_ids: list[str] = Field(default_factory=list)
    toolsets: list[PluginToolsetReference] = Field(default_factory=list)
    middleware_presets: list[PluginMiddlewarePreset] = Field(default_factory=list)
    package_checksum: str
    file_count: int = 0
    total_bytes: int = 0
    published_at: float


class PluginDefinition(BaseModel):
    id: str
    name: str
    slug: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    license: str = ""
    status: PluginStatus = "draft"
    draft_revision: int = Field(default=1, ge=1)
    published_version: int | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    package_checksum: str
    file_count: int = 0
    total_bytes: int = 0
    versions: list[PluginVersion] = Field(default_factory=list)
    created_at: float
    updated_at: float
