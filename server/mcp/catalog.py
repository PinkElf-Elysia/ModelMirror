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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ConfigDict, Field

from mcp.types import CallToolResult, Tool

try:
    from server.mcp.manager import (
        MCPClientManager,
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
    from mcp.manager import MCPClientManager, MCPInstaller, MCPSessionNotFoundError
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


AdapterAvailability = Literal["planned", "adapting", "ready", "blocked"]
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
CatalogApprovalContextKind = Literal["workspace", "remote-resource"]

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
    saas_policy: CatalogSaaSPolicy | None = None
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
            "saas_policy": (
                self.saas_policy.to_public()
                if self.saas_policy is not None
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
        ("等待断网、无宿主挂载的一次性代码执行沙箱验证。",),
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
            wave=5,
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

    if len(manifests) != 100:
        raise RuntimeError(f"MCP catalog must contain 100 adapters, got {len(manifests)}")
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


@dataclass(slots=True)
class CatalogExecutionLedgerEntry:
    approval_id: str
    tenant_id: str
    owner_id: str
    project_id: str
    idempotency_key: str
    state: Literal["started", "completed", "unknown", "rejected"]
    result: dict[str, Any] | None = None


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
            if manifest.database_policy is None and manifest.saas_policy is None:
                item["preflight_status"] = "not-applicable"
            elif manifest.availability == "blocked":
                item["preflight_status"] = "blocked"
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
        if manifest.credential_policies:
            self._credential_verification[scope_key] = "unverified"
        if manifest.database_policy is not None or manifest.saas_policy is not None:
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
                    return self._connection_payload(manifest, existing, tools)
                except MCPSessionNotFoundError:
                    self._sessions.pop(scope_key, None)

            if manifest.transport != "stdio" or not manifest.server_command:
                raise CatalogAdapterUnavailableError(
                    "该适配器尚未配置受控的可执行传输。"
                )
            if manifest.connection_kind == "sandboxed-stdio":
                environment: dict[str, str] = {}
                configuration = self._configurations.get(scope_key)
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
                if manifest.credential_policies or manifest.database_policy is not None:
                    assert configuration is not None
                    handshake_configuration: dict[str, Any] = {
                        "settings": configuration.settings,
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
                profile: dict[str, Any] = {
                    "transport": "stdio",
                    "server_command": list(manifest.server_command),
                    "network_policy": manifest.network_policy,
                    "reconnect_attempts": (
                        0
                        if manifest.credential_policies
                        or manifest.database_policy is not None
                        else 1
                    ),
                    "operation_timeout": manifest.operation_timeout,
                    "session_owner": session_owner,
                }
                if environment:
                    profile["environment"] = environment
                session_id = await self.manager.connect_profile(**profile)
                if manifest.credential_policies or manifest.database_policy is not None:
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
                if manifest.database_policy is not None or manifest.saas_policy is not None:
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
                if manifest.database_policy is not None or manifest.saas_policy is not None:
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
            if manifest.credential_policies:
                self._credential_snapshots[scope_key] = snapshots
            if account_snapshot is not None:
                self._account_snapshots[scope_key] = account_snapshot
            if manifest.database_policy is not None or manifest.saas_policy is not None:
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
        try:
            await self.manager.disconnect(
                session_id,
                session_owner=self._session_owner(manifest.project_id),
            )
        finally:
            await self.registry.unregister_session(session_id)
            async with self._lock:
                if self._sessions.get(scope_key) == session_id:
                    self._sessions.pop(scope_key, None)
                    self._credential_snapshots.pop(scope_key, None)
                    self._account_snapshots.pop(scope_key, None)
                    if (
                        manifest.database_policy is not None
                        or manifest.saas_policy is not None
                    ):
                        self._preflight_status[scope_key] = "unverified"
        self._revoke_approvals(manifest.project_id)
        logger.info("MCP catalog disconnect project=%s", manifest.project_id)
        return {"ok": True, "project_id": manifest.project_id}

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
            if policy.sensitive or policy.terminal or policy.effect == "terminal":
                raise CatalogAdapterPolicyError(
                    "该工具属于敏感或终止性操作，当前批次不允许执行。"
                )
            if policy.requires_approval:
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
            else:
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
            session_id = self._sessions.get(scope_key)
            if (
                approval.tool_name != "__delete_workspace__"
                and (not session_id or session_id != approval.session_id)
            ):
                raise CatalogAdapterPolicyError("MCP 会话已经变化，请重新发起操作。")
            approval.used = True
            self._approvals.pop(approval_key, None)
            if approval.context_kind == "remote-resource":
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
        if approval.context_kind != "remote-resource":
            return await self._execute_tool(
                manifest,
                session_id=session_id,
                tool_name=approval.tool_name,
                arguments=approval.arguments,
            )
        assert ledger is not None
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
    ) -> dict[str, Any]:

        scope_key = self._scope_key(manifest.project_id)
        started_at = time.monotonic()
        tool_policy = manifest.tool_policies.get(tool_name)
        retry_on_failure = not (
            manifest.saas_policy is not None
            and tool_policy is not None
            and tool_policy.effect == "state-write"
        )
        try:
            call_lock = self._call_locks.setdefault(scope_key, asyncio.Lock())
            async with call_lock:
                if scope_key in self._unbinding_scopes:
                    raise CatalogAdapterPolicyError(
                        "账号解绑或凭据撤销正在进行，当前不能调用工具。"
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
        except Exception:
            if manifest.credential_policies and manifest.saas_policy is None:
                self._credential_verification[scope_key] = "verification-failed"
            if manifest.database_policy is not None:
                self._preflight_status[scope_key] = "failed"
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
            tool_schema_sha256=tool_schema_sha256,
            tool_policy_digest=self._tool_policy_digest(policy),
            idempotency_key=f"mcpidem_{uuid.uuid4().hex}",
            target_preview=target_preview,
        )
        self._approvals[self._approval_key(approval.approval_id)] = approval
        return {
            "code": "approval_required",
            "message": "该操作会修改持久状态；参数和账号上下文已由服务端冻结，请确认后执行。",
            "approval_id": approval.approval_id,
            "summary": approval.summary,
            "argument_digest": approval.argument_digest,
            "idempotency_key": approval.idempotency_key,
            "target_preview": approval.target_preview or None,
            "expires_at": approval.expires_at,
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
                if manifest.database_policy is not None or manifest.saas_policy is not None
                else "not-applicable"
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
