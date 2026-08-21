from __future__ import annotations

import time

from server.model_router.catalog import (
    CATALOG_TTL_SECONDS,
    CatalogCoordinator,
    NativeCatalogService,
    _CacheEntry,
)
from server.omniroute.catalog import OmniRouteCatalogService
from server.omniroute.config import OmniRouteSettings
from server.omniroute.schemas import ModelCandidate, ModelCatalogResponse


def _settings(*, enabled: bool = True, configured: bool = True) -> OmniRouteSettings:
    return OmniRouteSettings(
        enabled=enabled,
        base_url="http://omniroute:20128" if configured else "",
        api_key="secret" if configured else "",
        default_router="omniroute",
        catalog_ttl_seconds=30,
        stale_ttl_seconds=600,
    )


def _catalog(source: str = "omniroute") -> ModelCatalogResponse:
    return ModelCatalogResponse(
        source=source,
        router_status="online",
        stale=False,
        synced_at="2026-08-21T00:00:00+00:00",
        catalog_version="test",
        models=[
            ModelCandidate(
                profile_id="model",
                invocation_id="model",
                name="Model",
                provider="provider",
            )
        ],
    )


def test_omniroute_peek_is_side_effect_free_and_bounded() -> None:
    calls = 0

    def client_factory(_settings):
        nonlocal calls
        calls += 1
        raise AssertionError("peek must not construct a client")

    service = OmniRouteCatalogService(lambda: _settings(), client_factory)
    assert service.peek_catalog() is None
    assert calls == 0

    service._last_good = _catalog()
    service._last_good_monotonic = time.monotonic() - 60
    snapshot = service.peek_catalog()
    assert snapshot is not None and snapshot.stale is True
    assert snapshot.models[0].availability == "degraded"
    assert calls == 0


def test_disabled_sidecar_peek_reports_configuration_without_io() -> None:
    service = OmniRouteCatalogService(lambda: _settings(enabled=False))
    snapshot = service.peek_catalog()
    assert snapshot is not None
    assert snapshot.router_status == "disabled"
    assert snapshot.models == []


def test_native_peek_marks_old_cache_stale_without_refresh() -> None:
    native = NativeCatalogService()
    native._cache = _CacheEntry(
        catalog=_catalog("native"),
        stored_at=time.monotonic() - CATALOG_TTL_SECONDS - 1,
    )
    snapshot = native.peek_catalog()
    assert snapshot is not None
    assert snapshot.router_status == "stale"
    assert snapshot.models[0].availability == "degraded"


def test_coordinator_peek_uses_selected_sidecar_snapshot(monkeypatch) -> None:
    sidecar = OmniRouteCatalogService(lambda: _settings(enabled=False))
    coordinator = CatalogCoordinator(sidecar)

    class Service:
        @staticmethod
        def get_policy():
            return type("Policy", (), {"engine": "sidecar"})()

    monkeypatch.setattr(
        "server.model_router.catalog.get_model_router_service",
        lambda: Service(),
    )
    assert coordinator.peek_catalog().router_status == "disabled"
