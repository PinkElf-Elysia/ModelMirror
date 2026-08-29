from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CaseId = Literal["success", "task_error", "long_running_cancel"]
Phase = Literal["queued", "running", "terminal"]
Outcome = Literal["success", "task_error", "cancelled", "infrastructure_error"]
InspectStatus = Literal["started", "success", "error", "cancelled"]
EvidenceState = Literal["pending", "synced", "failed"]


class FrozenRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RunCreateRequest(FrozenRequestModel):
    fixture_id: Literal["inspect-smoke-v1"] = Field(alias="fixtureId")
    case_id: CaseId = Field(alias="caseId")
    idempotency_key: str = Field(
        alias="idempotencyKey", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    tenant_id: Literal["local"] = Field(default="local", alias="tenantId")
    project_id: Literal["local"] = Field(default="local", alias="projectId")
    actor_id: Literal["local"] = Field(default="local", alias="actorId")


class RunView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    fixture_id: str = Field(alias="fixtureId")
    case_id: CaseId = Field(alias="caseId")
    tenant_id: Literal["local"] = Field(alias="tenantId")
    project_id: Literal["local"] = Field(alias="projectId")
    actor_id: Literal["local"] = Field(alias="actorId")
    phase: Phase
    outcome: Outcome | None
    inspect_status: InspectStatus | None = Field(alias="inspectStatus")
    cancel_requested: bool = Field(alias="cancelRequested")
    cancel_applied: bool = Field(alias="cancelApplied")
    evidence_state: EvidenceState = Field(alias="evidenceState")
    error_type: str | None = Field(alias="errorType")
    error_message: str | None = Field(alias="errorMessage")
    replay_verified: bool = Field(alias="replayVerified")
    mlflow_run_id: str | None = Field(alias="mlflowRunId")
    created_at: str = Field(alias="createdAt")
    started_at: str | None = Field(alias="startedAt")
    cancel_requested_at: str | None = Field(alias="cancelRequestedAt")
    cancel_applied_at: str | None = Field(alias="cancelAppliedAt")
    terminal_at: str | None = Field(alias="terminalAt")
    evidence_synced_at: str | None = Field(alias="evidenceSyncedAt")
    updated_at: str = Field(alias="updatedAt")

    @model_validator(mode="after")
    def validate_terminal_contract(self) -> "RunView":
        if self.phase == "terminal" and self.outcome is None:
            raise ValueError("terminal runs require an outcome")
        if self.cancel_applied and not self.cancel_requested:
            raise ValueError("cancelApplied requires cancelRequested")
        return self


class EventView(BaseModel):
    sequence: int
    event_type: str = Field(alias="eventType")
    payload: dict[str, object]
    created_at: str = Field(alias="createdAt")


class RunListResponse(BaseModel):
    items: list[RunView]
    next_cursor: str | None = Field(alias="nextCursor")


class RunSummaryResponse(BaseModel):
    total: int
    phases: dict[Phase, int]
    outcomes: dict[Outcome, int]
    evidence_states: dict[EvidenceState, int] = Field(alias="evidenceStates")
    updated_at: str | None = Field(alias="updatedAt")


class EventListResponse(BaseModel):
    items: list[EventView]
    next_sequence: int = Field(alias="nextSequence")


class ReadyView(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class SystemCheckView(BaseModel):
    id: str
    status: Literal["ready", "not_ready"]
    required: bool


class SystemView(BaseModel):
    status: Literal["ready", "degraded", "not_ready"]
    checks: list[SystemCheckView]
    checked_at: str = Field(alias="checkedAt")
    literature_capability: dict[str, object] = Field(
        default_factory=dict, alias="literatureCapability"
    )


class FixtureCapabilityClaimView(BaseModel):
    enabled: Literal[True] = True
    claim_level: Literal["harness_only"] = Field(
        default="harness_only", alias="claimLevel"
    )
    pack_status: Literal["fixture_only"] = Field(
        default="fixture_only", alias="packStatus"
    )


class LiteratureCapabilityClaimView(BaseModel):
    enabled: Literal[True] = True
    scientific_claim: Literal["none"] = Field(
        default="none", alias="scientificClaim"
    )
    acceptance_state: Literal["pending_live_acceptance"] = Field(
        default="pending_live_acceptance", alias="acceptanceState"
    )
    workflow_source: Literal["local_deep_research"] = Field(
        default="local_deep_research", alias="workflowSource"
    )


class CapabilityClaimsView(BaseModel):
    fixture_execution: FixtureCapabilityClaimView = Field(
        default_factory=FixtureCapabilityClaimView, alias="fixtureExecution"
    )
    literature_research: LiteratureCapabilityClaimView = Field(
        default_factory=LiteratureCapabilityClaimView, alias="literatureResearch"
    )


class ModuleInfoView(BaseModel):
    module_id: str = Field(alias="moduleId")
    module_version: str = Field(alias="moduleVersion")
    api_version: str = Field(alias="apiVersion")
    worker_protocol_version: int = Field(alias="workerProtocolVersion")
    fixtures: list[CaseId]
    runtimes: dict[str, str]
    capabilities: dict[str, bool]
    capability_claims: CapabilityClaimsView = Field(
        default_factory=CapabilityClaimsView, alias="capabilityClaims"
    )
    links: dict[str, str]
    limitations: list[str]


class ArtifactView(BaseModel):
    name: str
    size_bytes: int = Field(alias="sizeBytes")
    sha256: str
    download_url: str = Field(alias="downloadUrl")


class EvidenceView(BaseModel):
    run_id: str = Field(alias="runId")
    evidence_state: EvidenceState = Field(alias="evidenceState")
    integrity_status: Literal["pending", "verified", "failed"] = Field(
        alias="integrityStatus"
    )
    integrity_error: str | None = Field(alias="integrityError")
    verified_at: str = Field(alias="verifiedAt")
    receipt: dict[str, object] | None
    artifacts: list[ArtifactView]
    mlflow: dict[str, str | None]
    outbox: dict[str, object] | None
