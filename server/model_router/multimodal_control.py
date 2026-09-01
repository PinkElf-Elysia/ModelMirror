from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import re
import struct
from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping
from urllib.parse import urlsplit

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
R8C_EXECUTION_SHAPES: frozenset[ProviderWorkloadExecutionShape] = frozenset(
    {"audio_transcription", "audio_speech"}
)
R8D_EXECUTION_SHAPES: frozenset[ProviderWorkloadExecutionShape] = frozenset(
    {"chat_audio_input", "chat_audio_output", "audio_generation_stream"}
)
_MAX_GENERATION_METADATA_BYTES = 256 * 1024
OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS = 2.0
_OPENROUTER_GENERATION_ID_LOG_PATTERN = re.compile(
    r"([?&]id=)[^&\s]+",
    re.IGNORECASE,
)


class _OpenRouterGenerationMetadataLogFilter(logging.Filter):
    """Keep HTTPX request telemetry without logging the opaque generation ID."""

    modelmirror_openrouter_generation_id_redactor = True

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            record.name != "httpx"
            or not isinstance(args, tuple)
            or len(args) < 2
        ):
            return True
        url = str(args[1])
        redacted_url = url
        try:
            parsed = urlsplit(url)
            if parsed.hostname is not None:
                ipaddress.ip_address(parsed.hostname)
                redacted_url = f"{parsed.scheme}://[provider-address-redacted]"
        except ValueError:
            pass
        if "/generation?" in redacted_url:
            redacted_url = _OPENROUTER_GENERATION_ID_LOG_PATTERN.sub(
                r"\1[redacted]",
                redacted_url,
            )
        if redacted_url != url:
            clean_args = list(args)
            clean_args[1] = redacted_url
            record.args = tuple(clean_args)
        return True


def _install_httpx_generation_metadata_log_filter() -> None:
    logger = logging.getLogger("httpx")
    if any(
        getattr(item, "modelmirror_openrouter_generation_id_redactor", False)
        for item in logger.filters
    ):
        return
    logger.addFilter(_OpenRouterGenerationMetadataLogFilter())


_install_httpx_generation_metadata_log_filter()

# Fixed synthetic voice saying "Okay", generated offline as 8 kHz mono 16-bit PCM.
# Keeping the media in source makes certification deterministic and avoids network,
# user content, or runtime TTS dependencies.
_SYNTHETIC_AUDIO_PCM_BASE64 = (
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAD//wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAIA/v8EAAUABAAEAAMABgAGAPn/+//0//7///8BAP//AQD//wAA/P/7//7/"
    "//8CAAkABgAMAAsACgAGAAMA//8CAAAA///8//n/DAAFABQA6v/N/9X/vf8aADYASABWADkAXQBHANn/vf9c/6j/0/8AABEAEgDl"
    "////2f/X/+f/0f8PAEYAYwC/ALEApgCNAFYAAgAJAOb/2//M/3f/iQBXAWkBXgEHAA8Amv/A/9//7P49ApcHRgkFCrsEkgBP/wD+"
    "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"
    "7/4H/3P+L/7y/Z/9y/2D/Wn99v1E/d794f2K/Sv+8f0A/on+IP7V/qj+6v4W/7kAcwFnAawE4ANKBLwGigSHBX0GUwT/BdEFuQRW"
    "BjUFXAVtBYYEwQStA6MDVQM3AukC5wFyASgCjgD1ANgAm/8gALL/4/6C/8v+t/7c/nL+iP5X/mT+Mf4y/ln+F/47/pv+8f3Q/nP+"
    "Xv5Q/4cA6//aAeIC2AGzA74DqwJaAzUEzgLKAyUEXANbBB0EDATdA/gDgQM3A6gDngL8AgMDFAKyAisCowH4AVUBBwEsAZAAjwBa"
    "ACwA8f/X/7D/bv9a/0T/9/4P/9z+yP7L/rP+of6u/qT+cf7K/7X/AwBxAS8BMwGCApUBBAJzApEBaQKsAgsC0QK4AmYCtQJQAmQC"
    "+AHkAe0BMAG5AU0BrQB/AV0AhQCTAHf/9f9W/wL/NP+n/r7+if5K/qf+Fv6N/pf+Xv4a/+b+EP+I/1L/nv/i/7r/GgD+/14ApwBy"
    "AAgB1gCtACUBmAC5AMoAUQC/AIwAVwCcAPD//P/o/53/l/+v/3L/m/+Y/03/Xf9C/zT/Uf80/3D/af91/6r/ZP+d/5L/gP+7/6T/"
    "xv/W/8//BAD//w8ARwA+AF4AbABAAHIAXgA/AH8ATgBeAIMAdgB3AJkAXQBsAF8ALwBcADMAQgA7ADMANwAPACcAAwAEABUA5/8H"
    "AA8AAQAQAOz/GAAPAPf/IQD+/xQAAADe/9v/2//E/8v/8//W/wEAIAD8/yYAFAAdADAAKgA0AFQAVQBhAGEAZABPAF4AVABLAEcA"
    "VABJAEgAUgA8ACsALQAPAP//CAD+/9//6v/c/8//y/+5/9f/xv/K/+j/z/8BAPr/9P8YAAIAFQAlAAkAOQAcABkALQAlACEAMwAz"
    "ACoAMQAlACkAJAAhABgAIwAgAB0AJQAbAA8AEwAEAPv/AgAAAOz/9P/v/+j/5f/c//P/5v/r//3/7f8OAAYAAwAWAAkAEwAWAA0A"
    "FwAOAA4ADwAOAA4ADwARAA0ADAAEAP//BAD5//7/AAD5////AwAAAAUAAgD8/wQA+/8BAAUAAAAHAAgACQAKAPz/BQD7////AAD8"
    "/wAA/v///wEA/f/+/wAA/v8AAAAAAwAEAAAAAgAFAAUABAAJAAkABgAIAAQAAgACAAEAAwABAAMAAAAAAAEA/v///wAA/P8AAAEA"
    "//8DAAEA//8AAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAD/////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
)


