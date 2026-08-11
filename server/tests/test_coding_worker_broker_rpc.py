from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCClient, BrokerRPCError, BrokerRPCServer
from server.coding_worker.broker_mcp import build_server
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import FrozenCheck, ToolBroker
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


class _Executor:
    calls: list[tuple[str, ...]]

    def __init__(self) -> None:
        self.calls = []

    async def run_process(self, **kwargs: object) -> dict[str, object]:
        argv = tuple(str(item) for item in kwargs["argv"])  # type: ignore[index]
        self.calls.append(argv)
        return {"exit_code": 0, "output": "approved\n"}


async def _rpc(tmp_path: Path) -> tuple[BrokerRPCServer, str, str, _Executor]:
    source = WorkspaceSource(kind="manifest", source_id="rpc", revision="h0")
    workspace = WorkspaceBroker(
        tmp_path / "workspace",
        {"manifest": InMemoryWorkspaceSourceAdapter({("rpc", "h0"): {"app.py": b"hello\n"}})},
        id_key=b"r" * 32,
    )
    prepared = await workspace.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    task = store.create_task(
        TaskSpec(
            client_task_id="rpc",
            origin=Origin(module="tests", object_id="rpc"),
            objective="inspect",
            workspace_source=source,
            acceptance=AcceptanceContract(
                contract_id="contract",
                required_checks=(
                    AcceptanceCheck(check_id="check", label="check", kind="command"),
                ),
            ),
            model_route="coding/default",
            policy_profile=PolicyProfile.DEVELOP,
        )
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id)
    executor = _Executor()
    server = BrokerRPCServer(
        ToolBroker(
            store=store,
            workspace_broker=workspace,
            frozen_checks={"check": FrozenCheck(check_id="check", argv=("python", "-V"))},
            executor=executor,
        )
    )
    endpoint = await server.start_tcp_for_tests()
    return server, task.task_id, endpoint, executor


@pytest.mark.asyncio
async def test_rpc_is_task_token_bound_and_returns_provider_neutral_result(
    tmp_path: Path,
) -> None:
    server, task_id, endpoint, _ = await _rpc(tmp_path)
    token = server.register_task(task_id)
    client = BrokerRPCClient(endpoint, token=token, task_id=task_id)
    result = await client.call(
        operation_id="rpc-list", tool_name="list_files", arguments={}
    )
    assert result["tool_name"] == "list_files"
    assert result["data"]["entries"][0]["display_path"] == "app.py"
    assert "workspace" not in result and "path" not in result
    server.revoke_task(task_id)
    with pytest.raises(BrokerRPCError) as revoked:
        await client.call(operation_id="rpc-revoked", tool_name="list_files", arguments={})
    assert revoked.value.code == "broker_unauthorized"
    await server.close()


@pytest.mark.asyncio
async def test_rpc_rejects_wrong_token_without_executing_operation(tmp_path: Path) -> None:
    server, task_id, endpoint, _ = await _rpc(tmp_path)
    server.register_task(task_id)
    attacker = BrokerRPCClient(endpoint, token="x" * 48, task_id=task_id)
    with pytest.raises(BrokerRPCError) as denied:
        await attacker.call(
            operation_id="rpc-attacker", tool_name="list_files", arguments={}
        )
    assert denied.value.code == "broker_unauthorized"
    with pytest.raises(Exception):
        server.broker.store.get_operation("rpc-attacker")
    await server.close()


@pytest.mark.asyncio
async def test_mcp_exposes_only_modelmirror_broker_tools() -> None:
    client = BrokerRPCClient(
        "tcp:127.0.0.1:1", token="x" * 48, task_id="task_schema"
    )
    tools = await build_server(client).list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "list_files",
        "read_file",
        "search_text",
        "workspace_diff",
        "write_file",
        "delete_file",
        "list_acceptance_checks",
        "run_check",
        "run_command",
        "install_dependencies",
        "start_service",
        "service_status",
        "service_input",
        "stop_service",
    }
    write = next(tool for tool in tools if tool.name == "write_file")
    assert set(write.inputSchema["required"]) == {"operation_id", "path", "content"}
    assert "content_sha256" not in write.inputSchema["properties"]
    command = next(tool for tool in tools if tool.name == "run_command")
    assert set(command.inputSchema["required"]) == {"operation_id", "argv"}
    assert "lease_id" not in command.inputSchema["properties"]
    install = next(tool for tool in tools if tool.name == "install_dependencies")
    assert set(install.inputSchema["required"]) == {"operation_id"}
    assert "lease_id" not in install.inputSchema["properties"]
    assert "network_lease_id" not in install.inputSchema["properties"]
    service = next(tool for tool in tools if tool.name == "start_service")
    assert set(service.inputSchema["required"]) == {"operation_id", "argv"}
    assert "lease_id" not in service.inputSchema["properties"]


