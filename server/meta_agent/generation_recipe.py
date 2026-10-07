"""Private generation syntax lowered to the existing GraphIntent/Adapter boundary."""
from __future__ import annotations

from copy import deepcopy
from itertools import islice
import json
import re
from typing import Annotated, Any, Callable, Literal, Union

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .capabilities import assert_scope_is_authorized
from .control_flow import model_control_contract, semantic_outcomes
from .generation_contract import (
    _install_config, compact_generation_schema, generation_node_kinds,
)
from .generation_diagnostics import (
    GenerationDiagnostics, RecipeControlFlowError, RecipeEffectiveContractError,
    RecipeInputModeError, RecipeTemplateError,
)
from .resource_generation_contract import scope_resource_schema
from .graph_ir_v3 import graph_input_type_issue, resolve_node_resource_snapshot
from .node_adapters import get_planner_node_adapter, planner_capability_metadata, workflow_node_contract_registry
from .schemas import (
    GraphIntentNodeResourceRefV3, GraphIntentNodeV3,
    GraphIntentV3, MetaPlannerIRMiddlewareBinding, MetaPlannerIRResourceBinding,
)
try:
    from server.workflow_native.node_contracts import WorkflowValueSchema, canonical_checksum
except ModuleNotFoundError as exc:
    if exc.name != "server":
        raise
    from workflow_native.node_contracts import WorkflowValueSchema, canonical_checksum


GENERATION_PROTOCOL_VERSION = 1
MAX_FLOW_DEPTH = 8
REF = r"^[a-z][a-z0-9_-]{0,63}$"
PORT = r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$"
OUTCOME = r"^(?:success|error|matched|unmatched|case_[1-8]|default)$"


class RecipeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecipeInput(RecipeModel):
    port: str = Field(pattern=PORT)
    source_ref: str = Field(pattern=REF)
    source_port: str = Field(pattern=PORT)


def recipe_input_contract(kind: str) -> dict[str, Any]:
    template_sources = kind == "workflow_agent"
    return {
        "mode": "template_or_explicit" if template_sources else "explicit",
        "null_allowed": template_sources,
        "no_bindings": [],
        "required_ports_still_apply": True,
    }


class RecipeNode(RecipeModel):
    ref: str = Field(pattern=REF)
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    task_ids: list[str] = Field(default_factory=list, max_length=8)
    inputs: list[RecipeInput] | None = Field(default_factory=list, max_length=50)
    config: dict[str, Any]
    resource_ref: GraphIntentNodeResourceRefV3 | None = None

    @model_validator(mode="after")
    def validate_input_mode(self):
        if self.inputs is None and not recipe_input_contract(self.kind)["null_allowed"]:
            raise RecipeInputModeError()
        return self


class FlowBranch(RecipeModel):
    outcome_ref: str = Field(pattern=OUTCOME)
    steps: list["FlowItem"] = Field(max_length=24)


class FlowNode(RecipeModel):
    type: Literal["node"] = "node"
    node_ref: str = Field(pattern=REF)
    branches: list[FlowBranch] = Field(default_factory=list, max_length=9)


class FlowParallel(RecipeModel):
    type: Literal["parallel"]
    paths: list[list["FlowItem"]] = Field(min_length=2, max_length=8)


FlowItem = Annotated[Union[FlowNode, FlowParallel], Field(discriminator="type")]
FlowBranch.model_rebuild()
FlowNode.model_rebuild()
FlowParallel.model_rebuild()


class RecipeFinalSource(RecipeModel):
    node_ref: str = Field(pattern=REF)
    port: str = Field(default="result", pattern=PORT)


class RecipeFinalOutput(RecipeModel):
    sources: list[RecipeFinalSource] = Field(min_length=1, max_length=8)
    selection_policy: Literal["exactly_one_arrived"] = "exactly_one_arrived"


class GenerationRecipeV1(RecipeModel):
    generation_protocol_version: Literal[1]
    ir_version: Literal[3] = 3
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    starters: list[str] = Field(default_factory=list, max_length=8)
    nodes: list[RecipeNode] = Field(min_length=1, max_length=24)
    control_flow: list[FlowItem] = Field(min_length=1, max_length=24)
    resources: list[MetaPlannerIRResourceBinding] = Field(default_factory=list, max_length=40)
    middleware: list[MetaPlannerIRMiddlewareBinding] = Field(default_factory=list, max_length=40)
    prompt_profile_ids: list[str] = Field(default_factory=list, max_length=20)
    final_output: RecipeFinalOutput

    @field_validator("generation_protocol_version", mode="before")
    @classmethod
    def reject_boolean_protocol(cls, value):
        if isinstance(value, bool):
            raise ValueError("生成协议版本不能使用布尔值。")
        return value


