from __future__ import annotations

import asyncio
import socket
import uuid
from pathlib import Path
from typing import Any

import pytest
import httpx
from fastapi import FastAPI
from pydantic import ValidationError

from server.mcp.hub import (
    CandidateCreateRequest,
    PinnedRegistryClient,
    HubError,
    HubUnknownOutcomeError,
    MCPHubService,
    MCPHubStore,
    SESSION_IDLE_SECONDS,
    arguments_digest,
    configure_mcp_hub,
    normalize_hub_remote_url,
    normalize_registry_entry,
    router,
    stable_digest,
)
from server.mcp.remote_auth import (
    LocalSubjectScopeResolver,
    MCPRemoteAuthBroker,
    MCPRemoteAuthStore,
)
from server.toolsets.credentials import CredentialStore
from server.xpert_runtime.hub_toolset import HubMCPToolsetProvider


def registry_entry(
    *,
    name: str = "io.example/public",
    version: str = "1.2.3",
    url: str = "https://mcp.example.com/mcp",
    status: str = "active",
) -> dict[str, Any]:
    return {
        "server": {
            "name": name,
            "version": version,
            "title": "Public Example",
            "description": "Anonymous public metadata",
            "remotes": [{"type": "streamable-http", "url": url}],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": status,
                "isLatest": True,
            }
        },
    }


TOOLS = [
    {
        "name": "search",
        "description": "Search public metadata",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 200}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]


def reviewed_test_contracts() -> dict[tuple[str, str, str], dict[str, Any]]:
    normalized = [
        {
            "name": item["name"],
            "description": item["description"],
            "input_schema": item["input_schema"],
            "schema_digest": stable_digest(item["input_schema"]),
        }
        for item in TOOLS
    ]
    normalized.sort(key=lambda item: item["name"])
    return {
        ("io.example/public", "1.2.3", "https://mcp.example.com/mcp"): {
            "schema_digest": stable_digest(normalized),
            "tool_schema_digests": {
                item["name"]: item["schema_digest"] for item in normalized
            },
        }
    }


class FakeRegistryClient:
    def __init__(self, pages: list[dict[str, Any] | Exception]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[str, str]] = []

    def get_page(self, *, cursor: str = "", etag: str = ""):
        self.calls.append((cursor, etag))
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value, '"snapshot-v1"', False


class FakeBridge:
    def __init__(self) -> None:
        self.tools = [dict(item) for item in TOOLS]
        self.reset_count = 0
        self.list_count = 0
        self.call_count = 0
        self.open_count = 0
        self.closed: list[str] = []
        self.revoked: list[str] = []
        self.fail_call = False
        self.list_errors: list[HubError] = []

    async def authorize(self, candidate_id: str, url: str) -> str:
        assert candidate_id.startswith("mcphub_")
        assert url == "https://mcp.example.com/mcp"
        return "a" * 64

    async def revoke(self, capability: str) -> None:
        self.revoked.append(capability)

    async def open(
        self,
        candidate_id: str,
        url: str,
        capability: str,
        session_owner: str,
    ) -> dict[str, Any]:
        self.open_count += 1
        assert capability == "a" * 64
        assert session_owner == f"hub:tenant-a:owner-a:{candidate_id}"
        return {"session_id": "hubsession_" + "b" * 32, "tools": self.tools}

    async def list_tools(self, session_id: str) -> dict[str, Any]:
        assert session_id.startswith("hubsession_")
        self.list_count += 1
        if self.list_errors:
            raise self.list_errors.pop(0)
        return {"tools": self.tools}

    async def call(
        self, session_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.call_count += 1
        if self.fail_call:
            raise HubError("lost", code="hub_sidecar_unavailable", status_code=503)
        return {
            "result": {
                "content": [{"type": "text", "text": f"result:{arguments['query']}"}],
                "isError": False,
            }
        }

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def reset(self) -> None:
        self.reset_count += 1


@pytest.fixture(autouse=True)
def enable_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HUB_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_REMOTE_ENABLED", "true")


def make_service(tmp_path: Path, *, bridge: FakeBridge | None = None) -> MCPHubService:
    store = MCPHubStore(tmp_path)
    normalized = normalize_registry_entry(registry_entry())
    store.replace_snapshot("hub_sync_seed", [normalized], '"seed"')
    return MCPHubService(
        store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=bridge or FakeBridge(),
        reviewed_contracts=reviewed_test_contracts(),
    )


def test_remote_url_policy_rejects_user_controlled_network_shapes() -> None:
    assert normalize_hub_remote_url("https://mcp.example.com/mcp") == (
        "https://mcp.example.com/mcp",
        "https://mcp.example.com",
    )
    for denied in (
        "http://mcp.example.com/mcp",
        "https://mcp.example.com:8443/mcp",
        "https://user@mcp.example.com/mcp",
        "https://mcp.example.com/mcp?token=x",
        "https://mcp.example.com/mcp#fragment",
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
        "https://{tenant}.example.com/mcp",
    ):
        with pytest.raises(HubError, match="Hub|远程"):
            normalize_hub_remote_url(denied)


def test_fixed_registry_allows_only_docker_transport_synthetic_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("198.18.1.182", 443))
        ],
    )
    assert PinnedRegistryClient._resolve() == ("198.18.1.182",)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", 443))
        ],
    )
    with pytest.raises(HubError, match="非公网"):
        PinnedRegistryClient._resolve()


