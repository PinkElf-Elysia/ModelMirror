from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

import httpx

from .chat_control import ProviderChatControlService
from .egress import AuthorizedProviderTarget, ProviderEgressError
from .provider_catalog import ProviderCatalogService
from .provider_chat import ProviderChatTarget, ProviderChatTransport
from .provider_operations import (
    provider_operation_batch_model_matches,
    ProviderOperationTarget,
    ProviderOperationTransport,
    provider_operation_model_matches,
)
from .multimodal_control import (
    PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
    R8B_EXECUTION_SHAPES,
    R8C_EXECUTION_SHAPES,
    SYNTHETIC_AUDIO_WAV_BASE64,
    SYNTHETIC_AUDIO_WAV_BYTES,
    ProviderMultimodalTarget,
    ProviderMultimodalTransport,
    validate_multimodal_adapter,
)
from .repository import RouterCredentialUnavailable, RouterRepositoryError
from .schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingSummary,
    ProviderWorkloadCertificationChecks,
    ProviderWorkloadCertificationListResponse,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadCertificationSummary,
    ProviderWorkloadDeactivationRequest,
    ProviderWorkloadEntryId,
    ProviderWorkloadExecutionShape,
    ProviderWorkloadOverview,
    ProviderWorkloadPolicyListResponse,
    ProviderWorkloadPolicyResponse,
    ProviderWorkloadPolicyUpdate,
    ProviderWorkloadPublicStatus,
    ProviderWorkloadCallSummary,
    ProviderWorkloadReceiptsResponse,
    ProviderWorkloadRunSummary,
    RouterConnection,
    MULTIMODAL_WORKLOAD_SHAPES,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_WORKLOAD_CONTRACT_VERSION = "modelmirror-provider-workload-routing-v1"
R8C_AUDIO_PARAMETER_CONTRACT_VERSION = "modelmirror-provider-audio-parameters-v1"
PROVIDER_WORKLOAD_CERTIFICATION_ENABLED_ENV = (
    "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED"
)
SYNTHETIC_UNARY_PROMPT = "Reply with OK."
SYNTHETIC_JSON_PROMPT = 'Return exactly one JSON object: {"ok":true}'
SYNTHETIC_EMBEDDING_INPUTS = (
    "ModelMirror embedding certification one.",
    "ModelMirror embedding certification two.",
)
SYNTHETIC_RERANK_QUERY = "ModelMirror provider routing certification"
SYNTHETIC_RERANK_DOCUMENTS = (
    "ModelMirror routes requests through an explicit provider control plane.",
    "This document is unrelated to provider routing.",
    "Managed bindings select one exact provider model.",
)
SYNTHETIC_VISION_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGNkSPvPwMDAxAAGAA/nAWnOxjxZAAAAAElFTkSuQmCC"
)


def r8c_audio_parameter_profile_reason(
    execution_shape: str,
    profile: Mapping[str, object],
) -> str | None:
    """Return the stable reason why an R8C audio parameter profile is stale."""

    if execution_shape not in R8C_EXECUTION_SHAPES:
        return None
    if str(profile.get("audio_parameter_contract_version") or "") != (
        R8C_AUDIO_PARAMETER_CONTRACT_VERSION
    ):
        return "provider_multimodal_audio_parameter_contract_stale"
    if execution_shape == "audio_transcription":
        formats = profile.get("certified_input_formats")
        if formats != ["wav"]:
            return "provider_multimodal_audio_parameter_profile_invalid"
        return None
    voice = profile.get("certified_voice")
    response_format = profile.get("certified_response_format")
    upstream_format = profile.get("certified_upstream_format")
    if not isinstance(voice, str) or not voice.strip():
        return "provider_multimodal_audio_parameter_profile_invalid"
    if response_format not in {"mp3", "wav"}:
        return "provider_multimodal_audio_parameter_profile_invalid"
    if upstream_format not in {"mp3", "pcm", "wav"}:
        return "provider_multimodal_audio_parameter_profile_invalid"
    if (response_format == "wav") != (upstream_format in {"pcm", "wav"}):
        return "provider_multimodal_audio_parameter_profile_invalid"
    return None


def _build_synthetic_single_page_pdf() -> bytes:
    content = b"BT /F1 12 Tf 20 100 Td (ModelMirror PDF certification) Tj ET"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
    )
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)


SYNTHETIC_NATIVE_PDF_DATA_URL = "data:application/pdf;base64," + base64.b64encode(
    _build_synthetic_single_page_pdf()
).decode("ascii")
MAX_WORKLOAD_UNARY_RESPONSE_BYTES = 1024 * 1024
MAX_WORKLOAD_SSE_EVENT_BYTES = 256 * 1024
MAX_WORKLOAD_STREAM_BYTES = 4 * 1024 * 1024
WORKLOAD_RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_INITIAL_BATCH_NOT_FOUND_POLLS = 5
ENTRY_FEATURE_FLAGS: dict[ProviderWorkloadEntryId, tuple[str, ...]] = {
    "agent_shadow": ("MODEL_CONTROL_AGENT_SHADOW_ENABLED",),
    "meta_agent": ("MODEL_CONTROL_META_AGENT_ENABLED",),
    "workflow_interactive_llm": ("MODEL_CONTROL_WORKFLOW_LLM_ENABLED",),
    "workflow_deployment_llm": (
        "MODEL_CONTROL_WORKFLOW_LLM_ENABLED",
        "MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED",
    ),
    "workflow_interactive_agent": ("MODEL_CONTROL_WORKFLOW_AGENT_ENABLED",),
    "workflow_deployment_agent": (
        "MODEL_CONTROL_WORKFLOW_AGENT_ENABLED",
        "MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED",
    ),
    "xpert": ("MODEL_CONTROL_XPERT_ENABLED",),
    "xpert_app": ("MODEL_CONTROL_XPERT_APP_ENABLED",),
    "expert_team_planner": ("MODEL_CONTROL_EXPERT_TEAM_PLANNER_ENABLED",),
    "expert_team_dag": ("MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED",),
    "fusion": ("MODEL_CONTROL_FUSION_ENABLED",),
    "route_agent": ("MODEL_CONTROL_ROUTE_AGENT_ENABLED",),
    "team_chat": ("MODEL_CONTROL_TEAM_CHAT_ENABLED",),
    "rag_query_generate": ("MODEL_CONTROL_RAG_QUERY_ENABLED",),
    "rag_processor_generate": ("MODEL_CONTROL_RAG_PROCESSOR_ENABLED",),
    "rag_embedding": ("MODEL_CONTROL_RAG_EMBEDDING_ENABLED",),
    "rag_rerank": ("MODEL_CONTROL_RAG_RERANK_ENABLED",),
    "skill_rerank": ("MODEL_CONTROL_SKILL_RERANK_ENABLED",),
    "openrouter_batch": ("MODEL_CONTROL_OPENROUTER_BATCH_ENABLED",),
    "chat_image": ("MODEL_CONTROL_CHAT_IMAGE_ENABLED",),
    "chat_document_native": ("MODEL_CONTROL_CHAT_DOCUMENT_ENABLED",),
    "rag_vision": ("MODEL_CONTROL_RAG_VISION_ENABLED",),
    "workflow_interactive_vision": ("MODEL_CONTROL_WORKFLOW_VISION_ENABLED",),
    "workflow_deployment_vision": (
        "MODEL_CONTROL_WORKFLOW_VISION_ENABLED",
        "MODEL_CONTROL_WORKFLOW_VISION_DEPLOYMENT_ENABLED",
    ),
    "xpert_vision": ("MODEL_CONTROL_XPERT_VISION_ENABLED",),
    "image_generation": ("MODEL_CONTROL_IMAGE_GENERATION_ENABLED",),
    "multimodal_transcription": ("MODEL_CONTROL_TRANSCRIPTION_ENABLED",),
    "multimodal_speech": ("MODEL_CONTROL_SPEECH_ENABLED",),
    "xpert_transcription": ("MODEL_CONTROL_XPERT_AUDIO_ENABLED",),
    "xpert_speech": ("MODEL_CONTROL_XPERT_AUDIO_ENABLED",),
    "chat_audio_input": ("MODEL_CONTROL_CHAT_AUDIO_ENABLED",),
    "chat_audio_output": ("MODEL_CONTROL_CHAT_AUDIO_ENABLED",),
    "audio_generation": ("MODEL_CONTROL_AUDIO_GENERATION_ENABLED",),
    "multimodal_video_analysis": ("MODEL_CONTROL_VIDEO_ANALYSIS_ENABLED",),
    "chat_video": ("MODEL_CONTROL_CHAT_VIDEO_ENABLED",),
    "video_generation": ("MODEL_CONTROL_VIDEO_GENERATION_ENABLED",),
    "realtime_voice": ("MODEL_CONTROL_REALTIME_VOICE_ENABLED",),
}

ENTRY_ALLOWED_SHAPES: dict[
    ProviderWorkloadEntryId, frozenset[ProviderWorkloadExecutionShape]
] = {
    "agent_shadow": frozenset({"chat_tools"}),
    "meta_agent": frozenset({"chat_json_object"}),
    "workflow_interactive_llm": frozenset(
        {"chat_text", "chat_text_unary", "chat_json_object"}
    ),
    "workflow_deployment_llm": frozenset(
        {"chat_text", "chat_text_unary", "chat_json_object"}
    ),
    "workflow_interactive_agent": frozenset(
        {"chat_text", "chat_tools", "chat_json_object"}
    ),
    "workflow_deployment_agent": frozenset(
        {"chat_text", "chat_tools", "chat_json_object"}
    ),
    "xpert": frozenset({"chat_text", "chat_tools", "chat_json_object"}),
    "xpert_app": frozenset({"chat_text", "chat_tools", "chat_json_object"}),
    # The Agency planner emits a YAML plan through a non-streaming text request.
    "expert_team_planner": frozenset({"chat_text_unary"}),
    # The Agency DAG executes expert text steps and JSON acceptance checks.
    # These qualifications are independent and both must be explicit.
    "expert_team_dag": frozenset({"chat_text_unary", "chat_json_object"}),
    "fusion": frozenset({"chat_text", "fusion_native"}),
    "route_agent": frozenset({"chat_text"}),
    "team_chat": frozenset({"chat_text"}),
    "rag_query_generate": frozenset({"chat_text_unary"}),
    "rag_processor_generate": frozenset({"chat_json_object"}),
    "rag_embedding": frozenset({"embedding_vectors"}),
    "rag_rerank": frozenset({"rerank_documents"}),
    "skill_rerank": frozenset({"rerank_documents"}),
    "openrouter_batch": frozenset(
        {"openrouter_batch_chat", "openrouter_batch_embeddings"}
    ),
    "chat_image": frozenset({"chat_image_stream"}),
    "chat_document_native": frozenset({"chat_document_stream"}),
    "rag_vision": frozenset({"vision_json_unary"}),
    "workflow_interactive_vision": frozenset({"vision_json_unary"}),
    "workflow_deployment_vision": frozenset({"vision_json_unary"}),
    "xpert_vision": frozenset({"vision_json_unary"}),
    "image_generation": frozenset({"image_generation"}),
    "multimodal_transcription": frozenset({"audio_transcription"}),
    "multimodal_speech": frozenset({"audio_speech"}),
    "xpert_transcription": frozenset({"audio_transcription"}),
    "xpert_speech": frozenset({"audio_speech"}),
    "chat_audio_input": frozenset({"chat_audio_input"}),
    "chat_audio_output": frozenset({"chat_audio_output"}),
    "audio_generation": frozenset({"audio_generation_stream"}),
    "multimodal_video_analysis": frozenset({"video_analysis_unary"}),
    "chat_video": frozenset({"chat_video_stream"}),
    "video_generation": frozenset({"video_generation_async"}),
    "realtime_voice": frozenset({"realtime_voice_session"}),
}

ENTRY_ALLOWED_LOCAL_FALLBACKS: dict[ProviderWorkloadEntryId, frozenset[str]] = {
    entry_id: frozenset({"none"}) for entry_id in ENTRY_FEATURE_FLAGS
}
ENTRY_ALLOWED_LOCAL_FALLBACKS.update(
    {
        "rag_query_generate": frozenset({"none", "extractive"}),
        "rag_rerank": frozenset({"none", "lexical"}),
        "skill_rerank": frozenset({"none", "lexical"}),
    }
)

# Each data-plane PR adds its entry here only after the corresponding transport and
# fail-closed integration exist.  A control-plane-only entry must stay absent.
DATA_PLANE_INTEGRATED_ENTRIES: frozenset[ProviderWorkloadEntryId] = frozenset(
    {
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
    }
)


