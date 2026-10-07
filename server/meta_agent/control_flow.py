from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
from itertools import product
import math
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
from .control_domains import ControlDomainUnproven, value_partition


CONTROL_FLOW_CONTRACT_VERSION = 2
MAX_ROUTER_NODES = 8
MAX_SYMBOLIC_SCENARIOS = 256
CONTROL_PROOF_CHECKS = ("structure", "predicate_domains", "outcome_coverage", "terminal_coverage", "data_availability")


def _proof_checks(**states: str) -> list[dict[str, Any]]:
    result = []
    blocker = None
    for name in CONTROL_PROOF_CHECKS:
        status = states.get(name, "blocked")
        result.append({"id": name, "status": status,
                       "blocked_by": (blocker or "not_executed") if status == "blocked" else None})
        if status == "failed" and blocker is None:
            blocker = name
    return result


class ControlFlowAnalysisError(ValueError):
    def __init__(self, issues: list[str], *, dependency_issues: list[dict[str, Any]] | None = None,
                 path_issues: list[dict[str, Any]] | None = None,
                 proof_checks: list[dict[str, Any]] | None = None) -> None:
        self.issues = list(dict.fromkeys(issues))
        self.dependency_issues = deepcopy(dependency_issues or [])
        self.path_issues = deepcopy(path_issues or [])
        self.proof_checks = deepcopy(proof_checks if proof_checks is not None else _proof_checks(structure="failed"))
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


def _connection_rule(outcomes: tuple[str, ...]) -> str:
    return "none" if not outcomes else "exactly_once" if len(outcomes) > 1 else "fanout"


def model_control_contract(kind: str, config_schema: dict[str, Any]) -> dict[str, Any]:
    """Project the existing semantic resolver over the contract's bounded config domain."""
    properties = config_schema.get("properties") or {}
    selector, field, values = "constant", None, [None]
    if "failure_action" in properties:
        selector, field = "value", "failure_action"
        values = list(properties[field]["enum"])
    elif "routes" in properties:
        selector, field = "length", "routes"
        values = list(range(properties[field]["minItems"], properties[field]["maxItems"] + 1))
    variants = []
    for value in values:
        config = {field: [{}] * value if selector == "length" else value} if field else {}
        probe = GraphIntentNodeV3(ref="contract", kind=kind, title="出口契约", config=config)
        outcomes = semantic_outcomes(probe)
        variants.append({
            "config_value": value, "outcomes": list(outcomes),
            "connections": _connection_rule(outcomes),
        })
    result = {
        "selector": selector, "config_field": field,
        "default_value": properties.get(field, {}).get("default"),
        "variants": variants,
    }
    if kind in {"condition", "multi_route"}:
        result["predicate_semantics"] = {
            "input_selection": ("空 field 比较整个输入；非空 field 只读取输入对象的同名顶层字段，不支持属性路径。"
                                if kind == "condition" else "所有 routes 规则比较同一完整输入；不支持 field 或属性路径。"),
            "types": "比较按真实类型执行，不把对象、null 或字符串自动转为数字。value_type 不能改变输入类型。",
            "null_safety": "is_null 判断选中值是否为 null，不表示记录存在。Condition 字段比较前须证明对象存在；可空或可缺失字段还需对应保护。",
            "outcomes": ({"matched": "谓词为真；is_null 时表示值为空。", "unmatched": "谓词为假；is_null 时表示值非空。"}
                         if kind == "condition" else {"case_n": "按 routes 顺序选择第一个为真的规则。", "default": "所有规则均为假。"}),
        }
    return result


def _control_connection_state(
    node: GraphIntentNodeV3, indexed_edges: list[tuple[int, GraphIntentControlEdgeV3]],
    *, final_source: bool,
) -> dict[str, Any]:
    expected = semantic_outcomes(node)
    counts = Counter(edge.outcome_ref for _, edge in indexed_edges)
    return {
        "expected_outcomes": list(expected),
        "connections": _connection_rule(expected),
        "actual_counts": {outcome: counts[outcome] for outcome in expected},
        "missing_outcomes": [outcome for outcome in expected if not counts[outcome]]
        if len(expected) > 1 or not final_source else [],
        "duplicate_outcomes": [outcome for outcome in expected if counts[outcome] > 1]
        if len(expected) > 1 else [],
        "unexpected_edge_indices": [index for index, edge in indexed_edges if edge.outcome_ref not in expected],
        "outgoing_edge_indices": [index for index, _ in indexed_edges],
        "final_source": final_source,
    }


