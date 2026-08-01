from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urljoin

import yaml
from defusedxml import ElementTree as SafeElementTree

from .models import ToolDefinition
from .store import TOOL_NAME_PATTERN, ToolsetValidationError


SUPPORTED_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head")
MUTATING_HTTP_METHODS = {"post", "put", "patch", "delete"}
OPENAPI_PARAMETER_LOCATIONS = {"path", "query", "header"}
ODATA_PRIMITIVE_TYPES: dict[str, dict[str, Any]] = {
    "Edm.String": {"type": "string"},
    "Edm.Boolean": {"type": "boolean"},
    "Edm.Byte": {"type": "integer"},
    "Edm.SByte": {"type": "integer"},
    "Edm.Int16": {"type": "integer"},
    "Edm.Int32": {"type": "integer"},
    "Edm.Int64": {"type": "integer"},
    "Edm.Decimal": {"type": "number"},
    "Edm.Double": {"type": "number"},
    "Edm.Single": {"type": "number"},
    "Edm.Guid": {"type": "string", "format": "uuid"},
    "Edm.Date": {"type": "string", "format": "date"},
    "Edm.DateTimeOffset": {"type": "string", "format": "date-time"},
    "Edm.TimeOfDay": {"type": "string"},
    "Edm.Binary": {"type": "string", "contentEncoding": "base64"},
}


@dataclass(frozen=True)
class CompiledAPISpec:
    tools: list[ToolDefinition]
    base_url: str
    source_version: str
    source_hash: str
    warnings: list[str]
    summary: dict[str, Any]


