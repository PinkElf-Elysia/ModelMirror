from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator

try:
    from server.mcp.manager import MCPClientManager
    from server.xpert_runtime.toolset import (
        RuntimeTool,
        RuntimeToolCall,
        RuntimeToolError,
        RuntimeToolResult,
    )
except ModuleNotFoundError:  # Docker copies server packages directly under /app.
    from mcp.manager import MCPClientManager
    from xpert_runtime.toolset import (
        RuntimeTool,
        RuntimeToolCall,
        RuntimeToolError,
        RuntimeToolResult,
    )

from .credentials import CredentialStore
from .api_compiler import (
    CompiledAPISpec,
    compare_toolsets,
    compile_odata,
    compile_openapi,
    parse_openapi_text,
)
from .http_executor import SafeAPIExecutor
from .providers import BuiltinToolProviderRegistry
from .models import (
    MCPConnectionProfile,
    ToolDefinition,
    ToolsetDefinition,
    ToolsetVersion,
)
from .store import (
    ToolsetNotFoundError,
    ToolsetStore,
    ToolsetValidationError,
)


ToolTestRunner = Callable[[RuntimeToolCall], Awaitable[RuntimeToolResult]]
InstalledProjectResolver = Callable[[str], dict[str, Any] | None]


def _schema_hash(schema: dict[str, Any]) -> str:
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ToolsetService:
    def __init__(
        self,
        store: ToolsetStore,
        credentials: CredentialStore,
        mcp_manager: MCPClientManager,
        installed_project_resolver: InstalledProjectResolver | None = None,
        api_executor: SafeAPIExecutor | None = None,
        builtin_providers: BuiltinToolProviderRegistry | None = None,
    ) -> None:
        self.store = store
        self.credentials = credentials
        self.mcp_manager = mcp_manager
        self.installed_project_resolver = installed_project_resolver
        self.api_executor = api_executor or SafeAPIExecutor(credentials)
        self.builtin_providers = builtin_providers or BuiltinToolProviderRegistry(
            credentials
        )
        self._draft_sessions: dict[str, str] = {}
        self._version_sessions: dict[tuple[str, int], str] = {}
        self._version_warnings: dict[tuple[str, int], list[str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tool_test_runner: ToolTestRunner | None = None

    def set_tool_test_runner(self, runner: ToolTestRunner) -> None:
        self._tool_test_runner = runner

    async def ensure_builtin_toolsets(self) -> list[str]:
        """Materialize each builtin Provider as one stable default Toolset."""

        warnings: list[str] = []
        existing = self.store.list_toolsets(limit=500)
        for provider in self.builtin_providers.list_providers():
            provider_id = str(provider["id"])
            matches = sorted(
                (
                    item
                    for item in existing
                    if item.kind == "builtin"
                    and item.connection.provider_id == provider_id
                    and item.status != "archived"
                ),
                key=lambda item: (item.created_at, item.id),
            )
            if matches:
                item = matches[0]
            else:
                item = self.store.create_toolset(
                    name=str(provider["title"]),
                    kind="builtin",
                    description=str(provider["description"]),
                    tags=["builtin", "default-provider"],
                    connection={
                        "provider_id": provider_id,
                        "timeout_seconds": 30,
                        "response_limit_bytes": 2 * 1024 * 1024,
                    },
                )
                existing.append(item)
            try:
                if (
                    provider.get("credential_required")
                    and not item.connection.provider_credential_id
                ):
                    continue
                expected_tools = self.builtin_providers.provider_tools(provider_id)
                current_by_name = {
                    tool.original_name: tool for tool in item.tools
                }
                needs_refresh = item.runtime_status != "ready" or {
                    tool.original_name for tool in expected_tools
                } != set(current_by_name)
                if not needs_refresh:
                    needs_refresh = any(
                        current_by_name[tool.original_name].schema_hash
                        != _schema_hash(tool.input_schema)
                        for tool in expected_tools
                    )
                if needs_refresh:
                    item = await self.connect(item.id, enable_new_tools=True)
                if provider_id == "todos" and item.published_version is None:
                    await self.publish(
                        item.id,
                        expected_revision=item.revision,
                        release_notes="Default builtin Provider",
                    )
            except Exception as exc:
                warnings.append(f"{provider_id}: {str(exc)[:300]}")
        return warnings

    async def builtin_provider_catalog(self) -> list[dict[str, Any]]:
        await self.ensure_builtin_toolsets()
        items = self.store.list_toolsets(limit=500)
        result: list[dict[str, Any]] = []
        for provider in self.builtin_providers.list_providers():
            provider_id = str(provider["id"])
            matches = sorted(
                (
                    item
                    for item in items
                    if item.kind == "builtin"
                    and item.connection.provider_id == provider_id
                    and item.status != "archived"
                ),
                key=lambda item: (item.created_at, item.id),
            )
            default = matches[0] if matches else None
            result.append(
                {
                    **provider,
                    "singleton": True,
                    "default_toolset_id": default.id if default else None,
                    "configuration_status": (
                        "ready"
                        if default is not None
                        and default.runtime_status == "ready"
                        and (
                            not provider.get("credential_required")
                            or bool(default.connection.provider_credential_id)
                        )
                        else "credential_required"
                        if provider.get("credential_required")
                        else "unavailable"
                    ),
                    "published_version": (
                        default.published_version if default is not None else None
                    ),
                }
            )
        return result

    async def configure_builtin_provider(
        self,
        provider_id: str,
        *,
        name: str,
        description: str,
        credential_id: str,
        tags: list[str],
    ) -> ToolsetDefinition:
        provider = self.builtin_providers.get_provider(provider_id)
        await self.ensure_builtin_toolsets()
        matches = sorted(
            (
                item
                for item in self.store.list_toolsets(limit=500)
                if item.kind == "builtin"
                and item.connection.provider_id == provider_id
                and item.status != "archived"
            ),
            key=lambda item: (item.created_at, item.id),
        )
        if not matches:
            raise ToolsetValidationError(
                f"Default builtin Provider Toolset is unavailable: {provider_id}."
            )
        item = matches[0]
        resolved_credential = (
            str(credential_id or "").strip()
            or item.connection.provider_credential_id
        )
        if provider.get("credential_required") and not resolved_credential:
            raise ToolsetValidationError("A Provider credential is required.")
        connection = item.connection.model_copy(deep=True)
        connection.provider_credential_id = resolved_credential
        item = self.store.update_toolset(
            item.id,
            revision=item.revision,
            patch={
                "name": str(name or provider["title"]),
                "description": str(description or provider["description"]),
                "tags": list(tags or item.tags),
                "connection": connection.model_dump(mode="json"),
            },
        )
        return await self.connect(item.id, enable_new_tools=True)

    async def connect(
        self,
        toolset_id: str,
        *,
        enable_new_tools: bool = False,
    ) -> ToolsetDefinition:
        lock = self._locks.setdefault(toolset_id, asyncio.Lock())
        async with lock:
            item = self.store.get_toolset(toolset_id)
            if item.kind == "builtin":
                provider_id = item.connection.provider_id.strip()
                self.builtin_providers.get_provider(provider_id)
                tools = self.builtin_providers.provider_tools(provider_id)
                return self.store.replace_discovered_tools(
                    toolset_id,
                    tools=tools,
                    runtime_status="ready",
                    enable_new_tools=enable_new_tools,
                )
            if item.kind != "mcp":
                if not item.tools or not item.connection.api_base_url:
                    raise ToolsetValidationError(
                        "Import an API specification before validating this Toolset."
                    )
                return self.store.set_runtime_state(
                    toolset_id,
                    status="ready",
                    session_id=None,
                )
            existing = self._draft_sessions.pop(toolset_id, None)
            if existing:
                await self._safe_disconnect(existing)
            session_id: str | None = None
            try:
                session_id = await self._connect_profile(item.connection)
                raw_tools = await self.mcp_manager.list_tools(session_id)
                tools = [_normalize_tool(tool) for tool in raw_tools]
                updated = self.store.replace_discovered_tools(
                    toolset_id,
                    tools=tools,
                )
                updated = self.store.set_runtime_state(
                    toolset_id,
                    status="connected",
                    session_id=session_id,
                )
                self._draft_sessions[toolset_id] = session_id
                return updated
            except Exception as exc:
                if session_id:
                    await self._safe_disconnect(session_id)
                self.store.set_runtime_state(
                    toolset_id,
                    status="error",
                    session_id=None,
                    error=str(exc),
                )
                raise

    async def disconnect(self, toolset_id: str) -> ToolsetDefinition:
        item = self.store.get_toolset(toolset_id)
        session_id = self._draft_sessions.pop(toolset_id, None)
        if session_id:
            await self._safe_disconnect(session_id)
        return self.store.set_runtime_state(
            toolset_id,
            status="disconnected" if item.kind == "mcp" else "ready",
            session_id=None,
        )

    async def import_spec(
        self,
        toolset_id: str,
        *,
        document_text: str,
        base_url: str = "",
        source_url: str = "",
        source_label: str = "",
    ) -> ToolsetDefinition:
        item = self.store.get_toolset(toolset_id)
        if item.kind == "mcp":
            raise ToolsetValidationError("MCP Toolsets do not import API specifications.")
        compiled = self._compile_api_document(
            item.kind,
            document_text,
            base_url=base_url or item.connection.api_base_url,
        )
        drift = compare_toolsets(item.tools, compiled.tools) if item.tools else {
            "breaking": [],
            "warnings": [],
            "added": [tool.original_name for tool in compiled.tools],
            "removed": [],
            "compatible": True,
        }
        connection = item.connection.model_copy(deep=True)
        connection.api_base_url = compiled.base_url
        connection.api_source_url = str(source_url or "").strip()[:2048]
        connection.api_source_label = str(source_label or source_url or "manual").strip()[:300]
        connection.api_spec_version = compiled.source_version
        connection.api_spec_hash = compiled.source_hash
        return self.store.replace_discovered_tools(
            toolset_id,
            tools=compiled.tools,
            runtime_status="ready",
            import_warnings=[*compiled.warnings, *drift.get("warnings", [])],
            drift_report=drift,
            connection=connection,
        )

    async def import_spec_from_url(
        self,
        toolset_id: str,
        *,
        url: str,
        base_url: str = "",
    ) -> ToolsetDefinition:
        item = self.store.get_toolset(toolset_id)
        if item.kind == "mcp":
            raise ToolsetValidationError("MCP Toolsets do not import API specifications.")
        document = await self.api_executor.fetch_text(
            url,
            network_policy=item.connection.network_policy,
            timeout_seconds=item.connection.timeout_seconds,
        )
        return await self.import_spec(
            toolset_id,
            document_text=document,
            base_url=base_url,
            source_url=url,
            source_label=url,
        )

    async def refresh_spec(self, toolset_id: str) -> ToolsetDefinition:
        item = self.store.get_toolset(toolset_id)
        if not item.connection.api_source_url:
            raise ToolsetValidationError(
                "Only URL-imported API Toolsets can be refreshed automatically."
            )
        return await self.import_spec_from_url(
            toolset_id,
            url=item.connection.api_source_url,
            base_url=item.connection.api_base_url,
        )

    async def publish(
        self,
        toolset_id: str,
        *,
        expected_revision: int,
        release_notes: str = "",
    ) -> ToolsetVersion:
        item = self.store.get_toolset(toolset_id)
        connection = (
            self._resolve_installed_project(item.connection)
            if item.kind == "mcp"
            else item.connection
        )
        if item.kind in {"openapi", "odata"}:
            await self._validate_api_publish(item)
        elif item.kind == "builtin":
            self._validate_builtin_publish(item)
        return self.store.publish(
            toolset_id,
            revision=expected_revision,
            release_notes=release_notes,
            connection_override=connection,
        )

    async def test_tool(
        self,
        toolset_id: str,
        raw_name: str,
        arguments: dict[str, Any],
        *,
        confirm_mutating: bool = False,
    ) -> RuntimeToolResult:
        item = self.store.get_toolset(toolset_id)
        tool = next((row for row in item.tools if row.raw_name == raw_name), None)
        if tool is None:
            raise ToolsetNotFoundError(f"Tool not found: {raw_name}")
        if not tool.read_only and not confirm_mutating:
            raise ToolsetValidationError(
                "Mutating API operation tests require explicit confirmation."
            )
        session_id = self._draft_sessions.get(toolset_id)
        if item.kind == "mcp" and not session_id:
            raise ToolsetValidationError("Connect the Toolset before testing a tool.")
        merged = {**deepcopy(tool.default_arguments), **dict(arguments)}
        _validate_arguments(tool, merged)
        call = RuntimeToolCall(
            tool_name=tool.exposed_name,
            arguments=merged,
            metadata={
                "toolset_test": True,
                "toolset_id": toolset_id,
                "toolset_raw_name": raw_name,
                "toolset_session_id": session_id,
                "toolset_kind": item.kind,
            },
        )
        if self._tool_test_runner is not None:
            return await self._tool_test_runner(call)
        if item.kind == "mcp":
            result = await self.mcp_manager.call_tool(session_id, raw_name, merged)
            return _normalize_result(result, toolset_id=toolset_id, version=None)
        if item.kind == "builtin":
            return await self.builtin_providers.call(
                item.connection.provider_id,
                raw_name,
                merged,
                credential_id=item.connection.provider_credential_id,
                timeout_seconds=item.connection.timeout_seconds,
                response_limit_bytes=item.connection.response_limit_bytes,
                metadata={
                    **dict(call.metadata),
                    "todo_scope_type": "workflow",
                    "todo_scope_id": f"toolset-test:{toolset_id}",
                },
            )
        return await self.api_executor.execute(item.connection, tool, merged)

    async def autostart(self) -> list[str]:
        warnings: list[str] = []
        for item in self.store.list_toolsets(status="published"):
            if (
                item.kind != "mcp"
                or not item.connection.auto_start
                or item.published_version is None
            ):
                continue
            try:
                await self.ensure_version_session(item.id, item.published_version)
            except Exception as exc:
                warnings.append(f"{item.id}: {str(exc)[:300]}")
        return warnings

    async def close(self) -> None:
        session_ids = set(self._draft_sessions.values()) | set(
            self._version_sessions.values()
        )
        self._draft_sessions.clear()
        self._version_sessions.clear()
        self._version_warnings.clear()
        for session_id in session_ids:
            await self._safe_disconnect(session_id)

    async def ensure_version_session(self, toolset_id: str, version: int) -> str:
        if self.store.get_version(toolset_id, version).kind != "mcp":
            raise ToolsetValidationError("API Toolsets do not use MCP sessions.")
        key = (toolset_id, version)
        existing = self._version_sessions.get(key)
        if existing:
            try:
                snapshot = self.store.get_version(toolset_id, version)
                discovered = {
                    tool.name: tool
                    for tool in await self.mcp_manager.list_tools(existing)
                }
                hard_errors, warnings = _detect_schema_drift(snapshot, discovered)
                if hard_errors:
                    raise ToolsetValidationError("; ".join(hard_errors))
                self._version_warnings[key] = warnings
                return existing
            except Exception:
                self._version_sessions.pop(key, None)
                self._version_warnings.pop(key, None)
                await self._safe_disconnect(existing)
        lock = self._locks.setdefault(f"{toolset_id}@{version}", asyncio.Lock())
        async with lock:
            existing = self._version_sessions.get(key)
            if existing:
                return existing
            snapshot = self.store.get_version(toolset_id, version)
            session_id: str | None = None
            try:
                session_id = await self._connect_profile(snapshot.connection)
                discovered = {
                    tool.name: tool
                    for tool in await self.mcp_manager.list_tools(session_id)
                }
                hard_errors, warnings = _detect_schema_drift(snapshot, discovered)
                if hard_errors:
                    raise ToolsetValidationError("; ".join(hard_errors))
            except Exception:
                if session_id:
                    await self._safe_disconnect(session_id)
                raise
            if session_id is None:
                raise ToolsetValidationError("MCP Toolset session did not start.")
            self._version_warnings[key] = warnings
            self._version_sessions[key] = session_id
            return session_id

    async def call_published_tool(
        self,
        *,
        toolset_id: str,
        version: int,
        exposed_name: str,
        arguments: dict[str, Any],
    ) -> RuntimeToolResult:
        snapshot = self.store.get_version(toolset_id, version)
        tool = next(
            (row for row in snapshot.tools if row.exposed_name == exposed_name),
            None,
        )
        if tool is None:
            raise RuntimeToolError(
                exposed_name,
                "Tool is not enabled in the fixed Toolset version.",
                code="toolset_scope_denied",
            )
        merged = {**deepcopy(tool.default_arguments), **dict(arguments)}
        try:
            _validate_arguments(tool, merged)
            if snapshot.kind == "mcp":
                session_id = await self.ensure_version_session(toolset_id, version)
                result = await self.mcp_manager.call_tool(
                    session_id,
                    tool.raw_name,
                    merged,
                )
                normalized = _normalize_result(
                    result,
                    toolset_id=toolset_id,
                    version=version,
                )
            elif snapshot.kind == "builtin":
                normalized = await self.builtin_providers.call(
                    snapshot.connection.provider_id,
                    tool.raw_name,
                    merged,
                    credential_id=snapshot.connection.provider_credential_id,
                    timeout_seconds=snapshot.connection.timeout_seconds,
                    response_limit_bytes=snapshot.connection.response_limit_bytes,
                )
            else:
                normalized = await self.api_executor.execute(
                    snapshot.connection,
                    tool,
                    merged,
                )
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                exposed_name,
                str(exc)[:500],
                code="toolset_call_failed",
            ) from exc
        normalized.metadata["toolset_id"] = toolset_id
        normalized.metadata["toolset_version"] = version
        warnings = self._version_warnings.get((toolset_id, version), [])
        if warnings:
            normalized.metadata["schema_warnings"] = list(warnings)
        return normalized

    @staticmethod
    def _compile_api_document(
        kind: str,
        document_text: str,
        *,
        base_url: str,
    ) -> CompiledAPISpec:
        if kind == "openapi":
            return compile_openapi(
                parse_openapi_text(document_text),
                base_url_override=base_url,
            )
        if kind == "odata":
            return compile_odata(document_text, base_url=base_url)
        raise ToolsetValidationError(f"Unsupported API Toolset kind: {kind}")

    async def _validate_api_publish(self, item: ToolsetDefinition) -> None:
        connection = item.connection
        await self.api_executor.url_validator(
            connection.api_base_url,
            connection.network_policy,
        )
        auth = connection.api_auth
        credential_ids: list[str] = []
        if auth.auth_type in {"api_key", "bearer"}:
            credential_ids.append(auth.credential_id)
        elif auth.auth_type == "basic":
            credential_ids.extend(
                [auth.username_credential_id, auth.password_credential_id]
            )
        elif auth.auth_type == "oauth2_client_credentials":
            if not auth.token_url:
                raise ToolsetValidationError(
                    "OAuth2 client credentials requires a token URL."
                )
            await self.api_executor.url_validator(
                auth.token_url,
                connection.network_policy,
            )
            credential_ids.extend(
                [
                    auth.client_id_credential_id,
                    auth.client_secret_credential_id,
                ]
            )
        if auth.auth_type == "api_key" and not auth.api_key_name:
            raise ToolsetValidationError("API key authentication requires a key name.")
        for credential_id in credential_ids:
            if not credential_id:
                raise ToolsetValidationError(
                    f"{auth.auth_type} authentication has incomplete credential references."
                )
            credential = self.credentials.get_public(credential_id)
            if credential.status != "active":
                raise ToolsetValidationError(
                    f"Credential is unavailable: {credential_id}."
                )
        unsafe = [
            tool.exposed_name
            for tool in item.tools
            if tool.enabled and not tool.read_only and not tool.requires_approval
        ]
        if unsafe:
            raise ToolsetValidationError(
                "Mutating API operations must require approval: "
                + ", ".join(sorted(unsafe))
            )

    def _validate_builtin_publish(self, item: ToolsetDefinition) -> None:
        provider = self.builtin_providers.get_provider(
            item.connection.provider_id
        )
        if provider.get("credential_required"):
            credential_id = item.connection.provider_credential_id.strip()
            if not credential_id:
                raise ToolsetValidationError(
                    "Builtin Provider requires a credential reference."
                )
            credential = self.credentials.get_public(credential_id)
            if credential.status != "active":
                raise ToolsetValidationError(
                    f"Credential is unavailable: {credential_id}."
                )

    async def _connect_profile(self, profile: Any) -> str:
        profile = self._resolve_installed_project(profile)
        headers: dict[str, str] = {}
        environment: dict[str, str] = {}
        for binding in profile.headers:
            value = self.credentials.resolve(binding.credential_id)
            headers[binding.name] = (
                value
                if binding.name.lower() != "authorization"
                or value.lower().startswith(("bearer ", "basic "))
                else f"Bearer {value}"
            )
        for binding in profile.environment:
            environment[binding.name] = self.credentials.resolve(
                binding.credential_id
            )
        return await self.mcp_manager.connect_profile(
            transport=profile.transport,
            server_command=profile.command,
            url=profile.url,
            headers=headers,
            environment=environment,
            network_policy=profile.network_policy,
            reconnect_attempts=(
                profile.reconnect_attempts if profile.auto_reconnect else 0
            ),
            operation_timeout=profile.timeout_seconds,
            working_directory=profile.working_directory,
        )

    def _resolve_installed_project(
        self,
        profile: MCPConnectionProfile,
    ) -> MCPConnectionProfile:
        resolved = profile.model_copy(deep=True)
        project_id = resolved.installed_project_id.strip()
        if resolved.transport != "stdio" or not project_id:
            return resolved
        if resolved.command:
            return resolved
        if self.installed_project_resolver is None:
            raise ToolsetValidationError(
                "Installed MCP project resolver is unavailable."
            )
        project = self.installed_project_resolver(project_id)
        if not project:
            raise ToolsetValidationError(
                f"Installed MCP project not found: {project_id}"
            )
        command = [
            str(part)
            for part in list(project.get("server_command") or [])
            if isinstance(part, str) and part
        ]
        if not command:
            raise ToolsetValidationError(
                f"Installed MCP project is not runnable: {project_id}"
            )
        resolved.command = command
        return resolved

    async def _safe_disconnect(self, session_id: str) -> None:
        try:
            await self.mcp_manager.disconnect(session_id)
        except Exception:
            pass


class PublishedToolsetProvider:
    # Keep the provider key stable for existing runtime capability mappings.
    provider_name = "published_mcp_toolset"

    def __init__(self, service: ToolsetService) -> None:
        self.service = service
        self._runtime_delegates: dict[str, Any] = {}

    def register_runtime_delegate(self, provider_id: str, provider: Any) -> None:
        self._runtime_delegates[str(provider_id or "").strip().lower()] = provider

    async def list_tools(
        self,
        resources: list[dict[str, Any]] | None = None,
    ) -> list[RuntimeTool]:
        result: list[RuntimeTool] = []
        for resource in resources or []:
            toolset_id = str(resource.get("toolset_id") or "")
            version = int(resource.get("pinned_version") or 0)
            if not toolset_id or version < 1:
                continue
            snapshot = self.service.store.get_version(toolset_id, version)
            prefix = snapshot.connection.tool_prefix
            for tool in snapshot.tools:
                exposed = tool.exposed_name
                if prefix:
                    exposed = f"{prefix}_{exposed}"
                result.append(
                    RuntimeTool(
                        name=exposed,
                        description=tool.exposed_description,
                        input_schema=deepcopy(tool.input_schema),
                        provider=self.provider_name,
                        metadata={
                            "toolset_id": toolset_id,
                            "toolset_version": version,
                            "toolset_raw_name": tool.raw_name,
                            "toolset_exposed_name": tool.exposed_name,
                            "toolset_kind": snapshot.kind,
                            "provider_id": snapshot.connection.provider_id,
                            "requires_approval": tool.requires_approval,
                            "read_only": tool.read_only,
                            "sensitive": tool.sensitive,
                            "terminal": tool.terminal,
                            "memory_mode": tool.memory_mode,
                            "parallel_safe": tool.parallel_safe,
                            "public_app_allowed": tool.public_app_allowed,
                        },
                        read_only=tool.read_only,
                        requires_approval=tool.requires_approval,
                        sensitive=tool.sensitive,
                        terminal=tool.terminal,
                        memory_mode=tool.memory_mode,
                        parallel_safe=tool.parallel_safe,
                        public_app_allowed=tool.public_app_allowed,
                    )
                )
        return result

    async def find_tool(
        self,
        tool_name: str,
        resources: list[dict[str, Any]] | None = None,
    ) -> RuntimeTool | None:
        return next(
            (tool for tool in await self.list_tools(resources) if tool.name == tool_name),
            None,
        )

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        resources = list(call.metadata.get("toolset_resources") or [])
        matched = await self.find_tool(call.tool_name, resources)
        if matched is None:
            raise RuntimeToolError(
                call.tool_name,
                "Tool is outside the bound Toolset scope.",
                code="toolset_scope_denied",
            )
        metadata = matched.metadata
        provider_id = str(metadata.get("provider_id") or "").strip().lower()
        delegate = self._runtime_delegates.get(provider_id)
        if delegate is not None:
            raw_name = str(metadata["toolset_raw_name"])
            delegated_tool = await delegate.find_tool(raw_name)
            if delegated_tool is None:
                raise RuntimeToolError(
                    call.tool_name,
                    "The builtin Runtime Provider no longer exposes this fixed tool.",
                    code="toolset_schema_drift",
                )
            return await delegate.call_tool(
                RuntimeToolCall(
                    tool_name=raw_name,
                    arguments=dict(call.arguments),
                    metadata={
                        **dict(call.metadata),
                        "toolset_id": metadata["toolset_id"],
                        "toolset_version": metadata["toolset_version"],
                        "toolset_exposed_name": call.tool_name,
                    },
                )
            )
        return await self.service.call_published_tool(
            toolset_id=str(metadata["toolset_id"]),
            version=int(metadata["toolset_version"]),
            exposed_name=str(metadata["toolset_exposed_name"]),
            arguments=call.arguments,
        )


PublishedMCPToolsetProvider = PublishedToolsetProvider


class DraftToolsetTestProvider:
    """Scoped provider used only by the trusted Toolset management test action."""

    def __init__(self, service: ToolsetService) -> None:
        self.service = service

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        if not call.metadata.get("toolset_test"):
            raise RuntimeToolError(
                call.tool_name,
                "Draft Toolset test scope is missing.",
                code="toolset_scope_denied",
            )
        session_id = str(call.metadata.get("toolset_session_id") or "")
        raw_name = str(call.metadata.get("toolset_raw_name") or "")
        toolset_id = str(call.metadata.get("toolset_id") or "")
        toolset_kind = str(call.metadata.get("toolset_kind") or "mcp")
        if (
            not raw_name
            or not toolset_id
            or (toolset_kind == "mcp" and not session_id)
        ):
            raise RuntimeToolError(
                call.tool_name,
                "Draft Toolset test metadata is incomplete.",
                code="toolset_scope_denied",
            )
        try:
            if toolset_kind == "builtin":
                item = self.service.store.get_toolset(toolset_id)
                return await self.service.builtin_providers.call(
                    item.connection.provider_id,
                    raw_name,
                    call.arguments,
                    credential_id=item.connection.provider_credential_id,
                    timeout_seconds=item.connection.timeout_seconds,
                    response_limit_bytes=item.connection.response_limit_bytes,
                    metadata={
                        **dict(call.metadata),
                        "todo_scope_type": "workflow",
                        "todo_scope_id": f"toolset-test:{toolset_id}",
                    },
                )
            if toolset_kind != "mcp":
                item = self.service.store.get_toolset(toolset_id)
                tool = next(
                    (
                        candidate
                        for candidate in item.tools
                        if candidate.original_name == raw_name
                    ),
                    None,
                )
                if tool is None:
                    raise ToolsetValidationError(f"Tool not found: {raw_name}")
                return await self.service.api_executor.execute(
                    item.connection,
                    tool,
                    call.arguments,
                )
            result = await self.service.mcp_manager.call_tool(
                session_id,
                raw_name,
                call.arguments,
            )
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc)[:500],
                code="toolset_test_failed",
            ) from exc
        return _normalize_result(result, toolset_id=toolset_id, version=None)


