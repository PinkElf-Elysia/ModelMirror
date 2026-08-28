from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LiteraturePhase = Literal["not_started", "queued", "running", "terminal"]
LiteratureOutcome = Literal[
    "completed", "cancelled", "failed", "infrastructure_error"
]
IntegrityStatus = Literal["pending", "verified", "failed"]


class FrozenRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProjectCreateRequest(FrozenRequestModel):
    title: str = Field(min_length=1, max_length=120)
    research_question: str = Field(
        alias="researchQuestion", min_length=1, max_length=5000
    )
    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("title", "research_question")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must contain non-whitespace characters")
        return value


class ProjectUpdateRequest(FrozenRequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    research_question: str | None = Field(
        default=None, alias="researchQuestion", min_length=1, max_length=5000
    )

    @field_validator("title", "research_question")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must contain non-whitespace characters")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "ProjectUpdateRequest":
        if self.title is None and self.research_question is None:
            raise ValueError("at least one editable field is required")
        return self


class LiteratureRunCreateRequest(FrozenRequestModel):
    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    collection_id: str | None = Field(
        default=None,
        alias="collectionId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class LiteratureUnlockRequest(FrozenRequestModel):
    username: str = Field(
        min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    password: str = Field(min_length=1, max_length=1024)


class LiteratureSessionView(BaseModel):
    status: Literal["locked", "ready", "expired"]
    username: str | None


class LiteratureAttemptView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    ldr_research_id: str | None = Field(alias="ldrResearchId")
    phase: LiteraturePhase
    outcome: LiteratureOutcome | None
    raw_status: str | None = Field(alias="rawStatus")
    cancel_requested_at: str | None = Field(alias="cancelRequestedAt")
    cancel_applied_at: str | None = Field(alias="cancelAppliedAt")
    started_at: str | None = Field(alias="startedAt")
    terminal_at: str | None = Field(alias="terminalAt")
    synced_at: str | None = Field(alias="syncedAt")
    error_type: str | None = Field(alias="errorType")
    error_message: str | None = Field(alias="errorMessage")
    integrity_status: IntegrityStatus = Field(alias="integrityStatus")
    created_at: str = Field(alias="createdAt")
    progress: int = Field(ge=0, le=100)
    latest_log: dict[str, object] | None = Field(alias="latestLog")


class ProjectView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    project_id: str = Field(alias="projectId", pattern=r"^rp_[0-9a-f]{32}$")
    title: str
    research_question: str = Field(alias="researchQuestion")
    domain: Literal["ai_agent"]
    current_stage: Literal["literature"] = Field(alias="currentStage")
    stages: dict[str, Literal["active", "not_available"]]
    literature_phase: LiteraturePhase = Field(alias="literaturePhase")
    literature_outcome: LiteratureOutcome | None = Field(alias="literatureOutcome")
    active_run_id: str | None = Field(alias="activeRunId")
    completed_run_id: str | None = Field(alias="completedRunId")
    collection_id: str | None = Field(alias="collectionId")
    profile_id: str = Field(alias="profileId")
    model_id: str | None = Field(alias="modelId")
    attempts: list[LiteratureAttemptView]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProjectListResponse(BaseModel):
    items: list[ProjectView]
    next_cursor: str | None = Field(alias="nextCursor")
