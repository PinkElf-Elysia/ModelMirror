from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.model_router.chat_canary import (
    PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
    ProviderChatCanaryService,
    ProviderChatCanaryStreamEvidence,
)
from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate
from server.model_router.service import ModelRouterService


def _repository(tmp_path: Path) -> tuple[SQLiteRouterRepository, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
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
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-20T00:00:00+00:00",
    )
    return repository, connection.id


def _pass_certification(
    repository: SQLiteRouterRepository,
    connection_id: str,
    *,
    certification_id: str = "cert-1",
    model_id: str = "provider/model",
) -> dict[str, object]:
    row, _ = repository.claim_chat_certification(
        "local",
        certification_id=certification_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
        requested_model=model_id,
        idempotency_key_hash=hashlib.sha256(certification_id.encode()).hexdigest(),
    )
    return repository.complete_chat_certification(
        "local",
        str(row["id"]),
        status="passed",
        checks={"terminal_observed": True},
        warning_codes=[],
    )


def _complete_canary_run(
    repository: SQLiteRouterRepository,
    connection_id: str,
    certification: dict[str, object],
    *,
    run_id: str,
    result_class: str,
    status: str,
    error_code: str | None = None,
) -> None:
    repository.claim_chat_canary_run(
        "local",
        run_id=run_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        certification_id=str(certification["id"]),
        contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
        requested_model="provider/model",
        session_id_hash=f"hash-{run_id}",
        baseline_overlap=False,
    )
    repository.mark_chat_canary_dispatched("local", run_id)
    repository.complete_chat_canary_run(
        "local",
        run_id,
        status=status,
        result_class=result_class,
        checks={"terminal_observed": status == "succeeded"},
        warning_codes=[],
        error_code=error_code,
        ttft_ms=100.0,
        e2e_ms=200.0,
        total_tokens=4,
    )


def test_v12_to_v13_is_additive_and_preserves_round2_rows(tmp_path: Path) -> None:
    repository, connection_id = _repository(tmp_path)
    _pass_certification(repository, connection_id)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("PRAGMA user_version = 12")

    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    with sqlite3.connect(restarted.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "provider_chat_certifications",
        "provider_chat_canary_policies",
        "provider_chat_canary_runs",
    }.issubset(tables)
    assert restarted.get_connection("local", connection_id).name == "newAPI"
    assert restarted.get_latest_chat_certification(
        "local", connection_id, "provider/model"
    )["status"] == "passed"


def test_policy_and_runs_are_tenant_scoped_and_store_no_payload(tmp_path: Path) -> None:
    repository, connection_id = _repository(tmp_path)
    certification = _pass_certification(repository, connection_id)
    fingerprint = repository.connection_config_fingerprint("local", connection_id)
    repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=True
    )
    row = repository.claim_chat_canary_run(
        "local",
        run_id="run-1",
        connection_id=connection_id,
        connection_fingerprint=fingerprint,
        certification_id=str(certification["id"]),
        contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
        requested_model="provider/model",
        session_id_hash="one-way-session-hash",
        baseline_overlap=False,
    )
    repository.mark_chat_canary_dispatched("local", str(row["id"]))
    repository.complete_chat_canary_run(
        "local",
        str(row["id"]),
        status="succeeded",
        result_class="success",
        checks={"terminal_observed": True},
        warning_codes=[],
        total_tokens=4,
    )

    assert repository.get_chat_canary_policy("other") is None
    assert repository.list_chat_canary_runs("other") == []
    with sqlite3.connect(repository.database_path) as connection:
        columns = {
            item[1]
            for item in connection.execute(
                "PRAGMA table_info(provider_chat_canary_runs)"
            ).fetchall()
        }
        stored = connection.execute(
            "SELECT session_id_hash, dispatched FROM provider_chat_canary_runs"
        ).fetchone()
    assert not {
        "prompt",
        "messages",
        "response",
        "body",
        "api_key",
        "cookie",
        "csrf",
    }.intersection(columns)
    assert stored == ("one-way-session-hash", 1)


def test_restart_marks_running_uncertain_without_replay(tmp_path: Path) -> None:
    repository, connection_id = _repository(tmp_path)
    certification = _pass_certification(repository, connection_id)
    repository.claim_chat_canary_run(
        "local",
        run_id="run-running",
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        certification_id=str(certification["id"]),
        contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
        requested_model="provider/model",
        session_id_hash="hash",
        baseline_overlap=False,
    )

    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    row = restarted.list_chat_canary_runs("local")[0]

    assert row["status"] == "uncertain"
    assert row["result_class"] == "uncertain"
    assert row["error_code"] == "server_restarted"


