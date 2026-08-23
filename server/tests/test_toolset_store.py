from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.toolsets.credentials import (
    CredentialNotFoundError,
    CredentialStore,
    CredentialStoreError,
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


def test_credentials_are_isolated_by_tenant_and_owner(tmp_path: Path) -> None:
    store = CredentialStore(
        tmp_path / "scoped-credentials",
        master_key=Fernet.generate_key(),
    )
    owned, _ = store.create(
        name="Tenant A database password",
        value="tenant-a-secret",
        tenant_id="tenant-a",
        owner_id="user-a",
        catalog_project_id="postgres-mcp",
        catalog_slot="password",
    )
    sibling, _ = store.create(
        name="Tenant A second owner",
        value="tenant-a-user-b-secret",
        tenant_id="tenant-a",
        owner_id="user-b",
        catalog_project_id="postgres-mcp",
        catalog_slot="password",
    )
    other_tenant, _ = store.create(
        name="Tenant B database password",
        value="tenant-b-secret",
        tenant_id="tenant-b",
        owner_id="user-a",
        catalog_project_id="postgres-mcp",
        catalog_slot="password",
    )

    assert store.list() == []
    assert [
        item.credential_id
        for item in store.list(tenant_id="tenant-a", owner_id="user-a")
    ] == [owned.credential_id]
    assert store.resolve(
        owned.credential_id,
        tenant_id="tenant-a",
        owner_id="user-a",
    ) == "tenant-a-secret"

    for tenant_id, owner_id in (
        ("tenant-a", "user-b"),
        ("tenant-b", "user-a"),
        ("local", "local"),
    ):
        with pytest.raises(CredentialNotFoundError):
            store.get_public(
                owned.credential_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        with pytest.raises(CredentialNotFoundError):
            store.resolve(
                owned.credential_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        with pytest.raises(CredentialNotFoundError):
            store.rotate(
                owned.credential_id,
                value="cross-scope-rotation",
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        with pytest.raises(CredentialNotFoundError):
            store.revoke(
                owned.credential_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )

    assert store.get_public(
        owned.credential_id,
        tenant_id="tenant-a",
        owner_id="user-a",
    ).status == "active"
    assert sibling.credential_id != owned.credential_id
    assert other_tenant.credential_id != owned.credential_id


def test_legacy_credentials_migrate_to_explicit_local_scope(tmp_path: Path) -> None:
    storage_dir = tmp_path / "legacy-credentials"
    master_key = Fernet.generate_key()
    original = CredentialStore(storage_dir, master_key=master_key)
    record, _ = original.create(name="Legacy", value="legacy-secret")
    storage_path = storage_dir / "credentials.json"
    payload = json.loads(storage_path.read_text(encoding="utf-8"))
    payload["version"] = "modelmirror-credentials-v1"
    payload["credentials"][0].pop("tenant_id")
    payload["credentials"][0].pop("owner_id")
    storage_path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = CredentialStore(storage_dir, master_key=master_key)
    public = migrated.get_public(record.credential_id)
    assert public.tenant_id == "local"
    assert public.owner_id == "local"
    assert migrated.resolve(record.credential_id) == "legacy-secret"
    with pytest.raises(CredentialNotFoundError):
        migrated.resolve(
            record.credential_id,
            tenant_id="another-tenant",
            owner_id="local",
        )

    persisted = json.loads(storage_path.read_text(encoding="utf-8"))
    assert persisted["version"] == "modelmirror-credentials-v2"
    assert persisted["credentials"][0]["tenant_id"] == "local"
    assert persisted["credentials"][0]["owner_id"] == "local"


def test_external_master_key_can_be_required_without_breaking_dev_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", raising=False)
    monkeypatch.setenv(
        "MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY",
        "true",
    )
    with pytest.raises(CredentialStoreError, match="external credential master key"):
        CredentialStore(tmp_path / "required-key")

    external_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", external_key)
    external = CredentialStore(tmp_path / "external-key")
    assert external.remote_auth_master_key_attestation() == (True, True)
    record, _ = external.create(name="External", value="external-secret")
    assert external.resolve(record.credential_id) == "external-secret"
    assert not external.master_key_path.exists()

    monkeypatch.delenv(
        "MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY",
        raising=False,
    )
    monkeypatch.delenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", raising=False)
    development = CredentialStore(tmp_path / "development-default")
    assert development.remote_auth_master_key_attestation() == (False, False)
    assert development.master_key_path.exists()
