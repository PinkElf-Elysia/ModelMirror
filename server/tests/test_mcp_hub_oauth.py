from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from server.mcp.hub import (
    MCPHubService,
    MCPHubStore,
    configure_mcp_hub,
    normalize_registry_entry,
    router,
)
from server.mcp.remote_auth import LocalSubjectScopeResolver
from server.mcp.remote_oauth import MCPRemoteOAuthService, MCPRemoteOAuthStore


RESOURCE = "https://mcp.example.com/mcp"
PRM = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
ISSUER = "https://auth.example.net/"


class Bridge:
    async def probe_resource(self, target_id: str, url: str) -> dict[str, Any]:
        return {"resource_metadata_url": PRM}

    async def fetch_json(
        self, target_id: str, url: str, *, document_kind: str
    ) -> dict[str, Any]:
        if document_kind == "protected_resource_metadata":
            return {
                "document": {
                    "resource": RESOURCE,
                    "authorization_servers": [ISSUER],
                    "scopes_supported": ["mcp:read"],
                }
            }
        return {
            "document": {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}authorize",
                "token_endpoint": f"{ISSUER}token",
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code"],
                "response_types_supported": ["code"],
            }
        }

    async def register_public_client(
        self, target_id: str, url: str, *, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        raise AssertionError("dynamic registration must remain disabled")


class AuthorizationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_authorization(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "authorization_session": {"session_id": "mcpoauthsession_" + "1" * 32},
            "authorization_url": "https://auth.example.net/authorize?server-owned=1",
        }

    def status(self) -> dict[str, Any]:
        return {"authorization_enabled": True, "token_storage_enabled": True}

    def summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "authorization_enabled": True,
            "token_storage_enabled": True,
            "authorization_session": None,
            "token": None,
        }


def entry() -> dict[str, Any]:
    return {
        "server": {
            "name": "io.example/oauth",
            "version": "1.0.0",
            "title": "OAuth MCP",
            "remotes": [{
                "type": "streamable-http",
                "url": RESOURCE,
                "headers": [{
                    "name": "Authorization",
                    "description": "OAuth bearer token",
                    "isSecret": True,
                }],
            }],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": "active",
                "isLatest": True,
            }
        },
    }


def make_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[MCPHubService, dict[str, Any]]:
    monkeypatch.setenv("MCP_HUB_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_REMOTE_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK", "true")
    monkeypatch.setenv("MCP_REMOTE_OAUTH_ENABLED", "true")
    normalized = normalize_registry_entry(entry())
    store = MCPHubStore(tmp_path / "hub")
    store.replace_snapshot("seed", [normalized], '"seed"')
    service = MCPHubService(
        store, tenant_id="local", owner_id="local", reviewed_contracts={}
    )
    oauth = MCPRemoteOAuthService(
        MCPRemoteOAuthStore(tmp_path / "auth"),
        subject_resolver=LocalSubjectScopeResolver(),
        remote_auth_status=lambda: {
            "enabled": True,
            "single_owner_acknowledged": True,
            "external_master_key_available": True,
            "external_master_key_enforced": True,
        },
        bridge=Bridge(),
    )
    service.set_remote_oauth(oauth)
    candidate = service.create_candidate(
        normalized["server_name"],
        normalized["version"],
        normalized["remotes"][0]["remote_id"],
    )
    return service, candidate


@pytest.mark.asyncio
async def test_hub_oauth_uses_only_server_owned_target_and_does_not_activate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, candidate = make_service(tmp_path, monkeypatch)
    assert service.get_candidate(candidate["candidate_id"])[
        "oauth_discovery_available"
    ] is True
    assert service.get_candidate(candidate["candidate_id"])[
        "registry_eligibility"
    ] == "oauth_discovery_candidate"
    with pytest.raises(Exception) as oauth_runtime:
        await service.preflight(candidate["candidate_id"])
    assert getattr(oauth_runtime.value, "code", "") == (
        "mcp_remote_oauth_authorization_not_implemented"
    )
    result = await service.discover_candidate_oauth(
        candidate["candidate_id"],
        expected_source_digest=candidate["source_digest"],
    )
    assert result["discovery"]["resource_uri"] == RESOURCE
    assert result["authorization_enabled"] is False
    assert service.get_candidate(candidate["candidate_id"])["state"] == "draft"
    assert service.runtime_tools() == []

    registered = await service.register_candidate_oauth_client(
        candidate["candidate_id"],
        expected_discovery_fingerprint=result["discovery"]["discovery_fingerprint"],
        mode="pre_registered",
        client_id="public-client-id",
    )
    assert registered["registration"]["client_id"] == "public-client-id"
    assert service.get_candidate(candidate["candidate_id"])["state"] == "draft"


@pytest.mark.asyncio
async def test_hub_oauth_api_rejects_client_scope_endpoint_and_header_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, candidate = make_service(tmp_path, monkeypatch)
    configure_mcp_hub(service)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/api/mcp/hub/candidates/{candidate['candidate_id']}/oauth/discover",
            json={
                "expected_source_digest": candidate["source_digest"],
                "resource_url": "https://attacker.invalid/mcp",
                "issuer": "https://attacker.invalid",
                "headers": {"Authorization": "secret"},
                "tenant_id": "other",
                "owner_id": "other",
            },
        )
        registration = await client.post(
            f"/api/mcp/hub/candidates/{candidate['candidate_id']}/oauth/registrations",
            json={
                "expected_discovery_fingerprint": "d" * 64,
                "mode": "pre_registered",
                "client_id": "public-client",
                "client_secret": "must-not-be-reflected",
            },
        )
    assert response.status_code == 422
    assert "secret" not in response.text
    assert registration.status_code == 422
    assert "must-not-be-reflected" not in registration.text


