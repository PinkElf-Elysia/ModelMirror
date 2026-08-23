from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any

from jsonschema import Draft202012Validator

from .node_contracts import canonical_checksum
from .values import WorkflowValue, normalize_workflow_value


VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SCHEMA_CHECKSUM_PATTERN = re.compile(r"^[a-f0-9]{64}$")
MAX_WORKFLOW_NODE_OUTPUT_BYTES = 5 * 1_024 * 1_024
MAX_MCP_BINDINGS = 100


class WorkflowR20NodeError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


def contract_version(data: dict[str, Any]) -> int:
    try:
        return int(data.get("contractVersion") or 1)
    except (TypeError, ValueError):
        return 0


def is_r20_v2(data: dict[str, Any]) -> bool:
    return contract_version(data) == 2


def workflow_mcp_tools_enabled() -> bool:
    return os.getenv("WORKFLOW_MCP_TOOLS_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def validate_human_intervention_v2_config(data: dict[str, Any]) -> None:
    if contract_version(data) != 2:
        _fail("HUMAN_INTERVENTION_CONTRACT_VERSION_INVALID", "Human intervention contractVersion must be 2.")
    mode = str(data.get("interactionMode") or "").strip()
    if mode not in {"input", "approval"}:
        _fail("HUMAN_INTERVENTION_MODE_INVALID", "Human intervention mode must be input or approval.")
    prompt = str(data.get("prompt") or "")
    if not prompt.strip() or len(prompt) > 4_000:
        _fail("HUMAN_INTERVENTION_PROMPT_INVALID", "Human intervention prompt must contain 1 to 4000 characters.")
    _variable_name(data.get("outputVariable"), "HUMAN_INTERVENTION_OUTPUT_VARIABLE_INVALID", "Human intervention outputVariable")
    timeout = _integer(data.get("timeoutSeconds"), "HUMAN_INTERVENTION_TIMEOUT_INVALID")
    if timeout < 30 or timeout > 86_400:
        _fail("HUMAN_INTERVENTION_TIMEOUT_INVALID", "Human intervention timeoutSeconds must be between 30 and 86400.")


def validate_variable_assign_v2_config(data: dict[str, Any]) -> None:
    if contract_version(data) != 2:
        _fail("VARIABLE_ASSIGN_CONTRACT_VERSION_INVALID", "Variable assignment contractVersion must be 2.")
    _variable_name(data.get("outputVariable"), "VARIABLE_ASSIGN_OUTPUT_VARIABLE_INVALID", "Variable assignment outputVariable")
    source = str(data.get("valueSource") or "").strip()
    if source not in {"literal", "variable", "template"}:
        _fail("VARIABLE_ASSIGN_SOURCE_INVALID", "Variable assignment valueSource must be literal, variable, or template.")
    if source == "literal":
        if "literalValue" not in data:
            _fail("VARIABLE_ASSIGN_LITERAL_MISSING", "Variable assignment literalValue is required.")
        value = normalize_workflow_value(data.get("literalValue"), path="$.variableAssign.literalValue")
        _ensure_output_size(value, code="VARIABLE_ASSIGN_OUTPUT_TOO_LARGE")
    elif source == "variable":
        _variable_name(data.get("sourceVariable"), "VARIABLE_ASSIGN_SOURCE_VARIABLE_INVALID", "Variable assignment sourceVariable")
    else:
        if "template" not in data or not isinstance(data.get("template"), str):
            _fail("VARIABLE_ASSIGN_TEMPLATE_MISSING", "Variable assignment template is required.")
        template = str(data["template"])
        if len(template) > 100_000:
            _fail("VARIABLE_ASSIGN_TEMPLATE_TOO_LARGE", "Variable assignment template is too large.")


def execute_variable_assign_v2(
    data: dict[str, Any],
    variables: dict[str, WorkflowValue],
    *,
    render_template: Any,
) -> tuple[str, WorkflowValue]:
    validate_variable_assign_v2_config(data)
    output_variable = str(data["outputVariable"]).strip()
    source = str(data["valueSource"]).strip()
    if source == "literal":
        value = normalize_workflow_value(data.get("literalValue"), path=f"$.variables.{output_variable}")
    elif source == "variable":
        source_variable = str(data["sourceVariable"]).strip()
        if source_variable not in variables:
            _fail("VARIABLE_ASSIGN_SOURCE_UNAVAILABLE", "Variable assignment source variable is unavailable.")
        value = normalize_workflow_value(variables[source_variable], path=f"$.variables.{output_variable}")
    else:
        template = str(data.get("template") or "")
        references = {
            match.group(1).strip()
            for match in re.finditer(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", template)
        }
        if references - set(variables):
            _fail("VARIABLE_ASSIGN_TEMPLATE_VARIABLE_UNAVAILABLE", "Variable assignment template references an unavailable variable.")
        value = str(render_template(template, variables))
        if "{{" in value or "}}" in value:
            _fail(
                "VARIABLE_ASSIGN_TEMPLATE_INVALID",
                "Variable assignment template contains an unsupported or unresolved reference.",
            )
    _ensure_output_size(value, code="VARIABLE_ASSIGN_OUTPUT_TOO_LARGE")
    return output_variable, value


def mcp_schema_checksum(schema: Any) -> str:
    if not isinstance(schema, dict):
        _fail("MCP_TOOL_SCHEMA_INVALID", "MCP tool input schema must be an object.")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise WorkflowR20NodeError("MCP_TOOL_SCHEMA_INVALID", "MCP tool input schema is invalid.") from exc
    return canonical_checksum(schema)


def validate_mcp_tool_v2_config(
    data: dict[str, Any],
    *,
    input_schema: dict[str, Any] | None = None,
) -> None:
    if contract_version(data) != 2:
        _fail("MCP_TOOL_CONTRACT_VERSION_INVALID", "MCP tool contractVersion must be 2.")
    server_id = str(data.get("serverId") or "").strip()
    tool_name = str(data.get("toolName") or "").strip()
    checksum = str(data.get("inputSchemaChecksum") or "").strip().lower()
    if not server_id or len(server_id) > 300:
        _fail("MCP_TOOL_SERVER_ID_INVALID", "MCP tool serverId is required and must not exceed 300 characters.")
    if not tool_name or len(tool_name) > 160:
        _fail("MCP_TOOL_NAME_INVALID", "MCP tool toolName is required and must not exceed 160 characters.")
    if not SCHEMA_CHECKSUM_PATTERN.fullmatch(checksum):
        _fail("MCP_TOOL_SCHEMA_CHECKSUM_INVALID", "MCP tool inputSchemaChecksum must be a SHA-256 checksum.")
    _variable_name(data.get("outputVariable"), "MCP_TOOL_OUTPUT_VARIABLE_INVALID", "MCP tool outputVariable")
    mode = str(data.get("argumentMode") or "").strip()
    if mode not in {"fields", "object_variable"}:
        _fail("MCP_TOOL_ARGUMENT_MODE_INVALID", "MCP tool argumentMode must be fields or object_variable.")
    if mode == "object_variable":
        _variable_name(data.get("argumentsVariable"), "MCP_TOOL_ARGUMENTS_VARIABLE_INVALID", "MCP tool argumentsVariable")
    else:
        bindings = data.get("argumentBindings")
        if not isinstance(bindings, list) or len(bindings) > MAX_MCP_BINDINGS:
            _fail("MCP_TOOL_BINDINGS_INVALID", "MCP tool argumentBindings must be an array with at most 100 items.")
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for item in bindings:
            if not isinstance(item, dict):
                _fail("MCP_TOOL_BINDING_INVALID", "Each MCP tool argument binding must be an object.")
            item_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not item_id or len(item_id) > 64 or item_id in seen_ids:
                _fail("MCP_TOOL_BINDING_ID_INVALID", "MCP tool argument binding IDs must be unique and no longer than 64 characters.")
            if not name or len(name) > 160 or name in seen_names:
                _fail("MCP_TOOL_BINDING_NAME_INVALID", "MCP tool argument names must be unique and no longer than 160 characters.")
            seen_ids.add(item_id)
            seen_names.add(name)
            _validate_binding(item.get("binding"))

    if input_schema is None:
        return
    actual_checksum = mcp_schema_checksum(input_schema)
    if actual_checksum != checksum:
        _fail("MCP_TOOL_SCHEMA_DRIFT", "MCP tool input schema changed; review and republish the node.")
    if mode == "fields":
        if input_schema.get("type") not in {None, "object"} or not isinstance(input_schema.get("properties", {}), dict):
            _fail("MCP_TOOL_FIELDS_UNSUPPORTED", "This MCP tool schema requires object-variable argument mode.")
        properties = dict(input_schema.get("properties") or {})
        binding_names = {str(item.get("name") or "") for item in data.get("argumentBindings", []) if isinstance(item, dict)}
        unknown = binding_names - set(properties)
        if unknown:
            _fail("MCP_TOOL_BINDING_UNKNOWN", "MCP tool bindings reference fields that are not present in the current schema.")
        required = {str(value) for value in input_schema.get("required", []) if isinstance(value, str)}
        if required - binding_names:
            _fail("MCP_TOOL_REQUIRED_BINDING_MISSING", "MCP tool required arguments must all be bound.")


def resolve_mcp_tool_arguments(
    data: dict[str, Any],
    variables: dict[str, WorkflowValue],
    *,
    input_schema: dict[str, Any],
) -> dict[str, WorkflowValue]:
    validate_mcp_tool_v2_config(data, input_schema=input_schema)
    mode = str(data["argumentMode"])
    if mode == "object_variable":
        name = str(data["argumentsVariable"]).strip()
        if name not in variables:
            _fail("MCP_TOOL_ARGUMENTS_UNAVAILABLE", "MCP tool arguments variable is unavailable.")
        raw = normalize_workflow_value(variables[name], path="$.mcpTool.arguments")
        if not isinstance(raw, dict):
            _fail("MCP_TOOL_ARGUMENTS_NOT_OBJECT", "MCP tool arguments variable must contain a JSON object.")
        arguments: dict[str, WorkflowValue] = dict(raw)
    else:
        arguments = {}
        for item in data.get("argumentBindings", []):
            if not isinstance(item, dict):
                continue
            arguments[str(item["name"])] = _resolve_binding(
                item.get("binding"), variables
            )
    errors = sorted(
        Draft202012Validator(input_schema).iter_errors(arguments),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        _fail("MCP_TOOL_ARGUMENTS_SCHEMA_MISMATCH", "MCP tool arguments do not satisfy the pinned input schema.")
    return arguments


def mcp_arguments_digest(arguments: dict[str, WorkflowValue]) -> str:
    return hashlib.sha256(
        json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_mcp_result(
    *,
    server_id: str,
    tool_name: str,
    text: str,
    content_types: Any,
    file_outputs: Any,
) -> dict[str, WorkflowValue]:
    safe_content_types = sorted(
        {
            str(value)[:80]
            for value in (content_types if isinstance(content_types, list) else [])
            if str(value).strip()
        }
    )
    asset_ids: list[str] = []
    for item in file_outputs if isinstance(file_outputs, list) else []:
        if not isinstance(item, dict) or str(item.get("status") or "") != "completed":
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)
    result: dict[str, WorkflowValue] = {
        "status": "completed",
        "serverId": server_id,
        "toolName": tool_name,
        "text": str(text),
        "contentTypes": safe_content_types,
        "fileAssetIds": asset_ids,
    }
    normalized = normalize_workflow_value(result, path="$.mcpTool.result")
    if not isinstance(normalized, dict):
        raise AssertionError("MCP tool result normalization changed its object shape.")
    _ensure_output_size(normalized, code="MCP_TOOL_RESULT_TOO_LARGE")
    return normalized


def _validate_binding(value: Any) -> None:
    if not isinstance(value, dict):
        _fail("MCP_TOOL_BINDING_INVALID", "MCP tool argument binding must be an object.")
    source = str(value.get("source") or "").strip()
    if source == "literal":
        if "value" not in value:
            _fail("MCP_TOOL_LITERAL_MISSING", "MCP tool literal binding requires value.")
        normalize_workflow_value(value.get("value"), path="$.mcpTool.binding.value")
        return
    if source == "variable":
        _variable_name(value.get("variable"), "MCP_TOOL_BINDING_VARIABLE_INVALID", "MCP tool binding variable")
        return
    _fail("MCP_TOOL_BINDING_SOURCE_INVALID", "MCP tool binding source must be literal or variable.")


def _resolve_binding(value: Any, variables: dict[str, WorkflowValue]) -> WorkflowValue:
    _validate_binding(value)
    assert isinstance(value, dict)
    if value.get("source") == "literal":
        return normalize_workflow_value(value.get("value"), path="$.mcpTool.binding.value")
    name = str(value.get("variable") or "").strip()
    if name not in variables:
        _fail("MCP_TOOL_BINDING_VARIABLE_UNAVAILABLE", "MCP tool binding variable is unavailable.")
    return normalize_workflow_value(variables[name], path=f"$.mcpTool.binding.{name}")


def _ensure_output_size(value: WorkflowValue, *, code: str) -> None:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_WORKFLOW_NODE_OUTPUT_BYTES:
        _fail(code, "Workflow node output exceeds the 5 MiB limit.")


def _integer(value: Any, code: str) -> int:
    if isinstance(value, bool):
        _fail(code, "Expected an integer value.")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        _fail(code, "Expected an integer value.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowR20NodeError(code, "Expected an integer value.") from exc
    return parsed


def _variable_name(value: Any, code: str, label: str) -> str:
    name = str(value or "").strip()
    if not VARIABLE_NAME_PATTERN.fullmatch(name):
        _fail(code, f"{label} must be a valid identifier.")
    return name


def _fail(code: str, message: str) -> None:
    raise WorkflowR20NodeError(code, message)
