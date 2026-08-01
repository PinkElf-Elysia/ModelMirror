from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


NativeNodeKind = Literal[
    "input",
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
    "list_operation",
    "iteration",
    "runtime_middleware",
    "output",
]
IssueSeverity = Literal["error", "warning"]


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
