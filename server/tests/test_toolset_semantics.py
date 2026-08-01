from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from server.toolsets.credentials import CredentialStore
from server.toolsets import api as toolset_api
from server.toolsets.providers import BuiltinToolProviderRegistry
from server.toolsets.service import PublishedToolsetProvider, ToolsetService
from server.toolsets.store import ToolsetStore
from server.xpert_runtime.todo_store import RuntimeTodoStore
from server.xpert_runtime.todo_toolset import TodoToolsetProvider
from server.xpert_runtime.toolset import RuntimeToolCall
from server.xperts.context import XpertContextStore
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy
from server.xperts.models import XpertVersion
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph


class UnusedMCPManager:
    async def disconnect(self, session_id: str) -> None:
        return None


def _service(
    storage: Path,
    *,
    providers: BuiltinToolProviderRegistry | None = None,
) -> ToolsetService:
    credentials = CredentialStore(storage)
    return ToolsetService(
        ToolsetStore(storage),
        credentials,
        UnusedMCPManager(),  # type: ignore[arg-type]
        builtin_providers=providers,
    )


@pytest.mark.asyncio
async def test_builtin_providers_are_stable_default_toolsets(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "toolsets")

    warnings = await service.ensure_builtin_toolsets()
    assert warnings == []
    first = service.store.list_toolsets(limit=20)
    todos = next(
        item for item in first if item.connection.provider_id == "todos"
    )
    tavily = next(
        item for item in first if item.connection.provider_id == "tavily"
    )
    assert todos.status == "published"
    assert todos.published_version == 1
    assert all(tool.enabled for tool in todos.tools)
    assert tavily.status == "draft"
    assert tavily.tools == []

    await service.ensure_builtin_toolsets()
    second = service.store.list_toolsets(limit=20)
    assert len(second) == len(first) == 2
    assert {item.id for item in second} == {item.id for item in first}

    catalog = await service.builtin_provider_catalog()
    todo_provider = next(item for item in catalog if item["id"] == "todos")
    tavily_provider = next(item for item in catalog if item["id"] == "tavily")
    assert todo_provider["default_toolset_id"] == todos.id
    assert todo_provider["configuration_status"] == "ready"
    assert tavily_provider["default_toolset_id"] == tavily.id
    assert tavily_provider["configuration_status"] == "credential_required"


@pytest.mark.asyncio
async def test_builtin_tavily_provider_uses_encrypted_credential_and_safe_limits(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"results": [{"title": "Result", "url": "https://example.test"}]},
        )

    storage = tmp_path / "toolsets"
    credentials = CredentialStore(storage)
    registry = BuiltinToolProviderRegistry(
        credentials,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    service = ToolsetService(
        ToolsetStore(storage),
        credentials,
        UnusedMCPManager(),  # type: ignore[arg-type]
        builtin_providers=registry,
    )
    credential, _ = credentials.create(
        name="Tavily",
        kind="provider_key",
        value="tavily-secret",
    )
    created = service.store.create_toolset(
        kind="builtin",
        name="Web research",
        connection={
            "provider_id": "tavily",
            "provider_credential_id": credential.credential_id,
        },
    )
    connected = await service.connect(created.id)
    enabled = service.store.update_tool(
        created.id,
        "tavily_search",
        revision=connected.revision,
        patch={
            "enabled": True,
            "memory_mode": "run",
            "parallel_safe": True,
            "public_app_allowed": True,
        },
    )
    version = await service.publish(created.id, expected_revision=enabled.revision)
    provider = PublishedToolsetProvider(service)
    resources = [{"toolset_id": created.id, "pinned_version": version.version}]

    tools = await provider.list_tools(resources)
    assert len(tools) == 1
    assert tools[0].memory_mode == "run"
    assert tools[0].parallel_safe is True
    assert tools[0].public_app_allowed is True
    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="tavily_search",
            arguments={"query": "ModelMirror"},
            metadata={"toolset_resources": resources},
        )
    )

    assert "Result" in result.output
    assert requests[-1].headers["Authorization"] == "Bearer tavily-secret"
    persisted = (storage / "toolsets.json").read_text("utf-8")
    assert "tavily-secret" not in persisted

    latest = service.store.get_toolset(created.id)
    changed = service.store.update_tool(
        created.id,
        "tavily_search",
        revision=latest.revision,
        patch={"terminal": True, "public_app_allowed": False},
    )
    assert changed.tools[0].terminal is True
    fixed = service.store.get_version(created.id, version.version)
    assert fixed.tools[0].terminal is False
    assert fixed.tools[0].public_app_allowed is True

    await registry._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_todo_builtin_toolset_delegates_to_existing_runtime_store(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "toolsets")
    created = service.store.create_toolset(
        kind="builtin",
        name="Todos",
        connection={"provider_id": "todos"},
    )
    connected = await service.connect(created.id)
    enabled = service.store.update_tool(
        created.id,
        "todo_create",
        revision=connected.revision,
        patch={"enabled": True},
    )
    version = await service.publish(created.id, expected_revision=enabled.revision)
    todo_store = RuntimeTodoStore(tmp_path / "todos")
    provider = PublishedToolsetProvider(service)
    provider.register_runtime_delegate("todos", TodoToolsetProvider(todo_store))
    resources = [{"toolset_id": created.id, "pinned_version": version.version}]

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="todo_create",
            arguments={"title": "Verify semantic Toolset"},
            metadata={
                "toolset_resources": resources,
                "todo_scope_type": "conversation",
                "todo_scope_id": "xpert:conversation",
                "run_id": "run-1",
            },
        )
    )

    assert json.loads(result.output)["title"] == "Verify semantic Toolset"
    items = todo_store.list_items(
        scope_type="conversation",
        scope_id="xpert:conversation",
    )
    assert [item.title for item in items] == ["Verify semantic Toolset"]


