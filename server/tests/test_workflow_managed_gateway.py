import hashlib
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workflow_gateway import (
    ManagedWorkflowGateway,
    ManagedWorkflowRoutingError,
)
from server.model_router.workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadControlService,
)
import server.model_router.workload_control as workload_control_module


MODEL_ID = "provider/workflow-model"
PROVIDER_SECRET = "workflow-provider-secret"


def _profile(execution_shape: str) -> tuple[dict[str, object], str]:
    value: dict[str, object] = {
        "execution_shape": execution_shape,
        "model_id": MODEL_ID,
        "candidate_model_ids": [],
        "judge_model_id": None,
    }
    fingerprint = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return value, fingerprint


def _qualified_router(tmp_path: Path) -> tuple[ModelRouterService, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Workflow Provider",
            kind="openai_compatible",
            base_url="https://workflow-provider.example/v1",
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
        catalog_fingerprint="workflow-catalog",
        observed_at="2026-08-23T00:00:00+00:00",
    )
    chat_cert, created = repository.claim_chat_certification(
        "local",
        certification_id="workflow-chat-text-cert",
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
        contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
        capability="chat_text",
        requested_model=MODEL_ID,
        idempotency_key_hash=hashlib.sha256(b"workflow-chat-text").hexdigest(),
    )
    assert created is True
    repository.complete_chat_certification(
        "local",
        str(chat_cert["id"]),
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
    for execution_shape in ("chat_text_unary", "chat_json_object"):
        profile, profile_fingerprint = _profile(execution_shape)
        certification, created = repository.claim_workload_certification(
            "local",
            certification_id=f"workflow-{execution_shape}-cert",
            connection_id=connection.id,
            connection_fingerprint=fingerprint,
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            execution_shape=execution_shape,
            requested_model=MODEL_ID,
            profile=profile,
            profile_fingerprint=profile_fingerprint,
            idempotency_key_hash=hashlib.sha256(
                f"workflow-{execution_shape}".encode("utf-8")
            ).hexdigest(),
        )
        assert created is True
        repository.complete_workload_certification(
            "local",
            str(certification["id"]),
            status="passed",
            checks={
                "content_observed": True,
                "actual_model_verified": True,
                "json_object_verified": execution_shape == "chat_json_object",
            },
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


def _activate(
    service: ModelRouterService,
    connection_id: str,
    entry_id: str,
) -> None:
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=MODEL_ID,
                    connection_id=connection_id,
                )
                for shape in ("chat_text", "chat_text_unary", "chat_json_object")
            ],
        ),
    )
    active = control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert active.effective_status == "managed_required"


@pytest.fixture
def qualified_interactive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModelRouterService, str]:
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_WORKFLOW_LLM_ENABLED", "true")
    monkeypatch.setattr(
        workload_control_module,
        "DATA_PLANE_INTEGRATED_ENTRIES",
        frozenset(
            {
                "agent_shadow",
                "meta_agent",
                "workflow_interactive_llm",
            }
        ),
    )
    _activate(service, connection_id, "workflow_interactive_llm")
    return service, connection_id


