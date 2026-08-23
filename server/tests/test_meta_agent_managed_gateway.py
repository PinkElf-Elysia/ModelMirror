from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from server.meta_agent.managed_gateway import (
    ManagedMetaAgentGateway,
    ManagedMetaAgentRoutingError,
)
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
    ProviderWorkloadControlService,
)


MODEL_ID = "provider/meta-model"
PROVIDER_SECRET = "managed-meta-provider-secret"


async def _qualified_router(
    tmp_path: Path,
) -> tuple[ModelRouterService, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Meta Provider",
            kind="openai_compatible",
            base_url="https://meta-provider.example/v1",
            api_key=PROVIDER_SECRET,
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-23T00:00:00+00:00",
    )
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    refresh_id = f"refresh-{connection.id}"
    repository.claim_catalog_refresh(
        "local",
        refresh_id=refresh_id,
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        refresh_id,
        connection_id=connection.id,
        models=[
            {
                "model_id": MODEL_ID,
                "normalized_model_id": MODEL_ID,
                "capability_state": "declared",
            }
        ],
        offerings=[],
        model_count=1,
        truncated=False,
        catalog_fingerprint="meta-catalog",
        observed_at="2026-08-23T00:00:00+00:00",
    )
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )

    profile = {
        "execution_shape": "chat_json_object",
        "model_id": MODEL_ID,
        "candidate_model_ids": [],
        "judge_model_id": None,
    }
    profile_fingerprint = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    certification, created = repository.claim_workload_certification(
        "local",
        certification_id="meta-json-certification",
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
        contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
        execution_shape="chat_json_object",
        requested_model=MODEL_ID,
        profile=profile,
        profile_fingerprint=profile_fingerprint,
        idempotency_key_hash=hashlib.sha256(b"meta-json-certification").hexdigest(),
    )
    assert created is True
    repository.complete_workload_certification(
        "local",
        str(certification["id"]),
        status="passed",
        checks={"json_object_verified": True, "actual_model_verified": True},
        warning_codes=[],
        actual_model=MODEL_ID,
    )
    return service, connection.id


def _activate_policy(service: ModelRouterService, connection_id: str) -> None:
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "meta_agent",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_json_object",
                    model_id=MODEL_ID,
                    connection_id=connection_id,
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


@pytest.mark.asyncio
async def test_managed_meta_agent_records_each_json_call_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["stream"] is False
        assert body["model"] == MODEL_ID
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                },
            },
        )

    gateway = ManagedMetaAgentGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    assert gateway.routing_mode() == "managed_required"
    run = gateway.start_run(parent_run_reference="meta-parent-run")
    first = await run.complete_json(
        logical_call_key="task-plan",
        call_sequence=1,
        model_id=MODEL_ID,
        system_prompt="private system prompt",
        user_prompt="private user goal",
        temperature=0.2,
        max_tokens=4096,
    )
    second = await run.complete_json(
        logical_call_key="blueprint",
        call_sequence=2,
        model_id=MODEL_ID,
        system_prompt="private blueprint prompt",
        user_prompt="private capability snapshot",
        temperature=0.2,
        max_tokens=8192,
    )
    run.finish("passed")

    assert json.loads(first) == {"result": "ok"}
    assert json.loads(second) == {"result": "ok"}
    assert len(requests) == 2
    summary = run.receipt_summary()
    assert summary.status == "passed"
    assert summary.call_count == 2
    assert [item.call_sequence for item in summary.calls] == [1, 2]
    assert all(item.total_tokens == 15 for item in summary.calls)
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["runs"][0]["parent_run_reference"] == "meta-parent-run"
    assert [item["status"] for item in receipts["calls"]] == ["passed", "passed"]
    database = service.repository.database_path.read_bytes()
    assert b"private user goal" not in database
    assert b"private capability snapshot" not in database
    assert b'{"result":"ok"}' not in database
    assert PROVIDER_SECRET.encode() not in database


@pytest.mark.asyncio
async def test_managed_meta_agent_preflight_failure_is_not_counted_as_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    post_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        raise AssertionError("missing binding must fail before Provider dispatch")

    gateway = ManagedMetaAgentGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    run = gateway.start_run(parent_run_reference="meta-preflight")
    with pytest.raises(ManagedMetaAgentRoutingError) as blocked:
        await run.complete_json(
            logical_call_key="unbound-plan",
            call_sequence=1,
            model_id="provider/unbound-model",
            system_prompt="private prompt",
            user_prompt="private goal",
            temperature=0.2,
            max_tokens=4096,
        )
    run.finish("failed", reason_code=blocked.value.code)

    assert blocked.value.code == "provider_workload_binding_missing"
    assert post_count == 0
    summary = run.receipt_summary()
    assert summary.call_count == 0
    assert len(summary.calls) == 1
    assert summary.calls[0].dispatched is False
    assert service.repository.list_workload_receipts("local")["calls"] == []


