"""Local-single-owner OAuth authorization and reviewed execution resolver.

The authorization server and token endpoint are always taken from an active
R2A discovery snapshot. Client requests cannot supply a URL, Header, client ID,
tenant, owner, authorization code, verifier, or refresh token to write APIs.
OAuth tokens may be resolved only for internal Review Factory or V3 Runtime
paths whose independent feature gates and frozen execution context are current.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .remote_auth import SubjectScopeResolver, SubjectScopeV1
from .remote_oauth import (
    MCPRemoteOAuthStore,
    OAuthTargetType,
    RemoteOAuthClientRegistrationV1,
    RemoteOAuthDiscoverySnapshotV1,
    RemoteOAuthError,
    RemoteOAuthBridgeProtocol,
    RemoteOAuthPolicyV2,
    MCP_PROTOCOL_VERSION,
    OAUTH_RUNTIME_MIN_TTL_SECONDS,
)


HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SESSION_ID_RE = re.compile(r"^mcpoauthsession_[0-9a-f]{32}$")
TOKEN_ID_RE = re.compile(r"^mcpoauthtoken_[0-9a-f]{32}$")
STATE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CODE_RE = re.compile(r"^[^\x00-\x20\x7f]{1,4096}$")
SCOPE_RE = re.compile(r"^[\x21-\x7e]{1,160}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
MAX_SELECTED_SCOPES = 20
SESSION_TTL_SECONDS = 10 * 60


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _challenge(verifier: str) -> str:
    if VERIFIER_RE.fullmatch(verifier) is None:
        raise RemoteOAuthError(
            "PKCE verifier 无效。",
            code="mcp_remote_oauth_pkce_invalid",
            status_code=422,
        )
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")


def _authorization_scopes(
    policy: RemoteOAuthPolicyV2,
    *,
    expected_scope_digest: str,
    request_refresh_token: bool,
) -> tuple[str, ...]:
    if expected_scope_digest != policy.recommended_scope_digest:
        raise RemoteOAuthError(
            "OAuth 推荐 Scope 摘要已变化。",
            code="mcp_remote_oauth_scope_invalid",
            status_code=409,
        )
    selected = list(policy.recommended_scopes)
    if request_refresh_token:
        if not policy.offline_access_available:
            raise RemoteOAuthError(
                "授权服务器未明确声明 offline_access。",
                code="mcp_remote_oauth_refresh_unavailable",
                status_code=409,
            )
        selected.append("offline_access")
    clean = tuple(sorted(set(selected)))
    if len(clean) > MAX_SELECTED_SCOPES:
        raise RemoteOAuthError(
            "OAuth 推荐 Scope 超出上限。",
            code="mcp_remote_oauth_scope_invalid",
            status_code=409,
        )
    return clean


def _token_payload(
    value: Any,
    *,
    requested_scopes: tuple[str, ...],
    previous_refresh_token: str = "",
    allow_refresh_token: bool = False,
) -> tuple[dict[str, str], tuple[str, ...], float | None]:
    if not isinstance(value, dict):
        raise RemoteOAuthError(
            "OAuth token 响应无效。",
            code="mcp_remote_oauth_token_response_invalid",
            status_code=502,
        )
    raw_access_token = value.get("access_token")
    raw_refresh_token = value.get("refresh_token", previous_refresh_token)
    raw_token_type = value.get("token_type")
    if (
        not isinstance(raw_access_token, str)
        or not isinstance(raw_refresh_token, str)
        or not isinstance(raw_token_type, str)
    ):
        raise RemoteOAuthError(
            "OAuth token 响应类型无效。",
            code="mcp_remote_oauth_token_response_invalid",
            status_code=502,
        )
    access_token = raw_access_token
    refresh_token = raw_refresh_token if allow_refresh_token else ""
    token_type = raw_token_type
    if (
        not access_token
        or len(access_token) > 20_000
        or token_type.lower() != "bearer"
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in access_token)
        or len(refresh_token) > 20_000
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in refresh_token)
    ):
        raise RemoteOAuthError(
            "OAuth token 响应不满足 Bearer 边界。",
            code="mcp_remote_oauth_token_response_invalid",
            status_code=502,
        )
    raw_scope = value.get("scope")
    if raw_scope is not None and not isinstance(raw_scope, str):
        raise RemoteOAuthError(
            "OAuth Scope 响应类型无效。",
            code="mcp_remote_oauth_token_response_invalid",
            status_code=502,
        )
    granted = (
        requested_scopes
        if raw_scope is None or not str(raw_scope).strip()
        else tuple(sorted(set(str(raw_scope).split())))
    )
    if (
        len(granted) > MAX_SELECTED_SCOPES
        or len(set(granted)) != len(granted)
        or any(SCOPE_RE.fullmatch(scope) is None for scope in granted)
    ):
        raise RemoteOAuthError(
            "授权服务器返回了无效 Scope。",
            code="mcp_remote_oauth_token_response_invalid",
            status_code=502,
        )
    if requested_scopes and not set(granted).issubset(requested_scopes):
        raise RemoteOAuthError(
            "授权服务器返回了未批准的 Scope。",
            code="mcp_remote_oauth_scope_escalation_denied",
            status_code=409,
        )
    expires_at: float | None = None
    if value.get("expires_in") is not None:
        raw_expires = value["expires_in"]
        if isinstance(raw_expires, bool):
            raise RemoteOAuthError(
                "OAuth token 有效期无效。",
                code="mcp_remote_oauth_token_response_invalid",
                status_code=502,
            )
        try:
            expires_in = int(raw_expires)
        except (TypeError, ValueError) as exc:
            raise RemoteOAuthError(
                "OAuth token 有效期无效。",
                code="mcp_remote_oauth_token_response_invalid",
                status_code=502,
            ) from exc
        if expires_in < 1 or expires_in > 366 * 24 * 60 * 60:
            raise RemoteOAuthError(
                "OAuth token 有效期超出限制。",
                code="mcp_remote_oauth_token_response_invalid",
                status_code=502,
            )
        expires_at = time.time() + expires_in
    return (
        {"access_token": access_token, "refresh_token": refresh_token},
        granted,
        expires_at,
    )


class RemoteOAuthAuthorizationSessionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(pattern=r"^mcpoauthsession_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: OAuthTargetType
    target_id: str
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration_id: str
    registration_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    pkce_credential_id: str
    token_credential_id: str
    scopes: tuple[str, ...]
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_source: str = "legacy"
    resource_uri: str = ""
    resource_digest: str = ""
    protocol_version: str = ""
    request_refresh_token: bool = False
    status: Literal[
        "pending",
        "started",
        "completed",
        "failed",
        "cancelled",
        "expired",
        "unknown_outcome",
    ]
    error_code: str = ""
    token_id: str = ""
    created_at: float = Field(ge=0)
    expires_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)


class RemoteOAuthTokenRevisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str = Field(pattern=r"^mcpoauthtoken_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: OAuthTargetType
    target_id: str
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration_id: str
    registration_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_id: str
    revision: int = Field(ge=1)
    scopes: tuple[str, ...]
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_source: str = "legacy"
    resource_uri: str = ""
    resource_digest: str = ""
    protocol_version: str = ""
    resource_bound: bool = False
    expires_at: float | None = Field(default=None, ge=0)
    refresh_available: bool
    status: Literal[
        "active", "revoked", "stale", "unknown_outcome", "legacy_unbound"
    ]
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)
    revoked_at: float | None = Field(default=None, ge=0)


class RemoteOAuthRefreshAttemptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(pattern=r"^mcpoauthrefresh_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: OAuthTargetType
    target_id: str
    token_id: str = Field(pattern=r"^mcpoauthtoken_[0-9a-f]{32}$")
    expected_revision: int = Field(ge=1)
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration_id: str
    registration_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["started", "completed", "failed", "unknown_outcome"]
    error_code: str = ""
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)


class RemoteOAuthRevocationAttemptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(pattern=r"^mcpoauthrevoke_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: OAuthTargetType
    target_id: str
    token_id: str = Field(pattern=r"^mcpoauthtoken_[0-9a-f]{32}$")
    expected_revision: int = Field(ge=1)
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration_id: str
    registration_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revocation_endpoint_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_type_hint: Literal["access_token", "refresh_token"]
    status: Literal[
        "started", "completed", "failed", "unknown_outcome", "local_only"
    ]
    error_code: str = ""
    created_at: float = Field(ge=0)
    updated_at: float = Field(ge=0)


class RemoteOAuthExecutionMetadataV1(BaseModel):
    """Secret-free, short-lived execution identity for Review Factory.

    This object is safe to persist in bounded evidence.  The corresponding
    Bearer value is resolved only inside ``resolve_for_execution``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["remote-oauth-execution-metadata-v1"] = (
        "remote-oauth-execution-metadata-v1"
    )
    target_type: OAuthTargetType
    target_id: str
    origin: str
    resource_uri: str
    resource_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_source: str
    scopes: tuple[str, ...]
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_version: Literal[MCP_PROTOCOL_VERSION] = MCP_PROTOCOL_VERSION
    expires_at: float | None = Field(default=None, ge=0)


@dataclass
class RemoteOAuthExecutionEnvelope:
    metadata: RemoteOAuthExecutionMetadataV1
    authorization_value: str = field(repr=False)


