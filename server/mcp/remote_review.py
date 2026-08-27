"""Unified, fail-closed review and Runtime surface for remote MCP targets.

Hub V1-V3 contracts remain byte-compatible. Catalog remote tools become
executable only from an explicit activation snapshot of a reviewed contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal, Protocol, TypeAlias
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import CatalogAdapterManifest, MCPCatalogService
from .hub import (
    HubBridgeProtocol,
    HubError,
    MCPHubService,
    arguments_digest,
    normalize_hub_remote_url,
)
from .hub_contracts import (
    HubReviewedContractV1,
    HubReviewedContractV2,
    HubReviewedContractV3,
    canonical_digest,
    canonical_json_bytes,
)
from .hub_review import (
    MAX_REVIEW_ITEMS,
    assess_oauth_scopes,
    classify_tool_effect,
    deterministic_arguments,
    deterministic_proposal_sort_key,
)
from .remote_auth import MCPRemoteAuthBroker, RemoteAuthError, RemoteAuthPolicyV1
from .remote_oauth import (
    MCP_PROTOCOL_VERSION,
    MCPRemoteOAuthService,
    RemoteOAuthError,
    RemoteOAuthPolicyV2,
)
from .remote_oauth_authorization import MCPRemoteOAuthAuthorizationService


REMOTE_REVIEW_SOP_VERSION = "remote_https_tools_2025_v1"
CATALOG_CONTRACT_SCHEMA_VERSION = "catalog-reviewed-remote-contract-v1"
CATALOG_EVIDENCE_SCHEMA_VERSION = "catalog-remote-evidence-v1"
RESOLVED_CONTRACT_SCHEMA_VERSION = "resolved-remote-contract-v1"
TARGET_REF_SCHEMA_VERSION = "remote-target-ref-v1"
TARGET_STATE_SCHEMA_VERSION = "remote-target-state-v1"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
PROJECT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")
REMOTE_RUN_ID_RE = re.compile(r"^remreview_[0-9a-f]{32}$")
REMOTE_ITEM_ID_RE = re.compile(r"^remitem_[0-9a-f]{32}$")
REMOTE_PROPOSAL_ID_RE = re.compile(r"^remproposal_[0-9a-f]{32}$")
CATALOG_CONTRACT_ID_RE = re.compile(r"^catalogct_[0-9a-f]{32}$")
HUB_RUN_ID_RE = re.compile(r"^hubreview_[0-9a-f]{32}$")
HUB_ITEM_ID_RE = re.compile(r"^hubitem_[0-9a-f]{32}$")
HUB_PROPOSAL_ID_RE = re.compile(r"^hubproposal_[0-9a-f]{32}$")
HUB_CONTRACT_ID_RE = re.compile(r"^hubct_[0-9a-f]{32}$")
OAUTH_TOKEN_ID_RE = re.compile(r"^mcpoauthtoken_[0-9a-f]{32}$")
APPROVAL_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
REMOTE_TARGET_STATES = {
    "draft",
    "reviewing",
    "reviewed",
    "active",
    "drifted",
    "tainted",
    "disconnected",
    "revoked",
}
AUTH_FAILURE_CODES = {
    "hub_upstream_auth_required",
    "mcp_remote_auth_unauthorized",
    "mcp_remote_auth_forbidden",
    "mcp_remote_oauth_unauthorized",
    "mcp_remote_oauth_forbidden",
}


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def review_unification_enabled() -> bool:
    return _flag("MCP_REMOTE_REVIEW_UNIFICATION_ENABLED")


def catalog_oauth_enabled() -> bool:
    return _flag("MCP_REMOTE_CATALOG_OAUTH_ENABLED")


def contract_runtime_enabled() -> bool:
    return _flag("MCP_REMOTE_CONTRACT_RUNTIME_ENABLED")


def catalog_runtime_enabled() -> bool:
    return _flag("MCP_REMOTE_CATALOG_RUNTIME_ENABLED")


def _manifest_source_payload(manifest: CatalogAdapterManifest) -> dict[str, Any]:
    return {
        "schema_version": "catalog-remote-manifest-execution-v1",
        "project_id": manifest.project_id,
        "adapter_version": manifest.adapter_version,
        "availability": manifest.availability,
        "connection_kind": manifest.connection_kind,
        "transport": manifest.transport,
        "endpoint": manifest.endpoint,
        "network_policy": manifest.network_policy,
        "remote_auth_mode": manifest.remote_auth_mode,
        "remote_auth_header_name": manifest.remote_auth_header_name,
        "remote_oauth_registration_mode": manifest.remote_oauth_registration_mode,
        "remote_oauth_client_id": manifest.remote_oauth_client_id,
        "allowed_inert_server_capabilities": list(
            manifest.allowed_inert_server_capabilities
        ),
        "tool_policies": {
            name: {
                "read_only": policy.read_only,
                "requires_approval": policy.requires_approval,
                "sensitive": policy.sensitive,
                "terminal": policy.terminal,
                "effect": policy.effect,
            }
            for name, policy in sorted(manifest.tool_policies.items())
        },
        "limits": {
            "operation_timeout_ms": int(manifest.operation_timeout * 1000),
            "max_output_bytes": manifest.max_output_bytes,
        },
    }


def catalog_manifest_source_digest(manifest: CatalogAdapterManifest) -> str:
    return canonical_digest(_manifest_source_payload(manifest))


def _normalized_remote_identity(manifest: CatalogAdapterManifest) -> tuple[str, str]:
    try:
        endpoint, origin = normalize_hub_remote_url(manifest.endpoint)
    except (HubError, ValueError):
        raise HubError(
            "目录远程 MCP 必须冻结为静态公网 HTTPS 443 地址。",
            code="mcp_remote_catalog_manifest_ineligible",
            status_code=409,
        ) from None
    if manifest.transport != "streamable-http":
        raise HubError(
            "目录远程 MCP 必须冻结 Streamable HTTP 传输。",
            code="mcp_remote_catalog_manifest_ineligible",
            status_code=409,
        )
    return endpoint, origin


class RemoteTargetRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[TARGET_REF_SCHEMA_VERSION] = TARGET_REF_SCHEMA_VERSION
    target_type: Literal["hub_candidate", "catalog_project"]
    target_id: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_identity(self) -> "RemoteTargetRefV1":
        if self.target_type == "catalog_project" and not PROJECT_ID_RE.fullmatch(
            self.target_id
        ):
            raise ValueError("invalid catalog project identity")
        return self


RemoteTargetStateName: TypeAlias = Literal[
    "draft",
    "reviewing",
    "reviewed",
    "active",
    "drifted",
    "tainted",
    "disconnected",
    "revoked",
]


class RemoteTargetStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[TARGET_STATE_SCHEMA_VERSION] = TARGET_STATE_SCHEMA_VERSION
    target: RemoteTargetRefV1
    state: RemoteTargetStateName = "draft"
    contract_fingerprint: str = ""
    reason_code: str = ""
    revision: int = 1
    updated_at: float


class RemoteRuntimeBindingV1(BaseModel):
    """Immutable execution snapshot created by explicit target activation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["remote-runtime-binding-v1"] = "remote-runtime-binding-v1"
    target: RemoteTargetRefV1
    contract_id: str
    contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    auth_context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schemas: dict[str, dict[str, Any]]
    revision: int = Field(ge=1)
    updated_at: float

    @model_validator(mode="after")
    def validate_tools(self) -> "RemoteRuntimeBindingV1":
        if not self.tool_schemas:
            raise ValueError("runtime binding requires frozen tools")
        for name, item in self.tool_schemas.items():
            if not name or not isinstance(item, dict):
                raise ValueError("runtime tool binding is invalid")
            digest = str(item.get("schema_digest") or "")
            schema = item.get("input_schema")
            if not HEX64_RE.fullmatch(digest) or not isinstance(schema, dict):
                raise ValueError("runtime tool schema is invalid")
            if canonical_digest(schema) != digest:
                raise ValueError("runtime tool schema digest mismatch")
        return self


class CatalogRemoteSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["catalog-remote-snapshot-v1"] = "catalog-remote-snapshot-v1"
    project_id: str
    adapter_version: str
    remote_url: str
    origin: str
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport: Literal["streamable-http"] = "streamable-http"
    auth_mode: Literal[
        "static_bearer", "static_header", "oauth_authorization_code_pkce"
    ]

    @property
    def snapshot_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class CatalogRemoteEvidenceBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[CATALOG_EVIDENCE_SCHEMA_VERSION] = (
        CATALOG_EVIDENCE_SCHEMA_VERSION
    )
    sop_version: Literal[REMOTE_REVIEW_SOP_VERSION] = REMOTE_REVIEW_SOP_VERSION
    snapshot: CatalogRemoteSnapshotV1
    stages: dict[str, dict[str, Any]]
    unauthenticated_schema_digest: str = ""
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_digests: dict[str, str]
    effect_proposals: dict[str, str]
    auth_policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    auth_revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_scopes: tuple[str, ...] = ()
    authorized_scope_digest: str = ""
    representative_call: dict[str, Any] = Field(default_factory=dict)
    cleanup: dict[str, Any] = Field(default_factory=dict)
    fixed_errors: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_evidence(self) -> "CatalogRemoteEvidenceBundleV1":
        if not self.tool_schema_digests:
            raise ValueError("authenticated tool schemas are required")
        if any(not HEX64_RE.fullmatch(value) for value in self.tool_schema_digests.values()):
            raise ValueError("tool schema digest must be sha256")
        if set(self.effect_proposals) != set(self.tool_schema_digests):
            raise ValueError("effect proposals must cover frozen tools")
        if self.snapshot.auth_mode == "oauth_authorization_code_pkce":
            if not HEX64_RE.fullmatch(self.authorized_scope_digest):
                raise ValueError("OAuth scope digest is required")
            if canonical_digest(list(self.authorized_scopes)) != self.authorized_scope_digest:
                raise ValueError("OAuth scope digest mismatch")
        elif self.authorized_scopes or self.authorized_scope_digest:
            raise ValueError("static auth evidence cannot carry OAuth scopes")
        return self

    @property
    def evidence_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


def stable_catalog_contract_id(project_id: str, version: str, remote_url: str) -> str:
    return "catalogct_" + canonical_digest(
        {
            "project_id": project_id.strip(),
            "version": version.strip(),
            "remote_url": remote_url.strip(),
        }
    )[:32]


class CatalogReviewedRemoteContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[CATALOG_CONTRACT_SCHEMA_VERSION] = (
        CATALOG_CONTRACT_SCHEMA_VERSION
    )
    sop_version: Literal[REMOTE_REVIEW_SOP_VERSION] = REMOTE_REVIEW_SOP_VERSION
    contract_id: str = Field(pattern=r"^catalogct_[0-9a-f]{32}$")
    project_id: str
    version: str
    remote_url: str
    origin: str
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_version: Literal[MCP_PROTOCOL_VERSION] = MCP_PROTOCOL_VERSION
    transport: Literal["streamable-http"] = "streamable-http"
    auth_mode: Literal[
        "static_bearer", "static_header", "oauth_authorization_code_pkce"
    ]
    remote_auth_policy: RemoteAuthPolicyV1 | None = None
    remote_oauth_policy: RemoteOAuthPolicyV2 | None = None
    authorized_scopes: tuple[str, ...] = ()
    authorized_scope_digest: str = ""
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_digests: dict[str, str]
    allowed_tools: list[str]
    tool_effects: dict[str, Literal["read"]]
    limits: dict[str, int]
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_fingerprint: str = ""
    published_at: float = 0.0
    display_note: str = ""

    @model_validator(mode="after")
    def validate_contract(self) -> "CatalogReviewedRemoteContractV1":
        expected_id = stable_catalog_contract_id(
            self.project_id, self.version, self.remote_url
        )
        if self.contract_id != expected_id:
            raise ValueError("contract identity mismatch")
        static = self.auth_mode in {"static_bearer", "static_header"}
        if static != (self.remote_auth_policy is not None):
            raise ValueError("static auth policy mismatch")
        if (not static) != (self.remote_oauth_policy is not None):
            raise ValueError("OAuth policy mismatch")
        if not set(self.allowed_tools).issubset(self.tool_schema_digests):
            raise ValueError("allowed tools must be frozen")
        if not self.allowed_tools or set(self.tool_effects) != set(self.allowed_tools):
            raise ValueError("read effects must cover a non-empty allowed subset")
        if any(effect != "read" for effect in self.tool_effects.values()):
            raise ValueError("R4A only publishes read tools")
        if not static:
            if canonical_digest(list(self.authorized_scopes)) != self.authorized_scope_digest:
                raise ValueError("OAuth contract scope digest mismatch")
        fingerprint = catalog_contract_fingerprint(self)
        if self.contract_fingerprint and self.contract_fingerprint != fingerprint:
            raise ValueError("contract fingerprint mismatch")
        object.__setattr__(self, "contract_fingerprint", fingerprint)
        return self

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.project_id, self.version, self.remote_url


def catalog_contract_execution_fields(
    contract: CatalogReviewedRemoteContractV1 | dict[str, Any],
) -> dict[str, Any]:
    payload = (
        contract.model_dump(mode="json")
        if isinstance(contract, CatalogReviewedRemoteContractV1)
        else dict(contract)
    )
    for key in ("contract_fingerprint", "evidence_digest", "published_at", "display_note"):
        payload.pop(key, None)
    return payload


def catalog_contract_fingerprint(
    contract: CatalogReviewedRemoteContractV1 | dict[str, Any],
) -> str:
    return canonical_digest(catalog_contract_execution_fields(contract))


def catalog_contract_export(contract: CatalogReviewedRemoteContractV1) -> bytes:
    return canonical_json_bytes(contract.model_dump(mode="json")) + b"\n"


def catalog_contract_signature(
    contract: CatalogReviewedRemoteContractV1, signing_key: str
) -> str:
    return hmac.new(
        signing_key.encode("utf-8"),
        catalog_contract_export(contract).rstrip(b"\n"),
        hashlib.sha256,
    ).hexdigest()


class ResolvedRemoteContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[RESOLVED_CONTRACT_SCHEMA_VERSION] = (
        RESOLVED_CONTRACT_SCHEMA_VERSION
    )
    target: RemoteTargetRefV1
    contract_id: str
    contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: str
    remote_url: str
    origin: str
    protocol_version: Literal[MCP_PROTOCOL_VERSION] = MCP_PROTOCOL_VERSION
    transport: Literal["streamable-http"] = "streamable-http"
    auth_mode: Literal[
        "anonymous",
        "static_bearer",
        "static_header",
        "oauth_authorization_code_pkce",
    ]
    auth_policy_fingerprint: str = ""
    authorized_scopes: tuple[str, ...] = ()
    authorized_scope_digest: str = ""
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_digests: dict[str, str]
    allowed_tools: tuple[str, ...]
    tool_effects: dict[str, Literal["read"]]
    limits: dict[str, int]

    @classmethod
    def from_hub(
        cls, candidate_id: str, contract: HubReviewedContractV1
    ) -> "ResolvedRemoteContractV1":
        auth_mode = "anonymous"
        auth_fingerprint = ""
        scopes: tuple[str, ...] = ()
        scope_digest = ""
        if isinstance(contract, HubReviewedContractV2):
            auth_mode = contract.remote_auth_policy.mode
            auth_fingerprint = contract.remote_auth_policy.policy_fingerprint
        elif isinstance(contract, HubReviewedContractV3):
            auth_mode = "oauth_authorization_code_pkce"
            auth_fingerprint = contract.remote_oauth_policy.policy_fingerprint
            scopes = contract.authorized_scopes
            scope_digest = contract.authorized_scope_digest
        return cls(
            target=RemoteTargetRefV1(
                target_type="hub_candidate", target_id=candidate_id
            ),
            contract_id=contract.contract_id,
            contract_fingerprint=contract.contract_fingerprint,
            source_digest=contract.source_digest,
            version=contract.version,
            remote_url=contract.remote_url,
            origin=contract.origin,
            auth_mode=auth_mode,
            auth_policy_fingerprint=auth_fingerprint,
            authorized_scopes=scopes,
            authorized_scope_digest=scope_digest,
            schema_digest=contract.schema_digest,
            tool_schema_digests=contract.tool_schema_digests,
            allowed_tools=tuple(contract.allowed_tools),
            tool_effects=contract.tool_effects,
            limits=contract.limits,
        )

    @classmethod
    def from_catalog(
        cls, contract: CatalogReviewedRemoteContractV1
    ) -> "ResolvedRemoteContractV1":
        policy = contract.remote_auth_policy or contract.remote_oauth_policy
        assert policy is not None
        return cls(
            target=RemoteTargetRefV1(
                target_type="catalog_project", target_id=contract.project_id
            ),
            contract_id=contract.contract_id,
            contract_fingerprint=contract.contract_fingerprint,
            source_digest=contract.source_digest,
            version=contract.version,
            remote_url=contract.remote_url,
            origin=contract.origin,
            auth_mode=contract.auth_mode,
            auth_policy_fingerprint=policy.policy_fingerprint,
            authorized_scopes=contract.authorized_scopes,
            authorized_scope_digest=contract.authorized_scope_digest,
            schema_digest=contract.schema_digest,
            tool_schema_digests=contract.tool_schema_digests,
            allowed_tools=tuple(contract.allowed_tools),
            tool_effects=contract.tool_effects,
            limits=contract.limits,
        )


class RemoteReviewTargetAdapter(Protocol):
    target_type: Literal["hub_candidate", "catalog_project"]

    def resolve(self, target_id: str) -> dict[str, Any]: ...


class HubCandidateReviewAdapter:
    target_type: Literal["hub_candidate"] = "hub_candidate"

    def __init__(self, hub: MCPHubService) -> None:
        self.hub = hub

    def resolve(self, target_id: str) -> dict[str, Any]:
        candidate = self.hub.get_candidate(target_id)
        return {
            "target": RemoteTargetRefV1(
                target_type="hub_candidate", target_id=candidate["candidate_id"]
            ),
            "server_name": candidate["server_name"],
            "version": candidate["version"],
            "remote_id": candidate["remote_id"],
        }


