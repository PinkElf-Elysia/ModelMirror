from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from server.main import app, workflow_topological_order
from server.plugins import PluginConflictError, PluginStore, PluginValidationError
from server.plugins.registry import configure_plugin_store
from server.prompts import (
    PromptProfileBinding,
    PromptProfileConflictError,
    PromptProfileStore,
    PromptProfileValidationError,
    ResolvedPromptProfile,
    configure_prompt_profile_store,
    resolve_prompt_command,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xperts import XpertStore, set_xpert_store_for_tests
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy


def _plugin_zip(manifest: dict[str, Any], files: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "modelmirror-plugin.json",
            json.dumps(manifest, ensure_ascii=False),
        )
        for path, content in (files or {}).items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _manifest(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "name": "Research Toolkit",
        "slug": "research-toolkit",
        "description": "Research resources",
        "license": "MIT",
        "prompts": [
            {
                "name": "Review",
                "slug": "review",
                "aliases": ["review"],
                "template": "Review the following material:\n{{args}}",
            }
        ],
        "skills": [],
        "toolsets": [],
        "middleware_presets": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def stores(tmp_path: Path):
    prompt_store = PromptProfileStore(tmp_path / "prompts")
    plugin_store = PluginStore(tmp_path / "plugins")
    xpert_store = XpertStore(tmp_path / "xperts")
    configure_prompt_profile_store(prompt_store)
    configure_plugin_store(plugin_store)
    set_xpert_store_for_tests(xpert_store)
    yield prompt_store, plugin_store, xpert_store
    configure_prompt_profile_store(PromptProfileStore(tmp_path / "reset-prompts"))
    configure_plugin_store(PluginStore(tmp_path / "reset-plugins"))
    set_xpert_store_for_tests(None)


@pytest_asyncio.fixture
async def client(stores):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def test_prompt_profile_versions_aliases_and_command_resolution(
    tmp_path: Path,
) -> None:
    store = PromptProfileStore(tmp_path / "prompts")
    created = store.create_profile(
        name="Review",
        aliases=["review", "check"],
        template="Review this:\n{{args}}",
        public_app_allowed=True,
    )
    version = store.publish_profile(created.id, revision=created.draft_revision)
    updated = store.update_profile(
        created.id,
        revision=created.draft_revision,
        patch={"template": "Changed draft: {{args}}"},
    )

    assert store.render(created.id, 1, "alpha") == "Review this:\nalpha"
    assert version.template == "Review this:\n{{args}}"
    assert updated.template == "Changed draft: {{args}}"
    assert PromptProfileStore(store.storage_dir).get_version(created.id, 1) == version

    resolved = ResolvedPromptProfile(
        profile_id=created.id,
        slug=created.slug,
        version=1,
        name=version.name,
        aliases=version.aliases,
        template=version.template,
        public_app_allowed=True,
        checksum=version.checksum,
    )
    command = resolve_prompt_command("/review release notes", [resolved])
    assert command.original_message == "/review release notes"
    assert command.effective_message == "Review this:\nrelease notes"
    assert resolve_prompt_command("//review", [resolved]).effective_message == "/review"
    with pytest.raises(PromptProfileValidationError, match="Unknown"):
        resolve_prompt_command("/missing data", [resolved])

    conflict = store.create_profile(
        name="Other",
        aliases=["review"],
        template="{{args}}",
    )
    with pytest.raises(PromptProfileValidationError, match="already exist"):
        store.publish_profile(conflict.id, revision=conflict.draft_revision)

    archived = store.archive_profile(
        created.id,
        revision=updated.draft_revision,
    )
    with pytest.raises(PromptProfileConflictError, match="cannot be published"):
        store.publish_profile(archived.id, revision=archived.draft_revision)


def test_plugin_store_rejects_unsafe_zip_and_publishes_immutable_snapshot(
    tmp_path: Path,
) -> None:
    store = PluginStore(tmp_path / "plugins")
    imported = store.import_package(
        filename="research.zip",
        content=_plugin_zip(_manifest()),
    )
    published = store.publish_plugin(
        imported.id,
        revision=imported.draft_revision,
        installed_skill_ids=[],
    )
    assert published.version == 1
    assert published.prompts[0].aliases == ["review"]
    assert published.prompts[0].source == "plugin"
    assert PluginStore(store.storage_dir).get_version(imported.id, 1) == published

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../modelmirror-plugin.json", json.dumps(_manifest()))
    with pytest.raises(PluginValidationError, match="Unsafe"):
        store.import_package(filename="unsafe.zip", content=unsafe.getvalue())

    archived = store.archive_plugin(
        imported.id,
        revision=imported.draft_revision,
    )
    with pytest.raises(PluginConflictError, match="cannot be published"):
        store.publish_plugin(
            archived.id,
            revision=archived.draft_revision,
            installed_skill_ids=[],
        )


def test_plugin_store_removes_version_directory_when_metadata_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PluginStore(tmp_path / "plugins")
    imported = store.import_package(
        filename="research.zip",
        content=_plugin_zip(_manifest()),
    )
    original_write = store._write_unlocked

    def fail_write(*_: Any, **__: Any) -> None:
        raise OSError("Simulated metadata write failure.")

    monkeypatch.setattr(store, "_write_unlocked", fail_write)
    with pytest.raises(OSError, match="metadata write failure"):
        store.publish_plugin(
            imported.id,
            revision=imported.draft_revision,
            installed_skill_ids=[],
        )

    assert not store._version_dir(imported.id, 1).exists()
    monkeypatch.setattr(store, "_write_unlocked", original_write)
    assert store.list_versions(imported.id) == []


def _plugin_workflow(plugin_id: str) -> NativeWorkflowDefinition:
    return NativeWorkflowDefinition.model_validate(
        {
            "id": "plugin-workflow",
            "title": "Plugin workflow",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "Manager",
                        "modelId": "test-model",
                        "rolePrompt": "Use bound resources.",
                        "taskInput": "{{user_input}}",
                        "outputVariable": "agent_output",
                        "toolMode": "mcp_tools",
                        "maxIterations": "5",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "agent_output"},
                },
                {
                    "id": "plugin",
                    "type": "plugin_resource",
                    "data": {
                        "kind": "plugin_resource",
                        "pluginId": plugin_id,
                        "versionPolicy": "latest",
                    },
                },
            ],
            "edges": [
                {"id": "input-agent", "source": "input", "target": "agent"},
                {"id": "agent-output", "source": "agent", "target": "output"},
                {
                    "id": "bind-plugin",
                    "source": "plugin",
                    "target": "agent",
                    "sourceHandle": "plugin-binding",
                    "targetHandle": "plugin",
                },
            ],
        }
    )


