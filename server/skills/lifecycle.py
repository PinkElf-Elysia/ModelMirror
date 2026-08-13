from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping

from .draft_store import (
    SkillDraftError,
    WorkspaceSkillDraftStore,
)
from .local_import import SkillLocalImportError, SkillLocalImportStore
from .package_validation import compute_package_digest
from .skill_manager import InstalledSkill, SkillManager, SkillManagerError
from .trust_scanner import (
    MAX_TRUST_DIRECTORY_DEPTH,
    MAX_TRUST_FILE_BYTES,
    MAX_TRUST_FILES,
    MAX_TRUST_PATH_CHARS,
    MAX_TRUST_TOTAL_BYTES,
)
from .trust_service import SkillTrustError


SKILL_LIFECYCLE_STORE_VERSION = 1
SKILL_LIFECYCLE_PROTOCOL_VERSION = "skill-lifecycle-v1"
SKILL_LIFECYCLE_DEFAULT_MAX_VERSIONS = 5
SKILL_LIFECYCLE_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
SKILL_LIFECYCLE_MAX_EVENTS = 200
SKILL_LIFECYCLE_MAX_INDEX_BYTES = 16 * 1024 * 1024

LifecycleSourceKind = Literal["git", "local_import", "workspace_draft"]
LifecycleStateStatus = Literal["active", "uninstalled", "migration_blocked"]
LifecycleTransactionPhase = Literal[
    "prepared",
    "archived",
    "swapped",
    "metadata_committed",
    "source_projected",
    "lifecycle_committed",
]

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


class SkillLifecycleError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "skill_lifecycle_storage_unavailable",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class SkillLifecycleDisabledError(SkillLifecycleError):
    pass


class SkillLifecycleStorageError(SkillLifecycleError):
    pass


class SkillLifecycleConflictError(SkillLifecycleError):
    pass


class SkillLifecycleValidationError(SkillLifecycleError):
    pass


@dataclass(frozen=True, slots=True)
class SkillLifecycleEvent:
    event_id: str
    kind: str
    version_id: str | None
    reason_code: str | None
    actor_kind: Literal["system_migration", "local_console"]
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class SkillVersionSnapshot:
    version_id: str
    skill_id: str
    ordinal: int
    package_digest: str
    file_count: int
    total_bytes: int
    source_kind: LifecycleSourceKind
    source_id: str | None
    source_revision: int | None
    repo_url: str
    sub_path: str
    source_ref: str | None
    trust_receipt_id: str | None
    trust_fingerprint: str | None
    trust_directory_tree_sha: str | None
    trust_receipt_snapshot: dict[str, Any] | None
    trust_risk_level: str | None
    trust_status: str | None
    trust_install_policy: str | None
    trust_compatibility_status: str | None
    trust_router_eligible: bool
    quality_required: bool
    quality_evidence_status: str
    quality_status: str | None
    quality_decision_id: str | None
    quality_run_id: str | None
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class SkillLifecycleState:
    skill_id: str
    revision: int
    status: LifecycleStateStatus
    current_version_id: str | None
    recovery_version_id: str | None
    protected_version_ids: tuple[str, ...]
    version_ids: tuple[str, ...]
    migration_code: str | None
    events: tuple[SkillLifecycleEvent, ...]
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class SkillLifecycleTransaction:
    transaction_id: str
    skill_id: str
    operation: Literal["install", "replace", "rollback", "uninstall"]
    phase: LifecycleTransactionPhase
    previous_version_id: str | None
    target_version_id: str | None
    expected_state_revision: int
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class InstalledPackageSnapshot:
    installed: InstalledSkill
    files: dict[str, bytes]
    error_code: str | None = None


