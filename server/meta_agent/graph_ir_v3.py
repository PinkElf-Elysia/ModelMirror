from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

try:
    from server.workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )

from .schemas import (
    GraphIntentControlEdgeV3,
    GraphIntentFinalOutputV3,
    GraphIntentFinalOutputSourceV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
    MetaPlannerCapabilitySnapshot,
    MetaPlannerIRCompatibility,
    MetaPlannerIRControlEdge,
    MetaPlannerIRFinalOutput,
    MetaPlannerIRInputBinding,
    MetaPlannerIRMiddlewareBinding,
    MetaPlannerIRNode,
    MetaPlannerIROutputBinding,
    MetaPlannerIRResourceBinding,
    MetaPlannerTypedBlueprintV2,
    ResolvedGraphEdgeV3,
    ResolvedGraphEndpointV3,
    ResolvedGraphIRV3,
    ResolvedGraphNodeV3,
    ResolvedGraphPortV3,
    ResolvedPromptProfileV3,
)


GRAPH_IR_VERSION = 3
SUPPORTED_GRAPH_IR_VERSIONS = (2, 3)

_TEMPLATE_PATTERN = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)
_SENSITIVE_CONFIG_KEY = re.compile(
    r"(?:^|[_-])(?:authorization|cookie|credential|password|passwd|secret|"
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_CONFIG_VALUE = re.compile(
    r"(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----)",
    re.IGNORECASE,
)


def _safe_identifier(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized or normalized[0].isdigit():
        normalized = f"{fallback}_{normalized}" if normalized else fallback
    return normalized[:120]


def _stable_ref(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(str(item) for item in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _template_variables(template: str) -> set[str]:
    moustache: set[str] = set()
    for match in _TEMPLATE_PATTERN.finditer(template):
        expression = match.group(1).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression):
            raise ValueError("Template contains an unsupported template expression.")
        moustache.add(expression)
    return moustache


def _contains_sensitive_config(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _SENSITIVE_CONFIG_KEY.search(
                re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
            )
            or _contains_sensitive_config(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_config(item) for item in value)
    return isinstance(value, str) and bool(_SENSITIVE_CONFIG_VALUE.search(value))


def resolve_middleware_config(
    middleware: dict[str, Any], supplied: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        str(field.get("name") or ""): field
        for field in middleware.get("fields") or []
        if isinstance(field, dict) and field.get("name")
    }
    unknown = sorted(set(supplied) - set(fields))
    if unknown:
        raise ValueError(
            "Middleware config fields are not declared: " + ", ".join(unknown)
        )
    if _contains_sensitive_config(supplied):
        raise ValueError("Middleware config contains credential material.")
    resolved = dict(middleware.get("default_config") or {})
    resolved.update(supplied)
    for name, field in fields.items():
        if field.get("required") and name not in resolved:
            raise ValueError(f"Middleware config field {name} is required.")
        if name not in resolved:
            continue
        value = resolved[name]
        field_type = str(field.get("type") or "")
        valid = (
            (
                field_type in {"text", "textarea", "select"}
                and isinstance(value, str)
            )
            or (field_type == "boolean" and isinstance(value, bool))
            or (
                field_type == "number"
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
            or field_type == "json"
        )
        if not valid:
            raise ValueError(
                f"Middleware config field {name} does not match type {field_type}."
            )
        options = field.get("options")
        if options and value not in options:
            raise ValueError(
                f"Middleware config field {name} has an invalid option."
            )
        if field_type == "number":
            minimum = field.get("min_value")
            maximum = field.get("max_value")
            if minimum is not None and value < minimum:
                raise ValueError(
                    f"Middleware config field {name} is below its minimum."
                )
            if maximum is not None and value > maximum:
                raise ValueError(
                    f"Middleware config field {name} exceeds its maximum."
                )
    return resolved


def graph_ir_checksum(graph_ir: ResolvedGraphIRV3 | dict[str, Any]) -> str:
    payload = (
        graph_ir.model_dump(mode="json")
        if isinstance(graph_ir, ResolvedGraphIRV3)
        else deepcopy(graph_ir)
    )
    payload.pop("graph_checksum", None)
    payload["tags"] = sorted(set(payload.get("tags") or []))
    payload["starters"] = sorted(set(payload.get("starters") or []))
    payload["nodes"] = sorted(
        payload.get("nodes") or [], key=lambda item: str(item.get("ref") or "")
    )
    payload["edges"] = sorted(
        payload.get("edges") or [], key=lambda item: str(item.get("ref") or "")
    )
    payload["prompt_profiles"] = sorted(
        payload.get("prompt_profiles") or [],
        key=lambda item: (
            str(item.get("profile_id") or ""),
            int(item.get("pinned_version") or 0),
        ),
    )
    return canonical_checksum(payload)


def graph_authoring_checksum(
    graph_ir: ResolvedGraphIRV3 | dict[str, Any]
) -> str:
    """Hash graph-relevant resolved facts without global Snapshot identity."""

    payload = (
        graph_ir.model_dump(mode="json")
        if isinstance(graph_ir, ResolvedGraphIRV3)
        else deepcopy(graph_ir)
    )
    payload.pop("graph_checksum", None)
    payload.pop("capability_snapshot_version", None)
    payload.pop("capability_snapshot_hash", None)
    payload["tags"] = sorted(set(payload.get("tags") or []))
    payload["starters"] = sorted(set(payload.get("starters") or []))
    payload["nodes"] = sorted(
        payload.get("nodes") or [], key=lambda item: str(item.get("ref") or "")
    )
    payload["edges"] = sorted(
        payload.get("edges") or [], key=lambda item: str(item.get("ref") or "")
    )
    payload["prompt_profiles"] = sorted(
        payload.get("prompt_profiles") or [],
        key=lambda item: (
            str(item.get("profile_id") or ""),
            int(item.get("pinned_version") or 0),
        ),
    )
    return canonical_checksum(payload)


def workflow_semantic_checksum(candidate: dict[str, Any]) -> str:
    workflow = dict(candidate.get("draft", {}).get("workflow") or {})
    payload = {
        "id": workflow.get("id"),
        "version": workflow.get("version"),
        "nodes": sorted(
            [
                {
                    "id": node.get("id"),
                    "type": node.get("type"),
                    "data": node.get("data") or {},
                }
                for node in workflow.get("nodes") or []
            ],
            key=lambda item: str(item["id"]),
        ),
        "edges": sorted(
            [
                {
                    key: edge.get(key)
                    for key in (
                        "id",
                        "source",
                        "target",
                        "sourceHandle",
                        "targetHandle",
                    )
                    if edge.get(key) is not None
                }
                for edge in workflow.get("edges") or []
            ],
            key=lambda item: str(item["id"]),
        ),
        "prompt_profiles": sorted(
            candidate.get("draft", {}).get("prompt_profiles") or [],
            key=lambda item: (
                str(item.get("profile_id") or ""),
                int(item.get("pinned_version") or 0),
            ),
        ),
    }
    return canonical_checksum(payload)


def workflow_authoring_checksum(candidate: dict[str, Any]) -> str:
    """Checksum the complete candidate, including deterministic editor layout."""

    payload = deepcopy(candidate)
    draft = payload.get("draft")
    if isinstance(draft, dict):
        workflow = draft.get("workflow")
        if isinstance(workflow, dict):
            workflow["nodes"] = sorted(
                list(workflow.get("nodes") or []),
                key=lambda item: str(item.get("id") or ""),
            )
            workflow["edges"] = sorted(
                list(workflow.get("edges") or []),
                key=lambda item: str(item.get("id") or ""),
            )
        draft["prompt_profiles"] = sorted(
            list(draft.get("prompt_profiles") or []),
            key=lambda item: (
                str(item.get("profile_id") or ""),
                int(item.get("pinned_version") or 0),
            ),
        )
    return canonical_checksum(payload)


def _value_schema(value_type: str) -> WorkflowValueSchema:
    normalized = value_type if value_type in {
        "any",
        "null",
        "string",
        "number",
        "boolean",
        "object",
        "array",
    } else "any"
    return WorkflowValueSchema(type=normalized)


def _control_graph(
    refs: list[str],
    edges: list[GraphIntentControlEdgeV3],
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[str], list[str]]:
    children = {ref: set() for ref in refs}
    parents = {ref: set() for ref in refs}
    indegree = {ref: 0 for ref in refs}
    for edge in edges:
        if edge.source_ref not in children or edge.target_ref not in children:
            continue
        if edge.target_ref not in children[edge.source_ref]:
            children[edge.source_ref].add(edge.target_ref)
            parents[edge.target_ref].add(edge.source_ref)
            indegree[edge.target_ref] += 1
    queue = deque(sorted(ref for ref, count in indegree.items() if count == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    sinks = sorted(ref for ref in refs if not children[ref])
    return children, parents, order, sinks


def _ancestors(
    refs: list[str], parents: dict[str, set[str]], order: list[str]
) -> dict[str, set[str]]:
    result = {ref: set() for ref in refs}
    for ref in order:
        for parent in parents[ref]:
            result[ref].add(parent)
            result[ref].update(result[parent])
    return result


def v2_to_graph_intent(
    blueprint: MetaPlannerTypedBlueprintV2,
) -> tuple[GraphIntentV3 | None, MetaPlannerIRCompatibility]:
    refs = [node.ref for node in blueprint.nodes]
    _, parents, order, _ = _control_graph(
        refs,
        [
            GraphIntentControlEdgeV3(
                source_ref=edge.source_ref,
                target_ref=edge.target_ref,
            )
            for edge in blueprint.control_edges
        ],
    )
    ancestors = _ancestors(refs, parents, order)
    producers: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for node in blueprint.nodes:
        for output in node.outputs:
            producers[output.variable].append(
                (node.ref, output.port, output.value_type)
            )
    external = {
        "user_input": ("input", "user_input", "string"),
        "conversation_history": ("input", "conversation_history", "array"),
    }
    warnings: list[str] = []
    reserved_collisions = sorted(set(producers) & set(external))
    if reserved_collisions:
        warnings.append(
            "lossy_conversion: node outputs collide with reserved external "
            "variables: "
            + ", ".join(reserved_collisions)
            + "."
        )
    converted_nodes: list[GraphIntentNodeV3] = []
    for node in blueprint.nodes:
        converted_inputs: list[GraphIntentInputBindingV3] = []
        for binding in node.inputs:
            candidates = producers.get(binding.variable, [])
            if binding.variable in external:
                source_ref, source_port, source_type = external[binding.variable]
            elif len(candidates) == 1 and candidates[0][0] in ancestors.get(node.ref, set()):
                source_ref, source_port, source_type = candidates[0]
            else:
                reason = "ambiguous" if len(candidates) > 1 else "unreachable"
                warnings.append(
                    "lossy_conversion: input variable "
                    f"{binding.variable} for {node.ref} has {reason} provenance."
                )
                continue
            converted_inputs.append(
                GraphIntentInputBindingV3(
                    port="task",
                    variable=binding.variable,
                    source_ref=source_ref,
                    source_port=source_port,
                    value_schema=_value_schema(binding.value_type or source_type),
                )
            )
        converted_nodes.append(
            GraphIntentNodeV3(
                ref=node.ref,
                kind=node.kind,
                title=node.title,
                description=node.description,
                task_ids=node.task_ids,
                inputs=converted_inputs,
                outputs=[
                    GraphIntentOutputBindingV3(
                        port=item.port,
                        variable=item.variable,
                        value_schema=_value_schema(item.value_type),
                    )
                    for item in node.outputs
                ],
                config=node.config,
            )
        )
    compatibility = MetaPlannerIRCompatibility(
        source_version=2,
        upgraded=not warnings,
        lossy=bool(warnings),
        warnings=warnings,
    )
    if warnings:
        return None, compatibility
    return (
        GraphIntentV3(
            name=blueprint.name,
            description=blueprint.description,
            tags=blueprint.tags,
            starters=blueprint.starters,
            nodes=converted_nodes,
            control_edges=[
                GraphIntentControlEdgeV3(
                    source_ref=edge.source_ref,
                    target_ref=edge.target_ref,
                )
                for edge in blueprint.control_edges
            ],
            resources=blueprint.resources,
            middleware=blueprint.middleware,
            prompt_profile_ids=blueprint.prompt_profile_ids,
            final_output=GraphIntentFinalOutputV3(
                sources=[
                    GraphIntentFinalOutputSourceV3(
                        node_ref=blueprint.final_output.node_ref,
                        port="result",
                    )
                ],
            ),
        ),
        compatibility,
    )


def _graph_intent_inputs_to_v2(
    node: GraphIntentNodeV3,
) -> list[MetaPlannerIRInputBinding]:
    totals: dict[str, int] = defaultdict(int)
    for item in node.inputs:
        totals[item.port] += 1
    seen: dict[str, int] = defaultdict(int)
    converted: list[MetaPlannerIRInputBinding] = []
    for item in node.inputs:
        seen[item.port] += 1
        legacy_port = (
            f"{item.port}_{seen[item.port]}"
            if totals[item.port] > 1
            else item.port
        )
        converted.append(
            MetaPlannerIRInputBinding(
                port=legacy_port,
                variable=item.variable,
                value_type=item.value_schema.type,
            )
        )
    return converted


def graph_intent_to_v2(intent: GraphIntentV3) -> MetaPlannerTypedBlueprintV2:
    if len(intent.final_output.sources) != 1 or any(
        edge.outcome_ref != "success" for edge in intent.control_edges
    ):
        raise ValueError(
            "Graph Intent uses control-flow semantics that cannot be represented in V2."
        )
    final_source = intent.final_output.sources[0]
    final_node = next(
        (node for node in intent.nodes if node.ref == final_source.node_ref), None
    )
    if final_node is None:
        raise ValueError("Graph Intent final output references an unknown node.")
    final_binding = next(
        (item for item in final_node.outputs if item.port == final_source.port), None
    )
    if final_binding is None:
        raise ValueError("Graph Intent final output references an unknown port.")
    return MetaPlannerTypedBlueprintV2(
        name=intent.name,
        description=intent.description,
        tags=intent.tags,
        starters=intent.starters,
        nodes=[
            MetaPlannerIRNode(
                ref=node.ref,
                kind=node.kind,
                title=node.title,
                description=node.description,
                task_ids=node.task_ids,
                inputs=_graph_intent_inputs_to_v2(node),
                outputs=[
                    MetaPlannerIROutputBinding(
                        port=item.port,
                        variable=item.variable,
                        value_type=item.value_schema.type,
                    )
                    for item in node.outputs
                ],
                config=node.config,
            )
            for node in intent.nodes
        ],
        control_edges=[
            MetaPlannerIRControlEdge(
                source_ref=edge.source_ref,
                target_ref=edge.target_ref,
            )
            for edge in intent.control_edges
        ],
        resources=intent.resources,
        middleware=intent.middleware,
        prompt_profile_ids=intent.prompt_profile_ids,
        final_output=MetaPlannerIRFinalOutput(
            node_ref=final_source.node_ref,
            variable=final_binding.variable,
        ),
    )


def _schemas_compatible(
    source: WorkflowValueSchema, target: WorkflowValueSchema
) -> bool:
    def variants(schema: WorkflowValueSchema) -> list[WorkflowValueSchema]:
        resolved = (
            list(schema.any_of)
            if schema.any_of
            else [schema.model_copy(update={"nullable": False, "any_of": ()})]
        )
        if schema.nullable:
            resolved.append(WorkflowValueSchema(type="null"))
        return resolved

    if source.any_of or source.nullable or target.any_of or target.nullable:
        return all(
            any(_schemas_compatible(item, accepted) for accepted in variants(target))
            for item in variants(source)
        )

    if target.type == "any":
        return True
    if source.type == "any":
        return False
    if source.type == "null" or target.type == "null":
        return source.type == target.type
    if source.type == "integer" and target.type == "number":
        return True
    if source.type != target.type:
        return False
    if source.type == "array":
        if target.items is None:
            return True
        if source.items is None:
            return False
        return _schemas_compatible(source.items, target.items)
    if source.type == "object":
        source_required = set(source.required)
        target_required = set(target.required)
        if not target_required.issubset(source_required):
            return False
        for name, target_property in target.properties.items():
            source_property = source.properties.get(name)
            if source_property is None:
                if name in target_required:
                    return False
                continue
            if not _schemas_compatible(source_property, target_property):
                return False
    return True


def _resolved_node(
    *,
    ref: str,
    node_id: str,
    kind: str,
    role: str,
    title: str,
    description: str = "",
    task_ids: list[str] | None = None,
    config: dict[str, Any] | None = None,
    execution_version: int | None = None,
) -> ResolvedGraphNodeV3:
    contract = workflow_node_contract_registry.require(kind)
    execution_data = (
        {"contractVersion": execution_version}
        if execution_version is not None
        else config
    )
    return ResolvedGraphNodeV3(
        ref=ref,
        node_id=node_id,
        kind=kind,
        role=role,
        title=title,
        description=description,
        task_ids=list(task_ids or []),
        config=dict(config or {}),
        ports=[ResolvedGraphPortV3.model_validate(port.model_dump(mode="json")) for port in contract.ports],
        contract_version=NODE_CONTRACT_VERSION,
        contract_checksum=contract.checksum,
        compiler_checksum=contract.compiler_checksum,
        execution=contract.effective_execution(execution_data).model_dump(mode="json"),
        resource_contracts=[item.model_dump(mode="json") for item in contract.resources],
    )


def _resolve_immutable_resource_version(
    resource: dict[str, Any],
    *,
    expected_version: int | None,
    label: str,
) -> tuple[int, str]:
    """Resolve an exact immutable version independently from the latest pointer."""

    latest = resource.get("published_version")
    if not latest:
        raise ValueError(f"{label} has no published version.")
    target = int(expected_version or latest)
    available = list((resource.get("metadata") or {}).get("available_versions") or [])
    if available:
        match = next(
            (
                item
                for item in available
                if isinstance(item, dict) and int(item.get("version") or 0) == target
            ),
            None,
        )
        if match is None:
            raise ValueError(f"{label} pinned version {target} is unavailable.")
        checksum = str(match.get("checksum") or "").strip()
        return target, checksum or canonical_checksum(
            {"id": resource.get("id"), "version": target}
        )
    if target != int(latest):
        raise ValueError(
            f"{label} drifted from pinned version {target} to {int(latest)}."
        )
    return target, canonical_checksum(resource)


def resolve_graph_intent(
    intent: GraphIntentV3,
    snapshot: MetaPlannerCapabilitySnapshot,
    *,
    default_agent_model_id: str | None = None,
) -> ResolvedGraphIRV3:
    refs = [node.ref for node in intent.nodes]
    if len(refs) != len(set(refs)):
        raise ValueError("Graph IR node refs must be unique.")
    compiled_ids = [f"node_{_safe_identifier(ref, 'node')}" for ref in refs]
    if len(compiled_ids) != len(set(compiled_ids)):
        raise ValueError(
            "Graph IR node refs collide after identifier normalization."
        )
    snapshot_nodes = {
        str(item.get("kind") or ""): item for item in snapshot.nodes
    }
    available_models = {
        str(item.get("id") or "")
        for item in snapshot.models
        if item.get("safe") is True and item.get("id")
    }
    if default_agent_model_id:
        available_models.add(default_agent_model_id)
    try:
        from .node_adapters import (
            get_planner_node_adapter,
            planner_capability_metadata,
        )
    except ImportError:  # pragma: no cover - package fallback
        from meta_agent.node_adapters import (  # type: ignore
            get_planner_node_adapter,
            planner_capability_metadata,
        )
    for node in intent.nodes:
        projection = snapshot_nodes.get(node.kind)
        current = planner_capability_metadata(node.kind)
        planner = dict((projection or {}).get("planner") or {})
        if (
            projection is None
            or current is None
            or current.get("support") != "full"
            or not planner.get("compilable")
            or any(
                planner.get(field) != current.get(field)
                for field in (
                    "ir_version",
                    "contract_version",
                    "contract_checksum",
                    "compiler_checksum",
                    "adapter_checksum",
                )
            )
        ):
            raise ValueError(
                f"Node kind {node.kind} is not an authoritative Planner capability."
            )
        if get_planner_node_adapter(node.kind) is None:
            raise ValueError(f"Node kind {node.kind} has no Planner adapter.")
    known_refs = set(refs)
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in intent.control_edges:
        if edge.source_ref not in known_refs or edge.target_ref not in known_refs:
            raise ValueError("Graph IR control edge references an unknown node.")
        key = (edge.source_ref, edge.outcome_ref, edge.target_ref)
        if edge.source_ref == edge.target_ref or key in edge_keys:
            raise ValueError("Graph IR control edges must be unique and non-reflexive.")
        edge_keys.add(key)
    _, parents, order, _sinks = _control_graph(refs, intent.control_edges)
    if len(order) != len(refs):
        raise ValueError("Graph IR control edges must form an acyclic graph.")
    ancestors = _ancestors(refs, parents, order)

    nodes: list[ResolvedGraphNodeV3] = [
        _resolved_node(
            ref="input",
            node_id="input",
            kind="input",
            role="input",
            title="Conversation input",
            config={
                "variableName": "user_input",
                "historyVariable": "conversation_history",
            },
        )
    ]
    resolved_by_ref = {"input": nodes[0]}
    declared_outputs: dict[tuple[str, str], tuple[str, WorkflowValueSchema]] = {
        ("input", "user_input"): (
            "user_input",
            WorkflowValueSchema(type="string"),
        ),
        ("input", "conversation_history"): (
            "conversation_history",
            WorkflowValueSchema(
                type="array", items=WorkflowValueSchema(type="object")
            ),
        ),
    }
    producer_by_variable: dict[str, str] = {
        "user_input": "input",
        "conversation_history": "input",
    }
    for node in intent.nodes:
        resolved_config = dict(node.config)
        if (
            node.kind == "workflow_agent"
            and not resolved_config.get("model_id")
            and default_agent_model_id
        ):
            resolved_config["model_id"] = default_agent_model_id
        adapter = get_planner_node_adapter(node.kind)
        assert adapter is not None
        effective_node = node.model_copy(update={"config": resolved_config})
        parsed_config = adapter.validate_intent_node(effective_node)
        resolved_config = parsed_config.model_dump(mode="json")
        contract = workflow_node_contract_registry.require(node.kind)
        if len(node.task_ids) != len(set(node.task_ids)):
            raise ValueError(f"Node {node.ref} task IDs must be unique.")
        if contract.planner.task_binding == "required" and not node.task_ids:
            raise ValueError(f"Node {node.ref} must cover at least one plan task.")
        if contract.planner.task_binding == "forbidden" and node.task_ids:
            raise ValueError(f"Node {node.ref} cannot cover plan tasks.")
        model_id = str(resolved_config.get("model_id") or "")
        if model_id and model_id not in available_models:
            raise ValueError(
                f"Agent model {model_id} is unavailable in the Capability Snapshot."
            )
        declared_inputs = {binding.variable for binding in node.inputs}
        referenced = adapter.referenced_input_variables(parsed_config)
        missing_bindings = sorted(referenced - declared_inputs)
        if missing_bindings:
            raise ValueError(
                f"Node {node.ref} template variables need explicit data bindings: "
                + ", ".join(missing_bindings)
            )
        resolved = _resolved_node(
            ref=node.ref,
            node_id=f"node_{_safe_identifier(node.ref, 'node')}",
            kind=node.kind,
            role="executable",
            title=node.title,
            description=node.description,
            task_ids=node.task_ids,
            config=resolved_config,
            execution_version=(
                2
                if node.kind in {"json_serialize", "json_deserialize"}
                else None
            ),
        )
        contract_outputs = {
            port.name: port for port in resolved.ports if port.direction == "output"
        }
        for output in node.outputs:
            contract_port = contract_outputs.get(output.port)
            if contract_port is None:
                raise ValueError(
                    f"Node {node.ref} declares unknown output port {output.port}."
                )
            authoritative_schema = adapter.authoritative_output_schema(
                output.port,
                parsed_config,
            )
            if canonical_checksum(
                output.value_schema.model_dump(mode="json")
            ) != canonical_checksum(authoritative_schema.model_dump(mode="json")):
                raise ValueError(
                    f"Node {node.ref} output {output.port} does not match its "
                    "authoritative Adapter type."
                )
            key = (node.ref, output.port)
            if key in declared_outputs:
                raise ValueError(f"Node {node.ref} output port {output.port} is duplicated.")
            previous_producer = producer_by_variable.get(output.variable)
            if previous_producer is not None:
                raise ValueError(
                    f"Variable {output.variable} is produced by multiple nodes."
                )
            producer_by_variable[output.variable] = node.ref
            declared_outputs[key] = (output.variable, output.value_schema)
        nodes.append(resolved)
        resolved_by_ref[node.ref] = resolved

    edges: list[ResolvedGraphEdgeV3] = []
    roots = sorted(ref for ref in refs if not parents[ref])
    for target_ref in roots:
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref("control", "input", target_ref),
                mode="control",
                source=ResolvedGraphEndpointV3(node_ref="input", node_id="input"),
                target=ResolvedGraphEndpointV3(
                    node_ref=target_ref,
                    node_id=resolved_by_ref[target_ref].node_id,
                ),
                outcome_ref="success",
                join="all",
            )
        )
    for edge in intent.control_edges:
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref(
                    "control", edge.source_ref, edge.outcome_ref, edge.target_ref
                ),
                mode="control",
                source=ResolvedGraphEndpointV3(
                    node_ref=edge.source_ref,
                    node_id=resolved_by_ref[edge.source_ref].node_id,
                ),
                target=ResolvedGraphEndpointV3(
                    node_ref=edge.target_ref,
                    node_id=resolved_by_ref[edge.target_ref].node_id,
                ),
                outcome_ref=edge.outcome_ref,
                join="all",
            )
        )

    for node in intent.nodes:
        target_ports = {
            port.name: port
            for port in resolved_by_ref[node.ref].ports
            if port.direction == "input"
        }
        counts: dict[str, int] = defaultdict(int)
        for binding in node.inputs:
            target_port = target_ports.get(binding.port)
            if target_port is None:
                raise ValueError(
                    f"Node {node.ref} declares unknown input port {binding.port}."
                )
            counts[binding.port] += 1
            if counts[binding.port] > 1 and target_port.cardinality != "many":
                raise ValueError(
                    f"Node {node.ref} input port {binding.port} exceeds cardinality."
                )
            source = declared_outputs.get((binding.source_ref, binding.source_port))
            if source is None:
                raise ValueError(
                    f"Node {node.ref} input {binding.variable} has an unknown source port."
                )
            source_variable, source_schema = source
            if source_variable != binding.variable:
                raise ValueError(
                    f"Node {node.ref} input variable does not match its source output."
                )
            if binding.source_ref != "input" and binding.source_ref not in ancestors[node.ref]:
                raise ValueError(
                    f"Node {node.ref} input {binding.variable} is not control-reachable."
                )
            if not _schemas_compatible(source_schema, binding.value_schema):
                raise ValueError(
                    f"Node {node.ref} input {binding.variable} has an incompatible type."
                )
            if not _schemas_compatible(binding.value_schema, target_port.value_schema):
                raise ValueError(
                    f"Node {node.ref} input port {binding.port} rejects its value type."
                )
            edges.append(
                ResolvedGraphEdgeV3(
                    ref=_stable_ref(
                        "data",
                        binding.source_ref,
                        binding.source_port,
                        node.ref,
                        binding.port,
                        binding.variable,
                    ),
                    mode="data",
                    source=ResolvedGraphEndpointV3(
                        node_ref=binding.source_ref,
                        node_id=resolved_by_ref[binding.source_ref].node_id,
                        port=binding.source_port,
                    ),
                    target=ResolvedGraphEndpointV3(
                        node_ref=node.ref,
                        node_id=resolved_by_ref[node.ref].node_id,
                        port=binding.port,
                    ),
                    variable=binding.variable,
                    value_schema=binding.value_schema,
                )
            )
        missing_required = sorted(
            port.name
            for port in target_ports.values()
            if port.required and not counts[port.name]
        )
        if missing_required:
            raise ValueError(
                f"Node {node.ref} is missing required input ports: "
                + ", ".join(missing_required)
            )

    resources = {
        "external_xpert": {item["id"]: item for item in snapshot.external_xperts},
        "knowledge_base": {item["id"]: item for item in snapshot.knowledge_bases},
        "toolset_resource": {item["id"]: item for item in snapshot.toolsets},
        "plugin_resource": {item["id"]: item for item in snapshot.plugins},
    }
    seen_resources: set[tuple[str, str, str]] = set()
    seen_external_tools: set[tuple[str, str]] = set()
    for binding in intent.resources:
        target = resolved_by_ref.get(binding.target_ref)
        if target is None:
            raise ValueError(f"Resource {binding.resource_id} targets an unknown node.")
        if target.kind != "workflow_agent" or target.role != "executable":
            raise ValueError(
                f"Resource {binding.resource_id} must target a workflow_agent."
            )
        resource_key = (binding.kind, binding.resource_id, binding.target_ref)
        if resource_key in seen_resources:
            raise ValueError("Graph IR cannot bind a duplicate resource.")
        seen_resources.add(resource_key)
        resource = resources[binding.kind].get(binding.resource_id)
        if resource is None:
            raise ValueError(f"Resource {binding.resource_id} is unavailable.")
        ref = _stable_ref("resource", binding.kind, binding.resource_id, binding.target_ref)
        contract = workflow_node_contract_registry.require(binding.kind)
        config: dict[str, Any] = {
            "resource_id": binding.resource_id,
            "version_policy": "active" if binding.kind == "knowledge_base" else "pinned",
        }
        if binding.kind == "knowledge_base":
            config["observed_active_version_id"] = (resource.get("metadata") or {}).get(
                "active_version_id"
            )
            config["top_k"] = binding.top_k
            config["score_threshold"] = binding.score_threshold
        else:
            expected_version = intent._pinned_resource_versions.get(
                (binding.kind, binding.resource_id, binding.target_ref)
            )
            version, version_checksum = _resolve_immutable_resource_version(
                resource,
                expected_version=expected_version,
                label=f"Resource {binding.resource_id}",
            )
            config["pinned_version"] = version
        if binding.kind == "external_xpert":
            tool_name = _safe_identifier(
                binding.tool_name or f"xpert_{binding.resource_id[:12]}",
                "external_xpert",
            )
            tool_key = (binding.target_ref, tool_name)
            if tool_key in seen_external_tools:
                raise ValueError(
                    f"External Xpert tool name {tool_name} is duplicated for "
                    f"node {binding.target_ref}."
                )
            seen_external_tools.add(tool_key)
            config["tool_name"] = tool_name
        config["resource_checksum"] = (
            version_checksum
            if binding.kind != "knowledge_base"
            else canonical_checksum(resource)
        )
        resolved_resource = _resolved_node(
            ref=ref,
            node_id=ref,
            kind=binding.kind,
            role="resource",
            title=str(resource.get("name") or binding.kind),
            description=binding.description or str(resource.get("description") or ""),
            config=config,
        )
        nodes.append(resolved_resource)
        resolved_by_ref[ref] = resolved_resource
        source_handle = contract.edge.allowed_source_handles[0]
        target_handle = contract.edge.allowed_target_handles[0]
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref("binding", ref, binding.target_ref),
                mode="binding",
                source=ResolvedGraphEndpointV3(
                    node_ref=ref, node_id=ref, handle=source_handle
                ),
                target=ResolvedGraphEndpointV3(
                    node_ref=binding.target_ref,
                    node_id=resolved_by_ref[binding.target_ref].node_id,
                    handle=target_handle,
                ),
            )
        )

    middleware_lookup = {item["id"]: item for item in snapshot.middleware}
    seen_middleware: set[tuple[str, str]] = set()
    for binding in sorted(
        intent.middleware,
        key=lambda item: (item.priority, item.middleware_id, item.target_ref),
    ):
        key = (binding.target_ref, binding.middleware_id)
        if key in seen_middleware:
            raise ValueError("Graph IR cannot bind duplicate middleware.")
        seen_middleware.add(key)
        middleware = middleware_lookup.get(binding.middleware_id)
        target = resolved_by_ref.get(binding.target_ref)
        if middleware is None or target is None:
            raise ValueError(f"Middleware {binding.middleware_id} is unavailable.")
        if target.kind != "workflow_agent" or target.role != "executable":
            raise ValueError(
                f"Middleware {binding.middleware_id} must target a workflow_agent."
            )
        defaults = resolve_middleware_config(middleware, binding.config)
        ref = _stable_ref("middleware", binding.middleware_id, binding.target_ref)
        resolved_middleware = _resolved_node(
            ref=ref,
            node_id=ref,
            kind="runtime_middleware",
            role="metadata",
            title=str(middleware.get("title") or binding.middleware_id),
            description=str(middleware.get("description") or ""),
            config={
                "middleware_id": binding.middleware_id,
                "priority": binding.priority,
                "config": defaults,
                "config_version": middleware.get("config_version"),
                "definition_checksum": canonical_checksum(middleware),
            },
        )
        nodes.append(resolved_middleware)
        resolved_by_ref[ref] = resolved_middleware
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref("metadata", ref, binding.target_ref),
                mode="metadata",
                source=ResolvedGraphEndpointV3(
                    node_ref=ref,
                    node_id=ref,
                    handle="middleware-binding",
                ),
                target=ResolvedGraphEndpointV3(
                    node_ref=binding.target_ref,
                    node_id=resolved_by_ref[binding.target_ref].node_id,
                    handle="middleware",
                ),
            )
        )

    final_sources: list[tuple[GraphIntentFinalOutputSourceV3, str, WorkflowValueSchema]] = []
    for source in intent.final_output.sources:
        final_node = resolved_by_ref.get(source.node_ref)
        final_source = declared_outputs.get((source.node_ref, source.port))
        if final_node is None or final_node.kind != "workflow_agent":
            raise ValueError("Graph IR final outputs must come from workflow_agent nodes.")
        if final_source is None:
            raise ValueError("Graph IR final output does not match its source port.")
        final_sources.append((source, final_source[0], final_source[1]))
    output_sources = [
        {
            "sourceRef": source.node_ref,
            "sourcePort": source.port,
            "variable": variable,
        }
        for source, variable, _schema in final_sources
    ]
    output_node = _resolved_node(
        ref="output",
        node_id="output",
        kind="output",
        role="output",
        title="Final answer",
        config={
            "contractVersion": 2,
            "selectionPolicy": "exactly_one_arrived",
            "outputSources": output_sources,
        },
    )
    nodes.append(output_node)
    resolved_by_ref["output"] = output_node
    for source, variable, schema in final_sources:
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref("control", source.node_ref, "success", "output"),
                mode="control",
                source=ResolvedGraphEndpointV3(
                    node_ref=source.node_ref,
                    node_id=resolved_by_ref[source.node_ref].node_id,
                ),
                target=ResolvedGraphEndpointV3(node_ref="output", node_id="output"),
                outcome_ref="success",
                join="all",
            )
        )
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref(
                    "data",
                    source.node_ref,
                    source.port,
                    "output",
                    "result",
                    variable,
                ),
                mode="data",
                source=ResolvedGraphEndpointV3(
                    node_ref=source.node_ref,
                    node_id=resolved_by_ref[source.node_ref].node_id,
                    port=source.port,
                ),
                target=ResolvedGraphEndpointV3(
                    node_ref="output", node_id="output", port="result"
                ),
                variable=variable,
                value_schema=schema,
            )
        )

    prompt_lookup = {item["id"]: item for item in snapshot.prompt_profiles}
    prompt_profiles: list[ResolvedPromptProfileV3] = []
    if len(intent.prompt_profile_ids) != len(set(intent.prompt_profile_ids)):
        raise ValueError("Graph IR cannot bind duplicate Prompt Profiles.")
    for profile_id in dict.fromkeys(intent.prompt_profile_ids):
        resource = prompt_lookup.get(profile_id)
        if resource is None or not resource.get("published_version"):
            raise ValueError(f"Prompt Profile {profile_id} is unavailable.")
        expected_version = intent._pinned_prompt_profile_versions.get(profile_id)
        version, version_checksum = _resolve_immutable_resource_version(
            resource,
            expected_version=expected_version,
            label=f"Prompt Profile {profile_id}",
        )
        prompt_profiles.append(
            ResolvedPromptProfileV3(
                profile_id=profile_id,
                pinned_version=version,
                checksum=version_checksum,
            )
        )

    from .control_flow import analyze_control_flow

    control_flow_report = analyze_control_flow(intent)
    graph = ResolvedGraphIRV3(
        name=intent.name,
        description=intent.description,
        tags=list(dict.fromkeys(intent.tags)),
        starters=list(dict.fromkeys(intent.starters)),
        nodes=sorted(nodes, key=lambda item: item.ref),
        edges=sorted(edges, key=lambda item: item.ref),
        prompt_profiles=prompt_profiles,
        final_output=intent.final_output,
        control_flow_report=control_flow_report,
        terminal_count=len(intent.final_output.sources),
        contract_version=NODE_CONTRACT_VERSION,
        capability_snapshot_version=snapshot.version,
        capability_snapshot_hash=snapshot.snapshot_hash,
    )
    return graph.model_copy(update={"graph_checksum": graph_ir_checksum(graph)})


