from __future__ import annotations

import json
import math
import re
from functools import cmp_to_key
from typing import Any

from .values import WorkflowValue, normalize_workflow_value


COMPARISON_OPERATORS = {
    "equals",
    "not_equals",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "in",
    "is_null",
}
COMPARISON_VALUE_TYPES = {"text", "number", "boolean", "null", "json"}
LIST_OPERATORS = {
    "length",
    "join",
    "first",
    "last",
    "filter",
    "sort",
    "deduplicate",
    "take",
    "skip",
    "slice",
}
AGGREGATE_OPERATIONS = {"count", "sum", "avg", "min", "max"}
MAX_COLLECTION_ITEMS = 10_000
MAX_DATA_AGGREGATE_OUTPUT_BYTES = 5 * 1_024 * 1_024
MAX_DATASET_COMPARE_OUTPUT_BYTES = 5 * 1_024 * 1_024
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
ROUTE_ID_PATTERN = re.compile(r"^route_[1-8]$")
SAFE_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SENSITIVE_ERROR_MESSAGE_PATTERN = re.compile(
    r"(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[0-9A-Za-z_-]{35}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}|"
    r"eyJ[A-Za-z0-9_-]{4,}\.eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{8,}|"
    r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----|"
    r"(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


class WorkflowControlDataError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


class WorkflowTerminationError(RuntimeError):
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        node_id: str,
        attempts: int | None = None,
        max_attempts: int | None = None,
        classification: str | None = None,
        exhausted: bool | None = None,
    ) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message
        self.node_id = node_id
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.classification = classification
        self.exhausted = exhausted


def _fail(code: str, message: str) -> None:
    raise WorkflowControlDataError(code, message)


def is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def typed_identity(value: WorkflowValue) -> tuple[Any, ...]:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if is_finite_number(value):
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(typed_identity(item) for item in value))
    return (
        "object",
        tuple(
            (key, typed_identity(item))
            for key, item in sorted(value.items(), key=lambda pair: pair[0])
        ),
    )


def typed_deep_equal(left: object, right: object) -> bool:
    normalized_left = normalize_workflow_value(left)
    normalized_right = normalize_workflow_value(right)
    return typed_identity(normalized_left) == typed_identity(normalized_right)


def comparison_rule_value(rule: dict[str, Any]) -> WorkflowValue:
    operator = str(rule.get("operator") or "").strip()
    if operator == "is_null":
        return None
    value_type = str(rule.get("valueType") or "").strip()
    if value_type not in COMPARISON_VALUE_TYPES:
        _fail(
            "INVALID_COMPARISON_VALUE_TYPE",
            "Comparison valueType must be text, number, boolean, null, or json.",
        )
    if "value" not in rule:
        _fail("MISSING_COMPARISON_VALUE", "Comparison rule needs a typed value.")
    value = normalize_workflow_value(rule.get("value"), path="$.rule.value")
    matches_type = {
        "text": isinstance(value, str),
        "number": is_finite_number(value),
        "boolean": isinstance(value, bool),
        "null": value is None,
        "json": True,
    }[value_type]
    if not matches_type:
        _fail(
            "COMPARISON_VALUE_TYPE_MISMATCH",
            "Comparison value does not match its declared valueType.",
        )
    if operator in {"gt", "gte", "lt", "lte"} and not is_finite_number(value):
        _fail(
            "NUMERIC_COMPARISON_REQUIRES_NUMBER",
            "Numeric comparison rules require a finite numeric value.",
        )
    if operator == "in" and not isinstance(value, list):
        _fail("IN_COMPARISON_REQUIRES_ARRAY", "The in operator requires an array value.")
    return value


