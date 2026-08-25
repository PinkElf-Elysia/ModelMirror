"""Versioned, fail-closed execution contracts for reviewed MCP Hub servers."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .remote_auth import RemoteAuthPolicyV1
from .remote_oauth import MCP_PROTOCOL_VERSION, RemoteOAuthPolicyV2


SOP_VERSION = "anonymous_https_tools_v1"
CONTRACT_SCHEMA_VERSION = "hub-reviewed-contract-v1"
SNAPSHOT_SCHEMA_VERSION = "hub-candidate-snapshot-v1"
EVIDENCE_SCHEMA_VERSION = "hub-evidence-bundle-v1"
STATIC_TOKEN_SOP_VERSION = "static_token_https_tools_v1"
CONTRACT_SCHEMA_VERSION_V2 = "hub-reviewed-contract-v2"
EVIDENCE_SCHEMA_VERSION_V2 = "hub-evidence-bundle-v2"
OAUTH_SOP_VERSION = "oauth_https_tools_v1"
CONTRACT_SCHEMA_VERSION_V3 = "hub-reviewed-contract-v3"
EVIDENCE_SCHEMA_VERSION_V3 = "hub-evidence-bundle-v3"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CONTRACT_ID_RE = re.compile(r"^hubct_[0-9a-f]{32}$")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_contract_id(server_name: str, version: str, remote_url: str) -> str:
    identity = {
        "server_name": str(server_name).strip(),
        "version": str(version).strip(),
        "remote_url": str(remote_url).strip(),
    }
    return "hubct_" + canonical_digest(identity)[:32]


class HubCandidateSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SNAPSHOT_SCHEMA_VERSION] = SNAPSHOT_SCHEMA_VERSION
    server_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    remote_id: str = Field(min_length=1, max_length=40)
    remote_url: str = Field(min_length=1, max_length=4096)
    origin: str = Field(min_length=1, max_length=512)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport: Literal["streamable-http"] = "streamable-http"
    publisher: str = Field(default="", max_length=500)
    registry_status: str = Field(default="active", max_length=40)

    @property
    def snapshot_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class HubEvidenceBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[EVIDENCE_SCHEMA_VERSION] = EVIDENCE_SCHEMA_VERSION
    sop_version: Literal[SOP_VERSION] = SOP_VERSION
    snapshot: HubCandidateSnapshotV1
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    schema_digest: str = Field(default="", max_length=64)
    tool_schema_digests: dict[str, str] = Field(default_factory=dict)
    effect_proposals: dict[str, str] = Field(default_factory=dict)
    representative_call: dict[str, Any] = Field(default_factory=dict)
    cleanup: dict[str, Any] = Field(default_factory=dict)
    fixed_errors: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_digests(self) -> "HubEvidenceBundleV1":
        if self.schema_digest and not HEX64_RE.fullmatch(self.schema_digest):
            raise ValueError("schema_digest must be empty or sha256")
        if any(not HEX64_RE.fullmatch(value) for value in self.tool_schema_digests.values()):
            raise ValueError("tool schema digest must be sha256")
        if set(self.effect_proposals) != set(self.tool_schema_digests):
            raise ValueError("effect proposals must cover every frozen tool")
        return self

    @property
    def evidence_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class HubEvidenceBundleV2(HubEvidenceBundleV1):
    """Secret-free evidence for the static-token SOP."""

    schema_version: Literal[EVIDENCE_SCHEMA_VERSION_V2] = EVIDENCE_SCHEMA_VERSION_V2
    sop_version: Literal[STATIC_TOKEN_SOP_VERSION] = STATIC_TOKEN_SOP_VERSION
    auth_mode: Literal["static_bearer", "static_header"]
    header_name: str = Field(min_length=1, max_length=64)
    auth_policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class HubEvidenceBundleV3(HubEvidenceBundleV1):
    """Secret-free evidence for resource-bound OAuth review."""

    schema_version: Literal[EVIDENCE_SCHEMA_VERSION_V3] = EVIDENCE_SCHEMA_VERSION_V3
    sop_version: Literal[OAUTH_SOP_VERSION] = OAUTH_SOP_VERSION
    auth_mode: Literal["oauth_authorization_code_pkce"] = (
        "oauth_authorization_code_pkce"
    )
    oauth_policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_source: str = Field(min_length=1, max_length=64)
    authorized_scopes: tuple[str, ...]
    authorized_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_version: Literal[MCP_PROTOCOL_VERSION] = MCP_PROTOCOL_VERSION
    scope_assessment: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_oauth_evidence(self) -> "HubEvidenceBundleV3":
        if canonical_digest(list(self.authorized_scopes)) != self.authorized_scope_digest:
            raise ValueError("authorized OAuth scope digest mismatch")
        return self


class HubReviewedContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[CONTRACT_SCHEMA_VERSION] = CONTRACT_SCHEMA_VERSION
    sop_version: Literal[SOP_VERSION] = SOP_VERSION
    contract_id: str = Field(pattern=r"^hubct_[0-9a-f]{32}$")
    server_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    remote_url: str = Field(min_length=1, max_length=4096)
    origin: str = Field(min_length=1, max_length=512)
    source_digest: str = Field(default="", max_length=64)
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_digests: dict[str, str]
    allowed_tools: list[str]
    tool_effects: dict[str, Literal["read"]]
    limits: dict[str, int]
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_fingerprint: str = Field(default="", max_length=64)
    published_at: float = 0.0
    display_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_contract(self) -> "HubReviewedContractV1":
        expected_id = stable_contract_id(self.server_name, self.version, self.remote_url)
        if self.contract_id != expected_id:
            raise ValueError("contract_id does not match normalized identity")
        if self.source_digest and not HEX64_RE.fullmatch(self.source_digest):
            raise ValueError("source_digest must be empty or sha256")
        if not self.tool_schema_digests:
            raise ValueError("tool_schema_digests must not be empty")
        if any(not HEX64_RE.fullmatch(value) for value in self.tool_schema_digests.values()):
            raise ValueError("tool schema digest must be sha256")
        if not self.allowed_tools or len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools must be a non-empty unique subset")
        if not set(self.allowed_tools).issubset(self.tool_schema_digests):
            raise ValueError("allowed_tools must be frozen tools")
        if set(self.tool_effects) != set(self.allowed_tools):
            raise ValueError("tool_effects must cover the allowed subset")
        expected = contract_fingerprint(self)
        if self.contract_fingerprint and self.contract_fingerprint != expected:
            raise ValueError("contract_fingerprint does not match execution fields")
        object.__setattr__(self, "contract_fingerprint", expected)
        return self

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.server_name, self.version, self.remote_url


class HubReviewedContractV2(HubReviewedContractV1):
    schema_version: Literal[CONTRACT_SCHEMA_VERSION_V2] = CONTRACT_SCHEMA_VERSION_V2
    sop_version: Literal[STATIC_TOKEN_SOP_VERSION] = STATIC_TOKEN_SOP_VERSION
    remote_auth_policy: RemoteAuthPolicyV1


class HubReviewedContractV3(HubReviewedContractV1):
    schema_version: Literal[CONTRACT_SCHEMA_VERSION_V3] = CONTRACT_SCHEMA_VERSION_V3
    sop_version: Literal[OAUTH_SOP_VERSION] = OAUTH_SOP_VERSION
    remote_oauth_policy: RemoteOAuthPolicyV2
    authorized_scopes: tuple[str, ...]
    authorized_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_version: Literal[MCP_PROTOCOL_VERSION] = MCP_PROTOCOL_VERSION

    @model_validator(mode="after")
    def validate_oauth_contract(self) -> "HubReviewedContractV3":
        if canonical_digest(list(self.authorized_scopes)) != self.authorized_scope_digest:
            raise ValueError("authorized OAuth scope digest mismatch")
        if self.remote_oauth_policy.protocol_version != self.protocol_version:
            raise ValueError("OAuth protocol version mismatch")
        return self


HubReviewedContract: TypeAlias = (
    HubReviewedContractV1 | HubReviewedContractV2 | HubReviewedContractV3
)
HubEvidenceBundle: TypeAlias = (
    HubEvidenceBundleV1 | HubEvidenceBundleV2 | HubEvidenceBundleV3
)


def contract_execution_fields(contract: HubReviewedContract | dict[str, Any]) -> dict[str, Any]:
    payload = (
        contract.model_dump(mode="json")
        if isinstance(
            contract,
            (HubReviewedContractV1, HubReviewedContractV2, HubReviewedContractV3),
        )
        else dict(contract)
    )
    payload.pop("contract_fingerprint", None)
    # Evidence is signed audit provenance for a revision, not an execution
    # permission. Re-running the same frozen contract may legitimately produce
    # a different read result and therefore a different evidence digest.
    payload.pop("evidence_digest", None)
    payload.pop("published_at", None)
    payload.pop("display_note", None)
    return payload


def contract_fingerprint(contract: HubReviewedContract | dict[str, Any]) -> str:
    return canonical_digest(contract_execution_fields(contract))


def normalize_contract(payload: dict[str, Any]) -> HubReviewedContract:
    if payload.get("schema_version") == CONTRACT_SCHEMA_VERSION_V3:
        return HubReviewedContractV3.model_validate(payload)
    if payload.get("schema_version") == CONTRACT_SCHEMA_VERSION_V2:
        return HubReviewedContractV2.model_validate(payload)
    return HubReviewedContractV1.model_validate(payload)


def contract_export(contract: HubReviewedContract) -> bytes:
    return canonical_json_bytes(contract.model_dump(mode="json")) + b"\n"


def contract_signature(contract: HubReviewedContract, signing_key: str) -> str:
    key = str(signing_key or "").encode("utf-8")
    return hmac.new(key, contract_export(contract).rstrip(b"\n"), hashlib.sha256).hexdigest()


class HubContractRegistry:
    """Loads repository contracts plus valid local signed revisions.

    A duplicate identity with different execution fingerprints is never resolved
    by precedence. It becomes a collision and the identity is denied.
    """

    def __init__(
        self,
        *,
        local_store: Any | None = None,
        tenant_id: str = "local",
        owner_id: str = "local",
        signing_key: str = "",
        repository_dir: str | Path | None = None,
    ) -> None:
        self.local_store = local_store
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self.signing_key = str(signing_key or "")
        self.repository_dir = Path(repository_dir or Path(__file__).with_name("hub_contracts"))

    def _repository_contracts(self) -> list[HubReviewedContract]:
        contracts: list[HubReviewedContract] = []
        if not self.repository_dir.exists():
            return contracts
        for path in sorted(self.repository_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"invalid Hub contract document: {path.name}")
            contracts.append(normalize_contract(raw))
        return contracts

    def _local_contracts(self) -> list[HubReviewedContract]:
        if self.local_store is None or not self.signing_key:
            return []
        contracts: list[HubReviewedContract] = []
        for row in self.local_store.list_local_contract_revisions(
            self.tenant_id, self.owner_id
        ):
            try:
                raw = json.loads(str(row.get("contract_json") or ""))
                contract = normalize_contract(raw)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            expected = contract_signature(contract, self.signing_key)
            if not hmac.compare_digest(expected, str(row.get("signature") or "")):
                continue
            contracts.append(contract)
        return contracts

    def all(self) -> tuple[list[HubReviewedContract], set[tuple[str, str, str]]]:
        repository = self._repository_contracts()
        local = self._local_contracts()
        by_identity: dict[tuple[str, str, str], HubReviewedContract] = {}
        collisions: set[tuple[str, str, str]] = set()
        for contract in [*repository, *local]:
            current = by_identity.get(contract.identity)
            if current is None:
                by_identity[contract.identity] = contract
            elif current.contract_fingerprint != contract.contract_fingerprint:
                collisions.add(contract.identity)
        return list(by_identity.values()), collisions

    def lookup_identity(
        self, server_name: str, version: str, remote_url: str
    ) -> tuple[HubReviewedContract | None, str]:
        identity = (server_name, version, remote_url)
        contracts, collisions = self.all()
        if identity in collisions:
            return None, "hub_contract_collision"
        contract = next((item for item in contracts if item.identity == identity), None)
        if contract is None:
            return None, "hub_contract_unreviewed"
        if self.local_store is not None and self.local_store.is_contract_revoked(
            self.tenant_id, self.owner_id, contract.contract_id
        ):
            return None, "hub_contract_revoked"
        return contract, ""

    def get_contract(self, contract_id: str) -> tuple[HubReviewedContract | None, str]:
        contracts, collisions = self.all()
        matches = [item for item in contracts if item.contract_id == contract_id]
        if any(item.identity in collisions for item in matches):
            return None, "hub_contract_collision"
        if not matches:
            return None, "hub_contract_not_found"
        contract = matches[0]
        if self.local_store is not None and self.local_store.is_contract_revoked(
            self.tenant_id, self.owner_id, contract.contract_id
        ):
            return contract, "hub_contract_revoked"
        return contract, ""

    def describe(self) -> list[dict[str, Any]]:
        contracts, collisions = self.all()
        repository_fingerprints = {
            item.contract_fingerprint for item in self._repository_contracts()
        }
        result: list[dict[str, Any]] = []
        for contract in sorted(contracts, key=lambda item: item.contract_id):
            revoked = bool(
                self.local_store
                and self.local_store.is_contract_revoked(
                    self.tenant_id, self.owner_id, contract.contract_id
                )
            )
            result.append(
                {
                    **contract.model_dump(mode="json"),
                    "contract_source": (
                        "repository"
                        if contract.contract_fingerprint in repository_fingerprints
                        else "local"
                    ),
                    "collision": contract.identity in collisions,
                    "revoked": revoked,
                }
            )
        return result
