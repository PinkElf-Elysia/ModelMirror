from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Literal

from .application_receipts import (
    SkillApplicationReceiptStore,
    SkillApplicationReceiptStorageError,
    SkillApplicationReceiptV1,
)
from .creator_evidence import (
    CreatorEvidenceError,
    CreatorEvidencePreview,
    build_creator_evidence_preview,
)
from .package_validation import scan_skill_package_credentials

try:
    from server.xpert_runtime.execution_store import WorkflowExecution, WorkflowExecutionStore
    from server.xperts.context import XpertContextStore
except ModuleNotFoundError:
    from xpert_runtime.execution_store import WorkflowExecution, WorkflowExecutionStore
    from xperts.context import XpertContextStore


EXPERIENCE_CANDIDATE_VERSION = "skill-experience-candidate-v1"
EXPERIENCE_STORE_SCHEMA_VERSION = 1
ExperienceSourceKind = Literal["workflow_classic", "xpert_chat"]
ExperienceCandidateState = Literal[
    "captured",
    "analyzing",
    "awaiting_review",
    "promotion_ready",
    "promoted",
    "dismissed",
    "failed",
    "stale",
    "archived",
]
ExperienceAnalysisStatus = Literal["running", "succeeded", "manual_required"]
ExperienceBriefSuggestion = Literal["create", "update", "no_skill"]
ExperienceBriefSource = Literal["model", "manual", "user"]
ExperienceDecisionKind = Literal["create", "update", "dismiss"]

_STATES = {
    "captured",
    "analyzing",
    "awaiting_review",
    "promotion_ready",
    "promoted",
    "dismissed",
    "failed",
    "stale",
    "archived",
}
_SOURCE_KINDS = {"workflow_classic", "xpert_chat"}
_EVIDENCE_KINDS = {
    "intent_summary",
    "successful_steps",
    "tool_names",
    "user_correction",
    "io_shape",
    "final_output_excerpt",
}
_APPLICATION_METHODS = {
    "prompt_injected",
    "skill_read",
    "skill_stage",
    "hook_execute",
}
_APPLICATION_STATUSES = {"selected", "applied", "failed"}
_COMPLIANCE_STATUSES = {"verified", "incomplete", "unverified"}
_ANALYSIS_STATUSES = {"running", "succeeded", "manual_required"}
_BRIEF_SUGGESTIONS = {"create", "update", "no_skill"}
_BRIEF_SOURCES = {"model", "manual", "user"}
_DECISION_KINDS = {"create", "update", "dismiss"}
_NO_SKILL_REASONS = {
    "one_off_task",
    "preference_or_environment_fact",
    "insufficient_evidence",
    "already_covered",
    "cannot_generalize",
}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,239}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_RECORDS = 2_000
_MAX_SELECTED_EVIDENCE = 6
_MAX_RECEIPT_REFERENCES = 64
_MAX_BRIEF_EXAMPLES = 6
_MAX_BRIEF_LIST_ITEMS = 12
_MAX_OVERLAPS = 168
_MAX_OVERLAP_CASES = 7
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


class SkillExperienceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "skill_experience_source_invalid",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class SkillExperienceNotFoundError(SkillExperienceError):
    pass


class SkillExperienceConflictError(SkillExperienceError):
    pass


class SkillExperienceStorageError(SkillExperienceError):
    pass


@dataclass(frozen=True, slots=True)
class SkillExperienceEvidenceV1:
    evidence_id: str
    kind: str
    title: str
    summary: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class SkillExperienceReceiptReferenceV1:
    receipt_id: str
    receipt_revision: int
    receipt_version: str
    contract_fingerprint: str
    skill_id: str
    source_kind: str
    version_id: str | None
    content_digest: str | None
    trust_fingerprint: str | None
    methods: tuple[str, ...]
    application_status: str
    compliance_status: str
    resource_manifest_digest: str | None


@dataclass(frozen=True, slots=True)
class SkillExperienceAnalysisAttemptV1:
    attempt_id: str
    analysis_key: str
    base_revision: int
    base_digest: str
    status: ExperienceAnalysisStatus
    executor_mode: Literal["model", "manual", "trusted_handoff"]
    error_code: str | None
    started_at: float
    finished_at: float | None = None


