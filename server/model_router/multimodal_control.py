from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

import httpx

from .egress import AuthorizedProviderTarget, ProviderEgressPolicy
from .provider_chat import ProviderChatEndpointResolver
from .repository import RouterRepositoryError
from .schemas import (
    MULTIMODAL_WORKLOAD_SHAPES,
    ConnectionKind,
    ProviderMultimodalAdapterContract,
    ProviderWorkloadExecutionShape,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_MULTIMODAL_PROTOCOL_VERSION = "modelmirror-provider-multimodal-v1"
R8B_EXECUTION_SHAPES: frozenset[ProviderWorkloadExecutionShape] = frozenset(
    {
        "chat_image_stream",
        "chat_document_stream",
        "vision_json_unary",
        "image_generation",
    }
)


@dataclass(frozen=True, slots=True)
class MultimodalAdapterSpec:
    contract: ProviderMultimodalAdapterContract
    execution_shape: ProviderWorkloadExecutionShape
    provider_kinds: frozenset[ConnectionKind]
    required_scopes: tuple[str, ...]
    certification_mode: Literal["sync", "async", "browser_assisted"]


@dataclass(frozen=True, slots=True)
class ProviderMultimodalTarget:
    provider_kind: ConnectionKind
    connection_id: str
    adapter_contract: ProviderMultimodalAdapterContract
    execution_shape: ProviderWorkloadExecutionShape
    endpoint_url: str
    _api_key: str = field(repr=False, compare=False)

    @classmethod
    def create(
        cls,
        *,
        provider_kind: ConnectionKind,
        connection_id: str,
        base_url: str,
        api_key: str,
        adapter_contract: ProviderMultimodalAdapterContract,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> "ProviderMultimodalTarget":
        multimodal_adapter_spec(adapter_contract, execution_shape)
        api_base = ProviderChatEndpointResolver.resolve(base_url).base_url
        if adapter_contract == "openrouter_images_v1":
            endpoint_url = f"{api_base}/images"
        elif adapter_contract == "openai_compatible_images_generations_v1":
            endpoint_url = f"{api_base}/images/generations"
        else:
            endpoint_url = f"{api_base}/chat/completions"
        return cls(
            provider_kind=provider_kind,
            connection_id=connection_id,
            adapter_contract=adapter_contract,
            execution_shape=execution_shape,
            endpoint_url=endpoint_url,
            _api_key=str(api_key or ""),
        )

    def authorization_headers(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        headers = dict(extra or {})
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


class ProviderMultimodalTransport:
    """One-address transport for a qualified multimodal Adapter endpoint."""

    def __init__(self, egress_policy: ProviderEgressPolicy) -> None:
        self.egress_policy = egress_policy

    async def authorize(
        self, target: ProviderMultimodalTarget
    ) -> AuthorizedProviderTarget:
        return await self.egress_policy.authorize(target.endpoint_url)

    @staticmethod
    def build_authorized_json_request(
        client: httpx.AsyncClient,
        target: ProviderMultimodalTarget,
        authorized: AuthorizedProviderTarget,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Request:
        return client.build_request(
            "POST",
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers(headers)),
            extensions=authorized.extensions,
            json=dict(payload),
        )

    @staticmethod
    async def send_authorized(
        client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        return await client.send(request, stream=True, follow_redirects=False)


_OPENAI_COMPATIBLE_KINDS: frozenset[ConnectionKind] = frozenset(
    {"newapi", "openai_compatible", "openai"}
)


MULTIMODAL_ADAPTER_SPECS: dict[
    ProviderMultimodalAdapterContract, MultimodalAdapterSpec
] = {
    "openrouter_chat_multimodal_v1": MultimodalAdapterSpec(
        "openrouter_chat_multimodal_v1",
        "chat_image_stream",
        frozenset({"openrouter"}),
        ("chat", "image"),
        "sync",
    ),
    "openai_compatible_chat_multimodal_v1": MultimodalAdapterSpec(
        "openai_compatible_chat_multimodal_v1",
        "vision_json_unary",
        _OPENAI_COMPATIBLE_KINDS,
        ("chat", "image"),
        "sync",
    ),
    "openrouter_chat_native_pdf_v1": MultimodalAdapterSpec(
        "openrouter_chat_native_pdf_v1",
        "chat_document_stream",
        frozenset({"openrouter"}),
        ("chat", "document"),
        "sync",
    ),
    "openrouter_images_v1": MultimodalAdapterSpec(
        "openrouter_images_v1",
        "image_generation",
        frozenset({"openrouter"}),
        ("image",),
        "sync",
    ),
    "openai_compatible_images_generations_v1": MultimodalAdapterSpec(
        "openai_compatible_images_generations_v1",
        "image_generation",
        _OPENAI_COMPATIBLE_KINDS,
        ("image",),
        "sync",
    ),
    "openrouter_audio_transcription_json_v1": MultimodalAdapterSpec(
        "openrouter_audio_transcription_json_v1",
        "audio_transcription",
        frozenset({"openrouter"}),
        ("audio",),
        "sync",
    ),
    "openai_compatible_audio_transcription_multipart_v1": MultimodalAdapterSpec(
        "openai_compatible_audio_transcription_multipart_v1",
        "audio_transcription",
        _OPENAI_COMPATIBLE_KINDS,
        ("audio",),
        "sync",
    ),
    "openrouter_audio_speech_v1": MultimodalAdapterSpec(
        "openrouter_audio_speech_v1",
        "audio_speech",
        frozenset({"openrouter"}),
        ("audio",),
        "sync",
    ),
    "openai_compatible_audio_speech_v1": MultimodalAdapterSpec(
        "openai_compatible_audio_speech_v1",
        "audio_speech",
        _OPENAI_COMPATIBLE_KINDS,
        ("audio",),
        "sync",
    ),
    "openrouter_chat_audio_v1": MultimodalAdapterSpec(
        "openrouter_chat_audio_v1",
        "chat_audio_input",
        frozenset({"openrouter"}),
        ("chat", "audio"),
        "sync",
    ),
    "openrouter_audio_generation_stream_v1": MultimodalAdapterSpec(
        "openrouter_audio_generation_stream_v1",
        "audio_generation_stream",
        frozenset({"openrouter"}),
        ("audio",),
        "sync",
    ),
    "openrouter_chat_video_v1": MultimodalAdapterSpec(
        "openrouter_chat_video_v1",
        "video_analysis_unary",
        frozenset({"openrouter"}),
        ("chat", "video"),
        "sync",
    ),
    "openrouter_video_jobs_v1": MultimodalAdapterSpec(
        "openrouter_video_jobs_v1",
        "video_generation_async",
        frozenset({"openrouter"}),
        ("video",),
        "async",
    ),
    "openai_realtime_sdp_v1": MultimodalAdapterSpec(
        "openai_realtime_sdp_v1",
        "realtime_voice_session",
        frozenset({"openai"}),
        ("realtime",),
        "browser_assisted",
    ),
}


_SHAPE_ALIASES: dict[
    tuple[ProviderMultimodalAdapterContract, ProviderWorkloadExecutionShape],
    ProviderWorkloadExecutionShape,
] = {
    ("openai_compatible_chat_multimodal_v1", "chat_image_stream"): (
        "chat_image_stream"
    ),
    ("openrouter_chat_multimodal_v1", "vision_json_unary"): (
        "vision_json_unary"
    ),
    ("openrouter_chat_audio_v1", "chat_audio_output"): "chat_audio_output",
    ("openrouter_chat_video_v1", "chat_video_stream"): "chat_video_stream",
}


def multimodal_adapter_spec(
    contract: ProviderMultimodalAdapterContract,
    execution_shape: ProviderWorkloadExecutionShape,
) -> MultimodalAdapterSpec:
    spec = MULTIMODAL_ADAPTER_SPECS[contract]
    if execution_shape == spec.execution_shape:
        return spec
    if (contract, execution_shape) in _SHAPE_ALIASES:
        return MultimodalAdapterSpec(
            contract=spec.contract,
            execution_shape=execution_shape,
            provider_kinds=spec.provider_kinds,
            required_scopes=spec.required_scopes,
            certification_mode=spec.certification_mode,
        )
    raise RouterServiceError(
        "provider_multimodal_adapter_shape_mismatch",
        "所选 Adapter 与执行形态不匹配。",
        status_code=422,
    )


def validate_multimodal_adapter(
    *,
    contract: ProviderMultimodalAdapterContract,
    execution_shape: ProviderWorkloadExecutionShape,
    provider_kind: ConnectionKind,
    scopes: list[str],
) -> MultimodalAdapterSpec:
    if execution_shape not in MULTIMODAL_WORKLOAD_SHAPES:
        raise RouterServiceError(
            "provider_multimodal_execution_shape_required",
            "该 Adapter 只能用于多模态执行形态。",
            status_code=422,
        )
    spec = multimodal_adapter_spec(contract, execution_shape)
    if provider_kind not in spec.provider_kinds:
        raise RouterServiceError(
            "provider_multimodal_adapter_provider_mismatch",
            "所选 Provider 类型不支持该 Adapter。",
            status_code=422,
        )
    missing = [scope for scope in spec.required_scopes if scope not in scopes]
    if missing:
        raise RouterServiceError(
            f"connection_{missing[0]}_scope_required",
            "连接缺少该多模态 Adapter 所需的 scope。",
            status_code=409,
        )
    return spec


class ProviderMultimodalCertificationSessionService:
    """Persist safe orchestration state; protocol runners land with R8B-R8F."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    def refresh(self, certification_id: str) -> None:
        session = self.repository.get_multimodal_certification_session(
            self.router_service.tenant_id,
            certification_id=certification_id,
        )
        if session is None:
            raise RouterServiceError(
                "provider_multimodal_certification_session_not_found",
                "未找到该多模态资格会话。",
                status_code=404,
            )
        if not session.get("upstream_operation_id"):
            raise RouterServiceError(
                "provider_multimodal_certification_result_uncertain",
                "资格提交结果待确认；同一幂等键不会重新发送。",
                status_code=409,
            )
        raise RouterServiceError(
            "provider_multimodal_certification_refresh_not_integrated",
            "该异步 Adapter 将在对应 R8 数据面批次接入，只读轮询尚未开放。",
            status_code=409,
        )

    def realtime_not_integrated(self, connection_id: str) -> None:
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                "provider_multimodal_connection_missing",
                "未找到所选 Managed 连接。",
                status_code=404,
            ) from exc
        validate_multimodal_adapter(
            contract="openai_realtime_sdp_v1",
            execution_shape="realtime_voice_session",
            provider_kind=connection.kind,
            scopes=connection.scopes,
        )
        raise RouterServiceError(
            "provider_realtime_certification_not_integrated",
            "Realtime 浏览器辅助认证将在 R8F 接入；本批次不会创建付费会话。",
            status_code=409,
        )

    def realtime_complete_not_integrated(self, certification_id: str) -> None:
        session = self.repository.get_multimodal_certification_session(
            self.router_service.tenant_id,
            certification_id=certification_id,
        )
        if session is None:
            raise RouterServiceError(
                "provider_multimodal_certification_session_not_found",
                "未找到该 Realtime 资格会话。",
                status_code=404,
            )
        raise RouterServiceError(
            "provider_realtime_certification_not_integrated",
            "Realtime 浏览器辅助认证将在 R8F 接入；本批次不会保存媒体确认。",
            status_code=409,
        )
