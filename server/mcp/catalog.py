"""Curated MCP catalog adapter registry and catalog-scoped runtime API.

The frontend catalog is descriptive.  Executable commands, transports and
permission policy live here so clients cannot turn a planned catalog entry into
an arbitrary MCP connection.  Batch zero intentionally exposes only the seven
previously supported local stdio adapters; every other project remains a
non-executable roadmap entry.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mcp.types import CallToolResult, Tool

try:
    from server.mcp.manager import (
        MCPClientManager,
        MCPInstaller,
        MCPSessionNotFoundError,
    )
    from server.registry.tool_registry import ToolRegistry
except ModuleNotFoundError:
    from mcp.manager import MCPClientManager, MCPInstaller, MCPSessionNotFoundError
    from registry.tool_registry import ToolRegistry


AdapterAvailability = Literal["planned", "adapting", "ready", "blocked"]
AdapterConnectionKind = Literal[
    "local-stdio",
    "sandboxed-stdio",
    "remote-mcp",
    "desktop-bridge",
]
AdapterRisk = Literal["low", "medium", "high", "critical"]

logger = logging.getLogger("modelmirror.mcp.catalog")


class CatalogAdapterError(RuntimeError):
    """Base catalog adapter failure."""


class CatalogAdapterNotFoundError(CatalogAdapterError):
    """Raised when a project is not part of the frozen catalog."""


class CatalogAdapterUnavailableError(CatalogAdapterError):
    """Raised when a project has not crossed its production readiness gate."""


class CatalogAdapterPolicyError(CatalogAdapterError):
    """Raised when configuration or execution violates an adapter policy."""


@dataclass(frozen=True, slots=True)
class CatalogToolPolicy:
    read_only: bool = True
    requires_approval: bool = False
    sensitive: bool = False
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class CatalogAdapterManifest:
    project_id: str
    wave: int
    availability: AdapterAvailability
    connection_kind: AdapterConnectionKind
    risk: AdapterRisk
    required_capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    server_command: tuple[str, ...] = ()
    install_command: str = ""
    transport: str = "stdio"
    endpoint: str = ""
    allowed_settings: tuple[str, ...] = ()
    credential_slots: tuple[str, ...] = ()
    tool_policies: dict[str, CatalogToolPolicy] = field(default_factory=dict)
    legacy_unrestricted_calls: bool = False
    enabled_by_default: bool = False

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

    for wave, project_ids in WAVE_PROJECTS.items():
        connection_kind, risk, capabilities, limitations = WAVE_METADATA[wave]
        for project_id in project_ids:
            if project_id in manifests:
                raise RuntimeError(f"Duplicate MCP catalog project: {project_id}")
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
    settings: dict[str, str | int | float | bool] = Field(default_factory=dict)
    credential_bindings: dict[str, str] = Field(default_factory=dict)


class CatalogToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


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
    ) -> None:
        self.manager = manager
        self.installer = installer
        self.registry = registry
        self.manifests = dict(manifests or CATALOG_ADAPTERS)
        self.credential_validator = credential_validator
        self._sessions: dict[str, str] = {}
        self._configurations: dict[str, CatalogConfigurationRequest] = {}
        self._lock = asyncio.Lock()

    def get_manifest(self, project_id: str) -> CatalogAdapterManifest:
        manifest = self.manifests.get(str(project_id or "").strip())
        if manifest is None:
            raise CatalogAdapterNotFoundError(
                f"MCP 目录中不存在项目：{project_id}"
            )
        return manifest

    def list_adapters(self) -> dict[str, Any]:
        adapters = [
            manifest.to_public(
                connected=project_id in self._sessions,
                session_id=self._sessions.get(project_id),
            )
            for project_id, manifest in sorted(self.manifests.items())
        ]
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

        supplied_setting_keys = set(request.settings)
        supplied_credential_slots = set(request.credential_bindings)
        if supplied_setting_keys & self._RESERVED_CONFIGURATION_KEYS:
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
        for credential_id in request.credential_bindings.values():
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

        self._configurations[manifest.project_id] = request.model_copy(deep=True)
        return {
            "project_id": manifest.project_id,
            "configured": True,
            "configured_settings": sorted(request.settings),
            "configured_credential_slots": sorted(request.credential_bindings),
        }

    async def connect(self, project_id: str) -> dict[str, Any]:
        manifest = self._require_executable(project_id)
        started_at = time.monotonic()
        async with self._lock:
            existing = self._sessions.get(manifest.project_id)
            if existing:
                try:
                    tools = await self.manager.list_tools(existing)
                    return self._connection_payload(manifest, existing, tools)
                except MCPSessionNotFoundError:
                    self._sessions.pop(manifest.project_id, None)

            if manifest.transport != "stdio" or not manifest.server_command:
                raise CatalogAdapterUnavailableError(
                    "该适配器尚未配置受控的可执行传输。"
                )
            session_id = await self.manager.connect(list(manifest.server_command))
            try:
                tools = await self.manager.list_tools(session_id)
                await self.registry.register_session_tools(
                    session_id=session_id,
                    server_id=f"catalog:{manifest.project_id}",
                    tools=tools,
                )
            except Exception:
                await self.manager.disconnect(session_id)
                raise
            self._sessions[manifest.project_id] = session_id
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
        if session_id is None:
            raise MCPSessionNotFoundError(
                f"MCP catalog session not found: {manifest.project_id}"
            )
        try:
            await self.manager.disconnect(session_id)
        finally:
            await self.registry.unregister_session(session_id)
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

        if not manifest.legacy_unrestricted_calls:
            policy = manifest.tool_policies.get(tool_name)
            if policy is None:
                raise CatalogAdapterPolicyError(
                    "该工具尚未完成显式读写与审批策略分类。"
                )
            if policy.requires_approval or not policy.read_only:
                raise CatalogAdapterPolicyError(
                    "该工具需要通过运行时审批流程后执行。"
                )

        started_at = time.monotonic()
        result = await self.manager.call_tool(session_id, tool_name, arguments)
        logger.info(
            "MCP catalog call project=%s tool=%s duration_ms=%d",
            manifest.project_id,
            tool_name,
            int((time.monotonic() - started_at) * 1000),
        )
        return self._serialize_call_result(result)

    async def clear_sessions(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session_id in sessions:
            try:
                await self.manager.disconnect(session_id)
            except Exception:
                pass
            await self.registry.unregister_session(session_id)

    async def forget_sessions(self, session_ids: list[str]) -> None:
        """Forget sessions already removed by the manager TTL cleanup."""

        cleaned = set(session_ids)
        if not cleaned:
            return
        async with self._lock:
            for project_id, session_id in list(self._sessions.items()):
                if session_id in cleaned:
                    self._sessions.pop(project_id, None)

    def _require_executable(self, project_id: str) -> CatalogAdapterManifest:
        manifest = self.get_manifest(project_id)
        if not manifest.executable:
            raise CatalogAdapterUnavailableError(
                "该 MCP 尚未通过生产级适配验收，当前不可准备、连接或执行。"
            )
        return manifest

    @staticmethod
    def _connection_payload(
        manifest: CatalogAdapterManifest,
        session_id: str,
        tools: list[Tool],
    ) -> dict[str, Any]:
        return {
            "project_id": manifest.project_id,
            "session_id": session_id,
            "tools_count": len(tools),
        }

    @staticmethod
    def _public_install_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {"project_id", "install_type", "npm_package", "installed_at"}
        return {key: value[key] for key in allowed if key in value}

    @staticmethod
    def _serialize_call_result(result: CallToolResult) -> dict[str, Any]:
        payload = result.model_dump(mode="json", exclude_none=True)
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
    if isinstance(exc, CatalogAdapterNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, CatalogAdapterUnavailableError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, CatalogAdapterPolicyError):
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
