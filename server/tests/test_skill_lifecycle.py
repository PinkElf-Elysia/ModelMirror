from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from server.skills.draft_store import WorkspaceSkillDraftStore
from server.skills.lifecycle import (
    SkillLifecycleDisabledError,
    SkillLifecycleMigrationService,
    SkillLifecycleStorageError,
    SkillLifecycleStore,
)
from server.skills.local_import import SkillLocalImportStore
from server.skills.package_validation import compute_package_digest
from server.skills.skill_manager import InstalledSkill, SkillInstallError, SkillManager
from server.skills.trust_scanner import sha256_json


SKILL_MARKDOWN = b"""---
name: lifecycle-sample
description: Store one deterministic lifecycle sample. Use when testing history.
---

# Lifecycle sample

Read `references/guide.md` and return the requested result.
"""


def _package(*, crlf: bool = False) -> dict[str, bytes]:
    markdown = SKILL_MARKDOWN.replace(b"\n", b"\r\n") if crlf else SKILL_MARKDOWN
    return {
        "SKILL.md": markdown,
        "references/guide.md": b"stable reference\n",
    }


def _digest(files: dict[str, bytes]) -> str:
    return compute_package_digest(
        files["SKILL.md"],
        {path: content for path, content in files.items() if path != "SKILL.md"},
    )


def _installed(
    *,
    source_kind: str = "git",
    source_id: str | None = None,
    source_revision: int | None = None,
    digest: str,
) -> InstalledSkill:
    return InstalledSkill(
        skill_id="lifecycle-sample",
        name="lifecycle-sample",
        description="Store one deterministic lifecycle sample.",
        repo_url=(
            "https://github.com/example/skills.git"
            if source_kind == "git"
            else f"{source_kind}://{source_id}"
        ),
        sub_path="skills/lifecycle-sample" if source_kind == "git" else "",
        installed_at=1.0,
        source_ref="a" * 40 if source_kind == "git" else None,
        source_kind=source_kind,
        source_id=source_id,
        source_revision=source_revision,
        content_digest=digest,
        trust_state="receipt_matched" if source_kind == "git" else "not_applicable",
        trust_receipt_id="receipt-1" if source_kind == "git" else None,
        trust_fingerprint="b" * 64 if source_kind == "git" else None,
        trust_risk_level="low" if source_kind == "git" else None,
        trust_status="verified" if source_kind == "git" else None,
        trust_install_policy="allow" if source_kind == "git" else None,
        trust_compatibility_status="portable" if source_kind == "git" else None,
        trust_router_eligible=source_kind == "git",
        trust_package_digest=digest if source_kind == "git" else None,
        trust_directory_tree_sha="c" * 40 if source_kind == "git" else None,
    )


def _materialize_installed(
    manager: SkillManager,
    installed: InstalledSkill,
    files: dict[str, bytes],
) -> None:
    package_dir = manager.installed_dir / installed.skill_id
    package_dir.mkdir(parents=True)
    for path, content in files.items():
        target = package_dir.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manager._write_metadata({installed.skill_id: asdict(installed)})


def _publish_test_git_receipt(
    manager: SkillManager, installed: InstalledSkill
) -> None:
    receipt_payload = {
        "receiptId": installed.trust_receipt_id,
        "packageDigest": installed.trust_package_digest,
        "directoryTreeSha": installed.trust_directory_tree_sha,
        "source": {
            "repoUrl": installed.repo_url,
            "subPath": installed.sub_path,
            "verifiedCommit": installed.source_ref,
        },
    }
    receipt = {
        **receipt_payload,
        "trustFingerprint": sha256_json(receipt_payload),
    }
    metadata = manager._read_metadata()
    metadata[installed.skill_id]["trust_fingerprint"] = receipt["trustFingerprint"]
    manager._write_metadata(metadata)
    manager.trust_service.receipt_by_id = lambda _receipt_id: receipt  # type: ignore[method-assign]


