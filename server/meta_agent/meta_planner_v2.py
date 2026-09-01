from __future__ import annotations

import json
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

try:
    from server.prompts.models import PromptProfileBinding
    from server.workflow_native.schemas import (
        NativeWorkflowDefinition,
        NativeWorkflowEdge,
        NativeWorkflowNode,
        WorkflowPosition,
    )
    from server.workflow_native.node_contracts import (
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from server.workflow_native.validate import node_kind, validate_workflow_graph
    from server.xpert_runtime.authoring_service import AuthoringService
    from server.xpert_runtime.authoring_store import AuthoringProposal
    from server.xperts.models import XpertDefinition, XpertDraft
except ModuleNotFoundError:
    from prompts.models import PromptProfileBinding
    from workflow_native.schemas import (
        NativeWorkflowDefinition,
        NativeWorkflowEdge,
        NativeWorkflowNode,
        WorkflowPosition,
    )
    from workflow_native.node_contracts import (
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from workflow_native.validate import node_kind, validate_workflow_graph
    from xpert_runtime.authoring_service import AuthoringService
    from xpert_runtime.authoring_store import AuthoringProposal
    from xperts.models import XpertDefinition, XpertDraft

from .capabilities import assert_scope_is_authorized
from .graph_ir_v3 import (
    GRAPH_IR_VERSION,
    annotate_candidate_with_graph_ir,
    graph_authoring_checksum,
    graph_intent_to_v2,
    resolve_middleware_config,
    resolve_graph_intent,
    v2_to_graph_intent,
    workflow_authoring_checksum,
    workflow_semantic_checksum,
)
from .graph_patch import GraphPatchEnvelopeV1, apply_graph_patch
from .node_adapters import (
    META_PLANNER_BINDING_KINDS,
    META_PLANNER_COMPILER_MANAGED_KINDS,
    META_PLANNER_COMPILABLE_NODE_KINDS,
    META_PLANNER_IR_VERSION,
    PlannerNodeCompileContext,
    get_planner_node_adapter,
)
from .planner import extract_json_object_text
from .schemas import (
    MetaPlannerAgentBlueprint,
    MetaPlannerBlueprint,
    MetaPlannerCapabilitySnapshot,
    MetaPlannerGenerateRequest,
    MetaPlannerGenerateResponse,
    GraphIntentV3,
    MetaPlannerIRControlEdge,
    MetaPlannerIRFinalOutput,
    MetaPlannerIRInputBinding,
    MetaPlannerIRMiddlewareBinding,
    MetaPlannerIRNode,
    MetaPlannerIROutputBinding,
    MetaPlannerIRResourceBinding,
    MetaPlannerPreviewResponse,
    MetaPlannerIRCompatibility,
    ResolvedGraphIRV3,
    MetaPlannerTaskPlan,
    MetaPlannerTypedBlueprintV2,
    MetaPlannerWorkflowAgentConfig,
)


CompletionCallback = Callable[[str, str, str, float, int], Awaitable[str]]
PreflightCallback = Callable[[XpertDefinition], Any]


TASK_PLAN_SYSTEM_PROMPT = """\
You are the task-planning stage of ModelMirror Meta Planner Graph IR V3.
Return one strict JSON object only. Do not include markdown or hidden reasoning.
Create a bounded task DAG. Every task must have a stable lowercase task_id,
explicit dependencies, an input contract, and one output contract.
Tasks represent model-driven responsibilities. Deterministic helper-node operations
must not become tasks; obey the supplied task_planning_contract.
"""


BLUEPRINT_SYSTEM_PROMPT = """\
You are the capability-compilation stage of ModelMirror Meta Planner Graph IR V3.
Return one strict JSON object only. Do not include markdown or hidden reasoning.
Return GraphIntentV3 with ir_version=3. The nodes array contains executable nodes
only; compiler-managed input/output and resource-binding nodes are never emitted.
Use only IDs and middleware listed in the authorized capability snapshot.
Compile the task DAG into explicit typed IR nodes, control edges, resource bindings,
middleware bindings, and one explicit final output. A node may cover multiple tasks
and every task must be covered by a workflow_agent. Auxiliary pure nodes must have an
empty task_ids list. Bindings must target a workflow_agent node ref.
Every node input must identify its source node and source port. Use the workflow_agent
task port for each task input; that port accepts multiple typed variables.
Respect the supplied typed_ir_constraints, including its workflow-agent node limit.
Never invent credentials, tools, resource IDs, node kinds, versions, or private content.
"""


REPAIR_SYSTEM_PROMPT = """\
You repair a ModelMirror Meta Planner GraphIntentV3. Return one strict JSON object
only with ir_version=3. Never return legacy V2 IR. The nodes array contains only
executable nodes; input/output and resource nodes are compiler-managed.
Make the smallest changes required by the structured validation issues. Do not add
capabilities outside the supplied authorized snapshot. This is the only repair pass.
Return the complete blueprint and obey every supplied typed_ir_constraint.
"""


PATCH_REPAIR_SYSTEM_PROMPT = """\
You repair one parsed ModelMirror GraphIntentV3 using GraphPatchEnvelopeV1.
Return one strict JSON object only. Never return a complete workflow or GraphIntent.
Use only the listed semantic refs, named ports, Adapter config, and authorized IDs.
Do not emit native node IDs, Handles, resource versions, schemas, policies, checksums
other than the exact expected checksums supplied in the envelope. Make the smallest
ordered patch that addresses the validation issues. This is the only repair pass.
The virtual refs input and output may be referenced where allowed but can never be
added, updated, removed, or moved as nodes.
"""


def _json_payload(raw_text: str) -> dict[str, Any]:
    payload = json.loads(extract_json_object_text(raw_text))
    if not isinstance(payload, dict):
        raise ValueError("Meta Planner output must be a JSON object.")
    return payload


def _safe_exception_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        issues = []
        for item in exc.errors(include_input=False, include_url=False)[:20]:
            location = ".".join(str(value) for value in item.get("loc") or [])
            message = str(item.get("msg") or "Invalid value.")
            issues.append(f"{location}: {message}" if location else message)
        return "; ".join(issues)[:2_000]
    if isinstance(exc, json.JSONDecodeError):
        return f"Invalid JSON at line {exc.lineno}, column {exc.colno}."
    return str(exc).replace("\r", " ").replace("\n", " ")[:2_000]


def _safe_identifier(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized or normalized[0].isdigit():
        normalized = f"{fallback}_{normalized}" if normalized else fallback
    return normalized[:120]


def _typed_ir_prompt_constraints(
    request: MetaPlannerGenerateRequest,
    plan: MetaPlannerTaskPlan,
) -> dict[str, Any]:
    children: dict[str, list[str]] = defaultdict(list)
    indegree = {task.task_id: len(task.depends_on) for task in plan.tasks}
    for task in plan.tasks:
        for dependency in task.depends_on:
            children[dependency].append(task.task_id)
    queue = deque(sorted(task_id for task_id, count in indegree.items() if count == 0))
    topological_order: list[str] = []
    while queue:
        current = queue.popleft()
        topological_order.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    suggested_groups: list[list[str]] = []
    can_group_by_order = all(
        not task.agent_id and not task.method_skill_ids for task in plan.tasks
    )
    if can_group_by_order and topological_order:
        group_count = min(request.max_agents, len(topological_order))
        for index in range(group_count):
            start = index * len(topological_order) // group_count
            end = (index + 1) * len(topological_order) // group_count
            suggested_groups.append(topological_order[start:end])

    return {
        "max_workflow_agent_nodes": request.max_agents,
        "required_task_ids": [task.task_id for task in plan.tasks],
        "topological_task_order": topological_order,
        "suggested_task_groups": suggested_groups,
        "task_dependencies": {
            task.task_id: list(task.depends_on) for task in plan.tasks
        },
        "task_agent_bindings": {
            task.task_id: task.agent_id for task in plan.tasks
        },
        "authorized_agent_ids": list(request.scope.agent_ids),
        "workflow_agent_config_allowed_fields": list(
            MetaPlannerWorkflowAgentConfig.model_fields
        ),
        "workflow_agent_config_forbidden_fields": ["agent_id"],
        "rules": [
            "Every required task_id must appear in at least one node.task_ids entry.",
            "When the plan has more tasks than the node limit, group compatible task_ids into shared workflow_agent nodes.",
            "Use suggested_task_groups when present unless a stricter task binding requires separate nodes.",
            "Represent every dependency between tasks assigned to different nodes with a control edge.",
            "Control edges must follow topological_task_order and must never form a cycle.",
            "Node inputs may reference only user_input, conversation_history, or outputs from ancestor nodes.",
            "Set source_agent_id only to the exact non-null task_agent_binding; otherwise omit it.",
            "workflow_agent config accepts only workflow_agent_config_allowed_fields; agent_id belongs to the task plan and must not appear in node.config.",
            "Return a complete typed blueprint, not a patch or partial fragment.",
        ],
    }


def _task_planning_contract(
    request: MetaPlannerGenerateRequest,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> dict[str, Any]:
    allowed_kinds = set(request.scope.allowed_node_kinds)
    auxiliary_node_contracts: list[dict[str, Any]] = []
    for item in snapshot.nodes:
        kind = str(item.get("kind") or "")
        planner = dict(item.get("planner") or {})
        if (
            kind not in allowed_kinds
            or get_planner_node_adapter(kind) is None
            or planner.get("task_binding") != "forbidden"
        ):
            continue
        contracts = dict(item.get("contracts") or {})

        def port_summary(port: Any) -> dict[str, Any]:
            payload = dict(port) if isinstance(port, dict) else {}
            value_schema = dict(payload.get("value_schema") or {})
            return {
                "name": str(payload.get("name") or ""),
                "type": str(value_schema.get("type") or "any"),
                "cardinality": str(payload.get("cardinality") or "one"),
            }

        auxiliary_node_contracts.append(
            {
                "kind": kind,
                "title": str(item.get("title") or kind),
                "purpose": str(item.get("description") or ""),
                "task_binding": "forbidden",
                "inputs": [
                    port_summary(port)
                    for port in list(contracts.get("inputs") or [])
                ],
                "outputs": [
                    port_summary(port)
                    for port in list(contracts.get("outputs") or [])
                ],
            }
        )
    auxiliary_node_contracts.sort(key=lambda item: item["kind"])
    auxiliary_node_kinds = [item["kind"] for item in auxiliary_node_contracts]
    return {
        "expert_task_binding": "required",
        "max_workflow_agent_nodes": request.max_agents,
        "auxiliary_node_kinds": auxiliary_node_kinds,
        "auxiliary_node_contracts": auxiliary_node_contracts,
        "expert_task_test": {
            "question": (
                "After all authorized auxiliary nodes are available, does this "
                "step still require model judgment, interpretation, extraction "
                "from unstructured content, or synthesis?"
            ),
            "required_answer": "yes",
        },
        "non_task_examples": [
            "Parse a JSON string into a typed value.",
            "Serialize a typed value to JSON.",
            "Pack named variables into one object.",
            "Group rows and calculate deterministic measures.",
            "Compare two datasets by stable keys.",
        ],
        "expert_task_examples": [
            "Extract audit controls from unstructured evidence.",
            "Interpret deterministic comparison results and write a professional conclusion.",
        ],
        "rules": [
            "Plan tasks describe model-driven judgment, responsibility, or synthesis that a workflow_agent must perform.",
            "Deterministic operations represented by auxiliary_node_kinds must not become plan tasks; they are compiled later as task-free helper nodes.",
            "Do not create one expert task per requested JSON conversion, packing, aggregation, or dataset comparison step.",
            "Apply expert_task_test to every proposed task and omit the task unless the required answer is yes.",
            "Use auxiliary_node_contracts purpose and ports to recognize deterministic steps before emitting tasks.",
            "Keep distinct expert responsibilities separate, but do not split a single responsibility merely to name its deterministic data transforms.",
        ],
    }


def _graph_patch_repair_contract(
    request: MetaPlannerGenerateRequest,
    blueprint: GraphIntentV3,
    issues: list[str],
) -> dict[str, Any]:
    existing_nodes = sorted(
        (
            {
                "ref": node.ref,
                "kind": node.kind,
                "task_ids": list(node.task_ids),
                "input_sources": sorted(
                    {
                        f"{binding.source_ref}.{binding.source_port}"
                        for binding in node.inputs
                    }
                ),
                "output_ports": sorted(output.port for output in node.outputs),
            }
            for node in blueprint.nodes
        ),
        key=lambda item: item["ref"],
    )
    workflow_agent_count = sum(
        node.kind == "workflow_agent" for node in blueprint.nodes
    )
    agent_overflow = workflow_agent_count > request.max_agents
    issue_playbook: list[str] = []
    if agent_overflow or any("exceeds max_agents" in issue for issue in issues):
        issue_playbook.append(
            "For max_agents overflow, consolidate task_ids into at most "
            f"{request.max_agents} retained workflow_agent nodes, reconnect their "
            "control and data edges, set a retained workflow_agent as final output, "
            "then remove surplus agents; do not add workflow_agent nodes."
        )
    return {
        "compiler_managed_refs": ["input", "output"],
        "existing_node_refs": [item["ref"] for item in existing_nodes],
        "existing_nodes": existing_nodes,
        "max_workflow_agent_nodes": request.max_agents,
        "current_workflow_agent_nodes": workflow_agent_count,
        "allow_add_workflow_agent": workflow_agent_count < request.max_agents,
        "addable_node_kinds": sorted(
            kind
            for kind in set(request.scope.allowed_node_kinds)
            if get_planner_node_adapter(kind) is not None
        ),
        "issue_playbook": issue_playbook,
        "rules": [
            "The refs input and output are compiler-managed virtual refs: never add, update, remove, or move them.",
            "input may only be referenced as an existing source_ref; output is created from set_final_output.",
            "add_node must use a new ref outside compiler_managed_refs and existing_node_refs.",
            "update_node, remove_node, and move_node may target only existing_node_refs.",
            "Pure nodes use empty task_ids; workflow_agent nodes cover all fixed plan tasks.",
            "Do not add a workflow_agent when allow_add_workflow_agent is false.",
            "Adapter-derived output Schemas are recomputed by the server after this repair; never inject or patch output Schemas directly.",
        ],
    }


def _normalize_adapter_outputs_for_repair(
    intent: GraphIntentV3,
) -> tuple[GraphIntentV3, list[str]]:
    """Refresh Adapter-owned outputs and their derived data-edge schemas."""

    normalized_refs: set[str] = set()
    authoritative_outputs: dict[
        tuple[str, str], tuple[str, Any]
    ] = {}
    output_normalized_nodes = []
    for node in intent.nodes:
        adapter = get_planner_node_adapter(node.kind)
        if adapter is None:
            output_normalized_nodes.append(node)
            continue
        parsed = adapter.validate_intent_node(node)
        outputs = []
        for output in node.outputs:
            authoritative = adapter.authoritative_output_schema(output.port, parsed)
            authoritative_outputs[(node.ref, output.port)] = (
                output.variable,
                authoritative,
            )
            if canonical_checksum(
                output.value_schema.model_dump(mode="json")
            ) != canonical_checksum(authoritative.model_dump(mode="json")):
                normalized_refs.add(node.ref)
                output = output.model_copy(
                    update={"value_schema": authoritative}
                )
            outputs.append(output)
        output_normalized_nodes.append(node.model_copy(update={"outputs": outputs}))

    normalized_nodes = []
    for node in output_normalized_nodes:
        inputs = []
        for binding in node.inputs:
            source = authoritative_outputs.get(
                (binding.source_ref, binding.source_port)
            )
            if source is not None and binding.variable == source[0]:
                authoritative = source[1]
                if canonical_checksum(
                    binding.value_schema.model_dump(mode="json")
                ) != canonical_checksum(authoritative.model_dump(mode="json")):
                    normalized_refs.add(binding.source_ref)
                    binding = binding.model_copy(
                        update={"value_schema": authoritative}
                    )
            inputs.append(binding)
        normalized_nodes.append(node.model_copy(update={"inputs": inputs}))
    return (
        intent.model_copy(update={"nodes": normalized_nodes}),
        sorted(normalized_refs),
    )


def _graph_intent_prompt_contract(
    request: MetaPlannerGenerateRequest,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> dict[str, Any]:
    allowed_kinds = set(request.scope.allowed_node_kinds)
    executable_kinds = sorted(
        kind
        for kind in allowed_kinds
        if get_planner_node_adapter(kind) is not None
    )
    compiler_managed_kinds = sorted(
        allowed_kinds & set(META_PLANNER_COMPILER_MANAGED_KINDS)
    )
    binding_kinds = sorted(allowed_kinds & set(META_PLANNER_BINDING_KINDS))
    snapshot_kinds = {
        str(item.get("kind") or "")
        for item in snapshot.nodes
        if isinstance(item, dict)
    }
    node_contracts = {
        str(item.get("kind") or ""): {
            "task_binding": dict(item.get("planner") or {}).get("task_binding"),
            "config_schema": dict(
                (item.get("contract") or {}).get("planner") or {}
            ).get("ir_config_schema", {}),
            "ports": list((item.get("contract") or {}).get("ports") or []),
        }
        for item in snapshot.nodes
        if str(item.get("kind") or "") in executable_kinds
    }

    return {
        "required_ir_version": GRAPH_IR_VERSION,
        "node_roles": {
            "executable_node_kinds": executable_kinds,
            "compiler_managed_node_kinds": compiler_managed_kinds,
            "resource_binding_kinds": binding_kinds,
            "executable_node_contracts": node_contracts,
        },
        "workflow_agent": {
            "config_schema": MetaPlannerWorkflowAgentConfig.model_json_schema(),
            "config_field_names": list(MetaPlannerWorkflowAgentConfig.model_fields),
            "input_port": {
                "name": "task",
                "cardinality": "many",
                "root_source_ref": "input",
                "root_source_port": "user_input",
                "root_variable": "user_input",
            },
            "output_port": {
                "name": "result",
                "type": "string",
                "cardinality": "one",
            },
        },
        "authorized": {
            "middleware_ids": list(request.scope.middleware_ids),
            "prompt_profile_ids": list(request.scope.prompt_profile_ids),
            "external_xpert_ids": list(request.scope.external_xpert_ids),
            "knowledge_base_ids": list(request.scope.knowledge_base_ids),
            "toolset_ids": list(request.scope.toolset_ids),
            "plugin_ids": list(request.scope.plugin_ids),
        },
        "rules": [
            "Set ir_version to 3; never return a V2 blueprint.",
            "nodes may contain only executable_node_kinds.",
            "Never put compiler_managed_node_kinds or resource_binding_kinds in nodes.",
            "Represent resources only in resources and target a workflow_agent ref.",
            "Use snake_case workflow_agent config fields exactly as config_field_names; never emit outputVariable, taskInput, rolePrompt, or agent_id in config.",
            "Every workflow_agent declares exactly one string output on port result with a unique variable.",
            "Every workflow_agent has one or more task_ids; nodes whose task_binding is forbidden have an empty task_ids list.",
            "Pure nodes are deterministic auxiliary transforms only; do not use redundant serialize-deserialize round trips or duplicate aggregates.",
            "Pure node config, named ports, and output Schema must exactly match executable_node_contracts.",
            "Every root workflow_agent binds user_input from source_ref input and source_port user_input to input port task.",
            "Every dependency input binds the exact ancestor result variable from source_port result to input port task.",
            "Every {{variable}} used in role_prompt or task_input has a matching explicit input binding.",
            "Control edges represent every cross-node task dependency and form one acyclic graph with one terminal node.",
            "final_output references the terminal workflow_agent result port and its exact output variable.",
            "Resources and middleware may target workflow_agent nodes only, never pure nodes.",
            "middleware may contain only authorized middleware_ids; when that list is empty, middleware must be empty.",
        ],
        "snapshot_node_kinds": sorted(snapshot_kinds & allowed_kinds),
    }


def _planner_prompt_snapshot(
    request: MetaPlannerGenerateRequest,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> dict[str, Any]:
    def authorized(items: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
        allowed = set(ids)
        return [item for item in items if str(item.get("id") or "") in allowed]

    return {
        "version": snapshot.version,
        "snapshot_hash": snapshot.snapshot_hash,
        "graph_intent_contract": _graph_intent_prompt_contract(request, snapshot),
        "resources": {
            "external_xperts": authorized(
                snapshot.external_xperts, request.scope.external_xpert_ids
            ),
            "knowledge_bases": authorized(
                snapshot.knowledge_bases, request.scope.knowledge_base_ids
            ),
            "toolsets": authorized(snapshot.toolsets, request.scope.toolset_ids),
            "plugins": authorized(snapshot.plugins, request.scope.plugin_ids),
            "prompt_profiles": authorized(
                snapshot.prompt_profiles, request.scope.prompt_profile_ids
            ),
        },
        "middleware": authorized(snapshot.middleware, request.scope.middleware_ids),
        "models": [
            item
            for item in snapshot.models
            if item.get("safe") is True and item.get("id")
        ],
        "agents": authorized(snapshot.agents, request.scope.agent_ids),
    }


def _canonical_graph_intent_example(
    request: MetaPlannerGenerateRequest,
    plan: MetaPlannerTaskPlan,
) -> dict[str, Any]:
    """Give the model one compact, structurally valid V3 shape to adapt."""

    task_ids = [task.task_id for task in plan.tasks]
    return {
        "ir_version": GRAPH_IR_VERSION,
        "name": "Replace with the candidate Xpert name",
        "description": "Replace with a concise candidate description",
        "tags": [],
        "starters": [],
        "nodes": [
            {
                "ref": "xpert_agent",
                "kind": "workflow_agent",
                "title": "Replace with the workflow agent title",
                "description": "Replace with the workflow agent responsibility",
                "task_ids": task_ids,
                "inputs": [
                    {
                        "port": "task",
                        "variable": "user_input",
                        "source_ref": "input",
                        "source_port": "user_input",
                        "value_schema": {"type": "string"},
                    }
                ],
                "outputs": [
                    {
                        "port": "result",
                        "variable": "final_result",
                        "value_schema": {"type": "string"},
                    }
                ],
                "config": {
                    "role_prompt": "Replace with instructions covering task_ids.",
                    "task_input": "{{user_input}}",
                    "model_id": request.default_agent_model_id,
                    "source_agent_id": None,
                    "method_skill_ids": [],
                },
            }
        ],
        "control_edges": [],
        "resources": [],
        "middleware": [],
        "prompt_profile_ids": [],
        "final_output": {
            "node_ref": "xpert_agent",
            "port": "result",
            "variable": "final_result",
        },
    }


def validate_task_plan(
    plan: MetaPlannerTaskPlan,
    *,
    max_agents: int,
    authorized_agent_ids: set[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    interaction_ids = [
        task.task_id for task in plan.tasks if task.task_type != "expert"
    ]
    if interaction_ids:
        issues.append(
            "Generic Meta Planner task plans support expert tasks only; "
            "HITL is scoped to Expert Team: " + ", ".join(interaction_ids) + "."
        )
    task_ids = [task.task_id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        issues.append("Task IDs must be unique.")
    known = set(task_ids)
    assigned_agent_ids = {
        task.agent_id for task in plan.tasks if task.agent_id is not None
    }
    if len(assigned_agent_ids) > max_agents:
        issues.append(
            f"Task plan assigns {len(assigned_agent_ids)} experts; "
            f"max_agents={max_agents}."
        )
    if authorized_agent_ids is not None:
        for task in plan.tasks:
            if task.agent_id and task.agent_id not in authorized_agent_ids:
                issues.append(
                    f"Task {task.task_id} binds unauthorized expert "
                    f"{task.agent_id}."
                )
    graph: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    indegree = {task_id: 0 for task_id in task_ids}
    for task in plan.tasks:
        for dependency in task.depends_on:
            if dependency not in known:
                issues.append(
                    f"Task {task.task_id} references unknown dependency {dependency}."
                )
                continue
            if dependency == task.task_id:
                issues.append(f"Task {task.task_id} cannot depend on itself.")
                continue
            graph[dependency].append(task.task_id)
            indegree[task.task_id] += 1
    queue = deque(sorted(key for key, value in indegree.items() if value == 0))
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in sorted(graph[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(task_ids):
        issues.append("Task dependencies must form an acyclic graph.")
    sinks = sorted(task_id for task_id in task_ids if not graph[task_id])
    if len(sinks) != 1:
        issues.append(
            "Task plan must have exactly one terminal task; "
            f"found {len(sinks)}."
        )
    return issues


def _resource_lookup(
    snapshot: MetaPlannerCapabilitySnapshot,
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "external_xpert": {item["id"]: item for item in snapshot.external_xperts},
        "knowledge_base": {item["id"]: item for item in snapshot.knowledge_bases},
        "toolset_resource": {item["id"]: item for item in snapshot.toolsets},
        "plugin_resource": {item["id"]: item for item in snapshot.plugins},
    }


def _middleware_lookup(
    snapshot: MetaPlannerCapabilitySnapshot,
) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in snapshot.middleware}


def legacy_blueprint_to_typed_ir(
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint,
) -> MetaPlannerTypedBlueprintV2:
    task_by_id = {task.task_id: task for task in plan.tasks}
    agents_by_task: dict[str, list[MetaPlannerAgentBlueprint]] = defaultdict(list)
    for agent in blueprint.agents:
        agents_by_task[agent.task_id].append(agent)
    if any(len(agents_by_task[task_id]) != 1 for task_id in task_by_id):
        raise ValueError(
            "Legacy blueprint must contain exactly one agent for each planned task."
        )
    unknown_tasks = sorted(set(agents_by_task) - set(task_by_id))
    if unknown_tasks:
        raise ValueError(
            "Legacy blueprint references unknown task IDs: "
            + ", ".join(unknown_tasks)
        )

    indegree = {task.task_id: len(task.depends_on) for task in plan.tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for task in plan.tasks:
        for dependency in task.depends_on:
            children[dependency].append(task.task_id)
    queue = deque(sorted(ref for ref, count in indegree.items() if count == 0))
    task_order: list[str] = []
    while queue:
        current = queue.popleft()
        task_order.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(task_order) != len(plan.tasks):
        raise ValueError("Legacy blueprint task plan contains a dependency cycle.")

    task_ancestors: dict[str, set[str]] = {task_id: set() for task_id in task_order}
    for task_id in task_order:
        for dependency in task_by_id[task_id].depends_on:
            task_ancestors[task_id].add(dependency)
            task_ancestors[task_id].update(task_ancestors[dependency])

    outputs: dict[str, str] = {}
    node_refs: dict[str, str] = {}
    nodes: list[MetaPlannerIRNode] = []
    for task_id in task_order:
        task = task_by_id[task_id]
        agent = agents_by_task[task.task_id][0]
        node_ref = f"agent_{task.task_id}"
        node_refs[task.task_id] = node_ref
        output_variable = _safe_identifier(agent.output_variable, "agent_output")
        outputs[task.task_id] = output_variable
        task_input = agent.task_input.strip()
        input_bindings: list[MetaPlannerIRInputBinding] = []
        references_user_input = any(
            re.search(r"\{\{\s*user_input\s*\}\}", template)
            for template in (agent.role_prompt, task_input)
        )
        if not task.depends_on or references_user_input:
            input_bindings.append(
                MetaPlannerIRInputBinding(
                    port="request", variable="user_input", value_type="string"
                )
            )
            if "{{user_input}}" not in task_input:
                task_input += "\n\nUser request:\n{{user_input}}"
        for dependency in task.depends_on:
            dependency_variable = outputs.get(dependency)
            if not dependency_variable:
                raise ValueError(
                    "Legacy blueprint task order does not follow dependencies."
                )
            input_bindings.append(
                MetaPlannerIRInputBinding(
                    port=f"dependency_{dependency}",
                    variable=dependency_variable,
                    value_type="string",
                )
            )
            if f"{{{{{dependency_variable}}}}}" not in task_input:
                task_input += (
                    f"\n\nDependency {dependency}:\n"
                    f"{{{{{dependency_variable}}}}}"
                )
        referenced_variables = {
            match.group(1)
            for template in (agent.role_prompt, task_input)
            for match in re.finditer(
                r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
                template,
            )
        }
        bound_variables = {binding.variable for binding in input_bindings}
        if (
            "conversation_history" in referenced_variables
            and "conversation_history" not in bound_variables
        ):
            input_bindings.append(
                MetaPlannerIRInputBinding(
                    port="history",
                    variable="conversation_history",
                    value_type="array",
                )
            )
            bound_variables.add("conversation_history")
        ancestor_outputs = {
            outputs[ancestor]: ancestor
            for ancestor in task_ancestors[task.task_id]
            if ancestor in outputs
        }
        for variable in sorted(referenced_variables - bound_variables):
            producer_task = ancestor_outputs.get(variable)
            if producer_task is None:
                continue
            input_bindings.append(
                MetaPlannerIRInputBinding(
                    port=f"context_{producer_task}",
                    variable=variable,
                    value_type="string",
                )
            )
            bound_variables.add(variable)
        nodes.append(
            MetaPlannerIRNode(
                ref=node_ref,
                kind="workflow_agent",
                title=agent.name,
                description=task.objective,
                task_ids=[task.task_id],
                inputs=input_bindings,
                outputs=[
                    MetaPlannerIROutputBinding(
                        port="result",
                        variable=output_variable,
                        value_type="string",
                    )
                ],
                config=MetaPlannerWorkflowAgentConfig(
                    role_prompt=agent.role_prompt,
                    task_input=task_input,
                    model_id=agent.model_id,
                    source_agent_id=agent.source_agent_id,
                    method_skill_ids=task.method_skill_ids,
                ).model_dump(mode="json", exclude_none=True),
            )
        )

    sinks = [task_id for task_id in task_order if not children[task_id]]
    if len(sinks) != 1:
        raise ValueError(
            "Legacy blueprint requires a task plan with exactly one terminal task."
        )
    return MetaPlannerTypedBlueprintV2(
        name=blueprint.name,
        description=blueprint.description,
        tags=blueprint.tags,
        starters=blueprint.starters,
        nodes=nodes,
        control_edges=[
            MetaPlannerIRControlEdge(
                source_ref=node_refs[dependency],
                target_ref=node_refs[task.task_id],
            )
            for task in plan.tasks
            for dependency in task.depends_on
        ],
        resources=[
            MetaPlannerIRResourceBinding(
                target_ref=node_refs[binding.task_id],
                **binding.model_dump(exclude={"task_id"}),
            )
            for binding in blueprint.resources
        ],
        middleware=[
            MetaPlannerIRMiddlewareBinding(
                target_ref=node_refs[binding.task_id],
                **binding.model_dump(exclude={"task_id"}),
            )
            for binding in blueprint.middleware
        ],
        prompt_profile_ids=blueprint.prompt_profile_ids,
        final_output=MetaPlannerIRFinalOutput(
            node_ref=node_refs[sinks[0]],
            variable=outputs[sinks[0]],
        ),
    )


def _typed_blueprint(
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint | MetaPlannerTypedBlueprintV2 | GraphIntentV3,
) -> MetaPlannerTypedBlueprintV2:
    if isinstance(blueprint, GraphIntentV3):
        return graph_intent_to_v2(blueprint)
    if isinstance(blueprint, MetaPlannerTypedBlueprintV2):
        return blueprint
    return legacy_blueprint_to_typed_ir(plan, blueprint)


def _graph_intent(
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint | MetaPlannerTypedBlueprintV2 | GraphIntentV3,
) -> tuple[GraphIntentV3, MetaPlannerIRCompatibility]:
    if isinstance(blueprint, GraphIntentV3):
        return blueprint, MetaPlannerIRCompatibility(source_version=3)
    typed = _typed_blueprint(plan, blueprint)
    intent, compatibility = v2_to_graph_intent(typed)
    if intent is None:
        raise ValueError("; ".join(compatibility.warnings))
    return intent, compatibility


def _typed_graph(
    blueprint: MetaPlannerTypedBlueprintV2,
) -> tuple[dict[str, list[str]], dict[str, set[str]], list[str], list[str]]:
    refs = [node.ref for node in blueprint.nodes]
    children: dict[str, list[str]] = {ref: [] for ref in refs}
    parents: dict[str, set[str]] = {ref: set() for ref in refs}
    indegree = {ref: 0 for ref in refs}
    for edge in blueprint.control_edges:
        if edge.source_ref not in children or edge.target_ref not in children:
            continue
        children[edge.source_ref].append(edge.target_ref)
        parents[edge.target_ref].add(edge.source_ref)
        indegree[edge.target_ref] += 1
    queue = deque(sorted(ref for ref, value in indegree.items() if value == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for target_ref in sorted(children[current]):
            indegree[target_ref] -= 1
            if indegree[target_ref] == 0:
                queue.append(target_ref)
    sinks = sorted(ref for ref in refs if not children[ref])
    return children, parents, order, sinks


def validate_blueprint_authorization(
    request: MetaPlannerGenerateRequest,
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint | MetaPlannerTypedBlueprintV2 | GraphIntentV3,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> list[str]:
    issues = validate_task_plan(
        plan,
        max_agents=request.max_agents,
        authorized_agent_ids=set(request.scope.agent_ids),
    )
    try:
        typed = _typed_blueprint(plan, blueprint)
    except (KeyError, ValueError, ValidationError) as exc:
        return issues + [_safe_exception_message(exc)]

    plan_ids = {task.task_id for task in plan.tasks}
    node_refs = [node.ref for node in typed.nodes]
    if len(node_refs) != len(set(node_refs)):
        issues.append("Typed IR node refs must be unique.")
    compiled_ids = [_safe_identifier(ref, "node") for ref in node_refs]
    if len(compiled_ids) != len(set(compiled_ids)):
        issues.append("Typed IR node refs collide after identifier normalization.")

    workflow_agent_count = sum(
        node.kind == "workflow_agent" for node in typed.nodes
    )
    if workflow_agent_count > request.max_agents:
        issues.append(f"Typed IR exceeds max_agents={request.max_agents}.")

    task_nodes: dict[str, list[MetaPlannerIRNode]] = defaultdict(list)
    known_agents = {item["id"] for item in snapshot.agents}
    authorized_agents = set(request.scope.agent_ids)
    available_models = {
        str(item.get("id") or "")
        for item in snapshot.models
        if item.get("safe") is True and item.get("id")
    }
    available_models.add(request.default_agent_model_id)
    graph_nodes_by_ref = (
        {node.ref: node for node in blueprint.nodes}
        if isinstance(blueprint, GraphIntentV3)
        else {}
    )
    for node in typed.nodes:
        if node.kind not in request.scope.allowed_node_kinds:
            issues.append(f"Node kind {node.kind} is not authorized.")
        if node.kind not in META_PLANNER_COMPILABLE_NODE_KINDS:
            issues.append(f"Node kind {node.kind} has no Meta Planner compiler support.")
        adapter = get_planner_node_adapter(node.kind)
        if adapter is None:
            issues.append(
                f"Node kind {node.kind} cannot appear as an executable IR node."
            )
            continue
        try:
            parsed = adapter.validate_config(node)
            if isinstance(blueprint, GraphIntentV3):
                adapter.validate_intent_node(graph_nodes_by_ref[node.ref])
        except (ValidationError, ValueError) as exc:
            issues.append(
                f"Node {node.ref} config is invalid: {_safe_exception_message(exc)}"
            )
            continue
        contract = workflow_node_contract_registry.require(node.kind)
        task_binding = contract.planner.task_binding
        if task_binding == "required" and not node.task_ids:
            issues.append(f"Node {node.ref} must cover at least one plan task.")
        if task_binding == "forbidden" and node.task_ids:
            issues.append(f"Node {node.ref} cannot cover plan tasks.")
        if isinstance(blueprint, GraphIntentV3):
            graph_node = graph_nodes_by_ref[node.ref]
            declared_inputs = {item.variable for item in graph_node.inputs}
            referenced = adapter.referenced_input_variables(parsed)
            for variable in sorted(referenced - declared_inputs):
                issues.append(
                    f"Node {node.ref} template variable {variable} needs an "
                    "explicit data binding."
                )
        if node.kind == "workflow_agent":
            if len(node.outputs) != 1 or node.outputs[0].port != "result":
                issues.append(
                    f"Node {node.ref} must expose exactly one result output port."
                )
            elif node.outputs[0].value_type != "string":
                issues.append(
                    f"Node {node.ref} workflow_agent result must be a string."
                )
        if len({item.port for item in node.inputs}) != len(node.inputs):
            issues.append(f"Node {node.ref} input ports must be unique.")
        if len({item.variable for item in node.outputs}) != len(node.outputs):
            issues.append(f"Node {node.ref} output variables must be unique.")
        unknown_tasks = sorted(set(node.task_ids) - plan_ids)
        if unknown_tasks:
            issues.append(
                f"Node {node.ref} references unknown tasks: "
                + ", ".join(unknown_tasks)
            )
        if task_binding != "required":
            continue
        config = MetaPlannerWorkflowAgentConfig.model_validate(
            parsed.model_dump(mode="json")
        )
        for task_id in set(node.task_ids) & plan_ids:
            task_nodes[task_id].append(node)
            planned_task = next(
                task for task in plan.tasks if task.task_id == task_id
            )
            assigned = planned_task.agent_id
            if assigned and config.source_agent_id != assigned:
                issues.append(
                    f"Node {node.ref} must keep assigned expert {assigned} "
                    f"for task {task_id}."
                )
            if config.method_skill_ids != planned_task.method_skill_ids:
                issues.append(
                    f"Node {node.ref} must keep method Skills for task {task_id}."
                )
        source_agent_id = config.source_agent_id
        if config.model_id and config.model_id not in available_models:
            issues.append(
                f"Agent model {config.model_id} is no longer available."
            )
        if source_agent_id and source_agent_id not in authorized_agents:
            issues.append(f"Expert {source_agent_id} is not authorized.")
        if source_agent_id and source_agent_id not in known_agents:
            issues.append(f"Expert {source_agent_id} is no longer available.")
    for task_id in sorted(plan_ids):
        if not task_nodes[task_id]:
            issues.append(f"Planned task {task_id} is not covered by any IR node.")

    edge_keys: set[tuple[str, str]] = set()
    known_refs = set(node_refs)
    for edge in typed.control_edges:
        key = (edge.source_ref, edge.target_ref)
        if edge.source_ref not in known_refs or edge.target_ref not in known_refs:
            issues.append(
                f"Control edge {edge.source_ref}->{edge.target_ref} references "
                "an unknown node."
            )
        if edge.source_ref == edge.target_ref:
            issues.append(f"Control edge {edge.source_ref} cannot target itself.")
        if key in edge_keys:
            issues.append(
                f"Control edge {edge.source_ref}->{edge.target_ref} is duplicated."
            )
        edge_keys.add(key)
    children, parents, order, sinks = _typed_graph(typed)
    if len(order) != len(typed.nodes):
        issues.append("Typed IR control edges must form an acyclic graph.")
    if len(sinks) != 1:
        issues.append(
            "Typed IR must have exactly one terminal node; "
            f"found {len(sinks)}."
        )
    elif typed.final_output.node_ref != sinks[0]:
        issues.append("Typed IR final_output must reference the terminal node.")
    final_node = next(
        (node for node in typed.nodes if node.ref == typed.final_output.node_ref),
        None,
    )
    if final_node is None:
        issues.append("Typed IR final_output references an unknown node.")
    elif final_node.kind != "workflow_agent":
        issues.append("Typed IR final_output must come from a workflow_agent.")
    elif typed.final_output.variable not in {
        item.variable for item in final_node.outputs
    }:
        issues.append("Typed IR final_output variable is not produced by its node.")

    ancestors: dict[str, set[str]] = {ref: set() for ref in node_refs}
    for ref in order:
        for parent in parents[ref]:
            ancestors[ref].add(parent)
            ancestors[ref].update(ancestors[parent])
    producer_by_variable: dict[str, tuple[str, str]] = {}
    for node in typed.nodes:
        for output in node.outputs:
            previous = producer_by_variable.get(output.variable)
            if previous and previous[0] != node.ref:
                issues.append(
                    f"Variable {output.variable} is produced by multiple nodes."
                )
            producer_by_variable[output.variable] = (node.ref, output.value_type)
    external_variables = {"user_input", "conversation_history"}
    for node in typed.nodes:
        for input_binding in node.inputs:
            producer = producer_by_variable.get(input_binding.variable)
            if not producer and input_binding.variable not in external_variables:
                issues.append(
                    f"Node {node.ref} consumes unknown variable "
                    f"{input_binding.variable}."
                )
            elif producer:
                producer_ref, producer_type = producer
                if producer_ref not in ancestors[node.ref]:
                    issues.append(
                        f"Variable {input_binding.variable} is not reachable at "
                        f"node {node.ref}."
                    )
                if (
                    input_binding.value_type != "any"
                    and producer_type != "any"
                    and input_binding.value_type != producer_type
                ):
                    issues.append(
                        f"Variable {input_binding.variable} type {producer_type} "
                        f"does not match {node.ref} input type "
                        f"{input_binding.value_type}."
                    )

    for task in plan.tasks:
        for dependency in task.depends_on:
            dependency_refs = {node.ref for node in task_nodes[dependency]}
            target_refs = {node.ref for node in task_nodes[task.task_id]}
            if dependency_refs & target_refs:
                continue
            if not any(
                source_ref in ancestors.get(target_ref, set())
                for source_ref in dependency_refs
                for target_ref in target_refs
            ):
                issues.append(
                    f"Task dependency {dependency}->{task.task_id} is not "
                    "represented by the control graph."
                )

    scoped = {
        "external_xpert": set(request.scope.external_xpert_ids),
        "knowledge_base": set(request.scope.knowledge_base_ids),
        "toolset_resource": set(request.scope.toolset_ids),
        "plugin_resource": set(request.scope.plugin_ids),
    }
    lookup = _resource_lookup(snapshot)
    seen_external_tools: set[tuple[str, str]] = set()
    for binding in typed.resources:
        target = next(
            (node for node in typed.nodes if node.ref == binding.target_ref), None
        )
        if target is None or target.kind != "workflow_agent":
            issues.append(
                f"Resource {binding.resource_id} must target a workflow_agent ref."
            )
        if binding.kind not in request.scope.allowed_node_kinds:
            issues.append(f"Resource kind {binding.kind} is not authorized.")
        if binding.resource_id not in scoped[binding.kind]:
            issues.append(
                f"Resource {binding.resource_id} is not authorized for {binding.kind}."
            )
        if binding.resource_id not in lookup[binding.kind]:
            issues.append(f"Resource {binding.resource_id} is no longer available.")
        if binding.kind == "external_xpert":
            tool_name = _safe_identifier(
                binding.tool_name or f"xpert_{binding.resource_id[:12]}",
                "external_xpert",
            )
            key = (binding.target_ref, tool_name)
            if key in seen_external_tools:
                issues.append(
                    f"External Xpert tool name {tool_name} is duplicated for "
                    f"node {binding.target_ref}."
                )
            seen_external_tools.add(key)
            if (
                request.mode == "update"
                and request.target_xpert_id
                and binding.resource_id == request.target_xpert_id
            ):
                issues.append("An Xpert candidate cannot bind itself as an expert.")

    middleware_lookup = _middleware_lookup(snapshot)
    seen_middleware: set[tuple[str, str]] = set()
    for binding in typed.middleware:
        target = next(
            (node for node in typed.nodes if node.ref == binding.target_ref), None
        )
        if target is None or target.kind != "workflow_agent":
            issues.append(
                f"Middleware {binding.middleware_id} must target a workflow_agent ref."
            )
        if binding.middleware_id not in request.scope.middleware_ids:
            issues.append(f"Middleware {binding.middleware_id} is not authorized.")
        if binding.middleware_id not in middleware_lookup:
            issues.append(
                f"Middleware {binding.middleware_id} is no longer available."
            )
        else:
            try:
                resolve_middleware_config(
                    middleware_lookup[binding.middleware_id], binding.config
                )
            except ValueError as exc:
                issues.append(
                    f"Middleware {binding.middleware_id} config is invalid: {exc}"
                )
        key = (binding.target_ref, binding.middleware_id)
        if key in seen_middleware:
            issues.append(
                f"Middleware {binding.middleware_id} is duplicated for "
                f"node {binding.target_ref}."
            )
        seen_middleware.add(key)

    authorized_prompts = set(request.scope.prompt_profile_ids)
    available_prompts = {item["id"] for item in snapshot.prompt_profiles}
    for profile_id in typed.prompt_profile_ids:
        if profile_id not in authorized_prompts:
            issues.append(f"Prompt Profile {profile_id} is not authorized.")
        if profile_id not in available_prompts:
            issues.append(f"Prompt Profile {profile_id} is no longer available.")
    return list(dict.fromkeys(issues))


def _compile_xpert_candidate_legacy(
    *,
    request: MetaPlannerGenerateRequest,
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint,
    snapshot: MetaPlannerCapabilitySnapshot,
    target: XpertDefinition | None,
) -> dict[str, Any]:
    task_by_id = {task.task_id: task for task in plan.tasks}
    agent_by_task = {agent.task_id: agent for agent in blueprint.agents}
    resource_lookup = _resource_lookup(snapshot)
    middleware_lookup = _middleware_lookup(snapshot)

    indegree = {task.task_id: len(task.depends_on) for task in plan.tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for task in plan.tasks:
        for dependency in task.depends_on:
            children[dependency].append(task.task_id)
    queue = deque(sorted(key for key, count in indegree.items() if count == 0))
    order: list[str] = []
    levels: dict[str, int] = {}
    while queue:
        current = queue.popleft()
        order.append(current)
        task = task_by_id[current]
        levels[current] = (
            max((levels[item] for item in task.depends_on), default=-1) + 1
        )
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(plan.tasks):
        raise ValueError("Task plan contains a dependency cycle.")

    nodes: list[NativeWorkflowNode] = [
        NativeWorkflowNode(
            id="input",
            type="input",
            position=WorkflowPosition(x=40, y=160),
            data={
                "kind": "input",
                "title": "Conversation input",
                "variableName": "user_input",
                "historyVariable": "conversation_history",
            },
        )
    ]
    edges: list[NativeWorkflowEdge] = []
    task_node_ids: dict[str, str] = {}
    outputs: dict[str, str] = {}
    level_rows: dict[int, int] = defaultdict(int)
    resources_by_task: dict[str, list[Any]] = defaultdict(list)
    middleware_by_task: dict[str, list[Any]] = defaultdict(list)
    for binding in blueprint.resources:
        resources_by_task[binding.task_id].append(binding)
    for binding in blueprint.middleware:
        middleware_by_task[binding.task_id].append(binding)

    for task_id in order:
        task = task_by_id[task_id]
        agent = agent_by_task[task_id]
        node_id = f"agent_{_safe_identifier(task_id, 'task')}"
        task_node_ids[task_id] = node_id
        output_variable = _safe_identifier(
            agent.output_variable or f"{task_id}_output",
            "agent_output",
        )
        outputs[task_id] = output_variable
        dependency_variables = [outputs[item] for item in task.depends_on]
        task_input = agent.task_input.strip()
        if not task.depends_on and "{{user_input}}" not in task_input:
            task_input = f"{task_input}\n\nUser request:\n{{{{user_input}}}}"
        if task.depends_on:
            missing = [
                variable
                for variable in dependency_variables
                if f"{{{{{variable}}}}}" not in task_input
            ]
            if missing:
                task_input += "\n\nDependency results:\n" + "\n".join(
                    f"- {variable}: {{{{{variable}}}}}" for variable in missing
                )
        has_runtime_resources = bool(resources_by_task[task_id])
        requires_runtime_mode = any(
            middleware_lookup.get(binding.middleware_id, {}).get(
                "requires_tool_mode"
            )
            == "mcp_tools"
            for binding in middleware_by_task[task_id]
        )
        level = levels[task_id]
        row = level_rows[level]
        level_rows[level] += 1
        nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type="workflow_agent",
                position=WorkflowPosition(
                    x=300 + level * 340,
                    y=80 + row * 260,
                ),
                data={
                    "kind": "workflow_agent",
                    "title": agent.name,
                    "description": task.objective,
                    "agentName": agent.name,
                    "modelId": agent.model_id
                    or request.default_agent_model_id,
                    "rolePrompt": agent.role_prompt,
                    "taskInput": task_input,
                    "toolMode": (
                        "mcp_tools"
                        if has_runtime_resources or requires_runtime_mode
                        else "none"
                    ),
                    "toolNames": "",
                    "maxIterations": "6",
                    "parallelToolCalls": "false",
                    "maxToolConcurrency": "2",
                    "maxToolCalls": "12",
                    "maxToolDepth": "4",
                    "outputVariable": output_variable,
                    "exceptionHandling": "fail",
                    **(
                        {"sourceAgentId": agent.source_agent_id}
                        if agent.source_agent_id
                        else {}
                    ),
                    **(
                        {"acceptanceCriteria": task.acceptance}
                        if task.acceptance
                        else {}
                    ),
                    **(
                        {"methodSkillIds": task.method_skill_ids}
                        if task.method_skill_ids
                        else {}
                    ),
                },
            )
        )
        if task.depends_on:
            for dependency in task.depends_on:
                edges.append(
                    NativeWorkflowEdge(
                        id=f"edge_{dependency}_{task_id}",
                        source=task_node_ids[dependency],
                        target=node_id,
                    )
                )
        else:
            edges.append(
                NativeWorkflowEdge(
                    id=f"edge_input_{task_id}",
                    source="input",
                    target=node_id,
                )
            )

    for index, binding in enumerate(blueprint.resources):
        target_node_id = task_node_ids[binding.task_id]
        resource = resource_lookup[binding.kind][binding.resource_id]
        node_id = f"resource_{index + 1}_{binding.kind}"
        published_version = resource.get("published_version")
        if binding.kind == "external_xpert":
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "xpertId": binding.resource_id,
                "toolName": _safe_identifier(
                    binding.tool_name or f"xpert_{binding.resource_id[:12]}",
                    "external_xpert",
                ),
                "versionPolicy": "pinned",
                "pinnedVersion": published_version,
            }
            source_handle, target_handle = "expert-binding", "expert"
        elif binding.kind == "knowledge_base":
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "knowledgeBaseId": binding.resource_id,
                "topK": str(binding.top_k),
                "scoreThreshold": str(binding.score_threshold),
                "observedActiveVersionId": resource.get("metadata", {}).get(
                    "active_version_id"
                ),
            }
            source_handle, target_handle = "knowledge-binding", "knowledge"
        elif binding.kind == "toolset_resource":
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "toolsetId": binding.resource_id,
                "versionPolicy": "pinned",
                "pinnedVersion": published_version,
            }
            source_handle, target_handle = "toolset-binding", "toolset"
        else:
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "pluginId": binding.resource_id,
                "versionPolicy": "pinned",
                "pinnedVersion": published_version,
            }
            source_handle, target_handle = "plugin-binding", "plugin"
        target_position = next(
            node.position for node in nodes if node.id == target_node_id
        )
        nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type=binding.kind,
                position=WorkflowPosition(
                    x=(target_position.x if target_position else 300) - 120,
                    y=(target_position.y if target_position else 80) + 150,
                ),
                data=data,
            )
        )
        edges.append(
            NativeWorkflowEdge(
                id=f"edge_{node_id}_{target_node_id}",
                source=node_id,
                target=target_node_id,
                sourceHandle=source_handle,
                targetHandle=target_handle,
            )
        )

    for index, binding in enumerate(
        sorted(
            blueprint.middleware,
            key=lambda item: (item.priority, item.middleware_id, item.task_id),
        )
    ):
        target_node_id = task_node_ids[binding.task_id]
        middleware = middleware_lookup[binding.middleware_id]
        defaults = dict(middleware.get("default_config") or {})
        defaults.update(binding.config)
        node_id = f"middleware_{index + 1}_{_safe_identifier(binding.middleware_id, 'mw')}"
        target_position = next(
            node.position for node in nodes if node.id == target_node_id
        )
        nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type="runtime_middleware",
                position=WorkflowPosition(
                    x=(target_position.x if target_position else 300) + 120,
                    y=(target_position.y if target_position else 80) + 150,
                ),
                data={
                    "kind": "runtime_middleware",
                    "title": middleware["title"],
                    "description": middleware["description"],
                    "runtimeMiddlewareId": binding.middleware_id,
                    "runtimeMiddlewareKind": middleware["kind"],
                    "runtimeMiddlewareConfig": defaults,
                    "middlewarePriority": str(binding.priority),
                    "configVersion": middleware["config_version"],
                },
            )
        )
        edges.append(
            NativeWorkflowEdge(
                id=f"edge_{node_id}_{target_node_id}",
                source=node_id,
                target=target_node_id,
                sourceHandle="middleware-binding",
                targetHandle="middleware",
            )
        )

    sinks = [task_id for task_id in order if not children[task_id]]
    if len(sinks) != 1:
        raise ValueError(
            "Legacy compiler requires exactly one terminal task; "
            f"found {len(sinks)}."
        )
    final_task_id = sinks[0]
    final_node_id = task_node_ids[final_task_id]
    final_output = outputs[final_task_id]
    final_position = next(node.position for node in nodes if node.id == final_node_id)
    nodes.append(
        NativeWorkflowNode(
            id="output",
            type="output",
            position=WorkflowPosition(
                x=(final_position.x if final_position else 300) + 360,
                y=final_position.y if final_position else 160,
            ),
            data={
                "kind": "output",
                "title": "Final answer",
                "outputVariable": final_output,
                "template": f"{{{{{final_output}}}}}",
            },
        )
    )
    edges.append(
        NativeWorkflowEdge(
            id=f"edge_{final_task_id}_output",
            source=final_node_id,
            target="output",
        )
    )

    workflow = NativeWorkflowDefinition(
        id=f"meta_{uuid.uuid4().hex[:12]}",
        title=blueprint.name,
        version="evoagentx-meta-planner-v2",
        source="workflow-native",
        nodes=nodes,
        edges=edges,
    )
    prompt_lookup = {item["id"]: item for item in snapshot.prompt_profiles}
    prompt_bindings = [
        PromptProfileBinding(
            profile_id=profile_id,
            version_policy="pinned",
            pinned_version=prompt_lookup[profile_id]["published_version"],
        )
        for profile_id in dict.fromkeys(blueprint.prompt_profile_ids)
    ]
    base_draft = target.draft.model_copy(deep=True) if target else None
    draft_payload: dict[str, Any] = {
        "workflow": workflow,
        "input_variable": "user_input",
        "history_variable": "conversation_history",
        "output_variable": final_output,
        "prompt_profiles": prompt_bindings,
    }
    if base_draft is not None:
        draft_payload["agent_config"] = base_draft.agent_config
        draft_payload["features"] = base_draft.features
    draft = XpertDraft(**draft_payload)
    return {
        "name": blueprint.name,
        "description": blueprint.description,
        "tags": list(dict.fromkeys(blueprint.tags)),
        "starters": list(dict.fromkeys(blueprint.starters)),
        "draft": draft.model_dump(mode="json"),
    }


def compile_xpert_candidate(
    *,
    request: MetaPlannerGenerateRequest,
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint | MetaPlannerTypedBlueprintV2 | GraphIntentV3,
    snapshot: MetaPlannerCapabilitySnapshot,
    target: XpertDefinition | None,
) -> dict[str, Any]:
    intent, _ = _graph_intent(plan, blueprint)
    resolved_graph = resolve_graph_intent(
        intent,
        snapshot,
        default_agent_model_id=request.default_agent_model_id,
    )
    typed = graph_intent_to_v2(intent)
    task_by_id = {task.task_id: task for task in plan.tasks}
    node_by_ref = {node.ref: node for node in typed.nodes}
    resource_lookup = _resource_lookup(snapshot)
    middleware_lookup = _middleware_lookup(snapshot)
    _, parents, order, sinks = _typed_graph(typed)
    if len(order) != len(typed.nodes):
        raise ValueError("Typed IR control graph contains a cycle.")
    if len(sinks) != 1 or sinks[0] != typed.final_output.node_ref:
        raise ValueError("Typed IR final output does not match its terminal node.")

    levels: dict[str, int] = {}
    level_rows: dict[int, int] = defaultdict(int)
    for ref in order:
        levels[ref] = max((levels[parent] for parent in parents[ref]), default=-1) + 1

    nodes: list[NativeWorkflowNode] = [
        NativeWorkflowNode(
            id="input",
            type="input",
            position=WorkflowPosition(x=40, y=160),
            data={
                "kind": "input",
                "title": "Conversation input",
                "variableName": "user_input",
                "historyVariable": "conversation_history",
            },
        )
    ]
    edges: list[NativeWorkflowEdge] = []
    compiled_node_ids = {
        ref: f"node_{_safe_identifier(ref, 'node')}" for ref in order
    }
    resources_by_ref: dict[str, list[MetaPlannerIRResourceBinding]] = defaultdict(list)
    middleware_by_ref: dict[str, list[MetaPlannerIRMiddlewareBinding]] = defaultdict(list)
    resolved_nodes_by_ref = {node.ref: node for node in resolved_graph.nodes}
    resolved_resource_ids: dict[tuple[str, str, str], str] = {}
    resolved_middleware_ids: dict[tuple[str, str], str] = {}
    for graph_edge in resolved_graph.edges:
        source_node = resolved_nodes_by_ref.get(graph_edge.source.node_ref)
        if source_node is None:
            continue
        if graph_edge.mode == "binding":
            resolved_resource_ids[
                (
                    source_node.kind,
                    str(source_node.config.get("resource_id") or ""),
                    graph_edge.target.node_ref,
                )
            ] = source_node.node_id
        elif graph_edge.mode == "metadata":
            resolved_middleware_ids[
                (
                    str(source_node.config.get("middleware_id") or ""),
                    graph_edge.target.node_ref,
                )
            ] = source_node.node_id
    for binding in typed.resources:
        resources_by_ref[binding.target_ref].append(binding)
    for binding in typed.middleware:
        middleware_by_ref[binding.target_ref].append(binding)

    for ref in order:
        ir_node = node_by_ref[ref]
        adapter = get_planner_node_adapter(ir_node.kind)
        if adapter is None:
            raise ValueError(f"Node kind {ir_node.kind} has no compiler adapter.")
        parsed_config = adapter.validate_config(ir_node)
        output_variable = ir_node.outputs[0].variable
        level = levels[ref]
        row = level_rows[level]
        level_rows[level] += 1
        acceptance = "\n".join(
            task_by_id[task_id].acceptance
            for task_id in ir_node.task_ids
            if task_id in task_by_id and task_by_id[task_id].acceptance
        )
        requires_runtime_mode = any(
            middleware_lookup.get(binding.middleware_id, {}).get(
                "requires_tool_mode"
            )
            == "mcp_tools"
            for binding in middleware_by_ref[ref]
        )
        nodes.append(
            adapter.compile_node(
                ir_node,
                parsed_config,
                PlannerNodeCompileContext(
                    node_id=compiled_node_ids[ref],
                    position=WorkflowPosition(
                        x=300 + level * 340,
                        y=80 + row * 260,
                    ),
                    default_agent_model_id=request.default_agent_model_id,
                    output_variable=output_variable,
                    acceptance_criteria=acceptance,
                    has_runtime_resources=bool(resources_by_ref[ref]),
                    requires_runtime_mode=requires_runtime_mode,
                ),
            )
        )

    for ref in order:
        if not parents[ref]:
            edges.append(
                NativeWorkflowEdge(
                    id=f"edge_input_{compiled_node_ids[ref]}",
                    source="input",
                    target=compiled_node_ids[ref],
                )
            )
    for edge in typed.control_edges:
        edges.append(
            NativeWorkflowEdge(
                id=(
                    f"edge_{compiled_node_ids[edge.source_ref]}_"
                    f"{compiled_node_ids[edge.target_ref]}"
                ),
                source=compiled_node_ids[edge.source_ref],
                target=compiled_node_ids[edge.target_ref],
            )
        )

    for index, binding in enumerate(typed.resources):
        target_node_id = compiled_node_ids[binding.target_ref]
        resource = resource_lookup[binding.kind][binding.resource_id]
        node_id = resolved_resource_ids[
            (binding.kind, binding.resource_id, binding.target_ref)
        ]
        published_version = intent._pinned_resource_versions.get(
            (binding.kind, binding.resource_id, binding.target_ref),
            resource.get("published_version"),
        )
        if binding.kind == "external_xpert":
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "xpertId": binding.resource_id,
                "toolName": _safe_identifier(
                    binding.tool_name or f"xpert_{binding.resource_id[:12]}",
                    "external_xpert",
                ),
                "versionPolicy": "pinned",
                "pinnedVersion": published_version,
            }
            source_handle, target_handle = "expert-binding", "expert"
        elif binding.kind == "knowledge_base":
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "knowledgeBaseId": binding.resource_id,
                "topK": str(binding.top_k),
                "scoreThreshold": str(binding.score_threshold),
                "observedActiveVersionId": resource.get("metadata", {}).get(
                    "active_version_id"
                ),
            }
            source_handle, target_handle = "knowledge-binding", "knowledge"
        elif binding.kind == "toolset_resource":
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "toolsetId": binding.resource_id,
                "versionPolicy": "pinned",
                "pinnedVersion": published_version,
            }
            source_handle, target_handle = "toolset-binding", "toolset"
        else:
            data = {
                "kind": binding.kind,
                "title": resource["name"],
                "description": binding.description or resource["description"],
                "pluginId": binding.resource_id,
                "versionPolicy": "pinned",
                "pinnedVersion": published_version,
            }
            source_handle, target_handle = "plugin-binding", "plugin"
        target_position = next(
            node.position for node in nodes if node.id == target_node_id
        )
        nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type=binding.kind,
                position=WorkflowPosition(
                    x=(target_position.x if target_position else 300) - 120,
                    y=(target_position.y if target_position else 80) + 150,
                ),
                data=data,
            )
        )
        edges.append(
            NativeWorkflowEdge(
                id=f"edge_{node_id}_{target_node_id}",
                source=node_id,
                target=target_node_id,
                sourceHandle=source_handle,
                targetHandle=target_handle,
            )
        )

    for index, binding in enumerate(
        sorted(
            typed.middleware,
            key=lambda item: (
                item.priority,
                item.middleware_id,
                item.target_ref,
            ),
        )
    ):
        target_node_id = compiled_node_ids[binding.target_ref]
        middleware = middleware_lookup[binding.middleware_id]
        defaults = dict(middleware.get("default_config") or {})
        defaults.update(binding.config)
        node_id = resolved_middleware_ids[
            (binding.middleware_id, binding.target_ref)
        ]
        target_position = next(
            node.position for node in nodes if node.id == target_node_id
        )
        nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type="runtime_middleware",
                position=WorkflowPosition(
                    x=(target_position.x if target_position else 300) + 120,
                    y=(target_position.y if target_position else 80) + 150,
                ),
                data={
                    "kind": "runtime_middleware",
                    "title": middleware["title"],
                    "description": middleware["description"],
                    "runtimeMiddlewareId": binding.middleware_id,
                    "runtimeMiddlewareKind": middleware["kind"],
                    "runtimeMiddlewareConfig": defaults,
                    "middlewarePriority": str(binding.priority),
                    "configVersion": middleware["config_version"],
                },
            )
        )
        edges.append(
            NativeWorkflowEdge(
                id=f"edge_{node_id}_{target_node_id}",
                source=node_id,
                target=target_node_id,
                sourceHandle="middleware-binding",
                targetHandle="middleware",
            )
        )

    final_node_id = compiled_node_ids[typed.final_output.node_ref]
    final_position = next(node.position for node in nodes if node.id == final_node_id)
    nodes.append(
        NativeWorkflowNode(
            id="output",
            type="output",
            position=WorkflowPosition(
                x=(final_position.x if final_position else 300) + 360,
                y=final_position.y if final_position else 160,
            ),
            data={
                "kind": "output",
                "title": "Final answer",
                "outputVariable": typed.final_output.variable,
                "template": f"{{{{{typed.final_output.variable}}}}}",
            },
        )
    )
    edges.append(
        NativeWorkflowEdge(
            id=f"edge_{final_node_id}_output",
            source=final_node_id,
            target="output",
        )
    )

    workflow = NativeWorkflowDefinition(
        id=f"meta_{resolved_graph.graph_checksum[:12]}",
        title=typed.name,
        version="evoagentx-meta-planner-graph-ir-v3",
        source="workflow-native",
        nodes=nodes,
        edges=edges,
    )
    prompt_lookup = {item["id"]: item for item in snapshot.prompt_profiles}
    prompt_bindings = [
        PromptProfileBinding(
            profile_id=profile_id,
            version_policy="pinned",
            pinned_version=intent._pinned_prompt_profile_versions.get(
                profile_id, prompt_lookup[profile_id]["published_version"]
            ),
        )
        for profile_id in dict.fromkeys(typed.prompt_profile_ids)
    ]
    base_draft = target.draft.model_copy(deep=True) if target else None
    draft_payload: dict[str, Any] = {
        "workflow": workflow,
        "input_variable": "user_input",
        "history_variable": "conversation_history",
        "output_variable": typed.final_output.variable,
        "prompt_profiles": prompt_bindings,
    }
    if base_draft is not None:
        draft_payload["agent_config"] = base_draft.agent_config
        draft_payload["features"] = base_draft.features
    draft = XpertDraft(**draft_payload)
    candidate = {
        "name": typed.name,
        "description": typed.description,
        "tags": list(dict.fromkeys(typed.tags)),
        "starters": list(dict.fromkeys(typed.starters)),
        "draft": draft.model_dump(mode="json"),
    }
    annotate_candidate_with_graph_ir(candidate, intent, resolved_graph)
    return candidate


