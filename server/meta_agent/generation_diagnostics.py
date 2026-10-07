from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from .control_flow import CONTROL_PROOF_CHECKS, control_contract_issues, semantic_outcomes
from .node_adapters import PlannerResourceContractError, get_planner_node_adapter, workflow_node_contract_registry


class RecipeInputModeError(ValueError):
    def __init__(self):
        super().__init__("只有 workflow_agent 可用 inputs=null 从模板派生文本输入。")


class RecipeEffectiveContractError(ValueError):
    def __init__(self, constraint: str):
        self.constraint = constraint if constraint in {
            "required", "type", "const", "enum", "additionalProperties", "false_schema",
            "oneOf", "anyOf", "minItems", "maxItems", "minimum", "maximum",
            "minLength", "maxLength", "pattern",
        } else "schema"
        super().__init__("生成描述不符合本次授权的节点契约，请按 required_schema 修正该字段。")


_SCHEMA_MESSAGES = {
    "GENERATION_PROTOCOL_REQUIRED": "缺少 generation_protocol_version=1；新生成不能返回完整 GraphIntent 或原生工作流。",
    "GENERATION_PROTOCOL_INVALID": "generation_protocol_version 必须为整数 1，不能更换生成协议。",
    "RECIPE_INPUT_MODE_INVALID": "该节点只接受显式 inputs 数组；无数据绑定时使用 []，必需输入仍须完整声明。",
    "RECIPE_EFFECTIVE_CONTRACT_INVALID": "生成描述不符合本次授权的节点契约，请按 required_schema 修正该字段。",
}


def schema_issue_projection(error: dict[str, Any]) -> dict[str, Any]:
    """Disclose trusted error semantics, never validator context or input values."""
    location = list(error.get("loc") or ())
    code, detail = "SCHEMA_VALIDATION_FAILED", None
    cause = (error.get("ctx") or {}).get("error")
    if isinstance(cause, RecipeEffectiveContractError):
        code = "RECIPE_EFFECTIVE_CONTRACT_INVALID"
        detail = {"expected": "scoped_generation_contract", "constraint": cause.constraint}
    elif isinstance(cause, RecipeInputModeError):
        code = "RECIPE_INPUT_MODE_INVALID"
        location.append("inputs")
        detail = {"expected": "array", "actual": "null", "input_mode": "explicit"}
    elif location == ["generation_protocol_version"]:
        missing = error.get("type") == "missing"
        code = "GENERATION_PROTOCOL_REQUIRED" if missing else "GENERATION_PROTOCOL_INVALID"
        detail = {"expected": "protocol_v1", "actual": "missing" if missing else "invalid"}
    return {"code": code, "location": _safe_location(location), "schema_detail": detail,
            "message": _SCHEMA_MESSAGES.get(code)}


class RecipeControlFlowError(ValueError):
    MESSAGES = {
        "RECIPE_UNKNOWN_NODE": "控制流引用了未声明的节点。请核对 node_ref 与 nodes.ref，不得猜测节点。",
        "RECIPE_REPEATED_NODE": "控制流重复引用同一节点。本语法要求每个 ref 只出现一次；公共后续步骤应置于分支结构之后，且只能读取各路径保证存在的数据。重复引用本身不等于循环。",
        "RECIPE_OMITTED_NODE": "已声明的执行节点未出现在控制流中。请显式确定其控制位置，不得静默删除业务步骤。",
        "RECIPE_BRANCH_OUTCOMES_MISMATCH": "节点必须显式且仅声明契约要求的分支。请修正 branches，不得用 parallel 替代互斥出口。",
        "RECIPE_AFTER_TERMINAL": "终止路径后不能继续添加节点。",
    }

    def __init__(self, code: str, ref: str, location: list[str | int], *, first_location=None,
                 expected_outcomes=None, actual_outcomes=None):
        self.code, self.node_ref, self.location = code, ref, location
        self.first_location = first_location
        self.expected_outcomes = expected_outcomes
        self.actual_outcomes = actual_outcomes
        message = self.MESSAGES[code]
        if expected_outcomes is not None:
            expected = [item for item in expected_outcomes if item in _SEMANTIC_OUTCOMES][:9]
            actual = [item for item in (actual_outcomes or []) if item in _SEMANTIC_OUTCOMES][:9]
            message += f"期望：{', '.join(expected) or '无'}；实际：{', '.join(actual) or '无'}。"
        super().__init__(message)


