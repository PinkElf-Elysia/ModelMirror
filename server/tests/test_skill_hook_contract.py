from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from server.skills.hook_contract import (
    HOOK_MANIFEST_VERSION,
    HOOK_RESULT_VERSION,
    SkillHookContractError,
    hook_capability_projection,
    parse_hook_manifest,
    parse_hook_result,
)
from server.skills.package_validation import validate_skill_package
from server.skills.skill_manager import InstalledSkill, SkillManager


def _hook(
    *,
    hook_id: str = "check-release-name",
    event: str = "pre_tool_use",
    mode: str = "guard",
    tool_names: list[str] | None = None,
    script_path: str = "scripts/check_release.py",
) -> dict:
    return {
        "hook_id": hook_id,
        "event": event,
        "mode": mode,
        "tool_names": ["sandbox_write_file"] if tool_names is None else tool_names,
        "script_path": script_path,
        "purpose": "Reject unsafe release file names before a write.",
        "acceptance_checks": ["Allow a safe relative release path.", "Deny an executable extension."],
        "timeout_seconds": 15,
    }


def _manifest(*hooks: dict) -> str:
    return json.dumps(
        {"version": HOOK_MANIFEST_VERSION, "hooks": list(hooks or (_hook(),))},
        ensure_ascii=False,
    )


def _skill_markdown() -> str:
    return (
        "---\n"
        "name: release-file-guard\n"
        "description: Check release file names before writing published artifacts.\n"
        "---\n\n"
        "# Release file guard\n\n"
        "The immutable Hook contract is in `hooks/manifest.json`.\n"
    )


def _gold_fixture_root() -> Path:
    return Path(__file__).parent / "fixtures" / "skill_hook_v2_gold"


def test_parses_fixed_manifest_and_projects_only_bounded_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _manifest()

    manifest = parse_hook_manifest(
        content,
        available_paths={"hooks/manifest.json", "scripts/check_release.py"},
    )

    assert manifest.version == HOOK_MANIFEST_VERSION
    assert len(manifest.hooks) == 1
    assert manifest.hooks[0].tool_names == ("sandbox_write_file",)
    assert len(manifest.fingerprint) == 64
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "false")
    projection = hook_capability_projection(
        content, available_paths={"hooks/manifest.json", "scripts/check_release.py"}
    )
    assert projection == {
        "available": True,
        "manifestVersion": HOOK_MANIFEST_VERSION,
        "manifestFingerprint": manifest.fingerprint,
        "hookCount": 1,
        "events": ["pre_tool_use"],
        "modes": ["guard"],
        "contractValid": True,
        "runnable": False,
        "errorCode": None,
        "hooks": [
            {
                "hookId": "check-release-name",
                "event": "pre_tool_use",
                "mode": "guard",
                "toolNames": ["sandbox_write_file"],
                "timeoutSeconds": 15,
            }
        ],
    }
    assert "purpose" not in projection
    assert "tool_names" not in projection


def test_installed_projection_is_bounded_and_disabled_before_runtime_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "false")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    skill = InstalledSkill(
        skill_id="release-file-guard",
        name="Release file guard",
        description="Check release names.",
        repo_url="plugin://fixture/v1",
        sub_path="release-file-guard",
        installed_at=time.time(),
        source_kind="plugin",
        source_id="fixture",
        source_revision=1,
    )
    root = manager.installed_dir / skill.skill_id
    (root / "scripts").mkdir(parents=True)
    (root / "hooks").mkdir()
    (root / "SKILL.md").write_text(_skill_markdown(), encoding="utf-8")
    (root / "scripts" / "check_release.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    (root / "hooks" / "manifest.json").write_text(_manifest(), encoding="utf-8")
    manager._write_metadata({skill.skill_id: asdict(skill)})

    projection = manager.get_hook_capability(skill.skill_id)

    assert projection["available"] is True
    assert projection["contractValid"] is True
    assert projection["runnable"] is False
    assert "purpose" not in projection
    assert "tool_names" not in projection
    assert "script_path" not in projection


@pytest.mark.parametrize(
    ("path", "output_type", "code"),
    [
        ("release/report.pdf", "validation", "release_name_allowed"),
        ("release/installer.exe", "deny", "release_extension_denied"),
    ],
)
def test_gold_fixture_validates_and_runs_offline(
    tmp_path: Path,
    path: str,
    output_type: str,
    code: str,
) -> None:
    root = _gold_fixture_root()
    files = {
        item.relative_to(root).as_posix(): item.read_text(encoding="utf-8")
        for item in root.rglob("*")
        if item.is_file() and item.name != "SKILL.md"
    }
    validation = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=(root / "SKILL.md").read_text(encoding="utf-8"),
        files=files,
    )
    assert validation.valid is True, validation.issues

    context_path = tmp_path / "context.json"
    result_path = tmp_path / "result.json"
    context_path.write_text(
        json.dumps({"tool_name": "sandbox_write_file", "arguments": {"path": path}}),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_release.py"),
            "--context",
            str(context_path),
            "--result",
            str(result_path),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    parsed = parse_hook_result(
        result_path.read_text(encoding="utf-8"),
        hook_event="pre_tool_use",
        hook_mode="guard",
    )

    assert parsed.outputs[0].output_type == output_type
    assert parsed.outputs[0].code == code


@pytest.mark.parametrize(
    "mutation",
    [
        lambda hook: hook.update(command="python"),
        lambda hook: hook.update(argv=["--unsafe"]),
        lambda hook: hook.update(tool_names=["sandbox_*"]),
        lambda hook: hook.update(script_path="../outside.py"),
        lambda hook: hook.update(script_path="scripts/check_release.sh"),
        lambda hook: hook.update(event="session_start"),
        lambda hook: hook.update(mode="guard", event="session_end", tool_names=[]),
    ],
)
def test_rejects_manifest_escape_hatches_and_invalid_event_mode_combinations(
    mutation,
) -> None:
    hook = _hook()
    mutation(hook)

    with pytest.raises(SkillHookContractError) as error:
        parse_hook_manifest(
            _manifest(hook), available_paths={"scripts/check_release.py"}
        )

    assert error.value.code == "skill_hook_manifest_invalid"


def test_requires_unique_ids_exact_script_and_strict_json() -> None:
    duplicate = _manifest(_hook(), _hook())
    duplicate_key = (
        '{"version":"modelmirror-hook-manifest-v2",'
        '"version":"modelmirror-hook-manifest-v2","hooks":[]}'
    )

    for content, paths in (
        (duplicate, {"scripts/check_release.py"}),
        (_manifest(), {"scripts/other.py"}),
        (duplicate_key, set()),
    ):
        with pytest.raises(SkillHookContractError):
            parse_hook_manifest(content, available_paths=paths)


def test_session_hook_omits_tool_filter_and_timeout_defaults_to_fifteen() -> None:
    hook = _hook(
        hook_id="summarize-session",
        event="session_end",
        mode="annotation",
        tool_names=[],
    )
    hook.pop("tool_names")
    hook.pop("timeout_seconds")

    manifest = parse_hook_manifest(
        _manifest(hook), available_paths={"scripts/check_release.py"}
    )

    assert manifest.hooks[0].tool_names == ()
    assert manifest.hooks[0].timeout_seconds == 15


def test_package_validation_allows_only_canonical_hook_manifest() -> None:
    valid = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=_skill_markdown(),
        files={
            "scripts/check_release.py": "import sys\nraise SystemExit(0)\n",
            "hooks/manifest.json": _manifest(),
        },
    )
    unsupported = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=_skill_markdown(),
        files={
            "scripts/check_release.py": "raise SystemExit(0)\n",
            "hooks/extra.json": _manifest(),
        },
    )
    missing_script = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=_skill_markdown(),
        files={"hooks/manifest.json": _manifest()},
    )

    assert valid.valid is True, valid.issues
    assert valid.package is not None
    assert "hooks/manifest.json" in valid.package.files
    assert {issue.code for issue in unsupported.issues} >= {"hooks_file_unsupported"}
    assert {issue.code for issue in missing_script.issues} >= {
        "skill_hook_manifest_invalid"
    }