def _candidate_xpert(
    candidate: dict[str, Any],
    *,
    target: XpertDefinition | None,
) -> XpertDefinition:
    if target is not None:
        preview = target.model_copy(deep=True)
        preview.name = candidate["name"]
        preview.description = candidate["description"]
        preview.tags = candidate["tags"]
        preview.starters = candidate["starters"]
        preview.draft = XpertDraft.model_validate(candidate["draft"])
        return preview
    return XpertDefinition(
        id="meta-planner-preview",
        slug="meta-planner-preview",
        name=candidate["name"],
        description=candidate["description"],
        tags=candidate["tags"],
        starters=candidate["starters"],
        draft=XpertDraft.model_validate(candidate["draft"]),
        created_at=time.time(),
        updated_at=time.time(),
    )


def _unsupported_target_node_kinds(target: XpertDefinition) -> list[str]:
    supported = set(META_PLANNER_COMPILABLE_NODE_KINDS) | {"runtime_middleware"}
    return sorted(
        {
            node_kind(node)
            for node in target.draft.workflow.nodes
            if node_kind(node) not in supported
        }
    )


def _validation_report(
    candidate: dict[str, Any],
    *,
    target: XpertDefinition | None,
    preflight: PreflightCallback,
) -> dict[str, Any]:
    preview = _candidate_xpert(candidate, target=target)
    workflow_validation = validate_workflow_graph(preview.draft.workflow)
    publish_validation, _, _ = preflight(preview)
    stages = [
        {
            "id": "workflow",
            "valid": workflow_validation.valid,
            "issues": [
                issue.model_dump(mode="json")
                for issue in workflow_validation.issues
            ],
        },
        {
            "id": "publish_preflight",
            "valid": publish_validation.valid,
            "issues": [
                issue.model_dump(mode="json")
                for issue in publish_validation.issues
            ],
        },
    ]
    return {
        "valid": all(stage["valid"] for stage in stages),
        "stages": stages,
        "issues": [
            issue
            for stage in stages
            for issue in stage["issues"]
            if issue.get("severity", "error") == "error"
        ],
    }