class RecipeTemplateError(ValueError):
    MESSAGES = {
        "RECIPE_TEMPLATE_REFERENCE_INVALID": "Prompt 占位符只能使用 {{source_ref.source_port}}，不能使用原生变量或属性路径。",
        "RECIPE_TEMPLATE_SOURCE_UNKNOWN": "Prompt 引用的节点或输出端口不存在，请使用已解析的来源端口。",
        "RECIPE_TEMPLATE_INPUT_MISSING": "Prompt 引用了本节点未声明的输入，请显式配置来源，并继续核对输入类型与路径可达性。",
    }

    def __init__(self, code: str, ref: str, location: list[str | int], reference: str,
                 *, source: tuple[str, str] | None = None, reference_index: int | None = None,
                 known_source: tuple[str, str, tuple[str, ...]] | None = None, reason: str | None = None):
        self.code, self.node_ref, self.location = code, ref, location
        self.reference_checksum = diagnostic_checksum(reference)
        self.source = source
        self.reference_index = reference_index
        self.known_source = known_source
        self.reason = reason
        super().__init__(self.MESSAGES[code])


class RecipeEditTargetError(ValueError):
    MESSAGES = {
        "RECIPE_EDIT_UNKNOWN_NODE": "修改目标不在原节点或已执行的克隆操作中。请核对原节点 ref，不得猜测节点。",
        "RECIPE_EDIT_CLONED_NODE_IMMUTABLE": "不能用 update_node 修改同批克隆节点；请在对应 clone_agent 中一次写完整 task_input 和所需 role_prompt。",
        "RECIPE_EDIT_NODE_UNAUTHORIZED": "原节点已不在本次授权范围内，不能通过语义修复扩大权限。",
    }

    def __init__(self, code: str, operation_index: int, ref: str):
        self.code = code
        self.operation_index = operation_index
        self.node_ref_checksum = diagnostic_checksum(ref)
        super().__init__(self.MESSAGES[code])


DIAGNOSTICS_VERSION = 3
MAX_DIAGNOSTIC_ISSUES = 64
MAX_BINDING_DETAILS = 64
MAX_PATCH_CHANGE_BYTES = 65_536
_CATEGORIES = frozenset({
    "task_plan", "intent_parse", "patch_parse", "compatibility", "authorization",
    "node_config", "resource_contract", "type_ports", "control_flow",
    "resolve", "compile", "publish_preflight", "patch_normalization",
    "patch_apply", "output_normalization", "recipe_lowering", "repair_preparation",
})
_GRAPH_PHASES = ("intent_parse", "authorization", "resolve", "compile", "publish_preflight")
_PHASES_BY_STAGE = {
    "repair_preparation": ("repair_preparation",),
    "task_plan": ("task_plan",), "task_plan_v1": ("task_plan",),
    "capability_compile": _GRAPH_PHASES, "graph_intent_v3": _GRAPH_PHASES,
    "generation_recipe_v1": _GRAPH_PHASES,
    "recipe_edits_v1": ("patch_parse", "patch_apply", *_GRAPH_PHASES),
    "graph_patch_v1": ("patch_parse", "patch_normalization", "patch_apply", "output_normalization", *_GRAPH_PHASES),
}
# This is a disclosure allowlist, not a second validation schema.
_LOCATION_FIELDS = frozenset({
    "tasks", "task_id", "depends_on", "nodes", "kind", "ref", "config", "inputs",
    "outputs", "port", "value_schema", "type", "items", "properties", "required",
    "nullable", "any_of", "task_ids", "source_ref", "source_port", "target_ref",
    "target_port", "outcome_ref", "control_edges", "final_output", "sources",
    "selection_policy", "operations", "op", "resource_ref", "resource_id",
    "filter", "children", "field", "operator", "value_type", "value_source",
    "values", "limit", "return_mode", "max_affected_rows", "expected_schema",
    "ir_version", "version", "resources", "middleware", "prompt_profile_ids",
    "generation_protocol_version", "control_flow", "node_ref", "branches", "steps", "paths",
    "role_prompt", "task_input",
})
_KNOWN_ERRORS = {
    "RECIPE_REPAIR_UNCHANGED：语义修复没有改变原生成描述。": "RECIPE_REPAIR_UNCHANGED",
    "Control edge already exists.": "PATCH_CONTROL_EDGE_EXISTS",
    "Control edge does not exist.": "PATCH_CONTROL_EDGE_MISSING",
    "Data edge already exists.": "PATCH_DATA_EDGE_EXISTS",
    "Data edge does not exist.": "PATCH_DATA_EDGE_MISSING",
    "Resource binding already exists.": "PATCH_RESOURCE_BINDING_EXISTS",
    "Resource binding does not exist.": "PATCH_RESOURCE_BINDING_MISSING",
    "Middleware binding already exists.": "PATCH_MIDDLEWARE_BINDING_EXISTS",
    "Middleware binding does not exist.": "PATCH_MIDDLEWARE_BINDING_MISSING",
}
_CODES = frozenset({
    "CONTRACT_CHECK_FAILED", "SCHEMA_VALIDATION_FAILED", "JSON_PARSE_FAILED",
    "CONTROL_UNKNOWN_NODE", "CONTROL_SELF_EDGE", "CONTROL_DUPLICATE_EDGE",
    "CONTROL_CYCLE", "DATA_UNKNOWN_VARIABLE", "DATA_UNREACHABLE", "DATA_TYPE_MISMATCH",
    "DATA_UNKNOWN_SOURCE_REF", "DATA_UNKNOWN_SOURCE_PORT", "DATA_AMBIGUOUS_SOURCE_PORT",
    "DATA_SOURCE_VARIABLE_MISMATCH", "WRITE_VALUES_PRODUCER_INVALID",
    *_KNOWN_ERRORS.values(),
    *_SCHEMA_MESSAGES,
    *PlannerResourceContractError.MESSAGES,
    *RecipeControlFlowError.MESSAGES,
    *RecipeTemplateError.MESSAGES,
    *RecipeEditTargetError.MESSAGES,
    "RECIPE_REPAIR_UNCHANGED", "RECIPE_CONTROL_FLOW_UNCHANGED", "REPAIR_PREPARATION_FAILED",
    "ROUTER_INPUT_DOMAIN_INVALID", "CONTROL_TERMINAL_COUNT", "DATA_PATH_NOT_GUARANTEED",
})
_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_PRIVATE_TOKEN = re.compile(r"(?i)(?:^sk[-_]|^gh[pousr]_|secret|password|credential|token|api[-_]?key)")
_SEMANTIC_OUTCOMES = frozenset({
    "success", "error", "matched", "unmatched", "default",
    *(f"case_{index}" for index in range(1, 9)),
})


