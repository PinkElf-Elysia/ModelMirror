"""Controlled official MCP Registry discovery and owner-scoped Hub candidates.

The Hub is deliberately not a general MCP configuration endpoint.  Clients
submit registry identifiers only; canonical remote URLs are resolved from the
server-owned snapshot and may be used only after the strict eligibility check.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .hub_contracts import HubContractRegistry, HubReviewedContractV1
from .remote_auth import RemoteAuthError, RemoteAuthPolicyV1
from .remote_oauth import RemoteOAuthError


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io"
REGISTRY_HOST = "registry.modelcontextprotocol.io"
REGISTRY_API_PREFIX = "/v0.1"
REGISTRY_PAGE_LIMIT = 100
REGISTRY_FALLBACK_PAGE_LIMIT = 10
REGISTRY_PAGE_ATTEMPTS = 4
REGISTRY_MAX_PAGES = 500
REGISTRY_MAX_PAGE_BYTES = 2 * 1024 * 1024
REGISTRY_MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
REGISTRY_MAX_ENTRIES = 50_000
DOCKER_DESKTOP_SYNTHETIC_NETWORK = ipaddress.ip_network("198.18.0.0/15")
SYNC_INTERVAL_SECONDS = 24 * 60 * 60
MANUAL_SYNC_INTERVAL_SECONDS = 10 * 60
MAX_TOOL_COUNT = 50
MAX_TOOL_SCHEMA_BYTES = 32 * 1024
MAX_TOTAL_SCHEMA_BYTES = 256 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_RESULT_BYTES = 256 * 1024
SESSION_IDLE_SECONDS = 5 * 60
SESSION_TTL_SECONDS = 15 * 60
MAX_SESSION_ACTIONS = 50
CALL_TIMEOUT_SECONDS = 20.0
SESSION_CLEANUP_INTERVAL_SECONDS = 15.0
SAFE_SESSION_RECONNECT_CODES = frozenset(
    {
        "hub_session_expired",
        "hub_session_not_found",
        "hub_sidecar_unavailable",
        "hub_tool_recheck_failed",
    }
)

SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9.-]+/[A-Za-z0-9._-]+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.+_-]{1,255}$")
CANDIDATE_ID_RE = re.compile(r"^mcphub_[0-9a-f]{32}$")
REMOTE_ID_RE = re.compile(r"^remote_[0-9a-f]{16}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
APPROVAL_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class HubError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class HubUnknownOutcomeError(HubError):
    def __init__(self) -> None:
        super().__init__(
            "MCP Hub 操作结果未知，临时会话已销毁。请先核对目标状态，再重新连接；不要重试旧操作。",
            code="unknown_outcome",
            status_code=409,
        )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def arguments_digest(value: dict[str, Any]) -> str:
    if len(_json_bytes(value)) > MAX_ARGUMENT_BYTES:
        raise HubError(
            "MCP Hub 工具参数超过 32 KiB 上限。",
            code="hub_arguments_too_large",
            status_code=413,
        )
    return stable_digest(value)


def hub_enabled() -> bool:
    return os.getenv("MCP_HUB_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def hub_remote_enabled() -> bool:
    return os.getenv("MCP_HUB_REMOTE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _required_identifier(value: str, pattern: re.Pattern[str], field: str) -> str:
    clean = str(value or "").strip()
    if pattern.fullmatch(clean) is None:
        raise HubError(
            f"{field} 格式无效。",
            code="hub_identifier_invalid",
            status_code=422,
        )
    return clean


def _normalize_host(hostname: str | None) -> str:
    raw = str(hostname or "").strip().rstrip(".").lower()
    if not raw or len(raw) > 253:
        raise HubError("远程主机名无效。", code="hub_remote_ineligible")
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HubError("远程主机名无效。", code="hub_remote_ineligible") from exc
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HubError("Hub 不允许 IP 字面量远程地址。", code="hub_remote_ineligible")
    if host == "localhost" or host.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise HubError("Hub 不允许本地或内部主机。", code="hub_remote_ineligible")
    return host


def normalize_hub_remote_url(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or any(token in raw for token in ("{", "}")):
        raise HubError("Hub 远程地址必须是静态 URL。", code="hub_remote_ineligible")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise HubError("Hub 第一轮仅允许 HTTPS。", code="hub_remote_ineligible")
    if parsed.username is not None or parsed.password is not None:
        raise HubError("Hub 远程地址不能包含用户信息。", code="hub_remote_ineligible")
    if parsed.query or parsed.fragment:
        raise HubError("Hub 远程地址不能包含查询或片段。", code="hub_remote_ineligible")
    host = _normalize_host(parsed.hostname)
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise HubError("Hub 远程端口无效。", code="hub_remote_ineligible") from exc
    if port != 443:
        raise HubError("Hub 第一轮仅允许 443 端口。", code="hub_remote_ineligible")
    path = parsed.path or "/"
    normalized = f"https://{host}{path}"
    return normalized, f"https://{host}"


def _remote_transport(remote: dict[str, Any]) -> str:
    return str(remote.get("type") or remote.get("transport") or "").strip().lower()


def _has_remote_headers(remote: dict[str, Any]) -> bool:
    headers = remote.get("headers")
    return bool(headers) or bool(remote.get("variables"))


def _static_remote_auth_policy(
    remote: dict[str, Any], normalized_url: str, origin: str
) -> RemoteAuthPolicyV1 | None:
    """Reduce one current Registry secret Header declaration to a fixed policy."""

    if remote.get("variables"):
        return None
    headers = remote.get("headers")
    if not isinstance(headers, list) or len(headers) != 1:
        return None
    declaration = headers[0]
    if not isinstance(declaration, dict):
        return None
    if set(declaration) - {"name", "description", "isRequired", "isSecret"}:
        return None
    name = str(declaration.get("name") or "").strip()
    if declaration.get("isRequired") is not True or declaration.get("isSecret") is not True:
        return None
    mode: Literal["static_bearer", "static_header"] = (
        "static_bearer" if name.lower() == "authorization" else "static_header"
    )
    try:
        return RemoteAuthPolicyV1(
            mode=mode,
            slot="registry-secret-header",
            header_name=name,
            origin=origin,
            remote_url_digest=hashlib.sha256(normalized_url.encode("utf-8")).hexdigest(),
        )
    except RemoteAuthError:
        return None


def _oauth_discovery_header_hint(remote: dict[str, Any]) -> bool:
    """Recognize one optional Bearer slot without trusting prose metadata."""

    if remote.get("variables"):
        return False
    headers = remote.get("headers")
    if not isinstance(headers, list) or len(headers) != 1:
        return False
    declaration = headers[0]
    if not isinstance(declaration, dict):
        return False
    if set(declaration) - {"name", "description", "isRequired", "isSecret"}:
        return False
    return (
        str(declaration.get("name") or "").strip().lower() == "authorization"
        and declaration.get("isSecret") is True
        and (
            "isRequired" not in declaration
            or declaration.get("isRequired") is False
        )
    )


def normalize_registry_remote(remote: Any) -> dict[str, Any]:
    if not isinstance(remote, dict):
        return {
            "remote_id": "remote_" + stable_digest({"invalid": True})[:16],
            "transport": "unknown",
            "url": "",
            "origin": "",
            "eligibility": "no_remote",
            "reason": "远程端点元数据无效",
        }
    transport = _remote_transport(remote)
    raw_url = str(remote.get("url") or "").strip()
    raw_headers = remote.get("headers")
    identity_headers = []
    if isinstance(raw_headers, list):
        identity_headers = [
            {
                "name": str(item.get("name") or "")[:64],
                "isRequired": item.get("isRequired") is True,
                "isSecret": item.get("isSecret") is True,
            }
            for item in raw_headers[:4]
            if isinstance(item, dict)
        ]
    identity = {
        "transport": transport,
        "url": raw_url,
        "headers": identity_headers,
        "variables": bool(remote.get("variables")),
    }
    remote_id = "remote_" + stable_digest(identity)[:16]
    if transport in {"sse", "legacy-sse", "legacy_sse"}:
        return {
            "remote_id": remote_id,
            "transport": transport,
            "url": "",
            "origin": "",
            "eligibility": "legacy_transport",
            "reason": "旧 SSE 传输不在第一轮范围内",
        }
    if transport not in {"streamable-http", "streamable_http"}:
        return {
            "remote_id": remote_id,
            "transport": transport or "unknown",
            "url": "",
            "origin": "",
            "eligibility": "no_remote",
            "reason": "没有可用的 Streamable HTTP 端点",
        }
    if _has_remote_headers(remote):
        try:
            normalized, origin = normalize_hub_remote_url(raw_url)
        except HubError as exc:
            return {
                "remote_id": remote_id,
                "transport": "streamable-http",
                "url": "",
                "origin": "",
                "eligibility": "no_remote",
                "reason": str(exc),
            }
        policy = _static_remote_auth_policy(remote, normalized, origin)
        if policy is not None:
            return {
                "remote_id": remote_id,
                "transport": "streamable-http",
                "url": normalized,
                "origin": origin,
                "eligibility": "static_token_candidate",
                "reason": "可绑定一个固定 Secret Header 后进入复核",
                "auth_policy": policy.model_dump(mode="json"),
            }
        if _oauth_discovery_header_hint(remote):
            return {
                "remote_id": remote_id,
                "transport": "streamable-http",
                "url": normalized,
                "origin": origin,
                "eligibility": "oauth_discovery_candidate",
                "reason": "可检查标准 OAuth 元数据；R2A 不执行用户授权",
            }
        return {
            "remote_id": remote_id,
            "transport": "streamable-http",
            "url": "",
            "origin": "",
            "eligibility": "auth_required",
            "reason": "端点需要 Header、变量或凭据",
        }
    try:
        normalized, origin = normalize_hub_remote_url(raw_url)
    except HubError as exc:
        return {
            "remote_id": remote_id,
            "transport": "streamable-http",
            "url": "",
            "origin": "",
            "eligibility": "no_remote",
            "reason": str(exc),
        }
    return {
        "remote_id": remote_id,
        "transport": "streamable-http",
        "url": normalized,
        "origin": origin,
        "eligibility": "eligible",
        "reason": "可进行匿名只读远程试连",
    }


def normalize_registry_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("server"), dict):
        raise HubError("Registry 条目结构无效。", code="hub_registry_schema_drift")
    server = value["server"]
    name = _required_identifier(str(server.get("name") or ""), SERVER_NAME_RE, "server_name")
    version = _required_identifier(str(server.get("version") or ""), VERSION_RE, "version")
    metadata = value.get("_meta") if isinstance(value.get("_meta"), dict) else {}
    official = metadata.get("io.modelcontextprotocol.registry/official")
    official = official if isinstance(official, dict) else {}
    status = str(official.get("status") or "active").strip().lower()
    remotes = [normalize_registry_remote(item) for item in list(server.get("remotes") or [])[:20]]
    packages = list(server.get("packages") or [])
    raw_publisher = server.get("publisher")
    publisher = (
        str(raw_publisher.get("name") or "").strip()[:300]
        if isinstance(raw_publisher, dict)
        else str(raw_publisher or "").strip()[:300]
    )
    raw_categories = server.get("categories") or server.get("tags") or []
    categories = sorted(
        {
            str(item).strip()[:80]
            for item in raw_categories
            if isinstance(item, str) and str(item).strip()
        }
    )[:20]
    if status not in {"active", "published"}:
        eligibility = "removed"
    elif any(item["eligibility"] == "eligible" for item in remotes):
        eligibility = "eligible"
    elif any(item["eligibility"] == "static_token_candidate" for item in remotes):
        eligibility = "static_token_candidate"
    elif any(item["eligibility"] == "oauth_discovery_candidate" for item in remotes):
        eligibility = "oauth_discovery_candidate"
    elif any(item["eligibility"] == "auth_required" for item in remotes):
        eligibility = "auth_required"
    elif any(item["eligibility"] == "legacy_transport" for item in remotes):
        eligibility = "legacy_transport"
    elif packages:
        eligibility = "local_runtime"
    else:
        eligibility = "no_remote"
    normalized = {
        "server_name": name,
        "version": version,
        "title": str(server.get("title") or name).strip()[:300],
        "description": str(server.get("description") or "").strip()[:4000],
        "publisher": publisher,
        "categories": categories,
        "status": status,
        "is_latest": bool(official.get("isLatest", False)),
        "published_at": str(official.get("publishedAt") or "")[:80],
        "updated_at": str(official.get("updatedAt") or "")[:80],
        "eligibility": eligibility,
        "remotes": remotes,
    }
    normalized["source_digest"] = stable_digest(normalized)
    return normalized


class PinnedRegistryClient:
    """Small fixed-host HTTPS client for the official Registry snapshot."""

    def __init__(self, *, timeout: float = 12.0) -> None:
        self.timeout = max(2.0, min(float(timeout), 30.0))

    @staticmethod
    def _resolve() -> tuple[str, ...]:
        records = socket.getaddrinfo(
            REGISTRY_HOST,
            443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        addresses = tuple(dict.fromkeys(record[4][0] for record in records))
        if not addresses:
            raise HubError("官方 Registry DNS 无有效结果。", code="hub_registry_unavailable")
        for value in addresses:
            address = ipaddress.ip_address(value)
            if not address.is_global and address not in DOCKER_DESKTOP_SYNTHETIC_NETWORK:
                raise HubError(
                    "官方 Registry DNS 返回非公网地址。",
                    code="hub_registry_network_denied",
                )
        return addresses

    def get_page(
        self,
        *,
        cursor: str = "",
        etag: str = "",
        limit: int = REGISTRY_PAGE_LIMIT,
    ) -> tuple[dict[str, Any], str, bool]:
        # Discovery tracks only the Registry's currently published version.
        # Previously observed versions remain in SQLite as deleted records,
        # so a version change still creates a new immutable candidate identity
        # instead of replacing an active candidate in place.
        if limit not in {REGISTRY_PAGE_LIMIT, REGISTRY_FALLBACK_PAGE_LIMIT}:
            raise HubError(
                "Registry 分页大小无效。",
                code="hub_registry_page_limit_invalid",
            )
        query = f"?limit={limit}&version=latest"
        if cursor:
            query += "&cursor=" + quote(cursor, safe="")
        path = f"{REGISTRY_API_PREFIX}/servers{query}"
        address = self._resolve()[0]
        request_timeout = (
            max(self.timeout, 30.0)
            if limit == REGISTRY_FALLBACK_PAGE_LIMIT
            else self.timeout
        )

        class PinnedConnection(http.client.HTTPSConnection):
            def connect(inner_self) -> None:
                raw = socket.create_connection((address, 443), request_timeout)
                inner_self.sock = ssl.create_default_context().wrap_socket(
                    raw,
                    server_hostname=REGISTRY_HOST,
                )

        connection = PinnedConnection(REGISTRY_HOST, 443, timeout=request_timeout)
        headers = {
            "Accept": "application/json",
            "User-Agent": "ModelMirror-MCP-Hub/1.0",
            "Connection": "close",
        }
        if etag:
            headers["If-None-Match"] = etag
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            if response.status == 304:
                response.read()
                return {}, etag, True
            if response.status == 429:
                raise HubError("官方 Registry 请求受限。", code="hub_registry_rate_limited", status_code=429)
            if response.status != 200:
                raise HubError("官方 Registry 暂不可用。", code="hub_registry_unavailable", status_code=503)
            length = response.getheader("Content-Length")
            if length and int(length) > REGISTRY_MAX_PAGE_BYTES:
                raise HubError("Registry 页面超过大小上限。", code="hub_registry_response_too_large")
            body = response.read(REGISTRY_MAX_PAGE_BYTES + 1)
            if len(body) > REGISTRY_MAX_PAGE_BYTES:
                raise HubError("Registry 页面超过大小上限。", code="hub_registry_response_too_large")
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HubError("Registry 返回无效 JSON。", code="hub_registry_schema_drift") from exc
            if not isinstance(payload, dict):
                raise HubError("Registry 返回结构无效。", code="hub_registry_schema_drift")
            return payload, str(response.getheader("ETag") or "")[:500], False
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise HubError("官方 Registry 网络请求失败。", code="hub_registry_unavailable", status_code=503) from exc
        finally:
            connection.close()


class MCPHubStore:
    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("MCP_CATALOG_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.storage_dir / "hub.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS hub_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hub_servers (
                    server_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_latest INTEGER NOT NULL,
                    published_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    eligibility TEXT NOT NULL,
                    remotes_json TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    seen_sync_id TEXT NOT NULL,
                    PRIMARY KEY (server_name, version)
                );
                CREATE TABLE IF NOT EXISTS hub_sync_jobs (
                    sync_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL
                );
                CREATE TABLE IF NOT EXISTS hub_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    remote_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    remote_url TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    schema_digest TEXT NOT NULL,
                    tools_json TEXT NOT NULL,
                    taint_reason TEXT NOT NULL,
                    oauth_discovery_source TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE (tenant_id, owner_id, server_name, version, remote_id)
                );
                CREATE TABLE IF NOT EXISTS hub_execution_ledger (
                    approval_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(hub_servers)").fetchall()
            }
            if "publisher" not in columns:
                db.execute(
                    "ALTER TABLE hub_servers ADD COLUMN publisher TEXT NOT NULL DEFAULT ''"
                )
            if "categories_json" not in columns:
                db.execute(
                    "ALTER TABLE hub_servers ADD COLUMN categories_json TEXT NOT NULL DEFAULT '[]'"
                )
            candidate_columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(hub_candidates)").fetchall()
            }
            if "auth_policy_json" not in candidate_columns:
                db.execute(
                    "ALTER TABLE hub_candidates ADD COLUMN auth_policy_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "auth_binding_id" not in candidate_columns:
                db.execute(
                    "ALTER TABLE hub_candidates ADD COLUMN auth_binding_id TEXT NOT NULL DEFAULT ''"
                )
            if "oauth_discovery_source" not in candidate_columns:
                db.execute(
                    "ALTER TABLE hub_candidates ADD COLUMN oauth_discovery_source TEXT NOT NULL DEFAULT ''"
                )

    def meta(self, key: str, default: str = "") -> str:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM hub_meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def create_sync(self) -> str:
        sync_id = "hub_sync_" + uuid.uuid4().hex
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_sync_jobs VALUES(?,?,?,?,?,NULL)",
                (sync_id, "running", "", 0, time.time()),
            )
        return sync_id

    def finish_sync(self, sync_id: str, *, status: str, error_code: str = "", count: int = 0) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_sync_jobs SET status=?,error_code=?,item_count=?,finished_at=? WHERE sync_id=?",
                (status, error_code, int(count), time.time(), sync_id),
            )

    def get_sync(self, sync_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM hub_sync_jobs WHERE sync_id=?", (sync_id,)).fetchone()
        return dict(row) if row else None

    def replace_snapshot(self, sync_id: str, entries: list[dict[str, Any]], etag: str) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in entries:
                db.execute(
                    """
                    INSERT INTO hub_servers(
                      server_name,version,title,description,publisher,
                      categories_json,status,is_latest,published_at,updated_at,
                      eligibility,remotes_json,source_digest,seen_sync_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(server_name,version) DO UPDATE SET
                      title=excluded.title,description=excluded.description,
                      publisher=excluded.publisher,categories_json=excluded.categories_json,
                      status=excluded.status,is_latest=excluded.is_latest,
                      published_at=excluded.published_at,updated_at=excluded.updated_at,
                      eligibility=excluded.eligibility,remotes_json=excluded.remotes_json,
                      source_digest=excluded.source_digest,seen_sync_id=excluded.seen_sync_id
                    """,
                    (
                        item["server_name"], item["version"], item["title"],
                        item["description"], item["publisher"],
                        json.dumps(item["categories"], ensure_ascii=False, separators=(",", ":")),
                        item["status"], int(item["is_latest"]),
                        item["published_at"], item["updated_at"], item["eligibility"],
                        json.dumps(item["remotes"], ensure_ascii=False, separators=(",", ":")),
                        item["source_digest"], sync_id,
                    ),
                )
            db.execute(
                "UPDATE hub_servers SET status='deleted',eligibility='removed' WHERE seen_sync_id<>?",
                (sync_id,),
            )
            db.execute(
                "INSERT INTO hub_meta(key,value) VALUES('snapshot_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(now),),
            )
            db.execute(
                "INSERT INTO hub_meta(key,value) VALUES('snapshot_etag',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (etag,),
            )
            db.execute(
                "INSERT INTO hub_meta(key,value) VALUES('snapshot_count',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(len(entries)),),
            )

    @staticmethod
    def _server(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["is_latest"] = bool(item["is_latest"])
        item["categories"] = json.loads(item.pop("categories_json"))
        item["remotes"] = json.loads(item.pop("remotes_json"))
        item.pop("seen_sync_id", None)
        return item

    def list_servers(self, *, query: str = "", category: str = "", eligibility: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if query:
            clauses.append("(lower(server_name) LIKE ? OR lower(title) LIKE ? OR lower(description) LIKE ?)")
            token = f"%{query.lower()}%"
            params.extend([token, token, token])
        if eligibility:
            clauses.append("eligibility=?")
            params.append(eligibility)
        if category:
            clauses.append("categories_json LIKE ?")
            params.append(
                "%" + json.dumps(category, ensure_ascii=False)[1:-1] + "%"
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connect() as db:
            total = int(db.execute("SELECT count(*) FROM hub_servers" + where, params).fetchone()[0])
            rows = db.execute(
                "SELECT * FROM hub_servers" + where + " ORDER BY is_latest DESC, lower(title), version DESC LIMIT ? OFFSET ?",
                [*params, int(limit), int(offset)],
            ).fetchall()
        return [self._server(row) for row in rows], total

    def get_server(self, name: str, version: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_servers WHERE server_name=? AND version=?",
                (name, version),
            ).fetchone()
        return self._server(row) if row else None

    def list_categories(self) -> list[str]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT categories_json FROM hub_servers").fetchall()
        values: set[str] = set()
        for row in rows:
            try:
                categories = json.loads(str(row["categories_json"] or "[]"))
            except json.JSONDecodeError:
                continue
            values.update(
                str(item) for item in categories if isinstance(item, str) and item
            )
        return sorted(values, key=str.lower)[:200]

    def create_candidate(self, *, tenant_id: str, owner_id: str, server: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        candidate_id = "mcphub_" + uuid.uuid4().hex
        values = (
            candidate_id, tenant_id, owner_id, server["server_name"], server["version"],
            remote["remote_id"], "draft", remote["origin"], remote["url"],
            server["source_digest"], "", "[]", "", "", now, now,
            json.dumps(remote.get("auth_policy") or {}, ensure_ascii=False, separators=(",", ":")),
            "",
        )
        with self._lock, self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO hub_candidates("
                    "candidate_id,tenant_id,owner_id,server_name,version,remote_id,state,"
                    "origin,remote_url,source_digest,schema_digest,tools_json,taint_reason,"
                    "oauth_discovery_source,created_at,updated_at,auth_policy_json,auth_binding_id"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            except sqlite3.IntegrityError:
                row = db.execute(
                    "SELECT * FROM hub_candidates WHERE tenant_id=? AND owner_id=? AND server_name=? AND version=? AND remote_id=?",
                    (tenant_id, owner_id, server["server_name"], server["version"], remote["remote_id"]),
                ).fetchone()
                if row is None:
                    raise
                return self._candidate(row)
        return self.require_candidate(candidate_id, tenant_id, owner_id)

    @staticmethod
    def _candidate(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["tools"] = json.loads(item.pop("tools_json"))
        item["auth_policy"] = json.loads(item.pop("auth_policy_json", "{}") or "{}")
        return item

    def set_candidate_auth_binding(
        self,
        candidate_id: str,
        tenant_id: str,
        owner_id: str,
        binding_id: str,
    ) -> dict[str, Any]:
        self.require_candidate(candidate_id, tenant_id, owner_id)
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_candidates SET auth_binding_id=?,updated_at=? "
                "WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (str(binding_id), time.time(), candidate_id, tenant_id, owner_id),
            )
        return self.require_candidate(candidate_id, tenant_id, owner_id)

    def mark_candidate_oauth_discovery(
        self,
        candidate_id: str,
        tenant_id: str,
        owner_id: str,
        *,
        source: str,
    ) -> dict[str, Any]:
        if source != "www_authenticate":
            raise HubError(
                "OAuth 发现来源无效。",
                code="mcp_remote_oauth_candidate_ineligible",
                status_code=409,
            )
        self.require_candidate(candidate_id, tenant_id, owner_id)
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_candidates SET oauth_discovery_source=?,state='draft',"
                "taint_reason='',updated_at=? WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (source, time.time(), candidate_id, tenant_id, owner_id),
            )
        return self.require_candidate(candidate_id, tenant_id, owner_id)

    def list_candidates(self, tenant_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM hub_candidates WHERE tenant_id=? AND owner_id=? ORDER BY updated_at DESC",
                (tenant_id, owner_id),
            ).fetchall()
        return [self._candidate(row) for row in rows]

    def require_candidate(self, candidate_id: str, tenant_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_candidates WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (candidate_id, tenant_id, owner_id),
            ).fetchone()
        if row is None:
            raise HubError("MCP Hub 候选不存在。", code="hub_candidate_not_found", status_code=404)
        return self._candidate(row)

    def update_candidate(self, candidate_id: str, tenant_id: str, owner_id: str, *, state: str, schema_digest: str | None = None, tools: list[dict[str, Any]] | None = None, taint_reason: str = "") -> dict[str, Any]:
        current = self.require_candidate(candidate_id, tenant_id, owner_id)
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_candidates SET state=?,schema_digest=?,tools_json=?,taint_reason=?,updated_at=? WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (
                    state,
                    current["schema_digest"] if schema_digest is None else schema_digest,
                    json.dumps(current["tools"] if tools is None else tools, ensure_ascii=False, separators=(",", ":")),
                    str(taint_reason)[:120], time.time(), candidate_id, tenant_id, owner_id,
                ),
            )
        return self.require_candidate(candidate_id, tenant_id, owner_id)

    def activate_candidate_if_current(
        self,
        candidate_id: str,
        tenant_id: str,
        owner_id: str,
        expected_schema_digest: str,
    ) -> dict[str, Any]:
        """Atomically bind activation to the current Registry row and endpoint."""
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate_row = db.execute(
                "SELECT * FROM hub_candidates WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (candidate_id, tenant_id, owner_id),
            ).fetchone()
            if candidate_row is None:
                raise HubError(
                    "MCP Hub 候选不存在。",
                    code="hub_candidate_not_found",
                    status_code=404,
                )
            candidate = self._candidate(candidate_row)
            if (
                candidate["state"] != "verified"
                or not expected_schema_digest
                or expected_schema_digest != candidate["schema_digest"]
            ):
                raise HubError(
                    "候选尚未通过当前 Schema 预检。",
                    code="hub_activation_precondition",
                    status_code=409,
                )
            server_row = db.execute(
                "SELECT * FROM hub_servers WHERE server_name=? AND version=?",
                (candidate["server_name"], candidate["version"]),
            ).fetchone()
            current = self._server(server_row) if server_row is not None else None
            remote = next(
                (
                    item
                    for item in (current or {}).get("remotes", [])
                    if item.get("remote_id") == candidate["remote_id"]
                ),
                None,
            )
            if (
                current is None
                or current.get("status") not in {"active", "published"}
                or current.get("source_digest") != candidate["source_digest"]
                or remote is None
                or remote.get("eligibility") not in {"eligible", "static_token_candidate"}
                or remote.get("url") != candidate["remote_url"]
                or remote.get("origin") != candidate["origin"]
                or (remote.get("auth_policy") or {}) != candidate.get("auth_policy", {})
            ):
                db.execute(
                    "UPDATE hub_candidates SET state='drifted',taint_reason='hub_source_drift',updated_at=? "
                    "WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                    (time.time(), candidate_id, tenant_id, owner_id),
                )
                db.commit()
                raise HubError(
                    "Registry 版本或远程端点已变化。",
                    code="hub_source_drift",
                    status_code=409,
                )
            db.execute(
                "UPDATE hub_candidates SET state='active',taint_reason='',updated_at=? "
                "WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (time.time(), candidate_id, tenant_id, owner_id),
            )
        return self.require_candidate(candidate_id, tenant_id, owner_id)

    def delete_candidate(self, candidate_id: str, tenant_id: str, owner_id: str) -> None:
        self.require_candidate(candidate_id, tenant_id, owner_id)
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM hub_candidates WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                (candidate_id, tenant_id, owner_id),
            )

    def begin_execution(self, *, approval_id: str, tenant_id: str, owner_id: str, candidate_id: str, tool_name: str, args_digest: str) -> tuple[str, dict[str, Any] | None]:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM hub_execution_ledger WHERE approval_id=?", (approval_id,)).fetchone()
            if row is not None:
                item = dict(row)
                if (
                    item["tenant_id"], item["owner_id"], item["candidate_id"],
                    item["tool_name"], item["arguments_digest"],
                ) != (tenant_id, owner_id, candidate_id, tool_name, args_digest):
                    raise HubError("审批回放范围不匹配。", code="hub_approval_scope_mismatch", status_code=409)
                result = json.loads(item["result_json"]) if item["result_json"] else None
                return str(item["state"]), result
            db.execute(
                "INSERT INTO hub_execution_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (approval_id, tenant_id, owner_id, candidate_id, tool_name, args_digest, "started", None, "", now, now),
            )
        return "new", None

    def find_execution(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        owner_id: str,
        candidate_id: str,
        tool_name: str,
        args_digest: str,
    ) -> tuple[str, dict[str, Any] | None] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_execution_ledger WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        if (
            item["tenant_id"],
            item["owner_id"],
            item["candidate_id"],
            item["tool_name"],
            item["arguments_digest"],
        ) != (tenant_id, owner_id, candidate_id, tool_name, args_digest):
            raise HubError(
                "审批回放范围不匹配。",
                code="hub_approval_scope_mismatch",
                status_code=409,
            )
        result = json.loads(item["result_json"]) if item["result_json"] else None
        return str(item["state"]), result

    def finish_execution(self, approval_id: str, *, state: str, result: dict[str, Any] | None = None, error_code: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_execution_ledger SET state=?,result_json=?,error_code=?,updated_at=? WHERE approval_id=?",
                (state, json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result is not None else None, error_code, time.time(), approval_id),
            )

    def recover_started_executions(self, tenant_id: str, owner_id: str) -> list[str]:
        """Make crash-interrupted calls explicit and prevent silent replay."""
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT DISTINCT candidate_id FROM hub_execution_ledger "
                "WHERE tenant_id=? AND owner_id=? AND state='started'",
                (tenant_id, owner_id),
            ).fetchall()
            candidate_ids = [str(row["candidate_id"]) for row in rows]
            db.execute(
                "UPDATE hub_execution_ledger SET state='unknown',error_code='unknown_outcome',updated_at=? "
                "WHERE tenant_id=? AND owner_id=? AND state='started'",
                (now, tenant_id, owner_id),
            )
            for candidate_id in candidate_ids:
                db.execute(
                    "UPDATE hub_candidates SET state='tainted',taint_reason='unknown_outcome',updated_at=? "
                    "WHERE candidate_id=? AND tenant_id=? AND owner_id=?",
                    (now, candidate_id, tenant_id, owner_id),
                )
        return candidate_ids


class HubBridgeProtocol:
    async def authorize(self, candidate_id: str, url: str) -> str: ...
    async def revoke(self, capability: str) -> None: ...
    async def open(
        self,
        candidate_id: str,
        url: str,
        capability: str,
        session_owner: str,
        *,
        auth: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    async def list_tools(self, session_id: str) -> dict[str, Any]: ...
    async def call(self, session_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...
    async def close(self, session_id: str) -> None: ...
    async def reset(self) -> None: ...


class HubSocketBridge:
    def __init__(self, *, remote_socket: str | None = None, egress_socket: str | None = None) -> None:
        self.remote_socket = remote_socket or os.getenv("MCP_HUB_REMOTE_SOCKET_PATH", "/run/modelmirror-hub-mcp/hub-mcp.sock")
        self.egress_socket = egress_socket or os.getenv("MCP_HUB_EGRESS_SOCKET_PATH", "/run/modelmirror-hub-egress/hub-egress.sock")

    async def _request(self, path: str, payload: dict[str, Any], *, timeout: float = 25.0) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    path,
                    limit=MAX_RESULT_BYTES + 4096,
                ),
                timeout=3,
            )
            writer.write(_json_bytes(payload) + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=2)
            raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            raise HubError("MCP Hub 隔离服务不可用。", code="hub_sidecar_unavailable", status_code=503) from exc
        except ValueError as exc:
            raise HubError("MCP Hub 隔离服务响应无效。", code="hub_sidecar_invalid", status_code=502) from exc
        finally:
            if "writer" in locals():
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    pass
        if not raw or len(raw) > MAX_RESULT_BYTES + 4096:
            raise HubError("MCP Hub 隔离服务响应无效。", code="hub_sidecar_invalid", status_code=502)
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HubError("MCP Hub 隔离服务响应无效。", code="hub_sidecar_invalid", status_code=502) from exc
        if not isinstance(response, dict) or not response.get("ok"):
            code = str(response.get("code") if isinstance(response, dict) else "hub_sidecar_invalid")
            raise HubError("MCP Hub 隔离服务拒绝请求。", code=code or "hub_sidecar_invalid", status_code=502)
        return response

    async def authorize(self, candidate_id: str, url: str) -> str:
        response = await self._request(self.egress_socket, {"action": "authorize", "candidate_id": candidate_id, "url": url})
        capability = str(response.get("capability") or "")
        if re.fullmatch(r"[0-9a-f]{64}", capability) is None:
            raise HubError("MCP Hub 出口能力无效。", code="hub_egress_invalid", status_code=502)
        return capability

    async def revoke(self, capability: str) -> None:
        if capability:
            await self._request(self.egress_socket, {"action": "revoke", "capability": capability}, timeout=5)

    async def open(
        self,
        candidate_id: str,
        url: str,
        capability: str,
        session_owner: str,
        *,
        auth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
                "action": "open",
                "candidate_id": candidate_id,
                "url": url,
                "capability": capability,
                "session_owner": session_owner,
        }
        if auth is not None:
            payload["auth"] = dict(auth)
        try:
            return await self._request(self.remote_socket, payload, timeout=35)
        finally:
            nested = payload.get("auth")
            if isinstance(nested, dict):
                nested["header_value"] = ""

    async def list_tools(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            self.remote_socket,
            {"action": "list_tools", "session_id": session_id},
            timeout=CALL_TIMEOUT_SECONDS + 5,
        )

    async def call(self, session_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request(self.remote_socket, {"action": "call", "session_id": session_id, "tool_name": tool_name, "arguments": arguments}, timeout=CALL_TIMEOUT_SECONDS + 5)

    async def close(self, session_id: str) -> None:
        if session_id:
            await self._request(self.remote_socket, {"action": "close", "session_id": session_id}, timeout=5)

    async def reset(self) -> None:
        await self._request(self.remote_socket, {"action": "reset"}, timeout=8)
        await self._request(self.egress_socket, {"action": "reset"}, timeout=8)


@dataclass(slots=True)
class LiveHubSession:
    session_id: str
    capability: str
    schema_digest: str
    session_owner: str
    created_at: float
    last_activity: float


class MCPHubService:
    def __init__(
        self,
        store: MCPHubStore,
        *,
        tenant_id: str,
        owner_id: str,
        registry_client: Any | None = None,
        bridge: HubBridgeProtocol | None = None,
        reviewed_contracts: dict[tuple[str, str, str], dict[str, Any]] | None = None,
        contract_registry: HubContractRegistry | None = None,
    ) -> None:
        self.store = store
        self.tenant_id = str(tenant_id or "local")
        self.owner_id = str(owner_id or "local")
        self.registry_client = registry_client or PinnedRegistryClient()
        self.bridge = bridge or HubSocketBridge()
        self.reviewed_contracts = reviewed_contracts
        self.contract_registry = contract_registry or HubContractRegistry()
        self.review_service: Any | None = None
        self.trusted_service: Any | None = None
        self.remote_auth_broker: Any | None = None
        self.remote_oauth_service: Any | None = None
        self.credential_creator: Any | None = None
        self.credential_lookup: Any | None = None
        self.credential_revoker: Any | None = None
        self._sync_lock = asyncio.Lock()
        self._sync_tasks: dict[str, asyncio.Task[None]] = {}
        self._refresh_task: asyncio.Task[None] | None = None
        self._session_cleanup_task: asyncio.Task[None] | None = None
        self._live: dict[str, LiveHubSession] = {}
        self._candidate_locks: dict[str, asyncio.Lock] = {}

    def set_review_service(self, service: Any) -> None:
        self.review_service = service

    def set_trusted_service(self, service: Any) -> None:
        self.trusted_service = service

    def set_remote_auth(
        self,
        broker: Any,
        *,
        credential_creator: Any,
        credential_lookup: Any,
        credential_revoker: Any,
    ) -> None:
        self.remote_auth_broker = broker
        self.credential_creator = credential_creator
        self.credential_lookup = credential_lookup
        self.credential_revoker = credential_revoker

    def set_remote_oauth(self, service: Any) -> None:
        self.remote_oauth_service = service

    def _require_enabled(self) -> None:
        if not hub_enabled():
            raise HubError("MCP Hub 当前未启用。", code="hub_disabled", status_code=503)

    def _require_remote(self) -> None:
        self._require_enabled()
        if not hub_remote_enabled():
            raise HubError("MCP Hub 远程试连当前未启用。", code="hub_remote_disabled", status_code=503)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": hub_enabled(),
            "remote_enabled": hub_remote_enabled(),
            "source": REGISTRY_BASE_URL,
            "snapshot_at": float(self.store.meta("snapshot_at", "0") or 0),
            "snapshot_count": int(self.store.meta("snapshot_count", "0") or 0),
            "last_sync_skipped_count": int(
                self.store.meta("last_sync_skipped_count", "0") or 0
            ),
            "refresh_interval_seconds": SYNC_INTERVAL_SECONDS,
            "manual_refresh_interval_seconds": MANUAL_SYNC_INTERVAL_SECONDS,
            "owner_scope": "local-owner",
        }

    def _ensure_refresh(self) -> None:
        if not hub_enabled() or self._refresh_task and not self._refresh_task.done():
            return
        if float(self.store.meta("snapshot_at", "0") or 0) <= 0:
            return
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    def _ensure_session_cleanup(self) -> None:
        if (
            not hub_remote_enabled()
            or self._session_cleanup_task
            and not self._session_cleanup_task.done()
        ):
            return
        self._session_cleanup_task = asyncio.create_task(
            self._session_cleanup_loop()
        )

    async def start(self) -> None:
        self.store.recover_started_executions(self.tenant_id, self.owner_id)
        if hub_remote_enabled():
            await self.bridge.reset()
            self._ensure_session_cleanup()
        self._ensure_refresh()

    async def close(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._session_cleanup_task:
            self._session_cleanup_task.cancel()
        tasks = [
            task
            for task in [
                self._refresh_task,
                self._session_cleanup_task,
                *self._sync_tasks.values(),
            ]
            if task
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for candidate_id in list(self._live):
            await self._disconnect_live(candidate_id)

    @staticmethod
    def _live_session_current(
        live: LiveHubSession, now: float | None = None
    ) -> bool:
        checked_at = time.monotonic() if now is None else now
        return (
            checked_at - live.created_at < SESSION_TTL_SECONDS
            and checked_at - live.last_activity < SESSION_IDLE_SECONDS
        )

    async def _cleanup_expired_live_sessions(self) -> None:
        now = time.monotonic()
        for candidate_id, observed in list(self._live.items()):
            if self._live_session_current(observed, now):
                continue
            lock = self._candidate_locks.setdefault(candidate_id, asyncio.Lock())
            async with lock:
                current = self._live.get(candidate_id)
                if (
                    current is observed
                    and not self._live_session_current(current)
                ):
                    await self._disconnect_live(candidate_id)

    async def _session_cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
            try:
                await self._cleanup_expired_live_sessions()
            except Exception:
                pass

    async def _refresh_loop(self) -> None:
        while True:
            snapshot_at = float(self.store.meta("snapshot_at", "0") or 0)
            requested_at = float(
                self.store.meta("last_sync_requested_at", "0") or 0
            )
            delay = max(
                0.0,
                max(snapshot_at, requested_at) + SYNC_INTERVAL_SECONDS - time.time(),
            )
            await asyncio.sleep(delay)
            try:
                self.store.set_meta("last_sync_requested_at", str(time.time()))
                await self._run_sync(self.store.create_sync())
            except Exception:
                pass

    def request_sync(self) -> str:
        self._require_enabled()
        active = next(
            (sync_id for sync_id, task in self._sync_tasks.items() if not task.done()),
            None,
        )
        if active:
            return active
        last = max(
            float(self.store.meta("snapshot_at", "0") or 0),
            float(self.store.meta("last_sync_requested_at", "0") or 0),
        )
        if last and time.time() - last < MANUAL_SYNC_INTERVAL_SECONDS:
            raise HubError("MCP Hub 刷新过于频繁。", code="hub_sync_rate_limited", status_code=429)
        self.store.set_meta("last_sync_requested_at", str(time.time()))
        sync_id = self.store.create_sync()
        self._sync_tasks[sync_id] = asyncio.create_task(self._run_sync(sync_id))
        return sync_id

    async def _run_sync(self, sync_id: str) -> None:
        async with self._sync_lock:
            try:
                cursor = ""
                etag = self.store.meta("snapshot_etag", "")
                entries: list[dict[str, Any]] = []
                seen: set[tuple[str, str]] = set()
                skipped_entries = 0
                snapshot_bytes = 0
                response_etag = etag
                page_limit = REGISTRY_PAGE_LIMIT
                for page in range(REGISTRY_MAX_PAGES):
                    page_error: HubError | None = None
                    for attempt in range(REGISTRY_PAGE_ATTEMPTS):
                        try:
                            request_limit = page_limit
                            payload, current_etag, not_modified = await asyncio.to_thread(
                                self.registry_client.get_page,
                                cursor=cursor,
                                etag=etag if page == 0 else "",
                                limit=request_limit,
                            )
                            if request_limit == REGISTRY_FALLBACK_PAGE_LIMIT:
                                page_limit = REGISTRY_PAGE_LIMIT
                            break
                        except HubError as exc:
                            page_error = exc
                            if (
                                exc.code == "hub_registry_unavailable"
                                and page_limit == REGISTRY_PAGE_LIMIT
                            ):
                                # The official Registry can stall on otherwise
                                # valid cursors when a large page is requested.
                                # Keep all existing snapshot bounds and use one
                                # smaller page to advance beyond that cursor.
                                page_limit = REGISTRY_FALLBACK_PAGE_LIMIT
                            if (
                                exc.code != "hub_registry_unavailable"
                                or attempt + 1 >= REGISTRY_PAGE_ATTEMPTS
                            ):
                                raise
                            await asyncio.sleep(0.25 * (2**attempt))
                    else:
                        raise page_error or HubError(
                            "官方 Registry 暂不可用。",
                            code="hub_registry_unavailable",
                            status_code=503,
                        )
                    if not_modified:
                        self.store.finish_sync(sync_id, status="not_modified", count=int(self.store.meta("snapshot_count", "0") or 0))
                        if self.trusted_service is not None:
                            self.trusted_service.on_registry_sync()
                        return
                    response_etag = current_etag or response_etag
                    raw_servers = payload.get("servers")
                    metadata = payload.get("metadata")
                    if not isinstance(raw_servers, list) or not isinstance(metadata, dict):
                        raise HubError("Registry 分页结构漂移。", code="hub_registry_schema_drift")
                    for raw in raw_servers:
                        try:
                            item = normalize_registry_entry(raw)
                        except HubError as exc:
                            if exc.code != "hub_identifier_invalid":
                                raise
                            skipped_entries += 1
                            continue
                        key = (item["server_name"], item["version"])
                        if key in seen:
                            continue
                        encoded_size = len(_json_bytes(item))
                        if (
                            len(entries) >= REGISTRY_MAX_ENTRIES
                            or snapshot_bytes + encoded_size
                            > REGISTRY_MAX_SNAPSHOT_BYTES
                        ):
                            raise HubError(
                                "Registry 快照超过总量限制。",
                                code="hub_registry_snapshot_limit",
                            )
                        seen.add(key)
                        entries.append(item)
                        snapshot_bytes += encoded_size
                    cursor = str(metadata.get("nextCursor") or "")
                    if not cursor:
                        break
                else:
                    raise HubError("Registry 分页超过上限。", code="hub_registry_page_limit")
                self.store.replace_snapshot(sync_id, entries, response_etag)
                if self.review_service is not None:
                    await self.review_service.reconcile_registry_drift()
                if self.trusted_service is not None:
                    self.trusted_service.on_registry_sync()
                self.store.set_meta("last_sync_skipped_count", str(skipped_entries))
                self.store.finish_sync(sync_id, status="completed", count=len(entries))
                self._ensure_refresh()
            except HubError as exc:
                self.store.finish_sync(sync_id, status="failed", error_code=exc.code)
            except Exception:
                self.store.finish_sync(sync_id, status="failed", error_code="hub_registry_unavailable")
            finally:
                self._sync_tasks.pop(sync_id, None)

    def list_servers(self, *, query: str = "", category: str = "", eligibility: str = "", limit: int = 50, cursor: int = 0) -> dict[str, Any]:
        self._require_enabled()
        items, total = self.store.list_servers(
            query=query,
            category=category,
            eligibility=eligibility,
            limit=limit,
            offset=cursor,
        )
        next_cursor = cursor + len(items) if cursor + len(items) < total else None
        return {
            "items": items,
            "total": total,
            "next_cursor": next_cursor,
            "categories": self.store.list_categories(),
        }

    def get_server(self, name: str, version: str) -> dict[str, Any]:
        self._require_enabled()
        item = self.store.get_server(
            _required_identifier(name, SERVER_NAME_RE, "server_name"),
            _required_identifier(version, VERSION_RE, "version"),
        )
        if item is None:
            raise HubError("Registry 版本不存在。", code="hub_server_not_found", status_code=404)
        return item

    def create_candidate(self, server_name: str, version: str, remote_id: str) -> dict[str, Any]:
        self._require_enabled()
        server = self.get_server(server_name, version)
        if server["status"] not in {"active", "published"}:
            raise HubError("该 Registry 版本已下架。", code="hub_server_removed", status_code=409)
        clean_remote_id = _required_identifier(remote_id, REMOTE_ID_RE, "remote_id")
        remote = next((item for item in server["remotes"] if item["remote_id"] == clean_remote_id), None)
        if remote is None:
            raise HubError("Registry 远程端点不存在。", code="hub_remote_not_found", status_code=404)
        if remote["eligibility"] not in {
            "eligible",
            "static_token_candidate",
            "oauth_discovery_candidate",
        }:
            raise HubError("该远程端点不满足第一轮准入条件。", code="hub_remote_ineligible", status_code=409)
        return self.store.create_candidate(
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
            server=server,
            remote=remote,
        )

    def list_candidates(self) -> list[dict[str, Any]]:
        self._require_enabled()
        return [self._decorate_candidate(item) for item in self.store.list_candidates(self.tenant_id, self.owner_id)]

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        return self._decorate_candidate(self.store.require_candidate(clean, self.tenant_id, self.owner_id))

    def _decorate_candidate(self, item: dict[str, Any]) -> dict[str, Any]:
        output = dict(item)
        output.pop("remote_url", None)
        output.pop("auth_binding_id", None)
        policy = dict(output.pop("auth_policy", {}) or {})
        output["auth_required"] = bool(policy)
        output["auth_mode"] = str(policy.get("mode") or "")
        output["auth_header_name"] = str(policy.get("header_name") or "")
        output["auth_slot"] = str(policy.get("slot") or "")
        output["auth_policy_fingerprint"] = str(
            policy.get("policy_fingerprint") or ""
        )
        server = self.store.get_server(item["server_name"], item["version"])
        remote = next(
            (
                candidate_remote
                for candidate_remote in (server or {}).get("remotes", [])
                if candidate_remote.get("remote_id") == item["remote_id"]
            ),
            None,
        )
        registry_eligibility = str((remote or {}).get("eligibility") or "")
        output["registry_eligibility"] = registry_eligibility
        oauth_source = self._candidate_oauth_source(item, remote=remote)
        if (
            not oauth_source
            and registry_eligibility == "eligible"
            and item.get("state") == "blocked"
            and item.get("taint_reason") == "hub_upstream_auth_required"
        ):
            oauth_source = "pending_www_authenticate"
        output["oauth_discovery_source"] = oauth_source
        output["oauth_discovery_available"] = bool(
            item.get("remote_url")
            and not policy
            and oauth_source
        )
        live = self._live.get(item["candidate_id"])
        output["connected"] = bool(
            live is not None and self._live_session_current(live)
        )
        eligible, reason = self._activation_review(item)
        output["activation_eligible"] = eligible
        output["activation_reason"] = reason
        return output

    def _require_remote_oauth(self) -> Any:
        if self.remote_oauth_service is None:
            raise HubError(
                "远程 OAuth 发现基础尚未配置。",
                code="mcp_remote_oauth_unconfigured",
                status_code=503,
            )
        return self.remote_oauth_service

    def _candidate_oauth_source(
        self,
        candidate: dict[str, Any],
        *,
        remote: dict[str, Any] | None = None,
    ) -> str:
        server = self.store.get_server(candidate["server_name"], candidate["version"])
        current_remote = remote or next(
            (
                item
                for item in (server or {}).get("remotes", [])
                if item.get("remote_id") == candidate["remote_id"]
            ),
            None,
        )
        if (
            server is None
            or server.get("source_digest") != candidate.get("source_digest")
            or current_remote is None
            or current_remote.get("url") != candidate.get("remote_url")
            or current_remote.get("origin") != candidate.get("origin")
        ):
            return ""
        if current_remote.get("eligibility") == "oauth_discovery_candidate":
            return "registry"
        if (
            current_remote.get("eligibility") == "eligible"
            and candidate.get("oauth_discovery_source") == "www_authenticate"
        ):
            return "www_authenticate"
        return ""

    def _require_oauth_candidate(
        self, candidate: dict[str, Any], *, allow_pending_challenge: bool = False
    ) -> str:
        source = self._candidate_oauth_source(candidate)
        if not source and allow_pending_challenge:
            server = self.store.get_server(candidate["server_name"], candidate["version"])
            remote = next(
                (
                    item
                    for item in (server or {}).get("remotes", [])
                    if item.get("remote_id") == candidate["remote_id"]
                ),
                None,
            )
            if (
                server is not None
                and server.get("source_digest") == candidate.get("source_digest")
                and (remote or {}).get("eligibility") == "eligible"
                and remote.get("url") == candidate.get("remote_url")
                and remote.get("origin") == candidate.get("origin")
                and candidate.get("state") == "blocked"
                and candidate.get("taint_reason") == "hub_upstream_auth_required"
            ):
                return "pending_www_authenticate"
        if not source:
            raise HubError(
                "该候选尚未返回可验证的 OAuth Bearer 挑战。",
                code="mcp_remote_oauth_candidate_ineligible",
                status_code=409,
            )
        return source

    @staticmethod
    def _raise_remote_oauth(exc: RemoteOAuthError) -> None:
        raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None

    def candidate_oauth(self, candidate_id: str) -> dict[str, Any]:
        self._require_enabled()
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id,
            self.owner_id,
        )
        if self._candidate_auth_policy(candidate) is not None:
            raise HubError(
                "静态 Header 候选不能切换为 OAuth 发现。",
                code="mcp_remote_oauth_candidate_ineligible",
                status_code=409,
            )
        self._require_oauth_candidate(candidate, allow_pending_challenge=True)
        try:
            return self._require_remote_oauth().summary(
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
                source_digest=candidate["source_digest"],
            )
        except RemoteOAuthError as exc:
            self._raise_remote_oauth(exc)

    async def discover_candidate_oauth(
        self, candidate_id: str, *, expected_source_digest: str
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            if expected_source_digest != candidate["source_digest"]:
                raise HubError(
                    "Registry 候选来源已漂移。",
                    code="mcp_remote_oauth_source_drift",
                    status_code=409,
                )
            if self._candidate_auth_policy(candidate) is not None:
                raise HubError(
                    "静态 Header 候选不能切换为 OAuth 发现。",
                    code="mcp_remote_oauth_candidate_ineligible",
                    status_code=409,
                )
            discovery_source = self._require_oauth_candidate(
                candidate, allow_pending_challenge=True
            )
            try:
                await self._require_remote_oauth().discover(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    resource_url=candidate["remote_url"],
                    source_digest=candidate["source_digest"],
                    require_bearer_challenge=(
                        discovery_source == "pending_www_authenticate"
                    ),
                )
                if discovery_source == "pending_www_authenticate":
                    self.store.mark_candidate_oauth_discovery(
                        candidate["candidate_id"],
                        self.tenant_id,
                        self.owner_id,
                        source="www_authenticate",
                    )
                return self.candidate_oauth(clean)
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    async def register_candidate_oauth_client(
        self,
        candidate_id: str,
        *,
        expected_discovery_fingerprint: str,
        mode: str,
        client_id: str = "",
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            try:
                await self._require_remote_oauth().register_client(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    source_digest=candidate["source_digest"],
                    expected_discovery_fingerprint=expected_discovery_fingerprint,
                    mode=mode,
                    client_id=client_id,
                )
                return self.candidate_oauth(clean)
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    async def revoke_candidate_oauth_client(
        self, candidate_id: str, registration_id: str
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            try:
                self._require_remote_oauth().revoke_registration(
                    registration_id,
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                )
                return self.candidate_oauth(clean)
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    def _require_oauth_authorization(self) -> Any:
        oauth = self._require_remote_oauth()
        service = getattr(oauth, "authorization_service", None)
        if service is None:
            raise HubError(
                "OAuth 用户授权尚未配置。",
                code="mcp_remote_oauth_authorization_unconfigured",
                status_code=503,
            )
        return service

    async def create_candidate_oauth_authorization(
        self,
        candidate_id: str,
        *,
        expected_discovery_fingerprint: str,
        expected_registration_digest: str,
        expected_scope_digest: str,
        request_refresh_token: bool,
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            try:
                return self._require_oauth_authorization().create_authorization(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    source_digest=candidate["source_digest"],
                    expected_discovery_fingerprint=expected_discovery_fingerprint,
                    expected_registration_digest=expected_registration_digest,
                    expected_scope_digest=expected_scope_digest,
                    request_refresh_token=request_refresh_token,
                )
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    async def cancel_candidate_oauth_authorization(
        self, candidate_id: str, session_id: str
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            try:
                self._require_oauth_authorization().cancel(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    session_id=session_id,
                )
                return self.candidate_oauth(clean)
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    async def refresh_candidate_oauth_token(
        self,
        candidate_id: str,
        token_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            try:
                await self._require_oauth_authorization().refresh(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    token_id=token_id,
                    expected_revision=expected_revision,
                )
                return self.candidate_oauth(clean)
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    async def revoke_candidate_oauth_token(
        self, candidate_id: str, token_id: str
    ) -> dict[str, Any]:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            try:
                self._require_oauth_authorization().revoke(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    token_id=token_id,
                )
                return self.candidate_oauth(clean)
            except RemoteOAuthError as exc:
                self._raise_remote_oauth(exc)

    def _candidate_auth_policy(
        self, candidate: dict[str, Any]
    ) -> RemoteAuthPolicyV1 | None:
        raw = candidate.get("auth_policy")
        if not isinstance(raw, dict) or not raw:
            return None
        try:
            return RemoteAuthPolicyV1.model_validate(raw)
        except RemoteAuthError as exc:
            raise HubError(
                "Registry 认证策略不再满足固定边界。",
                code=exc.code,
                status_code=exc.status_code,
            ) from None

    def _require_remote_auth(self) -> Any:
        if self.remote_auth_broker is None:
            raise HubError(
                "远程认证 Broker 尚未配置。",
                code="mcp_remote_auth_disabled",
                status_code=503,
            )
        return self.remote_auth_broker

    @staticmethod
    def _raise_remote_auth(exc: RemoteAuthError) -> None:
        raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None

    def candidate_auth(self, candidate_id: str) -> dict[str, Any]:
        self._require_enabled()
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id,
            self.owner_id,
        )
        policy = self._candidate_auth_policy(candidate)
        if policy is None:
            return {"required": False, "binding": None}
        broker = self._require_remote_auth()
        try:
            binding = broker.binding_for_target(
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
                current_policy=policy,
            )
        except RemoteAuthError as exc:
            self._raise_remote_auth(exc)
        summary: dict[str, Any] | None = None
        if binding is not None:
            credential = None
            try:
                credential = self.credential_lookup(
                    binding.credential_id,
                    tenant_id=self.tenant_id,
                    owner_id=self.owner_id,
                )
            except Exception:
                credential = None
            summary = {
                "binding_id": binding.binding_id,
                "revision": binding.revision,
                "status": binding.status,
                "masked_value": str(getattr(credential, "masked_value", "")),
                "display_name": str(getattr(credential, "name", "")),
            }
        return {
            "required": True,
            "mode": policy.mode,
            "slot": policy.slot,
            "header_name": policy.header_name,
            "origin": policy.origin,
            "policy_fingerprint": policy.policy_fingerprint,
            "binding": summary,
            "single_owner_warning": True,
        }

    def create_candidate_auth_binding(
        self,
        candidate_id: str,
        *,
        slot: str,
        display_name: str,
        secret: str,
    ) -> dict[str, Any]:
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id,
            self.owner_id,
        )
        policy = self._candidate_auth_policy(candidate)
        if policy is None or slot != policy.slot:
            raise HubError(
                "候选不满足固定静态认证策略。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )
        broker = self._require_remote_auth()
        if not all((self.credential_creator, self.credential_lookup, self.credential_revoker)):
            raise HubError(
                "远程认证凭据存储尚未配置。",
                code="mcp_remote_auth_credential_unavailable",
                status_code=503,
            )
        credential = None
        try:
            credential, _ = self.credential_creator(
                name=display_name,
                value=secret,
                kind="header",
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
            )
            binding = broker.create_binding(
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
                policy=policy,
                credential_id=credential.credential_id,
            )
        except RemoteAuthError as exc:
            if credential is not None:
                try:
                    self.credential_revoker(
                        credential.credential_id,
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                    )
                except Exception:
                    pass
            self._raise_remote_auth(exc)
        except Exception:
            if credential is not None:
                try:
                    self.credential_revoker(
                        credential.credential_id,
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                    )
                except Exception:
                    pass
            raise HubError(
                "远程认证凭据当前不可写入。",
                code="mcp_remote_auth_credential_unavailable",
                status_code=503,
            ) from None
        self.store.set_candidate_auth_binding(
            candidate["candidate_id"], self.tenant_id, self.owner_id, binding.binding_id
        )
        return self.candidate_auth(candidate["candidate_id"])

    async def rotate_candidate_auth_binding(
        self,
        candidate_id: str,
        binding_id: str,
        *,
        secret: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            return await self._rotate_candidate_auth_binding_locked(
                clean,
                binding_id,
                secret=secret,
                expected_revision=expected_revision,
            )

    async def _rotate_candidate_auth_binding_locked(
        self,
        candidate_id: str,
        binding_id: str,
        *,
        secret: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id,
            self.owner_id,
        )
        clean_binding_id = str(binding_id or "").strip()
        if clean_binding_id != candidate.get("auth_binding_id"):
            raise HubError(
                "远程认证绑定不存在。",
                code="mcp_remote_auth_binding_missing",
                status_code=404,
            )
        policy = self._candidate_auth_policy(candidate)
        if policy is None:
            raise HubError(
                "候选不满足固定静态认证策略。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )
        broker = self._require_remote_auth()
        new_credential = None
        try:
            current = broker.get_binding(
                clean_binding_id,
                current_policy=policy,
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
            )
            previous = self.credential_lookup(
                current.credential_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
            )
            new_credential, _ = self.credential_creator(
                name=str(getattr(previous, "name", "Hub MCP Token")),
                value=secret,
                kind="header",
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
            )
            self.credential_revoker(
                current.credential_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
            )
            binding = broker.rotate_binding(
                clean_binding_id,
                current_policy=policy,
                credential_id=new_credential.credential_id,
                expected_revision=expected_revision,
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
            )
        except RemoteAuthError as exc:
            if new_credential is not None:
                try:
                    self.credential_revoker(
                        new_credential.credential_id,
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                    )
                except Exception:
                    pass
            self._raise_remote_auth(exc)
        except Exception:
            if new_credential is not None:
                try:
                    self.credential_revoker(
                        new_credential.credential_id,
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                    )
                except Exception:
                    pass
            raise HubError(
                "远程认证凭据当前不可轮换。",
                code="mcp_remote_auth_credential_unavailable",
                status_code=503,
            ) from None
        await self._disconnect_live(candidate["candidate_id"])
        self.store.set_candidate_auth_binding(
            candidate["candidate_id"], self.tenant_id, self.owner_id, binding.binding_id
        )
        return self.candidate_auth(candidate["candidate_id"])

    async def revoke_candidate_auth_binding(
        self, candidate_id: str, binding_id: str
    ) -> None:
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            await self._revoke_candidate_auth_binding_locked(clean, binding_id)

    async def _revoke_candidate_auth_binding_locked(
        self, candidate_id: str, binding_id: str
    ) -> None:
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id,
            self.owner_id,
        )
        clean_binding_id = str(binding_id or "").strip()
        if clean_binding_id != candidate.get("auth_binding_id"):
            raise HubError(
                "远程认证绑定不存在。",
                code="mcp_remote_auth_binding_missing",
                status_code=404,
            )
        broker = self._require_remote_auth()
        policy = self._candidate_auth_policy(candidate)
        if policy is None:
            raise HubError(
                "候选不满足固定静态认证策略。",
                code="mcp_remote_auth_policy_ineligible",
                status_code=422,
            )
        await self._disconnect_live(candidate["candidate_id"])
        try:
            current = broker.binding_metadata_for_target(
                clean_binding_id,
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
            )
            self.credential_revoker(
                current.credential_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
            )
            broker.revoke_binding(
                clean_binding_id,
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
            )
        except RemoteAuthError as exc:
            self._raise_remote_auth(exc)
        except Exception:
            raise HubError(
                "远程认证凭据当前无法撤销。",
                code="mcp_remote_auth_credential_unavailable",
                status_code=503,
            ) from None
        self.store.set_candidate_auth_binding(
            candidate["candidate_id"], self.tenant_id, self.owner_id, ""
        )
        try:
            self.credential_revoker(
                current.credential_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
            )
        except Exception:
            pass

    def _reviewed_contract(
        self, candidate: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        identity = (
            str(candidate.get("server_name") or ""),
            str(candidate.get("version") or ""),
            str(candidate.get("remote_url") or ""),
        )
        if self.reviewed_contracts is not None:
            contract = self.reviewed_contracts.get(identity)
            if contract is None:
                return None, "hub_contract_unreviewed"
            normalized = dict(contract)
            normalized.setdefault(
                "allowed_tools",
                sorted(dict(normalized.get("tool_schema_digests") or {})),
            )
            normalized.setdefault("contract_id", "legacy-test-contract")
            normalized.setdefault("contract_fingerprint", stable_digest(normalized))
        else:
            loaded, reason = self.contract_registry.lookup_identity(*identity)
            if loaded is None:
                return None, reason
            normalized = loaded.model_dump(mode="json")
        if normalized.get("schema_version") == "hub-reviewed-contract-v3":
            return None, "mcp_remote_oauth_runtime_disabled"
        current_server = self.store.get_server(identity[0], identity[1])
        current_remote = next(
            (
                item
                for item in (current_server or {}).get("remotes", [])
                if item.get("remote_id") == candidate.get("remote_id")
            ),
            None,
        )
        if (
            current_server is None
            or current_server.get("status") not in {"active", "published"}
            or current_server.get("source_digest") != candidate.get("source_digest")
            or current_remote is None
            or current_remote.get("url") != identity[2]
            or (current_remote.get("auth_policy") or {})
            != (candidate.get("auth_policy") or {})
        ):
            return None, "hub_source_drift"
        policy = self._candidate_auth_policy(candidate)
        frozen_policy = normalized.get("remote_auth_policy")
        if policy is None:
            if frozen_policy is not None:
                return None, "hub_reviewed_contract_drift"
        else:
            if not isinstance(frozen_policy, dict):
                return None, "hub_contract_unreviewed"
            try:
                contract_policy = RemoteAuthPolicyV1.model_validate(frozen_policy)
            except RemoteAuthError:
                return None, "hub_reviewed_contract_drift"
            if contract_policy != policy:
                return None, "hub_reviewed_contract_drift"
            binding_id = str(candidate.get("auth_binding_id") or "")
            if not binding_id or self.remote_auth_broker is None:
                return None, "mcp_remote_auth_binding_missing"
            try:
                binding = self.remote_auth_broker.get_binding(
                    binding_id,
                    current_policy=policy,
                    target_type="hub_candidate",
                    target_id=str(candidate.get("candidate_id") or ""),
                )
            except RemoteAuthError as exc:
                return None, exc.code
            if binding.target_type != "hub_candidate" or binding.target_id != candidate.get(
                "candidate_id"
            ):
                return None, "mcp_remote_auth_scope_denied"
        frozen_source = str(normalized.get("source_digest") or "")
        if frozen_source and frozen_source != str(candidate.get("source_digest") or ""):
            return None, "hub_contract_source_drift"
        schema_digest = str(candidate.get("schema_digest") or "")
        if not schema_digest:
            return None, "hub_preflight_required"
        expected_tools = dict(normalized.get("tool_schema_digests") or {})
        actual_tools = {
            str(tool.get("name") or ""): str(tool.get("schema_digest") or "")
            for tool in candidate.get("tools") or []
            if isinstance(tool, dict)
        }
        if (
            schema_digest != str(normalized.get("schema_digest") or "")
            or actual_tools != expected_tools
        ):
            return None, "hub_reviewed_contract_drift"
        allowed_tools = set(normalized.get("allowed_tools") or [])
        if not allowed_tools or not allowed_tools.issubset(actual_tools):
            return None, "hub_reviewed_contract_drift"
        if self.trusted_service is not None:
            available, availability_reason = self.trusted_service.activation_guard(
                str(normalized.get("contract_id") or ""),
                str(normalized.get("contract_fingerprint") or ""),
            )
            if not available:
                return None, availability_reason
        return normalized, ""

    def _activation_review(self, candidate: dict[str, Any]) -> tuple[bool, str]:
        contract, reason = self._reviewed_contract(candidate)
        return contract is not None, reason

    def _validate_tools(self, raw_tools: Any) -> tuple[list[dict[str, Any]], str]:
        if not isinstance(raw_tools, list) or not raw_tools or len(raw_tools) > MAX_TOOL_COUNT:
            raise HubError("远程工具数量不符合限制。", code="hub_tool_contract_denied", status_code=409)
        tools: list[dict[str, Any]] = []
        names: set[str] = set()
        total = 0
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise HubError("远程工具结构无效。", code="hub_tool_contract_denied", status_code=409)
            name = str(raw.get("name") or "").strip()
            schema = raw.get("input_schema")
            if TOOL_NAME_RE.fullmatch(name) is None or name in names or not isinstance(schema, dict):
                raise HubError("远程工具结构无效。", code="hub_tool_contract_denied", status_code=409)
            encoded = _json_bytes(schema)
            if len(encoded) > MAX_TOOL_SCHEMA_BYTES:
                raise HubError("远程工具 Schema 超过上限。", code="hub_tool_contract_denied", status_code=409)
            total += len(encoded)
            names.add(name)
            tools.append({
                "name": name,
                "description": str(raw.get("description") or "")[:4000],
                "input_schema": schema,
                "schema_digest": stable_digest(schema),
            })
        if total > MAX_TOTAL_SCHEMA_BYTES:
            raise HubError("远程工具 Schema 总量超过上限。", code="hub_tool_contract_denied", status_code=409)
        tools.sort(key=lambda item: item["name"])
        return tools, stable_digest(tools)

    async def inspect_reviewed_contract(
        self, contract: HubReviewedContractV1
    ) -> dict[str, Any]:
        """Inspect a reviewed identity without creating a persisted candidate."""

        self._require_remote()
        server = self.store.get_server(contract.server_name, contract.version)
        remote = next(
            (
                item
                for item in (server or {}).get("remotes", [])
                if item.get("url") == contract.remote_url
            ),
            None,
        )
        if (
            server is None
            or server.get("status") not in {"active", "published"}
            or remote is None
            or remote.get("eligibility") != "eligible"
            or (
                contract.source_digest
                and server.get("source_digest") != contract.source_digest
            )
        ):
            raise HubError(
                "可信契约与当前 Registry 身份不一致。",
                code="hub_source_drift",
                status_code=409,
            )
        probe_id = "mcphub_" + uuid.uuid4().hex
        session_id = ""
        capability = await self.bridge.authorize(probe_id, contract.remote_url)
        try:
            session_owner = (
                "hub:"
                + quote(self.tenant_id, safe="")
                + ":"
                + quote(self.owner_id, safe="")
                + ":"
                + probe_id
            )
            response = await self.bridge.open(
                probe_id,
                contract.remote_url,
                capability,
                session_owner,
            )
            session_id = str(response.get("session_id") or "")
            if not session_id:
                raise HubError(
                    "MCP Hub 临时检查会话无效。",
                    code="hub_sidecar_invalid",
                    status_code=502,
                )
            tools, schema_digest = self._validate_tools(response.get("tools"))
            actual_tools = {
                str(tool["name"]): str(tool["schema_digest"]) for tool in tools
            }
            if (
                schema_digest != contract.schema_digest
                or actual_tools != dict(contract.tool_schema_digests)
            ):
                raise HubError(
                    "远程工具 Schema 与可信契约不一致。",
                    code="hub_schema_drift",
                    status_code=409,
                )
            return {
                "schema_digest": schema_digest,
                "tools": tools,
                "origin": contract.origin,
                "source_digest": str(server.get("source_digest") or ""),
                "remote_id": str(remote.get("remote_id") or ""),
            }
        finally:
            await asyncio.gather(
                self.bridge.close(session_id) if session_id else asyncio.sleep(0),
                self.bridge.revoke(capability),
                return_exceptions=True,
            )

    async def _open_candidate(
        self,
        candidate: dict[str, Any],
        *,
        allow_oauth_review: bool = False,
        expected_oauth_context: dict[str, str] | None = None,
    ) -> LiveHubSession:
        capability = await self.bridge.authorize(candidate["candidate_id"], candidate["remote_url"])
        session_id = ""
        try:
            session_owner = (
                "hub:"
                + quote(self.tenant_id, safe="")
                + ":"
                + quote(self.owner_id, safe="")
                + ":"
                + candidate["candidate_id"]
            )
            policy = self._candidate_auth_policy(candidate)
            server = self.store.get_server(candidate["server_name"], candidate["version"])
            remote = next(
                (
                    item
                    for item in (server or {}).get("remotes", [])
                    if item.get("remote_id") == candidate.get("remote_id")
                ),
                None,
            )
            oauth_candidate = bool(self._candidate_oauth_source(candidate, remote=remote))
            if oauth_candidate:
                if not allow_oauth_review or not _flag("MCP_REMOTE_OAUTH_REVIEW_ENABLED"):
                    raise HubError(
                        "OAuth 候选仅允许通过启用的 Review Factory 内部路径连接。",
                        code="mcp_remote_oauth_review_disabled",
                        status_code=409,
                    )
                authorization = self._require_oauth_authorization()
                try:
                    subject = authorization.subject_resolver.resolve()
                    if (
                        subject.tenant_id != self.tenant_id
                        or subject.owner_id != self.owner_id
                    ):
                        raise HubError(
                            "OAuth 执行主体与 Hub Owner 不一致。",
                            code="mcp_remote_oauth_scope_denied",
                            status_code=403,
                        )
                    metadata = authorization.execution_metadata(
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                        source_digest=candidate["source_digest"],
                    )
                    if metadata.origin != candidate["origin"]:
                        raise HubError(
                            "OAuth Origin 与 Registry 候选不一致。",
                            code="mcp_remote_oauth_scope_denied",
                            status_code=409,
                        )
                    expected = expected_oauth_context or {
                        "policy_fingerprint": metadata.policy_fingerprint,
                        "scope_digest": metadata.scope_digest,
                        "token_revision_digest": metadata.token_revision_digest,
                        "resource_digest": metadata.resource_digest,
                        "discovery_fingerprint": metadata.discovery_fingerprint,
                        "registration_digest": metadata.registration_digest,
                    }
                    if (
                        metadata.policy_fingerprint
                        != expected.get("policy_fingerprint")
                        or metadata.scope_digest != expected.get("scope_digest")
                        or metadata.token_revision_digest
                        != expected.get("token_revision_digest")
                        or metadata.resource_digest != expected.get("resource_digest")
                        or metadata.discovery_fingerprint
                        != expected.get("discovery_fingerprint")
                        or metadata.registration_digest
                        != expected.get("registration_digest")
                    ):
                        raise HubError(
                            "OAuth 复核证据已变化。",
                            code="mcp_remote_oauth_contract_scope_drift",
                            status_code=409,
                        )
                    with authorization.resolve_for_execution(
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                        source_digest=candidate["source_digest"],
                        expected_policy_fingerprint=expected["policy_fingerprint"],
                        expected_scope_digest=expected["scope_digest"],
                        expected_token_revision_digest=expected[
                            "token_revision_digest"
                        ],
                    ) as envelope:
                        auth_payload = {
                            "auth_mode": "oauth_authorization_code_pkce",
                            "header_value": envelope.authorization_value,
                            "origin": metadata.origin,
                            "policy_fingerprint": metadata.policy_fingerprint,
                            "protocol_version": metadata.protocol_version,
                            "resource_digest": metadata.resource_digest,
                            "scope_digest": metadata.scope_digest,
                            "target_id": candidate["candidate_id"],
                            "token_revision_digest": metadata.token_revision_digest,
                        }
                        try:
                            response = await self.bridge.open(
                                candidate["candidate_id"],
                                candidate["remote_url"],
                                capability,
                                session_owner,
                                auth=auth_payload,
                            )
                        finally:
                            auth_payload["header_value"] = ""
                except RemoteOAuthError as exc:
                    self._raise_remote_oauth(exc)
            elif policy is None:
                response = await self.bridge.open(
                    candidate["candidate_id"],
                    candidate["remote_url"],
                    capability,
                    session_owner,
                )
            else:
                binding_id = str(candidate.get("auth_binding_id") or "")
                if not binding_id:
                    raise HubError(
                        "候选尚未绑定远程认证凭据。",
                        code="mcp_remote_auth_binding_missing",
                        status_code=409,
                    )
                broker = self._require_remote_auth()
                try:
                    with broker.resolve_for_execution(
                        binding_id,
                        current_policy=policy,
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                    ) as envelope:
                        auth_payload = {
                            "binding_id": envelope.binding_id,
                            "binding_revision": envelope.binding_revision,
                            "header_name": envelope.header_name,
                            "header_value": envelope.header_value,
                            "origin": envelope.origin,
                            "policy_fingerprint": envelope.policy_fingerprint,
                            "target_id": candidate["candidate_id"],
                        }
                        try:
                            response = await self.bridge.open(
                                candidate["candidate_id"],
                                candidate["remote_url"],
                                capability,
                                session_owner,
                                auth=auth_payload,
                            )
                        finally:
                            auth_payload["header_value"] = ""
                except RemoteAuthError as exc:
                    self._raise_remote_auth(exc)
            tools, digest = self._validate_tools(response.get("tools"))
            expected = str(candidate.get("schema_digest") or "")
            if expected and digest != expected:
                raise HubError("远程工具 Schema 已漂移。", code="hub_schema_drift", status_code=409)
            session_id = str(response.get("session_id") or "")
            if not session_id:
                raise HubError("MCP Hub 会话无效。", code="hub_sidecar_invalid", status_code=502)
            now = time.monotonic()
            live = LiveHubSession(
                session_id=session_id,
                capability=capability,
                schema_digest=digest,
                session_owner=session_owner,
                created_at=now,
                last_activity=now,
            )
            self.store.update_candidate(
                candidate["candidate_id"],
                self.tenant_id,
                self.owner_id,
                state=("active" if candidate["state"] == "active" else "verified"),
                schema_digest=digest,
                tools=tools,
            )
            self._live[candidate["candidate_id"]] = live
            return live
        except Exception:
            await asyncio.gather(
                self.bridge.close(session_id) if session_id else asyncio.sleep(0),
                self.bridge.revoke(capability),
                return_exceptions=True,
            )
            raise

    async def preflight_oauth_review(
        self,
        candidate_id: str,
        *,
        expected_oauth_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one OAuth preflight without exposing a public activation path."""

        self._require_remote()
        if not _flag("MCP_REMOTE_OAUTH_REVIEW_ENABLED"):
            raise HubError(
                "OAuth Review Factory 当前未启用。",
                code="mcp_remote_oauth_review_disabled",
                status_code=503,
            )
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(
                clean, self.tenant_id, self.owner_id
            )
            self._require_oauth_candidate(candidate)
            await self._disconnect_live(clean)
            try:
                await self._open_candidate(
                    candidate,
                    allow_oauth_review=True,
                    expected_oauth_context=expected_oauth_context,
                )
                return self.get_candidate(clean)
            finally:
                await self._disconnect_live(clean)

    async def preflight(self, candidate_id: str) -> dict[str, Any]:
        self._require_remote()
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id, self.owner_id,
        )
        current_server = self.store.get_server(candidate["server_name"], candidate["version"])
        if current_server is None or current_server["source_digest"] != candidate["source_digest"]:
            return self._decorate_candidate(self.store.update_candidate(
                candidate["candidate_id"], self.tenant_id, self.owner_id,
                state="drifted", taint_reason="hub_source_drift",
            ))
        current_remote = next(
            (
                remote
                for remote in current_server.get("remotes", [])
                if remote.get("remote_id") == candidate["remote_id"]
            ),
            None,
        )
        if self._candidate_oauth_source(candidate, remote=current_remote):
            raise HubError(
                "OAuth 契约复核使用专用内部路径；R3A 不开放 Runtime 激活。",
                code="mcp_remote_oauth_runtime_disabled",
                status_code=409,
            )
        async with self._candidate_locks.setdefault(candidate["candidate_id"], asyncio.Lock()):
            await self._disconnect_live(candidate["candidate_id"])
            try:
                await self._open_candidate(candidate)
            except HubError as exc:
                self.store.update_candidate(candidate["candidate_id"], self.tenant_id, self.owner_id, state="blocked", taint_reason=exc.code)
                raise
            current = self.store.require_candidate(
                candidate["candidate_id"], self.tenant_id, self.owner_id
            )
            if not self._activation_review(current)[0]:
                await self._disconnect_live(candidate["candidate_id"])
        return self.get_candidate(candidate["candidate_id"])

    async def activate(self, candidate_id: str, expected_schema_digest: str) -> dict[str, Any]:
        self._require_remote()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        expected = str(expected_schema_digest or "").strip()
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(clean, self.tenant_id, self.owner_id)
            if (
                candidate["state"] != "verified"
                or not expected
                or expected != candidate["schema_digest"]
            ):
                raise HubError(
                    "候选尚未通过当前 Schema 预检。",
                    code="hub_activation_precondition",
                    status_code=409,
                )
            eligible, reason = self._activation_review(candidate)
            if not eligible:
                if reason in {"hub_source_drift", "hub_contract_source_drift"}:
                    self.store.update_candidate(
                        clean,
                        self.tenant_id,
                        self.owner_id,
                        state="drifted",
                        taint_reason=reason,
                    )
                raise HubError(
                    "该候选尚未完成 ModelMirror 执行契约复核。",
                    code=reason,
                    status_code=409,
                )
            return self._decorate_candidate(
                self.store.activate_candidate_if_current(
                    clean,
                    self.tenant_id,
                    self.owner_id,
                    expected,
                )
            )

    async def _disconnect_live(self, candidate_id: str) -> None:
        live = self._live.pop(candidate_id, None)
        if live is None:
            return
        await asyncio.gather(
            self.bridge.close(live.session_id),
            self.bridge.revoke(live.capability),
            return_exceptions=True,
        )

    async def disconnect(self, candidate_id: str) -> dict[str, Any]:
        self._require_enabled()
        candidate = self.store.require_candidate(
            _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id"),
            self.tenant_id, self.owner_id,
        )
        async with self._candidate_locks.setdefault(candidate["candidate_id"], asyncio.Lock()):
            await self._disconnect_live(candidate["candidate_id"])
            candidate = self.store.update_candidate(candidate["candidate_id"], self.tenant_id, self.owner_id, state="disconnected")
        if self.trusted_service is not None:
            self.trusted_service.record_runtime_event(
                "candidate_disconnected",
                {"candidate_id": candidate["candidate_id"]},
            )
        return self._decorate_candidate(candidate)

    async def delete_candidate(self, candidate_id: str) -> None:
        self._require_enabled()
        clean = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        async with self._candidate_locks.setdefault(clean, asyncio.Lock()):
            candidate = self.store.require_candidate(clean, self.tenant_id, self.owner_id)
            binding_id = str(candidate.get("auth_binding_id") or "")
            credential_id = ""
            if binding_id:
                broker = self._require_remote_auth()
                try:
                    binding = broker.binding_metadata_for_target(
                        binding_id,
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                    )
                    credential_id = binding.credential_id
                except RemoteAuthError as exc:
                    self._raise_remote_auth(exc)
            await self._disconnect_live(clean)
            if self.remote_oauth_service is not None:
                try:
                    self.remote_oauth_service.revoke_target_locally(
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                    )
                except RemoteOAuthError as exc:
                    self._raise_remote_oauth(exc)
            if credential_id:
                try:
                    self.credential_revoker(
                        credential_id,
                        tenant_id=self.tenant_id,
                        owner_id=self.owner_id,
                    )
                except Exception:
                    raise HubError(
                        "远程认证凭据当前无法撤销，候选未删除。",
                        code="mcp_remote_auth_credential_unavailable",
                        status_code=503,
                    ) from None
                try:
                    broker.revoke_binding(
                        binding_id,
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                    )
                except RemoteAuthError as exc:
                    self._raise_remote_auth(exc)
            self.store.delete_candidate(clean, self.tenant_id, self.owner_id)
        self._candidate_locks.pop(clean, None)

    def runtime_tools(self) -> list[dict[str, Any]]:
        if not hub_enabled() or not hub_remote_enabled():
            return []
        output: list[dict[str, Any]] = []
        for candidate in self.store.list_candidates(self.tenant_id, self.owner_id):
            contract, _reason = self._reviewed_contract(candidate)
            if candidate["state"] != "active" or contract is None:
                continue
            output.extend(self._runtime_tools_for_candidate(candidate, contract))
        return output

    def _runtime_tools_for_candidate(
        self,
        candidate: dict[str, Any],
        contract: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if contract is None:
            contract, _reason = self._reviewed_contract(candidate)
        if contract is None:
            return []
        allowed_tools = set(contract.get("allowed_tools") or [])
        prefix = hashlib.sha256(
            candidate["candidate_id"].encode("utf-8")
        ).hexdigest()[:10]
        result: list[dict[str, Any]] = []
        for tool in candidate["tools"]:
            upstream_name = str(tool["name"])
            if upstream_name not in allowed_tools:
                continue
            tool_slug = re.sub(
                r"[^a-z0-9]+", "_", upstream_name.lower()
            ).strip("_")[:35] or "tool"
            tool_hash = hashlib.sha256(upstream_name.encode("utf-8")).hexdigest()[:8]
            result.append({
                "name": f"hub__{prefix}__{tool_slug}_{tool_hash}",
                "description": "受控 MCP Hub 外部工具；Registry 收录不代表安全认证，调用前必须逐次审批。",
                "input_schema": dict(tool["input_schema"]),
                "candidate_id": candidate["candidate_id"],
                "upstream_tool_name": upstream_name,
                "tool_schema_digest": tool["schema_digest"],
                "schema_digest": candidate["schema_digest"],
                "origin": candidate["origin"],
                "server_name": candidate["server_name"],
                "version": candidate["version"],
                "contract_id": contract.get("contract_id", ""),
                "contract_fingerprint": contract.get("contract_fingerprint", ""),
            })
        return result

    async def execute(self, *, candidate_id: str, runtime_tool_name: str, upstream_tool_name: str, arguments: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        self._require_remote()
        clean_id = _required_identifier(candidate_id, CANDIDATE_ID_RE, "candidate_id")
        candidate = self.store.require_candidate(clean_id, self.tenant_id, self.owner_id)
        contract, reason = self._reviewed_contract(candidate)
        if contract is None:
            raise HubError(
                "MCP Hub 执行契约未通过复核。",
                code=reason,
                status_code=409,
            )
        args_digest = arguments_digest(arguments)
        approval_id = str(approval.get("approval_id") or "").strip()
        if APPROVAL_ID_RE.fullmatch(approval_id) is None:
            raise HubError(
                "MCP Hub 审批凭据无效。",
                code="hub_approval_invalid",
                status_code=409,
            )
        metadata = approval.get("metadata") if isinstance(approval.get("metadata"), dict) else {}
        hub_meta = metadata.get("hub_approval") if isinstance(metadata.get("hub_approval"), dict) else {}
        runtime_entry = next(
            (
                item
                for item in self._runtime_tools_for_candidate(candidate, contract)
                if item["candidate_id"] == clean_id
                and item["name"] == runtime_tool_name
            ),
            None,
        )
        if (
            runtime_entry is None
            or runtime_entry["upstream_tool_name"] != upstream_tool_name
            or approval.get("status") != "decided"
            or approval.get("decision") not in {"approve", "edit"}
            or approval.get("tool_name") != runtime_tool_name
            or hub_meta.get("candidate_id") != clean_id
            or hub_meta.get("tenant_id") != self.tenant_id
            or hub_meta.get("owner_id") != self.owner_id
            or hub_meta.get("server_name") != candidate["server_name"]
            or hub_meta.get("version") != candidate["version"]
            or hub_meta.get("origin") != candidate["origin"]
            or hub_meta.get("arguments_digest") != args_digest
            or hub_meta.get("schema_digest") != candidate["schema_digest"]
            or hub_meta.get("tool_schema_digest")
            != runtime_entry["tool_schema_digest"]
            or (
                hub_meta.get("contract_fingerprint") is not None
                and hub_meta.get("contract_fingerprint")
                != runtime_entry["contract_fingerprint"]
            )
        ):
            raise HubError("MCP Hub 审批凭据无效。", code="hub_approval_invalid", status_code=409)
        lock = self._candidate_locks.setdefault(clean_id, asyncio.Lock())
        async with lock:
            existing = self.store.find_execution(
                approval_id=approval_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
                candidate_id=clean_id,
                tool_name=runtime_tool_name,
                args_digest=args_digest,
            )
            if existing is not None:
                ledger_state, replay = existing
                if ledger_state == "completed" and replay is not None:
                    return replay
                if ledger_state == "unknown":
                    raise HubUnknownOutcomeError()
                if ledger_state == "started":
                    raise HubError(
                        "MCP Hub 审批正在执行。",
                        code="hub_execution_in_progress",
                        status_code=409,
                    )
                raise HubError(
                    "MCP Hub 审批状态无效。",
                    code="hub_execution_state_invalid",
                    status_code=409,
                )
            if candidate["state"] != "active":
                raise HubError(
                    "MCP Hub 候选当前未激活。",
                    code="hub_candidate_inactive",
                    status_code=409,
                )
            live = self._live.get(clean_id)
            if live is not None and not self._live_session_current(live):
                await self._disconnect_live(clean_id)
                live = None
            try:
                if live is None:
                    live = await self._open_candidate(candidate)
                refreshed = await self.bridge.list_tools(live.session_id)
                refreshed_tools, refreshed_digest = self._validate_tools(
                    refreshed.get("tools")
                )
                live.last_activity = time.monotonic()
            except HubError as exc:
                await self._disconnect_live(clean_id)
                if exc.code not in SAFE_SESSION_RECONNECT_CODES:
                    self.store.update_candidate(
                        clean_id,
                        self.tenant_id,
                        self.owner_id,
                        state="drifted",
                        taint_reason="hub_schema_recheck_failed",
                    )
                    raise
                try:
                    live = await self._open_candidate(candidate)
                    refreshed = await self.bridge.list_tools(live.session_id)
                    refreshed_tools, refreshed_digest = self._validate_tools(
                        refreshed.get("tools")
                    )
                    live.last_activity = time.monotonic()
                except HubError:
                    await self._disconnect_live(clean_id)
                    self.store.update_candidate(
                        clean_id,
                        self.tenant_id,
                        self.owner_id,
                        state="drifted",
                        taint_reason="hub_schema_recheck_failed",
                    )
                    raise
            if (
                live.schema_digest != candidate["schema_digest"]
                or refreshed_digest != candidate["schema_digest"]
                or refreshed_tools != candidate["tools"]
            ):
                await self._disconnect_live(clean_id)
                self.store.update_candidate(clean_id, self.tenant_id, self.owner_id, state="drifted", taint_reason="hub_schema_drift")
                raise HubError("远程工具 Schema 已漂移。", code="hub_schema_drift", status_code=409)
            ledger_state, replay = self.store.begin_execution(
                approval_id=approval_id, tenant_id=self.tenant_id, owner_id=self.owner_id,
                candidate_id=clean_id, tool_name=runtime_tool_name, args_digest=args_digest,
            )
            if ledger_state != "new":
                raise HubError(
                    "MCP Hub 审批状态冲突。",
                    code="hub_execution_state_invalid",
                    status_code=409,
                )
            try:
                live.last_activity = time.monotonic()
                response = await self.bridge.call(live.session_id, upstream_tool_name, arguments)
                result = response.get("result")
                if not isinstance(result, dict) or len(_json_bytes(result)) > MAX_RESULT_BYTES:
                    raise HubError("远程结果结构或大小无效。", code="hub_result_denied", status_code=502)
                self.store.finish_execution(approval_id, state="completed", result=result)
                return result
            except Exception as exc:
                self.store.finish_execution(approval_id, state="unknown", error_code="unknown_outcome")
                await self._disconnect_live(clean_id)
                self.store.update_candidate(clean_id, self.tenant_id, self.owner_id, state="tainted", taint_reason="unknown_outcome")
                if isinstance(exc, HubUnknownOutcomeError):
                    raise
                raise HubUnknownOutcomeError() from exc


class CandidateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    remote_id: str = Field(min_length=1, max_length=40)


class CandidateActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_schema_digest: str = Field(min_length=64, max_length=64)


class CandidateAuthBindingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    slot: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    secret: SecretStr


class CandidateAuthBindingRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    secret: SecretStr
    expected_revision: int = Field(ge=1)


class CandidateOAuthDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CandidateOAuthRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["pre_registered", "client_id_metadata_document", "dynamic"]
    client_id: str = Field(default="", max_length=2048)


class CandidateOAuthAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_discovery_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_registration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_refresh_token: bool = False


class CandidateOAuthTokenRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_revision: int = Field(ge=1)


router = APIRouter(tags=["mcp-hub"])
_hub_service: MCPHubService | None = None


def configure_mcp_hub(service: MCPHubService) -> None:
    global _hub_service
    _hub_service = service


def _service() -> MCPHubService:
    if _hub_service is None:
        raise HTTPException(status_code=503, detail={"code": "hub_unconfigured", "error": "MCP Hub 尚未配置。"})
    return _hub_service


def _raise_http(exc: HubError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "error": str(exc)}) from exc


async def _redacted_request_model(
    request: Request, model: type[BaseModel]
) -> BaseModel:
    if request.query_params:
        raise _invalid_hub_request()
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _invalid_hub_request()
    declared = request.headers.get("content-length", "").strip()
    if declared:
        try:
            if int(declared) > 8192:
                raise ValueError
        except ValueError:
            raise _invalid_hub_request() from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise _invalid_hub_request()
    raw = bytes(body)
    if not raw:
        raise _invalid_hub_request()
    try:
        value = json.loads(raw.decode("utf-8"))
        return model.model_validate(value)
    except (UnicodeError, json.JSONDecodeError, ValidationError):
        # OAuth request models intentionally accept identifiers only.  FastAPI's
        # default validation response echoes unknown input values, which could
        # expose an accidentally submitted Header or Secret.
        raise _invalid_hub_request() from None


def _invalid_hub_request() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "hub_invalid_request",
            "message": "Invalid MCP Hub request.",
        },
    )


