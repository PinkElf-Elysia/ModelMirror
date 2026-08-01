from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.xpert_runtime import (
    CLIENT_TOOLS,
    CapabilityRegistry,
    ClientToolConnectionManager,
    ClientToolConflictError,
    ClientToolCoordinator,
    ClientToolStore,
    ClientToolsetProvider,
    MiddlewareContext,
    MiddlewarePipeline,
    RuntimeApprovalStore,
    RuntimeInterrupt,
    RuntimeMiddlewareSpec,
    RuntimeToolCall,
    WorkflowExecutionStore,
    build_human_in_the_loop_middleware,
    client_tool_schema_hash,
    run_tool_with_runtime,
)
from server.xpert_runtime import client_tool_api


def pair_host(store: ClientToolStore, *, name: str = "Test Chrome"):
    _pairing, code = store.create_pairing(name=name)
    schemas = {tool.name: client_tool_schema_hash(tool) for tool in CLIENT_TOOLS}
    host, token = store.consume_pairing(
        code,
        version="1.0.0",
        capabilities=[{"name": tool.name} for tool in CLIENT_TOOLS],
        schema_hashes=schemas,
    )
    store.connect_host(
        host.host_id,
        connection_id="connection-1",
        version="1.0.0",
        capabilities=[{"name": tool.name} for tool in CLIENT_TOOLS],
        schema_hashes=schemas,
        bound_tab={
            "bound": True,
            "tabId": 12,
            "origin": "https://example.com",
            "title": "Example",
        },
    )
    return host, token


def client_call(host_id: str, tool_name: str = "host_page_read") -> RuntimeToolCall:
    return RuntimeToolCall(
        tool_name,
        {} if tool_name == "host_page_read" else {"ref": "mm:rev:1"},
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "node_id": "agent-1",
            "iteration": 1,
            "tool_call_id": "call-1",
            "client_tools_config": {
                "clientHostId": host_id,
                "clientToolNames": tool_name,
                "clientToolTimeoutSeconds": 1800,
                "requireBoundTab": True,
            },
        },
    )


def test_pairing_token_is_one_time_hashed_and_persistent(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path / "runtime", tmp_path / "artifacts")
    pairing, code = store.create_pairing(name="Personal Chrome")
    host, token = store.consume_pairing(code, version="1", capabilities=[])

    assert len(code) == 8 and code.isdigit()
    assert host.token_prefix == token[:8]
    snapshot = (tmp_path / "runtime" / "client_tools.json").read_text("utf-8")
    assert token not in snapshot
    assert code not in snapshot
    assert store.authenticate(host.host_id, token).host_id == host.host_id
    with pytest.raises(Exception, match="invalid or expired"):
        store.consume_pairing(code, version="1", capabilities=[])

    restored = ClientToolStore(tmp_path / "runtime", tmp_path / "artifacts")
    assert restored.authenticate(host.host_id, token).status == "offline"
    assert pairing.pairing_id in snapshot


def test_disconnect_replays_reads_but_marks_running_mutation_uncertain(
    tmp_path: Path,
) -> None:
    store = ClientToolStore(tmp_path)
    host, _token = pair_host(store)
    read = store.create_request(
        operation_id="read-operation",
        tool_call_id="read-call",
        host_id=host.host_id,
        task_id="task-1",
        run_id="run-1",
        node_id="agent",
        scope_type="workflow",
        scope_id="task-1:agent",
        tool_name="host_page_read",
        arguments={},
        schema_hash="read-schema",
        timeout_seconds=60,
    )
    mutation = store.create_request(
        operation_id="click-operation",
        tool_call_id="click-call",
        host_id=host.host_id,
        task_id="task-1",
        run_id="run-1",
        node_id="agent",
        scope_type="workflow",
        scope_id="task-1:agent",
        tool_name="host_page_click",
        arguments={"ref": "mm:rev:1"},
        schema_hash="click-schema",
        timeout_seconds=60,
    )
    store.mark_dispatched(read.request_id)
    store.mark_dispatched(mutation.request_id)
    store.mark_running(mutation.request_id, host_id=host.host_id)

    store.disconnect_host(host.host_id, connection_id="connection-1")

    assert store.require_request(read.request_id).status == "pending"
    assert store.require_request(mutation.request_id).status == "uncertain"
    with pytest.raises(ClientToolConflictError):
        store.complete_request(
            mutation.request_id,
            host_id=host.host_id,
            operation_id="wrong",
            tool_call_id="click-call",
            result="clicked",
        )


