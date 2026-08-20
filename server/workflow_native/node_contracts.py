from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import NativeNodeKind


NODE_CONTRACT_VERSION = 3

WorkflowValueType = Literal[
    "any",
    "null",
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
]
NodeContractStatus = Literal["complete", "compatibility"]
NodePortDirection = Literal["input", "output"]
NodePortCardinality = Literal["one", "many"]
NodePortBinding = Literal["variable", "literal", "resource", "none"]
NodeEdgeMode = Literal["control", "binding", "metadata"]
NodeSideEffect = Literal[
    "none",
    "read",
    "write",
    "external_read",
    "external_write",
    "code_execution",
    "unknown",
]
NodeErrorSemantics = Literal[
    "exception_strategy",
    "fail_closed",
    "ignored",
    "legacy_inline_error",
    "unknown",
]
NodeAvailabilityState = Literal["allow", "deny", "conditional"]
NodePlannerSupport = Literal[
    "full",
    "binding_only",
    "metadata_only",
    "unsupported",
]
NodePlannerCompilationMode = Literal[
    "adapter",
    "binding",
    "compiler_managed",
    "none",
]


def canonical_checksum(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class WorkflowValueSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: WorkflowValueType = "any"
    nullable: bool = False
    items: "WorkflowValueSchema | None" = None
    properties: dict[str, "WorkflowValueSchema"] = Field(default_factory=dict)
    required: tuple[str, ...] = ()
    any_of: tuple["WorkflowValueSchema", ...] = ()

    @model_validator(mode="after")
    def validate_shape(self) -> "WorkflowValueSchema":
        if self.items is not None and self.type != "array":
            raise ValueError("items is only valid for array schemas")
        if (self.properties or self.required) and self.type != "object":
            raise ValueError("properties and required are only valid for object schemas")
        if any(name not in self.properties for name in self.required):
            raise ValueError("required properties must be declared")
        if len(self.any_of) > 4:
            raise ValueError("any_of supports at most four alternatives")
        return self


class NodePortContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    direction: NodePortDirection
    value_schema: WorkflowValueSchema = Field(default_factory=WorkflowValueSchema)
    required: bool = False
    cardinality: NodePortCardinality = "one"
    binding: NodePortBinding = "variable"


class NodeEdgeContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    modes: tuple[NodeEdgeMode, ...] = ("control",)
    topology_modes: tuple[NodeEdgeMode, ...] = ("control",)
    allowed_source_handles: tuple[str, ...] = ()
    allowed_target_handles: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_modes(self) -> "NodeEdgeContract":
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("edge modes must be unique")
        if len(set(self.topology_modes)) != len(self.topology_modes):
            raise ValueError("topology modes must be unique")
        if any(mode not in self.modes for mode in self.topology_modes):
            raise ValueError("topology modes must be supported edge modes")
        return self

    def supports(self, mode: NodeEdgeMode) -> bool:
        return mode in self.modes


class NodeExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    side_effect: NodeSideEffect = "unknown"
    deterministic: bool = False
    idempotent: bool = False
    external_io: bool = False
    can_wait: bool = False
    error_semantics: NodeErrorSemantics = "unknown"
    security_category: str = "legacy"


class NodeAvailabilityRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: NodeAvailabilityState = "conditional"
    code: str = ""
    message: str = ""


class NodeAvailabilityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow: NodeAvailabilityRule = Field(
        default_factory=lambda: NodeAvailabilityRule(state="allow")
    )
    xpert: NodeAvailabilityRule = Field(
        default_factory=lambda: NodeAvailabilityRule(state="allow")
    )
    goal: NodeAvailabilityRule = Field(
        default_factory=lambda: NodeAvailabilityRule(state="allow")
    )
    handoff: NodeAvailabilityRule = Field(
        default_factory=lambda: NodeAvailabilityRule(state="allow")
    )
    app: NodeAvailabilityRule = Field(default_factory=NodeAvailabilityRule)
    evaluation: NodeAvailabilityRule = Field(default_factory=NodeAvailabilityRule)
    evolution: NodeAvailabilityRule = Field(
        default_factory=lambda: NodeAvailabilityRule(state="deny")
    )


class NodeResourceContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    id_field: str
    required: bool = True
    version_policy_field: str | None = None
    pinned_version_field: str | None = None
    dynamic_schema: bool = False


class NodePlannerContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    support: NodePlannerSupport = "unsupported"
    compilation_mode: NodePlannerCompilationMode = "none"
    ir_version: int = 2
    adapter_version: str = ""
    default_data: dict[str, Any] = Field(default_factory=dict)
    config_constraints: dict[str, Any] = Field(default_factory=dict)
    ir_config_schema: dict[str, Any] = Field(default_factory=dict)


class NodeContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    contract_status: NodeContractStatus = "compatibility"
    config_schema: dict[str, Any] = Field(default_factory=dict)
    ports: tuple[NodePortContract, ...] = ()
    edge: NodeEdgeContract = Field(default_factory=NodeEdgeContract)
    execution: NodeExecutionPolicy = Field(default_factory=NodeExecutionPolicy)
    availability: NodeAvailabilityPolicy = Field(default_factory=NodeAvailabilityPolicy)
    resources: tuple[NodeResourceContract, ...] = ()
    planner: NodePlannerContract = Field(default_factory=NodePlannerContract)
    deprecated: bool = False
    replacement_kind: str | None = None

    @model_validator(mode="after")
    def validate_complete_contract(self) -> "NodeContract":
        if self.contract_status == "complete" and not self.config_schema:
            raise ValueError(f"complete node contract {self.kind} needs config_schema")
        if self.planner.enabled and self.contract_status != "complete":
            raise ValueError("planner-enabled nodes need complete contracts")
        if self.planner.enabled and self.planner.support == "unsupported":
            raise ValueError("planner-enabled nodes cannot be unsupported")
        return self

    def compiler_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "contract_version": NODE_CONTRACT_VERSION,
            "config_schema": self.config_schema,
            "ports": [port.model_dump(mode="json") for port in self.ports],
            "edge": self.edge.model_dump(mode="json"),
            "resources": [resource.model_dump(mode="json") for resource in self.resources],
            "planner": {
                "support": self.planner.support,
                "compilation_mode": self.planner.compilation_mode,
                "ir_version": self.planner.ir_version,
                "adapter_version": self.planner.adapter_version,
                "ir_config_schema": self.planner.ir_config_schema,
            },
        }

    @property
    def compiler_checksum(self) -> str:
        return canonical_checksum(self.compiler_payload())

    @property
    def checksum(self) -> str:
        return canonical_checksum(self.model_dump(mode="json", exclude_none=True))

    def to_safe_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload.update(
            {
                "contract_version": NODE_CONTRACT_VERSION,
                "checksum": self.checksum,
                "compiler_checksum": self.compiler_checksum,
            }
        )
        return payload


class WorkflowAgentPlannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_prompt: str = Field(min_length=1, max_length=20_000)
    task_input: str = Field(min_length=1, max_length=8_000)
    model_id: str | None = Field(default=None, max_length=300)
    source_agent_id: str | None = Field(default=None, min_length=1, max_length=160)
    method_skill_ids: list[str] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def validate_method_skill_ids(self) -> "WorkflowAgentPlannerConfig":
        if len(self.method_skill_ids) != len(set(self.method_skill_ids)):
            raise ValueError("method_skill_ids must be unique")
        if any(
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", item)
            for item in self.method_skill_ids
        ):
            raise ValueError("method_skill_ids contains an invalid Skill id")
        return self


@dataclass(frozen=True, slots=True)
class NodePolicyDecision:
    allowed: bool
    conditional: bool
    code: str = ""
    message: str = ""


class NodeContractRegistry:
    def __init__(self, contracts: list[NodeContract]) -> None:
        self._contracts: dict[str, NodeContract] = {}
        for contract in contracts:
            if contract.kind in self._contracts:
                raise ValueError(f"duplicate node contract: {contract.kind}")
            self._contracts[contract.kind] = contract
        expected = set(get_args(NativeNodeKind))
        missing = expected - set(self._contracts)
        extra = set(self._contracts) - expected
        if missing or extra:
            raise ValueError(
                "node contract coverage mismatch; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )

    def kinds(self) -> set[str]:
        return set(self._contracts)

    def list(self) -> list[NodeContract]:
        return [self._contracts[kind] for kind in sorted(self._contracts)]

    def get(self, kind: str) -> NodeContract | None:
        return self._contracts.get(kind)

    def require(self, kind: str) -> NodeContract:
        contract = self.get(kind)
        if contract is None:
            raise KeyError(f"unknown workflow node kind: {kind}")
        return contract

    @property
    def checksum(self) -> str:
        return canonical_checksum(
            [contract.to_safe_payload() for contract in self.list()]
        )

    def to_safe_payload(self) -> dict[str, Any]:
        return {
            "contract_version": NODE_CONTRACT_VERSION,
            "contract_checksum": self.checksum,
            "items": [contract.to_safe_payload() for contract in self.list()],
        }


class NodePolicyService:
    def __init__(self, registry: NodeContractRegistry) -> None:
        self.registry = registry

    def decision(self, kind: str, entrypoint: str) -> NodePolicyDecision:
        contract = self.registry.get(kind)
        if contract is None:
            return NodePolicyDecision(
                allowed=False,
                conditional=False,
                code="unknown_node_kind",
                message=f"Unknown workflow node kind: {kind}.",
            )
        rule = getattr(contract.availability, entrypoint, None)
        if not isinstance(rule, NodeAvailabilityRule):
            return NodePolicyDecision(
                allowed=False,
                conditional=False,
                code="unknown_node_entrypoint",
                message=f"Unknown node entrypoint: {entrypoint}.",
            )
        return NodePolicyDecision(
            allowed=rule.state != "deny",
            conditional=rule.state == "conditional",
            code=rule.code,
            message=rule.message,
        )

    def evolution_control_kinds(self) -> set[str]:
        return {
            contract.kind
            for contract in self.registry.list()
            if contract.contract_status == "complete"
            and contract.planner.enabled
            and contract.planner.support == "full"
            and contract.planner.compilation_mode == "adapter"
            and contract.availability.evolution.state == "allow"
        }


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
    additional_properties: bool = True,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required or []),
        "additionalProperties": additional_properties,
    }


