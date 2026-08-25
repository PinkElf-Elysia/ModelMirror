from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import httpx
from fastapi import FastAPI

from server.mcp.hub import (
    HubError,
    HubUnknownOutcomeError,
    MCPHubService,
    MCPHubStore,
    normalize_registry_entry,
    stable_digest,
)
from server.mcp.hub_contracts import (
    HubContractRegistry,
    HubReviewedContractV1,
    HubReviewedContractV2,
    HubReviewedContractV3,
    canonical_digest,
    contract_export,
    contract_signature,
    normalize_contract,
    stable_contract_id,
)
from server.mcp.remote_auth import RemoteAuthPolicyV1, SubjectScopeV1
from server.mcp.remote_oauth import MCP_PROTOCOL_VERSION, RemoteOAuthPolicyV2
from server.mcp.remote_oauth_authorization import RemoteOAuthExecutionMetadataV1
from server.mcp.remote_auth import (
    LocalSubjectScopeResolver,
    MCPRemoteAuthBroker,
    MCPRemoteAuthStore,
)
from server.toolsets.credentials import CredentialStore
from server.xpert_runtime.hub_toolset import HubMCPToolsetProvider
from server.mcp.hub_review import (
    MCPHubReviewService,
    MCPHubReviewStore,
    configure_mcp_hub_review,
    assess_oauth_scopes,
    deterministic_arguments,
    router as review_router,
)


TOOLS = [
    {
        "name": "search",
        "description": "Search public documentation metadata",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 80}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "admin_delete",
        "description": "Delete account data",
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string", "maxLength": 80}},
            "required": ["account"],
        },
    },
]


def registry_entry(
    *,
    name: str = "io.example/reviewable",
    version: str = "1.0.0",
    url: str = "https://review.example.com/mcp",
) -> dict[str, Any]:
    return {
        "server": {
            "name": name,
            "version": version,
            "title": "Reviewable public service",
            "description": "Anonymous public documentation",
            "publisher": {"name": "Example Publisher"},
            "remotes": [{"type": "streamable-http", "url": url}],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": "active",
                "isLatest": True,
            }
        },
    }


def oauth_registry_entry() -> dict[str, Any]:
    value = registry_entry(
        name="io.example/oauth-review",
        url="https://oauth-review.example.com/mcp",
    )
    value["server"]["remotes"][0]["headers"] = [
        {
            "name": "Authorization",
            "description": "OAuth bearer token",
            "isSecret": True,
        }
    ]
    return value


