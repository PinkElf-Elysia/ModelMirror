from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ToolsetStatus = Literal["draft", "published", "archived"]
ToolsetKind = Literal["mcp", "openapi", "odata", "builtin"]
ToolMemoryMode = Literal["off", "run", "conversation"]
MCPTransport = Literal["stdio", "streamable_http", "legacy_sse"]
MCPNetworkPolicy = Literal["public_only", "trusted_private"]
APIAuthType = Literal[
    "none",
    "api_key",
    "bearer",
    "basic",
    "oauth2_client_credentials",
]
APIKeyLocation = Literal["header", "query"]


class SecretBinding(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    credential_id: str = Field(min_length=1, max_length=160)


class APIAuthProfile(BaseModel):
    auth_type: APIAuthType = "none"
    credential_id: str = Field(default="", max_length=160)
    api_key_name: str = Field(default="", max_length=160)
    api_key_location: APIKeyLocation = "header"
    username_credential_id: str = Field(default="", max_length=160)
    password_credential_id: str = Field(default="", max_length=160)
    client_id_credential_id: str = Field(default="", max_length=160)
    client_secret_credential_id: str = Field(default="", max_length=160)
    token_url: str = Field(default="", max_length=2048)
    scopes: list[str] = Field(default_factory=list, max_length=40)


class MCPConnectionProfile(BaseModel):
    transport: MCPTransport = "stdio"
    command: list[str] = Field(default_factory=list, max_length=64)
    url: str = Field(default="", max_length=2048)
    headers: list[SecretBinding] = Field(default_factory=list, max_length=40)
    environment: list[SecretBinding] = Field(default_factory=list, max_length=40)
    installed_project_id: str = Field(default="", max_length=160)
    working_directory: str = Field(default="", max_length=500)
    auto_start: bool = False
    auto_reconnect: bool = True
    reconnect_attempts: int = Field(default=2, ge=0, le=5)
    tool_prefix: str = Field(
        default="",
        max_length=80,
        pattern=r"^$|^[A-Za-z_][A-Za-z0-9_]{0,79}$",
    )
    network_policy: MCPNetworkPolicy = "public_only"
    timeout_seconds: int = Field(default=30, ge=5, le=300)
    api_base_url: str = Field(default="", max_length=2048)
    api_source_url: str = Field(default="", max_length=2048)
    api_source_label: str = Field(default="", max_length=300)
    api_spec_version: str = Field(default="", max_length=80)
    api_spec_hash: str = Field(default="", max_length=128)
    api_auth: APIAuthProfile = Field(default_factory=APIAuthProfile)
    response_limit_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        le=10 * 1024 * 1024,
    )
    redirect_limit: int = Field(default=3, ge=0, le=5)
    provider_id: str = Field(default="", max_length=120)
    provider_credential_id: str = Field(default="", max_length=160)
    provider_config: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    original_name: str = Field(min_length=1, max_length=200)
    alias: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    default_arguments: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    order: int = Field(default=0, ge=0, le=10_000)
    schema_hash: str = Field(default="", max_length=128)
    discovered_at: float = 0.0
    execution: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    requires_approval: bool = False
    sensitive: bool = False
    terminal: bool = False
    memory_mode: ToolMemoryMode = "off"
    parallel_safe: bool = False
    public_app_allowed: bool = False
    compatibility: Literal["compatible", "warning", "breaking"] = "compatible"
    compatibility_message: str = Field(default="", max_length=1000)

    @property
    def raw_name(self) -> str:
        return self.original_name

    @property
    def exposed_name(self) -> str:
        return self.alias.strip() or self.original_name

    @property
    def exposed_description(self) -> str:
        return self.description


class ToolsetVersion(BaseModel):
    version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    kind: ToolsetKind = "mcp"
    connection: MCPConnectionProfile
    tools: list[ToolDefinition] = Field(default_factory=list)
    schema_hash: str
    release_notes: str = Field(default="", max_length=2000)
    published_at: float


class ToolsetDefinition(BaseModel):
    id: str
    kind: ToolsetKind = "mcp"
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    privacy_policy: str = Field(default="", max_length=4000)
    disclaimer: str = Field(default="", max_length=4000)
    status: ToolsetStatus = "draft"
    revision: int = Field(default=1, ge=1)
    published_version: int | None = None
    connection: MCPConnectionProfile = Field(default_factory=MCPConnectionProfile)
    tools: list[ToolDefinition] = Field(default_factory=list)
    versions: list[ToolsetVersion] = Field(default_factory=list)
    runtime_status: str = "disconnected"
    runtime_session_id: str | None = None
    runtime_error: str = ""
    import_warnings: list[str] = Field(default_factory=list, max_length=100)
    drift_report: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class CredentialRecord(BaseModel):
    credential_id: str
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["header", "environment", "provider_key", "generic"] = "generic"
    prefix: str = ""
    masked_value: str = ""
    ciphertext: str
    status: Literal["active", "unavailable", "revoked"] = "active"
    created_at: float
    updated_at: float
