"""Shared, fail-closed remote MCP authentication foundation.

R0 deliberately exposes only a read-only status endpoint. Binding mutations and
credential resolution are internal service methods reserved for later Hub and
Catalog integrations; no client-supplied scope, origin, header, or credential
identifier crosses the HTTP boundary in this round.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


SUBJECT_MODE = "local-single-owner"
POLICY_SCHEMA_VERSION = "mcp-remote-auth-policy-v1"
BINDING_SCHEMA_VERSION = "mcp-remote-auth-binding-v1"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DENIED_SECRET_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "cookie",
        "host",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

RemoteAuthMode = Literal["static_bearer", "static_header"]
RemoteAuthTargetType = Literal["hub_candidate", "catalog_project"]
RemoteAuthBindingStatus = Literal["active", "stale", "revoked"]


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RemoteAuthError(Exception):
    """Fixed-code remote-auth failure safe for API and audit summaries."""

    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SubjectScopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    tenant_id: str = Field(min_length=1, max_length=120)
    owner_id: str = Field(min_length=1, max_length=160)
    mode: Literal[SUBJECT_MODE] = SUBJECT_MODE


class SubjectScopeResolver(Protocol):
    def resolve(self) -> SubjectScopeV1: ...


class LocalSubjectScopeResolver:
    """Current single-subject resolver; future control planes replace this only."""

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._owner_id = owner_id

    def resolve(self) -> SubjectScopeV1:
        tenant_id = (
            self._tenant_id
            if self._tenant_id is not None
            else os.getenv("MODELMIRROR_DEFAULT_TENANT_ID", "local")
        )
        owner_id = (
            self._owner_id
            if self._owner_id is not None
            else os.getenv("MODELMIRROR_DEFAULT_OWNER_ID", "local")
        )
        if tenant_id != "local" or owner_id != "local":
            raise RemoteAuthError(
                "R0 仅允许 local/local 单主体范围。",
                code="mcp_remote_auth_scope_denied",
                status_code=403,
            )
        return SubjectScopeV1(tenant_id="local", owner_id="local")


class RemoteAuthPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[POLICY_SCHEMA_VERSION] = POLICY_SCHEMA_VERSION
    mode: RemoteAuthMode
    slot: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    header_name: str = Field(min_length=1, max_length=64)
    origin: str = Field(min_length=1, max_length=512)
    remote_url_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_fingerprint: str = Field(default="", max_length=64)

    @staticmethod
    def _reject_unknown_input(data: Any) -> None:
        if isinstance(data, Mapping) and set(data) - {
            "schema_version",
            "mode",
            "slot",
            "header_name",
            "origin",
            "remote_url_digest",
            "policy_fingerprint",
        }:
            raise RemoteAuthError(
                "远程认证策略包含不允许的字段。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )

    def __init__(self, **data: Any) -> None:
        self._reject_unknown_input(data)
        try:
            super().__init__(**data)
        except RemoteAuthError:
            raise
        except (ValidationError, ValueError, TypeError):
            pass
        else:
            return
        raise RemoteAuthError(
            "远程认证策略不满足固定静态认证边界。",
            code="mcp_remote_auth_policy_ineligible",
            status_code=422,
        )

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "RemoteAuthPolicyV1":
        cls._reject_unknown_input(obj)
        try:
            return super().model_validate(obj, **kwargs)
        except RemoteAuthError:
            raise
        except (ValidationError, ValueError, TypeError):
            pass
        raise RemoteAuthError(
            "远程认证策略不满足固定静态认证边界。",
            code="mcp_remote_auth_policy_ineligible",
            status_code=422,
        )

    @model_validator(mode="after")
    def validate_policy(self) -> "RemoteAuthPolicyV1":
        header = self.header_name.strip()
        if not HEADER_RE.fullmatch(header):
            raise ValueError("header_name is not a safe static header name")
        lower_header = header.lower()
        if self.mode == "static_bearer":
            if lower_header != "authorization":
                raise ValueError("static_bearer requires Authorization")
            header = "Authorization"
        elif lower_header == "authorization" or lower_header in DENIED_SECRET_HEADERS:
            raise ValueError("static_header cannot use a reserved header")
        else:
            header = lower_header

        raw_origin = self.origin.strip()
        if (
            any(ord(character) <= 0x20 or ord(character) == 0x7F for character in raw_origin)
            or "\\" in raw_origin
        ):
            raise ValueError("origin contains unsafe characters")
        try:
            parsed = urlsplit(raw_origin)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("origin is not parseable") from exc
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or port not in {None, 443}
        ):
            raise ValueError("origin must be a fixed HTTPS origin on port 443")
        parsed_host = parsed.hostname.lower()
        if parsed_host.endswith(".."):
            raise ValueError("origin host has ambiguous trailing dots")
        raw_host = parsed_host.rstrip(".")
        if not raw_host or "%" in raw_host:
            raise ValueError("origin host is not canonical")
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("origin host is not valid IDNA") from exc
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("origin cannot use an IP literal")
        if re.fullmatch(r"[0-9.]+", host):
            raise ValueError("origin cannot use a numeric host")
        labels = host.split(".")
        if (
            len(host) > 253
            or len(labels) < 2
            or any(not DNS_LABEL_RE.fullmatch(label) for label in labels)
        ):
            raise ValueError("origin host is not a fixed DNS name")
        canonical_origin = f"https://{host}"
        execution_fields = {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "slot": self.slot,
            "header_name": header,
            "origin": canonical_origin,
            "remote_url_digest": self.remote_url_digest,
        }
        expected = _canonical_digest(execution_fields)
        if self.policy_fingerprint and self.policy_fingerprint != expected:
            raise ValueError("policy_fingerprint does not match policy fields")
        object.__setattr__(self, "header_name", header)
        object.__setattr__(self, "origin", canonical_origin)
        object.__setattr__(self, "policy_fingerprint", expected)
        return self


class RemoteAuthBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[BINDING_SCHEMA_VERSION] = BINDING_SCHEMA_VERSION
    binding_id: str = Field(pattern=r"^mcpra_[0-9a-f]{32}$")
    subject: SubjectScopeV1
    target_type: RemoteAuthTargetType
    target_id: str = Field(min_length=1, max_length=255)
    slot: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_id: str = Field(min_length=1, max_length=255)
    revision: int = Field(ge=1)
    status: RemoteAuthBindingStatus
    created_at: float
    updated_at: float
    revoked_at: float | None = None


class RemoteAuthExecutionEnvelope:
    """Internal short-lived credential material; intentionally not serializable."""

    __slots__ = (
        "binding_id",
        "binding_revision",
        "header_name",
        "header_value",
        "origin",
        "policy_fingerprint",
    )

    def __init__(
        self,
        *,
        binding: RemoteAuthBindingV1,
        policy: RemoteAuthPolicyV1,
        secret: str,
    ) -> None:
        self.binding_id = binding.binding_id
        self.binding_revision = binding.revision
        self.header_name = policy.header_name
        self.header_value = f"Bearer {secret}" if policy.mode == "static_bearer" else secret
        self.origin = policy.origin
        self.policy_fingerprint = policy.policy_fingerprint

    def __repr__(self) -> str:
        return (
            "RemoteAuthExecutionEnvelope("
            f"binding_id={self.binding_id!r}, binding_revision={self.binding_revision!r}, "
            f"header_name={self.header_name!r}, header_value='<redacted>', "
            f"origin={self.origin!r}, policy_fingerprint={self.policy_fingerprint!r})"
        )

    def clear(self) -> None:
        self.header_value = ""


class MCPRemoteAuthStore:
    """Independent additive SQLite store containing no credential material."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("MCP_CATALOG_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.storage_dir, 0o700)
        except OSError:
            pass
        self.path = self.storage_dir / "remote-auth.sqlite3"
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
                CREATE TABLE IF NOT EXISTS remote_auth_bindings (
                    binding_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    subject_mode TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    credential_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_auth_one_active_binding
                    ON remote_auth_bindings(tenant_id, owner_id, target_type, target_id, slot)
                    WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS remote_auth_events (
                    event_id TEXT PRIMARY KEY,
                    binding_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    binding_revision INTEGER NOT NULL,
                    error_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (binding_id) REFERENCES remote_auth_bindings(binding_id)
                );
                CREATE INDEX IF NOT EXISTS remote_auth_events_binding_created
                    ON remote_auth_events(binding_id, created_at);
                """
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def ready(self) -> bool:
        try:
            with self._lock, self._connect() as db:
                return db.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def create_binding(
        self,
        *,
        subject: SubjectScopeV1,
        target_type: RemoteAuthTargetType,
        target_id: str,
        policy: RemoteAuthPolicyV1,
        credential_id: str,
    ) -> RemoteAuthBindingV1:
        clean_target_id = self._required(target_id, "target_id", 255)
        clean_credential_id = self._required(credential_id, "credential_id", 255)
        now = time.time()
        binding = RemoteAuthBindingV1(
            binding_id=f"mcpra_{uuid.uuid4().hex}",
            subject=subject,
            target_type=target_type,
            target_id=clean_target_id,
            slot=policy.slot,
            policy_fingerprint=policy.policy_fingerprint,
            credential_id=clean_credential_id,
            revision=1,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO remote_auth_bindings("
                    "binding_id,schema_version,tenant_id,owner_id,subject_mode,"
                    "target_type,target_id,slot,policy_fingerprint,credential_id,"
                    "revision,status,created_at,updated_at,revoked_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        binding.binding_id,
                        binding.schema_version,
                        subject.tenant_id,
                        subject.owner_id,
                        subject.mode,
                        binding.target_type,
                        binding.target_id,
                        binding.slot,
                        binding.policy_fingerprint,
                        binding.credential_id,
                        binding.revision,
                        binding.status,
                        binding.created_at,
                        binding.updated_at,
                        binding.revoked_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteAuthError(
                    "该主体、目标和凭据槽已存在绑定。",
                    code="mcp_remote_auth_binding_conflict",
                ) from exc
            self._append_event(db, binding, "created")
        return binding

    def get_binding(
        self,
        binding_id: str,
        *,
        subject: SubjectScopeV1,
    ) -> RemoteAuthBindingV1:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM remote_auth_bindings WHERE binding_id=?",
                (binding_id,),
            ).fetchone()
        if row is None:
            raise RemoteAuthError(
                "远程认证绑定不存在。",
                code="mcp_remote_auth_binding_missing",
                status_code=404,
            )
        if row["tenant_id"] != subject.tenant_id or row["owner_id"] != subject.owner_id:
            raise RemoteAuthError(
                "远程认证绑定不属于当前主体。",
                code="mcp_remote_auth_scope_denied",
                status_code=403,
            )
        return self._row_to_binding(row)

    def reconcile_policy(
        self,
        binding_id: str,
        *,
        subject: SubjectScopeV1,
        current_policy_fingerprint: str,
    ) -> RemoteAuthBindingV1:
        if not HEX64_RE.fullmatch(current_policy_fingerprint):
            raise RemoteAuthError(
                "远程认证策略指纹无效。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )
        binding = self.get_binding(binding_id, subject=subject)
        if binding.status != "active" or binding.policy_fingerprint == current_policy_fingerprint:
            return binding
        now = time.time()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE remote_auth_bindings SET status='stale',revision=revision+1,updated_at=? "
                "WHERE binding_id=? AND tenant_id=? AND owner_id=? "
                "AND status='active' AND revision=?",
                (now, binding_id, subject.tenant_id, subject.owner_id, binding.revision),
            )
            if cursor.rowcount != 1:
                row = db.execute(
                    "SELECT * FROM remote_auth_bindings WHERE binding_id=?",
                    (binding_id,),
                ).fetchone()
                if row is None:
                    raise RemoteAuthError(
                        "远程认证绑定不存在。",
                        code="mcp_remote_auth_binding_missing",
                        status_code=404,
                    )
                return self._row_to_binding(row)
            row = db.execute(
                "SELECT * FROM remote_auth_bindings WHERE binding_id=?",
                (binding_id,),
            ).fetchone()
            updated = self._row_to_binding(row)
            self._append_event(
                db,
                updated,
                "policy_stale",
                error_code="mcp_remote_auth_binding_stale",
            )
        return updated

    def rotate_binding(
        self,
        binding_id: str,
        *,
        subject: SubjectScopeV1,
        credential_id: str,
        expected_revision: int,
    ) -> RemoteAuthBindingV1:
        binding = self.get_binding(binding_id, subject=subject)
        if binding.status != "active":
            raise RemoteAuthError(
                "远程认证绑定当前不可旋转。",
                code="mcp_remote_auth_binding_stale",
            )
        clean_credential_id = self._required(credential_id, "credential_id", 255)
        now = time.time()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE remote_auth_bindings SET credential_id=?,revision=revision+1,updated_at=? "
                "WHERE binding_id=? AND tenant_id=? AND owner_id=? "
                "AND status='active' AND revision=?",
                (
                    clean_credential_id,
                    now,
                    binding_id,
                    subject.tenant_id,
                    subject.owner_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RemoteAuthError(
                    "远程认证绑定 revision 已变化。",
                    code="mcp_remote_auth_binding_revision_conflict",
                )
            row = db.execute(
                "SELECT * FROM remote_auth_bindings WHERE binding_id=?",
                (binding_id,),
            ).fetchone()
            updated = self._row_to_binding(row)
            self._append_event(db, updated, "rotated")
        return updated

    def revoke_binding(
        self,
        binding_id: str,
        *,
        subject: SubjectScopeV1,
    ) -> RemoteAuthBindingV1:
        binding = self.get_binding(binding_id, subject=subject)
        if binding.status == "revoked":
            return binding
        now = time.time()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE remote_auth_bindings SET status='revoked',"
                "revision=revision+1,updated_at=?,revoked_at=? "
                "WHERE binding_id=? AND tenant_id=? AND owner_id=? AND revision=?",
                (
                    now,
                    now,
                    binding_id,
                    subject.tenant_id,
                    subject.owner_id,
                    binding.revision,
                ),
            )
            if cursor.rowcount != 1:
                row = db.execute(
                    "SELECT * FROM remote_auth_bindings WHERE binding_id=?",
                    (binding_id,),
                ).fetchone()
                if row is None:
                    raise RemoteAuthError(
                        "远程认证绑定不存在。",
                        code="mcp_remote_auth_binding_missing",
                        status_code=404,
                    )
                return self._row_to_binding(row)
            row = db.execute(
                "SELECT * FROM remote_auth_bindings WHERE binding_id=?",
                (binding_id,),
            ).fetchone()
            updated = self._row_to_binding(row)
            self._append_event(db, updated, "revoked")
        return updated

    def events_for_binding(
        self,
        binding_id: str,
        *,
        subject: SubjectScopeV1,
    ) -> list[dict[str, Any]]:
        self.get_binding(binding_id, subject=subject)
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT event_id,binding_id,target_type,target_id,slot,event_type,"
                "policy_fingerprint,binding_revision,error_code,created_at "
                "FROM remote_auth_events WHERE binding_id=? AND tenant_id=? "
                "AND owner_id=? ORDER BY created_at,event_id",
                (binding_id, subject.tenant_id, subject.owner_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _append_event(
        db: sqlite3.Connection,
        binding: RemoteAuthBindingV1,
        event_type: str,
        *,
        error_code: str = "",
    ) -> None:
        db.execute(
            "INSERT INTO remote_auth_events("
            "event_id,binding_id,tenant_id,owner_id,target_type,target_id,slot,"
            "event_type,policy_fingerprint,binding_revision,error_code,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"mcprae_{uuid.uuid4().hex}",
                binding.binding_id,
                binding.subject.tenant_id,
                binding.subject.owner_id,
                binding.target_type,
                binding.target_id,
                binding.slot,
                event_type,
                binding.policy_fingerprint,
                binding.revision,
                error_code,
                time.time(),
            ),
        )

    @staticmethod
    def _row_to_binding(row: sqlite3.Row) -> RemoteAuthBindingV1:
        binding: RemoteAuthBindingV1 | None = None
        try:
            binding = RemoteAuthBindingV1(
                schema_version=row["schema_version"],
                binding_id=row["binding_id"],
                subject=SubjectScopeV1(
                    tenant_id=row["tenant_id"],
                    owner_id=row["owner_id"],
                    mode=row["subject_mode"],
                ),
                target_type=row["target_type"],
                target_id=row["target_id"],
                slot=row["slot"],
                policy_fingerprint=row["policy_fingerprint"],
                credential_id=row["credential_id"],
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
        except (
            ValidationError,
            ValueError,
            TypeError,
            OverflowError,
            IndexError,
            KeyError,
        ):
            pass
        if binding is None:
            raise RemoteAuthError(
                "远程认证绑定存储损坏。",
                code="mcp_remote_auth_storage_corrupt",
                status_code=503,
            )
        return binding

    @staticmethod
    def _required(value: str, field_name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text or len(text) > limit:
            raise RemoteAuthError(
                f"{field_name} 无效。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )
        return text


CredentialLookup = Callable[..., Any]
CredentialResolver = Callable[..., str]
CredentialSecurityAttestor = Callable[[], tuple[bool, bool]]


class MCPRemoteAuthBroker:
    """Shared policy/binding broker; R0 has no mutating public API."""

    def __init__(
        self,
        store: MCPRemoteAuthStore,
        *,
        subject_resolver: SubjectScopeResolver,
        credential_lookup: CredentialLookup,
        credential_resolver: CredentialResolver,
        credential_security_attestor: CredentialSecurityAttestor,
    ) -> None:
        self.store = store
        self.subject_resolver = subject_resolver
        self._credential_lookup = credential_lookup
        self._credential_resolver = credential_resolver
        self._credential_security_attestor = credential_security_attestor
        self._binding_locks = tuple(threading.RLock() for _ in range(64))

    def status(self) -> dict[str, Any]:
        external_key_available, external_key_enforced = (
            self._master_key_attestation()
        )
        return {
            "enabled": _environment_flag("MCP_REMOTE_AUTH_ENABLED"),
            "static_token_enabled": _environment_flag("MCP_REMOTE_STATIC_TOKEN_ENABLED"),
            "single_owner_acknowledged": _environment_flag(
                "MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK"
            ),
            "subject_mode": SUBJECT_MODE,
            "external_master_key_available": external_key_available,
            "external_master_key_enforced": external_key_enforced,
            "storage_ready": self.store.ready(),
            "supported_auth_modes": ["static_bearer", "static_header"],
            "multi_tenant": False,
        }

    def create_binding(
        self,
        *,
        target_type: RemoteAuthTargetType,
        target_id: str,
        policy: RemoteAuthPolicyV1,
        credential_id: str,
    ) -> RemoteAuthBindingV1:
        subject = self._require_operational()
        self._require_static_policy_enabled(policy)
        self._credential_metadata(credential_id, subject=subject)
        return self.store.create_binding(
            subject=subject,
            target_type=target_type,
            target_id=target_id,
            policy=policy,
            credential_id=credential_id,
        )

    def get_binding(
        self,
        binding_id: str,
        *,
        current_policy: RemoteAuthPolicyV1,
    ) -> RemoteAuthBindingV1:
        with self._binding_lock(binding_id):
            subject = self._require_operational()
            self._require_static_policy_enabled(current_policy)
            binding = self._get_binding_locked(
                binding_id,
                subject=subject,
                current_policy=current_policy,
            )
        return binding

    def _get_binding_locked(
        self,
        binding_id: str,
        *,
        subject: SubjectScopeV1,
        current_policy: RemoteAuthPolicyV1,
    ) -> RemoteAuthBindingV1:
        binding = self.store.reconcile_policy(
            binding_id,
            subject=subject,
            current_policy_fingerprint=current_policy.policy_fingerprint,
        )
        if binding.status == "stale":
            raise RemoteAuthError(
                "远程认证策略已漂移，绑定已失效。",
                code="mcp_remote_auth_binding_stale",
            )
        if binding.status == "revoked":
            raise RemoteAuthError(
                "远程认证绑定已撤销。",
                code="mcp_remote_auth_binding_missing",
                status_code=404,
            )
        return binding

    def rotate_binding(
        self,
        binding_id: str,
        *,
        current_policy: RemoteAuthPolicyV1,
        credential_id: str,
        expected_revision: int,
    ) -> RemoteAuthBindingV1:
        with self._binding_lock(binding_id):
            subject = self._require_operational()
            self._require_static_policy_enabled(current_policy)
            binding = self._get_binding_locked(
                binding_id,
                subject=subject,
                current_policy=current_policy,
            )
            self._credential_metadata(credential_id, subject=subject)
            return self.store.rotate_binding(
                binding.binding_id,
                subject=subject,
                credential_id=credential_id,
                expected_revision=expected_revision,
            )

    def revoke_binding(self, binding_id: str) -> RemoteAuthBindingV1:
        with self._binding_lock(binding_id):
            subject = self._require_operational()
            return self.store.revoke_binding(binding_id, subject=subject)

    @contextmanager
    def resolve_for_execution(
        self,
        binding_id: str,
        *,
        current_policy: RemoteAuthPolicyV1,
    ) -> Iterator[RemoteAuthExecutionEnvelope]:
        with self._binding_lock(binding_id):
            subject = self._require_operational()
            self._require_static_policy_enabled(current_policy)
            binding = self._get_binding_locked(
                binding_id,
                subject=subject,
                current_policy=current_policy,
            )
            self._credential_metadata(binding.credential_id, subject=subject)
            secret: Any = None
            try:
                secret = self._credential_resolver(
                    binding.credential_id,
                    tenant_id=subject.tenant_id,
                    owner_id=subject.owner_id,
                )
            except Exception:
                pass
            if not isinstance(secret, str) or not secret:
                raise RemoteAuthError(
                    "远程认证凭据当前不可用。",
                    code="mcp_remote_auth_credential_unavailable",
                    status_code=503,
                )
            latest = self.store.get_binding(binding_id, subject=subject)
            if (
                latest.status != "active"
                or latest.revision != binding.revision
                or latest.credential_id != binding.credential_id
                or latest.policy_fingerprint != binding.policy_fingerprint
            ):
                raise RemoteAuthError(
                    "远程认证绑定在解析期间发生变化。",
                    code=(
                        "mcp_remote_auth_binding_missing"
                        if latest.status == "revoked"
                        else "mcp_remote_auth_binding_stale"
                    ),
                    status_code=404 if latest.status == "revoked" else 409,
                )
            envelope = RemoteAuthExecutionEnvelope(
                binding=binding,
                policy=current_policy,
                secret=secret,
            )
            try:
                yield envelope
            finally:
                envelope.clear()

    def _require_operational(self) -> SubjectScopeV1:
        if not _environment_flag("MCP_REMOTE_AUTH_ENABLED"):
            raise RemoteAuthError(
                "远程 MCP 认证当前未启用。",
                code="mcp_remote_auth_disabled",
                status_code=503,
            )
        if not _environment_flag("MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK"):
            raise RemoteAuthError(
                "尚未确认本地单主体运行边界。",
                code="mcp_remote_auth_single_owner_ack_required",
                status_code=503,
            )
        external_key_available, external_key_enforced = (
            self._master_key_attestation()
        )
        if not external_key_available or not external_key_enforced:
            raise RemoteAuthError(
                "远程 MCP 认证要求外部凭据主密钥及强制策略。",
                code="mcp_remote_auth_master_key_required",
                status_code=503,
            )
        return self.subject_resolver.resolve()

    def _master_key_attestation(self) -> tuple[bool, bool]:
        try:
            result = self._credential_security_attestor()
        except Exception:
            return False, False
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not all(isinstance(value, bool) for value in result)
        ):
            return False, False
        return result

    def _binding_lock(self, binding_id: str) -> threading.RLock:
        digest = hashlib.sha256(str(binding_id).encode("utf-8")).digest()
        return self._binding_locks[int.from_bytes(digest[:2], "big") % len(self._binding_locks)]

    @staticmethod
    def _require_static_policy_enabled(policy: RemoteAuthPolicyV1) -> None:
        if not _environment_flag("MCP_REMOTE_STATIC_TOKEN_ENABLED"):
            raise RemoteAuthError(
                "静态 Token 远程认证当前未启用。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=503,
            )
        if policy.mode not in {"static_bearer", "static_header"}:
            raise RemoteAuthError(
                "远程认证策略不满足静态 Token 边界。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )

    def _credential_metadata(
        self,
        credential_id: str,
        *,
        subject: SubjectScopeV1,
    ) -> Any:
        record: Any = None
        try:
            record = self._credential_lookup(
                credential_id,
                tenant_id=subject.tenant_id,
                owner_id=subject.owner_id,
            )
        except Exception:
            pass
        if record is None:
            raise RemoteAuthError(
                "远程认证凭据不存在或不属于当前主体。",
                code="mcp_remote_auth_scope_denied",
                status_code=403,
            )
        if (
            getattr(record, "credential_id", None) != credential_id
            or getattr(record, "tenant_id", None) != subject.tenant_id
            or getattr(record, "owner_id", None) != subject.owner_id
        ):
            raise RemoteAuthError(
                "远程认证凭据不存在或不属于当前主体。",
                code="mcp_remote_auth_scope_denied",
                status_code=403,
            )
        if getattr(record, "status", None) != "active":
            raise RemoteAuthError(
                "远程认证凭据当前不可用。",
                code="mcp_remote_auth_credential_unavailable",
                status_code=503,
            )
        return record


router = APIRouter()
_broker: MCPRemoteAuthBroker | None = None


def configure_mcp_remote_auth(broker: MCPRemoteAuthBroker) -> None:
    global _broker
    _broker = broker


def _service() -> MCPRemoteAuthBroker:
    if _broker is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "mcp_remote_auth_unconfigured",
                "error": "远程 MCP 认证基础尚未配置。",
            },
        )
    return _broker


@router.get("/api/mcp/remote-auth/status")
async def remote_auth_status(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length", "").strip()
    has_body = (
        content_length not in {"", "0"}
        or "transfer-encoding" in request.headers
    )
    if not request.query_params and not has_body:
        async for chunk in request.stream():
            if chunk:
                has_body = True
                break
    if request.query_params or has_body:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "mcp_remote_auth_client_scope_denied",
                "error": "状态接口不接受客户端范围或目标参数。",
            },
        )
    return _service().status()