@pytest.mark.asyncio
async def test_todo_builtin_toolset_management_test_uses_scoped_runtime_store(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "toolsets")
    todo_store = RuntimeTodoStore(tmp_path / "todos")
    service.builtin_providers.register_runtime_delegate(
        "todos",
        TodoToolsetProvider(todo_store),
    )
    created = service.store.create_toolset(
        kind="builtin",
        name="Todo management test",
        connection={"provider_id": "todos"},
    )
    connected = await service.connect(created.id)
    enabled = service.store.update_tool(
        created.id,
        "todo_create",
        revision=connected.revision,
        patch={"enabled": True},
    )

    result = await service.test_tool(
        created.id,
        "todo_create",
        {"title": "Management test"},
        confirm_mutating=True,
    )

    assert json.loads(result.output)["title"] == "Management test"
    items = todo_store.list_items(
        scope_type="workflow",
        scope_id=f"toolset-test:{created.id}",
    )
    assert [item.title for item in items] == ["Management test"]
    assert enabled.tools[1].enabled is True


def test_conversation_tool_memory_is_redacted_bounded_and_persistent(
    tmp_path: Path,
) -> None:
    store = XpertContextStore(tmp_path / "context")
    conversation = store.create_conversation("xpert-memory")
    memory = store.create_tool_memory(
        "xpert-memory",
        conversation.conversation_id,
        tool_name="tavily_search",
        provider="tavily",
        summary="result " * 2000,
        arguments={
            "query": "public topic",
            "api_key": "must-not-persist",
            "nested": {"token": "must-not-persist"},
        },
        source_run_id="run-1",
    )

    assert len(memory.summary) <= 8192
    assert memory.parameter_summary["api_key"] == "[redacted]"
    assert memory.parameter_summary["nested"]["token"] == "[redacted]"
    persisted = (
        tmp_path / "context" / "xpert_context" / "context.json"
    ).read_text("utf-8")
    assert "must-not-persist" not in persisted

    restored = XpertContextStore(tmp_path / "context")
    items = restored.list_tool_memories(
        "xpert-memory",
        conversation.conversation_id,
    )
    assert [item.memory_id for item in items] == [memory.memory_id]
    restored.archive_tool_memory(
        "xpert-memory",
        conversation.conversation_id,
        memory.memory_id,
    )
    assert (
        restored.list_tool_memories(
            "xpert-memory",
            conversation.conversation_id,
        )
        == []
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maxToolConcurrency", "9"),
        ("maxToolCalls", "0"),
        ("maxToolDepth", "5"),
    ],
)
def test_workflow_agent_rejects_invalid_tool_budget_ranges(
    field: str,
    value: str,
) -> None:
    agent_data = {
        "kind": "workflow_agent",
        "agentName": "Analyst",
        "modelId": "test-model",
        "rolePrompt": "Use tools safely.",
        "taskInput": "{{user_input}}",
        "outputVariable": "agent_output",
        "toolMode": "mcp_tools",
        "maxIterations": "5",
        field: value,
    }
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "tool-budget-validation",
            "title": "Tool budget validation",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": agent_data,
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "agent_output"},
                },
            ],
            "edges": [
                {"id": "input-agent", "source": "input", "target": "agent"},
                {"id": "agent-output", "source": "agent", "target": "output"},
            ],
        }
    )

    validation = validate_workflow_graph(workflow)
    assert validation.valid is False
    assert any(field in issue.message for issue in validation.issues)


