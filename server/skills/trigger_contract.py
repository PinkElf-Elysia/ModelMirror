from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .finder import MAX_RECALL_RESULTS, MAX_RESULTS, RANKER_VERSION, SkillFinder, SkillRuntimeIndexError
from .package_validation import scan_skill_package_credentials
from .skill_manager import InstalledSkill


TRIGGER_SUITE_VERSION = "skill-trigger-suite-v1"
TRIGGER_RECEIPT_VERSION = "skill-trigger-receipt-v1"
TRIGGER_STORE_SCHEMA_VERSION = 1

TriggerCaseKind = Literal["should_trigger", "should_not_trigger", "exact_name_smoke"]
TriggerCaseSource = Literal["user", "model", "session"]
TriggerSuiteState = Literal["draft", "confirmed", "stale"]

_CASE_KINDS = {"should_trigger", "should_not_trigger", "exact_name_smoke"}
_CASE_SOURCES = {"user", "model", "session"}
_SUITE_STATES = {"draft", "confirmed", "stale"}
_MIN_REQUIRED_CASES = 2
_MAX_REQUIRED_CASES = 6
_MAX_CASE_CHARS = 500
_MAX_SUITES_PER_SESSION = 40
_MAX_SESSIONS = 500
_MAX_RECEIPTS = 2_000
_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")


class SkillTriggerConflictError(SkillCreatorConflictError):
    def __init__(self, message: str, *, code: str = "skill_trigger_suite_stale") -> None:
        super().__init__(message)
        self.code = code


class SkillTriggerNotFoundError(SkillCreatorNotFoundError):
    def __init__(self, message: str, *, code: str = "skill_trigger_suite_required") -> None:
        super().__init__(message)
        self.code = code


