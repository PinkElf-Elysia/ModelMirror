from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

import server.main as main_module
from server.toolsets import (
    CredentialStore,
    SafeAPIExecutor,
    ToolsetService,
    ToolsetStore,
    configure_toolsets,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xperts.api import _prepare_published_resource_snapshot
from server.xperts.models import XpertDefinition, XpertDraft


class APIFakeMCPManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.counter = 0

    async def connect_profile(self, **kwargs: Any) -> str:
        self.counter += 1
        return f"api-session-{self.counter}"

    async def list_tools(self, session_id: str) -> list[Any]:
        return [
            SimpleNamespace(
                name="echo",
                description="Echo a message.",
                inputSchema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
            )
        ]

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        self.calls.append((session_id, tool_name, dict(arguments)))
        return SimpleNamespace(
            content=[{"type": "text", "text": arguments["message"]}],
            isError=False,
        )

    async def disconnect(self, session_id: str) -> None:
        return None


def _xpert_with_api_toolset(
    toolset_id: str,
    *,
    with_hitl: bool,
) -> XpertDefinition:
    nodes: list[dict[str, Any]] = [
        {
            "id": "input",
            "type": "input",
            "data": {"kind": "input", "variableName": "user_input"},
        },
        {
            "id": "agent",
            "type": "workflow_agent",
            "data": {
                "kind": "workflow_agent",
                "agentName": "API Agent",
                "modelId": "test-model",
                "rolePrompt": "Use the bound API.",
                "taskInput": "{{user_input}}",
                "outputVariable": "agent_output",
                "toolMode": "mcp_tools",
                "maxIterations": "5",
            },
        },
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "agent_output"},
        },
        {
            "id": "toolset",
            "type": "toolset_resource",
            "data": {
                "kind": "toolset_resource",
                "toolsetId": toolset_id,
                "versionPolicy": "current_published",
                "pinnedVersion": "",
            },
        },
    ]
    edges: list[dict[str, Any]] = [
        {"id": "input-agent", "source": "input", "target": "agent"},
        {"id": "agent-output", "source": "agent", "target": "output"},
        {
            "id": "toolset-agent",
            "source": "toolset",
            "target": "agent",
            "sourceHandle": "toolset-binding",
            "targetHandle": "toolset",
        },
    ]
    if with_hitl:
        nodes.append(
            {
                "id": "hitl",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "human_in_the_loop",
                    "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                    "middlewarePriority": "40",
                    "runtimeMiddlewareConfig": {
                        "interrupt_on_tools": "create_item",
                        "final_confirmation": False,
                        "timeout_seconds": 3600,
                    },
                },
            }
        )
        edges.append(
            {
                "id": "hitl-agent",
                "source": "hitl",
                "target": "agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "api-toolset-xpert",
            "title": "API Toolset Xpert",
            "nodes": nodes,
            "edges": edges,
        }
    )
    return XpertDefinition(
        id="xpert-api-toolset",
        slug="api-toolset",
        name="API Toolset",
        status="draft",
        draft=XpertDraft(workflow=workflow),
        created_at=1.0,
        updated_at=1.0,
    )


@pytest.mark.asyncio
async def test_toolset_api_create_discover_configure_test_and_publish(
    tmp_path: Path,
) -> None:
    original = main_module.toolset_service
    manager = APIFakeMCPManager()
    storage = tmp_path / "toolsets"
    service = ToolsetService(
        ToolsetStore(storage),
        CredentialStore(storage),
        manager,  # type: ignore[arg-type]
    )
    configure_toolsets(service)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app),
            base_url="http://test",
        ) as client:
            credential_response = await client.post(
                "/api/runtime/credentials",
                json={
                    "name": "MCP Authorization",
                    "kind": "header",
                    "value": "one-time-secret",
                },
            )
            assert credential_response.status_code == 201
            credential = credential_response.json()
            assert credential["secret_value"] == "one-time-secret"
            assert credential["secret_value_visible_once"] is True

            listed_credentials = await client.get("/api/runtime/credentials")
            assert listed_credentials.status_code == 200
            credential_text = listed_credentials.text
            assert "one-time-secret" not in credential_text
            assert "ciphertext" not in credential_text

            created_response = await client.post(
                "/api/toolsets",
                json={
                    "name": "API test Toolset",
                    "connection": {
                        "transport": "stdio",
                        "command": ["python", "-m", "echo_mcp"],
                    },
                },
            )
            assert created_response.status_code == 201
            created = created_response.json()

            resources_response = await client.get("/api/toolsets/resources")
            assert resources_response.status_code == 200
            resources = resources_response.json()
            assert resources["version"] == "modelmirror-toolset-resources-v2"
            assert resources["summary"]["toolset_count"] == 1
            assert resources["toolsets"][0]["id"] == created["id"]
            assert "connection" not in resources["toolsets"][0]

            connected_response = await client.post(
                f"/api/toolsets/{created['id']}/connect",
            )
            assert connected_response.status_code == 200
            connected = connected_response.json()
            assert connected["runtime_status"] == "connected"
            assert connected["tools"][0]["enabled"] is False

            configured_response = await client.patch(
                f"/api/toolsets/{created['id']}/tools/echo",
                json={
                    "revision": connected["revision"],
                    "patch": {
                        "enabled": True,
                        "alias": "say",
                        "default_arguments": {"message": "default"},
                    },
                },
            )
            assert configured_response.status_code == 200
            configured = configured_response.json()

            tested_response = await client.post(
                f"/api/toolsets/{created['id']}/tools/echo/test",
                json={"arguments": {"message": "hello"}},
            )
            assert tested_response.status_code == 200
            assert tested_response.json()["output"] == "hello"

            published_response = await client.post(
                f"/api/toolsets/{created['id']}/publish",
                json={
                    "revision": configured["revision"],
                    "release_notes": "First stable version.",
                },
            )
            assert published_response.status_code == 200
            published = published_response.json()
            assert published["version"] == 1
            assert [tool["alias"] for tool in published["tools"]] == ["say"]

            versions_response = await client.get(
                f"/api/toolsets/{created['id']}/versions/1"
            )
            assert versions_response.status_code == 200
            assert versions_response.json()["schema_hash"] == published["schema_hash"]

        persisted = (storage / "credentials.json").read_text(encoding="utf-8")
        assert "one-time-secret" not in persisted
        assert json.loads(persisted)["credentials"][0]["ciphertext"]
    finally:
        configure_toolsets(original)


