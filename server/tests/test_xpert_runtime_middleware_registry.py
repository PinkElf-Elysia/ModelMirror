from __future__ import annotations

from server.xpert_runtime import (
    RuntimeMiddlewareNode,
    RuntimeMiddlewareRegistry,
    register_builtin_middleware_nodes,
    skill_creator_authoring_mode,
)


def _registry() -> RuntimeMiddlewareRegistry:
    registry = RuntimeMiddlewareRegistry()
    register_builtin_middleware_nodes(registry)
    return registry


def test_registry_list_returns_builtin_nodes() -> None:
    registry = _registry()
    nodes = registry.list()

    node_ids = {node.id for node in nodes}
    assert node_ids == {
        "system_prompt_injector",
        "event_recorder",
        "tool_policy",
        "tool_audit",
        "mcp_tools",
        "context_compression",
        "xpert_file_memory",
        "structured_output",
        "todo_planner",
        "llm_tool_selector",
        "human_in_the_loop",
        "sandbox_files",
        "sandbox_shell",
        "skills_runtime",
        "browser_automation",
        "client_tools",
        "office_automation",
        "scheduler",
        "ralph_loop",
        "knowledge_writer",
        "plugin_hooks",
        "xpert_authoring",
        "skill_creator",
        "datax_indicators",
    }
    for node in nodes:
        assert node.id
        assert node.kind.startswith("runtime_middleware.")
        assert node.title
        assert node.category
        assert isinstance(node.fields, list)
        assert isinstance(node.metadata, dict)
        assert node.config_version >= 1
        assert node.execution_status == "real"
        assert node.app_policy in {"allowed", "conditional", "forbidden"}

    authoring = registry.get("xpert_authoring")
    skill_creator = registry.get("skill_creator")
    assert authoring is not None and skill_creator is not None
    assert authoring.requires_tool_mode == "mcp_tools"
    assert skill_creator.requires_tool_mode is None
    authoring_mode = next(
        field for field in skill_creator.fields if field.name == "authoring_mode"
    )
    assert authoring_mode.default == "creator_handoff"
    assert authoring_mode.options == ["creator_handoff", "legacy_proposal"]
    assert skill_creator.metadata["supported_authoring_modes"] == [
        "legacy_proposal",
        "creator_handoff",
    ]
    plugin_hooks = registry.get("plugin_hooks")
    assert plugin_hooks is not None
    hook_mode = next(field for field in plugin_hooks.fields if field.name == "hook_mode")
    assert hook_mode.default == "typed_v2"
    assert hook_mode.options == ["typed_v2", "legacy_argv"]
    assert {"xpert_authoring", "skill_creator"}.issubset(
        registry.app_forbidden_ids()
    )


def test_skill_creator_authoring_mode_defaults_legacy_for_saved_nodes() -> None:
    assert skill_creator_authoring_mode(None) == "legacy_proposal"
    assert skill_creator_authoring_mode({}) == "legacy_proposal"
    assert (
        skill_creator_authoring_mode({"authoring_mode": "creator_handoff"})
        == "creator_handoff"
    )


def test_registry_get_by_id() -> None:
    registry = _registry()
    first = registry.list()[0]

    assert registry.get(first.id) == first
    assert registry.get("nonexistent") is None


def test_registry_categories_returns_unique() -> None:
    registry = _registry()
    categories = registry.categories()

    assert len(categories) == len(set(categories))
    assert "agent" in categories
    assert "tool" in categories


def test_registry_by_category_groups_correctly() -> None:
    registry = _registry()
    grouped = registry.by_category()

    assert isinstance(grouped, dict)
    assert all(isinstance(category, str) for category in grouped)
    assert all(
        isinstance(node, RuntimeMiddlewareNode)
        for nodes in grouped.values()
        for node in nodes
    )
    assert sum(len(nodes) for nodes in grouped.values()) == len(registry.list())
