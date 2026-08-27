from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from pydantic import ValidationError

from server.mcp.catalog import (
    CATALOG_ADAPTERS,
    CatalogAdapterManifest,
    CatalogCredentialSlotPolicy,
    CatalogToolPolicy,
    MCPCatalogService,
)
from server.mcp.hub import HubError, MCPHubService
from server.mcp.remote_review import (
    CatalogRemoteContractRegistry,
    CatalogReviewedRemoteContractV1,
    CatalogProjectReviewAdapter,
    MCPRemoteReviewService,
    MCPRemoteReviewStore,
    RemoteTargetRefV1,
    ResolvedRemoteContractV1,
    CatalogOAuthAuthorizeRequest,
    RemoteReviewRunCreateRequest,
    catalog_contract_export,
    catalog_contract_signature,
    catalog_manifest_source_digest,
    stable_catalog_contract_id,
    _clean_item_id,
    _clean_run_id,
)


SECRET = "catalog-static-secret-that-must-not-persist"
TOOLS = [
    {
        "name": "search",
        "description": "Search public metadata",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 64},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]


def static_manifest(
    *,
    project_id: str = "catalog-remote-static",
    endpoint: str = "https://catalog.example.com/mcp",
    availability: str = "planned",
    allowed_inert_server_capabilities: tuple[str, ...] = (),
) -> CatalogAdapterManifest:
    return CatalogAdapterManifest(
        project_id=project_id,
        wave=4,
        availability=availability,  # type: ignore[arg-type]
        connection_kind="remote-mcp",
        risk="medium",
        required_capabilities=("remote-review", "credential-binding"),
        limitations=("read-only contract review",),
        adapter_version="1.2.3",
        network_policy="allowlist:catalog.example.com",
        transport="streamable-http",
        endpoint=endpoint,
        credential_slots=("api_token",),
        credential_policies=(
            CatalogCredentialSlotPolicy(
                key="api_token",
                label="API Token",
                description="Fixed remote token",
            ),
        ),
        tool_policies={"search": CatalogToolPolicy(effect="read")},
        remote_auth_mode="static_bearer",
        remote_auth_header_name="Authorization",
        allowed_inert_server_capabilities=allowed_inert_server_capabilities,  # type: ignore[arg-type]
    )


def oauth_manifest() -> CatalogAdapterManifest:
    return CatalogAdapterManifest(
        project_id="catalog-remote-oauth",
        wave=10,
        availability="planned",
        connection_kind="remote-mcp",
        risk="high",
        required_capabilities=("oauth-pkce", "remote-review"),
        limitations=("read-only contract review",),
        adapter_version="official/oauth-server@0123456789abcdef",
        network_policy="allowlist:oauth.example.com",
        transport="streamable-http",
        endpoint="https://oauth.example.com/mcp",
        remote_auth_mode="oauth_authorization_code_pkce",
        remote_auth_header_name="Authorization",
        remote_oauth_registration_mode="dynamic",
    )


class FakeCatalog:
    def __init__(
        self,
        manifests: dict[str, CatalogAdapterManifest],
        *,
        tenant_id: str = "tenant-a",
        owner_id: str = "owner-a",
    ) -> None:
        self.manifests = manifests
        self.tenant_id = tenant_id
        self.owner_id = owner_id

    def get_manifest(self, project_id: str) -> CatalogAdapterManifest:
        try:
            return self.manifests[project_id]
        except KeyError:
            raise HubError(
                "missing catalog project",
                code="mcp_catalog_project_not_found",
                status_code=404,
            ) from None

    def _catalog_remote_auth_policy(self, manifest: CatalogAdapterManifest) -> Any:
        return MCPCatalogService._catalog_remote_auth_policy(self, manifest)

    def _remote_review_credential_ready(
        self, _manifest: CatalogAdapterManifest
    ) -> bool:
        return True


class FakeBroker:
    def __init__(self) -> None:
        self.binding = SimpleNamespace(binding_id="mcpra_test", revision=1)
        self.handlers: list[Any] = []

    def add_target_change_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    def binding_for_target(self, **kwargs: Any) -> Any:
        assert kwargs["target_type"] == "catalog_project"
        return self.binding

    @contextmanager
    def resolve_for_execution(self, binding_id: str, **kwargs: Any) -> Iterator[Any]:
        policy = kwargs["current_policy"]
        assert binding_id == self.binding.binding_id
        yield SimpleNamespace(
            binding_id=binding_id,
            binding_revision=self.binding.revision,
            header_name=policy.header_name,
            header_value=SECRET,
            origin=policy.origin,
            policy_fingerprint=policy.policy_fingerprint,
        )

    def notify(self, project_id: str) -> None:
        self.binding.revision += 1
        for handler in tuple(self.handlers):
            handler("catalog_project", project_id)


