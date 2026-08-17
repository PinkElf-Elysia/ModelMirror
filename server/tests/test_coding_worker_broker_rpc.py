from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCClient, BrokerRPCError, BrokerRPCServer
from server.coding_worker.broker_mcp import (
    BrokerReplaceChange,
    _replace_change_as_write,
    _workspace_relative_cwd,
    _workspace_relative_shell_script,
    build_server,
)
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    OperationState,
    Origin,
    PolicyProfile,
    RuntimeProtocol,
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


async def _rpc(
    tmp_path: Path,
    *,
    runtime_protocol: RuntimeProtocol = RuntimeProtocol.V16,
) -> tuple[BrokerRPCServer, str, str, _Executor]:
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
        ),
        runtime_protocol=runtime_protocol,
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id)
    if runtime_protocol is RuntimeProtocol.V17:
        store.open_turn_transaction(
            task_id=task.task_id,
            turn_id="turn_rpc_v17",
            workspace_tree_hash=prepared.baseline_tree_hash,
        )
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
        "read_file_range",
        "glob_files",
        "search_text",
        "search_regex",
        "workspace_diff",
        "read_operation_output",
        "code_symbols",
        "code_definition",
        "code_references",
        "code_hover",
        "code_diagnostics",
        "write_file",
        "delete_file",
        "apply_changeset",
        "list_acceptance_checks",
        "run_check",
        "run_command",
        "run_shell",
        "install_dependencies",
        "query_documentation",
        "start_service",
        "service_status",
            "service_input",
            "stop_service",
            "create_subtask",
            "merge_subtask",
            "update_plan",
            "update_todo",
            "request_user_input",
            "compact_context",
        }
    write = next(tool for tool in tools if tool.name == "write_file")
    assert set(write.inputSchema["required"]) == {"operation_id", "path", "content"}
    assert "content_sha256" not in write.inputSchema["properties"]
    changeset = next(tool for tool in tools if tool.name == "apply_changeset")
    assert set(changeset.inputSchema["required"]) == {
        "operation_id",
        "base_tree_hash",
        "changes",
    }
    assert "provider" not in changeset.inputSchema["properties"]
    change_schema = changeset.inputSchema["properties"]["changes"]["items"]
    assert change_schema["discriminator"]["propertyName"] == "kind"
    assert set(change_schema["discriminator"]["mapping"]) == {
        "write",
        "delete",
        "move",
        "patch",
        "replace",
    }
    operation_output = next(
        tool for tool in tools if tool.name == "read_operation_output"
    )
    assert set(operation_output.inputSchema["required"]) == {"operation_id"}
    assert "task_id" not in operation_output.inputSchema["properties"]
    command = next(tool for tool in tools if tool.name == "run_command")
    assert set(command.inputSchema["required"]) == {"operation_id", "argv"}
    assert "lease_id" not in command.inputSchema["properties"]
    shell = next(tool for tool in tools if tool.name == "run_shell")
    assert set(shell.inputSchema["required"]) == {"operation_id", "script"}
    assert "lease_id" not in shell.inputSchema["properties"]
    assert "provider" not in shell.inputSchema["properties"]
    symbols = next(tool for tool in tools if tool.name == "code_symbols")
    assert set(symbols.inputSchema["required"]) == {"entry_id"}
    assert "path" not in symbols.inputSchema["properties"]
    definition = next(tool for tool in tools if tool.name == "code_definition")
    assert set(definition.inputSchema["required"]) == {
        "entry_id",
        "line",
        "character",
    }
    assert "provider" not in definition.inputSchema["properties"]
    install = next(tool for tool in tools if tool.name == "install_dependencies")
    assert set(install.inputSchema["required"]) == {"operation_id"}
    assert {"manager", "action", "requirements"}.issubset(
        install.inputSchema["properties"]
    )
    assert "lease_id" not in install.inputSchema["properties"]
    assert "network_lease_id" not in install.inputSchema["properties"]
    documentation = next(tool for tool in tools if tool.name == "query_documentation")
    assert set(documentation.inputSchema["required"]) == {
        "operation_id",
        "resource_id",
        "document_path",
    }
    assert "url" not in documentation.inputSchema["properties"]
    assert "network_lease_id" not in documentation.inputSchema["properties"]
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
async def test_mcp_changeset_schema_computes_content_and_patch_digests() -> None:
    calls: list[dict[str, object]] = []

    class RecordingClient:
        async def call(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"ok": True}

    patch = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    content = "created\n"
    await build_server(RecordingClient()).call_tool(
        "apply_changeset",
        {
            "operation_id": "structured-change",
            "base_tree_hash": "a" * 64,
            "changes": [
                {
                    "kind": "patch",
                    "path": "app.py",
                    "expected_sha256": "b" * 64,
                    "patch": patch,
                },
                {
                    "kind": "write",
                    "path": "new.py",
                    "expected_absent": True,
                    "content": content,
                },
            ],
        },
    )

    arguments = calls[0]["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["changes"] == [
        {
            "kind": "patch",
            "path": "app.py",
            "expected_sha256": "b" * 64,
            "patch": patch,
            "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        },
        {
            "kind": "write",
            "path": "new.py",
            "expected_absent": True,
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        },
    ]


def test_replace_change_preserves_unrelated_bytes_and_final_newline(
    tmp_path: Path,
) -> None:
    source = "before\nold value\nafter\n"
    target = tmp_path / "app.py"
    target.write_text(source, encoding="utf-8", newline="")
    expected_sha256 = hashlib.sha256(source.encode()).hexdigest()

    encoded = _replace_change_as_write(
        BrokerReplaceChange(
            kind="replace",
            path="app.py",
            expected_sha256=expected_sha256,
            old_text="old value",
            new_text="new value",
        ),
        workspace=tmp_path,
    )

    assert encoded == {
        "kind": "write",
        "path": "app.py",
        "expected_sha256": expected_sha256,
        "expected_absent": False,
        "content": "before\nnew value\nafter\n",
        "content_sha256": hashlib.sha256(
            b"before\nnew value\nafter\n"
        ).hexdigest(),
    }
    assert target.read_bytes() == source.encode()


def test_replace_change_requires_one_exact_preimage_match(tmp_path: Path) -> None:
    content = "same\nsame\n"
    (tmp_path / "app.py").write_text(content, encoding="utf-8", newline="")
    change = BrokerReplaceChange(
        kind="replace",
        path="app.py",
        expected_sha256=hashlib.sha256(content.encode()).hexdigest(),
        old_text="same",
        new_text="different",
    )
    with pytest.raises(ValueError, match="exactly once"):
        _replace_change_as_write(change, workspace=tmp_path)


def test_shell_adapter_canonicalizes_only_current_workspace_references(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "task" / "repo"
    workspace.mkdir(parents=True)
    root = workspace.resolve().as_posix()
    script = (
        f"cd '{root}' && python {root}/tests/test_app.py\n"
        f"printf '%s\\n' '{root}-other'\n"
        "cat /outside/project/file.txt\n"
    )

    normalized = _workspace_relative_shell_script(script, workspace=workspace)

    assert normalized == (
        "cd '.' && python ./tests/test_app.py\n"
        f"printf '%s\\n' '{root}-other'\n"
        "cat /outside/project/file.txt\n"
    )
    assert _workspace_relative_cwd(root, workspace=workspace) == "."
    assert (
        _workspace_relative_cwd(f"{root}/src", workspace=workspace) == "src"
    )
    assert _workspace_relative_cwd("/outside/project", workspace=workspace) == (
        "/outside/project"
    )


@pytest.mark.asyncio
async def test_mcp_forwards_only_frozen_dependency_and_registered_document_inputs() -> None:
    calls: list[dict[str, object]] = []

    class RecordingClient:
        async def call(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"ok": True}

    server = build_server(RecordingClient())
    await server.call_tool(
        "install_dependencies",
        {
            "operation_id": "uv-sync",
            "manager": "uv",
            "action": "sync",
        },
    )
    await server.call_tool(
        "query_documentation",
        {
            "operation_id": "python-docs",
            "resource_id": "python",
            "document_path": "library/asyncio.html",
        },
    )
    assert calls == [
        {
            "operation_id": "uv-sync",
            "tool_name": "install_dependencies",
            "arguments": {"manager": "uv", "action": "sync"},
            "lease_id": None,
            "network_lease_id": None,
        },
        {
            "operation_id": "python-docs",
            "tool_name": "query_documentation",
            "arguments": {
                "resource_id": "python",
                "document_path": "library/asyncio.html",
            },
            "lease_id": None,
            "network_lease_id": None,
        },
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
async def test_v17_rpc_reuses_the_approved_exact_operation_without_exposing_lease(
    tmp_path: Path,
) -> None:
    server, task_id, endpoint, executor = await _rpc(
        tmp_path, runtime_protocol=RuntimeProtocol.V17
    )
    client = BrokerRPCClient(
        endpoint, token=server.register_task(task_id), task_id=task_id
    )
    request = {
        "operation_id": "rpc-v17-command",
        "tool_name": "run_command",
        "arguments": {"argv": ["python", "-m", "pytest"], "timeout_seconds": 30},
    }

    parked = await client.call(**request)
    assert parked["data"] == {"control": "turn_parking", "barrier": "approval"}
    task = server.broker.store.get_task(task_id)
    checkpoint = server.broker.store.create_checkpoint(
        task_id=task_id,
        workspace_tree_hash=server.broker.workspace_broker.current_tree_hash(
            task.workspace_id or ""
        ),
        payload={"phase": "waiting_approval"},
    )
    server.broker.store.park_turn_transaction(
        task_id=task_id,
        turn_id="turn_rpc_v17",
        checkpoint_id=checkpoint.checkpoint_id,
    )
    server.broker.store.transition(
        task_id,
        TaskState.WAITING_APPROVAL,
        expected_state=TaskState.RUNNING,
    )
    approval = server.broker.store.list_approvals(task_id)[0]
    server.broker.store.decide_approval(approval.approval_id, approved=True)

    completed = await client.call(**request)

    assert completed["state"] == "completed"
    assert executor.calls == [("python", "-m", "pytest")]
    operation = server.broker.store.get_operation("rpc-v17-command")
    assert operation.state is OperationState.COMPLETED
    assert operation.turn_id == "turn_rpc_v17"
    await server.close()


@pytest.mark.asyncio
async def test_rpc_reads_only_task_bound_streamed_operation_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    server, task_id, endpoint, _executor = await _rpc(tmp_path)
    client = BrokerRPCClient(
        endpoint, token=server.register_task(task_id), task_id=task_id
    )
    request = {"workspace_id": "workspace", "arguments": {}}
    operation = server.broker.store.create_operation(
        task_id=task_id,
        operation_id="shell-output",
        tool_name="run_shell",
        intent_sha256=hashlib.sha256(
            b'{"arguments":{},"workspace_id":"workspace"}'
        ).hexdigest(),
        request=request,
    )
    server.broker.store.append_event(
        task_id,
        "operation_output",
        {
            "operation_id": operation.operation_id,
            "stream": "stdout",
            "text": "failure details\n",
            "truncated": False,
        },
    )
    result = await client.call(
        operation_id="inspect-output",
        tool_name="read_operation_output",
        arguments={"operation_id": operation.operation_id, "after": 0},
    )
    assert result["data"]["chunks"][0]["text"] == "failure details\n"
    assert result["data"]["next_after"] > 0
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
