from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.toolsets.credentials import (
    CredentialStore,
    CredentialUnavailableError,
)
from server.toolsets.store import (
    ToolsetConflictError,
    ToolsetStore,
    ToolsetValidationError,
)


TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _connected_toolset(store: ToolsetStore):
    created = store.create_toolset(
        name="Research tools",
        connection={
            "transport": "stdio",
            "command": ["python", "-m", "research_server"],
            "tool_prefix": "research",
        },
    )
    discovered = store.replace_discovered_tools(
        created.id,
        tools=[
            {
                "original_name": "search",
                "description": "Search a local corpus.",
                "input_schema": TOOL_SCHEMA,
            }
        ],
    )
    enabled = store.update_tool(
        created.id,
        "search",
        revision=discovered.revision,
        patch={
            "enabled": True,
            "alias": "find_sources",
            "default_arguments": {"limit": 5},
        },
    )
    return store.set_runtime_state(
        created.id,
        status="connected",
        session_id="session-draft",
    ), enabled


def test_toolset_store_persists_drafts_and_immutable_versions(
    tmp_path: Path,
) -> None:
    store = ToolsetStore(tmp_path / "toolsets")
    connected, _ = _connected_toolset(store)

    version_one = store.publish(connected.id, revision=connected.revision)
    changed = store.update_toolset(
        connected.id,
        revision=connected.revision,
        patch={"description": "Changed after version one."},
    )
    changed = store.update_tool(
        connected.id,
        "search",
        revision=changed.revision,
        patch={"description": "New draft description."},
    )
    changed = store.set_runtime_state(
        connected.id,
        status="connected",
        session_id="session-draft-2",
    )
    version_two = store.publish(changed.id, revision=changed.revision)

    reloaded = ToolsetStore(store.storage_dir)
    persisted = reloaded.get_toolset(connected.id)
    assert persisted.published_version == 2
    assert version_one.version == 1
    assert version_two.version == 2
    assert reloaded.get_version(connected.id, 1).tools[0].description == (
        "Search a local corpus."
    )
    assert reloaded.get_version(connected.id, 2).tools[0].description == (
        "New draft description."
    )


def test_toolset_store_enforces_revision_and_publish_contract(
    tmp_path: Path,
) -> None:
    store = ToolsetStore(tmp_path / "toolsets")
    created = store.create_toolset(name="Draft")

    with pytest.raises(ToolsetConflictError):
        store.update_toolset(
            created.id,
            revision=created.revision + 1,
            patch={"description": "stale"},
        )
    with pytest.raises(ToolsetValidationError):
        store.publish(created.id, revision=created.revision)


def test_credentials_are_encrypted_rotatable_and_report_key_loss(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "credentials"
    store = CredentialStore(storage_dir, master_key=Fernet.generate_key())
    record, visible_once = store.create(
        name="Authorization",
        kind="header",
        value="super-secret-token",
    )

    assert visible_once == "super-secret-token"
    assert record.ciphertext == ""
    assert store.resolve(record.credential_id) == "super-secret-token"
    persisted = (storage_dir / "credentials.json").read_text(encoding="utf-8")
    assert "super-secret-token" not in persisted
    assert json.loads(persisted)["credentials"][0]["ciphertext"]

    rotated, visible_once = store.rotate(
        record.credential_id,
        value="replacement-secret",
    )
    assert visible_once == "replacement-secret"
    assert rotated.ciphertext == ""
    assert store.resolve(record.credential_id) == "replacement-secret"

    wrong_key_store = CredentialStore(
        storage_dir,
        master_key=Fernet.generate_key(),
    )
    assert wrong_key_store.list()[0].status == "unavailable"
    with pytest.raises(CredentialUnavailableError):
        wrong_key_store.resolve(record.credential_id)
