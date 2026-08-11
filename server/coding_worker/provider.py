from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import Field, field_validator

from .contracts import PolicyProfile, SAFE_ID, StrictModel, TaskBudget


PROVIDER_CONTRACT_VERSION = 2
PROVIDER_CHECKPOINT_FORMAT_VERSION = 2
PROVIDER_TOOL_NAMES = (
    "list_files",
    "read_file",
    "read_file_range",
    "glob_files",
    "search_text",
    "search_regex",
    "workspace_diff",
    "code_symbols",
    "code_definition",
    "code_references",
    "code_hover",
    "code_diagnostics",
    "write_file",
    "delete_file",
    "apply_changeset",
    "list_acceptance_checks",
    "run_check",
    "run_command",
    "run_shell",
    "install_dependencies",
    "start_service",
    "service_status",
    "service_input",
    "stop_service",
)
INSPECT_PROVIDER_TOOLS = PROVIDER_TOOL_NAMES[:12] + (
    "list_acceptance_checks",
)


class ProviderEventKind(StrEnum):
    SESSION_OPENED = "session_opened"
    MESSAGE = "message"
    PLAN = "plan"
    TOOL_REQUEST = "tool_request"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUIRED = "approval_required"
    CHECKPOINT = "checkpoint"
    TURN_COMPLETED = "turn_completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    USAGE = "usage"


class ProviderFailureKind(StrEnum):
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    POLICY = "policy"
    BUDGET = "budget"
    INTERRUPTED = "interrupted"


class ProviderUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_microusd: int | None = Field(default=None, ge=0)


class ProviderCheckpointCompatibility(StrictModel):
    contract_version: int = PROVIDER_CONTRACT_VERSION
    format_version: int = PROVIDER_CHECKPOINT_FORMAT_VERSION
    provider_family: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    provider_version: str = Field(min_length=1, max_length=64)
    task_id: str
    workspace_tree_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class ProviderCapabilities(StrictModel):
    contract_version: int = PROVIDER_CONTRACT_VERSION
    supports_streaming: bool = True
    supports_cancel: bool = True
    supports_checkpoint: bool = False
    supports_restore: bool = False
    supports_steering: bool = True
    supports_usage: bool = True
    tool_names: tuple[str, ...] = PROVIDER_TOOL_NAMES


class ProviderOpenRequest(StrictModel):
    _VALID_TOOLS: ClassVar[frozenset[str]] = frozenset(PROVIDER_TOOL_NAMES)

    task_id: str
    workspace_id: str
    objective: str = Field(min_length=1, max_length=1_048_576)
    model_route: str
    policy_profile: PolicyProfile
    budget: TaskBudget
    workspace_tree_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    tool_allowlist: tuple[str, ...] = PROVIDER_TOOL_NAMES

    @field_validator("tool_allowlist")
    @classmethod
    def _validate_tool_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            tool not in cls._VALID_TOOLS for tool in value
        ):
            raise ValueError("provider tool allowlist is invalid")
        return value


class ProviderSession(StrictModel):
    session_id: str
    task_id: str
    provider_capabilities: ProviderCapabilities


class ProviderEvent(StrictModel):
    kind: ProviderEventKind
    data: dict[str, Any] = Field(default_factory=dict)


class ProviderCheckpoint(StrictModel):
    checkpoint_id: str
    compatibility: ProviderCheckpointCompatibility | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def provider_tools_for_policy(policy: PolicyProfile) -> tuple[str, ...]:
    if policy is PolicyProfile.INSPECT:
        return INSPECT_PROVIDER_TOOLS
    return PROVIDER_TOOL_NAMES


@runtime_checkable
class CodingAgentProvider(Protocol):
    async def capabilities(self) -> ProviderCapabilities: ...

    async def open(self, request: ProviderOpenRequest) -> ProviderSession: ...

    def message(self, session: ProviderSession, text: str) -> AsyncIterator[ProviderEvent]: ...

    async def cancel(self, session: ProviderSession) -> bool: ...

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint: ...

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession: ...

    async def close(self, session: ProviderSession) -> None: ...


class FakeCodingAgentProvider:
    """Deterministic contract provider; it never touches a workspace or network."""

    def __init__(
        self,
        *,
        script: Sequence[ProviderEvent] | None = None,
        block: asyncio.Event | None = None,
    ) -> None:
        self._script = tuple(
            script
            or (
                ProviderEvent(kind=ProviderEventKind.PLAN, data={"summary": "inspect"}),
                ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED),
            )
        )
        self._block = block
        self._cancelled: set[str] = set()
        self._closed: set[str] = set()

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_checkpoint=True, supports_restore=True)

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        return ProviderSession(
            session_id=f"fake_{uuid.uuid4().hex}",
            task_id=request.task_id,
            provider_capabilities=await self.capabilities(),
        )

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        if not text.strip():
            raise ValueError("provider message cannot be blank")
        if self._block is not None:
            await self._block.wait()
        for event in self._script:
            await asyncio.sleep(0)
            if session.session_id in self._cancelled:
                yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
                return
            yield event

    async def cancel(self, session: ProviderSession) -> bool:
        if session.session_id in self._closed:
            return False
        self._cancelled.add(session.session_id)
        return True

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        return ProviderCheckpoint(
            checkpoint_id=f"checkpoint_{uuid.uuid4().hex}",
            compatibility=ProviderCheckpointCompatibility(
                provider_family="fake",
                provider_version="1",
                task_id=session.task_id,
            ),
            payload={"fake_session": session.session_id},
        )

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        if SAFE_ID.fullmatch(checkpoint.checkpoint_id) is None:
            raise ValueError("invalid checkpoint")
        compatibility = checkpoint.compatibility
        if compatibility is not None and (
            compatibility.provider_family != "fake"
            or compatibility.provider_version != "1"
            or compatibility.task_id != request.task_id
            or compatibility.workspace_tree_hash
            not in {None, request.workspace_tree_hash}
        ):
            raise ValueError("incompatible checkpoint")
        return await self.open(request)

    async def close(self, session: ProviderSession) -> None:
        self._closed.add(session.session_id)
        self._cancelled.discard(session.session_id)
