from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue, model_serializer, model_validator

from .values import normalize_workflow_value


NativeNodeKind = Literal[
    "input",
    "scheduled_start",
    "http_event_entry",
    "failure_event_entry",
    "workflow_call_entry",
    "invoke_workflow",
    "llm",
    "condition",
    "code",
    "variable_assign",
    "template_transform",
    "variable_aggregator",
    "parameter_extractor",
    "knowledge_retrieval",
    "knowledge_citation",
    "document_extractor",
    "vision_understanding",
    "human_intervention",
    "question_classifier",
    "agent",
    "workflow_agent",
    "external_xpert",
    "knowledge_base",
    "toolset_resource",
    "plugin_resource",
    "agent_task",
    "agent_handoff",
    "handoff_router",
    "mcp_tool",
    "time_tool",
    "http_request",
    "terminate_error",
    "multi_route",
    "list_operation",
    "data_aggregate",
    "dataset_compare",
    "iteration",
    "json_serialize",
    "json_deserialize",
    "data_table_query",
    "data_table_insert",
    "data_table_update",
    "data_table_delete",
    "annotation",
    "runtime_middleware",
    "suspend_wait",
    "http_event_reply",
    "output",
]
IssueSeverity = Literal["error", "warning"]

_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:^|_)(?:secret|password|passwd|api_key|access_token|refresh_token|credential|private_key|env|environment)(?:$|_)",
    re.IGNORECASE,
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|/|~[\\/]|file://)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"^(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----|\$\{?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\}?)$",
    re.IGNORECASE,
)


def _contains_sensitive_workflow_value(value: JsonValue) -> bool:
    if isinstance(value, str):
        clean_value = value.strip()
        return bool(
            _ABSOLUTE_PATH_PATTERN.match(clean_value)
            or _SENSITIVE_VALUE_PATTERN.match(clean_value)
        )
    if isinstance(value, list):
        return any(_contains_sensitive_workflow_value(item) for item in value)
    if isinstance(value, dict):
        return any(
            _SENSITIVE_NAME_PATTERN.search(str(key))
            or _contains_sensitive_workflow_value(item)
            for key, item in value.items()
        )
    return False


def workflow_variable_value_matches_type(
    value_type: str,
    value: JsonValue,
) -> bool:
    if value_type == "text":
        return isinstance(value, str)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    return True


class WorkflowVariableDeclaration(BaseModel):
    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["input", "constant"]
    valueType: Literal["text", "number", "boolean", "json"]
    defaultValue: JsonValue | None = None
    description: str | None = Field(default=None, max_length=500)

    @model_serializer(mode="wrap")
    def serialize_declaration(self, handler):
        data = handler(self)
        if "defaultValue" not in self.model_fields_set:
            data.pop("defaultValue", None)
        return data

    @model_validator(mode="after")
    def validate_declaration(self) -> "WorkflowVariableDeclaration":
        if not _VARIABLE_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("workflow_variable_invalid_name")
        if _SENSITIVE_NAME_PATTERN.search(self.name):
            raise ValueError("workflow_variable_sensitive_name")
        has_default = "defaultValue" in self.model_fields_set
        if self.kind == "constant" and not has_default:
            raise ValueError("workflow_constant_requires_value")
        if not has_default:
            return self
        normalized = normalize_workflow_value(
            self.defaultValue,
            path=f"$.variables.{self.name}.defaultValue",
        )
        if not workflow_variable_value_matches_type(self.valueType, normalized):
            raise ValueError("workflow_variable_default_type_mismatch")
        if _contains_sensitive_workflow_value(normalized):
            raise ValueError("workflow_variable_sensitive_value")
        self.defaultValue = normalized
        return self


class WorkflowPosition(BaseModel):
    x: float = 0
    y: float = 0


class NativeWorkflowNode(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: NativeNodeKind | str | None = None
    position: WorkflowPosition | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class NativeWorkflowEdge(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    sourceHandle: str | None = None
    targetHandle: str | None = None


class NativeWorkflowDefinition(BaseModel):
    id: str = Field(default="draft", max_length=128)
    title: str = Field(default="Untitled workflow", max_length=120)
    version: str = Field(default="native-draft", max_length=40)
    source: Literal["workflow-native", "classic", "dify-import"] = "workflow-native"
    variables: list[WorkflowVariableDeclaration] = Field(
        default_factory=list,
        max_length=100,
    )
    nodes: list[NativeWorkflowNode] = Field(default_factory=list, max_length=80)
    edges: list[NativeWorkflowEdge] = Field(default_factory=list, max_length=120)


class ValidateWorkflowRequest(BaseModel):
    workflow: NativeWorkflowDefinition


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: IssueSeverity = "error"
    node_id: str | None = None
    edge_id: str | None = None


class ValidateWorkflowResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    node_count: int
    edge_count: int


class NativeTemplatePayload(BaseModel):
    id: str
    title: str
    description: str
    workflow: NativeWorkflowDefinition
