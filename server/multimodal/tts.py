from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

try:
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.service import ModelRouterService

from .stt import MultimodalServiceError, OpenRouterTarget


logger = logging.getLogger("modelmirror.multimodal")

MAX_SPEECH_INPUT_CHARS = 4_000
MAX_SPEECH_BYTES = 20 * 1024 * 1024
CATALOG_CACHE_SECONDS = 300.0
ALLOWED_SPEECH_PROFILES: dict[str, tuple[str, ...]] = {
    "microsoft/mai-voice-2": ("en-US-Harper:MAI-Voice-2",),
}


@dataclass(frozen=True)
class SpeechResult:
    content: bytes
    requested_model: str
    actual_model: str
    provider: str
    request_id: str
    generation_id: str | None
    output_bytes: int
    cost_usd: float | None = None
    cost_kind: str = "unavailable"


class OpenRouterTtsAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        catalog_cache_seconds: float = CATALOG_CACHE_SECONDS,
    ) -> None:
        self._client_factory = client_factory or self._default_client
        self.catalog_cache_seconds = max(0.0, float(catalog_cache_seconds))
        self._catalog_cache: dict[str, tuple[float, set[str]]] = {}

    async def synthesize(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        text: str,
        voice: str,
        speed: float,
    ) -> tuple[bytes, str | None]:
        await self._verify_speech_model(target, model_id)
        async with self._client_factory() as client:
            try:
                response = await client.post(
                    self._api_url(target.base_url, "audio/speech"),
                    headers=self._headers(target.api_key),
                    json={
                        "model": model_id,
                        "input": text,
                        "voice": voice,
                        "response_format": "mp3",
                        "speed": speed,
                    },
                )
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
            ) as exc:
                raise MultimodalServiceError(
                    "upstream_timeout",
                    "语音生成超时。请缩短文字后重试。",
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                raise MultimodalServiceError(
                    "upstream_unreachable",
                    "暂时无法连接语音生成服务，请检查网络后重试。",
                    status_code=502,
                ) from exc
        self._raise_for_status(response, model_id=model_id)
        self._validate_mp3(response)
        generation_id = str(
            response.headers.get("X-Generation-Id") or ""
        ).strip() or None
        return bytes(response.content), generation_id

    async def _verify_speech_model(
        self,
        target: OpenRouterTarget,
        model_id: str,
    ) -> None:
        now = time.monotonic()
        cached = self._catalog_cache.get(target.cache_key)
        if cached and now - cached[0] <= self.catalog_cache_seconds:
            model_ids = cached[1]
        else:
            async with self._client_factory() as client:
                try:
                    response = await client.get(
                        self._api_url(target.base_url, "models"),
                        headers=self._headers(target.api_key),
                        params={"output_modalities": "speech"},
                    )
                except (
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                ) as exc:
                    raise MultimodalServiceError(
                        "catalog_timeout",
                        "无法确认该模型的语音能力，请稍后重试。",
                        status_code=504,
                    ) from exc
                except httpx.HTTPError as exc:
                    raise MultimodalServiceError(
                        "catalog_unreachable",
                        "无法读取语音生成模型目录，请检查连接后重试。",
                        status_code=502,
                    ) from exc
            self._raise_for_status(response, model_id=model_id)
            try:
                payload = response.json()
            except ValueError as exc:
                raise MultimodalServiceError(
                    "invalid_catalog",
                    "语音生成模型目录格式不兼容，请稍后重试。",
                    status_code=502,
                ) from exc
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise MultimodalServiceError(
                    "invalid_catalog",
                    "语音生成模型目录缺少模型列表，请稍后重试。",
                    status_code=502,
                )
            model_ids: set[str] = set()
            for item in rows:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                raw_outputs = item.get("output_modalities")
                if raw_outputs is None and isinstance(
                    item.get("architecture"), dict
                ):
                    raw_outputs = item["architecture"].get(
                        "output_modalities"
                    )
                if raw_outputs is None or "speech" in {
                    str(value)
                    for value in raw_outputs
                    if isinstance(value, str)
                }:
                    model_ids.add(item_id)
            self._catalog_cache[target.cache_key] = (now, model_ids)
        if model_id not in model_ids:
            raise MultimodalServiceError(
                "operation_mismatch",
                "所选模型不提供文字转语音能力，请选择标有“文字转语音”的模型。",
                status_code=422,
            )

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=15, read=90, write=30, pool=10)
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": False,
        }
        proxy = (
            os.getenv("OPENROUTER_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("ALL_PROXY")
            or None
        )
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.AsyncClient(**kwargs)

    @staticmethod
    def _api_url(base_url: str, path: str) -> str:
        root = str(base_url or "").strip().rstrip("/")
        for suffix in (
            "/chat/completions",
            "/audio/transcriptions",
            "/audio/speech",
            "/models",
        ):
            if root.lower().endswith(suffix):
                root = root[: -len(suffix)].rstrip("/")
                break
        if not root.lower().endswith("/v1"):
            root = f"{root}/v1"
        return f"{root}/{path.lstrip('/')}"

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        title = os.getenv("OPENROUTER_APP_TITLE", "ModelMirror").strip()
        referer = os.getenv(
            "OPENROUTER_HTTP_REFERER", "http://localhost:5173"
        ).strip()
        return {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": referer,
            "X-Title": title,
            "X-OpenRouter-Title": title,
        }

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
        *,
        model_id: str,
    ) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            raise MultimodalServiceError(
                "provider_credentials_invalid",
                "OpenRouter 密钥无效或没有语音生成权限，请在模型服务连接中更新密钥。",
                status_code=502,
            )
        if status == 402:
            raise MultimodalServiceError(
                "provider_quota_exceeded",
                "OpenRouter 余额或预算不足，本次未生成语音。",
                status_code=402,
            )
        if status == 404:
            raise MultimodalServiceError(
                "model_unavailable",
                f"未找到语音模型 {model_id}，请刷新模型目录后重新选择。",
                status_code=422,
            )
        if status == 429:
            raise MultimodalServiceError(
                "provider_rate_limited",
                "语音生成服务请求过多，请稍后重试。",
                status_code=429,
            )
        if status >= 500:
            raise MultimodalServiceError(
                "provider_unavailable",
                "语音生成服务暂时不可用，请稍后重试。",
                status_code=502,
            )
        raise MultimodalServiceError(
            "provider_rejected_request",
            "语音生成请求未被接受，请检查模型、声线和文字长度。",
            status_code=422,
        )

    @staticmethod
    def _validate_mp3(response: httpx.Response) -> None:
        content = bytes(response.content)
        content_type = str(response.headers.get("content-type") or "")
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime not in {"audio/mpeg", "audio/mp3"}:
            raise MultimodalServiceError(
                "invalid_audio_mime",
                "语音服务没有返回标准 MP3 音频，请稍后重试。",
                status_code=502,
            )
        if not content:
            raise MultimodalServiceError(
                "empty_speech",
                "语音服务没有返回音频，请稍后重试。",
                status_code=502,
            )
        if len(content) > MAX_SPEECH_BYTES:
            raise MultimodalServiceError(
                "speech_too_large",
                "生成的语音超过安全大小限制，请缩短文字后重试。",
                status_code=502,
            )
        is_mp3 = content.startswith(b"ID3") or (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xE0 == 0xE0
        )
        if not is_mp3:
            raise MultimodalServiceError(
                "invalid_speech_audio",
                "语音服务返回的 MP3 不完整或已损坏，请重新生成。",
                status_code=502,
            )


