from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, TextContent, Tool
from pydantic import ValidationError

from server.mcp import catalog
from server.mcp.catalog import (
    CATALOG_ADAPTERS,
    LOCAL_STDIO_ADAPTERS,
    WAVE_ONE_ADAPTERS,
    WAVE_TWO_ADAPTERS,
    WAVE_THREE_ADAPTERS,
    WAVE_FOUR_ADAPTERS,
    WAVE_FIVE_ADAPTERS,
    WAVE_SIX_ADAPTERS,
    WAVE_PROJECTS,
    CatalogAdapterManifest,
    CatalogConfigurationRequest,
    CatalogConfigurationSnapshot,
    CatalogCredentialCreateRequest,
    CatalogUnknownOutcomeError,
    CatalogUnbindRequest,
    MCPCatalogService,
)
from server.sandbox_sidecar.saas_contracts import SAAS_SCHEMA_SHA256
from server.toolsets.credentials import CredentialStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeManager:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.retry_on_failure: list[bool] = []
        self.disconnected: list[str] = []
        self.sessions: set[str] = set()
        self.session_owners: dict[str, str] = {}
        self.profiles: list[dict[str, Any]] = []
        self.scrubbed: list[str] = []
        self.call_is_error = False
        self.call_error: Exception | None = None
        self.call_gate: asyncio.Event | None = None
        self.connect_gate: asyncio.Event | None = None
        self.connect_started = asyncio.Event()
        self.disconnect_gate: asyncio.Event | None = None
        self.disconnect_started = asyncio.Event()
        self.tools: list[Tool] = [Tool(name="echo", description="Echo", inputSchema={})]

    async def connect(
        self,
        command: list[str],
        *,
        session_owner: str = "",
    ) -> str:
        self.commands.append(list(command))
        session_id = f"session-{len(self.commands)}"
        self.sessions.add(session_id)
        self.session_owners[session_id] = session_owner
        return session_id

    async def connect_profile(self, **kwargs: Any) -> str:
        self.profiles.append(dict(kwargs))
        self.connect_started.set()
        if self.connect_gate is not None:
            await self.connect_gate.wait()
        return await self.connect(
            list(kwargs.get("server_command") or []),
            session_owner=str(kwargs.get("session_owner") or ""),
        )

    async def list_tools(
        self,
        session_id: str,
        *,
        session_owner: str = "",
    ) -> list[Tool]:
        if (
            session_id not in self.sessions
            or self.session_owners.get(session_id, "") != session_owner
        ):
            raise catalog.MCPSessionNotFoundError(session_id)
        return list(self.tools)

    async def scrub_session_environment(
        self,
        session_id: str,
        *,
        session_owner: str = "",
    ) -> None:
        assert session_id in self.sessions
        assert self.session_owners.get(session_id, "") == session_owner
        self.scrubbed.append(session_id)

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        retry_on_failure: bool = True,
        session_owner: str = "",
    ) -> CallToolResult:
        if self.session_owners.get(session_id, "") != session_owner:
            raise catalog.MCPSessionNotFoundError(session_id)
        self.calls.append((session_id, tool_name, dict(arguments)))
        self.retry_on_failure.append(retry_on_failure)
        if self.call_gate is not None:
            await self.call_gate.wait()
        if self.call_error is not None:
            raise self.call_error
        return CallToolResult(
            content=[TextContent(type="text", text=str(arguments.get("value", "ok")))],
            isError=self.call_is_error,
        )

    async def disconnect(
        self,
        session_id: str,
        *,
        session_owner: str = "",
    ) -> None:
        if (
            session_id not in self.sessions
            or self.session_owners.get(session_id, "") != session_owner
        ):
            raise catalog.MCPSessionNotFoundError(session_id)
        self.disconnect_started.set()
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()
        self.sessions.remove(session_id)
        self.session_owners.pop(session_id, None)
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
    credential_scopes = {
        "cred_agentql": ("agentql-mcp", "api_key"),
        "cred_grafana": ("grafana-mcp", "service_token"),
        "cred_pinecone": ("pinecone-assistant-mcp", "api_key"),
        "cred_dbhub": ("dbhub", "password"),
        "cred_supabase": ("supabase-mcp", "access_token"),
        "cred_airtable": ("airtable-mcp", "personal_access_token"),
        "cred_asana": ("asana-mcp", "personal_access_token"),
        "cred_gitlab": ("gitlab-mcp", "personal_access_token"),
        "cred_notion": ("notion-mcp-server", "integration_token"),
    }

    def credential_validator(credential_id: str) -> SimpleNamespace:
        catalog_project_id, catalog_slot = credential_scopes.get(
            credential_id,
            ("", ""),
        )
        return SimpleNamespace(
            credential_id=credential_id,
            status="active",
            kind="provider_key",
            updated_at=1.0,
            catalog_project_id=catalog_project_id,
            catalog_slot=catalog_slot,
        )

    service = MCPCatalogService(  # type: ignore[arg-type]
        manager,
        installer,
        registry,
        manifests=manifests,
        credential_validator=credential_validator,
        credential_resolver=lambda credential_id: f"secret-for-{credential_id}",
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
    assert set(WAVE_THREE_ADAPTERS) == set(WAVE_PROJECTS[3]) - {"manim-mcp"}
    assert set(WAVE_FOUR_ADAPTERS) == set(WAVE_PROJECTS[4]) - {"snyk-mcp"}
    assert set(WAVE_FIVE_ADAPTERS) == {
        "dbhub",
        "mongodb-mcp",
        "clickhouse-mcp",
        "redis-mcp",
        "duckdb-mcp",
        "supabase-mcp",
    }
    assert sum(
        manifest.availability == "ready"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 42
    assert sum(
        manifest.availability == "planned"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 47
    assert sum(
        manifest.availability == "blocked"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 11
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
    credential_panel_source = (
        PROJECT_ROOT / "client" / "src" / "components" / "McpCredentialPanel.tsx"
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
    assert "/toolsets#credentials" not in credential_panel_source
    assert "/api/runtime/credentials" not in credential_panel_source
    assert "/api/mcp/catalog/${projectId}/credentials" in credential_panel_source
    assert 'type="password"' in credential_panel_source


def test_planned_adapter_cannot_be_enabled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = CATALOG_ADAPTERS["chrome-devtools-mcp"]
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
            assert payload["ready"] == 42
            assert payload["planned"] == 47
            assert payload["blocked"] == 11
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
                "session_owner": "catalog:local:local:calculator-mcp",
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
                "session_owner": "catalog:local:local:fetch-mcp",
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


@pytest.mark.asyncio
async def test_wave_four_adapter_resolves_secret_only_for_private_proxy() -> None:
    service, manager, installer, registry = make_service()
    configured = service.configure(
        "agentql-mcp",
        CatalogConfigurationRequest(
            credential_bindings={"api_key": "cred_agentql"},
        ),
    )
    assert configured["configured_credential_slots"] == ["api_key"]

    connected = await service.connect("agentql-mcp")
    assert connected["tools_count"] == 1
    assert installer.calls == []
    assert registry.registered == []
    profile = manager.profiles[0]
    assert profile["server_command"] == list(CATALOG_ADAPTERS["agentql-mcp"].server_command)
    assert profile["reconnect_attempts"] == 0
    assert manager.scrubbed == [connected["session_id"]]
    encoded = profile["environment"]["MCP_TOKEN_HANDSHAKE_B64"]
    payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    assert payload == {
        "settings": {},
        "credentials": {"api_key": "secret-for-cred_agentql"},
    }
    public = service.list_adapters()
    serialized = json.dumps(public, ensure_ascii=False)
    assert "secret-for-cred_agentql" not in serialized
    adapter = next(item for item in public["adapters"] if item["project_id"] == "agentql-mcp")
    assert adapter["configured"] is True
    assert adapter["credential_bindings"] == {"api_key": "cred_agentql"}
    assert adapter["credential_fields"][0]["label"] == "AgentQL API Key"


@pytest.mark.asyncio
async def test_wave_four_rotated_credential_forces_disconnect() -> None:
    manager = FakeManager()
    installer = FakeInstaller()
    registry = FakeRegistry()
    state = SimpleNamespace(
        status="active",
        kind="provider_key",
        updated_at=1.0,
        catalog_project_id="agentql-mcp",
        catalog_slot="api_key",
    )
    service = MCPCatalogService(  # type: ignore[arg-type]
        manager,
        installer,
        registry,
        credential_validator=lambda _: state,
        credential_resolver=lambda _: "private-token",
    )
    service.configure(
        "agentql-mcp",
        CatalogConfigurationRequest(credential_bindings={"api_key": "cred_agentql"}),
    )
    connected = await service.connect("agentql-mcp")
    state.updated_at = 2.0

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="会话已断开"):
        await service.call_tool("agentql-mcp", "extract-web-data", {})

    assert manager.disconnected == [connected["session_id"]]
    assert service.project_for_session(connected["session_id"]) is None


@pytest.mark.asyncio
async def test_wave_four_credentials_are_card_scoped_and_track_verification(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    installer = FakeInstaller()
    registry = FakeRegistry()
    store = CredentialStore(tmp_path / "credentials")
    service = MCPCatalogService(  # type: ignore[arg-type]
        manager,
        installer,
        registry,
        credential_validator=store.get_public,
        credential_resolver=store.resolve,
        credential_lister=store.list,
        credential_creator=store.create,
        credential_revoker=store.revoke,
    )

    created = service.create_credential(
        "agentql-mcp",
        CatalogCredentialCreateRequest(
            slot="api_key",
            name="AgentQL 生产凭据",
            value="private-agentql-token",
        ),
    )
    credential_id = created["credential_id"]
    assert created["catalog_project_id"] == "agentql-mcp"
    assert created["catalog_slot"] == "api_key"
    assert "private-agentql-token" not in json.dumps(created)
    assert service.list_credentials("agentql-mcp")["credentials"] == [created]
    assert service.list_credentials("exa-mcp")["credentials"] == []

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="固定槽位"):
        service.configure(
            "exa-mcp",
            CatalogConfigurationRequest(
                credential_bindings={"api_key": credential_id},
            ),
        )

    service.configure(
        "agentql-mcp",
        CatalogConfigurationRequest(
            credential_bindings={"api_key": credential_id},
        ),
    )
    connected = await service.connect("agentql-mcp")
    adapter = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "agentql-mcp"
    )
    assert adapter["credential_verification"] == "unverified"

    await service.call_tool("agentql-mcp", "extract-web-data", {})
    adapter = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "agentql-mcp"
    )
    assert adapter["credential_verification"] == "verified"

    manager.call_is_error = True
    await service.call_tool("agentql-mcp", "extract-web-data", {})
    adapter = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "agentql-mcp"
    )
    assert adapter["credential_verification"] == "verification-failed"

    revoked = await service.revoke_credential("agentql-mcp", credential_id)
    assert revoked["status"] == "revoked"
    assert manager.disconnected == [connected["session_id"]]
    adapter = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "agentql-mcp"
    )
    assert adapter["configured"] is False
    assert adapter["credential_verification"] == "missing"