def _synthetic_wav_bytes() -> bytes:
    sample_rate = 8_000
    pcm = base64.b64decode(_SYNTHETIC_AUDIO_PCM_BASE64, validate=True)
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + len(pcm)),
            b"WAVEfmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16),
            b"data",
            struct.pack("<I", len(pcm)),
            pcm,
        )
    )


SYNTHETIC_AUDIO_WAV_BYTES = _synthetic_wav_bytes()
SYNTHETIC_AUDIO_WAV_BASE64 = base64.b64encode(
    SYNTHETIC_AUDIO_WAV_BYTES
).decode("ascii")


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
    generation_metadata_url: str | None
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
        elif adapter_contract in {
            "openrouter_audio_transcription_json_v1",
            "openai_compatible_audio_transcription_multipart_v1",
        }:
            endpoint_url = f"{api_base}/audio/transcriptions"
        elif adapter_contract in {
            "openrouter_audio_speech_v1",
            "openai_compatible_audio_speech_v1",
        }:
            endpoint_url = f"{api_base}/audio/speech"
        else:
            endpoint_url = f"{api_base}/chat/completions"
        return cls(
            provider_kind=provider_kind,
            connection_id=connection_id,
            adapter_contract=adapter_contract,
            execution_shape=execution_shape,
            endpoint_url=endpoint_url,
            generation_metadata_url=(
                f"{api_base}/generation" if provider_kind == "openrouter" else None
            ),
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
    def build_authorized_multipart_request(
        client: httpx.AsyncClient,
        target: ProviderMultimodalTarget,
        authorized: AuthorizedProviderTarget,
        *,
        data: Mapping[str, str],
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> httpx.Request:
        return client.build_request(
            "POST",
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers()),
            extensions=authorized.extensions,
            data=dict(data),
            files=dict(files),
        )

    @staticmethod
    async def send_authorized(
        client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        return await client.send(request, stream=True, follow_redirects=False)

    async def fetch_openrouter_generation_model(
        self,
        client: httpx.AsyncClient,
        target: ProviderMultimodalTarget,
        generation_id: str,
        *,
        timeout_seconds: float = (
            OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS
        ),
        on_dispatch: Callable[[], None] | None = None,
    ) -> str | None:
        """Resolve the actual model with one bounded, DNS-pinned metadata GET."""

        metadata_url = target.generation_metadata_url
        clean_generation_id = str(generation_id or "").strip()
        if (
            target.provider_kind != "openrouter"
            or not metadata_url
            or not clean_generation_id
        ):
            return None
        clean_timeout_seconds = max(0.001, float(timeout_seconds))
        authorized = await self.egress_policy.authorize(metadata_url)
        extensions = dict(authorized.extensions)
        extensions["timeout"] = {
            phase: clean_timeout_seconds
            for phase in ("connect", "read", "write", "pool")
        }
        request = client.build_request(
            "GET",
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers()),
            extensions=extensions,
            params={"id": clean_generation_id},
        )
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(clean_timeout_seconds):
                if on_dispatch is not None:
                    on_dispatch()
                response = await client.send(
                    request, stream=True, follow_redirects=False
                )
                if not 200 <= response.status_code < 300:
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_GENERATION_METADATA_BYTES:
                        return None
                    chunks.append(chunk)
                try:
                    payload = json.loads(b"".join(chunks))
                except (TypeError, ValueError):
                    return None
                data = payload.get("data") if isinstance(payload, dict) else None
                model = data.get("model") if isinstance(data, dict) else None
                return (
                    str(model).strip()
                    if isinstance(model, str) and model.strip()
                    else None
                )
        finally:
            if response is not None:
                await response.aclose()


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
