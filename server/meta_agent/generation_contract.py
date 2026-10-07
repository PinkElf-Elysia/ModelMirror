"""Model-facing schemas; semantic validators remain authoritative at execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import ConfigDict, Field

from .node_adapters import get_planner_node_adapter, planner_capability_metadata
from .schemas import GraphIntentV3, MetaPlannerTask, MetaPlannerTaskPlan
from .write_contract import write_value_source_contract
from .resource_generation_contract import scope_resource_schema
try:
    from server.workflow_native.node_contracts import WorkflowValueSchema
except ModuleNotFoundError as exc:
    if exc.name != "server":
        raise
    from workflow_native.node_contracts import WorkflowValueSchema


class GenerationTask(MetaPlannerTask):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal["expert"] = "expert"
    interaction_prompt: Literal[""] = ""
    output_variable: None = None


class GenerationTaskPlan(MetaPlannerTaskPlan):
    model_config = ConfigDict(extra="forbid")

    tasks: list[GenerationTask] = Field(min_length=1, max_length=8)


def parse_generation_task_plan(payload: Any) -> MetaPlannerTaskPlan:
    parsed = GenerationTaskPlan.model_validate(payload)
    return MetaPlannerTaskPlan.model_validate(parsed.model_dump(mode="json"))


def generation_task_plan_schema() -> dict[str, Any]:
    return compact_generation_schema(GenerationTaskPlan.model_json_schema())


def compact_generation_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop display-only titles without altering validation or authoring rules.

    Recurse through Schema positions only: a property named `title`, defaults,
    examples and user constants must not be edited as if they were annotations.
    """
    result = deepcopy(schema)
    result.pop("title", None)
    for key in ("$defs", "properties", "patternProperties", "dependentSchemas"):
        if isinstance(result.get(key), dict):
            result[key] = {name: compact_generation_schema(value) if isinstance(value, dict) else value
                           for name, value in result[key].items()}
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [compact_generation_schema(value) if isinstance(value, dict) else value for value in result[key]]
    for key in ("items", "additionalProperties", "contains", "if", "then", "else", "not"):
        if isinstance(result.get(key), dict):
            result[key] = compact_generation_schema(result[key])
    return result


def config_schema_ref(kind: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/ModelConfig_{kind}"}


def _install_config(definitions: dict[str, Any], kind: str, available_kinds: set[str]) -> dict[str, str]:
    adapter = get_planner_node_adapter(kind)
    if adapter is None:
        raise ValueError("生成契约缺少节点 Adapter。")
    schema = deepcopy(adapter.config_model.model_json_schema())
    value_contract = write_value_source_contract(kind, available_kinds)
    if value_contract and not value_contract["input_available"]:
        schema["properties"]["value_source"] = {"type": "string", "const": "literal", "default": "literal"}
        # JSON Schema defaults do not change the legacy config parser's input default.
        schema["required"] = sorted(set(schema.get("required", [])) | {"value_source", "values"})
    nested = schema.pop("$defs", {})
    prefix = f"ModelConfig_{kind}"

    def relocate(value: Any) -> Any:
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: relocate(item) for key, item in value.items()}
        ref = result.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            result["$ref"] = f"#/$defs/{prefix}__{ref.removeprefix('#/$defs/')}"
        return result

    for name, value in nested.items():
        definitions[f"{prefix}__{name}"] = relocate(value)
    definitions[prefix] = relocate(schema)
    return config_schema_ref(kind)


def _adapters(request: Any, snapshot: Any) -> list[Any]:
    available = {item["kind"] for item in snapshot.nodes}
    return [adapter for kind in sorted(set(request.scope.allowed_node_kinds) & available)
            if (adapter := get_planner_node_adapter(kind)) is not None
            and planner_capability_metadata(kind) is not None]


def generation_node_kinds(request: Any, snapshot: Any) -> set[str]:
    return {adapter.kind for adapter in _adapters(request, snapshot)}


def _task_binding(adapter: Any) -> str:
    metadata = planner_capability_metadata(adapter.kind)
    if metadata is None:
        raise ValueError("生成契约的 Adapter 与 NodeContract 不一致。")
    return str(metadata["task_binding"])


def _binding(port_schema: dict[str, Any], direction: str, value_type: str = "any") -> dict[str, Any]:
    properties: dict[str, Any] = {"port": port_schema}
    if value_type != "any":
        properties["value_schema"] = {
            "properties": {"type": {"const": value_type}}, "required": ["type"],
        }
    return {"allOf": [
        {"$ref": f"#/$defs/GraphIntent{'Input' if direction == 'input' else 'Output'}BindingV3"},
        {"properties": properties, "required": ["value_schema"] if value_type != "any" else []},
    ]}