class FakeBridge:
    def __init__(self) -> None:
        self.call_count = 0
        self.fail_call = False
        self.allow_unauthenticated = False
        self.fail_close_before_call = False
        self.fail_close_after_call = False
        self.closed: list[str] = []
        self.revoked: list[str] = []
        self.auth_refs: list[dict[str, Any]] = []
        self.authorized: list[tuple[str, str]] = []
        self.inert_capability_requests: list[tuple[str, ...]] = []
        self.tools = list(TOOLS)

    async def authorize(self, target_id: str, url: str) -> str:
        self.authorized.append((target_id, url))
        return "a" * 64

    async def revoke(self, capability: str) -> None:
        self.revoked.append(capability)

    async def open(
        self,
        target_id: str,
        url: str,
        capability: str,
        session_owner: str,
        *,
        auth: dict[str, Any] | None = None,
        allowed_inert_capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        assert target_id.startswith("mcphub_")
        assert len(target_id) == len("mcphub_") + 32
        assert all(character in "0123456789abcdef" for character in target_id[7:])
        assert url == "https://catalog.example.com/mcp"
        assert capability == "a" * 64
        assert session_owner == f"hub:tenant-a:owner-a:{target_id}"
        self.inert_capability_requests.append(allowed_inert_capabilities)
        if auth is None:
            if self.allow_unauthenticated:
                return {"session_id": "hubsession_" + "a" * 32, "tools": self.tools}
            raise HubError(
                "authentication required",
                code="mcp_remote_auth_unauthorized",
                status_code=401,
            )
        self.auth_refs.append(auth)
        assert auth["target_id"] == target_id
        return {"session_id": "hubsession_" + "b" * 32, "tools": self.tools}

    async def list_tools(self, session_id: str) -> dict[str, Any]:
        assert session_id.startswith("hubsession_")
        return {"tools": self.tools}

    async def call(
        self, session_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.call_count += 1
        if self.fail_call:
            raise HubError(
                "connection lost after dispatch",
                code="hub_sidecar_unavailable",
                status_code=503,
            )
        return {
            "result": {
                "content": [{"type": "text", "text": arguments["query"]}],
                "isError": False,
            }
        }

    async def close(self, session_id: str) -> None:
        if self.fail_close_before_call and not self.call_count:
            raise RuntimeError("forced preflight close failure")
        if self.fail_close_after_call and self.call_count:
            raise RuntimeError("forced close failure")
        self.closed.append(session_id)


class FakeHub:
    def __init__(self, bridge: FakeBridge) -> None:
        self.bridge = bridge

    def _validate_tools(self, raw_tools: Any) -> tuple[list[dict[str, Any]], str]:
        return MCPHubService._validate_tools(self, raw_tools)


class FakeHubReview:
    tenant_id = "tenant-a"
    owner_id = "owner-a"

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.store = SimpleNamespace(list_runs=lambda *_args: list(self.runs))
        self.external_run_admission: Any = None

    def set_external_run_admission(
        self, admission: Any, _run_lock: Any = None
    ) -> None:
        self.external_run_admission = admission


class FakeAuthorization:
    def __init__(self) -> None:
        self.handlers: list[Any] = []
        self.authorization_calls: list[dict[str, Any]] = []
        self.refresh_calls: list[dict[str, Any]] = []
        self.revoke_calls: list[dict[str, Any]] = []

    def set_target_change_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    def create_authorization(self, **kwargs: Any) -> dict[str, Any]:
        self.authorization_calls.append(kwargs)
        return {"authorization_url": "https://issuer.example.com/authorize?opaque=1"}

    async def refresh(self, **kwargs: Any) -> None:
        self.refresh_calls.append(kwargs)

    async def revoke_with_remote(self, **kwargs: Any) -> dict[str, Any]:
        self.revoke_calls.append(kwargs)
        return {
            "local_revocation": "completed",
            "remote_revocation": "not_requested",
            "remote_error_code": "",
        }


class FakeOAuth:
    def __init__(self) -> None:
        self.store = SimpleNamespace(active_discovery=lambda **_kwargs: None)
        self.subject_resolver = SimpleNamespace(resolve=lambda: None)
        self.discover_calls: list[dict[str, Any]] = []
        self.registration_calls: list[dict[str, Any]] = []
        self.recommended_scopes = ["mcp:read"]
        self.discovered = False

    async def discover(self, **kwargs: Any) -> None:
        self.discover_calls.append(kwargs)
        self.discovered = True

    async def register_client(self, **kwargs: Any) -> None:
        self.registration_calls.append(kwargs)

    def summary(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "target": kwargs,
            "discovery": (
                {
                    "discovery_fingerprint": "a" * 64,
                    "recommended_scopes": list(self.recommended_scopes),
                }
                if self.discovered
                else None
            ),
            "registration": None,
            "authorization_session": None,
            "token": None,
        }


def make_service(
    tmp_path: Path,
    *,
    bridge: FakeBridge | None = None,
    manifest: CatalogAdapterManifest | None = None,
) -> tuple[MCPRemoteReviewService, FakeBridge, FakeBroker, FakeCatalog]:
    manifest = manifest or static_manifest()
    catalog = FakeCatalog({manifest.project_id: manifest})
    bridge = bridge or FakeBridge()
    broker = FakeBroker()
    service = MCPRemoteReviewService(
        hub=FakeHub(bridge),  # type: ignore[arg-type]
        hub_review=FakeHubReview(),
        catalog=catalog,  # type: ignore[arg-type]
        broker=broker,  # type: ignore[arg-type]
        oauth=FakeOAuth(),  # type: ignore[arg-type]
        authorization=FakeAuthorization(),  # type: ignore[arg-type]
        store=MCPRemoteReviewStore(tmp_path),
        signing_key="catalog-review-signing-key-with-32-bytes",
    )
    return service, bridge, broker, catalog


def make_oauth_service(
    tmp_path: Path,
) -> tuple[MCPRemoteReviewService, FakeOAuth, FakeAuthorization]:
    manifest = oauth_manifest()
    catalog = FakeCatalog({manifest.project_id: manifest})
    oauth = FakeOAuth()
    authorization = FakeAuthorization()
    service = MCPRemoteReviewService(
        hub=FakeHub(FakeBridge()),  # type: ignore[arg-type]
        hub_review=FakeHubReview(),
        catalog=catalog,  # type: ignore[arg-type]
        broker=FakeBroker(),  # type: ignore[arg-type]
        oauth=oauth,  # type: ignore[arg-type]
        authorization=authorization,  # type: ignore[arg-type]
        store=MCPRemoteReviewStore(tmp_path),
        signing_key="catalog-review-signing-key-with-32-bytes",
    )
    return service, oauth, authorization


async def publish_static_contract(
    service: MCPRemoteReviewService,
) -> CatalogReviewedRemoteContractV1:
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    item = service.get_run(run["run_id"])["items"][0]
    proposal = item["proposal"]
    approved = await service.approve_proposal(
        run["run_id"],
        item["item_id"],
        proposal["proposal_id"],
        proposal["proposal_digest"],
    )
    decision = service.decide(
        run["run_id"],
        item["item_id"],
        decision="approve",
        expected_evidence_digest=approved["evidence_digest"],
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        acknowledge_unknown_oauth_scopes=False,
    )
    published = service.publish(
        run["run_id"],
        item["item_id"],
        decision["contract_fingerprint"],
    )
    return CatalogReviewedRemoteContractV1.model_validate(published["contract"])


def runtime_approval(entry: dict[str, Any], *, tenant_id: str = "tenant-a") -> dict[str, Any]:
    arguments = {"query": "modelmirror"}
    from server.mcp.hub import arguments_digest

    return {
        "approval_id": str(uuid.uuid4()),
        "status": "decided",
        "decision": "approve",
        "tool_name": entry["name"],
        "metadata": {
            "remote_approval": {
                "target_type": "catalog_project",
                "target_id": entry["project_id"],
                "upstream_tool_name": entry["upstream_tool_name"],
                "tenant_id": tenant_id,
                "owner_id": "owner-a",
                "version": entry["version"],
                "origin": entry["origin"],
                "source_digest": entry["source_digest"],
                "auth_context_digest": entry["auth_context_digest"],
                "arguments_digest": arguments_digest(arguments),
                "schema_digest": entry["schema_digest"],
                "tool_schema_digest": entry["tool_schema_digest"],
                "contract_id": entry["contract_id"],
                "contract_fingerprint": entry["contract_fingerprint"],
            }
        },
    }


@pytest.fixture(autouse=True)
def enable_r4a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_REMOTE_REVIEW_UNIFICATION_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_STATIC_TOKEN_ENABLED", "true")


def test_catalog_target_adapter_requires_frozen_public_https_identity() -> None:
    allowed = static_manifest()
    adapter = CatalogProjectReviewAdapter(
        FakeCatalog({allowed.project_id: allowed})  # type: ignore[arg-type]
    )

    resolved = adapter.resolve(allowed.project_id)

    assert resolved["snapshot"].remote_url == "https://catalog.example.com/mcp"
    assert resolved["snapshot"].origin == "https://catalog.example.com"
    for endpoint in (
        "http://catalog.example.com/mcp",
        "https://catalog.example.com:8443/mcp",
        "https://catalog.example.com/mcp?token=x",
        "https://user@catalog.example.com/mcp",
        "https://127.0.0.1/mcp",
        "https://service.local/mcp",
        "https://catalog.example.com/{tenant}",
    ):
        denied = static_manifest(endpoint=endpoint)
        denied_adapter = CatalogProjectReviewAdapter(
            FakeCatalog({denied.project_id: denied})  # type: ignore[arg-type]
        )
        with pytest.raises(HubError) as captured:
            denied_adapter.resolve(denied.project_id)
        assert captured.value.code == "mcp_remote_catalog_manifest_ineligible"


def test_manifest_digest_and_static_binding_policy_include_remote_path() -> None:
    first = static_manifest(endpoint="https://catalog.example.com/mcp")
    second = static_manifest(endpoint="https://catalog.example.com/v2/mcp")
    catalog = FakeCatalog({first.project_id: first})

    first_policy = catalog._catalog_remote_auth_policy(first)
    second_policy = catalog._catalog_remote_auth_policy(second)

    assert catalog_manifest_source_digest(first) != catalog_manifest_source_digest(second)
    assert catalog_manifest_source_digest(first) != catalog_manifest_source_digest(
        static_manifest(
            endpoint="https://catalog.example.com/mcp",
            allowed_inert_server_capabilities=("completions",),
        )
    )
    assert first_policy.policy_fingerprint != second_policy.policy_fingerprint
    assert SECRET not in json.dumps(first.to_public())


def test_r4a_adds_exact_tako_identity_and_scopes_github_inert_capability() -> None:
    assert len(CATALOG_ADAPTERS) == 301

    github = CATALOG_ADAPTERS["github-mcp-server"]
    assert github.availability == "planned"
    assert github.endpoint == "https://api.githubcopilot.com/mcp/x/repos/readonly"
    assert github.remote_auth_mode == "static_bearer"
    assert github.remote_auth_header_name == "Authorization"
    assert github.enabled_by_default is False
    assert github.executable is False
    assert github.to_public()["remote_review_capable"] is True
    assert github.allowed_inert_server_capabilities == ("completions",)
    assert "6206edb67f08" in github.adapter_version

    sentry = CATALOG_ADAPTERS["sentry-mcp"]
    assert sentry.availability == "planned"
    assert sentry.endpoint == "https://mcp.sentry.dev/mcp"
    assert sentry.remote_auth_mode == "oauth_authorization_code_pkce"
    assert sentry.remote_oauth_registration_mode == "dynamic"
    assert sentry.enabled_by_default is False
    assert sentry.executable is False
    assert sentry.to_public()["remote_review_capable"] is True

    tako = CATALOG_ADAPTERS["tako-mcp"]
    assert tako.availability == "planned"
    assert tako.endpoint == "https://mcp.tako.com/mcp"
    assert tako.remote_auth_mode == "oauth_authorization_code_pkce"
    assert tako.remote_oauth_registration_mode == "dynamic"
    assert tako.enabled_by_default is False
    assert tako.executable is False
    assert tako.to_public()["remote_review_capable"] is True
    assert "io.github.TakoData/tako-mcp@0.22.2" in " ".join(
        tako.limitations
    )


def test_unified_requests_reject_client_target_and_oauth_fields() -> None:
    with pytest.raises(ValidationError):
        RemoteReviewRunCreateRequest.model_validate(
            {
                "items": [
                    {
                        "target_type": "catalog_project",
                        "target_id": "sentry-mcp",
                        "url": "https://attacker.invalid/mcp",
                    }
                ]
            }
        )

    with pytest.raises(HubError) as invalid_run:
        _clean_run_id("hubreview_not-a-real-id")
    assert invalid_run.value.code == "mcp_remote_review_identifier_invalid"

    with pytest.raises(HubError) as cross_era_item:
        _clean_item_id("hubitem_" + "a" * 32, hub=False)
    assert cross_era_item.value.code == "mcp_remote_review_identifier_invalid"


def test_unified_owner_cannot_start_catalog_run_while_hub_run_is_active(
    tmp_path: Path,
) -> None:
    service, _bridge, _broker, _catalog = make_service(tmp_path)
    service.hub_review.runs.append(
        {"run_id": "hubreview_" + "a" * 32, "status": "awaiting_operator"}
    )

    with pytest.raises(HubError) as busy:
        service.create_run(
            [
                RemoteTargetRefV1(
                    target_type="catalog_project",
                    target_id="catalog-remote-static",
                )
            ]
        )

    assert busy.value.code == "mcp_remote_review_owner_busy"


def test_legacy_hub_run_admission_rejects_active_catalog_run(tmp_path: Path) -> None:
    service, _bridge, _broker, _catalog = make_service(tmp_path)
    service.store.create_run(
        "tenant-a",
        "owner-a",
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ],
    )

    with pytest.raises(HubError) as busy:
        service.hub_review.external_run_admission()

    assert busy.value.code == "mcp_remote_review_owner_busy"

    with pytest.raises(ValidationError):
        CatalogOAuthAuthorizeRequest.model_validate(
            {
                "expected_discovery_fingerprint": "a" * 64,
                "expected_registration_digest": "b" * 64,
                "expected_scope_digest": "c" * 64,
                "request_refresh_token": False,
                "scope": "admin write",
            }
        )


@pytest.mark.asyncio
async def test_catalog_oauth_routes_derive_target_resource_and_never_accept_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CATALOG_OAUTH_ENABLED", "true")
    service, oauth, authorization = make_oauth_service(tmp_path)
    snapshot = CatalogProjectReviewAdapter(service.catalog).resolve(
        "catalog-remote-oauth"
    )["snapshot"]

    await service.catalog_oauth_discover("catalog-remote-oauth")
    assert oauth.discover_calls == [
        {
            "target_type": "catalog_project",
            "target_id": "catalog-remote-oauth",
            "resource_url": "https://oauth.example.com/mcp",
            "source_digest": snapshot.source_digest,
            "require_bearer_challenge": False,
        }
    ]

    await service.catalog_oauth_register(
        "catalog-remote-oauth", "a" * 64
    )
    assert oauth.registration_calls == [
        {
            "target_type": "catalog_project",
            "target_id": "catalog-remote-oauth",
            "source_digest": snapshot.source_digest,
            "expected_discovery_fingerprint": "a" * 64,
            "mode": "dynamic",
            "client_id": "",
        }
    ]

    authorization_result = await service.catalog_oauth_authorize(
        "catalog-remote-oauth",
        expected_discovery_fingerprint="a" * 64,
        expected_registration_digest="b" * 64,
        expected_scope_digest="c" * 64,
        request_refresh_token=True,
    )
    assert authorization_result["authorization_url"].startswith("https://")
    assert authorization.authorization_calls == [
        {
            "target_type": "catalog_project",
            "target_id": "catalog-remote-oauth",
            "source_digest": snapshot.source_digest,
            "expected_discovery_fingerprint": "a" * 64,
            "expected_registration_digest": "b" * 64,
            "expected_scope_digest": "c" * 64,
            "request_refresh_token": True,
        }
    ]
    assert "scope" not in authorization.authorization_calls[0]
    assert "resource_url" not in authorization.authorization_calls[0]

    token_id = "mcpoauthtoken_" + "d" * 32
    await service.catalog_oauth_refresh("catalog-remote-oauth", token_id, 7)
    assert authorization.refresh_calls == [
        {
            "target_type": "catalog_project",
            "target_id": "catalog-remote-oauth",
            "token_id": token_id,
            "expected_revision": 7,
        }
    ]
    revoked = await service.catalog_oauth_revoke(
        "catalog-remote-oauth", token_id
    )
    assert authorization.revoke_calls == [
        {
            "target_type": "catalog_project",
            "target_id": "catalog-remote-oauth",
            "token_id": token_id,
        }
    ]
    assert revoked["target_state"]["state"] == "revoked"


@pytest.mark.asyncio
async def test_catalog_oauth_high_risk_scopes_fail_closed_before_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CATALOG_OAUTH_ENABLED", "true")
    service, oauth, _authorization = make_oauth_service(tmp_path)
    await service.catalog_oauth_discover("catalog-remote-oauth")
    oauth.recommended_scopes = ["event:write", "org:read", "project:write"]

    summary = service.catalog_remote_summary("catalog-remote-oauth")
    assert summary["oauth"]["scope_assessment"]["dangerous_scopes"] == [
        "event:write",
        "project:write",
    ]
    with pytest.raises(HubError) as denied:
        await service.catalog_oauth_register("catalog-remote-oauth", "a" * 64)

    assert denied.value.code == "mcp_remote_oauth_high_risk_scope_denied"
    assert oauth.registration_calls == []


@pytest.mark.asyncio
async def test_catalog_static_review_publishes_contract_without_runtime_exposure(
    tmp_path: Path,
) -> None:
    service, bridge, _broker, _catalog = make_service(tmp_path)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    current = service.get_run(run["run_id"])
    item = current["items"][0]

    assert current["status"] == "awaiting_operator"
    assert item["state"] == "awaiting_call_approval"
    assert item["evidence"]["stages"]["unauthenticated_initialize_tools"] == {
        "status": "observed_auth_required",
        "implementation_version": "r4a-v1",
        "tool_count": 0,
        "error_code": "mcp_remote_auth_unauthorized",
    }
    assert item["evidence"]["cleanup"] == {
        "unauthenticated_session_closed": True,
        "unauthenticated_capability_revoked": True,
        "authenticated_session_closed": True,
        "authenticated_capability_revoked": True,
    }
    assert all(ref["header_value"] == "" for ref in bridge.auth_refs)

    proposal = item["proposal"]
    approved = await service.approve_proposal(
        run["run_id"],
        item["item_id"],
        proposal["proposal_id"],
        proposal["proposal_digest"],
    )
    assert approved["state"] == "awaiting_decision"
    assert bridge.call_count == 1

    evidence_digest = approved["evidence_digest"]
    decision = service.decide(
        run["run_id"],
        item["item_id"],
        decision="approve",
        expected_evidence_digest=evidence_digest,
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        acknowledge_unknown_oauth_scopes=False,
    )
    published = service.publish(
        run["run_id"],
        item["item_id"],
        decision["contract_fingerprint"],
    )

    assert published["activation_eligible"] is False
    assert published["runtime_tool_count"] == 0
    exported = service.export_contract(run["run_id"], item["item_id"])
    contract = CatalogReviewedRemoteContractV1.model_validate_json(exported)
    assert contract.allowed_tools == ["search"]
    assert contract.auth_mode == "static_bearer"
    resolved = ResolvedRemoteContractV1.from_catalog(contract)
    assert resolved.target.target_type == "catalog_project"
    assert "credential" not in exported.decode("utf-8").lower()
    assert SECRET not in exported.decode("utf-8")
    assert SECRET not in json.dumps(service.get_run(run["run_id"]), ensure_ascii=False)

    with pytest.raises(HubError) as replay:
        await service.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )
    assert replay.value.code in {
        "mcp_remote_review_proposal_digest",
        "mcp_remote_review_call_replay",
    }

    _catalog.manifests["catalog-remote-static"] = static_manifest(
        endpoint="https://catalog.example.com/v2/mcp"
    )
    drifted = service.catalog_remote_summary("catalog-remote-static")
    assert drifted["target_state"]["state"] == "drifted"
    assert (
        drifted["target_state"]["reason_code"]
        == "mcp_remote_contract_unreviewed"
    )