DraftMCPToolTestProvider = DraftToolsetTestProvider


def _normalize_tool(tool: Any) -> ToolDefinition:
    if hasattr(tool, "model_dump"):
        data = tool.model_dump(by_alias=True)
    elif isinstance(tool, dict):
        data = dict(tool)
    else:
        data = vars(tool)
    schema = data.get("inputSchema") or data.get("input_schema") or {}
    return ToolDefinition(
        original_name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        input_schema=dict(schema) if isinstance(schema, dict) else {},
        schema_hash=_schema_hash(schema if isinstance(schema, dict) else {}),
    )


def _validate_arguments(tool: ToolDefinition, arguments: dict[str, Any]) -> None:
    try:
        Draft202012Validator(tool.input_schema or {}).validate(arguments)
    except Exception as exc:
        raise ToolsetValidationError(
            f"Arguments do not match {tool.exposed_name} schema: {str(exc)[:300]}"
        ) from exc


def _detect_schema_drift(
    snapshot: ToolsetVersion,
    discovered: dict[str, Any],
) -> tuple[list[str], list[str]]:
    hard_errors: list[str] = []
    warnings: list[str] = []
    for expected in snapshot.tools:
        current_raw = discovered.get(expected.raw_name)
        if current_raw is None:
            hard_errors.append(f"Published tool disappeared: {expected.raw_name}")
            continue
        current = _normalize_tool(current_raw)
        old_required = set(expected.input_schema.get("required") or [])
        new_required = set(current.input_schema.get("required") or [])
        added_required = sorted(new_required - old_required)
        if added_required:
            hard_errors.append(
                f"{expected.raw_name} added required parameters: "
                f"{', '.join(added_required)}"
            )
        old_properties = expected.input_schema.get("properties") or {}
        new_properties = current.input_schema.get("properties") or {}
        if isinstance(old_properties, dict) and isinstance(new_properties, dict):
            changed_types = sorted(
                name
                for name, old_schema in old_properties.items()
                if name in new_properties
                and isinstance(old_schema, dict)
                and isinstance(new_properties[name], dict)
                and old_schema.get("type")
                and new_properties[name].get("type")
                and old_schema.get("type") != new_properties[name].get("type")
            )
            if changed_types:
                hard_errors.append(
                    f"{expected.raw_name} changed parameter types: "
                    f"{', '.join(changed_types)}"
                )
        if (
            not added_required
            and not any(
                message.startswith(
                    f"{expected.raw_name} changed parameter types:"
                )
                for message in hard_errors
            )
            and current.schema_hash != expected.schema_hash
        ):
            warnings.append(f"{expected.raw_name} schema changed compatibly.")
    return hard_errors, warnings


def _normalize_result(
    result: Any,
    *,
    toolset_id: str,
    version: int | None,
) -> RuntimeToolResult:
    content_items = list(getattr(result, "content", []) or [])
    content: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for item in content_items:
        if hasattr(item, "model_dump"):
            value = dict(item.model_dump())
        elif isinstance(item, dict):
            value = dict(item)
        else:
            value = {"type": "unknown", "raw": str(item)}
        content.append(value)
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            text_parts.append(value["text"])
    output = "\n\n".join(text_parts)
    if len(output) > 65536:
        output = output[:65536] + "\n...[truncated]"
    return RuntimeToolResult(
        output=output,
        content=content[:50],
        metadata={
            "toolset_id": toolset_id,
            "toolset_version": version,
            "output_length": len(output),
        },
        is_error=bool(
            getattr(result, "isError", False)
            or getattr(result, "is_error", False)
        ),
    )
