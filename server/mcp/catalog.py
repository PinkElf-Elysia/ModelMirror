"""Curated MCP catalog adapter registry and catalog-scoped runtime API.

The frontend catalog is descriptive.  Executable commands, transports and
permission policy live here so clients cannot turn a planned catalog entry into
an arbitrary MCP connection.  Batch zero intentionally exposes only the seven
previously supported local stdio adapters; every other project remains a
non-executable roadmap entry.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import inspect
import json
import logging
import math
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ConfigDict, Field

from mcp.types import CallToolResult, Tool

try:
    from server.mcp.catalog_expansion_v2 import CATALOG_EXPANSION_V2_ADAPTERS
    from server.mcp.catalog_expansion_v3 import CATALOG_EXPANSION_V3_ADAPTERS
    from server.mcp.manager import (
        MCPClientManager,
        MCPClientError,
        MCPInstaller,
        MCPSessionNotFoundError,
    )
    from server.registry.tool_registry import ToolRegistry
    from server.mcp.workspace import (
        FILE_PROJECTS,
        MAX_FILE_BYTES,
        MAX_WORKSPACE_BYTES,
        MAX_WORKSPACE_FILES,
        PROJECT_EXTENSIONS,
        CatalogWorkspaceError,
        CatalogWorkspaceNotFoundError,
        CatalogWorkspacePolicyError,
        MCPCatalogWorkspaceStore,
    )
except ModuleNotFoundError:
    from mcp.catalog_expansion_v2 import CATALOG_EXPANSION_V2_ADAPTERS
    from mcp.catalog_expansion_v3 import CATALOG_EXPANSION_V3_ADAPTERS
    from mcp.manager import (
        MCPClientError,
        MCPClientManager,
        MCPInstaller,
        MCPSessionNotFoundError,
    )
    from registry.tool_registry import ToolRegistry
    from mcp.workspace import (
        FILE_PROJECTS,
        MAX_FILE_BYTES,
        MAX_WORKSPACE_BYTES,
        MAX_WORKSPACE_FILES,
        PROJECT_EXTENSIONS,
        CatalogWorkspaceError,
        CatalogWorkspaceNotFoundError,
        CatalogWorkspacePolicyError,
        MCPCatalogWorkspaceStore,
    )

try:
    from server.mcp.browser_proxy import (
        BROWSER_SCHEMA_SHA256,
        CONTRACT_VERSION as BROWSER_CONTRACT_VERSION,
        EXPECTED_LIMITS as BROWSER_LIMITS,
    )
except ModuleNotFoundError:
    from mcp.browser_proxy import (
        BROWSER_SCHEMA_SHA256,
        CONTRACT_VERSION as BROWSER_CONTRACT_VERSION,
        EXPECTED_LIMITS as BROWSER_LIMITS,
    )


AdapterAvailability = Literal["planned", "adapting", "ready", "blocked"]

BROWSER_LOGIN_TOKENS = frozenset(
    {
        "auth",
        "account",
        "accounts",
        "authorize",
        "callback",
        "consent",
        "login",
        "log-in",
        "oauth",
        "oauth2",
        "oidc",
        "saml",
        "session",
        "signin",
        "sign-in",
        "sso",
    }
)
BROWSER_LOGIN_COMPONENT_BOUNDARIES = re.compile(r"[/&=?#]+")
BROWSER_HOST_COMPONENT_BOUNDARIES = re.compile(r"[.]+")
BROWSER_QUERY_COMPONENT_BOUNDARIES = re.compile(r"[&;?#]+")
BROWSER_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "auth",
        "bearer",
        "code",
        "credential",
        "idtoken",
        "jwt",
        "key",
        "keypairid",
        "oauth",
        "oauthtoken",
        "password",
        "passwd",
        "refreshtoken",
        "secret",
        "session",
        "sessionid",
        "sig",
        "signature",
        "token",
    }
)
BROWSER_SENSITIVE_QUERY_PREFIXES = ("oauth", "xamz", "xgoog")


def canonical_browser_login_tokens(
    value: str, *, host: bool = False
) -> frozenset[str]:
    """Mirror the browser sidecar's ordinary and punctuation-folded tokens."""

    boundaries = (
        BROWSER_HOST_COMPONENT_BOUNDARIES
        if host
        else BROWSER_LOGIN_COMPONENT_BOUNDARIES
    )
    tokens: set[str] = set()
    ordinary_sequence: list[str] = []
    for component in boundaries.split(value.lower()):
        ordinary = re.findall(r"[a-z0-9]+", component)
        tokens.update(ordinary)
        ordinary_sequence.extend(ordinary)
        compact = "".join(ordinary)
        if compact:
            tokens.add(compact)
    tokens.update(
        left + right
        for left, right in zip(ordinary_sequence, ordinary_sequence[1:])
    )
    return frozenset(tokens)


def browser_query_has_sensitive_key(query: str) -> bool:
    """Mirror the sidecar's bearer/signing query-key rejection."""

    decoded = query
    for _ in range(2):
        decoded = unquote(decoded)
    for component in BROWSER_QUERY_COMPONENT_BOUNDARIES.split(decoded):
        raw_key = component.partition("=")[0]
        key = "".join(re.findall(r"[a-z0-9]+", raw_key.lower()))
        if not key:
            continue
        if (
            key in BROWSER_SENSITIVE_QUERY_KEYS
            or key.startswith(BROWSER_SENSITIVE_QUERY_PREFIXES)
            or key.endswith(("secret", "signature", "token"))
        ):
            return True
    return False


AdapterConnectionKind = Literal[
    "local-stdio",
    "sandboxed-stdio",
    "remote-mcp",
    "desktop-bridge",
]
AdapterRisk = Literal["low", "medium", "high", "critical"]
AdapterPreparationKind = Literal["installer", "bundled"]
CatalogToolEffect = Literal["read", "artifact-create", "state-write", "terminal"]
CatalogSettingKind = Literal["text", "integer", "enum", "slug", "hostname"]
CatalogDatabaseMode = Literal["remote-read-only", "local-file-read-only"]
CatalogApprovalContextKind = Literal[
    "workspace",
    "remote-resource",
    "browser-session",
]

logger = logging.getLogger("modelmirror.mcp.catalog")


class CatalogAdapterError(RuntimeError):
    """Base catalog adapter failure."""


class CatalogAdapterNotFoundError(CatalogAdapterError):
    """Raised when a project is not part of the frozen catalog."""


class CatalogAdapterUnavailableError(CatalogAdapterError):
    """Raised when a project has not crossed its production readiness gate."""


class CatalogAdapterPolicyError(CatalogAdapterError):
    """Raised when configuration or execution violates an adapter policy."""


class CatalogApprovalRequiredError(CatalogAdapterPolicyError):
    """Raised before a state-changing operation so the UI can ask once."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("该操作需要一次性确认。")
        self.payload = payload


class CatalogUnknownOutcomeError(CatalogAdapterPolicyError):
    """Raised when a state-changing remote request may have been accepted."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__("远程写入结果未知；为避免重复修改，系统不会自动重试。")
        self.idempotency_key = idempotency_key


class CatalogProviderRejectedError(CatalogAdapterPolicyError):
    """Raised when the provider proves that a remote write did not complete."""

    def __init__(self, reason: str, idempotency_key: str) -> None:
        normalized_reason = (
            "rate_limited" if reason == "rate_limited" else "provider_rejected"
        )
        message = (
            "上游服务已限流，本次写入未执行且不会自动重试。"
            if normalized_reason == "rate_limited"
            else "上游服务已明确拒绝本次写入，系统不会自动重试。"
        )
        super().__init__(message)
        self.reason = normalized_reason
        self.idempotency_key = idempotency_key


class CatalogBrowserPolicyRejectedError(CatalogAdapterPolicyError):
    """Raised when the browser proves that no reviewed action was dispatched."""

    def __init__(self, reason: str, idempotency_key: str) -> None:
        normalized_reason = (
            reason
            if reason
            in {
                "dns_policy_rejected",
                "target_policy_rejected",
            }
            else "browser_policy_rejected"
        )
        messages = {
            "dns_policy_rejected": (
                "目标域名未通过浏览器公网 DNS 安全校验，本次操作未执行；会话仍可继续。"
            ),
            "target_policy_rejected": (
                "目标地址未通过浏览器安全策略，本次操作未执行；会话仍可继续。"
            ),
            "browser_policy_rejected": (
                "浏览器安全策略已明确拒绝本次操作；会话仍可继续。"
            ),
        }
        super().__init__(messages[normalized_reason])
        self.reason = normalized_reason
        self.idempotency_key = idempotency_key


def _browser_policy_rejection_reason(exc: BaseException) -> str | None:
    """Classify only reviewed, non-retryable browser JSON-RPC rejections."""

    if not isinstance(exc, McpError):
        return None
    rpc_error = exc.error
    rpc_data = rpc_error.data if isinstance(rpc_error.data, dict) else {}
    if rpc_error.code != -32011 or rpc_data.get("retryable") is not False:
        return None
    raw_reason = str(rpc_data.get("reason") or "")
    if raw_reason.startswith("browser_dns_") or raw_reason == "browser_private_dns_denied":
        return "dns_policy_rejected"
    if raw_reason in {
        "browser_external_login_denied",
        "browser_sensitive_query_denied",
        "browser_url_scheme_denied",
        "browser_url_host_denied",
        "browser_url_port_denied",
        "browser_url_credentials_denied",
        "browser_url_fragment_denied",
        "browser_literal_address_denied",
    }:
        return "target_policy_rejected"
    # Other -32011 reasons can be emitted after an upstream action (for
    # example a cross-origin redirect observed after navigation). They remain
    # fail-closed and ambiguous rather than being mislabeled pre-dispatch.
    return None


class CatalogBrowserStateDriftError(CatalogAdapterPolicyError):
    """Raised before dispatch when a frozen browser page is no longer current."""


class CatalogBrowserSessionExpiredError(CatalogAdapterPolicyError):
    """Raised when the ephemeral browser TTL has elapsed."""


@dataclass(frozen=True, slots=True)
class CatalogToolPolicy:
    read_only: bool = True
    requires_approval: bool = False
    sensitive: bool = False
    terminal: bool = False
    effect: CatalogToolEffect = "read"


@dataclass(frozen=True, slots=True)
class CatalogWorkspacePolicy:
    required: bool = True
    persistent: bool = False
    max_file_bytes: int = MAX_FILE_BYTES
    max_workspace_bytes: int = MAX_WORKSPACE_BYTES
    max_files: int = MAX_WORKSPACE_FILES
    idle_ttl_seconds: int | None = 24 * 60 * 60
    artifact_ttl_seconds: int = 7 * 24 * 60 * 60
    accepted_extensions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogSettingPolicy:
    key: str
    label: str
    description: str
    kind: CatalogSettingKind = "text"
    required: bool = False
    default: str | int | None = None
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[tuple[str, str], ...] = ()
    pattern: str = ""
    allowed_hostname_suffixes: tuple[str, ...] = ()

    def to_public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "options": [
                {"value": value, "label": label}
                for value, label in self.options
            ],
            "allowed_hostname_suffixes": list(self.allowed_hostname_suffixes),
        }


@dataclass(frozen=True, slots=True)
class CatalogCredentialSlotPolicy:
    key: str
    label: str
    description: str
    required: bool = True
    accepted_kinds: tuple[str, ...] = ("provider_key",)

    def to_public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "required": self.required,
            "accepted_kinds": list(self.accepted_kinds),
        }


@dataclass(frozen=True, slots=True)
class CatalogDatabasePolicy:
    """Public, non-secret guardrails for a fixed database adapter."""

    mode: CatalogDatabaseMode
    engine: str
    read_only: bool = True
    tls_required: bool = True
    max_rows_default: int = 200
    max_rows_hard: int = 1_000
    statement_timeout_seconds: int = 15
    operation_timeout_seconds: int = 20
    preflight_checks: tuple[str, ...] = (
        "dns-policy",
        "tls-verification",
        "authentication",
        "native-read-only-mode",
        "query-limits",
    )

    def to_public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "engine": self.engine,
            "read_only": self.read_only,
            "tls_required": self.tls_required,
            "max_rows_default": self.max_rows_default,
            "max_rows_hard": self.max_rows_hard,
            "statement_timeout_seconds": self.statement_timeout_seconds,
            "operation_timeout_seconds": self.operation_timeout_seconds,
            "preflight_checks": list(self.preflight_checks),
        }


@dataclass(frozen=True, slots=True)
class CatalogPublicPolicy:
    """Public guardrails for a credential-free fixed-host adapter facade."""

    provider: str
    upstream_repository: str
    upstream_version: str
    upstream_commit: str
    upstream_license: str
    fixed_hosts: tuple[str, ...]
    tool_schema_sha256: str
    anonymous_only: bool = True
    read_only: bool = True
    read_retry_limit: int = 1
    rate_limit_per_minute: int = 30
    max_results: int = 25

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CatalogSaaSPolicy:
    """Public guardrails for a fixed, stateful SaaS account adapter."""

    provider: str
    fixed_hosts: tuple[str, ...]
    tool_schema_sha256: str
    preflight_checks: tuple[str, ...] = (
        "authentication",
        "account-identity",
        "resource-scope",
        "tool-schema",
    )
    rate_limit_per_minute: int = 30
    max_concurrent_calls: int = 1
    read_retry_limit: int = 2
    write_retry_mode: str = "idempotency-key-only"
    account_unbind_supported: bool = True

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CatalogBrowserPolicy:
    """Public, non-secret guardrails for an ephemeral browser adapter."""

    engine: str
    contract_version: str
    tool_schema_sha256: str
    session_ttl_seconds: int = 15 * 60
    idle_ttl_seconds: int = 5 * 60
    max_pages: int = 1
    max_actions: int = 50
    max_concurrent_sessions: int = 1
    max_tunnels_per_session: int = 12
    max_egress_bytes_per_session: int = 64 * 1024 * 1024
    egress_tunnel_idle_seconds: int = 30
    egress_tunnel_ttl_seconds: int = 120
    navigation_timeout_seconds: int = 20
    call_timeout_seconds: int = 30
    max_output_bytes: int = 256 * 1024
    max_artifact_bytes: int = 32 * 1024 * 1024
    max_artifacts_per_project: int = 50
    max_artifact_storage_bytes: int = 256 * 1024 * 1024
    artifact_ttl_seconds: int = 24 * 60 * 60
    allowed_schemes: tuple[str, ...] = ("http", "https")
    allowed_ports: tuple[int, ...] = (80, 443)
    uploads: bool = False
    downloads: bool = False
    clipboard: bool = False
    local_files: bool = False
    cookies: bool = False
    storage: bool = False
    login_state: bool = False
    evaluate: bool = False
    cdp: bool = False
    limitations: tuple[str, ...] = (
        "仅允许经一次性确认的公网 HTTP/HTTPS 导航；每次 DNS 解析和重定向都重新校验。",
        "浏览器会话临时化且仅允许单页；不保留 Cookie、存储、登录态或本机文件。",
        "不采集账号凭据或提供外站登录流程；页面仍可能呈现登录界面，用户不得输入账号、密码或 OTP。",
        "上传、下载、剪贴板、任意脚本求值和外部 CDP 均关闭。",
    )

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CatalogAdapterManifest:
    project_id: str
    wave: int
    availability: AdapterAvailability
    connection_kind: AdapterConnectionKind
    risk: AdapterRisk
    required_capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    adapter_version: str = ""
    runtime_image: str = ""
    network_policy: str = "unspecified"
    filesystem_policy: str = "unspecified"
    resource_limits: tuple[tuple[str, str], ...] = ()
    server_command: tuple[str, ...] = ()
    install_command: str = ""
    preparation_kind: AdapterPreparationKind = "installer"
    transport: str = "stdio"
    endpoint: str = ""
    allowed_settings: tuple[str, ...] = ()
    credential_slots: tuple[str, ...] = ()
    setting_policies: tuple[CatalogSettingPolicy, ...] = ()
    credential_policies: tuple[CatalogCredentialSlotPolicy, ...] = ()
    tool_policies: dict[str, CatalogToolPolicy] = field(default_factory=dict)
    workspace_policy: CatalogWorkspacePolicy | None = None
    database_policy: CatalogDatabasePolicy | None = None
    public_policy: CatalogPublicPolicy | None = None
    saas_policy: CatalogSaaSPolicy | None = None
    browser_policy: CatalogBrowserPolicy | None = None
    legacy_unrestricted_calls: bool = False
    enabled_by_default: bool = False
    operation_timeout: float = 30.0
    max_output_bytes: int = 256 * 1024

    @property
    def feature_flag(self) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]", "_", self.project_id).upper()
        return f"MCP_CATALOG_ENABLE_{normalized}"

    @property
    def feature_enabled(self) -> bool:
        raw = os.getenv(self.feature_flag, "").strip().lower()
        if raw:
            return raw in {"1", "true", "yes", "on"}
        return self.enabled_by_default

    @property
    def executable(self) -> bool:
        return (
            self.availability == "ready"
            and self.feature_enabled
            and (
                self.saas_policy is None
                or self.stateful_saas_gate_enabled
            )
            and bool(self.server_command or self.endpoint)
        )

    @property
    def stateful_saas_gate_enabled(self) -> bool:
        if self.saas_policy is None:
            return True
        return os.getenv(
            "MCP_CATALOG_STATEFUL_SAAS_SINGLE_USER_ACK",
            "",
        ).strip().lower() in {"1", "true", "yes", "on"}

    def to_public(
        self,
        *,
        connected: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "wave": self.wave,
            "availability": self.availability,
            "connection_kind": self.connection_kind,
            "risk": self.risk,
            "required_capabilities": list(self.required_capabilities),
            "limitations": list(self.limitations),
            "feature_enabled": self.feature_enabled,
            "executable": self.executable,
            "connected": connected,
            "session_id": session_id if connected else None,
            "allowed_settings": list(self.allowed_settings),
            "credential_slots": list(self.credential_slots),
            "setting_fields": [item.to_public() for item in self.setting_policies],
            "credential_fields": [item.to_public() for item in self.credential_policies],
            "adapter_version": self.adapter_version,
            "runtime_image": self.runtime_image,
            "network_policy": self.network_policy,
            "filesystem_policy": self.filesystem_policy,
            "resource_limits": dict(self.resource_limits),
            "tool_policies": {
                name: {
                    "read_only": policy.read_only,
                    "requires_approval": policy.requires_approval,
                    "sensitive": policy.sensitive,
                    "terminal": policy.terminal,
                    "effect": policy.effect,
                }
                for name, policy in sorted(self.tool_policies.items())
            },
            "workspace_policy": (
                {
                    "required": self.workspace_policy.required,
                    "persistent": self.workspace_policy.persistent,
                    "max_file_bytes": self.workspace_policy.max_file_bytes,
                    "max_workspace_bytes": self.workspace_policy.max_workspace_bytes,
                    "max_files": self.workspace_policy.max_files,
                    "idle_ttl_seconds": self.workspace_policy.idle_ttl_seconds,
                    "artifact_ttl_seconds": self.workspace_policy.artifact_ttl_seconds,
                    "accepted_extensions": list(self.workspace_policy.accepted_extensions),
                }
                if self.workspace_policy is not None
                else None
            ),
            "database_policy": (
                self.database_policy.to_public()
                if self.database_policy is not None
                else None
            ),
            "public_policy": (
                self.public_policy.to_public()
                if self.public_policy is not None
                else None
            ),
            "saas_policy": (
                self.saas_policy.to_public()
                if self.saas_policy is not None
                else None
            ),
            "browser_policy": (
                self.browser_policy.to_public()
                if self.browser_policy is not None
                else None
            ),
            "stateful_saas_gate_enabled": self.stateful_saas_gate_enabled,
        }


LOCAL_STDIO_ADAPTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "context7": ("npx ctx7 setup", ("npx", "-y", "@upstash/context7-mcp")),
    "filesystem-mcp": (
        "npx -y @modelcontextprotocol/server-filesystem <allowed-directory>",
        ("npx", "-y", "@modelcontextprotocol/server-filesystem", "."),
    ),
    "youtube-transcript-mcp": (
        "npx -y @kimtaeyoon83/mcp-server-youtube-transcript",
        ("npx", "-y", "@kimtaeyoon83/mcp-server-youtube-transcript"),
    ),
    "memory-mcp": (
        "npx -y @modelcontextprotocol/server-memory",
        ("npx", "-y", "@modelcontextprotocol/server-memory"),
    ),
    "12306-mcp": ("npx -y 12306-mcp", ("npx", "-y", "12306-mcp")),
    "sequential-thinking-mcp": (
        "npx -y @modelcontextprotocol/server-sequential-thinking",
        ("npx", "-y", "@modelcontextprotocol/server-sequential-thinking"),
    ),
    "everything-mcp": (
        "npx -y @modelcontextprotocol/server-everything",
        ("npx", "-y", "@modelcontextprotocol/server-everything"),
    ),
}


SANDBOX_PROXY = (
    sys.executable,
    str(Path(__file__).resolve().with_name("sandbox_proxy.py")),
)
PUBLIC_SANDBOX_PROXY = (
    sys.executable,
    str(Path(__file__).resolve().with_name("public_proxy.py")),
)
FILE_SANDBOX_PROXY = (
    sys.executable,
    str(Path(__file__).resolve().with_name("file_proxy.py")),
)
TOKEN_SANDBOX_PROXY = (
    sys.executable,
    str(Path(__file__).resolve().with_name("token_proxy.py")),
)
DATABASE_SANDBOX_PROXY = (
    sys.executable,
    str(Path(__file__).resolve().with_name("database_proxy.py")),
)
SAAS_SANDBOX_PROXY = (
    sys.executable,
    "-m",
    "mcp.saas_proxy",
)
BROWSER_SANDBOX_PROXY = (
    sys.executable,
    str(Path(__file__).resolve().with_name("browser_proxy.py")),
)
WAVE_ONE_ADAPTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "calculator-mcp": (
        "0.2.1-compatible-python-v1",
        ("add", "sub", "mul", "div", "mod", "sqrt"),
    ),
    "time-mcp": (
        "0.6.2-compatible-python-v1",
        ("get_current_time", "convert_time"),
    ),
    "vegalite-mcp": (
        "0.0.1-compatible-python-v1",
        ("save_data", "visualize_data"),
    ),
}

WAVE_TWO_ADAPTERS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "fetch-mcp": (
        "0.6.3-secure-compatible-v1",
        ("fetch",),
        "validated-public-https:user-supplied-host",
    ),
    "quickchart-mcp": (
        "1.0.6-secure-compatible-v1",
        ("generate_chart",),
        "allowlist:quickchart.io",
    ),
    "geowire-mcp": (
        "0.6.2-secure-compatible-v1",
        (
            "search_places",
            "geocode_address",
            "reverse_geocode",
            "get_directions",
            "distance_matrix",
            "list_geo_providers",
        ),
        "allowlist:nominatim.openstreetmap.org,router.project-osrm.org",
    ),
}

WAVE_SIXTEEN_PUBLIC_ADAPTERS: dict[
    str,
    tuple[
        str,
        tuple[str, ...],
        CatalogPublicPolicy,
        tuple[str, ...],
    ],
] = {
    "nickclyde-duckduckgo-mcp-server": (
        "0.6.1-compatible-native-v1",
        ("search",),
        CatalogPublicPolicy(
            provider="DuckDuckGo",
            upstream_repository="nickclyde/duckduckgo-mcp-server",
            upstream_version="v0.6.1",
            upstream_commit="ad2e681bfb4461c969d3032b47ac5b3cd513f0a9",
            upstream_license="MIT",
            fixed_hosts=("html.duckduckgo.com",),
            tool_schema_sha256=(
                "9a10fcfb68759337ab6af5fcfe76f5a7ebc87f3724e34a2017ea25807e4cc197"
            ),
            rate_limit_per_minute=30,
            max_results=20,
        ),
        (
            "仅开放 DuckDuckGo 搜索结果元数据；网页抓取、任意 URL、关闭安全搜索和自定义 Header 均不可发现。",
            "固定 Strict SafeSearch、最多 20 条结果、2 秒请求间隔；标题、摘要与链接均按不可信公网内容返回。",
        ),
    ),
    "jpisnice-shadcn-ui-mcp-server": (
        "2.0.0-compatible-native-v1",
        ("list_components", "get_component_metadata"),
        CatalogPublicPolicy(
            provider="shadcn/ui",
            upstream_repository="Jpisnice/shadcn-ui-mcp-server",
            upstream_version="v2.0.0",
            upstream_commit="d750f1645bb0fe10c6fbf5e246bc3b12d3807c05",
            upstream_license="MIT",
            fixed_hosts=("api.github.com",),
            tool_schema_sha256=(
                "8a04ba4e5da26f151bc0a563e63d9567e2932e0450d08565bc64f2498e39336f"
            ),
            rate_limit_per_minute=1,
            max_results=100,
        ),
        (
            "仅列出固定 shadcn-ui/ui 提交中的 v4 组件并读取单组件 Git 元数据；源码、Demo、Block、主题和目录遍历均关闭。",
            "不接受 GitHub Token、仓库、分支、路径或写入目标；上游 apply_theme 及任何本地项目修改工具不可发现。",
        ),
    ),
    "docker-hub-mcp": (
        "0.18.0-compatible-native-v1",
        ("search", "getRepositoryInfo", "listRepositoryTags"),
        CatalogPublicPolicy(
            provider="Docker Hub",
            upstream_repository="docker/hub-mcp",
            upstream_version="dockerhub-mcp/v0.18.0",
            upstream_commit="98cf1b9cbec64316ea2b465462468a2d2204a406",
            upstream_license="Apache-2.0",
            fixed_hosts=("hub.docker.com",),
            tool_schema_sha256=(
                "e8ce120ed943ee25aaa0d67218e4ce8e408dc42592251e9eec108daa1065d35d"
            ),
            rate_limit_per_minute=60,
            max_results=25,
        ),
        (
            "仅开放匿名仓库搜索、仓库元数据与标签元数据；账号、命名空间、Docker Hardened Images 组织数据及认证均关闭。",
            "创建/更新仓库、状态变更、PAT、任意 Registry/Host/Header 和镜像执行或拉取工具均不可发现。",
        ),
    ),
    "genomoncology-biomcp": (
        "0.8.25-compatible-native-v1",
        ("search", "get"),
        CatalogPublicPolicy(
            provider="BioMCP public data sources",
            upstream_repository="genomoncology/biomcp",
            upstream_version="v0.8.25",
            upstream_commit="b5337826dbf06db6d6409f36ead7a4d6a70c710e",
            upstream_license="MIT",
            fixed_hosts=(
                "www.ebi.ac.uk",
                "clinicaltrials.gov",
                "myvariant.info",
            ),
            tool_schema_sha256=(
                "24c2ca66ce7643bdb91323912a73956c1adbd93c82c246c55fe773afa95f1c31"
            ),
            rate_limit_per_minute=30,
            max_results=10,
        ),
        (
            "仅开放匿名公共文章、临床试验和变异元数据的 search/get；原始 biomcp 查询、研究文件、诊断上传和任意 URL 均不可发现。",
            "出口固定为 Europe PMC、ClinicalTrials.gov 与 MyVariant.info；结果仅供研究检索，不构成医疗建议。",
        ),
    ),
    "safedep-vet": (
        "1.18.1-compatible-native-v1",
        (
            "get_package_version_vulnerabilities",
            "get_package_version_popularity",
            "get_package_version_license_info",
            "get_package_version_malware_report",
            "get_package_latest_version",
            "get_package_available_versions",
        ),
        CatalogPublicPolicy(
            provider="SafeDep community insights and public registries",
            upstream_repository="safedep/vet",
            upstream_version="v1.18.1",
            upstream_commit="67abab1b0ec915713edb50e5e5b36687fd4cd86a",
            upstream_license="Apache-2.0",
            fixed_hosts=(
                "community-api.safedep.io",
                "registry.npmjs.org",
                "pypi.org",
            ),
            tool_schema_sha256=(
                "52be50ad2e6b7c53e2b6e76799a9083f3892ae49e2b0f2bfccee4ca8262be652"
            ),
            rate_limit_per_minute=30,
            max_results=100,
        ),
        (
            "仅接受规范化 npm/PyPI PURL，并开放已存在的漏洞、流行度、许可证、恶意软件报告及公共版本元数据；不下载、不解包、不执行包。",
            "扫描、上传、SQL、认证租户、任意 Registry/Endpoint/Header 与诊断回传均不可发现。",
        ),
    ),
    "aas-ee-open-websearch": (
        "2.1.9-compatible-native-v1",
        ("search",),
        CatalogPublicPolicy(
            provider="open-webSearch fixed request engines",
            upstream_repository="Aas-ee/open-webSearch",
            upstream_version="v2.1.9",
            upstream_commit="84695b392ca03ffc68fbd406f1d7937b7151e4b6",
            upstream_license="Apache-2.0",
            fixed_hosts=("cn.bing.com", "html.duckduckgo.com"),
            tool_schema_sha256=(
                "cf695f0f1d6a9fb3fe08ae454f3729367f28103bc85d1c893737f42ad706fe99"
            ),
            rate_limit_per_minute=30,
            max_results=10,
        ),
        (
            "仅开放 Bing RSS 与 DuckDuckGo Strict SafeSearch 的固定 request-only 搜索；结果标题、摘要和链接均按不可信公网内容返回。",
            "网页抓取、Playwright、代理、任意 URL/Header/env、动态搜索引擎和关闭 TLS 校验均不可发现。",
        ),
    ),
    "mnemox-ai-idea-reality-mcp": (
        "0.5.0-compatible-native-v1",
        ("idea_check",),
        CatalogPublicPolicy(
            provider="Idea Reality public research indexes",
            upstream_repository="mnemox-ai/idea-reality-mcp",
            upstream_version="v0.5.0",
            upstream_commit="755e1859c1f7d1d017c67f615c67ec595c8edb66",
            upstream_license="MIT",
            fixed_hosts=(
                "api.github.com",
                "hn.algolia.com",
                "registry.npmjs.org",
                "pypi.org",
            ),
            tool_schema_sha256=(
                "65b4b069bcb5faa961341576f452e72faa49b4deae214a6f840da2521a010c24"
            ),
            rate_limit_per_minute=30,
            max_results=20,
        ),
        (
            "仅以确定性关键词查询 GitHub、Hacker News、npm 与 PyPI 公共索引；相似性指标只用于研究发现，不构成投资或产品建议。",
            "Product Hunt Token、LLM 调用、账号数据、诊断上传、动态 endpoint 和任意 Header 均关闭。",
        ),
    ),
    "idosal-git-mcp": (
        "c487a298-compatible-native-v1",
        (
            "fetch_repository_documentation",
            "search_repository_documentation",
            "search_repository_code",
        ),
        CatalogPublicPolicy(
            provider="GitHub public repository metadata",
            upstream_repository="idosal/git-mcp",
            upstream_version="reviewed-commit-c487a298",
            upstream_commit="c487a29895dcfcb5b672247e646426a56e2051c1",
            upstream_license="Apache-2.0",
            fixed_hosts=("api.github.com",),
            tool_schema_sha256=(
                "56a8c84a969a4beaca16bf905be83899bb497d19a4e95cef5135ad4465ef4811"
            ),
            rate_limit_per_minute=60,
            max_results=20,
        ),
        (
            "仅接受规范 GitHub owner/repository slug，读取有限 README、文档路径与仓库路径索引；代码搜索不克隆仓库。",
            "动态 GitMCP endpoint、通用 URL 抓取、Token、clone、代码执行、私有仓库和仓库写入均不可发现。",
        ),
    ),
    "coinpaprika-dexpaprika-mcp": (
        "2.3.2-compatible-native-v1",
        ("getNetworks", "getStats", "search"),
        CatalogPublicPolicy(
            provider="DexPaprika",
            upstream_repository="coinpaprika/dexpaprika-mcp",
            upstream_version="v2.3.2",
            upstream_commit="02bfbcc8e0468d3a82d9e060e5da398a0d22f23c",
            upstream_license="MIT",
            fixed_hosts=("api.dexpaprika.com",),
            tool_schema_sha256=(
                "b6b6a6ef17aed4544341be76648401fd4ac6a62f4d657d9f5da0f2429429ebc9"
            ),
            rate_limit_per_minute=120,
            max_results=10,
        ),
        (
            "仅开放公共 network、aggregate stats 与有限 token/pool 搜索；名称、符号和市场字段均按不可信公网元数据返回，不构成金融建议。",
            "反馈、钱包、交易、任意 URL/Header/env、认证和动态 endpoint 均不可发现。",
        ),
    ),
    "pab1it0-chess-mcp": (
        "0.1.0-compatible-native-v1",
        ("get_player_profile", "get_player_stats"),
        CatalogPublicPolicy(
            provider="Chess.com Public Data API",
            upstream_repository="pab1it0/chess-mcp",
            upstream_version="v0.1.0",
            upstream_commit="3f4068ed6befe0be34c4cef3e7e5e9234ebc3a3d",
            upstream_license="MIT",
            fixed_hosts=("api.chess.com",),
            tool_schema_sha256=(
                "d33380c3a2cd3e271e289c9a021c1c8d67403bb2f74a4c5df6e075b67882cf7d"
            ),
            rate_limit_per_minute=120,
            max_results=5,
        ),
        (
            "仅按规范化用户名读取公开玩家 profile 与有限 rating/result 汇总；头像、外部 URL、对局下载和大批量历史均不返回。",
            "上游声明的 is_player_online 当前公共 API 路径返回 404，已从冻结工具面移除而不伪造结果；账号操作和 PGN 下载不可发现。",
        ),
    ),
    "yuna0x0-anilist-mcp": (
        "1.4.0-compatible-native-v1",
        ("get_genres", "search_anime", "get_anime"),
        CatalogPublicPolicy(
            provider="AniList public GraphQL API",
            upstream_repository="yuna0x0/anilist-mcp",
            upstream_version="v1.4.0",
            upstream_commit="7c5cf1e374c09e3ddbd9c68f92c4c08a43e65477",
            upstream_license="MIT",
            fixed_hosts=("graphql.anilist.co",),
            tool_schema_sha256=(
                "060e2a7e6eb92fd44a945b99ca91adb614eb877e286535516b9ec8c0a7b7e239"
            ),
            rate_limit_per_minute=80,
            max_results=10,
        ),
        (
            "仅发送仓库内冻结的匿名 GraphQL 查询，开放 genre、有限 anime 搜索和最多五个 ID 的详情读取；页面与结果数量固定受限。",
            "客户端 GraphQL、OAuth、账号、收藏/列表写入、成人内容扩展、任意 URL/Header/env 和 mutation 均不可发现。",
        ),
    ),
    "rishijatia-fantasy-pl-mcp": (
        "0.1.7-compatible-native-v1",
        (
            "search_fpl_players",
            "get_player_information",
            "list_fpl_fixtures",
        ),
        CatalogPublicPolicy(
            provider="Fantasy Premier League public API",
            upstream_repository="rishijatia/fantasy-pl-mcp",
            upstream_version="v0.1.7",
            upstream_commit="fdaef005143347455fc500cb1f934d451f95251a",
            upstream_license="MIT",
            fixed_hosts=("fantasy.premierleague.com",),
            tool_schema_sha256=(
                "b9760cc0e80c3c906a96e9090e90c57e31b4443e2f58a622c6769ee8448fe602"
            ),
            rate_limit_per_minute=60,
            max_results=50,
        ),
        (
            "仅投影官方公开球员与赛程字段；球员和赛程查询均有固定数量上限，不构成阵容、转会或其他现实决策建议。",
            "登录、经理队伍、联赛、阵容、转会、建议、任意 URL/Header/env 和动态 endpoint 均不可发现。",
        ),
    ),
}