def test_wave_four_settings_and_snyk_fail_closed() -> None:
    service, _, _, _ = make_service()
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="缺少必填配置"):
        service.configure(
            "grafana-mcp",
            CatalogConfigurationRequest(
                credential_bindings={"service_token": "cred_grafana"},
            ),
        )
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="格式不正确"):
        service.configure(
            "grafana-mcp",
            CatalogConfigurationRequest(
                settings={"stack_slug": "Bad_slug!"},
                credential_bindings={"service_token": "cred_grafana"},
            ),
        )
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="固定域名范围"):
        service.configure(
            "pinecone-assistant-mcp",
            CatalogConfigurationRequest(
                settings={"assistant_host": "assistant.example.com", "assistant_name": "docs"},
                credential_bindings={"api_key": "cred_pinecone"},
            ),
        )

    snyk = CATALOG_ADAPTERS["snyk-mcp"]
    assert snyk.availability == "blocked"
    assert snyk.wave == 4
    assert snyk.server_command == ()
    assert snyk.executable is False


@pytest.mark.asyncio
async def test_wave_five_database_adapter_uses_structured_scoped_handshake() -> None:
    service, manager, installer, registry = make_service()
    configured = service.configure(
        "dbhub",
        CatalogConfigurationRequest(
            settings={
                "engine": "postgresql",
                "host": "database.example.com",
                "port": 5432,
                "database": "analytics",
                "username": "readonly",
                "tls_mode": "verify-full",
            },
            credential_bindings={"password": "cred_dbhub"},
        ),
    )
    assert configured["configured_settings"] == [
        "database", "engine", "host", "port", "tls_mode", "username",
    ]

    connected = await service.connect("dbhub")
    assert connected["tools_count"] == 1
    assert connected["preflight_status"] == "verified"
    assert connected["credential_verification"] == "verified"
    assert installer.calls == []
    assert registry.registered == []
    profile = manager.profiles[0]
    assert profile["server_command"] == list(CATALOG_ADAPTERS["dbhub"].server_command)
    assert profile["reconnect_attempts"] == 0
    assert profile["operation_timeout"] == 20.0
    assert manager.scrubbed == [connected["session_id"]]
    assert set(profile["environment"]) == {"MCP_DATABASE_HANDSHAKE_B64"}
    handshake = json.loads(
        base64.urlsafe_b64decode(
            profile["environment"]["MCP_DATABASE_HANDSHAKE_B64"]
        ).decode("utf-8")
    )
    assert handshake == {
        "settings": {
            "engine": "postgresql",
            "host": "database.example.com",
            "port": 5432,
            "database": "analytics",
            "username": "readonly",
            "tls_mode": "verify-full",
        },
        "credentials": {"password": "secret-for-cred_dbhub"},
        "workspace_id": None,
    }
    serialized = json.dumps(service.list_adapters(), ensure_ascii=False)
    assert "secret-for-cred_dbhub" not in serialized
    assert "server_command" not in serialized
    public = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "dbhub"
    )
    assert public["database_policy"]["read_only"] is True
    assert public["database_policy"]["max_rows_hard"] == 1000
    assert public["preflight_status"] == "verified"
    assert public["credential_verification"] == "verified"
    assert all(
        policy["read_only"] and policy["effect"] == "read"
        for policy in public["tool_policies"].values()
    )


