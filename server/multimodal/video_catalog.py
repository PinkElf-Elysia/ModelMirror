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

from .stt import (
    MultimodalServiceError,
    OpenRouterTarget,
    manual_verification_enabled,
)
from .readiness import OperationReadiness


VIDEO_CATALOG_TTL_SECONDS = 300.0
VIDEO_CATALOG_STALE_SECONDS = 1_800.0

VERIFIED_VIDEO_GENERATION_MODELS = frozenset(
    {
        # 2026-07 人工验收：文生视频、轮询、播放与下载闭环通过。
        "bytedance/seedance-2.0",
        "runway/aleph-2",
        "runway/gen-4.5",
        # 2026-08-06 人工验收：最低规格文生视频可完整播放与下载。
        "x-ai/grok-imagine-video",
        "x-ai/grok-imagine-video-1.5",
        "alibaba/happyhorse-1.0",
        "alibaba/happyhorse-1.1",
        "alibaba/wan-2.6",
        "alibaba/wan-2.7",
        "minimax/hailuo-2.3",
        "minimax/hailuo-3",
        # 2026-08-06 人工验收：首尾帧、参考图及受控音频/种子参数通过。
        "kwaivgi/kling-v3.0-pro",
        "kwaivgi/kling-v3.0-std",
        "kwaivgi/kling-video-o1",
        "bytedance/seedance-1-5-pro",
        "bytedance/seedance-2.0-fast",
        # 2026-08-06 人工验收：最低规格生成、轮询、播放与下载闭环通过。
        # Veo Lite 的受控高级参数也已完成验收。
        "google/veo-3.1-lite",
        "google/veo-3.1-fast",
        "google/veo-3.1",
        "black-forest-labs/flux-3-video",
        "openai/sora-2-pro",
    }
)

HIGH_COST_VIDEO_VERIFICATION_MODELS = frozenset(
    {
        "google/veo-3.1-lite",
        "google/veo-3.1-fast",
        "google/veo-3.1",
        "black-forest-labs/flux-3-video",
        "openai/sora-2-pro",
    }
)


class VideoProviderOption(BaseModel):
    key: str
    label: str
    type: Literal["text", "number", "boolean", "select"]
    options: list[str] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    default: str | int | float | bool | None = None


class VideoModelProfile(BaseModel):
    model_id: str
    operation: Literal["analyze_video", "generate_video"]
    supported_input_sources: list[Literal["file", "url"]] = Field(
        default_factory=list
    )
    supported_resolutions: list[str] = Field(default_factory=list)
    supported_aspect_ratios: list[str] = Field(default_factory=list)
    supported_durations: list[int] = Field(default_factory=list)
    supported_frame_types: list[
        Literal["first_frame", "last_frame"]
    ] = Field(default_factory=list)
    supports_first_frame: bool = False
    supports_reference_images: bool = False
    max_reference_images: int | None = None
    supports_generated_audio: bool = False
    supports_seed: bool = False
    provider_options: list[VideoProviderOption] = Field(
        default_factory=list
    )
    pricing_skus: dict[str, str] = Field(default_factory=dict)
    interaction_status: Literal["ready", "planned", "unsupported"] = "planned"
    status_reason: str | None = None
    verification_entry_enabled: bool = False
    verification_requires_cost_estimate: bool = False
    operation_readiness: list[OperationReadiness] = Field(
        default_factory=list
    )


REFERENCE_IMAGE_AUDIT: dict[str, int] = {
    "bytedance/seedance-2.0-fast": 3,
}

PROVIDER_OPTION_AUDIT: dict[
    str, tuple[str, tuple[VideoProviderOption, ...]]
] = {
    "google/veo-3.1-lite": (
        "google-vertex",
        (
            VideoProviderOption(
                key="negativePrompt",
                label="排除内容",
                type="text",
                default="",
            ),
            VideoProviderOption(
                key="enhancePrompt",
                label="自动增强提示词",
                type="boolean",
                default=True,
            ),
        ),
    ),
}


class VideoModelCatalogResponse(BaseModel):
    source: Literal["openrouter"]
    status: Literal["online", "stale", "offline", "disabled"]
    stale: bool
    synced_at: str | None
    profiles: list[VideoModelProfile] = Field(default_factory=list)


