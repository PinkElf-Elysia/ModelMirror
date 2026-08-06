from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AGENT_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
SKILL_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
TOOL_NAMES = (
    "read_file",
    "edit_file",
    "write_file",
    "exec_command",
    "input_command",
    "run_subagent",
    "input_subagent",
    "read_image",
    "describe_image",
)
ALLOWED_PROMPT_PLACEHOLDERS = frozenset(
    {
        "AGENTS_MD",
        "SKILL_METADATA",
        "SESSION_ID",
        "CWD",
        "AGENT_ID",
        "PROJECT_DIR",
        "PROVIDER",
        "MODEL_ID",
        "PLATFORM",
        "OS_VERSION",
        "SHELL",
        "DATE",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentModelConfig(StrictModel):
    max_tokens: int = Field(ge=1, le=2_000_000)
    thinking_level: Literal["low", "medium", "high", "xhigh"]
    timeoutMs: int = Field(ge=1_000, le=3_600_000)


class CompactionConfig(StrictModel):
    max_context_length: int = Field(ge=1_024, le=4_000_000)
    max_session_turns: int = Field(ge=-1, le=100_000)
    mode: Literal["summarize"]
    prompt: str = Field(min_length=1, max_length=64_000)

    @field_validator("max_session_turns")
    @classmethod
    def reject_zero_turn_limit(cls, value: int) -> int:
        if value == 0:
            raise ValueError("max_session_turns must be -1 or a positive integer")
        return value


class ToolDefinitionConfig(StrictModel):
    name: Literal[
        "read_file",
        "edit_file",
        "write_file",
        "exec_command",
        "input_command",
        "run_subagent",
        "input_subagent",
        "read_image",
        "describe_image",
    ]
    description: str = Field(min_length=1, max_length=4_000)
    parameters: dict[str, Any]
    permission: Literal["r", "rw"]
    timeoutMs: int = Field(ge=1_000, le=900_000)
    maxOutputLength: int = Field(ge=1_024, le=256_000)
    call_description: bool = False


class ToolConfig(StrictModel):
    builtin: list[ToolDefinitionConfig] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def require_exact_builtin_tools(self) -> "ToolConfig":
        names = [tool.name for tool in self.builtin]
        if len(set(names)) != len(names):
            raise ValueError("built-in tool names must be unique")
        if tuple(names) != TOOL_NAMES:
            raise ValueError(
                "built-in tools must keep the supported nine-tool order"
            )
        return self


class AgentSystemConfig(StrictModel):
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    system_prompt: str = Field(min_length=1, max_length=256_000)
    max_turns: int = Field(ge=-1, le=10_000)
    model: AgentModelConfig
    compaction: CompactionConfig
    tools: ToolConfig
    skillset_id: str = Field(
        default="general-agent-default",
        min_length=1,
        max_length=120,
        pattern=SKILL_ID_PATTERN,
    )

    @field_validator("max_turns")
    @classmethod
    def reject_zero_max_turns(cls, value: int) -> int:
        if value == 0:
            raise ValueError("max_turns must be -1 or a positive integer")
        return value

    @field_validator("system_prompt")
    @classmethod
    def reject_unknown_placeholders(cls, value: str) -> str:
        placeholders = set(re.findall(r"\{\{([A-Z_]+)\}\}", value))
        unknown = sorted(placeholders - ALLOWED_PROMPT_PLACEHOLDERS)
        if unknown:
            raise ValueError(
                f"unknown system prompt placeholders: {', '.join(unknown)}"
            )
        return value


SkillCapabilityStatus = Literal[
    "ready", "conditional", "dependency_missing", "reference_only"
]


class AgentSkillSnapshot(StrictModel):
    skill_id: str = Field(pattern=SKILL_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    status: SkillCapabilityStatus
    reason: str = Field(default="", max_length=1_000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_url: str = Field(min_length=1, max_length=500)
    source_path: str = Field(min_length=1, max_length=500)
    source_license: Literal["Apache-2.0"]
    adapted: bool = False


class AgentPayload(StrictModel):
    agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    builtin: bool
    config: AgentSystemConfig
    agents_md: str = Field(max_length=1_048_576)
    skills: list[AgentSkillSnapshot]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_path: str


class AgentSummary(StrictModel):
    agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    name: str
    description: str
    version: int
    builtin: bool
    skill_count: int
    revision: str


class AgentListResponse(StrictModel):
    agents: list[AgentSummary]


class AgentCreateRequest(StrictModel):
    agent_id: str = Field(min_length=1, max_length=120, pattern=AGENT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)


class AgentUpdateRequest(StrictModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: AgentSystemConfig
    agents_md: str = Field(max_length=1_048_576)


class AgentResetRequest(StrictModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
