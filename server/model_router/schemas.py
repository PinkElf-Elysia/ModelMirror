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
ConnectionScope = Literal[
    "chat",
    "document",
    "image",
    "audio",
    "realtime",
    "video",
    "embedding",
    "rerank",
    "batch",
]
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
ProviderWorkloadEntryId = Literal[
    "agent_shadow",
    "meta_agent",
    "workflow_interactive_llm",
    "workflow_deployment_llm",
    "workflow_interactive_agent",
    "workflow_deployment_agent",
    "xpert",
    "xpert_app",
    "expert_team_planner",
    "expert_team_dag",
    "fusion",
    "route_agent",
    "team_chat",
    "rag_query_generate",
    "rag_processor_generate",
    "rag_embedding",
    "rag_rerank",
    "skill_rerank",
    "openrouter_batch",
    "chat_image",
    "chat_document_native",
    "rag_vision",
    "workflow_interactive_vision",
    "workflow_deployment_vision",
    "xpert_vision",
    "image_generation",
    "multimodal_transcription",
    "multimodal_speech",
    "xpert_transcription",
    "xpert_speech",
    "chat_audio_input",
    "chat_audio_output",
    "audio_generation",
    "multimodal_video_analysis",
    "chat_video",
    "video_generation",
    "realtime_voice",
]
ProviderWorkloadExecutionShape = Literal[
    "chat_text",
    "chat_tools",
    "chat_text_unary",
    "chat_json_object",
    "fusion_native",
    "embedding_vectors",
    "rerank_documents",
    "openrouter_batch_chat",
    "openrouter_batch_embeddings",
    "chat_image_stream",
    "chat_document_stream",
    "vision_json_unary",
    "image_generation",
    "audio_transcription",
    "audio_speech",
    "chat_audio_input",
    "chat_audio_output",
    "audio_generation_stream",
    "video_analysis_unary",
    "chat_video_stream",
    "video_generation_async",
    "realtime_voice_session",
]
ProviderMultimodalAdapterContract = Literal[
    "openrouter_chat_multimodal_v1",
    "openai_compatible_chat_multimodal_v1",
    "openrouter_chat_native_pdf_v1",
    "openrouter_images_v1",
    "openai_compatible_images_generations_v1",
    "openrouter_audio_transcription_json_v1",
    "openai_compatible_audio_transcription_multipart_v1",
    "openrouter_audio_speech_v1",
    "openai_compatible_audio_speech_v1",
    "openrouter_chat_audio_v1",
    "openrouter_audio_generation_stream_v1",
    "openrouter_chat_video_v1",
    "openrouter_video_jobs_v1",
    "openai_realtime_sdp_v1",
]
ProviderDispatchState = Literal[
    "not_dispatched",
    "dispatched",
    "delivery_pending",
    "confirmed",
    "uncertain",
]
MULTIMODAL_WORKLOAD_SHAPES: frozenset[str] = frozenset(
    {
        "chat_image_stream",
        "chat_document_stream",
        "vision_json_unary",
        "image_generation",
        "audio_transcription",
        "audio_speech",
        "chat_audio_input",
        "chat_audio_output",
        "audio_generation_stream",
        "video_analysis_unary",
        "chat_video_stream",
        "video_generation_async",
        "realtime_voice_session",
    }
)
ProviderWorkloadLocalFallbackMode = Literal["none", "extractive", "lexical"]
ProviderWorkloadRerankAccessMode = Literal["dedicated", "llm_json"]
ProviderWorkloadPolicyStatus = Literal[
    "legacy",
    "managed_required",
    "degraded_required",
]
ProviderWorkloadCertificationStatus = Literal[
    "not_run",
    "running",
    "passed",
    "failed",
    "uncertain",
    "stale",
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
    "document",
    "image",
    "audio",
    "realtime",
    "video",
    "embedding",
    "rerank",
    "batch",
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
    scopes: list[ConnectionScope] = Field(default_factory=list, max_length=9)
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
        max_length=9,
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


class ProviderChatRequiredActivationRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    no_open_p0_p1: bool
    acknowledge_fail_closed: bool
    drills: dict[str, bool] = Field(default_factory=dict, max_length=16)
    newapi_correlation_reference: SecretStr
    quota_decrement_verified: bool
    usage_log_verified: bool
    restart_persistence_verified: bool


class ProviderChatGateModelProgress(BaseModel):
    model_id: str
    success_count: int = 0
    minimum_success_count: int = 10
    ready: bool = False


class ProviderChatGateEvidenceSummary(BaseModel):
    evidence_kind: str
    passed: bool
    observed_at: str


class ProviderChatControlGateResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-chat-routing-v1"]
    feature_enabled: bool
    data_plane_integrated: bool = False
    policy_fingerprint: str
    configured_mode: ProviderChatControlMode
    required_activation_available: bool = False
    required_active: bool = False
    ready: bool = False
    epoch_id: str | None = None
    epoch_status: str | None = None
    epoch_started_at: str | None = None
    epoch_closed_at: str | None = None
    hard_failure_code: str | None = None
    minimum_request_count: int = 500
    minimum_observed_days: float = 14
    minimum_success_rate: float = 0.99
    request_count: int = 0
    success_count: int = 0
    hard_failure_count: int = 0
    observed_days: float = 0
    success_rate: float | None = None
    model_progress: list[ProviderChatGateModelProgress] = Field(default_factory=list)
    required_drills: list[str] = Field(default_factory=list)
    approval_recorded: bool = False
    acceptance_evidence_complete: bool = False
    acceptance_evidence: list[ProviderChatGateEvidenceSummary] = Field(
        default_factory=list
    )
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


class ProviderWorkloadCertificationRequest(BaseModel):
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str = Field(min_length=1, max_length=512)
    acknowledge_billed_call: bool
    adapter_contract: ProviderMultimodalAdapterContract | None = None
    candidate_model_ids: list[str] = Field(default_factory=list, max_length=5)
    judge_model_id: str | None = Field(default=None, max_length=512)
    rerank_access_mode: ProviderWorkloadRerankAccessMode | None = None

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        return _required_text(value, field_name="model_id", limit=512)

    @field_validator("candidate_model_ids")
    @classmethod
    def validate_candidate_model_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            model_id = _required_text(value, field_name="candidate_model_id", limit=512)
            if model_id in normalized:
                raise ValueError("candidate_model_ids must be unique")
            normalized.append(model_id)
        return normalized

    @field_validator("judge_model_id")
    @classmethod
    def validate_judge_model_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value, field_name="judge_model_id", limit=512)

    @model_validator(mode="after")
    def validate_fusion_profile(self) -> "ProviderWorkloadCertificationRequest":
        if self.execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            if self.adapter_contract is None:
                raise ValueError(
                    "multimodal certification requires adapter_contract"
                )
        elif self.adapter_contract is not None:
            raise ValueError(
                "adapter_contract is only valid for multimodal execution shapes"
            )
        if self.execution_shape == "fusion_native":
            if self.model_id != "openrouter/fusion":
                raise ValueError("fusion_native requires model_id=openrouter/fusion")
            if len(self.candidate_model_ids) < 2 or self.judge_model_id is None:
                raise ValueError(
                    "fusion_native requires 2-5 candidate_model_ids and judge_model_id"
                )
        elif self.candidate_model_ids or self.judge_model_id is not None:
            raise ValueError("fusion profile is only valid for fusion_native")
        if self.execution_shape == "rerank_documents":
            if self.rerank_access_mode is None:
                raise ValueError("rerank_documents requires rerank_access_mode")
        elif self.rerank_access_mode is not None:
            raise ValueError("rerank_access_mode is only valid for rerank_documents")
        if self.execution_shape in {"chat_text", "chat_tools"}:
            raise ValueError("chat_text and chat_tools reuse Provider Chat certification")
        return self