@pytest.mark.asyncio
async def test_catalog_runtime_requires_both_default_off_flags(
    tmp_path: Path,
) -> None:
    service, _bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)

    with pytest.raises(HubError) as disabled:
        await service.activate_catalog_runtime(
            "catalog-remote-static", contract.contract_fingerprint
        )

    assert disabled.value.code == "mcp_remote_contract_runtime_disabled"
    assert service.catalog_runtime_tools() == []


@pytest.mark.asyncio
async def test_catalog_runtime_activation_and_single_approved_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)

    activated = await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    tools = service.catalog_runtime_tools()

    assert activated["target_state"]["state"] == "active"
    assert activated["runtime_tool_count"] == 1
    assert len(tools) == 1
    entry = tools[0]
    assert entry["name"].startswith("catalog__catalog_remote_static_")
    approval = runtime_approval(entry)
    result = await service.execute_catalog_runtime(
        project_id="catalog-remote-static",
        runtime_tool_name=entry["name"],
        upstream_tool_name="search",
        arguments={"query": "modelmirror"},
        approval=approval,
    )

    assert result["content"][0]["text"] == "modelmirror"
    assert bridge.call_count == 2  # representative review + one Runtime call
    replay = await service.execute_catalog_runtime(
        project_id="catalog-remote-static",
        runtime_tool_name=entry["name"],
        upstream_tool_name="search",
        arguments={"query": "modelmirror"},
        approval=approval,
    )
    assert replay == result
    assert bridge.call_count == 2
    assert bridge.closed
    # The initial unauthenticated 401 has no session to close, but its egress
    # capability must still be revoked.
    assert len(bridge.revoked) == len(bridge.closed) + 1