@pytest.mark.asyncio
async def test_mcp_computes_write_digest_inside_trusted_adapter() -> None:
    calls: list[dict[str, object]] = []

    class RecordingClient:
        async def call(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"ok": True}

    content = "discount = 0.0\n"
    await build_server(RecordingClient()).call_tool(
        "write_file",
        {"operation_id": "write-discount", "path": "pricing.py", "content": content},
    )
    assert calls == [
        {
            "operation_id": "write-discount",
            "tool_name": "write_file",
            "arguments": {
                "path": "pricing.py",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            "lease_id": None,
            "network_lease_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_rpc_waits_for_exact_approval_and_executes_same_operation_once(
    tmp_path: Path,
) -> None:
    server, task_id, endpoint, executor = await _rpc(tmp_path)
    client = BrokerRPCClient(
        endpoint, token=server.register_task(task_id), task_id=task_id
    )
    pending = asyncio.create_task(
        client.call(
            operation_id="rpc-command",
            tool_name="run_command",
            arguments={"argv": ["python", "-m", "pytest"], "timeout_seconds": 30},
        )
    )
    for _ in range(100):
        approvals = server.broker.store.list_approvals(task_id)
        if approvals:
            break
        await asyncio.sleep(0.01)
    assert len(approvals) == 1
    assert server.broker.store.get_task(task_id).state is TaskState.WAITING_APPROVAL
    decided = server.broker.store.decide_approval(approvals[0].approval_id, approved=True)
    assert decided.lease is not None
    server.broker.store.transition(
        task_id, TaskState.RUNNING, expected_state=TaskState.WAITING_APPROVAL
    )
    result = await asyncio.wait_for(pending, timeout=2)
    assert result["state"] == "completed"
    assert result["data"]["output"] == "approved\n"
    assert executor.calls == [("python", "-m", "pytest")]
    await server.close()


@pytest.mark.asyncio
async def test_rpc_rejected_approval_never_reaches_executor(tmp_path: Path) -> None:
    server, task_id, endpoint, executor = await _rpc(tmp_path)
    client = BrokerRPCClient(
        endpoint, token=server.register_task(task_id), task_id=task_id
    )
    pending = asyncio.create_task(
        client.call(
            operation_id="rpc-rejected",
            tool_name="run_command",
            arguments={"argv": ["python", "-V"]},
        )
    )
    for _ in range(100):
        approvals = server.broker.store.list_approvals(task_id)
        if approvals:
            break
        await asyncio.sleep(0.01)
    assert len(approvals) == 1
    server.broker.store.decide_approval(approvals[0].approval_id, approved=False)
    server.broker.store.transition(
        task_id, TaskState.RUNNING, expected_state=TaskState.WAITING_APPROVAL
    )
    with pytest.raises(BrokerRPCError) as rejected:
        await asyncio.wait_for(pending, timeout=2)
    assert rejected.value.code == "approval_rejected"
    assert executor.calls == []
    assert server.broker.store.get_operation("rpc-rejected").state.value == "failed"
    await server.close()


@pytest.mark.asyncio
async def test_rpc_lists_and_enforces_task_acceptance_checks(tmp_path: Path) -> None:
    server, task_id, endpoint, executor = await _rpc(tmp_path)
    client = BrokerRPCClient(
        endpoint, token=server.register_task(task_id), task_id=task_id
    )
    listed = await client.call(
        operation_id="rpc-checks", tool_name="list_acceptance_checks", arguments={}
    )
    assert listed["data"] == {
        "checks": [
            {"check_id": "check", "label": "check", "kind": "command", "required": True}
        ]
    }
    with pytest.raises(BrokerRPCError) as denied:
        await client.call(
            operation_id="rpc-other-check",
            tool_name="run_check",
            arguments={"check_id": "other"},
        )
    assert denied.value.code == "check_not_allowed"
    assert executor.calls == []
    await server.close()
