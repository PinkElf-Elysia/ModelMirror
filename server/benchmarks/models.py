from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

try:
    from server.evaluations.models import ConversationImportSelection
except ModuleNotFoundError:
    from evaluations.models import ConversationImportSelection


BenchmarkKind = Literal["agent_response", "knowledge_retrieval"]


class BenchmarkManifest(BaseModel):
    pack_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    kind: BenchmarkKind
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    locales: list[str] = Field(min_length=1, max_length=10)
    coverage: list[str] = Field(min_length=1, max_length=30)
    difficulty: Literal["basic", "intermediate", "advanced", "mixed"]
    metric_policy: dict[str, Any]
    target_requirements: dict[str, Any]
    source: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=120)
    case_count: int = Field(ge=1, le=500)
    document_count: int = Field(default=0, ge=0, le=100)
    checksum: str = Field(min_length=64, max_length=64)


class BenchmarkPack(BaseModel):
    manifest: BenchmarkManifest
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    documents: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class InstantiateBenchmarkRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)


BenchmarkTargetKind = Literal[
    "xpert_draft",
    "xpert_version",
    "proposal",
    "prompt_profile",
    "knowledge_version",
]


class BenchmarkTargetRequest(BaseModel):
    kind: BenchmarkTargetKind
    xpert_id: str | None = Field(default=None, max_length=200)
    draft_revision: int | None = Field(default=None, ge=1)
    version: int | None = Field(default=None, ge=1)
    proposal_id: str | None = Field(default=None, max_length=200)
    proposal_revision: int | None = Field(default=None, ge=1)
    prompt_profile_id: str | None = Field(default=None, max_length=200)
    prompt_profile_revision: int | None = Field(default=None, ge=1)
    host_xpert_id: str | None = Field(default=None, max_length=200)
    host_xpert_version: int | None = Field(default=None, ge=1)
    kb_id: str | None = Field(default=None, max_length=200)
    pipeline_version_id: str | None = Field(default=None, max_length=200)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    label: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_reference(self) -> "BenchmarkTargetRequest":
        if self.kind == "xpert_draft" and (
            not self.xpert_id or self.draft_revision is None
        ):
            raise ValueError("xpert_id and draft_revision are required.")
        if self.kind == "xpert_version" and (
            not self.xpert_id or self.version is None
        ):
            raise ValueError("xpert_id and version are required.")
        if self.kind == "proposal" and (
            not self.proposal_id or self.proposal_revision is None
        ):
            raise ValueError("proposal_id and proposal_revision are required.")
        if self.kind == "prompt_profile" and (
            not self.prompt_profile_id
            or self.prompt_profile_revision is None
            or not self.host_xpert_id
            or self.host_xpert_version is None
        ):
            raise ValueError(
                "prompt_profile_id, prompt_profile_revision, host_xpert_id, and "
                "host_xpert_version are required."
            )
        if self.kind == "knowledge_version" and (
            not self.kb_id or not self.pipeline_version_id
        ):
            raise ValueError("kb_id and pipeline_version_id are required.")
        return self


class BenchmarkGenerationRequest(BaseModel):
    target: BenchmarkTargetRequest
    generator_model_id: str = Field(min_length=1, max_length=300)
    generation_purpose: Literal["general", "strategy_tuning"] = "general"
    case_count: int = Field(default=12, ge=6, le=60)
    locales: list[Literal["zh-CN", "en-US"]] = Field(
        default_factory=lambda: ["zh-CN", "en-US"],
        min_length=1,
        max_length=2,
    )
    coverage: list[str] = Field(default_factory=list, max_length=10)
    conversation_selections: list[ConversationImportSelection] = Field(
        default_factory=list,
        max_length=20,
    )
    no_result_count: int = Field(default=0, ge=0, le=20)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_no_result_count(self) -> "BenchmarkGenerationRequest":
        if self.target.kind != "knowledge_version" and self.no_result_count:
            raise ValueError("no_result_count is only supported for knowledge targets.")
        if self.generation_purpose == "strategy_tuning":
            if self.target.kind != "knowledge_version":
                raise ValueError(
                    "strategy_tuning generation is only supported for knowledge targets."
                )
            positive_count = self.case_count - self.no_result_count
            if positive_count < 30:
                raise ValueError(
                    "strategy_tuning generation requires at least 30 answerable cases."
                )
            if self.no_result_count and self.no_result_count < 12:
                raise ValueError(
                    "strategy_tuning threshold evidence requires either 0 or at least 12 "
                    "hard-negative cases."
                )
        else:
            if self.case_count > 30:
                raise ValueError("general generation cannot exceed 30 cases.")
            limit = min(5, self.case_count // 5)
            if self.no_result_count > limit:
                raise ValueError(
                    f"no_result_count cannot exceed {limit} for {self.case_count} cases."
                )
        return self


class BenchmarkGenerationPreflightRequest(BaseModel):
    target: BenchmarkTargetRequest
    coverage: list[str] = Field(default_factory=list, max_length=10)
    locales: list[Literal["zh-CN", "en-US"]] = Field(
        default_factory=lambda: ["zh-CN", "en-US"],
        min_length=1,
        max_length=2,
    )
    conversation_selections: list[ConversationImportSelection] = Field(
        default_factory=list,
        max_length=20,
    )


class BenchmarkCalibrationRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_revision: int = Field(ge=1)
    target: BenchmarkTargetRequest | None = None
