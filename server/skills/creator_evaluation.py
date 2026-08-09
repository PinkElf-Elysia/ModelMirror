from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from statistics import mean
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator


SkillEvaluationTarget = Literal["baseline", "candidate"]
SkillEvaluationRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "stale",
]
SkillEvaluationItemStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "skill_not_read",
]
SkillEvaluationReviewDecision = Literal["accept", "revise"]
SkillEvaluationQualityMode = Literal["objective", "subjective"]
SkillEvaluationAssertionKind = Literal[
    "exact_match",
    "contains",
    "not_contains",
    "json_schema",
    "file_exists",
    "file_sha256",
]


class SkillEvaluationError(RuntimeError):
    """Base error for isolated Skill Creator evaluations."""


class SkillEvaluationNotFoundError(SkillEvaluationError):
    pass


class SkillEvaluationConflictError(SkillEvaluationError):
    pass


class SkillEvaluationStateError(SkillEvaluationError):
    pass


class SkillEvaluationStorageError(SkillEvaluationError):
    pass


class SkillEvaluationValidationError(SkillEvaluationError):
    def __init__(self, message: str, *, code: str = "skill_evaluation_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SkillEvaluationOverlay:
    """Immutable Skill package snapshot used by one side of an evaluation."""

    overlay_id: str
    draft_id: str
    draft_revision: int
    content_digest: str
    package: dict[str, Any]
    package_fingerprint: str
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class SkillEvaluationCase:
    case_id: str
    name: str
    prompt: str
    expected_behavior: str
    fixtures: list[dict[str, str]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    case_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class SkillEvaluationCaseSet:
    """One immutable, content-bound revision of a session's test cases."""

    session_id: str
    cases_revision: int
    draft_id: str
    draft_revision: int
    content_digest: str
    quality_mode: SkillEvaluationQualityMode
    cases: list[SkillEvaluationCase]
    case_set_fingerprint: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class SkillEvaluationItem:
    item_id: str
    pair_id: str
    case_id: str
    target: SkillEvaluationTarget
    repetition: int
    overlay_id: str | None
    status: SkillEvaluationItemStatus = "pending"
    attempts: int = 0
    output: str = ""
    actual_model: str | None = None
    skill_read: bool | None = None
    work_manifest: list[dict[str, Any]] = field(default_factory=list)
    assertion_results: list[dict[str, Any]] = field(default_factory=list)
    score: float | None = None
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    runtime_run_id: str | None = None
    error_code: str | None = None
    error: str | None = None
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class SkillEvaluationReview:
    review_id: str
    review_revision: int
    decision: SkillEvaluationReviewDecision
    reason: str
    feedback_revision: int
    feedback: str
    actor_kind: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class SkillEvaluationRun:
    run_id: str
    session_id: str
    draft_id: str
    draft_revision: int
    frozen_digest: str
    baseline_overlay_id: str | None
    candidate_overlay_id: str
    model_id: str
    repetitions: int
    cases: list[SkillEvaluationCase]
    items: list[SkillEvaluationItem]
    config: dict[str, Any]
    case_set_revision: int | None = None
    status: SkillEvaluationRunStatus = "queued"
    revision: int = 1
    review_state: Literal["pending", "accepted", "revise"] = "pending"
    feedback: str = ""
    feedback_revision: int = 0
    reviews: list[SkillEvaluationReview] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None


@dataclass(frozen=True, slots=True)
class SkillEvaluationRunnerResult:
    output: str
    actual_model: str
    skill_read: bool
    work_manifest: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    runtime_run_id: str | None = None


class SkillEvaluationRunner(Protocol):
    async def __call__(
        self,
        run: SkillEvaluationRun,
        item: SkillEvaluationItem,
        case: SkillEvaluationCase,
        overlay: SkillEvaluationOverlay | None,
    ) -> SkillEvaluationRunnerResult | Mapping[str, Any]: ...


RunnerCallable = Callable[
    [
        SkillEvaluationRun,
        SkillEvaluationItem,
        SkillEvaluationCase,
        SkillEvaluationOverlay | None,
    ],
    Awaitable[SkillEvaluationRunnerResult | Mapping[str, Any]],
]


class SkillEvaluationStore:
    """Atomic, fail-closed storage for immutable overlays and resumable runs."""

    SCHEMA_VERSION = 1
    REQUIRED_CASE_COUNT = 3
    MAX_PACKAGE_BYTES = 4 * 1024 * 1024
    MAX_FIXTURE_BYTES = 256 * 1024
    MAX_OUTPUT_CHARS = 50_000

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        configured = os.getenv("SKILL_CREATOR_EVALUATION_STORAGE_DIR", "").strip()
        self.storage_dir = Path(
            storage_dir or configured or runtime_dir or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "skill_creator_evaluations.json"
        self._lock = threading.RLock()
        self._overlays: dict[str, SkillEvaluationOverlay] = {}
        self._case_sets: dict[str, list[SkillEvaluationCaseSet]] = {}
        self._runs: dict[str, SkillEvaluationRun] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def create_overlay(
        self,
        *,
        draft_id: str,
        draft_revision: int,
        content_digest: str,
        package: Mapping[str, Any],
        overlay_id: str | None = None,
    ) -> SkillEvaluationOverlay:
        clean_package = self._json_mapping(package, "package")
        encoded = self._canonical_json(clean_package).encode("utf-8")
        if len(encoded) > self.MAX_PACKAGE_BYTES:
            raise SkillEvaluationValidationError(
                "Evaluation overlay package is too large.",
                code="skill_evaluation_overlay_too_large",
            )
        item = SkillEvaluationOverlay(
            overlay_id=self._identifier(
                overlay_id or f"skill_eval_overlay_{uuid.uuid4().hex}",
                "overlay_id",
            ),
            draft_id=self._identifier(draft_id, "draft_id"),
            draft_revision=self._positive_int(draft_revision, "draft_revision"),
            content_digest=self._digest(content_digest, "content_digest"),
            package=clean_package,
            package_fingerprint=hashlib.sha256(encoded).hexdigest(),
        )
        with self._lock:
            self._ensure_writable_unlocked()
            existing = self._overlays.get(item.overlay_id)
            if existing is not None:
                if asdict(existing) == asdict(item):
                    return copy.deepcopy(existing)
                raise SkillEvaluationConflictError(
                    "Evaluation overlay id already exists with different content."
                )
            previous = self._snapshot_unlocked()
            self._overlays[item.overlay_id] = item
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(item)

    def require_overlay(self, overlay_id: str) -> SkillEvaluationOverlay:
        with self._lock:
            self._ensure_readable_unlocked()
            item = self._overlays.get(overlay_id)
            if item is None:
                raise SkillEvaluationNotFoundError(
                    f"Skill evaluation overlay not found: {overlay_id}"
                )
            return copy.deepcopy(item)

    def save_cases(
        self,
        *,
        session_id: str,
        draft_id: str,
        draft_revision: int,
        content_digest: str,
        expected_revision: int,
        cases: list[SkillEvaluationCase | Mapping[str, Any]],
        quality_mode: SkillEvaluationQualityMode,
    ) -> SkillEvaluationCaseSet:
        """Append an immutable case-set revision; prompts stay out of Session Store."""

        clean_session_id = self._identifier(session_id, "session_id")
        clean_draft_id = self._identifier(draft_id, "draft_id")
        clean_draft_revision = self._positive_int(
            draft_revision, "draft_revision"
        )
        clean_digest = self._digest(content_digest, "content_digest")
        if quality_mode not in {"objective", "subjective"}:
            raise SkillEvaluationValidationError("Invalid evaluation quality mode.")
        clean_cases = [self.normalize_case(item) for item in cases]
        if quality_mode == "objective" and len(clean_cases) != self.REQUIRED_CASE_COUNT:
            raise SkillEvaluationValidationError(
                "Objective Skill evaluation requires exactly three cases.",
                code="skill_evaluation_three_cases_required",
            )
        if quality_mode == "subjective" and len(clean_cases) not in {
            0,
            self.REQUIRED_CASE_COUNT,
        }:
            raise SkillEvaluationValidationError(
                "Subjective evaluation supports either no cases or exactly three cases.",
                code="skill_evaluation_subjective_cases_invalid",
            )
        if len({item.case_id for item in clean_cases}) != len(clean_cases):
            raise SkillEvaluationValidationError(
                "Evaluation case ids must be unique.",
                code="skill_evaluation_case_duplicate",
            )
        fingerprint_payload = {
            "session_id": clean_session_id,
            "draft_id": clean_draft_id,
            "draft_revision": clean_draft_revision,
            "content_digest": clean_digest,
            "quality_mode": quality_mode,
            "case_fingerprints": [item.case_fingerprint for item in clean_cases],
        }
        fingerprint = hashlib.sha256(
            self._canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        with self._lock:
            self._ensure_writable_unlocked()
            revisions = self._case_sets.get(clean_session_id) or []
            current_revision = len(revisions)
            if int(expected_revision) != current_revision:
                raise SkillEvaluationConflictError(
                    "Evaluation cases changed. Reload before saving."
                )
            if revisions and revisions[-1].case_set_fingerprint == fingerprint:
                return copy.deepcopy(revisions[-1])
            item = SkillEvaluationCaseSet(
                session_id=clean_session_id,
                cases_revision=current_revision + 1,
                draft_id=clean_draft_id,
                draft_revision=clean_draft_revision,
                content_digest=clean_digest,
                quality_mode=quality_mode,
                cases=clean_cases,
                case_set_fingerprint=fingerprint,
            )
            previous = self._snapshot_unlocked()
            self._case_sets.setdefault(clean_session_id, []).append(item)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(item)

    def require_cases(
        self, session_id: str, *, revision: int | None = None
    ) -> SkillEvaluationCaseSet:
        with self._lock:
            self._ensure_readable_unlocked()
            revisions = self._case_sets.get(str(session_id)) or []
            if not revisions:
                raise SkillEvaluationNotFoundError(
                    f"Skill evaluation cases not found: {session_id}"
                )
            if revision is None:
                return copy.deepcopy(revisions[-1])
            clean_revision = self._positive_int(revision, "cases_revision")
            if clean_revision > len(revisions):
                raise SkillEvaluationNotFoundError(
                    f"Skill evaluation case revision not found: {clean_revision}"
                )
            return copy.deepcopy(revisions[clean_revision - 1])

    def create_run(
        self,
        *,
        session_id: str,
        draft_id: str,
        draft_revision: int,
        frozen_digest: str,
        candidate_overlay_id: str,
        cases: list[SkillEvaluationCase | Mapping[str, Any]] | None = None,
        model_id: str,
        repetitions: int = 1,
        baseline_overlay_id: str | None = None,
        case_set_revision: int | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> SkillEvaluationRun:
        clean_session_id = self._identifier(session_id, "session_id")
        clean_digest = self._digest(frozen_digest, "frozen_digest")
        clean_draft_id = self._identifier(draft_id, "draft_id")
        clean_draft_revision = self._positive_int(
            draft_revision, "draft_revision"
        )
        bound_case_set: SkillEvaluationCaseSet | None = None
        if case_set_revision is not None:
            bound_case_set = self.require_cases(
                clean_session_id, revision=case_set_revision
            )
            if (
                bound_case_set.draft_id != clean_draft_id
                or bound_case_set.draft_revision != clean_draft_revision
                or bound_case_set.content_digest != clean_digest
            ):
                raise SkillEvaluationConflictError(
                    "Evaluation cases no longer match the requested draft revision."
                )
            clean_cases = copy.deepcopy(bound_case_set.cases)
            if cases is not None:
                supplied_cases = [self.normalize_case(item) for item in cases]
                if [item.case_fingerprint for item in supplied_cases] != [
                    item.case_fingerprint for item in clean_cases
                ]:
                    raise SkillEvaluationConflictError(
                        "Submitted cases do not match the frozen case-set revision."
                    )
        else:
            if cases is None:
                raise SkillEvaluationValidationError(
                    "Evaluation cases are required.",
                    code="skill_evaluation_cases_required",
                )
            clean_cases = [self.normalize_case(item) for item in cases]
        if len(clean_cases) != self.REQUIRED_CASE_COUNT:
            raise SkillEvaluationValidationError(
                "Objective Skill evaluation requires exactly three cases.",
                code="skill_evaluation_three_cases_required",
            )
        if len({item.case_id for item in clean_cases}) != len(clean_cases):
            raise SkillEvaluationValidationError(
                "Evaluation case ids must be unique.",
                code="skill_evaluation_case_duplicate",
            )
        clean_repetitions = self._bounded_int(
            repetitions, "repetitions", minimum=1, maximum=3
        )
        clean_model_id = self._required_text(model_id, "model_id", maximum=300)
        clean_config = self._normalize_config(config or {})
        with self._lock:
            self._ensure_writable_unlocked()
            candidate = self._overlay_unlocked(candidate_overlay_id)
            if (
                candidate.draft_id != clean_draft_id
                or candidate.draft_revision != clean_draft_revision
                or candidate.content_digest != clean_digest
            ):
                raise SkillEvaluationConflictError(
                    "Candidate overlay no longer matches the requested draft revision."
                )
            baseline: SkillEvaluationOverlay | None = None
            if baseline_overlay_id is not None:
                baseline = self._overlay_unlocked(baseline_overlay_id)
                if baseline.draft_id != clean_draft_id:
                    raise SkillEvaluationConflictError(
                        "Baseline and candidate overlays must belong to the same draft."
                    )
            run_id = f"skill_eval_run_{uuid.uuid4().hex}"
            now = time.time()
            items: list[SkillEvaluationItem] = []
            for case in clean_cases:
                for repetition in range(1, clean_repetitions + 1):
                    pair_key = f"{run_id}:{case.case_id}:{repetition}"
                    pair_id = "skill_eval_pair_" + hashlib.sha256(
                        pair_key.encode("utf-8")
                    ).hexdigest()[:24]
                    for target, target_overlay_id in (
                        ("baseline", baseline.overlay_id if baseline else None),
                        ("candidate", candidate.overlay_id),
                    ):
                        item_key = f"{pair_key}:{target}"
                        item_id = "skill_eval_item_" + hashlib.sha256(
                            item_key.encode("utf-8")
                        ).hexdigest()[:24]
                        items.append(
                            SkillEvaluationItem(
                                item_id=item_id,
                                pair_id=pair_id,
                                case_id=case.case_id,
                                target=target,  # type: ignore[arg-type]
                                repetition=repetition,
                                overlay_id=target_overlay_id,
                                created_at=now,
                                updated_at=now,
                            )
                        )
            run = SkillEvaluationRun(
                run_id=run_id,
                session_id=clean_session_id,
                draft_id=clean_draft_id,
                draft_revision=clean_draft_revision,
                frozen_digest=clean_digest,
                baseline_overlay_id=baseline.overlay_id if baseline else None,
                candidate_overlay_id=candidate.overlay_id,
                model_id=clean_model_id,
                repetitions=clean_repetitions,
                cases=clean_cases,
                items=items,
                config=clean_config,
                case_set_revision=(
                    bound_case_set.cases_revision if bound_case_set else None
                ),
                created_at=now,
                updated_at=now,
            )
            previous = self._snapshot_unlocked()
            self._runs[run.run_id] = run
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def require_run(self, run_id: str) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_readable_unlocked()
            return copy.deepcopy(self._run_unlocked(run_id))

    def list_runs(
        self, *, session_id: str | None = None, limit: int = 100
    ) -> list[SkillEvaluationRun]:
        with self._lock:
            self._ensure_readable_unlocked()
            items = list(self._runs.values())
            if session_id is not None:
                items = [item for item in items if item.session_id == session_id]
            items.sort(key=lambda item: item.updated_at, reverse=True)
            return copy.deepcopy(items[: max(1, min(int(limit), 500))])

    def list_quarantined(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_readable_unlocked()
            return copy.deepcopy(self._quarantine)

    def claim_next_run(self) -> SkillEvaluationRun | None:
        with self._lock:
            self._ensure_writable_unlocked()
            queued = sorted(
                (item for item in self._runs.values() if item.status == "queued"),
                key=lambda item: item.created_at,
            )
            if not queued:
                return None
            run = queued[0]
            previous = self._snapshot_unlocked()
            run.status = "running"
            run.started_at = run.started_at or time.time()
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def claim_pairs(
        self, run_id: str, *, limit_pairs: int = 1
    ) -> list[list[SkillEvaluationItem]]:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.status != "running" or run.cancel_requested:
                return []
            grouped: dict[str, list[SkillEvaluationItem]] = {}
            for item in run.items:
                grouped.setdefault(item.pair_id, []).append(item)
            claimed: list[list[SkillEvaluationItem]] = []
            previous = self._snapshot_unlocked()
            now = time.time()
            for pair in grouped.values():
                if len(pair) != 2 or any(item.status == "running" for item in pair):
                    continue
                pending = [item for item in pair if item.status == "pending"]
                if not pending:
                    continue
                pair.sort(key=lambda item: 0 if item.target == "baseline" else 1)
                for item in pending:
                    item.status = "running"
                    item.attempts += 1
                    item.updated_at = now
                # A recovered pair may already have one completed side. Preserve
                # that immutable result and only rerun the unfinished side.
                claimed.append(copy.deepcopy(pending))
                if len(claimed) >= max(1, min(int(limit_pairs), 4)):
                    break
            if claimed:
                self._touch_run(run)
                self._save_or_restore_unlocked(previous)
            return claimed

    def record_item_result(
        self,
        run_id: str,
        item_id: str,
        *,
        result: Mapping[str, Any],
    ) -> SkillEvaluationItem:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            item = self._item_unlocked(run, item_id)
            if item.status != "running":
                return copy.deepcopy(item)
            previous = self._snapshot_unlocked()
            self._apply_item_result(item, result)
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(item)

    def complete_run(
        self, run_id: str, report: Mapping[str, Any]
    ) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if any(item.status in {"pending", "running"} for item in run.items):
                raise SkillEvaluationStateError(
                    "Cannot complete an evaluation with unfinished items."
                )
            previous = self._snapshot_unlocked()
            run.status = "cancelled" if run.cancel_requested else "completed"
            run.report = self._json_mapping(report, "report")
            run.completed_at = time.time()
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def fail_run(
        self, run_id: str, error: str, *, code: str = "evaluation_failed"
    ) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.status in {"completed", "cancelled", "stale"}:
                return copy.deepcopy(run)
            previous = self._snapshot_unlocked()
            for item in run.items:
                if item.status in {"pending", "running"}:
                    item.status = "failed"
                    item.error_code = str(code)[:120]
                    item.error = str(error)[:500]
                    item.updated_at = time.time()
            run.status = "failed"
            run.error = str(error)[:500]
            run.completed_at = time.time()
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def cancel_run(self, run_id: str) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.status in {"completed", "failed", "cancelled", "stale"}:
                return copy.deepcopy(run)
            previous = self._snapshot_unlocked()
            run.cancel_requested = True
            for item in run.items:
                if item.status == "pending":
                    item.status = "cancelled"
                    item.error_code = "evaluation_cancelled"
                    item.error = "Evaluation cancelled."
                    item.updated_at = time.time()
            if run.status == "queued":
                run.status = "cancelled"
                run.completed_at = time.time()
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def retry_run(
        self, run_id: str, *, case_ids: list[str] | None = None
    ) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.review_state != "pending" or run.reviews:
                raise SkillEvaluationConflictError(
                    "Reviewed evaluations cannot be retried."
                )
            if run.status not in {"completed", "failed", "cancelled"}:
                raise SkillEvaluationStateError(
                    "Only terminal evaluations can be retried."
                )
            known = {case.case_id for case in run.cases}
            selected = set(case_ids or known)
            if not selected or not selected <= known:
                raise SkillEvaluationValidationError(
                    "Retry case ids do not match this evaluation.",
                    code="skill_evaluation_retry_cases_invalid",
                )
            previous = self._snapshot_unlocked()
            for item in run.items:
                if item.case_id not in selected:
                    continue
                item.attempt_history.append(self._attempt_snapshot(item))
                item.attempt_history = item.attempt_history[-10:]
                self._clear_item_result(item)
            run.status = "queued"
            run.cancel_requested = False
            run.report = {}
            run.error = None
            run.completed_at = None
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def recover_runs(self) -> int:
        with self._lock:
            self._ensure_writable_unlocked()
            previous = self._snapshot_unlocked()
            changed = 0
            for run in self._runs.values():
                if run.status not in {"queued", "running"}:
                    continue
                if run.cancel_requested:
                    for item in run.items:
                        if item.status in {"pending", "running"}:
                            item.status = "cancelled"
                            item.error_code = "evaluation_cancelled"
                            item.error = "Evaluation cancelled during recovery."
                            item.updated_at = time.time()
                    run.status = "cancelled"
                    run.completed_at = time.time()
                else:
                    for item in run.items:
                        if item.status == "running":
                            item.status = "pending"
                            item.updated_at = time.time()
                    run.status = "queued"
                self._touch_run(run)
                changed += 1
            if changed:
                self._save_or_restore_unlocked(previous)
            return changed

    def mark_stale(self, run_id: str, *, reason: str) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.status == "stale":
                return copy.deepcopy(run)
            previous = self._snapshot_unlocked()
            run.status = "stale"
            run.error = self._required_text(reason, "reason", maximum=500)
            run.completed_at = time.time()
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def review_run(
        self,
        run_id: str,
        *,
        expected_revision: int,
        expected_feedback_revision: int,
        decision: SkillEvaluationReviewDecision,
        reason: str,
        actor_kind: str = "local_console",
    ) -> SkillEvaluationRun:
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.revision != int(expected_revision):
                raise SkillEvaluationConflictError(
                    "Evaluation changed. Reload before reviewing."
                )
            if run.feedback_revision != int(expected_feedback_revision):
                raise SkillEvaluationConflictError(
                    "Evaluation feedback changed. Reload before reviewing."
                )
            if run.status != "completed" or run.cancel_requested:
                raise SkillEvaluationStateError(
                    "Only completed evaluations can be reviewed."
                )
            if run.review_state != "pending" or run.reviews:
                raise SkillEvaluationConflictError(
                    "This evaluation already has a review decision."
                )
            if decision not in {"accept", "revise"}:
                raise SkillEvaluationValidationError(
                    "Invalid evaluation review decision."
                )
            clean_reason = str(reason or "").strip()
            if decision == "revise" and not (clean_reason or run.feedback.strip()):
                raise SkillEvaluationValidationError(
                    "Revision feedback is required.",
                    code="skill_evaluation_feedback_required",
                )
            report = run.report or aggregate_skill_evaluation_report(run)
            if decision == "accept":
                if not bool(report.get("eligible_for_accept")):
                    raise SkillEvaluationStateError(
                        "Evaluation is not eligible for acceptance."
                    )
                if int(report.get("assertion_failed_count") or 0) and not clean_reason:
                    raise SkillEvaluationValidationError(
                        "Accepting failed assertions requires a reason.",
                        code="skill_evaluation_accept_reason_required",
                    )
            previous = self._snapshot_unlocked()
            review = SkillEvaluationReview(
                review_id=f"skill_eval_review_{uuid.uuid4().hex}",
                review_revision=len(run.reviews) + 1,
                decision=decision,
                reason=(clean_reason or run.feedback.strip())[:4_000],
                feedback_revision=run.feedback_revision,
                feedback=run.feedback,
                actor_kind=self._required_text(
                    actor_kind, "actor_kind", maximum=80
                ),
            )
            run.reviews.append(review)
            run.review_state = "accepted" if decision == "accept" else "revise"
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    def save_feedback(
        self,
        run_id: str,
        *,
        expected_revision: int,
        expected_feedback_revision: int,
        feedback: str,
    ) -> SkillEvaluationRun:
        """Persist review feedback separately so UI autosave is conflict-safe."""

        if not isinstance(feedback, str) or len(feedback) > 4_000:
            raise SkillEvaluationValidationError(
                "Invalid evaluation feedback.",
                code="skill_evaluation_feedback_invalid",
            )
        with self._lock:
            self._ensure_writable_unlocked()
            run = self._run_unlocked(run_id)
            if run.revision != int(expected_revision):
                raise SkillEvaluationConflictError(
                    "Evaluation changed. Reload before saving feedback."
                )
            if run.feedback_revision != int(expected_feedback_revision):
                raise SkillEvaluationConflictError(
                    "Evaluation feedback changed. Reload before saving."
                )
            if run.status != "completed" or run.review_state != "pending":
                raise SkillEvaluationStateError(
                    "Feedback can only be edited before reviewing a completed run."
                )
            clean = feedback.strip()
            if run.feedback == clean:
                return copy.deepcopy(run)
            previous = self._snapshot_unlocked()
            run.feedback = clean
            run.feedback_revision += 1
            self._touch_run(run)
            self._save_or_restore_unlocked(previous)
            return copy.deepcopy(run)

    @classmethod
    def normalize_case(
        cls, raw: SkillEvaluationCase | Mapping[str, Any]
    ) -> SkillEvaluationCase:
        if isinstance(raw, SkillEvaluationCase):
            source = asdict(raw)
        elif isinstance(raw, Mapping):
            source = copy.deepcopy(dict(raw))
        else:
            raise SkillEvaluationValidationError("Evaluation case must be an object.")
        case_id = cls._identifier(
            source.get("case_id") or f"skill_eval_case_{uuid.uuid4().hex}",
            "case_id",
        )
        name = cls._required_text(source.get("name"), "name", maximum=160)
        prompt = cls._required_text(source.get("prompt"), "prompt", maximum=20_000)
        expected_behavior = cls._required_text(
            source.get("expected_behavior"), "expected_behavior", maximum=4_000
        )
        fixtures = cls._normalize_fixtures(source.get("fixtures") or [])
        assertions = cls._normalize_assertions(source.get("assertions") or [])
        fingerprint_payload = {
            "case_id": case_id,
            "name": name,
            "prompt": prompt,
            "expected_behavior": expected_behavior,
            "fixtures": fixtures,
            "assertions": assertions,
        }
        fingerprint = hashlib.sha256(
            cls._canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        supplied = source.get("case_fingerprint")
        if supplied and cls._digest(supplied, "case_fingerprint") != fingerprint:
            raise SkillEvaluationConflictError(
                "Evaluation case fingerprint does not match its content."
            )
        return SkillEvaluationCase(
            **fingerprint_payload,
            case_fingerprint=fingerprint,
        )

    @classmethod
    def serialize(cls, item: Any) -> dict[str, Any]:
        if not hasattr(item, "__dataclass_fields__"):
            raise TypeError("Skill evaluation value is not serializable.")
        return asdict(item)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("snapshot must be an object")
            if raw.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            overlays_raw = raw.get("overlays")
            case_sets_raw = raw.get("case_sets", {})
            runs_raw = raw.get("runs")
            quarantine_raw = raw.get("quarantine", [])
            if (
                not isinstance(overlays_raw, dict)
                or not isinstance(case_sets_raw, dict)
                or not isinstance(runs_raw, dict)
            ):
                raise ValueError("snapshot indexes must be objects")
            if not isinstance(quarantine_raw, list):
                raise ValueError("snapshot quarantine must be a list")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._load_error = f"skill_evaluation_store_corrupt: {str(exc)[:300]}"
            return

        for record_id, record in overlays_raw.items():
            try:
                overlay = self._parse_overlay(record)
                if overlay.overlay_id != record_id:
                    raise ValueError("overlay index id mismatch")
                self._overlays[record_id] = overlay
            except Exception as exc:
                self._quarantine.append(
                    self._quarantine_record(
                        "overlay", record_id, record, "invalid_overlay", exc
                    )
                )
        for session_id, records in case_sets_raw.items():
            try:
                if not isinstance(records, list):
                    raise ValueError("case-set revisions must be a list")
                revisions = [self._parse_case_set(record) for record in records]
                if any(item.session_id != session_id for item in revisions):
                    raise ValueError("case-set session index mismatch")
                if [item.cases_revision for item in revisions] != list(
                    range(1, len(revisions) + 1)
                ):
                    raise ValueError("case-set revisions are not contiguous")
                self._case_sets[session_id] = revisions
            except Exception as exc:
                self._quarantine.append(
                    self._quarantine_record(
                        "case_set", session_id, records, "invalid_case_set", exc
                    )
                )
        for record_id, record in runs_raw.items():
            try:
                run = self._parse_run(record)
                if run.run_id != record_id:
                    raise ValueError("run index id mismatch")
                if run.candidate_overlay_id not in self._overlays:
                    raise ValueError("candidate overlay unavailable")
                if (
                    run.baseline_overlay_id is not None
                    and run.baseline_overlay_id not in self._overlays
                ):
                    raise ValueError("baseline overlay unavailable")
                if run.case_set_revision is not None:
                    revisions = self._case_sets.get(run.session_id) or []
                    if run.case_set_revision > len(revisions):
                        raise ValueError("case-set revision unavailable")
                    bound = revisions[run.case_set_revision - 1]
                    if [item.case_fingerprint for item in bound.cases] != [
                        item.case_fingerprint for item in run.cases
                    ]:
                        raise ValueError("run cases differ from frozen case set")
                self._runs[record_id] = run
            except Exception as exc:
                self._quarantine.append(
                    self._quarantine_record(
                        "run", record_id, record, "invalid_run", exc
                    )
                )
        for index, entry in enumerate(quarantine_raw):
            safe = self._sanitize_quarantine_entry(entry, index=index)
            if safe is not None:
                self._quarantine.append(safe)

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp = self.snapshot_path.with_name(
            f".{self.snapshot_path.name}.{uuid.uuid4().hex}.tmp"
        )
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "overlays": {
                item_id: asdict(item) for item_id, item in self._overlays.items()
            },
            "case_sets": {
                session_id: [asdict(item) for item in revisions]
                for session_id, revisions in self._case_sets.items()
            },
            "runs": {item_id: asdict(item) for item_id, item in self._runs.items()},
            "quarantine": copy.deepcopy(self._quarantine),
        }
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.snapshot_path)
        except BaseException as exc:
            with contextlib.suppress(OSError):
                temp.unlink()
            if isinstance(exc, SkillEvaluationStorageError):
                raise
            raise SkillEvaluationStorageError(
                f"Unable to persist Skill evaluation store: {str(exc)[:300]}"
            ) from exc

    def _save_or_restore_unlocked(
        self,
        previous: tuple[
            dict[str, SkillEvaluationOverlay],
            dict[str, list[SkillEvaluationCaseSet]],
            dict[str, SkillEvaluationRun],
            list[dict[str, Any]],
        ],
    ) -> None:
        try:
            self._save_unlocked()
        except BaseException:
            self._overlays, self._case_sets, self._runs, self._quarantine = previous
            raise

    def _snapshot_unlocked(
        self,
    ) -> tuple[
        dict[str, SkillEvaluationOverlay],
        dict[str, list[SkillEvaluationCaseSet]],
        dict[str, SkillEvaluationRun],
        list[dict[str, Any]],
    ]:
        return (
            copy.deepcopy(self._overlays),
            copy.deepcopy(self._case_sets),
            copy.deepcopy(self._runs),
            copy.deepcopy(self._quarantine),
        )

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillEvaluationStorageError(self._load_error)

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()

    def _overlay_unlocked(self, overlay_id: str) -> SkillEvaluationOverlay:
        item = self._overlays.get(str(overlay_id))
        if item is None:
            raise SkillEvaluationNotFoundError(
                f"Skill evaluation overlay not found: {overlay_id}"
            )
        return item

    def _run_unlocked(self, run_id: str) -> SkillEvaluationRun:
        item = self._runs.get(str(run_id))
        if item is None:
            raise SkillEvaluationNotFoundError(
                f"Skill evaluation run not found: {run_id}"
            )
        return item

    @staticmethod
    def _item_unlocked(
        run: SkillEvaluationRun, item_id: str
    ) -> SkillEvaluationItem:
        for item in run.items:
            if item.item_id == item_id:
                return item
        raise SkillEvaluationNotFoundError(
            f"Skill evaluation item not found: {item_id}"
        )

    @classmethod
    def _parse_overlay(cls, raw: Any) -> SkillEvaluationOverlay:
        if not isinstance(raw, dict):
            raise ValueError("overlay must be an object")
        package = cls._json_mapping(raw.get("package"), "package")
        encoded = cls._canonical_json(package).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        if cls._digest(raw.get("package_fingerprint"), "package_fingerprint") != fingerprint:
            raise ValueError("package fingerprint mismatch")
        return SkillEvaluationOverlay(
            overlay_id=cls._identifier(raw.get("overlay_id"), "overlay_id"),
            draft_id=cls._identifier(raw.get("draft_id"), "draft_id"),
            draft_revision=cls._positive_int(
                raw.get("draft_revision"), "draft_revision"
            ),
            content_digest=cls._digest(
                raw.get("content_digest"), "content_digest"
            ),
            package=package,
            package_fingerprint=fingerprint,
            created_at=cls._timestamp(raw.get("created_at")),
        )

    @classmethod
    def _parse_case_set(cls, raw: Any) -> SkillEvaluationCaseSet:
        if not isinstance(raw, dict):
            raise ValueError("case set must be an object")
        quality_mode = str(raw.get("quality_mode") or "")
        if quality_mode not in {"objective", "subjective"}:
            raise ValueError("invalid quality mode")
        cases_raw = raw.get("cases")
        if not isinstance(cases_raw, list):
            raise ValueError("case set cases must be a list")
        cases = [cls.normalize_case(item) for item in cases_raw]
        if len({item.case_id for item in cases}) != len(cases):
            raise ValueError("case-set case ids must be unique")
        if quality_mode == "objective" and len(cases) != cls.REQUIRED_CASE_COUNT:
            raise ValueError("objective case set must contain three cases")
        if quality_mode == "subjective" and len(cases) not in {
            0,
            cls.REQUIRED_CASE_COUNT,
        }:
            raise ValueError("subjective case set must be empty or complete")
        item = SkillEvaluationCaseSet(
            session_id=cls._identifier(raw.get("session_id"), "session_id"),
            cases_revision=cls._positive_int(
                raw.get("cases_revision"), "cases_revision"
            ),
            draft_id=cls._identifier(raw.get("draft_id"), "draft_id"),
            draft_revision=cls._positive_int(
                raw.get("draft_revision"), "draft_revision"
            ),
            content_digest=cls._digest(
                raw.get("content_digest"), "content_digest"
            ),
            quality_mode=quality_mode,  # type: ignore[arg-type]
            cases=cases,
            case_set_fingerprint=cls._digest(
                raw.get("case_set_fingerprint"), "case_set_fingerprint"
            ),
            created_at=cls._timestamp(raw.get("created_at")),
        )
        expected = hashlib.sha256(
            cls._canonical_json(
                {
                    "session_id": item.session_id,
                    "draft_id": item.draft_id,
                    "draft_revision": item.draft_revision,
                    "content_digest": item.content_digest,
                    "quality_mode": item.quality_mode,
                    "case_fingerprints": [
                        case.case_fingerprint for case in item.cases
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()
        if item.case_set_fingerprint != expected:
            raise ValueError("case-set fingerprint mismatch")
        return item

    @classmethod
    def _parse_run(cls, raw: Any) -> SkillEvaluationRun:
        if not isinstance(raw, dict):
            raise ValueError("run must be an object")
        status = str(raw.get("status") or "")
        if status not in {"queued", "running", "completed", "failed", "cancelled", "stale"}:
            raise ValueError("invalid run status")
        review_state = str(raw.get("review_state") or "pending")
        if review_state not in {"pending", "accepted", "revise"}:
            raise ValueError("invalid review state")
        cases_raw = raw.get("cases")
        items_raw = raw.get("items")
        if not isinstance(cases_raw, list) or len(cases_raw) != cls.REQUIRED_CASE_COUNT:
            raise ValueError("run must contain exactly three cases")
        if not isinstance(items_raw, list):
            raise ValueError("run items must be a list")
        cases = [cls.normalize_case(item) for item in cases_raw]
        repetitions = cls._bounded_int(
            raw.get("repetitions"), "repetitions", minimum=1, maximum=3
        )
        items = [cls._parse_item(item) for item in items_raw]
        cls._validate_item_matrix(cases, items, repetitions)
        reviews_raw = raw.get("reviews", [])
        if not isinstance(reviews_raw, list):
            raise ValueError("run reviews must be a list")
        reviews = [cls._parse_review(item) for item in reviews_raw]
        if bool(reviews) != (review_state != "pending"):
            raise ValueError("review state does not match review history")
        return SkillEvaluationRun(
            run_id=cls._identifier(raw.get("run_id"), "run_id"),
            session_id=cls._identifier(raw.get("session_id"), "session_id"),
            draft_id=cls._identifier(raw.get("draft_id"), "draft_id"),
            draft_revision=cls._positive_int(
                raw.get("draft_revision"), "draft_revision"
            ),
            frozen_digest=cls._digest(
                raw.get("frozen_digest"), "frozen_digest"
            ),
            baseline_overlay_id=cls._optional_identifier(
                raw.get("baseline_overlay_id")
            ),
            candidate_overlay_id=cls._identifier(
                raw.get("candidate_overlay_id"), "candidate_overlay_id"
            ),
            model_id=cls._required_text(
                raw.get("model_id"), "model_id", maximum=300
            ),
            repetitions=repetitions,
            cases=cases,
            items=items,
            config=cls._normalize_config(raw.get("config") or {}),
            case_set_revision=(
                None
                if raw.get("case_set_revision") is None
                else cls._positive_int(
                    raw.get("case_set_revision"), "case_set_revision"
                )
            ),
            status=status,  # type: ignore[arg-type]
            revision=cls._positive_int(raw.get("revision"), "revision"),
            review_state=review_state,  # type: ignore[arg-type]
            feedback=str(raw.get("feedback") or "")[:4_000],
            feedback_revision=cls._nonnegative_int(
                raw.get("feedback_revision", 0), "feedback_revision"
            ),
            reviews=reviews,
            report=cls._json_mapping(raw.get("report") or {}, "report"),
            cancel_requested=bool(raw.get("cancel_requested")),
            error=cls._optional_text(raw.get("error"), maximum=500),
            created_at=cls._timestamp(raw.get("created_at")),
            updated_at=cls._timestamp(raw.get("updated_at")),
            started_at=cls._optional_timestamp(raw.get("started_at")),
            completed_at=cls._optional_timestamp(raw.get("completed_at")),
        )

    @classmethod
    def _parse_item(cls, raw: Any) -> SkillEvaluationItem:
        if not isinstance(raw, dict):
            raise ValueError("item must be an object")
        target = str(raw.get("target") or "")
        if target not in {"baseline", "candidate"}:
            raise ValueError("invalid item target")
        status = str(raw.get("status") or "")
        if status not in {
            "pending", "running", "completed", "failed", "cancelled", "skill_not_read"
        }:
            raise ValueError("invalid item status")
        history = raw.get("attempt_history", [])
        if not isinstance(history, list) or len(history) > 10:
            raise ValueError("invalid attempt history")
        score = raw.get("score")
        if score is not None:
            score = float(score)
            if not 0.0 <= score <= 1.0:
                raise ValueError("invalid item score")
        assertion_results = raw.get("assertion_results", [])
        if not isinstance(assertion_results, list):
            raise ValueError("invalid assertion results")
        return SkillEvaluationItem(
            item_id=cls._identifier(raw.get("item_id"), "item_id"),
            pair_id=cls._identifier(raw.get("pair_id"), "pair_id"),
            case_id=cls._identifier(raw.get("case_id"), "case_id"),
            target=target,  # type: ignore[arg-type]
            repetition=cls._bounded_int(
                raw.get("repetition"), "repetition", minimum=1, maximum=3
            ),
            overlay_id=cls._optional_identifier(raw.get("overlay_id")),
            status=status,  # type: ignore[arg-type]
            attempts=max(0, int(raw.get("attempts") or 0)),
            output=str(raw.get("output") or "")[: cls.MAX_OUTPUT_CHARS],
            actual_model=cls._optional_text(raw.get("actual_model"), maximum=300),
            skill_read=(
                None if raw.get("skill_read") is None else bool(raw.get("skill_read"))
            ),
            work_manifest=normalize_work_manifest(raw.get("work_manifest") or []),
            assertion_results=copy.deepcopy(assertion_results),
            score=score,
            usage=cls._normalize_usage(raw.get("usage") or {}),
            latency_ms=max(0.0, float(raw.get("latency_ms") or 0.0)),
            runtime_run_id=cls._optional_identifier(raw.get("runtime_run_id")),
            error_code=cls._optional_text(raw.get("error_code"), maximum=120),
            error=cls._optional_text(raw.get("error"), maximum=500),
            attempt_history=copy.deepcopy(history),
            created_at=cls._timestamp(raw.get("created_at")),
            updated_at=cls._timestamp(raw.get("updated_at")),
        )

    @classmethod
    def _parse_review(cls, raw: Any) -> SkillEvaluationReview:
        if not isinstance(raw, dict):
            raise ValueError("review must be an object")
        decision = str(raw.get("decision") or "")
        if decision not in {"accept", "revise"}:
            raise ValueError("invalid review decision")
        return SkillEvaluationReview(
            review_id=cls._identifier(raw.get("review_id"), "review_id"),
            review_revision=cls._positive_int(
                raw.get("review_revision"), "review_revision"
            ),
            decision=decision,  # type: ignore[arg-type]
            reason=str(raw.get("reason") or "")[:4_000],
            feedback_revision=cls._nonnegative_int(
                raw.get("feedback_revision", 0), "feedback_revision"
            ),
            feedback=str(raw.get("feedback") or "")[:4_000],
            actor_kind=cls._required_text(
                raw.get("actor_kind"), "actor_kind", maximum=80
            ),
            created_at=cls._timestamp(raw.get("created_at")),
        )

    @classmethod
    def _validate_item_matrix(
        cls,
        cases: list[SkillEvaluationCase],
        items: list[SkillEvaluationItem],
        repetitions: int,
    ) -> None:
        expected = {
            (case.case_id, repetition, target)
            for case in cases
            for repetition in range(1, repetitions + 1)
            for target in ("baseline", "candidate")
        }
        actual = {(item.case_id, item.repetition, item.target) for item in items}
        if actual != expected or len(actual) != len(items):
            raise ValueError("evaluation item matrix is incomplete or duplicated")
        pairs: dict[tuple[str, int], set[str]] = {}
        pair_ids: dict[tuple[str, int], set[str]] = {}
        for item in items:
            key = (item.case_id, item.repetition)
            pairs.setdefault(key, set()).add(item.target)
            pair_ids.setdefault(key, set()).add(item.pair_id)
        if any(value != {"baseline", "candidate"} for value in pairs.values()):
            raise ValueError("evaluation targets are not paired")
        if any(len(value) != 1 for value in pair_ids.values()):
            raise ValueError("evaluation pair ids do not match")

    @classmethod
    def _normalize_fixtures(cls, raw: Any) -> list[dict[str, str]]:
        if isinstance(raw, Mapping):
            source = [{"path": key, "content": value} for key, value in raw.items()]
        elif isinstance(raw, list):
            source = raw
        else:
            raise SkillEvaluationValidationError("Fixtures must be a list or object.")
        if len(source) > 10:
            raise SkillEvaluationValidationError(
                "Each evaluation case supports at most ten fixtures."
            )
        fixtures: list[dict[str, str]] = []
        seen: set[str] = set()
        total = 0
        for entry in source:
            if not isinstance(entry, Mapping):
                raise SkillEvaluationValidationError("Fixture must be an object.")
            path = cls._relative_path(entry.get("path"), "fixture path")
            if path in seen:
                raise SkillEvaluationValidationError("Fixture paths must be unique.")
            content = entry.get("content")
            if not isinstance(content, str):
                raise SkillEvaluationValidationError("Fixture content must be UTF-8 text.")
            try:
                size = len(content.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as exc:
                raise SkillEvaluationValidationError(
                    "Fixture content must be valid UTF-8 text."
                ) from exc
            total += size
            if total > cls.MAX_FIXTURE_BYTES:
                raise SkillEvaluationValidationError(
                    "Evaluation fixtures are too large.",
                    code="skill_evaluation_fixtures_too_large",
                )
            seen.add(path)
            fixtures.append({"path": path, "content": content})
        fixtures.sort(key=lambda item: item["path"].casefold())
        return fixtures

    @classmethod
    def _normalize_assertions(cls, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or len(raw) > 20:
            raise SkillEvaluationValidationError("Invalid assertion list.")
        assertions: list[dict[str, Any]] = []
        allowed = {
            "exact_match", "contains", "not_contains", "json_schema",
            "file_exists", "file_sha256",
        }
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise SkillEvaluationValidationError("Assertion must be an object.")
            kind = str(entry.get("kind") or "").strip()
            if kind not in allowed:
                raise SkillEvaluationValidationError(
                    f"Unsupported assertion kind: {kind or '(missing)'}."
                )
            normalized: dict[str, Any] = {"kind": kind}
            if kind in {"exact_match", "contains", "not_contains"}:
                normalized["value"] = cls._required_text(
                    entry.get("value"), "assertion value", maximum=20_000
                )
            elif kind == "json_schema":
                schema = cls._json_mapping(entry.get("schema"), "assertion schema")
                try:
                    Draft202012Validator.check_schema(schema)
                except Exception as exc:
                    raise SkillEvaluationValidationError(
                        f"Invalid JSON Schema assertion: {str(exc)[:300]}"
                    ) from exc
                normalized["schema"] = schema
            elif kind == "file_exists":
                normalized["path"] = cls._relative_path(
                    entry.get("path"), "assertion path"
                )
            else:
                normalized["path"] = cls._relative_path(
                    entry.get("path"), "assertion path"
                )
                normalized["sha256"] = cls._digest(
                    entry.get("sha256"), "assertion sha256"
                )
            assertions.append(normalized)
        return assertions

    @classmethod
    def _normalize_config(cls, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise SkillEvaluationValidationError("Evaluation config must be an object.")
        forbidden = {"judge", "judge_model_id", "rubric", "network"}
        if forbidden & set(raw):
            raise SkillEvaluationValidationError(
                "Judge and network configuration are not supported by Skill evaluation.",
                code="skill_evaluation_config_forbidden",
            )
        return {
            "timeout_seconds": cls._bounded_int(
                raw.get("timeout_seconds", 120),
                "timeout_seconds",
                minimum=10,
                maximum=600,
            ),
            "max_concurrency": cls._bounded_int(
                raw.get("max_concurrency", 2),
                "max_concurrency",
                minimum=1,
                maximum=4,
            ),
            "max_output_chars": cls._bounded_int(
                raw.get("max_output_chars", 20_000),
                "max_output_chars",
                minimum=1_000,
                maximum=cls.MAX_OUTPUT_CHARS,
            ),
            "seed": cls._bounded_int(
                raw.get("seed", 0),
                "seed",
                minimum=0,
                maximum=2_147_483_647,
            ),
            "temperature": 0,
        }

    @classmethod
    def _apply_item_result(
        cls, item: SkillEvaluationItem, result: Mapping[str, Any]
    ) -> None:
        status = str(result.get("status") or "completed")
        if status not in {"completed", "failed", "cancelled", "skill_not_read"}:
            raise SkillEvaluationValidationError("Invalid evaluation item result status.")
        item.status = status  # type: ignore[assignment]
        item.output = str(result.get("output") or "")[: cls.MAX_OUTPUT_CHARS]
        item.actual_model = cls._optional_text(
            result.get("actual_model"), maximum=300
        )
        item.skill_read = (
            None if result.get("skill_read") is None else bool(result.get("skill_read"))
        )
        item.work_manifest = normalize_work_manifest(
            result.get("work_manifest") or []
        )
        assertions = result.get("assertion_results") or []
        if not isinstance(assertions, list):
            raise SkillEvaluationValidationError("Invalid assertion results.")
        item.assertion_results = copy.deepcopy(assertions)
        score = result.get("score")
        item.score = None if score is None else max(0.0, min(float(score), 1.0))
        item.usage = cls._normalize_usage(result.get("usage") or {})
        item.latency_ms = max(0.0, float(result.get("latency_ms") or 0.0))
        item.runtime_run_id = cls._optional_identifier(
            result.get("runtime_run_id")
        )
        item.error_code = cls._optional_text(result.get("error_code"), maximum=120)
        item.error = cls._optional_text(result.get("error"), maximum=500)
        item.updated_at = time.time()

    @staticmethod
    def _attempt_snapshot(item: SkillEvaluationItem) -> dict[str, Any]:
        return {
            "attempt": item.attempts,
            "status": item.status,
            "output": item.output,
            "actual_model": item.actual_model,
            "skill_read": item.skill_read,
            "work_manifest": copy.deepcopy(item.work_manifest),
            "assertion_results": copy.deepcopy(item.assertion_results),
            "score": item.score,
            "usage": copy.deepcopy(item.usage),
            "latency_ms": item.latency_ms,
            "runtime_run_id": item.runtime_run_id,
            "error_code": item.error_code,
            "error": item.error,
            "finished_at": item.updated_at,
        }

    @staticmethod
    def _clear_item_result(item: SkillEvaluationItem) -> None:
        item.status = "pending"
        item.output = ""
        item.actual_model = None
        item.skill_read = None
        item.work_manifest = []
        item.assertion_results = []
        item.score = None
        item.usage = {}
        item.latency_ms = 0.0
        item.runtime_run_id = None
        item.error_code = None
        item.error = None
        item.updated_at = time.time()

    @staticmethod
    def _touch_run(run: SkillEvaluationRun) -> None:
        run.revision += 1
        run.updated_at = time.time()

    @classmethod
    def _normalize_usage(cls, raw: Any) -> dict[str, int]:
        if not isinstance(raw, Mapping):
            raise SkillEvaluationValidationError("Evaluation usage must be an object.")
        result: dict[str, int] = {}
        for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "estimated_tokens"):
            value = int(raw.get(key) or 0)
            if value < 0:
                raise SkillEvaluationValidationError("Evaluation usage cannot be negative.")
            result[key] = value
        return result

    @staticmethod
    def _quarantine_record(
        kind: str,
        record_id: Any,
        record: Any,
        reason_code: str,
        error: BaseException,
    ) -> dict[str, Any]:
        try:
            payload = json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError):
            payload = repr(type(record))
        return {
            "kind": str(kind)[:40],
            "record_id": str(record_id)[:200],
            "record_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "reason_code": str(reason_code)[:80],
            "error": str(error)[:300],
            "quarantined_at": time.time(),
        }

    @classmethod
    def _sanitize_quarantine_entry(
        cls, raw: Any, *, index: int
    ) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return cls._quarantine_record(
                "legacy", f"entry-{index}", raw, "invalid_quarantine", ValueError("invalid quarantine entry")
            )
        digest = str(raw.get("record_digest") or "")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
            digest = hashlib.sha256(
                cls._canonical_json(raw).encode("utf-8")
            ).hexdigest()
        return {
            "kind": str(raw.get("kind") or "legacy")[:40],
            "record_id": str(raw.get("record_id") or f"entry-{index}")[:200],
            "record_digest": digest.lower(),
            "reason_code": str(raw.get("reason_code") or "legacy_quarantine")[:80],
            "error": str(raw.get("error") or "")[:300],
            "quarantined_at": cls._timestamp(raw.get("quarantined_at", time.time())),
        }

    @staticmethod
    def _identifier(value: Any, field_name: str) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > 200 or any(ord(ch) < 32 for ch in clean):
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @classmethod
    def _optional_identifier(cls, value: Any) -> str | None:
        if value is None:
            return None
        return cls._identifier(value, "identifier")

    @staticmethod
    def _required_text(value: Any, field_name: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise SkillEvaluationValidationError(f"{field_name} must be text.")
        clean = value.strip()
        if not clean or len(clean) > maximum:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _optional_text(value: Any, *, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise SkillEvaluationValidationError("Optional value must be text.")
        return value[:maximum]

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        try:
            clean = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.") from exc
        if clean < 1:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _nonnegative_int(value: Any, field_name: str) -> int:
        try:
            clean = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.") from exc
        if clean < 0:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @classmethod
    def _bounded_int(
        cls, value: Any, field_name: str, *, minimum: int, maximum: int
    ) -> int:
        try:
            clean = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.") from exc
        if clean < minimum or clean > maximum:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _digest(value: Any, field_name: str) -> str:
        clean = str(value or "").strip().lower()
        if len(clean) != 64 or any(ch not in "0123456789abcdef" for ch in clean):
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @classmethod
    def _relative_path(cls, value: Any, field_name: str) -> str:
        clean = str(value or "").strip().replace("\\", "/")
        path = PurePosixPath(clean)
        if (
            not clean
            or clean.startswith("/")
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or ":" in path.parts[0]
            or len(clean) > 240
        ):
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return path.as_posix()

    @staticmethod
    def _json_mapping(value: Any, field_name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise SkillEvaluationValidationError(f"{field_name} must be an object.")
        try:
            return json.loads(json.dumps(dict(value), ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise SkillEvaluationValidationError(
                f"{field_name} must contain JSON values."
            ) from exc

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _timestamp(value: Any) -> float:
        clean = float(value)
        if clean < 0:
            raise ValueError("invalid timestamp")
        return clean

    @classmethod
    def _optional_timestamp(cls, value: Any) -> float | None:
        return None if value is None else cls._timestamp(value)


def normalize_work_manifest(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 100:
        raise SkillEvaluationValidationError("Invalid evaluation work manifest.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise SkillEvaluationValidationError("Work manifest entry must be an object.")
        path = SkillEvaluationStore._relative_path(entry.get("path"), "work path")
        if path in seen:
            raise SkillEvaluationValidationError("Work manifest paths must be unique.")
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise SkillEvaluationValidationError("Invalid work file size.") from exc
        if size < 0 or size > 16 * 1024 * 1024:
            raise SkillEvaluationValidationError("Invalid work file size.")
        sha256 = SkillEvaluationStore._digest(entry.get("sha256"), "work sha256")
        preview = entry.get("preview")
        if preview is not None and not isinstance(preview, str):
            raise SkillEvaluationValidationError("Work preview must be UTF-8 text.")
        result.append(
            {
                "path": path,
                "size": size,
                "sha256": sha256,
                "preview": None if preview is None else preview[:2_000],
            }
        )
        seen.add(path)
    result.sort(key=lambda item: item["path"].casefold())
    return result


def normalize_runner_result(
    raw: SkillEvaluationRunnerResult | Mapping[str, Any],
    *,
    max_output_chars: int,
) -> SkillEvaluationRunnerResult:
    if isinstance(raw, SkillEvaluationRunnerResult):
        source = asdict(raw)
    elif isinstance(raw, Mapping):
        source = dict(raw)
    else:
        raise SkillEvaluationValidationError(
            "Skill evaluation runner returned an invalid result.",
            code="skill_evaluation_runner_result_invalid",
        )
    output = source.get("output")
    if not isinstance(output, str):
        raise SkillEvaluationValidationError(
            "Skill evaluation runner output must be text.",
            code="skill_evaluation_runner_result_invalid",
        )
    actual_model = SkillEvaluationStore._required_text(
        source.get("actual_model"), "actual_model", maximum=300
    )
    skill_read = source.get("skill_read")
    if not isinstance(skill_read, bool):
        raise SkillEvaluationValidationError(
            "Skill evaluation runner must report skill_read.",
            code="skill_evaluation_runner_result_invalid",
        )
    return SkillEvaluationRunnerResult(
        output=output[:max_output_chars],
        actual_model=actual_model,
        skill_read=skill_read,
        work_manifest=normalize_work_manifest(source.get("work_manifest") or []),
        usage=SkillEvaluationStore._normalize_usage(source.get("usage") or {}),
        runtime_run_id=SkillEvaluationStore._optional_identifier(
            source.get("runtime_run_id")
        ),
    )


def evaluate_skill_case(
    case: SkillEvaluationCase,
    *,
    output: str,
    work_manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = {str(item["path"]): item for item in work_manifest}
    normalized_output = _normalize_text(output)
    results: list[dict[str, Any]] = []
    for assertion in case.assertions:
        kind = str(assertion["kind"])
        passed = False
        reason = "Assertion failed."
        if kind == "exact_match":
            passed = normalized_output == _normalize_text(str(assertion["value"]))
            reason = "Normalized output matched exactly." if passed else "Normalized output did not match exactly."
        elif kind == "contains":
            passed = _normalize_text(str(assertion["value"])) in normalized_output
            reason = "Output contained the required text." if passed else "Output did not contain the required text."
        elif kind == "not_contains":
            passed = _normalize_text(str(assertion["value"])) not in normalized_output
            reason = "Output excluded the forbidden text." if passed else "Output contained forbidden text."
        elif kind == "json_schema":
            try:
                parsed = json.loads(output)
                Draft202012Validator(assertion["schema"]).validate(parsed)
                passed = True
                reason = "Output satisfied the JSON Schema."
            except Exception as exc:
                reason = f"JSON Schema validation failed: {str(exc)[:300]}"
        elif kind == "file_exists":
            path = str(assertion["path"])
            passed = path in manifest
            reason = f"Work file {'exists' if passed else 'is missing'}: {path}."
        elif kind == "file_sha256":
            path = str(assertion["path"])
            entry = manifest.get(path)
            passed = bool(entry and entry.get("sha256") == assertion["sha256"])
            reason = f"Work file digest {'matched' if passed else 'did not match'}: {path}."
        results.append(
            {
                "kind": kind,
                "passed": passed,
                "score": 1.0 if passed else 0.0,
                "reason": reason[:500],
                **({"path": assertion["path"]} if "path" in assertion else {}),
            }
        )
    score = mean(float(item["score"]) for item in results) if results else None
    return {
        "assertion_results": results,
        "assertion_count": len(results),
        "assertion_passed_count": sum(1 for item in results if item["passed"]),
        "score": None if score is None else round(score, 6),
    }


def aggregate_skill_evaluation_report(run: SkillEvaluationRun) -> dict[str, Any]:
    statuses = Counter(item.status for item in run.items)
    target_summaries: list[dict[str, Any]] = []
    for target in ("baseline", "candidate"):
        items = [item for item in run.items if item.target == target]
        scores = [float(item.score) for item in items if item.score is not None]
        target_summaries.append(
            {
                "target": target,
                "item_count": len(items),
                "completed_count": sum(item.status == "completed" for item in items),
                "failed_count": sum(item.status != "completed" for item in items),
                "score": round(mean(scores), 6) if scores else None,
                "actual_models": sorted(
                    {item.actual_model for item in items if item.actual_model}
                ),
                "skill_read_count": sum(item.skill_read is True for item in items),
                "average_latency_ms": round(
                    mean(item.latency_ms for item in items), 3
                ) if items else 0.0,
                "usage": {
                    key: sum(int(item.usage.get(key) or 0) for item in items)
                    for key in (
                        "model_calls", "tool_calls", "input_tokens",
                        "output_tokens", "estimated_tokens",
                    )
                },
            }
        )
    grouped: dict[str, dict[str, SkillEvaluationItem]] = {}
    for item in run.items:
        grouped.setdefault(item.pair_id, {})[item.target] = item
    pairs: list[dict[str, Any]] = []
    model_mismatch_count = 0
    for pair_id, targets in grouped.items():
        baseline = targets.get("baseline")
        candidate = targets.get("candidate")
        actual_model_match = bool(
            baseline
            and candidate
            and baseline.actual_model
            and baseline.actual_model == candidate.actual_model
        )
        if baseline and candidate and not actual_model_match:
            model_mismatch_count += 1
        comparable = bool(
            baseline
            and candidate
            and baseline.status == "completed"
            and candidate.status == "completed"
            and actual_model_match
        )
        score_delta = None
        if comparable and baseline.score is not None and candidate.score is not None:
            score_delta = round(float(candidate.score) - float(baseline.score), 6)
        pairs.append(
            {
                "pair_id": pair_id,
                "case_id": baseline.case_id if baseline else candidate.case_id,
                "repetition": baseline.repetition if baseline else candidate.repetition,
                "baseline_item_id": baseline.item_id if baseline else None,
                "candidate_item_id": candidate.item_id if candidate else None,
                "baseline_status": baseline.status if baseline else "missing",
                "candidate_status": candidate.status if candidate else "missing",
                "actual_model_match": actual_model_match,
                "comparable": comparable,
                "candidate_skill_read": candidate.skill_read if candidate else None,
                "score_delta": score_delta,
            }
        )
    pairs.sort(key=lambda item: (str(item["case_id"]), int(item["repetition"])))
    assertion_results = [
        assertion
        for item in run.items
        for assertion in item.assertion_results
    ]
    assertion_failed_count = sum(
        not bool(assertion.get("passed")) for assertion in assertion_results
    )
    all_completed = bool(run.items) and all(
        item.status == "completed" for item in run.items
    )
    candidate_read = all(
        item.skill_read is True
        for item in run.items
        if item.target == "candidate"
    )
    return {
        "run_id": run.run_id,
        "item_count": len(run.items),
        "pair_count": len(pairs),
        "status_counts": dict(sorted(statuses.items())),
        "targets": target_summaries,
        "pairs": pairs,
        "model_mismatch_count": model_mismatch_count,
        "skill_not_read_count": statuses.get("skill_not_read", 0),
        "assertion_count": len(assertion_results),
        "assertion_passed_count": len(assertion_results) - assertion_failed_count,
        "assertion_failed_count": assertion_failed_count,
        "eligible_for_accept": all_completed and candidate_read and model_mismatch_count == 0,
        "ranker_or_judge_used": False,
    }


class SkillEvaluationExecutor:
    """Restart-safe paired executor with an injected, isolated target runner."""

    def __init__(
        self,
        store: SkillEvaluationStore,
        *,
        runner: SkillEvaluationRunner | RunnerCallable,
        poll_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.runner = runner
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.store.recover_runs()
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def execute_next(self) -> bool:
        run = await asyncio.to_thread(self.store.claim_next_run)
        if run is None:
            return False
        try:
            await self._execute_run(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self.store.fail_run,
                run.run_id,
                str(exc),
                code=getattr(exc, "code", "evaluation_executor_failed"),
            )
        return True

    async def _loop(self) -> None:
        while not self._stopping:
            if await self.execute_next():
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _execute_run(self, claimed: SkillEvaluationRun) -> None:
        config = claimed.config
        concurrency = max(1, min(int(config.get("max_concurrency") or 2), 4))
        cases = {item.case_id: item for item in claimed.cases}

        async def execute_item(item: SkillEvaluationItem) -> None:
            current = await asyncio.to_thread(self.store.require_run, claimed.run_id)
            if current.cancel_requested:
                await asyncio.to_thread(
                    self.store.record_item_result,
                    claimed.run_id,
                    item.item_id,
                    result={
                        "status": "cancelled",
                        "error_code": "evaluation_cancelled",
                        "error": "Evaluation cancelled.",
                    },
                )
                return
            case = cases[item.case_id]
            overlay = (
                await asyncio.to_thread(self.store.require_overlay, item.overlay_id)
                if item.overlay_id
                else None
            )
            started = time.perf_counter()
            try:
                async with asyncio.timeout(int(config.get("timeout_seconds") or 120)):
                    raw = await self.runner(claimed, item, case, overlay)
                result = normalize_runner_result(
                    raw,
                    max_output_chars=int(config.get("max_output_chars") or 20_000),
                )
                evaluated = evaluate_skill_case(
                    case,
                    output=result.output,
                    work_manifest=result.work_manifest,
                )
                status: SkillEvaluationItemStatus = "completed"
                error_code = error = None
                if item.target == "candidate" and not result.skill_read:
                    status = "skill_not_read"
                    error_code = "skill_not_read"
                    error = "Candidate did not read the frozen Skill overlay."
                payload = {
                    "status": status,
                    "output": result.output,
                    "actual_model": result.actual_model,
                    "skill_read": result.skill_read,
                    "work_manifest": result.work_manifest,
                    "usage": result.usage,
                    "runtime_run_id": result.runtime_run_id,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_code": error_code,
                    "error": error,
                    **evaluated,
                }
            except Exception as exc:
                payload = {
                    "status": "failed",
                    "output": "",
                    "actual_model": None,
                    "skill_read": None,
                    "work_manifest": [],
                    "assertion_results": [],
                    "score": None,
                    "usage": {},
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_code": str(getattr(exc, "code", "runner_failed"))[:120],
                    "error": str(exc)[:500],
                }
            await asyncio.to_thread(
                self.store.record_item_result,
                claimed.run_id,
                item.item_id,
                result=payload,
            )

        async def execute_pair(pair: list[SkillEvaluationItem]) -> None:
            # Stable baseline-then-candidate order avoids cross-side state leakage in
            # adapters while each side still receives an isolated workspace.
            for item in sorted(pair, key=lambda row: 0 if row.target == "baseline" else 1):
                await execute_item(item)

        while True:
            current = await asyncio.to_thread(self.store.require_run, claimed.run_id)
            if current.cancel_requested:
                break
            pairs = await asyncio.to_thread(
                self.store.claim_pairs,
                claimed.run_id,
                limit_pairs=concurrency,
            )
            if not pairs:
                break
            await asyncio.gather(*(execute_pair(pair) for pair in pairs))

        current = await asyncio.to_thread(self.store.require_run, claimed.run_id)
        if current.cancel_requested:
            # Running items can only exist when cancellation raced a target call.
            for item in current.items:
                if item.status == "running":
                    await asyncio.to_thread(
                        self.store.record_item_result,
                        claimed.run_id,
                        item.item_id,
                        result={
                            "status": "cancelled",
                            "error_code": "evaluation_cancelled",
                            "error": "Evaluation cancelled.",
                        },
                    )
            current = await asyncio.to_thread(self.store.require_run, claimed.run_id)
        report = aggregate_skill_evaluation_report(current)
        await asyncio.to_thread(
            self.store.complete_run,
            claimed.run_id,
            report,
        )


def _normalize_text(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


__all__ = [
    "SkillEvaluationAssertionKind",
    "SkillEvaluationCase",
    "SkillEvaluationCaseSet",
    "SkillEvaluationConflictError",
    "SkillEvaluationError",
    "SkillEvaluationExecutor",
    "SkillEvaluationItem",
    "SkillEvaluationNotFoundError",
    "SkillEvaluationOverlay",
    "SkillEvaluationQualityMode",
    "SkillEvaluationReview",
    "SkillEvaluationRunner",
    "SkillEvaluationRunnerResult",
    "SkillEvaluationRun",
    "SkillEvaluationStateError",
    "SkillEvaluationStorageError",
    "SkillEvaluationStore",
    "SkillEvaluationValidationError",
    "aggregate_skill_evaluation_report",
    "evaluate_skill_case",
    "normalize_runner_result",
    "normalize_work_manifest",
]