def recipe_schema(request: Any, snapshot: Any) -> dict[str, Any]:
    """Use the same Adapter config schemas, not a second configuration language."""
    schema = GenerationRecipeV1.model_json_schema()
    definitions = schema["$defs"]
    kinds = generation_node_kinds(request, snapshot)
    base = definitions.pop("RecipeNode")
    definitions["ModelRecipeNodeBase"] = base
    alternatives = []
    for kind in sorted(kinds):
        adapter = get_planner_node_adapter(kind)
        node = {"properties": {"kind": {"const": kind},
                "config": _install_config(definitions, kind, kinds)}, "required": list(base["required"])}
        if not recipe_input_contract(kind)["null_allowed"]:
            node["properties"]["inputs"] = {"type": "array"}
        metadata = planner_capability_metadata(kind)
        if metadata["task_binding"] == "forbidden":
            node["properties"]["task_ids"] = {"maxItems": 0}
        else:
            node["properties"]["task_ids"] = {"minItems": 1}
            node["required"] = sorted(set(node["required"]) | {"task_ids"})
        if adapter.resource_kind:
            node["properties"]["resource_ref"] = {"$ref": "#/$defs/GraphIntentNodeResourceRefV3"}
            node["required"] = sorted(set(node["required"]) | {"resource_ref"})
        else:
            node["properties"]["resource_ref"] = {"type": "null"}
        definitions[f"ModelNode_{kind}"] = {
            "allOf": [{"$ref": "#/$defs/ModelRecipeNodeBase"}, node],
            "required": node["required"],
        }
        alternatives.append({"$ref": f"#/$defs/ModelNode_{kind}"})
    schema["properties"]["nodes"]["items"] = {"oneOf": alternatives} if alternatives else False
    scope_resource_schema(schema, request, snapshot)
    return compact_generation_schema(schema)


def validate_recipe_generation_contract(payload: dict[str, Any], request: Any, snapshot: Any) -> None:
    """Enforce the offered schema without changing the legacy Recipe reader."""
    _bounded_payload(payload)
    schema = recipe_schema(request, snapshot)
    validator = Draft202012Validator(schema)
    choices = schema["properties"]["nodes"]["items"]
    allowed_refs = {item["$ref"] for item in choices.get("oneOf", [])} if isinstance(choices, dict) else set()
    errors, seen = [], set()

    def add(error, prefix=()):
        location = (*prefix, *error.absolute_path)
        locations = [location]
        if error.validator == "required" and isinstance(error.instance, dict):
            locations = [(*location, name) for name in error.validator_value if name not in error.instance]
        for path in locations:
            constraint = error.validator or "false_schema"
            if (path, constraint) in seen or len(errors) >= 64:
                continue
            seen.add((path, constraint))
            # Do not copy validator messages, enum values, Schema bodies or input data.
            errors.append({"type": "value_error", "loc": path,
                           "ctx": {"error": RecipeEffectiveContractError(constraint)}})

    for error in islice(validator.iter_errors(payload), 64):
        location = tuple(error.absolute_path)
        if error.validator == "oneOf" and len(location) == 2 and location[0] == "nodes" and isinstance(error.instance, dict):
            ref = f"#/$defs/ModelNode_{error.instance.get('kind')}"
            if ref in allowed_refs:
                # Diagnose the declared kind, not every unrelated oneOf alternative.
                selected = Draft202012Validator({"$defs": schema["$defs"], "$ref": ref})
                for issue in islice(selected.iter_errors(error.instance), 64):
                    add(issue, location)
                continue
        add(error)
    if errors:
        raise ValidationError.from_exception_data("GenerationRecipeContract", errors)