def validate_comparison_rule(
    rule: object,
    *,
    allow_field: bool,
    require_route: bool = False,
) -> dict[str, Any]:
    if not isinstance(rule, dict):
        _fail("INVALID_COMPARISON_RULE", "Comparison rule must be an object.")
    operator = str(rule.get("operator") or "").strip()
    if operator not in COMPARISON_OPERATORS:
        _fail("INVALID_COMPARISON_OPERATOR", "Comparison rule uses an unsupported operator.")
    if allow_field:
        field = rule.get("field", "")
        if not isinstance(field, str) or len(field) > 64:
            _fail(
                "INVALID_COMPARISON_FIELD",
                "Comparison field must be a top-level field name of at most 64 characters.",
            )
    elif str(rule.get("field") or "").strip():
        _fail(
            "COMPARISON_FIELD_NOT_ALLOWED",
            "This comparison applies to the selected variable and does not accept a field.",
        )
    if require_route:
        route_id = str(rule.get("id") or "").strip()
        if not ROUTE_ID_PATTERN.fullmatch(route_id):
            _fail("INVALID_ROUTE_ID", "Route id must be route_1 through route_8.")
        label = str(rule.get("label") or "").strip()
        if not 1 <= len(label) <= 80:
            _fail("INVALID_ROUTE_LABEL", "Route label must contain 1 to 80 characters.")
    comparison_rule_value(rule)
    return rule


def evaluate_comparison_rule(actual: object, rule: dict[str, Any]) -> bool:
    operator = str(rule.get("operator") or "").strip()
    validate_comparison_rule(
        rule,
        allow_field="field" in rule,
        require_route="id" in rule,
    )
    normalized_actual = normalize_workflow_value(actual, path="$.rule.actual")
    expected = comparison_rule_value(rule)
    if operator == "equals":
        return typed_deep_equal(normalized_actual, expected)
    if operator == "not_equals":
        return not typed_deep_equal(normalized_actual, expected)
    if operator in {"gt", "gte", "lt", "lte"}:
        if not is_finite_number(normalized_actual):
            _fail(
                "NUMERIC_COMPARISON_TYPE_MISMATCH",
                "Numeric comparison input must be a finite number.",
            )
        if operator == "gt":
            return normalized_actual > expected  # type: ignore[operator]
        if operator == "gte":
            return normalized_actual >= expected  # type: ignore[operator]
        if operator == "lt":
            return normalized_actual < expected  # type: ignore[operator]
        return normalized_actual <= expected  # type: ignore[operator]
    if operator == "contains":
        if isinstance(normalized_actual, str):
            if not isinstance(expected, str):
                _fail(
                    "CONTAINS_COMPARISON_TYPE_MISMATCH",
                    "String contains comparison requires a text value.",
                )
            return expected in normalized_actual
        if isinstance(normalized_actual, list):
            return any(typed_deep_equal(item, expected) for item in normalized_actual)
        _fail(
            "CONTAINS_COMPARISON_TYPE_MISMATCH",
            "Contains comparison input must be text or an array.",
        )
    if operator == "in":
        return any(typed_deep_equal(normalized_actual, item) for item in expected)  # type: ignore[union-attr]
    return normalized_actual is None


def select_multi_route(value: object, routes: object) -> str:
    if not isinstance(routes, list) or not 2 <= len(routes) <= 8:
        _fail("INVALID_ROUTE_COUNT", "Multi route requires between 2 and 8 routes.")
    seen: set[str] = set()
    for raw_route in routes:
        route = validate_comparison_rule(
            raw_route,
            allow_field=False,
            require_route=True,
        )
        route_id = str(route["id"])
        if route_id in seen:
            _fail("DUPLICATE_ROUTE_ID", "Multi route ids must be unique.")
        seen.add(route_id)
        if evaluate_comparison_rule(value, route):
            return route_id
    return "default"


