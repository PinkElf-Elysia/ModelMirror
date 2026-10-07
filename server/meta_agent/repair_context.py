"""Read-only model projection; validators and persisted diagnostics stay intact."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .node_adapters import get_planner_node_adapter
from .schemas import GraphIntentNodeV3

def _dependency_review(payload: dict[str, Any]) -> dict[str, Any]:
    graph, contract = payload["base_graph_intent"], payload["repair_contract"]
    issues = payload["validation_frontier"]["issues"]
    nodes = {node["ref"]: node for node in graph["nodes"]}
    affected = {issue.get("node_ref") for issue in issues}
    for key in ("input_contract_issues", "data_contract_issues", "control_contract_issues"):
        affected.update(issue.get("node_ref") for issue in contract[key])
    dependency_issues = contract.get("control_dependency_issues", [])
    affected.update(issue["node_ref"] for issue in dependency_issues)
    affected.update(issue["node_ref"] for issue in contract.get("source_contract_issues", []))
    path_issues = contract.get("control_path_issues", [])
    for issue in path_issues:
        affected.add(issue.get("node_ref"))
        affected.update(issue.get("node_refs", []))
        counterexample = issue["counterexample"]
        affected.update(counterexample["choices"])
        affected.update(counterexample["reached_roots"])
    affected &= nodes.keys()
    # Show direct producers as context, without selecting a new source or inferring a path.
    review_refs = affected | {
        binding["source_ref"] for ref in affected for binding in nodes[ref]["inputs"]
        if binding["source_ref"] in nodes
    }
    requirements = {
        item["node_ref"]: item["required_input_ports"]
        for item in contract["resolved_resource_inputs"] if "required_input_ports" in item
    }
    work = []
    for node in graph["nodes"]:
        ref = node["ref"]
        if ref not in review_refs:
            continue
        item = {
            "node_ref": ref,
            "kind": node["kind"],
            "issue_indices": [i for i, issue in enumerate(issues) if issue.get("node_ref") == ref],
            "bound_inputs_now": [
                {"input_index": i, **{key: binding[key] for key in ("port", "source_ref", "source_port", "variable")}}
                for i, binding in enumerate(node["inputs"])
            ],
            "control_predecessors_now": [
                {key: edge[key] for key in ("source_ref", "outcome_ref")}
                for edge in graph["control_edges"] if edge["target_ref"] == ref
            ],
        }
        adapter = get_planner_node_adapter(node["kind"])
        # Resource-derived facts require the existing authorized resolution. Pure
        # port counts need only the public contract and this node's valid config.
        if (adapter is not None and node["kind"] in payload["authorized_scope"]["allowed_node_kinds"]
                and (adapter.resource_kind is None or ref in requirements)):
            try:
                parsed = adapter.config_model.model_validate(node["config"])
                state = adapter.input_binding_state(GraphIntentNodeV3.model_validate(node), parsed)
            except ValueError:
                item["input_contract_status"] = "config_invalid"
            else:
                item.update(
                    input_contract_basis="current_config",
                    required_inputs_now=[port["port"] for port in state["ports"] if port["minimum"]],
                    input_shape_valid_now=state["valid"],
                )
                if not state["valid"]:
                    item.update(input_requirements_now=state["ports"],
                                unexpected_input_indices=state["unexpected_input_indices"])
        work.append(item)
    return {
        "nodes": work,
        "control_dependency_issues": dependency_issues,
        "control_path_issues": path_issues,
        "omitted_control_dependency_issue_count": contract.get("omitted_control_dependency_issue_count", 0),
        "omitted_control_path_issue_count": contract.get("omitted_control_path_issue_count", 0),
        "global_issue_indices": [i for i, issue in enumerate(issues) if issue.get("node_ref") not in review_refs],
        "rules": [
            "只读事实，非修复方案。配置见 base_graph_intent，契约见 graph_intent_contract；issue_indices 对应 validation_frontier.issues，处理全部独立错误。",
            "先审查完整路径的类型保护、成功/错误终点与必需值，再确定最终 config 和原子 Patch；不得改动无关业务条件、值、来源或授权。",
            "input_requirements_now 给出数量及零基 input_indices；required_inputs_now 仅列必需端口。config 不改连线，需显式 connect/disconnect；重复原配置不是修复。",
            "DATA_PATH_NOT_GUARANTEED 表示消费者到达但值缺失；DATA_NOT_CONTROL_ANCESTOR 表示来源不保证先执行。数据边不调度节点，普通节点任一控制入口到达即可执行，增加边不是 AND 保护。",
            "结合 control_path_issues、reached_roots 与 control_predecessors_now 核对旁路，必要时显式断边。查询 error 不等于 success 返回 null；字段比较须有同来源的非空保护。",
            "公共节点只能消费每条到达路径均有的值，无写入分支不能消费写入回执。禁止串行化、关闭 error、伪造回执或猜测接边来规避目标分支。",
            "整批校验通过才算修复；错误数量下降不等于安全，省略诊断不等于通过。",
        ],
    }


def project_patch_repair_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep full graph/authority/schema; remove only redundant audit telemetry."""
    result = deepcopy(payload)
    frontier = result["validation_frontier"]
    # Full audit hashes and all-edge telemetry remain in the stored report, not the repair prompt.
    for key in (
        "control_graph_checksum", "data_graph_checksum", "data_bindings", "data_binding_count",
        "omitted_binding_detail_count", "router_inputs", "input_contract_issues",
        "input_contract_issue_count", "control_contract_issues",
    ):
        frontier.pop(key, None)
    for issue in frontier["issues"]:
        issue.pop("fingerprint", None)
    review = _dependency_review(result)
    result["repair_contract"].pop("control_dependency_issues", None)
    result["repair_contract"].pop("omitted_control_dependency_issue_count", None)
    result["repair_contract"].pop("control_path_issues", None)
    result["repair_contract"].pop("omitted_control_path_issue_count", None)
    result["repair_contract"] = {"dependency_review": review, **result["repair_contract"]}
    return result