def recipe_node_contracts(request: Any, snapshot: Any) -> dict[str, Any]:
    """Only semantic choices and port descriptions are shown to the generator."""
    result = {}
    for kind in sorted(generation_node_kinds(request, snapshot)):
        adapter = get_planner_node_adapter(kind)
        result[kind] = {
            "task_binding": planner_capability_metadata(kind)["task_binding"],
            "input_mode": recipe_input_contract(kind),
            "inputs": [{"port": port.name, "type": port.value_schema.model_dump(mode="json", exclude_defaults=True),
                        "required": port.required, "cardinality": port.cardinality}
                       for port in adapter.intent_port_contracts("input")],
            "outputs": [{"port": port.name, "type": port.value_schema.model_dump(mode="json", exclude_defaults=True)}
                        for port in adapter.intent_port_contracts("output")],
            "resource_kind": adapter.resource_kind,
            "control_contract": model_control_contract(kind, adapter.config_model.model_json_schema()),
        }
        binding = adapter.model_binding_contract(available_kinds=generation_node_kinds(request, snapshot))
        if binding:
            # Port selection is semantic; variable and Schema declarations are not.
            contract = binding["input_binding_contract"]
            result[kind]["configured_inputs"] = {
                key: value for key, value in contract.items() if key not in {"rules", "records_schema_from"}
            }
            result[kind]["output_binding_contract"] = binding["output_binding_contract"]
        if kind == "workflow_agent":
            result[kind]["text_inputs"] = {
                "mode": "template_sources", "selector": {"inputs": None},
                "source_syntax": "{{source_ref.source_port}}",
                "fields": ["role_prompt", "task_input"],
                "rules": "推荐 inputs=null：模板来源自动去重；非字符串经已授权的 JSON Serialize V2 转为紧凑 JSON。数组形式保持严格显式绑定。",
                "write_request_evidence": {
                    "trigger": "显式消费 Update result，可经过 JSON Serialize。",
                    "literal": "编译器附带同一 Update 的固定请求值，不必在 Prompt 中手工抄写。",
                    "input": "只标记已显式接入的同一 JSON Deserialize V2 来源；未接入时明确缺失，不新增连线。",
                    "limits": "请求值不是写后状态证据；不扩大回执、不添加查询，仍受原 Prompt 长度限制。",
                },
            }
    return result


def _bounded_payload(payload: Any) -> None:
    pending = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 32:
            raise ValueError("生成描述嵌套超过 32 层。")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("生成描述超过 1 MiB。")


def _output_variable(ref: str, port: str) -> str:
    return "v_" + canonical_checksum([ref, port])[:32]


def _template_source(expression: str, sources: set[tuple[str, str]], *,
                     node_ref: str, location: list[str | int], reference_index: int | None = None,
                     node_outputs: dict[str, tuple[str, tuple[str, ...]]] | None = None) -> tuple[str, str]:
    parts = expression.split(".")
    source = (parts[0], parts[1]) if len(parts) == 2 else None
    if source is None or source not in sources:
        code = "RECIPE_TEMPLATE_REFERENCE_INVALID" if source is None else "RECIPE_TEMPLATE_SOURCE_UNKNOWN"
        known = (node_outputs or {}).get(parts[0])
        reason = "invalid_reference_syntax" if source is None else "unknown_source" if known is None else (
            "source_has_no_data_outputs" if not known[1] else "unknown_output_port")
        raise RecipeTemplateError(code, node_ref, location, expression, reference_index=reference_index,
                                  known_source=(parts[0], *known) if known else None, reason=reason)
    return source


def _template(template: str, bindings: dict[tuple[str, str], str], sources: set[tuple[str, str]], *,
              node_ref: str, location: list[str | int], on_error: Callable[[RecipeTemplateError], None],
              node_outputs: dict[str, tuple[str, tuple[str, ...]]]) -> str:
    reference_index = -1
    def replace(match: re.Match[str]) -> str:
        nonlocal reference_index
        reference_index += 1
        expression = match.group(1).strip()
        try:
            source = _template_source(expression, sources, node_ref=node_ref, location=location,
                                      reference_index=reference_index, node_outputs=node_outputs)
            if source not in bindings:
                raise RecipeTemplateError("RECIPE_TEMPLATE_INPUT_MISSING", node_ref, location, expression,
                                          source=source, reference_index=reference_index)
            return "{{" + bindings[source] + "}}"
        except RecipeTemplateError as error:
            on_error(error)
        return match.group(0)

    return re.sub(r"\{\{\s*(.*?)\s*\}\}", replace, template, flags=re.DOTALL)