@pytest.mark.asyncio
async def test_catalog_runtime_cross_owner_approval_is_rejected_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    entry = service.catalog_runtime_tools()[0]

    with pytest.raises(HubError) as denied:
        await service.execute_catalog_runtime(
            project_id="catalog-remote-static",
            runtime_tool_name=entry["name"],
            upstream_tool_name="search",
            arguments={"query": "modelmirror"},
            approval=runtime_approval(entry, tenant_id="tenant-b"),
        )

    assert denied.value.code == "mcp_remote_runtime_approval_invalid"
    assert bridge.call_count == 1


@pytest.mark.asyncio
async def test_catalog_runtime_unknown_outcome_taints_and_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    entry = service.catalog_runtime_tools()[0]
    approval = runtime_approval(entry)
    bridge.fail_call = True

    with pytest.raises(HubError) as unknown:
        await service.execute_catalog_runtime(
            project_id="catalog-remote-static",
            runtime_tool_name=entry["name"],
            upstream_tool_name="search",
            arguments={"query": "modelmirror"},
            approval=approval,
        )

    assert unknown.value.code == "unknown_outcome"
    assert bridge.call_count == 2
    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "tainted"
    assert service.catalog_runtime_tools() == []
    with pytest.raises(HubError) as replay:
        await service.execute_catalog_runtime(
            project_id="catalog-remote-static",
            runtime_tool_name=entry["name"],
            upstream_tool_name="search",
            arguments={"query": "modelmirror"},
            approval=approval,
        )
    assert replay.value.code == "unknown_outcome"
    assert bridge.call_count == 2


