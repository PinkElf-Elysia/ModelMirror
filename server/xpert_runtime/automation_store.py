from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .workflow_outcomes import resolve_workflow_outcome

if TYPE_CHECKING:
    from .execution_store import WorkflowExecution, WorkflowExecutionStore


AutomationStatus = Literal["draft", "scheduled", "paused", "archived"]
AutomationExecutionStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "waiting_client",
    "completed",
    "failed",
    "dead_letter",
    "skipped",
    "budget_limited",
    "cancelled",
]
TriggerType = Literal["once", "interval", "cron"]


class AutomationError(Exception):
    """Base error for durable automation definitions and executions."""


class AutomationNotFoundError(AutomationError):
    pass


class AutomationConflictError(AutomationError):
    pass


class AutomationValidationError(AutomationError):
    pass


@dataclass(slots=True)
class AutomationTrigger:
    type: TriggerType
    once_at: float | None = None
    interval_seconds: int | None = None
    cron: str | None = None
    timezone: str = "UTC"


@dataclass(slots=True)
class AutomationBudget:
    max_runs_total: int | None = None
    max_runs_per_day: int = 100
    max_runtime_seconds: int = 1800


@dataclass(slots=True)
class AutomationDefinition:
    automation_id: str
    name: str
    prompt: str
    target_xpert_id: str
    target_xpert_slug: str
    target_xpert_version: int
    trigger: AutomationTrigger
    status: AutomationStatus = "draft"
    overlap_policy: str = "skip"
    misfire_policy: str = "latest"
    max_attempts: int = 3
    budget: AutomationBudget = field(default_factory=AutomationBudget)
    metadata: dict[str, Any] = field(default_factory=dict)
    revision: int = 1
    next_run_at: float | None = None
    last_run_at: float | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class AutomationExecution:
    execution_id: str
    automation_id: str
    occurrence_key: str
    scheduled_at: float
    available_at: float
    status: AutomationExecutionStatus = "pending"
    attempt: int = 0
    run_id: str | None = None
    workflow_task_id: str | None = None
    wait_kind: str | None = None
    wait_id: str | None = None
    result: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    runtime_budget_seconds: int | None = None
    runtime_deadline_at: float | None = None
    runtime_expired_at: float | None = None


@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool


@dataclass(frozen=True, slots=True)
class CronSchedule:
    minute: _CronField
    hour: _CronField
    day: _CronField
    month: _CronField
    weekday: _CronField

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        parts = expression.strip().split()
        if len(parts) != 5:
            raise AutomationValidationError("Cron expression must contain five fields.")
        return cls(
            _parse_cron_field(parts[0], 0, 59),
            _parse_cron_field(parts[1], 0, 23),
            _parse_cron_field(parts[2], 1, 31),
            _parse_cron_field(parts[3], 1, 12),
            _parse_cron_field(parts[4], 0, 7, normalize_weekday=True),
        )

    def matches(self, value: datetime) -> bool:
        weekday = (value.weekday() + 1) % 7
        day_match = value.day in self.day.values
        weekday_match = weekday in self.weekday.values
        if self.day.wildcard and self.weekday.wildcard:
            calendar_match = True
        elif self.day.wildcard:
            calendar_match = weekday_match
        elif self.weekday.wildcard:
            calendar_match = day_match
        else:
            calendar_match = day_match or weekday_match
        return (
            value.minute in self.minute.values
            and value.hour in self.hour.values
            and value.month in self.month.values
            and calendar_match
        )

    def next_after(self, timestamp: float, timezone_name: str) -> float:
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise AutomationValidationError(f"Unknown timezone: {timezone_name}") from exc
        candidate = datetime.fromtimestamp(timestamp, tz=zone).replace(second=0, microsecond=0)
        candidate += timedelta(minutes=1)
        deadline = candidate + timedelta(days=366 * 2)
        while candidate <= deadline:
            if self.matches(candidate):
                return candidate.timestamp()
            candidate += timedelta(minutes=1)
        raise AutomationValidationError("Cron expression has no occurrence in the next two years.")


def _parse_cron_field(
    raw: str,
    minimum: int,
    maximum: int,
    *,
    normalize_weekday: bool = False,
) -> _CronField:
    wildcard = raw == "*"
    values: set[int] = set()
    for segment in raw.split(","):
        segment = segment.strip()
        if not segment:
            raise AutomationValidationError("Cron field contains an empty segment.")
        base, step_raw = (segment.split("/", 1) + [None])[:2]
        try:
            step = int(step_raw) if step_raw is not None else 1
        except ValueError as exc:
            raise AutomationValidationError("Cron step must be an integer.") from exc
        if step < 1 or step > maximum - minimum + 1:
            raise AutomationValidationError("Cron step is outside the supported range.")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            try:
                start, end = int(start_raw), int(end_raw)
            except ValueError as exc:
                raise AutomationValidationError("Cron range must use integers.") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise AutomationValidationError("Cron field must use integers.") from exc
        if start < minimum or end > maximum or start > end:
            raise AutomationValidationError("Cron value is outside the supported range.")
        values.update(range(start, end + 1, step))
    if normalize_weekday and 7 in values:
        values.remove(7)
        values.add(0)
    if not values:
        raise AutomationValidationError("Cron field cannot be empty.")
    return _CronField(frozenset(values), wildcard)