def _derived_agent_inputs(item: RecipeNode, index: int, sources: set[tuple[str, str]]) -> list[RecipeInput]:
    unique: dict[tuple[str, str], RecipeInput] = {}
    for field in ("role_prompt", "task_input"):
        for match in re.finditer(r"\{\{\s*(.*?)\s*\}\}", item.config.get(field, ""), flags=re.DOTALL):
            try:
                source = _template_source(match.group(1).strip(), sources,
                                          node_ref=item.ref, location=["nodes", index, "config", field])
            except RecipeTemplateError:
                # Keep the original placeholder. The common template pass rejects it
                # after trusted preparation, together with independent structural errors.
                continue
            unique.setdefault(source, RecipeInput(port="task", source_ref=source[0], source_port=source[1]))
            if len(unique) > 50:
                raise ValueError(f"节点 {item.ref} 的模板输入超过 50 个。")
    return list(unique.values())


def _recipe_input_origins(recipe: GenerationRecipeV1, nodes: dict[str, GraphIntentNodeV3]) -> dict:
    origins = {}
    for index, item in enumerate(recipe.nodes):
        template_locations: dict[tuple[str, str], list] = {}
        if item.inputs is None:
            for field in ("role_prompt", "task_input"):
                for match in re.finditer(r"\{\{\s*(.*?)\s*\}\}", item.config.get(field, ""), flags=re.DOTALL):
                    source = tuple(match.group(1).strip().split("."))
                    location = ["nodes", index, "config", field]
                    locations = template_locations.setdefault(source, [])
                    if location not in locations:
                        locations.append(location)
        for input_index, binding in enumerate(nodes[item.ref].inputs):
            origins[(item.ref, input_index)] = {
                "node_ref": item.ref, "source_ref": binding.source_ref, "source_port": binding.source_port,
                "locations": template_locations.get((binding.source_ref, binding.source_port), [])
                    if item.inputs is None else [["nodes", index, "inputs", input_index]],
            }
    return origins


def _adapt_agent_text_inputs(recipe: GenerationRecipeV1, nodes: dict[str, GraphIntentNodeV3],
                             request: Any, snapshot: Any, input_origins: dict | None = None):
    """Insert branch-local, authorized native serializers, never business decisions."""
    adapted, chains = dict(nodes), {}
    allowed = generation_node_kinds(request, snapshot)
    for index, item in enumerate(recipe.nodes):
        if item.inputs is not None:
            continue
        node = nodes[item.ref]
        target = next(port.value_schema for port in get_planner_node_adapter(node.kind).intent_port_contracts("input")
                      if port.name == "task")
        inputs, replacements, chain = [], {}, []
        for input_index, binding in enumerate(node.inputs):
            if graph_input_type_issue(index, input_index, node.ref, binding, binding.value_schema, target) is None:
                inputs.append(binding)
                continue
            if "json_serialize" not in allowed:
                raise ValueError(f"节点 {node.ref} 的文本适配需要显式授权 json_serialize。")
            ref = "text_" + canonical_checksum([node.ref, binding.source_ref, binding.source_port])[:24]
            if ref in adapted:
                raise ValueError(f"节点 {node.ref} 的编译器文本适配 ref 冲突。")
            if len(adapted) >= 24:
                raise ValueError("文本适配后超过 24 个节点。")
            adapter = get_planner_node_adapter("json_serialize")
            config = adapter.config_model.model_validate({"format": "compact"})
            schema = adapter.authoritative_output_schema("json", config, None)
            variable = _output_variable(ref, "json")
            adapted[ref] = GraphIntentNodeV3(
                ref=ref, kind="json_serialize", title="结果转为文本",
                description="将声明的上游结果转为紧凑 JSON，供下游智能体消费。",
                inputs=[binding.model_copy(update={"port": "value"})],
                outputs=[{"port": "json", "variable": variable, "value_schema": schema}],
                config=config.model_dump(mode="json"),
            )
            if input_origins is not None:
                input_origins[(ref, 0)] = deepcopy(input_origins[(node.ref, input_index)])
            inputs.append(binding.model_copy(update={"source_ref": ref, "source_port": "json",
                                                     "variable": variable, "value_schema": schema}))
            replacements[(binding.source_ref, binding.source_port)] = ref
            chain.append(ref)
        config = deepcopy(node.config)
        for field in ("role_prompt", "task_input"):
            def replace(match: re.Match[str]) -> str:
                ref = replacements.get(tuple(match.group(1).strip().split(".")))
                return "{{" + ref + ".json}}" if ref else match.group(0)
            config[field] = re.sub(r"\{\{\s*(.*?)\s*\}\}", replace, config[field], flags=re.DOTALL)
        adapted[node.ref] = node.model_copy(update={"inputs": inputs, "config": config})
        if chain:
            chains[node.ref] = chain
    return adapted, chains