def test_stable_node_run_blocks_recovery_replay(
    qualified_interactive: tuple[ModelRouterService, str],
) -> None:
    service, _ = qualified_interactive
    gateway = ManagedWorkflowGateway.for_router(service)

    first = gateway.start_node_run(
        source_kind="workflow_classic",
        execution_reference="workflow-task-1",
        node_id="llm-node",
    )
    first.finish("failed", reason_code="test_preflight_stop")
    with pytest.raises(ManagedWorkflowRoutingError) as replay:
        gateway.start_node_run(
            source_kind="workflow_classic",
            execution_reference="workflow-task-1",
            node_id="llm-node",
        )

    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert replay.value.receipt is not None
    assert replay.value.receipt["call_count"] == 0
    receipts = service.repository.list_workload_receipts("local")
    assert len(receipts["runs"]) == 1
    assert receipts["runs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_workflow_gateway_streams_and_records_all_execution_shapes(
    qualified_interactive: tuple[ModelRouterService, str],
) -> None:
    service, _ = qualified_interactive
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if body["stream"]:
            return httpx.Response(
                200,
                content=(
                    b'data: {"model":"provider/workflow-model","choices":'
                    b'[{"delta":{"content":"hello "},"finish_reason":null}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":"world"},'
                    b'"finish_reason":"stop"}],"usage":{"prompt_tokens":7,'
                    b'"completion_tokens":2,"total_tokens":9}}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )
        content = (
            '{"value":"ok"}'
            if body.get("response_format") == {"type": "json_object"}
            else "category-a"
        )
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "total_tokens": 6,
                },
            },
        )

    gateway = ManagedWorkflowGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    stream_run = gateway.start_node_run(
        source_kind="workflow_classic",
        execution_reference="workflow-task-stream",
        node_id="llm-node",
    )
    chunks = [
        item
        async for item in stream_run.stream_text(
            logical_call_key="llm:primary",
            call_sequence=1,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private prompt"}],
            temperature=0.7,
            max_tokens=128,
        )
    ]
    stream_run.finish("passed")

    unary_run = gateway.start_node_run(
        source_kind="workflow_classic",
        execution_reference="workflow-task-unary",
        node_id="classifier-node",
    )
    unary = await unary_run.complete_text_unary(
        logical_call_key="question_classifier:model_fallback",
        call_sequence=1,
        model_id=MODEL_ID,
        messages=[{"role": "user", "content": "private classifier input"}],
        temperature=0,
        max_tokens=64,
    )
    unary_run.finish("passed")

    json_run = gateway.start_node_run(
        source_kind="workflow_classic",
        execution_reference="workflow-task-json",
        node_id="extractor-node",
    )
    extracted = await json_run.complete_json_object(
        logical_call_key="parameter_extractor:initial",
        call_sequence=1,
        model_id=MODEL_ID,
        messages=[{"role": "user", "content": "private extraction input"}],
        temperature=0,
        max_tokens=128,
    )
    json_run.finish("passed")

    assert chunks == ["hello ", "world"]
    assert unary == "category-a"
    assert json.loads(extracted) == {"value": "ok"}
    assert len(requests) == 3
    assert stream_run.receipt_summary()["calls"][0]["total_tokens"] == 9
    assert unary_run.receipt_summary()["call_count"] == 1
    assert json_run.receipt_summary()["call_count"] == 1
    database = service.repository.database_path.read_bytes()
    assert b"private prompt" not in database
    assert b"private classifier input" not in database
    assert b"private extraction input" not in database
    assert PROVIDER_SECRET.encode() not in database


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            httpx.Response(401, json={"error": {"message": "secret upstream body"}}),
            "provider_workload_http_401",
        ),
        (
            httpx.Response(
                200,
                content=(
                    b'data: {"model":"different/model","choices":'
                    b'[{"delta":{"content":"wrong"},"finish_reason":"stop"}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            ),
            "provider_workload_actual_model_mismatch",
        ),
        (
            httpx.Response(
                200,
                content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            ),
            "provider_workload_missing_terminal",
        ),
    ],
)
async def test_stream_failure_is_one_post_and_never_replayed(
    qualified_interactive: tuple[ModelRouterService, str],
    response: httpx.Response,
    expected_code: str,
) -> None:
    service, _ = qualified_interactive
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    gateway = ManagedWorkflowGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    run = gateway.start_node_run(
        source_kind="workflow_classic",
        execution_reference=f"failure-{expected_code}",
        node_id="llm-node",
    )
    with pytest.raises(ManagedWorkflowRoutingError) as failure:
        _ = [
            item
            async for item in run.stream_text(
                logical_call_key="llm:primary",
                call_sequence=1,
                model_id=MODEL_ID,
                messages=[{"role": "user", "content": "private"}],
                temperature=0.7,
                max_tokens=128,
            )
        ]
    run.finish("failed", reason_code=failure.value.code)

    assert failure.value.code == expected_code
    assert len(requests) == 1
    assert run.receipt_summary()["call_count"] == 1
    with pytest.raises(ManagedWorkflowRoutingError) as replay:
        gateway.start_node_run(
            source_kind="workflow_classic",
            execution_reference=f"failure-{expected_code}",
            node_id="llm-node",
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_stream_cancel_closes_one_post_and_blocks_replay(
    qualified_interactive: tuple[ModelRouterService, str],
) -> None:
    service, _ = qualified_interactive
    requests: list[httpx.Request] = []
    release = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                b'data: {"model":"provider/workflow-model","choices":'
                b'[{"delta":{"content":"first"},"finish_reason":null}]}\n\n'
            )
            await release.wait()
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=BlockingStream())

    gateway = ManagedWorkflowGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    run = gateway.start_node_run(
        source_kind="workflow_classic",
        execution_reference="cancelled-stream",
        node_id="llm-node",
    )
    cancel_event = asyncio.Event()
    stream = run.stream_text(
        logical_call_key="llm:primary",
        call_sequence=1,
        model_id=MODEL_ID,
        messages=[{"role": "user", "content": "private"}],
        temperature=0.7,
        max_tokens=128,
        cancel_event=cancel_event,
    )
    assert await anext(stream) == "first"
    cancel_event.set()
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)
    run.finish("cancelled", reason_code="provider_workload_call_cancelled")

    assert len(requests) == 1
    receipt = run.receipt_summary()
    assert receipt["status"] == "cancelled"
    assert receipt["calls"][0]["status"] == "cancelled"
    assert receipt["calls"][0]["dispatched"] is True
    with pytest.raises(ManagedWorkflowRoutingError) as replay:
        gateway.start_node_run(
            source_kind="workflow_classic",
            execution_reference="cancelled-stream",
            node_id="llm-node",
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_deployment_restart_blocks_completed_call_before_node_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_WORKFLOW_LLM_ENABLED", "true")
    monkeypatch.setenv("MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED", "true")
    _activate(service, connection_id, "workflow_deployment_llm")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(
                b'data: {"model":"provider/workflow-model","choices":'
                b'[{"delta":{"content":"completed upstream"},'
                b'"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    gateway = ManagedWorkflowGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    interrupted = gateway.start_node_run(
        source_kind="workflow_deployment",
        execution_reference="deployment-execution-1",
        node_id="llm-node",
    )
    chunks = [
        item
        async for item in interrupted.stream_text(
            logical_call_key="llm:primary",
            call_sequence=1,
            model_id=MODEL_ID,
            messages=[{"role": "user", "content": "private deployment input"}],
            temperature=0.7,
            max_tokens=128,
        )
    ]
    assert chunks == ["completed upstream"]
    assert interrupted.status == "running"
    assert len(requests) == 1

    restarted_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted_service = ModelRouterService(
        restarted_repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    restarted = ManagedWorkflowGateway.for_router(restarted_service)
    with pytest.raises(ManagedWorkflowRoutingError) as replay:
        restarted.start_node_run(
            source_kind="workflow_deployment",
            execution_reference="deployment-execution-1",
            node_id="llm-node",
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(requests) == 1

    fresh = restarted.start_node_run(
        source_kind="workflow_deployment",
        execution_reference="deployment-execution-2",
        node_id="llm-node",
    )
    fresh.finish("failed", reason_code="test_no_paid_rerun")
    assert len(requests) == 1
