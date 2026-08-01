from __future__ import annotations

import json
import re
import stat
import sys
import time
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
from server.coding_runtime.verifier_client import VerifierClientError

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
    executable = Path(sys.executable)

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


class _RestoredSessionAdapter:
    def __init__(self) -> None:
        self.closed = False

    async def open(self, session):
        session.transition(CodingSessionState.READY)
        return session.append_event(CodingEventKind.SESSION_STARTED)

    async def close(self, session) -> None:
        self.closed = True
        if session.state is not CodingSessionState.CLOSED:
            session.active_turn_id = None
            session.transition(CodingSessionState.CLOSED)


@pytest.mark.asyncio
async def test_worker_restores_one_safe_draft_and_rejects_snapshot_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("before\n", encoding="utf-8")
    server = CodingWorkerServer(
        tmp_path / "worker.sock",
        source_snapshot_path=source,
        workspace_path=tmp_path / "workspace",
        checkpoint_path=tmp_path / "checkpoint",
    )
    patch = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-before
+after-731
"""
    adapter = _RestoredSessionAdapter()
    monkeypatch.setenv("CODING_AGENT_MODE", "draft")
    monkeypatch.setattr(
        "server.coding_runtime.worker.create_acp_client",
        lambda mode: adapter,
    )
    writer = _MemoryWriter()

    await server._restore_session(
        {
            "revision": 6,
            "patch": patch,
            "paths": ["app.txt"],
            "snapshot_fingerprint": server._source_fingerprint,
        },
        writer,
    )

    response = writer.frames[-1]
    assert response["recovered"] is True
    assert response["mode"] == "draft"
    assert response["changes"]["revision"] == 6
    assert response["changes"]["files"][0]["path"] == "app.txt"
    assert response["event"]["type"] == "session_started"
    assert (tmp_path / "workspace" / "app.txt").read_text(encoding="utf-8") == (
        "after-731\n"
    )
    await server.close()
    assert adapter.closed is True

    mismatch_server = CodingWorkerServer(
        tmp_path / "mismatch.sock",
        source_snapshot_path=source,
        workspace_path=tmp_path / "mismatch-workspace",
        checkpoint_path=tmp_path / "mismatch-checkpoint",
    )
    with pytest.raises(CodingWorkerError) as mismatch:
        await mismatch_server._restore_session(
            {
                "revision": 6,
                "patch": patch,
                "paths": ["app.txt"],
                "snapshot_fingerprint": "0" * 64,
            },
            _MemoryWriter(),
        )
    assert mismatch.value.code == "snapshot_mismatch"
    assert mismatch_server._sessions == {}


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


class _FakeVerifier:
    def __init__(self, *, start_state: str = "running") -> None:
        self.fingerprint = ""
        self.report: dict[str, Any] | None = None
        self.start_payload: dict[str, Any] | None = None
        self.start_state = start_state
        self.cancel_calls = 0
        self.closed: list[str] = []
        self.fail_status = False

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "configured": True,
            "snapshot_fingerprint": self.fingerprint,
            "max_duration_seconds": 600,
        }

    async def start(self, **payload: Any) -> dict[str, Any]:
        self.start_payload = payload
        self.report = self._report(
            revision=payload["revision"],
            state=self.start_state,
            result="not_run",
        )
        return {"ok": True, "verification": self.report}

    async def status(self, **payload: Any) -> dict[str, Any]:
        if self.fail_status:
            raise VerifierClientError(
                "unavailable",
                code="verifier_unavailable",
            )
        assert self.report is not None
        assert payload["revision"] == self.report["revision"]
        return {"ok": True, "verification": self.report}

    async def cancel(self, **payload: Any) -> dict[str, Any]:
        self.cancel_calls += 1
        self.report = self._report(
            revision=payload["revision"],
            state="cancelled",
            result="not_run",
        )
        return {
            "ok": True,
            "accepted": True,
            "verification": self.report,
        }

    async def close(self, *, session_id: str) -> None:
        self.closed.append(session_id)

    @staticmethod
    def _report(
        *,
        revision: int,
        state: str,
        result: str,
    ) -> dict[str, Any]:
        terminal = state in {"completed", "cancelled"}
        return {
            "revision": revision,
            "state": state,
            "result": result,
            "stale": False,
            "reason": "cancelled" if state == "cancelled" else None,
            "started_at": time.time(),
            "finished_at": time.time() if terminal else None,
            "steps": [
                {
                    "id": "backend_tests",
                    "label": "检查服务代码",
                    "state": state,
                    "result": result,
                    "duration_ms": None,
                    "summary": "",
                    "details": "",
                    "truncated": False,
                }
            ],
        }


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


@pytest.mark.asyncio
async def test_worker_verification_locks_mutation_and_degrades_safely(
    tmp_path: Path,
) -> None:
    server, record = _draft_record(tmp_path, outcome="complete")
    verifier = _FakeVerifier()
    verifier.fingerprint = server._source_fingerprint
    server._verifier = verifier

    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "draft"},
        _MemoryWriter(),
    )
    start_writer = _MemoryWriter()
    await server._verification_start(
        {"session_id": record.session.session_id, "revision": 1},
        start_writer,
    )

    assert start_writer.frames[-1]["verification"]["state"] == "running"
    assert verifier.start_payload is not None
    assert verifier.start_payload["paths"] == ["complete.txt"]
    assert verifier.start_payload["expected_fingerprint"] == (
        server._source_fingerprint
    )
    assert verifier.start_payload["patch"].startswith(
        "diff --git a/complete.txt"
    )
    with pytest.raises(CodingWorkerError) as prompt_error:
        await server._prompt(
            {"session_id": record.session.session_id, "prompt": "blocked"},
            _MemoryWriter(),
        )
    assert prompt_error.value.code == "verification_in_progress"
    with pytest.raises(CodingWorkerError) as discard_error:
        await server._discard(
            {"session_id": record.session.session_id},
            _MemoryWriter(),
        )
    assert discard_error.value.code == "verification_in_progress"

    verifier.fail_status = True
    status_writer = _MemoryWriter()
    await server._verification_status(
        {"session_id": record.session.session_id, "revision": 1},
        status_writer,
    )
    degraded = status_writer.frames[-1]["verification"]
    assert degraded["state"] == "completed"
    assert degraded["result"] == "not_run"
    assert degraded["reason"] == "verifier_unavailable"
    assert (record.workspace.workspace_root / "complete.txt").exists()


@pytest.mark.asyncio
async def test_worker_tracks_verifier_jobs_accepted_before_running(
    tmp_path: Path,
) -> None:
    server, record = _draft_record(tmp_path, outcome="complete")
    verifier = _FakeVerifier(start_state="not_started")
    verifier.fingerprint = server._source_fingerprint
    server._verifier = verifier

    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "draft"},
        _MemoryWriter(),
    )
    await server._verification_start(
        {"session_id": record.session.session_id, "revision": 1},
        _MemoryWriter(),
    )

    assert server._verification_running(record) is True
    with pytest.raises(CodingWorkerError) as prompt_error:
        await server._prompt(
            {"session_id": record.session.session_id, "prompt": "blocked"},
            _MemoryWriter(),
        )
    assert prompt_error.value.code == "verification_in_progress"

    assert verifier.report is not None
    verifier.report["state"] = "completed"
    verifier.report["result"] = "passed"
    verifier.report["finished_at"] = 2.0
    status_writer = _MemoryWriter()
    await server._verification_status(
        {"session_id": record.session.session_id, "revision": 1},
        status_writer,
    )

    assert status_writer.frames[-1]["verification"]["state"] == "completed"
    assert status_writer.frames[-1]["verification"]["result"] == "passed"


@pytest.mark.asyncio
async def test_worker_verification_cancel_and_cleanup_are_idempotent(
    tmp_path: Path,
) -> None:
    server, record = _draft_record(tmp_path, outcome="complete")
    verifier = _FakeVerifier()
    verifier.fingerprint = server._source_fingerprint
    server._verifier = verifier
    await server._prompt(
        {"session_id": record.session.session_id, "prompt": "draft"},
        _MemoryWriter(),
    )
    await server._verification_start(
        {"session_id": record.session.session_id, "revision": 1},
        _MemoryWriter(),
    )

    first = _MemoryWriter()
    second = _MemoryWriter()
    request = {"session_id": record.session.session_id, "revision": 1}
    await server._verification_cancel(request, first)
    await server._verification_cancel(request, second)
    await server._cleanup_record(record)

    assert first.frames[-1]["accepted"] is True
    assert second.frames[-1]["accepted"] is True
    assert second.frames[-1]["verification"]["state"] == "cancelled"
    assert verifier.cancel_calls == 1
    assert verifier.closed == [record.session.session_id]


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
