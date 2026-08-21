from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping

from .creator_evaluation import (
    SkillEvaluationCase,
    SkillEvaluationConflictError,
    SkillEvaluationNotFoundError,
    SkillEvaluationStorageError,
    SkillEvaluationStore,
    SkillEvaluationValidationError,
)
from .package_validation import scan_skill_package_credentials


EVALUATION_SUITE_VERSION = "skill-evaluation-suite-v2"
SuiteState = Literal["draft", "confirmed"]
SuiteCaseRole = Literal["normal", "ambiguous", "boundary", "regression"]
SuiteCaseSource = Literal["generated", "user", "migrated"]
SuiteQualityMode = Literal["objective", "subjective"]

_CORE_ROLES = ("normal", "ambiguous", "boundary")
_SAFE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:@/-"
)
_MAX_SUITES_PER_SESSION = 50
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_REGRESSION_CASES = 9
_MAX_COVERAGE_IDS = 64
_MAX_RESOURCE_PATHS = 20


@dataclass(frozen=True, slots=True)
class SkillEvaluationSuiteCase:
    case_id: str
    role: SuiteCaseRole
    source: SuiteCaseSource
    name: str
    prompt: str
    expected_behavior: str
    fixtures: list[dict[str, str]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    requirement_ids: tuple[str, ...] = ()
    required_resource_paths: tuple[str, ...] = ()
    workflow_step_ids: tuple[str, ...] = ()
    case_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class SkillEvaluationSuite:
    suite_id: str
    version: str
    suite_revision: int
    suite_digest: str
    session_id: str
    session_revision: int
    session_definition_digest: str
    draft_id: str
    draft_state_revision: int
    draft_revision: int
    draft_digest: str
    quality_mode: SuiteQualityMode
    state: SuiteState
    cases: tuple[SkillEvaluationSuiteCase, ...]
    change_reason: str
    based_on_revision: int | None = None
    created_at: float = field(default_factory=time.time)


class SkillEvaluationSuiteStore:
    """Append-only Creator evaluation suites with fail-closed local storage."""

    SCHEMA_VERSION = 1

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        configured = os.getenv("SKILL_CREATOR_EVALUATION_SUITE_STORAGE_DIR", "").strip()
        self.storage_dir = Path(
            storage_dir or configured or runtime_dir or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "skill_creator_evaluation_suites.json"
        self._lock = threading.RLock()
        self._items: dict[str, list[SkillEvaluationSuite]] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": EVALUATION_SUITE_VERSION,
                "available": self._load_error is None,
                "session_count": len(self._items),
                "suite_revision_count": sum(len(items) for items in self._items.values()),
                "quarantine_count": len(self._quarantine),
                "error_code": (
                    "skill_evaluation_suite_store_corrupt"
                    if self._load_error
                    else None
                ),
            }

    def current_for_session(self, session_id: str) -> SkillEvaluationSuite | None:
        clean_session_id = self._identifier(session_id, "session_id")
        with self._lock:
            self._ensure_readable_unlocked()
            revisions = self._items.get(clean_session_id) or []
            return copy.deepcopy(revisions[-1]) if revisions else None

    def require(
        self, suite_id: str, *, revision: int | None = None
    ) -> SkillEvaluationSuite:
        clean_suite_id = self._identifier(suite_id, "suite_id")
        with self._lock:
            self._ensure_readable_unlocked()
            for revisions in self._items.values():
                if not revisions or revisions[0].suite_id != clean_suite_id:
                    continue
                if revision is None:
                    return copy.deepcopy(revisions[-1])
                clean_revision = self._positive_int(revision, "suite_revision")
                if clean_revision > len(revisions):
                    break
                return copy.deepcopy(revisions[clean_revision - 1])
        raise SkillEvaluationNotFoundError(
            f"Skill evaluation suite not found: {clean_suite_id}"
        )

    def save_generated(
        self,
        *,
        session_id: str,
        session_revision: int,
        session_definition_digest: str,
        draft_id: str,
        draft_state_revision: int,
        draft_revision: int,
        draft_digest: str,
        quality_mode: SuiteQualityMode,
        cases: Iterable[Mapping[str, Any]],
        expected_suite_revision: int | None,
        expected_suite_digest: str | None,
        allowed_requirement_ids: Iterable[str],
        allowed_resource_paths: Iterable[str],
        allowed_workflow_step_ids: Iterable[str],
    ) -> SkillEvaluationSuite:
        normalized = tuple(
            self.normalize_case(
                item,
                forced_source="generated",
                allowed_requirement_ids=allowed_requirement_ids,
                allowed_resource_paths=allowed_resource_paths,
                allowed_workflow_step_ids=allowed_workflow_step_ids,
            )
            for item in cases
        )
        self._validate_case_set(normalized, allow_regressions=False)
        return self._append(
            session_id=session_id,
            session_revision=session_revision,
            session_definition_digest=session_definition_digest,
            draft_id=draft_id,
            draft_state_revision=draft_state_revision,
            draft_revision=draft_revision,
            draft_digest=draft_digest,
            quality_mode=quality_mode,
            state="draft",
            cases=normalized,
            change_reason="Generated three core evaluation cases.",
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )

    def migrate_case_set(
        self,
        *,
        session_id: str,
        session_revision: int,
        session_definition_digest: str,
        draft_id: str,
        draft_state_revision: int,
        draft_revision: int,
        draft_digest: str,
        quality_mode: SuiteQualityMode,
        cases: Iterable[SkillEvaluationCase | Mapping[str, Any]],
        allowed_requirement_ids: Iterable[str],
    ) -> SkillEvaluationSuite:
        roles = iter(_CORE_ROLES)
        normalized: list[SkillEvaluationSuiteCase] = []
        for raw in cases:
            source = asdict(raw) if isinstance(raw, SkillEvaluationCase) else dict(raw)
            source.pop("case_fingerprint", None)
            source["role"] = next(roles, "regression")
            source["requirement_ids"] = list(allowed_requirement_ids)
            normalized.append(
                self.normalize_case(
                    source,
                    forced_source="migrated",
                    allowed_requirement_ids=allowed_requirement_ids,
                    allowed_resource_paths=(),
                    allowed_workflow_step_ids=(),
                )
            )
        cases_tuple = tuple(normalized)
        self._validate_case_set(cases_tuple, allow_regressions=False)
        return self._append(
            session_id=session_id,
            session_revision=session_revision,
            session_definition_digest=session_definition_digest,
            draft_id=draft_id,
            draft_state_revision=draft_state_revision,
            draft_revision=draft_revision,
            draft_digest=draft_digest,
            quality_mode=quality_mode,
            state="confirmed",
            cases=cases_tuple,
            change_reason="Migrated the existing frozen three-case set without a model call.",
            expected_suite_revision=None,
            expected_suite_digest=None,
        )

    def patch(
        self,
        suite_id: str,
        *,
        expected_suite_revision: int,
        expected_suite_digest: str,
        session_revision: int,
        session_definition_digest: str,
        draft_state_revision: int,
        cases: Iterable[Mapping[str, Any]],
        change_reason: str,
        allowed_requirement_ids: Iterable[str],
        allowed_resource_paths: Iterable[str],
        allowed_workflow_step_ids: Iterable[str],
    ) -> SkillEvaluationSuite:
        current, cases_tuple, clean_reason = self._prepare_patch(
            suite_id,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            cases=cases,
            change_reason=change_reason,
            allowed_requirement_ids=allowed_requirement_ids,
            allowed_resource_paths=allowed_resource_paths,
            allowed_workflow_step_ids=allowed_workflow_step_ids,
        )
        return self._append(
            session_id=current.session_id,
            session_revision=session_revision,
            session_definition_digest=session_definition_digest,
            draft_id=current.draft_id,
            draft_state_revision=draft_state_revision,
            draft_revision=current.draft_revision,
            draft_digest=current.draft_digest,
            quality_mode=current.quality_mode,
            state="draft",
            cases=cases_tuple,
            change_reason=clean_reason,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            suite_id=current.suite_id,
        )

    def validate_patch(
        self,
        suite_id: str,
        *,
        expected_suite_revision: int,
        expected_suite_digest: str,
        cases: Iterable[Mapping[str, Any]],
        change_reason: str,
        allowed_requirement_ids: Iterable[str],
        allowed_resource_paths: Iterable[str],
        allowed_workflow_step_ids: Iterable[str],
    ) -> None:
        """Validate a proposed revision without changing the immutable Store."""
        self._prepare_patch(
            suite_id,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            cases=cases,
            change_reason=change_reason,
            allowed_requirement_ids=allowed_requirement_ids,
            allowed_resource_paths=allowed_resource_paths,
            allowed_workflow_step_ids=allowed_workflow_step_ids,
        )

    def _prepare_patch(
        self,
        suite_id: str,
        *,
        expected_suite_revision: int,
        expected_suite_digest: str,
        cases: Iterable[Mapping[str, Any]],
        change_reason: str,
        allowed_requirement_ids: Iterable[str],
        allowed_resource_paths: Iterable[str],
        allowed_workflow_step_ids: Iterable[str],
    ) -> tuple[SkillEvaluationSuite, tuple[SkillEvaluationSuiteCase, ...], str]:
        current = self.require(suite_id)
        self._require_expected(
            current,
            expected_revision=expected_suite_revision,
            expected_digest=expected_suite_digest,
        )
        clean_reason = self._text(change_reason, "change_reason", maximum=4_000)
        if current.state == "confirmed" and not clean_reason:
            raise SkillEvaluationValidationError(
                "Changing a confirmed evaluation suite requires a reason.",
                code="skill_evaluation_suite_change_reason_required",
            )
        existing = {item.case_id: item for item in current.cases}
        normalized: list[SkillEvaluationSuiteCase] = []
        for raw in cases:
            source = dict(raw)
            case_id = str(source.get("case_id") or "").strip()
            previous = existing.get(case_id)
            role = str(source.get("role") or "").strip()
            if previous is not None and role != previous.role:
                raise SkillEvaluationValidationError(
                    "An existing evaluation case role cannot be changed.",
                    code="skill_evaluation_suite_case_role_frozen",
                )
            normalized_case = self.normalize_case(
                source,
                forced_source=(previous.source if previous else "user"),
                allowed_requirement_ids=allowed_requirement_ids,
                allowed_resource_paths=allowed_resource_paths,
                allowed_workflow_step_ids=allowed_workflow_step_ids,
            )
            if (
                previous is not None
                and previous.source != "user"
                and normalized_case.case_fingerprint != previous.case_fingerprint
            ):
                normalized_case = self.normalize_case(
                    source,
                    forced_source="user",
                    allowed_requirement_ids=allowed_requirement_ids,
                    allowed_resource_paths=allowed_resource_paths,
                    allowed_workflow_step_ids=allowed_workflow_step_ids,
                )
            normalized.append(normalized_case)
        cases_tuple = tuple(normalized)
        self._validate_case_set(cases_tuple, allow_regressions=True)
        return current, cases_tuple, clean_reason

    def confirm(
        self,
        suite_id: str,
        *,
        expected_suite_revision: int,
        expected_suite_digest: str,
        session_revision: int,
        session_definition_digest: str,
        draft_state_revision: int,
        allowed_requirement_ids: Iterable[str],
        allowed_resource_paths: Iterable[str],
        allowed_workflow_step_ids: Iterable[str],
    ) -> SkillEvaluationSuite:
        current = self.require(suite_id)
        self._require_expected(
            current,
            expected_revision=expected_suite_revision,
            expected_digest=expected_suite_digest,
        )
        if current.state == "confirmed":
            return current
        cases = tuple(
            self.normalize_case(
                asdict(item),
                forced_source=item.source,
                allowed_requirement_ids=allowed_requirement_ids,
                allowed_resource_paths=allowed_resource_paths,
                allowed_workflow_step_ids=allowed_workflow_step_ids,
            )
            for item in current.cases
        )
        self._validate_case_set(cases, allow_regressions=True)
        covered = {value for item in cases for value in item.requirement_ids}
        missing = sorted(set(allowed_requirement_ids) - covered)
        if missing:
            raise SkillEvaluationValidationError(
                "Evaluation suite does not cover every frozen Creator requirement.",
                code="skill_evaluation_suite_coverage_incomplete",
            )
        return self._append(
            session_id=current.session_id,
            session_revision=session_revision,
            session_definition_digest=session_definition_digest,
            draft_id=current.draft_id,
            draft_state_revision=draft_state_revision,
            draft_revision=current.draft_revision,
            draft_digest=current.draft_digest,
            quality_mode=current.quality_mode,
            state="confirmed",
            cases=cases,
            change_reason=current.change_reason,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            suite_id=current.suite_id,
        )

    def _append(
        self,
        *,
        session_id: str,
        session_revision: int,
        session_definition_digest: str,
        draft_id: str,
        draft_state_revision: int,
        draft_revision: int,
        draft_digest: str,
        quality_mode: SuiteQualityMode,
        state: SuiteState,
        cases: tuple[SkillEvaluationSuiteCase, ...],
        change_reason: str,
        expected_suite_revision: int | None,
        expected_suite_digest: str | None,
        suite_id: str | None = None,
    ) -> SkillEvaluationSuite:
        clean_session_id = self._identifier(session_id, "session_id")
        clean_draft_id = self._identifier(draft_id, "draft_id")
        clean_session_revision = self._positive_int(session_revision, "session_revision")
        clean_session_definition_digest = self._digest(
            session_definition_digest, "session_definition_digest"
        )
        clean_draft_state_revision = self._positive_int(
            draft_state_revision, "draft_state_revision"
        )
        clean_draft_revision = self._positive_int(draft_revision, "draft_revision")
        clean_draft_digest = self._digest(draft_digest, "draft_digest")
        if quality_mode not in {"objective", "subjective"}:
            raise SkillEvaluationValidationError(
                "Invalid evaluation suite quality mode.",
                code="skill_evaluation_suite_invalid",
            )
        self._reject_credentials(
            {"cases": [asdict(item) for item in cases], "change_reason": change_reason}
        )
        with self._lock:
            self._ensure_writable_unlocked()
            revisions = self._items.get(clean_session_id) or []
            current = revisions[-1] if revisions else None
            self._require_expected(
                current,
                expected_revision=expected_suite_revision,
                expected_digest=expected_suite_digest,
            )
            if len(revisions) >= _MAX_SUITES_PER_SESSION:
                raise SkillEvaluationValidationError(
                    "Evaluation suite revision limit reached.",
                    code="skill_evaluation_suite_revision_limit",
                )
            clean_suite_id = self._identifier(
                suite_id
                or (current.suite_id if current else f"skill_eval_suite_{uuid.uuid4().hex}"),
                "suite_id",
            )
            if current is not None and clean_suite_id != current.suite_id:
                raise SkillEvaluationConflictError("Evaluation suite identity changed.")
            revision = len(revisions) + 1
            payload = {
                "version": EVALUATION_SUITE_VERSION,
                "suite_id": clean_suite_id,
                "suite_revision": revision,
                "session_id": clean_session_id,
                "session_revision": clean_session_revision,
                "session_definition_digest": clean_session_definition_digest,
                "draft_id": clean_draft_id,
                "draft_state_revision": clean_draft_state_revision,
                "draft_revision": clean_draft_revision,
                "draft_digest": clean_draft_digest,
                "quality_mode": quality_mode,
                "state": state,
                "case_fingerprints": [item.case_fingerprint for item in cases],
                "change_reason": str(change_reason or "").strip(),
                "based_on_revision": current.suite_revision if current else None,
            }
            digest = hashlib.sha256(self._canonical_json(payload).encode("utf-8")).hexdigest()
            item = SkillEvaluationSuite(
                suite_id=clean_suite_id,
                version=EVALUATION_SUITE_VERSION,
                suite_revision=revision,
                suite_digest=digest,
                session_id=clean_session_id,
                session_revision=clean_session_revision,
                session_definition_digest=clean_session_definition_digest,
                draft_id=clean_draft_id,
                draft_state_revision=clean_draft_state_revision,
                draft_revision=clean_draft_revision,
                draft_digest=clean_draft_digest,
                quality_mode=quality_mode,
                state=state,
                cases=cases,
                change_reason=str(change_reason or "").strip(),
                based_on_revision=current.suite_revision if current else None,
            )
            previous = copy.deepcopy(self._items)
            self._items.setdefault(clean_session_id, []).append(item)
            try:
                self._save_unlocked()
            except BaseException:
                self._items = previous
                raise
            return copy.deepcopy(item)

    @classmethod
    def normalize_case(
        cls,
        raw: Mapping[str, Any],
        *,
        forced_source: SuiteCaseSource,
        allowed_requirement_ids: Iterable[str],
        allowed_resource_paths: Iterable[str],
        allowed_workflow_step_ids: Iterable[str],
    ) -> SkillEvaluationSuiteCase:
        if not isinstance(raw, Mapping):
            raise SkillEvaluationValidationError(
                "Evaluation suite case must be an object.",
                code="skill_evaluation_suite_case_invalid",
            )
        role = str(raw.get("role") or "").strip()
        if role not in {*_CORE_ROLES, "regression"}:
            raise SkillEvaluationValidationError(
                "Evaluation suite case has an invalid role.",
                code="skill_evaluation_suite_case_role_invalid",
            )
        # ``SkillEvaluationStore`` owns the legacy evaluation-case fingerprint,
        # while Suite cases have a wider fingerprint that also covers role,
        # provenance, and coverage.  Never feed the Suite fingerprint into the
        # legacy normalizer: the two domains are deliberately incompatible.
        legacy_case = dict(raw)
        legacy_case.pop("case_fingerprint", None)
        if isinstance(legacy_case.get("required_resource_paths"), tuple):
            legacy_case["required_resource_paths"] = list(
                legacy_case["required_resource_paths"]
            )
        base = SkillEvaluationStore.normalize_case(legacy_case)
        requirement_ids = cls._identifier_list(
            raw.get("requirement_ids") or (),
            field_name="requirement_ids",
            maximum=_MAX_COVERAGE_IDS,
            allowed=set(allowed_requirement_ids),
        )
        resource_paths = cls._resource_paths(
            raw.get("required_resource_paths") or (),
            allowed=set(allowed_resource_paths),
        )
        workflow_step_ids = cls._identifier_list(
            raw.get("workflow_step_ids") or (),
            field_name="workflow_step_ids",
            maximum=_MAX_COVERAGE_IDS,
            allowed=set(allowed_workflow_step_ids),
        )
        payload = {
            "case_id": base.case_id,
            "role": role,
            "source": forced_source,
            "name": base.name,
            "prompt": base.prompt,
            "expected_behavior": base.expected_behavior,
            "fixtures": base.fixtures,
            "assertions": base.assertions,
            "requirement_ids": list(requirement_ids),
            "required_resource_paths": list(resource_paths),
            "workflow_step_ids": list(workflow_step_ids),
        }
        fingerprint = hashlib.sha256(cls._canonical_json(payload).encode("utf-8")).hexdigest()
        supplied = raw.get("case_fingerprint")
        if supplied and str(supplied).lower() != fingerprint:
            raise SkillEvaluationConflictError(
                "Evaluation suite case fingerprint does not match its content."
            )
        return SkillEvaluationSuiteCase(
            case_id=base.case_id,
            role=role,  # type: ignore[arg-type]
            source=forced_source,
            name=base.name,
            prompt=base.prompt,
            expected_behavior=base.expected_behavior,
            fixtures=base.fixtures,
            assertions=base.assertions,
            requirement_ids=requirement_ids,
            required_resource_paths=resource_paths,
            workflow_step_ids=workflow_step_ids,
            case_fingerprint=fingerprint,
        )

    @staticmethod
    def to_evaluation_cases(
        suite: SkillEvaluationSuite,
    ) -> list[dict[str, Any]]:
        return [
            {
                "case_id": item.case_id,
                "name": item.name,
                "prompt": item.prompt,
                "expected_behavior": item.expected_behavior,
                "fixtures": copy.deepcopy(item.fixtures),
                "assertions": copy.deepcopy(item.assertions),
                "required_resource_paths": list(item.required_resource_paths),
            }
            for item in suite.cases
        ]

    @staticmethod
    def serialize(item: SkillEvaluationSuite) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def serialize_case(item: SkillEvaluationSuiteCase) -> dict[str, Any]:
        return asdict(item)

    @classmethod
    def _validate_case_set(
        cls,
        cases: tuple[SkillEvaluationSuiteCase, ...],
        *,
        allow_regressions: bool,
    ) -> None:
        if len({item.case_id for item in cases}) != len(cases):
            raise SkillEvaluationValidationError(
                "Evaluation suite case IDs must be unique.",
                code="skill_evaluation_suite_case_duplicate",
            )
        roles = [item.role for item in cases]
        for role in _CORE_ROLES:
            if roles.count(role) != 1:
                raise SkillEvaluationValidationError(
                    "Evaluation suite requires exactly one normal, ambiguous, and boundary case.",
                    code="skill_evaluation_suite_core_roles_required",
                )
        regressions = [item for item in cases if item.role == "regression"]
        if not allow_regressions and regressions:
            raise SkillEvaluationValidationError(
                "The Creator model cannot add regression cases.",
                code="skill_evaluation_suite_model_regression_forbidden",
            )
        if len(regressions) > _MAX_REGRESSION_CASES:
            raise SkillEvaluationValidationError(
                "Evaluation suite supports at most nine regression cases.",
                code="skill_evaluation_suite_regression_limit",
            )
        if any(item.role == "regression" and item.source == "generated" for item in cases):
            raise SkillEvaluationValidationError(
                "Regression cases require explicit user confirmation.",
                code="skill_evaluation_suite_regression_confirmation_required",
            )
        if len(cases) > 3 + _MAX_REGRESSION_CASES:
            raise SkillEvaluationValidationError(
                "Evaluation suite contains too many cases.",
                code="skill_evaluation_suite_case_limit",
            )

    @staticmethod
    def _require_expected(
        current: SkillEvaluationSuite | None,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        if current is None:
            if expected_revision is not None or expected_digest is not None:
                raise SkillEvaluationConflictError(
                    "Evaluation suite does not exist. Reload before continuing."
                )
            return
        if (
            expected_revision is None
            or int(expected_revision) != current.suite_revision
            or not expected_digest
            or str(expected_digest).lower() != current.suite_digest
        ):
            raise SkillEvaluationConflictError(
                "Evaluation suite changed. Reload before continuing."
            )

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw_bytes = self.snapshot_path.read_bytes()
            if len(raw_bytes) > _MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot too large")
            payload = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError("invalid suite snapshot")
            sessions = payload.get("sessions")
            if not isinstance(sessions, dict):
                raise ValueError("invalid suite index")
            quarantine = payload.get("quarantine") or []
            if not isinstance(quarantine, list):
                raise ValueError("invalid suite quarantine")
        except Exception as exc:
            self._load_error = f"skill_evaluation_suite_store_corrupt:{type(exc).__name__}"
            return
        for session_id, records in sessions.items():
            try:
                clean_session_id = self._identifier(session_id, "session_id")
                if not isinstance(records, list) or len(records) > _MAX_SUITES_PER_SESSION:
                    raise ValueError("invalid suite revisions")
                revisions = [self._parse_suite(item) for item in records]
                if any(item.session_id != clean_session_id for item in revisions):
                    raise ValueError("suite session mismatch")
                if [item.suite_revision for item in revisions] != list(
                    range(1, len(revisions) + 1)
                ):
                    raise ValueError("suite revisions are not contiguous")
                if revisions and len({item.suite_id for item in revisions}) != 1:
                    raise ValueError("suite identity changed")
                self._items[clean_session_id] = revisions
            except Exception:
                encoded = self._canonical_json(records).encode("utf-8", errors="replace")
                self._quarantine.append(
                    {
                        "code": "skill_evaluation_suite_record_invalid",
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "size_bytes": len(encoded),
                    }
                )
        for item in quarantine[-200:]:
            if not isinstance(item, dict):
                continue
            digest = str(item.get("sha256") or "").lower()
            if (
                item.get("code") == "skill_evaluation_suite_record_invalid"
                and len(digest) == 64
                and all(char in "0123456789abcdef" for char in digest)
            ):
                self._quarantine.append(
                    {
                        "code": "skill_evaluation_suite_record_invalid",
                        "sha256": digest,
                        "size_bytes": max(0, int(item.get("size_bytes") or 0)),
                    }
                )

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "version": EVALUATION_SUITE_VERSION,
            "sessions": {
                session_id: [asdict(item) for item in revisions]
                for session_id, revisions in sorted(self._items.items())
            },
            "quarantine": list(self._quarantine[-200:]),
        }
        content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(content) > _MAX_SNAPSHOT_BYTES:
            raise SkillEvaluationStorageError(
                "Evaluation suite Store reached its bounded capacity."
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
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()

    @classmethod
    def _parse_suite(cls, raw: Any) -> SkillEvaluationSuite:
        if not isinstance(raw, dict):
            raise ValueError("suite must be an object")
        if raw.get("version") != EVALUATION_SUITE_VERSION:
            raise ValueError("unsupported suite version")
        quality_mode = str(raw.get("quality_mode") or "")
        state = str(raw.get("state") or "")
        if quality_mode not in {"objective", "subjective"} or state not in {
            "draft",
            "confirmed",
        }:
            raise ValueError("invalid suite state")
        raw_cases = raw.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError("suite cases must be a list")
        # Persisted cases are verified against their own complete fingerprints.
        cases: list[SkillEvaluationSuiteCase] = []
        for value in raw_cases:
            if not isinstance(value, dict):
                raise ValueError("invalid suite case")
            role = str(value.get("role") or "")
            source = str(value.get("source") or "")
            if role not in {*_CORE_ROLES, "regression"} or source not in {
                "generated",
                "user",
                "migrated",
            }:
                raise ValueError("invalid suite case role or source")
            legacy_case = dict(value)
            legacy_case.pop("case_fingerprint", None)
            base = SkillEvaluationStore.normalize_case(legacy_case)
            requirement_ids = cls._identifier_list(
                value.get("requirement_ids") or (),
                field_name="requirement_ids",
                maximum=_MAX_COVERAGE_IDS,
                allowed=None,
            )
            resource_paths = cls._resource_paths(
                value.get("required_resource_paths") or (), allowed=None
            )
            step_ids = cls._identifier_list(
                value.get("workflow_step_ids") or (),
                field_name="workflow_step_ids",
                maximum=_MAX_COVERAGE_IDS,
                allowed=None,
            )
            payload = {
                "case_id": base.case_id,
                "role": role,
                "source": source,
                "name": base.name,
                "prompt": base.prompt,
                "expected_behavior": base.expected_behavior,
                "fixtures": base.fixtures,
                "assertions": base.assertions,
                "requirement_ids": list(requirement_ids),
                "required_resource_paths": list(resource_paths),
                "workflow_step_ids": list(step_ids),
            }
            fingerprint = hashlib.sha256(
                cls._canonical_json(payload).encode("utf-8")
            ).hexdigest()
            if str(value.get("case_fingerprint") or "").lower() != fingerprint:
                raise ValueError("suite case fingerprint mismatch")
            cases.append(
                SkillEvaluationSuiteCase(
                    case_id=base.case_id,
                    role=role,  # type: ignore[arg-type]
                    source=source,  # type: ignore[arg-type]
                    name=base.name,
                    prompt=base.prompt,
                    expected_behavior=base.expected_behavior,
                    fixtures=base.fixtures,
                    assertions=base.assertions,
                    requirement_ids=requirement_ids,
                    required_resource_paths=resource_paths,
                    workflow_step_ids=step_ids,
                    case_fingerprint=fingerprint,
                )
            )
        cases_tuple = tuple(cases)
        cls._validate_case_set(cases_tuple, allow_regressions=True)
        item = SkillEvaluationSuite(
            suite_id=cls._identifier(raw.get("suite_id"), "suite_id"),
            version=EVALUATION_SUITE_VERSION,
            suite_revision=cls._positive_int(raw.get("suite_revision"), "suite_revision"),
            suite_digest=cls._digest(raw.get("suite_digest"), "suite_digest"),
            session_id=cls._identifier(raw.get("session_id"), "session_id"),
            session_revision=cls._positive_int(raw.get("session_revision"), "session_revision"),
            session_definition_digest=cls._digest(
                raw.get("session_definition_digest"), "session_definition_digest"
            ),
            draft_id=cls._identifier(raw.get("draft_id"), "draft_id"),
            draft_state_revision=cls._positive_int(
                raw.get("draft_state_revision"), "draft_state_revision"
            ),
            draft_revision=cls._positive_int(raw.get("draft_revision"), "draft_revision"),
            draft_digest=cls._digest(raw.get("draft_digest"), "draft_digest"),
            quality_mode=quality_mode,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            cases=cases_tuple,
            change_reason=str(raw.get("change_reason") or "")[:4_000],
            based_on_revision=(
                None
                if raw.get("based_on_revision") is None
                else cls._positive_int(raw.get("based_on_revision"), "based_on_revision")
            ),
            created_at=max(0.0, float(raw.get("created_at") or 0.0)),
        )
        payload = {
            "version": item.version,
            "suite_id": item.suite_id,
            "suite_revision": item.suite_revision,
            "session_id": item.session_id,
            "session_revision": item.session_revision,
            "session_definition_digest": item.session_definition_digest,
            "draft_id": item.draft_id,
            "draft_state_revision": item.draft_state_revision,
            "draft_revision": item.draft_revision,
            "draft_digest": item.draft_digest,
            "quality_mode": item.quality_mode,
            "state": item.state,
            "case_fingerprints": [case.case_fingerprint for case in item.cases],
            "change_reason": item.change_reason,
            "based_on_revision": item.based_on_revision,
        }
        expected = hashlib.sha256(cls._canonical_json(payload).encode("utf-8")).hexdigest()
        if item.suite_digest != expected:
            raise ValueError("suite digest mismatch")
        return item

    @staticmethod
    def _identifier(value: Any, field_name: str) -> str:
        clean = str(value or "").strip()
        if (
            not 1 <= len(clean) <= 240
            or clean[0] not in _SAFE_ID_CHARS
            or any(char not in _SAFE_ID_CHARS for char in clean)
            or any(ord(char) < 32 for char in clean)
        ):
            raise SkillEvaluationValidationError(
                f"Invalid {field_name}.", code="skill_evaluation_suite_invalid"
            )
        return clean

    @classmethod
    def _identifier_list(
        cls,
        values: Any,
        *,
        field_name: str,
        maximum: int,
        allowed: set[str] | None,
    ) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)) or len(values) > maximum:
            raise SkillEvaluationValidationError(
                f"Invalid {field_name}.", code="skill_evaluation_suite_invalid"
            )
        result = tuple(sorted({cls._identifier(value, field_name) for value in values}))
        if allowed is not None and not set(result).issubset(allowed):
            raise SkillEvaluationValidationError(
                f"Evaluation suite contains unknown {field_name}.",
                code="skill_evaluation_suite_coverage_invalid",
            )
        return result

    @classmethod
    def _resource_paths(
        cls, values: Any, *, allowed: set[str] | None
    ) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)) or len(values) > _MAX_RESOURCE_PATHS:
            raise SkillEvaluationValidationError(
                "Invalid evaluation resource paths.",
                code="skill_evaluation_suite_resource_invalid",
            )
        result: set[str] = set()
        for value in values:
            raw = str(value or "").replace("\\", "/").strip()
            path = PurePosixPath(raw)
            if (
                not raw
                or path.is_absolute()
                or ".." in path.parts
                or raw != path.as_posix()
                or not raw.startswith(("scripts/", "references/", "assets/"))
            ):
                raise SkillEvaluationValidationError(
                    "Evaluation suite resource path is invalid.",
                    code="skill_evaluation_suite_resource_invalid",
                )
            result.add(raw)
        if allowed is not None and not result.issubset(allowed):
            raise SkillEvaluationValidationError(
                "Evaluation suite references a resource outside the frozen Skill package.",
                code="skill_evaluation_suite_resource_missing",
            )
        return tuple(sorted(result))

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        try:
            clean = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.") from exc
        if isinstance(value, bool) or clean < 1:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _digest(value: Any, field_name: str) -> str:
        clean = str(value or "").strip().lower()
        if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return clean

    @staticmethod
    def _text(value: Any, field_name: str, *, maximum: int) -> str:
        if not isinstance(value, str) or len(value) > maximum:
            raise SkillEvaluationValidationError(f"Invalid {field_name}.")
        return value.strip()

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _reject_credentials(cls, value: Any) -> None:
        text = cls._canonical_json(value)
        if scan_skill_package_credentials(skill_markdown=text):
            raise SkillEvaluationValidationError(
                "Blocked credential material was detected in evaluation suite data.",
                code="skill_evaluation_suite_credentials_blocked",
            )

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillEvaluationStorageError(
                "Skill evaluation suite Store is unavailable."
            )

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()


__all__ = [
    "EVALUATION_SUITE_VERSION",
    "SkillEvaluationSuite",
    "SkillEvaluationSuiteCase",
    "SkillEvaluationSuiteStore",
]