def test_manifest_or_script_change_invalidates_package_and_manifest_digests() -> None:
    first_manifest = _manifest()
    changed_hook = _hook()
    changed_hook["timeout_seconds"] = 16
    changed_manifest = _manifest(changed_hook)
    files = {
        "scripts/check_release.py": "raise SystemExit(0)\n",
        "hooks/manifest.json": first_manifest,
    }
    first = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=_skill_markdown(),
        files=files,
    )
    manifest_changed = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=_skill_markdown(),
        files={**files, "hooks/manifest.json": changed_manifest},
    )
    script_changed = validate_skill_package(
        root_name="release-file-guard",
        skill_markdown=_skill_markdown(),
        files={**files, "scripts/check_release.py": "raise SystemExit(1)\n"},
    )

    assert first.package is not None
    assert manifest_changed.package is not None
    assert script_changed.package is not None
    assert len(
        {
            first.package.content_digest,
            manifest_changed.package.content_digest,
            script_changed.package.content_digest,
        }
    ) == 3
    assert parse_hook_manifest(
        first_manifest, available_paths=files
    ).fingerprint != parse_hook_manifest(
        changed_manifest, available_paths=files
    ).fingerprint


def test_parses_typed_results_and_rejects_rewrites_or_wrong_deny_boundary() -> None:
    valid = parse_hook_result(
        json.dumps(
            {
                "version": HOOK_RESULT_VERSION,
                "outputs": [
                    {
                        "type": "deny",
                        "code": "release_name_denied",
                        "message": "Executable extensions are not allowed.",
                    }
                ],
            }
        ),
        hook_event="pre_tool_use",
        hook_mode="guard",
    )
    assert valid.outputs[0].output_type == "deny"

    rewrite = json.dumps(
        {
            "version": HOOK_RESULT_VERSION,
            "outputs": [
                {
                    "type": "validation",
                    "code": "checked",
                    "passed": True,
                    "message": "Safe.",
                    "new_arguments": {"path": "changed"},
                }
            ],
        }
    )
    deny_after = json.dumps(
        {
            "version": HOOK_RESULT_VERSION,
            "outputs": [
                {"type": "deny", "code": "late_deny", "message": "Too late."}
            ],
        }
    )
    with pytest.raises(SkillHookContractError):
        parse_hook_result(rewrite, hook_event="post_tool_use", hook_mode="validation")
    with pytest.raises(SkillHookContractError):
        parse_hook_result(deny_after, hook_event="post_tool_use", hook_mode="guard")

    invalid_message = json.dumps(
        {
            "version": HOOK_RESULT_VERSION,
            "outputs": [
                {
                    "type": "annotation",
                    "code": "empty_message",
                    "severity": "warning",
                    "message": "",
                }
            ],
        }
    )
    with pytest.raises(SkillHookContractError) as invalid:
        parse_hook_result(
            invalid_message, hook_event="session_start", hook_mode="annotation"
        )
    assert invalid.value.code == "skill_hook_result_invalid"