class SpeechService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        adapter: OpenRouterTtsAdapter | None = None,
    ) -> None:
        self.router_service = router_service
        self.adapter = adapter or OpenRouterTtsAdapter()

    async def synthesize(
        self,
        *,
        model_id: str,
        text: str,
        voice: str,
        response_format: str,
        speed: float,
    ) -> SpeechResult:
        clean_model = self._model_id(model_id)
        clean_text = self._text(text)
        clean_voice = self._voice(clean_model, voice)
        clean_format = self._response_format(response_format)
        clean_speed = self._speed(speed)
        target = self._target()
        decision_id = self._record_start(
            target,
            model_id=clean_model,
            input_bytes=len(clean_text.encode("utf-8")),
        )
        try:
            content, generation_id = await self.adapter.synthesize(
                target,
                model_id=clean_model,
                text=clean_text,
                voice=clean_voice,
                speed=clean_speed,
            )
        except MultimodalServiceError as exc:
            self._record_failure(decision_id, exc.code)
            raise
        self._record_success(decision_id, output_bytes=len(content))
        return SpeechResult(
            content=content,
            requested_model=clean_model,
            actual_model=clean_model,
            provider="openrouter",
            request_id=decision_id,
            generation_id=generation_id,
            output_bytes=len(content),
        )

    def _target(self) -> OpenRouterTarget:
        connections = [
            item
            for item in self.router_service.list_connections()
            if item.kind == "openrouter"
            and item.enabled
            and item.health != "offline"
        ]
        connections.sort(
            key=lambda item: (
                0 if item.health == "online" else 1,
                item.id,
            )
        )
        if connections:
            connection = connections[0]
            try:
                api_key = self.router_service.repository.resolve_api_key(
                    self.router_service.tenant_id,
                    connection.id,
                )
            except Exception as exc:
                raise MultimodalServiceError(
                    "provider_credentials_unavailable",
                    "无法读取 OpenRouter 连接密钥，请重新保存模型服务连接。",
                    status_code=503,
                ) from exc
            return OpenRouterTarget(
                base_url=connection.base_url,
                api_key=api_key,
                connection_id=connection.id,
                cache_key=f"connection:{connection.id}",
            )
        api_key = (
            os.getenv("MULTIMODAL_OPENROUTER_API_KEY", "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        if not api_key:
            raise MultimodalServiceError(
                "openrouter_not_configured",
                "尚未配置 OpenRouter。请先在“模型服务连接”中添加并测试 OpenRouter。",
                status_code=503,
            )
        base_url = os.getenv(
            "MULTIMODAL_OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ).strip()
        return OpenRouterTarget(
            base_url=base_url,
            api_key=api_key,
            connection_id=None,
            cache_key="environment:openrouter",
        )

    def _record_start(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        input_bytes: int,
    ) -> str:
        record = getattr(
            self.router_service.repository,
            "record_routing_decision",
            None,
        )
        if not callable(record):
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立语音生成审计记录，请稍后重试。",
                status_code=503,
            )
        try:
            return str(
                record(
                    self.router_service.tenant_id,
                    session_id_hash=None,
                    engine="openrouter",
                    strategy="explicit",
                    operation="synthesize_speech",
                    connection_id=target.connection_id,
                    model_id=model_id,
                    reason_codes=[
                        "explicit_model",
                        "operation_synthesize_speech",
                    ],
                    input_bytes=input_bytes,
                )
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立语音生成审计记录，请稍后重试。",
                status_code=503,
            ) from exc

    def _record_failure(self, decision_id: str, outcome: str) -> None:
        update = getattr(
            self.router_service.repository,
            "update_routing_decision_outcome",
            None,
        )
        if callable(update):
            try:
                update(self.router_service.tenant_id, decision_id, outcome)
            except Exception:
                logger.warning(
                    "Unable to update speech audit outcome: %s",
                    decision_id,
                )

    def _record_success(self, decision_id: str, *, output_bytes: int) -> None:
        update = getattr(
            self.router_service.repository,
            "update_routing_decision_usage",
            None,
        )
        if callable(update):
            try:
                update(
                    self.router_service.tenant_id,
                    decision_id,
                    outcome="success",
                    media_seconds=None,
                    settled_cost_usd=None,
                    cost_status="unavailable",
                    output_bytes=output_bytes,
                )
            except Exception:
                logger.warning(
                    "Unable to update speech audit usage: %s",
                    decision_id,
                )
            return
        self._record_failure(decision_id, "success")

    @staticmethod
    def _model_id(value: str) -> str:
        model_id = str(value or "").strip()
        if model_id not in ALLOWED_SPEECH_PROFILES:
            raise MultimodalServiceError(
                "unsupported_speech_model",
                "该语音模型尚未完成行为验证，请选择 Microsoft MAI-Voice-2。",
                status_code=422,
            )
        return model_id

    @staticmethod
    def _text(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise MultimodalServiceError(
                "empty_speech_input",
                "请输入需要生成语音的文字。",
                status_code=422,
            )
        if len(text) > MAX_SPEECH_INPUT_CHARS:
            raise MultimodalServiceError(
                "speech_input_too_long",
                "文字不能超过 4,000 个字符，请缩短后重试。",
                status_code=422,
            )
        return text

    @staticmethod
    def _voice(model_id: str, value: str) -> str:
        voice = str(value or "").strip()
        if voice not in ALLOWED_SPEECH_PROFILES[model_id]:
            raise MultimodalServiceError(
                "unsupported_voice",
                "该声线尚未完成行为验证，请使用当前页面提供的声线。",
                status_code=422,
            )
        return voice

    @staticmethod
    def _response_format(value: str) -> str:
        response_format = str(value or "").strip().lower()
        if response_format != "mp3":
            raise MultimodalServiceError(
                "unsupported_speech_format",
                "首期语音生成只支持 MP3。",
                status_code=422,
            )
        return response_format

    @staticmethod
    def _speed(value: float) -> float:
        if isinstance(value, bool):
            speed = 0.0
        else:
            try:
                speed = float(value)
            except (TypeError, ValueError):
                speed = 0.0
        if speed < 0.5 or speed > 2.0:
            raise MultimodalServiceError(
                "invalid_speech_speed",
                "语速应在 0.5 到 2.0 之间。",
                status_code=422,
            )
        return speed