def diagnostic_checksum(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _safe_location(location: Any) -> list[str | int]:
    return [
        part if isinstance(part, int) and 0 <= part <= 100_000
        else part if isinstance(part, str) and part in _LOCATION_FIELDS
        else "<field>"
        for part in list(location or ())[:12]
    ]


def _patch_node_state(node: Any) -> dict[str, Any]:
    adapter = get_planner_node_adapter(node.kind)
    known_ports = {port.name for port in adapter.intent_port_contracts("input")} if adapter else set()

    def port_summary(port: str) -> dict[str, str]:
        if port in known_ports or port in {"records", "values"}:
            return {"port": port}
        return {"port_checksum": diagnostic_checksum(port)}

    result: dict[str, Any] = {
        "config_checksum": diagnostic_checksum(node.config),
        "actual_input_count": len(node.inputs),
        "actual_ports": [port_summary(item.port) for item in node.inputs[:MAX_BINDING_DETAILS]],
        "inputs_checksum": diagnostic_checksum([item.model_dump(mode="json") for item in node.inputs]),
    }
    if adapter is None:
        return {**result, "config_status": "unsupported"}
    try:
        parsed = adapter.config_model.model_validate(node.config)
    except ValueError:
        return {**result, "config_status": "invalid"}
    inputs = adapter.input_binding_state(node, parsed)
    result.update(
        config_status="valid",
        effective_config_checksum=diagnostic_checksum(parsed.model_dump(mode="json")),
        expected_ports=[port_summary(item["port"]) for item in inputs["ports"]],
        input_shape_valid=inputs["valid"],
        input_port_counts=[{**port_summary(item["port"]),
                            **{key: item[key] for key in ("minimum", "maximum", "actual")}}
                           for item in inputs["ports"]],
    )
    predicate = getattr(parsed, "filter", None)
    result["filter_checksum"] = diagnostic_checksum(predicate.model_dump(mode="json") if predicate else None)
    result["filter_shape"] = []
    pending = [(predicate, 0)] if predicate is not None else []
    while pending and len(result["filter_shape"]) < MAX_BINDING_DETAILS:
        item, depth = pending.pop()
        shape = {"kind": item.kind, "depth": depth}
        if item.kind == "group":
            shape["logic"] = item.logic
            pending.extend((child, depth + 1) for child in reversed(item.items))
        else:
            shape.update(operator=item.operator, value_source=item.value_source,
                         ref_checksum=diagnostic_checksum(item.ref), field_checksum=diagnostic_checksum(item.field))
        result["filter_shape"].append(shape)
    return result


@dataclass
class GraphPatchProgress:
    """Passive progress; never changes Patch acceptance or exception handling."""

    phase: str = "not_started"
    operation_index: int | None = None
    node_changes: list[dict[str, Any]] = field(default_factory=list)
    omitted_node_change_count: int = 0
    unavailable_node_change_count: int = 0
    _node_change_bytes: int = 2

    def record_node_change(self, op: str, before: Any, after: Any) -> None:
        try:
            entry = {"operation_index": self.operation_index, "op": op,
                     "node_ref_checksum": diagnostic_checksum(after.ref),
                     "before": _patch_node_state(before), "after": _patch_node_state(after)}
            if _TOKEN.fullmatch(after.ref) and not _PRIVATE_TOKEN.search(after.ref):
                entry["node_ref"] = after.ref
            size = len(json.dumps(entry, ensure_ascii=True, separators=(",", ":"))) + bool(self.node_changes)
            if len(self.node_changes) >= MAX_DIAGNOSTIC_ISSUES or self._node_change_bytes + size > MAX_PATCH_CHANGE_BYTES:
                self.omitted_node_change_count += 1
                return
            self.node_changes.append(entry)
            self._node_change_bytes += size
        except Exception:
            # Evidence failure must not change the applied operation or its error.
            self.unavailable_node_change_count += 1


class GenerationDiagnostics:
    """Bounded observer; fingerprints correlate failures without retaining text."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.phase = "intent_parse"
        self.failed_phase: str | None = None
        self.checks: list[str] = []
        self.issues: list[dict[str, Any]] = []
        self.issue_count = 0
        self._seen: set[tuple[str, str]] = set()
        self.category_counts: Counter[str] = Counter()
        self.recompile_executed = False
        self.patch: dict[str, Any] | None = None
        self._node_refs: list[str | None] = []
        self._control_edges: list[dict[str, str]] = []
        self._control_contract_issues: list[dict[str, Any]] | None = None
        self._control_graph_checksum: str | None = None
        self._binding_summary: dict[str, Any] | None = None
        self._failed_phases: set[str] = set()
        self._router_inputs: list[dict[str, Any]] = []
        self.parsed_recipe = None
        self.recipe_draft = None
        self._control_proof_checks: list[dict[str, Any]] = []
        self.recipe_preflight: dict[str, Any] | None = None
        self.recipe_repair_progress: dict[str, Any] | None = None

    def compare_recipe_repair(self, previous: GenerationDiagnostics) -> bool:
        """Observe the failed structure, not changes to titles or unrelated config."""
        if self.parsed_recipe is None or previous.parsed_recipe is None:
            return False
        before = diagnostic_checksum([item.model_dump(mode="json") for item in previous.parsed_recipe.control_flow])
        after = diagnostic_checksum([item.model_dump(mode="json") for item in self.parsed_recipe.control_flow])
        earlier = {(item["code"], item["fingerprint"]) for item in previous.issues
                   if item["code"] in RecipeControlFlowError.MESSAGES}
        persisted = any((item["code"], item["fingerprint"]) in earlier for item in self.issues)
        unchanged = persisted and before == after
        def identities(observer):
            result = set()
            for item in observer.issues:
                detail = item.get("template_detail")
                # Reordering placeholders is not a repair of the same bad source.
                identity = diagnostic_checksum([item["code"], item.get("node_ref"),
                    (item.get("location") or [None])[-1], detail.get("reference_checksum"),
                    detail.get("source_ref"), detail.get("source_port")]) if detail else item["fingerprint"]
                result.add((item["code"], identity))
            return result
        earlier_issues, current_issues = identities(previous), identities(self)
        persisting = len(earlier_issues & current_issues)
        # A rejected edit never reached the checks that found the old failures.
        # Missing observations are unknown, not evidence that those failures vanished.
        not_rechecked = sorted(previous._failed_phases - set(self.checks))
        self.recipe_repair_progress = {
            "control_flow_before_checksum": before, "control_flow_after_checksum": after,
            "blocking_structure_unchanged": unchanged if not not_rechecked else None,
            "persisting_issue_count": persisting if not not_rechecked else None,
            "new_issue_count": len(current_issues - earlier_issues),
            "no_longer_observed_issue_count": len(earlier_issues - current_issues) if not not_rechecked else None,
            "not_rechecked_phases": not_rechecked,
            "comparison_truncated": previous.issue_count > len(previous.issues) or self.issue_count > len(self.issues),
            "assessment": "not_rechecked" if not_rechecked else "blocking_issues_persist" if persisting else "requires_full_validation",
            "local_type_issue_count_before": (previous.recipe_preflight or {}).get("input_type_issue_count"),
            "local_type_issue_count_after": (self.recipe_preflight or {}).get("input_type_issue_count"),
            "acceptance": "requires_full_validation",
        }
        return unchanged and not not_rechecked

    def bind_control_proof(self, checks: list[dict[str, Any]], issues: list[dict[str, Any]] = ()) -> None:
        self._control_proof_checks = [
            {"id": item["id"], "status": item["status"],
             "blocked_by": item.get("blocked_by") if item.get("blocked_by") in {*CONTROL_PROOF_CHECKS, "not_executed"} else None}
            for item in checks if item.get("id") in CONTROL_PROOF_CHECKS
            and item.get("status") in {"passed", "failed", "blocked"}
        ]
        for item in issues:
            ref = item.get("node_ref")
            if ref not in self._node_refs or item.get("code") not in {
                "ROUTER_INPUT_DOMAIN_INVALID", "DATA_PATH_NOT_GUARANTEED",
            }:
                continue
            self._add("control_flow", item["code"], diagnostic_checksum(item),
                      node_index=self._node_refs.index(ref))

    def bind_authoritative_types(self, graph: Any, outputs: dict) -> None:
        """Correlate router contracts without retaining field names or values."""
        self._router_inputs = []
        for index, node in enumerate(graph.nodes):
            if node.kind not in {"condition", "multi_route"}:
                continue
            bindings = [item for item in node.inputs if item.port == "value"]
            source = outputs.get((bindings[0].source_ref, bindings[0].source_port)) if len(bindings) == 1 else None
            field = str(node.config.get("field") or "").strip()
            rule = node.config
            item = {
                "node_index": index, "kind": node.kind,
                "source_status": "resolved" if source is not None else "blocked",
                "field_checksum": diagnostic_checksum(field), "has_field": bool(field),
                "config_checksum": diagnostic_checksum(node.config),
            }
            if index < len(self._node_refs) and self._node_refs[index] is not None:
                item["node_ref"] = self._node_refs[index]
            if rule.get("operator") in {"equals", "not_equals", "gt", "gte", "lt", "lte", "contains", "in", "is_null"}:
                item["operator"] = rule["operator"]
            if rule.get("value_type") in {"text", "number", "boolean", "null", "json"}:
                item["value_type"] = rule["value_type"]
            if source is not None:
                item.update(source_schema_checksum=diagnostic_checksum(source.model_dump(mode="json")),
                            source_type=source.type, source_nullable=source.nullable)
                if field:
                    prop = source.properties.get(field)
                    item.update(field_declared=prop is not None, field_required=field in source.required)
                    if prop is not None:
                        item.update(field_type=prop.type, field_nullable=prop.nullable)
            self._router_inputs.append(item)
            if len(self._router_inputs) == MAX_BINDING_DETAILS:
                break

    def bind_graph(self, graph: Any) -> None:
        self._node_refs = [
            node.ref if _TOKEN.fullmatch(node.ref) and not _PRIVATE_TOKEN.search(node.ref)
            else None for node in graph.nodes
        ]
        known = set(self._node_refs) - {None}
        self._control_edges = [
            {name: value for name, value in (
                ("source_ref", edge.source_ref), ("target_ref", edge.target_ref),
            ) if value in known}
            for edge in graph.control_edges
        ]
        self._control_contract_issues = []
        for item in control_contract_issues(graph)[:MAX_DIAGNOSTIC_ISSUES]:
            summary = {key: deepcopy(value) for key, value in item.items() if key != "node_ref"}
            for key in ("expected_outcomes", "missing_outcomes", "duplicate_outcomes"):
                summary[key] = [outcome for outcome in item[key] if outcome in _SEMANTIC_OUTCOMES]
            summary["actual_counts"] = {
                outcome: count for outcome, count in item["actual_counts"].items()
                if outcome in _SEMANTIC_OUTCOMES
            }
            omitted = len(item["expected_outcomes"]) - len(summary["expected_outcomes"])
            if omitted:
                summary["omitted_outcome_count"] = omitted
            if item["node_ref"] in known:
                summary["node_ref"] = item["node_ref"]
            self._control_contract_issues.append(summary)
        self._control_graph_checksum = diagnostic_checksum({
            "nodes": sorted((node.ref, node.kind, semantic_outcomes(node)) for node in graph.nodes),
            "edges": sorted((edge.source_ref, edge.outcome_ref, edge.target_ref) for edge in graph.control_edges),
            "final_sources": sorted((source.node_ref, source.port) for source in graph.final_output.sources),
        })
        self._bind_data(graph)

    def _bind_data(self, graph: Any) -> None:
        contracts: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        contract_count = 0
        binding_count = 0
        by_ref: dict[str, list[tuple[int, Any]]] = {}
        for index, node in enumerate(graph.nodes):
            by_ref.setdefault(node.ref, []).append((index, node))

        def add(target: list[dict[str, Any]], item: dict[str, Any]) -> None:
            if len(contracts) + len(bindings) < MAX_BINDING_DETAILS:
                target.append(item)

        def port_summary(port: str, known_ports: set[str]) -> dict[str, str]:
            # Dynamic predicate refs and all unknown names remain hash-only.
            known = port if port in known_ports else "predicate" if port.startswith("predicate_") else None
            return {"port_checksum": diagnostic_checksum(port), **({"port": known} if known else {})}

        for index, node in enumerate(graph.nodes):
            adapter = get_planner_node_adapter(node.kind)
            if adapter is None:
                continue
            try:
                parsed = adapter.config_model.model_validate(node.config)
            except ValueError:
                contract_count += 1
                add(contracts, {"node_index": index, "code": "INPUT_CONTRACT_CONFIG_INVALID"})
                continue
            inputs = adapter.input_binding_state(node, parsed)
            known_ports = {port.name for port in adapter.intent_port_contracts("input")}
            for item in inputs["ports"]:
                if item["actual"] < item["minimum"] or (
                    item["maximum"] is not None and item["actual"] > item["maximum"]
                ):
                    contract_count += 1
                    add(contracts, {
                        "node_index": index, "code": "INPUT_PORT_COUNT_MISMATCH",
                        **port_summary(item["port"], known_ports),
                        **({"expected_count": item["minimum"]} if item["minimum"] == item["maximum"] else {
                            "expected_minimum": item["minimum"], "expected_maximum": item["maximum"],
                        }),
                        "actual_count": item["actual"],
                    })
            for input_index in inputs["unexpected_input_indices"]:
                contract_count += 1
                add(contracts, {
                    "node_index": index, "input_index": input_index,
                    "code": "INPUT_PORT_UNEXPECTED",
                    **port_summary(node.inputs[input_index].port, known_ports),
                })

        for index, node in enumerate(graph.nodes):
            for input_index, item in enumerate(node.inputs):
                binding_count += 1
                sources = by_ref.get(item.source_ref, [])
                source_index, source = sources[0] if len(sources) == 1 else (None, None)
                outputs = [output for output in source.outputs if output.port == item.source_port] if source else []
                detail: dict[str, Any] = {
                    "node_index": index, "input_index": input_index,
                    "source_node_index": source_index,
                    "source_node_count": len(sources), "source_output_count": len(outputs),
                    "compiler_managed_source": item.source_ref == "input",
                    "variable_matches_source": outputs[0].variable == item.variable if len(outputs) == 1 else None,
                    "declared_input_type": item.value_schema.type,
                    "declared_input_schema_checksum": diagnostic_checksum(item.value_schema.model_dump(mode="json")),
                }
                contract_kind = source.kind if source else "input" if item.source_ref == "input" else None
                source_adapter = get_planner_node_adapter(contract_kind) if contract_kind else None
                source_ports = source_adapter.intent_port_contracts("output") if source_adapter else workflow_node_contract_registry.require("input").ports if contract_kind == "input" else ()
                if item.source_port in {port.name for port in source_ports if port.direction == "output"}:
                    detail["source_port"] = item.source_port
                if len(outputs) == 1:
                    detail["declared_source_type"] = outputs[0].value_schema.type
                    detail["declared_source_schema_checksum"] = diagnostic_checksum(outputs[0].value_schema.model_dump(mode="json"))
                add(bindings, detail)
        self._binding_summary = {
            "input_contract_issues": contracts, "input_contract_issue_count": contract_count,
            "data_bindings": bindings, "data_binding_count": binding_count,
            "omitted_binding_detail_count": contract_count + binding_count - len(contracts) - len(bindings),
            "data_graph_checksum": diagnostic_checksum(sorted((
                node.ref, node.kind,
                sorted((item.port, item.variable, item.source_ref, item.source_port, diagnostic_checksum(item.value_schema.model_dump(mode="json"))) for item in node.inputs),
                sorted((item.port, item.variable, diagnostic_checksum(item.value_schema.model_dump(mode="json"))) for item in node.outputs),
            ) for node in graph.nodes)),
        }

    def enter(self, phase: str) -> None:
        if phase not in _CATEGORIES:
            raise ValueError("未知的诊断阶段。")
        self.phase = phase
        if phase not in self.checks:
            self.checks.append(phase)

    def messages(
        self, messages: list[str], *, category: str | None = None,
        node_index: int | None = None,
        code: str = "CONTRACT_CHECK_FAILED",
        location: list[str | int] | None = None,
    ) -> None:
        selected = category or self.phase
        for message in messages:
            self._add(
                selected, code, diagnostic_checksum(message),
                node_index=node_index,
                location=_safe_location(location) if location is not None else None,
            )

    def _add(
        self, category: str, code: str, fingerprint: str, *,
        node_index: int | None = None, location: list[str | int] | None = None,
        validation_type: str | None = None,
        resource_detail: dict[str, Any] | None = None,
        schema_detail: dict[str, Any] | None = None,
    ) -> None:
        if category not in _CATEGORIES:
            category = self.phase
        identity = (category, fingerprint)
        if identity in self._seen:
            return
        self._seen.add(identity)
        self.issue_count += 1
        self.category_counts[category] += 1
        self.failed_phase = self.failed_phase or self.phase
        self._failed_phases.add(self.phase)
        if len(self.issues) >= MAX_DIAGNOSTIC_ISSUES:
            return
        item: dict[str, Any] = {
            "category": category,
            "code": code if code in _CODES else "CONTRACT_CHECK_FAILED",
            "fingerprint": fingerprint,
        }
        if node_index is not None:
            item["node_index"] = node_index
        if location is not None:
            item["location"] = location
            if (
                len(location) >= 2 and location[0] == "nodes"
                and isinstance(location[1], int)
            ):
                node_index = location[1]
            if (
                len(location) >= 2 and location[0] == "control_edges"
                and isinstance(location[1], int)
                and 0 <= location[1] < len(self._control_edges)
            ):
                item.update(self._control_edges[location[1]])
        if node_index is not None and 0 <= node_index < len(self._node_refs):
            ref = self._node_refs[node_index]
            if ref is not None:
                item["node_ref"] = ref
        if validation_type is not None:
            item["validation_type"] = validation_type
        if resource_detail is not None:
            item["resource_detail"] = deepcopy(resource_detail)
        if schema_detail is not None:
            item["schema_detail"] = deepcopy(schema_detail)
        self.issues.append(item)

    def exception(
        self, exc: Exception, *, category: str | None = None,
        node_index: int | None = None,
    ) -> None:
        selected = category or self.phase
        if isinstance(exc, RecipeEditTargetError):
            detail = {"operation_index": exc.operation_index, "op": "update_node",
                      "node_ref_checksum": exc.node_ref_checksum}
            previous_count = len(self.issues)
            self._add(selected, exc.code, diagnostic_checksum([exc.code, detail]),
                      location=["operations", exc.operation_index, "node_ref"])
            if len(self.issues) != previous_count:
                self.issues[-1]["edit_detail"] = detail
            return
        if isinstance(exc, RecipeTemplateError):
            location = _safe_location(exc.location)
            detail = {"reference_checksum": exc.reference_checksum}
            if exc.source and all(_TOKEN.fullmatch(value) and not _PRIVATE_TOKEN.search(value) for value in exc.source):
                detail = dict(zip(("source_ref", "source_port"), exc.source))
            if exc.reference_index is not None:
                detail["reference_index"] = exc.reference_index
            if exc.reason in {"invalid_reference_syntax", "unknown_source", "unknown_output_port", "source_has_no_data_outputs"}:
                detail["reason"] = exc.reason
            if exc.known_source:
                ref, kind, ports = exc.known_source
                adapter = get_planner_node_adapter(kind)
                contract = workflow_node_contract_registry.get(kind)
                allowed = {port.name for port in adapter.intent_port_contracts("output")} if adapter else {
                    port.name for port in contract.ports if port.direction == "output"
                } if contract else set()
                if contract and _TOKEN.fullmatch(ref) and not _PRIVATE_TOKEN.search(ref):
                    detail.update(source_ref=ref, source_kind=kind,
                                  available_output_ports=sorted(set(ports) & allowed))
            previous_count = len(self.issues)
            self._add(selected, exc.code, diagnostic_checksum([exc.code, exc.node_ref, location, detail]),
                      location=location)
            if len(self.issues) != previous_count:
                issue = self.issues[-1]
                if _TOKEN.fullmatch(exc.node_ref) and not _PRIVATE_TOKEN.search(exc.node_ref):
                    issue["node_ref"] = exc.node_ref
                issue["template_detail"] = detail
            return
        if isinstance(exc, RecipeControlFlowError):
            detail = {"location": _safe_location(exc.location)}
            if exc.first_location is not None:
                detail["first_location"] = _safe_location(exc.first_location)
            if exc.expected_outcomes is not None:
                expected = [item for item in exc.expected_outcomes if item in _SEMANTIC_OUTCOMES][:9]
                counts = Counter(item for item in (exc.actual_outcomes or []) if item in _SEMANTIC_OUTCOMES)
                detail.update(expected_outcomes=expected, actual_counts=dict(sorted(counts.items())),
                    missing_outcomes=[item for item in expected if not counts[item]],
                    duplicate_outcomes=sorted(item for item, count in counts.items() if count > 1),
                    unexpected_outcomes=sorted(set(counts) - set(expected)))
            previous_count = len(self.issues)
            self._add(selected, exc.code, diagnostic_checksum([exc.code, exc.node_ref, detail]),
                      location=detail["location"])
            if len(self.issues) == previous_count:
                return
            issue = self.issues[-1]
            if _TOKEN.fullmatch(exc.node_ref) and not _PRIVATE_TOKEN.search(exc.node_ref):
                issue["node_ref"] = exc.node_ref
            issue["recipe_detail"] = detail
            return
        if isinstance(exc, PlannerResourceContractError):
            self._add(
                selected, exc.code, diagnostic_checksum([node_index, exc.code, exc.detail]),
                node_index=node_index, resource_detail=exc.detail,
            )
            return
        if isinstance(exc, ValidationError):
            for error in exc.errors(include_input=False, include_url=False):
                kind = str(error.get("type") or "")
                projection = schema_issue_projection(error)
                # Custom validator codes are not a trusted disclosure surface.
                safe_kind = kind if kind in {
                    "missing", "extra_forbidden", "literal_error", "enum", "value_error",
                    "string_type", "string_pattern_mismatch", "string_too_long",
                    "int_type", "int_parsing", "float_type", "bool_type", "list_type",
                    "dict_type", "too_short", "too_long", "union_tag_invalid",
                    "union_tag_not_found", "greater_than_equal", "less_than_equal",
                } else "validation_error"
                self._add(
                    selected, projection["code"],
                    diagnostic_checksum([
                        node_index, kind, list(error.get("loc") or ()),
                        str(error.get("msg") or ""),
                    ]),
                    node_index=node_index, location=projection["location"],
                    validation_type=safe_kind,
                    schema_detail=projection["schema_detail"],
                )
            return
        message = str(exc)
        code = _KNOWN_ERRORS.get(message, "CONTRACT_CHECK_FAILED")
        if isinstance(exc, json.JSONDecodeError):
            code = "JSON_PARSE_FAILED"
        self._add(selected, code, diagnostic_checksum([node_index, message]), node_index=node_index)

    def patch_receipt(
        self, before: Any, after: Any, progress: GraphPatchProgress,
        graph: Any, *, failed: bool,
    ) -> None:
        original = [item.model_dump(mode="json") for item in before.operations]
        normalized = [item.model_dump(mode="json") for item in after.operations]
        index = (
            progress.operation_index
            if failed and progress.phase == "operations" else None
        )
        receipt: dict[str, Any] = {
            "phase": progress.phase,
            "before_operation_count": len(original),
            "after_operation_count": len(normalized),
            "before_checksum": diagnostic_checksum(original),
            "after_checksum": diagnostic_checksum(normalized),
            "failed_operation_index": index,
            "index_basis": "normalized_zero_based",
            "original_operation_indices": [],
            "node_changes": deepcopy(progress.node_changes),
            "omitted_node_change_count": progress.omitted_node_change_count,
            "unavailable_node_change_count": progress.unavailable_node_change_count,
        }
        if index is not None and 0 <= index < len(normalized):
            operation = normalized[index]
            digest = diagnostic_checksum(operation)
            receipt["operation_checksum"] = digest
            receipt["original_operation_indices"] = [
                i for i, item in enumerate(original) if diagnostic_checksum(item) == digest
            ]
            known_nodes = {node.ref: node for node in graph.nodes}
            safe_operation = {"op": after.operations[index].op}
            for name in ("ref", "node_ref", "source_ref", "target_ref"):
                value = operation.get(name)
                if (
                    isinstance(value, str) and value in known_nodes
                    and _TOKEN.fullmatch(value) and not _PRIVATE_TOKEN.search(value)
                ):
                    safe_operation[name] = value
            # Unknown ports are represented by the operation index/hash only.
            for name, ref_name, bindings in (
                ("source_port", "source_ref", "outputs"),
                ("target_port", "target_ref", "inputs"),
            ):
                value = operation.get(name)
                node = known_nodes.get(operation.get(ref_name))
                if (
                    node is not None and isinstance(value, str)
                    and value in {item.port for item in getattr(node, bindings)}
                    and value in {"task", "result", "value", "values", "records", "json", "left", "right"}
                ):
                    safe_operation[name] = value
            outcome = operation.get("outcome_ref")
            if outcome in _SEMANTIC_OUTCOMES:
                safe_operation["outcome_ref"] = outcome
            receipt["operation"] = safe_operation
        self.patch = receipt

    def as_dict(self) -> dict[str, Any]:
        phases = list(_PHASES_BY_STAGE.get(self.stage, tuple(self.checks)))
        if "recipe_lowering" in self.checks and "recipe_lowering" not in phases:
            phases.insert(phases.index("intent_parse") + 1, "recipe_lowering")
        result: dict[str, Any] = {
            "version": DIAGNOSTICS_VERSION, "stage": self.stage,
            "checks_executed": list(self.checks), "failed_phase": self.failed_phase,
            "issue_count": self.issue_count, "issues": deepcopy(self.issues),
            "category_counts": dict(sorted(self.category_counts.items())),
            "omitted_issue_count": self.issue_count - len(self.issues),
            "recompile_executed": self.recompile_executed,
            "phase_results": [
                {"id": phase,
                 "status": "failed" if phase in self._failed_phases else "passed" if phase in self.checks else "blocked",
                 "blocked_by": None if phase in self.checks else self.failed_phase or "not_executed"}
                for phase in phases
            ],
        }
        if self.patch is not None:
            result["patch"] = deepcopy(self.patch)
        if self._control_contract_issues is not None:
            result["control_contract_issues"] = deepcopy(self._control_contract_issues)
            result["control_graph_checksum"] = self._control_graph_checksum
        if self._binding_summary is not None:
            result.update(deepcopy(self._binding_summary))
        if self._router_inputs:
            result["router_inputs"] = deepcopy(self._router_inputs)
        if self._control_proof_checks:
            result["control_proof_checks"] = deepcopy(self._control_proof_checks)
        if self.recipe_preflight is not None:
            result["recipe_preflight"] = deepcopy(self.recipe_preflight)
        if self.recipe_repair_progress is not None:
            result["recipe_repair_progress"] = deepcopy(self.recipe_repair_progress)
        return result
