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
from server.model_router.provider_operations import (
    OPENROUTER_BATCHES_URL,
    ProviderOperationEndpointResolver,
    ProviderOperationTarget,
    ProviderOperationTransport,
    provider_operation_model_matches,
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
V17_TABLES = {
    "provider_workload_certifications",
    "provider_workload_policies",
    "provider_workload_bindings",
    "provider_workload_runs",
    "provider_workload_calls",
    "provider_workload_approvals",
    "provider_batch_jobs",
}


@pytest.fixture(autouse=True)
def _reset_admin_auth() -> None:
    reset_provider_admin_auth()
    yield
    reset_provider_admin_auth()


def test_v16_to_v17_is_additive_and_tenant_scoped(tmp_path: Path) -> None:
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE preserved_v15_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved_v15_data VALUES ('keep-me')")
        connection.execute(
            """
            CREATE TABLE provider_workload_policies (
                tenant_id TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'legacy',
                revision INTEGER NOT NULL DEFAULT 0,
                policy_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, entry_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO provider_workload_policies VALUES (
                'local', 'meta_agent', 'legacy', 1, 'preserved-policy',
                '2026-08-25T00:00:00Z', '2026-08-25T00:00:00Z'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE provider_workload_bindings (
                tenant_id TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                execution_shape TEXT NOT NULL,
                model_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                certification_id TEXT NOT NULL,
                certification_source TEXT NOT NULL,
                connection_fingerprint TEXT NOT NULL,
                qualification_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, entry_id, execution_shape, model_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO provider_workload_bindings VALUES (
                'local', 'meta_agent', 'chat_json_object', 'provider/model',
                'conn-old', 'cert-old', 'provider_workload', 'conn-fp',
                'qualification-fp', '2026-08-25T00:00:00Z',
                '2026-08-25T00:00:00Z'
            )
            """
        )
        connection.execute("PRAGMA user_version = 16")

    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert V17_TABLES <= tables
        assert connection.execute("SELECT value FROM preserved_v15_data").fetchone()[0] == (
            "keep-me"
        )
        policy = connection.execute(
            "SELECT policy_fingerprint, local_fallback_mode "
            "FROM provider_workload_policies WHERE tenant_id = 'local'"
        ).fetchone()
        assert policy == ("preserved-policy", "none")
        binding = connection.execute(
            "SELECT certification_id, rerank_access_mode "
            "FROM provider_workload_bindings WHERE tenant_id = 'local'"
        ).fetchone()
        assert binding == ("cert-old", None)
        assert "vector_dimension" in {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(provider_workload_certifications)"
            )
        }
    assert SCHEMA_VERSION == 17
    assert len(repository.get_workload_policy_bundle("local")["policies"]) == 1
    assert repository.get_workload_policy_bundle("other")["policies"] == []