async def _require_empty_write_request(request: Request) -> None:
    if request.query_params:
        raise _invalid_hub_request()
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > 8192:
            break
    if body_size:
        raise _invalid_hub_request()


@router.get("/api/mcp/hub/status")
async def get_hub_status() -> dict[str, Any]:
    return _service().status()


@router.post("/api/mcp/hub/sync", status_code=202)
async def start_hub_sync() -> dict[str, Any]:
    try:
        sync_id = _service().request_sync()
        return {"sync_id": sync_id, "status": "running"}
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/sync/{sync_id}")
async def get_hub_sync(sync_id: str) -> dict[str, Any]:
    item = _service().store.get_sync(str(sync_id)[:80])
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "hub_sync_not_found", "error": "同步任务不存在。"})
    return item


@router.get("/api/mcp/hub/servers")
async def list_hub_servers(
    q: str = Query("", max_length=200),
    category: str = Query("", max_length=80),
    eligibility: str = Query("", max_length=40),
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return _service().list_servers(
            query=q.strip(),
            category=category.strip(),
            eligibility=eligibility.strip(),
            limit=limit,
            cursor=cursor,
        )
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/servers/{server_name:path}/versions/{version}")
async def get_hub_server(server_name: str, version: str) -> dict[str, Any]:
    try:
        return _service().get_server(server_name, version)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/candidates", status_code=201)
async def create_hub_candidate(request: Request) -> dict[str, Any]:
    payload = await _redacted_request_model(request, CandidateCreateRequest)
    assert isinstance(payload, CandidateCreateRequest)
    try:
        return _service().create_candidate(payload.server_name, payload.version, payload.remote_id)
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/candidates")
async def list_hub_candidates() -> dict[str, Any]:
    try:
        items = _service().list_candidates()
        return {"items": items, "total": len(items)}
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/candidates/{candidate_id}")
async def get_hub_candidate(candidate_id: str) -> dict[str, Any]:
    try:
        return _service().get_candidate(candidate_id)
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/candidates/{candidate_id}/auth")
async def get_hub_candidate_auth(candidate_id: str) -> dict[str, Any]:
    try:
        return _service().candidate_auth(candidate_id)
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/candidates/{candidate_id}/oauth")
async def get_hub_candidate_oauth(candidate_id: str) -> dict[str, Any]:
    try:
        return _service().candidate_oauth(candidate_id)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/candidates/{candidate_id}/oauth/discover")
async def discover_hub_candidate_oauth(
    candidate_id: str, request: Request
) -> dict[str, Any]:
    payload = await _redacted_request_model(request, CandidateOAuthDiscoveryRequest)
    assert isinstance(payload, CandidateOAuthDiscoveryRequest)
    try:
        return await _service().discover_candidate_oauth(
            candidate_id,
            expected_source_digest=payload.expected_source_digest,
        )
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/hub/candidates/{candidate_id}/oauth/registrations",
    status_code=201,
)
async def register_hub_candidate_oauth_client(
    candidate_id: str, request: Request
) -> dict[str, Any]:
    payload = await _redacted_request_model(request, CandidateOAuthRegistrationRequest)
    assert isinstance(payload, CandidateOAuthRegistrationRequest)
    try:
        return await _service().register_candidate_oauth_client(
            candidate_id,
            expected_discovery_fingerprint=payload.expected_discovery_fingerprint,
            mode=payload.mode,
            client_id=payload.client_id,
        )
    except HubError as exc:
        _raise_http(exc)


