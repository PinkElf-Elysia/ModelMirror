from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

try:
    from server.omniroute.catalog import (
        OmniRouteCatalogService,
        normalize_model,
    )
    from server.omniroute.schemas import (
        ModelCandidate,
        ModelCatalogResponse,
        RouteCandidate,
        RouterStatusResponse,
    )
except ModuleNotFoundError:
    from omniroute.catalog import OmniRouteCatalogService, normalize_model
    from omniroute.schemas import (
        ModelCandidate,
        ModelCatalogResponse,
        RouteCandidate,
        RouterStatusResponse,
    )

from .api import get_model_router_service
from .schemas import RouterConnection
from .service import ModelRouterService


CATALOG_TTL_SECONDS = 30.0
STALE_IF_ERROR_SECONDS = 600.0
NATIVE_CATALOG_VERSION = "modelmirror-native-catalog-v1"

ROUTES = (
    ("auto", "智能调度", "在质量、速度、成本和稳定性之间自动平衡"),
    ("auto/fast", "速度优先", "优先选择响应更快的可用模型"),
    ("auto/quality", "质量优先", "优先选择综合能力更强的可用模型"),
    ("auto/cheap", "成本优先", "在满足能力要求时优先降低费用"),
    ("auto/reliable", "稳定优先", "优先选择近期成功率更高的模型"),
    ("auto/offline", "本地优先", "优先选择本地或不依赖外部额度的连接"),
)


@dataclass
class _CacheEntry:
    catalog: ModelCatalogResponse
    stored_at: float


class NativeCatalogService:
    def __init__(self) -> None:
        self._cache: _CacheEntry | None = None
        self._lock = asyncio.Lock()

    async def get_catalog(
        self,
        service: ModelRouterService,
        fallback: OmniRouteCatalogService,
    ) -> ModelCatalogResponse:
        now = time.monotonic()
        cached = self._cache
        if cached and now - cached.stored_at <= CATALOG_TTL_SECONDS:
            return cached.catalog.model_copy(deep=True)
        async with self._lock:
            now = time.monotonic()
            cached = self._cache
            if cached and now - cached.stored_at <= CATALOG_TTL_SECONDS:
                return cached.catalog.model_copy(deep=True)
            fresh = await self._fetch(service)
            if fresh.models:
                self._cache = _CacheEntry(catalog=fresh, stored_at=now)
                return fresh.model_copy(deep=True)
            if cached and now - cached.stored_at <= STALE_IF_ERROR_SECONDS:
                stale = cached.catalog.model_copy(deep=True)
                stale.stale = True
                stale.router_status = "stale"
                for model in stale.models:
                    model.availability = "degraded"
                return stale
            bundled = await fallback.get_catalog()
            bundled = bundled.model_copy(deep=True)
            bundled.source = "bundled"
            bundled.router_status = "offline"
            bundled.stale = False
            bundled.catalog_version = NATIVE_CATALOG_VERSION
            for model in bundled.models:
                model.invocable = False
                model.availability = "offline"
                model.source = "bundled"
                model.connection_id = None
            bundled.routes = self._routes(0, False)
            return bundled

    async def get_status(
        self,
        service: ModelRouterService,
        fallback: OmniRouteCatalogService,
    ) -> RouterStatusResponse:
        catalog = await self.get_catalog(service, fallback)
        status = service.status()
        return RouterStatusResponse(
            enabled=True,
            configured=status.connection_count > 0,
            status=catalog.router_status,
            version=NATIVE_CATALOG_VERSION,
            candidate_count=sum(1 for item in catalog.models if item.invocable),
            route_count=sum(1 for item in catalog.routes if item.invocable),
            synced_at=catalog.synced_at,
            stale=catalog.stale,
        )

    async def _fetch(
        self, service: ModelRouterService
    ) -> ModelCatalogResponse:
        connections = [
            item for item in service.list_connections() if item.enabled
        ]
        results = await asyncio.gather(
            *(self._fetch_connection(service, item) for item in connections),
            return_exceptions=True,
        )
        models: list[ModelCandidate] = []
        for result in results:
            if isinstance(result, list):
                models.extend(result)
        models.sort(key=lambda item: (item.provider.lower(), item.name.lower()))
        synced_at = (
            datetime.now(UTC).isoformat() if models else None
        )
        return ModelCatalogResponse(
            source="native",
            router_status="online" if models else "offline",
            stale=False,
            synced_at=synced_at,
            catalog_version=NATIVE_CATALOG_VERSION,
            models=models,
            routes=self._routes(len(models), bool(models)),
        )

    async def _fetch_connection(
        self,
        service: ModelRouterService,
        connection: RouterConnection,
    ) -> list[ModelCandidate]:
        result, records = await service.fetch_connection_model_records(
            connection.id
        )
        if not result.ok:
            return []
        models: list[ModelCandidate] = []
        for record in records:
            architecture = record.get("architecture")
            architecture = architecture if isinstance(architecture, dict) else {}
            normalized = normalize_model(
                {
                    **record,
                    "input_modalities": (
                        record.get("input_modalities")
                        or architecture.get("input_modalities")
                    ),
                    "output_modalities": (
                        record.get("output_modalities")
                        or architecture.get("output_modalities")
                    ),
                }
            )
            if normalized is None:
                continue
            normalized.profile_id = (
                f"native:{connection.id}:{normalized.invocation_id}"
            )
            normalized.provider = connection.name
            normalized.source = "native"
            normalized.connection_id = connection.id
            normalized.invocable = True
            normalized.availability = "live"
            models.append(normalized)
        return models

    @staticmethod
    def _routes(
        candidate_count: int, invocable: bool
    ) -> list[RouteCandidate]:
        return [
            RouteCandidate(
                id=route_id,
                name=name,
                description=description,
                channel=route_id.removeprefix("auto/") or "auto",
                candidate_count=candidate_count,
                reachable_count=candidate_count,
                invocable=invocable,
                availability="live" if invocable else "offline",
            )
            for route_id, name, description in ROUTES
        ]


class CatalogCoordinator:
    def __init__(
        self,
        sidecar: OmniRouteCatalogService,
        native: NativeCatalogService | None = None,
    ) -> None:
        self.sidecar = sidecar
        self.native = native or NativeCatalogService()

    async def get_catalog(self) -> ModelCatalogResponse:
        service = get_model_router_service()
        policy = service.get_policy()
        if policy.engine in {"native", "native_canary"}:
            return await self.native.get_catalog(service, self.sidecar)
        return await self.sidecar.get_catalog()

    async def get_status(self) -> RouterStatusResponse:
        service = get_model_router_service()
        policy = service.get_policy()
        if policy.engine in {"native", "native_canary"}:
            return await self.native.get_status(service, self.sidecar)
        return await self.sidecar.get_status()
