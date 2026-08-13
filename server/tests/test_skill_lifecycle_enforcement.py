from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import hashlib

import pytest

from server.skills.draft_store import WorkspaceSkillDraftStore
from server.skills.local_import import SkillLocalImportStore
from server.skills.package_validation import compute_skill_content_digest
from server.skills.lifecycle import (
    SkillLifecycleConflictError,
    SkillLifecycleStore,
)
from server.skills.skill_manager import (
    InstalledSkill,
    SkillManager,
    SkillNotFoundError,
    SkillValidationError,
)
from server.skills.trust_scanner import SkillTrustTreeEntry, scan_skill_trust_receipt
from server.skills.trust_service import SkillRuntimeEnvironment
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


def _markdown(version: str) -> str:
    return f"""---
name: lifecycle-runtime
description: Exercise immutable Skill lifecycle versions. Use for lifecycle tests.
---

# Lifecycle runtime

## Workflow

Return the exact marker `{version}`.
"""


def _install_current(
    draft_store: WorkspaceSkillDraftStore,
    manager: SkillManager,
    draft_id: str,
) -> tuple[object, object]:
    draft = draft_store.require(draft_id)

    def installer(item):
        return manager.install_workspace_draft(
            draft_id=item.draft_id,
            slug=item.slug,
            skill_markdown=item.skill_markdown,
            files=item.files,
            source_revision=item.content_revision,
            quality_required=item.quality_required,
            quality_status=item.quality_status,
        )

    updated, installed = draft_store.install_current(
        draft_id,
        expected_revision=draft.revision,
        expected_digest=draft.content_digest,
        installer=installer,
    )
    manager.finalize_lifecycle_transaction(installed.skill_id)
    return updated, installed


def _setup(tmp_path: Path):
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    drafts = WorkspaceSkillDraftStore(tmp_path / "drafts")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    draft = drafts.create(
        name="lifecycle-runtime",
        slug="lifecycle-runtime",
        description="Exercise immutable Skill lifecycle versions.",
        skill_markdown=_markdown("version-one"),
        files={"references/version.txt": "version-one\n"},
    )
    return lifecycle, drafts, manager, draft


def _git_receipt(files: dict[str, bytes]) -> dict:
    entries = [
        SkillTrustTreeEntry(
            path=path,
            mode="100644",
            object_type="blob",
            object_id=hashlib.sha1(content).hexdigest(),
            size=len(content),
            content=content,
        )
        for path, content in files.items()
    ]
    return scan_skill_trust_receipt(
        repo_url="https://github.com/example/lifecycle.git",
        sub_path="lifecycle-runtime",
        verified_commit="a" * 40,
        directory_tree_sha="b" * 40,
        entries=entries,
    )


def _git_installed(receipt: dict) -> InstalledSkill:
    return InstalledSkill(
        skill_id="lifecycle-runtime",
        name="lifecycle-runtime",
        description="Exercise immutable Skill lifecycle versions.",
        repo_url="https://github.com/example/lifecycle.git",
        sub_path="lifecycle-runtime",
        installed_at=1.0,
        source_ref="a" * 40,
        source_kind="git",
        content_digest=receipt["packageDigest"],
        trust_state="receipt_matched",
        trust_receipt_id=receipt["receiptId"],
        trust_fingerprint=receipt["trustFingerprint"],
        trust_risk_level=receipt["riskLevel"],
        trust_status=receipt["trustStatus"],
        trust_install_policy=receipt["installPolicy"],
        trust_compatibility_status=receipt["compatibilityStatus"],
        trust_router_eligible=receipt["routerEligible"],
        trust_package_digest=receipt["packageDigest"],
        trust_directory_tree_sha=receipt["directoryTreeSha"],
    )


def _materialize(manager: SkillManager, installed: InstalledSkill, files: dict[str, bytes]) -> None:
    target = manager.installed_dir / installed.skill_id
    target.mkdir(parents=True)
    for path, content in files.items():
        file_path = target.joinpath(*path.split("/"))
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
    manager._write_metadata({installed.skill_id: asdict(installed)})


