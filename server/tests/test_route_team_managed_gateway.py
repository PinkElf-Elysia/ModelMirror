from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.egress import ProviderEgressError
from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.route_team_gateway import ManagedRouteTeamGateway
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError
from server.model_router.workload_control import ProviderWorkloadControlService


MODEL_ID = "provider/route-team-model"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sse(
    text: str = "managed answer", *, actual_model: str = MODEL_ID
) -> bytes:
    chunks = [
        {
            "model": actual_model,
            "choices": [{"delta": {"content": text}, "finish_reason": None}],
        },
        {
            "model": actual_model,
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
        },
    ]
    return (
        "".join(f"data: {json.dumps(item)}\n\n" for item in chunks)
        + "data: [DONE]\n\n"
    ).encode("utf-8")


def _gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry_id: str,
    handler,
) -> tuple[ManagedRouteTeamGateway, SQLiteRouterRepository]:
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Route Team Provider",
            kind="openrouter",
            base_url="https://provider.example/v1",
            api_key="test-route-team-key",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-25T00:00:00+00:00",
    )
    connection_fingerprint = repository.connection_config_fingerprint(
        "local", connection.id
    )
    repository.claim_catalog_refresh(
        "local",
        refresh_id="refresh-r6i",
        connection_id=connection.id,
        connection_fingerprint=connection_fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        "refresh-r6i",
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
        catalog_fingerprint="catalog-r6i",
        observed_at="2026-08-25T00:00:00+00:00",
    )
    certification, created = repository.claim_chat_certification(
        "local",
        certification_id="route-team-chat-cert",
        connection_id=connection.id,
        connection_fingerprint=connection_fingerprint,
        contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
        capability="chat_text",
        requested_model=MODEL_ID,
        idempotency_key_hash=_fingerprint({"route-team": MODEL_ID}),
    )
    assert created is True
    repository.complete_chat_certification(
        "local",
        str(certification["id"]),
        status="passed",
        checks={
            "catalog_contains_model": True,
            "http_2xx": True,
            "content_observed": True,
            "response_complete": True,
            "terminal_observed": True,
        },
        warning_codes=[],
        actual_model=MODEL_ID,
    )

    flag = (
        "MODEL_CONTROL_ROUTE_AGENT_ENABLED"
        if entry_id == "route_agent"
        else "MODEL_CONTROL_TEAM_CHAT_ENABLED"
    )
    monkeypatch.setenv(flag, "true")
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    connection_id=connection.id,
                    model_id=MODEL_ID,
                    execution_shape="chat_text",
                )
            ],
        ),
    )
    control.activate(
        entry_id,
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            acknowledge_fail_closed=True,
            no_open_p0_p1=True,
        ),
    )
    return (
        ManagedRouteTeamGateway.for_router(
            service,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), trust_env=False
            ),
        ),
        repository,
    )


@pytest.mark.asyncio
async def test_route_agent_uses_one_exact_managed_post_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=_sse())

    gateway, _repository = _gateway(
        tmp_path, monkeypatch, entry_id="route_agent", handler=handler
    )
    run = gateway.start_run("route_agent")
    plan = await run.prepare_plan(
        model_id=MODEL_ID, logical_call_keys=["route-answer:1"]
    )
    output = ""
    async for delta in run.stream_text(
        plan[0],
        messages=[{"role": "user", "content": "route me"}],
        temperature=0.2,
        max_tokens=128,
    ):
        output += delta
    receipt = run.finish("passed")

    assert output == "managed answer"
    assert len(payloads) == 1
    assert payloads[0]["model"] == MODEL_ID
    assert receipt["entry_id"] == "route_agent"
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["status"] == "passed"


