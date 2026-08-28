from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.model_router.multimodal_control import PROVIDER_MULTIMODAL_PROTOCOL_VERSION
from server.model_router.repository import RouterRepositoryError, SQLiteRouterRepository


def claim_session(repository: SQLiteRouterRepository) -> None:
    repository.claim_multimodal_certification_session(
        "local",
        session_id="session-1",
        certification_id="cert-1",
        connection_id="connection-1",
        requested_model="test/video",
        execution_shape="video_generation_async",
        adapter_contract="openrouter_video_jobs_v1",
        protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
        idempotency_key_hash="test-idempotency-hash",
    )


def dispatch(repository: SQLiteRouterRepository) -> None:
    repository.update_multimodal_certification_session(
        "local", "session-1", status="running",
        provider_dispatch_state="dispatched", post_dispatched=True,
    )


def test_multimodal_dispatch_claim_is_one_shot_across_repository_instances(
    tmp_path: Path,
) -> None:
    repositories = [
        SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
        for _ in range(2)
    ]
    claim_session(repositories[0])

    def attempt(repository: SQLiteRouterRepository) -> str:
        try:
            dispatch(repository)
            return "dispatched"
        except RouterRepositoryError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, repositories))
    assert results.count("dispatched") == 1
    assert results.count("provider_multimodal_dispatch_already_claimed") == 1


def test_known_async_operation_survives_restart_for_polling_only(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    claim_session(repository)
    dispatch(repository)
    repository.update_multimodal_certification_session(
        "local", "session-1", status="running",
        provider_dispatch_state="confirmed", post_dispatched=True,
        upstream_operation_id="upstream-job-1",
    )
    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    session = restarted.get_multimodal_certification_session(
        "local", session_id="session-1"
    )
    assert session is not None
    assert session["status"] == "running"
    assert session["provider_dispatch_state"] == "confirmed"
    assert session["upstream_operation_id"] == "upstream-job-1"
    assert session["completed_at"] is None
    with pytest.raises(RouterRepositoryError):
        dispatch(restarted)
    completed = restarted.update_multimodal_certification_session(
        "local", "session-1", status="passed",
        provider_dispatch_state="confirmed", post_dispatched=True,
        upstream_operation_id="upstream-job-1",
        increment_poll_count=True, completed=True,
    )
    assert completed["poll_count"] == 1
    assert completed["status"] == "passed"


def test_multimodal_session_cannot_succeed_without_dispatch(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    claim_session(repository)
    with pytest.raises(RouterRepositoryError):
        repository.update_multimodal_certification_session(
            "local", "session-1", status="passed",
            provider_dispatch_state="confirmed", post_dispatched=False,
            completed=True,
        )


def test_restart_marks_dispatched_multimodal_call_uncertain(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.claim_workload_run(
        "local", run_id="run-1", entry_id="chat_image",
        policy_fingerprint="test-policy-fingerprint",
    )
    repository.claim_workload_call(
        "local", call_id="call-1", run_id="run-1", entry_id="chat_image",
        execution_shape="chat_image_stream", requested_model="test/image",
        connection_id="connection-1", certification_id="cert-1",
        connection_fingerprint="test-fingerprint", logical_call_key_hash="key-1",
        call_sequence=1, adapter_contract="openrouter_chat_multimodal_v1",
        protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE provider_workload_calls SET dispatched = 1, "
            "provider_dispatch_state = 'dispatched' "
            "WHERE tenant_id = 'local' AND id = 'call-1'"
        )
    SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    with sqlite3.connect(repository.database_path) as connection:
        row = connection.execute(
            "SELECT status, provider_dispatch_state FROM provider_workload_calls "
            "WHERE tenant_id = 'local' AND id = 'call-1'"
        ).fetchone()
    assert row == ("uncertain", "uncertain")
