from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


MAX_EXTRACTOR_SCHEMA_BYTES = 64 * 1024
MAX_EXTRACTOR_SCHEMA_DEPTH = 10
MAX_EXTRACTOR_SCHEMA_PROPERTIES = 100
MAX_EXTRACTOR_FIELDS = 50
MAX_CLASSIFIER_CATEGORIES = 8
MAX_CLASSIFIER_KEYWORDS = 20
MAX_TYPED_AI_OUTPUT_BYTES = 5 * 1024 * 1024

VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
FIELD_ID_PATTERN = re.compile(r"^field_(?:[1-9]|[1-4][0-9]|50)$")
CATEGORY_ID_PATTERN = re.compile(r"^category_[1-8]$")
FENCED_JSON_PATTERN = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)

EXTRACTOR_FIELD_TYPES: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "string_array": {"type": "array", "items": {"type": "string"}},
    "number_array": {"type": "array", "items": {"type": "number"}},
}


class WorkflowTypedAIError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ClassifierCategory:
    id: str
    label: str
    description: str
    keywords: tuple[str, ...]
    match_mode: str


@dataclass(frozen=True, slots=True)
class ClassifierSelection:
    category_id: str
    label: str
    method: str
    matched_keyword: str | None = None


def contract_version(data: dict[str, Any]) -> int:
    raw = data.get("contractVersion")
    if raw is None or raw == "":
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw


def is_typed_ai_v2(data: dict[str, Any]) -> bool:
    return contract_version(data) == 2


def build_parameter_extractor_schema(data: dict[str, Any]) -> dict[str, Any]:
    if contract_version(data) != 2:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_contract_version",
            "Parameter extractor V2 requires contractVersion=2.",
        )
    schema_mode = str(data.get("schemaMode") or "fields").strip()
    output_shape = str(data.get("outputShape") or "object").strip()
    if schema_mode not in {"fields", "json_schema"}:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_schema_mode",
            "Parameter extractor schemaMode must be fields or json_schema.",
        )
    if output_shape not in {"object", "object_list"}:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_output_shape",
            "Parameter extractor outputShape must be object or object_list.",
        )

    if schema_mode == "fields":
        schema = _schema_from_extractor_fields(data.get("fields"), output_shape)
    else:
        schema = _coerce_json_schema(data.get("jsonSchema"))
        _validate_schema_root(schema, output_shape)
    _validate_schema_complexity(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_json_schema",
            f"Parameter extractor JSON Schema is invalid: {exc.message[:300]}",
        ) from exc
    return schema


def validate_parameter_extractor_v2_config(data: dict[str, Any]) -> dict[str, Any]:
    for field_name in ("inputVariable", "outputVariable"):
        raw_value = data.get(field_name)
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        if not VARIABLE_NAME_PATTERN.fullmatch(value):
            raise WorkflowTypedAIError(
                f"invalid_parameter_extractor_{_snake(field_name)}",
                f"Parameter extractor {field_name} must be a valid variable name.",
            )
    if not isinstance(data.get("modelId"), str) or not data["modelId"].strip():
        raise WorkflowTypedAIError(
            "missing_parameter_extractor_model_id",
            "Parameter extractor V2 requires modelId.",
        )
    repair_attempts = data.get("repairAttempts", 0)
    if (
        isinstance(repair_attempts, bool)
        or not isinstance(repair_attempts, int)
        or repair_attempts not in {0, 1}
    ):
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_repair_attempts",
            "Parameter extractor repairAttempts must be 0 or 1.",
        )
    return build_parameter_extractor_schema(data)


def parse_and_validate_extractor_output(
    text: str,
    schema: dict[str, Any],
    *,
    max_output_bytes: int = MAX_TYPED_AI_OUTPUT_BYTES,
) -> Any:
    candidate = extract_json_text(text)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise WorkflowTypedAIError(
            "parameter_extractor_invalid_json",
            "Parameter extractor model output is not valid JSON.",
        ) from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        issue = errors[0]
        path = ".".join(str(part) for part in issue.absolute_path) or "$"
        raise WorkflowTypedAIError(
            "parameter_extractor_schema_mismatch",
            f"Parameter extractor output does not match the schema at {path}.",
        )
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > max_output_bytes:
        raise WorkflowTypedAIError(
            "parameter_extractor_output_too_large",
            "Parameter extractor output exceeds the workflow value size limit.",
        )
    return value


