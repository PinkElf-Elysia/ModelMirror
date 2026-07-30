from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from server.coding_runtime.api import CodingTurnRequest, _public_event
from server.coding_runtime.draft_workspace import DraftWorkspace
from server.coding_runtime.models import (
    CodingEvent,
    CodingEventKind,
    CodingSession,
    CodingSessionState,
)
from server.coding_runtime.worker import (
    INTERNAL_GATEWAY_BASE_URL,
    MAX_AGENT_STEPS,
    MODEL_CONTEXT_TOKENS,
    MODEL_OUTPUT_TOKENS,
    CodingWorkerError,
    CodingWorkerServer,
    WORKSPACE_PATH,
    _WorkerSession,
    build_opencode_config,
    create_acp_client,
    validate_runtime_dependencies,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _compose_service(name: str) -> str:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        compose,
    )
    assert match is not None
    return match.group(1)


def test_container_isolation_uses_an_immutable_sanitized_source_snapshot() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = _compose_service("coding-runtime")
    dockerfile = (
        REPOSITORY_ROOT / "server/coding_worker/Dockerfile"
    ).read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    notices = (
        REPOSITORY_ROOT / "server/THIRD_PARTY_NOTICES.md"
    ).read_text(encoding="utf-8")

    assert "profiles:\n      - coding" in service
    assert "context: ." in service
    assert "dockerfile: server/coding_worker/Dockerfile" in service
    assert 'user: "65532:65532"' in service
    assert "read_only: true" in service
    assert "cap_drop:\n      - ALL" in service
    assert "no-new-privileges:true" in service
    assert ":/workspace" not in service
    assert "- coding_internal" in service
    assert "ports:" not in service
    assert "privileged:" not in service
    assert "COPY . /opt/modelmirror-source" in dockerfile
    assert "chmod -R a-w /opt/modelmirror-source" in dockerfile
    assert "COPY --chown=coding:coding . /workspace" not in dockerfile
    assert "ARG RIPGREP_VERSION=14.1.1-1+b4" in dockerfile
    assert '"ripgrep=${RIPGREP_VERSION}"' in dockerfile
    assert '"$(rg --version | head -n 1)" = "ripgrep 14.1.1"' in dockerfile
    assert "ripgrep` 14.1.1" in notices
    assert (
        "/workspace:rw,nosuid,noexec,size=256m,uid=65532,gid=65532,mode=0700"
        in service
    )
    assert "CODING_AGENT_MODE: ${CODING_AGENT_MODE:-readonly}" in service
    assert all(
        pattern in dockerignore
        for pattern in (
            ".git",
            ".env",
            "**/*.key",
            "**/*.pem",
            "**/node_modules",
            "**/storage/**",
            "**/uploads/**",
        )
    )

    network = compose.split("  coding_internal:\n", maxsplit=1)[1]
    assert "internal: true" in network


def test_agent_configuration_fails_closed_for_write_shell_and_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("CODING_AGENT_GATEWAY_KEY", "test-only-key")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross")

    client = create_acp_client("readonly")
    config = json.loads(client._config.environment["OPENCODE_CONFIG_CONTENT"])
    permission = config["permission"]

    assert client._config.command == (
        "/usr/local/bin/opencode",
        "acp",
        "--cwd",
        WORKSPACE_PATH,
    )
    assert client._config.workspace == WORKSPACE_PATH
    assert client._config.process_cwd == WORKSPACE_PATH
    assert permission["*"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert all(
        permission[name] == "allow" for name in ("list", "glob", "grep", "lsp")
    )
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
            "todowrite",
        )
    )
    assert permission["read"]["**/.git/**"] == "deny"
    assert permission["read"]["**/.env"] == "deny"
    assert permission["read"]["**/*.key"] == "deny"
    assert config["plugin"] == []
    assert config["mcp"] == {}
    assert config["share"] == "disabled"
    assert config["autoupdate"] is False
    assert config["model"] == "modelmirror/deepseek/deepseek-v4-flash"
    assert config["agent"]["readonly"]["steps"] == MAX_AGENT_STEPS
    model_config = config["provider"]["modelmirror"]["models"][
        "deepseek/deepseek-v4-flash"
    ]
    assert model_config["limit"] == {
        "context": MODEL_CONTEXT_TOKENS,
        "output": MODEL_OUTPUT_TOKENS,
    }
    assert config["provider"]["modelmirror"]["options"]["baseURL"] == (
        INTERNAL_GATEWAY_BASE_URL
    )
    assert "UNRELATED_SECRET" not in client._config.environment