class FakeBridge:
    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self.tools = [dict(item) for item in (tools or TOOLS)]
        self.call_count = 0
        self.closed: list[str] = []
        self.revoked: list[str] = []
        self.fail_call = False
        self.auth_envelopes: list[dict[str, Any]] = []

    async def authorize(self, candidate_id: str, url: str) -> str:
        assert candidate_id.startswith("mcphub_")
        assert url.startswith("https://")
        return "a" * 64

    async def revoke(self, capability: str) -> None:
        self.revoked.append(capability)

    async def open(
        self,
        candidate_id: str,
        url: str,
        capability: str,
        session_owner: str,
        *,
        auth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert capability == "a" * 64
        assert session_owner.endswith(candidate_id)
        if auth is not None:
            self.auth_envelopes.append(dict(auth))
        return {"session_id": "hubsession_" + "b" * 32, "tools": self.tools}

    async def list_tools(self, session_id: str) -> dict[str, Any]:
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
        return None


class FailingPreflightBridge(FakeBridge):
    async def authorize(self, candidate_id: str, url: str) -> str:
        raise HubError("network denied", code="hub_egress_dns_denied", status_code=409)


class BlockingPreflightBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.authorize_started = asyncio.Event()
        self.authorize_release = asyncio.Event()

    async def authorize(self, candidate_id: str, url: str) -> str:
        self.authorize_started.set()
        await self.authorize_release.wait()
        return await super().authorize(candidate_id, url)


class BlockingApprovalPreflightBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.authorize_count = 0
        self.approval_preflight_started = asyncio.Event()
        self.approval_preflight_release = asyncio.Event()

    async def authorize(self, candidate_id: str, url: str) -> str:
        self.authorize_count += 1
        if self.authorize_count == 2:
            self.approval_preflight_started.set()
            await self.approval_preflight_release.wait()
        return await super().authorize(candidate_id, url)


@pytest.fixture(autouse=True)
def enable_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HUB_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_REMOTE_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_REVIEW_FACTORY_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED", "true")


def make_factory(
    tmp_path: Path,
    *,
    bridge: FakeBridge | None = None,
    repository_dir: Path | None = None,
    signing_key: str = "review-signing-key-with-more-than-32-bytes",
) -> tuple[MCPHubService, MCPHubReviewService, FakeBridge]:
    hub_store = MCPHubStore(tmp_path)
    normalized = normalize_registry_entry(registry_entry())
    hub_store.replace_snapshot("hub_sync_seed", [normalized], '"seed"')
    fake = bridge or FakeBridge()
    hub = MCPHubService(
        hub_store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=fake,
        reviewed_contracts=None,
    )
    review_store = MCPHubReviewStore(hub_store)
    review = MCPHubReviewService(
        hub,
        review_store,
        signing_key=signing_key,
        repository_dir=repository_dir or tmp_path / "repo-contracts",
    )
    hub.contract_registry = review.contracts
    hub.set_review_service(review)
    return hub, review, fake


async def completed_run(review: MCPHubReviewService) -> dict[str, Any]:
    server = review.hub.get_server("io.example/reviewable", "1.0.0")
    run = review.create_run(
        [
            {
                "server_name": server["server_name"],
                "version": server["version"],
                "remote_id": server["remotes"][0]["remote_id"],
            }
        ]
    )
    task = review._tasks[run["run_id"]]
    await task
    return review.store.require_run(run["run_id"], review.tenant_id, review.owner_id)


def test_contract_fingerprint_hmac_collision_and_revocation(tmp_path: Path) -> None:
    store = MCPHubReviewStore(MCPHubStore(tmp_path))
    schema_digest = "1" * 64
    tool_digest = "2" * 64
    contract = HubReviewedContractV1(
        contract_id=stable_contract_id(
            "io.example/contract", "1.0.0", "https://contract.example/mcp"
        ),
        server_name="io.example/contract",
        version="1.0.0",
        remote_url="https://contract.example/mcp",
        origin="https://contract.example",
        source_digest="3" * 64,
        schema_digest=schema_digest,
        tool_schema_digests={"search": tool_digest},
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        limits={"max_result_bytes": 1024},
        evidence_digest="4" * 64,
    )
    key = "contract-signing-key-with-more-than-32-bytes"
    store.add_local_contract_revision(
        "tenant-a", "owner-a", contract, contract_signature(contract, key)
    )
    valid = HubContractRegistry(
        local_store=store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        signing_key=key,
        repository_dir=tmp_path / "empty",
    )
    assert valid.lookup_identity(*contract.identity)[0] == contract
    wrong_key = HubContractRegistry(
        local_store=store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        signing_key="wrong-key",
        repository_dir=tmp_path / "empty",
    )
    assert wrong_key.lookup_identity(*contract.identity)[1] == "hub_contract_unreviewed"

    repository = tmp_path / "repo"
    repository.mkdir()
    changed = contract.model_copy(
        update={"limits": {"max_result_bytes": 2048}, "contract_fingerprint": ""}
    )
    (repository / "collision.json").write_bytes(contract_export(changed))
    collision = HubContractRegistry(
        local_store=store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        signing_key=key,
        repository_dir=repository,
    )
    assert collision.lookup_identity(*contract.identity)[1] == "hub_contract_collision"
    store.add_revocation("tenant-a", "owner-a", contract.contract_id, "revoke")
    assert valid.lookup_identity(*contract.identity)[1] == "hub_contract_revoked"


def test_v2_contract_freezes_auth_policy_without_changing_v1_loader() -> None:
    remote_url = "https://token.example/mcp"
    policy = RemoteAuthPolicyV1(
        mode="static_bearer",
        slot="registry-secret-header",
        header_name="Authorization",
        origin="https://token.example",
        remote_url_digest=stable_digest(remote_url),
    )
    v2 = HubReviewedContractV2(
        contract_id=stable_contract_id("io.example/token", "1.0.0", remote_url),
        server_name="io.example/token",
        version="1.0.0",
        remote_url=remote_url,
        origin="https://token.example",
        source_digest="1" * 64,
        schema_digest="2" * 64,
        tool_schema_digests={"search": "3" * 64},
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        limits={"max_result_bytes": 1024},
        evidence_digest="4" * 64,
        remote_auth_policy=policy,
    )
    loaded = normalize_contract(json.loads(contract_export(v2)))
    assert isinstance(loaded, HubReviewedContractV2)
    assert loaded.remote_auth_policy == policy
    assert "credential" not in contract_export(v2).decode("utf-8").lower()

    changed_policy = RemoteAuthPolicyV1(
        mode="static_bearer",
        slot="registry-secret-header",
        header_name="Authorization",
        origin="https://token.example",
        remote_url_digest="5" * 64,
    )
    changed = HubReviewedContractV2(
        **{
            **v2.model_dump(mode="json", exclude={"contract_fingerprint", "remote_auth_policy"}),
            "remote_auth_policy": changed_policy.model_dump(mode="json"),
        }
    )
    assert changed.contract_fingerprint != v2.contract_fingerprint


def test_v3_contract_freezes_resource_scope_and_protocol_without_local_ids() -> None:
    remote_url = "https://oauth-review.example/mcp"
    policy_fields = {
        "schema_version": "remote-oauth-policy-v2",
        "mode": "oauth_authorization_code_pkce",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "resource_uri": remote_url,
        "origin": "https://oauth-review.example",
        "remote_url_digest": hashlib.sha256(remote_url.encode()).hexdigest(),
        "protected_resource_metadata_url": "https://oauth-review.example/.well-known/oauth-protected-resource/mcp",
        "protected_resource_metadata_digest": "1" * 64,
        "issuer": "https://auth.example/",
        "authorization_server_metadata_url": "https://auth.example/.well-known/oauth-authorization-server",
        "authorization_server_metadata_digest": "2" * 64,
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "",
        "revocation_endpoint": "https://auth.example/revoke",
        "client_id_metadata_document_supported": True,
        "scopes_supported": ("mcp:read",),
        "scope_source": "protected_resource_metadata",
        "recommended_scopes": ("mcp:read",),
        "recommended_scope_digest": canonical_digest(["mcp:read"]),
        "offline_access_available": True,
    }
    policy = RemoteOAuthPolicyV2(
        **policy_fields,
        policy_fingerprint=canonical_digest(policy_fields),
    )
    contract = HubReviewedContractV3(
        contract_id=stable_contract_id("io.example/oauth", "1.0.0", remote_url),
        server_name="io.example/oauth",
        version="1.0.0",
        remote_url=remote_url,
        origin=policy.origin,
        source_digest="3" * 64,
        schema_digest="4" * 64,
        tool_schema_digests={"search": "5" * 64},
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        limits={"max_concurrency": 1},
        evidence_digest="6" * 64,
        remote_oauth_policy=policy,
        authorized_scopes=("mcp:read",),
        authorized_scope_digest=canonical_digest(["mcp:read"]),
    )
    loaded = normalize_contract(json.loads(contract_export(contract)))
    assert isinstance(loaded, HubReviewedContractV3)
    assert loaded.protocol_version == MCP_PROTOCOL_VERSION
    exported = contract_export(contract).decode("utf-8")
    for forbidden in ('"token_id"', '"credential_id"', '"client_id"', "callback"):
        assert forbidden not in exported


@pytest.mark.asyncio
async def test_static_token_sop_requires_binding_and_publishes_v2_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK", "true")
    secret = "review-static-token-secret"
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
    raw = registry_entry(name="io.example/static-token")
    raw["server"]["remotes"][0]["headers"] = [
        {"name": "Authorization", "isRequired": True, "isSecret": True}
    ]
    hub_store = MCPHubStore(tmp_path / "hub")
    normalized = normalize_registry_entry(raw)
    hub_store.replace_snapshot("seed", [normalized], '"seed"')
    bridge = FakeBridge()
    hub = MCPHubService(
        hub_store,
        tenant_id="local",
        owner_id="local",
        bridge=bridge,
        reviewed_contracts=None,
    )
    hub.set_remote_auth(
        broker,
        credential_creator=vault.create,
        credential_lookup=vault.get_public,
        credential_revoker=vault.revoke,
    )
    review_store = MCPHubReviewStore(hub_store)
    review = MCPHubReviewService(
        hub,
        review_store,
        signing_key="review-signing-key-with-more-than-32-bytes",
        repository_dir=tmp_path / "contracts",
    )
    hub.contract_registry = review.contracts
    hub.set_review_service(review)
    remote = normalized["remotes"][0]
    candidate = hub.create_candidate(
        normalized["server_name"], normalized["version"], remote["remote_id"]
    )

    run_without_binding = review.create_run(
        [
            {
                "server_name": normalized["server_name"],
                "version": normalized["version"],
                "remote_id": remote["remote_id"],
            }
        ]
    )
    await review._tasks[run_without_binding["run_id"]]
    missing = review.store.require_run(
        run_without_binding["run_id"], "local", "local"
    )["items"][0]
    assert missing["error_code"] == "mcp_remote_auth_binding_missing"
    assert bridge.auth_envelopes == []

    hub.create_candidate_auth_binding(
        candidate["candidate_id"],
        slot="registry-secret-header",
        display_name="Review token",
        secret=secret,
    )
    run = review.create_run(
        [
            {
                "server_name": normalized["server_name"],
                "version": normalized["version"],
                "remote_id": remote["remote_id"],
            }
        ]
    )
    await review._tasks[run["run_id"]]
    item = review.store.require_run(run["run_id"], "local", "local")["items"][0]
    assert item["state"] == "awaiting_call_approval"
    assert item["evidence"]["sop_version"] == "static_token_https_tools_v1"
    assert secret not in json.dumps(item["evidence"])
    proposal = review.generate_proposal(run["run_id"], item["item_id"])
    await review.approve_proposal(
        run["run_id"],
        item["item_id"],
        proposal["proposal_id"],
        proposal["proposal_digest"],
    )
    item = review.store.require_item(run["run_id"], item["item_id"], "local", "local")
    decided = review.decide(
        run["run_id"],
        item["item_id"],
        decision="approve",
        expected_evidence_digest=item["evidence_digest"],
        allowed_tools=["search"],
        tool_effects={"search": "read"},
    )
    published = review.publish(
        run["run_id"], item["item_id"], decided["contract_fingerprint"]
    )
    contract, reason = review.contracts.get_contract(published["contract_id"])
    assert reason == ""
    assert isinstance(contract, HubReviewedContractV2)
    assert contract.remote_auth_policy.policy_fingerprint == remote["auth_policy"][
        "policy_fingerprint"
    ]
    assert secret not in contract_export(contract).decode("utf-8")
    assert bridge.auth_envelopes
    assert all(envelope["target_id"] == candidate["candidate_id"] for envelope in bridge.auth_envelopes)


@pytest.mark.asyncio
async def test_oauth_sop_publishes_v3_but_runtime_remains_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_REMOTE_OAUTH_REVIEW_ENABLED", "true")
    normalized = normalize_registry_entry(oauth_registry_entry())
    remote = normalized["remotes"][0]
    hub_store = MCPHubStore(tmp_path / "hub")
    hub_store.replace_snapshot("oauth_seed", [normalized], '"seed"')
    bridge = FakeBridge()
    hub = MCPHubService(
        hub_store,
        tenant_id="local",
        owner_id="local",
        bridge=bridge,
        reviewed_contracts=None,
    )
    policy_fields = {
        "schema_version": "remote-oauth-policy-v2",
        "mode": "oauth_authorization_code_pkce",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "resource_uri": remote["url"],
        "origin": remote["origin"],
        "remote_url_digest": hashlib.sha256(remote["url"].encode()).hexdigest(),
        "protected_resource_metadata_url": "https://oauth-review.example.com/.well-known/oauth-protected-resource/mcp",
        "protected_resource_metadata_digest": "1" * 64,
        "issuer": "https://auth.example/",
        "authorization_server_metadata_url": "https://auth.example/.well-known/oauth-authorization-server",
        "authorization_server_metadata_digest": "2" * 64,
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "",
        "revocation_endpoint": "https://auth.example/revoke",
        "client_id_metadata_document_supported": True,
        "scopes_supported": ("mcp:read",),
        "scope_source": "protected_resource_metadata",
        "recommended_scopes": ("mcp:read",),
        "recommended_scope_digest": canonical_digest(["mcp:read"]),
        "offline_access_available": True,
    }
    policy = RemoteOAuthPolicyV2(
        **policy_fields,
        policy_fingerprint=canonical_digest(policy_fields),
    )

    class Authorization:
        subject_resolver = SimpleNamespace(
            resolve=lambda: SubjectScopeV1(
                tenant_id="local", owner_id="local", mode="local-single-owner"
            )
        )
        token_revision_digest = "5" * 64

        def execution_metadata(self, **kwargs: Any) -> RemoteOAuthExecutionMetadataV1:
            return RemoteOAuthExecutionMetadataV1(
                target_type="hub_candidate",
                target_id=kwargs["target_id"],
                origin=policy.origin,
                resource_uri=policy.resource_uri,
                resource_digest=hashlib.sha256(policy.resource_uri.encode()).hexdigest(),
                policy_fingerprint=policy.policy_fingerprint,
                discovery_fingerprint="3" * 64,
                registration_digest="4" * 64,
                scope_source=policy.scope_source,
                scopes=("mcp:read",),
                scope_digest=canonical_digest(["mcp:read"]),
                token_revision_digest=self.token_revision_digest,
                expires_at=time.time() + 600,
            )

        @contextmanager
        def resolve_for_execution(self, **_kwargs: Any) -> Any:
            envelope = SimpleNamespace(authorization_value="Bearer oauth-test-secret")
            try:
                yield envelope
            finally:
                envelope.authorization_value = ""

    class OAuthMetadataStore:
        @staticmethod
        def active_discovery(**_kwargs: Any) -> Any:
            return SimpleNamespace(policy=policy)

    oauth = SimpleNamespace(
        authorization_service=Authorization(),
        subject_resolver=Authorization.subject_resolver,
        store=OAuthMetadataStore(),
    )
    hub.set_remote_oauth(oauth)
    review = MCPHubReviewService(
        hub,
        MCPHubReviewStore(hub_store),
        signing_key="review-signing-key-with-more-than-32-bytes",
        repository_dir=tmp_path / "contracts",
    )
    hub.contract_registry = review.contracts
    hub.set_review_service(review)
    run = review.create_run(
        [{
            "server_name": normalized["server_name"],
            "version": normalized["version"],
            "remote_id": remote["remote_id"],
        }]
    )
    await review._tasks[run["run_id"]]
    item = review.store.require_run(run["run_id"], "local", "local")["items"][0]
    assert item["state"] == "awaiting_call_approval"
    assert item["evidence"]["sop_version"] == "oauth_https_tools_v1"
    assert "oauth-test-secret" not in json.dumps(item["evidence"])
    oauth.authorization_service.token_revision_digest = "6" * 64
    envelope_count = len(bridge.auth_envelopes)
    with pytest.raises(HubError) as preflight_drifted:
        await hub.preflight_oauth_review(
            item["candidate_id"],
            expected_oauth_context={
                "policy_fingerprint": item["evidence"]["oauth_policy_fingerprint"],
                "scope_digest": item["evidence"]["authorized_scope_digest"],
                "token_revision_digest": item["evidence"]["token_revision_digest"],
                "resource_digest": item["evidence"]["resource_digest"],
                "discovery_fingerprint": item["evidence"]["discovery_fingerprint"],
                "registration_digest": item["evidence"]["registration_digest"],
            },
        )
    assert preflight_drifted.value.code == "mcp_remote_oauth_contract_scope_drift"
    assert len(bridge.auth_envelopes) == envelope_count
    with pytest.raises(HubError) as drifted:
        await review.approve_proposal(
            run["run_id"],
            item["item_id"],
            item["proposal"]["proposal_id"],
            item["proposal"]["proposal_digest"],
        )
    assert drifted.value.code == "mcp_remote_oauth_contract_scope_drift"
    assert bridge.call_count == 0
    oauth.authorization_service.token_revision_digest = "5" * 64
    await review.approve_proposal(
        run["run_id"],
        item["item_id"],
        item["proposal"]["proposal_id"],
        item["proposal"]["proposal_digest"],
    )
    item = review.store.require_item(run["run_id"], item["item_id"], "local", "local")
    decided = review.decide(
        run["run_id"],
        item["item_id"],
        decision="approve",
        expected_evidence_digest=item["evidence_digest"],
        allowed_tools=["search"],
        tool_effects={"search": "read"},
    )
    published = review.publish(
        run["run_id"], item["item_id"], decided["contract_fingerprint"]
    )
    assert published["activation_eligible"] is False
    assert published["activation_reason"] == "mcp_remote_oauth_runtime_disabled"
    contract, reason = review.contracts.get_contract(published["contract_id"])
    assert reason == ""
    assert isinstance(contract, HubReviewedContractV3)
    candidate = hub.store.list_candidates("local", "local")[0]
    assert hub._activation_review(candidate) == (
        False,
        "mcp_remote_oauth_runtime_disabled",
    )
    with pytest.raises(HubError) as public_preflight:
        await hub.preflight(candidate["candidate_id"])
    assert public_preflight.value.code == "mcp_remote_oauth_runtime_disabled"
    assert bridge.call_count == 1
    assert await HubMCPToolsetProvider(hub).list_tools() == []
    assert all(
        envelope.get("auth_mode") == "oauth_authorization_code_pkce"
        for envelope in bridge.auth_envelopes
    )

def test_deterministic_proposal_rejects_sensitive_and_unbounded_schemas() -> None:
    assert assess_oauth_scopes(("files.read", "account.admin")) == {
        "classification": "dangerous",
        "dangerous_scopes": ["account.admin"],
        "unknown_scopes": [],
        "read_candidate_scopes": ["files.read"],
    }
    assert deterministic_arguments(TOOLS[0]["input_schema"]) == {
        "query": "modelmirror-review"
    }
    assert deterministic_arguments(TOOLS[1]["input_schema"]) is None
    assert deterministic_arguments(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
    ) is None


def test_reproducible_selection_uses_namespace_for_missing_publisher(
    tmp_path: Path,
) -> None:
    hub_store = MCPHubStore(tmp_path)
    entries = []
    for index in range(3):
        raw = registry_entry(
            name=f"io.publisher{index}/service",
            url=f"https://service{index}.example.com/mcp",
        )
        raw["server"].pop("publisher", None)
        entries.append(normalize_registry_entry(raw))
    hub_store.replace_snapshot("hub_sync_seed", entries, '"seed"')
    hub = MCPHubService(
        hub_store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=FakeBridge(),
        reviewed_contracts=None,
    )
    review = MCPHubReviewService(
        hub,
        MCPHubReviewStore(hub_store),
        signing_key="review-signing-key-with-more-than-32-bytes",
        repository_dir=tmp_path / "empty",
    )
    assert len(review.reproducible_registry_selection(3)) == 3


@pytest.mark.asyncio
async def test_review_publish_activate_subset_revoke_and_republish(tmp_path: Path) -> None:
    hub, review, bridge = make_factory(tmp_path)
    run = await completed_run(review)
    item = run["items"][0]
    assert run["status"] == "awaiting_operator"
    assert item["state"] == "awaiting_call_approval"
    assert item["proposal"]["arguments"] == {"query": "modelmirror-review"}

    call = await review.approve_proposal(
        run["run_id"],
        item["item_id"],
        item["proposal"]["proposal_id"],
        item["proposal"]["proposal_digest"],
    )
    assert call["state"] == "awaiting_decision"
    assert len(call["preview"].encode("utf-8")) <= 4096
    assert bridge.call_count == 1
    item = review.store.require_item(
        run["run_id"], item["item_id"], review.tenant_id, review.owner_id
    )
    decided = review.decide(
        run["run_id"],
        item["item_id"],
        decision="approve",
        expected_evidence_digest=item["evidence_digest"],
        allowed_tools=["search"],
        tool_effects={"search": "read"},
    )
    fingerprint = decided["contract_fingerprint"]
    published = review.publish(run["run_id"], item["item_id"], fingerprint)
    assert published["activation_eligible"] is True
    exported = normalize_contract(
        json.loads(review.export_contract(run["run_id"], item["item_id"]))
    )
    assert exported.contract_fingerprint == fingerprint

    candidate = hub.get_candidate(item["candidate_id"])
    assert candidate["activation_eligible"] is True
    active = await hub.activate(candidate["candidate_id"], candidate["schema_digest"])
    assert active["state"] == "active"
    runtime = hub.runtime_tools()
    assert [tool["upstream_tool_name"] for tool in runtime] == ["search"]
    assert runtime[0]["contract_fingerprint"] == fingerprint

    revoked = await review.revoke(published["contract_id"], "operator test")
    assert revoked["disconnected_candidates"] == 1
    assert hub.runtime_tools() == []
    republished = review.publish(run["run_id"], item["item_id"], fingerprint)
    assert republished["contract_fingerprint"] == fingerprint
    assert hub.get_candidate(item["candidate_id"])["activation_eligible"] is True

    changed = registry_entry()
    changed["server"]["description"] = "Registry metadata drift"
    hub.store.replace_snapshot(
        "hub_sync_changed",
        [normalize_registry_entry(changed)],
        '"changed"',
    )
    await review.reconcile_registry_drift()
    await asyncio.sleep(0)
    assert hub.get_candidate(item["candidate_id"])["state"] == "drifted"
    drift_runs = [
        current
        for current in review.store.list_runs(review.tenant_id, review.owner_id)
        if current["run_id"] != run["run_id"]
    ]
    assert len(drift_runs) == 1
    assert drift_runs[0]["items"][0]["server_name"] == "io.example/reviewable"


@pytest.mark.asyncio
async def test_re_review_same_execution_contract_does_not_collide_on_new_evidence(
    tmp_path: Path,
) -> None:
    hub, review, _bridge = make_factory(tmp_path)

    async def review_and_publish() -> tuple[dict[str, Any], dict[str, Any]]:
        run = await completed_run(review)
        item = run["items"][0]
        proposal = item["proposal"]
        await review.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )
        item = review.store.require_item(
            run["run_id"], item["item_id"], review.tenant_id, review.owner_id
        )
        decided = review.decide(
            run["run_id"],
            item["item_id"],
            decision="approve",
            expected_evidence_digest=item["evidence_digest"],
            allowed_tools=["search"],
            tool_effects={"search": "read"},
        )
        published = review.publish(
            run["run_id"], item["item_id"], decided["contract_fingerprint"]
        )
        return item, published

    first_item, first = await review_and_publish()
    await review.revoke(first["contract_id"], "force a fresh reviewed revision")
    second_item, second = await review_and_publish()

    assert first_item["evidence_digest"] != second_item["evidence_digest"]
    assert first["contract_fingerprint"] == second["contract_fingerprint"]
    contract, reason = review.contracts.lookup_identity(
        "io.example/reviewable", "1.0.0", "https://review.example.com/mcp"
    )
    assert contract is not None
    assert reason == ""
    assert hub.get_candidate(second_item["candidate_id"])["activation_eligible"] is True