def parse_openapi_text(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    if not clean:
        raise ToolsetValidationError("OpenAPI document is empty.")
    if len(clean.encode("utf-8")) > 5 * 1024 * 1024:
        raise ToolsetValidationError("OpenAPI document exceeds 5 MB.")
    try:
        parsed = json.loads(clean) if clean[:1] in {"{", "["} else yaml.safe_load(clean)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ToolsetValidationError("OpenAPI document is not valid JSON or YAML.") from exc
    if not isinstance(parsed, dict):
        raise ToolsetValidationError("OpenAPI document must be an object.")
    return parsed


def compile_openapi(
    document: dict[str, Any],
    *,
    base_url_override: str = "",
) -> CompiledAPISpec:
    version = str(document.get("openapi") or "")
    if not version.startswith(("3.0.", "3.1.")):
        raise ToolsetValidationError("Only OpenAPI 3.0 and 3.1 are supported.")
    _reject_external_refs(document)
    canonical = _canonical(document)
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    base_url = str(base_url_override or _first_server_url(document) or "").strip()
    if not base_url:
        raise ToolsetValidationError("OpenAPI base URL is required.")

    tools: list[ToolDefinition] = []
    warnings: list[str] = []
    seen_names: set[str] = set()
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ToolsetValidationError("OpenAPI paths must be an object.")
    for path, raw_path_item in sorted(paths.items()):
        if not isinstance(path, str) or not isinstance(raw_path_item, dict):
            continue
        path_item = _resolve_ref(document, raw_path_item)
        path_parameters = _parameter_map(document, path_item.get("parameters"))
        for method in SUPPORTED_HTTP_METHODS:
            raw_operation = path_item.get(method)
            if not isinstance(raw_operation, dict):
                continue
            operation = _resolve_ref(document, raw_operation)
            parameters = dict(path_parameters)
            parameters.update(_parameter_map(document, operation.get("parameters")))
            raw_name = str(operation.get("operationId") or "").strip()
            if not raw_name:
                raw_name = _generated_operation_name(method, path)
                warnings.append(
                    f"{method.upper()} {path} has no operationId; generated {raw_name}."
                )
            raw_name = _safe_tool_name(raw_name)
            if raw_name in seen_names:
                raise ToolsetValidationError(f"Duplicate OpenAPI operation name: {raw_name}")
            seen_names.add(raw_name)
            input_schema, parameter_plan = _compile_openapi_parameters(
                document,
                parameters.values(),
                operation.get("requestBody"),
            )
            description = str(
                operation.get("description")
                or operation.get("summary")
                or f"{method.upper()} {path}"
            ).strip()[:4000]
            read_only = method not in MUTATING_HTTP_METHODS
            tool = ToolDefinition(
                original_name=raw_name,
                description=description,
                input_schema=input_schema,
                enabled=False,
                schema_hash=_schema_hash(input_schema),
                discovered_at=time.time(),
                execution={
                    "kind": "openapi",
                    "method": method.upper(),
                    "path": path,
                    "parameters": parameter_plan,
                    "content_type": parameter_plan.get("content_type", ""),
                },
                read_only=read_only,
                requires_approval=not read_only,
            )
            tools.append(tool)
    if not tools:
        raise ToolsetValidationError("OpenAPI document contains no supported operations.")
    return CompiledAPISpec(
        tools=tools,
        base_url=base_url,
        source_version=version,
        source_hash=source_hash,
        warnings=warnings,
        summary={"path_count": len(paths), "operation_count": len(tools)},
    )


def compile_odata(
    metadata_text: str,
    *,
    base_url: str,
) -> CompiledAPISpec:
    clean = str(metadata_text or "").strip()
    if not clean:
        raise ToolsetValidationError("OData metadata is empty.")
    if len(clean.encode("utf-8")) > 5 * 1024 * 1024:
        raise ToolsetValidationError("OData metadata exceeds 5 MB.")
    try:
        root = SafeElementTree.fromstring(clean)
    except Exception as exc:
        raise ToolsetValidationError("OData metadata XML is invalid.") from exc
    service_root = str(base_url or "").strip()
    if not service_root:
        raise ToolsetValidationError("OData service root is required.")

    schemas = [node for node in root.iter() if _local_name(node.tag) == "Schema"]
    entity_types: dict[str, dict[str, Any]] = {}
    operations: list[tuple[str, Any]] = []
    for schema in schemas:
        namespace = str(schema.attrib.get("Namespace") or "")
        for child in list(schema):
            local = _local_name(child.tag)
            if local == "EntityType":
                name = str(child.attrib.get("Name") or "")
                properties: dict[str, dict[str, Any]] = {}
                keys: list[str] = []
                for row in list(child):
                    row_local = _local_name(row.tag)
                    if row_local == "Key":
                        keys.extend(
                            str(ref.attrib.get("Name") or "")
                            for ref in list(row)
                            if _local_name(ref.tag) == "PropertyRef"
                        )
                    elif row_local == "Property":
                        field_name = str(row.attrib.get("Name") or "")
                        field_type = str(row.attrib.get("Type") or "Edm.String")
                        properties[field_name] = {
                            "schema": _odata_type_schema(field_type),
                            "nullable": str(row.attrib.get("Nullable", "true")).lower()
                            != "false",
                        }
                entity_types[f"{namespace}.{name}"] = {
                    "properties": properties,
                    "keys": [key for key in keys if key],
                }
            elif local in {"Action", "Function"}:
                operations.append((local.lower(), child))

    entity_sets: list[dict[str, str]] = []
    for container in (
        node for node in root.iter() if _local_name(node.tag) == "EntityContainer"
    ):
        for child in list(container):
            if _local_name(child.tag) != "EntitySet":
                continue
            entity_sets.append(
                {
                    "name": str(child.attrib.get("Name") or ""),
                    "entity_type": str(child.attrib.get("EntityType") or ""),
                }
            )

    tools: list[ToolDefinition] = []
    warnings: list[str] = []
    for entity_set in entity_sets:
        name = entity_set["name"]
        entity = entity_types.get(entity_set["entity_type"], {})
        properties = dict(entity.get("properties") or {})
        keys = list(entity.get("keys") or [])
        if not name or not properties:
            warnings.append(f"EntitySet {name or '<unnamed>'} has no usable properties.")
            continue
        tools.extend(_compile_odata_entity_set(name, properties, keys))

    for kind, operation in operations:
        compiled = _compile_odata_operation(kind, operation)
        if compiled is None:
            warnings.append(
                f"OData {kind} {operation.attrib.get('Name', '<unnamed>')} is unsupported."
            )
        else:
            tools.append(compiled)
    if not tools:
        raise ToolsetValidationError("OData metadata contains no supported operations.")
    source_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    return CompiledAPISpec(
        tools=tools,
        base_url=service_root,
        source_version="4.0",
        source_hash=source_hash,
        warnings=warnings,
        summary={
            "entity_set_count": len(entity_sets),
            "operation_count": len(tools),
        },
    )


def compare_toolsets(
    previous: Iterable[ToolDefinition],
    current: Iterable[ToolDefinition],
) -> dict[str, Any]:
    before = {tool.original_name: tool for tool in previous}
    after = {tool.original_name: tool for tool in current}
    breaking: list[str] = []
    warnings: list[str] = []
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    breaking.extend(f"Operation removed: {name}" for name in removed)
    for name in sorted(set(before) & set(after)):
        old = before[name].input_schema or {}
        new = after[name].input_schema or {}
        old_required = _required_schema_paths(old)
        new_required = _required_schema_paths(new)
        introduced = sorted(new_required - old_required)
        if introduced:
            breaking.append(
                f"{name} added required parameters: {', '.join(introduced)}"
            )
        if before[name].execution.get("method") != after[name].execution.get("method"):
            breaking.append(f"{name} changed HTTP method.")
        if before[name].execution.get("path") != after[name].execution.get("path"):
            breaking.append(f"{name} changed HTTP path.")
        if before[name].schema_hash != after[name].schema_hash and not introduced:
            warnings.append(f"{name} schema changed compatibly or requires review.")
    warnings.extend(f"New operation disabled by default: {name}" for name in added)
    return {
        "breaking": breaking,
        "warnings": warnings,
        "added": added,
        "removed": removed,
        "compatible": not breaking,
    }


def _required_schema_paths(
    schema: dict[str, Any],
    *,
    prefix: str = "",
) -> set[str]:
    required_paths: set[str] = set()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return required_paths
    required_names = {
        str(name)
        for name in schema.get("required") or []
        if isinstance(name, str)
    }
    for name, child in properties.items():
        if not isinstance(name, str):
            continue
        path = f"{prefix}.{name}" if prefix else name
        if name in required_names:
            required_paths.add(path)
        if isinstance(child, dict):
            required_paths.update(_required_schema_paths(child, prefix=path))
    return required_paths


def _compile_openapi_parameters(
    document: dict[str, Any],
    parameters: Iterable[dict[str, Any]],
    raw_request_body: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    location_properties: dict[str, dict[str, Any]] = {
        "path": {},
        "query": {},
        "headers": {},
    }
    location_required: dict[str, list[str]] = {
        "path": [],
        "query": [],
        "headers": [],
    }
    parameter_plan: dict[str, Any] = {"locations": {}}
    for raw in parameters:
        parameter = _resolve_ref(document, raw)
        location = str(parameter.get("in") or "")
        name = str(parameter.get("name") or "")
        if location not in OPENAPI_PARAMETER_LOCATIONS or not name:
            continue
        public_location = "headers" if location == "header" else location
        schema = _normalize_openapi_schema(
            document,
            parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {},
        )
        schema.setdefault("description", str(parameter.get("description") or "")[:1000])
        location_properties[public_location][name] = schema
        required = bool(parameter.get("required")) or location == "path"
        if required:
            location_required[public_location].append(name)
        parameter_plan["locations"].setdefault(public_location, []).append(name)

    properties: dict[str, Any] = {}
    required_sections: list[str] = []
    for location in ("path", "query", "headers"):
        if not location_properties[location]:
            continue
        section: dict[str, Any] = {
            "type": "object",
            "properties": location_properties[location],
            "additionalProperties": False,
        }
        if location_required[location]:
            section["required"] = sorted(set(location_required[location]))
            required_sections.append(location)
        properties[location] = section

    content_type = ""
    request_body = (
        _resolve_ref(document, raw_request_body)
        if isinstance(raw_request_body, dict)
        else {}
    )
    content = request_body.get("content") if isinstance(request_body, dict) else {}
    if isinstance(content, dict):
        for candidate in ("application/json", "application/x-www-form-urlencoded"):
            media = content.get(candidate)
            if isinstance(media, dict):
                content_type = candidate
                properties["body"] = _normalize_openapi_schema(
                    document,
                    media.get("schema") if isinstance(media.get("schema"), dict) else {},
                )
                if request_body.get("required"):
                    required_sections.append("body")
                break
    parameter_plan["content_type"] = content_type
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required_sections:
        schema["required"] = sorted(set(required_sections))
    return schema, parameter_plan


def _compile_odata_entity_set(
    name: str,
    properties: dict[str, dict[str, Any]],
    keys: list[str],
) -> list[ToolDefinition]:
    field_enum = sorted(properties)
    filter_schema = {
        "type": "array",
        "maxItems": 20,
        "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": field_enum},
                "op": {
                    "type": "string",
                    "enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains", "startswith"],
                },
                "value": {},
            },
            "required": ["field", "op", "value"],
            "additionalProperties": False,
        },
    }
    list_schema = {
        "type": "object",
        "properties": {
            "select": {
                "type": "array",
                "items": {"type": "string", "enum": field_enum},
                "maxItems": 50,
                "uniqueItems": True,
            },
            "filter": filter_schema,
            "orderby": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": field_enum},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["field"],
                    "additionalProperties": False,
                },
            },
            "top": {"type": "integer", "minimum": 1, "maximum": 100},
            "skip": {"type": "integer", "minimum": 0, "maximum": 100000},
        },
        "additionalProperties": False,
    }
    tools = [
        _tool(
            f"list_{name}",
            f"List {name} entities using controlled OData query options.",
            list_schema,
            {
                "kind": "odata",
                "operation": "list",
                "entity_set": name,
                "method": "GET",
                "path": name,
                "fields": field_enum,
            },
            read_only=True,
        )
    ]
    if keys:
        key_properties = {
            key: deepcopy(properties[key]["schema"])
            for key in keys
            if key in properties
        }
        if key_properties:
            key_schema = {
                "type": "object",
                "properties": {"key": {"type": "object", "properties": key_properties}},
                "required": ["key"],
                "additionalProperties": False,
            }
            key_schema["properties"]["key"]["required"] = list(key_properties)
            key_schema["properties"]["key"]["additionalProperties"] = False
            tools.append(
                _tool(
                    f"get_{name}",
                    f"Get one {name} entity by key.",
                    key_schema,
                    {
                        "kind": "odata",
                        "operation": "get",
                        "entity_set": name,
                        "method": "GET",
                        "path": name,
                        "keys": list(key_properties),
                    },
                    read_only=True,
                )
            )
    writable = {
        field: deepcopy(metadata["schema"])
        for field, metadata in properties.items()
        if field not in keys
    }
    body_schema = {
        "type": "object",
        "properties": writable,
        "additionalProperties": False,
    }
    tools.append(
        _tool(
            f"create_{name}",
            f"Create one {name} entity.",
            {"type": "object", "properties": {"body": body_schema}, "required": ["body"]},
            {
                "kind": "odata",
                "operation": "create",
                "entity_set": name,
                "method": "POST",
                "path": name,
            },
            read_only=False,
        )
    )
    return tools


