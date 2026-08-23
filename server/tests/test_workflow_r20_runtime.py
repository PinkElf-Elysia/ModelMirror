from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

import server.main as main_module
from server.main import app
from server.xpert_runtime.approval_store import RuntimeApprovalStore
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.toolset import RuntimeTool, RuntimeToolCall, RuntimeToolResult
from server.workflow_native.r20_nodes import mcp_schema_checksum


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _human_workflow(mode: str) -> dict:
    return {
        "id": f"human-{mode}",
        "title": f"human {mode}",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "human",
                "type": "human_intervention",
                "data": {
                    "kind": "human_intervention",
                    "contractVersion": 2,
                    "interactionMode": mode,
                    "prompt": "Review {{user_input}}",
                    "outputVariable": "human_result",
                    "timeoutSeconds": 3600,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "human_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "human"},
            {"id": "e2", "source": "human", "target": "output"},
        ],
    }


class _ExactMCPProvider:
    def __init__(self, *, server_id: str, tool_name: str, input_schema: dict) -> None:
        self.tool = RuntimeTool(
            name=tool_name,
            input_schema=input_schema,
            server_id=server_id,
            session_id="session-current",
        )
        self.calls: list[RuntimeToolCall] = []

    async def find_tool_exact(
        self,
        *,
        server_id: str,
        tool_name: str,
    ) -> RuntimeTool | None:
        if server_id == self.tool.server_id and tool_name == self.tool.name:
            return self.tool
        return None

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        self.calls.append(call)
        return RuntimeToolResult(
            output="controlled result",
            metadata={"content_types": ["text"]},
        )


def _mcp_workflow(*, schema: dict, sentinel: str) -> dict:
    return {
        "id": "controlled-mcp",
        "title": "controlled MCP",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "mcp",
                "type": "mcp_tool",
                "data": {
                    "kind": "mcp_tool",
                    "contractVersion": 2,
                    "serverId": "server-a",
                    "toolName": "lookup",
                    "inputSchemaChecksum": mcp_schema_checksum(schema),
                    "argumentMode": "fields",
                    "argumentBindings": [
                        {
                            "id": "argument_query",
                            "name": "query",
                            "binding": {"source": "literal", "value": sentinel},
                        }
                    ],
                    "outputVariable": "mcp_result",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "mcp_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "mcp"},
            {"id": "e2", "source": "mcp", "target": "output"},
        ],
    }


async def _start_waiting(
    client: httpx.AsyncClient,
    *,
    mode: str,
) -> tuple[dict, dict]:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _human_workflow(mode),
            "inputs": {"user_input": "synthetic order"},
        },
    )
    assert response.status_code == 200, response.text
    events = _events(response)
    pending = next(
        event for event in events if event.get("event") == "human_intervention_pending"
    )
    runtime_pending = next(
        event for event in events if event.get("event") == "runtime_approval_pending"
    )
    assert pending["approval_id"] == runtime_pending["approval_id"]
    assert pending["revision"] == 1
    assert pending["interaction_mode"] == mode
    assert pending["expires_at"] > 0
    return pending, runtime_pending


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "decision", "replacement", "expected"),
    [
        ("input", "replace", "operator supplied value", "operator supplied value"),
        ("approval", "approve", None, "approved"),
    ],
)
async def test_human_intervention_v2_resumes_once_with_typed_mode_result(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    decision: str,
    replacement: str | None,
    expected: str,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)

    pending, runtime_pending = await _start_waiting(client, mode=mode)
    task_id = runtime_pending["task_id"]
    waiting = executions.require(task_id)
    assert waiting.status == "waiting"
    assert waiting.continuation["agent_state"]["interaction_mode"] == mode

    approval = approvals.require(pending["approval_id"])
    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision=decision,
        operator="tester",
        replacement_text=replacement,
    )
    executions.mark_ready(task_id, approval_id=approval.approval_id)
    claimed = executions.claim(task_id, worker_id="test-worker")
    await main_module.resume_runtime_approval_execution(claimed, decided)

    completed = executions.require(task_id)
    assert completed.status == "completed"
    assert completed.result == expected
    assert completed.continuation == {}
    assert sum(event.get("event") == "workflow_end" for event in completed.events) == 1


