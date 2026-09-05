from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MetricKind = Literal[
    "exact_match",
    "contains",
    "json_schema",
    "citation_hit",
    "tool_call_match",
    "workflow_path_match",
    "workflow_resource_match",
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
    required_tools: list[str] = Field(default_factory=list, max_length=30)
    forbidden_tools: list[str] = Field(default_factory=list, max_length=30)
    tool_order: list[str] = Field(default_factory=list, max_length=30)
    rubric: str | None = Field(default=None, max_length=4_000)


class EvaluationPathExpectation(BaseModel):
    required_outcomes: list[str] = Field(default_factory=list, max_length=32)
    forbidden_outcomes: list[str] = Field(default_factory=list, max_length=32)
    terminal: Literal["success", "error"]
    error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_path(self) -> "EvaluationPathExpectation":
        outcome_pattern = r"^[a-z][a-z0-9_-]{0,63}:(?:success|error|matched|unmatched|case_[1-8]|default)$"
        import re

        all_outcomes = [*self.required_outcomes, *self.forbidden_outcomes]
        if any(not re.fullmatch(outcome_pattern, item) for item in all_outcomes):
            raise ValueError("Path outcomes must use planner_ref:semantic_outcome.")
        if len(all_outcomes) != len(set(all_outcomes)):
            raise ValueError("Path outcomes must be unique across required and forbidden lists.")
        if self.terminal == "error":
            if not self.error_code or not re.fullmatch(
                r"^[A-Z][A-Z0-9_]{0,63}$", self.error_code
            ):
                raise ValueError("Expected error paths require a safe error_code.")
        elif self.error_code is not None:
            raise ValueError("Successful paths cannot declare an error_code.")
        return self


class EvaluationResourceReadExpectation(BaseModel):
    node_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: Literal["knowledge_retrieval", "data_table_query"]
    resource_id: str = Field(min_length=1, max_length=200)
    version_id: str | None = Field(default=None, max_length=200)
    schema_version: int | None = Field(default=None, ge=1)
    query_checksum: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    expected_count: int | None = Field(default=None, ge=0, le=200)
    record_ids: list[str] = Field(default_factory=list, max_length=200)
    citation_ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "EvaluationResourceReadExpectation":
        if self.kind == "knowledge_retrieval":
            if self.schema_version is not None or self.record_ids:
                raise ValueError(
                    "Knowledge resource assertions cannot declare table schema or record ids."
                )
        elif self.version_id is not None or self.citation_ids:
            raise ValueError(
                "Agent Table assertions cannot declare knowledge version or citation ids."
            )
        return self


class AgentTableQueryFixture(BaseModel):
    """Private evaluator input. API projections must never expose records."""

    model_config = ConfigDict(extra="forbid")

    fixture_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_id: str = Field(min_length=1, max_length=240)
    case_id: str = Field(min_length=1, max_length=120)
    node_id: str = Field(min_length=1, max_length=200)
    node_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    resource_kind: Literal["data_table_query"] = "data_table_query"
    resource_id: str = Field(min_length=1, max_length=200)
    table_id: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(ge=1)
    schema_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    query_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: list[str] | None = Field(default=None, max_length=50)
    filter: dict[str, Any] | None = None
    sort: list[dict[str, Any]] | None = Field(default=None, max_length=5)
    limit: int = Field(ge=1, le=200)
    return_mode: Literal["list", "first"] = "list"
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    records_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_count: int = Field(ge=0, le=200)
    record_ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_fixture_identity(self) -> "AgentTableQueryFixture":
        if self.resource_id != self.table_id:
            raise ValueError("Fixture resource_id must match table_id.")
        if self.result_count != len(self.records):
            raise ValueError("Fixture result_count must match the stored record count.")
        if self.records_checksum != _evaluation_checksum(self.records):
            raise ValueError("Fixture records checksum does not match its stored records.")
        identity = {
            "target_id": self.target_id,
            "case_id": self.case_id,
            "node_id": self.node_id,
        }
        if self.fixture_key != _evaluation_checksum(identity):
            raise ValueError("Fixture key does not match its target, case, and node.")
        query_contract = {
            "table_id": self.table_id,
            "schema_version": self.schema_version,
            "fields": self.fields,
            "filter": self.filter,
            "sort": self.sort,
            "limit": self.limit,
            "return_mode": self.return_mode,
        }
        if self.query_checksum != _evaluation_checksum(query_contract):
            raise ValueError("Fixture query checksum does not match its contract.")
        expected_record_ids = [
            str(record.get("record_id"))[:200]
            for record in self.records
            if str(record.get("record_id") or "")
        ][:200]
        if self.record_ids != expected_record_ids:
            raise ValueError("Fixture record ids do not match its stored records.")
        return self


def _evaluation_checksum(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EvaluationProfessionalEvidence(BaseModel):
    sufficient: bool = False
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    exact_focus_matches: list[str] = Field(default_factory=list, max_length=8)
    input_markers: list[str] = Field(default_factory=list, max_length=12)
    support_markers: list[str] = Field(default_factory=list, max_length=12)
    anchor_ids: list[str] = Field(default_factory=list, max_length=8)


class EvaluationCaseTargeting(BaseModel):
    blueprint_id: str = Field(default="", max_length=120)
    difficulty: Literal["basic", "edge", "adversarial"]
    target_refs: list[str] = Field(min_length=1, max_length=5)
    capability_matrix: list[str] = Field(default_factory=list, max_length=3)
    focus_terms: list[str] = Field(default_factory=list, max_length=8)
    pressure_types: list[
        Literal[
            "ambiguity",
            "conflicting_context",
            "missing_evidence",
            "competing_constraints",
            "schema_boundary",
            "tool_decoy",
            "cross_turn_override",
            "domain_exception",
        ]
    ] = Field(default_factory=list, max_length=4)
    rationale: str = Field(min_length=8, max_length=1_000)
    challenge: str = Field(default="", max_length=500)
    discriminator: str = Field(default="", max_length=500)
    professional_evidence: EvaluationProfessionalEvidence | None = None
    normalization_notes: list[str] = Field(default_factory=list, max_length=8)


class EvaluationCaseInput(BaseModel):
    case_id: str | None = Field(default=None, max_length=120)
    name: str = Field(default="", max_length=160)
    message: str = Field(min_length=1, max_length=20_000)
    messages: list[EvaluationMessage] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    expected: EvaluationExpectation = Field(default_factory=EvaluationExpectation)
    path: EvaluationPathExpectation | None = None
    resource_reads: list[EvaluationResourceReadExpectation] = Field(
        default_factory=list,
        max_length=32,
    )
    weights: dict[MetricKind, float] = Field(default_factory=dict)
    targeting: EvaluationCaseTargeting | None = None

    @model_validator(mode="after")
    def normalize_weights(self) -> "EvaluationCaseInput":
        for key, value in self.weights.items():
            if not 0 <= float(value) <= 10:
                raise ValueError(f"Metric weight must be between 0 and 10: {key}")
        if self.path is not None and self.path.terminal == "error":
            text_expectations = (
                self.expected.exact_answer is not None
                or bool(self.expected.contains)
                or self.expected.json_schema is not None
                or bool(self.expected.citation_ids)
                or bool(self.expected.chunk_ids)
                or bool(self.expected.document_names)
                or bool(self.expected.rubric)
            )
            if text_expectations:
                raise ValueError(
                    "Expected error paths cannot include text-answer metrics."
                )
        resource_keys = [(item.node_ref, item.kind) for item in self.resource_reads]
        if len(resource_keys) != len(set(resource_keys)):
            raise ValueError("Resource read assertions must be unique by node_ref and kind.")
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
    acknowledge_calibration_warnings: bool = False


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
