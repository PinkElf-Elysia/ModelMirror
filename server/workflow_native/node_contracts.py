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
    private_file_availability = NodeAvailabilityPolicy(
        workflow=_rule("allow"),
        xpert=_rule("allow"),
        goal=_rule("deny"),
        handoff=_rule("deny"),
        app=_rule(
            "deny",
            code="public_file_node_forbidden",
            message="Public Xpert Apps cannot deploy workflow file nodes.",
        ),
        evaluation=_rule(
            "deny",
            code="evaluation_file_node_forbidden",
            message="Evaluation does not allow workflow file nodes.",
        ),
        evolution=_rule(
            "deny",
            code="evolution_file_node_forbidden",
            message="Evolution does not allow workflow file nodes.",
        ),
    )
    private_content_availability = NodeAvailabilityPolicy(
        workflow=_rule("allow"),
        xpert=_rule("allow"),
        goal=_rule("deny"),
        handoff=_rule("deny"),
        app=_rule(
            "deny",
            code="public_content_parser_forbidden",
            message="Public Xpert Apps cannot deploy content parser nodes.",
        ),
        evaluation=_rule(
            "deny",
            code="evaluation_content_parser_forbidden",
            message="Evaluation does not allow content parser nodes.",
        ),
        evolution=_rule(
            "deny",
            code="evolution_content_parser_forbidden",
            message="Evolution does not allow content parser nodes.",
        ),
    )

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
    form_option_schema = _object_schema(
        {
            "id": {"type": "string", "pattern": r"^option_[A-Za-z0-9_-]{1,55}$"},
            "value": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"},
            "label": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        required=["id", "value", "label"],
        additional_properties=False,
    )
    form_field_schema = _object_schema(
        {
            "id": {"type": "string", "pattern": r"^field_[A-Za-z0-9_-]{1,56}$"},
            "outputVariable": {"type": "string"},
            "label": {"type": "string", "minLength": 1, "maxLength": 120},
            "helpText": {"type": "string", "maxLength": 500},
            "placeholder": {"type": "string", "maxLength": 200},
            "type": {
                "type": "string",
                "enum": [
                    "short_text",
                    "long_text",
                    "email",
                    "number",
                    "boolean",
                    "date",
                    "single_select",
                    "multi_select",
                ],
            },
            "required": {"type": "boolean"},
            "options": {
                "type": "array",
                "items": form_option_schema,
                "maxItems": 20,
            },
        },
        required=[
            "id",
            "outputVariable",
            "label",
            "helpText",
            "placeholder",
            "type",
            "required",
            "options",
        ],
        additional_properties=False,
    )
    contracts["form_event_entry"] = NodeContract(
        kind="form_event_entry",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "kind": {"type": "string", "const": "form_event_entry"},
                "contractVersion": {"type": "integer", "const": 1},
                "formTitle": {"type": "string", "minLength": 1, "maxLength": 120},
                "formDescription": {"type": "string", "maxLength": 1000},
                "submitLabel": {"type": "string", "minLength": 1, "maxLength": 40},
                "privacyNotice": {"type": "string", "maxLength": 1000},
                "successTitle": {"type": "string", "minLength": 1, "maxLength": 120},
                "successMessage": {"type": "string", "minLength": 1, "maxLength": 1000},
                "theme": {"type": "string", "enum": ["light", "dark"]},
                "eventVariable": {"type": "string"},
                "submissionVariable": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": form_field_schema,
                    "minItems": 1,
                    "maxItems": 30,
                },
            },
            required=[
                "contractVersion",
                "formTitle",
                "formDescription",
                "submitLabel",
                "privacyNotice",
                "successTitle",
                "successMessage",
                "theme",
                "eventVariable",
                "submissionVariable",
                "fields",
            ],
            additional_properties=False,
        ),
        ports=(
            NodePortContract(name="event", direction="output", value_schema=event_value),
            NodePortContract(name="submission", direction="output", value_schema=object_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            deterministic=False,
            idempotent=True,
            external_io=True,
            error_semantics="fail_closed",
            security_category="public_form",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    contracts["rss_event_entry"] = NodeContract(
        kind="rss_event_entry",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "kind": {"type": "string", "const": "rss_event_entry"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "contractVersion": {"type": "integer", "const": 1},
                "feedUrl": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2048,
                    "pattern": r"^https://",
                },
                "pollIntervalMinutes": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 1440,
                },
                "eventVariable": {"type": "string"},
                "itemVariable": {"type": "string"},
            },
            required=[
                "contractVersion",
                "feedUrl",
                "pollIntervalMinutes",
                "eventVariable",
                "itemVariable",
            ],
            additional_properties=False,
        ),
        ports=(
            NodePortContract(name="event", direction="output", value_schema=event_value),
            NodePortContract(name="item", direction="output", value_schema=object_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            deterministic=False,
            idempotent=True,
            external_io=True,
            error_semantics="fail_closed",
            security_category="public_feed",
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
    contracts["workflow_call_entry"] = NodeContract(
        kind="workflow_call_entry",
        contract_status="complete",
        config_schema=_object_schema(
            {"eventVariable": {"type": "string"}},
            required=["eventVariable"],
        ),
        ports=(NodePortContract(name="event", direction="output", value_schema=event_value),),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=False,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="private_subworkflow_entry",
        ),
        availability=deployment_only_availability,
        planner=_planner(),
    )
    binding_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": ["literal", "variable"]},
            "value": {},
            "variable": {"type": "string"},
        },
        "required": ["source"],
        "additionalProperties": False,
    }
    contracts["invoke_workflow"] = NodeContract(
        kind="invoke_workflow",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "targetProjectId": {
                    "type": "string",
                    "pattern": r"^wf_[a-f0-9]{32}$",
                },
                "targetVersion": {"type": "integer", "minimum": 1},
                "inputBindings": {
                    "type": "object",
                    "additionalProperties": binding_schema,
                },
                "resultVariable": {"type": "string"},
                "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            required=[
                "targetProjectId",
                "targetVersion",
                "inputBindings",
                "resultVariable",
                "timeoutSeconds",
            ],
        ),
        ports=(
            NodePortContract(name="inputs", direction="input", value_schema=object_value),
            NodePortContract(name="result", direction="output", value_schema=object_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_write",
            deterministic=False,
            idempotent=False,
            external_io=True,
            can_wait=False,
            error_semantics="fail_closed",
            security_category="private_subworkflow_call",
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
    comparison_rule_properties = {
        "operator": {
            "type": "string",
            "enum": [
                "equals",
                "not_equals",
                "gt",
                "gte",
                "lt",
                "lte",
                "contains",
                "in",
                "is_null",
            ],
        },
        "valueType": {
            "type": "string",
            "enum": ["text", "number", "boolean", "null", "json"],
        },
        "value": {},
    }
    route_rule_schema = _object_schema(
        {
            "id": {"type": "string", "pattern": r"^route_[1-8]$"},
            "label": {"type": "string", "minLength": 1, "maxLength": 80},
            **comparison_rule_properties,
        },
        required=["id", "label", "operator"],
        additional_properties=False,
    )
    filter_rule_schema = _object_schema(
        {
            "field": {"type": "string", "maxLength": 64},
            **comparison_rule_properties,
        },
        required=["operator"],
        additional_properties=False,
    )
    condition_v1_schema = _object_schema(
        {
            "conditionVariable": {"type": "string"},
            "conditionOperator": {"type": "string", "enum": ["equals", "contains"]},
            "conditionValue": {"type": "string"},
        },
        required=["conditionVariable", "conditionOperator", "conditionValue"],
    )
    condition_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "inputVariable": {"type": "string"},
            "field": {"type": "string", "maxLength": 64},
            **comparison_rule_properties,
        },
        required=["contractVersion", "inputVariable", "operator", "valueType", "value"],
    )
    contracts["condition"] = NodeContract(
        kind="condition",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [condition_v1_schema, condition_v2_schema],
        },
        ports=(
            NodePortContract(
                name="value",
                direction="input",
                value_schema=any_value,
                required=True,
            ),
        ),
        edge=NodeEdgeContract(allowed_source_handles=("true", "false")),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="control",
        ),
        planner=_planner(),
    )
    code_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "operation": {
                "type": "string",
                "enum": ["upper", "lower", "replace", "concat"],
            },
            "inputVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "outputVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "replaceFrom": {"type": "string", "maxLength": 100_000},
            "replaceTo": {"type": "string", "maxLength": 100_000},
            "concatValue": {"type": "string", "maxLength": 100_000},
        },
        required=["contractVersion", "operation", "inputVariable", "outputVariable"],
    )
    code_v2_schema["allOf"] = [
        {
            "if": {
                "properties": {"operation": {"const": "replace"}},
                "required": ["operation"],
            },
            "then": {"required": ["replaceFrom", "replaceTo"]},
        },
        {
            "if": {
                "properties": {"operation": {"const": "concat"}},
                "required": ["operation"],
            },
            "then": {"required": ["concatValue"]},
        },
    ]
    code_v2_schema["not"] = {
        "anyOf": [
            {"required": [field]}
            for field in (
                "codeOperation",
                "codeInputVariable",
                "codeOutputVariable",
                "pythonCode",
            )
        ]
    }
    contracts["code"] = NodeContract(
        kind="code",
        contract_status="complete",
        config_schema=code_v2_schema,
        ports=(
            NodePortContract(
                name="value",
                direction="input",
                value_schema=any_value,
                required=True,
            ),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=string_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="data",
        ),
        availability=_availability(
            app=_rule("allow"),
            evaluation=_rule("allow"),
            evolution=_rule("deny"),
        ),
        planner=_planner(
            default_data={
                "contractVersion": 2,
                "operation": "upper",
                "inputVariable": "user_input",
                "outputVariable": "code_output",
                "replaceFrom": "",
                "replaceTo": "",
                "concatValue": "",
            }
        ),
    )
    variable_assign_v1_schema = _object_schema(
        {
            "variableName": {"type": "string"},
            "template": {"type": "string"},
        },
        required=["variableName", "template"],
    )
    variable_assign_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "outputVariable": {"type": "string"},
            "valueSource": {
                "type": "string",
                "enum": ["literal", "variable", "template"],
            },
            "literalValue": {},
            "sourceVariable": {"type": "string"},
            "template": {"type": "string", "maxLength": 100_000},
        },
        required=["contractVersion", "outputVariable", "valueSource"],
    )
    contracts["variable_assign"] = NodeContract(
        kind="variable_assign",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [variable_assign_v1_schema, variable_assign_v2_schema],
        },
        ports=(
            NodePortContract(
                name="value",
                direction="output",
                value_schema=any_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="data",
        ),
        availability=_availability(
            app=_rule("allow"),
            evaluation=_rule("allow"),
            evolution=_rule("deny"),
        ),
        planner=_planner(),
    )
    variable_aggregator_v1_schema = _object_schema(
        {
            "variableNames": {"type": "string"},
            "outputTemplate": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["variableNames", "outputVariable"],
    )
    variable_pack_binding_schema = _object_schema(
        {
            "id": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "sourceVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "outputField": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
        },
        required=["id", "sourceVariable", "outputField"],
        additional_properties=False,
    )
    variable_aggregator_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "bindings": {
                "type": "array",
                "items": variable_pack_binding_schema,
                "minItems": 1,
                "maxItems": 50,
            },
            "outputVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
        },
        required=["contractVersion", "bindings", "outputVariable"],
    )
    contracts["variable_aggregator"] = NodeContract(
        kind="variable_aggregator",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [variable_aggregator_v1_schema, variable_aggregator_v2_schema],
        },
        ports=(
            NodePortContract(
                name="values",
                direction="input",
                value_schema=any_value,
                required=True,
                cardinality="many",
            ),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=object_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="data",
        ),
        availability=_availability(
            app=_rule("allow"),
            evaluation=_rule("allow"),
            evolution=_rule("allow"),
        ),
        planner=_planner(),
    )

    iteration_v1_schema = _object_schema(
        {
            "inputVariable": {"type": "string"},
            "iterationVariable": {"type": "string"},
            "itemTemplate": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=[
            "inputVariable",
            "iterationVariable",
            "itemTemplate",
            "outputVariable",
        ],
    )
    iteration_binding_schema = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["item", "index", "variable", "literal"],
            },
            "variable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "value": {},
        },
        "required": ["source"],
        "additionalProperties": False,
    }
    iteration_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "mode": {
                "type": "string",
                "enum": ["template_map", "workflow_map"],
            },
            "inputVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "itemVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "indexVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "outputVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "itemTemplate": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20_000,
            },
            "targetProjectId": {
                "type": "string",
                "pattern": r"^wf_[a-f0-9]{32}$",
            },
            "targetVersion": {"type": "integer", "minimum": 1},
            "inputBindings": {
                "type": "object",
                "additionalProperties": iteration_binding_schema,
            },
            "timeoutSeconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
            },
        },
        required=[
            "contractVersion",
            "mode",
            "inputVariable",
            "itemVariable",
            "indexVariable",
            "outputVariable",
        ],
    )
    iteration_v2_schema["allOf"] = [
        {
            "if": {
                "properties": {"mode": {"const": "template_map"}},
                "required": ["mode"],
            },
            "then": {"required": ["itemTemplate"]},
        },
        {
            "if": {
                "properties": {"mode": {"const": "workflow_map"}},
                "required": ["mode"],
            },
            "then": {
                "required": [
                    "targetProjectId",
                    "targetVersion",
                    "inputBindings",
                    "timeoutSeconds",
                ]
            },
        },
    ]
    contracts["iteration"] = NodeContract(
        kind="iteration",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [iteration_v1_schema, iteration_v2_schema],
        },
        ports=(
            NodePortContract(
                name="items",
                direction="input",
                value_schema=WorkflowValueSchema(type="array"),
                required=True,
            ),
            NodePortContract(
                name="results",
                direction="output",
                value_schema=WorkflowValueSchema(type="array"),
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_write",
            deterministic=False,
            idempotent=False,
            external_io=False,
            can_wait=False,
            error_semantics="fail_closed",
            security_category="private_batch_subworkflow",
        ),
        availability=_availability(
            app=_rule("allow"),
            evaluation=_rule("allow"),
            evolution=_rule("deny"),
        ),
        planner=_planner(),
    )

    agent_task_v1_schema = _object_schema(
        {
            "taskTitle": {"type": "string"},
            "taskInput": {"type": "string"},
            "assignedAgent": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["taskTitle", "taskInput", "outputVariable"],
    )
    agent_task_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "taskTitle": {"type": "string", "minLength": 1, "maxLength": 500},
            "taskInput": {"type": "string", "minLength": 1, "maxLength": 20_000},
            "assignedAgent": {"type": "string", "minLength": 1, "maxLength": 160},
            "outputVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
        },
        required=[
            "contractVersion",
            "taskTitle",
            "taskInput",
            "assignedAgent",
            "outputVariable",
        ],
        additional_properties=False,
    )
    task_receipt_value = WorkflowValueSchema(
        type="object",
        properties={
            "status": string_value,
            "taskId": string_value,
            "runId": string_value,
            "assignedAgent": string_value,
        },
        required=("status", "taskId", "runId", "assignedAgent"),
    )
    collaboration_availability = NodeAvailabilityPolicy(
        workflow=_rule("allow"),
        xpert=_rule("allow"),
        goal=_rule("allow"),
        handoff=_rule("allow"),
        app=_rule(
            "conditional",
            code="app_handoffs_capability_required",
            message="Xpert App Handoffs require allow_handoffs.",
        ),
        evaluation=_rule("deny", code="evaluation_unsafe_node"),
        evolution=_rule("deny"),
    )
    contracts["agent_task"] = NodeContract(
        kind="agent_task",
        contract_status="complete",
        config_schema={"type": "object", "anyOf": [agent_task_v1_schema, agent_task_v2_schema]},
        ports=(
            NodePortContract(name="task", direction="input", value_schema=string_value),
            NodePortContract(name="receipt", direction="output", value_schema=task_receipt_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="write",
            deterministic=False,
            idempotent=True,
            external_io=False,
            can_wait=False,
            error_semantics="fail_closed",
            security_category="agent_collaboration",
        ),
        availability=collaboration_availability,
        planner=_planner(),
    )

    handoff_target_properties = {
        "targetMode": {"type": "string", "enum": ["inbox", "xpert"]},
        "inboxTarget": {"type": "string", "maxLength": 160},
        "targetXpertId": {"type": "string", "maxLength": 200},
        "targetVersion": {"type": "integer", "minimum": 1},
        "waitForCompletion": {"type": "boolean"},
        "timeoutSeconds": {"type": "integer", "minimum": 5, "maximum": 600},
        "outputVariable": {
            "type": "string",
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
        },
        "resultVariable": {
            "type": "string",
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
        },
    }
    agent_handoff_v1_schema = _object_schema(
        {
            "taskIdVariable": {"type": "string"},
            "sourceAgent": {"type": "string"},
            "targetAgent": {"type": "string"},
            "executionMode": {"type": "string"},
            "waitForCompletion": {},
            "resultVariable": {"type": "string"},
            "waitTimeoutSeconds": {},
            "reason": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["taskIdVariable", "targetAgent", "reason", "outputVariable"],
    )
    agent_handoff_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "taskVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "taskValueKind": {"type": "string", "enum": ["receipt", "task_id"]},
            "sourceAgent": {"type": "string", "minLength": 1, "maxLength": 160},
            "reason": {"type": "string", "minLength": 1, "maxLength": 4000},
            **handoff_target_properties,
        },
        required=[
            "contractVersion",
            "taskVariable",
            "taskValueKind",
            "sourceAgent",
            "targetMode",
            "waitForCompletion",
            "timeoutSeconds",
            "reason",
            "outputVariable",
        ],
        additional_properties=False,
    )
    handoff_receipt_value = WorkflowValueSchema(
        type="object",
        properties={
            "status": string_value,
            "taskId": string_value,
            "handoffId": string_value,
            "runId": string_value,
            "targetKind": string_value,
            "targetId": string_value,
            "targetVersion": WorkflowValueSchema(type="integer", nullable=True),
            "result": WorkflowValueSchema(type="string", nullable=True),
        },
        required=(
            "status",
            "taskId",
            "handoffId",
            "runId",
            "targetKind",
            "targetId",
            "targetVersion",
            "result",
        ),
    )
    contracts["agent_handoff"] = NodeContract(
        kind="agent_handoff",
        contract_status="complete",
        config_schema={"type": "object", "anyOf": [agent_handoff_v1_schema, agent_handoff_v2_schema]},
        ports=(
            NodePortContract(name="task", direction="input", value_schema=task_receipt_value, required=True),
            NodePortContract(name="receipt", direction="output", value_schema=handoff_receipt_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_write",
            deterministic=False,
            idempotent=True,
            external_io=True,
            can_wait=True,
            error_semantics="fail_closed",
            security_category="agent_collaboration",
        ),
        availability=collaboration_availability,
        planner=_planner(),
    )

    handoff_router_v1_schema = _object_schema(
        {
            "sourceVariable": {"type": "string"},
            "taskTitle": {"type": "string"},
            "sourceAgent": {"type": "string"},
            "targetAgent": {"type": "string"},
            "executionMode": {"type": "string"},
            "waitForCompletion": {},
            "resultVariable": {"type": "string"},
            "waitTimeoutSeconds": {},
            "reasonTemplate": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["sourceVariable", "taskTitle", "targetAgent", "reasonTemplate", "outputVariable"],
    )
    handoff_router_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "sourceVariable": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "taskTitle": {"type": "string", "minLength": 1, "maxLength": 500},
            "sourceAgent": {"type": "string", "minLength": 1, "maxLength": 160},
            "reasonTemplate": {"type": "string", "minLength": 1, "maxLength": 4000},
            **handoff_target_properties,
        },
        required=[
            "contractVersion",
            "sourceVariable",
            "taskTitle",
            "sourceAgent",
            "targetMode",
            "waitForCompletion",
            "timeoutSeconds",
            "reasonTemplate",
            "outputVariable",
        ],
        additional_properties=False,
    )
    contracts["handoff_router"] = NodeContract(
        kind="handoff_router",
        contract_status="complete",
        config_schema={"type": "object", "anyOf": [handoff_router_v1_schema, handoff_router_v2_schema]},
        ports=(
            NodePortContract(name="source", direction="input", value_schema=any_value, required=True),
            NodePortContract(name="receipt", direction="output", value_schema=handoff_receipt_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_write",
            deterministic=False,
            idempotent=True,
            external_io=True,
            can_wait=True,
            error_semantics="fail_closed",
            security_category="agent_collaboration",
        ),
        availability=collaboration_availability,
        planner=_planner(),
    )
    extractor_v1_schema = _object_schema(
        {
            "contractVersion": {"const": 1},
            "inputVariable": {"type": "string"},
            "schema": {"type": "string"},
            "modelId": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["inputVariable", "schema", "modelId", "outputVariable"],
    )
    extractor_field_schema = _object_schema(
        {
            "id": {"type": "string", "pattern": r"^field_(?:[1-9]|[1-4][0-9]|50)$"},
            "name": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
            },
            "description": {"type": "string", "maxLength": 500},
            "valueType": {
                "type": "string",
                "enum": [
                    "string",
                    "number",
                    "boolean",
                    "string_array",
                    "number_array",
                ],
            },
            "required": {"type": "boolean"},
            "nullable": {"type": "boolean"},
        },
        required=["id", "name", "description", "valueType", "required", "nullable"],
        additional_properties=False,
    )
    extractor_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "inputVariable": {"type": "string"},
            "modelId": {"type": "string"},
            "outputVariable": {"type": "string"},
            "schemaMode": {"type": "string", "enum": ["fields", "json_schema"]},
            "outputShape": {"type": "string", "enum": ["object", "object_list"]},
            "fields": {
                "type": "array",
                "items": extractor_field_schema,
                "minItems": 1,
                "maxItems": 50,
            },
            "jsonSchema": {"type": "object"},
            "repairAttempts": {"type": "integer", "minimum": 0, "maximum": 1},
        },
        required=[
            "contractVersion",
            "inputVariable",
            "modelId",
            "outputVariable",
            "schemaMode",
            "outputShape",
            "repairAttempts",
        ],
    )
    contracts["parameter_extractor"] = NodeContract(
        kind="parameter_extractor",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [extractor_v1_schema, extractor_v2_schema],
        },
        ports=(
            NodePortContract(name="text", direction="input", value_schema=string_value, required=True),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=WorkflowValueSchema(
                    any_of=(
                        string_value,
                        object_value,
                        WorkflowValueSchema(type="array"),
                    ),
                ),
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            deterministic=False,
            idempotent=True,
            external_io=True,
            error_semantics="fail_closed",
            security_category="model_inference",
        ),
        planner=_planner(),
    )
    classifier_v1_schema = _object_schema(
        {
            "contractVersion": {"const": 1},
            "inputVariable": {"type": "string"},
            "categories": {"type": "string"},
            "outputVariable": {"type": "string"},
            "defaultCategory": {"type": "string"},
            "matchMode": {"type": "string", "enum": ["contains_any", "contains_all"]},
            "caseSensitive": {"type": ["string", "boolean"]},
            "useLlmFallback": {"type": ["string", "boolean"]},
            "modelId": {"type": "string"},
            "llmFallbackPrompt": {"type": "string"},
        },
        required=["inputVariable", "categories", "outputVariable"],
    )
    classifier_category_schema = _object_schema(
        {
            "id": {"type": "string", "pattern": r"^category_[1-8]$"},
            "label": {"type": "string", "minLength": 1, "maxLength": 100},
            "description": {"type": "string", "maxLength": 500},
            "keywords": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
                "maxItems": 20,
            },
            "matchMode": {"type": "string", "enum": ["contains_any", "contains_all"]},
        },
        required=["id", "label", "description", "keywords", "matchMode"],
        additional_properties=False,
    )
    classifier_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "inputVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
            "classificationMode": {
                "type": "string",
                "enum": ["rules_only", "rules_then_model", "model_only"],
            },
            "categoriesV2": {
                "type": "array",
                "items": classifier_category_schema,
                "minItems": 2,
                "maxItems": 8,
            },
            "caseSensitive": {"type": "boolean"},
            "modelId": {"type": "string"},
            "defaultLabel": {"type": "string", "minLength": 1, "maxLength": 100},
        },
        required=[
            "contractVersion",
            "inputVariable",
            "outputVariable",
            "classificationMode",
            "categoriesV2",
            "caseSensitive",
            "defaultLabel",
        ],
    )
    contracts["question_classifier"] = NodeContract(
        kind="question_classifier",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [classifier_v1_schema, classifier_v2_schema],
        },
        ports=(
            NodePortContract(name="text", direction="input", value_schema=string_value, required=True),
            NodePortContract(name="category", direction="output", value_schema=string_value),
        ),
        edge=NodeEdgeContract(
            allowed_source_handles=(
                "category_1",
                "category_2",
                "category_3",
                "category_4",
                "category_5",
                "category_6",
                "category_7",
                "category_8",
                "default",
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            deterministic=False,
            idempotent=True,
            external_io=True,
            error_semantics="fail_closed",
            security_category="model_inference",
        ),
        planner=_planner(),
    )
    human_v1_schema = _object_schema(
        {
            "prompt": {"type": "string"},
            "outputVariable": {"type": "string"},
            "interactionMode": {"type": "string"},
        },
        required=["prompt", "outputVariable"],
    )
    human_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "interactionMode": {
                "type": "string",
                "enum": ["input", "approval"],
            },
            "prompt": {"type": "string", "minLength": 1, "maxLength": 4_000},
            "outputVariable": {"type": "string"},
            "timeoutSeconds": {
                "type": "integer",
                "minimum": 30,
                "maximum": 86_400,
            },
        },
        required=[
            "contractVersion",
            "interactionMode",
            "prompt",
            "outputVariable",
            "timeoutSeconds",
        ],
    )
    contracts["human_intervention"] = NodeContract(
        kind="human_intervention",
        contract_status="complete",
        config_schema={"type": "object", "anyOf": [human_v1_schema, human_v2_schema]},
        ports=(
            NodePortContract(
                name="result",
                direction="output",
                value_schema=string_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="write",
            deterministic=False,
            idempotent=True,
            external_io=True,
            can_wait=True,
            error_semantics="fail_closed",
            security_category="human_approval",
        ),
        availability=_availability(
            app=_rule(
                "deny",
                code="app_interactive_hitl_forbidden",
                message="Public Xpert Apps cannot deploy interactive HITL workflows.",
            ),
            evaluation=_rule(
                "deny",
                code="evaluation_unsafe_node",
                message="Evaluation does not allow node kind: human_intervention.",
            ),
            evolution=_rule("deny"),
        ),
        planner=_planner(),
    )
    http_binding_schema = _object_schema(
        {
            "source": {"type": "string", "enum": ["literal", "variable"]},
            "variable": {"type": "string"},
            "valueType": {
                "type": "string",
                "enum": ["text", "number", "boolean", "null", "json"],
            },
            "value": {},
        },
        required=["source"],
        additional_properties=False,
    )
    http_item_schema = _object_schema(
        {
            "id": {"type": "string", "minLength": 1, "maxLength": 64},
            "name": {"type": "string", "minLength": 1, "maxLength": 128},
            "binding": http_binding_schema,
        },
        required=["id", "name", "binding"],
        additional_properties=False,
    )
    http_v1_schema = _object_schema(
        {
            "url": {"type": "string", "minLength": 1, "maxLength": 2_048},
            "method": {"type": "string", "enum": ["GET", "POST"]},
            "headersJson": {"type": "string"},
            "bodyVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["url", "method", "outputVariable"],
    )
    http_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            },
            "url": {"type": "string", "minLength": 1, "maxLength": 2_048},
            "queryItems": {"type": "array", "items": http_item_schema, "maxItems": 20},
            "headerItems": {"type": "array", "items": http_item_schema, "maxItems": 20},
            "bodyMode": {"type": "string", "enum": ["none", "json", "text", "form"]},
            "bodyBinding": http_binding_schema,
            "formFields": {"type": "array", "items": http_item_schema, "maxItems": 20},
            "authType": {"type": "string", "enum": ["none", "api_key", "bearer", "basic"]},
            "credentialId": {"type": "string", "maxLength": 160},
            "apiKeyLocation": {"type": "string", "enum": ["header", "query"]},
            "apiKeyName": {"type": "string", "maxLength": 128},
            "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 60},
            "redirectLimit": {"type": "integer", "minimum": 0, "maximum": 3},
            "responseLimitBytes": {
                "type": "integer",
                "minimum": 1_024,
                "maximum": 2_097_152,
            },
            "responseMode": {"type": "string", "enum": ["auto", "json", "text"]},
            "statusPolicy": {"type": "string", "enum": ["success_only", "capture_all"]},
            "outputVariable": {"type": "string"},
        },
        required=[
            "contractVersion",
            "method",
            "url",
            "queryItems",
            "headerItems",
            "bodyMode",
            "formFields",
            "authType",
            "timeoutSeconds",
            "redirectLimit",
            "responseLimitBytes",
            "responseMode",
            "statusPolicy",
            "outputVariable",
        ],
    )
    contracts["http_request"] = NodeContract(
        kind="http_request",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [http_v1_schema, http_v2_schema],
        },
        ports=(
            NodePortContract(name="response", direction="output", value_schema=object_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_read",
            external_io=True,
            deterministic=False,
            idempotent=False,
            error_semantics="fail_closed",
            security_category="network",
        ),
        availability=NodeAvailabilityPolicy(
            app=_rule(
                "deny",
                code="app_http_request_forbidden",
                message="Public Xpert Apps cannot deploy HTTP request nodes.",
            ),
            evaluation=_rule(
                "deny",
                code="evaluation_http_request_forbidden",
                message="Evaluation does not allow HTTP request nodes.",
            ),
            evolution=_rule(
                "deny",
                code="evolution_http_request_forbidden",
                message="Evolution does not allow HTTP request nodes.",
            ),
        ),
        planner=_planner(),
    )
    mcp_binding_schema = _object_schema(
        {
            "source": {"type": "string", "enum": ["literal", "variable"]},
            "variable": {"type": "string"},
            "value": {},
        },
        required=["source"],
        additional_properties=False,
    )
    mcp_argument_binding_schema = _object_schema(
        {
            "id": {"type": "string", "minLength": 1, "maxLength": 64},
            "name": {"type": "string", "minLength": 1, "maxLength": 160},
            "binding": mcp_binding_schema,
        },
        required=["id", "name", "binding"],
        additional_properties=False,
    )
    mcp_v1_schema = _object_schema(
        {
            "toolName": {"type": "string"},
            "argumentsJson": {"type": "string"},
            "outputVariable": {"type": "string"},
            "errorMode": {"type": "string"},
        },
        required=["toolName", "argumentsJson", "outputVariable"],
    )
    mcp_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "serverId": {"type": "string", "minLength": 1, "maxLength": 300},
            "toolName": {"type": "string", "minLength": 1, "maxLength": 160},
            "inputSchemaChecksum": {
                "type": "string",
                "pattern": r"^[a-f0-9]{64}$",
            },
            "argumentMode": {
                "type": "string",
                "enum": ["fields", "object_variable"],
            },
            "argumentBindings": {
                "type": "array",
                "items": mcp_argument_binding_schema,
                "maxItems": 100,
            },
            "argumentsVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=[
            "contractVersion",
            "serverId",
            "toolName",
            "inputSchemaChecksum",
            "argumentMode",
            "argumentBindings",
            "outputVariable",
        ],
    )
    contracts["mcp_tool"] = NodeContract(
        kind="mcp_tool",
        contract_status="complete",
        config_schema={"type": "object", "anyOf": [mcp_v1_schema, mcp_v2_schema]},
        ports=(
            NodePortContract(
                name="result",
                direction="output",
                value_schema=WorkflowValueSchema(
                    type="object",
                    properties={
                        "status": string_value,
                        "serverId": string_value,
                        "toolName": string_value,
                        "text": string_value,
                        "contentTypes": WorkflowValueSchema(type="array", items=string_value),
                        "fileAssetIds": WorkflowValueSchema(type="array", items=string_value),
                    },
                    required=(
                        "status",
                        "serverId",
                        "toolName",
                        "text",
                        "contentTypes",
                        "fileAssetIds",
                    ),
                ),
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="external_write",
            deterministic=False,
            idempotent=False,
            external_io=True,
            can_wait=True,
            error_semantics="fail_closed",
            security_category="tool",
        ),
        availability=NodeAvailabilityPolicy(
            workflow=_rule("allow"),
            xpert=_rule(
                "conditional",
                code="mcp_tools_feature_required",
                message="Private Xpert MCP tools require WORKFLOW_MCP_TOOLS_ENABLED.",
            ),
            goal=_rule("allow"),
            handoff=_rule("allow"),
            app=_rule(
                "deny",
                code="public_mcp_tool_forbidden",
                message="Public Xpert Apps cannot deploy direct MCP tool nodes.",
            ),
            evaluation=_rule(
                "deny",
                code="evaluation_mcp_tool_forbidden",
                message="Evaluation does not allow direct MCP tool nodes.",
            ),
            evolution=_rule("deny"),
        ),
        planner=_planner(),
    )
    contracts["terminate_error"] = NodeContract(
        kind="terminate_error",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "errorCode": {
                    "type": "string",
                    "pattern": r"^[A-Z][A-Z0-9_]{0,63}$",
                },
                "message": {"type": "string", "minLength": 1, "maxLength": 2_000},
            },
            required=["errorCode", "message"],
        ),
        edge=NodeEdgeContract(modes=(), topology_modes=()),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="control",
        ),
        planner=_planner(),
    )
    contracts["multi_route"] = NodeContract(
        kind="multi_route",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "routes": {
                    "type": "array",
                    "items": route_rule_schema,
                    "minItems": 2,
                    "maxItems": 8,
                },
            },
            required=["inputVariable", "routes"],
        ),
        ports=(
            NodePortContract(
                name="value",
                direction="input",
                value_schema=any_value,
                required=True,
            ),
        ),
        edge=NodeEdgeContract(
            allowed_source_handles=(
                "route_1",
                "route_2",
                "route_3",
                "route_4",
                "route_5",
                "route_6",
                "route_7",
                "route_8",
                "default",
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="control",
        ),
        planner=_planner(),
    )
    document_asset_schema = _object_schema(
        {
            "assetIdVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["assetIdVariable", "outputVariable"],
    )
    document_asset_schema["not"] = {"required": ["contractVersion"]}
    document_legacy_schema = _object_schema(
        {
            "sourcePathVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["sourcePathVariable", "outputVariable"],
    )
    document_legacy_schema["not"] = {"required": ["contractVersion"]}
    document_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "assetIdVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
        },
        required=["contractVersion", "assetIdVariable", "outputVariable"],
    )
    document_v3_http_schema = _object_schema(
        {
            "contractVersion": {"const": 3},
            "sourceMode": {"const": "http_response"},
            "inputVariable": {"type": "string"},
            "format": {
                "type": "string",
                "enum": ["auto", "html", "markdown", "xml"],
            },
            "outputMode": {"type": "string", "enum": ["structured", "text"]},
            "outputVariable": {"type": "string"},
        },
        required=[
            "contractVersion",
            "sourceMode",
            "inputVariable",
            "format",
            "outputMode",
            "outputVariable",
        ],
    )
    document_v3_http_schema["not"] = {
        "anyOf": [
            {"required": ["assetIdVariable"]},
            {"required": ["sourcePathVariable"]},
        ]
    }
    document_v3_file_schema = _object_schema(
        {
            "contractVersion": {"const": 3},
            "sourceMode": {"const": "file_asset"},
            "assetIdVariable": {"type": "string"},
            "format": {"const": "auto"},
            "outputMode": {"type": "string", "enum": ["structured", "text"]},
            "outputVariable": {"type": "string"},
        },
        required=[
            "contractVersion",
            "sourceMode",
            "assetIdVariable",
            "format",
            "outputMode",
            "outputVariable",
        ],
    )
    document_v3_file_schema["not"] = {
        "anyOf": [
            {"required": ["inputVariable"]},
            {"required": ["sourcePathVariable"]},
        ]
    }
    contracts["document_extractor"] = NodeContract(
        kind="document_extractor",
        contract_status="complete",
        config_schema={
            "type": "object",
            "anyOf": [
                document_asset_schema,
                document_legacy_schema,
                document_v2_schema,
                document_v3_http_schema,
                document_v3_file_schema,
            ],
        },
        ports=(
            NodePortContract(name="content", direction="input", value_schema=any_value),
            NodePortContract(name="parsed", direction="output", value_schema=any_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="read",
            deterministic=True,
            idempotent=True,
            external_io=False,
            error_semantics="fail_closed",
            security_category="file_read",
        ),
        availability=private_content_availability,
        resources=(
            NodeResourceContract(
                kind="file_asset", id_field="assetIdVariable", dynamic_schema=True
            ),
        ),
        planner=_planner(),
    )
    time_v1_schema = _object_schema(
        {
            "operation": {"type": "string", "enum": ["now_iso", "now_epoch", "format"]},
            "formatString": {"type": "string", "maxLength": 200},
            "outputVariable": {"type": "string"},
        },
        required=["operation", "outputVariable"],
    )
    time_v1_schema["not"] = {"required": ["contractVersion"]}
    time_v2_schema = _object_schema(
        {
            "contractVersion": {"const": 2},
            "operation": {
                "type": "string",
                "enum": [
                    "now",
                    "to_iso",
                    "format",
                    "add",
                    "subtract",
                    "difference",
                    "start_of",
                    "end_of",
                ],
            },
            "inputVariable": {"type": "string"},
            "rightVariable": {"type": "string"},
            "outputVariable": {"type": "string"},
            "timezone": {"type": "string", "minLength": 1, "maxLength": 128},
            "formatString": {"type": "string", "minLength": 1, "maxLength": 200},
            "amount": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
            "unit": {
                "type": "string",
                "enum": [
                    "second",
                    "minute",
                    "hour",
                    "day",
                    "week",
                    "month",
                    "year",
                    "seconds",
                    "minutes",
                    "hours",
                    "days",
                    "weeks",
                    "months",
                    "years",
                ],
            },
        },
        required=["contractVersion", "operation", "outputVariable", "timezone"],
    )
    contracts["time_tool"] = NodeContract(
        kind="time_tool",
        contract_status="complete",
        config_schema={"type": "object", "anyOf": [time_v1_schema, time_v2_schema]},
        ports=(
            NodePortContract(name="time", direction="input", value_schema=any_value),
            NodePortContract(name="result", direction="output", value_schema=any_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=False,
            idempotent=False,
            error_semantics="fail_closed",
            security_category="transform",
        ),
        planner=_planner(),
    )
    object_binding_schema = _object_schema(
        {
            "source": {"type": "string", "enum": ["literal", "variable"]},
            "variable": {"type": "string"},
            "valueType": {
                "type": "string",
                "enum": ["text", "number", "boolean", "null", "json"],
            },
            "value": {},
        },
        required=["source"],
        additional_properties=False,
    )
    object_operation_schema = _object_schema(
        {
            "id": {"type": "string", "minLength": 1, "maxLength": 64},
            "operation": {
                "type": "string",
                "enum": ["set", "set_default", "rename", "remove", "keep_only"],
            },
            "sourceField": {"type": "string", "maxLength": 128},
            "targetField": {"type": "string", "maxLength": 128},
            "fields": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
                "minItems": 1,
                "maxItems": 50,
                "uniqueItems": True,
            },
            "binding": object_binding_schema,
        },
        required=["id", "operation"],
        additional_properties=False,
    )
    contracts["object_transform"] = NodeContract(
        kind="object_transform",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "outputVariable": {"type": "string"},
                "operations": {
                    "type": "array",
                    "items": object_operation_schema,
                    "minItems": 1,
                    "maxItems": 20,
                },
            },
            required=["inputVariable", "outputVariable", "operations"],
        ),
        ports=(
            NodePortContract(name="object", direction="input", value_schema=object_value),
            NodePortContract(name="result", direction="output", value_schema=object_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="transform",
        ),
        planner=_planner(),
    )
    file_column_schema = _object_schema(
        {
            "id": {"type": "string", "minLength": 1, "maxLength": 64},
            "field": {"type": "string", "minLength": 1, "maxLength": 128},
            "label": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        required=["id", "field", "label"],
        additional_properties=False,
    )
    contracts["file_output"] = NodeContract(
        kind="file_output",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "outputVariable": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["plain_text", "markdown", "json", "csv", "pdf", "docx", "xlsx"],
                },
                "filenameTemplate": {"type": "string", "minLength": 1, "maxLength": 150},
                "titleTemplate": {"type": "string", "maxLength": 500},
                "columns": {
                    "type": "array",
                    "items": file_column_schema,
                    "maxItems": 200,
                },
            },
            required=["inputVariable", "outputVariable", "format", "filenameTemplate"],
        ),
        ports=(
            NodePortContract(name="content", direction="input", value_schema=any_value),
            NodePortContract(name="file", direction="output", value_schema=object_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="write",
            deterministic=False,
            idempotent=True,
            external_io=False,
            error_semantics="fail_closed",
            security_category="file_write",
        ),
        availability=private_file_availability,
        planner=_planner(),
    )
    contracts["list_operation"] = NodeContract(
        kind="list_operation",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "outputVariable": {"type": "string"},
                "operator": {
                    "type": "string",
                    "enum": [
                        "length",
                        "join",
                        "first",
                        "last",
                        "filter",
                        "sort",
                        "deduplicate",
                        "take",
                        "skip",
                        "slice",
                    ],
                },
                "joinSeparator": {"type": "string", "maxLength": 1_000},
                "filterMode": {"type": "string", "enum": ["all", "any"]},
                "filterRules": {
                    "type": "array",
                    "items": filter_rule_schema,
                    "minItems": 1,
                    "maxItems": 10,
                },
                "sortKeys": {
                    "type": "array",
                    "items": _object_schema(
                        {
                            "field": {"type": "string", "maxLength": 64},
                            "direction": {"type": "string", "enum": ["asc", "desc"]},
                            "nulls": {"type": "string", "enum": ["first", "last"]},
                        },
                        required=["field", "direction", "nulls"],
                        additional_properties=False,
                    ),
                    "minItems": 1,
                    "maxItems": 3,
                },
                "deduplicateFields": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 64},
                    "maxItems": 5,
                    "uniqueItems": True,
                },
                "count": {"type": "integer", "minimum": 0, "maximum": 10_000},
                "startIndex": {"type": "integer", "minimum": 0, "maximum": 10_000},
                "endIndex": {"type": "integer", "minimum": 0, "maximum": 10_000},
            },
            required=["inputVariable", "operator", "outputVariable"],
        ),
        ports=(
            NodePortContract(name="list", direction="input", value_schema=any_value),
            NodePortContract(name="result", direction="output", value_schema=any_value),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="transform",
        ),
        planner=_planner(),
    )
    contracts["data_aggregate"] = NodeContract(
        kind="data_aggregate",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "inputVariable": {"type": "string"},
                "outputVariable": {"type": "string"},
                "groupByFields": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 64},
                    "maxItems": 3,
                    "uniqueItems": True,
                },
                "measures": {
                    "type": "array",
                    "items": _object_schema(
                        {
                            "outputField": {
                                "type": "string",
                                "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                            },
                            "operation": {
                                "type": "string",
                                "enum": ["count", "sum", "avg", "min", "max"],
                            },
                            "sourceField": {"type": "string", "maxLength": 64},
                        },
                        required=["outputField", "operation"],
                        additional_properties=False,
                    ),
                    "minItems": 1,
                    "maxItems": 10,
                },
            },
            required=[
                "inputVariable",
                "outputVariable",
                "groupByFields",
                "measures",
            ],
        ),
        ports=(
            NodePortContract(
                name="rows",
                direction="input",
                value_schema=array_object_value,
                required=True,
            ),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=array_object_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="transform",
        ),
        planner=_planner(),
    )
    contracts["data_merge"] = NodeContract(
        kind="data_merge",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "contractVersion": {"const": 1},
                "mergeMode": {
                    "type": "string",
                    "enum": ["append", "keyed_join"],
                },
                "leftVariable": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                },
                "rightVariable": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                },
                "outputVariable": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                },
                "keyFields": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                    },
                    "maxItems": 3,
                    "uniqueItems": True,
                },
            },
            required=[
                "contractVersion",
                "mergeMode",
                "leftVariable",
                "rightVariable",
                "outputVariable",
                "keyFields",
            ],
        ),
        ports=(
            NodePortContract(
                name="left",
                direction="input",
                value_schema=WorkflowValueSchema(type="array"),
                required=True,
            ),
            NodePortContract(
                name="right",
                direction="input",
                value_schema=WorkflowValueSchema(type="array"),
                required=True,
            ),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=WorkflowValueSchema(type="array"),
            ),
        ),
        edge=NodeEdgeContract(allowed_target_handles=("left", "right")),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="transform",
        ),
        planner=_planner(),
    )
    contracts["dataset_compare"] = NodeContract(
        kind="dataset_compare",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "leftVariable": {"type": "string"},
                "rightVariable": {"type": "string"},
                "keyFields": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 64},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                },
                "includeUnchanged": {"type": "boolean"},
                "outputVariable": {"type": "string"},
            },
            required=[
                "leftVariable",
                "rightVariable",
                "keyFields",
                "includeUnchanged",
                "outputVariable",
            ],
        ),
        ports=(
            NodePortContract(
                name="left",
                direction="input",
                value_schema=array_object_value,
                required=True,
            ),
            NodePortContract(
                name="right",
                direction="input",
                value_schema=array_object_value,
                required=True,
            ),
            NodePortContract(
                name="result",
                direction="output",
                value_schema=object_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="none",
            deterministic=True,
            idempotent=True,
            error_semantics="fail_closed",
            security_category="transform",
        ),
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

    contracts["knowledge_write_proposal"] = NodeContract(
        kind="knowledge_write_proposal",
        contract_status="complete",
        config_schema=_object_schema(
            {
                "contractVersion": {"const": 1},
                "knowledgeBaseId": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "pattern": r"^[^{}]+$",
                },
                "titleTemplate": {"type": "string", "minLength": 1, "maxLength": 2_000},
                "contentVariable": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                },
                "tags": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "minLength": 1, "maxLength": 50},
                },
                "outputVariable": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$",
                },
            },
            required=[
                "contractVersion",
                "knowledgeBaseId",
                "titleTemplate",
                "contentVariable",
                "tags",
                "outputVariable",
            ],
        ),
        ports=(
            NodePortContract(
                name="content",
                direction="input",
                value_schema=string_value,
            ),
            NodePortContract(
                name="proposal",
                direction="output",
                value_schema=object_value,
            ),
        ),
        execution=NodeExecutionPolicy(
            side_effect="write",
            deterministic=False,
            idempotent=True,
            external_io=True,
            can_wait=False,
            error_semantics="fail_closed",
            security_category="knowledge_write_proposal",
        ),
        availability=NodeAvailabilityPolicy(
            workflow=_rule("allow"),
            xpert=_rule("allow"),
            goal=_rule("allow"),
            handoff=_rule("allow"),
            app=_rule(
                "deny",
                code="public_knowledge_write_proposal_forbidden",
                message="Public Xpert Apps cannot create Knowledge Inbox proposals.",
            ),
            evaluation=_rule(
                "deny",
                code="evaluation_knowledge_write_proposal_forbidden",
                message="Evaluation cannot create Knowledge Inbox proposals.",
            ),
            evolution=_rule(
                "deny",
                code="evolution_knowledge_write_proposal_forbidden",
                message="Evolution cannot create Knowledge Inbox proposals.",
            ),
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
                "contractVersion": 1,
                "knowledgeBaseId": "",
                "titleTemplate": "Knowledge proposal",
                "contentVariable": "proposal_content",
                "tags": [],
                "outputVariable": "knowledge_proposal",
            },
            constraints={
                "required": [
                    "knowledgeBaseId",
                    "titleTemplate",
                    "contentVariable",
                    "outputVariable",
                ],
                "tags_max": 20,
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
    contracts["knowledge_citation"] = contracts["knowledge_citation"].model_copy(
        update={
            "deprecated": True,
            "replacement_kind": "knowledge_retrieval",
        }
    )
    contracts["template_transform"] = contracts["template_transform"].model_copy(
        update={
            "deprecated": True,
            "replacement_kind": "variable_assign",
        }
    )
    contracts["agent"] = contracts["agent"].model_copy(
        update={
            "deprecated": True,
            "replacement_kind": "workflow_agent",
        }
    )

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

    return NodeContractRegistry(list(contracts.values()))


workflow_node_contract_registry = build_builtin_node_contract_registry()
node_policy_service = NodePolicyService(workflow_node_contract_registry)