def test_registry_page_request_is_fixed_to_latest_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    class FakeResponse:
        status = 200

        @staticmethod
        def getheader(_name: str) -> str | None:
            return None

        @staticmethod
        def read(_limit: int | None = None) -> bytes:
            return b'{"servers":[],"metadata":{"count":0}}'

    class FakeConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def request(
            self, method: str, path: str, *, headers: dict[str, str]
        ) -> None:
            assert method == "GET"
            assert headers["Accept"] == "application/json"
            requested_paths.append(path)

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        @staticmethod
        def close() -> None:
            pass

    monkeypatch.setattr(
        PinnedRegistryClient,
        "_resolve",
        staticmethod(lambda: ("203.0.113.1",)),
    )
    monkeypatch.setattr(
        "server.mcp.hub.http.client.HTTPSConnection", FakeConnection
    )

    payload, _etag, not_modified = PinnedRegistryClient().get_page(
        cursor="next cursor"
    )

    assert not_modified is False
    assert payload["servers"] == []
    assert requested_paths == [
        "/v0.1/servers?limit=100&version=latest&cursor=next%20cursor"
    ]


def test_registry_eligibility_is_metadata_only_and_request_forbids_url() -> None:
    assert normalize_registry_entry(registry_entry())["eligibility"] == "eligible"
    auth = registry_entry()
    auth["server"]["remotes"][0]["headers"] = [{"name": "Authorization"}]
    assert normalize_registry_entry(auth)["eligibility"] == "auth_required"
    local = registry_entry()
    local["server"]["remotes"] = []
    local["server"]["packages"] = [{"registryType": "npm"}]
    assert normalize_registry_entry(local)["eligibility"] == "local_runtime"
    legacy = registry_entry()
    legacy["server"]["remotes"][0]["type"] = "sse"
    assert normalize_registry_entry(legacy)["eligibility"] == "legacy_transport"
    removed = registry_entry(status="deleted")
    assert normalize_registry_entry(removed)["eligibility"] == "removed"
    with pytest.raises(ValidationError):
        CandidateCreateRequest(
            server_name="io.example/public",
            version="1.2.3",
            remote_id="remote_" + "1" * 16,
            url="https://attacker.invalid/mcp",  # type: ignore[call-arg]
        )


