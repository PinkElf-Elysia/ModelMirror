from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ProjectStatus = Literal["active", "archived"]
SourceStatus = Literal["pending", "processing", "ready", "failed"]
IndicatorStatus = Literal["draft", "published", "archived"]
ProposalStatus = Literal["pending", "approved", "rejected", "cancelled"]
FieldRole = Literal["dimension", "time", "measure", "hidden"]


class DataXProject(BaseModel):
    project_id: str
    name: str
    description: str = ""
    status: ProjectStatus = "active"
    revision: int = 1
    created_at: float
    updated_at: float


class DataSourceSnapshot(BaseModel):
    source_id: str
    project_id: str
    name: str
    file_name: str
    file_type: Literal["csv", "xlsx", "parquet"]
    content_sha256: str
    byte_size: int
    row_count: int = 0
    column_count: int = 0
    table_name: str
    status: SourceStatus = "pending"
    profile: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: float
    updated_at: float


class DataXImportJob(BaseModel):
    job_id: str
    project_id: str
    source_id: str
    status: SourceStatus = "pending"
    attempt_count: int = 0
    lease_token: str | None = None
    lease_expires_at: float | None = None
    error: str = ""
    created_at: float
    updated_at: float
    completed_at: float | None = None


class SemanticEntity(BaseModel):
    entity_id: str
    source_id: str
    alias: str
    label: str = ""


class SemanticJoin(BaseModel):
    left_entity_id: str
    left_field: str
    right_entity_id: str
    right_field: str
    join_type: Literal["inner", "left"] = "left"


class ModelField(BaseModel):
    field_id: str
    entity_id: str
    source_field: str
    name: str
    label: str = ""
    data_type: str = "VARCHAR"
    role: FieldRole = "dimension"


class SemanticModel(BaseModel):
    model_id: str
    project_id: str
    name: str
    description: str = ""
    entities: list[SemanticEntity] = Field(default_factory=list, min_length=1, max_length=5)
    joins: list[SemanticJoin] = Field(default_factory=list)
    fields: list[ModelField] = Field(default_factory=list)
    revision: int = 1
    created_at: float
    updated_at: float


class IndicatorFilter(BaseModel):
    field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"] = "eq"
    value: Any


class IndicatorDefinition(BaseModel):
    indicator_id: str
    project_id: str
    model_id: str
    code: str
    name: str
    description: str = ""
    indicator_type: Literal["basic", "derived"] = "basic"
    aggregation: Literal["sum", "count", "count_distinct", "avg", "min", "max"] | None = None
    measure_field: str | None = None
    formula: str | None = None
    default_dimensions: list[str] = Field(default_factory=list)
    time_field: str | None = None
    filters: list[IndicatorFilter] = Field(default_factory=list)
    status: IndicatorStatus = "draft"
    revision: int = 1
    current_version: int | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float


class IndicatorVersion(BaseModel):
    version_id: str
    indicator_id: str
    version: int
    snapshot: dict[str, Any]
    content_sha256: str
    published_at: float


class IndicatorProposal(BaseModel):
    proposal_id: str
    project_id: str
    model_id: str
    indicator_id: str | None = None
    proposal_type: Literal["create", "update"] = "create"
    title: str
    payload: dict[str, Any]
    status: ProposalStatus = "pending"
    revision: int = 1
    source_xpert_id: str | None = None
    source_run_id: str | None = None
    source_goal_id: str | None = None
    source_handoff_id: str | None = None
    created_at: float
    updated_at: float
    resolved_at: float | None = None
    operator: str | None = None
    reason: str = ""


class DataXResultArtifact(BaseModel):
    artifact_id: str
    project_id: str
    model_id: str
    view: Literal["kpi", "table", "line", "bar"] = "table"
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    created_at: float
