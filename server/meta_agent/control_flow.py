from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from itertools import product
from typing import Any

try:
    from server.workflow_native.control_data import (
        WorkflowControlDataError,
        evaluate_typed_condition,
        select_multi_route,
    )
    from server.workflow_native.node_contracts import WorkflowValueSchema
except ModuleNotFoundError:
    from workflow_native.control_data import (
        WorkflowControlDataError,
        evaluate_typed_condition,
        select_multi_route,
    )
    from workflow_native.node_contracts import WorkflowValueSchema

from .schemas import GraphIntentControlEdgeV3, GraphIntentNodeV3, GraphIntentV3


CONTROL_FLOW_CONTRACT_VERSION = 2
MAX_ROUTER_NODES = 8
MAX_SYMBOLIC_SCENARIOS = 256


class ControlFlowAnalysisError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = list(dict.fromkeys(issues))
        super().__init__("; ".join(self.issues))


def semantic_outcomes(node: GraphIntentNodeV3) -> tuple[str, ...]:
    if node.kind == "condition":
        return ("matched", "unmatched")
    if node.kind == "multi_route":
        routes = node.config.get("routes")
        count = len(routes) if isinstance(routes, list) else 0
        return tuple([*(f"case_{index}" for index in range(1, count + 1)), "default"])
    if node.kind == "terminate_error":
        return ()
    if (
        node.kind in {"knowledge_retrieval", "data_table_query"}
        and str(node.config.get("failure_action") or "stop") == "error_output"
    ):
        return ("success", "error")
    return ("success",)


def native_outcome_map(node: GraphIntentNodeV3) -> dict[str, str]:
    if node.kind == "condition":
        return {"matched": "true", "unmatched": "false"}
    if node.kind == "multi_route":
        return {
            **{
                f"case_{index}": f"route_{index}"
                for index in range(1, len(node.config.get("routes") or []) + 1)
            },
            "default": "default",
        }
    if node.kind == "terminate_error":
        return {}
    if (
        node.kind in {"knowledge_retrieval", "data_table_query"}
        and str(node.config.get("failure_action") or "stop") == "error_output"
    ):
        return {"success": "", "error": "error"}
    return {"success": ""}


def semantic_outcome_from_native(
    node: GraphIntentNodeV3,
    source_handle: str,
) -> str:
    matches = [
        semantic
        for semantic, native in native_outcome_map(node).items()
        if native == source_handle
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Node {node.ref} has no unique semantic outcome for native handle "
            f"{source_handle or '<default>'}."
        )
    return matches[0]


def _candidate_values(raw_rules: list[dict[str, Any]]) -> list[Any]:
    values: list[Any] = [None, "", "__other__", 0, 1, -1, True, False, [], {}]
    for rule in raw_rules:
        if str(rule.get("operator") or "") == "is_null":
            continue
        value = deepcopy(rule.get("value"))
        values.append(value)
        if isinstance(value, bool):
            values.append(not value)
        elif isinstance(value, (int, float)):
            values.extend([value - 1, value + 1])
        elif isinstance(value, str):
            values.extend([f"prefix-{value}-suffix", f"{value}__other__"])
        elif isinstance(value, list):
            values.extend(value[:8])
            values.append([*value, "__other__"])
    unique: list[Any] = []
    fingerprints: set[str] = set()
    for value in values:
        fingerprint = repr(value)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        unique.append(value)
    return unique[:128]


def _schema_seed(schema: WorkflowValueSchema) -> Any:
    if schema.any_of:
        return _schema_seed(schema.any_of[0])
    if schema.type == "object":
        return {
            name: _schema_seed(schema.properties[name])
            for name in schema.required
        }
    return {
        "string": "", "number": 0, "integer": 0,
        "boolean": False, "array": [],
    }.get(schema.type)