@pytest.mark.asyncio
async def test_call_approval_replay_and_unknown_outcome_are_fail_closed(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge()
    bridge.fail_call = True
    hub, review, _ = make_factory(tmp_path, bridge=bridge)
    run = await completed_run(review)
    item = run["items"][0]
    proposal = item["proposal"]
    with pytest.raises(HubUnknownOutcomeError):
        await review.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )
    assert bridge.call_count == 1
    assert hub.get_candidate(item["candidate_id"])["state"] == "tainted"
    with pytest.raises(HubError) as replay:
        await review.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )
    assert replay.value.code in {"hub_review_proposal_digest", "hub_review_state_conflict"}
    assert bridge.call_count == 1


@pytest.mark.asyncio
async def test_owner_batch_limit_and_cross_owner_access(tmp_path: Path) -> None:
    _hub, review, _bridge = make_factory(tmp_path)
    server = review.hub.get_server("io.example/reviewable", "1.0.0")
    identity = {
        "server_name": server["server_name"],
        "version": server["version"],
        "remote_id": server["remotes"][0]["remote_id"],
    }
    with pytest.raises(HubError) as too_large:
        review.store.create_run(
            review.tenant_id, review.owner_id, [dict(identity) for _ in range(21)]
        )
    assert too_large.value.code == "hub_review_batch_size"
    run = review.create_run([identity])
    with pytest.raises(HubError) as busy:
        review.create_run([identity])
    assert busy.value.code == "hub_review_owner_busy"
    with pytest.raises(HubError) as cross_owner:
        review.store.require_run(run["run_id"], review.tenant_id, "owner-b")
    assert cross_owner.value.code == "hub_review_run_not_found"
    await review._tasks[run["run_id"]]