class CatalogProjectReviewAdapter:
    target_type: Literal["catalog_project"] = "catalog_project"

    def __init__(self, catalog: MCPCatalogService) -> None:
        self.catalog = catalog

    def resolve(self, target_id: str) -> dict[str, Any]:
        manifest = self.catalog.get_manifest(target_id)
        if (
            manifest.connection_kind != "remote-mcp"
            or manifest.availability in {"blocked", "superseded"}
            or manifest.remote_auth_mode
            not in {
                "static_bearer",
                "static_header",
                "oauth_authorization_code_pkce",
            }
        ):
            raise HubError(
                "目录项目不满足受控远程复核门禁。",
                code="mcp_remote_catalog_manifest_ineligible",
                status_code=409,
            )
        endpoint, origin = _normalized_remote_identity(manifest)
        if not manifest.adapter_version:
            raise HubError(
                "目录项目缺少冻结版本。",
                code="mcp_remote_catalog_manifest_ineligible",
                status_code=409,
            )
        if manifest.remote_auth_mode in {"static_bearer", "static_header"}:
            policy = self.catalog._catalog_remote_auth_policy(manifest)
            if policy is None or policy.origin != origin:
                raise HubError(
                    "目录静态认证策略与远程 Origin 不一致。",
                    code="mcp_remote_catalog_manifest_ineligible",
                    status_code=409,
                )
        elif not manifest.remote_oauth_registration_mode:
            raise HubError(
                "目录 OAuth 项目缺少冻结客户端登记策略。",
                code="mcp_remote_catalog_manifest_ineligible",
                status_code=409,
            )
        source_digest = catalog_manifest_source_digest(manifest)
        snapshot = CatalogRemoteSnapshotV1(
            project_id=manifest.project_id,
            adapter_version=manifest.adapter_version,
            remote_url=endpoint,
            origin=origin,
            source_digest=source_digest,
            auth_mode=manifest.remote_auth_mode,
        )
        return {
            "target": RemoteTargetRefV1(
                target_type="catalog_project", target_id=manifest.project_id
            ),
            "manifest": manifest,
            "snapshot": snapshot,
        }


