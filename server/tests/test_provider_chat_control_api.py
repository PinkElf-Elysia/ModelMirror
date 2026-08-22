from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.model_router.admin_auth import reset_provider_admin_auth
from server.model_router.api import configure_model_router, models_router, router
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService


PAIRING_SECRET = "provider-admin-test-secret-at-least-32-chars"


@pytest.fixture(autouse=True)
def _reset_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


def _app(tmp_path: Path) -> FastAPI:
    configure_model_router(
        ModelRouterService(SQLiteRouterRepository(tmp_path, master_key=b"x" * 32))
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(models_router)
    return app


@pytest.mark.asyncio
async def test_public_status_is_redacted_no_store_and_does_not_gate_data_plane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)), base_url="http://localhost"
    ) as client:
        response = await client.get(
            "/api/models/provider-chat-control",
            params={"model_id": "provider/model", "capability": "chat_text"},
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["contract_version"] == "modelmirror-provider-chat-routing-v1"
    assert payload["data_plane_integrated"] is False
    assert payload["available"] is False
    assert payload["would_block"] is False
    assert "tenant" not in response.text
    assert "base_url" not in response.text


@pytest.mark.asyncio
async def test_admin_policy_gate_and_receipts_require_session_and_csrf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    app = _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/router/chat-control/policy")).status_code == 401
        assert (await client.get("/api/router/chat-control/gate")).status_code == 401
        assert (
            await client.get("/api/router/chat-control/receipts")
        ).status_code == 401

        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        csrf = paired.json()["csrf_token"]
        policy = await client.get("/api/router/chat-control/policy")
        assert policy.status_code == 200
        assert policy.json()["revision"] == 0
        assert policy.json()["configured_mode"] == "legacy"

        payload = {
            "expected_revision": 0,
            "mode": "legacy",
            "auto_enabled": False,
            "stable_model_ids": [],
            "routes": [],
        }
        assert (
            await client.put("/api/router/chat-control/policy", json=payload)
        ).status_code == 403
        saved = await client.put(
            "/api/router/chat-control/policy",
            headers={"X-ModelMirror-CSRF": csrf},
            json=payload,
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1

        gate = await client.get("/api/router/chat-control/gate")
        assert gate.status_code == 200
        assert gate.json()["required_activation_available"] is False
        assert "provider_chat_control_data_plane_pending_r5b" in gate.json()[
            "blocking_reason_codes"
        ]
        receipts = await client.get("/api/router/chat-control/receipts")
        assert receipts.status_code == 200
        assert receipts.json()["runs"] == []
        assert receipts.json()["next_cursor"] is None
        invalid_cursor = await client.get(
            "/api/router/chat-control/receipts", params={"cursor": "not-found"}
        )
        assert invalid_cursor.status_code == 422
        assert invalid_cursor.json()["detail"]["code"] == (
            "provider_chat_receipt_cursor_invalid"
        )


@pytest.mark.asyncio
async def test_required_policy_is_rejected_until_r5e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)), base_url="http://localhost"
    ) as client:
        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        response = await client.put(
            "/api/router/chat-control/policy",
            headers={"X-ModelMirror-CSRF": paired.json()["csrf_token"]},
            json={
                "expected_revision": 0,
                "mode": "newapi_required_default",
                "auto_enabled": False,
                "stable_model_ids": [],
                "routes": [],
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "provider_chat_required_activation_not_available"
    )
