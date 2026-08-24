from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from server.mcp.remote_auth import LocalSubjectScopeResolver, SubjectScopeV1
from server.mcp.remote_oauth import (
    MCPRemoteOAuthService,
    MCPRemoteOAuthStore,
    RemoteOAuthError,
    RemoteOAuthSocketBridge,
    _authorization_server_well_known_urls,
    normalize_oauth_url,
)


RESOURCE = "https://mcp.example.com/mcp"
PRM = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
ISSUER = "https://auth.example.net/tenant"
AS_METADATA = "https://auth.example.net/.well-known/oauth-authorization-server/tenant"
SOURCE = "a" * 64
REDIRECT_URI = "http://127.0.0.1:8765/oauth/callback"


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.resource = RESOURCE
        self.registration_contains_secret = False
        self.registration_error_code = ""
        self.client_metadata_auth_method = "none"
        self.client_metadata_document: dict[str, Any] | None = None
        self.client_metadata_document_supported = True

    async def probe_resource(self, target_id: str, url: str) -> dict[str, Any]:
        self.calls.append(("probe", url))
        return {"resource_metadata_url": PRM, "status_class": "4xx"}

    async def fetch_json(
        self, target_id: str, url: str, *, document_kind: str
    ) -> dict[str, Any]:
        self.calls.append((document_kind, url))
        if document_kind == "protected_resource_metadata":
            assert url == PRM
            return {
                "document": {
                    "resource": self.resource,
                    "authorization_servers": [ISSUER],
                    "scopes_supported": ["mcp:read"],
                }
            }
        if document_kind == "authorization_server_metadata":
            assert url == AS_METADATA
            return {
                "document": {
                    "issuer": ISSUER,
                    "authorization_endpoint": "https://auth.example.net/authorize",
                    "token_endpoint": "https://auth.example.net/token",
                    "registration_endpoint": "https://auth.example.net/register",
                    "revocation_endpoint": "https://auth.example.net/revoke",
                    "code_challenge_methods_supported": ["S256"],
                    "grant_types_supported": ["authorization_code"],
                    "response_types_supported": ["code"],
                    "client_id_metadata_document_supported": self.client_metadata_document_supported,
                    "scopes_supported": ["mcp:read"],
                }
            }
        if document_kind == "client_id_metadata_document":
            return {
                "document": self.client_metadata_document or {
                    "client_id": url,
                    "client_name": "ModelMirror local MCP OAuth",
                    "redirect_uris": [REDIRECT_URI],
                    "grant_types": ["authorization_code"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": self.client_metadata_auth_method,
                },
                "document_digest": "f" * 64,
            }
        raise AssertionError(document_kind)

    async def register_public_client(
        self, target_id: str, url: str, *, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("register", url))
        assert request_body["token_endpoint_auth_method"] == "none"
        if self.registration_error_code:
            raise RemoteOAuthError(
                "ambiguous registration",
                code=self.registration_error_code,
                status_code=502,
            )
        return {
            "client_id": "dynamic-public-client",
            "contains_secret": self.registration_contains_secret,
            "registration_response_digest": "e" * 64,
        }


def enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK", "true")
    monkeypatch.setenv("MCP_REMOTE_OAUTH_ENABLED", "true")


def service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bridge: FakeBridge | None = None,
) -> MCPRemoteOAuthService:
    enable(monkeypatch)
    return MCPRemoteOAuthService(
        MCPRemoteOAuthStore(tmp_path),
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=lambda: {
            "enabled": True,
            "single_owner_acknowledged": True,
            "external_master_key_available": True,
            "external_master_key_enforced": True,
        },
        bridge=bridge or FakeBridge(),
    )


def test_oauth_url_policy_rejects_client_controlled_network_shapes() -> None:
    assert normalize_oauth_url("https://MCP.Example.com:443/mcp") == RESOURCE
    for denied in (
        "http://mcp.example.com/mcp",
        "https://mcp.example.com:8443/mcp",
        "https://user@mcp.example.com/mcp",
        "https://mcp.example.com/mcp?next=x",
        "https://mcp.example.com/mcp#fragment",
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
        "https://{tenant}.example.com/mcp",
    ):
        with pytest.raises(RemoteOAuthError) as captured:
            normalize_oauth_url(denied)
        assert captured.value.code == "mcp_remote_oauth_metadata_invalid"


def test_authorization_server_discovery_includes_both_oidc_path_forms() -> None:
    assert _authorization_server_well_known_urls(ISSUER) == (
        "https://auth.example.net/.well-known/oauth-authorization-server/tenant",
        "https://auth.example.net/.well-known/openid-configuration/tenant",
        "https://auth.example.net/tenant/.well-known/openid-configuration",
    )


