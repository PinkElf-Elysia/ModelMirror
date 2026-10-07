"""Translate retained semantic repairs into the existing, bounded Patch protocol."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from .failed_artifacts import _retained_recipe
from .generation_recipe import GenerationRecipeV1, lower_generation_recipe, parse_generation_recipe
from .graph_ir_v3 import resolve_node_resource_snapshot
from .graph_patch import (
    ConnectDataOperation, DisconnectDataOperation, GraphPatchEnvelopeV1,
    GraphPatchLimitError, GRAPH_PATCH_MAX_OPERATIONS,
    apply_graph_patch, diff_graph_intents,
)
from .node_adapters import get_planner_node_adapter
from ..workflow_native.node_contracts import canonical_checksum


def retained_recipe(selected, state) -> GenerationRecipeV1 | None:
    raw = selected.get("source_recipe")
    if raw is None:
        return None  # Legacy artifacts are never reverse-engineered into a Recipe.
    if selected.get("recipe_format", "recipe_v1") != "recipe_v1":
        raise ValueError("修复依据的生成描述格式不受支持。")
    recipe = parse_generation_recipe(raw)
    safe, status = _retained_recipe(recipe)
    if status != "retained" or safe != raw or canonical_checksum(safe) != selected.get("source_recipe_checksum"):
        raise ValueError("修复依据的生成描述完整性校验失败。")
    lowered = lower_generation_recipe(recipe, state.request, state.snapshot)
    if canonical_checksum(lowered.model_dump(mode="json")) != canonical_checksum(state.intent.model_dump(mode="json")):
        raise ValueError("生成描述与保留原图不一致，或资源契约已漂移；禁止猜测还原。")
    return recipe


def _semantic_projection(intent, snapshot):
    """Normalize only Adapter-owned types/defaults, preserving ordered input bindings."""
    payload = intent.model_dump(mode="json")
    for node, raw in zip(intent.nodes, payload["nodes"]):
        adapter = get_planner_node_adapter(node.kind)
        parsed = adapter.validate_intent_node(node)
        resource = resolve_node_resource_snapshot(node, snapshot)
        resource_payload = resource.model_dump(mode="json") if resource else None
        adapter.validate_resolved_resource(node, parsed, resource_payload)
        raw["config"] = parsed.model_dump(mode="json")
        for output in raw["outputs"]:
            output["value_schema"] = adapter.authoritative_output_schema(output["port"], parsed, resource_payload).model_dump(mode="json")
    sources = {(node["ref"], output["port"]): output for node in payload["nodes"] for output in node["outputs"]}
    for node in payload["nodes"]:
        for binding in node["inputs"]:
            source = sources.get((binding["source_ref"], binding["source_port"]))
            if source is not None:
                # Variables and source identity are compared unchanged; only type evidence is derived.
                binding["value_schema"] = deepcopy(source["value_schema"])
    payload["nodes"].sort(key=lambda item: item["ref"])
    for name in ("control_edges", "resources", "middleware"):
        payload[name].sort(key=canonical_checksum)
    return payload


def _ordered_input_operations(ref, before, after):
    """Retain the longest append-compatible prefix; one disconnect removes all duplicate keys."""
    def key(binding):
        return (binding.source_ref, binding.source_port, ref, binding.port)

    counts = Counter(key(binding) for binding in before)
    if len({key(binding) for binding in after}) != len(after):
        raise ValueError("修复后的输入连接重复，不能生成 Patch。")
    retained, cursor, prefix = set(), 0, 0
    for binding in after:
        position = next((index for index in range(cursor, len(before))
                         if before[index] == binding and counts[key(binding)] == 1), None)
        if position is None:
            break
        retained.add(key(binding))
        cursor, prefix = position + 1, prefix + 1
    disconnects = []
    for edge in dict.fromkeys(key(binding) for binding in before):
        if edge not in retained:
            disconnects.append(DisconnectDataOperation(source_ref=edge[0], source_port=edge[1],
                target_ref=edge[2], target_port=edge[3]))
    connects = [ConnectDataOperation(source_ref=b.source_ref, source_port=b.source_port,
        target_ref=ref, target_port=b.port) for b in after[prefix:]]
    return disconnects, connects


def recipe_repair_patch(state, raw) -> GraphPatchEnvelopeV1:
    recipe = parse_generation_recipe(raw)
    if _retained_recipe(recipe)[1] != "retained":
        raise ValueError("修复描述不符合安全保留契约。")
    target = lower_generation_recipe(recipe, state.request, state.snapshot)
    source = state.intent
    before_nodes = {node.ref: node for node in source.nodes}
    target_nodes = {node.ref: node for node in target.nodes}
    diff_source = source.model_copy(deep=True)
    for node in diff_source.nodes:
        after = target_nodes.get(node.ref)
        if after is not None and after.kind == node.kind:
            schemas = {output.port: output.value_schema for output in after.outputs}
            # These are server-lowered schemas, never model-supplied Patch fields.
            for output in node.outputs:
                if output.port in schemas:
                    output.value_schema = schemas[output.port]
            # Ordered data changes are derived below, separately from the set-based editor diff.
            node.inputs = deepcopy(after.inputs)
    patch = diff_graph_intents(diff_source, target, proposal_revision=state.proposal.revision,
        expected_graph_checksum=state.graph_checksum, expected_candidate_checksum=state.candidate_checksum)

    # Diff's set view is sufficient for graph edges, but aggregator/Agent input order is semantic.
    reconnect = {ref for ref, node in target_nodes.items()
                 if ref not in before_nodes or node.inputs != before_nodes[ref].inputs}
    disconnects, connects = [], []
    for ref in sorted(reconnect):
        removed, added = _ordered_input_operations(ref, before_nodes[ref].inputs if ref in before_nodes else [],
                                                   target_nodes[ref].inputs)
        disconnects.extend(removed)
        connects.extend(added)
    other = [op for op in patch.operations if not (
        isinstance(op, (ConnectDataOperation, DisconnectDataOperation)) and op.target_ref in reconnect)]
    operations = [*disconnects, *other, *connects]
    if len(operations) > GRAPH_PATCH_MAX_OPERATIONS:
        raise GraphPatchLimitError(len(operations))
    patch = GraphPatchEnvelopeV1(**patch.model_dump(exclude={"operations"}), operations=operations)
    replay = apply_graph_patch(source, patch, plan_task_ids={task.task_id for task in state.plan.tasks},
                               allowed_node_kinds=set(state.scope.allowed_node_kinds)).intent
    if canonical_checksum(_semantic_projection(replay, state.snapshot)) != canonical_checksum(_semantic_projection(target, state.snapshot)):
        raise ValueError("语义修复与服务端 Patch 往返不一致，已阻断建议。")
    return patch
