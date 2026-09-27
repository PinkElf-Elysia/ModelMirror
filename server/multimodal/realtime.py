from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

try:
    from server.model_router.egress import (
        AuthorizedProviderTarget,
        ProviderEgressError,
        ProviderEgressPolicy,
    )
    from server.model_router.multimodal_control import (
        PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
        validate_multimodal_adapter,
    )
    from server.model_router.provider_catalog import ProviderCatalogService
    from server.model_router.repository import (
        RouterConnectionNotFound,
        RouterRepositoryError,
        utc_now,
    )
    from server.model_router.schemas import (
        ProviderRealtimeCertificationCompleteRequest,
        ProviderRealtimeCertificationSessionRequest,
        ProviderRealtimeCertificationSessionResponse,
        ProviderWorkloadCertificationSummary,
    )
    from server.model_router.service import ModelRouterService
    from server.model_router.service import RouterServiceError
    from server.model_router.workload_control import (
        PROVIDER_WORKLOAD_CONTRACT_VERSION,
        R8F_REALTIME_PARAMETER_CONTRACT_VERSION,
        ProviderWorkloadCallService,
        ProviderWorkloadCertificationService,
        ProviderWorkloadControlService,
    )
except ModuleNotFoundError:
    from model_router.egress import (
        AuthorizedProviderTarget,
        ProviderEgressError,
        ProviderEgressPolicy,
    )
    from model_router.multimodal_control import (
        PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
        validate_multimodal_adapter,
    )
    from model_router.provider_catalog import ProviderCatalogService
    from model_router.repository import (
        RouterConnectionNotFound,
        RouterRepositoryError,
        utc_now,
    )
    from model_router.schemas import (
        ProviderRealtimeCertificationCompleteRequest,
        ProviderRealtimeCertificationSessionRequest,
        ProviderRealtimeCertificationSessionResponse,
        ProviderWorkloadCertificationSummary,
    )
    from model_router.service import ModelRouterService, RouterServiceError
    from model_router.workload_control import (
        PROVIDER_WORKLOAD_CONTRACT_VERSION,
        R8F_REALTIME_PARAMETER_CONTRACT_VERSION,
        ProviderWorkloadCallService,
        ProviderWorkloadCertificationService,
        ProviderWorkloadControlService,
    )

from .stt import MultimodalServiceError


logger = logging.getLogger("modelmirror.multimodal.realtime")

MAX_REALTIME_SDP_CHARS = 256_000
MAX_REALTIME_ANSWER_CHARS = 512_000
MAX_REALTIME_SESSION_SECONDS = 600
MAX_REALTIME_ERROR_BODY_BYTES = 16_384
REALTIME_MODELS = (
    "gpt-realtime-2.1-mini",
    "gpt-realtime-2.1",
)
REALTIME_VOICES = ("marin", "cedar")
REALTIME_VAD_MODES = ("semantic_vad",)
REALTIME_ENTRY_ID = "realtime_voice"
REALTIME_EXECUTION_SHAPE = "realtime_voice_session"
REALTIME_ADAPTER_CONTRACT = "openai_realtime_sdp_v1"
_CALL_ID_PATTERN = re.compile(r"^rtc_[A-Za-z0-9_-]{3,128}$")
_LANGUAGE_PATTERN = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"
)
_SAFE_PROVIDER_ERROR_TOKEN_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$"
)
_SAFE_PROVIDER_ERROR_PARAM_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.\[\]-]{0,127}$"
)
_REALTIME_DIAGNOSTIC_CODES = frozenset(
    {
        "provider_realtime_answer_content_type_invalid",
        "provider_realtime_answer_content_type_unexpected",
        "provider_realtime_answer_location_invalid",
        "provider_realtime_answer_sdp_invalid",
        "provider_realtime_cancelled_after_dispatch",
        "provider_realtime_transport_connect_error",
        "provider_realtime_transport_connect_timeout",
        "provider_realtime_transport_http_error",
        "provider_realtime_transport_pool_timeout",
        "provider_realtime_transport_protocol_error",
        "provider_realtime_transport_read_error",
        "provider_realtime_transport_read_timeout",
        "provider_realtime_transport_write_error",
        "provider_realtime_transport_write_timeout",
        "provider_realtime_unexpected_after_dispatch",
    }
)


