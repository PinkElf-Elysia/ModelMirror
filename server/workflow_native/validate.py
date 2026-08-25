from __future__ import annotations

import re
import json
from collections import defaultdict, deque
from datetime import datetime
from string import Formatter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .control_data import (
    LIST_OPERATORS,
    WorkflowControlDataError,
    deduplicate_array,
    execute_list_operation,
    filter_array,
    sort_array,
    validate_aggregate_config,
    validate_comparison_rule,
    validate_dataset_compare_config,
    validate_terminate_error_config,
)
from .node_contracts import workflow_node_contract_registry
from .file_data import (
    WorkflowFileDataError,
    is_time_v2,
    object_transform_variable_references,
    time_v2_variable_references,
    validate_file_output_config,
    validate_object_transform_config,
    validate_time_v2_config,
)
from .secure_http import (
    WorkflowHttpRequestError,
    http_request_variable_references,
    is_http_request_v2,
    validate_http_request_v2_config,
)
from .typed_ai import (
    WorkflowTypedAIError,
    contract_version as typed_ai_contract_version,
    validate_parameter_extractor_v2_config,
    validate_question_classifier_v2_config,
)
from .r20_nodes import (
    WorkflowR20NodeError,
    contract_version as r20_contract_version,
    validate_code_v2_config,
    validate_human_intervention_v2_config,
    validate_mcp_tool_v2_config,
    validate_variable_aggregator_v2_config,
    validate_variable_assign_v2_config,
    variable_aggregator_v2_references,
)
from .r21_nodes import WorkflowR21Error, validate_data_merge_config
from .schemas import (
    NativeWorkflowDefinition,
    NativeWorkflowEdge,
    NativeWorkflowNode,
    ValidationIssue,
    ValidateWorkflowResponse,
)


NODE_KIND_ALIASES = {
    "start": "input",
    "input": "input",
    "user-input": "input",
    "scheduled_start": "scheduled_start",
    "scheduled-start": "scheduled_start",
    "http_event_entry": "http_event_entry",
    "http-event-entry": "http_event_entry",
    "failure_event_entry": "failure_event_entry",
    "failure-event-entry": "failure_event_entry",
    "workflow_call_entry": "workflow_call_entry",
    "workflow-call-entry": "workflow_call_entry",
    "invoke_workflow": "invoke_workflow",
    "invoke-workflow": "invoke_workflow",
    "llm": "llm",
    "if-else": "condition",
    "condition": "condition",
    "code": "code",
    "variable_assign": "variable_assign",
    "variable-assign": "variable_assign",
    "variable-assigner": "variable_assign",
    "template_transform": "template_transform",
    "template-transform": "template_transform",
    "variable_aggregator": "variable_aggregator",
    "variable-aggregator": "variable_aggregator",
    "parameter_extractor": "parameter_extractor",
    "parameter-extractor": "parameter_extractor",
    "knowledge_retrieval": "knowledge_retrieval",
    "knowledge-retrieval": "knowledge_retrieval",
    "knowledge_citation": "knowledge_citation",
    "knowledge-citation": "knowledge_citation",
    "document_extractor": "document_extractor",
    "document-extractor": "document_extractor",
    "vision_understanding": "vision_understanding",
    "vision-understanding": "vision_understanding",
    "human_intervention": "human_intervention",
    "human-intervention": "human_intervention",
    "human-in-the-loop": "human_intervention",
    "question_classifier": "question_classifier",
    "question-classifier": "question_classifier",
    "agent": "agent",
    "workflow_agent": "workflow_agent",
    "workflow-agent": "workflow_agent",
    "external_xpert": "external_xpert",
    "external-xpert": "external_xpert",
    "knowledge_base": "knowledge_base",
    "knowledge-base": "knowledge_base",
    "toolset_resource": "toolset_resource",
    "toolset-resource": "toolset_resource",
    "plugin_resource": "plugin_resource",
    "plugin-resource": "plugin_resource",
    "agent_task": "agent_task",
    "agent-task": "agent_task",
    "agent_handoff": "agent_handoff",
    "agent-handoff": "agent_handoff",
    "handoff_router": "handoff_router",
    "handoff-router": "handoff_router",
    "mcp_tool": "mcp_tool",
    "mcp-tool": "mcp_tool",
    "tool": "mcp_tool",
    "time_tool": "time_tool",
    "time-tool": "time_tool",
    "time": "time_tool",
    "http_request": "http_request",
    "http-request": "http_request",
    "terminate_error": "terminate_error",
    "terminate-error": "terminate_error",
    "multi_route": "multi_route",
    "multi-route": "multi_route",
    "list_operation": "list_operation",
    "list-operation": "list_operation",
    "list-operator": "list_operation",
    "data_aggregate": "data_aggregate",
    "data-aggregate": "data_aggregate",
    "data_merge": "data_merge",
    "data-merge": "data_merge",
    "dataset_compare": "dataset_compare",
    "dataset-compare": "dataset_compare",
    "object_transform": "object_transform",
    "object-transform": "object_transform",
    "file_output": "file_output",
    "file-output": "file_output",
    "iteration": "iteration",
    "json_serialize": "json_serialize",
    "json-serialize": "json_serialize",
    "json_deserialize": "json_deserialize",
    "json-deserialize": "json_deserialize",
    "data_table_query": "data_table_query",
    "data-table-query": "data_table_query",
    "data_table_insert": "data_table_insert",
    "data-table-insert": "data_table_insert",
    "data_table_update": "data_table_update",
    "data-table-update": "data_table_update",
    "data_table_delete": "data_table_delete",
    "data-table-delete": "data_table_delete",
    "annotation": "annotation",
    "note": "annotation",
    "runtime_middleware": "runtime_middleware",
    "runtime-middleware": "runtime_middleware",
    "suspend_wait": "suspend_wait",
    "suspend-wait": "suspend_wait",
    "http_event_reply": "http_event_reply",
    "http-event-reply": "http_event_reply",
    "end": "output",
    "answer": "output",
    "output": "output",
}

TEMPLATE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SUPPORTED_NODE_KINDS = workflow_node_contract_registry.kinds()


def node_kind(node: NativeWorkflowNode) -> str:
    """Return a normalized native node kind when possible."""

    data_kind = node.data.get("kind")
    raw_kind = data_kind if isinstance(data_kind, str) else node.type
    if not isinstance(raw_kind, str):
        return ""
    return NODE_KIND_ALIASES.get(raw_kind.strip().lower(), raw_kind.strip().lower())


def config_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


DATA_TABLE_NODE_KINDS = {
    "data_table_query",
    "data_table_insert",
    "data_table_update",
    "data_table_delete",
}
DATA_TABLE_FILTER_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "contains",
    "is_null",
}


