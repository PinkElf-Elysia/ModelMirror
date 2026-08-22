from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.coding_runtime.models import CodingEventKind, CodingSessionState
from server.coding_runtime.patch_policy import snapshot_fingerprint
from server.coding_runtime.worker import (
    CodingWorkerClient,
    CodingWorkerError,
    CodingWorkerProtocolError,
    CodingWorkerServer,
    ProjectKind,
    build_opencode_config,
)


class _MemoryWriter:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def write(self, encoded: bytes) -> None:
        self.frames.append(json.loads(encoded))

    async def drain(self) -> None:
        return None


class _Adapter:
    async def open(self, session):
        session.transition(CodingSessionState.READY)
        return session.append_event(CodingEventKind.SESSION_STARTED)

    async def close(self, session) -> None:
        if session.state is not CodingSessionState.CLOSED:
            session.active_turn_id = None
            session.transition(CodingSessionState.CLOSED)


def _local_source(slot: Path, *, marker: str = "ALPHA-q7M3") -> dict[str, object]:
    current = slot / "current"
    workspace = current / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text(f"marker: {marker}\n", encoding="utf-8")
    fingerprint = snapshot_fingerprint(workspace)
    source: dict[str, object] = {
        "kind": "local_clone",
        "lease_id": "lease_7qM3vK8xP2dR6tN4",
        "project_id": "local-1234567890abcdef12345678",
        "name": "随机 Alpha 项目",
        "branch": "main",
        "head": "a" * 40,
        "fingerprint": fingerprint,
        "file_count": 1,
        "total_bytes": len(f"marker: {marker}\n".encode("utf-8")),
        "hidden_files": 2,
        "created_at": 1_785_600_000.0,
    }
    (current / "lease.json").write_text(
        json.dumps({key: value for key, value in source.items() if key != "kind"}),
        encoding="utf-8",
    )
    return source


def _server(tmp_path: Path, slot: Path) -> CodingWorkerServer:
    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    (builtin / "builtin-only.txt").write_text("MODELMIRROR-z2T9\n", encoding="utf-8")
    return CodingWorkerServer(
        tmp_path / "worker.sock",
        source_snapshot_path=builtin,
        project_snapshot_path=slot / "current",
        workspace_path=tmp_path / "workspace",
        checkpoint_path=tmp_path / "checkpoint",
    )


