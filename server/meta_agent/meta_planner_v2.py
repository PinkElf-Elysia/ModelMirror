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
    from server.workflow_native.validate import validate_workflow_graph
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
    from workflow_native.validate import validate_workflow_graph
    from xpert_runtime.authoring_service import AuthoringService
    from xpert_runtime.authoring_store import AuthoringProposal
    from xperts.models import XpertDefinition, XpertDraft

from .capabilities import assert_scope_is_authorized
from .planner import extract_json_object_text
from .schemas import (
    MetaPlannerAgentBlueprint,
    MetaPlannerBlueprint,
    MetaPlannerCapabilitySnapshot,
    MetaPlannerGenerateRequest,
    MetaPlannerGenerateResponse,
    MetaPlannerPreviewResponse,
    MetaPlannerTaskPlan,
)


CompletionCallback = Callable[[str, str, str, float, int], Awaitable[str]]
PreflightCallback = Callable[[XpertDefinition], Any]


TASK_PLAN_SYSTEM_PROMPT = """\
You are the task-planning stage of ModelMirror Meta Planner V2.
Return one strict JSON object only. Do not include markdown or hidden reasoning.
Create a bounded task DAG. Every task must have a stable lowercase task_id,
explicit dependencies, an input contract, and one output contract.
"""


BLUEPRINT_SYSTEM_PROMPT = """\
You are the capability-compilation stage of ModelMirror Meta Planner V2.
Return one strict JSON object only. Do not include markdown or hidden reasoning.
Use only IDs and middleware listed in the authorized capability snapshot.
Every planned task must map to exactly one workflow_agent. Resource and middleware
bindings must refer to an existing task_id. Never invent credentials, tools, resource
IDs, node kinds, versions, or private content.
"""


