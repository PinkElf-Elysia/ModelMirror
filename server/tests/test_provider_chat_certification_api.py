from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.admin_auth import reset_provider_admin_auth
from server.model_router.api import configure_model_router, router
from server.model_router.chat_certification import ProviderChatCertificationService
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderChatCertificationSummary,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService


PAIRING_SECRET = "provider-admin-test-secret-at-least-32-chars"


def test_model_router_service_lazy_initialization_is_thread_safe(monkeypatch) -> None:
    import server.model_router.api as router_api

    original_service = router_api._service
    start = threading.Barrier(8)
    creation_lock = threading.Lock()
    creations = 0

    class _Service:
        def __init__(self) -> None:
            nonlocal creations
            with creation_lock:
                creations += 1
            time.sleep(0.05)

    monkeypatch.setattr(router_api, "ModelRouterService", _Service)
    router_api._service = None
    try:
        def resolve_service() -> object:
            start.wait()
            return router_api.get_model_router_service()

        with ThreadPoolExecutor(max_workers=8) as executor:
            services = list(executor.map(lambda _: resolve_service(), range(8)))

        assert creations == 1
        assert all(service is services[0] for service in services)
    finally:
        router_api._service = original_service


@pytest.fixture(autouse=True)
def _reset_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


def _app(tmp_path: Path) -> tuple[FastAPI, str]:
    def handler(_request: Request) -> Response:
        return Response(
            200,
            json={"data": [{"id": f"provider/model-{index}"} for index in range(501)]},
        )

    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="newAPI",
            kind="newapi",
            base_url="https://newapi.example/v1",
            api_key="secret-key",
            scopes=["chat"],
        ),
    )
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
    app = FastAPI()
    app.include_router(router)
    return app, connection.id


@pytest.mark.asyncio
async def test_certification_admin_routes_require_session_csrf_and_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    app, connection_id = _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/router/certifications/chat")).status_code == 401
        assert (
            await client.post(f"/api/router/connections/{connection_id}/models/refresh")
        ).status_code == 401

        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        csrf = paired.json()["csrf_token"]
        status_before = await client.get("/api/router/certifications/chat")
        assert status_before.status_code == 200
        assert status_before.json()["contract_version"] == "modelmirror-provider-chat-v1"
        assert status_before.json()["certifications"][0]["status"] == "not_run"

        assert (
            await client.post(f"/api/router/connections/{connection_id}/models/refresh")
        ).status_code == 403
        refreshed = await client.post(
            f"/api/router/connections/{connection_id}/models/refresh",
            headers={"X-ModelMirror-CSRF": csrf},
        )
        assert refreshed.status_code == 200
        assert len(refreshed.json()["model_ids"]) == 500
        assert refreshed.json()["model_count"] == 501
        assert refreshed.json()["truncated"] is True

        missing_idempotency = await client.post(
            f"/api/router/connections/{connection_id}/certifications/chat",
            headers={"X-ModelMirror-CSRF": csrf},
            json={
                "model_id": "provider/model-1",
                "acknowledge_billed_call": True,
            },
        )
        assert missing_idempotency.status_code == 422
        assert (
            missing_idempotency.json()["detail"]["code"]
            == "invalid_idempotency_key"
        )

        not_acknowledged = await client.post(
            f"/api/router/connections/{connection_id}/certifications/chat",
            headers={
                "X-ModelMirror-CSRF": csrf,
                "Idempotency-Key": "test-key",
            },
            json={
                "model_id": "provider/model-1",
                "acknowledge_billed_call": False,
            },
        )
        assert not_acknowledged.status_code == 422
        assert (
            not_acknowledged.json()["detail"]["code"]
            == "billed_call_acknowledgement_required"
        )


@pytest.mark.asyncio
async def test_certification_post_returns_only_redacted_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    app, connection_id = _app(tmp_path)

    async def fake_run(self, selected_connection_id: str, **_kwargs):
        assert selected_connection_id == connection_id
        return ProviderChatCertificationSummary(
            certification_id="cert-1",
            connection_id=connection_id,
            connection_name="newAPI",
            status="passed",
            can_run=True,
            requested_model="provider/model-1",
        )

    monkeypatch.setattr(ProviderChatCertificationService, "run", fake_run)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        result = await client.post(
            f"/api/router/connections/{connection_id}/certifications/chat",
            headers={
                "X-ModelMirror-CSRF": paired.json()["csrf_token"],
                "Idempotency-Key": "one-call",
            },
            json={
                "model_id": "provider/model-1",
                "acknowledge_billed_call": True,
            },
        )
    assert result.status_code == 200
    assert result.json()["status"] == "passed"
    assert "secret-key" not in result.text
    assert "Reply with OK." not in result.text
