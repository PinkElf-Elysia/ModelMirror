from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.agent_workspace.api import set_agent_workspace_for_tests
from server.agent_workspace.store import AgentStateStore
from server.main import app
from server.skills.api import set_builtin_skill_library_for_tests
from server.skills.builtin_library import (
    BuiltinSkillLibrary,
    BuiltinSkillLibraryError,
    SkillsetUpdate,
    SkillsetWrite,
)


def test_library_contains_only_the_16_digest_verified_builtins(
    tmp_path: Path,
) -> None:
    library = BuiltinSkillLibrary(skillset_path=tmp_path / "skillsets.json")
    skills = library.list_skills()
    default = library.list_skillsets()[0]

    assert len(skills) == 16
    assert len({skill.skill_id for skill in skills}) == 16
    assert {skill.status for skill in skills} == {
        "ready",
        "conditional",
        "dependency_missing",
        "reference_only",
    }
    assert sum(skill.status == "ready" for skill in skills) == 6
    assert sum(skill.status == "conditional" for skill in skills) == 3
    assert sum(skill.status == "dependency_missing" for skill in skills) == 1
    assert sum(skill.status == "reference_only" for skill in skills) == 6
    assert all(skill.source_license == "Apache-2.0" for skill in skills)
    assert default.skillset_id == "general-agent-default"
    assert default.builtin is True
    assert [member.skill_id for member in default.members] == [
        skill.skill_id for skill in skills
    ]
    assert "external" not in {skill.skill_id for skill in skills}


def test_builtin_digest_normalizes_crlf_but_rejects_content_changes(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[1] / "skills" / "builtin"
    copied_root = tmp_path / "builtin"
    shutil.copytree(source_root, copied_root)
    skill_path = copied_root / "agent-evaluation" / "SKILL.md"
    canonical_content = skill_path.read_bytes().replace(b"\r\n", b"\n")
    skill_path.write_bytes(canonical_content.replace(b"\n", b"\r\n"))

    library = BuiltinSkillLibrary(
        root=copied_root,
        skillset_path=tmp_path / "skillsets.json",
    )
    assert len(library.list_skills()) == 16

    skill_path.write_bytes(skill_path.read_bytes() + b"substantive change\r\n")
    with pytest.raises(BuiltinSkillLibraryError, match="agent-evaluation"):
        library.list_skills()


def test_agent_creation_keeps_upstream_method_and_adds_native_guardrails(
    tmp_path: Path,
) -> None:
    library = BuiltinSkillLibrary(skillset_path=tmp_path / "skillsets.json")
    content = library.get_content("agent-creation")

    assert "## Upstream PenguinHarness v7 instructions (preserved in full)" in content
    assert "## Resolve the inherited runtime" in content
    assert "## The embedded agent of an SDK app" in content
    assert "## ModelMirror native staging and quality addendum" in content
    assert "mandatory second review pass" in content
    assert ".modelmirror/generated-agent/" in content


def test_custom_skillset_crud_locks_content_digests(tmp_path: Path) -> None:
    library = BuiltinSkillLibrary(skillset_path=tmp_path / "skillsets.json")
    created = library.create_skillset(
        SkillsetWrite(
            skillset_id="analysis-core",
            name="Analysis Core",
            description="Focused analysis tools.",
            skill_ids=["data-analysis"],
        )
    )
    assert created.members[0].digest

    updated = library.update_skillset(
        "analysis-core",
        SkillsetUpdate(
            expected_revision=created.revision,
            name="Analysis and Slides",
            description="Analysis with presentation output.",
            skill_ids=["data-analysis", "bento-slides"],
        ),
    )
    assert len(updated.members) == 2

    with pytest.raises(BuiltinSkillLibraryError, match="changed"):
        library.update_skillset(
            "analysis-core",
            SkillsetUpdate(
                expected_revision=created.revision,
                name="Stale",
                description="",
                skill_ids=["data-analysis"],
            ),
        )

    library.delete_skillset("analysis-core")
    assert [item.skillset_id for item in library.list_skillsets()] == [
        "general-agent-default"
    ]


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    library = BuiltinSkillLibrary(skillset_path=tmp_path / "skillsets.json")
    store = AgentStateStore(root=tmp_path / "workspace")
    set_builtin_skill_library_for_tests(library)
    set_agent_workspace_for_tests(store, enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        yield async_client
    set_builtin_skill_library_for_tests(None)
    set_agent_workspace_for_tests(None, enabled=None)


@pytest.mark.asyncio
async def test_library_and_skillset_api_materialize_snapshot(
    client: httpx.AsyncClient,
) -> None:
    library = await client.get("/api/skills/library")
    assert library.status_code == 200, library.text
    assert library.json()["total"] == 16

    created = await client.post(
        "/api/skills/skillsets",
        json={
            "skillset_id": "engineering-core",
            "name": "Engineering Core",
            "description": "Implementation bundle.",
            "skill_ids": ["software-engineering", "web-design"],
        },
    )
    assert created.status_code == 201, created.text

    agent = await client.get("/api/agent-workspace/agents/default_agent")
    materialized = await client.post(
        "/api/skills/skillsets/engineering-core/materialize",
        json={
            "agent_id": "default_agent",
            "expected_revision": agent.json()["revision"],
        },
    )
    assert materialized.status_code == 200, materialized.text
    assert materialized.json()["config"]["skillset_id"] == "engineering-core"
    assert [skill["skill_id"] for skill in materialized.json()["skills"]] == [
        "software-engineering",
        "web-design",
    ]
