from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from server.coding_runtime import (
    AcpClient,
    AcpProcessConfig,
    AcpProcessExited,
    AcpProtocolError,
    AcpRequestTimeout,
    CodingEventKind,
    CodingSession,
    CodingSessionState,
)
from server.coding_runtime import worker

FAKE_AGENT = Path(__file__).with_name("fake_acp_agent.py")


def make_client(mode: str = "normal", *, timeout: float = 2.0) -> AcpClient:
    environment = {
        "FAKE_ACP_MODE": mode,
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
    }
    return AcpClient(
        AcpProcessConfig(
            command=(sys.executable, str(FAKE_AGENT)),
            workspace="/workspace",
            process_cwd=str(Path.cwd()),
            environment=environment,
            request_timeout=timeout,
            shutdown_timeout=0.5,
        )
    )


def test_acp_workspace_cannot_be_redirected() -> None:
    with pytest.raises(ValueError, match="fixed to /workspace"):
        AcpProcessConfig(command=("opencode", "acp"), workspace="/tmp/other")


def test_worker_config_is_read_only_and_child_env_is_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_MODEL", "test-model")
    monkeypatch.setenv("CODING_AGENT_GATEWAY_KEY", "test-only-key")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-inherited")

    client = worker.create_acp_client()
    config = json.loads(client._config.environment["OPENCODE_CONFIG_CONTENT"])
    permission = config["permission"]

    assert permission["*"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert all(permission[name] == "allow" for name in ("list", "glob", "grep", "lsp"))
    assert all(
        permission[name] == "deny"
        for name in (
            "edit",
            "bash",
            "task",
            "webfetch",
            "websearch",
            "skill",
            "external_directory",
            "question",
        )
    )
    assert config["plugin"] == []
    assert config["mcp"] == {}
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False
    assert config["provider"]["modelmirror"]["options"] == {
        "baseURL": "http://new-api:3000/v1",
        "apiKey": "{env:CODING_AGENT_GATEWAY_KEY}",
    }
    assert "UNRELATED_SECRET" not in client._config.environment
    assert client._config.environment["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert client._config.environment["OPENCODE_PURE"] == "1"
    assert client._config.environment["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert client._config.environment["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert set(client._config.environment) == {
        "PATH",
        "HOME",
        "OPENCODE_TEST_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_PURE",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_AUTOCOMPACT",
        "OPENCODE_DISABLE_MODELS_FETCH",
        "OPENCODE_AUTH_CONTENT",
        "CODING_AGENT_GATEWAY_KEY",
        "NO_PROXY",
        "no_proxy",
    }


@pytest.mark.asyncio
async def test_acp_initializes_and_maps_streaming_updates() -> None:
    client = make_client()
    session = CodingSession(session_id="domain-session")

    opened = await client.open(session)
    events = [event async for event in client.prompt(session, "Explain the code")]
    await client.close(session)

    assert opened.kind is CodingEventKind.SESSION_STARTED
    assert [event.kind for event in events] == [
        CodingEventKind.TURN_STARTED,
        CodingEventKind.PLAN,
        CodingEventKind.TOOL_STATUS,
        CodingEventKind.TOOL_STATUS,
        CodingEventKind.ANSWER_DELTA,
        CodingEventKind.TURN_COMPLETED,
    ]
    assert events[3].data == {
        "tool_call_id": "tool-1",
        "title": "Read source",
        "kind": "read",
        "status": "completed",
    }
    assert session.state is CodingSessionState.CLOSED
    assert client.is_running is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("permission", "permission:reject-once"),
        ("permission-no-reject", "permission:cancelled"),
    ],
)
async def test_acp_permission_request_always_fails_closed(
    mode: str,
    expected: str,
) -> None:
    client = make_client(mode)
    session = CodingSession()
    await client.open(session)

    events = [event async for event in client.prompt(session, "Try a write")]
    await client.close(session)

    answer = "".join(
        event.data["text"]
        for event in events
        if event.kind is CodingEventKind.ANSWER_DELTA
    )
    assert answer == expected


@pytest.mark.asyncio
async def test_acp_cancel_is_idempotent_and_cleans_up() -> None:
    client = make_client("cancel")
    session = CodingSession()
    await client.open(session)
    stream = client.prompt(session, "Long request")

    assert (await anext(stream)).kind is CodingEventKind.TURN_STARTED
    assert (await anext(stream)).data["text"] == "working"
    assert await client.cancel(session) is True
    assert await client.cancel(session) is False

    remaining = [event async for event in stream]
    assert [event.kind for event in remaining] == [CodingEventKind.CANCELLED]
    assert session.state is CodingSessionState.READY

    await client.close(session)
    assert client.is_running is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "error_type"),
    [
        ("malformed", AcpProtocolError),
        ("exit", AcpProcessExited),
    ],
)
async def test_acp_protocol_failure_closes_process(
    mode: str,
    error_type: type[BaseException],
) -> None:
    client = make_client(mode)
    session = CodingSession()
    await client.open(session)

    with pytest.raises(error_type):
        _ = [event async for event in client.prompt(session, "Break")]

    assert session.state is CodingSessionState.FAILED
    assert client.is_running is False


@pytest.mark.asyncio
async def test_acp_timeout_fails_closed_and_cleans_up() -> None:
    client = make_client("timeout", timeout=0.05)
    session = CodingSession()

    with pytest.raises(AcpRequestTimeout):
        await client.open(session)

    assert session.state is CodingSessionState.FAILED
    assert client.is_running is False