def validate_data_table_filter(
    value: object,
    *,
    node_id: str,
    required: bool,
    depth: int = 0,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if value is None or value == {}:
        if required:
            issues.append(
                ValidationIssue(
                    code="missing_data_table_filter",
                    message="Agent Table update and delete nodes require a non-empty filter.",
                    node_id=node_id,
                )
            )
        return issues
    if not isinstance(value, dict) or depth > 4:
        return [
            ValidationIssue(
                code="invalid_data_table_filter",
                message="Agent Table filter must be an object with at most 5 levels.",
                node_id=node_id,
            )
        ]
    if "logic" in value or "items" in value:
        logic = str(value.get("logic") or "").lower()
        items = value.get("items")
        if logic not in {"and", "or"} or not isinstance(items, list) or not 1 <= len(items) <= 20:
            return [
                ValidationIssue(
                    code="invalid_data_table_filter_group",
                    message="Filter groups need logic=and|or and 1 to 20 items.",
                    node_id=node_id,
                )
            ]
        for item in items:
            issues.extend(
                validate_data_table_filter(
                    item,
                    node_id=node_id,
                    required=True,
                    depth=depth + 1,
                )
            )
        return issues
    field = str(value.get("field") or "").strip()
    operator = str(value.get("operator") or "").strip().lower()
    if not field:
        issues.append(
            ValidationIssue(
                code="missing_data_table_filter_field",
                message="Agent Table filter leaves require a field.",
                node_id=node_id,
            )
        )
    if operator not in DATA_TABLE_FILTER_OPERATORS:
        issues.append(
            ValidationIssue(
                code="invalid_data_table_filter_operator",
                message="Agent Table filter operator is not supported.",
                node_id=node_id,
            )
        )
    elif operator != "is_null" and "value" not in value:
        issues.append(
            ValidationIssue(
                code="missing_data_table_filter_value",
                message=f"Agent Table filter operator {operator} requires a value binding.",
                node_id=node_id,
            )
        )
    elif operator != "is_null":
        issues.extend(
            validate_data_table_binding(
                value.get("value"),
                node_id=node_id,
                label=f"Agent Table filter '{field or '<empty>'}'",
            )
        )
    return issues


def validate_data_table_binding(
    value: object,
    *,
    node_id: str,
    label: str,
) -> list[ValidationIssue]:
    if not isinstance(value, dict):
        return [
            ValidationIssue(
                code="invalid_data_table_value_binding",
                message=f"{label} must use a literal or variable value binding.",
                node_id=node_id,
            )
        ]
    source = str(value.get("source") or "").strip()
    if source == "literal" and "value" in value:
        return []
    if source == "variable" and is_variable_name(str(value.get("variable") or "").strip()):
        return []
    return [
        ValidationIssue(
            code="invalid_data_table_value_binding",
            message=f"{label} must use source=literal with value or source=variable with an identifier.",
            node_id=node_id,
        )
    ]


def iter_data_table_bindings(data: dict[str, Any]) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    value_bindings = data.get("valueBindings")
    if isinstance(value_bindings, dict):
        bindings.extend(
            value for value in value_bindings.values() if isinstance(value, dict)
        )

    def collect_filter(value: object) -> None:
        if not isinstance(value, dict):
            return
        items = value.get("items")
        if isinstance(items, list):
            for item in items:
                collect_filter(item)
            return
        binding = value.get("value")
        if isinstance(binding, dict):
            bindings.append(binding)

    collect_filter(data.get("filter"))
    return bindings


def validate_handoff_execution_configuration(
    node: NativeWorkflowNode,
    *,
    code_prefix: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    data = node.data
    execution_mode = str(data.get("executionMode") or "manual").strip()
    if execution_mode not in {"manual", "xpert_auto"}:
        issues.append(
            ValidationIssue(
                code=f"invalid_{code_prefix}_execution_mode",
                message="Handoff executionMode must be manual or xpert_auto.",
                node_id=node.id,
            )
        )
    target_agent = str(data.get("targetAgent") or "").strip()
    if execution_mode == "xpert_auto" and not target_agent.startswith("xpert:"):
        issues.append(
            ValidationIssue(
                code=f"invalid_{code_prefix}_xpert_target",
                message="Automatic Handoff targetAgent must use xpert:<slug-or-id>.",
                node_id=node.id,
            )
        )
    wait_for_completion = config_truthy(data.get("waitForCompletion"))
    if wait_for_completion and execution_mode != "xpert_auto":
        issues.append(
            ValidationIssue(
                code=f"invalid_{code_prefix}_wait_mode",
                message="waitForCompletion requires executionMode=xpert_auto.",
                node_id=node.id,
            )
        )
    result_variable = str(data.get("resultVariable") or "").strip()
    if wait_for_completion:
        if not result_variable:
            issues.append(
                ValidationIssue(
                    code=f"missing_{code_prefix}_result_variable",
                    message="Waiting Handoff needs data.resultVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(result_variable):
            issues.append(
                ValidationIssue(
                    code=f"invalid_{code_prefix}_result_variable",
                    message="Handoff resultVariable must be an identifier.",
                    node_id=node.id,
                )
            )
    raw_timeout = data.get("waitTimeoutSeconds", 120)
    try:
        wait_timeout = int(raw_timeout)
    except (TypeError, ValueError):
        wait_timeout = 0
    if wait_timeout < 5 or wait_timeout > 600:
        issues.append(
            ValidationIssue(
                code=f"invalid_{code_prefix}_wait_timeout",
                message="Handoff waitTimeoutSeconds must be between 5 and 600.",
                node_id=node.id,
            )
        )
    return issues


def validate_workflow_graph(workflow: NativeWorkflowDefinition) -> ValidateWorkflowResponse:
    """Validate a workflow graph without executing any node."""

    issues: list[ValidationIssue] = []
    node_ids: set[str] = set()
    duplicate_ids: set[str] = set()

    for node in workflow.nodes:
        if node.id in node_ids:
            duplicate_ids.add(node.id)
        node_ids.add(node.id)

    for node_id in sorted(duplicate_ids):
        issues.append(
            ValidationIssue(
                code="duplicate_node_id",
                message=f"Node id '{node_id}' is duplicated.",
                node_id=node_id,
            )
        )

    edge_ids: set[str] = set()
    duplicate_edge_ids: set[str] = set()
    for edge in workflow.edges:
        if edge.id in edge_ids:
            duplicate_edge_ids.add(edge.id)
        edge_ids.add(edge.id)
    for edge_id in sorted(duplicate_edge_ids):
        issues.append(
            ValidationIssue(
                code="duplicate_edge_id",
                message=f"Edge id '{edge_id}' is duplicated.",
                edge_id=edge_id,
            )
        )

    kinds_by_id = {node.id: node_kind(node) for node in workflow.nodes}
    for node in workflow.nodes:
        kind = kinds_by_id[node.id]
        if workflow_node_contract_registry.get(kind) is None:
            issues.append(
                ValidationIssue(
                    code="unknown_node_kind",
                    message=f"Node '{node.id}' has an unsupported kind.",
                    node_id=node.id,
                )
            )

    if not any(
        kind in {"input", "scheduled_start", "http_event_entry", "failure_event_entry", "workflow_call_entry"}
        for kind in kinds_by_id.values()
    ):
        issues.append(
            ValidationIssue(
                code="missing_input_node",
                message="Workflow needs at least one input or deployment start node.",
            )
        )

    if not any(
        kind in {"output", "http_event_reply", "terminate_error"}
        for kind in kinds_by_id.values()
    ):
        issues.append(
            ValidationIssue(
                code="missing_output_node",
                message="Workflow needs at least one output or HTTP reply end node.",
            )
        )

    for node in workflow.nodes:
        issues.extend(validate_node_configuration(node, kinds_by_id[node.id]))

    node_variable_producers = collect_node_variable_producers(
        workflow.nodes,
        kinds_by_id,
    )
    declaration_ids = [item.id for item in workflow.variables]
    declaration_names = [item.name for item in workflow.variables]
    if len(declaration_ids) != len(set(declaration_ids)):
        issues.append(
            ValidationIssue(
                code="duplicate_workflow_variable_declaration_id",
                message="Workflow variable declarations must use unique IDs.",
            )
        )
    if len(declaration_names) != len(set(declaration_names)):
        issues.append(
            ValidationIssue(
                code="duplicate_workflow_variable_declaration",
                message="Workflow variable declarations must use unique names.",
            )
        )
    for name in sorted(set(declaration_names).intersection(node_variable_producers)):
        issues.append(
            ValidationIssue(
                code="workflow_variable_producer_conflict",
                message=f"Workflow variable '{name}' conflicts with a node output.",
            )
        )
    for name, producer_ids in sorted(node_variable_producers.items()):
        if len(producer_ids) < 2:
            continue
        issues.append(
            ValidationIssue(
                code="duplicate_variable_producer",
                message=(
                    f"Variable '{name}' has multiple node producers: "
                    + ", ".join(producer_ids)
                ),
                severity="warning",
            )
        )

    available_variables = collect_declared_variables(workflow.nodes, kinds_by_id)
    available_variables.update(declaration_names)
    for node in workflow.nodes:
        issues.extend(
            validate_variable_references(node, kinds_by_id[node.id], available_variables)
        )

    valid_edges = validate_edges(
        workflow.edges,
        node_ids,
        issues,
        nodes_by_id={node.id: node for node in workflow.nodes},
        kinds_by_id=kinds_by_id,
    )
    validate_invoke_workflow_upstream_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
        declaration_names=set(declaration_names),
        producers=node_variable_producers,
    )
    validate_http_event_reply_structure(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_control_data_structure(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_data_merge_structure(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
        declaration_names=set(declaration_names),
        producers=node_variable_producers,
    )
    validate_sandbox_middleware_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_skill_runtime_middleware_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_browser_middleware_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_client_tool_middleware_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_office_middleware_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    validate_automation_middleware_bindings(
        workflow.nodes,
        valid_edges,
        issues,
        kinds_by_id=kinds_by_id,
    )
    order = topological_order(workflow.nodes, valid_edges, issues)

    has_errors = any(issue.severity == "error" for issue in issues)
    return ValidateWorkflowResponse(
        valid=not has_errors,
        issues=issues,
        order=order if not has_errors else [],
        node_count=len(workflow.nodes),
        edge_count=len(workflow.edges),
    )


def validate_invoke_workflow_upstream_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
    declaration_names: set[str],
    producers: dict[str, list[str]],
) -> None:
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        parents[edge.target].add(edge.source)
    for node in nodes:
        if kinds_by_id.get(node.id) != "invoke_workflow":
            continue
        ancestors: set[str] = set()
        stack = list(parents.get(node.id, set()))
        while stack:
            current = stack.pop()
            if current in ancestors:
                continue
            ancestors.add(current)
            stack.extend(parents.get(current, set()))
        bindings = node.data.get("inputBindings")
        if not isinstance(bindings, dict):
            continue
        for binding in bindings.values():
            if not isinstance(binding, dict) or binding.get("source") != "variable":
                continue
            variable = str(binding.get("variable") or "").strip()
            if not variable or variable in declaration_names:
                continue
            producer_ids = set(producers.get(variable, []))
            if producer_ids and producer_ids.isdisjoint(ancestors):
                issues.append(
                    ValidationIssue(
                        code="invoke_workflow_binding_not_upstream",
                        message=(
                            f"Workflow call variable '{variable}' must be declared globally "
                            "or produced by an upstream node."
                        ),
                        node_id=node.id,
                    )
                )


def validate_http_event_reply_structure(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    reply_ids = {
        node.id for node in nodes if kinds_by_id.get(node.id) == "http_event_reply"
    }
    if not reply_ids:
        return
    has_http_entry = any(kind == "http_event_entry" for kind in kinds_by_id.values())
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        parents[edge.target].add(edge.source)
        if edge.source in reply_ids:
            issues.append(
                ValidationIssue(
                    code="http_event_reply_not_terminal",
                    message="HTTP event reply must be a terminal node.",
                    node_id=edge.source,
                )
            )
    for reply_id in sorted(reply_ids):
        if not has_http_entry:
            issues.append(
                ValidationIssue(
                    code="http_event_reply_without_entry",
                    message="HTTP event reply requires an HTTP event entry.",
                    node_id=reply_id,
                )
            )
        stack = list(parents.get(reply_id, set()))
        visited: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            if kinds_by_id.get(current) == "suspend_wait":
                issues.append(
                    ValidationIssue(
                        code="http_event_reply_after_suspend_wait",
                        message="HTTP event reply cannot have a suspend wait upstream.",
                        node_id=reply_id,
                    )
                )
                break
            stack.extend(parents.get(current, set()))


def validate_control_data_structure(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    outgoing: dict[str, list[NativeWorkflowEdge]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.source].append(edge)

    for node in nodes:
        kind = kinds_by_id.get(node.id)
        node_edges = outgoing.get(node.id, [])
        if kind == "terminate_error" and node_edges:
            issues.append(
                ValidationIssue(
                    code="terminate_error_not_terminal",
                    message="Terminate error must be a terminal node without outgoing edges.",
                    node_id=node.id,
                )
            )
        if kind == "question_classifier" and typed_ai_contract_version(node.data) == 2:
            categories = node.data.get("categoriesV2")
            category_ids = (
                [
                    str(category.get("id") or "").strip()
                    for category in categories
                    if isinstance(category, dict)
                ]
                if isinstance(categories, list)
                else []
            )
            expected_handles = {*category_ids, "default"}
            counts: dict[str, int] = defaultdict(int)
            for edge in node_edges:
                handle = str(edge.sourceHandle or "").strip()
                counts[handle] += 1
                if handle not in expected_handles:
                    issues.append(
                        ValidationIssue(
                            code="question_classifier_unknown_edge_handle",
                            message="Question classifier edge uses an unconfigured category handle.",
                            node_id=node.id,
                            edge_id=edge.id,
                        )
                    )
            for handle in sorted(expected_handles):
                if counts.get(handle, 0) == 0:
                    issues.append(
                        ValidationIssue(
                            code="question_classifier_missing_edge",
                            message=f"Question classifier handle '{handle}' needs exactly one outgoing edge.",
                            node_id=node.id,
                        )
                    )
                elif counts[handle] > 1:
                    issues.append(
                        ValidationIssue(
                            code="question_classifier_duplicate_edge",
                            message=f"Question classifier handle '{handle}' has more than one outgoing edge.",
                            node_id=node.id,
                        )
                    )
            continue
        if kind != "multi_route":
            continue
        routes = node.data.get("routes")
        route_ids = (
            [str(route.get("id") or "").strip() for route in routes if isinstance(route, dict)]
            if isinstance(routes, list)
            else []
        )
        expected_handles = {*route_ids, "default"}
        counts: dict[str, int] = defaultdict(int)
        for edge in node_edges:
            handle = str(edge.sourceHandle or "").strip()
            counts[handle] += 1
            if handle not in expected_handles:
                issues.append(
                    ValidationIssue(
                        code="multi_route_unknown_edge_handle",
                        message="Multi route edge uses an unconfigured route handle.",
                        node_id=node.id,
                        edge_id=edge.id,
                    )
                )
        for handle in sorted(expected_handles):
            if counts.get(handle, 0) == 0:
                issues.append(
                    ValidationIssue(
                        code="multi_route_missing_edge",
                        message=f"Multi route handle '{handle}' needs exactly one outgoing edge.",
                        node_id=node.id,
                    )
                )
            elif counts[handle] > 1:
                issues.append(
                    ValidationIssue(
                        code="multi_route_duplicate_edge",
                        message=f"Multi route handle '{handle}' has more than one outgoing edge.",
                        node_id=node.id,
                    )
                )


def validate_data_merge_structure(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
    declaration_names: set[str],
    producers: dict[str, list[str]],
) -> None:
    control_edges = [edge for edge in edges if not is_non_control_binding_edge(edge)]
    parents: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, list[NativeWorkflowEdge]] = defaultdict(list)
    for edge in control_edges:
        parents[edge.target].add(edge.source)
        incoming[edge.target].append(edge)

    def ancestors_including(node_id: str) -> set[str]:
        ancestors = {node_id}
        stack = list(parents.get(node_id, set()))
        while stack:
            current = stack.pop()
            if current in ancestors:
                continue
            ancestors.add(current)
            stack.extend(parents.get(current, set()))
        return ancestors

    for node in nodes:
        if kinds_by_id.get(node.id) != "data_merge":
            continue
        by_handle: dict[str, list[NativeWorkflowEdge]] = defaultdict(list)
        for edge in incoming.get(node.id, []):
            by_handle[str(edge.targetHandle or "").strip()].append(edge)
        if set(by_handle) != {"left", "right"} or any(
            len(by_handle[handle]) != 1 for handle in ("left", "right")
        ):
            issues.append(
                ValidationIssue(
                    code="data_merge_input_edges_invalid",
                    message=(
                        "Data merge requires exactly one control edge for each "
                        "left and right target handle, with no extra input edges."
                    ),
                    node_id=node.id,
                )
            )
            continue

        for handle, variable_field in (
            ("left", "leftVariable"),
            ("right", "rightVariable"),
        ):
            variable = str(node.data.get(variable_field) or "").strip()
            if not variable or variable in declaration_names:
                continue
            producer_ids = producers.get(variable, [])
            if len(producer_ids) != 1:
                issues.append(
                    ValidationIssue(
                        code="data_merge_variable_producer_invalid",
                        message=(
                            f"Data merge {handle} variable '{variable}' must have "
                            "exactly one producer or be declared globally."
                        ),
                        node_id=node.id,
                    )
                )
                continue
            source_id = by_handle[handle][0].source
            if producer_ids[0] not in ancestors_including(source_id):
                issues.append(
                    ValidationIssue(
                        code="data_merge_variable_not_on_input_path",
                        message=(
                            f"Data merge {handle} variable '{variable}' must be produced "
                            f"on the {handle} input path."
                        ),
                        node_id=node.id,
                    )
                )


def validate_node_configuration(
    node: NativeWorkflowNode,
    kind: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    data = node.data

    if kind == "input":
        variable_name = str(data.get("variableName") or "").strip()
        if not variable_name:
            issues.append(
                ValidationIssue(
                    code="missing_input_variable",
                    message="Input node needs data.variableName.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(variable_name):
            issues.append(
                ValidationIssue(
                    code="invalid_variable_name",
                    message="Input variable name must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind in {"scheduled_start", "http_event_entry", "failure_event_entry", "workflow_call_entry"}:
        event_variable = str(data.get("eventVariable") or "").strip()
        if not is_variable_name(event_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_trigger_event_variable",
                    message="Trigger eventVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "scheduled_start":
        schedule_type = str(data.get("scheduleType") or "").strip()
        timezone_name = str(data.get("timezone") or "").strip()
        if schedule_type not in {"once", "interval", "cron"}:
            issues.append(
                ValidationIssue(
                    code="invalid_schedule_type",
                    message="Scheduled start type must be once, interval, or cron.",
                    node_id=node.id,
                )
            )
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            issues.append(
                ValidationIssue(
                    code="invalid_schedule_timezone",
                    message="Scheduled start needs a valid IANA timezone.",
                    node_id=node.id,
                )
            )
        if schedule_type == "once":
            once_at = str(data.get("onceAt") or "").strip()
            try:
                datetime.fromisoformat(once_at.replace("Z", "+00:00"))
            except ValueError:
                issues.append(
                    ValidationIssue(
                        code="invalid_schedule_once_at",
                        message="onceAt must be an ISO date and time.",
                        node_id=node.id,
                    )
                )
        if schedule_type == "interval":
            try:
                interval_seconds = int(data.get("intervalSeconds") or 0)
            except (TypeError, ValueError):
                interval_seconds = 0
            if not 30 <= interval_seconds <= 31_536_000:
                issues.append(
                    ValidationIssue(
                        code="invalid_schedule_interval",
                        message="intervalSeconds must be between 30 and 31536000.",
                        node_id=node.id,
                    )
                )
        if schedule_type == "cron":
            cron_expression = str(data.get("cronExpression") or "").strip()
            if len(cron_expression.split()) != 5:
                issues.append(
                    ValidationIssue(
                        code="invalid_schedule_cron",
                        message="cronExpression must contain five fields.",
                        node_id=node.id,
                    )
                )

    if kind == "failure_event_entry":
        source_project_ids = data.get("sourceProjectIds")
        if (
            not isinstance(source_project_ids, list)
            or not 1 <= len(source_project_ids) <= 50
            or len(source_project_ids) != len(set(source_project_ids))
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"wf_[a-f0-9]{32}", item)
                for item in source_project_ids
            )
        ):
            issues.append(
                ValidationIssue(
                    code="invalid_failure_source_projects",
                    message="Failure entry needs 1 to 50 unique workflow project IDs.",
                    node_id=node.id,
                )
            )

    if kind == "invoke_workflow":
        target_project_id = str(data.get("targetProjectId") or "").strip()
        if not re.fullmatch(r"wf_[a-f0-9]{32}", target_project_id):
            issues.append(
                ValidationIssue(
                    code="invalid_invoke_workflow_target_project",
                    message="Workflow call needs a fixed workflow project ID.",
                    node_id=node.id,
                )
            )
        try:
            target_version = int(data.get("targetVersion") or 0)
        except (TypeError, ValueError):
            target_version = 0
        if target_version < 1:
            issues.append(
                ValidationIssue(
                    code="invalid_invoke_workflow_target_version",
                    message="Workflow call needs a fixed published version.",
                    node_id=node.id,
                )
            )
        try:
            timeout_seconds = int(data.get("timeoutSeconds") or 60)
        except (TypeError, ValueError):
            timeout_seconds = 0
        if not 1 <= timeout_seconds <= 60:
            issues.append(
                ValidationIssue(
                    code="invalid_invoke_workflow_timeout",
                    message="Workflow call timeoutSeconds must be between 1 and 60.",
                    node_id=node.id,
                )
            )
        result_variable = str(data.get("resultVariable") or "").strip()
        if not is_variable_name(result_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_invoke_workflow_result_variable",
                    message="Workflow call resultVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        input_bindings = data.get("inputBindings")
        if not isinstance(input_bindings, dict):
            issues.append(
                ValidationIssue(
                    code="invalid_invoke_workflow_input_bindings",
                    message="Workflow call inputBindings must be an object.",
                    node_id=node.id,
                )
            )
        else:
            for input_name, binding in input_bindings.items():
                if not is_variable_name(str(input_name)):
                    issues.append(
                        ValidationIssue(
                            code="invalid_invoke_workflow_input_name",
                            message="Workflow call input names must be identifiers.",
                            node_id=node.id,
                        )
                    )
                issues.extend(
                    validate_data_table_binding(
                        binding,
                        node_id=node.id,
                        label=f"Workflow call input '{input_name}'",
                    )
                )

    if kind == "http_event_entry":
        body_variable = str(data.get("bodyVariable") or "").strip()
        if body_variable and not is_variable_name(body_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_http_event_body_variable",
                    message="HTTP bodyVariable must be an identifier when configured.",
                    node_id=node.id,
                )
            )
        accepted_content_type = str(
            data.get("acceptedContentType") or "both"
        ).strip()
        if accepted_content_type not in {"both", "json", "text"}:
            issues.append(
                ValidationIssue(
                    code="invalid_http_event_content_type",
                    message="HTTP acceptedContentType must be both, json, or text.",
                    node_id=node.id,
                )
            )
        try:
            max_body_bytes = int(data.get("maxBodyBytes") or 1_048_576)
        except (TypeError, ValueError):
            max_body_bytes = 0
        if not 1_024 <= max_body_bytes <= 1_048_576:
            issues.append(
                ValidationIssue(
                    code="invalid_http_event_max_body_bytes",
                    message="HTTP maxBodyBytes must be between 1024 and 1048576.",
                    node_id=node.id,
                )
            )

    if kind == "suspend_wait":
        wait_mode = str(data.get("waitMode") or "").strip()
        output_variable = str(data.get("outputVariable") or "").strip()
        if wait_mode not in {"duration", "until"}:
            issues.append(
                ValidationIssue(
                    code="invalid_suspend_wait_mode",
                    message="Suspend waitMode must be duration or until.",
                    node_id=node.id,
                )
            )
        if not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_suspend_wait_output_variable",
                    message="Suspend outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        if wait_mode == "duration":
            try:
                duration_seconds = int(data.get("durationSeconds") or 0)
            except (TypeError, ValueError):
                duration_seconds = 0
            if not 1 <= duration_seconds <= 2_592_000:
                issues.append(
                    ValidationIssue(
                        code="invalid_suspend_wait_duration",
                        message="durationSeconds must be between 1 and 2592000.",
                        node_id=node.id,
                    )
                )
        if wait_mode == "until":
            if not str(data.get("untilTemplate") or "").strip():
                issues.append(
                    ValidationIssue(
                        code="missing_suspend_wait_until",
                        message="Until mode needs data.untilTemplate.",
                        node_id=node.id,
                    )
                )
            until_timezone = str(data.get("untilTimezone") or "UTC").strip()
            try:
                ZoneInfo(until_timezone)
            except (ZoneInfoNotFoundError, ValueError):
                issues.append(
                    ValidationIssue(
                        code="invalid_suspend_wait_timezone",
                        message="Suspend wait needs a valid IANA timezone.",
                        node_id=node.id,
                    )
                )

    if kind == "http_event_reply":
        try:
            status_code = int(data.get("statusCode") or 0)
        except (TypeError, ValueError):
            status_code = 0
        if not 200 <= status_code <= 599:
            issues.append(
                ValidationIssue(
                    code="invalid_http_event_reply_status",
                    message="HTTP event reply statusCode must be between 200 and 599.",
                    node_id=node.id,
                )
            )
        if str(data.get("responseBodyType") or "") not in {"text", "json"}:
            issues.append(
                ValidationIssue(
                    code="invalid_http_event_reply_body_type",
                    message="HTTP event reply body type must be text or json.",
                    node_id=node.id,
                )
            )
    if kind == "llm":
        model_id = str(data.get("modelId") or "").strip()
        if not model_id:
            issues.append(
                ValidationIssue(
                    code="missing_llm_model",
                    message="LLM node needs data.modelId.",
                    node_id=node.id,
                )
            )
        elif len(model_id) > 256:
            issues.append(
                ValidationIssue(
                    code="invalid_llm_model",
                    message="LLM modelId cannot exceed 256 characters.",
                    node_id=node.id,
                )
            )
        prompt = str(data.get("prompt") or "")
        if not prompt.strip():
            issues.append(
                ValidationIssue(
                    code="missing_llm_prompt",
                    message="LLM node needs data.prompt.",
                    node_id=node.id,
                )
            )
        elif len(prompt) > 100_000:
            issues.append(
                ValidationIssue(
                    code="invalid_llm_prompt",
                    message="LLM prompt cannot exceed 100000 characters.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_llm_output_variable",
                    message="LLM node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable) or len(output_variable) > 64:
            issues.append(
                ValidationIssue(
                    code="invalid_llm_output_variable",
                    message="LLM outputVariable must be an identifier up to 64 characters.",
                    node_id=node.id,
                )
            )

    if kind == "condition":
        if str(data.get("contractVersion") or "1") == "2":
            variable_name = str(data.get("inputVariable") or "").strip()
            if not is_variable_name(variable_name):
                issues.append(
                    ValidationIssue(
                        code="invalid_condition_input_variable",
                        message="Condition inputVariable must be an identifier.",
                        node_id=node.id,
                    )
                )
            try:
                validate_comparison_rule(
                    {
                        "field": str(data.get("field") or "").strip(),
                        "operator": data.get("operator"),
                        "valueType": data.get("valueType"),
                        "value": data.get("value"),
                    },
                    allow_field=True,
                )
            except WorkflowControlDataError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
        else:
            if not str(data.get("conditionVariable") or "").strip():
                issues.append(
                    ValidationIssue(
                        code="missing_condition_variable",
                        message="Condition node needs data.conditionVariable.",
                        node_id=node.id,
                    )
                )
            if str(data.get("conditionOperator") or "").strip() not in {"equals", "contains"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_condition_operator",
                        message="Condition node operator must be equals or contains.",
                        node_id=node.id,
                    )
                )
            if data.get("conditionValue") in {None, ""}:
                issues.append(
                    ValidationIssue(
                        code="missing_condition_value",
                        message="Condition node needs data.conditionValue.",
                        node_id=node.id,
                    )
                )

    if kind == "code":
        if r20_contract_version(data) == 2:
            try:
                validate_code_v2_config(data)
            except WorkflowR20NodeError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
            return issues
        operation = str(data.get("codeOperation") or "").strip()
        if operation and operation not in {"upper", "lower", "replace", "concat"}:
            issues.append(
                ValidationIssue(
                    code="invalid_code_operation",
                    message="Code node only supports upper, lower, replace, and concat.",
                    node_id=node.id,
                )
            )

    if kind == "variable_assign":
        if r20_contract_version(data) == 2:
            try:
                validate_variable_assign_v2_config(data)
            except WorkflowR20NodeError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
            return issues
        variable_name = str(data.get("variableName") or "").strip()
        if not variable_name:
            issues.append(
                ValidationIssue(
                    code="missing_variable_assign_name",
                    message="Variable assignment node needs data.variableName.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(variable_name):
            issues.append(
                ValidationIssue(
                    code="invalid_variable_assign_name",
                    message="Variable assignment name must be an identifier.",
                    node_id=node.id,
                )
            )
        if not str(data.get("template") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_variable_assign_template",
                    message="Variable assignment node needs data.template.",
                    node_id=node.id,
                )
            )

    if kind == "template_transform":
        if not str(data.get("template") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_template_transform_template",
                    message="Template transform node needs data.template.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_template_transform_output_variable",
                    message="Template transform node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_template_transform_output_variable",
                    message="Template transform outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "variable_aggregator":
        if r20_contract_version(data) == 2:
            try:
                validate_variable_aggregator_v2_config(data)
            except WorkflowR20NodeError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
            return issues
        variable_names = parse_variable_names(str(data.get("variableNames") or ""))
        if not variable_names:
            issues.append(
                ValidationIssue(
                    code="missing_aggregator_variable_names_empty",
                    message="Variable aggregator needs at least one variable name.",
                    node_id=node.id,
                )
            )
        invalid_names = [name for name in variable_names if not is_variable_name(name)]
        if invalid_names:
            issues.append(
                ValidationIssue(
                    code="invalid_aggregator_variable_name",
                    message=f"Variable aggregator has invalid variable names: {', '.join(invalid_names)}.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_aggregator_output_variable",
                    message="Variable aggregator needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_aggregator_output_variable",
                    message="Variable aggregator outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind in {"json_serialize", "json_deserialize"}:
        input_variable = str(data.get("inputVariable") or "").strip()
        output_variable = str(data.get("outputVariable") or "").strip()
        if not input_variable:
            issues.append(
                ValidationIssue(
                    code=f"missing_{kind}_input_variable",
                    message=f"{kind} node needs data.inputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(input_variable):
            issues.append(
                ValidationIssue(
                    code=f"invalid_{kind}_input_variable",
                    message=f"{kind} inputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code=f"missing_{kind}_output_variable",
                    message=f"{kind} node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code=f"invalid_{kind}_output_variable",
                    message=f"{kind} outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        if kind == "json_serialize":
            output_format = str(data.get("format") or "compact").strip()
            if output_format not in {"compact", "pretty"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_json_serialize_format",
                        message="json_serialize format must be compact or pretty.",
                        node_id=node.id,
                    )
                )

    if kind in DATA_TABLE_NODE_KINDS:
        table_id = str(data.get("tableId") or "").strip()
        if not table_id:
            issues.append(
                ValidationIssue(
                    code="missing_data_table_id",
                    message=f"{kind} needs data.tableId.",
                    node_id=node.id,
                )
            )
        version_policy = str(data.get("versionPolicy") or "latest").strip()
        if version_policy not in {"latest", "pinned"}:
            issues.append(
                ValidationIssue(
                    code="invalid_data_table_version_policy",
                    message="Agent Table versionPolicy must be latest or pinned.",
                    node_id=node.id,
                )
            )
        if version_policy == "pinned":
            try:
                pinned_version = int(data.get("pinnedSchemaVersion") or 0)
            except (TypeError, ValueError):
                pinned_version = 0
            if pinned_version < 1:
                issues.append(
                    ValidationIssue(
                        code="missing_data_table_schema_version",
                        message="Pinned Agent Table nodes need a positive pinnedSchemaVersion.",
                        node_id=node.id,
                    )
                )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_data_table_output_variable",
                    message=f"{kind} needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_data_table_output_variable",
                    message="Agent Table outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

        filter_required = kind in {"data_table_update", "data_table_delete"}
        issues.extend(
            validate_data_table_filter(
                data.get("filter"),
                node_id=node.id,
                required=filter_required,
            )
        )

        if kind == "data_table_query":
            fields = data.get("selectFields")
            if fields is not None and (
                not isinstance(fields, list)
                or len(fields) > 50
                or any(not isinstance(value, str) or not value.strip() for value in fields)
            ):
                issues.append(
                    ValidationIssue(
                        code="invalid_data_table_select_fields",
                        message="selectFields must contain at most 50 non-empty field names.",
                        node_id=node.id,
                    )
                )
            try:
                limit = int(data.get("limit") or 20)
            except (TypeError, ValueError):
                limit = 0
            if not 1 <= limit <= 200:
                issues.append(
                    ValidationIssue(
                        code="invalid_data_table_limit",
                        message="Agent Table query limit must be between 1 and 200.",
                        node_id=node.id,
                    )
                )
            if str(data.get("returnMode") or "list") not in {"list", "first"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_data_table_return_mode",
                        message="Agent Table query returnMode must be list or first.",
                        node_id=node.id,
                    )
                )
            sort = data.get("sort") or []
            if not isinstance(sort, list) or len(sort) > 5:
                issues.append(
                    ValidationIssue(
                        code="invalid_data_table_sort",
                        message="Agent Table query sort supports at most 5 entries.",
                        node_id=node.id,
                    )
                )
            else:
                for item in sort:
                    if (
                        not isinstance(item, dict)
                        or not str(item.get("field") or "").strip()
                        or str(item.get("direction") or "asc").lower()
                        not in {"asc", "desc"}
                    ):
                        issues.append(
                            ValidationIssue(
                                code="invalid_data_table_sort",
                                message="Sort entries need a field and asc|desc direction.",
                                node_id=node.id,
                            )
                        )
                        break

        if kind in {"data_table_insert", "data_table_update"}:
            value_bindings = data.get("valueBindings")
            if not isinstance(value_bindings, dict) or not value_bindings:
                issues.append(
                    ValidationIssue(
                        code="missing_data_table_value_bindings",
                        message=f"{kind} needs at least one field value binding.",
                        node_id=node.id,
                    )
                )
            else:
                if len(value_bindings) > 50:
                    issues.append(
                        ValidationIssue(
                            code="too_many_data_table_value_bindings",
                            message="Agent Table writes support at most 50 field bindings.",
                            node_id=node.id,
                        )
                    )
                for field_name, binding in value_bindings.items():
                    if not isinstance(field_name, str) or not field_name.strip():
                        issues.append(
                            ValidationIssue(
                                code="invalid_data_table_binding_field",
                                message="Agent Table value binding field names cannot be empty.",
                                node_id=node.id,
                            )
                        )
                    issues.extend(
                        validate_data_table_binding(
                            binding,
                            node_id=node.id,
                            label=f"Agent Table field '{field_name}'",
                        )
                    )

    if kind == "annotation":
        content = data.get("content", "")
        if not isinstance(content, str):
            issues.append(
                ValidationIssue(
                    code="invalid_annotation_content",
                    message="Annotation content must be a string.",
                    node_id=node.id,
                )
            )
        elif len(content) > 20_000:
            issues.append(
                ValidationIssue(
                    code="annotation_content_too_long",
                    message="Annotation content must not exceed 20,000 characters.",
                    node_id=node.id,
                )
            )

    if kind == "parameter_extractor":
        contract_version = typed_ai_contract_version(data)
        if contract_version not in {1, 2}:
            issues.append(
                ValidationIssue(
                    code="invalid_parameter_extractor_contract_version",
                    message="Parameter extractor contractVersion must be 1 or 2.",
                    node_id=node.id,
                )
            )
            return issues
        elif contract_version == 2:
            try:
                validate_parameter_extractor_v2_config(data)
            except WorkflowTypedAIError as exc:
                issues.append(
                    ValidationIssue(code=exc.code, message=str(exc), node_id=node.id)
                )
        elif not str(data.get("inputVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_input_variable",
                    message="Parameter extractor needs data.inputVariable.",
                    node_id=node.id,
                )
            )
        if typed_ai_contract_version(data) != 2 and not str(data.get("schema") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_schema",
                    message="Parameter extractor needs data.schema.",
                    node_id=node.id,
                )
            )
        if typed_ai_contract_version(data) != 2 and not str(data.get("modelId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_model_id",
                    message="Parameter extractor needs data.modelId.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if typed_ai_contract_version(data) != 2 and not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_output_variable",
                    message="Parameter extractor needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif typed_ai_contract_version(data) != 2 and not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_parameter_extractor_output_variable",
                    message="Parameter extractor outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "knowledge_retrieval":
        query_variable = str(data.get("queryVariable") or "").strip()
        if not query_variable:
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_retrieval_query_variable",
                    message="Knowledge retrieval node needs data.queryVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(query_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_retrieval_query_variable",
                    message="Knowledge retrieval queryVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        raw_contract_version = data.get("contractVersion")
        contract_version = 1
        if raw_contract_version is not None:
            try:
                contract_version = int(raw_contract_version)
            except (TypeError, ValueError):
                contract_version = 0
            if contract_version != 2:
                issues.append(
                    ValidationIssue(
                        code="invalid_knowledge_retrieval_contract_version",
                        message="Knowledge retrieval contractVersion must be 2 when set.",
                        node_id=node.id,
                    )
                )
        if contract_version == 2:
            if not str(data.get("knowledgeBaseId") or "").strip():
                issues.append(
                    ValidationIssue(
                        code="missing_knowledge_retrieval_knowledge_base_id",
                        message="Knowledge retrieval V2 needs data.knowledgeBaseId.",
                        node_id=node.id,
                    )
                )
            return_mode = str(data.get("returnMode") or "result").strip()
            if return_mode not in {"context", "result"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_knowledge_retrieval_return_mode",
                        message=(
                            "Knowledge retrieval returnMode must be context or result."
                        ),
                        node_id=node.id,
                    )
                )
        top_k = str(data.get("top_k") or "3").strip()
        try:
            top_k_int = int(top_k)
        except ValueError:
            top_k_int = 0
        top_k_max = 10 if contract_version == 2 else 20
        if top_k_int < 1 or top_k_int > top_k_max:
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_retrieval_top_k",
                    message=(
                        "Knowledge retrieval top_k must be an integer between "
                        f"1 and {top_k_max}."
                    ),
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_retrieval_output_variable",
                    message="Knowledge retrieval node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_retrieval_output_variable",
                    message="Knowledge retrieval outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "knowledge_citation":
        query_variable = str(data.get("queryVariable") or "").strip()
        if not query_variable:
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_citation_query_variable",
                    message="Knowledge citation node needs data.queryVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(query_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_citation_query_variable",
                    message="Knowledge citation queryVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        top_k = str(data.get("top_k") or "4").strip()
        try:
            top_k_int = int(top_k)
        except ValueError:
            top_k_int = 0
        if top_k_int < 1 or top_k_int > 10:
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_citation_top_k",
                    message="Knowledge citation top_k must be an integer between 1 and 10.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_citation_output_variable",
                    message="Knowledge citation node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_citation_output_variable",
                    message="Knowledge citation outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "document_extractor":
        asset_id_variable = str(data.get("assetIdVariable") or "").strip()
        legacy_path_variable = str(data.get("sourcePathVariable") or "").strip()
        if asset_id_variable and legacy_path_variable:
            issues.append(
                ValidationIssue(
                    code="ambiguous_document_extractor_source",
                    message=(
                        "Document extractor cannot combine assetIdVariable with the "
                        "legacy sourcePathVariable."
                    ),
                    node_id=node.id,
                )
            )
        elif asset_id_variable:
            if not is_variable_name(asset_id_variable):
                issues.append(
                    ValidationIssue(
                        code="invalid_document_extractor_asset_id_variable",
                        message=(
                            "Document extractor assetIdVariable must be an identifier."
                        ),
                        node_id=node.id,
                    )
                )
        elif legacy_path_variable:
            if not is_variable_name(legacy_path_variable):
                issues.append(
                    ValidationIssue(
                        code="invalid_document_extractor_source_path_variable",
                        message=(
                            "Legacy document extractor sourcePathVariable must be an "
                            "identifier."
                        ),
                        node_id=node.id,
                    )
                )
            issues.append(
                ValidationIssue(
                    code="legacy_document_extractor_source_path_read_only",
                    message=(
                        "Legacy path-based document extractors are read-only compatible; "
                        "new nodes must use data.assetIdVariable."
                    ),
                    severity="warning",
                    node_id=node.id,
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    code="missing_document_extractor_asset_id_variable",
                    message="Document extractor needs data.assetIdVariable.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_document_extractor_output_variable",
                    message="Document extractor needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_document_extractor_output_variable",
                    message="Document extractor outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "vision_understanding":
        asset_id_variable = str(data.get("assetIdVariable") or "").strip()
        if not asset_id_variable:
            issues.append(
                ValidationIssue(
                    code="missing_vision_asset_id_variable",
                    message="Vision understanding needs data.assetIdVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(asset_id_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_vision_asset_id_variable",
                    message="Vision assetIdVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        if not str(data.get("visionModelId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_vision_model_id",
                    message="Vision understanding needs data.visionModelId.",
                    node_id=node.id,
                )
            )
        page_strategy = str(data.get("pdfPageStrategy") or "auto").strip()
        if page_strategy not in {"auto", "all", "scanned_only"}:
            issues.append(
                ValidationIssue(
                    code="invalid_vision_pdf_page_strategy",
                    message=(
                        "Vision pdfPageStrategy must be auto, all, or scanned_only."
                    ),
                    node_id=node.id,
                )
            )
        failure_policy = str(
            data.get("failurePolicy") or "continue_on_error"
        ).strip()
        if failure_policy not in {"continue_on_error", "strict"}:
            issues.append(
                ValidationIssue(
                    code="invalid_vision_failure_policy",
                    message=(
                        "Vision failurePolicy must be continue_on_error or strict."
                    ),
                    node_id=node.id,
                )
            )
        try:
            max_pages = int(data.get("maxPages") or 100)
        except (TypeError, ValueError):
            max_pages = 0
        if max_pages < 1 or max_pages > 200:
            issues.append(
                ValidationIssue(
                    code="invalid_vision_max_pages",
                    message="Vision maxPages must be between 1 and 200.",
                    node_id=node.id,
                )
            )
        try:
            max_image_edge = int(data.get("maxImageEdge") or 2048)
        except (TypeError, ValueError):
            max_image_edge = 0
        if max_image_edge < 512 or max_image_edge > 4096:
            issues.append(
                ValidationIssue(
                    code="invalid_vision_max_image_edge",
                    message="Vision maxImageEdge must be between 512 and 4096.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_vision_output_variable",
                    message="Vision understanding needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_vision_output_variable",
                    message="Vision outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "human_intervention":
        if r20_contract_version(data) == 2:
            try:
                validate_human_intervention_v2_config(data)
            except WorkflowR20NodeError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
            return issues
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            issues.append(
                ValidationIssue(
                    code="missing_prompt",
                    message="Human intervention node needs data.prompt.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_output_variable",
                    message="Human intervention node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_human_intervention_output_variable",
                    message="Human intervention outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "question_classifier":
        contract_version = typed_ai_contract_version(data)
        if contract_version not in {1, 2}:
            issues.append(
                ValidationIssue(
                    code="invalid_question_classifier_contract_version",
                    message="Question classifier contractVersion must be 1 or 2.",
                    node_id=node.id,
                )
            )
            return issues
        if contract_version == 2:
            try:
                validate_question_classifier_v2_config(data)
            except WorkflowTypedAIError as exc:
                issues.append(
                    ValidationIssue(code=exc.code, message=str(exc), node_id=node.id)
                )
            return issues
        input_variable = str(data.get("inputVariable") or "").strip()
        if not input_variable:
            issues.append(
                ValidationIssue(
                    code="missing_input_variable",
                    message="Question classifier node needs data.inputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(input_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_input_variable",
                    message="Question classifier inputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

        categories_json = str(data.get("categories") or "").strip()
        if not categories_json:
            issues.append(
                ValidationIssue(
                    code="missing_categories",
                    message="Question classifier node needs data.categories.",
                    node_id=node.id,
                )
            )
        else:
            try:
                categories = json.loads(categories_json)
            except ValueError:
                categories = None
                issues.append(
                    ValidationIssue(
                        code="invalid_categories_json",
                        message="Question classifier categories must be valid JSON.",
                        node_id=node.id,
                    )
                )
            if categories is not None:
                valid_schema = isinstance(categories, dict) and bool(categories)
                if valid_schema:
                    for category_name, keywords in categories.items():
                        if not isinstance(category_name, str) or not category_name.strip():
                            valid_schema = False
                            break
                        if not isinstance(keywords, list) or not keywords:
                            valid_schema = False
                            break
                        if not all(
                            isinstance(keyword, str) and keyword.strip()
                            for keyword in keywords
                        ):
                            valid_schema = False
                            break
                if not valid_schema:
                    issues.append(
                        ValidationIssue(
                            code="invalid_categories_schema",
                            message=(
                                "Question classifier categories must be a non-empty "
                                "object of string arrays."
                            ),
                            node_id=node.id,
                        )
                    )

        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_output_variable",
                    message="Question classifier node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_output_variable",
                    message="Question classifier outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

        match_mode = str(data.get("matchMode") or "contains_any").strip()
        if match_mode not in {"contains_any", "contains_all"}:
            issues.append(
                ValidationIssue(
                    code="invalid_match_mode",
                    message="Question classifier matchMode must be contains_any or contains_all.",
                    node_id=node.id,
                )
            )

        case_sensitive = str(data.get("caseSensitive") or "false").strip().lower()
        if case_sensitive not in {"true", "false"}:
            issues.append(
                ValidationIssue(
                    code="invalid_case_sensitive",
                    message="Question classifier caseSensitive must be true or false.",
                    node_id=node.id,
                )
            )

        use_llm_fallback = str(data.get("useLlmFallback") or "false").strip().lower()
        if use_llm_fallback not in {"true", "false"}:
            issues.append(
                ValidationIssue(
                    code="invalid_use_llm_fallback",
                    message="Question classifier useLlmFallback must be true or false.",
                    node_id=node.id,
                )
            )
        elif use_llm_fallback == "true" and not str(data.get("modelId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_model_when_fallback",
                    message="Question classifier needs data.modelId when LLM fallback is enabled.",
                    node_id=node.id,
                )
            )

    if kind == "agent":
        instruction = str(data.get("instruction") or "").strip()
        if not instruction:
            issues.append(
                ValidationIssue(
                    code="missing_instruction",
                    message="Agent node needs data.instruction.",
                    node_id=node.id,
                )
            )

        model_id = str(data.get("modelId") or "").strip()
        if not model_id:
            issues.append(
                ValidationIssue(
                    code="missing_model_id",
                    message="Agent node needs data.modelId.",
                    node_id=node.id,
                )
            )

        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_output_variable",
                    message="Agent node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_output_variable",
                    message="Agent outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

        agent_mode = str(data.get("agentMode") or "tool_first").strip()
        if agent_mode not in {"tool_first", "direct"}:
            issues.append(
                ValidationIssue(
                    code="invalid_agent_mode",
                    message="Agent agentMode must be tool_first or direct.",
                    node_id=node.id,
                )
            )

        agent_strategy = str(data.get("agentStrategy") or "auto").strip()
        if agent_strategy not in {"auto", "function_calling", "react"}:
            issues.append(
                ValidationIssue(
                    code="invalid_agent_strategy",
                    message=(
                        "Agent agentStrategy must be auto, function_calling, or react."
                    ),
                    node_id=node.id,
                )
            )

        parallel_tool_calls = str(
            data.get("parallelToolCalls") or "false"
        ).strip().lower()
        if parallel_tool_calls not in {"true", "false"}:
            issues.append(
                ValidationIssue(
                    code="invalid_parallel_tool_calls",
                    message="Agent parallelToolCalls must be true or false.",
                    node_id=node.id,
                )
            )

        max_iterations = str(data.get("maxIterations") or "").strip()
        if max_iterations:
            try:
                max_iterations_value = int(max_iterations)
            except ValueError:
                max_iterations_value = 0
            if max_iterations_value < 1 or max_iterations_value > 20:
                issues.append(
                    ValidationIssue(
                        code="invalid_max_iterations",
                        message="Agent maxIterations must be between 1 and 20.",
                        node_id=node.id,
                    )
                )

        temperature = str(data.get("temperature") or "").strip()
        if temperature:
            try:
                temperature_value = float(temperature)
            except ValueError:
                temperature_value = -1.0
            if temperature_value < 0 or temperature_value > 2:
                issues.append(
                    ValidationIssue(
                        code="invalid_temperature",
                        message="Agent temperature must be between 0 and 2.",
                        node_id=node.id,
                    )
                )

    if kind == "workflow_agent":
        agent_name = str(data.get("agentName") or "").strip()
        if not agent_name:
            issues.append(
                ValidationIssue(
                    code="missing_workflow_agent_name",
                    message="Workflow agent node needs data.agentName.",
                    node_id=node.id,
                )
            )

        model_id = str(data.get("modelId") or "").strip()
        if not model_id:
            issues.append(
                ValidationIssue(
                    code="missing_workflow_agent_model",
                    message="Workflow agent node needs data.modelId.",
                    node_id=node.id,
                )
            )

        role_prompt = str(data.get("rolePrompt") or "").strip()
        if not role_prompt:
            issues.append(
                ValidationIssue(
                    code="missing_workflow_agent_role_prompt",
                    message="Workflow agent node needs data.rolePrompt.",
                    node_id=node.id,
                )
            )

        task_input = str(data.get("taskInput") or "").strip()
        if not task_input:
            issues.append(
                ValidationIssue(
                    code="missing_workflow_agent_task_input",
                    message="Workflow agent node needs data.taskInput.",
                    node_id=node.id,
                )
            )

        tool_mode = str(data.get("toolMode") or "none").strip()
        if tool_mode not in {"none", "mcp_tools"}:
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_tool_mode",
                    message="Workflow agent toolMode must be none or mcp_tools.",
                    node_id=node.id,
                )
            )

        agent_strategy = str(data.get("agentStrategy") or "auto").strip()
        if agent_strategy not in {"auto", "function_calling", "react"}:
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_strategy",
                    message=(
                        "Workflow agent agentStrategy must be auto, "
                        "function_calling, or react."
                    ),
                    node_id=node.id,
                )
            )

        parallel_tool_calls = str(
            data.get("parallelToolCalls") or "false"
        ).strip().lower()
        if parallel_tool_calls not in {"true", "false"}:
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_parallel_tool_calls",
                    message="Workflow agent parallelToolCalls must be true or false.",
                    node_id=node.id,
                )
            )

        exception_handling = str(data.get("exceptionHandling") or "none").strip()
        if exception_handling not in {"none", "fail", "empty_output"}:
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_exception_handling",
                    message=(
                        "Workflow agent exceptionHandling must be none, fail, "
                        "or empty_output."
                    ),
                    node_id=node.id,
                )
            )

        memory_read_scope = str(data.get("memoryReadScope") or "both").strip()
        if memory_read_scope not in {"conversation", "xpert", "both"}:
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_memory_read_scope",
                    message=(
                        "Workflow agent memoryReadScope must be conversation, "
                        "xpert, or both."
                    ),
                    node_id=node.id,
                )
            )

        memory_write_target = str(data.get("memoryWriteTarget") or "xpert").strip()
        if memory_write_target not in {"conversation", "xpert"}:
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_memory_write_target",
                    message=(
                        "Workflow agent memoryWriteTarget must be conversation or xpert."
                    ),
                    node_id=node.id,
                )
            )

        knowledge_read_enabled = config_truthy(data.get("knowledgeReadEnabled"))
        knowledge_write_enabled = config_truthy(data.get("knowledgeWriteEnabled"))
        if knowledge_read_enabled or knowledge_write_enabled:
            if tool_mode != "mcp_tools":
                issues.append(
                    ValidationIssue(
                        code="workflow_agent_knowledge_tools_require_runtime_mode",
                        message=(
                            "Workflow agent knowledge tools require toolMode=mcp_tools."
                        ),
                        node_id=node.id,
                    )
                )
            knowledge_base_ids = list(
                dict.fromkeys(
                    item.strip()
                    for item in re.split(
                        r"[,\n]",
                        str(data.get("knowledgeBaseIds") or ""),
                    )
                    if item.strip()
                )
            )
            if not 1 <= len(knowledge_base_ids) <= 5:
                issues.append(
                    ValidationIssue(
                        code="invalid_workflow_agent_knowledge_base_ids",
                        message=(
                            "Workflow agent knowledge tools require between 1 and 5 knowledge base IDs."
                        ),
                        node_id=node.id,
                    )
                )

        max_iterations = str(data.get("maxIterations") or "").strip()
        if max_iterations:
            try:
                max_iterations_value = int(max_iterations)
            except ValueError:
                max_iterations_value = 0
            if max_iterations_value < 1 or max_iterations_value > 20:
                issues.append(
                    ValidationIssue(
                        code="invalid_workflow_agent_max_iterations",
                        message="Workflow agent maxIterations must be between 1 and 20.",
                        node_id=node.id,
                    )
                )

        tool_budget_fields = (
            ("maxToolConcurrency", 1, 8),
            ("maxToolCalls", 1, 50),
            ("maxToolDepth", 1, 4),
        )
        for field_name, minimum, maximum in tool_budget_fields:
            raw_value = str(data.get(field_name) or "").strip()
            if not raw_value:
                continue
            try:
                parsed_value = int(raw_value)
            except ValueError:
                parsed_value = minimum - 1
            if parsed_value < minimum or parsed_value > maximum:
                issues.append(
                    ValidationIssue(
                        code=f"invalid_workflow_agent_{field_name}",
                        message=(
                            f"Workflow agent {field_name} must be between "
                            f"{minimum} and {maximum}."
                        ),
                        node_id=node.id,
                    )
                )

        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_workflow_agent_output_variable",
                    message="Workflow agent node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_workflow_agent_output_variable",
                    message="Workflow agent outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "external_xpert":
        if not str(data.get("xpertId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_external_xpert_id",
                    message="External Xpert resource needs data.xpertId.",
                    node_id=node.id,
                )
            )
        tool_name = str(data.get("toolName") or "").strip()
        if not tool_name:
            issues.append(
                ValidationIssue(
                    code="missing_external_xpert_tool_name",
                    message="External Xpert resource needs data.toolName.",
                    node_id=node.id,
                )
            )
        elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", tool_name):
            issues.append(
                ValidationIssue(
                    code="invalid_external_xpert_tool_name",
                    message="External Xpert toolName must be a stable tool identifier.",
                    node_id=node.id,
                )
            )
        version_policy = str(
            data.get("versionPolicy") or "current_published"
        ).strip()
        if version_policy not in {"current_published", "pinned"}:
            issues.append(
                ValidationIssue(
                    code="invalid_external_xpert_version_policy",
                    message=(
                        "External Xpert versionPolicy must be current_published or pinned."
                    ),
                    node_id=node.id,
                )
            )
        if version_policy == "pinned":
            try:
                pinned_version = int(data.get("pinnedVersion"))
            except (TypeError, ValueError):
                pinned_version = 0
            if pinned_version < 1:
                issues.append(
                    ValidationIssue(
                        code="invalid_external_xpert_pinned_version",
                        message="Pinned External Xpert resources require pinnedVersion >= 1.",
                        node_id=node.id,
                    )
                )

    if kind == "knowledge_base":
        if not str(data.get("knowledgeBaseId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_base_resource_id",
                    message="Knowledge base resource needs data.knowledgeBaseId.",
                    node_id=node.id,
                )
            )
        try:
            resource_top_k = int(data.get("topK", 5))
        except (TypeError, ValueError):
            resource_top_k = 0
        if resource_top_k < 1 or resource_top_k > 10:
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_base_resource_top_k",
                    message="Knowledge base resource topK must be between 1 and 10.",
                    node_id=node.id,
                )
            )
        try:
            score_threshold = float(data.get("scoreThreshold", 0))
        except (TypeError, ValueError):
            score_threshold = -1
        if score_threshold < 0 or score_threshold > 1:
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_base_resource_score_threshold",
                    message=(
                        "Knowledge base resource scoreThreshold must be between 0 and 1."
                    ),
                    node_id=node.id,
                )
            )

    if kind == "toolset_resource":
        if not str(data.get("toolsetId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_toolset_resource_id",
                    message="Toolset resource needs data.toolsetId.",
                    node_id=node.id,
                )
            )
        version_policy = str(
            data.get("versionPolicy") or "current_published"
        ).strip()
        if version_policy not in {"current_published", "pinned"}:
            issues.append(
                ValidationIssue(
                    code="invalid_toolset_version_policy",
                    message=(
                        "Toolset versionPolicy must be current_published or pinned."
                    ),
                    node_id=node.id,
                )
            )
        if version_policy == "pinned":
            try:
                pinned_version = int(data.get("pinnedVersion"))
            except (TypeError, ValueError):
                pinned_version = 0
            if pinned_version < 1:
                issues.append(
                    ValidationIssue(
                        code="invalid_toolset_pinned_version",
                        message="Pinned Toolset resources require pinnedVersion >= 1.",
                        node_id=node.id,
                    )
                )

    if kind == "plugin_resource":
        if not str(data.get("pluginId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_plugin_resource_id",
                    message="Plugin resource needs data.pluginId.",
                    node_id=node.id,
                )
            )
        version_policy = str(data.get("versionPolicy") or "latest").strip()
        if version_policy not in {"latest", "pinned"}:
            issues.append(
                ValidationIssue(
                    code="invalid_plugin_version_policy",
                    message="Plugin versionPolicy must be latest or pinned.",
                    node_id=node.id,
                )
            )
        if version_policy == "pinned":
            try:
                pinned_version = int(data.get("pinnedVersion"))
            except (TypeError, ValueError):
                pinned_version = 0
            if pinned_version < 1:
                issues.append(
                    ValidationIssue(
                        code="invalid_plugin_pinned_version",
                        message="Pinned Plugin resources require pinnedVersion >= 1.",
                        node_id=node.id,
                    )
                )

    if kind == "agent_task":
        task_title = str(data.get("taskTitle") or "").strip()
        if not task_title:
            issues.append(
                ValidationIssue(
                    code="missing_agent_task_title",
                    message="Agent task node needs data.taskTitle.",
                    node_id=node.id,
                )
            )

        task_input = str(data.get("taskInput") or "").strip()
        if not task_input:
            issues.append(
                ValidationIssue(
                    code="missing_agent_task_input",
                    message="Agent task node needs data.taskInput.",
                    node_id=node.id,
                )
            )

        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_agent_task_output_variable",
                    message="Agent task node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_agent_task_output_variable",
                    message="Agent task outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "agent_handoff":
        task_id_variable = str(data.get("taskIdVariable") or "").strip()
        if not task_id_variable:
            issues.append(
                ValidationIssue(
                    code="missing_agent_handoff_task_id_variable",
                    message="Agent handoff node needs data.taskIdVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(task_id_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_agent_handoff_task_id_variable",
                    message="Agent handoff taskIdVariable must be an identifier.",
                    node_id=node.id,
                )
            )

        target_agent = str(data.get("targetAgent") or "").strip()
        if not target_agent:
            issues.append(
                ValidationIssue(
                    code="missing_agent_handoff_target_agent",
                    message="Agent handoff node needs data.targetAgent.",
                    node_id=node.id,
                )
            )

        reason = str(data.get("reason") or "").strip()
        if not reason:
            issues.append(
                ValidationIssue(
                    code="missing_agent_handoff_reason",
                    message="Agent handoff node needs data.reason.",
                    node_id=node.id,
                )
            )

        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_agent_handoff_output_variable",
                    message="Agent handoff node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_agent_handoff_output_variable",
                    message="Agent handoff outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        issues.extend(
            validate_handoff_execution_configuration(
                node,
                code_prefix="agent_handoff",
            )
        )

    if kind == "handoff_router":
        source_variable = str(data.get("sourceVariable") or "").strip()
        if not source_variable:
            issues.append(
                ValidationIssue(
                    code="missing_handoff_router_source_variable",
                    message="Handoff router node needs data.sourceVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(source_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_handoff_router_source_variable",
                    message="Handoff router sourceVariable must be an identifier.",
                    node_id=node.id,
                )
            )

        task_title = str(data.get("taskTitle") or "").strip()
        if not task_title:
            issues.append(
                ValidationIssue(
                    code="missing_handoff_router_task_title",
                    message="Handoff router node needs data.taskTitle.",
                    node_id=node.id,
                )
            )

        target_agent = str(data.get("targetAgent") or "").strip()
        if not target_agent:
            issues.append(
                ValidationIssue(
                    code="missing_handoff_router_target_agent",
                    message="Handoff router node needs data.targetAgent.",
                    node_id=node.id,
                )
            )

        reason_template = str(data.get("reasonTemplate") or "").strip()
        if not reason_template:
            issues.append(
                ValidationIssue(
                    code="missing_handoff_router_reason_template",
                    message="Handoff router node needs data.reasonTemplate.",
                    node_id=node.id,
                )
            )

        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_handoff_router_output_variable",
                    message="Handoff router node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_handoff_router_output_variable",
                    message="Handoff router outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        issues.extend(
            validate_handoff_execution_configuration(
                node,
                code_prefix="handoff_router",
            )
        )

    if kind == "mcp_tool":
        if r20_contract_version(data) == 2:
            try:
                validate_mcp_tool_v2_config(data)
            except WorkflowR20NodeError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
            return issues
        tool_name = str(data.get("toolName") or "").strip()
        if not tool_name:
            issues.append(
                ValidationIssue(
                    code="missing_tool_name",
                    message="MCP tool node needs data.toolName.",
                    node_id=node.id,
                )
            )
        arguments_json = str(data.get("argumentsJson") or "").strip()
        if not arguments_json:
            issues.append(
                ValidationIssue(
                    code="missing_arguments",
                    message="MCP tool node needs data.argumentsJson.",
                    node_id=node.id,
                )
            )
        else:
            try:
                parsed_arguments = json.loads(arguments_json)
            except ValueError:
                parsed_arguments = None
            if not isinstance(parsed_arguments, dict):
                issues.append(
                    ValidationIssue(
                        code="invalid_arguments_json",
                        message="MCP tool argumentsJson must be a JSON object.",
                        node_id=node.id,
                    )
                )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_output_variable",
                    message="MCP tool node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_output_variable",
                    message="MCP tool outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "time_tool":
        if is_time_v2(data):
            try:
                validate_time_v2_config(data)
            except WorkflowFileDataError as exc:
                issues.append(
                    ValidationIssue(
                        code=exc.code.lower(),
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
        else:
            operation = str(data.get("operation") or "").strip()
            if not operation:
                issues.append(
                    ValidationIssue(
                        code="missing_time_operation",
                        message="Time tool node needs data.operation.",
                        node_id=node.id,
                    )
                )
            elif operation not in {"now_iso", "now_epoch", "format"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_time_operation",
                        message="Time tool operation must be now_iso, now_epoch, or format.",
                        node_id=node.id,
                    )
                )
            output_variable = str(data.get("outputVariable") or "").strip()
            if not output_variable:
                issues.append(
                    ValidationIssue(
                        code="missing_output_variable",
                        message="Time tool node needs data.outputVariable.",
                        node_id=node.id,
                    )
                )
            elif not is_variable_name(output_variable):
                issues.append(
                    ValidationIssue(
                        code="invalid_output_variable",
                        message="Time tool outputVariable must be an identifier.",
                        node_id=node.id,
                    )
                )

    if kind == "http_request" and is_http_request_v2(data):
        try:
            validate_http_request_v2_config(data)
        except WorkflowHttpRequestError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )

    if kind == "http_request" and not is_http_request_v2(data):
        if not str(data.get("url") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_http_request_url",
                    message="HTTP request node needs data.url.",
                    node_id=node.id,
                )
            )
        method = str(data.get("method") or "GET").strip().upper()
        if method not in {"GET", "POST"}:
            issues.append(
                ValidationIssue(
                    code="invalid_http_request_method",
                    message="HTTP request method must be GET or POST.",
                    node_id=node.id,
                )
            )
        headers_json = str(data.get("headersJson") or "").strip()
        if headers_json:
            try:
                parsed_headers = json.loads(headers_json)
            except ValueError:
                parsed_headers = None
            if not isinstance(parsed_headers, dict):
                issues.append(
                    ValidationIssue(
                        code="invalid_http_request_headers_json",
                        message="HTTP request headersJson must be a JSON object.",
                        node_id=node.id,
                    )
                )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_http_request_output_variable",
                    message="HTTP request node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_http_request_output_variable",
                    message="HTTP request outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "terminate_error":
        try:
            validate_terminate_error_config(
                data.get("errorCode"),
                data.get("message"),
            )
        except WorkflowControlDataError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )

    if kind == "multi_route":
        input_variable = str(data.get("inputVariable") or "").strip()
        if not is_variable_name(input_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_multi_route_input_variable",
                    message="Multi route inputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        routes = data.get("routes")
        if not isinstance(routes, list) or not 2 <= len(routes) <= 8:
            issues.append(
                ValidationIssue(
                    code="invalid_multi_route_count",
                    message="Multi route requires between 2 and 8 routes.",
                    node_id=node.id,
                )
            )
        else:
            route_ids: list[str] = []
            for route in routes:
                try:
                    validated = validate_comparison_rule(
                        route,
                        allow_field=False,
                        require_route=True,
                    )
                    route_ids.append(str(validated.get("id") or ""))
                except WorkflowControlDataError as exc:
                    issues.append(
                        ValidationIssue(
                            code=exc.code.lower(),
                            message=exc.safe_message,
                            node_id=node.id,
                        )
                    )
            if len(route_ids) != len(set(route_ids)):
                issues.append(
                    ValidationIssue(
                        code="duplicate_multi_route_id",
                        message="Multi route ids must be unique.",
                        node_id=node.id,
                    )
                )

    if kind == "list_operation":
        input_variable = str(data.get("inputVariable") or "").strip()
        if not input_variable:
            issues.append(
                ValidationIssue(
                    code="missing_list_operation_input_variable",
                    message="List operation node needs data.inputVariable.",
                    node_id=node.id,
                )
            )

        elif not is_variable_name(input_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_list_operation_input_variable",
                    message="List operation inputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        operator = str(data.get("operator") or "").strip()
        if operator not in LIST_OPERATORS:
            issues.append(
                ValidationIssue(
                    code="invalid_list_operation_operator",
                    message="List operation uses an unsupported operator.",
                    node_id=node.id,
                )
            )
        try:
            if operator == "filter":
                filter_array(
                    [],
                    rules=data.get("filterRules"),
                    mode=str(data.get("filterMode") or ""),
                )
            elif operator == "sort":
                sort_array([], keys=data.get("sortKeys"))
            elif operator == "deduplicate":
                deduplicate_array(
                    [],
                    fields=data.get("deduplicateFields", []),
                )
            elif operator in {"take", "skip", "slice"}:
                execute_list_operation(
                    [],
                    operator=operator,
                    count=data.get("count"),
                    start_index=data.get("startIndex"),
                    end_index=data.get("endIndex"),
                )
        except WorkflowControlDataError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )
        if operator == "join" and data.get("joinSeparator") in {None, ""}:
            issues.append(
                ValidationIssue(
                    code="missing_list_operation_separator",
                    message="Join list operation needs data.joinSeparator.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_list_operation_output_variable",
                    message="List operation node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_list_operation_output_variable",
                    message="List operation outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "data_aggregate":
        input_variable = str(data.get("inputVariable") or "").strip()
        output_variable = str(data.get("outputVariable") or "").strip()
        if not is_variable_name(input_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_data_aggregate_input_variable",
                    message="Data aggregate inputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        if not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_data_aggregate_output_variable",
                    message="Data aggregate outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )
        try:
            validate_aggregate_config(
                group_by_fields=data.get("groupByFields"),
                measures=data.get("measures"),
            )
        except WorkflowControlDataError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )

    if kind == "data_merge":
        try:
            validate_data_merge_config(data)
        except WorkflowR21Error as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )

    if kind == "object_transform":
        try:
            validate_object_transform_config(data)
        except WorkflowFileDataError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )

    if kind == "file_output":
        try:
            validate_file_output_config(data)
        except WorkflowFileDataError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )

    if kind == "dataset_compare":
        for field_name in ("leftVariable", "rightVariable", "outputVariable"):
            variable = str(data.get(field_name) or "").strip()
            if not is_variable_name(variable):
                issues.append(
                    ValidationIssue(
                        code=f"invalid_dataset_compare_{field_name.lower()}",
                        message=f"Dataset compare {field_name} must be an identifier.",
                        node_id=node.id,
                    )
                )
        try:
            validate_dataset_compare_config(data.get("keyFields"))
        except WorkflowControlDataError as exc:
            issues.append(
                ValidationIssue(
                    code=exc.code.lower(),
                    message=exc.safe_message,
                    node_id=node.id,
                )
            )
        if not isinstance(data.get("includeUnchanged"), bool):
            issues.append(
                ValidationIssue(
                    code="invalid_dataset_compare_include_unchanged",
                    message="Dataset compare includeUnchanged must be boolean.",
                    node_id=node.id,
                )
            )

    if kind == "iteration":
        if not str(data.get("inputVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_iteration_input_variable",
                    message="Iteration node needs data.inputVariable.",
                    node_id=node.id,
                )
            )
        iteration_variable = str(data.get("iterationVariable") or "").strip()
        if not iteration_variable:
            issues.append(
                ValidationIssue(
                    code="missing_iteration_variable",
                    message="Iteration node needs data.iterationVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(iteration_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_iteration_variable",
                    message="Iteration variable must be an identifier.",
                    node_id=node.id,
                )
            )
        if not str(data.get("itemTemplate") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_iteration_template",
                    message="Iteration node needs data.itemTemplate.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_iteration_output_variable",
                    message="Iteration node needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_iteration_output_variable",
                    message="Iteration outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "output":
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_output_variable",
                    message="Output node needs data.outputVariable.",
                    node_id=node.id,
                )
            )

    if kind == "runtime_middleware":
        middleware_id = str(data.get("runtimeMiddlewareId") or "").strip()
        if not middleware_id:
            issues.append(
                ValidationIssue(
                    code="missing_runtime_middleware_id",
                    message="runtime_middleware node needs data.runtimeMiddlewareId.",
                    node_id=node.id,
                )
            )
        middleware_kind = str(data.get("runtimeMiddlewareKind") or "").strip()
        if not middleware_kind:
            issues.append(
                ValidationIssue(
                    code="missing_runtime_middleware_kind",
                    message="runtime_middleware node needs data.runtimeMiddlewareKind.",
                    node_id=node.id,
                )
            )
        if middleware_id == "system_prompt_injector":
            config = data.get("runtimeMiddlewareConfig")
            if not isinstance(config, dict):
                issues.append(
                    ValidationIssue(
                        code="missing_runtime_middleware_config",
                        message="system_prompt_injector needs data.runtimeMiddlewareConfig.",
                        node_id=node.id,
                    )
                )
                config = {}
            system_prompt = str(config.get("system_prompt") or "").strip()
            if not system_prompt:
                issues.append(
                    ValidationIssue(
                        code="missing_runtime_middleware_system_prompt",
                        message="system_prompt_injector needs runtimeMiddlewareConfig.system_prompt.",
                        node_id=node.id,
                    )
                )
        if middleware_id == "tool_policy":
            config = data.get("runtimeMiddlewareConfig")
            if not isinstance(config, dict):
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_config",
                        message="tool_policy needs data.runtimeMiddlewareConfig as a dict.",
                        node_id=node.id,
                    )
                )
            else:
                allow_by_default_raw = config.get("allow_by_default")
                if allow_by_default_raw is not None:
                    if isinstance(allow_by_default_raw, bool):
                        pass
                    elif isinstance(allow_by_default_raw, str):
                        if allow_by_default_raw.lower() not in {"true", "false"}:
                            issues.append(
                                ValidationIssue(
                                    code="invalid_runtime_middleware_tool_policy",
                                    message=(
                                        "tool_policy allow_by_default must be a "
                                        "boolean or the string 'true'/'false'."
                                    ),
                                    node_id=node.id,
                                )
                            )
                    else:
                        issues.append(
                            ValidationIssue(
                                code="invalid_runtime_middleware_tool_policy",
                                message=(
                                    "tool_policy allow_by_default must be a "
                                    "boolean or the string 'true'/'false'."
                                ),
                                node_id=node.id,
                            )
                        )

        priority_raw = data.get("middlewarePriority", 100)
        try:
            priority = int(str(priority_raw))
        except (TypeError, ValueError):
            priority = -1
        if not 0 <= priority <= 1000:
            issues.append(
                ValidationIssue(
                    code="invalid_runtime_middleware_priority",
                    message="runtime_middleware middlewarePriority must be an integer from 0 to 1000.",
                    node_id=node.id,
                )
            )

        config = data.get("runtimeMiddlewareConfig")
        config = config if isinstance(config, dict) else {}
        if middleware_id == "content_policy":
            try:
                from server.xpert_runtime.content_policy import (
                    ContentPolicyError,
                    validate_content_policy_config,
                )
            except ModuleNotFoundError:
                from xpert_runtime.content_policy import (  # type: ignore[no-redef]
                    ContentPolicyError,
                    validate_content_policy_config,
                )
            try:
                validate_content_policy_config(config)
            except ContentPolicyError as exc:
                issues.append(
                    ValidationIssue(code=exc.code, message=str(exc), node_id=node.id)
                )
        if middleware_id == "context_compression":
            for name, minimum, maximum in (
                ("max_context_tokens", 2048, 200000),
                ("keep_recent_messages", 2, 40),
                ("summary_max_tokens", 256, 4000),
                ("max_tool_output_chars", 500, 20000),
            ):
                _validate_middleware_number(
                    issues,
                    node.id,
                    config,
                    name,
                    minimum,
                    maximum,
                    integer=True,
                )
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "trigger_ratio",
                0.5,
                0.95,
                integer=False,
            )
        if middleware_id == "xpert_file_memory":
            recall_mode = str(config.get("recall_mode") or "hybrid").strip()
            if recall_mode not in {"deterministic", "model", "hybrid"}:
                issues.append(
                    ValidationIssue(
                        code="invalid_xpert_file_memory_recall_mode",
                        message="xpert_file_memory recall_mode must be deterministic, model, or hybrid.",
                        node_id=node.id,
                    )
                )
            for name, minimum, maximum in (
                ("selector_timeout_seconds", 1, 60),
                ("max_selected", 1, 10),
                ("digest_limit", 1, 30),
                ("max_detail_chars_per_turn", 1000, 40000),
                ("max_detail_chars_per_session", 1000, 200000),
                ("max_candidates", 1, 3),
            ):
                _validate_middleware_number(
                    issues,
                    node.id,
                    config,
                    name,
                    minimum,
                    maximum,
                    integer=True,
                )
            try:
                per_turn = int(config.get("max_detail_chars_per_turn") or 20000)
                per_session = int(config.get("max_detail_chars_per_session") or 60000)
                if per_session < per_turn:
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(
                    ValidationIssue(
                        code="invalid_xpert_file_memory_budget",
                        message="xpert_file_memory session budget must be at least the per-turn budget.",
                        node_id=node.id,
                    )
                )
        if middleware_id == "structured_output":
            raw_schema = config.get("schema_json")
            try:
                schema = raw_schema if isinstance(raw_schema, dict) else json.loads(str(raw_schema or ""))
                if not isinstance(schema, dict) or not schema:
                    raise ValueError("schema must be a non-empty object")
                Draft202012Validator.check_schema(schema)
            except (ValueError, TypeError, json.JSONDecodeError, SchemaError) as exc:
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_structured_output_schema",
                        message=f"structured_output schema_json must be a valid JSON Schema: {str(exc)[:200]}",
                        node_id=node.id,
                    )
                )
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "repair_attempts",
                0,
                1,
                integer=True,
            )
        if middleware_id == "todo_planner":
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "max_items",
                1,
                100,
                integer=True,
            )
        if middleware_id == "llm_tool_selector":
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "max_selected_tools",
                1,
                20,
                integer=True,
            )
        if middleware_id == "sandbox_files":
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "quota_mb",
                16,
                1024,
                integer=True,
            )
        if middleware_id == "sandbox_shell":
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "timeout_seconds",
                1,
                300,
                integer=True,
            )
            commands = [
                value.strip()
                for value in re.split(r"[,\n]+", str(config.get("allowed_commands") or ""))
                if value.strip()
            ]
            supported = {"python", "python3", "node", "npm", "npx", "git", "rg"}
            if not commands or any(command not in supported for command in commands):
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_sandbox_commands",
                        message="sandbox_shell allowed_commands must be a non-empty subset of the supported command list.",
                        node_id=node.id,
                    )
                )
        if middleware_id == "skills_runtime":
            skill_ids = [
                value.strip()
                for value in re.split(r"[,\n]+", str(config.get("skill_ids") or ""))
                if value.strip()
            ]
            auto_discover = str(config.get("auto_discover", False)).lower() in {
                "true",
                "1",
                "yes",
            }
            catalog_search = config_truthy(config.get("catalog_search", False))
            if len(skill_ids) > 10 or (
                not auto_discover and not catalog_search and not skill_ids
            ):
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_skills",
                        message=(
                            "skills_runtime needs 1-10 Skill IDs unless auto_discover "
                            "or catalog_search is enabled."
                        ),
                        node_id=node.id,
                    )
                )
        if middleware_id == "browser_automation":
            if str(
                config.get("networkPolicy") or "public_with_domain_approval"
            ) != "public_with_domain_approval":
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_browser_network_policy",
                        message="browser_automation only supports public_with_domain_approval.",
                        node_id=node.id,
                    )
                )
            if str(config.get("approvalMode") or "mutating") != "mutating":
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_browser_approval_mode",
                        message="browser_automation approvalMode must be mutating.",
                        node_id=node.id,
                    )
                )
            for field_name, minimum, maximum in (
                ("maxPages", 1, 3),
                ("maxActions", 1, 100),
                ("navigationTimeoutSeconds", 5, 120),
                ("downloadLimitMb", 1, 50),
            ):
                _validate_middleware_number(
                    issues,
                    node.id,
                    config,
                    field_name,
                    minimum,
                    maximum,
                    integer=True,
                )
            for field_name in ("allowedDomains", "blockedDomains"):
                domains = [
                    value.strip().lower().rstrip(".")
                    for value in re.split(
                        r"[,\n]+", str(config.get(field_name) or "")
                    )
                    if value.strip()
                ]
                invalid = [
                    domain
                    for domain in domains
                    if len(domain) > 253
                    or not re.fullmatch(
                        r"(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                        domain,
                    )
                ]
                if len(domains) > 100 or invalid:
                    issues.append(
                        ValidationIssue(
                            code="invalid_runtime_middleware_browser_domains",
                            message=f"browser_automation {field_name} must contain valid public domain names.",
                            node_id=node.id,
                        )
                    )
        if middleware_id == "human_in_the_loop":
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "timeout_seconds",
                30,
                86400,
                integer=True,
            )
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "max_revision_rounds",
                0,
                5,
                integer=True,
            )
            interrupt_on_tools = str(
                config.get("interrupt_on_tools") or ""
            ).strip()
            final_confirmation_raw = config.get("final_confirmation", False)
            if isinstance(final_confirmation_raw, str):
                final_confirmation = final_confirmation_raw.lower() == "true"
            else:
                final_confirmation = bool(final_confirmation_raw)
            if not interrupt_on_tools and not final_confirmation:
                issues.append(
                    ValidationIssue(
                        code="inactive_runtime_middleware_hitl",
                        message=(
                            "human_in_the_loop must configure interrupt_on_tools "
                            "or enable final_confirmation."
                        ),
                        node_id=node.id,
                    )
                )
        if middleware_id == "scheduler":
            timezone_name = str(config.get("default_timezone") or "UTC").strip()
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_scheduler_timezone",
                        message="scheduler default_timezone must be a valid IANA timezone.",
                        node_id=node.id,
                    )
                )
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "max_runs_per_day",
                1,
                1000,
                integer=True,
            )
        if middleware_id == "ralph_loop":
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "max_iterations",
                1,
                20,
                integer=True,
            )
            _validate_middleware_number(
                issues,
                node.id,
                config,
                "max_output_chars",
                4000,
                200000,
                integer=True,
            )
        if middleware_id == "knowledge_writer":
            if not str(config.get("knowledge_base_id") or "").strip():
                issues.append(
                    ValidationIssue(
                        code="knowledge_writer_kb_required",
                        message="knowledge_writer requires knowledge_base_id.",
                        node_id=node.id,
                    )
                )
        if middleware_id == "plugin_hooks":
            hook_mode = str(config.get("hook_mode") or "").strip()
            if hook_mode and hook_mode not in {"legacy_argv", "typed_v2"}:
                issues.append(
                    ValidationIssue(
                        code="skill_hook_mode_invalid",
                        message="plugin_hooks hook_mode must be legacy_argv or typed_v2.",
                        node_id=node.id,
                    )
                )
            if hook_mode == "typed_v2" and "fail_closed" in config:
                issues.append(
                    ValidationIssue(
                        code="skill_hook_v2_fail_closed_unsupported",
                        message=(
                            "typed_v2 derives failure policy from each Hook mode; "
                            "remove the legacy fail_closed setting."
                        ),
                        node_id=node.id,
                    )
                )
            if hook_mode != "typed_v2":
                issues.append(
                    ValidationIssue(
                        code="skill_hook_legacy_middleware",
                        message="This Skill Hook middleware uses the legacy argv contract.",
                        severity="warning",
                        node_id=node.id,
                    )
                )
            skill_ids = [
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get("skill_ids") or "")
                )
                if value.strip()
            ]
            if not 1 <= len(skill_ids) <= 10:
                issues.append(
                    ValidationIssue(
                        code="plugin_hooks_skills_required",
                        message="plugin_hooks requires between 1 and 10 installed Skill IDs.",
                        node_id=node.id,
                    )
                )
        if middleware_id == "skill_creator":
            authoring_mode = str(config.get("authoring_mode") or "").strip()
            if authoring_mode and authoring_mode not in {
                "legacy_proposal",
                "creator_handoff",
            }:
                issues.append(
                    ValidationIssue(
                        code="invalid_skill_creator_authoring_mode",
                        message=(
                            "skill_creator authoring_mode must be "
                            "legacy_proposal or creator_handoff."
                        ),
                        node_id=node.id,
                    )
                )
            if authoring_mode != "creator_handoff":
                issues.append(
                    ValidationIssue(
                        code="skill_creator_legacy_middleware",
                        message=(
                            "This Skill Creator middleware uses the legacy proposal path."
                        ),
                        severity="warning",
                        node_id=node.id,
                    )
                )
        if middleware_id in {"xpert_authoring", "skill_creator"}:
            allowed_key = (
                "allowed_xpert_ids"
                if middleware_id == "xpert_authoring"
                else "allowed_draft_ids"
            )
            allowed_ids = [
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get(allowed_key) or "")
                )
                if value.strip()
            ]
            if len(allowed_ids) > 50 or any(len(value) > 200 for value in allowed_ids):
                issues.append(
                    ValidationIssue(
                        code="invalid_authoring_target_scope",
                        message=(
                            f"{middleware_id} {allowed_key} supports at most 50 "
                            "resource IDs of 200 characters each."
                        ),
                        node_id=node.id,
                    )
                )

    return issues


