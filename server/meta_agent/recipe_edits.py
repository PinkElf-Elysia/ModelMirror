"""Bounded model edits over a server-held Recipe, never a new graph authority."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Annotated, Any, Literal, Union

from pydantic import Field, model_validator

from .generation_contract import _install_config, compact_generation_schema, generation_node_kinds
from .generation_diagnostics import RecipeEditTargetError
from .generation_recipe import (
    FlowItem, GenerationRecipeV1, RecipeFinalOutput, RecipeInput, RecipeModel,
    REF, _bounded_payload, parse_generation_recipe,
)
from .node_adapters import get_planner_node_adapter
from ..workflow_native.node_contracts import canonical_checksum


RECIPE_EDIT_PROTOCOL = "recipe_edits_v1"
MAX_RECIPE_EDITS = 16
MAX_RECIPE_EDIT_BYTES = 64 * 1024
RECIPE_EDIT_SYSTEM_PROMPT = """你只为服务端保留的失败生成描述提供一次受限语义修复。
仅输出严格 JSON 对象 {"operations": [...]}，不得重写完整 Recipe 或 Native Workflow。
优先修复 repair_focus 中的原始来源和反例；未提交的部分由服务端保持原值。
只能使用给定操作、原节点 ref、命名端口和 Adapter 配置。资源、任务、模型身份与安全授权不能变更。
不能删除业务节点、篡改原业务条件或把互斥分支强行串行化以绕过校验。
Agent 的 inputs=null 表示从 role_prompt/task_input 中的 {{source_ref.source_port}} 派生输入；
显式数组则必须完整声明模板使用的来源。公共节点不能消费只在部分路径存在的值。
可复制已有无资源绑定 Agent 形成互斥分支结论，任务与模型由服务端继承；必须同时明确控制流和最终来源。
只修复确有依据的部分；无法修复时返回空 operations，服务端会明确记录未修复，不会自动重试。
用户可见标题、说明、Prompt 使用简体中文；ID、ref、字段名和错误码保持原值。
"""


class UpdateRecipeNode(RecipeModel):
    op: Literal["update_node"]
    node_ref: str = Field(pattern=REF)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] | None = None
    inputs: list[RecipeInput] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def explicit_changes(self):
        fields = self.model_fields_set - {"op", "node_ref"}
        if not fields or any(getattr(self, field) is None for field in fields - {"inputs"}):
            raise ValueError("节点修改必须显式指定非空变更；只有 Agent inputs 可设为 null。")
        return self


class CloneRecipeAgent(RecipeModel):
    op: Literal["clone_agent"]
    source_ref: str = Field(pattern=REF)
    ref: str = Field(pattern=REF)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    task_input: str = Field(min_length=1, max_length=8000)
    role_prompt: str | None = Field(default=None, min_length=1, max_length=20000)


class ReplaceRecipeFlow(RecipeModel):
    op: Literal["replace_control_flow"]
    control_flow: list[FlowItem] = Field(min_length=1, max_length=24)


class SetRecipeFinalOutput(RecipeModel):
    op: Literal["set_final_output"]
    final_output: RecipeFinalOutput


RecipeEdit = Annotated[Union[UpdateRecipeNode, CloneRecipeAgent, ReplaceRecipeFlow, SetRecipeFinalOutput], Field(discriminator="op")]


class RecipeEditsV1(RecipeModel):
    operations: list[RecipeEdit] = Field(max_length=MAX_RECIPE_EDITS)


def cloneable_agents(recipe: GenerationRecipeV1) -> list[str]:
    bound = {item.target_ref for item in [*recipe.resources, *recipe.middleware]}
    return sorted(node.ref for node in recipe.nodes
                  if node.kind == "workflow_agent" and node.resource_ref is None and node.ref not in bound)


def recipe_edit_contract(recipe: GenerationRecipeV1, request: Any, snapshot: Any) -> dict[str, Any]:
    """Project the same original-node boundary for the prompt, schema and merge."""
    kinds = generation_node_kinds(request, snapshot)
    schema = RecipeEditsV1.model_json_schema()
    commands = {}
    for op, ref in schema["properties"]["operations"]["items"]["discriminator"]["mapping"].items():
        definition = schema["$defs"][ref.rsplit("/", 1)[-1]]
        required = definition.get("required", [])
        commands[op] = {"required_fields": required,
                        "optional_fields": [key for key in definition["properties"] if key not in required]}
    return {
        "operations": commands,
        "max_operations": MAX_RECIPE_EDITS,
        "max_bytes": MAX_RECIPE_EDIT_BYTES,
        "update_node_refs": sorted(node.ref for node in recipe.nodes if node.kind in kinds),
        "cloneable_agent_refs": cloneable_agents(recipe),
        "cloned_nodes_editable": False,
        "clone_required_fields": [name for name, field in CloneRecipeAgent.model_fields.items()
                                  if name != "op" and field.is_required()],
    }


def recipe_edit_schema(recipe: GenerationRecipeV1, request: Any, snapshot: Any) -> dict[str, Any]:
    schema = RecipeEditsV1.model_json_schema()
    definitions = schema["$defs"]
    update = definitions["UpdateRecipeNode"]
    # Null is only meaningful for input inference; omission preserves every other field.
    for field in ("title", "description", "config"):
        update["properties"][field] = next(item for item in update["properties"][field]["anyOf"] if item.get("type") != "null")
    kinds = generation_node_kinds(request, snapshot)
    contract = recipe_edit_contract(recipe, request, snapshot)
    editable = set(contract["update_node_refs"])
    choices = []
    for kind in sorted({node.kind for node in recipe.nodes} & kinds):
        properties = {
            "node_ref": {"enum": sorted(node.ref for node in recipe.nodes if node.kind == kind and node.ref in editable)},
            "config": _install_config(definitions, kind, kinds),
        }
        if kind != "workflow_agent":
            properties["inputs"] = {"type": "array"}
        choices.append({"properties": properties})
    update["allOf"] = [{"oneOf": choices}] if choices else [False]
    update["anyOf"] = [{"required": [field]} for field in ("title", "description", "config", "inputs")]
    agents = contract["cloneable_agent_refs"]
    definitions["CloneRecipeAgent"]["properties"]["source_ref"] = {"enum": agents} if agents else False
    return compact_generation_schema(schema)


def apply_recipe_edits(recipe: GenerationRecipeV1, payload: Any, request: Any, snapshot: Any) -> GenerationRecipeV1:
    """Atomic structural merge only; callers must still lower, authorize and compile."""
    _bounded_payload(payload)
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_RECIPE_EDIT_BYTES:
        raise ValueError("语义修复超过 64 KiB。")
    edits = RecipeEditsV1.model_validate(payload)
    original = recipe.model_dump(mode="json")
    result = deepcopy(original)
    nodes = {node["ref"]: node for node in result["nodes"]}
    if len(nodes) != len(result["nodes"]):
        raise ValueError("原生成描述存在重复 ref，不能定向修改。")
    originals = {node["ref"]: node for node in original["nodes"]}
    contract = recipe_edit_contract(recipe, request, snapshot)
    editable = set(contract["update_node_refs"])
    cloneable = set(contract["cloneable_agent_refs"])
    touched = set()
    for operation_index, operation in enumerate(edits.operations):
        if isinstance(operation, UpdateRecipeNode):
            key = ("node", operation.node_ref)
            node = nodes.get(operation.node_ref)
            if node is None:
                raise RecipeEditTargetError("RECIPE_EDIT_UNKNOWN_NODE", operation_index, operation.node_ref)
            if operation.node_ref not in originals:
                raise RecipeEditTargetError("RECIPE_EDIT_CLONED_NODE_IMMUTABLE", operation_index, operation.node_ref)
            if operation.node_ref not in editable:
                raise RecipeEditTargetError("RECIPE_EDIT_NODE_UNAUTHORIZED", operation_index, operation.node_ref)
            if key in touched:
                raise ValueError("同一节点的修改必须合并为一个操作。")
            changes = operation.model_dump(mode="json", exclude_unset=True, exclude={"op", "node_ref"})
            config = changes.get("config")
            if config is not None:
                get_planner_node_adapter(node["kind"]).config_model.model_validate(config)
                if config.get("model_id") != node["config"].get("model_id"):
                    raise ValueError("语义修复不能替换节点模型身份。")
                if node["kind"] == "workflow_agent" and any(
                    config.get(field, default) != node["config"].get(field, default)
                    for field, default in (("source_agent_id", None), ("method_skill_ids", []))
                ):
                    raise ValueError("语义修复不能替换 Agent 身份或既定方法技能。")
            node.update(deepcopy(changes))
        elif isinstance(operation, CloneRecipeAgent):
            key = ("node", operation.ref)
            if operation.source_ref not in cloneable or operation.ref in nodes or operation.ref == "input":
                raise ValueError("只能复制原有无资源绑定 Agent，且新 ref 不能冲突。")
            node = deepcopy(originals[operation.source_ref])
            node.update(ref=operation.ref, title=operation.title, description=operation.description, inputs=None)
            node["config"]["task_input"] = operation.task_input
            if operation.role_prompt is not None:
                node["config"]["role_prompt"] = operation.role_prompt
            get_planner_node_adapter("workflow_agent").config_model.model_validate(node["config"])
            nodes[operation.ref] = node
            result["nodes"].append(node)
        else:
            field = "control_flow" if isinstance(operation, ReplaceRecipeFlow) else "final_output"
            key = (field,)
            if key in touched:
                raise ValueError("控制树和最终来源每批只能各修改一次。")
            result[field] = operation.model_dump(mode="json")[field]
        touched.add(key)
    merged = parse_generation_recipe(result)
    if canonical_checksum(merged.model_dump(mode="json")) == canonical_checksum(original):
        raise ValueError("RECIPE_REPAIR_UNCHANGED：语义修复没有改变原生成描述。")
    return merged