def test_exact_model_certification_and_new_pass_reset_pause(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    repository, connection_id = _repository(tmp_path)
    certification = _pass_certification(repository, connection_id)
    repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=True
    )
    service = ProviderChatCanaryService(ModelRouterService(repository))

    assert service.public_status("other/model").reason_code == "certification_required"
    for index in range(3):
        run_id = f"run-transient-{index}"
        repository.claim_chat_canary_run(
            "local",
            run_id=run_id,
            connection_id=connection_id,
            connection_fingerprint=repository.connection_config_fingerprint(
                "local", connection_id
            ),
            certification_id=str(certification["id"]),
            contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
            requested_model="provider/model",
            session_id_hash=f"hash-{index}",
            baseline_overlap=False,
        )
        repository.mark_chat_canary_dispatched("local", run_id)
        repository.complete_chat_canary_run(
            "local",
            run_id,
            status="failed",
            result_class="transient_failure",
            checks={},
            warning_codes=[],
            error_code="provider_chat_http_503",
        )
    paused = service.public_status("provider/model")
    assert paused.available is False
    assert paused.reason_code == "automatically_paused"

    _pass_certification(
        repository,
        connection_id,
        certification_id="cert-2",
    )
    reset = service.public_status("provider/model")
    assert reset.available is True
    assert reset.reason_code == "available"


def test_client_cancel_is_recorded_without_pausing_the_model(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    repository, connection_id = _repository(tmp_path)
    certification = _pass_certification(repository, connection_id)
    repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=True
    )
    run_id = "run-client-cancelled"
    repository.claim_chat_canary_run(
        "local",
        run_id=run_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        certification_id=str(certification["id"]),
        contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
        requested_model="provider/model",
        session_id_hash="cancel-session-hash",
        baseline_overlap=False,
    )
    repository.mark_chat_canary_dispatched("local", run_id)
    evidence = ProviderChatCanaryStreamEvidence(started_at=0.0)
    status, result_class, error_code, checks, warnings = evidence.finish(
        transport_completed=False,
        transport_error_code="provider_chat_client_cancelled",
    )
    repository.complete_chat_canary_run(
        "local",
        run_id,
        status=status,
        result_class=result_class,
        checks=checks,
        warning_codes=warnings,
        error_code=error_code,
    )

    assert (status, result_class, error_code) == (
        "cancelled",
        "client_cancelled",
        "provider_chat_client_cancelled",
    )
    public = ProviderChatCanaryService(
        ModelRouterService(repository)
    ).public_status("provider/model")
    assert public.available is True
    assert public.reason_code == "available"


def test_expired_or_invalid_certification_ttl_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setenv(
        "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS", "3600"
    )
    repository, connection_id = _repository(tmp_path)
    certification = _pass_certification(repository, connection_id)
    expired_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE provider_chat_certifications "
            "SET completed_at = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
            (expired_at, expired_at, "local", certification["id"]),
        )
    repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=True
    )
    service = ProviderChatCanaryService(ModelRouterService(repository))

    expired = service.public_status("provider/model")
    assert expired.available is False
    assert expired.reason_code == "certification_expired"

    monkeypatch.setenv(
        "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS", "invalid"
    )
    invalid = service.public_status("provider/model")
    assert invalid.available is False
    assert invalid.reason_code == "certification_ttl_invalid"


def test_admin_evidence_marks_old_fingerprint_stale_and_aggregates_current_window(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    repository, connection_id = _repository(tmp_path)
    old_certification = _pass_certification(repository, connection_id)
    _complete_canary_run(
        repository,
        connection_id,
        old_certification,
        run_id="run-old",
        status="succeeded",
        result_class="success",
    )

    repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(api_key="rotated-secret-key"),
    )
    repository.save_test_result(
        "local",
        connection_id,
        health="online",
        model_count=1,
        checked_at=datetime.now(UTC).isoformat(),
    )
    current_certification = _pass_certification(
        repository,
        connection_id,
        certification_id="cert-current",
    )
    _complete_canary_run(
        repository,
        connection_id,
        current_certification,
        run_id="run-current",
        status="failed",
        result_class="transient_failure",
        error_code="provider_chat_http_503",
    )
    repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=True
    )

    admin = ProviderChatCanaryService(
        ModelRouterService(repository)
    ).admin_status()
    runs = {run.run_id: run for run in admin.runs}

    assert runs["run-current"].current_evidence is True
    assert runs["run-current"].stale_reason is None
    assert runs["run-old"].current_evidence is False
    assert runs["run-old"].stale_reason == "connection_fingerprint_changed"
    assert len(admin.aggregates) == 1
    aggregate = admin.aggregates[0]
    assert aggregate.connection_id == connection_id
    assert aggregate.model_id == "provider/model"
    assert aggregate.total_runs == 1
    assert aggregate.succeeded_runs == 0
    assert aggregate.transient_failure_runs == 1
    assert aggregate.success_rate == 0.0
    assert aggregate.average_ttft_ms == 100.0
    assert aggregate.average_e2e_ms == 200.0
    assert aggregate.total_tokens == 4
