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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Protocol

from .creator_resource_plan import SkillResourcePlan
from .creator_resource_service import SkillCreatorResourcePlanningService
from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorError,
    SkillCreatorSession,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft
from .package_validation import scan_skill_package_credentials
from .trigger_contract import (
    SkillTriggerEvaluator,
    SkillTriggerReceiptV1,
    SkillTriggerStore,
    SkillTriggerSuiteV1,
    trigger_definition_digest,
    trigger_optimization_enabled,
)


TRIGGER_OPTIMIZATION_SERVICE_VERSION = "skill-trigger-optimization-v1"
TRIGGER_ATTEMPT_VERSION = "skill-trigger-description-attempt-v1"
TRIGGER_OPTIMIZATION_STORE_VERSION = 1

TriggerAttemptState = Literal["evaluated", "confirmed"]


class TriggerOptimizationExecutor(Protocol):
    def available(self) -> bool: ...

    async def generate_suite(self, context: dict[str, Any]) -> list[dict[str, str]]: ...

    async def optimize_descriptions(self, context: dict[str, Any]) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class SkillTriggerDescriptionCandidate:
    description: str
    description_digest: str
    receipt_id: str
    passed: bool
    worst_positive_rank: int
    positive_rank_sum: int
    negative_safety_distance: int


@dataclass(frozen=True, slots=True)
class SkillTriggerDescriptionAttempt:
    attempt_id: str
    version: str
    revision: int
    digest: str
    session_id: str
    session_revision: int
    plan_id: str
    plan_revision: int
    plan_digest: str
    suite_id: str
    suite_revision: int
    suite_digest: str
    state: TriggerAttemptState
    candidates: tuple[SkillTriggerDescriptionCandidate, ...]
    recommended_description_digest: str | None
    selected_description_digest: str | None = None
    created_at: float = field(default_factory=time.time)
    confirmed_at: float | None = None


