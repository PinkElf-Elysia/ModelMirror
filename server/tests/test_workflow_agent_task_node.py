from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app
from server.xpert_runtime import (
    RuntimeApprovalStore,
    RuntimeTool,
    RuntimeToolResult,
    WorkflowExecutionStore,
)
from server.xpert_runtime.todo_store import RuntimeTodoStore


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


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

    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        captured["model_id"] = model_id
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        yield "agent "
        yield "result"

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
async def test_workflow_agent_terminal_tool_ends_without_model_summary(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
