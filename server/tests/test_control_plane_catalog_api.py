from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.admin_auth import reset_provider_admin_auth
from server.model_router.api import configure_model_router, models_router, router
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.provider_catalog import ProviderCatalogService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService


PAIRING_SECRET = "provider-admin-test-secret-at-least-32-chars"


@pytest.fixture(autouse=True)
def _reset_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


async def _app(tmp_path: Path) -> tuple[FastAPI, str]:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="newAPI",
            kind="newapi",
            base_url="https://provider.example/v1",
            api_key="catalog-secret",
            scopes=["chat"],
        ),
    )

    def handler(_request: Request) -> Response:
        return Response(200, json={"data": [{"id": "provider/model"}]})

    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler), trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    configure_model_router(service)
    await ProviderCatalogService(service).refresh_connection(connection.id)
    app = FastAPI()
    app.include_router(router)
    app.include_router(models_router)
    return app, connection.id


@pytest.mark.asyncio
async def test_public_catalog_is_redacted_and_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "false")
    app, _connection_id = await _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.get(
            "/api/models/control-plane-catalog?include_unavailable=true"
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["contract_version"] == "modelmirror-provider-catalog-v1"
    assert payload["models"][0]["model_id"] == "provider/model"
    serialized = response.text
    assert "catalog-secret" not in serialized
    assert "provider.example" not in serialized
    assert "connection_id" not in serialized
    assert "tenant_id" not in serialized


@pytest.mark.asyncio
async def test_admin_overview_and_offerings_require_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "false")
    app, connection_id = await _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/router/control-plane/overview")).status_code == 401
        assert (await client.get("/api/router/catalog/offerings")).status_code == 401
        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        assert paired.status_code == 200
        overview = await client.get("/api/router/control-plane/overview")
        offerings = await client.get("/api/router/catalog/offerings")
        filtered = await client.get(
            "/api/router/catalog/offerings?status=active&operation=chat"
        )

    assert overview.status_code == 200
    assert overview.json()["provider_count"] == 1
    assert overview.json()["default_qualification"] == "not_evaluated"
    assert offerings.status_code == 200
    assert offerings.json()["offerings"][0]["connection_id"] == connection_id
    assert filtered.status_code == 200
    assert len(filtered.json()["offerings"]) == 1
    assert "catalog-secret" not in offerings.text
    assert "provider.example" not in offerings.text


@pytest.mark.asyncio
async def test_public_catalog_pagination_rejects_stale_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "false")
    app, _connection_id = await _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        first = await client.get(
            "/api/models/control-plane-catalog?include_unavailable=true&limit=1"
        )
        invalid = await client.get(
            "/api/models/control-plane-catalog?include_unavailable=true&cursor=invalid"
        )

    assert first.status_code == 200
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_catalog_cursor"
