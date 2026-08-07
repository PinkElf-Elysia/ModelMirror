from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.types import CallToolResult, TextContent, Tool
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
    WAVE_PROJECTS,
    CatalogAdapterManifest,
    CatalogConfigurationRequest,
    CatalogCredentialCreateRequest,
    MCPCatalogService,
)
from server.toolsets.credentials import CredentialStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeManager:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.disconnected: list[str] = []
        self.sessions: set[str] = set()
        self.profiles: list[dict[str, Any]] = []
        self.scrubbed: list[str] = []
        self.call_is_error = False

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

    async def scrub_session_environment(self, session_id: str) -> None:
        assert session_id in self.sessions
        self.scrubbed.append(session_id)

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        self.calls.append((session_id, tool_name, dict(arguments)))
        return CallToolResult(
            content=[TextContent(type="text", text=str(arguments.get("value", "ok")))],
            isError=self.call_is_error,
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
    credential_scopes = {
        "cred_agentql": ("agentql-mcp", "api_key"),
        "cred_grafana": ("grafana-mcp", "service_token"),
        "cred_pinecone": ("pinecone-assistant-mcp", "api_key"),
        "cred_dbhub": ("dbhub", "password"),
        "cred_supabase": ("supabase-mcp", "access_token"),
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
    ) == 38
    assert sum(
        manifest.availability == "planned"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 53
    assert sum(
        manifest.availability == "blocked"
        for manifest in CATALOG_ADAPTERS.values()
    ) == 9
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
    manifest = CATALOG_ADAPTERS["airtable-mcp"]
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
            assert payload["ready"] == 38
            assert payload["planned"] == 53
            assert payload["blocked"] == 9
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