@pytest.mark.asyncio
async def test_catalog_runtime_binding_is_removed_on_credential_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, _bridge, broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    assert len(service.catalog_runtime_tools()) == 1

    broker.notify("catalog-remote-static")

    assert service.catalog_runtime_tools() == []
    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "drifted"
    assert state.reason_code == "mcp_remote_auth_binding_revision_changed"


@pytest.mark.asyncio
async def test_old_approval_cannot_cross_credential_rotation_and_reactivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, bridge, broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    old_entry = service.catalog_runtime_tools()[0]
    old_approval = runtime_approval(old_entry)

    broker.notify("catalog-remote-static")
    refreshed_contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", refreshed_contract.contract_fingerprint
    )

    with pytest.raises(HubError) as denied:
        await service.execute_catalog_runtime(
            project_id="catalog-remote-static",
            runtime_tool_name=old_entry["name"],
            upstream_tool_name="search",
            arguments={"query": "modelmirror"},
            approval=old_approval,
        )

    assert denied.value.code == "mcp_remote_runtime_approval_invalid"
    assert bridge.call_count == 2  # only the two review representative calls


@pytest.mark.asyncio
async def test_started_runtime_ledger_is_tainted_on_process_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, _bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    entry = service.catalog_runtime_tools()[0]
    approval = runtime_approval(entry)
    service.store.begin_runtime_execution(
        approval["approval_id"],
        tenant_id="tenant-a",
        owner_id="owner-a",
        target=RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
        contract_fingerprint=entry["contract_fingerprint"],
        tool_name=entry["name"],
        args_digest=approval["metadata"]["remote_approval"]["arguments_digest"],
    )

    await service.start()

    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "tainted"
    assert state.reason_code == "unknown_outcome"
    assert service.catalog_runtime_tools() == []


