from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


AgentStrategyName = Literal["auto", "function_calling", "react"]


@dataclass(slots=True)
class AgentToolCall:
    call_id: str
    name: str
    raw_arguments: str


@dataclass(slots=True)
class AgentUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "AgentUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class AgentModelTurn:
    content: str = ""
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: AgentUsage = field(default_factory=AgentUsage)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentStrategyEvent:
    event_type: str
    strategy: str
    iteration: int = 0
    status: str = "info"
    message: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments_summary: str | None = None
    output_preview: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentStrategyResult:
    answer: str
    strategy: Literal["function_calling", "react"]
    events: list[AgentStrategyEvent] = field(default_factory=list)
    usage: AgentUsage = field(default_factory=AgentUsage)
    tool_calls_attempted: int = 0
    tool_calls_executed: int = 0


class AgentModelClient(Protocol):
    async def complete(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        parallel_tool_calls: bool | None = None,
    ) -> AgentModelTurn:
        ...


class AgentModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.param = param

    def is_function_calling_unsupported(self) -> bool:
        if self.status_code not in {400, 422}:
            return False
        param = (self.param or "").lower()
        message = self.message.lower()
        tooling_terms = (
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "function_call",
            "functions",
        )
        unsupported_terms = (
            "unsupported",
            "not support",
            "unrecognized",
            "unknown parameter",
            "extra inputs are not permitted",
            "not allowed",
        )
        return param in tooling_terms or (
            any(term in message for term in tooling_terms)
            and any(term in message for term in unsupported_terms)
        )


class AgentStrategyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "agent_strategy_error",
        events: list[AgentStrategyEvent] | None = None,
        usage: AgentUsage | None = None,
        tool_calls_attempted: int = 0,
        tool_calls_executed: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.events = list(events or [])
        self.usage = usage or AgentUsage()
        self.tool_calls_attempted = tool_calls_attempted
        self.tool_calls_executed = tool_calls_executed

    @property
    def retry_safe(self) -> bool:
        return self.tool_calls_attempted == 0 and self.code not in {
            "capability_not_found",
            "invalid_tool_schema",
            "tool_denied",
        }
