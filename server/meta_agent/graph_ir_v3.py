from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
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


def graph_ir_checksum(graph_ir: ResolvedGraphIRV3 | dict[str, Any]) -> str:
    payload = (
        graph_ir.model_dump(mode="json")
        if isinstance(graph_ir, ResolvedGraphIRV3)
        else dict(graph_ir)
    )
    payload.pop("graph_checksum", None)
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
        "prompt_profiles": candidate.get("draft", {}).get("prompt_profiles") or [],
    }
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
                node_ref=blueprint.final_output.node_ref,
                variable=blueprint.final_output.variable,
            ),
        ),
        compatibility,
    )


def graph_intent_to_v2(intent: GraphIntentV3) -> MetaPlannerTypedBlueprintV2:
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
                inputs=[
                    MetaPlannerIRInputBinding(
                        port=f"{item.port}_{index + 1}",
                        variable=item.variable,
                        value_type=item.value_schema.type,
                    )
                    for index, item in enumerate(node.inputs)
                ],
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
            node_ref=intent.final_output.node_ref,
            variable=intent.final_output.variable,
        ),
    )


def _schemas_compatible(
    source: WorkflowValueSchema, target: WorkflowValueSchema
) -> bool:
    if source.type == "any" or target.type == "any":
        return True
    if source.type == target.type:
        return True
    return source.type == "integer" and target.type == "number"


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
) -> ResolvedGraphNodeV3:
    contract = workflow_node_contract_registry.require(kind)
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
        execution=contract.execution.model_dump(mode="json"),
        resource_contracts=[item.model_dump(mode="json") for item in contract.resources],
    )


