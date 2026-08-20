from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .package_validation import scan_skill_package_credentials


RESOURCE_PLAN_VERSION = "skill-resource-plan-v1"
ResourcePlanState = Literal[
    "needs_input",
    "needs_regeneration",
    "ready",
    "confirmed",
]
ResourceKind = Literal["script", "reference", "asset"]
ResourceAction = Literal["keep", "create", "update", "delete"]
ResourceGenerationCost = Literal["low", "medium", "high"]

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_RESOURCE_ROOTS = {
    "script": "scripts",
    "reference": "references",
    "asset": "assets",
}
_RESOURCE_KIND_ALIASES = {
    "script": "script",
    "scripts": "script",
    "脚本": "script",
    "reference": "reference",
    "references": "reference",
    "参考": "reference",
    "参考资料": "reference",
    "asset": "asset",
    "assets": "asset",
    "template": "asset",
    "模板": "asset",
}
_RESOURCE_ACTION_ALIASES = {
    "keep": "keep",
    "retain": "keep",
    "reuse": "keep",
    "保留": "keep",
    "复用": "keep",
    "create": "create",
    "add": "create",
    "new": "create",
    "generate": "create",
    "创建": "create",
    "新增": "create",
    "update": "update",
    "modify": "update",
    "replace": "update",
    "修改": "update",
    "更新": "update",
    "delete": "delete",
    "remove": "delete",
    "删除": "delete",
    "移除": "delete",
}
_RESOURCE_COST_ALIASES = {
    "low": "low",
    "small": "low",
    "低": "low",
    "medium": "medium",
    "moderate": "medium",
    "中": "medium",
    "high": "high",
    "large": "high",
    "高": "high",
}
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class ResourcePlanStep:
    step_id: str
    instruction: str


@dataclass(frozen=True, slots=True)
class ResourcePlanQuestion:
    question_id: str
    question: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillResourcePlanItem:
    resource_id: str
    spec_digest: str
    kind: ResourceKind
    action: ResourceAction
    generation_cost: ResourceGenerationCost
    path: str
    purpose: str
    source_ids: list[str]
    used_by_steps: list[str]
    depends_on: list[str]
    acceptance_checks: list[str]