REPAIR_SYSTEM_PROMPT = """\
You repair a ModelMirror Meta Planner blueprint. Return one strict JSON object only.
Make the smallest changes required by the structured validation issues. Do not add
capabilities outside the supplied authorized snapshot. This is the only repair pass.
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


def validate_task_plan(
    plan: MetaPlannerTaskPlan,
    *,
    max_agents: int,
) -> list[str]:
    issues: list[str] = []
    if len(plan.tasks) > max_agents:
        issues.append(f"Task plan exceeds max_agents={max_agents}.")
    task_ids = [task.task_id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        issues.append("Task IDs must be unique.")
    known = set(task_ids)
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


def validate_blueprint_authorization(
    request: MetaPlannerGenerateRequest,
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> list[str]:
    issues = validate_task_plan(plan, max_agents=request.max_agents)
    plan_ids = {task.task_id for task in plan.tasks}
    agent_ids = [agent.task_id for agent in blueprint.agents]
    if set(agent_ids) != plan_ids or len(agent_ids) != len(set(agent_ids)):
        issues.append("Blueprint agents must map one-to-one to planned task IDs.")

    known_agents = {item["id"] for item in snapshot.agents}
    authorized_agents = set(request.scope.agent_ids)
    task_by_id = {task.task_id: task for task in plan.tasks}
    for agent in blueprint.agents:
        task = task_by_id.get(agent.task_id)
        source_agent_id = agent.source_agent_id or (task.agent_id if task else None)
        if task and task.agent_id and agent.source_agent_id != task.agent_id:
            issues.append(
                f"Blueprint task {agent.task_id} must keep assigned expert "
                f"{task.agent_id}."
            )
        if source_agent_id and source_agent_id not in authorized_agents:
            issues.append(f"Expert {source_agent_id} is not authorized.")
        if source_agent_id and source_agent_id not in known_agents:
            issues.append(f"Expert {source_agent_id} is no longer available.")

    scoped = {
        "external_xpert": set(request.scope.external_xpert_ids),
        "knowledge_base": set(request.scope.knowledge_base_ids),
        "toolset_resource": set(request.scope.toolset_ids),
        "plugin_resource": set(request.scope.plugin_ids),
    }
    lookup = _resource_lookup(snapshot)
    seen_external_tools: set[tuple[str, str]] = set()
    for binding in blueprint.resources:
        if binding.task_id not in plan_ids:
            issues.append(
                f"Resource {binding.resource_id} targets unknown task {binding.task_id}."
            )
        if binding.resource_id not in scoped[binding.kind]:
            issues.append(
                f"Resource {binding.resource_id} is not authorized for {binding.kind}."
            )
        if binding.resource_id not in lookup[binding.kind]:
            issues.append(
                f"Resource {binding.resource_id} is no longer available."
            )
        if binding.kind == "external_xpert":
            tool_name = _safe_identifier(
                binding.tool_name or f"xpert_{binding.resource_id[:12]}",
                "external_xpert",
            )
            key = (binding.task_id, tool_name)
            if key in seen_external_tools:
                issues.append(
                    f"External Xpert tool name {tool_name} is duplicated for "
                    f"task {binding.task_id}."
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
    for binding in blueprint.middleware:
        if binding.task_id not in plan_ids:
            issues.append(
                f"Middleware {binding.middleware_id} targets unknown task "
                f"{binding.task_id}."
            )
        if binding.middleware_id not in request.scope.middleware_ids:
            issues.append(
                f"Middleware {binding.middleware_id} is not authorized."
            )
        if binding.middleware_id not in middleware_lookup:
            issues.append(
                f"Middleware {binding.middleware_id} is no longer available."
            )
        key = (binding.task_id, binding.middleware_id)
        if key in seen_middleware:
            issues.append(
                f"Middleware {binding.middleware_id} is duplicated for "
                f"task {binding.task_id}."
            )
        seen_middleware.add(key)

    authorized_prompts = set(request.scope.prompt_profile_ids)
    available_prompts = {item["id"] for item in snapshot.prompt_profiles}
    for profile_id in blueprint.prompt_profile_ids:
        if profile_id not in authorized_prompts:
            issues.append(f"Prompt Profile {profile_id} is not authorized.")
        if profile_id not in available_prompts:
            issues.append(f"Prompt Profile {profile_id} is no longer available.")
    return issues


def compile_xpert_candidate(
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
    final_task_id = sinks[-1]
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

        plan_prompt = self._plan_prompt(request)
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
        plan_issues = validate_task_plan(plan, max_agents=request.max_agents)
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
        blueprint, candidate, validation, issues = self._compile_and_validate(
            request=request,
            plan=plan,
            raw_blueprint=raw_blueprint,
            snapshot=snapshot,
            target=target,
        )
        if issues:
            repair_used = True
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
            blueprint, candidate, validation, issues = self._compile_and_validate(
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
                    candidate = self._fallback_candidate(
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
            "planner_version": "evoagentx-meta-planner-v2",
            "goal": request.goal,
            "mode": request.mode,
            "plan": plan.model_dump(mode="json"),
            "assumptions": list(plan.assumptions),
            "capability_snapshot": {
                "version": snapshot.version,
                "hash": snapshot.snapshot_hash,
            },
            "authorized_scope": request.scope.model_dump(mode="json"),
            "validation": validation,
            "repair_used": repair_used,
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
        return self._response(
            request=request,
            plan=plan,
            candidate=candidate,
            proposal=proposal,
            validation=validation,
            warnings=warnings,
            repair_used=repair_used,
            snapshot=snapshot,
        )

    def preview(
        self,
        request: MetaPlannerGenerateRequest,
        snapshot: MetaPlannerCapabilitySnapshot,
        *,
        plan: MetaPlannerTaskPlan,
        blueprint: MetaPlannerBlueprint,
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
        plan_issues = validate_task_plan(plan, max_agents=request.max_agents)
        blueprint_issues = validate_blueprint_authorization(
            request, plan, blueprint, snapshot
        )
        issues = list(dict.fromkeys([*plan_issues, *blueprint_issues]))
        if issues:
            raise ValueError("; ".join(issues))
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
        )

    @staticmethod
    def _fallback_candidate(
        *,
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        snapshot: MetaPlannerCapabilitySnapshot,
        target: XpertDefinition | None,
    ) -> dict[str, Any]:
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
                node["data"]["modelId"] = ""
                break
        return candidate

    def _compile_and_validate(
        self,
        *,
        request: MetaPlannerGenerateRequest,
        plan: MetaPlannerTaskPlan,
        raw_blueprint: str,
        snapshot: MetaPlannerCapabilitySnapshot,
        target: XpertDefinition | None,
    ) -> tuple[
        MetaPlannerBlueprint | None,
        dict[str, Any],
        dict[str, Any],
        list[str],
    ]:
        try:
            blueprint = MetaPlannerBlueprint.model_validate(
                _json_payload(raw_blueprint)
            )
            issues = validate_blueprint_authorization(
                request, plan, blueprint, snapshot
            )
            if issues:
                return blueprint, {}, {"valid": False, "issues": issues}, issues
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
            return blueprint, candidate, validation, errors
        except Exception as exc:
            message = _safe_exception_message(exc)
            return None, {}, {"valid": False, "issues": [message]}, [message]

    @staticmethod
    def _plan_prompt(request: MetaPlannerGenerateRequest) -> str:
        return json.dumps(
            {
                "goal": request.goal,
                "mode": request.mode,
                "max_agents": request.max_agents,
                "required_schema": MetaPlannerTaskPlan.model_json_schema(),
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
                "capability_snapshot": snapshot.model_dump(mode="json"),
                "target_xpert": target_summary,
                "required_schema": MetaPlannerBlueprint.model_json_schema(),
                "rules": [
                    "Create exactly one agent entry for every task_id.",
                    "Use resource and middleware IDs only from authorized_scope.",
                    "Reference dependency outputs in task_input using {{variable}}.",
                    "Do not include credentials, hidden reasoning, or raw private data.",
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
        return json.dumps(
            {
                "goal": request.goal,
                "task_plan": plan.model_dump(mode="json"),
                "authorized_scope": request.scope.model_dump(mode="json"),
                "capability_snapshot_hash": snapshot.snapshot_hash,
                "available_resources": {
                    "external_xperts": snapshot.external_xperts,
                    "knowledge_bases": snapshot.knowledge_bases,
                    "toolsets": snapshot.toolsets,
                    "plugins": snapshot.plugins,
                    "prompt_profiles": snapshot.prompt_profiles,
                    "middleware": snapshot.middleware,
                },
                "invalid_blueprint": raw_blueprint[:30_000],
                "validation_issues": issues[:30],
                "required_schema": MetaPlannerBlueprint.model_json_schema(),
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
        )
