from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from scripts.audit_workspace_skill_drafts import audit_snapshot

from server.skills import api as skills_api
from server.skills.draft_store import (
    SkillDraftConflictError,
    SkillDraftStorageError,
    SkillDraftValidationError,
    WorkspaceSkillDraftStore,
)
from server.skills.skill_manager import (
    SkillManager,
    SkillValidationError,
)


SKILL_MD = """---
name: safe-helper
description: A reviewed workspace helper.
---

Run the local script only after explicit installation.
"""


def test_skill_draft_persists_and_rejects_unsafe_paths(tmp_path: Path) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    draft = store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={
            "scripts/run.py": "print('safe')\n",
            "references/guide.md": "# Guide\n",
            "agents/openai.yaml": "name: safe-helper\n",
        },
    )

    restored = WorkspaceSkillDraftStore(tmp_path / "drafts").require(draft.draft_id)
    assert restored.files["scripts/run.py"] == "print('safe')\n"

    with pytest.raises(SkillDraftValidationError, match="unsafe or non-canonical"):
        WorkspaceSkillDraftStore.validate_package(
            name="safe-helper",
            slug="safe-helper",
            description="A reviewed workspace helper.",
            skill_markdown=SKILL_MD,
            files={"../escape.py": "print('no')"},
        )
    with pytest.raises(SkillDraftValidationError, match="agents/openai.yaml"):
        WorkspaceSkillDraftStore.validate_package(
            name="safe-helper",
            slug="safe-helper",
            description="A reviewed workspace helper.",
            skill_markdown=SKILL_MD,
            files={"agents/other.yaml": "name: no"},
        )


def test_skill_install_is_explicit_revisioned_and_never_overwrites(
    tmp_path: Path,
) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    draft = store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={"scripts/run.py": "print('safe')\n"},
    )

    assert manager.list_installed_skills() == []
    installed = manager.install_workspace_draft(
        draft_id=draft.draft_id,
        slug=draft.slug,
        skill_markdown=draft.skill_markdown,
        files=draft.files,
    )
    marked = store.mark_installed(
        draft.draft_id, revision=draft.revision, skill_id=installed.skill_id
    )

    assert marked.status == "installed"
    assert marked.installed_skill_id == installed.skill_id
    assert manager.list_installed_skills()[0].skill_id == installed.skill_id
    repeated = manager.install_workspace_draft(
        draft_id=draft.draft_id,
        slug=draft.slug,
        skill_markdown=draft.skill_markdown,
        files=draft.files,
    )
    assert repeated.skill_id == installed.skill_id
    assert len(manager.list_installed_skills()) == 1
    with pytest.raises(SkillValidationError, match="unsafe or non-canonical"):
        manager.install_workspace_draft(
            draft_id="safe-helper-draft",
            slug="safe-helper",
            skill_markdown=SKILL_MD,
            files={"../escape.py": "print('no')"},
        )
    with pytest.raises(SkillDraftConflictError):
        store.archive(draft.draft_id, revision=draft.revision)


@pytest.mark.asyncio
async def test_workspace_skill_draft_api_requires_current_revision(
    tmp_path: Path,
) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    draft = store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={"scripts/run.py": "print('safe')\n"},
    )
    previous_store = skills_api._skill_draft_store
    previous_manager = skills_api._skill_manager
    skills_api.set_skill_draft_store_for_tests(store)
    skills_api.set_skill_manager_for_tests(manager)
    app = FastAPI()
    app.include_router(skills_api.router)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            listed = await client.get("/api/skills/drafts")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["draft_id"] == draft.draft_id

            stale = await client.post(
                f"/api/skills/drafts/{draft.draft_id}/install",
                json={
                    "expected_revision": draft.revision + 1,
                    "expected_digest": draft.content_digest,
                },
            )
            assert stale.status_code == 409

            installed = await client.post(
                f"/api/skills/drafts/{draft.draft_id}/install",
                json={
                    "expected_revision": draft.revision,
                    "expected_digest": draft.content_digest,
                },
            )
            assert installed.status_code == 200, installed.text
            assert installed.json()["draft"]["status"] == "installed"
            assert len(manager.list_installed_skills()) == 1

            installed_skill_id = installed.json()["installed"]["skill_id"]
            removed = await client.delete(f"/api/skills/{installed_skill_id}")
            assert removed.status_code == 200, removed.text
            after_uninstall = store.require(draft.draft_id)
            assert after_uninstall.status == "draft"
            assert after_uninstall.install_state == "not_installed"
            assert after_uninstall.installed_skill_id is None

            reinstalled = await client.post(
                f"/api/skills/drafts/{draft.draft_id}/install",
                json={
                    "expected_revision": after_uninstall.revision,
                    "expected_digest": after_uninstall.content_digest,
                },
            )
            assert reinstalled.status_code == 200, reinstalled.text
            assert reinstalled.json()["draft"]["install_state"] == "current"
    finally:
        skills_api._skill_draft_store = previous_store
        skills_api._skill_manager = previous_manager


