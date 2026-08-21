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
ProviderChatCertificationStatus = Literal[
    "not_run",
    "running",
    "passed",
    "failed",
    "uncertain",
    "stale",
]
ProviderChatCanaryRunStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "uncertain",
    "preflight_fallback",
    "cancelled",
]
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


class ProviderModelsRefreshResponse(BaseModel):
    connection_id: str
    ok: bool
    model_ids: list[str] = Field(default_factory=list, max_length=500)
    model_count: int = 0
    checked_at: str
    truncated: bool = False
    message: str


class ProviderChatCertificationRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=512)
    acknowledge_billed_call: bool

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        return _required_text(value, field_name="model_id", limit=512)


class ProviderChatCertificationChecks(BaseModel):
    catalog_ok: bool = False
    model_present: bool = False
    chat_http_ok: bool = False
    text_delta_observed: bool = False
    stream_completed: bool = False
    terminal_observed: bool = False


class ProviderChatCertificationSummary(BaseModel):
    certification_id: str | None = None
    connection_id: str
    connection_name: str
    status: ProviderChatCertificationStatus = "not_run"
    can_run: bool
    blocked_reason: str | None = None
    checks: ProviderChatCertificationChecks = Field(
        default_factory=ProviderChatCertificationChecks
    )
    warning_codes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    requested_model: str | None = None
    actual_model: str | None = None
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: str | None = None
    completed_at: str | None = None


class ProviderChatCertificationListResponse(BaseModel):
    enabled: bool
    contract_version: str
    certifications: list[ProviderChatCertificationSummary] = Field(
        default_factory=list
    )


class ProviderChatCanaryPolicyUpdate(BaseModel):
    connection_id: str = Field(min_length=1, max_length=128)
    enabled: bool

    @field_validator("connection_id")
    @classmethod
    def validate_connection_id(cls, value: str) -> str:
        return _required_text(value, field_name="connection_id", limit=128)


class ProviderChatCanaryPublicStatus(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-canary-v1"]
    feature_enabled: bool
    available: bool
    gateway: Literal["newapi_canary"] = "newapi_canary"
    model_id: str
    reason_code: str
    consent_revision: str


class ProviderChatCanaryModelStatus(BaseModel):
    model_id: str
    certification_id: str | None = None
    certification_status: ProviderChatCertificationStatus = "not_run"
    available: bool = False
    reason_code: str
    paused: bool = False
    pause_reason: str | None = None
    baseline_overlap: bool = False
    completed_at: str | None = None
    certification_expires_at: str | None = None


class ProviderChatCanaryConnectionStatus(BaseModel):
    connection_id: str
    connection_name: str
    eligible_connection: bool
    reason_code: str
    models: list[ProviderChatCanaryModelStatus] = Field(default_factory=list)


class ProviderChatCanaryRunSummary(BaseModel):
    run_id: str
    connection_id: str
    model_id: str
    status: ProviderChatCanaryRunStatus
    dispatched: bool
    result_class: str | None = None
    error_code: str | None = None
    checks: dict[str, bool] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list)
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    baseline_overlap: bool = False
    current_evidence: bool = False
    stale_reason: str | None = None
    created_at: str
    completed_at: str | None = None


class ProviderChatCanaryAggregate(BaseModel):
    connection_id: str
    model_id: str
    certification_id: str
    total_runs: int
    dispatched_runs: int
    succeeded_runs: int
    hard_failure_runs: int
    transient_failure_runs: int
    request_failure_runs: int
    cancelled_runs: int
    uncertain_runs: int
    preflight_fallback_runs: int
    success_rate: float | None = None
    average_ttft_ms: float | None = None
    average_e2e_ms: float | None = None
    total_tokens: int = 0
    baseline_overlap: bool = False
    last_completed_at: str | None = None


class ProviderChatCanaryAdminResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-canary-v1"]
    feature_enabled: bool
    policy_enabled: bool
    selected_connection_id: str | None = None
    consent_revision: str
    certification_max_age_seconds: int | None = None
    connections: list[ProviderChatCanaryConnectionStatus] = Field(
        default_factory=list
    )
    runs: list[ProviderChatCanaryRunSummary] = Field(default_factory=list)
    aggregates: list[ProviderChatCanaryAggregate] = Field(default_factory=list)


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


class RouterGateApprovalRequest(BaseModel):
    no_open_p0_p1: bool
    drills: dict[str, bool] = Field(default_factory=dict, max_length=16)
