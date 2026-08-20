from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from server.model_router.repository import (
    RouterRepositoryError,
    SQLiteRouterRepository,
)
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate


def _connection(repository: SQLiteRouterRepository, *, tenant: str = "local") -> str:
    return repository.create_connection(
        tenant,
        RouterConnectionCreate(
            name="newAPI",
            kind="newapi",
            base_url="https://newapi.example/v1",
            api_key="secret-key",
            scopes=["chat"],
        ),
    ).id


def _claim(
    repository: SQLiteRouterRepository,
    connection_id: str,
    *,
    certification_id: str = "cert-1",
    key: str = "idempotency-1",
) -> tuple[dict[str, object], bool]:
    return repository.claim_chat_certification(
        "local",
        certification_id=certification_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
        requested_model="provider/model",
        idempotency_key_hash=hashlib.sha256(key.encode()).hexdigest(),
    )


def test_v11_to_v12_is_additive_and_preserves_connection(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE router_connections (
                id TEXT NOT NULL, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
                kind TEXT NOT NULL, base_url TEXT NOT NULL, masked_key TEXT NOT NULL,
                api_key_ciphertext TEXT NOT NULL, scopes_json TEXT NOT NULL DEFAULT '["chat"]',
                enabled INTEGER NOT NULL DEFAULT 1, health TEXT NOT NULL DEFAULT 'untested',
                model_count INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT,
                last_error_code TEXT, last_error_hint TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY (tenant_id, id)
            );
            PRAGMA user_version = 11;
            """
        )

    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("provider_chat_certifications",),
        ).fetchone()
    assert table == ("provider_chat_certifications",)
    assert repository.list_connections("local") == []


def test_idempotency_and_single_running_claim_are_atomic(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)

    first, created = _claim(repository, connection_id)
    repeated, repeated_created = _claim(repository, connection_id)

    assert created is True
    assert repeated_created is False
    assert repeated["id"] == first["id"]
    with pytest.raises(
        RouterRepositoryError, match="provider_chat_certification_already_running"
    ):
        _claim(
            repository,
            connection_id,
            certification_id="cert-2",
            key="idempotency-2",
        )


def test_restart_marks_running_uncertain_without_replay(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    _claim(repository, connection_id)

    restarted = SQLiteRouterRepository(tmp_path)
    records = restarted.list_chat_certifications("local")

    assert records[0]["status"] == "uncertain"
    assert records[0]["error_code"] == "server_restarted"


def test_config_fingerprint_ignores_name_health_and_enabled_but_tracks_contract(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    original = repository.connection_config_fingerprint("local", connection_id)

    repository.update_connection(
        "local", connection_id, RouterConnectionUpdate(name="renamed", enabled=False)
    )
    assert repository.connection_config_fingerprint("local", connection_id) == original

    repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(
            base_url="https://other.example/v1", api_key="rotated-secret"
        ),
    )
    assert repository.connection_config_fingerprint("local", connection_id) != original


def test_certification_rows_are_tenant_scoped_and_contain_no_payload(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection_id = _connection(repository)
    row, _ = _claim(repository, connection_id)
    completed = repository.complete_chat_certification(
        "local",
        str(row["id"]),
        status="passed",
        checks={"catalog_ok": True, "text_delta_observed": True},
        warning_codes=[],
        actual_model="provider/model",
        ttft_ms=10.5,
        e2e_ms=20.5,
        total_tokens=3,
    )

    assert completed["status"] == "passed"
    assert repository.list_chat_certifications("other") == []
    with sqlite3.connect(repository.database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(provider_chat_certifications)"
            ).fetchall()
        }
    assert not {"prompt", "response", "api_key", "body"}.intersection(columns)