@pytest.mark.asyncio
async def test_socket_bridge_maps_invalid_post_write_registration_reply_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reader:
        async def readline(self) -> bytes:
            return b"not-json\n"

    class Writer:
        def write(self, _value: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def open_socket(_path: str) -> tuple[Reader, Writer]:
        return Reader(), Writer()

    monkeypatch.setattr(asyncio, "open_unix_connection", open_socket)
    bridge = RemoteOAuthSocketBridge(oauth_socket="oauth.sock", egress_socket="egress.sock")
    with pytest.raises(RemoteOAuthError) as unknown:
        await bridge._request(
            "oauth.sock",
            {"action": "register_public_client"},
            ambiguous_after_write=True,
        )
    assert unknown.value.code == "mcp_remote_oauth_registration_unknown_outcome"


@pytest.mark.asyncio
async def test_discovery_freezes_resource_issuer_pkce_and_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)

    snapshot = await current.discover(
        target_type="hub_candidate",
        target_id="mcphub_" + "1" * 32,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )

    assert snapshot.policy.resource_uri == RESOURCE
    assert snapshot.policy.issuer == ISSUER
    assert snapshot.policy.authorization_endpoint.endswith("/authorize")
    assert snapshot.policy.scopes_supported == ("mcp:read",)
    assert len(snapshot.policy.policy_fingerprint) == 64
    assert bridge.calls[:3] == [
        ("probe", RESOURCE),
        ("protected_resource_metadata", PRM),
        ("authorization_server_metadata", AS_METADATA),
    ]
    summary = current.summary(
        target_type="hub_candidate",
        target_id=snapshot.target_id,
        source_digest=SOURCE,
    )
    assert summary["discovery"]["pkce_method"] == "S256"
    assert summary["authorization_enabled"] is False
    assert summary["token_storage_enabled"] is False


@pytest.mark.asyncio
async def test_discovery_rejects_resource_issuer_and_pkce_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    bridge.resource = "https://other.example.com/mcp"
    with pytest.raises(RemoteOAuthError) as mismatch:
        await current.discover(
            target_type="hub_candidate",
            target_id="mcphub_" + "2" * 32,
            resource_url=RESOURCE,
            source_digest=SOURCE,
        )
    assert mismatch.value.code == "mcp_remote_oauth_resource_mismatch"

    metadata = {
        "issuer": ISSUER,
        "authorization_endpoint": "https://auth.example.net/authorize",
        "token_endpoint": "https://auth.example.net/token",
        "code_challenge_methods_supported": ["plain"],
        "grant_types_supported": ["authorization_code"],
        "response_types_supported": ["code"],
    }
    with pytest.raises(RemoteOAuthError) as pkce:
        current._policy(
            resource=RESOURCE,
            protected_url=PRM,
            protected={"resource": RESOURCE},
            protected_digest="d" * 64,
            issuer=ISSUER,
            metadata_url=AS_METADATA,
            metadata=metadata,
            metadata_digest="e" * 64,
        )
    assert pkce.value.code == "mcp_remote_oauth_pkce_s256_required"


@pytest.mark.asyncio
async def test_registration_is_public_only_and_discovery_drift_stales_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    target_id = "mcphub_" + "3" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    registered = await current.register_client(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovered.discovery_fingerprint,
        mode="pre_registered",
        client_id="operator-provided-public-client",
    )
    assert registered.status == "active"

    bridge.resource = RESOURCE
    drifted = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest="b" * 64,
    )
    assert drifted.discovery_fingerprint != discovered.discovery_fingerprint
    assert current.store.active_registration(
        subject=LocalSubjectScopeResolver().resolve(),
        target_type="hub_candidate",
        target_id=target_id,
    ) is None


@pytest.mark.asyncio
async def test_client_id_metadata_document_is_server_configured_and_public_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata_url = "https://client.example.com/.well-known/oauth-client/modelmirror"
    monkeypatch.setenv("MCP_REMOTE_OAUTH_CLIENT_METADATA_URL", metadata_url)
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REDIRECT_URI", REDIRECT_URI)
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    target_id = "mcphub_" + "6" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    with pytest.raises(RemoteOAuthError) as injection:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="client_id_metadata_document",
            client_id="https://attacker.example/client",
        )
    assert injection.value.code == "mcp_remote_oauth_client_metadata_injection_denied"

    registered = await current.register_client(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovered.discovery_fingerprint,
        mode="client_id_metadata_document",
    )
    assert registered.client_id == metadata_url
    assert registered.mode == "client_id_metadata_document"
    assert ("client_id_metadata_document", metadata_url) in bridge.calls

    monkeypatch.setenv(
        "MCP_REMOTE_OAUTH_REDIRECT_URI", "http://127.0.0.1:9876/oauth/callback"
    )
    summary = current.summary(
        target_type="hub_candidate", target_id=target_id, source_digest=SOURCE
    )
    assert summary["registration"] is None


