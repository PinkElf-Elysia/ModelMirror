"""Bounded local facts, never a substitute graph or a publish decision."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .control_flow import MAX_ROUTER_NODES, ControlFlowAnalysisError, _joint_assignments, analyze_control_flow, semantic_outcomes
from .generation_diagnostics import MAX_DIAGNOSTIC_ISSUES, _PRIVATE_TOKEN, _SEMANTIC_OUTCOMES, _TOKEN, diagnostic_checksum
from .graph_ir_v3 import graph_input_type_issue
from .node_adapters import get_planner_node_adapter, workflow_node_contract_registry
from .schemas import GraphIntentNodeV3, GraphIntentV3


def _safe_detail(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"node_ref", "source_ref", "source_port", "target_port", "port"}:
                if isinstance(item, str) and _TOKEN.fullmatch(item) and not _PRIVATE_TOKEN.search(item):
                    result[key] = item
                else:
                    result[key + "_checksum"] = diagnostic_checksum(item)
            else:
                result[key] = _safe_detail(item)
        return result
    if isinstance(value, list):
        return [_safe_detail(item) for item in value]
    return value


def recipe_dependency_feedback(recipe: Any, input_origins: dict, dependencies: list[dict]) -> dict:
    """Translate proof failures using this lowering's source map, not ref heuristics."""
    control_locations: dict[str, list] = {}

    def visit(steps, path):
        for index, step in enumerate(steps):
            location = [*path, index]
            if step.type == "parallel":
                for branch_index, branch in enumerate(step.paths):
                    visit(branch, [*location, "paths", branch_index])
            else:
                control_locations.setdefault(step.node_ref, []).append([*location, "node_ref"])
                for branch_index, branch in enumerate(step.branches):
                    visit(branch.steps, [*location, "branches", branch_index, "steps"])

    if recipe is not None:
        visit(recipe.control_flow, ["control_flow"])
    entries = []
    for issue in dependencies[:MAX_DIAGNOSTIC_ISSUES]:
        origin = input_origins.get((issue.get("node_ref"), issue.get("input_index")))
        mapped = bool(origin and origin["locations"]
                      and (origin["source_ref"], origin["source_port"]) == (issue.get("source_ref"), issue.get("source_port")))
        entry = {key: deepcopy(issue[key]) for key in (
            "code", "source_ref", "source_port", "reason", "target_scenario_count", "violating_scenario_count",
            "violating_control_locations",
        ) if key in issue}
        entry["mapping_status"] = "mapped" if mapped else "unavailable"
        if mapped:
            entry.update(node_ref=origin["node_ref"], locations=deepcopy(origin["locations"]))
            locations = control_locations.get(origin["node_ref"], [])
            if len(locations) == 1:
                entry["control_location"] = locations[0]
            elif locations:
                entry["control_locations"] = locations
        # Proof choices contain only semantic outcomes; never include witness values.
        counterexample = issue.get("counterexample", {})
        choices = counterexample.get("choices", {})
        safe_choices = {ref: outcome for ref, outcome in choices.items()
                        if _TOKEN.fullmatch(ref) and not _PRIVATE_TOKEN.search(ref) and outcome in _SEMANTIC_OUTCOMES}
        entry["counterexample"] = {
            "choices": safe_choices, "omitted_choice_count": len(choices) - len(safe_choices),
            **{key: counterexample[key] for key in ("source_reached", "target_reached", "source_value_available")
               if isinstance(counterexample.get(key), bool)},
        }
        entries.append(_safe_detail(entry))
    return {
        "issues": entries, "issue_count": len(dependencies),
        "omitted_issue_count": max(0, len(dependencies) - MAX_DIAGNOSTIC_ISSUES),
        "obligation": "按原始 locations 核对消费节点与控制位置。反例中未到达的来源不能供公共节点使用；若各分支需要不同输入，可用不同 Agent ref 覆盖同一固定任务，并更新互斥 final_output.sources。保留业务读写和授权，不以删除步骤、串行化分支或伪造空值绕过。映射 unavailable 时不得猜测位置。",
    }


