from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgencyAgentDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2_000)
    system_prompt: str = Field(min_length=1, max_length=16_000)
    emoji: str | None = Field(default=None, max_length=16)


class AgencyModelMessage(StrictModel):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=2 * 1024 * 1024)


class AgencyModelRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=256)
    messages: list[AgencyModelMessage] = Field(min_length=1, max_length=4)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1, le=16_384)


class AgencyModelResponse(StrictModel):
    content: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    usage: dict[str, int] = Field(default_factory=dict)


class AgencyWorkerResult(StrictModel):
    payload: dict[str, Any]
    model_calls: int = Field(default=0, ge=0, le=3)
