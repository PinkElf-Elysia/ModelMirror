from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal

from .values import WorkflowValue, normalize_workflow_value


IterationMode = Literal["template_map", "workflow_map"]

MAX_LOCAL_ITEMS = 10_000
MAX_WORKFLOW_ITEMS = 32
MAX_ITEM_TEMPLATE_CHARS = 20_000
MAX_BATCH_OUTPUT_BYTES = 5 * 1_024 * 1_024
MAX_CHILD_RESULT_CHARS = 100_000
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class WorkflowIterationError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(f"{code}: {safe_message}")
        self.code = code
        self.safe_message = safe_message


def _fail(code: str, message: str) -> None:
    raise WorkflowIterationError(code, message)


def contract_version(data: Mapping[str, Any]) -> int:
    value = data.get("contractVersion")
    return value if type(value) is int else 1


def iteration_mode(data: Mapping[str, Any]) -> str:
    return str(data.get("mode") or "").strip()


def is_iteration_v2(data: Mapping[str, Any]) -> bool:
    return contract_version(data) == 2


def is_workflow_map(data: Mapping[str, Any]) -> bool:
    return is_iteration_v2(data) and iteration_mode(data) == "workflow_map"


def _identifier(data: Mapping[str, Any], field_name: str) -> str:
    value = str(data.get(field_name) or "").strip()
    if not VARIABLE_NAME_PATTERN.fullmatch(value):
        _fail(
            f"ITERATION_{field_name.upper()}_INVALID",
            f"Batch processing {field_name} must be an identifier.",
        )
    return value