class SkillTriggerOptimizationStore:
    """Atomic local state for opt-in sessions and immutable description attempts."""

    MAX_ATTEMPTS = 2_000

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        configured = os.getenv("SKILL_CREATOR_TRIGGER_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or configured or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_creator_trigger_optimization.json"
        self._lock = threading.RLock()
        self._required_sessions: set[str] = set()
        self._attempts: dict[str, list[SkillTriggerDescriptionAttempt]] = {}
        self._quarantined_records = 0
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self._load_error is None,
                "required_session_count": len(self._required_sessions),
                "attempt_count": sum(len(items) for items in self._attempts.values()),
                "quarantined_record_count": self._quarantined_records,
                "error_code": "skill_trigger_index_unavailable" if self._load_error else None,
            }

    def is_required(self, session_id: str) -> bool:
        clean = _identifier(session_id, "session_id")
        with self._lock:
            self._ensure_readable_unlocked()
            return clean in self._required_sessions

    def mark_required(self, session_id: str) -> None:
        clean = _identifier(session_id, "session_id")
        with self._lock:
            self._ensure_writable_unlocked()
            if clean in self._required_sessions:
                return
            self._required_sessions.add(clean)
            try:
                self._save_unlocked()
            except Exception:
                self._required_sessions.remove(clean)
                raise

    def current_for_session(self, session_id: str) -> SkillTriggerDescriptionAttempt | None:
        clean = _identifier(session_id, "session_id")
        with self._lock:
            self._ensure_readable_unlocked()
            attempts = [items[-1] for items in self._attempts.values() if items and items[-1].session_id == clean]
            attempts.sort(key=lambda item: (item.created_at, item.attempt_id), reverse=True)
            return copy.deepcopy(attempts[0]) if attempts else None

    def current_for_scope(
        self,
        *,
        session_id: str,
        plan_id: str,
        plan_revision: int,
        plan_digest: str,
        suite_id: str,
        suite_revision: int,
        suite_digest: str,
    ) -> SkillTriggerDescriptionAttempt | None:
        expected = (
            _identifier(session_id, "session_id"),
            _identifier(plan_id, "plan_id"),
            int(plan_revision),
            _digest(plan_digest, "plan_digest"),
            _identifier(suite_id, "suite_id"),
            int(suite_revision),
            _digest(suite_digest, "suite_digest"),
        )
        with self._lock:
            self._ensure_readable_unlocked()
            matches = [
                items[-1]
                for items in self._attempts.values()
                if items
                and (
                    items[-1].session_id,
                    items[-1].plan_id,
                    items[-1].plan_revision,
                    items[-1].plan_digest,
                    items[-1].suite_id,
                    items[-1].suite_revision,
                    items[-1].suite_digest,
                )
                == expected
            ]
            matches.sort(
                key=lambda item: (item.created_at, item.attempt_id), reverse=True
            )
            return copy.deepcopy(matches[0]) if matches else None

    def require(self, attempt_id: str) -> SkillTriggerDescriptionAttempt:
        clean = _identifier(attempt_id, "attempt_id")
        with self._lock:
            self._ensure_readable_unlocked()
            revisions = self._attempts.get(clean)
            if not revisions:
                raise SkillCreatorValidationError(
                    "Trigger description attempt was not found.",
                    code="skill_trigger_evaluation_failed",
                )
            return copy.deepcopy(revisions[-1])

    def matching_confirmation(
        self,
        *,
        session_id: str,
        plan_id: str,
        suite_id: str,
        suite_revision: int,
        suite_digest: str,
        description_digest: str,
    ) -> SkillTriggerDescriptionAttempt | None:
        expected = (
            _identifier(session_id, "session_id"),
            _identifier(plan_id, "plan_id"),
            _identifier(suite_id, "suite_id"),
            int(suite_revision),
            _digest(suite_digest, "suite_digest"),
            _digest(description_digest, "description_digest"),
        )
        with self._lock:
            self._ensure_readable_unlocked()
            matches = [
                items[-1]
                for items in self._attempts.values()
                if items
                and items[-1].state == "confirmed"
                and (
                    items[-1].session_id,
                    items[-1].plan_id,
                    items[-1].suite_id,
                    items[-1].suite_revision,
                    items[-1].suite_digest,
                    items[-1].selected_description_digest,
                )
                == expected
            ]
            matches.sort(key=lambda item: (item.confirmed_at or 0, item.attempt_id), reverse=True)
            return copy.deepcopy(matches[0]) if matches else None

    def create(
        self,
        *,
        session: SkillCreatorSession,
        plan: SkillResourcePlan,
        suite: SkillTriggerSuiteV1,
        candidates: Iterable[SkillTriggerDescriptionCandidate],
    ) -> SkillTriggerDescriptionAttempt:
        normalized = tuple(candidates)
        if not 1 <= len(normalized) <= 3:
            raise SkillCreatorValidationError(
                "A trigger attempt requires one to three descriptions.",
                code="skill_trigger_optimizer_invalid",
            )
        ranked = sorted(normalized, key=_candidate_sort_key)
        content = {
            "version": TRIGGER_ATTEMPT_VERSION,
            "session_id": session.session_id,
            "session_revision": session.session_revision,
            "plan_id": plan.plan_id,
            "plan_revision": plan.revision,
            "plan_digest": plan.digest,
            "suite_id": suite.suite_id,
            "suite_revision": suite.suite_revision,
            "suite_digest": suite.suite_digest,
            "state": "evaluated",
            "candidates": [asdict(item) for item in normalized],
            "recommended_description_digest": (
                ranked[0].description_digest if ranked[0].passed else None
            ),
            "selected_description_digest": None,
        }
        digest = _sha256(content)
        attempt = SkillTriggerDescriptionAttempt(
            attempt_id=f"triggerattempt_{uuid.uuid4().hex}",
            revision=1,
            digest=digest,
            created_at=time.time(),
            candidates=normalized,
            **{key: value for key, value in content.items() if key != "candidates"},  # type: ignore[arg-type]
        )
        with self._lock:
            self._ensure_writable_unlocked()
            if sum(len(items) for items in self._attempts.values()) >= self.MAX_ATTEMPTS:
                raise SkillCreatorStorageError("Trigger optimization Store reached its bounded capacity.")
            self._attempts[attempt.attempt_id] = [attempt]
            try:
                self._save_unlocked()
            except Exception:
                self._attempts.pop(attempt.attempt_id, None)
                raise
        return copy.deepcopy(attempt)

    def confirm(
        self,
        attempt_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        selected_description_digest: str,
    ) -> SkillTriggerDescriptionAttempt:
        with self._lock:
            self._ensure_writable_unlocked()
            current = self.require(attempt_id)
            if current.revision != int(expected_revision) or current.digest != str(expected_digest).lower():
                raise SkillCreatorConflictError("Trigger description attempt changed. Reload before continuing.")
            selected = str(selected_description_digest or "").lower()
            if not any(item.description_digest == selected and item.passed for item in current.candidates):
                raise SkillCreatorValidationError(
                    "Only a description that passes the trigger gate can be confirmed.",
                    code="skill_trigger_evaluation_failed",
                )
            if current.state == "confirmed":
                if current.selected_description_digest != selected:
                    raise SkillCreatorConflictError("A different trigger description is already confirmed.")
                return current
            content = {
                key: value
                for key, value in asdict(current).items()
                if key not in {"attempt_id", "revision", "digest", "created_at", "confirmed_at"}
            }
            content["state"] = "confirmed"
            content["selected_description_digest"] = selected
            content["candidates"] = [asdict(item) for item in current.candidates]
            confirmed = SkillTriggerDescriptionAttempt(
                attempt_id=current.attempt_id,
                revision=current.revision + 1,
                digest=_sha256(content),
                created_at=current.created_at,
                confirmed_at=time.time(),
                **{**content, "candidates": current.candidates},  # type: ignore[arg-type]
            )
            self._attempts[current.attempt_id].append(confirmed)
            try:
                self._save_unlocked()
            except Exception:
                self._attempts[current.attempt_id].pop()
                raise
            return copy.deepcopy(confirmed)

    @staticmethod
    def serialize(item: SkillTriggerDescriptionAttempt) -> dict[str, Any]:
        return asdict(item)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != TRIGGER_OPTIMIZATION_STORE_VERSION:
                raise ValueError("Trigger optimization Store version is invalid.")
            required = raw.get("required_sessions", [])
            attempts = raw.get("attempts", [])
            if not isinstance(required, list) or not isinstance(attempts, list):
                raise ValueError("Trigger optimization Store structure is invalid.")
            for item in required:
                try:
                    self._required_sessions.add(_identifier(item, "session_id"))
                except SkillCreatorValidationError:
                    self._quarantined_records += 1
            for record in attempts:
                try:
                    item = _decode_attempt(record)
                    revisions = self._attempts.setdefault(item.attempt_id, [])
                    if item.revision != len(revisions) + 1:
                        raise ValueError("Trigger attempt revisions are not contiguous.")
                    revisions.append(item)
                except Exception:
                    self._quarantined_records += 1
        except Exception as exc:
            self._required_sessions = set()
            self._attempts = {}
            self._quarantined_records = 0
            self._load_error = f"Unable to read trigger optimization Store: {exc}"

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        payload = {
            "version": TRIGGER_OPTIMIZATION_STORE_VERSION,
            "required_sessions": sorted(self._required_sessions),
            "attempts": [
                asdict(item)
                for attempt_id in sorted(self._attempts)
                for item in self._attempts[attempt_id]
            ],
        }
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_suffix(f".tmp-{uuid.uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.snapshot_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillCreatorStorageError("Trigger optimization Store is unavailable.")

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()


class SkillCreatorTriggerOptimizationService:
    VERSION = TRIGGER_OPTIMIZATION_SERVICE_VERSION

    def __init__(
        self,
        creator_service: SkillCreatorService,
        planning_service: SkillCreatorResourcePlanningService,
        trigger_store: SkillTriggerStore,
        optimization_store: SkillTriggerOptimizationStore,
        evaluator: SkillTriggerEvaluator,
        *,
        actor_id: str,
        executor: TriggerOptimizationExecutor | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.creator_service = creator_service
        self.planning_service = planning_service
        self.trigger_store = trigger_store
        self.optimization_store = optimization_store
        self.evaluator = evaluator
        self.actor_id = _identifier(actor_id, "actor_id")
        self.executor = executor
        self.enabled = trigger_optimization_enabled() if enabled is None else bool(enabled)

    def require_enabled(self) -> None:
        if not self.enabled:
            raise SkillCreatorValidationError(
                "Skill trigger optimization is disabled.",
                code="skill_trigger_gate_required",
            )

    def requires_trigger(self, session: SkillCreatorSession) -> bool:
        """Resolve the atomic new-session fact before legacy opt-in state."""

        if session.trigger_required:
            return True
        if self.optimization_store.is_required(session.session_id):
            return True
        if self.optimization_store.current_for_session(session.session_id) is not None:
            return True
        return self.trigger_store.current_for_session(session.session_id) is not None

    def status(self) -> dict[str, Any]:
        try:
            model_available = bool(self.executor and self.executor.available())
        except Exception:
            model_available = False
        return {
            "version": self.VERSION,
            "enabled": self.enabled,
            "model_available": model_available,
            "trigger_store": self.trigger_store.status(),
            "optimization_store": self.optimization_store.status(),
        }

    async def generate_suite(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        expected_suite_revision: int | None,
        expected_suite_digest: str | None,
    ) -> SkillTriggerSuiteV1:
        session, plan = self._require_scope(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
        )
        self.optimization_store.mark_required(session.session_id)
        executor = self._require_executor()
        try:
            cases = await executor.generate_suite(self._suite_context(session, plan))
        except SkillCreatorValidationError:
            raise
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The trigger suite generator failed.",
                code="skill_trigger_optimizer_invalid",
            ) from exc
        return self.trigger_store.save_draft(
            session_id=session.session_id,
            session_revision=session.session_revision,
            definition_digest=_definition_digest(session),
            skill_name=plan.skill_name,
            cases=cases,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            change_reason="Generated trigger boundaries.",
        )

    def save_suite(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        cases: list[dict[str, str]],
        expected_suite_revision: int | None,
        expected_suite_digest: str | None,
        change_reason: str,
    ) -> SkillTriggerSuiteV1:
        session, plan = self._require_scope(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
        )
        self.optimization_store.mark_required(session.session_id)
        return self.trigger_store.save_draft(
            session_id=session.session_id,
            session_revision=session.session_revision,
            definition_digest=_definition_digest(session),
            skill_name=plan.skill_name,
            cases=cases,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            change_reason=change_reason,
        )

    def confirm_suite(
        self,
        session_id: str,
        *,
        suite_id: str,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
    ) -> SkillTriggerSuiteV1:
        session, plan = self._require_scope(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
        )
        self.optimization_store.mark_required(session.session_id)
        return self.trigger_store.confirm(
            suite_id=suite_id,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            session_revision=session.session_revision,
            definition_digest=_definition_digest(session),
            skill_name=plan.skill_name,
            actor_id=self.actor_id,
        )

    async def optimize(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
    ) -> SkillTriggerDescriptionAttempt:
        session, plan, suite = self._require_confirmed_suite(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )
        executor = self._require_executor()
        model_description = _model_safe_current_description(plan.skill_description)
        try:
            seed = self._evaluate_and_save(suite, plan.skill_name, model_description)
        except SkillCreatorValidationError as exc:
            if exc.code != "skill_trigger_description_invalid":
                raise
            seed = self._evaluate_and_save(
                suite,
                plan.skill_name,
                "Guide the task defined by this Creator session and avoid unrelated requests.",
            )
        try:
            descriptions = await executor.optimize_descriptions(
                self._description_context(session, plan, suite, seed)
            )
        except SkillCreatorValidationError:
            raise
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The trigger description optimizer failed.",
                code="skill_trigger_optimizer_invalid",
            ) from exc
        return self._create_attempt(session, plan, suite, descriptions)

    def evaluate_description(
        self,
        session_id: str,
        *,
        description: str,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
    ) -> SkillTriggerDescriptionAttempt:
        session, plan, suite = self._require_confirmed_suite(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )
        return self._create_attempt(session, plan, suite, [description])

    def confirm_description(
        self,
        session_id: str,
        *,
        attempt_id: str,
        selected_description_digest: str,
        expected_attempt_revision: int,
        expected_attempt_digest: str,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
    ) -> tuple[SkillTriggerDescriptionAttempt, SkillResourcePlan]:
        session, plan, suite = self._require_confirmed_suite(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )
        current = self.optimization_store.require(attempt_id)
        if (
            current.session_id != session.session_id
            or current.plan_id != plan.plan_id
            or current.suite_id != suite.suite_id
            or current.suite_revision != suite.suite_revision
            or current.suite_digest != suite.suite_digest
        ):
            raise SkillCreatorConflictError("Trigger description attempt is stale. Reload before continuing.")
        selected = next(
            (item for item in current.candidates if item.description_digest == selected_description_digest.lower()),
            None,
        )
        if selected is None:
            raise SkillCreatorValidationError(
                "Selected trigger description is not part of this attempt.",
                code="skill_trigger_optimizer_invalid",
            )
        current_receipt = self._evaluate_and_save(
            suite, plan.skill_name, selected.description
        )
        if not current_receipt.passed:
            raise SkillCreatorValidationError(
                "The selected description no longer passes the trigger contract.",
                code="skill_trigger_evaluation_failed",
            )
        if current_receipt.receipt_id != selected.receipt_id:
            raise SkillCreatorConflictError(
                "The trigger ranking inputs changed. Evaluate the description again.",
                code="skill_trigger_receipt_stale",
            )
        selected_digest = selected.description_digest
        plan_description_digest = hashlib.sha256(plan.skill_description.encode("utf-8")).hexdigest()
        if current.state == "evaluated" and (
            current.plan_revision != plan.revision or current.plan_digest != plan.digest
        ):
            raise SkillCreatorConflictError("Trigger description attempt is stale. Reload before continuing.")
        if current.state == "confirmed" and (
            current.selected_description_digest != selected_digest
            or (
                plan_description_digest != selected_digest
                and (current.plan_revision != plan.revision or current.plan_digest != plan.digest)
            )
        ):
            raise SkillCreatorConflictError("Trigger description confirmation conflicts with the resource plan.")
        confirmed = self.optimization_store.confirm(
            attempt_id,
            expected_revision=expected_attempt_revision,
            expected_digest=expected_attempt_digest,
            selected_description_digest=selected_digest,
        )
        if plan_description_digest == selected_digest:
            return confirmed, plan
        updated_plan = self.planning_service.patch(
            session.session_id,
            plan_id=plan.plan_id,
            expected_session_revision=session.session_revision,
            expected_plan_revision=plan.revision,
            expected_plan_digest=plan.digest,
            changes={"skill_description": selected.description},
        )
        return confirmed, updated_plan

    def require_plan_gate(
        self,
        session: SkillCreatorSession,
        plan: SkillResourcePlan,
        draft: WorkspaceSkillDraft | None = None,
    ) -> SkillTriggerReceiptV1 | None:
        if not self.enabled:
            return None
        if not self.requires_trigger(session):
            return None
        suite = self._current_confirmed_suite(session, skill_name=plan.skill_name)
        self._require_confirmed_description(
            session_id=session.session_id,
            plan_id=plan.plan_id,
            suite=suite,
            description=plan.skill_description,
        )
        receipt = self._evaluate_and_save(suite, plan.skill_name, plan.skill_description)
        if not receipt.passed:
            raise SkillCreatorValidationError(
                "The current Skill description does not pass the trigger contract.",
                code="skill_trigger_evaluation_failed",
            )
        return receipt

    def require_draft_install_gate(self, draft: WorkspaceSkillDraft) -> SkillTriggerReceiptV1 | None:
        if not self.enabled:
            return None
        if draft.creator_session_id is None:
            return None
        session, bound_draft = self.creator_service.get_session(draft.creator_session_id)
        if (
            session.draft_id != draft.draft_id
            or bound_draft is None
            or bound_draft.draft_id != draft.draft_id
        ):
            raise SkillCreatorConflictError(
                "The Creator draft no longer matches its owning session."
            )
        if not self.requires_trigger(session):
            return None
        suite = self._current_confirmed_suite(session, skill_name=draft.slug)
        plan = self.planning_service.plan_store.current_for_session(session.session_id)
        if plan is None:
            raise SkillCreatorValidationError(
                "The Creator trigger contract has no resource plan binding.",
                code="skill_trigger_gate_required",
            )
        self._require_confirmed_description(
            session_id=session.session_id,
            plan_id=plan.plan_id,
            suite=suite,
            description=draft.description,
        )
        receipt = self._evaluate_and_save(suite, draft.slug, draft.description)
        if not receipt.passed:
            raise SkillCreatorValidationError(
                "The current Creator draft no longer passes its trigger contract.",
                code="skill_trigger_gate_required",
            )
        return receipt

    def _require_confirmed_description(
        self,
        *,
        session_id: str,
        plan_id: str,
        suite: SkillTriggerSuiteV1,
        description: str,
    ) -> SkillTriggerDescriptionAttempt:
        description_digest = hashlib.sha256(
            validate_trigger_description(description).encode("utf-8")
        ).hexdigest()
        confirmation = self.optimization_store.matching_confirmation(
            session_id=session_id,
            plan_id=plan_id,
            suite_id=suite.suite_id,
            suite_revision=suite.suite_revision,
            suite_digest=suite.suite_digest,
            description_digest=description_digest,
        )
        if confirmation is None:
            raise SkillCreatorValidationError(
                "Confirm a passing trigger description before continuing.",
                code="skill_trigger_gate_required",
            )
        return confirmation

    def projection(
        self,
        session: SkillCreatorSession,
        plan: SkillResourcePlan | None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "trigger_required": False,
                "trigger_suite": None,
                "trigger_attempt": None,
                "trigger_receipt": None,
                "trigger_stale_reason": None,
            }
        required = self.requires_trigger(session)
        suite = self.trigger_store.current_for_session(session.session_id)
        attempt = None
        stale_reason = None
        receipt = None
        if required and suite is None:
            stale_reason = "skill_trigger_suite_required"
        elif required and suite is not None and suite.state != "confirmed":
            stale_reason = "skill_trigger_suite_required"
        elif suite is not None and suite.definition_digest != _definition_digest(session):
            stale_reason = "definition_changed"
        elif plan is not None and suite is not None and suite.skill_name != plan.skill_name:
            stale_reason = "skill_name_changed"
        elif required and suite is not None and suite.state == "confirmed" and plan is not None:
            try:
                description_digest = hashlib.sha256(
                    validate_trigger_description(plan.skill_description).encode("utf-8")
                ).hexdigest()
                confirmation = self.optimization_store.matching_confirmation(
                    session_id=session.session_id,
                    plan_id=plan.plan_id,
                    suite_id=suite.suite_id,
                    suite_revision=suite.suite_revision,
                    suite_digest=suite.suite_digest,
                    description_digest=description_digest,
                )
                if confirmation is None:
                    stale_reason = "description_unconfirmed"
                    attempt = self.optimization_store.current_for_scope(
                        session_id=session.session_id,
                        plan_id=plan.plan_id,
                        plan_revision=plan.revision,
                        plan_digest=plan.digest,
                        suite_id=suite.suite_id,
                        suite_revision=suite.suite_revision,
                        suite_digest=suite.suite_digest,
                    )
                    if attempt is not None and attempt.candidates:
                        diagnostic = next(
                            (
                                item
                                for item in attempt.candidates
                                if item.description_digest
                                == attempt.recommended_description_digest
                            ),
                            attempt.candidates[0],
                        )
                        receipt = self.trigger_store.require_receipt(
                            diagnostic.receipt_id
                        )
                else:
                    attempt = confirmation
                    receipt = self._evaluate_and_save(
                        suite, plan.skill_name, plan.skill_description
                    )
                    if not receipt.passed:
                        stale_reason = "description_failed"
            except SkillCreatorError as exc:
                stale_reason = str(getattr(exc, "code", "skill_trigger_index_unavailable"))
        return {
            "trigger_required": required,
            "trigger_suite": asdict(suite) if suite is not None else None,
            "trigger_attempt": (
                self.optimization_store.serialize(attempt) if attempt is not None else None
            ),
            "trigger_receipt": asdict(receipt) if receipt is not None else None,
            "trigger_stale_reason": stale_reason,
        }

    def _create_attempt(
        self,
        session: SkillCreatorSession,
        plan: SkillResourcePlan,
        suite: SkillTriggerSuiteV1,
        descriptions: Iterable[str],
    ) -> SkillTriggerDescriptionAttempt:
        unique: dict[str, str] = {}
        for raw in descriptions:
            description = validate_trigger_description(raw)
            unique.setdefault(hashlib.sha256(description.encode("utf-8")).hexdigest(), description)
        if not 1 <= len(unique) <= 3:
            raise SkillCreatorValidationError(
                "Trigger optimizer must produce one to three distinct descriptions.",
                code="skill_trigger_optimizer_invalid",
            )
        candidates = [
            _candidate_from_receipt(description, self._evaluate_and_save(suite, plan.skill_name, description))
            for description in unique.values()
        ]
        return self.optimization_store.create(
            session=session,
            plan=plan,
            suite=suite,
            candidates=candidates,
        )

    def _evaluate_and_save(
        self,
        suite: SkillTriggerSuiteV1,
        skill_name: str,
        description: str,
    ) -> SkillTriggerReceiptV1:
        clean = validate_trigger_description(description)
        receipt = self.evaluator.evaluate(
            suite=suite,
            skill_id=skill_name,
            skill_name=skill_name,
            description=clean,
            sub_path=skill_name,
        )
        return self.trigger_store.save_receipt(receipt)

    def _require_scope(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
    ) -> tuple[SkillCreatorSession, SkillResourcePlan]:
        self.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        if session.session_revision != int(expected_session_revision):
            raise SkillCreatorConflictError("Creator session changed. Reload before continuing.")
        plan = self.planning_service.plan_store.current_for_session(session_id)
        if plan is None:
            raise SkillCreatorValidationError(
                "Create a resource plan before defining trigger boundaries.",
                code="skill_trigger_suite_required",
            )
        if plan.plan_id != _identifier(plan_id, "plan_id"):
            raise SkillCreatorConflictError("Resource plan changed. Reload before continuing.")
        if plan.revision != int(expected_plan_revision) or plan.digest != str(expected_plan_digest).lower():
            raise SkillCreatorConflictError("Resource plan changed. Reload before continuing.")
        self.planning_service._require_plan_scope(plan, session=session, draft=draft)
        if plan.state == "confirmed":
            raise SkillCreatorConflictError("A confirmed resource plan cannot change its trigger contract.")
        return session, plan

    def _require_confirmed_suite(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        plan_id: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
    ) -> tuple[SkillCreatorSession, SkillResourcePlan, SkillTriggerSuiteV1]:
        session, plan = self._require_scope(
            session_id,
            expected_session_revision=expected_session_revision,
            plan_id=plan_id,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
        )
        suite = self._current_confirmed_suite(session, skill_name=plan.skill_name)
        if suite.suite_revision != int(expected_suite_revision) or suite.suite_digest != str(expected_suite_digest).lower():
            raise SkillCreatorConflictError("Trigger suite changed. Reload before continuing.")
        return session, plan, suite

    def _current_confirmed_suite(
        self,
        session: SkillCreatorSession,
        *,
        skill_name: str,
    ) -> SkillTriggerSuiteV1:
        suite = self.trigger_store.current_for_session(session.session_id)
        if suite is None or suite.state != "confirmed":
            raise SkillCreatorValidationError(
                "Confirm should-trigger and should-not-trigger cases first.",
                code="skill_trigger_suite_required",
            )
        if suite.definition_digest != _definition_digest(session) or suite.skill_name != skill_name:
            raise SkillCreatorConflictError(
                "The trigger suite no longer matches the Creator definition.",
                code="skill_trigger_suite_stale",
            )
        return suite

    def _require_executor(self) -> TriggerOptimizationExecutor:
        executor = self.executor
        try:
            available = bool(executor and executor.available())
        except Exception:
            available = False
        if not available or executor is None:
            raise SkillCreatorValidationError(
                "The model gateway is not configured for trigger optimization.",
                code="skill_trigger_optimizer_unconfigured",
            )
        return executor

    @staticmethod
    def _suite_context(session: SkillCreatorSession, plan: SkillResourcePlan) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "skill_name": plan.skill_name,
            "current_description": _model_safe_current_description(plan.skill_description),
            "intent": session.intent,
            "positive_examples": list(session.positive_examples),
            "near_miss_examples": list(session.near_miss_examples),
        }

    def _description_context(
        self,
        session: SkillCreatorSession,
        plan: SkillResourcePlan,
        suite: SkillTriggerSuiteV1,
        seed: SkillTriggerReceiptV1,
    ) -> dict[str, Any]:
        public_by_id = {
            str(item.get("candidateId") or ""): item
            for item in self.evaluator.finder.candidates()
            if item.get("sourceType") == "catalog"
        }
        competitor_ids: list[str] = []
        for result in seed.case_results:
            for domain in (result.finder, result.router):
                for competitor in domain.competitors:
                    if competitor.candidate_id in public_by_id and competitor.candidate_id not in competitor_ids:
                        competitor_ids.append(competitor.candidate_id)
        competitors = []
        for candidate_id in competitor_ids[:24]:
            item = public_by_id[candidate_id]
            competitors.append(
                {
                    "name": str(item.get("name") or "")[:120],
                    "category": str(item.get("category") or "")[:80],
                    "description": str(item.get("description") or "")[:320],
                }
            )
        return {
            "session_id": session.session_id,
            "skill_name": plan.skill_name,
            "current_description": _model_safe_current_description(plan.skill_description),
            "intent": session.intent,
            "cases": [
                {"kind": item.kind, "text": item.text}
                for item in suite.cases
            ],
            "public_competitors": competitors,
        }