def compact_recipe_diagnostics(details: dict) -> dict:
    """Prompt-only projection; the full passive report remains unchanged."""
    return {key: deepcopy(details[key]) for key in (
        "version", "stage", "checks_executed", "failed_phase", "issue_count", "issues",
        "category_counts", "omitted_issue_count", "phase_results", "control_contract_issues",
        "input_contract_issues", "input_contract_issue_count", "control_proof_checks",
    ) if key in details}


def known_recipe_source_dependencies(recipe, nodes, control_edges, origins, local):
    """Check only resolved bindings; this projection can never authorize a graph.

    Invalid placeholders stay in their original Prompt. Their unknown edges are
    not inferred, and a clean subset is never reported as a complete path proof.
    """
    if local.get("input_type_issues") or local.get("input_shape_issues"):
        return {"status": "blocked", "blocked_by": "input_contract", "issues": []}
    try:
        projection = GraphIntentV3(**recipe.model_dump(exclude={"generation_protocol_version", "nodes", "control_flow"}),
            nodes=list(nodes.values()), control_edges=control_edges)
        outputs = {(node.ref, port.port): port.value_schema for node in nodes.values() for port in node.outputs}
        outputs.update({("input", port.name): port.value_schema
                        for port in workflow_node_contract_registry.require("input").ports if port.direction == "output"})
        analyze_control_flow(projection, output_schemas=outputs)
    except ControlFlowAnalysisError as error:
        # Invalid predicate domains cannot provide a trustworthy dependency witness.
        domains = next((item["status"] for item in error.proof_checks if item["id"] == "predicate_domains"), "blocked")
        if domains != "passed":
            return {"status": "blocked", "blocked_by": "path_prerequisite", "issues": []}
        result = recipe_dependency_feedback(recipe, origins, error.dependency_issues)
        result.pop("obligation")
        return {"status": "partial", **result}
    except ValueError:
        return {"status": "blocked", "blocked_by": "path_prerequisite", "issues": []}
    return {"status": "partial", "issues": [], "issue_count": 0, "omitted_issue_count": 0}