@pytest.mark.asyncio
async def test_no_safe_proposal_blocks_without_remote_call(tmp_path: Path) -> None:
    dangerous_only = [dict(TOOLS[1])]
    _hub, review, bridge = make_factory(tmp_path, bridge=FakeBridge(dangerous_only))
    run = await completed_run(review)
    item = run["items"][0]
    assert item["state"] == "blocked"
    assert item["error_code"] == "manual_call_unavailable"
    assert item["proposal"] is None
    assert bridge.call_count == 0


@pytest.mark.asyncio
async def test_failed_preflight_keeps_bounded_fixed_evidence(tmp_path: Path) -> None:
    _hub, review, _bridge = make_factory(
        tmp_path, bridge=FailingPreflightBridge()
    )
    run = await completed_run(review)
    item = run["items"][0]
    assert item["state"] == "blocked"
    assert item["error_code"] == "hub_egress_dns_denied"
    assert item["evidence_digest"] == stable_digest(item["evidence"])
    assert item["evidence"]["fixed_errors"] == ["hub_egress_dns_denied"]
    assert len(json.dumps(item["evidence"]).encode("utf-8")) < 512 * 1024


@pytest.mark.asyncio
async def test_restart_marks_started_representative_call_unknown(tmp_path: Path) -> None:
    hub, review, bridge = make_factory(tmp_path)
    run = await completed_run(review)
    item = run["items"][0]
    proposal = item["proposal"]
    review.store.begin_call(proposal, item["candidate_id"])

    restarted_hub = MCPHubService(
        MCPHubStore(tmp_path),
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=bridge,
        reviewed_contracts=None,
    )
    restarted = MCPHubReviewService(
        restarted_hub,
        MCPHubReviewStore(restarted_hub.store),
        signing_key="review-signing-key-with-more-than-32-bytes",
        repository_dir=tmp_path / "repo-contracts",
    )
    restarted_hub.contract_registry = restarted.contracts
    await restarted.start()

    recovered = restarted.store.require_item(
        run["run_id"], item["item_id"], "tenant-a", "owner-a"
    )
    assert recovered["state"] == "unknown_outcome"
    assert restarted_hub.get_candidate(item["candidate_id"])["state"] == "tainted"
    assert bridge.call_count == 0
    await restarted.close()


