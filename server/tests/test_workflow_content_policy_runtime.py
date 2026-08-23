from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.main import app
from server.workflow_deployments import WorkflowDeploymentStore
from server.xpert_runtime.execution_store import WorkflowExecutionStore


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as value:
        yield value


@pytest.fixture(autouse=True)
def legacy_agent_path(monkeypatch: pytest.MonkeyPatch):
    main_module.request_windows.clear()
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    yield
    main_module.request_windows.clear()


def _events(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def _workflow(*, phase: str, action: str, detector: str = "literal_terms") -> dict:
    terms = ["R19_SYNTHETIC_SENTINEL"] if detector == "literal_terms" else []
    return {
        "id": "content-policy-runtime",
        "title": "content policy runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "policy",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "content_policy",
                    "runtimeMiddlewareKind": "runtime_middleware.content_policy",
                    "middlewarePriority": "100",
                    "runtimeMiddlewareConfig": {
                        "phase": phase,
                        "rules": [
                            {
                                "id": "rule_1",
                                "label": "Synthetic sentinel",
                                "detector": detector,
                                "action": action,
                                "terms": terms,
                                "caseSensitive": False,
                            }
                        ],
                    },
                },
            },
            {
                "id": "workflow_agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": "guarded-agent",
                    "modelId": "test/model",
                    "rolePrompt": "You are a test agent.",
                    "taskInput": "{{user_input}}",
                    "toolMode": "none",
                    "outputVariable": "agent_output",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_output"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "workflow_agent"},
            {
                "id": "e2",
                "source": "policy",
                "sourceHandle": "middleware-binding",
                "target": "workflow_agent",
                "targetHandle": "middleware",
            },
            {"id": "e3", "source": "workflow_agent", "target": "output"},
        ],
    }


@pytest.mark.asyncio
async def test_output_redaction_buffers_before_sse_variables_and_checkpoints(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "R19_SYNTHETIC_SENTINEL"

    async def fake_collect(*args, **kwargs):
        return f"prefix {sentinel} suffix"

    def unexpected_stream(*args, **kwargs):
        raise AssertionError("output policy must buffer rather than stream raw deltas")

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", unexpected_stream)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(phase="output", action="redact"),
            "inputs": {"user_input": "safe request"},
        },
    )

    assert response.status_code == 200, response.text
    assert sentinel not in response.text
    events = _events(response.text)
    end = next(event for event in events if event.get("event") == "workflow_end")
    assert end["variables"]["agent_output"] == "prefix [已脱敏] suffix"
    deltas = [
        event["output"]
        for event in events
        if event.get("event") == "node_delta"
        and event.get("node_id") == "workflow_agent"
    ]
    assert deltas == ["prefix [已脱敏] suffix"]

    workflow_run_id = next(
        event["run_id"] for event in events if event.get("event") == "workflow_meta"
    )
    children = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=50"
    )
    agent_run = next(
        item for item in children.json() if item["run_type"] == "workflow_agent"
    )
    checkpoints = await client.get(
        f"/api/runtime/runs/{agent_run['run_id']}/checkpoints"
    )
    assert sentinel not in json.dumps(checkpoints.json(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_output_block_exposes_only_safe_policy_identity(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "R19_SYNTHETIC_SENTINEL"

    async def fake_collect(*args, **kwargs):
        return sentinel

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(phase="output", action="block"),
            "inputs": {"user_input": "safe request"},
        },
    )

    assert response.status_code == 200, response.text
    assert sentinel not in response.text
    errors = [event for event in _events(response.text) if event.get("event") == "error"]
    assert errors
    assert errors[-1]["code"] == "content_policy_blocked_output"
    assert errors[-1]["phase"] == "output"
    assert errors[-1]["rule_id"] == "rule_1"
    workflow_run_id = next(
        event["run_id"] for event in _events(response.text)
        if event.get("event") == "workflow_meta"
    )
    children = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=50"
    )
    agent_run = next(
        item for item in children.json() if item["run_type"] == "workflow_agent"
    )
    checkpoints = await client.get(
        f"/api/runtime/runs/{agent_run['run_id']}/checkpoints"
    )
    assert sentinel not in json.dumps(checkpoints.json(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_input_block_terminates_without_retry_or_empty_output_fallback(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "R19_SYNTHETIC_SENTINEL"
    model_calls = 0

    async def forbidden_model(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("blocked input must not reach the model")

    workflow = _workflow(phase="input", action="block")
    workflow["nodes"][2]["data"].update(
        {
            "retryOnFailure": "true",
            "exceptionHandling": "empty_output",
        }
    )
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", forbidden_model)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": sentinel},
        },
    )

    assert response.status_code == 200, response.text
    assert model_calls == 0
    errors = [event for event in _events(response.text) if event.get("event") == "error"]
    assert sentinel not in response.text
    assert errors[-1]["code"] == "content_policy_blocked_input"
    assert not any(event.get("event") == "workflow_end" for event in _events(response.text))
    workflow_run_id = next(
        event["run_id"]
        for event in _events(response.text)
        if event.get("event") == "workflow_meta"
    )
    children = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=50"
    )
    agent_run = next(
        item for item in children.json() if item["run_type"] == "workflow_agent"
    )
    checkpoints = await client.get(
        f"/api/runtime/runs/{agent_run['run_id']}/checkpoints"
    )
    checkpoint_types = {item["event_type"] for item in checkpoints.json()}
    assert sentinel not in json.dumps(checkpoints.json(), ensure_ascii=False)
    assert "workflow_agent.failed_attempt" not in checkpoint_types
    assert "workflow_agent.empty_output" not in checkpoint_types