def evaluate_typed_condition(
    value: object,
    *,
    field: object,
    operator: object,
    value_type: object,
    expected: object,
) -> bool:
    clean_field = str(field or "").strip()
    if len(clean_field) > 64:
        _fail(
            "INVALID_CONDITION_FIELD",
            "Condition field must be a top-level field name of at most 64 characters.",
        )
    normalized = normalize_workflow_value(value, path="$.condition_input")
    if clean_field:
        if not isinstance(normalized, dict):
            _fail(
                "CONDITION_FIELD_REQUIRES_OBJECT",
                "Condition field selection requires an object input.",
            )
        if clean_field not in normalized:
            _fail(
                "CONDITION_FIELD_MISSING",
                "The selected condition field is missing from the input object.",
            )
        normalized = normalized[clean_field]
    rule: dict[str, Any] = {
        "operator": str(operator or "").strip(),
        "valueType": str(value_type or "").strip(),
    }
    if rule["operator"] != "is_null":
        rule["value"] = expected
    return evaluate_comparison_rule(normalized, rule)


def validate_dataset_compare_config(key_fields: object) -> list[str]:
    if not isinstance(key_fields, list) or not 1 <= len(key_fields) <= 3:
        _fail(
            "INVALID_DATASET_KEY_FIELD_COUNT",
            "Dataset compare requires between 1 and 3 key fields.",
        )
    fields = [str(field).strip() for field in key_fields]
    if any(not field or len(field) > 64 for field in fields):
        _fail(
            "INVALID_DATASET_KEY_FIELD",
            "Dataset key fields must be non-empty top-level field names.",
        )
    if len(fields) != len(set(fields)):
        _fail("DUPLICATE_DATASET_KEY_FIELD", "Dataset key fields must be unique.")
    return fields


def _dataset_index(
    value: object,
    *,
    side: str,
    key_fields: list[str],
) -> tuple[list[dict[str, WorkflowValue]], dict[tuple[Any, ...], dict[str, WorkflowValue]]]:
    rows = normalize_workflow_value(value, path=f"$.dataset_compare.{side}")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _fail(
            "DATASET_OBJECT_ARRAY_REQUIRED",
            "Both dataset compare inputs must be JSON object arrays.",
        )
    if len(rows) > MAX_COLLECTION_ITEMS:
        _fail(
            "DATASET_ROW_LIMIT_EXCEEDED",
            f"Each dataset compare input accepts at most {MAX_COLLECTION_ITEMS} rows.",
        )
    index: dict[tuple[Any, ...], dict[str, WorkflowValue]] = {}
    for row in rows:
        if any(field not in row for field in key_fields):
            _fail(
                "DATASET_KEY_FIELD_MISSING",
                "Every dataset row must contain every configured key field.",
            )
        values = [row[field] for field in key_fields]
        if any(isinstance(item, (dict, list)) for item in values):
            _fail(
                "DATASET_KEY_VALUE_NOT_SCALAR",
                "Dataset key values must be scalar or null.",
            )
        identity = tuple(typed_identity(item) for item in values)
        if identity in index:
            _fail(
                "DATASET_KEY_NOT_UNIQUE",
                f"Composite keys must be unique within the {side} dataset.",
            )
        index[identity] = row
    return rows, index