class ProviderWorkloadCertificationChecks(BaseModel):
    catalog_ok: bool = False
    model_present: bool = False
    http_ok: bool = False
    response_complete: bool = False
    content_observed: bool = False
    json_object_verified: bool = False
    fusion_profile_verified: bool = False
    actual_model_verified: bool = False
    embedding_vectors_verified: bool = False
    rerank_results_verified: bool = False
    batch_terminal_verified: bool = False
    media_format_verified: bool = False
    terminal_signal_verified: bool = False
    async_terminal_verified: bool = False
    manual_media_verified: bool = False


class ProviderWorkloadCertificationSummary(BaseModel):
    certification_id: str | None = None
    connection_id: str
    connection_name: str
    provider_kind: ConnectionKind
    execution_shape: ProviderWorkloadExecutionShape
    adapter_contract: ProviderMultimodalAdapterContract | None = None
    protocol_version: str | None = None
    status: ProviderWorkloadCertificationStatus = "not_run"
    can_run: bool
    blocked_reason: str | None = None
    checks: ProviderWorkloadCertificationChecks = Field(
        default_factory=ProviderWorkloadCertificationChecks
    )
    warning_codes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    requested_model: str | None = None
    actual_model: str | None = None
    candidate_model_ids: list[str] = Field(default_factory=list)
    judge_model_id: str | None = None
    profile_fingerprint: str | None = None
    rerank_access_mode: ProviderWorkloadRerankAccessMode | None = None
    vector_dimension: int | None = None
    batch_job_id: str | None = None
    batch_status: str | None = None
    certified_input_formats: list[str] = Field(default_factory=list)
    certified_voice: str | None = None
    certified_response_format: Literal["mp3", "wav"] | None = None
    certified_output_format: Literal["mp3"] | None = None
    supports_image_prompt: bool | None = None
    provider_dispatch_state: ProviderDispatchState | None = None
    retry_allowed: bool | None = None
    refresh_available: bool = False
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: str | None = None
    completed_at: str | None = None