def _validate_common(data: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if contract_version(data) != 2:
        _fail(
            "ITERATION_CONTRACT_VERSION_INVALID",
            "Batch processing V2 requires contractVersion=2.",
        )
    mode = iteration_mode(data)
    if mode not in {"template_map", "workflow_map"}:
        _fail(
            "ITERATION_MODE_INVALID",
            "Batch processing mode must be template_map or workflow_map.",
        )
    input_variable = _identifier(data, "inputVariable")
    item_variable = _identifier(data, "itemVariable")
    index_variable = _identifier(data, "indexVariable")
    output_variable = _identifier(data, "outputVariable")
    if item_variable == index_variable:
        _fail(
            "ITERATION_LOCAL_VARIABLES_CONFLICT",
            "Batch item and index variables must use different names.",
        )
    if input_variable in {item_variable, index_variable}:
        _fail(
            "ITERATION_LOCAL_VARIABLES_SHADOW_INPUT",
            "Batch item and index variables cannot shadow the input variable.",
        )
    if output_variable in {input_variable, item_variable, index_variable}:
        _fail(
            "ITERATION_OUTPUT_VARIABLE_CONFLICT",
            "Batch output variable cannot overwrite the input or a local variable.",
        )
    return input_variable, item_variable, index_variable, output_variable


def _validate_binding(input_name: str, binding: Any) -> str:
    if not VARIABLE_NAME_PATTERN.fullmatch(input_name):
        _fail(
            "ITERATION_BINDING_NAME_INVALID",
            "Batch workflow input names must be identifiers.",
        )
    if not isinstance(binding, dict):
        _fail(
            "ITERATION_BINDING_INVALID",
            f"Batch workflow input '{input_name}' must use a structured binding.",
        )
    source = str(binding.get("source") or "").strip()
    if source not in {"item", "index", "variable", "literal"}:
        _fail(
            "ITERATION_BINDING_SOURCE_INVALID",
            f"Batch workflow input '{input_name}' has an unsupported source.",
        )
    allowed_fields = {
        "item": {"source"},
        "index": {"source"},
        "variable": {"source", "variable"},
        "literal": {"source", "value"},
    }[source]
    if set(binding) - allowed_fields:
        _fail(
            "ITERATION_BINDING_FIELDS_INVALID",
            f"Batch workflow input '{input_name}' contains fields that do not apply to its source.",
        )
    if source == "variable":
        variable = str(binding.get("variable") or "").strip()
        if not VARIABLE_NAME_PATTERN.fullmatch(variable):
            _fail(
                "ITERATION_BINDING_VARIABLE_INVALID",
                f"Batch workflow input '{input_name}' needs a variable identifier.",
            )
    if source == "literal":
        if "value" not in binding:
            _fail(
                "ITERATION_BINDING_LITERAL_MISSING",
                f"Batch workflow input '{input_name}' needs a literal value.",
            )
        try:
            normalize_workflow_value(
                binding.get("value"),
                path=f"$.iteration.inputBindings.{input_name}.value",
            )
        except ValueError:
            _fail(
                "ITERATION_BINDING_LITERAL_INVALID",
                f"Batch workflow input '{input_name}' literal is not a valid workflow value.",
            )
    return source


def validate_iteration_v2_config(data: Mapping[str, Any]) -> None:
    _validate_common(data)
    mode = iteration_mode(data)
    if mode == "template_map":
        template = data.get("itemTemplate")
        if not isinstance(template, str) or not 1 <= len(template) <= MAX_ITEM_TEMPLATE_CHARS:
            _fail(
                "ITERATION_TEMPLATE_INVALID",
                f"Batch itemTemplate must contain 1 to {MAX_ITEM_TEMPLATE_CHARS} characters.",
            )
        return

    project_id = str(data.get("targetProjectId") or "").strip()
    if not re.fullmatch(r"wf_[a-f0-9]{32}", project_id):
        _fail(
            "ITERATION_TARGET_PROJECT_INVALID",
            "Batch subworkflow mode needs a fixed workflow project ID.",
        )
    version = data.get("targetVersion")
    if type(version) is not int or version < 1:
        _fail(
            "ITERATION_TARGET_VERSION_INVALID",
            "Batch subworkflow mode needs a fixed published version.",
        )
    timeout = data.get("timeoutSeconds")
    if type(timeout) is not int or not 1 <= timeout <= 60:
        _fail(
            "ITERATION_TIMEOUT_INVALID",
            "Batch subworkflow timeoutSeconds must be between 1 and 60.",
        )
    bindings = data.get("inputBindings")
    if not isinstance(bindings, dict):
        _fail(
            "ITERATION_BINDINGS_INVALID",
            "Batch subworkflow inputBindings must be an object.",
        )
    item_binding_count = sum(
        _validate_binding(str(name), binding) == "item"
        for name, binding in bindings.items()
    )
    if item_binding_count != 1:
        _fail(
            "ITERATION_ITEM_BINDING_COUNT_INVALID",
            "Batch subworkflow mode requires exactly one item binding.",
        )


def iteration_variable_references(data: Mapping[str, Any]) -> set[str]:
    if not is_iteration_v2(data):
        return set()
    references = {str(data.get("inputVariable") or "").strip()}
    if is_workflow_map(data):
        bindings = data.get("inputBindings")
        if isinstance(bindings, dict):
            for binding in bindings.values():
                if isinstance(binding, dict) and binding.get("source") == "variable":
                    references.add(str(binding.get("variable") or "").strip())
    return {value for value in references if value}


def require_input_array(
    data: Mapping[str, Any],
    variables: Mapping[str, WorkflowValue],
) -> list[WorkflowValue]:
    input_variable, item_variable, index_variable, _ = _validate_common(data)
    if input_variable not in variables:
        _fail(
            "ITERATION_INPUT_VARIABLE_MISSING",
            f"Batch processing input variable '{input_variable}' is unavailable.",
        )
    shadowed_variables = sorted(
        {item_variable, index_variable}.intersection(variables)
    )
    if shadowed_variables:
        _fail(
            "ITERATION_LOCAL_VARIABLE_SHADOWS_VISIBLE_VARIABLE",
            "Batch local variables cannot shadow visible workflow variables: "
            + ", ".join(shadowed_variables)
            + ".",
        )
    try:
        value = normalize_workflow_value(
            variables[input_variable], path=f"$.variables.{input_variable}"
        )
    except ValueError:
        _fail(
            "ITERATION_INPUT_VALUE_INVALID",
            "Batch processing input is not a valid workflow value.",
        )
    if not isinstance(value, list):
        _fail(
            "ITERATION_INPUT_NOT_ARRAY",
            "Batch processing input must be a JSON array.",
        )
    limit = MAX_WORKFLOW_ITEMS if is_workflow_map(data) else MAX_LOCAL_ITEMS
    if len(value) > limit:
        _fail(
            "ITERATION_ITEM_LIMIT_EXCEEDED",
            f"Batch processing accepts at most {limit} items in this mode.",
        )
    return value


def ensure_batch_output_size(value: WorkflowValue) -> None:
    encoded = json.dumps(
        normalize_workflow_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_BATCH_OUTPUT_BYTES:
        _fail(
            "ITERATION_OUTPUT_TOO_LARGE",
            "Batch processing output exceeds the 5 MiB workflow value limit.",
        )


def workflow_batch_input_digest(
    *,
    target_project_id: str,
    target_version: int,
    resolved_inputs: list[dict[str, WorkflowValue]],
) -> str:
    encoded = json.dumps(
        {
            "targetProjectId": str(target_project_id),
            "targetVersion": int(target_version),
            "resolvedInputs": normalize_workflow_value(resolved_inputs),
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execute_template_map(
    data: Mapping[str, Any],
    variables: Mapping[str, WorkflowValue],
    *,
    render: Callable[[str, Mapping[str, WorkflowValue]], str],
) -> list[str]:
    validate_iteration_v2_config(data)
    if iteration_mode(data) != "template_map":
        _fail(
            "ITERATION_MODE_MISMATCH",
            "Template batch execution requires template_map mode.",
        )
    items = require_input_array(data, variables)
    item_variable = str(data["itemVariable"])
    index_variable = str(data["indexVariable"])
    template = str(data["itemTemplate"])
    results: list[str] = []
    encoded_size = 2  # JSON array delimiters: []
    for index, item in enumerate(items):
        scoped = dict(variables)
        scoped[item_variable] = normalize_workflow_value(
            item, path=f"$.iteration.items[{index}]"
        )
        scoped[index_variable] = index
        result = render(template, scoped)
        encoded_result = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded_size += len(encoded_result) + (1 if results else 0)
        if encoded_size > MAX_BATCH_OUTPUT_BYTES:
            _fail(
                "ITERATION_OUTPUT_TOO_LARGE",
                "Batch processing output exceeds the 5 MiB workflow value limit.",
            )
        results.append(result)
    ensure_batch_output_size(results)
    return results


def resolve_workflow_map_inputs(
    data: Mapping[str, Any],
    variables: Mapping[str, WorkflowValue],
    *,
    item: WorkflowValue,
    index: int,
) -> dict[str, WorkflowValue]:
    validate_iteration_v2_config(data)
    if iteration_mode(data) != "workflow_map":
        _fail(
            "ITERATION_MODE_MISMATCH",
            "Subworkflow batch execution requires workflow_map mode.",
        )
    resolved: dict[str, WorkflowValue] = {}
    bindings = data.get("inputBindings")
    assert isinstance(bindings, dict)
    for raw_name, raw_binding in bindings.items():
        name = str(raw_name)
        assert isinstance(raw_binding, dict)
        source = str(raw_binding.get("source"))
        if source == "item":
            value: Any = item
        elif source == "index":
            value = index
        elif source == "literal":
            value = raw_binding.get("value")
        else:
            variable = str(raw_binding.get("variable") or "")
            if variable not in variables:
                _fail(
                    "ITERATION_BINDING_VARIABLE_MISSING",
                    f"Batch subworkflow variable '{variable}' is unavailable.",
                )
            value = variables[variable]
        try:
            resolved[name] = normalize_workflow_value(
                value,
                path=f"$.iteration.resolvedInputs.{index}.{name}",
            )
        except ValueError:
            _fail(
                "ITERATION_BINDING_VALUE_INVALID",
                f"Batch subworkflow input '{name}' is not a valid workflow value.",
            )
    return resolved