def parameter_extractor_prompt(input_text: str, schema: dict[str, Any]) -> str:
    return (
        "Extract the requested information from the input. Return only JSON that "
        "matches the supplied JSON Schema. Do not include Markdown or commentary. "
        "Do not invent required values; if the input cannot satisfy the schema, return "
        "the closest JSON value so validation can fail safely.\n\n"
        f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Input:\n{input_text}"
    )


def parameter_extractor_repair_prompt(
    invalid_text: str,
    schema: dict[str, Any],
) -> str:
    return (
        "Repair the candidate so it is valid JSON matching the supplied JSON Schema. "
        "Return only JSON. Do not add facts that are absent from the candidate.\n\n"
        f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Candidate:\n{invalid_text[:20_000]}"
    )


def validate_question_classifier_v2_config(
    data: dict[str, Any],
) -> tuple[ClassifierCategory, ...]:
    if contract_version(data) != 2:
        raise WorkflowTypedAIError(
            "invalid_question_classifier_contract_version",
            "Question classifier V2 requires contractVersion=2.",
        )
    for field_name in ("inputVariable", "outputVariable"):
        raw_value = data.get(field_name)
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        if not VARIABLE_NAME_PATTERN.fullmatch(value):
            raise WorkflowTypedAIError(
                f"invalid_question_classifier_{_snake(field_name)}",
                f"Question classifier {field_name} must be a valid variable name.",
            )
    mode = str(data.get("classificationMode") or "rules_only").strip()
    if mode not in {"rules_only", "rules_then_model", "model_only"}:
        raise WorkflowTypedAIError(
            "invalid_question_classifier_mode",
            "Question classifier classificationMode is invalid.",
        )
    if mode != "rules_only" and (
        not isinstance(data.get("modelId"), str) or not data["modelId"].strip()
    ):
        raise WorkflowTypedAIError(
            "missing_question_classifier_model_id",
            "Question classifier modelId is required for model classification.",
        )
    default_label = data.get("defaultLabel")
    if (
        not isinstance(default_label, str)
        or not default_label.strip()
        or len(default_label.strip()) > 100
    ):
        raise WorkflowTypedAIError(
            "missing_question_classifier_default_label",
            "Question classifier defaultLabel is required.",
        )
    if not isinstance(data.get("caseSensitive"), bool):
        raise WorkflowTypedAIError(
            "invalid_question_classifier_case_sensitive",
            "Question classifier caseSensitive must be a boolean.",
        )
    raw_categories = data.get("categoriesV2")
    if not isinstance(raw_categories, list) or not 2 <= len(raw_categories) <= 8:
        raise WorkflowTypedAIError(
            "invalid_question_classifier_categories",
            "Question classifier V2 requires 2 to 8 categories.",
        )
    categories: list[ClassifierCategory] = []
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for raw in raw_categories:
        if not isinstance(raw, dict):
            raise WorkflowTypedAIError(
                "invalid_question_classifier_category",
                "Each question classifier category must be an object.",
            )
        raw_category_id = raw.get("id")
        category_id = (
            raw_category_id.strip() if isinstance(raw_category_id, str) else ""
        )
        raw_label = raw.get("label")
        label = raw_label.strip() if isinstance(raw_label, str) else ""
        raw_description = raw.get("description", "")
        description = (
            raw_description.strip() if isinstance(raw_description, str) else ""
        )
        raw_match_mode = raw.get("matchMode")
        match_mode = (
            raw_match_mode.strip() if isinstance(raw_match_mode, str) else ""
        )
        raw_keywords = raw.get("keywords")
        if not CATEGORY_ID_PATTERN.fullmatch(category_id) or category_id in seen_ids:
            raise WorkflowTypedAIError(
                "invalid_question_classifier_category_id",
                "Question classifier category IDs must be unique category_1 through category_8 values.",
            )
        if not label or len(label) > 100 or label in seen_labels:
            raise WorkflowTypedAIError(
                "invalid_question_classifier_category_label",
                "Question classifier labels must be unique and 1 to 100 characters.",
            )
        if not isinstance(raw_description, str) or len(description) > 500:
            raise WorkflowTypedAIError(
                "invalid_question_classifier_category_description",
                "Question classifier category descriptions cannot exceed 500 characters.",
            )
        if match_mode not in {"contains_any", "contains_all"}:
            raise WorkflowTypedAIError(
                "invalid_question_classifier_match_mode",
                "Question classifier category matchMode is invalid.",
            )
        if not isinstance(raw_keywords, list) or len(raw_keywords) > MAX_CLASSIFIER_KEYWORDS:
            raise WorkflowTypedAIError(
                "invalid_question_classifier_keywords",
                "Question classifier keywords must be an array with at most 20 items.",
            )
        keywords = tuple(
            str(item).strip()
            for item in raw_keywords
            if isinstance(item, str) and str(item).strip()
        )
        if (
            len(keywords) != len(raw_keywords)
            or len(set(keywords)) != len(keywords)
            or any(len(keyword) > 200 for keyword in keywords)
        ):
            raise WorkflowTypedAIError(
                "invalid_question_classifier_keywords",
                "Question classifier keywords must be unique non-empty strings.",
            )
        if mode == "rules_only" and not keywords:
            raise WorkflowTypedAIError(
                "missing_question_classifier_keywords",
                "Every rules-only category requires at least one keyword.",
            )
        if mode != "rules_only" and not description and not keywords:
            raise WorkflowTypedAIError(
                "missing_question_classifier_category_description",
                "Model-classified categories require a description or keywords.",
            )
        seen_ids.add(category_id)
        seen_labels.add(label)
        categories.append(
            ClassifierCategory(
                id=category_id,
                label=label,
                description=description,
                keywords=keywords,
                match_mode=match_mode,
            )
        )
    return tuple(categories)


