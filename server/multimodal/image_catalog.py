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
    from server.model_router.egress import request_provider_url
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import request_provider_url
    from model_router.service import ModelRouterService

from .stt import MultimodalServiceError, OpenRouterTarget
from .readiness import OperationReadiness


IMAGE_CATALOG_TTL_SECONDS = 300.0
IMAGE_CATALOG_STALE_SECONDS = 1_800.0
IMAGE_PRICING_CONCURRENCY = 8


class ImageParameterProfile(BaseModel):
    type: Literal["enum", "range", "boolean", "string"]
    values: list[str] = Field(default_factory=list)
    min: int | float | None = None
    max: int | float | None = None


class ImagePricingItem(BaseModel):
    billable: Literal["input_image", "output_image"]
    unit: Literal["image"]
    cost_usd: float = Field(ge=0)
    variant: str | None = None


class ImageModelProfile(BaseModel):
    model_id: str
    display_name: str
    operation: Literal["analyze_image", "generate_image"]
    invocable: bool = True
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    supported_parameters: dict[str, ImageParameterProfile] = Field(
        default_factory=dict
    )
    pricing: list[ImagePricingItem] = Field(default_factory=list)
    supports_streaming: bool = False
    interaction_status: Literal["ready", "planned", "disabled"] = "ready"
    status_reason: str | None = None
    operation_readiness: list[OperationReadiness] = Field(
        default_factory=list
    )


class ImageModelCatalogResponse(BaseModel):
    source: Literal["openrouter"]
    status: Literal["online", "stale", "offline", "disabled"]
    stale: bool
    synced_at: str | None
    profiles: list[ImageModelProfile] = Field(default_factory=list)


class _CachedImageCatalog:
    def __init__(
        self,
        profiles: list[ImageModelProfile],
        synced_at: str,
        stored_at: float,
    ) -> None:
        self.profiles = profiles
        self.synced_at = synced_at
        self.stored_at = stored_at


