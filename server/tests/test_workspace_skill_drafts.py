from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.skills import api as skills_api
from server.skills.draft_store import (
    SkillDraftConflictError,
    SkillDraftValidationError,
    WorkspaceSkillDraftStore,
)
from server.skills.skill_manager import (
    SkillInstallError,
    SkillManager,
    SkillValidationError,
)


SKILL_MD = """---
name: Safe Helper
description: A reviewed workspace helper.
---

Run the local script only after explicit installation.
"""


def test_skill_draft_persists_and_rejects_unsafe_paths(tmp_path: Path) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts")
    draft = store.create(
        name="Safe Helper",
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

    with pytest.raises(SkillDraftValidationError, match="Unsafe Skill file path"):
        WorkspaceSkillDraftStore.validate_package(
            name="Unsafe",
            slug="unsafe",
            description="Unsafe",
            skill_markdown=SKILL_MD,
            files={"../escape.py": "print('no')"},
        )
    with pytest.raises(SkillDraftValidationError, match="agents/openai.yaml"):
        WorkspaceSkillDraftStore.validate_package(
            name="Unsafe",
            slug="unsafe",
            description="Unsafe",
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
        name="Safe Helper",
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
    with pytest.raises(SkillInstallError, match="already installed"):
        manager.install_workspace_draft(
            draft_id=draft.draft_id,
            slug=draft.slug,
            skill_markdown=draft.skill_markdown,
            files=draft.files,
        )
    with pytest.raises(SkillValidationError, match="Unsafe Skill file path"):
        manager.install_workspace_draft(
            draft_id="unsafe-draft",
            slug="unsafe",
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
        name="Safe Helper",
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
                json={"revision": draft.revision + 1},
            )
            assert stale.status_code == 409

            installed = await client.post(
                f"/api/skills/drafts/{draft.draft_id}/install",
                json={"revision": draft.revision},
            )
            assert installed.status_code == 200, installed.text
            assert installed.json()["draft"]["status"] == "installed"
            assert len(manager.list_installed_skills()) == 1
    finally:
        skills_api._skill_draft_store = previous_store
        skills_api._skill_manager = previous_manager