def collect_declared_variables(
    nodes: list[NativeWorkflowNode],
    kinds_by_id: dict[str, str],
) -> set[str]:
    variables: set[str] = set()

    for node in nodes:
        data = node.data
        kind = kinds_by_id[node.id]
        if kind in {"input", "scheduled_start", "http_event_entry", "failure_event_entry", "workflow_call_entry"}:
            field_name = "variableName" if kind == "input" else "eventVariable"
            variable = str(data.get(field_name) or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "http_event_entry":
            body_variable = str(data.get("bodyVariable") or "").strip()
            if is_variable_name(body_variable):
                variables.add(body_variable)
        if kind == "llm":
            variable = str(data.get("outputVariable") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "code":
            variable = str(
                (
                    data.get("outputVariable")
                    if r20_contract_version(data) == 2
                    else data.get("codeOutputVariable")
                )
                or ""
            ).strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "variable_assign":
            variable = str(
                (
                    data.get("outputVariable")
                    if r20_contract_version(data) == 2
                    else data.get("variableName")
                )
                or ""
            ).strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind in {
            "template_transform",
            "variable_aggregator",
            "parameter_extractor",
            "knowledge_retrieval",
            "knowledge_citation",
            "document_extractor",
            "vision_understanding",
            "human_intervention",
            "question_classifier",
            "agent",
            "workflow_agent",
            "agent_task",
            "agent_handoff",
            "handoff_router",
            "mcp_tool",
            "time_tool",
            "http_request",
            "list_operation",
            "data_aggregate",
            "data_merge",
            "dataset_compare",
            "object_transform",
            "file_output",
            "iteration",
            "json_serialize",
            "json_deserialize",
            "data_table_query",
            "data_table_insert",
            "data_table_update",
            "data_table_delete",
            "suspend_wait",
        }:
            variable = str(data.get("outputVariable") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "invoke_workflow":
            result_variable = str(data.get("resultVariable") or "").strip()
            if is_variable_name(result_variable):
                variables.add(result_variable)
        if kind in {"agent_handoff", "handoff_router"} and config_truthy(
            data.get("waitForCompletion")
        ):
            result_variable = str(data.get("resultVariable") or "").strip()
            if is_variable_name(result_variable):
                variables.add(result_variable)

    return variables


def collect_node_variable_producers(
    nodes: list[NativeWorkflowNode],
    kinds_by_id: dict[str, str],
) -> dict[str, list[str]]:
    producers: dict[str, list[str]] = {}
    declaration_fields: dict[str, tuple[str, ...]] = {
        "input": ("variableName",),
        "scheduled_start": ("eventVariable",),
        "http_event_entry": ("eventVariable", "bodyVariable"),
        "failure_event_entry": ("eventVariable",),
        "workflow_call_entry": ("eventVariable",),
        "invoke_workflow": ("resultVariable",),
        "llm": ("outputVariable",),
        "code": ("codeOutputVariable", "outputVariable"),
        "variable_assign": ("variableName", "outputVariable"),
        "template_transform": ("outputVariable",),
        "variable_aggregator": ("outputVariable",),
        "parameter_extractor": ("outputVariable",),
        "knowledge_retrieval": ("outputVariable",),
        "knowledge_citation": ("outputVariable",),
        "document_extractor": ("assetIdVariable", "outputVariable"),
        "vision_understanding": ("assetIdVariable", "outputVariable"),
        "human_intervention": ("outputVariable",),
        "question_classifier": ("outputVariable",),
        "agent": ("outputVariable",),
        "workflow_agent": ("outputVariable",),
        "agent_task": ("outputVariable",),
        "agent_handoff": ("outputVariable", "resultVariable"),
        "handoff_router": ("outputVariable", "resultVariable"),
        "mcp_tool": ("outputVariable",),
        "time_tool": ("outputVariable",),
        "http_request": ("outputVariable",),
        "list_operation": ("outputVariable",),
        "data_aggregate": ("outputVariable",),
        "data_merge": ("outputVariable",),
        "dataset_compare": ("outputVariable",),
        "object_transform": ("outputVariable",),
        "file_output": ("outputVariable",),
        "iteration": ("outputVariable",),
        "json_serialize": ("outputVariable",),
        "json_deserialize": ("outputVariable",),
        "data_table_query": ("outputVariable",),
        "data_table_insert": ("outputVariable",),
        "data_table_update": ("outputVariable",),
        "data_table_delete": ("outputVariable",),
        "suspend_wait": ("outputVariable",),
    }
    for node in nodes:
        for field_name in declaration_fields.get(kinds_by_id[node.id], ()):
            name = str(node.data.get(field_name) or "").strip()
            if not is_variable_name(name):
                continue
            producers.setdefault(name, []).append(node.id)
    return producers


def validate_variable_references(
    node: NativeWorkflowNode,
    kind: str,
    available_variables: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    data = node.data

    if kind in {"suspend_wait", "http_event_reply"}:
        template_field = "untilTemplate" if kind == "suspend_wait" else "bodyTemplate"
        for variable in sorted(extract_template_variables(str(data.get(template_field) or ""))):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_deployment_template_variable",
                        message=f"Deployment node references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "llm":
        prompt = str(data.get("prompt") or "")
        for variable in sorted(extract_template_variables(prompt)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Prompt references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "condition":
        variable = str(
            (
                data.get("inputVariable")
                if str(data.get("contractVersion") or "1") == "2"
                else data.get("conditionVariable")
            )
            or ""
        ).strip()
        if variable and variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_condition_variable_reference",
                    message=f"Condition references undefined variable '{variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "code":
        variable = str(
            (
                data.get("inputVariable")
                if r20_contract_version(data) == 2
                else data.get("codeInputVariable")
            )
            or ""
        ).strip()
        if variable and variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_code_input_variable_reference",
                    message=f"Text processing references undefined variable '{variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "output":
        variable = str(data.get("outputVariable") or "").strip()
        if variable and variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_output_variable_reference",
                    message=f"Output references undefined variable '{variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "runtime_middleware":
        middleware_id = str(data.get("runtimeMiddlewareId") or "").strip()
        if middleware_id == "system_prompt_injector":
            config = data.get("runtimeMiddlewareConfig")
            if isinstance(config, dict):
                system_prompt = str(config.get("system_prompt") or "")
                for variable in sorted(extract_template_variables(system_prompt)):
                    if variable not in available_variables:
                        issues.append(
                            ValidationIssue(
                                code="missing_runtime_middleware_template_variable",
                                message=(
                                    "System prompt middleware references "
                                    f"undefined variable '{variable}'."
                                ),
                                node_id=node.id,
                            )
                        )

    if kind in DATA_TABLE_NODE_KINDS:
        for binding in iter_data_table_bindings(data):
            if str(binding.get("source") or "") != "variable":
                continue
            variable = str(binding.get("variable") or "").strip()
            if variable and variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_data_table_variable_reference",
                        message=f"Agent Table binding references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "invoke_workflow":
        input_bindings = data.get("inputBindings")
        if isinstance(input_bindings, dict):
            for binding in input_bindings.values():
                if not isinstance(binding, dict) or str(binding.get("source") or "") != "variable":
                    continue
                variable = str(binding.get("variable") or "").strip()
                if variable and variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_invoke_workflow_variable_reference",
                            message=f"Workflow call binding references undefined variable '{variable}'.",
                            node_id=node.id,
                        )
                    )

    if kind == "variable_assign":
        if r20_contract_version(data) == 2:
            if str(data.get("valueSource") or "") == "variable":
                variable = str(data.get("sourceVariable") or "").strip()
                if variable and variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_variable_assign_source_reference",
                            message=(
                                "Variable assignment references undefined sourceVariable "
                                f"'{variable}'."
                            ),
                            node_id=node.id,
                        )
                    )
            template = (
                str(data.get("template") or "")
                if str(data.get("valueSource") or "") == "template"
                else ""
            )
        else:
            template = str(data.get("template") or "")
        for variable in sorted(extract_template_variables(template)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Template references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "http_request" and is_http_request_v2(data):
        for variable in sorted(http_request_variable_references(data)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_http_request_variable_reference",
                        message=f"HTTP request references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "http_request" and not is_http_request_v2(data):
        url = str(data.get("url") or "")
        for variable in sorted(extract_template_variables(url)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"HTTP URL references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )
        body_variable = str(data.get("bodyVariable") or "").strip()
        if body_variable and body_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_http_request_body_variable_reference",
                    message=f"HTTP bodyVariable references undefined variable '{body_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "dataset_compare":
        for field_name in ("leftVariable", "rightVariable"):
            variable = str(data.get(field_name) or "").strip()
            if variable and variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_dataset_compare_variable_reference",
                        message=f"Dataset compare references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "template_transform":
        template = str(data.get("template") or "")
        for variable in sorted(extract_template_variables(template)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Template transform references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "variable_aggregator":
        referenced_variables = (
            variable_aggregator_v2_references(data)
            if r20_contract_version(data) == 2
            else set(parse_variable_names(str(data.get("variableNames") or "")))
        )
        for variable in referenced_variables:
            if is_variable_name(variable) and variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_aggregator_variable_reference",
                        message=f"Variable pack references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind in {"json_serialize", "json_deserialize"}:
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code=f"missing_{kind}_input_variable_reference",
                    message=f"{kind} references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "parameter_extractor":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_input_variable_reference",
                    message=f"Parameter extractor references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "knowledge_retrieval":
        query_variable = str(data.get("queryVariable") or "").strip()
        if query_variable and query_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_retrieval_query_variable_reference",
                    message=f"Knowledge retrieval references undefined variable '{query_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "knowledge_citation":
        query_variable = str(data.get("queryVariable") or "").strip()
        if query_variable and query_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_citation_query_variable_reference",
                    message=f"Knowledge citation references undefined variable '{query_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "document_extractor":
        asset_id_variable = str(data.get("assetIdVariable") or "").strip()
        legacy_path_variable = str(data.get("sourcePathVariable") or "").strip()
        # assetIdVariable is an explicit run input supplied by the Workflow file
        # selector. Legacy path variables still have to originate upstream.
        if (
            not asset_id_variable
            and legacy_path_variable
            and legacy_path_variable not in available_variables
        ):
            issues.append(
                ValidationIssue(
                    code="missing_document_extractor_source_path_reference",
                    message=(
                        "Document extractor references undefined variable "
                        f"'{legacy_path_variable}'."
                    ),
                    node_id=node.id,
                )
            )

    if kind == "vision_understanding":
        # assetIdVariable is supplied explicitly by the scoped file selector.
        pass

    if kind == "human_intervention":
        prompt = str(data.get("prompt") or "")
        for variable in sorted(extract_template_variables(prompt)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Human intervention prompt references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "question_classifier":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_question_classifier_input_variable_reference",
                    message=(
                        "Question classifier references undefined inputVariable "
                        f"'{input_variable}'."
                    ),
                    node_id=node.id,
                )
            )
        fallback_prompt = str(data.get("llmFallbackPrompt") or "")
        for variable in sorted(extract_template_variables(fallback_prompt)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=(
                            "Question classifier fallback prompt references "
                            f"undefined variable '{variable}'."
                        ),
                        node_id=node.id,
                    )
                )

    if kind == "agent":
        instruction = str(data.get("instruction") or "")
        for variable in sorted(extract_template_variables(instruction)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Agent instruction references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )
        prompt_suffix = str(data.get("promptSuffix") or "")
        for variable in sorted(extract_template_variables(prompt_suffix)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Agent promptSuffix references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "workflow_agent":
        for field_name in ("rolePrompt", "taskInput"):
            template = str(data.get(field_name) or "")
            for variable in sorted(extract_template_variables(template)):
                if variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_workflow_agent_template_variable",
                            message=(
                                f"Workflow agent {field_name} references undefined "
                                f"variable '{variable}'."
                            ),
                            node_id=node.id,
                        )
                    )

    if kind == "agent_task":
        for field_name in ("taskTitle", "taskInput"):
            template = str(data.get(field_name) or "")
            for variable in sorted(extract_template_variables(template)):
                if variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_agent_task_template_variable",
                            message=(
                                f"Agent task {field_name} references undefined "
                                f"variable '{variable}'."
                            ),
                            node_id=node.id,
                        )
                    )

    if kind == "agent_handoff":
        task_id_variable = str(data.get("taskIdVariable") or "").strip()
        if task_id_variable and task_id_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_agent_handoff_task_id_reference",
                    message=(
                        "Agent handoff taskIdVariable references undefined "
                        f"variable '{task_id_variable}'."
                    ),
                    node_id=node.id,
                )
            )

        reason = str(data.get("reason") or "")
        for variable in sorted(extract_template_variables(reason)):
            if variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_agent_handoff_template_variable",
                        message=(
                            "Agent handoff reason references undefined "
                            f"variable '{variable}'."
                        ),
                        node_id=node.id,
                    )
                )

    if kind == "handoff_router":
        source_variable = str(data.get("sourceVariable") or "").strip()
        if source_variable and source_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_handoff_router_source_variable_reference",
                    message=(
                        "Handoff router sourceVariable references undefined "
                        f"variable '{source_variable}'."
                    ),
                    node_id=node.id,
                )
            )

        for field_name in ("taskTitle", "reasonTemplate"):
            template = str(data.get(field_name) or "")
            for variable in sorted(extract_template_variables(template)):
                if variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_handoff_router_template_variable",
                            message=(
                                f"Handoff router {field_name} references undefined "
                                f"variable '{variable}'."
                            ),
                            node_id=node.id,
                        )
                    )

    if kind == "mcp_tool":
        if r20_contract_version(data) == 2:
            references: set[str] = set()
            if str(data.get("argumentMode") or "") == "object_variable":
                references.add(str(data.get("argumentsVariable") or "").strip())
            else:
                for item in data.get("argumentBindings", []):
                    if not isinstance(item, dict):
                        continue
                    binding = item.get("binding")
                    if (
                        isinstance(binding, dict)
                        and str(binding.get("source") or "") == "variable"
                    ):
                        references.add(str(binding.get("variable") or "").strip())
            for variable in sorted(references - {""}):
                if variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_mcp_tool_variable_reference",
                            message=f"MCP tool references undefined variable '{variable}'.",
                            node_id=node.id,
                        )
                    )
        else:
            arguments_json = str(data.get("argumentsJson") or "")
            for variable in sorted(extract_template_variables(arguments_json)):
                if variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_template_variable",
                            message=(
                                "MCP tool argumentsJson references undefined variable "
                                f"'{variable}'."
                            ),
                            node_id=node.id,
                        )
                    )

    if kind == "multi_route":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_multi_route_input_variable_reference",
                    message=f"Multi route references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "list_operation":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_list_operation_input_variable_reference",
                    message=f"List operation references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "time_tool" and is_time_v2(data):
        try:
            time_references = time_v2_variable_references(data)
        except WorkflowFileDataError:
            time_references = set()
        for variable in sorted(time_references - available_variables):
            issues.append(
                ValidationIssue(
                    code="missing_time_variable_reference",
                    message=f"Time tool references undefined variable '{variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "object_transform":
        try:
            object_references = object_transform_variable_references(data)
        except WorkflowFileDataError:
            object_references = set()
        for variable in sorted(object_references - available_variables):
            issues.append(
                ValidationIssue(
                    code="missing_object_transform_variable_reference",
                    message=f"Object transform references undefined variable '{variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "file_output":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_file_output_input_variable_reference",
                    message=f"File output references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )
        for field_name in ("filenameTemplate", "titleTemplate"):
            for variable in sorted(
                extract_template_variables(str(data.get(field_name) or ""))
            ):
                if variable not in available_variables:
                    issues.append(
                        ValidationIssue(
                            code="missing_file_output_template_variable",
                            message=(
                                f"File output {field_name} references undefined variable "
                                f"'{variable}'."
                            ),
                            node_id=node.id,
                        )
                    )

    if kind == "data_aggregate":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_data_aggregate_input_variable_reference",
                    message=f"Data aggregate references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )

    if kind == "data_merge":
        for field_name in ("leftVariable", "rightVariable"):
            variable = str(data.get(field_name) or "").strip()
            if variable and variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_data_merge_variable_reference",
                        message=f"Data merge references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    if kind == "iteration":
        input_variable = str(data.get("inputVariable") or "").strip()
        if input_variable and input_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_iteration_input_variable_reference",
                    message=f"Iteration references undefined variable '{input_variable}'.",
                    node_id=node.id,
                )
            )
        iteration_variable = str(data.get("iterationVariable") or "").strip()
        template_variables = extract_template_variables(str(data.get("itemTemplate") or ""))
        scoped_variables = set(available_variables)
        if is_variable_name(iteration_variable):
            scoped_variables.add(iteration_variable)
        for variable in sorted(template_variables):
            if variable not in scoped_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_template_variable",
                        message=f"Iteration template references undefined variable '{variable}'.",
                        node_id=node.id,
                    )
                )

    return issues