def test_registry_static_token_requires_one_current_required_secret_header() -> None:
    bearer = registry_entry()
    bearer["server"]["remotes"][0]["headers"] = [
        {
            "name": "Authorization",
            "description": "Bearer API token",
            "isRequired": True,
            "isSecret": True,
        }
    ]
    normalized = normalize_registry_entry(bearer)
    remote = normalized["remotes"][0]
    assert normalized["eligibility"] == "static_token_candidate"
    assert remote["url"] == "https://mcp.example.com/mcp"
    assert remote["auth_policy"]["mode"] == "static_bearer"
    assert remote["auth_policy"]["header_name"] == "Authorization"
    assert set(remote["auth_policy"]) == {
        "schema_version",
        "mode",
        "slot",
        "header_name",
        "origin",
        "remote_url_digest",
        "policy_fingerprint",
    }

    api_key = registry_entry()
    api_key["server"]["remotes"][0]["headers"] = [
        {"name": "X-API-Key", "isRequired": True, "isSecret": True}
    ]
    custom = normalize_registry_entry(api_key)["remotes"][0]
    assert custom["eligibility"] == "static_token_candidate"
    assert custom["auth_policy"]["mode"] == "static_header"
    assert custom["auth_policy"]["header_name"] == "x-api-key"

    denied_shapes = [
        [{"name": "Authorization", "isRequired": False, "isSecret": True}],
        [{"name": "Authorization", "isRequired": True, "isSecret": False}],
        [
            {"name": "Authorization", "isRequired": True, "isSecret": True},
            {"name": "X-API-Key", "isRequired": True, "isSecret": True},
        ],
        [{"name": "Host", "isRequired": True, "isSecret": True}],
        [{"name": "User-Agent", "isRequired": True, "isSecret": True}],
        [{"name": "MCP-Protocol-Version", "isRequired": True, "isSecret": True}],
        [{"name": "X-Forwarded-Host", "isRequired": True, "isSecret": True}],
        [{"name": "X-Original-URL", "isRequired": True, "isSecret": True}],
        [
            {
                "name": "Authorization",
                "isRequired": True,
                "isSecret": True,
                "default": "must-not-be-accepted",
            }
        ],
    ]
    for headers in denied_shapes:
        denied = registry_entry()
        denied["server"]["remotes"][0]["headers"] = headers
        assert normalize_registry_entry(denied)["eligibility"] == "auth_required"

    templated = registry_entry()
    templated["server"]["remotes"][0]["headers"] = [
        {"name": "Authorization", "isRequired": True, "isSecret": True}
    ]
    templated["server"]["remotes"][0]["variables"] = {
        "tenant": {"isRequired": True}
    }
    assert normalize_registry_entry(templated)["eligibility"] == "auth_required"


@pytest.mark.asyncio
async def test_static_token_binding_create_rotate_revoke_is_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK", "true")
    vault = CredentialStore(
        tmp_path / "vault",
        master_key="test-external-master-key",
        require_external_master_key=True,
    )
    broker = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path / "bindings"),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_security_attestor=vault.remote_auth_master_key_attestation,
    )
    entry = registry_entry(name="io.example/token")
    entry["server"]["remotes"][0]["headers"] = [
        {"name": "Authorization", "isRequired": True, "isSecret": True}
    ]
    store = MCPHubStore(tmp_path / "hub")
    normalized = normalize_registry_entry(entry)
    store.replace_snapshot("seed", [normalized], '"seed"')
    service = MCPHubService(
        store,
        tenant_id="local",
        owner_id="local",
        bridge=FakeBridge(),
        reviewed_contracts={},
    )
    service.set_remote_auth(
        broker,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_revoker=vault.revoke,
    )
    remote = normalized["remotes"][0]
    candidate = service.create_candidate(
        normalized["server_name"], normalized["version"], remote["remote_id"]
    )

    created = service.create_candidate_auth_binding(
        candidate["candidate_id"],
        slot="registry-secret-header",
        display_name="Example token",
        secret="first-secret-token",
    )
    assert created["binding"]["revision"] == 1
    assert created["binding"]["masked_value"] != "first-secret-token"
    binding_id = created["binding"]["binding_id"]

    rotated = await service.rotate_candidate_auth_binding(
        candidate["candidate_id"],
        binding_id,
        secret="second-secret-token",
        expected_revision=1,
    )
    assert rotated["binding"]["revision"] == 2
    assert "second-secret-token" not in str(rotated)

    await service.revoke_candidate_auth_binding(candidate["candidate_id"], binding_id)
    assert service.candidate_auth(candidate["candidate_id"])["binding"] is None

    replacement = service.create_candidate_auth_binding(
        candidate["candidate_id"],
        slot="registry-secret-header",
        display_name="Replacement token",
        secret="delete-with-candidate-secret",
    )
    assert "auth_binding_id" not in service.get_candidate(candidate["candidate_id"])
    replacement_binding = broker.store.get_binding(
        replacement["binding"]["binding_id"],
        subject=broker.subject_resolver.resolve(),
    )
    await service.delete_candidate(candidate["candidate_id"])
    assert vault.get_public(
        replacement_binding.credential_id,
        tenant_id="local",
        owner_id="local",
    ).status == "revoked"
    assert broker.store.get_binding(
        replacement_binding.binding_id,
        subject=broker.subject_resolver.resolve(),
    ).status == "revoked"
    persisted = b"".join(
        path.read_bytes()
        for path in (store.path, broker.store.path, vault.storage_path)
    )
    assert b"first-secret-token" not in persisted
    assert b"second-secret-token" not in persisted
    assert b"delete-with-candidate-secret" not in persisted