def _rule(
    state: NodeAvailabilityState,
    *,
    code: str = "",
    message: str = "",
) -> NodeAvailabilityRule:
    return NodeAvailabilityRule(state=state, code=code, message=message)


def _availability(
    *,
    app: NodeAvailabilityRule | None = None,
    evaluation: NodeAvailabilityRule | None = None,
    evolution: NodeAvailabilityRule | None = None,
) -> NodeAvailabilityPolicy:
    return NodeAvailabilityPolicy(
        app=app or _rule("conditional"),
        evaluation=evaluation or _rule("conditional"),
        evolution=evolution or _rule("deny"),
    )


def _planner(
    *,
    enabled: bool = False,
    support: NodePlannerSupport = "unsupported",
    compilation_mode: NodePlannerCompilationMode = "none",
    adapter_version: str = "",
    default_data: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    ir_config_schema: dict[str, Any] | None = None,
) -> NodePlannerContract:
    return NodePlannerContract(
        enabled=enabled,
        support=support,
        compilation_mode=compilation_mode,
        adapter_version=adapter_version,
        default_data=dict(default_data or {}),
        config_constraints=dict(constraints or {}),
        ir_config_schema=dict(ir_config_schema or {}),
    )


def _compatibility_contract(kind: str) -> NodeContract:
    return NodeContract(
        kind=kind,
        config_schema=_object_schema(),
        planner=_planner(),
    )