def validate_trigger_description(value: Any) -> str:
    if not isinstance(value, str):
        raise SkillCreatorValidationError(
            "Skill description must be text.", code="skill_trigger_description_invalid"
        )
    clean = value.strip()
    if not clean or len(clean) > 600 or "\n" in clean or "\r" in clean:
        raise SkillCreatorValidationError(
            "Skill description must be one non-empty line of at most 600 characters.",
            code="skill_trigger_description_invalid",
        )
    if scan_skill_package_credentials(skill_markdown=clean):
        raise SkillCreatorValidationError(
            "Skill description contains credential-like content.",
            code="skill_trigger_description_invalid",
        )
    lowered = clean.casefold()
    if any(token in lowered for token in ("todo", "tbd", "lorem ipsum", "填入", "待补充", "占位符")):
        raise SkillCreatorValidationError(
            "Skill description contains placeholder content.",
            code="skill_trigger_description_invalid",
        )
    if clean.startswith(("---", "{", "[", "!", "&", "*", "|", ">")) or "---" in clean:
        raise SkillCreatorValidationError(
            "Skill description contains YAML control content.",
            code="skill_trigger_description_invalid",
        )
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}", lowered)
    counts = {word: words.count(word) for word in set(words)}
    repeated_cjk = re.search(
        r"([\u3400-\u9fff]{2,12})(?:[\s,，、;；:：/-]*\1){3,}", clean
    )
    if any(count > 3 for count in counts.values()) or repeated_cjk:
        raise SkillCreatorValidationError(
            "Skill description appears to repeat keywords excessively.",
            code="skill_trigger_description_invalid",
        )
    return clean


