from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.admin_auth import reset_provider_admin_auth
from server.model_router.api import configure_model_router, models_router, router
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import (
    SCHEMA_VERSION,
    RouterRepositoryError,
    SQLiteRouterRepository,
)
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import (
    MAX_WORKLOAD_SSE_EVENT_BYTES,
    MAX_WORKLOAD_UNARY_RESPONSE_BYTES,
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    SYNTHETIC_UNARY_PROMPT,
    ProviderWorkloadCallService,
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
import server.model_router.workload_control as workload_control_module
from server.model_router.cleanup_chat_receipts import cleanup_receipts


PAIRING_SECRET = "provider-admin-test-secret-at-least-32-chars"
V16_TABLES = {
    "provider_workload_certifications",
    "provider_workload_policies",
    "provider_workload_bindings",
    "provider_workload_runs",
    "provider_workload_calls",
    "provider_workload_approvals",
}


@pytest.fixture(autouse=True)
def _reset_admin_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


def test_v15_to_v16_is_additive_and_tenant_scoped(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE preserved_v15_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved_v15_data VALUES ('keep-me')")
        connection.execute("PRAGMA user_version = 15")

    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 16
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert V16_TABLES <= tables
        assert connection.execute("SELECT value FROM preserved_v15_data").fetchone()[0] == (
            "keep-me"
        )
    assert SCHEMA_VERSION == 16
    assert repository.get_workload_policy_bundle("local")["policies"] == []
    assert repository.get_workload_policy_bundle("other")["policies"] == []


def test_policy_revision_drift_and_receipt_replay_guards(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    saved = repository.replace_workload_policy(
        "local",
        entry_id="meta_agent",
        expected_revision=0,
        policy_fingerprint="policy-one",
        bindings=[
            {
                "execution_shape": "chat_json_object",
                "model_id": "provider/model",
                "connection_id": "conn-one",
                "certification_id": "cert-one",
                "certification_source": "provider_workload",
                "connection_fingerprint": "connection-one",
                "qualification_fingerprint": "qualification-one",
            }
        ],
    )
    assert saved["policies"][0]["revision"] == 1
    assert repository.get_workload_policy_bundle("other")["policies"] == []
    with pytest.raises(RouterRepositoryError) as exc_info:
        repository.replace_workload_policy(
            "local",
            entry_id="meta_agent",
            expected_revision=0,
            policy_fingerprint="stale",
            bindings=[],
        )
    assert str(exc_info.value) == "provider_workload_policy_revision_conflict"

    other_saved = repository.replace_workload_policy(
        "other",
        entry_id="meta_agent",
        expected_revision=0,
        policy_fingerprint="other-policy",
        bindings=[],
    )
    assert other_saved["policies"][0]["revision"] == 1
    missing_connection = ProviderWorkloadControlService(
        ModelRouterService(repository)
    ).get_policy("meta_agent")
    assert missing_connection.bindings[0].valid is False
    assert missing_connection.bindings[0].reason_code == (
        "provider_workload_connection_missing"
    )
    assert missing_connection.bindings[0].provider_kind is None

    repository.activate_workload_policy(
        "local",
        entry_id="meta_agent",
        expected_revision=1,
        policy_fingerprint="policy-one",
        no_open_p0_p1=True,
        acknowledge_fail_closed=True,
    )

    repository.claim_workload_run(
        "local",
        run_id="run-one",
        entry_id="meta_agent",
        policy_fingerprint="policy-one",
    )
    first, created = repository.claim_workload_call(
        "local",
        call_id="call-one",
        run_id="run-one",
        entry_id="meta_agent",
        execution_shape="chat_json_object",
        requested_model="provider/model",
        connection_id="conn-one",
        certification_id="cert-one",
        connection_fingerprint="connection-one",
        logical_call_key_hash="logical-one",
        call_sequence=1,
    )
    replay, replay_created = repository.claim_workload_call(
        "local",
        call_id="must-not-be-used",
        run_id="run-one",
        entry_id="meta_agent",
        execution_shape="chat_json_object",
        requested_model="provider/model",
        connection_id="conn-one",
        certification_id="cert-one",
        connection_fingerprint="connection-one",
        logical_call_key_hash="logical-one",
        call_sequence=1,
    )
    assert created is True
    assert replay_created is False
    assert replay["id"] == first["id"] == "call-one"
    repository.claim_workload_run(
        "other",
        run_id="run-one",
        entry_id="meta_agent",
        policy_fingerprint="other-policy",
    )
    other_call, other_created = repository.claim_workload_call(
        "other",
        call_id="call-one",
        run_id="run-one",
        entry_id="meta_agent",
        execution_shape="chat_json_object",
        requested_model="provider/model",
        connection_id="conn-one",
        certification_id="cert-one",
        connection_fingerprint="connection-one",
        logical_call_key_hash="logical-one",
        call_sequence=1,
    )
    assert other_created is True
    assert other_call["id"] == "call-one"
    assert len(repository.list_workload_receipts("local")["calls"]) == 1
    assert len(repository.list_workload_receipts("other")["calls"]) == 1
    with pytest.raises(RouterRepositoryError) as entry_error:
        repository.claim_workload_call(
            "local",
            call_id="wrong-entry",
            run_id="run-one",
            entry_id="fusion",
            execution_shape="fusion_native",
            requested_model="openrouter/fusion",
            connection_id="conn-one",
            certification_id="cert-one",
            connection_fingerprint="connection-one",
            logical_call_key_hash="wrong-entry",
            call_sequence=2,
        )
    assert str(entry_error.value) == "provider_workload_run_entry_mismatch"
    dispatch_args = {
        "run_id": "run-one",
        "entry_id": "meta_agent",
        "execution_shape": "chat_json_object",
        "requested_model": "provider/model",
        "connection_id": "conn-one",
        "certification_id": "cert-one",
        "connection_fingerprint": "connection-one",
        "policy_fingerprint": "policy-one",
    }
    repository.mark_workload_call_dispatched(
        "local", "call-one", **dispatch_args
    )
    with pytest.raises(RouterRepositoryError) as dispatch_error:
        repository.mark_workload_call_dispatched(
            "local", "call-one", **dispatch_args
        )
    assert str(dispatch_error.value) == "provider_workload_duplicate_dispatch_blocked"
    completed_call = repository.complete_workload_call(
        "local", "call-one", status="passed", result_class="success"
    )
    assert completed_call["status"] == "passed"
    completed_run = repository.complete_workload_run(
        "local", "run-one", status="passed", result_class="success"
    )
    assert completed_run["status"] == "passed"


def test_workload_receipt_state_machine_rejects_impossible_success(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.claim_workload_run(
        "local",
        run_id="run-impossible",
        entry_id="meta_agent",
        policy_fingerprint="policy-impossible",
    )
    repository.claim_workload_call(
        "local",
        call_id="call-impossible",
        run_id="run-impossible",
        entry_id="meta_agent",
        execution_shape="chat_json_object",
        requested_model="provider/model",
        connection_id="conn-one",
        certification_id="cert-one",
        connection_fingerprint="connection-one",
        logical_call_key_hash="logical-impossible",
        call_sequence=1,
    )

    with pytest.raises(RouterRepositoryError) as invalid_call_status:
        repository.complete_workload_call(
            "local", "call-impossible", status="looks_good"
        )
    assert str(invalid_call_status.value) == "invalid_provider_workload_call_status"
    with pytest.raises(RouterRepositoryError) as undispatched_success:
        repository.complete_workload_call(
            "local",
            "call-impossible",
            status="passed",
            result_class="success",
        )
    assert str(undispatched_success.value) == (
        "provider_workload_call_passed_without_dispatch"
    )
    with pytest.raises(RouterRepositoryError) as running_child:
        repository.complete_workload_run(
            "local", "run-impossible", status="passed", result_class="success"
        )
    assert str(running_child.value) == "provider_workload_run_has_running_calls"

    failed = repository.complete_workload_call(
        "local",
        "call-impossible",
        status="failed",
        result_class="preflight_failure",
    )
    assert failed["dispatched"] == 0
    with pytest.raises(RouterRepositoryError) as failed_child:
        repository.complete_workload_run(
            "local", "run-impossible", status="passed", result_class="success"
        )
    assert str(failed_child.value) == (
        "provider_workload_run_passed_without_successful_calls"
    )
    completed = repository.complete_workload_run(
        "local",
        "run-impossible",
        status="failed",
        result_class="preflight_failure",
    )
    assert completed["status"] == "failed"


def test_running_workload_evidence_becomes_uncertain_and_cleanup_is_dry_run(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.claim_workload_run(
        "local",
        run_id="run-one",
        entry_id="meta_agent",
        policy_fingerprint="policy-one",
    )
    repository.claim_workload_call(
        "local",
        call_id="call-one",
        run_id="run-one",
        entry_id="meta_agent",
        execution_shape="chat_json_object",
        requested_model="provider/model",
        connection_id="conn-one",
        certification_id="cert-one",
        connection_fingerprint="connection-one",
        logical_call_key_hash="logical-one",
        call_sequence=1,
    )
    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    receipts = restarted.list_workload_receipts("local")
    assert receipts["runs"][0]["status"] == "uncertain"
    assert receipts["calls"][0]["status"] == "uncertain"
    with pytest.raises(RouterRepositoryError) as uncertain_error:
        restarted.claim_workload_call(
            "local",
            call_id="call-two",
            run_id="run-one",
            entry_id="meta_agent",
            execution_shape="chat_json_object",
            requested_model="provider/model",
            connection_id="conn-one",
            certification_id="cert-one",
            connection_fingerprint="connection-one",
            logical_call_key_hash="logical-two",
            call_sequence=2,
        )
    assert str(uncertain_error.value) == "provider_workload_run_not_running"

    with sqlite3.connect(restarted.database_path) as connection:
        connection.execute(
            "UPDATE provider_workload_runs SET completed_at = ?, updated_at = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "UPDATE provider_workload_calls SET completed_at = ?, updated_at = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )
    dry_run = restarted.cleanup_workload_receipts(
        "local", before="2021-01-01T00:00:00+00:00"
    )
    assert dry_run == {
        "applied": False,
        "before": "2021-01-01T00:00:00+00:00",
        "runs": 1,
        "calls": 1,
    }
    assert restarted.list_workload_receipts("local")["runs"]
    restarted.cleanup_workload_receipts(
        "local", before="2021-01-01T00:00:00+00:00", apply=True
    )
    assert restarted.list_workload_receipts("local")["runs"] == []


def test_bounded_receipt_cleanup_includes_workload_and_stays_dry_run(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    repository.claim_workload_run(
        "local",
        run_id="run-cleanup",
        entry_id="meta_agent",
        policy_fingerprint="policy-cleanup",
    )
    repository.complete_workload_run(
        "local",
        "run-cleanup",
        status="failed",
        result_class="test",
        reason_codes=["test"],
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE provider_workload_runs SET completed_at = ?, updated_at = ?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )

    result = cleanup_receipts(
        repository,
        "local",
        before="2021-01-01T00:00:00+00:00",
    )

    assert result["applied"] is False
    assert result["workload_runs"] == 1
    assert result["workload_calls"] == 0
    assert repository.list_workload_receipts("local")["runs"]


@pytest.mark.asyncio
async def test_workload_certification_requires_billed_ack_and_idempotency_key(
    tmp_path: Path,
) -> None:
    service = ProviderWorkloadCertificationService(
        ModelRouterService(SQLiteRouterRepository(tmp_path, master_key=b"x" * 32))
    )
    with pytest.raises(RouterServiceError) as acknowledgement:
        await service.run(
            "missing",
            ProviderWorkloadCertificationRequest(
                execution_shape="chat_text_unary",
                model_id="provider/model",
                acknowledge_billed_call=False,
            ),
            idempotency_key="ack-required",
        )
    assert acknowledgement.value.code == "billed_call_acknowledgement_required"

    with pytest.raises(RouterServiceError) as idempotency:
        await service.run(
            "missing",
            ProviderWorkloadCertificationRequest(
                execution_shape="chat_text_unary",
                model_id="provider/model",
                acknowledge_billed_call=True,
            ),
            idempotency_key="",
        )
    assert idempotency.value.code == "invalid_idempotency_key"


@pytest.mark.asyncio
async def test_unary_certification_uses_one_pinned_post_and_stores_no_content(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []
    catalog_online = True

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            if not catalog_online:
                return Response(503)
            return Response(200, json={"data": [{"id": "provider/model"}]})
        body = json.loads(request.content)
        assert body == {
            "model": "provider/model",
            "temperature": 0,
            "max_tokens": 64,
            "stream": False,
            "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
        }
        return Response(
            200,
            json={
                "model": "provider/model",
                "choices": [
                    {"message": {"content": "OK"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Managed Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="workload-certification-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    service = ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    payload = ProviderWorkloadCertificationRequest(
        execution_shape="chat_text_unary",
        model_id="provider/model",
        acknowledge_billed_call=True,
    )
    first = await service.run(connection.id, payload, idempotency_key="one-call")
    catalog_online = False
    replay = await service.run(connection.id, payload, idempotency_key="one-call")

    assert first.status == replay.status == "passed"
    assert first.can_run is True
    assert first.total_tokens == 4
    assert [request.method for request in requests].count("GET") == 1
    assert [request.method for request in requests].count("POST") == 1
    assert requests[-1].url.host == "8.8.8.8"
    assert "workload-certification-secret" not in first.model_dump_json()
    database_bytes = repository.database_path.read_bytes()
    assert SYNTHETIC_UNARY_PROMPT.encode() not in database_bytes
    assert b'"content":"OK"' not in database_bytes
    repository.save_test_result(
        "local",
        connection.id,
        health="offline",
        model_count=0,
        checked_at="2026-08-23T00:00:00+00:00",
        error_code="test_offline",
    )
    offline = service.list().certifications[0]
    assert offline.can_run is False
    assert offline.blocked_reason == "provider_connection_not_online"


@pytest.mark.asyncio
async def test_failed_workload_certification_is_not_reported_as_runnable(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(200, json={"model": "provider/model", "choices": []})

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Managed Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="failed-cert-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    result = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_text_unary",
            model_id="provider/model",
            acknowledge_billed_call=True,
        ),
        idempotency_key="failed-certification",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_empty_response"
    assert result.can_run is False


@pytest.mark.asyncio
async def test_workload_certification_rejects_oversized_unary_response(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(
            200,
            json={
                "model": "provider/model",
                "choices": [
                    {
                        "message": {
                            "content": "x" * MAX_WORKLOAD_UNARY_RESPONSE_BYTES
                        }
                    }
                ],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Bounded Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="bounded-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    result = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_text_unary",
            model_id="provider/model",
            acknowledge_billed_call=True,
        ),
        idempotency_key="oversized-unary",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_response_too_large"
    assert [request.method for request in requests].count("POST") == 1
    assert b"x" * 1024 not in repository.database_path.read_bytes()


@pytest.mark.asyncio
async def test_json_certification_qualifies_exact_binding_and_new_evidence_stales_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/json-model"}]})
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["stream"] is False
        return Response(
            200,
            json={
                "model": "provider/json-model",
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="JSON Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="json-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    certification = ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    request = ProviderWorkloadCertificationRequest(
        execution_shape="chat_json_object",
        model_id="provider/json-model",
        acknowledge_billed_call=True,
    )
    first = await certification.run(
        connection.id, request, idempotency_key="json-first"
    )
    assert first.status == "passed"
    assert first.checks.json_object_verified is True

    control = ProviderWorkloadControlService(router_service)
    saved = control.update_policy(
        "meta_agent",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_json_object",
                    model_id="provider/json-model",
                    connection_id=connection.id,
                )
            ],
        ),
    )
    assert saved.bindings[0].valid is True
    assert saved.bindings[0].certification_id == first.certification_id
    repository.activate_workload_policy(
        "local",
        entry_id="meta_agent",
        expected_revision=saved.revision,
        policy_fingerprint=saved.policy_fingerprint,
        no_open_p0_p1=True,
        acknowledge_fail_closed=True,
    )
    assert control.get_policy("meta_agent").approval_valid is True

    second = await certification.run(
        connection.id, request, idempotency_key="json-second"
    )
    assert second.status == "passed"
    drifted = control.get_policy("meta_agent")
    assert drifted.bindings[0].valid is False
    assert drifted.approval_valid is False
    assert drifted.bindings[0].reason_code == (
        "provider_workload_newer_certification_requires_policy_update"
    )
    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            "UPDATE provider_workload_certifications SET completed_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", second.certification_id),
        )
    monkeypatch.setenv(
        "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS", "300"
    )
    expired = control.get_policy("meta_agent")
    assert expired.bindings[0].reason_code == (
        "provider_workload_certification_expired"
    )
    expired_summary = next(
        item
        for item in certification.list().certifications
        if item.certification_id == second.certification_id
    )
    assert expired_summary.status == "stale"
    assert expired_summary.can_run is False
    assert expired_summary.blocked_reason == "provider_workload_certification_expired"
    assert [item.method for item in requests].count("POST") == 2


@pytest.mark.asyncio
async def test_workload_dispatch_rechecks_policy_after_credential_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/json-model"}]})
        return Response(
            200,
            json={
                "model": "provider/json-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Drift Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="before-drift-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    certification = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_json_object",
            model_id="provider/json-model",
            acknowledge_billed_call=True,
        ),
        idempotency_key="dispatch-drift",
    )
    assert certification.status == "passed"

    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    monkeypatch.setattr(
        workload_control_module,
        "DATA_PLANE_INTEGRATED_ENTRIES",
        frozenset({"meta_agent"}),
    )
    control = ProviderWorkloadControlService(router_service)
    saved = control.update_policy(
        "meta_agent",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_json_object",
                    model_id="provider/json-model",
                    connection_id=connection.id,
                )
            ],
        ),
    )
    active = control.activate(
        "meta_agent",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert active.effective_status == "managed_required"

    call_service = ProviderWorkloadCallService(router_service)
    run_id = call_service.start_run("meta_agent")
    prepared = await call_service.prepare_call(
        run_id=run_id,
        entry_id="meta_agent",
        execution_shape="chat_json_object",
        model_id="provider/json-model",
        logical_call_key="first-planner-call",
        call_sequence=1,
    )
    repository.update_connection(
        "local",
        connection.id,
        RouterConnectionUpdate(api_key="after-drift-secret"),
    )

    with pytest.raises(RouterServiceError) as drift:
        call_service.mark_dispatched(prepared)
    assert drift.value.code == "provider_workload_policy_not_active"
    receipt = repository.list_workload_receipts("local")["calls"][0]
    assert receipt["dispatched"] == 0
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_native_fusion_certification_is_openrouter_only_and_one_post(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []
    model_ids = [
        "openrouter/fusion",
        "provider/candidate-a",
        "provider/candidate-b",
        "provider/judge",
    ]

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": item} for item in model_ids]})
        body = json.loads(request.content)
        assert body["model"] == "openrouter/fusion"
        assert body["plugins"] == [
            {
                "id": "fusion",
                "analysis_models": [
                    "provider/candidate-a",
                    "provider/candidate-b",
                ],
                "model": "provider/judge",
            }
        ]
        return Response(
            200,
            content=(
                b'data: {"model":"openrouter/fusion","choices":'
                b'[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.example/api/v1",
            api_key="fusion-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    service = ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    result = await service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="fusion_native",
            model_id="openrouter/fusion",
            candidate_model_ids=["provider/candidate-a", "provider/candidate-b"],
            judge_model_id="provider/judge",
            acknowledge_billed_call=True,
        ),
        idempotency_key="fusion-once",
    )
    assert result.status == "passed"
    assert result.checks.fusion_profile_verified is True
    assert result.candidate_model_ids == [
        "provider/candidate-a",
        "provider/candidate-b",
    ]
    assert result.judge_model_id == "provider/judge"
    assert [request.method for request in requests].count("POST") == 1
    assert "fusion-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_workload_certification_rejects_oversized_sse_event(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []
    model_ids = [
        "openrouter/fusion",
        "provider/candidate-a",
        "provider/candidate-b",
        "provider/judge",
    ]

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": item} for item in model_ids]})
        return Response(
            200,
            content=b"data: " + b"x" * MAX_WORKLOAD_SSE_EVENT_BYTES + b"\n\n",
            headers={"content-type": "text/event-stream"},
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Bounded Fusion",
            kind="openrouter",
            base_url="https://openrouter.example/api/v1",
            api_key="bounded-fusion-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    result = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="fusion_native",
            model_id="openrouter/fusion",
            candidate_model_ids=["provider/candidate-a", "provider/candidate-b"],
            judge_model_id="provider/judge",
            acknowledge_billed_call=True,
        ),
        idempotency_key="oversized-fusion-event",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_sse_event_too_large"
    assert [request.method for request in requests].count("POST") == 1


def _app(tmp_path: Path) -> FastAPI:
    configure_model_router(
        ModelRouterService(SQLiteRouterRepository(tmp_path, master_key=b"x" * 32))
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(models_router)
    return app


@pytest.mark.asyncio
async def test_workload_admin_api_is_session_and_csrf_protected_and_public_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", PAIRING_SECRET)
    async with AsyncClient(
        transport=ASGITransport(app=_app(tmp_path)), base_url="http://localhost"
    ) as client:
        assert (
            await client.get("/api/router/workload-control/policies")
        ).status_code == 401
        assert (
            await client.get("/api/router/certifications/workloads")
        ).status_code == 401
        paired = await client.post(
            "/api/router/admin/session", json={"pairing_secret": PAIRING_SECRET}
        )
        csrf = paired.json()["csrf_token"]
        policies = await client.get("/api/router/workload-control/policies")
        assert policies.status_code == 200
        assert len(policies.json()["policies"]) == 13
        assert policies.json()["contract_version"] == PROVIDER_WORKLOAD_CONTRACT_VERSION

        update = {
            "expected_revision": 0,
            "bindings": [],
        }
        assert (
            await client.put(
                "/api/router/workload-control/policies/meta_agent", json=update
            )
        ).status_code == 403
        saved = await client.put(
            "/api/router/workload-control/policies/meta_agent",
            headers={"X-ModelMirror-CSRF": csrf},
            json=update,
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert saved.json()["data_plane_integrated"] is False
        activation = await client.post(
            "/api/router/workload-control/policies/meta_agent/activate",
            headers={"X-ModelMirror-CSRF": csrf},
            json={
                "expected_revision": 1,
                "no_open_p0_p1": True,
                "acknowledge_fail_closed": True,
            },
        )
        assert activation.status_code == 409
        assert activation.json()["detail"]["code"] in {
            "provider_workload_bindings_required",
            "provider_workload_data_plane_not_integrated",
            "provider_workload_feature_disabled",
        }

        public = await client.get(
            "/api/models/provider-workload-control",
            params={
                "entry_id": "meta_agent",
                "model_id": "provider/model",
                "execution_shape": "chat_json_object",
            },
        )
        assert public.status_code == 200
        assert public.headers["cache-control"] == "no-store"
        assert public.json()["available"] is False
        assert public.json()["blocks_before_dispatch"] is True
        assert "tenant" not in public.text
        assert "connection_id" not in public.text
        assert "base_url" not in public.text
