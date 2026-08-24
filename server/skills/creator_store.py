from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .package_validation import scan_skill_package_credentials


CREATOR_ASSISTANT_AGENT_ID = "skill-creator-assistant-v1"
CreatorMode = Literal["blank", "run"]
CreatorSourceKind = Literal["blank", "xpert_chat", "workflow_classic"]
CreatorQualityMode = Literal["objective", "subjective"]
CreatorAuthoringFlow = Literal["legacy", "resource"]
CreatorReviewState = Literal["none", "pending", "accepted", "revise", "waived"]
CreatorSessionState = Literal[
    "defining",
    "selecting_evidence",
    "editing_draft",
    "designing_tests",
    "reviewing_results",
    "iterating",
    "completed",
    "archived",
]


class SkillCreatorError(Exception):
    """Base error for persisted Skill Creator sessions."""


class SkillCreatorNotFoundError(SkillCreatorError):
    pass


class SkillCreatorConflictError(SkillCreatorError):
    pass


class SkillCreatorStorageError(SkillCreatorError):
    pass


class SkillCreatorValidationError(SkillCreatorError):
    def __init__(self, message: str, *, code: str = "skill_creator_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class SkillCreatorSession:
    session_id: str
    session_revision: int = 1
    draft_state_revision: int = 0
    mode: CreatorMode = "blank"
    assistant_agent_id: str = CREATOR_ASSISTANT_AGENT_ID
    authoring_flow: CreatorAuthoringFlow = "legacy"
    trigger_required: bool = False
    intent: str = ""
    positive_examples: list[str] = field(default_factory=list)
    near_miss_examples: list[str] = field(default_factory=list)
    expected_output: str = ""
    success_criteria: list[str] = field(default_factory=list)
    selected_evidence: list[dict[str, str]] = field(default_factory=list)
    evidence_preview_fingerprint: str | None = None
    evidence_confirmed: bool = False
    proposal_id: str | None = None
    draft_id: str | None = None
    current_revision: int | None = None
    current_digest: str | None = None
    quality_mode: CreatorQualityMode = "objective"
    cases_revision: int = 0
    baseline_content_revision: int | None = None
    baseline_content_digest: str | None = None
    active_evaluation_run_id: str | None = None
    latest_evaluation_run_id: str | None = None
    review_state: CreatorReviewState = "none"
    review_revision: int = 0
    quality_status: str = "not_evaluated"
    quality_run_id: str | None = None
    quality_reason: str | None = None
    install_state: str = "not_installed"
    state: CreatorSessionState = "defining"
    source_kind: CreatorSourceKind = "blank"
    source_task_id: str | None = None
    source_run_id: str | None = None
    source_xpert_id: str | None = None
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SkillCreatorSessionStore:
    """Atomic, fail-closed persistence for recoverable Creator sessions."""

    SCHEMA_VERSION = 2
    READABLE_SCHEMA_VERSIONS = frozenset({1, SCHEMA_VERSION})
    MAX_SESSIONS = 500
    MAX_TEXT_BYTES = 64 * 1024

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_creator_sessions.json"
        self._lock = threading.RLock()
        self._items: dict[str, SkillCreatorSession] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def create(
        self,
        *,
        mode: CreatorMode = "blank",
        intent: str = "",
        positive_examples: list[str] | None = None,
        near_miss_examples: list[str] | None = None,
        expected_output: str = "",
        success_criteria: list[str] | None = None,
        source_kind: CreatorSourceKind = "blank",
        source_task_id: str | None = None,
        source_run_id: str | None = None,
        source_xpert_id: str | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        authoring_flow: CreatorAuthoringFlow = "legacy",
        trigger_required: bool = False,
    ) -> SkillCreatorSession:
        session = self._new_session(
            mode=mode,
            intent=intent,
            positive_examples=positive_examples,
            near_miss_examples=near_miss_examples,
            expected_output=expected_output,
            success_criteria=success_criteria,
            source_kind=source_kind,
            source_task_id=source_task_id,
            source_run_id=source_run_id,
            source_xpert_id=source_xpert_id,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            authoring_flow=authoring_flow,
            trigger_required=trigger_required,
        )
        with self._lock:
            self._insert_unlocked(session)
        return self._copy(session)

    def create_or_get_workflow_handoff(
        self,
        *,
        intent: str,
        positive_examples: list[str],
        near_miss_examples: list[str],
        expected_output: str,
        success_criteria: list[str],
        source_task_id: str,
        source_run_id: str,
        trigger_required: bool = False,
    ) -> SkillCreatorSession:
        """Atomically create one resource-authoring session per trusted run.

        A pristine run-capture session may win a narrow race with the automatic
        handoff. In that case it is hydrated in place. Any edited or otherwise
        divergent session fails closed instead of silently changing ownership.
        """

        candidate = self._new_session(
            mode="run",
            intent=intent,
            positive_examples=positive_examples,
            near_miss_examples=near_miss_examples,
            expected_output=expected_output,
            success_criteria=success_criteria,
            source_kind="workflow_classic",
            source_task_id=source_task_id,
            source_run_id=source_run_id,
            authoring_flow="resource",
            trigger_required=trigger_required,
        )
        with self._lock:
            self._ensure_writable_unlocked()
            matches = [
                item
                for item in self._items.values()
                if item.source_kind == "workflow_classic"
                and item.source_task_id == candidate.source_task_id
                and item.source_run_id == candidate.source_run_id
            ]
            if len(matches) > 1:
                raise SkillCreatorConflictError(
                    "Multiple Creator sessions match this workflow execution."
                )
            if matches:
                existing = matches[0]
                if self._handoff_definition_matches(existing, candidate):
                    return self._copy(existing)
                if self._is_pristine_run_capture(existing):
                    previous = self._copy(existing)
                    existing.authoring_flow = "resource"
                    existing.trigger_required = candidate.trigger_required
                    existing.intent = candidate.intent
                    existing.positive_examples = candidate.positive_examples
                    existing.near_miss_examples = candidate.near_miss_examples
                    existing.expected_output = candidate.expected_output
                    existing.success_criteria = candidate.success_criteria
                    existing.state = self._derive_state(existing)
                    existing.session_revision += 1
                    existing.updated_at = time.time()
                    try:
                        self._save_unlocked()
                    except BaseException:
                        self._items[existing.session_id] = previous
                        raise
                    return self._copy(existing)
                raise SkillCreatorConflictError(
                    "Creator workflow handoff conflicts with an existing session."
                )
            self._insert_unlocked(candidate)
            return self._copy(candidate)

    def create_or_get_run_capture(
        self,
        *,
        intent: str = "",
        positive_examples: list[str] | None = None,
        near_miss_examples: list[str] | None = None,
        expected_output: str = "",
        success_criteria: list[str] | None = None,
        source_kind: CreatorSourceKind,
        source_task_id: str,
        source_run_id: str,
        source_xpert_id: str | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        authoring_flow: CreatorAuthoringFlow = "legacy",
        trigger_required: bool = False,
    ) -> SkillCreatorSession:
        """Make the existing completed-run capture API idempotent by source."""

        candidate = self._new_session(
            mode="run",
            intent=intent,
            positive_examples=positive_examples,
            near_miss_examples=near_miss_examples,
            expected_output=expected_output,
            success_criteria=success_criteria,
            source_kind=source_kind,
            source_task_id=source_task_id,
            source_run_id=source_run_id,
            source_xpert_id=source_xpert_id,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            authoring_flow=authoring_flow,
            trigger_required=trigger_required,
        )
        with self._lock:
            self._ensure_writable_unlocked()
            matches = [
                item
                for item in self._items.values()
                if self._source_matches(item, candidate)
            ]
            if len(matches) > 1:
                raise SkillCreatorConflictError(
                    "Multiple Creator sessions match this runtime source."
                )
            if matches:
                existing = matches[0]
                if self._is_empty_definition(candidate) or self._definitions_match(
                    existing, candidate
                ):
                    return self._copy(existing)
                raise SkillCreatorConflictError(
                    "Creator runtime source is already bound to another definition."
                )
            self._insert_unlocked(candidate)
            return self._copy(candidate)

    def _new_session(
        self,
        *,
        mode: CreatorMode,
        intent: str,
        positive_examples: list[str] | None,
        near_miss_examples: list[str] | None,
        expected_output: str,
        success_criteria: list[str] | None,
        source_kind: CreatorSourceKind,
        source_task_id: str | None,
        source_run_id: str | None,
        source_xpert_id: str | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        authoring_flow: CreatorAuthoringFlow = "legacy",
        trigger_required: bool = False,
    ) -> SkillCreatorSession:
        if not isinstance(trigger_required, bool):
            raise SkillCreatorValidationError("trigger_required must be boolean.")
        if trigger_required and authoring_flow != "resource":
            raise SkillCreatorValidationError(
                "Only resource Creator sessions can require trigger validation."
            )
        session = SkillCreatorSession(
            session_id=f"skillcreator_{uuid.uuid4().hex}",
            mode=self._mode(mode),
            authoring_flow=self._authoring_flow(authoring_flow),
            trigger_required=trigger_required,
            intent=self._text(intent, "intent", maximum=8_000),
            positive_examples=self._text_list(
                positive_examples or [], "positive_examples", maximum_items=10
            ),
            near_miss_examples=self._text_list(
                near_miss_examples or [], "near_miss_examples", maximum_items=10
            ),
            expected_output=self._text(
                expected_output, "expected_output", maximum=8_000
            ),
            success_criteria=self._text_list(
                success_criteria or [], "success_criteria", maximum_items=12
            ),
            source_kind=self._source_kind(source_kind),
            source_task_id=self._optional_text(source_task_id, 200),
            source_run_id=self._optional_text(source_run_id, 200),
            source_xpert_id=self._optional_text(source_xpert_id, 200),
            source_conversation_id=self._optional_text(
                source_conversation_id, 200
            ),
            source_message_id=self._optional_text(source_message_id, 200),
        )
        self._validate_source(session)
        self._reject_credentials(session)
        session.state = self._derive_state(session)
        return session

    def _insert_unlocked(self, session: SkillCreatorSession) -> None:
        self._ensure_writable_unlocked()
        if len(self._items) >= self.MAX_SESSIONS:
            raise SkillCreatorValidationError(
                "Skill Creator session limit reached.",
                code="skill_creator_session_limit",
            )
        self._items[session.session_id] = session
        try:
            self._save_unlocked()
        except BaseException:
            self._items.pop(session.session_id, None)
            raise

    @staticmethod
    def _handoff_definition_matches(
        existing: SkillCreatorSession,
        candidate: SkillCreatorSession,
    ) -> bool:
        return (
            existing.mode == "run"
            and existing.authoring_flow == "resource"
            and existing.intent == candidate.intent
            and existing.positive_examples == candidate.positive_examples
            and existing.near_miss_examples == candidate.near_miss_examples
            and existing.expected_output == candidate.expected_output
            and existing.success_criteria == candidate.success_criteria
        )

    @staticmethod
    def _definitions_match(
        existing: SkillCreatorSession,
        candidate: SkillCreatorSession,
    ) -> bool:
        return (
            existing.intent == candidate.intent
            and existing.positive_examples == candidate.positive_examples
            and existing.near_miss_examples == candidate.near_miss_examples
            and existing.expected_output == candidate.expected_output
            and existing.success_criteria == candidate.success_criteria
        )

    @staticmethod
    def _is_empty_definition(item: SkillCreatorSession) -> bool:
        return not any(
            (
                item.intent,
                item.positive_examples,
                item.near_miss_examples,
                item.expected_output,
                item.success_criteria,
            )
        )

    @staticmethod
    def _source_matches(
        existing: SkillCreatorSession,
        candidate: SkillCreatorSession,
    ) -> bool:
        return (
            existing.mode == "run"
            and existing.source_kind == candidate.source_kind
            and existing.source_task_id == candidate.source_task_id
            and existing.source_run_id == candidate.source_run_id
            and existing.source_xpert_id == candidate.source_xpert_id
            and existing.source_conversation_id == candidate.source_conversation_id
            and existing.source_message_id == candidate.source_message_id
        )

    @staticmethod
    def _is_pristine_run_capture(item: SkillCreatorSession) -> bool:
        return (
            item.mode == "run"
            and item.authoring_flow == "legacy"
            and not item.trigger_required
            and item.session_revision == 1
            and not item.intent
            and not item.positive_examples
            and not item.near_miss_examples
            and not item.expected_output
            and not item.success_criteria
            and not item.evidence_confirmed
            and not item.proposal_id
            and not item.draft_id
        )

    def activate_resource_authoring(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
    ) -> SkillCreatorSession:
        """Persist an explicit opt-in for legacy Creator sessions."""

        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            if item.authoring_flow == "resource":
                return self._copy(item)
            if item.state == "archived":
                raise SkillCreatorConflictError("Archived Creator sessions are read-only.")
            previous = self._copy(item)
            item.authoring_flow = "resource"
            item.proposal_id = None
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def require(self, session_id: str) -> SkillCreatorSession:
        with self._lock:
            self._ensure_readable_unlocked()
            item = self._items.get(session_id)
            if item is None:
                raise SkillCreatorNotFoundError(
                    f"Skill Creator session not found: {session_id}"
                )
            return self._copy(item)

    def list(self, *, limit: int = 100) -> list[SkillCreatorSession]:
        with self._lock:
            self._ensure_readable_unlocked()
            items = sorted(
                self._items.values(), key=lambda item: item.updated_at, reverse=True
            )
            return [self._copy(item) for item in items[: max(1, min(limit, 500))]]

    def list_quarantined(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_readable_unlocked()
            return self._json_copy(self._quarantine)

    def update_definition(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        changes: dict[str, Any],
    ) -> SkillCreatorSession:
        allowed = {
            "intent",
            "positive_examples",
            "near_miss_examples",
            "expected_output",
            "success_criteria",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise SkillCreatorValidationError(
                "Unsupported Creator session fields: " + ", ".join(unknown)
            )
        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            if item.state == "archived":
                raise SkillCreatorConflictError("Archived Creator sessions are read-only.")
            previous = self._copy(item)
            if "intent" in changes:
                item.intent = self._text(changes["intent"], "intent", maximum=8_000)
            if "positive_examples" in changes:
                item.positive_examples = self._text_list(
                    changes["positive_examples"],
                    "positive_examples",
                    maximum_items=10,
                )
            if "near_miss_examples" in changes:
                item.near_miss_examples = self._text_list(
                    changes["near_miss_examples"],
                    "near_miss_examples",
                    maximum_items=10,
                )
            if "expected_output" in changes:
                item.expected_output = self._text(
                    changes["expected_output"], "expected_output", maximum=8_000
                )
            if "success_criteria" in changes:
                item.success_criteria = self._text_list(
                    changes["success_criteria"],
                    "success_criteria",
                    maximum_items=12,
                )
            item.selected_evidence = []
            item.evidence_preview_fingerprint = None
            item.evidence_confirmed = False
            item.proposal_id = None
            self._reject_credentials(item)
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def set_quality_mode(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        quality_mode: CreatorQualityMode,
    ) -> SkillCreatorSession:
        clean_mode = self._quality_mode(quality_mode)
        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            if item.active_evaluation_run_id or item.review_state in {
                "accepted",
                "waived",
            }:
                raise SkillCreatorConflictError(
                    "Quality mode is frozen for the current evaluation cycle."
                )
            if item.quality_mode == clean_mode:
                return self._copy(item)
            previous = self._copy(item)
            item.quality_mode = clean_mode
            item.cases_revision = 0
            item.latest_evaluation_run_id = None
            item.review_state = "none"
            item.review_revision += 1
            item.quality_status = "not_evaluated"
            item.quality_run_id = None
            item.quality_reason = None
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def bind_cases(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        cases_revision: int,
        baseline_content_revision: int | None,
        baseline_content_digest: str | None,
    ) -> SkillCreatorSession:
        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            previous = self._copy(item)
            item.cases_revision = self._positive_int(cases_revision, "cases_revision")
            item.baseline_content_revision = (
                self._positive_int(
                    baseline_content_revision, "baseline_content_revision"
                )
                if baseline_content_revision is not None
                else None
            )
            item.baseline_content_digest = (
                self._digest(
                    baseline_content_digest, "baseline_content_digest"
                )
                if baseline_content_digest is not None
                else None
            )
            item.active_evaluation_run_id = None
            item.review_state = "none"
            item.review_revision += 1
            item.quality_status = (
                "outdated"
                if item.quality_status
                in {"running", "accepted", "eval_waived", "outdated"}
                else "not_evaluated"
            )
            item.quality_run_id = None
            item.quality_reason = None
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def bind_evaluation(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        run_id: str,
    ) -> SkillCreatorSession:
        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            previous = self._copy(item)
            clean_run_id = self._required_identifier(run_id, "run_id")
            item.active_evaluation_run_id = clean_run_id
            item.latest_evaluation_run_id = clean_run_id
            item.review_state = "pending"
            item.review_revision += 1
            item.quality_status = "running"
            item.quality_run_id = None
            item.quality_reason = None
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def bind_quality_projection(
        self,
        session_id: str,
        *,
        draft_state_revision: int,
        content_revision: int,
        content_digest: str,
        quality_status: str,
        install_state: str,
        active_run_id: str | None = None,
        latest_run_id: str | None = None,
        review_state: CreatorReviewState | None = None,
        review_revision: int | None = None,
        quality_run_id: str | None = None,
        quality_reason: str | None = None,
    ) -> SkillCreatorSession:
        """Reconcile projections from authoritative Draft/Evaluation stores."""

        with self._lock:
            self._ensure_writable_unlocked()
            item = self._items.get(session_id)
            if item is None:
                raise SkillCreatorNotFoundError(
                    f"Skill Creator session not found: {session_id}"
                )
            clean_digest = self._digest(content_digest, "content_digest")
            clean_review_state = (
                self._review_state(review_state)
                if review_state is not None
                else item.review_state
            )
            projection = (
                self._positive_int(draft_state_revision, "draft_state_revision"),
                self._positive_int(content_revision, "content_revision"),
                clean_digest,
                self._quality_status(quality_status),
                self._install_state(install_state),
                self._optional_text(active_run_id, 200),
                self._optional_text(latest_run_id, 200),
                clean_review_state,
                max(0, int(review_revision if review_revision is not None else item.review_revision)),
                self._optional_text(quality_run_id, 200),
                self._optional_text(quality_reason, 4_000),
            )
            current = (
                item.draft_state_revision,
                item.current_revision,
                item.current_digest,
                item.quality_status,
                item.install_state,
                item.active_evaluation_run_id,
                item.latest_evaluation_run_id,
                item.review_state,
                item.review_revision,
                item.quality_run_id,
                item.quality_reason,
            )
            if current == projection:
                return self._copy(item)
            previous = self._copy(item)
            (
                item.draft_state_revision,
                item.current_revision,
                item.current_digest,
                item.quality_status,
                item.install_state,
                item.active_evaluation_run_id,
                item.latest_evaluation_run_id,
                item.review_state,
                item.review_revision,
                item.quality_run_id,
                item.quality_reason,
            ) = projection
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def set_evidence(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        preview_fingerprint: str,
        selected_evidence: list[dict[str, str]],
    ) -> SkillCreatorSession:
        clean_fingerprint = self._digest(preview_fingerprint, "preview_fingerprint")
        clean_evidence = self._evidence(selected_evidence)
        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            previous = self._copy(item)
            item.evidence_preview_fingerprint = clean_fingerprint
            item.selected_evidence = clean_evidence
            item.evidence_confirmed = True
            self._reject_credentials(item)
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def bind_proposal(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        proposal_id: str,
    ) -> SkillCreatorSession:
        with self._lock:
            item = self._require_mutable_unlocked(
                session_id, expected_session_revision=expected_session_revision
            )
            previous = self._copy(item)
            item.proposal_id = self._required_identifier(proposal_id, "proposal_id")
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    def bind_draft(
        self,
        session_id: str,
        *,
        draft_id: str,
        draft_state_revision: int,
        content_revision: int,
        content_digest: str,
        expected_session_revision: int | None = None,
    ) -> SkillCreatorSession:
        with self._lock:
            item = self._items.get(session_id)
            if item is None:
                raise SkillCreatorNotFoundError(
                    f"Skill Creator session not found: {session_id}"
                )
            self._ensure_writable_unlocked()
            if (
                expected_session_revision is not None
                and item.session_revision != expected_session_revision
            ):
                raise SkillCreatorConflictError(
                    "Creator session changed. Reload it before saving."
                )
            clean_digest = self._digest(content_digest, "content_digest")
            clean_draft_id = self._required_identifier(draft_id, "draft_id")
            if (
                item.draft_id == clean_draft_id
                and item.draft_state_revision == int(draft_state_revision)
                and item.current_revision == int(content_revision)
                and item.current_digest == clean_digest
            ):
                return self._copy(item)
            previous = self._copy(item)
            content_changed = bool(
                item.current_digest and item.current_digest != clean_digest
            )
            item.draft_id = clean_draft_id
            item.draft_state_revision = self._positive_int(
                draft_state_revision, "draft_state_revision"
            )
            item.current_revision = self._positive_int(
                content_revision, "content_revision"
            )
            item.current_digest = clean_digest
            if content_changed:
                item.active_evaluation_run_id = None
                item.review_state = "none"
                item.review_revision += 1
                item.quality_status = (
                    "outdated"
                    if item.quality_status
                    in {"running", "accepted", "eval_waived", "outdated"}
                    else "not_evaluated"
                )
                item.quality_run_id = None
                item.quality_reason = None
            item.state = self._derive_state(item)
            item.session_revision += 1
            item.updated_at = time.time()
            return self._save_or_restore_unlocked(item, previous)

    @staticmethod
    def serialize(item: SkillCreatorSession) -> dict[str, Any]:
        return asdict(item)

    def _require_mutable_unlocked(
        self, session_id: str, *, expected_session_revision: int
    ) -> SkillCreatorSession:
        self._ensure_writable_unlocked()
        item = self._items.get(session_id)
        if item is None:
            raise SkillCreatorNotFoundError(
                f"Skill Creator session not found: {session_id}"
            )
        if item.session_revision != int(expected_session_revision):
            raise SkillCreatorConflictError(
                "Creator session changed. Reload it before saving."
            )
        return item

    def _save_or_restore_unlocked(
        self, item: SkillCreatorSession, previous: SkillCreatorSession
    ) -> SkillCreatorSession:
        try:
            self._save_unlocked()
        except BaseException:
            self._items[item.session_id] = previous
            raise
        return self._copy(item)

    @staticmethod
    def _derive_state(item: SkillCreatorSession) -> CreatorSessionState:
        if item.state == "archived":
            return "archived"
        if item.draft_id:
            if (
                item.install_state == "current"
                and item.quality_status in {"accepted", "eval_waived"}
            ):
                return "completed"
            if item.review_state in {"accepted", "revise", "waived"}:
                return "iterating"
            if item.active_evaluation_run_id or (
                item.latest_evaluation_run_id and item.review_state == "pending"
            ):
                return "reviewing_results"
            if item.cases_revision > 0:
                return "designing_tests"
            return "editing_draft"
        if item.intent and item.expected_output and item.success_criteria:
            return "selecting_evidence"
        return "defining"

    @staticmethod
    def _mode(value: Any) -> CreatorMode:
        if value not in {"blank", "run"}:
            raise SkillCreatorValidationError("Invalid Creator session mode.")
        return value

    @staticmethod
    def _source_kind(value: Any) -> CreatorSourceKind:
        if value not in {"blank", "xpert_chat", "workflow_classic"}:
            raise SkillCreatorValidationError("Invalid Creator source kind.")
        return value

    @staticmethod
    def _quality_mode(value: Any) -> CreatorQualityMode:
        if value not in {"objective", "subjective"}:
            raise SkillCreatorValidationError("Invalid Creator quality mode.")
        return value

    @staticmethod
    def _authoring_flow(value: Any) -> CreatorAuthoringFlow:
        if value not in {"legacy", "resource"}:
            raise SkillCreatorValidationError("Invalid Creator authoring flow.")
        return value

    @staticmethod
    def _review_state(value: Any) -> CreatorReviewState:
        if value not in {"none", "pending", "accepted", "revise", "waived"}:
            raise SkillCreatorValidationError("Invalid Creator review state.")
        return value

    @staticmethod
    def _quality_status(value: Any) -> str:
        if value not in {
            "not_evaluated",
            "running",
            "accepted",
            "eval_waived",
            "outdated",
        }:
            raise SkillCreatorValidationError("Invalid Creator quality status.")
        return str(value)

    @staticmethod
    def _install_state(value: Any) -> str:
        if value not in {"not_installed", "current", "outdated"}:
            raise SkillCreatorValidationError("Invalid Creator install state.")
        return str(value)

    @staticmethod
    def _validate_source(item: SkillCreatorSession) -> None:
        if item.mode == "blank":
            if item.source_kind != "blank" or any(
                (
                    item.source_task_id,
                    item.source_run_id,
                    item.source_xpert_id,
                    item.source_conversation_id,
                    item.source_message_id,
                )
            ):
                raise SkillCreatorValidationError(
                    "Blank Creator sessions cannot bind a runtime source.",
                    code="skill_creator_source_invalid",
                )
            return
        if item.source_kind == "blank" or not item.source_task_id or not item.source_run_id:
            raise SkillCreatorValidationError(
                "Run-based Creator sessions require a trusted task and run source.",
                code="skill_creator_source_invalid",
            )
        if item.source_kind == "xpert_chat" and not all(
            (
                item.source_xpert_id,
                item.source_conversation_id,
                item.source_message_id,
            )
        ):
            raise SkillCreatorValidationError(
                "Xpert Chat sources require Xpert, conversation, and message IDs.",
                code="skill_creator_source_invalid",
            )
        if item.source_kind == "workflow_classic" and any(
            (
                item.source_xpert_id,
                item.source_conversation_id,
                item.source_message_id,
            )
        ):
            raise SkillCreatorValidationError(
                "Classic workflow sources cannot include Xpert conversation IDs.",
                code="skill_creator_source_invalid",
            )

    @classmethod
    def _reject_credentials(cls, item: SkillCreatorSession) -> None:
        payload = asdict(item)
        issues = scan_skill_package_credentials(
            skill_markdown=json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        if issues:
            raise SkillCreatorValidationError(
                "Creator session contains blocked credential material.",
                code="skill_creator_credentials_blocked",
            )
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > cls.MAX_TEXT_BYTES:
            raise SkillCreatorValidationError(
                "Creator session content is too large.",
                code="skill_creator_session_too_large",
            )

    @staticmethod
    def _text(value: Any, field_name: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise SkillCreatorValidationError(f"{field_name} must be text.")
        clean = value.strip()
        if len(clean) > maximum:
            raise SkillCreatorValidationError(f"{field_name} is too long.")
        return clean

    @classmethod
    def _text_list(
        cls, value: Any, field_name: str, *, maximum_items: int
    ) -> list[str]:
        if not isinstance(value, list) or len(value) > maximum_items:
            raise SkillCreatorValidationError(f"Invalid {field_name} list.")
        return [cls._text(item, field_name, maximum=4_000) for item in value]

    @classmethod
    def _evidence(cls, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list) or len(value) > 30:
            raise SkillCreatorValidationError("Invalid selected evidence list.")
        result: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict) or set(item) - {
                "candidate_id",
                "kind",
                "title",
                "summary",
                "content_hash",
            }:
                raise SkillCreatorValidationError("Invalid Creator evidence item.")
            result.append(
                {
                    "candidate_id": cls._required_identifier(
                        item.get("candidate_id"), "candidate_id"
                    ),
                    "kind": cls._text(item.get("kind", ""), "kind", maximum=80),
                    "title": cls._text(
                        item.get("title", ""), "title", maximum=300
                    ),
                    "summary": cls._text(
                        item.get("summary", ""), "summary", maximum=2_000
                    ),
                    "content_hash": cls._digest(
                        item.get("content_hash"), "content_hash"
                    ),
                }
            )
        return result

    @staticmethod
    def _required_identifier(value: Any, field_name: str) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > 200:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _optional_text(value: Any, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise SkillCreatorValidationError("Optional identifiers must be text.")
        clean = value.strip()
        if len(clean) > maximum:
            raise SkillCreatorValidationError("Optional identifier is too long.")
        return clean or None

    @staticmethod
    def _digest(value: Any, field_name: str) -> str:
        clean = str(value or "").strip().lower()
        if len(clean) != 64 or any(character not in "0123456789abcdef" for character in clean):
            raise SkillCreatorValidationError(f"Invalid {field_name} digest.")
        return clean

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        if isinstance(value, bool):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillCreatorValidationError(f"Invalid {field_name}.") from exc
        if result < 1:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return result

    def _load(self) -> None:
        with self._lock:
            if not self.snapshot_path.exists():
                return
            try:
                raw_bytes = self.snapshot_path.read_bytes()
                raw = json.loads(raw_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                self._load_error = f"Skill Creator storage is unreadable: {exc}"
                return
            if (
                not isinstance(raw, dict)
                or raw.get("version") not in self.READABLE_SCHEMA_VERSIONS
                or not isinstance(raw.get("items"), list)
            ):
                self._load_error = "Skill Creator storage has an unsupported structure."
                return
            sanitized = raw["version"] != self.SCHEMA_VERSION
            for index, record in enumerate(raw["items"]):
                try:
                    item = self._decode_item(record)
                    if item.session_id in self._items:
                        raise ValueError("Duplicate Creator session ID.")
                    self._reject_credentials(item)
                except (TypeError, ValueError, SkillCreatorValidationError):
                    self._quarantine.append(self._quarantine_record(record, index=index))
                    sanitized = True
                    continue
                self._items[item.session_id] = item
            for index, entry in enumerate(raw.get("quarantine", [])):
                safe = self._sanitize_quarantine_entry(entry, index=index)
                if safe is not None:
                    self._quarantine.append(safe)
                if safe != entry:
                    sanitized = True
            if sanitized:
                try:
                    self._save_unlocked()
                except OSError as exc:
                    self._load_error = f"Unable to sanitize Skill Creator storage: {exc}"

    def _decode_item(self, record: Any) -> SkillCreatorSession:
        if not isinstance(record, dict):
            raise TypeError("Creator session record must be an object.")
        item = SkillCreatorSession(**record)
        if item.assistant_agent_id != CREATOR_ASSISTANT_AGENT_ID:
            raise ValueError("Creator assistant identity is immutable.")
        item.mode = self._mode(item.mode)
        item.authoring_flow = self._authoring_flow(item.authoring_flow)
        if not isinstance(item.trigger_required, bool):
            raise ValueError("Invalid Creator trigger requirement.")
        if item.trigger_required and item.authoring_flow != "resource":
            raise ValueError("Legacy Creator sessions cannot require trigger validation.")
        item.source_kind = self._source_kind(item.source_kind)
        item.quality_mode = self._quality_mode(item.quality_mode)
        item.review_state = self._review_state(item.review_state)
        item.quality_status = self._quality_status(item.quality_status)
        item.install_state = self._install_state(item.install_state)
        self._validate_source(item)
        if item.state not in {
            "defining",
            "selecting_evidence",
            "editing_draft",
            "designing_tests",
            "reviewing_results",
            "iterating",
            "completed",
            "archived",
        }:
            raise ValueError("Invalid Creator session state.")
        item.session_revision = self._positive_int(
            item.session_revision, "session_revision"
        )
        if item.draft_state_revision < 0:
            raise ValueError("Invalid draft_state_revision.")
        if item.cases_revision < 0 or item.review_revision < 0:
            raise ValueError("Invalid Creator evaluation revision.")
        if item.current_revision is not None:
            item.current_revision = self._positive_int(
                item.current_revision, "current_revision"
            )
        if item.current_digest is not None:
            item.current_digest = self._digest(item.current_digest, "current_digest")
        if item.baseline_content_revision is not None:
            item.baseline_content_revision = self._positive_int(
                item.baseline_content_revision, "baseline_content_revision"
            )
        if item.baseline_content_digest is not None:
            item.baseline_content_digest = self._digest(
                item.baseline_content_digest, "baseline_content_digest"
            )
        for field_name in (
            "active_evaluation_run_id",
            "latest_evaluation_run_id",
            "quality_run_id",
        ):
            setattr(
                item,
                field_name,
                self._optional_text(getattr(item, field_name), 200),
            )
        item.quality_reason = self._optional_text(item.quality_reason, 4_000)
        item.intent = self._text(item.intent, "intent", maximum=8_000)
        item.positive_examples = self._text_list(
            item.positive_examples, "positive_examples", maximum_items=10
        )
        item.near_miss_examples = self._text_list(
            item.near_miss_examples, "near_miss_examples", maximum_items=10
        )
        item.expected_output = self._text(
            item.expected_output, "expected_output", maximum=8_000
        )
        item.success_criteria = self._text_list(
            item.success_criteria, "success_criteria", maximum_items=12
        )
        if not isinstance(item.evidence_confirmed, bool):
            raise ValueError("Invalid evidence_confirmed value.")
        item.selected_evidence = self._evidence(item.selected_evidence)
        item.state = self._derive_state(item) if item.state != "archived" else "archived"
        return item

    @staticmethod
    def _quarantine_record(record: Any, *, index: int) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, UnicodeEncodeError):
            encoded = type(record).__name__.encode("ascii", errors="replace")
        return {
            "index": max(0, int(index)),
            "reason_code": "blocked_or_invalid_session",
            "record_sha256": hashlib.sha256(encoded).hexdigest(),
            "record_size_bytes": len(encoded),
            "quarantined_at": time.time(),
        }

    @staticmethod
    def _sanitize_quarantine_entry(
        entry: Any, *, index: int
    ) -> dict[str, Any] | None:
        if not isinstance(entry, dict):
            return None
        digest = str(entry.get("record_sha256") or "").lower()
        size = entry.get("record_size_bytes")
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or size < 0
        ):
            return None
        return {
            "index": max(0, int(entry.get("index", index))),
            "reason_code": "blocked_or_invalid_session",
            "record_sha256": digest,
            "record_size_bytes": size,
            "quarantined_at": float(entry.get("quarantined_at", time.time())),
        }

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillCreatorStorageError(self._load_error)

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        payload = {
            "version": self.SCHEMA_VERSION,
            "items": [
                asdict(item)
                for item in sorted(self._items.values(), key=lambda value: value.session_id)
            ],
            "quarantine": self._quarantine,
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            with temp_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.snapshot_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _json_copy(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def _copy(item: SkillCreatorSession) -> SkillCreatorSession:
        return SkillCreatorSession(**SkillCreatorSessionStore._json_copy(asdict(item)))
