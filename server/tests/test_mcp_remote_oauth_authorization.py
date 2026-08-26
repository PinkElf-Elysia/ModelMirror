from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import httpx
from fastapi import FastAPI

from server.mcp.remote_auth import LocalSubjectScopeResolver
from server.mcp.remote_oauth import (
    MCPRemoteOAuthStore,
    RemoteOAuthError,
    RemoteOAuthPolicyV2,
    MCP_PROTOCOL_VERSION,
)
from server.mcp.remote_oauth_authorization import (
    MCPRemoteOAuthAuthorizationService,
    MCPRemoteOAuthAuthorizationStore,
    configure_mcp_remote_oauth_authorization,
    router,
)
from server.toolsets.credentials import CredentialStore, CredentialStoreError


SOURCE = "a" * 64
RESOURCE = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.net/tenant"
REDIRECT = "http://127.0.0.1:8765/oauth/callback"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FakeBridge:
    def __init__(self) -> None:
        self.exchange_calls = 0
        self.refresh_calls = 0
        self.revoke_calls = 0
        self.exchange_result: dict[str, Any] = {
            "access_token": "access-one",
            "refresh_token": "refresh-one",
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": "mcp.read",
        }
        self.refresh_result: dict[str, Any] = {
            "access_token": "access-two",
            "refresh_token": "refresh-two",
            "token_type": "Bearer",
            "expires_in": 600,
            "scope": "mcp.read",
        }
        self.exchange_error = ""
        self.refresh_error = ""
        self.revoke_error = ""
        self.last_exchange_digest = ""
        self.last_refresh_digest = ""
        self.last_revoke_body: dict[str, str] = {}

    async def exchange_authorization_code(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        self.exchange_calls += 1
        assert target_id.startswith("mcphub_")
        assert url == f"{ISSUER}/token"
        assert request_body["grant_type"] == "authorization_code"
        assert request_body["redirect_uri"] == REDIRECT
        assert request_body["resource"] == RESOURCE
        assert 43 <= len(request_body["code_verifier"]) <= 128
        self.last_exchange_digest = digest(request_body)
        if self.exchange_error:
            raise RemoteOAuthError(
                "exchange failed", code=self.exchange_error, status_code=502
            )
        return dict(self.exchange_result)

    async def refresh_access_token(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        self.refresh_calls += 1
        assert target_id.startswith("mcphub_")
        assert url == f"{ISSUER}/token"
        assert request_body["grant_type"] == "refresh_token"
        assert request_body["resource"] == RESOURCE
        self.last_refresh_digest = digest(request_body)
        if self.refresh_error:
            raise RemoteOAuthError(
                "refresh failed", code=self.refresh_error, status_code=502
            )
        return dict(self.refresh_result)

    async def revoke_token(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        self.revoke_calls += 1
        assert target_id.startswith("mcphub_")
        assert url == f"{ISSUER}/revoke"
        self.last_revoke_body = dict(request_body)
        if self.revoke_error:
            raise RemoteOAuthError(
                "revoke failed", code=self.revoke_error, status_code=503
            )
        return {"remote_status": "completed"}


def configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    MCPRemoteOAuthAuthorizationService,
    MCPRemoteOAuthStore,
    CredentialStore,
    FakeBridge,
    str,
]:
    for name in (
        "MCP_REMOTE_AUTH_ENABLED",
        "MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK",
        "MCP_REMOTE_OAUTH_ENABLED",
        "MCP_REMOTE_OAUTH_AUTHORIZATION_ENABLED",
        "MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    metadata_store = MCPRemoteOAuthStore(tmp_path / "auth")
    subject = LocalSubjectScopeResolver().resolve()
    execution = {
        "schema_version": "remote-oauth-policy-v2",
        "mode": "oauth_authorization_code_pkce",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "resource_uri": RESOURCE,
        "origin": "https://mcp.example.com",
        "remote_url_digest": hashlib.sha256(RESOURCE.encode()).hexdigest(),
        "protected_resource_metadata_url": (
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
        ),
        "protected_resource_metadata_digest": "b" * 64,
        "issuer": ISSUER,
        "authorization_server_metadata_url": (
            "https://auth.example.net/.well-known/oauth-authorization-server/tenant"
        ),
        "authorization_server_metadata_digest": "c" * 64,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": "",
        "revocation_endpoint": f"{ISSUER}/revoke",
        "client_id_metadata_document_supported": False,
        "scopes_supported": ("mcp.read",),
        "scope_source": "protected_resource_metadata",
        "recommended_scopes": ("mcp.read",),
        "recommended_scope_digest": digest(["mcp.read"]),
        "offline_access_available": True,
    }
    policy = RemoteOAuthPolicyV2(
        **execution, policy_fingerprint=digest(execution)
    )
    target_id = "mcphub_" + "1" * 32
    discovery = metadata_store.save_discovery(
        subject=subject,
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        policy=policy,
    )
    metadata_store.save_registration(
        subject=subject,
        discovery=discovery,
        mode="pre_registered",
        client_id="public-client",
        evidence={
            "schema_version": "remote-oauth-registration-evidence-v1",
            "mode": "pre_registered",
            "discovery_fingerprint": discovery.discovery_fingerprint,
        },
    )
    vault = CredentialStore(
        tmp_path / "vault",
        master_key="test-external-master-key-32-bytes",
        require_external_master_key=True,
    )
    bridge = FakeBridge()
    service = MCPRemoteOAuthAuthorizationService(
        MCPRemoteOAuthAuthorizationStore(tmp_path / "auth"),
        metadata_store=metadata_store,
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=lambda: {
            "enabled": True,
            "single_owner_acknowledged": True,
            "external_master_key_available": True,
            "external_master_key_enforced": True,
        },
        redirect_uri=lambda: REDIRECT,
        bridge=bridge,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_rotator=vault.rotate,
        credential_revoker=vault.revoke,
    )
    return service, metadata_store, vault, bridge, target_id


def create_url(
    service: MCPRemoteOAuthAuthorizationService,
    metadata_store: MCPRemoteOAuthStore,
    target_id: str,
) -> tuple[dict[str, Any], str]:
    subject = LocalSubjectScopeResolver().resolve()
    discovery = metadata_store.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    registration = metadata_store.active_registration(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert discovery is not None and registration is not None
    result = service.create_authorization(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovery.discovery_fingerprint,
        expected_registration_digest=service.registration_revision_digest(registration),
        expected_scope_digest=discovery.policy.recommended_scope_digest,
        request_refresh_token=True,
    )
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["resource"] == [RESOURCE]
    assert query["scope"] == ["mcp.read offline_access"]
    assert "code_verifier" not in query
    return result, query["state"][0]


@pytest.mark.asyncio
async def test_authorization_code_is_single_use_and_token_is_only_in_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    created, state = create_url(service, metadata, target_id)
    assert set(created) == {"authorization_session", "authorization_url"}

    completed = await service.callback(state=state, code="one-time-code")
    assert completed.status == "completed"
    assert bridge.exchange_calls == 1
    summary = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )
    assert summary["token"]["stored_encrypted"] is True
    assert summary["token"]["scopes"] == ["mcp.read"]
    assert "credential_id" not in summary["token"]
    assert "authorization_url" not in summary

    persisted = (tmp_path / "auth" / "remote-auth.sqlite3").read_bytes()
    vault_payload = (tmp_path / "vault" / "credentials.json").read_text("utf-8")
    for secret in ("one-time-code", "access-one", "refresh-one", state):
        assert secret.encode() not in persisted
        assert secret not in vault_payload

    with pytest.raises(RemoteOAuthError) as replay:
        await service.callback(state=state, code="second-code")
    assert replay.value.code == "mcp_remote_oauth_state_replay_denied"
    assert bridge.exchange_calls == 1
    assert any(record.status == "active" for record in vault.list())


@pytest.mark.asyncio
async def test_refresh_token_is_discarded_when_operator_did_not_request_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    subject = LocalSubjectScopeResolver().resolve()
    discovery = metadata.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    registration = metadata.active_registration(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert discovery is not None and registration is not None
    created = service.create_authorization(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovery.discovery_fingerprint,
        expected_registration_digest=service.registration_revision_digest(registration),
        expected_scope_digest=discovery.policy.recommended_scope_digest,
        request_refresh_token=False,
    )
    query = parse_qs(urlsplit(created["authorization_url"]).query)
    assert query["scope"] == ["mcp.read"]
    assert "offline_access" not in query["scope"][0]

    await service.callback(
        state=query["state"][0], code="one-time-code", issuer=ISSUER
    )
    assert bridge.exchange_calls == 1
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    assert token["refresh_available"] is False
    with pytest.raises(RemoteOAuthError) as denied:
        await service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
            expected_revision=token["revision"],
        )
    assert denied.value.code == "mcp_remote_oauth_token_stale"
    assert bridge.refresh_calls == 0


@pytest.mark.asyncio
async def test_callback_issuer_mixup_is_denied_before_token_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _created, state = create_url(service, metadata, target_id)
    denied = await service.callback(
        state=state,
        code="one-time-code",
        issuer="https://attacker.example/",
    )
    assert denied.status == "failed"
    assert denied.error_code == "mcp_remote_oauth_issuer_mismatch"
    assert bridge.exchange_calls == 0
    with pytest.raises(RemoteOAuthError) as replay:
        await service.callback(state=state, code="retry", issuer=ISSUER)
    assert replay.value.code == "mcp_remote_oauth_state_replay_denied"


@pytest.mark.asyncio
async def test_execution_resolver_binds_resource_scope_and_token_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, _bridge, target_id = configured(tmp_path, monkeypatch)
    _created, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code", issuer=ISSUER)
    metadata_value = service.execution_metadata(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
    )
    assert metadata_value.resource_uri == RESOURCE
    assert metadata_value.protocol_version == MCP_PROTOCOL_VERSION
    with service.resolve_for_execution(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_policy_fingerprint=metadata_value.policy_fingerprint,
        expected_scope_digest=metadata_value.scope_digest,
        expected_token_revision_digest=metadata_value.token_revision_digest,
    ) as envelope:
        assert envelope.authorization_value == "Bearer access-one"
        captured = envelope
    assert captured.authorization_value == ""
    with pytest.raises(RemoteOAuthError) as stale:
        with service.resolve_for_execution(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_policy_fingerprint=metadata_value.policy_fingerprint,
            expected_scope_digest=metadata_value.scope_digest,
            expected_token_revision_digest="0" * 64,
        ):
            pass
    assert stale.value.code == "mcp_remote_oauth_token_stale"


@pytest.mark.asyncio
async def test_execution_resolver_rejects_token_within_refresh_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _created, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code", issuer=ISSUER)
    database = tmp_path / "auth" / "remote-auth.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE remote_oauth_token_revisions SET expires_at=strftime('%s','now')+60 "
            "WHERE status='active'"
        )

    with pytest.raises(RemoteOAuthError) as denied:
        service.execution_metadata(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
        )

    assert denied.value.code == "mcp_remote_oauth_refresh_required"
    assert bridge.refresh_calls == 0


@pytest.mark.asyncio
async def test_legacy_unbound_token_is_preserved_but_cannot_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, _bridge, target_id = configured(tmp_path, monkeypatch)
    _created, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    path = tmp_path / "auth" / "remote-auth.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE remote_oauth_token_revisions SET resource_bound=0,"
            "resource_uri='',resource_digest='',protocol_version='' WHERE status='active'"
        )
    restarted = MCPRemoteOAuthAuthorizationService(
        MCPRemoteOAuthAuthorizationStore(tmp_path / "auth"),
        metadata_store=metadata,
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=service.remote_auth_status,
        redirect_uri=lambda: REDIRECT,
        bridge=service.bridge,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_rotator=vault.rotate,
        credential_revoker=vault.revoke,
    )
    summary = restarted.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )
    assert summary["token"]["status"] == "legacy_unbound"
    assert summary["token"]["resource_bound"] is False
    assert any(record.status == "active" for record in vault.list())
    with pytest.raises(RemoteOAuthError) as denied:
        restarted.execution_metadata(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
        )
    assert denied.value.code == "mcp_remote_oauth_legacy_token_reauthorization_required"
    revoked = restarted.revoke(
        target_type="hub_candidate",
        target_id=target_id,
        token_id=summary["token"]["token_id"],
    )
    assert revoked.status == "revoked"
    assert all(record.status == "revoked" for record in vault.list())