def test_wave_five_rejects_connection_strings_and_raw_secrets() -> None:
    service, _, _, _ = make_service()
    for forbidden, value in (
        ("dsn", "postgresql://readonly:secret@database.example.com/app"),
        ("connection_uri", "mongodb://database.example.com/app"),
        ("password", "raw-secret"),
        ("certificate_path", "C:/private/client.pem"),
    ):
        with pytest.raises(catalog.CatalogAdapterPolicyError, match="不能包含命令"):
            service.configure(
                "dbhub",
                CatalogConfigurationRequest(settings={forbidden: value}),
            )


def test_wave_five_status_and_blocked_subbatch_are_exact() -> None:
    blocked = {
        "postgres-mcp",
        "sqlite-mcp",
        "cognee-mcp",
        "graphiti-mcp",
        "hindsight-mcp",
    }
    assert {
        project_id
        for project_id in WAVE_PROJECTS[5]
        if CATALOG_ADAPTERS[project_id].availability == "blocked"
    } == blocked
    for project_id in blocked:
        manifest = CATALOG_ADAPTERS[project_id]
        assert manifest.server_command == ()
        assert manifest.executable is False
        assert manifest.network_policy == "blocked:no-production-runtime"
    duckdb = CATALOG_ADAPTERS["duckdb-mcp"]
    assert duckdb.workspace_policy is not None
    assert duckdb.workspace_policy.accepted_extensions == (".duckdb",)
    assert duckdb.credential_policies == ()
    assert duckdb.network_policy == "disabled"
    dbhub = CATALOG_ADAPTERS["dbhub"]
    engine_policy = next(item for item in dbhub.setting_policies if item.key == "engine")
    assert {value for value, _ in engine_policy.options} == {
        "postgresql", "mysql", "mariadb",
    }
    supabase = CATALOG_ADAPTERS["supabase-mcp"]
    assert set(supabase.tool_policies) == {
        "list_tables", "list_extensions", "execute_sql",
    }
    assert supabase.setting_policies[0].key == "project_ref"
    assert supabase.setting_policies[0].pattern == r"^[a-z]{20}$"
    assert supabase.credential_policies[0].key == "access_token"


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

    for forbidden in (
        "server_command",
        "url",
        "headers",
        "environment",
        "cwd",
    ):
        with pytest.raises(ValidationError):
            CatalogConfigurationRequest.model_validate({forbidden: "denied"})

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