def validate_edges(
    edges: list[NativeWorkflowEdge],
    node_ids: set[str],
    issues: list[ValidationIssue],
    *,
    nodes_by_id: dict[str, NativeWorkflowNode],
    kinds_by_id: dict[str, str],
) -> list[NativeWorkflowEdge]:
    valid_edges: list[NativeWorkflowEdge] = []
    bindings_by_source: dict[str, list[NativeWorkflowEdge]] = defaultdict(list)
    control_node_ids: set[str] = set()

    for edge in edges:
        source_missing = edge.source not in node_ids
        target_missing = edge.target not in node_ids
        if source_missing or target_missing:
            issues.append(
                ValidationIssue(
                    code="invalid_edge_reference",
                    message="Edge references a missing source or target node.",
                    edge_id=edge.id,
                )
            )
            continue
        if (
            kinds_by_id.get(edge.source) == "annotation"
            or kinds_by_id.get(edge.target) == "annotation"
        ):
            issues.append(
                ValidationIssue(
                    code="annotation_edge_forbidden",
                    message="Annotation nodes cannot connect to workflow edges.",
                    edge_id=edge.id,
                )
            )
            continue
        valid_edges.append(edge)
        source_contract = workflow_node_contract_registry.get(
            kinds_by_id.get(edge.source, "")
        )
        if is_non_control_binding_edge(edge):
            if source_contract is not None and (
                not source_contract.edge.supports("binding")
                or str(edge.sourceHandle or "").strip()
                not in source_contract.edge.allowed_source_handles
                or str(edge.targetHandle or "").strip()
                not in source_contract.edge.allowed_target_handles
            ):
                issues.append(
                    ValidationIssue(
                        code="invalid_node_contract_binding",
                        message=(
                            "Binding edge handles do not match the source node contract."
                        ),
                        edge_id=edge.id,
                    )
                )
            bindings_by_source[edge.source].append(edge)
            source_kind = kinds_by_id.get(edge.source)
            target_kind = kinds_by_id.get(edge.target)
            target_handle = str(edge.targetHandle or "").strip()
            expected_source_kind = {
                "middleware": "runtime_middleware",
                "expert": "external_xpert",
                "knowledge": "knowledge_base",
                "toolset": "toolset_resource",
                "plugin": "plugin_resource",
            }.get(target_handle)
            expected_source_handle = {
                "middleware": "middleware-binding",
                "expert": "expert-binding",
                "knowledge": "knowledge-binding",
                "toolset": "toolset-binding",
                "plugin": "plugin-binding",
            }.get(target_handle)
            if (
                expected_source_kind is None
                or source_kind != expected_source_kind
                or target_kind != "workflow_agent"
                or str(edge.sourceHandle or "").strip() != expected_source_handle
            ):
                issues.append(
                    ValidationIssue(
                        code=f"invalid_{target_handle or 'resource'}_binding",
                        message=(
                            "Resource binding edges must connect the matching resource "
                            "handle to a workflow_agent resource handle."
                        ),
                        edge_id=edge.id,
                    )
                )
        else:
            if (
                source_contract is not None
                and not source_contract.edge.supports("control")
            ):
                issues.append(
                    ValidationIssue(
                        code="node_contract_control_edge_forbidden",
                        message=(
                            "The source node contract does not allow control-flow edges."
                        ),
                        edge_id=edge.id,
                    )
                )
            source_handle = str(edge.sourceHandle or "").strip()
            if (
                source_contract is not None
                and source_handle
                and source_contract.edge.allowed_source_handles
                and source_handle not in source_contract.edge.allowed_source_handles
            ):
                issues.append(
                    ValidationIssue(
                        code="invalid_node_contract_source_handle",
                        message="Edge sourceHandle is not declared by the source node contract.",
                        edge_id=edge.id,
                    )
                )
            control_node_ids.update({edge.source, edge.target})
            if str(edge.sourceHandle or "").strip() in {
                "middleware-binding",
                "expert-binding",
                "knowledge-binding",
                "toolset-binding",
                "plugin-binding",
            }:
                issues.append(
                    ValidationIssue(
                        code="invalid_resource_binding",
                        message=(
                            "A resource binding source handle can only connect to its "
                            "matching workflow_agent resource handle."
                        ),
                        edge_id=edge.id,
                    )
                )

    for source_id, binding_edges in bindings_by_source.items():
        source_kind = kinds_by_id.get(source_id)
        if len(binding_edges) > 1:
            issues.append(
                ValidationIssue(
                    code=(
                        "duplicate_middleware_binding"
                        if source_kind == "runtime_middleware"
                        else "duplicate_resource_binding"
                    ),
                    message=(
                        "A middleware node can bind to only one workflow_agent."
                        if source_kind == "runtime_middleware"
                        else "A resource node can bind to only one workflow_agent."
                    ),
                    node_id=source_id,
                )
            )
        if source_id in control_node_ids:
            issues.append(
                ValidationIssue(
                    code=(
                        "mixed_middleware_binding_and_control_flow"
                        if source_kind == "runtime_middleware"
                        else "mixed_resource_binding_and_control_flow"
                    ),
                    message=(
                        "A bound middleware node cannot also use control-flow edges."
                        if source_kind == "runtime_middleware"
                        else "A bound resource node cannot also use control-flow edges."
                    ),
                    node_id=source_id,
                )
            )

    for node_id, kind in kinds_by_id.items():
        if kind not in {
            "external_xpert",
            "knowledge_base",
            "toolset_resource",
            "plugin_resource",
        }:
            continue
        if node_id not in bindings_by_source:
            issues.append(
                ValidationIssue(
                    code="missing_resource_binding",
                    message=(
                        "External Xpert, Knowledge Base, Toolset, and Plugin nodes must bind "
                        "to exactly one workflow_agent."
                    ),
                    node_id=node_id,
                )
            )
        if node_id in control_node_ids:
            issues.append(
                ValidationIssue(
                    code="resource_node_in_control_flow",
                    message="Resource nodes cannot participate in workflow control flow.",
                    node_id=node_id,
                )
            )

    expert_tool_names_by_agent: dict[str, set[str]] = defaultdict(set)
    for edge in valid_edges:
        if str(edge.targetHandle or "").strip() != "expert":
            continue
        source = nodes_by_id.get(edge.source)
        if source is None:
            continue
        tool_name = str(source.data.get("toolName") or "").strip()
        if tool_name in expert_tool_names_by_agent[edge.target]:
            issues.append(
                ValidationIssue(
                    code="duplicate_external_xpert_tool_name",
                    message=(
                        "External Xpert toolName values must be unique for each workflow_agent."
                    ),
                    node_id=source.id,
                )
            )
        expert_tool_names_by_agent[edge.target].add(tool_name)

    for edge in valid_edges:
        if str(edge.targetHandle or "").strip() not in {
            "expert",
            "knowledge",
            "toolset",
            "plugin",
        }:
            continue
        target = nodes_by_id.get(edge.target)
        if (
            target is not None
            and str(target.data.get("toolMode") or "none") != "mcp_tools"
        ):
            issues.append(
                ValidationIssue(
                    code="resource_binding_requires_runtime_tool_mode",
                    message=(
                        "Bound External Xpert, Knowledge, Toolset, and Plugin resources require "
                        "workflow_agent toolMode=mcp_tools."
                    ),
                    node_id=target.id,
                    edge_id=edge.id,
                )
            )

    return valid_edges