@pytest.mark.asyncio
async def test_input_block_runs_before_missing_gateway_preflight(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "R19_SYNTHETIC_SENTINEL"

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("blocked input must not reach the model")

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", forbidden_model)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(phase="input", action="block"),
            "inputs": {"user_input": sentinel},
        },
    )

    assert response.status_code == 200, response.text
    assert sentinel not in response.text
    error = next(event for event in _events(response.text) if event.get("event") == "error")
    assert error["code"] == "content_policy_blocked_input"
    assert error["phase"] == "input"
    assert error["rule_id"] == "rule_1"


@pytest.mark.asyncio
async def test_redact_only_policy_does_not_bypass_missing_gateway_preflight(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(phase="input", action="redact"),
            "inputs": {"user_input": "safe request"},
        },
    )

    assert response.status_code == 500
    assert response.json()["error"] == main_module.LLM_GATEWAY_NOT_CONFIGURED_MESSAGE


@pytest.mark.asyncio
async def test_output_policy_guards_high_confidence_memory_reply_before_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "R19_SYNTHETIC_SENTINEL"

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("high-confidence memory reply must bypass the model")

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", forbidden_model)
    result = await main_module._run_workflow_response(
        main_module.WorkflowRunRequest.model_validate(
            {
                "workflow": _workflow(phase="output", action="redact"),
                "inputs": {"user_input": "safe request"},
            }
        ),
        None,
        runtime_metadata={
            "xpert_output_agent_node_id": "workflow_agent",
            "memory_reply": {
                "answer": f"memory says {sentinel}",
                "memory_id": "memory-1",
                "confidence": 1.0,
            },
        },
    )
    chunks = [
        chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
        async for chunk in result.body_iterator
    ]
    body = b"".join(chunks).decode("utf-8")

    assert sentinel not in body
    assert "memory says [已脱敏]" in body


@pytest.mark.asyncio
async def test_input_redaction_changes_model_copy_but_not_original_variable(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_messages: list[dict] = []

    async def fake_collect(model_id, messages, **kwargs):
        captured_messages.extend(message.model_dump() for message in messages)
        return "safe response"

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(phase="both", action="redact"),
            "inputs": {"user_input": "R19_SYNTHETIC_SENTINEL"},
        },
    )

    assert response.status_code == 200, response.text
    assert captured_messages[-1]["content"] == "[已脱敏]"
    end = next(event for event in _events(response.text) if event.get("event") == "workflow_end")
    assert end["variables"]["user_input"] == "R19_SYNTHETIC_SENTINEL"
    assert end["variables"]["agent_output"] == "safe response"


