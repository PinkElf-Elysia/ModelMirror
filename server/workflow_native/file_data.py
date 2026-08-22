from __future__ import annotations

import calendar
import copy
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .values import WorkflowValue, normalize_workflow_value, workflow_value_to_text


MAX_COLLECTION_ITEMS = 10_000
MAX_OBJECT_OPERATIONS = 20
MAX_FILE_COLUMNS = 200
_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_OBJECT_OPERATION_KINDS = {"set", "set_default", "rename", "remove", "keep_only"}
_FILE_FORMAT_SUFFIXES = {
    "plain_text": ".txt",
    "markdown": ".md",
    "json": ".json",
    "csv": ".csv",
    "pdf": ".pdf",
    "docx": ".docx",
    "xlsx": ".xlsx",
}
_TIME_V2_OPERATIONS = {
    "now",
    "to_iso",
    "format",
    "add",
    "subtract",
    "difference",
    "start_of",
    "end_of",
}
_TIME_AMOUNT_UNITS = {"seconds", "minutes", "hours", "days", "weeks", "months", "years"}
_TIME_DIFFERENCE_UNITS = {"seconds", "minutes", "hours", "days"}
_TIME_BOUNDARY_UNITS = {"minute", "hour", "day", "week", "month", "year"}


class WorkflowFileDataError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


def _fail(code: str, message: str) -> None:
    raise WorkflowFileDataError(code, message)


def _variable_name(value: object, *, code: str, label: str) -> str:
    clean = str(value or "").strip()
    if not _VARIABLE_PATTERN.fullmatch(clean):
        _fail(code, f"{label} must be a valid workflow variable name.")
    return clean