def _model_safe_current_description(value: Any) -> str:
    clean = " ".join(str(value or "").split())
    if scan_skill_package_credentials(skill_markdown=clean):
        raise SkillCreatorValidationError(
            "Skill description contains credential-like content.",
            code="skill_trigger_description_invalid",
        )
    return validate_trigger_description(clean[:600])


def _candidate_from_receipt(
    description: str,
    receipt: SkillTriggerReceiptV1,
) -> SkillTriggerDescriptionCandidate:
    positive_ranks: list[int] = []
    negative_distances: list[int] = []
    for result in receipt.case_results:
        finder_rank = result.finder.rank_top_24 or 25
        router_rank = result.router.rank_top_24 or 25
        if result.kind == "should_trigger":
            positive_ranks.extend((finder_rank, router_rank))
        elif result.kind == "should_not_trigger":
            negative_distances.extend((finder_rank, router_rank))
    return SkillTriggerDescriptionCandidate(
        description=description,
        description_digest=receipt.description_digest,
        receipt_id=receipt.receipt_id,
        passed=receipt.passed,
        worst_positive_rank=max(positive_ranks or [25]),
        positive_rank_sum=sum(positive_ranks),
        negative_safety_distance=min(negative_distances or [25]),
    )


def _candidate_sort_key(item: SkillTriggerDescriptionCandidate) -> tuple[Any, ...]:
    return (
        0 if item.passed else 1,
        item.worst_positive_rank,
        item.positive_rank_sum,
        -item.negative_safety_distance,
        len(item.description),
        item.description_digest,
    )


