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
ProviderChatCapability = Literal[
    "chat_text",
    "chat_tools",
    "chat_file_output",
]
ProviderChatControlMode = Literal[
    "legacy",
    "newapi_preferred",
    "newapi_required_default",
]
ProviderChatCanaryRunStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "uncertain",
    "preflight_fallback",
    "cancelled",
]
ProviderCatalogRefreshStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "uncertain",
]
ProviderCatalogModelStatus = Literal["active", "stale", "retired"]
OperationName = Literal[
    "chat",
    "analyze_document",
    "analyze_image",
    "generate_image",
    "transcribe",
    "synthesize_speech",
    "generate_audio",
    "analyze_audio",
    "realtime_voice",
    "analyze_video",
    "generate_video",
    "generate_world",
    "embed",
    "rerank",
]
OperationAvailabilityStatus = Literal[
    "available",
    "needs_configuration",
    "verification_required",
    "upstream_unavailable",
    "disabled",
]
OperationVerificationStatus = Literal[
    "verified",
    "contract_verified",
    "manual_required",
    "failed",
    "not_applicable",
]
OperationInteractionStatus = Literal["ready", "planned", "disabled"]
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


class ProviderCatalogPrice(BaseModel):
    currency: str | None = None
    unit: str | None = None
    input_price: str | None = None
    output_price: str | None = None
    source: str
    observed_at: str
    status: Literal["reported", "verified_source", "ambiguous", "unknown", "stale"]
    billing_authoritative: Literal[False] = False


class ProviderCatalogRefreshResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-catalog-v1"]
    refresh_id: str
    connection_id: str
    status: ProviderCatalogRefreshStatus
    model_ids: list[str] = Field(default_factory=list, max_length=500)
    model_count: int = 0
    checked_at: str
    truncated: bool = False
    catalog_fingerprint: str | None = None
    error_code: str | None = None
    message: str


class ProviderCatalogOfferingSummary(BaseModel):
    connection_id: str
    connection_name: str
    provider_kind: ConnectionKind
    model_id: str
    operation: OperationName
    access_mode: str
    capability_source: str
    inventory_status: ProviderCatalogModelStatus
    connection_health: ConnectionHealth
    verification_status: OperationVerificationStatus
    invocable: bool
    reason_codes: list[str] = Field(default_factory=list)
    refresh_id: str
    observed_at: str
    stale: bool = False
    pricing: ProviderCatalogPrice | None = None


class ProviderCatalogOfferingsResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-catalog-v1"]
    next_cursor: str | None = None
    offerings: list[ProviderCatalogOfferingSummary] = Field(default_factory=list)


class OperationReadinessProjection(BaseModel):
    operation: OperationName
    interaction_status: OperationInteractionStatus
    availability_status: OperationAvailabilityStatus
    verification_status: OperationVerificationStatus
    invocable: bool
    access_modes: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    observed_at: str | None = None
    stale: bool = False
    pricing: list[ProviderCatalogPrice] = Field(default_factory=list)


class ControlPlaneCatalogModel(BaseModel):
    model_id: str
    catalog_presence: Literal["present", "stale", "retired", "unknown"]
    display_source: Literal["runtime_discovered", "runtime_and_curated"] = (
        "runtime_discovered"
    )
    operations: list[OperationReadinessProjection] = Field(default_factory=list)


class ControlPlaneCatalogResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-catalog-v1"]
    catalog_revision: str
    generated_at: str
    stale: bool
    next_cursor: str | None = None
    models: list[ControlPlaneCatalogModel] = Field(default_factory=list)


class ControlPlaneOperationCount(BaseModel):
    operation: OperationName
    total: int
    invocable: int
    stale: int
    blocked: int


class ProviderControlPlaneOverview(BaseModel):
    contract_version: Literal["modelmirror-provider-catalog-v1"]
    catalog_revision: str
    generated_at: str
    provider_count: int
    online_provider_count: int
    discovered_model_count: int
    stale_model_count: int
    operation_counts: list[ControlPlaneOperationCount] = Field(default_factory=list)
    blocking_reason_codes: list[str] = Field(default_factory=list)
    default_qualification: Literal["not_evaluated"] = "not_evaluated"


class ProviderChatCertificationRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=512)
    capability: ProviderChatCapability = "chat_text"
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
    tool_call_observed: bool = False
    file_output_contract_observed: bool = False
    capability_verified: bool = False
    stream_completed: bool = False
    terminal_observed: bool = False


class ProviderChatCertificationSummary(BaseModel):
    certification_id: str | None = None
    connection_id: str
    connection_name: str
    capability: ProviderChatCapability = "chat_text"
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


class ProviderChatControlRouteUpdate(BaseModel):
    capability: ProviderChatCapability
    connection_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("connection_ids")
    @classmethod
    def validate_connection_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            connection_id = _required_text(
                value,
                field_name="connection_id",
                limit=128,
            )
            if connection_id in normalized:
                raise ValueError("connection_ids must be unique")
            normalized.append(connection_id)
        return normalized


class ProviderChatControlPolicyUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    mode: ProviderChatControlMode = "legacy"
    auto_enabled: bool = False
    stable_model_ids: list[str] = Field(default_factory=list, max_length=500)
    routes: list[ProviderChatControlRouteUpdate] = Field(
        default_factory=list,
        max_length=3,
    )

    @field_validator("stable_model_ids")
    @classmethod
    def validate_stable_model_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            model_id = _required_text(value, field_name="model_id", limit=512)
            if model_id not in normalized:
                normalized.append(model_id)
        return normalized

    @model_validator(mode="after")
    def validate_routes(self) -> "ProviderChatControlPolicyUpdate":
        capabilities = [route.capability for route in self.routes]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("each capability may appear only once")
        return self


class ProviderChatControlRouteSummary(BaseModel):
    capability: ProviderChatCapability
    connection_ids: list[str] = Field(default_factory=list)


class ProviderChatQualificationSummary(BaseModel):
    capability: ProviderChatCapability
    connection_id: str
    connection_name: str
    provider_kind: ConnectionKind
    model_id: str
    certification_id: str | None = None
    valid: bool
    reason_code: str


class ProviderChatControlPolicyResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-routing-v1"]
    feature_enabled: bool
    data_plane_integrated: bool = False
    configured_mode: ProviderChatControlMode
    effective_mode: ProviderChatControlMode
    auto_enabled: bool
    revision: int
    policy_fingerprint: str
    stable_model_ids: list[str] = Field(default_factory=list)
    routes: list[ProviderChatControlRouteSummary] = Field(default_factory=list)
    qualifications: list[ProviderChatQualificationSummary] = Field(
        default_factory=list
    )
    updated_at: str | None = None


class ProviderChatControlGateResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-routing-v1"]
    feature_enabled: bool
    data_plane_integrated: bool = False
    policy_fingerprint: str
    configured_mode: ProviderChatControlMode
    required_activation_available: bool = False
    ready: bool = False
    request_count: int = 0
    observed_days: float = 0
    success_rate: float | None = None
    blocking_reason_codes: list[str] = Field(default_factory=list)


class ProviderChatControlAttemptSummary(BaseModel):
    attempt_id: str
    run_id: str
    capability: ProviderChatCapability
    provider_kind: str
    connection_id: str | None = None
    position: int
    dispatched: bool
    status: str
    result_class: str | None = None
    error_code: str | None = None
    actual_model: str | None = None
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    total_tokens: int | None = None
    created_at: str
    completed_at: str | None = None


class ProviderChatControlRunSummary(BaseModel):
    run_id: str
    policy_fingerprint: str
    capability: ProviderChatCapability
    requested_model: str
    actual_model: str | None = None
    gateway: str
    strategy: str
    status: str
    result_class: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    total_tokens: int | None = None
    created_at: str
    completed_at: str | None = None
    attempts: list[ProviderChatControlAttemptSummary] = Field(default_factory=list)


class ProviderChatControlReceiptsResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-routing-v1"]
    runs: list[ProviderChatControlRunSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class ProviderChatControlPublicStatus(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-routing-v1"]
    feature_enabled: bool
    data_plane_integrated: bool = False
    model_id: str
    capability: ProviderChatCapability
    effective_mode: ProviderChatControlMode
    available: bool
    would_block: bool
    reason_code: str


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