def compare_datasets(
    left: object,
    right: object,
    *,
    key_fields: object,
    include_unchanged: bool = False,
    max_output_bytes: int = MAX_DATASET_COMPARE_OUTPUT_BYTES,
) -> dict[str, WorkflowValue]:
    fields = validate_dataset_compare_config(key_fields)
    left_rows, left_index = _dataset_index(left, side="left", key_fields=fields)
    right_rows, right_index = _dataset_index(right, side="right", key_fields=fields)
    added: list[WorkflowValue] = []
    removed: list[WorkflowValue] = []
    changed: list[WorkflowValue] = []
    unchanged: list[WorkflowValue] = []
    unchanged_count = 0

    for after in right_rows:
        identity = tuple(typed_identity(after[field]) for field in fields)
        before = left_index.get(identity)
        if before is None:
            added.append(after)
            continue
        if typed_deep_equal(before, after):
            unchanged_count += 1
            if include_unchanged:
                unchanged.append(after)
            continue
        changed_fields = sorted(
            field
            for field in set(before) | set(after)
            if field not in fields
            and (
                field not in before
                or field not in after
                or not typed_deep_equal(before[field], after[field])
            )
        )
        changed.append(
            {
                "key": {field: after[field] for field in fields},
                "before": before,
                "after": after,
                "changedFields": changed_fields,
            }
        )

    for before in left_rows:
        identity = tuple(typed_identity(before[field]) for field in fields)
        if identity not in right_index:
            removed.append(before)

    result: dict[str, WorkflowValue] = {
        "summary": {
            "leftCount": len(left_rows),
            "rightCount": len(right_rows),
            "addedCount": len(added),
            "removedCount": len(removed),
            "changedCount": len(changed),
            "unchangedCount": unchanged_count,
        },
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchangedIncluded": bool(include_unchanged),
        "unchanged": unchanged,
    }
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > max(1, int(max_output_bytes)):
        _fail(
            "DATASET_OUTPUT_LIMIT_EXCEEDED",
            "Dataset compare output exceeds the workflow persistence limit.",
        )
    return result


def _field_value(item: WorkflowValue, field: str) -> WorkflowValue:
    if not field:
        return item
    if isinstance(item, dict):
        return item.get(field)
    return None


def filter_array(
    items: list[WorkflowValue],
    *,
    rules: object,
    mode: str,
) -> list[WorkflowValue]:
    if not isinstance(rules, list) or not 1 <= len(rules) <= 10:
        _fail("INVALID_FILTER_RULE_COUNT", "Filter requires between 1 and 10 rules.")
    if mode not in {"all", "any"}:
        _fail("INVALID_FILTER_MODE", "Filter mode must be all or any.")
    validated = [
        validate_comparison_rule(rule, allow_field=True)
        for rule in rules
    ]
    output: list[WorkflowValue] = []
    for item in items:
        matches = [
            evaluate_comparison_rule(
                _field_value(item, str(rule.get("field") or "").strip()),
                rule,
            )
            for rule in validated
        ]
        if (all(matches) if mode == "all" else any(matches)):
            output.append(item)
    return list(output)


def _comparable_kind(value: WorkflowValue) -> str:
    if is_finite_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    _fail(
        "SORT_VALUE_NOT_COMPARABLE",
        "Sort values must be finite numbers, text, booleans, or null.",
    )


def sort_array(items: list[WorkflowValue], *, keys: object) -> list[WorkflowValue]:
    if not isinstance(keys, list) or not 1 <= len(keys) <= 3:
        _fail("INVALID_SORT_KEY_COUNT", "Sort requires between 1 and 3 keys.")
    normalized_keys: list[dict[str, str]] = []
    for raw_key in keys:
        if not isinstance(raw_key, dict):
            _fail("INVALID_SORT_KEY", "Sort key must be an object.")
        field = raw_key.get("field", "")
        direction = str(raw_key.get("direction") or "").strip()
        nulls = str(raw_key.get("nulls") or "").strip()
        if not isinstance(field, str) or len(field) > 64:
            _fail("INVALID_SORT_FIELD", "Sort field must be a top-level field name.")
        if direction not in {"asc", "desc"}:
            _fail("INVALID_SORT_DIRECTION", "Sort direction must be asc or desc.")
        if nulls not in {"first", "last"}:
            _fail("INVALID_SORT_NULL_POSITION", "Sort null position must be first or last.")
        normalized_keys.append({"field": field.strip(), "direction": direction, "nulls": nulls})

    for key in normalized_keys:
        kinds = {
            _comparable_kind(value)
            for item in items
            if (value := _field_value(item, key["field"])) is not None
        }
        if len(kinds) > 1:
            _fail(
                "SORT_VALUE_TYPE_MISMATCH",
                "Each sort key must resolve to one comparable value type.",
            )

    def compare(left: WorkflowValue, right: WorkflowValue) -> int:
        for key in normalized_keys:
            left_value = _field_value(left, key["field"])
            right_value = _field_value(right, key["field"])
            if left_value is None or right_value is None:
                if left_value is None and right_value is None:
                    continue
                result = -1 if left_value is None else 1
                return result if key["nulls"] == "first" else -result
            if typed_deep_equal(left_value, right_value):
                continue
            result = -1 if left_value < right_value else 1  # type: ignore[operator]
            return result if key["direction"] == "asc" else -result
        return 0

    return sorted(list(items), key=cmp_to_key(compare))