def _control_connection_messages(node: GraphIntentNodeV3, state: dict[str, Any]) -> list[str]:
    outgoing = state["outgoing_edge_indices"]
    expected = state["expected_outcomes"]
    if node.kind == "terminate_error":
        return [f"Terminate node {node.ref} cannot have outgoing edges."] if outgoing else []
    issues = []
    if not outgoing and not state["final_source"]:
        issues.append(f"Node {node.ref} is a nonterminal dead end.")
    if outgoing and state["final_source"]:
        issues.append(f"Final source {node.ref} must not have outgoing edges.")
    if len(expected) > 1:
        if state["missing_outcomes"] or state["duplicate_outcomes"] or state["unexpected_edge_indices"]:
            issues.append(f"Router {node.ref} must connect exactly once for outcomes: " + ", ".join(sorted(expected)))
    elif outgoing and state["unexpected_edge_indices"]:
        issues.append(f"Node {node.ref} only permits outcomes: " + ", ".join(sorted(expected)))
    return issues


def control_contract_issues(intent: GraphIntentV3) -> list[dict[str, Any]]:
    outgoing: dict[str, list[tuple[int, GraphIntentControlEdgeV3]]] = defaultdict(list)
    for index, edge in enumerate(intent.control_edges):
        outgoing[edge.source_ref].append((index, edge))
    final_refs = {source.node_ref for source in intent.final_output.sources}
    result = []
    for index, node in enumerate(intent.nodes):
        state = _control_connection_state(node, outgoing[node.ref], final_source=node.ref in final_refs)
        if _control_connection_messages(node, state):
            result.append({"node_index": index, "node_ref": node.ref, **state})
    return result


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
    bases = [
        _schema_seed(variant)
        for schema in schemas
        for variant in (schema.any_of or (schema,))
        if variant.type in {"object", "any"}
    ]
    if field:
        candidates = [
            {**(base or {}), field: value}
            for base in bases
            for value in candidates
        ] + bases
    else:
        # An empty object cannot witness a non-null branch with required fields.
        candidates += bases
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


def _joint_assignments(
    routers: list[GraphIntentNodeV3], nodes: dict[str, GraphIntentNodeV3],
    output_schemas: dict[tuple[str, str], WorkflowValueSchema] | None,
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[GraphIntentNodeV3]] = defaultdict(list)
    domains: list[list[dict[str, str]]] = []
    for node in sorted(routers, key=lambda item: item.ref):
        if node.kind not in {"condition", "multi_route"}:
            domains.append([{node.ref: outcome} for outcome in semantic_outcomes(node)])
            continue
        bindings = [binding for binding in node.inputs if binding.port == "value"]
        if len(bindings) != 1:
            raise ControlFlowAnalysisError([f"Router {node.ref} has unproven outcomes: 输入来源不唯一。"])
        grouped[(bindings[0].source_ref, bindings[0].source_port)].append(node)
    for key, group in sorted(grouped.items()):
        if output_schemas is not None:
            schema = output_schemas.get(key)
        elif key[0] == "input":
            schema = {"user_input": WorkflowValueSchema(type="string"),
                      "conversation_history": WorkflowValueSchema(type="array", items=WorkflowValueSchema(type="object"))}.get(key[1])
        else:
            producer = nodes.get(key[0])
            schema = next((output.value_schema for output in producer.outputs if output.port == key[1]), None) if producer else None
        if schema is None:
            raise ControlFlowAnalysisError([f"Router {group[0].ref} has unproven outcomes: 权威输入类型尚未解析。"])
        predicates = []
        native_rules: dict[str, list[dict[str, Any]]] = {}
        for node in group:
            rules = node.config.get("routes") if node.kind == "multi_route" else [node.config]
            if not isinstance(rules, list) or not rules:
                raise ControlFlowAnalysisError([f"Router {node.ref} has unproven outcomes: 规则为空。"])
            native_rules[node.ref] = []
            for index, rule in enumerate(rules, 1):
                native = {"operator": rule.get("operator"), "valueType": rule.get("value_type")}
                if native["operator"] != "is_null":
                    native["value"] = rule.get("value")
                if node.kind == "multi_route":
                    native.update(id=f"route_{index}", label=rule.get("label"))
                native_rules[node.ref].append(native)
                predicates.append((str(node.config.get("field") or "").strip() if node.kind == "condition" else "", native))
        try:
            values = value_partition(schema, predicates, seed=_schema_seed)
        except ControlDomainUnproven as exc:
            raise ControlFlowAnalysisError([f"Router {group[0].ref} has unproven outcomes: {exc}"]) from None
        signatures: dict[tuple[str, ...], dict[str, str]] = {}
        for value in values:
            choices = {}
            for node in group:
                try:
                    if node.kind == "condition":
                        matched = evaluate_typed_condition(value, field=node.config.get("field", ""),
                            operator=node.config.get("operator"), value_type=node.config.get("value_type"),
                            expected=node.config.get("value"))
                        outcome = "matched" if matched else "unmatched"
                    else:
                        selected = select_multi_route(value, native_rules[node.ref])
                        outcome = "default" if selected == "default" else selected.replace("route_", "case_", 1)
                except WorkflowControlDataError as exc:
                    outcome = "!error:" + exc.code
                choices[node.ref] = outcome
            signatures.setdefault(tuple(choices.values()), choices)
        if not signatures:
            raise ControlFlowAnalysisError([f"Router {group[0].ref} has unproven outcomes: 没有合法输入等价类。"])
        domains.append([signatures[key] for key in sorted(signatures)])
    if math.prod(map(len, domains)) > MAX_SYMBOLIC_SCENARIOS:
        raise ControlFlowAnalysisError(["Control flow input partitions exceed 256 scenarios; 无法完成有界证明。"])
    return [{key: value for group in assignment for key, value in group.items()}
            for assignment in product(*domains)]


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