@pytest.mark.asyncio
async def test_linear_content_policy_applies_to_downstream_agent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(*args, **kwargs):
        return "R19_SYNTHETIC_SENTINEL"

    workflow = _workflow(phase="output", action="redact")
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "policy"},
        {"id": "e2", "source": "policy", "target": "workflow_agent"},
        {"id": "e3", "source": "workflow_agent", "target": "output"},
    ]
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "safe request"}},
    )

    assert response.status_code == 200, response.text
    assert "R19_SYNTHETIC_SENTINEL" not in response.text
    assert "[已脱敏]" in response.text


@pytest.mark.asyncio
async def test_deployed_content_policy_block_dispatches_one_sanitized_failure(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sentinel = "R19_SYNTHETIC_SENTINEL"
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "true")

    async def fake_collect(*args, **kwargs):
        return sentinel

    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    source = deployment_store.create_project(
        {
            "id": "draft",
            "title": "guarded schedule",
            "nodes": [
                {
                    "id": "entry",
                    "type": "scheduled_start",
                    "data": {
                        "kind": "scheduled_start",
                        "scheduleType": "interval",
                        "intervalSeconds": 30,
                        "timezone": "UTC",
                        "eventVariable": "schedule_event",
                    },
                },
                {
                    "id": "policy",
                    "type": "runtime_middleware",
                    "data": {
                        "kind": "runtime_middleware",
                        "runtimeMiddlewareId": "content_policy",
                        "runtimeMiddlewareKind": "runtime_middleware.content_policy",
                        "middlewarePriority": "100",
                        "runtimeMiddlewareConfig": {
                            "phase": "output",
                            "rules": [
                                {
                                    "id": "rule_1",
                                    "label": "Synthetic sentinel",
                                    "detector": "literal_terms",
                                    "action": "block",
                                    "terms": [sentinel],
                                    "caseSensitive": False,
                                }
                            ],
                        },
                    },
                },
                {
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "guarded-agent",
                        "modelId": "test/model",
                        "rolePrompt": "You are a test agent.",
                        "taskInput": "safe request",
                        "toolMode": "none",
                        "outputVariable": "agent_output",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "agent_output"},
                },
            ],
            "edges": [
                {"id": "entry-agent", "source": "entry", "target": "agent"},
                {
                    "id": "policy-agent",
                    "source": "policy",
                    "sourceHandle": "middleware-binding",
                    "target": "agent",
                    "targetHandle": "middleware",
                },
                {"id": "agent-output", "source": "agent", "target": "output"},
            ],
        }
    )
    source_release = deployment_store.publish(source.project_id)
    deployment_store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = deployment_store.create_project(
        {
            "id": "draft",
            "title": "failure handler",
            "nodes": [
                {
                    "id": "failure-entry",
                    "type": "failure_event_entry",
                    "data": {
                        "kind": "failure_event_entry",
                        "sourceProjectIds": [source.project_id],
                        "eventVariable": "failure_event",
                    },
                },
                {
                    "id": "failure-output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "failure_event"},
                },
            ],
            "edges": [
                {
                    "id": "handler-edge",
                    "source": "failure-entry",
                    "target": "failure-output",
                }
            ],
        }
    )
    handler_release = deployment_store.publish(handler.project_id)
    deployment_store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )

    pending = deployment_store.materialize_due_schedules(now=130)[0]
    failed = await deployment_api._execute_trigger(pending, {"type": "schedule_event"})

    assert failed.status == "failed"
    assert (
        failed.error_summary
        == "content_policy_blocked_output: 内容策略在 output 阶段阻止了文本。"
    )
    assert sentinel not in json.dumps(
        deployment_store.serialize_execution(failed),
        ensure_ascii=False,
    )
    assert sentinel not in caplog.text
    dispatched = deployment_store.list_executions(handler.project_id)
    assert len(dispatched) == 1
    assert dispatched[0].trigger_summary["source_execution_id"] == failed.execution_id
    assert dispatched[0].trigger_summary["failed_node_id"] == "agent"
    assert dispatched[0].trigger_summary["suppress_failure_dispatch"] is True
    assert sentinel not in json.dumps(dispatched[0].trigger_summary, ensure_ascii=False)

    deployment_store.fail_execution(
        failed.execution_id,
        error="duplicate callback must not dispatch again",
    )
    assert len(deployment_store.list_executions(handler.project_id)) == 1