def _input_witnesses(
    node: GraphIntentNodeV3,
    nodes: dict[str, GraphIntentNodeV3],
    rules: list[dict[str, Any]],
    *,
    field: str = "",
) -> list[Any]:
    bindings = [binding for binding in node.inputs if binding.port == "value"]
    if len(bindings) != 1:
        return []
    binding = bindings[0]
    if binding.source_ref == "input":
        source_schema = {
            "user_input": WorkflowValueSchema(type="string"),
            "conversation_history": WorkflowValueSchema(
                type="array", items=WorkflowValueSchema(type="object")
            ),
        }.get(binding.source_port)
    else:
        source = nodes.get(binding.source_ref)
        source_schema = next(
            (output.value_schema for output in source.outputs
             if output.port == binding.source_port),
            None,
        ) if source else None
    if source_schema is None:
        return []

    # Resolution validates producer schemas before this analysis. A consumer
    # widened to `any` must not invent witnesses outside that producer's type.
    schemas = (source_schema, binding.value_schema)
    candidates = _candidate_values(rules)
    if field:
        bases = [
            _schema_seed(variant)
            for schema in schemas
            for variant in (schema.any_of or (schema,))
            if variant.type in {"object", "any"}
        ]
        candidates = [
            {**(base or {}), field: value}
            for base in bases
            for value in candidates
        ] + bases
    valid: list[Any] = []
    for value in candidates:
        try:
            for schema in schemas:
                schema.assert_value(value)
        except ValueError:
            continue
        valid.append(value)
    return valid[:256]


def _condition_witnesses(
    node: GraphIntentNodeV3, nodes: dict[str, GraphIntentNodeV3]
) -> tuple[str, ...]:
    config = node.config
    field = str(config.get("field") or "")
    raw_rule = {
        "operator": config.get("operator"),
        "valueType": config.get("value_type"),
    }
    if str(config.get("operator") or "") != "is_null":
        raw_rule["value"] = config.get("value")
    found: set[str] = set()
    for value in _input_witnesses(node, nodes, [raw_rule], field=field):
        try:
            matched = evaluate_typed_condition(
                value,
                field=field,
                operator=config.get("operator"),
                value_type=config.get("value_type"),
                expected=config.get("value"),
            )
        except WorkflowControlDataError:
            return ()
        found.add("matched" if matched else "unmatched")
    return tuple(sorted(found))


def _route_witnesses(
    node: GraphIntentNodeV3, nodes: dict[str, GraphIntentNodeV3]
) -> tuple[str, ...]:
    raw_routes = node.config.get("routes")
    if not isinstance(raw_routes, list):
        return ()
    native_routes = []
    for index, raw in enumerate(raw_routes, start=1):
        if not isinstance(raw, dict):
            return ()
        route = {
            "id": f"route_{index}",
            "label": raw.get("label"),
            "operator": raw.get("operator"),
            "valueType": raw.get("value_type"),
        }
        if str(raw.get("operator") or "") != "is_null":
            route["value"] = raw.get("value")
        native_routes.append(route)
    found: set[str] = set()
    for value in _input_witnesses(node, nodes, native_routes):
        try:
            selected = select_multi_route(value, native_routes)
        except WorkflowControlDataError:
            return ()
        found.add(
            "default"
            if selected == "default"
            else f"case_{int(selected.removeprefix('route_'))}"
        )
    return tuple(sorted(found))


def _topological_order(
    refs: list[str],
    edges: list[GraphIntentControlEdgeV3],
) -> tuple[list[str], dict[str, list[GraphIntentControlEdgeV3]], dict[str, list[GraphIntentControlEdgeV3]]]:
    incoming: dict[str, list[GraphIntentControlEdgeV3]] = defaultdict(list)
    outgoing: dict[str, list[GraphIntentControlEdgeV3]] = defaultdict(list)
    indegree = {ref: 0 for ref in refs}
    for edge in edges:
        incoming[edge.target_ref].append(edge)
        outgoing[edge.source_ref].append(edge)
        indegree[edge.target_ref] += 1
    queue = deque(sorted(ref for ref, count in indegree.items() if count == 0))
    order: list[str] = []
    while queue:
        ref = queue.popleft()
        order.append(ref)
        for edge in sorted(
            outgoing.get(ref, []), key=lambda item: (item.outcome_ref, item.target_ref)
        ):
            indegree[edge.target_ref] -= 1
            if indegree[edge.target_ref] == 0:
                queue.append(edge.target_ref)
    return order, incoming, outgoing