def test_runtime_accepts_only_the_exact_active_snapshot_lease(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    source = _local_source(slot)
    server = _server(tmp_path, slot)

    resolved = server._resolve_workspace_source(source)

    assert resolved.kind is ProjectKind.LOCAL_CLONE
    assert resolved.project_id == source["project_id"]
    assert resolved.snapshot_path == slot / "current" / "workspace"
    assert "lease" not in resolved.to_public_dict()
    assert resolved.to_public_dict()["head"] == "a" * 40

    wrong_project = dict(source)
    wrong_project["project_id"] = "local-fedcba0987654321fedcba09"
    with pytest.raises(CodingWorkerError) as mismatch:
        server._resolve_workspace_source(wrong_project)
    assert mismatch.value.code == "snapshot_mismatch"


@pytest.mark.asyncio
async def test_runtime_rejects_browser_paths_and_commands_in_session_request(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    source = _local_source(slot)
    server = _server(tmp_path, slot)

    with pytest.raises(CodingWorkerProtocolError) as injected:
        await server._create_session(
            {
                "action": "create_session",
                "source": source,
                "path": "../other-project",
                "command": "git status",
            },
            _MemoryWriter(),
        )

    assert injected.value.code == "invalid_request"
    assert server._sessions == {}


@pytest.mark.parametrize("unsafe_path", [".env", "opencode.json", "nested/AGENTS.md"])
def test_runtime_independently_rejects_hidden_project_files(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    slot = tmp_path / "slot"
    source = _local_source(slot)
    workspace = slot / "current" / "workspace"
    target = workspace.joinpath(*unsafe_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("must not load\n", encoding="utf-8")
    source["file_count"] = 2
    source["total_bytes"] = sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file())
    source["fingerprint"] = snapshot_fingerprint(workspace)
    (slot / "current" / "lease.json").write_text(
        json.dumps({key: value for key, value in source.items() if key != "kind"}),
        encoding="utf-8",
    )
    server = _server(tmp_path, slot)

    with pytest.raises(CodingWorkerError) as rejected:
        server._resolve_workspace_source(source)

    assert rejected.value.code == "snapshot_unsafe"


@pytest.mark.asyncio
async def test_local_project_session_uses_only_selected_snapshot_and_blocks_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = tmp_path / "slot"
    source = _local_source(slot, marker="SELECTED-b8R4")
    server = _server(tmp_path, slot)
    adapter = _Adapter()
    monkeypatch.setenv("CODING_AGENT_MODE", "draft")
    monkeypatch.setattr("server.coding_runtime.worker.create_acp_client", lambda mode: adapter)
    writer = _MemoryWriter()

    await server._create_session({"action": "create_session", "source": source}, writer)

    response = writer.frames[-1]
    assert response["project"] == {
        "id": source["project_id"],
        "name": "随机 Alpha 项目",
        "kind": "local_clone",
        "branch": "main",
        "head": "a" * 40,
    }
    assert response["event"]["data"]["project"] == response["project"]
    assert (tmp_path / "workspace" / "README.md").read_text(encoding="utf-8") == "marker: SELECTED-b8R4\n"
    assert not (tmp_path / "workspace" / "builtin-only.txt").exists()
    session_id = response["session_id"]
    with pytest.raises(CodingWorkerError) as unavailable:
        await server._verification_start(
            {"session_id": session_id, "revision": 1},
            _MemoryWriter(),
        )
    assert unavailable.value.code == "project_operation_unavailable"
    await server.close()


@pytest.mark.asyncio
async def test_local_project_restore_rebuilds_the_same_diff_and_project_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = tmp_path / "slot"
    source = _local_source(slot, marker="RESTORE-n5C8")
    server = _server(tmp_path, slot)
    monkeypatch.setenv("CODING_AGENT_MODE", "draft")
    monkeypatch.setattr("server.coding_runtime.worker.create_acp_client", lambda mode: _Adapter())
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-marker: RESTORE-n5C8
+marker: RECOVERED-f2W6
"""
    writer = _MemoryWriter()

    await server._restore_session(
        {
            "revision": 4,
            "patch": patch,
            "paths": ["README.md"],
            "snapshot_fingerprint": source["fingerprint"],
            "source": source,
        },
        writer,
    )

    response = writer.frames[-1]
    assert response["recovered"] is True
    assert response["project"]["id"] == source["project_id"]
    assert response["event"]["data"]["project"] == response["project"]
    assert (tmp_path / "workspace" / "README.md").read_text(encoding="utf-8") == "marker: RECOVERED-f2W6\n"
    recovery_writer = _MemoryWriter()
    await server._recovery_snapshot({"session_id": response["session_id"]}, recovery_writer)
    assert recovery_writer.frames[-1]["project"] == response["project"]
    assert recovery_writer.frames[-1]["snapshot_fingerprint"] == source["fingerprint"]
    await server.close()


@pytest.mark.asyncio
async def test_host_writeback_restore_does_not_start_a_second_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = tmp_path / "slot"
    source = _local_source(slot, marker="WRITEBACK-r7K2")
    source["kind"] = "host_git"
    source["project_id"] = "hostgit_" + "7" * 32
    workspace = slot / "current" / "workspace"
    (workspace / "README.md").unlink()
    (workspace / ".gitignore").write_bytes(b"__pycache__/\n")
    (workspace / "README.md").write_bytes(b"# Formatter sample\n")
    (workspace / "app.py").write_bytes(
        b"from legacy_formatter import slugify\n\n\n"
        b"def render_cache_key(project: str, revision: int) -> str:\n"
        b"    return f\"{slugify(project)}:r{revision}\"\n"
    )
    (workspace / "legacy_formatter.py").write_bytes(
        b"import re\n\n\n"
        b"def slugify(value: str) -> str:\n"
        b"    \"\"\"Return a stable lowercase slug for a display label.\"\"\"\n"
        b"    collapsed = re.sub(r\"[^a-z0-9]+\", \"-\", value.strip().lower())\n"
        b"    return collapsed.strip(\"-\")\n"
    )
    (workspace / "test_formatter.py").write_bytes(
        b"from app import render_cache_key\n\n\n"
        b"def test_render_cache_key() -> None:\n"
        b"    assert render_cache_key(\"Alpha Project\", 3) == \"alpha-project:r3\"\n"
    )
    source["file_count"] = sum(1 for path in workspace.rglob("*") if path.is_file())
    source["total_bytes"] = sum(
        path.stat().st_size for path in workspace.rglob("*") if path.is_file()
    )
    source["fingerprint"] = snapshot_fingerprint(workspace)
    (slot / "current" / "lease.json").write_text(
        json.dumps({key: value for key, value in source.items() if key != "kind"}),
        encoding="utf-8",
    )
    server = _server(tmp_path, slot)
    monkeypatch.setenv("CODING_AGENT_MODE", "draft")
    monkeypatch.delenv("CODING_AGENT_MODEL", raising=False)
    monkeypatch.setattr(
        "server.coding_runtime.worker.create_acp_client",
        lambda *args, **kwargs: pytest.fail("writeback handoff started an ACP agent"),
    )
    patch = "\n".join(
        [
            "diff --git a/app.py b/app.py",
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,4 +1,4 @@",
            "-from legacy_formatter import slugify",
            "+from formatters.slug import slugify",
            " ",
            " ",
            " def render_cache_key(project: str, revision: int) -> str:",
            "diff --git a/formatters/slug.py b/formatters/slug.py",
            "new file mode 100644",
            "--- /dev/null",
            "+++ b/formatters/slug.py",
            "@@ -0,0 +1,7 @@",
            "+import re",
            "+",
            "+",
            "+def slugify(value: str) -> str:",
            '+    """Return a stable lowercase slug for a display label."""',
            '+    collapsed = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())',
            '+    return collapsed.strip("-")',
            "diff --git a/legacy_formatter.py b/legacy_formatter.py",
            "deleted file mode 100644",
            "--- a/legacy_formatter.py",
            "+++ /dev/null",
            "@@ -1,7 +0,0 @@",
            "-import re",
            "-",
            "-",
            "-def slugify(value: str) -> str:",
            '-    """Return a stable lowercase slug for a display label."""',
            '-    collapsed = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())',
            '-    return collapsed.strip("-")',
            "",
        ]
    )
    writer = _MemoryWriter()

    await server._restore_session(
        {
            "revision": 1,
            "patch": patch,
            "paths": ["app.py", "formatters/slug.py", "legacy_formatter.py"],
            "snapshot_fingerprint": source["fingerprint"],
            "source": source,
            "writeback_only": True,
        },
        writer,
    )

    response = writer.frames[-1]
    assert response["recovered"] is True
    assert response["project"]["kind"] == "host_git"
    assert (tmp_path / "workspace" / "app.py").read_text(encoding="utf-8") == (
        "from formatters.slug import slugify\n\n\n"
        "def render_cache_key(project: str, revision: int) -> str:\n"
        "    return f\"{slugify(project)}:r{revision}\"\n"
    )
    assert (tmp_path / "workspace" / "formatters" / "slug.py").is_file()
    assert not (tmp_path / "workspace" / "legacy_formatter.py").exists()
    with pytest.raises(CodingWorkerError) as unavailable:
        await server._prompt(
            {"session_id": response["session_id"], "prompt": "change it again"},
            _MemoryWriter(),
        )
    assert unavailable.value.code == "project_operation_unavailable"
    await server.close()


@pytest.mark.asyncio
async def test_worker_client_marks_writeback_only_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    async def capture_request(
        _client: CodingWorkerClient,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        requests.append({**payload, "timeout": timeout})
        return {"recovered": True, "changes": {}}

    monkeypatch.setattr(CodingWorkerClient, "_request", capture_request)
    client = CodingWorkerClient("unused.sock")

    await client.restore_session(
        revision=1,
        patch="",
        paths=[],
        snapshot_fingerprint="a" * 64,
        source={"kind": "host_git"},
        writeback_only=True,
    )

    assert requests == [
        {
            "action": "restore_session",
            "revision": 1,
            "patch": "",
            "paths": [],
            "base_patch": "",
            "base_paths": [],
            "snapshot_fingerprint": "a" * 64,
            "verification": None,
            "source": {"kind": "host_git"},
            "writeback_only": True,
            "timeout": 130.0,
        }
    ]


def test_runtime_keeps_project_config_disabled_and_uses_generic_agent_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_AGENT_MODEL_BASE_URL", "https://coding-provider.example/v1"
    )
    root = Path(__file__).resolve().parents[2]
    worker = (root / "server/coding_runtime/worker.py").read_text(encoding="utf-8")
    dockerfile = (root / "server/coding_worker/Dockerfile").read_text(encoding="utf-8")

    assert '"OPENCODE_DISABLE_PROJECT_CONFIG": "1"' in worker
    assert '"plugin": []' in worker
    assert '"mcp": {}' in worker
    assert "Isolated project change draft assistant" in worker
    assert "Read-only project analyst" in worker
    assert "CODING_PROJECT_SNAPSHOT_PATH=/project-snapshots/current" in dockerfile
    assert "/project-snapshots" in dockerfile

    without_project_rules = build_opencode_config("provider/model", "draft")
    with_project_rules = build_opencode_config(
        "provider/model",
        "draft",
        project_instructions=True,
    )
    assert without_project_rules["instructions"] == []
    assert with_project_rules["instructions"] == ["/workspace/AGENTS.md"]