def test_r2b_database_schema_is_upgraded_additively_and_token_is_quarantined(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "auth"
    storage.mkdir()
    database = storage / "remote-auth.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE remote_oauth_authorization_sessions (
                session_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                subject_mode TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                discovery_fingerprint TEXT NOT NULL,
                registration_id TEXT NOT NULL,
                registration_revision INTEGER NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                state_digest TEXT NOT NULL UNIQUE,
                pkce_credential_id TEXT NOT NULL,
                token_credential_id TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                scope_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT NOT NULL,
                token_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE remote_oauth_token_revisions (
                token_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                subject_mode TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                discovery_fingerprint TEXT NOT NULL,
                registration_id TEXT NOT NULL,
                registration_revision INTEGER NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                credential_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                scopes_json TEXT NOT NULL,
                scope_digest TEXT NOT NULL,
                expires_at REAL,
                refresh_available INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                revoked_at REAL
            );
            """
        )
        db.execute(
            "INSERT INTO remote_oauth_token_revisions VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "mcpoauthtoken_" + "1" * 32,
                "local",
                "local",
                "local-single-owner",
                "hub_candidate",
                "mcphub_" + "1" * 32,
                "a" * 64,
                "mcpoauthreg_" + "1" * 32,
                1,
                "b" * 64,
                "cred_legacy",
                1,
                '["mcp.read"]',
                digest(["mcp.read"]),
                None,
                1,
                "active",
                1.0,
                1.0,
                None,
            ),
        )

    store = MCPRemoteOAuthAuthorizationStore(storage)
    assert store.ready() is True
    with sqlite3.connect(database) as db:
        session_columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(remote_oauth_authorization_sessions)"
            )
        }
        token_columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(remote_oauth_token_revisions)")
        }
        status = db.execute(
            "SELECT status,resource_bound,resource_uri,protocol_version "
            "FROM remote_oauth_token_revisions"
        ).fetchone()
    assert {
        "scope_source",
        "resource_uri",
        "resource_digest",
        "protocol_version",
        "request_refresh_token",
    }.issubset(session_columns)
    assert {
        "scope_source",
        "resource_uri",
        "resource_digest",
        "protocol_version",
        "resource_bound",
    }.issubset(token_columns)
    assert status == ("legacy_unbound", 0, "", "")


@pytest.mark.asyncio
async def test_refresh_rotates_revision_once_and_local_revoke_removes_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]

    refreshed = await service.refresh(
        target_type="hub_candidate",
        target_id=target_id,
        token_id=token["token_id"],
        expected_revision=token["revision"],
    )
    assert refreshed.revision == 2
    assert bridge.refresh_calls == 1
    with pytest.raises(RemoteOAuthError) as stale:
        await service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
            expected_revision=1,
        )
    assert stale.value.code == "mcp_remote_oauth_token_stale"
    assert bridge.refresh_calls == 1

    revoked = service.revoke(
        target_type="hub_candidate", target_id=target_id, token_id=token["token_id"]
    )
    assert revoked.status == "revoked"
    assert service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"] is None
    assert all(record.status == "revoked" for record in vault.list())


@pytest.mark.asyncio
async def test_concurrent_refresh_dispatches_once_and_loser_fails_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_refresh(
        _target_id: str, _url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        bridge.refresh_calls += 1
        assert request_body["refresh_token"] == "refresh-one"
        entered.set()
        await release.wait()
        return dict(bridge.refresh_result)

    bridge.refresh_access_token = controlled_refresh  # type: ignore[method-assign]
    first = asyncio.create_task(
        service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
            expected_revision=token["revision"],
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
            expected_revision=token["revision"],
        )
    )
    release.set()
    assert (await first).revision == 2
    with pytest.raises(RemoteOAuthError) as stale:
        await second
    assert stale.value.code == "mcp_remote_oauth_token_stale"
    assert bridge.refresh_calls == 1


@pytest.mark.asyncio
async def test_cross_instance_refresh_claim_dispatches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    second = MCPRemoteOAuthAuthorizationService(
        MCPRemoteOAuthAuthorizationStore(tmp_path / "auth"),
        metadata_store=metadata,
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=service.remote_auth_status,
        redirect_uri=lambda: REDIRECT,
        bridge=bridge,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_rotator=vault.rotate,
        credential_revoker=vault.revoke,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_refresh(
        _target_id: str, _url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        bridge.refresh_calls += 1
        entered.set()
        await release.wait()
        return dict(bridge.refresh_result)

    bridge.refresh_access_token = controlled_refresh  # type: ignore[method-assign]
    kwargs = dict(
        target_type="hub_candidate",
        target_id=target_id,
        token_id=token["token_id"],
        expected_revision=token["revision"],
    )
    first = asyncio.create_task(service.refresh(**kwargs))
    await entered.wait()
    with pytest.raises(RemoteOAuthError) as duplicate:
        await second.refresh(**kwargs)
    assert duplicate.value.code == "mcp_remote_oauth_refresh_in_progress"
    assert bridge.refresh_calls == 1
    release.set()
    assert (await first).revision == 2


@pytest.mark.asyncio
async def test_active_token_blocks_new_authorization_before_remote_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    active_before = [record for record in vault.list() if record.status == "active"]

    with pytest.raises(RemoteOAuthError) as conflict:
        create_url(service, metadata, target_id)
    assert conflict.value.code == "mcp_remote_oauth_token_conflict"
    assert bridge.exchange_calls == 1
    assert [record for record in vault.list() if record.status == "active"] == active_before


@pytest.mark.asyncio
async def test_scope_escalation_and_ambiguous_exchange_fail_closed_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    bridge.exchange_result["scope"] = "mcp.read admin.write"
    with pytest.raises(RemoteOAuthError) as escalation:
        await service.callback(state=state, code="one-time-code")
    assert escalation.value.code == "mcp_remote_oauth_scope_escalation_denied"
    assert bridge.exchange_calls == 1
    summary = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )
    assert summary["authorization_session"]["status"] == "unknown_outcome"
    assert summary["token"] is None

    with pytest.raises(RemoteOAuthError) as replay:
        await service.callback(state=state, code="retry")
    assert replay.value.code == "mcp_remote_oauth_state_replay_denied"
    assert bridge.exchange_calls == 1


def test_scope_is_server_owned_and_stale_digests_are_rejected_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    subject = LocalSubjectScopeResolver().resolve()
    discovery = metadata.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    registration = metadata.active_registration(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert discovery and registration
    with pytest.raises(RemoteOAuthError) as stale_scope:
        service.create_authorization(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovery.discovery_fingerprint,
            expected_registration_digest=service.registration_revision_digest(
                registration
            ),
            expected_scope_digest="0" * 64,
        )
    assert stale_scope.value.code == "mcp_remote_oauth_scope_invalid"
    normalized = service.create_authorization(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovery.discovery_fingerprint,
        expected_registration_digest=service.registration_revision_digest(registration),
        expected_scope_digest=discovery.policy.recommended_scope_digest,
    )
    assert normalized["authorization_session"]["scopes"] == ["mcp.read"]
    assert bridge.exchange_calls == 0


def test_authorization_flags_and_external_master_key_fail_closed_before_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    monkeypatch.setenv("MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED", "false")
    with pytest.raises(RemoteOAuthError) as disabled:
        create_url(service, metadata, target_id)
    assert disabled.value.code == "mcp_remote_oauth_token_storage_disabled"

    monkeypatch.setenv("MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED", "true")
    service.remote_auth_status = lambda: {
        "enabled": True,
        "single_owner_acknowledged": True,
        "external_master_key_available": False,
        "external_master_key_enforced": True,
    }
    with pytest.raises(RemoteOAuthError) as no_key:
        create_url(service, metadata, target_id)
    assert no_key.value.code == "mcp_remote_auth_master_key_required"
    assert vault.list() == []
    assert bridge.exchange_calls == 0


def test_restart_converts_dispatched_exchange_to_unknown_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    created, state = create_url(service, metadata, target_id)
    subject = LocalSubjectScopeResolver().resolve()
    started = service.store.claim_state(
        hashlib.sha256(state.encode()).hexdigest(), subject=subject
    )
    vault.create(
        name="simulated post-exchange crash",
        value=json.dumps(
            {"access_token": "orphan-access", "refresh_token": "orphan-refresh"}
        ),
        credential_id=started.token_credential_id,
        tenant_id=subject.tenant_id,
        owner_id=subject.owner_id,
    )
    restarted_store = MCPRemoteOAuthAuthorizationStore(tmp_path / "auth")
    MCPRemoteOAuthAuthorizationService(
        restarted_store,
        metadata_store=metadata,
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=service.remote_auth_status,
        redirect_uri=lambda: REDIRECT,
        bridge=bridge,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_rotator=vault.rotate,
        credential_revoker=vault.revoke,
    )
    recovered = restarted_store.session(
        created["authorization_session"]["session_id"], subject=subject
    )
    assert recovered.status == "unknown_outcome"
    assert recovered.error_code == "mcp_remote_oauth_token_exchange_unknown_outcome"
    assert all(record.status == "revoked" for record in vault.list())


@pytest.mark.asyncio
async def test_restart_converts_claimed_refresh_to_unknown_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    subject = LocalSubjectScopeResolver().resolve()
    token = service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    discovery = metadata.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    registration = metadata.active_registration(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert token is not None and discovery is not None and registration is not None
    attempt = service.store.claim_refresh(
        subject=subject,
        token=token,
        discovery=discovery,
        registration=registration,
    )

    restarted_store = MCPRemoteOAuthAuthorizationStore(tmp_path / "auth")
    MCPRemoteOAuthAuthorizationService(
        restarted_store,
        metadata_store=metadata,
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=service.remote_auth_status,
        redirect_uri=lambda: REDIRECT,
        bridge=bridge,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_rotator=vault.rotate,
        credential_revoker=vault.revoke,
    )
    assert restarted_store.refresh_attempt(
        attempt.attempt_id, subject=subject
    ).status == "unknown_outcome"
    assert restarted_store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ) is None
    assert all(record.status == "revoked" for record in vault.list())


def test_vault_reserved_credential_id_rejects_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service, _metadata, vault, _bridge, _target_id = configured(
        tmp_path, monkeypatch
    )
    reserved = "cred_" + "a" * 32
    vault.create(name="first", value="first-secret", credential_id=reserved)
    with pytest.raises(CredentialStoreError):
        vault.create(name="collision", value="second-secret", credential_id=reserved)
    assert vault.resolve(reserved) == "first-secret"


def test_started_exchange_cannot_be_cancelled_and_drift_becomes_unknown_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, _bridge, target_id = configured(tmp_path, monkeypatch)
    created, state = create_url(service, metadata, target_id)
    subject = LocalSubjectScopeResolver().resolve()
    started = service.store.claim_state(
        hashlib.sha256(state.encode()).hexdigest(), subject=subject
    )
    assert started.status == "started"

    with pytest.raises(RemoteOAuthError) as cancel:
        service.cancel(
            target_type="hub_candidate",
            target_id=target_id,
            session_id=created["authorization_session"]["session_id"],
        )
    assert cancel.value.code == "mcp_remote_oauth_state_replay_denied"
    service.invalidate_target(target_type="hub_candidate", target_id=target_id)
    invalidated = service.store.session(started.session_id, subject=subject)
    assert invalidated.status == "unknown_outcome"
    assert invalidated.error_code == "mcp_remote_oauth_token_exchange_unknown_outcome"


@pytest.mark.asyncio
async def test_discovery_drift_during_exchange_revokes_new_secret_and_publishes_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    subject = LocalSubjectScopeResolver().resolve()
    current = metadata.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert current is not None

    async def drift_then_return(
        _target_id: str, _url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        bridge.exchange_calls += 1
        metadata.save_discovery(
            subject=subject,
            target_type="hub_candidate",
            target_id=target_id,
            source_digest="d" * 64,
            policy=current.policy,
        )
        service.invalidate_target(
            target_type="hub_candidate", target_id=target_id
        )
        request_body["code"] = ""
        request_body["code_verifier"] = ""
        return dict(bridge.exchange_result)

    bridge.exchange_authorization_code = drift_then_return  # type: ignore[method-assign]
    with pytest.raises(RemoteOAuthError) as drifted:
        await service.callback(state=state, code="one-time-code")
    assert drifted.value.code == "mcp_remote_oauth_discovery_stale"
    assert bridge.exchange_calls == 1
    assert service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ) is None
    assert service.store.latest_session(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ).status == "unknown_outcome"
    assert all(record.status == "revoked" for record in vault.list())


@pytest.mark.asyncio
async def test_clear_refresh_rejection_stales_token_and_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    bridge.refresh_error = "mcp_remote_oauth_unauthorized"
    with pytest.raises(RemoteOAuthError) as rejected:
        await service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
            expected_revision=token["revision"],
        )
    assert rejected.value.code == "mcp_remote_oauth_unauthorized"
    assert bridge.refresh_calls == 1
    assert service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"] is None


@pytest.mark.asyncio
async def test_malformed_successful_refresh_is_unknown_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    subject = LocalSubjectScopeResolver().resolve()
    token = service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert token is not None
    bridge.refresh_result = {"token_type": "Bearer", "scope": "mcp.read"}

    with pytest.raises(RemoteOAuthError) as malformed:
        await service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token.token_id,
            expected_revision=token.revision,
        )
    assert malformed.value.code == "mcp_remote_oauth_refresh_unknown_outcome"
    assert bridge.refresh_calls == 1
    persisted = service.store._token_by_id(token.token_id, subject=subject)
    assert persisted.status == "unknown_outcome"
    with service.store._connect() as db:
        attempt = db.execute(
            "SELECT status,error_code FROM remote_oauth_refresh_attempts "
            "WHERE token_id=? ORDER BY created_at DESC LIMIT 1",
            (token.token_id,),
        ).fetchone()
    assert attempt is not None
    assert attempt["status"] == "unknown_outcome"
    assert attempt["error_code"] == "mcp_remote_oauth_refresh_unknown_outcome"
    assert all(record.status == "revoked" for record in vault.list())


@pytest.mark.asyncio
async def test_discovery_drift_during_refresh_cannot_publish_new_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    subject = LocalSubjectScopeResolver().resolve()
    token = service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    discovery = metadata.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert token is not None and discovery is not None

    async def drift_then_return(
        _target_id: str, _url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        bridge.refresh_calls += 1
        metadata.save_discovery(
            subject=subject,
            target_type="hub_candidate",
            target_id=target_id,
            source_digest="d" * 64,
            policy=discovery.policy,
        )
        return dict(bridge.refresh_result)

    bridge.refresh_access_token = drift_then_return  # type: ignore[method-assign]
    with pytest.raises(RemoteOAuthError) as drifted:
        await service.refresh(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token.token_id,
            expected_revision=token.revision,
        )
    assert drifted.value.code == "mcp_remote_oauth_refresh_unknown_outcome"
    assert bridge.refresh_calls == 1
    assert service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ) is None
    assert all(record.status == "revoked" for record in vault.list())


@pytest.mark.asyncio
async def test_callback_rejects_query_injection_without_echoing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _metadata, _vault, bridge, _target_id = configured(tmp_path, monkeypatch)
    configure_mcp_remote_oauth_authorization(service)
    app = FastAPI()
    app.include_router(router)
    observed_query: bytes | None = None

    async def access_log_probe(scope: Any, receive: Any, send: Any) -> None:
        async def observe_start(message: Any) -> None:
            nonlocal observed_query
            if message["type"] == "http.response.start":
                observed_query = scope.get("query_string")
            await send(message)

        await app(scope, receive, observe_start)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=access_log_probe),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/oauth/callback",
            params={
                "state": "s" * 48,
                "code": "one-time-code",
                "token_endpoint": "https://attacker.example/token",
            },
        )
    assert response.status_code == 400
    assert "attacker.example" not in response.text
    assert "one-time-code" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert observed_query == b""
    assert bridge.exchange_calls == 0


@pytest.mark.asyncio
async def test_remote_revocation_disabled_degrades_to_local_only_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED", "false")
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    result = await service.revoke_with_remote(
        target_type="hub_candidate", target_id=target_id, token_id=token["token_id"]
    )
    assert result["local_revocation"] == "completed"
    assert result["remote_revocation"] == "local_only"
    assert bridge.revoke_calls == 0
    assert all(record.status == "revoked" for record in vault.list())


@pytest.mark.asyncio
async def test_remote_revocation_prefers_refresh_token_and_never_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED", "true")
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    result = await service.revoke_with_remote(
        target_type="hub_candidate", target_id=target_id, token_id=token["token_id"]
    )
    assert result["local_revocation"] == "completed"
    assert result["remote_revocation"] == "completed"
    assert bridge.revoke_calls == 1
    assert bridge.last_revoke_body == {
        "token": "refresh-one",
        "token_type_hint": "refresh_token",
        "client_id": "public-client",
    }
    assert all(record.status == "revoked" for record in vault.list())
    with pytest.raises(RemoteOAuthError) as replay:
        await service.revoke_with_remote(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
        )
    assert replay.value.code == "mcp_remote_oauth_token_missing"
    assert bridge.revoke_calls == 1


@pytest.mark.asyncio
async def test_remote_revocation_unknown_outcome_still_removes_local_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED", "true")
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    bridge.revoke_error = "mcp_remote_oauth_revocation_unknown_outcome"
    result = await service.revoke_with_remote(
        target_type="hub_candidate", target_id=target_id, token_id=token["token_id"]
    )
    assert result["local_revocation"] == "completed"
    assert result["remote_revocation"] == "unknown_outcome"
    subject = LocalSubjectScopeResolver().resolve()
    attempt = service.store.revocation_attempt(result["attempt_id"], subject=subject)
    assert attempt.status == "unknown_outcome"
    assert attempt.error_code == "mcp_remote_oauth_revocation_unknown_outcome"
    assert service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ) is None
    assert all(record.status == "revoked" for record in vault.list())
    assert bridge.revoke_calls == 1


@pytest.mark.asyncio
async def test_revocation_is_rejected_while_same_revision_is_refreshing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED", "true")
    service, metadata, _vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_refresh(
        _target_id: str, _url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        bridge.refresh_calls += 1
        entered.set()
        await release.wait()
        return dict(bridge.refresh_result)

    bridge.refresh_access_token = controlled_refresh  # type: ignore[method-assign]
    refreshing = asyncio.create_task(service.refresh(
        target_type="hub_candidate",
        target_id=target_id,
        token_id=token["token_id"],
        expected_revision=token["revision"],
    ))
    await entered.wait()
    with pytest.raises(RemoteOAuthError) as concurrent:
        await service.revoke_with_remote(
            target_type="hub_candidate", target_id=target_id, token_id=token["token_id"]
        )
    assert concurrent.value.code == "mcp_remote_oauth_refresh_in_progress"
    assert bridge.revoke_calls == 0
    release.set()
    assert (await refreshing).revision == 2


@pytest.mark.asyncio
async def test_cancelled_remote_revocation_is_unknown_and_clears_local_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED", "true")
    service, metadata, vault, bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    await service.callback(state=state, code="one-time-code")
    token = service.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )["token"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_revoke(
        _target_id: str, _url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        bridge.revoke_calls += 1
        bridge.last_revoke_body = dict(request_body)
        entered.set()
        await release.wait()
        return {"remote_status": "completed"}

    bridge.revoke_token = controlled_revoke  # type: ignore[method-assign]
    revoking = asyncio.create_task(
        service.revoke_with_remote(
            target_type="hub_candidate",
            target_id=target_id,
            token_id=token["token_id"],
        )
    )
    await entered.wait()
    revoking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await revoking
    subject = LocalSubjectScopeResolver().resolve()
    with service.store._connect() as db:
        row = db.execute(
            "SELECT attempt_id FROM remote_oauth_revocation_attempts "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    attempt = service.store.revocation_attempt(row["attempt_id"], subject=subject)
    assert attempt.status == "unknown_outcome"
    assert attempt.error_code == "mcp_remote_oauth_revocation_unknown_outcome"
    assert service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ) is None
    assert all(record.status == "revoked" for record in vault.list())
    assert bridge.revoke_calls == 1


def test_restart_converts_claimed_revocation_to_unknown_and_clears_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, metadata, vault, _bridge, target_id = configured(tmp_path, monkeypatch)
    _, state = create_url(service, metadata, target_id)
    asyncio.run(service.callback(state=state, code="one-time-code"))
    subject = LocalSubjectScopeResolver().resolve()
    token = service.store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    discovery = metadata.active_discovery(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    registration = metadata.active_registration(
        subject=subject, target_type="hub_candidate", target_id=target_id
    )
    assert token is not None and discovery is not None and registration is not None
    attempt = service.store.claim_revocation(
        subject=subject,
        token=token,
        discovery=discovery,
        registration=registration,
        revocation_endpoint_digest=hashlib.sha256(
            discovery.policy.revocation_endpoint.encode()
        ).hexdigest(),
        token_type_hint="refresh_token",
    )
    restarted_store = MCPRemoteOAuthAuthorizationStore(tmp_path / "auth")
    MCPRemoteOAuthAuthorizationService(
        restarted_store,
        metadata_store=metadata,
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=service.remote_auth_status,
        redirect_uri=lambda: REDIRECT,
        bridge=service.bridge,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_rotator=vault.rotate,
        credential_revoker=vault.revoke,
    )
    recovered = restarted_store.revocation_attempt(attempt.attempt_id, subject=subject)
    assert recovered.status == "unknown_outcome"
    assert recovered.error_code == "mcp_remote_oauth_revocation_unknown_outcome"
    assert restarted_store.active_token(
        subject=subject, target_type="hub_candidate", target_id=target_id
    ) is None
    assert all(record.status == "revoked" for record in vault.list())