def _compile_odata_operation(kind: str, operation: Any) -> ToolDefinition | None:
    name = str(operation.attrib.get("Name") or "")
    if not name:
        return None
    parameters: dict[str, Any] = {}
    required: list[str] = []
    for child in list(operation):
        if _local_name(child.tag) != "Parameter":
            continue
        param_name = str(child.attrib.get("Name") or "")
        param_type = str(child.attrib.get("Type") or "")
        if not param_name or param_type not in ODATA_PRIMITIVE_TYPES:
            return None
        parameters[param_name] = deepcopy(ODATA_PRIMITIVE_TYPES[param_type])
        if str(child.attrib.get("Nullable", "true")).lower() == "false":
            required.append(param_name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": parameters,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    is_action = kind == "action"
    return _tool(
        _safe_tool_name(name),
        f"OData {kind} {name}.",
        schema,
        {
            "kind": "odata",
            "operation": kind,
            "operation_name": name,
            "method": "POST" if is_action else "GET",
            "path": name,
        },
        read_only=not is_action,
    )


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    execution: dict[str, Any],
    *,
    read_only: bool,
) -> ToolDefinition:
    return ToolDefinition(
        original_name=_safe_tool_name(name),
        description=description[:4000],
        input_schema=schema,
        enabled=False,
        schema_hash=_schema_hash(schema),
        discovered_at=time.time(),
        execution=execution,
        read_only=read_only,
        requires_approval=not read_only,
    )