def select_question_classifier_rule(
    text: str,
    categories: tuple[ClassifierCategory, ...],
    *,
    case_sensitive: bool,
) -> ClassifierSelection | None:
    candidate = text if case_sensitive else text.lower()
    for category in categories:
        keywords = category.keywords if case_sensitive else tuple(
            keyword.lower() for keyword in category.keywords
        )
        if not keywords:
            continue
        if category.match_mode == "contains_all":
            matched = all(keyword in candidate for keyword in keywords)
            matched_keyword = ",".join(category.keywords) if matched else None
        else:
            hit = next(
                (index for index, keyword in enumerate(keywords) if keyword in candidate),
                None,
            )
            matched = hit is not None
            matched_keyword = category.keywords[hit] if hit is not None else None
        if matched:
            return ClassifierSelection(
                category_id=category.id,
                label=category.label,
                method="rule",
                matched_keyword=matched_keyword,
            )
    return None


def question_classifier_prompt(
    text: str,
    categories: tuple[ClassifierCategory, ...],
) -> str:
    catalog = [
        {
            "categoryId": item.id,
            "label": item.label,
            "description": item.description,
            "keywords": list(item.keywords),
        }
        for item in categories
    ]
    return (
        "Classify the input into exactly one category. Return only JSON as "
        '{"categoryId":"category_1"}. Use "default" when no category applies. '
        "Do not return labels or commentary.\n\n"
        f"Categories:\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"Input:\n{text}"
    )


def parse_question_classifier_model_output(
    text: str,
    categories: tuple[ClassifierCategory, ...],
    *,
    default_label: str,
) -> ClassifierSelection:
    try:
        parsed = json.loads(extract_json_text(text))
    except json.JSONDecodeError as exc:
        raise WorkflowTypedAIError(
            "question_classifier_invalid_model_output",
            "Question classifier model output is not valid JSON.",
        ) from exc
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"categoryId"}
        or not isinstance(parsed.get("categoryId"), str)
    ):
        raise WorkflowTypedAIError(
            "question_classifier_invalid_model_output",
            "Question classifier model output must contain only categoryId.",
        )
    category_id = parsed["categoryId"].strip()
    if category_id == "default":
        return ClassifierSelection("default", default_label, "default")
    selected = next((item for item in categories if item.id == category_id), None)
    if selected is None:
        raise WorkflowTypedAIError(
            "question_classifier_unknown_category",
            "Question classifier model returned an unknown category ID.",
        )
    return ClassifierSelection(selected.id, selected.label, "model")


def extract_json_text(text: str) -> str:
    stripped = str(text or "").strip()
    match = FENCED_JSON_PATTERN.fullmatch(stripped)
    return match.group(1).strip() if match else stripped