def _env_enabled(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _configured_int(value: int | str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        return int(default)
    return parsed


def _version_identity(
    installed: InstalledSkill,
    package_digest: str,
    *,
    quality_required: bool,
    quality_decision_id: str | None,
) -> dict[str, Any]:
    """Bind immutable bytes to the exact trust or quality evidence edition."""

    identity: dict[str, Any] = {
        "skillId": installed.skill_id,
        "sourceKind": installed.source_kind,
        "sourceId": installed.source_id,
        "sourceRevision": installed.source_revision,
        "sourceRef": installed.source_ref,
        "packageDigest": package_digest,
    }
    if installed.source_kind in {"git", "local_import"}:
        identity["trustFingerprint"] = installed.trust_fingerprint
    if quality_required:
        identity["qualityDecisionId"] = quality_decision_id
    return identity


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_id(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not _ID_RE.fullmatch(clean):
        raise ValueError(f"invalid {label}")
    return clean


def _validate_digest(value: str, label: str = "digest") -> str:
    clean = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(clean):
        raise ValueError(f"invalid {label}")
    return clean


def _validate_relative_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value or "").replace("\\", "/"))
    if not normalized or len(normalized) > MAX_TRUST_PATH_CHARS or "\x00" in normalized:
        raise SkillLifecycleValidationError(
            "Skill lifecycle package contains an unsafe path.",
            code="skill_lifecycle_package_invalid",
        )
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise SkillLifecycleValidationError(
            "Skill lifecycle package contains an unsafe path.",
            code="skill_lifecycle_package_invalid",
        )
    if len(pure.parts) - 1 > MAX_TRUST_DIRECTORY_DEPTH:
        raise SkillLifecycleValidationError(
            "Skill lifecycle package exceeds the directory depth limit.",
            code="skill_lifecycle_package_limit_exceeded",
        )
    for part in pure.parts:
        if part != part.rstrip(". "):
            raise SkillLifecycleValidationError(
                "Skill lifecycle package contains a non-portable path.",
                code="skill_lifecycle_package_invalid",
            )
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED:
            raise SkillLifecycleValidationError(
                "Skill lifecycle package contains a reserved path.",
                code="skill_lifecycle_package_invalid",
            )
    return pure.as_posix()


class SkillLifecycleStore:
    """Immutable, content-addressed history for installed Skill packages."""

    def __init__(
        self,
        storage_dir: str | os.PathLike[str] | None = None,
        *,
        enabled: bool | None = None,
        max_versions: int | None = None,
        max_storage_bytes: int | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.root = Path(
            storage_dir
            or os.getenv("SKILL_LIFECYCLE_STORAGE_DIR", "").strip()
            or package_dir / "lifecycle"
        ).resolve()
        self.enabled = (
            _env_enabled("SKILL_LIFECYCLE_ENABLED", default="true")
            if enabled is None
            else bool(enabled)
        )
        self.max_versions = max(
            1,
            min(
                100,
                _configured_int(
                    max_versions
                    if max_versions is not None
                    else os.getenv(
                        "SKILL_LIFECYCLE_MAX_VERSIONS",
                        str(SKILL_LIFECYCLE_DEFAULT_MAX_VERSIONS),
                    ),
                    SKILL_LIFECYCLE_DEFAULT_MAX_VERSIONS,
                ),
            ),
        )
        self.max_storage_bytes = max(
            MAX_TRUST_TOTAL_BYTES,
            _configured_int(
                max_storage_bytes
                if max_storage_bytes is not None
                else os.getenv(
                    "SKILL_LIFECYCLE_MAX_BYTES",
                    str(SKILL_LIFECYCLE_DEFAULT_MAX_BYTES),
                ),
                SKILL_LIFECYCLE_DEFAULT_MAX_BYTES,
            ),
        )
        self.index_path = self.root / "skill_lifecycle.json"
        self.packages_root = self.root / "packages"
        self.tmp_root = self.root / "tmp"
        self.transactions_root = self.root / "transactions"
        self._lock = threading.RLock()
        self._states: dict[str, SkillLifecycleState] = {}
        self._versions: dict[str, SkillVersionSnapshot] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()
        if self._load_error is None:
            self._recover_temp_unlocked()

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def status(self) -> dict[str, Any]:
        with self._lock:
            package_digests = {item.package_digest for item in self._versions.values()}
            transaction_count = 0
            if self.transactions_root.is_dir() and not self.transactions_root.is_symlink():
                transaction_count = sum(
                    1 for path in self.transactions_root.iterdir() if path.suffix == ".json"
                )
            return {
                "enabled": self.enabled,
                "available": self._load_error is None,
                "version": SKILL_LIFECYCLE_PROTOCOL_VERSION,
                "storeVersion": SKILL_LIFECYCLE_STORE_VERSION,
                "limits": {
                    "nonCurrentVersionsPerSkill": self.max_versions,
                    "storageBytes": self.max_storage_bytes,
                    "fileCount": MAX_TRUST_FILES,
                    "fileBytes": MAX_TRUST_FILE_BYTES,
                    "packageBytes": MAX_TRUST_TOTAL_BYTES,
                },
                "counts": {
                    "skills": len(self._states),
                    "versions": len(self._versions),
                    "packages": len(package_digests),
                    "quarantinedRecords": len(self._quarantine),
                    "migrationBlocked": sum(
                        item.status == "migration_blocked"
                        for item in self._states.values()
                    ),
                },
                "storageBytes": self._storage_bytes_unlocked(),
                "pendingTransactions": transaction_count,
                "errorCode": self._load_error,
            }

    def list_states(self) -> list[SkillLifecycleState]:
        with self._lock:
            self._ensure_available()
            return [copy.deepcopy(self._states[key]) for key in sorted(self._states)]

    def require_state(self, skill_id: str) -> SkillLifecycleState:
        clean = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available()
            item = self._states.get(clean)
            if item is None:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle state was not found.",
                    code="skill_lifecycle_not_found",
                )
            return copy.deepcopy(item)

    def require_version(self, version_id: str) -> SkillVersionSnapshot:
        clean = _validate_id(version_id, "version ID")
        with self._lock:
            self._ensure_available()
            item = self._versions.get(clean)
            if item is None:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle version was not found.",
                    code="skill_lifecycle_not_found",
                )
            self._verify_package_unlocked(item.package_digest)
            return copy.deepcopy(item)

    def list_versions(self, skill_id: str) -> list[SkillVersionSnapshot]:
        state = self.require_state(skill_id)
        with self._lock:
            return [
                copy.deepcopy(self._versions[version_id])
                for version_id in reversed(state.version_ids)
            ]

    def package_directory(self, version_id: str) -> Path:
        item = self.require_version(version_id)
        return self.packages_root / item.package_digest

    def bind_current_versions(self, skill_ids: list[str] | set[str] | tuple[str, ...]) -> dict[str, str]:
        bindings: dict[str, str] = {}
        with self._lock:
            self._ensure_available()
            for raw_skill_id in sorted({str(item).strip() for item in skill_ids if str(item).strip()}):
                skill_id = _validate_id(raw_skill_id, "skill ID")
                transaction = self._read_transaction_unlocked(skill_id, missing_ok=True)
                if transaction is not None and transaction.phase != "lifecycle_committed":
                    raise SkillLifecycleConflictError(
                        "Skill lifecycle transaction is incomplete.",
                        code="skill_lifecycle_transaction_incomplete",
                        details={"skillId": skill_id, "phase": transaction.phase},
                    )
                state = self._states.get(skill_id)
                if (
                    state is None
                    or state.status != "active"
                    or state.current_version_id is None
                ):
                    raise SkillLifecycleConflictError(
                        "Installed Skill does not have an active immutable version.",
                        code="skill_lifecycle_version_unavailable",
                        details={"skillId": skill_id},
                    )
                self._verify_package_unlocked(
                    self._versions[state.current_version_id].package_digest
                )
                bindings[skill_id] = state.current_version_id
        return bindings

    def stage_version(
        self,
        *,
        installed: InstalledSkill,
        files: Mapping[str, bytes],
        quality_evidence_status: str = "not_applicable",
        quality_required: bool = False,
        quality_status: str | None = None,
        quality_decision_id: str | None = None,
        quality_run_id: str | None = None,
        trust_receipt_snapshot: Mapping[str, Any] | None = None,
        actor_kind: Literal["system_migration", "local_console"] = "local_console",
        event_kind: str = "version_archived",
    ) -> SkillVersionSnapshot:
        """Persist one immutable package without changing the active pointer."""

        if installed.source_kind not in {"git", "local_import", "workspace_draft"}:
            raise SkillLifecycleValidationError(
                "This Skill source is outside the lifecycle scope.",
                code="skill_lifecycle_source_unsupported",
            )
        normalized, digest, total_bytes = self._normalize_files(files)
        if digest != installed.content_digest:
            raise SkillLifecycleConflictError(
                "Installed Skill bytes changed while creating lifecycle history.",
                code="skill_lifecycle_package_mismatch",
            )
        skill_id = _validate_id(installed.skill_id, "skill ID")
        identity = _version_identity(
            installed,
            digest,
            quality_required=quality_required,
            quality_decision_id=quality_decision_id,
        )
        version_id = "skillver_" + hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]
        with self._lock:
            self._ensure_available(mutation=True)
            existing_version = self._versions.get(version_id)
            if existing_version is None:
                existing_version = self._matching_version_unlocked(
                    installed,
                    digest,
                    quality_required=quality_required,
                    quality_decision_id=quality_decision_id,
                )
                if existing_version is not None:
                    version_id = existing_version.version_id
            if existing_version is not None:
                if existing_version.skill_id != skill_id or existing_version.package_digest != digest:
                    raise SkillLifecycleConflictError(
                        "Skill lifecycle version identity is inconsistent.",
                        code="skill_lifecycle_version_conflict",
                    )
                self._verify_package_unlocked(digest)
                existing_version = self._enrich_trust_snapshot_unlocked(
                    existing_version,
                    installed=installed,
                    receipt=trust_receipt_snapshot,
                )
                return copy.deepcopy(existing_version)
            existing_state = self._states.get(skill_id)
            version_ids = list(existing_state.version_ids if existing_state else ())
            non_current = [
                item
                for item in version_ids
                if item != (existing_state.current_version_id if existing_state else None)
            ]
            if len(non_current) >= self.max_versions:
                raise SkillLifecycleStorageError(
                    "Skill lifecycle retention is full and protected versions cannot be pruned.",
                    code="skill_lifecycle_retention_full",
                )
            now = time.time()
            frozen_receipt = self._normalize_trust_snapshot(
                installed, trust_receipt_snapshot, digest
            )
            version = SkillVersionSnapshot(
                version_id=version_id,
                skill_id=skill_id,
                ordinal=max(
                    [self._versions[item].ordinal for item in version_ids if item in self._versions]
                    or [0]
                ) + 1,
                package_digest=digest,
                file_count=len(normalized),
                total_bytes=total_bytes,
                source_kind=installed.source_kind,  # type: ignore[arg-type]
                source_id=installed.source_id,
                source_revision=installed.source_revision,
                repo_url=installed.repo_url,
                sub_path=installed.sub_path,
                source_ref=installed.source_ref,
                trust_receipt_id=installed.trust_receipt_id,
                trust_fingerprint=installed.trust_fingerprint,
                trust_directory_tree_sha=installed.trust_directory_tree_sha,
                trust_receipt_snapshot=frozen_receipt,
                trust_risk_level=installed.trust_risk_level,
                trust_status=installed.trust_status,
                trust_install_policy=installed.trust_install_policy,
                trust_compatibility_status=installed.trust_compatibility_status,
                trust_router_eligible=installed.trust_router_eligible,
                quality_required=bool(quality_required),
                quality_evidence_status=str(quality_evidence_status or "not_applicable"),
                quality_status=quality_status,
                quality_decision_id=quality_decision_id,
                quality_run_id=quality_run_id,
                created_at=now,
            )
            version_ids.append(version_id)
            event = SkillLifecycleEvent(
                event_id="skillevent_" + uuid.uuid4().hex,
                kind=str(event_kind or "version_archived")[:80],
                version_id=version_id,
                reason_code=None,
                actor_kind=actor_kind,
                created_at=now,
            )
            state = SkillLifecycleState(
                skill_id=skill_id,
                revision=(existing_state.revision + 1 if existing_state else 1),
                status=(existing_state.status if existing_state else "uninstalled"),
                current_version_id=(existing_state.current_version_id if existing_state else None),
                recovery_version_id=(existing_state.recovery_version_id if existing_state else None),
                protected_version_ids=(existing_state.protected_version_ids if existing_state else ()),
                version_ids=tuple(version_ids),
                migration_code=(existing_state.migration_code if existing_state else None),
                events=self._append_event(existing_state, event),
                created_at=(existing_state.created_at if existing_state else now),
                updated_at=now,
            )
            versions = dict(self._versions)
            versions[version_id] = version
            states = dict(self._states)
            states[skill_id] = state
            created_package = self._persist_package_unlocked(digest, normalized)
            try:
                self._save_unlocked(states, versions, self._quarantine)
            except BaseException:
                if created_package and not any(
                    item.package_digest == digest for item in self._versions.values()
                ):
                    shutil.rmtree(self.packages_root / digest, ignore_errors=True)
                raise
            self._versions = versions
            self._states = states
            return copy.deepcopy(version)

    def activate_version(
        self,
        skill_id: str,
        version_id: str,
        *,
        expected_revision: int,
        event_kind: Literal["installed", "replaced", "rolled_back", "recovered"] = "installed",
    ) -> SkillLifecycleState:
        clean_skill_id = _validate_id(skill_id, "skill ID")
        clean_version_id = _validate_id(version_id, "version ID")
        with self._lock:
            self._ensure_available(mutation=True)
            state = self._states.get(clean_skill_id)
            version = self._versions.get(clean_version_id)
            if state is None or version is None or version.skill_id != clean_skill_id:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle version was not found.",
                    code="skill_lifecycle_not_found",
                )
            if state.revision != expected_revision:
                raise SkillLifecycleConflictError(
                    "Skill lifecycle state changed. Reload before switching versions.",
                    code="skill_lifecycle_version_conflict",
                )
            if state.status == "active" and state.current_version_id == clean_version_id:
                return copy.deepcopy(state)
            now = time.time()
            event = SkillLifecycleEvent(
                event_id="skillevent_" + uuid.uuid4().hex,
                kind=event_kind,
                version_id=clean_version_id,
                reason_code=None,
                actor_kind="local_console",
                created_at=now,
            )
            updated = SkillLifecycleState(
                **{
                    **asdict(state),
                    "revision": state.revision + 1,
                    "status": "active",
                    "current_version_id": clean_version_id,
                    "recovery_version_id": (
                        state.current_version_id
                        if state.current_version_id != clean_version_id
                        else state.recovery_version_id
                    ),
                    "migration_code": None,
                    "events": self._append_event(state, event),
                    "updated_at": now,
                }
            )
            states = dict(self._states)
            states[clean_skill_id] = updated
            self._save_unlocked(states, self._versions, self._quarantine)
            self._states = states
            return copy.deepcopy(updated)

    def mark_uninstalled(
        self,
        skill_id: str,
        *,
        expected_revision: int,
    ) -> SkillLifecycleState:
        clean = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available(mutation=True)
            state = self._states.get(clean)
            if state is None:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle state was not found.", code="skill_lifecycle_not_found"
                )
            if state.revision != expected_revision:
                raise SkillLifecycleConflictError(
                    "Skill lifecycle state changed. Reload before uninstalling.",
                    code="skill_lifecycle_version_conflict",
                )
            if state.status == "uninstalled" and state.current_version_id is None:
                return copy.deepcopy(state)
            now = time.time()
            event = SkillLifecycleEvent(
                event_id="skillevent_" + uuid.uuid4().hex,
                kind="uninstalled",
                version_id=state.current_version_id,
                reason_code=None,
                actor_kind="local_console",
                created_at=now,
            )
            updated = SkillLifecycleState(
                **{
                    **asdict(state),
                    "revision": state.revision + 1,
                    "status": "uninstalled",
                    "current_version_id": None,
                    "recovery_version_id": state.current_version_id or state.recovery_version_id,
                    "events": self._append_event(state, event),
                    "updated_at": now,
                }
            )
            states = dict(self._states)
            states[clean] = updated
            self._save_unlocked(states, self._versions, self._quarantine)
            self._states = states
            return copy.deepcopy(updated)

    def prepare_transaction(
        self,
        *,
        skill_id: str,
        operation: Literal["install", "replace", "rollback", "uninstall"],
        previous_version_id: str | None,
        target_version_id: str | None,
        expected_state_revision: int,
    ) -> SkillLifecycleTransaction:
        clean_skill_id = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available(mutation=True)
            state = self._states.get(clean_skill_id)
            if state is None or state.revision != expected_state_revision:
                raise SkillLifecycleConflictError(
                    "Skill lifecycle state changed before the transaction started.",
                    code="skill_lifecycle_version_conflict",
                )
            if previous_version_id is not None and previous_version_id not in state.version_ids:
                raise SkillLifecycleConflictError(
                    "Previous lifecycle version is no longer available.",
                    code="skill_lifecycle_version_conflict",
                )
            if target_version_id is not None and target_version_id not in state.version_ids:
                raise SkillLifecycleConflictError(
                    "Target lifecycle version is no longer available.",
                    code="skill_lifecycle_version_conflict",
                )
            existing = self._read_transaction_unlocked(clean_skill_id, missing_ok=True)
            if existing is not None:
                if (
                    existing.operation == operation
                    and existing.previous_version_id == previous_version_id
                    and existing.target_version_id == target_version_id
                    and existing.expected_state_revision == expected_state_revision
                ):
                    return existing
                raise SkillLifecycleConflictError(
                    "Another Skill lifecycle transaction is incomplete.",
                    code="skill_lifecycle_transaction_incomplete",
                    details={"phase": existing.phase},
                )
            now = time.time()
            receipt = SkillLifecycleTransaction(
                transaction_id="skilltxn_" + uuid.uuid4().hex,
                skill_id=clean_skill_id,
                operation=operation,
                phase="prepared",
                previous_version_id=previous_version_id,
                target_version_id=target_version_id,
                expected_state_revision=expected_state_revision,
                created_at=now,
                updated_at=now,
            )
            self._write_transaction_unlocked(receipt)
            return receipt

    def advance_transaction(
        self,
        skill_id: str,
        *,
        transaction_id: str,
        expected_phase: LifecycleTransactionPhase,
        phase: LifecycleTransactionPhase,
    ) -> SkillLifecycleTransaction:
        order: tuple[LifecycleTransactionPhase, ...] = (
            "prepared",
            "archived",
            "swapped",
            "metadata_committed",
            "source_projected",
            "lifecycle_committed",
        )
        if order.index(phase) != order.index(expected_phase) + 1:
            raise SkillLifecycleValidationError(
                "Skill lifecycle transaction phase transition is invalid.",
                code="skill_lifecycle_transaction_invalid",
            )
        clean_skill_id = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available(mutation=True)
            current = self._read_transaction_unlocked(clean_skill_id)
            if current.transaction_id != transaction_id:
                raise SkillLifecycleConflictError(
                    "Skill lifecycle transaction changed.",
                    code="skill_lifecycle_version_conflict",
                )
            if current.phase == phase:
                return current
            if current.phase != expected_phase:
                raise SkillLifecycleConflictError(
                    "Skill lifecycle transaction phase changed.",
                    code="skill_lifecycle_transaction_incomplete",
                    details={"phase": current.phase},
                )
            updated = SkillLifecycleTransaction(
                **{**asdict(current), "phase": phase, "updated_at": time.time()}
            )
            self._write_transaction_unlocked(updated)
            return updated

    def require_transaction(self, skill_id: str) -> SkillLifecycleTransaction:
        clean = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available()
            return self._read_transaction_unlocked(clean)

    def finish_transaction(self, skill_id: str, *, transaction_id: str) -> None:
        clean = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available(mutation=True)
            receipt = self._read_transaction_unlocked(clean)
            if receipt.transaction_id != transaction_id or receipt.phase != "lifecycle_committed":
                raise SkillLifecycleConflictError(
                    "Skill lifecycle transaction is not fully committed.",
                    code="skill_lifecycle_transaction_incomplete",
                )
            self._transaction_path(clean).unlink(missing_ok=True)

    def abort_transaction(self, skill_id: str, *, transaction_id: str) -> None:
        """Discard a transaction that did not commit installed metadata."""

        clean = _validate_id(skill_id, "skill ID")
        with self._lock:
            self._ensure_available(mutation=True)
            receipt = self._read_transaction_unlocked(clean)
            if receipt.transaction_id != transaction_id or receipt.phase not in {
                "prepared", "archived", "swapped"
            }:
                raise SkillLifecycleConflictError(
                    "Committed Skill lifecycle transactions cannot be aborted.",
                    code="skill_lifecycle_transaction_incomplete",
                )
            self._transaction_path(clean).unlink(missing_ok=True)

    def _transaction_path(self, skill_id: str) -> Path:
        return self.transactions_root / f"{_validate_id(skill_id, 'skill ID')}.json"

    def _write_transaction_unlocked(self, receipt: SkillLifecycleTransaction) -> None:
        self._ensure_storage_paths_unlocked()
        self.transactions_root.mkdir(parents=True, exist_ok=True)
        path = self._transaction_path(receipt.skill_id)
        temporary = self.transactions_root / f".{path.name}.{uuid.uuid4().hex}.tmp"
        encoded = json.dumps(asdict(receipt), ensure_ascii=False, sort_keys=True).encode("utf-8")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise SkillLifecycleStorageError(
                "Skill lifecycle transaction could not be persisted.",
                code="skill_lifecycle_storage_unavailable",
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _read_transaction_unlocked(
        self, skill_id: str, *, missing_ok: bool = False
    ) -> SkillLifecycleTransaction | None:
        path = self._transaction_path(skill_id)
        if not path.exists():
            if missing_ok:
                return None
            raise SkillLifecycleValidationError(
                "Skill lifecycle transaction was not found.",
                code="skill_lifecycle_not_found",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            receipt = SkillLifecycleTransaction(**raw)
            if (
                receipt.skill_id != skill_id
                or receipt.operation not in {"install", "replace", "rollback", "uninstall"}
                or receipt.phase not in {
                    "prepared", "archived", "swapped", "metadata_committed",
                    "source_projected", "lifecycle_committed",
                }
                or not receipt.transaction_id.startswith("skilltxn_")
                or receipt.expected_state_revision < 1
            ):
                raise ValueError("invalid transaction")
            return receipt
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SkillLifecycleStorageError(
                "Skill lifecycle transaction is corrupt.",
                code="skill_lifecycle_storage_unavailable",
            ) from exc

    def record_migrated_current(
        self,
        *,
        installed: InstalledSkill,
        files: Mapping[str, bytes],
        quality_evidence_status: str = "not_applicable",
        quality_required: bool = False,
        quality_status: str | None = None,
        quality_decision_id: str | None = None,
        quality_run_id: str | None = None,
        trust_receipt_snapshot: Mapping[str, Any] | None = None,
    ) -> SkillLifecycleState:
        if installed.source_kind not in {"git", "local_import", "workspace_draft"}:
            raise SkillLifecycleValidationError(
                "This Skill source is outside the lifecycle scope.",
                code="skill_lifecycle_source_unsupported",
            )
        normalized, digest, total_bytes = self._normalize_files(files)
        if digest != installed.content_digest:
            raise SkillLifecycleConflictError(
                "Installed Skill bytes changed during lifecycle migration.",
                code="skill_lifecycle_package_mismatch",
            )
        skill_id = _validate_id(installed.skill_id, "skill ID")
        identity = _version_identity(
            installed,
            digest,
            quality_required=quality_required,
            quality_decision_id=quality_decision_id,
        )
        version_id = "skillver_" + hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]
        with self._lock:
            self._ensure_available(mutation=True)
            existing_version = self._versions.get(version_id)
            if existing_version is None:
                existing_version = self._matching_version_unlocked(
                    installed,
                    digest,
                    quality_required=quality_required,
                    quality_decision_id=quality_decision_id,
                )
                if existing_version is not None:
                    version_id = existing_version.version_id
            existing_state = self._states.get(skill_id)
            if existing_version is not None:
                if (
                    existing_version.package_digest != digest
                    or existing_version.skill_id != skill_id
                ):
                    raise SkillLifecycleConflictError(
                        "Skill lifecycle version identity is inconsistent.",
                        code="skill_lifecycle_version_conflict",
                    )
                existing_version = self._enrich_trust_snapshot_unlocked(
                    existing_version,
                    installed=installed,
                    receipt=trust_receipt_snapshot,
                )
                if (
                    existing_state is not None
                    and existing_state.current_version_id == version_id
                    and existing_state.status == "active"
                ):
                    self._verify_package_unlocked(digest)
                    return copy.deepcopy(existing_state)
            current_versions = list(existing_state.version_ids if existing_state else ())
            if version_id not in current_versions:
                non_current = [
                    item
                    for item in current_versions
                    if item != (existing_state.current_version_id if existing_state else None)
                ]
                if len(non_current) >= self.max_versions:
                    raise SkillLifecycleStorageError(
                        "Skill lifecycle retention is full and protected versions cannot be pruned.",
                        code="skill_lifecycle_retention_full",
                    )
                current_versions.append(version_id)
            now = time.time()
            frozen_receipt = self._normalize_trust_snapshot(
                installed, trust_receipt_snapshot, digest
            )
            version = existing_version or SkillVersionSnapshot(
                version_id=version_id,
                skill_id=skill_id,
                ordinal=max(
                    [
                        self._versions[item].ordinal
                        for item in current_versions
                        if item in self._versions
                    ]
                    or [0]
                )
                + 1,
                package_digest=digest,
                file_count=len(normalized),
                total_bytes=total_bytes,
                source_kind=installed.source_kind,  # type: ignore[arg-type]
                source_id=installed.source_id,
                source_revision=installed.source_revision,
                repo_url=installed.repo_url,
                sub_path=installed.sub_path,
                source_ref=installed.source_ref,
                trust_receipt_id=installed.trust_receipt_id,
                trust_fingerprint=installed.trust_fingerprint,
                trust_directory_tree_sha=installed.trust_directory_tree_sha,
                trust_receipt_snapshot=frozen_receipt,
                trust_risk_level=installed.trust_risk_level,
                trust_status=installed.trust_status,
                trust_install_policy=installed.trust_install_policy,
                trust_compatibility_status=installed.trust_compatibility_status,
                trust_router_eligible=installed.trust_router_eligible,
                quality_required=bool(quality_required),
                quality_evidence_status=str(quality_evidence_status or "not_applicable"),
                quality_status=quality_status,
                quality_decision_id=quality_decision_id,
                quality_run_id=quality_run_id,
                created_at=now,
            )
            event = SkillLifecycleEvent(
                event_id="skillevent_" + uuid.uuid4().hex,
                kind="migrated_current",
                version_id=version_id,
                reason_code=None,
                actor_kind="system_migration",
                created_at=now,
            )
            state = SkillLifecycleState(
                skill_id=skill_id,
                revision=(existing_state.revision + 1 if existing_state else 1),
                status="active",
                current_version_id=version_id,
                recovery_version_id=(
                    existing_state.recovery_version_id if existing_state else None
                ),
                protected_version_ids=(
                    existing_state.protected_version_ids if existing_state else ()
                ),
                version_ids=tuple(current_versions),
                migration_code=None,
                events=self._append_event(existing_state, event),
                created_at=existing_state.created_at if existing_state else now,
                updated_at=now,
            )
            versions = dict(self._versions)
            versions[version_id] = version
            states = dict(self._states)
            states[skill_id] = state
            created_package = self._persist_package_unlocked(digest, normalized)
            try:
                self._save_unlocked(states, versions, self._quarantine)
            except Exception:
                if created_package and not any(
                    item.package_digest == digest for item in self._versions.values()
                ):
                    shutil.rmtree(self.packages_root / digest, ignore_errors=True)
                raise
            self._versions = versions
            self._states = states
            return copy.deepcopy(state)

    def record_migration_blocked(
        self,
        skill_id: str,
        *,
        reason_code: str,
    ) -> SkillLifecycleState:
        clean = _validate_id(skill_id, "skill ID")
        clean_reason = str(reason_code or "skill_lifecycle_migration_blocked")[:120]
        with self._lock:
            self._ensure_available(mutation=True)
            existing = self._states.get(clean)
            if (
                existing is not None
                and existing.status == "migration_blocked"
                and existing.migration_code == clean_reason
            ):
                return copy.deepcopy(existing)
            now = time.time()
            event = SkillLifecycleEvent(
                event_id="skillevent_" + uuid.uuid4().hex,
                kind="migration_blocked",
                version_id=None,
                reason_code=clean_reason,
                actor_kind="system_migration",
                created_at=now,
            )
            state = SkillLifecycleState(
                skill_id=clean,
                revision=(existing.revision + 1 if existing else 1),
                status="migration_blocked",
                current_version_id=existing.current_version_id if existing else None,
                recovery_version_id=existing.recovery_version_id if existing else None,
                protected_version_ids=(
                    existing.protected_version_ids if existing else ()
                ),
                version_ids=existing.version_ids if existing else (),
                migration_code=clean_reason,
                events=self._append_event(existing, event),
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            states = dict(self._states)
            states[clean] = state
            self._save_unlocked(states, self._versions, self._quarantine)
            self._states = states
            return copy.deepcopy(state)

    @staticmethod
    def serialize_state(item: SkillLifecycleState) -> dict[str, Any]:
        payload = asdict(item)
        payload["version_ids"] = list(item.version_ids)
        payload["protected_version_ids"] = list(item.protected_version_ids)
        payload["events"] = [asdict(event) for event in item.events]
        return payload

    @staticmethod
    def serialize_version(item: SkillVersionSnapshot) -> dict[str, Any]:
        payload = asdict(item)
        payload.pop("trust_receipt_snapshot", None)
        payload["trust_evidence_frozen"] = item.trust_receipt_snapshot is not None
        return payload

    @staticmethod
    def _normalize_trust_snapshot(
        installed: InstalledSkill,
        receipt: Mapping[str, Any] | None,
        package_digest: str,
    ) -> dict[str, Any] | None:
        if receipt is None:
            return None
        if not isinstance(receipt, Mapping):
            raise SkillLifecycleValidationError(
                "Skill lifecycle trust receipt snapshot is invalid.",
                code="skill_lifecycle_source_unverified",
            )
        clean = json.loads(json.dumps(dict(receipt), ensure_ascii=False))
        fingerprint = str(clean.get("trustFingerprint") or "")
        payload = {key: value for key, value in clean.items() if key != "trustFingerprint"}
        if (
            not _DIGEST_RE.fullmatch(fingerprint)
            or hashlib.sha256(_canonical_json(payload)).hexdigest() != fingerprint
            or clean.get("receiptId") != installed.trust_receipt_id
            or fingerprint != installed.trust_fingerprint
            or clean.get("packageDigest") != package_digest
        ):
            raise SkillLifecycleValidationError(
                "Skill lifecycle trust receipt snapshot is invalid.",
                code="skill_lifecycle_source_unverified",
            )
        return clean

    def _enrich_trust_snapshot_unlocked(
        self,
        version: SkillVersionSnapshot,
        *,
        installed: InstalledSkill,
        receipt: Mapping[str, Any] | None,
    ) -> SkillVersionSnapshot:
        if version.trust_receipt_snapshot is not None or receipt is None:
            return version
        frozen = self._normalize_trust_snapshot(
            installed, receipt, version.package_digest
        )
        updated = replace(
            version,
            trust_directory_tree_sha=(
                installed.trust_directory_tree_sha
                or version.trust_directory_tree_sha
            ),
            trust_receipt_snapshot=frozen,
        )
        versions = dict(self._versions)
        versions[version.version_id] = updated
        self._save_unlocked(self._states, versions, self._quarantine)
        self._versions = versions
        return updated

    def _matching_version_unlocked(
        self,
        installed: InstalledSkill,
        package_digest: str,
        *,
        quality_required: bool,
        quality_decision_id: str | None,
    ) -> SkillVersionSnapshot | None:
        """Reuse legacy PR1 IDs only when their frozen evidence is identical."""

        for version in self._versions.values():
            if (
                version.skill_id == installed.skill_id
                and version.package_digest == package_digest
                and version.source_kind == installed.source_kind
                and version.source_id == installed.source_id
                and version.source_revision == installed.source_revision
                and version.source_ref == installed.source_ref
                and version.trust_fingerprint == installed.trust_fingerprint
                and version.quality_required == bool(quality_required)
                and version.quality_decision_id == quality_decision_id
            ):
                return version
        return None

    @staticmethod
    def _append_event(
        existing: SkillLifecycleState | None,
        event: SkillLifecycleEvent,
    ) -> tuple[SkillLifecycleEvent, ...]:
        events = [*(existing.events if existing else ()), event]
        return tuple(events[-SKILL_LIFECYCLE_MAX_EVENTS:])

    def _normalize_files(
        self, files: Mapping[str, bytes]
    ) -> tuple[dict[str, bytes], str, int]:
        normalized: dict[str, bytes] = {}
        casefolded: dict[str, str] = {}
        total_bytes = 0
        if not isinstance(files, Mapping):
            raise SkillLifecycleValidationError(
                "Skill lifecycle package must be a file mapping.",
                code="skill_lifecycle_package_invalid",
            )
        for raw_path, raw_content in files.items():
            path = _validate_relative_path(str(raw_path))
            folded = path.casefold()
            if folded in casefolded and casefolded[folded] != path:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle package contains a case-insensitive path collision.",
                    code="skill_lifecycle_package_invalid",
                )
            content = bytes(raw_content)
            if len(content) > MAX_TRUST_FILE_BYTES:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle package exceeds the per-file limit.",
                    code="skill_lifecycle_package_limit_exceeded",
                )
            total_bytes += len(content)
            if total_bytes > MAX_TRUST_TOTAL_BYTES:
                raise SkillLifecycleValidationError(
                    "Skill lifecycle package exceeds the total size limit.",
                    code="skill_lifecycle_package_limit_exceeded",
                )
            casefolded[folded] = path
            normalized[path] = content
        if len(normalized) > MAX_TRUST_FILES:
            raise SkillLifecycleValidationError(
                "Skill lifecycle package exceeds the file-count limit.",
                code="skill_lifecycle_package_limit_exceeded",
            )
        if "SKILL.md" not in normalized:
            raise SkillLifecycleValidationError(
                "Skill lifecycle package is missing SKILL.md.",
                code="skill_lifecycle_package_invalid",
            )
        digest = compute_package_digest(
            normalized["SKILL.md"],
            {path: content for path, content in normalized.items() if path != "SKILL.md"},
        )
        return dict(sorted(normalized.items())), digest, total_bytes

    def _persist_package_unlocked(
        self, digest: str, files: Mapping[str, bytes]
    ) -> bool:
        self._ensure_storage_paths_unlocked()
        target = self.packages_root / digest
        if target.exists():
            self._verify_package_unlocked(digest)
            return False
        projected_bytes = self._storage_bytes_unlocked() + sum(map(len, files.values()))
        if projected_bytes > self.max_storage_bytes:
            raise SkillLifecycleStorageError(
                "Skill lifecycle storage limit would be exceeded.",
                code="skill_lifecycle_storage_limit_exceeded",
            )
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.packages_root.mkdir(parents=True, exist_ok=True)
        temporary = self.tmp_root / f"package-{digest}-{uuid.uuid4().hex}"
        temporary.mkdir(parents=False, exist_ok=False)
        try:
            for relative_path, content in files.items():
                destination = temporary.joinpath(*relative_path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        self._verify_package_unlocked(digest)
        return True

    def _verify_package_unlocked(self, digest: str) -> dict[str, bytes]:
        target = self.packages_root / _validate_digest(digest)
        files = self.read_directory(target)
        _, actual, _ = self._normalize_files(files)
        if actual != digest:
            raise SkillLifecycleStorageError(
                "Stored Skill lifecycle package no longer matches its digest.",
                code="skill_lifecycle_package_mismatch",
            )
        return files

    @staticmethod
    def read_directory(package_dir: Path) -> dict[str, bytes]:
        root = package_dir.resolve()
        if not root.is_dir() or package_dir.is_symlink():
            raise SkillLifecycleValidationError(
                "Installed Skill package directory is unavailable or unsafe.",
                code="skill_lifecycle_package_invalid",
            )
        files: dict[str, bytes] = {}
        total_bytes = 0
        for path in root.rglob("*"):
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise SkillLifecycleValidationError(
                    "Installed Skill package could not be scanned completely.",
                    code="skill_lifecycle_scan_incomplete",
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise SkillLifecycleValidationError(
                    "Installed Skill package contains a symbolic link.",
                    code="skill_lifecycle_package_invalid",
                )
            if path.is_dir():
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
                raise SkillLifecycleValidationError(
                    "Installed Skill package contains a non-regular or linked file.",
                    code="skill_lifecycle_package_invalid",
                )
            relative = _validate_relative_path(path.relative_to(root).as_posix())
            if len(files) >= MAX_TRUST_FILES:
                raise SkillLifecycleValidationError(
                    "Installed Skill package exceeds the file-count limit.",
                    code="skill_lifecycle_package_limit_exceeded",
                )
            if metadata.st_size > MAX_TRUST_FILE_BYTES:
                raise SkillLifecycleValidationError(
                    "Installed Skill package exceeds the per-file limit.",
                    code="skill_lifecycle_package_limit_exceeded",
                )
            try:
                with path.open("rb") as handle:
                    content = handle.read(MAX_TRUST_FILE_BYTES + 1)
                if len(content) > MAX_TRUST_FILE_BYTES:
                    raise SkillLifecycleValidationError(
                        "Installed Skill package exceeds the per-file limit.",
                        code="skill_lifecycle_package_limit_exceeded",
                    )
                total_bytes += len(content)
                if total_bytes > MAX_TRUST_TOTAL_BYTES:
                    raise SkillLifecycleValidationError(
                        "Installed Skill package exceeds the total size limit.",
                        code="skill_lifecycle_package_limit_exceeded",
                    )
                files[relative] = content
            except OSError as exc:
                raise SkillLifecycleValidationError(
                    "Installed Skill package could not be scanned completely.",
                    code="skill_lifecycle_scan_incomplete",
                ) from exc
        return files

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            if self.index_path.stat().st_size > SKILL_LIFECYCLE_MAX_INDEX_BYTES:
                raise ValueError("lifecycle index is too large")
            raw = self.index_path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(payload, Mapping)
                or payload.get("version") != SKILL_LIFECYCLE_STORE_VERSION
                or not isinstance(payload.get("states"), list)
                or not isinstance(payload.get("versions"), list)
                or not isinstance(payload.get("quarantine", []), list)
            ):
                raise ValueError("invalid lifecycle store")
            quarantine = self._decode_quarantine(payload.get("quarantine", []))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            self._load_error = "skill_lifecycle_storage_unavailable"
            return
        versions: dict[str, SkillVersionSnapshot] = {}
        for index, raw_version in enumerate(payload["versions"]):
            try:
                item = self._decode_version(raw_version)
                if item.version_id in versions:
                    raise ValueError("duplicate version")
                versions[item.version_id] = item
            except (TypeError, ValueError):
                quarantine.append(self._quarantine_record("version", index, raw_version))
        states: dict[str, SkillLifecycleState] = {}
        for index, raw_state in enumerate(payload["states"]):
            try:
                item = self._decode_state(raw_state, versions)
                if item.skill_id in states:
                    raise ValueError("duplicate state")
                states[item.skill_id] = item
            except (TypeError, ValueError):
                quarantine.append(self._quarantine_record("state", index, raw_state))
        self._versions = versions
        self._states = states
        self._quarantine = quarantine

    @staticmethod
    def _decode_version(raw: Any) -> SkillVersionSnapshot:
        if not isinstance(raw, Mapping):
            raise ValueError("version must be an object")
        payload = dict(raw)
        payload["version_id"] = _validate_id(payload.get("version_id"), "version ID")
        payload["skill_id"] = _validate_id(payload.get("skill_id"), "skill ID")
        payload["package_digest"] = _validate_digest(payload.get("package_digest"))
        source_kind = payload.get("source_kind")
        if source_kind not in {"git", "local_import", "workspace_draft"}:
            raise ValueError("invalid source kind")
        ordinal = payload.get("ordinal")
        file_count = payload.get("file_count")
        total_bytes = payload.get("total_bytes")
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 1
        ):
            raise ValueError("invalid ordinal")
        if (
            not isinstance(file_count, int)
            or isinstance(file_count, bool)
            or not 1 <= file_count <= MAX_TRUST_FILES
            or not isinstance(total_bytes, int)
            or isinstance(total_bytes, bool)
            or not 1 <= total_bytes <= MAX_TRUST_TOTAL_BYTES
        ):
            raise ValueError("invalid package summary")
        source_revision = payload.get("source_revision")
        if source_revision is not None and (
            not isinstance(source_revision, int)
            or isinstance(source_revision, bool)
            or source_revision < 1
        ):
            raise ValueError("invalid source revision")
        if source_kind in {"local_import", "workspace_draft"} and (
            not payload.get("source_id") or source_revision is None
        ):
            raise ValueError("missing immutable source")
        source_ref = payload.get("source_ref")
        if source_kind == "git" and not _COMMIT_RE.fullmatch(
            str(source_ref or "").lower()
        ):
            raise ValueError("invalid Git source ref")
        if len(str(payload.get("repo_url") or "")) > 2_000 or len(
            str(payload.get("sub_path") or "")
        ) > 500:
            raise ValueError("invalid source location")
        source_id = payload.get("source_id")
        if source_id is not None and len(str(source_id)) > 200:
            raise ValueError("invalid source ID")
        for key in ("trust_fingerprint",):
            value = payload.get(key)
            if value is not None and not _DIGEST_RE.fullmatch(str(value)):
                raise ValueError(f"invalid {key}")
        tree_sha = payload.get("trust_directory_tree_sha")
        if tree_sha is not None and not _COMMIT_RE.fullmatch(str(tree_sha)):
            raise ValueError("invalid trust directory tree SHA")
        snapshot = payload.get("trust_receipt_snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, Mapping):
                raise ValueError("invalid trust receipt snapshot")
            snapshot = json.loads(json.dumps(dict(snapshot), ensure_ascii=False))
            if len(_canonical_json(snapshot)) > 2_000_000:
                raise ValueError("trust receipt snapshot is too large")
            if (
                snapshot.get("receiptId") != payload.get("trust_receipt_id")
                or snapshot.get("trustFingerprint") != payload.get("trust_fingerprint")
                or snapshot.get("packageDigest") != payload.get("package_digest")
                or hashlib.sha256(
                    _canonical_json(
                        {
                            key: value
                            for key, value in snapshot.items()
                            if key != "trustFingerprint"
                        }
                    )
                ).hexdigest()
                != snapshot.get("trustFingerprint")
            ):
                raise ValueError("trust receipt snapshot identity mismatch")
            payload["trust_receipt_snapshot"] = snapshot
        payload.setdefault("trust_directory_tree_sha", None)
        payload.setdefault("trust_receipt_snapshot", None)
        if not isinstance(payload.get("trust_router_eligible"), bool):
            raise ValueError("invalid Router eligibility")
        payload.setdefault("quality_required", False)
        if not isinstance(payload.get("quality_required"), bool):
            raise ValueError("invalid quality requirement")
        if payload.get("quality_evidence_status") not in {
            "not_applicable",
            "matched",
            "legacy_unavailable",
        }:
            raise ValueError("invalid quality evidence")
        created_at = payload.get("created_at")
        if (
            not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
            or created_at <= 0
        ):
            raise ValueError("invalid version timestamp")
        legacy_identity = {
            "skillId": payload["skill_id"],
            "sourceKind": source_kind,
            "sourceId": payload.get("source_id"),
            "sourceRevision": source_revision,
            "sourceRef": source_ref,
            "packageDigest": payload["package_digest"],
        }
        evidence_identity = dict(legacy_identity)
        if source_kind in {"git", "local_import"}:
            evidence_identity["trustFingerprint"] = payload.get("trust_fingerprint")
        if payload.get("quality_required"):
            evidence_identity["qualityDecisionId"] = payload.get(
                "quality_decision_id"
            )
        accepted_version_ids = {
            "skillver_"
            + hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]
            for identity in (legacy_identity, evidence_identity)
        }
        if payload["version_id"] not in accepted_version_ids:
            raise ValueError("version identity mismatch")
        return SkillVersionSnapshot(**payload)

    @staticmethod
    def _decode_state(
        raw: Any, versions: Mapping[str, SkillVersionSnapshot]
    ) -> SkillLifecycleState:
        if not isinstance(raw, Mapping):
            raise ValueError("state must be an object")
        payload = dict(raw)
        payload["skill_id"] = _validate_id(payload.get("skill_id"), "skill ID")
        if payload.get("status") not in {"active", "uninstalled", "migration_blocked"}:
            raise ValueError("invalid state status")
        if int(payload.get("revision") or 0) < 1:
            raise ValueError("invalid state revision")
        version_ids = tuple(payload.get("version_ids") or ())
        if any(item not in versions or versions[item].skill_id != payload["skill_id"] for item in version_ids):
            raise ValueError("state references invalid versions")
        current = payload.get("current_version_id")
        recovery = payload.get("recovery_version_id")
        protected = tuple(payload.get("protected_version_ids") or ())
        if current is not None and current not in version_ids:
            raise ValueError("invalid current version")
        if recovery is not None and recovery not in version_ids:
            raise ValueError("invalid recovery version")
        if any(item not in version_ids for item in protected) or len(set(protected)) != len(protected):
            raise ValueError("invalid protected versions")
        raw_events = payload.get("events") or []
        if not isinstance(raw_events, list) or len(raw_events) > SKILL_LIFECYCLE_MAX_EVENTS:
            raise ValueError("invalid events")
        events_list: list[SkillLifecycleEvent] = []
        for raw_event in raw_events:
            if not isinstance(raw_event, Mapping):
                raise ValueError("invalid event")
            event = SkillLifecycleEvent(**dict(raw_event))
            _validate_id(event.event_id, "event ID")
            if event.actor_kind not in {"system_migration", "local_console"}:
                raise ValueError("invalid event actor")
            if event.version_id is not None and event.version_id not in version_ids:
                raise ValueError("event references an invalid version")
            if not event.kind or len(event.kind) > 80 or event.created_at <= 0:
                raise ValueError("invalid event")
            events_list.append(event)
        events = tuple(events_list)
        if len(set(version_ids)) != len(version_ids):
            raise ValueError("duplicate state version")
        if payload.get("status") == "active" and current is None:
            raise ValueError("active state is missing current version")
        payload["version_ids"] = version_ids
        payload["protected_version_ids"] = protected
        payload["events"] = events
        return SkillLifecycleState(**payload)

    @staticmethod
    def _decode_quarantine(raw: Any) -> list[dict[str, Any]]:
        decoded: list[dict[str, Any]] = []
        for entry in raw:
            if (
                not isinstance(entry, Mapping)
                or entry.get("kind") not in {"state", "version"}
                or not isinstance(entry.get("index"), int)
                or not _DIGEST_RE.fullmatch(str(entry.get("sha256") or ""))
                or not isinstance(entry.get("sizeBytes"), int)
            ):
                raise ValueError("invalid quarantine")
            decoded.append(
                {
                    "kind": entry["kind"],
                    "index": entry["index"],
                    "sha256": entry["sha256"],
                    "sizeBytes": entry["sizeBytes"],
                }
            )
        return decoded

    @staticmethod
    def _quarantine_record(kind: str, index: int, raw: Any) -> dict[str, Any]:
        encoded = _canonical_json(raw)
        return {
            "kind": kind,
            "index": index,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "sizeBytes": len(encoded),
        }

    def _save_unlocked(
        self,
        states: Mapping[str, SkillLifecycleState],
        versions: Mapping[str, SkillVersionSnapshot],
        quarantine: list[dict[str, Any]],
    ) -> None:
        self._ensure_available(mutation=True)
        self._ensure_storage_paths_unlocked()
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SKILL_LIFECYCLE_STORE_VERSION,
            "states": [asdict(states[key]) for key in sorted(states)],
            "versions": [asdict(versions[key]) for key in sorted(versions)],
            "quarantine": copy.deepcopy(quarantine),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        temporary = self.root / f".{self.index_path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.index_path)
        except OSError as exc:
            raise SkillLifecycleStorageError(
                "Skill lifecycle metadata could not be persisted.",
                code="skill_lifecycle_storage_unavailable",
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_available(self, *, mutation: bool = False) -> None:
        if self._load_error:
            raise SkillLifecycleStorageError(
                "Skill lifecycle storage is unavailable.",
                code="skill_lifecycle_storage_unavailable",
            )
        if mutation and not self.enabled:
            raise SkillLifecycleDisabledError(
                "Skill lifecycle management is disabled.",
                code="skill_lifecycle_disabled",
            )

    def _recover_temp_unlocked(self) -> None:
        try:
            if (
                self.tmp_root.is_symlink()
                or self.packages_root.is_symlink()
                or self.transactions_root.is_symlink()
            ):
                raise OSError("linked lifecycle storage")
            if self.tmp_root.exists():
                for child in self.tmp_root.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
            if self.packages_root.exists():
                for child in self.packages_root.iterdir():
                    if (
                        child.is_symlink()
                        or not child.is_dir()
                        or not _DIGEST_RE.fullmatch(child.name)
                    ):
                        raise OSError("unsafe lifecycle package entry")
            if self.transactions_root.exists():
                for child in self.transactions_root.iterdir():
                    if child.is_symlink() or not child.is_file() or child.suffix != ".json":
                        raise OSError("unsafe lifecycle transaction entry")
        except OSError:
            self._load_error = "skill_lifecycle_storage_unavailable"

    def _ensure_storage_paths_unlocked(self) -> None:
        for path in (
            self.root,
            self.packages_root,
            self.tmp_root,
            self.transactions_root,
        ):
            if path.exists() and (path.is_symlink() or not path.is_dir()):
                raise SkillLifecycleStorageError(
                    "Skill lifecycle storage contains an unsafe path.",
                    code="skill_lifecycle_storage_unavailable",
                )

    def _storage_bytes_unlocked(self) -> int:
        if not self.packages_root.exists():
            return 0
        total = 0
        try:
            for path in self.packages_root.rglob("*"):
                if path.is_symlink():
                    raise OSError("linked lifecycle package")
                if path.is_file():
                    total += path.stat().st_size
        except OSError:
            return self.max_storage_bytes + 1
        return total


class SkillLifecycleMigrationService:
    """Read installed sources, prove their identity, and seed lifecycle history."""

    def __init__(
        self,
        *,
        store: SkillLifecycleStore,
        manager: SkillManager,
        draft_store: WorkspaceSkillDraftStore | None = None,
        local_import_store: SkillLocalImportStore | None = None,
    ) -> None:
        self.store = store
        self.manager = manager
        self.draft_store = draft_store
        self.local_import_store = local_import_store

    def status(self) -> dict[str, Any]:
        payload = self.store.status()
        payload["supportedSources"] = ["git", "local_import", "workspace_draft"]
        return payload

    def audit(self) -> dict[str, Any]:
        with self.store._lock:
            self.store._ensure_available()
        items = self._snapshot_installed()
        results = [self._audit_item(item) for item in items]
        return self._report(results, applied=False)

    def migrate(self, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise SkillLifecycleValidationError(
                "Skill lifecycle migration requires explicit confirmation.",
                code="skill_lifecycle_confirmation_required",
            )
        if not self.store.enabled:
            raise SkillLifecycleDisabledError(
                "Skill lifecycle management is disabled.",
                code="skill_lifecycle_disabled",
            )
        with self.store._lock:
            self.store._ensure_available(mutation=True)
        audited = [
            (item, self._audit_item(item))
            for item in self._snapshot_installed()
        ]
        self._preflight_migration(audited)
        results: list[dict[str, Any]] = []
        for item, audit in audited:
            if audit["outcome"] == "eligible":
                evidence = audit.pop("_evidence")
                state = self.store.record_migrated_current(
                    installed=item.installed,
                    files=item.files,
                    **evidence,
                )
                audit["outcome"] = "migrated"
                audit["lifecycleRevision"] = state.revision
                audit["versionId"] = state.current_version_id
            elif audit["outcome"] == "blocked":
                state = self.store.record_migration_blocked(
                    item.installed.skill_id,
                    reason_code=audit["code"],
                )
                audit["lifecycleRevision"] = state.revision
            audit.pop("_evidence", None)
            results.append(audit)
        return self._report(results, applied=True)

    def _preflight_migration(
        self,
        audited: list[tuple[InstalledPackageSnapshot, dict[str, Any]]],
    ) -> None:
        with self.store._lock:
            known_digests = {
                version.package_digest for version in self.store._versions.values()
            }
            projected_digests = set(known_digests)
            projected_bytes = self.store._storage_bytes_unlocked()
            for item, audit in audited:
                if audit["outcome"] != "eligible":
                    continue
                digest = str(audit["packageDigest"])
                if digest not in projected_digests:
                    projected_bytes += sum(len(content) for content in item.files.values())
                    projected_digests.add(digest)
                identity = {
                    "skillId": item.installed.skill_id,
                    "sourceKind": item.installed.source_kind,
                    "sourceId": item.installed.source_id,
                    "sourceRevision": item.installed.source_revision,
                    "sourceRef": item.installed.source_ref,
                    "packageDigest": digest,
                }
                version_id = (
                    "skillver_"
                    + hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]
                )
                state = self.store._states.get(item.installed.skill_id)
                if state is not None and version_id not in state.version_ids:
                    non_current = [
                        known
                        for known in state.version_ids
                        if known != state.current_version_id
                    ]
                    if len(non_current) >= self.store.max_versions:
                        raise SkillLifecycleStorageError(
                            "Skill lifecycle retention is full and protected versions cannot be pruned.",
                            code="skill_lifecycle_retention_full",
                        )
            if projected_bytes > self.store.max_storage_bytes:
                raise SkillLifecycleStorageError(
                    "Skill lifecycle migration would exceed the storage limit.",
                    code="skill_lifecycle_storage_limit_exceeded",
                )

    def _snapshot_installed(self) -> list[InstalledPackageSnapshot]:
        snapshots: list[InstalledPackageSnapshot] = []
        with self.manager._lock:  # Internal service shares the manager transaction lock.
            records = copy.deepcopy(self.manager._read_metadata())
            for skill_id in sorted(records):
                record = records[skill_id]
                try:
                    normalized_skill_id = self.manager._validate_skill_id(skill_id)
                    installed = self.manager._installed_skill_from_record(record)
                except (SkillManagerError, TypeError, ValueError) as exc:
                    raise SkillLifecycleStorageError(
                        "Installed Skill metadata contains an invalid record.",
                        code="skill_lifecycle_installed_metadata_unavailable",
                    ) from exc
                if installed.skill_id != normalized_skill_id:
                    raise SkillLifecycleStorageError(
                        "Installed Skill metadata identity is inconsistent.",
                        code="skill_lifecycle_installed_metadata_unavailable",
                    )
                if installed.source_kind not in {
                    "git",
                    "local_import",
                    "workspace_draft",
                }:
                    snapshots.append(InstalledPackageSnapshot(installed, {}))
                    continue
                try:
                    # Scan the manager-owned root before resolving an optional
                    # nested package.  This prevents a wrapper symlink from
                    # disappearing behind Path.resolve().
                    SkillLifecycleStore.read_directory(
                        self.manager.installed_dir / skill_id
                    )
                    package_dir = self.manager._resolve_package_directory(skill_id, record)
                    first = SkillLifecycleStore.read_directory(package_dir)
                    second = SkillLifecycleStore.read_directory(package_dir)
                except SkillLifecycleError as exc:
                    snapshots.append(
                        InstalledPackageSnapshot(installed, {}, exc.code)
                    )
                    continue
                except SkillManagerError:
                    snapshots.append(
                        InstalledPackageSnapshot(
                            installed, {}, "skill_lifecycle_scan_incomplete"
                        )
                    )
                    continue
                if first != second or self.manager._read_metadata().get(skill_id) != record:
                    snapshots.append(
                        InstalledPackageSnapshot(
                            installed, {}, "skill_lifecycle_scan_incomplete"
                        )
                    )
                    continue
                snapshots.append(InstalledPackageSnapshot(installed, first))
        return snapshots

    def _audit_item(self, item: InstalledPackageSnapshot) -> dict[str, Any]:
        installed = item.installed
        base: dict[str, Any] = {
            "skillId": installed.skill_id,
            "sourceKind": installed.source_kind,
        }
        if installed.source_kind not in {"git", "local_import", "workspace_draft"}:
            return {**base, "outcome": "ignored", "code": "skill_lifecycle_source_unsupported"}
        if not item.files:
            return {
                **base,
                "outcome": "blocked",
                "code": item.error_code or "skill_lifecycle_scan_incomplete",
            }
        try:
            _, actual_digest, _ = self.store._normalize_files(item.files)
        except SkillLifecycleError as exc:
            return {**base, "outcome": "blocked", "code": exc.code}
        if not installed.content_digest or actual_digest != installed.content_digest:
            return {**base, "outcome": "blocked", "code": "skill_lifecycle_package_mismatch"}
        try:
            evidence = self._verify_source(installed, actual_digest)
        except SkillLifecycleError as exc:
            return {**base, "outcome": "blocked", "code": exc.code}
        return {
            **base,
            "outcome": "eligible",
            "code": None,
            "packageDigest": actual_digest,
            "_evidence": evidence,
        }

    def _verify_source(
        self, installed: InstalledSkill, actual_digest: str
    ) -> dict[str, Any]:
        if installed.source_kind == "git":
            if (
                not installed.source_ref
                or not _COMMIT_RE.fullmatch(installed.source_ref.lower())
                or installed.trust_state != "receipt_matched"
                or not installed.trust_receipt_id
                or not installed.trust_fingerprint
                or not _DIGEST_RE.fullmatch(installed.trust_fingerprint)
                or installed.trust_package_digest != actual_digest
                or not installed.trust_directory_tree_sha
                or not _COMMIT_RE.fullmatch(installed.trust_directory_tree_sha)
            ):
                raise SkillLifecycleValidationError(
                    "Installed Git Skill does not have an exact trust receipt.",
                    code="skill_lifecycle_source_unverified",
                )
            try:
                receipt = self.manager.trust_service.receipt_by_id(
                    installed.trust_receipt_id
                )
            except SkillTrustError as exc:
                raise SkillLifecycleValidationError(
                    "Installed Git Skill trust receipt is no longer published.",
                    code="skill_lifecycle_source_unverified",
                ) from exc
            source = receipt.get("source")
            if (
                not isinstance(source, Mapping)
                or source.get("repoUrl") != installed.repo_url
                or str(source.get("subPath") or "") != installed.sub_path
                or str(source.get("verifiedCommit") or "").lower()
                != installed.source_ref.lower()
                or receipt.get("receiptId") != installed.trust_receipt_id
                or receipt.get("trustFingerprint") != installed.trust_fingerprint
                or receipt.get("packageDigest") != actual_digest
                or receipt.get("directoryTreeSha")
                != installed.trust_directory_tree_sha
            ):
                raise SkillLifecycleValidationError(
                    "Installed Git Skill does not match its published trust receipt.",
                    code="skill_lifecycle_source_unverified",
                )
            return {
                "quality_evidence_status": "not_applicable",
                "trust_receipt_snapshot": receipt,
            }
        if installed.source_kind == "local_import":
            if self.local_import_store is None or not installed.source_id:
                raise SkillLifecycleValidationError(
                    "Local import source is unavailable.",
                    code="skill_lifecycle_source_unverified",
                )
            try:
                source = self.local_import_store.require(installed.source_id)
            except SkillLocalImportError as exc:
                raise SkillLifecycleValidationError(
                    "Local import source is unavailable.",
                    code="skill_lifecycle_source_unverified",
                ) from exc
            if (
                source.content_revision != installed.source_revision
                or source.package_digest != actual_digest
                or source.installed_skill_id != installed.skill_id
                or source.receipt_id != installed.trust_receipt_id
                or source.trust_fingerprint != installed.trust_fingerprint
            ):
                raise SkillLifecycleValidationError(
                    "Installed local Skill does not match its immutable import.",
                    code="skill_lifecycle_source_unverified",
                )
            try:
                receipt = self.manager.trust_service.validate_local_receipt(
                    source.trust_receipt,
                    import_id=source.import_id,
                    import_revision=source.content_revision,
                    package_digest=actual_digest,
                )
            except SkillTrustError as exc:
                raise SkillLifecycleValidationError(
                    "Local import trust receipt is unavailable.",
                    code="skill_lifecycle_source_unverified",
                ) from exc
            return {
                "quality_evidence_status": "not_applicable",
                "trust_receipt_snapshot": receipt,
            }
        if self.draft_store is None or not installed.source_id or not installed.source_revision:
            raise SkillLifecycleValidationError(
                "Workspace draft source is unavailable.",
                code="skill_lifecycle_source_unverified",
            )
        try:
            snapshot = self.draft_store.require_revision_snapshot(
                installed.source_id,
                revision=installed.source_revision,
                content_digest=actual_digest,
            )
            draft = self.draft_store.require(installed.source_id)
        except SkillDraftError as exc:
            raise SkillLifecycleValidationError(
                "Workspace draft revision is unavailable.",
                code="skill_lifecycle_source_unverified",
            ) from exc
        package = snapshot.package
        source_files = {
            "SKILL.md": str(package.get("skill_markdown") or "").encode("utf-8"),
            **{
                str(path): str(content).encode("utf-8")
                for path, content in dict(package.get("files") or {}).items()
            },
        }
        _, source_digest, _ = self.store._normalize_files(source_files)
        if source_digest != actual_digest or draft.installed_skill_id != installed.skill_id:
            raise SkillLifecycleValidationError(
                "Installed Workspace Skill does not match its immutable revision.",
                code="skill_lifecycle_source_unverified",
            )
        decision = draft.quality_decision
        if not draft.quality_required:
            return {
                "quality_required": False,
                "quality_evidence_status": "not_applicable",
            }
        if (
            decision is not None
            and decision.content_revision == installed.source_revision
            and decision.content_digest == actual_digest
            and decision.status in {"accepted", "eval_waived"}
        ):
            return {
                "quality_required": True,
                "quality_evidence_status": "matched",
                "quality_status": decision.status,
                "quality_decision_id": decision.decision_id,
                "quality_run_id": decision.run_id,
            }
        return {
            "quality_required": True,
            "quality_evidence_status": "legacy_unavailable",
            "quality_status": None,
            "quality_decision_id": None,
            "quality_run_id": None,
        }

    @staticmethod
    def _report(results: list[dict[str, Any]], *, applied: bool) -> dict[str, Any]:
        public_results = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in results
        ]
        return {
            "version": SKILL_LIFECYCLE_PROTOCOL_VERSION,
            "applied": applied,
            "counts": {
                "total": len(results),
                "eligible": sum(item["outcome"] == "eligible" for item in results),
                "migrated": sum(item["outcome"] == "migrated" for item in results),
                "blocked": sum(item["outcome"] == "blocked" for item in results),
                "ignored": sum(item["outcome"] == "ignored" for item in results),
            },
            "items": public_results,
        }
