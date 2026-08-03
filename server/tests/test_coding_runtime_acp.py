from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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
from server.coding_runtime.draft_workspace import DraftWorkspace
from server.coding_runtime.worker import CodingWorkerServer, _WorkerSession

FAKE_AGENT = Path(__file__).with_name("fake_acp_agent.py")


def make_client(
    mode: str = "normal",
    *,
    timeout: float = 2.0,
    prompt_timeout: float | None = None,
    prompt_idle_timeout: float | None = None,
    permission_mode: str = "readonly",
) -> AcpClient:
    environment = {
        "FAKE_ACP_MODE": mode,
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
    }
    return AcpClient(
        AcpProcessConfig(
            command=(sys.executable, str(FAKE_AGENT)),
            workspace="/workspace",
            mode=permission_mode,
            process_cwd=str(Path.cwd()),
            environment=environment,
            request_timeout=timeout,
            prompt_timeout=prompt_timeout or timeout,
            prompt_idle_timeout=prompt_idle_timeout or timeout,
            shutdown_timeout=0.5,
        )
    )


def test_acp_workspace_cannot_be_redirected() -> None:
    with pytest.raises(ValueError, match="fixed to /workspace"):
        AcpProcessConfig(command=("opencode", "acp"), workspace="/tmp/other")
    with pytest.raises(ValueError, match="readonly or draft"):
        AcpProcessConfig(command=("opencode", "acp"), mode="write")


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
    assert client._config.environment["PYTHONPATH"] == "/opt/modelmirror"
    assert set(client._config.environment) == {
        "PATH",
        "PYTHONPATH",
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


def _edit_params(
    *,
    session_id: str = "acp-session",
    filepath: object = "/workspace/server/main.py",
    diff: object = (
        "Index: /workspace/server/main.py\n"
        "===================================================================\n"
        "--- /workspace/server/main.py\n"
        "+++ /workspace/server/main.py\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n"
    ),
    kind: str = "edit",
    extra_input: dict[str, object] | None = None,
    options: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    raw_input = {"filepath": filepath, "diff": diff}
    raw_input.update(extra_input or {})
    return {
        "sessionId": session_id,
        "toolCall": {
            "toolCallId": "edit-1",
            "kind": kind,
            "rawInput": raw_input,
        },
        "options": options
        or [
            {"optionId": "once", "kind": "allow_once", "name": "Allow once"},
            {"optionId": "always", "kind": "allow_always", "name": "Always"},
            {"optionId": "reject", "kind": "reject_once", "name": "Reject"},
        ],
    }


def _permission_client(mode: str = "draft") -> AcpClient:
    client = AcpClient(
        AcpProcessConfig(
            command=("opencode", "acp"),
            workspace="/workspace",
            mode=mode,
        )
    )
    session = CodingSession()
    session.transition(CodingSessionState.READY)
    session.begin_turn()
    client._session = session
    client._acp_session_id = "acp-session"
    return client


@pytest.mark.parametrize(
    "filepath",
    [
        "/workspace/server/main.py",
        "server/main.py",
    ],
)
def test_draft_permission_allows_only_safe_single_edit_once(filepath: str) -> None:
    client = _permission_client()

    selected = client._select_permission_option(_edit_params(filepath=filepath))

    assert selected == "once"


def test_new_text_file_diff_can_be_approved_once() -> None:
    client = _permission_client()
    params = _edit_params(
        filepath="/workspace/notes/new.txt",
        diff=(
            "Index: /workspace/notes/new.txt\n"
            "===================================================================\n"
            "--- /dev/null\n"
            "+++ /workspace/notes/new.txt\n"
            "@@ -0,0 +1 @@\n"
            "+clear summary\n"
        ),
    )

    assert client._select_permission_option(params) == "once"


@pytest.mark.parametrize(
    "params",
    [
        _edit_params(session_id="another-session"),
        _edit_params(filepath="/etc/passwd"),
        _edit_params(filepath="../outside.txt"),
        _edit_params(filepath="C:\\private\\file.txt"),
        _edit_params(filepath="/workspace/.env"),
        _edit_params(kind="execute"),
        _edit_params(extra_input={"files": []}),
        _edit_params(diff=None),
        _edit_params(diff="\ud800"),
        _edit_params(diff="not a unified diff"),
        _edit_params(
            diff=(
                "--- /workspace/server/main.py\n"
                "+++ /dev/null\n"
                "@@ -1 +0,0 @@\n"
                "-content\n"
            )
        ),
        _edit_params(
            diff=(
                "--- /workspace/server/main.py\n"
                "+++ /workspace/client/main.ts\n"
                "@@ -1 +1 @@\n"
                "-before\n"
                "+after\n"
            )
        ),
        _edit_params(
            diff=(
                "diff --git a/server/main.py b/server/main.py\n"
                "--- a/server/main.py\n"
                "+++ b/server/main.py\n"
                "@@ -1 +1 @@\n"
                "-before\n"
                "+after\n"
                "diff --git a/other.py b/other.py\n"
            )
        ),
    ],
)
def test_draft_permission_rejects_unsafe_or_malformed_edit(
    params: dict[str, object],
) -> None:
    client = _permission_client()

    assert client._select_permission_option(params) == "reject"


def test_readonly_mode_rejects_even_safe_edit() -> None:
    client = _permission_client("readonly")

    assert client._select_permission_option(_edit_params()) == "reject"


def test_permission_never_selects_allow_always() -> None:
    client = _permission_client()
    params = _edit_params(
        options=[
            {"optionId": "always", "kind": "allow_always", "name": "Always"},
            {"optionId": "reject", "kind": "reject_once", "name": "Reject"},
        ]
    )

    assert client._select_permission_option(params) == "reject"


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


@pytest.mark.asyncio
async def test_prompt_progress_is_not_limited_by_control_request_timeout() -> None:
    client = make_client(
        "prompt-progress",
        timeout=2.0,
        prompt_timeout=1.0,
        prompt_idle_timeout=0.2,
    )
    session = CodingSession()
    await client.open(session)

    events = [event async for event in client.prompt(session, "Keep working")]

    assert "".join(
        event.data["text"]
        for event in events
        if event.kind is CodingEventKind.ANSWER_DELTA
    ) == "onetwothree"
    assert events[-1].kind is CodingEventKind.TURN_COMPLETED
    await client.close(session)


@pytest.mark.asyncio
async def test_prompt_idle_timeout_fails_closed_and_cleans_up() -> None:
    client = make_client(
        "prompt-idle",
        timeout=2.0,
        prompt_timeout=0.5,
        prompt_idle_timeout=0.05,
    )
    session = CodingSession()
    await client.open(session)

    with pytest.raises(AcpRequestTimeout, match="made no progress"):
        _ = [event async for event in client.prompt(session, "Stop responding")]

    assert session.state is CodingSessionState.FAILED
    assert client.is_running is False


@pytest.mark.asyncio
async def test_worker_recovers_session_after_prompt_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutAdapter:
        async def prompt(self, session, prompt):
            turn_id = session.begin_turn()
            yield session.append_event(
                CodingEventKind.TURN_STARTED,
                turn_id=turn_id,
            )
            session.active_turn_id = None
            session.transition(CodingSessionState.FAILED)
            raise AcpRequestTimeout("synthetic prompt timeout")

        async def close(self, session):
            session.transition(CodingSessionState.CLOSED)

    class ReplacementAdapter:
        async def open(self, session):
            session.transition(CodingSessionState.READY)
            return session.append_event(CodingEventKind.SESSION_STARTED)

        async def close(self, session):
            session.transition(CodingSessionState.CLOSED)

    class MemoryWriter:
        def __init__(self) -> None:
            self.frames: list[dict[str, Any]] = []

        def write(self, encoded: bytes) -> None:
            self.frames.append(json.loads(encoded))

        async def drain(self) -> None:
            return None

    source = tmp_path / "source"
    source.mkdir()
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    workspace = DraftWorkspace(
        source,
        tmp_path / "workspace",
        tmp_path / "checkpoint",
    )
    workspace.initialize()
    session = CodingSession()
    session.transition(CodingSessionState.READY)
    record = _WorkerSession(
        session=session,
        adapter=TimeoutAdapter(),
        workspace=workspace,
        mode="draft",
    )
    server = CodingWorkerServer(tmp_path / "worker.sock")
    server._sessions[session.session_id] = record
    monkeypatch.setattr(
        "server.coding_runtime.worker.create_acp_client",
        lambda mode: ReplacementAdapter(),
    )
    writer = MemoryWriter()

    await server._prompt(
        {"session_id": session.session_id, "prompt": "Keep the old draft"},
        writer,
    )

    events = [
        frame["event"]
        for frame in writer.frames
        if isinstance(frame.get("event"), dict)
    ]
    assert [event["type"] for event in events] == ["turn_started", "failed"]
    assert events[-1]["data"]["code"] == "agent_turn_timeout"
    assert writer.frames[-1] == {"ok": True, "done": True}
    assert record.session.state is CodingSessionState.READY
    assert record.workspace.revision == 0
    assert (record.workspace.workspace_root / "baseline.txt").exists()