@pytest.mark.asyncio
async def test_human_intervention_v2_rejection_reason_never_enters_execution_record(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    sentinel = "R20_REJECTION_REASON_SENTINEL"

    pending, runtime_pending = await _start_waiting(client, mode="approval")
    task_id = runtime_pending["task_id"]
    approval = approvals.require(pending["approval_id"])
    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="reject",
        operator="tester",
        message=sentinel,
    )
    executions.mark_ready(task_id, approval_id=approval.approval_id)
    claimed = executions.claim(task_id, worker_id="test-worker")
    with pytest.raises(
        main_module.WorkflowStreamFailure,
        match="HUMAN_INTERVENTION_REJECTED",
    ):
        await main_module.resume_runtime_approval_execution(claimed, decided)

    failed = executions.require(task_id)
    assert failed.status == "failed"
    public = WorkflowExecutionStore.serialize_public(failed)
    assert sentinel not in json.dumps(public, ensure_ascii=False)
    assert sentinel not in json.dumps(failed.continuation, ensure_ascii=False)
    assert "HUMAN_INTERVENTION_REJECTED" in str(failed.error)


@pytest.mark.asyncio
async def test_r20_approval_validation_rejects_stale_cancelled_execution_before_decision(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)

    pending, runtime_pending = await _start_waiting(client, mode="approval")
    approval = approvals.require(pending["approval_id"])
    executions.cancel(runtime_pending["task_id"], error="operator cancelled")

    with pytest.raises(HTTPException) as caught:
        await main_module.validate_runtime_approval_decision(
            approval,
            SimpleNamespace(decision="approve"),
        )

    assert caught.value.status_code == 409
    assert approvals.require(approval.approval_id).status == "pending"


@pytest.mark.asyncio
async def test_mcp_tool_v2_requires_read_only_approval_before_exact_provider_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    provider = _ExactMCPProvider(
        server_id="server-a",
        tool_name="lookup",
        input_schema=schema,
    )
    capability = main_module.runtime_capabilities.require("mcp_tools")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    monkeypatch.setattr(capability, "implementation", provider)
    monkeypatch.setenv("WORKFLOW_MCP_TOOLS_ENABLED", "true")
    sentinel = "R20_PRIVATE_MCP_ARGUMENT_SENTINEL"

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _mcp_workflow(schema=schema, sentinel=sentinel),
            "inputs": {"user_input": "synthetic"},
        },
    )

    assert response.status_code == 200, response.text
    pending = next(
        event
        for event in _events(response)
        if event.get("event") == "runtime_approval_pending"
    )
    assert provider.calls == []
    approval = approvals.require(pending["approval_id"])
    serialized_approval = json.dumps(
        RuntimeApprovalStore.serialize(approval),
        ensure_ascii=False,
    )
    assert approval.allowed_decisions == ["approve", "reject"]
    assert approval.arguments == {"query": "[已脱敏]"}
    assert approval.metadata["arguments_read_only"] is True
    assert sentinel not in serialized_approval

    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="approve",
        operator="tester",
    )
    executions.mark_ready(approval.task_id, approval_id=approval.approval_id)
    claimed = executions.claim(approval.task_id, worker_id="test-worker")
    await main_module.resume_runtime_approval_execution(claimed, decided)

    assert len(provider.calls) == 1
    assert provider.calls[0].metadata["server_id"] == "server-a"
    assert provider.calls[0].arguments == {"query": sentinel}
    completed = executions.require(approval.task_id)
    assert completed.status == "completed"
    assert "controlled result" in completed.result
    assert completed.continuation == {}