def validate_sandbox_middleware_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    """Require an explicit HITL gate for bound shell execution."""

    nodes_by_id = {node.id: node for node in nodes}
    bound_by_agent: dict[str, list[NativeWorkflowNode]] = defaultdict(list)
    for edge in edges:
        if not is_middleware_binding_edge(edge):
            continue
        source = nodes_by_id.get(edge.source)
        if source is not None and kinds_by_id.get(source.id) == "runtime_middleware":
            bound_by_agent[edge.target].append(source)

    for agent_id, middleware_nodes in bound_by_agent.items():
        shell_nodes = [
            node
            for node in middleware_nodes
            if str(node.data.get("runtimeMiddlewareId") or "") == "sandbox_shell"
            and str(
                (node.data.get("runtimeMiddlewareConfig") or {}).get(
                    "require_approval", True
                )
            ).lower()
            not in {"false", "0", "no"}
        ]
        if not shell_nodes:
            continue
        hitl_tools: set[str] = set()
        for node in middleware_nodes:
            if str(node.data.get("runtimeMiddlewareId") or "") != "human_in_the_loop":
                continue
            config = node.data.get("runtimeMiddlewareConfig") or {}
            hitl_tools.update(
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get("interrupt_on_tools") or "")
                )
                if value.strip()
            )
        if "*" not in hitl_tools and "sandbox_shell" not in hitl_tools:
            for shell_node in shell_nodes:
                issues.append(
                    ValidationIssue(
                        code="sandbox_shell_requires_hitl",
                        message=(
                            "sandbox_shell require_approval needs a human_in_the_loop "
                            "binding that interrupts sandbox_shell or '*'."
                        ),
                        node_id=shell_node.id,
                    )
                )