@pytest.mark.asyncio
async def test_static_token_binding_api_rejects_client_scope_and_target_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK", "true")
    vault = CredentialStore(
        tmp_path / "vault",
        master_key="test-external-master-key",
        require_external_master_key=True,
    )
    broker = MCPRemoteAuthBroker(
        MCPRemoteAuthStore(tmp_path / "bindings"),
        subject_resolver=LocalSubjectScopeResolver(),
        credential_lookup=vault.get_public,
        credential_resolver=vault.resolve,
        credential_security_attestor=vault.remote_auth_master_key_attestation,
    )
    entry = registry_entry(name="io.example/token-api")
    entry["server"]["remotes"][0]["headers"] = [
        {"name": "Authorization", "isRequired": True, "isSecret": True}
    ]
    normalized = normalize_registry_entry(entry)
    store = MCPHubStore(tmp_path / "hub")
    store.replace_snapshot("seed", [normalized], '"seed"')
    service = MCPHubService(
        store,
        tenant_id="local",
        owner_id="local",
        bridge=FakeBridge(),
        reviewed_contracts={},
    )
    service.set_remote_auth(
        broker,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_revoker=vault.revoke,
    )
    candidate = service.create_candidate(
        normalized["server_name"],
        normalized["version"],
        normalized["remotes"][0]["remote_id"],
    )
    configure_mcp_hub(service)
    app = FastAPI()
    app.include_router(router)
    injected_secret = "api-injected-secret-must-not-echo"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/mcp/hub/candidates/{candidate['candidate_id']}/auth-bindings",
            json={
                "slot": "registry-secret-header",
                "display_name": "Rejected",
                "secret": injected_secret,
                "tenant_id": "other",
                "owner_id": "other",
                "origin": "https://attacker.invalid",
                "header_name": "X-Evil",
                "credential_id": "credential_other",
            },
        )
    assert response.status_code == 422
    assert injected_secret not in response.text
    assert service.candidate_auth(candidate["candidate_id"])["binding"] is None


@pytest.mark.asyncio
async def test_sync_paginates_deduplicates_and_preserves_last_good_snapshot(
    tmp_path: Path,
) -> None:
    first = registry_entry(name="io.example/one")
    second = registry_entry(name="io.example/two")
    client = FakeRegistryClient(
        [
            {"servers": [first, first], "metadata": {"nextCursor": "next"}},
            {"servers": [second], "metadata": {"nextCursor": ""}},
        ]
    )
    store = MCPHubStore(tmp_path)
    service = MCPHubService(
        store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        registry_client=client,
        bridge=FakeBridge(),
    )
    sync_id = store.create_sync()
    await service._run_sync(sync_id)
    assert store.get_sync(sync_id)["status"] == "completed"
    assert service.list_servers(limit=50)["total"] == 2
    assert client.calls == [("", ""), ("next", "")]

    service.registry_client = FakeRegistryClient(
        [HubError("rate", code="hub_registry_rate_limited", status_code=429)]
    )
    failed_id = store.create_sync()
    await service._run_sync(failed_id)
    assert store.get_sync(failed_id)["error_code"] == "hub_registry_rate_limited"
    assert service.list_servers(limit=50)["total"] == 2
    await service.close()