class ProviderWorkloadCertificationListResponse(BaseModel):
    enabled: bool
    contract_version: Literal["modelmirror-provider-workload-routing-v1"]
    certifications: list[ProviderWorkloadCertificationSummary] = Field(
        default_factory=list
    )


class ProviderWorkloadBindingUpdate(BaseModel):
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str = Field(min_length=1, max_length=512)
    connection_id: str = Field(min_length=1, max_length=128)
    adapter_contract: ProviderMultimodalAdapterContract | None = None
    rerank_access_mode: ProviderWorkloadRerankAccessMode | None = None

    @field_validator("model_id", "connection_id")
    @classmethod
    def validate_required_text(cls, value: str, info) -> str:
        return _required_text(value, field_name=info.field_name, limit=512)

    @model_validator(mode="after")
    def validate_rerank_access_mode(self) -> "ProviderWorkloadBindingUpdate":
        if self.execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            if self.adapter_contract is None:
                raise ValueError("multimodal binding requires adapter_contract")
        elif self.adapter_contract is not None:
            raise ValueError(
                "adapter_contract is only valid for multimodal execution shapes"
            )
        if self.execution_shape == "rerank_documents":
            if self.rerank_access_mode is None:
                raise ValueError("rerank_documents binding requires rerank_access_mode")
        elif self.rerank_access_mode is not None:
            raise ValueError(
                "rerank_access_mode is only valid for rerank_documents binding"
            )
        return self


class ProviderWorkloadPolicyUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    local_fallback_mode: ProviderWorkloadLocalFallbackMode = "none"
    bindings: list[ProviderWorkloadBindingUpdate] = Field(
        default_factory=list,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_bindings(self) -> "ProviderWorkloadPolicyUpdate":
        keys = [
            (binding.execution_shape, binding.model_id)
            for binding in self.bindings
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("each execution_shape and model_id binding must be unique")
        return self


class ProviderWorkloadBindingSummary(BaseModel):
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str
    connection_id: str
    connection_name: str
    provider_kind: ConnectionKind | None = None
    certification_id: str
    certification_source: Literal["provider_chat", "provider_workload"]
    connection_fingerprint: str
    qualification_fingerprint: str
    adapter_contract: ProviderMultimodalAdapterContract | None = None
    protocol_version: str | None = None
    rerank_access_mode: ProviderWorkloadRerankAccessMode | None = None
    valid: bool
    reason_code: str


class ProviderWorkloadPolicyResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-workload-routing-v1"]
    entry_id: ProviderWorkloadEntryId
    feature_enabled: bool
    data_plane_integrated: bool = False
    configured_status: ProviderWorkloadPolicyStatus
    effective_status: ProviderWorkloadPolicyStatus
    revision: int
    policy_fingerprint: str
    local_fallback_mode: ProviderWorkloadLocalFallbackMode = "none"
    bindings: list[ProviderWorkloadBindingSummary] = Field(default_factory=list)
    approval_valid: bool = False
    blocking_reason_codes: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class ProviderWorkloadPolicyListResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-workload-routing-v1"]
    policies: list[ProviderWorkloadPolicyResponse] = Field(default_factory=list)


class ProviderWorkloadActivationRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    no_open_p0_p1: bool
    acknowledge_fail_closed: bool


class ProviderWorkloadDeactivationRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class ProviderWorkloadCallSummary(BaseModel):
    call_id: str
    run_id: str
    entry_id: ProviderWorkloadEntryId
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str
    connection_id: str | None = None
    adapter_contract: ProviderMultimodalAdapterContract | None = None
    protocol_version: str | None = None
    provider_dispatch_state: ProviderDispatchState | None = None
    call_sequence: int
    dispatched: bool
    status: str
    result_class: str | None = None
    error_code: str | None = None
    actual_model: str | None = None
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    generation_id_observed: bool | None = None
    generation_metadata_get_count: int | None = None
    generation_metadata_wait_ms: float | None = None
    created_at: str
    completed_at: str | None = None


class ProviderMultimodalCertificationRefreshRequest(BaseModel):
    acknowledge_poll_only: Literal[True]


class ProviderRealtimeCertificationSessionRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=512)
    adapter_contract: Literal["openai_realtime_sdp_v1"]
    offer_sdp: str = Field(min_length=1, max_length=128_000)
    acknowledge_billed_call: bool

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        return _required_text(value, field_name="model_id", limit=512)

    @field_validator("offer_sdp")
    @classmethod
    def validate_offer_sdp(cls, value: str) -> str:
        return _required_text(value, field_name="offer_sdp", limit=128_000)


class ProviderRealtimeCertificationCompleteRequest(BaseModel):
    media_observed: bool
    hangup_observed: bool


class ProviderRealtimeCertificationSessionResponse(BaseModel):
    certification_id: str
    status: ProviderWorkloadCertificationStatus
    answer_sdp: str | None = None
    expires_at: str | None = None
    provider_dispatch_state: ProviderDispatchState
    retry_allowed: bool = False


class ProviderWorkloadRunSummary(BaseModel):
    run_id: str
    entry_id: ProviderWorkloadEntryId
    policy_fingerprint: str
    parent_run_reference: str | None = None
    status: str
    result_class: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    created_at: str
    completed_at: str | None = None
    batch_job_id: str | None = None
    batch_status: str | None = None
    batch_request_count: int | None = None
    batch_completed_count: int | None = None
    batch_failed_count: int | None = None
    billing_authoritative: bool | None = None
    calls: list[ProviderWorkloadCallSummary] = Field(default_factory=list)


class ProviderWorkloadReceiptsResponse(BaseModel):
    contract_version: Literal["modelmirror-provider-workload-routing-v1"]
    runs: list[ProviderWorkloadRunSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class ProviderWorkloadOverview(BaseModel):
    contract_version: Literal["modelmirror-provider-workload-routing-v1"]
    entry_count: int
    feature_enabled_count: int
    managed_required_count: int
    degraded_required_count: int
    qualified_binding_count: int
    blocking_reason_codes: list[str] = Field(default_factory=list)
    policies: list[ProviderWorkloadPolicyResponse] = Field(default_factory=list)


class ProviderWorkloadPublicStatus(BaseModel):
    contract_version: Literal["modelmirror-provider-workload-routing-v1"]
    entry_id: ProviderWorkloadEntryId
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str
    feature_enabled: bool
    status: ProviderWorkloadPolicyStatus
    available: bool
    blocks_before_dispatch: bool
    reason_code: str
    certified_input_formats: list[str] = Field(default_factory=list)
    certified_voice: str | None = None
    certified_response_format: Literal["mp3", "wav"] | None = None
    certified_output_format: Literal["mp3"] | None = None
    supports_image_prompt: bool | None = None


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