@router.delete(
    "/api/mcp/hub/candidates/{candidate_id}/oauth/registrations/{registration_id}"
)
async def revoke_hub_candidate_oauth_client(
    candidate_id: str, registration_id: str, request: Request
) -> dict[str, Any]:
    await _require_empty_write_request(request)
    try:
        return await _service().revoke_candidate_oauth_client(
            candidate_id, registration_id
        )
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/hub/candidates/{candidate_id}/oauth/authorization-sessions",
    status_code=201,
)
async def create_hub_candidate_oauth_authorization(
    candidate_id: str, request: Request
) -> dict[str, Any]:
    payload = await _redacted_request_model(
        request, CandidateOAuthAuthorizationRequest
    )
    assert isinstance(payload, CandidateOAuthAuthorizationRequest)
    try:
        return await _service().create_candidate_oauth_authorization(
            candidate_id,
            expected_discovery_fingerprint=payload.expected_discovery_fingerprint,
            expected_registration_digest=payload.expected_registration_digest,
            expected_scope_digest=payload.expected_scope_digest,
            request_refresh_token=payload.request_refresh_token,
        )
    except HubError as exc:
        _raise_http(exc)


@router.delete(
    "/api/mcp/hub/candidates/{candidate_id}/oauth/authorization-sessions/{session_id}"
)
async def cancel_hub_candidate_oauth_authorization(
    candidate_id: str, session_id: str, request: Request
) -> dict[str, Any]:
    await _require_empty_write_request(request)
    try:
        return await _service().cancel_candidate_oauth_authorization(
            candidate_id, session_id
        )
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/hub/candidates/{candidate_id}/oauth/tokens/{token_id}/refresh"
)
async def refresh_hub_candidate_oauth_token(
    candidate_id: str, token_id: str, request: Request
) -> dict[str, Any]:
    payload = await _redacted_request_model(
        request, CandidateOAuthTokenRefreshRequest
    )
    assert isinstance(payload, CandidateOAuthTokenRefreshRequest)
    try:
        return await _service().refresh_candidate_oauth_token(
            candidate_id,
            token_id,
            expected_revision=payload.expected_revision,
        )
    except HubError as exc:
        _raise_http(exc)