def deduplicate_array(
    items: list[WorkflowValue],
    *,
    fields: object,
) -> list[WorkflowValue]:
    if not isinstance(fields, list) or len(fields) > 5:
        _fail(
            "INVALID_DEDUPLICATE_FIELDS",
            "Deduplicate fields must be an array with at most 5 top-level fields.",
        )
    normalized_fields = [str(field).strip() for field in fields]
    if any(not field or len(field) > 64 for field in normalized_fields):
        _fail(
            "INVALID_DEDUPLICATE_FIELD",
            "Deduplicate fields must be non-empty top-level field names.",
        )
    if len(normalized_fields) != len(set(normalized_fields)):
        _fail("DUPLICATE_DEDUPLICATE_FIELD", "Deduplicate fields must be unique.")
    seen: set[tuple[Any, ...]] = set()
    output: list[WorkflowValue] = []
    for item in items:
        if normalized_fields:
            if not isinstance(item, dict):
                _fail(
                    "DEDUPLICATE_FIELD_REQUIRES_OBJECTS",
                    "Field-based deduplication requires an object array.",
                )
            identity = (
                "fields",
                tuple(
                    (field, typed_identity(item.get(field)))
                    for field in normalized_fields
                ),
            )
        else:
            identity = typed_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        output.append(item)
    return output


def execute_list_operation(
    value: object,
    *,
    operator: str,
    join_separator: str = "",
    filter_rules: object = None,
    filter_mode: str = "all",
    sort_keys: object = None,
    deduplicate_fields: object = None,
    count: object = None,
    start_index: object = None,
    end_index: object = None,
) -> WorkflowValue:
    normalized = normalize_workflow_value(value, path="$.list_input")
    typed_input = isinstance(normalized, list)
    if operator in {"length", "join", "first", "last"}:
        if isinstance(normalized, list):
            items = normalized
        elif isinstance(normalized, str):
            items = (
                [item.strip() for item in normalized.split(",") if item.strip()]
                if normalized.strip()
                else []
            )
        elif normalized is None:
            items = []
            typed_input = True
        else:
            _fail(
                "LIST_INPUT_TYPE_MISMATCH",
                "Legacy list operations require an array or comma-separated text.",
            )
        if len(items) > MAX_COLLECTION_ITEMS:
            _fail(
                "LIST_ITEM_LIMIT_EXCEEDED",
                f"List operation accepts at most {MAX_COLLECTION_ITEMS} items.",
            )
        if operator == "length":
            return len(items) if typed_input else str(len(items))
        if operator == "join":
            from .values import workflow_value_to_text

            return join_separator.join(workflow_value_to_text(item) for item in items)
        if operator == "first":
            return items[0] if items else (None if typed_input else "")
        return items[-1] if items else (None if typed_input else "")

    if not isinstance(normalized, list):
        _fail(
            "LIST_TYPED_ARRAY_REQUIRED",
            "Filter, sort, deduplicate, take, skip, and slice require a JSON array input.",
        )
    if len(normalized) > MAX_COLLECTION_ITEMS:
        _fail(
            "LIST_ITEM_LIMIT_EXCEEDED",
            f"List operation accepts at most {MAX_COLLECTION_ITEMS} items.",
        )
    if operator == "filter":
        return filter_array(normalized, rules=filter_rules, mode=filter_mode)
    if operator == "sort":
        return sort_array(normalized, keys=sort_keys)
    if operator == "deduplicate":
        return deduplicate_array(
            normalized,
            fields=deduplicate_fields if deduplicate_fields is not None else [],
        )
    if operator in {"take", "skip"}:
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_COLLECTION_ITEMS:
            _fail(
                "INVALID_LIST_COUNT",
                "Take and skip count must be a whole number between 0 and 10,000.",
            )
        return list(normalized[:count] if operator == "take" else normalized[count:])
    if operator == "slice":
        if (
            not isinstance(start_index, int)
            or isinstance(start_index, bool)
            or not 0 <= start_index <= MAX_COLLECTION_ITEMS
        ):
            _fail(
                "INVALID_LIST_SLICE_START",
                "Slice start index must be a whole number between 0 and 10,000.",
            )
        if end_index is None:
            return list(normalized[start_index:])
        if (
            not isinstance(end_index, int)
            or isinstance(end_index, bool)
            or not start_index <= end_index <= MAX_COLLECTION_ITEMS
        ):
            _fail(
                "INVALID_LIST_SLICE_END",
                "Slice end index must be a whole number from the start index through 10,000.",
            )
        return list(normalized[start_index:end_index])
    _fail("INVALID_LIST_OPERATOR", "List operation uses an unsupported operator.")


