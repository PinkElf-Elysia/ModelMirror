from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.api import configure_model_router, router
from server.model_router.catalog import NativeCatalogService
from server.model_router.repository import (
    RouterConnectionNotFound,
    SCHEMA_VERSION,
    SQLiteRouterRepository,
)
from server.model_router.schemas import (
    RouterConnectionCreate,
    RouterConnectionUpdate,
    RouterPolicy,
)
from server.model_router.service import ModelRouterService
from server.model_router.service import RouterServiceError
from server.omniroute.schemas import ModelCatalogResponse


def connection_payload(**updates: object) -> RouterConnectionCreate:
    data: dict[str, object] = {
        "name": "OpenRouter",
        "kind": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-test-secret-value",
    }
    data.update(updates)
    return RouterConnectionCreate.model_validate(data)


def test_schema_and_credentials_are_tenant_scoped_and_persistent(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    created = repository.create_connection("local", connection_payload())

    assert all(repository.count_schema_tenant_columns().values())
    assert created.tenant_id == "local"
    assert created.masked_key != "sk-test-secret-value"
    assert repository.resolve_api_key("local", created.id) == "sk-test-secret-value"
    with pytest.raises(RouterConnectionNotFound):
        repository.get_connection("another-tenant", created.id)

    persisted = repository.database_path.read_bytes()
    assert b"sk-test-secret-value" not in persisted

    restarted = SQLiteRouterRepository(tmp_path)
    restored = restarted.get_connection("local", created.id)
    assert restored.id == created.id
    assert restarted.resolve_api_key("local", created.id) == "sk-test-secret-value"

    with sqlite3.connect(restarted.database_path) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )


def test_disable_restore_and_policy_persist_without_delete(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    created = repository.create_connection("local", connection_payload())

    disabled = repository.update_connection(
        "local", created.id, RouterConnectionUpdate(enabled=False)
    )
    assert disabled.enabled is False
    assert disabled.health == "disabled"

    restored = repository.update_connection(
        "local", created.id, RouterConnectionUpdate(enabled=True)
    )
    assert restored.enabled is True
    assert restored.health == "untested"

    saved = repository.save_policy(
        "local",
        RouterPolicy(
            tenant_id="ignored-by-repository",
            engine="shadow",
            default_mode="reliable",
            canary_percent=10,
            compression_mode="auto",
        ),
    )
    assert saved.tenant_id == "local"
    assert SQLiteRouterRepository(tmp_path).get_policy("local") == saved


def test_candidate_breaker_and_lkgp_are_tenant_model_scoped(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    for _ in range(3):
        stats = repository.record_candidate_outcome(
            "local",
            "connection-a",
            "model-a",
            success=False,
            latency_ms=800,
        )
    assert stats["breaker_state"] == "open"
    assert (
        repository.get_candidate_stats("local", "connection-a", "model-b")[
            "breaker_state"
        ]
        == "closed"
    )
    assert (
        repository.get_candidate_stats("another", "connection-a", "model-a")[
            "breaker_state"
        ]
        == "closed"
    )

    decision_id = repository.record_routing_decision(
        "local",
        session_id_hash="session-hash",
        engine="native",
        strategy="reliable",
        connection_id="connection-a",
        model_id="model-a",
        reason_codes=["mode_reliable"],
        outcome="success",
    )
    assert decision_id.startswith("decision_")
    assert repository.get_last_known_good("local", "session-hash") == (
        "connection-a",
        "model-a",
    )
    assert repository.get_last_known_good("another", "session-hash") is None


def test_diagnostics_are_tenant_scoped_and_native_default_is_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_ROUTER_ALLOW_NATIVE_OVERRIDE", raising=False)
    repository = SQLiteRouterRepository(tmp_path)
    repository.record_routing_decision(
        "local",
        session_id_hash="local-session",
        engine="native",
        strategy="auto",
        connection_id="connection-a",
        model_id="model-a",
        reason_codes=["mode_auto"],
        outcome="success",
        budget_limit_usd=0.1,
        reserved_cost_usd=0.05,
    )
    repository.record_routing_decision(
        "another",
        session_id_hash="other-session",
        engine="native",
        strategy="cheap",
        connection_id="connection-b",
        model_id="private-model",
        reason_codes=["mode_cheap"],
        outcome="success",
    )

    diagnostics = repository.get_diagnostics("local")
    assert diagnostics["migration_gate"]["request_count"] == 1
    assert len(diagnostics["recent_decisions"]) == 1
    assert "private-model" not in json.dumps(diagnostics)
    assert diagnostics["recent_decisions"][0]["budget"]["status"] == "reserved"

    empty_service = ModelRouterService(SQLiteRouterRepository(tmp_path / "empty"))
    with pytest.raises(RouterServiceError) as no_connection:
        empty_service.save_policy(
            RouterPolicy(
                tenant_id="local",
                engine="native_canary",
                default_mode="auto",
                canary_percent=10,
                compression_mode="auto",
            )
        )
    assert no_connection.value.code == "native_connection_required"

    service = ModelRouterService(repository)
    connection = repository.create_connection("local", connection_payload())
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-07-27T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    service.save_policy(
        RouterPolicy(
            tenant_id="local",
            engine="native_canary",
            default_mode="auto",
            canary_percent=100,
            compression_mode="auto",
        )
    )
    with pytest.raises(RouterServiceError) as exc:
        service.save_policy(
            RouterPolicy(
                tenant_id="local",
                engine="native",
                default_mode="auto",
                canary_percent=100,
                compression_mode="auto",
            )
        )
    assert exc.value.code == "native_gate_not_met"

    monkeypatch.setenv("MODEL_ROUTER_ALLOW_NATIVE_OVERRIDE", "true")
    saved = service.save_policy(
        RouterPolicy(
            tenant_id="local",
            engine="native",
            default_mode="auto",
            canary_percent=100,
            compression_mode="auto",
        )
    )
    assert saved.engine == "native"
    monkeypatch.setenv("MODEL_ROUTER_ENGINE", "sidecar")
    assert service.get_policy().engine == "sidecar"


@pytest.mark.asyncio
async def test_connection_probe_returns_safe_actionable_results(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []

    def success(request: Request) -> Response:
        requests.append(request)
        return Response(
            200,
            json={"data": [{"id": "openai/gpt-5.6"}, {"id": "anthropic/opus-5"}]},
        )

    service = ModelRouterService(
        SQLiteRouterRepository(tmp_path),
        client_factory=lambda: httpx.AsyncClient(transport=MockTransport(success)),
    )
    result = await service.test_unsaved_connection(connection_payload())
    assert result.ok is True
    assert result.model_count == 2
    assert requests[0].url == "https://openrouter.ai/api/v1/models"
    assert requests[0].headers["authorization"] == "Bearer sk-test-secret-value"

    def unauthorized(_: Request) -> Response:
        return Response(401, text="upstream secret diagnostics must not leak")

    service = ModelRouterService(
        SQLiteRouterRepository(tmp_path / "unauthorized"),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(unauthorized)
        ),
    )
    failed = await service.test_unsaved_connection(connection_payload())
    assert failed.ok is False
    assert "密钥无效" in failed.message
    assert "upstream secret diagnostics" not in failed.message


@pytest.mark.asyncio
async def test_connection_api_is_redacted_and_records_health(tmp_path: Path) -> None:
    def success(_: Request) -> Response:
        return Response(200, json={"data": [{"id": "model-a"}]})

    repository = SQLiteRouterRepository(tmp_path)
    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(transport=MockTransport(success)),
    )
    configure_model_router(service)
    app = FastAPI()
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        created_response = await client.post(
            "/api/router/connections",
            json=connection_payload().model_dump(mode="json"),
        )
        assert created_response.status_code == 201
        created = created_response.json()
        serialized = json.dumps(created, ensure_ascii=False)
        assert "sk-test-secret-value" not in serialized
        assert "api_key_ciphertext" not in serialized

        tested_response = await client.post(
            f"/api/router/connections/{created['id']}/test"
        )
        assert tested_response.status_code == 200
        assert tested_response.json()["model_count"] == 1

        status_response = await client.get("/api/router/status")
        status = status_response.json()
        assert status["tenant_id"] == "local"
        assert status["online_connection_count"] == 1
        assert status["ready"] is True


def test_behavior_baselines_are_pinned_and_non_vendored() -> None:
    root = Path(__file__).resolve().parents[1]
    routing = json.loads(
        (root / "model_router/fixtures/omniroute-v3.8.49-routing.json").read_text(
            encoding="utf-8"
        )
    )
    compression = json.loads(
        (
            root / "context_engine/fixtures/compression-baseline-v1.json"
        ).read_text(encoding="utf-8")
    )

    assert routing["source"]["commit"] == "36f8fd10052f"
    assert routing["source"]["source_code_copied"] is False
    assert sum(routing["score_factors"].values()) == pytest.approx(1.0)
    assert routing["public_modes"] == [
        "auto",
        "fast",
        "quality",
        "cheap",
        "reliable",
        "offline",
    ]
    assert compression["auto_trigger_ratio"] == 0.8
    assert "latest_user_message" in compression["protected_content"]


def test_model_router_imports_in_flat_docker_layout() -> None:
    server_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import model_router; import context_engine"],
        cwd=server_root,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_native_catalog_uses_30_second_cache_and_stale_if_error(
    tmp_path: Path,
) -> None:
    call_count = 0

    def handler(_: Request) -> Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(200, json={"data": [{"id": "provider/model-a"}]})
        return Response(503, text="internal upstream details")

    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection("local", connection_payload())
    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(transport=MockTransport(handler)),
    )

    class FallbackCatalog:
        async def get_catalog(self) -> ModelCatalogResponse:
            return ModelCatalogResponse(
                source="bundled",
                router_status="offline",
                stale=False,
                synced_at=None,
                catalog_version="test",
                models=[],
                routes=[],
            )

    catalog_service = NativeCatalogService()
    fresh = await catalog_service.get_catalog(
        service, FallbackCatalog()  # type: ignore[arg-type]
    )
    assert fresh.source == "native"
    assert fresh.models[0].connection_id == connection.id
    assert fresh.models[0].invocation_id == "provider/model-a"

    cached = await catalog_service.get_catalog(
        service, FallbackCatalog()  # type: ignore[arg-type]
    )
    assert cached.router_status == "online"
    assert call_count == 1

    assert catalog_service._cache is not None
    catalog_service._cache.stored_at -= 31
    stale = await catalog_service.get_catalog(
        service, FallbackCatalog()  # type: ignore[arg-type]
    )
    assert stale.stale is True
    assert stale.router_status == "stale"
    assert stale.models[0].availability == "degraded"
