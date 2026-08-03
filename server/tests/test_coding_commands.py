from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.coding_runtime.commands import (
    CommandContractError,
    ProjectCommandKind,
    ProjectCommandOrigin,
    ProjectVerificationConfig,
    command_plan_fingerprint,
    dependency_input_hashes,
    detect_project_commands,
    load_runner_pack_manifest,
    normalize_agent_command,
    normalize_project_command,
    parse_project_verification,
    runner_pack_matches_project,
)
from server.coding_runtime.projects import load_project_manifest


def _manifest_command(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "运行随机检查",
        "kind": "test",
        "argv": ["python", "-m", "pytest", "-q"],
        "cwd": ".",
        "timeout_seconds": 240,
    }
    value.update(overrides)
    return value


def test_manifest_v1_remains_compatible_and_v2_carries_verification(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    manifest = root / ".modelmirror-coding-projects.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": [{"name": "旧项目", "path": "team/legacy"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    legacy = load_project_manifest(root)[0]

    assert legacy.verification == ProjectVerificationConfig()

    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "projects": [
                    {
                        "name": "新项目",
                        "path": "team/current",
                        "verification": {
                            "auto": False,
                            "runner_pack": "team-current-202608",
                            "commands": [_manifest_command()],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    current = load_project_manifest(root)[0]

    assert current.verification.auto is False
    assert current.verification.runner_pack == "team-current-202608"
    assert current.verification.commands[0].argv == ("python", "-m", "pytest", "-q")


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"argv": ["bash", "-c", "pytest"]}, "command_shell_denied"),
        ({"argv": ["python", "/etc/passwd"]}, "command_path_invalid"),
        ({"cwd": "../outside"}, "command_path_invalid"),
        ({"timeout_seconds": 301}, "project_command_timeout_invalid"),
        ({"argv": ["python"] * 65}, "command_argv_invalid"),
    ],
)
def test_project_command_rejects_unsafe_or_unbounded_values(
    overrides: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(CommandContractError) as error:
        normalize_project_command(
            _manifest_command(**overrides),
            origin=ProjectCommandOrigin.MANIFEST,
        )

    assert error.value.code == code


def test_agent_command_is_structured_and_stable() -> None:
    first = normalize_agent_command(
        argv=["python", "-m", "pytest", "tests/test_q7m4.py", "-q"],
        cwd=".",
        purpose="检查随机测试 q7m4",
        timeout_seconds=120,
    )
    second = normalize_agent_command(
        argv=["python", "-m", "pytest", "tests/test_q7m4.py", "-q"],
        cwd=".",
        purpose="检查随机测试 q7m4",
        timeout_seconds=120,
    )

    assert first == second
    assert first.kind is ProjectCommandKind.CUSTOM
    assert first.origin is ProjectCommandOrigin.AGENT
    assert first.command_id.startswith("command-")


def test_auto_detection_combines_python_and_node_checks_without_duplicates(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "node test.js",
                    "typecheck": "tsc --noEmit",
                    "lint": "eslint .",
                    "build": "vite build",
                    "deploy": "ignored",
                }
            }
        ),
        encoding="utf-8",
    )
    configured = normalize_project_command(
        _manifest_command(name="部署者测试"),
        origin=ProjectCommandOrigin.MANIFEST,
    )

    commands = detect_project_commands(
        tmp_path,
        ProjectVerificationConfig(commands=(configured,)),
    )

    assert commands[0] == configured
    assert len(commands) == 6
    assert [item.kind.value for item in commands] == [
        "test",
        "test",
        "test",
        "typecheck",
        "lint",
        "build",
    ]
    assert commands[1].argv[:3] == ("python", "-m", "pytest")
    assert commands[2].argv == ("npm", "run", "test")
    assert all(item.origin in {ProjectCommandOrigin.AUTO, ProjectCommandOrigin.MANIFEST} for item in commands)


def test_auto_detection_does_not_treat_unrelated_pyproject_as_tests(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "random-library"\n',
        encoding="utf-8",
    )

    assert detect_project_commands(tmp_path, ProjectVerificationConfig()) == ()


def test_parse_project_verification_rejects_duplicate_commands() -> None:
    command = _manifest_command()

    with pytest.raises(CommandContractError) as error:
        parse_project_verification(
            {"commands": [command, dict(command)]}
        )

    assert error.value.code == "project_commands_duplicate"


def test_runner_pack_binds_exact_dependency_inputs_and_plan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = b"pytest==8.4.1\n"
    package_lock = b'{"lockfileVersion":3}\n'
    (project / "requirements.txt").write_bytes(requirements)
    (project / "client").mkdir()
    (project / "client" / "package-lock.json").write_bytes(package_lock)
    inputs = {
        "requirements.txt": f"sha256:{hashlib.sha256(requirements).hexdigest()}",
        "client/package-lock.json": f"sha256:{hashlib.sha256(package_lock).hexdigest()}",
    }
    packs = tmp_path / "packs"
    pack = packs / "random-pack-7m2"
    pack.mkdir(parents=True)
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "version": 1,
                "id": "random-pack-7m2",
                "platform": "linux-x86_64",
                "python_version": "3.12",
                "node_version": "22",
                "inputs": inputs,
                "python_paths": ["python/site-packages"],
                "node_modules": {".": "node/root/node_modules"},
                "bin_paths": ["bin"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    manifest = load_runner_pack_manifest(packs, "random-pack-7m2")
    command = normalize_project_command(
        _manifest_command(),
        origin=ProjectCommandOrigin.MANIFEST,
    )
    first_plan = command_plan_fingerprint(
        [command],
        source_fingerprint="a" * 64,
        pack_fingerprint=manifest.fingerprint,
    )
    second_plan = command_plan_fingerprint(
        [command],
        source_fingerprint="a" * 64,
        pack_fingerprint=manifest.fingerprint,
    )

    assert manifest.inputs == dependency_input_hashes(
        project,
        ["requirements.txt", "client/package-lock.json"],
    )
    assert runner_pack_matches_project(manifest, project) is True
    assert first_plan == second_plan

    (project / "requirements.txt").write_text("pytest==0\n", encoding="utf-8")
    assert runner_pack_matches_project(manifest, project) is False


def test_runner_pack_rejects_platform_and_path_escape(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    pack = packs / "unsafe-pack"
    pack.mkdir(parents=True)
    payload = {
        "version": 1,
        "id": "unsafe-pack",
        "platform": "windows-x86_64",
        "python_version": "3.12",
        "node_version": "22",
        "inputs": {},
        "python_paths": ["../outside"],
        "node_modules": {},
        "bin_paths": [],
    }
    (pack / "pack.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CommandContractError) as platform_error:
        load_runner_pack_manifest(packs, "unsafe-pack")
    assert platform_error.value.code == "runner_pack_platform_mismatch"

    payload["platform"] = "linux-x86_64"
    (pack / "pack.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CommandContractError) as path_error:
        load_runner_pack_manifest(packs, "unsafe-pack")
    assert path_error.value.code == "command_path_invalid"