WAVE_EIGHTEEN_FILE_ADAPTERS: dict[
    str,
    tuple[str, dict[str, CatalogToolPolicy], tuple[str, ...]],
] = {
    "zcaceres-markdownify-mcp": (
        "1.1.0-compatible-native-v1",
        {
            name: CatalogToolPolicy(read_only=False, effect="artifact-create")
            for name in (
                "pdf-to-markdown",
                "docx-to-markdown",
                "xlsx-to-markdown",
                "pptx-to-markdown",
            )
        },
        (
            "仅接受当前受控工作区中的 PDF、DOCX、XLSX 或 PPTX 文件标识；输入始终只读且完全断网。",
            "只生成服务端登记的 Markdown 产物；网页、图片、音频、Git、URI、宿主路径和插件均不开放。",
        ),
    ),
    "vivekvells-mcp-pandoc": (
        "0.11.0-compatible-native-v1",
        {
            "convert-contents": CatalogToolPolicy(
                read_only=False,
                effect="artifact-create",
            ),
        },
        (
            "仅接受封存工作区中的 Markdown、HTML 或纯文本，并使用固定 Pandoc 3.10.1 生成 Markdown、HTML 或 DOCX 副本。",
            "filter、defaults、template、reference、PDF、任意参数和宿主路径均不开放；输入始终只读且完全断网。",
        ),
    ),
    "antvis-mcp-server-chart": (
        "0.9.10-compatible-native-v1",
        {
            name: CatalogToolPolicy(read_only=False, effect="artifact-create")
            for name in (
                "generate_line_chart",
                "generate_bar_chart",
                "generate_pie_chart",
            )
        },
        (
            "仅接受有界结构化数据并生成确定性 PNG 产物；不读取工作区文件，也不连接任何网络。",
            "官方远程 antv-studio 服务、地图、动态图表、任意端点、远程 URL、脚本与自定义渲染器均不开放。",
        ),
    ),
    "cyberchitta-llm-context-py": (
        "0.6.4-reviewed-commit-6de16c22-compatible-native-v1",
        {
            "lc_preview": CatalogToolPolicy(read_only=True, effect="read"),
            "lc_outlines": CatalogToolPolicy(read_only=False, effect="artifact-create"),
        },
        (
            "仅遍历当前封存工作区中的有界 UTF-8 代码/文本文件；root_path、动态规则、缺失文件读取和剪贴板均不开放。",
            "预览保持只读，outline 只生成服务端登记的 Markdown 产物；输入始终只读且完全断网。",
        ),
    ),
    "haris-musa-excel-mcp-server": (
        "0.1.8-compatible-native-v1",
        {
            "get_workbook_metadata": CatalogToolPolicy(read_only=True, effect="read"),
            "read_data_from_excel": CatalogToolPolicy(read_only=True, effect="read"),
            "write_data_to_excel": CatalogToolPolicy(read_only=False, effect="artifact-create"),
        },
        (
            "仅接受当前封存工作区中的 XLSX；宏、外部关系、活动内容和输出副本中的公式均 fail closed。",
            "写入只创建服务端登记的新副本且绝不覆盖输入；filepath、URI、图表、透视表、格式和原地修改均不开放。",
        ),
    ),
    "dataeval-dingo": (
        "2.5.0-rule-compatible-native-v1",
        {
            "list_dingo_components": CatalogToolPolicy(read_only=True, effect="read"),
            "run_dingo_evaluation": CatalogToolPolicy(read_only=False, effect="artifact-create"),
        },
        (
            "仅接受封存工作区中的 JSONL、JSON、CSV 或 TXT，并运行三个固定本地规则。",
            "LLM、Agent、Prompt、Hugging Face、S3、SQL、API Key、自定义规则和动态 kwargs 均不开放。",
        ),
    ),
}


WAVE_TWENTY_FILE_ADAPTERS: dict[
    str,
    tuple[str, dict[str, CatalogToolPolicy], tuple[str, ...]],
] = {
    "ozgurcd-gograph": (
        "1.5.6-reviewed-commit-aa4d6d54-compatible-native-v1",
        {
            "index_repository": CatalogToolPolicy(
                read_only=False,
                requires_approval=True,
                effect="state-write",
            ),
            **{
                name: CatalogToolPolicy(read_only=True, effect="read")
                for name in (
                    "search_symbols",
                    "get_symbol_context",
                    "get_source",
                    "get_callers",
                    "get_repository_summary",
                )
            },
        },
        (
            "仅索引当前封存 Go 工作区并在单次上游进程内保存临时图；输入只读且完全断网。",
            "任意路径、Git baseline、持久化刷新、边界配置、会话遥测、Wiki、doc、工具链下载和其余上游工具均不开放。",
        ),
    ),
}


WAVE_THREE_ADAPTERS: dict[str, tuple[str, dict[str, CatalogToolPolicy]]] = {
    "basic-memory-mcp": (
        "0.22.1-local-contract-v1",
        {
            **{
                name: CatalogToolPolicy(read_only=True, effect="read")
                for name in (
                    "read_note", "read_content", "view_note", "search_notes",
                    "search", "fetch", "recent_activity", "list_directory",
                    "build_context", "basic_memory_diagnostics",
                )
            },
            **{
                name: CatalogToolPolicy(
                    read_only=False,
                    requires_approval=True,
                    effect="state-write",
                )
                for name in ("write_note", "edit_note", "move_note")
            },
        },
    ),
    "excel-mcp-server": (
        "1.0.4-secure-contract-v1",
        {
            **{
                name: CatalogToolPolicy(read_only=True, effect="read")
                for name in (
                    "read_excel", "get_excel_info", "get_sheet_names",
                    "analyze_excel", "filter_excel", "pivot_table", "data_summary",
                )
            },
            "export_chart": CatalogToolPolicy(
                read_only=False,
                effect="artifact-create",
            ),
            "write_excel": CatalogToolPolicy(
                read_only=False,
                requires_approval=True,
                effect="state-write",
            ),
            "update_excel": CatalogToolPolicy(
                read_only=False,
                requires_approval=True,
                effect="state-write",
            ),
        },
    ),
    "git-mcp": (
        "0.6.2-read-only-contract-v1",
        {
            name: CatalogToolPolicy(read_only=True, effect="read")
            for name in (
                "git_status", "git_diff_unstaged", "git_diff_staged", "git_diff",
                "git_log", "git_show", "git_branch",
            )
        },
    ),
    "markitdown-mcp": (
        "0.1.7-local-contract-v1",
        {
            "convert_to_markdown": CatalogToolPolicy(
                read_only=False,
                effect="artifact-create",
            ),
        },
    ),
}


def _credential(
    key: str,
    label: str,
    description: str,
) -> CatalogCredentialSlotPolicy:
    return CatalogCredentialSlotPolicy(
        key=key,
        label=label,
        description=description,
    )


@dataclass(frozen=True, slots=True)
class WaveFourAdapterSpec:
    adapter_version: str
    tools: tuple[str, ...]
    credential_policies: tuple[CatalogCredentialSlotPolicy, ...]
    setting_policies: tuple[CatalogSettingPolicy, ...] = ()
    network_policy: str = ""
    limitations: tuple[str, ...] = ()


