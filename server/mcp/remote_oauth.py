"""OAuth metadata, isolation bridge, and status foundation for remote MCP.

Every network target is derived from a server-owned candidate and validated
metadata. Authorization, token storage, review, runtime, and remote revocation
remain independently gated by their owning services and feature flags.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypeAlias
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .remote_auth import SubjectScopeResolver, SubjectScopeV1


HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
TARGET_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,255}$")
REGISTRATION_ID_RE = re.compile(r"^mcpoauthreg_[0-9a-f]{32}$")
MAX_METADATA_BYTES = 64 * 1024
MAX_SCOPES = 100
MAX_SCOPE_LENGTH = 200
MAX_RECOMMENDED_SCOPES = 20
MCP_PROTOCOL_VERSION = "2025-11-25"
OAUTH_RUNTIME_MIN_TTL_SECONDS = 60
OAuthTargetType = Literal["hub_candidate", "catalog_project"]
OAuthRegistrationMode = Literal[
    "pre_registered", "client_id_metadata_document", "dynamic"
]


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


class RemoteOAuthError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def normalize_oauth_url(value: Any, *, field: str = "OAuth URL") -> str:
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > 4096
        or any(token in raw for token in ("{", "}"))
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw)
    ):
        raise RemoteOAuthError(
            f"{field} 无效。", code="mcp_remote_oauth_metadata_invalid", status_code=422
        )
    parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RemoteOAuthError(
            f"{field} 必须是固定 HTTPS 地址。",
            code="mcp_remote_oauth_metadata_invalid",
            status_code=422,
        )
    raw_host = str(parsed.hostname or "").strip().rstrip(".").lower()
    try:
        host = raw_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RemoteOAuthError(
            f"{field} 主机无效。",
            code="mcp_remote_oauth_metadata_invalid",
            status_code=422,
        ) from exc
    if (
        not host
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
    ):
        raise RemoteOAuthError(
            f"{field} 主机无效。",
            code="mcp_remote_oauth_metadata_invalid",
            status_code=422,
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise RemoteOAuthError(
            f"{field} 不允许 IP 字面量。",
            code="mcp_remote_oauth_metadata_invalid",
            status_code=422,
        )
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise RemoteOAuthError(
            f"{field} 端口无效。",
            code="mcp_remote_oauth_metadata_invalid",
            status_code=422,
        ) from exc
    if port != 443:
        raise RemoteOAuthError(
            f"{field} 仅允许 443 端口。",
            code="mcp_remote_oauth_metadata_invalid",
            status_code=422,
        )
    return f"https://{host}{parsed.path or '/'}"


def _origin(url: str) -> str:
    return f"https://{urlsplit(url).hostname}"


def _protected_resource_well_known_urls(resource_url: str) -> tuple[str, ...]:
    parsed = urlsplit(resource_url)
    origin = _origin(resource_url)
    path = parsed.path if parsed.path != "/" else ""
    values = [f"{origin}/.well-known/oauth-protected-resource{path}"]
    fallback = f"{origin}/.well-known/oauth-protected-resource"
    if fallback not in values:
        values.append(fallback)
    return tuple(values)


def _authorization_server_well_known_urls(issuer: str) -> tuple[str, ...]:
    parsed = urlsplit(issuer)
    origin = _origin(issuer)
    path = parsed.path.rstrip("/")
    return tuple(
        dict.fromkeys(
            (
                f"{origin}/.well-known/oauth-authorization-server{path}",
                f"{origin}/.well-known/openid-configuration{path}",
                f"{issuer.rstrip('/')}/.well-known/openid-configuration",
            )
        )
    )


class RemoteOAuthPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["remote-oauth-policy-v1"] = "remote-oauth-policy-v1"
    mode: Literal["oauth_authorization_code_pkce"] = "oauth_authorization_code_pkce"
    resource_uri: str
    origin: str
    remote_url_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_resource_metadata_url: str
    protected_resource_metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    issuer: str
    authorization_server_metadata_url: str
    authorization_server_metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    revocation_endpoint: str = ""
    client_id_metadata_document_supported: bool = False
    scopes_supported: tuple[str, ...] = ()
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteOAuthPolicyV2(RemoteOAuthPolicyV1):
    """Resource-bound policy used by OAuth review contracts.

    The recommended scope set is server-derived.  It never contains a client
    supplied subset, and ``offline_access`` is represented separately so the
    operator must opt in explicitly.
    """

    schema_version: Literal["remote-oauth-policy-v2"] = "remote-oauth-policy-v2"
    protocol_version: Literal[MCP_PROTOCOL_VERSION] = MCP_PROTOCOL_VERSION
    scope_source: Literal[
        "www_authenticate", "protected_resource_metadata", "omitted"
    ]
    recommended_scopes: tuple[str, ...] = ()
    recommended_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    offline_access_available: bool = False


RemoteOAuthPolicy: TypeAlias = RemoteOAuthPolicyV1 | RemoteOAuthPolicyV2


def _policy_from_json(value: str) -> RemoteOAuthPolicy:
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid OAuth policy JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("invalid OAuth policy document")
    if raw.get("schema_version") == "remote-oauth-policy-v2":
        return RemoteOAuthPolicyV2.model_validate(raw)
    return RemoteOAuthPolicyV1.model_validate(raw)


class RemoteOAuthDiscoverySnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["remote-oauth-discovery-v1"] = "remote-oauth-discovery-v1"
    discovery_id: str = Field(pattern=r"^mcpoauthdisc_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: OAuthTargetType
    target_id: str
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: RemoteOAuthPolicyV2 | RemoteOAuthPolicyV1
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "drifted", "blocked"]
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)


class RemoteOAuthClientRegistrationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["remote-oauth-registration-v1"] = "remote-oauth-registration-v1"
    registration_id: str = Field(pattern=r"^mcpoauthreg_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: OAuthTargetType
    target_id: str
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    issuer: str
    mode: OAuthRegistrationMode
    client_id: str = Field(min_length=1, max_length=2048)
    revision: int = Field(ge=1)
    status: Literal["active", "revoked", "stale"]
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)
    revoked_at: float | None = Field(default=None, ge=0)


class RemoteOAuthBridgeProtocol(Protocol):
    async def probe_resource(self, target_id: str, url: str) -> dict[str, Any]: ...

    async def fetch_json(
        self, target_id: str, url: str, *, document_kind: str
    ) -> dict[str, Any]: ...

    async def register_public_client(
        self, target_id: str, url: str, *, request_body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def exchange_authorization_code(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]: ...

    async def refresh_access_token(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]: ...

    async def revoke_token(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]: ...


class RemoteOAuthSocketBridge:
    """Backend control client for the network-less OAuth metadata sidecar."""

    def __init__(
        self,
        *,
        oauth_socket: str | None = None,
        egress_socket: str | None = None,
    ) -> None:
        self.oauth_socket = oauth_socket or os.getenv(
            "MCP_REMOTE_OAUTH_SOCKET_PATH", "/run/modelmirror-oauth/oauth.sock"
        )
        self.egress_socket = egress_socket or os.getenv(
            "MCP_HUB_EGRESS_SOCKET_PATH", "/run/modelmirror-hub-egress/hub-egress.sock"
        )

    async def _request(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float = 15.0,
        ambiguous_after_write: bool = False,
        ambiguous_code: str = "mcp_remote_oauth_registration_unknown_outcome",
        ambiguous_message: str = "OAuth 动态登记结果未知。",
    ) -> dict[str, Any]:
        writer: asyncio.StreamWriter | None = None
        dispatched = False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path), timeout=3
            )
            writer.write(_canonical_json(payload) + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=2)
            dispatched = True
            raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            raise RemoteOAuthError(
                ambiguous_message
                if ambiguous_after_write and dispatched
                else "OAuth 隔离发现服务不可用。",
                code=(
                    ambiguous_code
                    if ambiguous_after_write and dispatched
                    else "mcp_remote_oauth_sidecar_unavailable"
                ),
                status_code=503,
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    pass
        if not raw or len(raw) > MAX_METADATA_BYTES + 4096:
            raise RemoteOAuthError(
                ambiguous_message
                if ambiguous_after_write
                else "OAuth 隔离发现服务响应无效。",
                code=(
                    ambiguous_code
                    if ambiguous_after_write
                    else "mcp_remote_oauth_sidecar_invalid"
                ),
                status_code=502,
            )
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RemoteOAuthError(
                ambiguous_message
                if ambiguous_after_write
                else "OAuth 隔离发现服务响应无效。",
                code=(
                    ambiguous_code
                    if ambiguous_after_write
                    else "mcp_remote_oauth_sidecar_invalid"
                ),
                status_code=502,
            ) from exc
        if not isinstance(response, dict) or not response.get("ok"):
            code = str(response.get("code") if isinstance(response, dict) else "")
            raise RemoteOAuthError(
                "OAuth 隔离发现服务拒绝请求。",
                code=code or "mcp_remote_oauth_sidecar_invalid",
                status_code=502,
            )
        return response

    async def _authorize(self, target_id: str, url: str) -> str:
        response = await self._request(
            self.egress_socket,
            {"action": "authorize", "candidate_id": target_id, "url": url},
        )
        capability = str(response.get("capability") or "")
        if HEX64_RE.fullmatch(capability) is None:
            raise RemoteOAuthError(
                "OAuth 出口能力无效。",
                code="mcp_remote_oauth_egress_invalid",
                status_code=502,
            )
        return capability

    async def _exchange(
        self,
        target_id: str,
        url: str,
        payload: dict[str, Any],
        *,
        timeout: float,
        ambiguous_after_write: bool = False,
        ambiguous_code: str = "mcp_remote_oauth_registration_unknown_outcome",
        ambiguous_message: str = "OAuth 动态登记结果未知。",
    ) -> dict[str, Any]:
        capability = await self._authorize(target_id, url)
        try:
            return await self._request(
                self.oauth_socket,
                {
                    **payload,
                    "target_id": target_id,
                    "url": url,
                    "capability": capability,
                },
                timeout=timeout,
                ambiguous_after_write=ambiguous_after_write,
                ambiguous_code=ambiguous_code,
                ambiguous_message=ambiguous_message,
            )
        finally:
            try:
                await self._request(
                    self.egress_socket,
                    {"action": "revoke", "capability": capability},
                    timeout=5,
                )
            except RemoteOAuthError:
                pass

    async def probe_resource(self, target_id: str, url: str) -> dict[str, Any]:
        return await self._exchange(target_id, url, {"action": "probe_resource"}, timeout=12)

    async def fetch_json(
        self, target_id: str, url: str, *, document_kind: str
    ) -> dict[str, Any]:
        return await self._exchange(
            target_id,
            url,
            {"action": "fetch_json", "document_kind": document_kind},
            timeout=15,
        )

    async def register_public_client(
        self, target_id: str, url: str, *, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._exchange(
            target_id,
            url,
            {"action": "register_public_client", "request_body": request_body},
            timeout=20,
            ambiguous_after_write=True,
        )

    async def exchange_authorization_code(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        try:
            return await self._exchange(
                target_id,
                url,
                {"action": "exchange_authorization_code", "request_body": request_body},
                timeout=20,
                ambiguous_after_write=True,
            )
        finally:
            request_body["code"] = ""
            request_body["code_verifier"] = ""

    async def refresh_access_token(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        try:
            return await self._exchange(
                target_id,
                url,
                {"action": "refresh_access_token", "request_body": request_body},
                timeout=20,
                ambiguous_after_write=True,
            )
        finally:
            request_body["refresh_token"] = ""

    async def revoke_token(
        self, target_id: str, url: str, *, request_body: dict[str, str]
    ) -> dict[str, Any]:
        try:
            return await self._exchange(
                target_id,
                url,
                {"action": "revoke_token", "request_body": request_body},
                timeout=20,
                ambiguous_after_write=True,
                ambiguous_code="mcp_remote_oauth_revocation_unknown_outcome",
                ambiguous_message="OAuth 远程撤销结果未知。",
            )
        finally:
            request_body["token"] = ""


class MCPRemoteOAuthStore:
    """Additive OAuth metadata tables in the R0 remote-auth database."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("MCP_CATALOG_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.storage_dir / "remote-auth.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_oauth_discoveries (
                    discovery_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    subject_mode TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    discovery_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_oauth_one_active_discovery
                    ON remote_oauth_discoveries(tenant_id,owner_id,target_type,target_id)
                    WHERE status='active';
                CREATE TABLE IF NOT EXISTS remote_oauth_registrations (
                    registration_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    subject_mode TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    discovery_fingerprint TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_oauth_one_active_registration
                    ON remote_oauth_registrations(tenant_id,owner_id,target_type,target_id)
                    WHERE status='active';
                CREATE TABLE IF NOT EXISTS remote_oauth_registration_evidence (
                    registration_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    evidence_fingerprint TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(registration_id) REFERENCES remote_oauth_registrations(registration_id)
                );
                CREATE TABLE IF NOT EXISTS remote_oauth_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS remote_oauth_events_target_created
                    ON remote_oauth_events(tenant_id,owner_id,target_type,target_id,created_at);
                CREATE TABLE IF NOT EXISTS remote_oauth_registration_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    discovery_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            db.execute(
                "UPDATE remote_oauth_registration_attempts SET state='unknown_outcome',"
                "error_code='mcp_remote_oauth_registration_unknown_outcome',updated_at=? "
                "WHERE state='started'",
                (time.time(),),
            )

    def ready(self) -> bool:
        try:
            with self._lock, self._connect() as db:
                return db.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def save_discovery(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
        policy: RemoteOAuthPolicy,
    ) -> RemoteOAuthDiscoverySnapshotV1:
        clean_target = _target(target_id)
        if HEX64_RE.fullmatch(source_digest) is None:
            raise RemoteOAuthError(
                "候选来源摘要无效。",
                code="mcp_remote_oauth_source_drift",
                status_code=409,
            )
        fingerprint = _digest(
            {
                "schema_version": "remote-oauth-discovery-v1",
                "target_type": target_type,
                "target_id": clean_target,
                "source_digest": source_digest,
                "policy": policy.model_dump(mode="json"),
            }
        )
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM remote_oauth_discoveries WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, clean_target),
            ).fetchone()
            if current is not None and current["discovery_fingerprint"] == fingerprint:
                return self._row_to_discovery(current)
            if current is not None:
                db.execute(
                    "UPDATE remote_oauth_discoveries SET status='drifted',updated_at=? "
                    "WHERE discovery_id=?",
                    (now, current["discovery_id"]),
                )
                db.execute(
                    "UPDATE remote_oauth_registrations SET status='stale',revision=revision+1,updated_at=? "
                    "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? AND status='active'",
                    (now, subject.tenant_id, subject.owner_id, target_type, clean_target),
                )
            snapshot = RemoteOAuthDiscoverySnapshotV1(
                discovery_id=f"mcpoauthdisc_{uuid.uuid4().hex}",
                subject=subject,
                target_type=target_type,
                target_id=clean_target,
                source_digest=source_digest,
                policy=policy,
                discovery_fingerprint=fingerprint,
                status="active",
                created_at=now,
                updated_at=now,
            )
            db.execute(
                "INSERT INTO remote_oauth_discoveries("
                "discovery_id,schema_version,tenant_id,owner_id,subject_mode,target_type,"
                "target_id,source_digest,policy_json,discovery_fingerprint,status,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.discovery_id,
                    snapshot.schema_version,
                    subject.tenant_id,
                    subject.owner_id,
                    subject.mode,
                    target_type,
                    clean_target,
                    source_digest,
                    snapshot.policy.model_dump_json(),
                    fingerprint,
                    snapshot.status,
                    now,
                    now,
                ),
            )
            self._event(
                db,
                subject=subject,
                target_type=target_type,
                target_id=clean_target,
                object_id=snapshot.discovery_id,
                event_type="discovered",
                fingerprint=fingerprint,
            )
        return snapshot

    def active_discovery(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> RemoteOAuthDiscoverySnapshotV1 | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_discoveries WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, _target(target_id)),
            ).fetchone()
        return self._row_to_discovery(row) if row is not None else None

    def save_registration(
        self,
        *,
        subject: SubjectScopeV1,
        discovery: RemoteOAuthDiscoverySnapshotV1,
        mode: OAuthRegistrationMode,
        client_id: str,
        evidence: dict[str, Any],
    ) -> RemoteOAuthClientRegistrationV1:
        clean_client_id = str(client_id or "").strip()
        if not clean_client_id or len(clean_client_id) > 2048 or any(
            ord(char) < 0x20 or ord(char) == 0x7F for char in clean_client_id
        ):
            raise RemoteOAuthError(
                "OAuth client_id 无效。",
                code="mcp_remote_oauth_registration_invalid",
                status_code=422,
            )
        if not isinstance(evidence, dict) or len(_canonical_json(evidence)) > 8192:
            raise RemoteOAuthError(
                "OAuth 客户端登记证据无效。",
                code="mcp_remote_oauth_registration_invalid",
                status_code=422,
            )
        evidence_fingerprint = _digest(evidence)
        now = time.time()
        registration = RemoteOAuthClientRegistrationV1(
            registration_id=f"mcpoauthreg_{uuid.uuid4().hex}",
            subject=subject,
            target_type=discovery.target_type,
            target_id=discovery.target_id,
            discovery_fingerprint=discovery.discovery_fingerprint,
            issuer=discovery.policy.issuer,
            mode=mode,
            client_id=clean_client_id,
            revision=1,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                active = db.execute(
                    "SELECT discovery_id,source_digest,discovery_fingerprint,policy_json "
                    "FROM remote_oauth_discoveries WHERE tenant_id=? AND owner_id=? "
                    "AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        registration.target_type,
                        registration.target_id,
                    ),
                ).fetchone()
                if (
                    active is None
                    or active["discovery_id"] != discovery.discovery_id
                    or active["source_digest"] != discovery.source_digest
                    or active["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                    or active["policy_json"] != discovery.policy.model_dump_json()
                ):
                    raise RemoteOAuthError(
                        "OAuth 发现快照已过期或发生漂移。",
                        code="mcp_remote_oauth_discovery_stale",
                        status_code=409,
                    )
                db.execute(
                    "INSERT INTO remote_oauth_registrations("
                    "registration_id,schema_version,tenant_id,owner_id,subject_mode,target_type,"
                    "target_id,discovery_fingerprint,issuer,mode,client_id,revision,status,"
                    "created_at,updated_at,revoked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        registration.registration_id,
                        registration.schema_version,
                        subject.tenant_id,
                        subject.owner_id,
                        subject.mode,
                        registration.target_type,
                        registration.target_id,
                        registration.discovery_fingerprint,
                        registration.issuer,
                        registration.mode,
                        registration.client_id,
                        registration.revision,
                        registration.status,
                        registration.created_at,
                        registration.updated_at,
                        registration.revoked_at,
                    ),
                )
                db.execute(
                    "INSERT INTO remote_oauth_registration_evidence("
                    "registration_id,schema_version,evidence_json,evidence_fingerprint,created_at"
                    ") VALUES(?,?,?,?,?)",
                    (
                        registration.registration_id,
                        "remote-oauth-registration-evidence-v1",
                        _canonical_json(evidence).decode("utf-8"),
                        evidence_fingerprint,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteOAuthError(
                    "该候选已存在 OAuth 客户端登记。",
                    code="mcp_remote_oauth_registration_conflict",
                ) from exc
            self._event(
                db,
                subject=subject,
                target_type=registration.target_type,
                target_id=registration.target_id,
                object_id=registration.registration_id,
                event_type="registered",
                fingerprint=registration.discovery_fingerprint,
            )
        return registration

    def active_state(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> tuple[
        RemoteOAuthDiscoverySnapshotV1 | None,
        RemoteOAuthClientRegistrationV1 | None,
        dict[str, Any] | None,
    ]:
        clean_target = _target(target_id)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            discovery_row = db.execute(
                "SELECT * FROM remote_oauth_discoveries WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, clean_target),
            ).fetchone()
            registration_row = db.execute(
                "SELECT * FROM remote_oauth_registrations WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, clean_target),
            ).fetchone()
            if (
                registration_row is not None
                and (
                    discovery_row is None
                    or registration_row["discovery_fingerprint"]
                    != discovery_row["discovery_fingerprint"]
                )
            ):
                db.execute(
                    "UPDATE remote_oauth_registrations SET status='stale',revision=revision+1,"
                    "updated_at=? WHERE registration_id=? AND status='active'",
                    (time.time(), registration_row["registration_id"]),
                )
                registration_row = None
            evidence: dict[str, Any] | None = None
            if registration_row is not None:
                evidence_row = db.execute(
                    "SELECT evidence_json,evidence_fingerprint "
                    "FROM remote_oauth_registration_evidence "
                    "WHERE registration_id=?",
                    (registration_row["registration_id"],),
                ).fetchone()
                if evidence_row is None:
                    db.execute(
                        "UPDATE remote_oauth_registrations SET status='stale',revision=revision+1,"
                        "updated_at=? WHERE registration_id=? AND status='active'",
                        (time.time(), registration_row["registration_id"]),
                    )
                    registration_row = None
                else:
                    try:
                        loaded = json.loads(evidence_row["evidence_json"])
                    except json.JSONDecodeError as exc:
                        raise RemoteOAuthError(
                            "OAuth 客户端登记存储损坏。",
                            code="mcp_remote_oauth_storage_corrupt",
                            status_code=503,
                        ) from exc
                    if (
                        not isinstance(loaded, dict)
                        or _digest(loaded) != evidence_row["evidence_fingerprint"]
                    ):
                        raise RemoteOAuthError(
                            "OAuth 客户端登记存储损坏。",
                            code="mcp_remote_oauth_storage_corrupt",
                            status_code=503,
                        )
                    evidence = loaded
        return (
            self._row_to_discovery(discovery_row)
            if discovery_row is not None
            else None,
            self._row_to_registration(registration_row)
            if registration_row is not None
            else None,
            evidence,
        )

    def mark_registration_stale(
        self,
        registration_id: str,
        *,
        subject: SubjectScopeV1,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE remote_oauth_registrations SET status='stale',revision=revision+1,"
                "updated_at=? WHERE registration_id=? AND tenant_id=? AND owner_id=? "
                "AND status='active'",
                (
                    time.time(),
                    registration_id,
                    subject.tenant_id,
                    subject.owner_id,
                ),
            )

    def active_registration(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> RemoteOAuthClientRegistrationV1 | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_registrations WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, _target(target_id)),
            ).fetchone()
        return self._row_to_registration(row) if row is not None else None

    def start_registration_attempt(
        self,
        *,
        subject: SubjectScopeV1,
        discovery: RemoteOAuthDiscoverySnapshotV1,
    ) -> str:
        attempt_id = f"mcpoauthattempt_{uuid.uuid4().hex}"
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT 1 FROM remote_oauth_registration_attempts "
                "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND discovery_fingerprint=? LIMIT 1",
                (
                    subject.tenant_id,
                    subject.owner_id,
                    discovery.target_type,
                    discovery.target_id,
                    discovery.discovery_fingerprint,
                ),
            ).fetchone()
            if previous is not None:
                raise RemoteOAuthError(
                    "该发现 revision 已执行过不可安全重放的动态登记。",
                    code="mcp_remote_oauth_registration_replay_denied",
                    status_code=409,
                )
            db.execute(
                "INSERT INTO remote_oauth_registration_attempts("
                "attempt_id,tenant_id,owner_id,target_type,target_id,discovery_fingerprint,"
                "state,error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    subject.tenant_id,
                    subject.owner_id,
                    discovery.target_type,
                    discovery.target_id,
                    discovery.discovery_fingerprint,
                    "started",
                    "",
                    now,
                    now,
                ),
            )
        return attempt_id

    def finish_registration_attempt(
        self, attempt_id: str, *, state: str, error_code: str = ""
    ) -> None:
        if state not in {"completed", "failed", "unknown_outcome"}:
            raise RemoteOAuthError(
                "OAuth 客户端登记账本状态无效。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            )
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE remote_oauth_registration_attempts SET state=?,error_code=?,updated_at=? "
                "WHERE attempt_id=? AND state='started'",
                (state, error_code, time.time(), attempt_id),
            )
            if cursor.rowcount != 1:
                raise RemoteOAuthError(
                    "OAuth 客户端登记账本已完成。",
                    code="mcp_remote_oauth_registration_replay_denied",
                    status_code=409,
                )

    def dynamic_attempt_blocks_replay(
        self,
        *,
        subject: SubjectScopeV1,
        discovery: RemoteOAuthDiscoverySnapshotV1,
    ) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT state,error_code FROM remote_oauth_registration_attempts "
                "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND discovery_fingerprint=? ORDER BY created_at DESC LIMIT 1",
                (
                    subject.tenant_id,
                    subject.owner_id,
                    discovery.target_type,
                    discovery.target_id,
                    discovery.discovery_fingerprint,
                ),
            ).fetchone()
        return row is not None

    def revoke_registration(
        self,
        registration_id: str,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> RemoteOAuthClientRegistrationV1:
        if REGISTRATION_ID_RE.fullmatch(registration_id) is None:
            raise RemoteOAuthError(
                "OAuth 客户端登记不存在。",
                code="mcp_remote_oauth_registration_missing",
                status_code=404,
            )
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_registrations WHERE registration_id=?",
                (registration_id,),
            ).fetchone()
            if row is None:
                raise RemoteOAuthError(
                    "OAuth 客户端登记不存在。",
                    code="mcp_remote_oauth_registration_missing",
                    status_code=404,
                )
            if (
                row["tenant_id"] != subject.tenant_id
                or row["owner_id"] != subject.owner_id
                or row["target_type"] != target_type
                or row["target_id"] != _target(target_id)
            ):
                raise RemoteOAuthError(
                    "OAuth 客户端登记不属于当前主体和目标。",
                    code="mcp_remote_oauth_scope_denied",
                    status_code=403,
                )
            current = self._row_to_registration(row)
            if current.status == "revoked":
                return current
            now = time.time()
            db.execute(
                "UPDATE remote_oauth_registrations SET status='revoked',revision=revision+1,"
                "updated_at=?,revoked_at=? WHERE registration_id=? AND revision=?",
                (now, now, registration_id, current.revision),
            )
            updated_row = db.execute(
                "SELECT * FROM remote_oauth_registrations WHERE registration_id=?",
                (registration_id,),
            ).fetchone()
            updated = self._row_to_registration(updated_row)
            self._event(
                db,
                subject=subject,
                target_type=target_type,
                target_id=target_id,
                object_id=registration_id,
                event_type="revoked",
                fingerprint=updated.discovery_fingerprint,
            )
        return updated

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
        object_id: str,
        event_type: str,
        fingerprint: str,
        error_code: str = "",
    ) -> None:
        db.execute(
            "INSERT INTO remote_oauth_events("
            "event_id,tenant_id,owner_id,target_type,target_id,object_id,event_type,"
            "fingerprint,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                f"mcpoauthe_{uuid.uuid4().hex}",
                subject.tenant_id,
                subject.owner_id,
                target_type,
                target_id,
                object_id,
                event_type,
                fingerprint,
                error_code,
                time.time(),
            ),
        )

    @staticmethod
    def _row_to_discovery(row: sqlite3.Row) -> RemoteOAuthDiscoverySnapshotV1:
        try:
            return RemoteOAuthDiscoverySnapshotV1(
                schema_version=row["schema_version"],
                discovery_id=row["discovery_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                source_digest=row["source_digest"],
                policy=_policy_from_json(row["policy_json"]),
                discovery_fingerprint=row["discovery_fingerprint"],
                status=row["status"],
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
            )
        except Exception as exc:
            raise RemoteOAuthError(
                "OAuth 发现存储损坏。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            ) from exc

    @staticmethod
    def _row_to_registration(row: sqlite3.Row) -> RemoteOAuthClientRegistrationV1:
        try:
            return RemoteOAuthClientRegistrationV1(
                schema_version=row["schema_version"],
                registration_id=row["registration_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                discovery_fingerprint=row["discovery_fingerprint"],
                issuer=row["issuer"],
                mode=row["mode"],
                client_id=row["client_id"],
                revision=int(row["revision"]),
                status=row["status"],
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
                revoked_at=(
                    float(row["revoked_at"])
                    if row["revoked_at"] is not None
                    else None
                ),
            )
        except Exception as exc:
            raise RemoteOAuthError(
                "OAuth 客户端登记存储损坏。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            ) from exc


def _target(value: str) -> str:
    clean = str(value or "").strip()
    if TARGET_ID_RE.fullmatch(clean) is None:
        raise RemoteOAuthError(
            "OAuth 目标无效。",
            code="mcp_remote_oauth_scope_denied",
            status_code=403,
        )
    return clean


class MCPRemoteOAuthService:
    def __init__(
        self,
        store: MCPRemoteOAuthStore,
        *,
        subject_resolver: SubjectScopeResolver,
        remote_auth_status: Callable[[], dict[str, Any]],
        bridge: RemoteOAuthBridgeProtocol | None = None,
    ) -> None:
        self.store = store
        self.subject_resolver = subject_resolver
        self.remote_auth_status = remote_auth_status
        self.bridge = bridge or RemoteOAuthSocketBridge()
        self.authorization_service: Any | None = None
        self._locks = tuple(asyncio.Lock() for _ in range(64))

    def set_authorization_service(self, service: Any) -> None:
        self.authorization_service = service

    def status(self) -> dict[str, Any]:
        base = self.remote_auth_status()
        try:
            normalize_oauth_url(
                os.getenv("MCP_REMOTE_OAUTH_CLIENT_METADATA_URL", ""),
                field="Client ID Metadata Document 地址",
            )
            self._redirect_uri()
            client_metadata_configured = True
        except RemoteOAuthError:
            client_metadata_configured = False
        authorization = (
            self.authorization_service.status()
            if self.authorization_service is not None
            else {"authorization_enabled": False, "token_storage_enabled": False}
        )
        return {
            "enabled": _flag("MCP_REMOTE_OAUTH_ENABLED"),
            "dynamic_registration_enabled": _flag(
                "MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED"
            ),
            "remote_auth_enabled": bool(base.get("enabled")),
            "single_owner_acknowledged": bool(base.get("single_owner_acknowledged")),
            "external_master_key_available": bool(
                base.get("external_master_key_available")
            ),
            "external_master_key_enforced": bool(
                base.get("external_master_key_enforced")
            ),
            "storage_ready": self.store.ready(),
            "client_metadata_document_configured": client_metadata_configured,
            "subject_mode": "local-single-owner",
            "supported_registration_modes": [
                "pre_registered",
                *(
                    ["client_id_metadata_document"]
                    if client_metadata_configured
                    else []
                ),
                *(
                    ["dynamic"]
                    if _flag("MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED")
                    else []
                ),
            ],
            "authorization_enabled": bool(
                authorization.get("authorization_enabled")
            ),
            "token_storage_enabled": bool(
                authorization.get("token_storage_enabled")
            ),
            "review_enabled": bool(authorization.get("review_enabled")),
            "runtime_enabled": bool(authorization.get("runtime_enabled")),
            "remote_revocation_enabled": bool(
                authorization.get("remote_revocation_enabled")
            ),
            "multi_tenant": False,
        }

    def _require_operational(self) -> SubjectScopeV1:
        state = self.status()
        if not state["remote_auth_enabled"]:
            raise RemoteOAuthError(
                "远程认证基础尚未启用。",
                code="mcp_remote_auth_disabled",
                status_code=503,
            )
        if not state["enabled"]:
            raise RemoteOAuthError(
                "OAuth 发现功能尚未启用。",
                code="mcp_remote_oauth_disabled",
                status_code=503,
            )
        if not state["single_owner_acknowledged"]:
            raise RemoteOAuthError(
                "尚未确认本地单主体边界。",
                code="mcp_remote_auth_single_owner_ack_required",
                status_code=503,
            )
        if not (
            state["external_master_key_available"]
            and state["external_master_key_enforced"]
        ):
            raise RemoteOAuthError(
                "外部凭据主密钥不可用。",
                code="mcp_remote_auth_master_key_required",
                status_code=503,
            )
        if not state["storage_ready"]:
            raise RemoteOAuthError(
                "OAuth 发现存储不可用。",
                code="mcp_remote_oauth_storage_unavailable",
                status_code=503,
            )
        return self.subject_resolver.resolve()

    def authorization_state(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> tuple[
        RemoteOAuthDiscoverySnapshotV1 | None,
        RemoteOAuthClientRegistrationV1 | None,
    ]:
        discovery, registration, evidence = self.store.active_state(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if registration is not None and not self._registration_evidence_current(
            registration, evidence
        ):
            self.store.mark_registration_stale(
                registration.registration_id, subject=subject
            )
            registration = None
        return discovery, registration

    async def discover(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        resource_url: str,
        source_digest: str,
        require_bearer_challenge: bool = False,
    ) -> RemoteOAuthDiscoverySnapshotV1:
        subject = self._require_operational()
        clean_target = _target(target_id)
        resource = normalize_oauth_url(resource_url, field="MCP 资源地址")
        if HEX64_RE.fullmatch(source_digest) is None:
            raise RemoteOAuthError(
                "候选来源摘要无效。",
                code="mcp_remote_oauth_source_drift",
                status_code=409,
            )
        lock = self._locks[
            hash((subject.tenant_id, subject.owner_id, target_type, clean_target))
            % len(self._locks)
        ]
        async with lock:
            previous = self.store.active_discovery(
                subject=subject,
                target_type=target_type,
                target_id=clean_target,
            )
            metadata_hint = ""
            challenge_scopes: tuple[str, ...] = ()
            try:
                probe = await self.bridge.probe_resource(clean_target, resource)
                if require_bearer_challenge and not (
                    probe.get("status_class") == "4xx"
                    and probe.get("bearer_challenge") is True
                ):
                    raise RemoteOAuthError(
                        "远程端点未返回可归属到当前资源的 Bearer 挑战。",
                        code="mcp_remote_oauth_bearer_challenge_required",
                        status_code=409,
                    )
                metadata_hint = str(probe.get("resource_metadata_url") or "")
                raw_challenge_scopes = probe.get("challenge_scopes") or []
                challenge_scopes = self._validated_scope_values(
                    raw_challenge_scopes,
                    field="WWW-Authenticate Scope",
                    maximum=MAX_RECOMMENDED_SCOPES,
                )
            except RemoteOAuthError as exc:
                if require_bearer_challenge:
                    raise
                if exc.code not in {
                    "mcp_remote_oauth_probe_unsupported",
                    "mcp_remote_oauth_upstream_http",
                    "hub_upstream_method_denied",
                }:
                    raise
            prm_urls: list[str] = []
            if metadata_hint:
                prm_urls.append(
                    normalize_oauth_url(
                        metadata_hint, field="Protected Resource Metadata 地址"
                    )
                )
            prm_urls.extend(_protected_resource_well_known_urls(resource))
            protected_url, protected, protected_digest = await self._first_document(
                clean_target,
                tuple(dict.fromkeys(prm_urls)),
                document_kind="protected_resource_metadata",
                missing_code="mcp_remote_oauth_protected_metadata_missing",
            )
            if str(protected.get("resource") or "") != resource:
                raise RemoteOAuthError(
                    "Protected Resource Metadata 未绑定当前 MCP 资源。",
                    code="mcp_remote_oauth_resource_mismatch",
                    status_code=409,
                )
            servers = protected.get("authorization_servers")
            if not isinstance(servers, list) or len(servers) != 1:
                raise RemoteOAuthError(
                    "第一轮仅允许一个固定授权服务器。",
                    code="mcp_remote_oauth_authorization_server_ambiguous",
                    status_code=409,
                )
            # The issuer identifier is compared as an exact string by OAuth
            # mix-up defenses. Validate a normalized copy for network safety,
            # but freeze the server-declared identifier without inventing a
            # trailing slash that was not present in either metadata document.
            issuer = str(servers[0]).strip()
            issuer_network_url = normalize_oauth_url(
                issuer, field="授权服务器 issuer"
            )
            metadata_url, metadata, metadata_digest = await self._first_document(
                clean_target,
                _authorization_server_well_known_urls(issuer_network_url),
                document_kind="authorization_server_metadata",
                missing_code="mcp_remote_oauth_authorization_metadata_missing",
            )
            policy = self._policy(
                resource=resource,
                protected_url=protected_url,
                protected=protected,
                protected_digest=protected_digest,
                issuer=issuer,
                metadata_url=metadata_url,
                metadata=metadata,
                metadata_digest=metadata_digest,
                challenge_scopes=challenge_scopes,
            )
            saved = self.store.save_discovery(
                subject=subject,
                target_type=target_type,
                target_id=clean_target,
                source_digest=source_digest,
                policy=policy,
            )
            if (
                previous is not None
                and previous.discovery_fingerprint != saved.discovery_fingerprint
                and self.authorization_service is not None
            ):
                self.authorization_service.invalidate_target(
                    target_type=target_type, target_id=clean_target
                )
            return saved

    async def _first_document(
        self,
        target_id: str,
        urls: tuple[str, ...],
        *,
        document_kind: str,
        missing_code: str,
    ) -> tuple[str, dict[str, Any], str]:
        last: RemoteOAuthError | None = None
        for url in urls:
            try:
                response = await self.bridge.fetch_json(
                    target_id, url, document_kind=document_kind
                )
                document = response.get("document")
                document_digest = str(response.get("document_digest") or "")
                if not isinstance(document, dict) or len(_canonical_json(document)) > MAX_METADATA_BYTES:
                    raise RemoteOAuthError(
                        "OAuth 元数据结构无效。",
                        code="mcp_remote_oauth_metadata_invalid",
                        status_code=502,
                    )
                if HEX64_RE.fullmatch(document_digest) is None:
                    # Test and alternative bridge implementations may omit the
                    # digest; derive it from their already bounded document.
                    document_digest = _digest(document)
                return url, document, document_digest
            except RemoteOAuthError as exc:
                last = exc
                if exc.code not in {
                    "mcp_remote_oauth_document_not_found",
                    "mcp_remote_oauth_upstream_http",
                }:
                    raise
        raise RemoteOAuthError(
            "OAuth 发现元数据不存在。", code=missing_code, status_code=409
        ) from last

    @staticmethod
    def _policy(
        *,
        resource: str,
        protected_url: str,
        protected: dict[str, Any],
        protected_digest: str,
        issuer: str,
        metadata_url: str,
        metadata: dict[str, Any],
        metadata_digest: str,
        challenge_scopes: tuple[str, ...] = (),
    ) -> RemoteOAuthPolicyV2:
        metadata_issuer = str(metadata.get("issuer") or "").strip()
        normalize_oauth_url(metadata_issuer, field="授权服务器 metadata issuer")
        if metadata_issuer != issuer:
            raise RemoteOAuthError(
                "授权服务器 metadata issuer 不匹配。",
                code="mcp_remote_oauth_issuer_mismatch",
                status_code=409,
            )
        pkce = metadata.get("code_challenge_methods_supported")
        grants = metadata.get("grant_types_supported")
        responses = metadata.get("response_types_supported")
        if not isinstance(pkce, list) or "S256" not in pkce:
            raise RemoteOAuthError(
                "授权服务器不声明 PKCE S256。",
                code="mcp_remote_oauth_pkce_s256_required",
                status_code=409,
            )
        if not isinstance(grants, list) or "authorization_code" not in grants:
            raise RemoteOAuthError(
                "授权服务器不支持 authorization_code。",
                code="mcp_remote_oauth_grant_unsupported",
                status_code=409,
            )
        if not isinstance(responses, list) or "code" not in responses:
            raise RemoteOAuthError(
                "授权服务器不支持 code 响应。",
                code="mcp_remote_oauth_response_unsupported",
                status_code=409,
            )
        authorization_endpoint = normalize_oauth_url(
            metadata.get("authorization_endpoint"), field="authorization_endpoint"
        )
        token_endpoint = normalize_oauth_url(
            metadata.get("token_endpoint"), field="token_endpoint"
        )
        registration_endpoint = ""
        if metadata.get("registration_endpoint"):
            registration_endpoint = normalize_oauth_url(
                metadata.get("registration_endpoint"), field="registration_endpoint"
            )
        revocation_endpoint = ""
        if metadata.get("revocation_endpoint"):
            revocation_endpoint = normalize_oauth_url(
                metadata.get("revocation_endpoint"), field="revocation_endpoint"
            )
        protected_scopes = MCPRemoteOAuthService._validated_scope_values(
            protected.get("scopes_supported") or [],
            field="Protected Resource Metadata Scope",
            maximum=MAX_SCOPES,
        )
        server_scopes = MCPRemoteOAuthService._validated_scope_values(
            metadata.get("scopes_supported") or [],
            field="Authorization Server Scope",
            maximum=MAX_SCOPES,
        )
        if challenge_scopes:
            recommended = tuple(
                scope for scope in challenge_scopes if scope != "offline_access"
            )
            scope_source: Literal[
                "www_authenticate", "protected_resource_metadata", "omitted"
            ] = "www_authenticate"
        elif protected_scopes:
            recommended = tuple(
                scope for scope in protected_scopes if scope != "offline_access"
            )
            scope_source = "protected_resource_metadata"
        else:
            recommended = ()
            scope_source = "omitted"
        if len(recommended) > MAX_RECOMMENDED_SCOPES:
            raise RemoteOAuthError(
                "OAuth 推荐 Scope 超出上限。",
                code="mcp_remote_oauth_metadata_invalid",
                status_code=409,
            )
        execution = {
            "schema_version": "remote-oauth-policy-v2",
            "mode": "oauth_authorization_code_pkce",
            "protocol_version": MCP_PROTOCOL_VERSION,
            "resource_uri": resource,
            "origin": _origin(resource),
            "remote_url_digest": hashlib.sha256(resource.encode()).hexdigest(),
            "protected_resource_metadata_url": protected_url,
            "protected_resource_metadata_digest": protected_digest,
            "issuer": issuer,
            "authorization_server_metadata_url": metadata_url,
            "authorization_server_metadata_digest": metadata_digest,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "registration_endpoint": registration_endpoint,
            "revocation_endpoint": revocation_endpoint,
            "client_id_metadata_document_supported": bool(
                metadata.get("client_id_metadata_document_supported") is True
            ),
            "scopes_supported": protected_scopes,
            "scope_source": scope_source,
            "recommended_scopes": recommended,
            "recommended_scope_digest": _digest(list(recommended)),
            "offline_access_available": "offline_access" in server_scopes,
        }
        return RemoteOAuthPolicyV2(
            **execution, policy_fingerprint=_digest(execution)
        )

    @staticmethod
    def _validated_scope_values(
        value: Any,
        *,
        field: str,
        maximum: int,
    ) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or len(value) > maximum:
            raise RemoteOAuthError(
                f"{field} 超出上限。",
                code="mcp_remote_oauth_metadata_invalid",
                status_code=409,
            )
        clean: list[str] = []
        for item in value:
            if (
                not isinstance(item, str)
                or not item
                or len(item) > MAX_SCOPE_LENGTH
                or any(ord(char) < 0x21 or ord(char) == 0x7F for char in item)
            ):
                raise RemoteOAuthError(
                    f"{field} 无效。",
                    code="mcp_remote_oauth_metadata_invalid",
                    status_code=409,
                )
            clean.append(item)
        if len(set(clean)) != len(clean):
            raise RemoteOAuthError(
                f"{field} 包含重复值。",
                code="mcp_remote_oauth_metadata_invalid",
                status_code=409,
            )
        return tuple(sorted(clean))

    def summary(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
    ) -> dict[str, Any]:
        subject = self._require_operational()
        discovery, registration = self.authorization_state(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if discovery is not None and discovery.source_digest != source_digest:
            discovery = None
            registration = None
        authorization = (
            self.authorization_service.summary(
                target_type=target_type,
                target_id=target_id,
                source_digest=source_digest,
            )
            if self.authorization_service is not None
            else {"authorization_session": None, "token": None}
        )
        token = authorization.get("token")
        runtime_enabled = bool(authorization.get("runtime_enabled"))
        token_expires_at = token.get("expires_at") if isinstance(token, dict) else None
        token_has_runtime_ttl = token_expires_at is None or (
            isinstance(token_expires_at, (int, float))
            and not isinstance(token_expires_at, bool)
            and token_expires_at > time.time() + OAUTH_RUNTIME_MIN_TTL_SECONDS
        )
        return {
            "discovery": self._public_discovery(discovery) if discovery else None,
            "registration": (
                self._public_registration(registration) if registration else None
            ),
            "authorization_session": authorization.get("authorization_session"),
            "token": token,
            "authorization_enabled": bool(
                authorization.get("authorization_enabled")
            ),
            "token_storage_enabled": bool(
                authorization.get("token_storage_enabled")
            ),
            "review_enabled": bool(authorization.get("review_enabled")),
            "runtime_enabled": runtime_enabled,
            "remote_revocation_enabled": bool(
                authorization.get("remote_revocation_enabled")
            ),
            "runtime_eligible": bool(
                runtime_enabled
                and isinstance(token, dict)
                and token.get("status") == "active"
                and token.get("resource_bound")
                and token_has_runtime_ttl
            ),
            "local_single_owner_warning": True,
        }

    async def register_client(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
        expected_discovery_fingerprint: str,
        mode: OAuthRegistrationMode,
        client_id: str = "",
    ) -> RemoteOAuthClientRegistrationV1:
        subject = self._require_operational()
        clean_target = _target(target_id)
        lock = self._locks[
            hash((subject.tenant_id, subject.owner_id, target_type, clean_target))
            % len(self._locks)
        ]
        async with lock:
            return await self._register_client_locked(
                subject=subject,
                target_type=target_type,
                target_id=clean_target,
                source_digest=source_digest,
                expected_discovery_fingerprint=expected_discovery_fingerprint,
                mode=mode,
                client_id=client_id,
            )

    async def _register_client_locked(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
        expected_discovery_fingerprint: str,
        mode: OAuthRegistrationMode,
        client_id: str,
    ) -> RemoteOAuthClientRegistrationV1:
        discovery = self.store.active_discovery(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if (
            discovery is None
            or discovery.source_digest != source_digest
            or discovery.discovery_fingerprint != expected_discovery_fingerprint
        ):
            raise RemoteOAuthError(
                "OAuth 发现快照已过期或发生漂移。",
                code="mcp_remote_oauth_discovery_stale",
                status_code=409,
            )
        if self.store.active_registration(
            subject=subject, target_type=target_type, target_id=target_id
        ) is not None:
            raise RemoteOAuthError(
                "该候选已存在 OAuth 客户端登记。",
                code="mcp_remote_oauth_registration_conflict",
                status_code=409,
            )
        resolved_client_id = str(client_id or "").strip()
        evidence: dict[str, Any] = {
            "schema_version": "remote-oauth-registration-evidence-v1",
            "mode": mode,
            "discovery_fingerprint": discovery.discovery_fingerprint,
        }
        attempt_id = ""
        if mode == "client_id_metadata_document":
            if resolved_client_id:
                raise RemoteOAuthError(
                    "客户端不能覆盖 Client ID Metadata Document 地址。",
                    code="mcp_remote_oauth_client_metadata_injection_denied",
                    status_code=422,
                )
            resolved_client_id = normalize_oauth_url(
                os.getenv("MCP_REMOTE_OAUTH_CLIENT_METADATA_URL", ""),
                field="Client ID Metadata Document 地址",
            )
            if not discovery.policy.client_id_metadata_document_supported:
                raise RemoteOAuthError(
                    "授权服务器未声明 Client ID Metadata Document 支持。",
                    code="mcp_remote_oauth_client_metadata_unsupported",
                    status_code=409,
                )
            redirect_uri = self._redirect_uri()
            response = await self.bridge.fetch_json(
                target_id,
                resolved_client_id,
                document_kind="client_id_metadata_document",
            )
            document = response.get("document")
            document_digest = str(response.get("document_digest") or "")
            if isinstance(document, dict) and HEX64_RE.fullmatch(document_digest) is None:
                document_digest = _digest(document)
            if (
                not isinstance(document, dict)
                or document.get("client_id") != resolved_client_id
                or not isinstance(document.get("client_name"), str)
                or not str(document.get("client_name") or "").strip()
                or len(str(document.get("client_name"))) > 200
                or document.get("redirect_uris") != [redirect_uri]
                or document.get("grant_types") != ["authorization_code"]
                or document.get("response_types") != ["code"]
                or HEX64_RE.fullmatch(document_digest) is None
            ):
                raise RemoteOAuthError(
                    "Client ID Metadata Document 无效。",
                    code="mcp_remote_oauth_client_metadata_invalid",
                    status_code=409,
                )
            if document.get("token_endpoint_auth_method", "none") != "none":
                raise RemoteOAuthError(
                    "R2A 仅登记 OAuth public client。",
                    code="mcp_remote_oauth_confidential_client_denied",
                    status_code=409,
                )
            evidence.update(
                {
                    "client_metadata_url": resolved_client_id,
                    "client_metadata_document_digest": document_digest,
                    "redirect_uri": redirect_uri,
                }
            )
        elif mode == "dynamic":
            if resolved_client_id:
                raise RemoteOAuthError(
                    "动态登记不接受客户端提交 client_id。",
                    code="mcp_remote_oauth_registration_invalid",
                    status_code=422,
                )
            if not _flag("MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED"):
                raise RemoteOAuthError(
                    "OAuth 动态客户端登记尚未启用。",
                    code="mcp_remote_oauth_dynamic_registration_disabled",
                    status_code=503,
                )
            endpoint = discovery.policy.registration_endpoint
            if not endpoint:
                raise RemoteOAuthError(
                    "授权服务器未声明动态客户端登记端点。",
                    code="mcp_remote_oauth_registration_unsupported",
                    status_code=409,
                )
            if self.store.dynamic_attempt_blocks_replay(
                subject=subject, discovery=discovery
            ):
                raise RemoteOAuthError(
                    "该发现 revision 已执行过不可安全重放的动态登记。",
                    code="mcp_remote_oauth_registration_replay_denied",
                    status_code=409,
                )
            redirect_uri = self._redirect_uri()
            grant_types = ["authorization_code"]
            if isinstance(discovery.policy, RemoteOAuthPolicyV2) and discovery.policy.offline_access_available:
                grant_types.append("refresh_token")
            request_body = {
                "redirect_uris": [redirect_uri],
                "token_endpoint_auth_method": "none",
                "grant_types": grant_types,
                "response_types": ["code"],
                "application_type": "native",
                "client_name": "ModelMirror local MCP OAuth",
            }
            attempt_id = self.store.start_registration_attempt(
                subject=subject, discovery=discovery
            )
            try:
                response = await self.bridge.register_public_client(
                    target_id,
                    endpoint,
                    request_body=request_body,
                )
                if response.get("contains_secret"):
                    raise RemoteOAuthError(
                        "动态登记返回了 R2A 不允许保存的客户端秘密。",
                        code="mcp_remote_oauth_registration_secret_denied",
                        status_code=409,
                    )
                resolved_client_id = str(response.get("client_id") or "").strip()
                response_digest = str(
                    response.get("registration_response_digest") or ""
                )
                if HEX64_RE.fullmatch(response_digest) is None:
                    raise RemoteOAuthError(
                        "OAuth 动态登记响应证据无效，结果不可重放。",
                        code="mcp_remote_oauth_registration_unknown_outcome",
                        status_code=502,
                    )
                evidence.update(
                    {
                        "redirect_uri": redirect_uri,
                        "registration_request_digest": _digest(request_body),
                        "registration_response_digest": response_digest,
                    }
                )
            except asyncio.CancelledError:
                self.store.finish_registration_attempt(
                    attempt_id,
                    state="unknown_outcome",
                    error_code="mcp_remote_oauth_registration_unknown_outcome",
                )
                raise
            except RemoteOAuthError as exc:
                self.store.finish_registration_attempt(
                    attempt_id,
                    state="unknown_outcome",
                    error_code=exc.code,
                )
                raise
            except Exception as exc:
                self.store.finish_registration_attempt(
                    attempt_id,
                    state="unknown_outcome",
                    error_code="mcp_remote_oauth_registration_unknown_outcome",
                )
                raise RemoteOAuthError(
                    "OAuth 动态登记结果未知。",
                    code="mcp_remote_oauth_registration_unknown_outcome",
                    status_code=502,
                ) from exc
        elif mode != "pre_registered":
            raise RemoteOAuthError(
                "OAuth 客户端登记模式无效。",
                code="mcp_remote_oauth_registration_invalid",
                status_code=422,
            )
        try:
            registration = self.store.save_registration(
                subject=subject,
                discovery=discovery,
                mode=mode,
                client_id=resolved_client_id,
                evidence=evidence,
            )
        except RemoteOAuthError as exc:
            if attempt_id:
                self.store.finish_registration_attempt(
                    attempt_id, state="unknown_outcome", error_code=exc.code
                )
            raise
        if attempt_id:
            self.store.finish_registration_attempt(attempt_id, state="completed")
        return registration

    def _registration_evidence_current(
        self,
        registration: RemoteOAuthClientRegistrationV1,
        evidence: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(evidence, dict):
            return False
        if (
            evidence.get("schema_version")
            != "remote-oauth-registration-evidence-v1"
            or evidence.get("mode") != registration.mode
            or evidence.get("discovery_fingerprint")
            != registration.discovery_fingerprint
        ):
            return False
        if registration.mode == "client_id_metadata_document":
            try:
                configured_url = normalize_oauth_url(
                    os.getenv("MCP_REMOTE_OAUTH_CLIENT_METADATA_URL", ""),
                    field="Client ID Metadata Document 地址",
                )
                configured_redirect = self._redirect_uri()
            except RemoteOAuthError:
                return False
            return (
                evidence.get("client_metadata_url") == configured_url
                and evidence.get("redirect_uri") == configured_redirect
                and HEX64_RE.fullmatch(
                    str(evidence.get("client_metadata_document_digest") or "")
                )
                is not None
            )
        if registration.mode == "dynamic":
            try:
                configured_redirect = self._redirect_uri()
            except RemoteOAuthError:
                return False
            return (
                evidence.get("redirect_uri") == configured_redirect
                and HEX64_RE.fullmatch(
                    str(evidence.get("registration_request_digest") or "")
                )
                is not None
                and HEX64_RE.fullmatch(
                    str(evidence.get("registration_response_digest") or "")
                )
                is not None
            )
        return registration.mode == "pre_registered"

    def revoke_registration(
        self,
        registration_id: str,
        *,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> RemoteOAuthClientRegistrationV1:
        subject = self._require_operational()
        if self.authorization_service is not None:
            self.authorization_service.invalidate_target(
                target_type=target_type, target_id=target_id
            )
        return self.store.revoke_registration(
            registration_id,
            subject=subject,
            target_type=target_type,
            target_id=target_id,
        )

    def revoke_target_locally(
        self, *, target_type: OAuthTargetType, target_id: str
    ) -> None:
        """Revoke local registration on target deletion even when OAuth is off."""

        subject = self.subject_resolver.resolve()
        if self.authorization_service is not None:
            self.authorization_service.invalidate_target(
                target_type=target_type, target_id=target_id
            )
        registration = self.store.active_registration(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if registration is not None:
            self.store.revoke_registration(
                registration.registration_id,
                subject=subject,
                target_type=target_type,
                target_id=target_id,
            )

    @staticmethod
    def _redirect_uri() -> str:
        raw = os.getenv("MCP_REMOTE_OAUTH_REDIRECT_URI", "").strip()
        if not raw:
            raise RemoteOAuthError(
                "动态登记缺少固定回调地址。",
                code="mcp_remote_oauth_redirect_uri_required",
                status_code=503,
            )
        parsed = urlsplit(raw)
        try:
            port = parsed.port
        except ValueError as exc:
            raise RemoteOAuthError(
                "OAuth 回调地址不满足固定 HTTPS 或 loopback 边界。",
                code="mcp_remote_oauth_redirect_uri_invalid",
                status_code=422,
            ) from exc
        if parsed.scheme == "https":
            return normalize_oauth_url(raw, field="OAuth 回调地址")
        if (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and port is not None
        ):
            return raw
        raise RemoteOAuthError(
            "OAuth 回调地址不满足固定 HTTPS 或 loopback 边界。",
            code="mcp_remote_oauth_redirect_uri_invalid",
            status_code=503,
        )

    @staticmethod
    def _public_discovery(value: RemoteOAuthDiscoverySnapshotV1) -> dict[str, Any]:
        policy = value.policy
        output = {
            "discovery_id": value.discovery_id,
            "status": value.status,
            "discovery_fingerprint": value.discovery_fingerprint,
            "resource_uri": policy.resource_uri,
            "protected_resource_metadata_url": policy.protected_resource_metadata_url,
            "issuer": policy.issuer,
            "authorization_endpoint": policy.authorization_endpoint,
            "token_endpoint_origin": _origin(policy.token_endpoint),
            "registration_endpoint_available": bool(policy.registration_endpoint),
            "registration_endpoint": policy.registration_endpoint,
            "revocation_endpoint_available": bool(policy.revocation_endpoint),
            "pkce_method": "S256",
            "scopes_supported": list(policy.scopes_supported),
            "policy_fingerprint": policy.policy_fingerprint,
        }
        if isinstance(policy, RemoteOAuthPolicyV2):
            output.update(
                {
                    "scope_source": policy.scope_source,
                    "recommended_scopes": list(policy.recommended_scopes),
                    "recommended_scope_digest": policy.recommended_scope_digest,
                    "offline_access_available": policy.offline_access_available,
                    "protocol_version": policy.protocol_version,
                }
            )
        else:
            output.update(
                {
                    "scope_source": "legacy",
                    "recommended_scopes": [],
                    "recommended_scope_digest": _digest([]),
                    "offline_access_available": False,
                    "protocol_version": "",
                }
            )
        return output

    @staticmethod
    def _public_registration(value: RemoteOAuthClientRegistrationV1) -> dict[str, Any]:
        return {
            "registration_id": value.registration_id,
            "mode": value.mode,
            "client_id": value.client_id,
            "revision": value.revision,
            "status": value.status,
            "discovery_fingerprint": value.discovery_fingerprint,
            "registration_digest": _digest(
                {
                    "schema_version": value.schema_version,
                    "registration_id": value.registration_id,
                    "revision": value.revision,
                    "discovery_fingerprint": value.discovery_fingerprint,
                    "issuer": value.issuer,
                    "mode": value.mode,
                }
            ),
        }


router = APIRouter(tags=["mcp-remote-oauth"])
_oauth_service: MCPRemoteOAuthService | None = None


def configure_mcp_remote_oauth(service: MCPRemoteOAuthService) -> None:
    global _oauth_service
    _oauth_service = service


def _service() -> MCPRemoteOAuthService:
    if _oauth_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "mcp_remote_oauth_unconfigured",
                "error": "远程 MCP OAuth 发现基础尚未配置。",
            },
        )
    return _oauth_service


@router.get("/api/mcp/remote-auth/oauth/status")
async def remote_oauth_status(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length", "").strip()
    has_body = content_length not in {"", "0"} or "transfer-encoding" in request.headers
    if not request.query_params and not has_body:
        async for chunk in request.stream():
            if chunk:
                has_body = True
                break
    if request.query_params or has_body:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "mcp_remote_oauth_client_scope_denied",
                "error": "OAuth 状态接口不接受客户端范围或目标参数。",
            },
        )
    return _service().status()
