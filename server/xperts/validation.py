from __future__ import annotations

from .models import XpertDefinition, XpertValidationResult

try:
    from server.workflow_native.schemas import ValidationIssue
    from server.workflow_native.validate import validate_workflow_graph
except ModuleNotFoundError:
    from workflow_native.schemas import ValidationIssue
    from workflow_native.validate import validate_workflow_graph


def _node_kind(node) -> str:
    value = node.data.get("kind") if isinstance(node.data, dict) else None
    return str(value or node.type or "")


def validate_xpert_definition(xpert: XpertDefinition) -> XpertValidationResult:
    base = validate_workflow_graph(xpert.draft.workflow)
    history_variable = xpert.draft.history_variable
    history_reference = f"variable '{history_variable}'"
    issues = [
        issue
        for issue in base.issues
        if not (
            issue.code.endswith("template_variable")
            and history_reference in issue.message
        )
    ]
    nodes = xpert.draft.workflow.nodes
    input_nodes = [node for node in nodes if _node_kind(node) == "input"]
    output_nodes = [node for node in nodes if _node_kind(node) == "output"]
    agent_nodes = [node for node in nodes if _node_kind(node) == "workflow_agent"]

    if len(input_nodes) != 1:
        issues.append(
            ValidationIssue(
                code="xpert_input_contract",
                message="Published Xpert requires exactly one input node.",
            )
        )
    elif str(input_nodes[0].data.get("variableName") or "") != xpert.draft.input_variable:
        issues.append(
            ValidationIssue(
                code="xpert_input_variable_mismatch",
                message="Xpert input variable must match the input node variableName.",
                node_id=input_nodes[0].id,
            )
        )

    if len(output_nodes) != 1:
        issues.append(
            ValidationIssue(
                code="xpert_output_contract",
                message="Published Xpert requires exactly one output node.",
            )
        )
    elif str(output_nodes[0].data.get("outputVariable") or "") != xpert.draft.output_variable:
        issues.append(
            ValidationIssue(
                code="xpert_output_variable_mismatch",
                message="Xpert output variable must match the output node outputVariable.",
                node_id=output_nodes[0].id,
            )
        )

    if not agent_nodes:
        issues.append(
            ValidationIssue(
                code="xpert_workflow_agent_required",
                message="Published Xpert requires at least one workflow_agent node.",
            )
        )
    for node in agent_nodes:
        for field_name in ("modelId", "rolePrompt", "taskInput", "outputVariable"):
            if not str(node.data.get(field_name) or "").strip():
                issues.append(
                    ValidationIssue(
                        code=f"xpert_workflow_agent_missing_{field_name}",
                        message=f"workflow_agent requires {field_name} before publish.",
                        node_id=node.id,
                    )
                )

    return XpertValidationResult(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        order=base.order,
        node_count=base.node_count,
        edge_count=base.edge_count,
    )