def test_store_archives_exact_bytes_deduplicates_and_is_idempotent(tmp_path: Path) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)

    first = store.record_migrated_current(installed=installed, files=files)
    second = store.record_migrated_current(installed=installed, files=files)

    assert first == second
    assert first.revision == 1
    assert len(first.version_ids) == 1
    version = store.require_version(first.current_version_id or "")
    assert version.package_digest == installed.content_digest
    assert version.file_count == 2
    assert (store.packages_root / version.package_digest / "SKILL.md").read_bytes() == SKILL_MARKDOWN
    assert store.status()["counts"] == {
        "skills": 1,
        "versions": 1,
        "packages": 1,
        "quarantinedRecords": 0,
        "migrationBlocked": 0,
    }
    reloaded = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    assert reloaded.require_state(installed.skill_id) == first
    assert reloaded.require_version(version.version_id) == version


def test_trust_receipt_change_creates_a_new_version_without_copying_bytes(
    tmp_path: Path,
) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    first = store.record_migrated_current(installed=installed, files=files)
    updated_receipt = InstalledSkill(
        **{
            **asdict(installed),
            "trust_receipt_id": "receipt-2",
            "trust_fingerprint": "d" * 64,
        }
    )

    second = store.record_migrated_current(installed=updated_receipt, files=files)

    assert first.current_version_id != second.current_version_id
    assert len(second.version_ids) == 2
    assert store.status()["counts"]["packages"] == 1
    assert (
        store.require_version(second.current_version_id or "").trust_fingerprint
        == "d" * 64
    )
    reloaded = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    assert reloaded.require_state(installed.skill_id) == second


def test_store_still_loads_pr1_version_ids_without_evidence_fingerprint(
    tmp_path: Path,
) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    root = tmp_path / "lifecycle"
    store = SkillLifecycleStore(root, enabled=True)
    state = store.record_migrated_current(installed=installed, files=files)
    payload = json.loads(store.index_path.read_text(encoding="utf-8"))
    legacy_identity = {
        "skillId": installed.skill_id,
        "sourceKind": installed.source_kind,
        "sourceId": installed.source_id,
        "sourceRevision": installed.source_revision,
        "sourceRef": installed.source_ref,
        "packageDigest": installed.content_digest,
    }
    legacy_version_id = "skillver_" + sha256_json(legacy_identity)[:32]
    current_version_id = state.current_version_id or ""
    payload["versions"][0]["version_id"] = legacy_version_id
    for raw_state in payload["states"]:
        raw_state["version_ids"] = [
            legacy_version_id if item == current_version_id else item
            for item in raw_state["version_ids"]
        ]
        if raw_state.get("current_version_id") == current_version_id:
            raw_state["current_version_id"] = legacy_version_id
        for event in raw_state.get("events", []):
            if event.get("version_id") == current_version_id:
                event["version_id"] = legacy_version_id
    store.index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    reloaded = SkillLifecycleStore(root, enabled=True)

    assert reloaded.require_state(installed.skill_id).current_version_id == legacy_version_id
    assert reloaded.require_version(legacy_version_id).package_digest == installed.content_digest
    assert reloaded.status()["counts"]["quarantinedRecords"] == 0
    replay = reloaded.record_migrated_current(installed=installed, files=files)
    assert replay.current_version_id == legacy_version_id
    assert len(replay.version_ids) == 1


def test_store_preserves_raw_line_endings_and_detects_package_tampering(tmp_path: Path) -> None:
    lf_files = _package()
    crlf_files = _package(crlf=True)
    assert _digest(lf_files) != _digest(crlf_files)
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    state = store.record_migrated_current(
        installed=_installed(digest=_digest(crlf_files)),
        files=crlf_files,
    )
    version = store.require_version(state.current_version_id or "")
    package_file = store.packages_root / version.package_digest / "SKILL.md"
    assert b"\r\n" in package_file.read_bytes()

    package_file.write_bytes(package_file.read_bytes() + b"tampered")
    with pytest.raises(SkillLifecycleStorageError) as error:
        store.require_version(version.version_id)
    assert error.value.code == "skill_lifecycle_package_mismatch"