@router.delete(
    "/api/mcp/hub/candidates/{candidate_id}/oauth/tokens/{token_id}"
)
async def revoke_hub_candidate_oauth_token(
    candidate_id: str, token_id: str, request: Request
) -> dict[str, Any]:
    await _require_empty_write_request(request)
    try:
        return await _service().revoke_candidate_oauth_token(candidate_id, token_id)
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/hub/candidates/{candidate_id}/auth-bindings",
    status_code=201,
)
async def create_hub_candidate_auth_binding(
    candidate_id: str, request: Request
) -> dict[str, Any]:
    payload = await _redacted_request_model(
        request, CandidateAuthBindingCreateRequest
    )
    assert isinstance(payload, CandidateAuthBindingCreateRequest)
    try:
        return _service().create_candidate_auth_binding(
            candidate_id,
            slot=payload.slot,
            display_name=payload.display_name,
            secret=payload.secret.get_secret_value(),
        )
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/hub/candidates/{candidate_id}/auth-bindings/{binding_id}/rotate"
)
async def rotate_hub_candidate_auth_binding(
    candidate_id: str,
    binding_id: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _redacted_request_model(
        request, CandidateAuthBindingRotateRequest
    )
    assert isinstance(payload, CandidateAuthBindingRotateRequest)
    try:
        return await _service().rotate_candidate_auth_binding(
            candidate_id,
            binding_id,
            secret=payload.secret.get_secret_value(),
            expected_revision=payload.expected_revision,
        )
    except HubError as exc:
        _raise_http(exc)


@router.delete(
    "/api/mcp/hub/candidates/{candidate_id}/auth-bindings/{binding_id}",
    status_code=204,
    response_class=Response,
)
async def revoke_hub_candidate_auth_binding(
    candidate_id: str, binding_id: str, request: Request
) -> Response:
    await _require_empty_write_request(request)
    try:
        await _service().revoke_candidate_auth_binding(candidate_id, binding_id)
        return Response(status_code=204)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/candidates/{candidate_id}/preflight")
async def preflight_hub_candidate(candidate_id: str) -> dict[str, Any]:
    try:
        return await _service().preflight(candidate_id)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/candidates/{candidate_id}/activate")
async def activate_hub_candidate(candidate_id: str, payload: CandidateActivateRequest) -> dict[str, Any]:
    try:
        return await _service().activate(candidate_id, payload.expected_schema_digest)
    except HubError as exc:
        _raise_http(exc)


@router.delete("/api/mcp/hub/candidates/{candidate_id}/session")
async def disconnect_hub_candidate(candidate_id: str) -> dict[str, Any]:
    try:
        return await _service().disconnect(candidate_id)
    except HubError as exc:
        _raise_http(exc)


@router.delete(
    "/api/mcp/hub/candidates/{candidate_id}",
    status_code=204,
    response_class=Response,
)
async def delete_hub_candidate(candidate_id: str) -> Response:
    try:
        await _service().delete_candidate(candidate_id)
        return Response(status_code=204)
    except HubError as exc:
        _raise_http(exc)