def _authoritative_validation_report(
    validation: dict[str, Any],
    proposal: AuthoringProposal,
) -> dict[str, Any]:
    """Combine local checks with the persisted Proposal validation verdict."""

    proposal_validation = (
        proposal.validation if isinstance(proposal.validation, dict) else {}
    )
    authoring_valid = proposal_validation.get("valid") is True
    raw_issues = proposal_validation.get("issues")
    authoring_issues = (
        [dict(issue) for issue in raw_issues[:20] if isinstance(issue, dict)]
        if isinstance(raw_issues, list)
        else []
    )
    stages = [
        *list(validation.get("stages") or []),
        {
            "id": "authoring_proposal",
            "valid": authoring_valid,
            "issues": authoring_issues,
        },
    ]
    return {
        **validation,
        "valid": validation.get("valid") is True and authoring_valid,
        "stages": stages,
        "issues": [
            issue
            for stage in stages
            for issue in stage.get("issues", [])
            if issue.get("severity", "error") == "error"
        ],
    }


class MetaPlannerV2Service:
    def __init__(
        self,
        *,
        authoring_service: AuthoringService,
        preflight: PreflightCallback,
        completion: CompletionCallback | None = None,
    ) -> None:
        self.authoring_service = authoring_service
        self.preflight = preflight
        self.completion = completion

    async def generate(
        self,
        request: MetaPlannerGenerateRequest,
        snapshot: MetaPlannerCapabilitySnapshot,
        *,
        target: XpertDefinition | None = None,
        source_run_id: str | None = None,
    ) -> MetaPlannerGenerateResponse:
        if self.completion is None:
            raise ValueError("Meta Planner completion callback is not configured.")
        if not any(request.scope.model_dump(mode="json").values()):
            request = request.model_copy(
                update={"scope": snapshot.default_scope.model_copy(deep=True)}
            )
        assert_scope_is_authorized(request.scope, snapshot)
        if request.mode == "update" and target is None:
            raise ValueError("Update mode requires an existing target Xpert.")
        if request.mode == "create" and target is not None:
            raise ValueError("Create mode cannot receive a target Xpert.")
        if target is not None:
            unsupported = _unsupported_target_node_kinds(target)
            if unsupported:
                raise ValueError(
                    "Meta Planner update cannot safely round-trip target node kinds: "
                    + ", ".join(unsupported)
                    + ". Use create mode or wait for a dedicated compiler adapter."
                )

        plan_prompt = self._plan_prompt(request, snapshot)
        raw_plan = await self.completion(
            request.planner_model_id,
            TASK_PLAN_SYSTEM_PROMPT,
            plan_prompt,
            request.temperature,
            4_096,
        )
        try:
            plan = MetaPlannerTaskPlan.model_validate(_json_payload(raw_plan))
        except Exception as exc:
            raise ValueError(_safe_exception_message(exc)) from exc
        plan_issues = validate_task_plan(
            plan,
            max_agents=request.max_agents,
            authorized_agent_ids=set(request.scope.agent_ids),
        )
        if plan_issues:
            raise ValueError("; ".join(plan_issues))

        raw_blueprint = await self.completion(
            request.planner_model_id,
            BLUEPRINT_SYSTEM_PROMPT,
            self._blueprint_prompt(request, plan, snapshot, target),
            request.temperature,
            8_192,
        )
        repair_used = False
        warnings: list[str] = []
        (
            blueprint,
            candidate,
            validation,
            issues,
            graph_ir,
            compatibility,
        ) = self._compile_and_validate(
            request=request,
            plan=plan,
            raw_blueprint=raw_blueprint,
            snapshot=snapshot,
            target=target,
        )
        repair_protocol = "none"
        if issues:
            repair_used = True
            if blueprint is not None:
                repair_protocol = "graph_patch_v1"
                base_graph_checksum = canonical_checksum(
                    blueprint.model_dump(mode="json")
                )
                base_candidate_checksum = canonical_checksum(candidate or {})
                repaired_raw = await self.completion(
                    request.planner_model_id,
                    PATCH_REPAIR_SYSTEM_PROMPT,
                    self._patch_repair_prompt(
                        request,
                        plan,
                        snapshot,
                        blueprint,
                        issues,
                        expected_graph_checksum=base_graph_checksum,
                        expected_candidate_checksum=base_candidate_checksum,
                    ),
                    0,
                    8_192,
                )
                try:
                    repair_patch = GraphPatchEnvelopeV1.model_validate(
                        _json_payload(repaired_raw)
                    )
                    if repair_patch.proposal_revision != 1:
                        raise ValueError(
                            "Planner repair patch must use synthetic revision 1."
                        )
                    if (
                        repair_patch.expected_graph_checksum
                        != base_graph_checksum
                        or repair_patch.expected_candidate_checksum
                        != base_candidate_checksum
                    ):
                        raise ValueError(
                            "Planner repair patch changed its base checksums."
                        )
                    patched = apply_graph_patch(
                        blueprint,
                        repair_patch,
                        plan_task_ids={task.task_id for task in plan.tasks},
                        allowed_node_kinds=set(request.scope.allowed_node_kinds),
                    )
                    normalized_intent, normalized_refs = (
                        _normalize_adapter_outputs_for_repair(patched.intent)
                    )
                    if normalized_refs:
                        warnings.append(
                            "The single Graph Patch repair refreshed "
                            "Adapter-derived output Schemas and dependent data "
                            "bindings for: "
                            + ", ".join(normalized_refs)
                            + "."
                        )
                    repaired_raw = json.dumps(
                        normalized_intent.model_dump(mode="json"),
                        ensure_ascii=False,
                    )
                    (
                        blueprint,
                        candidate,
                        validation,
                        issues,
                        graph_ir,
                        compatibility,
                    ) = self._compile_and_validate(
                        request=request,
                        plan=plan,
                        raw_blueprint=repaired_raw,
                        snapshot=snapshot,
                        target=target,
                    )
                except Exception as exc:
                    message = _safe_exception_message(exc)
                    candidate = {}
                    graph_ir = None
                    issues = [message]
                    validation = {"valid": False, "issues": [message]}
            else:
                repair_protocol = "graph_intent_v3"
                repaired_raw = await self.completion(
                    request.planner_model_id,
                    REPAIR_SYSTEM_PROMPT,
                    self._repair_prompt(
                        request,
                        plan,
                        snapshot,
                        raw_blueprint,
                        issues,
                    ),
                    0,
                    8_192,
                )
                (
                    blueprint,
                    candidate,
                    validation,
                    issues,
                    graph_ir,
                    compatibility,
                ) = self._compile_and_validate(
                    request=request,
                    plan=plan,
                    raw_blueprint=repaired_raw,
                    snapshot=snapshot,
                    target=target,
                )
            if issues:
                warnings.append(
                    "The single repair pass did not produce an approvable candidate."
                )
                if not candidate:
                    candidate, graph_ir, compatibility = self._fallback_candidate(
                        request=request,
                        plan=plan,
                        snapshot=snapshot,
                        target=target,
                    )
                    validation = _validation_report(
                        candidate,
                        target=target,
                        preflight=self.preflight,
                    )
                repair_stage = {
                    "id": "planner_repair",
                    "valid": False,
                    "issues": [
                        {
                            "code": "meta_planner_repair_failed",
                            "message": issue[:500],
                            "severity": "error",
                        }
                        for issue in issues[:20]
                    ],
                }
                validation = dict(validation)
                validation["valid"] = False
                validation["stages"] = [
                    repair_stage,
                    *list(validation.get("stages") or []),
                ]
                validation["issues"] = [
                    *repair_stage["issues"],
                    *list(validation.get("issues") or []),
                ]

        report = {
            "planner_version": "evoagentx-meta-planner-graph-ir-v3",
            "typed_ir_version": GRAPH_IR_VERSION,
            "ir_version": GRAPH_IR_VERSION,
            "graph_ir": (
                graph_ir.model_dump(mode="json") if graph_ir is not None else None
            ),
            "graph_ir_checksum": (
                graph_ir.graph_checksum if graph_ir is not None else ""
            ),
            "authoring_graph_checksum": (
                graph_authoring_checksum(graph_ir) if graph_ir is not None else ""
            ),
            "graph_ir_status": "current" if graph_ir is not None else "unavailable",
            "compiled_workflow_checksum": workflow_semantic_checksum(candidate),
            "authoring_candidate_checksum": workflow_authoring_checksum(candidate),
            "compatibility": compatibility.model_dump(mode="json"),
            "goal": request.goal,
            "mode": request.mode,
            "plan": plan.model_dump(mode="json"),
            "assumptions": list(plan.assumptions),
            "capability_snapshot": {
                "version": snapshot.version,
                "hash": snapshot.snapshot_hash,
            },
            "authorized_scope": request.scope.model_dump(mode="json"),
            "generation_config": {
                "planner_model_id": request.planner_model_id,
                "default_agent_model_id": request.default_agent_model_id,
                "max_agents": request.max_agents,
            },
            "validation": validation,
            "repair_used": repair_used,
            "repair_protocol": repair_protocol,
            "warnings": warnings,
            "human_modified": False,
        }
        if request.mode == "create":
            payload = {**candidate, "meta_planner_report": report}
            proposal = self.authoring_service.proposal_store.create(
                kind="xpert_create",
                title=f"Meta Planner: {candidate['name']}",
                payload=payload,
                source_type="meta_planner",
                source_id=f"meta_planner:{uuid.uuid4().hex}",
                source_run_id=source_run_id,
            )
        else:
            assert target is not None
            payload = {
                "xpert_id": target.id,
                "patch": candidate,
                "meta_planner_report": report,
            }
            proposal = self.authoring_service.proposal_store.create(
                kind="xpert_update",
                title=f"Meta Planner update: {candidate['name']}",
                payload=payload,
                source_type="meta_planner",
                source_id=f"meta_planner:{uuid.uuid4().hex}",
                source_run_id=source_run_id,
                target_id=target.id,
                base_revision=target.draft_revision,
            )
        proposal = self.authoring_service.validate(
            proposal.proposal_id,
            revision=proposal.revision,
        )
        validation = _authoritative_validation_report(validation, proposal)
        return self._response(
            request=request,
            plan=plan,
            candidate=candidate,
            proposal=proposal,
            validation=validation,
            warnings=warnings,
            repair_used=repair_used,
            snapshot=snapshot,
            graph_ir=graph_ir,
            compatibility=compatibility,
        )

    def preview(
        self,
        request: MetaPlannerGenerateRequest,
        snapshot: MetaPlannerCapabilitySnapshot,
        *,
        plan: MetaPlannerTaskPlan,
        blueprint: MetaPlannerBlueprint | MetaPlannerTypedBlueprintV2 | GraphIntentV3,
        target: XpertDefinition | None = None,
        warnings: list[str] | None = None,
        repair_used: bool = False,
    ) -> MetaPlannerPreviewResponse:
        """Compile and validate a plan without creating an Authoring Proposal."""

        if not any(request.scope.model_dump(mode="json").values()):
            request = request.model_copy(
                update={"scope": snapshot.default_scope.model_copy(deep=True)}
            )
        assert_scope_is_authorized(request.scope, snapshot)
        if target is not None:
            unsupported = _unsupported_target_node_kinds(target)
            if unsupported:
                raise ValueError(
                    "Meta Planner preview cannot safely round-trip target node kinds: "
                    + ", ".join(unsupported)
                    + "."
                )
        plan_issues = validate_task_plan(
            plan,
            max_agents=request.max_agents,
            authorized_agent_ids=set(request.scope.agent_ids),
        )
        blueprint_issues = validate_blueprint_authorization(
            request, plan, blueprint, snapshot
        )
        issues = list(dict.fromkeys([*plan_issues, *blueprint_issues]))
        if issues:
            raise ValueError("; ".join(issues))
        intent, compatibility = _graph_intent(plan, blueprint)
        graph_ir = resolve_graph_intent(
            intent,
            snapshot,
            default_agent_model_id=request.default_agent_model_id,
        )
        candidate = compile_xpert_candidate(
            request=request,
            plan=plan,
            blueprint=blueprint,
            snapshot=snapshot,
            target=target,
        )
        validation = _validation_report(
            candidate,
            target=target,
            preflight=self.preflight,
        )
        return MetaPlannerPreviewResponse(
            plan=plan,
            candidate=candidate,
            validation=validation,
            warnings=list(warnings or []),
            repair_used=repair_used,
            capability_snapshot_version=snapshot.version,
            capability_snapshot_hash=snapshot.snapshot_hash,
            ir_version=GRAPH_IR_VERSION,
            graph_ir=graph_ir.model_dump(mode="json"),
            graph_ir_checksum=graph_ir.graph_checksum,
            compatibility=compatibility,
        )

    @staticmethod
    def _fallback_candidate(
        *,
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        snapshot: MetaPlannerCapabilitySnapshot,
        target: XpertDefinition | None,
    ) -> tuple[dict[str, Any], ResolvedGraphIRV3, MetaPlannerIRCompatibility]:
        blueprint = MetaPlannerBlueprint(
            name=(target.name if target is not None else "Unresolved Xpert candidate"),
            description="Meta Planner repair failed. Review validation issues.",
            agents=[
                MetaPlannerAgentBlueprint(
                    task_id=task.task_id,
                    name=task.title,
                    role_prompt="Candidate requires human repair before approval.",
                    task_input="{{user_input}}",
                    output_variable=_safe_identifier(
                        f"{task.task_id}_output", "agent_output"
                    ),
                    model_id=request.default_agent_model_id,
                    source_agent_id=task.agent_id,
                )
                for task in plan.tasks
            ],
        )
        intent, compatibility = _graph_intent(plan, blueprint)
        graph_ir = resolve_graph_intent(
            intent,
            snapshot,
            default_agent_model_id=request.default_agent_model_id,
        )
        candidate = compile_xpert_candidate(
            request=request,
            plan=plan,
            blueprint=blueprint,
            snapshot=snapshot,
            target=target,
        )
        workflow = candidate["draft"]["workflow"]
        for node in workflow["nodes"]:
            if node.get("type") == "workflow_agent":
                # Keep the emergency fallback unapprovable until a human repairs it.
                node["data"]["modelId"] = ""
                break
        return candidate, graph_ir, compatibility

    def _compile_and_validate(
        self,
        *,
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        raw_blueprint: str,
        snapshot: MetaPlannerCapabilitySnapshot,
        target: XpertDefinition | None,
    ) -> tuple[
        GraphIntentV3 | None,
        dict[str, Any],
        dict[str, Any],
        list[str],
        ResolvedGraphIRV3 | None,
        MetaPlannerIRCompatibility,
    ]:
        compatibility = MetaPlannerIRCompatibility(source_version=3)
        # A parsed Intent remains eligible for the one typed Patch repair even
        # when a later semantic gate rejects it.
        blueprint: GraphIntentV3 | None = None
        try:
            payload = _json_payload(raw_blueprint)
            if payload.get("ir_version") == 2:
                issue = (
                    "Legacy V2 IR is read-only compatibility input and is not "
                    "valid for a new V3 generation. Return GraphIntentV3 with "
                    "ir_version=3."
                )
                return (
                    None,
                    {},
                    {"valid": False, "issues": [issue]},
                    [issue],
                    None,
                    MetaPlannerIRCompatibility(
                        source_version=2,
                        warnings=[issue],
                    ),
                )
            else:
                blueprint = GraphIntentV3.model_validate(payload)
            issues = validate_blueprint_authorization(
                request, plan, blueprint, snapshot
            )
            if issues:
                return (
                    blueprint,
                    {},
                    {"valid": False, "issues": issues},
                    issues,
                    None,
                    compatibility,
                )
            graph_ir = resolve_graph_intent(
                blueprint,
                snapshot,
                default_agent_model_id=request.default_agent_model_id,
            )
            candidate = compile_xpert_candidate(
                request=request,
                plan=plan,
                blueprint=blueprint,
                snapshot=snapshot,
                target=target,
            )
            validation = _validation_report(
                candidate,
                target=target,
                preflight=self.preflight,
            )
            errors = [
                str(issue.get("message") or issue)
                for issue in validation.get("issues", [])
            ]
            return (
                blueprint,
                candidate,
                validation,
                errors,
                graph_ir,
                compatibility,
            )
        except Exception as exc:
            message = _safe_exception_message(exc)
            return (
                blueprint,
                {},
                {"valid": False, "issues": [message]},
                [message],
                None,
                compatibility,
            )

    @staticmethod
    def _plan_prompt(
        request: MetaPlannerGenerateRequest,
        snapshot: MetaPlannerCapabilitySnapshot,
    ) -> str:
        required_schema = MetaPlannerTaskPlan.model_json_schema()
        task_schema = (
            required_schema.get("$defs", {}).get("MetaPlannerTask", {})
            if isinstance(required_schema.get("$defs"), dict)
            else {}
        )
        properties = task_schema.get("properties")
        if isinstance(properties, dict):
            for field_name in ("task_type", "interaction_prompt", "output_variable"):
                properties.pop(field_name, None)
        required = task_schema.get("required")
        if isinstance(required, list):
            task_schema["required"] = [
                field_name
                for field_name in required
                if field_name
                not in {"task_type", "interaction_prompt", "output_variable"}
            ]
        return json.dumps(
            {
                "goal": request.goal,
                "mode": request.mode,
                "max_tasks": 8,
                "max_workflow_agents": request.max_agents,
                "authorized_agent_ids": list(request.scope.agent_ids),
                "task_planning_contract": _task_planning_contract(
                    request, snapshot
                ),
                "required_schema": required_schema,
                "rules": [
                    "agent_id may only use an exact value from authorized_agent_ids.",
                    "When authorized_agent_ids is empty, omit agent_id or set it to null for every task.",
                    "Never invent descriptive role names as agent_id values.",
                    "Use no more than max_workflow_agents distinct non-null agent_id values.",
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _blueprint_prompt(
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        snapshot: MetaPlannerCapabilitySnapshot,
        target: XpertDefinition | None,
    ) -> str:
        prompt_snapshot = _planner_prompt_snapshot(request, snapshot)
        target_summary = None
        if target is not None:
            target_summary = {
                "id": target.id,
                "name": target.name,
                "description": target.description,
                "draft_revision": target.draft_revision,
                "workflow": target.draft.workflow.model_dump(mode="json"),
            }
        return json.dumps(
            {
                "goal": request.goal,
                "task_plan": plan.model_dump(mode="json"),
                "default_agent_model_id": request.default_agent_model_id,
                "authorized_scope": request.scope.model_dump(mode="json"),
                "capability_snapshot": prompt_snapshot,
                "graph_intent_contract": prompt_snapshot[
                    "graph_intent_contract"
                ],
                "target_xpert": target_summary,
                "required_schema": GraphIntentV3.model_json_schema(),
                "canonical_minimal_example": _canonical_graph_intent_example(
                    request, plan
                ),
                "typed_ir_constraints": _typed_ir_prompt_constraints(request, plan),
                "rules": [
                    "Use only executable node kinds marked compilable in the snapshot.",
                    "A workflow_agent may cover multiple task_ids and a task may use multiple nodes.",
                    "Declare every control edge, typed input/output binding, and the final output explicitly.",
                    "Every input binding must identify source_ref and source_port.",
                    "Use target port task for every workflow_agent input; the port accepts many variables.",
                    "Do not emit input, output, or resource nodes inside nodes; the compiler creates them from bindings.",
                    "Use resource and middleware IDs only from authorized_scope.",
                    "Resource and middleware bindings target workflow_agent node refs.",
                    "Reference dependency outputs in task_input using {{variable}}.",
                    "Do not include credentials, hidden reasoning, or raw private data.",
                    "When uncertain, preserve the exact shape of canonical_minimal_example and adapt its content; never change it to V2.",
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _repair_prompt(
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        snapshot: MetaPlannerCapabilitySnapshot,
        raw_blueprint: str,
        issues: list[str],
    ) -> str:
        prompt_snapshot = _planner_prompt_snapshot(request, snapshot)
        resources = prompt_snapshot["resources"]
        return json.dumps(
            {
                "goal": request.goal,
                "task_plan": plan.model_dump(mode="json"),
                "default_agent_model_id": request.default_agent_model_id,
                "authorized_scope": request.scope.model_dump(mode="json"),
                "capability_snapshot_hash": snapshot.snapshot_hash,
                "capability_snapshot": prompt_snapshot,
                "graph_intent_contract": prompt_snapshot[
                    "graph_intent_contract"
                ],
                "available_resources": {
                    **resources,
                    "middleware": prompt_snapshot["middleware"],
                },
                "invalid_blueprint": raw_blueprint[:30_000],
                "validation_issues": issues[:30],
                "required_schema": GraphIntentV3.model_json_schema(),
                "canonical_minimal_example": _canonical_graph_intent_example(
                    request, plan
                ),
                "typed_ir_constraints": _typed_ir_prompt_constraints(request, plan),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _patch_repair_prompt(
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        snapshot: MetaPlannerCapabilitySnapshot,
        blueprint: GraphIntentV3,
        issues: list[str],
        *,
        expected_graph_checksum: str,
        expected_candidate_checksum: str,
    ) -> str:
        prompt_snapshot = _planner_prompt_snapshot(request, snapshot)
        return json.dumps(
            {
                "goal": request.goal,
                "task_plan": plan.model_dump(mode="json"),
                "authorized_scope": request.scope.model_dump(mode="json"),
                "capability_snapshot_hash": snapshot.snapshot_hash,
                "capability_snapshot": prompt_snapshot,
                "base_graph_intent": blueprint.model_dump(mode="json"),
                "validation_issues": issues[:30],
                "required_schema": GraphPatchEnvelopeV1.model_json_schema(),
                "typed_ir_constraints": _typed_ir_prompt_constraints(
                    request, plan
                ),
                "repair_contract": _graph_patch_repair_contract(
                    request, blueprint, issues
                ),
                "required_envelope": {
                    "protocol_version": 1,
                    "proposal_revision": 1,
                    "expected_graph_checksum": expected_graph_checksum,
                    "expected_candidate_checksum": expected_candidate_checksum,
                },
                "rules": [
                    "Return GraphPatchEnvelopeV1, never a complete GraphIntent or Native Workflow.",
                    "Copy every required_envelope value exactly.",
                    "Use Adapter config only; do not emit resource versions, Handles, schemas, policies, or native node IDs.",
                    "Obey repair_contract ref scopes and issue_playbook exactly.",
                    "Do not change the fixed task plan or add unauthorized capabilities.",
                    "This is the only repair pass.",
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _response(
        *,
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        candidate: dict[str, Any],
        proposal: AuthoringProposal,
        validation: dict[str, Any],
        warnings: list[str],
        repair_used: bool,
        snapshot: MetaPlannerCapabilitySnapshot,
        graph_ir: ResolvedGraphIRV3 | None,
        compatibility: MetaPlannerIRCompatibility,
    ) -> MetaPlannerGenerateResponse:
        return MetaPlannerGenerateResponse(
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.revision,
            mode=request.mode,
            target_xpert_id=request.target_xpert_id,
            base_revision=proposal.base_revision,
            plan=plan,
            candidate=candidate,
            validation=validation,
            warnings=warnings,
            repair_used=repair_used,
            capability_snapshot_version=snapshot.version,
            capability_snapshot_hash=snapshot.snapshot_hash,
            ir_version=GRAPH_IR_VERSION,
            graph_ir=(
                graph_ir.model_dump(mode="json") if graph_ir is not None else None
            ),
            graph_ir_checksum=(
                graph_ir.graph_checksum if graph_ir is not None else ""
            ),
            compatibility=compatibility,
        )
