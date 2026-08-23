from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from server.agent_upstream.managed_gateway import ManagedShadowGateway
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
    ProviderWorkloadCallService,
    ProviderWorkloadControlService,
)
from server.agent_workspace.gateway import GatewayRequestError


MODEL_ID = "provider/tool-model"
PROVIDER_SECRET = "managed-shadow-provider-secret"


def _qualified_router(tmp_path: Path) -> tuple[ModelRouterService, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Shadow Provider",
            kind="openai_compatible",
            base_url="https://shadow-provider.example/v1",
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
        catalog_fingerprint="shadow-catalog",
        observed_at="2026-08-23T00:00:00+00:00",
    )
    certification, created = repository.claim_chat_certification(
        "local",
        certification_id="shadow-tools-certification",
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
        contract_version="modelmirror-provider-chat-v1",
        capability="chat_tools",
        requested_model=MODEL_ID,
        idempotency_key_hash=hashlib.sha256(b"shadow-tools").hexdigest(),
    )
    assert created is True
    repository.complete_chat_certification(
        "local",
        str(certification["id"]),
        status="passed",
        checks={"capability_verified": True},
        warning_codes=[],
        actual_model=MODEL_ID,
    )
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    return service, connection.id


def _activate_shadow_policy(service: ModelRouterService, connection_id: str) -> None:
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "agent_shadow",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_tools",
                    model_id=MODEL_ID,
                    connection_id=connection_id,
                )
            ],
        ),
    )
    active = control.activate(
        "agent_shadow",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert active.effective_status == "managed_required"


@pytest.mark.asyncio
async def test_managed_shadow_exact_binding_dispatches_once_and_records_no_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_AGENT_SHADOW_ENABLED", "true")
    _activate_shadow_policy(service, connection_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == MODEL_ID
        assert payload["tool_choice"] == "auto"
        assert request.headers["host"] == "shadow-provider.example"
        assert request.headers["authorization"] == f"Bearer {PROVIDER_SECRET}"
        body = "\n".join(
            [
                "data: "
                + json.dumps(
                    {
                        "model": MODEL_ID,
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "function": {
                                                "name": "read_file",
                                                "arguments": '{"file_path":"README.md"}',
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        },
                    }
                ),
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(
            200,
            text=body,
            headers={"Content-Type": "text/event-stream"},
        )

    call_service = ProviderWorkloadCallService(service)
    gateway = ManagedShadowGateway(
        call_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    assert gateway.routing_mode() == "managed_required"
    assert gateway.resolve_exact_model(MODEL_ID) == MODEL_ID
    assert gateway.resolve_exact_model("provider/other-model") is None
    run_id = gateway.start_run(parent_run_reference="shadow-run-1")
    turn = await gateway.stream_turn(
        workload_run_id=run_id,
        logical_call_key="worker-model-request-1",
        call_sequence=1,
        model_id=MODEL_ID,
        messages=[{"role": "user", "content": "private prompt"}],
        tools=[{"type": "function", "function": {"name": "read_file"}}],
        max_tokens=128,
        thinking_level="medium",
        timeout_ms=30_000,
        on_delta=lambda *_args: None,
    )

    assert turn.model_id == MODEL_ID
    assert turn.tool_calls[0].name == "read_file"
    assert turn.total_tokens == 14
    with pytest.raises(GatewayRequestError):
        await gateway.stream_turn(
            workload_run_id=run_id,
            logical_call_key="worker-model-request-1",
            call_sequence=2,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private prompt"}],
            tools=[],
            max_tokens=128,
            thinking_level="medium",
            timeout_ms=30_000,
            on_delta=lambda *_args: None,
        )
    gateway.finish_run(run_id, "candidate_ready")

    assert len(requests) == 1
    receipts = service.repository.list_workload_receipts("local")
    assert len(receipts["runs"]) == 1
    assert len(receipts["calls"]) == 1
    assert receipts["runs"][0]["parent_run_reference"] == "shadow-run-1"
    assert receipts["runs"][0]["status"] == "passed"
    assert receipts["calls"][0]["status"] == "passed"
    assert receipts["calls"][0]["dispatched"] == 1
    assert receipts["calls"][0]["actual_model"] == MODEL_ID
    database = service.repository.database_path.read_bytes()
    assert b"private prompt" not in database
    assert b"README.md" not in database
    assert PROVIDER_SECRET.encode() not in database


@pytest.mark.asyncio
async def test_managed_shadow_model_mismatch_fails_without_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_AGENT_SHADOW_ENABLED", "true")
    _activate_shadow_policy(service, connection_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"model":"provider/other","choices":[{"delta":{"content":"x"},'
                '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    gateway = ManagedShadowGateway(
        ProviderWorkloadCallService(service),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    run_id = gateway.start_run(parent_run_reference="shadow-run-mismatch")
    with pytest.raises(GatewayRequestError, match="different model"):
        await gateway.stream_turn(
            workload_run_id=run_id,
            logical_call_key="model-1",
            call_sequence=1,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private prompt"}],
            tools=[],
            max_tokens=128,
            thinking_level="medium",
            timeout_ms=30_000,
            on_delta=lambda *_args: None,
        )
    gateway.finish_run(run_id, "failed")

    assert len(requests) == 1
    receipt = service.repository.list_workload_receipts("local")["calls"][0]
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "provider_workload_actual_model_mismatch"


@pytest.mark.asyncio
async def test_managed_shadow_cancellation_after_dispatch_is_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_AGENT_SHADOW_ENABLED", "true")
    _activate_shadow_policy(service, connection_id)
    call_service = ProviderWorkloadCallService(service)
    dispatch_started = asyncio.Event()
    never_finishes = asyncio.Event()
    sends = 0

    async def stalled_send(_client, _request):
        nonlocal sends
        sends += 1
        dispatch_started.set()
        await never_finishes.wait()
        raise AssertionError("cancelled dispatch must not resume")

    monkeypatch.setattr(
        call_service.transport, "send_authorized_stream", stalled_send
    )
    gateway = ManagedShadowGateway(call_service)
    run_id = gateway.start_run(parent_run_reference="shadow-run-cancel")
    task = asyncio.create_task(
        gateway.stream_turn(
            workload_run_id=run_id,
            logical_call_key="model-cancel-1",
            call_sequence=1,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private prompt"}],
            tools=[],
            max_tokens=128,
            thinking_level="medium",
            timeout_ms=30_000,
            on_delta=lambda *_args: None,
        )
    )
    await asyncio.wait_for(dispatch_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(GatewayRequestError):
        await gateway.stream_turn(
            workload_run_id=run_id,
            logical_call_key="model-cancel-1",
            call_sequence=2,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private prompt"}],
            tools=[],
            max_tokens=128,
            thinking_level="medium",
            timeout_ms=30_000,
            on_delta=lambda *_args: None,
        )
    gateway.finish_run(run_id, "stopped")

    assert sends == 1
    receipt = service.repository.list_workload_receipts("local")["calls"][0]
    assert receipt["dispatched"] == 1
    assert receipt["status"] == "cancelled"
    assert receipt["error_code"] == "provider_workload_call_cancelled"


@pytest.mark.asyncio
async def test_managed_shadow_incomplete_stream_fails_closed_after_one_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_AGENT_SHADOW_ENABLED", "true")
    _activate_shadow_policy(service, connection_id)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"model":"provider/tool-model",'
                '"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    gateway = ManagedShadowGateway(
        ProviderWorkloadCallService(service),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    run_id = gateway.start_run(parent_run_reference="shadow-run-incomplete")
    with pytest.raises(GatewayRequestError, match="终止信号"):
        await gateway.stream_turn(
            workload_run_id=run_id,
            logical_call_key="model-incomplete-1",
            call_sequence=1,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private prompt"}],
            tools=[],
            max_tokens=128,
            thinking_level="medium",
            timeout_ms=30_000,
            on_delta=lambda *_args: None,
        )
    gateway.finish_run(run_id, "failed")

    assert len(requests) == 1
    receipt = service.repository.list_workload_receipts("local")["calls"][0]
    assert receipt["dispatched"] == 1
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "provider_workload_provider_request_failed"