@pytest.mark.asyncio
async def test_provider_interrupts_then_returns_matching_host_result(
    tmp_path: Path,
) -> None:
    store = ClientToolStore(tmp_path)
    host, _token = pair_host(store)
    provider = ClientToolsetProvider(store)
    call = client_call(host.host_id)

    with pytest.raises(RuntimeInterrupt) as caught:
        await provider.prepare_dispatch(call)

    assert caught.value.wait_kind == "client_tool"
    request = store.require_request(caught.value.wait_id)
    store.mark_dispatched(request.request_id)
    store.mark_running(request.request_id, host_id=host.host_id)
    store.complete_request(
        request.request_id,
        host_id=host.host_id,
        operation_id=request.operation_id,
        tool_call_id=request.tool_call_id,
        result="bounded page text",
        metadata={"title": "Example", "token": "must-not-survive"},
    )

    result = await provider.call_tool(call)

    assert result.output == "bounded page text"
    assert result.metadata["client_request_id"] == request.request_id
    assert "token" not in json.dumps(result.metadata)


def test_client_request_public_payload_redacts_form_values(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path)
    host, _token = pair_host(store)
    request = store.create_request(
        operation_id="fill-operation",
        tool_call_id="fill-call",
        host_id=host.host_id,
        task_id="task-1",
        run_id="run-1",
        node_id="agent",
        scope_type="workflow",
        scope_id="task-1:agent",
        tool_name="host_page_fill",
        arguments={"ref": "mm:rev:1", "value": "private content"},
        schema_hash="fill-schema",
        timeout_seconds=60,
    )

    public = store.serialize_request(request, include_result=True)

    assert public["arguments"]["value"] == "[redacted]"
    assert "private content" not in json.dumps(public)


def test_expired_request_requires_explicit_retry(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path)
    host, _token = pair_host(store)
    request = store.create_request(
        operation_id="wait-operation",
        tool_call_id="wait-call",
        host_id=host.host_id,
        task_id="task-1",
        run_id="run-1",
        node_id="agent",
        scope_type="workflow",
        scope_id="task-1:agent",
        tool_name="host_page_read",
        arguments={},
        schema_hash="read-schema",
        timeout_seconds=30,
    )

    store.expire_due(now=time.time() + 31)
    assert store.require_request(request.request_id).status == "expired"
    assert store.retry_request(request.request_id).status == "pending"


def test_extension_manifest_has_narrow_permissions() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "client_extension" / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))

    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {
        "activeTab",
        "scripting",
        "storage",
        "alarms",
    }
    assert all("localhost:8000" in item or "127.0.0.1:8000" in item for item in manifest["host_permissions"])
    assert "<all_urls>" not in manifest["host_permissions"]


def test_websocket_pairing_and_token_reauthentication(tmp_path: Path) -> None:
    store = ClientToolStore(tmp_path / "runtime", tmp_path / "artifacts")
    connections = ClientToolConnectionManager(store)
    client_tool_api.configure_runtime_client_tools(store, connections)
    app = FastAPI()
    app.include_router(client_tool_api.router)
    schemas = {tool.name: client_tool_schema_hash(tool) for tool in CLIENT_TOOLS}
    capabilities = [{"name": tool.name} for tool in CLIENT_TOOLS]

    with TestClient(app) as client:
        pairing = client.post(
            "/api/runtime/client-hosts/pairings", json={"name": "Test Chrome"}
        )
        assert pairing.status_code == 200
        pairing_code = pairing.json()["pairing_code"]

        with client.websocket_connect("/api/runtime/client-tools/connect") as socket:
            socket.send_json(
                {
                    "type": "pair",
                    "pairing_code": pairing_code,
                    "version": "1.0.0",
                    "capabilities": capabilities,
                    "schema_hashes": schemas,
                    "bound_tab": {
                        "bound": True,
                        "tabId": 7,
                        "origin": "https://example.com",
                    },
                }
            )
            welcome = socket.receive_json()
            assert welcome["type"] == "welcome"
            assert welcome["paired"] is True
            host_id = welcome["host_id"]
            host_token = welcome["host_token"]
            socket.send_json({"type": "heartbeat"})
            assert socket.receive_json() == {"type": "heartbeat", "ok": True}

        with client.websocket_connect("/api/runtime/client-tools/connect") as socket:
            socket.send_json(
                {
                    "type": "authenticate",
                    "host_id": host_id,
                    "host_token": host_token,
                    "version": "1.0.0",
                    "capabilities": capabilities,
                    "schema_hashes": schemas,
                }
            )
            welcome = socket.receive_json()
            assert welcome["type"] == "welcome"
            assert welcome["paired"] is False
            assert "host_token" not in welcome