class SkillTriggerStorageError(SkillCreatorStorageError):
    def __init__(self, message: str, *, code: str = "skill_trigger_index_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SkillTriggerCase:
    case_id: str
    kind: TriggerCaseKind
    text: str
    source: TriggerCaseSource
    case_hash: str


@dataclass(frozen=True, slots=True)
class SkillTriggerSuiteV1:
    suite_id: str
    version: str
    suite_revision: int
    suite_digest: str
    session_id: str
    session_revision: int
    definition_digest: str
    skill_name: str
    state: TriggerSuiteState
    cases: tuple[SkillTriggerCase, ...]
    change_reason: str
    based_on_revision: int | None = None
    confirmed_actor_id: str | None = None
    confirmed_at: float | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class SkillTriggerMatchReason:
    reason_type: str
    origin: str
    matched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillTriggerCompetitor:
    candidate_id: str
    candidate_fingerprint: str
    rank: int


@dataclass(frozen=True, slots=True)
class SkillTriggerDomainResult:
    rank_top_6: int | None
    rank_top_24: int | None
    in_top_6: bool
    in_top_24: bool
    score: float | None
    reasons: tuple[SkillTriggerMatchReason, ...]
    competitors: tuple[SkillTriggerCompetitor, ...]


@dataclass(frozen=True, slots=True)
class SkillTriggerCaseResult:
    case_id: str
    case_hash: str
    kind: TriggerCaseKind
    finder: SkillTriggerDomainResult
    router: SkillTriggerDomainResult
    passed: bool


@dataclass(frozen=True, slots=True)
class SkillTriggerReceiptV1:
    receipt_id: str
    version: str
    suite_id: str
    suite_revision: int
    suite_digest: str
    session_id: str
    skill_name: str
    description_digest: str
    ranker_version: str
    runtime_index_fingerprint: str
    directory_fingerprint: str
    trust_index_fingerprint: str
    candidate_fingerprint: str
    candidate_set_fingerprint: str
    passed: bool
    case_results: tuple[SkillTriggerCaseResult, ...]
    created_at: float = field(default_factory=time.time)


def trigger_optimization_enabled() -> bool:
    return os.getenv("SKILL_CREATOR_TRIGGER_OPTIMIZATION_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def trigger_definition_digest(
    *,
    intent: str,
    positive_examples: Iterable[str],
    near_miss_examples: Iterable[str],
) -> str:
    return _sha256(
        {
            "intent": _normalized_text(intent),
            "positive_examples": [_normalized_text(item) for item in positive_examples],
            "near_miss_examples": [_normalized_text(item) for item in near_miss_examples],
        }
    )


class SkillTriggerStore:
    """Append-only trigger suites and receipts with fail-closed local storage."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        configured = os.getenv("SKILL_CREATOR_TRIGGER_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or configured or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_creator_trigger_contracts.json"
        self._lock = threading.RLock()
        self._suites: dict[str, list[SkillTriggerSuiteV1]] = {}
        self._receipts: dict[str, SkillTriggerReceiptV1] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": TRIGGER_SUITE_VERSION,
                "receipt_version": TRIGGER_RECEIPT_VERSION,
                "available": self._load_error is None,
                "session_count": len(self._suites),
                "suite_revision_count": sum(len(items) for items in self._suites.values()),
                "receipt_count": len(self._receipts),
                "quarantine_count": len(self._quarantine),
                "error_code": "skill_trigger_store_corrupt" if self._load_error else None,
            }

    def current_for_session(self, session_id: str) -> SkillTriggerSuiteV1 | None:
        clean_session_id = _identifier(session_id, "session_id")
        with self._lock:
            self._ensure_readable_unlocked()
            revisions = self._suites.get(clean_session_id) or []
            return copy.deepcopy(revisions[-1]) if revisions else None

    def require_suite(self, suite_id: str, *, revision: int | None = None) -> SkillTriggerSuiteV1:
        clean_suite_id = _identifier(suite_id, "suite_id")
        with self._lock:
            self._ensure_readable_unlocked()
            for revisions in self._suites.values():
                if not revisions or revisions[0].suite_id != clean_suite_id:
                    continue
                if revision is None:
                    return copy.deepcopy(revisions[-1])
                clean_revision = _positive_int(revision, "suite_revision")
                if clean_revision <= len(revisions):
                    return copy.deepcopy(revisions[clean_revision - 1])
                break
        raise SkillTriggerNotFoundError(f"Skill trigger suite not found: {clean_suite_id}")

    def save_draft(
        self,
        *,
        session_id: str,
        session_revision: int,
        definition_digest: str,
        skill_name: str,
        cases: Iterable[Mapping[str, Any]],
        expected_suite_revision: int | None = None,
        expected_suite_digest: str | None = None,
        change_reason: str = "",
    ) -> SkillTriggerSuiteV1:
        normalized_skill_name = _skill_name(skill_name)
        normalized_cases = _normalize_case_set(cases, skill_name=normalized_skill_name)
        _reject_credentials(normalized_cases)
        return self._append_suite(
            session_id=session_id,
            session_revision=session_revision,
            definition_digest=definition_digest,
            skill_name=normalized_skill_name,
            state="draft",
            cases=normalized_cases,
            change_reason=change_reason,
            confirmed_actor_id=None,
            confirmed_at=None,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )

    def confirm(
        self,
        *,
        suite_id: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
        session_revision: int,
        definition_digest: str,
        skill_name: str,
        actor_id: str,
    ) -> SkillTriggerSuiteV1:
        current = self.require_suite(suite_id)
        if current.state != "draft":
            raise SkillTriggerConflictError("Trigger suite is not awaiting confirmation.")
        clean_actor_id = _identifier(actor_id, "actor_id")
        if (
            current.session_revision != _positive_int(session_revision, "session_revision")
            or current.definition_digest != _digest(definition_digest, "definition_digest")
            or current.skill_name != _skill_name(skill_name)
        ):
            raise SkillTriggerConflictError("Creator session definition changed. Reload before continuing.")
        return self._append_suite(
            session_id=current.session_id,
            session_revision=current.session_revision,
            definition_digest=current.definition_digest,
            skill_name=current.skill_name,
            state="confirmed",
            cases=current.cases,
            change_reason="Confirmed trigger boundaries.",
            confirmed_actor_id=clean_actor_id,
            confirmed_at=time.time(),
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )

    def mark_stale(
        self,
        *,
        suite_id: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
        reason: str,
    ) -> SkillTriggerSuiteV1:
        current = self.require_suite(suite_id)
        clean_reason = _bounded_text(reason, "reason", maximum=500)
        return self._append_suite(
            session_id=current.session_id,
            session_revision=current.session_revision,
            definition_digest=current.definition_digest,
            skill_name=current.skill_name,
            state="stale",
            cases=current.cases,
            change_reason=clean_reason,
            confirmed_actor_id=current.confirmed_actor_id,
            confirmed_at=current.confirmed_at,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
        )

    def save_receipt(self, receipt: SkillTriggerReceiptV1) -> SkillTriggerReceiptV1:
        _validate_receipt(receipt)
        with self._lock:
            self._ensure_writable_unlocked()
            suite = self.require_suite(receipt.suite_id, revision=receipt.suite_revision)
            if suite.suite_digest != receipt.suite_digest or suite.state != "confirmed":
                raise SkillTriggerConflictError("Trigger suite changed before evaluation was saved.")
            _validate_receipt_suite_binding(receipt, suite)
            existing = self._receipts.get(receipt.receipt_id)
            if existing is not None:
                if _receipt_content(existing) != _receipt_content(receipt):
                    raise SkillTriggerConflictError("Trigger receipt identity conflicts with stored content.")
                return copy.deepcopy(existing)
            if len(self._receipts) >= _MAX_RECEIPTS:
                raise SkillTriggerStorageError("Trigger receipt Store reached its bounded capacity.")
            self._receipts[receipt.receipt_id] = copy.deepcopy(receipt)
            try:
                self._save_unlocked()
            except Exception:
                self._receipts.pop(receipt.receipt_id, None)
                raise
            return copy.deepcopy(receipt)

    def require_receipt(self, receipt_id: str) -> SkillTriggerReceiptV1:
        clean_receipt_id = _identifier(receipt_id, "receipt_id")
        with self._lock:
            self._ensure_readable_unlocked()
            receipt = self._receipts.get(clean_receipt_id)
            if receipt is None:
                raise SkillTriggerNotFoundError(
                    f"Skill trigger receipt not found: {clean_receipt_id}",
                    code="skill_trigger_gate_required",
                )
            return copy.deepcopy(receipt)

    def matching_receipt(
        self,
        *,
        suite_id: str,
        suite_revision: int,
        suite_digest: str,
        description_digest: str,
        runtime_index_fingerprint: str,
        candidate_fingerprint: str,
        candidate_set_fingerprint: str,
        passed_only: bool = True,
    ) -> SkillTriggerReceiptV1 | None:
        expected = (
            _identifier(suite_id, "suite_id"),
            _positive_int(suite_revision, "suite_revision"),
            _digest(suite_digest, "suite_digest"),
            _digest(description_digest, "description_digest"),
            _digest(runtime_index_fingerprint, "runtime_index_fingerprint"),
            _digest(candidate_fingerprint, "candidate_fingerprint"),
            _digest(candidate_set_fingerprint, "candidate_set_fingerprint"),
        )
        with self._lock:
            self._ensure_readable_unlocked()
            matches = [
                receipt
                for receipt in self._receipts.values()
                if (
                    receipt.suite_id,
                    receipt.suite_revision,
                    receipt.suite_digest,
                    receipt.description_digest,
                    receipt.runtime_index_fingerprint,
                    receipt.candidate_fingerprint,
                    receipt.candidate_set_fingerprint,
                )
                == expected
                and (receipt.passed or not passed_only)
            ]
            matches.sort(key=lambda item: (item.created_at, item.receipt_id), reverse=True)
            return copy.deepcopy(matches[0]) if matches else None

    def _append_suite(
        self,
        *,
        session_id: str,
        session_revision: int,
        definition_digest: str,
        skill_name: str,
        state: TriggerSuiteState,
        cases: tuple[SkillTriggerCase, ...],
        change_reason: str,
        confirmed_actor_id: str | None,
        confirmed_at: float | None,
        expected_suite_revision: int | None,
        expected_suite_digest: str | None,
    ) -> SkillTriggerSuiteV1:
        clean_session_id = _identifier(session_id, "session_id")
        clean_session_revision = _positive_int(session_revision, "session_revision")
        clean_definition_digest = _digest(definition_digest, "definition_digest")
        clean_skill_name = _skill_name(skill_name)
        clean_reason = str(change_reason or "").strip()
        if clean_reason:
            clean_reason = _bounded_text(clean_reason, "change_reason", maximum=500)
        if state not in _SUITE_STATES:
            raise SkillCreatorValidationError("Trigger suite state is invalid.", code="skill_trigger_suite_invalid")
        with self._lock:
            self._ensure_writable_unlocked()
            revisions = self._suites.get(clean_session_id) or []
            current = revisions[-1] if revisions else None
            _check_expected(current, expected_suite_revision, expected_suite_digest)
            if not revisions and len(self._suites) >= _MAX_SESSIONS:
                raise SkillTriggerStorageError("Trigger suite Store reached its bounded capacity.")
            if len(revisions) >= _MAX_SUITES_PER_SESSION:
                raise SkillTriggerStorageError("Trigger suite revision limit reached.")
            if current is not None and current.state in {"confirmed", "stale"} and state == "draft" and not clean_reason:
                raise SkillCreatorValidationError(
                    "A reason is required when revising confirmed trigger boundaries.",
                    code="skill_trigger_suite_invalid",
                )
            if not clean_reason:
                clean_reason = "Created trigger boundary draft."
            revision = len(revisions) + 1
            suite_id = current.suite_id if current else f"triggersuite_{_sha256(clean_session_id)[:24]}"
            content = {
                "suite_id": suite_id,
                "version": TRIGGER_SUITE_VERSION,
                "suite_revision": revision,
                "session_id": clean_session_id,
                "session_revision": clean_session_revision,
                "definition_digest": clean_definition_digest,
                "skill_name": clean_skill_name,
                "state": state,
                "cases": [asdict(item) for item in cases],
                "change_reason": clean_reason,
                "based_on_revision": current.suite_revision if current else None,
                "confirmed_actor_id": confirmed_actor_id,
                "confirmed_at": confirmed_at,
            }
            suite = SkillTriggerSuiteV1(
                suite_id=suite_id,
                version=TRIGGER_SUITE_VERSION,
                suite_revision=revision,
                suite_digest=_sha256(content),
                session_id=clean_session_id,
                session_revision=clean_session_revision,
                definition_digest=clean_definition_digest,
                skill_name=clean_skill_name,
                state=state,
                cases=cases,
                change_reason=clean_reason,
                based_on_revision=current.suite_revision if current else None,
                confirmed_actor_id=confirmed_actor_id,
                confirmed_at=confirmed_at,
                created_at=time.time(),
            )
            self._suites[clean_session_id] = [*revisions, suite]
            try:
                self._save_unlocked()
            except Exception:
                if revisions:
                    self._suites[clean_session_id] = revisions
                else:
                    self._suites.pop(clean_session_id, None)
                raise
            return copy.deepcopy(suite)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw_bytes = self.snapshot_path.read_bytes()
            if len(raw_bytes) > _MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot too large")
            payload = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != TRIGGER_STORE_SCHEMA_VERSION:
                raise ValueError("invalid trigger snapshot")
            sessions = payload.get("sessions")
            receipts = payload.get("receipts")
            quarantine = payload.get("quarantine") or []
            if not isinstance(sessions, dict) or not isinstance(receipts, dict) or not isinstance(quarantine, list):
                raise ValueError("invalid trigger indexes")
            if len(sessions) > _MAX_SESSIONS or len(receipts) > _MAX_RECEIPTS:
                raise ValueError("trigger snapshot exceeds bounded indexes")
        except Exception as exc:
            self._load_error = f"skill_trigger_store_corrupt:{type(exc).__name__}"
            return
        for session_id, records in sessions.items():
            try:
                clean_session_id = _identifier(session_id, "session_id")
                if (
                    not isinstance(records, list)
                    or not records
                    or len(records) > _MAX_SUITES_PER_SESSION
                ):
                    raise ValueError("invalid trigger revisions")
                revisions = [_parse_suite(item) for item in records]
                if any(item.session_id != clean_session_id for item in revisions):
                    raise ValueError("trigger session mismatch")
                if [item.suite_revision for item in revisions] != list(range(1, len(revisions) + 1)):
                    raise ValueError("trigger revisions are not contiguous")
                if revisions and len({item.suite_id for item in revisions}) != 1:
                    raise ValueError("trigger suite identity changed")
                self._suites[clean_session_id] = revisions
            except Exception:
                self._quarantine.append(_quarantine_entry("skill_trigger_suite_record_invalid", records))
        for receipt_id, raw_receipt in receipts.items():
            try:
                receipt = _parse_receipt(raw_receipt)
                if receipt.receipt_id != _identifier(receipt_id, "receipt_id"):
                    raise ValueError("trigger receipt identity mismatch")
                revisions = self._suites.get(receipt.session_id) or []
                if receipt.suite_revision > len(revisions):
                    raise ValueError("trigger receipt suite missing")
                _validate_receipt_suite_binding(receipt, revisions[receipt.suite_revision - 1])
                self._receipts[receipt.receipt_id] = receipt
            except Exception:
                self._quarantine.append(_quarantine_entry("skill_trigger_receipt_record_invalid", raw_receipt))
        for item in quarantine[-200:]:
            if not isinstance(item, dict):
                continue
            digest = str(item.get("sha256") or "").lower()
            code = str(item.get("code") or "")
            if code in {"skill_trigger_suite_record_invalid", "skill_trigger_receipt_record_invalid"} and _DIGEST_RE.fullmatch(digest):
                self._quarantine.append({"code": code, "sha256": digest, "size_bytes": max(0, int(item.get("size_bytes") or 0))})

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": TRIGGER_STORE_SCHEMA_VERSION,
            "suite_version": TRIGGER_SUITE_VERSION,
            "receipt_version": TRIGGER_RECEIPT_VERSION,
            "sessions": {
                session_id: [asdict(item) for item in revisions]
                for session_id, revisions in sorted(self._suites.items())
            },
            "receipts": {
                receipt_id: asdict(receipt)
                for receipt_id, receipt in sorted(self._receipts.items())
            },
            "quarantine": list(self._quarantine[-200:]),
        }
        content = _canonical_json(payload).encode("utf-8")
        if len(content) > _MAX_SNAPSHOT_BYTES:
            raise SkillTriggerStorageError("Trigger contract Store reached its bounded capacity.")
        temporary = self.snapshot_path.with_name(f".{self.snapshot_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.snapshot_path)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillTriggerStorageError("Trigger contract Store is unavailable.")

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()


class _InstalledSkillProjection:
    def __init__(self, installed: Sequence[InstalledSkill]) -> None:
        self._installed = tuple(installed)

    def list_installed_skills(self) -> tuple[InstalledSkill, ...]:
        return self._installed


class SkillTriggerEvaluator:
    """Evaluate a draft description through the exact production Finder paths."""

    def __init__(self, finder: SkillFinder) -> None:
        self.finder = finder

    def evaluate(
        self,
        *,
        suite: SkillTriggerSuiteV1,
        skill_id: str,
        skill_name: str,
        description: str,
        sub_path: str = "",
    ) -> SkillTriggerReceiptV1:
        if suite.version != TRIGGER_SUITE_VERSION or suite.state != "confirmed":
            raise SkillCreatorValidationError(
                "A confirmed trigger suite is required.", code="skill_trigger_suite_required"
            )
        clean_skill_id = _identifier(skill_id, "skill_id")
        clean_skill_name = _skill_name(skill_name)
        if clean_skill_name != suite.skill_name:
            raise SkillTriggerConflictError("Skill name changed after trigger confirmation.")
        clean_description = _bounded_text(description, "description", maximum=2_000)
        clean_sub_path = str(sub_path or clean_skill_id).strip().replace("\\", "/")
        if not clean_sub_path or clean_sub_path.startswith("/") or ".." in clean_sub_path.split("/"):
            raise SkillCreatorValidationError(
                "Skill path is invalid.", code="skill_trigger_description_invalid"
            )
        try:
            installed = (
                list(self.finder.skill_manager.list_installed_skills())
                if self.finder.skill_manager
                else []
            )
            installed = [item for item in installed if item.skill_id != clean_skill_id]
            installed.append(
                InstalledSkill(
                    skill_id=clean_skill_id,
                    name=clean_skill_name,
                    description=clean_description,
                    repo_url=f"workspace://draft/{clean_skill_id}",
                    sub_path=clean_sub_path,
                    installed_at=0.0,
                    source_kind="workspace_draft",
                    source_id=suite.session_id,
                    source_revision=suite.suite_revision,
                    content_digest=hashlib.sha256(clean_description.encode("utf-8")).hexdigest(),
                    package_subpath=clean_sub_path,
                )
            )
            simulation = self.finder.fork_with_skill_manager(
                _InstalledSkillProjection(installed)
            )
            index_metadata = simulation.index_metadata()
            target_id = f"installed:{clean_skill_id}"
            simulation_candidates = simulation.candidates()
            target = next(item for item in simulation_candidates if item["candidateId"] == target_id)
            candidate_set_fingerprint = _sha256(
                [
                    {
                        "candidate_id": str(item["candidateId"]),
                        "candidate_fingerprint": str(item["candidateFingerprint"]),
                    }
                    for item in sorted(
                        simulation_candidates,
                        key=lambda value: str(value["candidateId"]),
                    )
                ]
            )
            case_results = tuple(
                self._evaluate_case(simulation, item, target_id=target_id)
                for item in suite.cases
            )
        except SkillRuntimeIndexError as exc:
            raise SkillTriggerStorageError(
                "Skill ranking index is unavailable.", code="skill_trigger_index_unavailable"
            ) from exc
        except (SkillCreatorValidationError, SkillTriggerConflictError, SkillTriggerStorageError):
            raise
        except Exception as exc:
            raise SkillCreatorValidationError(
                "Skill trigger evaluation failed.", code="skill_trigger_evaluation_failed"
            ) from exc
        description_digest = hashlib.sha256(clean_description.encode("utf-8")).hexdigest()
        content = {
            "version": TRIGGER_RECEIPT_VERSION,
            "suite_id": suite.suite_id,
            "suite_revision": suite.suite_revision,
            "suite_digest": suite.suite_digest,
            "session_id": suite.session_id,
            "skill_name": clean_skill_name,
            "description_digest": description_digest,
            "ranker_version": RANKER_VERSION,
            "runtime_index_fingerprint": str(index_metadata["runtimeIndexFingerprint"]),
            "directory_fingerprint": str(index_metadata["directoryFingerprint"]),
            "trust_index_fingerprint": str(index_metadata["trustIndexFingerprint"]),
            "candidate_fingerprint": str(target["candidateFingerprint"]),
            "candidate_set_fingerprint": candidate_set_fingerprint,
            "passed": all(item.passed for item in case_results),
            "case_results": [asdict(item) for item in case_results],
        }
        return SkillTriggerReceiptV1(
            receipt_id=f"triggerreceipt_{_sha256(content)[:24]}",
            case_results=case_results,
            created_at=time.time(),
            **{key: value for key, value in content.items() if key != "case_results"},
        )

    @staticmethod
    def _evaluate_case(
        finder: SkillFinder,
        case: SkillTriggerCase,
        *,
        target_id: str,
    ) -> SkillTriggerCaseResult:
        finder_result = _evaluate_domain(finder, case.text, target_id=target_id, router=False)
        router_result = _evaluate_domain(finder, case.text, target_id=target_id, router=True)
        if case.kind == "should_not_trigger":
            passed = not finder_result.in_top_6 and not router_result.in_top_6
        elif case.kind == "exact_name_smoke":
            passed = finder_result.rank_top_6 == 1 and router_result.rank_top_6 == 1
        else:
            passed = finder_result.in_top_6 and router_result.in_top_6
        return SkillTriggerCaseResult(
            case_id=case.case_id,
            case_hash=case.case_hash,
            kind=case.kind,
            finder=finder_result,
            router=router_result,
            passed=passed,
        )


def _evaluate_domain(
    finder: SkillFinder,
    query: str,
    *,
    target_id: str,
    router: bool,
) -> SkillTriggerDomainResult:
    top_24 = finder.recall(query, limit=MAX_RECALL_RESULTS, router_eligible_only=router)["results"]
    # Finder and recall share the same stable production ranker; the public
    # Top 6 window is the prefix of the diagnostic Top 24 window. Reusing the
    # single ranking pass avoids doubling every suite evaluation.
    top_6 = top_24[:MAX_RESULTS]
    top_6_rank = next((index for index, item in enumerate(top_6, 1) if item["candidateId"] == target_id), None)
    top_24_rank = next((index for index, item in enumerate(top_24, 1) if item["candidateId"] == target_id), None)
    target = next((item for item in top_24 if item["candidateId"] == target_id), None)
    reasons = tuple(
        SkillTriggerMatchReason(
            reason_type=str(item.get("type") or ""),
            origin=str(item.get("origin") or ""),
            matched_terms=tuple(str(term) for term in (item.get("matchedTerms") or [])[:4]),
        )
        for item in ((target or {}).get("reasons") or [])[:8]
        if isinstance(item, dict)
    )
    competitors = tuple(
        SkillTriggerCompetitor(
            candidate_id=str(item["candidateId"]),
            candidate_fingerprint=str(item["candidateFingerprint"]),
            rank=index,
        )
        for index, item in enumerate(top_24, 1)
        if item["candidateId"] != target_id
    )[:3]
    return SkillTriggerDomainResult(
        rank_top_6=top_6_rank,
        rank_top_24=top_24_rank,
        in_top_6=top_6_rank is not None,
        in_top_24=top_24_rank is not None,
        score=float(target["score"]) if target is not None else None,
        reasons=reasons,
        competitors=competitors,
    )


def _normalize_case_set(
    cases: Iterable[Mapping[str, Any]], *, skill_name: str
) -> tuple[SkillTriggerCase, ...]:
    normalized: list[SkillTriggerCase] = []
    seen: set[str] = set()
    counts = {"should_trigger": 0, "should_not_trigger": 0, "exact_name_smoke": 0}
    normalized_name = _normalized_text(skill_name)
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise SkillCreatorValidationError("Trigger case must be an object.", code="skill_trigger_suite_invalid")
        kind = str(raw.get("kind") or "")
        source = str(raw.get("source") or "user")
        if kind not in _CASE_KINDS or source not in _CASE_SOURCES:
            raise SkillCreatorValidationError("Trigger case kind or source is invalid.", code="skill_trigger_suite_invalid")
        text = _bounded_text(raw.get("text"), "case.text", maximum=_MAX_CASE_CHARS)
        comparison = _normalized_text(text)
        if not comparison:
            raise SkillCreatorValidationError("Trigger case cannot be empty.", code="skill_trigger_suite_invalid")
        case_hash = hashlib.sha256(comparison.encode("utf-8")).hexdigest()
        if case_hash in seen:
            raise SkillCreatorValidationError("Trigger cases must be unique.", code="skill_trigger_suite_invalid")
        contains_name = bool(normalized_name and normalized_name in comparison)
        if kind == "exact_name_smoke" and not contains_name:
            raise SkillCreatorValidationError("Exact-name smoke test must contain the Skill name.", code="skill_trigger_suite_invalid")
        if kind != "exact_name_smoke" and contains_name:
            raise SkillCreatorValidationError(
                "Exact Skill names cannot count toward required trigger cases.",
                code="skill_trigger_suite_invalid",
            )
        counts[kind] += 1
        seen.add(case_hash)
        normalized.append(
            SkillTriggerCase(
                case_id=f"triggercase_{hashlib.sha256(f'{kind}:{comparison}'.encode('utf-8')).hexdigest()[:24]}",
                kind=kind,  # type: ignore[arg-type]
                text=text,
                source=source,  # type: ignore[arg-type]
                case_hash=case_hash,
            )
        )
    if not (_MIN_REQUIRED_CASES <= counts["should_trigger"] <= _MAX_REQUIRED_CASES):
        raise SkillCreatorValidationError("Provide 2-6 should-trigger cases.", code="skill_trigger_suite_invalid")
    if not (_MIN_REQUIRED_CASES <= counts["should_not_trigger"] <= _MAX_REQUIRED_CASES):
        raise SkillCreatorValidationError("Provide 2-6 should-not-trigger cases.", code="skill_trigger_suite_invalid")
    if counts["exact_name_smoke"] > 1:
        raise SkillCreatorValidationError("Only one exact-name smoke test is allowed.", code="skill_trigger_suite_invalid")
    return tuple(normalized)


def _reject_credentials(cases: Sequence[SkillTriggerCase]) -> None:
    issues = scan_skill_package_credentials(
        files={f"trigger-cases/{item.case_id}.txt": item.text for item in cases}
    )
    if issues:
        raise SkillCreatorValidationError(
            "Trigger cases contain credential-like content.", code="skill_trigger_suite_invalid"
        )


def _check_expected(
    current: SkillTriggerSuiteV1 | None,
    expected_revision: int | None,
    expected_digest: str | None,
) -> None:
    if current is None:
        if expected_revision is not None or expected_digest is not None:
            raise SkillTriggerConflictError("Trigger suite does not exist. Reload before continuing.")
        return
    if (
        expected_revision is None
        or int(expected_revision) != current.suite_revision
        or not expected_digest
        or str(expected_digest).lower() != current.suite_digest
    ):
        raise SkillTriggerConflictError("Trigger suite changed. Reload before continuing.")


def _parse_suite(raw: Any) -> SkillTriggerSuiteV1:
    if not isinstance(raw, dict) or raw.get("version") != TRIGGER_SUITE_VERSION:
        raise ValueError("invalid trigger suite")
    _expect_keys(
        raw,
        {
            "suite_id",
            "version",
            "suite_revision",
            "suite_digest",
            "session_id",
            "session_revision",
            "definition_digest",
            "skill_name",
            "state",
            "cases",
            "change_reason",
            "based_on_revision",
            "confirmed_actor_id",
            "confirmed_at",
            "created_at",
        },
        "trigger suite",
    )
    state = str(raw.get("state") or "")
    if state not in _SUITE_STATES:
        raise ValueError("invalid trigger suite state")
    cases = tuple(_parse_case(item) for item in raw.get("cases") or [])
    suite = SkillTriggerSuiteV1(
        suite_id=_identifier(raw.get("suite_id"), "suite_id"),
        version=TRIGGER_SUITE_VERSION,
        suite_revision=_positive_int(raw.get("suite_revision"), "suite_revision"),
        suite_digest=_digest(raw.get("suite_digest"), "suite_digest"),
        session_id=_identifier(raw.get("session_id"), "session_id"),
        session_revision=_positive_int(raw.get("session_revision"), "session_revision"),
        definition_digest=_digest(raw.get("definition_digest"), "definition_digest"),
        skill_name=_skill_name(raw.get("skill_name")),
        state=state,  # type: ignore[arg-type]
        cases=cases,
        change_reason=_bounded_text(raw.get("change_reason"), "change_reason", maximum=500),
        based_on_revision=(
            _positive_int(raw.get("based_on_revision"), "based_on_revision")
            if raw.get("based_on_revision") is not None
            else None
        ),
        confirmed_actor_id=(
            _identifier(raw.get("confirmed_actor_id"), "confirmed_actor_id")
            if raw.get("confirmed_actor_id") is not None
            else None
        ),
        confirmed_at=_optional_timestamp(raw.get("confirmed_at"), "confirmed_at"),
        created_at=_timestamp(raw.get("created_at"), "created_at"),
    )
    content = asdict(suite)
    content.pop("suite_digest")
    content.pop("created_at")
    if _sha256(content) != suite.suite_digest:
        raise ValueError("trigger suite digest mismatch")
    _validate_persisted_case_set(cases, skill_name=suite.skill_name)
    return suite


def _parse_case(raw: Any) -> SkillTriggerCase:
    if not isinstance(raw, dict):
        raise ValueError("invalid trigger case")
    _expect_keys(raw, {"case_id", "kind", "text", "source", "case_hash"}, "trigger case")
    kind = str(raw.get("kind") or "")
    source = str(raw.get("source") or "")
    text = _bounded_text(raw.get("text"), "case.text", maximum=_MAX_CASE_CHARS)
    if kind not in _CASE_KINDS or source not in _CASE_SOURCES:
        raise ValueError("invalid trigger case kind")
    comparison = _normalized_text(text)
    case_hash = hashlib.sha256(comparison.encode("utf-8")).hexdigest()
    expected_id = f"triggercase_{hashlib.sha256(f'{kind}:{comparison}'.encode('utf-8')).hexdigest()[:24]}"
    if raw.get("case_hash") != case_hash or raw.get("case_id") != expected_id:
        raise ValueError("trigger case fingerprint mismatch")
    return SkillTriggerCase(
        case_id=expected_id,
        kind=kind,  # type: ignore[arg-type]
        text=text,
        source=source,  # type: ignore[arg-type]
        case_hash=case_hash,
    )


def _validate_persisted_case_set(cases: Sequence[SkillTriggerCase], *, skill_name: str) -> None:
    raw = [{"kind": item.kind, "source": item.source, "text": item.text} for item in cases]
    normalized = _normalize_case_set(raw, skill_name=skill_name)
    if tuple((item.case_id, item.case_hash) for item in normalized) != tuple(
        (item.case_id, item.case_hash) for item in cases
    ):
        raise ValueError("trigger case set mismatch")


def _parse_receipt(raw: Any) -> SkillTriggerReceiptV1:
    if not isinstance(raw, dict) or raw.get("version") != TRIGGER_RECEIPT_VERSION:
        raise ValueError("invalid trigger receipt")
    _expect_keys(
        raw,
        {
            "receipt_id",
            "version",
            "suite_id",
            "suite_revision",
            "suite_digest",
            "session_id",
            "skill_name",
            "description_digest",
            "ranker_version",
            "runtime_index_fingerprint",
            "directory_fingerprint",
            "trust_index_fingerprint",
            "candidate_fingerprint",
            "candidate_set_fingerprint",
            "passed",
            "case_results",
            "created_at",
        },
        "trigger receipt",
    )
    raw_case_results = raw.get("case_results")
    if not isinstance(raw_case_results, list) or not 4 <= len(raw_case_results) <= 13:
        raise ValueError("invalid trigger receipt cases")
    case_results = tuple(_parse_case_result(item) for item in raw_case_results)
    receipt = SkillTriggerReceiptV1(
        receipt_id=_identifier(raw.get("receipt_id"), "receipt_id"),
        version=TRIGGER_RECEIPT_VERSION,
        suite_id=_identifier(raw.get("suite_id"), "suite_id"),
        suite_revision=_positive_int(raw.get("suite_revision"), "suite_revision"),
        suite_digest=_digest(raw.get("suite_digest"), "suite_digest"),
        session_id=_identifier(raw.get("session_id"), "session_id"),
        skill_name=_skill_name(raw.get("skill_name")),
        description_digest=_digest(raw.get("description_digest"), "description_digest"),
        ranker_version=str(raw.get("ranker_version") or ""),
        runtime_index_fingerprint=_digest(raw.get("runtime_index_fingerprint"), "runtime_index_fingerprint"),
        directory_fingerprint=_digest(raw.get("directory_fingerprint"), "directory_fingerprint"),
        trust_index_fingerprint=_digest(raw.get("trust_index_fingerprint"), "trust_index_fingerprint"),
        candidate_fingerprint=_digest(raw.get("candidate_fingerprint"), "candidate_fingerprint"),
        candidate_set_fingerprint=_digest(
            raw.get("candidate_set_fingerprint"), "candidate_set_fingerprint"
        ),
        passed=_strict_bool(raw.get("passed"), "receipt.passed"),
        case_results=case_results,
        created_at=_timestamp(raw.get("created_at"), "created_at"),
    )
    _validate_receipt(receipt)
    return receipt


def _parse_case_result(raw: Any) -> SkillTriggerCaseResult:
    if not isinstance(raw, dict):
        raise ValueError("invalid trigger case result")
    _expect_keys(
        raw,
        {"case_id", "case_hash", "kind", "finder", "router", "passed"},
        "trigger case result",
    )
    kind = str(raw.get("kind") or "")
    if kind not in _CASE_KINDS:
        raise ValueError("invalid trigger case result kind")
    return SkillTriggerCaseResult(
        case_id=_identifier(raw.get("case_id"), "case_id"),
        case_hash=_digest(raw.get("case_hash"), "case_hash"),
        kind=kind,  # type: ignore[arg-type]
        finder=_parse_domain_result(raw.get("finder")),
        router=_parse_domain_result(raw.get("router")),
        passed=_strict_bool(raw.get("passed"), "case_result.passed"),
    )


def _parse_domain_result(raw: Any) -> SkillTriggerDomainResult:
    if not isinstance(raw, dict):
        raise ValueError("invalid trigger domain result")
    _expect_keys(
        raw,
        {
            "rank_top_6",
            "rank_top_24",
            "in_top_6",
            "in_top_24",
            "score",
            "reasons",
            "competitors",
        },
        "trigger domain result",
    )
    raw_reasons = raw.get("reasons")
    raw_competitors = raw.get("competitors")
    if not isinstance(raw_reasons, list) or len(raw_reasons) > 8:
        raise ValueError("invalid trigger reasons")
    if not isinstance(raw_competitors, list) or len(raw_competitors) > 3:
        raise ValueError("invalid trigger competitors")
    reasons = tuple(
        SkillTriggerMatchReason(
            reason_type=_bounded_text(item.get("reason_type"), "reason_type", maximum=80),
            origin=_bounded_text(item.get("origin"), "origin", maximum=40),
            matched_terms=_parse_matched_terms(item.get("matched_terms")),
        )
        for item in raw_reasons
        if _expect_reason(item)
    )
    competitors = tuple(
        SkillTriggerCompetitor(
            candidate_id=_identifier(item.get("candidate_id"), "candidate_id"),
            candidate_fingerprint=_digest(item.get("candidate_fingerprint"), "candidate_fingerprint"),
            rank=_positive_int(item.get("rank"), "rank"),
        )
        for item in raw_competitors
        if _expect_competitor(item)
    )
    rank_top_6 = _optional_rank(raw.get("rank_top_6"), MAX_RESULTS)
    rank_top_24 = _optional_rank(raw.get("rank_top_24"), MAX_RECALL_RESULTS)
    result = SkillTriggerDomainResult(
        rank_top_6=rank_top_6,
        rank_top_24=rank_top_24,
        in_top_6=_strict_bool(raw.get("in_top_6"), "in_top_6"),
        in_top_24=_strict_bool(raw.get("in_top_24"), "in_top_24"),
        score=(float(raw["score"]) if raw.get("score") is not None else None),
        reasons=reasons,
        competitors=competitors,
    )
    if result.in_top_6 != (rank_top_6 is not None) or result.in_top_24 != (rank_top_24 is not None):
        raise ValueError("trigger rank flags mismatch")
    return result


def _validate_receipt(receipt: SkillTriggerReceiptV1) -> None:
    if receipt.version != TRIGGER_RECEIPT_VERSION or receipt.ranker_version != RANKER_VERSION:
        raise ValueError("unsupported trigger receipt")
    _identifier(receipt.receipt_id, "receipt_id")
    _identifier(receipt.suite_id, "suite_id")
    _identifier(receipt.session_id, "session_id")
    _skill_name(receipt.skill_name)
    for value in (
        receipt.suite_digest,
        receipt.description_digest,
        receipt.runtime_index_fingerprint,
        receipt.directory_fingerprint,
        receipt.trust_index_fingerprint,
        receipt.candidate_fingerprint,
        receipt.candidate_set_fingerprint,
    ):
        _digest(value, "receipt_digest")
    if not 4 <= len(receipt.case_results) <= 13:
        raise ValueError("trigger receipt case count mismatch")
    seen_case_ids: set[str] = set()
    seen_case_hashes: set[str] = set()
    for item in receipt.case_results:
        _identifier(item.case_id, "case_id")
        _digest(item.case_hash, "case_hash")
        if item.kind not in _CASE_KINDS:
            raise ValueError("trigger receipt case kind mismatch")
        if item.case_id in seen_case_ids or item.case_hash in seen_case_hashes:
            raise ValueError("trigger receipt contains duplicate cases")
        seen_case_ids.add(item.case_id)
        seen_case_hashes.add(item.case_hash)
        _validate_domain_result(item.finder)
        _validate_domain_result(item.router)
    if receipt.passed != all(item.passed for item in receipt.case_results):
        raise ValueError("trigger receipt result mismatch")
    if any(item.passed != _case_result_passed(item) for item in receipt.case_results):
        raise ValueError("trigger receipt gate result mismatch")
    content = _receipt_content(receipt)
    if receipt.receipt_id != f"triggerreceipt_{_sha256(content)[:24]}":
        raise ValueError("trigger receipt fingerprint mismatch")


def _receipt_content(receipt: SkillTriggerReceiptV1) -> dict[str, Any]:
    content = asdict(receipt)
    content.pop("receipt_id")
    content.pop("created_at")
    return content


def _validate_receipt_suite_binding(
    receipt: SkillTriggerReceiptV1, suite: SkillTriggerSuiteV1
) -> None:
    if (
        suite.state != "confirmed"
        or receipt.suite_id != suite.suite_id
        or receipt.suite_revision != suite.suite_revision
        or receipt.suite_digest != suite.suite_digest
        or receipt.session_id != suite.session_id
        or receipt.skill_name != suite.skill_name
    ):
        raise ValueError("trigger receipt suite binding mismatch")
    expected_cases = tuple(
        (item.case_id, item.case_hash, item.kind) for item in suite.cases
    )
    actual_cases = tuple(
        (item.case_id, item.case_hash, item.kind) for item in receipt.case_results
    )
    if actual_cases != expected_cases:
        raise ValueError("trigger receipt case binding mismatch")


def _case_result_passed(result: SkillTriggerCaseResult) -> bool:
    if result.kind == "should_not_trigger":
        return not result.finder.in_top_6 and not result.router.in_top_6
    if result.kind == "exact_name_smoke":
        return result.finder.rank_top_6 == 1 and result.router.rank_top_6 == 1
    return result.finder.in_top_6 and result.router.in_top_6


def _validate_domain_result(result: SkillTriggerDomainResult) -> None:
    if result.in_top_6 != (result.rank_top_6 is not None) or result.in_top_24 != (
        result.rank_top_24 is not None
    ):
        raise ValueError("trigger rank flags mismatch")
    if result.rank_top_6 is not None and not 1 <= result.rank_top_6 <= MAX_RESULTS:
        raise ValueError("trigger Top 6 rank is invalid")
    if result.rank_top_24 is not None and not 1 <= result.rank_top_24 <= MAX_RECALL_RESULTS:
        raise ValueError("trigger Top 24 rank is invalid")
    if result.rank_top_6 is not None and result.rank_top_24 != result.rank_top_6:
        raise ValueError("trigger result windows disagree")
    if result.score is not None and not math.isfinite(result.score):
        raise ValueError("trigger score is invalid")
    if len(result.reasons) > 8 or len(result.competitors) > 3:
        raise ValueError("trigger diagnostics exceed limits")
    for reason in result.reasons:
        if reason.origin not in {"direct", "expanded"} or not reason.reason_type:
            raise ValueError("trigger match reason is invalid")
        if len(reason.matched_terms) > 4 or any(
            not term or len(term) > 100 for term in reason.matched_terms
        ):
            raise ValueError("trigger matched terms are invalid")
    competitor_ids: set[str] = set()
    competitor_ranks: set[int] = set()
    for competitor in result.competitors:
        _identifier(competitor.candidate_id, "candidate_id")
        _digest(competitor.candidate_fingerprint, "candidate_fingerprint")
        if not 1 <= competitor.rank <= MAX_RECALL_RESULTS:
            raise ValueError("trigger competitor rank is invalid")
        if competitor.candidate_id in competitor_ids or competitor.rank in competitor_ranks:
            raise ValueError("trigger competitors are duplicated")
        competitor_ids.add(competitor.candidate_id)
        competitor_ranks.add(competitor.rank)


def _quarantine_entry(code: str, value: Any) -> dict[str, Any]:
    encoded = _canonical_json(value).encode("utf-8", errors="replace")
    return {"code": code, "sha256": hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)}


def _expect_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"{label} fields are invalid")


def _expect_reason(raw: Any) -> bool:
    if not isinstance(raw, dict):
        raise ValueError("invalid trigger match reason")
    _expect_keys(raw, {"reason_type", "origin", "matched_terms"}, "trigger match reason")
    return True


def _expect_competitor(raw: Any) -> bool:
    if not isinstance(raw, dict):
        raise ValueError("invalid trigger competitor")
    _expect_keys(
        raw,
        {"candidate_id", "candidate_fingerprint", "rank"},
        "trigger competitor",
    )
    return True


def _parse_matched_terms(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("invalid trigger matched terms")
    return tuple(
        _bounded_text(term, "matched_term", maximum=100) for term in value
    )


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _timestamp(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} is invalid")
    return result


def _optional_timestamp(value: Any, field_name: str) -> float | None:
    return None if value is None else _timestamp(value, field_name)


def _optional_rank(value: Any, maximum: int) -> int | None:
    if value is None:
        return None
    rank = _positive_int(value, "rank")
    if rank > maximum:
        raise ValueError("rank exceeds result window")
    return rank


def _skill_name(value: Any) -> str:
    text = _bounded_text(value, "skill_name", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,79}", text):
        raise SkillCreatorValidationError("Skill name is invalid.", code="skill_trigger_suite_invalid")
    return text


def _bounded_text(value: Any, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise SkillCreatorValidationError(f"{field_name} must be text.", code="skill_trigger_suite_invalid")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SkillCreatorValidationError(f"{field_name} must be UTF-8 text.", code="skill_trigger_suite_invalid") from exc
    text = value.strip()
    if not text or len(text) > maximum or any(ord(character) == 0 for character in text):
        raise SkillCreatorValidationError(f"{field_name} is invalid.", code="skill_trigger_suite_invalid")
    return text


def _normalized_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"[_/\\-]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _identifier(value: Any, field_name: str) -> str:
    text = str(value or "")
    if not _IDENTIFIER_RE.fullmatch(text):
        raise SkillCreatorValidationError(f"{field_name} is invalid.", code="skill_trigger_suite_invalid")
    return text


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise SkillCreatorValidationError(f"{field_name} is invalid.", code="skill_trigger_suite_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillCreatorValidationError(f"{field_name} is invalid.", code="skill_trigger_suite_invalid") from exc
    if result <= 0:
        raise SkillCreatorValidationError(f"{field_name} is invalid.", code="skill_trigger_suite_invalid")
    return result


def _digest(value: Any, field_name: str) -> str:
    text = str(value or "").lower()
    if not _DIGEST_RE.fullmatch(text):
        raise SkillCreatorValidationError(f"{field_name} is invalid.", code="skill_trigger_suite_invalid")
    return text


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "SkillTriggerCase",
    "SkillTriggerCaseResult",
    "SkillTriggerConflictError",
    "SkillTriggerDomainResult",
    "SkillTriggerEvaluator",
    "SkillTriggerNotFoundError",
    "SkillTriggerReceiptV1",
    "SkillTriggerStorageError",
    "SkillTriggerStore",
    "SkillTriggerSuiteV1",
    "TRIGGER_RECEIPT_VERSION",
    "TRIGGER_SUITE_VERSION",
    "trigger_definition_digest",
    "trigger_optimization_enabled",
]