def _bridge_control_edges(edges: list[dict[str, str]], chains: dict[str, list[str]]) -> list[dict[str, str]]:
    # Preserve incoming semantic outcomes; the normal analyzer proves data availability.
    bridged = {(edge["source_ref"], edge["outcome_ref"],
                chains[edge["target_ref"]][0] if edge["target_ref"] in chains else edge["target_ref"])
               for edge in edges}
    for consumer, helpers in chains.items():
        bridged.update((left, "success", right) for left, right in zip(helpers, [*helpers[1:], consumer]))
    if len(bridged) > 40:
        raise ValueError("文本适配后超过 40 条控制边。")
    return [{"source_ref": source, "outcome_ref": outcome, "target_ref": target}
            for source, outcome, target in sorted(bridged)]


def _lower_recipe_templates(nodes: dict[str, GraphIntentNodeV3], diagnostics: GenerationDiagnostics | None):
    sources = {(node.ref, output.port) for node in nodes.values() for output in node.outputs}
    sources.update(("input", port.name) for port in workflow_node_contract_registry.require("input").ports
                   if port.direction == "output")
    sources.update((item.source_ref, item.source_port) for node in nodes.values() for item in node.inputs)
    node_outputs = {node.ref: (node.kind, tuple(output.port for output in node.outputs)) for node in nodes.values()}
    node_outputs["input"] = ("input", tuple(sorted(port for ref, port in sources if ref == "input")))
    first_error, count = None, 0

    def report(error: RecipeTemplateError) -> None:
        nonlocal first_error, count
        first_error = first_error or error
        count += 1
        if diagnostics is not None:
            diagnostics.exception(error)

    lowered = dict(nodes)
    for index, node in enumerate(nodes.values()):
        if node.kind != "workflow_agent":
            continue
        config = deepcopy(node.config)
        bindings = {(item.source_ref, item.source_port): item.variable for item in node.inputs}
        for field in ("role_prompt", "task_input"):
            config[field] = _template(config[field], bindings, sources, node_ref=node.ref,
                location=["nodes", index, "config", field], on_error=report, node_outputs=node_outputs)
        lowered[node.ref] = node.model_copy(update={"config": config})
    return lowered, first_error, count


