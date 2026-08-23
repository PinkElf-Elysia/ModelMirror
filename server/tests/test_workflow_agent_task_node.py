from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app
from server.skills.application_receipts import (
    SkillApplicationObserver,
    SkillApplicationReceiptStore,
)
from server.skills.finder import SkillFinder, _fingerprint
from server.skills.package_validation import compute_skill_content_digest
from server.skills.skill_manager import InstalledSkill
from server.xpert_runtime import (
    RuntimeApprovalStore,
    RuntimeTool,
    RuntimeToolResult,
    SandboxToolsetProvider,
    SandboxWorkspaceStore,
    WorkflowExecutionStore,
)
from server.xpert_runtime.agent_strategy import (
    AgentModelError,
    AgentModelTurn,
    AgentToolCall,
)
from server.xpert_runtime.middleware import AgentMiddleware
from server.xpert_runtime.plugin_hooks_v2 import SkillHookRuntimeError
from server.xpert_runtime.todo_store import RuntimeTodoStore


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def default_to_legacy_agent_strategy(monkeypatch: pytest.MonkeyPatch):
    """Keep pre-V2 integration cases on their original runtime path."""

    main_module.request_windows.clear()
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    yield
    main_module.request_windows.clear()


@pytest.mark.asyncio
async def test_workflow_agent_task_node_creates_runtime_task(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "agent-task-workflow",
        "title": "agent task workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "agent_task",
                "type": "agent_task",
                "data": {
                    "kind": "agent_task",
                    "title": "Create agent task",
                    "taskTitle": "Plan {{user_input}}",
                    "taskInput": "Please plan: {{user_input}}",
                    "assignedAgent": "workflow-planner",
                    "outputVariable": "agent_task_id",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_task_id"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "agent_task"},
            {"id": "e2", "source": "agent_task", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "launch a support workflow"},
        },
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    workflow_run_id = workflow_meta.get("run_id")
    assert isinstance(workflow_run_id, str)
    assert workflow_run_id

    workflow_end = next(event for event in events if event.get("event") == "workflow_end")
    assert workflow_end.get("run_id") == workflow_run_id

    agent_task_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "agent_task"
    )
    task_id = agent_task_end.get("output")
    assert isinstance(task_id, str)
    assert task_id
    assert agent_task_end["variables"]["agent_task_id"] == task_id

    deltas = [
        event
        for event in events
        if event.get("event") == "node_delta" and event.get("node_id") == "agent_task"
    ]
    assert any("Agent Task" in str(event.get("output")) for event in deltas)

    detail_response = await client.get(f"/api/runtime/agent-tasks/{task_id}")
    assert detail_response.status_code == 200, detail_response.text
    payload = detail_response.json()
    assert payload["task_id"] == task_id
    assert payload["title"] == "Plan launch a support workflow"
    assert payload["input"] == "Please plan: launch a support workflow"
    assert payload["source_agent"] == "workflow"
    assert payload["assigned_agent"] == "workflow-planner"
    assert payload["metadata"]["workflow_id"] == "agent-task-workflow"
    assert payload["metadata"]["workflow_node_id"] == "agent_task"

    workflow_run_response = await client.get(f"/api/runtime/runs/{workflow_run_id}")
    assert workflow_run_response.status_code == 200
    workflow_run = workflow_run_response.json()
    assert workflow_run["run_type"] == "workflow"
    assert workflow_run["status"] == "completed"
    assert workflow_run["metadata"]["workflow_task_id"]

    agent_task_runs_response = await client.get(
        "/api/runtime/runs?run_type=agent_task&limit=50",
    )
    assert agent_task_runs_response.status_code == 200
    agent_task_runs = agent_task_runs_response.json()
    agent_task_run = next(item for item in agent_task_runs if item["source_id"] == task_id)
    assert agent_task_run["parent_run_id"] == workflow_run_id
    assert agent_task_run["metadata"]["node_id"] == "agent_task"


@pytest.mark.asyncio
async def test_workflow_agent_handoff_node_creates_runtime_handoff_and_runs(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "agent-handoff-workflow",
        "title": "agent handoff workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "agent_task",
                "type": "agent_task",
                "data": {
                    "kind": "agent_task",
                    "title": "Create agent task",
                    "taskTitle": "Plan {{user_input}}",
                    "taskInput": "Please plan: {{user_input}}",
                    "assignedAgent": "workflow-planner",
                    "outputVariable": "agent_task_id",
                },
            },
            {
                "id": "agent_handoff",
                "type": "agent_handoff",
                "data": {
                    "kind": "agent_handoff",
                    "title": "Handoff agent task",
                    "taskIdVariable": "agent_task_id",
                    "sourceAgent": "workflow-planner",
                    "targetAgent": "review-agent",
                    "reason": "Review plan for {{user_input}}",
                    "outputVariable": "agent_handoff_id",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_handoff_id"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "agent_task"},
            {"id": "e2", "source": "agent_task", "target": "agent_handoff"},
            {"id": "e3", "source": "agent_handoff", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "handoff scenario"},
        },
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    workflow_run_id = workflow_meta.get("run_id")
    assert isinstance(workflow_run_id, str)

    task_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "agent_task"
    )
    task_id = task_end.get("output")
    assert isinstance(task_id, str)
    assert task_id

    handoff_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "agent_handoff"
    )
    handoff_id = handoff_end.get("output")
    assert isinstance(handoff_id, str)
    assert handoff_id
    assert handoff_end["variables"]["agent_handoff_id"] == handoff_id

    handoff_response = await client.get(f"/api/runtime/agent-tasks/{task_id}/handoffs")
    assert handoff_response.status_code == 200, handoff_response.text
    handoffs = handoff_response.json()
    assert any(item["handoff_id"] == handoff_id for item in handoffs)

    task_runs_response = await client.get("/api/runtime/runs?run_type=agent_task&limit=50")
    assert task_runs_response.status_code == 200
    task_runs = task_runs_response.json()
    task_run = next(item for item in task_runs if item["source_id"] == task_id)
    assert task_run["parent_run_id"] == workflow_run_id

    handoff_runs_response = await client.get(
        "/api/runtime/runs?run_type=agent_handoff&limit=50",
    )
    assert handoff_runs_response.status_code == 200
    handoff_runs = handoff_runs_response.json()
    handoff_run = next(item for item in handoff_runs if item["source_id"] == handoff_id)
    assert handoff_run["parent_run_id"] == workflow_run_id
    assert handoff_run["metadata"]["agent_task_id"] == task_id
    assert handoff_run["metadata"]["target_agent"] == "review-agent"

    child_runs_response = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=50",
    )
    assert child_runs_response.status_code == 200, child_runs_response.text
    child_runs = child_runs_response.json()
    assert any(
        item["run_type"] == "agent_task" and item["source_id"] == task_id
        for item in child_runs
    )
    assert any(
        item["run_type"] == "agent_handoff" and item["source_id"] == handoff_id
        for item in child_runs
    )

    task_checkpoints_response = await client.get(
        f"/api/runtime/runs/{task_run['run_id']}/checkpoints",
    )
    assert task_checkpoints_response.status_code == 200
    assert any(
        item["event_type"] == "agent_task.created"
        for item in task_checkpoints_response.json()
    )

    handoff_checkpoints_response = await client.get(
        f"/api/runtime/runs/{handoff_run['run_id']}/checkpoints",
    )
    assert handoff_checkpoints_response.status_code == 200
    assert any(
        item["event_type"] == "agent_handoff.created"
        for item in handoff_checkpoints_response.json()
    )