def test_workspace_replacement_archives_versions_and_preserves_bound_reads(
    tmp_path: Path,
) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    first_state = lifecycle.require_state(installed.skill_id)
    first_version = first_state.current_version_id or ""

    current = drafts.require(draft.draft_id)
    drafts.update(
        draft.draft_id,
        expected_revision=current.revision,
        expected_digest=current.content_digest,
        skill_markdown=_markdown("version-two"),
        files={"references/version.txt": "version-two\n"},
    )
    _, installed_v2 = _install_current(drafts, manager, draft.draft_id)
    second_state = lifecycle.require_state(installed.skill_id)
    second_version = second_state.current_version_id or ""

    assert first_version != second_version
    assert second_state.version_ids == (first_version, second_version)
    assert "version-two" in manager.get_skill_content(installed.skill_id)
    assert "version-one" in manager.get_skill_content(
        installed.skill_id, version_id=first_version
    )
    assert manager.bind_skill_versions({installed.skill_id}) == {
        installed.skill_id: second_version
    }
    assert installed_v2.content_digest == lifecycle.require_version(
        second_version
    ).package_digest


def test_uninstall_keeps_recovery_package_and_old_binding_readable(
    tmp_path: Path,
) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    version_id = lifecycle.require_state(installed.skill_id).current_version_id or ""

    manager.uninstall_skill(installed.skill_id)
    drafts.mark_uninstalled_skill(installed.skill_id)
    manager.finalize_lifecycle_transaction(installed.skill_id)

    state = lifecycle.require_state(installed.skill_id)
    assert state.status == "uninstalled"
    assert state.current_version_id is None
    assert state.recovery_version_id == version_id
    with pytest.raises(SkillNotFoundError):
        manager.get_skill_content(installed.skill_id)
    assert "version-one" in manager.get_skill_content(
        installed.skill_id, version_id=version_id
    )


def test_rollback_requires_exact_state_and_switches_after_source_projection(
    tmp_path: Path,
) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    first_version = lifecycle.require_state(installed.skill_id).current_version_id or ""

    current = drafts.require(draft.draft_id)
    drafts.update(
        draft.draft_id,
        expected_revision=current.revision,
        expected_digest=current.content_digest,
        skill_markdown=_markdown("version-two"),
        files={"references/version.txt": "version-two\n"},
    )
    _install_current(drafts, manager, draft.draft_id)
    before = lifecycle.require_state(installed.skill_id)
    target = lifecycle.require_version(first_version)

    rolled_back = manager.rollback_skill_version(
        installed.skill_id,
        first_version,
        expected_state_revision=before.revision,
        expected_current_version_id=before.current_version_id,
        expected_package_digest=target.package_digest,
        confirmed=True,
    )
    drafts.mark_lifecycle_version_installed(
        draft.draft_id,
        content_revision=target.source_revision or 0,
        content_digest=target.package_digest,
        skill_id=installed.skill_id,
    )
    manager.finalize_lifecycle_transaction(installed.skill_id)

    after = lifecycle.require_state(installed.skill_id)
    assert after.current_version_id == first_version
    assert after.recovery_version_id == before.current_version_id
    assert "version-one" in manager.get_skill_content(installed.skill_id)
    assert rolled_back.content_digest == target.package_digest
    assert drafts.require(draft.draft_id).install_state == "outdated"


def test_rollback_retry_reuses_transaction_and_source_projection(tmp_path: Path) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    first_version = lifecycle.require_state(installed.skill_id).current_version_id or ""

    current = drafts.require(draft.draft_id)
    drafts.update(
        draft.draft_id,
        expected_revision=current.revision,
        expected_digest=current.content_digest,
        skill_markdown=_markdown("version-two"),
        files={"references/version.txt": "version-two\n"},
    )
    _install_current(drafts, manager, draft.draft_id)
    before = lifecycle.require_state(installed.skill_id)
    target = lifecycle.require_version(first_version)

    first_result = manager.rollback_skill_version(
        installed.skill_id,
        first_version,
        expected_state_revision=before.revision,
        expected_current_version_id=before.current_version_id,
        expected_package_digest=target.package_digest,
        confirmed=True,
    )
    first_projection = drafts.mark_lifecycle_version_installed(
        draft.draft_id,
        content_revision=target.source_revision or 0,
        content_digest=target.package_digest,
        skill_id=installed.skill_id,
    )
    retry_result = manager.rollback_skill_version(
        installed.skill_id,
        first_version,
        expected_state_revision=before.revision,
        expected_current_version_id=before.current_version_id,
        expected_package_digest=target.package_digest,
        confirmed=True,
    )
    retry_projection = drafts.mark_lifecycle_version_installed(
        draft.draft_id,
        content_revision=target.source_revision or 0,
        content_digest=target.package_digest,
        skill_id=installed.skill_id,
    )

    assert retry_result.content_digest == first_result.content_digest
    assert retry_projection.revision == first_projection.revision
    assert lifecycle.status()["pendingTransactions"] == 1
    manager.finalize_lifecycle_transaction(installed.skill_id)
    assert lifecycle.require_state(installed.skill_id).current_version_id == first_version