class ImageCatalogService:
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
                trust_env=False,
            )
        )
        self._cache: _CachedImageCatalog | None = None
        self._lock = asyncio.Lock()

    async def get_catalog(
        self,
        *,
        force: bool = False,
    ) -> ImageModelCatalogResponse:
        analysis_enabled = self._enabled(
            "MULTIMODAL_IMAGE_ANALYSIS_ENABLED", default=True
        )
        generation_enabled = self._enabled(
            "MULTIMODAL_IMAGE_GENERATION_ENABLED", default=True
        )
        if not analysis_enabled and not generation_enabled:
            return ImageModelCatalogResponse(
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
            and now - cached.stored_at <= IMAGE_CATALOG_TTL_SECONDS
        ):
            return self._response(cached, stale=False)

        async with self._lock:
            now = time.monotonic()
            cached = self._cache
            if (
                not force
                and cached is not None
                and now - cached.stored_at <= IMAGE_CATALOG_TTL_SECONDS
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
                    and now - cached.stored_at <= IMAGE_CATALOG_STALE_SECONDS
                ):
                    return self._response(cached, stale=True)
                return ImageModelCatalogResponse(
                    source="openrouter",
                    status="offline",
                    stale=False,
                    synced_at=None,
                    profiles=[],
                )

            synced_at = datetime.now(UTC).isoformat()
            self._cache = _CachedImageCatalog(profiles, synced_at, now)
            return self._response(self._cache, stale=False)

    async def supports(
        self,
        model_id: str,
        operation: Literal["analyze_image", "generate_image"],
    ) -> bool:
        catalog = await self.get_catalog()
        return any(
            profile.model_id == model_id
            and profile.operation == operation
            and profile.invocable
            and profile.interaction_status == "ready"
            for profile in catalog.profiles
        )

    async def _fetch(
        self,
        target: OpenRouterTarget,
        *,
        analysis_enabled: bool,
        generation_enabled: bool,
    ) -> list[ImageModelProfile]:
        headers = {"Authorization": f"Bearer {target.api_key}"}
        async with self.client_factory() as client:
            requests = []
            if analysis_enabled:
                requests.append(
                    request_provider_url(
                        client,
                        self.router_service.egress_policy,
                        target.connection_id,
                        "GET",
                        self._api_url(target.base_url, "models"),
                        headers=headers,
                        params={"input_modalities": "image"},
                    )
                )
            if generation_enabled:
                requests.append(
                    request_provider_url(
                        client,
                        self.router_service.egress_policy,
                        target.connection_id,
                        "GET",
                        self._api_url(target.base_url, "images/models"),
                        headers=headers,
                    )
                )
            responses = await asyncio.gather(*requests)

        profiles: list[ImageModelProfile] = []
        response_index = 0
        if analysis_enabled:
            response = responses[response_index]
            response_index += 1
            self._raise_for_status(response)
            for item in self._items(response.json()):
                inputs = self._modalities(item, "input_modalities")
                outputs = self._modalities(item, "output_modalities")
                model_id = str(item.get("id") or "").strip()
                if model_id and "image" in inputs and "text" in outputs:
                    profiles.append(
                        ImageModelProfile(
                            model_id=model_id,
                            display_name=str(item.get("name") or model_id),
                            operation="analyze_image",
                            input_modalities=inputs,
                            output_modalities=outputs,
                            operation_readiness=[
                                OperationReadiness(
                                    operation="analyze_image",
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
            generation_items = [
                item
                for item in self._items(response.json())
                if str(item.get("id") or "").strip()
                and "image" in self._modalities(item, "output_modalities")
            ]
            pricing_semaphore = asyncio.Semaphore(IMAGE_PRICING_CONCURRENCY)

            async def fetch_pricing(model_id: str):
                async with pricing_semaphore:
                    return model_id, await self._fetch_pricing(target, model_id)

            pricing_by_model = dict(
                await asyncio.gather(
                    *(
                        fetch_pricing(str(item["id"]).strip())
                        for item in generation_items
                    )
                )
            )
            for item in generation_items:
                model_id = str(item.get("id") or "").strip()
                inputs = self._modalities(item, "input_modalities")
                outputs = self._modalities(item, "output_modalities")
                profiles.append(
                    ImageModelProfile(
                        model_id=model_id,
                        display_name=str(item.get("name") or model_id),
                        operation="generate_image",
                        input_modalities=inputs,
                        output_modalities=outputs,
                        supported_parameters=self._parameters(
                            item.get("supported_parameters")
                        ),
                        pricing=pricing_by_model.get(model_id, []),
                        supports_streaming=bool(
                            item.get("supports_streaming")
                        ),
                        operation_readiness=[
                            OperationReadiness(
                                operation="generate_image",
                                interaction_status="ready",
                                availability_status="available",
                                verification_status="verified",
                            )
                        ],
                    )
                )
        return profiles

    async def _fetch_pricing(
        self,
        target: OpenRouterTarget,
        model_id: str,
    ) -> list[ImagePricingItem]:
        try:
            async with self.client_factory() as client:
                response = await request_provider_url(
                    client,
                    self.router_service.egress_policy,
                    target.connection_id,
                    "GET",
                    self._api_url(
                        target.base_url,
                        f"images/models/{model_id}/endpoints",
                    ),
                    headers={"Authorization": f"Bearer {target.api_key}"},
                )
            if response.status_code >= 400:
                return []
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        endpoints = (
            payload.get("endpoints") if isinstance(payload, dict) else None
        )
        result: list[ImagePricingItem] = []
        if not isinstance(endpoints, list):
            return result
        for endpoint in endpoints:
            raw_pricing = (
                endpoint.get("pricing")
                if isinstance(endpoint, dict)
                else None
            )
            if not isinstance(raw_pricing, list):
                continue
            for raw in raw_pricing:
                if not isinstance(raw, dict):
                    continue
                billable = str(raw.get("billable") or "")
                unit = str(raw.get("unit") or "")
                cost = raw.get("cost_usd")
                if (
                    billable not in {"input_image", "output_image"}
                    or unit != "image"
                    or not isinstance(cost, (int, float))
                    or cost < 0
                ):
                    continue
                variant = str(raw.get("variant") or "").strip() or None
                result.append(
                    ImagePricingItem(
                        billable=billable,
                        unit="image",
                        cost_usd=float(cost),
                        variant=variant,
                    )
                )
        return result

    def resolve_target(self) -> OpenRouterTarget:
        connections = [
            item
            for item in self.router_service.list_connections(scope="chat")
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
    def _parameters(value: Any) -> dict[str, ImageParameterProfile]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, ImageParameterProfile] = {}
        for key, raw in value.items():
            if not isinstance(raw, dict):
                continue
            parameter_type = str(raw.get("type") or "")
            if parameter_type not in {"enum", "range", "boolean", "string"}:
                continue
            result[str(key)] = ImageParameterProfile(
                type=parameter_type,
                values=[str(item) for item in raw.get("values", [])],
                min=raw.get("min") if isinstance(raw.get("min"), (int, float)) else None,
                max=raw.get("max") if isinstance(raw.get("max"), (int, float)) else None,
            )
        return result

    @staticmethod
    def _modalities(item: dict[str, Any], key: str) -> list[str]:
        value = item.get(key)
        if not isinstance(value, list):
            architecture = item.get("architecture")
            value = architecture.get(key) if isinstance(architecture, dict) else []
        return [str(entry) for entry in value if isinstance(entry, str)]

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise MultimodalServiceError(
                "image_catalog_unavailable",
                "图片能力目录暂时不可用，请稍后重试。",
                status_code=503,
            )

    @staticmethod
    def _response(
        cached: _CachedImageCatalog,
        *,
        stale: bool,
    ) -> ImageModelCatalogResponse:
        return ImageModelCatalogResponse(
            source="openrouter",
            status="stale" if stale else "online",
            stale=stale,
            synced_at=cached.synced_at,
            profiles=[item.model_copy(deep=True) for item in cached.profiles],
        )

    @staticmethod
    def _api_url(base_url: str, suffix: str) -> str:
        return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"

    @staticmethod
    def _enabled(name: str, *, default: bool) -> bool:
        fallback = "true" if default else "false"
        return os.getenv(name, fallback).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
