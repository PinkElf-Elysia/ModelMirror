from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.model_router.repository import (
    SCHEMA_VERSION,
    RouterRepositoryError,
    SQLiteRouterRepository,
)
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate


def _connection(repository: SQLiteRouterRepository, tenant_id: str = "local") -> str:
    return repository.create_connection(
        tenant_id,
        RouterConnectionCreate(
            name="provider",
            kind="newapi",
            base_url="https://provider.example/v1",
            api_key="secret",
            scopes=["chat"],
        ),
    ).id


def _claim(repository: SQLiteRouterRepository, connection_id: str, refresh_id: str) -> None:
    repository.claim_catalog_refresh(
        "local",
        refresh_id=refresh_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
    )


def _complete(
    repository: SQLiteRouterRepository,
    connection_id: str,
    refresh_id: str,
    model_ids: list[str],
    *,
    truncated: bool = False,
) -> None:
    repository.complete_catalog_refresh(
        "local",
        refresh_id,
        connection_id=connection_id,
        models=[
            {
                "model_id": model_id,
                "normalized_model_id": model_id.casefold(),
                "metadata": {"owned_by": "provider"},
                "capability_state": "capabilities_unclassified",
            }
            for model_id in model_ids
        ],
        offerings=[
            {
                "model_id": model_id,
                "operation": "chat",
                "access_mode": "managed",
                "capability_source": "connection_scope",
                "pricing_status": "unknown",
            }
            for model_id in model_ids
        ],
        model_count=len(model_ids),
        truncated=truncated,
        catalog_fingerprint=f"fingerprint-{refresh_id}",
        observed_at="2026-08-21T00:00:00+00:00",
    )


def test_v13_to_current_is_additive_and_creates_catalog_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 13")

    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "provider_catalog_refreshes",
        "provider_catalog_models",
        "provider_catalog_offerings",
    }.issubset(tables)
    assert repository.list_connections("local") == []


def test_refresh_claim_is_atomic_and_restart_marks_it_uncertain(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    _claim(repository, connection_id, "refresh-1")

    with pytest.raises(RouterRepositoryError, match="refresh_in_progress"):
        _claim(repository, connection_id, "refresh-2")

    restarted = SQLiteRouterRepository(tmp_path)
    rows = restarted.list_catalog_refreshes("local", connection_id=connection_id)
    assert rows[0]["status"] == "uncertain"
    assert rows[0]["error_code"] == "server_restarted"


def test_complete_refresh_retires_only_after_complete_snapshot(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    _claim(repository, connection_id, "refresh-1")
    _complete(repository, connection_id, "refresh-1", ["model-a", "model-b"])

    _claim(repository, connection_id, "refresh-2")
    _complete(
        repository,
        connection_id,
        "refresh-2",
        ["model-a"],
        truncated=True,
    )
    truncated = {
        row["model_id"]: row["status"]
        for row in repository.list_catalog_models(
            "local", connection_id=connection_id
        )
    }
    assert truncated == {"model-a": "active", "model-b": "stale"}

    _claim(repository, connection_id, "refresh-3")
    _complete(repository, connection_id, "refresh-3", ["model-a"])
    complete = {
        row["model_id"]: row["status"]
        for row in repository.list_catalog_models(
            "local", connection_id=connection_id
        )
    }
    assert complete == {"model-a": "active", "model-b": "retired"}
    connection = repository.get_connection("local", connection_id)
    assert connection.health == "online"
    assert connection.model_count == 1


def test_complete_refresh_rolls_back_inventory_and_health_together(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    _claim(repository, connection_id, "refresh-1")
    _complete(repository, connection_id, "refresh-1", ["model-a"])
    _claim(repository, connection_id, "refresh-2")

    with pytest.raises(KeyError):
        repository.complete_catalog_refresh(
            "local",
            "refresh-2",
            connection_id=connection_id,
            models=[{"model_id": "model-b"}],
            offerings=[{"operation": "chat"}],
            model_count=99,
            truncated=False,
            catalog_fingerprint="never-committed",
            observed_at="2026-08-21T01:00:00+00:00",
        )

    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert [(row["model_id"], row["status"]) for row in rows] == [("model-a", "active")]
    connection = repository.get_connection("local", connection_id)
    assert connection.health == "online"
    assert connection.model_count == 1


def test_failed_refresh_preserves_rows_as_stale_and_is_tenant_scoped(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    _claim(repository, connection_id, "refresh-1")
    _complete(repository, connection_id, "refresh-1", ["model-a"])
    _claim(repository, connection_id, "refresh-2")

    repository.fail_catalog_refresh(
        "local",
        "refresh-2",
        connection_id=connection_id,
        error_code="unreachable",
    )

    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert rows[0]["status"] == "stale"
    assert repository.list_catalog_models("other") == []
    offerings = repository.list_catalog_offerings(
        "local", connection_id=connection_id
    )
    assert offerings[0]["stale"] == 1


def test_catalog_schema_contains_no_payload_or_secret_columns(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    with sqlite3.connect(repository.database_path) as connection:
        columns = {
            row[1]
            for table in (
                "provider_catalog_refreshes",
                "provider_catalog_models",
                "provider_catalog_offerings",
            )
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
    assert not {
        "api_key",
        "base_url",
        "prompt",
        "response",
        "response_body",
        "credential",
    }.intersection(columns)


def test_provider_configuration_edit_invalidates_old_connection_health(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    repository.save_test_result(
        "local",
        connection_id,
        health="online",
        model_count=2,
        checked_at="2026-08-21T00:00:00+00:00",
    )

    updated = repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(base_url="https://changed.example/v1"),
    )

    assert updated.health == "untested"
    assert updated.model_count == 0
    assert updated.last_checked_at is None


def test_configuration_edit_with_enabled_field_clears_old_evidence(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    repository.save_test_result(
        "local",
        connection_id,
        health="online",
        model_count=2,
        checked_at="2026-08-21T00:00:00+00:00",
    )

    updated = repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(
            base_url="https://changed.example/v1",
            enabled=True,
        ),
    )

    assert updated.enabled is True
    assert updated.health == "untested"
    assert updated.model_count == 0
    assert updated.last_checked_at is None