def _schema_from_extractor_fields(raw_fields: Any, output_shape: str) -> dict[str, Any]:
    if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= MAX_EXTRACTOR_FIELDS:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_fields",
            "Parameter extractor fields mode requires 1 to 50 fields.",
        )
    properties: dict[str, Any] = {}
    required: list[str] = []
    seen_ids: set[str] = set()
    for raw in raw_fields:
        if not isinstance(raw, dict):
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_field",
                "Each parameter extractor field must be an object.",
            )
        raw_field_id = raw.get("id")
        field_id = raw_field_id.strip() if isinstance(raw_field_id, str) else ""
        raw_name = raw.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        raw_description = raw.get("description", "")
        description = (
            raw_description.strip() if isinstance(raw_description, str) else ""
        )
        raw_value_type = raw.get("valueType")
        value_type = (
            raw_value_type.strip() if isinstance(raw_value_type, str) else ""
        )
        if not FIELD_ID_PATTERN.fullmatch(field_id) or field_id in seen_ids:
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_field_id",
                "Parameter extractor field IDs must be unique field_1 through field_50 values.",
            )
        if not VARIABLE_NAME_PATTERN.fullmatch(name) or name in properties:
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_field_name",
                "Parameter extractor field names must be unique identifiers.",
            )
        if not isinstance(raw_description, str) or len(description) > 500:
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_field_description",
                "Parameter extractor field descriptions cannot exceed 500 characters.",
            )
        if value_type not in EXTRACTOR_FIELD_TYPES:
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_field_type",
                "Parameter extractor field valueType is invalid.",
            )
        if not isinstance(raw.get("required"), bool) or not isinstance(
            raw.get("nullable"), bool
        ):
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_field_flags",
                "Parameter extractor required and nullable flags must be booleans.",
            )
        field_schema = dict(EXTRACTOR_FIELD_TYPES[value_type])
        if description:
            field_schema["description"] = description
        if raw["nullable"]:
            field_schema = {"anyOf": [field_schema, {"type": "null"}]}
        properties[name] = field_schema
        if raw["required"]:
            required.append(name)
        seen_ids.add(field_id)
    item_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if output_shape == "object_list":
        return {"type": "array", "items": item_schema}
    return item_schema


def _coerce_json_schema(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        schema = raw
    elif isinstance(raw, str):
        try:
            schema = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkflowTypedAIError(
                "invalid_parameter_extractor_json_schema",
                "Parameter extractor jsonSchema must be valid JSON.",
            ) from exc
    else:
        schema = None
    if not isinstance(schema, dict) or not schema:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_json_schema",
            "Parameter extractor jsonSchema must be a non-empty object.",
        )
    return schema


def _validate_schema_root(schema: dict[str, Any], output_shape: str) -> None:
    if output_shape == "object":
        valid = schema.get("type") == "object"
    else:
        items = schema.get("items")
        valid = schema.get("type") == "array" and isinstance(items, dict) and items.get("type") == "object"
    if not valid:
        raise WorkflowTypedAIError(
            "invalid_parameter_extractor_schema_root",
            "Parameter extractor JSON Schema root must match outputShape.",
        )


def _validate_schema_complexity(schema: dict[str, Any]) -> None:
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_EXTRACTOR_SCHEMA_BYTES:
        raise WorkflowTypedAIError(
            "parameter_extractor_schema_too_large",
            "Parameter extractor JSON Schema exceeds 64 KiB.",
        )
    property_count = 0

    def visit(value: Any, depth: int) -> None:
        nonlocal property_count
        if depth > MAX_EXTRACTOR_SCHEMA_DEPTH:
            raise WorkflowTypedAIError(
                "parameter_extractor_schema_too_deep",
                "Parameter extractor JSON Schema exceeds depth 10.",
            )
        if isinstance(value, dict):
            if "$ref" in value:
                raise WorkflowTypedAIError(
                    "parameter_extractor_schema_ref_forbidden",
                    "Parameter extractor JSON Schema cannot use $ref.",
                )
            properties = value.get("properties")
            if isinstance(properties, dict):
                property_count += len(properties)
                if property_count > MAX_EXTRACTOR_SCHEMA_PROPERTIES:
                    raise WorkflowTypedAIError(
                        "parameter_extractor_schema_too_many_properties",
                        "Parameter extractor JSON Schema exceeds 100 properties.",
                    )
            for item in value.values():
                visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, depth + 1)

    visit(schema, 0)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