def test_skill_draft_content_revisions_are_immutable_and_digest_guarded(
    tmp_path: Path,
) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    created = store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={"scripts/run.py": "print('v1')\n"},
    )

    assert created.content_revision == 1
    assert len(created.content_digest) == 64
    first = store.require_revision_snapshot(
        created.draft_id,
        revision=1,
        content_digest=created.content_digest,
    )

    updated = store.update(
        created.draft_id,
        expected_revision=created.revision,
        expected_digest=created.content_digest,
        files={"scripts/run.py": "print('v2')\n"},
    )

    assert updated.revision == created.revision + 1
    assert updated.content_revision == 2
    assert updated.content_digest != created.content_digest
    assert store.require_revision_snapshot(created.draft_id, revision=1) == first
    assert store.require_revision_snapshot(created.draft_id, revision=1).package[
        "files"
    ]["scripts/run.py"] == "print('v1')\n"
    assert store.require_revision_snapshot(created.draft_id, revision=2).package[
        "files"
    ]["scripts/run.py"] == "print('v2')\n"

    with pytest.raises(SkillDraftConflictError, match="content changed"):
        store.update(
            created.draft_id,
            expected_revision=updated.revision,
            expected_digest=created.content_digest,
            description="stale writer",
        )


def test_validation_is_bound_to_content_and_install_identity_survives_edit(
    tmp_path: Path,
) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    created = store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={"scripts/run.py": "print('v1')\n"},
    )
    validated = store.set_validation(
        created.draft_id,
        expected_revision=created.revision,
        expected_digest=created.content_digest,
        validation={"valid": True, "issues": []},
    )

    assert validated.validation["validator_version"]
    assert validated.validation["content_revision"] == created.content_revision
    assert validated.validation["content_digest"] == created.content_digest
    restored = WorkspaceSkillDraftStore(tmp_path / "drafts").require(created.draft_id)
    assert restored.validation["stale"] is False
    assert restored.needs_review is False
    installed = store.mark_installed(
        created.draft_id,
        expected_revision=validated.revision,
        expected_digest=validated.content_digest,
        skill_id="workspace-safe-helper",
    )
    assert installed.install_state == "current"
    assert installed.installed_content_revision == 1
    assert installed.installed_content_digest == installed.content_digest

    edited = store.update(
        installed.draft_id,
        expected_revision=installed.revision,
        expected_digest=installed.content_digest,
        files={"scripts/run.py": "print('v2')\n"},
    )
    assert edited.status == "draft"
    assert edited.install_state == "outdated"
    assert edited.installed_skill_id == installed.installed_skill_id
    assert edited.installed_content_digest == installed.content_digest
    assert edited.validation == {}