class MCPRemoteReviewStore:
    """Additive target/review/contract tables under the existing MCP storage."""

    def __init__(self, storage: str | Path) -> None:
        root = Path(storage)
        self.path = root if root.suffix == ".sqlite3" else root / "remote-review.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_target_states (
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL DEFAULT '',
                    reason_code TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(tenant_id,owner_id,target_type,target_id)
                );
                CREATE TABLE IF NOT EXISTS remote_review_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_remote_review_runs_owner
                    ON remote_review_runs(tenant_id,owner_id,updated_at DESC);
                CREATE TABLE IF NOT EXISTS remote_review_items (
                    item_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES remote_review_runs(run_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_stage TEXT NOT NULL DEFAULT '',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    evidence_digest TEXT NOT NULL DEFAULT '',
                    draft_contract_json TEXT NOT NULL DEFAULT '{}',
                    contract_fingerprint TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(run_id,target_type,target_id)
                );
                CREATE TABLE IF NOT EXISTS remote_review_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL REFERENCES remote_review_items(item_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    safe_to_retry INTEGER NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    UNIQUE(item_id,sequence)
                );
                CREATE TABLE IF NOT EXISTS remote_review_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL REFERENCES remote_review_items(item_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    schema_digest TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(item_id)
                );
                CREATE TABLE IF NOT EXISTS remote_review_call_ledger (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_digest TEXT NOT NULL DEFAULT '',
                    result_size INTEGER NOT NULL DEFAULT 0,
                    result_type TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalog_remote_contract_revisions (
                    revision_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_catalog_remote_contract_identity
                    ON catalog_remote_contract_revisions(tenant_id,owner_id,contract_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS remote_contract_revocations (
                    revocation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_remote_contract_revocations
                    ON remote_contract_revocations(tenant_id,owner_id,target_type,contract_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS remote_runtime_bindings (
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    schema_digest TEXT NOT NULL,
                    auth_context_digest TEXT NOT NULL,
                    tool_schemas_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(tenant_id,owner_id,target_type,target_id)
                );
                CREATE INDEX IF NOT EXISTS idx_remote_runtime_bindings_owner
                    ON remote_runtime_bindings(tenant_id,owner_id,updated_at DESC);
                CREATE TABLE IF NOT EXISTS remote_runtime_execution_ledger (
                    approval_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_remote_runtime_ledger_target
                    ON remote_runtime_execution_ledger(
                        tenant_id,owner_id,target_type,target_id,updated_at DESC
                    );
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for column, output in (
            ("snapshot_json", "snapshot"),
            ("evidence_json", "evidence"),
            ("draft_contract_json", "draft_contract"),
            ("payload_json", "payload"),
            ("arguments_json", "arguments"),
        ):
            if column in item:
                raw = str(item.pop(column) or "{}")
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    decoded = {}
                item[output] = decoded if isinstance(decoded, dict) else {}
        return item

    def get_target_state(
        self, tenant_id: str, owner_id: str, target: RemoteTargetRefV1
    ) -> RemoteTargetStateV1:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_target_states WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=?",
                (tenant_id, owner_id, target.target_type, target.target_id),
            ).fetchone()
        if row is None:
            return RemoteTargetStateV1(
                target=target, state="draft", updated_at=0.0
            )
        return RemoteTargetStateV1(
            target=target,
            state=str(row["state"]),
            contract_fingerprint=str(row["contract_fingerprint"]),
            reason_code=str(row["reason_code"]),
            revision=int(row["revision"]),
            updated_at=float(row["updated_at"]),
        )

    def set_target_state(
        self,
        tenant_id: str,
        owner_id: str,
        target: RemoteTargetRefV1,
        state: RemoteTargetStateName,
        *,
        contract_fingerprint: str = "",
        reason_code: str = "",
    ) -> RemoteTargetStateV1:
        if state not in REMOTE_TARGET_STATES:
            raise ValueError("invalid remote target state")
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM remote_target_states WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=?",
                (tenant_id, owner_id, target.target_type, target.target_id),
            ).fetchone()
            revision = (int(row["revision"]) + 1) if row is not None else 1
            db.execute(
                "INSERT INTO remote_target_states(tenant_id,owner_id,target_type,target_id,state,"
                "contract_fingerprint,reason_code,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id,owner_id,target_type,target_id) DO UPDATE SET "
                "state=excluded.state,contract_fingerprint=excluded.contract_fingerprint,"
                "reason_code=excluded.reason_code,revision=excluded.revision,updated_at=excluded.updated_at",
                (
                    tenant_id,
                    owner_id,
                    target.target_type,
                    target.target_id,
                    state,
                    contract_fingerprint,
                    reason_code,
                    revision,
                    now,
                ),
            )
        return self.get_target_state(tenant_id, owner_id, target)

    def save_runtime_binding(
        self,
        tenant_id: str,
        owner_id: str,
        *,
        target: RemoteTargetRefV1,
        contract_id: str,
        contract_fingerprint: str,
        source_digest: str,
        schema_digest: str,
        auth_context_digest: str,
        tool_schemas: dict[str, dict[str, Any]],
    ) -> RemoteRuntimeBindingV1:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM remote_runtime_bindings WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=?",
                (tenant_id, owner_id, target.target_type, target.target_id),
            ).fetchone()
            revision = int(row["revision"]) + 1 if row is not None else 1
            db.execute(
                "INSERT INTO remote_runtime_bindings(tenant_id,owner_id,target_type,target_id,"
                "contract_id,contract_fingerprint,source_digest,schema_digest,auth_context_digest,"
                "tool_schemas_json,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id,owner_id,target_type,target_id) DO UPDATE SET "
                "contract_id=excluded.contract_id,contract_fingerprint=excluded.contract_fingerprint,"
                "source_digest=excluded.source_digest,schema_digest=excluded.schema_digest,"
                "auth_context_digest=excluded.auth_context_digest,tool_schemas_json=excluded.tool_schemas_json,"
                "revision=excluded.revision,updated_at=excluded.updated_at",
                (
                    tenant_id,
                    owner_id,
                    target.target_type,
                    target.target_id,
                    contract_id,
                    contract_fingerprint,
                    source_digest,
                    schema_digest,
                    auth_context_digest,
                    json.dumps(tool_schemas, ensure_ascii=False, separators=(",", ":")),
                    revision,
                    now,
                ),
            )
        binding = self.get_runtime_binding(tenant_id, owner_id, target)
        assert binding is not None
        return binding

    def get_runtime_binding(
        self, tenant_id: str, owner_id: str, target: RemoteTargetRefV1
    ) -> RemoteRuntimeBindingV1 | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_runtime_bindings WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=?",
                (tenant_id, owner_id, target.target_type, target.target_id),
            ).fetchone()
        if row is None:
            return None
        try:
            tool_schemas = json.loads(str(row["tool_schemas_json"] or "{}"))
        except json.JSONDecodeError:
            return None
        if not isinstance(tool_schemas, dict):
            return None
        try:
            return RemoteRuntimeBindingV1(
                target=target,
                contract_id=str(row["contract_id"]),
                contract_fingerprint=str(row["contract_fingerprint"]),
                source_digest=str(row["source_digest"]),
                schema_digest=str(row["schema_digest"]),
                auth_context_digest=str(row["auth_context_digest"]),
                tool_schemas=tool_schemas,
                revision=int(row["revision"]),
                updated_at=float(row["updated_at"]),
            )
        except ValueError:
            return None

    def list_runtime_bindings(
        self, tenant_id: str, owner_id: str
    ) -> list[RemoteRuntimeBindingV1]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT target_type,target_id FROM remote_runtime_bindings "
                "WHERE tenant_id=? AND owner_id=? ORDER BY updated_at DESC",
                (tenant_id, owner_id),
            ).fetchall()
        result: list[RemoteRuntimeBindingV1] = []
        for row in rows:
            target = RemoteTargetRefV1(
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
            )
            binding = self.get_runtime_binding(tenant_id, owner_id, target)
            if binding is not None:
                result.append(binding)
        return result

    def delete_runtime_binding(
        self, tenant_id: str, owner_id: str, target: RemoteTargetRefV1
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM remote_runtime_bindings WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=?",
                (tenant_id, owner_id, target.target_type, target.target_id),
            )

    def runtime_execution(
        self,
        approval_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        target: RemoteTargetRefV1,
        contract_fingerprint: str,
        tool_name: str,
        args_digest: str,
    ) -> tuple[str, dict[str, Any] | None] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_runtime_execution_ledger WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        if (
            str(row["tenant_id"]) != tenant_id
            or str(row["owner_id"]) != owner_id
            or str(row["target_type"]) != target.target_type
            or str(row["target_id"]) != target.target_id
            or str(row["contract_fingerprint"]) != contract_fingerprint
            or str(row["tool_name"]) != tool_name
            or str(row["arguments_digest"]) != args_digest
        ):
            raise HubError(
                "远程 Runtime 审批回放范围不匹配。",
                code="mcp_remote_runtime_approval_scope_mismatch",
                status_code=409,
            )
        result: dict[str, Any] | None = None
        if row["result_json"]:
            try:
                decoded = json.loads(str(row["result_json"]))
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                result = decoded
        return str(row["state"]), result

    def begin_runtime_execution(
        self,
        approval_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        target: RemoteTargetRefV1,
        contract_fingerprint: str,
        tool_name: str,
        args_digest: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT approval_id FROM remote_runtime_execution_ledger WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if row is not None:
                raise HubError(
                    "远程 Runtime 审批不可重复派发。",
                    code="mcp_remote_runtime_approval_replay",
                    status_code=409,
                )
            db.execute(
                "INSERT INTO remote_runtime_execution_ledger(approval_id,tenant_id,owner_id,"
                "target_type,target_id,contract_fingerprint,tool_name,arguments_digest,state,"
                "result_json,error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    tenant_id,
                    owner_id,
                    target.target_type,
                    target.target_id,
                    contract_fingerprint,
                    tool_name,
                    args_digest,
                    "started",
                    None,
                    "",
                    now,
                    now,
                ),
            )

    def finish_runtime_execution(
        self,
        approval_id: str,
        *,
        state: Literal["completed", "failed", "unknown_outcome"],
        result: dict[str, Any] | None = None,
        error_code: str = "",
    ) -> None:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE remote_runtime_execution_ledger SET state=?,result_json=?,"
                "error_code=?,updated_at=? WHERE approval_id=?",
                (
                    state,
                    (
                        json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                        if result is not None
                        else None
                    ),
                    error_code,
                    time.time(),
                    approval_id,
                ),
            )
            if cursor.rowcount != 1:
                raise HubError(
                    "远程 Runtime 执行账本不存在。",
                    code="mcp_remote_runtime_ledger_missing",
                    status_code=409,
                )

    def recover_started_runtime_calls(
        self, tenant_id: str, owner_id: str
    ) -> list[RemoteTargetRefV1]:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT DISTINCT target_type,target_id FROM remote_runtime_execution_ledger "
                "WHERE tenant_id=? AND owner_id=? AND state='started'",
                (tenant_id, owner_id),
            ).fetchall()
            db.execute(
                "UPDATE remote_runtime_execution_ledger SET state='unknown_outcome',"
                "error_code='unknown_outcome',updated_at=? WHERE tenant_id=? AND owner_id=? "
                "AND state='started'",
                (now, tenant_id, owner_id),
            )
        return [
            RemoteTargetRefV1(
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
            )
            for row in rows
        ]

    def create_run(
        self, tenant_id: str, owner_id: str, targets: list[RemoteTargetRefV1]
    ) -> dict[str, Any]:
        if not 1 <= len(targets) <= MAX_REVIEW_ITEMS:
            raise HubError(
                "统一复核批次必须包含 1–20 个目标。",
                code="mcp_remote_review_batch_size",
                status_code=422,
            )
        if len({(item.target_type, item.target_id) for item in targets}) != len(targets):
            raise HubError(
                "统一复核批次包含重复目标。",
                code="mcp_remote_review_duplicate_target",
                status_code=422,
            )
        now = time.time()
        run_id = "remreview_" + uuid.uuid4().hex
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT run_id FROM remote_review_runs WHERE tenant_id=? AND owner_id=? "
                "AND status IN ('queued','running','awaiting_operator') LIMIT 1",
                (tenant_id, owner_id),
            ).fetchone()
            if active is not None:
                raise HubError(
                    "当前本地运维者已有统一复核批次。",
                    code="mcp_remote_review_owner_busy",
                    status_code=409,
                )
            db.execute(
                "INSERT INTO remote_review_runs VALUES(?,?,?,?,0,'',?,?)",
                (run_id, tenant_id, owner_id, "queued", now, now),
            )
            for target in targets:
                db.execute(
                    "INSERT INTO remote_review_items(item_id,run_id,tenant_id,owner_id,target_type,"
                    "target_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)",
                    (
                        "remitem_" + uuid.uuid4().hex,
                        run_id,
                        tenant_id,
                        owner_id,
                        target.target_type,
                        target.target_id,
                        now,
                        now,
                    ),
                )
        return self.require_run(run_id, tenant_id, owner_id)

    def require_run(self, run_id: str, tenant_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_review_runs WHERE run_id=? AND tenant_id=? AND owner_id=?",
                (run_id, tenant_id, owner_id),
            ).fetchone()
            items = db.execute(
                "SELECT * FROM remote_review_items WHERE run_id=? AND tenant_id=? AND owner_id=? "
                "ORDER BY created_at,item_id",
                (run_id, tenant_id, owner_id),
            ).fetchall()
            events = db.execute(
                "SELECT * FROM remote_review_events WHERE run_id=? ORDER BY created_at,sequence",
                (run_id,),
            ).fetchall()
            proposals = db.execute(
                "SELECT * FROM remote_review_proposals WHERE run_id=? AND tenant_id=? AND owner_id=?",
                (run_id, tenant_id, owner_id),
            ).fetchall()
        if row is None:
            raise HubError(
                "统一复核批次不存在。",
                code="mcp_remote_review_run_not_found",
                status_code=404,
            )
        by_item_events: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            decoded = self._decode(event)
            by_item_events.setdefault(str(event["item_id"]), []).append(decoded)
        by_item_proposal = {
            str(item["item_id"]): self._decode(item) for item in proposals
        }
        decoded_items = []
        for item in items:
            decoded = self._decode(item)
            decoded["target"] = {
                "schema_version": TARGET_REF_SCHEMA_VERSION,
                "target_type": decoded["target_type"],
                "target_id": decoded["target_id"],
            }
            decoded["events"] = by_item_events.get(str(item["item_id"]), [])
            decoded["proposal"] = by_item_proposal.get(str(item["item_id"]))
            decoded_items.append(decoded)
        output = dict(row)
        output["cancel_requested"] = bool(output["cancel_requested"])
        output["items"] = decoded_items
        return output

    def list_runs(self, tenant_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT run_id FROM remote_review_runs WHERE tenant_id=? AND owner_id=? "
                "ORDER BY updated_at DESC",
                (tenant_id, owner_id),
            ).fetchall()
        return [self.require_run(str(row["run_id"]), tenant_id, owner_id) for row in rows]

    def require_item(
        self, run_id: str, item_id: str, tenant_id: str, owner_id: str
    ) -> dict[str, Any]:
        run = self.require_run(run_id, tenant_id, owner_id)
        item = next((entry for entry in run["items"] if entry["item_id"] == item_id), None)
        if item is None:
            raise HubError(
                "统一复核项不存在。",
                code="mcp_remote_review_item_not_found",
                status_code=404,
            )
        return item

    def set_run(self, run_id: str, **fields: Any) -> None:
        allowed = {"status", "cancel_requested", "error_code"}
        clean = {key: value for key, value in fields.items() if key in allowed}
        if not clean:
            return
        clean["updated_at"] = time.time()
        assignments = ",".join(f"{key}=?" for key in clean)
        with self._lock, self._connect() as db:
            db.execute(
                f"UPDATE remote_review_runs SET {assignments} WHERE run_id=?",
                (*clean.values(), run_id),
            )

    def set_item(self, item_id: str, **fields: Any) -> None:
        json_fields = {
            "snapshot": "snapshot_json",
            "evidence": "evidence_json",
            "draft_contract": "draft_contract_json",
        }
        allowed = {
            "state",
            "current_stage",
            "evidence_digest",
            "contract_fingerprint",
            "error_code",
        }
        clean: dict[str, Any] = {}
        for key, value in fields.items():
            if key in json_fields:
                clean[json_fields[key]] = json.dumps(
                    value, ensure_ascii=False, separators=(",", ":")
                )
            elif key in allowed:
                clean[key] = value
        if not clean:
            return
        clean["updated_at"] = time.time()
        assignments = ",".join(f"{key}=?" for key in clean)
        with self._lock, self._connect() as db:
            db.execute(
                f"UPDATE remote_review_items SET {assignments} WHERE item_id=?",
                (*clean.values(), item_id),
            )

    def add_event(
        self,
        run_id: str,
        item_id: str,
        stage: str,
        *,
        status: str = "passed",
        safe_to_retry: bool,
        error_code: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS next FROM remote_review_events WHERE item_id=?",
                (item_id,),
            ).fetchone()
            sequence = int(row["next"])
            db.execute(
                "INSERT INTO remote_review_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "remevent_" + uuid.uuid4().hex,
                    run_id,
                    item_id,
                    sequence,
                    stage,
                    status,
                    int(safe_to_retry),
                    error_code,
                    json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                ),
            )
        self.set_item(item_id, current_stage=stage)

    def create_proposal(
        self,
        *,
        run_id: str,
        item_id: str,
        tenant_id: str,
        owner_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        schema_digest: str,
    ) -> dict[str, Any]:
        proposal_id = "remproposal_" + uuid.uuid4().hex
        args_digest = canonical_digest(arguments)
        proposal_digest = canonical_digest(
            {
                "schema_version": "remote-call-proposal-v1",
                "run_id": run_id,
                "item_id": item_id,
                "tool_name": tool_name,
                "arguments_digest": args_digest,
                "schema_digest": schema_digest,
            }
        )
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO remote_review_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id,
                    run_id,
                    item_id,
                    tenant_id,
                    owner_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                    args_digest,
                    schema_digest,
                    proposal_digest,
                    "proposed",
                    now,
                    now,
                ),
            )
        return self.require_proposal(proposal_id, tenant_id, owner_id)

    def require_proposal(
        self, proposal_id: str, tenant_id: str, owner_id: str
    ) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_review_proposals WHERE proposal_id=? AND tenant_id=? AND owner_id=?",
                (proposal_id, tenant_id, owner_id),
            ).fetchone()
        if row is None:
            raise HubError(
                "统一复核提案不存在。",
                code="mcp_remote_review_proposal_not_found",
                status_code=404,
            )
        return self._decode(row)

    def begin_call(
        self, proposal: dict[str, Any], target: RemoteTargetRefV1
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT state FROM remote_review_proposals WHERE proposal_id=?",
                (proposal["proposal_id"],),
            ).fetchone()
            if current is None or str(current["state"]) != "proposed":
                raise HubError(
                    "代表调用批准不可重放。",
                    code="mcp_remote_review_call_replay",
                    status_code=409,
                )
            db.execute(
                "UPDATE remote_review_proposals SET state='started',updated_at=? WHERE proposal_id=?",
                (now, proposal["proposal_id"]),
            )
            db.execute(
                "INSERT INTO remote_review_call_ledger(proposal_id,run_id,item_id,tenant_id,owner_id,"
                "target_type,target_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'started',?,?)",
                (
                    proposal["proposal_id"],
                    proposal["run_id"],
                    proposal["item_id"],
                    proposal["tenant_id"],
                    proposal["owner_id"],
                    target.target_type,
                    target.target_id,
                    now,
                    now,
                ),
            )

    def finish_call(
        self,
        proposal_id: str,
        *,
        state: str,
        result_digest: str = "",
        result_size: int = 0,
        result_type: str = "",
        error_code: str = "",
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE remote_review_call_ledger SET state=?,result_digest=?,result_size=?,"
                "result_type=?,error_code=?,updated_at=? WHERE proposal_id=?",
                (
                    state,
                    result_digest,
                    result_size,
                    result_type,
                    error_code,
                    now,
                    proposal_id,
                ),
            )
            db.execute(
                "UPDATE remote_review_proposals SET state=?,updated_at=? WHERE proposal_id=?",
                (state, now, proposal_id),
            )

    def recover_started_calls(self, tenant_id: str, owner_id: str) -> list[dict[str, str]]:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT target_type,target_id,item_id,proposal_id FROM remote_review_call_ledger "
                "WHERE tenant_id=? AND owner_id=? AND state='started'",
                (tenant_id, owner_id),
            ).fetchall()
            now = time.time()
            db.execute(
                "UPDATE remote_review_call_ledger SET state='unknown_outcome',"
                "error_code='unknown_outcome',updated_at=? WHERE tenant_id=? AND owner_id=? AND state='started'",
                (now, tenant_id, owner_id),
            )
            for row in rows:
                db.execute(
                    "UPDATE remote_review_items SET state='unknown_outcome',error_code='unknown_outcome',"
                    "updated_at=? WHERE item_id=?",
                    (now, row["item_id"]),
                )
                db.execute(
                    "UPDATE remote_review_proposals SET state='unknown_outcome',updated_at=? WHERE proposal_id=?",
                    (now, row["proposal_id"]),
                )
        return [dict(row) for row in rows]

    def invalidate_target_items(
        self,
        tenant_id: str,
        owner_id: str,
        target: RemoteTargetRefV1,
        reason_code: str,
    ) -> list[str]:
        active_states = (
            "queued",
            "running",
            "awaiting_call_approval",
            "awaiting_decision",
            "approved",
        )
        placeholders = ",".join("?" for _ in active_states)
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT item_id,run_id FROM remote_review_items WHERE tenant_id=? "
                "AND owner_id=? AND target_type=? AND target_id=? AND state IN ("
                + placeholders
                + ")",
                (
                    tenant_id,
                    owner_id,
                    target.target_type,
                    target.target_id,
                    *active_states,
                ),
            ).fetchall()
            now = time.time()
            for row in rows:
                db.execute(
                    "UPDATE remote_review_items SET state='drifted',error_code=?,updated_at=? "
                    "WHERE item_id=?",
                    (reason_code, now, row["item_id"]),
                )
                db.execute(
                    "UPDATE remote_review_proposals SET state='drifted',updated_at=? "
                    "WHERE item_id=? AND state IN ('proposed','started')",
                    (now, row["item_id"]),
                )
        return sorted({str(row["run_id"]) for row in rows})

    def save_catalog_contract(
        self,
        tenant_id: str,
        owner_id: str,
        contract: CatalogReviewedRemoteContractV1,
        signature: str,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO catalog_remote_contract_revisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    "catalogrev_" + uuid.uuid4().hex,
                    tenant_id,
                    owner_id,
                    contract.contract_id,
                    contract.contract_fingerprint,
                    catalog_contract_export(contract).decode("utf-8").strip(),
                    signature,
                    time.time(),
                ),
            )

    def list_catalog_contract_rows(
        self, tenant_id: str, owner_id: str
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM catalog_remote_contract_revisions WHERE tenant_id=? AND owner_id=? "
                "ORDER BY created_at DESC",
                (tenant_id, owner_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_contract(
        self,
        tenant_id: str,
        owner_id: str,
        target_type: str,
        contract_id: str,
        reason: str,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO remote_contract_revocations VALUES(?,?,?,?,?,?,?)",
                (
                    "remrevoke_" + uuid.uuid4().hex,
                    tenant_id,
                    owner_id,
                    target_type,
                    contract_id,
                    reason[:500],
                    time.time(),
                ),
            )

    def is_contract_revoked(
        self, tenant_id: str, owner_id: str, target_type: str, contract_id: str
    ) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM remote_contract_revocations WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND contract_id=? LIMIT 1",
                (tenant_id, owner_id, target_type, contract_id),
            ).fetchone()
        return row is not None


class CatalogRemoteContractRegistry:
    def __init__(
        self,
        *,
        store: MCPRemoteReviewStore,
        tenant_id: str,
        owner_id: str,
        signing_key: str,
        repository_dir: str | Path | None = None,
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self.signing_key = signing_key
        self.repository_dir = Path(
            repository_dir or Path(__file__).with_name("catalog_remote_contracts")
        )

    def _repository(self) -> list[CatalogReviewedRemoteContractV1]:
        if not self.repository_dir.exists():
            return []
        result: list[CatalogReviewedRemoteContractV1] = []
        for path in sorted(self.repository_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            result.append(CatalogReviewedRemoteContractV1.model_validate(raw))
        return result

    def _local(self) -> list[CatalogReviewedRemoteContractV1]:
        if not self.signing_key:
            return []
        result: list[CatalogReviewedRemoteContractV1] = []
        for row in self.store.list_catalog_contract_rows(self.tenant_id, self.owner_id):
            try:
                contract = CatalogReviewedRemoteContractV1.model_validate_json(
                    str(row["contract_json"])
                )
            except (ValueError, TypeError):
                continue
            expected = catalog_contract_signature(contract, self.signing_key)
            if hmac.compare_digest(expected, str(row["signature"])):
                result.append(contract)
        return result

    def all(
        self,
    ) -> tuple[list[CatalogReviewedRemoteContractV1], set[tuple[str, str, str]]]:
        by_identity: dict[tuple[str, str, str], CatalogReviewedRemoteContractV1] = {}
        collisions: set[tuple[str, str, str]] = set()
        for contract in [*self._repository(), *self._local()]:
            current = by_identity.get(contract.identity)
            if current is None:
                by_identity[contract.identity] = contract
            elif current.contract_fingerprint != contract.contract_fingerprint:
                collisions.add(contract.identity)
        return list(by_identity.values()), collisions

    def get(
        self, contract_id: str
    ) -> tuple[CatalogReviewedRemoteContractV1 | None, str]:
        contracts, collisions = self.all()
        matches = [item for item in contracts if item.contract_id == contract_id]
        if any(item.identity in collisions for item in matches):
            return None, "hub_contract_collision"
        if not matches:
            return None, "mcp_remote_contract_not_found"
        contract = matches[0]
        if self.store.is_contract_revoked(
            self.tenant_id, self.owner_id, "catalog_project", contract_id
        ):
            return contract, "mcp_remote_contract_revoked"
        return contract, ""

    def lookup_project(
        self, project_id: str, version: str, remote_url: str
    ) -> tuple[CatalogReviewedRemoteContractV1 | None, str]:
        contracts, collisions = self.all()
        identity = (project_id, version, remote_url)
        if identity in collisions:
            return None, "hub_contract_collision"
        contract = next((item for item in contracts if item.identity == identity), None)
        if contract is None:
            return None, "mcp_remote_contract_unreviewed"
        if self.store.is_contract_revoked(
            self.tenant_id, self.owner_id, "catalog_project", contract.contract_id
        ):
            return None, "mcp_remote_contract_revoked"
        return contract, ""

    def describe(self) -> list[dict[str, Any]]:
        contracts, collisions = self.all()
        repository = {item.contract_fingerprint for item in self._repository()}
        return [
            {
                **item.model_dump(mode="json"),
                "target_type": "catalog_project",
                "contract_source": (
                    "repository"
                    if item.contract_fingerprint in repository
                    else "local"
                ),
                "collision": item.identity in collisions,
                "revoked": self.store.is_contract_revoked(
                    self.tenant_id,
                    self.owner_id,
                    "catalog_project",
                    item.contract_id,
                ),
            }
            for item in sorted(contracts, key=lambda value: value.contract_id)
        ]


class RemoteTargetSessionCoordinator:
    """Invalidate executable target state without trusting client-supplied scope."""

    def __init__(
        self,
        *,
        store: MCPRemoteReviewStore,
        tenant_id: str,
        owner_id: str,
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.owner_id = owner_id

    def invalidate(
        self,
        target: RemoteTargetRefV1,
        *,
        state: Literal["drifted", "tainted", "disconnected", "revoked"],
        reason_code: str,
    ) -> RemoteTargetStateV1:
        self.store.delete_runtime_binding(self.tenant_id, self.owner_id, target)
        return self.store.set_target_state(
            self.tenant_id,
            self.owner_id,
            target,
            state,
            reason_code=reason_code,
        )


class MCPRemoteReviewService:
    """Target-neutral facade plus the Catalog authenticated review pipeline."""

    def __init__(
        self,
        *,
        hub: MCPHubService,
        hub_review: Any,
        catalog: MCPCatalogService,
        broker: MCPRemoteAuthBroker,
        oauth: MCPRemoteOAuthService,
        authorization: MCPRemoteOAuthAuthorizationService,
        store: MCPRemoteReviewStore,
        signing_key: str,
    ) -> None:
        self.hub = hub
        self.hub_review = hub_review
        self.catalog = catalog
        self.broker = broker
        self.oauth = oauth
        self.authorization = authorization
        self.store = store
        self.signing_key = str(signing_key or "")
        self.tenant_id = catalog.tenant_id
        self.owner_id = catalog.owner_id
        self.adapters: dict[str, RemoteReviewTargetAdapter] = {
            "hub_candidate": HubCandidateReviewAdapter(hub),
            "catalog_project": CatalogProjectReviewAdapter(catalog),
        }
        self.catalog_contracts = CatalogRemoteContractRegistry(
            store=store,
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
            signing_key=self.signing_key,
        )
        self.session_coordinator = RemoteTargetSessionCoordinator(
            store=store,
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._run_creation_lock = threading.RLock()
        self._item_locks: dict[str, asyncio.Lock] = {}
        self._target_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._catalog_live: dict[str, tuple[str, str]] = {}
        self.authorization.set_target_change_handler(self._oauth_target_changed)
        self.broker.add_target_change_handler(self._static_auth_target_changed)
        set_hub_admission = getattr(
            self.hub_review, "set_external_run_admission", None
        )
        if callable(set_hub_admission):
            set_hub_admission(
                self._require_no_catalog_active_run,
                self._run_creation_lock,
            )

    def _require_no_catalog_active_run(self) -> None:
        if any(
            item["status"] in {"queued", "running", "awaiting_operator"}
            for item in self.store.list_runs(self.tenant_id, self.owner_id)
        ):
            raise HubError(
                "当前本地运维者已有 Catalog 统一复核批次。",
                code="mcp_remote_review_owner_busy",
                status_code=409,
            )

    def _static_auth_target_changed(self, target_type: str, target_id: str) -> None:
        if target_type != "catalog_project":
            return
        self._invalidate_catalog_target(
            target_id, reason_code="mcp_remote_auth_binding_revision_changed"
        )

    def _oauth_target_changed(self, target_type: str, target_id: str) -> None:
        if target_type != "catalog_project":
            return
        self._invalidate_catalog_target(
            target_id, reason_code="mcp_remote_oauth_token_revision_changed"
        )

    def _invalidate_catalog_target(self, target_id: str, *, reason_code: str) -> None:
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=target_id
        )
        self.session_coordinator.invalidate(
            target,
            state="drifted",
            reason_code=reason_code,
        )
        for run_id in self.store.invalidate_target_items(
            self.tenant_id,
            self.owner_id,
            target,
            reason_code,
        ):
            self._refresh_catalog_run(run_id)
        self._close_catalog_live(target_id)

    def _close_catalog_live(self, target_id: str) -> None:
        live = self._catalog_live.pop(target_id, None)
        if live is None:
            return
        session_id, capability = live

        async def close_changed_session() -> None:
            await asyncio.gather(
                self.hub.bridge.close(session_id),
                self.hub.bridge.revoke(capability),
                return_exceptions=True,
            )

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is not None:
            current_loop.create_task(close_changed_session())
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(
                lambda: loop.create_task(close_changed_session())
            )
            return
        # A live entry is only expected after start() captured the service loop.
        # Preserve the reference if shutdown ordering violates that invariant so
        # the owning session can still perform its normal finally cleanup.
        self._catalog_live[target_id] = live

    def _require_enabled(self) -> None:
        if not review_unification_enabled():
            raise HubError(
                "MCP 远程统一复核当前未启用。",
                code="mcp_remote_review_unification_disabled",
                status_code=503,
            )

    def _require_catalog_oauth(self) -> None:
        if not catalog_oauth_enabled():
            raise HubError(
                "Catalog OAuth 当前未启用。",
                code="mcp_remote_catalog_oauth_disabled",
                status_code=503,
            )

    def _require_contract_runtime(self) -> None:
        if not contract_runtime_enabled():
            raise HubError(
                "MCP 远程契约 Runtime 当前未启用。",
                code="mcp_remote_contract_runtime_disabled",
                status_code=503,
            )

    def _require_catalog_runtime(self) -> None:
        self._require_contract_runtime()
        if not catalog_runtime_enabled():
            raise HubError(
                "Catalog 远程 Runtime 当前未启用。",
                code="mcp_remote_catalog_runtime_disabled",
                status_code=503,
            )

    def status(self) -> dict[str, Any]:
        runs = self.store.list_runs(self.tenant_id, self.owner_id)
        active = next(
            (
                item["run_id"]
                for item in runs
                if item["status"] in {"queued", "running", "awaiting_operator"}
            ),
            None,
        )
        return {
            "enabled": review_unification_enabled(),
            "catalog_oauth_enabled": catalog_oauth_enabled(),
            "local_publish_enabled": _flag("MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED"),
            "signing_key_configured": bool(self.signing_key),
            "subject_mode": "local-single-owner",
            "multi_tenant": False,
            "runtime_enabled": contract_runtime_enabled(),
            "catalog_runtime_enabled": catalog_runtime_enabled(),
            "supported_target_types": ["hub_candidate", "catalog_project"],
            "protocol_version": MCP_PROTOCOL_VERSION,
            "sop_version": REMOTE_REVIEW_SOP_VERSION,
            "max_batch_size": MAX_REVIEW_ITEMS,
            "active_run_id": active,
        }

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for target in self.store.recover_started_runtime_calls(
            self.tenant_id, self.owner_id
        ):
            self.session_coordinator.invalidate(
                target,
                state="tainted",
                reason_code="unknown_outcome",
            )
        for recovered in self.store.recover_started_calls(
            self.tenant_id, self.owner_id
        ):
            target = RemoteTargetRefV1(
                target_type=str(recovered["target_type"]),
                target_id=str(recovered["target_id"]),
            )
            self.store.set_target_state(
                self.tenant_id,
                self.owner_id,
                target,
                "tainted",
                reason_code="unknown_outcome",
            )
        if not review_unification_enabled():
            return
        for run in self.store.list_runs(self.tenant_id, self.owner_id):
            if run["status"] not in {"queued", "running"}:
                continue
            unsafe = any(
                item["current_stage"] in {
                    "representative_call",
                    "human_decision",
                    "contract_publish",
                }
                for item in run["items"]
                if item["state"] in {"running", "started"}
            )
            if unsafe:
                self.store.set_run(
                    run["run_id"],
                    status="interrupted",
                    error_code="mcp_remote_review_resume_unsafe_stage",
                )
            else:
                self._schedule(run["run_id"])

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._loop = None

    @staticmethod
    def _decorate_hub_run(run: dict[str, Any]) -> dict[str, Any]:
        output = dict(run)
        decorated = []
        for item in run.get("items") or []:
            current = dict(item)
            current["target"] = {
                "schema_version": TARGET_REF_SCHEMA_VERSION,
                "target_type": "hub_candidate",
                "target_id": str(item.get("candidate_id") or ""),
            }
            decorated.append(current)
        output["items"] = decorated
        output["target_backend"] = "hub-compatible"
        return output

    def create_run(self, targets: list[RemoteTargetRefV1]) -> dict[str, Any]:
        self._require_enabled()
        with self._run_creation_lock:
            active_statuses = {"queued", "running", "awaiting_operator"}
            catalog_active = any(
                item["status"] in active_statuses
                for item in self.store.list_runs(self.tenant_id, self.owner_id)
            )
            hub_active = any(
                item["status"] in active_statuses
                for item in self.hub_review.store.list_runs(
                    self.hub_review.tenant_id, self.hub_review.owner_id
                )
            )
            if catalog_active or hub_active:
                raise HubError(
                    "当前本地运维者已有 Hub 或 Catalog 复核批次。",
                    code="mcp_remote_review_owner_busy",
                    status_code=409,
                )
            kinds = {item.target_type for item in targets}
            if kinds == {"hub_candidate"}:
                identities = []
                for target in targets:
                    resolved = self.adapters["hub_candidate"].resolve(
                        target.target_id
                    )
                    identities.append(
                        {
                            key: resolved[key]
                            for key in ("server_name", "version", "remote_id")
                        }
                    )
                return self._decorate_hub_run(
                    self.hub_review.create_run(identities)
                )
            if kinds != {"catalog_project"}:
                raise HubError(
                    "单个复核批次不能混合 Hub 与 Catalog 目标。",
                    code="mcp_remote_review_mixed_target_batch_denied",
                    status_code=422,
                )
            for target in targets:
                self.adapters["catalog_project"].resolve(target.target_id)
            run = self.store.create_run(self.tenant_id, self.owner_id, targets)
            for target in targets:
                self.store.delete_runtime_binding(
                    self.tenant_id, self.owner_id, target
                )
                self._close_catalog_live(target.target_id)
                self.store.set_target_state(
                    self.tenant_id, self.owner_id, target, "reviewing"
                )
            self._schedule(run["run_id"])
            return run

    def list_runs(self) -> list[dict[str, Any]]:
        self._require_enabled()
        catalog = self.store.list_runs(self.tenant_id, self.owner_id)
        hub = [
            self._decorate_hub_run(item)
            for item in self.hub_review.store.list_runs(
                self.hub_review.tenant_id, self.hub_review.owner_id
            )
        ]
        return sorted(
            [*catalog, *hub],
            key=lambda item: float(item.get("updated_at") or 0),
            reverse=True,
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self._decorate_hub_run(
                self.hub_review.store.require_run(
                    run_id, self.hub_review.tenant_id, self.hub_review.owner_id
                )
            )
        return self.store.require_run(run_id, self.tenant_id, self.owner_id)

    def _schedule(self, run_id: str) -> None:
        current = self._tasks.get(run_id)
        if current is None or current.done():
            self._tasks[run_id] = asyncio.create_task(self._run_catalog(run_id))

    async def _run_catalog(self, run_id: str) -> None:
        self.store.set_run(run_id, status="running", error_code="")
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        semaphore = asyncio.Semaphore(2)

        async def process(item: dict[str, Any]) -> None:
            async with semaphore:
                if item["state"] in {"queued", "running", "failed", "interrupted"}:
                    await self._process_catalog_item(run_id, item["item_id"])

        try:
            await asyncio.gather(*(process(item) for item in run["items"]))
            self._refresh_catalog_run(run_id)
        except asyncio.CancelledError:
            self.store.set_run(
                run_id, status="interrupted", error_code="mcp_remote_review_interrupted"
            )
            raise
        except Exception:
            self.store.set_run(
                run_id, status="failed", error_code="mcp_remote_review_internal_error"
            )
        finally:
            self._tasks.pop(run_id, None)

    def _refresh_catalog_run(self, run_id: str) -> None:
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if run["cancel_requested"]:
            self.store.set_run(run_id, status="cancelled")
            return
        states = {item["state"] for item in run["items"]}
        if states & {"awaiting_call_approval", "awaiting_decision", "approved"}:
            status = "awaiting_operator"
        elif states <= {"published", "blocked", "cancelled", "revoked"}:
            status = "completed"
        elif states & {"running", "queued"}:
            status = "running"
        else:
            status = "failed"
        self.store.set_run(run_id, status=status)

    def _event(
        self,
        run_id: str,
        item_id: str,
        stage: str,
        *,
        status: str = "passed",
        safe_to_retry: bool,
        error_code: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.add_event(
            run_id,
            item_id,
            stage,
            status=status,
            safe_to_retry=safe_to_retry,
            error_code=error_code,
            payload=payload,
        )

    def _cancel_catalog_item_if_requested(
        self,
        run_id: str,
        item_id: str,
        target: RemoteTargetRefV1,
    ) -> bool:
        with self._run_creation_lock:
            run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
            if not run["cancel_requested"]:
                return False
            self.store.set_item(
                item_id,
                state="cancelled",
                error_code="mcp_remote_review_cancelled",
            )
            current = self.store.get_target_state(
                self.tenant_id, self.owner_id, target
            )
            if current.state in {"draft", "reviewing", "disconnected"}:
                self.store.set_target_state(
                    self.tenant_id,
                    self.owner_id,
                    target,
                    "disconnected",
                    reason_code="mcp_remote_review_cancelled",
                )
            return True

    def _require_catalog_run_active(self, run_id: str) -> None:
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if run["cancel_requested"] or run["status"] == "cancelled":
            raise HubError(
                "已取消的统一复核批次不能继续操作。",
                code="mcp_remote_review_cancelled",
                status_code=409,
            )

    def _catalog_binding_context(
        self, manifest: CatalogAdapterManifest
    ) -> tuple[RemoteAuthPolicyV1, Any, str]:
        policy = self.catalog._catalog_remote_auth_policy(manifest)
        if policy is None:
            raise HubError(
                "目录静态认证策略不可用。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=409,
            )
        try:
            binding = self.broker.binding_for_target(
                target_type="catalog_project",
                target_id=manifest.project_id,
                current_policy=policy,
            )
        except RemoteAuthError as exc:
            raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None
        if binding is None:
            raise HubError(
                "目录远程项目尚未绑定凭据。",
                code="mcp_remote_auth_binding_missing",
                status_code=409,
            )
        revision_digest = canonical_digest(
            {
                "schema_version": "catalog-auth-revision-v1",
                "target_type": "catalog_project",
                "target_id": manifest.project_id,
                "policy_fingerprint": policy.policy_fingerprint,
                "revision": binding.revision,
            }
        )
        return policy, binding, revision_digest

    def _catalog_oauth_context(
        self, snapshot: CatalogRemoteSnapshotV1
    ) -> tuple[Any, str]:
        try:
            metadata = self.authorization.execution_metadata(
                target_type="catalog_project",
                target_id=snapshot.project_id,
                source_digest=snapshot.source_digest,
            )
        except RemoteOAuthError as exc:
            raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None
        if metadata.origin != snapshot.origin:
            raise HubError(
                "Catalog OAuth Origin 与 manifest 不一致。",
                code="mcp_remote_oauth_scope_denied",
                status_code=409,
            )
        assessment = assess_oauth_scopes(tuple(metadata.scopes))
        if assessment["dangerous_scopes"]:
            raise HubError(
                "OAuth Scope 含高危写入或控制语义，本轮拒绝复核。",
                code="mcp_remote_oauth_high_risk_scope_denied",
                status_code=409,
            )
        return metadata, metadata.token_revision_digest

    def _resolve_catalog_runtime_contract(
        self, project_id: str
    ) -> tuple[
        dict[str, Any],
        CatalogReviewedRemoteContractV1,
        ResolvedRemoteContractV1,
    ]:
        resolved = self.adapters["catalog_project"].resolve(project_id)
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        manifest: CatalogAdapterManifest = resolved["manifest"]
        contract, error = self.catalog_contracts.lookup_project(
            project_id, manifest.adapter_version, snapshot.remote_url
        )
        if contract is None or error:
            raise HubError(
                "Catalog 远程执行契约不可用。",
                code=error or "mcp_remote_contract_unreviewed",
                status_code=409,
            )
        if (
            contract.source_digest != snapshot.source_digest
            or contract.version != snapshot.adapter_version
            or contract.remote_url != snapshot.remote_url
            or contract.origin != snapshot.origin
            or contract.protocol_version != MCP_PROTOCOL_VERSION
            or contract.transport != "streamable-http"
        ):
            raise HubError(
                "Catalog manifest 或远程 Origin 已漂移。",
                code="mcp_remote_catalog_manifest_drift",
                status_code=409,
            )
        return resolved, contract, ResolvedRemoteContractV1.from_catalog(contract)

    def _catalog_runtime_auth_context(
        self,
        *,
        resolved: dict[str, Any],
        contract: CatalogReviewedRemoteContractV1,
    ) -> str:
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        manifest: CatalogAdapterManifest = resolved["manifest"]
        if snapshot.auth_mode in {"static_bearer", "static_header"}:
            policy, _binding, revision_digest = self._catalog_binding_context(manifest)
            if (
                contract.remote_auth_policy is None
                or policy.policy_fingerprint
                != contract.remote_auth_policy.policy_fingerprint
                or contract.auth_mode != snapshot.auth_mode
            ):
                raise HubError(
                    "Catalog 静态认证策略已漂移。",
                    code="mcp_remote_auth_binding_stale",
                    status_code=409,
                )
            scope_digest = ""
        else:
            metadata, revision_digest = self._catalog_oauth_context(snapshot)
            if (
                contract.remote_oauth_policy is None
                or metadata.policy_fingerprint
                != contract.remote_oauth_policy.policy_fingerprint
                or metadata.scope_digest != contract.authorized_scope_digest
                or tuple(metadata.scopes) != tuple(contract.authorized_scopes)
            ):
                raise HubError(
                    "Catalog OAuth Scope 或策略已漂移。",
                    code="mcp_remote_oauth_contract_scope_drift",
                    status_code=409,
                )
            scope_digest = metadata.scope_digest
        return canonical_digest(
            {
                "schema_version": "catalog-runtime-auth-context-v1",
                "target_type": "catalog_project",
                "target_id": snapshot.project_id,
                "contract_fingerprint": contract.contract_fingerprint,
                "auth_policy_fingerprint": (
                    contract.remote_auth_policy.policy_fingerprint
                    if contract.remote_auth_policy is not None
                    else contract.remote_oauth_policy.policy_fingerprint
                ),
                "auth_revision_digest": revision_digest,
                "authorized_scope_digest": scope_digest,
            }
        )

    def _validate_catalog_runtime_session(
        self,
        *,
        resolved: dict[str, Any],
        contract: CatalogReviewedRemoteContractV1,
        session: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], str]:
        tools = session.get("tools")
        if not isinstance(tools, list):
            raise HubError(
                "Catalog 远程工具列表无效。",
                code="hub_schema_drift",
                status_code=409,
            )
        current = {
            str(item.get("name") or ""): str(item.get("schema_digest") or "")
            for item in tools
            if isinstance(item, dict)
        }
        if (
            str(session.get("schema_digest") or "") != contract.schema_digest
            or current != contract.tool_schema_digests
        ):
            raise HubError(
                "Catalog 远程工具 Schema 已漂移。",
                code="hub_schema_drift",
                status_code=409,
            )
        allowed = set(contract.allowed_tools)
        tool_schemas: dict[str, dict[str, Any]] = {}
        for item in tools:
            name = str(item.get("name") or "")
            if name not in allowed:
                continue
            input_schema = item.get("input_schema")
            schema_digest = str(item.get("schema_digest") or "")
            if not isinstance(input_schema, dict) or canonical_digest(input_schema) != schema_digest:
                raise HubError(
                    "Catalog 工具 Schema 结构无效。",
                    code="hub_schema_drift",
                    status_code=409,
                )
            tool_schemas[name] = {
                "input_schema": dict(input_schema),
                "schema_digest": schema_digest,
            }
        if set(tool_schemas) != allowed:
            raise HubError(
                "Catalog 契约工具子集已漂移。",
                code="hub_schema_drift",
                status_code=409,
            )
        auth_context_digest = self._catalog_runtime_auth_context(
            resolved=resolved,
            contract=contract,
        )
        return tool_schemas, auth_context_digest

    @staticmethod
    def _catalog_sidecar_target_id(snapshot: CatalogRemoteSnapshotV1) -> str:
        digest = canonical_digest(
            {
                "schema_version": "catalog-sidecar-target-v1",
                "target_type": "catalog_project",
                "project_id": snapshot.project_id,
                "source_digest": snapshot.source_digest,
                "remote_url": snapshot.remote_url,
            }
        )
        return f"mcphub_{digest[:32]}"

    @staticmethod
    def _static_auth_payload(envelope: Any, target_id: str) -> dict[str, Any]:
        return {
            "binding_id": envelope.binding_id,
            "binding_revision": envelope.binding_revision,
            "header_name": envelope.header_name,
            "header_value": envelope.header_value,
            "origin": envelope.origin,
            "policy_fingerprint": envelope.policy_fingerprint,
            "target_id": target_id,
        }

    @staticmethod
    def _oauth_auth_payload(metadata: Any, value: str, target_id: str) -> dict[str, Any]:
        return {
            "auth_mode": "oauth_authorization_code_pkce",
            "header_value": value,
            "origin": metadata.origin,
            "policy_fingerprint": metadata.policy_fingerprint,
            "protocol_version": metadata.protocol_version,
            "resource_digest": metadata.resource_digest,
            "scope_digest": metadata.scope_digest,
            "target_id": target_id,
            "token_revision_digest": metadata.token_revision_digest,
        }

    @staticmethod
    async def _open_catalog_bridge(
        bridge: HubBridgeProtocol,
        *,
        manifest: CatalogAdapterManifest,
        sidecar_target_id: str,
        remote_url: str,
        capability: str,
        session_owner: str,
        auth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if auth is not None:
            kwargs["auth"] = auth
        if manifest.allowed_inert_server_capabilities:
            kwargs["allowed_inert_capabilities"] = (
                manifest.allowed_inert_server_capabilities
            )
        return await bridge.open(
            sidecar_target_id,
            remote_url,
            capability,
            session_owner,
            **kwargs,
        )

    async def _open_static_catalog(
        self,
        *,
        manifest: CatalogAdapterManifest,
        snapshot: CatalogRemoteSnapshotV1,
        sidecar_target_id: str,
        capability: str,
        session_owner: str,
        expected: CatalogRemoteEvidenceBundleV1 | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        policy, binding, revision_digest = self._catalog_binding_context(manifest)
        if expected is not None and (
            expected.auth_policy_fingerprint != policy.policy_fingerprint
            or expected.auth_revision_digest != revision_digest
        ):
            raise HubError(
                "目录静态认证 revision 已变化。",
                code="mcp_remote_auth_binding_stale",
                status_code=409,
            )
        try:
            with self.broker.resolve_for_execution(
                binding.binding_id,
                current_policy=policy,
                target_type="catalog_project",
                target_id=snapshot.project_id,
            ) as envelope:
                auth = self._static_auth_payload(envelope, sidecar_target_id)
                try:
                    response = await self._open_catalog_bridge(
                        self.hub.bridge,
                        manifest=manifest,
                        sidecar_target_id=sidecar_target_id,
                        remote_url=snapshot.remote_url,
                        capability=capability,
                        session_owner=session_owner,
                        auth=auth,
                    )
                finally:
                    auth["header_value"] = ""
        except RemoteAuthError as exc:
            raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None
        return response, {
            "auth_policy_fingerprint": policy.policy_fingerprint,
            "auth_revision_digest": revision_digest,
            "authorized_scopes": (),
            "authorized_scope_digest": "",
            "remote_auth_policy": policy,
            "remote_oauth_policy": None,
        }

    async def _open_oauth_catalog(
        self,
        *,
        manifest: CatalogAdapterManifest,
        snapshot: CatalogRemoteSnapshotV1,
        sidecar_target_id: str,
        capability: str,
        session_owner: str,
        expected: CatalogRemoteEvidenceBundleV1 | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata, revision_digest = self._catalog_oauth_context(snapshot)
        if expected is not None and (
            expected.auth_policy_fingerprint != metadata.policy_fingerprint
            or expected.auth_revision_digest != revision_digest
            or expected.authorized_scope_digest != metadata.scope_digest
        ):
            raise HubError(
                "Catalog OAuth evidence 已变化。",
                code="mcp_remote_oauth_contract_scope_drift",
                status_code=409,
            )
        try:
            with self.authorization.resolve_for_execution(
                target_type="catalog_project",
                target_id=snapshot.project_id,
                source_digest=snapshot.source_digest,
                expected_policy_fingerprint=metadata.policy_fingerprint,
                expected_scope_digest=metadata.scope_digest,
                expected_token_revision_digest=metadata.token_revision_digest,
            ) as envelope:
                auth = self._oauth_auth_payload(
                    metadata, envelope.authorization_value, sidecar_target_id
                )
                try:
                    response = await self._open_catalog_bridge(
                        self.hub.bridge,
                        manifest=manifest,
                        sidecar_target_id=sidecar_target_id,
                        remote_url=snapshot.remote_url,
                        capability=capability,
                        session_owner=session_owner,
                        auth=auth,
                    )
                finally:
                    auth["header_value"] = ""
        except RemoteOAuthError as exc:
            raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None
        discovery = self.oauth.store.active_discovery(
            subject=self.oauth.subject_resolver.resolve(),
            target_type="catalog_project",
            target_id=snapshot.project_id,
        )
        if discovery is None or not isinstance(discovery.policy, RemoteOAuthPolicyV2):
            raise HubError(
                "Catalog OAuth discovery 已漂移。",
                code="mcp_remote_oauth_discovery_stale",
                status_code=409,
            )
        return response, {
            "auth_policy_fingerprint": metadata.policy_fingerprint,
            "auth_revision_digest": revision_digest,
            "authorized_scopes": metadata.scopes,
            "authorized_scope_digest": metadata.scope_digest,
            "remote_auth_policy": None,
            "remote_oauth_policy": discovery.policy,
        }

    @asynccontextmanager
    async def _catalog_session(
        self,
        resolved: dict[str, Any],
        *,
        authenticated: bool,
        expected_evidence: CatalogRemoteEvidenceBundleV1 | None = None,
        cleanup_observer: dict[str, bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        lock = self._target_locks.setdefault(
            ("catalog_project", snapshot.project_id), asyncio.Lock()
        )
        async with lock:
            async with self._catalog_session_unlocked(
                resolved,
                authenticated=authenticated,
                expected_evidence=expected_evidence,
                cleanup_observer=cleanup_observer,
            ) as session:
                yield session

    @asynccontextmanager
    async def _catalog_session_unlocked(
        self,
        resolved: dict[str, Any],
        *,
        authenticated: bool,
        expected_evidence: CatalogRemoteEvidenceBundleV1 | None = None,
        cleanup_observer: dict[str, bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        manifest: CatalogAdapterManifest = resolved["manifest"]
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        bridge: HubBridgeProtocol = self.hub.bridge
        sidecar_target_id = self._catalog_sidecar_target_id(snapshot)
        capability = await bridge.authorize(sidecar_target_id, snapshot.remote_url)
        session_id = ""
        cleanup = {
            "temporary_session_closed": False,
            "capability_revoked": False,
        }
        session_owner = ":".join(
            (
                "hub",
                quote(self.tenant_id, safe=""),
                quote(self.owner_id, safe=""),
                sidecar_target_id,
            )
        )
        try:
            if not authenticated:
                response = await self._open_catalog_bridge(
                    bridge,
                    manifest=manifest,
                    sidecar_target_id=sidecar_target_id,
                    remote_url=snapshot.remote_url,
                    capability=capability,
                    session_owner=session_owner,
                )
                auth_metadata: dict[str, Any] = {}
            elif snapshot.auth_mode in {"static_bearer", "static_header"}:
                response, auth_metadata = await self._open_static_catalog(
                    manifest=manifest,
                    snapshot=snapshot,
                    sidecar_target_id=sidecar_target_id,
                    capability=capability,
                    session_owner=session_owner,
                    expected=expected_evidence,
                )
            else:
                response, auth_metadata = await self._open_oauth_catalog(
                    manifest=manifest,
                    snapshot=snapshot,
                    sidecar_target_id=sidecar_target_id,
                    capability=capability,
                    session_owner=session_owner,
                    expected=expected_evidence,
                )
            session_id = str(response.get("session_id") or "")
            if not session_id:
                raise HubError(
                    "目录远程 MCP 临时会话无效。",
                    code="hub_sidecar_invalid",
                    status_code=502,
                )
            tools, schema_digest = self.hub._validate_tools(response.get("tools"))
            listed = await bridge.list_tools(session_id)
            listed_tools, listed_digest = self.hub._validate_tools(listed.get("tools"))
            tool_map = {item["name"]: item["schema_digest"] for item in tools}
            listed_map = {
                item["name"]: item["schema_digest"] for item in listed_tools
            }
            if listed_digest != schema_digest or listed_map != tool_map:
                raise HubError(
                    "目录远程工具列表在同一临时会话内漂移。",
                    code="hub_schema_drift",
                    status_code=409,
                )
            self._catalog_live[snapshot.project_id] = (session_id, capability)
            yield {
                "session_id": session_id,
                "tools": listed_tools,
                "schema_digest": listed_digest,
                "cleanup": cleanup,
                **auth_metadata,
            }
        finally:
            self._catalog_live.pop(snapshot.project_id, None)
            close_result, revoke_result = await asyncio.gather(
                bridge.close(session_id) if session_id else asyncio.sleep(0),
                bridge.revoke(capability),
                return_exceptions=True,
            )
            cleanup["temporary_session_closed"] = not isinstance(
                close_result, BaseException
            )
            cleanup["capability_revoked"] = not isinstance(
                revoke_result, BaseException
            )
            if cleanup_observer is not None:
                cleanup_observer.update(cleanup)

    def _make_catalog_proposal(
        self,
        *,
        run_id: str,
        item_id: str,
        tools: list[dict[str, Any]],
        effects: dict[str, str],
    ) -> dict[str, Any] | None:
        for tool in sorted(tools, key=deterministic_proposal_sort_key):
            if effects.get(str(tool["name"])) != "read_candidate":
                continue
            arguments = deterministic_arguments(dict(tool.get("input_schema") or {}))
            if arguments is None:
                continue
            return self.store.create_proposal(
                run_id=run_id,
                item_id=item_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
                tool_name=str(tool["name"]),
                arguments=arguments,
                schema_digest=str(tool["schema_digest"]),
            )
        return None

    async def _process_catalog_item(self, run_id: str, item_id: str) -> None:
        item = self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=item["target_id"]
        )
        if self._cancel_catalog_item_if_requested(run_id, item_id, target):
            return
        self.store.set_item(item_id, state="running", error_code="")
        try:
            resolved = self.adapters["catalog_project"].resolve(target.target_id)
            snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
            self.store.set_item(item_id, snapshot=snapshot.model_dump(mode="json"))
            self._event(
                run_id,
                item_id,
                "target_snapshot",
                safe_to_retry=True,
                payload={"snapshot_digest": snapshot.snapshot_digest},
            )
            self._event(
                run_id,
                item_id,
                "static_policy",
                safe_to_retry=True,
                payload={"auth_mode": snapshot.auth_mode, "origin": snapshot.origin},
            )
            unauth_digest = ""
            unauth_tools: list[dict[str, Any]] = []
            unauth_status = "passed"
            unauth_error_code = ""
            unauth_cleanup: dict[str, bool] = {}
            try:
                async with self._catalog_session(
                    resolved,
                    authenticated=False,
                    cleanup_observer=unauth_cleanup,
                ) as unauthenticated:
                    unauth_digest = unauthenticated["schema_digest"]
                    unauth_tools = unauthenticated["tools"]
            except HubError as exc:
                if exc.code not in AUTH_FAILURE_CODES:
                    raise
                unauth_status = "observed_auth_required"
                unauth_error_code = exc.code
            if not unauth_cleanup or not all(unauth_cleanup.values()):
                self._event(
                    run_id,
                    item_id,
                    "cleanup",
                    status="failed",
                    safe_to_retry=True,
                    error_code="mcp_remote_review_cleanup_failed",
                )
                raise HubError(
                    "Catalog 未认证预检临时资源清理失败。",
                    code="mcp_remote_review_cleanup_failed",
                    status_code=503,
                )
            if self._cancel_catalog_item_if_requested(run_id, item_id, target):
                return
            self._event(
                run_id,
                item_id,
                "network_preflight",
                safe_to_retry=True,
            )
            self._event(
                run_id,
                item_id,
                "unauthenticated_initialize_tools",
                status=unauth_status,
                safe_to_retry=True,
                error_code=unauth_error_code,
                payload={"tool_count": len(unauth_tools)},
            )
            self._event(
                run_id, item_id, "credential_resolution", safe_to_retry=False
            )
            authenticated_cleanup: dict[str, bool] = {}
            async with self._catalog_session(
                resolved,
                authenticated=True,
                cleanup_observer=authenticated_cleanup,
            ) as authenticated:
                if self._cancel_catalog_item_if_requested(run_id, item_id, target):
                    return
                tools = list(authenticated["tools"])
                schema_digest = str(authenticated["schema_digest"])
                tool_digests = {
                    str(tool["name"]): str(tool["schema_digest"])
                    for tool in tools
                }
                effects = {
                    str(tool["name"]): classify_tool_effect(tool) for tool in tools
                }
                proposal = self._make_catalog_proposal(
                    run_id=run_id,
                    item_id=item_id,
                    tools=tools,
                    effects=effects,
                )
                self._event(
                    run_id,
                    item_id,
                    "authenticated_initialize_tools",
                    safe_to_retry=True,
                    payload={"tool_count": len(tools)},
                )
                self._event(
                    run_id,
                    item_id,
                    "schema_freeze",
                    safe_to_retry=True,
                    payload={"schema_digest": schema_digest},
                )
            if self._cancel_catalog_item_if_requested(run_id, item_id, target):
                return
            evidence = self._build_catalog_evidence(
                snapshot=snapshot,
                unauth_digest=unauth_digest,
                unauth_tool_count=len(unauth_tools),
                unauth_status=unauth_status,
                unauth_error_code=unauth_error_code,
                schema_digest=schema_digest,
                tool_digests=tool_digests,
                effects=effects,
                authenticated=authenticated,
                unauthenticated_cleanup=unauth_cleanup,
                authenticated_cleanup=authenticated_cleanup,
                proposal_available=proposal is not None,
            )
            self._event(
                run_id,
                item_id,
                "cleanup",
                status=(
                    "passed"
                    if authenticated_cleanup and all(authenticated_cleanup.values())
                    else "failed"
                ),
                safe_to_retry=True,
                error_code=(
                    ""
                    if authenticated_cleanup and all(authenticated_cleanup.values())
                    else "mcp_remote_review_cleanup_failed"
                ),
            )
            if not authenticated_cleanup or not all(authenticated_cleanup.values()):
                raise HubError(
                    "Catalog 远程复核临时资源清理失败。",
                    code="mcp_remote_review_cleanup_failed",
                    status_code=503,
                )
            with self._run_creation_lock:
                if self._cancel_catalog_item_if_requested(run_id, item_id, target):
                    return
                self.store.set_item(
                    item_id,
                    state=("awaiting_call_approval" if proposal else "blocked"),
                    evidence=evidence.model_dump(mode="json"),
                    evidence_digest=evidence.evidence_digest,
                    error_code=("" if proposal else "manual_call_unavailable"),
                )
            self._event(
                run_id,
                item_id,
                "effect_call_proposal",
                status=("passed" if proposal else "blocked"),
                safe_to_retry=True,
                error_code=("" if proposal else "manual_call_unavailable"),
                payload=(
                    {"proposal_digest": proposal["proposal_digest"]}
                    if proposal
                    else {}
                ),
            )
        except HubError as exc:
            self.store.set_item(item_id, state="blocked", error_code=exc.code)
            target_state = (
                "tainted"
                if exc.code == "mcp_remote_review_cleanup_failed"
                else "drifted" if "drift" in exc.code else "disconnected"
            )
            self.store.set_target_state(
                self.tenant_id,
                self.owner_id,
                target,
                target_state,
                reason_code=exc.code,
            )
        except Exception:
            self.store.set_item(
                item_id,
                state="failed",
                error_code="mcp_remote_review_internal_error",
            )

    @staticmethod
    def _build_catalog_evidence(
        *,
        snapshot: CatalogRemoteSnapshotV1,
        unauth_digest: str,
        unauth_tool_count: int,
        unauth_status: str,
        unauth_error_code: str,
        schema_digest: str,
        tool_digests: dict[str, str],
        effects: dict[str, str],
        authenticated: dict[str, Any],
        unauthenticated_cleanup: dict[str, bool],
        authenticated_cleanup: dict[str, bool],
        proposal_available: bool,
    ) -> CatalogRemoteEvidenceBundleV1:
        stages = {
            name: {"status": "passed", "implementation_version": "r4a-v1"}
            for name in (
                "target_snapshot",
                "static_policy",
                "network_preflight",
                "unauthenticated_initialize_tools",
                "credential_resolution",
                "authenticated_initialize_tools",
                "schema_freeze",
                "effect_call_proposal",
                "cleanup",
            )
        }
        stages["unauthenticated_initialize_tools"] = {
            "status": unauth_status,
            "implementation_version": "r4a-v1",
            "tool_count": unauth_tool_count,
            "error_code": unauth_error_code,
        }
        if not proposal_available:
            stages["effect_call_proposal"] = {
                "status": "blocked",
                "implementation_version": "r4a-v1",
                "error_code": "manual_call_unavailable",
            }
        return CatalogRemoteEvidenceBundleV1(
            snapshot=snapshot,
            stages=stages,
            unauthenticated_schema_digest=unauth_digest,
            schema_digest=schema_digest,
            tool_schema_digests=tool_digests,
            effect_proposals=effects,
            auth_policy_fingerprint=authenticated["auth_policy_fingerprint"],
            auth_revision_digest=authenticated["auth_revision_digest"],
            authorized_scopes=authenticated["authorized_scopes"],
            authorized_scope_digest=authenticated["authorized_scope_digest"],
            cleanup={
                "unauthenticated_session_closed": bool(
                    unauthenticated_cleanup.get("temporary_session_closed")
                ),
                "unauthenticated_capability_revoked": bool(
                    unauthenticated_cleanup.get("capability_revoked")
                ),
                "authenticated_session_closed": bool(
                    authenticated_cleanup.get("temporary_session_closed")
                ),
                "authenticated_capability_revoked": bool(
                    authenticated_cleanup.get("capability_revoked")
                ),
            },
        )

    def _catalog_item_evidence(
        self, run_id: str, item_id: str
    ) -> tuple[dict[str, Any], CatalogRemoteEvidenceBundleV1]:
        item = self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )
        try:
            evidence = CatalogRemoteEvidenceBundleV1.model_validate(item["evidence"])
        except ValueError as exc:
            raise HubError(
                "统一复核证据无效。",
                code="mcp_remote_review_evidence_invalid",
                status_code=409,
            ) from exc
        return item, evidence

    async def approve_proposal(
        self,
        run_id: str,
        item_id: str,
        proposal_id: str,
        expected_digest: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return await self.hub_review.approve_proposal(
                run_id, item_id, proposal_id, expected_digest
            )
        self._require_catalog_run_active(run_id)
        item, evidence = self._catalog_item_evidence(run_id, item_id)
        proposal = self.store.require_proposal(
            proposal_id, self.tenant_id, self.owner_id
        )
        if (
            proposal["run_id"] != run_id
            or proposal["item_id"] != item_id
            or proposal["proposal_digest"] != expected_digest
            or proposal["state"] != "proposed"
            or item["state"] != "awaiting_call_approval"
        ):
            raise HubError(
                "代表调用批准范围、摘要或状态无效。",
                code="mcp_remote_review_proposal_digest",
                status_code=409,
            )
        lock = self._item_locks.setdefault(item_id, asyncio.Lock())
        async with lock:
            proposal = self.store.require_proposal(
                proposal_id, self.tenant_id, self.owner_id
            )
            if proposal["state"] != "proposed":
                raise HubError(
                    "代表调用批准不可重放。",
                    code="mcp_remote_review_call_replay",
                    status_code=409,
                )
            resolved = self.adapters["catalog_project"].resolve(item["target_id"])
            snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
            if snapshot.source_digest != evidence.snapshot.source_digest:
                raise HubError(
                    "Catalog manifest 已漂移。",
                    code="mcp_remote_catalog_manifest_drift",
                    status_code=409,
                )
            target = RemoteTargetRefV1(
                target_type="catalog_project", target_id=item["target_id"]
            )
            started = False
            result_recorded = False
            representative_cleanup: dict[str, bool] = {}
            try:
                async with self._catalog_session(
                    resolved, authenticated=True, expected_evidence=evidence
                ) as session:
                    current = {
                        str(tool["name"]): str(tool["schema_digest"])
                        for tool in session["tools"]
                    }
                    if (
                        session["schema_digest"] != evidence.schema_digest
                        or current != evidence.tool_schema_digests
                        or current.get(proposal["tool_name"])
                        != proposal["schema_digest"]
                    ):
                        raise HubError(
                            "Catalog 远程工具 Schema 已漂移。",
                            code="hub_schema_drift",
                            status_code=409,
                        )
                    self.store.begin_call(proposal, target)
                    self._event(
                        run_id,
                        item_id,
                        "representative_call",
                        status="started",
                        safe_to_retry=False,
                        payload={"proposal_digest": expected_digest},
                    )
                    started = True
                    response = await asyncio.wait_for(
                        self.hub.bridge.call(
                            session["session_id"],
                            proposal["tool_name"],
                            dict(proposal["arguments"]),
                        ),
                        timeout=20,
                    )
                    representative_cleanup = session["cleanup"]
                self._record_catalog_call_result(
                    item_id=item_id,
                    proposal=proposal,
                    evidence=evidence,
                    response=response,
                    cleanup=dict(representative_cleanup),
                )
                result_recorded = True
            except Exception as exc:
                if started and not result_recorded:
                    self.store.finish_call(
                        proposal_id,
                        state="unknown_outcome",
                        error_code="unknown_outcome",
                    )
                    self.store.set_item(
                        item_id,
                        state="unknown_outcome",
                        error_code="unknown_outcome",
                    )
                    self.store.set_target_state(
                        self.tenant_id,
                        self.owner_id,
                        target,
                        "tainted",
                        reason_code="unknown_outcome",
                    )
                    self._refresh_catalog_run(run_id)
                    raise HubError(
                        "代表调用结果未知；旧审批不可重试。",
                        code="unknown_outcome",
                        status_code=409,
                    ) from None
                if isinstance(exc, HubError) and exc.code in {
                    "mcp_remote_catalog_manifest_drift",
                    "hub_schema_drift",
                    "mcp_remote_auth_binding_stale",
                    "mcp_remote_auth_scope_denied",
                    "mcp_remote_oauth_contract_scope_drift",
                    "mcp_remote_oauth_discovery_stale",
                    "mcp_remote_review_evidence_stale",
                }:
                    self.store.finish_call(
                        proposal_id,
                        state="drifted",
                        error_code=exc.code,
                    )
                    self.store.set_item(
                        item_id,
                        state="drifted",
                        error_code=exc.code,
                    )
                    self.store.set_target_state(
                        self.tenant_id,
                        self.owner_id,
                        target,
                        "drifted",
                        reason_code=exc.code,
                    )
                    self._refresh_catalog_run(run_id)
                raise
            cleanup_ok = bool(representative_cleanup) and all(
                representative_cleanup.values()
            )
            self._event(
                run_id,
                item_id,
                "representative_cleanup",
                status="passed" if cleanup_ok else "failed",
                safe_to_retry=False,
                error_code="" if cleanup_ok else "mcp_remote_review_cleanup_failed",
            )
            if not cleanup_ok:
                self.store.set_item(
                    item_id,
                    state="blocked",
                    error_code="mcp_remote_review_cleanup_failed",
                )
                self.store.set_target_state(
                    self.tenant_id,
                    self.owner_id,
                    target,
                    "tainted",
                    reason_code="mcp_remote_review_cleanup_failed",
                )
                self._refresh_catalog_run(run_id)
                raise HubError(
                    "代表调用结果已收到，但临时资源清理失败；目标已污染且不可发布。",
                    code="mcp_remote_review_cleanup_failed",
                    status_code=503,
                )
        self._refresh_catalog_run(run_id)
        return self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )

    def _record_catalog_call_result(
        self,
        *,
        item_id: str,
        proposal: dict[str, Any],
        evidence: CatalogRemoteEvidenceBundleV1,
        response: dict[str, Any],
        cleanup: dict[str, bool],
    ) -> None:
        result = response.get("result")
        if not isinstance(result, dict):
            raise HubError(
                "Catalog 远程结果结构无效。",
                code="hub_result_denied",
                status_code=502,
            )
        encoded = canonical_json_bytes(result)
        if len(encoded) > 256 * 1024:
            raise HubError(
                "Catalog 远程结果超过上限。",
                code="hub_result_denied",
                status_code=502,
            )
        result_digest = canonical_digest(result)
        result_type = (
            "mcp-content" if isinstance(result.get("content"), list) else "object"
        )
        remote_error = bool(result.get("isError") or result.get("is_error"))
        self.store.finish_call(
            proposal["proposal_id"],
            state="completed",
            result_digest=result_digest,
            result_size=len(encoded),
            result_type=result_type,
        )
        payload = evidence.model_dump(mode="json")
        payload["representative_call"] = {
            "proposal_digest": proposal["proposal_digest"],
            "tool_name": proposal["tool_name"],
            "arguments_digest": proposal["arguments_digest"],
            "result_digest": result_digest,
            "result_size": len(encoded),
            "result_type": result_type,
            "assertions": {
                "result_is_object": True,
                "remote_reported_error": remote_error,
            },
            "cleanup": cleanup,
        }
        updated = CatalogRemoteEvidenceBundleV1.model_validate(payload)
        self.store.set_item(
            item_id,
            state=("blocked" if remote_error else "awaiting_decision"),
            evidence=updated.model_dump(mode="json"),
            evidence_digest=updated.evidence_digest,
            error_code=(
                "mcp_remote_review_representative_call_error" if remote_error else ""
            ),
        )

    def decide(
        self,
        run_id: str,
        item_id: str,
        *,
        decision: Literal["approve", "block"],
        expected_evidence_digest: str,
        allowed_tools: list[str],
        tool_effects: dict[str, Literal["read"]],
        acknowledge_unknown_oauth_scopes: bool = False,
    ) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self.hub_review.decide(
                run_id,
                item_id,
                decision=decision,
                expected_evidence_digest=expected_evidence_digest,
                allowed_tools=allowed_tools,
                tool_effects=tool_effects,
                acknowledge_unknown_oauth_scopes=acknowledge_unknown_oauth_scopes,
            )
        self._require_catalog_run_active(run_id)
        item, evidence = self._catalog_item_evidence(run_id, item_id)
        if (
            item["state"] != "awaiting_decision"
            or item["evidence_digest"] != expected_evidence_digest
        ):
            raise HubError(
                "人工决定绑定的证据已失效。",
                code="mcp_remote_review_evidence_stale",
                status_code=409,
            )
        if decision == "block":
            self.store.set_item(
                item_id,
                state="blocked",
                error_code="mcp_remote_review_operator_blocked",
            )
            self._refresh_catalog_run(run_id)
            return self.store.require_item(
                run_id, item_id, self.tenant_id, self.owner_id
            )
        if (
            not allowed_tools
            or len(set(allowed_tools)) != len(allowed_tools)
            or not set(allowed_tools).issubset(evidence.tool_schema_digests)
            or set(tool_effects) != set(allowed_tools)
            or any(value != "read" for value in tool_effects.values())
        ):
            raise HubError(
                "Catalog 契约只能冻结人工确认的只读工具子集。",
                code="mcp_remote_review_effect_denied",
                status_code=422,
            )
        resolved = self.adapters["catalog_project"].resolve(item["target_id"])
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        if snapshot.source_digest != evidence.snapshot.source_digest:
            raise HubError(
                "Catalog manifest 已漂移。",
                code="mcp_remote_catalog_manifest_drift",
                status_code=409,
            )
        contract = self._draft_catalog_contract(
            resolved=resolved,
            evidence=evidence,
            allowed_tools=allowed_tools,
            tool_effects=tool_effects,
        )
        self.store.set_item(
            item_id,
            state="approved",
            draft_contract=contract.model_dump(mode="json"),
            contract_fingerprint=contract.contract_fingerprint,
        )
        self._event(
            run_id,
            item_id,
            "human_decision",
            safe_to_retry=False,
            payload={"contract_fingerprint": contract.contract_fingerprint},
        )
        self._refresh_catalog_run(run_id)
        return self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )

    def _draft_catalog_contract(
        self,
        *,
        resolved: dict[str, Any],
        evidence: CatalogRemoteEvidenceBundleV1,
        allowed_tools: list[str],
        tool_effects: dict[str, Literal["read"]],
    ) -> CatalogReviewedRemoteContractV1:
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        manifest: CatalogAdapterManifest = resolved["manifest"]
        remote_auth_policy: RemoteAuthPolicyV1 | None = None
        remote_oauth_policy: RemoteOAuthPolicyV2 | None = None
        if snapshot.auth_mode in {"static_bearer", "static_header"}:
            policy, _binding, revision = self._catalog_binding_context(manifest)
            if (
                policy.policy_fingerprint != evidence.auth_policy_fingerprint
                or revision != evidence.auth_revision_digest
            ):
                raise HubError(
                    "Catalog 静态凭据 evidence 已失效。",
                    code="mcp_remote_auth_binding_stale",
                    status_code=409,
                )
            remote_auth_policy = policy
        else:
            metadata, revision = self._catalog_oauth_context(snapshot)
            if (
                metadata.policy_fingerprint != evidence.auth_policy_fingerprint
                or metadata.scope_digest != evidence.authorized_scope_digest
                or revision != evidence.auth_revision_digest
            ):
                raise HubError(
                    "Catalog OAuth evidence 已失效。",
                    code="mcp_remote_oauth_contract_scope_drift",
                    status_code=409,
                )
            discovery = self.oauth.store.active_discovery(
                subject=self.oauth.subject_resolver.resolve(),
                target_type="catalog_project",
                target_id=snapshot.project_id,
            )
            if discovery is None or not isinstance(discovery.policy, RemoteOAuthPolicyV2):
                raise HubError(
                    "Catalog OAuth discovery 已漂移。",
                    code="mcp_remote_oauth_discovery_stale",
                    status_code=409,
                )
            remote_oauth_policy = discovery.policy
        return CatalogReviewedRemoteContractV1(
            contract_id=stable_catalog_contract_id(
                snapshot.project_id,
                snapshot.adapter_version,
                snapshot.remote_url,
            ),
            project_id=snapshot.project_id,
            version=snapshot.adapter_version,
            remote_url=snapshot.remote_url,
            origin=snapshot.origin,
            source_digest=snapshot.source_digest,
            auth_mode=snapshot.auth_mode,
            remote_auth_policy=remote_auth_policy,
            remote_oauth_policy=remote_oauth_policy,
            authorized_scopes=evidence.authorized_scopes,
            authorized_scope_digest=evidence.authorized_scope_digest,
            schema_digest=evidence.schema_digest,
            tool_schema_digests=evidence.tool_schema_digests,
            allowed_tools=allowed_tools,
            tool_effects=tool_effects,
            limits={
                "max_output_bytes": min(manifest.max_output_bytes, 256 * 1024),
                "call_timeout_seconds": min(int(manifest.operation_timeout), 20),
                "max_concurrency": 1,
            },
            evidence_digest=evidence.evidence_digest,
        )

    def publish(
        self, run_id: str, item_id: str, expected_fingerprint: str
    ) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self.hub_review.publish(run_id, item_id, expected_fingerprint)
        self._require_catalog_run_active(run_id)
        if not _flag("MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED") or not self.signing_key:
            raise HubError(
                "本机契约发布当前不可用。",
                code="hub_local_contract_publish_disabled",
                status_code=503,
            )
        item, evidence = self._catalog_item_evidence(run_id, item_id)
        if item["state"] != "approved":
            raise HubError(
                "Catalog 复核项尚未完成人工决定。",
                code="mcp_remote_review_state_conflict",
                status_code=409,
            )
        contract = CatalogReviewedRemoteContractV1.model_validate(
            item["draft_contract"]
        )
        if (
            contract.contract_fingerprint != expected_fingerprint
            or contract.evidence_digest != evidence.evidence_digest
        ):
            raise HubError(
                "Catalog 契约指纹或 evidence 已失效。",
                code="mcp_remote_review_contract_fingerprint",
                status_code=409,
            )
        resolved = self.adapters["catalog_project"].resolve(item["target_id"])
        if resolved["snapshot"].source_digest != contract.source_digest:
            raise HubError(
                "Catalog manifest 已漂移。",
                code="mcp_remote_catalog_manifest_drift",
                status_code=409,
            )
        current = self._draft_catalog_contract(
            resolved=resolved,
            evidence=evidence,
            allowed_tools=contract.allowed_tools,
            tool_effects=contract.tool_effects,
        )
        if current.contract_fingerprint != contract.contract_fingerprint:
            raise HubError(
                "Catalog 认证 revision 已变化，需要重新复核。",
                code="mcp_remote_review_evidence_stale",
                status_code=409,
            )
        self.store.save_catalog_contract(
            self.tenant_id,
            self.owner_id,
            contract,
            catalog_contract_signature(contract, self.signing_key),
        )
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=contract.project_id
        )
        self.store.delete_runtime_binding(
            self.tenant_id, self.owner_id, target
        )
        self._close_catalog_live(target.target_id)
        state = self.store.set_target_state(
            self.tenant_id,
            self.owner_id,
            target,
            "reviewed",
            contract_fingerprint=contract.contract_fingerprint,
        )
        self.store.set_item(item_id, state="published")
        self._event(
            run_id,
            item_id,
            "contract_publish",
            safe_to_retry=False,
            payload={"contract_fingerprint": contract.contract_fingerprint},
        )
        self._refresh_catalog_run(run_id)
        return {
            "contract": contract.model_dump(mode="json"),
            "activation_eligible": (
                contract_runtime_enabled() and catalog_runtime_enabled()
            ),
            "runtime_tool_count": 0,
            "target_state": state.model_dump(mode="json"),
        }

    @staticmethod
    def _catalog_runtime_tool_name(project_id: str, upstream_name: str) -> str:
        project_slug = re.sub(r"[^a-z0-9]+", "_", project_id.lower()).strip("_")
        project_slug = project_slug[:32] or "project"
        project_hash = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:8]
        tool_slug = re.sub(r"[^a-z0-9]+", "_", upstream_name.lower()).strip("_")
        tool_slug = tool_slug[:32] or "tool"
        tool_hash = hashlib.sha256(upstream_name.encode("utf-8")).hexdigest()[:8]
        return f"catalog__{project_slug}_{project_hash}__{tool_slug}_{tool_hash}"

    async def activate_catalog_runtime(
        self, project_id: str, expected_contract_fingerprint: str
    ) -> dict[str, Any]:
        self._require_enabled()
        self._require_catalog_runtime()
        expected = str(expected_contract_fingerprint or "").strip()
        if not HEX64_RE.fullmatch(expected):
            raise HubError(
                "Catalog 契约指纹无效。",
                code="mcp_remote_runtime_contract_fingerprint_invalid",
                status_code=422,
            )
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=project_id
        )
        lock = self._target_locks.setdefault(
            (target.target_type, target.target_id), asyncio.Lock()
        )
        cleanup: dict[str, bool] = {}
        async with lock:
            state = self.store.get_target_state(
                self.tenant_id, self.owner_id, target
            )
            if state.state not in {"reviewed", "disconnected", "active"}:
                raise HubError(
                    "Catalog 远程目标尚未处于可激活状态。",
                    code="mcp_remote_runtime_activation_precondition",
                    status_code=409,
                )
            resolved, contract, execution = self._resolve_catalog_runtime_contract(
                project_id
            )
            if (
                contract.contract_fingerprint != expected
                or state.contract_fingerprint not in {"", expected}
            ):
                raise HubError(
                    "Catalog 契约指纹已变化。",
                    code="mcp_remote_runtime_contract_fingerprint_mismatch",
                    status_code=409,
                )
            try:
                async with self._catalog_session_unlocked(
                    resolved,
                    authenticated=True,
                    cleanup_observer=cleanup,
                ) as session:
                    tool_schemas, auth_context_digest = (
                        self._validate_catalog_runtime_session(
                            resolved=resolved,
                            contract=contract,
                            session=session,
                        )
                    )
            except HubError as exc:
                self.session_coordinator.invalidate(
                    target,
                    state="drifted",
                    reason_code=exc.code,
                )
                raise
            if not cleanup or not all(cleanup.values()):
                self.session_coordinator.invalidate(
                    target,
                    state="tainted",
                    reason_code="mcp_remote_runtime_cleanup_failed",
                )
                raise HubError(
                    "Catalog 激活预检临时资源清理失败。",
                    code="mcp_remote_runtime_cleanup_failed",
                    status_code=503,
                )
            try:
                binding = self.store.save_runtime_binding(
                    self.tenant_id,
                    self.owner_id,
                    target=target,
                    contract_id=execution.contract_id,
                    contract_fingerprint=execution.contract_fingerprint,
                    source_digest=execution.source_digest,
                    schema_digest=execution.schema_digest,
                    auth_context_digest=auth_context_digest,
                    tool_schemas=tool_schemas,
                )
                self.store.set_target_state(
                    self.tenant_id,
                    self.owner_id,
                    target,
                    "active",
                    contract_fingerprint=execution.contract_fingerprint,
                )
            except Exception as exc:
                self.session_coordinator.invalidate(
                    target,
                    state="disconnected",
                    reason_code="mcp_remote_runtime_activation_persist_failed",
                )
                raise HubError(
                    "Catalog Runtime 激活状态无法持久化。",
                    code="mcp_remote_runtime_activation_persist_failed",
                    status_code=503,
                ) from exc
        summary = self.catalog_remote_summary(project_id)
        summary["runtime_binding_revision"] = binding.revision
        return summary

    async def disconnect_catalog_runtime(self, project_id: str) -> dict[str, Any]:
        self._require_enabled()
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=project_id
        )
        lock = self._target_locks.setdefault(
            (target.target_type, target.target_id), asyncio.Lock()
        )
        async with lock:
            self.adapters["catalog_project"].resolve(project_id)
            self.session_coordinator.invalidate(
                target,
                state="disconnected",
                reason_code="mcp_remote_runtime_disconnected",
            )
            live = self._catalog_live.pop(project_id, None)
            if live is not None:
                await asyncio.gather(
                    self.hub.bridge.close(live[0]),
                    self.hub.bridge.revoke(live[1]),
                    return_exceptions=True,
                )
        return self.catalog_remote_summary(project_id)

    def catalog_runtime_tools(self) -> list[dict[str, Any]]:
        if (
            not review_unification_enabled()
            or not contract_runtime_enabled()
            or not catalog_runtime_enabled()
        ):
            return []
        output: list[dict[str, Any]] = []
        for binding in self.store.list_runtime_bindings(
            self.tenant_id, self.owner_id
        ):
            if binding.target.target_type != "catalog_project":
                continue
            project_id = binding.target.target_id
            try:
                state = self.store.get_target_state(
                    self.tenant_id, self.owner_id, binding.target
                )
                resolved, contract, execution = (
                    self._resolve_catalog_runtime_contract(project_id)
                )
                auth_context_digest = self._catalog_runtime_auth_context(
                    resolved=resolved,
                    contract=contract,
                )
                if (
                    state.state != "active"
                    or state.contract_fingerprint != execution.contract_fingerprint
                    or binding.contract_id != execution.contract_id
                    or binding.contract_fingerprint != execution.contract_fingerprint
                    or binding.source_digest != execution.source_digest
                    or binding.schema_digest != execution.schema_digest
                    or binding.auth_context_digest != auth_context_digest
                ):
                    raise HubError(
                        "Catalog Runtime 激活快照已漂移。",
                        code="mcp_remote_runtime_binding_stale",
                        status_code=409,
                    )
            except (HubError, ValueError):
                self._invalidate_catalog_target(
                    project_id,
                    reason_code="mcp_remote_runtime_binding_stale",
                )
                continue
            for upstream_name in execution.allowed_tools:
                frozen = binding.tool_schemas.get(upstream_name)
                if not isinstance(frozen, dict):
                    continue
                output.append(
                    {
                        "name": self._catalog_runtime_tool_name(
                            project_id, upstream_name
                        ),
                        "description": (
                            "受控 Catalog 远程 MCP 工具；调用前必须逐次审批，"
                            "远程结果可能包含不受信内容。"
                        ),
                        "input_schema": dict(frozen["input_schema"]),
                        "target_type": "catalog_project",
                        "project_id": project_id,
                        "upstream_tool_name": upstream_name,
                        "tool_schema_digest": frozen["schema_digest"],
                        "schema_digest": execution.schema_digest,
                        "origin": execution.origin,
                        "version": execution.version,
                        "source_digest": execution.source_digest,
                        "auth_context_digest": auth_context_digest,
                        "contract_id": execution.contract_id,
                        "contract_fingerprint": execution.contract_fingerprint,
                    }
                )
        return output

    async def execute_catalog_runtime(
        self,
        *,
        project_id: str,
        runtime_tool_name: str,
        upstream_tool_name: str,
        arguments: dict[str, Any],
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_enabled()
        self._require_catalog_runtime()
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=project_id
        )
        args_digest = arguments_digest(arguments)
        approval_id = str(approval.get("approval_id") or "").strip()
        metadata = (
            approval.get("metadata")
            if isinstance(approval.get("metadata"), dict)
            else {}
        )
        remote_meta = (
            metadata.get("remote_approval")
            if isinstance(metadata.get("remote_approval"), dict)
            else {}
        )
        approved_contract_fingerprint = str(
            remote_meta.get("contract_fingerprint") or ""
        )
        if (
            APPROVAL_ID_RE.fullmatch(approval_id) is None
            or approval.get("status") != "decided"
            or approval.get("decision") not in {"approve", "edit"}
            or approval.get("tool_name") != runtime_tool_name
            or remote_meta.get("target_type") != "catalog_project"
            or remote_meta.get("target_id") != project_id
            or remote_meta.get("upstream_tool_name") != upstream_tool_name
            or remote_meta.get("tenant_id") != self.tenant_id
            or remote_meta.get("owner_id") != self.owner_id
            or remote_meta.get("arguments_digest") != args_digest
            or HEX64_RE.fullmatch(approved_contract_fingerprint) is None
        ):
            raise HubError(
                "Catalog 远程 Runtime 审批凭据无效。",
                code="mcp_remote_runtime_approval_invalid",
                status_code=409,
            )
        lock = self._target_locks.setdefault(
            (target.target_type, target.target_id), asyncio.Lock()
        )
        async with lock:
            existing = self.store.runtime_execution(
                approval_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
                target=target,
                contract_fingerprint=approved_contract_fingerprint,
                tool_name=runtime_tool_name,
                args_digest=args_digest,
            )
            if existing is not None:
                state, replay = existing
                if state == "completed" and replay is not None:
                    return replay
                if state == "unknown_outcome":
                    raise HubError(
                        "Catalog 远程调用结果未知，禁止重试旧审批。",
                        code="unknown_outcome",
                        status_code=409,
                    )
                if state == "started":
                    raise HubError(
                        "Catalog 远程审批正在执行。",
                        code="mcp_remote_runtime_execution_in_progress",
                        status_code=409,
                    )
                raise HubError(
                    "Catalog 远程审批已失效。",
                    code="mcp_remote_runtime_execution_state_invalid",
                    status_code=409,
                )
            entry = next(
                (
                    item
                    for item in self.catalog_runtime_tools()
                    if item["project_id"] == project_id
                    and item["name"] == runtime_tool_name
                ),
                None,
            )
            if (
                entry is None
                or entry["upstream_tool_name"] != upstream_tool_name
                or remote_meta.get("version") != entry["version"]
                or remote_meta.get("origin") != entry["origin"]
                or remote_meta.get("source_digest") != entry["source_digest"]
                or remote_meta.get("auth_context_digest")
                != entry["auth_context_digest"]
                or remote_meta.get("schema_digest") != entry["schema_digest"]
                or remote_meta.get("tool_schema_digest")
                != entry["tool_schema_digest"]
                or remote_meta.get("contract_id") != entry["contract_id"]
                or approved_contract_fingerprint
                != entry["contract_fingerprint"]
            ):
                raise HubError(
                    "Catalog 远程 Runtime 审批凭据无效。",
                    code="mcp_remote_runtime_approval_invalid",
                    status_code=409,
                )
            state = self.store.get_target_state(
                self.tenant_id, self.owner_id, target
            )
            binding = self.store.get_runtime_binding(
                self.tenant_id, self.owner_id, target
            )
            resolved, contract, execution = self._resolve_catalog_runtime_contract(
                project_id
            )
            current_auth_context = self._catalog_runtime_auth_context(
                resolved=resolved,
                contract=contract,
            )
            if (
                state.state != "active"
                or binding is None
                or state.contract_fingerprint != execution.contract_fingerprint
                or binding.contract_fingerprint != execution.contract_fingerprint
                or binding.auth_context_digest != current_auth_context
            ):
                self.session_coordinator.invalidate(
                    target,
                    state="drifted",
                    reason_code="mcp_remote_runtime_binding_stale",
                )
                raise HubError(
                    "Catalog Runtime 激活快照已失效。",
                    code="mcp_remote_runtime_binding_stale",
                    status_code=409,
                )
            cleanup: dict[str, bool] = {}
            result: dict[str, Any] | None = None
            started = False
            try:
                async with self._catalog_session_unlocked(
                    resolved,
                    authenticated=True,
                    cleanup_observer=cleanup,
                ) as session:
                    tool_schemas, auth_context_digest = (
                        self._validate_catalog_runtime_session(
                            resolved=resolved,
                            contract=contract,
                            session=session,
                        )
                    )
                    if (
                        auth_context_digest != binding.auth_context_digest
                        or tool_schemas != binding.tool_schemas
                    ):
                        raise HubError(
                            "Catalog Runtime Schema 或认证上下文已漂移。",
                            code="mcp_remote_runtime_binding_stale",
                            status_code=409,
                        )
                    self.store.begin_runtime_execution(
                        approval_id,
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                        target=target,
                        contract_fingerprint=execution.contract_fingerprint,
                        tool_name=runtime_tool_name,
                        args_digest=args_digest,
                    )
                    started = True
                    call_timeout = min(
                        max(
                            int(
                                execution.limits.get("call_timeout_seconds") or 1
                            ),
                            1,
                        ),
                        20,
                    )
                    response = await asyncio.wait_for(
                        self.hub.bridge.call(
                            str(session["session_id"]),
                            upstream_tool_name,
                            arguments,
                        ),
                        timeout=call_timeout,
                    )
                    candidate = response.get("result")
                    max_output = min(
                        int(execution.limits.get("max_output_bytes") or 0),
                        256 * 1024,
                    )
                    if (
                        not isinstance(candidate, dict)
                        or max_output <= 0
                        or len(canonical_json_bytes(candidate)) > max_output
                    ):
                        raise HubError(
                            "Catalog 远程结果结构或大小无效。",
                            code="mcp_remote_runtime_result_denied",
                            status_code=502,
                        )
                    result = candidate
            except HubError as exc:
                if not started:
                    self.session_coordinator.invalidate(
                        target,
                        state="drifted",
                        reason_code=exc.code,
                    )
                    raise
                if exc.code in AUTH_FAILURE_CODES or exc.code in {
                    "mcp_remote_oauth_refresh_required",
                    "mcp_remote_oauth_scope_upgrade_required",
                }:
                    self.store.finish_runtime_execution(
                        approval_id,
                        state="failed",
                        error_code=exc.code,
                    )
                    self.session_coordinator.invalidate(
                        target,
                        state="disconnected",
                        reason_code=exc.code,
                    )
                    raise
                self.store.finish_runtime_execution(
                    approval_id,
                    state="unknown_outcome",
                    error_code="unknown_outcome",
                )
                self.session_coordinator.invalidate(
                    target,
                    state="tainted",
                    reason_code="unknown_outcome",
                )
                raise HubError(
                    "Catalog 远程调用结果未知，临时会话已销毁；禁止重试旧审批。",
                    code="unknown_outcome",
                    status_code=409,
                ) from exc
            except Exception as exc:
                if started:
                    self.store.finish_runtime_execution(
                        approval_id,
                        state="unknown_outcome",
                        error_code="unknown_outcome",
                    )
                    self.session_coordinator.invalidate(
                        target,
                        state="tainted",
                        reason_code="unknown_outcome",
                    )
                    raise HubError(
                        "Catalog 远程调用结果未知，临时会话已销毁；禁止重试旧审批。",
                        code="unknown_outcome",
                        status_code=409,
                    ) from exc
                self.session_coordinator.invalidate(
                    target,
                    state="drifted",
                    reason_code="mcp_remote_runtime_preflight_failed",
                )
                raise HubError(
                    "Catalog 远程调用前置校验失败。",
                    code="mcp_remote_runtime_preflight_failed",
                    status_code=503,
                ) from exc
            if result is None:
                raise HubError(
                    "Catalog 远程调用未返回结果。",
                    code="mcp_remote_runtime_result_denied",
                    status_code=502,
                )
            try:
                self.store.finish_runtime_execution(
                    approval_id,
                    state="completed",
                    result=result,
                )
            except Exception as exc:
                self.session_coordinator.invalidate(
                    target,
                    state="tainted",
                    reason_code="unknown_outcome",
                )
                raise HubError(
                    "Catalog 远程结果已返回，但执行账本未能持久化；禁止重试旧审批。",
                    code="unknown_outcome",
                    status_code=409,
                ) from exc
            if not cleanup or not all(cleanup.values()):
                self.session_coordinator.invalidate(
                    target,
                    state="tainted",
                    reason_code="mcp_remote_runtime_cleanup_failed",
                )
                raise HubError(
                    "Catalog 远程调用已完成，但临时资源清理失败。",
                    code="mcp_remote_runtime_cleanup_failed",
                    status_code=503,
                )
            return result

    def export_contract(self, run_id: str, item_id: str) -> bytes:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self.hub_review.export_contract(run_id, item_id)
        item = self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )
        if item["state"] != "published":
            raise HubError(
                "只有已发布 Catalog 契约可导出。",
                code="mcp_remote_review_state_conflict",
                status_code=409,
            )
        contract = CatalogReviewedRemoteContractV1.model_validate(
            item["draft_contract"]
        )
        return catalog_contract_export(contract)

    def resume(self, run_id: str) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self._decorate_hub_run(self.hub_review.resume(run_id))
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if run["status"] not in {"failed", "interrupted"}:
            raise HubError(
                "统一复核批次当前不可恢复。",
                code="mcp_remote_review_state_conflict",
                status_code=409,
            )
        if any(item["state"] == "unknown_outcome" for item in run["items"]):
            raise HubError(
                "结果未知的代表调用禁止恢复。",
                code="unknown_outcome",
                status_code=409,
            )
        self.store.set_run(run_id, status="queued", cancel_requested=0, error_code="")
        self._schedule(run_id)
        return self.store.require_run(run_id, self.tenant_id, self.owner_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self._decorate_hub_run(self.hub_review.cancel(run_id))
        with self._run_creation_lock:
            run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
            self.store.set_run(run_id, cancel_requested=1, status="cancelled")
            for item in run["items"]:
                if item["state"] in {
                    "queued",
                    "running",
                    "awaiting_call_approval",
                    "awaiting_decision",
                    "approved",
                }:
                    self.store.set_item(
                        item["item_id"],
                        state="cancelled",
                        error_code="mcp_remote_review_cancelled",
                    )
                    proposal = item.get("proposal")
                    if proposal and proposal.get("state") == "proposed":
                        self.store.finish_call(
                            str(proposal["proposal_id"]),
                            state="cancelled",
                            error_code="mcp_remote_review_cancelled",
                        )
        return self.store.require_run(run_id, self.tenant_id, self.owner_id)

    def contracts(self) -> list[dict[str, Any]]:
        self._require_enabled()
        hub = [
            {**item, "target_type": "hub_candidate"}
            for item in self.hub_review.contracts.describe()
        ]
        return [*hub, *self.catalog_contracts.describe()]

    async def revoke_contract(self, contract_id: str, reason: str) -> dict[str, Any]:
        self._require_enabled()
        if contract_id.startswith("hubct_"):
            return await self.hub_review.revoke(contract_id, reason)
        contract, error = self.catalog_contracts.get(contract_id)
        if contract is None or error:
            raise HubError(
                "Catalog 远程契约不存在或已撤销。",
                code=error or "mcp_remote_contract_not_found",
                status_code=404 if not error else 409,
            )
        self.store.revoke_contract(
            self.tenant_id,
            self.owner_id,
            "catalog_project",
            contract_id,
            reason,
        )
        target = RemoteTargetRefV1(
            target_type="catalog_project", target_id=contract.project_id
        )
        state = self.session_coordinator.invalidate(
            target,
            state="revoked",
            reason_code="mcp_remote_contract_revoked",
        )
        return {
            "contract_id": contract_id,
            "revoked": True,
            "runtime_tool_count": 0,
            "target_state": state.model_dump(mode="json"),
        }

    def catalog_remote_summary(self, project_id: str) -> dict[str, Any]:
        self._require_enabled()
        resolved = self.adapters["catalog_project"].resolve(project_id)
        manifest: CatalogAdapterManifest = resolved["manifest"]
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        state = self.store.get_target_state(
            self.tenant_id,
            self.owner_id,
            RemoteTargetRefV1(
                target_type="catalog_project", target_id=project_id
            ),
        )
        oauth_summary: dict[str, Any] | None = None
        if snapshot.auth_mode == "oauth_authorization_code_pkce":
            try:
                oauth_summary = self.oauth.summary(
                    target_type="catalog_project",
                    target_id=project_id,
                    source_digest=snapshot.source_digest,
                )
                discovery_summary = oauth_summary.get("discovery")
                if isinstance(discovery_summary, dict):
                    oauth_summary["scope_assessment"] = assess_oauth_scopes(
                        tuple(discovery_summary.get("recommended_scopes") or ())
                    )
            except RemoteOAuthError as exc:
                oauth_summary = {
                    "error_code": exc.code,
                    "discovery": None,
                    "registration": None,
                    "authorization_session": None,
                    "token": None,
                }
        contract, contract_error = self.catalog_contracts.lookup_project(
            project_id, manifest.adapter_version, snapshot.remote_url
        )
        if state.state in {"reviewed", "active"} and contract_error:
            state = self.session_coordinator.invalidate(
                RemoteTargetRefV1(
                    target_type="catalog_project", target_id=project_id
                ),
                state=(
                    "revoked"
                    if contract_error == "mcp_remote_contract_revoked"
                    else "drifted"
                ),
                reason_code=contract_error,
            )
        binding = self.store.get_runtime_binding(
            self.tenant_id,
            self.owner_id,
            RemoteTargetRefV1(
                target_type="catalog_project", target_id=project_id
            ),
        )
        if state.state == "active":
            try:
                if contract is None or contract_error or binding is None:
                    raise HubError(
                        "Catalog Runtime 激活快照不存在。",
                        code="mcp_remote_runtime_binding_stale",
                        status_code=409,
                    )
                auth_context_digest = self._catalog_runtime_auth_context(
                    resolved=resolved,
                    contract=contract,
                )
                if (
                    binding.contract_fingerprint != contract.contract_fingerprint
                    or binding.source_digest != contract.source_digest
                    or binding.schema_digest != contract.schema_digest
                    or binding.auth_context_digest != auth_context_digest
                ):
                    raise HubError(
                        "Catalog Runtime 激活快照已漂移。",
                        code="mcp_remote_runtime_binding_stale",
                        status_code=409,
                    )
            except (HubError, ValueError) as exc:
                self._invalidate_catalog_target(
                    project_id,
                    reason_code=(
                        exc.code
                        if isinstance(exc, HubError)
                        else "mcp_remote_runtime_binding_stale"
                    ),
                )
                state = self.store.get_target_state(
                    self.tenant_id,
                    self.owner_id,
                    RemoteTargetRefV1(
                        target_type="catalog_project", target_id=project_id
                    ),
                )
                binding = None
        runtime_tool_count = 0
        if binding is not None and state.state == "active":
            runtime_tool_count = len(binding.tool_schemas)
        activation_eligible = bool(
            contract is not None
            and not contract_error
            and state.state in {"reviewed", "disconnected", "active"}
            and contract_runtime_enabled()
            and catalog_runtime_enabled()
        )
        return {
            "project_id": project_id,
            "origin": snapshot.origin,
            "version": snapshot.adapter_version,
            "transport": snapshot.transport,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "auth_mode": snapshot.auth_mode,
            "source_digest": snapshot.source_digest,
            "target_state": state.model_dump(mode="json"),
            "oauth": oauth_summary,
            "reviewed_contract": (
                {
                    "contract_id": contract.contract_id,
                    "contract_fingerprint": contract.contract_fingerprint,
                }
                if contract is not None and not contract_error
                else None
            ),
            "contract_error": contract_error,
            "activation_eligible": activation_eligible,
            "runtime_tool_count": runtime_tool_count,
            "runtime_enabled": contract_runtime_enabled(),
            "catalog_runtime_enabled": catalog_runtime_enabled(),
            "credential_binding_ready": (
                self.catalog._remote_review_credential_ready(manifest)
                if snapshot.auth_mode in {"static_bearer", "static_header"}
                else False
            ),
            "catalog_oauth_enabled": catalog_oauth_enabled(),
            "local_single_owner_warning": True,
        }

    def _catalog_oauth_target(
        self, project_id: str
    ) -> tuple[CatalogAdapterManifest, CatalogRemoteSnapshotV1]:
        self._require_enabled()
        self._require_catalog_oauth()
        resolved = self.adapters["catalog_project"].resolve(project_id)
        snapshot: CatalogRemoteSnapshotV1 = resolved["snapshot"]
        if snapshot.auth_mode != "oauth_authorization_code_pkce":
            raise HubError(
                "目录项目不是固定 OAuth 远程 MCP。",
                code="mcp_remote_catalog_oauth_ineligible",
                status_code=409,
            )
        return resolved["manifest"], snapshot

    def _require_catalog_oauth_scopes_safe(
        self, snapshot: CatalogRemoteSnapshotV1
    ) -> dict[str, Any]:
        try:
            summary = self.oauth.summary(
                target_type="catalog_project",
                target_id=snapshot.project_id,
                source_digest=snapshot.source_digest,
            )
        except RemoteOAuthError as exc:
            raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None
        discovery = summary.get("discovery")
        if not isinstance(discovery, dict):
            raise HubError(
                "Catalog OAuth discovery 已过期或发生漂移。",
                code="mcp_remote_oauth_discovery_stale",
                status_code=409,
            )
        assessment = assess_oauth_scopes(
            tuple(discovery.get("recommended_scopes") or ())
        )
        if assessment["dangerous_scopes"]:
            raise HubError(
                "OAuth Scope 含高危写入或控制语义，本轮拒绝授权与复核。",
                code="mcp_remote_oauth_high_risk_scope_denied",
                status_code=409,
            )
        return assessment

    async def catalog_oauth_discover(self, project_id: str) -> dict[str, Any]:
        _manifest, snapshot = self._catalog_oauth_target(project_id)
        lock = self._target_locks.setdefault(
            ("catalog_project", project_id), asyncio.Lock()
        )
        async with lock:
            try:
                await self.oauth.discover(
                    target_type="catalog_project",
                    target_id=project_id,
                    resource_url=snapshot.remote_url,
                    source_digest=snapshot.source_digest,
                    # The fixed Catalog manifest is the OAuth trust root. Some
                    # MCP servers expose an anonymous tool subset and publish
                    # same-origin RFC 9728 metadata without returning a 401
                    # challenge; discovery must preserve that authenticated vs
                    # unauthenticated diff instead of rejecting it up front.
                    require_bearer_challenge=False,
                )
            except RemoteOAuthError as exc:
                raise HubError(
                    str(exc), code=exc.code, status_code=exc.status_code
                ) from None
        return self.catalog_remote_summary(project_id)

    async def catalog_oauth_register(
        self, project_id: str, expected_discovery_fingerprint: str
    ) -> dict[str, Any]:
        manifest, snapshot = self._catalog_oauth_target(project_id)
        lock = self._target_locks.setdefault(
            ("catalog_project", project_id), asyncio.Lock()
        )
        async with lock:
            self._require_catalog_oauth_scopes_safe(snapshot)
            try:
                await self.oauth.register_client(
                    target_type="catalog_project",
                    target_id=project_id,
                    source_digest=snapshot.source_digest,
                    expected_discovery_fingerprint=expected_discovery_fingerprint,
                    mode=manifest.remote_oauth_registration_mode,
                    client_id=manifest.remote_oauth_client_id,
                )
            except RemoteOAuthError as exc:
                raise HubError(
                    str(exc), code=exc.code, status_code=exc.status_code
                ) from None
        return self.catalog_remote_summary(project_id)

    async def catalog_oauth_authorize(
        self,
        project_id: str,
        *,
        expected_discovery_fingerprint: str,
        expected_registration_digest: str,
        expected_scope_digest: str,
        request_refresh_token: bool,
    ) -> dict[str, Any]:
        _manifest, snapshot = self._catalog_oauth_target(project_id)
        lock = self._target_locks.setdefault(
            ("catalog_project", project_id), asyncio.Lock()
        )
        async with lock:
            self._require_catalog_oauth_scopes_safe(snapshot)
            try:
                return self.authorization.create_authorization(
                    target_type="catalog_project",
                    target_id=project_id,
                    source_digest=snapshot.source_digest,
                    expected_discovery_fingerprint=expected_discovery_fingerprint,
                    expected_registration_digest=expected_registration_digest,
                    expected_scope_digest=expected_scope_digest,
                    request_refresh_token=request_refresh_token,
                )
            except RemoteOAuthError as exc:
                raise HubError(
                    str(exc), code=exc.code, status_code=exc.status_code
                ) from None

    async def catalog_oauth_refresh(
        self, project_id: str, token_id: str, expected_revision: int
    ) -> dict[str, Any]:
        self._catalog_oauth_target(project_id)
        lock = self._target_locks.setdefault(
            ("catalog_project", project_id), asyncio.Lock()
        )
        async with lock:
            try:
                await self.authorization.refresh(
                    target_type="catalog_project",
                    target_id=project_id,
                    token_id=token_id,
                    expected_revision=expected_revision,
                )
            except RemoteOAuthError as exc:
                raise HubError(
                    str(exc), code=exc.code, status_code=exc.status_code
                ) from None
            self.session_coordinator.invalidate(
                RemoteTargetRefV1(
                    target_type="catalog_project", target_id=project_id
                ),
                state="drifted",
                reason_code="mcp_remote_oauth_token_revision_changed",
            )
        return self.catalog_remote_summary(project_id)

    async def catalog_oauth_revoke(
        self, project_id: str, token_id: str
    ) -> dict[str, Any]:
        self._catalog_oauth_target(project_id)
        lock = self._target_locks.setdefault(
            ("catalog_project", project_id), asyncio.Lock()
        )
        async with lock:
            try:
                revocation = await self.authorization.revoke_with_remote(
                    target_type="catalog_project",
                    target_id=project_id,
                    token_id=token_id,
                )
            except RemoteOAuthError as exc:
                raise HubError(
                    str(exc), code=exc.code, status_code=exc.status_code
                ) from None
            state = self.session_coordinator.invalidate(
                RemoteTargetRefV1(
                    target_type="catalog_project", target_id=project_id
                ),
                state="revoked",
                reason_code="mcp_remote_oauth_token_revoked",
            )
        return {
            "project_id": project_id,
            "local_revocation": revocation["local_revocation"],
            "remote_revocation": revocation["remote_revocation"],
            "remote_error_code": revocation["remote_error_code"],
            "target_state": state.model_dump(mode="json"),
        }

    def generate_proposal(self, run_id: str, item_id: str) -> dict[str, Any]:
        self._require_enabled()
        if run_id.startswith("hubreview_"):
            return self.hub_review.generate_proposal(run_id, item_id)
        self._require_catalog_run_active(run_id)
        item = self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )
        if item["state"] != "awaiting_call_approval" or item.get("proposal") is None:
            raise HubError(
                "复核项当前没有可批准的代表调用提案。",
                code="manual_call_unavailable",
                status_code=409,
            )
        return item["proposal"]


class RemoteReviewRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[RemoteTargetRefV1] = Field(min_length=1, max_length=MAX_REVIEW_ITEMS)


class RemoteProposalApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "block"]
    expected_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    tool_effects: dict[str, Literal["read"]] = Field(default_factory=dict)
    acknowledge_unknown_oauth_scopes: bool = False


class RemoteContractPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class CatalogRemoteActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteContractRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class CatalogOAuthRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class CatalogOAuthAuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_registration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_refresh_token: bool = False


class CatalogOAuthRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


router = APIRouter(tags=["mcp-remote-review"])
_remote_review_service: MCPRemoteReviewService | None = None


def configure_mcp_remote_review(service: MCPRemoteReviewService) -> None:
    global _remote_review_service
    _remote_review_service = service


def _service() -> MCPRemoteReviewService:
    if _remote_review_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "mcp_remote_review_unconfigured",
                "error": "MCP 远程统一复核尚未配置。",
            },
        )
    return _remote_review_service


def _raise_http(exc: HubError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "error": str(exc)},
    ) from exc