def test_draft_mode_only_changes_edit_to_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_MODEL", "test-model")
    monkeypatch.setenv("CODING_AGENT_GATEWAY_KEY", "test-only-key")

    client = create_acp_client("draft")
    config = json.loads(client._config.environment["OPENCODE_CONFIG_CONTENT"])
    permission = config["permission"]

    assert client._config.mode == "draft"
    assert config["default_agent"] == "draft"
    assert permission["edit"] == "ask"
    assert permission["*"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert config["agent"]["draft"]["steps"] == MAX_AGENT_STEPS
    assert all(
        permission[name] == "deny"
        for name in (
            "bash",
            "task",
            "webfetch",
            "websearch",
            "skill",
            "external_directory",
            "question",
            "todowrite",
        )
    )


def test_runtime_dependencies_fail_closed_when_search_backend_is_missing(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "opencode"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    validate_runtime_dependencies((executable,))

    with pytest.raises(CodingWorkerError) as exc_info:
        validate_runtime_dependencies((executable, tmp_path / "rg"))

    assert exc_info.value.code == "not_configured"


def test_workspace_reset_preserves_tmpfs_mount_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    workspace_root = tmp_path / "workspace-mount"
    workspace_root.mkdir()
    (workspace_root / "stale.txt").write_text("stale\n", encoding="utf-8")
    workspace = DraftWorkspace(
        source,
        workspace_root,
        tmp_path / "checkpoint",
        preserve_workspace_root=True,
    )

    workspace.initialize()
    assert workspace_root.is_dir()
    assert not (workspace_root / "stale.txt").exists()
    assert (workspace_root / "baseline.txt").read_text(encoding="utf-8") == (
        "baseline\n"
    )

    workspace.destroy()
    assert workspace_root.is_dir()
    assert list(workspace_root.iterdir()) == []


class _MemoryWriter:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def write(self, encoded: bytes) -> None:
        self.frames.append(json.loads(encoded))

    async def drain(self) -> None:
        return None


class _DraftTurnAdapter:
    def __init__(
        self,
        workspace: DraftWorkspace,
        *,
        outcome: str,
    ) -> None:
        self.workspace = workspace
        self.outcome = outcome

    async def open(self, session):
        session.transition(CodingSessionState.READY)
        return session.append_event(CodingEventKind.SESSION_STARTED)

    async def prompt(self, session, prompt):
        turn_id = session.begin_turn()
        yield session.append_event(CodingEventKind.TURN_STARTED, turn_id=turn_id)
        if self.outcome == "delete":
            (self.workspace.workspace_root / "baseline.txt").unlink()
        else:
            (self.workspace.workspace_root / f"{self.outcome}.txt").write_text(
                f"{prompt}\n",
                encoding="utf-8",
            )
        if self.outcome == "exception":
            session.active_turn_id = None
            session.transition(CodingSessionState.FAILED)
            raise RuntimeError("synthetic agent failure")
        terminal_kind = {
            "complete": CodingEventKind.TURN_COMPLETED,
            "cancel": CodingEventKind.CANCELLED,
            "delete": CodingEventKind.TURN_COMPLETED,
        }[self.outcome]
        yield session.append_event(terminal_kind, turn_id=turn_id)
        session.finish_turn()

    async def cancel(self, session) -> bool:
        return session.request_cancel()

    async def close(self, session) -> None:
        if session.state is not CodingSessionState.CLOSED:
            session.active_turn_id = None
            session.transition(CodingSessionState.CLOSED)


def _draft_record(
    tmp_path: Path,
    *,
    outcome: str,
) -> tuple[CodingWorkerServer, _WorkerSession]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    (source / "baseline.txt").chmod(0o444)
    source.chmod(0o555)
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
        adapter=_DraftTurnAdapter(workspace, outcome=outcome),
        workspace=workspace,
        mode="draft",
    )
    server = CodingWorkerServer(
        tmp_path / "worker.sock",
        source_snapshot_path=source,
        workspace_path=workspace.workspace_root,
        checkpoint_path=workspace.checkpoint_root,
    )
    server._set_workspace_writable(workspace.workspace_root)
    server._sessions[session.session_id] = record
    return server, record


@pytest.mark.asyncio
async def test_worker_commits_success_and_rolls_back_cancel_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, record = _draft_record(tmp_path, outcome="complete")
    writer = _MemoryWriter()

    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "accepted"},
        writer,
    )
    assert (record.workspace.workspace_root / "complete.txt").exists()
    assert record.workspace.revision == 1

    replacement_adapters: list[_DraftTurnAdapter] = []

    def replacement_adapter(mode: str) -> _DraftTurnAdapter:
        assert mode == "draft"
        adapter = _DraftTurnAdapter(record.workspace, outcome="complete")
        replacement_adapters.append(adapter)
        return adapter

    monkeypatch.setattr(
        "server.coding_runtime.worker.create_acp_client",
        replacement_adapter,
    )
    old_session = record.session
    record.adapter = _DraftTurnAdapter(record.workspace, outcome="cancel")
    cancel_writer = _MemoryWriter()
    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "cancelled"},
        cancel_writer,
    )
    assert not (record.workspace.workspace_root / "cancel.txt").exists()
    assert (record.workspace.workspace_root / "complete.txt").exists()
    assert (
        (record.workspace.workspace_root / "baseline.txt").stat().st_mode
        & stat.S_IWUSR
    )
    assert record.workspace.revision == 1
    assert replacement_adapters == [record.adapter]
    assert record.session is not old_session
    assert record.session.session_id == old_session.session_id
    assert old_session.state is CodingSessionState.CLOSED
    assert record.session.state is CodingSessionState.READY
    assert (
        record.session._next_seq
        == cancel_writer.frames[-2]["event"]["seq"] + 1
    )

    record.adapter = _DraftTurnAdapter(record.workspace, outcome="exception")
    failure_writer = _MemoryWriter()
    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "failed"},
        failure_writer,
    )
    assert not (record.workspace.workspace_root / "exception.txt").exists()
    assert (record.workspace.workspace_root / "complete.txt").exists()
    assert record.workspace.revision == 1
    assert failure_writer.frames[-2]["event"]["type"] == "failed"
    assert failure_writer.frames[-2]["event"]["data"] == {
        "code": "agent_turn_failed"
    }
    assert failure_writer.frames[-1] == {"ok": True, "done": True}
    assert len(replacement_adapters) == 2
    assert record.session.state is CodingSessionState.READY
    assert record.session.session_id in server._sessions

    review_writer = _MemoryWriter()
    request = {"session_id": record.session.session_id}
    await server._changes(request, review_writer)
    assert review_writer.frames[-1]["changes"]["can_download"] is True
    await server._diff(
        {**request, "path": "complete.txt", "revision": 1},
        review_writer,
    )
    assert "complete.txt" in review_writer.frames[-1]["diff"]
    await server._patch({**request, "revision": 1}, review_writer)
    assert review_writer.frames[-1]["patch"].startswith(
        "diff --git a/complete.txt"
    )
    await server._validate(request, review_writer)
    assert review_writer.frames[-1]["changes"]["validation_status"] == "passed"
    await server._discard(request, review_writer)
    assert review_writer.frames[-1]["changes"]["files"] == []