def _complete_contracts() -> dict[str, NodeContract]:
    any_value = WorkflowValueSchema()
    string_value = WorkflowValueSchema(type="string")
    object_value = WorkflowValueSchema(type="object")
    array_object_value = WorkflowValueSchema(type="array", items=object_value)
    planner_adapter_version = "node-contract-v3"
    contracts: dict[str, NodeContract] = {}

    contracts["input"] = NodeContract(
        kind="input",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "variableName": {"type": "string"},
                "historyVariable": {"type": "string"},
            }
        ),
        ports=(
            NodePortContract(
                name="user_input", direction="output", value_schema=string_value
            ),
            NodePortContract(
                name="conversation_history",
                direction="output",
                value_schema=WorkflowValueSchema(
                    type="array", items=WorkflowValueSchema(type="object")
                ),
                required=False,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="input",
        ),
        planner=_planner(
            enabled=True,
            support="full",
            compilation_mode="compiler_managed",
            default_data={"variableName": "user_input"},
        ),
    )
    deployment_only_availability = NodeAvailabilityPolicy(
        workflow=_rule("allow"),
        xpert=_rule(
            "deny",
            code="deployment_node_xpert_forbidden",
            message="Independent deployment nodes are unavailable in Xpert workflows.",
        ),
        goal=_rule("deny"),
        handoff=_rule("deny"),
        app=_rule("deny"),
        evaluation=_rule("deny"),
        evolution=_rule("deny"),
    )
    event_value = WorkflowValueSchema(type="object")
    contracts["scheduled_start"] = NodeContract(
        kind="scheduled_start",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "scheduleType": {"type": "string", "enum": ["once", "interval", "cron"]},
                "onceAt": {"type": "string"},
                "intervalSeconds": {"type": "integer", "minimum": 30, "maximum": 31_536_000},
                "cronExpression": {"type": "string"},
                "timezone": {"type": "string"},
                "eventVariable": {"type": "string"},
            },
            required=["scheduleType", "timezone", "eventVariable"],
        ),
        ports=(NodePortContract(name="event", direction="output", value_schema=event_value),),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=False,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="private_trigger",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    contracts["http_event_entry"] = NodeContract(
        kind="http_event_entry",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "eventVariable": {"type": "string"},
                "bodyVariable": {"type": "string"},
                "acceptedContentType": {
                    "type": "string",
                    "enum": ["both", "json", "text"],
                },
                "maxBodyBytes": {
                    "type": "integer",
                    "minimum": 1_024,
                    "maximum": 1_048_576,
                },
            },
            required=["eventVariable"],
        ),
        ports=(
            NodePortContract(name="event", direction="output", value_schema=event_value),
            NodePortContract(name="body", direction="output", value_schema=any_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            deterministic=False,
            idempotent=True,
            external_io=True,
            error_semantics="fail_closed",
            security_category="private_webhook",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    contracts["failure_event_entry"] = NodeContract(
        kind="failure_event_entry",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "sourceProjectIds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "pattern": r"^wf_[a-f0-9]{32}$",
                    },
                    "minItems": 1,
                    "maxItems": 50,
                    "uniqueItems": True,
                },
                "eventVariable": {"type": "string"},
            },
            required=["sourceProjectIds", "eventVariable"],
        ),
        ports=(NodePortContract(name="event", direction="output", value_schema=event_value),),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=False,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="private_trigger",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    contracts["suspend_wait"] = NodeContract(
        kind="suspend_wait",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "waitMode": {"type": "string", "enum": ["duration", "until"]},
                "durationSeconds": {"type": "integer", "minimum": 1, "maximum": 2_592_000},
                "untilTemplate": {"type": "string"},
                "untilInputMode": {"type": "string", "enum": ["fixed", "template"]},
                "untilTimezone": {"type": "string"},
                "outputVariable": {"type": "string"},
            },
            required=["waitMode", "outputVariable"],
        ),
        ports=(
            NodePortContract(name="input", direction="input", value_schema=any_value, required=False),
            NodePortContract(name="resumed", direction="output", value_schema=event_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=False,
            idempotent=True,
            can_wait=True,
            error_semantics="fail_closed",
            security_category="durable_timer",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    contracts["http_event_reply"] = NodeContract(
        kind="http_event_reply",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "statusCode": {"type": "integer", "minimum": 200, "maximum": 599},
                "responseBodyType": {"type": "string", "enum": ["text", "json"]},
                "bodyTemplate": {"type": "string"},
            },
            required=["statusCode", "responseBodyType"],
        ),
        ports=(NodePortContract(name="response", direction="input", value_schema=any_value, required=False),),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="private_webhook_reply",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    contracts["llm"] = NodeContract(
        kind="llm",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "modelId": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                },
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100_000,
                },
                "outputVariable": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                },
            },
            required=["modelId", "prompt", "outputVariable"],
        ),
        ports=(
            NodePortContract(
                name="prompt",
                direction="input",
                value_schema=string_value,
                required=False,
            ),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=string_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            deterministic=False,
            idempotent=False,
            external_io=True,
            error_semantics="fail_closed",
            security_category="model",
        ),
        planner=_planner(),
    )
    contracts["output"] = NodeContract(
        kind="output",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "outputVariable": {"type": "string"},
                "template": {"type": "string"},
            }
        ),
        ports=(
            NodePortContract(
                name="result", direction="input", value_schema=any_value, required=True
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="output",
        ),
        planner=_planner(
            enabled=True,
            support="full",
            compilation_mode="compiler_managed",
        ),
    )
    contracts["workflow_agent"] = NodeContract(
        kind="workflow_agent",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "modelId": {"type": "string"},
                "rolePrompt": {"type": "string"},
                "taskInput": {"type": "string"},
                "outputVariable": {"type": "string"},
            },
            required=["modelId", "rolePrompt", "taskInput", "outputVariable"],
        ),
        ports=(
            NodePortContract(
                name="task", direction="input", value_schema=string_value, required=True
            ),
            NodePortContract(
                name="result", direction="output", value_schema=string_value
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            external_io=True,
            error_semantics="exception_strategy",
            security_category="model",
        ),
        availability=_availability(evolution=_rule("allow")),
        planner=_planner(
            enabled=True,
            support="full",
            compilation_mode="adapter",
            adapter_version=planner_adapter_version,
            default_data={
                "toolMode": "none",
                "maxIterations": "6",
                "outputVariable": "agent_output",
            },
            constraints={
                "required": ["modelId", "rolePrompt", "taskInput", "outputVariable"]
            },
            ir_config_schema=WorkflowAgentPlannerConfig.model_json_schema(),
        ),
    )

    binding_specs = {
        "external_xpert": (
            "expert-binding",
            "expert",
            "xpert",
            "xpertId",
            "pinnedVersion",
            _rule(
                "deny",
                code="app_external_xpert_forbidden",
                message="Public Xpert Apps cannot deploy external Xpert collaborators.",
            ),
        ),
        "knowledge_base": (
            "knowledge-binding",
            "knowledge",
            "knowledge_base",
            "knowledgeBaseId",
            None,
            _rule("conditional"),
        ),
        "toolset_resource": (
            "toolset-binding",
            "toolset",
            "toolset",
            "toolsetId",
            "pinnedVersion",
            _rule("conditional"),
        ),
        "plugin_resource": (
            "plugin-binding",
            "plugin",
            "plugin",
            "pluginId",
            "pinnedVersion",
            _rule(
                "deny",
                code="app_plugin_resource_forbidden",
                message=(
                    "Public Xpert Apps cannot deploy declarative Plugin resources. "
                    "Bind public-safe Prompt Profiles directly instead."
                ),
            ),
        ),
    }
    for kind, (
        source_handle,
        target_handle,
        resource_kind,
        id_field,
        pinned_field,
        app_rule,
    ) in binding_specs.items():
        properties: dict[str, Any] = {id_field: {"type": "string"}}
        required_fields = [id_field]
        if kind == "external_xpert":
            properties.update(
                {
                    "toolName": {
                        "type": "string",
                        "pattern": r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$",
                    },
                    "description": {"type": "string"},
                }
            )
            required_fields.append("toolName")
        elif kind == "knowledge_base":
            properties.update(
                {
                    "topK": {"type": "integer", "minimum": 1, "maximum": 10},
                    "scoreThreshold": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "description": {"type": "string"},
                }
            )
        else:
            properties["description"] = {"type": "string"}
        if pinned_field:
            version_values = (
                ["latest", "pinned"]
                if kind == "plugin_resource"
                else ["current_published", "pinned"]
            )
            properties.update(
                {
                    "versionPolicy": {"enum": version_values},
                    pinned_field: {"type": ["integer", "null"]},
                }
            )
        contracts[kind] = NodeContract(
            kind=kind,
            contract_status="complete",
            config_schema=_object_schema(properties, required=required_fields),
            ports=(),
            edge=NodeEdgeContract(
                modes=("binding",),
                topology_modes=(),
                allowed_source_handles=(source_handle,),
                allowed_target_handles=(target_handle,),
            ),
            execution=NodeExecutionPolicy(
                side_effect="read",
                deterministic=False,
                idempotent=True,
                external_io=kind
                in {"external_xpert", "toolset_resource", "plugin_resource"},
                error_semantics="fail_closed",
                security_category="resource",
            ),
            availability=_availability(app=app_rule),
            resources=(
                NodeResourceContract(
                    kind=resource_kind,
                    id_field=id_field,
                    version_policy_field="versionPolicy" if pinned_field else None,
                    pinned_version_field=pinned_field,
                ),
            ),
            planner=_planner(
                enabled=True,
                support="binding_only",
                compilation_mode="binding",
                default_data=(
                    {"versionPolicy": "pinned", "pinnedVersion": None}
                    if pinned_field
                    else {"topK": "5", "scoreThreshold": "0"}
                ),
                constraints={
                    "required": required_fields,
                    "target_handle": target_handle,
                },
            ),
        )

    contracts["json_serialize"] = NodeContract(
        kind="json_serialize",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "outputVariable": {"type": "string"},
                "format": {"enum": ["compact", "pretty"]},
            },
            required=["inputVariable", "outputVariable"],
        ),
        ports=(
            NodePortContract(name="value", direction="input", value_schema=any_value),
            NodePortContract(name="json", direction="output", value_schema=string_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="legacy_inline_error",
            security_category="transform",
        ),
        planner=_planner(
            default_data={
                "inputVariable": "json_value",
                "outputVariable": "json_text",
                "format": "compact",
            },
            constraints={
                "required": ["inputVariable", "outputVariable"],
                "format": ["compact", "pretty"],
            },
        ),
    )
    contracts["json_deserialize"] = NodeContract(
        kind="json_deserialize",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "outputVariable": {"type": "string"},
            },
            required=["inputVariable", "outputVariable"],
        ),
        ports=(
            NodePortContract(name="json", direction="input", value_schema=string_value),
            NodePortContract(name="value", direction="output", value_schema=any_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="legacy_inline_error",
            security_category="transform",
        ),
        planner=_planner(
            default_data={
                "inputVariable": "json_text",
                "outputVariable": "json_value",
            },
            constraints={"required": ["inputVariable", "outputVariable"]},
        ),
    )

    contracts["knowledge_retrieval"] = NodeContract(
        kind="knowledge_retrieval",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "contractVersion": {"const": 2},
                "knowledgeBaseId": {"type": "string"},
                "queryVariable": {"type": "string"},
                "top_k": {"type": ["string", "integer"]},
                "returnMode": {"enum": ["context", "result"]},
                "outputVariable": {"type": "string"},
            },
            required=["knowledgeBaseId", "queryVariable", "outputVariable"],
        ),
        ports=(
            NodePortContract(name="query", direction="input", value_schema=string_value),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=WorkflowValueSchema(
                    any_of=(string_value, object_value)
                ),
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="read",
            idempotent=True,
            external_io=True,
            error_semantics="fail_closed",
            security_category="knowledge",
        ),
        resources=(
            NodeResourceContract(
                kind="knowledge_base",
                id_field="knowledgeBaseId",
                dynamic_schema=True,
            ),
        ),
        planner=_planner(
            default_data={
                "contractVersion": 2,
                "knowledgeBaseId": "",
                "queryVariable": "user_input",
                "top_k": "5",
                "returnMode": "result",
                "outputVariable": "knowledge_result",
            },
            constraints={
                "required": ["knowledgeBaseId", "queryVariable", "outputVariable"],
                "return_mode": ["context", "result"],
            },
        ),
    )

    app_table_denied = _rule(
        "deny",
        code="app_agent_table_forbidden",
        message="Public Xpert Apps cannot deploy private Agent Table nodes.",
    )
    table_specs = {
        "data_table_query": (
            "read",
            array_object_value,
            {
                "versionPolicy": "latest",
                "selectFields": [],
                "filter": None,
                "sort": [],
                "limit": 20,
                "returnMode": "list",
                "outputVariable": "table_records",
            },
        ),
        "data_table_insert": (
            "write",
            object_value,
            {
                "versionPolicy": "latest",
                "valueBindings": {},
                "outputVariable": "inserted_record",
            },
        ),
        "data_table_update": (
            "write",
            object_value,
            {
                "versionPolicy": "latest",
                "filter": None,
                "valueBindings": {},
                "outputVariable": "update_result",
            },
        ),
        "data_table_delete": (
            "write",
            object_value,
            {
                "versionPolicy": "latest",
                "filter": None,
                "outputVariable": "delete_result",
            },
        ),
    }
    for kind, (side_effect, output_schema, defaults) in table_specs.items():
        contracts[kind] = NodeContract(
            kind=kind,
            contract_status="complete",
            config_schema=_object_schema(
                {
                    "tableId": {"type": "string"},
                    "versionPolicy": {"enum": ["latest", "pinned"]},
                    "pinnedSchemaVersion": {"type": ["integer", "null"]},
                    "selectFields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 50,
                    },
                    "filter": {"type": ["object", "null"]},
                    "sort": {
                        "type": "array",
                        "items": {"type": "object"},
                        "maxItems": 5,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "returnMode": {"enum": ["list", "first"]},
                    "valueBindings": {
                        "type": "object",
                        "maxProperties": 50,
                    },
                    "outputVariable": {"type": "string"},
                },
                required=["tableId", "outputVariable"],
            ),
            ports=(
                NodePortContract(
                    name="result", direction="output", value_schema=output_schema
                ),
            ),
            execution=NodeExecutionPolicy(
                side_effect=side_effect,
                deterministic=True,
                idempotent=True,
                error_semantics="fail_closed",
                security_category="private_data",
            ),
            availability=_availability(
                app=app_table_denied,
                evaluation=_rule(
                    "deny",
                    code="evaluation_unsafe_node",
                    message=f"Evaluation does not allow node kind: {kind}.",
                ),
            ),
            resources=(
                NodeResourceContract(
                    kind="data_table",
                    id_field="tableId",
                    version_policy_field="versionPolicy",
                    pinned_version_field="pinnedSchemaVersion",
                    dynamic_schema=True,
                ),
            ),
            planner=_planner(default_data=defaults),
        )

    contracts["vision_understanding"] = NodeContract(
        kind="vision_understanding",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "assetIdVariable": {"type": "string"},
                "visionModelId": {"type": "string"},
                "pdfPageStrategy": {"enum": ["auto", "all", "scanned_only"]},
                "maxPages": {"type": "integer", "minimum": 1, "maximum": 200},
                "maxImageEdge": {"type": "integer", "minimum": 512, "maximum": 4096},
                "failurePolicy": {"enum": ["continue_on_error", "strict"]},
                "outputVariable": {"type": "string"},
            },
            required=["assetIdVariable", "visionModelId", "outputVariable"],
        ),
        ports=(
            NodePortContract(
                name="asset_id", direction="input", value_schema=string_value
            ),
            NodePortContract(
                name="result", direction="output", value_schema=object_value
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            external_io=True,
            error_semantics="exception_strategy",
            security_category="private_file",
        ),
        availability=_availability(
            app=_rule(
                "deny",
                code="app_vision_understanding_forbidden",
                message=(
                    "Public Xpert Apps cannot deploy vision understanding nodes "
                    "because public attachment upload is disabled."
                ),
            ),
            evaluation=_rule(
                "deny",
                code="evaluation_unsafe_node",
                message=(
                    "Evaluation does not allow vision understanding until "
                    "evaluation datasets support explicit file assets."
                ),
            ),
        ),
        resources=(
            NodeResourceContract(
                kind="file_asset", id_field="assetIdVariable", dynamic_schema=True
            ),
        ),
        planner=_planner(
            default_data={
                "assetIdVariable": "selected_file_asset_id",
                "visionModelId": "",
                "pdfPageStrategy": "auto",
                "maxPages": 100,
                "maxImageEdge": 2048,
                "failurePolicy": "continue_on_error",
                "outputVariable": "vision_result",
            }
        ),
    )

    contracts["annotation"] = NodeContract(
        kind="annotation",
        contract_status="complete",
        config_schema=_object_schema({"content": {"type": "string", "maxLength": 20_000}}),
        edge=NodeEdgeContract(
            modes=(),
            topology_modes=(),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="ignored",
            security_category="metadata",
        ),
        planner=_planner(
            support="metadata_only",
            default_data={"content": ""},
        ),
    )
    contracts["runtime_middleware"] = NodeContract(
        kind="runtime_middleware",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "runtimeMiddlewareId": {"type": "string"},
                "runtimeMiddlewareConfig": {"type": "object"},
                "middlewarePriority": {"type": ["string", "integer"]},
                "configVersion": {"type": "integer"},
            },
            required=["runtimeMiddlewareId"],
        ),
        edge=NodeEdgeContract(
            modes=("control", "binding"),
            topology_modes=("control",),
            allowed_source_handles=("middleware-binding",),
            allowed_target_handles=("middleware",),
        ),
        execution=NodeExecutionPolicy(
            side_effect="unknown",
            external_io=True,
            can_wait=True,
            error_semantics="exception_strategy",
            security_category="middleware",
        ),
        planner=_planner(support="binding_only", compilation_mode="binding"),
    )
    return contracts


