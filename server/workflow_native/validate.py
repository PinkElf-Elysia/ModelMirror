from __future__ import annotations

import re
import json
from collections import defaultdict, deque
from string import Formatter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

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
    "list_operation": "list_operation",
    "list-operation": "list_operation",
    "list-operator": "list_operation",
    "iteration": "iteration",
    "runtime_middleware": "runtime_middleware",
    "runtime-middleware": "runtime_middleware",
    "end": "output",
    "answer": "output",
    "output": "output",
}

TEMPLATE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SUPPORTED_NODE_KINDS = {
    "input",
    "llm",
    "condition",
    "code",
    "variable_assign",
    "template_transform",
    "variable_aggregator",
    "parameter_extractor",
    "knowledge_retrieval",
    "knowledge_citation",
    "document_extractor",
    "human_intervention",
    "question_classifier",
    "agent",
    "workflow_agent",
    "external_xpert",
    "knowledge_base",
    "toolset_resource",
    "plugin_resource",
    "agent_task",
    "agent_handoff",
    "handoff_router",
    "mcp_tool",
    "time_tool",
    "http_request",
    "list_operation",
    "iteration",
    "runtime_middleware",
    "output",
}


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

    kinds_by_id = {node.id: node_kind(node) for node in workflow.nodes}
    for node in workflow.nodes:
        kind = kinds_by_id[node.id]
        if kind not in SUPPORTED_NODE_KINDS:
            issues.append(
                ValidationIssue(
                    code="unknown_node_kind",
                    message=f"Node '{node.id}' has an unsupported kind.",
                    node_id=node.id,
                )
            )

    if not any(kind == "input" for kind in kinds_by_id.values()):
        issues.append(
            ValidationIssue(
                code="missing_input_node",
                message="Workflow needs at least one input/start node.",
            )
        )

    if not any(kind == "output" for kind in kinds_by_id.values()):
        issues.append(
            ValidationIssue(
                code="missing_output_node",
                message="Workflow needs at least one output/end node.",
            )
        )

    for node in workflow.nodes:
        issues.extend(validate_node_configuration(node, kinds_by_id[node.id]))

    available_variables = collect_declared_variables(workflow.nodes, kinds_by_id)
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
    validate_sandbox_middleware_bindings(
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

    if kind == "llm":
        if not str(data.get("modelId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_llm_model",
                    message="LLM node needs data.modelId.",
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
        if not str(data.get("outputVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_llm_output_variable",
                    message="LLM node needs data.outputVariable.",
                    node_id=node.id,
                )
            )

    if kind == "condition":
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

    if kind == "parameter_extractor":
        if not str(data.get("inputVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_input_variable",
                    message="Parameter extractor needs data.inputVariable.",
                    node_id=node.id,
                )
            )
        if not str(data.get("schema") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_schema",
                    message="Parameter extractor needs data.schema.",
                    node_id=node.id,
                )
            )
        if not str(data.get("modelId") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_model_id",
                    message="Parameter extractor needs data.modelId.",
                    node_id=node.id,
                )
            )
        output_variable = str(data.get("outputVariable") or "").strip()
        if not output_variable:
            issues.append(
                ValidationIssue(
                    code="missing_parameter_extractor_output_variable",
                    message="Parameter extractor needs data.outputVariable.",
                    node_id=node.id,
                )
            )
        elif not is_variable_name(output_variable):
            issues.append(
                ValidationIssue(
                    code="invalid_parameter_extractor_output_variable",
                    message="Parameter extractor outputVariable must be an identifier.",
                    node_id=node.id,
                )
            )

    if kind == "knowledge_retrieval":
        if not str(data.get("queryVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_knowledge_retrieval_query_variable",
                    message="Knowledge retrieval node needs data.queryVariable.",
                    node_id=node.id,
                )
            )
        top_k = str(data.get("top_k") or "3").strip()
        try:
            top_k_int = int(top_k)
        except ValueError:
            top_k_int = 0
        if top_k_int < 1 or top_k_int > 20:
            issues.append(
                ValidationIssue(
                    code="invalid_knowledge_retrieval_top_k",
                    message="Knowledge retrieval top_k must be an integer between 1 and 20.",
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
        if not str(data.get("sourcePathVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_document_extractor_source_path",
                    message="Document extractor needs data.sourcePathVariable.",
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

    if kind == "human_intervention":
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

    if kind == "http_request":
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

    if kind == "list_operation":
        if not str(data.get("inputVariable") or "").strip():
            issues.append(
                ValidationIssue(
                    code="missing_list_operation_input_variable",
                    message="List operation node needs data.inputVariable.",
                    node_id=node.id,
                )
            )
        operator = str(data.get("operator") or "").strip()
        if operator not in {"length", "join", "first", "last"}:
            issues.append(
                ValidationIssue(
                    code="invalid_list_operation_operator",
                    message="List operation operator must be length, join, first, or last.",
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
            if len(skill_ids) > 10 or (not auto_discover and not skill_ids):
                issues.append(
                    ValidationIssue(
                        code="invalid_runtime_middleware_skills",
                        message="skills_runtime needs 1-10 Skill IDs unless auto_discover is enabled.",
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
        if kind == "input":
            variable = str(data.get("variableName") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "llm":
            variable = str(data.get("outputVariable") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "code":
            variable = str(data.get("codeOutputVariable") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind == "variable_assign":
            variable = str(data.get("variableName") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind in {
            "template_transform",
            "variable_aggregator",
            "parameter_extractor",
            "knowledge_retrieval",
            "knowledge_citation",
            "document_extractor",
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
            "iteration",
        }:
            variable = str(data.get("outputVariable") or "").strip()
            if is_variable_name(variable):
                variables.add(variable)
        if kind in {"agent_handoff", "handoff_router"} and config_truthy(
            data.get("waitForCompletion")
        ):
            result_variable = str(data.get("resultVariable") or "").strip()
            if is_variable_name(result_variable):
                variables.add(result_variable)

    return variables


def validate_variable_references(
    node: NativeWorkflowNode,
    kind: str,
    available_variables: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    data = node.data

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
        variable = str(data.get("conditionVariable") or "").strip()
        if variable and variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_condition_variable_reference",
                    message=f"Condition references undefined variable '{variable}'.",
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

    if kind == "variable_assign":
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

    if kind == "http_request":
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
        for variable in parse_variable_names(str(data.get("variableNames") or "")):
            if is_variable_name(variable) and variable not in available_variables:
                issues.append(
                    ValidationIssue(
                        code="missing_aggregator_variable_reference",
                        message=f"Variable aggregator references undefined variable '{variable}'.",
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
        source_path_variable = str(data.get("sourcePathVariable") or "").strip()
        if source_path_variable and source_path_variable not in available_variables:
            issues.append(
                ValidationIssue(
                    code="missing_document_extractor_source_path_reference",
                    message=f"Document extractor references undefined variable '{source_path_variable}'.",
                    node_id=node.id,
                )
            )

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
        valid_edges.append(edge)
        if is_non_control_binding_edge(edge):
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

    for agent_id, middleware_nodes in bound_by_agent.items():
        agent = nodes_by_id.get(agent_id)
        tool_mode = str((agent.data if agent else {}).get("toolMode") or "none")
        for middleware_node in middleware_nodes:
            middleware_id = str(
                middleware_node.data.get("runtimeMiddlewareId") or ""
            )
            config = middleware_node.data.get("runtimeMiddlewareConfig") or {}
            if middleware_id == "scheduler" and tool_mode != "mcp_tools":
                issues.append(
                    ValidationIssue(
                        code="scheduler_requires_runtime_tool_mode",
                        message="scheduler requires workflow_agent toolMode=mcp_tools.",
                        node_id=middleware_node.id,
                    )
                )
            if (
                middleware_id in {"xpert_authoring", "skill_creator"}
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
        node.id for node in nodes if node.id not in bound_resource_ids
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
