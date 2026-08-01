from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.sandbox_sidecar.engine import SandboxEngine, SandboxEngineError
from server.xpert_runtime import (
    LocalSandboxClient,
    RuntimeToolCall,
    SandboxToolsetProvider,
    SandboxWorkspaceStore,
)


class FakeSkill:
    def __init__(self, skill_id: str, root: Path) -> None:
        self.skill_id = skill_id
        self.name = "Local test skill"
        self.description = "Runs an offline script."
        self.root = root


class FakeSkillManager:
    def __init__(self, root: Path) -> None:
        self.skill = FakeSkill("local-test", root)

    def list_installed_skills(self):
        return [self.skill]

    def get_skill_content(self, skill_id: str) -> str:
        assert skill_id == self.skill.skill_id
        return (self.skill.root / "SKILL.md").read_text(encoding="utf-8")

    def get_skill_directory(self, skill_id: str) -> Path:
        assert skill_id == self.skill.skill_id
        return self.skill.root


def test_engine_enforces_paths_quota_binary_and_idempotency(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    base = {
        "workspace_id": "workspace-1",
        "action": "write_file",
        "path": "work/result.txt",
        "content": "hello sandbox",
        "quota_bytes": 1024,
        "operation_id": "write-1",
    }

    first = engine.dispatch(base)
    second = engine.dispatch(base)
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert engine.dispatch(
        {
            "action": "read_file",
            "workspace_id": "workspace-1",
            "path": "work/result.txt",
        }
    )["content"] == "hello sandbox"

    with pytest.raises(SandboxEngineError, match="Unsafe"):
        engine.dispatch(
            {
                "action": "read_file",
                "workspace_id": "workspace-1",
                "path": "../outside.txt",
            }
        )
    with pytest.raises(SandboxEngineError) as quota_error:
        engine.dispatch(
            {
                **base,
                "path": "work/large.txt",
                "content": "x" * 2048,
                "operation_id": "write-2",
            }
        )
    assert quota_error.value.code == "quota_exceeded"

    binary = tmp_path / "workspaces" / "workspace-1" / "work" / "binary.bin"
    binary.write_bytes(b"\x00\x01")
    with pytest.raises(SandboxEngineError) as binary_error:
        engine.dispatch(
            {
                "action": "read_file",
                "workspace_id": "workspace-1",
                "path": "work/binary.bin",
            }
        )
    assert binary_error.value.code == "binary_file"


def test_engine_rejects_symlink_and_unapproved_command(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    engine.dispatch({"action": "ensure_workspace", "workspace_id": "workspace-1"})
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "workspaces" / "workspace-1" / "work" / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows host.")
    with pytest.raises(SandboxEngineError) as symlink_error:
        engine.dispatch(
            {
                "action": "list_files",
                "workspace_id": "workspace-1",
                "path": "work/link",
            }
        )
    assert symlink_error.value.code == "symlink_denied"
    with pytest.raises(SandboxEngineError) as command_error:
        engine.dispatch(
            {
                "action": "shell",
                "workspace_id": "workspace-1",
                "argv": ["powershell", "Get-ChildItem"],
                "operation_id": "shell-1",
            }
        )
    assert command_error.value.code == "command_denied"


@pytest.mark.asyncio
async def test_toolset_stages_skill_and_publishes_durable_artifact(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("# Local test\nRun script.py.", encoding="utf-8")
    (skill_root / "script.py").write_text("print('ok')", encoding="utf-8")
    engine = SandboxEngine(workspace_root, require_landlock=False)
    store = SandboxWorkspaceStore(tmp_path / "runtime", workspace_root=workspace_root)
    provider = SandboxToolsetProvider(
        store,
        LocalSandboxClient(engine),
        skill_manager=FakeSkillManager(skill_root),
    )
    metadata = {
        "task_id": "task-1",
        "run_id": "run-1",
        "node_id": "agent-1",
        "iteration": 1,
        "skills_config": {"skill_ids": "local-test"},
        "sandbox_config": {"quota_mb": 16},
    }

    staged = await provider.call_tool(
        RuntimeToolCall("skill_stage", {"skill_id": "local-test"}, metadata)
    )
    assert "skills/local-test/SKILL.md" in staged.output
    written = await provider.call_tool(
        RuntimeToolCall(
            "sandbox_write_file",
            {"path": "work/report.md", "content": "# Result"},
            {**metadata, "iteration": 2},
        )
    )
    assert written.metadata["workspace_id"]
    published = await provider.call_tool(
        RuntimeToolCall(
            "sandbox_publish_artifact",
            {"path": "work/report.md"},
            {**metadata, "iteration": 3},
        )
    )
    artifact = store.get_artifact(published.metadata["artifact_id"])
    assert artifact.filename == "report.md"
    assert store.artifact_path(artifact.artifact_id).read_text(encoding="utf-8") == "# Result"
    reloaded = SandboxWorkspaceStore(tmp_path / "runtime", workspace_root=workspace_root)
    assert reloaded.get_artifact(artifact.artifact_id).sha256 == artifact.sha256


@pytest.mark.asyncio
async def test_toolset_denies_public_app(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("# test", encoding="utf-8")
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(tmp_path / "runtime", workspace_root=tmp_path / "workspaces"),
        LocalSandboxClient(engine),
        skill_manager=FakeSkillManager(skill_root),
    )
    with pytest.raises(Exception, match="Xpert App"):
        await provider.call_tool(
            RuntimeToolCall(
                "sandbox_list_files",
                {},
                {"runtime_run_type": "xpert_app", "node_id": "agent"},
            )
        )