def validate_skill_runtime_middleware_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    """Require deterministic search and durable approval for catalog installs."""

    nodes_by_id = {node.id: node for node in nodes}
    bound_by_agent: dict[str, list[NativeWorkflowNode]] = defaultdict(list)
    for edge in edges:
        if not is_middleware_binding_edge(edge):
            continue
        source = nodes_by_id.get(edge.source)
        if source is not None and kinds_by_id.get(source.id) == "runtime_middleware":
            bound_by_agent[edge.target].append(source)

    for middleware_nodes in bound_by_agent.values():
        skill_nodes = [
            node
            for node in middleware_nodes
            if str(node.data.get("runtimeMiddlewareId") or "") == "skills_runtime"
        ]
        if not skill_nodes:
            continue
        hitl_tools: set[str] = set()
        for node in middleware_nodes:
            if str(node.data.get("runtimeMiddlewareId") or "") != "human_in_the_loop":
                continue
            config = node.data.get("runtimeMiddlewareConfig") or {}
            hitl_tools.update(
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get("interrupt_on_tools") or "")
                )
                if value.strip()
            )
        for skill_node in skill_nodes:
            config = skill_node.data.get("runtimeMiddlewareConfig") or {}
            catalog_search = config_truthy(config.get("catalog_search", False))
            catalog_install = config_truthy(config.get("catalog_install", False))
            try:
                max_installs = int(config.get("max_catalog_installs", 3))
            except (TypeError, ValueError):
                max_installs = 0
            if not 1 <= max_installs <= 3:
                issues.append(
                    ValidationIssue(
                        code="invalid_skill_catalog_install_limit",
                        message="skills_runtime max_catalog_installs must be between 1 and 3.",
                        node_id=skill_node.id,
                    )
                )
            if catalog_install and not catalog_search:
                issues.append(
                    ValidationIssue(
                        code="skill_catalog_install_requires_search",
                        message="skills_runtime catalog_install requires catalog_search.",
                        node_id=skill_node.id,
                    )
                )
            if (
                catalog_install
                and "*" not in hitl_tools
                and "skill_install" not in hitl_tools
            ):
                issues.append(
                    ValidationIssue(
                        code="skill_catalog_install_requires_hitl",
                        message=(
                            "skills_runtime catalog_install needs a human_in_the_loop "
                            "binding that interrupts skill_install or '*'."
                        ),
                        node_id=skill_node.id,
                    )
                )


