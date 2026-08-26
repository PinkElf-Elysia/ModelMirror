from __future__ import annotations

from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.model_router.admin_auth import (
    SESSION_TTL_SECONDS,
    reset_provider_admin_auth,
)
from server.model_router.api import configure_model_router, router
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService


PAIRING_SECRET = "provider-admin-test-secret-at-least-32-chars"


@pytest.fixture(autouse=True)
def _reset_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


def _app(tmp_path: Path) -> FastAPI:
    configure_model_router(ModelRouterService(SQLiteRouterRepository(tmp_path)))
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_missing_pairing_secret_locks_only_management(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        session = await client.get("/api/router/admin/session")
        assert session.json() == {
            "configured": False,
            "authenticated": False,
            "expires_at": None,
            "csrf_token": None,
        }
        protected = await client.get("/api/router/connections")
        assert protected.status_code == 503
        assert protected.json()["detail"]["code"] == "admin_pairing_not_configured"


@pytest.mark.asyncio
async def test_pairing_cookie_csrf_and_logout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        paired = await client.post(
            "/api/router/admin/session",
            json={"pairing_secret": PAIRING_SECRET},
        )
        assert paired.status_code == 200
        payload = paired.json()
        assert payload["authenticated"] is True
        assert "provider-admin-test-secret" not in paired.text
        cookie = paired.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/api/router" in cookie
        assert "secure" not in cookie
        set_cookies = [value.lower() for value in paired.headers.get_list("set-cookie")]
        assert any(
            "modelmirror_rag_admin=" in value and "path=/api/rag" in value
            for value in set_cookies
        )
        parsed_cookie = SimpleCookie()
        parsed_cookie.load(paired.headers["set-cookie"])
        assert parsed_cookie["modelmirror_provider_admin"].value not in paired.text

        assert (await client.get("/api/router/connections")).status_code == 200
        rejected = await client.post(
            "/api/router/connections",
            json={
                "name": "OpenRouter",
                "kind": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "test-key",
            },
        )
        assert rejected.status_code == 403
        assert rejected.json()["detail"]["code"] == "admin_csrf_invalid"
        wrong_csrf = await client.post(
            "/api/router/connections",
            headers={"X-ModelMirror-CSRF": "wrong"},
            json={
                "name": "OpenRouter",
                "kind": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "test-key",
            },
        )
        assert wrong_csrf.status_code == 403

        logged_out = await client.delete(
            "/api/router/admin/session",
            headers={"X-ModelMirror-CSRF": payload["csrf_token"]},
        )
        assert logged_out.status_code == 204
        assert (await client.get("/api/router/admin/session")).json()[
            "authenticated"
        ] is False
        assert (await client.get("/api/router/connections")).status_code == 401


@pytest.mark.asyncio
async def test_pairing_requires_https_outside_loopback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://admin.example",
    ) as client:
        for headers in (
            {},
            {"X-Forwarded-Proto": "https"},
            {"Origin": "https://admin.example"},
        ):
            rejected = await client.post(
                "/api/router/admin/session",
                headers=headers,
                json={"pairing_secret": PAIRING_SECRET},
            )
            assert rejected.status_code == 403
            assert rejected.json()["detail"]["code"] == "admin_https_required"

    reset_provider_admin_auth()
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path / "https")),
        base_url="https://admin.example",
    ) as client:
        accepted = await client.post(
            "/api/router/admin/session",
            json={"pairing_secret": PAIRING_SECRET},
        )
        assert accepted.status_code == 200
        assert "secure" in accepted.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_native_gate_mutations_require_session_and_csrf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        approval = {
            "no_open_p0_p1": True,
            "drills": {
                "connection_failure": True,
                "rate_limit": True,
                "stream_interrupt": True,
            },
        }
        assert (
            await client.put("/api/router/gate/approval", json=approval)
        ).status_code == 401
        assert (await client.delete("/api/router/gate/approval")).status_code == 401

        paired = await client.post(
            "/api/router/admin/session",
            json={"pairing_secret": PAIRING_SECRET},
        )
        assert paired.status_code == 200
        assert (
            await client.put("/api/router/gate/approval", json=approval)
        ).status_code == 403
        assert (await client.delete("/api/router/gate/approval")).status_code == 403


@pytest.mark.asyncio
async def test_failed_pairing_is_rate_limited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        for _ in range(5):
            failed = await client.post(
                "/api/router/admin/session",
                json={"pairing_secret": "wrong"},
            )
            assert failed.status_code == 401
        limited = await client.post(
            "/api/router/admin/session",
            json={"pairing_secret": PAIRING_SECRET},
        )
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 300


@pytest.mark.asyncio
async def test_pairing_rate_limit_window_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    clock = [2_000.0]
    monkeypatch.setattr(
        "server.model_router.admin_auth.time.time", lambda: clock[0]
    )
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        for _ in range(5):
            assert (
                await client.post(
                    "/api/router/admin/session",
                    json={"pairing_secret": "wrong"},
                )
            ).status_code == 401
        assert (
            await client.post(
                "/api/router/admin/session",
                json={"pairing_secret": PAIRING_SECRET},
            )
        ).status_code == 429
        clock[0] += 301
        assert (
            await client.post(
                "/api/router/admin/session",
                json={"pairing_secret": PAIRING_SECRET},
            )
        ).status_code == 200


@pytest.mark.asyncio
async def test_pairing_uses_constant_time_compare_and_logs_no_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    calls = 0
    original_compare = __import__("hmac").compare_digest

    def compare(left: object, right: object) -> bool:
        nonlocal calls
        calls += 1
        return original_compare(left, right)  # type: ignore[arg-type]

    monkeypatch.setattr("server.model_router.admin_auth.hmac.compare_digest", compare)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        response = await client.post(
            "/api/router/admin/session",
            json={"pairing_secret": PAIRING_SECRET},
        )
    assert response.status_code == 200
    assert calls >= 1
    assert PAIRING_SECRET not in caplog.text


@pytest.mark.asyncio
async def test_short_pairing_secret_and_server_restart_keep_management_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", "too-short")
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path / "short")),
        base_url="http://localhost",
    ) as client:
        response = await client.post(
            "/api/router/admin/session", json={"pairing_secret": "too-short"}
        )
        assert response.status_code == 503

    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    reset_provider_admin_auth()
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path / "restart")),
        base_url="http://localhost",
    ) as client:
        assert (
            await client.post(
                "/api/router/admin/session",
                json={"pairing_secret": PAIRING_SECRET},
            )
        ).status_code == 200
        reset_provider_admin_auth()
        assert (await client.get("/api/router/admin/session")).json()[
            "authenticated"
        ] is False


@pytest.mark.asyncio
async def test_session_expires_absolutely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    clock = [1_000.0]
    monkeypatch.setattr(
        "server.model_router.admin_auth.time.time", lambda: clock[0]
    )
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)),
        base_url="http://localhost",
    ) as client:
        paired = await client.post(
            "/api/router/admin/session",
            json={"pairing_secret": PAIRING_SECRET},
        )
        assert paired.status_code == 200
        assert SESSION_TTL_SECONDS == 28_800
        clock[0] += SESSION_TTL_SECONDS + 1
        expired = await client.get("/api/router/admin/session")
        assert expired.json()["authenticated"] is False
