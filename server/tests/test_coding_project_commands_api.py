from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from server.coding_runtime.commands import ProjectVerificationConfig
from server.coding_runtime.api import _public_event, _verification_from_worker, router
from server.coding_runtime.draft_workspace import DraftWorkspace
from server.coding_runtime.models import CodingEventKind, CodingSession, CodingSessionState
from server.coding_runtime.projects import ProjectKind
from server.coding_runtime.worker import (
    CodingWorkerServer,
    WorkspaceSource,
    _WorkerSession,
)


class _Adapter:
    async def close(self, session: CodingSession) -> None:
        return None


class _Verifier:
    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    async def health(self) -> dict[str, Any]:
        return {"configured": True, "commands": True}

    async def execute_command(self, **kwargs: Any) -> dict[str, Any]:
        self.commands.append(kwargs)
        return {
            "status": "passed",
            "exit_code": 0,
            "output": "ok /workspace " + "sk-" + "1" * 24,
            "duration_seconds": 0.01,
        }

    async def cancel_command(self, **kwargs: Any) -> bool:
        return True

    async def close(self, **kwargs: Any) -> None:
        return None


class _Writer:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def write(self, value: bytes) -> None:
        self.frames.append(json.loads(value))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CodingWorkerServer, _WorkerSession, _Verifier]:
    monkeypatch.setenv("CODING_PROJECT_COMMANDS_ENABLED", "true")
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = source_root / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    verifier = _Verifier()
    server = CodingWorkerServer(
        tmp_path / "worker.sock",
        source_snapshot_path=source_root,
        project_snapshot_path=tmp_path / "project-slot",
        workspace_path=tmp_path / "workspace",
        checkpoint_path=tmp_path / "checkpoint",
        verifier=verifier,  # type: ignore[arg-type]
    )
    session = CodingSession(state=CodingSessionState.READY)
    lease = {
        "kind": "local_clone",
        "lease_id": "lease-random-k7m4",
        "project_id": "local-1234567890abcdef12345678",
        "name": "Random project",
        "branch": "main",
        "head": "a" * 40,
        "fingerprint": "b" * 64,
        "file_count": 2,
        "total_bytes": 50,
        "hidden_files": 0,
        "created_at": 1_785_600_000.0,
    }
    source = WorkspaceSource(
        kind=ProjectKind.LOCAL_CLONE,
        project_id=lease["project_id"],
        name=lease["name"],
        snapshot_path=source_root,
        fingerprint=lease["fingerprint"],
        branch="main",
        head=lease["head"],
        lease_id=lease["lease_id"],
        lease_payload=lease,
        verification=ProjectVerificationConfig(),
    )
    workspace = DraftWorkspace(
        source_root,
        tmp_path / "workspace",
        tmp_path / "checkpoint",
        preserve_workspace_root=True,
    )
    workspace.initialize()
    token, bridge, events = server._build_command_bridge(session, "draft", source)
    assert bridge is not None
    record = _WorkerSession(
        session=session,
        adapter=_Adapter(),  # type: ignore[arg-type]
        workspace=workspace,
        mode="draft",
        source=source,
        command_bridge=bridge,
        command_events=events,
        runner_token=token,
    )
    server._sessions[session.session_id] = record
    return server, record, verifier