def test_v1_migration_is_lossless_per_record_and_quarantines_only_bad_items(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "drafts"
    storage_dir.mkdir()
    valid_record = {
        "draft_id": "skilldraft_valid",
        "name": "safe-helper",
        "slug": "safe-helper",
        "description": "A reviewed workspace helper.",
        "skill_markdown": SKILL_MD,
        "files": {"scripts/run.py": "print('safe')\n"},
        "status": "installed",
        "revision": 4,
        "source_proposal_id": "proposal_1",
        "installed_skill_id": "workspace-safe-helper",
        "validation": {"valid": True, "issues": []},
        "created_at": 10.0,
        "updated_at": 20.0,
    }
    bad_record = {
        "draft_id": "skilldraft_bad",
        "name": "broken",
        "slug": "broken",
        "description": "broken",
        "skill_markdown": 123,
        "files": {},
    }
    duplicate_slug_record = {
        **valid_record,
        "draft_id": "skilldraft_duplicate_slug",
        "status": "draft",
        "revision": 2,
        "installed_skill_id": None,
        "validation": {},
    }
    original = {
        "version": 1,
        "items": [valid_record, duplicate_slug_record, bad_record],
    }
    snapshot_path = storage_dir / "skill_drafts.json"
    snapshot_path.write_text(json.dumps(original), encoding="utf-8")

    store = WorkspaceSkillDraftStore(storage_dir)
    restored = store.require("skilldraft_valid")

    assert restored.revision == 4
    assert restored.content_revision == 1
    assert restored.install_state == "current"
    assert restored.installed_content_digest == restored.content_digest
    assert restored.validation["stale"] is True
    assert restored.needs_review is True
    assert len(store.list_revision_snapshots(restored.draft_id)) == 1
    quarantined = store.list_quarantined()
    assert len(quarantined) == 1
    assert "record" not in quarantined[0]
    assert len(quarantined[0]["record_sha256"]) == 64
    assert json.loads((storage_dir / "skill_drafts.v1.backup.json").read_text()) == original
    migrated = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert [item["draft_id"] for item in migrated["items"]] == [
        "skilldraft_duplicate_slug",
        "skilldraft_valid",
    ]


def test_v1_migration_does_not_copy_credentials_into_v2_records(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "drafts"
    storage_dir.mkdir()
    leaked_secret = "dify-live-secret-1234567890"
    record = {
        "draft_id": "skilldraft_secret",
        "name": "secret-helper",
        "slug": "secret-helper",
        "description": f'DIFY_API_KEY="{leaked_secret}"',
        "skill_markdown": """---
name: secret-helper
description: A legacy draft containing blocked content.
---

Never persist credentials.
""",
        "files": {"scripts/run.py": "print('safe')\n"},
    }
    snapshot_path = storage_dir / "skill_drafts.json"
    snapshot_path.write_text(
        json.dumps({"version": 1, "items": [record]}),
        encoding="utf-8",
    )

    store = WorkspaceSkillDraftStore(storage_dir)

    assert store.list() == []
    quarantine = store.list_quarantined()
    assert len(quarantine) == 1
    assert quarantine[0]["reason_code"] == "invalid_record"
    assert "record" not in quarantine[0]
    migrated_text = snapshot_path.read_text(encoding="utf-8")
    assert leaked_secret not in migrated_text
    assert "DIFY_API_KEY" not in migrated_text
    assert leaked_secret in (storage_dir / "skill_drafts.v1.backup.json").read_text(
        encoding="utf-8"
    )


def test_install_current_serializes_edit_against_reviewed_digest(tmp_path: Path) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    created = store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={"scripts/run.py": "print('v1')\n"},
    )
    install_started = threading.Event()
    release_install = threading.Event()
    edit_started = threading.Event()
    outcomes: dict[str, object] = {}

    def installer(item):
        install_started.set()
        assert release_install.wait(timeout=2)
        return SimpleNamespace(
            skill_id="workspace-safe-helper",
            content_digest=item.content_digest,
        )

    def run_install() -> None:
        outcomes["installed"] = store.install_current(
            created.draft_id,
            expected_revision=created.revision,
            expected_digest=created.content_digest,
            installer=installer,
        )

    def run_edit() -> None:
        edit_started.set()
        try:
            store.update(
                created.draft_id,
                expected_revision=created.revision,
                expected_digest=created.content_digest,
                files={"scripts/run.py": "print('v2')\n"},
            )
        except Exception as exc:  # noqa: BLE001 - captured for cross-thread assertion
            outcomes["edit_error"] = exc

    install_thread = threading.Thread(target=run_install)
    edit_thread = threading.Thread(target=run_edit)
    install_thread.start()
    assert install_started.wait(timeout=2)
    edit_thread.start()
    assert edit_started.wait(timeout=2)
    release_install.set()
    install_thread.join(timeout=2)
    edit_thread.join(timeout=2)

    assert not install_thread.is_alive()
    assert not edit_thread.is_alive()
    assert "installed" in outcomes
    assert isinstance(outcomes.get("edit_error"), SkillDraftConflictError)
    restored = store.require(created.draft_id)
    assert restored.install_state == "current"
    assert restored.content_digest == created.content_digest


def test_all_draft_mutations_restore_memory_when_atomic_save_fails(
    tmp_path: Path,
) -> None:
    def fail_save() -> None:
        raise OSError("simulated disk full")

    empty_store = WorkspaceSkillDraftStore(tmp_path / "create")
    empty_store._save_unlocked = fail_save  # type: ignore[method-assign]
    with pytest.raises(OSError, match="disk full"):
        empty_store.create(
            name="safe-helper",
            slug="safe-helper",
            description="A reviewed workspace helper.",
            skill_markdown=SKILL_MD,
        )
    assert empty_store.list() == []

    actions = (
        lambda store, item: store.update(
            item.draft_id,
            expected_revision=item.revision,
            expected_digest=item.content_digest,
            files={"scripts/run.py": "print('changed')\n"},
        ),
        lambda store, item: store.set_validation(
            item.draft_id,
            expected_revision=item.revision,
            expected_digest=item.content_digest,
            validation={"valid": True, "issues": []},
        ),
        lambda store, item: store.mark_installed(
            item.draft_id,
            expected_revision=item.revision,
            expected_digest=item.content_digest,
            skill_id="workspace-safe-helper",
        ),
        lambda store, item: store.archive(
            item.draft_id,
            expected_revision=item.revision,
            expected_digest=item.content_digest,
        ),
        lambda store, item: store.mark_uninstalled_skill(
            "workspace-safe-helper"
        ),
        lambda store, item: store.install_current(
            item.draft_id,
            expected_revision=item.revision,
            expected_digest=item.content_digest,
            installer=lambda current: SimpleNamespace(
                skill_id="workspace-safe-helper",
                content_digest=current.content_digest,
            ),
        ),
    )
    for index, action in enumerate(actions):
        store = WorkspaceSkillDraftStore(tmp_path / f"mutation-{index}")
        created = store.create(
            name="safe-helper",
            slug="safe-helper",
            description="A reviewed workspace helper.",
            skill_markdown=SKILL_MD,
            files={"scripts/run.py": "print('safe')\n"},
        )
        if index == 4:
            created = store.mark_installed(
                created.draft_id,
                expected_revision=created.revision,
                expected_digest=created.content_digest,
                skill_id="workspace-safe-helper",
            )
        snapshots_before = store.list_revision_snapshots(created.draft_id)
        store._save_unlocked = fail_save  # type: ignore[method-assign]
        with pytest.raises(OSError, match="disk full"):
            action(store, created)
        assert store.require(created.draft_id) == created
        assert store.list_revision_snapshots(created.draft_id) == snapshots_before


def test_corrupt_top_level_snapshot_is_backed_up_and_never_overwritten(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "drafts"
    storage_dir.mkdir()
    snapshot_path = storage_dir / "skill_drafts.json"
    snapshot_path.write_text("{not-json", encoding="utf-8")
    store = WorkspaceSkillDraftStore(storage_dir)

    with pytest.raises(SkillDraftStorageError, match="cannot be overwritten"):
        store.create(
            name="safe-helper",
            slug="safe-helper",
            description="A reviewed workspace helper.",
            skill_markdown=SKILL_MD,
        )

    assert snapshot_path.read_text(encoding="utf-8") == "{not-json"
    assert (storage_dir / "skill_drafts.corrupt.backup.json").read_text(
        encoding="utf-8"
    ) == "{not-json"


def test_workspace_draft_audit_is_read_only(tmp_path: Path) -> None:
    storage_dir = tmp_path / "drafts"
    store = WorkspaceSkillDraftStore(storage_dir)
    store.create(
        name="safe-helper",
        slug="safe-helper",
        description="A reviewed workspace helper.",
        skill_markdown=SKILL_MD,
        files={"scripts/run.py": "print('safe')\n"},
    )
    before = store.snapshot_path.read_bytes()

    report, exit_code = audit_snapshot(store.snapshot_path)

    assert exit_code == 0
    assert report["record_count"] == 1
    assert report["valid_count"] == 1
    assert store.snapshot_path.read_bytes() == before