@pytest.mark.asyncio
async def test_operation_endpoints_are_explicit_and_transport_is_single_ip() -> None:
    endpoints = ProviderOperationEndpointResolver.resolve(
        provider_kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )
    assert endpoints.embeddings_url == "https://openrouter.ai/api/v1/embeddings"
    assert endpoints.rerank_url == "https://openrouter.ai/api/v1/rerank"
    assert endpoints.batches_url == OPENROUTER_BATCHES_URL

    target = ProviderOperationTarget.create(
        provider_kind="openrouter",
        connection_id="conn-openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="operation-secret",
    )
    assert target.endpoint_for("rerank_documents", rerank_access_mode="llm_json") == (
        "https://openrouter.ai/api/v1/chat/completions"
    )
    assert target.endpoint_for("openrouter_batch_chat") == OPENROUTER_BATCHES_URL
    assert target.endpoint_for(
        "openrouter_batch_chat", upstream_batch_id="batch_123"
    ) == f"{OPENROUTER_BATCHES_URL}/batch_123"

    egress = ProviderEgressPolicy(
        resolver=lambda _host, _port: ["93.184.216.34"]
    )
    transport = ProviderOperationTransport(egress)
    authorized = await transport.authorize(target, "embedding_vectors")
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        assert request.url == "https://93.184.216.34/api/v1/embeddings"
        assert request.headers["host"] == "openrouter.ai"
        assert request.headers["authorization"] == "Bearer operation-secret"
        assert request.extensions["sni_hostname"] == "openrouter.ai"
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(
        transport=MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        request = transport.build_authorized_request(
            client,
            target,
            authorized,
            method="POST",
            payload={"model": "provider/embed", "input": ["one", "two"]},
        )
        response = await transport.send_authorized(client, request)
        await response.aclose()
    assert len(observed) == 1
    assert ProviderOperationTransport.client_kwargs()["follow_redirects"] is False
    assert ProviderOperationTransport.client_kwargs()["trust_env"] is False


def test_provider_batch_job_is_tenant_scoped_idempotent_and_restart_safe(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter batch",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-secret",
            scopes=["batch"],
        ),
    )
    base = {
        "connection_id": connection.id,
        "connection_fingerprint": "connection-fingerprint",
        "endpoint": "/v1/chat/completions",
        "model_id": "provider/model",
        "idempotency_key_hash": "idem-one",
        "request_fingerprint": "request-one",
        "purpose": "certification",
        "request_count": 1,
    }
    claimed, created = repository.claim_provider_batch_job(
        "local", job_id="mmbatch_one", **base
    )
    replay, replay_created = repository.claim_provider_batch_job(
        "local", job_id="must-not-be-used", **base
    )
    assert created is True
    assert replay_created is False
    assert replay["id"] == claimed["id"] == "mmbatch_one"
    with pytest.raises(RouterRepositoryError) as conflict:
        repository.claim_provider_batch_job(
            "local",
            job_id="mmbatch_conflict",
            **{**base, "request_fingerprint": "different"},
        )
    assert str(conflict.value) == "provider_batch_idempotency_conflict"
    assert repository.list_provider_batch_jobs("other") == []

    submitted = repository.mark_provider_batch_submitted(
        "local",
        "mmbatch_one",
        upstream_batch_id="batch_upstream_1",
        status="validating",
    )
    assert submitted["upstream_batch_id"] == "batch_upstream_1"
    repository.update_provider_batch_job(
        "local",
        "mmbatch_one",
        status="in_progress",
        completed_count=0,
        failed_count=0,
        usage={"total_tokens": 0},
    )

    repository.claim_provider_batch_job(
        "local",
        job_id="mmbatch_uncertain",
        **{**base, "idempotency_key_hash": "idem-two"},
    )
    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    assert restarted.get_provider_batch_job("local", "mmbatch_one")["status"] == (
        "in_progress"
    )
    uncertain = restarted.get_provider_batch_job("local", "mmbatch_uncertain")
    assert uncertain["status"] == "uncertain"
    assert uncertain["error_code"] == "server_restarted"
    serialized = json.dumps(restarted.list_provider_batch_jobs("local"))
    assert "test-secret" not in serialized
    assert "prompt" not in serialized.casefold()