def test_creator_history_without_frozen_quality_evidence_cannot_rollback(
    tmp_path: Path,
) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    current_version = lifecycle.require_state(installed.skill_id).current_version_id or ""
    current = lifecycle.require_version(current_version)
    creator_version = lifecycle.stage_version(
        installed=InstalledSkill(
            **{
                **installed.__dict__,
                "source_revision": (current.source_revision or 0) + 1,
            }
        ),
        files=lifecycle.read_directory(lifecycle.package_directory(current_version)),
        quality_required=True,
        quality_evidence_status="legacy_unavailable",
    )
    state = lifecycle.require_state(installed.skill_id)

    with pytest.raises(SkillValidationError) as error:
        manager.rollback_skill_version(
            installed.skill_id,
            creator_version.version_id,
            expected_state_revision=state.revision,
            expected_current_version_id=state.current_version_id,
            expected_package_digest=creator_version.package_digest,
            confirmed=True,
        )
    assert error.value.code == "skill_lifecycle_quality_unavailable"


def test_local_import_lifecycle_projection_retry_is_idempotent(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [("SKILL.md", _markdown("local-import-version").encode())]
    )

    first = store.mark_lifecycle_version_installed(
        record.import_id,
        content_revision=record.content_revision,
        package_digest=record.package_digest or "",
        skill_id="lifecycle-runtime",
    )
    retry = store.mark_lifecycle_version_installed(
        record.import_id,
        content_revision=record.content_revision,
        package_digest=record.package_digest or "",
        skill_id="lifecycle-runtime",
    )

    assert retry.revision == first.revision
    assert retry.state == "installed"


def test_uninstall_preserves_creator_quality_evidence(tmp_path: Path) -> None:
    files = {
        "SKILL.md": _markdown("creator-accepted").encode(),
        "references/version.txt": b"creator-accepted\n",
    }
    digest = compute_skill_content_digest(files)
    installed = InstalledSkill(
        skill_id="lifecycle-runtime",
        name="lifecycle-runtime",
        description="Exercise immutable Skill lifecycle versions.",
        repo_url="workspace://draft/draft-creator",
        sub_path="",
        installed_at=1.0,
        source_kind="workspace_draft",
        source_id="draft-creator",
        source_revision=3,
        content_digest=digest,
    )
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    state = lifecycle.record_migrated_current(
        installed=installed,
        files=files,
        quality_required=True,
        quality_evidence_status="matched",
        quality_status="accepted",
        quality_decision_id="quality-decision-1",
        quality_run_id="quality-run-1",
    )
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    target = manager.installed_dir / installed.skill_id
    target.mkdir(parents=True)
    for path, content in files.items():
        file_path = target.joinpath(*path.split("/"))
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
    manager._write_metadata({installed.skill_id: asdict(installed)})

    manager.uninstall_skill(installed.skill_id)

    transaction = lifecycle.require_transaction(installed.skill_id)
    assert transaction.previous_version_id == state.current_version_id
    version = lifecycle.require_version(transaction.previous_version_id or "")
    assert version.quality_required is True
    assert version.quality_evidence_status == "matched"
    assert version.quality_status == "accepted"
    manager.finalize_lifecycle_transaction(installed.skill_id)


def test_incomplete_transaction_blocks_new_runtime_binding(tmp_path: Path) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    state = lifecycle.require_state(installed.skill_id)
    receipt = lifecycle.prepare_transaction(
        skill_id=installed.skill_id,
        operation="uninstall",
        previous_version_id=state.current_version_id,
        target_version_id=None,
        expected_state_revision=state.revision,
    )
    lifecycle.advance_transaction(
        installed.skill_id,
        transaction_id=receipt.transaction_id,
        expected_phase="prepared",
        phase="archived",
    )

    with pytest.raises(SkillLifecycleConflictError) as error:
        lifecycle.bind_current_versions({installed.skill_id})
    assert error.value.code == "skill_lifecycle_transaction_incomplete"

    lifecycle.abort_transaction(
        installed.skill_id, transaction_id=receipt.transaction_id
    )
    assert lifecycle.bind_current_versions({installed.skill_id}) == {
        installed.skill_id: state.current_version_id
    }