def analyze_control_flow(intent: GraphIntentV3) -> dict[str, Any]:
    nodes = {node.ref: node for node in intent.nodes}
    issues: list[str] = []
    if len(nodes) != len(intent.nodes):
        issues.append("Control flow node refs must be unique.")
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in intent.control_edges:
        if edge.source_ref not in nodes or edge.target_ref not in nodes:
            issues.append(
                f"Control edge {edge.source_ref}:{edge.outcome_ref}->{edge.target_ref} "
                "references an unknown node."
            )
            continue
        key = (edge.source_ref, edge.outcome_ref, edge.target_ref)
        if edge.source_ref == edge.target_ref or key in edge_keys:
            issues.append("Control edges must be unique and non-reflexive.")
        edge_keys.add(key)

    if issues:
        raise ControlFlowAnalysisError(issues)

    order, incoming, outgoing = _topological_order(list(nodes), intent.control_edges)
    if len(order) != len(nodes):
        issues.append("Control flow must be acyclic.")

    routers = [node for node in intent.nodes if len(semantic_outcomes(node)) > 1]
    if len(routers) > MAX_ROUTER_NODES:
        issues.append(f"Control flow supports at most {MAX_ROUTER_NODES} route nodes.")

    route_domains: list[tuple[str, tuple[str, ...]]] = []
    final_refs = {source.node_ref for source in intent.final_output.sources}
    for node in intent.nodes:
        expected = set(semantic_outcomes(node))
        actual = [edge.outcome_ref for edge in outgoing.get(node.ref, [])]
        actual_set = set(actual)
        if node.kind == "terminate_error":
            if actual:
                issues.append(f"Terminate node {node.ref} cannot have outgoing edges.")
            continue
        if not actual and node.ref not in final_refs:
            issues.append(f"Node {node.ref} is a nonterminal dead end.")
        if actual and node.ref in final_refs:
            issues.append(f"Final source {node.ref} must not have outgoing edges.")
        if len(expected) > 1:
            if actual_set != expected or len(actual) != len(expected):
                issues.append(
                    f"Router {node.ref} must connect exactly once for outcomes: "
                    + ", ".join(sorted(expected))
                )
            if node.kind in {"condition", "multi_route"}:
                witnesses = (
                    _condition_witnesses(node, nodes)
                    if node.kind == "condition"
                    else _route_witnesses(node, nodes)
                )
                missing_witnesses = sorted(expected - set(witnesses))
                if missing_witnesses:
                    issues.append(
                        f"Router {node.ref} has shadowed or unproven outcomes: "
                        + ", ".join(missing_witnesses)
                    )
            route_domains.append((node.ref, tuple(sorted(expected))))
        elif actual and actual_set != expected:
            issues.append(
                f"Node {node.ref} only permits outcomes: "
                + ", ".join(sorted(expected))
            )

    scenario_count = 1
    for _ref, outcomes in route_domains:
        scenario_count *= max(1, len(outcomes))
    if scenario_count > MAX_SYMBOLIC_SCENARIOS:
        issues.append(
            f"Control flow expands to {scenario_count} scenarios; the limit is "
            f"{MAX_SYMBOLIC_SCENARIOS}."
        )
    if issues:
        raise ControlFlowAnalysisError(issues)

    final_sources = {(item.node_ref, item.port) for item in intent.final_output.sources}
    if len(final_sources) != len(intent.final_output.sources):
        raise ControlFlowAnalysisError(["Final output sources must be unique."])
    for node in intent.nodes:
        if node.kind != "data_merge":
            continue
        left_sources = {
            binding.source_ref for binding in node.inputs if binding.port == "left"
        }
        right_sources = {
            binding.source_ref for binding in node.inputs if binding.port == "right"
        }
        if (
            len(left_sources) != 1
            or len(right_sources) != 1
            or left_sources == right_sources
        ):
            issues.append(
                f"Data merge {node.ref} requires two distinct, uniquely bound "
                "left and right sources."
            )
    if issues:
        raise ControlFlowAnalysisError(issues)

    scenarios: list[dict[str, Any]] = []
    reached_by_ref: dict[str, set[int]] = defaultdict(set)
    domains = [domain for _ref, domain in route_domains]
    assignments = product(*domains) if domains else [()]
    for scenario_index, selected in enumerate(assignments):
        choices = {
            route_domains[index][0]: selected[index]
            for index in range(len(route_domains))
        }
        reached: set[str] = set()
        arrived_edges: set[tuple[str, str, str]] = set()
        merge_partial: list[str] = []
        for ref in order:
            node = nodes[ref]
            incoming_edges = incoming.get(ref, [])
            if not incoming_edges:
                node_reached = True
            else:
                arrived = [
                    edge
                    for edge in incoming_edges
                    if edge.source_ref in reached
                    and (
                        choices.get(edge.source_ref, "success") == edge.outcome_ref
                    )
                ]
                node_reached = bool(arrived)
                if node.kind == "data_merge" and arrived:
                    left_sources = {
                        binding.source_ref
                        for binding in node.inputs
                        if binding.port == "left"
                    }
                    right_sources = {
                        binding.source_ref
                        for binding in node.inputs
                        if binding.port == "right"
                    }
                    arrived_sources = {edge.source_ref for edge in arrived}
                    node_reached = (left_sources | right_sources).issubset(
                        arrived_sources
                    )
                    if not node_reached:
                        merge_partial.append(ref)
                for edge in arrived:
                    arrived_edges.add(
                        (edge.source_ref, edge.outcome_ref, edge.target_ref)
                    )
            if node_reached:
                reached.add(ref)
                reached_by_ref[ref].add(scenario_index)

        successes = sorted(
            source.node_ref
            for source in intent.final_output.sources
            if source.node_ref in reached
        )
        errors = sorted(
            ref for ref in reached if nodes[ref].kind == "terminate_error"
        )
        terminal_count = len(successes) + len(errors)
        if merge_partial:
            issues.append(
                f"Scenario {scenario_index + 1} reaches only one side of data_merge: "
                + ", ".join(sorted(merge_partial))
            )
        if terminal_count != 1:
            issues.append(
                f"Scenario {scenario_index + 1} reaches {terminal_count} terminals "
                f"(success={successes}, error={errors})."
            )
        scenarios.append(
            {
                "id": f"scenario_{scenario_index + 1}",
                "choices": dict(sorted(choices.items())),
                "reached": sorted(reached),
                "outcomes": [
                    f"{source}:{outcome}"
                    for source, outcome, _target in sorted(arrived_edges)
                ],
                "success_sources": successes,
                "error_sources": errors,
            }
        )

    unreachable = sorted(set(nodes) - set(reached_by_ref))
    if unreachable:
        issues.append("Control flow contains unreachable nodes: " + ", ".join(unreachable))
    for node in intent.nodes:
        target_scenarios = reached_by_ref.get(node.ref, set())
        for binding in node.inputs:
            if binding.source_ref == "input":
                continue
            source_scenarios = reached_by_ref.get(binding.source_ref, set())
            source_node = nodes[binding.source_ref]
            if (
                source_node.kind in {"knowledge_retrieval", "data_table_query"}
                and str(source_node.config.get("failure_action") or "stop")
                == "error_output"
                and binding.source_port == "result"
            ):
                source_scenarios = {
                    scenario_index
                    for scenario_index in source_scenarios
                    if scenarios[scenario_index]["choices"].get(
                        binding.source_ref
                    )
                    == "success"
                }
            if not target_scenarios.issubset(source_scenarios):
                issues.append(
                    f"Data source {binding.source_ref}.{binding.source_port} is not "
                    f"available in every scenario and is not guaranteed for "
                    f"{node.ref}.{binding.port}."
                )
    if issues:
        raise ControlFlowAnalysisError(issues)

    return {
        "version": CONTROL_FLOW_CONTRACT_VERSION,
        "router_count": len(routers),
        "scenario_count": len(scenarios),
        "final_source_count": len(intent.final_output.sources),
        "scenarios": scenarios,
        "unreachable_nodes": [],
    }