def test_policy_revision_drift_and_receipt_replay_guards(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    saved = repository.replace_workload_policy(
        "local",
        entry_id="meta_agent",
        expected_revision=0,
        policy_fingerprint="policy-one",
        local_fallback_mode="none",
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
            local_fallback_mode="none",
            bindings=[],
        )
    assert str(exc_info.value) == "provider_workload_policy_revision_conflict"

    other_saved = repository.replace_workload_policy(
        "other",
        entry_id="meta_agent",
        expected_revision=0,
        policy_fingerprint="other-policy",
        local_fallback_mode="none",
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


@pytest.mark.parametrize(
    ("provider_kind", "requested_model", "actual_model", "expected_warning"),
    [
        ("openai_compatible", "provider/embed", "provider/embed", None),
        (
            "openrouter",
            "openai/text-embedding-3-small",
            "text-embedding-3-small",
            "actual_model_provider_prefix_omitted",
        ),
    ],
)
@pytest.mark.asyncio
async def test_embedding_certification_validates_exact_finite_vector_space(
    tmp_path: Path,
    provider_kind: str,
    requested_model: str,
    actual_model: str,
    expected_warning: str | None,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": requested_model}]})
        assert request.url.path.endswith("/v1/embeddings")
        assert json.loads(request.content) == {
            "model": requested_model,
            "input": [
                "ModelMirror embedding certification one.",
                "ModelMirror embedding certification two.",
            ],
            "encoding_format": "float",
        }
        return Response(
            200,
            json={
                "model": actual_model,
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Embedding Provider",
            kind=provider_kind,
            base_url="https://provider.example/v1",
            api_key="embedding-cert-secret",
            scopes=["embedding"],
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
            execution_shape="embedding_vectors",
            model_id=requested_model,
            acknowledge_billed_call=True,
        ),
        idempotency_key="embedding-certification",
    )

    assert result.status == "passed"
    assert result.checks.embedding_vectors_verified is True
    assert result.checks.actual_model_verified is True
    assert result.vector_dimension == 3
    assert (expected_warning in result.warning_codes) is bool(expected_warning)
    offering = repository.list_catalog_offerings(
        "local",
        connection_id=connection.id,
        model_id=requested_model,
        operation="embed",
        include_stale=False,
    )
    assert any(
        item["capability_source"] == "certification"
        and item["access_mode"] == "managed_embedding"
        for item in offering
    )
    assert sum(request.method == "POST" for request in requests) == 1
    serialized = repository.database_path.read_bytes()
    assert b"0.1" not in serialized
    assert b"embedding-cert-secret" not in serialized


def test_operation_model_match_only_allows_openrouter_provider_prefix_omission() -> None:
    assert provider_operation_model_matches(
        provider_kind="openrouter",
        requested_model="openai/text-embedding-3-small",
        actual_model="text-embedding-3-small",
    )
    assert not provider_operation_model_matches(
        provider_kind="openai_compatible",
        requested_model="openai/text-embedding-3-small",
        actual_model="text-embedding-3-small",
    )
    assert not provider_operation_model_matches(
        provider_kind="openrouter",
        requested_model="openai/text-embedding-3-small",
        actual_model="text-embedding-3-large",
    )


@pytest.mark.asyncio
async def test_rerank_certification_keeps_dedicated_and_llm_json_modes_explicit(
    tmp_path: Path,
) -> None:
    post_paths: list[str] = []

    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/rerank"}]})
        post_paths.append(request.url.path)
        results = [
            {"index": 0, "relevance_score": 0.9},
            {"index": 2, "relevance_score": 0.7},
            {"index": 1, "relevance_score": 0.1},
        ]
        if request.url.path.endswith("/rerank"):
            return Response(
                200,
                json={"model": "provider/rerank", "results": results},
            )
        return Response(
            200,
            json={
                "model": "provider/rerank",
                "choices": [
                    {"message": {"content": json.dumps({"results": results})}}
                ],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Rerank Provider",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="rerank-cert-secret",
            scopes=["rerank"],
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
    dedicated = await service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="rerank_documents",
            model_id="provider/rerank",
            rerank_access_mode="dedicated",
            acknowledge_billed_call=True,
        ),
        idempotency_key="rerank-dedicated",
    )
    llm_json = await service.run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="rerank_documents",
            model_id="provider/rerank",
            rerank_access_mode="llm_json",
            acknowledge_billed_call=True,
        ),
        idempotency_key="rerank-llm-json",
    )

    assert dedicated.status == llm_json.status == "passed"
    assert dedicated.rerank_access_mode == "dedicated"
    assert llm_json.rerank_access_mode == "llm_json"
    assert dedicated.profile_fingerprint != llm_json.profile_fingerprint
    assert post_paths == ["/v1/rerank", "/v1/chat/completions"]
    control = ProviderWorkloadControlService(router_service)
    dedicated_policy = control.update_policy(
        "rag_rerank",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="rerank_documents",
                    model_id="provider/rerank",
                    connection_id=connection.id,
                    rerank_access_mode="dedicated",
                )
            ],
        ),
    )
    assert dedicated_policy.bindings[0].certification_id == dedicated.certification_id
    assert dedicated_policy.bindings[0].rerank_access_mode == "dedicated"
    llm_policy = control.update_policy(
        "rag_rerank",
        ProviderWorkloadPolicyUpdate(
            expected_revision=dedicated_policy.revision,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="rerank_documents",
                    model_id="provider/rerank",
                    connection_id=connection.id,
                    rerank_access_mode="llm_json",
                )
            ],
        ),
    )
    assert llm_policy.bindings[0].certification_id == llm_json.certification_id
    assert llm_policy.bindings[0].rerank_access_mode == "llm_json"
    assert llm_policy.policy_fingerprint != dedicated_policy.policy_fingerprint
    with pytest.raises(ValueError, match="rerank_access_mode"):
        ProviderWorkloadCertificationRequest(
            execution_shape="rerank_documents",
            model_id="provider/rerank",
            acknowledge_billed_call=True,
        )
    with pytest.raises(ValueError, match="rerank_access_mode"):
        ProviderWorkloadBindingUpdate(
            execution_shape="rerank_documents",
            model_id="provider/rerank",
            connection_id=connection.id,
        )


