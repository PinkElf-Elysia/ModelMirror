from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

try:
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.service import ModelRouterService

from .stt import ALLOWED_AUDIO_FORMATS, MultimodalServiceError, OpenRouterTarget
from .tts import ALLOWED_SPEECH_PROFILES


AUDIO_CATALOG_TTL_SECONDS = 300.0
AUDIO_CATALOG_STALE_SECONDS = 1_800.0

VERIFIED_NATIVE_AUDIO_MODELS = {
    "openai/gpt-audio",
    "openai/gpt-audio-mini",
}
NATIVE_AUDIO_VOICES = (
    "alloy",
    "echo",
    "fable",
    "onyx",
    "nova",
    "shimmer",
)
DIRECT_AUDIO_INPUT_FORMATS = (
    "wav",
    "mp3",
    "aac",
    "m4a",
    "flac",
    "ogg",
)

AudioChatMode = Literal[
    "direct_audio_input",
    "native_streaming_audio_output",
    "transcribe",
    "synthesize_speech",
]


class AudioChatProfile(BaseModel):
    model_id: str
    display_name: str
    invocable: bool
    interaction_status: Literal["ready", "planned", "disabled"]
    chat_modes: list[AudioChatMode] = Field(default_factory=list)
    input_formats: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=list)
    voices: list[str] = Field(default_factory=list)


class AudioModelCatalogResponse(BaseModel):
    source: Literal["openrouter"]
    status: Literal["online", "stale", "offline", "disabled"]
    stale: bool
    synced_at: str | None
    profiles: list[AudioChatProfile] = Field(default_factory=list)


class _CachedAudioCatalog:
    def __init__(
        self,
        profiles: list[AudioChatProfile],
        synced_at: str,
        stored_at: float,
    ) -> None:
        self.profiles = profiles
        self.synced_at = synced_at
        self.stored_at = stored_at


