from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


XpertAppStatus = Literal["draft", "active", "disabled"]
XpertAppVisibility = Literal["unlisted"]


class XpertAppPolicy(BaseModel):
    allow_tools: bool = False
    allow_handoffs: bool = False
    allow_xpert_memory: bool = False
    allow_knowledge_read: bool = False
    allow_datax_read: bool = False


class XpertAppLimits(BaseModel):
    requests_per_minute: int = Field(default=30, ge=1, le=600)
    requests_per_day: int = Field(default=1000, ge=1, le=100_000)
    max_concurrency: int = Field(default=2, ge=1, le=20)


class XpertAppDeploymentRecord(BaseModel):
    revision: int = Field(ge=1)
    version: int = Field(ge=1)
    release_notes: str = ""
    deployed_at: float


class XpertAppApiKey(BaseModel):
    key_id: str
    name: str
    prefix: str
    key_hash: str
    limits: XpertAppLimits = Field(default_factory=XpertAppLimits)
    usage_day: str = ""
    requests_today: int = Field(default=0, ge=0)
    created_at: float
    last_used_at: float | None = None
    revoked_at: float | None = None
    expires_at: float | None = None


class XpertAppDefinition(BaseModel):
    app_id: str
    xpert_id: str
    slug: str
    name: str
    description: str = ""
    starters: list[str] = Field(default_factory=list)
    status: XpertAppStatus = "draft"
    visibility: XpertAppVisibility = "unlisted"
    pinned_version: int | None = Field(default=None, ge=1)
    deployment_revision: int = Field(default=0, ge=0)
    policy: XpertAppPolicy = Field(default_factory=XpertAppPolicy)
    limits: XpertAppLimits = Field(default_factory=XpertAppLimits)
    share_token_prefix: str
    share_token_hash: str
    share_usage_day: str = ""
    share_requests_today: int = Field(default=0, ge=0)
    share_last_used_at: float | None = None
    api_keys: list[XpertAppApiKey] = Field(default_factory=list)
    deployments: list[XpertAppDeploymentRecord] = Field(default_factory=list)
    created_at: float
    updated_at: float


class XpertAppAccessGrant(BaseModel):
    app_id: str
    app_slug: str
    access_type: Literal["share", "api_key"]
    credential_id: str
    credential_prefix: str
    limits: XpertAppLimits
    requests_today: int