@pytest.mark.asyncio
async def test_embedding_and_rerank_reject_invalid_contract_evidence(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(
                200,
                json={
                    "data": [
                        {"id": "provider/embed"},
                        {"id": "provider/rerank"},
                    ]
                },
            )
        if request.url.path.endswith("/embeddings"):
            return Response(
                200,
                content=(
                    b'{"model":"provider/embed","data":['
                    b'{"index":0,"embedding":[0.1,NaN]},'
                    b'{"index":1,"embedding":[0.2,0.3]}]}'
                ),
                headers={"content-type": "application/json"},
            )
        return Response(
            200,
            json={
                "model": "provider/rerank",
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                    {"index": 2, "relevance_score": 0.1},
                ],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    embedding_connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Invalid Embedding",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="invalid-evidence-secret",
            scopes=["embedding"],
        ),
    )
    rerank_connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Invalid Rerank",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="invalid-evidence-secret",
            scopes=["rerank"],
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
    embedding = await service.run(
        embedding_connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="embedding_vectors",
            model_id="provider/embed",
            acknowledge_billed_call=True,
        ),
        idempotency_key="invalid-embedding",
    )
    rerank = await service.run(
        rerank_connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="rerank_documents",
            model_id="provider/rerank",
            rerank_access_mode="dedicated",
            acknowledge_billed_call=True,
        ),
        idempotency_key="invalid-rerank",
    )
    assert embedding.status == rerank.status == "failed"
    assert embedding.error_code == "provider_embedding_non_finite_vector"
    assert rerank.error_code == "provider_rerank_duplicate_or_missing_index"
    database_bytes = repository.database_path.read_bytes()
    assert b"invalid-evidence-secret" not in database_bytes
    assert b"relevance_score" not in database_bytes


@pytest.mark.asyncio
async def test_batch_certification_posts_once_then_polls_without_storing_results(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            assert request.url.path == "/api/beta/batches"
            return Response(202, json={"id": "batch_cert_1", "status": "validating"})
        assert request.url.path == "/api/beta/batches/batch_cert_1"
        return Response(
            200,
            json={
                "id": "batch_cert_1",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {
                                "model": "provider/model",
                                "choices": [{"message": {"content": "OK"}}],
                            },
                        },
                    }
                ],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter Batch",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="batch-cert-secret",
            scopes=["batch"],
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
            execution_shape="openrouter_batch_chat",
            model_id="provider/model",
            acknowledge_billed_call=True,
        ),
        idempotency_key="batch-certification",
    )

    assert result.status == "passed"
    assert result.batch_status == "completed"
    assert result.checks.batch_terminal_verified is True
    assert sum(request.method == "POST" for request in requests) == 1
    assert sum(request.method == "GET" for request in requests) == 2
    database_bytes = repository.database_path.read_bytes()
    assert b"modelmirror-certification" not in database_bytes
    assert b'"content":"OK"' not in database_bytes
    assert b"batch-cert-secret" not in database_bytes


