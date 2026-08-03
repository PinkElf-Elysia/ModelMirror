from __future__ import annotations

import asyncio
import json

import pytest

from server.coding_runtime.command_bridge import (
    CommandBridgeError,
    CommandConfirmationBridge,
    CommandExecutionResult,
)
from server.coding_runtime.commands import normalize_agent_command
from server.coding_runtime.runner_mcp import RunnerMcpServer
from server.coding_runtime.worker import CodingWorkerError, build_opencode_config


def _command(marker: str = "q7m4", timeout_seconds: int = 120):
    return normalize_agent_command(
        argv=["python", "-m", "pytest", f"tests/test_{marker}.py", "-q"],
        cwd=".",
        purpose=f"检查随机用例 {marker}",
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_confirmation_allows_one_request_and_returns_execution_result() -> None:
    requested = asyncio.Event()
    observed: list[str] = []

    async def observer(request) -> None:
        observed.append(request.state.value)
        requested.set()

    async def executor(command, remaining: float) -> CommandExecutionResult:
        assert command == _command()
        assert remaining == 600
        return CommandExecutionResult("passed", 0, "1 passed", 0.25)

    bridge = CommandConfirmationBridge(observer=observer)
    await bridge.begin_turn("turn-r4t8")
    task = asyncio.create_task(
        bridge.request(
            session_id="session-n5c2",
            turn_id="turn-r4t8",
            command=_command(),
            executor=executor,
        )
    )
    await requested.wait()
    pending = await bridge.pending(session_id="session-n5c2")
    assert pending is not None

    decided = await bridge.decide(
        session_id="session-n5c2",
        request_id=pending["request_id"],
        decision="allow_once",
    )
    result = await task

    assert decided["state"] == "awaiting_confirmation"
    assert result["state"] == "completed"
    assert result["result"]["output"] == "1 passed"
    assert observed == ["awaiting_confirmation", "running", "completed"]
    assert await bridge.pending(session_id="session-n5c2") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_state", "expected_status"),
    [
        ("reject", "rejected", "rejected"),
        (None, "timed_out", "confirmation_timed_out"),
    ],
)
async def test_reject_and_timeout_are_non_error_tool_results(
    decision: str | None,
    expected_state: str,
    expected_status: str,
) -> None:
    bridge = CommandConfirmationBridge(confirmation_timeout=0.02)
    await bridge.begin_turn("turn-b3p9")

    async def never_execute(_command, _remaining):
        raise AssertionError("rejected commands must not execute")

    task = asyncio.create_task(
        bridge.request(
            session_id="session-z8f1",
            turn_id="turn-b3p9",
            command=_command("b3p9"),
            executor=never_execute,
        )
    )
    while (pending := await bridge.pending(session_id="session-z8f1")) is None:
        await asyncio.sleep(0)
    if decision is not None:
        await bridge.decide(
            session_id="session-z8f1",
            request_id=pending["request_id"],
            decision=decision,
        )

    result = await task

    assert result["state"] == expected_state
    assert result["result"]["status"] == expected_status