def validate_aggregate_config(
    *,
    group_by_fields: object,
    measures: object,
) -> tuple[list[str], list[dict[str, str]]]:
    if not isinstance(group_by_fields, list) or len(group_by_fields) > 3:
        _fail("INVALID_GROUP_FIELD_COUNT", "Data aggregate accepts at most 3 group fields.")
    groups = [str(field).strip() for field in group_by_fields]
    if any(not field or len(field) > 64 for field in groups):
        _fail("INVALID_GROUP_FIELD", "Group fields must be non-empty top-level field names.")
    if len(groups) != len(set(groups)):
        _fail("DUPLICATE_GROUP_FIELD", "Group fields must be unique.")
    if not isinstance(measures, list) or not 1 <= len(measures) <= 10:
        _fail("INVALID_MEASURE_COUNT", "Data aggregate requires between 1 and 10 measures.")
    normalized: list[dict[str, str]] = []
    output_fields: set[str] = set()
    for raw_measure in measures:
        if not isinstance(raw_measure, dict):
            _fail("INVALID_MEASURE", "Aggregate measure must be an object.")
        output_field = str(raw_measure.get("outputField") or "").strip()
        operation = str(raw_measure.get("operation") or "").strip()
        source_field = str(raw_measure.get("sourceField") or "").strip()
        if not VARIABLE_NAME_PATTERN.fullmatch(output_field):
            _fail("INVALID_MEASURE_OUTPUT_FIELD", "Measure outputField must be an identifier.")
        if output_field in output_fields:
            _fail("DUPLICATE_MEASURE_OUTPUT_FIELD", "Measure output fields must be unique.")
        if output_field in groups:
            _fail(
                "MEASURE_GROUP_FIELD_CONFLICT",
                "Measure output fields cannot conflict with group fields.",
            )
        if operation not in AGGREGATE_OPERATIONS:
            _fail("INVALID_MEASURE_OPERATION", "Aggregate measure uses an unsupported operation.")
        if operation != "count" and (not source_field or len(source_field) > 64):
            _fail(
                "MISSING_MEASURE_SOURCE_FIELD",
                "Numeric aggregate measures require a top-level sourceField.",
            )
        if operation == "count" and source_field:
            _fail("COUNT_MEASURE_SOURCE_FORBIDDEN", "Count measure does not accept sourceField.")
        output_fields.add(output_field)
        normalized.append(
            {
                "outputField": output_field,
                "operation": operation,
                "sourceField": source_field,
            }
        )
    return groups, normalized