class RealtimeProviderDiagnosticError(MultimodalServiceError):
    """Realtime failure carrying only a bounded, non-sensitive stage code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        diagnostic_code: str,
    ) -> None:
        super().__init__(code, message, status_code=status_code)
        if diagnostic_code not in _REALTIME_DIAGNOSTIC_CODES:
            raise ValueError("invalid realtime diagnostic code")
        self.diagnostic_code = diagnostic_code


class RealtimeCallRequest(BaseModel):
    """Untrusted request envelope; the service returns only sanitized errors."""

    sdp: Any = None
    model_id: Any = "gpt-realtime-2.1-mini"
    voice: Any = "marin"
    vad_mode: Any = "semantic_vad"
    language: Any = "zh-CN"


class RealtimeCallEndEvidence(BaseModel):
    """Bounded browser evidence; never contains SDP, audio, or transcript text."""

    data_channel_open: bool = False
    local_audio_observed: bool = False
    outbound_audio_sent: bool = False
    input_speech_started: bool = False
    input_speech_stopped: bool = False
    response_created: bool = False
    remote_track_observed: bool = False
    output_audio_started: bool = False
    inbound_audio_received: bool = False
    remote_audio_observed: bool = False
    response_done: bool = False
    playback_started: bool = False


class RealtimeCallResponse(BaseModel):
    session_id: str
    sdp_answer: str
    expires_at: str
    model_id: str
    voice: str
    execution_mode: Literal["managed", "legacy"] = "legacy"
    provider_route_receipts: list[dict[str, object]] = Field(default_factory=list)
    provider_dispatch_state: Literal[
        "not_dispatched", "dispatched", "confirmed", "uncertain"
    ] | None = None
    fallback_reason_codes: list[str] = Field(default_factory=list)


class RealtimeCallEndResponse(BaseModel):
    session_id: str
    status: Literal["ended", "expired", "interrupted"]
    ended_at: str
    execution_mode: Literal["managed", "legacy"] = "legacy"
    provider_route_receipts: list[dict[str, object]] = Field(default_factory=list)
    provider_dispatch_state: Literal[
        "not_dispatched", "dispatched", "confirmed", "uncertain"
    ] | None = None
    fallback_reason_codes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class DirectOpenAITarget:
    base_url: str
    api_key: str
    connection_id: str
    safety_identifier: str


@dataclass(frozen=True)
class RealtimeUpstreamCall:
    call_id: str
    sdp_answer: str
    warning_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedRealtimeRequest:
    sdp: str
    model_id: str
    voice: str
    vad_mode: str
    language: str


class OpenAIRealtimeAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        egress_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        self.client_factory = client_factory or self._default_client
        self.egress_policy = egress_policy

    async def create_call(
        self,
        target: DirectOpenAITarget,
        *,
        sdp: str,
        session: dict[str, object],
        on_dispatched: Callable[[], None] | None = None,
        authorized_target: AuthorizedProviderTarget | None = None,
    ) -> RealtimeUpstreamCall:
        files = {
            # OpenAI's unified Realtime WebRTC endpoint expects plain FormData
            # string fields. A filename makes these parts file uploads and the
            # upstream API rejects the otherwise-valid SDP/session payload.
            "sdp": (None, sdp),
            "session": (
                None,
                json.dumps(
                    session,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        }
        try:
            async with self.client_factory() as client:
                response = await self._send_once(
                    client,
                    target,
                    "POST",
                    self._calls_url(target.base_url),
                    headers=self._headers(target),
                    files=files,
                    on_dispatched=on_dispatched,
                    authorized_target=authorized_target,
                )
        except httpx.TimeoutException as exc:
            diagnostic_code = self._transport_diagnostic_code(exc)
            self._log_diagnostic(diagnostic_code)
            raise RealtimeProviderDiagnosticError(
                "realtime_timeout",
                "实时语音连接超时，请检查网络后重试。",
                status_code=504,
                diagnostic_code=diagnostic_code,
            ) from exc
        except ProviderEgressError as exc:
            raise MultimodalServiceError(
                exc.code,
                "实时语音连接未通过 Provider 安全出口校验。",
                status_code=422,
            ) from exc
        except httpx.HTTPError as exc:
            diagnostic_code = self._transport_diagnostic_code(exc)
            self._log_diagnostic(diagnostic_code)
            raise RealtimeProviderDiagnosticError(
                "realtime_unreachable",
                "无法连接实时语音服务，请检查模型服务连接。",
                status_code=502,
                diagnostic_code=diagnostic_code,
            ) from exc

        if response.status_code != 201:
            self._raise_for_status(response)
        # SDP is a line-oriented wire protocol. OpenAI's browser flow passes
        # the answer to setRemoteDescription unchanged; stripping its terminal
        # CRLF can make an otherwise valid answer fail browser parsing.
        answer = response.text
        if (
            not answer.startswith("v=0")
            or len(answer) > MAX_REALTIME_ANSWER_CHARS
        ):
            diagnostic_code = "provider_realtime_answer_sdp_invalid"
            self._log_diagnostic(diagnostic_code)
            raise RealtimeProviderDiagnosticError(
                "invalid_realtime_answer",
                "实时语音服务返回了无效的连接信息，请稍后重试。",
                status_code=502,
                diagnostic_code=diagnostic_code,
            )
        call_id = self._call_id(response.headers.get("location", ""))
        warning_codes: tuple[str, ...] = ()
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("application/sdp"):
            diagnostic_code = "provider_realtime_answer_content_type_unexpected"
            self._log_warning(diagnostic_code)
            warning_codes = (diagnostic_code,)
        return RealtimeUpstreamCall(
            call_id=call_id,
            sdp_answer=answer,
            warning_codes=warning_codes,
        )

    async def hangup(
        self,
        target: DirectOpenAITarget,
        call_id: str,
    ) -> None:
        if not _CALL_ID_PATTERN.fullmatch(call_id):
            raise MultimodalServiceError(
                "invalid_realtime_session",
                "实时语音会话标识无效，请重新发起会话。",
                status_code=409,
            )
        try:
            async with self.client_factory() as client:
                response = await self._send_once(
                    client,
                    target,
                    "POST",
                    (
                        f"{self._calls_url(target.base_url)}/"
                        f"{call_id}/hangup"
                    ),
                    headers=self._headers(target),
                )
        except httpx.TimeoutException as exc:
            raise MultimodalServiceError(
                "realtime_hangup_timeout",
                "结束实时语音连接超时，请稍后重试。",
                status_code=504,
            ) from exc
        except ProviderEgressError as exc:
            raise MultimodalServiceError(
                exc.code,
                "实时语音 Hangup 未通过 Provider 安全出口校验。",
                status_code=422,
            ) from exc
        except httpx.HTTPError as exc:
            raise MultimodalServiceError(
                "realtime_hangup_unreachable",
                "暂时无法通知实时语音服务结束连接，请稍后重试。",
                status_code=502,
            ) from exc
        if response.status_code in {200, 204, 404, 410}:
            return
        self._raise_for_status(response)

    async def _send_once(
        self,
        client: httpx.AsyncClient,
        target: DirectOpenAITarget,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        on_dispatched: Callable[[], None] | None = None,
        authorized_target: AuthorizedProviderTarget | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        """Authorize once, pin one address, and never retry a Realtime POST."""

        authorized = authorized_target
        if authorized is None and self.egress_policy is not None:
            authorized = await self.egress_policy.authorize(url)
        if authorized is not None:
            request = client.build_request(
                method,
                authorized.pinned_urls[0],
                headers=authorized.request_headers(headers),
                extensions=authorized.extensions,
                **kwargs,
            )
        else:
            request = client.build_request(method, url, headers=headers, **kwargs)
        if on_dispatched is not None:
            on_dispatched()
        return await client.send(request, stream=False, follow_redirects=False)

    @staticmethod
    def _headers(target: DirectOpenAITarget) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {target.api_key}",
            "OpenAI-Safety-Identifier": target.safety_identifier,
        }

    @staticmethod
    def _calls_url(base_url: str) -> str:
        root = base_url.rstrip("/")
        if not root.endswith("/v1"):
            root = f"{root}/v1"
        return f"{root}/realtime/calls"

    @staticmethod
    def _call_id(location: str) -> str:
        parsed = urlparse(str(location or "").strip())
        call_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if not _CALL_ID_PATTERN.fullmatch(call_id):
            diagnostic_code = "provider_realtime_answer_location_invalid"
            OpenAIRealtimeAdapter._log_diagnostic(diagnostic_code)
            raise RealtimeProviderDiagnosticError(
                "invalid_realtime_answer",
                "实时语音服务未返回有效会话标识，请稍后重试。",
                status_code=502,
                diagnostic_code=diagnostic_code,
            )
        return call_id

    @staticmethod
    def _transport_diagnostic_code(exc: httpx.HTTPError) -> str:
        """Map HTTPX failures to a fixed code without retaining exception text."""

        mapping: tuple[tuple[type[BaseException], str], ...] = (
            (
                httpx.ConnectTimeout,
                "provider_realtime_transport_connect_timeout",
            ),
            (httpx.ReadTimeout, "provider_realtime_transport_read_timeout"),
            (httpx.WriteTimeout, "provider_realtime_transport_write_timeout"),
            (httpx.PoolTimeout, "provider_realtime_transport_pool_timeout"),
            (
                httpx.RemoteProtocolError,
                "provider_realtime_transport_protocol_error",
            ),
            (
                httpx.LocalProtocolError,
                "provider_realtime_transport_protocol_error",
            ),
            (httpx.ConnectError, "provider_realtime_transport_connect_error"),
            (httpx.ReadError, "provider_realtime_transport_read_error"),
            (httpx.WriteError, "provider_realtime_transport_write_error"),
        )
        for error_type, diagnostic_code in mapping:
            if isinstance(exc, error_type):
                return diagnostic_code
        return "provider_realtime_transport_http_error"

    @staticmethod
    def _log_diagnostic(diagnostic_code: str) -> None:
        if diagnostic_code in _REALTIME_DIAGNOSTIC_CODES:
            logger.warning(
                "realtime_create_uncertain diagnostic_code=%s",
                diagnostic_code,
            )

    @staticmethod
    def _log_warning(diagnostic_code: str) -> None:
        if diagnostic_code in _REALTIME_DIAGNOSTIC_CODES:
            logger.warning(
                "realtime_provider_warning diagnostic_code=%s",
                diagnostic_code,
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        metadata = OpenAIRealtimeAdapter._safe_error_metadata(response)
        if metadata:
            logger.warning(
                (
                    "realtime_provider_error status=%s error_type=%s "
                    "error_code=%s error_param=%s"
                ),
                response.status_code,
                metadata.get("type", "-"),
                metadata.get("code", "-"),
                metadata.get("param", "-"),
            )
        mapping = {
            400: (
                "realtime_request_rejected",
                "实时语音连接参数被拒绝，请刷新页面后重试。",
                422,
            ),
            401: (
                "realtime_credentials_invalid",
                "OpenAI 密钥无效，请在模型服务连接中重新保存。",
                401,
            ),
            403: (
                "realtime_access_denied",
                "当前 OpenAI 项目无权使用实时语音，请检查模型权限。",
                403,
            ),
            402: (
                "realtime_payment_required",
                "实时语音额度不足，请补充额度后重试。",
                402,
            ),
            429: (
                "realtime_rate_limited",
                "实时语音请求过于频繁，请稍后重试。",
                429,
            ),
        }
        code, message, status_code = mapping.get(
            response.status_code,
            (
                "realtime_provider_error",
                "实时语音服务暂时不可用，请稍后重试。",
                502,
            ),
        )
        raise MultimodalServiceError(
            code,
            message,
            status_code=status_code,
        )

    @staticmethod
    def _safe_error_metadata(response: httpx.Response) -> dict[str, str]:
        """Extract only bounded, token-shaped OpenAI error metadata."""

        if len(response.content) > MAX_REALTIME_ERROR_BODY_BYTES:
            return {}
        try:
            payload = response.json()
        except (UnicodeDecodeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        raw_error = payload.get("error")
        if not isinstance(raw_error, dict):
            return {}

        result: dict[str, str] = {}
        for field, pattern in (
            ("type", _SAFE_PROVIDER_ERROR_TOKEN_PATTERN),
            ("code", _SAFE_PROVIDER_ERROR_TOKEN_PATTERN),
            ("param", _SAFE_PROVIDER_ERROR_PARAM_PATTERN),
        ):
            value = raw_error.get(field)
            clean = value.strip() if isinstance(value, str) else ""
            if clean and pattern.fullmatch(clean):
                result[field] = clean
        return result

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=30.0,
                write=30.0,
                pool=10.0,
            ),
            follow_redirects=False,
            trust_env=False,
        )


class RealtimeVoiceService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        adapter: OpenAIRealtimeAdapter | None = None,
        session_seconds: int = MAX_REALTIME_SESSION_SECONDS,
    ) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.tenant_id = router_service.tenant_id
        self.adapter = adapter or OpenAIRealtimeAdapter(
            egress_policy=router_service.egress_policy
        )
        self.call_service = ProviderWorkloadCallService(router_service)
        self.control_service = ProviderWorkloadControlService(router_service)
        self.certification_service = ProviderWorkloadCertificationService(
            router_service
        )
        self.session_seconds = max(
            1,
            min(int(session_seconds), MAX_REALTIME_SESSION_SECONDS),
        )
        self._expiry_tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    async def create(
        self,
        payload: RealtimeCallRequest,
        *,
        idempotency_key: str | None = None,
    ) -> RealtimeCallResponse:
        self._require_enabled()
        request = self._validate_request(payload)
        mode = self._routing_mode()
        if mode == "degraded_required":
            reason = "provider_workload_policy_not_active"
            raise MultimodalServiceError(
                reason,
                "Realtime Voice Managed Provider 策略已降级，会话未创建。",
                status_code=409,
                route_receipt=self._blocked_receipt(reason),
            )
        if mode == "managed_required":
            return await self._create_managed(
                request,
                idempotency_key=idempotency_key,
            )
        return await self._create_legacy(request)

    async def _create_legacy(
        self,
        request: ValidatedRealtimeRequest,
    ) -> RealtimeCallResponse:
        target = self._resolve_target()
        session_id = f"local_rt_{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=self.session_seconds)).isoformat()
        decision_id = self.repository.record_routing_decision(
            self.tenant_id,
            session_id_hash=self._session_hash(session_id),
            engine="native",
            strategy="realtime",
            operation="realtime_voice",
            connection_id=target.connection_id,
            model_id=request.model_id,
            reason_codes=[
                "explicit_realtime_model",
                "operation_realtime_voice",
                request.vad_mode,
            ],
            outcome="connecting",
        )
        self.repository.create_realtime_call(
            self.tenant_id,
            session_id=session_id,
            decision_id=decision_id,
            connection_id=target.connection_id,
            model_id=request.model_id,
            provider="openai",
            voice=request.voice,
            vad_mode=request.vad_mode,
            language=request.language,
            expires_at=expires_at,
        )
        try:
            upstream = await self.adapter.create_call(
                target,
                sdp=request.sdp,
                session=self._session_config(request),
            )
        except MultimodalServiceError as exc:
            self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                status="failed",
                ended_at=utc_now(),
                error_code=exc.code,
            )
            self.repository.update_routing_decision_outcome(
                self.tenant_id,
                decision_id,
                "failed",
            )
            raise

        started_at = utc_now()
        try:
            self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                upstream_call_id=upstream.call_id,
                status="active",
                started_at=started_at,
                error_code=None,
            )
            self.repository.update_routing_decision_outcome(
                self.tenant_id,
                decision_id,
                "active",
            )
        except Exception:
            try:
                await self.adapter.hangup(target, upstream.call_id)
            finally:
                raise
        self._schedule_expiry(session_id)
        return RealtimeCallResponse(
            session_id=session_id,
            sdp_answer=upstream.sdp_answer,
            expires_at=expires_at,
            model_id=request.model_id,
            voice=request.voice,
            fallback_reason_codes=list(upstream.warning_codes),
        )

    async def _create_managed(
        self,
        request: ValidatedRealtimeRequest,
        *,
        idempotency_key: str | None,
    ) -> RealtimeCallResponse:
        clean_key = self._idempotency_key(idempotency_key)
        key_hash = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()
        parent_reference = f"realtime:{key_hash}"
        run_id: str | None = None
        prepared: Any = None
        try:
            run_id = self.call_service.start_stable_run(
                REALTIME_ENTRY_ID,  # type: ignore[arg-type]
                parent_run_reference=parent_reference,
            )
            prepared = await self.call_service.prepare_call(
                run_id=run_id,
                entry_id=REALTIME_ENTRY_ID,  # type: ignore[arg-type]
                execution_shape=REALTIME_EXECUTION_SHAPE,  # type: ignore[arg-type]
                model_id=request.model_id,
                logical_call_key="session-create",
                call_sequence=1,
            )
            if (
                prepared.adapter_contract != REALTIME_ADAPTER_CONTRACT
                or prepared.protocol_version
                != PROVIDER_MULTIMODAL_PROTOCOL_VERSION
                or prepared.multimodal_target is None
                or prepared.multimodal_target.provider_kind != "openai"
                or prepared.multimodal_target.endpoint_url.rstrip("/")
                != "https://api.openai.com/v1/realtime/calls"
            ):
                raise RouterServiceError(
                    "provider_realtime_binding_invalid",
                    "Realtime Voice Binding 必须精确指向官方 OpenAI SDP Adapter。",
                    status_code=409,
                )
            target = self._target_from_connection(
                self.repository.get_connection(
                    self.tenant_id,
                    prepared.connection_id,
                )
            )
        except (
            ProviderEgressError,
            RouterServiceError,
            RouterRepositoryError,
        ) as exc:
            reason = getattr(exc, "code", str(exc))
            if prepared is not None:
                self._complete_managed_call(
                    prepared,
                    status="failed",
                    error_code=reason,
                )
            elif run_id is not None:
                self._complete_managed_run(run_id, "failed", reason)
            raise MultimodalServiceError(
                reason,
                "Realtime Voice Managed Provider 会话在派发前被阻断。",
                status_code=getattr(exc, "status_code", 409),
                route_receipt=self._managed_receipt(
                    run_id=run_id,
                    call_id=(prepared.call_id if prepared is not None else None),
                    model_id=request.model_id,
                    fallback_status="failed",
                    reason_code=reason,
                ),
            ) from exc

        session_id = f"local_rt_{key_hash[:32]}"
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=self.session_seconds)).isoformat()
        decision_id = self.repository.record_routing_decision(
            self.tenant_id,
            session_id_hash=self._session_hash(session_id),
            engine="provider_control",
            strategy="managed_required",
            operation="realtime_voice",
            connection_id=prepared.connection_id,
            model_id=request.model_id,
            reason_codes=[
                "managed_realtime_binding",
                REALTIME_ADAPTER_CONTRACT,
                request.vad_mode,
            ],
            outcome="connecting",
        )
        try:
            self.repository.create_realtime_call(
                self.tenant_id,
                session_id=session_id,
                decision_id=decision_id,
                connection_id=prepared.connection_id,
                model_id=request.model_id,
                provider="openai",
                voice=request.voice,
                vad_mode=request.vad_mode,
                language=request.language,
                expires_at=expires_at,
                workload_run_id=prepared.run_id,
                workload_call_id=prepared.call_id,
                policy_fingerprint=prepared.policy_fingerprint,
                connection_fingerprint=prepared.connection_fingerprint,
                adapter_contract=prepared.adapter_contract,
                protocol_version=prepared.protocol_version,
            )
        except Exception as exc:
            self._complete_managed_call(
                prepared,
                status="failed",
                error_code="provider_realtime_session_store_failed",
            )
            raise MultimodalServiceError(
                "provider_realtime_session_store_failed",
                "Realtime Voice 会话证据无法安全保存，未发送 Provider 请求。",
                status_code=503,
                route_receipt=self._managed_receipt(
                    run_id=prepared.run_id,
                    call_id=prepared.call_id,
                    model_id=request.model_id,
                    fallback_status="failed",
                    reason_code="provider_realtime_session_store_failed",
                ),
            ) from exc

        dispatched = False

        def mark_dispatched() -> None:
            nonlocal dispatched
            self.call_service.mark_dispatched(prepared)
            dispatched = True
            updated = self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                post_dispatched=True,
                provider_dispatch_state="dispatched",
            )
            if updated is None:
                raise RouterRepositoryError(
                    "provider_realtime_session_not_found"
                )

        started = time.perf_counter()
        try:
            upstream = await self.adapter.create_call(
                target,
                sdp=request.sdp,
                session=self._session_config(request),
                on_dispatched=mark_dispatched,
                authorized_target=prepared.authorized_target,
            )
        except asyncio.CancelledError:
            reason = (
                "provider_realtime_create_result_uncertain"
                if dispatched
                else "provider_workload_call_cancelled"
            )
            self._fail_managed_create(
                prepared,
                session_id=session_id,
                decision_id=decision_id,
                dispatched=dispatched,
                error_code=reason,
                uncertain=dispatched,
            )
            raise
        except MultimodalServiceError as exc:
            uncertain = dispatched and exc.code in {
                "realtime_timeout",
                "realtime_unreachable",
                "invalid_realtime_answer",
            }
            reason = (
                "provider_realtime_create_result_uncertain"
                if uncertain
                else exc.code
            )
            receipt = self._fail_managed_create(
                prepared,
                session_id=session_id,
                decision_id=decision_id,
                dispatched=dispatched,
                error_code=reason,
                uncertain=uncertain,
                e2e_ms=(time.perf_counter() - started) * 1000,
            )
            message = (
                "Realtime 创建结果不确定；请先检查并显式结束可能存在的会话，"
                "不要使用同一幂等键重新创建。"
                if uncertain
                else exc.message
            )
            raise MultimodalServiceError(
                reason,
                message,
                status_code=exc.status_code,
                route_receipt=receipt,
            ) from exc
        except Exception as exc:
            reason = (
                "provider_realtime_create_result_uncertain"
                if dispatched
                else getattr(exc, "code", "provider_realtime_preflight_failed")
            )
            receipt = self._fail_managed_create(
                prepared,
                session_id=session_id,
                decision_id=decision_id,
                dispatched=dispatched,
                error_code=reason,
                uncertain=dispatched,
                e2e_ms=(time.perf_counter() - started) * 1000,
            )
            raise MultimodalServiceError(
                reason,
                (
                    "Realtime 创建结果不确定；请先检查并显式结束可能存在的会话，"
                    "不要自动重试或切换 Provider。"
                    if dispatched
                    else "Realtime Voice 会话在派发前被阻断。"
                ),
                status_code=getattr(exc, "status_code", 502 if dispatched else 409),
                route_receipt=receipt,
            ) from exc

        started_at = utc_now()
        try:
            updated = self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                upstream_call_id=upstream.call_id,
                status="active",
                started_at=started_at,
                error_code=None,
                provider_dispatch_state="confirmed",
                post_dispatched=True,
                provider_terminal_status="active",
            )
            if updated is None:
                raise RouterRepositoryError(
                    "provider_realtime_session_not_found"
                )
            self.repository.update_routing_decision_outcome(
                self.tenant_id,
                decision_id,
                "active",
            )
        except Exception as exc:
            try:
                await self.adapter.hangup(target, upstream.call_id)
            except MultimodalServiceError:
                pass
            receipt = self._fail_managed_create(
                prepared,
                session_id=session_id,
                decision_id=decision_id,
                dispatched=True,
                error_code="provider_realtime_persistence_uncertain",
                uncertain=True,
                e2e_ms=(time.perf_counter() - started) * 1000,
            )
            raise MultimodalServiceError(
                "provider_realtime_persistence_uncertain",
                "Realtime 会话已联系 Provider，但本地证据保存失败；请显式检查并结束会话。",
                status_code=503,
                route_receipt=receipt,
            ) from exc

        self._schedule_expiry(session_id)
        return RealtimeCallResponse(
            session_id=session_id,
            sdp_answer=upstream.sdp_answer,
            expires_at=expires_at,
            model_id=request.model_id,
            voice=request.voice,
            execution_mode="managed",
            provider_route_receipts=[
                self._managed_receipt(
                    run_id=prepared.run_id,
                    call_id=prepared.call_id,
                    model_id=request.model_id,
                    fallback_status="running",
                )
            ],
            provider_dispatch_state="confirmed",
            fallback_reason_codes=list(upstream.warning_codes),
        )

    def _routing_mode(
        self,
    ) -> Literal["legacy", "managed_required", "degraded_required"]:
        if not self.control_service.feature_enabled(REALTIME_ENTRY_ID):  # type: ignore[arg-type]
            return "legacy"
        policy = self.control_service.get_policy(REALTIME_ENTRY_ID)  # type: ignore[arg-type]
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    @staticmethod
    def _idempotency_key(value: str | None) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > 200:
            raise MultimodalServiceError(
                "invalid_idempotency_key",
                "Managed Realtime Voice 要求 1 至 200 个字符的 Idempotency-Key。",
                status_code=422,
                route_receipt=RealtimeVoiceService._blocked_receipt(
                    "invalid_idempotency_key"
                ),
            )
        return clean

    @staticmethod
    def _blocked_receipt(reason_code: str) -> dict[str, object]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": REALTIME_ENTRY_ID,
            "routing_mode": "managed_required",
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }

    def _complete_managed_call(
        self,
        prepared: Any,
        *,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        error_code: str | None,
        e2e_ms: float | None = None,
    ) -> None:
        try:
            self.call_service.complete_call(
                prepared,
                status=status,
                result_class=(
                    "success"
                    if status == "passed"
                    else "transport_error"
                    if status == "uncertain"
                    else "provider_error"
                ),
                error_code=error_code,
                actual_model=(prepared.model_id if status == "passed" else None),
                e2e_ms=e2e_ms,
                complete_run_id=prepared.run_id,
                run_result_class=f"realtime_{status}",
                run_reason_codes=([error_code] if error_code else []),
            )
        except RouterRepositoryError as exc:
            if str(exc) not in {
                "provider_workload_call_not_running",
                "provider_workload_run_not_running",
            }:
                raise

    def _complete_managed_run(
        self,
        run_id: str,
        status: Literal["failed", "uncertain", "cancelled"],
        reason_code: str,
    ) -> None:
        try:
            self.call_service.complete_run(
                run_id,
                status=status,
                result_class=f"realtime_{status}",
                reason_codes=[reason_code],
            )
        except RouterRepositoryError as exc:
            if str(exc) != "provider_workload_run_not_running":
                raise

    def _fail_managed_create(
        self,
        prepared: Any,
        *,
        session_id: str,
        decision_id: str,
        dispatched: bool,
        error_code: str,
        uncertain: bool = False,
        e2e_ms: float | None = None,
    ) -> dict[str, object]:
        status: Literal["failed", "uncertain"] = (
            "uncertain" if uncertain else "failed"
        )
        dispatch_state = (
            "uncertain"
            if uncertain
            else "confirmed"
            if dispatched
            else "not_dispatched"
        )
        try:
            self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                status="interrupted" if uncertain else "failed",
                ended_at=utc_now(),
                error_code=error_code,
                provider_dispatch_state=dispatch_state,
                post_dispatched=dispatched,
                provider_terminal_status=status,
            )
        finally:
            self._complete_managed_call(
                prepared,
                status=status,
                error_code=error_code,
                e2e_ms=e2e_ms,
            )
            self.repository.update_routing_decision_outcome(
                self.tenant_id,
                decision_id,
                status,
            )
        return self._managed_receipt(
            run_id=prepared.run_id,
            call_id=prepared.call_id,
            model_id=prepared.model_id,
            fallback_status=status,
            reason_code=error_code,
        )

    def _managed_receipt(
        self,
        *,
        run_id: str | None,
        call_id: str | None,
        model_id: str,
        fallback_status: str,
        reason_code: str | None = None,
    ) -> dict[str, object]:
        run_status = fallback_status
        reason_codes = [reason_code] if reason_code else []
        if run_id:
            try:
                run = self.repository.get_workload_run(self.tenant_id, run_id)
                run_status = str(run.get("status") or fallback_status)
                stored_reasons = json.loads(
                    str(run.get("reason_codes_json") or "[]")
                )
                if isinstance(stored_reasons, list):
                    reason_codes = [str(item) for item in stored_reasons]
            except (RouterRepositoryError, TypeError, ValueError):
                pass
        calls: list[dict[str, object]] = []
        if call_id:
            call = self.repository.get_workload_call(self.tenant_id, call_id)
            if call is not None:
                calls.append(
                    {
                        "call_sequence": int(call.get("call_sequence") or 1),
                        "model_id": str(call.get("requested_model") or model_id),
                        "actual_model": (
                            str(call["actual_model"])
                            if call.get("actual_model")
                            else None
                        ),
                        "dispatched": bool(call.get("dispatched")),
                        "status": str(call.get("status") or fallback_status),
                        "error_code": (
                            str(call["error_code"])
                            if call.get("error_code")
                            else None
                        ),
                        "prompt_tokens": call.get("prompt_tokens"),
                        "completion_tokens": call.get("completion_tokens"),
                        "total_tokens": call.get("total_tokens"),
                    }
                )
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": REALTIME_ENTRY_ID,
            "routing_mode": "managed_required",
            "run_reference": run_id or "blocked_before_dispatch",
            "status": run_status,
            "call_count": sum(
                1 for item in calls if bool(item.get("dispatched"))
            ),
            "reason_codes": reason_codes,
            "calls": calls,
        }

    async def begin_certification(
        self,
        connection_id: str,
        payload: ProviderRealtimeCertificationSessionRequest,
        *,
        idempotency_key: str,
    ) -> ProviderRealtimeCertificationSessionResponse:
        if not self.certification_service.enabled():
            raise RouterServiceError(
                "provider_workload_certification_disabled",
                "Provider 资格认证已由部署配置关闭。",
                status_code=503,
            )
        if not payload.acknowledge_billed_call:
            raise RouterServiceError(
                "billed_call_acknowledgement_required",
                "运行 Realtime 资格认证前必须确认本次调用可能产生费用。",
                status_code=422,
            )
        try:
            clean_key = self._idempotency_key(idempotency_key)
            request = self._validate_request(
                RealtimeCallRequest(
                    sdp=payload.offer_sdp,
                    model_id=payload.model_id,
                    voice="marin",
                    vad_mode="semantic_vad",
                    language="en-US",
                )
            )
        except MultimodalServiceError as exc:
            raise RouterServiceError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc
        connection = self.repository.get_connection(
            self.tenant_id,
            connection_id,
        )
        validate_multimodal_adapter(
            contract=payload.adapter_contract,
            execution_shape=REALTIME_EXECUTION_SHAPE,  # type: ignore[arg-type]
            provider_kind=connection.kind,
            scopes=connection.scopes,
        )
        if not connection.enabled or connection.health != "online":
            raise RouterServiceError(
                "provider_connection_not_online",
                "Realtime 资格认证要求连接已启用且健康状态为在线。",
                status_code=409,
            )
        try:
            target = self._target_from_connection(connection)
        except MultimodalServiceError as exc:
            raise RouterServiceError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc
        idempotency_hash = hashlib.sha256(
            clean_key.encode("utf-8")
        ).hexdigest()
        existing = self.repository.get_workload_certification_by_idempotency(
            self.tenant_id,
            connection_id,
            idempotency_hash,
        )
        if existing is not None:
            if (
                str(existing.get("execution_shape") or "")
                != REALTIME_EXECUTION_SHAPE
                or str(existing.get("requested_model") or "")
                != request.model_id
                or str(existing.get("adapter_contract") or "")
                != REALTIME_ADAPTER_CONTRACT
            ):
                raise RouterServiceError(
                    "provider_workload_certification_idempotency_conflict",
                    "该 Idempotency-Key 已用于另一份资格配置。",
                    status_code=409,
                )
            raise RouterServiceError(
                "provider_realtime_certification_replay_blocked",
                "该 Realtime 认证键已有证据；SDP Answer 不会持久化或重新派发。",
                status_code=409,
            )

        refreshed = await ProviderCatalogService(
            self.router_service
        ).refresh_connection(connection_id)
        if refreshed.status != "succeeded" or refreshed.truncated:
            raise RouterServiceError(
                "provider_workload_catalog_stale",
                "最新完整模型目录不可用，未发送 Realtime 认证请求。",
                status_code=409,
            )
        connection, _api_key, connection_fingerprint = (
            self.repository.get_connection_credential_snapshot(
                self.tenant_id,
                connection_id,
            )
        )
        validate_multimodal_adapter(
            contract=payload.adapter_contract,
            execution_shape=REALTIME_EXECUTION_SHAPE,  # type: ignore[arg-type]
            provider_kind=connection.kind,
            scopes=connection.scopes,
        )
        try:
            target = self._target_from_connection(connection)
        except MultimodalServiceError as exc:
            raise RouterServiceError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc
        refresh_record = next(
            (
                item
                for item in self.repository.list_catalog_refreshes(
                    self.tenant_id,
                    connection_id=connection_id,
                    limit=500,
                )
                if str(item["id"]) == refreshed.refresh_id
            ),
            None,
        )
        if (
            refresh_record is None
            or str(refresh_record.get("connection_fingerprint") or "")
            != connection_fingerprint
            or not self.repository.list_catalog_models(
                self.tenant_id,
                connection_id=connection_id,
                model_id=request.model_id,
                status="active",
                limit=1,
            )
        ):
            raise RouterServiceError(
                "provider_workload_certification_model_not_found",
                "精确 Realtime 模型不在当前完整目录中，未发送认证请求。",
                status_code=409,
            )

        profile = self._certification_profile(request.model_id)
        profile_fingerprint = hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        certification_id = f"workcert_{uuid.uuid4().hex}"
        session_id = f"mmcertsession_{uuid.uuid4().hex}"
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.session_seconds)
        ).isoformat()
        try:
            row, created = self.repository.claim_workload_certification(
                self.tenant_id,
                certification_id=certification_id,
                connection_id=connection_id,
                connection_fingerprint=connection_fingerprint,
                contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
                execution_shape=REALTIME_EXECUTION_SHAPE,
                requested_model=request.model_id,
                profile=profile,
                profile_fingerprint=profile_fingerprint,
                idempotency_key_hash=idempotency_hash,
                adapter_contract=REALTIME_ADAPTER_CONTRACT,
                protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
                multimodal_session_id=session_id,
                multimodal_session_expires_at=expires_at,
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                str(exc),
                "Realtime 认证证据无法安全创建，未发送 Provider 请求。",
                status_code=409,
            ) from exc
        if not created:
            raise RouterServiceError(
                "provider_realtime_certification_replay_blocked",
                "该 Realtime 认证键已有证据；系统不会重新派发。",
                status_code=409,
            )

        try:
            self.repository.create_realtime_call(
                self.tenant_id,
                session_id=session_id,
                decision_id=None,
                connection_id=connection_id,
                model_id=request.model_id,
                provider="openai",
                voice=request.voice,
                vad_mode=request.vad_mode,
                language=request.language,
                expires_at=expires_at,
                connection_fingerprint=connection_fingerprint,
                adapter_contract=REALTIME_ADAPTER_CONTRACT,
                protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
            )
            authorized = await self.router_service.egress_policy.authorize(
                f"{target.base_url.rstrip('/')}/realtime/calls"
                if target.base_url.rstrip("/").endswith("/v1")
                else f"{target.base_url.rstrip('/')}/v1/realtime/calls"
            )
        except Exception as exc:
            self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code="provider_realtime_certification_preflight_failed",
                uncertain=False,
            )
            raise RouterServiceError(
                getattr(exc, "code", "provider_realtime_certification_preflight_failed"),
                "Realtime 认证在派发前被阻断。",
                status_code=getattr(exc, "status_code", 409),
            ) from exc

        dispatched = False

        def mark_dispatched() -> None:
            nonlocal dispatched
            self.repository.update_multimodal_certification_session(
                self.tenant_id,
                session_id,
                status="running",
                provider_dispatch_state="dispatched",
                post_dispatched=True,
                expected_connection_fingerprint=connection_fingerprint,
            )
            dispatched = True
            updated = self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                post_dispatched=True,
                provider_dispatch_state="dispatched",
            )
            if updated is None:
                raise RouterRepositoryError(
                    "provider_realtime_session_not_found"
                )

        started = time.perf_counter()
        try:
            upstream = await self.adapter.create_call(
                target,
                sdp=request.sdp,
                session=self._session_config(request),
                on_dispatched=mark_dispatched,
                authorized_target=authorized,
            )
        except asyncio.CancelledError:
            self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code=(
                    "provider_realtime_create_result_uncertain"
                    if dispatched
                    else "provider_workload_cancelled"
                ),
                uncertain=dispatched,
                e2e_ms=(time.perf_counter() - started) * 1000,
                diagnostic_code=(
                    "provider_realtime_cancelled_after_dispatch"
                    if dispatched
                    else None
                ),
            )
            raise
        except MultimodalServiceError as exc:
            uncertain = dispatched and exc.code in {
                "realtime_timeout",
                "realtime_unreachable",
                "invalid_realtime_answer",
            }
            self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code=(
                    "provider_realtime_create_result_uncertain"
                    if uncertain
                    else exc.code
                ),
                uncertain=uncertain,
                e2e_ms=(time.perf_counter() - started) * 1000,
                diagnostic_code=(
                    exc.diagnostic_code
                    if uncertain
                    and isinstance(exc, RealtimeProviderDiagnosticError)
                    else None
                ),
            )
            raise RouterServiceError(
                (
                    "provider_realtime_create_result_uncertain"
                    if uncertain
                    else exc.code
                ),
                (
                    "Realtime 认证创建结果不确定；同一幂等键不会重放。"
                    if uncertain
                    else exc.message
                ),
                status_code=exc.status_code,
            ) from exc
        except Exception as exc:
            diagnostic_code = (
                "provider_realtime_unexpected_after_dispatch"
                if dispatched
                else None
            )
            if diagnostic_code is not None:
                self.adapter._log_diagnostic(diagnostic_code)
            self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code=(
                    "provider_realtime_create_result_uncertain"
                    if dispatched
                    else "provider_realtime_certification_preflight_failed"
                ),
                uncertain=dispatched,
                e2e_ms=(time.perf_counter() - started) * 1000,
                diagnostic_code=diagnostic_code,
            )
            raise RouterServiceError(
                (
                    "provider_realtime_create_result_uncertain"
                    if dispatched
                    else getattr(
                        exc,
                        "code",
                        "provider_realtime_certification_preflight_failed",
                    )
                ),
                "Realtime 认证未完成；系统不会自动重试或切换 Provider。",
                status_code=getattr(exc, "status_code", 502 if dispatched else 409),
            ) from exc

        try:
            if upstream.warning_codes:
                self.repository.update_running_workload_certification_warnings(
                    self.tenant_id,
                    certification_id,
                    list(upstream.warning_codes),
                )
            self.repository.update_multimodal_certification_session(
                self.tenant_id,
                session_id,
                status="running",
                provider_dispatch_state="confirmed",
                post_dispatched=True,
                upstream_operation_id=upstream.call_id,
                expected_connection_fingerprint=connection_fingerprint,
            )
            updated = self.repository.update_realtime_call(
                self.tenant_id,
                session_id,
                upstream_call_id=upstream.call_id,
                status="active",
                started_at=utc_now(),
                provider_dispatch_state="confirmed",
                post_dispatched=True,
                provider_terminal_status="active",
            )
            if updated is None:
                raise RouterRepositoryError(
                    "provider_realtime_session_not_found"
                )
        except Exception as exc:
            try:
                await self.adapter.hangup(target, upstream.call_id)
            except MultimodalServiceError:
                pass
            self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code="provider_realtime_persistence_uncertain",
                uncertain=True,
                e2e_ms=(time.perf_counter() - started) * 1000,
            )
            raise RouterServiceError(
                "provider_realtime_persistence_uncertain",
                "Realtime 认证会话已联系 Provider，但本地证据保存失败。",
                status_code=503,
            ) from exc

        self._schedule_expiry(
            session_id,
            certification_id=certification_id,
        )
        return ProviderRealtimeCertificationSessionResponse(
            certification_id=str(row["id"]),
            status="running",
            answer_sdp=upstream.sdp_answer,
            expires_at=expires_at,
            provider_dispatch_state="confirmed",
            retry_allowed=False,
        )

    async def complete_certification(
        self,
        certification_id: str,
        payload: ProviderRealtimeCertificationCompleteRequest,
    ) -> ProviderWorkloadCertificationSummary:
        session = self.repository.get_multimodal_certification_session(
            self.tenant_id,
            certification_id=certification_id,
        )
        certification = self.repository.get_workload_certification(
            self.tenant_id,
            certification_id,
        )
        if session is None or certification is None:
            raise RouterServiceError(
                "provider_workload_certification_not_found",
                "未找到该 Realtime 认证会话。",
                status_code=404,
            )
        connection = self.repository.get_connection(
            self.tenant_id,
            str(certification["connection_id"]),
        )
        if str(certification.get("status") or "") != "running":
            return self.certification_service._summary(  # noqa: SLF001
                connection,
                certification,
            )
        if (
            str(certification.get("execution_shape") or "")
            != REALTIME_EXECUTION_SHAPE
            or str(certification.get("adapter_contract") or "")
            != REALTIME_ADAPTER_CONTRACT
            or str(session.get("adapter_contract") or "")
            != REALTIME_ADAPTER_CONTRACT
        ):
            raise RouterServiceError(
                "provider_realtime_certification_mismatch",
                "Realtime 认证证据与当前 Adapter 不一致。",
                status_code=409,
            )
        session_id = str(session["id"])
        realtime = self.repository.get_realtime_call(
            self.tenant_id,
            session_id,
        )
        if realtime is None:
            return self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code="provider_realtime_session_not_found",
                uncertain=True,
            )
        claimed, claimed_hangup = self.repository.claim_realtime_hangup(
            self.tenant_id,
            session_id,
        )
        if claimed is None:
            return self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code="provider_realtime_session_not_found",
                uncertain=True,
            )
        if not claimed_hangup:
            raise RouterServiceError(
                "provider_realtime_hangup_in_progress",
                "Realtime 认证会话正在结束，系统不会重复发送 Hangup。",
                status_code=409,
            )
        upstream_call_id = str(claimed.get("upstream_call_id") or "")
        if not upstream_call_id:
            return self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code="provider_realtime_call_id_missing",
                uncertain=True,
            )
        try:
            target = self._resolve_existing_target(claimed)
            await self.adapter.hangup(target, upstream_call_id)
        except (MultimodalServiceError, RouterRepositoryError) as exc:
            return self._finalize_certification_failure(
                connection,
                certification_id=certification_id,
                session_id=session_id,
                error_code="provider_realtime_hangup_result_uncertain",
                uncertain=True,
            )

        passed = bool(payload.media_observed and payload.hangup_observed)
        warning_codes = list(
            dict.fromkeys(
                [
                    *self._stored_warning_codes(certification),
                    *payload.browser_diagnostic_codes,
                ]
            )
        )
        checks: dict[str, bool] = {
            "http_ok": True,
            "content_observed": bool(payload.media_observed),
            "response_complete": True,
            "media_format_verified": bool(payload.media_observed),
            "actual_model_verified": True,
            "multimodal_adapter_verified": True,
            "manual_media_verified": bool(payload.media_observed),
            "hangup_verified": bool(payload.hangup_observed),
        }
        status = "passed" if passed else "failed"
        error_code = (
            None
            if passed
            else (
                payload.browser_error_code
                or "provider_realtime_manual_check_failed"
            )
        )
        completed, _completed_session = (
            self.repository.complete_multimodal_workload_certification(
                self.tenant_id,
                certification_id,
                session_id,
                status=status,
                checks=checks,
                warning_codes=warning_codes,
                error_code=error_code,
                actual_model=str(certification["requested_model"]),
            )
        )
        self.repository.update_realtime_call(
            self.tenant_id,
            session_id,
            status="ended" if passed else "interrupted",
            ended_at=utc_now(),
            error_code=error_code,
            provider_dispatch_state="confirmed",
            provider_terminal_status=status,
        )
        self._cancel_expiry(session_id)
        return self.certification_service._summary(  # noqa: SLF001
            connection,
            completed,
        )

    @staticmethod
    def _stored_warning_codes(certification: dict[str, object]) -> list[str]:
        try:
            values = json.loads(str(certification.get("warnings_json") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(values, list):
            return []
        return [
            value
            for value in values
            if isinstance(value, str) and value in _REALTIME_DIAGNOSTIC_CODES
        ]

    def _finalize_certification_failure(
        self,
        connection: Any,
        *,
        certification_id: str,
        session_id: str,
        error_code: str,
        uncertain: bool,
        e2e_ms: float | None = None,
        diagnostic_code: str | None = None,
    ) -> ProviderWorkloadCertificationSummary:
        warning_codes = (
            [diagnostic_code]
            if diagnostic_code in _REALTIME_DIAGNOSTIC_CODES
            else []
        )
        status = "uncertain" if uncertain else "failed"
        session = self.repository.get_multimodal_certification_session(
            self.tenant_id,
            certification_id=certification_id,
        )
        post_dispatched = bool(session and session.get("post_dispatched"))
        self.repository.update_realtime_call(
            self.tenant_id,
            session_id,
            status="interrupted" if uncertain else "failed",
            ended_at=utc_now(),
            error_code=error_code,
            provider_dispatch_state=(
                "uncertain"
                if uncertain
                else "confirmed"
                if post_dispatched
                else "not_dispatched"
            ),
            provider_terminal_status=status,
        )
        completed, _completed_session = (
            self.repository.complete_multimodal_workload_certification(
                self.tenant_id,
                certification_id,
                session_id,
                status=status,
                checks={
                    "http_ok": False,
                    "content_observed": False,
                    "response_complete": False,
                    "media_format_verified": False,
                    "actual_model_verified": False,
                    "multimodal_adapter_verified": False,
                    "manual_media_verified": False,
                    "hangup_verified": False,
                },
                warning_codes=warning_codes,
                error_code=error_code,
                actual_model=None,
                e2e_ms=e2e_ms,
            )
        )
        self._cancel_expiry(session_id)
        return self.certification_service._summary(  # noqa: SLF001
            connection,
            completed,
        )

    @staticmethod
    def _certification_profile(model_id: str) -> dict[str, object]:
        return {
            "execution_shape": REALTIME_EXECUTION_SHAPE,
            "model_id": model_id,
            "adapter_contract": REALTIME_ADAPTER_CONTRACT,
            "protocol_version": PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
            "realtime_parameter_contract_version": (
                R8F_REALTIME_PARAMETER_CONTRACT_VERSION
            ),
            "transport": "webrtc_sdp",
            "endpoint": "/v1/realtime/calls",
            "browser_assisted": True,
            "manual_media_confirmation": True,
            "sdp_persisted": False,
            "media_persisted": False,
            "max_session_seconds": MAX_REALTIME_SESSION_SECONDS,
        }

    async def end(
        self,
        session_id: str,
        *,
        reason: Literal["ended", "expired", "interrupted"] = "ended",
        evidence: RealtimeCallEndEvidence | None = None,
    ) -> RealtimeCallEndResponse:
        clean_session_id = self._session_id(session_id)
        row = self.repository.get_realtime_call(
            self.tenant_id,
            clean_session_id,
        )
        if row is None:
            raise MultimodalServiceError(
                "realtime_session_not_found",
                "未找到该实时语音会话，可能已结束或不属于当前用户。",
                status_code=404,
            )
        current_status = str(row.get("status") or "")
        if current_status in {"ended", "expired", "interrupted"}:
            return self._end_response(row)
        if current_status == "failed":
            raise MultimodalServiceError(
                "realtime_session_failed",
                "该实时语音会话未能建立，请重新发起。",
                status_code=409,
            )
        row, hangup_claimed = self.repository.claim_realtime_hangup(
            self.tenant_id,
            clean_session_id,
        )
        if row is None:
            raise MultimodalServiceError(
                "realtime_session_not_found",
                "未找到该实时语音会话。",
                status_code=404,
            )
        if not hangup_claimed:
            claimed_status = str(row.get("status") or "")
            if claimed_status in {"ended", "expired", "interrupted"}:
                return self._end_response(row)
            raise MultimodalServiceError(
                "realtime_hangup_in_progress",
                "该实时语音会话正在结束，系统不会重复发送 Hangup。",
                status_code=409,
                route_receipt=(
                    self._managed_receipt(
                        run_id=str(row.get("workload_run_id") or "") or None,
                        call_id=str(row.get("workload_call_id") or "") or None,
                        model_id=str(row.get("model_id") or ""),
                        fallback_status="running",
                    )
                    if row.get("workload_run_id")
                    else None
                ),
            )
        upstream_call_id = str(row.get("upstream_call_id") or "").strip()
        if upstream_call_id:
            target = self._resolve_existing_target(row)
            try:
                await self.adapter.hangup(target, upstream_call_id)
            except MultimodalServiceError as exc:
                response = self._finish(
                    row,
                    status="interrupted",
                    error_code="provider_realtime_hangup_result_uncertain",
                )
                raise MultimodalServiceError(
                    "provider_realtime_hangup_result_uncertain",
                    "Realtime Hangup 结果不确定；会话已在本地封闭，系统不会自动重连或切换 Provider。",
                    status_code=exc.status_code,
                    route_receipt=(
                        response.provider_route_receipts[0]
                        if response.provider_route_receipts
                        else None
                    ),
                ) from exc
        return self._finish(row, status=reason, evidence=evidence)

    async def recover_active(self) -> None:
        for row in self.repository.list_active_realtime_calls(self.tenant_id):
            await self._interrupt_for_lifecycle(
                row,
                success_code="realtime_restart_interrupted",
                uncertain_code="realtime_restart_hangup_result_uncertain",
            )

    async def shutdown(self) -> None:
        self._closing = True
        rows = self.repository.list_active_realtime_calls(self.tenant_id)
        for row in rows:
            await self._interrupt_for_lifecycle(
                row,
                success_code="realtime_server_shutdown",
                uncertain_code="realtime_shutdown_hangup_result_uncertain",
            )
        tasks = list(self._expiry_tasks.values())
        self._expiry_tasks.clear()
        for task in tasks:
            if task is not asyncio.current_task():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _interrupt_for_lifecycle(
        self,
        row: dict[str, object],
        *,
        success_code: str,
        uncertain_code: str,
    ) -> None:
        """Close one persisted session without replaying an in-flight Hangup."""

        if not row.get("upstream_call_id"):
            self._finish(
                row,
                status="interrupted",
                error_code=success_code,
            )
            return
        claimed, did_claim = self.repository.claim_realtime_hangup(
            self.tenant_id,
            str(row["id"]),
        )
        if claimed is None:
            return
        if not did_claim:
            self._finish(
                claimed,
                status="interrupted",
                error_code=uncertain_code,
            )
            return
        try:
            target = self._resolve_existing_target(claimed)
            await self.adapter.hangup(
                target,
                str(claimed["upstream_call_id"]),
            )
            self._finish(
                claimed,
                status="interrupted",
                error_code=success_code,
            )
        except (
            MultimodalServiceError,
            RouterConnectionNotFound,
            RouterRepositoryError,
        ):
            self._finish(
                claimed,
                status="interrupted",
                error_code=uncertain_code,
            )

    def _finish(
        self,
        row: dict[str, object],
        *,
        status: Literal["ended", "expired", "interrupted"],
        error_code: str | None = None,
        evidence: RealtimeCallEndEvidence | None = None,
    ) -> RealtimeCallEndResponse:
        if (
            error_code is None
            and row.get("workload_run_id")
            and status in {"ended", "expired"}
        ):
            error_code = self._runtime_evidence_error(evidence)
        ended_at = datetime.now(UTC)
        started_at = self._parse_time(row.get("started_at")) or self._parse_time(
            row.get("created_at")
        )
        duration = (
            max(0.0, (ended_at - started_at).total_seconds())
            if started_at is not None
            else None
        )
        session_id = str(row["id"])
        updated = self.repository.update_realtime_call(
            self.tenant_id,
            session_id,
            status=status,
            ended_at=ended_at.isoformat(),
            duration_seconds=duration,
            cost_kind="unavailable",
            error_code=error_code,
            provider_dispatch_state=(
                "uncertain"
                if status == "interrupted" and error_code
                else str(row.get("provider_dispatch_state") or "confirmed")
            ),
            provider_terminal_status=status,
        )
        decision_id = str(row.get("decision_id") or "").strip()
        if decision_id:
            self.repository.update_routing_decision_usage(
                self.tenant_id,
                decision_id,
                outcome=status,
                media_seconds=duration,
                settled_cost_usd=None,
                cost_status="unavailable",
            )
        self._cancel_expiry(session_id)
        if updated is None:
            raise MultimodalServiceError(
                "realtime_session_not_found",
                "未找到该实时语音会话。",
                status_code=404,
            )
        if updated.get("workload_run_id") and updated.get("workload_call_id"):
            managed_status = (
                "passed"
                if status in {"ended", "expired"} and error_code is None
                else "uncertain"
            )
            try:
                self.repository.complete_workload_call(
                    self.tenant_id,
                    str(updated["workload_call_id"]),
                    status=managed_status,
                    result_class=(
                        "success"
                        if managed_status == "passed"
                        else "runtime_evidence_incomplete"
                        if status in {"ended", "expired"}
                        else "transport_error"
                    ),
                    error_code=error_code,
                    actual_model=(
                        str(updated.get("model_id") or "")
                        if managed_status == "passed"
                        else None
                    ),
                    e2e_ms=(duration * 1000 if duration is not None else None),
                    complete_run_id=str(updated["workload_run_id"]),
                    run_result_class=f"realtime_{managed_status}",
                    run_reason_codes=([error_code] if error_code else []),
                )
            except RouterRepositoryError as exc:
                if str(exc) not in {
                    "provider_workload_call_not_running",
                    "provider_workload_run_not_running",
                }:
                    raise
        elif (
            status == "interrupted"
            and str(updated.get("adapter_contract") or "")
            == REALTIME_ADAPTER_CONTRACT
        ):
            session = self.repository.get_multimodal_certification_session(
                self.tenant_id,
                session_id=session_id,
            )
            certification = (
                self.repository.get_workload_certification(
                    self.tenant_id,
                    str(session["certification_id"]),
                )
                if session is not None
                else None
            )
            if (
                session is not None
                and certification is not None
                and str(session.get("status") or "") == "running"
                and str(certification.get("status") or "") == "running"
            ):
                connection = self.repository.get_connection(
                    self.tenant_id,
                    str(certification["connection_id"]),
                )
                self._finalize_certification_failure(
                    connection,
                    certification_id=str(certification["id"]),
                    session_id=session_id,
                    error_code=error_code or "realtime_session_interrupted",
                    uncertain=True,
                    e2e_ms=(duration * 1000 if duration is not None else None),
                )
        return self._end_response(updated)

    @staticmethod
    def _runtime_evidence_error(
        evidence: RealtimeCallEndEvidence | None,
    ) -> str | None:
        """Classify a managed session without retaining any media content."""

        if evidence is None:
            return "provider_realtime_runtime_evidence_missing"
        checks = (
            (
                evidence.data_channel_open,
                "provider_realtime_data_channel_not_open",
            ),
            (
                evidence.outbound_audio_sent,
                "provider_realtime_outbound_audio_not_sent",
            ),
            (
                evidence.local_audio_observed,
                "provider_realtime_microphone_input_not_observed",
            ),
            (
                evidence.input_speech_started,
                "provider_realtime_input_speech_not_observed",
            ),
            (
                evidence.input_speech_stopped,
                "provider_realtime_input_turn_incomplete",
            ),
            (
                evidence.response_created,
                "provider_realtime_response_not_created",
            ),
            (
                evidence.remote_track_observed,
                "provider_realtime_remote_track_not_observed",
            ),
            (
                evidence.output_audio_started,
                "provider_realtime_output_audio_not_observed",
            ),
            (
                evidence.inbound_audio_received,
                "provider_realtime_inbound_audio_not_received",
            ),
            (
                evidence.remote_audio_observed,
                "provider_realtime_remote_audio_not_observed",
            ),
            (
                evidence.response_done,
                "provider_realtime_response_incomplete",
            ),
            (
                evidence.playback_started,
                "provider_realtime_playback_not_started",
            ),
        )
        return next((code for passed, code in checks if not passed), None)

    def _schedule_expiry(
        self,
        session_id: str,
        *,
        certification_id: str | None = None,
    ) -> None:
        if self._closing:
            return

        async def expire() -> None:
            try:
                await asyncio.sleep(self.session_seconds)
                if certification_id is None:
                    await self.end(session_id, reason="expired")
                else:
                    await self.complete_certification(
                        certification_id,
                        ProviderRealtimeCertificationCompleteRequest(
                            media_observed=False,
                            hangup_observed=False,
                        ),
                    )
            except asyncio.CancelledError:
                raise
            except (
                MultimodalServiceError,
                RouterServiceError,
                RouterRepositoryError,
            ):
                return

        task = asyncio.create_task(
            expire(),
            name=f"modelmirror-realtime-expiry-{session_id[-8:]}",
        )
        self._expiry_tasks[session_id] = task

    def _cancel_expiry(self, session_id: str) -> None:
        task = self._expiry_tasks.pop(session_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _resolve_target(self) -> DirectOpenAITarget:
        connections = [
            item
            for item in self.router_service.list_connections(
                scope="realtime"
            )
            if item.kind == "openai"
            and item.enabled
            and item.health != "offline"
        ]
        connections.sort(
            key=lambda item: (0 if item.health == "online" else 1, item.id)
        )
        if not connections:
            raise MultimodalServiceError(
                "realtime_connection_required",
                "请先在模型服务连接中添加并启用 OpenAI 实时语音连接。",
                status_code=503,
            )
        return self._target_from_connection(connections[0])

    def _resolve_existing_target(
        self,
        row: dict[str, object],
    ) -> DirectOpenAITarget:
        connection_id = str(row.get("connection_id") or "")
        connection = self.repository.get_connection(
            self.tenant_id,
            connection_id,
        )
        if connection.kind != "openai" or "realtime" not in connection.scopes:
            raise MultimodalServiceError(
                "realtime_connection_unavailable",
                "实时语音连接配置已变化，请重新发起会话。",
                status_code=409,
            )
        return self._target_from_connection(connection)

    def _target_from_connection(self, connection: Any) -> DirectOpenAITarget:
        base_url = str(connection.base_url).strip().rstrip("/")
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold() != "api.openai.com"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") not in {"", "/v1"}
        ):
            raise MultimodalServiceError(
                "invalid_realtime_connection",
                "实时语音仅允许使用官方 OpenAI API 地址，请检查连接设置。",
                status_code=422,
            )
        try:
            api_key = self.repository.resolve_api_key(
                self.tenant_id,
                connection.id,
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "realtime_credentials_unavailable",
                "无法读取 OpenAI 密钥，请重新保存模型服务连接。",
                status_code=503,
            ) from exc
        return DirectOpenAITarget(
            base_url=base_url,
            api_key=api_key,
            connection_id=connection.id,
            safety_identifier=self._safety_identifier(),
        )

    def _validate_request(
        self,
        payload: RealtimeCallRequest,
    ) -> ValidatedRealtimeRequest:
        sdp = payload.sdp if isinstance(payload.sdp, str) else ""
        if (
            not sdp
            or len(sdp) > MAX_REALTIME_SDP_CHARS
            or not sdp.lstrip().startswith("v=0")
            or "\x00" in sdp
        ):
            raise MultimodalServiceError(
                "invalid_realtime_sdp",
                "浏览器连接信息无效，请刷新页面后重新发起实时语音。",
                status_code=422,
            )
        model_id = self._choice(
            payload.model_id,
            choices=REALTIME_MODELS,
            code="unsupported_realtime_model",
            message="请选择已验证的实时语音模型。",
        )
        voice = self._choice(
            payload.voice,
            choices=REALTIME_VOICES,
            code="unsupported_realtime_voice",
            message="请选择当前实时语音模型支持的声音。",
        )
        vad_mode = self._choice(
            payload.vad_mode,
            choices=REALTIME_VAD_MODES,
            code="unsupported_realtime_vad",
            message="当前仅支持语义语音活动检测。",
        )
        language = (
            payload.language.strip()
            if isinstance(payload.language, str)
            else ""
        )
        if not _LANGUAGE_PATTERN.fullmatch(language):
            raise MultimodalServiceError(
                "invalid_realtime_language",
                "语言代码无效，请使用 zh-CN、en-US 等格式。",
                status_code=422,
            )
        return ValidatedRealtimeRequest(
            sdp=sdp,
            model_id=model_id,
            voice=voice,
            vad_mode=vad_mode,
            language=language,
        )

    @staticmethod
    def _session_config(
        request: ValidatedRealtimeRequest,
    ) -> dict[str, object]:
        return {
            "type": "realtime",
            "model": request.model_id,
            "output_modalities": ["audio"],
            "instructions": (
                f"Use {request.language} for this voice conversation. "
                "Do not claim to use tools, files, or external knowledge."
            ),
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": request.vad_mode,
                        "eagerness": "auto",
                        "create_response": True,
                        "interrupt_response": True,
                    }
                },
                "output": {"voice": request.voice},
            },
        }

    @staticmethod
    def _choice(
        value: Any,
        *,
        choices: tuple[str, ...],
        code: str,
        message: str,
    ) -> str:
        clean = value.strip() if isinstance(value, str) else ""
        if clean not in choices:
            raise MultimodalServiceError(
                code,
                message,
                status_code=422,
            )
        return clean

    @staticmethod
    def _session_id(value: str) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"local_rt_[a-f0-9]{32}", clean):
            raise MultimodalServiceError(
                "invalid_realtime_session",
                "实时语音会话标识无效。",
                status_code=404,
            )
        return clean

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _end_response(
        self,
        row: dict[str, object],
    ) -> RealtimeCallEndResponse:
        status = str(row.get("status") or "ended")
        if status not in {"ended", "expired", "interrupted"}:
            status = "ended"
        managed = bool(row.get("workload_run_id"))
        receipts = (
            [
                self._managed_receipt(
                    run_id=str(row["workload_run_id"]),
                    call_id=(
                        str(row["workload_call_id"])
                        if row.get("workload_call_id")
                        else None
                    ),
                    model_id=str(row.get("model_id") or ""),
                    fallback_status=(
                        "passed"
                        if status in {"ended", "expired"}
                        and not row.get("error_code")
                        else "uncertain"
                    ),
                    reason_code=(
                        str(row["error_code"])
                        if row.get("error_code")
                        else None
                    ),
                )
            ]
            if managed
            else []
        )
        dispatch_state = str(row.get("provider_dispatch_state") or "") or None
        if dispatch_state not in {
            None,
            "not_dispatched",
            "dispatched",
            "confirmed",
            "uncertain",
        }:
            dispatch_state = None
        return RealtimeCallEndResponse(
            session_id=str(row["id"]),
            status=status,
            ended_at=str(row.get("ended_at") or utc_now()),
            execution_mode="managed" if managed else "legacy",
            provider_route_receipts=receipts,
            provider_dispatch_state=dispatch_state,  # type: ignore[arg-type]
            fallback_reason_codes=(
                [str(row["error_code"])] if row.get("error_code") else []
            ),
        )

    def _safety_identifier(self) -> str:
        digest = hashlib.sha256(
            f"modelmirror:{self.tenant_id}".encode("utf-8")
        ).hexdigest()
        return f"mm_{digest[:32]}"

    @staticmethod
    def _session_hash(session_id: str) -> str:
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_enabled() -> None:
        if (
            os.getenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "false")
            .strip()
            .lower()
            not in {"1", "true", "yes", "on"}
        ):
            raise MultimodalServiceError(
                "realtime_voice_disabled",
                "实时语音当前未启用。",
                status_code=404,
            )
