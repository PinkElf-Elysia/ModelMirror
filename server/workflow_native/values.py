from __future__ import annotations

import json
import math
from typing import Any

from pydantic import JsonValue


WorkflowValue = JsonValue


def normalize_workflow_value(
    value: Any,
    *,
    path: str = "$",
    depth: int = 0,
) -> WorkflowValue:
    """Return a detached JSON-safe workflow value or raise a precise error."""

    if depth > 64:
        raise ValueError(f"Workflow value at {path} exceeds the maximum nesting depth.")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Workflow value at {path} must be a finite number.")
        return value
    if isinstance(value, list):
        return [
            normalize_workflow_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, WorkflowValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Workflow object key at {path} must be a string.")
            normalized[key] = normalize_workflow_value(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized
    raise ValueError(
        f"Workflow value at {path} has unsupported type {type(value).__name__}."
    )


def normalize_workflow_variables(values: dict[str, Any]) -> dict[str, WorkflowValue]:
    return {
        str(key): normalize_workflow_value(value, path=f"$.{key}")
        for key, value in values.items()
    }


def workflow_value_to_text(value: Any) -> str:
    """Render a typed value for prompts and legacy text-only node contracts."""

    normalized = normalize_workflow_value(value)
    if isinstance(normalized, str):
        return normalized
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def serialize_workflow_value(value: Any, *, pretty: bool = False) -> str:
    normalized = normalize_workflow_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def deserialize_workflow_value(value: str) -> WorkflowValue:
    if not isinstance(value, str):
        raise ValueError("JSON deserialize input must be a string.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON deserialize input is invalid: {exc.msg}.") from exc
    return normalize_workflow_value(parsed)


def workflow_list_items(value: Any) -> tuple[list[WorkflowValue], bool]:
    """Return list items and whether they originated from a typed array."""

    normalized = normalize_workflow_value(value)
    if isinstance(normalized, list):
        return normalized, True
    if isinstance(normalized, str):
        if not normalized.strip():
            return [], False
        return [
            item.strip()
            for item in normalized.split(",")
            if item.strip()
        ], False
    if normalized is None:
        return [], True
    raise ValueError("List operation input must be an array or comma-separated string.")


def workflow_condition_matches(actual: Any, operator: str, expected_text: str) -> bool:
    normalized = normalize_workflow_value(actual)
    if isinstance(normalized, str):
        return (
            normalized == expected_text
            if operator == "equals"
            else expected_text in normalized
        )

    try:
        expected: WorkflowValue = normalize_workflow_value(json.loads(expected_text))
    except (json.JSONDecodeError, ValueError):
        expected = expected_text

    if operator == "equals":
        return normalized == expected
    if isinstance(normalized, list):
        return expected in normalized
    if isinstance(normalized, dict):
        return expected_text in normalized
    return expected_text in workflow_value_to_text(normalized)