def resolve_graph_intent(
    intent: GraphIntentV3,
    snapshot: MetaPlannerCapabilitySnapshot,
    *,
    default_agent_model_id: str | None = None,
) -> ResolvedGraphIRV3:
    refs = [node.ref for node in intent.nodes]
    if len(refs) != len(set(refs)):
        raise ValueError("Graph IR node refs must be unique.")
    known_refs = set(refs)
    edge_keys: set[tuple[str, str]] = set()
    for edge in intent.control_edges:
        if edge.source_ref not in known_refs or edge.target_ref not in known_refs:
            raise ValueError("Graph IR control edge references an unknown node.")
        key = (edge.source_ref, edge.target_ref)
        if edge.source_ref == edge.target_ref or key in edge_keys:
            raise ValueError("Graph IR control edges must be unique and non-reflexive.")
        edge_keys.add(key)
    _, parents, order, sinks = _control_graph(refs, intent.control_edges)
    if len(order) != len(refs):
        raise ValueError("Graph IR control edges must form an acyclic graph.")
    if len(sinks) != 1 or sinks[0] != intent.final_output.node_ref:
        raise ValueError("Graph IR requires one terminal matching final_output.")
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
        resolved = _resolved_node(
            ref=node.ref,
            node_id=f"node_{_safe_identifier(node.ref, 'node')}",
            kind=node.kind,
            role="executable",
            title=node.title,
            description=node.description,
            task_ids=node.task_ids,
            config=resolved_config,
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
            if not _schemas_compatible(output.value_schema, contract_port.value_schema):
                raise ValueError(
                    f"Node {node.ref} output {output.port} has an incompatible type."
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
                outcome="success",
                join="all",
            )
        )
    for edge in intent.control_edges:
        edges.append(
            ResolvedGraphEdgeV3(
                ref=_stable_ref("control", edge.source_ref, edge.target_ref),
                mode="control",
                source=ResolvedGraphEndpointV3(
                    node_ref=edge.source_ref,
                    node_id=resolved_by_ref[edge.source_ref].node_id,
                ),
                target=ResolvedGraphEndpointV3(
                    node_ref=edge.target_ref,
                    node_id=resolved_by_ref[edge.target_ref].node_id,
                ),
                outcome=edge.outcome,
                join=edge.join,
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
    for binding in intent.resources:
        if binding.target_ref not in resolved_by_ref:
            raise ValueError(f"Resource {binding.resource_id} targets an unknown node.")
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
            version = resource.get("published_version")
            if not version:
                raise ValueError(f"Resource {binding.resource_id} has no published version.")
            config["pinned_version"] = int(version)
        config["resource_checksum"] = canonical_checksum(resource)
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
        if middleware is None or binding.target_ref not in resolved_by_ref:
            raise ValueError(f"Middleware {binding.middleware_id} is unavailable.")
        defaults = dict(middleware.get("default_config") or {})
        defaults.update(binding.config)
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

    final_source = declared_outputs.get(
        (intent.final_output.node_ref, intent.final_output.port)
    )
    if final_source is None or final_source[0] != intent.final_output.variable:
        raise ValueError("Graph IR final output does not match its source port.")
    output_node = _resolved_node(
        ref="output",
        node_id="output",
        kind="output",
        role="output",
        title="Final answer",
        config={
            "outputVariable": intent.final_output.variable,
            "template": f"{{{{{intent.final_output.variable}}}}}",
        },
    )
    nodes.append(output_node)
    resolved_by_ref["output"] = output_node
    edges.append(
        ResolvedGraphEdgeV3(
            ref=_stable_ref("control", intent.final_output.node_ref, "output"),
            mode="control",
            source=ResolvedGraphEndpointV3(
                node_ref=intent.final_output.node_ref,
                node_id=resolved_by_ref[intent.final_output.node_ref].node_id,
            ),
            target=ResolvedGraphEndpointV3(node_ref="output", node_id="output"),
            outcome="success",
            join="all",
        )
    )
    edges.append(
        ResolvedGraphEdgeV3(
            ref=_stable_ref(
                "data",
                intent.final_output.node_ref,
                intent.final_output.port,
                "output",
                "result",
                intent.final_output.variable,
            ),
            mode="data",
            source=ResolvedGraphEndpointV3(
                node_ref=intent.final_output.node_ref,
                node_id=resolved_by_ref[intent.final_output.node_ref].node_id,
                port=intent.final_output.port,
            ),
            target=ResolvedGraphEndpointV3(
                node_ref="output", node_id="output", port="result"
            ),
            variable=intent.final_output.variable,
            value_schema=final_source[1],
        )
    )

    prompt_lookup = {item["id"]: item for item in snapshot.prompt_profiles}
    prompt_profiles: list[ResolvedPromptProfileV3] = []
    for profile_id in dict.fromkeys(intent.prompt_profile_ids):
        resource = prompt_lookup.get(profile_id)
        if resource is None or not resource.get("published_version"):
            raise ValueError(f"Prompt Profile {profile_id} is unavailable.")
        prompt_profiles.append(
            ResolvedPromptProfileV3(
                profile_id=profile_id,
                pinned_version=int(resource["published_version"]),
                checksum=canonical_checksum(resource),
            )
        )

    graph = ResolvedGraphIRV3(
        name=intent.name,
        description=intent.description,
        tags=list(dict.fromkeys(intent.tags)),
        starters=list(dict.fromkeys(intent.starters)),
        nodes=sorted(nodes, key=lambda item: item.ref),
        edges=sorted(edges, key=lambda item: item.ref),
        prompt_profiles=prompt_profiles,
        final_output=intent.final_output,
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


def decompile_candidate_to_graph_intent(
    candidate: dict[str, Any],
) -> GraphIntentV3:
    from .node_adapters import decompile_planner_node_v3

    try:
        from server.workflow_native.schemas import NativeWorkflowNode
    except ModuleNotFoundError:
        from workflow_native.schemas import NativeWorkflowNode

    draft = candidate.get("draft") or {}
    workflow = draft.get("workflow") or {}
    raw_nodes = list(workflow.get("nodes") or [])
    node_by_id = {str(node.get("id") or ""): node for node in raw_nodes}
    ref_by_id: dict[str, str] = {}
    business_nodes: list[GraphIntentNodeV3] = []
    for raw_node in raw_nodes:
        data = raw_node.get("data") or {}
        kind = str(data.get("kind") or raw_node.get("type") or "")
        if kind != "workflow_agent":
            continue
        restored = decompile_planner_node_v3(
            NativeWorkflowNode.model_validate(raw_node)
        )
        node_id = str(raw_node.get("id") or "")
        ref_by_id[node_id] = restored.ref
        business_nodes.append(restored)

    control_edges: list[GraphIntentControlEdgeV3] = []
    resources: list[MetaPlannerIRResourceBinding] = []
    middleware: list[MetaPlannerIRMiddlewareBinding] = []
    for raw_edge in workflow.get("edges") or []:
        source_id = str(raw_edge.get("source") or "")
        target_id = str(raw_edge.get("target") or "")
        source_ref = ref_by_id.get(source_id)
        target_ref = ref_by_id.get(target_id)
        source_handle = str(raw_edge.get("sourceHandle") or "")
        target_handle = str(raw_edge.get("targetHandle") or "")
        if source_ref and target_ref and not source_handle and not target_handle:
            control_edges.append(
                GraphIntentControlEdgeV3(
                    source_ref=source_ref,
                    target_ref=target_ref,
                )
            )
            continue
        if not target_ref:
            continue
        source_node = node_by_id.get(source_id) or {}
        source_data = source_node.get("data") or {}
        source_kind = str(source_data.get("kind") or source_node.get("type") or "")
        if target_handle == "middleware" and source_kind == "runtime_middleware":
            middleware.append(
                MetaPlannerIRMiddlewareBinding(
                    target_ref=target_ref,
                    middleware_id=str(source_data.get("runtimeMiddlewareId") or ""),
                    priority=int(source_data.get("middlewarePriority") or 100),
                    config=dict(source_data.get("runtimeMiddlewareConfig") or {}),
                )
            )
            continue
        if source_kind not in {
            "external_xpert",
            "knowledge_base",
            "toolset_resource",
            "plugin_resource",
        }:
            continue
        id_fields = {
            "external_xpert": "xpertId",
            "knowledge_base": "knowledgeBaseId",
            "toolset_resource": "toolsetId",
            "plugin_resource": "pluginId",
        }
        resources.append(
            MetaPlannerIRResourceBinding(
                target_ref=target_ref,
                kind=source_kind,
                resource_id=str(source_data.get(id_fields[source_kind]) or ""),
                tool_name=str(source_data.get("toolName") or ""),
                description=str(source_data.get("description") or ""),
                top_k=int(source_data.get("topK") or 5),
                score_threshold=float(source_data.get("scoreThreshold") or 0),
            )
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
    output_variable = str(output_data.get("outputVariable") or "")
    terminal_edge = next(
        (
            edge
            for edge in workflow.get("edges") or []
            if str(edge.get("target") or "") == str(output_node.get("id") or "")
            and not edge.get("sourceHandle")
            and not edge.get("targetHandle")
        ),
        None,
    )
    terminal_ref = ref_by_id.get(str((terminal_edge or {}).get("source") or ""))
    if not terminal_ref or not output_variable:
        raise ValueError("Compiled candidate final output metadata is incomplete.")
    prompt_ids = [
        str(item.get("profile_id") or "")
        for item in draft.get("prompt_profiles") or []
        if item.get("enabled", True) and item.get("profile_id")
    ]
    return GraphIntentV3(
        name=str(candidate.get("name") or workflow.get("title") or "Xpert"),
        description=str(candidate.get("description") or ""),
        tags=list(candidate.get("tags") or []),
        starters=list(candidate.get("starters") or []),
        nodes=sorted(business_nodes, key=lambda item: item.ref),
        control_edges=sorted(
            control_edges, key=lambda item: (item.source_ref, item.target_ref)
        ),
        resources=resources,
        middleware=middleware,
        prompt_profile_ids=prompt_ids,
        final_output=GraphIntentFinalOutputV3(
            node_ref=terminal_ref,
            variable=output_variable,
        ),
    )


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
