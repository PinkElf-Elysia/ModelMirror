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
import json
import logging
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
            and bool(self.server_command or self.endpoint)
        )

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


@dataclass(slots=True)
class CatalogApproval:
    approval_id: str
    project_id: str
    session_id: str
    workspace_id: str
    workspace_manifest_sha256: str
    tool_name: str
    arguments: dict[str, Any]
    argument_digest: str
    summary: str
    expires_at: float
    used: bool = False


class MCPCatalogService:
    """Operate only server-owned adapters from the frozen catalog."""

    _RESERVED_CONFIGURATION_KEYS = {
        "command",
        "server_command",
        "url",
        "endpoint",
        "headers",
        "environment",
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
        self._sessions: dict[str, str] = {}
        self._configurations: dict[str, CatalogConfigurationRequest] = {}
        self._credential_snapshots: dict[str, dict[str, tuple[str, float]]] = {}
        self._credential_verification: dict[str, str] = {}
        self._approvals: dict[str, CatalogApproval] = {}
        self._lock = asyncio.Lock()

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
            item = manifest.to_public(
                connected=project_id in self._sessions,
                session_id=self._sessions.get(project_id),
            )
            configuration = self._configurations.get(project_id)
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
            if not manifest.credential_policies:
                item["credential_verification"] = "not-required"
            elif configuration is None:
                item["credential_verification"] = "missing"
            else:
                item["credential_verification"] = self._credential_verification.get(
                    project_id,
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

        if manifest.credential_policies and manifest.project_id in self._sessions:
            raise CatalogAdapterPolicyError("请先断开当前会话，再更新目录配置。")

        supplied_setting_keys = set(request.settings)
        supplied_credential_slots = set(request.credential_bindings)
        if {key.lower() for key in supplied_setting_keys} & self._RESERVED_CONFIGURATION_KEYS:
            raise CatalogAdapterPolicyError(
                "目录配置不能包含命令、URL、Header、环境变量或工作目录。"
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
                credential = self.credential_validator(credential_id)
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
        self._configurations[manifest.project_id] = normalized
        self._credential_snapshots.pop(manifest.project_id, None)
        if manifest.credential_policies:
            self._credential_verification[manifest.project_id] = "unverified"
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
        started_at = time.monotonic()
        async with self._lock:
            existing = self._sessions.get(manifest.project_id)
            if existing:
                try:
                    await self._ensure_credentials_fresh(manifest.project_id)
                    tools = await self.manager.list_tools(existing)
                    return self._connection_payload(manifest, existing, tools)
                except MCPSessionNotFoundError:
                    self._sessions.pop(manifest.project_id, None)

            if manifest.transport != "stdio" or not manifest.server_command:
                raise CatalogAdapterUnavailableError(
                    "该适配器尚未配置受控的可执行传输。"
                )
            if manifest.connection_kind == "sandboxed-stdio":
                environment: dict[str, str] = {}
                if manifest.workspace_policy is not None:
                    configuration = self._configurations.get(manifest.project_id)
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
                    environment["MCP_FILE_WORKSPACE_ID"] = workspace_id
                if manifest.credential_policies:
                    configuration = self._configurations.get(manifest.project_id)
                    if configuration is None:
                        raise CatalogAdapterPolicyError("请先绑定所需凭据并保存配置。")
                    if self.credential_resolver is None or self.credential_validator is None:
                        raise CatalogAdapterPolicyError("目录凭据存储当前不可用。")
                    secrets: dict[str, str] = {}
                    snapshots: dict[str, tuple[str, float]] = {}
                    for policy in manifest.credential_policies:
                        credential_id = configuration.credential_bindings.get(policy.key, "")
                        if not credential_id:
                            if policy.required:
                                raise CatalogAdapterPolicyError(f"缺少凭据绑定：{policy.key}")
                            continue
                        public = self.credential_validator(credential_id)
                        if getattr(public, "status", "") != "active":
                            raise CatalogAdapterPolicyError("绑定凭据已撤销或不可用，请重新配置。")
                        if (
                            getattr(public, "catalog_project_id", "") != manifest.project_id
                            or getattr(public, "catalog_slot", "") != policy.key
                        ):
                            raise CatalogAdapterPolicyError(
                                "绑定凭据不属于当前目录项目或固定槽位。"
                            )
                        secrets[policy.key] = self.credential_resolver(credential_id)
                        snapshots[policy.key] = (
                            credential_id,
                            float(getattr(public, "updated_at", 0.0)),
                        )
                    token_payload = json.dumps(
                        {
                            "settings": configuration.settings,
                            "credentials": secrets,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if len(token_payload) > 64 * 1024:
                        raise CatalogAdapterPolicyError("目录凭据配置超过内部传输上限。")
                    environment["MCP_TOKEN_HANDSHAKE_B64"] = base64.urlsafe_b64encode(
                        token_payload
                    ).decode("ascii")
                profile: dict[str, Any] = {
                    "transport": "stdio",
                    "server_command": list(manifest.server_command),
                    "network_policy": manifest.network_policy,
                    "reconnect_attempts": 0 if manifest.credential_policies else 1,
                    "operation_timeout": manifest.operation_timeout,
                }
                if environment:
                    profile["environment"] = environment
                session_id = await self.manager.connect_profile(**profile)
                if manifest.credential_policies:
                    await self.manager.scrub_session_environment(session_id)
            else:
                session_id = await self.manager.connect(list(manifest.server_command))
            try:
                tools = await self.manager.list_tools(session_id)
                if manifest.legacy_unrestricted_calls:
                    await self.registry.register_session_tools(
                        session_id=session_id,
                        server_id=f"catalog:{manifest.project_id}",
                        tools=tools,
                    )
            except Exception:
                await self.manager.disconnect(session_id)
                raise
            self._sessions[manifest.project_id] = session_id
            if manifest.credential_policies:
                self._credential_snapshots[manifest.project_id] = snapshots
            payload = self._connection_payload(manifest, session_id, tools)
            logger.info(
                "MCP catalog connect project=%s tools=%d duration_ms=%d",
                manifest.project_id,
                len(tools),
                int((time.monotonic() - started_at) * 1000),
            )
            return payload

    async def disconnect(self, project_id: str) -> dict[str, Any]:
        manifest = self.get_manifest(project_id)
        async with self._lock:
            session_id = self._sessions.pop(manifest.project_id, None)
            self._credential_snapshots.pop(manifest.project_id, None)
        if session_id is None:
            raise MCPSessionNotFoundError(
                f"MCP catalog session not found: {manifest.project_id}"
            )
        try:
            await self.manager.disconnect(session_id)
        finally:
            await self.registry.unregister_session(session_id)
        self._revoke_approvals(manifest.project_id)
        logger.info("MCP catalog disconnect project=%s", manifest.project_id)
        return {"ok": True, "project_id": manifest.project_id}

    async def call_tool(
        self,
        project_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self._require_executable(project_id)
        session_id = self._sessions.get(manifest.project_id)
        if session_id is None:
            raise CatalogAdapterUnavailableError("请先连接该 MCP 适配器。")
        await self._ensure_credentials_fresh(manifest.project_id)

        if not manifest.legacy_unrestricted_calls:
            policy = manifest.tool_policies.get(tool_name)
            if policy is None:
                raise CatalogAdapterPolicyError(
                    "该工具尚未完成显式读写与审批策略分类。"
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
        approval = self._approvals.get(str(approval_id or ""))
        if approval is None or approval.project_id != manifest.project_id:
            raise CatalogAdapterPolicyError("一次性确认不存在或已经失效。")
        if approval.used or approval.expires_at <= time.time():
            self._approvals.pop(approval.approval_id, None)
            raise CatalogAdapterPolicyError("一次性确认已经使用或过期。")
        if self.workspace_store is None:
            raise CatalogAdapterPolicyError("MCP 文件工作区当前不可用。")
        workspace = self.workspace_store.require_sealed(
            manifest.project_id,
            approval.workspace_id,
            tenant_id=self.tenant_id,
        )
        if workspace.manifest_sha256 != approval.workspace_manifest_sha256:
            raise CatalogAdapterPolicyError("工作区内容已经变化，请重新发起操作。")
        approval.used = True
        self._approvals.pop(approval.approval_id, None)
        if approval.tool_name == "__delete_workspace__":
            if manifest.project_id in self._sessions:
                await self.disconnect(manifest.project_id)
            self.workspace_store.delete(
                manifest.project_id,
                approval.workspace_id,
                tenant_id=self.tenant_id,
            )
            self._configurations.pop(manifest.project_id, None)
            return {"ok": True, "project_id": manifest.project_id, "workspace_id": approval.workspace_id}
        session_id = self._sessions.get(manifest.project_id)
        if not session_id or session_id != approval.session_id:
            raise CatalogAdapterPolicyError("MCP 会话已经变化，请重新发起操作。")
        return await self._execute_tool(
            manifest,
            session_id=session_id,
            tool_name=approval.tool_name,
            arguments=approval.arguments,
        )

    def cancel_approval(self, project_id: str, approval_id: str) -> dict[str, Any]:
        approval = self._approvals.get(str(approval_id or ""))
        if approval is None or approval.project_id != project_id:
            raise CatalogAdapterPolicyError("一次性确认不存在或已经失效。")
        self._approvals.pop(approval.approval_id, None)
        return {"ok": True, "approval_id": approval.approval_id}

    async def _execute_tool(
        self,
        manifest: CatalogAdapterManifest,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:

        started_at = time.monotonic()
        try:
            result = await self.manager.call_tool(session_id, tool_name, arguments)
        except Exception:
            if manifest.credential_policies:
                self._credential_verification[manifest.project_id] = "verification-failed"
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
        if manifest.credential_policies:
            self._credential_verification[manifest.project_id] = (
                "verification-failed" if payload["is_error"] else "verified"
            )
        configuration = self._configurations.get(manifest.project_id)
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
        configuration = self._configurations.get(manifest.project_id)
        bound_workspace_id = str(
            workspace_id or (configuration.workspace_id if configuration else "") or ""
        )
        if not bound_workspace_id or self.workspace_store is None:
            raise CatalogAdapterPolicyError("该操作缺少有效的受控工作区。")
        workspace = self.workspace_store.require_sealed(
            manifest.project_id,
            bound_workspace_id,
            tenant_id=self.tenant_id,
        )
        canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        approval = CatalogApproval(
            approval_id=f"mcpauth_{uuid.uuid4().hex}",
            project_id=manifest.project_id,
            session_id=session_id,
            workspace_id=bound_workspace_id,
            workspace_manifest_sha256=workspace.manifest_sha256,
            tool_name=tool_name,
            arguments=json.loads(canonical),
            argument_digest=digest,
            summary=self._approval_summary(tool_name, arguments, workspace.display_name),
            expires_at=time.time() + 300,
        )
        self._approvals[approval.approval_id] = approval
        return {
            "code": "approval_required",
            "message": "该操作会写入持久记忆或生成新的表格产物，请确认后执行。",
            "approval_id": approval.approval_id,
            "summary": approval.summary,
            "argument_digest": approval.argument_digest,
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

    def _revoke_approvals(self, project_id: str) -> None:
        for approval_id, approval in list(self._approvals.items()):
            if approval.project_id == project_id:
                self._approvals.pop(approval_id, None)

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
        if item.persistent:
            raise CatalogApprovalRequiredError(
                self._create_approval(
                    manifest,
                    session_id=self._sessions.get(manifest.project_id, "workspace-management"),
                    tool_name="__delete_workspace__",
                    arguments={"workspace_id": workspace_id},
                    workspace_id=workspace_id,
                )
            )
        if self._configurations.get(manifest.project_id, CatalogConfigurationRequest()).workspace_id == workspace_id:
            if manifest.project_id in self._sessions:
                await self.disconnect(manifest.project_id)
            self._configurations.pop(manifest.project_id, None)
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
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._credential_snapshots.clear()
        for session_id in sessions:
            try:
                await self.manager.disconnect(session_id)
            except Exception:
                pass
            await self.registry.unregister_session(session_id)
        self._approvals.clear()

    def list_credentials(self, project_id: str) -> dict[str, Any]:
        manifest = self._require_credential_adapter(project_id)
        if self.credential_lister is None:
            raise CatalogAdapterUnavailableError("目录凭据存储当前不可用。")
        credentials = [
            self._public_credential(item)
            for item in self.credential_lister()
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
        record, _ = self.credential_creator(
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
            public = self.credential_validator(credential_id)
        except Exception as exc:
            raise CatalogAdapterPolicyError("目录凭据不存在或不可用。") from exc
        if getattr(public, "catalog_project_id", "") != manifest.project_id:
            raise CatalogAdapterPolicyError("不能撤销其他目录项目的凭据。")
        slot = str(getattr(public, "catalog_slot", ""))
        if slot not in manifest.credential_slots:
            raise CatalogAdapterPolicyError("凭据槽不属于当前目录项目。")
        revoked = self.credential_revoker(credential_id)
        configuration = self._configurations.get(manifest.project_id)
        if configuration and credential_id in configuration.credential_bindings.values():
            if manifest.project_id in self._sessions:
                await self.disconnect(manifest.project_id)
            self._configurations.pop(manifest.project_id, None)
            self._credential_snapshots.pop(manifest.project_id, None)
            self._credential_verification[manifest.project_id] = "missing"
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
            for project_id, session_id in list(self._sessions.items()):
                if session_id in cleaned:
                    self._sessions.pop(project_id, None)
                    self._credential_snapshots.pop(project_id, None)

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
        snapshots = self._credential_snapshots.get(project_id)
        if not snapshots:
            return
        stale = False
        if self.credential_validator is None:
            stale = True
        else:
            for credential_id, expected_updated_at in snapshots.values():
                try:
                    public = self.credential_validator(credential_id)
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
        session_id = self._sessions.pop(project_id, None)
        self._credential_snapshots.pop(project_id, None)
        self._credential_verification[project_id] = "unverified"
        self._revoke_approvals(project_id)
        if session_id is not None:
            try:
                await self.manager.disconnect(session_id)
            finally:
                await self.registry.unregister_session(session_id)
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
        for project_id, active_session_id in self._sessions.items():
            if active_session_id == clean:
                return project_id
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
                self._credential_verification.get(manifest.project_id, "unverified")
                if manifest.credential_policies
                else "not-required"
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
        return get_mcp_catalog_service().cancel_approval(project_id, approval_id)
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