WAVE_FOUR_ADAPTERS: dict[str, WaveFourAdapterSpec] = {
    "agentql-mcp": WaveFourAdapterSpec(
        "1.0.1",
        ("extract-web-data",),
        (_credential("api_key", "AgentQL API Key", "用于只读网页数据提取。"),),
        network_policy="allowlist:api.agentql.com",
    ),
    "brave-search-mcp": WaveFourAdapterSpec(
        "0.6.2-archived-contract-v1",
        ("brave_web_search", "brave_local_search"),
        (_credential("api_key", "Brave Search API Key", "用于网页与地点搜索。"),),
        network_policy="allowlist:api.search.brave.com",
        limitations=("上游参考服务器已归档；本适配器锁定 0.6.2 工具契约并监测漂移。",),
    ),
    "exa-mcp": WaveFourAdapterSpec(
        "3.4.0",
        ("web_search_exa", "web_fetch_exa"),
        (_credential("api_key", "Exa API Key", "用于只读搜索与正文提取。"),),
        network_policy="allowlist:api.exa.ai",
    ),
    "firecrawl-mcp": WaveFourAdapterSpec(
        "3.23.4",
        ("firecrawl_search", "firecrawl_scrape", "firecrawl_map"),
        (_credential("api_key", "Firecrawl API Key", "用于搜索、单页抓取与站点地图。"),),
        network_policy="allowlist:api.firecrawl.dev",
        limitations=("已关闭 crawl、agent、extract、interact、反馈写入与长任务工具。",),
    ),
    "perplexity-mcp": WaveFourAdapterSpec(
        "1.2.0",
        ("perplexity_search", "perplexity_ask"),
        (_credential("api_key", "Perplexity API Key", "用于搜索与简短问答。"),),
        network_policy="allowlist:api.perplexity.ai",
        limitations=("首轮关闭深度研究与推理工具，避免不可控的长任务和费用。",),
    ),
    "tavily-mcp": WaveFourAdapterSpec(
        "0.2.22",
        ("tavily_search", "tavily_extract", "tavily_map"),
        (_credential("api_key", "Tavily API Key", "用于搜索、正文提取与站点地图。"),),
        network_policy="allowlist:api.tavily.com",
        limitations=("已关闭 crawl 与 research 长任务工具。",),
    ),
    "axiom-mcp": WaveFourAdapterSpec(
        "v0.05-pinned-compatible-v1",
        ("queryApl", "listDatasets", "getDatasetSchema", "getSavedQueries", "getMonitors", "getMonitorsHistory"),
        (_credential("api_token", "Axiom API Token", "仅用于数据集、查询与监控读取。"),),
        (CatalogSettingPolicy("organization_id", "组织 ID", "Axiom 组织的固定标识。", required=True, pattern=r"^[A-Za-z0-9_-]{1,120}$"),),
        "allowlist:api.axiom.co",
        ("上游 stdio 仓库已归档并推荐 OAuth 远程端点；本批仅保留经锁定的 Token 只读契约，不提供外站登录。",),
    ),
    "figma-context-mcp": WaveFourAdapterSpec(
        "0.13.2",
        ("get_figma_data",),
        (_credential("api_token", "Figma Personal Access Token", "仅用于读取明确指定的 Figma 文件。"),),
        network_policy="allowlist:api.figma.com",
        limitations=("下载图片会写入文件系统，本批不开放 download_figma_images。",),
    ),
    "google-maps-mcp": WaveFourAdapterSpec(
        "0.6.2-archived-contract-v1",
        ("maps_geocode", "maps_reverse_geocode", "maps_search_places", "maps_distance_matrix", "maps_elevation", "maps_directions"),
        (_credential("api_key", "Google Maps API Key", "用于地图检索与路线计算。"),),
        network_policy="allowlist:maps.googleapis.com",
        limitations=("上游参考服务器已归档；仅开放工具发现中声明的六个只读工具。",),
    ),
    "grafana-mcp": WaveFourAdapterSpec(
        "1.0.0-cloud-read-only-v1",
        ("search_dashboards", "get_dashboard_by_uid", "list_datasources", "list_alert_rules"),
        (_credential("service_token", "Grafana Cloud Service Account Token", "仅用于读取仪表盘、数据源与告警规则。"),),
        (CatalogSettingPolicy("stack_slug", "Grafana Cloud Stack", "仅填写 Stack 子域标识，例如 my-stack。", kind="slug", required=True, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"),),
        "derived-allowlist:{stack_slug}.grafana.net",
        ("首轮仅支持 Grafana Cloud Stack；不接受自托管 URL，也不开放任何写入工具。",),
    ),
    "graphlit-mcp": WaveFourAdapterSpec(
        "1.0.20260112001",
        ("queryProjectUsage", "askGraphlit", "retrieveSources", "queryContents", "queryCollections", "queryFeeds", "queryConversations", "webMap", "webSearch"),
        (_credential("api_token", "Graphlit API Token", "仅用于读取、检索和问答。"),),
        (
            CatalogSettingPolicy("organization_id", "组织 ID", "Graphlit 组织标识。", required=True, pattern=r"^[A-Za-z0-9_-]{1,120}$"),
            CatalogSettingPolicy("environment_id", "环境 ID", "Graphlit 环境标识。", required=True, pattern=r"^[A-Za-z0-9_-]{1,120}$"),
        ),
        "allowlist:graphlit-api.azurewebsites.net",
        ("已关闭内容摄取、发布、删除、对话写入和任意 URL 图片读取。",),
    ),
    "kagi-mcp": WaveFourAdapterSpec(
        "0d62ed3-compatible-v1",
        ("kagi_search",),
        (_credential("api_token", "Kagi API Token", "用于只读 Kagi 搜索。"),),
        network_policy="allowlist:kagi.com",
    ),
    "pinecone-assistant-mcp": WaveFourAdapterSpec(
        "0.1.0-read-only-v1",
        ("assistant_chat",),
        (_credential("api_key", "Pinecone API Key", "用于访问既有 Assistant。"),),
        (
            CatalogSettingPolicy("assistant_host", "Assistant 主机名", "仅填写控制台提供的 .pinecone.io 主机名，不含协议和路径。", kind="hostname", required=True, allowed_hostname_suffixes=(".pinecone.io",)),
            CatalogSettingPolicy("assistant_name", "Assistant 名称", "要查询的既有 Assistant 名称。", required=True, pattern=r"^[A-Za-z0-9_-]{1,80}$"),
        ),
        "derived-allowlist:{assistant_host}",
        ("仅开放既有 Assistant 的问答检索；文件上传、删除和 Assistant 管理均关闭。",),
    ),
    "shodan-mcp": WaveFourAdapterSpec(
        "1.0.22",
        ("ip_lookup", "shodan_search", "cve_lookup", "dns_lookup", "reverse_dns_lookup", "cpe_lookup", "cves_by_product"),
        (_credential("api_key", "Shodan API Key", "用于资产、DNS 与 CVE 只读检索。"),),
        network_policy="allowlist:api.shodan.io,cvedb.shodan.io",
    ),
    "virustotal-mcp": WaveFourAdapterSpec(
        "1.0.25",
        ("get_url_report", "get_url_relationship", "get_file_report", "get_file_relationship", "get_ip_report", "get_ip_relationship", "get_domain_report", "get_domain_relationship", "search_vt", "get_file_behaviour_summary", "get_collection"),
        (_credential("api_key", "VirusTotal API Key", "仅用于读取既有分析报告和关系。"),),
        network_policy="allowlist:www.virustotal.com",
        limitations=("不上传样本、不触发重新扫描；URL 工具只读取 VirusTotal 已缓存报告。",),
    ),
}


WAVE_THIRTEEN_TOKEN_ADAPTERS: dict[str, WaveFourAdapterSpec] = {
    "brave-brave-search-mcp-server": WaveFourAdapterSpec(
        "2.1.0",
        ("brave_web_search", "brave_local_search"),
        (_credential("api_key", "Brave Search API Key", "用于网页与地点搜索。"),),
        network_policy="allowlist:api.search.brave.com",
        limitations=(
            "仅开放官方 v2.1.0 的网页搜索与地点搜索；LLM Context、摘要、图片、视频和新闻工具均不可发现、不可调用。",
            "凭据由服务端加密库按固定槽位注入；客户端不能提交 Token、Header、环境变量、命令或 MCP URL。",
        ),
    ),
}


WAVE_FOURTEEN_TOKEN_ADAPTERS: dict[str, WaveFourAdapterSpec] = {
    "blazickjp-arxiv-mcp-server": WaveFourAdapterSpec(
        "0.6.2-compatible-native-v1",
        ("search_papers", "get_abstract"),
        (),
        network_policy="allowlist:export.arxiv.org",
        limitations=(
            "原生兼容层仅复现 v0.6.2 的论文搜索与摘要元数据读取；不运行上游宽工具进程。",
            "下载、全文读取、本地缓存、提醒、语义索引、引用导出和文件资源全部关闭；每次最多 20 篇，出口请求至少间隔 3 秒。",
        ),
    ),
    "kagisearch-kagimcp": WaveFourAdapterSpec(
        "1.0.2-compatible-native-v1",
        ("kagi_search_fetch", "kagi_extract"),
        (_credential("api_key", "Kagi API Key", "用于官方 Kagi Search 与 Extract API。"),),
        network_policy="allowlist:kagi.com",
        limitations=(
            "原生兼容层仅复现官方 v1.0.2 的搜索与单页提取；不运行可覆盖 API Host 或重试策略的上游进程。",
            "搜索上限固定为 20；提取 URL 必须为公网 HTTPS 且不得携带 Token、签名或其他凭据型查询参数。",
        ),
    ),
}


WAVE_FIFTEEN_TOKEN_ADAPTERS: dict[str, WaveFourAdapterSpec] = {
    "fatwang2-search1api-mcp": WaveFourAdapterSpec(
        "0.5.3-compatible-native-v1",
        ("search", "news", "trending"),
        (
            _credential(
                "api_key",
                "Search1API Key",
                "用于官方 Search1API 的网页、新闻与趋势只读发现。",
            ),
        ),
        network_policy="allowlist:api.search1api.com",
        limitations=(
            "原生兼容层仅开放官方 v0.5.3 的 search、news 与 trending；crawl、sitemap、截图、结构化提取和任意页面抓取均不可发现、不可调用。",
            "搜索与新闻强制 crawl_results=0，每次最多 20 条，出口固定为 api.search1api.com，并按官方账户级限流保守节流。",
        ),
    ),
    "livetennisapi-livetennisapi-mcp": WaveFourAdapterSpec(
        "1.4.0-compatible-native-v1",
        (
            "get_live_matches",
            "get_upcoming_matches",
            "get_match_score",
            "search_players",
            "get_player",
            "get_fixtures",
            "search_tournaments",
            "get_tournament",
        ),
        (
            _credential(
                "api_key",
                "Live Tennis API Key",
                "用于 FREE 层实时比分、赛程、球员与赛事目录只读查询。",
            ),
        ),
        network_policy="allowlist:api.livetennisapi.com",
        limitations=(
            "仅开放官方 v1.4.0 对应 FREE 层的实时/即将开始比赛、当前比分、球员、赛程与赛事目录；历史、赔率、市场、预测、模型分析、统计、WebSocket 与用量探测均关闭。",
            "响应按固定字段投影并移除 win_probability、danger、market、analysis 与 stats；每页最多 20 条、offset 最多 1000，出口请求至少间隔 2.1 秒。",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class WaveFiveAdapterSpec:
    adapter_version: str
    tools: tuple[str, ...]
    database_policy: CatalogDatabasePolicy
    credential_policies: tuple[CatalogCredentialSlotPolicy, ...] = ()
    setting_policies: tuple[CatalogSettingPolicy, ...] = ()
    limitations: tuple[str, ...] = ()
    network_policy: str = "database-egress:validated-host,admin-private-allowlist"
    workspace_extensions: tuple[str, ...] = ()
    wave: int = 5


def _database_host() -> CatalogSettingPolicy:
    return CatalogSettingPolicy(
        "host",
        "数据库主机名",
        "只填写主机名，不含协议、端口、路径或用户信息；IP 字面量会被拒绝。",
        kind="hostname",
        required=True,
    )


def _database_port(default: int) -> CatalogSettingPolicy:
    return CatalogSettingPolicy(
        "port",
        "数据库端口",
        "仅允许 1 到 65535 的固定 TCP 端口。",
        kind="integer",
        required=True,
        default=default,
        minimum=1,
        maximum=65_535,
    )


def _database_name() -> CatalogSettingPolicy:
    return CatalogSettingPolicy(
        "database",
        "数据库名称",
        "只填写目标数据库名称，不接受连接串或路径。",
        required=True,
        pattern=r"^[A-Za-z0-9_.$-]{1,128}$",
    )


def _database_username(*, required: bool = True) -> CatalogSettingPolicy:
    return CatalogSettingPolicy(
        "username",
        "只读用户名",
        "建议使用仅具有读取权限的独立数据库账号。",
        required=required,
        pattern=r"^[A-Za-z0-9_.-]{1,128}$",
    )


def _database_tls() -> CatalogSettingPolicy:
    return CatalogSettingPolicy(
        "tls_mode",
        "TLS 校验模式",
        "本批固定进行证书链和主机名校验，不允许明文或跳过校验。",
        kind="enum",
        required=True,
        default="verify-full",
        options=(("verify-full", "严格校验证书和主机名"),),
    )


WAVE_FIVE_ADAPTERS: dict[str, WaveFiveAdapterSpec] = {
    "dbhub": WaveFiveAdapterSpec(
        "1.2.0-read-only-contract-v1",
        ("list_schemas", "list_tables", "describe_table", "execute_sql"),
        CatalogDatabasePolicy(mode="remote-read-only", engine="dbhub"),
        (_credential("password", "数据库密码", "仅用于建立受控的只读数据库会话。"),),
        (
            CatalogSettingPolicy(
                "engine",
                "数据库类型",
                "首批仅支持已审计的远程数据库驱动。",
                kind="enum",
                required=True,
                options=(
                    ("postgresql", "PostgreSQL"),
                    ("mysql", "MySQL"),
                    ("mariadb", "MariaDB"),
                ),
            ),
            _database_host(),
            _database_port(5432),
            _database_name(),
            _database_username(),
            _database_tls(),
        ),
        (
            "由服务端生成固定连接配置；不接受 DSN、SSH 隧道、多数据源、SQLite 路径或自定义工具。",
            "只开放结构浏览和单语句只读查询，同时要求数据库侧只读账号。",
        ),
    ),
    "mongodb-mcp": WaveFiveAdapterSpec(
        "2.0.0-read-only-contract-v1",
        (
            "list_collections", "collection_schema", "collection_indexes",
            "find", "aggregate", "count_documents",
        ),
        CatalogDatabasePolicy(mode="remote-read-only", engine="mongodb"),
        (_credential("password", "MongoDB 密码", "仅用于既有数据库的只读访问。"),),
        (
            _database_host(),
            _database_port(27017),
            _database_name(),
            _database_username(),
            _database_tls(),
            CatalogSettingPolicy(
                "auth_source",
                "认证数据库",
                "默认使用 admin；不接受连接 URI。",
                required=True,
                default="admin",
                pattern=r"^[A-Za-z0-9_.$-]{1,128}$",
            ),
        ),
        (
            "固定启用上游只读模式并关闭请求覆盖；Atlas 管理、临时用户和写入工具全部不开放。",
            "聚合管道拒绝 $out、$merge、$where、$function 等写入或代码执行阶段。",
        ),
    ),
    "clickhouse-mcp": WaveFiveAdapterSpec(
        "0.4.1-read-only-contract-v1",
        ("list_databases", "list_tables", "run_query"),
        CatalogDatabasePolicy(mode="remote-read-only", engine="clickhouse"),
        (_credential("password", "ClickHouse 密码", "仅用于只读查询会话。"),),
        (
            _database_host(),
            _database_port(8443),
            _database_name(),
            _database_username(),
            _database_tls(),
        ),
        (
            "固定关闭写访问和 chDB；remote、url、file 等可绕过目标边界的表函数会被拒绝。",
            "数据库用户仍须配置 readonly=1，并受 15 秒查询超时和 1000 行硬上限约束。",
        ),
    ),
    "redis-mcp": WaveFiveAdapterSpec(
        "0.5.1-read-only-contract-v1",
        (
            "scan_keys", "get_value", "get_type", "get_ttl", "hash_get_all",
            "list_range", "set_members", "sorted_set_range",
        ),
        CatalogDatabasePolicy(mode="remote-read-only", engine="redis"),
        (_credential("password", "Redis 密码", "仅用于受限 ACL 只读账号。"),),
        (
            _database_host(),
            _database_port(6380),
            _database_tls(),
            _database_username(required=False),
            CatalogSettingPolicy(
                "database",
                "Redis 数据库编号",
                "仅允许 0 到 15。",
                kind="integer",
                required=True,
                default=0,
                minimum=0,
                maximum=15,
            ),
        ),
        (
            "必须同时使用 Redis 只读 ACL；不开放 raw command、Lua、CONFIG、DEBUG、KEYS 或任何写入命令。",
            "SCAN 和集合读取均使用固定分页与返回数量上限。",
        ),
    ),
    "duckdb-mcp": WaveFiveAdapterSpec(
        "1.0.7-local-read-only-contract-v1",
        ("list_schemas", "list_tables", "describe_table", "query"),
        CatalogDatabasePolicy(
            mode="local-file-read-only",
            engine="duckdb",
            tls_required=False,
            preflight_checks=(
                "sealed-workspace",
                "file-integrity",
                "native-read-only-mode",
                "query-limits",
            ),
        ),
        limitations=(
            "只允许封存工作区内的 .duckdb 文件标识，进程断网且不能读取兄弟工作区或宿主路径。",
            "固定禁用 ATTACH、COPY、INSTALL、LOAD、外部访问和扩展自动加载；MotherDuck、S3 与远程切库不开放。",
        ),
        network_policy="disabled",
        workspace_extensions=(".duckdb",),
    ),
    "supabase-mcp": WaveFiveAdapterSpec(
        "0.9.0-stdio-pat-read-only-v1",
        ("list_tables", "list_extensions", "execute_sql"),
        CatalogDatabasePolicy(mode="remote-read-only", engine="supabase"),
        (_credential("access_token", "Supabase Personal Access Token", "仅用于指定项目的只读能力。"),),
        (
            CatalogSettingPolicy(
                "project_ref",
                "Supabase 项目标识",
                "只填写固定 project ref，不接受 API URL。",
                kind="slug",
                required=True,
                pattern=r"^[a-z]{20}$",
            ),
        ),
        (
            "仅支持本地 stdio、PAT 和指定项目；固定启用只读模式并限制为数据库、调试和文档能力。",
            "远程 OAuth、迁移、函数、分支、项目管理和其他修改操作继续保留到后续批次。",
        ),
        network_policy="allowlist:api.supabase.com,supabase.com",
    ),
}


WAVE_NINETEEN_DATABASE_ADAPTERS: dict[str, WaveFiveAdapterSpec] = {
    "pab1it0-prometheus-mcp-server": WaveFiveAdapterSpec(
        "1.6.2-compatible-native-read-only-v1",
        (
            "execute_query",
            "execute_range_query",
            "list_metrics",
            "get_metric_metadata",
            "get_targets",
        ),
        CatalogDatabasePolicy(
            mode="remote-read-only",
            engine="prometheus",
            max_rows_default=100,
            max_rows_hard=200,
            statement_timeout_seconds=12,
            preflight_checks=(
                "dns-policy",
                "tls-verification",
                "build-info",
                "query-limits",
            ),
        ),
        (
            CatalogCredentialSlotPolicy(
                "bearer_token",
                "Prometheus Bearer Token",
                "可选；仅用于已有只读 HTTP 认证代理或 Prometheus 服务。",
                required=False,
            ),
        ),
        (_database_host(), _database_port(9090), _database_tls()),
        (
            "只开放 instant/range PromQL、指标名称/元数据和 active targets；规则、告警、配置、管理与写入入口不存在。",
            "范围最多 24 小时、每序列最多 1000 点、最多返回 200 个序列或目录项。",
        ),
        wave=19,
    ),
    "qdrant-mcp-server-qdrant": WaveFiveAdapterSpec(
        "0.8.1-compatible-native-read-only-v1",
        ("get_collection_info", "scroll_points", "query_points"),
        CatalogDatabasePolicy(
            mode="remote-read-only",
            engine="qdrant",
            max_rows_default=50,
            max_rows_hard=100,
            statement_timeout_seconds=12,
            preflight_checks=(
                "dns-policy",
                "tls-verification",
                "authentication",
                "collection-scope",
                "native-read-only-key",
                "query-limits",
            ),
        ),
        (_credential("api_key", "Qdrant 只读 API Key", "必须使用 Qdrant 原生只读 Key。"),),
        (
            _database_host(),
            _database_port(6333),
            _database_tls(),
            CatalogSettingPolicy(
                "collection",
                "Qdrant Collection",
                "只绑定一个既有 collection，不接受别名或动态资源选择。",
                required=True,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
            ),
        ),
        (
            "qdrant-store、写入、删除、索引和 collection 管理工具不可发现。",
            "分页和向量查询最多 100 点、向量最多 4096 维，结果不返回向量正文。",
        ),
        wave=19,
    ),
    "cr7258-elasticsearch-mcp-server": WaveFiveAdapterSpec(
        "2.1.2-compatible-native-read-only-v1",
        ("get_cluster_health", "get_index", "search_documents", "get_document"),
        CatalogDatabasePolicy(
            mode="remote-read-only",
            engine="elasticsearch",
            max_rows_default=50,
            max_rows_hard=100,
            statement_timeout_seconds=12,
            preflight_checks=(
                "dns-policy",
                "tls-verification",
                "authentication",
                "index-scope",
                "native-read-only-role",
                "query-limits",
            ),
        ),
        (_credential("password", "Elasticsearch 只读密码", "仅用于固定原生只读用户。"),),
        (
            _database_host(),
            _database_port(9200),
            _database_tls(),
            CatalogSettingPolicy(
                "index",
                "Elasticsearch Index",
                "只绑定一个既有索引；系统索引、通配符和动态切换均关闭。",
                required=True,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
            ),
            CatalogSettingPolicy(
                "search_field",
                "搜索字段",
                "服务端只在该固定字段构造 match 查询。",
                required=True,
                pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$",
            ),
            _database_username(),
        ),
        (
            "仅开放索引健康、mapping、固定字段 match 查询和单文档读取；任意 DSL、通用 API、写入、删除与管理全部关闭。",
            "原生角色仅允许 cluster monitor 与目标索引 read/view_index_metadata，最多返回 100 条。",
        ),
        wave=19,
    ),
    "zilliztech-mcp-server-milvus": WaveFiveAdapterSpec(
        "0.1.1-compatible-native-read-only-v1",
        ("list_collections", "describe_collection", "get_entities", "search_vectors"),
        CatalogDatabasePolicy(
            mode="remote-read-only",
            engine="milvus",
            max_rows_default=50,
            max_rows_hard=100,
            statement_timeout_seconds=12,
            preflight_checks=(
                "dns-policy",
                "tls-verification",
                "authentication",
                "database-and-collection-scope",
                "native-read-only-role",
                "query-limits",
            ),
        ),
        (_credential("password", "Milvus 只读密码", "仅用于固定 database 与 collection 的原生只读用户。"),),
        (
            _database_host(),
            _database_port(19530),
            _database_tls(),
            _database_name(),
            _database_username(),
            CatalogSettingPolicy(
                "collection",
                "Milvus Collection",
                "只绑定一个既有 collection，不接受动态切换。",
                required=True,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
            ),
            CatalogSettingPolicy(
                "vector_field",
                "向量字段",
                "固定一个既有向量字段，不接受运行时覆盖。",
                required=True,
                pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$",
            ),
            CatalogSettingPolicy(
                "output_fields",
                "输出字段",
                "逗号分隔的固定字段白名单，最多 32 个。",
                required=True,
                pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:,[A-Za-z_][A-Za-z0-9_]*){0,31}$",
            ),
        ),
        (
            "仅开放集合目录、集合描述、按 ID 读取和固定向量字段搜索；insert、delete、动态 filter、任意输出字段和管理入口关闭。",
            "实体 ID 最多 100 个、向量搜索最多 100 条，且必须使用只具 Query/Search/DescribeCollection/ShowCollections 权限的原生账号。",
        ),
        wave=23,
    ),
    "neo4j-contrib-mcp-neo4j": WaveFiveAdapterSpec(
        "mcp-neo4j-cypher-v0.6.0-compatible-native-read-only-v1",
        ("get_schema", "read_cypher"),
        CatalogDatabasePolicy(
            mode="remote-read-only",
            engine="neo4j",
            max_rows_default=50,
            max_rows_hard=100,
            statement_timeout_seconds=12,
            preflight_checks=(
                "dns-policy",
                "tls-verification",
                "authentication",
                "database-scope",
                "native-reader-role",
                "readonly-query-type",
            ),
        ),
        (_credential("password", "Neo4j 只读密码", "必须属于固定 database 的原生 reader 账号。"),),
        (
            _database_host(),
            _database_port(7474),
            _database_tls(),
            _database_name(),
            _database_username(),
        ),
        (
            "仅开放固定 database 的 Schema 与单条只读 Cypher；写子句、procedure、扩展函数、LOAD、管理、记忆和多语句全部关闭。",
            "服务端包裹固定 LIMIT，并要求 Query API 返回只读 queryType；必须使用 Neo4j 原生 reader 角色。",
        ),
        wave=23,
    ),
    "arcadedata-arcadedb": WaveFiveAdapterSpec(
        "26.8.1-compatible-native-read-only-v1",
        ("list_types", "describe_type", "read_query"),
        CatalogDatabasePolicy(
            mode="remote-read-only",
            engine="arcadedb",
            max_rows_default=50,
            max_rows_hard=100,
            statement_timeout_seconds=12,
            preflight_checks=(
                "dns-policy",
                "tls-verification",
                "authentication",
                "database-scope",
                "native-readonly-group",
                "query-limits",
            ),
        ),
        (_credential("password", "ArcadeDB 只读密码", "必须属于固定 database 的原生 readonly 账号。"),),
        (
            _database_host(),
            _database_port(2480),
            _database_tls(),
            _database_name(),
            _database_username(),
        ),
        (
            "仅开放固定 database 的类型目录、类型描述和 Query API 只读查询；command、写入、DDL、管理、脚本与导入导出关闭。",
            "只允许单条 SELECT、MATCH 或 TRAVERSE，最多返回 100 行，并要求原生账号属于 readonly 组。",
        ),
        wave=23,
    ),
}


@dataclass(frozen=True, slots=True)
class WaveSixAdapterSpec:
    adapter_version: str
    provider: str
    fixed_hosts: tuple[str, ...]
    rate_limit_per_minute: int
    tool_schema_sha256: str
    read_tools: tuple[str, ...]
    write_tools: tuple[str, ...]
    credential_policies: tuple[CatalogCredentialSlotPolicy, ...]
    setting_policies: tuple[CatalogSettingPolicy, ...]
    limitations: tuple[str, ...] = ()


WAVE_SIX_ADAPTERS: dict[str, WaveSixAdapterSpec] = {
    "airtable-mcp": WaveSixAdapterSpec(
        "wave6-v1",
        "Airtable",
        ("api.airtable.com",),
        240,
        "5fce8249d6fcfa6b57f17d6c4d996c0c1ac5b8299584547923c2f18b14ca86c4",
        ("list_tables", "list_records", "get_record"),
        ("create_record", "update_record"),
        (
            _credential(
                "personal_access_token",
                "Airtable Personal Access Token",
                "仅用于访问配置中固定的 Base。",
            ),
        ),
        (
            CatalogSettingPolicy(
                "base_id",
                "Airtable Base ID",
                "只填写固定 Base 标识，不接受 URL、Workspace 或任意端点。",
                required=True,
                pattern=r"^app[A-Za-z0-9]{14}$",
            ),
        ),
        ("删除记录、修改表结构、批量写入和任意 Base 访问均关闭。",),
    ),
    "asana-mcp": WaveSixAdapterSpec(
        "wave6-v1",
        "Asana",
        ("app.asana.com",),
        120,
        "c935b8d982352d5e8379fe32a84986a4dd45c7d8635a6bb95838d26680f37d86",
        ("list_projects", "list_tasks", "get_task"),
        ("create_task", "update_task", "add_comment"),
        (
            _credential(
                "personal_access_token",
                "Asana Personal Access Token",
                "仅用于配置中固定的 Workspace 与 Project。",
            ),
        ),
        (
            CatalogSettingPolicy(
                "workspace_gid",
                "Asana Workspace GID",
                "固定账号工作区；不接受 URL。",
                required=True,
                pattern=r"^[1-9][0-9]{0,31}$",
            ),
            CatalogSettingPolicy(
                "project_gid",
                "Asana Project GID",
                "所有任务读写均限制在该项目。",
                required=True,
                pattern=r"^[1-9][0-9]{0,31}$",
            ),
        ),
        ("删除任务或项目、批量修改和跨 Workspace 操作均关闭。",),
    ),
    "gitlab-mcp": WaveSixAdapterSpec(
        "wave6-v1-gitlab-com-only",
        "GitLab",
        ("gitlab.com",),
        300,
        "c5525a94bbe3dd3c4f83381f6138375243000102d31b28360641acc3cddb6dd9",
        (
            "list_issues", "get_issue", "list_merge_requests",
            "get_merge_request", "get_repository_file",
        ),
        ("create_issue", "update_issue", "add_issue_note"),
        (
            _credential(
                "personal_access_token",
                "GitLab Personal Access Token",
                "仅用于 gitlab.com 上配置中固定的 Project。",
            ),
        ),
        (
            CatalogSettingPolicy(
                "project_id",
                "GitLab Project ID",
                "首批只接受 gitlab.com 的数字 Project ID；自建实例继续阻断。",
                kind="integer",
                required=True,
                minimum=1,
                maximum=9_999_999_999,
            ),
        ),
        ("仅支持 gitlab.com；合并、仓库写入、流水线触发和删除操作全部关闭。",),
    ),
    "notion-mcp-server": WaveSixAdapterSpec(
        "wave6-v1",
        "Notion",
        ("api.notion.com",),
        150,
        "4c5a2edc829d7d8823dd6e2270d52c6bc8bc67f9a4cba55b6edc59be4a70da80",
        ("query_data_source", "retrieve_page"),
        ("create_page", "update_page_properties"),
        (
            _credential(
                "integration_token",
                "Notion Integration Token",
                "仅用于 Integration 已授权且配置中固定的 Data Source。",
            ),
        ),
        (
            CatalogSettingPolicy(
                "data_source_id",
                "Notion Data Source ID",
                "只填写已共享给 Integration 的固定 Data Source ID。",
                required=True,
                pattern=(
                    r"^(?:[0-9a-fA-F]{32}|"
                    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
                ),
            ),
        ),
        ("仅开放固定 Data Source；归档、删除、Schema 修改、任意搜索和跨作用域写入均关闭。",),
    ),
}


@dataclass(frozen=True, slots=True)
class WaveSevenAdapterSpec:
    adapter_version: str
    engine: str
    tool_schema_sha256: str
    read_tools: tuple[str, ...]
    artifact_tools: tuple[str, ...]
    state_write_tools: tuple[str, ...]


WAVE_SEVEN_ADAPTERS: dict[str, WaveSevenAdapterSpec] = {
    "chrome-devtools-mcp": WaveSevenAdapterSpec(
        "1.6.0-wave7-v1",
        "chrome-devtools",
        BROWSER_SCHEMA_SHA256["chrome-devtools-mcp"],
        ("browser_session_status", "take_snapshot"),
        ("take_screenshot",),
        ("navigate_page", "click", "fill"),
    ),
    "playwright-mcp": WaveSevenAdapterSpec(
        "0.0.79-wave7-v1",
        "playwright",
        BROWSER_SCHEMA_SHA256["playwright-mcp"],
        ("browser_session_status", "browser_snapshot"),
        ("browser_take_screenshot",),
        ("browser_navigate", "browser_click", "browser_fill_form"),
    ),
}


@dataclass(frozen=True, slots=True)
class WaveNineAdapterSpec:
    adapter_version: str
    tools: tuple[str, ...]
    credential_policies: tuple[CatalogCredentialSlotPolicy, ...]
    network_policy: str
    limitations: tuple[str, ...]
    sensitive: bool = False


WAVE_NINE_READY_ADAPTERS: dict[str, WaveNineAdapterSpec] = {
    "terraform-mcp": WaveNineAdapterSpec(
        "1.2.0-registry-read-only-compatible-v1",
        (
            "get_latest_provider_version",
            "get_provider_capabilities",
            "get_provider_details",
            "search_modules",
            "get_module_details",
            "get_latest_module_version",
        ),
        (),
        "allowlist:registry.terraform.io",
        (
            "仅访问匿名公共 Terraform Registry；不接收 Token，不读取工作区、状态文件、变量或本机 Terraform 配置。",
            "HCP Terraform、Terraform Enterprise、私有 Registry、plan、apply、destroy、run、workspace 与任何资源变更工具全部不可发现、不可调用。",
        ),
    ),
}


WAVE_NINE_BLOCKED_ADAPTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "apify-mcp": (
        "0.14.2-blocked:credential-and-cost-boundary",
        (
            "Actor 搜索与详情虽免费，但固定 stdio 上游仍要求 Apify Token；Actor 运行和动态工具会产生外部费用。",
            "本批不向 Apify 发送账号 Token，也不开放 OAuth、AGI、x402、MPP、Skyfire、Actor 运行、数据集或动态工具；需单独批准凭据出站后再评估只读发现子集。",
        ),
    ),
    "aiven-mcp": (
        "1.15.2-blocked:credential-egress-not-approved",
        (
            "上游 1.15.2 支持 read-only/core scope，但仍需向 Aiven API 发送项目账号 Token，并可读取项目、服务、VPC 与套餐元数据。",
            "本批未获向 Aiven 发送具体凭据的授权，因此 Token、配置、连接和工具入口全部关闭；后续需单独批准账号范围与凭据出站。",
        ),
    ),
    "bright-data-mcp": (
        "2.11.1-blocked:unreconciled-provider-cost",
        (
            "上游基础抓取与 Pro 工具均消耗 Bright Data 请求额度，且 Pro 模式默认不限制调用频率；当前目录没有可与供应商账单对账的逐项目硬预算。",
            "Token、连接和抓取工具全部关闭；完成供应商 usage 对账、硬预算、并发与停止开关前不以免费额度替代费用护栏。",
        ),
    ),
    "browserbase-mcp": (
        "3.0.0-blocked:archived-cloud-browser-runtime",
        (
            "官方仓库已归档，托管浏览器会话又会持续消耗外部资源，无法锁定维护中的生产契约与费用停止语义。",
            "连接、项目密钥、云浏览器和任意页面操作全部关闭；需要浏览器能力时使用第七批的一次性本地浏览器适配器。",
        ),
    ),
    "e2b-mcp": (
        "0.1.1-blocked:archived-billed-code-runtime",
        (
            "官方 MCP 仓库已归档；上游沙箱默认允许联网、支持任意安装和运行命令，并在存活期间计费。",
            "连接、API Key、代码、依赖安装和云沙箱创建入口全部关闭；不会以当前归档包承载不可信代码。",
        ),
    ),
    "stripe-mcp": (
        "remote-oauth-blocked:wave10",
        (
            "Stripe 当前官方 MCP 是 mcp.stripe.com 的 OAuth 远程服务，而非旧 agent-toolkit 本地包；支付、退款和订阅写入具有真实资金影响。",
            "本批不打开外站登录、API Key 或支付对象入口；转入第十批完成 OAuth scope、账号解绑与终止性金融操作审批。",
        ),
    ),
    "alpaca-mcp": (
        "v2-blocked:trading-and-market-data-cost",
        (
            "官方 v2 同时暴露真实交易、平仓、期权行权和账户能力，市场数据订阅也可能产生套餐费用；paper 默认值不能证明凭据属于模拟账户。",
            "API Key、行情、账户和订单工具全部关闭；完成 paper/live 身份证明、行情费用上限与金融终止操作审批前不开放。",
        ),
    ),
    "aws-kb-mcp": (
        "2026.08.20260805092707-blocked:aws-scope-and-query-cost",
        (
            "Bedrock Knowledge Bases 检索依赖 AWS 身份、区域和知识库范围，并可能产生检索或模型费用；结果还可能包含企业敏感内容。",
            "AWS 凭据、Knowledge Base ID 和检索入口全部关闭；等待 SigV4 凭据代理、资源白名单、usage 对账与数据范围预检。",
        ),
    ),
    "elevenlabs-mcp": (
        "0.12.2-blocked:paid-media-and-artifacts",
        (
            "语音、音效与相关生成工具会消耗外部额度并产生音频产物，部分工具还管理 Voice 等持久资源。",
            "API Key、生成、克隆、播放、下载和删除入口全部关闭；等待字符/时长预算、价格快照、产物隔离和逐次费用确认。",
        ),
    ),
    "minimax-mcp": (
        "main-blocked:paid-multimodal-and-local-files",
        (
            "官方工具会发起语音、图像、视频、音乐和 Voice Clone 付费任务，并接受本地文件或 URL、写入媒体产物。",
            "API Key、地区主机、生成、轮询、文件和产物入口全部关闭；等待模型价格锁定、异步任务账本与媒体输入输出隔离。",
        ),
    ),
    "s3-mcp": (
        "2026.08.20260805092707-blocked:aws-resource-scope",
        (
            "S3 Tables Server 同时包含 Namespace 与 Table 的创建、更新和删除能力，依赖 AWS 凭据、区域和资源级 IAM。",
            "AWS 凭据、Bucket/Table ARN 和工具入口全部关闭；等待 SigV4 代理、固定资源白名单、只读工具集与终止性删除审批。",
        ),
    ),
    "kubernetes-mcp": (
        "0.0.66-blocked:cluster-credential-and-namespace-scope",
        (
            "上游虽提供 disable-destructive，但仍需 kubeconfig/ServiceAccount、集群网络和命名空间级 RBAC；日志与资源内容可能包含敏感数据。",
            "Kubeconfig、集群连接、exec、日志和资源工具全部关闭；等待固定集群/namespace 绑定、只读 RBAC 实测与连接后权限预检。",
        ),
    ),
    "semgrep-mcp": (
        "0.4.0-blocked:archived-local-scan-runtime",
        (
            "官方仓库已归档，扫描需要读取项目源码并启动本地 Semgrep 运行时，无法满足维护中的代码执行与文件范围契约。",
            "源码目录、Token、CLI 和扫描入口全部关闭；不会回退到归档包或未固定的社区替代品。",
        ),
    ),
}


WAVE_ELEVEN_BLOCKED_ADAPTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "xiaohongshu-mcp": (
        "v2026.07.26.1327-b8412a2-blocked:browser-account-and-publish",
        (
            "当前上游要求本机 Chromium 登录并持久保存 Cookie，公开 QR 登录、删除 Cookie、搜索、评论、收藏以及图文/视频发布工具；发布还接受外部 URL 与宿主绝对文件路径。",
            "现有服务端没有可信桌面主体、账号实例绑定、本地媒体授权和发布终止操作审批；浏览器登录态、代理、Cookie、二维码、文件和全部工具入口保持关闭。",
        ),
    ),
    "ableton-mcp": (
        "1.3.5-blocked:local-live-socket-and-project-write",
        (
            "Ableton MCP 1.3.5 需要把 Remote Script 安装进 Ableton Live，并通过 localhost:9000 TCP 桥接创建/删除轨道、编辑 Clip、加载设备和控制播放；上游还默认收集匿名工具遥测。",
            "当前没有可证明宿主版本、当前 Live Set、端口归属和用户在场的签名桌面桥；在项目快照、逐动作预览、撤销/未知结果和遥测禁用验收完成前不连接宿主。",
        ),
    ),
    "binary-ninja-mcp": (
        "1.2.1-blocked:commercial-host-and-binary-mutation",
        (
            "Binary Ninja MCP v1.2.1 是安装进商业桌面宿主的插件与 localhost:9009 桥接器，既读取反编译/内存数据，也能定义类型、创建函数、重命名和删除注释。",
            "当前不能验证许可证席位、打开二进制、桥接端口、插件版本和写入目标属于当前用户；缺少只读工具冻结与二进制修改审批时保持完全阻断。",
        ),
    ),
    "blender-mcp": (
        "1.8.0-blocked:arbitrary-python-host-execution",
        (
            "Blender MCP 1.8.0 通过宿主插件与本地 Socket 操作场景，并公开在 Blender 内执行任意 Python、读取/删除文件、下载外部资产和调用生成服务的能力。",
            "这些调用继承 Blender 进程的完整宿主权限，不能由服务端 sidecar 沙箱约束；在移除任意代码、固定插件握手、场景副本和产物范围前不提供安装或连接入口。",
        ),
    ),
    "ghidra-mcp": (
        "0.2.2+ghidra12.0.4-blocked:local-bridge-and-binary-mutation",
        (
            "GhidraMCP v0.2.2+ghidra12.0.4 在 Ghidra 内启动 localhost TCP 服务和 Go 桥，公开 70 个查询与修改工具，包括 patch_bytes、内存权限、结构、类型和符号修改；默认 localhost 模式不启用 API Key。",
            "当前没有固定 Ghidra 实例/Program、强制桥接认证和写入事务审批；端口扫描、多实例 target_port、远程模式、API Key 与全部分析工具均不接入。",
        ),
    ),
    "jetbrains-mcp": (
        "source-1.9.0/npm-1.8.0-blocked:ide-discovery-and-actions",
        (
            "JetBrains MCP Proxy 当前源码标签为 1.9.0、npm 包为 1.8.0；代理会扫描 63342—63352 或接受 HOST/IDE_PORT，并把项目读取、导航、检查和 IDE 动作转发到本机 HTTP API。",
            "当前没有经用户配对的 IDE 实例、项目根、端口所有权和工具级同意，也不能把容器内 localhost 当作用户 IDE；自动发现、LAN 主机和全部代理入口关闭。",
        ),
    ),
    "chatcrystal": (
        "0.5.8-blocked:sensitive-history-and-provider-state",
        (
            "ChatCrystal 0.5.8 会扫描 Claude Code、Cursor、Codex 等本机历史并导入完整编码对话，调用可配置 LLM/Embedding 服务，并通过 MCP 提供记忆检索与写回；云模式另有共享 API Token。",
            "对话可能包含源码、提示词和凭据，当前没有逐目录导入清单、内容脱敏、模型费用/保留策略和桌面主体绑定；本机扫描、Provider、Token、上传与记忆工具全部关闭。",
        ),
    ),
    "obsidian-mcp": (
        "0.15.0-blocked:host-vault-write-access",
        (
            "目录上游已迁移为 bitbonsai/mcpvault 0.15.0；它不需要 Obsidian 插件，但直接接收宿主 Vault 路径并开放笔记读取、覆盖/追加、移动、标签修改与确认删除。",
            "现有上传工作区不能等同用户实时 Vault，服务端也不接受任意宿主路径；在版本化本机代理、逐 Vault 授权、只读/写分离和备份恢复验收前保持关闭。",
        ),
    ),
    "opentabs": (
        "0.0.115-blocked:authenticated-browser-and-dynamic-plugins",
        (
            "OpenTabs 0.0.115 通过 Chrome 扩展复用用户已登录会话，宣称 100+ 动态插件与约 2000 个工具，可直接调用 Slack、GitHub、AWS、Stripe 等真实 Web API，并允许安装自定义插件。",
            "插件内确认不能替代模镜的固定 Schema、账号/Origin 绑定和外部写入账本；现有浏览器 sidecar明确不继承登录态，因此扩展、动态插件、Cookie 和所有工具入口关闭。",
        ),
    ),
    "zotero-mcp": (
        "0.9.1-blocked:local-library-and-cloud-write-scope",
        (
            "Zotero MCP 0.9.1 的本地模式可读取 Zotero 7+ 文献、附件全文与批注；配置 Web API Key 后又会新增文献、更新笔记/批注和下载 PDF，语义检索还可调用外部 Embedding 服务。",
            "当前没有签名桌面桥来确认本地 Zotero 实例和 Library，也未冻结仅本地只读工具与附件范围；API Key、数据库路径、全文、Provider 和写入入口全部关闭。",
        ),
    ),
    "docker-mcp": (
        "0.43.3-blocked:docker-daemon-and-dynamic-server-control",
        (
            "Docker MCP Gateway v0.43.3 是管理动态 MCP 目录、容器生命周期、Secrets 和 OAuth 的 Docker CLI 插件，而非单一固定只读工具；运行它需要可信 Docker Desktop/daemon 控制面。",
            "模镜服务端禁止挂载 Docker Socket，也不允许用户选择镜像、目录、工具、Secret 或网络策略；不能把现有 sidecar 隔离边界反向交给动态 Gateway 管理。",
        ),
    ),
    "mobile-mcp": (
        "1.0.2-blocked:device-control-and-installation",
        (
            "Mobile MCP 1.0.2 直接调用 adb、xcrun simctl、WebDriverAgent 或真实 USB 设备，可安装/卸载应用、点击输入、打开 URL、录屏并读取崩溃报告；SSE 模式还可暴露本机端口。",
            "当前没有设备序列号所有权、测试专用设备证明、应用 allowlist、输入隐私和安装/卸载终止审批；SDK、USB、Bearer Token、端口和全部设备工具关闭。",
        ),
    ),
    "xcodebuild-mcp": (
        "2.7.0-blocked:macos-build-and-ui-control",
        (
            "XcodeBuildMCP 2.7.0 仅能在具备 Xcode 的 macOS 宿主工作，公开项目发现、构建、测试、清理、安装/启动应用、调试、日志、截图与 UI 点击/输入等工具，并写入用户 Library 状态。",
            "当前 Windows/Docker 部署没有可验真的 macOS 主机、工程范围、Simulator/Device 和签名身份；构建执行、UI 自动化、项目脚手架与全部宿主文件入口关闭。",
        ),
    ),
}


WAVE_PROJECTS: dict[int, tuple[str, ...]] = {
    1: ("calculator-mcp", "time-mcp", "vegalite-mcp"),
    2: (
        "bibigpt-mcp",
        "fetch-mcp",
        "quickchart-mcp",
        "airbnb-mcp",
        "geowire-mcp",
    ),
    3: (
        "basic-memory-mcp",
        "excel-mcp-server",
        "git-mcp",
        "manim-mcp",
        "markitdown-mcp",
    ),
    4: (
        "agentql-mcp",
        "brave-search-mcp",
        "exa-mcp",
        "firecrawl-mcp",
        "perplexity-mcp",
        "tavily-mcp",
        "axiom-mcp",
        "figma-context-mcp",
        "google-maps-mcp",
        "grafana-mcp",
        "graphlit-mcp",
        "kagi-mcp",
        "pinecone-assistant-mcp",
        "shodan-mcp",
        "snyk-mcp",
        "virustotal-mcp",
    ),
    5: (
        "dbhub",
        "postgres-mcp",
        "mongodb-mcp",
        "clickhouse-mcp",
        "cognee-mcp",
        "graphiti-mcp",
        "hindsight-mcp",
        "redis-mcp",
        "sqlite-mcp",
        "duckdb-mcp",
        "supabase-mcp",
    ),
    6: (
        "airtable-mcp",
        "asana-mcp",
        "gitlab-mcp",
        "mcp-cn-commerce",
        "notion-mcp-server",
        "mem0-mcp",
    ),
    7: (
        "chrome-devtools-mcp",
        "playwright-mcp",
        "puppeteer-mcp",
        "selenium-mcp",
    ),
    8: ("mcp-run-python", "python-interpreter"),
    9: (
        "apify-mcp",
        "bright-data-mcp",
        "browserbase-mcp",
        "e2b-mcp",
        "stripe-mcp",
        "terraform-mcp",
        "aiven-mcp",
        "alpaca-mcp",
        "aws-kb-mcp",
        "elevenlabs-mcp",
        "minimax-mcp",
        "s3-mcp",
        "kubernetes-mcp",
        "semgrep-mcp",
    ),
    10: (
        "gmail-mcp",
        "atlassian-mcp",
        "google-calendar-mcp",
        "google-drive-mcp",
        "microsoft-365-mcp",
        "onedrive-mcp",
        "sentry-mcp",
        "azure-mcp",
        "box-mcp",
        "cloudflare-mcp",
        "github-mcp-server",
        "linear-mcp",
        "neon-mcp",
        "slack-mcp",
    ),
    11: (
        "xiaohongshu-mcp",
        "ableton-mcp",
        "binary-ninja-mcp",
        "blender-mcp",
        "ghidra-mcp",
        "jetbrains-mcp",
        "chatcrystal",
        "obsidian-mcp",
        "opentabs",
        "zotero-mcp",
        "docker-mcp",
        "mobile-mcp",
        "xcodebuild-mcp",
    ),
}


WAVE_METADATA: dict[
    int,
    tuple[AdapterConnectionKind, AdapterRisk, tuple[str, ...], tuple[str, ...]],
] = {
    1: (
        "sandboxed-stdio",
        "low",
        ("isolated-python-runtime", "resource-limits"),
        ("等待独立 Python 沙箱、断网策略和资源上限验证。",),
    ),
    2: (
        "remote-mcp",
        "medium",
        ("public-remote-policy", "ssrf-protection"),
        ("等待公网目标、DNS、重定向和响应大小策略验证。",),
    ),
    3: (
        "sandboxed-stdio",
        "medium",
        ("scoped-filesystem", "artifact-cleanup"),
        ("等待目录授权、路径越界防护和产物清理验证。",),
    ),
    4: (
        "remote-mcp",
        "medium",
        ("encrypted-credential-binding", "read-only-tool-policy"),
        ("等待固定凭据槽、出口域名和只读工具清单验证。",),
    ),
    5: (
        "sandboxed-stdio",
        "high",
        ("database-read-only-policy", "query-limits"),
        ("等待只读账号、TLS、查询超时和结果行数限制验证。",),
    ),
    6: (
        "remote-mcp",
        "high",
        ("mutating-tool-approval", "account-unbinding"),
        ("等待修改操作预览、审批、幂等和账号解绑验证。",),
    ),
    7: (
        "sandboxed-stdio",
        "high",
        ("ephemeral-browser", "browser-domain-policy"),
        ("等待临时浏览器、目标域及上传下载边界验证。",),
    ),
    8: (
        "sandboxed-stdio",
        "critical",
        ("ephemeral-code-sandbox", "process-resource-limits"),
        ("两个上游均未通过安全与发布物门槛，当前不提供代码执行运行时。",),
    ),
    9: (
        "sandboxed-stdio",
        "critical",
        ("cost-guardrails", "terminal-action-approval"),
        ("等待费用上限、资源预览和终止性操作强制审批验证。",),
    ),
    10: (
        "remote-mcp",
        "high",
        ("oauth-pkce", "oauth-revocation", "scope-review"),
        ("等待 PKCE、state、最小 scope、刷新、撤销和解绑验证。",),
    ),
    11: (
        "desktop-bridge",
        "critical",
        ("versioned-desktop-bridge", "per-app-consent"),
        ("等待本机桥接协议、宿主版本和逐应用授权验证。",),
    ),
}


def build_catalog_manifests() -> dict[str, CatalogAdapterManifest]:
    manifests: dict[str, CatalogAdapterManifest] = {}
    for project_id, (install_command, server_command) in LOCAL_STDIO_ADAPTERS.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=0,
            availability="ready",
            connection_kind="local-stdio",
            risk="medium" if project_id in {"filesystem-mcp", "memory-mcp"} else "low",
            required_capabilities=("existing-node-stdio-runtime",),
            limitations=("沿用现有本地 stdio 行为；批次 0 不扩大权限范围。",),
            server_command=server_command,
            install_command=install_command,
            legacy_unrestricted_calls=True,
            enabled_by_default=True,
        )

    for project_id, (adapter_version, tool_names) in WAVE_ONE_ADAPTERS.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=1,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="low",
            required_capabilities=(
                "isolated-python-runtime",
                "resource-limits",
            ),
            limitations=(
                "仅运行模镜内置兼容实现，不下载或执行上游任意代码。",
                "默认断网、文件系统只读；单次调用超时 10 秒，输出不超过 128 KiB。",
            ),
            adapter_version=adapter_version,
            runtime_image="modelmirror-sandbox:wave1-v1",
            network_policy="disabled",
            filesystem_policy="read-only-empty-workspace",
            resource_limits=(
                ("cpu", "1 core / 60 CPU seconds per session"),
                ("memory", "256 MiB per process / 512 MiB sidecar"),
                ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                ("operation_timeout", "10 seconds"),
                ("output", "128 KiB"),
            ),
            server_command=(*SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            tool_policies={
                name: CatalogToolPolicy(read_only=True)
                for name in tool_names
            },
            enabled_by_default=True,
            operation_timeout=10.0,
            max_output_bytes=128 * 1024,
        )

    for project_id, (
        adapter_version,
        tool_names,
        network_policy,
    ) in WAVE_TWO_ADAPTERS.items():
        project_limitations = {
            "fetch-mcp": (
                "仅允许用户明确提供的公网 HTTPS URL；每次请求和重定向都会重新执行 DNS 与 SSRF 校验。",
                "固定遵守 robots.txt；最多重定向 3 次，原始响应不超过 1 MiB，工具返回不超过 128 KiB。",
            ),
            "quickchart-mcp": (
                "仅生成 quickchart.io 的受控 Chart.js 图表 URL；拒绝远程引用、脚本回调和超大配置。",
                "本批关闭上游 download_chart，本地文件写入将在文件处理批次单独适配。",
            ),
            "geowire-mcp": (
                "仅开放无需 Key 的 Nominatim 与公共 OSRM 子集，并保留 OpenStreetMap 来源标注。",
                "Nominatim 固定为每秒最多 1 次；公共 OSRM 仅开放驾车模式和受限坐标数量。",
            ),
        }[project_id]
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=2,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="medium",
            required_capabilities=(
                "public-remote-policy",
                "ssrf-protection",
                "dns-pinning",
                "redirect-response-limits",
            ),
            limitations=project_limitations,
            adapter_version=adapter_version,
            runtime_image="modelmirror-sandbox:wave2-public-v1",
            network_policy=network_policy,
            filesystem_policy="read-only-empty-workspace",
            resource_limits=(
                ("cpu", "1 core / 60 CPU seconds per session"),
                ("memory", "256 MiB per process / 512 MiB sidecar"),
                ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                ("request_timeout", "12 seconds per HTTPS hop"),
                ("operation_timeout", "45 seconds"),
                ("redirects", "maximum 3, revalidated each hop"),
                ("raw_response", "maximum 2 MiB"),
                ("tool_output", "128 KiB"),
            ),
            server_command=(*PUBLIC_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            tool_policies={
                name: CatalogToolPolicy(read_only=True)
                for name in tool_names
            },
            enabled_by_default=True,
            operation_timeout=45.0,
            max_output_bytes=128 * 1024,
        )

    for project_id, (adapter_version, tool_policies) in WAVE_THREE_ADAPTERS.items():
        persistent = project_id == "basic-memory-mcp"
        project_limitations = {
            "basic-memory-mcp": (
                "仅使用本地持久 Markdown 工作区；云路由、促销、遥测、自动更新和语义模型下载全部关闭。",
                "写入、编辑和移动笔记需要一次性确认；删除笔记、项目和 Schema 写入工具不开放。",
            ),
            "excel-mcp-server": (
                "仅处理受控上传的 XLSX、XLS、CSV、TSV 与 JSON；预览最多 1000 行，写入最多 10000 行、200 列。",
                "输入文件始终只读；write_excel 与 update_excel 经确认后只生成新产物，绝不覆盖源文件。",
            ),
            "git-mcp": (
                "仅开放 status、diff、log、show 与 branch 查询；输入仓库只读且完全断网。",
                "固定禁用 add、commit、reset、checkout、分支创建、Hook、external diff、textconv、子模块和 LFS 网络访问。",
            ),
            "markitdown-mcp": (
                "只接受当前受控工作区的文件标识；不接受 http、https、file、data URI 或宿主路径。",
                "转换结果写入可清理 Markdown 产物目录，输入文件始终只读。",
                "本批支持文本、PDF、DOCX、PPTX、XLSX/XLS、CSV/TSV、JSON 与 HTML/XML；图片、音频和网页抓取不开放。",
            ),
        }[project_id]
        accepted = PROJECT_EXTENSIONS.get(project_id)
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=3,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="medium",
            required_capabilities=(
                "scoped-filesystem",
                "artifact-cleanup",
                "path-symlink-protection",
                "mutating-tool-approval",
            ),
            limitations=project_limitations,
            adapter_version=adapter_version,
            runtime_image="modelmirror-mcp-files:wave3-v1",
            network_policy="disabled",
            filesystem_policy=(
                "sealed-input-read-only,persistent-memory-write,artifact-write"
                if persistent
                else "sealed-input-read-only,artifact-write"
            ),
            resource_limits=(
                ("cpu", "1.5 cores / 60 CPU seconds per call"),
                (
                    "memory",
                    "768 MiB address space for lightweight adapters; "
                    "MarkItDown uses the 1 GiB sidecar cgroup because ONNX reserves virtual mappings",
                ),
                ("processes", "maximum 4 sessions / 128 sidecar PIDs"),
                ("operation_timeout", "60 seconds"),
                ("inline_output", "256 KiB"),
                ("workspace", "5000 files / 512 MiB"),
            ),
            server_command=(*FILE_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            tool_policies=tool_policies,
            workspace_policy=CatalogWorkspacePolicy(
                persistent=persistent,
                idle_ttl_seconds=None if persistent else 24 * 60 * 60,
                accepted_extensions=tuple(sorted(accepted or ())),
            ),
            enabled_by_default=True,
            operation_timeout=60.0,
            max_output_bytes=256 * 1024,
        )

    manifests["manim-mcp"] = CatalogAdapterManifest(
        project_id="manim-mcp",
        wave=3,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="critical",
        required_capabilities=(
            "ephemeral-code-sandbox",
            "process-resource-limits",
        ),
        limitations=(
            "上游 Manim MCP 会直接执行用户提供的任意 Python 场景代码，不属于普通文件处理能力。",
            "保留第 3 批目录编号，但连接和运行入口已阻断；等待第 8 批一次性代码执行容器完成后再适配。",
        ),
        adapter_version="blocked:requires-wave8-code-isolation",
        network_policy="blocked:arbitrary-code-execution",
        filesystem_policy="blocked:no-runtime",
    )

    manifests["bibigpt-mcp"] = CatalogAdapterManifest(
        project_id="bibigpt-mcp",
        wave=2,
        availability="blocked",
        connection_kind="remote-mcp",
        risk="medium",
        required_capabilities=(
            "oauth-pkce",
            "oauth-revocation",
            "scope-review",
        ),
        limitations=(
            "上游远程 MCP 当前要求 OAuth 2.1 或 API Key 才能执行工具，不再满足本批无凭据门槛。",
            "已转入第 10 批 OAuth 适配；完成服务端 PKCE、撤销、解绑与最小 scope 前不提供登录或连接入口。",
        ),
        network_policy="blocked:authentication-required",
        filesystem_policy="not-applicable",
    )

    manifests["airbnb-mcp"] = CatalogAdapterManifest(
        project_id="airbnb-mcp",
        wave=2,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="medium",
        required_capabilities=(
            "public-remote-policy",
            "ssrf-protection",
            "schema-drift-recovery",
        ),
        limitations=(
            "Airbnb 0.3.0 当前公开搜索页未返回其固定依赖的数据节点，代表调用触发工具契约漂移阻断。",
            "在上游恢复稳定公开契约并重新通过 robots.txt、代表调用和回归 smoke 前，不提供连接或绕过入口。",
        ),
        adapter_version="0.3.0-blocked-schema-drift",
        network_policy="blocked:upstream-schema-drift",
        filesystem_policy="read-only-empty-workspace",
    )

    for project_id, spec in WAVE_FOUR_ADAPTERS.items():
        limitations = spec.limitations or (
            "仅开放已审计的读取、检索和分析工具；修改、删除、上传、长任务与付费写入能力均不可发现、不可调用。",
            "凭据由服务端加密库按固定槽位注入；客户端不能提交 Token、Header 名、环境变量、命令或 MCP URL。",
        )
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=4,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="medium",
            required_capabilities=(
                "encrypted-credential-binding",
                "credential-revocation-check",
                "fixed-egress-policy",
                "read-only-tool-policy",
                "schema-drift-recovery",
            ),
            limitations=limitations,
            adapter_version=spec.adapter_version,
            runtime_image="modelmirror-mcp-token:wave4-v1",
            network_policy=spec.network_policy,
            filesystem_policy="read-only-empty-workspace",
            resource_limits=(
                ("cpu", "1.5 cores / 60 CPU seconds per call"),
                ("memory", "768 MiB sidecar"),
                ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                ("operation_timeout", "60 seconds"),
                ("tool_output", "256 KiB"),
            ),
            server_command=(*TOKEN_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            allowed_settings=tuple(item.key for item in spec.setting_policies),
            credential_slots=tuple(item.key for item in spec.credential_policies),
            setting_policies=spec.setting_policies,
            credential_policies=spec.credential_policies,
            tool_policies={
                name: CatalogToolPolicy(read_only=True, effect="read")
                for name in spec.tools
            },
            enabled_by_default=True,
            operation_timeout=60.0,
            max_output_bytes=256 * 1024,
        )

    for project_id, spec in WAVE_FIVE_ADAPTERS.items():
        workspace_policy = None
        filesystem_policy = "read-only-empty-workspace"
        if spec.workspace_extensions:
            workspace_policy = CatalogWorkspacePolicy(
                persistent=False,
                accepted_extensions=spec.workspace_extensions,
            )
            filesystem_policy = "sealed-database-input-read-only,no-artifact-write"
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=spec.wave,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="high",
            required_capabilities=(
                "tenant-scoped-credential-binding",
                "structured-database-configuration",
                "native-read-only-mode",
                "query-row-timeout-limits",
                "database-preflight",
                "schema-drift-recovery",
            ),
            limitations=(
                *spec.limitations,
                "当前仅支持部署时固定 tenant/owner 的单租户本地实例；多用户共享部署保持关闭。",
            ),
            adapter_version=spec.adapter_version,
            runtime_image="modelmirror-mcp-database:wave5-v1",
            network_policy=spec.network_policy,
            filesystem_policy=filesystem_policy,
            resource_limits=(
                ("cpu", "1.5 cores / 60 CPU seconds per session process"),
                ("memory", "1 GiB sidecar"),
                ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                ("statement_timeout", "15 seconds"),
                ("operation_timeout", "20 seconds"),
                ("rows", "200 default / 1000 hard maximum"),
                ("tool_output", "256 KiB"),
            ),
            server_command=(*DATABASE_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            allowed_settings=tuple(item.key for item in spec.setting_policies),
            credential_slots=tuple(item.key for item in spec.credential_policies),
            setting_policies=spec.setting_policies,
            credential_policies=spec.credential_policies,
            tool_policies={
                name: CatalogToolPolicy(read_only=True, effect="read")
                for name in spec.tools
            },
            workspace_policy=workspace_policy,
            database_policy=spec.database_policy,
            enabled_by_default=True,
            operation_timeout=20.0,
            max_output_bytes=256 * 1024,
        )

    blocked_wave_five: dict[str, tuple[AdapterRisk, str, tuple[str, ...]]] = {
        "postgres-mcp": (
            "high",
            "0.6.2-blocked:archived-deprecated",
            (
                "官方参考实现已经归档且 npm 包已弃用，因此不能标记为生产可用。",
                "需要 PostgreSQL 时请使用本批受控的 DBHub PostgreSQL 配置；本条目不提供连接按钮。",
            ),
        ),
        "sqlite-mcp": (
            "high",
            "0.6.2-blocked:archived-write-tools",
            (
                "官方实现已归档，并公开 write_query、create_table 等写入工具，缺少持续安全维护。",
                "等待维护中的受控 SQLite 驱动与文件沙箱完成前，连接和宿主路径读取全部关闭。",
            ),
        ),
        "cognee-mcp": (
            "critical",
            "0.5.5-blocked:stateful-memory-runtime",
            (
                "Cognee 依赖 LLM、Embedding、图与向量存储，remember 和 forget 会修改持久状态。",
                "转入第 5B 状态化记忆计划；完成独立持久卷、费用、保留和写入审批前不连接。",
            ),
        ),
        "graphiti-mcp": (
            "critical",
            "mcp-v1.0.2-blocked:experimental-stateful-memory",
            (
                "官方 MCP 仍标为 experimental，并要求 FalkorDB 或 Neo4j 以及 LLM、Embedding。",
                "add_episode、删除和 clear_graph 不属于只读数据库能力，转入第 5B 状态化记忆计划。",
            ),
        ),
        "hindsight-mcp": (
            "critical",
            "0.8.6-blocked:stateful-memory-runtime",
            (
                "Hindsight 是完整记忆服务，包含模型下载、retain、更新、清空和删除等有状态能力。",
                "转入第 5B 状态化记忆计划；预烘焙模型、租户隔离、持久卷和审批完成前不连接。",
            ),
        ),
    }
    for project_id, (risk, adapter_version, limitations) in blocked_wave_five.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=5,
            availability="blocked",
            connection_kind="sandboxed-stdio",
            risk=risk,
            required_capabilities=(
                "maintained-upstream-contract",
                "tenant-isolated-state",
                "native-read-only-mode",
            ),
            limitations=limitations,
            adapter_version=adapter_version,
            network_policy="blocked:no-production-runtime",
            filesystem_policy="blocked:no-runtime",
        )

    for project_id, spec in WAVE_SIX_ADAPTERS.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=6,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="high",
            required_capabilities=(
                "fixed-saas-contract",
                "tenant-owner-scoped-account-binding",
                "remote-resource-approval",
                "idempotent-write-ledger",
                "provider-rate-limits",
                "account-unbinding",
                "schema-drift-recovery",
            ),
            limitations=(
                *spec.limitations,
                "只允许部署时固定 tenant/owner 的单用户本地实例；多用户共享部署保持关闭。",
                "本批只接受用户预先创建的 Token，不提供 OAuth 或外站登录入口。",
            ),
            adapter_version=spec.adapter_version,
            runtime_image="modelmirror-mcp-saas:wave6-v1",
            network_policy="allowlist:" + ",".join(spec.fixed_hosts),
            filesystem_policy="read-only-empty-workspace",
            resource_limits=(
                ("cpu", "1.5 cores / 60 CPU seconds per session process"),
                ("memory", "768 MiB sidecar"),
                ("processes", "maximum 4 sessions / 96 sidecar PIDs"),
                ("operation_timeout", "30 seconds"),
                ("tool_output", "256 KiB"),
            ),
            server_command=(*SAAS_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            allowed_settings=tuple(item.key for item in spec.setting_policies),
            credential_slots=tuple(item.key for item in spec.credential_policies),
            setting_policies=spec.setting_policies,
            credential_policies=spec.credential_policies,
            tool_policies={
                **{
                    name: CatalogToolPolicy(read_only=True, effect="read")
                    for name in spec.read_tools
                },
                **{
                    name: CatalogToolPolicy(
                        read_only=False,
                        requires_approval=True,
                        effect="state-write",
                    )
                    for name in spec.write_tools
                },
            },
            saas_policy=CatalogSaaSPolicy(
                provider=spec.provider,
                fixed_hosts=spec.fixed_hosts,
                tool_schema_sha256=spec.tool_schema_sha256,
                rate_limit_per_minute=spec.rate_limit_per_minute,
            ),
            enabled_by_default=False,
            operation_timeout=30.0,
            max_output_bytes=256 * 1024,
        )

    manifests["mcp-cn-commerce"] = CatalogAdapterManifest(
        project_id="mcp-cn-commerce",
        wave=6,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="high",
        required_capabilities=(
            "per-platform-fixed-contracts",
            "conditional-credential-slots",
            "shop-scope-preflight",
        ),
        limitations=(
            "上游同时覆盖多个平台，各平台凭据、域名和店铺作用域不同；当前不能用单一宽泛契约安全连接。",
            "完成逐平台固定字段、固定域名与独立 smoke 前，不显示凭据、连接或外站登录入口。",
        ),
        adapter_version="blocked:platform-contract-matrix",
        network_policy="blocked:no-fixed-platform-contract",
        filesystem_policy="blocked:no-runtime",
    )
    manifests["mem0-mcp"] = CatalogAdapterManifest(
        project_id="mem0-mcp",
        wave=6,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="high",
        required_capabilities=(
            "tenant-isolated-memory-namespace",
            "state-write-approval",
            "retention-and-unbind-policy",
        ),
        limitations=(
            "长期记忆涉及持久状态、用户命名空间与删除语义；当前账户/保留策略尚未完成生产核验。",
            "完成命名空间隔离、写入幂等和数据清理验收前，不提供连接或写入入口。",
        ),
        adapter_version="blocked:stateful-memory-policy",
        network_policy="blocked:no-production-runtime",
        filesystem_policy="blocked:no-runtime",
    )

    for project_id, spec in WAVE_SEVEN_ADAPTERS.items():
        browser_policy = CatalogBrowserPolicy(
            engine=spec.engine,
            contract_version=BROWSER_CONTRACT_VERSION,
            tool_schema_sha256=spec.tool_schema_sha256,
        )
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=7,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="high",
            required_capabilities=(
                "ephemeral-browser",
                "browser-domain-policy",
                "browser-session-approval",
                "browser-artifact-cleanup",
            ),
            limitations=(
                *browser_policy.limitations,
                "所有页面状态修改都冻结参数并一次性确认；超时或连接中断按结果未知处理并销毁会话。",
                "单 Origin 锁定覆盖可信上游的正常 Chromium 与恶意网页流量；若浏览器或上游进程本身完全 RCE，独立出口仍拒绝私网与 metadata 并限流，但不保证同公网 IP/证书下的虚拟主机精确隔离；本批不做 TLS MITM 或每会话容器。",
                "浏览器与出口使用一次性配对密钥；任一服务单独重启后不复用旧会话并保持失败关闭，需成对重启恢复；共享 staging 临时卷硬限制为 64 MiB。",
                "Landlock 对 /proc 恢复 WRITE_FILE 类别供 Chromium user namespace 映射；这不是路径白名单，实际写入仍受非 root、无 capabilities、Docker procfs 掩蔽/只读挂载与 seccomp 约束。",
            ),
            adapter_version=spec.adapter_version,
            runtime_image="modelmirror-mcp-browser:wave7-v1",
            network_policy=(
                "public-http-https-only,dns-pinning,redirect-revalidation,"
                "private-metadata-blocked"
            ),
            filesystem_policy=(
                "read-only-root,ephemeral-profile,server-generated-artifacts-only,"
                "landlock-proc-write-file-with-docker-procfs-guards"
            ),
            resource_limits=(
                ("browser_cpu", "1.5 cores"),
                ("browser_memory", "1 GiB"),
                ("browser_processes", "maximum 1 session / 256 PIDs"),
                ("egress_cpu", "0.5 cores"),
                ("egress_memory", "256 MiB"),
                ("egress_processes", "64 PIDs"),
                ("pages", "1 page per session"),
                ("session_ttl", "15 minutes / 5 minutes idle"),
                ("actions", "50 per session"),
                ("navigation_timeout", "20 seconds"),
                ("operation_timeout", "30 seconds"),
                ("tool_output", "256 KiB"),
                ("screenshot", "32 MiB"),
            ),
            server_command=(*BROWSER_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            tool_policies={
                **{
                    name: CatalogToolPolicy(read_only=True, effect="read")
                    for name in spec.read_tools
                },
                **{
                    name: CatalogToolPolicy(
                        read_only=False,
                        effect="artifact-create",
                    )
                    for name in spec.artifact_tools
                },
                **{
                    name: CatalogToolPolicy(
                        read_only=False,
                        requires_approval=True,
                        effect="state-write",
                    )
                    for name in spec.state_write_tools
                },
            },
            browser_policy=browser_policy,
            enabled_by_default=False,
            operation_timeout=30.0,
            max_output_bytes=256 * 1024,
        )

    manifests["puppeteer-mcp"] = CatalogAdapterManifest(
        project_id="puppeteer-mcp",
        wave=7,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="critical",
        required_capabilities=(
            "maintained-upstream-contract",
            "ephemeral-browser",
            "browser-domain-policy",
        ),
        limitations=(
            "官方仓库已经归档，现有实现允许危险启动参数、任意脚本求值和宽泛浏览器控制，无法满足持续安全维护门槛。",
            "本条目保留目录与第 7 批编号，但不显示安装、连接、上传、登录或外部 CDP 入口。",
        ),
        adapter_version="blocked:archived-dangerous-runtime",
        network_policy="blocked:unmaintained-browser-runtime",
        filesystem_policy="blocked:no-runtime",
    )
    manifests["selenium-mcp"] = CatalogAdapterManifest(
        project_id="selenium-mcp",
        wave=7,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="critical",
        required_capabilities=(
            "license-contract-resolution",
            "maintained-upstream-contract",
            "ephemeral-browser",
        ),
        limitations=(
            "上游 v0.2.3 的仓库与包许可证声明冲突，并暴露任意 Chrome 参数、脚本执行、Cookie 和宿主路径能力。",
            "完成许可证核验、受控驱动封装和进程清理契约前，连接、Grid、VNC 和本机桥接入口全部关闭。",
        ),
        adapter_version="0.2.3-blocked:license-and-runtime-contract",
        network_policy="blocked:no-production-runtime",
        filesystem_policy="blocked:no-runtime",
    )

    manifests["mcp-run-python"] = CatalogAdapterManifest(
        project_id="mcp-run-python",
        wave=8,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="critical",
        required_capabilities=(
            "maintained-safe-execution-runtime",
            "ephemeral-code-sandbox",
            "process-resource-limits",
        ),
        limitations=(
            "上游 0.0.22 已由维护方归档；维护方明确说明 Pyodide 中的代码可执行任意 JavaScript、污染后续调用、访问运行时文件并耗尽宿主内存，因此不再把该实现视为不可信代码沙箱。",
            "连接、依赖安装、Deno/Pyodide 运行时和代码提交入口全部关闭；不会用实验性的 Monty 或模镜自研执行器冒充该上游适配器。",
        ),
        adapter_version="0.0.22-blocked:retired-unsafe-runtime",
        network_policy="blocked:no-production-runtime",
        filesystem_policy="blocked:no-runtime",
    )
    manifests["python-interpreter"] = CatalogAdapterManifest(
        project_id="python-interpreter",
        wave=8,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="critical",
        required_capabilities=(
            "complete-license-provenance",
            "ephemeral-code-sandbox",
            "fixed-subprocess-only-contract",
            "process-resource-limits",
        ),
        limitations=(
            "PyPI 1.2.3 默认以 inline 模式在 MCP Server 进程内执行代码并保留全局会话，同时开放任意 pip 安装、文件读写、环境选择和最长 300 秒子进程执行，不能作为受控代码沙箱直接部署。",
            "发布 wheel 虽声明 MIT classifier，但所携 LICENSE 文件为空；在许可证正文、固定 subprocess-only 契约与一次性容器边界全部核验前，不提供安装、连接、文件或执行入口。",
        ),
        adapter_version="1.2.3-blocked:unsafe-contract-and-empty-license",
        network_policy="blocked:no-production-runtime",
        filesystem_policy="blocked:no-runtime",
    )

    for project_id, spec in WAVE_NINE_READY_ADAPTERS.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=9,
            availability="ready",
            connection_kind="sandboxed-stdio",
            risk="medium",
            required_capabilities=(
                "fixed-egress-policy",
                "read-only-tool-policy",
                "schema-drift-recovery",
                "cost-guardrails",
                "process-resource-limits",
            ),
            limitations=spec.limitations,
            adapter_version=spec.adapter_version,
            runtime_image="modelmirror-mcp-registry:wave9-v1",
            network_policy=spec.network_policy,
            filesystem_policy="read-only-empty-workspace",
            resource_limits=(
                ("cpu", "1 core / 60 CPU seconds per call"),
                ("memory", "512 MiB sidecar"),
                ("processes", "maximum 4 sessions / 64 sidecar PIDs"),
                ("operation_timeout", "60 seconds"),
                ("tool_output", "256 KiB"),
                ("provider_cost", "anonymous public Registry reads only"),
            ),
            server_command=(*TOKEN_SANDBOX_PROXY, project_id),
            preparation_kind="bundled",
            tool_policies={
                name: CatalogToolPolicy(read_only=True, effect="read")
                for name in spec.tools
            },
            enabled_by_default=True,
            operation_timeout=60.0,
            max_output_bytes=256 * 1024,
        )

    for project_id, (version, limitations) in WAVE_NINE_BLOCKED_ADAPTERS.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=9,
            availability="blocked",
            connection_kind="sandboxed-stdio",
            risk="critical",
            required_capabilities=(
                "cost-guardrails",
                "resource-preview",
                "terminal-action-approval",
                "maintained-upstream-contract",
            ),
            limitations=limitations,
            adapter_version=version,
            network_policy="blocked:no-production-runtime",
            filesystem_policy="blocked:no-runtime",
        )

    for project_id, (version, limitations) in WAVE_ELEVEN_BLOCKED_ADAPTERS.items():
        manifests[project_id] = CatalogAdapterManifest(
            project_id=project_id,
            wave=11,
            availability="blocked",
            connection_kind="desktop-bridge",
            risk="critical",
            required_capabilities=(
                "versioned-desktop-bridge",
                "host-instance-attestation",
                "session-owner-binding",
                "per-app-consent",
                "terminal-action-approval",
                "bridge-revocation",
            ),
            limitations=limitations,
            adapter_version=version,
            network_policy="blocked:no-trusted-host-bridge",
            filesystem_policy="blocked:no-host-grant",
        )

    manifests["snyk-mcp"] = CatalogAdapterManifest(
        project_id="snyk-mcp",
        wave=4,
        availability="blocked",
        connection_kind="sandboxed-stdio",
        risk="critical",
        required_capabilities=(
            "ephemeral-code-sandbox",
            "scoped-filesystem",
            "terminal-action-approval",
        ),
        limitations=(
            "Snyk MCP 随 Snyk CLI 读取本地项目，并可能启动 Gradle、Maven 等构建链；这已跨越本批 Token 只读远程检索边界。",
            "保留第 4 批目录编号，但连接、安装和外站登录入口全部关闭；等待第 8 批一次性代码执行隔离与文件授权能力完成后再适配。",
        ),
        adapter_version="1.15.2-blocked:requires-wave8-code-isolation",
        network_policy="blocked:local-build-execution",
        filesystem_policy="blocked:no-runtime",
    )

    for wave, project_ids in WAVE_PROJECTS.items():
        connection_kind, risk, capabilities, limitations = WAVE_METADATA[wave]
        for project_id in project_ids:
            if project_id in manifests:
                continue
            manifests[project_id] = CatalogAdapterManifest(
                project_id=project_id,
                wave=wave,
                availability="planned",
                connection_kind=connection_kind,
                risk=risk,
                required_capabilities=capabilities,
                limitations=limitations,
            )

    for adapter in CATALOG_EXPANSION_V2_ADAPTERS:
        if adapter.project_id in manifests:
            raise RuntimeError(
                f"duplicate approved catalog expansion id: {adapter.project_id}"
            )
        if adapter.availability == "ready":
            database_spec = WAVE_NINETEEN_DATABASE_ADAPTERS.get(adapter.project_id)
            if database_spec is not None:
                manifests[adapter.project_id] = CatalogAdapterManifest(
                    project_id=adapter.project_id,
                    wave=adapter.adaptation_wave,
                    availability="ready",
                    connection_kind="sandboxed-stdio",
                    risk="high",
                    required_capabilities=(
                        "tenant-scoped-credential-binding",
                        "structured-database-configuration",
                        "native-read-only-mode",
                        "query-row-timeout-limits",
                        "database-preflight",
                        "schema-drift-recovery",
                    ),
                    limitations=(
                        *database_spec.limitations,
                        "当前仅支持部署时固定 tenant/owner 的单租户本地实例；多用户共享部署保持关闭。",
                    ),
                    adapter_version=database_spec.adapter_version,
                    runtime_image="modelmirror-mcp-database:wave5-v1",
                    network_policy=database_spec.network_policy,
                    filesystem_policy="read-only-empty-workspace",
                    resource_limits=(
                        ("cpu", "1.5 cores / 60 CPU seconds per session process"),
                        ("memory", "1 GiB sidecar"),
                        ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                        ("statement_timeout", "15 seconds"),
                        ("operation_timeout", "20 seconds"),
                        ("rows", "200 default / 1000 hard maximum"),
                        ("tool_output", "256 KiB"),
                    ),
                    server_command=(*DATABASE_SANDBOX_PROXY, adapter.project_id),
                    preparation_kind="bundled",
                    allowed_settings=tuple(
                        item.key for item in database_spec.setting_policies
                    ),
                    credential_slots=tuple(
                        item.key for item in database_spec.credential_policies
                    ),
                    setting_policies=database_spec.setting_policies,
                    credential_policies=database_spec.credential_policies,
                    tool_policies={
                        name: CatalogToolPolicy(read_only=True, effect="read")
                        for name in database_spec.tools
                    },
                    database_policy=database_spec.database_policy,
                    enabled_by_default=True,
                    operation_timeout=20.0,
                    max_output_bytes=256 * 1024,
                )
                continue
            code_index_spec = WAVE_TWENTY_FILE_ADAPTERS.get(adapter.project_id)
            if code_index_spec is not None:
                adapter_version, tool_policies, limitations = code_index_spec
                accepted = PROJECT_EXTENSIONS.get(adapter.project_id)
                manifests[adapter.project_id] = CatalogAdapterManifest(
                    project_id=adapter.project_id,
                    wave=adapter.adaptation_wave,
                    availability="ready",
                    connection_kind="sandboxed-stdio",
                    risk="medium",
                    required_capabilities=(
                        "scoped-filesystem",
                        "ephemeral-code-index",
                        "resource-limits",
                        "one-shot-write-approval",
                    ),
                    limitations=limitations,
                    adapter_version=adapter_version,
                    runtime_image="modelmirror-mcp-files:wave3-v1",
                    network_policy="disabled",
                    filesystem_policy="sealed-input-read-only,ephemeral-index",
                    resource_limits=(
                        ("cpu", "1.5 cores / 60 CPU seconds per session process"),
                        ("memory", "1 GiB sidecar cgroup"),
                        ("processes", "maximum 4 sessions / 128 sidecar PIDs"),
                        ("index_timeout", "55 seconds"),
                        ("query_timeout", "12 seconds"),
                        ("tool_output", "240 KiB"),
                        ("workspace", "5000 files / 512 MiB"),
                    ),
                    server_command=(*FILE_SANDBOX_PROXY, adapter.project_id),
                    preparation_kind="bundled",
                    tool_policies=tool_policies,
                    workspace_policy=CatalogWorkspacePolicy(
                        persistent=False,
                        idle_ttl_seconds=24 * 60 * 60,
                        accepted_extensions=tuple(sorted(accepted or ())),
                    ),
                    enabled_by_default=True,
                    operation_timeout=60.0,
                    max_output_bytes=240 * 1024,
                )
                continue
            file_spec = WAVE_EIGHTEEN_FILE_ADAPTERS.get(adapter.project_id)
            if file_spec is not None:
                adapter_version, tool_policies, limitations = file_spec
                accepted = PROJECT_EXTENSIONS.get(adapter.project_id)
                manifests[adapter.project_id] = CatalogAdapterManifest(
                    project_id=adapter.project_id,
                    wave=adapter.adaptation_wave,
                    availability="ready",
                    connection_kind="sandboxed-stdio",
                    risk="medium",
                    required_capabilities=(
                        "scoped-filesystem",
                        "artifact-cleanup",
                        "path-symlink-protection",
                        "deterministic-artifact-generation",
                    ),
                    limitations=limitations,
                    adapter_version=adapter_version,
                    runtime_image="modelmirror-mcp-files:wave3-v1",
                    network_policy="disabled",
                    filesystem_policy="sealed-input-read-only,artifact-write",
                    resource_limits=(
                        ("cpu", "1.5 cores / 60 CPU seconds per call"),
                        ("memory", "1 GiB sidecar cgroup"),
                        ("processes", "maximum 4 sessions / 128 sidecar PIDs"),
                        ("operation_timeout", "60 seconds"),
                        ("inline_output", "256 KiB"),
                        ("workspace", "5000 files / 512 MiB"),
                    ),
                    server_command=(*FILE_SANDBOX_PROXY, adapter.project_id),
                    preparation_kind="bundled",
                    tool_policies=tool_policies,
                    workspace_policy=CatalogWorkspacePolicy(
                        persistent=False,
                        idle_ttl_seconds=24 * 60 * 60,
                        accepted_extensions=tuple(sorted(accepted or ())),
                    ),
                    enabled_by_default=True,
                    operation_timeout=60.0,
                    max_output_bytes=256 * 1024,
                )
                continue
            public_spec = WAVE_SIXTEEN_PUBLIC_ADAPTERS.get(adapter.project_id)
            if public_spec is not None:
                adapter_version, tool_names, public_policy, limitations = public_spec
                manifests[adapter.project_id] = CatalogAdapterManifest(
                    project_id=adapter.project_id,
                    wave=adapter.adaptation_wave,
                    availability="ready",
                    connection_kind="sandboxed-stdio",
                    risk="medium",
                    required_capabilities=adapter.required_capabilities,
                    limitations=limitations,
                    adapter_version=adapter_version,
                    runtime_image="modelmirror-mcp-public:wave17a-v1",
                    network_policy="allowlist:" + ",".join(public_policy.fixed_hosts),
                    filesystem_policy="read-only-empty-workspace",
                    resource_limits=(
                        ("cpu", "1 core / 60 CPU seconds per session process"),
                        ("memory", "512 MiB sidecar"),
                        ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                        ("request_timeout", "20 seconds per HTTPS request"),
                        ("operation_timeout", "30 seconds"),
                        ("raw_response", "maximum 1 MiB"),
                        ("tool_output", "128 KiB"),
                    ),
                    server_command=(*PUBLIC_SANDBOX_PROXY, adapter.project_id),
                    preparation_kind="bundled",
                    tool_policies={
                        name: CatalogToolPolicy(read_only=True, effect="read")
                        for name in tool_names
                    },
                    public_policy=public_policy,
                    enabled_by_default=False,
                    operation_timeout=30.0,
                    max_output_bytes=128 * 1024,
                )
                continue
            spec = (
                WAVE_THIRTEEN_TOKEN_ADAPTERS
                | WAVE_FOURTEEN_TOKEN_ADAPTERS
                | WAVE_FIFTEEN_TOKEN_ADAPTERS
            ).get(adapter.project_id)
            if spec is None:
                raise RuntimeError(
                    f"ready catalog expansion lacks runtime contract: {adapter.project_id}"
                )
            manifests[adapter.project_id] = CatalogAdapterManifest(
                project_id=adapter.project_id,
                wave=adapter.adaptation_wave,
                availability="ready",
                connection_kind="sandboxed-stdio",
                risk="medium",
                required_capabilities=adapter.required_capabilities,
                limitations=spec.limitations,
                adapter_version=spec.adapter_version,
                runtime_image="modelmirror-mcp-token:wave4-v1",
                network_policy=spec.network_policy,
                filesystem_policy="read-only-empty-workspace",
                resource_limits=(
                    ("cpu", "1.5 cores / 60 CPU seconds per call"),
                    ("memory", "768 MiB sidecar"),
                    ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                    ("operation_timeout", "60 seconds"),
                    ("tool_output", "256 KiB"),
                ),
                server_command=(*TOKEN_SANDBOX_PROXY, adapter.project_id),
                preparation_kind="bundled",
                credential_slots=tuple(item.key for item in spec.credential_policies),
                credential_policies=spec.credential_policies,
                tool_policies={
                    name: CatalogToolPolicy(read_only=True, effect="read")
                    for name in spec.tools
                },
                enabled_by_default=True,
                operation_timeout=60.0,
                max_output_bytes=256 * 1024,
            )
            continue
        if adapter.project_id in (
            WAVE_THIRTEEN_TOKEN_ADAPTERS
            | WAVE_FOURTEEN_TOKEN_ADAPTERS
            | WAVE_FIFTEEN_TOKEN_ADAPTERS
            | WAVE_SIXTEEN_PUBLIC_ADAPTERS
            | WAVE_EIGHTEEN_FILE_ADAPTERS
            | WAVE_NINETEEN_DATABASE_ADAPTERS
            | WAVE_TWENTY_FILE_ADAPTERS
        ):
            raise RuntimeError(
                f"non-ready catalog expansion has runtime contract: {adapter.project_id}"
            )
        if adapter.availability not in {"planned", "blocked"}:
            raise RuntimeError(
                f"invalid catalog expansion availability: {adapter.availability}"
            )
        manifests[adapter.project_id] = CatalogAdapterManifest(
            project_id=adapter.project_id,
            wave=adapter.adaptation_wave,
            availability=adapter.availability,  # type: ignore[arg-type]
            connection_kind=adapter.connection_kind,  # type: ignore[arg-type]
            risk=adapter.risk,  # type: ignore[arg-type]
            required_capabilities=adapter.required_capabilities,
            limitations=adapter.limitations,
            adapter_version=adapter.adapter_version,
            network_policy=f"{adapter.availability}:{adapter.decision_reason_code}",
            filesystem_policy=f"{adapter.availability}:no-runtime",
        )

    for adapter in CATALOG_EXPANSION_V3_ADAPTERS:
        if adapter.project_id in manifests:
            raise RuntimeError(
                f"duplicate Wave 24 catalog expansion id: {adapter.project_id}"
            )
        if adapter.availability == "ready":
            public_spec = WAVE_SIXTEEN_PUBLIC_ADAPTERS.get(adapter.project_id)
            if public_spec is None:
                raise RuntimeError(
                    f"ready Wave 24 expansion lacks runtime contract: {adapter.project_id}"
                )
            adapter_version, tool_names, public_policy, limitations = public_spec
            manifests[adapter.project_id] = CatalogAdapterManifest(
                project_id=adapter.project_id,
                wave=adapter.adaptation_wave,
                availability="ready",
                connection_kind="sandboxed-stdio",
                risk="medium",
                required_capabilities=adapter.required_capabilities,
                limitations=limitations,
                adapter_version=adapter_version,
                runtime_image="modelmirror-mcp-public:wave17a-v1",
                network_policy="allowlist:" + ",".join(public_policy.fixed_hosts),
                filesystem_policy="read-only-empty-workspace",
                resource_limits=(
                    ("cpu", "1 core / 60 CPU seconds per session process"),
                    ("memory", "512 MiB sidecar"),
                    ("processes", "maximum 6 sessions / 128 sidecar PIDs"),
                    ("request_timeout", "20 seconds per HTTPS request"),
                    ("operation_timeout", "30 seconds"),
                    ("raw_response", "maximum 1 MiB"),
                    ("tool_output", "128 KiB"),
                ),
                server_command=(*PUBLIC_SANDBOX_PROXY, adapter.project_id),
                preparation_kind="bundled",
                tool_policies={
                    name: CatalogToolPolicy(read_only=True, effect="read")
                    for name in tool_names
                },
                public_policy=public_policy,
                enabled_by_default=False,
                operation_timeout=30.0,
                max_output_bytes=128 * 1024,
            )
            continue
        if adapter.project_id in WAVE_SIXTEEN_PUBLIC_ADAPTERS:
            raise RuntimeError(
                f"non-ready Wave 24 expansion has runtime contract: {adapter.project_id}"
            )
        if adapter.availability not in {"planned", "blocked"}:
            raise RuntimeError(
                f"invalid Wave 24 catalog availability: {adapter.project_id}"
            )
        manifests[adapter.project_id] = CatalogAdapterManifest(
            project_id=adapter.project_id,
            wave=adapter.adaptation_wave,
            availability=adapter.availability,  # type: ignore[arg-type]
            connection_kind=adapter.connection_kind,  # type: ignore[arg-type]
            risk=adapter.risk,  # type: ignore[arg-type]
            required_capabilities=adapter.required_capabilities,
            limitations=adapter.limitations,
            network_policy=f"{adapter.availability}:{adapter.decision_reason_code}",
            filesystem_policy=f"{adapter.availability}:no-runtime",
        )

    if len(manifests) != 300:
        raise RuntimeError(f"MCP catalog must contain 300 adapters, got {len(manifests)}")
    return manifests