@pytest.mark.asyncio
async def test_worker_hard_policy_failure_rolls_back_and_emits_safe_failure(
    tmp_path: Path,
) -> None:
    server, record = _draft_record(tmp_path, outcome="delete")
    writer = _MemoryWriter()

    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "delete"},
        writer,
    )

    terminal = [
        frame["event"]
        for frame in writer.frames
        if isinstance(frame.get("event"), dict)
        and frame["event"]["type"] == CodingEventKind.FAILED.value
    ]
    assert terminal[0]["data"] == {"code": "draft_policy_violation"}
    assert (record.workspace.workspace_root / "baseline.txt").exists()
    assert record.workspace.revision == 0


def test_api_rejects_control_injection_and_only_exposes_sanitized_events() -> None:
    with pytest.raises(ValidationError):
        CodingTurnRequest.model_validate(
            {
                "prompt": "Explain this",
                "cwd": "C:\\private\\repo",
                "command": "git status",
                "provider": "other",
            }
        )

    event = CodingEvent(
        session_id="session",
        seq=1,
        kind=CodingEventKind.TOOL_STATUS,
        created_at=1.0,
        turn_id="turn",
        data={
            "tool_call_id": "tool-1",
            "title": "Read C:\\private\\repo and /workspace/server/main.py",
            "kind": "read",
            "status": "completed",
            "raw": "must-not-cross",
            "api_key": "must-not-cross",
        },
    )

    public = _public_event(event)
    serialized = json.dumps(public, ensure_ascii=False)
    assert "C:\\private" not in serialized
    assert "/workspace" not in serialized
    assert "must-not-cross" not in serialized
    assert set(public["data"]) == {"tool_call_id", "title", "kind", "status"}
