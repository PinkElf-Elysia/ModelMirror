from __future__ import annotations

from copy import deepcopy

import pytest
from mcp.types import Tool

from server.registry.tool_registry import ToolRegistry
from server.workflow_native.r20_nodes import (
    WorkflowR20NodeError,
    build_mcp_result,
    execute_variable_assign_v2,
    mcp_arguments_digest,
    mcp_schema_checksum,
    resolve_mcp_tool_arguments,
    validate_human_intervention_v2_config,
    validate_mcp_tool_v2_config,
)


def _variable_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "contractVersion": 2,
        "outputVariable": "result",
        "valueSource": "literal",
        "literalValue": None,
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize(
    "value",
    [
        "text",
        42,
        1.25,
        True,
        None,
        {"nested": {"items": [1, None, "two"]}},
        [1, {"ok": True}, None],
    ],
)
def test_variable_assign_v2_preserves_json_types_and_detaches_values(value: object) -> None:
    original = deepcopy(value)
    output_name, assigned = execute_variable_assign_v2(
        _variable_config(literalValue=value),
        {},
        render_template=lambda template, _variables: template,
    )

    assert output_name == "result"
    assert assigned == original
    if isinstance(value, (dict, list)):
        assert assigned is not value


def test_variable_assign_v2_variable_copy_does_not_share_nested_references() -> None:
    source = {"nested": [{"value": 1}]}
    _, assigned = execute_variable_assign_v2(
        _variable_config(valueSource="variable", sourceVariable="source"),
        {"source": source},
        render_template=lambda template, _variables: template,
    )

    assert assigned == source
    assert assigned is not source
    assert isinstance(assigned, dict)
    assert assigned["nested"] is not source["nested"]


def test_variable_assign_v2_fails_before_rendering_missing_template_variable() -> None:
    rendered = False

    def render(_template: str, _variables: dict[str, object]) -> str:
        nonlocal rendered
        rendered = True
        return "unexpected"

    with pytest.raises(
        WorkflowR20NodeError,
        match="VARIABLE_ASSIGN_TEMPLATE_VARIABLE_UNAVAILABLE",
    ):
        execute_variable_assign_v2(
            _variable_config(valueSource="template", template="Hello {{missing}}"),
            {},
            render_template=render,
        )

    assert rendered is False


def test_variable_assign_v2_preserves_legacy_empty_template_semantics() -> None:
    output_name, assigned = execute_variable_assign_v2(
        _variable_config(valueSource="template", template=""),
        {},
        render_template=lambda template, _variables: template,
    )

    assert output_name == "result"
    assert assigned == ""


def test_variable_assign_v2_rejects_unsupported_template_reference() -> None:
    with pytest.raises(
        WorkflowR20NodeError,
        match="VARIABLE_ASSIGN_TEMPLATE_INVALID",
    ):
        execute_variable_assign_v2(
            _variable_config(
                valueSource="template",
                template="Hello {{customer.name}}",
            ),
            {"customer": {"name": "Ada"}},
            render_template=lambda template, _variables: template,
        )


@pytest.mark.parametrize("mode", ["input", "approval"])
def test_human_intervention_v2_accepts_both_modes(mode: str) -> None:
    validate_human_intervention_v2_config(
        {
            "contractVersion": 2,
            "interactionMode": mode,
            "prompt": "Please decide",
            "outputVariable": "decision",
            "timeoutSeconds": 3600,
        }
    )


def test_human_intervention_v2_rejects_fractional_timeout() -> None:
    with pytest.raises(WorkflowR20NodeError, match="HUMAN_INTERVENTION_TIMEOUT_INVALID"):
        validate_human_intervention_v2_config(
            {
                "contractVersion": 2,
                "interactionMode": "approval",
                "prompt": "Please decide",
                "outputVariable": "decision",
                "timeoutSeconds": 30.5,
            }
        )


def _mcp_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["query", "limit"],
        "additionalProperties": False,
    }


def _mcp_config(schema: dict[str, object], **overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "contractVersion": 2,
        "serverId": "server_alpha",
        "toolName": "search",
        "inputSchemaChecksum": mcp_schema_checksum(schema),
        "argumentMode": "fields",
        "argumentBindings": [
            {
                "id": "binding_query",
                "name": "query",
                "binding": {"source": "variable", "variable": "query_input"},
            },
            {
                "id": "binding_limit",
                "name": "limit",
                "binding": {"source": "literal", "value": 3},
            },
        ],
        "outputVariable": "tool_result",
    }
    config.update(overrides)
    return config