@pytest.mark.asyncio
async def test_managed_meta_agent_model_mismatch_fails_after_one_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "provider/other-model",
                "choices": [{"message": {"content": '{"result":"wrong"}'}}],
            },
        )

    gateway = ManagedMetaAgentGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    run = gateway.start_run(parent_run_reference="meta-mismatch")
    with pytest.raises(ManagedMetaAgentRoutingError) as mismatch:
        await run.complete_json(
            logical_call_key="task-plan",
            call_sequence=1,
            model_id=MODEL_ID,
            system_prompt="private prompt",
            user_prompt="private goal",
            temperature=0.2,
            max_tokens=4096,
        )
    run.finish("failed", reason_code=mismatch.value.code)

    assert mismatch.value.code == "provider_workload_actual_model_mismatch"
    assert len(requests) == 1
    receipt = service.repository.list_workload_receipts("local")["calls"][0]
    assert receipt["dispatched"] == 1
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "provider_workload_actual_model_mismatch"
    summary = run.receipt_summary()
    assert summary.call_count == 1
    assert summary.calls[0].dispatched is True


@pytest.mark.asyncio
async def test_managed_meta_agent_duplicate_logical_call_is_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    post_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
            },
        )

    gateway = ManagedMetaAgentGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    run = gateway.start_run(parent_run_reference="meta-no-replay")
    await run.complete_json(
        logical_call_key="same-call",
        call_sequence=1,
        model_id=MODEL_ID,
        system_prompt="private prompt",
        user_prompt="private goal",
        temperature=0.2,
        max_tokens=4096,
    )
    with pytest.raises(ManagedMetaAgentRoutingError) as replay:
        await run.complete_json(
            logical_call_key="same-call",
            call_sequence=2,
            model_id=MODEL_ID,
            system_prompt="private prompt",
            user_prompt="private goal",
            temperature=0.2,
            max_tokens=4096,
        )
    run.finish("failed", reason_code=replay.value.code)

    assert replay.value.code == "provider_workload_logical_call_replay_blocked"
    assert post_count == 1
    receipts = service.repository.list_workload_receipts("local")
    assert len(receipts["calls"]) == 1


@pytest.mark.asyncio
async def test_managed_meta_agent_invalid_json_fails_without_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    post_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        post_count += 1
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [{"message": {"content": "not-json"}}],
            },
        )

    gateway = ManagedMetaAgentGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    run = gateway.start_run(parent_run_reference="meta-invalid-json")
    with pytest.raises(ManagedMetaAgentRoutingError) as invalid:
        await run.complete_json(
            logical_call_key="task-plan",
            call_sequence=1,
            model_id=MODEL_ID,
            system_prompt="private prompt",
            user_prompt="private goal",
            temperature=0.2,
            max_tokens=4096,
        )
    run.finish("failed", reason_code=invalid.value.code)

    assert invalid.value.code == "provider_workload_json_object_invalid"
    assert post_count == 1
    receipt = service.repository.list_workload_receipts("local")["calls"][0]
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "provider_workload_json_object_invalid"
    assert run.receipt_summary().calls[0].dispatched is True


@pytest.mark.asyncio
async def test_managed_meta_agent_cancellation_after_dispatch_is_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    call_service = ProviderWorkloadCallService(service)
    dispatch_started = asyncio.Event()
    never_finishes = asyncio.Event()
    send_count = 0

    async def stalled_send(_client, _request):
        nonlocal send_count
        send_count += 1
        dispatch_started.set()
        await never_finishes.wait()
        raise AssertionError("cancelled dispatch must not resume")

    monkeypatch.setattr(
        call_service.transport, "send_authorized_stream", stalled_send
    )
    run = ManagedMetaAgentGateway(call_service).start_run(
        parent_run_reference="meta-cancelled"
    )
    task = asyncio.create_task(
        run.complete_json(
            logical_call_key="task-plan",
            call_sequence=1,
            model_id=MODEL_ID,
            system_prompt="private prompt",
            user_prompt="private goal",
            temperature=0.2,
            max_tokens=4096,
        )
    )
    await asyncio.wait_for(dispatch_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run.finish("cancelled", reason_code="provider_workload_call_cancelled")

    assert send_count == 1
    receipt = service.repository.list_workload_receipts("local")["calls"][0]
    assert receipt["dispatched"] == 1
    assert receipt["status"] == "cancelled"
    assert receipt["error_code"] == "provider_workload_call_cancelled"
    assert run.receipt_summary().calls[0].dispatched is True
