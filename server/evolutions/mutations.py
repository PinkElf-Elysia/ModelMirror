from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any

try:
    from server.meta_agent.schemas import MetaPlannerCapabilitySnapshot
    from server.workflow_native.node_contracts import node_policy_service
    from server.workflow_native.schemas import (
        NativeWorkflowDefinition,
        NativeWorkflowEdge,
        NativeWorkflowNode,
        WorkflowPosition,
    )
    from server.workflow_native.validate import (
        is_non_control_binding_edge,
        node_kind,
        validate_workflow_graph,
    )
    from server.xperts.models import XpertDefinition
except ModuleNotFoundError:
    from meta_agent.schemas import MetaPlannerCapabilitySnapshot
    from workflow_native.node_contracts import node_policy_service
    from workflow_native.schemas import (
        NativeWorkflowDefinition,
        NativeWorkflowEdge,
        NativeWorkflowNode,
        WorkflowPosition,
    )
    from workflow_native.validate import (
        is_non_control_binding_edge,
        node_kind,
        validate_workflow_graph,
    )
    from xperts.models import XpertDefinition

from .models import (
    EvolutionMutationPolicy,
    EvolutionStructureScope,
    StructureMutation,
)
from .store import EvolutionStateError


SAFE_CONTROL_NODE_KINDS = frozenset(node_policy_service.evolution_control_kinds())
RESOURCE_KINDS = {
    "external_xpert",
    "knowledge_base",
    "toolset_resource",
    "plugin_resource",
}
PROTECTED_NODE_KINDS = {"input", "output"}
BINDING_HANDLES = {
    "external_xpert": ("expert-binding", "expert"),
    "knowledge_base": ("knowledge-binding", "knowledge"),
    "toolset_resource": ("toolset-binding", "toolset"),
    "plugin_resource": ("plugin-binding", "plugin"),
    "runtime_middleware": ("middleware-binding", "middleware"),
}
RESOURCE_SCOPE_FIELDS = {
    "external_xpert": "external_xpert_ids",
    "knowledge_base": "knowledge_base_ids",
    "toolset_resource": "toolset_ids",
    "plugin_resource": "plugin_ids",
}
LOCAL_REF_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users|var|etc)/)[^\s]+"),
]