def _port_occurrence(name: str, *, required: bool, many: bool = False) -> dict[str, Any]:
    return {
        "contains": {"properties": {"port": {"const": name}}, "required": ["port"]},
        "minContains": int(required),
        **({} if many else {"maxContains": 1}),
    }


def _declared_type_envelope(
    definitions: dict[str, Any], name: str, target: WorkflowValueSchema,
) -> dict[str, str]:
    """Project declaration types; properties and source identity remain semantic gates."""
    ref = {"$ref": f"#/$defs/{name}"}
    if name in definitions:
        return ref
    definitions[name] = {"type": "object"}

    def variants(schema: WorkflowValueSchema) -> list[WorkflowValueSchema]:
        values = [item for member in schema.any_of for item in variants(member)] if schema.any_of else [schema]
        return [*values, WorkflowValueSchema(type="null")] if schema.nullable else values

    targets = variants(target)
    if any(item.type == "any" for item in targets):
        return ref
    alternatives = []
    for index, item in enumerate(targets):
        properties: dict[str, Any] = {
            "type": {"const": item.type}, "any_of": {"maxItems": 0},
        }
        required = ["type"]
        if item.type == "array" and item.items is not None:
            properties["items"] = _declared_type_envelope(definitions, f"{name}_items_{index}", item.items)
            required.append("items")
        alternatives.append({"properties": properties, "required": required})
    alternatives.append({
        "properties": {"any_of": {"type": "array", "minItems": 1, "items": ref}},
        "required": ["any_of"],
    })
    definitions[name] = {
        "type": "object",
        "anyOf": alternatives,
        **({} if any(item.type == "null" for item in targets) else {
            "properties": {"nullable": {"const": False}},
        }),
    }
    return ref


def _ports(adapter: Any, direction: str, definitions: dict[str, Any], available_kinds: set[str] | None = None) -> dict[str, Any]:
    ports = adapter.intent_port_contracts(direction)
    contract = adapter.model_binding_contract(available_kinds=available_kinds) if direction == "input" else None
    if contract:
        rule = contract["input_binding_contract"]
        names = [*rule["always"], *rule["when_value_source_input"]]
        choices: list[dict[str, Any]] = [{"enum": names}] if names else []
        if rule["filter_input_port_template"]:
            # Use the predicate ref pattern from the Adapter's own configuration schema.
            config_definitions = adapter.config_model.model_json_schema().get("$defs", {})
            predicate = config_definitions["DataTableQueryPredicatePlannerConfig"]
            pattern = predicate["properties"]["ref"]["pattern"]
            choices.append({"pattern": "^predicate_" + pattern.removeprefix("^")})
        result = {
            "type": "array", "maxItems": 50,
            "items": _binding({"anyOf": choices}, direction) if choices else False,
        }
        if rule["always"]:
            result["allOf"] = [_port_occurrence(name, required=True) for name in rule["always"]]
            for port in ports:
                if port.name not in rule["always"]:
                    continue
                declaration = _declared_type_envelope(
                    definitions, f"ModelInput_{adapter.kind}_{port.name}", port.value_schema,
                )
                result["allOf"].append({"items": {
                    "if": {"properties": {"port": {"const": port.name}}, "required": ["port"]},
                    "then": {"properties": {"value_schema": declaration}, "required": ["value_schema"]},
                }})
        return result
    if not ports:
        return {"type": "array", "maxItems": 0}
    result: dict[str, Any] = {
        "type": "array", "maxItems": 50 if direction == "input" else 16,
        "items": {"anyOf": [
            _binding({"const": port.name}, direction, port.value_schema.type if direction == "output" else "any")
            for port in ports
        ]},
        "allOf": (
            [_port_occurrence(name, required=minimum > 0, many=maximum is None)
             for name, (minimum, maximum) in adapter.input_port_counts().items()]
            if direction == "input" else
            [_port_occurrence(port.name, required=True, many=port.cardinality == "many") for port in ports]
        ),
    }
    if direction == "input":
        for port in ports:
            declaration = _declared_type_envelope(
                definitions, f"ModelInput_{adapter.kind}_{port.name}", port.value_schema,
            )
            result["allOf"].append({"items": {
                "if": {"properties": {"port": {"const": port.name}}, "required": ["port"]},
                "then": {"properties": {"value_schema": declaration}, "required": ["value_schema"]},
            }})
    return result