@pytest.mark.asyncio
async def test_client_id_metadata_document_requires_complete_fixed_public_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata_url = "https://client.example.com/.well-known/oauth-client/modelmirror"
    monkeypatch.setenv("MCP_REMOTE_OAUTH_CLIENT_METADATA_URL", metadata_url)
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REDIRECT_URI", REDIRECT_URI)
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    target_id = "mcphub_" + "9" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    bridge.client_metadata_document = {
        "client_id": metadata_url,
        "token_endpoint_auth_method": "none",
    }
    with pytest.raises(RemoteOAuthError) as incomplete:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="client_id_metadata_document",
        )
    assert incomplete.value.code == "mcp_remote_oauth_client_metadata_invalid"

    bridge.client_metadata_document = None
    bridge.client_metadata_document_supported = False
    other = service(tmp_path / "other", monkeypatch, bridge=bridge)
    other_discovery = await other.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    with pytest.raises(RemoteOAuthError) as unsupported:
        await other.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=other_discovery.discovery_fingerprint,
            mode="client_id_metadata_document",
        )
    assert unsupported.value.code == "mcp_remote_oauth_client_metadata_unsupported"


@pytest.mark.asyncio
async def test_dynamic_registration_unknown_outcome_blocks_service_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    monkeypatch.setenv("MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv(
        "MCP_REMOTE_OAUTH_REDIRECT_URI", "http://127.0.0.1:8765/oauth/callback"
    )
    bridge.registration_error_code = "mcp_remote_oauth_registration_unknown_outcome"
    target_id = "mcphub_" + "7" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    with pytest.raises(RemoteOAuthError) as unknown:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert unknown.value.code == "mcp_remote_oauth_registration_unknown_outcome"
    with pytest.raises(RemoteOAuthError) as replay:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert replay.value.code == "mcp_remote_oauth_registration_replay_denied"
    assert len([call for call in bridge.calls if call[0] == "register"]) == 1


@pytest.mark.asyncio
async def test_dynamic_registration_any_started_attempt_blocks_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    monkeypatch.setenv("MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REDIRECT_URI", REDIRECT_URI)
    bridge.registration_error_code = "mcp_remote_oauth_sidecar_unavailable"
    target_id = "mcphub_" + "0" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    with pytest.raises(RemoteOAuthError) as first:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert first.value.code == "mcp_remote_oauth_sidecar_unavailable"
    with pytest.raises(RemoteOAuthError) as replay:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert replay.value.code == "mcp_remote_oauth_registration_replay_denied"
    assert len([call for call in bridge.calls if call[0] == "register"]) == 1


@pytest.mark.asyncio
async def test_registration_cas_rejects_old_discovery_after_cross_service_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingBridge(FakeBridge):
        async def register_public_client(
            self, target_id: str, url: str, *, request_body: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append(("register", url))
            started.set()
            await release.wait()
            return {
                "client_id": "stale-dynamic-client",
                "contains_secret": False,
                "registration_response_digest": "e" * 64,
            }

    monkeypatch.setenv("MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REDIRECT_URI", REDIRECT_URI)
    first_bridge = BlockingBridge()
    first = service(tmp_path, monkeypatch, bridge=first_bridge)
    second = service(tmp_path, monkeypatch, bridge=FakeBridge())
    target_id = "mcphub_" + "d" * 32
    old = await first.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    task = asyncio.create_task(
        first.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=old.discovery_fingerprint,
            mode="dynamic",
        )
    )
    await started.wait()
    current = await second.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest="b" * 64,
    )
    release.set()
    with pytest.raises(RemoteOAuthError) as stale:
        await task
    assert stale.value.code == "mcp_remote_oauth_discovery_stale"
    summary = second.summary(
        target_type="hub_candidate", target_id=target_id, source_digest="b" * 64
    )
    assert summary["discovery"]["discovery_fingerprint"] == current.discovery_fingerprint
    assert summary["registration"] is None