@pytest.mark.asyncio
async def test_interrupted_safe_stage_can_resume(tmp_path: Path) -> None:
    _hub, review, _bridge = make_factory(tmp_path)
    server = review.hub.get_server("io.example/reviewable", "1.0.0")
    run = review.store.create_run(
        review.tenant_id,
        review.owner_id,
        [{
            "server_name": server["server_name"],
            "version": server["version"],
            "remote_id": server["remotes"][0]["remote_id"],
        }],
    )
    item = run["items"][0]
    review.store.set_run(run["run_id"], status="interrupted")
    review.store.set_item(item["item_id"], state="interrupted", stage="static_policy")
    resumed = review.resume(run["run_id"])
    assert resumed["status"] == "queued"
    await review._tasks[run["run_id"]]
    current = review.store.require_run(run["run_id"], review.tenant_id, review.owner_id)
    assert current["items"][0]["state"] == "awaiting_call_approval"


@pytest.mark.asyncio
async def test_interrupted_unsafe_stage_cannot_resume(tmp_path: Path) -> None:
    _hub, review, _bridge = make_factory(tmp_path)
    server = review.hub.get_server("io.example/reviewable", "1.0.0")
    run = review.store.create_run(
        review.tenant_id,
        review.owner_id,
        [{
            "server_name": server["server_name"],
            "version": server["version"],
            "remote_id": server["remotes"][0]["remote_id"],
        }],
    )
    item = run["items"][0]
    review.store.set_run(run["run_id"], status="interrupted")
    review.store.set_item(
        item["item_id"], state="interrupted", stage="network_preflight"
    )

    with pytest.raises(HubError) as denied:
        review.resume(run["run_id"])

    assert denied.value.code == "hub_review_resume_unsafe_stage"
    current = review.store.require_run(
        run["run_id"], review.tenant_id, review.owner_id
    )
    assert current["status"] == "interrupted"
    assert current["items"][0]["state"] == "interrupted"