def test_rollback_conflict_does_not_change_installed_bytes(tmp_path: Path) -> None:
    lifecycle, drafts, manager, draft = _setup(tmp_path)
    _, installed = _install_current(drafts, manager, draft.draft_id)
    state = lifecycle.require_state(installed.skill_id)
    version = lifecycle.require_version(state.current_version_id or "")

    with pytest.raises(Exception) as error:
        manager.rollback_skill_version(
            installed.skill_id,
            version.version_id,
            expected_state_revision=state.revision + 1,
            expected_current_version_id=state.current_version_id,
            expected_package_digest=version.package_digest,
            confirmed=True,
        )
    assert getattr(error.value, "code", None) == "skill_lifecycle_version_conflict"
    assert "version-one" in manager.get_skill_content(installed.skill_id)


def test_execution_store_persists_immutable_skill_bindings(tmp_path: Path) -> None:
    root = tmp_path / "executions"
    store = WorkflowExecutionStore(root)
    store.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        workflow={"id": "workflow-1"},
        inputs={},
        runtime_metadata={},
    )
    store.bind_skill_versions(
        "task-1", bindings={"lifecycle-runtime": "skillver_123"}
    )

    reloaded = WorkflowExecutionStore(root)
    assert reloaded.require("task-1").runtime_metadata[
        "skill_version_bindings"
    ] == {"lifecycle-runtime": "skillver_123"}
    with pytest.raises(WorkflowExecutionConflictError):
        reloaded.bind_skill_versions(
            "task-1", bindings={"lifecycle-runtime": "skillver_456"}
        )


def test_historical_script_receipt_rechecks_runtime_capabilities(
    tmp_path: Path,
) -> None:
    files = {
        "SKILL.md": _markdown("scripted").encode(),
        "scripts/run.py": b"print('ok')\n",
    }
    receipt = _git_receipt(files)
    installed = _git_installed(receipt)
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    manager.trust_service.receipt_by_fingerprint = (  # type: ignore[method-assign]
        lambda _fingerprint: receipt
    )
    manager.trust_service.acknowledge(
        skill_id=installed.skill_id,
        trust_fingerprint=installed.trust_fingerprint or "",
        confirmed=True,
    )
    state = lifecycle.record_migrated_current(
        installed=installed,
        files=files,
        trust_receipt_snapshot=receipt,
    )
    version_id = state.current_version_id or ""

    with pytest.raises(SkillValidationError) as error:
        manager.require_activation(
            installed.skill_id,
            version_id=version_id,
            runtime_environment=SkillRuntimeEnvironment(),
        )
    assert error.value.code == "skill_runtime_incompatible"

    allowed = manager.require_activation(
        installed.skill_id,
        version_id=version_id,
        runtime_environment=SkillRuntimeEnvironment(
            tool_names=frozenset({"skill_stage", "sandbox_shell"}),
            sandbox_commands=frozenset({"python"}),
        ),
    )
    assert allowed.content_digest == receipt["packageDigest"]


def test_git_metadata_committed_uninstall_recovers_without_source_store(
    tmp_path: Path,
) -> None:
    files = {"SKILL.md": _markdown("git-version").encode()}
    receipt = _git_receipt(files)
    installed = _git_installed(receipt)
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    state = lifecycle.record_migrated_current(
        installed=installed,
        files=files,
        trust_receipt_snapshot=receipt,
    )
    version_id = state.current_version_id or ""
    transaction = lifecycle.prepare_transaction(
        skill_id=installed.skill_id,
        operation="uninstall",
        previous_version_id=version_id,
        target_version_id=None,
        expected_state_revision=state.revision,
    )
    transaction = lifecycle.advance_transaction(
        installed.skill_id,
        transaction_id=transaction.transaction_id,
        expected_phase="prepared",
        phase="archived",
    )
    transaction = lifecycle.advance_transaction(
        installed.skill_id,
        transaction_id=transaction.transaction_id,
        expected_phase="archived",
        phase="swapped",
    )
    lifecycle.advance_transaction(
        installed.skill_id,
        transaction_id=transaction.transaction_id,
        expected_phase="swapped",
        phase="metadata_committed",
    )

    assert manager.recover_lifecycle_transaction(installed.skill_id) is True
    recovered = lifecycle.require_state(installed.skill_id)
    assert recovered.status == "uninstalled"
    assert recovered.current_version_id is None
    assert recovered.recovery_version_id == version_id