@pytest.mark.asyncio
async def test_schema_drift_after_activation_blocks_before_tool_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    entry = service.catalog_runtime_tools()[0]
    bridge.tools = [
        {
            **TOOLS[0],
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "integer"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    ]

    with pytest.raises(HubError) as drifted:
        await service.execute_catalog_runtime(
            project_id="catalog-remote-static",
            runtime_tool_name=entry["name"],
            upstream_tool_name="search",
            arguments={"query": "modelmirror"},
            approval=runtime_approval(entry),
        )

    assert drifted.value.code == "hub_schema_drift"
    assert bridge.call_count == 1
    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "drifted"
    assert service.catalog_runtime_tools() == []


@pytest.mark.asyncio
async def test_concurrent_replay_of_one_approval_dispatches_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MCP_REMOTE_CATALOG_RUNTIME_ENABLED", "true")
    service, bridge, _broker, _catalog = make_service(tmp_path)
    contract = await publish_static_contract(service)
    await service.activate_catalog_runtime(
        "catalog-remote-static", contract.contract_fingerprint
    )
    entry = service.catalog_runtime_tools()[0]
    approval = runtime_approval(entry)

    async def execute() -> dict[str, Any]:
        return await service.execute_catalog_runtime(
            project_id="catalog-remote-static",
            runtime_tool_name=entry["name"],
            upstream_tool_name="search",
            arguments={"query": "modelmirror"},
            approval=approval,
        )

    first, second = await asyncio.gather(execute(), execute())

    assert first == second
    assert bridge.call_count == 2  # review representative + exactly one Runtime call


@pytest.mark.asyncio
async def test_catalog_manifest_scopes_inert_completion_capability_to_sidecar(
    tmp_path: Path,
) -> None:
    service, bridge, _broker, _catalog = make_service(
        tmp_path,
        manifest=static_manifest(
            allowed_inert_server_capabilities=("completions",)
        ),
    )
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]

    assert bridge.inert_capability_requests
    assert set(bridge.inert_capability_requests) == {("completions",)}


