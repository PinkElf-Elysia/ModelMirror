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
from server.model_router.omniroute_parity import ALGORITHM_VERSION, CONFIG_HASH
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
    assert created.scopes == ["chat", "audio"]
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


def test_connection_scopes_default_by_provider_and_migrate_v7(
    tmp_path: Path,
) -> None:
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_database = legacy_dir / "router.sqlite3"
    with sqlite3.connect(legacy_database) as connection:
        connection.executescript(
            """
            CREATE TABLE router_connections (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                base_url TEXT NOT NULL,
                masked_key TEXT NOT NULL,
                api_key_ciphertext TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                health TEXT NOT NULL DEFAULT 'untested',
                model_count INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                last_error_code TEXT,
                last_error_hint TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, id)
            );
            PRAGMA user_version = 7;
            """
        )
        connection.execute(
            """
            INSERT INTO router_connections (
                id, tenant_id, name, kind, base_url, masked_key,
                api_key_ciphertext, enabled, health, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-openrouter",
                "local",
                "Legacy OpenRouter",
                "openrouter",
                "https://openrouter.ai/api/v1",
                "sk******cy",
                "not-used-by-this-test",
                1,
                "online",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )

    migrated = SQLiteRouterRepository(
        legacy_dir,
        master_key=b"migration-test-key",
    )
    assert migrated.get_connection(
        "local", "legacy-openrouter"
    ).scopes == ["chat", "audio"]

    direct_openai = migrated.create_connection(
        "local",
        connection_payload(
            kind="openai",
            name="OpenAI Audio",
            base_url="https://api.openai.com/v1",
        ),
    )
    assert direct_openai.scopes == ["audio", "realtime"]
    assert ModelRouterService(migrated).status().connection_count == 1


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
        algorithm_version=ALGORITHM_VERSION,
        config_hash=CONFIG_HASH,
        task_type="medium",
        task_level="standard",
        selection_kind="ranked",
        score_tier="top",
        planning_latency_ms=2,
        eligible_count=1,
        finalist_count=1,
    )
    repository.record_router_candidate_sample(
        "local",
        connection_id="connection-a",
        model_id="model-a",
        engine="native",
        algorithm_version=ALGORITHM_VERSION,
        config_hash=CONFIG_HASH,
        task_type="medium",
        success=True,
        outcome="success",
        ttft_ms=100,
        e2e_ms=500,
        output_tokens=100,
        tokens_per_second=250,
        planning_latency_ms=2,
    )
    repository.record_routing_decision(
        "local",
        session_id_hash="pending-session",
        engine="native",
        strategy="auto",
        connection_id="connection-a",
        model_id="model-a",
        reason_codes=["mode_auto"],
        algorithm_version=ALGORITHM_VERSION,
        config_hash=CONFIG_HASH,
        task_type="medium",
        task_level="standard",
        selection_kind="ranked",
        score_tier="top",
        planning_latency_ms=2,
        eligible_count=1,
        finalist_count=1,
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
    assert len(diagnostics["recent_decisions"]) == 2
    assert "private-model" not in json.dumps(diagnostics)
    assert any(
        decision["budget"]["status"] == "reserved"
        for decision in diagnostics["recent_decisions"]
    )

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


def test_gate_approval_is_version_bound_and_revocable(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    approval = repository.save_native_gate_approval(
        "local",
        algorithm_version=ALGORITHM_VERSION,
        config_hash=CONFIG_HASH,
        no_open_p0_p1=True,
        drills={
            "timeout": True,
            "http_429": True,
            "http_5xx": True,
            "empty_stream": True,
            "stream_interrupted": True,
            "strict_budget": True,
            "connection_disabled": True,
            "service_restart": True,
        },
    )
    assert approval["algorithm_version"] == ALGORITHM_VERSION
    assert approval["config_hash"] == CONFIG_HASH
    repository.revoke_native_gate_approval("local")
    assert repository.get_native_gate_approval("local") is None


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
        assert created["scopes"] == ["chat", "audio"]

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


def test_behavior_baselines_and_direct_port_provenance_are_pinned() -> None:
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
    assert routing["source"]["source_code_copied"] is True
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

    port_root = root / "model_router/omniroute_parity"
    manifest = json.loads(
        (port_root / "UPSTREAM_FILES.json").read_text(encoding="utf-8")
    )
    assert manifest["commit"] == (
        "36f8fd10052fd88f07e188b566f19a59c9cf5ea7"
    )
    assert manifest["release"] == "release/v3.8.49"
    assert len(manifest["files"]) == 8
    assert all(len(item["gitBlob"]) == 40 for item in manifest["files"])
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    assert all((port_root / item["local"]).is_file() for item in manifest["files"])
    assert "Copyright (c) 2026 diegosouzapw" in (
        port_root / "LICENSE"
    ).read_text(encoding="utf-8")


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
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": "provider/model-a",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                        },
                        {
                            "id": "provider/model-a:batch",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                        },
                    ]
                },
            )
        return Response(503, text="internal upstream details")

    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection("local", connection_payload())
    direct_openai = repository.create_connection(
        "local",
        connection_payload(
            kind="openai",
            name="OpenAI Audio",
            base_url="https://api.openai.com/v1",
        ),
    )
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
    assert fresh.models[0].input_modalities == ["text", "image"]
    assert fresh.models[0].operations == ["analyze_image", "chat"]
    assert all(not item.invocation_id.endswith(":batch") for item in fresh.models)
    assert all(
        item.connection_id != direct_openai.id for item in fresh.models
    )

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