@pytest.mark.asyncio
async def test_api_toolset_import_test_and_publish_contract(
    tmp_path: Path,
) -> None:
    original = main_module.toolset_service
    calls: list[Request] = []

    def handler(request: Request) -> Response:
        calls.append(request)
        return Response(200, json={"ok": True})

    async def allow_url(url: str, network_policy: str) -> None:
        assert url.startswith("https://")

    manager = APIFakeMCPManager()
    storage = tmp_path / "api-toolsets"
    credentials = CredentialStore(storage)
    service = ToolsetService(
        ToolsetStore(storage),
        credentials,
        manager,  # type: ignore[arg-type]
        api_executor=SafeAPIExecutor(
            credentials,
            transport=MockTransport(handler),
            url_validator=allow_url,
        ),
    )
    configure_toolsets(service)
    document = {
        "openapi": "3.1.0",
        "servers": [{"url": "https://catalog.example.test"}],
        "paths": {
            "/items": {
                "post": {
                    "operationId": "create_item",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                }
            }
        },
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app),
            base_url="http://test",
        ) as client:
            capabilities = await client.get("/api/toolsets/api-capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json()["formats"]["openapi"] == ["3.0", "3.1"]

            created_response = await client.post(
                "/api/toolsets",
                json={"kind": "openapi", "name": "Catalog API"},
            )
            assert created_response.status_code == 201
            created = created_response.json()

            imported_response = await client.post(
                f"/api/toolsets/{created['id']}/import",
                json={
                    "source_type": "text",
                    "document": json.dumps(document),
                },
            )
            assert imported_response.status_code == 200
            imported = imported_response.json()
            assert imported["runtime_status"] == "ready"
            assert imported["tools"][0]["requires_approval"] is True

            enabled_response = await client.patch(
                f"/api/toolsets/{created['id']}/tools/create_item",
                json={
                    "revision": imported["revision"],
                    "patch": {"enabled": True},
                },
            )
            assert enabled_response.status_code == 200
            enabled = enabled_response.json()

            blocked_test = await client.post(
                f"/api/toolsets/{created['id']}/tools/create_item/test",
                json={"arguments": {"body": {"name": "alpha"}}},
            )
            assert blocked_test.status_code == 400
            assert calls == []

            confirmed_test = await client.post(
                f"/api/toolsets/{created['id']}/tools/create_item/test",
                json={
                    "arguments": {"body": {"name": "alpha"}},
                    "confirm_mutating": True,
                },
            )
            assert confirmed_test.status_code == 200
            assert len(calls) == 1

            published_response = await client.post(
                f"/api/toolsets/{created['id']}/publish",
                json={"revision": enabled["revision"]},
            )
            assert published_response.status_code == 200
            assert published_response.json()["kind"] == "openapi"

            _, missing_hitl = _prepare_published_resource_snapshot(
                _xpert_with_api_toolset(created["id"], with_hitl=False)
            )
            assert "xpert_toolset_mutating_hitl_required" in {
                issue.code for issue in missing_hitl
            }

            pinned, covered = _prepare_published_resource_snapshot(
                _xpert_with_api_toolset(created["id"], with_hitl=True)
            )
            assert "xpert_toolset_mutating_hitl_required" not in {
                issue.code for issue in covered
            }
            toolset_node = next(
                node
                for node in pinned.nodes
                if (node.data or {}).get("kind") == "toolset_resource"
            )
            assert toolset_node.data["versionPolicy"] == "pinned"
            assert toolset_node.data["pinnedVersion"] == 1
    finally:
        configure_toolsets(original)