def recipe_occurrence_dependencies(recipe, nodes, origins, local):
    """Negative dependency facts for mutually exclusive occurrences, never an IR.

    Keep the original positions and use the authority's joint predicate domains.
    No deduplication, source selection, scheduling or suggested control rewrite is
    performed. Unsupported joins or ambiguous repeated execution remain unknown.
    """
    from .generation_recipe import MAX_FLOW_DEPTH

    def blocked(reason):
        return {"status": "blocked", "blocked_by": reason, "issues": [], "issue_count": None}

    if local.get("input_type_issues") or local.get("input_shape_issues"):
        return blocked("input_contract")
    if any(node.kind == "data_merge" for node in nodes.values()):
        return blocked("join_requires_valid_graph")
    locations: dict[str, list] = {}
    occurrence_count = 0

    def inspect(steps, path, depth):
        nonlocal occurrence_count
        if depth > MAX_FLOW_DEPTH:
            raise ValueError("control_depth")
        continues = True
        for index, step in enumerate(steps):
            occurrence_count += 1
            if occurrence_count > 64:
                raise ValueError("occurrence_limit")
            if not continues:
                raise ValueError("after_terminal")
            location = [*path, index]
            if step.type == "parallel":
                if any(not branch for branch in step.paths):
                    raise ValueError("empty_parallel_path")
                exits = [inspect(branch, [*location, "paths", i], depth + 1) for i, branch in enumerate(step.paths)]
            else:
                node = nodes.get(step.node_ref)
                if node is None:
                    raise ValueError("unknown_node")
                locations.setdefault(step.node_ref, []).append([*location, "node_ref"])
                outcomes = semantic_outcomes(node)
                declared = [branch.outcome_ref for branch in step.branches]
                expected = outcomes if len(outcomes) > 1 else ()
                if len(declared) != len(set(declared)) or set(declared) != set(expected):
                    raise ValueError("branch_outcomes")
                exits = [inspect(branch.steps, [*location, "branches", i, "steps"], depth + 1)
                         for i, branch in enumerate(step.branches)] if step.branches else [bool(outcomes)]
            continues = any(exits)
        return continues

    try:
        inspect(recipe.control_flow, ["control_flow"], 1)
    except ValueError as error:
        return blocked(str(error))
    if set(locations) != set(nodes):
        return blocked("omitted_node")
    repeated = sorted(ref for ref, items in locations.items() if len(items) > 1)
    if not repeated:
        return blocked("structure_requires_valid_graph")
    routers = [node for node in nodes.values() if len(semantic_outcomes(node)) > 1]
    if len(routers) > MAX_ROUTER_NODES or any(node.ref in repeated for node in routers):
        return blocked("router_limit_or_repetition")
    outputs = {(node.ref, port.port): port.value_schema for node in nodes.values() for port in node.outputs}
    outputs.update({("input", port.name): port.value_schema
                    for port in workflow_node_contract_registry.require("input").ports if port.direction == "output"})
    try:
        assignments = _joint_assignments(routers, nodes, outputs)
    except ControlFlowAnalysisError:
        return blocked("predicate_domains")
    scenarios, seen = [], set()
    for choices in assignments:
        reached: dict[str, list] = {}

        def visit(steps, path, incoming=True):
            for index, step in enumerate(steps):
                if not incoming:
                    break
                location = [*path, index]
                if step.type == "parallel":
                    exits = [visit(branch, [*location, "paths", i]) for i, branch in enumerate(step.paths)]
                    incoming = any(exits)
                    continue
                ref = step.node_ref
                reached.setdefault(ref, []).append([*location, "node_ref"])
                outcome = choices.get(ref, "success")
                if outcome.startswith("!error:"):
                    raise ValueError("predicate_path_guard")
                if step.branches:
                    i, branch = next((i, branch) for i, branch in enumerate(step.branches) if branch.outcome_ref == outcome)
                    incoming = visit(branch.steps, [*location, "branches", i, "steps"])
                else:
                    incoming = bool(semantic_outcomes(nodes[ref]))
            return incoming

        try:
            visit(recipe.control_flow, ["control_flow"])
        except ValueError:
            return blocked("predicate_path_guard")
        if any(len(items) > 1 for items in reached.values()):
            return blocked("co_reachable_repetition")
        choices = {ref: outcome for ref, outcome in sorted(choices.items()) if ref in reached}
        signature = (tuple(choices.items()), tuple(sorted(reached)))
        if signature not in seen:
            seen.add(signature)
            scenarios.append({"choices": choices, "reached": reached})

    dependencies = []
    for node in nodes.values():
        targets = [scenario for scenario in scenarios if node.ref in scenario["reached"]]
        for input_index, binding in enumerate(node.inputs):
            if binding.source_ref == "input":
                continue
            source = nodes[binding.source_ref]
            needs_success = (source.kind in {"knowledge_retrieval", "data_table_query"}
                             and source.config.get("failure_action") == "error_output" and binding.source_port == "result")
            missing = [scenario for scenario in targets if binding.source_ref not in scenario["reached"]
                       or (needs_success and scenario["choices"].get(binding.source_ref) != "success")]
            if not missing:
                continue
            witness = min(missing, key=lambda item: tuple(item["choices"].items()))
            source_reached = binding.source_ref in witness["reached"]
            violating = [location for location in locations[node.ref]
                         if any(location in scenario["reached"][node.ref] for scenario in missing)]
            dependencies.append({"code": "DATA_PATH_NOT_GUARANTEED", "node_ref": node.ref, "input_index": input_index,
                "source_ref": binding.source_ref, "source_port": binding.source_port,
                "reason": "source_result_unavailable" if source_reached else "source_not_reached",
                "target_scenario_count": len(targets), "violating_scenario_count": len(missing),
                "violating_control_locations": violating,
                "counterexample": {"choices": witness["choices"], "source_reached": source_reached,
                                   "target_reached": True, "source_value_available": False}})
    result = recipe_dependency_feedback(recipe, origins, dependencies)
    result.pop("obligation")
    failed_refs = {item["node_ref"] for item in dependencies}
    final_refs = {item.node_ref for item in recipe.final_output.sources}
    return {"status": "partial", **result, "scenario_count": len(scenarios),
            "repeated_nodes": _safe_detail([{
                "node_ref": ref, "control_locations": locations[ref], "known_input_count": len(nodes[ref].inputs),
                "input_availability": "failed" if ref in failed_refs else "not_disproved", "final_source": ref in final_refs,
            } for ref in repeated])}