def _control_edges(recipe: GenerationRecipeV1, nodes: dict[str, GraphIntentNodeV3]) -> list[dict[str, str]]:
    seen: dict[str, list[str | int]] = {}
    edges: set[tuple[str, str, str]] = set()

    def connect(tails: list[tuple[str, str]], ref: str) -> None:
        edges.update((source, outcome, ref) for source, outcome in tails)
        if len(edges) > 40:
            raise ValueError("结构化控制流编译超过 40 条边。")

    def sequence(steps: list[FlowItem], incoming: list[tuple[str, str]], depth: int, path: list[str | int]) -> list[tuple[str, str]]:
        if depth > MAX_FLOW_DEPTH:
            raise ValueError("结构化控制流超过 8 层。")
        tails = incoming
        for index, step in enumerate(steps):
            location = [*path, index]
            if index and not tails:
                raise RecipeControlFlowError("RECIPE_AFTER_TERMINAL",
                    step.node_ref if isinstance(step, FlowNode) else "", location)
            if isinstance(step, FlowParallel):
                if any(not path for path in step.paths):
                    raise ValueError("并行路径不能为空。")
                tails = [tail for branch_index, branch in enumerate(step.paths)
                         for tail in sequence(branch, tails, depth + 1, [*location, "paths", branch_index])]
                continue
            ref = step.node_ref
            ref_location = [*location, "node_ref"]
            if ref not in nodes:
                raise RecipeControlFlowError("RECIPE_UNKNOWN_NODE", ref, ref_location)
            if ref in seen:
                raise RecipeControlFlowError("RECIPE_REPEATED_NODE", ref, ref_location, first_location=seen[ref])
            seen[ref] = ref_location
            connect(tails, ref)
            outcomes = semantic_outcomes(nodes[ref])
            if len(outcomes) > 1:
                declared = [branch.outcome_ref for branch in step.branches]
                if len(declared) != len(set(declared)) or set(declared) != set(outcomes):
                    raise RecipeControlFlowError("RECIPE_BRANCH_OUTCOMES_MISMATCH", ref,
                        [*location, "branches"], expected_outcomes=list(outcomes), actual_outcomes=declared)
                tails = [tail for branch_index, branch in enumerate(step.branches)
                         for tail in sequence(branch.steps, [(ref, branch.outcome_ref)], depth + 1,
                                              [*location, "branches", branch_index, "steps"])]
            else:
                if step.branches:
                    raise RecipeControlFlowError("RECIPE_BRANCH_OUTCOMES_MISMATCH", ref,
                        [*location, "branches"], expected_outcomes=[],
                        actual_outcomes=[branch.outcome_ref for branch in step.branches])
                tails = [(ref, outcome) for outcome in outcomes]
        return tails

    tails = sequence(recipe.control_flow, [], 1, ["control_flow"])
    for index, node in enumerate(recipe.nodes):
        if node.ref not in seen:
            raise RecipeControlFlowError("RECIPE_OMITTED_NODE", node.ref, ["nodes", index, "ref"])
    # An empty branch is a declared bypass, not permission to omit an outcome.
    for ref, outcome in tails:
        if len(semantic_outcomes(nodes[ref])) > 1:
            raise ValueError(f"分支 {ref}:{outcome} 缺少后续节点或终点。")
    return [{"source_ref": source, "outcome_ref": outcome, "target_ref": target}
            for source, outcome, target in sorted(edges)]


def parse_generation_recipe(payload: Any) -> GenerationRecipeV1:
    _bounded_payload(payload)
    return GenerationRecipeV1.model_validate(payload)


def _prepare_recipe_nodes(recipe: GenerationRecipeV1, request: Any, snapshot: Any) -> dict[str, GraphIntentNodeV3]:
    """Resolve trusted config, resource and port facts before Prompt lowering."""
    assert_scope_is_authorized(request.scope, snapshot)
    allowed_kinds = generation_node_kinds(request, snapshot)
    refs = [item.ref for item in recipe.nodes]
    if len(refs) != len(set(refs)) or {"input", "output"} & set(refs):
        raise ValueError("节点 ref 必须唯一且不能占用编译器 input/output。")
    nodes: dict[str, GraphIntentNodeV3] = {}
    authority: dict[str, tuple[Any, Any, Any]] = {}
    variables = {("input", port.name): (port.name, port.value_schema)
                 for port in workflow_node_contract_registry.require("input").ports if port.direction == "output"}
    if any(item.kind == "vision_understanding" for item in recipe.nodes):
        from .vision_contract import ATTACHMENT_INPUT_PORT
        variables[("input", ATTACHMENT_INPUT_PORT)] = (ATTACHMENT_INPUT_PORT, WorkflowValueSchema(type="string"))
    for item in recipe.nodes:
        if item.kind not in allowed_kinds:
            raise ValueError(f"节点 {item.ref} 的类型未获授权或没有可用 Adapter。")
        adapter = get_planner_node_adapter(item.kind)
        parsed = adapter.config_model.model_validate(item.config)
        if item.resource_ref:
            resource_id = item.resource_ref.resource_id
            allowed_ids = set(request.scope.knowledge_base_ids) if adapter.resource_kind == "knowledge_base" else set(request.scope.data_table_ids)
            if item.kind in {"data_table_insert", "data_table_update", "data_table_delete"}:
                operation = item.kind.removeprefix("data_table_")
                allowed_ids = {grant.table_id for grant in request.scope.data_table_write_grants if operation in grant.operations}
            if not adapter.resource_kind or resource_id not in allowed_ids:
                raise ValueError(f"节点 {item.ref} 的资源未获对应操作授权。")
        node = GraphIntentNodeV3(**item.model_dump(exclude={"inputs"}))
        resource = resolve_node_resource_snapshot(node, snapshot)
        resource_payload = resource.model_dump(mode="json") if resource else None
        authority[item.ref] = (adapter, parsed, resource_payload)
        outputs = []
        for port in adapter.intent_port_contracts("output"):
            schema = adapter.authoritative_output_schema(port.name, parsed, resource_payload)
            variable = _output_variable(item.ref, port.name)
            variables[(item.ref, port.name)] = (variable, schema)
            outputs.append({"port": port.name, "variable": variable, "value_schema": schema})
        nodes[item.ref] = GraphIntentNodeV3(**item.model_dump(exclude={"inputs"}), outputs=outputs)
    for index, item in enumerate(recipe.nodes):
        inputs = []
        bindings = item.inputs if item.inputs is not None else _derived_agent_inputs(item, index, set(variables))
        for binding in bindings:
            source = variables.get((binding.source_ref, binding.source_port))
            if source is None:
                raise ValueError(f"节点 {item.ref} 的输入来源不存在。")
            inputs.append({**binding.model_dump(), "variable": source[0], "value_schema": source[1]})
        nodes[item.ref] = GraphIntentNodeV3(**item.model_dump(exclude={"inputs"}),
            inputs=inputs, outputs=nodes[item.ref].outputs)
        # Keep the same resource validator, after its typed predicate inputs exist.
        adapter, parsed, resource_payload = authority[item.ref]
        adapter.validate_resolved_resource(nodes[item.ref], parsed, resource_payload)
    return nodes