@pytest.mark.asyncio
async def test_xpert_app_allows_only_explicit_public_read_only_toolset_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path / "toolsets")
    monkeypatch.setattr(toolset_api, "_service", service)
    created = service.store.create_toolset(
        kind="builtin",
        name="Public todos",
        connection={"provider_id": "todos"},
    )
    connected = await service.connect(created.id)
    safe_draft = service.store.update_tool(
        created.id,
        "todo_list",
        revision=connected.revision,
        patch={
            "enabled": True,
            "public_app_allowed": True,
            "memory_mode": "run",
        },
    )
    safe_version = await service.publish(
        created.id,
        expected_revision=safe_draft.revision,
    )

    def xpert_version(toolset_version: int) -> XpertVersion:
        workflow = NativeWorkflowDefinition.model_validate(
            {
                "id": "public-toolset",
                "title": "Public toolset",
                "nodes": [
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
                            "agentName": "Public agent",
                            "modelId": "test-model",
                            "rolePrompt": "Use safe tools.",
                            "taskInput": "{{user_input}}",
                            "outputVariable": "agent_output",
                            "toolMode": "mcp_tools",
                            "maxIterations": "5",
                        },
                    },
                    {
                        "id": "policy",
                        "type": "runtime_middleware",
                        "data": {
                            "kind": "runtime_middleware",
                            "runtimeMiddlewareId": "tool_policy",
                            "runtimeMiddlewareKind": "runtime_middleware.tool_policy",
                            "runtimeMiddlewareConfig": {"allow_by_default": True},
                        },
                    },
                    {
                        "id": "toolset",
                        "type": "toolset_resource",
                        "data": {
                            "kind": "toolset_resource",
                            "toolsetId": created.id,
                            "versionPolicy": "pinned",
                            "pinnedVersion": str(toolset_version),
                        },
                    },
                    {
                        "id": "output",
                        "type": "output",
                        "data": {"kind": "output", "outputVariable": "agent_output"},
                    },
                ],
                "edges": [
                    {"id": "input-agent", "source": "input", "target": "agent"},
                    {"id": "agent-output", "source": "agent", "target": "output"},
                    {
                        "id": "policy-agent",
                        "source": "policy",
                        "target": "agent",
                        "targetHandle": "middleware",
                    },
                    {
                        "id": "toolset-agent",
                        "source": "toolset",
                        "target": "agent",
                        "targetHandle": "toolset",
                    },
                ],
            }
        )
        return XpertVersion(
            version=1,
            draft_revision=1,
            workflow=workflow,
            input_variable="user_input",
            history_variable="conversation_history",
            output_variable="agent_output",
            checksum="checksum",
            published_at=1.0,
        )

    allowed = _deployment_preflight(
        xpert_version(safe_version.version),
        XpertAppPolicy(allow_tools=True),
    )
    assert allowed["valid"] is True, allowed

    latest = service.store.get_toolset(created.id)
    unsafe_draft = service.store.update_tool(
        created.id,
        "todo_create",
        revision=latest.revision,
        patch={"enabled": True},
    )
    unsafe_version = await service.publish(
        created.id,
        expected_revision=unsafe_draft.revision,
    )
    denied = _deployment_preflight(
        xpert_version(unsafe_version.version),
        XpertAppPolicy(allow_tools=True),
    )
    assert "app_toolset_tools_unsafe" in {
        item["code"] for item in denied["issues"]
    }
