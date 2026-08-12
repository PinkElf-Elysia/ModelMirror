from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, JsonValue


AgentTableStatus = Literal["draft", "published", "archived"]
AgentTableFieldType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "datetime",
    "json",
]


class AgentTableField(BaseModel):
    field_id: str = ""
    name: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    data_type: AgentTableFieldType
    required: bool = False
    has_default: bool = False
    default_value: JsonValue = None


class AgentTableDefinition(BaseModel):
    table_id: str
    name: str
    description: str = ""
    status: AgentTableStatus = "draft"
    draft_revision: int = Field(default=1, ge=1)
    active_schema_version: int | None = None
    fields: list[AgentTableField] = Field(default_factory=list)
    created_at: float
    updated_at: float


class AgentTableSchemaVersion(BaseModel):
    table_id: str
    version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    fields: list[AgentTableField]
    checksum: str
    published_at: float


class AgentTableRecord(BaseModel):
    record_id: str
    table_id: str
    schema_version: int = Field(ge=1)
    data: dict[str, JsonValue]
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float


class AgentTableAuditEntry(BaseModel):
    audit_id: str
    table_id: str
    operation: str
    record_id: str | None = None
    schema_version: int | None = None
    affected_count: int = Field(default=0, ge=0)
    created_at: float


class AgentTableValidationResult(BaseModel):
    valid: bool
    table_id: str
    draft_revision: int
    issues: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)


class AgentTableDetail(BaseModel):
    table: AgentTableDefinition
    schema_versions: list[AgentTableSchemaVersion] = Field(default_factory=list)
    record_count: int = Field(default=0, ge=0)