def test_mcp_tool_v2_resolves_typed_fields_against_pinned_schema() -> None:
    schema = _mcp_schema()
    config = _mcp_config(schema)

    arguments = resolve_mcp_tool_arguments(
        config,
        {"query_input": "phoenix weather"},
        input_schema=schema,
    )

    assert arguments == {"query": "phoenix weather", "limit": 3}
    assert len(mcp_arguments_digest(arguments)) == 64


def test_mcp_tool_v2_rejects_schema_drift_and_type_mismatch() -> None:
    schema = _mcp_schema()
    config = _mcp_config(schema)
    drifted = deepcopy(schema)
    assert isinstance(drifted["properties"], dict)
    drifted["properties"]["limit"] = {"type": "string"}

    with pytest.raises(WorkflowR20NodeError, match="MCP_TOOL_SCHEMA_DRIFT"):
        validate_mcp_tool_v2_config(config, input_schema=drifted)

    with pytest.raises(WorkflowR20NodeError, match="MCP_TOOL_ARGUMENTS_SCHEMA_MISMATCH"):
        resolve_mcp_tool_arguments(
            config,
            {"query_input": 123},
            input_schema=schema,
        )


def test_mcp_tool_v2_builds_typed_result_with_completed_assets_only() -> None:
    result = build_mcp_result(
        server_id="server_alpha",
        tool_name="search",
        text="done",
        content_types=["text", "resource", "text"],
        file_outputs=[
            {"status": "completed", "asset_id": "asset_1"},
            {"status": "failed", "asset_id": "asset_2"},
            {"status": "completed", "asset_id": "asset_1"},
        ],
    )

    assert result == {
        "status": "completed",
        "serverId": "server_alpha",
        "toolName": "search",
        "text": "done",
        "contentTypes": ["resource", "text"],
        "fileAssetIds": ["asset_1"],
    }


@pytest.mark.asyncio
async def test_tool_registry_preserves_same_named_tools_from_different_servers() -> None:
    registry = ToolRegistry()
    schema = {"type": "object", "properties": {}}
    tool = Tool(name="search", description="Search", inputSchema=schema)

    await registry.register_session_tools(
        session_id="session_alpha",
        server_id="server_alpha",
        tools=[tool],
    )
    await registry.register_session_tools(
        session_id="session_beta",
        server_id="server_beta",
        tools=[tool],
    )

    listed = await registry.list_tools()
    assert [(item["server_id"], item["name"]) for item in listed] == [
        ("server_alpha", "search"),
        ("server_beta", "search"),
    ]
    assert all(item["schema_checksum"] == mcp_schema_checksum(schema) for item in listed)
    assert (await registry.find_tool(server_id="server_beta", name="search"))["session_id"] == "session_beta"


@pytest.mark.asyncio
async def test_tool_registry_reconnect_atomically_replaces_same_server_tool(
) -> None:
    registry = ToolRegistry()
    old_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    new_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }

    await registry.register_session_tools(
        session_id="session_old",
        server_id="server_alpha",
        tools=[Tool(name="search", description="old", inputSchema=old_schema)],
    )
    await registry.register_session_tools(
        session_id="session_new",
        server_id="server_alpha",
        tools=[Tool(name="search", description="new", inputSchema=new_schema)],
    )

    assert registry.snapshot_tools() == await registry.list_tools()
    assert registry.snapshot_tools()[0]["session_id"] == "session_new"
    assert registry.snapshot_tools()[0]["schema_checksum"] == mcp_schema_checksum(
        new_schema
    )

    await registry.unregister_session("session_new")
    assert registry.snapshot_tools()[0]["session_id"] == "session_old"
    await registry.clear()
    assert registry.snapshot_tools() == []


@pytest.mark.asyncio
async def test_tool_registry_reconnect_order_survives_wall_clock_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps = iter([200.0, 100.0])
    monkeypatch.setattr(
        "server.registry.tool_registry.time.time",
        lambda: next(timestamps),
    )
    registry = ToolRegistry()
    schema = {"type": "object", "properties": {}}

    await registry.register_session_tools(
        session_id="session_old",
        server_id="server_alpha",
        tools=[Tool(name="search", description="old", inputSchema=schema)],
    )
    await registry.register_session_tools(
        session_id="session_new",
        server_id="server_alpha",
        tools=[Tool(name="search", description="new", inputSchema=schema)],
    )

    current = await registry.find_tool(server_id="server_alpha", name="search")
    assert current is not None
    assert current["session_id"] == "session_new"
