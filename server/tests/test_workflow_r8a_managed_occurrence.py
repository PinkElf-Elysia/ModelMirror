"""Real runner and durable ledgers; model/host responses are offline substitutes."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

import server.main as main
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.workflow_gateway import ManagedWorkflowGateway, ManagedWorkflowRoutingError
from server.tests.test_workflow_managed_gateway import qualified_interactive_agent
from server.tests.test_workflow_managed_runtime import (
    MODEL_ID, _events, _workflow,
)
from server.tests.test_xpert_runtime_client_tools import pair_host
from server.xpert_runtime import ClientToolStore, ClientToolsetProvider, RunRegistry
from server.xpert_runtime.execution_store import WorkflowExecutionStore


@pytest.fixture
def runtime(tmp_path, monkeypatch, qualified_interactive_agent):
    service, _ = qualified_interactive_agent
    calls = []

    def response(request):
        calls.append(request)
        decision = (
            {"tool": "host_page_read", "arguments": {}}
            if len(calls) <= 2 else {"answer": "两次读取已完成"}
        )
        frame = {"model": MODEL_ID, "choices": [{"delta": {
            "content": json.dumps(decision, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        return httpx.Response(200, content=(
            "data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n"
        ).encode())

    gateway = ManagedWorkflowGateway.for_router(service, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(response), trust_env=False,
    ))
    executions = WorkflowExecutionStore(tmp_path / "executions")
    clients = ClientToolStore(tmp_path / "clients", tmp_path / "artifacts")
    host, _ = pair_host(clients)
    provider = ClientToolsetProvider(clients)
    runs = []

    original_start = gateway.start_agent_run

    def start(**kwargs):
        run = original_start(**kwargs)
        runs.append((dict(kwargs), run))
        return run

    monkeypatch.setattr(gateway, "start_agent_run", start)

    class Gateway(ManagedWorkflowGateway):
        @classmethod
        def for_router(cls, _service):
            return gateway

    monkeypatch.setattr(main, "ManagedWorkflowGateway", Gateway)
    monkeypatch.setattr(main, "workflow_execution_store", executions)
    monkeypatch.setattr(main, "client_tool_store", clients)
    monkeypatch.setattr(main, "workflow_client_tool_provider", provider)
    monkeypatch.setattr(main, "workflow_task_store", {})
    monkeypatch.setattr(main, "run_registry", RunRegistry())
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://offline.invalid", "synthetic"))
    original = main.runtime_capabilities.require("client_tools").implementation
    main.runtime_capabilities.register("client_tools", provider)
    request = _workflow([
        {"id": "input", "type": "input", "data": {"kind": "input"}},
        {"id": "agent", "type": "workflow_agent", "data": {
            "kind": "workflow_agent", "modelId": MODEL_ID,
            "agentName": "核对", "rolePrompt": "依次读取两次页面。",
            "taskInput": "{{user_input}}", "toolMode": "mcp_tools",
            "agentStrategy": "react", "maxIterations": 5,
            "outputVariable": "answer",
        }},
        {"id": "client", "type": "runtime_middleware", "data": {
            "kind": "runtime_middleware", "runtimeMiddlewareId": "client_tools",
            "runtimeMiddlewareKind": "runtime_middleware.client_tools",
            "runtimeMiddlewareConfig": {"clientHostId": host.host_id,
                "clientToolNames": "host_page_read", "requireBoundTab": True},
        }},
        {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "answer"}},
    ], [
        {"id": "e1", "source": "input", "target": "agent"},
        {"id": "e2", "source": "agent", "target": "output"},
        {"id": "bind", "source": "client", "target": "agent",
         "sourceHandle": "middleware-binding", "targetHandle": "middleware"},
    ])
    yield request, executions, clients, service, runs
    main.runtime_capabilities.register("client_tools", original)


def settle(clients, request_id):
    request = clients.require_request(request_id)
    clients.mark_dispatched(request_id)
    return clients.complete_request(
        request_id, host_id=request.host_id, operation_id=request.operation_id,
        tool_call_id=request.tool_call_id, result="合成页面",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_stores", [False, True])
async def test_two_client_waits_are_distinct_durable_model_occurrences(runtime, monkeypatch, reload_stores):
    request, executions, clients, service, runs = runtime
    events = await _events(await main._run_workflow_response(
        request, None, runtime_execution_source_kind="workflow_classic",
        runtime_task_id="occurrence-task",
    ))
    assert any(event.get("event") == "client_tool_waiting" for event in events), events
    wait_ids = []
    for index in range(2):
        if reload_stores:
            executions = WorkflowExecutionStore(executions.snapshot_path.parent)
            monkeypatch.setattr(main, "workflow_execution_store", executions)
        current = executions.snapshot("occurrence-task")
        assert current.status == "waiting", current.error
        wait_ids.append(current.wait_id)
        resolved = settle(clients, current.wait_id)
        executions.mark_ready(current.task_id, wait_kind="client_tool", wait_id=current.wait_id)
        claimed = executions.claim(current.task_id, worker_id=f"worker-{index}")
        await main.resume_runtime_client_tool_execution(claimed, resolved)
    completed = executions.snapshot("occurrence-task")
    assert completed.status == "completed", completed.error
    assert completed.result == "两次读取已完成"
    assert len(set(wait_ids)) == 2
    assert len(runs) == 3
    assert len({run.run_id for _, run in runs}) == 3
    assert len(service.repository.list_workload_receipts("local")["runs"]) == 3
    receipts = service.repository.list_workload_receipts("local")
    assert all(row["status"] == "passed" for row in receipts["runs"])
    assert len(receipts["calls"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["task", "node", "tool", "pending", "resolved", "state_node", "state_type"])
async def test_invalid_client_occurrence_fails_before_next_model_call(runtime, invalid):
    request, executions, clients, service, runs = runtime
    await _events(await main._run_workflow_response(
        request, None, runtime_execution_source_kind="workflow_classic",
        runtime_task_id="occurrence-task",
    ))
    current = executions.snapshot("occurrence-task")
    resolved = settle(clients, current.wait_id)
    if invalid in {"task", "node", "tool", "pending"}:
        changes = {
            "task": {"task_id": "foreign"}, "node": {"node_id": "foreign"},
            "tool": {"tool_name": "host_page_click"}, "pending": {"status": "pending"},
        }[invalid]
        clients._requests[current.wait_id] = replace(resolved, **changes)
    elif invalid == "resolved":
        resolved = replace(resolved, request_id="foreign")
    else:
        state = executions._items[current.task_id].continuation["agent_state"]
        state["node_id" if invalid == "state_node" else "type"] = "foreign"
    executions.mark_ready(current.task_id, wait_kind="client_tool", wait_id=current.wait_id)
    claimed = executions.claim(current.task_id, worker_id="worker")
    with pytest.raises(main.WorkflowStreamFailure, match="provider_workload_client_wait_invalid"):
        await main.resume_runtime_client_tool_execution(claimed, resolved)
    assert len(runs) == 1
    assert len(service.repository.list_workload_receipts("local")["calls"]) == 1


def old_phase():
    material = json.dumps({"type": "tool_call", "approval_id": None, "revision_round": None},
                          sort_keys=True, separators=(",", ":"))
    return "resume:" + hashlib.sha256(material.encode()).hexdigest()[:16]


def start_client(gateway, wait="wait-a", **kwargs):
    return gateway.start_agent_run(
        source_kind="workflow_classic", execution_reference="task", node_id="agent",
        logical_phase=old_phase(), client_wait_id=wait, **kwargs,
    )


@pytest.mark.parametrize("status", ["running", "failed"])
def test_legacy_evidence_blocks_new_namespace_without_claiming_it(qualified_interactive_agent, status):
    service, _ = qualified_interactive_agent
    gateway = ManagedWorkflowGateway.for_router(service)
    old = gateway.start_agent_run(source_kind="workflow_classic", execution_reference="task",
                                  node_id="agent", logical_phase=old_phase())
    if status == "failed":
        old.finish("failed", reason_code="synthetic_unknown_dispatch")
    before = service.repository.list_workload_receipts("local")
    with pytest.raises(ManagedWorkflowRoutingError) as blocked:
        start_client(gateway)
    assert blocked.value.code == "provider_workload_logical_run_replay_blocked"
    assert service.repository.list_workload_receipts("local") == before


@pytest.mark.parametrize("status", ["running", "failed"])
def test_same_wait_replay_rejected_after_repository_reload(qualified_interactive_agent, status):
    service, _ = qualified_interactive_agent
    gateway = ManagedWorkflowGateway.for_router(service)
    first = start_client(gateway)
    if status == "failed":
        first.finish("failed", reason_code="synthetic_unknown_dispatch")
    storage_dir = service.repository.storage_dir
    service.repository.close()
    service.repository = SQLiteRouterRepository.open(storage_dir, master_key=b"x" * 32)
    restarted = ManagedWorkflowGateway.for_router(service)
    with pytest.raises(ManagedWorkflowRoutingError) as blocked:
        start_client(restarted)
    assert blocked.value.code == "provider_workload_logical_run_replay_blocked"
    assert start_client(restarted, "wait-b").run_id != first.run_id


def test_concurrent_same_wait_claim_has_exactly_one_winner(qualified_interactive_agent):
    service, _ = qualified_interactive_agent
    gateway = ManagedWorkflowGateway.for_router(service)

    def claim(_):
        try:
            return start_client(gateway).run_id
        except ManagedWorkflowRoutingError as exc:
            assert exc.code == "provider_workload_logical_run_replay_blocked"
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(claim, range(6)))
    assert len([value for value in results if value]) == 1
    assert len(service.repository.list_workload_receipts("local")["runs"]) == 1


def test_failed_claim_leaves_no_new_occurrence(qualified_interactive_agent):
    service, _ = qualified_interactive_agent
    repository = service.repository
    with repository._connect() as connection:
        connection.execute("""CREATE TRIGGER reject_synthetic_run AFTER INSERT ON provider_workload_runs
            BEGIN SELECT RAISE(ABORT, 'synthetic-storage-failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic-storage-failure"):
        start_client(ManagedWorkflowGateway.for_router(service))
    assert repository.list_workload_receipts("local")["runs"] == []
    with repository._connect() as connection:
        connection.execute("DROP TRIGGER reject_synthetic_run")
    assert start_client(ManagedWorkflowGateway.for_router(service)).run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("response_kind", ["success", "uncertain"])
async def test_actual_dispatch_receipt_blocks_replay_after_restart(
    qualified_interactive_agent, legacy, response_kind,
):
    service, _ = qualified_interactive_agent
    calls = []

    def response(request):
        calls.append(request)
        if response_kind == "uncertain":
            raise httpx.ReadTimeout("synthetic response lost", request=request)
        return httpx.Response(200, content=(
            'data: {"model":"provider/workflow-model","choices":[{"delta":'
            '{"content":"synthetic"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        ).encode())

    factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(response), trust_env=False)
    gateway = ManagedWorkflowGateway.for_router(service, client_factory=factory)
    run = (
        gateway.start_agent_run(source_kind="workflow_classic", execution_reference="task",
                                node_id="agent", logical_phase=old_phase())
        if legacy else start_client(gateway)
    )
    if response_kind == "uncertain":
        with pytest.raises(ManagedWorkflowRoutingError):
            await run.complete_text(purpose="test", model_id=MODEL_ID,
                                    messages=[{"role": "user", "content": "synthetic"}],
                                    temperature=0, max_tokens=32)
        run.finish("failed", reason_code="synthetic_response_lost")
    else:
        assert await run.complete_text(purpose="test", model_id=MODEL_ID,
                                       messages=[{"role": "user", "content": "synthetic"}],
                                       temperature=0, max_tokens=32) == "synthetic"
        run.finish("passed")
    before = service.repository.list_workload_receipts("local")
    assert len(calls) == 1
    assert len(before["calls"]) == 1
    assert before["calls"][0]["dispatched"]
    storage_dir = service.repository.storage_dir
    service.repository.close()
    service.repository = SQLiteRouterRepository.open(storage_dir, master_key=b"x" * 32)
    restarted = ManagedWorkflowGateway.for_router(service, client_factory=factory)
    with pytest.raises(ManagedWorkflowRoutingError) as blocked:
        start_client(restarted)
    assert blocked.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(calls) == 1
    assert service.repository.list_workload_receipts("local") == before


@pytest.mark.parametrize("wait_id", ["", " ", 1, False])
def test_invalid_wait_identity_cannot_claim_a_run(qualified_interactive_agent, wait_id):
    service, _ = qualified_interactive_agent
    with pytest.raises(ManagedWorkflowRoutingError) as blocked:
        start_client(ManagedWorkflowGateway.for_router(service), wait_id)
    assert blocked.value.code == "provider_workload_client_wait_invalid"
    assert service.repository.list_workload_receipts("local")["runs"] == []