def test_content_addressed_package_is_shared_across_skill_states(tmp_path: Path) -> None:
    files = _package()
    digest = _digest(files)
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    first = _installed(digest=digest)
    second = InstalledSkill(
        **{
            **asdict(first),
            "skill_id": "lifecycle-sample-copy",
            "source_ref": "d" * 40,
        }
    )

    store.record_migrated_current(installed=first, files=files)
    store.record_migrated_current(installed=second, files=files)
    assert store.status()["counts"] == {
        "skills": 2,
        "versions": 2,
        "packages": 1,
        "quarantinedRecords": 0,
        "migrationBlocked": 0,
    }
    assert [path.name for path in store.packages_root.iterdir()] == [digest]


def test_top_level_corruption_fails_closed_without_overwriting(tmp_path: Path) -> None:
    root = tmp_path / "lifecycle"
    root.mkdir()
    index = root / "skill_lifecycle.json"
    index.write_text("{not-json", encoding="utf-8")
    store = SkillLifecycleStore(root, enabled=True)

    assert store.status()["available"] is False
    with pytest.raises(SkillLifecycleStorageError):
        store.record_migration_blocked(
            "lifecycle-sample", reason_code="skill_lifecycle_source_unverified"
        )
    assert index.read_text(encoding="utf-8") == "{not-json"


def test_invalid_record_is_quarantined_without_rewriting_secret(tmp_path: Path) -> None:
    root = tmp_path / "lifecycle"
    root.mkdir()
    index = root / "skill_lifecycle.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "states": [{"skill_id": "sk-" + "secret-value", "private": "hidden"}],
                "versions": [],
                "quarantine": [],
            }
        ),
        encoding="utf-8",
    )
    store = SkillLifecycleStore(root, enabled=True)
    assert store.status()["counts"]["quarantinedRecords"] == 1

    store.record_migration_blocked(
        "lifecycle-sample", reason_code="skill_lifecycle_source_unverified"
    )
    rewritten = index.read_text(encoding="utf-8")
    assert "secret-value" not in rewritten
    payload = json.loads(rewritten)
    assert set(payload["quarantine"][0]) == {"kind", "index", "sha256", "sizeBytes"}


def test_disabled_store_allows_status_but_rejects_mutations(tmp_path: Path) -> None:
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=False)
    assert store.status()["enabled"] is False
    with pytest.raises(SkillLifecycleDisabledError):
        store.record_migration_blocked(
            "lifecycle-sample", reason_code="skill_lifecycle_source_unverified"
        )


def test_invalid_limit_environment_uses_safe_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_LIFECYCLE_MAX_VERSIONS", "not-a-number")
    monkeypatch.setenv("SKILL_LIFECYCLE_MAX_BYTES", "not-a-number")
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=False)
    assert store.status()["limits"] == {
        "nonCurrentVersionsPerSkill": 5,
        "storageBytes": 1024 * 1024 * 1024,
        "fileCount": 500,
        "fileBytes": 10 * 1024 * 1024,
        "packageBytes": 50 * 1024 * 1024,
    }


def test_retention_keeps_current_plus_configured_non_current_versions(
    tmp_path: Path,
) -> None:
    store = SkillLifecycleStore(
        tmp_path / "lifecycle", enabled=True, max_versions=1
    )
    versions: list[str] = []
    for index in range(2):
        files = _package()
        files["references/guide.md"] = f"version {index}\n".encode()
        installed = _installed(digest=_digest(files))
        installed = InstalledSkill(
            **{**asdict(installed), "source_ref": f"{index + 1:040x}"}
        )
        state = store.record_migrated_current(installed=installed, files=files)
        versions.append(state.current_version_id or "")
    assert store.require_state("lifecycle-sample").version_ids == tuple(versions)

    third = _package()
    third["references/guide.md"] = b"version 3\n"
    installed = _installed(digest=_digest(third))
    installed = InstalledSkill(**{**asdict(installed), "source_ref": "f" * 40})
    with pytest.raises(SkillLifecycleStorageError) as error:
        store.record_migrated_current(installed=installed, files=third)
    assert error.value.code == "skill_lifecycle_retention_full"
    assert store.require_state("lifecycle-sample").current_version_id == versions[-1]
    assert not (store.packages_root / installed.content_digest).exists()