CATALOG_ADAPTERS = build_catalog_manifests()


class InstallerProtocol(Protocol):
    def install(
        self,
        *,
        project_id: str,
        install_command: str,
        server_command: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def get_installed(self, project_id: str) -> dict[str, Any] | None: ...


class CatalogConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, str | int | float | bool] = Field(default_factory=dict)
    credential_bindings: dict[str, str] = Field(default_factory=dict)
    workspace_id: str | None = None


class CatalogCredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=20_000)


class CatalogToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)


class CatalogWorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(default="", max_length=120)


class CatalogUnbindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revoke_credentials: bool = False


@dataclass(frozen=True, slots=True)
class CatalogConfigurationSnapshot:
    revision: str
    digest: str


@dataclass(frozen=True, slots=True)
class CatalogAccountSnapshot:
    digest: str
    tool_schema_sha256: str
    verified_at: float


@dataclass(frozen=True, slots=True)
class CatalogBrowserSnapshot:
    status: Literal["active", "tainted", "disconnected"]
    generation: str
    page_revision: int
    page_digest: str
    current_origin: str
    action_count: int
    max_actions: int
    expires_at: float
    approved_hosts: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class CatalogExecutionLedgerEntry:
    approval_id: str
    tenant_id: str
    owner_id: str
    project_id: str
    idempotency_key: str
    state: Literal["started", "completed", "unknown", "rejected"]
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CatalogBrowserArtifact:
    artifact_id: str
    tenant_id: str
    owner_id: str
    project_id: str
    session_id: str
    browser_generation: str
    relative_path: str
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: float
    expires_at: float