def recipe_repair_feedback(recipe: Any, input_origins: dict, dependencies: list[dict],
                           paths: list[dict], local: dict, details: dict) -> dict:
    """Order existing proof facts at the original Recipe boundary; never repair it."""
    focus = recipe_dependency_feedback(recipe, input_origins, dependencies)
    nodes = {node.ref: (index, node) for index, node in enumerate(recipe.nodes)} if recipe else {}
    counts: dict[str, int] = {}

    def visit(steps):
        for step in steps:
            if step.type == "parallel":
                for branch in step.paths:
                    visit(branch)
            else:
                counts[step.node_ref] = counts.get(step.node_ref, 0) + 1
                for branch in step.branches:
                    visit(branch.steps)

    if recipe:
        visit(recipe.control_flow)
    def safe_ref(ref):
        return bool(_TOKEN.fullmatch(ref) and not _PRIVATE_TOKEN.search(ref))
    focus["control_coverage"] = {
        "required_node_refs": sorted(ref for ref in nodes if safe_ref(ref)),
        "omitted_node_refs": sorted(ref for ref in nodes if not counts.get(ref) and safe_ref(ref)),
        "repeated_node_refs": sorted(ref for ref, count in counts.items() if count > 1 and safe_ref(ref)),
        "unknown_node_refs": sorted(ref for ref in counts if ref not in nodes and safe_ref(ref)),
    }
    parse_issues = [item for item in details.get("issues", []) if item.get("category") == "intent_parse"]
    structural = [item for item in details.get("issues", []) if item.get("category") == "recipe_lowering"
                  and str(item.get("code", "")).startswith("RECIPE_")]
    primary = [{key: deepcopy(item[key]) for key in (
        "code", "node_ref", "location", "validation_type", "schema_detail", "template_detail", "recipe_detail",
    ) if key in item} for item in [*parse_issues, *structural]]
    # Port compatibility and predicate-domain validity are different proofs.
    # A generic `any` input accepting an array does not make field selection legal.
    for item in [*local.get("input_shape_issues", []), *local.get("input_type_issues", [])]:
        primary.append({"code": item.get("code", "INPUT_TYPE_INCOMPATIBLE"), **{key: deepcopy(item[key]) for key in (
            "code", "node_ref", "node_index", "input_index", "source_ref", "source_port",
            "port", "target_port", "source_schema", "declared_schema", "target_schema",
            "source_assignable", "source_matches_declaration", "expected_ports", "unexpected_input_indices",
        ) if key in item}})
    for item in paths:
        if item.get("code") != "ROUTER_INPUT_DOMAIN_INVALID":
            continue
        ref = item.get("node_ref")
        if ref not in nodes:
            continue
        index, _node = nodes[ref]
        contract = item.get("input_contract", {})
        source_ref, source_port = contract.get("source_ref"), contract.get("source_port")
        entry = {
            "code": item["code"], "node_ref": ref, "error_code": item.get("error_code"),
            "locations": [["nodes", index, "config"], ["nodes", index, "inputs"]],
            "input_contract": {key: deepcopy(contract[key]) for key in (
                "source_status", "source_ref", "source_port", "source_type", "source_nullable",
                "source_union_types", "has_field", "field_declared", "field_required", "field_type", "field_nullable",
            ) if key in contract},
            "related_inputs": [],
        }
        if source_ref in nodes:
            entry["source_location"] = ["nodes", nodes[source_ref][0], "config"]
            related = set()
            for origin in input_origins.values():
                if (origin["source_ref"], origin["source_port"]) == (source_ref, source_port):
                    for location in origin["locations"]:
                        related.add((origin["node_ref"], tuple(location)))
            entry["related_inputs"] = [{"node_ref": consumer_ref, "location": list(location)}
                                       for consumer_ref, location in sorted(related)[:MAX_DIAGNOSTIC_ISSUES]]
            entry["omitted_related_input_count"] = max(0, len(related) - MAX_DIAGNOSTIC_ISSUES)
        primary.append(entry)
    # Secondary reachability/terminal errors cannot displace an invalid predicate.
    if not primary and not dependencies:
        primary = [{key: deepcopy(item[key]) for key in ("code", "node_ref", "location") if key in item}
                   for item in details.get("issues", []) if item.get("category") == "control_flow"]
    known = local.get("known_source_dependencies", {})
    partial = [{**deepcopy(item), "proof_scope": "known_sources_only"} for item in known.get("issues", [])]
    occurrences = local.get("occurrence_dependencies", {})
    if occurrences:
        focus["occurrence_analysis"] = {key: deepcopy(value) for key, value in occurrences.items() if key != "issues"}
    occurrence_issues = [{**deepcopy(item), "proof_scope": "recipe_occurrences"} for item in occurrences.get("issues", [])]
    entries = [*primary, *focus["issues"], *partial, *occurrence_issues]
    total = len(primary) + focus["issue_count"] + (known.get("issue_count") or 0) + (occurrences.get("issue_count") or 0)
    focus.update(issues=_safe_detail(entries[:MAX_DIAGNOSTIC_ISSUES]), issue_count=total,
                 omitted_issue_count=max(0, total - MAX_DIAGNOSTIC_ISSUES))
    templates = any(str(item.get("code", "")).startswith("RECIPE_TEMPLATE_") for item in primary)
    focus["primary_layer"] = "protocol_or_schema" if parse_issues else "template_sources" if templates else "structure" if structural else "input_or_path"
    data_proven = any(item["id"] == "data_availability" and item["status"] == "passed"
                      for item in details.get("control_proof_checks", []))
    focus["branch_repair_required"] = True if any(item.get("code") == "DATA_PATH_NOT_GUARANTEED" for item in entries) else False if data_proven else None
    focus["obligation"] = "按实际失败项修复，blocked 和 null 表示未完成证明，不代表没有问题。联动核对控制位置、消费输入及最终来源；保持目标、任务、业务条件和资源授权。"
    if parse_issues:
        focus["obligation"] = "先按 location 与 schema_detail 修复协议和字段结构。类型、授权与路径检查尚未执行，不能据此改写业务条件、增加分支或宣称通过；不得把所有 null 改成空数组或猜测输入来源。"
    if templates:
        focus["obligation"] += "先按 location 与字段内从 0 开始的 reference_index 定位占位符，再核对可信 source_ref 与 available_output_ports；config 不是输出，空输出列表不能提供数据。不得猜测替代来源或以空值绕过。"
    if focus["branch_repair_required"]:
        focus["obligation"] += "依据已列出的消费位置和路径反例处理分支独有值；若确需不同分支结论，可保留原 Agent 并克隆其他分支 Agent，明确各自输入与互斥最终来源。known_sources_only 仅证明合法引用子集，不代表完整图通过。"
    if occurrences:
        focus["obligation"] += "recipe_occurrences 仅检查原始控制位置中的已解析依赖，不生成替代图；not_disproved 不代表可外提。分别处理重复节点的依赖，不能将所有重复节点一并提到公共位置。修复后仍须完整类型、路径与发布门禁。"
    return focus


