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
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,239}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_RECORDS = 2_000
_MAX_SELECTED_EVIDENCE = 6
_MAX_RECEIPT_REFERENCES = 64
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
    return os.getenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "false").strip().lower() in {
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
    evidence_raw = raw.get("selected_evidence") or []
    receipts_raw = raw.get("application_receipts") or []
    if not isinstance(evidence_raw, list) or not isinstance(receipts_raw, list):
        raise ValueError("invalid candidate children")
    if len(evidence_raw) > _MAX_SELECTED_EVIDENCE or len(receipts_raw) > _MAX_RECEIPT_REFERENCES:
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
    if candidate.digest != _candidate_digest(replace(candidate, digest="")):
        raise ValueError("candidate digest mismatch")
    return candidate


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


def _reject_credentials(value: str) -> None:
    if scan_skill_package_credentials(skill_markdown=value):
        raise SkillExperienceError(
            "Sensitive credential material cannot be stored as Skill experience data.",
            code="skill_experience_source_invalid",
        )