@pytest.mark.asyncio
async def test_oauth_store_restart_persists_registration_and_denies_other_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = service(tmp_path, monkeypatch)
    target_id = "mcphub_" + "8" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    registration = await current.register_client(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovered.discovery_fingerprint,
        mode="pre_registered",
        client_id="public-client-after-restart",
    )
    restarted = MCPRemoteOAuthStore(tmp_path)
    subject = LocalSubjectScopeResolver().resolve()
    persisted = restarted.active_registration(
        subject=subject,
        target_type="hub_candidate",
        target_id=target_id,
    )
    assert persisted is not None
    assert persisted.registration_id == registration.registration_id
    with pytest.raises(RemoteOAuthError) as other_owner:
        restarted.revoke_registration(
            registration.registration_id,
            subject=SubjectScopeV1(tenant_id="other", owner_id="other"),
            target_type="hub_candidate",
            target_id=target_id,
        )
    assert other_owner.value.code == "mcp_remote_oauth_scope_denied"

    encoded_status = json.dumps(current.status(), sort_keys=True)
    assert "tenant_id" not in encoded_status
    assert "owner_id" not in encoded_status
    assert str(tmp_path) not in encoded_status


@pytest.mark.asyncio
async def test_registration_evidence_fingerprint_fails_closed_on_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = service(tmp_path, monkeypatch)
    target_id = "mcphub_" + "c" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    registered = await current.register_client(
        target_type="hub_candidate",
        target_id=target_id,
        source_digest=SOURCE,
        expected_discovery_fingerprint=discovered.discovery_fingerprint,
        mode="pre_registered",
        client_id="public-client",
    )
    with sqlite3.connect(current.store.path) as db:
        db.execute(
            "UPDATE remote_oauth_registration_evidence SET evidence_json=? "
            "WHERE registration_id=?",
            ('{"mode":"pre_registered"}', registered.registration_id),
        )
    with pytest.raises(RemoteOAuthError) as corrupt:
        current.summary(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
        )
    assert corrupt.value.code == "mcp_remote_oauth_storage_corrupt"


@pytest.mark.asyncio
async def test_dynamic_registration_is_default_off_non_retryable_and_secret_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge()
    current = service(tmp_path, monkeypatch, bridge=bridge)
    target_id = "mcphub_" + "4" * 32
    discovered = await current.discover(
        target_type="hub_candidate",
        target_id=target_id,
        resource_url=RESOURCE,
        source_digest=SOURCE,
    )
    with pytest.raises(RemoteOAuthError) as disabled:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert disabled.value.code == "mcp_remote_oauth_dynamic_registration_disabled"
    assert not [call for call in bridge.calls if call[0] == "register"]

    monkeypatch.setenv("MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv(
        "MCP_REMOTE_OAUTH_REDIRECT_URI", "http://127.0.0.1:8765/oauth/callback"
    )
    bridge.registration_contains_secret = True
    with pytest.raises(RemoteOAuthError) as secret:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert secret.value.code == "mcp_remote_oauth_registration_secret_denied"
    assert len([call for call in bridge.calls if call[0] == "register"]) == 1
    with pytest.raises(RemoteOAuthError) as replay:
        await current.register_client(
            target_type="hub_candidate",
            target_id=target_id,
            source_digest=SOURCE,
            expected_discovery_fingerprint=discovered.discovery_fingerprint,
            mode="dynamic",
        )
    assert replay.value.code == "mcp_remote_oauth_registration_replay_denied"
    assert len([call for call in bridge.calls if call[0] == "register"]) == 1


def test_dynamic_registration_rejects_malformed_loopback_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MCP_REMOTE_OAUTH_REDIRECT_URI", "http://127.0.0.1:bad/oauth/callback"
    )
    with pytest.raises(RemoteOAuthError) as invalid:
        MCPRemoteOAuthService._redirect_uri()
    assert invalid.value.code == "mcp_remote_oauth_redirect_uri_invalid"


def test_operational_gate_requires_remote_auth_ack_master_key_and_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "MCP_REMOTE_AUTH_ENABLED",
        "MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK",
        "MCP_REMOTE_OAUTH_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    current = MCPRemoteOAuthService(
        MCPRemoteOAuthStore(tmp_path),
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=lambda: {
            "enabled": False,
            "single_owner_acknowledged": False,
            "external_master_key_available": False,
            "external_master_key_enforced": False,
        },
        bridge=FakeBridge(),
    )
    with pytest.raises(RemoteOAuthError) as disabled:
        current.summary(
            target_type="hub_candidate",
            target_id="mcphub_" + "5" * 32,
            source_digest=SOURCE,
        )
    assert disabled.value.code == "mcp_remote_auth_disabled"
