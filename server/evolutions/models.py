from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EvolutionBudget(BaseModel):
    repetitions: int = Field(default=1, ge=1, le=3)
    max_concurrency: int = Field(default=2, ge=1, le=4)
    case_timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_model_calls: int = Field(default=16, ge=1, le=64)
    max_tool_calls: int = Field(default=24, ge=0, le=100)
    max_estimated_tokens: int = Field(default=64_000, ge=1_000, le=500_000)
    max_output_chars: int = Field(default=20_000, ge=1_000, le=20_000)


class EvolutionRunRequest(BaseModel):
    target_kind: Literal["xpert", "prompt_profile"]
    target_id: str = Field(min_length=1, max_length=200)
    target_revision: int = Field(ge=1)
    prompt_fields: list[str] = Field(default_factory=list, max_length=3)
    host_xpert_id: str | None = Field(default=None, max_length=200)
    host_xpert_version: int | None = Field(default=None, ge=1)
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: int = Field(ge=1)
    optimizer_model_id: str = Field(min_length=1, max_length=240)
    model_policy: Literal["snapshot", "override"] = "snapshot"
    override_model_id: str | None = Field(default=None, max_length=240)
    judge_model_id: str | None = Field(default=None, max_length=240)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    generations: int = Field(default=2, ge=1, le=3)
    population_size: int = Field(default=4, ge=2, le=5)
    min_score_delta: float = Field(default=0.01, ge=0, le=1)
    max_metric_regression: float = Field(default=0.02, ge=0, le=1)
    budget: EvolutionBudget = Field(default_factory=EvolutionBudget)

    @model_validator(mode="after")
    def validate_target(self) -> "EvolutionRunRequest":
        if self.target_kind == "xpert":
            if not self.prompt_fields:
                raise ValueError("Xpert evolution requires at least one Prompt field.")
            if len(set(self.prompt_fields)) != len(self.prompt_fields):
                raise ValueError("Prompt fields must be unique.")
            if self.host_xpert_id or self.host_xpert_version:
                raise ValueError("Xpert evolution does not use a host Xpert.")
        else:
            if self.prompt_fields:
                raise ValueError("Prompt Profile evolution does not accept prompt_fields.")
            if not self.host_xpert_id or not self.host_xpert_version:
                raise ValueError(
                    "Prompt Profile evolution requires a published host Xpert version."
                )
        if self.model_policy == "override" and not self.override_model_id:
            raise ValueError("override_model_id is required for override model policy.")
        return self


class EvolutionPreflightRequest(EvolutionRunRequest):
    pass
