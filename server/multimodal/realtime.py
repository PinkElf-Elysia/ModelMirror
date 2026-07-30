from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

try:
    from server.model_router.repository import (
        RouterConnectionNotFound,
        utc_now,
    )
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.repository import RouterConnectionNotFound, utc_now
    from model_router.service import ModelRouterService

from .stt import MultimodalServiceError


MAX_REALTIME_SDP_CHARS = 256_000
MAX_REALTIME_ANSWER_CHARS = 512_000
MAX_REALTIME_SESSION_SECONDS = 600
REALTIME_MODELS = (
    "gpt-realtime-2.1-mini",
    "gpt-realtime-2.1",
)
REALTIME_VOICES = ("marin", "cedar")
REALTIME_VAD_MODES = ("semantic_vad",)
_CALL_ID_PATTERN = re.compile(r"^rtc_[A-Za-z0-9_-]{3,128}$")
_LANGUAGE_PATTERN = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"
)


class RealtimeCallRequest(BaseModel):
    """Untrusted request envelope; the service returns only sanitized errors."""

    sdp: Any = None
    model_id: Any = "gpt-realtime-2.1-mini"
    voice: Any = "marin"
    vad_mode: Any = "semantic_vad"
    language: Any = "zh-CN"


class RealtimeCallResponse(BaseModel):
    session_id: str
    sdp_answer: str
    expires_at: str
    model_id: str
    voice: str


class RealtimeCallEndResponse(BaseModel):
    session_id: str
    status: Literal["ended", "expired", "interrupted"]
    ended_at: str


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
    ) -> None:
        self.client_factory = client_factory or self._default_client

    async def create_call(
        self,
        target: DirectOpenAITarget,
        *,
        sdp: str,
        session: dict[str, object],
    ) -> RealtimeUpstreamCall:
        files = {
            "sdp": ("offer.sdp", sdp, "application/sdp"),
            "session": (
                "session.json",
                json.dumps(
                    session,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "application/json",
            ),
        }
        try:
            async with self.client_factory() as client:
                response = await client.post(
                    self._calls_url(target.base_url),
                    headers=self._headers(target),
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise MultimodalServiceError(
                "realtime_timeout",
                "实时语音连接超时，请检查网络后重试。",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise MultimodalServiceError(
                "realtime_unreachable",
                "无法连接实时语音服务，请检查模型服务连接。",
                status_code=502,
            ) from exc

        if response.status_code != 201:
            self._raise_for_status(response)
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("application/sdp"):
            raise MultimodalServiceError(
                "invalid_realtime_answer",
                "实时语音服务返回了无效的连接信息，请稍后重试。",
                status_code=502,
            )
        answer = response.text.strip()
        if (
            not answer.startswith("v=0")
            or len(answer) > MAX_REALTIME_ANSWER_CHARS
        ):
            raise MultimodalServiceError(
                "invalid_realtime_answer",
                "实时语音服务返回了无效的连接信息，请稍后重试。",
                status_code=502,
            )
        call_id = self._call_id(response.headers.get("location", ""))
        return RealtimeUpstreamCall(call_id=call_id, sdp_answer=answer)

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
                response = await client.post(
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
        except httpx.HTTPError as exc:
            raise MultimodalServiceError(
                "realtime_hangup_unreachable",
                "暂时无法通知实时语音服务结束连接，请稍后重试。",
                status_code=502,
            ) from exc
        if response.status_code in {200, 204, 404, 410}:
            return
        self._raise_for_status(response)

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
            raise MultimodalServiceError(
                "invalid_realtime_answer",
                "实时语音服务未返回有效会话标识，请稍后重试。",
                status_code=502,
            )
        return call_id

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
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
    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=30.0,
                write=30.0,
                pool=10.0,
            ),
            follow_redirects=False,
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
        self.adapter = adapter or OpenAIRealtimeAdapter()
        self.session_seconds = max(
            1,
            min(int(session_seconds), MAX_REALTIME_SESSION_SECONDS),
        )
        self._expiry_tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    async def create(
        self,
        payload: RealtimeCallRequest,
    ) -> RealtimeCallResponse:
        self._require_enabled()
        request = self._validate_request(payload)
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
        )

    async def end(
        self,
        session_id: str,
        *,
        reason: Literal["ended", "expired", "interrupted"] = "ended",
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
        upstream_call_id = str(row.get("upstream_call_id") or "").strip()
        if upstream_call_id:
            target = self._resolve_existing_target(row)
            await self.adapter.hangup(target, upstream_call_id)
        return self._finish(row, status=reason)

    async def recover_active(self) -> None:
        for row in self.repository.list_active_realtime_calls(self.tenant_id):
            if not row.get("upstream_call_id"):
                self._finish(
                    row,
                    status="interrupted",
                    error_code="realtime_restart_interrupted",
                )
                continue
            try:
                target = self._resolve_existing_target(row)
                await self.adapter.hangup(
                    target,
                    str(row["upstream_call_id"]),
                )
                self._finish(
                    row,
                    status="interrupted",
                    error_code="realtime_restart_interrupted",
                )
            except (MultimodalServiceError, RouterConnectionNotFound):
                self._finish(
                    row,
                    status="interrupted",
                    error_code="realtime_recovery_failed",
                )

    async def shutdown(self) -> None:
        self._closing = True
        rows = self.repository.list_active_realtime_calls(self.tenant_id)
        for row in rows:
            try:
                if row.get("upstream_call_id"):
                    target = self._resolve_existing_target(row)
                    await self.adapter.hangup(
                        target,
                        str(row["upstream_call_id"]),
                    )
                self._finish(
                    row,
                    status="interrupted",
                    error_code="realtime_server_shutdown",
                )
            except (MultimodalServiceError, RouterConnectionNotFound):
                self._finish(
                    row,
                    status="interrupted",
                    error_code="realtime_shutdown_hangup_failed",
                )
        tasks = list(self._expiry_tasks.values())
        self._expiry_tasks.clear()
        for task in tasks:
            if task is not asyncio.current_task():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _finish(
        self,
        row: dict[str, object],
        *,
        status: Literal["ended", "expired", "interrupted"],
        error_code: str | None = None,
    ) -> RealtimeCallEndResponse:
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
        return self._end_response(updated)

    def _schedule_expiry(self, session_id: str) -> None:
        if self._closing:
            return

        async def expire() -> None:
            try:
                await asyncio.sleep(self.session_seconds)
                await self.end(session_id, reason="expired")
            except asyncio.CancelledError:
                raise
            except MultimodalServiceError:
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

    @staticmethod
    def _end_response(row: dict[str, object]) -> RealtimeCallEndResponse:
        status = str(row.get("status") or "ended")
        if status not in {"ended", "expired", "interrupted"}:
            status = "ended"
        return RealtimeCallEndResponse(
            session_id=str(row["id"]),
            status=status,
            ended_at=str(row.get("ended_at") or utc_now()),
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