def _enable_wave_six(monkeypatch: pytest.MonkeyPatch, project_id: str) -> None:
    monkeypatch.setenv("MCP_CATALOG_STATEFUL_SAAS_SINGLE_USER_ACK", "true")
    monkeypatch.setenv(
        f"MCP_CATALOG_ENABLE_{project_id.upper().replace('-', '_')}",
        "true",
    )


def _install_fake_wave_six_schema(
    service: MCPCatalogService,
    manager: FakeManager,
    project_id: str,
) -> CatalogAdapterManifest:
    manifest = service.manifests[project_id]
    tools = [
        Tool(name=name, description=name, inputSchema={"type": "object"})
        for name in manifest.tool_policies
    ]
    manager.tools = tools
    assert manifest.saas_policy is not None
    service.manifests[project_id] = replace(
        manifest,
        saas_policy=replace(
            manifest.saas_policy,
            tool_schema_sha256=service._tool_schema_digest(tools),
        ),
    )
    return service.manifests[project_id]


def test_wave_six_freezes_four_ready_and_two_blocked_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert set(WAVE_SIX_ADAPTERS) == {
        "airtable-mcp",
        "asana-mcp",
        "gitlab-mcp",
        "notion-mcp-server",
    }
    for project_id, spec in WAVE_SIX_ADAPTERS.items():
        manifest = CATALOG_ADAPTERS[project_id]
        assert manifest.availability == "ready"
        assert manifest.runtime_image == "modelmirror-mcp-saas:wave6-v1"
        assert manifest.server_command == (
            catalog.sys.executable,
            "-m",
            "mcp.saas_proxy",
            project_id,
        )
        assert manifest.saas_policy is not None
        assert manifest.saas_policy.fixed_hosts == spec.fixed_hosts
        assert manifest.saas_policy.tool_schema_sha256 == SAAS_SCHEMA_SHA256[project_id]
        assert (
            manifest.saas_policy.rate_limit_per_minute
            == spec.rate_limit_per_minute
        )
        assert manifest.executable is False
        monkeypatch.setenv(manifest.feature_flag, "true")
        assert manifest.executable is False
        monkeypatch.setenv("MCP_CATALOG_STATEFUL_SAAS_SINGLE_USER_ACK", "true")
        assert manifest.executable is True
        monkeypatch.delenv(manifest.feature_flag)
        monkeypatch.delenv("MCP_CATALOG_STATEFUL_SAAS_SINGLE_USER_ACK")
        for tool_name in spec.write_tools:
            policy = manifest.tool_policies[tool_name]
            assert policy.read_only is False
            assert policy.requires_approval is True
            assert policy.effect == "state-write"
        assert not {
            "delete_record",
            "delete_task",
            "delete_issue",
            "merge_merge_request",
            "delete_page",
        } & set(manifest.tool_policies)

    assert CATALOG_ADAPTERS["mcp-cn-commerce"].availability == "blocked"
    assert CATALOG_ADAPTERS["mem0-mcp"].availability == "blocked"
    notion = CATALOG_ADAPTERS["notion-mcp-server"]
    assert notion.allowed_settings == ("data_source_id",)
    assert notion.saas_policy is not None
    assert notion.saas_policy.fixed_hosts == ("api.notion.com",)


