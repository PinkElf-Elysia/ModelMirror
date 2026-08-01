from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


MAX_INCREMENTAL_CYCLES = 10
MAX_HISTORY_PATCH_BYTES = 10 * 1024 * 1024


class CycleState(StrEnum):
    COMMITTED = "committed"
    UNDONE = "undone"
    REVERTED = "reverted"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class CodingCycle:
    number: int
    revision: int
    state: CycleState
    patch: str
    changes: dict[str, Any]
    verification: dict[str, Any]
    apply: dict[str, Any]
    commit: dict[str, Any]
    created_at: float = field(default=0.0)
    updated_at: float = field(default=0.0)

    def __post_init__(self) -> None:
        if (
            isinstance(self.number, bool)
            or not 1 <= self.number <= MAX_INCREMENTAL_CYCLES
        ):
            raise ValueError("Cycle number is outside the allowed range")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Cycle revision is invalid")
        if not isinstance(self.state, CycleState):
            raise ValueError("Cycle state is invalid")
        try:
            patch_bytes = len(self.patch.encode("utf-8", errors="strict"))
        except UnicodeEncodeError as exc:
            raise ValueError("Cycle Patch is not UTF-8") from exc
        if not patch_bytes or patch_bytes > 1024 * 1024:
            raise ValueError("Cycle Patch is outside the allowed size")
        if not all(
            isinstance(value, dict)
            for value in (self.changes, self.verification, self.apply, self.commit)
        ):
            raise ValueError("Cycle payload is invalid")
        if self.changes.get("revision") != self.revision:
            raise ValueError("Cycle revision does not match its changes")
        if self.apply.get("revision") != self.revision:
            raise ValueError("Cycle revision does not match its application")
        if self.commit.get("revision") != self.revision:
            raise ValueError("Cycle revision does not match its commit")
        timestamps = (self.created_at, self.updated_at)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in timestamps
        ) or self.updated_at < self.created_at:
            raise ValueError("Cycle timestamps are invalid")

    @property
    def patch_bytes(self) -> int:
        return len(self.patch.encode("utf-8"))

    @property
    def is_latest_undoable(self) -> bool:
        return self.state is CycleState.COMMITTED

    def to_public(self, *, latest: bool) -> dict[str, Any]:
        commit_sha = self.commit.get("commit_sha")
        message = self.commit.get("message")
        return {
            "number": self.number,
            "revision": self.revision,
            "state": self.state.value,
            "file_count": self.changes.get("file_count", 0),
            "additions": self.changes.get("additions", 0),
            "deletions": self.changes.get("deletions", 0),
            "verification_result": self.verification.get("result", "not_run"),
            "commit_sha": commit_sha if isinstance(commit_sha, str) else None,
            "short_sha": commit_sha[:12] if isinstance(commit_sha, str) else None,
            "message": message if isinstance(message, str) else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "can_undo": latest and self.is_latest_undoable,
        }


@dataclass(frozen=True, slots=True)
class CodingCycleHistory:
    cycles: tuple[CodingCycle, ...] = ()

    def __post_init__(self) -> None:
        if len(self.cycles) > MAX_INCREMENTAL_CYCLES:
            raise ValueError("Coding cycle history is full")
        numbers = tuple(cycle.number for cycle in self.cycles)
        if numbers != tuple(range(1, len(numbers) + 1)):
            raise ValueError("Coding cycle numbers are not consecutive")
        if sum(cycle.patch_bytes for cycle in self.cycles) > MAX_HISTORY_PATCH_BYTES:
            raise ValueError("Coding cycle history exceeds its Patch limit")

    @property
    def next_number(self) -> int:
        return len(self.cycles) + 1

    @property
    def is_full(self) -> bool:
        return self.next_number > MAX_INCREMENTAL_CYCLES

    @property
    def latest(self) -> CodingCycle | None:
        return self.cycles[-1] if self.cycles else None

    def append(self, cycle: CodingCycle) -> CodingCycleHistory:
        if self.is_full or cycle.number != self.next_number:
            raise ValueError("Coding cycle cannot be appended")
        return CodingCycleHistory((*self.cycles, cycle))

    def replace_latest(self, cycle: CodingCycle) -> CodingCycleHistory:
        if not self.cycles or cycle.number != self.cycles[-1].number:
            raise ValueError("Only the latest Coding cycle can be replaced")
        return CodingCycleHistory((*self.cycles[:-1], cycle))

    def require_latest(self, number: int) -> CodingCycle:
        latest = self.latest
        if latest is None or latest.number != number:
            raise ValueError("Only the latest Coding cycle can be changed")
        return latest

    def to_public(self) -> list[dict[str, Any]]:
        latest_number = self.latest.number if self.latest is not None else None
        return [
            cycle.to_public(latest=cycle.number == latest_number)
            for cycle in reversed(self.cycles)
        ]

    @classmethod
    def from_iterable(cls, cycles: Iterable[CodingCycle]) -> CodingCycleHistory:
        return cls(tuple(cycles))