def annotate_candidate_with_graph_ir(
    candidate: dict[str, Any],
    intent: GraphIntentV3,
    graph_ir: ResolvedGraphIRV3,
) -> None:
    workflow = candidate.get("draft", {}).get("workflow") or {}
    resolved_by_id = {node.node_id: node for node in graph_ir.nodes}
    intent_by_ref = {node.ref: node for node in intent.nodes}
    for node in workflow.get("nodes") or []:
        resolved = resolved_by_id.get(str(node.get("id") or ""))
        if resolved is None:
            continue
        data = node.setdefault("data", {})
        data["plannerIRVersion"] = GRAPH_IR_VERSION
        data["plannerRef"] = resolved.ref
        data["plannerContractVersion"] = resolved.contract_version
        data["plannerCompilerChecksum"] = resolved.compiler_checksum
        source = intent_by_ref.get(resolved.ref)
        if source is not None:
            data["plannerInputsV3"] = [
                item.model_dump(mode="json") for item in source.inputs
            ]
            data["plannerOutputsV3"] = [
                item.model_dump(mode="json") for item in source.outputs
            ]
            if source.kind == "data_merge":
                data["plannerControlInputMapV1"] = {
                    item.port: item.source_ref for item in source.inputs
                }


def decompile_candidate_to_graph_intent_compat(
    candidate: dict[str, Any],
) -> tuple[GraphIntentV3 | None, MetaPlannerIRCompatibility]:
    from .node_adapters import (
        META_PLANNER_ADAPTER_KINDS,
        decompile_planner_node,
        decompile_planner_node_v3,
    )

    try:
        from server.workflow_native.schemas import NativeWorkflowNode
    except ModuleNotFoundError:
        from workflow_native.schemas import NativeWorkflowNode

    draft = candidate.get("draft") or {}
    workflow = draft.get("workflow") or {}
    raw_nodes = list(workflow.get("nodes") or [])
    node_by_id = {str(node.get("id") or ""): node for node in raw_nodes}
    if len(node_by_id) != len(raw_nodes) or "" in node_by_id:
        raise ValueError("Compiled candidate node IDs must be present and unique.")
    kind_by_id = {
        node_id: str(
            (node.get("data") or {}).get("kind") or node.get("type") or ""
        )
        for node_id, node in node_by_id.items()
    }
    supported_kinds = {
        "input",
        "output",
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
        "runtime_middleware",
        *META_PLANNER_ADAPTER_KINDS,
    }
    unsupported = sorted(
        f"{node_id}:{kind or '<missing>'}"
        for node_id, kind in kind_by_id.items()
        if kind not in supported_kinds
    )
    if unsupported:
        raise ValueError(
            "Compiled candidate contains nodes outside the recoverable Planner graph: "
            + ", ".join(unsupported)
        )
    input_ids = sorted(
        node_id for node_id, kind in kind_by_id.items() if kind == "input"
    )
    output_ids = sorted(
        node_id for node_id, kind in kind_by_id.items() if kind == "output"
    )
    if input_ids != ["input"] or output_ids != ["output"]:
        raise ValueError(
            "Compiled candidate must contain the canonical input and output nodes exactly once."
        )
    planner_nodes = [
        raw_node
        for raw_node in raw_nodes
        if kind_by_id[str(raw_node.get("id") or "")] in META_PLANNER_ADAPTER_KINDS
    ]
    if not any(
        kind_by_id[str(raw_node.get("id") or "")] == "workflow_agent"
        for raw_node in planner_nodes
    ):
        raise ValueError("Compiled candidate has no Workflow Agent node.")
    marker_versions = {
        int((raw_node.get("data") or {}).get("plannerIRVersion") or 0)
        for raw_node in planner_nodes
    }
    if not marker_versions.issubset({0, GRAPH_IR_VERSION}):
        raise ValueError("Compiled candidate carries an unknown Graph IR marker.")
    if len(marker_versions) != 1:
        raise ValueError(
            "Compiled candidate mixes Graph IR V2 and V3 Workflow Agent markers."
        )
    use_v3 = marker_versions == {GRAPH_IR_VERSION}
    ref_by_id: dict[str, str] = {}
    business_nodes: list[GraphIntentNodeV3] = []
    legacy_nodes: list[MetaPlannerIRNode] = []
    for raw_node in planner_nodes:
        native_node = NativeWorkflowNode.model_validate(raw_node)
        restored = (
            decompile_planner_node_v3(native_node)
            if use_v3
            else decompile_planner_node(native_node)
        )
        node_id = str(raw_node.get("id") or "")
        ref_by_id[node_id] = restored.ref
        if use_v3:
            business_nodes.append(restored)
        else:
            legacy_nodes.append(restored)
    business_by_ref = {
        node.ref: node for node in business_nodes
    } if use_v3 else {}

    control_edges: list[GraphIntentControlEdgeV3] = []
    resources: list[MetaPlannerIRResourceBinding] = []
    pinned_resource_versions: dict[tuple[str, str, str], int] = {}
    middleware: list[MetaPlannerIRMiddlewareBinding] = []
    consumed_node_ids = {"input", "output", *ref_by_id}
    input_root_refs: set[str] = set()
    input_root_edges: list[str] = []
    terminal_edges: list[dict[str, Any]] = []
    seen_edge_ids: set[str] = set()
    for raw_edge in workflow.get("edges") or []:
        edge_id = str(raw_edge.get("id") or "")
        if not edge_id or edge_id in seen_edge_ids:
            raise ValueError("Compiled candidate edge IDs must be present and unique.")
        seen_edge_ids.add(edge_id)
        source_id = str(raw_edge.get("source") or "")
        target_id = str(raw_edge.get("target") or "")
        if source_id not in node_by_id or target_id not in node_by_id:
            raise ValueError(f"Compiled edge {edge_id} has an unknown endpoint.")
        source_ref = ref_by_id.get(source_id)
        target_ref = ref_by_id.get(target_id)
        source_handle = str(raw_edge.get("sourceHandle") or "")
        target_handle = str(raw_edge.get("targetHandle") or "")
        source_kind = kind_by_id[source_id]
        target_kind = kind_by_id[target_id]
        if source_kind == "input" and target_ref and not source_handle and not target_handle:
            input_root_edges.append(target_ref)
            input_root_refs.add(target_ref)
            continue
        if source_ref and target_kind == "output" and not source_handle and not target_handle:
            terminal_edges.append(raw_edge)
            continue
        if source_ref and target_ref:
            if not use_v3:
                if source_handle or target_handle:
                    raise ValueError(
                        f"Legacy control edge {edge_id} cannot carry Handles."
                    )
                semantic_outcome = "success"
            else:
                from .control_flow import native_outcome_map

                source_intent = business_by_ref[source_ref]
                expected_outcomes = native_outcome_map(source_intent)
                raw_outcomes = (node_by_id[source_id].get("data") or {}).get(
                    "plannerOutcomeMapV1"
                )
                if raw_outcomes != expected_outcomes:
                    raise ValueError(
                        f"Compiled node {source_ref} outcome mapping has drifted."
                    )
                matches = [
                    semantic
                    for semantic, native in expected_outcomes.items()
                    if native == source_handle
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"Compiled control edge {edge_id} has an invalid source Handle."
                    )
                semantic_outcome = matches[0]
                target_intent = business_by_ref[target_ref]
                if target_intent.kind == "data_merge":
                    expected_inputs = (node_by_id[target_id].get("data") or {}).get(
                        "plannerControlInputMapV1"
                    )
                    if not isinstance(expected_inputs, dict) or expected_inputs.get(
                        target_handle
                    ) != source_ref:
                        raise ValueError(
                            f"Compiled data merge edge {edge_id} has an invalid target Handle."
                        )
                elif target_handle:
                    raise ValueError(
                        f"Compiled control edge {edge_id} has an unexpected target Handle."
                    )
            control_edges.append(
                GraphIntentControlEdgeV3(
                    source_ref=source_ref,
                    outcome_ref=semantic_outcome,
                    target_ref=target_ref,
                )
            )
            continue
        if not target_ref:
            raise ValueError(
                f"Compiled binding edge {edge_id} must target a Workflow Agent."
            )
        if target_kind != "workflow_agent":
            raise ValueError(
                f"Compiled binding edge {edge_id} must target a Workflow Agent."
            )
        source_node = node_by_id.get(source_id) or {}
        source_data = source_node.get("data") or {}
        expected_handles = {
            "external_xpert": ("expert-binding", "expert"),
            "knowledge_base": ("knowledge-binding", "knowledge"),
            "toolset_resource": ("toolset-binding", "toolset"),
            "plugin_resource": ("plugin-binding", "plugin"),
            "runtime_middleware": ("middleware-binding", "middleware"),
        }.get(source_kind)
        if expected_handles != (source_handle, target_handle):
            raise ValueError(
                f"Compiled binding edge {edge_id} has invalid source/target Handles."
            )
        if source_id in consumed_node_ids:
            raise ValueError(
                f"Compiled resource node {source_id} is bound more than once."
            )
        consumed_node_ids.add(source_id)
        if source_kind == "runtime_middleware":
            middleware_id = str(source_data.get("runtimeMiddlewareId") or "")
            if not middleware_id:
                raise ValueError("Compiled middleware binding has no middleware ID.")
            middleware.append(
                MetaPlannerIRMiddlewareBinding(
                    target_ref=target_ref,
                    middleware_id=middleware_id,
                    priority=int(source_data.get("middlewarePriority") or 100),
                    config=dict(source_data.get("runtimeMiddlewareConfig") or {}),
                )
            )
            continue
        id_fields = {
            "external_xpert": "xpertId",
            "knowledge_base": "knowledgeBaseId",
            "toolset_resource": "toolsetId",
            "plugin_resource": "pluginId",
        }
        resource_id = str(source_data.get(id_fields[source_kind]) or "")
        if not resource_id:
            raise ValueError(f"Compiled {source_kind} binding has no resource ID.")
        if source_kind != "knowledge_base":
            try:
                pinned_version = int(source_data.get("pinnedVersion"))
            except (TypeError, ValueError):
                raise ValueError(
                    f"Compiled {source_kind} resource has no pinned version."
                ) from None
            pin_key = (source_kind, resource_id, target_ref)
            previous_pin = pinned_resource_versions.get(pin_key)
            if previous_pin is not None and previous_pin != pinned_version:
                raise ValueError(
                    "Compiled resource carries conflicting pinned versions."
                )
            pinned_resource_versions[pin_key] = pinned_version
        resources.append(
            MetaPlannerIRResourceBinding(
                target_ref=target_ref,
                kind=source_kind,
                resource_id=resource_id,
                tool_name=str(source_data.get("toolName") or ""),
                description=str(source_data.get("description") or ""),
                top_k=int(source_data.get("topK") or 5),
                score_threshold=float(source_data.get("scoreThreshold") or 0),
            )
        )

    unconsumed_nodes = sorted(set(node_by_id) - consumed_node_ids)
    if unconsumed_nodes:
        raise ValueError(
            "Compiled candidate contains unbound or unconsumed nodes: "
            + ", ".join(unconsumed_nodes)
        )
    parents = {ref: set() for ref in ref_by_id.values()}
    for edge in control_edges:
        parents[edge.target_ref].add(edge.source_ref)
    expected_roots = {ref for ref, values in parents.items() if not values}
    if len(input_root_edges) != len(input_root_refs) or input_root_refs != expected_roots:
        raise ValueError(
            "Compiled input edges do not match the semantic control roots."
        )
    output_node = next(
        (
            node
            for node in raw_nodes
            if str((node.get("data") or {}).get("kind") or node.get("type"))
            == "output"
        ),
        None,
    )
    if output_node is None:
        raise ValueError("Compiled candidate has no output node.")
    output_data = output_node.get("data") or {}
    final_output_v3: GraphIntentFinalOutputV3 | None = None
    legacy_terminal_ref = ""
    legacy_output_variable = ""
    if use_v3 and output_data.get("contractVersion") == 2:
        if output_data.get("selectionPolicy") != "exactly_one_arrived":
            raise ValueError("Compiled Output V2 selection policy has drifted.")
        raw_output_sources = output_data.get("outputSources")
        if not isinstance(raw_output_sources, list) or not 1 <= len(
            raw_output_sources
        ) <= 8:
            raise ValueError("Compiled Output V2 must contain 1-8 output sources.")
        restored_sources: list[GraphIntentFinalOutputSourceV3] = []
        expected_terminal_ids: list[str] = []
        seen_sources: set[tuple[str, str]] = set()
        business_node_by_id = {
            node_id: business_by_ref[ref]
            for node_id, ref in ref_by_id.items()
        }
        for raw_source in raw_output_sources:
            if not isinstance(raw_source, dict) or set(raw_source) != {
                "sourceRef",
                "sourcePort",
                "variable",
            }:
                raise ValueError("Compiled Output V2 source metadata is invalid.")
            source_ref = str(raw_source.get("sourceRef") or "")
            source_port = str(raw_source.get("sourcePort") or "")
            variable = str(raw_source.get("variable") or "")
            source_node_id = next(
                (node_id for node_id, ref in ref_by_id.items() if ref == source_ref),
                "",
            )
            source_node = business_node_by_id.get(source_node_id)
            if (
                not source_node_id
                or source_node is None
                or source_node.kind != "workflow_agent"
            ):
                raise ValueError(
                    "Compiled Output V2 sources must use Workflow Agent nodes."
                )
            output_binding = next(
                (item for item in source_node.outputs if item.port == source_port),
                None,
            )
            if output_binding is None or output_binding.variable != variable:
                raise ValueError(
                    "Compiled Output V2 source port or variable has drifted."
                )
            source_key = (source_ref, source_port)
            if source_key in seen_sources:
                raise ValueError("Compiled Output V2 sources must be unique.")
            seen_sources.add(source_key)
            expected_terminal_ids.append(source_node_id)
            restored_sources.append(
                GraphIntentFinalOutputSourceV3(
                    node_ref=source_ref,
                    port=source_port,
                )
            )
        actual_terminal_ids = [
            str(edge.get("source") or "") for edge in terminal_edges
        ]
        if sorted(actual_terminal_ids) != sorted(expected_terminal_ids):
            raise ValueError(
                "Compiled Output V2 terminal edges do not match its output sources."
            )
        final_output_v3 = GraphIntentFinalOutputV3(sources=restored_sources)
    else:
        if len(terminal_edges) != 1:
            raise ValueError(
                "Legacy compiled candidate must contain exactly one terminal edge."
            )
        legacy_output_variable = str(output_data.get("outputVariable") or "")
        terminal_edge = terminal_edges[0]
        legacy_terminal_ref = ref_by_id.get(
            str(terminal_edge.get("source") or ""), ""
        )
        terminal_node_id = str(terminal_edge.get("source") or "")
        if not legacy_terminal_ref or not legacy_output_variable:
            raise ValueError("Compiled candidate final output metadata is incomplete.")
        if kind_by_id.get(terminal_node_id) != "workflow_agent":
            raise ValueError(
                "Compiled candidate final output must use a Workflow Agent."
            )
        if use_v3:
            terminal_node = business_by_ref[legacy_terminal_ref]
            matches = [
                item
                for item in terminal_node.outputs
                if item.variable == legacy_output_variable
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Legacy compiled final output cannot be mapped to one Adapter port."
                )
            final_output_v3 = GraphIntentFinalOutputV3(
                sources=[
                    GraphIntentFinalOutputSourceV3(
                        node_ref=legacy_terminal_ref,
                        port=matches[0].port,
                    )
                ]
            )
    prompt_items = [
        item
        for item in draft.get("prompt_profiles") or []
        if item.get("enabled", True) and item.get("profile_id")
    ]
    prompt_ids = [
        str(item.get("profile_id") or "")
        for item in prompt_items
    ]
    pinned_prompt_versions: dict[str, int] = {}
    for item in prompt_items:
        profile_id = str(item.get("profile_id") or "")
        try:
            pinned_prompt_versions[profile_id] = int(item.get("pinned_version"))
        except (TypeError, ValueError):
            raise ValueError(
                f"Compiled Prompt Profile {profile_id} has no pinned version."
            ) from None
    metadata = {
        "name": str(candidate.get("name") or workflow.get("title") or "Xpert"),
        "description": str(candidate.get("description") or ""),
        "tags": list(candidate.get("tags") or []),
        "starters": list(candidate.get("starters") or []),
    }
    if use_v3:
        compatibility = MetaPlannerIRCompatibility(source_version=3)
        intent = GraphIntentV3(
            **metadata,
            nodes=sorted(business_nodes, key=lambda item: item.ref),
            control_edges=sorted(
                control_edges,
                key=lambda item: (
                    item.source_ref,
                    item.outcome_ref,
                    item.target_ref,
                ),
            ),
            resources=resources,
            middleware=middleware,
            prompt_profile_ids=prompt_ids,
            final_output=final_output_v3,
        )
    else:
        blueprint = MetaPlannerTypedBlueprintV2(
            **metadata,
            nodes=sorted(legacy_nodes, key=lambda item: item.ref),
            control_edges=[
                MetaPlannerIRControlEdge(
                    source_ref=edge.source_ref,
                    target_ref=edge.target_ref,
                )
                for edge in sorted(
                    control_edges,
                    key=lambda item: (item.source_ref, item.target_ref),
                )
            ],
            resources=resources,
            middleware=middleware,
            prompt_profile_ids=prompt_ids,
            final_output=MetaPlannerIRFinalOutput(
                node_ref=legacy_terminal_ref,
                variable=legacy_output_variable,
            ),
        )
        intent, compatibility = v2_to_graph_intent(blueprint)
        if intent is None:
            return None, compatibility
    intent._pinned_resource_versions = pinned_resource_versions
    intent._pinned_prompt_profile_versions = pinned_prompt_versions
    return intent, compatibility


def decompile_candidate_to_graph_intent(
    candidate: dict[str, Any],
) -> GraphIntentV3:
    intent, compatibility = decompile_candidate_to_graph_intent_compat(candidate)
    if intent is None:
        raise ValueError("; ".join(compatibility.warnings))
    return intent


def resolved_behavior_projection(graph_ir: ResolvedGraphIRV3) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "ref": node.ref,
                "kind": node.kind,
                "role": node.role,
                "config": node.config,
            }
            for node in graph_ir.nodes
        ],
        "edges": [
            {
                "mode": edge.mode,
                "source": edge.source.node_ref,
                "target": edge.target.node_ref,
                "source_handle": edge.source.handle,
                "target_handle": edge.target.handle,
                "variable": edge.variable,
            }
            for edge in graph_ir.edges
            if edge.mode != "data"
        ],
        "final_output": graph_ir.final_output.model_dump(mode="json"),
        "prompt_profiles": [
            item.model_dump(mode="json") for item in graph_ir.prompt_profiles
        ],
    }