def test_save_failure_does_not_publish_memory_or_orphan_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)

    def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save_unlocked", fail_save)
    with pytest.raises(OSError, match="disk full"):
        store.record_migrated_current(installed=installed, files=files)
    assert store.status()["counts"]["versions"] == 0
    assert not store.packages_root.exists() or not list(store.packages_root.iterdir())


def test_migration_audits_and_archives_exact_git_install(tmp_path: Path) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    _materialize_installed(manager, installed, files)
    _publish_test_git_receipt(manager, installed)
    metadata_before = manager.metadata_path.read_bytes()
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    service = SkillLifecycleMigrationService(store=store, manager=manager)

    audit = service.audit()
    assert audit["counts"] == {
        "total": 1,
        "eligible": 1,
        "migrated": 0,
        "blocked": 0,
        "ignored": 0,
    }
    assert "_evidence" not in audit["items"][0]

    migrated = service.migrate(confirmed=True)
    assert migrated["counts"]["migrated"] == 1
    state = store.require_state(installed.skill_id)
    assert state.status == "active"
    assert manager.metadata_path.read_bytes() == metadata_before
    assert (
        manager.installed_dir / installed.skill_id / "SKILL.md"
    ).read_bytes() == files["SKILL.md"]


def test_migration_blocks_changed_package_without_altering_install(tmp_path: Path) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    _materialize_installed(manager, installed, files)
    target = manager.installed_dir / installed.skill_id / "references" / "guide.md"
    target.write_bytes(b"changed after installation\n")
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    service = SkillLifecycleMigrationService(store=store, manager=manager)

    result = service.migrate(confirmed=True)
    assert result["items"][0]["outcome"] == "blocked"
    assert result["items"][0]["code"] == "skill_lifecycle_package_mismatch"
    assert target.read_bytes() == b"changed after installation\n"
    assert store.require_state(installed.skill_id).status == "migration_blocked"


def test_migration_rejects_git_metadata_without_published_receipt(
    tmp_path: Path,
) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    _materialize_installed(manager, installed, files)
    service = SkillLifecycleMigrationService(
        store=SkillLifecycleStore(tmp_path / "lifecycle", enabled=True),
        manager=manager,
    )

    item = service.audit()["items"][0]
    assert item["outcome"] == "blocked"
    assert item["code"] == "skill_lifecycle_source_unverified"


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_migration_rejects_linked_installed_files(
    tmp_path: Path, link_kind: str
) -> None:
    files = _package()
    installed = _installed(digest=_digest(files))
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    _materialize_installed(manager, installed, files)
    target = manager.installed_dir / installed.skill_id / "references" / "guide.md"
    target.unlink()
    if link_kind == "symlink":
        os.symlink("../SKILL.md", target)
    else:
        os.link(manager.installed_dir / installed.skill_id / "SKILL.md", target)
    service = SkillLifecycleMigrationService(
        store=SkillLifecycleStore(tmp_path / "lifecycle", enabled=True),
        manager=manager,
    )

    item = service.audit()["items"][0]
    assert item["outcome"] == "blocked"
    assert item["code"] == "skill_lifecycle_package_invalid"