def aggregate_rows(
    value: object,
    *,
    group_by_fields: object,
    measures: object,
    max_output_bytes: int = MAX_DATA_AGGREGATE_OUTPUT_BYTES,
) -> list[dict[str, WorkflowValue]]:
    rows = normalize_workflow_value(value, path="$.aggregate_input")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _fail(
            "AGGREGATE_OBJECT_ARRAY_REQUIRED",
            "Data aggregate input must be a JSON object array.",
        )
    if len(rows) > MAX_COLLECTION_ITEMS:
        _fail(
            "AGGREGATE_ROW_LIMIT_EXCEEDED",
            f"Data aggregate accepts at most {MAX_COLLECTION_ITEMS} rows.",
        )
    groups, normalized_measures = validate_aggregate_config(
        group_by_fields=group_by_fields,
        measures=measures,
    )
    buckets: dict[tuple[Any, ...], tuple[list[WorkflowValue], list[dict[str, WorkflowValue]]]] = {}
    if not rows and not groups:
        buckets[("all",)] = ([], [])
    for row in rows:
        group_values = [row.get(field) for field in groups]
        if any(isinstance(item, (dict, list)) for item in group_values):
            _fail(
                "AGGREGATE_GROUP_VALUE_NOT_SCALAR",
                "Group fields accept only scalar or null values.",
            )
        key = tuple(typed_identity(item) for item in group_values)
        if key not in buckets:
            buckets[key] = (group_values, [])
        buckets[key][1].append(row)

    output: list[dict[str, WorkflowValue]] = []
    for group_values, bucket_rows in buckets.values():
        result: dict[str, WorkflowValue] = {
            field: group_values[index] for index, field in enumerate(groups)
        }
        for measure in normalized_measures:
            operation = measure["operation"]
            output_field = measure["outputField"]
            if operation == "count":
                result[output_field] = len(bucket_rows)
                continue
            numeric_values: list[int | float] = []
            source_field = measure["sourceField"]
            for row in bucket_rows:
                field_value = row.get(source_field)
                if field_value is None:
                    continue
                if not is_finite_number(field_value):
                    _fail(
                        "AGGREGATE_NUMERIC_VALUE_REQUIRED",
                        "Numeric aggregate fields may contain only finite numbers or null.",
                    )
                numeric_values.append(field_value)
            if operation == "sum":
                result[output_field] = sum(numeric_values)
            elif operation == "avg":
                result[output_field] = (
                    sum(numeric_values) / len(numeric_values)
                    if numeric_values
                    else None
                )
            elif operation == "min":
                result[output_field] = min(numeric_values) if numeric_values else None
            else:
                result[output_field] = max(numeric_values) if numeric_values else None
        output.append(result)
    encoded = json.dumps(
        output,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > max(1, int(max_output_bytes)):
        _fail(
            "AGGREGATE_OUTPUT_LIMIT_EXCEEDED",
            "Data aggregate output exceeds the workflow persistence limit.",
        )
    return output


def validate_terminate_error_config(error_code: object, message: object) -> tuple[str, str]:
    code = str(error_code or "").strip()
    safe_message = str(message or "").strip()
    if not SAFE_ERROR_CODE_PATTERN.fullmatch(code):
        _fail(
            "INVALID_TERMINATE_ERROR_CODE",
            "Terminate errorCode must use uppercase letters, numbers, and underscores.",
        )
    if not 1 <= len(safe_message) <= 2_000:
        _fail(
            "INVALID_TERMINATE_ERROR_MESSAGE",
            "Terminate message must contain 1 to 2000 characters.",
        )
    if "{{" in safe_message or "}}" in safe_message:
        _fail(
            "TERMINATE_ERROR_TEMPLATE_FORBIDDEN",
            "Terminate message must be fixed text and cannot reference variables.",
        )
    if SENSITIVE_ERROR_MESSAGE_PATTERN.search(safe_message):
        _fail(
            "TERMINATE_ERROR_SENSITIVE_MESSAGE",
            "Terminate message resembles a credential and cannot be persisted.",
        )
    return code, safe_message