@pytest.mark.asyncio
async def test_wave_six_connect_rejects_input_schema_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "airtable-mcp")
    service, manager, _, _ = make_service()
    manifest = CATALOG_ADAPTERS["airtable-mcp"]
    manager.tools = [
        Tool(name=name, description=name, inputSchema={"type": "object"})
        for name in manifest.tool_policies
    ]
    service.configure(
        "airtable-mcp",
        CatalogConfigurationRequest(
            settings={"base_id": "appABCDEFGHIJKLMN"},
            credential_bindings={"personal_access_token": "cred_airtable"},
        ),
    )

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="Schema"):
        await service.connect("airtable-mcp")

    assert service._scope_key("airtable-mcp") not in service._sessions
    assert not manager.sessions


@pytest.mark.asyncio
async def test_wave_six_remote_approval_freezes_context_and_replays_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "airtable-mcp")
    service, manager, _, _ = make_service()
    manifest = _install_fake_wave_six_schema(service, manager, "airtable-mcp")
    service.configure(
        "airtable-mcp",
        CatalogConfigurationRequest(
            settings={"base_id": "appABCDEFGHIJKLMN"},
            credential_bindings={"personal_access_token": "cred_airtable"},
        ),
    )
    connected = await service.connect("airtable-mcp")
    assert manager.profiles[-1]["session_owner"] == service._session_owner(
        "airtable-mcp"
    )
    status = service.list_adapters()["adapters"]
    airtable = next(item for item in status if item["project_id"] == "airtable-mcp")
    assert airtable["account_status"] == "verified"
    assert airtable["preflight_status"] == "verified"

    with pytest.raises(catalog.CatalogApprovalRequiredError) as captured:
        await service.call_tool(
            "airtable-mcp",
            "update_record",
            {
                "table_id": "tblPublic",
                "record_id": "rec123456789",
                "fields": {"secret_note": "正文不应进入审批摘要"},
            },
        )
    approval = captured.value.payload
    assert approval["target_preview"]["resource"]["id_suffix"] == "456789"
    assert "正文不应进入审批摘要" not in json.dumps(
        approval["target_preview"], ensure_ascii=False
    )
    assert re.fullmatch(r"mcpidem_[0-9a-f]{32}", approval["idempotency_key"])
    assert manager.calls == []

    manager.call_gate = asyncio.Event()
    first_task = asyncio.create_task(
        service.confirm_approval("airtable-mcp", approval["approval_id"])
    )
    while not manager.calls:
        await asyncio.sleep(0)
    with pytest.raises(CatalogUnknownOutcomeError):
        await service.confirm_approval("airtable-mcp", approval["approval_id"])
    with pytest.raises(catalog.CatalogAdapterPolicyError):
        await service.cancel_approval("airtable-mcp", approval["approval_id"])
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="写入仍在执行"):
        await service.disconnect("airtable-mcp")
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="写入仍在执行"):
        await service.unbind(
            "airtable-mcp",
            CatalogUnbindRequest(revoke_credentials=False),
        )
    revoked_while_writing: list[str] = []
    service.credential_revoker = lambda credential_id: revoked_while_writing.append(
        credential_id
    )
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="写入仍在执行"):
        await service.revoke_credential("airtable-mcp", "cred_airtable")
    assert revoked_while_writing == []
    assert service._scope_key("airtable-mcp") in service._sessions
    manager.call_gate.set()
    first = await first_task
    manager.call_gate = None
    assert first["idempotent_replay"] is False
    assert manager.calls[-1][0] == connected["session_id"]
    assert manager.calls[-1][2]["__modelmirror_idempotency_key"] == approval["idempotency_key"]
    assert manager.retry_on_failure == [False]
    replay = await service.confirm_approval(
        "airtable-mcp",
        approval["approval_id"],
    )
    assert replay["idempotent_replay"] is True
    assert len(manager.calls) == 1