def validate_browser_middleware_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    """Require durable HITL coverage for browser mutations."""

    nodes_by_id = {node.id: node for node in nodes}
    bound_by_agent: dict[str, list[NativeWorkflowNode]] = defaultdict(list)
    for edge in edges:
        if not is_middleware_binding_edge(edge):
            continue
        source = nodes_by_id.get(edge.source)
        if source is not None and kinds_by_id.get(source.id) == "runtime_middleware":
            bound_by_agent[edge.target].append(source)

    required = {
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_press",
        "browser_upload_file",
        "browser_download",
    }
    for agent_id, middleware_nodes in bound_by_agent.items():
        browser_nodes = [
            node
            for node in middleware_nodes
            if str(node.data.get("runtimeMiddlewareId") or "")
            == "browser_automation"
        ]
        if not browser_nodes:
            continue
        agent_node = nodes_by_id.get(agent_id)
        if (
            agent_node is not None
            and str(agent_node.data.get("toolMode") or "none") != "mcp_tools"
        ):
            for browser_node in browser_nodes:
                issues.append(
                    ValidationIssue(
                        code="browser_automation_requires_runtime_tool_mode",
                        message=(
                            "browser_automation requires its workflow_agent to use "
                            "toolMode=mcp_tools."
                        ),
                        node_id=browser_node.id,
                    )
                )
        hitl_tools: set[str] = set()
        for node in middleware_nodes:
            if str(node.data.get("runtimeMiddlewareId") or "") != "human_in_the_loop":
                continue
            config = node.data.get("runtimeMiddlewareConfig") or {}
            hitl_tools.update(
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get("interrupt_on_tools") or "")
                )
                if value.strip()
            )
        if "*" in hitl_tools or required.issubset(hitl_tools):
            continue
        for browser_node in browser_nodes:
            issues.append(
                ValidationIssue(
                    code="browser_automation_requires_hitl",
                    message=(
                        "browser_automation needs human_in_the_loop coverage for "
                        "click, fill, select, press, upload, and download tools."
                    ),
                    node_id=browser_node.id,
                )
            )