def graph_generation_schema(request: Any, snapshot: Any) -> dict[str, Any]:
    schema = deepcopy(GraphIntentV3.model_json_schema())
    definitions = schema["$defs"]
    base = deepcopy(definitions["GraphIntentNodeV3"])
    definitions["ModelGraphNodeBase"] = base
    variants = []
    adapters = _adapters(request, snapshot)
    available_kinds = {adapter.kind for adapter in adapters}
    for adapter in adapters:
        props: dict[str, Any] = {}
        node: dict[str, Any] = {"properties": props}
        props["kind"] = {"const": adapter.kind}
        props["ref"] = {"not": {"enum": ["input", "output"]}}
        props["config"] = _install_config(definitions, adapter.kind, available_kinds)
        props["inputs"] = _ports(adapter, "input", definitions, available_kinds)
        props["outputs"] = _ports(adapter, "output", definitions, available_kinds)
        binding = _task_binding(adapter)
        if binding == "forbidden":
            props["task_ids"] = {"maxItems": 0}
        elif binding == "required":
            props["task_ids"] = {"minItems": 1}
        if not adapter.resource_kind:
            props["resource_ref"] = {"type": "null"}
        required = {"config"}
        if adapter.resource_kind:
            props["resource_ref"] = {"$ref": "#/$defs/GraphIntentNodeResourceRefV3"}
            required.add("resource_ref")
        if binding == "required":
            required.add("task_ids")
        if adapter.intent_port_contracts("output"):
            required.add("outputs")
        table = adapter.model_binding_contract(available_kinds=available_kinds)
        if (table and table["input_binding_contract"]["always"]) or (
            not table and any(minimum for minimum, _ in adapter.input_port_counts().values())
        ):
            required.add("inputs")
        node["required"] = sorted(required)
        if table and table["input_binding_contract"]["when_value_source_input"]:
            values_required = {"properties": {"inputs": _port_occurrence("values", required=True)}, "required": ["inputs"]}
            values_forbidden = {"properties": {"inputs": {"not": {"contains": {"properties": {"port": {"const": "values"}}, "required": ["port"]}}}}}
            node["allOf"] = [{
                "if": {"properties": {"config": {"properties": {"value_source": {"const": "literal"}}, "required": ["value_source"]}}},
                "then": values_forbidden, "else": values_required,
            }]
        name = f"ModelNode_{adapter.kind}"
        definitions[name] = {"allOf": [{"$ref": "#/$defs/ModelGraphNodeBase"}, node]}
        variants.append({"$ref": f"#/$defs/{name}"})
    definitions["GraphIntentNodeV3"] = {"oneOf": variants} if variants else {"not": {}}
    scope_resource_schema(schema, request, snapshot)
    return compact_generation_schema(schema)


def patch_generation_schema(base_schema: dict[str, Any], request: Any, snapshot: Any, graph: Any) -> dict[str, Any]:
    schema = deepcopy(base_schema)
    definitions = schema["$defs"]
    add_base = deepcopy(definitions["AddNodeOperation"])
    update_base = deepcopy(definitions["UpdateNodeOperation"])
    definitions["ModelAddBase"] = add_base
    definitions["ModelUpdateBase"] = update_base
    adapters = _adapters(request, snapshot)
    available_kinds = {adapter.kind for adapter in adapters}
    configs = {adapter.kind: _install_config(definitions, adapter.kind, available_kinds) for adapter in adapters}
    add_variants = []
    for adapter in adapters:
        add: dict[str, Any] = {"properties": {}}
        add["properties"]["kind"] = {"const": adapter.kind}
        add["properties"]["config"] = configs[adapter.kind]
        if _task_binding(adapter) == "forbidden":
            add["properties"]["task_ids"] = {"maxItems": 0}
        name = f"ModelAdd_{adapter.kind}"
        definitions[name] = {"allOf": [{"$ref": "#/$defs/ModelAddBase"}, add]}
        add_variants.append({"$ref": f"#/$defs/{name}"})
    definitions["AddNodeOperation"] = {"oneOf": add_variants} if add_variants else {"not": {}}
    # Existing refs keep their kind; newly added refs still require an authorized config.
    updates = []
    for node in graph.nodes:
        if node.kind not in configs:
            continue
        update: dict[str, Any] = {"properties": {}}
        update["properties"]["ref"] = {"const": node.ref}
        update["properties"]["config"] = {"anyOf": [configs[node.kind], {"type": "null"}]}
        if _task_binding(get_planner_node_adapter(node.kind)) == "forbidden":
            update["properties"]["task_ids"] = {"anyOf": [{"type": "array", "maxItems": 0}, {"type": "null"}]}
        updates.append({"allOf": [{"$ref": "#/$defs/ModelUpdateBase"}, update]})
    future: dict[str, Any] = {"properties": {}}
    if graph.nodes:
        future["properties"]["ref"] = {"not": {"enum": [node.ref for node in graph.nodes]}}
    future["properties"]["config"] = {"anyOf": [*configs.values(), {"type": "null"}]}
    updates.append({"allOf": [{"$ref": "#/$defs/ModelUpdateBase"}, future]})
    definitions["UpdateNodeOperation"] = {"oneOf": updates}
    return compact_generation_schema(schema)
