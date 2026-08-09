from __future__ import annotations

import json
import os
import re
import asyncio
from pathlib import Path

import pytest

from server.sandbox_sidecar.engine import SandboxEngine, SandboxEngineError
from server.xpert_runtime.sandbox_client import (
    LocalSandboxClient,
    SandboxClientError,
)


PROFILE = "skill_evaluation_v1"


def _initialize(engine: SandboxEngine, workspace_id: str = "evaluation-1") -> tuple[str, dict[str, str]]:
    created = engine.dispatch(
        {
            "action": "ensure_workspace",
            "workspace_id": workspace_id,
            "profile": PROFILE,
        }
    )
    capability = str(created["provisioning_capability"])
    return capability, {
        "workspace_id": workspace_id,
        "profile": PROFILE,
        "provisioning_capability": capability,
    }


def _seed(
    engine: SandboxEngine,
    auth: dict[str, str],
    *,
    path: str,
    content: str,
    operation_id: str,
) -> dict[str, object]:
    return engine.dispatch(
        {
            **auth,
            "action": "seed_file",
            "path": path,
            "content": content,
            "operation_id": operation_id,
        }
    )


def test_profile_health_and_client_requirement_fail_closed(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    health = engine.dispatch({"action": "health"})

    profile = health["profiles"][PROFILE]
    assert profile == {
        "allowed_commands": ["node", "python", "python3", "rg"],
        "network_policy": "container_network_none_required",
        "read_only_roots": ["inputs", "skills"],
        "writable_roots": ["work", ".tmp"],
        "write_file_roots": ["work"],
        "provisioning": "capability_bound",
        "lifecycle_actions": [
            "ensure_workspace",
            "seed_file",
            "seal_workspace",
            "collect_work_manifest",
            "cleanup_workspace",
        ],
    }

    with pytest.raises(SandboxEngineError) as unsupported:
        engine.dispatch(
            {
                "action": "ensure_workspace",
                "workspace_id": "evaluation-1",
                "profile": "skill_evaluation_v2",
            }
        )
    assert unsupported.value.code == "sandbox_profile_unsupported"


def test_client_rejects_sidecar_without_required_profile(tmp_path: Path) -> None:
    class LegacyEngine:
        def dispatch(self, request: dict[str, object]) -> dict[str, object]:
            assert request == {"action": "health"}
            return {"ok": True, "engine": "legacy"}

    with pytest.raises(SandboxClientError) as missing:
        asyncio.run(LocalSandboxClient(LegacyEngine()).health(required_profile=PROFILE))
    assert missing.value.code == "sandbox_profile_unsupported"

    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    health = asyncio.run(LocalSandboxClient(engine).health(required_profile=PROFILE))
    assert PROFILE in health["profiles"]


def test_profile_binding_seeding_sealing_manifest_and_cleanup(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    capability, auth = _initialize(engine)

    _seed(
        engine,
        auth,
        path="inputs/case.txt",
        content="fixture",
        operation_id="seed-input",
    )
    _seed(
        engine,
        auth,
        path="skills/evaluation-skill/SKILL.md",
        content="# Evaluation skill",
        operation_id="seed-skill",
    )
    with pytest.raises(SandboxEngineError) as seed_scope:
        _seed(
            engine,
            auth,
            path="skills/other/SKILL.md",
            content="# Wrong alias",
            operation_id="seed-wrong-skill",
        )
    assert seed_scope.value.code == "seed_scope_denied"

    with pytest.raises(SandboxEngineError) as missing_capability:
        engine.dispatch(
            {
                "action": "read_file",
                "workspace_id": "evaluation-1",
                "profile": PROFILE,
                "path": "inputs/case.txt",
            }
        )
    assert missing_capability.value.code == "sandbox_profile_capability_invalid"

    with pytest.raises(SandboxEngineError) as downgrade:
        engine.dispatch(
            {
                "action": "read_file",
                "workspace_id": "evaluation-1",
                "path": "inputs/case.txt",
            }
        )
    assert downgrade.value.code == "sandbox_profile_mismatch"

    sealed_response = engine.dispatch({**auth, "action": "seal_workspace"})
    assert sealed_response["sealed"] is True

    with pytest.raises(SandboxEngineError) as input_write:
        engine.dispatch(
            {
                **auth,
                "action": "write_file",
                "path": "inputs/changed.txt",
                "content": "denied",
                "operation_id": "write-input",
            }
        )
    assert input_write.value.code == "write_scope_denied"

    with pytest.raises(SandboxEngineError) as dot_path:
        engine.dispatch(
            {
                **auth,
                "action": "write_file",
                "path": ".",
                "content": "denied",
                "operation_id": "write-dot-path",
            }
        )
    assert dot_path.value.code == "unsafe_path"

    written = engine.dispatch(
        {
            **auth,
            "action": "write_file",
            "path": "work/result.txt",
            "content": "result",
            "operation_id": "write-result",
        }
    )
    assert written["path"] == "work/result.txt"

    with pytest.raises(SandboxEngineError) as sealed:
        _seed(
            engine,
            auth,
            path="inputs/late.txt",
            content="too late",
            operation_id="seed-late",
        )
    assert sealed.value.code == "evaluation_workspace_sealed"

    manifest = engine.dispatch({**auth, "action": "collect_work_manifest"})
    assert manifest["truncated"] is False
    assert manifest["files"] == [
        {
            "path": "work/result.txt",
            "size_bytes": 6,
            "sha256": (
                "f6a214f7a5fcda0c2cee9660b7fc29f5649e3c68aad48e20e950137c98913a68"
            ),
            "text_preview": "result",
            "preview_truncated": False,
        }
    ]

    repeated = engine.dispatch(
        {
            "action": "ensure_workspace",
            "workspace_id": "evaluation-1",
            "profile": PROFILE,
            "provisioning_capability": capability,
        }
    )
    assert repeated["sealed"] is True
    assert "provisioning_capability" not in repeated

    cleaned = engine.dispatch({**auth, "action": "cleanup_workspace"})
    assert cleaned["removed"] is True
    assert not (tmp_path / "workspaces" / "evaluation-1").exists()


def test_profile_narrows_shell_commands_without_changing_default(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    _, auth = _initialize(engine)

    with pytest.raises(SandboxEngineError) as denied:
        engine.dispatch(
            {
                **auth,
                "action": "shell",
                "argv": ["npm", "--version"],
                "operation_id": "shell-npm",
            }
        )
    assert denied.value.code == "command_denied"

    default = engine.dispatch(
        {
            "action": "write_file",
            "workspace_id": "default-1",
            "path": "inputs/legacy.txt",
            "content": "legacy behavior",
            "operation_id": "default-write",
        }
    )
    assert default["path"] == "inputs/legacy.txt"


@pytest.mark.skipif(os.name != "posix", reason="Landlock is Linux-only")
def test_landlock_profile_makes_inputs_and_skills_read_only(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=True)
    _, auth = _initialize(engine)
    _seed(
        engine,
        auth,
        path="inputs/case.txt",
        content="original fixture",
        operation_id="seed-input",
    )
    _seed(
        engine,
        auth,
        path="skills/evaluation-skill/SKILL.md",
        content="# Original skill",
        operation_id="seed-skill",
    )
    script = """
import json
from pathlib import Path
results = {}
for path in (
    Path('../inputs/case.txt'),
    Path('../skills/evaluation-skill/SKILL.md'),
    Path('result.txt'),
    Path('../.tmp/cache.txt'),
):
    try:
        path.write_text('changed', encoding='utf-8')
    except OSError:
        results[path.as_posix()] = False
    else:
        results[path.as_posix()] = True
print(json.dumps(results, sort_keys=True))
""".strip()
    _seed(
        engine,
        auth,
        path="skills/evaluation-skill/landlock_probe.py",
        content=script,
        operation_id="seed-landlock-probe",
    )
    result = engine.dispatch(
        {
            **auth,
            "action": "shell",
            "argv": ["python", "../skills/evaluation-skill/landlock_probe.py"],
            "operation_id": "shell-landlock",
        }
    )
    assert result["exit_code"] == 0, result["stderr"]
    assert json.loads(result["stdout"]) == {
        "../.tmp/cache.txt": True,
        "../inputs/case.txt": False,
        "../skills/evaluation-skill/SKILL.md": False,
        "result.txt": True,
    }
    workspace = tmp_path / "workspaces" / "evaluation-1"
    assert (workspace / "inputs/case.txt").read_text(encoding="utf-8") == "original fixture"
    assert (
        workspace / "skills/evaluation-skill/SKILL.md"
    ).read_text(encoding="utf-8") == "# Original skill"
    assert (workspace / "work/result.txt").read_text(encoding="utf-8") == "changed"
    assert (workspace / ".tmp/cache.txt").read_text(encoding="utf-8") == "changed"


def test_compose_keeps_sidecar_offline_for_evaluation_profile() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^  sandbox:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", compose)
    assert match is not None
    body = match.group("body")
    assert "network_mode: none" in body
    assert "read_only: true" in body
    assert "no-new-privileges:true" in body
