from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping

from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .package_validation import scan_skill_package_credentials


EVOLUTION_PLAN_VERSION = "skill-evolution-plan-v1"
EvolutionPlanState = Literal["needs_input", "needs_regeneration", "ready", "confirmed", "stale"]
EvolutionActionKind = Literal["keep", "update", "create", "delete"]
EvolutionResourceKind = Literal["script", "reference", "asset"]

_RESOURCE_ROOTS = {"script": "scripts", "reference": "references", "asset": "assets"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


@dataclass(frozen=True, slots=True)
class SkillEvolutionQuestion:
    question_id: str
    question: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillEvolutionDiagnosis:
    case_id: str
    evidence_item_ids: tuple[str, ...]
    failure_types: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]
    sections: tuple[str, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class SkillEvolutionAction:
    action_id: str
    action: EvolutionActionKind
    resource_id: str
    kind: EvolutionResourceKind
    path: str
    purpose: str
    source_ids: tuple[str, ...]
    used_by_steps: tuple[str, ...]
    depends_on: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    related_case_ids: tuple[str, ...]
    expected_improvement: str
    non_regression_case_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillEvolutionPlan:
    plan_id: str
    version: str
    revision: int
    digest: str
    state: EvolutionPlanState
    session_id: str
    session_revision: int
    draft_id: str
    draft_state_revision: int
    draft_revision: int
    draft_digest: str
    evaluation_run_id: str
    evaluation_run_revision: int
    review_id: str
    review_revision: int
    suite_id: str
    suite_revision: int
    suite_digest: str
    resource_plan_id: str
    resource_plan_revision: int
    resource_plan_digest: str
    diagnoses: tuple[SkillEvolutionDiagnosis, ...]
    actions: tuple[SkillEvolutionAction, ...]
    workflow_steps: tuple[dict[str, str], ...]
    output_contract: tuple[str, ...]
    failure_modes: tuple[str, ...]
    expected_improvements: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    non_goals: tuple[str, ...]
    overfitting_risks: tuple[str, ...]
    clarifications: tuple[SkillEvolutionQuestion, ...] = ()
    clarification_answers: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SkillEvolutionPlanStore:
    """Append-only, fail-closed evolution plans bound to frozen review facts."""

    SCHEMA_VERSION = 1
    MAX_PLANS = 500
    MAX_REVISIONS = 40
    MAX_ACTIONS = 20
    MAX_ANSWER_BYTES = 32 * 1024
    MAX_ANSWERS_BYTES = 128 * 1024

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        configured = os.getenv("SKILL_CREATOR_EVOLUTION_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or configured or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_creator_evolution_plans.json"
        self._lock = threading.RLock()
        self._items: dict[str, list[SkillEvolutionPlan]] = {}
        self._session_index: dict[str, str] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self._load_error is None,
                "plan_count": len(self._items),
                "quarantine_count": len(self._quarantine),
            }

    def current_for_session(self, session_id: str) -> SkillEvolutionPlan | None:
        clean = self._identifier(session_id, "session_id")
        with self._lock:
            self._ensure_readable_unlocked()
            plan_id = self._session_index.get(clean)
            return copy.deepcopy(self._items[plan_id][-1]) if plan_id else None

    def require(self, plan_id: str, revision: int | None = None) -> SkillEvolutionPlan:
        clean = self._identifier(plan_id, "plan_id")
        with self._lock:
            self._ensure_readable_unlocked()
            revisions = self._items.get(clean)
            if not revisions:
                raise SkillCreatorNotFoundError(f"Evolution plan not found: {clean}")
            if revision is None:
                return copy.deepcopy(revisions[-1])
            for item in revisions:
                if item.revision == int(revision):
                    return copy.deepcopy(item)
        raise SkillCreatorNotFoundError(f"Evolution plan revision not found: {clean}@{revision}")

    def save_generated(
        self,
        *,
        bindings: Mapping[str, Any],
        payload: Mapping[str, Any],
        allowed_case_ids: set[str],
        allowed_item_ids: set[str],
        allowed_requirement_ids: set[str],
        allowed_resources: Mapping[str, Mapping[str, str]],
        allowed_source_ids: set[str],
        allowed_step_ids: set[str],
        expected_plan_revision: int | None,
        expected_plan_digest: str | None,
    ) -> SkillEvolutionPlan:
        clean_bindings = self._bindings(bindings)
        normalized = self._normalize_payload(
            payload,
            allowed_case_ids=allowed_case_ids,
            allowed_item_ids=allowed_item_ids,
            allowed_requirement_ids=allowed_requirement_ids,
            allowed_resources=allowed_resources,
            allowed_source_ids=allowed_source_ids,
            allowed_step_ids=allowed_step_ids,
        )
        with self._lock:
            self._ensure_writable_unlocked()
            current = self._current_unlocked(clean_bindings["session_id"])
            self._require_expected(current, expected_plan_revision, expected_plan_digest)
            if current is None and len(self._items) >= self.MAX_PLANS:
                raise SkillCreatorValidationError(
                    "Skill evolution plan limit reached.", code="skill_creator_evolution_plan_limit"
                )
            if current is not None and current.state == "confirmed" and self._same_bindings(current, clean_bindings):
                raise SkillCreatorConflictError(
                    "A confirmed evolution plan cannot be regenerated for the same review."
                )
            plan_id = current.plan_id if current else f"skillevo_{uuid.uuid4().hex}"
            revision = current.revision + 1 if current else 1
            now = time.time()
            item = self._build(
                plan_id=plan_id,
                version=EVOLUTION_PLAN_VERSION,
                revision=revision,
                state="needs_input" if normalized["clarifications"] else "ready",
                clarification_answers=(
                    dict(current.clarification_answers)
                    if current is not None and self._same_bindings(current, clean_bindings)
                    else {}
                ),
                created_at=(current.created_at if current else now),
                updated_at=now,
                **clean_bindings,
                **normalized,
            )
            return self._append_unlocked(item)

    def save_answers(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        answers: Mapping[str, Any],
    ) -> SkillEvolutionPlan:
        with self._lock:
            current = self._require_current_unlocked(plan_id, expected_revision, expected_digest)
            if current.state != "needs_input":
                raise SkillCreatorConflictError("This evolution plan is not waiting for answers.")
            question_ids = {item.question_id for item in current.clarifications}
            if set(answers) != question_ids:
                raise SkillCreatorValidationError(
                    "Answer every current evolution question exactly once.",
                    code="skill_creator_evolution_answers_incomplete",
                )
            clean_answers = {
                self._identifier(key, "question_id"): self._required_text(value, "answer", self.MAX_ANSWER_BYTES)
                for key, value in answers.items()
            }
            if sum(len(value.encode("utf-8")) for value in clean_answers.values()) > self.MAX_ANSWERS_BYTES:
                raise SkillCreatorValidationError(
                    "Skill evolution answers are too large.", code="skill_creator_evolution_answers_too_large"
                )
            self._reject_credentials(clean_answers)
            return self._append_unlocked(
                self._replace(
                    current,
                    revision=current.revision + 1,
                    state="needs_regeneration",
                    clarification_answers=clean_answers,
                    updated_at=time.time(),
                )
            )

    def patch(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        changes: Mapping[str, Any],
        allowed_case_ids: set[str],
        allowed_item_ids: set[str],
        allowed_requirement_ids: set[str],
        allowed_resources: Mapping[str, Mapping[str, str]],
        allowed_source_ids: set[str],
        allowed_step_ids: set[str],
    ) -> SkillEvolutionPlan:
        allowed = {
            "actions", "workflow_steps", "output_contract", "failure_modes",
            "expected_improvements", "acceptance_criteria", "non_goals", "overfitting_risks",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise SkillCreatorValidationError(
                "Unsupported evolution plan fields: " + ", ".join(unknown),
                code="skill_creator_evolution_plan_invalid",
            )
        with self._lock:
            current = self._require_current_unlocked(plan_id, expected_revision, expected_digest)
            if current.state != "ready":
                raise SkillCreatorConflictError("Only a ready evolution plan can be edited.")
            payload = {
                "diagnoses": [asdict(item) for item in current.diagnoses],
                "actions": [asdict(item) for item in current.actions],
                "workflow_steps": list(current.workflow_steps),
                "output_contract": list(current.output_contract),
                "failure_modes": list(current.failure_modes),
                "expected_improvements": list(current.expected_improvements),
                "acceptance_criteria": list(current.acceptance_criteria),
                "non_goals": list(current.non_goals),
                "overfitting_risks": list(current.overfitting_risks),
                "clarifications": [],
            }
            payload.update(dict(changes))
            normalized = self._normalize_payload(
                payload,
                allowed_case_ids=allowed_case_ids,
                allowed_item_ids=allowed_item_ids,
                allowed_requirement_ids=allowed_requirement_ids,
                allowed_resources=allowed_resources,
                allowed_source_ids=allowed_source_ids,
                allowed_step_ids=allowed_step_ids,
            )
            return self._append_unlocked(
                self._replace(
                    current,
                    revision=current.revision + 1,
                    state="ready",
                    updated_at=time.time(),
                    **normalized,
                )
            )

    def confirm(self, plan_id: str, *, expected_revision: int, expected_digest: str) -> SkillEvolutionPlan:
        with self._lock:
            current = self._require_current_unlocked(plan_id, expected_revision, expected_digest)
            if current.state != "ready":
                raise SkillCreatorConflictError("Only a ready evolution plan can be confirmed.")
            return self._append_unlocked(
                self._replace(
                    current,
                    revision=current.revision + 1,
                    state="confirmed",
                    updated_at=time.time(),
                )
            )

    def mark_stale(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> SkillEvolutionPlan:
        with self._lock:
            current = self._require_current_unlocked(
                plan_id,
                expected_revision,
                expected_digest,
            )
            if current.state == "stale":
                return copy.deepcopy(current)
            return self._append_unlocked(
                self._replace(current, revision=current.revision + 1, state="stale", updated_at=time.time())
            )

    @staticmethod
    def serialize(item: SkillEvolutionPlan) -> dict[str, Any]:
        return asdict(item)

    def _normalize_payload(
        self,
        payload: Mapping[str, Any],
        *,
        allowed_case_ids: set[str],
        allowed_item_ids: set[str],
        allowed_requirement_ids: set[str],
        allowed_resources: Mapping[str, Mapping[str, str]],
        allowed_source_ids: set[str],
        allowed_step_ids: set[str],
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise SkillCreatorValidationError(
                "Skill evolution planner returned an invalid object.", code="skill_creator_evolution_plan_invalid"
            )
        diagnoses = self._diagnoses(
            payload.get("diagnoses"), allowed_case_ids, allowed_item_ids,
            allowed_requirement_ids, set(allowed_resources),
        )
        workflow_steps = self._workflow_steps(payload.get("workflow_steps"), allowed_step_ids)
        step_ids = {item["step_id"] for item in workflow_steps}
        actions = self._actions(
            payload.get("actions"), allowed_case_ids, allowed_resources,
            allowed_source_ids, step_ids,
        )
        normalized = {
            "diagnoses": diagnoses,
            "actions": actions,
            "workflow_steps": workflow_steps,
            "output_contract": self._text_list(payload.get("output_contract"), "output_contract", 20, 2_000, minimum=1),
            "failure_modes": self._text_list(payload.get("failure_modes"), "failure_modes", 20, 2_000, minimum=1),
            "expected_improvements": self._text_list(payload.get("expected_improvements"), "expected_improvements", 20, 2_000, minimum=1),
            "acceptance_criteria": self._text_list(payload.get("acceptance_criteria"), "acceptance_criteria", 30, 2_000, minimum=1),
            "non_goals": self._text_list(payload.get("non_goals"), "non_goals", 20, 2_000),
            "overfitting_risks": self._text_list(payload.get("overfitting_risks"), "overfitting_risks", 20, 2_000, minimum=1),
            "clarifications": self._questions(payload.get("clarifications", [])),
        }
        self._reject_credentials(normalized)
        return normalized

    def _diagnoses(
        self, value: Any, allowed_cases: set[str], allowed_items: set[str],
        allowed_requirements: set[str], allowed_resources: set[str],
    ) -> tuple[SkillEvolutionDiagnosis, ...]:
        if not isinstance(value, list) or not value or len(value) > 12:
            raise SkillCreatorValidationError("Evolution plan requires one to twelve diagnoses.", code="skill_creator_evolution_plan_invalid")
        result: list[SkillEvolutionDiagnosis] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, Mapping):
                raise SkillCreatorValidationError("Invalid evolution diagnosis.", code="skill_creator_evolution_plan_invalid")
            case_id = self._identifier(raw.get("case_id"), "case_id")
            if case_id not in allowed_cases or case_id in seen:
                raise SkillCreatorValidationError("Evolution diagnosis references an unknown or duplicate case.", code="skill_creator_evolution_evidence_invalid")
            seen.add(case_id)
            item_ids = self._identifier_list(raw.get("evidence_item_ids"), "evidence_item_ids", 18, allowed_items, minimum=1)
            result.append(SkillEvolutionDiagnosis(
                case_id=case_id,
                evidence_item_ids=item_ids,
                failure_types=self._identifier_list(raw.get("failure_types"), "failure_types", 8, None, minimum=1),
                requirement_ids=self._identifier_list(raw.get("requirement_ids", []), "requirement_ids", 30, allowed_requirements),
                resource_ids=self._identifier_list(raw.get("resource_ids", []), "resource_ids", 20, allowed_resources),
                sections=self._text_list(raw.get("sections", []), "sections", 20, 300),
                summary=self._required_text(raw.get("summary"), "diagnosis summary", 2_000),
            ))
        return tuple(result)

    def _actions(
        self, value: Any, allowed_cases: set[str], allowed_resources: Mapping[str, Mapping[str, str]],
        allowed_source_ids: set[str], allowed_step_ids: set[str],
    ) -> tuple[SkillEvolutionAction, ...]:
        if not isinstance(value, list) or len(value) > self.MAX_ACTIONS:
            raise SkillCreatorValidationError("Invalid evolution resource actions.", code="skill_creator_evolution_plan_invalid")
        resolved: list[tuple[Mapping[str, Any], str, str, str, str]] = []
        all_resource_ids = set(allowed_resources)
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise SkillCreatorValidationError("Invalid evolution resource action.", code="skill_creator_evolution_plan_invalid")
            action = str(raw.get("action") or "").strip().lower()
            if action not in {"keep", "update", "create", "delete"}:
                raise SkillCreatorValidationError("Invalid evolution resource action.", code="skill_creator_evolution_plan_invalid")
            if action == "create":
                kind = str(raw.get("kind") or "").strip().lower()
                if kind not in _RESOURCE_ROOTS:
                    raise SkillCreatorValidationError("Invalid evolution resource kind.", code="skill_creator_evolution_plan_invalid")
                path = self._resource_path(raw.get("path"), kind)
                resource_id = self._resource_id(kind, path)
                if resource_id in all_resource_ids:
                    raise SkillCreatorValidationError("A create action cannot target an existing resource.", code="skill_creator_evolution_resource_invalid")
                all_resource_ids.add(resource_id)
            else:
                resource_id = self._identifier(raw.get("resource_id"), "resource_id")
                frozen = allowed_resources.get(resource_id)
                if frozen is None:
                    raise SkillCreatorValidationError("Evolution action references an unknown resource.", code="skill_creator_evolution_resource_invalid")
                kind = str(frozen.get("kind") or "")
                path = str(frozen.get("path") or "")
                if raw.get("path") not in {None, "", path} or raw.get("kind") not in {None, "", kind}:
                    raise SkillCreatorValidationError("Existing resource identity is server-controlled.", code="skill_creator_evolution_resource_invalid")
            action_id = self._identifier(raw.get("action_id") or f"evolution-action-{index + 1}", "action_id")
            resolved.append((raw, action, resource_id, kind, path))
        result: list[SkillEvolutionAction] = []
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        covered_existing: set[str] = set()
        deleted_resource_ids = {
            resource_id
            for _raw, action, resource_id, _kind, _path in resolved
            if action == "delete"
        }
        for index, (raw, action, resource_id, kind, path) in enumerate(resolved):
            if action != "create":
                covered_existing.add(resource_id)
            action_id = self._identifier(raw.get("action_id") or f"evolution-action-{index + 1}", "action_id")
            if action_id in seen_ids or path.casefold() in seen_paths:
                raise SkillCreatorValidationError("Evolution actions must have unique IDs and paths.", code="skill_creator_evolution_resource_invalid")
            seen_ids.add(action_id)
            seen_paths.add(path.casefold())
            depends_on = self._identifier_list(
                raw.get("depends_on", []),
                "depends_on",
                20,
                all_resource_ids - {resource_id},
            )
            if (action == "delete" and depends_on) or (
                action != "delete" and deleted_resource_ids.intersection(depends_on)
            ):
                raise SkillCreatorValidationError(
                    "Active resources cannot depend on a resource scheduled for deletion.",
                    code="skill_creator_evolution_resource_invalid",
                )
            result.append(SkillEvolutionAction(
                action_id=action_id,
                action=action,  # type: ignore[arg-type]
                resource_id=resource_id,
                kind=kind,  # type: ignore[arg-type]
                path=path,
                purpose=self._required_text(raw.get("purpose"), "resource purpose", 2_000),
                source_ids=self._identifier_list(raw.get("source_ids", []), "source_ids", 30, allowed_source_ids),
                used_by_steps=self._identifier_list(raw.get("used_by_steps", []), "used_by_steps", 20, allowed_step_ids),
                depends_on=depends_on,
                acceptance_checks=self._text_list(raw.get("acceptance_checks"), "acceptance_checks", 20, 1_000, minimum=1),
                related_case_ids=self._identifier_list(raw.get("related_case_ids"), "related_case_ids", 12, allowed_cases, minimum=1),
                expected_improvement=self._required_text(raw.get("expected_improvement"), "expected_improvement", 2_000),
                non_regression_case_ids=self._identifier_list(raw.get("non_regression_case_ids", []), "non_regression_case_ids", 12, allowed_cases),
            ))
        missing = set(allowed_resources) - covered_existing
        if missing:
            raise SkillCreatorValidationError("Evolution plan must account for every existing resource.", code="skill_creator_evolution_resource_incomplete")
        return tuple(result)

    def _workflow_steps(self, value: Any, allowed_step_ids: set[str]) -> tuple[dict[str, str], ...]:
        if not isinstance(value, list) or not 4 <= len(value) <= 10:
            raise SkillCreatorValidationError("Evolution workflow must contain four to ten steps.", code="skill_creator_evolution_plan_invalid")
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise SkillCreatorValidationError("Invalid evolution workflow step.", code="skill_creator_evolution_plan_invalid")
            step_id = self._identifier(raw.get("step_id") or raw.get("id") or f"step-{index + 1}", "step_id")
            if step_id in seen:
                raise SkillCreatorValidationError("Evolution workflow step IDs must be unique.", code="skill_creator_evolution_plan_invalid")
            seen.add(step_id)
            result.append({"step_id": step_id, "instruction": self._required_text(raw.get("instruction") or raw.get("description"), "workflow instruction", 2_000)})
        # Existing IDs may be retained, replaced, or augmented; the server still
        # freezes all resource references against the IDs in this result.
        _ = allowed_step_ids
        return tuple(result)

    def _questions(self, value: Any) -> tuple[SkillEvolutionQuestion, ...]:
        if not isinstance(value, list) or len(value) > 5:
            raise SkillCreatorValidationError("Evolution planner may ask at most five questions.", code="skill_creator_evolution_plan_invalid")
        result: list[SkillEvolutionQuestion] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, Mapping):
                raise SkillCreatorValidationError("Invalid evolution clarification.", code="skill_creator_evolution_plan_invalid")
            question_id = self._identifier(raw.get("question_id") or raw.get("id"), "question_id")
            if question_id in seen:
                raise SkillCreatorValidationError("Evolution question IDs must be unique.", code="skill_creator_evolution_plan_invalid")
            seen.add(question_id)
            result.append(SkillEvolutionQuestion(
                question_id=question_id,
                question=self._required_text(raw.get("question"), "question", 2_000),
                reason=self._required_text(raw.get("reason"), "reason", 1_000),
            ))
        return tuple(result)

    def _bindings(self, value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {
            "session_id": self._identifier(value.get("session_id"), "session_id"),
            "session_revision": self._positive_int(value.get("session_revision"), "session_revision"),
            "draft_id": self._identifier(value.get("draft_id"), "draft_id"),
            "draft_state_revision": self._positive_int(value.get("draft_state_revision"), "draft_state_revision"),
            "draft_revision": self._positive_int(value.get("draft_revision"), "draft_revision"),
            "draft_digest": self._digest(value.get("draft_digest"), "draft_digest"),
            "evaluation_run_id": self._identifier(value.get("evaluation_run_id"), "evaluation_run_id"),
            "evaluation_run_revision": self._positive_int(value.get("evaluation_run_revision"), "evaluation_run_revision"),
            "review_id": self._identifier(value.get("review_id"), "review_id"),
            "review_revision": self._positive_int(value.get("review_revision"), "review_revision"),
            "suite_id": self._identifier(value.get("suite_id"), "suite_id"),
            "suite_revision": self._positive_int(value.get("suite_revision"), "suite_revision"),
            "suite_digest": self._digest(value.get("suite_digest"), "suite_digest"),
            "resource_plan_id": self._identifier(value.get("resource_plan_id"), "resource_plan_id"),
            "resource_plan_revision": self._positive_int(value.get("resource_plan_revision"), "resource_plan_revision"),
            "resource_plan_digest": self._digest(value.get("resource_plan_digest"), "resource_plan_digest"),
        }
        return fields

    @staticmethod
    def _same_bindings(item: SkillEvolutionPlan, bindings: Mapping[str, Any]) -> bool:
        return all(getattr(item, key) == value for key, value in bindings.items())

    def _build(self, **values: Any) -> SkillEvolutionPlan:
        digest_values = {key: value for key, value in values.items() if key not in {"digest", "created_at", "updated_at"}}
        digest = hashlib.sha256(self._canonical_json(self._jsonable(digest_values)).encode("utf-8")).hexdigest()
        return SkillEvolutionPlan(digest=digest, **values)

    def _replace(self, current: SkillEvolutionPlan, **changes: Any) -> SkillEvolutionPlan:
        values = asdict(current)
        values.pop("digest", None)
        values.update(changes)
        values["diagnoses"] = tuple(
            item if isinstance(item, SkillEvolutionDiagnosis) else SkillEvolutionDiagnosis(**item)
            for item in values["diagnoses"]
        )
        values["actions"] = tuple(
            item if isinstance(item, SkillEvolutionAction) else SkillEvolutionAction(**item)
            for item in values["actions"]
        )
        values["clarifications"] = tuple(
            item if isinstance(item, SkillEvolutionQuestion) else SkillEvolutionQuestion(**item)
            for item in values["clarifications"]
        )
        values["workflow_steps"] = tuple(values["workflow_steps"])
        for key in ("output_contract", "failure_modes", "expected_improvements", "acceptance_criteria", "non_goals", "overfitting_risks"):
            values[key] = tuple(values[key])
        return self._build(**values)

    def _append_unlocked(self, item: SkillEvolutionPlan) -> SkillEvolutionPlan:
        revisions = self._items.setdefault(item.plan_id, [])
        if revisions and item.revision != revisions[-1].revision + 1:
            raise SkillCreatorConflictError("Evolution plan revision is not sequential.")
        if len(revisions) >= self.MAX_REVISIONS:
            raise SkillCreatorValidationError("Evolution plan revision limit reached.", code="skill_creator_evolution_plan_limit")
        previous_index = self._session_index.get(item.session_id)
        revisions.append(item)
        self._session_index[item.session_id] = item.plan_id
        try:
            self._save_unlocked()
        except BaseException:
            revisions.pop()
            if not revisions:
                self._items.pop(item.plan_id, None)
            if previous_index is None:
                self._session_index.pop(item.session_id, None)
            else:
                self._session_index[item.session_id] = previous_index
            raise
        return copy.deepcopy(item)

    def _require_current_unlocked(
        self, plan_id: str, expected_revision: int | None, expected_digest: str | None,
        *, require_expected: bool = True,
    ) -> SkillEvolutionPlan:
        self._ensure_writable_unlocked()
        clean = self._identifier(plan_id, "plan_id")
        revisions = self._items.get(clean)
        if not revisions:
            raise SkillCreatorNotFoundError(f"Evolution plan not found: {clean}")
        current = revisions[-1]
        if require_expected:
            self._require_expected(current, expected_revision, expected_digest)
        return current

    @staticmethod
    def _require_expected(current: SkillEvolutionPlan | None, revision: int | None, digest: str | None) -> None:
        if current is None:
            if revision is not None or digest is not None:
                raise SkillCreatorConflictError("Evolution plan changed. Reload before continuing.")
            return
        if revision is None or digest is None or current.revision != int(revision) or current.digest != str(digest).lower():
            raise SkillCreatorConflictError("Evolution plan changed. Reload before continuing.")

    def _current_unlocked(self, session_id: str) -> SkillEvolutionPlan | None:
        plan_id = self._session_index.get(session_id)
        return self._items[plan_id][-1] if plan_id else None

    def _load(self) -> None:
        with self._lock:
            if not self.snapshot_path.exists():
                return
            try:
                raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION or not isinstance(raw.get("items"), list):
                    raise ValueError("unsupported snapshot structure")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._load_error = f"skill_evolution_store_corrupt: {str(exc)[:300]}"
                return
            quarantine = raw.get("quarantine", [])
            if isinstance(quarantine, list):
                self._quarantine = [dict(item) for item in quarantine if isinstance(item, dict)][: self.MAX_PLANS]
            sanitized = False
            for index, record in enumerate(raw["items"]):
                try:
                    item = self._decode(record)
                    revisions = self._items.get(item.plan_id)
                    if revisions is None:
                        if item.revision != 1:
                            raise ValueError("evolution revision history must start at one")
                    elif item.revision != revisions[-1].revision + 1:
                        raise ValueError("non-sequential evolution revision")
                    existing = self._session_index.get(item.session_id)
                    if existing not in {None, item.plan_id}:
                        raise ValueError("multiple evolution plans for one session")
                    if revisions is None:
                        revisions = []
                        self._items[item.plan_id] = revisions
                    revisions.append(item)
                    self._session_index[item.session_id] = item.plan_id
                except Exception:
                    self._quarantine.append(self._quarantine_record(record, index))
                    sanitized = True
            if sanitized:
                try:
                    self._save_unlocked()
                except OSError as exc:
                    self._load_error = f"skill_evolution_store_sanitize_failed: {str(exc)[:300]}"

    def _decode(self, record: Any) -> SkillEvolutionPlan:
        if not isinstance(record, dict):
            raise TypeError("evolution record must be an object")
        values = dict(record)
        diagnoses = []
        for raw in values.get("diagnoses", []):
            item = dict(raw)
            for key in ("evidence_item_ids", "failure_types", "requirement_ids", "resource_ids", "sections"):
                item[key] = tuple(item.get(key, []))
            diagnoses.append(SkillEvolutionDiagnosis(**item))
        values["diagnoses"] = tuple(diagnoses)
        actions = []
        for raw in values.get("actions", []):
            item = dict(raw)
            for key in (
                "source_ids",
                "used_by_steps",
                "depends_on",
                "acceptance_checks",
                "related_case_ids",
                "non_regression_case_ids",
            ):
                item[key] = tuple(item.get(key, []))
            actions.append(SkillEvolutionAction(**item))
        values["actions"] = tuple(actions)
        values["clarifications"] = tuple(SkillEvolutionQuestion(**item) for item in values.get("clarifications", []))
        values["workflow_steps"] = tuple(values.get("workflow_steps", []))
        for key in ("output_contract", "failure_modes", "expected_improvements", "acceptance_criteria", "non_goals", "overfitting_risks"):
            values[key] = tuple(values.get(key, []))
        item = SkillEvolutionPlan(**values)
        if item.version != EVOLUTION_PLAN_VERSION or item.state not in {"needs_input", "needs_regeneration", "ready", "confirmed", "stale"}:
            raise ValueError("unsupported evolution plan")
        rebuilt = self._build(**{key: value for key, value in asdict(item).items() if key != "digest"})
        if rebuilt.digest != item.digest:
            raise ValueError("evolution plan digest mismatch")
        self._validate_decoded_payload(item)
        self._reject_credentials(asdict(item))
        return item

    def _validate_decoded_payload(self, item: SkillEvolutionPlan) -> None:
        case_ids = {
            case_id
            for diagnosis in item.diagnoses
            for case_id in (diagnosis.case_id,)
        } | {
            case_id
            for action in item.actions
            for case_id in (*action.related_case_ids, *action.non_regression_case_ids)
        }
        item_ids = {
            item_id
            for diagnosis in item.diagnoses
            for item_id in diagnosis.evidence_item_ids
        }
        requirement_ids = {
            requirement_id
            for diagnosis in item.diagnoses
            for requirement_id in diagnosis.requirement_ids
        }
        existing_resources = {
            action.resource_id: {"kind": action.kind, "path": action.path}
            for action in item.actions
            if action.action != "create"
        }
        source_ids = {
            source_id
            for action in item.actions
            for source_id in action.source_ids
        }
        step_ids = {
            str(step.get("step_id") or "")
            for step in item.workflow_steps
            if isinstance(step, Mapping)
        }
        normalized = self._normalize_payload(
            {
                "diagnoses": [asdict(value) for value in item.diagnoses],
                "actions": [asdict(value) for value in item.actions],
                "workflow_steps": list(item.workflow_steps),
                "output_contract": list(item.output_contract),
                "failure_modes": list(item.failure_modes),
                "expected_improvements": list(item.expected_improvements),
                "acceptance_criteria": list(item.acceptance_criteria),
                "non_goals": list(item.non_goals),
                "overfitting_risks": list(item.overfitting_risks),
                "clarifications": [asdict(value) for value in item.clarifications],
            },
            allowed_case_ids=case_ids,
            allowed_item_ids=item_ids,
            allowed_requirement_ids=requirement_ids,
            allowed_resources=existing_resources,
            allowed_source_ids=source_ids,
            allowed_step_ids=step_ids,
        )
        for field_name, value in normalized.items():
            if value != getattr(item, field_name):
                raise ValueError(f"evolution plan {field_name} is not canonical")

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_name(f"{self.snapshot_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "items": [asdict(item) for plan_id in sorted(self._items) for item in self._items[plan_id]],
            "quarantine": list(self._quarantine),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.snapshot_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _quarantine_record(record: Any, index: int) -> dict[str, Any]:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return {"index": index, "sha256": hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded), "code": "invalid_evolution_plan"}

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return asdict(value)
        if isinstance(value, tuple):
            return [SkillEvolutionPlanStore._jsonable(item) for item in value]
        if isinstance(value, list):
            return [SkillEvolutionPlanStore._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): SkillEvolutionPlanStore._jsonable(item) for key, item in value.items()}
        return value

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _required_text(value: Any, field_name: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise SkillCreatorValidationError(f"Invalid {field_name}.", code="skill_creator_evolution_plan_invalid")
        clean = value.strip()
        if not clean or len(clean.encode("utf-8")) > maximum:
            raise SkillCreatorValidationError(f"Invalid {field_name}.", code="skill_creator_evolution_plan_invalid")
        return clean

    @classmethod
    def _text_list(cls, value: Any, field_name: str, maximum_items: int, maximum_bytes: int, *, minimum: int = 0) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not minimum <= len(value) <= maximum_items:
            raise SkillCreatorValidationError(f"Invalid {field_name}.", code="skill_creator_evolution_plan_invalid")
        return tuple(cls._required_text(item, field_name, maximum_bytes) for item in value)

    @staticmethod
    def _identifier(value: Any, field_name: str) -> str:
        clean = str(value or "").strip()
        if not _ID_RE.fullmatch(clean):
            raise SkillCreatorValidationError(f"Invalid {field_name}.", code="skill_creator_evolution_plan_invalid")
        return clean

    @classmethod
    def _identifier_list(cls, value: Any, field_name: str, maximum: int, allowed: set[str] | None, *, minimum: int = 0) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not minimum <= len(value) <= maximum:
            raise SkillCreatorValidationError(f"Invalid {field_name}.", code="skill_creator_evolution_plan_invalid")
        result = tuple(dict.fromkeys(cls._identifier(item, field_name) for item in value))
        if len(result) != len(value) or (allowed is not None and not set(result).issubset(allowed)):
            raise SkillCreatorValidationError(f"Unknown or duplicate {field_name}.", code="skill_creator_evolution_evidence_invalid")
        return result

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillCreatorValidationError(f"Invalid {field_name}.") from exc
        if isinstance(value, bool) or result < 1:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return result

    @staticmethod
    def _digest(value: Any, field_name: str) -> str:
        clean = str(value or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", clean):
            raise SkillCreatorValidationError(f"Invalid {field_name}.", code="skill_creator_evolution_plan_invalid")
        return clean

    @staticmethod
    def _resource_path(value: Any, kind: str) -> str:
        raw = str(value or "").replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if (
            not raw or path.is_absolute() or ".." in path.parts or raw != path.as_posix()
            or len(raw) > 240 or not raw.startswith(f"{_RESOURCE_ROOTS[kind]}/")
        ):
            raise SkillCreatorValidationError("Invalid evolution resource path.", code="skill_creator_evolution_resource_invalid")
        if kind == "script" and path.suffix.lower() not in {".py", ".js"}:
            raise SkillCreatorValidationError("Evolution scripts must use Python or JavaScript.", code="skill_creator_evolution_resource_invalid")
        if kind in {"reference", "asset"} and path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}:
            raise SkillCreatorValidationError("Evolution resources must be supported UTF-8 text files.", code="skill_creator_evolution_resource_invalid")
        return raw

    @staticmethod
    def _resource_id(kind: str, path: str) -> str:
        return "skillres_" + hashlib.sha256(f"{kind}\0{path}".encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _reject_credentials(cls, value: Any) -> None:
        if scan_skill_package_credentials(skill_markdown=cls._canonical_json(cls._jsonable(value))):
            raise SkillCreatorValidationError(
                "Blocked credential material was detected in evolution plan data.",
                code="skill_creator_evolution_credentials_blocked",
            )

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillCreatorStorageError("Skill evolution plan Store is unavailable.")

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()


__all__ = [
    "EVOLUTION_PLAN_VERSION",
    "SkillEvolutionAction",
    "SkillEvolutionDiagnosis",
    "SkillEvolutionPlan",
    "SkillEvolutionPlanStore",
    "SkillEvolutionQuestion",
]