@pytest.mark.asyncio
async def test_agent_command_waits_for_allow_once_and_emits_sanitized_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, record, verifier = _record(tmp_path, monkeypatch)
    turn_id = record.session.begin_turn()
    assert record.command_bridge is not None
    await record.command_bridge.begin_turn(turn_id)
    reader = asyncio.StreamReader()
    reader.feed_data(
        json.dumps(
            {
                "token": record.runner_token,
                "arguments": {
                    "argv": ["python", "-m", "pytest", "-q"],
                    "cwd": ".",
                    "purpose": "Run random k7m4 check",
                    "timeout_seconds": 30,
                },
            }
        ).encode()
        + b"\n"
    )
    reader.feed_eof()
    writer = _Writer()
    task = asyncio.create_task(server._handle_runner(reader, writer))
    for _ in range(20):
        pending = await record.command_bridge.pending(
            session_id=record.session.session_id,
            turn_id=turn_id,
        )
        if pending is not None:
            break
        await asyncio.sleep(0)
    assert pending is not None
    assert verifier.commands == []
    await record.command_bridge.decide(
        session_id=record.session.session_id,
        request_id=pending["request_id"],
        decision="allow_once",
    )
    await task

    assert writer.frames[0]["state"] == "completed"
    assert len(verifier.commands) == 1
    requested = await record.command_events.get()
    resolved = await record.command_events.get()
    assert requested.kind is CodingEventKind.COMMAND_REQUESTED
    assert resolved.kind is CodingEventKind.COMMAND_RESOLVED
    assert "sk-" not in resolved.data["result"]["output"]
    assert "[workspace]" in resolved.data["result"]["output"]


@pytest.mark.asyncio
async def test_custom_project_verification_previews_then_runs_exact_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, record, verifier = _record(tmp_path, monkeypatch)
    (record.workspace.workspace_root / "tests" / "test_app.py").write_text(
        "def test_value():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    revision = record.workspace.cumulative_changes().revision
    preview = server._prepare_local_verification(record, revision)

    assert preview["state"] == "awaiting_confirmation"
    assert len(preview["steps"]) == 2
    writer = _Writer()
    await server._verification_confirm(
        {
            "action": "verification_confirm",
            "session_id": record.session.session_id,
            "revision": revision,
            "confirmation_id": preview["confirmation_id"],
        },
        writer,
    )
    assert record.verification_task is not None
    await record.verification_task

    assert record.verification is not None
    assert record.verification["result"] == "passed"
    assert len(verifier.commands) == 2
    assert verifier.commands[0]["paths"] == []
    assert verifier.commands[1]["paths"] == ["tests/test_app.py"]


def test_command_and_confirmation_payloads_cross_only_the_public_api_boundary() -> None:
    command = {
        "id": "command-1234567890abcdef12345678",
        "name": "Run q9t2 check",
        "kind": "test",
        "argv": ["python", "-m", "pytest", "-q"],
        "cwd": ".",
        "timeout_seconds": 30,
    }
    verification = _verification_from_worker(
        {
            "verification": {
                "revision": 7,
                "state": "awaiting_confirmation",
                "result": "not_run",
                "stale": False,
                "reason": None,
                "started_at": None,
                "finished_at": None,
                "confirmation_id": "verification-confirmation-q9t2random",
                "plan_fingerprint": "c" * 64,
                "steps": [
                    {
                        "id": "command-1234567890abcdef12345678-draft",
                        "label": "Run q9t2 check",
                        "command": command,
                        "state": "not_started",
                        "result": "not_run",
                        "duration_ms": None,
                        "summary": "",
                        "details": "",
                        "truncated": False,
                    }
                ],
                "_plan": [{"must": "not cross the API"}],
            }
        }
    )
    event = _public_event(
        record := CodingSession().append_event(
            CodingEventKind.COMMAND_REQUESTED,
            turn_id="turn-q9t2",
            data={
                "request_id": "command-request-q9t2",
                "command": command,
                "expires_at": 1_785_600_300.0,
                "raw": "must not cross the API",
            },
        )
    )

    assert verification["state"] == "awaiting_confirmation"
    assert "_plan" not in verification
    assert event["data"]["command"]["argv"] == command["argv"]
    assert "raw" not in event["data"]
    assert record.kind is CodingEventKind.COMMAND_REQUESTED
    paths = {route.path for route in router.routes}
    assert "/api/coding/sessions/{session_id}/verification/confirm" in paths
    assert "/api/coding/sessions/{session_id}/commands/pending" in paths
    assert "/api/coding/sessions/{session_id}/commands/{request_id}/decision" in paths