class AutomationStore:
    """Atomic file-backed scheduler state for pinned published Xperts."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "automations.json"
        self._lock = threading.RLock()
        self._definitions: dict[str, AutomationDefinition] = {}
        self._executions: dict[str, AutomationExecution] = {}
        self._load()

    def create_definition(
        self,
        *,
        name: str,
        prompt: str,
        target_xpert_id: str,
        target_xpert_slug: str,
        target_xpert_version: int,
        trigger: dict[str, Any],
        status: AutomationStatus = "draft",
        overlap_policy: str = "skip",
        misfire_policy: str = "latest",
        max_attempts: int = 3,
        budget: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutomationDefinition:
        clean_name = str(name or "").strip()
        clean_prompt = str(prompt or "").strip()
        if not clean_name or len(clean_name) > 200:
            raise AutomationValidationError("Automation name is required and limited to 200 characters.")
        if not clean_prompt or len(clean_prompt) > 20_000:
            raise AutomationValidationError("Automation prompt is required and limited to 20,000 characters.")
        parsed_trigger = self.validate_trigger(trigger)
        parsed_budget = self.validate_budget(budget or {})
        if status not in {"draft", "scheduled", "paused", "archived"}:
            raise AutomationValidationError("Invalid automation status.")
        if overlap_policy not in {"skip", "allow"}:
            raise AutomationValidationError("overlap_policy must be skip or allow.")
        if misfire_policy not in {"skip", "latest", "catch_up"}:
            raise AutomationValidationError("misfire_policy must be skip, latest, or catch_up.")
        if not 1 <= int(max_attempts) <= 10:
            raise AutomationValidationError("max_attempts must be between 1 and 10.")
        now = time.time()
        item = AutomationDefinition(
            automation_id=f"auto_{uuid.uuid4().hex}",
            name=clean_name,
            prompt=clean_prompt,
            target_xpert_id=str(target_xpert_id).strip(),
            target_xpert_slug=str(target_xpert_slug).strip(),
            target_xpert_version=max(1, int(target_xpert_version)),
            trigger=parsed_trigger,
            status=status,
            overlap_policy=overlap_policy,
            misfire_policy=misfire_policy,
            max_attempts=int(max_attempts),
            budget=parsed_budget,
            metadata=dict(metadata or {}),
            next_run_at=self.next_occurrence(parsed_trigger, now - 1) if status == "scheduled" else None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._definitions[item.automation_id] = item
            self._persist_unlocked()
        return item

    def update_definition(
        self,
        automation_id: str,
        *,
        revision: int,
        patch: dict[str, Any],
    ) -> AutomationDefinition:
        with self._lock:
            item = self._require_definition_unlocked(automation_id)
            if item.revision != int(revision):
                raise AutomationConflictError("Automation revision changed.")
            if "name" in patch:
                name = str(patch.get("name") or "").strip()
                if not name or len(name) > 200:
                    raise AutomationValidationError("Invalid automation name.")
                item.name = name
            if "prompt" in patch:
                prompt = str(patch.get("prompt") or "").strip()
                if not prompt or len(prompt) > 20_000:
                    raise AutomationValidationError("Invalid automation prompt.")
                item.prompt = prompt
            if "trigger" in patch:
                item.trigger = self.validate_trigger(dict(patch.get("trigger") or {}))
            if "overlap_policy" in patch:
                value = str(patch.get("overlap_policy") or "")
                if value not in {"skip", "allow"}:
                    raise AutomationValidationError("Invalid overlap policy.")
                item.overlap_policy = value
            if "misfire_policy" in patch:
                value = str(patch.get("misfire_policy") or "")
                if value not in {"skip", "latest", "catch_up"}:
                    raise AutomationValidationError("Invalid misfire policy.")
                item.misfire_policy = value
            if "max_attempts" in patch:
                value = int(patch.get("max_attempts") or 0)
                if not 1 <= value <= 10:
                    raise AutomationValidationError("Invalid max_attempts.")
                item.max_attempts = value
            if "budget" in patch:
                item.budget = self.validate_budget(dict(patch.get("budget") or {}))
            if "status" in patch:
                value = str(patch.get("status") or "")
                if value not in {"draft", "scheduled", "paused", "archived"}:
                    raise AutomationValidationError("Invalid automation status.")
                item.status = value  # type: ignore[assignment]
            if item.status == "scheduled":
                item.next_run_at = self.next_occurrence(item.trigger, time.time() - 1)
            else:
                item.next_run_at = None
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def get_definition(self, automation_id: str) -> AutomationDefinition | None:
        with self._lock:
            return self._definitions.get(automation_id)

    def require_definition(self, automation_id: str) -> AutomationDefinition:
        with self._lock:
            return self._require_definition_unlocked(automation_id)

    def list_definitions(
        self,
        *,
        status: str | None = None,
        search: str = "",
        limit: int = 100,
    ) -> list[AutomationDefinition]:
        with self._lock:
            items = list(self._definitions.values())
        if status:
            items = [item for item in items if item.status == status]
        term = search.strip().lower()
        if term:
            items = [
                item
                for item in items
                if term in f"{item.name} {item.prompt} {item.target_xpert_slug}".lower()
            ]
        items.sort(key=lambda item: (item.updated_at, item.automation_id), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def set_status(self, automation_id: str, status: AutomationStatus) -> AutomationDefinition:
        return self.update_definition(
            automation_id,
            revision=self.require_definition(automation_id).revision,
            patch={"status": status},
        )

    def run_now(self, automation_id: str) -> AutomationExecution:
        with self._lock:
            item = self._require_definition_unlocked(automation_id)
            if item.status == "archived":
                raise AutomationConflictError("Archived automation cannot run.")
            scheduled_at = time.time()
            execution = self._create_execution_unlocked(
                item,
                scheduled_at=scheduled_at,
                occurrence_key=f"manual:{uuid.uuid4().hex}",
                metadata={"manual": True},
            )
            self._persist_unlocked()
            return deepcopy(execution)

    def materialize_due(self, *, now: float | None = None) -> list[AutomationExecution]:
        current = time.time() if now is None else float(now)
        created: list[AutomationExecution] = []
        with self._lock:
            for item in sorted(self._definitions.values(), key=lambda value: value.automation_id):
                if item.status != "scheduled" or item.next_run_at is None or item.next_run_at > current:
                    continue
                occurrences: list[float] = []
                cursor = item.next_run_at
                while cursor is not None and cursor <= current and len(occurrences) < 100:
                    occurrences.append(cursor)
                    cursor = self.next_occurrence(item.trigger, cursor)
                if item.misfire_policy == "latest" and occurrences:
                    occurrences = [occurrences[-1]]
                elif item.misfire_policy == "skip" and occurrences:
                    occurrences = [occurrences[-1]] if current - occurrences[-1] <= 60 else []
                elif item.misfire_policy == "catch_up":
                    occurrences = occurrences[-10:]
                item.next_run_at = cursor
                item.updated_at = current
                for scheduled_at in occurrences:
                    key = self.occurrence_key(item.automation_id, scheduled_at)
                    if any(value.occurrence_key == key for value in self._executions.values()):
                        continue
                    budget_error = self._budget_error_unlocked(item, current)
                    if budget_error:
                        execution = self._create_execution_unlocked(
                            item,
                            scheduled_at=scheduled_at,
                            occurrence_key=key,
                            status="budget_limited",
                            error=budget_error,
                        )
                    elif item.overlap_policy == "skip" and self._has_active_unlocked(item.automation_id):
                        execution = self._create_execution_unlocked(
                            item,
                            scheduled_at=scheduled_at,
                            occurrence_key=key,
                            status="skipped",
                            error="Previous occurrence is still active.",
                        )
                    else:
                        execution = self._create_execution_unlocked(
                            item,
                            scheduled_at=scheduled_at,
                            occurrence_key=key,
                        )
                    created.append(execution)
                if item.trigger.type == "once" and item.next_run_at is None:
                    item.status = "paused"
                    item.revision += 1
            if created:
                self._persist_unlocked()
            else:
                self._persist_unlocked()
            return deepcopy(created)

    def list_executions(
        self,
        *,
        automation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AutomationExecution]:
        with self._lock:
            items = deepcopy(list(self._executions.values()))
        if automation_id:
            items = [item for item in items if item.automation_id == automation_id]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.created_at, item.execution_id), reverse=True)
        return items[: max(1, min(int(limit), 1000))]

    def get_execution(self, execution_id: str) -> AutomationExecution | None:
        with self._lock:
            return deepcopy(self._executions.get(execution_id))

    def claimable(self, *, now: float | None = None, limit: int = 20) -> list[AutomationExecution]:
        current = time.time() if now is None else float(now)
        with self._lock:
            items = [
                item
                for item in self._executions.values()
                if item.status == "pending" and item.available_at <= current
            ]
            items += [
                item
                for item in self._executions.values()
                if item.status == "running" and item.lease_expires_at <= current
            ]
            items = deepcopy(items)
        items.sort(key=lambda item: (item.available_at, item.scheduled_at, item.execution_id))
        return items[: max(1, min(limit, 100))]

    def claim_execution(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 60,
        now: float | None = None,
    ) -> AutomationExecution:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status == "running" and item.lease_expires_at > current:
                raise AutomationConflictError("Automation execution is already leased.")
            if item.status not in {"pending", "running"}:
                raise AutomationConflictError(f"Execution cannot be claimed from {item.status}.")
            if item.available_at > current:
                raise AutomationConflictError(
                    "AUTOMATION_NOT_DUE: 执行尚未到可领取时间；"
                    f"status={item.status}, available_at={item.available_at!r}, observed_at={current!r}"
                )
            if self._dispatch_may_have_started(item):
                raise AutomationConflictError("目标派发结果不确定，不能重新领取执行。")
            self._assert_runtime_remaining(item, current)
            original = item
            item = deepcopy(item)
            item.status = "running"
            item.attempt += 1
            item.metadata["target_dispatch"] = {"version": 1, "attempt": item.attempt, "phase": "prepared"}
            item.lease_owner = str(worker_id)
            item.lease_token = uuid.uuid4().hex
            item.lease_expires_at = current + max(5, float(lease_seconds))
            if item.started_at is None:
                item.started_at = current
                item.runtime_budget_seconds = self._require_definition_unlocked(item.automation_id).budget.max_runtime_seconds
                item.runtime_deadline_at = current + item.runtime_budget_seconds
            item.updated_at = current
            self._executions[execution_id] = item
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                raise
            return deepcopy(item)

    @staticmethod
    def _runtime_remaining(item: AutomationExecution, now: float) -> float | None:
        if item.runtime_budget_seconds is None and item.runtime_deadline_at is None and item.runtime_expired_at is None:
            return None  # Legacy occurrences have no provable frozen runtime authorization.
        if (
            type(item.runtime_budget_seconds) is not int or not 30 <= item.runtime_budget_seconds <= 86_400
            or type(item.started_at) not in {int, float} or not math.isfinite(item.started_at)
            or type(item.runtime_deadline_at) not in {int, float} or not math.isfinite(item.runtime_deadline_at)
            or item.runtime_deadline_at != item.started_at + item.runtime_budget_seconds
            or item.runtime_expired_at is not None and (
                type(item.runtime_expired_at) not in {int, float} or not math.isfinite(item.runtime_expired_at)
                or item.runtime_expired_at < item.runtime_deadline_at
            )
        ):
            raise AutomationValidationError("自动化固定期限状态无效，拒绝继续执行。")
        return max(0.0, item.runtime_deadline_at - now)

    @classmethod
    def _assert_runtime_remaining(cls, item: AutomationExecution, now: float | None = None) -> None:
        if cls._runtime_remaining(item, time.time() if now is None else now) == 0:
            raise AutomationConflictError("AUTOMATION_RUNTIME_BUDGET_EXCEEDED: 本次执行的固定总期限已到达。")

    def remaining_runtime(self, execution_id: str) -> float | None:
        with self._lock:
            return self._runtime_remaining(self._require_execution_unlocked(execution_id), time.time())

    @staticmethod
    def _runtime_expirable(item: AutomationExecution) -> bool:
        return item.status in {"pending", "running", "waiting_approval", "waiting_client"} or (
            item.status == "dead_letter" and item.error == "AUTOMATION_DISPATCH_UNCERTAIN"
        )

    def expire_runtime_budget(self, execution_id: str) -> AutomationExecution:
        """Only the persisted deadline can terminate an occurrence without a worker lease."""
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            current = time.time()
            if item.runtime_expired_at is not None or not self._runtime_expirable(item) or self._runtime_remaining(item, current) != 0:
                return deepcopy(item)
            original = item
            item = deepcopy(item)
            uncertain = item.error == "AUTOMATION_DISPATCH_UNCERTAIN" or (
                item.status in {"running", "pending"} and self._dispatch_may_have_started(item)
            )
            item.error = "AUTOMATION_DISPATCH_UNCERTAIN" if uncertain else "AUTOMATION_RUNTIME_BUDGET_EXCEEDED"
            if uncertain:
                receipt = item.metadata.get("target_dispatch")
                item.metadata["target_dispatch"] = {
                    "version": 1, "attempt": item.attempt, "phase": "uncertain",
                    "run_id": str(receipt.get("run_id") or "") if isinstance(receipt, dict) else "",
                }
            item.status, item.result = "dead_letter", None
            item.runtime_expired_at = item.updated_at = current
            if item.completed_at is None:
                item.completed_at = current
            self._clear_lease(item)
            definition = self._require_definition_unlocked(item.automation_id)
            updated = deepcopy(definition)
            updated.last_run_at = updated.updated_at = current
            self._executions[execution_id], self._definitions[item.automation_id] = item, updated
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id], self._definitions[item.automation_id] = original, definition
                raise
            return deepcopy(item)

    def runtime_expiry_candidates(self) -> list[AutomationExecution]:
        with self._lock:
            current = time.time()
            return [deepcopy(item) for item in self._executions.values()
                    if item.runtime_expired_at is not None
                    or self._runtime_expirable(item) and self._runtime_remaining(item, current) == 0]

    @staticmethod
    def _dispatch_may_have_started(item: AutomationExecution) -> bool:
        receipt = item.metadata.get("target_dispatch")
        if receipt is None:
            return item.attempt > 0
        return not (
            isinstance(receipt, dict) and type(receipt.get("version")) is int and receipt.get("version") == 1
            and type(receipt.get("attempt")) is int
            and receipt.get("attempt") == item.attempt and receipt.get("phase") == "prepared"
        )

    def mark_target_dispatch(self, execution_id: str, *, lease_token: str, run_id: str) -> AutomationExecution:
        """Persist the conservative dispatch boundary before invoking any target."""
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            self.assert_execution_owner(execution_id, lease_token)
            if self._dispatch_may_have_started(item) or not run_id:
                raise AutomationConflictError("目标可能已派发或运行身份缺失，拒绝再次调用。")
            original = item
            item = deepcopy(item)
            item.metadata["target_dispatch"] = {
                "version": 1, "attempt": item.attempt, "phase": "started", "run_id": run_id,
            }
            item.updated_at = time.time()
            self._executions[execution_id] = item
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                raise
            return deepcopy(item)

    def reconcile_uncertain_dispatches(self, *, now: float | None = None) -> list[AutomationExecution]:
        current = time.time() if now is None else float(now)
        with self._lock:
            for item in list(self._executions.values()):
                due = (item.status == "running" and item.lease_expires_at <= current
                       or item.status == "pending" and item.available_at <= current)
                if not due or not self._dispatch_may_have_started(item):
                    continue
                original = item
                item = deepcopy(item)
                receipt = item.metadata.get("target_dispatch")
                item.metadata["target_dispatch"] = {
                    "version": 1, "attempt": item.attempt, "phase": "uncertain",
                    "run_id": str(receipt.get("run_id") or "") if isinstance(receipt, dict) else "",
                }
                item.status, item.error = "dead_letter", "AUTOMATION_DISPATCH_UNCERTAIN"
                item.updated_at = item.completed_at = current
                self._clear_lease(item)
                definition = self._require_definition_unlocked(item.automation_id)
                updated = deepcopy(definition)
                updated.last_run_at = updated.updated_at = current
                self._executions[item.execution_id] = item
                self._definitions[item.automation_id] = updated
                try:
                    self._persist_unlocked()
                except BaseException:
                    self._executions[item.execution_id] = original
                    self._definitions[item.automation_id] = definition
                    raise
            return [deepcopy(item) for item in self._executions.values()
                    if item.status == "dead_letter" and item.error == "AUTOMATION_DISPATCH_UNCERTAIN"]

    @staticmethod
    def _settle_target_dispatch(item: AutomationExecution) -> None:
        receipt = item.metadata.get("target_dispatch")
        if isinstance(receipt, dict) and receipt.get("attempt") == item.attempt:
            item.metadata["target_dispatch"] = {**receipt, "phase": "settled"}

    def assert_execution_owner(self, execution_id: str, lease_token: str) -> None:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status != "running":
                raise AutomationConflictError("当前执行不允许派发。")
            self._assert_transition_unlocked(item, lease_token=lease_token)
            self._assert_runtime_remaining(item)

    def renew_execution(
        self, execution_id: str, *, lease_token: str, lease_seconds: float,
    ) -> AutomationExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status != "running":
                raise AutomationConflictError("当前执行不允许续租。")
            self._assert_transition_unlocked(item, lease_token=lease_token)
            self._assert_runtime_remaining(item)
            item.lease_expires_at = time.time() + max(5, float(lease_seconds))
            item.updated_at = time.time()
            self._persist_unlocked()
            return deepcopy(item)

    @staticmethod
    def _assert_transition_unlocked(
        item: AutomationExecution,
        *,
        lease_token: str | None,
        run_id: str | None = None,
        workflow_task_id: str | None = None,
    ) -> None:
        if item.status == "running":
            if (
                not lease_token or item.lease_token != lease_token
                or item.lease_expires_at <= time.time()
            ):
                raise AutomationConflictError("执行所有权已失效，拒绝迟到提交。")
            return
        if lease_token or item.status not in {"waiting_approval", "waiting_client"}:
            raise AutomationConflictError("当前执行状态不允许此回调提交。")
        if (
            (run_id is not None and item.run_id != run_id)
            or (workflow_task_id is not None and item.workflow_task_id != workflow_task_id)
        ):
            raise AutomationConflictError("恢复来源与登记的执行身份不一致。")

    def workflow_projection_executions(self) -> list[AutomationExecution]:
        with self._lock:
            return [deepcopy(item) for item in self._executions.values()
                    if item.status in {"waiting_approval", "waiting_client"}
                    or item.status in {"completed", "dead_letter"}
                    and isinstance(item.metadata.get("workflow_projection"), dict)]

    def expire_workflow_wait(
        self, executions: WorkflowExecutionStore, snapshot: WorkflowExecution,
    ) -> AutomationExecution:
        """Project a verified expired request without reopening or retrying it."""
        snapshot = deepcopy(snapshot)
        with self._lock, executions.projection_local_operation(snapshot.task_id, expected_snapshot=snapshot):
            if (snapshot.status != "waiting" or snapshot.wait_kind not in {"approval", "client_tool"}
                    or not snapshot.wait_id or not snapshot.continuation):
                raise AutomationConflictError("超时来源不是已登记的审批或客户端等待。")
            metadata = snapshot.runtime_metadata
            item = self._require_execution_unlocked(str(metadata.get("automation_execution_id") or ""))
            if (snapshot.run_type != "xpert" or item.automation_id != metadata.get("automation_id")
                    or item.occurrence_key != metadata.get("automation_occurrence_key")
                    or item.workflow_task_id != snapshot.task_id or item.run_id != snapshot.run_id
                    or item.wait_kind != snapshot.wait_kind or item.wait_id != snapshot.wait_id):
                raise AutomationConflictError("超时来源与自动化当前等待身份不一致。")
            receipt = {"task_id": snapshot.task_id, "run_id": snapshot.run_id, "revision": snapshot.revision,
                       "status": "expired", "wait_kind": snapshot.wait_kind, "wait_id": snapshot.wait_id}
            if item.status == "dead_letter" and item.metadata.get("workflow_projection") == receipt:
                return deepcopy(item)
            expected_status = "waiting_approval" if snapshot.wait_kind == "approval" else "waiting_client"
            if item.status != expected_status:
                raise AutomationConflictError("自动化已离开原等待状态，拒绝迟到超时。")
            original = item
            item = deepcopy(item)
            item.status, item.error, item.result = "dead_letter", "AUTOMATION_WAIT_EXPIRED", None
            item.metadata["workflow_projection"] = receipt
            item.updated_at = item.completed_at = time.time()
            self._clear_lease(item)
            definition = self._require_definition_unlocked(item.automation_id)
            updated_definition = deepcopy(definition)
            updated_definition.last_run_at = updated_definition.updated_at = item.updated_at
            self._executions[item.execution_id] = item
            self._definitions[item.automation_id] = updated_definition
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[item.execution_id] = original
                self._definitions[item.automation_id] = definition
                raise
            return deepcopy(item)

    def project_workflow_outcome(
        self, executions: WorkflowExecutionStore, snapshot: WorkflowExecution,
        event: dict[str, Any], *, lease_token: str | None = None,
    ) -> AutomationExecution:
        """Commit only a current durable child outcome under both Store locks."""
        snapshot, event = deepcopy(snapshot), deepcopy(event)
        with self._lock, executions.projection_local_operation(snapshot.task_id, expected_snapshot=snapshot):
            outcome = resolve_workflow_outcome(event, snapshot)
            metadata = snapshot.runtime_metadata
            item = self._require_execution_unlocked(str(metadata.get("automation_execution_id") or ""))
            if (
                item.automation_id != metadata.get("automation_id")
                or item.occurrence_key != metadata.get("automation_occurrence_key")
                or snapshot.run_type != "xpert"
            ):
                raise AutomationConflictError("自动化结果的父子身份不一致。")
            receipt = {"task_id": snapshot.task_id, "run_id": snapshot.run_id, "revision": snapshot.revision,
                       "status": outcome.status, "wait_kind": outcome.wait_kind, "wait_id": outcome.wait_id}
            target_status = (
                {"approval": "waiting_approval", "client_tool": "waiting_client"}.get(outcome.wait_kind, "dead_letter")
                if outcome.status == "waiting" else outcome.status
            )
            if item.metadata.get("workflow_projection") == receipt and item.status == target_status:
                return deepcopy(item)
            self._assert_runtime_remaining(item)
            if lease_token:
                self._assert_transition_unlocked(item, lease_token=lease_token)
            elif (
                item.status not in {"waiting_approval", "waiting_client"}
                or item.workflow_task_id != snapshot.task_id
                or item.run_id not in [snapshot.run_id, *snapshot.previous_run_ids]
            ):
                raise AutomationConflictError("自动化恢复结果与已绑定子执行不一致或父状态已改变。")
            original = item
            item = deepcopy(item)
            item.metadata["workflow_projection"] = receipt
            if outcome.status in {"waiting", "completed"}:
                self._settle_target_dispatch(item)
            item.run_id, item.workflow_task_id = snapshot.run_id, snapshot.task_id
            item.wait_kind, item.wait_id = outcome.wait_kind, outcome.wait_id
            item.error, item.result = None, None
            if outcome.status == "waiting":
                item.status = target_status
                if item.status == "dead_letter":
                    item.error = "AUTOMATION_WAIT_UNSUPPORTED"
            else:
                item.status = outcome.status
                if item.status == "completed":
                    item.result = str(snapshot.result or "")[:200_000]
                else:
                    item.error = "WORKFLOW_CANCELLED"
            item.updated_at = time.time()
            item.completed_at = item.updated_at if item.status in {"completed", "cancelled", "dead_letter"} else None
            self._clear_lease(item)
            definition = self._require_definition_unlocked(item.automation_id)
            updated_definition = deepcopy(definition)
            if item.completed_at is not None:
                updated_definition.last_run_at = item.completed_at
                updated_definition.updated_at = item.updated_at
            self._executions[item.execution_id] = item
            self._definitions[item.automation_id] = updated_definition
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[item.execution_id] = original
                self._definitions[item.automation_id] = definition
                raise
            return deepcopy(item)

    def mark_waiting(
        self,
        execution_id: str,
        *,
        status: Literal["waiting_approval", "waiting_client"],
        run_id: str,
        workflow_task_id: str,
        wait_id: str | None,
        lease_token: str | None = None,
    ) -> AutomationExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            self._assert_runtime_remaining(item)
            self._assert_transition_unlocked(
                item, lease_token=lease_token, run_id=run_id,
                workflow_task_id=workflow_task_id,
            )
            if status not in {"waiting_approval", "waiting_client"} or not wait_id:
                raise AutomationValidationError("等待状态和登记标识不能为空或未知。")
            original = item
            item = deepcopy(item)
            self._settle_target_dispatch(item)
            item.status = status
            item.run_id = run_id or item.run_id
            item.workflow_task_id = workflow_task_id or item.workflow_task_id
            item.wait_kind = "approval" if status == "waiting_approval" else "client_tool"
            item.wait_id = wait_id
            self._clear_lease(item)
            item.updated_at = time.time()
            self._executions[execution_id] = item
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                raise
            return deepcopy(item)

    def complete_execution(
        self,
        execution_id: str,
        *,
        result: str,
        run_id: str | None = None,
        workflow_task_id: str | None = None,
        lease_token: str | None = None,
    ) -> AutomationExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            self._assert_runtime_remaining(item)
            if item.status in {"waiting_approval", "waiting_client"} and (
                not run_id or not workflow_task_id
            ):
                raise AutomationConflictError("恢复完成必须提供已登记的执行身份。")
            self._assert_transition_unlocked(
                item, lease_token=lease_token, run_id=run_id,
                workflow_task_id=workflow_task_id,
            )
            original = item
            item = deepcopy(item)
            self._settle_target_dispatch(item)
            item.status = "completed"
            item.result = str(result or "")[:200_000]
            item.error = None
            item.run_id = run_id or item.run_id
            item.workflow_task_id = workflow_task_id or item.workflow_task_id
            item.wait_kind = None
            item.wait_id = None
            item.completed_at = time.time()
            item.updated_at = item.completed_at
            self._clear_lease(item)
            definition = self._require_definition_unlocked(item.automation_id)
            original_definition = definition
            definition = deepcopy(definition)
            definition.last_run_at = item.completed_at
            definition.updated_at = item.completed_at
            self._executions[execution_id] = item
            self._definitions[item.automation_id] = definition
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                self._definitions[item.automation_id] = original_definition
                raise
            return deepcopy(item)

    def fail_execution(
        self,
        execution_id: str,
        *,
        error: str,
        permanent: bool = False,
        lease_token: str | None = None,
    ) -> AutomationExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status != "running":
                raise AutomationConflictError("等待失败必须核验当前子执行和等待身份。")
            self._assert_transition_unlocked(item, lease_token=lease_token)
            definition = self._require_definition_unlocked(item.automation_id)
            original, original_definition = item, definition
            item, definition = deepcopy(item), deepcopy(definition)
            if self._dispatch_may_have_started(item):
                permanent = True
                error = "AUTOMATION_DISPATCH_UNCERTAIN"
                receipt = item.metadata.get("target_dispatch")
                item.metadata["target_dispatch"] = {
                    "version": 1, "attempt": item.attempt, "phase": "uncertain",
                    "run_id": str(receipt.get("run_id") or "") if isinstance(receipt, dict) else "",
                }
            item.error = str(error or "Automation execution failed.")[:4_000]
            self._clear_lease(item)
            now = time.time()
            if not permanent and item.attempt < definition.max_attempts:
                item.status = "pending"
                item.available_at = now + min(300, 2 ** max(1, item.attempt))
            else:
                item.status = "dead_letter"
                item.completed_at = now
            item.updated_at = now
            definition.last_run_at = now
            definition.updated_at = now
            self._executions[execution_id] = item
            self._definitions[item.automation_id] = definition
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                self._definitions[item.automation_id] = original_definition
                raise
            return deepcopy(item)

    def cancel_execution(self, execution_id: str) -> AutomationExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status in {"completed", "dead_letter", "cancelled", "skipped", "budget_limited"}:
                return deepcopy(item)
            original = item
            item = deepcopy(item)
            item.status = "cancelled"
            item.error = "操作员已取消本次执行。"
            item.completed_at = time.time()
            item.updated_at = item.completed_at
            self._clear_lease(item)
            self._executions[execution_id] = item
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                raise
            return deepcopy(item)

    def cancelled_executions(self) -> list[AutomationExecution]:
        """Internal reconciliation must not use the paginated management list."""
        with self._lock:
            return [deepcopy(item) for item in self._executions.values() if item.status == "cancelled"]

    def retry_execution(self, execution_id: str, *, reset_attempts: bool = False) -> AutomationExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status not in {"failed", "dead_letter", "budget_limited", "cancelled"}:
                raise AutomationConflictError("Only terminal failed executions can be retried.")
            self._assert_runtime_remaining(item)
            receipt = item.metadata.get("target_dispatch")
            if self._dispatch_may_have_started(item) and not (
                isinstance(receipt, dict) and type(receipt.get("version")) is int and receipt.get("version") == 1
                and type(receipt.get("attempt")) is int
                and receipt.get("attempt") == item.attempt and receipt.get("phase") == "settled"
            ):
                raise AutomationConflictError("目标结果不确定，普通重试不能清除派发证据；请先核对原执行。")
            original = item
            item = deepcopy(item)
            if reset_attempts:
                item.attempt = 0
            item.status = "pending"
            item.available_at = time.time()
            item.error = None
            item.completed_at = None
            item.wait_kind = None
            item.wait_id = None
            item.metadata["target_dispatch"] = {"version": 1, "attempt": item.attempt, "phase": "prepared"}
            item.updated_at = time.time()
            self._clear_lease(item)
            self._executions[execution_id] = item
            try:
                self._persist_unlocked()
            except BaseException:
                self._executions[execution_id] = original
                raise
            return deepcopy(item)

    @staticmethod
    def validate_trigger(raw: dict[str, Any]) -> AutomationTrigger:
        trigger_type = str(raw.get("type") or "").strip()
        timezone_name = str(raw.get("timezone") or "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise AutomationValidationError(f"Unknown timezone: {timezone_name}") from exc
        if trigger_type == "once":
            once_at = float(raw.get("once_at") or 0)
            if once_at <= 0:
                raise AutomationValidationError("once_at must be a Unix timestamp.")
            return AutomationTrigger(type="once", once_at=once_at, timezone=timezone_name)
        if trigger_type == "interval":
            interval = int(raw.get("interval_seconds") or 0)
            if not 30 <= interval <= 31_536_000:
                raise AutomationValidationError("interval_seconds must be between 30 and 31536000.")
            return AutomationTrigger(type="interval", interval_seconds=interval, timezone=timezone_name)
        if trigger_type == "cron":
            expression = str(raw.get("cron") or "").strip()
            CronSchedule.parse(expression)
            return AutomationTrigger(type="cron", cron=expression, timezone=timezone_name)
        raise AutomationValidationError("trigger.type must be once, interval, or cron.")

    @staticmethod
    def validate_budget(raw: dict[str, Any]) -> AutomationBudget:
        max_total_raw = raw.get("max_runs_total")
        max_total = int(max_total_raw) if max_total_raw not in {None, ""} else None
        per_day = int(raw.get("max_runs_per_day") or 100)
        runtime = int(raw.get("max_runtime_seconds") or 1800)
        if max_total is not None and not 1 <= max_total <= 1_000_000:
            raise AutomationValidationError("max_runs_total is outside the supported range.")
        if not 1 <= per_day <= 10_000:
            raise AutomationValidationError("max_runs_per_day is outside the supported range.")
        if not 30 <= runtime <= 86_400:
            raise AutomationValidationError("max_runtime_seconds must be between 30 and 86400.")
        return AutomationBudget(max_total, per_day, runtime)

    @staticmethod
    def next_occurrence(trigger: AutomationTrigger, after: float) -> float | None:
        if trigger.type == "once":
            return trigger.once_at if trigger.once_at is not None and trigger.once_at > after else None
        if trigger.type == "interval":
            interval = int(trigger.interval_seconds or 0)
            if interval <= 0:
                return None
            return after + interval
        return CronSchedule.parse(str(trigger.cron or "")).next_after(after, trigger.timezone)

    @staticmethod
    def occurrence_key(automation_id: str, scheduled_at: float) -> str:
        raw = f"{automation_id}:{scheduled_at:.6f}".encode("utf-8")
        return f"occ_{hashlib.sha256(raw).hexdigest()[:32]}"

    @staticmethod
    def serialize_definition(item: AutomationDefinition, *, include_prompt: bool = True) -> dict[str, Any]:
        payload = asdict(item)
        if not include_prompt:
            payload.pop("prompt", None)
            payload["prompt_length"] = len(item.prompt)
        return payload

    @staticmethod
    def serialize_execution(item: AutomationExecution, *, include_result: bool = True) -> dict[str, Any]:
        payload = asdict(item)
        if not include_result:
            payload.pop("result", None)
            payload["result_length"] = len(item.result or "")
        return payload

    def _create_execution_unlocked(
        self,
        definition: AutomationDefinition,
        *,
        scheduled_at: float,
        occurrence_key: str,
        status: AutomationExecutionStatus = "pending",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutomationExecution:
        item = AutomationExecution(
            execution_id=f"aexec_{uuid.uuid4().hex}",
            automation_id=definition.automation_id,
            occurrence_key=occurrence_key,
            scheduled_at=float(scheduled_at),
            available_at=time.time(),
            status=status,
            error=error,
            metadata=dict(metadata or {}),
            completed_at=time.time() if status in {"skipped", "budget_limited"} else None,
        )
        self._executions[item.execution_id] = item
        return item

    def _budget_error_unlocked(self, definition: AutomationDefinition, now: float) -> str | None:
        completed = [
            item
            for item in self._executions.values()
            if item.automation_id == definition.automation_id
            and item.status in {"completed", "running", "waiting_approval", "waiting_client"}
        ]
        if definition.budget.max_runs_total is not None and len(completed) >= definition.budget.max_runs_total:
            return "Automation total run budget reached."
        zone = ZoneInfo(definition.trigger.timezone)
        local_day = datetime.fromtimestamp(now, zone).date()
        daily = sum(
            1
            for item in completed
            if datetime.fromtimestamp(item.scheduled_at, zone).date() == local_day
        )
        if daily >= definition.budget.max_runs_per_day:
            return "Automation daily run budget reached."
        return None

    def _has_active_unlocked(self, automation_id: str) -> bool:
        return any(
            item.automation_id == automation_id
            and item.status in {"pending", "running", "waiting_approval", "waiting_client"}
            for item in self._executions.values()
        )

    def _require_definition_unlocked(self, automation_id: str) -> AutomationDefinition:
        item = self._definitions.get(automation_id)
        if item is None:
            raise AutomationNotFoundError("Automation not found.")
        return item

    def _require_execution_unlocked(self, execution_id: str) -> AutomationExecution:
        item = self._executions.get(execution_id)
        if item is None:
            raise AutomationNotFoundError("Automation execution not found.")
        return item

    @staticmethod
    def _clear_lease(item: AutomationExecution) -> None:
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = 0.0

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "xpert-automations-v1",
            "definitions": [asdict(item) for item in self._definitions.values()],
            "executions": [asdict(item) for item in self._executions.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("definitions", []):
                if not isinstance(raw, dict):
                    continue
                raw["trigger"] = AutomationTrigger(**dict(raw.get("trigger") or {}))
                raw["budget"] = AutomationBudget(**dict(raw.get("budget") or {}))
                item = AutomationDefinition(**raw)
                self._definitions[item.automation_id] = item
            for raw in payload.get("executions", []):
                if not isinstance(raw, dict):
                    continue
                item = AutomationExecution(**raw)
                self._executions[item.execution_id] = item
        except Exception:
            self._definitions = {}
            self._executions = {}
        # Invalid new deadline evidence must not silently turn into an empty Store.
        for item in self._executions.values():
            self._runtime_remaining(item, time.time())
