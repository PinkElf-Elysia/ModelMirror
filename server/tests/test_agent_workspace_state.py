from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from server.agent_workspace.defaults import default_tools
from server.agent_workspace.models import AgentCreateRequest
from server.agent_workspace.store import (
    AgentConflictError,
    AgentStateStore,
    AgentStateValidationError,
)
from server.skills.builtin_library import BuiltinSkillLibrary, SkillsetWrite


def test_general_agent_initialization_is_exact_and_idempotent(tmp_path: Path) -> None:
    store = AgentStateStore(root=tmp_path / "workspace")

    created = store.ensure_default_agent()

    assert created.agent_id == "default_agent"
    assert created.config.name == "General Agent"
    assert created.config.version == 1
    assert created.config.max_turns == 100
    assert created.config.model.max_tokens == 32000
    assert created.config.model.thinking_level == "medium"
    assert created.config.model.timeoutMs == 120000
    assert created.config.compaction.max_context_length == 128000
    assert created.config.compaction.max_session_turns == -1
    assert [tool.name for tool in created.config.tools.builtin] == [
        tool.name for tool in default_tools().builtin
    ]
    assert len(created.config.tools.builtin) == 9
    assert len(created.skills) == 16
    assert created.config.skillset_id == "general-agent-default"

    state_dir = tmp_path / "workspace" / "agents" / "default_agent" / "agent_state"
    config_path = state_dir / "system_config.yaml"
    agents_path = state_dir / "AGENTS.md"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["name"] = "My preserved General Agent"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    agents_path.write_text("# Keep this user edit", encoding="utf-8")

    reloaded = AgentStateStore(root=tmp_path / "workspace").ensure_default_agent()

    assert reloaded.config.name == "My preserved General Agent"
    assert reloaded.agents_md == "# Keep this user edit"
    assert len(list((state_dir / "skills").iterdir())) == 16


def test_agent_state_rejects_unknown_yaml_fields(tmp_path: Path) -> None:
    store = AgentStateStore(root=tmp_path / "workspace")
    store.ensure_default_agent()
    config_path = (
        tmp_path
        / "workspace"
        / "agents"
        / "default_agent"
        / "agent_state"
        / "system_config.yaml"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["vault"] = {"enabled": True}
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(AgentStateValidationError, match="invalid state"):
        store.get_agent("default_agent")


def test_revision_conflict_and_reset_preserve_user_layers(tmp_path: Path) -> None:
    store = AgentStateStore(root=tmp_path / "workspace")
    current = store.ensure_default_agent()
    changed = current.config.model_copy(update={"max_turns": 12})

    updated = store.update_agent(
        "default_agent",
        expected_revision=current.revision,
        config=changed,
        agents_md="# Custom behavior",
    )

    with pytest.raises(AgentConflictError, match="Reload"):
        store.update_agent(
            "default_agent",
            expected_revision=current.revision,
            config=changed,
            agents_md="stale",
        )

    reset = store.reset_agent_config(
        "default_agent", expected_revision=updated.revision
    )
    assert reset.config.max_turns == 100
    assert reset.agents_md == "# Custom behavior"
    assert len(reset.skills) == 16


def test_create_never_overwrites_and_general_agent_cannot_be_deleted(
    tmp_path: Path,
) -> None:
    store = AgentStateStore(root=tmp_path / "workspace")
    created = store.create_agent(
        AgentCreateRequest(
            agent_id="research-helper",
            name="Research Helper",
            description="Summarizes supplied files.",
        )
    )
    assert created.agent_id == "research-helper"

    with pytest.raises(AgentConflictError):
        store.create_agent(
            AgentCreateRequest(
                agent_id="research-helper",
                name="Replacement",
                description="Must not overwrite.",
            )
        )

    store.ensure_default_agent()
    with pytest.raises(AgentConflictError, match="cannot be deleted"):
        store.delete_agent("default_agent")


def test_custom_skillset_materializes_a_digest_locked_snapshot(tmp_path: Path) -> None:
    library = BuiltinSkillLibrary(skillset_path=tmp_path / "skillsets.json")
    skillset = library.create_skillset(
        SkillsetWrite(
            skillset_id="engineering-core",
            name="Engineering Core",
            description="Minimal implementation bundle.",
            skill_ids=["software-engineering", "web-design"],
        )
    )
    store = AgentStateStore(root=tmp_path / "workspace")
    current = store.ensure_default_agent()

    updated = store.materialize_builtin_skillset(
        "default_agent",
        skillset_id=skillset.skillset_id,
        members=[member.model_dump(mode="json") for member in skillset.members],
        expected_revision=current.revision,
    )

    assert updated.config.skillset_id == "engineering-core"
    assert [skill.skill_id for skill in updated.skills] == [
        "software-engineering",
        "web-design",
    ]
    snapshot_path = (
        tmp_path
        / "workspace"
        / "agents"
        / "default_agent"
        / "agent_state"
        / "skillset_snapshot.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["skillset_id"] == "engineering-core"
    assert [item["digest"] for item in snapshot["skills"]] == [
        member.digest for member in skillset.members
    ]