def _field_name(value: object, *, code: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 128 or any(character in clean for character in "\r\n\x00"):
        _fail(code, f"{label} must be a non-empty top-level field name of at most 128 characters.")
    return clean


def _typed_literal(binding: dict[str, Any]) -> WorkflowValue:
    value_type = str(binding.get("valueType") or "").strip()
    if value_type not in {"text", "number", "boolean", "null", "json"}:
        _fail("OBJECT_BINDING_TYPE_INVALID", "Object transform literal type is invalid.")
    if value_type == "null":
        return None
    if "value" not in binding:
        _fail("OBJECT_BINDING_VALUE_MISSING", "Object transform literal value is missing.")
    value = normalize_workflow_value(binding.get("value"), path="$.objectTransform.value")
    valid = {
        "text": isinstance(value, str),
        "number": isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        "boolean": isinstance(value, bool),
        "json": True,
    }[value_type]
    if not valid:
        _fail("OBJECT_BINDING_TYPE_MISMATCH", "Object transform literal does not match its selected type.")
    return value


def validate_object_transform_config(config: object) -> list[dict[str, Any]]:
    if not isinstance(config, dict):
        _fail("OBJECT_TRANSFORM_CONFIG_INVALID", "Object transform configuration must be an object.")
    _variable_name(
        config.get("inputVariable"),
        code="OBJECT_INPUT_VARIABLE_INVALID",
        label="Object transform inputVariable",
    )
    _variable_name(
        config.get("outputVariable"),
        code="OBJECT_OUTPUT_VARIABLE_INVALID",
        label="Object transform outputVariable",
    )
    operations = config.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OBJECT_OPERATIONS:
        _fail("OBJECT_OPERATION_COUNT_INVALID", "Object transform requires between 1 and 20 operations.")
    normalized: list[dict[str, Any]] = []
    operation_ids: set[str] = set()
    for raw in operations:
        if not isinstance(raw, dict):
            _fail("OBJECT_OPERATION_INVALID", "Each object transform operation must be an object.")
        operation_id = str(raw.get("id") or "").strip()
        if not _OPERATION_ID_PATTERN.fullmatch(operation_id):
            _fail("OBJECT_OPERATION_ID_INVALID", "Object transform operation id is invalid.")
        if operation_id in operation_ids:
            _fail("OBJECT_OPERATION_ID_DUPLICATE", "Object transform operation ids must be unique.")
        operation_ids.add(operation_id)
        kind = str(raw.get("operation") or "").strip()
        if kind not in _OBJECT_OPERATION_KINDS:
            _fail("OBJECT_OPERATION_KIND_INVALID", "Object transform operation is unsupported.")
        operation = {"id": operation_id, "operation": kind}
        if kind in {"set", "set_default"}:
            operation["targetField"] = _field_name(
                raw.get("targetField"), code="OBJECT_TARGET_FIELD_INVALID", label="Target field"
            )
            binding = raw.get("binding")
            if not isinstance(binding, dict):
                _fail("OBJECT_BINDING_INVALID", "Set operations require a typed binding.")
            source = str(binding.get("source") or "").strip()
            if source == "variable":
                operation["binding"] = {
                    "source": "variable",
                    "variable": _variable_name(
                        binding.get("variable"),
                        code="OBJECT_BINDING_VARIABLE_INVALID",
                        label="Object transform binding variable",
                    ),
                }
            elif source == "literal":
                operation["binding"] = {
                    "source": "literal",
                    "value": _typed_literal(binding),
                }
            else:
                _fail("OBJECT_BINDING_SOURCE_INVALID", "Object transform binding source must be literal or variable.")
        elif kind == "rename":
            operation["sourceField"] = _field_name(
                raw.get("sourceField"), code="OBJECT_SOURCE_FIELD_INVALID", label="Source field"
            )
            operation["targetField"] = _field_name(
                raw.get("targetField"), code="OBJECT_TARGET_FIELD_INVALID", label="Target field"
            )
        elif kind == "remove":
            operation["targetField"] = _field_name(
                raw.get("targetField"), code="OBJECT_TARGET_FIELD_INVALID", label="Field"
            )
        else:
            fields = raw.get("fields")
            if not isinstance(fields, list) or not 1 <= len(fields) <= 50:
                _fail("OBJECT_KEEP_FIELDS_INVALID", "Keep-only requires between 1 and 50 fields.")
            normalized_fields = [
                _field_name(field, code="OBJECT_KEEP_FIELD_INVALID", label="Keep-only field")
                for field in fields
            ]
            if len(normalized_fields) != len(set(normalized_fields)):
                _fail("OBJECT_KEEP_FIELD_DUPLICATE", "Keep-only fields must be unique.")
            operation["fields"] = normalized_fields
        normalized.append(operation)
    return normalized


def object_transform_variable_references(config: object) -> set[str]:
    operations = validate_object_transform_config(config)
    references = {str(config["inputVariable"])}  # type: ignore[index]
    references.update(
        str(operation["binding"]["variable"])
        for operation in operations
        if operation.get("binding", {}).get("source") == "variable"
    )
    return references


def execute_object_transform(
    value: object,
    *,
    config: object,
    variables: dict[str, WorkflowValue],
) -> dict[str, WorkflowValue]:
    operations = validate_object_transform_config(config)
    normalized = normalize_workflow_value(value, path="$.objectTransform.input")
    if not isinstance(normalized, dict):
        _fail("OBJECT_INPUT_TYPE_MISMATCH", "Object transform input must be a JSON object.")
    result: dict[str, WorkflowValue] = copy.deepcopy(normalized)
    for operation in operations:
        kind = str(operation["operation"])
        if kind in {"set", "set_default"}:
            target = str(operation["targetField"])
            if kind == "set_default" and target in result:
                continue
            binding = operation["binding"]
            if binding["source"] == "variable":
                variable = str(binding["variable"])
                if variable not in variables:
                    _fail("OBJECT_BINDING_VARIABLE_UNAVAILABLE", "Object transform binding variable is unavailable.")
                replacement = normalize_workflow_value(
                    variables[variable], path=f"$.objectTransform.variables.{variable}"
                )
            else:
                replacement = binding["value"]
            result[target] = copy.deepcopy(replacement)
            continue
        if kind == "rename":
            source = str(operation["sourceField"])
            target = str(operation["targetField"])
            if source not in result:
                _fail("OBJECT_SOURCE_FIELD_MISSING", "Object transform source field is missing.")
            if target != source and target in result:
                _fail("OBJECT_RENAME_CONFLICT", "Object transform rename target already exists.")
            if target != source:
                result[target] = result.pop(source)
            continue
        if kind == "remove":
            target = str(operation["targetField"])
            if target not in result:
                _fail("OBJECT_REMOVE_FIELD_MISSING", "Object transform remove field is missing.")
            del result[target]
            continue
        requested = list(operation["fields"])
        missing = [field for field in requested if field not in result]
        if missing:
            _fail("OBJECT_KEEP_FIELD_MISSING", "Object transform keep-only field is missing.")
        result = {field: result[field] for field in requested}
    return result


def is_time_v2(config: object) -> bool:
    return isinstance(config, dict) and config.get("contractVersion") == 2


def validate_time_v2_config(config: object) -> dict[str, Any]:
    if not isinstance(config, dict) or config.get("contractVersion") != 2:
        _fail("TIME_V2_CONFIG_INVALID", "Time V2 configuration is required.")
    operation = str(config.get("operation") or "").strip()
    if operation not in _TIME_V2_OPERATIONS:
        _fail("TIME_OPERATION_INVALID", "Time operation is unsupported.")
    output_variable = _variable_name(
        config.get("outputVariable"), code="TIME_OUTPUT_VARIABLE_INVALID", label="Time outputVariable"
    )
    timezone_name = str(config.get("timezone") or "UTC").strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        _fail("TIME_TIMEZONE_INVALID", "Time timezone must be a valid IANA timezone.")
    normalized: dict[str, Any] = {
        "contractVersion": 2,
        "operation": operation,
        "outputVariable": output_variable,
        "timezone": timezone_name,
    }
    if operation != "now":
        normalized["inputVariable"] = _variable_name(
            config.get("inputVariable"), code="TIME_INPUT_VARIABLE_INVALID", label="Time inputVariable"
        )
    if operation == "difference":
        normalized["rightVariable"] = _variable_name(
            config.get("rightVariable"), code="TIME_RIGHT_VARIABLE_INVALID", label="Time rightVariable"
        )
    if operation in {"add", "subtract"}:
        amount = config.get("amount")
        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not math.isfinite(float(amount))
            or abs(float(amount)) > 1_000_000
        ):
            _fail("TIME_AMOUNT_INVALID", "Time amount must be a finite number within the safe limit.")
        unit = str(config.get("unit") or "").strip()
        if unit not in _TIME_AMOUNT_UNITS:
            _fail("TIME_UNIT_INVALID", "Time amount unit is unsupported.")
        if unit in {"months", "years"} and float(amount) != int(amount):
            _fail("TIME_CALENDAR_AMOUNT_INVALID", "Month and year amounts must be whole numbers.")
        normalized.update({"amount": amount, "unit": unit})
    elif operation == "difference":
        unit = str(config.get("unit") or "").strip()
        if unit not in _TIME_DIFFERENCE_UNITS:
            _fail("TIME_DIFFERENCE_UNIT_INVALID", "Time difference unit is unsupported.")
        normalized["unit"] = unit
    elif operation in {"start_of", "end_of"}:
        unit = str(config.get("unit") or "").strip()
        if unit not in _TIME_BOUNDARY_UNITS:
            _fail("TIME_BOUNDARY_UNIT_INVALID", "Time boundary unit is unsupported.")
        normalized["unit"] = unit
    elif operation == "format":
        format_string = str(config.get("formatString") or "").strip()
        if not 1 <= len(format_string) <= 200:
            _fail("TIME_FORMAT_INVALID", "Time format must contain 1 to 200 characters.")
        normalized["formatString"] = format_string
    return normalized