@pytest.mark.asyncio
async def test_sync_skips_invalid_identity_without_losing_valid_records(
    tmp_path: Path,
) -> None:
    invalid = registry_entry(name="io.example/invalid", version="{{VERSION}}")
    client = FakeRegistryClient(
        [
            {
                "servers": [invalid, registry_entry()],
                "metadata": {"nextCursor": ""},
            }
        ]
    )
    store = MCPHubStore(tmp_path)
    service = MCPHubService(
        store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        registry_client=client,
        bridge=FakeBridge(),
    )
    sync_id = store.create_sync()
    await service._run_sync(sync_id)
    assert store.get_sync(sync_id)["status"] == "completed"
    assert service.list_servers(limit=50)["total"] == 1
    assert store.meta("last_sync_skipped_count") == "1"
    await service.close()


@pytest.mark.asyncio
async def test_sync_retries_only_transient_page_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = FakeRegistryClient(
        [
            HubError("network", code="hub_registry_unavailable"),
            HubError("network", code="hub_registry_unavailable"),
            {
                "servers": [registry_entry()],
                "metadata": {"nextCursor": ""},
            },
        ]
    )
    store = MCPHubStore(tmp_path)
    service = MCPHubService(
        store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        registry_client=client,
        bridge=FakeBridge(),
    )

    sync_id = store.create_sync()
    await service._run_sync(sync_id)

    assert store.get_sync(sync_id)["status"] == "completed"
    assert len(client.calls) == 3
    assert delays == [0.25, 0.5]
    await service.close()