def test_plugin_resource_binding_is_excluded_from_control_flow() -> None:
    workflow = _plugin_workflow("plugin-test")
    validation = validate_workflow_graph(workflow)
    order = workflow_topological_order(list(workflow.nodes), list(workflow.edges))
    assert validation.valid is True
    assert validation.order == ["input", "agent", "output"]
    assert order == ["input", "agent", "output"]

    payload = workflow.model_dump(mode="json")
    payload["edges"].append(
        {"id": "bad-edge", "source": "plugin", "target": "output"}
    )
    invalid = validate_workflow_graph(
        NativeWorkflowDefinition.model_validate(payload)
    )
    assert "mixed_resource_binding_and_control_flow" in {
        issue.code for issue in invalid.issues
    }


@pytest.mark.asyncio
async def test_xpert_publish_pins_prompt_and_plugin_versions(
    client: httpx.AsyncClient,
    stores,
) -> None:
    prompt_store, plugin_store, xpert_store = stores
    profile = prompt_store.create_profile(
        name="Direct Review",
        aliases=["direct-review"],
        template="Direct review:\n{{args}}",
        public_app_allowed=True,
    )
    prompt_store.publish_profile(profile.id, revision=profile.draft_revision)
    plugin = plugin_store.import_package(
        filename="plugin.zip",
        content=_plugin_zip(_manifest()),
    )
    plugin_store.publish_plugin(
        plugin.id,
        revision=plugin.draft_revision,
        installed_skill_ids=[],
    )

    xpert = xpert_store.create_xpert(name="Plugin Manager")
    draft = xpert.draft.model_copy(deep=True)
    draft.workflow = _plugin_workflow(plugin.id)
    draft.prompt_profiles = [
        PromptProfileBinding(
            profile_id=profile.id,
            version_policy="latest",
            enabled=True,
        )
    ]
    xpert = xpert_store.update_xpert(
        xpert.id,
        {"draft": draft.model_dump(mode="json")},
    )

    response = await client.post(
        f"/api/xperts/{xpert.id}/publish",
        json={"release_notes": "Fixed resources"},
    )
    assert response.status_code == 200, response.text
    version = xpert_store.get_version(xpert.id, 1)
    plugin_node = next(
        node
        for node in version.workflow.nodes
        if node.data.get("kind") == "plugin_resource"
    )
    assert plugin_node.data["versionPolicy"] == "pinned"
    assert int(plugin_node.data["pinnedVersion"]) == 1
    assert {item.aliases[0] for item in version.prompt_profiles} == {
        "direct-review",
        "review",
    }
    assert {item.source for item in version.prompt_profiles} == {"direct", "plugin"}

    preflight = _deployment_preflight(version, XpertAppPolicy())
    assert "app_plugin_resource_forbidden" in {
        str(issue.get("code")) for issue in preflight["issues"]
    }


@pytest.mark.asyncio
async def test_plugin_publish_rolls_back_materialized_skills(
    client: httpx.AsyncClient,
    stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, plugin_store, _ = stores
    manifest = _manifest(
        skills=[
            {
                "slug": "review-helper",
                "root": "skills/review-helper",
                "name": "Review Helper",
            }
        ]
    )
    plugin = plugin_store.import_package(
        filename="plugin-with-skill.zip",
        content=_plugin_zip(
            manifest,
            {
                "skills/review-helper/SKILL.md": "\n".join(
                    [
                        "---",
                        "name: Review Helper",
                        "description: Review a document safely.",
                        "---",
                        "",
                        "# Review Helper",
                    ]
                )
            },
        ),
    )

    class FakeSkill:
        skill_id = "plugin-research-toolkit-v1-review-helper"

    class FakeSkillManager:
        def __init__(self) -> None:
            self.uninstalled: list[str] = []

        def install_plugin_skill(self, **_: Any) -> FakeSkill:
            return FakeSkill()

        def uninstall_skill(self, skill_id: str) -> None:
            self.uninstalled.append(skill_id)

    skill_manager = FakeSkillManager()
    monkeypatch.setattr(
        "server.plugins.api.get_skill_manager",
        lambda: skill_manager,
    )

    def fail_publish(*_: Any, **__: Any) -> None:
        raise PluginConflictError("Simulated version write conflict.")

    monkeypatch.setattr(plugin_store, "publish_plugin", fail_publish)

    response = await client.post(
        f"/api/plugins/{plugin.id}/publish",
        json={"revision": plugin.draft_revision},
    )

    assert response.status_code == 409
    assert skill_manager.uninstalled == [FakeSkill.skill_id]
    assert plugin_store.list_versions(plugin.id) == []
