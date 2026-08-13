from __future__ import annotations

import os
import re
import time
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

try:
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.service import ModelRouterService


MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 200_000
CATALOG_CACHE_SECONDS = 300.0
logger = logging.getLogger("modelmirror.multimodal")

ALLOWED_AUDIO_FORMATS: dict[str, tuple[str, ...]] = {
    "wav": ("audio/wav", "audio/x-wav", "application/octet-stream"),
    "mp3": ("audio/mpeg", "audio/mp3", "application/octet-stream"),
    "flac": ("audio/flac", "audio/x-flac", "application/octet-stream"),
    "m4a": (
        "audio/mp4",
        "audio/x-m4a",
        "video/mp4",
        "application/octet-stream",
    ),
    "ogg": ("audio/ogg", "application/ogg", "application/octet-stream"),
    "webm": ("audio/webm", "video/webm", "application/octet-stream"),
    "aac": ("audio/aac", "audio/x-aac", "application/octet-stream"),
}
TRANSCRIPTION_PROFILE_VERSION = "stt-contracts-2026-08-13-c1"


@dataclass(frozen=True)
class TranscriptionProfile:
    input_formats: tuple[str, ...]
    smoke_languages: tuple[str, ...] = ("zh", "en")


_STANDARD_TRANSCRIPTION_PROFILE = TranscriptionProfile(
    input_formats=tuple(ALLOWED_AUDIO_FORMATS),
)
VERIFIED_TRANSCRIPTION_PROFILES: dict[str, TranscriptionProfile] = {
    model_id: _STANDARD_TRANSCRIPTION_PROFILE
    for model_id in (
        "deepgram/nova-3",
        "fish-audio/transcribe-1",
        "google/chirp-3",
        "microsoft/mai-transcribe-1.5",
        "mistralai/voxtral-mini-transcribe",
        "nvidia/parakeet-tdt-0.6b-v3",
        "openai/gpt-4o-mini-transcribe",
        "openai/gpt-4o-transcribe",
        "openai/gpt-transcribe",
        "openai/whisper-1",
        "openai/whisper-large-v3",
        "openai/whisper-large-v3-turbo",
        "qwen/qwen3-asr-flash-2026-02-10",
        "x-ai/grok-stt-1.0",
    )
}
MANUAL_TRANSCRIPTION_PROFILES: dict[str, TranscriptionProfile] = {
    model_id: _STANDARD_TRANSCRIPTION_PROFILE
    for model_id in (
        "qwen/qwen3-asr-0.6b",
        "qwen/qwen3-asr-1.7b",
    )
}


def verification_model_ids() -> set[str]:
    return {
        value.strip()
        for value in os.getenv(
            "MULTIMODAL_VERIFICATION_MODEL_IDS", ""
        ).split(",")
        if value.strip()
    }


def manual_verification_enabled(model_id: str) -> bool:
    return model_id in verification_model_ids()


class MultimodalServiceError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OpenRouterTarget:
    base_url: str
    api_key: str
    connection_id: str | None
    cache_key: str


@dataclass(frozen=True)
class TranscriptionUsage:
    audio_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_kind: str = "unavailable"


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    requested_model: str
    actual_model: str
    provider: str
    request_id: str
    usage: TranscriptionUsage


class OpenRouterSttAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        catalog_cache_seconds: float = CATALOG_CACHE_SECONDS,
    ) -> None:
        self._client_factory = client_factory or self._default_client
        self.catalog_cache_seconds = max(0.0, float(catalog_cache_seconds))
        self._catalog_cache: dict[str, tuple[float, set[str]]] = {}

    async def transcribe(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        filename: str,
        audio_format: str,
        content: bytes,
        language: str | None,
    ) -> tuple[str, str, TranscriptionUsage]:
        await self._verify_transcription_model(target, model_id)
        headers = self._headers(target.api_key)
        data = {"model": model_id, "response_format": "json"}
        if language:
            data["language"] = language
        async with self._client_factory() as client:
            try:
                response = await client.post(
                    self._api_url(target.base_url, "audio/transcriptions"),
                    headers=headers,
                    data=data,
                    files={
                        "file": (
                            filename,
                            content,
                            ALLOWED_AUDIO_FORMATS[audio_format][0],
                        )
                    },
                )
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                raise MultimodalServiceError(
                    "upstream_timeout",
                    "音频转写超时。请缩短录音、改用压缩格式后重试。",
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                raise MultimodalServiceError(
                    "upstream_unreachable",
                    "暂时无法连接音频转写服务，请检查网络后重试。",
                    status_code=502,
                ) from exc
        self._raise_for_status(response, model_id=model_id)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MultimodalServiceError(
                "invalid_upstream_response",
                "音频转写服务返回了无法识别的结果，请稍后重试。",
                status_code=502,
            ) from exc
        text = (
            str(payload.get("text") or "").strip()
            if isinstance(payload, dict)
            else ""
        )
        if not text:
            raise MultimodalServiceError(
                "empty_transcription",
                "音频中没有识别到可用文字，请检查音量或更换音频后重试。",
                status_code=502,
            )
        usage = self._usage(payload.get("usage") if isinstance(payload, dict) else None)
        actual_model = (
            str(payload.get("model") or "").strip()
            if isinstance(payload, dict)
            else ""
        ) or model_id
        return text[:MAX_TRANSCRIPT_CHARS], actual_model, usage

    async def _verify_transcription_model(
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
                        params={"output_modalities": "transcription"},
                    )
                except (
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                ) as exc:
                    raise MultimodalServiceError(
                        "catalog_timeout",
                        "无法确认该模型的转写能力，请稍后重试。",
                        status_code=504,
                    ) from exc
                except httpx.HTTPError as exc:
                    raise MultimodalServiceError(
                        "catalog_unreachable",
                        "无法读取音频转写模型目录，请检查连接后重试。",
                        status_code=502,
                    ) from exc
            self._raise_for_status(response, model_id=model_id)
            try:
                payload = response.json()
            except ValueError as exc:
                raise MultimodalServiceError(
                    "invalid_catalog",
                    "音频转写模型目录格式不兼容，请稍后重试。",
                    status_code=502,
                ) from exc
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise MultimodalServiceError(
                    "invalid_catalog",
                    "音频转写模型目录缺少模型列表，请稍后重试。",
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
                if raw_outputs is None or "transcription" in {
                    str(value)
                    for value in raw_outputs
                    if isinstance(value, str)
                }:
                    model_ids.add(item_id)
            self._catalog_cache[target.cache_key] = (now, model_ids)
        if model_id not in model_ids:
            raise MultimodalServiceError(
                "operation_mismatch",
                "所选模型不提供音频转文字能力，请选择标有“音频转文字”的模型。",
                status_code=422,
            )

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=15, read=60, write=60, pool=10)
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
    def _usage(raw: object) -> TranscriptionUsage:
        usage = raw if isinstance(raw, dict) else {}

        def number(name: str) -> float | None:
            value = usage.get(name)
            return (
                max(0.0, float(value))
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None
            )

        def integer(name: str) -> int | None:
            value = number(name)
            return int(value) if value is not None else None

        cost = number("cost")
        return TranscriptionUsage(
            audio_seconds=number("seconds"),
            input_tokens=integer("input_tokens"),
            output_tokens=integer("output_tokens"),
            total_tokens=integer("total_tokens"),
            cost_usd=cost,
            cost_kind="actual" if cost is not None else "unavailable",
        )

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
                "OpenRouter 密钥无效或没有音频转写权限，请在模型服务连接中更新密钥。",
                status_code=502,
            )
        if status == 402:
            raise MultimodalServiceError(
                "provider_quota_exceeded",
                "OpenRouter 余额或预算不足，本次未进行转写。",
                status_code=402,
            )
        if status == 404:
            raise MultimodalServiceError(
                "model_unavailable",
                f"未找到转写模型 {model_id}，请刷新模型目录后重新选择。",
                status_code=422,
            )
        if status == 429:
            raise MultimodalServiceError(
                "provider_rate_limited",
                "音频转写服务请求过多，请稍后重试。",
                status_code=429,
            )
        if status >= 500:
            raise MultimodalServiceError(
                "provider_unavailable",
                "音频转写服务暂时不可用，请稍后重试。",
                status_code=502,
            )
        raise MultimodalServiceError(
            "provider_rejected_request",
            "音频转写请求未被接受，请检查模型和文件格式。",
            status_code=422,
        )


