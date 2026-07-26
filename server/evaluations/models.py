from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


MetricKind = Literal[
    "exact_match",
    "contains",
    "json_schema",
    "citation_hit",
    "rubric_judge",
]


class EvaluationMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class EvaluationExpectation(BaseModel):
    exact_answer: str | None = Field(default=None, max_length=20_000)
    contains: list[str] = Field(default_factory=list, max_length=20)
    json_schema: dict[str, Any] | None = None
    citation_ids: list[str] = Field(default_factory=list, max_length=50)
    chunk_ids: list[str] = Field(default_factory=list, max_length=50)
    document_names: list[str] = Field(default_factory=list, max_length=50)
    rubric: str | None = Field(default=None, max_length=4_000)


class EvaluationCaseInput(BaseModel):
    case_id: str | None = Field(default=None, max_length=120)
    name: str = Field(default="", max_length=160)
    message: str = Field(min_length=1, max_length=20_000)
    messages: list[EvaluationMessage] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    expected: EvaluationExpectation = Field(default_factory=EvaluationExpectation)
    weights: dict[MetricKind, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_weights(self) -> "EvaluationCaseInput":
        for key, value in self.weights.items():
            if not 0 <= float(value) <= 10:
                raise ValueError(f"Metric weight must be between 0 and 10: {key}")
        return self


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)


class DatasetUpdateRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    status: Literal["draft", "archived"] | None = None


class DatasetCasesRequest(BaseModel):
    revision: int = Field(ge=1)
    cases: list[EvaluationCaseInput] = Field(min_length=1, max_length=500)
    replace: bool = False


class DatasetPublishRequest(BaseModel):
    revision: int = Field(ge=1)
    release_notes: str = Field(default="", max_length=2_000)


class ConversationImportSelection(BaseModel):
    xpert_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    message_ids: list[str] = Field(default_factory=list, max_length=100)


class ConversationImportRequest(BaseModel):
    revision: int = Field(ge=1)
    selections: list[ConversationImportSelection] = Field(min_length=1, max_length=50)


class EvaluationTargetRequest(BaseModel):
    kind: Literal["xpert_version", "proposal"]
    xpert_id: str | None = Field(default=None, max_length=200)
    version: int | None = Field(default=None, ge=1)
    proposal_id: str | None = Field(default=None, max_length=200)
    proposal_revision: int | None = Field(default=None, ge=1)
    label: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_reference(self) -> "EvaluationTargetRequest":
        if self.kind == "xpert_version":
            if not self.xpert_id or self.version is None:
                raise ValueError("xpert_id and version are required for xpert_version.")
        elif not self.proposal_id or self.proposal_revision is None:
            raise ValueError(
                "proposal_id and proposal_revision are required for proposal."
            )
        return self


class EvaluationBudget(BaseModel):
    repetitions: int = Field(default=1, ge=1, le=3)
    max_concurrency: int = Field(default=2, ge=1, le=4)
    case_timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_model_calls: int = Field(default=16, ge=1, le=64)
    max_tool_calls: int = Field(default=24, ge=0, le=100)
    max_estimated_tokens: int = Field(default=64_000, ge=1_000, le=500_000)
    max_output_chars: int = Field(default=20_000, ge=1_000, le=50_000)


class EvaluationRunRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: int = Field(ge=1)
    case_ids: list[str] = Field(default_factory=list, max_length=100)
    baseline: EvaluationTargetRequest | None = None
    candidates: list[EvaluationTargetRequest] = Field(min_length=1, max_length=5)
    model_policy: Literal["snapshot", "override"] = "snapshot"
    override_model_id: str | None = Field(default=None, max_length=300)
    judge_model_id: str | None = Field(default=None, max_length=300)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    budget: EvaluationBudget = Field(default_factory=EvaluationBudget)

    @model_validator(mode="after")
    def validate_model_policy(self) -> "EvaluationRunRequest":
        if self.model_policy == "override" and not self.override_model_id:
            raise ValueError("override_model_id is required for override policy.")
        return self


class EvaluationPreflightRequest(BaseModel):
    baseline: EvaluationTargetRequest | None = None
    candidates: list[EvaluationTargetRequest] = Field(min_length=1, max_length=5)
    model_policy: Literal["snapshot", "override"] = "snapshot"
    override_model_id: str | None = Field(default=None, max_length=300)