@pytest.mark.asyncio
async def test_cancel_during_preflight_cannot_resurrect_item(tmp_path: Path) -> None:
    bridge = BlockingPreflightBridge()
    _hub, review, _bridge = make_factory(tmp_path, bridge=bridge)
    server = review.hub.get_server("io.example/reviewable", "1.0.0")
    run = review.create_run(
        [{
            "server_name": server["server_name"],
            "version": server["version"],
            "remote_id": server["remotes"][0]["remote_id"],
        }]
    )
    await asyncio.wait_for(bridge.authorize_started.wait(), timeout=1)

    cancelled = review.cancel(run["run_id"])
    assert cancelled["status"] == "cancelled"
    bridge.authorize_release.set()
    await asyncio.wait_for(review._tasks[run["run_id"]], timeout=1)

    current = review.store.require_run(
        run["run_id"], review.tenant_id, review.owner_id
    )
    assert current["status"] == "cancelled"
    assert current["items"][0]["state"] == "cancelled"
    assert current["items"][0]["proposal"] is None


@pytest.mark.asyncio
async def test_cancel_before_call_ledger_prevents_remote_call(tmp_path: Path) -> None:
    bridge = BlockingApprovalPreflightBridge()
    _hub, review, _bridge = make_factory(tmp_path, bridge=bridge)
    run = await completed_run(review)
    item = run["items"][0]
    proposal = item["proposal"]
    approval = asyncio.create_task(
        review.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )
    )
    await asyncio.wait_for(bridge.approval_preflight_started.wait(), timeout=1)

    cancelled = review.cancel(run["run_id"])
    assert cancelled["status"] == "cancelled"
    bridge.approval_preflight_release.set()
    with pytest.raises(HubError) as denied:
        await asyncio.wait_for(approval, timeout=1)

    assert denied.value.code == "hub_review_cancelled"
    assert bridge.call_count == 0
    current = review.store.require_run(
        run["run_id"], review.tenant_id, review.owner_id
    )
    assert current["status"] == "cancelled"
    assert current["items"][0]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_review_api_rejects_network_fields_and_arbitrary_call_arguments(
    tmp_path: Path,
) -> None:
    _hub, review, _bridge = make_factory(tmp_path)
    configure_mcp_hub_review(review)
    server = review.hub.get_server("io.example/reviewable", "1.0.0")
    identity = {
        "server_name": server["server_name"],
        "version": server["version"],
        "remote_id": server["remotes"][0]["remote_id"],
    }
    app = FastAPI()
    app.include_router(review_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/mcp/hub/review-runs",
            json={"items": [{**identity, "url": "https://attacker.invalid/mcp"}]},
        )
        assert rejected.status_code == 422
        created = await client.post("/api/mcp/hub/review-runs", json={"items": [identity]})
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        await review._tasks[run_id]
        current = await client.get(f"/api/mcp/hub/review-runs/{run_id}")
        item = current.json()["items"][0]
        rejected_arguments = await client.post(
            f"/api/mcp/hub/review-runs/{run_id}/items/{item['item_id']}/call-proposals",
            json={"arguments": {"query": "client-controlled"}},
        )
        assert rejected_arguments.status_code == 422
        assert rejected_arguments.json()["detail"]["code"] == "hub_review_arbitrary_arguments_denied"
        proposal = await client.post(
            f"/api/mcp/hub/review-runs/{run_id}/items/{item['item_id']}/call-proposals"
        )
        assert proposal.status_code == 200
        assert proposal.json()["arguments"] == {"query": "modelmirror-review"}
        missing_generic_call = await client.post(
            f"/api/mcp/hub/review-runs/{run_id}/items/{item['item_id']}/call",
            json={"tool": "search", "arguments": {}},
        )
        assert missing_generic_call.status_code == 404