@pytest.mark.asyncio
async def test_dispatched_call_failure_is_unknown_and_not_retryable(
    tmp_path: Path,
) -> None:
    service, bridge, _broker, _catalog = make_service(tmp_path)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    item = service.get_run(run["run_id"])["items"][0]
    proposal = item["proposal"]
    bridge.fail_call = True

    with pytest.raises(HubError) as unknown:
        await service.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )

    assert unknown.value.code == "unknown_outcome"
    assert bridge.call_count == 1
    current = service.get_run(run["run_id"])["items"][0]
    assert current["state"] == "unknown_outcome"
    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "tainted"


@pytest.mark.asyncio
async def test_known_result_with_cleanup_failure_is_blocked_not_unknown(
    tmp_path: Path,
) -> None:
    service, bridge, _broker, _catalog = make_service(tmp_path)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    item = service.get_run(run["run_id"])["items"][0]
    proposal = item["proposal"]
    bridge.fail_close_after_call = True

    with pytest.raises(HubError) as cleanup_failed:
        await service.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )

    assert cleanup_failed.value.code == "mcp_remote_review_cleanup_failed"
    assert bridge.call_count == 1
    current = service.get_run(run["run_id"])["items"][0]
    assert current["state"] == "blocked"
    assert current["evidence"]["representative_call"]["cleanup"] == {
        "temporary_session_closed": False,
        "capability_revoked": True,
    }
    completed_proposal = service.store.require_proposal(
        proposal["proposal_id"], "tenant-a", "owner-a"
    )
    assert completed_proposal["state"] == "completed"
    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "tainted"
    assert state.reason_code == "mcp_remote_review_cleanup_failed"


@pytest.mark.asyncio
async def test_unauthenticated_preflight_cleanup_failure_taints_and_stops_review(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge()
    bridge.allow_unauthenticated = True
    bridge.fail_close_before_call = True
    service, _bridge, _broker, _catalog = make_service(tmp_path, bridge=bridge)

    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]

    current = service.get_run(run["run_id"])["items"][0]
    assert current["state"] == "blocked"
    assert current["error_code"] == "mcp_remote_review_cleanup_failed"
    assert current["proposal"] is None
    assert bridge.call_count == 0
    assert bridge.auth_refs == []
    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "tainted"
    assert state.reason_code == "mcp_remote_review_cleanup_failed"


@pytest.mark.asyncio
async def test_cancel_during_authenticated_preflight_cannot_publish_proposal(
    tmp_path: Path,
) -> None:
    class BlockingBridge(FakeBridge):
        def __init__(self) -> None:
            super().__init__()
            self.auth_open_started = asyncio.Event()
            self.release_auth_open = asyncio.Event()

        async def open(
            self,
            target_id: str,
            url: str,
            capability: str,
            session_owner: str,
            *,
            auth: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if auth is not None:
                self.auth_open_started.set()
                await self.release_auth_open.wait()
            return await super().open(
                target_id,
                url,
                capability,
                session_owner,
                auth=auth,
            )

    bridge = BlockingBridge()
    service, _bridge, _broker, _catalog = make_service(tmp_path, bridge=bridge)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await asyncio.wait_for(bridge.auth_open_started.wait(), timeout=2)

    cancelled = service.cancel(run["run_id"])
    assert cancelled["status"] == "cancelled"
    bridge.release_auth_open.set()
    await service._tasks[run["run_id"]]

    current = service.get_run(run["run_id"])
    assert current["status"] == "cancelled"
    assert current["items"][0]["state"] == "cancelled"
    assert current["items"][0]["proposal"] is None
    assert bridge.call_count == 0
    assert bridge.revoked


@pytest.mark.asyncio
async def test_cancelled_operator_item_rejects_old_proposal(
    tmp_path: Path,
) -> None:
    service, bridge, _broker, _catalog = make_service(tmp_path)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    item = service.get_run(run["run_id"])["items"][0]
    proposal = item["proposal"]

    service.cancel(run["run_id"])

    cancelled = service.get_run(run["run_id"])["items"][0]
    assert cancelled["state"] == "cancelled"
    assert cancelled["proposal"]["state"] == "cancelled"
    with pytest.raises(HubError) as denied:
        await service.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )
    assert denied.value.code == "mcp_remote_review_cancelled"
    assert bridge.call_count == 0


@pytest.mark.asyncio
async def test_schema_drift_invalidates_old_proposal_without_calling_tool(
    tmp_path: Path,
) -> None:
    service, bridge, _broker, _catalog = make_service(tmp_path)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    item = service.get_run(run["run_id"])["items"][0]
    proposal = item["proposal"]
    bridge.tools = [
        {
            **TOOLS[0],
            "input_schema": {
                **TOOLS[0]["input_schema"],
                "properties": {
                    "query": {"type": "string", "maxLength": 32},
                },
            },
        }
    ]

    with pytest.raises(HubError) as drifted:
        await service.approve_proposal(
            run["run_id"],
            item["item_id"],
            proposal["proposal_id"],
            proposal["proposal_digest"],
        )

    assert drifted.value.code == "hub_schema_drift"
    assert bridge.call_count == 0
    current = service.get_run(run["run_id"])["items"][0]
    assert current["state"] == "drifted"
    assert current["proposal"]["state"] == "drifted"
    target = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert target.state == "drifted"


