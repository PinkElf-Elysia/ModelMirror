from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from skills.finder import SkillFinder
from skills.local_import import SkillLocalImportError, SkillLocalImportStore
from skills.skill_manager import InstalledSkill, SkillManager, SkillValidationError
from skills.trust_service import (
    SkillRuntimeEnvironment,
    SkillTrustAcknowledgementStore,
    SkillTrustService,
)
from xpert_runtime.sandbox_store import SandboxWorkspace
from xpert_runtime.sandbox_toolset import (
    SKILL_STAGE_MAX_FILE_BYTES,
    SKILL_STAGE_MAX_FILES,
    SKILL_STAGE_MAX_TOTAL_BYTES,
    SandboxToolsetProvider,
)
from xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_INDEX = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"


def _markdown(name: str, body: str = "1. Read the input.\n2. Return the result.") -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use this local Skill for a bounded deterministic task.\n"
        "---\n\n"
        "## Workflow\n\n"
        f"{body}\n"
    ).encode()


def _manager(tmp_path: Path, store: SkillLocalImportStore) -> SkillManager:
    trust = SkillTrustService(
        mode="enforce",
        acknowledgement_store=SkillTrustAcknowledgementStore(
            path=tmp_path / "trust-acknowledgements.json"
        ),
    )
    return SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        trust_service=trust,
        local_import_store=store,
    )


def _install(
    store: SkillLocalImportStore,
    manager: SkillManager,
    record,
    *,
    confirmed: bool = False,
    expected_installed_digest: str | None = None,
):
    return manager.install_local_import_current(
        record.import_id,
        expected_revision=record.revision,
        expected_package_digest=record.package_digest,
        expected_trust_fingerprint=record.trust_fingerprint,
        confirmed=confirmed,
        expected_installed_digest=expected_installed_digest,
    )


def test_low_risk_local_import_install_is_idempotent_and_activatable(
    tmp_path: Path,
) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [("SKILL.md", _markdown("local-report"))]
    )
    manager = _manager(tmp_path, store)

    installed_record, installed = _install(store, manager, record)
    replay_record, replayed = _install(store, manager, installed_record)

    assert installed_record.state == "installed"
    assert installed.source_kind == "local_import"
    assert installed.source_id == record.import_id
    assert installed.source_revision == record.content_revision
    assert installed.content_digest == record.package_digest
    assert installed.trust_state == "receipt_matched"
    assert replay_record.revision == installed_record.revision
    assert replayed.content_digest == installed.content_digest
    assert manager.require_activation("local-report").skill_id == "local-report"

    manager.trust_service.mode = "off"
    manager.local_import_store = None
    assert manager.require_activation("local-report").skill_id == "local-report"


def test_confirmed_script_install_still_requires_persistent_activation_ack(
    tmp_path: Path,
) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [
            (
                "SKILL.md",
                _markdown(
                    "local-script",
                    "1. Run `python scripts/check.py`.\n2. Return the output.",
                ),
            ),
            ("scripts/check.py", b"print('ok')\n"),
        ]
    )
    manager = _manager(tmp_path, store)

    with pytest.raises(SkillValidationError) as error:
        _install(store, manager, record)
    assert error.value.code == "skill_trust_ack_required"

    installed_record, installed = _install(store, manager, record, confirmed=True)
    with pytest.raises(SkillValidationError) as activation_error:
        manager.require_activation(
            installed.skill_id,
            runtime_environment=SkillRuntimeEnvironment.installation_baseline(),
        )
    assert activation_error.value.code == "skill_trust_ack_required"

    manager.trust_service.acknowledge(
        skill_id=installed.skill_id,
        trust_fingerprint=installed_record.trust_fingerprint,
        confirmed=True,
        receipt=installed_record.trust_receipt,
    )
    assert (
        manager.require_activation(
            installed.skill_id,
            runtime_environment=SkillRuntimeEnvironment.installation_baseline(),
        ).skill_id
        == "local-script"
    )


def test_replacement_is_explicit_and_supersedes_previous_import(
    tmp_path: Path,
) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    first = store.create_from_folder(
        [("SKILL.md", _markdown("local-report", "1. Return version one."))]
    )
    manager = _manager(tmp_path, store)
    first_installed_record, first_installed = _install(store, manager, first)
    second = store.create_from_folder(
        [("SKILL.md", _markdown("local-report", "1. Return version two."))]
    )

    with pytest.raises(SkillValidationError) as error:
        _install(store, manager, second)
    assert error.value.code == "skill_import_replace_required"

    second_record, second_installed = _install(
        store,
        manager,
        second,
        expected_installed_digest=first_installed.content_digest,
    )

    assert second_record.state == "installed"
    assert second_installed.skill_id == first_installed.skill_id
    assert second_installed.content_digest == second.package_digest
    assert store.require(first.import_id).state == "superseded"
    assert store.require(first.import_id).installed_skill_id is None
    assert first_installed_record.installed_skill_id == "local-report"
    with pytest.raises(SkillLocalImportError) as superseded:
        _install(
            store,
            manager,
            store.require(first.import_id),
            expected_installed_digest=second_installed.content_digest,
        )
    assert superseded.value.code == "skill_import_stale"


