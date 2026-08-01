from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PromptProfileStatus = Literal["draft", "published", "archived"]


class PromptProfileVersion(BaseModel):
    version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    template: str
    argument_hint: str = ""
    tags: list[str] = Field(default_factory=list)
    public_app_allowed: bool = False
    checksum: str
    published_at: float


class PromptProfileDefinition(BaseModel):
    id: str
    slug: str
    name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    template: str
    argument_hint: str = ""
    tags: list[str] = Field(default_factory=list)
    public_app_allowed: bool = False
    status: PromptProfileStatus = "draft"
    draft_revision: int = Field(default=1, ge=1)
    published_version: int | None = None
    versions: list[PromptProfileVersion] = Field(default_factory=list)
    created_at: float
    updated_at: float


class PromptProfileBinding(BaseModel):
    profile_id: str = Field(min_length=1, max_length=160)
    version_policy: Literal["latest", "pinned"] = "latest"
    pinned_version: int | None = Field(default=None, ge=1)
    enabled: bool = True


class ResolvedPromptProfile(BaseModel):
    profile_id: str
    slug: str
    version: int = Field(ge=1)
    name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    template: str
    argument_hint: str = ""
    public_app_allowed: bool = False
    checksum: str
    source: Literal["direct", "plugin"] = "direct"
    source_plugin_id: str | None = None
    source_plugin_version: int | None = None
