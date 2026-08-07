from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.skills.package_validation import compute_package_digest
from server.skills.skill_manager import (
    SkillInstallError,
    SkillInstallReceipt,
    SkillManager,
)


def _skill_markdown(name: str = "safe-helper", version: str = "v1") -> str:
    return f"""---
name: {name}
description: A reviewed workspace helper.
---

# Safe Helper

Run the reviewed {version} workflow.
"""


def _manager(tmp_path: Path) -> SkillManager:
    return SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )


def test_workspace_install_is_stable_nested_idempotent_and_upgradeable(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    files_v1 = {"scripts/run.py": "print('v1')\n"}
    markdown_v1 = _skill_markdown()

    first = manager.install_workspace_draft(
        draft_id="skilldraft_stable",
        slug="safe-helper",
        skill_markdown=markdown_v1,
        files=files_v1,
        source_revision=1,
    )
    retried = manager.install_workspace_draft(
        draft_id="skilldraft_stable",
        slug="safe-helper",
        skill_markdown=markdown_v1,
        files=files_v1,
        source_revision=1,
    )

    assert retried == first
    assert first.source_kind == "workspace_draft"
    assert first.source_id == "skilldraft_stable"
    assert first.source_revision == 1
    assert first.content_digest == compute_package_digest(markdown_v1, files_v1)
    assert first.package_subpath == "safe-helper"
    assert manager.get_skill_directory(first.skill_id) == (
        tmp_path / "installed" / first.skill_id / "safe-helper"
    )
    assert manager.get_skill_content(first.skill_id) == markdown_v1

    files_v2 = {
        "scripts/run.py": "print('v2')\n",
        "references/guide.md": "# V2 guide\n",
    }
    markdown_v2 = _skill_markdown(name="renamed-helper", version="v2")
    upgraded = manager.install_workspace_draft(
        draft_id="skilldraft_stable",
        slug="renamed-helper",
        skill_markdown=markdown_v2,
        files=files_v2,
        source_revision=2,
    )

    assert upgraded.skill_id == first.skill_id
    assert upgraded.source_revision == 2
    assert upgraded.content_digest == compute_package_digest(markdown_v2, files_v2)
    assert upgraded.content_digest != first.content_digest
    assert upgraded.package_subpath == "renamed-helper"
    assert manager.get_skill_directory(first.skill_id) == (
        tmp_path / "installed" / first.skill_id / "renamed-helper"
    )
    assert not (tmp_path / "installed" / first.skill_id / "safe-helper").exists()
    assert manager.get_skill_content(first.skill_id) == markdown_v2
    assert not list((tmp_path / "installed").glob(".*.staging-*"))
    assert not list((tmp_path / "installed").glob(".*.backup-*"))
    assert not list((tmp_path / "installed").glob("*.receipt.json"))


def test_workspace_upgrade_failure_rolls_back_package_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    original = manager.install_workspace_draft(
        draft_id="skilldraft_rollback",
        slug="safe-helper",
        skill_markdown=_skill_markdown(),
        files={"scripts/run.py": "print('v1')\n"},
        source_revision=1,
    )
    original_content = manager.get_skill_content(original.skill_id)

    original_write_metadata = manager._write_metadata
    write_count = 0

    def fail_metadata_write(skills: dict[str, dict[str, object]]) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 1:
            raise OSError("simulated workspace metadata failure")
        original_write_metadata(skills)

    monkeypatch.setattr(manager, "_write_metadata", fail_metadata_write)
    with pytest.raises(OSError, match="simulated workspace metadata failure"):
        manager.install_workspace_draft(
            draft_id="skilldraft_rollback",
            slug="safe-helper",
            skill_markdown=_skill_markdown(version="v2"),
            files={"scripts/run.py": "print('v2')\n"},
            source_revision=2,
        )

    assert manager.get_skill_content(original.skill_id) == original_content
    assert manager.list_installed_skills() == [original]
    assert not list((tmp_path / "installed").glob(".*.staging-*"))
    assert not list((tmp_path / "installed").glob(".*.backup-*"))
    assert not list((tmp_path / "installed").glob("*.receipt.json"))


def test_workspace_exception_after_metadata_commit_restores_previous_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    original = manager.install_workspace_draft(
        draft_id="skilldraft_metadata_committed",
        slug="safe-helper",
        skill_markdown=_skill_markdown(),
        files={"scripts/run.py": "print('v1')\n"},
        source_revision=1,
    )
    original_receipt_write = manager._write_install_receipt

    def fail_committed_receipt(receipt: SkillInstallReceipt) -> None:
        if receipt.phase == "committed":
            raise OSError("simulated committed receipt failure")
        original_receipt_write(receipt)

    monkeypatch.setattr(manager, "_write_install_receipt", fail_committed_receipt)
    with pytest.raises(OSError, match="simulated committed receipt failure"):
        manager.install_workspace_draft(
            draft_id="skilldraft_metadata_committed",
            slug="safe-helper",
            skill_markdown=_skill_markdown(version="v2"),
            files={"scripts/run.py": "print('v2')\n"},
            source_revision=2,
        )

    assert manager.list_installed_skills() == [original]
    assert manager.get_skill_content(original.skill_id) == _skill_markdown()
    assert not list((tmp_path / "installed").glob("*.receipt.json"))


def test_workspace_rollback_failure_keeps_receipt_for_safe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    original = manager.install_workspace_draft(
        draft_id="skilldraft_rollback_retry",
        slug="safe-helper",
        skill_markdown=_skill_markdown(),
        files={"scripts/run.py": "print('v1')\n"},
        source_revision=1,
    )

    def fail_all_metadata_writes(_skills: dict[str, dict[str, object]]) -> None:
        raise OSError("simulated persistent metadata failure")

    monkeypatch.setattr(manager, "_write_metadata", fail_all_metadata_writes)
    markdown_v2 = _skill_markdown(version="v2")
    files_v2 = {"scripts/run.py": "print('v2')\n"}
    with pytest.raises(SkillInstallError, match="rollback is incomplete"):
        manager.install_workspace_draft(
            draft_id="skilldraft_rollback_retry",
            slug="safe-helper",
            skill_markdown=markdown_v2,
            files=files_v2,
            source_revision=2,
        )

    assert list((tmp_path / "installed").glob("*.receipt.json"))
    assert manager.get_skill_content(original.skill_id) == _skill_markdown()

    recovered_manager = _manager(tmp_path)
    recovered = recovered_manager.install_workspace_draft(
        draft_id="skilldraft_rollback_retry",
        slug="safe-helper",
        skill_markdown=markdown_v2,
        files=files_v2,
        source_revision=2,
    )
    assert recovered.skill_id == original.skill_id
    assert recovered.content_digest == compute_package_digest(markdown_v2, files_v2)
    assert not list((tmp_path / "installed").glob("*.receipt.json"))


def test_workspace_retry_repairs_tampered_installed_files(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    markdown = _skill_markdown()
    files = {"scripts/run.py": "print('reviewed')\n"}
    installed = manager.install_workspace_draft(
        draft_id="skilldraft_tampered",
        slug="safe-helper",
        skill_markdown=markdown,
        files=files,
        source_revision=1,
    )
    script = manager.get_skill_directory(installed.skill_id) / "scripts/run.py"
    script.write_text("print('tampered')\n", encoding="utf-8")

    repaired = manager.install_workspace_draft(
        draft_id="skilldraft_tampered",
        slug="safe-helper",
        skill_markdown=markdown,
        files=files,
        source_revision=1,
    )

    assert repaired.skill_id == installed.skill_id
    assert script.read_text(encoding="utf-8") == files["scripts/run.py"]
    assert repaired.content_digest == compute_package_digest(markdown, files)


def test_workspace_upgrade_switch_failure_restores_backed_up_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    original = manager.install_workspace_draft(
        draft_id="skilldraft_switch_failure",
        slug="safe-helper",
        skill_markdown=_skill_markdown(),
        files={"scripts/run.py": "print('v1')\n"},
        source_revision=1,
    )
    original_content = manager.get_skill_content(original.skill_id)
    original_rename = Path.rename

    def fail_staging_switch(path: Path, target: Path) -> Path:
        if ".staging-" in path.name:
            raise OSError("simulated workspace directory switch failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staging_switch)
    with pytest.raises(OSError, match="simulated workspace directory switch failure"):
        manager.install_workspace_draft(
            draft_id="skilldraft_switch_failure",
            slug="safe-helper",
            skill_markdown=_skill_markdown(version="v2"),
            files={"scripts/run.py": "print('v2')\n"},
            source_revision=2,
        )

    assert manager.get_skill_content(original.skill_id) == original_content
    assert manager.list_installed_skills() == [original]
    assert not list((tmp_path / "installed").glob(".*.staging-*"))
    assert not list((tmp_path / "installed").glob(".*.backup-*"))
    assert not list((tmp_path / "installed").glob("*.receipt.json"))


def test_workspace_receipt_recovers_interrupted_swap_and_retry_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    original = manager.install_workspace_draft(
        draft_id="skilldraft_recovery",
        slug="safe-helper",
        skill_markdown=_skill_markdown(),
        files={"scripts/run.py": "print('v1')\n"},
        source_revision=1,
    )
    markdown_v2 = _skill_markdown(version="v2")
    files_v2 = {"scripts/run.py": "print('v2')\n"}

    def interrupt_after_swap(_skills: dict[str, dict[str, object]]) -> None:
        raise KeyboardInterrupt("simulated process interruption")

    monkeypatch.setattr(manager, "_write_metadata", interrupt_after_swap)
    with pytest.raises(KeyboardInterrupt, match="simulated process interruption"):
        manager.install_workspace_draft(
            draft_id="skilldraft_recovery",
            slug="safe-helper",
            skill_markdown=markdown_v2,
            files=files_v2,
            source_revision=2,
        )

    assert list((tmp_path / "installed").glob("*.receipt.json"))
    assert list((tmp_path / "installed").glob(".*.backup-*"))

    recovered_manager = _manager(tmp_path)
    recovered = recovered_manager.install_workspace_draft(
        draft_id="skilldraft_recovery",
        slug="safe-helper",
        skill_markdown=markdown_v2,
        files=files_v2,
        source_revision=2,
    )

    assert recovered.skill_id == original.skill_id
    assert recovered.source_revision == 2
    assert recovered.content_digest == compute_package_digest(markdown_v2, files_v2)
    assert recovered_manager.get_skill_content(recovered.skill_id) == markdown_v2
    assert recovered_manager.install_workspace_draft(
        draft_id="skilldraft_recovery",
        slug="safe-helper",
        skill_markdown=markdown_v2,
        files=files_v2,
        source_revision=2,
    ) == recovered
    assert not list((tmp_path / "installed").glob("*.receipt.json"))
    assert not list((tmp_path / "installed").glob(".*.backup-*"))


def test_legacy_installed_metadata_and_root_layout_remain_readable(
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "installed"
    skill_id = "workspace-safe-helper-legacy"
    package_dir = installed_dir / skill_id
    package_dir.mkdir(parents=True)
    markdown = _skill_markdown()
    (package_dir / "SKILL.md").write_text(markdown, encoding="utf-8")
    (installed_dir / "installed.json").write_text(
        json.dumps(
            {
                "skills": {
                    skill_id: {
                        "skill_id": skill_id,
                        "name": "safe-helper",
                        "description": "A reviewed workspace helper.",
                        "repo_url": "workspace://draft/skilldraft_legacy",
                        "sub_path": "",
                        "installed_at": 1.0,
                        "source_ref": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    manager = _manager(tmp_path)
    restored = manager.list_installed_skills()[0]

    assert restored.skill_id == skill_id
    assert restored.source_kind == "workspace_draft"
    assert restored.source_id == "skilldraft_legacy"
    assert restored.source_revision is None
    assert restored.content_digest == ""
    assert restored.package_subpath == ""
    assert manager.get_skill_directory(skill_id) == package_dir
    assert manager.get_skill_content(skill_id) == markdown