@pytest.mark.asyncio
async def test_wave_six_approval_rejects_drift_and_marks_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "gitlab-mcp")
    service, manager, _, _ = make_service()
    manifest = _install_fake_wave_six_schema(service, manager, "gitlab-mcp")
    service.configure(
        "gitlab-mcp",
        CatalogConfigurationRequest(
            settings={"project_id": 12345},
            credential_bindings={"personal_access_token": "cred_gitlab"},
        ),
    )
    await service.connect("gitlab-mcp")

    async def request_approval() -> dict[str, Any]:
        with pytest.raises(catalog.CatalogApprovalRequiredError) as captured:
            await service.call_tool(
                "gitlab-mcp",
                "create_issue",
                {"title": "安全复核", "description": "frozen"},
            )
        return captured.value.payload

    drifted = await request_approval()
    scope_key = service._scope_key("gitlab-mcp")
    current = service._configuration_snapshots[scope_key]
    service._configuration_snapshots[scope_key] = CatalogConfigurationSnapshot(
        revision="mcpcfg_changed",
        digest=current.digest,
    )
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="已经变化"):
        await service.confirm_approval("gitlab-mcp", drifted["approval_id"])

    service._configuration_snapshots[scope_key] = current
    unknown = await request_approval()
    manager.call_error = RuntimeError("sidecar unknown outcome")
    with pytest.raises(CatalogUnknownOutcomeError) as captured_unknown:
        await service.confirm_approval("gitlab-mcp", unknown["approval_id"])
    assert captured_unknown.value.idempotency_key == unknown["idempotency_key"]
    with pytest.raises(CatalogUnknownOutcomeError):
        await service.confirm_approval("gitlab-mcp", unknown["approval_id"])
    assert len(manager.calls) == 1

    manager.call_error = None
    manager.call_is_error = True
    rejected = await request_approval()
    with pytest.raises(CatalogUnknownOutcomeError) as rejected_result:
        await service.confirm_approval("gitlab-mcp", rejected["approval_id"])
    assert rejected_result.value.idempotency_key == rejected["idempotency_key"]
    assert len(manager.calls) == 2
    await service.unbind(
        "gitlab-mcp",
        CatalogUnbindRequest(revoke_credentials=False),
    )
    with pytest.raises(CatalogUnknownOutcomeError):
        await service.confirm_approval("gitlab-mcp", rejected["approval_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "expected_code", "expected_status"),
    [
        ("rate_limited", "provider_rate_limited", 429),
        ("provider_rejected", "provider_rejected", 409),
    ],
)
async def test_wave_six_definite_provider_rejection_is_not_unknown(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    expected_code: str,
    expected_status: int,
) -> None:
    _enable_wave_six(monkeypatch, "gitlab-mcp")
    service, manager, _, _ = make_service()
    _install_fake_wave_six_schema(service, manager, "gitlab-mcp")
    service.configure(
        "gitlab-mcp",
        CatalogConfigurationRequest(
            settings={"project_id": 12345},
            credential_bindings={"personal_access_token": "cred_gitlab"},
        ),
    )
    await service.connect("gitlab-mcp")
    with pytest.raises(catalog.CatalogApprovalRequiredError) as approval_required:
        await service.call_tool(
            "gitlab-mcp",
            "create_issue",
            {"title": "安全复核", "description": "frozen"},
        )
    approval = approval_required.value.payload
    manager.call_error = McpError(
        ErrorData(
            code=-32009,
            message="SaaS write rejected",
            data={"reason": reason, "retryable": False},
        )
    )

    with pytest.raises(catalog.CatalogProviderRejectedError) as rejected:
        await service.confirm_approval("gitlab-mcp", approval["approval_id"])
    assert rejected.value.reason == reason
    ledger = service._execution_ledger[service._approval_key(approval["approval_id"])]
    assert ledger.state == "rejected"
    assert len(manager.calls) == 1
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="不存在或已经失效"):
        await service.confirm_approval("gitlab-mcp", approval["approval_id"])

    with pytest.raises(HTTPException) as response:
        catalog._raise_http_error(rejected.value)
    assert response.value.status_code == expected_status
    assert response.value.detail["code"] == expected_code
    assert response.value.detail["idempotency_key"] == approval["idempotency_key"]