def _clean_id(value: str, pattern: re.Pattern[str], field: str) -> str:
    clean = str(value or "").strip()
    if not pattern.fullmatch(clean):
        raise HubError(
            f"{field} 无效。", code="mcp_remote_review_identifier_invalid", status_code=422
        )
    return clean


def _clean_run_id(value: str) -> str:
    clean = str(value or "").strip()
    if not (REMOTE_RUN_ID_RE.fullmatch(clean) or HUB_RUN_ID_RE.fullmatch(clean)):
        raise HubError(
            "run_id 无效。",
            code="mcp_remote_review_identifier_invalid",
            status_code=422,
        )
    return clean


def _clean_item_id(value: str, *, hub: bool) -> str:
    return _clean_id(
        value,
        HUB_ITEM_ID_RE if hub else REMOTE_ITEM_ID_RE,
        "item_id",
    )


def _clean_proposal_id(value: str, *, hub: bool) -> str:
    return _clean_id(
        value,
        HUB_PROPOSAL_ID_RE if hub else REMOTE_PROPOSAL_ID_RE,
        "proposal_id",
    )


@router.get("/api/mcp/remote/status")
async def remote_review_status() -> dict[str, Any]:
    return _service().status()


@router.post("/api/mcp/remote/review-runs", status_code=201)
async def create_remote_review_run(
    payload: RemoteReviewRunCreateRequest,
) -> dict[str, Any]:
    try:
        return _service().create_run(payload.items)
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/remote/review-runs")
async def list_remote_review_runs() -> dict[str, Any]:
    try:
        items = _service().list_runs()
        return {"items": items, "total": len(items)}
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/remote/review-runs/{run_id}")
async def get_remote_review_run(run_id: str) -> dict[str, Any]:
    try:
        return _service().get_run(_clean_run_id(run_id))
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/remote/review-runs/{run_id}/resume")
async def resume_remote_review_run(run_id: str) -> dict[str, Any]:
    try:
        return _service().resume(_clean_run_id(run_id))
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/remote/review-runs/{run_id}/cancel")
async def cancel_remote_review_run(run_id: str) -> dict[str, Any]:
    try:
        return _service().cancel(_clean_run_id(run_id))
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/remote/review-runs/{run_id}/items/{item_id}/call-proposals"
)
async def create_remote_call_proposal(
    run_id: str, item_id: str, request: Request
) -> dict[str, Any]:
    try:
        clean_run = _clean_run_id(run_id)
        clean_item = _clean_item_id(
            item_id, hub=bool(HUB_RUN_ID_RE.fullmatch(clean_run))
        )
        if (await request.body()).strip():
            raise HubError(
                "代表调用提案不接受客户端参数。",
                code="mcp_remote_review_arbitrary_arguments_denied",
                status_code=422,
            )
        return _service().generate_proposal(clean_run, clean_item)
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/remote/review-runs/{run_id}/items/{item_id}/call-proposals/{proposal_id}/approve"
)
async def approve_remote_call_proposal(
    run_id: str,
    item_id: str,
    proposal_id: str,
    payload: RemoteProposalApproveRequest,
) -> dict[str, Any]:
    try:
        clean_run = _clean_run_id(run_id)
        is_hub = bool(HUB_RUN_ID_RE.fullmatch(clean_run))
        return await _service().approve_proposal(
            clean_run,
            _clean_item_id(item_id, hub=is_hub),
            _clean_proposal_id(proposal_id, hub=is_hub),
            payload.expected_proposal_digest,
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/remote/review-runs/{run_id}/items/{item_id}/decision")
async def decide_remote_review_item(
    run_id: str, item_id: str, payload: RemoteDecisionRequest
) -> dict[str, Any]:
    try:
        clean_run = _clean_run_id(run_id)
        return _service().decide(
            clean_run,
            _clean_item_id(
                item_id, hub=bool(HUB_RUN_ID_RE.fullmatch(clean_run))
            ),
            decision=payload.decision,
            expected_evidence_digest=payload.expected_evidence_digest,
            allowed_tools=payload.allowed_tools,
            tool_effects=dict(payload.tool_effects),
            acknowledge_unknown_oauth_scopes=(
                payload.acknowledge_unknown_oauth_scopes
            ),
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/remote/review-runs/{run_id}/items/{item_id}/publish")
async def publish_remote_review_contract(
    run_id: str, item_id: str, payload: RemoteContractPublishRequest
) -> dict[str, Any]:
    try:
        clean_run = _clean_run_id(run_id)
        return _service().publish(
            clean_run,
            _clean_item_id(
                item_id, hub=bool(HUB_RUN_ID_RE.fullmatch(clean_run))
            ),
            payload.expected_contract_fingerprint,
        )
    except HubError as exc:
        _raise_http(exc)


@router.get(
    "/api/mcp/remote/review-runs/{run_id}/items/{item_id}/contract-export"
)
async def export_remote_review_contract(run_id: str, item_id: str) -> Response:
    try:
        clean_run = _clean_run_id(run_id)
        content = _service().export_contract(
            clean_run,
            _clean_item_id(
                item_id, hub=bool(HUB_RUN_ID_RE.fullmatch(clean_run))
            ),
        )
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="reviewed-remote-contract.json"'
            },
        )
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/remote/contracts")
async def list_remote_contracts() -> dict[str, Any]:
    try:
        items = _service().contracts()
        return {"items": items, "total": len(items)}
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/remote/contracts/{contract_id}/revoke")
async def revoke_remote_contract(
    contract_id: str, payload: RemoteContractRevokeRequest
) -> dict[str, Any]:
    try:
        clean = str(contract_id or "").strip()
        if not (
            CATALOG_CONTRACT_ID_RE.fullmatch(clean)
            or HUB_CONTRACT_ID_RE.fullmatch(clean)
        ):
            raise HubError(
                "contract_id 无效。",
                code="mcp_remote_review_identifier_invalid",
                status_code=422,
            )
        return await _service().revoke_contract(clean, payload.reason)
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/catalog/{project_id}/remote")
async def get_catalog_remote_status(project_id: str) -> dict[str, Any]:
    try:
        return _service().catalog_remote_summary(
            _clean_id(project_id, PROJECT_ID_RE, "project_id")
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/catalog/{project_id}/remote/activate")
async def activate_catalog_remote_runtime(
    project_id: str, payload: CatalogRemoteActivateRequest
) -> dict[str, Any]:
    try:
        return await _service().activate_catalog_runtime(
            _clean_id(project_id, PROJECT_ID_RE, "project_id"),
            payload.expected_contract_fingerprint,
        )
    except HubError as exc:
        _raise_http(exc)


@router.delete("/api/mcp/catalog/{project_id}/remote/session")
async def disconnect_catalog_remote_runtime(
    project_id: str, request: Request
) -> dict[str, Any]:
    try:
        if request.query_params or (await request.body()).strip():
            raise HubError(
                "Catalog 远程断开不接受客户端字段。",
                code="mcp_remote_catalog_client_fields_denied",
                status_code=422,
            )
        return await _service().disconnect_catalog_runtime(
            _clean_id(project_id, PROJECT_ID_RE, "project_id")
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/catalog/{project_id}/remote/oauth/discover")
async def discover_catalog_remote_oauth(
    project_id: str, request: Request
) -> dict[str, Any]:
    try:
        if (await request.body()).strip():
            raise HubError(
                "OAuth 发现不接受客户端 URL、Header 或 Scope。",
                code="mcp_remote_catalog_oauth_client_fields_denied",
                status_code=422,
            )
        return await _service().catalog_oauth_discover(
            _clean_id(project_id, PROJECT_ID_RE, "project_id")
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/catalog/{project_id}/remote/oauth/register")
async def register_catalog_remote_oauth(
    project_id: str, payload: CatalogOAuthRegisterRequest
) -> dict[str, Any]:
    try:
        return await _service().catalog_oauth_register(
            _clean_id(project_id, PROJECT_ID_RE, "project_id"),
            payload.expected_discovery_fingerprint,
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/catalog/{project_id}/remote/oauth/authorize")
async def authorize_catalog_remote_oauth(
    project_id: str, payload: CatalogOAuthAuthorizeRequest
) -> dict[str, Any]:
    try:
        return await _service().catalog_oauth_authorize(
            _clean_id(project_id, PROJECT_ID_RE, "project_id"),
            expected_discovery_fingerprint=payload.expected_discovery_fingerprint,
            expected_registration_digest=payload.expected_registration_digest,
            expected_scope_digest=payload.expected_scope_digest,
            request_refresh_token=payload.request_refresh_token,
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/catalog/{project_id}/remote/oauth/tokens/{token_id}/refresh")
async def refresh_catalog_remote_oauth(
    project_id: str, token_id: str, payload: CatalogOAuthRefreshRequest
) -> dict[str, Any]:
    try:
        return await _service().catalog_oauth_refresh(
            _clean_id(project_id, PROJECT_ID_RE, "project_id"),
            _clean_id(token_id, OAUTH_TOKEN_ID_RE, "token_id"),
            payload.expected_revision,
        )
    except HubError as exc:
        _raise_http(exc)


@router.delete("/api/mcp/catalog/{project_id}/remote/oauth/tokens/{token_id}")
async def revoke_catalog_remote_oauth(
    project_id: str, token_id: str, request: Request
) -> dict[str, Any]:
    try:
        if (await request.body()).strip():
            raise HubError(
                "OAuth 撤销不接受客户端字段。",
                code="mcp_remote_catalog_oauth_client_fields_denied",
                status_code=422,
            )
        return await _service().catalog_oauth_revoke(
            _clean_id(project_id, PROJECT_ID_RE, "project_id"),
            _clean_id(token_id, OAUTH_TOKEN_ID_RE, "token_id"),
        )
    except HubError as exc:
        _raise_http(exc)