def _enabled(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() not in {"", "0", "false", "no", "off"}


def _fingerprint(value: object) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _clean_provider_evidence_identifier(
    value: object,
    *,
    max_length: int,
) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        return None
    return cleaned


class _WorkloadCertificationFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _WorkloadCertificationUncertain(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class _CertificationEvidence:
    checks: dict[str, bool]
    warning_codes: list[str]
    actual_model: str | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    vector_dimension: int | None = None

    @classmethod
    def create(cls) -> "_CertificationEvidence":
        return cls(
            checks={
                "catalog_ok": True,
                "model_present": True,
                "http_ok": False,
                "response_complete": False,
                "content_observed": False,
                "json_object_verified": False,
                "fusion_profile_verified": False,
                "actual_model_verified": False,
                "embedding_vectors_verified": False,
                "rerank_results_verified": False,
                "batch_terminal_verified": False,
            },
            warning_codes=[],
        )


class ProviderWorkloadCertificationService:
    """Certify non-stream text, JSON, and native Fusion without user data."""

    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        batch_poll_interval_seconds: float = 1.0,
    ) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.transport = ProviderChatTransport(router_service.egress_policy)
        self.operation_transport = ProviderOperationTransport(
            router_service.egress_policy
        )
        self.multimodal_transport = ProviderMultimodalTransport(
            router_service.egress_policy
        )
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                **ProviderChatTransport.client_kwargs(certification=True)
            )
        )
        self._batch_poll_interval_seconds = max(
            0.0, float(batch_poll_interval_seconds)
        )

    @staticmethod
    def enabled() -> bool:
        return _enabled(
            os.getenv(PROVIDER_WORKLOAD_CERTIFICATION_ENABLED_ENV),
            default=True,
        )

    def list(self) -> ProviderWorkloadCertificationListResponse:
        rows = self._repository_method("list_workload_certifications")(
            self.router_service.tenant_id
        )
        summaries: list[ProviderWorkloadCertificationSummary] = []
        for row in rows:
            try:
                connection = self.repository.get_connection(
                    self.router_service.tenant_id,
                    str(row["connection_id"]),
                )
            except RouterRepositoryError:
                continue
            summaries.append(self._summary(connection, row))
        return ProviderWorkloadCertificationListResponse(
            enabled=self.enabled(),
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            certifications=summaries,
        )

    async def run(
        self,
        connection_id: str,
        payload: ProviderWorkloadCertificationRequest,
        *,
        idempotency_key: str,
    ) -> ProviderWorkloadCertificationSummary:
        if not self.enabled():
            raise RouterServiceError(
                "provider_workload_certification_disabled",
                "Agent 与 Workflow Provider 资格认证已由部署配置关闭。",
                status_code=503,
            )
        if not payload.acknowledge_billed_call:
            raise RouterServiceError(
                "billed_call_acknowledgement_required",
                "运行资格认证前必须确认本次调用可能产生费用。",
                status_code=422,
            )
        clean_idempotency_key = str(idempotency_key or "").strip()
        if not clean_idempotency_key or len(clean_idempotency_key) > 200:
            raise RouterServiceError(
                "invalid_idempotency_key",
                "Idempotency-Key 必须是 1 至 200 个字符。",
                status_code=422,
            )
        if payload.execution_shape in {"chat_text", "chat_tools"}:
            raise RouterServiceError(
                "provider_workload_reuses_chat_certification",
                "该执行形态复用现有 Provider Chat 认证，请在 Chat 能力认证中运行。",
                status_code=409,
            )
        connection = self.repository.get_connection(
            self.router_service.tenant_id, connection_id
        )
        self._validate_connection(
            connection,
            payload.execution_shape,
            adapter_contract=payload.adapter_contract,
        )
        if (
            payload.execution_shape in MULTIMODAL_WORKLOAD_SHAPES
            and payload.execution_shape
            not in R8B_EXECUTION_SHAPES | R8C_EXECUTION_SHAPES
        ):
            raise RouterServiceError(
                "provider_multimodal_certification_not_integrated",
                "该多模态认证将在对应 R8 数据面批次接入；R8A 不会发送付费调用。",
                status_code=409,
            )

        try:
            profile = self._profile(payload)
        except _WorkloadCertificationFailure as exc:
            raise RouterServiceError(
                exc.code,
                "该音频模型缺少可认证的固定参数合同，未发送付费调用。",
                status_code=409,
            ) from exc
        profile_fingerprint = _fingerprint(profile)
        idempotency_hash = hashlib.sha256(
            clean_idempotency_key.encode("utf-8")
        ).hexdigest()
        existing = self._repository_method(
            "get_workload_certification_by_idempotency"
        )(
            self.router_service.tenant_id,
            connection_id,
            idempotency_hash,
        )
        if existing is not None:
            if (
                str(existing["execution_shape"]) != payload.execution_shape
                or str(existing["requested_model"]) != payload.model_id
                or str(existing["profile_fingerprint"]) != profile_fingerprint
            ):
                raise RouterServiceError(
                    "provider_workload_certification_idempotency_conflict",
                    "该 Idempotency-Key 已用于另一份 Workload 资格配置。",
                    status_code=409,
                )
            if (
                payload.execution_shape
                in {"openrouter_batch_chat", "openrouter_batch_embeddings"}
                and str(existing["status"]) == "uncertain"
            ):
                resumed = await self._resume_batch_certification(
                    connection,
                    existing,
                    payload,
                )
                if resumed is not None:
                    return resumed
            return self._summary(connection, existing)

        refreshed = await ProviderCatalogService(
            self.router_service
        ).refresh_connection(connection_id)
        if refreshed.status != "succeeded":
            raise RouterServiceError(
                "provider_model_catalog_unavailable",
                "模型目录刷新失败，未发送资格认证调用。",
                status_code=409,
            )
        if refreshed.truncated:
            raise RouterServiceError(
                "provider_workload_catalog_refresh_truncated",
                "最新目录已截断，不能作为精确模型资格证据。",
                status_code=409,
            )
        connection, api_key, connection_fingerprint = (
            self.repository.get_connection_credential_snapshot(
                self.router_service.tenant_id, connection_id
            )
        )
        self._validate_connection(
            connection,
            payload.execution_shape,
            adapter_contract=payload.adapter_contract,
        )
        refresh_record = next(
            (
                item
                for item in self.repository.list_catalog_refreshes(
                    self.router_service.tenant_id,
                    connection_id=connection_id,
                    limit=500,
                )
                if str(item["id"]) == refreshed.refresh_id
            ),
            None,
        )
        if (
            refresh_record is None
            or str(refresh_record["connection_fingerprint"])
            != connection_fingerprint
        ):
            raise RouterServiceError(
                "provider_workload_catalog_stale",
                "模型服务配置在目录刷新后发生变化，未发送资格认证调用。",
                status_code=409,
            )
        required_models = [payload.model_id]
        if payload.execution_shape == "fusion_native":
            required_models.extend(payload.candidate_model_ids)
            if payload.judge_model_id is not None:
                required_models.append(payload.judge_model_id)
        for model_id in dict.fromkeys(required_models):
            if not self.repository.list_catalog_models(
                self.router_service.tenant_id,
                connection_id=connection_id,
                model_id=model_id,
                status="active",
                limit=1,
            ):
                raise RouterServiceError(
                    "provider_workload_certification_model_not_found",
                    "资格所需的精确模型不在最新完整目录中，未发送付费调用。",
                    status_code=409,
                )

        multimodal_session_id = (
            f"mmcertsession_{uuid.uuid4().hex}"
            if payload.execution_shape in R8C_EXECUTION_SHAPES
            else None
        )
        try:
            row, created = self._repository_method(
                "claim_workload_certification"
            )(
                self.router_service.tenant_id,
                certification_id=f"workcert_{uuid.uuid4().hex}",
                connection_id=connection_id,
                connection_fingerprint=connection_fingerprint,
                contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
                execution_shape=payload.execution_shape,
                requested_model=payload.model_id,
                profile=profile,
                profile_fingerprint=profile_fingerprint,
                idempotency_key_hash=idempotency_hash,
                adapter_contract=payload.adapter_contract,
                protocol_version=(
                    PROVIDER_MULTIMODAL_PROTOCOL_VERSION
                    if payload.adapter_contract is not None
                    else None
                ),
                multimodal_session_id=multimodal_session_id,
            )
        except RouterRepositoryError as exc:
            code = str(exc)
            if code == "provider_workload_certification_already_running":
                raise RouterServiceError(
                    code,
                    "该连接已有一项 Workload 资格认证正在运行。",
                    status_code=409,
                ) from exc
            if code == "provider_workload_certification_idempotency_conflict":
                raise RouterServiceError(
                    code,
                    "该 Idempotency-Key 已用于另一份 Workload 资格配置。",
                    status_code=409,
                ) from exc
            if code == "provider_multimodal_session_idempotency_conflict":
                raise RouterServiceError(
                    code,
                    "该 Idempotency-Key 已用于另一份多模态资格配置。",
                    status_code=409,
                ) from exc
            if code == "provider_multimodal_dispatch_preconditions_changed":
                raise RouterServiceError(
                    code,
                    "模型服务配置在资格创建前发生变化，未发送付费调用。",
                    status_code=409,
                ) from exc
            if code == "provider_multimodal_session_store_busy":
                raise RouterServiceError(
                    code,
                    "资格存储当前繁忙，未创建认证记录或发送付费调用。",
                    status_code=503,
                ) from exc
            raise
        if not created:
            return self._summary(connection, row)

        evidence = _CertificationEvidence.create()
        status = "failed"
        error_code: str | None = None
        started = time.perf_counter()
        try:
            async with asyncio.timeout(60):
                async with self._client_factory() as client:
                    if payload.execution_shape in R8B_EXECUTION_SHAPES:
                        await self._run_r8b_certification(
                            client,
                            connection,
                            api_key,
                            payload,
                            evidence,
                            started,
                        )
                    elif payload.execution_shape in R8C_EXECUTION_SHAPES:
                        await self._run_r8c_audio_certification(
                            client,
                            connection,
                            api_key,
                            payload,
                            evidence,
                            started,
                            session_id=str(multimodal_session_id),
                            connection_fingerprint=connection_fingerprint,
                        )
                    elif payload.execution_shape in {
                        "embedding_vectors",
                        "rerank_documents",
                        "openrouter_batch_chat",
                        "openrouter_batch_embeddings",
                    }:
                        terminal = await self._run_operation_certification(
                            client,
                            connection,
                            api_key,
                            payload,
                            evidence,
                            started,
                            certification_id=str(row["id"]),
                            idempotency_key_hash=idempotency_hash,
                            connection_fingerprint=connection_fingerprint,
                        )
                        if not terminal:
                            status = "uncertain"
                            error_code = "provider_batch_certification_pending"
                    else:
                        target = ProviderChatTarget.create(
                            source="managed",
                            provider_kind=connection.kind,
                            base_url=connection.base_url,
                            api_key=api_key,
                            connection_id=connection.id,
                        )
                        request_payload = self._request_payload(payload)
                        authorized = await self.transport.authorize_managed_target(target)
                        request = self.transport.build_authorized_stream_request(
                            client,
                            target,
                            authorized,
                            request_payload,
                        )
                        response = await self.transport.send_authorized_stream(
                            client, request
                        )
                        try:
                            self._validate_status(response.status_code)
                            evidence.checks["http_ok"] = True
                            if payload.execution_shape == "fusion_native":
                                await self._consume_fusion_stream(
                                    response,
                                    evidence,
                                    started,
                                    expected_model=(
                                        payload.judge_model_id or payload.model_id
                                    ),
                                )
                            else:
                                await self._consume_unary_response(
                                    response,
                                    evidence,
                                    started,
                                    requested_model=payload.model_id,
                                    execution_shape=payload.execution_shape,
                                )
                        finally:
                            await response.aclose()
            if payload.execution_shape == "fusion_native":
                evidence.checks["fusion_profile_verified"] = True
            if status != "uncertain":
                status = "passed"
        except _WorkloadCertificationUncertain as exc:
            status = "uncertain"
            error_code = exc.code
        except _WorkloadCertificationFailure as exc:
            error_code = exc.code
        except httpx.ConnectTimeout:
            error_code = "provider_workload_connect_timeout"
        except httpx.ReadTimeout:
            error_code = "provider_workload_read_timeout"
        except httpx.TimeoutException:
            error_code = "provider_workload_timeout"
        except TimeoutError:
            if payload.execution_shape in {
                "openrouter_batch_chat",
                "openrouter_batch_embeddings",
            }:
                status = "uncertain"
                error_code = "provider_batch_certification_pending"
            else:
                error_code = "provider_workload_total_timeout"
        except httpx.ConnectError:
            error_code = "provider_workload_connect_error"
        except ProviderEgressError as exc:
            error_code = exc.code
        except RouterCredentialUnavailable:
            error_code = "provider_workload_credential_unavailable"
        except httpx.HTTPError:
            error_code = "provider_workload_transport_error"
        except asyncio.CancelledError:
            status = "uncertain"
            if multimodal_session_id is not None:
                session = self._repository_method(
                    "get_multimodal_certification_session"
                )(
                    self.router_service.tenant_id,
                    session_id=multimodal_session_id,
                )
                if session is not None and not bool(session["post_dispatched"]):
                    status = "failed"
            error_code = "provider_workload_cancelled"
            raise
        except Exception:
            error_code = "provider_workload_unexpected_error"
        finally:
            completed: dict[str, object]
            if multimodal_session_id is not None:
                session = self._repository_method(
                    "get_multimodal_certification_session"
                )(
                    self.router_service.tenant_id,
                    session_id=multimodal_session_id,
                )
                if session is not None and str(session["status"]) == "uncertain":
                    status = "uncertain"
                    error_code = str(session.get("error_code") or error_code or (
                        "provider_multimodal_certification_result_uncertain"
                    ))
                    completed = self._repository_method(
                        "complete_workload_certification"
                    )(
                        self.router_service.tenant_id,
                        str(row["id"]),
                        status=status,
                        checks=evidence.checks,
                        warning_codes=evidence.warning_codes,
                        error_code=error_code,
                        actual_model=evidence.actual_model,
                        ttft_ms=evidence.ttft_ms,
                        e2e_ms=(time.perf_counter() - started) * 1000,
                        prompt_tokens=evidence.prompt_tokens,
                        completion_tokens=evidence.completion_tokens,
                        total_tokens=evidence.total_tokens,
                        vector_dimension=evidence.vector_dimension,
                    )
                elif session is not None and str(session["status"]) == "running":
                    post_dispatched = bool(session["post_dispatched"])
                    if status != "passed" and post_dispatched and (
                        str(session["provider_dispatch_state"]) == "uncertain"
                    ):
                        status = "uncertain"
                        error_code = error_code or (
                            "provider_multimodal_certification_result_uncertain"
                        )
                    completed, _completed_session = self._repository_method(
                        "complete_multimodal_workload_certification"
                    )(
                        self.router_service.tenant_id,
                        str(row["id"]),
                        multimodal_session_id,
                        status=status,
                        checks=evidence.checks,
                        warning_codes=evidence.warning_codes,
                        error_code=error_code,
                        actual_model=evidence.actual_model,
                        ttft_ms=evidence.ttft_ms,
                        e2e_ms=(time.perf_counter() - started) * 1000,
                        prompt_tokens=evidence.prompt_tokens,
                        completion_tokens=evidence.completion_tokens,
                        total_tokens=evidence.total_tokens,
                        vector_dimension=evidence.vector_dimension,
                    )
                else:
                    completed = self._repository_method(
                        "complete_workload_certification"
                    )(
                        self.router_service.tenant_id,
                        str(row["id"]),
                        status=status,
                        checks=evidence.checks,
                        warning_codes=evidence.warning_codes,
                        error_code=error_code,
                        actual_model=evidence.actual_model,
                        ttft_ms=evidence.ttft_ms,
                        e2e_ms=(time.perf_counter() - started) * 1000,
                        prompt_tokens=evidence.prompt_tokens,
                        completion_tokens=evidence.completion_tokens,
                        total_tokens=evidence.total_tokens,
                        vector_dimension=evidence.vector_dimension,
                    )
            else:
                completed = self._repository_method(
                    "complete_workload_certification"
                )(
                    self.router_service.tenant_id,
                    str(row["id"]),
                    status=status,
                    checks=evidence.checks,
                    warning_codes=evidence.warning_codes,
                    error_code=error_code,
                    actual_model=evidence.actual_model,
                    ttft_ms=evidence.ttft_ms,
                    e2e_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=evidence.prompt_tokens,
                    completion_tokens=evidence.completion_tokens,
                    total_tokens=evidence.total_tokens,
                    vector_dimension=evidence.vector_dimension,
                )
        if status == "passed":
            self._record_certified_offering(connection, completed)
        return self._summary(connection, completed)

    async def refresh_multimodal_certification(
        self,
        certification_id: str,
    ) -> ProviderWorkloadCertificationSummary:
        """Refresh only persisted OpenRouter generation metadata.

        This path never calls the certification runner and therefore cannot
        submit another billed Provider POST.
        """

        session = self._repository_method(
            "get_multimodal_certification_session"
        )(
            self.router_service.tenant_id,
            certification_id=certification_id,
        )
        certification = self._repository_method("get_workload_certification")(
            self.router_service.tenant_id,
            certification_id,
        )
        if session is None or certification is None:
            raise RouterServiceError(
                "provider_multimodal_certification_session_not_found",
                "未找到该多模态资格会话。",
                status_code=404,
            )
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id,
                str(certification["connection_id"]),
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                "provider_multimodal_dispatch_preconditions_changed",
                "模型服务配置已变化，不能刷新该资格证据。",
                status_code=409,
            ) from exc
        if str(certification["status"]) in {"passed", "failed"}:
            return self._summary(connection, certification)
        if str(certification["status"]) == "running":
            raise RouterServiceError(
                "provider_multimodal_certification_refresh_in_progress",
                "该资格证据正在刷新，请稍后查看结果。",
                status_code=409,
            )
        if (
            str(certification.get("contract_version") or "")
            != PROVIDER_WORKLOAD_CONTRACT_VERSION
            or str(certification.get("protocol_version") or "")
            != PROVIDER_MULTIMODAL_PROTOCOL_VERSION
        ):
            raise RouterServiceError(
                "provider_multimodal_certification_contract_stale",
                "该资格证据的契约版本已过期，未执行上游查询。",
                status_code=409,
            )
        time_reason = ProviderChatControlService._certification_time_status(  # noqa: SLF001
            certification
        )
        if time_reason is not None:
            raise RouterServiceError(
                time_reason.replace("provider_chat_", "provider_workload_", 1),
                "该资格证据已过期或时间无效，未执行上游查询。",
                status_code=409,
            )
        if (
            str(certification["status"]) != "uncertain"
            or connection.kind != "openrouter"
            or not str(session.get("upstream_operation_id") or "").strip()
        ):
            raise RouterServiceError(
                "provider_multimodal_certification_not_refreshable",
                "该资格没有可执行只读刷新的上游证据。",
                status_code=409,
            )
        try:
            claimed, claimed_session = self._repository_method(
                "claim_multimodal_certification_refresh"
            )(
                self.router_service.tenant_id,
                certification_id,
                expected_contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
                expected_protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
            )
        except RouterRepositoryError as exc:
            code = str(exc)
            if code == "provider_multimodal_certification_session_not_found":
                status_code = 404
                message = "未找到该多模态资格会话。"
            elif code == "provider_multimodal_certification_refresh_in_progress":
                status_code = 409
                message = "该资格证据正在刷新，请稍后查看结果。"
            elif code == "provider_multimodal_dispatch_preconditions_changed":
                status_code = 409
                message = "模型服务配置已变化，未执行上游查询。"
            elif code == "provider_multimodal_certification_refresh_store_busy":
                status_code = 503
                message = "资格存储当前繁忙，未执行上游查询。"
            else:
                status_code = 409
                message = "该资格没有可执行只读刷新的上游证据。"
            raise RouterServiceError(code, message, status_code=status_code) from exc

        checks = _safe_json_object(
            json.loads(str(claimed.get("checks_json") or "{}"))
        )
        checks["actual_model_verified"] = False
        status = "uncertain"
        error_code: str | None = "provider_multimodal_actual_model_pending"
        actual_model: str | None = None
        cancelled = False
        try:
            connection, api_key, current_fingerprint = (
                self.repository.get_connection_credential_snapshot(
                    self.router_service.tenant_id,
                    str(claimed["connection_id"]),
                )
            )
            if current_fingerprint != str(claimed["connection_fingerprint"]):
                raise RouterRepositoryError(
                    "provider_multimodal_dispatch_preconditions_changed"
                )
            validate_multimodal_adapter(
                contract=str(claimed["adapter_contract"]),  # type: ignore[arg-type]
                execution_shape=str(claimed["execution_shape"]),  # type: ignore[arg-type]
                provider_kind=connection.kind,
                scopes=connection.scopes,
            )
            target = ProviderMultimodalTarget.create(
                provider_kind=connection.kind,
                connection_id=connection.id,
                base_url=connection.base_url,
                api_key=api_key,
                adapter_contract=str(claimed["adapter_contract"]),  # type: ignore[arg-type]
                execution_shape=str(claimed["execution_shape"]),  # type: ignore[arg-type]
            )
            async with asyncio.timeout(30):
                async with self._client_factory() as client:
                    actual_model = _clean_provider_evidence_identifier(
                        await self.multimodal_transport.fetch_openrouter_generation_model(
                            client,
                            target,
                            str(claimed_session["upstream_operation_id"]),
                        ),
                        max_length=512,
                    )
            if actual_model:
                checks["actual_model_verified"] = True
                if actual_model == str(claimed["requested_model"]):
                    status = "passed"
                    error_code = None
                else:
                    status = "failed"
                    error_code = "provider_workload_model_mismatch"
        except asyncio.CancelledError:
            cancelled = True
            error_code = "provider_workload_cancelled"
        except ProviderEgressError as exc:
            error_code = exc.code
        except RouterCredentialUnavailable:
            error_code = "provider_workload_credential_unavailable"
        except RouterRepositoryError as exc:
            error_code = str(exc)
        except (httpx.HTTPError, TimeoutError):
            error_code = "provider_multimodal_generation_metadata_unavailable"
        except Exception:
            error_code = "provider_multimodal_generation_metadata_unavailable"

        completed, _completed_session = self._repository_method(
            "complete_multimodal_certification_refresh"
        )(
            self.router_service.tenant_id,
            certification_id,
            status=status,
            checks={str(key): bool(value) for key, value in checks.items()},
            error_code=error_code,
            actual_model=actual_model,
        )
        if status == "passed":
            self._record_certified_offering(connection, completed)
        if cancelled:
            raise asyncio.CancelledError
        return self._summary(connection, completed)

    async def resume_pending_batch_certifications(self) -> int:
        """Resume only GET polling for persisted Batch certifications."""

        resumed = 0
        jobs = self._repository_method("list_provider_batch_jobs")(
            self.router_service.tenant_id,
            limit=500,
        )
        for job in jobs:
            if (
                str(job.get("status") or "")
                not in {"validating", "in_progress", "finalizing"}
                or not job.get("upstream_batch_id")
                or not job.get("certification_id")
            ):
                continue
            certification = self._repository_method(
                "get_workload_certification"
            )(
                self.router_service.tenant_id,
                str(job["certification_id"]),
            )
            if certification is None or str(certification["status"]) != "uncertain":
                continue
            try:
                connection = self.repository.get_connection(
                    self.router_service.tenant_id,
                    str(certification["connection_id"]),
                )
                payload = ProviderWorkloadCertificationRequest(
                    execution_shape=str(certification["execution_shape"]),  # type: ignore[arg-type]
                    model_id=str(certification["requested_model"]),
                    acknowledge_billed_call=True,
                )
                result = await self._resume_batch_certification(
                    connection,
                    certification,
                    payload,
                )
                if result is not None:
                    resumed += 1
            except (RouterRepositoryError, RouterServiceError, ValueError):
                continue
        return resumed

    async def _resume_batch_certification(
        self,
        connection: RouterConnection,
        certification: dict[str, object],
        payload: ProviderWorkloadCertificationRequest,
    ) -> ProviderWorkloadCertificationSummary | None:
        job = self._repository_method("get_provider_batch_job_by_certification")(
            self.router_service.tenant_id,
            str(certification["id"]),
        )
        if (
            job is None
            or str(job.get("status") or "")
            not in {"validating", "in_progress", "finalizing"}
            or not job.get("upstream_batch_id")
        ):
            return None
        current_fingerprint = self.repository.connection_config_fingerprint(
            self.router_service.tenant_id,
            connection.id,
        )
        if (
            str(certification["connection_fingerprint"]) != current_fingerprint
            or str(job["connection_fingerprint"]) != current_fingerprint
        ):
            return None
        try:
            running = self._repository_method("resume_workload_certification")(
                self.router_service.tenant_id,
                str(certification["id"]),
            )
        except RouterRepositoryError as exc:
            if str(exc) in {
                "provider_workload_certification_not_resumable",
                "provider_workload_certification_already_running",
            }:
                latest = self._repository_method("get_workload_certification")(
                    self.router_service.tenant_id,
                    str(certification["id"]),
                )
                return self._summary(connection, latest) if latest else None
            raise

        evidence = _CertificationEvidence.create()
        status = "failed"
        error_code: str | None = None
        started = time.perf_counter()
        try:
            api_key = self.repository.resolve_api_key(
                self.router_service.tenant_id,
                connection.id,
            )
            target = ProviderOperationTarget.create(
                provider_kind=connection.kind,
                connection_id=connection.id,
                base_url=connection.base_url,
                api_key=api_key,
            )
            async with asyncio.timeout(60):
                async with self._client_factory() as client:
                    await self._poll_batch_until_terminal(
                        client,
                        target,
                        payload,
                        evidence,
                        job_id=str(job["id"]),
                        upstream_batch_id=str(job["upstream_batch_id"]),
                    )
            status = "passed"
        except _WorkloadCertificationUncertain as exc:
            status = "uncertain"
            error_code = exc.code
        except _WorkloadCertificationFailure as exc:
            error_code = exc.code
        except (httpx.TimeoutException, TimeoutError):
            status = "uncertain"
            error_code = "provider_batch_certification_pending"
        except ProviderEgressError as exc:
            status = "uncertain"
            error_code = exc.code
        except (RouterCredentialUnavailable, httpx.HTTPError):
            status = "uncertain"
            error_code = "provider_batch_poll_unavailable"
        except asyncio.CancelledError:
            status = "uncertain"
            error_code = "provider_workload_cancelled"
            raise
        except Exception:
            status = "uncertain"
            error_code = "provider_workload_unexpected_error"
        finally:
            completed = self._repository_method(
                "complete_workload_certification"
            )(
                self.router_service.tenant_id,
                str(running["id"]),
                status=status,
                checks=evidence.checks,
                warning_codes=evidence.warning_codes,
                error_code=error_code,
                actual_model=evidence.actual_model,
                ttft_ms=evidence.ttft_ms,
                e2e_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=evidence.prompt_tokens,
                completion_tokens=evidence.completion_tokens,
                total_tokens=evidence.total_tokens,
                vector_dimension=evidence.vector_dimension,
            )
        if status == "passed":
            self._record_certified_offering(connection, completed)
        return self._summary(connection, completed)

    def _record_certified_offering(
        self,
        connection: RouterConnection,
        certification: dict[str, object],
    ) -> None:
        execution_shape = str(certification["execution_shape"])
        profile = _safe_json_object(
            json.loads(str(certification.get("profile_json") or "{}"))
        )
        mapping: dict[str, tuple[str, str]] = {
            "embedding_vectors": ("embed", "managed_embedding"),
            "openrouter_batch_chat": ("chat", "openrouter_batch"),
            "openrouter_batch_embeddings": ("embed", "openrouter_batch"),
        }
        if execution_shape == "rerank_documents":
            access = str(profile.get("rerank_access_mode") or "")
            if access not in {"dedicated", "llm_json"}:
                return
            operation, access_mode = "rerank", f"managed_rerank_{access}"
        elif execution_shape in mapping:
            operation, access_mode = mapping[execution_shape]
        else:
            return
        self._repository_method("upsert_certified_catalog_offering")(
            self.router_service.tenant_id,
            connection_id=connection.id,
            model_id=str(certification["requested_model"]),
            operation=operation,
            access_mode=access_mode,
        )

    @staticmethod
    def _profile(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        profile: dict[str, object] = {
            "execution_shape": payload.execution_shape,
            "model_id": payload.model_id,
            "adapter_contract": payload.adapter_contract,
            "protocol_version": (
                PROVIDER_MULTIMODAL_PROTOCOL_VERSION
                if payload.adapter_contract is not None
                else None
            ),
            "candidate_model_ids": list(payload.candidate_model_ids),
            "judge_model_id": payload.judge_model_id,
            "rerank_access_mode": payload.rerank_access_mode,
        }
        if payload.execution_shape == "audio_transcription":
            profile.update(
                {
                    "audio_parameter_contract_version": (
                        R8C_AUDIO_PARAMETER_CONTRACT_VERSION
                    ),
                    "certified_input_formats": ["wav"],
                }
            )
        elif payload.execution_shape == "audio_speech":
            voice, response_format, upstream_format = (
                ProviderWorkloadCertificationService._r8c_speech_contract(
                    payload.model_id,
                    openai_compatible=payload.adapter_contract
                    == "openai_compatible_audio_speech_v1",
                )
            )
            profile.update(
                {
                    "audio_parameter_contract_version": (
                        R8C_AUDIO_PARAMETER_CONTRACT_VERSION
                    ),
                    "certified_voice": voice,
                    "certified_response_format": response_format,
                    "certified_upstream_format": upstream_format,
                }
            )
        return profile

    @staticmethod
    def _request_payload(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "model": payload.model_id,
            "temperature": 0,
            "max_tokens": 64,
        }
        if payload.execution_shape == "chat_text_unary":
            request.update(
                {
                    "stream": False,
                    "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
                }
            )
        elif payload.execution_shape == "chat_json_object":
            request.update(
                {
                    "stream": False,
                    "messages": [{"role": "user", "content": SYNTHETIC_JSON_PROMPT}],
                    "response_format": {"type": "json_object"},
                }
            )
        else:
            request.update(
                {
                    "stream": True,
                    "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
                    "plugins": [
                        {
                            "id": "fusion",
                            "analysis_models": list(payload.candidate_model_ids),
                            "model": payload.judge_model_id,
                        }
                    ],
                }
            )
        return request

    async def _run_r8b_certification(
        self,
        client: httpx.AsyncClient,
        connection: RouterConnection,
        api_key: str,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        started: float,
    ) -> None:
        if payload.adapter_contract is None:
            raise _WorkloadCertificationFailure(
                "provider_multimodal_adapter_required"
            )
        target = ProviderMultimodalTarget.create(
            provider_kind=connection.kind,
            connection_id=connection.id,
            base_url=connection.base_url,
            api_key=api_key,
            adapter_contract=payload.adapter_contract,
            execution_shape=payload.execution_shape,
        )
        request_payload = self._r8b_request_payload(payload)
        authorized = await self.multimodal_transport.authorize(target)
        request = self.multimodal_transport.build_authorized_json_request(
            client,
            target,
            authorized,
            request_payload,
        )
        response = await self.multimodal_transport.send_authorized(client, request)
        try:
            self._validate_status(response.status_code)
            evidence.checks["http_ok"] = True
            if payload.execution_shape in {
                "chat_image_stream",
                "chat_document_stream",
            }:
                await self._consume_fusion_stream(
                    response,
                    evidence,
                    started,
                    expected_model=payload.model_id,
                )
            elif payload.execution_shape == "vision_json_unary":
                await self._consume_unary_response(
                    response,
                    evidence,
                    started,
                    requested_model=payload.model_id,
                    execution_shape="chat_json_object",
                )
            else:
                response_payload = await self._read_json_response(response)
                self._validate_image_generation_response(
                    response_payload,
                    evidence,
                    requested_model=payload.model_id,
                )
                evidence.ttft_ms = (time.perf_counter() - started) * 1000
        finally:
            await response.aclose()
        evidence.checks["multimodal_adapter_verified"] = True

    async def _run_r8c_audio_certification(
        self,
        client: httpx.AsyncClient,
        connection: RouterConnection,
        api_key: str,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        started: float,
        *,
        session_id: str,
        connection_fingerprint: str,
    ) -> None:
        try:
            await self._run_r8c_audio_certification_once(
                client,
                connection,
                api_key,
                payload,
                evidence,
                started,
                session_id=session_id,
                connection_fingerprint=connection_fingerprint,
            )
        except _WorkloadCertificationFailure as exc:
            if (
                exc.code == "provider_workload_response_too_large"
                and self._r8c_certification_was_dispatched(session_id)
            ):
                raise _WorkloadCertificationUncertain(exc.code) from exc
            raise
        except asyncio.CancelledError:
            raise
        except RouterRepositoryError as exc:
            if str(exc) == "provider_multimodal_dispatch_preconditions_changed":
                raise _WorkloadCertificationFailure(str(exc)) from exc
            raise
        except Exception as exc:
            error_code = self._r8c_transport_error_code(exc)
            if self._r8c_certification_was_dispatched(session_id):
                raise _WorkloadCertificationUncertain(error_code) from exc
            raise

    async def _run_r8c_audio_certification_once(
        self,
        client: httpx.AsyncClient,
        connection: RouterConnection,
        api_key: str,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        started: float,
        *,
        session_id: str,
        connection_fingerprint: str,
    ) -> None:
        if payload.adapter_contract is None:
            raise _WorkloadCertificationFailure(
                "provider_multimodal_adapter_required"
            )
        target = ProviderMultimodalTarget.create(
            provider_kind=connection.kind,
            connection_id=connection.id,
            base_url=connection.base_url,
            api_key=api_key,
            adapter_contract=payload.adapter_contract,
            execution_shape=payload.execution_shape,
        )
        authorized = await self.multimodal_transport.authorize(target)
        speech_response_format: str | None = None
        if payload.execution_shape == "audio_transcription":
            if payload.adapter_contract == "openrouter_audio_transcription_json_v1":
                request = self.multimodal_transport.build_authorized_json_request(
                    client,
                    target,
                    authorized,
                    {
                        "model": payload.model_id,
                        "input_audio": {
                            "data": SYNTHETIC_AUDIO_WAV_BASE64,
                            "format": "wav",
                        },
                        "language": "en",
                    },
                )
            else:
                request = self.multimodal_transport.build_authorized_multipart_request(
                    client,
                    target,
                    authorized,
                    data={"model": payload.model_id, "language": "en"},
                    files={
                        "file": (
                            "modelmirror-certification.wav",
                            SYNTHETIC_AUDIO_WAV_BYTES,
                            "audio/wav",
                        )
                    },
                )
        else:
            voice, _external_format, upstream_format = self._r8c_speech_contract(
                payload.model_id,
                openai_compatible=payload.adapter_contract
                == "openai_compatible_audio_speech_v1",
            )
            speech_response_format = upstream_format
            request = self.multimodal_transport.build_authorized_json_request(
                client,
                target,
                authorized,
                {
                    "model": payload.model_id,
                    "input": "OK",
                    "voice": voice,
                    "response_format": upstream_format,
                    "speed": 1.0,
                },
            )
        self._repository_method("update_multimodal_certification_session")(
            self.router_service.tenant_id,
            session_id,
            status="running",
            provider_dispatch_state="dispatched",
            post_dispatched=True,
            expected_connection_fingerprint=connection_fingerprint,
        )
        response = await self.multimodal_transport.send_authorized(client, request)
        actual_model: str | None = None
        generation_id = _clean_provider_evidence_identifier(
            response.headers.get("X-Generation-Id"),
            max_length=200,
        )
        try:
            self._validate_status(response.status_code)
            evidence.checks["http_ok"] = True
            if payload.execution_shape == "audio_transcription":
                response_payload = await self._read_json_response(response)
                text = response_payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise _WorkloadCertificationFailure(
                        "provider_multimodal_transcription_empty"
                    )
                candidate = response_payload.get("model")
                actual_model = _clean_provider_evidence_identifier(
                    candidate,
                    max_length=512,
                )
                if actual_model is None:
                    header_model = (
                        response.headers.get("x-model-id")
                        or response.headers.get("x-openrouter-model")
                    )
                    actual_model = _clean_provider_evidence_identifier(
                        header_model,
                        max_length=512,
                    )
                self._read_usage(response_payload, evidence)
                evidence.checks["content_observed"] = True
                evidence.checks["response_complete"] = True
                evidence.checks["media_format_verified"] = True
            else:
                audio = await self._read_audio_response(response)
                if not self._is_audio_payload(
                    audio,
                    content_type=response.headers.get("content-type", ""),
                    response_format=speech_response_format or "",
                ):
                    raise _WorkloadCertificationFailure(
                        "provider_multimodal_speech_output_invalid"
                    )
                evidence.checks["content_observed"] = True
                evidence.checks["response_complete"] = True
                evidence.checks["media_format_verified"] = True
                header_model = (
                    response.headers.get("x-model-id")
                    or response.headers.get("x-openrouter-model")
                )
                actual_model = _clean_provider_evidence_identifier(
                    header_model,
                    max_length=512,
                )
                evidence.ttft_ms = (time.perf_counter() - started) * 1000
        finally:
            await response.aclose()
        if actual_model is None:
            if connection.kind == "openrouter" and generation_id:
                self._repository_method(
                    "record_multimodal_certification_pending_evidence"
                )(
                    self.router_service.tenant_id,
                    session_id,
                    upstream_operation_id=generation_id,
                    checks=evidence.checks,
                    warning_codes=evidence.warning_codes,
                    error_code="provider_multimodal_actual_model_pending",
                    ttft_ms=evidence.ttft_ms,
                    e2e_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=evidence.prompt_tokens,
                    completion_tokens=evidence.completion_tokens,
                    total_tokens=evidence.total_tokens,
                    expected_connection_fingerprint=connection_fingerprint,
                )
                raise _WorkloadCertificationUncertain(
                    "provider_multimodal_actual_model_pending"
                )
            raise _WorkloadCertificationFailure(
                "provider_multimodal_actual_model_unverified"
            )
        self._repository_method("update_multimodal_certification_session")(
            self.router_service.tenant_id,
            session_id,
            status="running",
            provider_dispatch_state="confirmed",
            post_dispatched=True,
            upstream_operation_id=generation_id,
        )
        evidence.actual_model = actual_model
        evidence.checks["actual_model_verified"] = True
        if actual_model != payload.model_id:
            raise _WorkloadCertificationFailure("provider_workload_model_mismatch")
        evidence.checks["multimodal_adapter_verified"] = True

    def _r8c_certification_was_dispatched(
        self,
        session_id: str,
    ) -> bool:
        session = self._repository_method("get_multimodal_certification_session")(
            self.router_service.tenant_id,
            session_id=session_id,
        )
        return bool(
            session is not None
            and str(session["status"]) == "running"
            and bool(session["post_dispatched"])
        )

    @staticmethod
    def _r8c_transport_error_code(exc: Exception) -> str:
        if isinstance(exc, httpx.ConnectTimeout):
            return "provider_workload_connect_timeout"
        if isinstance(exc, httpx.ReadTimeout):
            return "provider_workload_read_timeout"
        if isinstance(exc, httpx.TimeoutException):
            return "provider_workload_timeout"
        if isinstance(exc, TimeoutError):
            return "provider_workload_total_timeout"
        if isinstance(exc, httpx.ConnectError):
            return "provider_workload_connect_error"
        if isinstance(exc, httpx.HTTPError):
            return "provider_workload_transport_error"
        return "provider_workload_unexpected_error"

    @staticmethod
    def _r8c_speech_parameters(
        model_id: str,
        *,
        openai_compatible: bool,
    ) -> tuple[str, str]:
        voice, _response_format, upstream_format = (
            ProviderWorkloadCertificationService._r8c_speech_contract(
                model_id,
                openai_compatible=openai_compatible,
            )
        )
        return voice, upstream_format

    @staticmethod
    def _r8c_speech_contract(
        model_id: str,
        *,
        openai_compatible: bool,
    ) -> tuple[str, str, str]:
        if openai_compatible:
            return "alloy", "mp3", "mp3"
        try:
            try:
                from server.multimodal.tts import (
                    ALLOWED_SPEECH_PROFILES,
                    speech_output_format,
                )
            except ModuleNotFoundError:
                # The container copies ``server/`` contents to ``/app`` and
                # imports this package as ``model_router`` rather than
                # ``server.model_router``.
                from multimodal.tts import (
                    ALLOWED_SPEECH_PROFILES,
                    speech_output_format,
                )

            voices = ALLOWED_SPEECH_PROFILES.get(model_id)
            if not voices:
                raise KeyError(model_id)
            response_format = speech_output_format(model_id)
            return (
                voices[0],
                response_format,
                "pcm" if response_format == "wav" else "mp3",
            )
        except (ImportError, KeyError):
            raise _WorkloadCertificationFailure(
                "provider_multimodal_speech_profile_missing"
            ) from None

    @staticmethod
    async def _read_audio_response(response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total_bytes = 0
        async for chunk in response.aiter_bytes(
            chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
        ):
            total_bytes += len(chunk)
            if total_bytes > MAX_WORKLOAD_UNARY_RESPONSE_BYTES:
                raise _WorkloadCertificationFailure(
                    "provider_workload_response_too_large"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _is_audio_payload(
        content: bytes,
        *,
        content_type: str,
        response_format: str,
    ) -> bool:
        if not content:
            return False
        if response_format == "pcm":
            parts = [part.strip() for part in content_type.split(";")]
            parameters = {
                key.strip().lower(): value.strip()
                for part in parts[1:]
                for key, separator, value in [part.partition("=")]
                if separator
            }
            return (
                parts[0].lower() == "audio/pcm"
                and parameters.get("rate") == "24000"
                and parameters.get("channels") == "1"
                and len(content) % 2 == 0
            )
        return (
            content.startswith(b"ID3")
            or (
                len(content) >= 2
                and content[0] == 0xFF
                and content[1] & 0xE0 == 0xE0
            )
            or (
                len(content) >= 12
                and content[:4] in {b"RIFF", b"RF64"}
                and content[8:12] == b"WAVE"
            )
        )

    @staticmethod
    def _r8b_request_payload(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        if payload.execution_shape == "image_generation":
            if payload.adapter_contract == "openrouter_images_v1":
                return {
                    "model": payload.model_id,
                    "prompt": "A single blue square on a white background.",
                    "n": 1,
                    "quality": "low",
                    "aspect_ratio": "1:1",
                }
            return {
                "model": payload.model_id,
                "prompt": "A single blue square on a white background.",
                "n": 1,
                "size": "256x256",
                "response_format": "b64_json",
            }
        if payload.execution_shape == "chat_document_stream":
            return {
                "model": payload.model_id,
                "stream": True,
                "temperature": 0,
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Reply with OK."},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "modelmirror-certification.pdf",
                                    "file_data": SYNTHETIC_NATIVE_PDF_DATA_URL,
                                },
                            },
                        ],
                    }
                ],
                "plugins": [{"id": "file-parser", "pdf": {"engine": "native"}}],
            }
        request: dict[str, object] = {
            "model": payload.model_id,
            "stream": payload.execution_shape == "chat_image_stream",
            "temperature": 0,
            "max_tokens": 64,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Reply with OK."
                                if payload.execution_shape == "chat_image_stream"
                                else 'Return exactly one JSON object: {"ok":true}'
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": SYNTHETIC_VISION_PNG_DATA_URL},
                        },
                    ],
                }
            ],
        }
        if payload.execution_shape == "vision_json_unary":
            request["response_format"] = {"type": "json_object"}
        return request

    @staticmethod
    def _validate_image_generation_response(
        payload: Mapping[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
    ) -> None:
        actual_model = payload.get("model")
        if isinstance(actual_model, str) and actual_model:
            evidence.actual_model = actual_model
            if actual_model != requested_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        else:
            evidence.warning_codes.append("actual_model_missing")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise _WorkloadCertificationFailure(
                "provider_multimodal_image_output_invalid"
            )
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise _WorkloadCertificationFailure(
                "provider_multimodal_image_output_invalid"
            )
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise _WorkloadCertificationFailure(
                "provider_multimodal_image_output_invalid"
            ) from exc
        if not (
            image.startswith(b"\x89PNG\r\n\x1a\n")
            or image.startswith(b"\xff\xd8\xff")
            or (len(image) >= 12 and image[8:12] == b"WEBP")
        ):
            raise _WorkloadCertificationFailure(
                "provider_multimodal_image_output_invalid"
            )
        evidence.checks["content_observed"] = True
        evidence.checks["response_complete"] = True
        ProviderWorkloadCertificationService._read_usage(payload, evidence)

    async def _run_operation_certification(
        self,
        client: httpx.AsyncClient,
        connection: RouterConnection,
        api_key: str,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        started: float,
        *,
        certification_id: str,
        idempotency_key_hash: str,
        connection_fingerprint: str,
    ) -> bool:
        target = ProviderOperationTarget.create(
            provider_kind=connection.kind,
            connection_id=connection.id,
            base_url=connection.base_url,
            api_key=api_key,
        )
        if payload.execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        }:
            return await self._run_batch_certification(
                client,
                target,
                payload,
                evidence,
                certification_id=certification_id,
                idempotency_key_hash=idempotency_key_hash,
                connection_fingerprint=connection_fingerprint,
            )

        operation = payload.execution_shape
        authorized = await self.operation_transport.authorize(
            target,
            operation,  # type: ignore[arg-type]
            rerank_access_mode=payload.rerank_access_mode,
        )
        request = self.operation_transport.build_authorized_request(
            client,
            target,
            authorized,
            method="POST",
            payload=self._operation_request_payload(payload),
        )
        response = await self.operation_transport.send_authorized(client, request)
        try:
            self._validate_status(response.status_code)
            evidence.checks["http_ok"] = True
            response_payload = await self._read_json_response(response)
            if operation == "embedding_vectors":
                self._validate_embedding_response(
                    response_payload,
                    evidence,
                    requested_model=payload.model_id,
                    provider_kind=connection.kind,
                )
            else:
                self._validate_rerank_response(
                    response_payload,
                    evidence,
                    requested_model=payload.model_id,
                    access_mode=payload.rerank_access_mode,
                )
            evidence.ttft_ms = (time.perf_counter() - started) * 1000
            return True
        finally:
            await response.aclose()

    @staticmethod
    def _operation_request_payload(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        if payload.execution_shape == "embedding_vectors":
            return {
                "model": payload.model_id,
                "input": list(SYNTHETIC_EMBEDDING_INPUTS),
                "encoding_format": "float",
            }
        if payload.rerank_access_mode == "dedicated":
            return {
                "model": payload.model_id,
                "query": SYNTHETIC_RERANK_QUERY,
                "documents": list(SYNTHETIC_RERANK_DOCUMENTS),
                "top_n": len(SYNTHETIC_RERANK_DOCUMENTS),
            }
        return {
            "model": payload.model_id,
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Return JSON with results containing every document index 0, 1, 2 "
                        "exactly once and a score from 0 to 1. Query: "
                        f"{SYNTHETIC_RERANK_QUERY} Documents: "
                        + " | ".join(SYNTHETIC_RERANK_DOCUMENTS)
                    ),
                }
            ],
        }

    @staticmethod
    async def _read_json_response(response: httpx.Response) -> dict[str, object]:
        chunks: list[bytes] = []
        total_bytes = 0
        async for chunk in response.aiter_bytes(
            chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
        ):
            total_bytes += len(chunk)
            if total_bytes > MAX_WORKLOAD_UNARY_RESPONSE_BYTES:
                raise _WorkloadCertificationFailure(
                    "provider_workload_response_too_large"
                )
            chunks.append(chunk)
        try:
            parsed = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            ) from exc
        if not isinstance(parsed, dict):
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            )
        return parsed

    @staticmethod
    def _validate_actual_model(
        response_payload: Mapping[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
    ) -> None:
        model = response_payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
            if model != requested_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        else:
            evidence.warning_codes.append("actual_model_missing")

    @classmethod
    def _validate_embedding_response(
        cls,
        response_payload: dict[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
        provider_kind: str,
    ) -> None:
        actual_model = response_payload.get("model")
        if not isinstance(actual_model, str) or not actual_model:
            raise _WorkloadCertificationFailure(
                "provider_embedding_actual_model_missing"
            )
        evidence.actual_model = actual_model
        if not provider_operation_model_matches(
            provider_kind=provider_kind,
            requested_model=requested_model,
            actual_model=actual_model,
        ):
            raise _WorkloadCertificationFailure(
                "provider_workload_model_mismatch"
            )
        evidence.checks["actual_model_verified"] = True
        if actual_model != requested_model:
            evidence.warning_codes.append(
                "actual_model_provider_prefix_omitted"
            )
        data = response_payload.get("data")
        if not isinstance(data, list) or len(data) != len(SYNTHETIC_EMBEDDING_INPUTS):
            raise _WorkloadCertificationFailure(
                "provider_embedding_vector_count_mismatch"
            )
        dimensions: set[int] = set()
        indexes: set[int] = set()
        for item in data:
            if not isinstance(item, dict):
                raise _WorkloadCertificationFailure(
                    "provider_embedding_invalid_vector"
                )
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or isinstance(index, bool):
                raise _WorkloadCertificationFailure(
                    "provider_embedding_invalid_index"
                )
            if not isinstance(vector, list) or not vector:
                raise _WorkloadCertificationFailure(
                    "provider_embedding_invalid_vector"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in vector
            ):
                raise _WorkloadCertificationFailure(
                    "provider_embedding_non_finite_vector"
                )
            indexes.add(index)
            dimensions.add(len(vector))
        if indexes != set(range(len(SYNTHETIC_EMBEDDING_INPUTS))):
            raise _WorkloadCertificationFailure(
                "provider_embedding_invalid_index"
            )
        if len(dimensions) != 1:
            raise _WorkloadCertificationFailure(
                "provider_embedding_dimension_mismatch"
            )
        evidence.vector_dimension = next(iter(dimensions))
        cls._read_usage(response_payload, evidence)
        evidence.checks["response_complete"] = True
        evidence.checks["content_observed"] = True
        evidence.checks["embedding_vectors_verified"] = True

    @classmethod
    def _validate_rerank_response(
        cls,
        response_payload: dict[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
        access_mode: str | None,
    ) -> None:
        cls._validate_actual_model(
            response_payload, evidence, requested_model=requested_model
        )
        cls._read_usage(response_payload, evidence)
        result_payload: object = response_payload
        if access_mode == "llm_json":
            choices = response_payload.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else None
            message = first.get("message") if isinstance(first, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise _WorkloadCertificationFailure(
                    "provider_rerank_empty_response"
                )
            try:
                result_payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise _WorkloadCertificationFailure(
                    "provider_rerank_invalid_json"
                ) from exc
        if not isinstance(result_payload, dict):
            raise _WorkloadCertificationFailure("provider_rerank_invalid_results")
        results = result_payload.get("results")
        if not isinstance(results, list) or len(results) != len(
            SYNTHETIC_RERANK_DOCUMENTS
        ):
            raise _WorkloadCertificationFailure(
                "provider_rerank_incomplete_results"
            )
        indexes: set[int] = set()
        for result in results:
            if not isinstance(result, dict):
                raise _WorkloadCertificationFailure(
                    "provider_rerank_invalid_results"
                )
            index = result.get("index")
            score = result.get("relevance_score", result.get("score"))
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0 <= float(score) <= 1
            ):
                raise _WorkloadCertificationFailure(
                    "provider_rerank_invalid_results"
                )
            indexes.add(index)
        if indexes != set(range(len(SYNTHETIC_RERANK_DOCUMENTS))):
            raise _WorkloadCertificationFailure(
                "provider_rerank_duplicate_or_missing_index"
            )
        evidence.checks["response_complete"] = True
        evidence.checks["content_observed"] = True
        evidence.checks["rerank_results_verified"] = True

    async def _run_batch_certification(
        self,
        client: httpx.AsyncClient,
        target: ProviderOperationTarget,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        *,
        certification_id: str,
        idempotency_key_hash: str,
        connection_fingerprint: str,
    ) -> bool:
        operation = payload.execution_shape
        endpoint = (
            "/v1/chat/completions"
            if operation == "openrouter_batch_chat"
            else "/v1/embeddings"
        )
        body: dict[str, object]
        if operation == "openrouter_batch_chat":
            body = {
                "model": payload.model_id,
                "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
                "max_tokens": 16,
                "temperature": 0,
            }
        else:
            body = {
                "model": payload.model_id,
                "input": SYNTHETIC_EMBEDDING_INPUTS[0],
                "encoding_format": "float",
            }
        request_payload = {
            "endpoint": endpoint,
            "model": payload.model_id,
            "requests": [{"custom_id": "modelmirror-certification", "body": body}],
        }
        request_fingerprint = _fingerprint(request_payload)
        authorized = await self.operation_transport.authorize(
            target, operation  # type: ignore[arg-type]
        )
        job_id = f"mmbatch_{uuid.uuid4().hex}"
        job, created = self._repository_method("claim_provider_batch_job")(
            self.router_service.tenant_id,
            job_id=job_id,
            connection_id=target.connection_id,
            connection_fingerprint=connection_fingerprint,
            endpoint=endpoint,
            model_id=payload.model_id,
            certification_id=certification_id,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            purpose="certification",
            request_count=1,
        )
        if not created:
            if str(job["request_fingerprint"]) != request_fingerprint:
                raise _WorkloadCertificationFailure(
                    "provider_batch_idempotency_conflict"
                )
            return str(job["status"]) == "completed"
        request = self.operation_transport.build_authorized_request(
            client,
            target,
            authorized,
            method="POST",
            payload=request_payload,
        )
        try:
            response = await self.operation_transport.send_authorized(client, request)
        except (httpx.HTTPError, TimeoutError) as exc:
            self._repository_method("mark_provider_batch_uncertain")(
                self.router_service.tenant_id,
                job_id,
                error_code="provider_batch_submission_uncertain",
            )
            raise _WorkloadCertificationUncertain(
                "provider_batch_submission_uncertain"
            ) from exc
        try:
            try:
                self._validate_status(response.status_code)
                submitted_payload = await self._read_json_response(response)
            except _WorkloadCertificationFailure as exc:
                self._repository_method("fail_provider_batch_submission")(
                    self.router_service.tenant_id,
                    job_id,
                    error_code=exc.code,
                )
                raise
        finally:
            await response.aclose()
        upstream_id = submitted_payload.get("id")
        upstream_status = str(submitted_payload.get("status") or "validating")
        if not isinstance(upstream_id, str) or not upstream_id:
            self._repository_method("fail_provider_batch_submission")(
                self.router_service.tenant_id,
                job_id,
                error_code="provider_batch_missing_upstream_id",
            )
            raise _WorkloadCertificationFailure(
                "provider_batch_missing_upstream_id"
            )
        initial_status = (
            upstream_status
            if upstream_status in {"validating", "in_progress", "finalizing"}
            else "validating"
        )
        self._repository_method("mark_provider_batch_submitted")(
            self.router_service.tenant_id,
            job_id,
            upstream_batch_id=upstream_id,
            status=initial_status,
        )
        return await self._poll_batch_until_terminal(
            client,
            target,
            payload,
            evidence,
            job_id=job_id,
            upstream_batch_id=upstream_id,
            initial_payload=submitted_payload,
        )

    async def _poll_batch_until_terminal(
        self,
        client: httpx.AsyncClient,
        target: ProviderOperationTarget,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        *,
        job_id: str,
        upstream_batch_id: str,
        initial_payload: dict[str, object] | None = None,
    ) -> bool:
        operation = payload.execution_shape
        polled_payload = initial_payload
        current_status = "validating"
        poll_count = 0
        batch_visible = False
        initial_not_found_polls = 0
        while True:
            if polled_payload is None:
                if poll_count and self._batch_poll_interval_seconds:
                    await asyncio.sleep(self._batch_poll_interval_seconds)
                authorized_poll = await self.operation_transport.authorize(
                    target,
                    operation,  # type: ignore[arg-type]
                    upstream_batch_id=upstream_batch_id,
                )
                poll_request = self.operation_transport.build_authorized_request(
                    client,
                    target,
                    authorized_poll,
                    method="GET",
                )
                try:
                    poll_response = await self.operation_transport.send_authorized(
                        client, poll_request
                    )
                    try:
                        if poll_response.status_code == 404 and not batch_visible:
                            initial_not_found_polls += 1
                            poll_count += 1
                            if initial_not_found_polls < MAX_INITIAL_BATCH_NOT_FOUND_POLLS:
                                polled_payload = None
                                continue
                            self._repository_method("update_provider_batch_job")(
                                self.router_service.tenant_id,
                                job_id,
                                status=current_status,
                                error_code="provider_batch_poll_not_visible",
                            )
                            raise _WorkloadCertificationUncertain(
                                "provider_batch_poll_not_visible"
                            )
                        self._validate_status(poll_response.status_code)
                        polled_payload = await self._read_json_response(poll_response)
                        batch_visible = True
                    finally:
                        await poll_response.aclose()
                except _WorkloadCertificationFailure as exc:
                    self._repository_method("update_provider_batch_job")(
                        self.router_service.tenant_id,
                        job_id,
                        status="failed",
                        error_code=exc.code,
                    )
                    raise
                except (httpx.HTTPError, TimeoutError) as exc:
                    self._repository_method("update_provider_batch_job")(
                        self.router_service.tenant_id,
                        job_id,
                        status=current_status,
                        error_code="provider_batch_poll_uncertain",
                    )
                    raise _WorkloadCertificationUncertain(
                        "provider_batch_poll_uncertain"
                    ) from exc
                poll_count += 1

            status = str(polled_payload.get("status") or "in_progress")
            normalized_status = (
                status
                if status
                in {
                    "validating",
                    "in_progress",
                    "finalizing",
                    "completed",
                    "failed",
                    "cancelled",
                    "expired",
                }
                else "in_progress"
            )
            current_status = normalized_status
            terminal = normalized_status in {
                "completed",
                "failed",
                "cancelled",
                "expired",
            }
            counts = polled_payload.get("request_counts")
            count_map = counts if isinstance(counts, dict) else {}
            completed_count = self._integer(count_map.get("completed")) or 0
            failed_count = self._integer(count_map.get("failed")) or 0
            usage = polled_payload.get("usage")
            usage_map = {
                key: value
                for key, value in (
                    usage.items() if isinstance(usage, dict) else []
                )
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            }
            self._repository_method("update_provider_batch_job")(
                self.router_service.tenant_id,
                job_id,
                status=normalized_status,
                completed_count=completed_count,
                failed_count=failed_count,
                usage=usage_map,
                error_code=(
                    None
                    if normalized_status in {"validating", "in_progress", "finalizing", "completed"}
                    else f"provider_batch_{normalized_status}"
                ),
            )
            if not terminal:
                polled_payload = None
                continue
            if (
                normalized_status != "completed"
                or completed_count != 1
                or failed_count != 0
            ):
                raise _WorkloadCertificationFailure(
                    f"provider_batch_{normalized_status if normalized_status != 'completed' else 'result_failed'}"
                )
            results = polled_payload.get("results")
            first_result = (
                results[0]
                if isinstance(results, list) and len(results) == 1
                else None
            )
            if (
                not isinstance(first_result, dict)
                or first_result.get("custom_id") != "modelmirror-certification"
            ):
                raise _WorkloadCertificationFailure(
                    "provider_batch_result_mismatch"
                )
            result_response = first_result.get("response")
            result_status = (
                result_response.get("status_code")
                if isinstance(result_response, dict)
                else None
            )
            result_body = (
                result_response.get("body")
                if isinstance(result_response, dict)
                else None
            )
            actual_model = (
                result_body.get("model") if isinstance(result_body, dict) else None
            )
            if not isinstance(result_status, int) or not 200 <= result_status < 300:
                raise _WorkloadCertificationFailure("provider_batch_result_failed")
            if not isinstance(actual_model, str) or not actual_model:
                raise _WorkloadCertificationFailure(
                    "provider_batch_actual_model_missing"
                )
            if not provider_operation_batch_model_matches(
                provider_kind=target.provider_kind,
                requested_model=payload.model_id,
                actual_model=actual_model,
            ):
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.actual_model = actual_model
            if actual_model != payload.model_id:
                evidence.warning_codes.append(
                    "actual_model_openrouter_alias_resolved"
                )
            evidence.checks["actual_model_verified"] = True
            evidence.checks["http_ok"] = True
            evidence.checks["response_complete"] = True
            evidence.checks["content_observed"] = True
            evidence.checks["batch_terminal_verified"] = True
            evidence.prompt_tokens = self._integer(usage_map.get("prompt_tokens"))
            evidence.completion_tokens = self._integer(
                usage_map.get("completion_tokens")
            )
            evidence.total_tokens = self._integer(usage_map.get("total_tokens"))
            return True

    @staticmethod
    async def _consume_unary_response(
        response: httpx.Response,
        evidence: _CertificationEvidence,
        started: float,
        *,
        requested_model: str,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> None:
        try:
            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in response.aiter_bytes(
                chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
            ):
                total_bytes += len(chunk)
                if total_bytes > MAX_WORKLOAD_UNARY_RESPONSE_BYTES:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_response_too_large"
                    )
                chunks.append(chunk)
            raw = b"".join(chunks)
            payload = json.loads(raw)
        except _WorkloadCertificationFailure:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            ) from exc
        if not isinstance(payload, dict):
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            )
        evidence.checks["response_complete"] = True
        ProviderWorkloadCertificationService._read_usage(payload, evidence)
        model = payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
            if model != requested_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        else:
            evidence.warning_codes.append("actual_model_missing")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _WorkloadCertificationFailure("provider_workload_empty_response")
        first = choices[0]
        if not isinstance(first, dict):
            raise _WorkloadCertificationFailure("provider_workload_empty_response")
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise _WorkloadCertificationFailure("provider_workload_empty_response")
        evidence.ttft_ms = (time.perf_counter() - started) * 1000
        evidence.checks["content_observed"] = True
        if execution_shape == "chat_json_object":
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError as exc:
                raise _WorkloadCertificationFailure(
                    "provider_workload_json_object_invalid"
                ) from exc
            if parsed_content != {"ok": True}:
                raise _WorkloadCertificationFailure(
                    "provider_workload_json_object_contract_mismatch"
                )
            evidence.checks["json_object_verified"] = True

    @staticmethod
    async def _consume_fusion_stream(
        response: httpx.Response,
        evidence: _CertificationEvidence,
        started: float,
        *,
        expected_model: str,
    ) -> None:
        buffer = b""
        total_bytes = 0
        terminal = False
        try:
            async for chunk in response.aiter_bytes(
                chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
            ):
                total_bytes += len(chunk)
                if total_bytes > MAX_WORKLOAD_STREAM_BYTES:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_stream_too_large"
                    )
                buffer += chunk
                while True:
                    delimiters = [
                        (index, delimiter)
                        for delimiter in (b"\n\n", b"\r\n\r\n", b"\r\r")
                        if (index := buffer.find(delimiter)) >= 0
                    ]
                    if not delimiters:
                        break
                    index, delimiter = min(delimiters, key=lambda item: item[0])
                    event_bytes = buffer[:index]
                    buffer = buffer[index + len(delimiter) :]
                    if len(event_bytes) > MAX_WORKLOAD_SSE_EVENT_BYTES:
                        raise _WorkloadCertificationFailure(
                            "provider_workload_sse_event_too_large"
                        )
                    try:
                        event = event_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                    except UnicodeDecodeError as exc:
                        raise _WorkloadCertificationFailure(
                            "provider_workload_invalid_sse"
                        ) from exc
                    terminal = (
                        ProviderWorkloadCertificationService._consume_sse_event(
                            event, evidence, started, expected_model=expected_model
                        )
                        or terminal
                    )
                if len(buffer) > MAX_WORKLOAD_SSE_EVENT_BYTES:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_sse_event_too_large"
                    )
            if buffer.strip():
                try:
                    event = buffer.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                except UnicodeDecodeError as exc:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_invalid_sse"
                    ) from exc
                terminal = (
                    ProviderWorkloadCertificationService._consume_sse_event(
                        event, evidence, started, expected_model=expected_model
                    )
                    or terminal
                )
        except _WorkloadCertificationFailure:
            raise
        except Exception as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_stream_interrupted"
            ) from exc
        evidence.checks["response_complete"] = True
        if not evidence.checks["content_observed"]:
            raise _WorkloadCertificationFailure("provider_workload_empty_stream")
        if not terminal:
            raise _WorkloadCertificationFailure(
                "provider_workload_missing_terminal"
            )

    @staticmethod
    def _consume_sse_event(
        event: str,
        evidence: _CertificationEvidence,
        started: float,
        *,
        expected_model: str,
    ) -> bool:
        data_lines = [
            line[5:].lstrip()
            for line in event.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            return False
        data = "\n".join(data_lines)
        if data == "[DONE]":
            return True
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_sse"
            ) from exc
        if not isinstance(payload, dict):
            raise _WorkloadCertificationFailure("provider_workload_invalid_sse")
        ProviderWorkloadCertificationService._read_usage(payload, evidence)
        model = payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
            if model != expected_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return False
        terminal = False
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            terminal = terminal or bool(finish_reason)
            delta = choice.get("delta")
            content = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(content, str) and content:
                if evidence.ttft_ms is None:
                    evidence.ttft_ms = (time.perf_counter() - started) * 1000
                evidence.checks["content_observed"] = True
        return terminal

    @staticmethod
    def _read_usage(
        payload: Mapping[str, object], evidence: _CertificationEvidence
    ) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        evidence.prompt_tokens = ProviderWorkloadCertificationService._integer(
            usage.get("prompt_tokens")
        )
        evidence.completion_tokens = ProviderWorkloadCertificationService._integer(
            usage.get("completion_tokens")
        )
        evidence.total_tokens = ProviderWorkloadCertificationService._integer(
            usage.get("total_tokens")
        )

    @staticmethod
    def _validate_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        code = {
            401: "provider_workload_http_401",
            403: "provider_workload_http_403",
            404: "provider_workload_http_404",
            429: "provider_workload_http_429",
        }.get(
            status_code,
            "provider_workload_http_5xx"
            if status_code >= 500
            else "provider_workload_http_error",
        )
        raise _WorkloadCertificationFailure(code)

    def _validate_connection(
        self,
        connection: RouterConnection,
        execution_shape: ProviderWorkloadExecutionShape,
        *,
        adapter_contract: str | None = None,
    ) -> None:
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled", "该模型服务已停用。", status_code=409
            )
        if execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            if adapter_contract is None:
                raise RouterServiceError(
                    "provider_multimodal_adapter_required",
                    "多模态资格必须选择明确的 Adapter。",
                    status_code=422,
                )
            validate_multimodal_adapter(
                contract=adapter_contract,  # type: ignore[arg-type]
                execution_shape=execution_shape,
                provider_kind=connection.kind,
                scopes=connection.scopes,
            )
            return
        required_scope = self._required_scope(execution_shape)
        if required_scope not in connection.scopes:
            raise RouterServiceError(
                f"connection_{required_scope}_scope_required",
                f"Workload 资格要求连接启用 {required_scope} scope。",
                status_code=409,
            )
        if execution_shape == "fusion_native" and connection.kind != "openrouter":
            raise RouterServiceError(
                "provider_workload_fusion_requires_openrouter",
                "原生 Fusion 资格只允许 OpenRouter 类型连接。",
                status_code=409,
            )
        if execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        } and connection.kind != "openrouter":
            raise RouterServiceError(
                "provider_workload_batch_requires_openrouter",
                "Batch 资格只允许 OpenRouter 类型连接。",
                status_code=409,
            )

    @staticmethod
    def _required_scope(execution_shape: ProviderWorkloadExecutionShape) -> str:
        if execution_shape == "embedding_vectors":
            return "embedding"
        if execution_shape == "rerank_documents":
            return "rerank"
        if execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        }:
            return "batch"
        if execution_shape == "chat_document_stream":
            return "document"
        if execution_shape in {
            "chat_image_stream",
            "vision_json_unary",
            "image_generation",
        }:
            return "image"
        if execution_shape in {
            "audio_transcription",
            "audio_speech",
            "chat_audio_input",
            "chat_audio_output",
            "audio_generation_stream",
        }:
            return "audio"
        if execution_shape in {
            "video_analysis_unary",
            "chat_video_stream",
            "video_generation_async",
        }:
            return "video"
        if execution_shape == "realtime_voice_session":
            return "realtime"
        return "chat"

    def _summary(
        self,
        connection: RouterConnection,
        row: dict[str, object],
    ) -> ProviderWorkloadCertificationSummary:
        status = str(row["status"])
        blocked_reason: str | None = None
        try:
            fingerprint = self.repository.connection_config_fingerprint(
                self.router_service.tenant_id, connection.id
            )
        except RouterRepositoryError:
            fingerprint = ""
        if str(row["connection_fingerprint"]) != fingerprint and status != "running":
            status = "stale"
            blocked_reason = "provider_workload_connection_fingerprint_changed"
        elif (
            status != "running"
            and str(row.get("contract_version") or "")
            != PROVIDER_WORKLOAD_CONTRACT_VERSION
        ):
            status = "stale"
            blocked_reason = "provider_workload_contract_stale"
        elif (
            status != "running"
            and str(row["execution_shape"]) in MULTIMODAL_WORKLOAD_SHAPES
            and str(row.get("protocol_version") or "")
            != PROVIDER_MULTIMODAL_PROTOCOL_VERSION
        ):
            status = "stale"
            blocked_reason = "provider_multimodal_protocol_stale"
        elif status == "passed":
            time_reason = ProviderChatControlService._certification_time_status(  # noqa: SLF001
                row
            )
            if time_reason is not None:
                status = "stale"
                blocked_reason = time_reason.replace(
                    "provider_chat_", "provider_workload_", 1
                )
        profile = _safe_json_object(json.loads(str(row["profile_json"] or "{}")))
        required_scope = self._required_scope(
            str(row["execution_shape"])  # type: ignore[arg-type]
        )
        if blocked_reason is None and not connection.enabled:
            blocked_reason = "connection_disabled"
        elif blocked_reason is None and required_scope not in connection.scopes:
            blocked_reason = f"connection_{required_scope}_scope_required"
        elif blocked_reason is None and connection.health != "online":
            blocked_reason = "provider_connection_not_online"
        elif blocked_reason is None:
            try:
                self.repository.resolve_api_key(
                    self.router_service.tenant_id, connection.id
                )
            except RouterCredentialUnavailable:
                blocked_reason = "provider_workload_credential_unavailable"
        checks = _safe_json_object(json.loads(str(row["checks_json"] or "{}")))
        if (
            str(row["execution_shape"]) in R8C_EXECUTION_SHAPES
            and status == "passed"
            and (
                not row.get("actual_model")
                or checks.get("actual_model_verified") is not True
            )
        ):
            status = "stale"
            blocked_reason = "provider_multimodal_actual_model_unverified"
        if str(row["execution_shape"]) in R8C_EXECUTION_SHAPES and status == "passed":
            profile_reason = r8c_audio_parameter_profile_reason(
                str(row["execution_shape"]),
                profile,
            )
            if profile_reason is not None:
                status = "stale"
                blocked_reason = profile_reason
        provider_dispatch_state: str | None = None
        retry_allowed: bool | None = None
        refresh_available = False
        if str(row["execution_shape"]) in R8C_EXECUTION_SHAPES:
            session = self._repository_method(
                "get_multimodal_certification_session"
            )(
                self.router_service.tenant_id,
                certification_id=str(row["id"]),
            )
            retry_allowed = False
            if session is not None:
                provider_dispatch_state = str(
                    session.get("provider_dispatch_state") or ""
                ) or None
                refresh_time_reason = (
                    ProviderChatControlService._certification_time_status(  # noqa: SLF001
                        row
                    )
                    if str(row["status"]) == "uncertain"
                    else None
                )
                refresh_available = bool(
                    str(row["status"]) == "uncertain"
                    and str(session.get("status") or "") == "uncertain"
                    and str(session.get("provider_dispatch_state") or "")
                    == "confirmed"
                    and session.get("post_dispatched")
                    and session.get("upstream_operation_id")
                    and connection.kind == "openrouter"
                    and blocked_reason is None
                    and all(
                        checks.get(name) is True
                        for name in (
                            "http_ok",
                            "content_observed",
                            "response_complete",
                            "media_format_verified",
                        )
                    )
                    and str(row.get("contract_version") or "")
                    == PROVIDER_WORKLOAD_CONTRACT_VERSION
                    and str(row.get("protocol_version") or "")
                    == PROVIDER_MULTIMODAL_PROTOCOL_VERSION
                    and refresh_time_reason is None
                )
        warnings = json.loads(str(row["warnings_json"] or "[]"))
        batch_job = None
        if str(row["execution_shape"]).startswith("openrouter_batch_"):
            batch_job = next(
                (
                    item
                    for item in self._repository_method("list_provider_batch_jobs")(
                        self.router_service.tenant_id,
                        connection_id=connection.id,
                        limit=100,
                    )
                    if str(item.get("certification_id") or "") == str(row["id"])
                ),
                None,
            )
        return ProviderWorkloadCertificationSummary(
            certification_id=str(row["id"]),
            connection_id=connection.id,
            connection_name=connection.name,
            provider_kind=connection.kind,
            execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
            adapter_contract=(
                str(row["adapter_contract"])  # type: ignore[arg-type]
                if row.get("adapter_contract")
                else None
            ),
            protocol_version=(
                str(row["protocol_version"])
                if row.get("protocol_version")
                else None
            ),
            status=status,  # type: ignore[arg-type]
            can_run=(
                status == "passed"
                and blocked_reason is None
                and self.enabled()
            ),
            blocked_reason=blocked_reason,
            checks=ProviderWorkloadCertificationChecks(**checks),
            warning_codes=[str(item) for item in warnings],
            error_code=str(row["error_code"]) if row["error_code"] else None,
            requested_model=str(row["requested_model"]),
            actual_model=str(row["actual_model"]) if row["actual_model"] else None,
            candidate_model_ids=[
                str(item) for item in profile.get("candidate_model_ids", [])
            ],
            judge_model_id=(
                str(profile["judge_model_id"])
                if profile.get("judge_model_id")
                else None
            ),
            profile_fingerprint=str(row["profile_fingerprint"]),
            rerank_access_mode=(
                str(profile["rerank_access_mode"])  # type: ignore[arg-type]
                if profile.get("rerank_access_mode")
                else None
            ),
            batch_job_id=(str(batch_job["id"]) if batch_job else None),
            batch_status=(str(batch_job["status"]) if batch_job else None),
            certified_input_formats=[
                str(item)
                for item in profile.get("certified_input_formats", [])
                if isinstance(item, str) and item
            ],
            certified_voice=(
                str(profile["certified_voice"])
                if isinstance(profile.get("certified_voice"), str)
                else None
            ),
            certified_response_format=(
                str(profile["certified_response_format"])  # type: ignore[arg-type]
                if profile.get("certified_response_format") in {"mp3", "wav"}
                else None
            ),
            provider_dispatch_state=provider_dispatch_state,  # type: ignore[arg-type]
            retry_allowed=retry_allowed,
            refresh_available=refresh_available,
            ttft_ms=self._float(row["ttft_ms"]),
            e2e_ms=self._float(row["e2e_ms"]),
            prompt_tokens=self._integer(row["prompt_tokens"]),
            completion_tokens=self._integer(row["completion_tokens"]),
            total_tokens=self._integer(row["total_tokens"]),
            vector_dimension=self._integer(row.get("vector_dimension")),
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        )

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_workload_storage_unavailable",
                "当前 Router 存储不支持 Workload 控制面。",
                status_code=503,
            )
        return method

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class ProviderWorkloadControlService:
    """Manage exact entry/model/shape bindings without changing data planes."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    @staticmethod
    def feature_enabled(entry_id: ProviderWorkloadEntryId) -> bool:
        return all(
            _enabled(os.getenv(flag), default=False)
            for flag in ENTRY_FEATURE_FLAGS[entry_id]
        )

    @staticmethod
    def data_plane_integrated(entry_id: ProviderWorkloadEntryId) -> bool:
        return entry_id in DATA_PLANE_INTEGRATED_ENTRIES

    def policies(self) -> ProviderWorkloadPolicyListResponse:
        return ProviderWorkloadPolicyListResponse(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            policies=[self.get_policy(entry_id) for entry_id in ENTRY_FEATURE_FLAGS],
        )

    def get_policy(
        self, entry_id: ProviderWorkloadEntryId
    ) -> ProviderWorkloadPolicyResponse:
        bundle = self._repository_method("get_workload_policy_bundle")(
            self.router_service.tenant_id,
            entry_id=entry_id,
        )
        return self._policy_response(entry_id, bundle)

    def update_policy(
        self,
        entry_id: ProviderWorkloadEntryId,
        payload: ProviderWorkloadPolicyUpdate,
    ) -> ProviderWorkloadPolicyResponse:
        allowed_shapes = ENTRY_ALLOWED_SHAPES[entry_id]
        if payload.local_fallback_mode not in ENTRY_ALLOWED_LOCAL_FALLBACKS[entry_id]:
            raise RouterServiceError(
                "provider_workload_local_fallback_not_allowed",
                "该入口不允许所选本地降级模式。",
                status_code=422,
            )
        qualified: list[dict[str, object]] = []
        for binding in payload.bindings:
            if binding.execution_shape not in allowed_shapes:
                raise RouterServiceError(
                    "provider_workload_execution_shape_not_allowed",
                    "该入口不允许所选 Provider 执行形态。",
                    status_code=422,
                )
            qualification, reason = self._current_qualification(
                connection_id=binding.connection_id,
                model_id=binding.model_id,
                execution_shape=binding.execution_shape,
                rerank_access_mode=binding.rerank_access_mode,
                adapter_contract=binding.adapter_contract,
            )
            if qualification is None:
                raise RouterServiceError(
                    reason,
                    "Binding 缺少当前完整目录、精确模型或对应执行形态资格。",
                    status_code=409,
                )
            qualified.append(qualification)
        policy_fingerprint = self._policy_fingerprint(
            entry_id,
            qualified,
            local_fallback_mode=payload.local_fallback_mode,
        )
        try:
            bundle = self._repository_method("replace_workload_policy")(
                self.router_service.tenant_id,
                entry_id=entry_id,
                expected_revision=payload.expected_revision,
                policy_fingerprint=policy_fingerprint,
                local_fallback_mode=payload.local_fallback_mode,
                bindings=qualified,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_workload_policy_revision_conflict":
                raise RouterServiceError(
                    str(exc),
                    "Workload 策略 revision 已变化，请刷新后重试。",
                    status_code=409,
                ) from exc
            raise
        return self._policy_response(entry_id, bundle)

    def activate(
        self,
        entry_id: ProviderWorkloadEntryId,
        payload: ProviderWorkloadActivationRequest,
    ) -> ProviderWorkloadPolicyResponse:
        policy = self.get_policy(entry_id)
        blockers = list(policy.blocking_reason_codes)
        if not self.data_plane_integrated(entry_id):
            blockers.append("provider_workload_data_plane_not_integrated")
        if not policy.feature_enabled:
            blockers.append("provider_workload_feature_disabled")
        if not payload.no_open_p0_p1:
            blockers.append("provider_workload_open_p0_p1_not_confirmed")
        if not payload.acknowledge_fail_closed:
            blockers.append("provider_workload_fail_closed_not_acknowledged")
        if blockers:
            raise RouterServiceError(
                blockers[0],
                "当前入口尚未满足 managed_required 人工激活条件。",
                status_code=409,
            )
        try:
            bundle = self._repository_method("activate_workload_policy")(
                self.router_service.tenant_id,
                entry_id=entry_id,
                expected_revision=payload.expected_revision,
                policy_fingerprint=policy.policy_fingerprint,
                no_open_p0_p1=payload.no_open_p0_p1,
                acknowledge_fail_closed=payload.acknowledge_fail_closed,
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                str(exc),
                "Workload 策略已变化，未执行激活。",
                status_code=409,
            ) from exc
        return self._policy_response(entry_id, bundle)

    def deactivate(
        self,
        entry_id: ProviderWorkloadEntryId,
        payload: ProviderWorkloadDeactivationRequest,
    ) -> ProviderWorkloadPolicyResponse:
        try:
            bundle = self._repository_method("deactivate_workload_policy")(
                self.router_service.tenant_id,
                entry_id=entry_id,
                expected_revision=payload.expected_revision,
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                str(exc),
                "Workload 策略 revision 已变化，请刷新后重试。",
                status_code=409,
            ) from exc
        return self._policy_response(entry_id, bundle)

    def overview(self) -> ProviderWorkloadOverview:
        policies = self.policies().policies
        blockers = sorted(
            {
                reason
                for policy in policies
                for reason in policy.blocking_reason_codes
            }
        )
        return ProviderWorkloadOverview(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_count=len(policies),
            feature_enabled_count=sum(item.feature_enabled for item in policies),
            managed_required_count=sum(
                item.effective_status == "managed_required" for item in policies
            ),
            degraded_required_count=sum(
                item.effective_status == "degraded_required" for item in policies
            ),
            qualified_binding_count=sum(
                binding.valid for policy in policies for binding in policy.bindings
            ),
            blocking_reason_codes=blockers,
            policies=policies,
        )

    def public_status(
        self,
        entry_id: ProviderWorkloadEntryId,
        model_id: str,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> ProviderWorkloadPublicStatus:
        clean_model = str(model_id or "").strip()
        policy = self.get_policy(entry_id)
        binding = next(
            (
                item
                for item in policy.bindings
                if item.model_id == clean_model
                and item.execution_shape == execution_shape
            ),
            None,
        )
        reason = "provider_workload_available"
        available = True
        if execution_shape not in ENTRY_ALLOWED_SHAPES[entry_id]:
            reason = "provider_workload_execution_shape_not_allowed"
            available = False
        elif not policy.feature_enabled:
            reason = "provider_workload_feature_disabled"
            available = False
        elif not policy.data_plane_integrated:
            reason = "provider_workload_data_plane_not_integrated"
            available = False
        elif policy.effective_status != "managed_required":
            reason = "provider_workload_policy_not_active"
            available = False
        elif binding is None:
            reason = "provider_workload_binding_missing"
            available = False
        elif not binding.valid:
            reason = binding.reason_code
            available = False
        profile: dict[str, object] = {}
        if binding is not None and execution_shape in R8C_EXECUTION_SHAPES:
            certification = self.repository.get_workload_certification(
                self.router_service.tenant_id,
                binding.certification_id,
            )
            if certification is not None:
                try:
                    parsed_profile = json.loads(
                        str(certification.get("profile_json") or "{}")
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    parsed_profile = {}
                if isinstance(parsed_profile, dict):
                    profile = parsed_profile
        return ProviderWorkloadPublicStatus(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_id=entry_id,
            execution_shape=execution_shape,
            model_id=clean_model,
            feature_enabled=policy.feature_enabled,
            status=policy.effective_status,
            available=available,
            blocks_before_dispatch=not available,
            reason_code=reason,
            certified_input_formats=[
                str(item)
                for item in profile.get("certified_input_formats", [])
                if isinstance(item, str) and item
            ],
            certified_voice=(
                str(profile["certified_voice"])
                if isinstance(profile.get("certified_voice"), str)
                else None
            ),
            certified_response_format=(
                str(profile["certified_response_format"])  # type: ignore[arg-type]
                if profile.get("certified_response_format") in {"mp3", "wav"}
                else None
            ),
        )

    def receipts(
        self,
        *,
        entry_id: ProviderWorkloadEntryId | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ProviderWorkloadReceiptsResponse:
        bundle = self._repository_method("list_workload_receipts")(
            self.router_service.tenant_id,
            entry_id=entry_id,
            limit=limit,
            cursor=cursor,
        )
        calls_by_run: dict[str, list[ProviderWorkloadCallSummary]] = {}
        for row in bundle["calls"]:
            calls_by_run.setdefault(str(row["run_id"]), []).append(
                ProviderWorkloadCallSummary(
                    call_id=str(row["id"]),
                    run_id=str(row["run_id"]),
                    entry_id=str(row["entry_id"]),  # type: ignore[arg-type]
                    execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
                    model_id=str(row["requested_model"]),
                    connection_id=(
                        str(row["connection_id"]) if row["connection_id"] else None
                    ),
                    adapter_contract=(
                        str(row["adapter_contract"])  # type: ignore[arg-type]
                        if row.get("adapter_contract")
                        else None
                    ),
                    protocol_version=(
                        str(row["protocol_version"])
                        if row.get("protocol_version")
                        else None
                    ),
                    provider_dispatch_state=(
                        str(row["provider_dispatch_state"])  # type: ignore[arg-type]
                        if row.get("provider_dispatch_state")
                        else None
                    ),
                    call_sequence=int(row["call_sequence"]),
                    dispatched=bool(row["dispatched"]),
                    status=str(row["status"]),
                    result_class=(
                        str(row["result_class"]) if row["result_class"] else None
                    ),
                    error_code=str(row["error_code"]) if row["error_code"] else None,
                    actual_model=(
                        str(row["actual_model"]) if row["actual_model"] else None
                    ),
                    ttft_ms=self._float(row["ttft_ms"]),
                    e2e_ms=self._float(row["e2e_ms"]),
                    prompt_tokens=self._integer(row["prompt_tokens"]),
                    completion_tokens=self._integer(row["completion_tokens"]),
                    total_tokens=self._integer(row["total_tokens"]),
                    generation_id_observed=(
                        bool(row["generation_id_observed"])
                        if row.get("generation_id_observed") is not None
                        else None
                    ),
                    generation_metadata_get_count=self._integer(
                        row.get("generation_metadata_get_count")
                    ),
                    generation_metadata_wait_ms=self._float(
                        row.get("generation_metadata_wait_ms")
                    ),
                    created_at=str(row["created_at"]),
                    completed_at=(
                        str(row["completed_at"]) if row["completed_at"] else None
                    ),
                )
            )
        batch_jobs_by_run: dict[str, dict[str, object]] = {}
        if entry_id in {None, "openrouter_batch"}:
            batch_jobs_by_run = {
                str(item["workload_run_id"]): item
                for item in self._repository_method("list_provider_batch_jobs")(
                    self.router_service.tenant_id,
                    limit=500,
                )
                if str(item.get("purpose")) == "runtime"
                and item.get("workload_run_id")
            }
        runs = [
            ProviderWorkloadRunSummary(
                run_id=str(row["id"]),
                entry_id=str(row["entry_id"]),  # type: ignore[arg-type]
                policy_fingerprint=str(row["policy_fingerprint"]),
                parent_run_reference=(
                    str(row["parent_run_reference"])
                    if row["parent_run_reference"]
                    else None
                ),
                status=str(row["status"]),
                result_class=(
                    str(row["result_class"]) if row["result_class"] else None
                ),
                reason_codes=[
                    str(item)
                    for item in json.loads(str(row["reason_codes_json"] or "[]"))
                ],
                created_at=str(row["created_at"]),
                completed_at=(
                    str(row["completed_at"]) if row["completed_at"] else None
                ),
                batch_job_id=(
                    str(batch_jobs_by_run[str(row["id"])]["id"])
                    if str(row["id"]) in batch_jobs_by_run
                    else None
                ),
                batch_status=(
                    str(batch_jobs_by_run[str(row["id"])]["status"])
                    if str(row["id"]) in batch_jobs_by_run
                    else None
                ),
                batch_request_count=(
                    int(batch_jobs_by_run[str(row["id"])]["request_count"])
                    if str(row["id"]) in batch_jobs_by_run
                    else None
                ),
                batch_completed_count=(
                    int(batch_jobs_by_run[str(row["id"])]["completed_count"])
                    if str(row["id"]) in batch_jobs_by_run
                    else None
                ),
                batch_failed_count=(
                    int(batch_jobs_by_run[str(row["id"])]["failed_count"])
                    if str(row["id"]) in batch_jobs_by_run
                    else None
                ),
                billing_authoritative=(
                    False if str(row["id"]) in batch_jobs_by_run else None
                ),
                calls=calls_by_run.get(str(row["id"]), []),
            )
            for row in bundle["runs"]
        ]
        return ProviderWorkloadReceiptsResponse(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            runs=runs,
            next_cursor=bundle["next_cursor"],
        )

    def _policy_response(
        self,
        entry_id: ProviderWorkloadEntryId,
        bundle: dict[str, object],
    ) -> ProviderWorkloadPolicyResponse:
        policies = list(bundle.get("policies") or [])
        bindings = list(bundle.get("bindings") or [])
        approvals = list(bundle.get("approvals") or [])
        policy = policies[0] if policies else None
        fingerprint = (
            str(policy["policy_fingerprint"])
            if isinstance(policy, dict)
            else self._policy_fingerprint(
                entry_id, [], local_fallback_mode="none"
            )
        )
        configured_status = (
            str(policy["status"]) if isinstance(policy, dict) else "legacy"
        )
        binding_summaries: list[ProviderWorkloadBindingSummary] = []
        for row in bindings:
            if not isinstance(row, dict):
                continue
            valid, reason, connection = self._binding_validity(row)
            binding_summaries.append(
                ProviderWorkloadBindingSummary(
                    execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
                    model_id=str(row["model_id"]),
                    connection_id=str(row["connection_id"]),
                    connection_name=(
                        connection.name if connection is not None else "已移除连接"
                    ),
                    provider_kind=(
                        connection.kind if connection is not None else None
                    ),
                    certification_id=str(row["certification_id"]),
                    certification_source=str(row["certification_source"]),  # type: ignore[arg-type]
                    connection_fingerprint=str(row["connection_fingerprint"]),
                    qualification_fingerprint=str(row["qualification_fingerprint"]),
                    adapter_contract=(
                        str(row["adapter_contract"])  # type: ignore[arg-type]
                        if row.get("adapter_contract")
                        else None
                    ),
                    protocol_version=(
                        str(row["protocol_version"])
                        if row.get("protocol_version")
                        else None
                    ),
                    rerank_access_mode=(
                        str(row["rerank_access_mode"])  # type: ignore[arg-type]
                        if row.get("rerank_access_mode")
                        else None
                    ),
                    valid=valid,
                    reason_code=reason,
                )
            )
        approval_record_valid = any(
            isinstance(approval, dict)
            and str(approval["policy_fingerprint"]) == fingerprint
            and approval.get("revoked_at") is None
            and bool(approval["no_open_p0_p1"])
            and bool(approval["acknowledge_fail_closed"])
            for approval in approvals
        )
        approval_valid = (
            approval_record_valid
            and bool(binding_summaries)
            and all(item.valid for item in binding_summaries)
        )
        blockers: list[str] = []
        if not binding_summaries:
            blockers.append("provider_workload_bindings_required")
        blockers.extend(
            sorted({item.reason_code for item in binding_summaries if not item.valid})
        )
        if not self.data_plane_integrated(entry_id):
            blockers.append("provider_workload_data_plane_not_integrated")
        if not self.feature_enabled(entry_id):
            blockers.append("provider_workload_feature_disabled")
        if configured_status == "managed_required" and not approval_valid:
            blockers.append("provider_workload_approval_missing")
        effective_status = configured_status
        if configured_status in {"managed_required", "degraded_required"} and blockers:
            effective_status = "degraded_required"
        return ProviderWorkloadPolicyResponse(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_id=entry_id,
            feature_enabled=self.feature_enabled(entry_id),
            data_plane_integrated=self.data_plane_integrated(entry_id),
            configured_status=configured_status,  # type: ignore[arg-type]
            effective_status=effective_status,  # type: ignore[arg-type]
            revision=int(policy["revision"]) if isinstance(policy, dict) else 0,
            policy_fingerprint=fingerprint,
            local_fallback_mode=(
                str(policy.get("local_fallback_mode") or "none")  # type: ignore[arg-type]
                if isinstance(policy, dict)
                else "none"
            ),
            bindings=binding_summaries,
            approval_valid=approval_valid,
            blocking_reason_codes=list(dict.fromkeys(blockers)),
            updated_at=(
                str(policy["updated_at"]) if isinstance(policy, dict) else None
            ),
        )

    def _current_qualification(
        self,
        *,
        connection_id: str,
        model_id: str,
        execution_shape: ProviderWorkloadExecutionShape,
        rerank_access_mode: str | None = None,
        adapter_contract: str | None = None,
    ) -> tuple[dict[str, object] | None, str]:
        if execution_shape == "rerank_documents":
            if rerank_access_mode not in {"dedicated", "llm_json"}:
                return None, "provider_workload_rerank_access_mode_required"
        elif rerank_access_mode is not None:
            return None, "provider_workload_rerank_access_mode_not_allowed"
        if execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            if adapter_contract is None:
                return None, "provider_multimodal_adapter_required"
        elif adapter_contract is not None:
            return None, "provider_multimodal_adapter_not_allowed"
        if execution_shape in {"chat_text", "chat_tools"}:
            qualification, reason = ProviderChatControlService(
                self.router_service
            )._current_qualification(  # noqa: SLF001 - shared qualification contract
                connection_id=connection_id,
                model_id=model_id,
                capability=execution_shape,
            )
            if qualification is None:
                return None, reason
            return (
                {
                    **qualification,
                    "execution_shape": execution_shape,
                    "certification_source": "provider_chat",
                    "qualification_fingerprint": _fingerprint(qualification),
                },
                "qualified",
            )
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
        except RouterRepositoryError:
            return None, "provider_workload_connection_missing"
        if not connection.enabled:
            return None, "connection_disabled"
        if execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            try:
                validate_multimodal_adapter(
                    contract=adapter_contract,  # type: ignore[arg-type]
                    execution_shape=execution_shape,
                    provider_kind=connection.kind,
                    scopes=connection.scopes,
                )
            except RouterServiceError as exc:
                return None, exc.code
        else:
            required_scope = ProviderWorkloadCertificationService._required_scope(
                execution_shape
            )
            if required_scope not in connection.scopes:
                return None, f"connection_{required_scope}_scope_required"
        if connection.health != "online":
            return None, "provider_connection_not_online"
        try:
            self.repository.resolve_api_key(
                self.router_service.tenant_id, connection_id
            )
        except RouterCredentialUnavailable:
            return None, "provider_workload_credential_unavailable"
        if execution_shape == "fusion_native" and connection.kind != "openrouter":
            return None, "provider_workload_fusion_requires_openrouter"
        if execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        } and connection.kind != "openrouter":
            return None, "provider_workload_batch_requires_openrouter"
        fingerprint = self.repository.connection_config_fingerprint(
            self.router_service.tenant_id, connection_id
        )
        inventory = self.repository.list_catalog_models(
            self.router_service.tenant_id,
            connection_id=connection_id,
            model_id=model_id,
            status="active",
            limit=1,
        )
        if not inventory:
            return None, "provider_workload_model_inventory_missing"
        refresh = next(
            (
                item
                for item in self.repository.list_catalog_refreshes(
                    self.router_service.tenant_id,
                    connection_id=connection_id,
                    limit=500,
                )
                if str(item["id"]) == str(inventory[0]["last_refresh_id"])
            ),
            None,
        )
        if refresh is None or str(refresh["status"]) != "succeeded":
            return None, "provider_workload_catalog_refresh_missing"
        if bool(refresh["truncated"]):
            return None, "provider_workload_catalog_refresh_truncated"
        if str(refresh["connection_fingerprint"]) != fingerprint:
            return None, "provider_workload_catalog_stale"
        certification = self._repository_method(
            "get_latest_workload_certification"
        )(
            self.router_service.tenant_id,
            connection_id,
            model_id,
            execution_shape,
            rerank_access_mode=rerank_access_mode,
            adapter_contract=adapter_contract,
        )
        if certification is None:
            return None, "provider_workload_certification_required"
        if str(certification["status"]) != "passed":
            return None, "provider_workload_certification_not_passed"
        if str(certification["connection_fingerprint"]) != fingerprint:
            return None, "provider_workload_certification_stale"
        if str(certification["contract_version"]) != PROVIDER_WORKLOAD_CONTRACT_VERSION:
            return None, "provider_workload_contract_stale"
        if execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            if str(certification.get("adapter_contract") or "") != adapter_contract:
                return None, "provider_multimodal_adapter_certification_mismatch"
            if str(certification.get("protocol_version") or "") != (
                PROVIDER_MULTIMODAL_PROTOCOL_VERSION
            ):
                return None, "provider_multimodal_protocol_stale"
        if execution_shape in R8C_EXECUTION_SHAPES:
            checks = _safe_json_object(
                json.loads(str(certification.get("checks_json") or "{}"))
            )
            if (
                not certification.get("actual_model")
                or checks.get("actual_model_verified") is not True
            ):
                return None, "provider_multimodal_actual_model_unverified"
            profile = _safe_json_object(
                json.loads(str(certification.get("profile_json") or "{}"))
            )
            profile_reason = r8c_audio_parameter_profile_reason(
                execution_shape,
                profile,
            )
            if profile_reason is not None:
                return None, profile_reason
        time_reason = ProviderChatControlService._certification_time_status(  # noqa: SLF001
            certification
        )
        if time_reason is not None:
            return None, time_reason.replace("provider_chat_", "provider_workload_", 1)
        expected_actual_model = model_id
        if execution_shape == "fusion_native":
            profile = _safe_json_object(
                json.loads(str(certification.get("profile_json") or "{}"))
            )
            judge_model_id = profile.get("judge_model_id")
            if not isinstance(judge_model_id, str) or not judge_model_id:
                return None, "provider_workload_fusion_profile_mismatch"
            expected_actual_model = judge_model_id
        actual_model = certification.get("actual_model")
        if actual_model:
            matches = (
                provider_operation_model_matches(
                    provider_kind=connection.kind,
                    requested_model=expected_actual_model,
                    actual_model=str(actual_model),
                )
                if execution_shape == "embedding_vectors"
                else (
                    provider_operation_batch_model_matches(
                        provider_kind=connection.kind,
                        requested_model=expected_actual_model,
                        actual_model=str(actual_model),
                    )
                    if execution_shape.startswith("openrouter_batch_")
                    else str(actual_model) == expected_actual_model
                )
            )
            if not matches:
                return None, "provider_workload_certification_model_mismatch"
        qualification = {
            "execution_shape": execution_shape,
            "connection_id": connection_id,
            "model_id": model_id,
            "certification_id": str(certification["id"]),
            "certification_source": "provider_workload",
            "connection_fingerprint": fingerprint,
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "profile_fingerprint": str(certification["profile_fingerprint"]),
            "rerank_access_mode": rerank_access_mode,
            "adapter_contract": adapter_contract,
            "protocol_version": (
                PROVIDER_MULTIMODAL_PROTOCOL_VERSION
                if adapter_contract is not None
                else None
            ),
        }
        qualification["qualification_fingerprint"] = _fingerprint(qualification)
        return qualification, "qualified"

    def _binding_validity(
        self, row: dict[str, object]
    ) -> tuple[bool, str, RouterConnection | None]:
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, str(row["connection_id"])
            )
        except RouterRepositoryError:
            return False, "provider_workload_connection_missing", None
        current, reason = self._current_qualification(
            connection_id=str(row["connection_id"]),
            model_id=str(row["model_id"]),
            execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
            rerank_access_mode=(
                str(row["rerank_access_mode"])
                if row.get("rerank_access_mode")
                else None
            ),
            adapter_contract=(
                str(row["adapter_contract"])
                if row.get("adapter_contract")
                else None
            ),
        )
        if current is None:
            return False, reason, connection
        if str(current["certification_source"]) != str(
            row["certification_source"]
        ):
            return False, "provider_workload_qualification_changed", connection
        if str(current["certification_id"]) != str(row["certification_id"]):
            return False, "provider_workload_newer_certification_requires_policy_update", connection
        if str(current["connection_fingerprint"]) != str(
            row["connection_fingerprint"]
        ):
            return False, "provider_workload_connection_fingerprint_changed", connection
        if str(current["qualification_fingerprint"]) != str(
            row["qualification_fingerprint"]
        ):
            return False, "provider_workload_qualification_changed", connection
        if str(current.get("adapter_contract") or "") != str(
            row.get("adapter_contract") or ""
        ):
            return False, "provider_multimodal_adapter_changed", connection
        if str(current.get("protocol_version") or "") != str(
            row.get("protocol_version") or ""
        ):
            return False, "provider_multimodal_protocol_changed", connection
        return True, "qualified", connection

    @staticmethod
    def _policy_fingerprint(
        entry_id: ProviderWorkloadEntryId,
        bindings: list[dict[str, object]],
        *,
        local_fallback_mode: str,
    ) -> str:
        material = [
            {
                "execution_shape": item["execution_shape"],
                "model_id": item["model_id"],
                "connection_id": item["connection_id"],
                "rerank_access_mode": item.get("rerank_access_mode"),
                "adapter_contract": item.get("adapter_contract"),
                "protocol_version": item.get("protocol_version"),
                "qualification_fingerprint": item["qualification_fingerprint"],
            }
            for item in sorted(
                bindings,
                key=lambda value: (
                    str(value["execution_shape"]),
                    str(value["model_id"]),
                    str(value["connection_id"]),
                ),
            )
        ]
        return _fingerprint(
            {
                "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
                "entry_id": entry_id,
                "local_fallback_mode": local_fallback_mode,
                "bindings": material,
            }
        )

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_workload_storage_unavailable",
                "当前 Router 存储不支持 Workload 控制面。",
                status_code=503,
            )
        return method

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class ProviderWorkloadPreparedCall:
    run_id: str
    call_id: str
    entry_id: ProviderWorkloadEntryId
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str
    call_sequence: int
    connection_id: str
    certification_id: str
    connection_fingerprint: str
    policy_fingerprint: str
    target: ProviderChatTarget
    operation_target: ProviderOperationTarget | None
    multimodal_target: ProviderMultimodalTarget | None
    rerank_access_mode: str | None
    adapter_contract: str | None
    protocol_version: str | None
    authorized_target: AuthorizedProviderTarget


class ProviderWorkloadCallService:
    """Exact managed target and one-dispatch receipt guard for R6 data planes."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.control = ProviderWorkloadControlService(router_service)
        self.transport = ProviderChatTransport(router_service.egress_policy)
        self.operation_transport = ProviderOperationTransport(
            router_service.egress_policy
        )
        self.multimodal_transport = ProviderMultimodalTransport(
            router_service.egress_policy
        )

    def start_run(
        self,
        entry_id: ProviderWorkloadEntryId,
        *,
        parent_run_reference: str | None = None,
    ) -> str:
        policy = self.control.get_policy(entry_id)
        self._ensure_active(policy)
        run_id = f"workrun_{uuid.uuid4().hex}"
        self.repository.claim_workload_run(
            self.router_service.tenant_id,
            run_id=run_id,
            entry_id=entry_id,
            policy_fingerprint=policy.policy_fingerprint,
            parent_run_reference=parent_run_reference,
        )
        return run_id

    def start_stable_run(
        self,
        entry_id: ProviderWorkloadEntryId,
        *,
        parent_run_reference: str,
    ) -> str:
        """Claim one durable logical run and reject every later replay."""

        clean_parent = parent_run_reference.strip()
        if not clean_parent:
            raise RouterServiceError(
                "provider_workload_parent_run_reference_required",
                "Workload 稳定运行缺少父执行引用。",
                status_code=422,
            )
        policy = self.control.get_policy(entry_id)
        self._ensure_active(policy)
        run_id = self.stable_run_id(entry_id, clean_parent)
        row, created = self.repository.claim_stable_workload_run(
            self.router_service.tenant_id,
            run_id=run_id,
            entry_id=entry_id,
            policy_fingerprint=policy.policy_fingerprint,
            parent_run_reference=clean_parent,
        )
        if (
            str(row["entry_id"]) != entry_id
            or str(row["parent_run_reference"] or "") != clean_parent
        ):
            raise RouterServiceError(
                "provider_workload_stable_run_collision",
                "Workload 稳定运行引用发生冲突，系统不会派发 Provider 请求。",
                status_code=409,
            )
        if not created:
            raise RouterServiceError(
                "provider_workload_logical_run_replay_blocked",
                "该 Workflow 模型节点已有执行证据，系统不会自动重放。",
                status_code=409,
            )
        return run_id

    def stable_run_id(
        self,
        entry_id: ProviderWorkloadEntryId,
        parent_run_reference: str,
    ) -> str:
        material = json.dumps(
            {
                "tenant_id": self.router_service.tenant_id,
                "entry_id": entry_id,
                "parent_run_reference": parent_run_reference,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"workrun_{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    async def prepare_call(
        self,
        *,
        run_id: str,
        entry_id: ProviderWorkloadEntryId,
        execution_shape: ProviderWorkloadExecutionShape,
        model_id: str,
        logical_call_key: str,
        call_sequence: int,
    ) -> ProviderWorkloadPreparedCall:
        policy = self.control.get_policy(entry_id)
        self._ensure_active(policy)
        run = self.repository.get_workload_run(
            self.router_service.tenant_id, run_id
        )
        if str(run["entry_id"]) != entry_id:
            raise RouterServiceError(
                "provider_workload_run_entry_mismatch",
                "Workload 运行入口与本次调用不一致。",
                status_code=409,
            )
        if str(run["status"]) != "running":
            raise RouterServiceError(
                "provider_workload_run_not_running",
                "Workload 运行已结束或状态不确定，系统不会重放调用。",
                status_code=409,
            )
        if str(run["policy_fingerprint"]) != policy.policy_fingerprint:
            raise RouterServiceError(
                "provider_workload_policy_changed",
                "Workload 策略已变化，本次运行在 Provider 派发前失败关闭。",
                status_code=409,
            )
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == execution_shape
                and item.model_id == model_id
                and item.valid
            ),
            None,
        )
        if binding is None:
            raise RouterServiceError(
                "provider_workload_binding_missing",
                "当前入口没有该精确模型与执行形态的合格 Binding。",
                status_code=409,
            )
        connection, api_key, current_connection_fingerprint = (
            self.repository.get_connection_credential_snapshot(
                self.router_service.tenant_id, binding.connection_id
            )
        )
        if (
            not connection.enabled
            or connection.health != "online"
            or current_connection_fingerprint != binding.connection_fingerprint
        ):
            raise RouterServiceError(
                "provider_workload_binding_changed",
                "Workload Binding 或连接配置已变化，本次调用在 Provider 派发前失败关闭。",
                status_code=409,
            )
        target = ProviderChatTarget.create(
            source="managed",
            provider_kind=connection.kind,
            base_url=connection.base_url,
            api_key=api_key,
            connection_id=connection.id,
        )
        operation_target: ProviderOperationTarget | None = None
        multimodal_target: ProviderMultimodalTarget | None = None
        if execution_shape in MULTIMODAL_WORKLOAD_SHAPES:
            if binding.adapter_contract is None:
                raise RouterServiceError(
                    "provider_multimodal_adapter_required",
                    "多模态调用缺少明确的 Adapter。",
                    status_code=409,
                )
            multimodal_target = ProviderMultimodalTarget.create(
                provider_kind=connection.kind,
                connection_id=connection.id,
                base_url=connection.base_url,
                api_key=api_key,
                adapter_contract=binding.adapter_contract,
                execution_shape=execution_shape,
            )
            authorized = await self.multimodal_transport.authorize(
                multimodal_target
            )
        elif execution_shape in {
            "embedding_vectors",
            "rerank_documents",
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        }:
            operation_target = ProviderOperationTarget.create(
                provider_kind=connection.kind,
                connection_id=connection.id,
                base_url=connection.base_url,
                api_key=api_key,
            )
            authorized = await self.operation_transport.authorize(
                operation_target,
                execution_shape,
                rerank_access_mode=binding.rerank_access_mode,
            )
        else:
            authorized = await self.transport.authorize_managed_target(target)
        call_id = f"workcall_{uuid.uuid4().hex}"
        row, created = self.repository.claim_workload_call(
            self.router_service.tenant_id,
            call_id=call_id,
            run_id=run_id,
            entry_id=entry_id,
            execution_shape=execution_shape,
            requested_model=model_id,
            connection_id=binding.connection_id,
            certification_id=binding.certification_id,
            connection_fingerprint=binding.connection_fingerprint,
            logical_call_key_hash=hashlib.sha256(
                logical_call_key.encode("utf-8")
            ).hexdigest(),
            call_sequence=call_sequence,
            adapter_contract=binding.adapter_contract,
            protocol_version=binding.protocol_version,
        )
        if not created:
            raise RouterServiceError(
                "provider_workload_logical_call_replay_blocked",
                "该逻辑调用已存在，系统不会自动重放。",
                status_code=409,
            )
        return ProviderWorkloadPreparedCall(
            run_id=run_id,
            call_id=str(row["id"]),
            entry_id=entry_id,
            execution_shape=execution_shape,
            model_id=model_id,
            call_sequence=call_sequence,
            connection_id=binding.connection_id,
            certification_id=binding.certification_id,
            connection_fingerprint=binding.connection_fingerprint,
            policy_fingerprint=policy.policy_fingerprint,
            target=target,
            operation_target=operation_target,
            multimodal_target=multimodal_target,
            rerank_access_mode=binding.rerank_access_mode,
            adapter_contract=binding.adapter_contract,
            protocol_version=binding.protocol_version,
            authorized_target=authorized,
        )

    def mark_dispatched(self, prepared: ProviderWorkloadPreparedCall) -> None:
        policy = self.control.get_policy(prepared.entry_id)
        self._ensure_active(policy)
        run = self.repository.get_workload_run(
            self.router_service.tenant_id, prepared.run_id
        )
        if (
            str(run["status"]) != "running"
            or str(run["entry_id"]) != prepared.entry_id
            or str(run["policy_fingerprint"]) != prepared.policy_fingerprint
            or policy.policy_fingerprint != prepared.policy_fingerprint
        ):
            raise RouterServiceError(
                "provider_workload_policy_changed",
                "Workload 策略已变化，本次调用在 Provider 派发前失败关闭。",
                status_code=409,
            )
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == prepared.execution_shape
                and item.model_id == prepared.model_id
                and item.connection_id == prepared.connection_id
                and item.certification_id == prepared.certification_id
                and item.connection_fingerprint == prepared.connection_fingerprint
                and item.adapter_contract == prepared.adapter_contract
                and item.protocol_version == prepared.protocol_version
                and item.valid
            ),
            None,
        )
        if binding is None:
            raise RouterServiceError(
                "provider_workload_binding_changed",
                "Workload Binding 或资格已变化，本次调用在 Provider 派发前失败关闭。",
                status_code=409,
            )
        try:
            self.repository.mark_workload_call_dispatched(
                self.router_service.tenant_id,
                prepared.call_id,
                run_id=prepared.run_id,
                entry_id=prepared.entry_id,
                execution_shape=prepared.execution_shape,
                requested_model=prepared.model_id,
                connection_id=prepared.connection_id,
                certification_id=prepared.certification_id,
                connection_fingerprint=prepared.connection_fingerprint,
                policy_fingerprint=prepared.policy_fingerprint,
                adapter_contract=prepared.adapter_contract,
                protocol_version=prepared.protocol_version,
                verify_current_connection=True,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_workload_dispatch_preconditions_changed":
                raise RouterServiceError(
                    "provider_workload_dispatch_preconditions_changed",
                    "Workload 派发前置条件已变化，系统未发送 Provider 请求。",
                    status_code=409,
                ) from exc
            raise

    def complete_call(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        status: str,
        result_class: str | None = None,
        error_code: str | None = None,
        actual_model: str | None = None,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        generation_id_observed: bool | None = None,
        generation_metadata_get_count: int | None = None,
        generation_metadata_wait_ms: float | None = None,
    ) -> None:
        self.repository.complete_workload_call(
            self.router_service.tenant_id,
            prepared.call_id,
            status=status,
            result_class=result_class,
            error_code=error_code,
            actual_model=actual_model,
            ttft_ms=ttft_ms,
            e2e_ms=e2e_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            generation_id_observed=generation_id_observed,
            generation_metadata_get_count=generation_metadata_get_count,
            generation_metadata_wait_ms=generation_metadata_wait_ms,
        )

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        result_class: str | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        self.repository.complete_workload_run(
            self.router_service.tenant_id,
            run_id,
            status=status,
            result_class=result_class,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _ensure_active(policy: ProviderWorkloadPolicyResponse) -> None:
        if policy.effective_status != "managed_required":
            raise RouterServiceError(
                "provider_workload_policy_not_active",
                "该入口未处于合格的 managed_required 状态。",
                status_code=409,
            )