@dataclass(frozen=True, slots=True)
class SkillResourcePlan:
    plan_id: str
    session_id: str
    revision: int
    digest: str
    state: ResourcePlanState
    session_revision: int
    draft_id: str | None
    draft_revision: int | None
    draft_digest: str | None
    skill_name: str
    skill_description: str
    workflow_steps: list[ResourcePlanStep]
    output_contract: list[str]
    failure_modes: list[str]
    resources: list[SkillResourcePlanItem]
    clarifications: list[ResourcePlanQuestion] = field(default_factory=list)
    clarification_answers: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SkillResourcePlanStore:
    """Atomic immutable revisions for Creator resource plans."""

    SCHEMA_VERSION = 1
    MAX_PLANS = 500
    MAX_REVISIONS_PER_PLAN = 40
    MAX_RESOURCES = 20
    MAX_ANSWER_BYTES = 32 * 1024
    MAX_ANSWERS_BYTES = 128 * 1024

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_creator_resource_plans.json"
        self._lock = threading.RLock()
        self._plans: dict[str, list[SkillResourcePlan]] = {}
        self._session_index: dict[str, str] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def current_for_session(self, session_id: str) -> SkillResourcePlan | None:
        clean = self._required_text(session_id, "session_id", 200)
        with self._lock:
            self._ensure_readable_unlocked()
            plan_id = self._session_index.get(clean)
            if plan_id is None:
                return None
            return self._copy(self._plans[plan_id][-1])

    def require(self, plan_id: str, revision: int | None = None) -> SkillResourcePlan:
        clean = self._required_text(plan_id, "plan_id", 200)
        with self._lock:
            self._ensure_readable_unlocked()
            revisions = self._plans.get(clean)
            if not revisions:
                raise SkillCreatorNotFoundError(f"Resource plan not found: {clean}")
            if revision is None:
                return self._copy(revisions[-1])
            for item in revisions:
                if item.revision == int(revision):
                    return self._copy(item)
        raise SkillCreatorNotFoundError(
            f"Resource plan revision not found: {clean}@{revision}"
        )

    def save_generated(
        self,
        *,
        session_id: str,
        session_revision: int,
        draft_id: str | None,
        draft_revision: int | None,
        draft_digest: str | None,
        payload: dict[str, Any],
        allowed_source_ids: set[str],
        expected_plan_revision: int | None = None,
        expected_plan_digest: str | None = None,
    ) -> SkillResourcePlan:
        clean_session_id = self._required_text(session_id, "session_id", 200)
        normalized = self._normalize_payload(payload, allowed_source_ids=allowed_source_ids)
        with self._lock:
            self._ensure_writable_unlocked()
            current = self._current_unlocked(clean_session_id)
            self._require_plan_match(
                current,
                expected_revision=expected_plan_revision,
                expected_digest=expected_plan_digest,
            )
            if (
                current is not None
                and current.state == "confirmed"
                and current.session_revision == int(session_revision)
                and current.draft_id == self._optional_text(draft_id, 200)
                and current.draft_revision == draft_revision
                and current.draft_digest == self._optional_digest(draft_digest, "draft_digest")
            ):
                raise SkillCreatorConflictError(
                    "Confirmed resource plans cannot be regenerated. Create a new plan revision first."
                )
            if current is None and len(self._plans) >= self.MAX_PLANS:
                raise SkillCreatorValidationError(
                    "Skill Creator resource plan limit reached.",
                    code="skill_creator_resource_plan_limit",
                )
            plan_id = current.plan_id if current else f"skillplan_{uuid.uuid4().hex}"
            revision = (current.revision + 1) if current else 1
            answers = dict(current.clarification_answers) if current else {}
            state: ResourcePlanState = (
                "needs_input" if normalized["clarifications"] else "ready"
            )
            now = time.time()
            item = self._build_plan(
                plan_id=plan_id,
                session_id=clean_session_id,
                revision=revision,
                state=state,
                session_revision=self._positive_int(session_revision, "session_revision"),
                draft_id=self._optional_text(draft_id, 200),
                draft_revision=(
                    self._positive_int(draft_revision, "draft_revision")
                    if draft_revision is not None
                    else None
                ),
                draft_digest=self._optional_digest(draft_digest, "draft_digest"),
                clarification_answers=answers,
                created_at=(current.created_at if current else now),
                updated_at=now,
                **normalized,
            )
            return self._append_unlocked(item)

    def save_answers(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        answers: dict[str, str],
    ) -> SkillResourcePlan:
        with self._lock:
            current = self._require_current_unlocked(
                plan_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.state != "needs_input":
                raise SkillCreatorConflictError(
                    "This resource plan is not waiting for clarification answers."
                )
            question_ids = {item.question_id for item in current.clarifications}
            if set(answers) != question_ids:
                raise SkillCreatorValidationError(
                    "Answer every current clarification question exactly once.",
                    code="skill_creator_resource_answers_incomplete",
                )
            clean_answers = {
                question_id: self._required_text(value, "answer", self.MAX_ANSWER_BYTES)
                for question_id, value in answers.items()
            }
            if sum(len(value.encode("utf-8")) for value in clean_answers.values()) > self.MAX_ANSWERS_BYTES:
                raise SkillCreatorValidationError(
                    "Creator clarification answers are too large.",
                    code="skill_creator_resource_answers_too_large",
                )
            self._reject_credentials(clean_answers)
            item = self._replace(
                current,
                revision=current.revision + 1,
                state="needs_regeneration",
                clarification_answers=clean_answers,
                updated_at=time.time(),
            )
            return self._append_unlocked(item)

    def patch(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        changes: dict[str, Any],
        allowed_source_ids: set[str],
    ) -> SkillResourcePlan:
        allowed = {
            "skill_name",
            "skill_description",
            "workflow_steps",
            "output_contract",
            "failure_modes",
            "resources",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise SkillCreatorValidationError(
                "Unsupported resource plan fields: " + ", ".join(unknown)
            )
        with self._lock:
            current = self._require_current_unlocked(
                plan_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.state not in {"ready", "needs_regeneration", "confirmed"}:
                raise SkillCreatorConflictError(
                    "Only the current resource plan can be revised."
                )
            path_by_id = {item.resource_id: item.path for item in current.resources}
            resource_payload = []
            for resource in current.resources:
                raw_resource = asdict(resource)
                raw_resource["depends_on"] = [
                    path_by_id[dependency_id]
                    for dependency_id in resource.depends_on
                    if dependency_id in path_by_id
                ]
                resource_payload.append(raw_resource)
            payload = {
                "skill_name": current.skill_name,
                "skill_description": current.skill_description,
                "workflow_steps": [asdict(item) for item in current.workflow_steps],
                "output_contract": list(current.output_contract),
                "failure_modes": list(current.failure_modes),
                "resources": resource_payload,
                "clarifications": [],
            }
            payload.update(changes)
            normalized = self._normalize_payload(payload, allowed_source_ids=allowed_source_ids)
            item = self._build_plan(
                plan_id=current.plan_id,
                session_id=current.session_id,
                revision=current.revision + 1,
                state="ready",
                session_revision=current.session_revision,
                draft_id=current.draft_id,
                draft_revision=current.draft_revision,
                draft_digest=current.draft_digest,
                clarification_answers=dict(current.clarification_answers),
                created_at=current.created_at,
                updated_at=time.time(),
                **normalized,
            )
            return self._append_unlocked(item)

    def confirm(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        session_revision: int,
        draft_revision: int | None,
        draft_digest: str | None,
    ) -> SkillResourcePlan:
        with self._lock:
            current = self._require_current_unlocked(
                plan_id, expected_revision=expected_revision, expected_digest=expected_digest
            )
            if current.state != "ready":
                raise SkillCreatorConflictError("Only a ready resource plan can be confirmed.")
            if current.session_revision != int(session_revision):
                raise SkillCreatorConflictError(
                    "Creator session changed after this resource plan was generated."
                )
            if current.draft_revision != draft_revision or current.draft_digest != self._optional_digest(
                draft_digest, "draft_digest"
            ):
                raise SkillCreatorConflictError(
                    "Creator draft changed after this resource plan was generated."
                )
            item = self._replace(
                current,
                revision=current.revision + 1,
                state="confirmed",
                updated_at=time.time(),
            )
            return self._append_unlocked(item)

    def save_evolution_revision(
        self,
        *,
        source_plan_id: str,
        source_revision: int,
        source_digest: str,
        session_revision: int,
        draft_id: str,
        draft_revision: int,
        draft_digest: str,
        payload: dict[str, Any],
        allowed_source_ids: set[str],
    ) -> SkillResourcePlan:
        """Create one server-owned revision rebound to the reviewed session facts.

        Public plan editing deliberately retains the original bindings. Evolution is
        the only flow allowed to rebind an immutable plan after an evaluation review
        advances the Creator session revision. The operation is idempotent across a
        crash between this Store and the Evolution Store.
        """

        clean_plan_id = self._required_text(source_plan_id, "source_plan_id", 200)
        clean_source_digest = self._optional_digest(source_digest, "source_digest")
        normalized = self._normalize_payload(
            {**dict(payload), "clarifications": []},
            allowed_source_ids=allowed_source_ids,
        )
        clean_bindings = {
            "session_revision": self._positive_int(session_revision, "session_revision"),
            "draft_id": self._optional_text(draft_id, 200),
            "draft_revision": self._positive_int(draft_revision, "draft_revision"),
            "draft_digest": self._optional_digest(draft_digest, "draft_digest"),
        }
        with self._lock:
            self._ensure_writable_unlocked()
            revisions = self._plans.get(clean_plan_id)
            if not revisions or int(source_revision) < 1 or int(source_revision) > len(revisions):
                raise SkillCreatorNotFoundError(
                    f"Resource plan revision not found: {clean_plan_id}@{source_revision}"
                )
            source = revisions[int(source_revision) - 1]
            if source.digest != clean_source_digest or source.state != "confirmed":
                raise SkillCreatorConflictError(
                    "The source resource plan changed before evolution was confirmed."
                )
            current = revisions[-1]
            if current.revision > source.revision:
                if self._matches_evolution_revision(
                    current,
                    source=source,
                    normalized=normalized,
                    bindings=clean_bindings,
                ):
                    return self._copy(current)
                raise SkillCreatorConflictError(
                    "A different resource plan revision already superseded this evolution source."
                )
            if current.revision != source.revision:
                raise SkillCreatorConflictError("Resource plan revision is no longer current.")
            item = self._build_plan(
                plan_id=source.plan_id,
                session_id=source.session_id,
                revision=source.revision + 1,
                state="ready",
                clarification_answers={},
                created_at=source.created_at,
                updated_at=time.time(),
                **clean_bindings,
                **normalized,
            )
            return self._append_unlocked(item)

    @staticmethod
    def serialize(item: SkillResourcePlan) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def _matches_evolution_revision(
        current: SkillResourcePlan,
        *,
        source: SkillResourcePlan,
        normalized: dict[str, Any],
        bindings: dict[str, Any],
    ) -> bool:
        if current.revision not in {source.revision + 1, source.revision + 2}:
            return False
        if current.state not in {"ready", "confirmed"}:
            return False
        return bool(
            current.session_revision == bindings["session_revision"]
            and current.draft_id == bindings["draft_id"]
            and current.draft_revision == bindings["draft_revision"]
            and current.draft_digest == bindings["draft_digest"]
            and current.skill_name == normalized["skill_name"]
            and current.skill_description == normalized["skill_description"]
            and current.workflow_steps == normalized["workflow_steps"]
            and current.output_contract == normalized["output_contract"]
            and current.failure_modes == normalized["failure_modes"]
            and current.resources == normalized["resources"]
        )

    def _normalize_payload(
        self, payload: dict[str, Any], *, allowed_source_ids: set[str]
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SkillCreatorValidationError(
                "Resource planner returned an invalid object.",
                code="skill_creator_resource_plan_invalid",
            )
        skill_name = self._required_text(payload.get("skill_name"), "skill_name", 64)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name):
            raise SkillCreatorValidationError(
                "Resource plan skill_name must be kebab-case.",
                code="skill_creator_resource_plan_invalid",
            )
        skill_description = self._required_text(
            payload.get("skill_description"), "skill_description", 1024
        )
        workflow_steps = self._steps(payload.get("workflow_steps"))
        step_ids = {item.step_id for item in workflow_steps}
        output_contract = self._text_list(
            payload.get("output_contract"), "output_contract", 20, 2_000
        )
        failure_modes = self._text_list(
            payload.get("failure_modes"), "failure_modes", 20, 2_000
        )
        clarifications = self._questions(payload.get("clarifications") or [])
        resources = self._resources(
            payload.get("resources") or [],
            allowed_source_ids=allowed_source_ids,
            step_ids=step_ids,
        )
        if clarifications and resources:
            raise SkillCreatorValidationError(
                "A plan waiting for clarification cannot pre-commit resource files.",
                code="skill_creator_resource_plan_invalid",
            )
        self._reject_credentials(payload)
        return {
            "skill_name": skill_name,
            "skill_description": skill_description,
            "workflow_steps": workflow_steps,
            "output_contract": output_contract,
            "failure_modes": failure_modes,
            "resources": resources,
            "clarifications": clarifications,
        }

    def _resources(
        self,
        value: Any,
        *,
        allowed_source_ids: set[str],
        step_ids: set[str],
    ) -> list[SkillResourcePlanItem]:
        if not isinstance(value, list) or len(value) > self.MAX_RESOURCES:
            raise SkillCreatorValidationError(
                "Resource plan contains too many resources.",
                code="skill_creator_resource_plan_invalid",
            )
        prelim: list[dict[str, Any]] = []
        paths: set[str] = set()
        for raw in value:
            if not isinstance(raw, dict):
                raise SkillCreatorValidationError("Invalid resource plan item.")
            kind = _RESOURCE_KIND_ALIASES.get(
                str(raw.get("kind") or "").strip().casefold()
            )
            action = _RESOURCE_ACTION_ALIASES.get(
                str(raw.get("action") or "create").strip().casefold()
            )
            generation_cost = _RESOURCE_COST_ALIASES.get(
                str(raw.get("generation_cost") or "medium").strip().casefold()
            )
            if kind is None:
                raise SkillCreatorValidationError(
                    "Invalid resource kind; use script, reference, or asset.",
                    code="skill_creator_resource_kind_invalid",
                )
            if action is None:
                raise SkillCreatorValidationError(
                    "Invalid resource action; use keep, create, update, or delete.",
                    code="skill_creator_resource_action_invalid",
                )
            if generation_cost is None:
                raise SkillCreatorValidationError(
                    "Invalid resource generation cost; use low, medium, or high.",
                    code="skill_creator_resource_cost_invalid",
                )
            path = self._resource_path(raw.get("path"), expected_root=_RESOURCE_ROOTS[kind])
            if path.casefold() in {item.casefold() for item in paths}:
                raise SkillCreatorValidationError("Resource plan paths must be unique.")
            paths.add(path)
            prelim.append({
                **raw,
                "kind": kind,
                "action": action,
                "generation_cost": generation_cost,
                "path": path,
            })
        by_path = {item["path"]: self._resource_id(item["kind"], item["path"]) for item in prelim}
        result: list[SkillResourcePlanItem] = []
        for raw in prelim:
            source_ids = self._source_id_list(raw.get("source_ids"), "source_ids", 30)
            if any(source_id not in allowed_source_ids for source_id in source_ids):
                raise SkillCreatorValidationError(
                    "Resource plan references an unknown source requirement.",
                    code="skill_creator_resource_source_unknown",
                )
            used_by_steps = self._identifier_list(
                raw.get("used_by_steps"), "used_by_steps", 20
            )
            if any(step_id not in step_ids for step_id in used_by_steps):
                raise SkillCreatorValidationError("Resource plan references an unknown workflow step.")
            depends_on_paths = self._text_list(
                raw.get("depends_on") or raw.get("depends_on_paths") or [],
                "depends_on",
                20,
                240,
                allow_empty=True,
            )
            if any(path not in by_path or path == raw["path"] for path in depends_on_paths):
                raise SkillCreatorValidationError("Resource dependency path is invalid.")
            purpose = self._required_text(raw.get("purpose"), "purpose", 2_000)
            dependency_ids = [by_path[path] for path in depends_on_paths]
            acceptance_checks = self._text_list(
                raw.get("acceptance_checks"), "acceptance_checks", 10, 1_000
            )
            resource_id = by_path[raw["path"]]
            result.append(
                SkillResourcePlanItem(
                    resource_id=resource_id,
                    spec_digest=self._resource_spec_digest(
                        resource_id=resource_id,
                        kind=raw["kind"],
                        action=raw["action"],
                        generation_cost=raw["generation_cost"],
                        path=raw["path"],
                        purpose=purpose,
                        source_ids=source_ids,
                        used_by_steps=used_by_steps,
                        depends_on=dependency_ids,
                        acceptance_checks=acceptance_checks,
                    ),
                    kind=raw["kind"],
                    action=raw["action"],
                    generation_cost=raw["generation_cost"],
                    path=raw["path"],
                    purpose=purpose,
                    source_ids=source_ids,
                    used_by_steps=used_by_steps,
                    depends_on=dependency_ids,
                    acceptance_checks=acceptance_checks,
                )
            )
        self._assert_acyclic(result)
        return result

    @staticmethod
    def _assert_acyclic(resources: list[SkillResourcePlanItem]) -> None:
        dependencies = {item.resource_id: set(item.depends_on) for item in resources}
        pending = dict(dependencies)
        while pending:
            ready = {key for key, values in pending.items() if not values}
            if not ready:
                raise SkillCreatorValidationError("Resource plan dependencies contain a cycle.")
            for key in ready:
                pending.pop(key)
            for values in pending.values():
                values.difference_update(ready)

    @staticmethod
    def _resource_spec_digest(**values: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                values,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _steps(self, value: Any) -> list[ResourcePlanStep]:
        if not isinstance(value, list) or not 4 <= len(value) <= 10:
            raise SkillCreatorValidationError("Resource plan requires four to ten workflow steps.")
        result: list[ResourcePlanStep] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, dict):
                raise SkillCreatorValidationError("Invalid resource plan workflow step.")
            step_id = self._identifier(raw.get("step_id") or raw.get("id"), "step_id")
            if step_id in seen:
                raise SkillCreatorValidationError("Workflow step IDs must be unique.")
            seen.add(step_id)
            result.append(
                ResourcePlanStep(
                    step_id=step_id,
                    instruction=self._required_text(
                        raw.get("instruction") or raw.get("description"),
                        "workflow instruction",
                        2_000,
                    ),
                )
            )
        return result

    def _questions(self, value: Any) -> list[ResourcePlanQuestion]:
        if not isinstance(value, list) or len(value) > 5:
            raise SkillCreatorValidationError("Resource planner may ask at most five questions.")
        result: list[ResourcePlanQuestion] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, dict):
                raise SkillCreatorValidationError("Invalid resource clarification question.")
            question_id = self._identifier(raw.get("question_id") or raw.get("id"), "question_id")
            if question_id in seen:
                raise SkillCreatorValidationError("Clarification question IDs must be unique.")
            seen.add(question_id)
            result.append(
                ResourcePlanQuestion(
                    question_id=question_id,
                    question=self._required_text(raw.get("question"), "question", 2_000),
                    reason=self._required_text(raw.get("reason"), "reason", 1_000),
                )
            )
        return result

    def _build_plan(self, **values: Any) -> SkillResourcePlan:
        digest_payload = {
            key: values[key]
            for key in (
                "plan_id",
                "revision",
                "state",
                "session_id",
                "session_revision",
                "draft_id",
                "draft_revision",
                "draft_digest",
                "skill_name",
                "skill_description",
                "workflow_steps",
                "output_contract",
                "failure_modes",
                "resources",
                "clarifications",
                "clarification_answers",
            )
        }
        digest = hashlib.sha256(
            json.dumps(
                self._jsonable(digest_payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return SkillResourcePlan(digest=digest, **values)

    def _replace(self, current: SkillResourcePlan, **changes: Any) -> SkillResourcePlan:
        values = asdict(current)
        values.pop("digest", None)
        values.update(changes)
        values["workflow_steps"] = [ResourcePlanStep(**item) for item in values["workflow_steps"]]
        values["clarifications"] = [ResourcePlanQuestion(**item) for item in values["clarifications"]]
        values["resources"] = [SkillResourcePlanItem(**item) for item in values["resources"]]
        return self._build_plan(**values)

    def _append_unlocked(self, item: SkillResourcePlan) -> SkillResourcePlan:
        revisions = self._plans.setdefault(item.plan_id, [])
        if revisions and item.revision != revisions[-1].revision + 1:
            raise SkillCreatorConflictError("Resource plan revision is not sequential.")
        if len(revisions) >= self.MAX_REVISIONS_PER_PLAN:
            raise SkillCreatorValidationError(
                "Resource plan revision limit reached.",
                code="skill_creator_resource_plan_revision_limit",
            )
        previous_index = self._session_index.get(item.session_id)
        revisions.append(item)
        self._session_index[item.session_id] = item.plan_id
        try:
            self._save_unlocked()
        except BaseException:
            revisions.pop()
            if not revisions:
                self._plans.pop(item.plan_id, None)
            if previous_index is None:
                self._session_index.pop(item.session_id, None)
            else:
                self._session_index[item.session_id] = previous_index
            raise
        return self._copy(item)

    def _require_current_unlocked(
        self, plan_id: str, *, expected_revision: int, expected_digest: str
    ) -> SkillResourcePlan:
        self._ensure_writable_unlocked()
        clean = self._required_text(plan_id, "plan_id", 200)
        revisions = self._plans.get(clean)
        if not revisions:
            raise SkillCreatorNotFoundError(f"Resource plan not found: {clean}")
        current = revisions[-1]
        self._require_plan_match(
            current, expected_revision=expected_revision, expected_digest=expected_digest
        )
        return current

    @staticmethod
    def _require_plan_match(
        current: SkillResourcePlan | None,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        if current is None:
            if expected_revision is not None or expected_digest is not None:
                raise SkillCreatorConflictError("Resource plan no longer exists as expected.")
            return
        if (
            expected_revision is None
            or expected_digest is None
            or current.revision != int(expected_revision)
            or current.digest != str(expected_digest).lower()
        ):
            raise SkillCreatorConflictError("Resource plan changed. Reload it before continuing.")

    def _current_unlocked(self, session_id: str) -> SkillResourcePlan | None:
        plan_id = self._session_index.get(session_id)
        return self._plans[plan_id][-1] if plan_id else None

    def _load(self) -> None:
        with self._lock:
            if not self.snapshot_path.exists():
                return
            try:
                raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                self._load_error = f"Skill Creator resource plan storage is unreadable: {exc}"
                return
            if not isinstance(raw, dict) or raw.get("version") != self.SCHEMA_VERSION or not isinstance(raw.get("items"), list):
                self._load_error = "Skill Creator resource plan storage has an unsupported structure."
                return
            persisted_quarantine = raw.get("quarantine", [])
            if isinstance(persisted_quarantine, list):
                self._quarantine = [
                    dict(item)
                    for item in persisted_quarantine
                    if isinstance(item, dict)
                ][: self.MAX_PLANS]
            sanitized = False
            for index, record in enumerate(raw["items"]):
                try:
                    item = self._decode(record)
                    revisions = self._plans.setdefault(item.plan_id, [])
                    if revisions and item.revision != revisions[-1].revision + 1:
                        raise ValueError("Non-sequential resource plan revision.")
                    existing_plan = self._session_index.get(item.session_id)
                    if existing_plan not in {None, item.plan_id}:
                        raise ValueError("Multiple resource plans for one Creator session.")
                    revisions.append(item)
                    self._session_index[item.session_id] = item.plan_id
                except (TypeError, ValueError, SkillCreatorValidationError):
                    self._quarantine.append(self._quarantine_record(record, index=index))
                    sanitized = True
            if sanitized:
                try:
                    self._save_unlocked()
                except OSError as exc:
                    self._load_error = f"Unable to sanitize Creator resource plans: {exc}"

    def _decode(self, record: Any) -> SkillResourcePlan:
        if not isinstance(record, dict):
            raise TypeError("Resource plan record must be an object.")
        values = dict(record)
        values["workflow_steps"] = [ResourcePlanStep(**item) for item in values.get("workflow_steps", [])]
        values["clarifications"] = [ResourcePlanQuestion(**item) for item in values.get("clarifications", [])]
        values["resources"] = [SkillResourcePlanItem(**item) for item in values.get("resources", [])]
        item = SkillResourcePlan(**values)
        if item.state not in {"needs_input", "needs_regeneration", "ready", "confirmed"}:
            raise ValueError("Invalid resource plan state.")
        for resource in item.resources:
            expected_spec_digest = self._resource_spec_digest(
                resource_id=resource.resource_id,
                kind=resource.kind,
                action=resource.action,
                generation_cost=resource.generation_cost,
                path=resource.path,
                purpose=resource.purpose,
                source_ids=resource.source_ids,
                used_by_steps=resource.used_by_steps,
                depends_on=resource.depends_on,
                acceptance_checks=resource.acceptance_checks,
            )
            if resource.spec_digest != expected_spec_digest:
                raise ValueError("Resource plan item digest mismatch.")
        rebuilt = self._build_plan(
            **{key: value for key, value in asdict(item).items() if key != "digest"}
        )
        if rebuilt.digest != item.digest:
            raise ValueError("Resource plan digest mismatch.")
        self._reject_credentials(asdict(item))
        return item

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        payload = {
            "version": self.SCHEMA_VERSION,
            "items": [
                asdict(item)
                for plan_id in sorted(self._plans)
                for item in self._plans[plan_id]
            ],
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

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillCreatorStorageError(self._load_error)

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()

    @staticmethod
    def _resource_id(kind: str, path: str) -> str:
        suffix = hashlib.sha256(f"{kind}\0{path}".encode("utf-8")).hexdigest()[:16]
        return f"skillres_{suffix}"

    @staticmethod
    def _resource_path(value: Any, *, expected_root: str) -> str:
        if not isinstance(value, str) or value != value.strip() or "\\" in value:
            raise SkillCreatorValidationError("Resource paths must use normalized POSIX text.")
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != expected_root
            or len(value) > 240
        ):
            raise SkillCreatorValidationError("Resource path is outside its allowed root.")
        for segment in path.parts:
            stem = segment.split(".", 1)[0].casefold()
            if (
                not segment
                or segment.endswith((" ", "."))
                or len(segment.encode("utf-8")) > 120
                or stem in _WINDOWS_RESERVED_NAMES
                or any(ord(character) < 32 for character in segment)
            ):
                raise SkillCreatorValidationError("Resource path is not portable.")
        if expected_root == "scripts" and path.suffix.casefold() not in {".py", ".js"}:
            raise SkillCreatorValidationError(
                "Creator scripts must use a .py or .js path."
            )
        return path.as_posix()

    @staticmethod
    def _identifier(value: Any, field_name: str) -> str:
        clean = str(value or "").strip()
        if not _IDENTIFIER_RE.fullmatch(clean):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return clean

    def _identifier_list(self, value: Any, field_name: str, maximum: int) -> list[str]:
        if not isinstance(value, list) or len(value) > maximum:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        result = [self._identifier(item, field_name) for item in value]
        if len(result) != len(set(result)):
            raise SkillCreatorValidationError(f"Duplicate {field_name}.")
        return result

    @staticmethod
    def _source_id_list(value: Any, field_name: str, maximum: int) -> list[str]:
        if not isinstance(value, list) or len(value) > maximum:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        result = [str(item or "").strip() for item in value]
        if any(not _SOURCE_ID_RE.fullmatch(item) for item in result):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        if len(result) != len(set(result)):
            raise SkillCreatorValidationError(f"Duplicate {field_name}.")
        return result

    @staticmethod
    def _required_text(value: Any, field_name: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        clean = value.strip()
        if not clean or len(clean.encode("utf-8")) > maximum:
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _optional_text(value: Any, maximum: int) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        if not clean or len(clean) > maximum:
            raise SkillCreatorValidationError("Invalid optional text value.")
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

    @staticmethod
    def _optional_digest(value: Any, field_name: str) -> str | None:
        if value is None:
            return None
        clean = str(value).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", clean):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return clean

    def _text_list(
        self,
        value: Any,
        field_name: str,
        maximum_items: int,
        maximum_text: int,
        *,
        allow_empty: bool = False,
    ) -> list[str]:
        if not isinstance(value, list) or len(value) > maximum_items or (not value and not allow_empty):
            raise SkillCreatorValidationError(f"Invalid {field_name}.")
        return [self._required_text(item, field_name, maximum_text) for item in value]

    @staticmethod
    def _reject_credentials(value: Any) -> None:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if scan_skill_package_credentials(skill_markdown=text, files={}):
            raise SkillCreatorValidationError(
                "Creator resource planning content contains credential-like material.",
                code="skill_creator_resource_secret_blocked",
            )

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: SkillResourcePlanStore._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [SkillResourcePlanStore._jsonable(item) for item in value]
        if hasattr(value, "__dataclass_fields__"):
            return SkillResourcePlanStore._jsonable(asdict(value))
        return value

    @staticmethod
    def _copy(item: SkillResourcePlan) -> SkillResourcePlan:
        values = json.loads(json.dumps(asdict(item), ensure_ascii=False))
        values["workflow_steps"] = [ResourcePlanStep(**entry) for entry in values["workflow_steps"]]
        values["clarifications"] = [ResourcePlanQuestion(**entry) for entry in values["clarifications"]]
        values["resources"] = [SkillResourcePlanItem(**entry) for entry in values["resources"]]
        return SkillResourcePlan(**values)

    @staticmethod
    def _quarantine_record(record: Any, *, index: int) -> dict[str, Any]:
        try:
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        except (TypeError, UnicodeEncodeError):
            encoded = type(record).__name__.encode("ascii", errors="replace")
        return {
            "index": max(0, int(index)),
            "reason_code": "blocked_or_invalid_resource_plan",
            "record_sha256": hashlib.sha256(encoded).hexdigest(),
            "record_size_bytes": len(encoded),
            "quarantined_at": time.time(),
        }


__all__ = [
    "RESOURCE_PLAN_VERSION",
    "ResourcePlanQuestion",
    "ResourcePlanStep",
    "SkillResourcePlan",
    "SkillResourcePlanItem",
    "SkillResourcePlanStore",
]