def recipe_repair_checklist() -> list[str]:
    return [
        "先按 repair_focus.obligation 处理当前已证实的失败，不用尚未执行的检查推断改图方向。",
        "来源返回类型改变时，按 source_location 和 related_inputs 联动核对所有消费者：空值/空数组判断、字段访问及可信记录输入。不得自动取首项或改变业务选取语义。",
        "按最终 config 核对 inputs；保留任务、业务条件、资源授权及影响上限。terminate_error 是终点，其后不能添加 Agent 或为输出文本把错误路径改成成功。",
        "修复后重新执行全部门禁；仅改标题或结构不证明原错误已消除，不能跳过原错误或新增错误。",
    ]


def local_recipe_facts(nodes: list[GraphIntentNodeV3]) -> dict[str, Any]:
    """Caller must first resolve all configs, authorization and resource facts.

    Reuse the analyzer's complete bounded input partitions, but never conclude
    that a nullable field is unsafe without the missing path guard proof.
    """
    by_ref = {node.ref: node for node in nodes}
    outputs = {(node.ref, output.port): output.value_schema for node in nodes for output in node.outputs}
    outputs.update({("input", port.name): port.value_schema
                    for port in workflow_node_contract_registry.require("input").ports if port.direction == "output"})
    types, shapes, routers = [], [], []
    for index, node in enumerate(nodes):
        adapter = get_planner_node_adapter(node.kind)
        parsed = adapter.config_model.model_validate(node.config)
        shape = adapter.input_binding_state(node, parsed)
        if not shape["valid"]:
            shapes.append({"node_index": index, "node_ref": node.ref, "code": "INPUT_BINDING_SHAPE_INVALID",
                           "expected_ports": [{key: item[key] for key in ("port", "minimum", "maximum", "actual")}
                                              for item in shape["ports"]],
                           "unexpected_input_indices": shape["unexpected_input_indices"]})
        ports = adapter.intent_port_contracts("input")
        for input_index, binding in enumerate(node.inputs):
            source = outputs.get((binding.source_ref, binding.source_port))
            port = next((port for port in ports if port.name == binding.port), None)
            if port is None:
                port = next((port for port in ports if binding.port.startswith(port.name + "_")), None)
            if source is None or port is None:
                continue
            producer = by_ref.get(binding.source_ref)
            producer_adapter = get_planner_node_adapter(producer.kind) if producer else None
            issue = graph_input_type_issue(index, input_index, node.ref, binding, source, port.value_schema,
                source_is_resource=producer_adapter is not None and producer_adapter.resource_kind is not None)
            if issue:
                types.append(issue.repair_detail())
        if node.kind not in {"condition", "multi_route"}:
            continue
        detail = {"node_index": index, "node_ref": node.ref, "kind": node.kind}
        if len(routers) >= 8:
            routers.append({**detail, "status": "blocked", "blocked_by": "router_limit"})
            continue
        try:
            assignments = _joint_assignments([node], by_ref, outputs)
        except ControlFlowAnalysisError:
            routers.append({**detail, "status": "blocked", "blocked_by": "input_partition"})
            continue
        outcomes = {item[node.ref] for item in assignments}
        valid = outcomes.intersection(semantic_outcomes(node))
        errors = outcomes - valid
        missing = [outcome for outcome in semantic_outcomes(node) if outcome not in valid]
        # Errors for only some legal values may be excluded by upstream guards.
        status = "failed" if not valid or missing else "blocked" if errors else "passed"
        routers.append({**detail, "status": status,
            "code": "ROUTER_INPUT_DOMAIN_INVALID" if not valid else "ROUTER_OUTCOMES_UNREACHABLE" if missing else None,
            "possible_outcomes": sorted(valid), "unreachable_outcomes": missing,
            "has_error_values": bool(errors), "blocked_by": "path_guard" if errors and valid else None,
            "input_partition_complete": True})
    return _safe_detail({
        "status": "completed", "node_count": len(nodes),
        "input_types_status": "failed" if types else "passed",
        "input_type_issues": types[:MAX_DIAGNOSTIC_ISSUES], "input_type_issue_count": len(types),
        "omitted_input_type_issue_count": max(0, len(types) - MAX_DIAGNOSTIC_ISSUES),
        "input_shape_issues": shapes, "router_facts": routers,
        "path_proof_status": "not_executed", "path_proof_required": True,
    })
