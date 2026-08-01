from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.toolsets.credentials import CredentialStore
from server.toolsets.service import (
    PublishedMCPToolsetProvider,
    ToolsetService,
)
from server.toolsets.store import ToolsetStore, ToolsetValidationError
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError


@dataclass
class FakeTool:
    name: str
    description: str
    inputSchema: dict[str, Any]


class FakeMCPManager:
    def __init__(self) -> None:
        self.tools = [
            FakeTool(
                name="search",
                description="Search sources.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            )
        ]
        self.connect_calls: list[dict[str, Any]] = []
        self.tool_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.disconnected: list[str] = []
        self._counter = 0

    async def connect_profile(self, **kwargs: Any) -> str:
        self.connect_calls.append(dict(kwargs))
        self._counter += 1
        return f"session-{self._counter}"

    async def list_tools(self, session_id: str) -> list[FakeTool]:
        return list(self.tools)

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        self.tool_calls.append((session_id, tool_name, dict(arguments)))
        return SimpleNamespace(
            content=[{"type": "text", "text": f"result:{arguments['query']}"}],
            isError=False,
        )

    async def disconnect(self, session_id: str) -> None:
        self.disconnected.append(session_id)


def _service(tmp_path: Path) -> tuple[ToolsetService, FakeMCPManager]:
    manager = FakeMCPManager()
    storage_dir = tmp_path / "toolsets"
    service = ToolsetService(
        ToolsetStore(storage_dir),
        CredentialStore(storage_dir),
        manager,  # type: ignore[arg-type]
    )
    return service, manager


@pytest.mark.asyncio
async def test_mcp_toolset_connect_publish_and_fixed_version_call(
    tmp_path: Path,
) -> None:
    service, manager = _service(tmp_path)
    credential, _ = service.credentials.create(
        name="Authorization",
        kind="header",
        value="secret-token",
    )
    created = service.store.create_toolset(
        name="Research",
        connection={
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "headers": [
                {
                    "name": "Authorization",
                    "credential_id": credential.credential_id,
                }
            ],
            "tool_prefix": "research",
            "auto_reconnect": False,
        },
    )
    connected = await service.connect(created.id)
    assert connected.runtime_status == "connected"
    assert manager.connect_calls[0]["headers"] == {
        "Authorization": "Bearer secret-token"
    }
    assert manager.connect_calls[0]["reconnect_attempts"] == 0

    enabled = service.store.update_tool(
        created.id,
        "search",
        revision=connected.revision,
        patch={
            "enabled": True,
            "alias": "find",
            "default_arguments": {"limit": 3},
        },
    )
    version = await service.publish(
        created.id,
        expected_revision=enabled.revision,
    )
    provider = PublishedMCPToolsetProvider(service)
    resources = [
        {"toolset_id": created.id, "pinned_version": version.version}
    ]
    tools = await provider.list_tools(resources)
    assert [tool.name for tool in tools] == ["research_find"]

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="research_find",
            arguments={"query": "alpha"},
            metadata={"toolset_resources": resources},
        )
    )
    assert result.output == "result:alpha"
    assert manager.tool_calls[-1][1:] == (
        "search",
        {"limit": 3, "query": "alpha"},
    )


@pytest.mark.asyncio
async def test_fixed_version_detects_compatible_and_breaking_schema_drift(
    tmp_path: Path,
) -> None:
    service, manager = _service(tmp_path)
    created = service.store.create_toolset(
        name="Schema drift",
        connection={"transport": "stdio", "command": ["python", "server.py"]},
    )
    connected = await service.connect(created.id)
    enabled = service.store.update_tool(
        created.id,
        "search",
        revision=connected.revision,
        patch={"enabled": True},
    )
    version = await service.publish(
        created.id,
        expected_revision=enabled.revision,
    )

    manager.tools[0].inputSchema["properties"]["language"] = {"type": "string"}
    result = await service.call_published_tool(
        toolset_id=created.id,
        version=version.version,
        exposed_name="search",
        arguments={"query": "beta"},
    )
    assert result.metadata["schema_warnings"]

    manager.tools[0].inputSchema["required"].append("language")
    with pytest.raises(RuntimeToolError) as error:
        await service.call_published_tool(
            toolset_id=created.id,
            version=version.version,
            exposed_name="search",
            arguments={"query": "gamma"},
        )
    assert error.value.code == "toolset_call_failed"


@pytest.mark.asyncio
async def test_installed_project_is_resolved_and_frozen_in_version(
    tmp_path: Path,
) -> None:
    projects = {
        "local-search": {
            "project_id": "local-search",
            "server_command": ["python", "-m", "local_search_mcp"],
        }
    }
    manager = FakeMCPManager()
    storage_dir = tmp_path / "toolsets"
    service = ToolsetService(
        ToolsetStore(storage_dir),
        CredentialStore(storage_dir),
        manager,  # type: ignore[arg-type]
        installed_project_resolver=projects.get,
    )
    created = service.store.create_toolset(
        name="Installed project",
        connection={
            "transport": "stdio",
            "installed_project_id": "local-search",
        },
    )
    connected = await service.connect(created.id)
    assert manager.connect_calls[0]["server_command"] == [
        "python",
        "-m",
        "local_search_mcp",
    ]
    enabled = service.store.update_tool(
        created.id,
        "search",
        revision=connected.revision,
        patch={"enabled": True},
    )
    version = await service.publish(
        created.id,
        expected_revision=enabled.revision,
    )
    assert version.connection.command == [
        "python",
        "-m",
        "local_search_mcp",
    ]

    projects["local-search"]["server_command"] = ["python", "-m", "changed"]
    service._draft_sessions.clear()
    await service.ensure_version_session(created.id, version.version)
    assert manager.connect_calls[-1]["server_command"] == [
        "python",
        "-m",
        "local_search_mcp",
    ]