@pytest.mark.asyncio
async def test_wave_six_unbind_clears_session_configuration_and_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "asana-mcp")
    service, manager, _, _ = make_service()
    manifest = _install_fake_wave_six_schema(service, manager, "asana-mcp")
    service.configure(
        "asana-mcp",
        CatalogConfigurationRequest(
            settings={"workspace_gid": "100", "project_gid": "200"},
            credential_bindings={"personal_access_token": "cred_asana"},
        ),
    )
    await service.connect("asana-mcp")
    with pytest.raises(catalog.CatalogApprovalRequiredError) as captured:
        await service.call_tool("asana-mcp", "create_task", {"name": "Review"})
    result = await service.unbind(
        "asana-mcp",
        CatalogUnbindRequest(revoke_credentials=False),
    )
    assert result == {
        "ok": True,
        "project_id": "asana-mcp",
        "disconnected": True,
        "revoked_credentials": 0,
    }
    adapter = next(
        item
        for item in service.list_adapters()["adapters"]
        if item["project_id"] == "asana-mcp"
    )
    assert adapter["account_status"] == "unbound"
    assert adapter["configured"] is False
    with pytest.raises(catalog.CatalogAdapterPolicyError):
        await service.confirm_approval("asana-mcp", captured.value.payload["approval_id"])

    guarded, guarded_manager, _, _ = make_service()
    _install_fake_wave_six_schema(guarded, guarded_manager, "asana-mcp")
    guarded.configure(
        "asana-mcp",
        CatalogConfigurationRequest(
            settings={"workspace_gid": "100", "project_gid": "200"},
            credential_bindings={"personal_access_token": "cred_asana"},
        ),
    )
    await guarded.connect("asana-mcp")
    guarded_scope = guarded._scope_key("asana-mcp")
    with pytest.raises(catalog.CatalogAdapterUnavailableError):
        await guarded.unbind(
            "asana-mcp",
            CatalogUnbindRequest(revoke_credentials=True),
        )
    assert guarded_scope in guarded._sessions
    assert guarded_scope in guarded._configurations

    revoked: list[str] = []

    def revoke_failure_after_disconnect(credential_id: str) -> SimpleNamespace:
        assert credential_id == "cred_asana"
        assert guarded_scope not in guarded._sessions
        assert guarded_scope in guarded._configurations
        raise RuntimeError("vault unavailable")

    guarded.credential_revoker = revoke_failure_after_disconnect
    with pytest.raises(RuntimeError, match="vault unavailable"):
        await guarded.unbind(
            "asana-mcp",
            CatalogUnbindRequest(revoke_credentials=True),
        )
    assert guarded_scope not in guarded._sessions
    assert guarded_scope in guarded._configurations

    def revoke_before_cleanup(credential_id: str) -> SimpleNamespace:
        assert guarded_scope not in guarded._sessions
        assert guarded_scope in guarded._configurations
        revoked.append(credential_id)
        return SimpleNamespace(credential_id=credential_id, status="revoked")

    guarded.credential_revoker = revoke_before_cleanup
    revoked_result = await guarded.unbind(
        "asana-mcp",
        CatalogUnbindRequest(revoke_credentials=True),
    )
    assert revoked_result["revoked_credentials"] == 1
    assert revoked == ["cred_asana"]


@pytest.mark.asyncio
async def test_wave_six_unbind_blocks_new_calls_and_waits_for_active_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "asana-mcp")
    service, manager, _, _ = make_service()
    manifest = _install_fake_wave_six_schema(service, manager, "asana-mcp")
    configuration = CatalogConfigurationRequest(
        settings={"workspace_gid": "100", "project_gid": "200"},
        credential_bindings={"personal_access_token": "cred_asana"},
    )
    service.configure("asana-mcp", configuration)
    await service.connect("asana-mcp")

    with pytest.raises(catalog.CatalogApprovalRequiredError) as captured:
        await service.call_tool("asana-mcp", "create_task", {"name": "Review"})
    approval_id = captured.value.payload["approval_id"]

    manager.call_gate = asyncio.Event()
    active_read = asyncio.create_task(
        service.call_tool("asana-mcp", "list_tasks", {})
    )
    while not manager.calls:
        await asyncio.sleep(0)

    queued_execute_started = asyncio.Event()
    original_execute_tool = service._execute_tool

    async def tracked_execute_tool(
        tracked_manifest: CatalogAdapterManifest,
        **kwargs: Any,
    ) -> dict[str, Any]:
        queued_execute_started.set()
        return await original_execute_tool(tracked_manifest, **kwargs)

    monkeypatch.setattr(service, "_execute_tool", tracked_execute_tool)
    queued_read = asyncio.create_task(
        service.call_tool("asana-mcp", "list_tasks", {})
    )
    await queued_execute_started.wait()

    async with service._lock:
        unbind_task = asyncio.create_task(
            service.unbind(
                "asana-mcp",
                CatalogUnbindRequest(revoke_credentials=False),
            )
        )
        scope_key = service._scope_key("asana-mcp")
        while scope_key not in service._unbinding_scopes:
            await asyncio.sleep(0)

        with pytest.raises(catalog.CatalogAdapterPolicyError, match="不能确认写入"):
            await service.confirm_approval("asana-mcp", approval_id)

    with pytest.raises(catalog.CatalogAdapterPolicyError, match="正在解绑"):
        await service.call_tool("asana-mcp", "list_tasks", {})
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="正在解绑"):
        service.configure("asana-mcp", configuration)
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="正在解绑"):
        await service.connect("asana-mcp")
    assert len(manager.calls) == 1

    manager.call_gate.set()
    await active_read
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="不能调用工具"):
        await queued_read
    result = await unbind_task
    assert result["disconnected"] is True
    assert scope_key not in service._unbinding_scopes
    assert scope_key not in service._sessions