def test_contract_registry_rejects_collision_wrong_key_and_revocation(
    tmp_path: Path,
) -> None:
    manifest = static_manifest()
    catalog = FakeCatalog({manifest.project_id: manifest})
    policy = catalog._catalog_remote_auth_policy(manifest)
    base = dict(
        contract_id=stable_catalog_contract_id(
            manifest.project_id, manifest.adapter_version, manifest.endpoint
        ),
        project_id=manifest.project_id,
        version=manifest.adapter_version,
        remote_url=manifest.endpoint,
        origin="https://catalog.example.com",
        source_digest="1" * 64,
        auth_mode="static_bearer",
        remote_auth_policy=policy,
        schema_digest="2" * 64,
        tool_schema_digests={"search": "3" * 64},
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        limits={"max_output_bytes": 1024, "call_timeout_seconds": 10},
        evidence_digest="4" * 64,
    )
    first = CatalogReviewedRemoteContractV1(**base)
    second = CatalogReviewedRemoteContractV1(
        **{**base, "limits": {"max_output_bytes": 2048, "call_timeout_seconds": 10}}
    )
    store = MCPRemoteReviewStore(tmp_path)
    key = "catalog-review-signing-key-with-32-bytes"
    store.save_catalog_contract(
        "tenant-a", "owner-a", first, catalog_contract_signature(first, key)
    )
    wrong_key = CatalogRemoteContractRegistry(
        store=store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        signing_key="wrong-signing-key-with-more-than-32-bytes",
        repository_dir=tmp_path / "empty-repository",
    )
    assert wrong_key.get(first.contract_id)[1] == "mcp_remote_contract_not_found"

    store.save_catalog_contract(
        "tenant-a", "owner-a", second, catalog_contract_signature(second, key)
    )
    registry = CatalogRemoteContractRegistry(
        store=store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        signing_key=key,
        repository_dir=tmp_path / "empty-repository",
    )
    assert registry.get(first.contract_id)[1] == "hub_contract_collision"
    assert catalog_contract_export(first).endswith(b"\n")

    isolated = MCPRemoteReviewStore(tmp_path / "isolated")
    isolated.save_catalog_contract(
        "tenant-a", "owner-a", first, catalog_contract_signature(first, key)
    )
    isolated.revoke_contract(
        "tenant-a", "owner-a", "catalog_project", first.contract_id, "operator"
    )
    revoked = CatalogRemoteContractRegistry(
        store=isolated,
        tenant_id="tenant-a",
        owner_id="owner-a",
        signing_key=key,
        repository_dir=tmp_path / "empty-repository",
    )
    assert revoked.get(first.contract_id)[1] == "mcp_remote_contract_revoked"


@pytest.mark.asyncio
async def test_binding_revision_change_invalidates_target_and_closes_live_session(
    tmp_path: Path,
) -> None:
    service, bridge, broker, _catalog = make_service(tmp_path)
    service.store.set_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
        "reviewed",
        contract_fingerprint="a" * 64,
    )
    service._catalog_live["catalog-remote-static"] = (
        "hubsession_" + "c" * 32,
        "d" * 64,
    )

    broker.notify("catalog-remote-static")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    state = service.store.get_target_state(
        "tenant-a",
        "owner-a",
        RemoteTargetRefV1(
            target_type="catalog_project", target_id="catalog-remote-static"
        ),
    )
    assert state.state == "drifted"
    assert state.reason_code == "mcp_remote_auth_binding_revision_changed"
    assert bridge.closed == ["hubsession_" + "c" * 32]
    assert bridge.revoked == ["d" * 64]


@pytest.mark.asyncio
async def test_threaded_binding_change_uses_service_loop_to_close_live_session(
    tmp_path: Path,
) -> None:
    service, bridge, broker, _catalog = make_service(tmp_path)
    await service.start()
    service._catalog_live["catalog-remote-static"] = (
        "hubsession_" + "e" * 32,
        "f" * 64,
    )

    await asyncio.to_thread(broker.notify, "catalog-remote-static")
    for _ in range(10):
        if bridge.closed and bridge.revoked:
            break
        await asyncio.sleep(0)

    assert bridge.closed == ["hubsession_" + "e" * 32]
    assert bridge.revoked == ["f" * 64]
    await service.close()


@pytest.mark.asyncio
async def test_binding_revision_change_invalidates_pending_review_proposal(
    tmp_path: Path,
) -> None:
    service, _bridge, broker, _catalog = make_service(tmp_path)
    run = service.create_run(
        [
            RemoteTargetRefV1(
                target_type="catalog_project", target_id="catalog-remote-static"
            )
        ]
    )
    await service._tasks[run["run_id"]]
    item = service.get_run(run["run_id"])["items"][0]
    assert item["state"] == "awaiting_call_approval"

    broker.notify("catalog-remote-static")
    await asyncio.sleep(0)

    current = service.get_run(run["run_id"])["items"][0]
    assert current["state"] == "drifted"
    assert current["proposal"]["state"] == "drifted"
    assert current["error_code"] == "mcp_remote_auth_binding_revision_changed"