def analyze_control_flow(
    intent: GraphIntentV3, *,
    output_schemas: dict[tuple[str, str], WorkflowValueSchema] | None = None,
) -> dict[str, Any]:
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

    routers = sorted((node for node in intent.nodes if len(semantic_outcomes(node)) > 1), key=lambda node: node.ref)
    if len(routers) > MAX_ROUTER_NODES:
        issues.append(f"Control flow supports at most {MAX_ROUTER_NODES} route nodes.")

    final_refs = {source.node_ref for source in intent.final_output.sources}
    for node in intent.nodes:
        state = _control_connection_state(
            node, list(enumerate(outgoing.get(node.ref, []))), final_source=node.ref in final_refs,
        )
        issues.extend(_control_connection_messages(node, state))
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
    scenario_keys: set[tuple] = set()
    witnessed: dict[str, set[str]] = defaultdict(set)
    try:
        assignments = _joint_assignments(routers, nodes, output_schemas)
    except ControlFlowAnalysisError as exc:
        exc.proof_checks = _proof_checks(structure="passed", predicate_domains="failed")
        raise
    partial_merge = False
    for choices in assignments:
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
                outcome = choices.get(ref, "success")
                if outcome.startswith("!error:"):
                    issues.append(f"Router {ref} has unproven outcomes: 可达输入触发 {outcome[7:]}，缺少有效的路径保护。")
                else:
                    witnessed[ref].add(outcome)

        # Unreached router assignments and presentation order are not semantics.
        choices = {ref: outcome for ref, outcome in sorted(choices.items()) if ref in reached}
        scenario_key = (tuple(choices.items()), tuple(sorted(reached)))
        if scenario_key in scenario_keys:
            continue
        scenario_keys.add(scenario_key)
        scenario_index = len(scenarios)
        for ref in reached:
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
            partial_merge = True
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

    # Project the failed proof, not witness values or a proposed graph rewrite.
    path_issues: list[dict[str, Any]] = []
    seen_paths: set[tuple] = set()
    for scenario in sorted(scenarios, key=lambda item: (tuple(item["choices"].items()), tuple(item["reached"]))):
        counterexample = {key: deepcopy(scenario[key]) for key in ("choices", "success_sources", "error_sources")}
        counterexample["reached_roots"] = sorted(ref for ref in scenario["reached"] if not incoming.get(ref))
        for ref, outcome in scenario["choices"].items():
            if not outcome.startswith("!error:"):
                continue
            key = ("ROUTER_INPUT_DOMAIN_INVALID", ref, outcome)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            node = nodes[ref]
            bindings = [binding for binding in node.inputs if binding.port == "value"]
            input_contract: dict[str, Any] = {"source_status": "blocked"}
            if len(bindings) == 1 and output_schemas is not None:
                binding = bindings[0]
                source = output_schemas.get((binding.source_ref, binding.source_port))
                if source is not None:
                    field = str(node.config.get("field") or "").strip()
                    prop = source.properties.get(field)
                    input_contract = {
                        "source_status": "resolved", "source_ref": binding.source_ref, "source_port": binding.source_port,
                        "source_type": source.type, "source_nullable": source.nullable,
                        "source_union_types": sorted({item.type for item in source.any_of}),
                        "has_field": bool(field), "field_declared": prop is not None,
                        "field_required": field in source.required,
                        **({"field_type": prop.type, "field_nullable": prop.nullable} if prop else {}),
                    }
            path_issues.append({"code": key[0], "node_ref": ref, "error_code": outcome[7:],
                                "input_contract": input_contract, "counterexample": deepcopy(counterexample)})
        if len(scenario["success_sources"]) + len(scenario["error_sources"]) != 1:
            key = ("CONTROL_TERMINAL_COUNT", tuple(scenario["success_sources"]), tuple(scenario["error_sources"]))
            if key not in seen_paths:
                seen_paths.add(key)
                path_issues.append({"code": key[0],
                    "node_refs": sorted(set(scenario["success_sources"] + scenario["error_sources"])),
                    "counterexample": deepcopy(counterexample)})

    missing_outcomes = False
    for node in routers:
        missing = sorted(set(semantic_outcomes(node)) - witnessed[node.ref])
        if missing:
            missing_outcomes = True
            issues.append(f"Router {node.ref} has shadowed or unproven outcomes: " + ", ".join(missing))
    unreachable = sorted(set(nodes) - set(reached_by_ref))
    if unreachable:
        issues.append("Control flow contains unreachable nodes: " + ", ".join(unreachable))
    dependency_issues: list[dict[str, Any]] = []
    unknown_data_source = False
    for node in intent.nodes:
        target_scenarios = reached_by_ref.get(node.ref, set())
        for input_index, binding in enumerate(node.inputs):
            if binding.source_ref == "input":
                continue
            source_scenarios = reached_by_ref.get(binding.source_ref, set())
            source_node = nodes.get(binding.source_ref)
            if source_node is None:
                unknown_data_source = True
                issues.append(f"Data source {binding.source_ref} references an unknown node.")
                continue
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
                # Reuse the failed proof's scenarios; never infer a repair or expose witness values.
                missing = target_scenarios - source_scenarios
                witness = min(
                    (scenarios[index] for index in missing),
                    key=lambda item: (tuple(item["choices"].items()), tuple(item["reached"])),
                )
                source_reached = binding.source_ref in witness["reached"]
                dependency_issues.append({
                    "code": "DATA_PATH_NOT_GUARANTEED",
                    "node_ref": node.ref, "input_index": input_index, "port": binding.port,
                    "source_ref": binding.source_ref, "source_port": binding.source_port,
                    "reason": "source_result_unavailable" if source_reached else "source_not_reached",
                    "target_scenario_count": len(target_scenarios),
                    "violating_scenario_count": len(missing),
                    "counterexample": {
                        "choices": dict(witness["choices"]),
                        "source_reached": source_reached, "target_reached": True,
                        "source_value_available": False,
                    },
                })
    invalid_predicate = any(item["code"] == "ROUTER_INPUT_DOMAIN_INVALID" for item in path_issues)
    incomplete_paths = invalid_predicate or missing_outcomes or bool(unreachable)
    proof_checks = _proof_checks(
        structure="passed", predicate_domains="failed" if invalid_predicate else "passed",
        outcome_coverage="blocked" if invalid_predicate else "failed" if missing_outcomes or unreachable else "passed",
        terminal_coverage="blocked" if invalid_predicate else "failed" if partial_merge or any(
            item["code"] == "CONTROL_TERMINAL_COUNT" for item in path_issues) else "passed",
        data_availability="failed" if dependency_issues or unknown_data_source else "blocked" if incomplete_paths else "passed",
    )
    if issues:
        raise ControlFlowAnalysisError(issues, dependency_issues=dependency_issues, path_issues=path_issues,
                                       proof_checks=proof_checks)

    scenarios.sort(key=lambda scenario: (tuple(scenario["choices"].items()), tuple(scenario["reached"])))
    for index, scenario in enumerate(scenarios, 1):
        scenario["id"] = f"scenario_{index}"

    return {
        "version": CONTROL_FLOW_CONTRACT_VERSION,
        "router_count": len(routers),
        "scenario_count": len(scenarios),
        "final_source_count": len(intent.final_output.sources),
        "scenarios": scenarios,
        "unreachable_nodes": [],
        "proof_checks": proof_checks,
    }
