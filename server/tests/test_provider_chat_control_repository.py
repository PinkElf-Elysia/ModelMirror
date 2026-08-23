from __future__ import annotations

import sqlite3
import hashlib
from datetime import UTC, datetime, timedelta
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


def test_r5d_open_gate_epoch_is_normalized_without_losing_evidence(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO provider_chat_gate_epochs (
                id, tenant_id, policy_fingerprint, status, started_at
            ) VALUES ('legacy-open', 'local', 'policy-open', 'open',
                      '2026-08-01T00:00:00+00:00')
            """
        )

    normalized = repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="must-not-replace",
        policy_fingerprint="policy-open",
        qualified=True,
        invalidation_code="policy_changed",
    )

    assert normalized is not None
    assert normalized["id"] == "legacy-open"
    assert normalized["status"] == "collecting"
    latest = repository.get_latest_chat_control_gate_epoch("local", "policy-open")
    assert latest is not None and latest["id"] == "legacy-open"


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


def _seed_gate_requests(
    repository: SQLiteRouterRepository,
    *,
    epoch_id: str,
    count: int = 500,
    model_id: str = "provider/model",
) -> None:
    started_at = datetime(2026, 8, 1, tzinfo=UTC)
    runs = []
    attempts = []
    for index in range(count):
        observed_at = started_at + timedelta(
            seconds=(14 * 24 * 60 * 60 * index / max(1, count - 1))
        )
        timestamp = observed_at.isoformat()
        run_id = f"gate-run-{index}"
        runs.append(
            (
                run_id,
                "local",
                "policy-1",
                epoch_id,
                "chat_text",
                model_id,
                model_id,
                "newapi_preferred",
                "default",
                "succeeded",
                "success",
                "[]",
                1,
                1,
                0,
                0,
                timestamp,
                timestamp,
                timestamp,
            )
        )
        attempts.append(
            (
                f"gate-attempt-{index}",
                "local",
                run_id,
                "chat_text",
                0,
                "conn-newapi",
                "newapi",
                1,
                "succeeded",
                "success",
                model_id,
                timestamp,
                timestamp,
                timestamp,
            )
        )
    with sqlite3.connect(repository.database_path) as connection:
        connection.executemany(
            """
            INSERT INTO provider_chat_runs (
                id, tenant_id, policy_fingerprint, epoch_id, capability,
                requested_model, actual_model, strategy, gateway, status,
                result_class, reason_codes_json, is_real_user, primary_newapi,
                client_cancelled, hard_failure, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            runs,
        )
        connection.executemany(
            """
            INSERT INTO provider_chat_attempts (
                id, tenant_id, run_id, capability, position, connection_id,
                provider_kind, dispatched, status, result_class, actual_model,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            attempts,
        )


def test_r5e_gate_aggregates_only_primary_real_text_and_activates_atomically(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.replace_chat_control_policy(
        "local",
        expected_revision=0,
        mode="newapi_preferred",
        auto_enabled=False,
        policy_fingerprint="policy-1",
        stable_model_ids=["provider/model"],
        routes=[],
        qualifications=[],
    )
    epoch = repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-1",
        policy_fingerprint="policy-1",
        qualified=True,
        invalidation_code="policy_changed",
    )
    assert epoch is not None
    _seed_gate_requests(repository, epoch_id="epoch-1")

    summary = repository.summarize_chat_control_gate("local", epoch_id="epoch-1")
    assert summary["request_count"] == 500
    assert summary["success_count"] == 500
    assert summary["hard_failure_count"] == 0
    assert summary["observed_days"] == pytest.approx(14.0)
    assert summary["model_successes"] == {"provider/model": 500}

    drills = {
        "auth_failure": True,
        "http_429": True,
        "http_5xx": True,
        "connect_timeout": True,
        "read_timeout": True,
        "empty_stream": True,
        "invalid_sse": True,
        "stream_interrupted": True,
        "service_restart": True,
        "credential_invalid": True,
        "data_plane_offline": True,
        "preferred_fallback": True,
    }
    repository.activate_chat_control_required(
        "local",
        expected_revision=1,
        policy_fingerprint="policy-1",
        epoch_id="epoch-1",
        no_open_p0_p1=True,
        drills=drills,
        acknowledge_fail_closed=True,
        correlation_hash=hashlib.sha256(b"opaque-newapi-log").hexdigest(),
        evidence_checks={
            "newapi_quota_decrement": True,
            "newapi_usage_log": True,
            "newapi_restart_persistence": True,
        },
    )

    policy = repository.get_chat_control_policy_bundle("local")["policy"]
    assert policy["mode"] == "newapi_required_default"
    assert policy["revision"] == 2
    active = repository.get_open_chat_control_gate_epoch("local", "policy-1")
    assert active is not None and active["status"] == "active"
    approval = repository.get_chat_control_gate_approval(
        "local", policy_fingerprint="policy-1"
    )
    assert approval is not None and approval["acknowledge_fail_closed"] is True
    evidence = repository.list_chat_control_acceptance_evidence(
        "local", policy_fingerprint="policy-1", epoch_id="epoch-1"
    )
    assert {item["evidence_kind"] for item in evidence} == {
        "newapi_quota_decrement",
        "newapi_usage_log",
        "newapi_restart_persistence",
    }
    with sqlite3.connect(repository.database_path) as connection:
        dump = "\n".join(connection.iterdump())
    assert "opaque-newapi-log" not in dump


def test_hard_failure_degrades_epoch_revokes_approval_and_requires_new_policy(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-hard",
        policy_fingerprint="policy-hard",
        qualified=True,
        invalidation_code="policy_changed",
    )
    repository.claim_chat_control_run(
        "local",
        run_id="run-hard",
        policy_fingerprint="policy-hard",
        capability="chat_text",
        requested_model="provider/model",
        strategy="newapi_required_default",
        epoch_id="epoch-hard",
        is_real_user=True,
        primary_newapi=True,
    )
    repository.claim_chat_control_attempt(
        "local",
        attempt_id="attempt-hard",
        run_id="run-hard",
        capability="chat_text",
        position=0,
        connection_id="conn-newapi",
        provider_kind="newapi",
    )
    repository.mark_chat_control_attempt_dispatched("local", "attempt-hard")
    repository.complete_chat_control_attempt(
        "local",
        "attempt-hard",
        status="failed",
        result_class="hard_failure",
        error_code="provider_chat_empty_stream",
    )
    repository.complete_chat_control_run(
        "local",
        "run-hard",
        status="failed",
        result_class="hard_failure",
        reason_codes=["provider_chat_empty_stream"],
        hard_failure=True,
    )

    latest = repository.get_latest_chat_control_gate_epoch(
        "local", "policy-hard"
    )
    assert latest is not None
    assert latest["status"] == "degraded"
    assert latest["hard_failure_code"] == "provider_chat_empty_stream"
    assert repository.get_open_chat_control_gate_epoch("local", "policy-hard") is None
    assert repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-must-not-open",
        policy_fingerprint="policy-hard",
        qualified=True,
        invalidation_code="qualification_changed",
    ) is None
    hard_failure = repository.get_latest_chat_control_hard_failure(
        "local",
        connection_id="conn-newapi",
        model_id="provider/model",
        capability="chat_text",
    )
    assert hard_failure is not None


def test_gate_summary_sees_hard_attempt_before_parent_run_finishes(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.sync_chat_control_gate_epoch(
        "local",
        epoch_id="epoch-race",
        policy_fingerprint="policy-race",
        qualified=True,
        invalidation_code="policy_changed",
    )
    repository.claim_chat_control_run(
        "local",
        run_id="run-race",
        policy_fingerprint="policy-race",
        capability="chat_text",
        requested_model="provider/model",
        strategy="newapi_preferred",
        epoch_id="epoch-race",
        is_real_user=True,
        primary_newapi=True,
    )
    repository.claim_chat_control_attempt(
        "local",
        attempt_id="attempt-race",
        run_id="run-race",
        capability="chat_text",
        position=0,
        connection_id="conn-newapi",
        provider_kind="newapi",
    )
    repository.mark_chat_control_attempt_dispatched("local", "attempt-race")
    repository.complete_chat_control_attempt(
        "local",
        "attempt-race",
        status="failed",
        result_class="hard_failure",
        error_code="provider_chat_empty_stream",
    )

    summary = repository.summarize_chat_control_gate(
        "local", epoch_id="epoch-race"
    )
    assert summary["request_count"] == 1
    assert summary["success_count"] == 0
    assert summary["hard_failure_count"] == 1