class AudioCatalogService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.router_service = router_service
        self.client_factory = client_factory or self._default_client
        self._cache: _CachedAudioCatalog | None = None
        self._lock = asyncio.Lock()

    async def get_catalog(
        self,
        *,
        force: bool = False,
    ) -> AudioModelCatalogResponse:
        chat_enabled = self._enabled("MULTIMODAL_CHAT_AUDIO_ENABLED")
        streaming_enabled = self._enabled(
            "MULTIMODAL_STREAMING_AUDIO_ENABLED"
        )
        if not chat_enabled and not streaming_enabled:
            return AudioModelCatalogResponse(
                source="openrouter",
                status="disabled",
                stale=False,
                synced_at=None,
                profiles=[],
            )

        now = time.monotonic()
        cached = self._cache
        if (
            not force
            and cached is not None
            and now - cached.stored_at <= AUDIO_CATALOG_TTL_SECONDS
        ):
            return self._response(cached, stale=False)

        async with self._lock:
            now = time.monotonic()
            cached = self._cache
            if (
                not force
                and cached is not None
                and now - cached.stored_at <= AUDIO_CATALOG_TTL_SECONDS
            ):
                return self._response(cached, stale=False)
            try:
                profiles = await self._fetch(
                    self.resolve_target(),
                    chat_enabled=chat_enabled,
                    streaming_enabled=streaming_enabled,
                )
            except (MultimodalServiceError, httpx.HTTPError, ValueError):
                if (
                    cached is not None
                    and now - cached.stored_at
                    <= AUDIO_CATALOG_STALE_SECONDS
                ):
                    return self._response(cached, stale=True)
                return AudioModelCatalogResponse(
                    source="openrouter",
                    status="offline",
                    stale=False,
                    synced_at=None,
                    profiles=[],
                )

            synced_at = datetime.now(UTC).isoformat()
            self._cache = _CachedAudioCatalog(profiles, synced_at, now)
            return self._response(self._cache, stale=False)

    async def _fetch(
        self,
        target: OpenRouterTarget,
        *,
        chat_enabled: bool,
        streaming_enabled: bool,
    ) -> list[AudioChatProfile]:
        async with self.client_factory() as client:
            response = await client.get(
                self._api_url(target.base_url, "models"),
                headers=self._headers(target.api_key),
                params={"output_modalities": "all", "sort": "newest"},
            )
        self._raise_for_status(response)

        profiles: list[AudioChatProfile] = []
        for item in self._items(response.json()):
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            inputs = self._modalities(item, "input_modalities")
            outputs = self._modalities(item, "output_modalities")
            relevant = bool(
                {"audio"} & inputs
                or {"audio", "speech", "transcription"} & outputs
            )
            if not relevant:
                continue

            modes: list[AudioChatMode] = []
            input_formats: set[str] = set()
            output_formats: set[str] = set()
            voices: set[str] = set()

            if chat_enabled:
                if (
                    model_id in VERIFIED_NATIVE_AUDIO_MODELS
                    and "audio" in inputs
                    and "text" in outputs
                ):
                    modes.append("direct_audio_input")
                    input_formats.update(DIRECT_AUDIO_INPUT_FORMATS)
                if "transcription" in outputs:
                    modes.append("transcribe")
                    input_formats.update(ALLOWED_AUDIO_FORMATS)
                if (
                    model_id in ALLOWED_SPEECH_PROFILES
                    and "speech" in outputs
                ):
                    modes.append("synthesize_speech")
                    output_formats.add("mp3")
                    voices.update(ALLOWED_SPEECH_PROFILES[model_id])

            if (
                streaming_enabled
                and model_id in VERIFIED_NATIVE_AUDIO_MODELS
                and "audio" in outputs
            ):
                modes.append("native_streaming_audio_output")
                output_formats.add("mp3")
                voices.update(NATIVE_AUDIO_VOICES)

            profiles.append(
                AudioChatProfile(
                    model_id=model_id,
                    display_name=str(item.get("name") or model_id).strip(),
                    invocable=True,
                    interaction_status="ready" if modes else "planned",
                    chat_modes=modes,
                    input_formats=sorted(input_formats),
                    output_formats=sorted(output_formats),
                    voices=sorted(voices),
                )
            )
        return sorted(
            profiles,
            key=lambda profile: (
                0 if profile.interaction_status == "ready" else 1,
                profile.display_name.casefold(),
                profile.model_id,
            ),
        )

    def resolve_target(self) -> OpenRouterTarget:
        connections = [
            item
            for item in self.router_service.list_connections()
            if item.kind == "openrouter"
            and item.enabled
            and item.health != "offline"
        ]
        connections.sort(
            key=lambda item: (0 if item.health == "online" else 1, item.id)
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
                "尚未配置 OpenRouter。",
                status_code=503,
            )
        return OpenRouterTarget(
            base_url=os.getenv(
                "MULTIMODAL_OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1",
            ).strip(),
            api_key=api_key,
            connection_id=None,
            cache_key="environment:openrouter",
        )

    @staticmethod
    def _response(
        cached: _CachedAudioCatalog,
        *,
        stale: bool,
    ) -> AudioModelCatalogResponse:
        return AudioModelCatalogResponse(
            source="openrouter",
            status="stale" if stale else "online",
            stale=stale,
            synced_at=cached.synced_at,
            profiles=[item.model_copy(deep=True) for item in cached.profiles],
        )

    @staticmethod
    def _enabled(name: str) -> bool:
        return os.getenv(name, "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

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
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "audio catalog unavailable",
                request=response.request,
                response=response,
            )

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("data"), list
        ):
            raise ValueError("invalid audio catalog")
        return [
            item for item in payload["data"] if isinstance(item, dict)
        ]

    @staticmethod
    def _modalities(item: dict[str, Any], field: str) -> set[str]:
        raw = item.get(field)
        architecture = item.get("architecture")
        if raw is None and isinstance(architecture, dict):
            raw = architecture.get(field)
        if not isinstance(raw, list):
            return set()
        return {
            str(value).strip().lower()
            for value in raw
            if isinstance(value, str) and value.strip()
        }

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=15, read=30, write=15, pool=10)
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