def time_v2_variable_references(config: object) -> set[str]:
    normalized = validate_time_v2_config(config)
    return {
        str(normalized[field])
        for field in ("inputVariable", "rightVariable")
        if field in normalized
    }


def _localize_strict(value: datetime, timezone: ZoneInfo) -> datetime:
    first = value.replace(tzinfo=timezone, fold=0)
    second = value.replace(tzinfo=timezone, fold=1)

    def round_trips(candidate: datetime) -> bool:
        return candidate.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) == value

    first_valid = round_trips(first)
    second_valid = round_trips(second)
    if not first_valid and not second_valid:
        _fail("TIME_DST_GAP", "The local time does not exist because of a daylight-saving transition.")
    if first_valid and second_valid and first.utcoffset() != second.utcoffset():
        _fail("TIME_DST_FOLD", "The local time is ambiguous because of a daylight-saving transition.")
    return first if first_valid else second


def _parse_time(value: object, timezone: ZoneInfo) -> datetime:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        _fail("TIME_INPUT_INVALID", "Time input must be an ISO date-time string.")
    clean = value.strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        _fail("TIME_INPUT_INVALID", "Time input must be an ISO date-time string.")
    if parsed.tzinfo is None:
        return _localize_strict(parsed, timezone)
    return parsed.astimezone(timezone)