@pytest.mark.asyncio
async def test_team_chat_preflights_all_members_and_summary_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(f"answer-{len(payloads)}"))

    gateway, _repository = _gateway(
        tmp_path, monkeypatch, entry_id="team_chat", handler=handler
    )
    run = gateway.start_run("team_chat")
    plan = await run.prepare_plan(
        model_id=MODEL_ID,
        logical_call_keys=["member:1", "member:2", "summary:3"],
    )
    assert payloads == []

    outputs: list[str] = []
    for prepared in plan:
        text = ""
        async for delta in run.stream_text(
            prepared,
            messages=[{"role": "user", "content": "planned team call"}],
            temperature=0.2,
            max_tokens=128,
        ):
            text += delta
        outputs.append(text)
    receipt = run.finish("passed")

    assert outputs == ["answer-1", "answer-2", "answer-3"]
    assert len(payloads) == 3
    assert receipt["entry_id"] == "team_chat"
    assert receipt["call_count"] == 3
    assert [call["call_sequence"] for call in receipt["calls"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_team_preflight_failure_sends_zero_provider_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse())

    gateway, repository = _gateway(
        tmp_path, monkeypatch, entry_id="team_chat", handler=handler
    )
    original_prepare = gateway.call_service.prepare_call

    async def fail_summary(**kwargs):
        if kwargs["call_sequence"] == 3:
            raise RouterServiceError(
                "provider_workload_binding_missing",
                "summary binding missing",
                status_code=409,
            )
        return await original_prepare(**kwargs)

    monkeypatch.setattr(gateway.call_service, "prepare_call", fail_summary)
    run = gateway.start_run("team_chat")
    with pytest.raises(ManagedWorkflowRoutingError) as caught:
        await run.prepare_plan(
            model_id=MODEL_ID,
            logical_call_keys=["member:1", "member:2", "summary:3"],
        )

    assert caught.value.code == "provider_workload_binding_missing"
    assert requests == []
    receipt = caught.value.receipt
    assert receipt["call_count"] == 0
    assert all(call["dispatched"] is False for call in receipt["calls"])
    stored = repository.list_workload_receipts("local")
    assert all(row["status"] != "running" for row in stored["runs"])
    assert all(row["status"] != "running" for row in stored["calls"])


@pytest.mark.asyncio
async def test_team_member_failure_never_dispatches_summary_or_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_models.append(json.loads(request.content)["model"])
        return httpx.Response(503)

    gateway, _repository = _gateway(
        tmp_path, monkeypatch, entry_id="team_chat", handler=handler
    )
    run = gateway.start_run("team_chat")
    plan = await run.prepare_plan(
        model_id=MODEL_ID,
        logical_call_keys=["member:1", "member:2", "summary:3"],
    )
    with pytest.raises(ManagedWorkflowRoutingError) as caught:
        async for _delta in run.stream_text(
            plan[0],
            messages=[{"role": "user", "content": "first member"}],
            temperature=0.2,
            max_tokens=128,
        ):
            pass
    run.abandon(plan[1:], code="provider_workload_team_plan_aborted")
    receipt = run.finish(run.failure_status(), reason_code=caught.value.code)

    assert requested_models == [MODEL_ID]
    assert receipt["call_count"] == 1
    assert [call["dispatched"] for call in receipt["calls"]] == [True, False, False]
    assert all(call["model_id"] == MODEL_ID for call in receipt["calls"])


def test_disabled_entry_keeps_legacy_without_control_plane_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODEL_CONTROL_ROUTE_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("MODEL_CONTROL_TEAM_CHAT_ENABLED", raising=False)
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    gateway = ManagedRouteTeamGateway.for_router(ModelRouterService(repository))

    assert gateway.routing_mode("route_agent") == "legacy"
    assert gateway.routing_mode("team_chat") == "legacy"


@pytest.mark.asyncio
async def test_policy_drift_after_full_preflight_sends_zero_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse())

    gateway, repository = _gateway(
        tmp_path, monkeypatch, entry_id="route_agent", handler=handler
    )
    run = gateway.start_run("route_agent")
    plan = await run.prepare_plan(
        model_id=MODEL_ID, logical_call_keys=["route-answer:1"]
    )
    connection_id = repository.list_connections("local")[0].id
    repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(api_key="rotated-after-preflight"),
    )

    with pytest.raises(ManagedWorkflowRoutingError) as caught:
        async for _delta in run.stream_text(
            plan[0],
            messages=[{"role": "user", "content": "must not dispatch"}],
            temperature=0.2,
            max_tokens=128,
        ):
            pass
    receipt = run.finish(run.failure_status(), reason_code=caught.value.code)

    assert caught.value.code in {
        "provider_workload_policy_not_active",
        "provider_workload_binding_changed",
    }
    assert requests == []
    assert receipt["call_count"] == 0
    assert receipt["calls"][0]["dispatched"] is False