@pytest.mark.asyncio
async def test_batch_poll_recovers_after_restart_without_second_post(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []
    poll_attempts = 0

    def handler(request: Request) -> Response:
        nonlocal poll_attempts
        requests.append(request)
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            return Response(202, json={"id": "batch_restart_1", "status": "validating"})
        poll_attempts += 1
        if poll_attempts == 1:
            raise httpx.ReadTimeout("poll interrupted", request=request)
        return Response(
            200,
            json={
                "id": "batch_restart_1",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {"model": "provider/model"},
                        },
                    }
                ],
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Restart-safe Batch",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="batch-restart-secret",
            scopes=["batch"],
        ),
    )

    def service_for(repository: SQLiteRouterRepository) -> ProviderWorkloadCertificationService:
        router_service = ModelRouterService(
            repository,
            client_factory=lambda: httpx.AsyncClient(
                transport=transport, follow_redirects=False, trust_env=False
            ),
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8"]
            ),
        )
        return ProviderWorkloadCertificationService(
            router_service,
            client_factory=lambda: httpx.AsyncClient(
                transport=transport, follow_redirects=False, trust_env=False
            ),
            batch_poll_interval_seconds=0,
        )

    payload = ProviderWorkloadCertificationRequest(
        execution_shape="openrouter_batch_chat",
        model_id="provider/model",
        acknowledge_billed_call=True,
    )
    first = await service_for(repository).run(
        connection.id,
        payload,
        idempotency_key="restart-safe-batch",
    )
    assert first.status == "uncertain"
    assert first.error_code == "provider_batch_poll_uncertain"

    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted_service = service_for(restarted)
    assert await restarted_service.resume_pending_batch_certifications() == 1
    resumed = next(
        item
        for item in restarted_service.list().certifications
        if item.certification_id == first.certification_id
    )
    assert resumed.status == "passed"
    assert resumed.checks.batch_terminal_verified is True
    assert sum(request.method == "POST" for request in requests) == 1
    assert poll_attempts == 2
    assert restarted.list_provider_batch_jobs("local")[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_batch_submission_uncertainty_never_reposts_same_idempotency_key(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        raise httpx.ReadTimeout("submission outcome unknown", request=request)

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Uncertain OpenRouter Batch",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="batch-uncertain-secret",
            scopes=["batch"],
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
    payload = ProviderWorkloadCertificationRequest(
        execution_shape="openrouter_batch_chat",
        model_id="provider/model",
        acknowledge_billed_call=True,
    )
    first = await service.run(
        connection.id,
        payload,
        idempotency_key="uncertain-batch-certification",
    )
    replay = await service.run(
        connection.id,
        payload,
        idempotency_key="uncertain-batch-certification",
    )
    assert first.status == replay.status == "uncertain"
    assert first.error_code == "provider_batch_submission_uncertain"
    assert sum(request.method == "POST" for request in requests) == 1
    jobs = repository.list_provider_batch_jobs("local")
    assert len(jobs) == 1
    assert jobs[0]["status"] == "uncertain"
    assert b"batch-uncertain-secret" not in repository.database_path.read_bytes()


def test_r7_local_fallback_policy_is_explicit_and_entry_scoped(
    tmp_path: Path,
) -> None:
    control = ProviderWorkloadControlService(
        ModelRouterService(SQLiteRouterRepository(tmp_path, master_key=b"x" * 32))
    )
    policy = control.update_policy(
        "rag_query_generate",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            local_fallback_mode="extractive",
            bindings=[],
        ),
    )
    assert policy.local_fallback_mode == "extractive"
    assert policy.data_plane_integrated is False
    with pytest.raises(RouterServiceError) as invalid:
        control.update_policy(
            "rag_embedding",
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                local_fallback_mode="lexical",
                bindings=[],
            ),
        )
    assert invalid.value.code == "provider_workload_local_fallback_not_allowed"


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
                b'data: {"model":"provider/judge","choices":'
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
    assert result.actual_model == "provider/judge"
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
        assert len(policies.json()["policies"]) == 19
        r7_policies = {
            item["entry_id"]: item for item in policies.json()["policies"]
            if item["entry_id"].startswith("rag_")
            or item["entry_id"] in {"skill_rerank", "openrouter_batch"}
        }
        assert len(r7_policies) == 6
        assert r7_policies["rag_embedding"]["data_plane_integrated"] is True
        assert all(
            item["data_plane_integrated"] is False
            for entry_id, item in r7_policies.items()
            if entry_id != "rag_embedding"
        )
        assert all(item["feature_enabled"] is False for item in r7_policies.values())
        assert all(item["local_fallback_mode"] == "none" for item in r7_policies.values())
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
        assert saved.json()["data_plane_integrated"] is True
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