def test_git_swapped_replacement_recovers_forward_from_immutable_target(
    tmp_path: Path,
) -> None:
    first_files = {"SKILL.md": _markdown("git-one").encode()}
    second_files = {"SKILL.md": _markdown("git-two").encode()}
    first_receipt = _git_receipt(first_files)
    second_receipt = _git_receipt(second_files)
    first_installed = _git_installed(first_receipt)
    second_installed = _git_installed(second_receipt)
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    first_state = lifecycle.record_migrated_current(
        installed=first_installed,
        files=first_files,
        trust_receipt_snapshot=first_receipt,
    )
    target = lifecycle.stage_version(
        installed=second_installed,
        files=second_files,
        trust_receipt_snapshot=second_receipt,
    )
    _materialize(manager, first_installed, first_files)
    transaction = lifecycle.prepare_transaction(
        skill_id=first_installed.skill_id,
        operation="replace",
        previous_version_id=first_state.current_version_id,
        target_version_id=target.version_id,
        expected_state_revision=lifecycle.require_state(first_installed.skill_id).revision,
    )
    transaction = lifecycle.advance_transaction(
        first_installed.skill_id,
        transaction_id=transaction.transaction_id,
        expected_phase="prepared",
        phase="archived",
    )
    lifecycle.advance_transaction(
        first_installed.skill_id,
        transaction_id=transaction.transaction_id,
        expected_phase="archived",
        phase="swapped",
    )

    assert manager.recover_lifecycle_transaction(first_installed.skill_id) is True

    assert "git-two" in manager.get_skill_content(first_installed.skill_id)
    recovered = lifecycle.require_state(first_installed.skill_id)
    assert recovered.current_version_id == target.version_id
    assert lifecycle.status()["pendingTransactions"] == 0


def test_git_archived_transaction_aborts_when_previous_bytes_are_intact(
    tmp_path: Path,
) -> None:
    files = {"SKILL.md": _markdown("git-intact").encode()}
    receipt = _git_receipt(files)
    installed = _git_installed(receipt)
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    state = lifecycle.record_migrated_current(
        installed=installed,
        files=files,
        trust_receipt_snapshot=receipt,
    )
    _materialize(manager, installed, files)
    transaction = lifecycle.prepare_transaction(
        skill_id=installed.skill_id,
        operation="uninstall",
        previous_version_id=state.current_version_id,
        target_version_id=None,
        expected_state_revision=state.revision,
    )
    lifecycle.advance_transaction(
        installed.skill_id,
        transaction_id=transaction.transaction_id,
        expected_phase="prepared",
        phase="archived",
    )

    assert manager.recover_lifecycle_transaction(installed.skill_id) is True
    assert "git-intact" in manager.get_skill_content(installed.skill_id)
    assert lifecycle.status()["pendingTransactions"] == 0


def test_workspace_receipt_recovery_advances_the_same_lifecycle_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    first = manager.install_workspace_draft(
        draft_id="draft-recovery",
        slug="lifecycle-runtime",
        skill_markdown=_markdown("workspace-one"),
        files={"references/version.txt": "workspace-one\n"},
        source_revision=1,
    )
    manager.finalize_lifecycle_transaction(first.skill_id)
    original_write_metadata = manager._write_metadata

    def interrupt_metadata(_skills: dict[str, dict[str, object]]) -> None:
        raise KeyboardInterrupt("simulated lifecycle interruption")

    monkeypatch.setattr(manager, "_write_metadata", interrupt_metadata)
    with pytest.raises(KeyboardInterrupt, match="simulated lifecycle interruption"):
        manager.install_workspace_draft(
            draft_id="draft-recovery",
            slug="lifecycle-runtime",
            skill_markdown=_markdown("workspace-two"),
            files={"references/version.txt": "workspace-two\n"},
            source_revision=2,
        )
    monkeypatch.setattr(manager, "_write_metadata", original_write_metadata)
    assert lifecycle.require_transaction(first.skill_id).phase == "swapped"

    recovered_manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    recovered = recovered_manager.install_workspace_draft(
        draft_id="draft-recovery",
        slug="lifecycle-runtime",
        skill_markdown=_markdown("workspace-two"),
        files={"references/version.txt": "workspace-two\n"},
        source_revision=2,
    )

    assert recovered.content_digest != first.content_digest
    assert lifecycle.require_transaction(first.skill_id).phase == "metadata_committed"
    recovered_manager.finalize_lifecycle_transaction(first.skill_id)
    current = lifecycle.require_version(
        lifecycle.require_state(first.skill_id).current_version_id or ""
    )
    assert current.package_digest == recovered.content_digest
    assert lifecycle.status()["pendingTransactions"] == 0