@pytest.mark.asyncio
async def test_egress_is_reauthorized_immediately_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse())

    gateway, _repository = _gateway(
        tmp_path, monkeypatch, entry_id="team_chat", handler=handler
    )
    run = gateway.start_run("team_chat")
    plan = await run.prepare_plan(
        model_id=MODEL_ID, logical_call_keys=["member:1", "summary:2"]
    )

    async def reject_reauthorization(_target):
        raise ProviderEgressError(
            "provider_egress_forbidden_address",
            "target changed to a forbidden address",
        )

    monkeypatch.setattr(
        gateway.call_service.transport,
        "authorize_managed_target",
        reject_reauthorization,
    )
    with pytest.raises(ManagedWorkflowRoutingError) as caught:
        async for _delta in run.stream_text(
            plan[0],
            messages=[{"role": "user", "content": "must not dispatch"}],
            temperature=0.2,
            max_tokens=128,
        ):
            pass
    run.abandon(plan[1:], code="provider_workload_team_plan_aborted")
    receipt = run.finish("failed", reason_code=caught.value.code)

    assert caught.value.code == "provider_egress_forbidden_address"
    assert requests == []
    assert receipt["call_count"] == 0
    assert all(call["dispatched"] is False for call in receipt["calls"])


@pytest.mark.asyncio
async def test_actual_model_mismatch_is_one_failed_post_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_models.append(json.loads(request.content)["model"])
        return httpx.Response(
            200,
            content=_sse(actual_model="provider/unexpected-model"),
        )

    gateway, _repository = _gateway(
        tmp_path, monkeypatch, entry_id="route_agent", handler=handler
    )
    run = gateway.start_run("route_agent")
    plan = await run.prepare_plan(
        model_id=MODEL_ID, logical_call_keys=["route-answer:1"]
    )
    with pytest.raises(ManagedWorkflowRoutingError) as caught:
        async for _delta in run.stream_text(
            plan[0],
            messages=[{"role": "user", "content": "exact model required"}],
            temperature=0.2,
            max_tokens=128,
        ):
            pass
    receipt = run.finish(run.failure_status(), reason_code=caught.value.code)

    assert caught.value.code == "provider_workload_actual_model_mismatch"
    assert requested_models == [MODEL_ID]
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_cancel_after_first_delta_closes_one_post_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    release = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                b'data: {"model":"provider/route-team-model","choices":'
                b'[{"delta":{"content":"first"},"finish_reason":null}]}\n\n'
            )
            await release.wait()
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=BlockingStream())

    gateway, _repository = _gateway(
        tmp_path, monkeypatch, entry_id="route_agent", handler=handler
    )
    run = gateway.start_run("route_agent")
    plan = await run.prepare_plan(
        model_id=MODEL_ID, logical_call_keys=["route-answer:1"]
    )
    stream = run.stream_text(
        plan[0],
        messages=[{"role": "user", "content": "cancel me"}],
        temperature=0.2,
        max_tokens=128,
    )
    assert await anext(stream) == "first"
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    receipt = run.finish(
        "cancelled", reason_code="provider_workload_call_cancelled"
    )

    assert len(requests) == 1
    assert receipt["status"] == "cancelled"
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["status"] == "cancelled"