class MCPRemoteOAuthAuthorizationStore:
    def __init__(self, storage_dir: str | Path) -> None:
        self.path = Path(storage_dir) / "remote-auth.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS remote_oauth_authorization_sessions (
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
                    scope_source TEXT NOT NULL DEFAULT 'legacy',
                    resource_uri TEXT NOT NULL DEFAULT '',
                    resource_digest TEXT NOT NULL DEFAULT '',
                    protocol_version TEXT NOT NULL DEFAULT '',
                    request_refresh_token INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_oauth_one_pending_authorization
                    ON remote_oauth_authorization_sessions(
                        tenant_id,owner_id,target_type,target_id
                    ) WHERE status IN ('pending','started');
                CREATE INDEX IF NOT EXISTS remote_oauth_authorization_state
                    ON remote_oauth_authorization_sessions(state_digest,status);
                CREATE TABLE IF NOT EXISTS remote_oauth_token_revisions (
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
                    scope_source TEXT NOT NULL DEFAULT 'legacy',
                    resource_uri TEXT NOT NULL DEFAULT '',
                    resource_digest TEXT NOT NULL DEFAULT '',
                    protocol_version TEXT NOT NULL DEFAULT '',
                    resource_bound INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL,
                    refresh_available INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_oauth_one_active_token
                    ON remote_oauth_token_revisions(
                        tenant_id,owner_id,target_type,target_id
                    ) WHERE status='active';
                CREATE TABLE IF NOT EXISTS remote_oauth_refresh_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    subject_mode TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL,
                    discovery_fingerprint TEXT NOT NULL,
                    registration_id TEXT NOT NULL,
                    registration_revision INTEGER NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_oauth_one_started_refresh
                    ON remote_oauth_refresh_attempts(
                        tenant_id,owner_id,token_id,expected_revision
                    ) WHERE status='started';
                CREATE TABLE IF NOT EXISTS remote_oauth_revocation_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    subject_mode TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL,
                    discovery_fingerprint TEXT NOT NULL,
                    registration_id TEXT NOT NULL,
                    registration_revision INTEGER NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    revocation_endpoint_digest TEXT NOT NULL,
                    token_type_hint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_oauth_one_started_revocation
                    ON remote_oauth_revocation_attempts(
                        tenant_id,owner_id,token_id,expected_revision
                    ) WHERE status='started';
                """
            )
            db.execute(
                "UPDATE remote_oauth_authorization_sessions SET "
                "status='unknown_outcome',error_code="
                "'mcp_remote_oauth_token_exchange_unknown_outcome',updated_at=? "
                "WHERE status='started'",
                (time.time(),),
            )
            now = time.time()
            db.execute(
                "UPDATE remote_oauth_token_revisions SET status='unknown_outcome',"
                "revision=revision+1,updated_at=? WHERE status='active' AND token_id IN "
                "(SELECT token_id FROM remote_oauth_refresh_attempts WHERE status='started')",
                (now,),
            )
            db.execute(
                "UPDATE remote_oauth_refresh_attempts SET status='unknown_outcome',"
                "error_code='mcp_remote_oauth_refresh_unknown_outcome',updated_at=? "
                "WHERE status='started'",
                (now,),
            )
            db.execute(
                "UPDATE remote_oauth_revocation_attempts SET status='unknown_outcome',"
                "error_code='mcp_remote_oauth_revocation_unknown_outcome',updated_at=? "
                "WHERE status='started'",
                (now,),
            )
            columns = {
                str(row[1])
                for row in db.execute(
                    "PRAGMA table_info(remote_oauth_authorization_sessions)"
                ).fetchall()
            }
            if "token_credential_id" not in columns:
                db.execute(
                    "ALTER TABLE remote_oauth_authorization_sessions "
                    "ADD COLUMN token_credential_id TEXT NOT NULL DEFAULT ''"
                )
            session_columns = {
                str(row[1])
                for row in db.execute(
                    "PRAGMA table_info(remote_oauth_authorization_sessions)"
                ).fetchall()
            }
            for name, declaration in (
                ("scope_source", "TEXT NOT NULL DEFAULT 'legacy'"),
                ("resource_uri", "TEXT NOT NULL DEFAULT ''"),
                ("resource_digest", "TEXT NOT NULL DEFAULT ''"),
                ("protocol_version", "TEXT NOT NULL DEFAULT ''"),
                ("request_refresh_token", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in session_columns:
                    db.execute(
                        f"ALTER TABLE remote_oauth_authorization_sessions "
                        f"ADD COLUMN {name} {declaration}"
                    )
            token_columns = {
                str(row[1])
                for row in db.execute(
                    "PRAGMA table_info(remote_oauth_token_revisions)"
                ).fetchall()
            }
            for name, declaration in (
                ("scope_source", "TEXT NOT NULL DEFAULT 'legacy'"),
                ("resource_uri", "TEXT NOT NULL DEFAULT ''"),
                ("resource_digest", "TEXT NOT NULL DEFAULT ''"),
                ("protocol_version", "TEXT NOT NULL DEFAULT ''"),
                ("resource_bound", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in token_columns:
                    db.execute(
                        f"ALTER TABLE remote_oauth_token_revisions "
                        f"ADD COLUMN {name} {declaration}"
                    )
            now = time.time()
            db.execute(
                "UPDATE remote_oauth_authorization_sessions SET status='failed',"
                "error_code='mcp_remote_oauth_legacy_token_reauthorization_required',"
                "updated_at=? WHERE status='pending' AND protocol_version=''",
                (now,),
            )
            db.execute(
                "UPDATE remote_oauth_token_revisions SET status='legacy_unbound',"
                "updated_at=? WHERE status='active' AND resource_bound=0",
                (now,),
            )

    def ready(self) -> bool:
        try:
            with self._lock, self._connect() as db:
                return bool(db.execute("SELECT 1").fetchone())
        except sqlite3.Error:
            return False

    def create_session(
        self,
        *,
        subject: SubjectScopeV1,
        discovery: RemoteOAuthDiscoverySnapshotV1,
        registration: RemoteOAuthClientRegistrationV1,
        state_digest: str,
        pkce_credential_id: str,
        token_credential_id: str,
        scopes: tuple[str, ...],
        scope_source: str,
        resource_uri: str,
        resource_digest: str,
        protocol_version: str,
        request_refresh_token: bool,
    ) -> tuple[RemoteOAuthAuthorizationSessionV1, list[str]]:
        now = time.time()
        value = RemoteOAuthAuthorizationSessionV1(
            session_id=f"mcpoauthsession_{uuid.uuid4().hex}",
            subject=subject,
            target_type=discovery.target_type,
            target_id=discovery.target_id,
            source_digest=discovery.source_digest,
            discovery_fingerprint=discovery.discovery_fingerprint,
            registration_id=registration.registration_id,
            registration_revision=registration.revision,
            policy_fingerprint=discovery.policy.policy_fingerprint,
            state_digest=state_digest,
            pkce_credential_id=pkce_credential_id,
            token_credential_id=token_credential_id,
            scopes=scopes,
            scope_digest=_digest(list(scopes)),
            scope_source=scope_source,
            resource_uri=resource_uri,
            resource_digest=resource_digest,
            protocol_version=protocol_version,
            request_refresh_token=request_refresh_token,
            status="pending",
            created_at=now,
            expires_at=now + SESSION_TTL_SECONDS,
            updated_at=now,
        )
        expired_credential_ids: list[str] = []
        with self._lock, self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                expired_rows = db.execute(
                    "SELECT pkce_credential_id,token_credential_id FROM "
                    "remote_oauth_authorization_sessions WHERE tenant_id=? AND "
                    "owner_id=? AND target_type=? AND target_id=? AND status='pending' "
                    "AND expires_at<=?",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        discovery.target_type,
                        discovery.target_id,
                        now,
                    ),
                ).fetchall()
                expired_credential_ids = [
                    str(row[key])
                    for row in expired_rows
                    for key in ("pkce_credential_id", "token_credential_id")
                    if str(row[key] or "")
                ]
                db.execute(
                    "UPDATE remote_oauth_authorization_sessions SET status='expired',"
                    "error_code='mcp_remote_oauth_authorization_session_expired',updated_at=? "
                    "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                    "AND status='pending' AND expires_at<=?",
                    (
                        now,
                        subject.tenant_id,
                        subject.owner_id,
                        discovery.target_type,
                        discovery.target_id,
                        now,
                    ),
                )
                active_discovery = db.execute(
                    "SELECT source_digest,discovery_fingerprint,policy_json FROM "
                    "remote_oauth_discoveries WHERE tenant_id=? AND owner_id=? "
                    "AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        discovery.target_type,
                        discovery.target_id,
                    ),
                ).fetchone()
                active_registration = db.execute(
                    "SELECT registration_id,revision,discovery_fingerprint FROM "
                    "remote_oauth_registrations WHERE tenant_id=? AND owner_id=? "
                    "AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        discovery.target_type,
                        discovery.target_id,
                    ),
                ).fetchone()
                active_token = db.execute(
                    "SELECT 1 FROM remote_oauth_token_revisions WHERE tenant_id=? "
                    "AND owner_id=? AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        discovery.target_type,
                        discovery.target_id,
                    ),
                ).fetchone()
                if (
                    active_discovery is None
                    or active_registration is None
                    or active_discovery["source_digest"] != discovery.source_digest
                    or active_discovery["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                    or active_discovery["policy_json"] != discovery.policy.model_dump_json()
                    or active_registration["registration_id"]
                    != registration.registration_id
                    or active_registration["revision"] != registration.revision
                    or active_registration["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                ):
                    raise RemoteOAuthError(
                        "OAuth 发现或客户端登记已漂移。",
                        code="mcp_remote_oauth_discovery_stale",
                        status_code=409,
                    )
                if active_token is not None:
                    raise RemoteOAuthError(
                        "该候选已有有效 OAuth token，请先本地撤销。",
                        code="mcp_remote_oauth_token_conflict",
                        status_code=409,
                    )
                db.execute(
                    "INSERT INTO remote_oauth_authorization_sessions("
                    "session_id,tenant_id,owner_id,subject_mode,target_type,target_id,"
                    "source_digest,discovery_fingerprint,registration_id,registration_revision,"
                    "policy_fingerprint,state_digest,pkce_credential_id,token_credential_id,"
                    "scopes_json,scope_digest,scope_source,resource_uri,resource_digest,"
                    "protocol_version,request_refresh_token,"
                    "status,error_code,token_id,created_at,expires_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        value.session_id,
                        subject.tenant_id,
                        subject.owner_id,
                        subject.mode,
                        value.target_type,
                        value.target_id,
                        value.source_digest,
                        value.discovery_fingerprint,
                        value.registration_id,
                        value.registration_revision,
                        value.policy_fingerprint,
                        value.state_digest,
                        value.pkce_credential_id,
                        value.token_credential_id,
                        _json(list(scopes)).decode("utf-8"),
                        value.scope_digest,
                        value.scope_source,
                        value.resource_uri,
                        value.resource_digest,
                        value.protocol_version,
                        int(value.request_refresh_token),
                        value.status,
                        "",
                        "",
                        value.created_at,
                        value.expires_at,
                        value.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteOAuthError(
                    "该候选已有待完成的 OAuth 授权。",
                    code="mcp_remote_oauth_authorization_session_conflict",
                    status_code=409,
                ) from exc
        return value, expired_credential_ids

    def latest_session(
        self, *, subject: SubjectScopeV1, target_type: OAuthTargetType, target_id: str
    ) -> RemoteOAuthAuthorizationSessionV1 | None:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM remote_oauth_authorization_sessions WHERE tenant_id=? "
                "AND owner_id=? AND target_type=? AND target_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (subject.tenant_id, subject.owner_id, target_type, target_id),
            ).fetchone()
            if row is not None and row["status"] == "pending" and row["expires_at"] <= time.time():
                db.execute(
                    "UPDATE remote_oauth_authorization_sessions SET status='expired',"
                    "error_code='mcp_remote_oauth_authorization_session_expired',updated_at=? "
                    "WHERE session_id=? AND status='pending'",
                    (time.time(), row["session_id"]),
                )
                row = db.execute(
                    "SELECT * FROM remote_oauth_authorization_sessions WHERE session_id=?",
                    (row["session_id"],),
                ).fetchone()
        return self._session(row) if row is not None else None

    def session(
        self, session_id: str, *, subject: SubjectScopeV1
    ) -> RemoteOAuthAuthorizationSessionV1:
        if SESSION_ID_RE.fullmatch(session_id) is None:
            raise RemoteOAuthError(
                "OAuth 授权会话不存在。",
                code="mcp_remote_oauth_authorization_session_missing",
                status_code=404,
            )
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_authorization_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise RemoteOAuthError(
                "OAuth 授权会话不存在。",
                code="mcp_remote_oauth_authorization_session_missing",
                status_code=404,
            )
        value = self._session(row)
        self._scope(value.subject, subject)
        return value

    def session_for_state(
        self, state_digest: str, *, subject: SubjectScopeV1
    ) -> RemoteOAuthAuthorizationSessionV1 | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_authorization_sessions WHERE state_digest=?",
                (state_digest,),
            ).fetchone()
        if row is None:
            return None
        value = self._session(row)
        self._scope(value.subject, subject)
        return value

    def claim_state(
        self, state_digest: str, *, subject: SubjectScopeV1
    ) -> RemoteOAuthAuthorizationSessionV1:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM remote_oauth_authorization_sessions WHERE state_digest=?",
                (state_digest,),
            ).fetchone()
            if row is None:
                raise RemoteOAuthError(
                    "OAuth state 不存在或已失效。",
                    code="mcp_remote_oauth_state_invalid",
                    status_code=400,
                )
            value = self._session(row)
            self._scope(value.subject, subject)
            if value.status != "pending":
                raise RemoteOAuthError(
                    "OAuth state 已使用，禁止重放。",
                    code="mcp_remote_oauth_state_replay_denied",
                    status_code=409,
                )
            now = time.time()
            if value.expires_at <= now:
                db.execute(
                    "UPDATE remote_oauth_authorization_sessions SET status='expired',"
                    "error_code='mcp_remote_oauth_authorization_session_expired',updated_at=? "
                    "WHERE session_id=?",
                    (now, value.session_id),
                )
                raise RemoteOAuthError(
                    "OAuth 授权会话已过期。",
                    code="mcp_remote_oauth_authorization_session_expired",
                    status_code=409,
                )
            changed = db.execute(
                "UPDATE remote_oauth_authorization_sessions SET status='started',updated_at=? "
                "WHERE session_id=? AND status='pending'",
                (now, value.session_id),
            )
            if changed.rowcount != 1:
                raise RemoteOAuthError(
                    "OAuth state 已使用，禁止重放。",
                    code="mcp_remote_oauth_state_replay_denied",
                    status_code=409,
                )
            row = db.execute(
                "SELECT * FROM remote_oauth_authorization_sessions WHERE session_id=?",
                (value.session_id,),
            ).fetchone()
        return self._session(row)

    def finish_session(
        self,
        session_id: str,
        *,
        subject: SubjectScopeV1,
        status: Literal["failed", "cancelled", "unknown_outcome"],
        error_code: str,
    ) -> RemoteOAuthAuthorizationSessionV1:
        current = self.session(session_id, subject=subject)
        # A dispatched code exchange cannot be cancelled safely: the remote
        # authorization server may already have issued a token.  Only a
        # session that has not left ``pending`` may become ``cancelled``.
        if status == "cancelled":
            allowed = ("pending",)
        elif status == "failed":
            allowed = ("pending", "started")
        else:
            allowed = ("started",)
        with self._lock, self._connect() as db:
            placeholders = ",".join("?" for _ in allowed)
            changed = db.execute(
                f"UPDATE remote_oauth_authorization_sessions SET status=?,error_code=?,"
                f"updated_at=? WHERE session_id=? AND status IN ({placeholders})",
                (status, error_code[:160], time.time(), session_id, *allowed),
            )
            if changed.rowcount != 1:
                raise RemoteOAuthError(
                    "OAuth 授权会话已完成。",
                    code="mcp_remote_oauth_state_replay_denied",
                    status_code=409,
                )
            row = db.execute(
                "SELECT * FROM remote_oauth_authorization_sessions WHERE session_id=?",
                (current.session_id,),
            ).fetchone()
        return self._session(row)

    def complete(
        self,
        session_id: str,
        *,
        subject: SubjectScopeV1,
        credential_id: str,
        scopes: tuple[str, ...],
        expires_at: float | None,
        refresh_available: bool,
    ) -> RemoteOAuthTokenRevisionV1:
        session = self.session(session_id, subject=subject)
        if credential_id != session.token_credential_id:
            raise RemoteOAuthError(
                "OAuth token 凭据引用与预留位不一致。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            )
        now = time.time()
        token = RemoteOAuthTokenRevisionV1(
            token_id=f"mcpoauthtoken_{uuid.uuid4().hex}",
            subject=subject,
            target_type=session.target_type,
            target_id=session.target_id,
            discovery_fingerprint=session.discovery_fingerprint,
            registration_id=session.registration_id,
            registration_revision=session.registration_revision,
            policy_fingerprint=session.policy_fingerprint,
            credential_id=credential_id,
            revision=1,
            scopes=scopes,
            scope_digest=_digest(list(scopes)),
            scope_source=session.scope_source,
            resource_uri=session.resource_uri,
            resource_digest=session.resource_digest,
            protocol_version=session.protocol_version,
            resource_bound=(
                session.protocol_version == MCP_PROTOCOL_VERSION
                and bool(session.resource_uri)
                and session.resource_digest
                == hashlib.sha256(session.resource_uri.encode("utf-8")).hexdigest()
            ),
            expires_at=expires_at,
            refresh_available=refresh_available,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT * FROM remote_oauth_authorization_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                active_discovery = db.execute(
                    "SELECT source_digest,discovery_fingerprint,policy_json FROM "
                    "remote_oauth_discoveries WHERE tenant_id=? AND owner_id=? "
                    "AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        session.target_type,
                        session.target_id,
                    ),
                ).fetchone()
                active_registration = db.execute(
                    "SELECT registration_id,revision,discovery_fingerprint FROM "
                    "remote_oauth_registrations WHERE tenant_id=? AND owner_id=? "
                    "AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        session.target_type,
                        session.target_id,
                    ),
                ).fetchone()
                if (
                    current is None
                    or current["status"] != "started"
                    or active_discovery is None
                    or active_registration is None
                    or active_discovery["source_digest"] != session.source_digest
                    or active_discovery["discovery_fingerprint"]
                    != session.discovery_fingerprint
                    or active_registration["registration_id"]
                    != session.registration_id
                    or active_registration["revision"]
                    != session.registration_revision
                    or active_registration["discovery_fingerprint"]
                    != session.discovery_fingerprint
                ):
                    raise RemoteOAuthError(
                        "OAuth 授权证据已漂移或会话已完成。",
                        code="mcp_remote_oauth_discovery_stale",
                        status_code=409,
                    )
                db.execute(
                    "INSERT INTO remote_oauth_token_revisions("
                    "token_id,tenant_id,owner_id,subject_mode,target_type,target_id,"
                    "discovery_fingerprint,registration_id,registration_revision,"
                    "policy_fingerprint,credential_id,revision,scopes_json,scope_digest,"
                    "scope_source,resource_uri,resource_digest,protocol_version,resource_bound,"
                    "expires_at,refresh_available,status,created_at,updated_at,revoked_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        token.token_id,
                        subject.tenant_id,
                        subject.owner_id,
                        subject.mode,
                        token.target_type,
                        token.target_id,
                        token.discovery_fingerprint,
                        token.registration_id,
                        token.registration_revision,
                        token.policy_fingerprint,
                        token.credential_id,
                        token.revision,
                        _json(list(scopes)).decode("utf-8"),
                        token.scope_digest,
                        token.scope_source,
                        token.resource_uri,
                        token.resource_digest,
                        token.protocol_version,
                        int(token.resource_bound),
                        token.expires_at,
                        int(token.refresh_available),
                        token.status,
                        token.created_at,
                        token.updated_at,
                        token.revoked_at,
                    ),
                )
                db.execute(
                    "UPDATE remote_oauth_authorization_sessions SET status='completed',"
                    "token_id=?,updated_at=? WHERE session_id=? AND status='started'",
                    (token.token_id, now, session_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteOAuthError(
                    "该候选已有有效 OAuth token。",
                    code="mcp_remote_oauth_token_conflict",
                    status_code=409,
                ) from exc
        return token

    def active_token(
        self, *, subject: SubjectScopeV1, target_type: OAuthTargetType, target_id: str
    ) -> RemoteOAuthTokenRevisionV1 | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE tenant_id=? "
                "AND owner_id=? AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, target_id),
            ).fetchone()
        return self._token(row) if row is not None else None

    def latest_token(
        self, *, subject: SubjectScopeV1, target_type: OAuthTargetType, target_id: str
    ) -> RemoteOAuthTokenRevisionV1 | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE tenant_id=? "
                "AND owner_id=? AND target_type=? AND target_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (subject.tenant_id, subject.owner_id, target_type, target_id),
            ).fetchone()
        return self._token(row) if row is not None else None

    def claim_refresh(
        self,
        *,
        subject: SubjectScopeV1,
        token: RemoteOAuthTokenRevisionV1,
        discovery: RemoteOAuthDiscoverySnapshotV1,
        registration: RemoteOAuthClientRegistrationV1,
    ) -> RemoteOAuthRefreshAttemptV1:
        now = time.time()
        attempt = RemoteOAuthRefreshAttemptV1(
            attempt_id=f"mcpoauthrefresh_{uuid.uuid4().hex}",
            subject=subject,
            target_type=token.target_type,
            target_id=token.target_id,
            token_id=token.token_id,
            expected_revision=token.revision,
            discovery_fingerprint=discovery.discovery_fingerprint,
            registration_id=registration.registration_id,
            registration_revision=registration.revision,
            policy_fingerprint=discovery.policy.policy_fingerprint,
            status="started",
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                token_row = db.execute(
                    "SELECT * FROM remote_oauth_token_revisions WHERE token_id=? "
                    "AND tenant_id=? AND owner_id=? AND status='active'",
                    (token.token_id, subject.tenant_id, subject.owner_id),
                ).fetchone()
                discovery_row = db.execute(
                    "SELECT discovery_fingerprint,policy_json FROM remote_oauth_discoveries "
                    "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                    "AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        token.target_type,
                        token.target_id,
                    ),
                ).fetchone()
                registration_row = db.execute(
                    "SELECT registration_id,revision,discovery_fingerprint FROM "
                    "remote_oauth_registrations WHERE tenant_id=? AND owner_id=? "
                    "AND target_type=? AND target_id=? AND status='active'",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        token.target_type,
                        token.target_id,
                    ),
                ).fetchone()
                if (
                    token_row is None
                    or token_row["revision"] != token.revision
                    or token_row["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                    or token_row["registration_id"] != registration.registration_id
                    or token_row["registration_revision"] != registration.revision
                    or token_row["policy_fingerprint"]
                    != discovery.policy.policy_fingerprint
                    or discovery_row is None
                    or discovery_row["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                    or discovery_row["policy_json"]
                    != discovery.policy.model_dump_json()
                    or registration_row is None
                    or registration_row["registration_id"]
                    != registration.registration_id
                    or registration_row["revision"] != registration.revision
                    or registration_row["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                ):
                    raise RemoteOAuthError(
                        "OAuth token revision 或发现证据已变化。",
                        code="mcp_remote_oauth_token_stale",
                        status_code=409,
                    )
                db.execute(
                    "INSERT INTO remote_oauth_refresh_attempts("
                    "attempt_id,tenant_id,owner_id,subject_mode,target_type,target_id,"
                    "token_id,expected_revision,discovery_fingerprint,registration_id,"
                    "registration_revision,policy_fingerprint,status,error_code,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        attempt.attempt_id,
                        subject.tenant_id,
                        subject.owner_id,
                        subject.mode,
                        attempt.target_type,
                        attempt.target_id,
                        attempt.token_id,
                        attempt.expected_revision,
                        attempt.discovery_fingerprint,
                        attempt.registration_id,
                        attempt.registration_revision,
                        attempt.policy_fingerprint,
                        attempt.status,
                        "",
                        attempt.created_at,
                        attempt.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteOAuthError(
                    "该 OAuth token revision 正在刷新。",
                    code="mcp_remote_oauth_refresh_in_progress",
                    status_code=409,
                ) from exc
        return attempt

    def complete_refresh(
        self,
        attempt_id: str,
        *,
        subject: SubjectScopeV1,
        scopes: tuple[str, ...],
        expires_at: float | None,
        refresh_available: bool,
    ) -> RemoteOAuthTokenRevisionV1:
        attempt = self.refresh_attempt(attempt_id, subject=subject)
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current_attempt = db.execute(
                "SELECT * FROM remote_oauth_refresh_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            token_row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE token_id=?",
                (attempt.token_id,),
            ).fetchone()
            discovery_row = db.execute(
                "SELECT discovery_fingerprint,policy_json FROM remote_oauth_discoveries "
                "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND status='active'",
                (
                    subject.tenant_id,
                    subject.owner_id,
                    attempt.target_type,
                    attempt.target_id,
                ),
            ).fetchone()
            registration_row = db.execute(
                "SELECT registration_id,revision,discovery_fingerprint FROM "
                "remote_oauth_registrations WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (
                    subject.tenant_id,
                    subject.owner_id,
                    attempt.target_type,
                    attempt.target_id,
                ),
            ).fetchone()
            if (
                current_attempt is None
                or current_attempt["status"] != "started"
                or token_row is None
                or token_row["status"] != "active"
                or token_row["revision"] != attempt.expected_revision
                or token_row["discovery_fingerprint"]
                != attempt.discovery_fingerprint
                or token_row["registration_id"] != attempt.registration_id
                or token_row["registration_revision"]
                != attempt.registration_revision
                or token_row["policy_fingerprint"] != attempt.policy_fingerprint
                or discovery_row is None
                or discovery_row["discovery_fingerprint"]
                != attempt.discovery_fingerprint
                or json.loads(discovery_row["policy_json"]).get("policy_fingerprint")
                != attempt.policy_fingerprint
                or registration_row is None
                or registration_row["registration_id"] != attempt.registration_id
                or registration_row["revision"] != attempt.registration_revision
                or registration_row["discovery_fingerprint"]
                != attempt.discovery_fingerprint
            ):
                raise RemoteOAuthError(
                    "OAuth token 刷新结果无法原子提交。",
                    code="mcp_remote_oauth_refresh_unknown_outcome",
                    status_code=409,
                )
            changed = db.execute(
                "UPDATE remote_oauth_token_revisions SET revision=revision+1,"
                "scopes_json=?,scope_digest=?,expires_at=?,refresh_available=?,"
                "updated_at=? WHERE token_id=? AND status='active' AND revision=?",
                (
                    _json(list(scopes)).decode("utf-8"),
                    _digest(list(scopes)),
                    expires_at,
                    int(refresh_available),
                    now,
                    attempt.token_id,
                    attempt.expected_revision,
                ),
            )
            if changed.rowcount != 1:
                raise RemoteOAuthError(
                    "OAuth token 刷新结果无法原子提交。",
                    code="mcp_remote_oauth_refresh_unknown_outcome",
                    status_code=409,
                )
            db.execute(
                "UPDATE remote_oauth_refresh_attempts SET status='completed',"
                "updated_at=? WHERE attempt_id=? AND status='started'",
                (now, attempt_id),
            )
            row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE token_id=?",
                (attempt.token_id,),
            ).fetchone()
        return self._token(row)

    def finish_refresh_attempt(
        self,
        attempt_id: str,
        *,
        subject: SubjectScopeV1,
        status: Literal["failed", "unknown_outcome"],
        error_code: str,
        token_status: Literal["stale", "unknown_outcome"],
    ) -> RemoteOAuthRefreshAttemptV1:
        attempt = self.refresh_attempt(attempt_id, subject=subject)
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM remote_oauth_refresh_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if current is None:
                raise RemoteOAuthError(
                    "OAuth token 刷新记录不存在。",
                    code="mcp_remote_oauth_refresh_unknown_outcome",
                    status_code=409,
                )
            if current["status"] != "started":
                persisted = self._refresh_attempt(current)
                if persisted.status == "unknown_outcome":
                    return persisted
                raise RemoteOAuthError(
                    "OAuth token 刷新记录已完成。",
                    code="mcp_remote_oauth_refresh_unknown_outcome",
                    status_code=409,
                )
            db.execute(
                "UPDATE remote_oauth_refresh_attempts SET status=?,error_code=?,"
                "updated_at=? WHERE attempt_id=? AND status='started'",
                (status, error_code[:160], now, attempt_id),
            )
            db.execute(
                "UPDATE remote_oauth_token_revisions SET status=?,revision=revision+1,"
                "updated_at=? WHERE token_id=? AND status='active' AND revision=?",
                (
                    token_status,
                    now,
                    attempt.token_id,
                    attempt.expected_revision,
                ),
            )
            row = db.execute(
                "SELECT * FROM remote_oauth_refresh_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        return self._refresh_attempt(row)

    def refresh_attempt(
        self, attempt_id: str, *, subject: SubjectScopeV1
    ) -> RemoteOAuthRefreshAttemptV1:
        if re.fullmatch(r"mcpoauthrefresh_[0-9a-f]{32}", attempt_id) is None:
            raise RemoteOAuthError(
                "OAuth token 刷新记录不存在。",
                code="mcp_remote_oauth_refresh_unknown_outcome",
                status_code=404,
            )
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_refresh_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RemoteOAuthError(
                "OAuth token 刷新记录不存在。",
                code="mcp_remote_oauth_refresh_unknown_outcome",
                status_code=404,
            )
        value = self._refresh_attempt(row)
        self._scope(value.subject, subject)
        return value

    def claim_revocation(
        self,
        *,
        subject: SubjectScopeV1,
        token: RemoteOAuthTokenRevisionV1,
        discovery: RemoteOAuthDiscoverySnapshotV1,
        registration: RemoteOAuthClientRegistrationV1,
        revocation_endpoint_digest: str,
        token_type_hint: Literal["access_token", "refresh_token"],
    ) -> RemoteOAuthRevocationAttemptV1:
        now = time.time()
        attempt = RemoteOAuthRevocationAttemptV1(
            attempt_id=f"mcpoauthrevoke_{uuid.uuid4().hex}",
            subject=subject,
            target_type=token.target_type,
            target_id=token.target_id,
            token_id=token.token_id,
            expected_revision=token.revision,
            discovery_fingerprint=discovery.discovery_fingerprint,
            registration_id=registration.registration_id,
            registration_revision=registration.revision,
            policy_fingerprint=discovery.policy.policy_fingerprint,
            revocation_endpoint_digest=revocation_endpoint_digest,
            token_type_hint=token_type_hint,
            status="started",
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT * FROM remote_oauth_token_revisions WHERE token_id=? "
                    "AND tenant_id=? AND owner_id=? AND status='active'",
                    (token.token_id, subject.tenant_id, subject.owner_id),
                ).fetchone()
                if (
                    current is None
                    or current["revision"] != token.revision
                    or current["discovery_fingerprint"]
                    != discovery.discovery_fingerprint
                    or current["registration_id"] != registration.registration_id
                    or current["registration_revision"] != registration.revision
                    or current["policy_fingerprint"]
                    != discovery.policy.policy_fingerprint
                ):
                    raise RemoteOAuthError(
                        "OAuth token revision 或发现证据已变化。",
                        code="mcp_remote_oauth_token_stale",
                        status_code=409,
                    )
                refresh_started = db.execute(
                    "SELECT 1 FROM remote_oauth_refresh_attempts WHERE tenant_id=? "
                    "AND owner_id=? AND token_id=? AND expected_revision=? "
                    "AND status='started' LIMIT 1",
                    (
                        subject.tenant_id,
                        subject.owner_id,
                        token.token_id,
                        token.revision,
                    ),
                ).fetchone()
                if refresh_started is not None:
                    raise RemoteOAuthError(
                        "该 OAuth token revision 正在刷新，不能并发撤销。",
                        code="mcp_remote_oauth_refresh_in_progress",
                        status_code=409,
                    )
                db.execute(
                    "INSERT INTO remote_oauth_revocation_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        attempt.attempt_id,
                        subject.tenant_id,
                        subject.owner_id,
                        subject.mode,
                        attempt.target_type,
                        attempt.target_id,
                        attempt.token_id,
                        attempt.expected_revision,
                        attempt.discovery_fingerprint,
                        attempt.registration_id,
                        attempt.registration_revision,
                        attempt.policy_fingerprint,
                        attempt.revocation_endpoint_digest,
                        attempt.token_type_hint,
                        attempt.status,
                        "",
                        attempt.created_at,
                        attempt.updated_at,
                    ),
                )
                changed = db.execute(
                    "UPDATE remote_oauth_token_revisions SET status='revoked',"
                    "revision=revision+1,updated_at=?,revoked_at=? WHERE token_id=? "
                    "AND status='active' AND revision=?",
                    (now, now, token.token_id, token.revision),
                )
                if changed.rowcount != 1:
                    raise RemoteOAuthError(
                        "OAuth token 撤销无法原子封锁旧 revision。",
                        code="mcp_remote_oauth_revocation_unknown_outcome",
                        status_code=409,
                    )
            except sqlite3.IntegrityError as exc:
                raise RemoteOAuthError(
                    "该 OAuth token revision 正在撤销。",
                    code="mcp_remote_oauth_revocation_in_progress",
                    status_code=409,
                ) from exc
        return attempt

    def finish_revocation_attempt(
        self,
        attempt_id: str,
        *,
        subject: SubjectScopeV1,
        status: Literal["completed", "failed", "unknown_outcome", "local_only"],
        error_code: str = "",
    ) -> RemoteOAuthRevocationAttemptV1:
        attempt = self.revocation_attempt(attempt_id, subject=subject)
        now = time.time()
        with self._lock, self._connect() as db:
            changed = db.execute(
                "UPDATE remote_oauth_revocation_attempts SET status=?,error_code=?,"
                "updated_at=? WHERE attempt_id=? AND status='started'",
                (status, error_code[:160], now, attempt_id),
            )
            if changed.rowcount != 1:
                current = db.execute(
                    "SELECT * FROM remote_oauth_revocation_attempts WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
                if current is not None and current["status"] == status:
                    return self._revocation_attempt(current)
                raise RemoteOAuthError(
                    "OAuth token 撤销记录已完成。",
                    code="mcp_remote_oauth_revocation_unknown_outcome",
                    status_code=409,
                )
            row = db.execute(
                "SELECT * FROM remote_oauth_revocation_attempts WHERE attempt_id=?",
                (attempt.attempt_id,),
            ).fetchone()
        return self._revocation_attempt(row)

    def revocation_attempt(
        self, attempt_id: str, *, subject: SubjectScopeV1
    ) -> RemoteOAuthRevocationAttemptV1:
        if re.fullmatch(r"mcpoauthrevoke_[0-9a-f]{32}", attempt_id) is None:
            raise RemoteOAuthError(
                "OAuth token 撤销记录不存在。",
                code="mcp_remote_oauth_revocation_unknown_outcome",
                status_code=404,
            )
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_revocation_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RemoteOAuthError(
                "OAuth token 撤销记录不存在。",
                code="mcp_remote_oauth_revocation_unknown_outcome",
                status_code=404,
            )
        value = self._revocation_attempt(row)
        self._scope(value.subject, subject)
        return value

    def rotate_token(
        self,
        token_id: str,
        *,
        subject: SubjectScopeV1,
        expected_revision: int,
        scopes: tuple[str, ...],
        expires_at: float | None,
        refresh_available: bool,
    ) -> RemoteOAuthTokenRevisionV1:
        current = self._token_by_id(token_id, subject=subject)
        if current.status != "active" or current.revision != expected_revision:
            raise RemoteOAuthError(
                "OAuth token revision 已变化。",
                code="mcp_remote_oauth_token_stale",
                status_code=409,
            )
        with self._lock, self._connect() as db:
            changed = db.execute(
                "UPDATE remote_oauth_token_revisions SET revision=revision+1,scopes_json=?,"
                "scope_digest=?,expires_at=?,refresh_available=?,updated_at=? WHERE token_id=? "
                "AND status='active' AND revision=?",
                (
                    _json(list(scopes)).decode("utf-8"),
                    _digest(list(scopes)),
                    expires_at,
                    int(refresh_available),
                    time.time(),
                    token_id,
                    expected_revision,
                ),
            )
            if changed.rowcount != 1:
                raise RemoteOAuthError(
                    "OAuth token revision 已变化。",
                    code="mcp_remote_oauth_token_stale",
                    status_code=409,
                )
            row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE token_id=?",
                (token_id,),
            ).fetchone()
        return self._token(row)

    def set_token_status(
        self,
        token_id: str,
        *,
        subject: SubjectScopeV1,
        status: Literal["revoked", "stale", "unknown_outcome"],
    ) -> RemoteOAuthTokenRevisionV1:
        current = self._token_by_id(token_id, subject=subject)
        if current.status == status:
            return current
        now = time.time()
        with self._lock, self._connect() as db:
            source_statuses = (
                ("active", "legacy_unbound")
                if status == "revoked"
                else ("active",)
            )
            placeholders = ",".join("?" for _ in source_statuses)
            db.execute(
                "UPDATE remote_oauth_token_revisions SET status=?,revision=revision+1,"
                f"updated_at=?,revoked_at=? WHERE token_id=? AND status IN ({placeholders})",
                (
                    status,
                    now,
                    now if status == "revoked" else None,
                    token_id,
                    *source_statuses,
                ),
            )
            row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE token_id=?",
                (token_id,),
            ).fetchone()
        return self._token(row)

    def cancel_target(
        self, *, subject: SubjectScopeV1, target_type: OAuthTargetType, target_id: str
    ) -> tuple[list[str], list[str]]:
        """Fail closed on discovery/registration drift; return vault refs to revoke."""
        now = time.time()
        with self._lock, self._connect() as db:
            session_rows = db.execute(
                "SELECT pkce_credential_id,token_credential_id FROM "
                "remote_oauth_authorization_sessions "
                "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND status IN ('pending','started')",
                (subject.tenant_id, subject.owner_id, target_type, target_id),
            ).fetchall()
            token_rows = db.execute(
                "SELECT credential_id FROM remote_oauth_token_revisions WHERE tenant_id=? "
                "AND owner_id=? AND target_type=? AND target_id=? AND status='active'",
                (subject.tenant_id, subject.owner_id, target_type, target_id),
            ).fetchall()
            refresh_rows = db.execute(
                "SELECT token_id FROM remote_oauth_refresh_attempts WHERE tenant_id=? "
                "AND owner_id=? AND target_type=? AND target_id=? AND status='started'",
                (subject.tenant_id, subject.owner_id, target_type, target_id),
            ).fetchall()
            db.execute(
                "UPDATE remote_oauth_authorization_sessions SET status='cancelled',"
                "error_code='mcp_remote_oauth_discovery_stale',updated_at=? WHERE "
                "tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND status='pending'",
                (now, subject.tenant_id, subject.owner_id, target_type, target_id),
            )
            db.execute(
                "UPDATE remote_oauth_authorization_sessions SET status='unknown_outcome',"
                "error_code='mcp_remote_oauth_token_exchange_unknown_outcome',updated_at=? "
                "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND status='started'",
                (now, subject.tenant_id, subject.owner_id, target_type, target_id),
            )
            db.execute(
                "UPDATE remote_oauth_refresh_attempts SET status='unknown_outcome',"
                "error_code='mcp_remote_oauth_refresh_unknown_outcome',updated_at=? "
                "WHERE tenant_id=? AND owner_id=? AND target_type=? AND target_id=? "
                "AND status='started'",
                (now, subject.tenant_id, subject.owner_id, target_type, target_id),
            )
            refresh_token_ids = [str(row["token_id"]) for row in refresh_rows]
            if refresh_token_ids:
                placeholders = ",".join("?" for _ in refresh_token_ids)
                db.execute(
                    f"UPDATE remote_oauth_token_revisions SET status='unknown_outcome',"
                    f"revision=revision+1,updated_at=? WHERE token_id IN ({placeholders}) "
                    "AND status='active'",
                    (now, *refresh_token_ids),
                )
            db.execute(
                "UPDATE remote_oauth_token_revisions SET status='stale',"
                "revision=revision+1,updated_at=? WHERE tenant_id=? AND owner_id=? "
                "AND target_type=? AND target_id=? AND status='active'",
                (now, subject.tenant_id, subject.owner_id, target_type, target_id),
            )
        return (
            [
                str(row[key])
                for row in session_rows
                for key in ("pkce_credential_id", "token_credential_id")
                if str(row[key] or "")
            ],
            [str(row["credential_id"]) for row in token_rows],
        )

    def recovery_credential_ids(self, *, subject: SubjectScopeV1) -> list[str]:
        """Return only fail-closed credential refs left by interrupted work."""

        with self._lock, self._connect() as db:
            session_rows = db.execute(
                "SELECT pkce_credential_id,token_credential_id FROM "
                "remote_oauth_authorization_sessions WHERE tenant_id=? AND owner_id=? "
                "AND status IN ('failed','cancelled','expired','unknown_outcome')",
                (subject.tenant_id, subject.owner_id),
            ).fetchall()
            token_rows = db.execute(
                "SELECT credential_id FROM remote_oauth_token_revisions WHERE tenant_id=? "
                "AND owner_id=? AND status IN ('revoked','stale','unknown_outcome')",
                (subject.tenant_id, subject.owner_id),
            ).fetchall()
        return list(
            dict.fromkeys(
                [
                    str(row[key])
                    for row in session_rows
                    for key in ("pkce_credential_id", "token_credential_id")
                    if str(row[key] or "")
                ]
                + [str(row["credential_id"]) for row in token_rows]
            )
        )

    def _token_by_id(
        self, token_id: str, *, subject: SubjectScopeV1
    ) -> RemoteOAuthTokenRevisionV1:
        if TOKEN_ID_RE.fullmatch(token_id) is None:
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=404,
            )
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_oauth_token_revisions WHERE token_id=?",
                (token_id,),
            ).fetchone()
        if row is None:
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=404,
            )
        value = self._token(row)
        self._scope(value.subject, subject)
        return value

    @staticmethod
    def _scope(actual: SubjectScopeV1, expected: SubjectScopeV1) -> None:
        if actual != expected:
            raise RemoteOAuthError(
                "OAuth 对象不属于当前主体。",
                code="mcp_remote_oauth_scope_denied",
                status_code=403,
            )

    @staticmethod
    def _session(row: sqlite3.Row) -> RemoteOAuthAuthorizationSessionV1:
        try:
            scopes = json.loads(row["scopes_json"])
            if not isinstance(scopes, list) or _digest(scopes) != row["scope_digest"]:
                raise ValueError("scope digest mismatch")
            return RemoteOAuthAuthorizationSessionV1(
                session_id=row["session_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                source_digest=row["source_digest"],
                discovery_fingerprint=row["discovery_fingerprint"],
                registration_id=row["registration_id"],
                registration_revision=row["registration_revision"],
                policy_fingerprint=row["policy_fingerprint"],
                state_digest=row["state_digest"],
                pkce_credential_id=row["pkce_credential_id"],
                token_credential_id=row["token_credential_id"],
                scopes=tuple(scopes),
                scope_digest=row["scope_digest"],
                scope_source=row["scope_source"],
                resource_uri=row["resource_uri"],
                resource_digest=row["resource_digest"],
                protocol_version=row["protocol_version"],
                request_refresh_token=bool(row["request_refresh_token"]),
                status=row["status"],
                error_code=row["error_code"],
                token_id=row["token_id"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                updated_at=row["updated_at"],
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RemoteOAuthError(
                "OAuth 授权会话存储损坏。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            ) from exc

    @staticmethod
    def _token(row: sqlite3.Row) -> RemoteOAuthTokenRevisionV1:
        try:
            scopes = json.loads(row["scopes_json"])
            if not isinstance(scopes, list) or _digest(scopes) != row["scope_digest"]:
                raise ValueError("scope digest mismatch")
            return RemoteOAuthTokenRevisionV1(
                token_id=row["token_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                discovery_fingerprint=row["discovery_fingerprint"],
                registration_id=row["registration_id"],
                registration_revision=row["registration_revision"],
                policy_fingerprint=row["policy_fingerprint"],
                credential_id=row["credential_id"],
                revision=row["revision"],
                scopes=tuple(scopes),
                scope_digest=row["scope_digest"],
                scope_source=row["scope_source"],
                resource_uri=row["resource_uri"],
                resource_digest=row["resource_digest"],
                protocol_version=row["protocol_version"],
                resource_bound=bool(row["resource_bound"]),
                expires_at=row["expires_at"],
                refresh_available=bool(row["refresh_available"]),
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                revoked_at=row["revoked_at"],
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RemoteOAuthError(
                "OAuth token 存储损坏。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            ) from exc

    @staticmethod
    def _refresh_attempt(row: sqlite3.Row) -> RemoteOAuthRefreshAttemptV1:
        try:
            return RemoteOAuthRefreshAttemptV1(
                attempt_id=row["attempt_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                token_id=row["token_id"],
                expected_revision=row["expected_revision"],
                discovery_fingerprint=row["discovery_fingerprint"],
                registration_id=row["registration_id"],
                registration_revision=row["registration_revision"],
                policy_fingerprint=row["policy_fingerprint"],
                status=row["status"],
                error_code=row["error_code"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (ValueError, TypeError) as exc:
            raise RemoteOAuthError(
                "OAuth token 刷新记录损坏。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            ) from exc

    @staticmethod
    def _revocation_attempt(row: sqlite3.Row) -> RemoteOAuthRevocationAttemptV1:
        try:
            return RemoteOAuthRevocationAttemptV1(
                attempt_id=row["attempt_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                token_id=row["token_id"],
                expected_revision=row["expected_revision"],
                discovery_fingerprint=row["discovery_fingerprint"],
                registration_id=row["registration_id"],
                registration_revision=row["registration_revision"],
                policy_fingerprint=row["policy_fingerprint"],
                revocation_endpoint_digest=row["revocation_endpoint_digest"],
                token_type_hint=row["token_type_hint"],
                status=row["status"],
                error_code=row["error_code"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (ValueError, TypeError) as exc:
            raise RemoteOAuthError(
                "OAuth token 撤销记录损坏。",
                code="mcp_remote_oauth_storage_corrupt",
                status_code=503,
            ) from exc


class MCPRemoteOAuthAuthorizationService:
    def __init__(
        self,
        store: MCPRemoteOAuthAuthorizationStore,
        *,
        metadata_store: MCPRemoteOAuthStore,
        metadata_state: Callable[..., Any] | None = None,
        subject_resolver: SubjectScopeResolver,
        remote_auth_status: Callable[[], dict[str, Any]],
        redirect_uri: Callable[[], str],
        bridge: RemoteOAuthBridgeProtocol,
        credential_creator: Callable[..., Any],
        credential_lookup: Callable[..., Any],
        credential_resolver: Callable[..., str],
        credential_rotator: Callable[..., Any],
        credential_revoker: Callable[..., Any],
    ) -> None:
        self.store = store
        self.metadata_store = metadata_store
        self.metadata_state = metadata_state
        self.subject_resolver = subject_resolver
        self.remote_auth_status = remote_auth_status
        self.redirect_uri = redirect_uri
        self.bridge = bridge
        self.credential_creator = credential_creator
        self.credential_lookup = credential_lookup
        self.credential_resolver = credential_resolver
        self.credential_rotator = credential_rotator
        self.credential_revoker = credential_revoker
        self.target_change_handlers: list[
            Callable[[OAuthTargetType, str], None]
        ] = []
        self._locks = tuple(asyncio.Lock() for _ in range(64))
        subject = self.subject_resolver.resolve()
        for credential_id in self.store.recovery_credential_ids(subject=subject):
            self._revoke_safely(credential_id, subject)

    def set_target_change_handler(
        self, handler: Callable[[OAuthTargetType, str], None]
    ) -> None:
        if handler not in self.target_change_handlers:
            self.target_change_handlers.append(handler)

    def _notify_target_changed(
        self, target_type: OAuthTargetType, target_id: str
    ) -> None:
        for handler in tuple(self.target_change_handlers):
            handler(target_type, target_id)

    def status(self) -> dict[str, Any]:
        configured = all(
            callable(value)
            for value in (
                self.credential_creator,
                self.credential_lookup,
                self.credential_resolver,
                self.credential_rotator,
                self.credential_revoker,
            )
        )
        return {
            "authorization_enabled": _flag(
                "MCP_REMOTE_OAUTH_AUTHORIZATION_ENABLED"
            )
            and configured,
            "token_storage_enabled": _flag(
                "MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED"
            )
            and configured,
            "review_enabled": _flag("MCP_REMOTE_OAUTH_REVIEW_ENABLED"),
            "runtime_enabled": _flag("MCP_REMOTE_OAUTH_RUNTIME_ENABLED"),
            "remote_revocation_enabled": _flag(
                "MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED"
            ),
            "storage_ready": self.store.ready(),
        }

    def _require(self) -> SubjectScopeV1:
        base = self.remote_auth_status()
        state = self.status()
        if not base.get("enabled"):
            raise RemoteOAuthError(
                "远程认证基础尚未启用。",
                code="mcp_remote_auth_disabled",
                status_code=503,
            )
        if not _flag("MCP_REMOTE_OAUTH_ENABLED"):
            raise RemoteOAuthError(
                "OAuth 发现基础尚未启用。",
                code="mcp_remote_oauth_disabled",
                status_code=503,
            )
        if not base.get("single_owner_acknowledged"):
            raise RemoteOAuthError(
                "尚未确认本地单主体边界。",
                code="mcp_remote_auth_single_owner_ack_required",
                status_code=503,
            )
        if not (
            base.get("external_master_key_available")
            and base.get("external_master_key_enforced")
        ):
            raise RemoteOAuthError(
                "外部凭据主密钥不可用。",
                code="mcp_remote_auth_master_key_required",
                status_code=503,
            )
        if not state["authorization_enabled"]:
            raise RemoteOAuthError(
                "OAuth 用户授权当前未启用。",
                code="mcp_remote_oauth_authorization_disabled",
                status_code=503,
            )
        if not state["token_storage_enabled"] or not state["storage_ready"]:
            raise RemoteOAuthError(
                "OAuth token 加密存储当前不可用。",
                code="mcp_remote_oauth_token_storage_disabled",
                status_code=503,
            )
        return self.subject_resolver.resolve()

    def _current(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
    ) -> tuple[RemoteOAuthDiscoverySnapshotV1, RemoteOAuthClientRegistrationV1]:
        if self.metadata_state is not None:
            discovery, registration = self.metadata_state(
                subject=subject, target_type=target_type, target_id=target_id
            )
        else:
            discovery = self.metadata_store.active_discovery(
                subject=subject, target_type=target_type, target_id=target_id
            )
            registration = self.metadata_store.active_registration(
                subject=subject, target_type=target_type, target_id=target_id
            )
        if (
            discovery is None
            or registration is None
            or registration.discovery_fingerprint != discovery.discovery_fingerprint
        ):
            raise RemoteOAuthError(
                "OAuth 发现或客户端登记已漂移。",
                code="mcp_remote_oauth_discovery_stale",
                status_code=409,
            )
        return discovery, registration

    def create_authorization(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
        expected_discovery_fingerprint: str,
        expected_registration_digest: str,
        expected_scope_digest: str,
        request_refresh_token: bool = False,
    ) -> dict[str, Any]:
        subject = self._require()
        discovery, registration = self._current(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if (
            discovery.source_digest != source_digest
            or discovery.discovery_fingerprint != expected_discovery_fingerprint
            or self.registration_revision_digest(registration)
            != expected_registration_digest
        ):
            raise RemoteOAuthError(
                "OAuth 发现或客户端登记已漂移。",
                code="mcp_remote_oauth_discovery_stale",
                status_code=409,
            )
        if not isinstance(discovery.policy, RemoteOAuthPolicyV2):
            raise RemoteOAuthError(
                "旧 OAuth 发现快照必须重新发现后再授权。",
                code="mcp_remote_oauth_legacy_token_reauthorization_required",
                status_code=409,
            )
        selected = _authorization_scopes(
            discovery.policy,
            expected_scope_digest=expected_scope_digest,
            request_refresh_token=request_refresh_token,
        )
        verifier = secrets.token_urlsafe(64)[:86]
        state = secrets.token_urlsafe(48)
        redirect_uri = self.redirect_uri()
        code_challenge = _challenge(verifier)
        pkce_credential_id = f"cred_{uuid.uuid4().hex}"
        token_credential_id = f"cred_{uuid.uuid4().hex}"
        credential: Any = None
        session: RemoteOAuthAuthorizationSessionV1 | None = None
        try:
            session, expired_ids = self.store.create_session(
                subject=subject,
                discovery=discovery,
                registration=registration,
                state_digest=hashlib.sha256(state.encode("ascii")).hexdigest(),
                pkce_credential_id=pkce_credential_id,
                token_credential_id=token_credential_id,
                scopes=selected,
                scope_source=discovery.policy.scope_source,
                resource_uri=discovery.policy.resource_uri,
                resource_digest=hashlib.sha256(
                    discovery.policy.resource_uri.encode("utf-8")
                ).hexdigest(),
                protocol_version=discovery.policy.protocol_version,
                request_refresh_token=request_refresh_token,
            )
            for credential_id in expired_ids:
                self._revoke_safely(credential_id, subject)
            credential, _ = self.credential_creator(
                name=f"OAuth PKCE {target_id}",
                value=verifier,
                credential_id=pkce_credential_id,
                kind="generic",
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
            credential_id = str(getattr(credential, "credential_id", ""))
            if credential_id != pkce_credential_id:
                raise RuntimeError("credential id missing")
        except Exception as exc:
            if credential is not None:
                self._revoke_safely(
                    str(getattr(credential, "credential_id", "")), subject
                )
            if session is not None:
                try:
                    self.store.finish_session(
                        session.session_id,
                        subject=subject,
                        status="failed",
                        error_code="mcp_remote_oauth_token_storage_unavailable",
                    )
                except RemoteOAuthError:
                    pass
            if isinstance(exc, RemoteOAuthError):
                raise
            raise RemoteOAuthError(
                "OAuth PKCE 临时凭据无法安全保存。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            ) from exc
        query: dict[str, str] = {
            "response_type": "code",
            "client_id": registration.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": discovery.policy.resource_uri,
        }
        if selected:
            query["scope"] = " ".join(selected)
        return {
            "authorization_session": self._public_session(session),
            "authorization_url": (
                f"{discovery.policy.authorization_endpoint}?{urlencode(query)}"
            ),
        }

    @staticmethod
    def registration_revision_digest(
        registration: RemoteOAuthClientRegistrationV1,
    ) -> str:
        return _digest(
            {
                "schema_version": registration.schema_version,
                "registration_id": registration.registration_id,
                "revision": registration.revision,
                "discovery_fingerprint": registration.discovery_fingerprint,
                "issuer": registration.issuer,
                "mode": registration.mode,
            }
        )

    async def callback(
        self,
        *,
        state: str,
        code: str = "",
        authorization_error: str = "",
        issuer: str = "",
    ) -> RemoteOAuthAuthorizationSessionV1:
        subject = self._require()
        if STATE_RE.fullmatch(state) is None:
            raise RemoteOAuthError(
                "OAuth state 无效。",
                code="mcp_remote_oauth_state_invalid",
                status_code=400,
            )
        state_digest = hashlib.sha256(state.encode("ascii")).hexdigest()
        try:
            session = self.store.claim_state(state_digest, subject=subject)
        except RemoteOAuthError as exc:
            if exc.code == "mcp_remote_oauth_authorization_session_expired":
                expired = self.store.session_for_state(state_digest, subject=subject)
                if expired is not None:
                    self._revoke_safely(expired.pkce_credential_id, subject)
                    self._revoke_safely(expired.token_credential_id, subject)
            raise
        try:
            discovery, registration = self._current(
                subject=subject,
                target_type=session.target_type,
                target_id=session.target_id,
            )
        except RemoteOAuthError as exc:
            self._revoke_safely(session.pkce_credential_id, subject)
            self.store.finish_session(
                session.session_id,
                subject=subject,
                status="failed",
                error_code=exc.code,
            )
            raise
        if (
            discovery.source_digest != session.source_digest
            or discovery.discovery_fingerprint != session.discovery_fingerprint
            or discovery.policy.policy_fingerprint != session.policy_fingerprint
            or registration.registration_id != session.registration_id
            or registration.revision != session.registration_revision
        ):
            self._revoke_safely(session.pkce_credential_id, subject)
            return self.store.finish_session(
                session.session_id,
                subject=subject,
                status="failed",
                error_code="mcp_remote_oauth_discovery_stale",
            )
        if issuer and issuer != discovery.policy.issuer:
            self._revoke_safely(session.pkce_credential_id, subject)
            return self.store.finish_session(
                session.session_id,
                subject=subject,
                status="failed",
                error_code="mcp_remote_oauth_issuer_mismatch",
            )
        if authorization_error:
            self._revoke_safely(session.pkce_credential_id, subject)
            return self.store.finish_session(
                session.session_id,
                subject=subject,
                status="failed",
                error_code="mcp_remote_oauth_authorization_denied",
            )
        if CODE_RE.fullmatch(code) is None:
            self._revoke_safely(session.pkce_credential_id, subject)
            return self.store.finish_session(
                session.session_id,
                subject=subject,
                status="failed",
                error_code="mcp_remote_oauth_authorization_code_invalid",
            )
        if (
            session.protocol_version != MCP_PROTOCOL_VERSION
            or session.resource_uri != discovery.policy.resource_uri
            or session.resource_digest
            != hashlib.sha256(session.resource_uri.encode("utf-8")).hexdigest()
        ):
            self._revoke_safely(session.pkce_credential_id, subject)
            return self.store.finish_session(
                session.session_id,
                subject=subject,
                status="failed",
                error_code="mcp_remote_oauth_legacy_token_reauthorization_required",
            )
        verifier = ""
        token_credential: Any = None
        bundle: dict[str, str] = {}
        response: dict[str, Any] = {}
        try:
            verifier = self._resolve(session.pkce_credential_id, subject)
            response = await self.bridge.exchange_authorization_code(
                session.target_id,
                discovery.policy.token_endpoint,
                request_body={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": registration.client_id,
                    "redirect_uri": self.redirect_uri(),
                    "code_verifier": verifier,
                    "resource": session.resource_uri,
                },
            )
            bundle, granted, expires_at = _token_payload(
                response,
                requested_scopes=session.scopes,
                allow_refresh_token=session.request_refresh_token,
            )
            token_credential, _ = self.credential_creator(
                name=f"OAuth token {session.target_id}",
                value=_json(bundle).decode("utf-8"),
                credential_id=session.token_credential_id,
                kind="generic",
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
            credential_id = str(getattr(token_credential, "credential_id", ""))
            if not credential_id:
                raise RuntimeError("credential id missing")
            self.store.complete(
                session.session_id,
                subject=subject,
                credential_id=credential_id,
                scopes=granted,
                expires_at=expires_at,
                refresh_available=bool(bundle["refresh_token"]),
            )
            self._notify_target_changed(session.target_type, session.target_id)
            return self.store.session(session.session_id, subject=subject)
        except asyncio.CancelledError:
            self._finish_safely(
                session, subject, "unknown_outcome",
                "mcp_remote_oauth_token_exchange_unknown_outcome",
            )
            raise
        except Exception as exc:
            if token_credential is not None:
                self._revoke_safely(
                    str(getattr(token_credential, "credential_id", "")), subject
                )
            status: Literal["failed", "unknown_outcome"] = (
                "failed"
                if isinstance(exc, RemoteOAuthError)
                and exc.code
                in {
                    "mcp_remote_oauth_authorization_rejected",
                    "mcp_remote_oauth_unauthorized",
                }
                else "unknown_outcome"
            )
            self._finish_safely(
                session,
                subject,
                status,
                (
                    exc.code
                    if status == "failed" and isinstance(exc, RemoteOAuthError)
                    else "mcp_remote_oauth_token_exchange_unknown_outcome"
                ),
            )
            if isinstance(exc, RemoteOAuthError):
                raise
            raise RemoteOAuthError(
                "OAuth 换票结果未知，禁止重试旧授权。",
                code="mcp_remote_oauth_token_exchange_unknown_outcome",
                status_code=502,
            ) from exc
        finally:
            code = ""
            verifier = ""
            bundle.clear()
            response.clear()
            self._revoke_safely(session.pkce_credential_id, subject)

    async def refresh(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        token_id: str,
        expected_revision: int,
    ) -> RemoteOAuthTokenRevisionV1:
        subject = self._require()
        lock = self._locks[
            hash((subject.tenant_id, subject.owner_id, target_type, target_id))
            % len(self._locks)
        ]
        async with lock:
            return await self._refresh_locked(
                subject=subject,
                target_type=target_type,
                target_id=target_id,
                token_id=token_id,
                expected_revision=expected_revision,
            )

    async def _refresh_locked(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: OAuthTargetType,
        target_id: str,
        token_id: str,
        expected_revision: int,
    ) -> RemoteOAuthTokenRevisionV1:
        token = self.store.active_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if token is None:
            legacy = self.store.latest_token(
                subject=subject, target_type=target_type, target_id=target_id
            )
            if legacy is not None and legacy.status == "legacy_unbound":
                token = legacy
        if token is None or token.token_id != token_id:
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=404,
            )
        if (
            not token.resource_bound
            or token.protocol_version != MCP_PROTOCOL_VERSION
            or not token.resource_uri
            or token.resource_digest
            != hashlib.sha256(token.resource_uri.encode("utf-8")).hexdigest()
        ):
            raise RemoteOAuthError(
                "旧 OAuth token 未绑定 resource，必须重新授权。",
                code="mcp_remote_oauth_legacy_token_reauthorization_required",
                status_code=409,
            )
        if token.revision != expected_revision or not token.refresh_available:
            raise RemoteOAuthError(
                "OAuth token revision 已变化或不可刷新。",
                code="mcp_remote_oauth_token_stale",
                status_code=409,
            )
        discovery, registration = self._current(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if (
            token.discovery_fingerprint != discovery.discovery_fingerprint
            or token.registration_id != registration.registration_id
            or token.registration_revision != registration.revision
            or token.policy_fingerprint != discovery.policy.policy_fingerprint
        ):
            self.store.set_token_status(token_id, subject=subject, status="stale")
            raise RemoteOAuthError(
                "OAuth token 与当前发现证据不一致。",
                code="mcp_remote_oauth_token_stale",
                status_code=409,
            )
        try:
            bundle = self._token_bundle(token, subject)
        except RemoteOAuthError:
            self.store.set_token_status(token.token_id, subject=subject, status="stale")
            self._revoke_safely(token.credential_id, subject)
            raise
        refresh_token = bundle.get("refresh_token", "")
        new_bundle: dict[str, str] = {}
        response: dict[str, Any] = {}
        if not refresh_token:
            self.store.set_token_status(token.token_id, subject=subject, status="stale")
            self._revoke_safely(token.credential_id, subject)
            bundle.clear()
            raise RemoteOAuthError(
                "OAuth token 不含 refresh token。",
                code="mcp_remote_oauth_refresh_unavailable",
                status_code=409,
            )
        try:
            attempt = self.store.claim_refresh(
                subject=subject,
                token=token,
                discovery=discovery,
                registration=registration,
            )
        except Exception:
            refresh_token = ""
            bundle.clear()
            raise
        try:
            response = await self.bridge.refresh_access_token(
                target_id,
                discovery.policy.token_endpoint,
                request_body={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": registration.client_id,
                    "resource": token.resource_uri,
                },
            )
            new_bundle, granted, expires_at = _token_payload(
                response,
                requested_scopes=token.scopes,
                previous_refresh_token=refresh_token,
                allow_refresh_token=True,
            )
            self.credential_rotator(
                token.credential_id,
                value=_json(new_bundle).decode("utf-8"),
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
            refreshed = self.store.complete_refresh(
                attempt.attempt_id,
                subject=subject,
                scopes=granted,
                expires_at=expires_at,
                refresh_available=bool(new_bundle["refresh_token"]),
            )
            self._notify_target_changed(target_type, target_id)
            return refreshed
        except asyncio.CancelledError:
            try:
                self.store.finish_refresh_attempt(
                    attempt.attempt_id,
                    subject=subject,
                    status="unknown_outcome",
                    error_code="mcp_remote_oauth_refresh_unknown_outcome",
                    token_status="unknown_outcome",
                )
            except RemoteOAuthError:
                pass
            self._revoke_safely(token.credential_id, subject)
            raise
        except Exception as exc:
            safe_rejection = isinstance(exc, RemoteOAuthError) and exc.code in {
                "mcp_remote_oauth_unauthorized",
                "mcp_remote_oauth_forbidden",
                "mcp_remote_oauth_rate_limited",
            }
            persisted = False
            try:
                self.store.finish_refresh_attempt(
                    attempt.attempt_id,
                    subject=subject,
                    status="failed" if safe_rejection else "unknown_outcome",
                    error_code=(
                        exc.code
                        if safe_rejection
                        else "mcp_remote_oauth_refresh_unknown_outcome"
                    ),
                    token_status="stale" if safe_rejection else "unknown_outcome",
                )
                persisted = True
            except RemoteOAuthError:
                pass
            self._revoke_safely(token.credential_id, subject)
            if safe_rejection and persisted:
                raise
            raise RemoteOAuthError(
                "OAuth token 刷新结果未知，旧 revision 已封锁。",
                code="mcp_remote_oauth_refresh_unknown_outcome",
                status_code=502,
            ) from exc
        finally:
            refresh_token = ""
            bundle.clear()
            new_bundle.clear()
            response.clear()

    def cancel(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        session_id: str,
    ) -> RemoteOAuthAuthorizationSessionV1:
        subject = self._require()
        session = self.store.session(session_id, subject=subject)
        if session.target_type != target_type or session.target_id != target_id:
            raise RemoteOAuthError(
                "OAuth 授权会话不属于当前目标。",
                code="mcp_remote_oauth_scope_denied",
                status_code=403,
            )
        self._revoke_safely(session.pkce_credential_id, subject)
        self._revoke_safely(session.token_credential_id, subject)
        return self.store.finish_session(
            session_id,
            subject=subject,
            status="cancelled",
            error_code="mcp_remote_oauth_authorization_cancelled",
        )

    def revoke(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        token_id: str,
    ) -> RemoteOAuthTokenRevisionV1:
        subject = self._require()
        token = self.store.active_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if token is None:
            legacy = self.store.latest_token(
                subject=subject, target_type=target_type, target_id=target_id
            )
            if legacy is not None and legacy.status == "legacy_unbound":
                token = legacy
        if token is None or token.token_id != token_id:
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=404,
            )
        revoked = self.store.set_token_status(
            token_id, subject=subject, status="revoked"
        )
        self._notify_target_changed(target_type, target_id)
        self._revoke_credential(token.credential_id, subject)
        return revoked

    async def revoke_with_remote(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        token_id: str,
    ) -> dict[str, Any]:
        subject = self._require()
        token = self.store.active_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if token is None or token.token_id != token_id:
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=404,
            )
        discovery, registration = self._current(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if (
            not isinstance(discovery.policy, RemoteOAuthPolicyV2)
            or token.discovery_fingerprint != discovery.discovery_fingerprint
            or token.registration_id != registration.registration_id
            or token.registration_revision != registration.revision
            or token.policy_fingerprint != discovery.policy.policy_fingerprint
        ):
            raise RemoteOAuthError(
                "OAuth token 与当前发现证据不一致。",
                code="mcp_remote_oauth_token_stale",
                status_code=409,
            )
        bundle = self._token_bundle(token, subject)
        selected_token = bundle.get("refresh_token") or bundle.get("access_token", "")
        token_type_hint: Literal["access_token", "refresh_token"] = (
            "refresh_token" if bundle.get("refresh_token") else "access_token"
        )
        endpoint = discovery.policy.revocation_endpoint
        endpoint_digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        try:
            attempt = self.store.claim_revocation(
                subject=subject,
                token=token,
                discovery=discovery,
                registration=registration,
                revocation_endpoint_digest=endpoint_digest,
                token_type_hint=token_type_hint,
            )
        except Exception:
            selected_token = ""
            bundle.clear()
            raise
        self._notify_target_changed(target_type, target_id)
        remote_status: Literal[
            "completed", "failed", "unknown_outcome", "local_only"
        ] = "local_only"
        error_code = ""
        try:
            if (
                endpoint
                and _flag("MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED")
            ):
                try:
                    await self.bridge.revoke_token(
                        target_id,
                        endpoint,
                        request_body={
                            "token": selected_token,
                            "token_type_hint": token_type_hint,
                            "client_id": registration.client_id,
                        },
                    )
                    remote_status = "completed"
                except RemoteOAuthError as exc:
                    if exc.code == "mcp_remote_oauth_revocation_unknown_outcome":
                        remote_status = "unknown_outcome"
                        error_code = exc.code
                    else:
                        remote_status = "failed"
                        error_code = exc.code
            persisted = self.store.finish_revocation_attempt(
                attempt.attempt_id,
                subject=subject,
                status=remote_status,
                error_code=error_code,
            )
        except asyncio.CancelledError:
            try:
                self.store.finish_revocation_attempt(
                    attempt.attempt_id,
                    subject=subject,
                    status="unknown_outcome",
                    error_code="mcp_remote_oauth_revocation_unknown_outcome",
                )
            except Exception:
                # The durable started row remains fail-closed and is recovered
                # as unknown_outcome on the next service start.
                pass
            raise
        finally:
            selected_token = ""
            bundle.clear()
            self._revoke_safely(token.credential_id, subject)
        return {
            "local_revocation": "completed",
            "remote_revocation": persisted.status,
            "remote_error_code": persisted.error_code,
            "attempt_id": persisted.attempt_id,
        }

    def invalidate_target(
        self, *, target_type: OAuthTargetType, target_id: str
    ) -> None:
        subject = self.subject_resolver.resolve()
        pkce_ids, token_ids = self.store.cancel_target(
            subject=subject, target_type=target_type, target_id=target_id
        )
        for credential_id in (*pkce_ids, *token_ids):
            self._revoke_safely(credential_id, subject)
        self._notify_target_changed(target_type, target_id)

    def summary(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
    ) -> dict[str, Any]:
        state = self.status()
        if not (
            state["authorization_enabled"] and state["token_storage_enabled"]
        ):
            return {
                **state,
                "authorization_session": None,
                "token": None,
            }
        subject = self._require()
        session = self.store.latest_session(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if session is not None and session.status == "expired":
            self._revoke_safely(session.pkce_credential_id, subject)
            self._revoke_safely(session.token_credential_id, subject)
        token = self.store.active_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        latest_token = token or self.store.latest_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if latest_token is not None and latest_token.status != "legacy_unbound":
            latest_token = token
        discovery = self.metadata_store.active_discovery(
            subject=subject, target_type=target_type, target_id=target_id
        )
        registration = self.metadata_store.active_registration(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if discovery is None or discovery.source_digest != source_digest:
            session = None
            token = None
            latest_token = None
        elif token is not None and (
            registration is None
            or token.discovery_fingerprint != discovery.discovery_fingerprint
            or token.registration_id != registration.registration_id
            or token.registration_revision != registration.revision
            or token.policy_fingerprint != discovery.policy.policy_fingerprint
        ):
            self.store.set_token_status(token.token_id, subject=subject, status="stale")
            self._revoke_safely(token.credential_id, subject)
            token = None
            latest_token = None
        return {
            **state,
            "authorization_session": self._public_session(session) if session else None,
            "token": self._public_token(token or latest_token)
            if (token or latest_token)
            else None,
        }

    def execution_metadata(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
    ) -> RemoteOAuthExecutionMetadataV1:
        subject = self._require()
        discovery, registration = self._current(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if (
            discovery.source_digest != source_digest
            or not isinstance(discovery.policy, RemoteOAuthPolicyV2)
        ):
            raise RemoteOAuthError(
                "OAuth 候选必须重新发现并授权。",
                code="mcp_remote_oauth_legacy_token_reauthorization_required",
                status_code=409,
            )
        token = self.store.active_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if token is None:
            latest = self.store.latest_token(
                subject=subject, target_type=target_type, target_id=target_id
            )
            if latest is not None and latest.status == "legacy_unbound":
                raise RemoteOAuthError(
                    "旧 OAuth token 未绑定 resource，必须重新授权。",
                    code="mcp_remote_oauth_legacy_token_reauthorization_required",
                    status_code=409,
                )
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=409,
            )
        resource_digest = hashlib.sha256(
            discovery.policy.resource_uri.encode("utf-8")
        ).hexdigest()
        if (
            not token.resource_bound
            or token.protocol_version != MCP_PROTOCOL_VERSION
            or token.resource_uri != discovery.policy.resource_uri
            or token.resource_digest != resource_digest
            or token.discovery_fingerprint != discovery.discovery_fingerprint
            or token.registration_id != registration.registration_id
            or token.registration_revision != registration.revision
            or token.policy_fingerprint != discovery.policy.policy_fingerprint
        ):
            raise RemoteOAuthError(
                "OAuth token 与冻结策略不一致，必须重新授权。",
                code="mcp_remote_oauth_token_stale",
                status_code=409,
            )
        if (
            token.expires_at is not None
            and token.expires_at <= time.time() + OAUTH_RUNTIME_MIN_TTL_SECONDS
        ):
            raise RemoteOAuthError(
                "OAuth token 即将到期，需要显式刷新。",
                code="mcp_remote_oauth_refresh_required",
                status_code=409,
            )
        registration_digest = self.registration_revision_digest(registration)
        revision_digest = _digest(
            {
                "schema_version": "remote-oauth-token-revision-digest-v1",
                "target_type": token.target_type,
                "target_id": token.target_id,
                "token_id": token.token_id,
                "revision": token.revision,
                "discovery_fingerprint": token.discovery_fingerprint,
                "registration_digest": registration_digest,
                "policy_fingerprint": token.policy_fingerprint,
                "resource_digest": token.resource_digest,
                "scope_digest": token.scope_digest,
                "protocol_version": token.protocol_version,
            }
        )
        return RemoteOAuthExecutionMetadataV1(
            target_type=target_type,
            target_id=target_id,
            origin=discovery.policy.origin,
            resource_uri=discovery.policy.resource_uri,
            resource_digest=resource_digest,
            policy_fingerprint=discovery.policy.policy_fingerprint,
            discovery_fingerprint=discovery.discovery_fingerprint,
            registration_digest=registration_digest,
            scope_source=token.scope_source,
            scopes=token.scopes,
            scope_digest=token.scope_digest,
            token_revision_digest=revision_digest,
            expires_at=token.expires_at,
        )

    @contextmanager
    def resolve_for_execution(
        self,
        *,
        target_type: OAuthTargetType,
        target_id: str,
        source_digest: str,
        expected_policy_fingerprint: str,
        expected_scope_digest: str,
        expected_token_revision_digest: str,
    ) -> Iterator[RemoteOAuthExecutionEnvelope]:
        metadata = self.execution_metadata(
            target_type=target_type,
            target_id=target_id,
            source_digest=source_digest,
        )
        if (
            metadata.policy_fingerprint != expected_policy_fingerprint
            or metadata.scope_digest != expected_scope_digest
            or metadata.token_revision_digest != expected_token_revision_digest
        ):
            raise RemoteOAuthError(
                "OAuth 执行证据已变化。",
                code="mcp_remote_oauth_token_stale",
                status_code=409,
            )
        subject = self.subject_resolver.resolve()
        token = self.store.active_token(
            subject=subject, target_type=target_type, target_id=target_id
        )
        if token is None:
            raise RemoteOAuthError(
                "OAuth token 不存在。",
                code="mcp_remote_oauth_token_missing",
                status_code=409,
            )
        bundle = self._token_bundle(token, subject)
        envelope = RemoteOAuthExecutionEnvelope(
            metadata=metadata,
            authorization_value=f"Bearer {bundle['access_token']}",
        )
        try:
            yield envelope
        finally:
            envelope.authorization_value = ""
            bundle.clear()

    def _resolve(self, credential_id: str, subject: SubjectScopeV1) -> str:
        try:
            record = self.credential_lookup(
                credential_id,
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
            if getattr(record, "status", None) != "active":
                raise RuntimeError("inactive")
            secret = self.credential_resolver(
                credential_id,
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
        except Exception as exc:
            raise RemoteOAuthError(
                "OAuth 加密凭据当前不可用。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            ) from exc
        if not isinstance(secret, str) or not secret:
            raise RemoteOAuthError(
                "OAuth 加密凭据当前不可用。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            )
        return secret

    def _token_bundle(
        self, token: RemoteOAuthTokenRevisionV1, subject: SubjectScopeV1
    ) -> dict[str, str]:
        secret = self._resolve(token.credential_id, subject)
        try:
            value = json.loads(secret)
        except json.JSONDecodeError as exc:
            raise RemoteOAuthError(
                "OAuth token 加密内容损坏。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            ) from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"access_token", "refresh_token"}
            or not isinstance(value["access_token"], str)
            or not isinstance(value["refresh_token"], str)
        ):
            raise RemoteOAuthError(
                "OAuth token 加密内容损坏。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            )
        return {"access_token": value["access_token"], "refresh_token": value["refresh_token"]}

    def _revoke_credential(
        self, credential_id: str, subject: SubjectScopeV1
    ) -> None:
        try:
            self.credential_revoker(
                credential_id,
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
        except Exception as exc:
            raise RemoteOAuthError(
                "OAuth 加密凭据无法撤销。",
                code="mcp_remote_oauth_token_storage_unavailable",
                status_code=503,
            ) from exc

    def _revoke_safely(self, credential_id: str, subject: SubjectScopeV1) -> None:
        if not credential_id:
            return
        try:
            self._revoke_credential(credential_id, subject)
        except RemoteOAuthError:
            pass

    def _finish_safely(
        self,
        session: RemoteOAuthAuthorizationSessionV1,
        subject: SubjectScopeV1,
        status: Literal["failed", "unknown_outcome"],
        error_code: str,
    ) -> None:
        try:
            self.store.finish_session(
                session.session_id,
                subject=subject,
                status=status,
                error_code=error_code,
            )
        except RemoteOAuthError as exc:
            if exc.code != "mcp_remote_oauth_state_replay_denied":
                raise

    @staticmethod
    def _public_session(
        value: RemoteOAuthAuthorizationSessionV1,
    ) -> dict[str, Any]:
        return {
            "session_id": value.session_id,
            "status": value.status,
            "scopes": list(value.scopes),
            "scope_digest": value.scope_digest,
            "scope_source": value.scope_source,
            "resource_bound": bool(
                value.protocol_version == MCP_PROTOCOL_VERSION
                and value.resource_uri
                and value.resource_digest
                == hashlib.sha256(value.resource_uri.encode("utf-8")).hexdigest()
            ),
            "request_refresh_token": value.request_refresh_token,
            "error_code": value.error_code,
            "token_id": value.token_id,
            "created_at": value.created_at,
            "expires_at": value.expires_at,
        }

    @staticmethod
    def _public_token(value: RemoteOAuthTokenRevisionV1) -> dict[str, Any]:
        return {
            "token_id": value.token_id,
            "revision": value.revision,
            "status": value.status,
            "scopes": list(value.scopes),
            "scope_digest": value.scope_digest,
            "scope_source": value.scope_source,
            "resource_bound": value.resource_bound,
            "protocol_version": value.protocol_version,
            "expires_at": value.expires_at,
            "refresh_available": value.refresh_available,
            "stored_encrypted": True,
        }


router = APIRouter(tags=["mcp-remote-oauth-authorization"])
_service: MCPRemoteOAuthAuthorizationService | None = None


def configure_mcp_remote_oauth_authorization(
    service: MCPRemoteOAuthAuthorizationService,
) -> None:
    global _service
    _service = service


def _configured_service() -> MCPRemoteOAuthAuthorizationService:
    if _service is None:
        raise RemoteOAuthError(
            "OAuth 用户授权尚未配置。",
            code="mcp_remote_oauth_authorization_unconfigured",
            status_code=503,
        )
    return _service


@router.get("/oauth/callback", response_class=HTMLResponse, include_in_schema=False)
@router.get("/api/mcp/remote-auth/oauth/callback", response_class=HTMLResponse)
async def remote_oauth_callback(request: Request) -> HTMLResponse:
    pairs = list(request.query_params.multi_items())
    # Uvicorn composes its access-log entry from this mutable ASGI scope when
    # the response starts.  Clear the sensitive query immediately after one
    # local parse so authorization codes and state are not written to the
    # Backend access log.  Upstream reverse proxies must apply the same rule.
    request.scope["query_string"] = b""
    allowed = {"code", "state", "error", "error_description", "iss"}
    keys = [key for key, _ in pairs]
    invalid = any(key not in allowed for key in keys) or any(
        keys.count(key) > 1 for key in set(keys)
    )
    values = dict(pairs)
    state = values.get("state", "")
    code = values.get("code", "")
    auth_error = values.get("error", "")
    issuer = values.get("iss", "")
    if (
        invalid
        or "state" not in values
        or bool(code) == bool(auth_error)
        or len(auth_error) > 160
        or len(values.get("error_description", "")) > 1024
        or len(issuer) > 4096
    ):
        result = (
            "授权回调无效",
            "mcp_remote_oauth_callback_invalid",
            400,
        )
    else:
        try:
            session = await _configured_service().callback(
                state=state,
                code=code,
                authorization_error=auth_error,
                issuer=issuer,
            )
            result = (
                "授权已安全保存" if session.status == "completed" else "授权未完成",
                session.error_code,
                200 if session.status == "completed" else 409,
            )
        except RemoteOAuthError as exc:
            result = ("授权未完成", exc.code, exc.status_code)
    title, error_code, status_code = result
    safe_code = html.escape(error_code or "ok")
    document = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<body style='font-family:system-ui;background:#07111f;color:#e5eef8;"
        "max-width:680px;margin:64px auto;padding:24px'>"
        f"<h1>{html.escape(title)}</h1>"
        "<p>此页面不会显示 Token。请关闭本页并返回 ModelMirror，点击“刷新授权状态”。</p>"
        f"<p style='color:#94a3b8'>状态码：<code>{safe_code}</code></p>"
        "</body></html>"
    )
    return HTMLResponse(
        document,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