def test_local_import_cannot_replace_another_source(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [("SKILL.md", _markdown("shared-skill", "1. Return the local version."))]
    )
    manager = _manager(tmp_path, store)
    target = manager.installed_dir / "shared-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(
        _markdown("shared-skill", "1. Return the workspace version.")
    )
    foreign = InstalledSkill(
        skill_id="shared-skill",
        name="Shared Skill",
        description="Existing non-import Skill.",
        repo_url="workspace://draft/workspace-draft",
        sub_path="",
        installed_at=1_700_000_000.0,
        source_kind="workspace_draft",
        source_id="workspace-draft",
        source_revision=1,
        content_digest=manager._directory_content_digest(target),
    )
    manager._write_metadata({foreign.skill_id: asdict(foreign)})

    with pytest.raises(SkillValidationError) as error:
        _install(store, manager, record)

    assert error.value.code == "skill_import_replace_required"
    assert "workspace version" in manager.get_skill_content("shared-skill")


def test_replacement_preview_keeps_binary_content_out_of_diff(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    markdown = _markdown(
        "local-binary-preview",
        "1. Copy `assets/template.pdf`.\n2. Return the rendered document.",
    )
    first = store.create_from_folder(
        [
            ("SKILL.md", markdown),
            ("assets/template.pdf", b"%PDF-1.4\nfirst\n%%EOF\n"),
        ]
    )
    manager = _manager(tmp_path, store)
    _install(store, manager, first, confirmed=True)
    second = store.create_from_folder(
        [
            ("SKILL.md", markdown),
            ("assets/template.pdf", b"%PDF-1.4\nsecond\n%%EOF\n"),
        ]
    )

    preview = manager.describe_local_import_replacement(
        record=second,
        package_dir=store.package_directory(second.import_id),
    )

    assert preview is not None
    assert preview["required"] is True
    changed = next(
        item for item in preview["changes"] if item["path"] == "assets/template.pdf"
    )
    assert changed["kind"] == "binary"
    assert "diff" not in changed
    assert changed["oldSha256"] != changed["newSha256"]


def test_uninstall_projection_is_retry_safe(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [("SKILL.md", _markdown("local-uninstall"))]
    )
    manager = _manager(tmp_path, store)
    installed_record, installed = _install(store, manager, record)

    manager.uninstall_skill(installed.skill_id)
    changed = store.mark_uninstalled_skill(installed.skill_id)
    replay = store.mark_uninstalled_skill(installed.skill_id)

    assert [item.import_id for item in changed] == [installed_record.import_id]
    current = store.require(installed_record.import_id)
    assert current.installed_skill_id is None
    assert current.state == "ready"
    assert replay == []


def test_router_uses_exact_local_receipt_and_acknowledgement(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [
            (
                "SKILL.md",
                _markdown(
                    "local-router-script-unique",
                    "1. Run `python scripts/check.py`.\n2. Return the output.",
                ),
            ),
            ("scripts/check.py", b"print('router-ok')\n"),
        ]
    )
    assert record.trust_receipt["routerEligible"] is True
    manager = _manager(tmp_path, store)
    installed_record, installed = _install(store, manager, record, confirmed=True)
    manager.acknowledge_trust(
        installed.skill_id,
        expected_trust_fingerprint=installed_record.trust_fingerprint,
        confirmed=True,
    )
    provider = SandboxToolsetProvider(
        SimpleNamespace(workspace_root=tmp_path / "workspaces"),
        SimpleNamespace(request=AsyncMock(return_value={"ok": True})),
        skill_manager=manager,
        skill_finder=SkillFinder(index_path=RUNTIME_INDEX, skill_manager=manager),
    )
    call = RuntimeToolCall(
        "skill_find",
        {"need": "local router script unique"},
        metadata={
            "task_id": "task",
            "run_id": "run",
            "node_id": "node",
            "skills_config": {"catalog_search": True},
            "skill_runtime_environment": {
                "tool_names": ["skill_read", "skill_stage", "sandbox_shell"],
                "tool_providers": ["skill", "sandbox"],
            },
            "sandbox_config": {"allowed_commands": ["python", "python3"]},
        },
    )

    found = json.loads(provider._skill_find(call).output)
    candidate = next(
        item
        for item in found["results"]
        if item["candidateId"] == f"installed:{installed.skill_id}"
    )
    assert candidate["trust"]["trustFingerprint"] == record.trust_fingerprint
    assert candidate["trustActionable"] is True

    manager.trust_service.revoke(installed.skill_id)
    revoked = json.loads(provider._skill_find(call).output)
    denied = next(
        item
        for item in revoked["results"]
        if item["candidateId"] == f"installed:{installed.skill_id}"
    )
    assert denied["trustActionable"] is False
    assert denied["trustDecision"]["errorCode"] == "skill_trust_ack_required"


def test_failed_metadata_replace_restores_previous_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    first = store.create_from_folder(
        [("SKILL.md", _markdown("local-report", "1. Return version one."))]
    )
    manager = _manager(tmp_path, store)
    _, first_installed = _install(store, manager, first)
    second = store.create_from_folder(
        [("SKILL.md", _markdown("local-report", "1. Return version two."))]
    )
    original_write = manager._write_metadata
    calls = 0

    def fail_first_replace(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk full")
        return original_write(payload)

    monkeypatch.setattr(manager, "_write_metadata", fail_first_replace)
    with pytest.raises(OSError, match="disk full"):
        _install(
            store,
            manager,
            second,
            expected_installed_digest=first_installed.content_digest,
        )

    restored = json.loads(manager.metadata_path.read_text(encoding="utf-8"))["skills"]
    assert restored["local-report"]["content_digest"] == first_installed.content_digest
    assert (
        manager.get_skill_content("local-report").find("version one")
        >= 0
    )
    assert store.require(second.import_id).state != "installed"


def test_store_projection_failure_is_safe_to_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [("SKILL.md", _markdown("local-store-retry"))]
    )
    manager = _manager(tmp_path, store)
    original_save = store._save_records_unlocked
    calls = 0

    def fail_projection_once(records):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk full")
        return original_save(records)

    monkeypatch.setattr(store, "_save_records_unlocked", fail_projection_once)
    with pytest.raises(OSError, match="disk full"):
        _install(store, manager, record)

    assert manager.get_skill_content("local-store-retry")
    assert store.require(record.import_id).state == "ready"
    with pytest.raises(SkillValidationError) as activation_error:
        manager.require_activation("local-store-retry")
    assert activation_error.value.code == "skill_trust_receipt_missing"

    retried_record, retried = _install(store, manager, record)
    assert retried_record.state == "installed"
    assert retried.content_digest == record.package_digest
    assert manager.require_activation(retried.skill_id).skill_id == retried.skill_id


@pytest.mark.asyncio
async def test_skill_stage_preflights_package_and_workspace_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert SKILL_STAGE_MAX_FILES == 500
    assert SKILL_STAGE_MAX_FILE_BYTES == 10 * 1024 * 1024
    assert SKILL_STAGE_MAX_TOTAL_BYTES == 50 * 1024 * 1024
    package = tmp_path / "package"
    package.mkdir()
    (package / "SKILL.md").write_text("# Local\n", encoding="utf-8")
    (package / "asset.bin").write_bytes(b"x" * 12)
    workspace_root = tmp_path / "workspaces"
    workspace = SandboxWorkspace(
        workspace_id="ws_local",
        scope_type="workflow",
        scope_id="task:node",
        node_id="node",
        quota_bytes=1024,
    )
    client = SimpleNamespace(request=AsyncMock(return_value={"ok": True}))
    manager = SimpleNamespace(
        list_installed_skills=lambda: [SimpleNamespace(skill_id="local-stage")],
        get_skill_directory=lambda _skill_id: package,
        require_activation=lambda *_args, **_kwargs: None,
    )
    provider = SandboxToolsetProvider(
        SimpleNamespace(workspace_root=workspace_root),
        client,
        skill_manager=manager,
    )
    call = RuntimeToolCall(
        "skill_stage",
        {"skill_id": "local-stage"},
        metadata={
            "task_id": "task",
            "node_id": "node",
            "skills_config": {"skill_ids": ["local-stage"]},
        },
    )

    monkeypatch.setattr(
        "xpert_runtime.sandbox_toolset.SKILL_STAGE_MAX_FILE_BYTES", 10
    )
    with pytest.raises(RuntimeToolError) as too_large:
        await provider._skill_stage(workspace, call)
    assert too_large.value.code == "skill_runtime_incompatible"
    client.request.assert_not_awaited()

    monkeypatch.setattr(
        "xpert_runtime.sandbox_toolset.SKILL_STAGE_MAX_FILE_BYTES", 20
    )
    workspace.quota_bytes = 15
    with pytest.raises(RuntimeToolError) as quota:
        await provider._skill_stage(workspace, call)
    assert quota.value.code == "skill_runtime_incompatible"
    client.request.assert_not_awaited()

    workspace.quota_bytes = 1024
    result = await provider._skill_stage(workspace, call)
    assert result.metadata["file_count"] == 2
    assert client.request.await_count == 2