def test_candidate_persists_and_is_owner_scoped(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    server = service.get_server("io.example/public", "1.2.3")
    candidate = service.create_candidate(
        server["server_name"], server["version"], server["remotes"][0]["remote_id"]
    )
    restarted = MCPHubService(
        MCPHubStore(tmp_path),
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=FakeBridge(),
    )
    assert restarted.get_candidate(candidate["candidate_id"])["state"] == "draft"
    other_owner = MCPHubService(
        MCPHubStore(tmp_path),
        tenant_id="tenant-a",
        owner_id="owner-b",
        bridge=FakeBridge(),
    )
    with pytest.raises(HubError) as captured:
        other_owner.get_candidate(candidate["candidate_id"])
    assert captured.value.code == "hub_candidate_not_found"


@pytest.mark.asyncio
async def test_unreviewed_registry_candidate_can_preflight_but_not_activate(
    tmp_path: Path,
) -> None:
    store = MCPHubStore(tmp_path)
    store.replace_snapshot(
        "hub_sync_seed", [normalize_registry_entry(registry_entry())], '"seed"'
    )
    service = MCPHubService(
        store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=FakeBridge(),
        reviewed_contracts={},
    )
    server = service.get_server("io.example/public", "1.2.3")
    candidate = service.create_candidate(
        server["server_name"], server["version"], server["remotes"][0]["remote_id"]
    )

    verified = await service.preflight(candidate["candidate_id"])

    assert verified["state"] == "verified"
    assert verified["connected"] is False
    assert verified["activation_eligible"] is False
    assert verified["activation_reason"] == "hub_contract_unreviewed"
    with pytest.raises(HubError) as captured:
        await service.activate(candidate["candidate_id"], verified["schema_digest"])
    assert captured.value.code == "hub_contract_unreviewed"
    assert service.runtime_tools() == []


@pytest.mark.asyncio
async def test_activation_rechecks_registry_source_inside_candidate_lock(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    server = service.get_server("io.example/public", "1.2.3")
    candidate = service.create_candidate(
        server["server_name"], server["version"], server["remotes"][0]["remote_id"]
    )
    verified = await service.preflight(candidate["candidate_id"])
    changed = registry_entry()
    changed["server"]["description"] = "changed after preflight"
    service.store.replace_snapshot(
        "hub_sync_changed", [normalize_registry_entry(changed)], '"changed"'
    )

    with pytest.raises(HubError) as captured:
        await service.activate(candidate["candidate_id"], verified["schema_digest"])

    assert captured.value.code == "hub_source_drift"
    assert service.get_candidate(candidate["candidate_id"])["state"] == "drifted"


@pytest.mark.asyncio
async def test_disconnected_candidate_preflight_restores_verified_state(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    candidate, _runtime = await active_candidate(service)

    disconnected = await service.disconnect(candidate["candidate_id"])
    assert disconnected["state"] == "disconnected"
    assert disconnected["connected"] is False

    verified = await service.preflight(candidate["candidate_id"])
    assert verified["state"] == "verified"
    assert verified["connected"] is True
    assert verified["activation_eligible"] is True

    active = await service.activate(
        candidate["candidate_id"], verified["schema_digest"]
    )
    assert active["state"] == "active"


async def active_candidate(
    service: MCPHubService,
) -> tuple[dict[str, Any], dict[str, Any]]:
    server = service.get_server("io.example/public", "1.2.3")
    candidate = service.create_candidate(
        server["server_name"], server["version"], server["remotes"][0]["remote_id"]
    )
    verified = await service.preflight(candidate["candidate_id"])
    active = await service.activate(candidate["candidate_id"], verified["schema_digest"])
    runtime = service.runtime_tools()[0]
    return active, runtime


def approval_for(
    service: MCPHubService,
    candidate: dict[str, Any],
    runtime: dict[str, Any],
    arguments: dict[str, Any],
    *,
    approval_id: str | None = None,
) -> dict[str, Any]:
    return {
        "approval_id": approval_id or str(uuid.uuid4()),
        "status": "decided",
        "decision": "approve",
        "tool_name": runtime["name"],
        "metadata": {
            "hub_approval": {
                "candidate_id": candidate["candidate_id"],
                "tenant_id": service.tenant_id,
                "owner_id": service.owner_id,
                "server_name": candidate["server_name"],
                "version": candidate["version"],
                "origin": candidate["origin"],
                "schema_digest": candidate["schema_digest"],
                "tool_schema_digest": runtime["tool_schema_digest"],
                "arguments_digest": arguments_digest(arguments),
            }
        },
    }


@pytest.mark.asyncio
async def test_every_call_rechecks_schema_and_completed_approval_replays_cache(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge()
    service = make_service(tmp_path, bridge=bridge)
    candidate, runtime = await active_candidate(service)
    arguments = {"query": "safe"}
    approval = approval_for(service, candidate, runtime, arguments)

    first = await service.execute(
        candidate_id=candidate["candidate_id"],
        runtime_tool_name=runtime["name"],
        upstream_tool_name=runtime["upstream_tool_name"],
        arguments=arguments,
        approval=approval,
    )
    assert first["content"][0]["text"] == "result:safe"
    assert bridge.list_count == 1
    assert bridge.call_count == 1

    await service.disconnect(candidate["candidate_id"])
    replay = await service.execute(
        candidate_id=candidate["candidate_id"],
        runtime_tool_name=runtime["name"],
        upstream_tool_name=runtime["upstream_tool_name"],
        arguments=arguments,
        approval=approval,
    )
    assert replay == first
    assert bridge.list_count == 1
    assert bridge.call_count == 1


@pytest.mark.asyncio
async def test_expired_sidecar_session_reconnects_only_before_tool_call(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge()
    service = make_service(tmp_path, bridge=bridge)
    candidate, runtime = await active_candidate(service)
    bridge.list_errors.append(
        HubError("expired", code="hub_session_not_found", status_code=502)
    )
    arguments = {"query": "safe"}

    result = await service.execute(
        candidate_id=candidate["candidate_id"],
        runtime_tool_name=runtime["name"],
        upstream_tool_name=runtime["upstream_tool_name"],
        arguments=arguments,
        approval=approval_for(service, candidate, runtime, arguments),
    )

    assert result["content"][0]["text"] == "result:safe"
    assert bridge.open_count == 2
    assert bridge.list_count == 2
    assert bridge.call_count == 1
    assert service.get_candidate(candidate["candidate_id"])["state"] == "active"


@pytest.mark.asyncio
async def test_idle_live_session_is_hidden_and_reaped(tmp_path: Path) -> None:
    bridge = FakeBridge()
    service = make_service(tmp_path, bridge=bridge)
    candidate, _runtime = await active_candidate(service)
    live = service._live[candidate["candidate_id"]]
    live.last_activity -= SESSION_IDLE_SECONDS + 1

    assert service.get_candidate(candidate["candidate_id"])["connected"] is False

    await service._cleanup_expired_live_sessions()

    assert candidate["candidate_id"] not in service._live
    assert bridge.closed == [live.session_id]
    assert bridge.revoked == [live.capability]


@pytest.mark.asyncio
async def test_schema_drift_blocks_call_before_ledger_start(tmp_path: Path) -> None:
    bridge = FakeBridge()
    service = make_service(tmp_path, bridge=bridge)
    candidate, runtime = await active_candidate(service)
    arguments = {"query": "safe"}
    bridge.tools = [
        {
            **TOOLS[0],
            "input_schema": {"type": "object", "additionalProperties": True},
        }
    ]
    with pytest.raises(HubError) as captured:
        await service.execute(
            candidate_id=candidate["candidate_id"],
            runtime_tool_name=runtime["name"],
            upstream_tool_name=runtime["upstream_tool_name"],
            arguments=arguments,
            approval=approval_for(service, candidate, runtime, arguments),
        )
    assert captured.value.code == "hub_schema_drift"
    assert bridge.open_count == 1
    assert bridge.call_count == 0
    assert service.get_candidate(candidate["candidate_id"])["state"] == "drifted"


@pytest.mark.asyncio
async def test_unknown_outcome_taints_disconnects_and_cannot_be_retried(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge()
    service = make_service(tmp_path, bridge=bridge)
    candidate, runtime = await active_candidate(service)
    arguments = {"query": "safe"}
    approval = approval_for(service, candidate, runtime, arguments)
    bridge.fail_call = True
    with pytest.raises(HubUnknownOutcomeError):
        await service.execute(
            candidate_id=candidate["candidate_id"],
            runtime_tool_name=runtime["name"],
            upstream_tool_name=runtime["upstream_tool_name"],
            arguments=arguments,
            approval=approval,
        )
    current = service.get_candidate(candidate["candidate_id"])
    assert current["state"] == "tainted"
    assert current["connected"] is False
    with pytest.raises(HubError) as retry:
        await service.execute(
            candidate_id=candidate["candidate_id"],
            runtime_tool_name=runtime["name"],
            upstream_tool_name=runtime["upstream_tool_name"],
            arguments=arguments,
            approval=approval,
        )
    assert retry.value.code == "unknown_outcome"
    assert bridge.call_count == 1


@pytest.mark.asyncio
async def test_approval_scope_and_provider_policy_are_fail_closed(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    candidate, runtime = await active_candidate(service)
    arguments = {"query": "safe"}
    forged = approval_for(service, candidate, runtime, arguments)
    forged["metadata"]["hub_approval"]["owner_id"] = "owner-b"
    with pytest.raises(HubError) as captured:
        await service.execute(
            candidate_id=candidate["candidate_id"],
            runtime_tool_name=runtime["name"],
            upstream_tool_name=runtime["upstream_tool_name"],
            arguments=arguments,
            approval=forged,
        )
    assert captured.value.code == "hub_approval_invalid"

    tool = (await HubMCPToolsetProvider(service).list_tools())[0]
    assert tool.provider == "mcp_hub"
    assert tool.requires_approval is True
    assert tool.sensitive is True
    assert tool.read_only is False
    assert tool.parallel_safe is False
    assert tool.public_app_allowed is False
    assert tool.metadata["retry_on_failure"] is False
    assert len(tool.name) <= 64
    assert "Search public metadata" not in tool.description


def test_catalog_tool_names_follow_the_mcp_name_contract(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    for name in ("bad name", "bad\nname", "name/with/slash", "x" * 129):
        with pytest.raises(HubError) as captured:
            service._validate_tools([{**TOOLS[0], "name": name}])
        assert captured.value.code == "hub_tool_contract_denied"


@pytest.mark.asyncio
async def test_service_start_resets_orphan_sidecar_state(tmp_path: Path) -> None:
    bridge = FakeBridge()
    service = make_service(tmp_path, bridge=bridge)
    await service.start()
    assert bridge.reset_count == 1
    await service.close()


@pytest.mark.asyncio
async def test_service_start_marks_crash_interrupted_execution_unknown(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    candidate, runtime = await active_candidate(service)
    approval_id = str(uuid.uuid4())
    args_digest = arguments_digest({"query": "safe"})
    assert service.store.begin_execution(
        approval_id=approval_id,
        tenant_id=service.tenant_id,
        owner_id=service.owner_id,
        candidate_id=candidate["candidate_id"],
        tool_name=runtime["name"],
        args_digest=args_digest,
    ) == ("new", None)
    bridge = FakeBridge()
    restarted = MCPHubService(
        MCPHubStore(tmp_path),
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=bridge,
        reviewed_contracts=reviewed_test_contracts(),
    )

    await restarted.start()

    assert restarted.store.find_execution(
        approval_id=approval_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
        candidate_id=candidate["candidate_id"],
        tool_name=runtime["name"],
        args_digest=args_digest,
    ) == ("unknown", None)
    current = restarted.get_candidate(candidate["candidate_id"])
    assert current["state"] == "tainted"
    assert current["taint_reason"] == "unknown_outcome"
    assert bridge.reset_count == 1
    await restarted.close()


@pytest.mark.asyncio
async def test_failed_manual_sync_attempt_is_still_rate_limited(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    service.store.set_meta("snapshot_at", "0")

    async def fail_sync(_sync_id: str) -> None:
        raise HubError("offline", code="hub_registry_unavailable")

    service._run_sync = fail_sync  # type: ignore[method-assign]
    sync_id = service.request_sync()
    await asyncio.gather(service._sync_tasks[sync_id], return_exceptions=True)

    with pytest.raises(HubError) as captured:
        service.request_sync()
    assert captured.value.code == "hub_sync_rate_limited"


@pytest.mark.asyncio
async def test_hub_api_uses_registry_ids_and_digest_activation(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    configure_mcp_hub(service)
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        status = await client.get("/api/mcp/hub/status")
        assert status.status_code == 200
        assert status.json()["source"] == "https://registry.modelcontextprotocol.io"

        detail = await client.get(
            "/api/mcp/hub/servers/io.example/public/versions/1.2.3"
        )
        assert detail.status_code == 200, detail.text
        remote_id = detail.json()["remotes"][0]["remote_id"]

        rejected = await client.post(
            "/api/mcp/hub/candidates",
            json={
                "server_name": "io.example/public",
                "version": "1.2.3",
                "remote_id": remote_id,
                "url": "https://attacker.invalid/mcp",
            },
        )
        assert rejected.status_code == 422

        created = await client.post(
            "/api/mcp/hub/candidates",
            json={
                "server_name": "io.example/public",
                "version": "1.2.3",
                "remote_id": remote_id,
            },
        )
        assert created.status_code == 201, created.text
        candidate_id = created.json()["candidate_id"]

        verified = await client.post(
            f"/api/mcp/hub/candidates/{candidate_id}/preflight"
        )
        assert verified.status_code == 200, verified.text
        activated = await client.post(
            f"/api/mcp/hub/candidates/{candidate_id}/activate",
            json={"expected_schema_digest": verified.json()["schema_digest"]},
        )
        assert activated.status_code == 200
        assert activated.json()["state"] == "active"

        disconnected = await client.delete(
            f"/api/mcp/hub/candidates/{candidate_id}/session"
        )
        assert disconnected.status_code == 200
        deleted = await client.delete(
            f"/api/mcp/hub/candidates/{candidate_id}"
        )
        assert deleted.status_code == 204