@pytest.mark.asyncio
async def test_wave_six_unbind_cancels_connect_before_session_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "asana-mcp")
    service, manager, _, _ = make_service()
    _install_fake_wave_six_schema(service, manager, "asana-mcp")
    service.configure(
        "asana-mcp",
        CatalogConfigurationRequest(
            settings={"workspace_gid": "100", "project_gid": "200"},
            credential_bindings={"personal_access_token": "cred_asana"},
        ),
    )
    manager.connect_gate = asyncio.Event()
    connect_task = asyncio.create_task(service.connect("asana-mcp"))
    await manager.connect_started.wait()

    unbind_task = asyncio.create_task(
        service.unbind(
            "asana-mcp",
            CatalogUnbindRequest(revoke_credentials=False),
        )
    )
    scope_key = service._scope_key("asana-mcp")
    while scope_key not in service._unbinding_scopes:
        await asyncio.sleep(0)
    assert not unbind_task.done()

    manager.connect_gate.set()
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="未发布"):
        await connect_task
    unbound = await unbind_task
    assert unbound["disconnected"] is False
    assert scope_key not in service._sessions
    assert scope_key not in service._configurations
    assert not manager.sessions


@pytest.mark.asyncio
async def test_wave_six_configuration_cannot_change_while_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "asana-mcp")
    service, manager, _, _ = make_service()
    _install_fake_wave_six_schema(service, manager, "asana-mcp")
    original = CatalogConfigurationRequest(
        settings={"workspace_gid": "100", "project_gid": "200"},
        credential_bindings={"personal_access_token": "cred_asana"},
    )
    replacement = CatalogConfigurationRequest(
        settings={"workspace_gid": "300", "project_gid": "400"},
        credential_bindings={"personal_access_token": "cred_asana"},
    )
    service.configure("asana-mcp", original)
    manager.connect_gate = asyncio.Event()
    connect_task = asyncio.create_task(service.connect("asana-mcp"))
    await manager.connect_started.wait()

    scope_key = service._scope_key("asana-mcp")
    assert scope_key in service._connecting_scopes
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="连接正在建立"):
        service.configure("asana-mcp", replacement)
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="连接正在建立"):
        await service.connect("asana-mcp")

    handshake = manager.profiles[-1]["environment"]["MCP_SAAS_HANDSHAKE_B64"]
    handshake_payload = json.loads(base64.urlsafe_b64decode(handshake).decode("utf-8"))
    assert handshake_payload["settings"] == original.settings
    assert service._configurations[scope_key].settings == original.settings

    manager.connect_gate.set()
    await connect_task
    assert scope_key not in service._connecting_scopes
    assert service._configurations[scope_key].settings == original.settings


@pytest.mark.asyncio
async def test_wave_six_bound_credential_revoke_drains_calls_and_is_fail_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_wave_six(monkeypatch, "asana-mcp")
    service, manager, _, _ = make_service()
    _install_fake_wave_six_schema(service, manager, "asana-mcp")
    configuration = CatalogConfigurationRequest(
        settings={"workspace_gid": "100", "project_gid": "200"},
        credential_bindings={"personal_access_token": "cred_asana"},
    )
    service.configure("asana-mcp", configuration)
    await service.connect("asana-mcp")
    scope_key = service._scope_key("asana-mcp")

    def fail_revoke(_: str) -> SimpleNamespace:
        raise RuntimeError("vault unavailable")

    service.credential_revoker = fail_revoke
    with pytest.raises(RuntimeError, match="vault unavailable"):
        await service.revoke_credential("asana-mcp", "cred_asana")
    assert scope_key in service._sessions
    assert scope_key in service._configurations
    assert scope_key not in service._unbinding_scopes

    revoked: list[str] = []

    def revoke(credential_id: str) -> SimpleNamespace:
        revoked.append(credential_id)
        return SimpleNamespace(
            credential_id=credential_id,
            name="Asana",
            kind="provider_key",
            masked_value="****",
            status="revoked",
            catalog_project_id="asana-mcp",
            catalog_slot="personal_access_token",
        )

    service.credential_revoker = revoke
    manager.call_gate = asyncio.Event()
    active_read = asyncio.create_task(
        service.call_tool("asana-mcp", "list_tasks", {})
    )
    while not manager.calls:
        await asyncio.sleep(0)
    revoke_task = asyncio.create_task(
        service.revoke_credential("asana-mcp", "cred_asana")
    )
    while scope_key not in service._unbinding_scopes:
        await asyncio.sleep(0)
    assert revoked == []
    with pytest.raises(catalog.CatalogAdapterPolicyError, match="正在解绑"):
        await service.call_tool("asana-mcp", "list_tasks", {})

    manager.call_gate.set()
    await active_read
    revoked_public = await revoke_task
    assert revoked_public["status"] == "revoked"
    assert revoked == ["cred_asana"]
    assert scope_key not in service._sessions
    assert scope_key not in service._configurations


def test_catalog_scope_keys_include_tenant_and_owner() -> None:
    service, _, _, _ = make_service()
    service.tenant_id = "tenant-a"
    service.owner_id = "owner-a"
    assert service._scope_key("airtable-mcp") == (
        "tenant-a",
        "owner-a",
        "airtable-mcp",
    )
    assert service._approval_key("approval") == (
        "tenant-a",
        "owner-a",
        "approval",
    )