@dataclass(frozen=True, slots=True)
class DistilledSkillBriefV1:
    version: str
    revision: int
    digest: str
    suggestion: ExperienceBriefSuggestion
    recommendation_reason: str
    no_skill_reason: str | None
    intent: str
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    expected_output: str
    success_criteria: tuple[str, ...]
    reusable_steps: tuple[str, ...]
    failure_boundaries: tuple[str, ...]
    resource_clues: tuple[str, ...]
    overfitting_risk: str
    source: ExperienceBriefSource
    complete: bool

    def serialize(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SkillExperienceOverlapRankV1:
    case_hash: str
    rank: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillExperienceOverlapV1:
    candidate_id: str
    candidate_fingerprint: str
    name: str
    source_type: str
    source_kind: str
    installed_skill_id: str | None
    creator_draft_id: str | None
    update_target_eligible: bool
    best_rank: int
    major_overlap: bool
    case_ranks: tuple[SkillExperienceOverlapRankV1, ...]


@dataclass(frozen=True, slots=True)
class SkillExperienceDecisionV1:
    decision: ExperienceDecisionKind
    target_skill_id: str | None
    target_draft_id: str | None
    override_reason: str | None
    new_boundary: str | None
    actor_kind: Literal["local_console"]
    decided_at: float


@dataclass(frozen=True, slots=True)
class SkillExperiencePromotionV1:
    session_id: str
    route: str
    decision: Literal["create", "update"]
    target_skill_id: str | None
    target_draft_id: str | None
    baseline_version_id: str | None
    baseline_content_digest: str | None
    promoted_at: float


@dataclass(frozen=True, slots=True)
class SkillExperienceCandidateV1:
    candidate_id: str
    version: str
    revision: int
    digest: str
    state: ExperienceCandidateState
    source_kind: ExperienceSourceKind
    source_task_id: str
    source_run_id: str
    source_xpert_id: str | None
    source_conversation_id: str | None
    source_message_id: str | None
    execution_revision: int
    execution_digest: str
    evidence_preview_fingerprint: str
    selected_evidence: tuple[SkillExperienceEvidenceV1, ...]
    application_receipts: tuple[SkillExperienceReceiptReferenceV1, ...]
    analysis_attempt: SkillExperienceAnalysisAttemptV1 | None = None
    brief: DistilledSkillBriefV1 | None = None
    overlaps: tuple[SkillExperienceOverlapV1, ...] = ()
    overlap_fingerprint: str | None = None
    decision: SkillExperienceDecisionV1 | None = None
    promotion: SkillExperiencePromotionV1 | None = None
    dismissal_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def serialize(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SkillExperienceSource:
    source_kind: ExperienceSourceKind
    source_task_id: str
    source_run_id: str
    source_xpert_id: str | None = None
    source_conversation_id: str | None = None
    source_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class SkillExperienceCapture:
    execution: WorkflowExecution
    source: SkillExperienceSource
    preview: CreatorEvidencePreview
    execution_digest: str
    application_receipts: tuple[SkillExperienceReceiptReferenceV1, ...]


def experience_promotion_enabled() -> bool:
    return os.getenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class SkillExperienceCandidateStore:
    """Atomic candidate snapshot with record quarantine and fail-closed corruption."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        configured = os.getenv("SKILL_EXPERIENCE_STORAGE_DIR", "").strip()
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or configured or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_experience_candidates.json"
        self._lock = threading.RLock()
        self._items: dict[str, SkillExperienceCandidateV1] = {}
        self._source_index: dict[str, str] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": EXPERIENCE_CANDIDATE_VERSION,
                "enabled": experience_promotion_enabled(),
                "available": self._load_error is None,
                "candidate_count": len(self._items),
                "quarantine_count": len(self._quarantine),
                "error_code": (
                    "skill_experience_store_unavailable" if self._load_error else None
                ),
            }

    def create_or_get(self, capture: SkillExperienceCapture) -> SkillExperienceCandidateV1:
        source_key = _source_key(capture.source.source_kind, capture.source.source_task_id)
        with self._lock:
            self._ensure_readable_unlocked()
            existing_id = self._source_index.get(source_key)
            if existing_id:
                existing = self._items[existing_id]
                return self._reconcile_capture_unlocked(existing, capture)
            if len(self._items) >= _MAX_RECORDS:
                raise SkillExperienceStorageError(
                    "Skill experience candidate capacity has been reached.",
                    code="skill_experience_store_unavailable",
                )
            now = time.time()
            candidate_id = f"skillexperience_{uuid.uuid4().hex}"
            candidate = SkillExperienceCandidateV1(
                candidate_id=candidate_id,
                version=EXPERIENCE_CANDIDATE_VERSION,
                revision=1,
                digest="",
                state="captured",
                source_kind=capture.source.source_kind,
                source_task_id=capture.source.source_task_id,
                source_run_id=capture.source.source_run_id,
                source_xpert_id=capture.source.source_xpert_id,
                source_conversation_id=capture.source.source_conversation_id,
                source_message_id=capture.source.source_message_id,
                execution_revision=int(capture.execution.revision),
                execution_digest=capture.execution_digest,
                evidence_preview_fingerprint=capture.preview.preview_fingerprint,
                selected_evidence=(),
                application_receipts=capture.application_receipts,
                created_at=now,
                updated_at=now,
            )
            candidate = replace(candidate, digest=_candidate_digest(candidate))
            self._items[candidate_id] = candidate
            self._source_index[source_key] = candidate_id
            try:
                self._save_unlocked()
            except Exception:
                self._items.pop(candidate_id, None)
                self._source_index.pop(source_key, None)
                raise
            return copy.deepcopy(candidate)

    def get(self, candidate_id: str) -> SkillExperienceCandidateV1 | None:
        clean_id = _identifier(candidate_id, "candidate_id")
        with self._lock:
            self._ensure_readable_unlocked()
            item = self._items.get(clean_id)
            return copy.deepcopy(item) if item else None

    def require(self, candidate_id: str) -> SkillExperienceCandidateV1:
        item = self.get(candidate_id)
        if item is None:
            raise SkillExperienceNotFoundError(
                "Skill experience candidate was not found.",
                code="skill_experience_source_invalid",
            )
        return item

    def list_candidates(self, *, limit: int = 200) -> list[SkillExperienceCandidateV1]:
        with self._lock:
            self._ensure_readable_unlocked()
            items = sorted(
                self._items.values(),
                key=lambda item: (item.updated_at, item.candidate_id),
                reverse=True,
            )
            return copy.deepcopy(items[: max(1, min(int(limit), 500))])

    def select_evidence(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        capture: SkillExperienceCapture,
        evidence: Iterable[SkillExperienceEvidenceV1],
    ) -> SkillExperienceCandidateV1:
        selected = tuple(evidence)
        if len(selected) > _MAX_SELECTED_EVIDENCE:
            raise SkillExperienceError(
                "Too many evidence items were selected.",
                code="skill_experience_source_invalid",
            )
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            self._assert_expected(current, expected_revision, expected_digest)
            self._assert_same_source_identity(current, capture)
            if current.state not in {"captured", "stale"}:
                raise SkillExperienceConflictError(
                    "Skill experience candidate is no longer accepting evidence.",
                    code="skill_experience_candidate_conflict",
                )
            return self._replace_unlocked(
                current,
                state="captured",
                source_run_id=capture.source.source_run_id,
                execution_revision=int(capture.execution.revision),
                execution_digest=capture.execution_digest,
                evidence_preview_fingerprint=capture.preview.preview_fingerprint,
                application_receipts=capture.application_receipts,
                selected_evidence=selected,
                analysis_attempt=None,
                brief=None,
                overlaps=(),
                overlap_fingerprint=None,
                decision=None,
            )

    def refresh_capture(
        self,
        candidate_id: str,
        *,
        capture: SkillExperienceCapture,
    ) -> SkillExperienceCandidateV1:
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            return self._reconcile_capture_unlocked(current, capture)

    def mark_stale(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> SkillExperienceCandidateV1:
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            self._assert_expected(current, expected_revision, expected_digest)
            if current.state == "stale" or current.state in {"dismissed", "promoted", "archived"}:
                return copy.deepcopy(current)
            return self._replace_unlocked(current, state="stale")

    def dismiss(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        reason: str,
    ) -> SkillExperienceCandidateV1:
        clean_reason = " ".join(str(reason or "").split()) or None
        if clean_reason is not None:
            if len(clean_reason) > 1_000:
                raise SkillExperienceError(
                    "Dismissal reason is too long.",
                    code="skill_experience_source_invalid",
                )
            _reject_credentials(clean_reason)
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            self._assert_expected(current, expected_revision, expected_digest)
            if current.state in {"promoted", "archived"}:
                raise SkillExperienceConflictError(
                    "Skill experience candidate cannot be dismissed in its current state.",
                    code="skill_experience_candidate_conflict",
                )
            if current.state == "dismissed" and current.dismissal_reason == clean_reason:
                return copy.deepcopy(current)
            return self._replace_unlocked(
                current,
                state="dismissed",
                dismissal_reason=clean_reason,
            )

    def begin_analysis(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        analysis_key: str,
    ) -> tuple[SkillExperienceCandidateV1, bool]:
        clean_key = _digest(analysis_key, "analysis_key")
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            attempt = current.analysis_attempt
            if attempt is not None and attempt.analysis_key == clean_key:
                supplied_revision = _positive_int(expected_revision, "expected_revision")
                supplied_digest = _digest(expected_digest, "expected_digest")
                is_current = (
                    supplied_revision == current.revision
                    and supplied_digest == current.digest
                )
                is_original_retry = (
                    supplied_revision == attempt.base_revision
                    and supplied_digest == attempt.base_digest
                )
                if not (is_current or is_original_retry):
                    self._assert_expected(current, expected_revision, expected_digest)
                return copy.deepcopy(current), attempt.status == "running"
            self._assert_expected(current, expected_revision, expected_digest)
            if current.state != "captured" or not current.selected_evidence:
                raise SkillExperienceConflictError(
                    "Select current evidence before analyzing this experience.",
                    code="skill_experience_decision_required",
                )
            now = time.time()
            attempt = SkillExperienceAnalysisAttemptV1(
                attempt_id=f"skillexperienceanalysis_{uuid.uuid4().hex}",
                analysis_key=clean_key,
                base_revision=current.revision,
                base_digest=current.digest,
                status="running",
                executor_mode="model",
                error_code=None,
                started_at=now,
            )
            updated = self._replace_unlocked(
                current,
                state="analyzing",
                analysis_attempt=attempt,
                brief=None,
                overlaps=(),
                overlap_fingerprint=None,
                decision=None,
                dismissal_reason=None,
            )
            return updated, True

    def complete_analysis(
        self,
        candidate_id: str,
        *,
        attempt_id: str,
        analysis_key: str,
        brief: DistilledSkillBriefV1,
        overlaps: Iterable[SkillExperienceOverlapV1],
        overlap_fingerprint: str,
        executor_mode: Literal["model", "manual"],
        error_code: str | None = None,
    ) -> SkillExperienceCandidateV1:
        clean_attempt_id = _identifier(attempt_id, "attempt_id")
        clean_key = _digest(analysis_key, "analysis_key")
        clean_fingerprint = _digest(overlap_fingerprint, "overlap_fingerprint")
        clean_error = (
            _identifier(error_code, "analysis_error_code") if error_code else None
        )
        if (executor_mode == "model" and clean_error is not None) or (
            executor_mode == "manual" and clean_error is None
        ):
            raise SkillExperienceError(
                "Skill experience analysis outcome is inconsistent.",
                code="skill_experience_analysis_invalid",
            )
        overlap_items = tuple(overlaps)
        _validate_brief_instance(brief)
        _validate_overlaps(overlap_items)
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            attempt = current.analysis_attempt
            if (
                attempt is None
                or attempt.attempt_id != clean_attempt_id
                or attempt.analysis_key != clean_key
            ):
                raise SkillExperienceConflictError(
                    "Skill experience analysis changed before completion.",
                    code="skill_experience_candidate_conflict",
                )
            if attempt.status != "running":
                return copy.deepcopy(current)
            if current.state != "analyzing":
                raise SkillExperienceConflictError(
                    "Skill experience analysis can no longer be completed.",
                    code="skill_experience_promotion_stale",
                )
            completed_attempt = replace(
                attempt,
                status=("succeeded" if executor_mode == "model" else "manual_required"),
                executor_mode=executor_mode,
                error_code=clean_error,
                finished_at=time.time(),
            )
            return self._replace_unlocked(
                current,
                state="awaiting_review",
                analysis_attempt=completed_attempt,
                brief=brief,
                overlaps=overlap_items,
                overlap_fingerprint=clean_fingerprint,
            )

    def update_brief(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        brief: DistilledSkillBriefV1,
        overlaps: Iterable[SkillExperienceOverlapV1],
        overlap_fingerprint: str,
    ) -> SkillExperienceCandidateV1:
        overlap_items = tuple(overlaps)
        clean_fingerprint = _digest(overlap_fingerprint, "overlap_fingerprint")
        _validate_brief_instance(brief)
        _validate_overlaps(overlap_items)
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            self._assert_expected(current, expected_revision, expected_digest)
            if current.state != "awaiting_review" or current.brief is None:
                raise SkillExperienceConflictError(
                    "Skill experience brief is not editable in its current state.",
                    code="skill_experience_candidate_conflict",
                )
            if brief.revision != current.brief.revision + 1:
                raise SkillExperienceConflictError(
                    "Skill experience brief revision is stale.",
                    code="skill_experience_candidate_conflict",
                )
            return self._replace_unlocked(
                current,
                brief=brief,
                overlaps=overlap_items,
                overlap_fingerprint=clean_fingerprint,
                decision=None,
            )

    def decide(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        decision: SkillExperienceDecisionV1,
    ) -> SkillExperienceCandidateV1:
        _validate_decision(decision)
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            self._assert_expected(current, expected_revision, expected_digest)
            if (
                current.state != "awaiting_review"
                or current.brief is None
                or not current.brief.complete
            ):
                raise SkillExperienceConflictError(
                    "Complete and review the Skill brief before deciding.",
                    code="skill_experience_decision_required",
                )
            next_state: ExperienceCandidateState = (
                "dismissed" if decision.decision == "dismiss" else "promotion_ready"
            )
            return self._replace_unlocked(
                current,
                state=next_state,
                decision=decision,
                dismissal_reason=(
                    decision.override_reason
                    if decision.decision == "dismiss"
                    else None
                ),
            )

    def prepare_trusted_handoff(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        brief: DistilledSkillBriefV1,
    ) -> SkillExperienceCandidateV1:
        """Freeze a no-model Creator middleware brief for explicit handoff.

        The workflow author already chose the Creator middleware, so its trusted
        taskInput can become a manually sourced brief. This method never accepts
        client-supplied source or target IDs and does not call a provider.
        """

        _validate_brief_instance(brief)
        if not brief.complete:
            raise SkillExperienceError(
                "Trusted Creator handoff brief is incomplete.",
                code="skill_experience_decision_required",
            )
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            self._assert_expected(current, expected_revision, expected_digest)
            if current.state == "promotion_ready":
                if current.brief == brief and current.decision is not None:
                    return copy.deepcopy(current)
                raise SkillExperienceConflictError(
                    "Skill experience already has another reviewed decision.",
                    code="skill_experience_candidate_conflict",
                )
            if current.state != "captured" or not current.selected_evidence:
                raise SkillExperienceConflictError(
                    "Skill experience is not ready for a trusted Creator handoff.",
                    code="skill_experience_candidate_conflict",
                )
            now = time.time()
            analysis_key = _sha256(
                {
                    "candidate_id": current.candidate_id,
                    "brief_digest": brief.digest,
                    "mode": "trusted_creator_handoff",
                }
            )
            attempt = SkillExperienceAnalysisAttemptV1(
                attempt_id=f"skillexperienceanalysis_{uuid.uuid4().hex}",
                analysis_key=analysis_key,
                base_revision=current.revision,
                base_digest=current.digest,
                status="succeeded",
                executor_mode="trusted_handoff",
                error_code=None,
                started_at=now,
                finished_at=now,
            )
            overlap_fingerprint = _sha256(
                {
                    "candidate_id": current.candidate_id,
                    "brief_digest": brief.digest,
                    "overlaps": [],
                }
            )
            decision = SkillExperienceDecisionV1(
                decision="create",
                target_skill_id=None,
                target_draft_id=None,
                override_reason=None,
                new_boundary=None,
                actor_kind="local_console",
                decided_at=now,
            )
            return self._replace_unlocked(
                current,
                state="promotion_ready",
                analysis_attempt=attempt,
                brief=brief,
                overlaps=(),
                overlap_fingerprint=overlap_fingerprint,
                decision=decision,
            )

    def mark_promoted(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        promotion: SkillExperiencePromotionV1,
    ) -> SkillExperienceCandidateV1:
        _validate_promotion(promotion)
        with self._lock:
            self._ensure_readable_unlocked()
            current = self._require_unlocked(candidate_id)
            if current.state == "promoted" and current.promotion is not None:
                if _promotion_identity(current.promotion) == _promotion_identity(
                    promotion
                ):
                    return copy.deepcopy(current)
                raise SkillExperienceConflictError(
                    "Skill experience is already bound to another Creator promotion.",
                    code="skill_experience_candidate_conflict",
                )
            self._assert_expected(current, expected_revision, expected_digest)
            if (
                current.state != "promotion_ready"
                or current.decision is None
                or current.decision.decision != promotion.decision
                or current.decision.target_skill_id != promotion.target_skill_id
                or current.decision.target_draft_id != promotion.target_draft_id
            ):
                raise SkillExperienceConflictError(
                    "Skill experience promotion no longer matches the reviewed decision.",
                    code="skill_experience_promotion_stale",
                )
            return self._replace_unlocked(
                current,
                state="promoted",
                promotion=promotion,
            )

    def _replace_unlocked(
        self,
        current: SkillExperienceCandidateV1,
        **changes: Any,
    ) -> SkillExperienceCandidateV1:
        updated = replace(
            current,
            **changes,
            revision=current.revision + 1,
            digest="",
            updated_at=time.time(),
        )
        updated = replace(updated, digest=_candidate_digest(updated))
        self._items[updated.candidate_id] = updated
        try:
            self._save_unlocked()
        except Exception:
            self._items[current.candidate_id] = current
            raise
        return copy.deepcopy(updated)

    @staticmethod
    def _assert_expected(
        current: SkillExperienceCandidateV1,
        expected_revision: int,
        expected_digest: str,
    ) -> None:
        if (
            current.revision != _positive_int(expected_revision, "expected_revision")
            or current.digest != _digest(expected_digest, "expected_digest")
        ):
            raise SkillExperienceConflictError(
                "Skill experience candidate changed. Reload before continuing.",
                code="skill_experience_candidate_conflict",
                details={"current_revision": current.revision, "current_digest": current.digest},
            )

    @staticmethod
    def _assert_same_source_identity(
        current: SkillExperienceCandidateV1,
        capture: SkillExperienceCapture,
    ) -> None:
        expected_scope = (
            capture.source.source_kind,
            capture.source.source_task_id,
            capture.source.source_xpert_id,
            capture.source.source_conversation_id,
            capture.source.source_message_id,
        )
        current_scope = (
            current.source_kind,
            current.source_task_id,
            current.source_xpert_id,
            current.source_conversation_id,
            current.source_message_id,
        )
        trusted_run_ids = {
            capture.execution.run_id,
            *capture.execution.previous_run_ids,
        }
        if (
            current_scope != expected_scope
            or current.source_run_id not in trusted_run_ids
        ):
            raise SkillExperienceConflictError(
                "The trusted source binding changed.",
                code="skill_experience_candidate_conflict",
            )

    @staticmethod
    def _capture_matches(
        current: SkillExperienceCandidateV1,
        capture: SkillExperienceCapture,
    ) -> bool:
        return not (
            current.source_run_id != capture.source.source_run_id
            or current.execution_revision != capture.execution.revision
            or current.execution_digest != capture.execution_digest
            or current.evidence_preview_fingerprint != capture.preview.preview_fingerprint
            or current.application_receipts != capture.application_receipts
        )

    def _reconcile_capture_unlocked(
        self,
        current: SkillExperienceCandidateV1,
        capture: SkillExperienceCapture,
    ) -> SkillExperienceCandidateV1:
        self._assert_same_source_identity(current, capture)
        if self._capture_matches(current, capture):
            if current.state == "stale":
                return self._replace_unlocked(current, state="captured")
            return copy.deepcopy(current)
        if current.state not in {"captured", "stale"}:
            return copy.deepcopy(current)
        if current.selected_evidence:
            if current.state == "stale":
                return copy.deepcopy(current)
            return self._replace_unlocked(current, state="stale")
        return self._replace_unlocked(
            current,
            state="captured",
            source_run_id=capture.source.source_run_id,
            execution_revision=int(capture.execution.revision),
            execution_digest=capture.execution_digest,
            evidence_preview_fingerprint=capture.preview.preview_fingerprint,
            application_receipts=capture.application_receipts,
        )

    def _require_unlocked(self, candidate_id: str) -> SkillExperienceCandidateV1:
        clean_id = _identifier(candidate_id, "candidate_id")
        item = self._items.get(clean_id)
        if item is None:
            raise SkillExperienceNotFoundError(
                "Skill experience candidate was not found.",
                code="skill_experience_source_invalid",
            )
        return item

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillExperienceStorageError(
                "Skill experience storage is unavailable.",
                code="skill_experience_store_unavailable",
            )

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw_bytes = self.snapshot_path.read_bytes()
            if len(raw_bytes) > _MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot too large")
            payload = json.loads(raw_bytes.decode("utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != EXPERIENCE_STORE_SCHEMA_VERSION
                or payload.get("version") != EXPERIENCE_CANDIDATE_VERSION
                or not isinstance(payload.get("candidates"), list)
            ):
                raise ValueError("invalid snapshot")
            quarantine = payload.get("quarantine") or []
            if not isinstance(quarantine, list):
                raise ValueError("invalid quarantine")
            items: dict[str, SkillExperienceCandidateV1] = {}
            source_index: dict[str, str] = {}
            safe_quarantine = [
                decoded
                for item in quarantine[-200:]
                if (decoded := _decode_quarantine_item(item)) is not None
            ]
            for raw in payload["candidates"]:
                try:
                    item = _decode_candidate(raw)
                    normalized_digest = _candidate_digest(replace(item, digest=""))
                    if item.digest != normalized_digest:
                        item = replace(item, digest=normalized_digest)
                    key = _source_key(item.source_kind, item.source_task_id)
                    if item.candidate_id in items or key in source_index:
                        raise ValueError("duplicate candidate")
                    items[item.candidate_id] = item
                    source_index[key] = item.candidate_id
                except Exception:
                    encoded = json.dumps(
                        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8", errors="replace")
                    safe_quarantine.append(
                        {
                            "code": "skill_experience_record_invalid",
                            "sha256": hashlib.sha256(encoded).hexdigest(),
                            "size_bytes": len(encoded),
                        }
                    )
            self._items = items
            self._source_index = source_index
            self._quarantine = safe_quarantine[-200:]
        except Exception as exc:
            self._load_error = f"skill_experience_store_corrupt:{type(exc).__name__}"

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": EXPERIENCE_STORE_SCHEMA_VERSION,
            "version": EXPERIENCE_CANDIDATE_VERSION,
            "candidates": [
                item.serialize()
                for item in sorted(self._items.values(), key=lambda value: value.candidate_id)
            ],
            "quarantine": list(self._quarantine[-200:]),
        }
        content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(content) > _MAX_SNAPSHOT_BYTES:
            raise SkillExperienceStorageError(
                "Skill experience storage reached its bounded capacity.",
                code="skill_experience_store_unavailable",
            )
        temporary = self.snapshot_path.with_name(
            f".{self.snapshot_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.snapshot_path)
        except SkillExperienceError:
            raise
        except Exception as exc:
            raise SkillExperienceStorageError(
                "Skill experience storage could not be updated.",
                code="skill_experience_store_unavailable",
            ) from exc
        finally:
            if temporary.exists():
                temporary.unlink()


class SkillExperienceService:
    def __init__(
        self,
        store: SkillExperienceCandidateStore,
        execution_store: WorkflowExecutionStore,
        context_store: XpertContextStore,
        application_receipt_store: SkillApplicationReceiptStore,
    ) -> None:
        self.store = store
        self.execution_store = execution_store
        self.context_store = context_store
        self.application_receipt_store = application_receipt_store

    @property
    def enabled(self) -> bool:
        return experience_promotion_enabled()

    def status(self) -> dict[str, Any]:
        return {
            **self.store.status(),
            "supported_sources": ["workflow_classic", "xpert_chat"],
            "evidence_version": "creator-evidence-v1",
            "model_calls_enabled": False,
        }

    def require_enabled(self) -> None:
        if not self.enabled:
            raise SkillExperienceError(
                "Skill experience promotion is disabled.",
                code="skill_experience_disabled",
            )

    def create_or_get(self, source: SkillExperienceSource) -> tuple[SkillExperienceCandidateV1, CreatorEvidencePreview]:
        self.require_enabled()
        capture = self._capture(source)
        candidate = self.store.create_or_get(capture)
        return candidate, capture.preview

    def get_candidate(
        self, candidate_id: str
    ) -> tuple[SkillExperienceCandidateV1, CreatorEvidencePreview | None]:
        self.require_enabled()
        candidate = self.store.require(candidate_id)
        if candidate.state not in {"captured", "stale"}:
            return candidate, None
        try:
            capture = self._capture(_source_from_candidate(candidate))
        except SkillExperienceStorageError:
            raise
        except SkillExperienceError:
            stale = self.store.mark_stale(
                candidate.candidate_id,
                expected_revision=candidate.revision,
                expected_digest=candidate.digest,
            )
            return stale, None
        refreshed = self.store.refresh_capture(candidate.candidate_id, capture=capture)
        return refreshed, capture.preview

    def list_candidates(self, *, limit: int = 200) -> list[SkillExperienceCandidateV1]:
        self.require_enabled()
        return self.store.list_candidates(limit=limit)

    def require_current_candidate(
        self, candidate_id: str
    ) -> SkillExperienceCandidateV1:
        """Revalidate the trusted source before any analysis or decision write."""

        self.require_enabled()
        current = self.store.require(candidate_id)
        try:
            capture = self._capture(_source_from_candidate(current))
        except SkillExperienceStorageError:
            raise
        except SkillExperienceError as exc:
            if current.state not in {"dismissed", "promoted", "archived", "stale"}:
                self.store.mark_stale(
                    current.candidate_id,
                    expected_revision=current.revision,
                    expected_digest=current.digest,
                )
            raise SkillExperienceConflictError(
                "The trusted experience source is no longer current.",
                code="skill_experience_promotion_stale",
            ) from exc
        if not self.store._capture_matches(current, capture):
            if current.state not in {"dismissed", "promoted", "archived", "stale"}:
                self.store.mark_stale(
                    current.candidate_id,
                    expected_revision=current.revision,
                    expected_digest=current.digest,
                )
            raise SkillExperienceConflictError(
                "The trusted experience source changed. Reload the evidence before continuing.",
                code="skill_experience_promotion_stale",
            )
        return current

    def select_evidence(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        preview_fingerprint: str,
        evidence_ids: Iterable[str],
    ) -> SkillExperienceCandidateV1:
        self.require_enabled()
        current = self.store.require(candidate_id)
        capture = self._capture(_source_from_candidate(current))
        if capture.preview.preview_fingerprint != _digest(
            preview_fingerprint, "preview_fingerprint"
        ):
            raise SkillExperienceConflictError(
                "The evidence preview changed. Reload before continuing.",
                code="skill_experience_evidence_stale",
            )
        by_id = {item.candidate_id: item for item in capture.preview.candidates}
        requested = list(dict.fromkeys(_identifier(item, "evidence_id") for item in evidence_ids))
        if any(item not in by_id for item in requested):
            raise SkillExperienceConflictError(
                "The evidence selection contains an unknown or stale item.",
                code="skill_experience_evidence_stale",
            )
        evidence = tuple(
            SkillExperienceEvidenceV1(
                evidence_id=by_id[item].candidate_id,
                kind=by_id[item].kind,
                title=by_id[item].title,
                summary=by_id[item].summary,
                content_hash=by_id[item].content_hash,
            )
            for item in requested
        )
        for item in evidence:
            _reject_credentials(item.summary)
        return self.store.select_evidence(
            candidate_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            capture=capture,
            evidence=evidence,
        )

    def dismiss(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        reason: str,
    ) -> SkillExperienceCandidateV1:
        self.require_enabled()
        return self.store.dismiss(
            candidate_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            reason=reason,
        )

    def _capture(self, requested: SkillExperienceSource) -> SkillExperienceCapture:
        source = _normalize_source(requested)
        execution = self.execution_store.get(source.source_task_id)
        if execution is None:
            raise SkillExperienceError(
                "The source execution was not found.",
                code="skill_experience_source_invalid",
            )
        _reject_ineligible_execution(execution)
        trusted_run_ids = {execution.run_id, *execution.previous_run_ids}
        if source.source_run_id not in trusted_run_ids:
            raise SkillExperienceError(
                "The source run does not belong to the trusted execution history.",
                code="skill_experience_source_invalid",
            )
        # task_id is the stable runtime identity. A resumed execution may acquire a
        # new run_id; only a server-recorded run in that recovery chain may rebind.
        rebound = replace(source, source_run_id=_identifier(execution.run_id, "source_run_id"))
        try:
            preview = build_creator_evidence_preview(
                self.execution_store,
                source_kind=rebound.source_kind,
                source_task_id=rebound.source_task_id,
                source_run_id=rebound.source_run_id,
                context_store=self.context_store,
                source_xpert_id=rebound.source_xpert_id,
                source_conversation_id=rebound.source_conversation_id,
                source_message_id=rebound.source_message_id,
            )
        except CreatorEvidenceError as exc:
            code = (
                "skill_experience_source_not_completed"
                if exc.code == "source_not_completed"
                else "skill_experience_source_invalid"
            )
            raise SkillExperienceError(str(exc), code=code) from exc
        receipts = self._receipt_references(execution)
        execution_digest = _sha256(
            {
                "source_kind": rebound.source_kind,
                "source_task_id": rebound.source_task_id,
                "source_run_id": rebound.source_run_id,
                "run_type": execution.run_type,
                "status": execution.status,
                "revision": execution.revision,
                "completed_at": execution.completed_at,
                "preview_fingerprint": preview.preview_fingerprint,
            }
        )
        return SkillExperienceCapture(
            execution=copy.deepcopy(execution),
            source=rebound,
            preview=preview,
            execution_digest=execution_digest,
            application_receipts=receipts,
        )

    def _receipt_references(
        self, execution: WorkflowExecution
    ) -> tuple[SkillExperienceReceiptReferenceV1, ...]:
        try:
            receipts = self.application_receipt_store.list_receipts(task_id=execution.task_id)
        except SkillApplicationReceiptStorageError as exc:
            raise SkillExperienceStorageError(
                "Skill application evidence is unavailable.",
                code="skill_experience_store_unavailable",
            ) from exc
        trusted_run_ids = {execution.run_id, *execution.previous_run_ids}
        matching = sorted(
            (item for item in receipts if item.run_id in trusted_run_ids),
            key=lambda item: item.receipt_id,
        )
        if len(matching) > _MAX_RECEIPT_REFERENCES:
            raise SkillExperienceError(
                "The source has too many Skill application receipts.",
                code="skill_experience_source_invalid",
            )
        return tuple(_receipt_reference(item) for item in matching)


def _normalize_source(source: SkillExperienceSource) -> SkillExperienceSource:
    kind = str(source.source_kind or "").strip()
    if kind not in _SOURCE_KINDS:
        raise SkillExperienceError(
            "Only completed classic Workflow and private Xpert Chat runs are supported.",
            code="skill_experience_source_invalid",
        )
    task_id = _identifier(source.source_task_id, "source_task_id")
    run_id = _identifier(source.source_run_id, "source_run_id")
    if kind == "workflow_classic":
        if any(
            (
                source.source_xpert_id,
                source.source_conversation_id,
                source.source_message_id,
            )
        ):
            raise SkillExperienceError(
                "Classic Workflow evidence cannot include Xpert message scope.",
                code="skill_experience_source_invalid",
            )
        return SkillExperienceSource(
            source_kind="workflow_classic",
            source_task_id=task_id,
            source_run_id=run_id,
        )
    return SkillExperienceSource(
        source_kind="xpert_chat",
        source_task_id=task_id,
        source_run_id=run_id,
        source_xpert_id=_identifier(source.source_xpert_id, "source_xpert_id"),
        source_conversation_id=_identifier(
            source.source_conversation_id, "source_conversation_id"
        ),
        source_message_id=_identifier(source.source_message_id, "source_message_id"),
    )


def _reject_ineligible_execution(execution: WorkflowExecution) -> None:
    metadata = execution.runtime_metadata
    excluded_markers = {
        "app_id",
        "agent_task_id",
        "handoff_id",
        "goal_id",
        "evaluation_run_id",
        "skill_evaluation_run_id",
        "creator_session_id",
        "experience_analysis_key",
        "experience_phase",
        "automation_execution_id",
        "external_xpert_source_id",
    }
    if any(metadata.get(key) for key in excluded_markers):
        raise SkillExperienceError(
            "This execution type cannot become a Skill experience candidate.",
            code="skill_experience_source_invalid",
        )


def _source_from_candidate(candidate: SkillExperienceCandidateV1) -> SkillExperienceSource:
    return SkillExperienceSource(
        source_kind=candidate.source_kind,
        source_task_id=candidate.source_task_id,
        source_run_id=candidate.source_run_id,
        source_xpert_id=candidate.source_xpert_id,
        source_conversation_id=candidate.source_conversation_id,
        source_message_id=candidate.source_message_id,
    )


def _receipt_reference(
    receipt: SkillApplicationReceiptV1,
) -> SkillExperienceReceiptReferenceV1:
    return SkillExperienceReceiptReferenceV1(
        receipt_id=receipt.receipt_id,
        receipt_revision=receipt.revision,
        receipt_version=receipt.version,
        contract_fingerprint=receipt.contract_fingerprint,
        skill_id=receipt.skill_id,
        source_kind=receipt.source_kind,
        version_id=receipt.version_id,
        content_digest=receipt.content_digest,
        trust_fingerprint=receipt.trust_fingerprint,
        methods=tuple(receipt.methods),
        application_status=receipt.application_status,
        compliance_status=receipt.compliance_status,
        resource_manifest_digest=receipt.resource_manifest_digest,
    )


def _decode_candidate(raw: Any) -> SkillExperienceCandidateV1:
    if not isinstance(raw, dict) or raw.get("version") != EXPERIENCE_CANDIDATE_VERSION:
        raise ValueError("invalid candidate")
    allowed_fields = {
        "candidate_id",
        "version",
        "revision",
        "digest",
        "state",
        "source_kind",
        "source_task_id",
        "source_run_id",
        "source_xpert_id",
        "source_conversation_id",
        "source_message_id",
        "execution_revision",
        "execution_digest",
        "evidence_preview_fingerprint",
        "selected_evidence",
        "application_receipts",
        "analysis_attempt",
        "brief",
        "overlaps",
        "overlap_fingerprint",
        "decision",
        "promotion",
        "dismissal_reason",
        "created_at",
        "updated_at",
    }
    if set(raw) - allowed_fields:
        raise ValueError("candidate contains unknown fields")
    evidence_raw = raw.get("selected_evidence") or []
    receipts_raw = raw.get("application_receipts") or []
    overlaps_raw = raw.get("overlaps") or []
    if (
        not isinstance(evidence_raw, list)
        or not isinstance(receipts_raw, list)
        or not isinstance(overlaps_raw, list)
    ):
        raise ValueError("invalid candidate children")
    if (
        len(evidence_raw) > _MAX_SELECTED_EVIDENCE
        or len(receipts_raw) > _MAX_RECEIPT_REFERENCES
        or len(overlaps_raw) > _MAX_OVERLAPS
    ):
        raise ValueError("candidate exceeds limits")
    state = str(raw.get("state") or "")
    source_kind = str(raw.get("source_kind") or "")
    if state not in _STATES or source_kind not in _SOURCE_KINDS:
        raise ValueError("invalid candidate state")
    evidence = tuple(
        SkillExperienceEvidenceV1(
            evidence_id=_identifier(item.get("evidence_id"), "evidence_id"),
            kind=_identifier(item.get("kind"), "evidence_kind"),
            title=_bounded_text(item.get("title"), "evidence_title", 200),
            summary=_bounded_text(item.get("summary"), "evidence_summary", 4_000),
            content_hash=_digest(item.get("content_hash"), "content_hash"),
        )
        for item in evidence_raw
        if isinstance(item, dict)
    )
    if len(evidence) != len(evidence_raw):
        raise ValueError("invalid evidence")
    if (
        any(item.kind not in _EVIDENCE_KINDS for item in evidence)
        or len({item.evidence_id for item in evidence}) != len(evidence)
        or len({item.kind for item in evidence}) != len(evidence)
    ):
        raise ValueError("invalid evidence identity")
    for item in evidence:
        if item.content_hash != _sha256({"kind": item.kind, "summary": item.summary}):
            raise ValueError("invalid evidence content hash")
        _reject_credentials(item.title)
        _reject_credentials(item.summary)
    receipts = tuple(_decode_receipt_reference(item) for item in receipts_raw)
    if len({item.receipt_id for item in receipts}) != len(receipts):
        raise ValueError("duplicate receipt reference")
    analysis_attempt = (
        _decode_analysis_attempt(raw.get("analysis_attempt"))
        if raw.get("analysis_attempt") is not None
        else None
    )
    brief = _decode_brief(raw.get("brief")) if raw.get("brief") is not None else None
    overlaps = tuple(_decode_overlap(item) for item in overlaps_raw)
    _validate_overlaps(overlaps)
    overlap_fingerprint = _optional_digest(
        raw.get("overlap_fingerprint"), "overlap_fingerprint"
    )
    decision = (
        _decode_decision(raw.get("decision"))
        if raw.get("decision") is not None
        else None
    )
    promotion = (
        _decode_promotion(raw.get("promotion"))
        if raw.get("promotion") is not None
        else None
    )
    candidate = SkillExperienceCandidateV1(
        candidate_id=_identifier(raw.get("candidate_id"), "candidate_id"),
        version=EXPERIENCE_CANDIDATE_VERSION,
        revision=_positive_int(raw.get("revision"), "revision"),
        digest=_digest(raw.get("digest"), "digest"),
        state=state,  # type: ignore[arg-type]
        source_kind=source_kind,  # type: ignore[arg-type]
        source_task_id=_identifier(raw.get("source_task_id"), "source_task_id"),
        source_run_id=_identifier(raw.get("source_run_id"), "source_run_id"),
        source_xpert_id=_optional_identifier(raw.get("source_xpert_id"), "source_xpert_id"),
        source_conversation_id=_optional_identifier(
            raw.get("source_conversation_id"), "source_conversation_id"
        ),
        source_message_id=_optional_identifier(raw.get("source_message_id"), "source_message_id"),
        execution_revision=_positive_int(raw.get("execution_revision"), "execution_revision"),
        execution_digest=_digest(raw.get("execution_digest"), "execution_digest"),
        evidence_preview_fingerprint=_digest(
            raw.get("evidence_preview_fingerprint"), "evidence_preview_fingerprint"
        ),
        selected_evidence=evidence,
        application_receipts=receipts,
        analysis_attempt=analysis_attempt,
        brief=brief,
        overlaps=overlaps,
        overlap_fingerprint=overlap_fingerprint,
        decision=decision,
        promotion=promotion,
        dismissal_reason=(
            _bounded_text(raw.get("dismissal_reason"), "dismissal_reason", 1_000)
            if raw.get("dismissal_reason") is not None
            else None
        ),
        created_at=float(raw.get("created_at") or 0),
        updated_at=float(raw.get("updated_at") or 0),
    )
    if candidate.source_kind == "workflow_classic":
        if any(
            (
                candidate.source_xpert_id,
                candidate.source_conversation_id,
                candidate.source_message_id,
            )
        ):
            raise ValueError("workflow candidate has Xpert scope")
    elif not all(
        (
            candidate.source_xpert_id,
            candidate.source_conversation_id,
            candidate.source_message_id,
        )
    ):
        raise ValueError("Xpert candidate scope is incomplete")
    if (
        not math.isfinite(candidate.created_at)
        or not math.isfinite(candidate.updated_at)
        or candidate.created_at <= 0
        or candidate.updated_at < candidate.created_at
    ):
        raise ValueError("invalid candidate timestamps")
    if candidate.dismissal_reason is not None:
        _reject_credentials(candidate.dismissal_reason)
    if candidate.overlaps and not candidate.overlap_fingerprint:
        raise ValueError("overlap fingerprint is incomplete")
    if candidate.overlap_fingerprint and candidate.brief is None:
        raise ValueError("overlap fingerprint has no brief")
    if candidate.state == "analyzing" and (
        candidate.analysis_attempt is None
        or candidate.analysis_attempt.status != "running"
        or candidate.brief is not None
    ):
        raise ValueError("invalid analyzing candidate")
    if candidate.state == "captured" and any(
        (
            candidate.analysis_attempt,
            candidate.brief,
            candidate.overlaps,
            candidate.overlap_fingerprint,
            candidate.decision,
            candidate.promotion,
        )
    ):
        raise ValueError("captured candidate contains analysis state")
    if candidate.state in {"awaiting_review", "promotion_ready"} and (
        candidate.analysis_attempt is None
        or candidate.analysis_attempt.status not in {"succeeded", "manual_required"}
        or candidate.brief is None
        or candidate.overlap_fingerprint is None
    ):
        raise ValueError("invalid reviewed candidate")
    if candidate.state == "promotion_ready" and (
        candidate.decision is None
        or candidate.decision.decision not in {"create", "update"}
        or not candidate.brief
        or not candidate.brief.complete
    ):
        raise ValueError("invalid promotion-ready candidate")
    if candidate.state == "promoted" and (
        candidate.promotion is None
        or candidate.decision is None
        or candidate.promotion.decision != candidate.decision.decision
        or candidate.promotion.target_skill_id != candidate.decision.target_skill_id
        or candidate.promotion.target_draft_id != candidate.decision.target_draft_id
    ):
        raise ValueError("invalid promoted candidate")
    if candidate.promotion is not None and candidate.state != "promoted":
        raise ValueError("candidate promotion is in an invalid state")
    if candidate.decision is not None and candidate.state not in {
        "promotion_ready",
        "promoted",
        "dismissed",
        "stale",
        "archived",
    }:
        raise ValueError("candidate decision is in an invalid state")
    digest_payload = dict(raw)
    digest_payload.pop("digest", None)
    if candidate.digest != _sha256(digest_payload):
        raise ValueError("candidate digest mismatch")
    return candidate


def build_distilled_skill_brief(
    payload: dict[str, Any],
    *,
    revision: int,
    source: ExperienceBriefSource,
    allow_incomplete: bool = False,
) -> DistilledSkillBriefV1:
    if not isinstance(payload, dict):
        raise SkillExperienceError(
            "Skill experience brief must be an object.",
            code="skill_experience_analysis_invalid",
        )
    suggestion = str(payload.get("suggestion") or "").strip()
    if suggestion not in _BRIEF_SUGGESTIONS:
        raise SkillExperienceError(
            "Skill experience brief has an invalid suggestion.",
            code="skill_experience_analysis_invalid",
        )
    if source not in _BRIEF_SOURCES:
        raise SkillExperienceError(
            "Skill experience brief source is invalid.",
            code="skill_experience_analysis_invalid",
        )
    no_skill_reason = str(payload.get("no_skill_reason") or "").strip() or None
    if suggestion == "no_skill":
        if no_skill_reason not in _NO_SKILL_REASONS:
            if not allow_incomplete:
                raise SkillExperienceError(
                    "A no-Skill recommendation requires a fixed reason.",
                    code="skill_experience_analysis_invalid",
                )
            no_skill_reason = None
    elif no_skill_reason is not None:
        raise SkillExperienceError(
            "Only a no-Skill recommendation may include a no-Skill reason.",
            code="skill_experience_analysis_invalid",
        )
    intent = _optional_bounded_text(payload.get("intent"), "brief_intent", 2_000)
    recommendation_reason = _optional_bounded_text(
        payload.get("recommendation_reason"), "recommendation_reason", 2_000
    )
    expected_output = _optional_bounded_text(
        payload.get("expected_output"), "expected_output", 4_000
    )
    overfitting_risk = _optional_bounded_text(
        payload.get("overfitting_risk"), "overfitting_risk", 2_000
    )
    positive_examples = _brief_text_list(
        payload.get("positive_examples"),
        "positive_examples",
        maximum=_MAX_BRIEF_EXAMPLES,
        item_limit=1_200,
    )
    negative_examples = _brief_text_list(
        payload.get("negative_examples"),
        "negative_examples",
        maximum=_MAX_BRIEF_EXAMPLES,
        item_limit=1_200,
    )
    success_criteria = _brief_text_list(
        payload.get("success_criteria"),
        "success_criteria",
        maximum=_MAX_BRIEF_LIST_ITEMS,
        item_limit=1_000,
    )
    reusable_steps = _brief_text_list(
        payload.get("reusable_steps"),
        "reusable_steps",
        maximum=_MAX_BRIEF_LIST_ITEMS,
        item_limit=1_000,
    )
    failure_boundaries = _brief_text_list(
        payload.get("failure_boundaries"),
        "failure_boundaries",
        maximum=_MAX_BRIEF_LIST_ITEMS,
        item_limit=1_000,
    )
    resource_clues = _brief_text_list(
        payload.get("resource_clues"),
        "resource_clues",
        maximum=_MAX_BRIEF_LIST_ITEMS,
        item_limit=1_000,
    )
    if set(_normalized_unique(positive_examples)) & set(
        _normalized_unique(negative_examples)
    ):
        raise SkillExperienceError(
            "Positive and negative examples must not overlap.",
            code="skill_experience_analysis_invalid",
        )
    promotion_ready = bool(
        intent
        and recommendation_reason
        and expected_output
        and overfitting_risk
        and 2 <= len(positive_examples) <= _MAX_BRIEF_EXAMPLES
        and 2 <= len(negative_examples) <= _MAX_BRIEF_EXAMPLES
        and success_criteria
        and reusable_steps
        and failure_boundaries
    )
    complete = (
        bool(no_skill_reason in _NO_SKILL_REASONS)
        if suggestion == "no_skill"
        else promotion_ready
    )
    if not allow_incomplete and not complete:
        raise SkillExperienceError(
            "Skill experience analysis did not produce a complete brief.",
            code="skill_experience_analysis_invalid",
        )
    values = [
        intent,
        recommendation_reason,
        expected_output,
        overfitting_risk,
        *positive_examples,
        *negative_examples,
        *success_criteria,
        *reusable_steps,
        *failure_boundaries,
        *resource_clues,
    ]
    for value in values:
        if value:
            _reject_credentials(value)
            _reject_keyword_stuffing(value)
    if sum(len(value) for value in values) > 48_000:
        raise SkillExperienceError(
            "Skill experience brief is too large.",
            code="skill_experience_analysis_invalid",
        )
    brief = DistilledSkillBriefV1(
        version="distilled-skill-brief-v1",
        revision=_positive_int(revision, "brief_revision"),
        digest="",
        suggestion=suggestion,  # type: ignore[arg-type]
        recommendation_reason=recommendation_reason,
        no_skill_reason=no_skill_reason,
        intent=intent,
        positive_examples=positive_examples,
        negative_examples=negative_examples,
        expected_output=expected_output,
        success_criteria=success_criteria,
        reusable_steps=reusable_steps,
        failure_boundaries=failure_boundaries,
        resource_clues=resource_clues,
        overfitting_risk=overfitting_risk,
        source=source,
        complete=complete,
    )
    return replace(brief, digest=_brief_digest(brief))


def distilled_skill_brief_is_promotion_ready(brief: DistilledSkillBriefV1) -> bool:
    return bool(
        brief.intent
        and brief.recommendation_reason
        and brief.expected_output
        and brief.overfitting_risk
        and 2 <= len(brief.positive_examples) <= _MAX_BRIEF_EXAMPLES
        and 2 <= len(brief.negative_examples) <= _MAX_BRIEF_EXAMPLES
        and brief.success_criteria
        and brief.reusable_steps
        and brief.failure_boundaries
    )


def build_manual_distilled_skill_brief(
    candidate: SkillExperienceCandidateV1,
) -> DistilledSkillBriefV1:
    summaries = [item.summary[:1_200] for item in candidate.selected_evidence]
    intent_item = next(
        (item.summary for item in candidate.selected_evidence if item.kind == "intent_summary"),
        summaries[0] if summaries else "请补充这次运行中值得复用的目标。",
    )
    intent_item = intent_item[:2_000]
    steps = [
        item.summary
        for item in candidate.selected_evidence
        if item.kind in {"successful_steps", "tool_names", "user_correction"}
    ][:3]
    steps = [item[:1_000] for item in steps]
    return build_distilled_skill_brief(
        {
            "suggestion": "create",
            "recommendation_reason": "请检查证据并决定是否值得沉淀为 Skill。",
            "no_skill_reason": None,
            "intent": intent_item,
            "positive_examples": summaries[:1],
            "negative_examples": [],
            "expected_output": "请补充这个 Skill 应稳定交付的结果。",
            "success_criteria": [],
            "reusable_steps": steps,
            "failure_boundaries": [],
            "resource_clues": [],
            "overfitting_risk": "请说明哪些内容只适用于这一次运行。",
        },
        revision=1,
        source="manual",
        allow_incomplete=True,
    )


def _decode_analysis_attempt(raw: Any) -> SkillExperienceAnalysisAttemptV1:
    if not isinstance(raw, dict) or set(raw) != {
        "attempt_id",
        "analysis_key",
        "base_revision",
        "base_digest",
        "status",
        "executor_mode",
        "error_code",
        "started_at",
        "finished_at",
    }:
        raise ValueError("invalid analysis attempt")
    status = str(raw.get("status") or "")
    executor_mode = str(raw.get("executor_mode") or "")
    if status not in _ANALYSIS_STATUSES or executor_mode not in {
        "model",
        "manual",
        "trusted_handoff",
    }:
        raise ValueError("invalid analysis attempt state")
    started_at = float(raw.get("started_at") or 0)
    finished_at = (
        float(raw["finished_at"]) if raw.get("finished_at") is not None else None
    )
    if (
        not math.isfinite(started_at)
        or started_at <= 0
        or (finished_at is not None and (not math.isfinite(finished_at) or finished_at < started_at))
        or (status == "running" and finished_at is not None)
        or (status != "running" and finished_at is None)
    ):
        raise ValueError("invalid analysis attempt timestamps")
    if (
        (status == "succeeded" and executor_mode not in {"model", "trusted_handoff"})
        or (status == "manual_required" and executor_mode != "manual")
        or (status == "succeeded" and raw.get("error_code") is not None)
        or (status == "manual_required" and raw.get("error_code") is None)
    ):
        raise ValueError("invalid analysis attempt outcome")
    return SkillExperienceAnalysisAttemptV1(
        attempt_id=_identifier(raw.get("attempt_id"), "attempt_id"),
        analysis_key=_digest(raw.get("analysis_key"), "analysis_key"),
        base_revision=_positive_int(raw.get("base_revision"), "base_revision"),
        base_digest=_digest(raw.get("base_digest"), "base_digest"),
        status=status,  # type: ignore[arg-type]
        executor_mode=executor_mode,  # type: ignore[arg-type]
        error_code=_optional_identifier(raw.get("error_code"), "analysis_error_code"),
        started_at=started_at,
        finished_at=finished_at,
    )


def _decode_brief(raw: Any) -> DistilledSkillBriefV1:
    if not isinstance(raw, dict) or set(raw) != {
        "version",
        "revision",
        "digest",
        "suggestion",
        "recommendation_reason",
        "no_skill_reason",
        "intent",
        "positive_examples",
        "negative_examples",
        "expected_output",
        "success_criteria",
        "reusable_steps",
        "failure_boundaries",
        "resource_clues",
        "overfitting_risk",
        "source",
        "complete",
    }:
        raise ValueError("invalid distilled brief")
    if raw.get("version") != "distilled-skill-brief-v1":
        raise ValueError("invalid distilled brief version")
    source = str(raw.get("source") or "")
    if source not in _BRIEF_SOURCES:
        raise ValueError("invalid distilled brief source")
    decoded = build_distilled_skill_brief(
        raw,
        revision=_positive_int(raw.get("revision"), "brief_revision"),
        source=source,  # type: ignore[arg-type]
        allow_incomplete=True,
    )
    if (
        raw.get("complete") is not decoded.complete
        or _digest(raw.get("digest"), "brief_digest") != decoded.digest
    ):
        raise ValueError("distilled brief digest mismatch")
    return decoded


def _decode_overlap(raw: Any) -> SkillExperienceOverlapV1:
    if not isinstance(raw, dict) or set(raw) != {
        "candidate_id",
        "candidate_fingerprint",
        "name",
        "source_type",
        "source_kind",
        "installed_skill_id",
        "creator_draft_id",
        "update_target_eligible",
        "best_rank",
        "major_overlap",
        "case_ranks",
    }:
        raise ValueError("invalid overlap")
    ranks_raw = raw.get("case_ranks")
    if not isinstance(ranks_raw, (list, tuple)) or not 1 <= len(ranks_raw) <= _MAX_OVERLAP_CASES:
        raise ValueError("invalid overlap ranks")
    if not isinstance(raw.get("update_target_eligible"), bool) or not isinstance(
        raw.get("major_overlap"), bool
    ):
        raise ValueError("invalid overlap flags")
    ranks = tuple(_decode_overlap_rank(item) for item in ranks_raw)
    overlap = SkillExperienceOverlapV1(
        candidate_id=_identifier(raw.get("candidate_id"), "overlap_candidate_id"),
        candidate_fingerprint=_digest(
            raw.get("candidate_fingerprint"), "candidate_fingerprint"
        ),
        name=_bounded_text(raw.get("name"), "overlap_name", 200),
        source_type=_identifier(raw.get("source_type"), "overlap_source_type"),
        source_kind=_identifier(raw.get("source_kind"), "overlap_source_kind"),
        installed_skill_id=_optional_identifier(
            raw.get("installed_skill_id"), "installed_skill_id"
        ),
        creator_draft_id=_optional_identifier(
            raw.get("creator_draft_id"), "creator_draft_id"
        ),
        update_target_eligible=bool(raw.get("update_target_eligible")),
        best_rank=_positive_int(raw.get("best_rank"), "best_rank"),
        major_overlap=bool(raw.get("major_overlap")),
        case_ranks=ranks,
    )
    if overlap.best_rank != min(item.rank for item in ranks):
        raise ValueError("invalid overlap best rank")
    if overlap.major_overlap is not any(item.rank <= 6 for item in ranks):
        raise ValueError("invalid overlap major flag")
    if overlap.update_target_eligible and not (
        overlap.installed_skill_id and overlap.creator_draft_id
    ):
        raise ValueError("invalid update target overlap")
    return overlap


def _decode_overlap_rank(raw: Any) -> SkillExperienceOverlapRankV1:
    if not isinstance(raw, dict) or set(raw) != {"case_hash", "rank", "reasons"}:
        raise ValueError("invalid overlap rank")
    reasons = raw.get("reasons")
    if (
        not isinstance(reasons, (list, tuple))
        or len(reasons) > 6
        or any(not isinstance(item, str) for item in reasons)
    ):
        raise ValueError("invalid overlap reasons")
    return SkillExperienceOverlapRankV1(
        case_hash=_digest(raw.get("case_hash"), "case_hash"),
        rank=_positive_int(raw.get("rank"), "rank"),
        reasons=tuple(_bounded_text(item, "overlap_reason", 120) for item in reasons),
    )


def _decode_decision(raw: Any) -> SkillExperienceDecisionV1:
    if not isinstance(raw, dict) or set(raw) != {
        "decision",
        "target_skill_id",
        "target_draft_id",
        "override_reason",
        "new_boundary",
        "actor_kind",
        "decided_at",
    }:
        raise ValueError("invalid experience decision")
    decided_at = float(raw.get("decided_at") or 0)
    if not math.isfinite(decided_at) or decided_at <= 0:
        raise ValueError("invalid decision timestamp")
    decision = SkillExperienceDecisionV1(
        decision=str(raw.get("decision") or ""),  # type: ignore[arg-type]
        target_skill_id=_optional_identifier(raw.get("target_skill_id"), "target_skill_id"),
        target_draft_id=_optional_identifier(raw.get("target_draft_id"), "target_draft_id"),
        override_reason=(
            _bounded_text(raw.get("override_reason"), "override_reason", 2_000)
            if raw.get("override_reason") is not None
            else None
        ),
        new_boundary=(
            _bounded_text(raw.get("new_boundary"), "new_boundary", 2_000)
            if raw.get("new_boundary") is not None
            else None
        ),
        actor_kind=str(raw.get("actor_kind") or ""),  # type: ignore[arg-type]
        decided_at=decided_at,
    )
    _validate_decision(decision)
    return decision


def _decode_promotion(raw: Any) -> SkillExperiencePromotionV1:
    if not isinstance(raw, dict) or set(raw) != {
        "session_id",
        "route",
        "decision",
        "target_skill_id",
        "target_draft_id",
        "baseline_version_id",
        "baseline_content_digest",
        "promoted_at",
    }:
        raise ValueError("invalid experience promotion")
    promoted_at = float(raw.get("promoted_at") or 0)
    promotion = SkillExperiencePromotionV1(
        session_id=_identifier(raw.get("session_id"), "session_id"),
        route=_bounded_text(raw.get("route"), "promotion_route", 500),
        decision=str(raw.get("decision") or ""),  # type: ignore[arg-type]
        target_skill_id=_optional_identifier(raw.get("target_skill_id"), "target_skill_id"),
        target_draft_id=_optional_identifier(raw.get("target_draft_id"), "target_draft_id"),
        baseline_version_id=_optional_identifier(
            raw.get("baseline_version_id"), "baseline_version_id"
        ),
        baseline_content_digest=_optional_digest(
            raw.get("baseline_content_digest"), "baseline_content_digest"
        ),
        promoted_at=promoted_at,
    )
    _validate_promotion(promotion)
    return promotion


def _validate_promotion(promotion: SkillExperiencePromotionV1) -> None:
    if promotion.decision not in {"create", "update"}:
        raise SkillExperienceError(
            "Skill experience promotion is invalid.",
            code="skill_experience_promotion_stale",
        )
    route_base = f"/skills/create/{promotion.session_id}"
    if promotion.route != route_base and not promotion.route.startswith(
        f"{route_base}?"
    ):
        raise SkillExperienceError(
            "Skill experience promotion route is invalid.",
            code="skill_experience_promotion_stale",
        )
    if not math.isfinite(promotion.promoted_at) or promotion.promoted_at <= 0:
        raise SkillExperienceError(
            "Skill experience promotion timestamp is invalid.",
            code="skill_experience_promotion_stale",
        )
    update_fields = (
        promotion.target_skill_id,
        promotion.target_draft_id,
        promotion.baseline_version_id,
        promotion.baseline_content_digest,
    )
    if promotion.decision == "update":
        if not all(update_fields):
            raise SkillExperienceError(
                "Skill experience update promotion is incomplete.",
                code="skill_experience_promotion_stale",
            )
    elif any(update_fields):
        raise SkillExperienceError(
            "Skill experience create promotion cannot target an installed Skill.",
            code="skill_experience_promotion_stale",
        )


def _promotion_identity(promotion: SkillExperiencePromotionV1) -> tuple[Any, ...]:
    return (
        promotion.session_id,
        promotion.route,
        promotion.decision,
        promotion.target_skill_id,
        promotion.target_draft_id,
        promotion.baseline_version_id,
        promotion.baseline_content_digest,
    )


def _validate_brief_instance(brief: DistilledSkillBriefV1) -> None:
    if not isinstance(brief, DistilledSkillBriefV1):
        raise SkillExperienceError(
            "Skill experience brief is invalid.", code="skill_experience_analysis_invalid"
        )
    decoded = build_distilled_skill_brief(
        brief.serialize(),
        revision=brief.revision,
        source=brief.source,
        allow_incomplete=True,
    )
    if decoded != brief:
        raise SkillExperienceError(
            "Skill experience brief digest is invalid.",
            code="skill_experience_analysis_invalid",
        )


def _validate_overlaps(overlaps: tuple[SkillExperienceOverlapV1, ...]) -> None:
    if len(overlaps) > _MAX_OVERLAPS:
        raise SkillExperienceError(
            "Skill experience overlap result is too large.",
            code="skill_experience_analysis_invalid",
        )
    if len({item.candidate_id for item in overlaps}) != len(overlaps):
        raise SkillExperienceError(
            "Skill experience overlap result contains duplicates.",
            code="skill_experience_analysis_invalid",
        )
    for item in overlaps:
        decoded = _decode_overlap(asdict(item))
        if decoded != item:
            raise SkillExperienceError(
                "Skill experience overlap result is invalid.",
                code="skill_experience_analysis_invalid",
            )


def _validate_decision(decision: SkillExperienceDecisionV1) -> None:
    if decision.decision not in _DECISION_KINDS or decision.actor_kind != "local_console":
        raise SkillExperienceError(
            "Skill experience decision is invalid.",
            code="skill_experience_decision_required",
        )
    if decision.decision == "update":
        if not decision.target_skill_id or not decision.target_draft_id:
            raise SkillExperienceError(
                "An update decision requires a verified Creator Skill target.",
                code="skill_experience_update_target_invalid",
            )
    elif decision.target_skill_id is not None or decision.target_draft_id is not None:
        raise SkillExperienceError(
            "Only an update decision may include a target Skill.",
            code="skill_experience_update_target_invalid",
        )
    for value in (decision.override_reason, decision.new_boundary):
        if value:
            _reject_credentials(value)


def _brief_digest(brief: DistilledSkillBriefV1) -> str:
    payload = brief.serialize()
    payload.pop("digest", None)
    return _sha256(payload)


def _brief_text_list(
    value: Any,
    field_name: str,
    *,
    maximum: int,
    item_limit: int,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_analysis_invalid"
        )
    items = tuple(_bounded_text(item, field_name, item_limit) for item in value)
    normalized = _normalized_unique(items)
    if len(set(normalized)) != len(items):
        raise SkillExperienceError(
            f"Duplicate {field_name}.", code="skill_experience_analysis_invalid"
        )
    return items


def _normalized_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(" ".join(str(item).casefold().split()) for item in values)


def _decode_receipt_reference(raw: Any) -> SkillExperienceReceiptReferenceV1:
    if not isinstance(raw, dict):
        raise ValueError("invalid receipt reference")
    methods = raw.get("methods") or []
    if (
        not isinstance(methods, list)
        or len(methods) > 4
        or len(set(methods)) != len(methods)
        or any(item not in _APPLICATION_METHODS for item in methods)
    ):
        raise ValueError("invalid receipt methods")
    application_status = str(raw.get("application_status") or "")
    compliance_status = str(raw.get("compliance_status") or "")
    if application_status not in _APPLICATION_STATUSES:
        raise ValueError("invalid application status")
    if compliance_status not in _COMPLIANCE_STATUSES:
        raise ValueError("invalid compliance status")
    reference = SkillExperienceReceiptReferenceV1(
        receipt_id=_identifier(raw.get("receipt_id"), "receipt_id"),
        receipt_revision=_positive_int(raw.get("receipt_revision"), "receipt_revision"),
        receipt_version=_identifier(raw.get("receipt_version"), "receipt_version"),
        contract_fingerprint=_digest(raw.get("contract_fingerprint"), "contract_fingerprint"),
        skill_id=_identifier(raw.get("skill_id"), "skill_id"),
        source_kind=_identifier(raw.get("source_kind"), "receipt_source_kind"),
        version_id=_optional_identifier(raw.get("version_id"), "version_id"),
        content_digest=_optional_digest(raw.get("content_digest"), "content_digest"),
        trust_fingerprint=_optional_digest(raw.get("trust_fingerprint"), "trust_fingerprint"),
        methods=tuple(_identifier(item, "application_method") for item in methods),
        application_status=application_status,
        compliance_status=compliance_status,
        resource_manifest_digest=_optional_digest(
            raw.get("resource_manifest_digest"), "resource_manifest_digest"
        ),
    )
    if reference.receipt_version not in {
        "skill-application-receipt-v1",
        "skill-application-receipt-v2",
    }:
        raise ValueError("invalid receipt version")
    return reference


def _decode_quarantine_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "")
    digest = str(raw.get("sha256") or "").lower()
    size = raw.get("size_bytes")
    if (
        code != "skill_experience_record_invalid"
        or not _DIGEST_RE.fullmatch(digest)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or size > _MAX_SNAPSHOT_BYTES
    ):
        return None
    return {"code": code, "sha256": digest, "size_bytes": size}


def _candidate_digest(candidate: SkillExperienceCandidateV1) -> str:
    payload = candidate.serialize()
    payload.pop("digest", None)
    return _sha256(payload)


def _source_key(source_kind: str, task_id: str) -> str:
    return f"{source_kind}:{task_id}"


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_source_invalid"
        )
    return text


def _optional_identifier(value: Any, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _identifier(value, field_name)


def _digest(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_source_invalid"
        )
    return text


def _optional_digest(value: Any, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _digest(value, field_name)


def _positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_source_invalid"
        ) from exc
    if number < 1:
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_source_invalid"
        )
    return number


def _bounded_text(value: Any, field_name: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_chars:
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_source_invalid"
        )
    return text


def _optional_bounded_text(value: Any, field_name: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        raise SkillExperienceError(
            f"Invalid {field_name}.", code="skill_experience_analysis_invalid"
        )
    return text


def _reject_credentials(value: str) -> None:
    if scan_skill_package_credentials(skill_markdown=value):
        raise SkillExperienceError(
            "Sensitive credential material cannot be stored as Skill experience data.",
            code="skill_experience_source_invalid",
        )


def _reject_keyword_stuffing(value: str) -> None:
    tokens = re.findall(r"[A-Za-z0-9+#.]{2,}|[\u3400-\u9fff]{2,}", value.casefold())
    if len(tokens) < 8:
        return
    most_common = max(tokens.count(token) for token in set(tokens))
    if most_common >= 8 and most_common / len(tokens) >= 0.5:
        raise SkillExperienceError(
            "Skill experience brief contains repeated keyword stuffing.",
            code="skill_experience_analysis_invalid",
        )