class TranscriptionService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        adapter: OpenRouterSttAdapter | None = None,
    ) -> None:
        self.router_service = router_service
        self.adapter = adapter or OpenRouterSttAdapter()

    async def transcribe(
        self,
        *,
        model_id: str,
        filename: str,
        content_type: str | None,
        content: bytes,
        language: str,
    ) -> TranscriptionResult:
        clean_model = self._model_id(model_id)
        clean_language = self._language(language)
        profile = (
            VERIFIED_TRANSCRIPTION_PROFILES.get(clean_model)
            or MANUAL_TRANSCRIPTION_PROFILES[clean_model]
        )
        clean_filename, audio_format = self._validate_audio(
            filename,
            content_type,
            content,
        )
        if audio_format not in profile.input_formats:
            raise MultimodalServiceError(
                "unsupported_model_audio_format",
                "所选转写模型尚未验证该音频格式，请更换模型或重新导出音频。",
                status_code=415,
            )
        target = self._target()
        decision_id = self._record_start(
            target,
            model_id=clean_model,
            input_bytes=len(content),
        )
        try:
            text, actual_model, usage = await self.adapter.transcribe(
                target,
                model_id=clean_model,
                filename=clean_filename,
                audio_format=audio_format,
                content=content,
                language=clean_language,
            )
        except MultimodalServiceError as exc:
            self._record_failure(decision_id, exc.code)
            raise
        self._record_success(decision_id, usage)
        return TranscriptionResult(
            text=text,
            requested_model=clean_model,
            actual_model=actual_model,
            provider="openrouter",
            request_id=decision_id,
            usage=usage,
        )

    def _target(self) -> OpenRouterTarget:
        connections = [
            item
            for item in self.router_service.list_connections()
            if item.kind == "openrouter"
            and item.enabled
            and item.health != "offline"
            and "audio" in item.scopes
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
                "暂时无法建立音频转写审计记录，请稍后重试。",
                status_code=503,
            )
        try:
            return str(
                record(
                    self.router_service.tenant_id,
                    session_id_hash=None,
                    engine="openrouter",
                    strategy="explicit",
                    operation="transcribe",
                    connection_id=target.connection_id,
                    model_id=model_id,
                    reason_codes=["explicit_model", "operation_transcribe"],
                    input_bytes=input_bytes,
                )
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立音频转写审计记录，请稍后重试。",
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
                    "Unable to update transcription audit outcome: %s",
                    decision_id,
                )

    def _record_success(
        self,
        decision_id: str,
        usage: TranscriptionUsage,
    ) -> None:
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
                    media_seconds=usage.audio_seconds,
                    settled_cost_usd=usage.cost_usd,
                    cost_status=usage.cost_kind,
                )
            except Exception:
                logger.warning(
                    "Unable to update transcription audit usage: %s",
                    decision_id,
                )
            return
        self._record_failure(decision_id, "success")

    @staticmethod
    def _model_id(value: str) -> str:
        model_id = str(value or "").strip()
        if (
            not model_id
            or len(model_id) > 256
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model_id)
            or model_id == "auto"
            or model_id.startswith("auto/")
        ):
            raise MultimodalServiceError(
                "invalid_model_id",
                "请选择一个具体的音频转文字模型。",
                status_code=422,
            )
        if model_id in MANUAL_TRANSCRIPTION_PROFILES:
            if not manual_verification_enabled(model_id):
                raise MultimodalServiceError(
                    "transcription_verification_required",
                    "该转写模型仅在本地人工验收名单中开放，请先确认预计费用并配置验收模型。",
                    status_code=422,
                )
            return model_id
        if model_id not in VERIFIED_TRANSCRIPTION_PROFILES:
            raise MultimodalServiceError(
                "unsupported_transcription_model",
                "该转写模型尚未完成本地契约验证，请从转写设置的可用列表中选择。",
                status_code=422,
            )
        return model_id

    @staticmethod
    def _language(value: str) -> str | None:
        language = str(value or "auto").strip().lower()
        if language in {"", "auto"}:
            return None
        if not re.fullmatch(r"[a-z]{2}", language):
            raise MultimodalServiceError(
                "invalid_language",
                "语言代码应为 auto 或两位 ISO 代码，例如 zh、en、ja。",
                status_code=422,
            )
        return language

    @staticmethod
    def _validate_audio(
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> tuple[str, str]:
        clean_filename = Path(filename or "audio").name
        extension = Path(clean_filename).suffix.lower().removeprefix(".")
        if extension not in ALLOWED_AUDIO_FORMATS:
            raise MultimodalServiceError(
                "unsupported_audio_format",
                "仅支持 WAV、MP3、FLAC、M4A、OGG、WebM 和 AAC 音频。",
                status_code=415,
            )
        if not content:
            raise MultimodalServiceError(
                "empty_audio",
                "音频文件为空，请重新选择文件。",
                status_code=422,
            )
        if len(content) > MAX_AUDIO_BYTES:
            raise MultimodalServiceError(
                "audio_too_large",
                "音频文件不能超过 25 MiB。请压缩或拆分后重试。",
                status_code=413,
            )
        mime = str(content_type or "application/octet-stream").split(";", 1)[0]
        if mime not in ALLOWED_AUDIO_FORMATS[extension]:
            raise MultimodalServiceError(
                "audio_type_mismatch",
                "文件类型与扩展名不一致，请重新导出音频后上传。",
                status_code=415,
            )
        if not _signature_matches(extension, content):
            raise MultimodalServiceError(
                "invalid_audio_file",
                "无法识别该音频文件，请确认文件未损坏且扩展名正确。",
                status_code=422,
            )
        return clean_filename, extension


def _signature_matches(audio_format: str, content: bytes) -> bool:
    if audio_format == "wav":
        return (
            len(content) >= 12
            and content[:4] in {b"RIFF", b"RF64"}
            and content[8:12] == b"WAVE"
        )
    if audio_format == "flac":
        return content.startswith(b"fLaC")
    if audio_format == "ogg":
        return content.startswith(b"OggS")
    if audio_format == "webm":
        return content.startswith(b"\x1a\x45\xdf\xa3")
    if audio_format == "m4a":
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if audio_format == "mp3":
        return content.startswith(b"ID3") or (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xE0 == 0xE0
        )
    if audio_format == "aac":
        return (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xF6 in {0xF0, 0xF4}
        )
    return False