@pytest.mark.asyncio
async def test_bridge_rejects_concurrent_commands_and_cancel_is_idempotent() -> None:
    bridge = CommandConfirmationBridge()
    await bridge.begin_turn("turn-v6k2")

    async def executor(_command, _remaining):
        return CommandExecutionResult("passed", 0, "", 0.0)

    first = asyncio.create_task(
        bridge.request(
            session_id="session-a4d7",
            turn_id="turn-v6k2",
            command=_command("v6k2"),
            executor=executor,
        )
    )
    while await bridge.pending(session_id="session-a4d7") is None:
        await asyncio.sleep(0)
    with pytest.raises(CommandBridgeError) as busy:
        await bridge.request(
            session_id="session-a4d7",
            turn_id="turn-v6k2",
            command=_command("w8n1"),
            executor=executor,
        )
    assert busy.value.code == "command_request_busy"

    assert await bridge.cancel_pending() is True
    assert await bridge.cancel_pending() is False
    result = await first
    assert result["state"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_stops_a_running_executor_without_overwriting_cancelled_state() -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()
    bridge = CommandConfirmationBridge()
    await bridge.begin_turn("turn-c8r3")

    async def executor(_command, _remaining):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    task = asyncio.create_task(
        bridge.request(
            session_id="session-c8r3",
            turn_id="turn-c8r3",
            command=_command("c8r3"),
            executor=executor,
        )
    )
    while (pending := await bridge.pending(session_id="session-c8r3")) is None:
        await asyncio.sleep(0)
    await bridge.decide(
        session_id="session-c8r3",
        request_id=pending["request_id"],
        decision="allow_once",
    )
    await started.wait()

    assert await bridge.cancel_pending() is True
    result = await task

    assert cleaned.is_set()
    assert result["state"] == "cancelled"
    assert result["result"]["status"] == "turn_cancelled"


@pytest.mark.asyncio
async def test_decision_is_idempotent_and_bound_to_session() -> None:
    bridge = CommandConfirmationBridge()
    await bridge.begin_turn("turn-j2s5")

    async def executor(_command, _remaining):
        return CommandExecutionResult("passed", 0, "", 0.0)

    task = asyncio.create_task(
        bridge.request(
            session_id="session-j2s5",
            turn_id="turn-j2s5",
            command=_command("j2s5"),
            executor=executor,
        )
    )
    while (pending := await bridge.pending(session_id="session-j2s5")) is None:
        await asyncio.sleep(0)
    with pytest.raises(CommandBridgeError) as wrong_session:
        await bridge.decide(
            session_id="session-other",
            request_id=pending["request_id"],
            decision="allow_once",
        )
    assert wrong_session.value.code == "command_request_not_found"

    await bridge.decide(
        session_id="session-j2s5",
        request_id=pending["request_id"],
        decision="reject",
    )
    result = await task
    repeated = await bridge.decide(
        session_id="session-j2s5",
        request_id=pending["request_id"],
        decision="reject",
    )
    assert repeated["state"] == result["state"] == "rejected"


@pytest.mark.asyncio
async def test_per_turn_command_limit_fails_closed_after_completed_request() -> None:
    bridge = CommandConfirmationBridge(max_commands=1)
    await bridge.begin_turn("turn-limit-r9n4")

    async def executor(_command, _remaining):
        return CommandExecutionResult("passed", 0, "", 0.0)

    first = asyncio.create_task(
        bridge.request(
            session_id="session-limit-r9n4",
            turn_id="turn-limit-r9n4",
            command=_command("limit-r9n4"),
            executor=executor,
        )
    )
    while (pending := await bridge.pending(session_id="session-limit-r9n4")) is None:
        await asyncio.sleep(0)
    await bridge.decide(
        session_id="session-limit-r9n4",
        request_id=pending["request_id"],
        decision="allow_once",
    )
    assert (await first)["state"] == "completed"

    with pytest.raises(CommandBridgeError) as limited:
        await bridge.request(
            session_id="session-limit-r9n4",
            turn_id="turn-limit-r9n4",
            command=_command("limit-next"),
            executor=executor,
        )
    assert limited.value.code == "command_limit_reached"


@pytest.mark.asyncio
async def test_runner_mcp_exposes_only_structured_command_tool(monkeypatch) -> None:
    captured: dict[str, object] = {}
    server = RunnerMcpServer(socket_path="/tmp/runner.sock", token="t" * 32)

    async def forward(arguments):
        captured.update(arguments)
        return {"state": "rejected", "result": {"status": "rejected"}}

    monkeypatch.setattr(server, "_forward", forward)
    listed = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    called = await server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_project_command",
                "arguments": {
                    "argv": ["python", "-m", "pytest", "-q"],
                    "cwd": ".",
                    "purpose": "运行检查",
                    "timeout_seconds": 120,
                },
            },
        }
    )

    tools = listed["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["run_project_command"]
    assert tools[0]["inputSchema"]["additionalProperties"] is False
    assert captured["argv"] == ["python", "-m", "pytest", "-q"]
    assert called["result"]["isError"] is False
    assert json.loads(called["result"]["content"][0]["text"])["state"] == "rejected"


def test_opencode_config_enables_only_internal_mcp_in_draft_mode() -> None:
    config = build_opencode_config(
        "provider/model",
        "draft",
        commands_enabled=True,
        runner_token="r" * 32,
    )

    assert config["mcp"] == {
        "modelmirror-runner": {
            "type": "local",
            "command": ["python", "-m", "coding_runtime.runner_mcp"],
            "environment": {
                "MODELMIRROR_RUNNER_SOCKET": "/tmp/modelmirror-runner.sock",
                "MODELMIRROR_RUNNER_TOKEN": "r" * 32,
            },
            "enabled": True,
            "timeout": 310_000,
        }
    }
    assert config["permission"]["modelmirror-runner_*"] == "allow"
    assert config["permission"]["bash"] == "deny"

    with pytest.raises(CodingWorkerError):
        build_opencode_config(
            "provider/model",
            "readonly",
            commands_enabled=True,
            runner_token="r" * 32,
        )
    with pytest.raises(CodingWorkerError):
        build_opencode_config(
            "provider/model",
            "draft",
            commands_enabled=True,
            runner_token="unsafe token with spaces" * 2,
        )