def test_migration_verifies_local_import_and_workspace_revision(tmp_path: Path) -> None:
    files = _package()
    digest = _digest(files)

    import_store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    imported = import_store.create_from_folder(list(files.items()))
    imported, _ = import_store.install_current(
        imported.import_id,
        expected_revision=imported.revision,
        expected_package_digest=imported.package_digest or "",
        expected_trust_fingerprint=imported.trust_fingerprint or "",
        installer=lambda _record, _path: None,
    )
    local_installed = _installed(
        source_kind="local_import",
        source_id=imported.import_id,
        source_revision=imported.content_revision,
        digest=digest,
    )
    local_installed = InstalledSkill(
        **{
            **asdict(local_installed),
            "repo_url": f"local-import://{imported.import_id}",
            "trust_receipt_id": imported.receipt_id,
            "trust_fingerprint": imported.trust_fingerprint,
        }
    )
    local_manager = SkillManager(
        installed_dir=tmp_path / "installed-local",
        tmp_dir=tmp_path / "tmp-local",
        local_import_store=import_store,
    )
    _materialize_installed(local_manager, local_installed, files)
    local_store = SkillLifecycleStore(tmp_path / "lifecycle-local", enabled=True)
    local_service = SkillLifecycleMigrationService(
        store=local_store,
        manager=local_manager,
        local_import_store=import_store,
    )
    assert local_service.migrate(confirmed=True)["counts"]["migrated"] == 1

    draft_store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    draft = draft_store.create(
        name="lifecycle-sample",
        slug="lifecycle-sample",
        description="Store one deterministic lifecycle sample.",
        skill_markdown=files["SKILL.md"].decode("utf-8"),
        files={"references/guide.md": files["references/guide.md"].decode("utf-8")},
    )
    draft = draft_store.mark_installed(
        draft.draft_id,
        expected_revision=draft.revision,
        expected_digest=draft.content_digest,
        skill_id="lifecycle-sample",
    )
    workspace_installed = _installed(
        source_kind="workspace_draft",
        source_id=draft.draft_id,
        source_revision=draft.content_revision,
        digest=digest,
    )
    workspace_manager = SkillManager(
        installed_dir=tmp_path / "installed-workspace",
        tmp_dir=tmp_path / "tmp-workspace",
    )
    _materialize_installed(workspace_manager, workspace_installed, files)
    workspace_lifecycle = SkillLifecycleStore(
        tmp_path / "lifecycle-workspace", enabled=True
    )
    workspace_service = SkillLifecycleMigrationService(
        store=workspace_lifecycle,
        manager=workspace_manager,
        draft_store=draft_store,
    )
    assert workspace_service.migrate(confirmed=True)["counts"]["migrated"] == 1
    version = workspace_lifecycle.require_version(
        workspace_lifecycle.require_state("lifecycle-sample").current_version_id or ""
    )
    assert version.quality_required is False
    assert version.quality_evidence_status == "not_applicable"


def test_migration_explicitly_ignores_plugin_source(tmp_path: Path) -> None:
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    plugin = InstalledSkill(
        skill_id="plugin-sample-v1-tool",
        name="plugin-tool",
        description="Plugin-owned Skill.",
        repo_url="plugin://sample/v1",
        sub_path="tool",
        installed_at=1.0,
        source_kind="plugin",
        source_id="sample",
        source_revision=1,
    )
    manager._write_metadata({plugin.skill_id: asdict(plugin)})
    service = SkillLifecycleMigrationService(
        store=SkillLifecycleStore(tmp_path / "lifecycle", enabled=True),
        manager=manager,
    )

    report = service.migrate(confirmed=True)
    assert report["counts"]["ignored"] == 1
    assert report["items"][0]["code"] == "skill_lifecycle_source_unsupported"
    assert service.store.status()["counts"]["skills"] == 0


def test_installed_metadata_corruption_is_not_treated_as_empty(tmp_path: Path) -> None:
    installed_dir = tmp_path / "installed"
    installed_dir.mkdir()
    metadata = installed_dir / "installed.json"
    metadata.write_text("{not-json", encoding="utf-8")
    manager = SkillManager(installed_dir=installed_dir, tmp_dir=tmp_path / "tmp")

    with pytest.raises(SkillInstallError, match="unavailable or corrupt"):
        manager.list_installed_skills()
    assert metadata.read_text(encoding="utf-8") == "{not-json"
