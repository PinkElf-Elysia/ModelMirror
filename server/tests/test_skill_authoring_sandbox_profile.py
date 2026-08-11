from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from server.sandbox_sidecar.engine import SandboxEngine, SandboxEngineError
from server.xpert_runtime.sandbox_client import LocalSandboxClient


PROFILE = "skill_authoring_v1"


def _workspace(engine: SandboxEngine, workspace_id: str = "authoring-1") -> dict[str, str]:
    created = engine.dispatch({"action": "ensure_workspace", "workspace_id": workspace_id, "profile": PROFILE})
    return {
        "workspace_id": workspace_id,
        "profile": PROFILE,
        "provisioning_capability": str(created["provisioning_capability"]),
    }


def test_authoring_profile_health_and_scope(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    health = asyncio.run(LocalSandboxClient(engine).health(required_profile=PROFILE))
    assert health["profiles"][PROFILE] == health["profiles"]["skill_evaluation_v1"]
    auth = _workspace(engine)
    seeded = engine.dispatch({
        **auth,
        "action": "seed_file",
        "path": "skills/authoring-resource/normalize.py",
        "content": "print('ok')",
        "operation_id": "script",
    })
    assert seeded["path"] == "skills/authoring-resource/normalize.py"
    with pytest.raises(SandboxEngineError) as wrong_alias:
        engine.dispatch({
            **auth,
            "action": "seed_file",
            "path": "skills/evaluation-skill/normalize.py",
            "content": "print('wrong')",
            "operation_id": "wrong-alias",
        })
    assert wrong_alias.value.code == "seed_scope_denied"
    with pytest.raises(SandboxEngineError) as denied:
        engine.dispatch({**auth, "action": "shell", "argv": ["npm", "--version"], "operation_id": "npm"})
    assert denied.value.code == "command_denied"
    with pytest.raises(SandboxEngineError) as downgrade:
        engine.dispatch({"action": "read_file", "workspace_id": "authoring-1", "path": "skills/authoring-resource/normalize.py"})
    assert downgrade.value.code == "sandbox_profile_mismatch"


@pytest.mark.skipif(os.name != "posix", reason="Landlock is Linux-only")
def test_authoring_landlock_keeps_inputs_and_skills_read_only(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=True)
    auth = _workspace(engine)
    engine.dispatch({**auth, "action": "seed_file", "path": "inputs/case.txt", "content": "input", "operation_id": "input"})
    probe = """
import json
from pathlib import Path
result = {}
for name in ('../inputs/case.txt', '../skills/authoring-resource/probe.py', 'result.txt', '../.tmp/cache.txt'):
    try:
        Path(name).write_text('changed', encoding='utf-8')
    except OSError:
        result[name] = False
    else:
        result[name] = True
print(json.dumps(result, sort_keys=True))
""".strip()
    engine.dispatch({**auth, "action": "seed_file", "path": "skills/authoring-resource/probe.py", "content": probe, "operation_id": "probe"})
    result = engine.dispatch({**auth, "action": "shell", "argv": ["python", "../skills/authoring-resource/probe.py"], "operation_id": "run"})
    assert result["exit_code"] == 0, result["stderr"]
    assert json.loads(result["stdout"]) == {
        "../.tmp/cache.txt": True,
        "../inputs/case.txt": False,
        "../skills/authoring-resource/probe.py": False,
        "result.txt": True,
    }


def test_profile_never_changes_default_workspace_contract(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "workspaces", require_landlock=False)
    _workspace(engine, "authoring-bound")
    written = engine.dispatch({
        "action": "write_file",
        "workspace_id": "default-workspace",
        "path": "inputs/default.txt",
        "content": "default remains writable",
        "operation_id": "default",
    })
    assert written["path"] == "inputs/default.txt"
