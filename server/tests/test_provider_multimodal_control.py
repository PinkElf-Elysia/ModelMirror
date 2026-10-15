from __future__ import annotations

import sqlite3
import hashlib
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.model_router.multimodal_control import PROVIDER_MULTIMODAL_PROTOCOL_VERSION
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.repository import RouterRepositoryError, SQLiteRouterRepository


def claim_session(repository: SQLiteRouterRepository) -> None:
    connection = repository.create_connection("local", RouterConnectionCreate(
        name="synthetic", kind="openrouter", base_url="https://provider.example/v1",
        api_key="synthetic-only", scopes=["video"],
    ))
    repository.save_test_result("local", connection.id, health="online", model_count=1,
        checked_at=datetime.now(UTC).isoformat())
    repository.claim_workload_certification(
        "local", certification_id="cert-1", connection_id=connection.id,
        connection_fingerprint=repository.connection_config_fingerprint("local", connection.id),
        contract_version="synthetic-v1",
        requested_model="test/video", execution_shape="video_generation_async",
        profile={}, profile_fingerprint=hashlib.sha256(b"{}").hexdigest(),
        idempotency_key_hash="test-idempotency-hash",
        adapter_contract="openrouter_video_jobs_v1",
        protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
    )
    repository.claim_multimodal_certification_session(
        "local",
        session_id="session-1",
        certification_id="cert-1",
        connection_id=connection.id,
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


def test_multimodal_dispatch_claim_is_one_shot_across_writer_threads(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    repositories = [repository, repository]
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


def test_multimodal_dispatch_claim_rejects_sequential_repeat(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    claim_session(repository)
    dispatch(repository)

    with pytest.raises(
        RouterRepositoryError,
        match="provider_multimodal_dispatch_already_claimed",
    ):
        dispatch(repository)


def test_known_async_operation_survives_restart_for_polling_only(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    claim_session(repository)
    dispatch(repository)
    certification = repository.get_workload_certification("local", "cert-1")
    # Use the same atomic pending-evidence write as the async adapter, not
    # an orphan session that has no corresponding certification state.
    repository.record_multimodal_certification_pending_evidence(
        "local", "session-1", upstream_operation_id="upstream-job-1",
        checks={name: True for name in ("http_ok", "content_observed", "response_complete",
            "multimodal_adapter_verified", "async_job_id_verified")},
        warning_codes=[], error_code="provider_video_certification_pending",
        ttft_ms=None, e2e_ms=None, prompt_tokens=None, completion_tokens=None, total_tokens=None,
        expected_connection_fingerprint=certification["connection_fingerprint"],
    )
    repository.close()
    restarted = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    session = restarted.get_multimodal_certification_session(
        "local", session_id="session-1"
    )
    assert session is not None
    assert session["status"] == "uncertain"
    assert session["provider_dispatch_state"] == "confirmed"
    assert session["upstream_operation_id"] == "upstream-job-1"
    assert session["completed_at"] is not None
    with pytest.raises(RouterRepositoryError):
        dispatch(restarted)
    _, resumed = restarted.claim_multimodal_certification_refresh(
        "local", "cert-1", expected_contract_version="synthetic-v1",
        expected_protocol_version=PROVIDER_MULTIMODAL_PROTOCOL_VERSION,
    )
    assert resumed["post_dispatched"] == 1
    assert resumed["provider_dispatch_state"] == "confirmed"
    with pytest.raises(RouterRepositoryError):
        dispatch(restarted)
    _, completed = restarted.complete_multimodal_certification_refresh(
        "local", "cert-1", status="passed", error_code=None,
        actual_model="test/video", checks={"actual_model_verified": True},
    )
    assert completed["poll_count"] == 1
    assert completed["status"] == "passed"


def test_multimodal_session_cannot_succeed_without_dispatch(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    claim_session(repository)
    with pytest.raises(RouterRepositoryError):
        repository.update_multimodal_certification_session(
            "local", "session-1", status="passed",
            provider_dispatch_state="confirmed", post_dispatched=False,
            completed=True,
        )


def test_restart_marks_dispatched_multimodal_call_uncertain(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    repository.replace_workload_policy(
        "local", entry_id="chat_image", expected_revision=0,
        policy_fingerprint="test-policy-fingerprint", local_fallback_mode="none", bindings=[],
    )
    ProviderContextStore(repository).register_execution(
        execution_id="image-request", task_id="image-task", source_kind="multimodal_request",
    )
    repository.claim_stable_workload_run(
        "local", run_id="run-1", entry_id="chat_image",
        policy_fingerprint="test-policy-fingerprint",
        parent_run_reference="image-task",
        origin_execution_id="image-request", policy_revision=1,
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
    repository.close()
    restarted = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    with sqlite3.connect(repository.database_path) as connection:
        row = connection.execute(
            "SELECT status, provider_dispatch_state FROM provider_workload_calls "
            "WHERE tenant_id = 'local' AND id = 'call-1'"
        ).fetchone()
    assert row == ("uncertain", "uncertain")
