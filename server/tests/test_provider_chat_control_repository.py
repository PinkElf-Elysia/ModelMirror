from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.model_router.repository import (
    SCHEMA_VERSION,
    RouterRepositoryError,
    SQLiteRouterRepository,
)


V15_TABLES = {
    "provider_chat_stable_policies",
    "provider_chat_capability_routes",
    "provider_chat_model_qualifications",
    "provider_chat_runs",
    "provider_chat_attempts",
    "provider_chat_gate_epochs",
    "provider_chat_gate_approvals",
    "provider_chat_acceptance_evidence",
}


def test_v14_to_v15_is_additive_and_defaults_existing_certification_to_text(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE provider_chat_certifications (
                id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                connection_id TEXT NOT NULL, connection_fingerprint TEXT NOT NULL,
                contract_version TEXT NOT NULL, requested_model TEXT NOT NULL,
                actual_model TEXT, idempotency_key_hash TEXT NOT NULL,
                status TEXT NOT NULL, checks_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL DEFAULT '[]', error_code TEXT,
                ttft_ms REAL, e2e_ms REAL, prompt_tokens INTEGER,
                completion_tokens INTEGER, total_tokens INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_at TEXT, PRIMARY KEY (tenant_id, id),
                UNIQUE (tenant_id, connection_id, idempotency_key_hash)
            );
            INSERT INTO provider_chat_certifications (
                id, tenant_id, connection_id, connection_fingerprint,
                contract_version, requested_model, idempotency_key_hash,
                status, created_at, updated_at
            ) VALUES (
                'cert-old', 'local', 'conn-old', 'fingerprint',
                'modelmirror-provider-chat-v1', 'provider/model', 'hash',
                'passed', '2026-08-20T00:00:00+00:00',
                '2026-08-20T00:00:00+00:00'
            );
            PRAGMA user_version = 14;
            """
        )

    SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert V15_TABLES <= tables
        assert connection.execute(
            "SELECT capability FROM provider_chat_certifications WHERE id = 'cert-old'"
        ).fetchone()[0] == "chat_text"
    assert SCHEMA_VERSION == 15


def test_policy_replace_is_atomic_revisioned_and_tenant_scoped(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    saved = repository.replace_chat_control_policy(
        "local",
        expected_revision=0,
        mode="legacy",
        auto_enabled=False,
        policy_fingerprint="fingerprint-local",
        stable_model_ids=["provider/model"],
        routes=[
            {
                "capability": "chat_text",
                "position": 0,
                "connection_id": "conn-local",
            }
        ],
        qualifications=[
            {
                "capability": "chat_text",
                "connection_id": "conn-local",
                "model_id": "provider/model",
                "certification_id": "cert-local",
                "connection_fingerprint": "connection-fingerprint",
                "contract_version": "modelmirror-provider-chat-v1",
            }
        ],
    )
    assert saved["policy"]["revision"] == 1
    assert repository.get_chat_control_policy_bundle("other")["policy"] is None

    with pytest.raises(RouterRepositoryError) as exc_info:
        repository.replace_chat_control_policy(
            "local",
            expected_revision=0,
            mode="legacy",
            auto_enabled=False,
            policy_fingerprint="stale-write",
            stable_model_ids=[],
            routes=[],
            qualifications=[],
        )
    assert str(exc_info.value) == "provider_chat_policy_revision_conflict"
    current = repository.get_chat_control_policy_bundle("local")
    assert current["policy"]["policy_fingerprint"] == "fingerprint-local"
    assert len(current["routes"]) == 1
    assert len(current["qualifications"]) == 1


def test_running_receipts_become_uncertain_and_cleanup_is_dry_run_by_default(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.claim_chat_control_run(
        "local",
        run_id="run-1",
        policy_fingerprint="policy",
        capability="chat_text",
        requested_model="provider/model",
        strategy="explicit_session",
    )
    repository.claim_chat_control_attempt(
        "local",
        attempt_id="attempt-1",
        run_id="run-1",
        capability="chat_text",
        position=0,
        connection_id="conn-1",
        provider_kind="newapi",
    )

    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    receipts = restarted.list_chat_control_receipts("local")
    assert receipts["runs"][0]["status"] == "uncertain"
    assert receipts["attempts"][0]["status"] == "uncertain"

    with sqlite3.connect(restarted.database_path) as connection:
        connection.execute(
            "UPDATE provider_chat_runs SET completed_at = ?, updated_at = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "UPDATE provider_chat_attempts SET completed_at = ?, updated_at = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )

    dry_run = restarted.cleanup_chat_control_receipts(
        "local", before="2021-01-01T00:00:00+00:00"
    )
    assert dry_run == {
        "applied": False,
        "before": "2021-01-01T00:00:00+00:00",
        "runs": 1,
        "attempts": 1,
    }
    assert len(restarted.list_chat_control_receipts("local")["runs"]) == 1
    applied = restarted.cleanup_chat_control_receipts(
        "local", before="2021-01-01T00:00:00+00:00", apply=True
    )
    assert applied["applied"] is True
    assert restarted.list_chat_control_receipts("local")["runs"] == []


def test_receipts_are_cursor_paginated_without_cross_tenant_access(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    for run_id in ("run-a", "run-b", "run-c"):
        repository.claim_chat_control_run(
            "local",
            run_id=run_id,
            policy_fingerprint="policy",
            capability="chat_text",
            requested_model="provider/model",
            strategy="explicit_session",
        )
        repository.complete_chat_control_run("local", run_id, status="succeeded")

    first = repository.list_chat_control_receipts("local", limit=2)
    assert len(first["runs"]) == 2
    assert first["next_cursor"] is not None
    second = repository.list_chat_control_receipts(
        "local", limit=2, cursor=str(first["next_cursor"])
    )
    assert len(second["runs"]) == 1
    assert second["next_cursor"] is None
    assert {row["id"] for row in first["runs"]}.isdisjoint(
        {row["id"] for row in second["runs"]}
    )
    with pytest.raises(RouterRepositoryError) as exc_info:
        repository.list_chat_control_receipts(
            "other", cursor=str(first["next_cursor"])
        )
    assert str(exc_info.value) == "provider_chat_receipt_cursor_invalid"


def test_gate_epoch_is_reused_then_invalidated_with_approvals(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    first = repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-1",
        policy_fingerprint="policy-1",
        qualified=True,
        invalidation_code="policy_changed",
    )
    assert first is not None
    reused = repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-unused",
        policy_fingerprint="policy-1",
        qualified=True,
        invalidation_code="policy_changed",
    )
    assert reused is not None and reused["id"] == "epoch-1"
    repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-unused",
        policy_fingerprint="policy-1",
        qualified=False,
        invalidation_code="qualification_stale",
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.row_factory = sqlite3.Row
        closed = connection.execute(
            "SELECT * FROM provider_chat_gate_epochs WHERE tenant_id = 'local'"
        ).fetchone()
    assert closed["status"] == "invalidated"
    assert closed["hard_failure_code"] == "qualification_stale"
    assert closed["closed_at"] is not None


def test_receipt_schema_has_no_prompt_message_or_response_body_columns(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    with sqlite3.connect(repository.database_path) as connection:
        columns = {
            row[1]
            for table in (
                "provider_chat_runs",
                "provider_chat_attempts",
                "provider_chat_acceptance_evidence",
            )
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        evidence_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(provider_chat_acceptance_evidence)"
            )
        }
    assert not {"prompt", "messages", "model_output", "response_body"} & columns
    assert "epoch_id" in evidence_columns