def _parameter_map(
    document: dict[str, Any],
    raw_parameters: Any,
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in raw_parameters if isinstance(raw_parameters, list) else []:
        if not isinstance(raw, dict):
            continue
        parameter = _resolve_ref(document, raw)
        key = (str(parameter.get("in") or ""), str(parameter.get("name") or ""))
        if all(key):
            result[key] = parameter
    return result


def _normalize_openapi_schema(
    document: dict[str, Any],
    raw_schema: dict[str, Any],
    *,
    depth: int = 0,
) -> dict[str, Any]:
    if depth > 20:
        raise ToolsetValidationError("OpenAPI schema nesting exceeds 20 levels.")
    schema = deepcopy(_resolve_ref(document, raw_schema))
    if not isinstance(schema, dict):
        return {}
    if schema.get("nullable") is True:
        current_type = schema.get("type")
        if isinstance(current_type, str):
            schema["type"] = [current_type, "null"]
        schema.pop("nullable", None)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["properties"] = {
            str(name): _normalize_openapi_schema(
                document,
                value if isinstance(value, dict) else {},
                depth=depth + 1,
            )
            for name, value in properties.items()
        }
    items = schema.get("items")
    if isinstance(items, dict):
        schema["items"] = _normalize_openapi_schema(
            document,
            items,
            depth=depth + 1,
        )
    for keyword in ("allOf", "anyOf", "oneOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            schema[keyword] = [
                _normalize_openapi_schema(
                    document,
                    value if isinstance(value, dict) else {},
                    depth=depth + 1,
                )
                for value in variants
            ]
    for key in list(schema):
        if key.startswith("x-") or key in {
            "xml",
            "externalDocs",
            "example",
            "examples",
            "discriminator",
            "readOnly",
            "writeOnly",
        }:
            schema.pop(key, None)
    return schema


def _resolve_ref(document: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    reference = value.get("$ref")
    if not reference:
        return deepcopy(value)
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise ToolsetValidationError("Only local OpenAPI $ref values are supported.")
    current: Any = document
    for segment in reference[2:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise ToolsetValidationError(f"OpenAPI reference not found: {reference}")
        current = current[key]
    if not isinstance(current, dict):
        raise ToolsetValidationError(f"OpenAPI reference is not an object: {reference}")
    merged = deepcopy(current)
    merged.update({key: deepcopy(item) for key, item in value.items() if key != "$ref"})
    return merged


def _reject_external_refs(value: Any) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference and (
            not isinstance(reference, str) or not reference.startswith("#/")
        ):
            raise ToolsetValidationError("External OpenAPI references are not allowed.")
        for child in value.values():
            _reject_external_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_external_refs(child)


def _first_server_url(document: dict[str, Any]) -> str:
    servers = document.get("servers")
    if not isinstance(servers, list):
        return ""
    for server in servers:
        if isinstance(server, dict) and server.get("url"):
            return str(server["url"])
    return ""


def _generated_operation_name(method: str, path: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", path.strip("/")).strip("_")
    return _safe_tool_name(f"{method}_{suffix or 'root'}")


def _safe_tool_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value).strip("_.:-")
    if not clean or clean[0].isdigit():
        clean = f"operation_{clean}"
    clean = clean[:200]
    if not TOOL_NAME_PATTERN.fullmatch(clean):
        raise ToolsetValidationError(f"Invalid operation name: {value}")
    return clean


def _schema_hash(schema: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(schema).encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _odata_type_schema(value: str) -> dict[str, Any]:
    clean = value.removeprefix("Collection(").removesuffix(")")
    if value.startswith("Collection("):
        return {
            "type": "array",
            "items": deepcopy(ODATA_PRIMITIVE_TYPES.get(clean, {"type": "object"})),
        }
    return deepcopy(ODATA_PRIMITIVE_TYPES.get(clean, {"type": "object"}))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