def _calendar_shift(value: datetime, amount: int, unit: str, timezone: ZoneInfo) -> datetime:
    months = amount * (12 if unit == "years" else 1)
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    if year < 1 or year > 9999:
        _fail("TIME_RESULT_OUT_OF_RANGE", "Time result is outside the supported range.")
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    shifted = value.replace(tzinfo=None, year=year, month=month, day=day)
    return _localize_strict(shifted, timezone)


def _wall_shift(value: datetime, delta: timedelta, timezone: ZoneInfo) -> datetime:
    try:
        return _localize_strict(value.replace(tzinfo=None) + delta, timezone)
    except OverflowError:
        _fail("TIME_RESULT_OUT_OF_RANGE", "Time result is outside the supported range.")


def _start_of(value: datetime, unit: str, timezone: ZoneInfo) -> datetime:
    naive = value.replace(tzinfo=None)
    if unit == "minute":
        naive = naive.replace(second=0, microsecond=0)
    elif unit == "hour":
        naive = naive.replace(minute=0, second=0, microsecond=0)
    elif unit == "day":
        naive = naive.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "week":
        naive = (naive - timedelta(days=naive.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif unit == "month":
        naive = naive.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        naive = naive.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return _localize_strict(naive, timezone)


def _next_boundary(value: datetime, unit: str, timezone: ZoneInfo) -> datetime:
    if unit == "minute":
        return _wall_shift(value, timedelta(minutes=1), timezone)
    if unit == "hour":
        return _wall_shift(value, timedelta(hours=1), timezone)
    if unit == "day":
        return _wall_shift(value, timedelta(days=1), timezone)
    if unit == "week":
        return _wall_shift(value, timedelta(weeks=1), timezone)
    return _calendar_shift(value, 1, "years" if unit == "year" else "months", timezone)


def execute_time_v2(
    config: object,
    *,
    variables: dict[str, WorkflowValue],
    now: Callable[[], datetime] | None = None,
) -> WorkflowValue:
    normalized = validate_time_v2_config(config)
    timezone = ZoneInfo(str(normalized["timezone"]))
    operation = str(normalized["operation"])
    if operation == "now":
        current = (now or (lambda: datetime.now(UTC)))()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current.astimezone(timezone).isoformat()
    input_variable = str(normalized["inputVariable"])
    if input_variable not in variables:
        _fail("TIME_INPUT_VARIABLE_UNAVAILABLE", "Time input variable is unavailable.")
    left = _parse_time(variables[input_variable], timezone)
    if operation == "to_iso":
        return left.isoformat()
    if operation == "format":
        return left.strftime(str(normalized["formatString"]))
    if operation == "difference":
        right_variable = str(normalized["rightVariable"])
        if right_variable not in variables:
            _fail("TIME_RIGHT_VARIABLE_UNAVAILABLE", "Time comparison variable is unavailable.")
        right = _parse_time(variables[right_variable], timezone)
        seconds = (left.astimezone(UTC) - right.astimezone(UTC)).total_seconds()
        divisor = {"seconds": 1, "minutes": 60, "hours": 3_600, "days": 86_400}[
            str(normalized["unit"])
        ]
        return seconds / divisor
    if operation in {"start_of", "end_of"}:
        start = _start_of(left, str(normalized["unit"]), timezone)
        if operation == "start_of":
            return start.isoformat()
        return (_next_boundary(start, str(normalized["unit"]), timezone) - timedelta(microseconds=1)).isoformat()
    amount = float(normalized["amount"])
    if operation == "subtract":
        amount = -amount
    unit = str(normalized["unit"])
    if unit in {"months", "years"}:
        result = _calendar_shift(left, int(amount), unit, timezone)
    else:
        seconds = amount * {
            "seconds": 1,
            "minutes": 60,
            "hours": 3_600,
            "days": 86_400,
            "weeks": 604_800,
        }[unit]
        result = _wall_shift(left, timedelta(seconds=seconds), timezone)
    return result.isoformat()


def validate_file_output_config(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        _fail("FILE_OUTPUT_CONFIG_INVALID", "File output configuration must be an object.")
    input_variable = _variable_name(
        config.get("inputVariable"), code="FILE_OUTPUT_INPUT_VARIABLE_INVALID", label="File output inputVariable"
    )
    output_variable = _variable_name(
        config.get("outputVariable"), code="FILE_OUTPUT_OUTPUT_VARIABLE_INVALID", label="File output outputVariable"
    )
    format_id = str(config.get("format") or "").strip()
    if format_id not in _FILE_FORMAT_SUFFIXES:
        _fail("FILE_OUTPUT_FORMAT_INVALID", "File output format is unsupported.")
    filename_template = str(config.get("filenameTemplate") or "").strip()
    if not 1 <= len(filename_template) <= 150:
        _fail("FILE_OUTPUT_FILENAME_INVALID", "File output filename template must contain 1 to 150 characters.")
    title_template = str(config.get("titleTemplate") or "").strip()
    if len(title_template) > 500:
        _fail("FILE_OUTPUT_TITLE_INVALID", "File output title template is too long.")
    columns: list[dict[str, str]] = []
    if format_id in {"csv", "xlsx"}:
        raw_columns = config.get("columns")
        if not isinstance(raw_columns, list) or not 1 <= len(raw_columns) <= MAX_FILE_COLUMNS:
            _fail("FILE_OUTPUT_COLUMNS_INVALID", "CSV and XLSX output require between 1 and 200 columns.")
        ids: set[str] = set()
        fields: set[str] = set()
        for raw in raw_columns:
            if not isinstance(raw, dict):
                _fail("FILE_OUTPUT_COLUMN_INVALID", "Each file output column must be an object.")
            column_id = str(raw.get("id") or "").strip()
            if not _OPERATION_ID_PATTERN.fullmatch(column_id) or column_id in ids:
                _fail("FILE_OUTPUT_COLUMN_ID_INVALID", "File output column ids must be valid and unique.")
            field = _field_name(
                raw.get("field"), code="FILE_OUTPUT_COLUMN_FIELD_INVALID", label="Column field"
            )
            if field in fields:
                _fail("FILE_OUTPUT_COLUMN_FIELD_DUPLICATE", "File output column fields must be unique.")
            label = str(raw.get("label") or field).strip()
            if not 1 <= len(label) <= 128 or any(character in label for character in "\r\n\x00"):
                _fail("FILE_OUTPUT_COLUMN_LABEL_INVALID", "File output column label is invalid.")
            ids.add(column_id)
            fields.add(field)
            columns.append({"id": column_id, "field": field, "label": label})
    return {
        "inputVariable": input_variable,
        "outputVariable": output_variable,
        "format": format_id,
        "filenameTemplate": filename_template,
        "titleTemplate": title_template,
        "columns": columns,
    }


def _safe_filename(rendered: str, format_id: str) -> str:
    clean = rendered.strip()
    if (
        not clean
        or "/" in clean
        or "\\" in clean
        or re.match(r"^[A-Za-z]:", clean)
        or Path(clean).name != clean
        or clean in {".", ".."}
        or any(character in clean for character in "\r\n\x00")
    ):
        _fail("FILE_OUTPUT_FILENAME_UNSAFE", "Rendered filename must be a safe file name without a path.")
    suffix = _FILE_FORMAT_SUFFIXES[format_id]
    if not clean.casefold().endswith(suffix):
        clean += suffix
    if len(clean) > 160:
        _fail("FILE_OUTPUT_FILENAME_TOO_LONG", "Rendered filename exceeds 160 characters.")
    return clean


def _tabular_rows(value: WorkflowValue, columns: list[dict[str, str]]) -> list[list[Any]]:
    if not isinstance(value, list) or len(value) > MAX_COLLECTION_ITEMS:
        _fail("FILE_OUTPUT_TABLE_INPUT_INVALID", "CSV and XLSX output require at most 10,000 object rows.")
    rows: list[list[Any]] = [[column["label"] for column in columns]]
    for item in value:
        if not isinstance(item, dict):
            _fail("FILE_OUTPUT_TABLE_ROW_INVALID", "CSV and XLSX output rows must be JSON objects.")
        row: list[Any] = []
        for column in columns:
            cell = item.get(column["field"])
            if not isinstance(cell, (str, int, float, bool)) and cell is not None:
                _fail("FILE_OUTPUT_TABLE_CELL_INVALID", "CSV and XLSX cells must be scalar values or null.")
            if isinstance(cell, float) and not math.isfinite(cell):
                _fail("FILE_OUTPUT_TABLE_CELL_INVALID", "CSV and XLSX cells must contain finite numbers.")
            row.append(cell)
        rows.append(row)
    return rows


def build_file_output_render_spec(
    config: object,
    *,
    value: object,
    rendered_filename: str,
    rendered_title: str = "",
) -> dict[str, Any]:
    normalized_config = validate_file_output_config(config)
    normalized_value = normalize_workflow_value(value, path="$.fileOutput.input")
    format_id = str(normalized_config["format"])
    filename = _safe_filename(rendered_filename, format_id)
    if len(rendered_title) > 2_000:
        _fail("FILE_OUTPUT_TITLE_TOO_LONG", "Rendered file title exceeds 2,000 characters.")
    base: dict[str, Any] = {"format_id": format_id, "filename": filename}
    if format_id in {"plain_text", "markdown"}:
        base["content"] = workflow_value_to_text(normalized_value)
    elif format_id == "json":
        base["content"] = normalized_value
    elif format_id == "csv":
        base["rows"] = _tabular_rows(normalized_value, normalized_config["columns"])
    elif format_id in {"pdf", "docx"}:
        blocks: list[dict[str, Any]] = []
        if rendered_title.strip():
            blocks.append({"kind": "heading", "text": rendered_title.strip(), "level": 1})
        blocks.append({"kind": "paragraph", "text": workflow_value_to_text(normalized_value)})
        base.update({"title": rendered_title.strip(), "blocks": blocks})
    else:
        base["sheets"] = [
            {
                "name": "Data",
                "rows": _tabular_rows(normalized_value, normalized_config["columns"]),
            }
        ]
    return base


def safe_file_output_variable(payload: dict[str, Any]) -> dict[str, WorkflowValue]:
    return {
        "outputId": str(payload.get("output_id") or ""),
        "assetId": str(payload.get("asset_id") or "") or None,
        "displayName": str(payload.get("display_name") or ""),
        "format": str(payload.get("format") or ""),
        "mediaType": str(payload.get("media_type") or ""),
        "byteSize": int(payload.get("byte_size") or 0),
        "status": str(payload.get("status") or "failed"),
        "expiresAt": str(payload.get("expires_at") or "") or None,
        "warnings": [str(item) for item in list(payload.get("warnings") or [])[:20]],
    }


__all__ = [
    "WorkflowFileDataError",
    "build_file_output_render_spec",
    "execute_object_transform",
    "execute_time_v2",
    "is_time_v2",
    "object_transform_variable_references",
    "safe_file_output_variable",
    "time_v2_variable_references",
    "validate_file_output_config",
    "validate_object_transform_config",
    "validate_time_v2_config",
]