@dataclass(slots=True)
class CatalogApproval:
    approval_id: str
    tenant_id: str
    owner_id: str
    project_id: str
    session_id: str
    context_kind: CatalogApprovalContextKind
    workspace_id: str
    workspace_manifest_sha256: str
    tool_name: str
    arguments: dict[str, Any]
    argument_digest: str
    summary: str
    expires_at: float
    configuration_revision: str = ""
    configuration_digest: str = ""
    credential_snapshot_digest: str = ""
    account_snapshot_digest: str = ""
    browser_snapshot_digest: str = ""
    tool_schema_sha256: str = ""
    tool_policy_digest: str = ""
    idempotency_key: str = ""
    target_preview: dict[str, Any] = field(default_factory=dict)
    used: bool = False


class MCPCatalogService:
    """Operate only server-owned adapters from the frozen catalog."""

    _RESERVED_CONFIGURATION_KEYS = {
        "command",
        "server_command",
        "url",
        "uri",
        "endpoint",
        "dsn",
        "connection_string",
        "connection_uri",
        "headers",
        "environment",
        "password",
        "token",
        "api_key",
        "certificate_path",
        "ssl_cert",
        "cwd",
        "working_directory",
    }

    def __init__(
        self,
        manager: MCPClientManager,
        installer: InstallerProtocol,
        registry: ToolRegistry,
        *,
        manifests: dict[str, CatalogAdapterManifest] | None = None,
        credential_validator: Callable[[str], Any] | None = None,
        credential_resolver: Callable[[str], str] | None = None,
        credential_lister: Callable[[], list[Any]] | None = None,
        credential_creator: Callable[..., tuple[Any, str]] | None = None,
        credential_revoker: Callable[[str], Any] | None = None,
        workspace_store: MCPCatalogWorkspaceStore | None = None,
        browser_artifact_root: Path | None = None,
        browser_artifact_staging_root: Path | None = None,
        browser_artifact_index_path: Path | None = None,
        tenant_id: str = "local",
        owner_id: str = "local",
    ) -> None:
        self.manager = manager
        self.installer = installer
        self.registry = registry
        self.manifests = dict(manifests or CATALOG_ADAPTERS)
        self.credential_validator = credential_validator
        self.credential_resolver = credential_resolver
        self.credential_lister = credential_lister
        self.credential_creator = credential_creator
        self.credential_revoker = credential_revoker
        self.workspace_store = workspace_store
        catalog_storage_root = Path(
            os.getenv(
                "MCP_CATALOG_STORAGE_DIR",
                str(Path(__file__).resolve().parent / "storage"),
            )
        )
        configured_browser_artifact_root = browser_artifact_root or Path(
            os.getenv(
                "MCP_BROWSER_TRUSTED_ARTIFACT_ROOT",
                str(catalog_storage_root / "browser-artifacts"),
            )
        )
        self.browser_artifact_root = configured_browser_artifact_root.resolve(
            strict=False
        )
        configured_browser_staging_root = browser_artifact_staging_root or Path(
            os.getenv(
                "MCP_BROWSER_ARTIFACT_STAGING_ROOT",
                str(Path(__file__).resolve().parent / "browser-artifact-staging"),
            )
        )
        self.browser_artifact_staging_root = (
            configured_browser_staging_root.resolve(strict=False)
        )
        configured_browser_index = browser_artifact_index_path or (
            self.browser_artifact_root / "index.json"
        )
        self.browser_artifact_index_path = configured_browser_index.resolve(
            strict=False
        )
        self.tenant_id = str(tenant_id or "local")
        self.owner_id = str(owner_id or "local")
        self._sessions: dict[tuple[str, str, str], str] = {}
        self._configurations: dict[
            tuple[str, str, str], CatalogConfigurationRequest
        ] = {}
        self._credential_snapshots: dict[
            tuple[str, str, str], dict[str, tuple[str, float]]
        ] = {}
        self._configuration_snapshots: dict[
            tuple[str, str, str], CatalogConfigurationSnapshot
        ] = {}
        self._account_snapshots: dict[
            tuple[str, str, str], CatalogAccountSnapshot
        ] = {}
        self._browser_snapshots: dict[
            tuple[str, str, str], CatalogBrowserSnapshot
        ] = {}
        self._browser_elements: dict[
            tuple[str, str, str], dict[str, dict[str, str]]
        ] = {}
        self._browser_disconnect_reasons: dict[
            tuple[str, str, str], str
        ] = {}
        self._browser_artifacts: dict[
            tuple[str, str, str], CatalogBrowserArtifact
        ] = {}
        self._load_browser_artifact_index()
        self._credential_verification: dict[tuple[str, str, str], str] = {}
        self._preflight_status: dict[tuple[str, str, str], str] = {}
        self._approvals: dict[tuple[str, str, str], CatalogApproval] = {}
        self._execution_ledger: dict[
            tuple[str, str, str], CatalogExecutionLedgerEntry
        ] = {}
        self._approval_locks: dict[
            tuple[str, str, str], asyncio.Lock
        ] = {}
        self._call_locks: dict[
            tuple[str, str, str], asyncio.Lock
        ] = {}
        self._connecting_scopes: set[tuple[str, str, str]] = set()
        self._unbinding_scopes: set[tuple[str, str, str]] = set()
        self._lock = asyncio.Lock()

    def _scope_key(self, project_id: str) -> tuple[str, str, str]:
        return (
            self.tenant_id,
            self.owner_id,
            str(project_id or "").strip(),
        )

    def _approval_key(self, approval_id: str) -> tuple[str, str, str]:
        return (
            self.tenant_id,
            self.owner_id,
            str(approval_id or "").strip(),
        )

    def _artifact_key(self, artifact_id: str) -> tuple[str, str, str]:
        return (
            self.tenant_id,
            self.owner_id,
            str(artifact_id or "").strip(),
        )

    def _session_owner(self, project_id: str) -> str:
        """Bind catalog sessions before they become visible to generic routes."""

        return ":".join(
            (
                "catalog",
                self.tenant_id,
                self.owner_id,
                str(project_id or "").strip(),
            )
        )

    def _credential_call(
        self,
        callback: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Call tenant-aware vaults while keeping old injected test doubles valid."""

        scoped = {"tenant_id": self.tenant_id, "owner_id": self.owner_id, **kwargs}
        try:
            parameters = inspect.signature(callback).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_keywords = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_keywords or "tenant_id" in parameters or "owner_id" in parameters:
            return callback(*args, **scoped)
        return callback(*args, **kwargs)

    def get_manifest(self, project_id: str) -> CatalogAdapterManifest:
        manifest = self.manifests.get(str(project_id or "").strip())
        if manifest is None:
            raise CatalogAdapterNotFoundError(
                f"MCP 目录中不存在项目：{project_id}"
            )
        return manifest

    def list_adapters(self) -> dict[str, Any]:
        adapters: list[dict[str, Any]] = []
        for project_id, manifest in sorted(self.manifests.items()):
            scope_key = self._scope_key(project_id)
            item = manifest.to_public(
                connected=scope_key in self._sessions,
                session_id=self._sessions.get(scope_key),
            )
            configuration = self._configurations.get(scope_key)
            item["configured"] = configuration is not None
            item["configured_settings"] = (
                sorted(configuration.settings) if configuration else []
            )
            item["configured_credential_slots"] = (
                sorted(configuration.credential_bindings) if configuration else []
            )
            item["configuration_values"] = (
                dict(configuration.settings) if configuration else {}
            )
            item["credential_bindings"] = (
                dict(configuration.credential_bindings) if configuration else {}
            )
            item["workspace_id"] = (
                configuration.workspace_id if configuration else None
            )
            if manifest.saas_policy is None:
                item["account_status"] = "not-applicable"
            elif manifest.availability == "blocked":
                item["account_status"] = "blocked"
            elif configuration is None:
                item["account_status"] = "unbound"
            elif scope_key in self._account_snapshots:
                item["account_status"] = "verified"
            else:
                item["account_status"] = "unverified"
            if not manifest.credential_policies:
                item["credential_verification"] = "not-required"
            elif configuration is None:
                item["credential_verification"] = "missing"
            else:
                item["credential_verification"] = self._credential_verification.get(
                    scope_key,
                    "unverified",
                )
            browser_snapshot = self._browser_snapshots.get(scope_key)
            item["browser_session_status"] = (
                browser_snapshot.status
                if manifest.browser_policy is not None and browser_snapshot is not None
                else (
                    "disconnected"
                    if manifest.browser_policy is not None
                    else "not-applicable"
                )
            )
            if (
                manifest.database_policy is None
                and manifest.saas_policy is None
                and manifest.browser_policy is None
            ):
                item["preflight_status"] = "not-applicable"
            elif manifest.availability == "blocked":
                item["preflight_status"] = "blocked"
            elif manifest.browser_policy is not None:
                item["preflight_status"] = self._preflight_status.get(
                    scope_key,
                    "unverified",
                )
            elif configuration is None:
                item["preflight_status"] = (
                    "awaiting-workspace"
                    if manifest.workspace_policy is not None
                    else "awaiting-configuration"
                )
            else:
                item["preflight_status"] = self._preflight_status.get(
                    scope_key,
                    "unverified",
                )
            adapters.append(item)
        return {
            "total": len(adapters),
            "ready": sum(item["availability"] == "ready" for item in adapters),
            "planned": sum(item["availability"] == "planned" for item in adapters),
            "adapting": sum(item["availability"] == "adapting" for item in adapters),
            "blocked": sum(item["availability"] == "blocked" for item in adapters),
            "adapters": adapters,
        }

    async def prepare(self, project_id: str) -> dict[str, Any]:
        manifest = self._require_executable(project_id)
        started_at = time.monotonic()
        if manifest.preparation_kind == "bundled":
            payload = {
                "project_id": manifest.project_id,
                "prepared": True,
                "message": "内置隔离适配器已随沙箱镜像准备完成。",
                "metadata": {
                    "project_id": manifest.project_id,
                    "adapter_version": manifest.adapter_version,
                    "runtime_image": manifest.runtime_image,
                },
            }
            logger.info(
                "MCP catalog prepare project=%s prepared=true duration_ms=%d",
                manifest.project_id,
                int((time.monotonic() - started_at) * 1000),
            )
            return payload
        existing = self.installer.get_installed(manifest.project_id)
        if existing is not None:
            return {
                "project_id": manifest.project_id,
                "prepared": True,
                "message": "MCP 适配器已经准备完成。",
                "metadata": self._public_install_metadata(existing),
            }
        result = await asyncio.to_thread(
            self.installer.install,
            project_id=manifest.project_id,
            install_command=manifest.install_command,
            server_command=list(manifest.server_command),
        )
        payload = {
            "project_id": manifest.project_id,
            "prepared": bool(result.get("installed")),
            "message": str(result.get("message") or "MCP 适配器已准备。"),
            "metadata": self._public_install_metadata(result.get("metadata")),
        }
        logger.info(
            "MCP catalog prepare project=%s prepared=%s duration_ms=%d",
            manifest.project_id,
            payload["prepared"],
            int((time.monotonic() - started_at) * 1000),
        )
        return payload

    def configure(
        self,
        project_id: str,
        request: CatalogConfigurationRequest,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        if manifest.availability not in {"adapting", "ready"}:
            raise CatalogAdapterUnavailableError(
                "该 MCP 仍处于待适配状态，当前不能提交配置。"
            )

        scope_key = self._scope_key(manifest.project_id)
        if scope_key in self._unbinding_scopes:
            raise CatalogAdapterPolicyError("账号正在解绑，请等待操作完成。")
        if scope_key in self._connecting_scopes:
            raise CatalogAdapterPolicyError("连接正在建立，请等待完成后再更新目录配置。")
        if (
            manifest.credential_policies or manifest.database_policy is not None
        ) and scope_key in self._sessions:
            raise CatalogAdapterPolicyError("请先断开当前会话，再更新目录配置。")

        supplied_setting_keys = set(request.settings)
        supplied_credential_slots = set(request.credential_bindings)
        if {key.lower() for key in supplied_setting_keys} & self._RESERVED_CONFIGURATION_KEYS:
            raise CatalogAdapterPolicyError(
                "目录配置不能包含命令、DSN、URL、Header、原始密钥、证书路径、环境变量或工作目录。"
            )
        unknown_settings = supplied_setting_keys - set(manifest.allowed_settings)
        unknown_slots = supplied_credential_slots - set(manifest.credential_slots)
        if unknown_settings or unknown_slots:
            unknown = sorted(unknown_settings | unknown_slots)
            raise CatalogAdapterPolicyError(
                "配置包含适配器未声明的字段：" + "、".join(unknown)
            )
        setting_policies = {item.key: item for item in manifest.setting_policies}
        credential_policies = {item.key: item for item in manifest.credential_policies}
        missing_settings = sorted(
            key
            for key, policy in setting_policies.items()
            if policy.required and key not in request.settings
        )
        missing_credentials = sorted(
            key
            for key, policy in credential_policies.items()
            if policy.required and key not in request.credential_bindings
        )
        if missing_settings or missing_credentials:
            raise CatalogAdapterPolicyError(
                "缺少必填配置：" + "、".join(missing_settings + missing_credentials)
            )
        normalized_settings: dict[str, str | int | float | bool] = {}
        for key, value in request.settings.items():
            normalized_settings[key] = self._validate_setting(
                setting_policies.get(key),
                value,
            )

        for slot, credential_id in request.credential_bindings.items():
            if not re.fullmatch(r"cred_[A-Za-z0-9]+", credential_id):
                raise CatalogAdapterPolicyError("凭据绑定必须使用有效 credential_id。")
            if self.credential_validator is None:
                raise CatalogAdapterPolicyError("目录凭据存储当前不可用。")
            try:
                credential = self._credential_call(
                    self.credential_validator,
                    credential_id,
                )
            except Exception as exc:
                raise CatalogAdapterPolicyError(
                    f"凭据绑定不存在或不可用：{credential_id}"
                ) from exc
            if getattr(credential, "status", "") != "active":
                raise CatalogAdapterPolicyError(
                    f"凭据绑定不是可用状态：{credential_id}"
                )
            policy = credential_policies.get(slot)
            accepted_kinds = set(policy.accepted_kinds if policy else ())
            if accepted_kinds and getattr(credential, "kind", "") not in accepted_kinds:
                raise CatalogAdapterPolicyError(
                    f"凭据类型不适用于配置槽：{slot}"
                )
            if manifest.credential_policies and (
                getattr(credential, "catalog_project_id", "") != manifest.project_id
                or getattr(credential, "catalog_slot", "") != slot
            ):
                raise CatalogAdapterPolicyError(
                    "凭据只能绑定到创建它的目录项目和固定槽位。"
                )

        workspace_id = str(request.workspace_id or "").strip()
        if manifest.workspace_policy is not None:
            if not workspace_id:
                raise CatalogAdapterPolicyError("该适配器必须绑定已封存的受控工作区。")
            if self.workspace_store is None:
                raise CatalogAdapterPolicyError("MCP 文件工作区当前不可用。")
            try:
                self.workspace_store.require_sealed(
                    manifest.project_id,
                    workspace_id,
                    tenant_id=self.tenant_id,
                )
            except CatalogWorkspaceError as exc:
                raise CatalogAdapterPolicyError(str(exc)) from exc
        elif workspace_id:
            raise CatalogAdapterPolicyError("该适配器不接受文件工作区配置。")

        normalized = request.model_copy(deep=True)
        normalized.settings = normalized_settings
        self._configurations[scope_key] = normalized
        self._configuration_snapshots[scope_key] = self._snapshot_configuration(
            normalized
        )
        self._credential_snapshots.pop(scope_key, None)
        self._account_snapshots.pop(scope_key, None)
        self._browser_snapshots.pop(scope_key, None)
        if manifest.credential_policies:
            self._credential_verification[scope_key] = "unverified"
        if (
            manifest.database_policy is not None
            or manifest.saas_policy is not None
            or manifest.browser_policy is not None
        ):
            self._preflight_status[scope_key] = "unverified"
        self._revoke_approvals(manifest.project_id)
        return {
            "project_id": manifest.project_id,
            "configured": True,
            "configured_settings": sorted(request.settings),
            "configured_credential_slots": sorted(request.credential_bindings),
            "workspace_id": workspace_id or None,
        }

    async def connect(self, project_id: str) -> dict[str, Any]:
        manifest = self._require_executable(project_id)
        scope_key = self._scope_key(manifest.project_id)
        if scope_key in self._unbinding_scopes:
            raise CatalogAdapterPolicyError("账号正在解绑，请等待操作完成。")
        if scope_key in self._connecting_scopes:
            raise CatalogAdapterPolicyError("该目录连接正在建立，请等待当前操作完成。")
        self._connecting_scopes.add(scope_key)
        try:
            return await self._connect_with_lifecycle_lock(manifest, scope_key)
        finally:
            self._connecting_scopes.discard(scope_key)

    async def _connect_with_lifecycle_lock(
        self,
        manifest: CatalogAdapterManifest,
        scope_key: tuple[str, str, str],
    ) -> dict[str, Any]:
        session_owner = self._session_owner(manifest.project_id)
        started_at = time.monotonic()
        async with self._lock:
            if scope_key in self._unbinding_scopes:
                raise CatalogAdapterPolicyError("账号正在解绑，请等待操作完成。")
            existing = self._sessions.get(scope_key)
            if existing:
                try:
                    await self._ensure_credentials_fresh(manifest.project_id)
                    tools = await self.manager.list_tools(
                        existing,
                        session_owner=session_owner,
                    )
                    if manifest.browser_policy is not None:
                        schema_digest = self._tool_schema_digest(tools)
                        if (
                            {tool.name for tool in tools}
                            != set(manifest.tool_policies)
                            or schema_digest
                            != manifest.browser_policy.tool_schema_sha256
                        ):
                            await self._taint_browser_session(manifest)
                            raise CatalogAdapterPolicyError(
                                "浏览器工具清单或输入 Schema 发生漂移，连接已阻断。"
                            )
                        snapshot = await self._refresh_browser_snapshot(
                            manifest,
                            session_id=existing,
                        )
                        if snapshot.status != "active":
                            await self._taint_browser_session(manifest)
                            raise CatalogAdapterPolicyError(
                                "浏览器会话已经污染，请重新连接。"
                            )
                    return self._connection_payload(manifest, existing, tools)
                except (
                    MCPClientError,
                    CatalogBrowserSessionExpiredError,
                    EOFError,
                    BrokenPipeError,
                    OSError,
                ):
                    if manifest.browser_policy is not None:
                        await self._forget_browser_session(
                            manifest,
                            reason="expired",
                        )
                    else:
                        self._sessions.pop(scope_key, None)

            if manifest.transport != "stdio" or not manifest.server_command:
                raise CatalogAdapterUnavailableError(
                    "该适配器尚未配置受控的可执行传输。"
                )
            if manifest.connection_kind == "sandboxed-stdio":
                environment: dict[str, str] = {}
                configuration = self._configurations.get(scope_key)
                uses_token_sidecar = (
                    tuple(manifest.server_command[: len(TOKEN_SANDBOX_PROXY)])
                    == TOKEN_SANDBOX_PROXY
                )
                if uses_token_sidecar and manifest.project_id == "terraform-mcp":
                    environment["MCP_TOKEN_SOCKET_PATH"] = os.getenv(
                        "MCP_REGISTRY_SOCKET_PATH",
                        "/run/modelmirror-registry-mcp/registry-mcp.sock",
                    )
                workspace_id = ""
                if manifest.workspace_policy is not None:
                    workspace_id = str(
                        configuration.workspace_id if configuration else ""
                    ).strip()
                    if not workspace_id or self.workspace_store is None:
                        raise CatalogAdapterPolicyError(
                            "请先创建、封存并绑定受控工作区。"
                        )
                    try:
                        self.workspace_store.require_sealed(
                            manifest.project_id,
                            workspace_id,
                            tenant_id=self.tenant_id,
                        )
                    except CatalogWorkspaceError as exc:
                        raise CatalogAdapterPolicyError(str(exc)) from exc
                    if manifest.database_policy is None:
                        environment["MCP_FILE_WORKSPACE_ID"] = workspace_id
                if manifest.database_policy is not None and configuration is None:
                    raise CatalogAdapterPolicyError("请先保存受控数据库配置。")
                secrets: dict[str, str] = {}
                snapshots: dict[str, tuple[str, float]] = {}
                if manifest.credential_policies:
                    if configuration is None:
                        raise CatalogAdapterPolicyError("请先绑定所需凭据并保存配置。")
                    if self.credential_resolver is None or self.credential_validator is None:
                        raise CatalogAdapterPolicyError("目录凭据存储当前不可用。")
                    for policy in manifest.credential_policies:
                        credential_id = configuration.credential_bindings.get(policy.key, "")
                        if not credential_id:
                            if policy.required:
                                raise CatalogAdapterPolicyError(f"缺少凭据绑定：{policy.key}")
                            continue
                        public = self._credential_call(
                            self.credential_validator,
                            credential_id,
                        )
                        if getattr(public, "status", "") != "active":
                            raise CatalogAdapterPolicyError("绑定凭据已撤销或不可用，请重新配置。")
                        if (
                            getattr(public, "catalog_project_id", "") != manifest.project_id
                            or getattr(public, "catalog_slot", "") != policy.key
                        ):
                            raise CatalogAdapterPolicyError(
                                "绑定凭据不属于当前目录项目或固定槽位。"
                            )
                        secrets[policy.key] = self._credential_call(
                            self.credential_resolver,
                            credential_id,
                        )
                        snapshots[policy.key] = (
                            credential_id,
                            float(getattr(public, "updated_at", 0.0)),
                        )
                if (
                    uses_token_sidecar
                    or manifest.credential_policies
                    or manifest.database_policy is not None
                ):
                    handshake_configuration: dict[str, Any] = {
                        "settings": (
                            configuration.settings if configuration is not None else {}
                        ),
                        "credentials": secrets,
                    }
                    if manifest.database_policy is not None:
                        handshake_configuration["workspace_id"] = workspace_id or None
                    handshake_payload = json.dumps(
                        handshake_configuration,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if len(handshake_payload) > 64 * 1024:
                        raise CatalogAdapterPolicyError("目录凭据配置超过内部传输上限。")
                    handshake_name = (
                        "MCP_DATABASE_HANDSHAKE_B64"
                        if manifest.database_policy is not None
                        else (
                            "MCP_SAAS_HANDSHAKE_B64"
                            if manifest.saas_policy is not None
                            else "MCP_TOKEN_HANDSHAKE_B64"
                        )
                    )
                    environment[handshake_name] = base64.urlsafe_b64encode(
                        handshake_payload
                    ).decode("ascii")
                if manifest.browser_policy is not None:
                    browser_handshake = json.dumps(
                        {
                            "project_id": manifest.project_id,
                            "contract_version": manifest.browser_policy.contract_version,
                            "tool_schema_sha256": (
                                manifest.browser_policy.tool_schema_sha256
                            ),
                            "limits": BROWSER_LIMITS,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if len(browser_handshake) > 64 * 1024:
                        raise CatalogAdapterPolicyError(
                            "浏览器内部握手超过安全上限。"
                        )
                    environment["MCP_BROWSER_HANDSHAKE_B64"] = (
                        base64.urlsafe_b64encode(browser_handshake).decode("ascii")
                    )
                profile: dict[str, Any] = {
                    "transport": "stdio",
                    "server_command": list(manifest.server_command),
                    "network_policy": manifest.network_policy,
                    "reconnect_attempts": (
                        0
                        if uses_token_sidecar
                        or manifest.credential_policies
                        or manifest.database_policy is not None
                        or manifest.browser_policy is not None
                        else 1
                    ),
                    "operation_timeout": manifest.operation_timeout,
                    "session_owner": session_owner,
                }
                if environment:
                    profile["environment"] = environment
                session_id = await self.manager.connect_profile(**profile)
                if (
                    uses_token_sidecar
                    or manifest.credential_policies
                    or manifest.database_policy is not None
                    or manifest.browser_policy is not None
                ):
                    await self.manager.scrub_session_environment(
                        session_id,
                        session_owner=session_owner,
                    )
            else:
                session_id = await self.manager.connect(
                    list(manifest.server_command),
                    session_owner=session_owner,
                )
            try:
                if (
                    manifest.database_policy is not None
                    or manifest.saas_policy is not None
                    or manifest.browser_policy is not None
                ):
                    self._preflight_status[scope_key] = "verifying"
                tools = await self.manager.list_tools(
                    session_id,
                    session_owner=session_owner,
                )
                if manifest.legacy_unrestricted_calls:
                    await self.registry.register_session_tools(
                        session_id=session_id,
                        server_id=f"catalog:{manifest.project_id}",
                        tools=tools,
                    )
            except Exception:
                if (
                    manifest.database_policy is not None
                    or manifest.saas_policy is not None
                    or manifest.browser_policy is not None
                ):
                    self._preflight_status[scope_key] = "failed"
                await self.manager.disconnect(
                    session_id,
                    session_owner=session_owner,
                )
                raise
            account_snapshot: CatalogAccountSnapshot | None = None
            if manifest.saas_policy is not None:
                configuration_snapshot = self._configuration_snapshots.get(scope_key)
                if configuration_snapshot is None:
                    await self.manager.disconnect(
                        session_id,
                        session_owner=session_owner,
                    )
                    self._preflight_status[scope_key] = "failed"
                    raise CatalogAdapterPolicyError("SaaS 配置快照缺失，请重新保存配置。")
                schema_digest = self._tool_schema_digest(tools)
                expected_tools = set(manifest.tool_policies)
                if (
                    {tool.name for tool in tools} != expected_tools
                    or schema_digest
                    != manifest.saas_policy.tool_schema_sha256
                ):
                    await self.manager.disconnect(
                        session_id,
                        session_owner=session_owner,
                    )
                    self._preflight_status[scope_key] = "failed"
                    raise CatalogAdapterPolicyError(
                        "SaaS 工具清单或输入 Schema 发生漂移，连接已阻断。"
                    )
                account_material = {
                    "project_id": manifest.project_id,
                    "configuration": configuration_snapshot.digest,
                    "credentials": self._credential_snapshot_digest(snapshots),
                    "tool_schema": schema_digest,
                }
                account_snapshot = CatalogAccountSnapshot(
                    digest=self._sha256_json(account_material),
                    tool_schema_sha256=schema_digest,
                    verified_at=time.time(),
                )
            browser_snapshot: CatalogBrowserSnapshot | None = None
            if manifest.browser_policy is not None:
                schema_digest = self._tool_schema_digest(tools)
                expected_tools = set(manifest.tool_policies)
                if (
                    {tool.name for tool in tools} != expected_tools
                    or schema_digest
                    != manifest.browser_policy.tool_schema_sha256
                ):
                    await self.manager.disconnect(
                        session_id,
                        session_owner=session_owner,
                    )
                    self._preflight_status[scope_key] = "failed"
                    raise CatalogAdapterPolicyError(
                        "浏览器工具清单或输入 Schema 发生漂移，连接已阻断。"
                    )
                try:
                    browser_snapshot = await self._refresh_browser_snapshot(
                        manifest,
                        session_id=session_id,
                    )
                except Exception:
                    await self.manager.disconnect(
                        session_id,
                        session_owner=session_owner,
                    )
                    self._preflight_status[scope_key] = "failed"
                    raise
                if browser_snapshot.status != "active":
                    await self.manager.disconnect(
                        session_id,
                        session_owner=session_owner,
                    )
                    self._preflight_status[scope_key] = "failed"
                    raise CatalogAdapterPolicyError(
                        "浏览器会话初始化后即处于污染状态，连接已阻断。"
                    )
            if scope_key in self._unbinding_scopes:
                await self.manager.disconnect(
                    session_id,
                    session_owner=session_owner,
                )
                self._preflight_status[scope_key] = "unverified"
                raise CatalogAdapterPolicyError(
                    "账号解绑已开始，本次连接未发布。"
                )
            # Publish catalog ownership only after all provider preflight and
            # fixed tools/inputSchema checks have succeeded.
            self._sessions[scope_key] = session_id
            if manifest.browser_policy is not None:
                self._browser_disconnect_reasons.pop(scope_key, None)
            if manifest.credential_policies:
                self._credential_snapshots[scope_key] = snapshots
            if account_snapshot is not None:
                self._account_snapshots[scope_key] = account_snapshot
            if (
                manifest.database_policy is not None
                or manifest.saas_policy is not None
                or manifest.browser_policy is not None
            ):
                self._preflight_status[scope_key] = "verified"
                if manifest.credential_policies:
                    # Database and stateful SaaS children complete an
                    # authenticated read-only preflight before initialization.
                    self._credential_verification[scope_key] = "verified"
            payload = self._connection_payload(manifest, session_id, tools)
            logger.info(
                "MCP catalog connect project=%s tools=%d duration_ms=%d",
                manifest.project_id,
                len(tools),
                int((time.monotonic() - started_at) * 1000),
            )
            return payload

    async def _disconnect_with_scope_locked(
        self,
        manifest: CatalogAdapterManifest,
    ) -> dict[str, Any]:
        scope_key = self._scope_key(manifest.project_id)
        async with self._lock:
            session_id = self._sessions.get(scope_key)
        if session_id is None:
            raise MCPSessionNotFoundError(
                f"MCP catalog session not found: {manifest.project_id}"
            )
        disconnected = False
        try:
            await self.manager.disconnect(
                session_id,
                session_owner=self._session_owner(manifest.project_id),
            )
            disconnected = True
        except Exception:
            if manifest.browser_policy is not None:
                self._preflight_status[scope_key] = "failed"
                self._browser_disconnect_reasons[scope_key] = "disconnect-failed"
                self._browser_elements.pop(scope_key, None)
                self._revoke_approvals(manifest.project_id)
            raise
        finally:
            if disconnected or manifest.browser_policy is None:
                await self.registry.unregister_session(session_id)
                async with self._lock:
                    if self._sessions.get(scope_key) == session_id:
                        self._sessions.pop(scope_key, None)
                        self._credential_snapshots.pop(scope_key, None)
                        self._account_snapshots.pop(scope_key, None)
                        browser_snapshot = self._browser_snapshots.get(scope_key)
                        if browser_snapshot is not None:
                            self._browser_snapshots[scope_key] = CatalogBrowserSnapshot(
                                **{**asdict(browser_snapshot), "status": "disconnected"}
                            )
                            self._browser_disconnect_reasons[scope_key] = (
                                "user-disconnected"
                            )
                        self._browser_elements.pop(scope_key, None)
                        if (
                            manifest.database_policy is not None
                            or manifest.saas_policy is not None
                            or manifest.browser_policy is not None
                        ):
                            self._preflight_status[scope_key] = "unverified"
        self._revoke_approvals(manifest.project_id)
        logger.info("MCP catalog disconnect project=%s", manifest.project_id)
        return {"ok": True, "project_id": manifest.project_id}

    async def _taint_browser_session(
        self,
        manifest: CatalogAdapterManifest,
    ) -> None:
        """Fail closed after an ambiguous browser state change."""

        if manifest.browser_policy is None:
            return
        scope_key = self._scope_key(manifest.project_id)
        snapshot = self._browser_snapshots.get(scope_key)
        if snapshot is not None and snapshot.status != "tainted":
            self._browser_snapshots[scope_key] = CatalogBrowserSnapshot(
                **{**asdict(snapshot), "status": "tainted"}
            )
        self._preflight_status[scope_key] = "failed"
        self._browser_disconnect_reasons[scope_key] = "tainted"
        self._browser_elements.pop(scope_key, None)
        self._revoke_approvals(manifest.project_id)
        session_id = self._sessions.pop(scope_key, None)
        if session_id is None:
            return
        try:
            await self.manager.disconnect(
                session_id,
                session_owner=self._session_owner(manifest.project_id),
            )
        except Exception as exc:
            logger.warning(
                "MCP catalog browser teardown failed project=%s",
                manifest.project_id,
            )
        finally:
            await self.registry.unregister_session(session_id)

    async def _forget_browser_session(
        self,
        manifest: CatalogAdapterManifest,
        *,
        reason: str,
    ) -> None:
        if manifest.browser_policy is None:
            return
        scope_key = self._scope_key(manifest.project_id)
        session_id = self._sessions.pop(scope_key, None)
        snapshot = self._browser_snapshots.get(scope_key)
        if snapshot is not None:
            self._browser_snapshots[scope_key] = CatalogBrowserSnapshot(
                **{**asdict(snapshot), "status": "disconnected"}
            )
        self._browser_disconnect_reasons[scope_key] = reason
        self._browser_elements.pop(scope_key, None)
        self._preflight_status[scope_key] = "unverified"
        self._revoke_approvals(manifest.project_id)
        if session_id is None:
            return
        try:
            await self.manager.disconnect(
                session_id,
                session_owner=self._session_owner(manifest.project_id),
            )
        except Exception:
            pass
        await self.registry.unregister_session(session_id)

    async def disconnect(self, project_id: str) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        scope_key = self._scope_key(manifest.project_id)
        lock = self._approval_locks.setdefault(scope_key, asyncio.Lock())
        async with lock:
            if any(
                entry.tenant_id == self.tenant_id
                and entry.owner_id == self.owner_id
                and entry.project_id == manifest.project_id
                and entry.state == "started"
                for entry in self._execution_ledger.values()
            ):
                raise CatalogAdapterPolicyError(
                    "远程写入仍在执行，当前不能断开会话；请等待结果确定。"
                )
            call_lock = self._call_locks.setdefault(scope_key, asyncio.Lock())
            async with call_lock:
                return await self._disconnect_with_scope_locked(manifest)

    async def call_tool(
        self,
        project_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self._require_executable(project_id)
        scope_key = self._scope_key(manifest.project_id)
        if scope_key in self._unbinding_scopes:
            raise CatalogAdapterPolicyError("账号正在解绑，当前不能调用工具。")
        session_id = self._sessions.get(scope_key)
        if session_id is None:
            raise CatalogAdapterUnavailableError("请先连接该 MCP 适配器。")
        if (
            manifest.browser_policy is not None
            and self._preflight_status.get(scope_key) != "verified"
        ):
            raise CatalogAdapterPolicyError(
                "浏览器会话状态尚未验证；请刷新会话状态或重新连接。"
            )
        await self._ensure_credentials_fresh(manifest.project_id)

        if not manifest.legacy_unrestricted_calls:
            policy = manifest.tool_policies.get(tool_name)
            if policy is None:
                raise CatalogAdapterPolicyError(
                    "该工具尚未完成显式读写与审批策略分类。"
                )
            if manifest.database_policy is not None and not policy.read_only:
                raise CatalogAdapterPolicyError(
                    "第 5 批数据库适配器仅允许已审计的只读工具。"
                )
            if manifest.browser_policy is not None and {
                "generation",
                "page_revision",
                "page_digest",
            } & set(arguments):
                raise CatalogAdapterPolicyError(
                    "浏览器会话代次与页面摘要由服务端绑定，客户端不能提交。"
                )
            if policy.sensitive or policy.terminal or policy.effect == "terminal":
                raise CatalogAdapterPolicyError(
                    "该工具属于敏感或终止性操作，当前批次不允许执行。"
                )
            if policy.requires_approval:
                if manifest.browser_policy is not None:
                    try:
                        browser_snapshot = await self._refresh_browser_snapshot(
                            manifest,
                            session_id=session_id,
                        )
                    except (
                        MCPClientError,
                        CatalogBrowserSessionExpiredError,
                        EOFError,
                        BrokenPipeError,
                        OSError,
                    ) as exc:
                        await self._forget_browser_session(
                            manifest,
                            reason="expired",
                        )
                        raise CatalogAdapterPolicyError(
                            "浏览器临时会话已经过期，请重新连接。"
                        ) from exc
                    if browser_snapshot.status != "active":
                        await self._taint_browser_session(manifest)
                        raise CatalogAdapterPolicyError(
                            "浏览器会话已经污染，请重新连接后再操作。"
                        )
                raise CatalogApprovalRequiredError(
                    self._create_approval(
                        manifest,
                        session_id=session_id,
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                )

        return await self._execute_tool(
            manifest,
            session_id=session_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    async def confirm_approval(
        self,
        project_id: str,
        approval_id: str,
    ) -> dict[str, Any]:
        manifest = self._require_executable(project_id)
        scope_key = self._scope_key(manifest.project_id)
        approval_key = self._approval_key(approval_id)
        lock = self._approval_locks.setdefault(scope_key, asyncio.Lock())
        async with lock:
            if scope_key in self._unbinding_scopes:
                raise CatalogAdapterPolicyError(
                    "账号解绑或凭据撤销正在进行，当前不能确认写入。"
                )
            approval = self._approvals.get(approval_key)
            ledger = self._execution_ledger.get(approval_key)
            if ledger is not None and (
                ledger.tenant_id != self.tenant_id
                or ledger.owner_id != self.owner_id
                or ledger.project_id != manifest.project_id
            ):
                ledger = None
            if approval is None and ledger is not None:
                if ledger.state == "completed" and ledger.result is not None:
                    replay = json.loads(json.dumps(ledger.result, ensure_ascii=False))
                    replay["idempotency_key"] = ledger.idempotency_key
                    replay["idempotent_replay"] = True
                    replay["unknown_outcome"] = False
                    return replay
                if ledger.state in {"started", "unknown"}:
                    raise CatalogUnknownOutcomeError(ledger.idempotency_key)
            if (
                approval is None
                or approval.tenant_id != self.tenant_id
                or approval.owner_id != self.owner_id
                or approval.project_id != manifest.project_id
            ):
                raise CatalogAdapterPolicyError("一次性确认不存在或已经失效。")
            if approval.used or approval.expires_at <= time.time():
                self._approvals.pop(approval_key, None)
                raise CatalogAdapterPolicyError("一次性确认已经使用或过期。")
            if approval.context_kind == "workspace":
                if self.workspace_store is None:
                    raise CatalogAdapterPolicyError("MCP 文件工作区当前不可用。")
                workspace = self.workspace_store.require_sealed(
                    manifest.project_id,
                    approval.workspace_id,
                    tenant_id=self.tenant_id,
                )
                if workspace.manifest_sha256 != approval.workspace_manifest_sha256:
                    raise CatalogAdapterPolicyError("工作区内容已经变化，请重新发起操作。")
            elif approval.context_kind == "remote-resource":
                await self._ensure_credentials_fresh(manifest.project_id)
                configuration_snapshot = self._configuration_snapshots.get(scope_key)
                account_snapshot = self._account_snapshots.get(scope_key)
                credential_digest = self._credential_snapshot_digest(
                    self._credential_snapshots.get(scope_key, {})
                )
                policy = manifest.tool_policies.get(approval.tool_name)
                if (
                    configuration_snapshot is None
                    or configuration_snapshot.revision != approval.configuration_revision
                    or configuration_snapshot.digest != approval.configuration_digest
                    or credential_digest != approval.credential_snapshot_digest
                    or account_snapshot is None
                    or account_snapshot.digest != approval.account_snapshot_digest
                    or account_snapshot.tool_schema_sha256 != approval.tool_schema_sha256
                    or policy is None
                    or self._tool_policy_digest(policy) != approval.tool_policy_digest
                ):
                    self._approvals.pop(approval_key, None)
                    raise CatalogAdapterPolicyError(
                        "账号、配置、凭据或工具策略已经变化，请重新发起操作。"
                    )
            elif approval.context_kind == "browser-session":
                current_session_id = self._sessions.get(scope_key)
                if not current_session_id or current_session_id != approval.session_id:
                    self._approvals.pop(approval_key, None)
                    raise CatalogAdapterPolicyError(
                        "浏览器会话已经变化，请重新发起操作。"
                    )
                try:
                    browser_snapshot = await self._refresh_browser_snapshot(
                        manifest,
                        session_id=current_session_id,
                    )
                except Exception:
                    self._approvals.pop(approval_key, None)
                    await self._taint_browser_session(manifest)
                    raise
                policy = manifest.tool_policies.get(approval.tool_name)
                if (
                    browser_snapshot.status != "active"
                    or browser_snapshot.digest != approval.browser_snapshot_digest
                    or manifest.browser_policy is None
                    or manifest.browser_policy.tool_schema_sha256
                    != approval.tool_schema_sha256
                    or policy is None
                    or self._tool_policy_digest(policy) != approval.tool_policy_digest
                ):
                    self._approvals.pop(approval_key, None)
                    raise CatalogAdapterPolicyError(
                        "浏览器页面、会话或工具策略已经变化，请重新发起操作。"
                    )
            else:
                self._approvals.pop(approval_key, None)
                raise CatalogAdapterPolicyError("一次性确认上下文无效。")
            session_id = self._sessions.get(scope_key)
            if (
                approval.tool_name != "__delete_workspace__"
                and (not session_id or session_id != approval.session_id)
            ):
                raise CatalogAdapterPolicyError("MCP 会话已经变化，请重新发起操作。")
            approval.used = True
            self._approvals.pop(approval_key, None)
            if approval.context_kind in {"remote-resource", "browser-session"}:
                ledger = CatalogExecutionLedgerEntry(
                    approval_id=approval.approval_id,
                    tenant_id=self.tenant_id,
                    owner_id=self.owner_id,
                    project_id=manifest.project_id,
                    idempotency_key=approval.idempotency_key,
                    state="started",
                )
                self._execution_ledger[approval_key] = ledger

        if approval.tool_name == "__delete_workspace__":
            if scope_key in self._sessions:
                await self.disconnect(manifest.project_id)
            assert self.workspace_store is not None
            self.workspace_store.delete(
                manifest.project_id,
                approval.workspace_id,
                tenant_id=self.tenant_id,
            )
            self._configurations.pop(scope_key, None)
            self._configuration_snapshots.pop(scope_key, None)
            return {
                "ok": True,
                "project_id": manifest.project_id,
                "workspace_id": approval.workspace_id,
            }
        assert session_id is not None
        if approval.context_kind == "workspace":
            return await self._execute_tool(
                manifest,
                session_id=session_id,
                tool_name=approval.tool_name,
                arguments=approval.arguments,
            )
        assert ledger is not None
        if approval.context_kind == "browser-session":
            try:
                result = await self._execute_tool(
                    manifest,
                    session_id=session_id,
                    tool_name=approval.tool_name,
                    arguments=approval.arguments,
                    expected_browser_snapshot_digest=(
                        approval.browser_snapshot_digest
                    ),
                )
            except CatalogBrowserStateDriftError:
                ledger.state = "rejected"
                raise
            except Exception as exc:
                rejection_reason = _browser_policy_rejection_reason(exc)
                if rejection_reason is not None:
                    ledger.state = "rejected"
                    raise CatalogBrowserPolicyRejectedError(
                        rejection_reason,
                        approval.idempotency_key,
                    ) from exc
                ledger.state = "unknown"
                await self._taint_browser_session(manifest)
                raise CatalogUnknownOutcomeError(approval.idempotency_key) from exc
            if result.get("is_error"):
                ledger.state = "unknown"
                await self._taint_browser_session(manifest)
                raise CatalogUnknownOutcomeError(approval.idempotency_key)
            result["idempotency_key"] = approval.idempotency_key
            result["idempotent_replay"] = False
            result["unknown_outcome"] = False
            ledger.state = "completed"
            ledger.result = json.loads(json.dumps(result, ensure_ascii=False))
            return result
        execution_arguments = dict(approval.arguments)
        execution_arguments["__modelmirror_idempotency_key"] = approval.idempotency_key
        try:
            result = await self._execute_tool(
                manifest,
                session_id=session_id,
                tool_name=approval.tool_name,
                arguments=execution_arguments,
            )
        except Exception as exc:
            if isinstance(exc, McpError):
                rpc_error = exc.error
                rpc_data = rpc_error.data if isinstance(rpc_error.data, dict) else {}
                rpc_reason = str(rpc_data.get("reason") or "")
                if rpc_error.code == -32009 and rpc_reason in {
                    "rate_limited",
                    "provider_rejected",
                }:
                    ledger.state = "rejected"
                    raise CatalogProviderRejectedError(
                        rpc_reason,
                        approval.idempotency_key,
                    ) from exc
                if rpc_error.code == -32008 and rpc_reason == "unknown_outcome":
                    ledger.state = "unknown"
                    raise CatalogUnknownOutcomeError(approval.idempotency_key) from exc
            if isinstance(
                exc,
                (CatalogAdapterPolicyError, MCPSessionNotFoundError),
            ):
                # These failures happen before the manager can send the frozen
                # write, so they are terminal local rejections rather than an
                # ambiguous provider outcome.
                ledger.state = "rejected"
                raise
            ledger.state = "unknown"
            raise CatalogUnknownOutcomeError(approval.idempotency_key) from exc
        if result.get("is_error"):
            # A state-changing request reached the provider boundary, but an
            # MCP error result does not prove whether the provider committed
            # the change. Keep the one-shot ledger fail-closed and never label
            # the operation completed or resend it automatically.
            ledger.state = "unknown"
            raise CatalogUnknownOutcomeError(approval.idempotency_key)
        result["idempotency_key"] = approval.idempotency_key
        result["idempotent_replay"] = False
        result["unknown_outcome"] = False
        ledger.state = "completed"
        ledger.result = json.loads(json.dumps(result, ensure_ascii=False))
        return result

    async def cancel_approval(
        self,
        project_id: str,
        approval_id: str,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        scope_key = self._scope_key(manifest.project_id)
        approval_key = self._approval_key(approval_id)
        lock = self._approval_locks.setdefault(scope_key, asyncio.Lock())
        async with lock:
            approval = self._approvals.get(approval_key)
            if (
                approval is None
                or approval.tenant_id != self.tenant_id
                or approval.owner_id != self.owner_id
                or approval.project_id != manifest.project_id
            ):
                raise CatalogAdapterPolicyError("一次性确认不存在或已经失效。")
            self._approvals.pop(approval_key, None)
            return {"ok": True, "approval_id": approval.approval_id}

    async def _execute_tool(
        self,
        manifest: CatalogAdapterManifest,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        expected_browser_snapshot_digest: str = "",
    ) -> dict[str, Any]:

        scope_key = self._scope_key(manifest.project_id)
        started_at = time.monotonic()
        tool_policy = manifest.tool_policies.get(tool_name)
        retry_on_failure = not (
            manifest.browser_policy is not None
            or (
                tool_policy is not None
                and tool_policy.effect in {"state-write", "terminal"}
            )
        )

        try:
            call_lock = self._call_locks.setdefault(scope_key, asyncio.Lock())
            async with call_lock:
                if scope_key in self._unbinding_scopes:
                    raise CatalogAdapterPolicyError(
                        "账号解绑或凭据撤销正在进行，当前不能调用工具。"
                    )
                if expected_browser_snapshot_digest:
                    if manifest.browser_policy is None:
                        raise CatalogBrowserStateDriftError(
                            "浏览器审批上下文不存在。"
                        )
                    current_snapshot = await self._refresh_browser_snapshot(
                        manifest,
                        session_id=session_id,
                    )
                    if (
                        current_snapshot.status != "active"
                        or current_snapshot.digest
                        != expected_browser_snapshot_digest
                    ):
                        raise CatalogBrowserStateDriftError(
                            "浏览器页面状态已经变化，请重新发起操作。"
                        )
                if retry_on_failure:
                    result = await self.manager.call_tool(
                        session_id,
                        tool_name,
                        arguments,
                        session_owner=self._session_owner(manifest.project_id),
                    )
                else:
                    result = await self.manager.call_tool(
                        session_id,
                        tool_name,
                        arguments,
                        retry_on_failure=False,
                        session_owner=self._session_owner(manifest.project_id),
                    )
        except Exception as exc:
            browser_rejection = _browser_policy_rejection_reason(exc)
            if manifest.credential_policies and manifest.saas_policy is None:
                self._credential_verification[scope_key] = "verification-failed"
            if manifest.database_policy is not None:
                self._preflight_status[scope_key] = "failed"
            if manifest.browser_policy is not None:
                if browser_rejection is None:
                    self._preflight_status[scope_key] = "failed"
                    if (
                        tool_policy is not None
                        and tool_policy.effect != "read"
                        and not isinstance(exc, CatalogBrowserStateDriftError)
                    ):
                        await self._taint_browser_session(manifest)
                else:
                    # JSON-RPC -32011 + retryable=false is a definitive
                    # pre-dispatch policy rejection. Preserve the one-shot
                    # browser session so the user can correct the target and
                    # request a fresh approval; never replay the old approval.
                    self._preflight_status[scope_key] = "verified"
            raise
        logger.info(
            "MCP catalog call project=%s tool=%s duration_ms=%d",
            manifest.project_id,
            tool_name,
            int((time.monotonic() - started_at) * 1000),
        )
        payload = self._serialize_call_result(
            result,
            max_output_bytes=manifest.max_output_bytes,
        )
        if manifest.browser_policy is not None:
            if (
                payload["is_error"]
                and tool_policy is not None
                and tool_policy.effect != "read"
            ):
                await self._taint_browser_session(manifest)
            else:
                try:
                    snapshot = await self._refresh_browser_snapshot(
                        manifest,
                        session_id=session_id,
                    )
                except Exception:
                    self._preflight_status[scope_key] = "failed"
                    if (
                        tool_policy is not None
                        and tool_policy.effect != "read"
                    ):
                        await self._taint_browser_session(manifest)
                    raise
                if snapshot.status == "tainted":
                    await self._taint_browser_session(manifest)
                    if (
                        tool_policy is not None
                        and tool_policy.effect != "read"
                    ):
                        raise CatalogAdapterPolicyError(
                            "浏览器操作后会话状态未知，已销毁临时浏览器。"
                        )
                else:
                    if tool_policy is not None and tool_policy.effect != "read":
                        self._browser_elements.pop(scope_key, None)
                    if tool_name in {"take_snapshot", "browser_snapshot"}:
                        self._store_browser_elements(
                            manifest,
                            payload,
                            snapshot,
                        )
                    if (
                        tool_policy is not None
                        and tool_policy.effect == "artifact-create"
                        and not payload["is_error"]
                    ):
                        try:
                            payload["artifacts"] = [
                                self._register_browser_artifact(
                                    manifest,
                                    session_id=session_id,
                                    payload=payload,
                                    snapshot=snapshot,
                                )
                            ]
                        except Exception:
                            self._preflight_status[scope_key] = "failed"
                            await self._taint_browser_session(manifest)
                            raise
                    self._preflight_status[scope_key] = "verified"
        if manifest.credential_policies and manifest.saas_policy is None:
            self._credential_verification[scope_key] = (
                "verification-failed" if payload["is_error"] else "verified"
            )
        if manifest.database_policy is not None:
            self._preflight_status[scope_key] = (
                "failed" if payload["is_error"] else "verified"
            )
        configuration = self._configurations.get(scope_key)
        if (
            self.workspace_store is not None
            and configuration is not None
            and configuration.workspace_id
        ):
            artifacts = self.workspace_store.discover_artifacts(
                manifest.project_id,
                configuration.workspace_id,
                tenant_id=self.tenant_id,
            )
            payload["artifacts"] = [
                {
                    **asdict(artifact),
                    "download_url": (
                        f"/api/mcp/catalog/{manifest.project_id}/workspaces/"
                        f"{configuration.workspace_id}/artifacts/{artifact.artifact_id}/download"
                    ),
                }
                for artifact in artifacts
            ]
        return payload

    def _store_browser_elements(
        self,
        manifest: CatalogAdapterManifest,
        payload: dict[str, Any],
        snapshot: CatalogBrowserSnapshot,
    ) -> None:
        raw = payload.get("raw")
        structured = None
        if isinstance(raw, dict):
            structured = raw.get("structuredContent") or raw.get(
                "structured_content"
            )
        if not isinstance(structured, dict):
            raise CatalogAdapterPolicyError(
                "浏览器快照缺少受控元素预览，工具契约已阻断。"
            )
        elements = structured.get("elements")
        if not isinstance(elements, list) or len(elements) > 100:
            raise CatalogAdapterPolicyError(
                "浏览器快照元素列表不符合固定契约。"
            )
        safe: dict[str, dict[str, str]] = {}
        sensitive = re.compile(
            r"password|passcode|secret|token|api[ _-]?key|credit|cvv|cvc|"
            r"otp|login|sign[ -]?in|oauth|密码|口令|令牌|密钥|验证码|信用卡|登录",
            re.IGNORECASE,
        )
        for item in elements:
            if not isinstance(item, dict):
                raise CatalogAdapterPolicyError("浏览器快照元素项无效。")
            ref = str(item.get("ref") or "").strip()
            role = " ".join(str(item.get("role") or "element").split())
            label = " ".join(str(item.get("label") or "").split())
            page_digest = str(item.get("page_digest") or "").strip().lower()
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", ref)
                is None
                or not role
                or not label
                or len(role) > 48
                or len(label) > 120
                or page_digest != snapshot.page_digest
                or any(ord(character) < 32 for character in role + label)
                or ref in safe
            ):
                raise CatalogAdapterPolicyError("浏览器快照元素项违反固定契约。")
            if sensitive.search(f"{role} {label}"):
                continue
            safe[ref] = {
                "role": role,
                "label": label,
                "page_digest": page_digest,
            }
        if len(
            json.dumps(
                safe,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ) > 32 * 1024:
            raise CatalogAdapterPolicyError(
                "浏览器快照元素摘要超过固定大小上限。"
            )
        self._browser_elements[self._scope_key(manifest.project_id)] = safe

    def _register_browser_artifact(
        self,
        manifest: CatalogAdapterManifest,
        *,
        session_id: str,
        payload: dict[str, Any],
        snapshot: CatalogBrowserSnapshot,
    ) -> dict[str, Any]:
        policy = manifest.browser_policy
        if policy is None:
            raise CatalogAdapterPolicyError("浏览器产物策略不存在。")
        raw = payload.get("raw")
        structured = None
        structured_key = ""
        if isinstance(raw, dict):
            if isinstance(raw.get("structuredContent"), dict):
                structured = raw["structuredContent"]
                structured_key = "structuredContent"
            elif isinstance(raw.get("structured_content"), dict):
                structured = raw["structured_content"]
                structured_key = "structured_content"
        if not isinstance(structured, dict):
            raise CatalogAdapterPolicyError("浏览器截图缺少结构化产物信息。")
        internal_id = str(structured.get("artifact_id") or "").strip()
        relative_path = str(structured.get("relative_path") or "").strip()
        sha256 = str(structured.get("sha256") or "").strip().lower()
        mime_type = str(structured.get("mime") or "").strip().lower()
        try:
            size_bytes = int(structured.get("size"))
        except (TypeError, ValueError) as exc:
            raise CatalogAdapterPolicyError("浏览器截图大小无效。") from exc
        if (
            re.fullmatch(r"browser_[0-9a-f]{32}", internal_id) is None
            or re.fullmatch(
                r"[0-9a-f]{32}/registered/browser_[0-9a-f]{32}\.png",
                relative_path,
            )
            is None
            or not relative_path.endswith(f"/{internal_id}.png")
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
            or mime_type != "image/png"
            or size_bytes < 1
            or size_bytes > policy.max_artifact_bytes
        ):
            raise CatalogAdapterPolicyError("浏览器截图产物违反固定契约。")
        staging_root = self.browser_artifact_staging_root.resolve(strict=False)
        candidate = staging_root.joinpath(*relative_path.split("/"))
        cursor = staging_root
        for component in relative_path.split("/"):
            cursor = cursor / component
            if cursor.is_symlink():
                raise CatalogAdapterPolicyError(
                    "浏览器截图产物路径包含符号链接。"
                )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(staging_root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise CatalogAdapterPolicyError("浏览器截图产物不存在或越界。") from exc
        if (
            candidate.is_symlink()
            or candidate.parent.is_symlink()
            or not resolved.is_file()
        ):
            raise CatalogAdapterPolicyError("浏览器截图产物类型不安全。")
        stat = resolved.stat()
        if stat.st_size != size_bytes or stat.st_nlink != 1:
            raise CatalogAdapterPolicyError("浏览器截图产物大小校验失败。")
        self._cleanup_expired_browser_artifacts()
        existing = [
            item
            for key, item in self._browser_artifacts.items()
            if key[:2] == (self.tenant_id, self.owner_id)
            and item.project_id == manifest.project_id
        ]
        if (
            len(existing) >= policy.max_artifacts_per_project
            or sum(item.size_bytes for item in existing) + size_bytes
            > policy.max_artifact_storage_bytes
        ):
            try:
                resolved.unlink()
            except OSError:
                pass
            raise CatalogAdapterPolicyError(
                "浏览器截图产物已达到每项目 50 张或 256 MiB 配额。"
            )
        now = time.time()
        public_id = f"mcpbart_{uuid.uuid4().hex}"
        trusted_relative_path = f"files/{public_id}.png"
        trusted_root = self.browser_artifact_root.resolve(strict=False)
        trusted_files = trusted_root / "files"
        trusted_files.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            trusted_files.chmod(0o700)
        except OSError:
            pass
        trusted_path = trusted_files / f"{public_id}.png"
        temporary_path = trusted_files / f".{public_id}.{uuid.uuid4().hex}.tmp"
        target_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        source_descriptor = -1
        try:
            source_descriptor = os.open(
                resolved,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            digest = hashlib.sha256()
            copied = 0
            magic = b""
            with os.fdopen(target_descriptor, "wb") as target, os.fdopen(
                source_descriptor,
                "rb",
            ) as source:
                target_descriptor = -1
                source_descriptor = -1
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    if not magic:
                        magic = chunk[:8]
                    copied += len(chunk)
                    digest.update(chunk)
                    target.write(chunk)
                source_stat = os.fstat(source.fileno())
                target.flush()
                os.fsync(target.fileno())
            if (
                magic != b"\x89PNG\r\n\x1a\n"
                or copied != size_bytes
                or source_stat.st_size != size_bytes
                or source_stat.st_nlink != 1
                or digest.hexdigest() != sha256
            ):
                raise CatalogAdapterPolicyError(
                    "浏览器截图在隔离复制期间发生变化或校验失败。"
                )
            os.replace(temporary_path, trusted_path)
            trusted_path.chmod(0o600)
            trusted_stat = trusted_path.stat()
            trusted_digest = hashlib.sha256()
            with trusted_path.open("rb") as trusted:
                trusted_magic = trusted.read(8)
                trusted.seek(0)
                for chunk in iter(lambda: trusted.read(1024 * 1024), b""):
                    trusted_digest.update(chunk)
            if (
                trusted_magic != b"\x89PNG\r\n\x1a\n"
                or trusted_stat.st_size != size_bytes
                or trusted_stat.st_nlink != 1
                or trusted_digest.hexdigest() != sha256
            ):
                raise CatalogAdapterPolicyError(
                    "浏览器截图可信副本复核失败。"
                )
        except Exception as exc:
            if target_descriptor >= 0:
                try:
                    os.close(target_descriptor)
                except OSError:
                    pass
            if source_descriptor >= 0:
                try:
                    os.close(source_descriptor)
                except OSError:
                    pass
            try:
                temporary_path.unlink(missing_ok=True)
                trusted_path.unlink(missing_ok=True)
                resolved.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(exc, CatalogAdapterPolicyError):
                raise
            raise CatalogAdapterPolicyError(
                "浏览器截图无法导入服务端可信存储。"
            ) from exc
        try:
            resolved.unlink()
        except OSError:
            pass
        artifact = CatalogBrowserArtifact(
            artifact_id=public_id,
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
            project_id=manifest.project_id,
            session_id=session_id,
            browser_generation=snapshot.generation,
            relative_path=trusted_relative_path,
            name=f"browser-screenshot-{public_id[-8:]}.png",
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            created_at=now,
            expires_at=now + policy.artifact_ttl_seconds,
        )
        self._browser_artifacts[self._artifact_key(public_id)] = artifact
        try:
            self._persist_browser_artifact_index()
        except Exception:
            self._browser_artifacts.pop(self._artifact_key(public_id), None)
            trusted_path.unlink(missing_ok=True)
            raise
        public = self._public_browser_artifact(artifact)
        assert isinstance(raw, dict)
        raw[structured_key] = {
            key: public[key]
            for key in (
                "artifact_id",
                "name",
                "mime_type",
                "size_bytes",
                "sha256",
                "created_at",
                "expires_at",
                "download_url",
            )
        }
        return public

    def list_browser_artifacts(self, project_id: str) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        if manifest.browser_policy is None:
            raise CatalogAdapterPolicyError("该目录条目不支持浏览器产物。")
        self._cleanup_expired_browser_artifacts()
        items = [
            self._public_browser_artifact(item)
            for key, item in self._browser_artifacts.items()
            if key[:2] == (self.tenant_id, self.owner_id)
            and item.project_id == manifest.project_id
        ]
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return {
            "project_id": manifest.project_id,
            "items": items,
            "total": len(items),
        }

    def browser_artifact_download(
        self,
        project_id: str,
        artifact_id: str,
    ) -> tuple[CatalogBrowserArtifact, Path]:
        manifest = self.get_manifest(project_id)
        if manifest.browser_policy is None:
            raise CatalogAdapterPolicyError("该目录条目不支持浏览器产物。")
        self._cleanup_expired_browser_artifacts()
        artifact = self._browser_artifacts.get(self._artifact_key(artifact_id))
        if artifact is None or artifact.project_id != manifest.project_id:
            raise CatalogAdapterNotFoundError("浏览器产物不存在或已经过期。")
        return artifact, self._browser_artifact_path(artifact)

    def delete_browser_artifact(
        self,
        project_id: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        if manifest.browser_policy is None:
            raise CatalogAdapterPolicyError("该目录条目不支持浏览器产物。")
        key = self._artifact_key(artifact_id)
        artifact = self._browser_artifacts.get(key)
        if artifact is None or artifact.project_id != manifest.project_id:
            raise CatalogAdapterNotFoundError("浏览器产物不存在或已经过期。")
        self._delete_browser_artifact_file(artifact)
        self._browser_artifacts.pop(key, None)
        self._persist_browser_artifact_index()
        return {
            "ok": True,
            "project_id": manifest.project_id,
            "artifact_id": artifact.artifact_id,
        }

    def _cleanup_expired_browser_artifacts(self) -> None:
        now = time.time()
        changed = False
        for key, artifact in list(self._browser_artifacts.items()):
            if key[:2] != (self.tenant_id, self.owner_id):
                continue
            if artifact.expires_at <= now:
                self._delete_browser_artifact_file(artifact)
                self._browser_artifacts.pop(key, None)
                changed = True
        if changed:
            self._persist_browser_artifact_index()

    def _browser_artifact_path(self, artifact: CatalogBrowserArtifact) -> Path:
        root = self.browser_artifact_root.resolve(strict=False)
        candidate = root.joinpath(*artifact.relative_path.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise CatalogAdapterNotFoundError(
                "浏览器产物不存在或已经过期。"
            ) from exc
        stat = resolved.stat()
        if (
            candidate.is_symlink()
            or candidate.parent.is_symlink()
            or not resolved.is_file()
            or stat.st_nlink != 1
        ):
            raise CatalogAdapterPolicyError("浏览器产物路径不安全。")
        if stat.st_size != artifact.size_bytes:
            raise CatalogAdapterPolicyError("浏览器产物已经变化，下载已阻断。")
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                raise CatalogAdapterPolicyError("浏览器产物 PNG 签名无效。")
            handle.seek(0)
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != artifact.sha256:
            raise CatalogAdapterPolicyError("浏览器产物摘要已经变化，下载已阻断。")
        return resolved

    def _delete_browser_artifact_file(
        self,
        artifact: CatalogBrowserArtifact,
    ) -> None:
        try:
            path = self._browser_artifact_path(artifact)
        except (CatalogAdapterNotFoundError, CatalogAdapterPolicyError):
            return
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _public_browser_artifact(
        artifact: CatalogBrowserArtifact,
    ) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "name": artifact.name,
            "mime_type": artifact.mime_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "created_at": artifact.created_at,
            "expires_at": artifact.expires_at,
            "download_url": (
                f"/api/mcp/catalog/{artifact.project_id}/browser-artifacts/"
                f"{artifact.artifact_id}/download"
            ),
        }

    def _load_browser_artifact_index(self) -> None:
        path = self.browser_artifact_index_path
        if not path.exists():
            self._scavenge_browser_artifact_orphans()
            return
        if path.is_symlink() or not path.is_file():
            raise CatalogAdapterPolicyError("浏览器产物索引路径不安全。")
        stat = path.stat()
        if stat.st_nlink != 1 or stat.st_size > 1024 * 1024:
            raise CatalogAdapterPolicyError("浏览器产物索引违反固定上限。")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogAdapterPolicyError("浏览器产物索引损坏。") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "items"}
            or payload.get("version") != 1
            or not isinstance(payload.get("items"), list)
            or len(payload["items"]) > 2_000
        ):
            raise CatalogAdapterPolicyError("浏览器产物索引 Schema 无效。")
        loaded: dict[tuple[str, str, str], CatalogBrowserArtifact] = {}
        expected_keys = set(CatalogBrowserArtifact.__dataclass_fields__)
        now = time.time()
        changed = False
        for raw in payload["items"]:
            if not isinstance(raw, dict) or set(raw) != expected_keys:
                raise CatalogAdapterPolicyError("浏览器产物索引记录无效。")
            try:
                artifact = CatalogBrowserArtifact(**raw)
            except TypeError as exc:
                raise CatalogAdapterPolicyError("浏览器产物索引记录无效。") from exc
            if (
                re.fullmatch(r"mcpbart_[0-9a-f]{32}", artifact.artifact_id)
                is None
                or not artifact.tenant_id
                or len(artifact.tenant_id) > 120
                or not artifact.owner_id
                or len(artifact.owner_id) > 120
                or artifact.project_id not in self.manifests
                or self.manifests[artifact.project_id].browser_policy is None
                or re.fullmatch(
                    r"files/mcpbart_[0-9a-f]{32}\.png",
                    artifact.relative_path,
                )
                is None
                or artifact.relative_path
                != f"files/{artifact.artifact_id}.png"
                or artifact.mime_type != "image/png"
                or artifact.size_bytes < 1
                or artifact.size_bytes > 32 * 1024 * 1024
                or re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None
                or not math.isfinite(artifact.created_at)
                or not math.isfinite(artifact.expires_at)
                or artifact.expires_at <= artifact.created_at
            ):
                raise CatalogAdapterPolicyError("浏览器产物索引记录违反安全契约。")
            key = (artifact.tenant_id, artifact.owner_id, artifact.artifact_id)
            if key in loaded:
                raise CatalogAdapterPolicyError("浏览器产物索引包含重复记录。")
            if artifact.expires_at <= now:
                self._delete_browser_artifact_file(artifact)
                changed = True
                continue
            self._browser_artifact_path(artifact)
            loaded[key] = artifact
        self._browser_artifacts = loaded
        if self._scavenge_browser_artifact_orphans():
            changed = True
        if changed:
            self._persist_browser_artifact_index()

    def _persist_browser_artifact_index(self) -> None:
        path = self.browser_artifact_index_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        payload = json.dumps(
            {
                "version": 1,
                "items": [
                    asdict(item)
                    for _, item in sorted(
                        self._browser_artifacts.items(),
                        key=lambda pair: pair[0],
                    )
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 1024 * 1024:
            raise CatalogAdapterPolicyError("浏览器产物索引超过固定上限。")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _scavenge_browser_artifact_orphans(self) -> bool:
        changed = False
        referenced = {
            item.relative_path for item in self._browser_artifacts.values()
        }
        trusted_files = self.browser_artifact_root / "files"
        if trusted_files.is_dir() and not trusted_files.is_symlink():
            for candidate in trusted_files.iterdir():
                if candidate.name.startswith(".") and candidate.name.endswith(".tmp"):
                    try:
                        candidate.unlink()
                        changed = True
                    except OSError:
                        pass
                    continue
                relative = f"files/{candidate.name}"
                if (
                    re.fullmatch(r"mcpbart_[0-9a-f]{32}\.png", candidate.name)
                    and relative not in referenced
                ):
                    try:
                        candidate.unlink()
                        changed = True
                    except OSError:
                        pass
        staging = self.browser_artifact_staging_root
        if staging.is_dir() and not staging.is_symlink():
            for candidate in staging.glob("*/registered/browser_*.png"):
                relative = candidate.relative_to(staging).as_posix()
                if re.fullmatch(
                    r"[0-9a-f]{32}/registered/browser_[0-9a-f]{32}\.png",
                    relative,
                ):
                    try:
                        candidate.unlink()
                        changed = True
                    except OSError:
                        pass
        return changed

    def _create_approval(
        self,
        manifest: CatalogAdapterManifest,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        scope_key = self._scope_key(manifest.project_id)
        configuration = self._configurations.get(scope_key)
        bound_workspace_id = str(
            workspace_id or (configuration.workspace_id if configuration else "") or ""
        )
        frozen_arguments, canonical = self._freeze_arguments(arguments)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        context_kind: CatalogApprovalContextKind
        workspace_manifest_sha256 = ""
        configuration_revision = ""
        configuration_digest = ""
        credential_snapshot_digest = ""
        account_snapshot_digest = ""
        browser_snapshot_digest = ""
        tool_schema_sha256 = ""
        target_preview: dict[str, Any] = {}
        if bound_workspace_id:
            if self.workspace_store is None:
                raise CatalogAdapterPolicyError("MCP 文件工作区当前不可用。")
            workspace = self.workspace_store.require_sealed(
                manifest.project_id,
                bound_workspace_id,
                tenant_id=self.tenant_id,
            )
            context_kind = "workspace"
            workspace_manifest_sha256 = workspace.manifest_sha256
            summary = self._approval_summary(
                tool_name,
                frozen_arguments,
                workspace.display_name,
            )
        elif manifest.saas_policy is not None:
            configuration_snapshot = self._configuration_snapshots.get(scope_key)
            account_snapshot = self._account_snapshots.get(scope_key)
            if configuration_snapshot is None or account_snapshot is None:
                raise CatalogAdapterPolicyError("SaaS 账号尚未完成配置与预检。")
            context_kind = "remote-resource"
            configuration_revision = configuration_snapshot.revision
            configuration_digest = configuration_snapshot.digest
            credential_snapshot_digest = self._credential_snapshot_digest(
                self._credential_snapshots.get(scope_key, {})
            )
            account_snapshot_digest = account_snapshot.digest
            tool_schema_sha256 = account_snapshot.tool_schema_sha256
            target_preview = self._target_preview(
                manifest,
                tool_name,
                frozen_arguments,
            )
            summary = str(target_preview["impact"])
        elif manifest.browser_policy is not None:
            browser_snapshot = self._browser_snapshots.get(scope_key)
            if browser_snapshot is None or browser_snapshot.status != "active":
                raise CatalogAdapterPolicyError(
                    "浏览器会话尚未完成预检或已经污染，请重新连接。"
                )
            context_kind = "browser-session"
            browser_snapshot_digest = browser_snapshot.digest
            tool_schema_sha256 = manifest.browser_policy.tool_schema_sha256
            target_preview = self._browser_target_preview(
                manifest,
                tool_name,
                frozen_arguments,
                browser_snapshot,
            )
            summary = str(target_preview["impact"])
        else:
            raise CatalogAdapterPolicyError("该操作缺少有效的受控审批上下文。")
        policy = manifest.tool_policies.get(tool_name)
        if policy is None:
            raise CatalogAdapterPolicyError("工具审批策略不存在。")
        approval = CatalogApproval(
            approval_id=f"mcpauth_{uuid.uuid4().hex}",
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
            project_id=manifest.project_id,
            session_id=session_id,
            context_kind=context_kind,
            workspace_id=bound_workspace_id,
            workspace_manifest_sha256=workspace_manifest_sha256,
            tool_name=tool_name,
            arguments=frozen_arguments,
            argument_digest=digest,
            summary=summary,
            expires_at=time.time() + 300,
            configuration_revision=configuration_revision,
            configuration_digest=configuration_digest,
            credential_snapshot_digest=credential_snapshot_digest,
            account_snapshot_digest=account_snapshot_digest,
            browser_snapshot_digest=browser_snapshot_digest,
            tool_schema_sha256=tool_schema_sha256,
            tool_policy_digest=self._tool_policy_digest(policy),
            idempotency_key=f"mcpidem_{uuid.uuid4().hex}",
            target_preview=target_preview,
        )
        self._approvals[self._approval_key(approval.approval_id)] = approval
        return {
            "code": "approval_required",
            "message": (
                "该浏览器操作会修改临时页面状态；参数和页面版本已由服务端冻结，请确认后执行一次。"
                if context_kind == "browser-session"
                else "该操作会修改持久状态；参数和账号上下文已由服务端冻结，请确认后执行。"
            ),
            "approval_id": approval.approval_id,
            "context_kind": approval.context_kind,
            "summary": approval.summary,
            "argument_digest": approval.argument_digest,
            "idempotency_key": approval.idempotency_key,
            "target_preview": approval.target_preview or None,
            "expires_at": approval.expires_at,
        }

    def _browser_target_preview(
        self,
        manifest: CatalogAdapterManifest,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot: CatalogBrowserSnapshot,
    ) -> dict[str, Any]:
        action_labels = {
            "navigate_page": "导航 Chrome DevTools 页面",
            "click": "点击 Chrome DevTools 页面元素",
            "fill": "填写 Chrome DevTools 页面字段",
            "browser_navigate": "导航 Playwright 页面",
            "browser_click": "点击 Playwright 页面元素",
            "browser_fill_form": "填写 Playwright 页面字段",
        }
        target_origin = snapshot.current_origin
        target_path = ""
        raw_url = arguments.get("url")
        if isinstance(raw_url, str) and raw_url:
            parsed = urlsplit(raw_url)
            if (
                len(raw_url) > 16_384
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise CatalogAdapterPolicyError(
                    "浏览器导航 URL 不符合受控公网策略。"
                )
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise CatalogAdapterPolicyError(
                    "浏览器导航 URL 不能使用 IP 字面量。"
                )
            if "." not in host or host.endswith(
                (".internal", ".local", ".localhost", ".home.arpa")
            ):
                raise CatalogAdapterPolicyError(
                    "浏览器导航目标不是允许的公网主机名。"
                )
            try:
                port = parsed.port
            except ValueError as exc:
                raise CatalogAdapterPolicyError(
                    "浏览器导航 URL 端口无效。"
                ) from exc
            effective_port = port or (80 if parsed.scheme == "http" else 443)
            if (parsed.scheme, effective_port) not in {
                ("http", 80),
                ("https", 443),
            }:
                raise CatalogAdapterPolicyError(
                    "浏览器导航端口必须与 HTTP/HTTPS 默认端口严格匹配。"
                )
            if browser_query_has_sensitive_key(parsed.query):
                raise CatalogAdapterPolicyError(
                    "浏览器导航 URL 不接受 Token、签名或其他敏感查询参数。"
                )
            decoded_target = f"{parsed.path}?{parsed.query}"
            for _ in range(2):
                decoded_target = unquote(decoded_target)
            target_tokens = canonical_browser_login_tokens(decoded_target)
            host_tokens = canonical_browser_login_tokens(host, host=True)
            if (
                target_tokens & BROWSER_LOGIN_TOKENS
                or host_tokens & BROWSER_LOGIN_TOKENS
            ):
                raise CatalogAdapterPolicyError(
                    "浏览器登录或外站认证路径不在本批开放范围。"
                )
            target_origin = f"{parsed.scheme}://{host}"
            segments = [
                re.sub(r"[^A-Za-z0-9._~-]", "·", segment)[:32]
                for segment in parsed.path.split("/")
                if segment
            ][:3]
            if segments:
                target_path = "/" + "/".join(segments)
        target_label = (
            f"{target_origin}{target_path}"
            if target_origin
            else "尚未导航的临时页面"
        )
        changes: list[dict[str, str]] = []
        if isinstance(raw_url, str) and raw_url:
            changes.append({"field": "目标 Origin", "summary": target_label})
        element_refs: list[str] = []
        if isinstance(arguments.get("ref"), str):
            element_refs.append(str(arguments["ref"]))
        fields = arguments.get("fields")
        if isinstance(fields, list):
            element_refs.extend(
                str(item.get("ref") or "")
                for item in fields
                if isinstance(item, dict)
            )
        elements = self._browser_elements.get(
            self._scope_key(manifest.project_id),
            {},
        )
        for ref in element_refs:
            element = elements.get(ref)
            if (
                element is None
                or element.get("page_digest") != snapshot.page_digest
            ):
                raise CatalogAdapterPolicyError(
                    "页面元素引用不属于当前受控快照，请重新获取快照。"
                )
            changes.append(
                {
                    "field": element["role"],
                    "summary": element["label"],
                }
            )
        if tool_name in {"fill", "browser_fill_form"}:
            count = len(fields) if isinstance(fields, list) else 1
            changes.append({"field": "填写内容", "summary": f"{count} 个受控字段（内容不展示）"})
        action_label = action_labels.get(tool_name, f"执行 {tool_name}")
        return {
            "action_label": action_label,
            "resource": {
                "type": "临时浏览器页面",
                "label": target_label,
                "id_suffix": snapshot.page_digest[-8:],
            },
            "changes": changes,
            "content": None,
            "impact": (
                f"将在“{target_label}”执行{action_label}；页面状态可能变化，"
                "仅执行一次且不会自动重试。"
            ),
            "destructive": False,
        }

    @staticmethod
    def _approval_summary(
        tool_name: str,
        arguments: dict[str, Any],
        workspace_name: str,
    ) -> str:
        if tool_name == "__delete_workspace__":
            return f"永久删除持久工作区“{workspace_name}”及其中全部本地笔记和产物。"
        visible: list[str] = []
        for key in ("title", "note", "destination_folder", "artifact_name", "sheet_name"):
            value = arguments.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value):
                visible.append(f"{key}={str(value)[:120]}")
        for key in ("content", "data", "updates"):
            if key in arguments:
                value = arguments[key]
                size = len(value) if isinstance(value, (str, list, dict)) else 1
                visible.append(f"{key}={size} 项/字符")
        detail = "，".join(visible) if visible else "参数已由服务端冻结"
        return f"在工作区“{workspace_name}”执行 {tool_name}：{detail}。"

    @classmethod
    def _freeze_arguments(
        cls,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        cls._validate_json_value(arguments)
        canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(canonical.encode("utf-8")) > 128 * 1024:
            raise CatalogAdapterPolicyError("工具参数超过 128 KiB 上限。")
        return json.loads(canonical), canonical

    @classmethod
    def _validate_json_value(cls, value: Any, *, depth: int = 0) -> None:
        if depth > 8:
            raise CatalogAdapterPolicyError("工具参数嵌套层级超过上限。")
        if value is None or isinstance(value, (str, bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise CatalogAdapterPolicyError("工具参数不能包含非有限数字。")
            return
        if isinstance(value, list):
            if len(value) > 2_000:
                raise CatalogAdapterPolicyError("工具参数数组项目过多。")
            for child in value:
                cls._validate_json_value(child, depth=depth + 1)
            return
        if isinstance(value, dict):
            if len(value) > 500:
                raise CatalogAdapterPolicyError("工具参数字段过多。")
            for raw_key, child in value.items():
                key = str(raw_key)
                if not key or len(key) > 160 or key.startswith("__modelmirror_"):
                    raise CatalogAdapterPolicyError("工具参数包含保留或无效字段。")
                cls._validate_json_value(child, depth=depth + 1)
            return
        raise CatalogAdapterPolicyError("工具参数必须是可验证的 JSON 值。")

    @staticmethod
    def _sha256_json(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _snapshot_configuration(
        cls,
        configuration: CatalogConfigurationRequest,
    ) -> CatalogConfigurationSnapshot:
        digest = cls._sha256_json(
            {
                "settings": configuration.settings,
                "credential_bindings": configuration.credential_bindings,
                "workspace_id": configuration.workspace_id,
            }
        )
        return CatalogConfigurationSnapshot(
            revision=f"mcpcfg_{uuid.uuid4().hex}",
            digest=digest,
        )

    @classmethod
    def _credential_snapshot_digest(
        cls,
        snapshots: dict[str, tuple[str, float]],
    ) -> str:
        return cls._sha256_json(
            [
                {"slot": slot, "credential_id": value[0], "updated_at": value[1]}
                for slot, value in sorted(snapshots.items())
            ]
        )

    @classmethod
    def _tool_schema_digest(cls, tools: list[Tool]) -> str:
        return cls._sha256_json(
            [
                {
                    "name": tool.name,
                    "inputSchema": tool.inputSchema,
                }
                for tool in sorted(tools, key=lambda item: item.name)
            ]
        )

    @classmethod
    def _tool_policy_digest(cls, policy: CatalogToolPolicy) -> str:
        return cls._sha256_json(asdict(policy))

    def _target_preview(
        self,
        manifest: CatalogAdapterManifest,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        configuration = self._configurations.get(self._scope_key(manifest.project_id))
        settings = configuration.settings if configuration is not None else {}
        action_labels = {
            "create_record": "创建 Airtable 记录",
            "update_record": "更新 Airtable 记录",
            "create_task": "创建 Asana 任务",
            "update_task": "更新 Asana 任务",
            "add_comment": "添加 Asana 评论",
            "create_issue": "创建 GitLab Issue",
            "update_issue": "更新 GitLab Issue",
            "add_issue_note": "添加 GitLab Issue 评论",
            "create_page": "创建 Notion 页面",
            "update_page_properties": "更新 Notion 页面属性",
        }
        resource_types = {
            "airtable-mcp": "Airtable Base",
            "asana-mcp": "Asana Project",
            "gitlab-mcp": "GitLab Project",
            "notion-mcp-server": "Notion Data Source",
        }
        scope_keys = {
            "airtable-mcp": "base_id",
            "asana-mcp": "project_gid",
            "gitlab-mcp": "project_id",
            "notion-mcp-server": "data_source_id",
        }
        target_keys = (
            "record_id",
            "task_gid",
            "issue_iid",
            "page_id",
            "table_id",
        )
        scope_value = str(settings.get(scope_keys.get(manifest.project_id, ""), ""))
        target_value = next(
            (
                str(arguments[key])
                for key in target_keys
                if key in arguments and str(arguments[key])
            ),
            scope_value,
        )
        id_suffix = target_value[-6:] if target_value else ""
        content_keys = {
            "content", "body", "comment", "text", "description", "notes",
            "fields", "properties", "updates",
        }
        changes: list[dict[str, str]] = []
        content_values: list[Any] = []
        for key, value in arguments.items():
            if key in content_keys:
                content_values.append(value)
                continue
            if key in target_keys:
                continue
            if len(changes) >= 8:
                break
            if isinstance(value, (str, int, float, bool)):
                summary = str(value)[:120]
            elif isinstance(value, (list, dict)):
                summary = f"{len(value)} 项"
            else:
                continue
            changes.append({"field": key[:80], "summary": summary})
        content: dict[str, Any] | None = None
        if content_values:
            encoded = json.dumps(
                content_values,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            content = {
                "bytes": len(encoded),
                "sha256_prefix": hashlib.sha256(encoded).hexdigest()[:12],
            }
        action_label = action_labels.get(tool_name, f"执行 {tool_name}")
        resource_type = resource_types.get(manifest.project_id, "远程资源")
        return {
            "action_label": action_label,
            "resource": {
                "type": resource_type,
                "label": f"{resource_type} …{scope_value[-6:]}" if scope_value else resource_type,
                "id_suffix": id_suffix,
            },
            "changes": changes,
            "content": content,
            "impact": f"将在已绑定的 {resource_type} 中{action_label}；仅执行一次，不自动重试。",
            "destructive": False,
        }

    def _revoke_approvals(self, project_id: str) -> None:
        for approval_key, approval in list(self._approvals.items()):
            if (
                approval.tenant_id == self.tenant_id
                and approval.owner_id == self.owner_id
                and approval.project_id == project_id
            ):
                self._approvals.pop(approval_key, None)

    def list_workspaces(self, project_id: str) -> dict[str, Any]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        items = self.workspace_store.list(
            manifest.project_id,
            tenant_id=self.tenant_id,
        )
        return {
            "project_id": manifest.project_id,
            "items": [self.workspace_store.payload(item) for item in items],
            "total": len(items),
        }

    def create_workspace(
        self,
        project_id: str,
        request: CatalogWorkspaceCreateRequest,
    ) -> dict[str, Any]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        item = self.workspace_store.create(
            manifest.project_id,
            display_name=request.display_name,
            tenant_id=self.tenant_id,
        )
        return self.workspace_store.payload(item)

    def add_workspace_upload(
        self,
        project_id: str,
        workspace_id: str,
        *,
        filename: str,
        relative_path: str,
        content: bytes,
    ) -> list[dict[str, Any]]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        items = self.workspace_store.add_upload(
            manifest.project_id,
            workspace_id,
            filename=filename,
            relative_path=relative_path,
            content=content,
            tenant_id=self.tenant_id,
        )
        return [asdict(item) for item in items]

    def seal_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        item = self.workspace_store.seal(
            manifest.project_id,
            workspace_id,
            tenant_id=self.tenant_id,
        )
        return self.workspace_store.payload(item)

    def get_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        item = self.workspace_store.get(
            manifest.project_id,
            workspace_id,
            tenant_id=self.tenant_id,
        )
        return self.workspace_store.payload(item)

    async def delete_workspace(
        self,
        project_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        item = self.workspace_store.get(
            manifest.project_id,
            workspace_id,
            tenant_id=self.tenant_id,
        )
        scope_key = self._scope_key(manifest.project_id)
        if item.persistent:
            raise CatalogApprovalRequiredError(
                self._create_approval(
                    manifest,
                    session_id=self._sessions.get(scope_key, "workspace-management"),
                    tool_name="__delete_workspace__",
                    arguments={"workspace_id": workspace_id},
                    workspace_id=workspace_id,
                )
            )
        if self._configurations.get(scope_key, CatalogConfigurationRequest()).workspace_id == workspace_id:
            if scope_key in self._sessions:
                await self.disconnect(manifest.project_id)
            self._configurations.pop(scope_key, None)
            self._preflight_status.pop(scope_key, None)
        self.workspace_store.delete(
            manifest.project_id,
            workspace_id,
            tenant_id=self.tenant_id,
        )
        return {"ok": True, "project_id": manifest.project_id, "workspace_id": workspace_id}

    def artifact_download(
        self,
        project_id: str,
        workspace_id: str,
        artifact_id: str,
    ) -> tuple[Any, Path]:
        manifest = self._require_file_workspace(project_id)
        assert self.workspace_store is not None
        return self.workspace_store.artifact_path(
            manifest.project_id,
            workspace_id,
            artifact_id,
            tenant_id=self.tenant_id,
        )

    def _require_file_workspace(self, project_id: str) -> CatalogAdapterManifest:
        manifest = self.get_manifest(project_id)
        if manifest.workspace_policy is None or manifest.project_id not in FILE_PROJECTS:
            raise CatalogAdapterPolicyError("该目录条目不支持受控文件工作区。")
        if manifest.availability != "ready" or not manifest.feature_enabled:
            raise CatalogAdapterUnavailableError("该文件适配器当前不可用。")
        if self.workspace_store is None:
            raise CatalogAdapterUnavailableError("MCP 文件工作区服务当前不可用。")
        return manifest

    async def clear_sessions(self) -> None:
        async with self._lock:
            tenant_keys = [
                key
                for key in self._sessions
                if key[:2] == (self.tenant_id, self.owner_id)
            ]
            sessions = [
                (key[2], self._sessions.pop(key))
                for key in tenant_keys
            ]
            for key in tenant_keys:
                self._credential_snapshots.pop(key, None)
                self._account_snapshots.pop(key, None)
                self._browser_snapshots.pop(key, None)
                self._browser_elements.pop(key, None)
                self._preflight_status.pop(key, None)
        for project_id, session_id in sessions:
            try:
                await self.manager.disconnect(
                    session_id,
                    session_owner=self._session_owner(project_id),
                )
            except Exception:
                pass
            await self.registry.unregister_session(session_id)
        for approval_key in list(self._approvals):
            if approval_key[:2] == (self.tenant_id, self.owner_id):
                self._approvals.pop(approval_key, None)

    async def unbind(
        self,
        project_id: str,
        request: CatalogUnbindRequest,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        if manifest.saas_policy is None or manifest.availability != "ready":
            raise CatalogAdapterPolicyError("该目录项目不支持 SaaS 账号解绑。")
        if request.revoke_credentials and self.credential_revoker is None:
            raise CatalogAdapterUnavailableError("目录凭据存储当前不可用。")
        scope_key = self._scope_key(manifest.project_id)
        if scope_key in self._unbinding_scopes:
            raise CatalogAdapterPolicyError("账号解绑已经在进行中。")
        self._unbinding_scopes.add(scope_key)
        approval_lock = self._approval_locks.setdefault(scope_key, asyncio.Lock())
        call_lock = self._call_locks.setdefault(scope_key, asyncio.Lock())
        disconnected = False
        revoked = 0
        try:
            # A connect that passed its initial tombstone check owns _lock for
            # its full preflight. Wait for it to publish or clean up before
            # inspecting the bound session and credentials.
            async with self._lock:
                pass
            async with approval_lock:
                project_ledgers = [
                    (ledger_key, entry)
                    for ledger_key, entry in self._execution_ledger.items()
                    if entry.tenant_id == self.tenant_id
                    and entry.owner_id == self.owner_id
                    and entry.project_id == manifest.project_id
                ]
                if any(entry.state == "started" for _, entry in project_ledgers):
                    raise CatalogAdapterPolicyError(
                        "远程写入仍在执行，当前不能解绑账号；请等待结果确定。"
                    )

                async with call_lock:
                    configuration = self._configurations.get(scope_key)
                    credential_ids = sorted(
                        set(configuration.credential_bindings.values())
                        if configuration is not None
                        else set()
                    )
                    # Stop the child before touching vault records. Calls that
                    # were queued behind call_lock then fail against a removed
                    # manager session instead of reusing the child's old token.
                    if scope_key in self._sessions:
                        try:
                            await self._disconnect_with_scope_locked(manifest)
                            disconnected = True
                        except MCPSessionNotFoundError:
                            self._sessions.pop(scope_key, None)

                    if request.revoke_credentials:
                        assert self.credential_revoker is not None
                        for credential_id in credential_ids:
                            self._credential_call(
                                self.credential_revoker,
                                credential_id,
                            )
                            revoked += 1

                    self._revoke_approvals(manifest.project_id)
                    self._configurations.pop(scope_key, None)
                    self._configuration_snapshots.pop(scope_key, None)
                    self._credential_snapshots.pop(scope_key, None)
                    self._account_snapshots.pop(scope_key, None)
                    self._credential_verification[scope_key] = "missing"
                    self._preflight_status[scope_key] = "awaiting-configuration"
                    for ledger_key, entry in project_ledgers:
                        if entry.state == "completed":
                            self._execution_ledger.pop(ledger_key, None)
        finally:
            self._unbinding_scopes.discard(scope_key)
        logger.info(
            "MCP catalog unbind project=%s disconnected=%s revoked_credentials=%d",
            manifest.project_id,
            disconnected,
            revoked,
        )
        return {
            "ok": True,
            "project_id": manifest.project_id,
            "disconnected": disconnected,
            "revoked_credentials": revoked,
        }

    def list_credentials(self, project_id: str) -> dict[str, Any]:
        manifest = self._require_credential_adapter(project_id)
        if self.credential_lister is None:
            raise CatalogAdapterUnavailableError("目录凭据存储当前不可用。")
        credentials = [
            self._public_credential(item)
            for item in self._credential_call(self.credential_lister)
            if getattr(item, "catalog_project_id", "") == manifest.project_id
        ]
        return {
            "project_id": manifest.project_id,
            "credentials": credentials,
        }

    def create_credential(
        self,
        project_id: str,
        request: CatalogCredentialCreateRequest,
    ) -> dict[str, Any]:
        manifest = self._require_credential_adapter(project_id)
        policies = {item.key: item for item in manifest.credential_policies}
        policy = policies.get(request.slot)
        if policy is None:
            raise CatalogAdapterPolicyError("凭据槽不属于当前目录项目。")
        if self.credential_creator is None:
            raise CatalogAdapterUnavailableError("目录凭据存储当前不可用。")
        record, _ = self._credential_call(
            self.credential_creator,
            name=request.name,
            value=request.value,
            kind="provider_key",
            catalog_project_id=manifest.project_id,
            catalog_slot=policy.key,
        )
        logger.info(
            "MCP catalog credential created project=%s slot=%s",
            manifest.project_id,
            policy.key,
        )
        return self._public_credential(record)

    async def revoke_credential(
        self,
        project_id: str,
        credential_id: str,
    ) -> dict[str, Any]:
        manifest = self._require_credential_adapter(project_id)
        if self.credential_validator is None or self.credential_revoker is None:
            raise CatalogAdapterUnavailableError("目录凭据存储当前不可用。")
        try:
            public = self._credential_call(
                self.credential_validator,
                credential_id,
            )
        except Exception as exc:
            raise CatalogAdapterPolicyError("目录凭据不存在或不可用。") from exc
        if getattr(public, "catalog_project_id", "") != manifest.project_id:
            raise CatalogAdapterPolicyError("不能撤销其他目录项目的凭据。")
        slot = str(getattr(public, "catalog_slot", ""))
        if slot not in manifest.credential_slots:
            raise CatalogAdapterPolicyError("凭据槽不属于当前目录项目。")
        scope_key = self._scope_key(manifest.project_id)
        configuration = self._configurations.get(scope_key)
        bound = bool(
            configuration
            and credential_id in configuration.credential_bindings.values()
        )
        if not bound:
            revoked = self._credential_call(self.credential_revoker, credential_id)
        else:
            if scope_key in self._unbinding_scopes:
                raise CatalogAdapterPolicyError("账号解绑或凭据撤销已经在进行中。")
            self._unbinding_scopes.add(scope_key)
            approval_lock = self._approval_locks.setdefault(
                scope_key,
                asyncio.Lock(),
            )
            call_lock = self._call_locks.setdefault(scope_key, asyncio.Lock())
            try:
                # Drain a connect that may already hold decrypted credentials.
                async with self._lock:
                    pass
                async with approval_lock:
                    if any(
                        entry.tenant_id == self.tenant_id
                        and entry.owner_id == self.owner_id
                        and entry.project_id == manifest.project_id
                        and entry.state == "started"
                        for entry in self._execution_ledger.values()
                    ):
                        raise CatalogAdapterPolicyError(
                            "远程写入仍在执行，当前不能撤销凭据。"
                        )
                    async with call_lock:
                        # The tombstone and call lock prevent any new use of
                        # the child while the local vault mutation is applied.
                        # If the vault rejects the revoke, session/config stay
                        # intact and become callable again after this method.
                        revoked = self._credential_call(
                            self.credential_revoker,
                            credential_id,
                        )
                        try:
                            if scope_key in self._sessions:
                                await self._disconnect_with_scope_locked(manifest)
                        finally:
                            self._revoke_approvals(manifest.project_id)
                            self._configurations.pop(scope_key, None)
                            self._configuration_snapshots.pop(scope_key, None)
                            self._credential_snapshots.pop(scope_key, None)
                            self._account_snapshots.pop(scope_key, None)
                            self._credential_verification[scope_key] = "missing"
                            self._preflight_status[scope_key] = "unverified"
            finally:
                self._unbinding_scopes.discard(scope_key)
        logger.info(
            "MCP catalog credential revoked project=%s slot=%s",
            manifest.project_id,
            slot,
        )
        return self._public_credential(revoked)

    async def forget_sessions(self, session_ids: list[str]) -> None:
        """Forget sessions already removed by the manager TTL cleanup."""

        cleaned = set(session_ids)
        if not cleaned:
            return
        async with self._lock:
            for scope_key, session_id in list(self._sessions.items()):
                if scope_key[:2] != (self.tenant_id, self.owner_id):
                    continue
                if session_id in cleaned:
                    self._sessions.pop(scope_key, None)
                    self._credential_snapshots.pop(scope_key, None)
                    self._account_snapshots.pop(scope_key, None)
                    self._browser_snapshots.pop(scope_key, None)
                    self._browser_elements.pop(scope_key, None)
                    self._preflight_status[scope_key] = "unverified"

    @staticmethod
    def _validate_setting(
        policy: CatalogSettingPolicy | None,
        value: str | int | float | bool,
    ) -> str | int | float | bool:
        if policy is None:
            if isinstance(value, str):
                clean = value.strip()
                if not clean or len(clean) > 253 or "://" in clean:
                    raise CatalogAdapterPolicyError("配置文本为空、过长或包含 URL。")
                return clean
            return value
        if policy.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise CatalogAdapterPolicyError(f"{policy.label} 必须是整数。")
            if policy.minimum is not None and value < policy.minimum:
                raise CatalogAdapterPolicyError(f"{policy.label} 小于允许下限。")
            if policy.maximum is not None and value > policy.maximum:
                raise CatalogAdapterPolicyError(f"{policy.label} 超过允许上限。")
            return value
        if not isinstance(value, str):
            raise CatalogAdapterPolicyError(f"{policy.label} 必须是文本。")
        clean = value.strip()
        if not clean or len(clean) > 253:
            raise CatalogAdapterPolicyError(f"{policy.label} 不能为空或超过长度上限。")
        if "://" in clean or "/" in clean or "@" in clean:
            raise CatalogAdapterPolicyError(f"{policy.label} 不能包含 URL、路径或用户信息。")
        if policy.kind == "enum":
            allowed = {item[0] for item in policy.options}
            if clean not in allowed:
                raise CatalogAdapterPolicyError(f"{policy.label} 不在允许选项中。")
        if policy.pattern and re.fullmatch(policy.pattern, clean) is None:
            raise CatalogAdapterPolicyError(f"{policy.label} 格式不正确。")
        if policy.kind == "hostname":
            host = clean.rstrip(".").lower()
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise CatalogAdapterPolicyError(f"{policy.label} 不能使用 IP 字面量。")
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise CatalogAdapterPolicyError(f"{policy.label} 无法安全规范化。") from exc
            if not re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                host,
            ):
                raise CatalogAdapterPolicyError(f"{policy.label} 不是有效主机名。")
            if policy.allowed_hostname_suffixes and not any(
                host.endswith(suffix) and host != suffix.lstrip(".")
                for suffix in policy.allowed_hostname_suffixes
            ):
                raise CatalogAdapterPolicyError(f"{policy.label} 不在固定域名范围内。")
            return host
        return clean

    async def _ensure_credentials_fresh(self, project_id: str) -> None:
        scope_key = self._scope_key(project_id)
        snapshots = self._credential_snapshots.get(scope_key)
        if not snapshots:
            return
        stale = False
        if self.credential_validator is None:
            stale = True
        else:
            for credential_id, expected_updated_at in snapshots.values():
                try:
                    public = self._credential_call(
                        self.credential_validator,
                        credential_id,
                    )
                    stale = (
                        getattr(public, "status", "") != "active"
                        or float(getattr(public, "updated_at", 0.0))
                        != expected_updated_at
                    )
                except Exception:
                    stale = True
                if stale:
                    break
        if not stale:
            return
        self._credential_verification[scope_key] = "unverified"
        self._preflight_status[scope_key] = "unverified"
        self._revoke_approvals(project_id)
        session_id = self._sessions.get(scope_key)
        if session_id is not None:
            try:
                await self.manager.disconnect(
                    session_id,
                    session_owner=self._session_owner(project_id),
                )
            finally:
                await self.registry.unregister_session(session_id)
                if self._sessions.get(scope_key) == session_id:
                    self._sessions.pop(scope_key, None)
                    self._credential_snapshots.pop(scope_key, None)
                    self._account_snapshots.pop(scope_key, None)
        else:
            self._credential_snapshots.pop(scope_key, None)
            self._account_snapshots.pop(scope_key, None)
        raise CatalogAdapterPolicyError(
            "绑定凭据已撤销、轮换或不可用，会话已断开；请重新保存配置并连接。"
        )

    def _require_executable(self, project_id: str) -> CatalogAdapterManifest:
        manifest = self.get_manifest(project_id)
        if not manifest.executable:
            raise CatalogAdapterUnavailableError(
                "该 MCP 尚未通过生产级适配验收，当前不可准备、连接或执行。"
            )
        return manifest

    def _require_credential_adapter(self, project_id: str) -> CatalogAdapterManifest:
        manifest = self._require_executable(project_id)
        if not manifest.credential_policies:
            raise CatalogAdapterPolicyError("该目录项目不使用加密凭据。")
        return manifest

    def project_for_session(self, session_id: str) -> str | None:
        """Return the catalog owner so generic call routes can fail closed."""

        clean = str(session_id or "")
        for scope_key, active_session_id in self._sessions.items():
            if scope_key[:2] != (self.tenant_id, self.owner_id):
                continue
            if active_session_id == clean:
                return scope_key[2]
        return None

    async def list_tools(self, project_id: str) -> dict[str, Any]:
        """List tools through the catalog owner boundary, never the generic route."""

        manifest = self._require_executable(project_id)
        scope_key = self._scope_key(manifest.project_id)
        session_id = self._sessions.get(scope_key)
        if session_id is None:
            raise MCPSessionNotFoundError(manifest.project_id)
        tools = await self.manager.list_tools(
            session_id,
            session_owner=self._session_owner(manifest.project_id),
        )
        names_match = {tool.name for tool in tools} == set(manifest.tool_policies)
        browser_schema_matches = (
            manifest.browser_policy is None
            or self._tool_schema_digest(tools)
            == manifest.browser_policy.tool_schema_sha256
        )
        if (
            not manifest.legacy_unrestricted_calls
            and (not names_match or not browser_schema_matches)
        ):
            if manifest.browser_policy is not None:
                await self._taint_browser_session(manifest)
            raise CatalogAdapterPolicyError(
                "目录工具清单与固定适配器策略不一致，工具发现已阻断。"
            )
        return {
            "project_id": manifest.project_id,
            "tools": [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in tools
            ],
        }

    async def browser_session_status(self, project_id: str) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        if manifest.browser_policy is None:
            raise CatalogAdapterPolicyError("该目录条目不是受控浏览器适配器。")
        scope_key = self._scope_key(manifest.project_id)
        session_id = self._sessions.get(scope_key)
        if session_id is None:
            snapshot = self._browser_snapshots.get(scope_key)
            if snapshot is None or snapshot.status != "tainted":
                payload = self._browser_session_payload(None)
            else:
                payload = self._browser_session_payload(snapshot)
            payload["reason"] = self._browser_disconnect_reasons.get(scope_key)
            return payload
        try:
            snapshot = await self._refresh_browser_snapshot(
                manifest,
                session_id=session_id,
            )
        except (MCPClientError, EOFError, BrokenPipeError, OSError) as exc:
            await self._forget_browser_session(manifest, reason="expired")
            payload = self._browser_session_payload(None)
            payload["reason"] = "expired"
            return payload
        except CatalogBrowserSessionExpiredError:
            await self._forget_browser_session(manifest, reason="expired")
            payload = self._browser_session_payload(None)
            payload["reason"] = "expired"
            return payload
        except CatalogAdapterPolicyError:
            await self._taint_browser_session(manifest)
            raise
        if snapshot.status == "active":
            self._preflight_status[scope_key] = "verified"
            self._browser_disconnect_reasons.pop(scope_key, None)
        payload = self._browser_session_payload(snapshot)
        payload["reason"] = None
        return payload

    async def _refresh_browser_snapshot(
        self,
        manifest: CatalogAdapterManifest,
        *,
        session_id: str,
    ) -> CatalogBrowserSnapshot:
        policy = manifest.browser_policy
        if policy is None:
            raise CatalogAdapterPolicyError("浏览器会话策略不存在。")
        result = await self.manager.call_tool(
            session_id,
            "browser_session_status",
            {},
            retry_on_failure=False,
            session_owner=self._session_owner(manifest.project_id),
        )
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        if bool(payload.get("isError") or payload.get("is_error")):
            raise CatalogAdapterPolicyError("浏览器会话状态预检失败。")
        structured = payload.get("structuredContent") or payload.get(
            "structured_content"
        )
        if not isinstance(structured, dict):
            for item in payload.get("content") or []:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                try:
                    candidate = json.loads(str(item.get("text") or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(candidate, dict):
                    structured = candidate
                    break
        if not isinstance(structured, dict):
            raise CatalogAdapterPolicyError("浏览器会话状态缺少结构化结果。")
        try:
            generation = str(structured["generation"]).strip().lower()
            page_revision = int(structured["page_revision"])
            page_digest = str(structured["page_digest"])
            current_origin = self._normalize_browser_origin(
                str(structured.get("current_origin") or "")
            )
            action_count = int(structured["action_count"])
            max_actions = int(structured["max_actions"])
            expires_at = float(structured["expires_at"])
            tainted = structured.get("tainted")
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogAdapterPolicyError("浏览器会话状态 Schema 无效。") from exc
        if (
            isinstance(tainted, bool) is False
            or re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                generation,
            )
            is None
            or page_revision < 0
            or re.fullmatch(r"[0-9a-f]{64}", page_digest) is None
            or action_count < 0
            or max_actions != policy.max_actions
            or action_count > max_actions
            or not math.isfinite(expires_at)
        ):
            raise CatalogAdapterPolicyError("浏览器会话状态违反固定安全契约。")
        if expires_at <= time.time():
            raise CatalogBrowserSessionExpiredError(
                "浏览器临时会话已经过期，请重新连接。"
            )
        previous = self._browser_snapshots.get(
            self._scope_key(manifest.project_id)
        )
        approved_hosts = set(
            previous.approved_hosts
            if previous is not None and previous.generation == generation
            else ()
        )
        if current_origin:
            host = str(urlsplit(current_origin).hostname or "").lower()
            if host:
                approved_hosts.add(host)
        snapshot = CatalogBrowserSnapshot(
            status="tainted" if tainted else "active",
            generation=generation,
            page_revision=page_revision,
            page_digest=page_digest,
            current_origin=current_origin,
            action_count=action_count,
            max_actions=max_actions,
            expires_at=expires_at,
            approved_hosts=tuple(sorted(approved_hosts)),
        )
        scope_key = self._scope_key(manifest.project_id)
        if previous is not None and (
            previous.generation != snapshot.generation
            or previous.page_revision != snapshot.page_revision
            or previous.page_digest != snapshot.page_digest
        ):
            self._browser_elements.pop(scope_key, None)
        self._browser_snapshots[scope_key] = snapshot
        if snapshot.status == "tainted":
            self._browser_elements.pop(scope_key, None)
            self._preflight_status[scope_key] = "failed"
        return snapshot

    @staticmethod
    def _normalize_browser_origin(value: str) -> str:
        if not value:
            return ""
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise CatalogAdapterPolicyError("浏览器会话返回了无效 Origin。")
        try:
            port = parsed.port
        except ValueError as exc:
            raise CatalogAdapterPolicyError("浏览器会话返回了无效 Origin 端口。") from exc
        if port is not None and port not in {80, 443}:
            raise CatalogAdapterPolicyError("浏览器会话 Origin 端口不在允许范围。")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise CatalogAdapterPolicyError(
                "浏览器会话 Origin 不能使用 IP 字面量。"
            )
        if "." not in host or host.endswith(
            (".internal", ".local", ".localhost", ".home.arpa")
        ):
            raise CatalogAdapterPolicyError(
                "浏览器会话 Origin 不是允许的公网主机名。"
            )
        default_port = 80 if parsed.scheme == "http" else 443
        suffix = f":{port}" if port is not None and port != default_port else ""
        return f"{parsed.scheme}://{host}{suffix}"

    @staticmethod
    def _browser_session_payload(
        snapshot: CatalogBrowserSnapshot | None,
    ) -> dict[str, Any]:
        if snapshot is None:
            return {
                "status": "disconnected",
                "generation": "",
                "page_revision": 0,
                "page_digest": "",
                "current_origin": "",
                "action_count": 0,
                "max_actions": 50,
                "expires_at": 0.0,
                "approved_hosts": [],
            }
        return {
            **asdict(snapshot),
            "approved_hosts": list(snapshot.approved_hosts),
        }

    def _connection_payload(
        self,
        manifest: CatalogAdapterManifest,
        session_id: str,
        tools: list[Tool],
    ) -> dict[str, Any]:
        return {
            "project_id": manifest.project_id,
            "session_id": session_id,
            "tools_count": len(tools),
            "credential_verification": (
                self._credential_verification.get(
                    self._scope_key(manifest.project_id),
                    "unverified",
                )
                if manifest.credential_policies
                else "not-required"
            ),
            "preflight_status": (
                self._preflight_status.get(
                    self._scope_key(manifest.project_id),
                    "unverified",
                )
                if (
                    manifest.database_policy is not None
                    or manifest.saas_policy is not None
                    or manifest.browser_policy is not None
                )
                else "not-applicable"
            ),
            "browser_session": (
                self._browser_session_payload(
                    self._browser_snapshots.get(
                        self._scope_key(manifest.project_id)
                    )
                )
                if manifest.browser_policy is not None
                else None
            ),
        }

    @staticmethod
    def _public_credential(value: Any) -> dict[str, Any]:
        allowed = {
            "credential_id",
            "name",
            "kind",
            "masked_value",
            "status",
            "catalog_project_id",
            "catalog_slot",
            "created_at",
            "updated_at",
        }
        if hasattr(value, "model_dump"):
            payload = value.model_dump(mode="json", exclude={"ciphertext"})
        elif isinstance(value, dict):
            payload = dict(value)
        else:
            payload = {
                key: getattr(value, key)
                for key in allowed
                if hasattr(value, key)
            }
        return {key: payload[key] for key in allowed if key in payload}

    @staticmethod
    def _public_install_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {"project_id", "install_type", "npm_package", "installed_at"}
        return {key: value[key] for key in allowed if key in value}

    @staticmethod
    def _serialize_call_result(
        result: CallToolResult,
        *,
        max_output_bytes: int,
    ) -> dict[str, Any]:
        payload = result.model_dump(mode="json", exclude_none=True)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > max(1, max_output_bytes):
            raise CatalogAdapterPolicyError(
                "工具返回超过该适配器允许的输出大小，结果已拒绝传回。"
            )
        content = payload.get("content")
        return {
            "content": content if isinstance(content, list) else [],
            "is_error": bool(payload.get("isError") or payload.get("is_error")),
            "raw": payload,
        }


router = APIRouter(tags=["mcp-catalog"])
_catalog_service: MCPCatalogService | None = None


def configure_mcp_catalog(service: MCPCatalogService) -> None:
    global _catalog_service
    _catalog_service = service


def get_mcp_catalog_service() -> MCPCatalogService:
    if _catalog_service is None:
        raise RuntimeError("MCP catalog service is not configured.")
    return _catalog_service


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, CatalogApprovalRequiredError):
        raise HTTPException(status_code=409, detail=exc.payload) from exc
    if isinstance(exc, CatalogUnknownOutcomeError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "unknown_outcome",
                "message": str(exc),
                "idempotency_key": exc.idempotency_key,
            },
        ) from exc
    if isinstance(exc, CatalogProviderRejectedError):
        raise HTTPException(
            status_code=429 if exc.reason == "rate_limited" else 409,
            detail={
                "code": (
                    "provider_rate_limited"
                    if exc.reason == "rate_limited"
                    else "provider_rejected"
                ),
                "message": str(exc),
                "idempotency_key": exc.idempotency_key,
            },
        ) from exc
    if isinstance(exc, CatalogBrowserPolicyRejectedError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "browser_policy_rejected",
                "reason": exc.reason,
                "message": str(exc),
                "idempotency_key": exc.idempotency_key,
            },
        ) from exc
    if isinstance(exc, CatalogAdapterNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, CatalogAdapterUnavailableError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, CatalogAdapterPolicyError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, CatalogWorkspaceNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (CatalogWorkspacePolicyError, CatalogWorkspaceError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, MCPSessionNotFoundError):
        raise HTTPException(status_code=404, detail="MCP 目录会话不存在或已断开。") from exc
    logger.warning(
        "MCP catalog operation failed error_type=%s",
        type(exc).__name__,
    )
    raise HTTPException(
        status_code=500,
        detail="MCP 目录操作失败，请检查适配器运行状态。",
    ) from exc


@router.get("/api/mcp/catalog/adapters")
async def list_catalog_adapters() -> dict[str, Any]:
    return get_mcp_catalog_service().list_adapters()


@router.get("/api/mcp/catalog/{project_id}/credentials")
async def list_catalog_credentials(project_id: str) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().list_credentials(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/credentials", status_code=201)
async def create_catalog_credential(
    project_id: str,
    request: CatalogCredentialCreateRequest,
) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().create_credential(project_id, request)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.delete("/api/mcp/catalog/{project_id}/credentials/{credential_id}")
async def revoke_catalog_credential(
    project_id: str,
    credential_id: str,
) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().revoke_credential(
            project_id,
            credential_id,
        )
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/prepare")
async def prepare_catalog_adapter(project_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().prepare(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.put("/api/mcp/catalog/{project_id}/configuration")
async def configure_catalog_adapter(
    project_id: str,
    request: CatalogConfigurationRequest,
) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().configure(project_id, request)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/unbind")
async def unbind_catalog_adapter(
    project_id: str,
    request: CatalogUnbindRequest,
) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().unbind(project_id, request)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/connect")
async def connect_catalog_adapter(project_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().connect(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.delete("/api/mcp/catalog/{project_id}/session")
async def disconnect_catalog_adapter(project_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().disconnect(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get("/api/mcp/catalog/{project_id}/tools")
async def list_catalog_tools(project_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().list_tools(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get("/api/mcp/catalog/{project_id}/browser-session")
async def get_catalog_browser_session(project_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().browser_session_status(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get("/api/mcp/catalog/{project_id}/browser-artifacts")
async def list_catalog_browser_artifacts(project_id: str) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().list_browser_artifacts(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get(
    "/api/mcp/catalog/{project_id}/browser-artifacts/{artifact_id}/download"
)
async def download_catalog_browser_artifact(
    project_id: str,
    artifact_id: str,
) -> FileResponse:
    try:
        artifact, path = get_mcp_catalog_service().browser_artifact_download(
            project_id,
            artifact_id,
        )
        return FileResponse(
            path,
            media_type=artifact.mime_type,
            filename=artifact.name,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.delete("/api/mcp/catalog/{project_id}/browser-artifacts/{artifact_id}")
async def delete_catalog_browser_artifact(
    project_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().delete_browser_artifact(
            project_id,
            artifact_id,
        )
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/tools/{tool_name}/call")
async def call_catalog_tool(
    project_id: str,
    tool_name: str,
    request: CatalogToolCallRequest,
) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().call_tool(
            project_id,
            tool_name,
            request.arguments,
        )
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/approvals/{approval_id}/confirm")
async def confirm_catalog_approval(project_id: str, approval_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().confirm_approval(project_id, approval_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.delete("/api/mcp/catalog/{project_id}/approvals/{approval_id}")
async def cancel_catalog_approval(project_id: str, approval_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().cancel_approval(project_id, approval_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get("/api/mcp/catalog/{project_id}/workspaces")
async def list_catalog_workspaces(project_id: str) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().list_workspaces(project_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/workspaces")
async def create_catalog_workspace(
    project_id: str,
    request: CatalogWorkspaceCreateRequest,
) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().create_workspace(project_id, request)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/workspaces/{workspace_id}/files")
async def upload_catalog_workspace_files(
    project_id: str,
    workspace_id: str,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] | None = Form(default=None),
) -> dict[str, Any]:
    try:
        paths = relative_paths or []
        uploaded: list[dict[str, Any]] = []
        for index, upload in enumerate(files):
            content = await upload.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise CatalogWorkspacePolicyError("单个上传文件不能超过 64 MiB。")
            uploaded.extend(
                get_mcp_catalog_service().add_workspace_upload(
                    project_id,
                    workspace_id,
                    filename=upload.filename or "upload",
                    relative_path=(
                        paths[index]
                        if index < len(paths) and paths[index]
                        else upload.filename or "upload"
                    ),
                    content=content,
                )
            )
        return {"workspace_id": workspace_id, "files": uploaded, "uploaded": len(uploaded)}
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.post("/api/mcp/catalog/{project_id}/workspaces/{workspace_id}/seal")
async def seal_catalog_workspace(project_id: str, workspace_id: str) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().seal_workspace(project_id, workspace_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get("/api/mcp/catalog/{project_id}/workspaces/{workspace_id}")
async def get_catalog_workspace(project_id: str, workspace_id: str) -> dict[str, Any]:
    try:
        return get_mcp_catalog_service().get_workspace(project_id, workspace_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.delete("/api/mcp/catalog/{project_id}/workspaces/{workspace_id}")
async def delete_catalog_workspace(project_id: str, workspace_id: str) -> dict[str, Any]:
    try:
        return await get_mcp_catalog_service().delete_workspace(project_id, workspace_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise


@router.get("/api/mcp/catalog/{project_id}/workspaces/{workspace_id}/artifacts/{artifact_id}/download")
async def download_catalog_artifact(
    project_id: str,
    workspace_id: str,
    artifact_id: str,
):
    try:
        artifact, path = get_mcp_catalog_service().artifact_download(
            project_id,
            workspace_id,
            artifact_id,
        )
        return FileResponse(path, media_type=artifact.content_type, filename=artifact.filename)
    except Exception as exc:
        _raise_http_error(exc)
        raise
