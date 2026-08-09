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
    checksum: str = Field(min_length=64, max_length=64)


class BenchmarkPack(BaseModel):
    manifest: BenchmarkManifest
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class InstantiateBenchmarkRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)


BenchmarkTargetKind = Literal[
    "xpert_draft",
    "xpert_version",
    "proposal",
    "prompt_profile",
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
        return self


class BenchmarkGenerationRequest(BaseModel):
    target: BenchmarkTargetRequest
    generator_model_id: str = Field(min_length=1, max_length=300)
    case_count: int = Field(default=12, ge=6, le=30)
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
    seed: int = Field(default=0, ge=0, le=2_147_483_647)


class BenchmarkGenerationPreflightRequest(BaseModel):
    target: BenchmarkTargetRequest
    coverage: list[str] = Field(default_factory=list, max_length=10)
    conversation_selections: list[ConversationImportSelection] = Field(
        default_factory=list,
        max_length=20,
    )


class BenchmarkCalibrationRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_revision: int = Field(ge=1)
    target: BenchmarkTargetRequest | None = None