def build_builtin_node_contract_registry() -> NodeContractRegistry:
    contracts = {
        kind: _compatibility_contract(kind) for kind in get_args(NativeNodeKind)
    }
    contracts.update(_complete_contracts())

    evaluation_denied = {
        "agent_handoff",
        "handoff_router",
        "human_intervention",
    }
    app_denied = {
        "human_intervention": (
            "app_interactive_hitl_forbidden",
            "Public Xpert Apps cannot deploy interactive HITL workflows.",
        )
    }
    for kind in evaluation_denied | set(app_denied):
        current = contracts[kind]
        availability = current.availability.model_copy(
            update={
                **(
                    {
                        "evaluation": _rule(
                            "deny",
                            code="evaluation_unsafe_node",
                            message=f"Evaluation does not allow node kind: {kind}.",
                        )
                    }
                    if kind in evaluation_denied
                    else {}
                ),
                **(
                    {
                        "app": _rule(
                            "deny",
                            code=app_denied[kind][0],
                            message=app_denied[kind][1],
                        )
                    }
                    if kind in app_denied
                    else {}
                ),
            }
        )
        contracts[kind] = current.model_copy(update={"availability": availability})

    condition = contracts["condition"]
    contracts["condition"] = condition.model_copy(
        update={
            "edge": NodeEdgeContract(
                allowed_source_handles=("true", "false"),
            )
        }
    )
    return NodeContractRegistry(list(contracts.values()))


workflow_node_contract_registry = build_builtin_node_contract_registry()
node_policy_service = NodePolicyService(workflow_node_contract_registry)