def _definition_digest(session: SkillCreatorSession) -> str:
    return trigger_definition_digest(
        intent=session.intent,
        positive_examples=session.positive_examples,
        near_miss_examples=session.near_miss_examples,
    )


def _decode_attempt(raw: Any) -> SkillTriggerDescriptionAttempt:
    if not isinstance(raw, Mapping):
        raise ValueError("Trigger attempt record must be an object.")
    candidates_raw = raw.get("candidates")
    if not isinstance(candidates_raw, list):
        raise ValueError("Trigger attempt candidates are invalid.")
    if not 1 <= len(candidates_raw) <= 3 or any(not isinstance(item, Mapping) for item in candidates_raw):
        raise ValueError("Trigger attempt candidates are invalid.")
    candidates = tuple(
        SkillTriggerDescriptionCandidate(
            description=validate_trigger_description(item.get("description")),
            description_digest=_digest(item.get("description_digest"), "description_digest"),
            receipt_id=_identifier(item.get("receipt_id"), "receipt_id"),
            passed=bool(item.get("passed")),
            worst_positive_rank=int(item.get("worst_positive_rank")),
            positive_rank_sum=int(item.get("positive_rank_sum")),
            negative_safety_distance=int(item.get("negative_safety_distance")),
        )
        for item in candidates_raw
    )
    if any(
        not 1 <= candidate.worst_positive_rank <= 25
        or not 0 <= candidate.positive_rank_sum <= 1_000
        or not 1 <= candidate.negative_safety_distance <= 25
        for candidate in candidates
    ):
        raise ValueError("Trigger attempt ranking metrics are invalid.")
    values = dict(raw)
    values["candidates"] = candidates
    item = SkillTriggerDescriptionAttempt(**values)
    if item.version != TRIGGER_ATTEMPT_VERSION or item.state not in {"evaluated", "confirmed"}:
        raise ValueError("Trigger attempt contract is invalid.")
    if not math.isfinite(float(item.created_at)) or (
        item.confirmed_at is not None and not math.isfinite(float(item.confirmed_at))
    ):
        raise ValueError("Trigger attempt timestamps are invalid.")
    if item.state == "confirmed":
        if item.confirmed_at is None or not any(
            candidate.passed
            and candidate.description_digest == item.selected_description_digest
            for candidate in item.candidates
        ):
            raise ValueError("Confirmed trigger attempt selection is invalid.")
    elif item.confirmed_at is not None or item.selected_description_digest is not None:
        raise ValueError("Unconfirmed trigger attempt contains confirmation state.")
    if item.recommended_description_digest is not None and not any(
        candidate.passed
        and candidate.description_digest == item.recommended_description_digest
        for candidate in item.candidates
    ):
        raise ValueError("Trigger attempt recommendation is invalid.")
    if item.recommended_description_digest is None and any(
        candidate.passed for candidate in item.candidates
    ):
        raise ValueError("Passing trigger attempt is missing its recommendation.")
    expected = {
        key: value
        for key, value in asdict(item).items()
        if key not in {"attempt_id", "revision", "digest", "created_at", "confirmed_at"}
    }
    if _sha256(expected) != item.digest:
        raise ValueError("Trigger attempt digest is invalid.")
    return item


def _identifier(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", clean):
        raise SkillCreatorValidationError(f"Invalid {field_name}.")
    return clean


def _digest(value: Any, field_name: str) -> str:
    clean = str(value or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", clean):
        raise SkillCreatorValidationError(f"Invalid {field_name}.")
    return clean


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
