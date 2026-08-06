from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.types import CallToolResult, TextContent, Tool

from server.mcp import catalog
from server.mcp.catalog import (
    CATALOG_ADAPTERS,
    LOCAL_STDIO_ADAPTERS,
    WAVE_ONE_ADAPTERS,
    WAVE_TWO_ADAPTERS,
    WAVE_PROJECTS,
    CatalogAdapterManifest,
    CatalogConfigurationRequest,
    MCPCatalogService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeManager:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.disconnected: list[str] = []
        self.sessions: set[str] = set()
        self.profiles: list[dict[str, Any]] = []

    async def connect(self, command: list[str]) -> str:
        self.commands.append(list(command))
        session_id = f"session-{len(self.commands)}"
        self.sessions.add(session_id)
        return session_id

    async def connect_profile(self, **kwargs: Any) -> str:
        self.profiles.append(dict(kwargs))
        return await self.connect(list(kwargs.get("server_command") or []))

    async def list_tools(self, session_id: str) -> list[Tool]:
        if session_id not in self.sessions:
            raise catalog.MCPSessionNotFoundError(session_id)
        return [Tool(name="echo", description="Echo", inputSchema={})]

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        self.calls.append((session_id, tool_name, dict(arguments)))
        return CallToolResult(
            content=[TextContent(type="text", text=str(arguments.get("value", "ok")))]
        )

    async def disconnect(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise catalog.MCPSessionNotFoundError(session_id)
        self.sessions.remove(session_id)
        self.disconnected.append(session_id)


class FakeInstaller:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.installed: dict[str, dict[str, Any]] = {}

    def install(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        result = {
            "project_id": kwargs["project_id"],
            "installed": True,
            "message": "prepared",
            "metadata": {
                "project_id": kwargs["project_id"],
                "install_type": "npm_global",
                "npm_package": kwargs["server_command"][2],
                "installed_at": 1.0,
                "config_path": "C:/private/catalog/config.json",
                "install_command": "must-not-leak",
            },
        }
        self.installed[kwargs["project_id"]] = result["metadata"]
        return result

    def get_installed(self, project_id: str) -> dict[str, Any] | None:
        return self.installed.get(project_id)


class FakeRegistry:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []
        self.unregistered: list[str] = []

    async def register_session_tools(
        self,
        *,
        session_id: str,
        server_id: str,
        tools: list[Tool],
    ) -> None:
        assert tools
        self.registered.append((session_id, server_id))

    async def unregister_session(self, session_id: str) -> None:
        self.unregistered.append(session_id)


def make_service(
    manifests: dict[str, CatalogAdapterManifest] | None = None,
) -> tuple[MCPCatalogService, FakeManager, FakeInstaller, FakeRegistry]:
    manager = FakeManager()
    installer = FakeInstaller()
    registry = FakeRegistry()
    service = MCPCatalogService(  # type: ignore[arg-type]
        manager,
        installer,
        registry,
        manifests=manifests,
        credential_validator=lambda credential_id: SimpleNamespace(
            credential_id=credential_id,
            status="active",
        ),
    )
    return service, manager, installer, registry


def test_catalog_freezes_100_projects_and_maps_all_waves_once() -> None:
    phased = [project for projects in WAVE_PROJECTS.values() for project in projects]

    assert len(CATALOG_ADAPTERS) == 100
    assert len(LOCAL_STDIO_ADAPTERS) == 7
    assert len(phased) == 93
    assert len(set(phased)) == 93
    assert set(phased).isdisjoint(LOCAL_STDIO_ADAPTERS)
    assert set(CATALOG_ADAPTERS) == set(phased) | set(LOCAL_STDIO_ADAPTERS)
    assert set(WAVE_ONE_ADAPTERS) == set(WAVE_PROJECTS[1])
    assert set(WAVE_TWO_ADAPTERS) == set(WAVE_PROJECTS[2]) - {
        "bibigpt-mcp",
        "airbnb-mcp",
    }
    assert sum(
        manifest.availability == "ready"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 13
    assert sum(
        manifest.availability == "planned"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 85
    assert sum(
        manifest.availability == "blocked"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 2
    assert {manifest.availability for manifest in CATALOG_ADAPTERS.values()} == {
        "ready",
        "planned",
        "blocked",
    }


def test_frontend_catalog_ids_match_backend_registry_and_never_submit_commands() -> None:
    projects_source = (
        PROJECT_ROOT / "client" / "src" / "data" / "mcpProjects.ts"
    ).read_text(encoding="utf-8")
    seed_source = projects_source[
        projects_source.index("const originalMcpProjectSeeds") :
        projects_source.index("const originalRequirements")
    ]
    frontend_ids = set(
        re.findall(r'^\s{4}id: "([a-z0-9.-]+)"', seed_source, flags=re.MULTILINE)
    )
    card_source = (
        PROJECT_ROOT / "client" / "src" / "components" / "McpServerCard.tsx"
    ).read_text(encoding="utf-8")

    assert frontend_ids == set(CATALOG_ADAPTERS)
    assert not re.search(r"^\s+command:", seed_source, flags=re.MULTILINE)
    assert 'fetch("/api/mcp/connect"' not in card_source
    assert 'fetch("/api/mcp/install"' not in card_source
    assert not re.search(
        r"body:\s*JSON\.stringify\([^)]*server_command",
        card_source,
        flags=re.DOTALL,
    )


def test_planned_adapter_cannot_be_enabled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = CATALOG_ADAPTERS["basic-memory-mcp"]
    monkeypatch.setenv(manifest.feature_flag, "true")

    assert manifest.feature_enabled is True
    assert manifest.executable is False
    assert not manifest.server_command
    assert not manifest.endpoint


@pytest.mark.asyncio
async def test_catalog_api_hides_execution_details_and_rejects_planned_connect() -> None:
    service, _, _, _ = make_service()
    previous = catalog._catalog_service
    catalog.configure_mcp_catalog(service)
    app = FastAPI()
    app.include_router(catalog.router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/mcp/catalog/adapters")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 100
            assert payload["ready"] == 13
            assert payload["planned"] == 85
            assert payload["blocked"] == 2
            serialized = response.text.lower()
            assert "server_command" not in serialized
            assert "install_command" not in serialized
            assert '"endpoint"' not in serialized

            blocked = await client.post(
                "/api/mcp/catalog/bibigpt-mcp/connect"
            )
            assert blocked.status_code == 409
            assert "尚未通过生产级适配验收" in blocked.text
    finally:
        catalog._catalog_service = previous


@pytest.mark.asyncio
async def test_main_app_registers_catalog_status_endpoint() -> None:
    from server.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/mcp/catalog/adapters")

    assert response.status_code == 200
    assert response.json()["total"] == 100


@pytest.mark.asyncio
async def test_ready_adapter_uses_server_owned_prepare_connect_call_and_disconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, manager, installer, registry = make_service()
    caplog.set_level("INFO", logger="modelmirror.mcp.catalog")

    prepared = await service.prepare("context7")
    assert prepared["prepared"] is True
    assert installer.calls == [
        {
            "project_id": "context7",
            "install_command": "npx ctx7 setup",
            "server_command": ["npx", "-y", "@upstash/context7-mcp"],
        }
    ]
    assert "config_path" not in prepared["metadata"]
    assert "install_command" not in prepared["metadata"]

    prepared_again = await service.prepare("context7")
    assert prepared_again["prepared"] is True
    assert len(installer.calls) == 1

    connected = await service.connect("context7")
    assert connected["tools_count"] == 1
    assert manager.commands == [["npx", "-y", "@upstash/context7-mcp"]]
    assert registry.registered == [(connected["session_id"], "catalog:context7")]
    status = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "context7"
    )
    assert status["connected"] is True

    called = await service.call_tool("context7", "echo", {"value": "hello"})
    assert called["content"][0]["text"] == "hello"
    assert manager.calls == [(connected["session_id"], "echo", {"value": "hello"})]
    assert "hello" not in caplog.text

    disconnected = await service.disconnect("context7")
    assert disconnected == {"ok": True, "project_id": "context7"}
    assert manager.disconnected == [connected["session_id"]]
    assert registry.unregistered == [connected["session_id"]]


@pytest.mark.asyncio
async def test_wave_one_adapter_uses_bundled_sandbox_profile() -> None:
    service, manager, installer, _ = make_service()

    prepared = await service.prepare("calculator-mcp")
    assert prepared["prepared"] is True
    assert prepared["metadata"]["adapter_version"] == "0.2.1-compatible-python-v1"
    assert prepared["metadata"]["runtime_image"] == "modelmirror-sandbox:wave1-v1"
    assert installer.calls == []

    connected = await service.connect("calculator-mcp")
    assert connected["tools_count"] == 1
    assert manager.profiles == [
        {
            "transport": "stdio",
            "server_command": list(
                CATALOG_ADAPTERS["calculator-mcp"].server_command
            ),
            "network_policy": "disabled",
            "reconnect_attempts": 1,
            "operation_timeout": 10.0,
        }
    ]
    public = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "calculator-mcp"
    )
    assert public["availability"] == "ready"
    assert public["executable"] is True
    assert public["network_policy"] == "disabled"
    assert public["filesystem_policy"] == "read-only-empty-workspace"
    assert set(public["tool_policies"]) == set(
        WAVE_ONE_ADAPTERS["calculator-mcp"][1]
    )


@pytest.mark.asyncio
async def test_wave_two_adapter_uses_fixed_public_sidecar_profile() -> None:
    service, manager, installer, _ = make_service()

    prepared = await service.prepare("fetch-mcp")
    assert prepared["prepared"] is True
    assert prepared["metadata"]["adapter_version"] == "0.6.3-secure-compatible-v1"
    assert prepared["metadata"]["runtime_image"] == "modelmirror-sandbox:wave2-public-v1"
    assert installer.calls == []

    connected = await service.connect("fetch-mcp")
    assert connected["tools_count"] == 1
    assert manager.profiles == [
        {
            "transport": "stdio",
            "server_command": list(CATALOG_ADAPTERS["fetch-mcp"].server_command),
            "network_policy": "validated-public-https:user-supplied-host",
            "reconnect_attempts": 1,
            "operation_timeout": 45.0,
        }
    ]
    public = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "fetch-mcp"
    )
    assert public["availability"] == "ready"
    assert public["executable"] is True
    assert public["connection_kind"] == "sandboxed-stdio"
    assert set(public["tool_policies"]) == {"fetch"}


def test_bibigpt_is_fail_closed_until_oauth_wave() -> None:
    manifest = CATALOG_ADAPTERS["bibigpt-mcp"]

    assert manifest.availability == "blocked"
    assert manifest.wave == 2
    assert manifest.connection_kind == "remote-mcp"
    assert manifest.server_command == ()
    assert manifest.endpoint == ""
    assert manifest.executable is False
    assert "oauth-pkce" in manifest.required_capabilities


def test_airbnb_is_fail_closed_on_upstream_schema_drift() -> None:
    manifest = CATALOG_ADAPTERS["airbnb-mcp"]

    assert manifest.availability == "blocked"
    assert manifest.wave == 2
    assert manifest.server_command == ()
    assert manifest.executable is False
    assert manifest.network_policy == "blocked:upstream-schema-drift"
    assert "schema-drift-recovery" in manifest.required_capabilities


@pytest.mark.asyncio
async def test_ttl_cleanup_can_forget_catalog_session_mapping() -> None:
    service, _, _, _ = make_service()
    connected = await service.connect("context7")

    await service.forget_sessions([connected["session_id"]])

    status = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "context7"
    )
    assert status["connected"] is False


def test_configuration_rejects_execution_fields_and_unknown_credential_slots() -> None:
    manifest = CatalogAdapterManifest(
        project_id="adapting-example",
        wave=4,
        availability="adapting",
        connection_kind="remote-mcp",
        risk="medium",
        required_capabilities=("credential-binding",),
        limitations=("test",),
        allowed_settings=("region",),
        credential_slots=("api_token",),
    )
    service, _, _, _ = make_service({manifest.project_id: manifest})

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="不能包含命令"):
        service.configure(
            manifest.project_id,
            CatalogConfigurationRequest(settings={"url": "https://evil.invalid"}),
        )

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="未声明的字段"):
        service.configure(
            manifest.project_id,
            CatalogConfigurationRequest(
                settings={"region": "cn"},
                credential_bindings={"admin_secret": "cred_example"},
            ),
        )

    configured = service.configure(
        manifest.project_id,
        CatalogConfigurationRequest(
            settings={"region": "cn"},
            credential_bindings={"api_token": "cred_example"},
        ),
    )
    assert configured["configured_settings"] == ["region"]
    assert configured["configured_credential_slots"] == ["api_token"]


@pytest.mark.asyncio
async def test_future_ready_adapter_requires_explicit_tool_policy() -> None:
    manifest = CatalogAdapterManifest(
        project_id="future-ready",
        wave=1,
        availability="ready",
        connection_kind="sandboxed-stdio",
        risk="low",
        required_capabilities=("sandbox",),
        limitations=(),
        server_command=("python", "-m", "example"),
        enabled_by_default=True,
    )
    service, _, _, _ = make_service({manifest.project_id: manifest})
    await service.connect(manifest.project_id)

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="显式读写"):
        await service.call_tool(manifest.project_id, "echo", {})