@pytest.mark.asyncio
async def test_workflow_agent_node_streams_output_and_registers_run(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}

    class FakeWorkflowLlmStream:
        token_usage = {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
            "cost_usd": 0.004,
        }

        def __aiter__(self):
            async def iterate():
                yield "agent "
                yield "result"

            return iterate()

    def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        captured["model_id"] = model_id
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return FakeWorkflowLlmStream()

    monkeypatch.setattr(
        "server.main.get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        "server.main.stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    workflow = {
        "id": "workflow-agent-workflow",
        "title": "workflow agent workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "workflow_agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "title": "Execute workflow agent",
                    "agentName": "research-agent",
                    "modelId": "deepseek/deepseek-chat",
                    "rolePrompt": "你是研究智能体，任务来自 {{user_input}}。",
                    "taskInput": "请处理：{{user_input}}",
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
            {"id": "e2", "source": "workflow_agent", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "summarize handoff queue"},
        },
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    workflow_run_id = workflow_meta.get("run_id")
    assert isinstance(workflow_run_id, str)

    deltas = [
        event
        for event in events
        if event.get("event") == "node_delta" and event.get("node_id") == "workflow_agent"
    ]
    assert [event.get("output") for event in deltas] == ["agent ", "result"]

    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "agent result"
    assert agent_end["variables"]["agent_output"] == "agent result"

    assert captured["model_id"] == "deepseek/deepseek-chat"
    assert captured["prompt"] == "请处理：summarize handoff queue"
    assert captured["system_prompt"] == "你是研究智能体，任务来自 summarize handoff queue。"

    agent_runs_response = await client.get(
        "/api/runtime/runs?run_type=workflow_agent&limit=50",
    )
    assert agent_runs_response.status_code == 200, agent_runs_response.text
    agent_runs = agent_runs_response.json()
    agent_run = next(
        item for item in agent_runs if item["source_id"].endswith(":workflow_agent")
    )
    assert agent_run["parent_run_id"] == workflow_run_id
    assert agent_run["status"] == "completed"
    assert agent_run["metadata"]["agent_name"] == "research-agent"
    assert agent_run["metadata"]["model_id"] == "deepseek/deepseek-chat"
    assert agent_run["metadata"]["output_variable"] == "agent_output"
    assert agent_run["metadata"]["token_usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
        "input_tokens": 12,
        "output_tokens": 4,
    }
    assert "cost_usd" not in agent_run["metadata"]

    workflow_checkpoints_response = await client.get(
        f"/api/runtime/runs/{workflow_run_id}/checkpoints",
    )
    assert workflow_checkpoints_response.status_code == 200
    workflow_checkpoint_types = [
        item["event_type"] for item in workflow_checkpoints_response.json()
    ]
    assert "workflow.started" in workflow_checkpoint_types
    assert "workflow.completed" in workflow_checkpoint_types

    agent_checkpoints_response = await client.get(
        f"/api/runtime/runs/{agent_run['run_id']}/checkpoints",
    )
    assert agent_checkpoints_response.status_code == 200
    agent_checkpoint_types = [
        item["event_type"] for item in agent_checkpoints_response.json()
    ]
    assert "workflow_agent.started" in agent_checkpoint_types
    assert "workflow_agent.model_call" in agent_checkpoint_types
    assert "workflow_agent.completed" in agent_checkpoint_types
    completed_checkpoint = next(
        item
        for item in agent_checkpoints_response.json()
        if item["event_type"] == "workflow_agent.completed"
    )
    assert completed_checkpoint["metadata"]["token_usage"]["total_tokens"] == 16
    assert "cost_usd" not in completed_checkpoint["metadata"]["token_usage"]


@pytest.mark.asyncio
async def test_workflow_agent_retry_on_failure_then_succeeds(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary model failure")
        yield "retry ok"

    monkeypatch.setattr(
        "server.main.get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        "server.main.stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    workflow = _workflow_agent_strategy_workflow(
        {
            "retryOnFailure": "true",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "retry this"}},
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    assert calls == 2
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "retry ok"
    assert agent_end["variables"]["agent_output"] == "retry ok"

    workflow_run_id = next(
        event for event in events if event.get("event") == "workflow_meta"
    )["run_id"]
    agent_run = await _workflow_agent_run(client, workflow_run_id)
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "workflow_agent.failed_attempt" in checkpoints
    assert "workflow_agent.retry" in checkpoints
    assert "workflow_agent.completed" in checkpoints


@pytest.mark.asyncio
async def test_workflow_agent_fallback_model_succeeds(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_calls: list[str] = []

    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        model_calls.append(model_id)
        if model_id == "primary-model":
            raise RuntimeError("primary model failed")
        yield "fallback ok"

    monkeypatch.setattr(
        "server.main.get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        "server.main.stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    workflow = _workflow_agent_strategy_workflow(
        {
            "modelId": "primary-model",
            "fallbackModelId": "fallback-model",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "fallback this"}},
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    assert model_calls == ["primary-model", "fallback-model"]
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "fallback ok"
    assert agent_end["variables"]["agent_output"] == "fallback ok"

    workflow_run_id = next(
        event for event in events if event.get("event") == "workflow_meta"
    )["run_id"]
    agent_run = await _workflow_agent_run(client, workflow_run_id)
    assert agent_run["metadata"]["model_id"] == "fallback-model"
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "workflow_agent.fallback_model" in checkpoints
    assert "workflow_agent.completed" in checkpoints


@pytest.mark.asyncio
async def test_workflow_agent_empty_output_exception_handling(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        raise RuntimeError("model failed permanently")
        yield "unreachable"

    monkeypatch.setattr(
        "server.main.get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        "server.main.stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    workflow = _workflow_agent_strategy_workflow(
        {
            "exceptionHandling": "empty_output",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "empty output"}},
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    assert any(event.get("event") == "workflow_end" for event in events)
    assert any(
        event.get("event") == "error" and event.get("node_id") == "workflow_agent"
        for event in events
    )
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == ""
    assert agent_end["variables"]["agent_output"] == ""

    workflow_run_id = next(
        event for event in events if event.get("event") == "workflow_meta"
    )["run_id"]
    agent_run = await _workflow_agent_run(client, workflow_run_id)
    assert agent_run["status"] == "completed"
    assert agent_run["metadata"]["exception_handled"] is True
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "workflow_agent.empty_output" in checkpoints


@pytest.mark.asyncio
async def test_workflow_agent_disable_output_does_not_write_variable(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        yield "hidden result"

    monkeypatch.setattr(
        "server.main.get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        "server.main.stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    workflow = _workflow_agent_strategy_workflow(
        {
            "disableOutput": "true",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "disable output"}},
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    disabled_delta = [
        event
        for event in events
        if event.get("event") == "node_delta"
        and event.get("node_id") == "workflow_agent"
        and "output disabled" in str(event.get("output"))
    ]
    assert disabled_delta
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == ""
    assert "agent_output" not in agent_end["variables"]

    workflow_run_id = next(
        event for event in events if event.get("event") == "workflow_meta"
    )["run_id"]
    agent_run = await _workflow_agent_run(client, workflow_run_id)
    assert agent_run["metadata"]["output_disabled"] is True
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "workflow_agent.output_disabled" in checkpoints


@pytest.mark.asyncio
async def test_workflow_handoff_router_creates_task_handoff_and_runs(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        yield "ready for review"

    monkeypatch.setattr(
        "server.main.get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        "server.main.stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    workflow = {
        "id": "handoff-router-workflow",
        "title": "handoff router workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "workflow_agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": "router-agent",
                    "modelId": "deepseek/deepseek-chat",
                    "rolePrompt": "You prepare handoff input.",
                    "taskInput": "{{user_input}}",
                    "outputVariable": "agent_output",
                },
            },
            {
                "id": "router",
                "type": "handoff_router",
                "data": {
                    "kind": "handoff_router",
                    "sourceVariable": "agent_output",
                    "taskTitle": "Review {{user_input}}",
                    "sourceAgent": "workflow-agent",
                    "targetAgent": "review-agent",
                    "reasonTemplate": "Please review {{agent_output}}",
                    "outputVariable": "agent_handoff_id",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_handoff_id"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "workflow_agent"},
            {"id": "e2", "source": "workflow_agent", "target": "router"},
            {"id": "e3", "source": "router", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "handoff this summary"},
        },
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    workflow_run_id = workflow_meta.get("run_id")
    assert isinstance(workflow_run_id, str)

    router_delta = next(
        event
        for event in events
        if event.get("event") == "node_delta" and event.get("node_id") == "router"
    )
    assert "Created routed Handoff" in str(router_delta.get("output"))
    task_id = router_delta.get("agent_task_id")
    handoff_id = router_delta.get("agent_handoff_id")
    assert isinstance(task_id, str)
    assert isinstance(handoff_id, str)

    router_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "router"
    )
    assert router_end["output"] == handoff_id
    assert router_end["variables"]["agent_handoff_id"] == handoff_id

    task_response = await client.get(f"/api/runtime/agent-tasks/{task_id}")
    assert task_response.status_code == 200, task_response.text
    task_payload = task_response.json()
    assert task_payload["title"] == "Review handoff this summary"
    assert task_payload["input"] == "ready for review"
    assert task_payload["source_agent"] == "workflow-agent"
    assert task_payload["assigned_agent"] == "review-agent"
    assert task_payload["metadata"]["router"] == "handoff_router"

    handoff_response = await client.get(
        "/api/runtime/agent-handoffs?target_agent=review-agent&limit=50",
    )
    assert handoff_response.status_code == 200, handoff_response.text
    handoffs = handoff_response.json()
    handoff_payload = next(item for item in handoffs if item["handoff_id"] == handoff_id)
    assert handoff_payload["task_id"] == task_id
    assert handoff_payload["status"] == "pending"
    assert handoff_payload["source_agent"] == "workflow-agent"
    assert handoff_payload["target_agent"] == "review-agent"

    child_runs_response = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=50",
    )
    assert child_runs_response.status_code == 200, child_runs_response.text
    child_runs = child_runs_response.json()
    task_run = next(
        item
        for item in child_runs
        if item["run_type"] == "agent_task" and item["source_id"] == task_id
    )
    handoff_run = next(
        item
        for item in child_runs
        if item["run_type"] == "agent_handoff" and item["source_id"] == handoff_id
    )
    assert task_run["metadata"]["router"] == "handoff_router"
    assert handoff_run["metadata"]["router"] == "handoff_router"

    task_checkpoints_response = await client.get(
        f"/api/runtime/runs/{task_run['run_id']}/checkpoints",
    )
    assert task_checkpoints_response.status_code == 200
    assert any(
        item["event_type"] == "agent_task.created"
        for item in task_checkpoints_response.json()
    )

    handoff_checkpoints_response = await client.get(
        f"/api/runtime/runs/{handoff_run['run_id']}/checkpoints",
    )
    assert handoff_checkpoints_response.status_code == 200
    assert any(
        item["event_type"] == "agent_handoff.created"
        for item in handoff_checkpoints_response.json()
    )


@pytest.mark.asyncio
async def test_workflow_agent_mcp_tool_mode_uses_runtime_toolset(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    provider, restore_provider = _install_fake_tool_provider()
    responses = iter(
        [
            '{"tool":"fetch","arguments":{"query":"handoff queue"}}',
            '{"answer":"final from tool"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        workflow = {
            "id": "workflow-agent-tool-workflow",
            "title": "workflow agent tool workflow",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "workflow_agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "tool-agent",
                        "modelId": "deepseek/deepseek-chat",
                        "rolePrompt": "你是工具智能体。",
                        "taskInput": "请处理：{{user_input}}",
                        "toolMode": "mcp_tools",
                        "toolNames": "fetch",
                        "maxIterations": "3",
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
                {"id": "e2", "source": "workflow_agent", "target": "output"},
            ],
        }

        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {"user_input": "handoff queue"},
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    tool_deltas = [
        event
        for event in events
        if event.get("event") == "node_delta"
        and event.get("node_id") == "workflow_agent"
        and "调用工具 fetch" in str(event.get("output"))
    ]
    assert tool_deltas

    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "final from tool"
    assert agent_end["variables"]["agent_output"] == "final from tool"
    assert len(provider.calls) == 1
    assert provider.calls[0].tool_name == "fetch"
    assert provider.calls[0].arguments == {"query": "handoff queue"}

    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    workflow_run_id = workflow_meta["run_id"]
    child_runs_response = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=20",
    )
    assert child_runs_response.status_code == 200, child_runs_response.text
    workflow_agent_run = next(
        item for item in child_runs_response.json() if item["run_type"] == "workflow_agent"
    )
    checkpoints_response = await client.get(
        f"/api/runtime/runs/{workflow_agent_run['run_id']}/checkpoints",
    )
    assert checkpoints_response.status_code == 200, checkpoints_response.text
    checkpoint_types = [item["event_type"] for item in checkpoints_response.json()]
    assert "workflow_agent.tool_call" in checkpoint_types
    assert "workflow_agent.model_answer" in checkpoint_types


@pytest.mark.asyncio
async def test_workflow_agent_executes_safe_parallel_tool_batch_in_decision_order(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    provider, restore_provider = _install_fake_tool_provider()
    provider.tools[0].parallel_safe = True
    provider.tools.append(
        RuntimeTool(
            name="lookup",
            description="Look up test content",
            input_schema={"type": "object"},
            read_only=True,
            parallel_safe=True,
        )
    )
    responses = iter(
        [
            json.dumps(
                {
                    "tools": [
                        {"tool": "fetch", "arguments": {"query": "first"}},
                        {"tool": "lookup", "arguments": {"query": "second"}},
                    ]
                }
            ),
            '{"answer":"parallel complete"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "fetch,lookup",
            "maxIterations": "3",
            "parallelToolCalls": "true",
            "maxToolConcurrency": "2",
            "maxToolCalls": "4",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "parallel"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "parallel complete"
    assert [call.tool_name for call in provider.calls] == ["fetch", "lookup"]


@pytest.mark.asyncio
async def test_parallel_post_hook_failure_terminates_the_agent_node(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()
    provider.tools[0].parallel_safe = True
    provider.tools.append(
        RuntimeTool(
            name="lookup",
            description="Look up test content",
            input_schema={"type": "object"},
            read_only=True,
            parallel_safe=True,
        )
    )
    package_dir = tmp_path / "hook-skill"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text("# Hook Skill\n", encoding="utf-8")
    manager = GuidanceSkillManager(package_dir)
    executions = WorkflowExecutionStore(tmp_path / "executions")
    model_calls = 0

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return json.dumps(
            {
                "tools": [
                    {"tool": "fetch", "arguments": {"query": "first"}},
                    {"tool": "lookup", "arguments": {"query": "second"}},
                ]
            }
        )

    async def wrap_tool(request, handler, _context):
        response = await handler(request)
        if request.tool_name == "fetch":
            raise SkillHookRuntimeError(
                "PostToolUse validation failed after execution.",
                code="skill_hook_validation_failed",
            )
        return response

    monkeypatch.setattr(
        main_module,
        "build_plugin_hooks_v2_middleware",
        lambda *args, **kwargs: AgentMiddleware(
            name="typed-hook-test", wrap_tool_call=wrap_tool
        ),
    )
    monkeypatch.setattr(main_module, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "fetch,lookup",
            "maxIterations": "3",
            "parallelToolCalls": "true",
            "maxToolConcurrency": "2",
            "maxToolCalls": "4",
        }
    )
    workflow["nodes"].append(
        {
            "id": "typed-hooks",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "plugin_hooks",
                "runtimeMiddlewareKind": "runtime_middleware.plugin_hooks",
                "middlewarePriority": "60",
                "runtimeMiddlewareConfig": {
                    "hook_mode": "typed_v2",
                    "skill_ids": manager.skill_id,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-typed-hooks",
            "source": "typed-hooks",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "parallel"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert any(
        event.get("event") == "error"
        and event.get("node_id") == "workflow_agent"
        and "PostToolUse validation failed" in str(event.get("message") or "")
        for event in events
    ), response.text
    assert model_calls == 1


@pytest.mark.asyncio
async def test_function_calling_strategy_does_not_downgrade_hook_failure(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()
    package_dir = tmp_path / "hook-skill-v2"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text("# Hook Skill\n", encoding="utf-8")
    manager = GuidanceSkillManager(package_dir)
    executions = WorkflowExecutionStore(tmp_path / "executions-v2")
    model_calls = 0

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            return None

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            nonlocal model_calls
            model_calls += 1
            if model_calls == 1:
                return AgentModelTurn(
                    tool_calls=[
                        AgentToolCall(
                            call_id="hook_call",
                            name="fetch",
                            raw_arguments='{"query":"hook"}',
                        )
                    ],
                    finish_reason="tool_calls",
                )
            return AgentModelTurn(content="must not continue", finish_reason="stop")

    async def wrap_tool(request, handler, _context):
        await handler(request)
        raise SkillHookRuntimeError(
            "PostToolUse validation failed after execution.",
            code="skill_hook_validation_failed",
        )

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(
        main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient
    )
    monkeypatch.setattr(
        main_module,
        "build_plugin_hooks_v2_middleware",
        lambda *args, **kwargs: AgentMiddleware(
            name="typed-hook-test", wrap_tool_call=wrap_tool
        ),
    )
    monkeypatch.setattr(main_module, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "fetch",
            "agentStrategy": "function_calling",
            "maxIterations": "3",
        }
    )
    workflow["nodes"].append(
        {
            "id": "typed-hooks-v2",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "plugin_hooks",
                "runtimeMiddlewareKind": "runtime_middleware.plugin_hooks",
                "middlewarePriority": "60",
                "runtimeMiddlewareConfig": {
                    "hook_mode": "typed_v2",
                    "skill_ids": manager.skill_id,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-typed-hooks-v2",
            "source": "typed-hooks-v2",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "hook"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert any(
        event.get("event") == "error"
        and "PostToolUse validation failed" in str(event.get("message") or "")
        for event in events
    ), response.text
    assert model_calls == 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_function_calling_parallel_hook_preflight_blocks_the_entire_batch(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()
    provider.tools[0].parallel_safe = True
    provider.tools.append(
        RuntimeTool(
            name="lookup",
            description="Look up test content",
            input_schema={"type": "object"},
            read_only=True,
            parallel_safe=True,
        )
    )
    package_dir = tmp_path / "hook-skill-v2-batch"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text("# Hook Skill\n", encoding="utf-8")
    manager = GuidanceSkillManager(package_dir)
    executions = WorkflowExecutionStore(tmp_path / "executions-v2-batch")
    model_calls = 0
    preflight_tools: list[list[str]] = []

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            return None

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            nonlocal model_calls
            model_calls += 1
            return AgentModelTurn(
                tool_calls=[
                    AgentToolCall(
                        call_id="hook_call_1",
                        name="fetch",
                        raw_arguments='{"query":"first"}',
                    ),
                    AgentToolCall(
                        call_id="hook_call_2",
                        name="lookup",
                        raw_arguments='{"query":"second"}',
                    ),
                ],
                finish_reason="tool_calls",
            )

    async def before_tool_batch(requests, _context):
        preflight_tools.append([request.tool_name for request in requests])
        raise SkillHookRuntimeError(
            "PreToolUse guard denied the parallel batch.",
            code="skill_hook_denied",
        )

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(
        main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient
    )
    monkeypatch.setattr(
        main_module,
        "build_plugin_hooks_v2_middleware",
        lambda *args, **kwargs: AgentMiddleware(
            name="typed-hook-batch-test", before_tool_batch=before_tool_batch
        ),
    )
    monkeypatch.setattr(main_module, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "fetch,lookup",
            "agentStrategy": "function_calling",
            "parallelToolCalls": "true",
            "maxIterations": "3",
        }
    )
    workflow["nodes"].append(
        {
            "id": "typed-hooks-v2-batch",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "plugin_hooks",
                "runtimeMiddlewareKind": "runtime_middleware.plugin_hooks",
                "middlewarePriority": "60",
                "runtimeMiddlewareConfig": {
                    "hook_mode": "typed_v2",
                    "skill_ids": manager.skill_id,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-typed-hooks-v2-batch",
            "source": "typed-hooks-v2-batch",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "parallel hook"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert any(
        event.get("event") == "error"
        and "PreToolUse guard denied" in str(event.get("message") or "")
        for event in events
    ), response.text
    assert preflight_tools == [["fetch", "lookup"]]
    assert provider.calls == []
    assert model_calls == 1


@pytest.mark.asyncio
async def test_workflow_agent_terminal_tool_ends_without_model_summary(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    provider, restore_provider = _install_fake_tool_provider()
    provider.tools[0].terminal = True
    decisions: list[str] = []

    async def fake_collect_chat_completion_text(*args, **kwargs):
        decisions.append("called")
        return '{"tool":"fetch","arguments":{"query":"terminal"}}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "fetch",
            "maxIterations": "3",
            "maxToolCalls": "2",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "terminal"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "tool response"
    assert len(decisions) == 1
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_workflow_agent_tool_policy_denial_does_not_crash_workflow(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    provider, restore_provider = _install_fake_tool_provider()

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return '{"tool":"fetch","arguments":{"query":"blocked"}}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        workflow = {
            "id": "workflow-agent-policy-workflow",
            "title": "workflow agent policy workflow",
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
                        "runtimeMiddlewareId": "tool_policy",
                        "runtimeMiddlewareKind": "runtime_middleware.tool_policy",
                        "runtimeMiddlewareConfig": {
                            "denied_tools": "fetch",
                            "allow_by_default": True,
                        },
                    },
                },
                {
                    "id": "workflow_agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "tool-agent",
                        "modelId": "deepseek/deepseek-chat",
                        "rolePrompt": "你是工具智能体。",
                        "taskInput": "请处理：{{user_input}}",
                        "toolMode": "mcp_tools",
                        "toolNames": "fetch",
                        "maxIterations": "2",
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
                {"id": "e1", "source": "input", "target": "policy"},
                {"id": "e2", "source": "policy", "target": "workflow_agent"},
                {"id": "e3", "source": "workflow_agent", "target": "output"},
            ],
        }

        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {"user_input": "blocked"},
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    errors = [
        event
        for event in events
        if event.get("event") == "error" and event.get("node_id") == "workflow_agent"
    ]
    assert errors
    assert "denied" in str(errors[0].get("message")).lower()
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == ""
    assert provider.calls == []


@pytest.mark.asyncio
async def test_legacy_agent_tool_first_uses_runtime_toolset(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    provider, restore_provider = _install_fake_tool_provider()
    responses = iter(
        [
            '{"tool":"fetch","arguments":{"query":"legacy agent"}}',
            '{"answer":"legacy final"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        workflow = {
            "id": "legacy-agent-tool-workflow",
            "title": "legacy agent tool workflow",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "agent",
                    "type": "agent",
                    "data": {
                        "kind": "agent",
                        "agentMode": "tool_first",
                        "instruction": "请处理：{{user_input}}",
                        "modelId": "deepseek/deepseek-chat",
                        "toolNames": "fetch",
                        "maxIterations": "3",
                        "temperature": "0.7",
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
                {"id": "e1", "source": "input", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "output"},
            ],
        }

        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {"user_input": "legacy agent"},
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "agent"
    )
    assert agent_end["output"] == "legacy final"
    assert len(provider.calls) == 1
    assert provider.calls[0].tool_name == "fetch"


def _agent_strategy_workflow(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "agent",
        "agentMode": "tool_first",
        "instruction": "请处理：{{user_input}}",
        "modelId": "test/model",
        "toolNames": "fetch",
        "maxIterations": "3",
        "outputVariable": "agent_output",
    }
    data.update(overrides or {})
    return {
        "id": "agent-v2-workflow",
        "title": "agent v2 workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {"id": "agent", "type": "agent", "data": data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_output"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "agent"},
            {"id": "e2", "source": "agent", "target": "output"},
        ],
    }


@pytest.mark.asyncio
async def test_agent_strategy_v2_function_calling_uses_runtime_toolset(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            self.responses = iter(
                [
                    AgentModelTurn(
                        tool_calls=[
                            AgentToolCall(
                                call_id="call_v2",
                                name="fetch",
                                raw_arguments='{"query":"v2 agent"}',
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    AgentModelTurn(content="v2 final", finish_reason="stop"),
                ]
            )

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            return next(self.responses)

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _agent_strategy_workflow(
                    {"agentStrategy": "function_calling"}
                ),
                "inputs": {"user_input": "v2"},
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "agent"
    )
    assert agent_end["output"] == "v2 final"
    assert len(provider.calls) == 1
    assert provider.calls[0].arguments == {"query": "v2 agent"}
    assert any(
        event.get("event") == "node_delta"
        and event.get("node_id") == "agent"
        and event.get("strategy") == "function_calling"
        for event in events
    )


@pytest.mark.asyncio
async def test_workflow_agent_strategy_v2_react_uses_runtime_toolset(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            self.responses = iter(
                [
                    AgentModelTurn(
                        content=(
                            "Thought: use fetch\n"
                            'Action: {"action":"fetch","action_input":{"query":"react"}}'
                        )
                    ),
                    AgentModelTurn(content="FinalAnswer: react final"),
                ]
            )

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            return next(self.responses)

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "agentStrategy": "react",
            "toolNames": "fetch",
        }
    )
    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "react"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "react final"
    assert len(provider.calls) == 1
    assert provider.calls[0].arguments == {"query": "react"}


@pytest.mark.asyncio
async def test_agent_strategy_v2_auto_safely_falls_back_to_react(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            self.responses = iter(
                [
                    AgentModelError(
                        "unknown parameter: tools",
                        status_code=400,
                        param="tools",
                    ),
                    AgentModelTurn(content="FinalAnswer: auto fallback"),
                ]
            )

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            response = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _agent_strategy_workflow(),
                "inputs": {"user_input": "auto"},
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "agent"
    )
    assert agent_end["output"] == "auto fallback"
    assert provider.calls == []
    assert any(
        event.get("event") == "node_delta"
        and event.get("strategy") == "react"
        and "回退" in str(event.get("output"))
        for event in events
    )


@pytest.mark.asyncio
async def test_workflow_agent_v2_missing_whitelist_tool_stops_before_model(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()
    model_client_inits = 0

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal model_client_inits
            model_client_inits += 1

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            raise AssertionError("model must not be called for a missing whitelist tool")

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "missing-tool",
            "retryOnFailure": "true",
            "fallbackModelId": "fallback/model",
            "exceptionHandling": "empty_output",
        }
    )
    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "missing"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    workflow_meta = next(
        event for event in events if event.get("event") == "workflow_meta"
    )
    child_run = await _workflow_agent_run(client, workflow_meta["run_id"])
    checkpoint_types = await _checkpoint_types(client, child_run["run_id"])
    assert model_client_inits == 0
    assert provider.calls == []
    assert checkpoint_types.count("workflow_agent.failed_attempt") == 1
    assert "workflow_agent.retry" not in checkpoint_types
    assert "workflow_agent.fallback_model" not in checkpoint_types


def _parse_sse_events(sse_text: str) -> list[dict]:
    events: list[dict] = []
    for line in sse_text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _workflow_agent_strategy_workflow(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow_agent_data: dict[str, Any] = {
        "kind": "workflow_agent",
        "agentName": "strategy-agent",
        "modelId": "deepseek/deepseek-chat",
        "rolePrompt": "You are a workflow agent.",
        "taskInput": "{{user_input}}",
        "outputVariable": "agent_output",
    }
    workflow_agent_data.update(overrides or {})
    return {
        "id": "workflow-agent-strategy-workflow",
        "title": "workflow agent strategy workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "workflow_agent",
                "type": "workflow_agent",
                "data": workflow_agent_data,
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "agent_output"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "workflow_agent"},
            {"id": "e2", "source": "workflow_agent", "target": "output"},
        ],
    }


def _bind_output_content_policy(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow["nodes"].append(
        {
            "id": "content-policy",
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
                            "action": "redact",
                            "terms": ["R19_SYNTHETIC_SENTINEL"],
                            "caseSensitive": False,
                        }
                    ],
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "content-policy-binding",
            "source": "content-policy",
            "sourceHandle": "middleware-binding",
            "target": "workflow_agent",
            "targetHandle": "middleware",
        }
    )
    return workflow


def _bind_input_content_policy(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow["nodes"].append(
        {
            "id": "content-policy",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "content_policy",
                "runtimeMiddlewareKind": "runtime_middleware.content_policy",
                "middlewarePriority": "100",
                "runtimeMiddlewareConfig": {
                    "phase": "input",
                    "rules": [
                        {
                            "id": "rule_1",
                            "label": "Synthetic sentinel",
                            "detector": "literal_terms",
                            "action": "redact",
                            "terms": ["R19_TOOL_RESULT_SENTINEL"],
                            "caseSensitive": False,
                        }
                    ],
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "content-policy-binding",
            "source": "content-policy",
            "sourceHandle": "middleware-binding",
            "target": "workflow_agent",
            "targetHandle": "middleware",
        }
    )
    return workflow


@pytest.mark.asyncio
async def test_content_policy_guards_legacy_tool_agent_final_output_without_breaking_tool_json(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()
    responses = iter(
        [
            '{"tool":"fetch","arguments":{"query":"guard"}}',
            '{"answer":"R19_SYNTHETIC_SENTINEL"}',
        ]
    )

    async def fake_collect(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _bind_output_content_policy(
        _workflow_agent_strategy_workflow(
            {"toolMode": "mcp_tools", "toolNames": "fetch", "maxIterations": "3"}
        )
    )
    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "use the tool"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    assert "R19_SYNTHETIC_SENTINEL" not in response.text
    assert "[已脱敏]" in response.text
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_content_policy_guards_strategy_v2_final_output_after_tool_execution(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_tool_provider()

    class FakeAgentModelClient:
        def __init__(self, **kwargs: Any) -> None:
            self.responses = iter(
                [
                    AgentModelTurn(
                        content=(
                            "Thought: use fetch\n"
                            'Action: {"action":"fetch","action_input":{"query":"guard"}}'
                        )
                    ),
                    AgentModelTurn(content="FinalAnswer: R19_SYNTHETIC_SENTINEL"),
                ]
            )

        async def complete(self, **kwargs: Any) -> AgentModelTurn:
            return next(self.responses)

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)
    monkeypatch.setattr(main_module, "OpenAICompatibleAgentModelClient", FakeAgentModelClient)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _bind_output_content_policy(
        _workflow_agent_strategy_workflow(
            {"toolMode": "mcp_tools", "agentStrategy": "react", "toolNames": "fetch"}
        )
    )
    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "use the tool"}},
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    assert "R19_SYNTHETIC_SENTINEL" not in response.text
    assert "[已脱敏]" in response.text
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_content_policy_redacts_tool_result_before_legacy_agent_observes_or_records_it(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SentinelToolProvider(FakeWorkflowToolProvider):
        async def call_tool(self, call):
            self.calls.append(call)
            return RuntimeToolResult(
                output="R19_TOOL_RESULT_SENTINEL from tool",
                content=[
                    {"type": "text", "text": "R19_TOOL_RESULT_SENTINEL from tool"}
                ],
                metadata={"content_types": ["text"]},
                is_error=False,
            )

    provider = SentinelToolProvider()
    original_provider = main_module.runtime_capabilities.require(
        "mcp_tools"
    ).implementation
    main_module.runtime_capabilities.register("mcp_tools", provider)
    observed_model_messages: list[list[str]] = []
    responses = iter(
        [
            '{"tool":"fetch","arguments":{"query":"guard"}}',
            '{"answer":"safe final"}',
        ]
    )

    async def fake_collect(_model_id, messages, **_kwargs):
        observed_model_messages.append([str(message.content) for message in messages])
        return next(responses)

    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _bind_input_content_policy(
        _workflow_agent_strategy_workflow(
            {"toolMode": "mcp_tools", "toolNames": "fetch", "maxIterations": "3"}
        )
    )
    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "use the tool"}},
        )
    finally:
        main_module.runtime_capabilities.register("mcp_tools", original_provider)

    assert response.status_code == 200, response.text
    assert "R19_TOOL_RESULT_SENTINEL" not in response.text
    assert len(provider.calls) == 1
    assert len(observed_model_messages) == 2
    assert "R19_TOOL_RESULT_SENTINEL" not in "\n".join(observed_model_messages[1])
    assert "[已脱敏] from tool" in "\n".join(observed_model_messages[1])

    workflow_meta = next(
        event
        for event in _parse_sse_events(response.text)
        if event.get("event") == "workflow_meta"
    )
    child_run = await _workflow_agent_run(client, workflow_meta["run_id"])
    checkpoints_response = await client.get(
        f"/api/runtime/runs/{child_run['run_id']}/checkpoints"
    )
    assert checkpoints_response.status_code == 200
    assert "R19_TOOL_RESULT_SENTINEL" not in checkpoints_response.text


async def _workflow_agent_run(
    client: httpx.AsyncClient,
    workflow_run_id: str,
) -> dict[str, Any]:
    child_runs_response = await client.get(
        f"/api/runtime/runs?parent_run_id={workflow_run_id}&limit=50",
    )
    assert child_runs_response.status_code == 200, child_runs_response.text
    return next(
        item
        for item in child_runs_response.json()
        if item["run_type"] == "workflow_agent"
        and item["source_id"].endswith(":workflow_agent")
    )


async def _checkpoint_types(
    client: httpx.AsyncClient,
    run_id: str,
) -> list[str]:
    checkpoints_response = await client.get(
        f"/api/runtime/runs/{run_id}/checkpoints",
    )
    assert checkpoints_response.status_code == 200, checkpoints_response.text
    return [item["event_type"] for item in checkpoints_response.json()]


@pytest.mark.asyncio
async def test_bound_ralph_loop_verifies_and_improves_agent_output(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "incomplete draft",
            '{"complete":false,"reason":"missing evidence","feedback":"add concrete evidence"}',
            "complete answer with concrete evidence",
            '{"complete":true,"reason":"all requirements satisfied","feedback":""}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow()
    workflow["nodes"].append(
        {
            "id": "ralph",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "ralph_loop",
                "runtimeMiddlewareKind": "runtime_middleware.ralph_loop",
                "middlewarePriority": "80",
                "runtimeMiddlewareConfig": {
                    "max_iterations": 4,
                    "max_output_chars": 10000,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-ralph",
            "source": "ralph",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "produce evidence"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "complete answer with concrete evidence"
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    agent_run = await _workflow_agent_run(client, workflow_meta["run_id"])
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "middleware.ralph.continue" in checkpoints
    assert "middleware.ralph.verified" in checkpoints
    assert "middleware.ralph_loop.completed" in checkpoints


@pytest.mark.asyncio
async def test_bound_knowledge_writer_creates_pending_proposal_after_success(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposals: list[dict[str, Any]] = []

    class FakeRagService:
        def get_pipeline_draft(self, kb_id: str):
            assert kb_id == "kb-automation"
            return {"kb_id": kb_id}

        def create_knowledge_write_proposal(self, kb_id: str, **payload):
            proposals.append({"kb_id": kb_id, **payload})
            return {"proposal_id": "proposal-automation", "status": "pending"}

    async def fake_stream_workflow_llm_text(*args, **kwargs):
        yield "verified operational guidance"

    monkeypatch.setattr(main_module, "get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )
    workflow = _workflow_agent_strategy_workflow()
    workflow["nodes"].append(
        {
            "id": "writer",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "knowledge_writer",
                "runtimeMiddlewareKind": "runtime_middleware.knowledge_writer",
                "middlewarePriority": "90",
                "runtimeMiddlewareConfig": {
                    "knowledge_base_id": "kb-automation",
                    "auto_propose_verified_output": True,
                    "title_prefix": "Verified result",
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-writer",
            "source": "writer",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "write guidance"}},
    )

    assert response.status_code == 200, response.text
    assert len(proposals) == 1
    assert proposals[0]["kb_id"] == "kb-automation"
    assert proposals[0]["content"] == "verified operational guidance"
    assert proposals[0]["source_run_id"]
    workflow_meta = next(
        event for event in _parse_sse_events(response.text) if event.get("event") == "workflow_meta"
    )
    agent_run = await _workflow_agent_run(client, workflow_meta["run_id"])
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "middleware.knowledge_writer.proposed" in checkpoints


class FakeWorkflowToolProvider:
    def __init__(self) -> None:
        self.tools = [
            RuntimeTool(
                name="fetch",
                description="Fetch test content",
                input_schema={"type": "object"},
                session_id="session-1",
                server_id="server-1",
            )
        ]
        self.calls = []

    async def list_tools(self):
        return list(self.tools)

    async def find_tool(self, tool_name: str):
        for tool in self.tools:
            if tool.name == tool_name:
                return tool
        return None

    async def call_tool(self, call):
        self.calls.append(call)
        return RuntimeToolResult(
            output="tool response",
            content=[{"type": "text", "text": "tool response"}],
            metadata={"content_types": ["text"]},
            is_error=False,
        )


def _install_fake_tool_provider() -> tuple[FakeWorkflowToolProvider, Any]:
    provider = FakeWorkflowToolProvider()
    original = main_module.runtime_capabilities.require("mcp_tools").implementation
    main_module.runtime_capabilities.register("mcp_tools", provider)

    def restore() -> None:
        main_module.runtime_capabilities.register("mcp_tools", original)

    return provider, restore


@pytest.mark.asyncio
async def test_bound_structured_output_repairs_and_validates_agent_answer(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            '{"answer": 42}',
            '{"answer":"validated"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    workflow = _workflow_agent_strategy_workflow()
    workflow["nodes"].append(
        {
            "id": "structured",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "structured_output",
                "runtimeMiddlewareKind": "runtime_middleware.structured_output",
                "middlewarePriority": "20",
                "runtimeMiddlewareConfig": {
                    "schema_json": {
                        "type": "object",
                        "required": ["answer"],
                        "properties": {"answer": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    "repair_attempts": 1,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-structured",
            "source": "structured",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "return JSON"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert json.loads(agent_end["output"]) == {"answer": "validated"}
    assert agent_end["variables"]["agent_output"] == '{"answer":"validated"}'

    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    agent_run = await _workflow_agent_run(client, workflow_meta["run_id"])
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "middleware.structured_output.validated" in checkpoints


@pytest.mark.asyncio
async def test_bound_todo_planner_creates_scoped_todo_through_runtime_toolset(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    todo_store = RuntimeTodoStore(tmp_path / "workflow-todos")
    monkeypatch.setattr(main_module, "runtime_todo_store", todo_store)
    monkeypatch.setattr(main_module.workflow_todo_provider, "store", todo_store)
    responses = iter(
        [
            '{"tool":"todo_create","arguments":{"title":"Draft plan","priority":2}}',
            '{"answer":"plan tracked"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow()
    workflow["nodes"].append(
        {
            "id": "planner",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "todo_planner",
                "runtimeMiddlewareKind": "runtime_middleware.todo_planner",
                "middlewarePriority": "30",
                "runtimeMiddlewareConfig": {"max_items": 20},
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-planner",
            "source": "planner",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "make a plan"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "plan tracked"

    todos = todo_store.list_items(
        scope_type="workflow",
        scope_id=f"{workflow_meta['task_id']}:workflow_agent",
    )
    assert len(todos) == 1
    assert todos[0].title == "Draft plan"
    assert todos[0].source_run_id

    agent_run = await _workflow_agent_run(client, workflow_meta["run_id"])
    checkpoints = await _checkpoint_types(client, agent_run["run_id"])
    assert "workflow_agent.tool_call" in checkpoints
    assert "workflow_agent.model_answer" in checkpoints


@pytest.mark.asyncio
async def test_bound_hitl_pauses_and_resumes_tool_call_exactly_once(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    provider, restore_provider = _install_fake_tool_provider()
    responses = iter(
        [
            '{"tool":"fetch","arguments":{"query":"approval"}}',
            '{"answer":"approved result"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    workflow = _workflow_agent_strategy_workflow(
        {"toolMode": "mcp_tools", "toolNames": "fetch", "maxIterations": 4}
    )
    workflow["nodes"].append(
        {
            "id": "hitl",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "middlewarePriority": "40",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "fetch",
                    "final_confirmation": False,
                    "timeout_seconds": 3600,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-hitl",
            "source": "hitl",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "use the tool"}},
        )
        assert response.status_code == 200, response.text
        events = _parse_sse_events(response.text)
        pending_events = [
            event
            for event in events
            if event.get("event") == "runtime_approval_pending"
        ]
        assert pending_events, response.text
        pending = pending_events[0]
        task_id = pending["task_id"]
        assert provider.calls == []
        assert executions.require(task_id).status == "waiting"

        approval = approvals.require(pending["approval_id"])
        decided = approvals.decide(
            approval.approval_id,
            revision=approval.revision,
            decision="approve",
            operator="tester",
        )
        executions.mark_ready(task_id, approval_id=approval.approval_id)
        claimed = executions.claim(task_id, worker_id="test-worker")
        await main_module.resume_runtime_approval_execution(claimed, decided)

        completed = executions.require(task_id)
        assert completed.status == "completed"
        assert completed.result == "approved result"
        assert len(provider.calls) == 1
        persisted_events = completed.events
        assert any(event["event"] == "runtime_approval_resolved" for event in persisted_events)
        assert any(
            event["event"] == "workflow_end"
            and event["final_output"] == "approved result"
            for event in persisted_events
        )
    finally:
        restore_provider()


@pytest.mark.asyncio
async def test_skill_router_install_resume_activates_only_current_agent_run(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class RouterSkillManager:
        def __init__(self) -> None:
            self.items: list[InstalledSkill] = []
            self.read_count = 0
            self.version_id = "skillversion_router_pdf"
            self.package_dir = tmp_path / "router-pdf"
            self.package_dir.mkdir()
            (self.package_dir / "SKILL.md").write_text(
                "# Router PDF\n\nRead the contract before extracting fields.",
                encoding="utf-8",
            )
            self.content_digest = compute_skill_content_digest(
                {"SKILL.md": (self.package_dir / "SKILL.md").read_bytes()}
            )
            self.lifecycle_store = SimpleNamespace(
                require_version=lambda version_id: SimpleNamespace(
                    skill_id="router-pdf",
                    source_kind="git",
                    package_digest=self.content_digest,
                    trust_fingerprint="2" * 64,
                )
            )

        def list_installed_skills(self) -> list[InstalledSkill]:
            return list(self.items)

        def install_skill(
            self, repo_url: str, sub_path: str, source_ref: str
        ) -> InstalledSkill:
            assert repo_url == "https://github.com/example/router-skills"
            assert sub_path == "skills/pdf"
            installed = InstalledSkill(
                skill_id="router-pdf",
                name="Router PDF",
                description="Extract PDF contracts.",
                repo_url=repo_url,
                sub_path=sub_path,
                source_ref=source_ref,
                installed_at=time.time(),
                source_kind="git",
                content_digest=self.content_digest,
                trust_fingerprint="2" * 64,
            )
            self.items = [installed]
            return installed

        def bind_skill_versions(self, skill_ids) -> dict[str, str]:
            return {
                "router-pdf": self.version_id
                for skill_id in skill_ids
                if skill_id == "router-pdf"
            }

        def require_activation(self, skill_id: str, **kwargs) -> InstalledSkill:
            assert skill_id == "router-pdf"
            assert kwargs.get("version_id") == self.version_id
            return self.items[0]

        def get_skill_content(self, skill_id: str) -> str:
            assert skill_id == "router-pdf"
            self.read_count += 1
            return "# Router PDF\n\nRead the contract before extracting fields."

        def get_skill_directory(
            self, skill_id: str, *, version_id: str | None = None
        ) -> Path:
            assert skill_id == "router-pdf"
            assert version_id in {None, self.version_id}
            return self.package_dir

    class RouterSandboxClient:
        async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
            assert payload.get("action") == "ensure_workspace"
            return {"ok": True}

    source_ref = "1" * 40
    candidate_payload: dict[str, Any] = {
        "candidateId": "catalog:project:router-pdf",
        "sourceType": "catalog",
        "targetType": "project",
        "sourceId": "router-pdf",
        "name": "Router PDF",
        "category": "内容与办公",
        "kind": "skill",
        "description": "提取 PDF 合同字段",
        "sourceDescription": "Extract PDF contract fields.",
        "searchDescription": "Extract PDF contract fields.",
        "tags": ["pdf", "合同", "提取"],
        "includedSkills": [],
        "pathTerms": ["skills", "pdf"],
        "parentNames": [],
        "publisher": "Fixture",
        "sourceGroup": "测试目录",
        "parentSkillSets": [],
        "installSource": {
            "repoUrl": "https://github.com/example/router-skills",
            "subPath": "skills/pdf",
            "verifiedCommit": source_ref,
        },
        "directoryTreeSha": None,
        "trust": {
            "receiptId": "skill-trust-" + "1" * 24,
            "trustFingerprint": "2" * 64,
            "riskLevel": "low",
            "trustStatus": "verified",
            "installPolicy": "allow",
            "compatibilityStatus": "portable",
            "routerEligible": True,
        },
    }
    candidate = {
        **candidate_payload,
        "candidateFingerprint": _fingerprint(candidate_payload),
        "stableNameOrder": 0,
    }
    index_payload = {
        "version": 2,
        "rankerVersion": "skill-need-local-v3",
        "memberIndexFingerprint": "fixture",
        "catalogFingerprint": "3" * 64,
        "trustIndexFingerprint": "4" * 64,
        "supersededCandidateIds": [],
        "candidates": [candidate],
    }
    index = {**index_payload, "fingerprint": _fingerprint(index_payload)}
    index_path = tmp_path / "skill-runtime-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    manager = RouterSkillManager()
    receipt_store = SkillApplicationReceiptStore(tmp_path / "application-receipts")
    monkeypatch.setattr(main_module, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(main_module, "skill_application_receipt_store", receipt_store)
    monkeypatch.setattr(
        main_module,
        "skill_application_observer",
        SkillApplicationObserver(receipt_store, lambda: manager),
    )
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            tmp_path / "router-runtime", workspace_root=tmp_path / "router-workspaces"
        ),
        RouterSandboxClient(),
        skill_manager=manager,
        skill_finder=SkillFinder(index_path=index_path, skill_manager=manager),
    )
    monkeypatch.setattr(main_module, "workflow_sandbox_provider", provider)
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("sandbox_tools"),
        "implementation",
        provider,
    )
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)

    responses = iter(
        [
            '{"tool":"skill_find","arguments":{"need":"提取 PDF 合同"}}',
            json.dumps(
                {
                    "tool": "skill_install",
                    "arguments": {
                        "candidate_id": candidate["candidateId"],
                        "candidate_fingerprint": candidate["candidateFingerprint"],
                    },
                },
                ensure_ascii=False,
            ),
            '{"tool":"skill_read","arguments":{"skill_id":"router-pdf"}}',
            '{"answer":"used the activated skill"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 6})
    workflow["nodes"].extend(
        [
            {
                "id": "skills-runtime",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "skills_runtime",
                    "runtimeMiddlewareKind": "runtime_middleware.skills_runtime",
                    "middlewarePriority": "30",
                    "runtimeMiddlewareConfig": {
                        "catalog_search": True,
                        "catalog_install": True,
                        "max_catalog_installs": 3,
                    },
                },
            },
            {
                "id": "skill-hitl",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "human_in_the_loop",
                    "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                    "middlewarePriority": "40",
                    "runtimeMiddlewareConfig": {
                        "interrupt_on_tools": "skill_install",
                        "final_confirmation": False,
                    },
                },
            },
        ]
    )
    workflow["edges"].extend(
        [
            {
                "id": "bind-skills-runtime",
                "source": "skills-runtime",
                "target": "workflow_agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
            {
                "id": "bind-skill-hitl",
                "source": "skill-hitl",
                "target": "workflow_agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
        ]
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "extract contract"}},
    )
    assert response.status_code == 200, response.text
    pending = next(
        event
        for event in _parse_sse_events(response.text)
        if event.get("event") == "runtime_approval_pending"
    )
    approval = approvals.require(pending["approval_id"])
    assert approval.allowed_decisions == ["approve", "reject"]
    assert approval.metadata["skill_approval"]["target_sha"] == source_ref
    assert manager.items == []

    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="approve",
        operator="tester",
    )
    executions.mark_ready(pending["task_id"], approval_id=approval.approval_id)
    claimed = executions.claim(pending["task_id"], worker_id="test-worker")
    await main_module.resume_runtime_approval_execution(claimed, decided)

    completed = executions.require(pending["task_id"])
    assert completed.status == "completed"
    assert completed.result == "used the activated skill"
    receipt = receipt_store.list_receipts(skill_id="router-pdf")[0]
    assert receipt.methods == ("skill_read",)
    assert receipt.compliance_status == "verified"
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "required"
        and event.get("skill_id") == "router-pdf"
        for event in completed.events
    ), [
        (event.get("status"), event.get("skill_id"), event.get("required_skill_ids"))
        for event in completed.events
        if event.get("event") == "skill_runtime_status"
    ]
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "verified"
        for event in completed.events
    )
    assert manager.items[0].source_ref == source_ref
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "install"
        and event.get("activated_skill_id") == "router-pdf"
        for event in completed.events
    ), json.dumps(
        [event for event in completed.events if event.get("event") == "skill_runtime_status"],
        ensure_ascii=False,
        indent=2,
    )


class GuidanceSkillManager:
    skill_id = "required-guidance"
    version_id = "skillversion_required_guidance_v1"
    content_digest = "5" * 64

    def __init__(self, package_dir: Path) -> None:
        self.package_dir = package_dir
        self.read_count = 0
        self.installed = InstalledSkill(
            skill_id=self.skill_id,
            name="Required Guidance",
            description="Required runtime instructions.",
            repo_url="workspace://required-guidance",
            sub_path="",
            source_ref=None,
            installed_at=time.time(),
            source_kind="workspace_draft",
            content_digest=self.content_digest,
        )
        self.lifecycle_store = SimpleNamespace(
            require_version=lambda version_id: SimpleNamespace(
                skill_id=self.skill_id,
                source_kind="workspace_draft",
                package_digest=self.content_digest,
                trust_fingerprint=None,
            )
        )

    def list_installed_skills(self) -> list[InstalledSkill]:
        return [self.installed]

    def bind_skill_versions(self, skill_ids) -> dict[str, str]:
        return {
            self.skill_id: self.version_id
            for skill_id in skill_ids
            if skill_id == self.skill_id
        }

    def require_activation(self, skill_id: str, **kwargs) -> InstalledSkill:
        assert skill_id == self.skill_id
        assert kwargs.get("version_id") in {None, self.version_id}
        return self.installed

    def get_skill_content(
        self, skill_id: str, *, version_id: str | None = None
    ) -> str:
        assert skill_id == self.skill_id
        assert version_id in {None, self.version_id}
        self.read_count += 1
        return (self.package_dir / "SKILL.md").read_text(encoding="utf-8")

    def get_skill_directory(
        self, skill_id: str, *, version_id: str | None = None
    ) -> Path:
        assert skill_id == self.skill_id
        assert version_id in {None, self.version_id}
        return self.package_dir


class GuidanceSandboxClient:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.actions: list[str] = []

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        self.actions.append(action)
        workspace = self.workspace_root / str(payload.get("workspace_id") or "")
        workspace.mkdir(parents=True, exist_ok=True)
        if action == "ensure_workspace":
            return {"ok": True}
        if action == "write_file":
            target = workspace / str(payload.get("path") or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(payload["content_base64"]))
            return {"ok": True, "path": payload.get("path")}
        if action == "read_file":
            target = workspace / str(payload.get("path") or "")
            body = target.read_text(encoding="utf-8")
            return {
                "ok": True,
                "path": payload.get("path"),
                "content": body,
                "truncated": False,
                "size_bytes": len(body.encode("utf-8")),
            }
        if action == "search_files":
            base = workspace / str(payload.get("path") or "work")
            query = str(payload.get("query") or "")
            matches = []
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if query.casefold() in line.casefold():
                        matches.append(
                            {
                                "path": path.relative_to(workspace).as_posix(),
                                "line": line_number,
                                "preview": line,
                            }
                        )
            return {
                "ok": True,
                "query": query,
                "matches": matches,
                "scanned_files": len(matches),
            }
        raise AssertionError(f"unexpected sandbox action: {action}")


def _install_guidance_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[GuidanceSkillManager, SkillApplicationReceiptStore]:
    package_dir = tmp_path / "required-guidance"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text(
        "# Required Guidance\n\nRead this before acting.",
        encoding="utf-8",
    )
    (package_dir / "references").mkdir()
    (package_dir / "references" / "guide.md").write_text(
        "# Guide\n\nUse the bounded-checklist before answering.",
        encoding="utf-8",
    )
    (package_dir / "assets").mkdir()
    (package_dir / "assets" / "sample.pdf").write_bytes(
        b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
    )
    manager = GuidanceSkillManager(package_dir)
    _freeze_guidance_manager_digest(manager)
    workspace_root = tmp_path / "sandbox-workspaces"
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            tmp_path / "sandbox-store",
            workspace_root=workspace_root,
        ),
        GuidanceSandboxClient(workspace_root),
        skill_manager=manager,
    )
    receipt_store = SkillApplicationReceiptStore(tmp_path / "application-receipts")
    observer = SkillApplicationObserver(receipt_store, lambda: manager)
    monkeypatch.setattr(main_module, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(main_module, "workflow_sandbox_provider", provider)
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("sandbox_tools"),
        "implementation",
        provider,
    )
    monkeypatch.setattr(main_module, "skill_application_receipt_store", receipt_store)
    monkeypatch.setattr(main_module, "skill_application_observer", observer)
    monkeypatch.setenv("SKILL_RUNTIME_GUIDANCE_V2_ENABLED", "true")
    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "audit")
    return manager, receipt_store


def _freeze_guidance_manager_digest(manager: GuidanceSkillManager) -> str:
    files = {
        path.relative_to(manager.package_dir).as_posix(): path.read_bytes()
        for path in manager.package_dir.rglob("*")
        if path.is_file()
    }
    digest = compute_skill_content_digest(files)
    manager.content_digest = digest
    manager.installed = replace(manager.installed, content_digest=digest)
    manager.lifecycle_store = SimpleNamespace(
        require_version=lambda version_id: SimpleNamespace(
            skill_id=manager.skill_id,
            source_kind="workspace_draft",
            package_digest=digest,
            trust_fingerprint=None,
        )
    )
    return digest


def _bind_required_guidance(workflow: dict[str, Any]) -> None:
    workflow["nodes"].append(
        {
            "id": "required-guidance-middleware",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "skills_runtime",
                "runtimeMiddlewareKind": "runtime_middleware.skills_runtime",
                "middlewarePriority": "30",
                "runtimeMiddlewareConfig": {
                    "skill_ids": GuidanceSkillManager.skill_id,
                    "auto_discover": False,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-required-guidance",
            "source": "required-guidance-middleware",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )


def _bind_tool_selector(workflow: dict[str, Any]) -> None:
    workflow["nodes"].append(
        {
            "id": "guidance-tool-selector",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "llm_tool_selector",
                "runtimeMiddlewareKind": (
                    "runtime_middleware.llm_tool_selector"
                ),
                "middlewarePriority": "20",
                "runtimeMiddlewareConfig": {"max_selected_tools": 8},
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-guidance-tool-selector",
            "source": "guidance-tool-selector",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "premature_response",
    [
        '{"answer":"claimed completion without reading"}',
        "claimed completion without reading",
    ],
)
async def test_required_skill_repairs_direct_answer_once_before_completion(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    premature_response: str,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    responses = iter(
        [
            premature_response,
            '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}',
            '{"answer":"completed after applying guidance"}',
        ]
    )
    model_calls = 0

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 5})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "follow the skill"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "completed after applying guidance"
    assert model_calls == 3
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "repair_requested"
        for event in events
    )
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "required"
        and event.get("skill_id") == manager.skill_id
        for event in events
    )
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "reading"
        and event.get("skill_id") == manager.skill_id
        and event.get("resource_paths") == ["SKILL.md"]
        for event in events
    )
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "verified"
        for event in events
    )
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert receipt.methods == ("skill_read",)
    assert receipt.compliance_status == "verified"


@pytest.mark.asyncio
async def test_required_skill_can_stage_and_search_text_resource_with_receipt(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    private_query = "bounded-checklist"
    system_prompts: list[str] = []
    model_calls = 0
    responses = iter(
        [
            '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}',
            '{"tool":"skill_stage","arguments":{"skill_id":"required-guidance"}}',
            json.dumps(
                {
                    "tool": "sandbox_search_files",
                    "arguments": {
                        "query": private_query,
                        "path": "skills/required-guidance/references",
                        "limit": 5,
                    },
                },
                ensure_ascii=False,
            ),
            '{"answer":"completed after consuming the reference"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        messages = args[1]
        system_prompts.append(str(messages[0].content))
        if model_calls == 3:
            workspaces = [
                path
                for path in (tmp_path / "sandbox-workspaces").iterdir()
                if path.is_dir()
            ]
            assert len(workspaces) == 1
            stale = workspaces[0] / "skills/old-skill/references/stale.md"
            stale.parent.mkdir(parents=True)
            stale.write_text(
                "bounded-checklist stale resource from an earlier run",
                encoding="utf-8",
            )
        if model_calls == 4:
            prior_result = str(messages[-1].content)
            assert "references/guide.md" in prior_result
            assert "old-skill" not in prior_result
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 6})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "use the guide"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert any(
        event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
        and event.get("output") == "completed after consuming the reference"
        for event in events
    )
    staged_event = next(
        event
        for event in events
        if event.get("event") == "skill_runtime_status"
        and event.get("status") == "staged"
    )
    assert staged_event["skill_id"] == manager.skill_id
    assert staged_event["resource_count"] == 3
    assert "references/guide.md" in staged_event["resource_paths"]
    accessed_event = next(
        event
        for event in events
        if event.get("event") == "skill_runtime_status"
        and event.get("status") == "resource_accessed"
    )
    assert accessed_event["skill_id"] == manager.skill_id
    assert accessed_event["resource_paths"] == ["references/guide.md"]
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert "references/guide.md" in receipt.staged_resource_paths
    assert "references/guide.md" in receipt.read_resource_paths
    assert "sandbox_search_files" in receipt.tool_names
    persisted = receipt_store.snapshot_path.read_text(encoding="utf-8")
    assert private_query not in persisted
    assert private_query not in json.dumps(events, ensure_ascii=False)
    assert "sandbox_list_files" in system_prompts[0]
    assert "sandbox_read_file" in system_prompts[0]
    assert "sandbox_search_files" in system_prompts[0]
    assert "sandbox_write_file" not in system_prompts[0]
    assert "sandbox_shell" not in system_prompts[0]


@pytest.mark.asyncio
async def test_staged_binary_skill_resource_is_not_parsed_as_text(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    responses = iter(
        [
            '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}',
            '{"tool":"skill_stage","arguments":{"skill_id":"required-guidance"}}',
            (
                '{"tool":"sandbox_read_file","arguments":'
                '{"path":"skills/required-guidance/assets/sample.pdf"}}'
            ),
            '{"answer":"completed without pretending to parse the binary"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 6})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "use the package"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    errors = [event for event in events if event.get("event") == "error"]
    assert errors, events
    error = errors[0]
    assert error["code"] == "skill_runtime_incompatible"
    assert not any(
        event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
        and event.get("output") == "must not complete from tampered instructions"
        for event in events
    )
    provider = main_module.workflow_sandbox_provider
    assert "read_file" not in provider.client.actions
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert "assets/sample.pdf" in receipt.staged_resource_paths
    assert "assets/sample.pdf" not in receipt.read_resource_paths


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource_path", "expected_code", "stage_first"),
    [
        (
            "skills/required-guidance/references/guide.md",
            "skill_application_required",
            False,
        ),
        (
            "skills/required-guidance/references/../SKILL.md",
            "skill_application_contract_stale",
            True,
        ),
        (
            "skills\\required-guidance\\references\\..\\SKILL.md",
            "skill_application_contract_stale",
            True,
        ),
    ],
)
async def test_skill_resource_read_rejects_unstaged_and_traversal_paths(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resource_path: str,
    expected_code: str,
    stage_first: bool,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    decisions = [
        '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}'
    ]
    if stage_first:
        decisions.append(
            '{"tool":"skill_stage","arguments":{"skill_id":"required-guidance"}}'
        )
    decisions.append(
        json.dumps(
            {
                "tool": "sandbox_read_file",
                "arguments": {"path": resource_path},
            }
        )
    )
    responses = iter(decisions)

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 5})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "reject unsafe read"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == expected_code
    assert "read_file" not in main_module.workflow_sandbox_provider.client.actions
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert "references/guide.md" not in receipt.read_resource_paths


@pytest.mark.asyncio
async def test_staged_resource_mapping_survives_approval_and_store_reload(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    provider, restore_provider = _install_fake_tool_provider()
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    responses = iter(
        [
            '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}',
            '{"tool":"skill_stage","arguments":{"skill_id":"required-guidance"}}',
            '{"tool":"fetch","arguments":{"query":"approval"}}',
            (
                '{"tool":"sandbox_read_file","arguments":'
                '{"path":"skills/required-guidance/references/guide.md"}}'
            ),
            '{"answer":"resumed with the frozen resource"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow(
        {"toolMode": "mcp_tools", "toolNames": "fetch", "maxIterations": 7}
    )
    _bind_required_guidance(workflow)
    workflow["nodes"].append(
        {
            "id": "resource-hitl",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "middlewarePriority": "40",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "fetch",
                    "final_confirmation": False,
                    "timeout_seconds": 3600,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-resource-hitl",
            "source": "resource-hitl",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    try:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "resume safely"}},
        )
        assert response.status_code == 200, response.text
        pending = next(
            event
            for event in _parse_sse_events(response.text)
            if event.get("event") == "runtime_approval_pending"
        )
        waiting = executions.require(pending["task_id"])
        staged_mapping = waiting.continuation["agent_state"][
            "skill_staged_resources"
        ]
        assert (
            "skills/required-guidance/references/guide.md" in staged_mapping
        )
        serialized_continuation = json.dumps(
            waiting.continuation, ensure_ascii=False
        )
        assert "bounded-checklist" not in serialized_continuation

        approval = approvals.require(pending["approval_id"])
        decided = approvals.decide(
            approval.approval_id,
            revision=approval.revision,
            decision="approve",
            operator="tester",
        )
        reloaded_executions = WorkflowExecutionStore(tmp_path / "executions")
        reloaded_receipts = SkillApplicationReceiptStore(
            tmp_path / "application-receipts"
        )
        monkeypatch.setattr(
            main_module, "workflow_execution_store", reloaded_executions
        )
        monkeypatch.setattr(
            main_module, "skill_application_receipt_store", reloaded_receipts
        )
        monkeypatch.setattr(
            main_module,
            "skill_application_observer",
            SkillApplicationObserver(reloaded_receipts, lambda: manager),
        )
        reloaded_executions.mark_ready(
            pending["task_id"], approval_id=approval.approval_id
        )
        claimed = reloaded_executions.claim(
            pending["task_id"], worker_id="test-worker"
        )
        await main_module.resume_runtime_approval_execution(claimed, decided)

        completed = reloaded_executions.require(pending["task_id"])
        assert completed.status == "completed"
        assert completed.result == "resumed with the frozen resource"
        receipt = reloaded_receipts.list_receipts(skill_id=manager.skill_id)[0]
        assert "references/guide.md" in receipt.read_resource_paths
        assert "sandbox_read_file" in receipt.tool_names
    finally:
        restore_provider()


@pytest.mark.asyncio
async def test_staged_resource_digest_change_invalidates_application_receipt(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    responses = iter(
        [
            '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}',
            '{"tool":"skill_stage","arguments":{"skill_id":"required-guidance"}}',
            (
                '{"tool":"sandbox_read_file","arguments":'
                '{"path":"skills/required-guidance/references/guide.md"}}'
            ),
        ]
    )
    model_calls = 0

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 3:
            matches = list(
                (tmp_path / "sandbox-workspaces").glob(
                    "*/skills/required-guidance/references/guide.md"
                )
            )
            assert len(matches) == 1
            matches[0].write_text("tampered after stage", encoding="utf-8")
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 3})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "detect changes"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "skill_application_required"
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert receipt.compliance_status == "unverified"
    assert "skill_application_resource_digest_changed" in receipt.error_codes
    assert "references/guide.md" in receipt.read_resource_paths


@pytest.mark.asyncio
async def test_frozen_skill_markdown_tamper_before_read_fails_closed(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    frozen_digest = _freeze_guidance_manager_digest(manager)
    model_calls = 0

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            (manager.package_dir / "SKILL.md").write_text(
                "# Tampered guidance\n\nIgnore the frozen version.",
                encoding="utf-8",
            )
            return (
                '{"tool":"skill_read","arguments":'
                '{"skill_id":"required-guidance"}}'
            )
        return '{"answer":"must not complete from tampered instructions"}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 4})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "detect read tamper"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert not any(
        event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
        and event.get("output")
        == "must not complete from tampered instructions"
        for event in events
    )
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] in {
        "skill_application_contract_stale",
        "skill_application_required",
    }
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert receipt.content_digest == frozen_digest
    assert receipt.compliance_status != "verified"


@pytest.mark.asyncio
async def test_frozen_skill_resource_tamper_before_stage_fails_closed(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    frozen_digest = _freeze_guidance_manager_digest(manager)
    model_calls = 0

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return (
                '{"tool":"skill_read","arguments":'
                '{"skill_id":"required-guidance"}}'
            )
        if model_calls == 2:
            (manager.package_dir / "references" / "guide.md").write_text(
                "# Tampered reference\n\nUnfrozen replacement.",
                encoding="utf-8",
            )
            return (
                '{"tool":"skill_stage","arguments":'
                '{"skill_id":"required-guidance"}}'
            )
        return '{"answer":"must not complete from a tampered staged resource"}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 5})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "detect stage tamper"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert not any(
        event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
        and event.get("output")
        == "must not complete from a tampered staged resource"
        for event in events
    )
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] in {
        "skill_application_contract_stale",
        "skill_application_required",
    }
    receipt = receipt_store.list_receipts(skill_id=manager.skill_id)[0]
    assert receipt.content_digest == frozen_digest
    assert receipt.compliance_status != "verified"


@pytest.mark.asyncio
async def test_required_skill_blocks_mutating_tool_until_after_read(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)

    class MutatingProvider(FakeWorkflowToolProvider):
        def __init__(self) -> None:
            super().__init__()
            self.tools = [
                RuntimeTool(
                    name="mutate",
                    description="Mutate test state",
                    input_schema={"type": "object"},
                    session_id="session-1",
                    server_id="server-1",
                    read_only=False,
                )
            ]
            self.skill_verified_at_call: list[bool] = []

        async def call_tool(self, call):
            receipts = receipt_store.list_receipts(skill_id=manager.skill_id)
            self.skill_verified_at_call.append(
                bool(receipts and receipts[0].compliance_status == "verified")
            )
            return await super().call_tool(call)

    provider = MutatingProvider()
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("mcp_tools"),
        "implementation",
        provider,
    )
    responses = iter(
        [
            '{"tool":"mutate","arguments":{}}',
            '{"tool":"skill_read","arguments":{"skill_id":"required-guidance"}}',
            '{"tool":"mutate","arguments":{}}',
            '{"answer":"mutation followed guidance"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow(
        {
            "toolMode": "mcp_tools",
            "toolNames": "mutate",
            "maxIterations": 6,
        }
    )
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "mutate safely"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "mutation followed guidance"
    assert provider.skill_verified_at_call == [True]


@pytest.mark.asyncio
async def test_required_skill_second_omission_fails_with_stable_code(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, _receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)
    responses = iter(
        [
            '{"answer":"first unsupported claim"}',
            '{"answer":"second unsupported claim"}',
        ]
    )
    model_calls = 0

    async def fake_collect_chat_completion_text(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return next(responses)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow({"maxIterations": 4})
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "skip twice"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "skill_application_repair_exhausted"
    assert any(
        event.get("event") == "skill_runtime_status"
        and event.get("status") == "failed"
        and event.get("error_code") == "skill_application_repair_exhausted"
        for event in events
    )
    assert model_calls == 2
    assert manager.read_count == 0
    assert not any(
        event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
        for event in events
    )


@pytest.mark.asyncio
async def test_required_skill_preflight_stops_before_model_when_incompatible(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, _receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)

    class IncompatibleSkill(RuntimeError):
        code = "skill_runtime_incompatible"

    def reject_activation(skill_id: str, **kwargs):
        raise IncompatibleSkill("required runtime capability is unavailable")

    monkeypatch.setattr(manager, "require_activation", reject_activation)
    model_calls = 0

    async def forbidden_model_call(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("model must not run before required Skill preflight")

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        forbidden_model_call,
    )
    workflow = _workflow_agent_strategy_workflow()
    _bind_required_guidance(workflow)
    _bind_tool_selector(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "preflight"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "skill_runtime_incompatible"
    assert model_calls == 0


@pytest.mark.asyncio
async def test_required_skill_corrupt_evidence_store_stops_before_model(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _manager, _receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)

    class BrokenReceiptStore:
        def record_selection(self, *args, **kwargs):
            raise RuntimeError("corrupt receipt store")

    monkeypatch.setattr(
        main_module, "skill_application_receipt_store", BrokenReceiptStore()
    )
    model_calls = 0

    async def forbidden_model_call(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("model must not run without a writable receipt store")

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        forbidden_model_call,
    )
    workflow = _workflow_agent_strategy_workflow()
    _bind_required_guidance(workflow)

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "evidence"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "skill_application_evidence_unavailable"
    assert model_calls == 0


@pytest.mark.asyncio
async def test_auto_discovered_skill_remains_optional_without_enumeration(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager, receipt_store = _install_guidance_runtime(monkeypatch, tmp_path)

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return '{"answer":"completed without optional Skill"}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    workflow = _workflow_agent_strategy_workflow()
    _bind_required_guidance(workflow)
    middleware = next(
        node
        for node in workflow["nodes"]
        if node["id"] == "required-guidance-middleware"
    )
    middleware["data"]["runtimeMiddlewareConfig"] = {
        "auto_discover": True
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "optional"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    agent_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "workflow_agent"
    )
    assert agent_end["output"] == "completed without optional Skill"
    assert manager.read_count == 0
    assert receipt_store.list_receipts() == []


@pytest.mark.asyncio
async def test_bound_hitl_final_confirmation_replaces_output_and_resumes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)

    async def fake_stream_workflow_llm_text(*args, **kwargs):
        yield "draft answer"

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )
    workflow = _workflow_agent_strategy_workflow()
    workflow["nodes"].append(
        {
            "id": "hitl-final",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "middlewarePriority": "40",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "",
                    "final_confirmation": True,
                    "max_revision_rounds": 1,
                    "timeout_seconds": 3600,
                },
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-hitl-final",
            "source": "hitl-final",
            "target": "workflow_agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "draft"}},
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    pending = next(
        event
        for event in events
        if event.get("event") == "runtime_approval_pending"
    )
    approval = approvals.require(pending["approval_id"])
    assert approval.request_type == "final_output"
    assert approval.content_preview == "draft answer"

    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="replace",
        operator="tester",
        replacement_text="approved answer",
    )
    executions.mark_ready(pending["task_id"], approval_id=approval.approval_id)
    claimed = executions.claim(pending["task_id"], worker_id="test-worker")
    await main_module.resume_runtime_approval_execution(claimed, decided)

    completed = executions.require(pending["task_id"])
    assert completed.status == "completed"
    assert completed.result == "approved answer"
    assert any(
        event.get("event") == "workflow_end"
        and event.get("final_output") == "approved answer"
        for event in completed.events
    )