def validate_client_tool_middleware_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    """Require an explicit host, runtime tool mode, and HITL for mutations."""

    nodes_by_id = {node.id: node for node in nodes}
    bound_by_agent: dict[str, list[NativeWorkflowNode]] = defaultdict(list)
    for edge in edges:
        if not is_middleware_binding_edge(edge):
            continue
        source = nodes_by_id.get(edge.source)
        if source is not None and kinds_by_id.get(source.id) == "runtime_middleware":
            bound_by_agent[edge.target].append(source)

    mutating = {
        "host_page_click",
        "host_page_fill",
        "host_page_select",
        "host_page_press",
        "host_page_navigate",
    }
    for agent_id, middleware_nodes in bound_by_agent.items():
        client_nodes = [
            node
            for node in middleware_nodes
            if str(node.data.get("runtimeMiddlewareId") or "") == "client_tools"
        ]
        if not client_nodes:
            continue
        agent_node = nodes_by_id.get(agent_id)
        if (
            agent_node is not None
            and str(agent_node.data.get("toolMode") or "none") != "mcp_tools"
        ):
            for client_node in client_nodes:
                issues.append(
                    ValidationIssue(
                        code="client_tools_requires_runtime_tool_mode",
                        message=(
                            "client_tools requires its workflow_agent to use "
                            "toolMode=mcp_tools."
                        ),
                        node_id=client_node.id,
                    )
                )
        hitl_tools: set[str] = set()
        for node in middleware_nodes:
            if str(node.data.get("runtimeMiddlewareId") or "") != "human_in_the_loop":
                continue
            config = node.data.get("runtimeMiddlewareConfig") or {}
            hitl_tools.update(
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get("interrupt_on_tools") or "")
                )
                if value.strip()
            )
        for client_node in client_nodes:
            config = client_node.data.get("runtimeMiddlewareConfig") or {}
            if not str(config.get("clientHostId") or "").strip():
                issues.append(
                    ValidationIssue(
                        code="client_tools_host_required",
                        message="client_tools requires clientHostId.",
                        node_id=client_node.id,
                    )
                )
            names = {
                value.strip()
                for value in re.split(
                    r"[,\n]+", str(config.get("clientToolNames") or "")
                )
                if value.strip()
            }
            if not names:
                issues.append(
                    ValidationIssue(
                        code="client_tools_names_required",
                        message="client_tools requires at least one client tool name.",
                        node_id=client_node.id,
                    )
                )
            try:
                timeout = int(config.get("clientToolTimeoutSeconds", 1800))
            except (TypeError, ValueError):
                timeout = 0
            if not 30 <= timeout <= 86400:
                issues.append(
                    ValidationIssue(
                        code="client_tools_timeout_invalid",
                        message="clientToolTimeoutSeconds must be between 30 and 86400.",
                        node_id=client_node.id,
                    )
                )
            required = names & mutating
            if required and "*" not in hitl_tools and not required.issubset(hitl_tools):
                issues.append(
                    ValidationIssue(
                        code="client_tools_requires_hitl",
                        message=(
                            "Mutating client tools require human_in_the_loop "
                            "coverage for every configured mutation."
                        ),
                        node_id=client_node.id,
                    )
                )


def validate_office_middleware_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    """Require a bound Office host, Runtime tool mode, and HITL for mutations."""

    nodes_by_id = {node.id: node for node in nodes}
    bound_by_agent: dict[str, list[NativeWorkflowNode]] = defaultdict(list)
    for edge in edges:
        if not is_middleware_binding_edge(edge):
            continue
        source = nodes_by_id.get(edge.source)
        if source is not None and kinds_by_id.get(source.id) == "runtime_middleware":
            bound_by_agent[edge.target].append(source)

    mutating = {
        "office_powerpoint_add_slide",
        "office_powerpoint_delete_slide",
        "office_powerpoint_add_text_box",
        "office_powerpoint_add_shape",
        "office_powerpoint_update_shape",
        "office_powerpoint_delete_shape",
        "office_powerpoint_insert_image",
        "office_word_insert_text",
        "office_word_replace_selection",
        "office_word_insert_heading",
        "office_word_insert_table",
        "office_excel_set_range_values",
        "office_excel_add_worksheet",
        "office_excel_delete_worksheet",
        "office_excel_autofit_range",
        "office_excel_add_table",
    }
    for agent_id, middleware_nodes in bound_by_agent.items():
        agent = nodes_by_id.get(agent_id)
        office_nodes = [
            node
            for node in middleware_nodes
            if str(node.data.get("runtimeMiddlewareId") or "")
            == "office_automation"
        ]
        if not office_nodes:
            continue
        hitl_tools: set[str] = set()
        for node in middleware_nodes:
            if str(node.data.get("runtimeMiddlewareId") or "") != "human_in_the_loop":
                continue
            config = node.data.get("runtimeMiddlewareConfig") or {}
            hitl_tools.update(
                item.strip()
                for item in re.split(
                    r"[,\n]", str(config.get("interrupt_on_tools") or "")
                )
                if item.strip()
            )
        for office_node in office_nodes:
            config = office_node.data.get("runtimeMiddlewareConfig") or {}
            if str((agent.data if agent else {}).get("toolMode") or "none") != "mcp_tools":
                issues.append(
                    ValidationIssue(
                        code="office_automation_requires_runtime_tool_mode",
                        message="office_automation requires workflow_agent toolMode=mcp_tools.",
                        node_id=office_node.id,
                    )
                )
            if not str(config.get("clientHostId") or "").strip():
                issues.append(
                    ValidationIssue(
                        code="office_automation_host_required",
                        message="office_automation requires clientHostId.",
                        node_id=office_node.id,
                    )
                )
            host_scope = str(config.get("host") or "all").strip().lower()
            if host_scope not in {"all", "word", "excel", "powerpoint"}:
                issues.append(
                    ValidationIssue(
                        code="office_automation_host_invalid",
                        message="office_automation host must be word, excel, powerpoint, or all.",
                        node_id=office_node.id,
                    )
                )
                host_scope = "all"
            try:
                timeout = int(config.get("timeoutSeconds", 1800))
            except (TypeError, ValueError):
                timeout = 0
            if not 30 <= timeout <= 86400:
                issues.append(
                    ValidationIssue(
                        code="office_automation_timeout_invalid",
                        message="office_automation timeoutSeconds must be between 30 and 86400.",
                        node_id=office_node.id,
                    )
                )
            required = {
                name
                for name in mutating
                if host_scope == "all" or name.startswith(f"office_{host_scope}_")
            }
            if not config_truthy(config.get("allowDeletes")):
                required = {name for name in required if "_delete_" not in name}
            if not config_truthy(config.get("allowImageInsert")):
                required.discard("office_powerpoint_insert_image")
            if "*" not in hitl_tools and not required.issubset(hitl_tools):
                issues.append(
                    ValidationIssue(
                        code="office_automation_requires_hitl",
                        message=(
                            "Every enabled mutating Office tool requires "
                            "human_in_the_loop coverage."
                        ),
                        node_id=office_node.id,
                    )
                )


def validate_automation_middleware_bindings(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
    *,
    kinds_by_id: dict[str, str],
) -> None:
    """Validate tool mode and execution constraints for automation middleware."""

    nodes_by_id = {node.id: node for node in nodes}
    bound_by_agent: dict[str, list[NativeWorkflowNode]] = defaultdict(list)
    for edge in edges:
        if not is_middleware_binding_edge(edge):
            continue
        source = nodes_by_id.get(edge.source)
        if source is not None and kinds_by_id.get(source.id) == "runtime_middleware":
            bound_by_agent[edge.target].append(source)

    creator_handoffs: list[str] = []
    for middleware_nodes in bound_by_agent.values():
        for middleware_node in middleware_nodes:
            if (
                str(middleware_node.data.get("runtimeMiddlewareId") or "")
                != "skill_creator"
            ):
                continue
            raw_config = middleware_node.data.get("runtimeMiddlewareConfig")
            config = raw_config if isinstance(raw_config, dict) else {}
            if str(config.get("authoring_mode") or "").strip() == "creator_handoff":
                creator_handoffs.append(middleware_node.id)
    if len(creator_handoffs) > 1:
        issues.append(
            ValidationIssue(
                code="skill_creator_multiple_handoffs",
                message="A workflow can bind at most one Creator V2 handoff.",
                node_id=sorted(creator_handoffs)[1],
            )
        )

    for agent_id, middleware_nodes in bound_by_agent.items():
        agent = nodes_by_id.get(agent_id)
        tool_mode = str((agent.data if agent else {}).get("toolMode") or "none")
        for middleware_node in middleware_nodes:
            middleware_id = str(
                middleware_node.data.get("runtimeMiddlewareId") or ""
            )
            raw_config = middleware_node.data.get("runtimeMiddlewareConfig")
            config = raw_config if isinstance(raw_config, dict) else {}
            if middleware_id == "scheduler" and tool_mode != "mcp_tools":
                issues.append(
                    ValidationIssue(
                        code="scheduler_requires_runtime_tool_mode",
                        message="scheduler requires workflow_agent toolMode=mcp_tools.",
                        node_id=middleware_node.id,
                    )
                )
            creator_handoff = bool(
                middleware_id == "skill_creator"
                and str(config.get("authoring_mode") or "").strip()
                == "creator_handoff"
            )
            if (
                middleware_id in {"xpert_authoring", "skill_creator"}
                and not creator_handoff
                and tool_mode != "mcp_tools"
            ):
                issues.append(
                    ValidationIssue(
                        code="authoring_requires_runtime_tool_mode",
                        message=(
                            f"{middleware_id} requires workflow_agent "
                            "toolMode=mcp_tools."
                        ),
                        node_id=middleware_node.id,
                    )
                )
            if middleware_id == "datax_indicators":
                if tool_mode != "mcp_tools":
                    issues.append(
                        ValidationIssue(
                            code="datax_indicators_requires_runtime_tool_mode",
                            message=(
                                "datax_indicators requires workflow_agent "
                                "toolMode=mcp_tools."
                            ),
                            node_id=middleware_node.id,
                        )
                    )
                def scoped_ids(value: Any) -> list[str]:
                    values = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
                    return list(
                        dict.fromkeys(str(item).strip() for item in values if str(item).strip())
                    )

                project_ids = scoped_ids(config.get("projectIds"))
                model_ids = scoped_ids(config.get("modelIds"))
                if not 1 <= len(project_ids) <= 10:
                    issues.append(
                        ValidationIssue(
                            code="datax_indicators_projects_required",
                            message="datax_indicators requires between 1 and 10 project IDs.",
                            node_id=middleware_node.id,
                        )
                    )
                if not 1 <= len(model_ids) <= 20:
                    issues.append(
                        ValidationIssue(
                            code="datax_indicators_models_required",
                            message="datax_indicators requires between 1 and 20 model IDs.",
                            node_id=middleware_node.id,
                        )
                    )
                try:
                    max_rows = int(config.get("maxResultRows", 100))
                except (TypeError, ValueError):
                    max_rows = 0
                if not 1 <= max_rows <= 500:
                    issues.append(
                        ValidationIssue(
                            code="datax_indicators_max_rows_invalid",
                            message="datax_indicators maxResultRows must be between 1 and 500.",
                            node_id=middleware_node.id,
                        )
                    )
            if (
                middleware_id == "knowledge_writer"
                and not config_truthy(config.get("auto_propose_verified_output"))
                and tool_mode != "mcp_tools"
            ):
                issues.append(
                    ValidationIssue(
                        code="knowledge_writer_requires_runtime_tool_mode",
                        message=(
                            "knowledge_writer requires workflow_agent toolMode=mcp_tools "
                            "unless automatic proposal is enabled."
                        ),
                        node_id=middleware_node.id,
                    )
                )


def topological_order(
    nodes: list[NativeWorkflowNode],
    edges: list[NativeWorkflowEdge],
    issues: list[ValidationIssue],
) -> list[str]:
    bound_resource_ids = {
        edge.source for edge in edges if is_non_control_binding_edge(edge)
    }
    node_ids = {
        node.id
        for node in nodes
        if node.id not in bound_resource_ids and node_kind(node) != "annotation"
    }
    indegree = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            continue
        if is_non_control_binding_edge(edge):
            continue
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1

    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    order: list[str] = []

    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for target_id in outgoing[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                queue.append(target_id)

    if len(order) != len(node_ids):
        issues.append(
            ValidationIssue(
                code="cycle_detected",
                message="Workflow graph contains a cycle.",
            )
        )
        return []

    return order


def is_middleware_binding_edge(edge: NativeWorkflowEdge) -> bool:
    return str(edge.targetHandle or "").strip() == "middleware"


def is_resource_binding_edge(edge: NativeWorkflowEdge) -> bool:
    return str(edge.targetHandle or "").strip() in {
        "expert",
        "knowledge",
        "toolset",
        "plugin",
    }


def is_non_control_binding_edge(edge: NativeWorkflowEdge) -> bool:
    return is_middleware_binding_edge(edge) or is_resource_binding_edge(edge)


def _validate_middleware_number(
    issues: list[ValidationIssue],
    node_id: str,
    config: dict,
    name: str,
    minimum: float,
    maximum: float,
    *,
    integer: bool,
) -> None:
    if name not in config or config.get(name) in {None, ""}:
        return
    try:
        value = int(config[name]) if integer else float(config[name])
    except (TypeError, ValueError):
        value = minimum - 1
    if not minimum <= value <= maximum:
        number_type = "integer" if integer else "number"
        issues.append(
            ValidationIssue(
                code="invalid_runtime_middleware_config",
                message=(
                    f"runtime_middleware {name} must be a {number_type} "
                    f"from {minimum} to {maximum}."
                ),
                node_id=node_id,
            )
        )


def extract_template_variables(template: str) -> set[str]:
    """Return variables referenced through the classic {{ variable }} syntax."""

    try:
        formatter_variables = {
            field_name
            for _, field_name, _, _ in Formatter().parse(template)
            if field_name and is_variable_name(field_name)
        }
    except ValueError:
        formatter_variables = set()
    moustache_variables = {
        match.group(1).strip() for match in TEMPLATE_PATTERN.finditer(template)
    }
    return formatter_variables | moustache_variables


def is_variable_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def parse_variable_names(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
