from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.model_router.admin_auth import reset_provider_admin_auth
from server.model_router.api import configure_model_router, models_router, router
from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService


PAIRING_SECRET = "provider-admin-test-secret-at-least-32-chars"


@pytest.fixture(autouse=True)
def _reset_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


def _app(tmp_path: Path) -> tuple[FastAPI, SQLiteRouterRepository, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="newAPI",
            kind="newapi",
            base_url="https://newapi.example/v1",
            api_key="canary-secret-key",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-20T00:00:00+00:00",
    )
    certification, _ = repository.claim_chat_certification(
        "local",
        certification_id="cert-api",
        connection_id=connection.id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection.id
        ),
        contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
        requested_model="provider/model",
        idempotency_key_hash=hashlib.sha256(b"cert-api").hexdigest(),
    )
    repository.complete_chat_certification(
        "local",
        str(certification["id"]),
        status="passed",
        checks={"terminal_observed": True},
        warning_codes=[],
    )
    configure_model_router(ModelRouterService(repository))
    app = FastAPI()
    app.include_router(router)
    app.include_router(models_router)
    return app, repository, connection.id


@pytest.mark.asyncio
async def test_public_status_is_redacted_no_store_and_exact_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    app, repository, connection_id = _app(tmp_path)
    repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=True
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        available = await client.get(
            "/api/models/provider-chat-canary",
            params={"model_id": "provider/model"},
        )
        missing = await client.get(
            "/api/models/provider-chat-canary",
            params={"model_id": "other/model"},
        )

    assert available.status_code == 200
    assert available.headers["cache-control"] == "no-store"
    assert available.json() == {
        "contract_version": "modelmirror-provider-chat-canary-v1",
        "feature_enabled": True,
        "available": True,
        "gateway": "newapi_canary",
        "model_id": "provider/model",
        "reason_code": "available",
        "consent_revision": "provider-chat-canary-consent-v1",
    }
    assert missing.json()["reason_code"] == "certification_required"
    assert connection_id not in available.text
    assert "newapi.example" not in available.text
    assert "canary-secret-key" not in available.text


@pytest.mark.asyncio
async def test_admin_routes_require_session_and_put_requires_csrf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    app, _repository, connection_id = _app(tmp_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/router/canaries/chat")).status_code == 401
        assert (
            await client.put(
                "/api/router/canaries/chat",
                json={"connection_id": connection_id, "enabled": True},
            )
        ).status_code == 401
        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        csrf = paired.json()["csrf_token"]
        assert (await client.get("/api/router/canaries/chat")).status_code == 200
        assert (
            await client.put(
                "/api/router/canaries/chat",
                json={"connection_id": connection_id, "enabled": True},
            )
        ).status_code == 403
        updated = await client.put(
            "/api/router/canaries/chat",
            headers={"X-ModelMirror-CSRF": csrf},
            json={"connection_id": connection_id, "enabled": True},
        )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["policy_enabled"] is True
    assert payload["selected_connection_id"] == connection_id
    assert payload["connections"][0]["models"][0]["available"] is True
    assert "canary-secret-key" not in updated.text


@pytest.mark.asyncio
async def test_deployment_flag_fails_closed_without_affecting_public_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    monkeypatch.delenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", raising=False)
    app, _repository, connection_id = _app(tmp_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        public = await client.get(
            "/api/models/provider-chat-canary",
            params={"model_id": "provider/model"},
        )
        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        update = await client.put(
            "/api/router/canaries/chat",
            headers={"X-ModelMirror-CSRF": paired.json()["csrf_token"]},
            json={"connection_id": connection_id, "enabled": True},
        )

    assert public.status_code == 200
    assert public.json()["feature_enabled"] is False
    assert public.json()["reason_code"] == "feature_disabled"
    assert update.status_code == 503
    assert update.json()["detail"]["code"] == "provider_chat_canary_disabled"