class StructureMutationCompiler:
    """Applies typed graph mutations without executing model-authored code."""

    def __init__(
        self,
        *,
        snapshot: MetaPlannerCapabilitySnapshot,
        scope: EvolutionStructureScope,
        policy: EvolutionMutationPolicy,
        default_agent_model_id: str,
        candidate_seed: str,
    ) -> None:
        self.snapshot = snapshot
        self.scope = scope
        self.policy = policy
        self.default_agent_model_id = str(default_agent_model_id or "").strip()
        self.candidate_seed = candidate_seed
        self._local_refs: dict[str, str] = {}
        self._added_node_ids: set[str] = set()
        self._removed_baseline_node_ids: set[str] = set()

        snapshot_node_kinds = {str(item.get("kind") or "") for item in snapshot.nodes}
        requested = set(scope.allowed_node_kinds)
        if not requested:
            requested = SAFE_CONTROL_NODE_KINDS & snapshot_node_kinds
        self.allowed_control_kinds = (
            requested & SAFE_CONTROL_NODE_KINDS & snapshot_node_kinds
        )
        unavailable = sorted(set(scope.allowed_node_kinds) - self.allowed_control_kinds)
        if unavailable:
            raise EvolutionStateError(
                "Structure scope includes unsafe or unavailable node kinds: "
                + ", ".join(unavailable)
            )

        self.nodes_by_kind = {
            str(item.get("kind") or ""): copy.deepcopy(item)
            for item in snapshot.nodes
        }
        self.middleware_by_id = {
            str(item.get("id") or ""): copy.deepcopy(item)
            for item in snapshot.middleware
        }
        self.resources_by_kind = {
            "external_xpert": {
                str(item.get("id") or ""): copy.deepcopy(item)
                for item in snapshot.external_xperts
            },
            "knowledge_base": {
                str(item.get("id") or ""): copy.deepcopy(item)
                for item in snapshot.knowledge_bases
            },
            "toolset_resource": {
                str(item.get("id") or ""): copy.deepcopy(item)
                for item in snapshot.toolsets
            },
            "plugin_resource": {
                str(item.get("id") or ""): copy.deepcopy(item)
                for item in snapshot.plugins
            },
        }

    def apply(
        self,
        baseline: XpertDefinition,
        mutations: list[StructureMutation],
    ) -> tuple[XpertDefinition, dict[str, Any]]:
        if not mutations:
            raise EvolutionStateError("Structure candidate requires mutations.")
        if len(mutations) > self.policy.max_operations_per_candidate:
            raise EvolutionStateError(
                "Structure candidate exceeds max_operations_per_candidate."
            )
        unsupported = sorted(
            {item.op for item in mutations}
            - set(self.policy.allowed_operations)
        )
        if unsupported:
            raise EvolutionStateError(
                "Structure candidate uses disabled operations: "
                + ", ".join(unsupported)
            )

        candidate = baseline.model_copy(deep=True)
        workflow = candidate.draft.workflow
        baseline_nodes = {node.id: node.model_copy(deep=True) for node in workflow.nodes}
        baseline_issue_keys = {
            (
                issue.code,
                issue.message,
                issue.node_id,
                issue.edge_id,
            )
            for issue in validate_workflow_graph(
                baseline.draft.workflow
            ).issues
            if issue.severity == "error"
        }
        manifest: list[dict[str, Any]] = []
        for index, mutation in enumerate(mutations, start=1):
            handler = getattr(self, f"_apply_{mutation.op}")
            detail = handler(workflow, mutation, index)
            manifest.append({"op": mutation.op, **detail})

        if len(self._added_node_ids) > self.policy.max_added_nodes:
            raise EvolutionStateError("Structure candidate adds too many nodes.")
        if len(self._removed_baseline_node_ids) > self.policy.max_removed_nodes:
            raise EvolutionStateError("Structure candidate removes too many nodes.")
        if len(workflow.nodes) > 80 or len(workflow.edges) > 120:
            raise EvolutionStateError("Structure candidate exceeds workflow limits.")

        self._layout(workflow, baseline_nodes)
        validation = validate_workflow_graph(workflow)
        errors = [
            issue.message
            for issue in validation.issues
            if issue.severity == "error"
            and (
                issue.code,
                issue.message,
                issue.node_id,
                issue.edge_id,
            )
            not in baseline_issue_keys
        ]
        if errors:
            raise EvolutionStateError(
                "Structure mutation produced an invalid workflow: "
                + "; ".join(errors[:10])
            )
        diff = self.graph_diff(
            baseline.draft.workflow,
            workflow,
            manifest=manifest,
        )
        return candidate, diff

    def _apply_add_control_node(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        ref = self._require_new_ref(mutation.ref)
        kind = str(mutation.kind or "").strip()
        if kind not in self.allowed_control_kinds:
            raise EvolutionStateError(f"Control node kind is not authorized: {kind}")
        node_id = self._stable_id("node", ref, index)
        data = self._control_node_data(kind, mutation.data, ref)
        workflow.nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type=kind,
                position=WorkflowPosition(x=0, y=0),
                data=data,
            )
        )
        self._local_refs[ref] = node_id
        self._added_node_ids.add(node_id)
        return {"ref": ref, "node_id": node_id, "kind": kind}

    def _apply_remove_control_node(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        del index
        node_id = self._resolve_ref(mutation.node_id)
        node = self._require_node(workflow, node_id)
        kind = node_kind(node)
        if kind in PROTECTED_NODE_KINDS or kind in RESOURCE_KINDS | {
            "runtime_middleware"
        }:
            raise EvolutionStateError(f"Node cannot be removed by control mutation: {node_id}")
        workflow.nodes = [item for item in workflow.nodes if item.id != node_id]
        workflow.edges = [
            edge
            for edge in workflow.edges
            if edge.source != node_id and edge.target != node_id
        ]
        if node_id in self._added_node_ids:
            self._added_node_ids.remove(node_id)
        else:
            self._removed_baseline_node_ids.add(node_id)
        return {"node_id": node_id, "kind": kind}

    def _apply_replace_control_node(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        del index
        node_id = self._resolve_ref(mutation.node_id)
        node = self._require_node(workflow, node_id)
        previous_kind = node_kind(node)
        kind = str(mutation.kind or "").strip()
        if previous_kind in PROTECTED_NODE_KINDS | {"workflow_agent"}:
            raise EvolutionStateError(
                "Input, output, and existing workflow_agent nodes cannot be replaced."
            )
        if previous_kind in RESOURCE_KINDS | {"runtime_middleware"}:
            raise EvolutionStateError("Resource bindings require dedicated operations.")
        if kind not in self.allowed_control_kinds:
            raise EvolutionStateError(f"Replacement node kind is not authorized: {kind}")
        node.type = kind
        node.data = self._control_node_data(kind, mutation.data, node_id)
        return {
            "node_id": node_id,
            "from_kind": previous_kind,
            "to_kind": kind,
        }

    def _apply_add_control_edge(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        source = self._resolve_ref(mutation.source)
        target = self._resolve_ref(mutation.target)
        source_node = self._require_node(workflow, source)
        target_node = self._require_node(workflow, target)
        if node_kind(source_node) in RESOURCE_KINDS | {"runtime_middleware"}:
            raise EvolutionStateError("Resource nodes cannot create control edges.")
        if node_kind(target_node) in RESOURCE_KINDS | {"runtime_middleware", "input"}:
            raise EvolutionStateError("Invalid control edge target.")
        source_handle = str(mutation.source_handle or "").strip() or None
        target_handle = str(mutation.target_handle or "").strip() or None
        if source_handle and source_handle.endswith("-binding"):
            raise EvolutionStateError("Binding handles require dedicated operations.")
        if target_handle in {"expert", "knowledge", "toolset", "plugin", "middleware"}:
            raise EvolutionStateError("Binding handles require dedicated operations.")
        edge_id = self._stable_id("edge", f"{source}:{target}", index)
        if any(edge.id == edge_id for edge in workflow.edges):
            raise EvolutionStateError("Generated control edge ID already exists.")
        workflow.edges.append(
            NativeWorkflowEdge(
                id=edge_id,
                source=source,
                target=target,
                sourceHandle=source_handle,
                targetHandle=target_handle,
            )
        )
        return {
            "edge_id": edge_id,
            "source": source,
            "target": target,
            "source_handle": source_handle,
        }

    def _apply_remove_control_edge(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        del index
        edge_id = str(mutation.edge_id or "").strip()
        edge = next((item for item in workflow.edges if item.id == edge_id), None)
        if edge is None:
            raise EvolutionStateError(f"Control edge not found: {edge_id}")
        if is_non_control_binding_edge(edge):
            raise EvolutionStateError("Binding edges require dedicated operations.")
        workflow.edges = [item for item in workflow.edges if item.id != edge_id]
        return {"edge_id": edge_id, "source": edge.source, "target": edge.target}

    def _apply_bind_resource(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        ref = self._require_new_ref(mutation.ref)
        kind = str(mutation.kind or "").strip()
        if kind not in RESOURCE_KINDS:
            raise EvolutionStateError(f"Unsupported resource binding kind: {kind}")
        resource_id = str(mutation.resource_id or "").strip()
        scope_field = RESOURCE_SCOPE_FIELDS[kind]
        if resource_id not in set(getattr(self.scope, scope_field)):
            raise EvolutionStateError(f"Resource is not authorized: {resource_id}")
        resource = self.resources_by_kind[kind].get(resource_id)
        if resource is None:
            raise EvolutionStateError(f"Resource is unavailable: {resource_id}")
        agent_id = self._resolve_ref(mutation.agent_node_id)
        agent = self._require_workflow_agent(workflow, agent_id)
        node_id = self._stable_id("resource", ref, index)
        data = self._resource_data(kind, resource_id, resource, mutation.data)
        workflow.nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type=kind,
                position=WorkflowPosition(x=0, y=0),
                data=data,
            )
        )
        source_handle, target_handle = BINDING_HANDLES[kind]
        edge_id = self._stable_id("binding", f"{node_id}:{agent_id}", index)
        workflow.edges.append(
            NativeWorkflowEdge(
                id=edge_id,
                source=node_id,
                target=agent_id,
                sourceHandle=source_handle,
                targetHandle=target_handle,
            )
        )
        agent.data["toolMode"] = "mcp_tools"
        self._local_refs[ref] = node_id
        self._added_node_ids.add(node_id)
        return {
            "ref": ref,
            "node_id": node_id,
            "kind": kind,
            "resource_id": resource_id,
            "agent_node_id": agent_id,
        }

    def _apply_unbind_resource(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        del index
        node_id = self._resolve_ref(mutation.node_id)
        node = self._require_node(workflow, node_id)
        kind = node_kind(node)
        if kind not in RESOURCE_KINDS:
            raise EvolutionStateError("unbind_resource requires a resource node.")
        workflow.nodes = [item for item in workflow.nodes if item.id != node_id]
        workflow.edges = [
            edge
            for edge in workflow.edges
            if edge.source != node_id and edge.target != node_id
        ]
        if node_id in self._added_node_ids:
            self._added_node_ids.remove(node_id)
        else:
            self._removed_baseline_node_ids.add(node_id)
        return {"node_id": node_id, "kind": kind}

    def _apply_bind_middleware(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        ref = self._require_new_ref(mutation.ref)
        middleware_id = str(mutation.middleware_id or "").strip()
        if middleware_id not in set(self.scope.middleware_ids):
            raise EvolutionStateError(f"Middleware is not authorized: {middleware_id}")
        middleware = self.middleware_by_id.get(middleware_id)
        if middleware is None or bool(middleware.get("high_risk")):
            raise EvolutionStateError(f"Middleware is unsafe or unavailable: {middleware_id}")
        agent_id = self._resolve_ref(mutation.agent_node_id)
        agent = self._require_workflow_agent(workflow, agent_id)
        defaults = dict(middleware.get("default_config") or {})
        config = {**defaults, **dict(mutation.config or {})}
        self._validate_safe_values(config)
        node_id = self._stable_id("middleware", ref, index)
        workflow.nodes.append(
            NativeWorkflowNode(
                id=node_id,
                type="runtime_middleware",
                position=WorkflowPosition(x=0, y=0),
                data={
                    "kind": "runtime_middleware",
                    "title": str(middleware.get("title") or middleware_id)[:120],
                    "description": str(middleware.get("description") or "")[:1_000],
                    "runtimeMiddlewareId": middleware_id,
                    "runtimeMiddlewareKind": str(
                        middleware.get("kind") or f"runtime_middleware.{middleware_id}"
                    ),
                    "runtimeMiddlewareFields": list(middleware.get("fields") or []),
                    "runtimeMiddlewareMetadata": {
                        "config_version": middleware.get("config_version"),
                        "security_category": middleware.get("security_category"),
                    },
                    "runtimeMiddlewareConfig": config,
                    "middlewarePriority": str(mutation.priority),
                },
            )
        )
        source_handle, target_handle = BINDING_HANDLES["runtime_middleware"]
        edge_id = self._stable_id("binding", f"{node_id}:{agent_id}", index)
        workflow.edges.append(
            NativeWorkflowEdge(
                id=edge_id,
                source=node_id,
                target=agent_id,
                sourceHandle=source_handle,
                targetHandle=target_handle,
            )
        )
        if middleware.get("requires_tool_mode"):
            agent.data["toolMode"] = "mcp_tools"
        self._local_refs[ref] = node_id
        self._added_node_ids.add(node_id)
        return {
            "ref": ref,
            "node_id": node_id,
            "middleware_id": middleware_id,
            "agent_node_id": agent_id,
        }

    def _apply_unbind_middleware(
        self,
        workflow: NativeWorkflowDefinition,
        mutation: StructureMutation,
        index: int,
    ) -> dict[str, Any]:
        del index
        node_id = self._resolve_ref(mutation.node_id)
        node = self._require_node(workflow, node_id)
        if node_kind(node) != "runtime_middleware":
            raise EvolutionStateError(
                "unbind_middleware requires a runtime_middleware node."
            )
        workflow.nodes = [item for item in workflow.nodes if item.id != node_id]
        workflow.edges = [
            edge
            for edge in workflow.edges
            if edge.source != node_id and edge.target != node_id
        ]
        if node_id in self._added_node_ids:
            self._added_node_ids.remove(node_id)
        else:
            self._removed_baseline_node_ids.add(node_id)
        return {
            "node_id": node_id,
            "middleware_id": str(node.data.get("runtimeMiddlewareId") or ""),
        }

    def _control_node_data(
        self,
        kind: str,
        supplied: dict[str, Any],
        ref: str,
    ) -> dict[str, Any]:
        payload = copy.deepcopy(
            (self.nodes_by_kind.get(kind, {}).get("planner") or {}).get(
                "default_data"
            )
            or {}
        )
        payload.update(copy.deepcopy(supplied or {}))
        payload["kind"] = kind
        payload.setdefault("title", self.nodes_by_kind.get(kind, {}).get("title", kind))
        payload.setdefault(
            "description", self.nodes_by_kind.get(kind, {}).get("description", "")
        )
        suffix = re.sub(r"[^A-Za-z0-9_]", "_", ref)[:48] or "step"
        defaults: dict[str, dict[str, Any]] = {
            "llm": {
                "modelId": self.default_agent_model_id,
                "prompt": "{{user_input}}",
                "outputVariable": f"{suffix}_output",
            },
            "condition": {
                "conditionVariable": "user_input",
                "conditionOperator": "contains",
                "conditionValue": "",
            },
            "variable_assign": {
                "variableName": f"{suffix}_value",
                "template": "{{user_input}}",
            },
            "template_transform": {
                "template": "{{user_input}}",
                "outputVariable": f"{suffix}_output",
            },
            "variable_aggregator": {
                "variableNames": "user_input",
                "outputTemplate": "{name}={value}\n",
                "outputVariable": f"{suffix}_output",
            },
            "parameter_extractor": {
                "inputVariable": "user_input",
                "schema": "result: extracted result",
                "modelId": self.default_agent_model_id,
                "outputVariable": f"{suffix}_output",
            },
            "question_classifier": {
                "inputVariable": "user_input",
                "categories": '{"default":[]}',
                "outputVariable": f"{suffix}_category",
                "defaultCategory": "default",
                "matchMode": "contains_any",
                "caseSensitive": "false",
                "useLlmFallback": "false",
                "modelId": "",
                "llmFallbackPrompt": "",
            },
            "list_operation": {
                "inputVariable": "user_input",
                "operator": "length",
                "joinSeparator": " / ",
                "outputVariable": f"{suffix}_output",
            },
            "iteration": {
                "inputVariable": "user_input",
                "iterationVariable": "item",
                "itemTemplate": "{{item}}",
                "outputVariable": f"{suffix}_output",
            },
            "workflow_agent": {
                "agentName": suffix,
                "modelId": self.default_agent_model_id,
                "rolePrompt": "Execute this workflow step and return only its result.",
                "taskInput": "{{user_input}}",
                "toolMode": "none",
                "toolNames": "",
                "maxIterations": "5",
                "promptSuffix": "",
                "outputVariable": f"{suffix}_output",
                "disableOutput": "false",
                "enableFileUnderstanding": "false",
                "parallelToolCalls": "false",
                "maxToolConcurrency": "2",
                "maxToolCalls": "12",
                "maxToolDepth": "4",
                "retryOnFailure": "false",
                "fallbackModelId": "",
                "exceptionHandling": "none",
                "outputSchemaMode": "default",
                "outputSchemaJson": "",
                "memoryReadEnabled": "false",
                "memoryReadScope": "both",
                "memoryWriteEnabled": "false",
                "memoryWriteTarget": "xpert",
                "knowledgeReadEnabled": "false",
                "knowledgeWriteEnabled": "false",
                "knowledgeBaseIds": "",
                "nodeParametersJson": "[]",
            },
        }
        payload = {**defaults.get(kind, {}), **payload}
        if kind in {"llm", "parameter_extractor", "workflow_agent"}:
            payload["modelId"] = self.default_agent_model_id
        self._validate_safe_values(payload)
        for key in (
            "outputVariable",
            "variableName",
            "codeOutputVariable",
        ):
            value = str(payload.get(key) or "").strip()
            if value and not VARIABLE_PATTERN.fullmatch(value):
                raise EvolutionStateError(f"Invalid generated variable name: {value}")
        return payload

    def _resource_data(
        self,
        kind: str,
        resource_id: str,
        resource: dict[str, Any],
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        description = str(supplied.get("description") or resource.get("description") or "")
        if kind == "external_xpert":
            version = int(resource.get("version") or 0)
            if version < 1:
                raise EvolutionStateError("External Xpert has no published version.")
            tool_name = str(
                supplied.get("toolName")
                or (resource.get("metadata") or {}).get("slug")
                or resource.get("name")
                or "external_expert"
            )
            tool_name = re.sub(r"[^A-Za-z0-9_-]", "_", tool_name)[:80]
            return {
                "kind": kind,
                "title": str(resource.get("name") or "External Xpert")[:120],
                "description": description[:1_000],
                "xpertId": resource_id,
                "toolName": tool_name,
                "versionPolicy": "pinned",
                "pinnedVersion": str(version),
            }
        if kind == "knowledge_base":
            top_k = max(1, min(int(supplied.get("topK") or 5), 50))
            threshold = max(
                0.0, min(float(supplied.get("scoreThreshold") or 0), 1.0)
            )
            return {
                "kind": kind,
                "title": str(resource.get("name") or "Knowledge Base")[:120],
                "description": description[:1_000],
                "knowledgeBaseId": resource_id,
                "topK": str(top_k),
                "scoreThreshold": str(threshold),
            }
        version = int(resource.get("version") or 0)
        if version < 1:
            raise EvolutionStateError("Bound resource has no published version.")
        id_field = "toolsetId" if kind == "toolset_resource" else "pluginId"
        return {
            "kind": kind,
            "title": str(resource.get("name") or kind)[:120],
            "description": description[:1_000],
            id_field: resource_id,
            "versionPolicy": "pinned",
            "pinnedVersion": str(version),
        }

    def _require_new_ref(self, value: str | None) -> str:
        ref = str(value or "").strip()
        if not LOCAL_REF_PATTERN.fullmatch(ref):
            raise EvolutionStateError(f"Invalid candidate-local ref: {ref}")
        if ref in self._local_refs:
            raise EvolutionStateError(f"Duplicate candidate-local ref: {ref}")
        return ref

    def _resolve_ref(self, value: str | None) -> str:
        text = str(value or "").strip()
        if text in self._local_refs:
            return self._local_refs[text]
        if not text:
            raise EvolutionStateError("Mutation is missing a node reference.")
        return text

    @staticmethod
    def _require_node(
        workflow: NativeWorkflowDefinition,
        node_id: str,
    ) -> NativeWorkflowNode:
        node = next((item for item in workflow.nodes if item.id == node_id), None)
        if node is None:
            raise EvolutionStateError(f"Workflow node not found: {node_id}")
        return node

    def _require_workflow_agent(
        self,
        workflow: NativeWorkflowDefinition,
        node_id: str,
    ) -> NativeWorkflowNode:
        node = self._require_node(workflow, node_id)
        if node_kind(node) != "workflow_agent":
            raise EvolutionStateError(
                f"Resource and middleware bindings require workflow_agent: {node_id}"
            )
        return node

    def _stable_id(self, prefix: str, value: str, index: int) -> str:
        digest = hashlib.sha256(
            f"{self.candidate_seed}:{prefix}:{index}:{value}".encode("utf-8")
        ).hexdigest()[:12]
        return f"evo-{prefix}-{digest}"

    @staticmethod
    def _validate_safe_values(value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 100_000:
            raise EvolutionStateError("Mutation configuration is too large.")
        for pattern in SENSITIVE_VALUE_PATTERNS:
            if pattern.search(encoded):
                raise EvolutionStateError(
                    "Mutation configuration failed sensitive-value checks."
                )

    @staticmethod
    def _layout(
        workflow: NativeWorkflowDefinition,
        baseline_nodes: dict[str, NativeWorkflowNode],
    ) -> None:
        node_ids = {node.id for node in workflow.nodes}
        control_edges = [
            edge
            for edge in workflow.edges
            if edge.source in node_ids
            and edge.target in node_ids
            and not is_non_control_binding_edge(edge)
        ]
        indegree = {node_id: 0 for node_id in node_ids}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in control_edges:
            indegree[edge.target] += 1
            outgoing[edge.source].append(edge.target)
        queue = deque(sorted(node_id for node_id, value in indegree.items() if value == 0))
        layer = {node_id: 0 for node_id in queue}
        while queue:
            current = queue.popleft()
            for target in sorted(outgoing[current]):
                layer[target] = max(layer.get(target, 0), layer[current] + 1)
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)

        per_layer: dict[int, list[NativeWorkflowNode]] = defaultdict(list)
        resource_nodes: list[NativeWorkflowNode] = []
        for node in workflow.nodes:
            if node.id in baseline_nodes and baseline_nodes[node.id].position is not None:
                node.position = baseline_nodes[node.id].position.model_copy(deep=True)
                continue
            if node_kind(node) in RESOURCE_KINDS | {"runtime_middleware"}:
                resource_nodes.append(node)
            else:
                per_layer[layer.get(node.id, 0)].append(node)
        for level, nodes in sorted(per_layer.items()):
            for index, node in enumerate(sorted(nodes, key=lambda item: item.id)):
                node.position = WorkflowPosition(x=160 + level * 280, y=120 + index * 180)

        binding_edges = {
            edge.source: edge.target
            for edge in workflow.edges
            if is_non_control_binding_edge(edge)
        }
        target_offsets: dict[str, int] = defaultdict(int)
        by_id = {node.id: node for node in workflow.nodes}
        for node in sorted(resource_nodes, key=lambda item: item.id):
            target_id = binding_edges.get(node.id)
            target = by_id.get(target_id or "")
            offset = target_offsets[target_id or ""]
            target_offsets[target_id or ""] += 1
            base = target.position if target and target.position else WorkflowPosition()
            node.position = WorkflowPosition(
                x=base.x - 260,
                y=base.y + 130 + offset * 150,
            )

    @staticmethod
    def graph_checksum(workflow: NativeWorkflowDefinition) -> str:
        payload = {
            "nodes": sorted(
                (
                    {
                        "id": node.id,
                        "kind": node_kind(node),
                        "data": node.data,
                    }
                    for node in workflow.nodes
                ),
                key=lambda item: item["id"],
            ),
            "edges": sorted(
                (
                    {
                        "id": edge.id,
                        "source": edge.source,
                        "target": edge.target,
                        "sourceHandle": edge.sourceHandle,
                        "targetHandle": edge.targetHandle,
                    }
                    for edge in workflow.edges
                ),
                key=lambda item: item["id"],
            ),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def graph_diff(
        baseline: NativeWorkflowDefinition,
        candidate: NativeWorkflowDefinition,
        *,
        manifest: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline_nodes = {node.id: node for node in baseline.nodes}
        candidate_nodes = {node.id: node for node in candidate.nodes}
        baseline_edges = {edge.id: edge for edge in baseline.edges}
        candidate_edges = {edge.id: edge for edge in candidate.edges}
        added_nodes = sorted(set(candidate_nodes) - set(baseline_nodes))
        removed_nodes = sorted(set(baseline_nodes) - set(candidate_nodes))
        replaced_nodes = sorted(
            node_id
            for node_id in set(baseline_nodes) & set(candidate_nodes)
            if node_kind(baseline_nodes[node_id]) != node_kind(candidate_nodes[node_id])
        )
        added_edges = sorted(set(candidate_edges) - set(baseline_edges))
        removed_edges = sorted(set(baseline_edges) - set(candidate_edges))
        return {
            "manifest": copy.deepcopy(manifest),
            "added_nodes": [
                {
                    "node_id": node_id,
                    "kind": node_kind(candidate_nodes[node_id]),
                    "title": str(candidate_nodes[node_id].data.get("title") or "")[:120],
                }
                for node_id in added_nodes
            ],
            "removed_nodes": [
                {
                    "node_id": node_id,
                    "kind": node_kind(baseline_nodes[node_id]),
                    "title": str(baseline_nodes[node_id].data.get("title") or "")[:120],
                }
                for node_id in removed_nodes
            ],
            "replaced_nodes": [
                {
                    "node_id": node_id,
                    "from_kind": node_kind(baseline_nodes[node_id]),
                    "to_kind": node_kind(candidate_nodes[node_id]),
                }
                for node_id in replaced_nodes
            ],
            "added_edge_ids": added_edges,
            "removed_edge_ids": removed_edges,
            "baseline_node_count": len(baseline.nodes),
            "candidate_node_count": len(candidate.nodes),
            "node_delta": len(candidate.nodes) - len(baseline.nodes),
            "baseline_edge_count": len(baseline.edges),
            "candidate_edge_count": len(candidate.edges),
            "edge_delta": len(candidate.edges) - len(baseline.edges),
        }


def public_workflow_graph(
    workflow: NativeWorkflowDefinition,
    diff: dict[str, Any],
) -> dict[str, Any]:
    hidden_fields = {
        "rolePrompt",
        "promptSuffix",
        "prompt",
        "taskInput",
        "instruction",
        "headersJson",
        "argumentsJson",
        "outputSchemaJson",
        "runtimeMiddlewareConfig",
    }
    return {
        "id": workflow.id,
        "title": workflow.title,
        "nodes": [
            {
                "id": node.id,
                "type": node.type,
                "position": (
                    node.position.model_dump(mode="json") if node.position else None
                ),
                "data": {
                    key: copy.deepcopy(value)
                    for key, value in (node.data or {}).items()
                    if key not in hidden_fields
                },
            }
            for node in workflow.nodes
        ],
        "edges": [edge.model_dump(mode="json") for edge in workflow.edges],
        "diff": copy.deepcopy(diff),
    }