def test_hub_oauth_rejects_anonymous_candidate_without_registry_auth_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_HUB_ENABLED", "true")
    normalized = normalize_registry_entry({
        **entry(),
        "server": {
            **entry()["server"],
            "remotes": [{"type": "streamable-http", "url": RESOURCE}],
        },
    })
    store = MCPHubStore(tmp_path / "hub")
    store.replace_snapshot("seed", [normalized], '"seed"')
    service = MCPHubService(
        store, tenant_id="local", owner_id="local", reviewed_contracts={}
    )
    candidate = service.create_candidate(
        normalized["server_name"],
        normalized["version"],
        normalized["remotes"][0]["remote_id"],
    )
    with pytest.raises(Exception) as ineligible:
        service.candidate_oauth(candidate["candidate_id"])
    assert getattr(ineligible.value, "code", "") == (
        "mcp_remote_oauth_candidate_ineligible"
    )


@pytest.mark.asyncio
async def test_hub_oauth_source_digest_and_registration_fingerprint_are_cas_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, candidate = make_service(tmp_path, monkeypatch)
    with pytest.raises(Exception) as stale_source:
        await service.discover_candidate_oauth(
            candidate["candidate_id"], expected_source_digest="b" * 64
        )
    assert getattr(stale_source.value, "code", "") == "mcp_remote_oauth_source_drift"

    discovered = await service.discover_candidate_oauth(
        candidate["candidate_id"],
        expected_source_digest=candidate["source_digest"],
    )
    with pytest.raises(Exception) as stale_discovery:
        await service.register_candidate_oauth_client(
            candidate["candidate_id"],
            expected_discovery_fingerprint="c" * 64,
            mode="pre_registered",
            client_id="public-client",
        )
    assert getattr(stale_discovery.value, "code", "") == "mcp_remote_oauth_discovery_stale"
    assert discovered["registration"] is None


@pytest.mark.asyncio
async def test_hub_kill_switch_blocks_registration_before_any_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, candidate = make_service(tmp_path, monkeypatch)
    discovered = await service.discover_candidate_oauth(
        candidate["candidate_id"],
        expected_source_digest=candidate["source_digest"],
    )
    monkeypatch.setenv("MCP_HUB_ENABLED", "false")
    with pytest.raises(Exception) as disabled:
        await service.register_candidate_oauth_client(
            candidate["candidate_id"],
            expected_discovery_fingerprint=discovered["discovery"][
                "discovery_fingerprint"
            ],
            mode="pre_registered",
            client_id="must-not-be-stored",
        )
    assert getattr(disabled.value, "code", "") == "hub_disabled"
    assert service.remote_oauth_service.store.active_registration(
        subject=LocalSubjectScopeResolver().resolve(),
        target_type="hub_candidate",
        target_id=candidate["candidate_id"],
    ) is None


@pytest.mark.asyncio
async def test_oauth_revoke_rejects_query_and_body_without_revoking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, candidate = make_service(tmp_path, monkeypatch)
    discovered = await service.discover_candidate_oauth(
        candidate["candidate_id"],
        expected_source_digest=candidate["source_digest"],
    )
    registered = await service.register_candidate_oauth_client(
        candidate["candidate_id"],
        expected_discovery_fingerprint=discovered["discovery"][
            "discovery_fingerprint"
        ],
        mode="pre_registered",
        client_id="public-client",
    )
    registration_id = registered["registration"]["registration_id"]
    configure_mcp_hub(service)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.request(
            "DELETE",
            f"/api/mcp/hub/candidates/{candidate['candidate_id']}/oauth/registrations/"
            f"{registration_id}?tenant_id=other&owner_id=other",
            json={"client_secret": "must-not-be-accepted"},
        )
    assert response.status_code == 422
    assert "must-not-be-accepted" not in response.text
    assert service.candidate_oauth(candidate["candidate_id"])["registration"][
        "registration_id"
    ] == registration_id


@pytest.mark.asyncio
async def test_oauth_authorization_api_accepts_only_server_owned_target_and_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, candidate = make_service(tmp_path, monkeypatch)
    discovered = await service.discover_candidate_oauth(
        candidate["candidate_id"],
        expected_source_digest=candidate["source_digest"],
    )
    registered = await service.register_candidate_oauth_client(
        candidate["candidate_id"],
        expected_discovery_fingerprint=discovered["discovery"][
            "discovery_fingerprint"
        ],
        mode="pre_registered",
        client_id="public-client",
    )
    authorization = AuthorizationService()
    service.remote_oauth_service.set_authorization_service(authorization)
    configure_mcp_hub(service)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        payload = {
            "expected_discovery_fingerprint": registered["discovery"][
                "discovery_fingerprint"
            ],
            "expected_registration_revision": registered["registration"][
                "revision"
            ],
            "scopes": ["mcp:read"],
        }
        response = await client.post(
            f"/api/mcp/hub/candidates/{candidate['candidate_id']}/oauth/authorization-sessions",
            json=payload,
        )
        assert response.status_code == 201
        for injected in (
            {"url": "https://attacker.example/token"},
            {"headers": {"Authorization": "secret"}},
            {"tenant_id": "other"},
            {"client_id": "attacker-client"},
            {"code": "attacker-code"},
        ):
            denied = await client.post(
                f"/api/mcp/hub/candidates/{candidate['candidate_id']}/oauth/authorization-sessions",
                json={**payload, **injected},
            )
            assert denied.status_code == 422
    assert authorization.calls == [
        {
            "target_type": "hub_candidate",
            "target_id": candidate["candidate_id"],
            "source_digest": candidate["source_digest"],
            "expected_discovery_fingerprint": registered["discovery"][
                "discovery_fingerprint"
            ],
            "expected_registration_revision": 1,
            "scopes": ["mcp:read"],
        }
    ]