class _CachedVideoCatalog:
    def __init__(
        self,
        profiles: list[VideoModelProfile],
        synced_at: str,
        stored_at: float,
    ) -> None:
        self.profiles = profiles
        self.synced_at = synced_at
        self.stored_at = stored_at


class VideoCatalogService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.router_service = router_service
        self.client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=False,
            )
        )
        self._cache: _CachedVideoCatalog | None = None
        self._lock = asyncio.Lock()

    async def get_catalog(self, *, force: bool = False) -> VideoModelCatalogResponse:
        analysis_enabled = self._enabled("MULTIMODAL_VIDEO_ANALYSIS_ENABLED")
        generation_enabled = self._enabled(
            "MULTIMODAL_VIDEO_GENERATION_ENABLED"
        )
        if not analysis_enabled and not generation_enabled:
            return VideoModelCatalogResponse(
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
            and now - cached.stored_at <= VIDEO_CATALOG_TTL_SECONDS
        ):
            return self._response(cached, stale=False)

        async with self._lock:
            now = time.monotonic()
            cached = self._cache
            if (
                not force
                and cached is not None
                and now - cached.stored_at <= VIDEO_CATALOG_TTL_SECONDS
            ):
                return self._response(cached, stale=False)
            try:
                target = self.resolve_target()
                profiles = await self._fetch(
                    target,
                    analysis_enabled=analysis_enabled,
                    generation_enabled=generation_enabled,
                )
            except (MultimodalServiceError, httpx.HTTPError, ValueError):
                if (
                    cached is not None
                    and now - cached.stored_at
                    <= VIDEO_CATALOG_STALE_SECONDS
                ):
                    return self._response(cached, stale=True)
                return VideoModelCatalogResponse(
                    source="openrouter",
                    status="offline",
                    stale=False,
                    synced_at=None,
                    profiles=[],
                )

            synced_at = datetime.now(UTC).isoformat()
            self._cache = _CachedVideoCatalog(profiles, synced_at, now)
            return self._response(self._cache, stale=False)

    async def _fetch(
        self,
        target: OpenRouterTarget,
        *,
        analysis_enabled: bool,
        generation_enabled: bool,
    ) -> list[VideoModelProfile]:
        headers = {"Authorization": f"Bearer {target.api_key}"}
        async with self.client_factory() as client:
            requests = []
            if analysis_enabled:
                requests.append(
                    client.get(
                        self._api_url(target.base_url, "models"),
                        headers=headers,
                        params={"input_modalities": "video"},
                    )
                )
            if generation_enabled:
                requests.append(
                    client.get(
                        self._api_url(target.base_url, "videos/models"),
                        headers=headers,
                    )
                )
            responses = await asyncio.gather(*requests)

        profiles: list[VideoModelProfile] = []
        response_index = 0
        if analysis_enabled:
            response = responses[response_index]
            response_index += 1
            self._raise_for_status(response)
            for item in self._items(response.json()):
                inputs = self._strings(
                    item.get("input_modalities")
                    or (item.get("architecture") or {}).get(
                        "input_modalities"
                    )
                )
                outputs = self._strings(
                    item.get("output_modalities")
                    or (item.get("architecture") or {}).get(
                        "output_modalities"
                    )
                )
                if "video" in inputs and "text" in outputs:
                    profiles.append(
                        VideoModelProfile(
                            model_id=str(item.get("id") or ""),
                            operation="analyze_video",
                            supported_input_sources=["file", "url"],
                            interaction_status="ready",
                            operation_readiness=[
                                OperationReadiness(
                                    operation="analyze_video",
                                    interaction_status="ready",
                                    availability_status="available",
                                    verification_status="verified",
                                )
                            ],
                        )
                    )
        if generation_enabled:
            response = responses[response_index]
            self._raise_for_status(response)
            for item in self._items(response.json()):
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                frame_types = self._frame_types(item)
                reference_limit = REFERENCE_IMAGE_AUDIT.get(model_id)
                verified = model_id in VERIFIED_VIDEO_GENERATION_MODELS
                status_reason = (
                    None
                    if verified
                    else "实时参数契约已确认，等待最低规格人工生成验收。"
                )
                profiles.append(
                    VideoModelProfile(
                        model_id=model_id,
                        operation="generate_video",
                        supported_resolutions=self._strings(
                            item.get("supported_resolutions")
                        ),
                        supported_aspect_ratios=self._strings(
                            item.get("supported_aspect_ratios")
                        ),
                        supported_durations=self._integers(
                            item.get("supported_durations")
                        ),
                        supported_frame_types=frame_types,
                        supports_first_frame=(
                            "first_frame" in frame_types
                        ),
                        supports_reference_images=(
                            reference_limit is not None
                        ),
                        max_reference_images=reference_limit,
                        supports_generated_audio=self._supports_audio(item),
                        supports_seed=self._supports_seed(item),
                        provider_options=self._provider_options(
                            model_id, item
                        ),
                        pricing_skus=self._pricing(item.get("pricing_skus")),
                        interaction_status=(
                            "ready" if verified else "planned"
                        ),
                        status_reason=status_reason,
                        verification_entry_enabled=(
                            not verified
                            and manual_verification_enabled(model_id)
                        ),
                        verification_requires_cost_estimate=(
                            not verified
                            and model_id
                            in HIGH_COST_VIDEO_VERIFICATION_MODELS
                        ),
                        operation_readiness=[
                            OperationReadiness(
                                operation="generate_video",
                                interaction_status=(
                                    "ready" if verified else "planned"
                                ),
                                availability_status=(
                                    "available"
                                    if verified
                                    else "verification_required"
                                ),
                                verification_status=(
                                    "verified"
                                    if verified
                                    else "contract_verified"
                                ),
                                status_reason=status_reason,
                            )
                        ],
                    )
                )
        return [
            profile
            for profile in profiles
            if profile.model_id
        ]

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
            api_key = self.router_service.repository.resolve_api_key(
                self.router_service.tenant_id,
                connection.id,
            )
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
        cached: _CachedVideoCatalog,
        *,
        stale: bool,
    ) -> VideoModelCatalogResponse:
        return VideoModelCatalogResponse(
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
    def _api_url(base_url: str, suffix: str) -> str:
        return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "video catalog unavailable",
                request=response.request,
                response=response,
            )

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("data"), list
        ):
            raise ValueError("invalid video catalog")
        return [
            item for item in payload["data"] if isinstance(item, dict)
        ]

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]

    @staticmethod
    def _integers(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        return [
            int(item)
            for item in value
            if isinstance(item, (int, float)) and int(item) > 0
        ]

    @staticmethod
    def _pricing(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(price)
            for key, price in value.items()
            if isinstance(key, str)
            and isinstance(price, (str, int, float))
        }

    @staticmethod
    def _frame_types(
        item: dict[str, Any],
    ) -> list[Literal["first_frame", "last_frame"]]:
        allowed = {"first_frame", "last_frame"}
        return [
            value
            for value in VideoCatalogService._strings(
                item.get("supported_frame_images")
            )
            if value in allowed
        ]

    @staticmethod
    def _provider_options(
        model_id: str,
        item: dict[str, Any],
    ) -> list[VideoProviderOption]:
        audited = PROVIDER_OPTION_AUDIT.get(model_id)
        if audited is None:
            return []
        _, definitions = audited
        live_allowed = set(
            VideoCatalogService._strings(
                item.get("allowed_passthrough_parameters")
            )
        )
        return [
            option.model_copy(deep=True)
            for option in definitions
            if option.key in live_allowed
        ]

    @staticmethod
    def _supports_audio(item: dict[str, Any]) -> bool:
        parameters = {
            value.lower()
            for value in VideoCatalogService._strings(
                item.get("allowed_passthrough_parameters")
                or item.get("supported_parameters")
            )
        }
        return bool(
            item.get("supports_generated_audio")
            or item.get("generate_audio")
            or "generate_audio" in parameters
        )

    @staticmethod
    def _supports_seed(item: dict[str, Any]) -> bool:
        parameters = {
            value.lower()
            for value in VideoCatalogService._strings(
                item.get("allowed_passthrough_parameters")
                or item.get("supported_parameters")
            )
        }
        return bool(item.get("seed") or "seed" in parameters)
