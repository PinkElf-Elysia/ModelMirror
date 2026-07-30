from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


TenantId = str
RouterEngine = Literal["sidecar", "shadow", "native_canary", "native"]
RoutingMode = Literal["auto", "fast", "quality", "cheap", "reliable", "offline"]
CompressionMode = Literal["auto", "off", "standard", "strong"]
ConnectionKind = Literal[
    "openrouter",
    "newapi",
    "openai_compatible",
    "openai",
]
ConnectionScope = Literal["chat", "audio", "realtime"]
ConnectionHealth = Literal["untested", "online", "offline", "disabled"]
CONNECTION_SCOPE_ORDER: tuple[ConnectionScope, ...] = (
    "chat",
    "audio",
    "realtime",
)


def default_connection_scopes(kind: ConnectionKind) -> list[ConnectionScope]:
    if kind == "openrouter":
        return ["chat", "audio"]
    if kind == "openai":
        return ["audio", "realtime"]
    return ["chat"]


def normalize_connection_scopes(
    values: list[ConnectionScope] | None,
    *,
    kind: ConnectionKind | None = None,
) -> list[ConnectionScope]:
    requested = values or (
        default_connection_scopes(kind) if kind is not None else []
    )
    normalized = [
        scope for scope in CONNECTION_SCOPE_ORDER if scope in requested
    ]
    if not normalized:
        raise ValueError("at least one connection scope is required")
    return normalized


def _required_text(value: str, *, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


class RouterConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ConnectionKind
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr
    scopes: list[ConnectionScope] = Field(default_factory=list, max_length=3)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _required_text(value, field_name="name", limit=120)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _required_text(value, field_name="base_url", limit=2048)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key is required")
        if len(value.get_secret_value()) > 20_000:
            raise ValueError("api_key exceeds 20000 characters")
        return value

    @model_validator(mode="after")
    def validate_scopes(self) -> "RouterConnectionCreate":
        self.scopes = normalize_connection_scopes(
            self.scopes,
            kind=self.kind,
        )
        return self


class RouterConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    scopes: list[ConnectionScope] | None = Field(
        default=None,
        max_length=3,
    )
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value, field_name="name", limit=120)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value, field_name="base_url", limit=2048)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(
        cls,
        value: list[ConnectionScope] | None,
    ) -> list[ConnectionScope] | None:
        if value is None:
            return None
        return normalize_connection_scopes(value)


class RouterConnection(BaseModel):
    id: str
    tenant_id: TenantId
    name: str
    kind: ConnectionKind
    base_url: str
    masked_key: str
    scopes: list[ConnectionScope]
    enabled: bool
    health: ConnectionHealth
    model_count: int = 0
    last_checked_at: str | None = None
    last_error_code: str | None = None
    last_error_hint: str | None = None
    created_at: str
    updated_at: str


class ConnectionTestResult(BaseModel):
    ok: bool
    health: ConnectionHealth
    model_count: int = 0
    models_preview: list[str] = Field(default_factory=list)
    message: str
    checked_at: str


class RouterPolicy(BaseModel):
    tenant_id: TenantId
    engine: RouterEngine = "sidecar"
    default_mode: RoutingMode = "auto"
    canary_percent: int = Field(default=0, ge=0, le=100)
    compression_mode: CompressionMode = "auto"
    updated_at: str | None = None


class RouterStatus(BaseModel):
    tenant_id: TenantId
    engine: RouterEngine
    default_mode: RoutingMode
    compression_mode: CompressionMode
    canary_percent: int
    connection_count: int
    online_connection_count: int
    model_count: int
    ready: bool
    redacted: bool = True
