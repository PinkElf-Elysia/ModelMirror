from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Protocol

try:
    from server.workflow_native.control_data import aggregate_rows, compare_datasets
    from server.workflow_native.r20_nodes import execute_variable_aggregator_v2
    from server.workflow_native.values import (
        deserialize_workflow_value,
        normalize_workflow_value,
        serialize_workflow_value,
    )
except ModuleNotFoundError:  # pragma: no cover - container import layout
    from workflow_native.control_data import aggregate_rows, compare_datasets
    from workflow_native.r20_nodes import execute_variable_aggregator_v2
    from workflow_native.values import (
        deserialize_workflow_value,
        normalize_workflow_value,
        serialize_workflow_value,
    )

from .store import EvaluationStateError


MAX_FIXTURE_ROWS = 200
MAX_FIXTURES_PER_RUN = 1_000
MAX_FIXTURE_BYTES_PER_RUN = 16 * 1024 * 1024
_PURE_NODE_KINDS = {
    "json_serialize",
    "json_deserialize",
    "variable_aggregator",
    "data_aggregate",
    "dataset_compare",
}
_AGENT_NODE_KINDS = {"agent", "workflow_agent", "agent_task"}


def render_evaluation_case_inputs(
    target: dict[str, Any],
    case: dict[str, Any],
) -> tuple[str, list[dict[str, str]], str]:
    """Render the exact input shared by fixture capture and runtime execution."""

    history = [
        {
            "role": str(item.get("role") or "user"),
            "content": str(item.get("content") or "")[:20_000],
        }
        for item in list(case.get("messages") or [])[-20:]
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    history_json = json.dumps(history, ensure_ascii=False)
    message = str(case.get("message") or "")[:20_000]
    input_template = str(target.get("input_template") or "")
    if input_template:
        message = re.sub(r"{{\s*args\s*}}", message, input_template)[:20_000]
    return message, history, history_json


class AgentTableEvaluationBackend(Protocol):
    """Trusted adapter supplied by the application integration layer.

    ``capture_evaluation_queries`` must execute the whole request in one
    read-only database snapshot. The Evaluator deliberately does not emulate
    this with repeated ``query_records`` calls.
    """

    def resolve_schema_version(self, table_id: str, **kwargs: Any) -> Any: ...

    def validate_workflow_node_contract(
        self,
        table_id: str,
        **kwargs: Any,
    ) -> None: ...

    def capture_evaluation_queries(
        self,
        queries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...


def prepare_agent_table_fixtures(
    *,
    targets: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    backend: AgentTableEvaluationBackend | None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for target in targets:
        workflow = dict(target.get("workflow") or {})
        table_nodes = [
            node
            for node in list(workflow.get("nodes") or [])
            if _node_kind(node) == "data_table_query"
        ]
        if not table_nodes:
            continue
        if backend is None:
            raise EvaluationStateError(
                "Agent Table evaluation requires a transactional resource fixture provider."
            )
        _assert_query_dependencies_are_deterministic(target, workflow, table_nodes)
        for case in cases:
            required_variables: set[str] = set()
            for node in table_nodes:
                required_variables.update(
                    _filter_variables(_node_data(node).get("filter"))
                )
            variables = _resolve_case_variables(
                target,
                case,
                workflow,
                required_variables=required_variables,
            )
            for node in table_nodes:
                requests.append(
                    _build_query_request(target, case, node, variables, backend)
                )
                if len(requests) > MAX_FIXTURES_PER_RUN:
                    raise EvaluationStateError(
                        "Evaluation run exceeds the 1000 Agent Table fixture limit."
                    )
    if not requests:
        return []
    assert backend is not None
    raw = backend.capture_evaluation_queries(copy.deepcopy(requests))
    if not isinstance(raw, list):
        raise EvaluationStateError(
            "Agent Table fixture provider returned an invalid response."
        )
    by_key: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise EvaluationStateError(
                "Agent Table fixture provider returned a non-object fixture."
            )
        fixture_key = str(item.get("fixture_key") or "")
        if not fixture_key or fixture_key in by_key:
            raise EvaluationStateError(
                "Agent Table fixture provider returned duplicate or missing fixture keys."
            )
        by_key[fixture_key] = item
    expected_keys = {str(item["fixture_key"]) for item in requests}
    if set(by_key) != expected_keys:
        raise EvaluationStateError(
            "Agent Table fixture provider did not return the exact requested fixture set."
        )

    fixtures: list[dict[str, Any]] = []
    for request in requests:
        response = by_key[str(request["fixture_key"])]
        records = response.get("records")
        if not isinstance(records, list):
            raise EvaluationStateError("Agent Table fixture records must be an array.")
        if len(records) > min(MAX_FIXTURE_ROWS, int(request["limit"])):
            raise EvaluationStateError(
                "Agent Table fixture exceeds the per-query row limit."
            )
        normalized = normalize_workflow_value(
            records,
            path=f"$.evaluation_fixtures.{request['fixture_key']}.records",
        )
        if not isinstance(normalized, list) or any(
            not isinstance(record, dict) for record in normalized
        ):
            raise EvaluationStateError(
                "Agent Table fixture records must contain JSON objects."
            )
        fixtures.append(
            {
                **copy.deepcopy(request),
                "records": normalized,
                "records_checksum": _checksum(normalized),
                "result_count": len(normalized),
                "record_ids": _record_ids(normalized),
            }
        )
    encoded = _canonical_json(fixtures).encode("utf-8")
    if len(encoded) > MAX_FIXTURE_BYTES_PER_RUN:
        raise EvaluationStateError(
            "Evaluation run exceeds the 16 MiB Agent Table fixture limit."
        )
    return fixtures


def inspect_agent_table_node(
    node: Any,
    *,
    backend: AgentTableEvaluationBackend | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    data = _node_data(node)
    if backend is None:
        return None, {
            "code": "evaluation_resource_fixture_unavailable",
            "message": (
                "Agent Table evaluation requires a transactional resource fixture provider."
            ),
            "node_id": _node_id(node),
        }
    try:
        table_id = str(data.get("tableId") or "").strip()
        policy = str(data.get("versionPolicy") or "latest").strip()
        pinned = data.get("pinnedSchemaVersion")
        schema = backend.resolve_schema_version(
            table_id,
            version_policy=policy,
            pinned_version=int(pinned) if pinned not in {None, ""} else None,
            write=False,
        )
        backend.validate_workflow_node_contract(
            table_id,
            schema_version=int(schema.version),
            kind="data_table_query",
            data=data,
        )
        limit = int(data.get("limit") or 20)
        if not 1 <= limit <= MAX_FIXTURE_ROWS:
            raise ValueError("Agent Table query limit must be between 1 and 200.")
        data["evaluationPinnedSchemaVersion"] = int(schema.version)
        data["evaluationPinnedSchemaChecksum"] = str(schema.checksum)
        _set_node_data(node, data)
        return {
            "table_id": table_id,
            "schema_version": int(schema.version),
            "schema_checksum": str(schema.checksum),
        }, None
    except Exception as exc:
        return None, {
            "code": "evaluation_data_table_invalid",
            "message": str(exc)[:500],
            "node_id": _node_id(node),
        }


def assert_agent_table_dependencies(
    workflow: Any,
    table_nodes: list[Any],
) -> list[dict[str, Any]]:
    workflow_payload = (
        workflow.model_dump(mode="json")
        if hasattr(workflow, "model_dump")
        else dict(workflow or {})
    )
    try:
        _assert_query_dependencies_are_deterministic(
            {}, workflow_payload, table_nodes
        )
    except EvaluationStateError as exc:
        return [
            {
                "code": "evaluation_data_table_dynamic_dependency",
                "message": str(exc)[:500],
                "node_id": _node_id(table_nodes[0]) if table_nodes else None,
            }
        ]
    return []


def fixture_payload_for_item(
    fixtures: list[dict[str, Any]],
    *,
    target_id: str,
    case_id: str,
) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(item)
        for item in fixtures
        if str(item.get("target_id") or "") == target_id
        and str(item.get("case_id") or "") == case_id
    ]


def sanitize_resource_reads(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in list(value or [])[:100]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("resource_kind") or "")
        node_ref = str(item.get("node_ref") or "")[:64]
        resource_id = str(item.get("resource_id") or "")[:200]
        if kind not in {"knowledge_retrieval", "data_table_query"}:
            continue
        if not node_ref or not resource_id:
            continue
        clean: dict[str, Any] = {
            "node_ref": node_ref,
            "kind": kind,
            "resource_id": resource_id,
            "result_count": max(0, min(int(item.get("result_count") or 0), 10_000)),
        }
        if item.get("version_id") not in {None, ""}:
            clean["version_id"] = str(item.get("version_id"))[:200]
        if item.get("schema_version") not in {None, ""}:
            clean["schema_version"] = max(1, int(item.get("schema_version")))
        checksum = str(item.get("query_checksum") or "")
        if len(checksum) == 64 and all(char in "0123456789abcdef" for char in checksum):
            clean["query_checksum"] = checksum
        clean["record_ids"] = _bounded_strings(item.get("record_ids"), 200, 200)
        clean["citation_ids"] = _bounded_strings(item.get("citation_ids"), 50, 200)
        result.append(clean)
    return result


def _build_query_request(
    target: dict[str, Any],
    case: dict[str, Any],
    node: Any,
    variables: dict[str, Any],
    backend: AgentTableEvaluationBackend,
) -> dict[str, Any]:
    data = _node_data(node)
    table_id = str(data.get("tableId") or "").strip()
    schema_version = int(data.get("evaluationPinnedSchemaVersion") or 0)
    if schema_version < 1:
        policy = str(data.get("versionPolicy") or "latest")
        pinned = data.get("pinnedSchemaVersion")
        schema = backend.resolve_schema_version(
            table_id,
            version_policy=policy,
            pinned_version=int(pinned) if pinned not in {None, ""} else None,
            write=False,
        )
        schema_version = int(schema.version)
        schema_checksum = str(schema.checksum)
    else:
        schema = backend.resolve_schema_version(
            table_id,
            version_policy="pinned",
            pinned_version=schema_version,
            write=False,
        )
        schema_checksum = str(schema.checksum)
    resolved_filter = _resolve_filter(data.get("filter"), variables)
    fields = data.get("selectFields")
    selected_fields = [str(item) for item in fields] if isinstance(fields, list) else None
    sort = data.get("sort")
    normalized_sort = copy.deepcopy(sort) if isinstance(sort, list) else None
    limit = max(1, min(int(data.get("limit") or 20), MAX_FIXTURE_ROWS))
    return_mode = str(data.get("returnMode") or "list")
    query_contract = {
        "table_id": table_id,
        "schema_version": schema_version,
        "fields": selected_fields,
        "filter": resolved_filter,
        "sort": normalized_sort,
        "limit": limit,
        "return_mode": return_mode,
    }
    node_id = _node_id(node)
    target_id = str(target.get("target_id") or "")
    case_id = str(case.get("case_id") or "")
    fixture_key = _checksum(
        {"target_id": target_id, "case_id": case_id, "node_id": node_id}
    )
    return {
        "fixture_key": fixture_key,
        "target_id": target_id,
        "case_id": case_id,
        "node_id": node_id,
        "node_ref": str(data.get("plannerRef") or node_id)[:64],
        "resource_kind": "data_table_query",
        "resource_id": table_id,
        "table_id": table_id,
        "schema_version": schema_version,
        "schema_checksum": schema_checksum,
        "query_checksum": _checksum(query_contract),
        "fields": selected_fields,
        "filter": resolved_filter,
        "sort": normalized_sort,
        "limit": limit,
        "return_mode": return_mode,
    }


def _resolve_case_variables(
    target: dict[str, Any],
    case: dict[str, Any],
    workflow: dict[str, Any],
    *,
    required_variables: set[str],
) -> dict[str, Any]:
    message, _history, history_json = render_evaluation_case_inputs(target, case)
    variables: dict[str, Any] = {
        str(target.get("input_variable") or "user_input"): message,
        str(target.get("history_variable") or "conversation_history"): history_json,
        "user_input": message,
        "conversation_history": history_json,
        "xpert_file_context": "",
        "xpert_memory_context": "",
    }
    for declaration in list(workflow.get("variables") or []):
        if not isinstance(declaration, dict):
            continue
        name = str(declaration.get("name") or "")
        if not name or name in variables:
            continue
        if declaration.get("kind") == "constant" or "defaultValue" in declaration:
            variables[name] = normalize_workflow_value(
                declaration.get("defaultValue"), path=f"$.variables.{name}"
            )
    for node in list(workflow.get("nodes") or []):
        if _node_kind(node) != "input":
            continue
        data = _node_data(node)
        name = str(data.get("variableName") or "user_input")
        variables.setdefault(name, variables.get("user_input", ""))

    producers = {
        str(_node_data(node).get("outputVariable") or ""): node
        for node in list(workflow.get("nodes") or [])
        if str(_node_data(node).get("outputVariable") or "")
    }

    def resolve(variable: str, stack: set[str]) -> None:
        if variable in variables:
            return
        if variable in stack:
            raise EvaluationStateError(
                f"Agent Table fixture variable dependency is cyclic: {variable}."
            )
        producer = producers.get(variable)
        if producer is None or _node_kind(producer) not in _PURE_NODE_KINDS:
            raise EvaluationStateError(
                f"Agent Table filter variable '{variable}' could not be frozen."
            )
        for source in _pure_inputs(producer):
            resolve(source, {*stack, variable})
        _execute_pure_node(producer, variables)

    for required in sorted(required_variables):
        resolve(required, set())
    return variables


def _execute_pure_node(node: Any, variables: dict[str, Any]) -> None:
    data = _node_data(node)
    kind = _node_kind(node)
    output = str(data.get("outputVariable") or "")
    if kind == "json_serialize":
        variables[output] = serialize_workflow_value(
            variables[str(data.get("inputVariable") or "")],
            pretty=str(data.get("format") or "compact") == "pretty",
            max_bytes=5 * 1024 * 1024,
        )
    elif kind == "json_deserialize":
        variables[output] = deserialize_workflow_value(
            variables[str(data.get("inputVariable") or "")],
            expected_schema=data.get("expectedSchema"),
            max_bytes=5 * 1024 * 1024,
        )
    elif kind == "variable_aggregator":
        name, value = execute_variable_aggregator_v2(data, variables)
        variables[name] = value
    elif kind == "data_aggregate":
        variables[output] = aggregate_rows(
            variables[str(data.get("inputVariable") or "")],
            group_by_fields=data.get("groupByFields"),
            measures=data.get("measures"),
        )
    elif kind == "dataset_compare":
        variables[output] = compare_datasets(
            variables[str(data.get("leftVariable") or "")],
            variables[str(data.get("rightVariable") or "")],
            key_fields=data.get("keyFields"),
            include_unchanged=bool(data.get("includeUnchanged", False)),
        )


def _assert_query_dependencies_are_deterministic(
    target: dict[str, Any],
    workflow: dict[str, Any],
    table_nodes: list[Any],
) -> None:
    base = {
        str(target.get("input_variable") or "user_input"),
        str(target.get("history_variable") or "conversation_history"),
        "user_input",
        "conversation_history",
        "xpert_file_context",
        "xpert_memory_context",
    }
    producers: dict[str, Any] = {}
    for declaration in list(workflow.get("variables") or []):
        if (
            isinstance(declaration, dict)
            and declaration.get("kind") in {"input", "constant"}
            and str(declaration.get("name") or "")
        ):
            base.add(str(declaration["name"]))
    for node in list(workflow.get("nodes") or []):
        data = _node_data(node)
        if _node_kind(node) == "input":
            base.add(str(data.get("variableName") or "user_input"))
        output = str(data.get("outputVariable") or "")
        if output:
            producers[output] = node

    def require_safe(variable: str, stack: set[str]) -> None:
        if variable in base:
            return
        if variable in stack:
            raise EvaluationStateError(
                f"Agent Table filter variable dependency is cyclic: {variable}."
            )
        producer = producers.get(variable)
        if producer is None:
            raise EvaluationStateError(
                f"Agent Table filter references unavailable variable '{variable}'."
            )
        kind = _node_kind(producer)
        if kind in _AGENT_NODE_KINDS:
            raise EvaluationStateError(
                "Agent Table evaluation cannot freeze filters derived from a prior Agent output: "
                + variable
            )
        if kind not in _PURE_NODE_KINDS:
            raise EvaluationStateError(
                f"Agent Table evaluation cannot freeze filter variable '{variable}' "
                f"from node kind '{kind}'."
            )
        for source in _pure_inputs(producer):
            require_safe(source, {*stack, variable})

    for node in table_nodes:
        for variable in _filter_variables(_node_data(node).get("filter")):
            require_safe(variable, set())


def _pure_inputs(node: Any) -> set[str]:
    data = _node_data(node)
    kind = _node_kind(node)
    if kind in {"json_serialize", "json_deserialize", "data_aggregate"}:
        return {str(data.get("inputVariable") or "")}
    if kind == "dataset_compare":
        return {
            str(data.get("leftVariable") or ""),
            str(data.get("rightVariable") or ""),
        }
    if kind == "variable_aggregator":
        return {
            str(item.get("sourceVariable") or "")
            for item in list(data.get("bindings") or [])
            if isinstance(item, dict)
        }
    return set()


def _filter_variables(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    if isinstance(value.get("items"), list):
        result: set[str] = set()
        for item in value["items"]:
            result.update(_filter_variables(item))
        return result
    binding = value.get("value")
    if isinstance(binding, dict) and binding.get("source") == "variable":
        name = str(binding.get("variable") or "")
        return {name} if name else set()
    return set()


def _resolve_filter(value: Any, variables: dict[str, Any]) -> dict[str, Any] | None:
    if value is None or value == () or value == {}:
        return None
    if not isinstance(value, dict):
        raise EvaluationStateError("Agent Table filter must be an object.")
    if "items" in value or "logic" in value:
        items = value.get("items")
        if not isinstance(items, list):
            raise EvaluationStateError("Agent Table filter group items must be an array.")
        return {
            "logic": str(value.get("logic") or "").lower(),
            "items": [_resolve_filter(item, variables) for item in items],
        }
    field = str(value.get("field") or "").strip()
    operator = str(value.get("operator") or "").strip().lower()
    result: dict[str, Any] = {"field": field, "operator": operator}
    if operator != "is_null":
        binding = value.get("value")
        if not isinstance(binding, dict):
            raise EvaluationStateError(
                f"Agent Table filter '{field}' must use a typed value binding."
            )
        source = str(binding.get("source") or "")
        if source == "literal" and "value" in binding:
            result["value"] = normalize_workflow_value(
                binding.get("value"), path=f"$.filter.{field}"
            )
        elif source == "variable":
            variable = str(binding.get("variable") or "")
            if variable not in variables:
                raise EvaluationStateError(
                    f"Agent Table filter variable '{variable}' could not be frozen."
                )
            result["value"] = normalize_workflow_value(
                variables[variable], path=f"$.filter.{field}"
            )
        else:
            raise EvaluationStateError(
                f"Agent Table filter '{field}' has an invalid value binding."
            )
    return result


def _node_data(node: Any) -> dict[str, Any]:
    if isinstance(node, dict):
        return dict(node.get("data") or {})
    return dict(getattr(node, "data", {}) or {})


def _set_node_data(node: Any, data: dict[str, Any]) -> None:
    if isinstance(node, dict):
        node["data"] = data
    else:
        node.data = data


def _node_kind(node: Any) -> str:
    data = _node_data(node)
    if isinstance(node, dict):
        return str(data.get("kind") or node.get("type") or "")
    return str(data.get("kind") or getattr(node, "type", "") or "")


def _node_id(node: Any) -> str:
    return str(node.get("id") if isinstance(node, dict) else getattr(node, "id", ""))


def _record_ids(records: list[dict[str, Any]]) -> list[str]:
    return [
        str(record.get("record_id"))[:200]
        for record in records
        if str(record.get("record_id") or "")
    ][:MAX_FIXTURE_ROWS]


def _bounded_strings(value: Any, limit: int, item_limit: int) -> list[str]:
    return [
        str(item).strip()[:item_limit]
        for item in list(value or [])[:limit]
        if str(item).strip()
    ]


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