def lower_generation_recipe(payload: Any, request: Any, snapshot: Any, *,
                            diagnostics: GenerationDiagnostics | None = None,
                            input_origins: dict | None = None) -> GraphIntentV3:
    """Derive mechanics only. Callers must still run every existing semantic gate."""
    from .recipe_preflight import known_recipe_source_dependencies, local_recipe_facts, recipe_occurrence_dependencies

    # Provenance is regenerated from this strict Recipe, never supplied by the model.
    origins = input_origins if input_origins is not None else {}
    origins.clear()
    recipe = parse_generation_recipe(payload.model_dump(mode="python") if isinstance(payload, GenerationRecipeV1) else payload)
    if diagnostics is not None:
        diagnostics.recipe_preflight = {"status": "blocked", "blocked_by": "node_preparation"}
    base_nodes = _prepare_recipe_nodes(recipe, request, snapshot)
    origins.update(_recipe_input_origins(recipe, base_nodes))
    # Validate the original namespace before introducing compiler-only helpers.
    # Rendering happens later, but unknown references can never acquire authority.
    _, first_error, invalid_references = _lower_recipe_templates(base_nodes, diagnostics)
    nodes, text_chains = _adapt_agent_text_inputs(recipe, base_nodes, request, snapshot, origins)
    if diagnostics is not None:
        diagnostics.recipe_preflight = local_recipe_facts(list(nodes.values()))
        diagnostics.recipe_preflight["text_input_lowering"] = {
            "derived_agent_count": sum(item.inputs is None for item in recipe.nodes),
            "serializer_count": sum(len(chain) for chain in text_chains.values()),
        }
    if first_error is None:
        nodes, first_error, invalid_references = _lower_recipe_templates(nodes, diagnostics)
    # Structural checks do not depend on successful Prompt interpolation.
    try:
        control_edges = _bridge_control_edges(_control_edges(recipe, base_nodes), text_chains)
        control_status = "passed"
    except ValueError as error:
        control_status = "failed"
        first_error = first_error or error
        if diagnostics is not None:
            diagnostics.exception(error)
    if diagnostics is not None:
        diagnostics.recipe_preflight.update(
            template_bindings_status="failed" if invalid_references else "passed",
            invalid_template_reference_count=invalid_references,
            control_structure_status=control_status,
        )
    if first_error is not None:
        if diagnostics is not None and control_status == "failed":
            diagnostics.recipe_preflight["occurrence_dependencies"] = recipe_occurrence_dependencies(
                recipe, base_nodes, origins, diagnostics.recipe_preflight)
        if diagnostics is not None and invalid_references:
            diagnostics.recipe_preflight["path_proof_status"] = "blocked"
            if control_status == "passed":
                diagnostics.recipe_preflight["known_source_dependencies"] = known_recipe_source_dependencies(
                    recipe, nodes, control_edges, origins, diagnostics.recipe_preflight)
        raise first_error
    return GraphIntentV3(**recipe.model_dump(exclude={"generation_protocol_version", "nodes", "control_flow"}),
        nodes=list(nodes.values()), control_edges=control_edges)
