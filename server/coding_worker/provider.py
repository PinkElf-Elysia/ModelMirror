from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from .contracts import PolicyProfile, SAFE_ID, StrictModel, TaskBudget


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


class ProviderCapabilities(StrictModel):
    supports_streaming: bool = True
    supports_cancel: bool = True
    supports_checkpoint: bool = False
    supports_restore: bool = False
    supports_steering: bool = True


class ProviderOpenRequest(StrictModel):
    task_id: str
    workspace_id: str
    objective: str = Field(min_length=1, max_length=1_048_576)
    model_route: str
    policy_profile: PolicyProfile
    budget: TaskBudget


class ProviderSession(StrictModel):
    session_id: str
    task_id: str
    provider_capabilities: ProviderCapabilities


class ProviderEvent(StrictModel):
    kind: ProviderEventKind
    data: dict[str, Any] = Field(default_factory=dict)


class ProviderCheckpoint(StrictModel):
    checkpoint_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


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
            payload={"fake_session": session.session_id},
        )

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        if SAFE_ID.fullmatch(checkpoint.checkpoint_id) is None:
            raise ValueError("invalid checkpoint")
        return await self.open(request)

    async def close(self, session: ProviderSession) -> None:
        self._closed.add(session.session_id)
        self._cancelled.discard(session.session_id)
