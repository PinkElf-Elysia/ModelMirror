from __future__ import annotations

import server.main as main_module
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy
from server.xperts.models import XpertVersion


def _workflow(tool_mode: str = "mcp_tools") -> NativeWorkflowDefinition:
    return NativeWorkflowDefinition.model_validate(
        {
            "id": "toolset-workflow",
            "title": "Toolset workflow",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {
                        "kind": "input",
                        "variableName": "user_input",
                    },
                },
                {
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "Manager",
                        "modelId": "test-model",
                        "rolePrompt": "Use the available tools.",
                        "taskInput": "{{user_input}}",
                        "outputVariable": "agent_output",
                        "toolMode": tool_mode,
                        "maxIterations": "5",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {
                        "kind": "output",
                        "outputVariable": "agent_output",
                    },
                },
                {
                    "id": "toolset",
                    "type": "toolset_resource",
                    "data": {
                        "kind": "toolset_resource",
                        "toolsetId": "toolset-research",
                        "versionPolicy": "pinned",
                        "pinnedVersion": "2",
                    },
                },
            ],
            "edges": [
                {"id": "input-agent", "source": "input", "target": "agent"},
                {"id": "agent-output", "source": "agent", "target": "output"},
                {
                    "id": "bind-toolset",
                    "source": "toolset",
                    "target": "agent",
                    "sourceHandle": "toolset-binding",
                    "targetHandle": "toolset",
                },
            ],
        }
    )


def test_toolset_binding_is_not_part_of_control_flow() -> None:
    workflow = _workflow()
    validation = validate_workflow_graph(workflow)
    order = main_module.workflow_topological_order(
        list(workflow.nodes),
        list(workflow.edges),
    )

    assert validation.valid is True
    assert "toolset" not in validation.order
    assert "toolset" not in order
    assert order == ["input", "agent", "output"]


def test_toolset_binding_requires_runtime_tool_mode() -> None:
    validation = validate_workflow_graph(_workflow("none"))
    assert "resource_binding_requires_runtime_tool_mode" in {
        issue.code for issue in validation.issues
    }


def test_toolset_resource_cannot_mix_control_and_binding_edges() -> None:
    payload = _workflow().model_dump(mode="json")
    payload["edges"].append(
        {
            "id": "bad-control-edge",
            "source": "toolset",
            "target": "output",
        }
    )
    validation = validate_workflow_graph(
        NativeWorkflowDefinition.model_validate(payload)
    )
    codes = {issue.code for issue in validation.issues}
    assert "mixed_resource_binding_and_control_flow" in codes
    assert "resource_node_in_control_flow" in codes


def test_xpert_app_preflight_requires_explicit_tool_access_for_bound_toolsets() -> None:
    preflight = _deployment_preflight(
        XpertVersion(
            version=1,
            draft_revision=1,
            workflow=_workflow(),
            input_variable="user_input",
            history_variable="conversation_history",
            output_variable="agent_output",
            checksum="test-checksum",
            published_at=1.0,
        ),
        XpertAppPolicy(),
    )
    assert "app_tools_not_allowed" in {
        str(item.get("code")) for item in preflight["issues"]
    }