@pytest.mark.asyncio
async def test_hitl_resolves_before_client_request_is_dispatched(
    tmp_path: Path,
) -> None:
    client_store = ClientToolStore(tmp_path / "client")
    approval_store = RuntimeApprovalStore(tmp_path / "approvals")
    host, _token = pair_host(client_store)
    provider = ClientToolsetProvider(client_store)
    capabilities = CapabilityRegistry()
    capabilities.register("client_tools", provider)
    middleware = build_human_in_the_loop_middleware(
        RuntimeMiddlewareSpec(
            node_id="hitl-1",
            middleware_id="human_in_the_loop",
            config={"interrupt_on_tools": "host_page_click"},
        ),
        approval_store,
    )
    pipeline = MiddlewarePipeline([middleware])
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )
    call = client_call(host.host_id, "host_page_click")

    with pytest.raises(RuntimeInterrupt) as approval_interrupt:
        await run_tool_with_runtime(
            call,
            capabilities,
            pipeline,
            context,
            capability_name="client_tools",
        )

    assert approval_interrupt.value.wait_kind == "approval"
    assert client_store.list_requests(limit=10) == []

    approved_call = RuntimeToolCall(
        call.tool_name,
        call.arguments,
        {
            **call.metadata,
            "resolved_approval": {
                "approval_id": approval_interrupt.value.wait_id,
                "decision": "approve",
            },
        },
    )
    with pytest.raises(RuntimeInterrupt) as client_interrupt:
        await run_tool_with_runtime(
            approved_call,
            capabilities,
            pipeline,
            context,
            capability_name="client_tools",
        )

    assert client_interrupt.value.wait_kind == "client_tool"
    assert len(client_store.list_requests(limit=10)) == 1


@pytest.mark.asyncio
async def test_coordinator_resumes_completed_client_wait_exactly_once(
    tmp_path: Path,
) -> None:
    store = ClientToolStore(tmp_path / "client")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    host, _token = pair_host(store)
    request = store.create_request(
        operation_id="resume-operation",
        tool_call_id="resume-call",
        host_id=host.host_id,
        task_id="task-resume",
        run_id="run-resume",
        node_id="agent",
        scope_type="workflow",
        scope_id="task-resume:agent",
        tool_name="host_page_read",
        arguments={},
        schema_hash="read-schema",
        timeout_seconds=60,
    )
    store.mark_dispatched(request.request_id)
    store.complete_request(
        request.request_id,
        host_id=host.host_id,
        operation_id=request.operation_id,
        tool_call_id=request.tool_call_id,
        result="client result",
    )
    executions.create(
        task_id="task-resume",
        run_id="run-resume",
        run_type="workflow",
        workflow={"nodes": [], "edges": []},
        inputs={},
    )
    executions.suspend(
        "task-resume",
        wait_kind="client_tool",
        wait_id=request.request_id,
        continuation={"queue": [], "executed": []},
        safe_event={"event": "client_tool_waiting", "request_id": request.request_id},
    )

    class OfflineConnections:
        async def dispatch(self, _request):
            return False

    resumed: list[str] = []

    async def resume(execution, resolved):
        resumed.append(resolved.request_id)
        executions.complete(execution.task_id, result=resolved.result)

    coordinator = ClientToolCoordinator(
        store,
        executions,
        OfflineConnections(),  # type: ignore[arg-type]
        resume,
    )

    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert resumed == [request.request_id]
    assert executions.require("task-resume").status == "completed"
