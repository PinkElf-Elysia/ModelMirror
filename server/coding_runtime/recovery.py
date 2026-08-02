from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from .draft_workspace import DraftLimits, DraftPolicyError, DraftWorkspace
from .cycles import CodingCycle, CodingCycleHistory
from .patch_policy import SNAPSHOT_FINGERPRINT_PATTERN, PatchPolicyError, validate_patch


RECOVERY_SCHEMA_VERSION = 3
DEFAULT_RECOVERY_RETENTION_SECONDS = 7 * 24 * 60 * 60
MIN_RECOVERY_RETENTION_SECONDS = 60
MAX_RECOVERY_RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_RECOVERY_PAYLOAD_BYTES = 16 * 1024 * 1024
SAFE_RECOVERY_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
FORBIDDEN_RECOVERY_KEYS = frozenset(
    {
        "answer",
        "api_key",
        "credential",
        "credentials",
        "env",
        "environment",
        "event",
        "events",
        "gateway_key",
        "log",
        "logs",
        "prompt",
        "raw",
        "raw_log",
        "response",
        "secret",
        "token",
        "tool",
        "tool_input",
        "tool_output",
        "tools",
    }
)
_PAYLOAD_KEYS_V1 = frozenset(
    {"patch", "changes", "verification", "apply", "commit", "operation"}
)
_PAYLOAD_KEYS_V2 = frozenset(
    {
        *_PAYLOAD_KEYS_V1,
        "base_patch",
        "base_changes",
        "active_patch",
        "active_changes",
        "cycles",
    }
)
_PAYLOAD_KEYS = frozenset({*_PAYLOAD_KEYS_V2, "publish"})
_CHANGE_KEYS = frozenset(
    {
        "revision",
        "files",
        "file_count",
        "additions",
        "deletions",
        "patch_bytes",
        "validation_status",
        "can_download",
        "checks",
    }
)
_FILE_KEYS = frozenset({"path", "status", "additions", "deletions"})
_CHECK_KEYS = frozenset({"id", "label", "status", "message"})


class RecoveryState(StrEnum):
    DRAFT = "draft"
    APPLIED = "applied"
    REVERTED = "reverted"
    COMMITTED = "committed"
    UNDONE = "undone"
    PUBLISHED = "published"
    CONFLICT = "conflict"


class CodingRecoveryError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RecoveryPayload:
    patch: str
    changes: dict[str, Any]
    verification: dict[str, Any] | None = None
    apply: dict[str, Any] | None = None
    commit: dict[str, Any] | None = None
    operation: dict[str, Any] | None = None
    publish: dict[str, Any] | None = None
    base_patch: str = ""
    base_changes: dict[str, Any] | None = None
    active_patch: str = ""
    active_changes: dict[str, Any] | None = None
    cycles: tuple[CodingCycle, ...] = ()

    def __post_init__(self) -> None:
        _validate_recovery_payload(self)

    @classmethod
    def from_dict(cls, value: Any) -> RecoveryPayload:
        keys = frozenset(value) if isinstance(value, dict) else frozenset()
        if not isinstance(value, dict) or keys not in {
            _PAYLOAD_KEYS_V1,
            _PAYLOAD_KEYS_V2,
            _PAYLOAD_KEYS,
        }:
            raise CodingRecoveryError(
                "Recovery payload shape is invalid.",
                code="recovery_data_corrupt",
            )
        try:
            legacy = keys == _PAYLOAD_KEYS_V1
            incremental = keys in {_PAYLOAD_KEYS_V2, _PAYLOAD_KEYS}
            return cls(
                patch=value["patch"],
                changes=value["changes"],
                verification=value["verification"],
                apply=value["apply"],
                commit=value["commit"],
                operation=value["operation"],
                publish=value["publish"] if keys == _PAYLOAD_KEYS else None,
                base_patch="" if legacy else value["base_patch"],
                base_changes=None if legacy else value["base_changes"],
                active_patch=value["patch"] if legacy else value["active_patch"],
                active_changes=value["changes"] if legacy else value["active_changes"],
                cycles=(
                    ()
                    if not incremental
                    else tuple(_cycle_from_dict(item) for item in value["cycles"])
                ),
            )
        except CodingRecoveryError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CodingRecoveryError(
                "Recovery payload is invalid.",
                code="recovery_data_corrupt",
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch": self.patch,
            "changes": self.changes,
            "verification": self.verification,
            "apply": self.apply,
            "commit": self.commit,
            "operation": self.operation,
            "publish": self.publish,
            "base_patch": self.base_patch,
            "base_changes": self.base_changes,
            "active_patch": self.active_patch,
            "active_changes": self.active_changes,
            "cycles": [_cycle_to_dict(cycle) for cycle in self.cycles],
        }


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    recovery_id: str
    state: RecoveryState
    revision: int
    snapshot_fingerprint: str
    payload: RecoveryPayload
    created_at: float
    updated_at: float
    expires_at: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.recovery_id, str)
            or SAFE_RECOVERY_ID.fullmatch(self.recovery_id) is None
        ):
            raise ValueError("Recovery id is invalid")
        if not isinstance(self.state, RecoveryState):
            raise ValueError("Recovery state is invalid")
        if not isinstance(self.payload, RecoveryPayload):
            raise ValueError("Recovery payload is invalid")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Recovery revision is invalid")
        if (
            not isinstance(self.snapshot_fingerprint, str)
            or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(self.snapshot_fingerprint) is None
        ):
            raise ValueError("Recovery snapshot fingerprint is invalid")
        if self.payload.changes["revision"] != self.revision:
            raise ValueError("Recovery revision does not match changes")
        timestamps = (self.created_at, self.updated_at, self.expires_at)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in timestamps
        ):
            raise ValueError("Recovery timestamp is invalid")
        if self.updated_at < self.created_at or self.expires_at <= self.updated_at:
            raise ValueError("Recovery timestamps are inconsistent")

    @property
    def file_count(self) -> int:
        return int(self.payload.changes["file_count"])

    def to_public(self, *, can_resume: bool = True, reason: str | None = None) -> dict[str, Any]:
        return {
            "pending": True,
            "state": self.state.value,
            "revision": self.revision,
            "file_count": self.file_count,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "can_resume": can_resume,
            "can_download": True,
            "reason": reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "state": self.state.value,
            "revision": self.revision,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "payload": self.payload.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> RecoveryRecord:
        if not isinstance(value, dict) or set(value) != {
            "recovery_id",
            "state",
            "revision",
            "snapshot_fingerprint",
            "payload",
            "created_at",
            "updated_at",
            "expires_at",
        }:
            raise CodingRecoveryError(
                "Recovery record shape is invalid.",
                code="recovery_data_corrupt",
            )
        try:
            return cls(
                recovery_id=value["recovery_id"],
                state=RecoveryState(value["state"]),
                revision=value["revision"],
                snapshot_fingerprint=value["snapshot_fingerprint"],
                payload=RecoveryPayload.from_dict(value["payload"]),
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                expires_at=value["expires_at"],
            )
        except CodingRecoveryError:
            raise
        except (TypeError, ValueError) as exc:
            raise CodingRecoveryError(
                "Recovery record is invalid.",
                code="recovery_data_corrupt",
            ) from exc


class CodingRecoveryStore:
    """Single-slot encrypted recovery storage for the local Coding feature."""

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        retention_seconds: int = DEFAULT_RECOVERY_RETENTION_SECONDS,
        clock: Callable[[], float] = time.time,
        master_key: bytes | str | None = None,
    ) -> None:
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, int)
            or not MIN_RECOVERY_RETENTION_SECONDS
            <= retention_seconds
            <= MAX_RECOVERY_RETENTION_SECONDS
        ):
            raise ValueError("Recovery retention is outside the allowed range")
        self.storage_dir = Path(storage_dir)
        self.database_path = self.storage_dir / "recovery.sqlite3"
        self.master_key_path = self.storage_dir / "recovery-master.key"
        self.retention_seconds = retention_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._resolve_master_key(master_key))
        self._initialize()

    def create_record(
        self,
        *,
        recovery_id: str,
        state: RecoveryState,
        revision: int,
        snapshot_fingerprint: str,
        payload: RecoveryPayload,
        created_at: float | None = None,
    ) -> RecoveryRecord:
        now = self._now()
        return RecoveryRecord(
            recovery_id=recovery_id,
            state=state,
            revision=revision,
            snapshot_fingerprint=snapshot_fingerprint,
            payload=payload,
            created_at=now if created_at is None else created_at,
            updated_at=now,
            expires_at=now + self.retention_seconds,
        )

    def save(self, record: RecoveryRecord) -> RecoveryRecord:
        raw_payload = _canonical_json(record.to_dict())
        encrypted = self._fernet.encrypt(raw_payload)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO coding_recovery (
                    slot, schema_version, recovery_id, state, revision,
                    snapshot_fingerprint, file_count, updated_at, expires_at,
                    payload_ciphertext
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    recovery_id = excluded.recovery_id,
                    state = excluded.state,
                    revision = excluded.revision,
                    snapshot_fingerprint = excluded.snapshot_fingerprint,
                    file_count = excluded.file_count,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at,
                    payload_ciphertext = excluded.payload_ciphertext
                """,
                (
                    RECOVERY_SCHEMA_VERSION,
                    record.recovery_id,
                    record.state.value,
                    record.revision,
                    record.snapshot_fingerprint,
                    record.file_count,
                    record.updated_at,
                    record.expires_at,
                    encrypted.decode("ascii"),
                ),
            )
        return record

    def load(self) -> RecoveryRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM coding_recovery WHERE slot = 1"
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= self._now():
                connection.execute("DELETE FROM coding_recovery WHERE slot = 1")
                return None
            if row["schema_version"] not in {1, 2, RECOVERY_SCHEMA_VERSION}:
                raise CodingRecoveryError(
                    "Recovery schema is unsupported.",
                    code="recovery_schema_unsupported",
                )
            try:
                decrypted = self._fernet.decrypt(
                    str(row["payload_ciphertext"]).encode("ascii")
                )
                value = json.loads(decrypted.decode("utf-8"))
                record = RecoveryRecord.from_dict(value)
            except (InvalidToken, UnicodeError, json.JSONDecodeError) as exc:
                raise CodingRecoveryError(
                    "Recovery data could not be authenticated.",
                    code="recovery_data_corrupt",
                ) from exc
            if (
                record.recovery_id != row["recovery_id"]
                or record.state.value != row["state"]
                or record.revision != row["revision"]
                or record.snapshot_fingerprint != row["snapshot_fingerprint"]
                or record.file_count != row["file_count"]
                or record.updated_at != row["updated_at"]
                or record.expires_at != row["expires_at"]
            ):
                raise CodingRecoveryError(
                    "Recovery metadata does not match its encrypted payload.",
                    code="recovery_data_corrupt",
                )
            return record

    def discard(self, *, recovery_id: str | None = None) -> bool:
        with self._lock, self._connect() as connection:
            if recovery_id is None:
                cursor = connection.execute(
                    "DELETE FROM coding_recovery WHERE slot = 1"
                )
            else:
                if SAFE_RECOVERY_ID.fullmatch(recovery_id) is None:
                    return False
                cursor = connection.execute(
                    "DELETE FROM coding_recovery WHERE slot = 1 AND recovery_id = ?",
                    (recovery_id,),
                )
            return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            existing_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'coding_recovery'"
            ).fetchone()
            if current_version == 0 and existing_table is None:
                connection.execute(
                    """
                    CREATE TABLE coding_recovery (
                        slot INTEGER PRIMARY KEY CHECK (slot = 1),
                        schema_version INTEGER NOT NULL,
                        recovery_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        snapshot_fingerprint TEXT NOT NULL,
                        file_count INTEGER NOT NULL,
                        updated_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        payload_ciphertext TEXT NOT NULL
                    )
                    """
                )
                connection.execute(f"PRAGMA user_version = {RECOVERY_SCHEMA_VERSION}")
            elif current_version in {1, 2}:
                connection.execute(f"PRAGMA user_version = {RECOVERY_SCHEMA_VERSION}")
            elif current_version != RECOVERY_SCHEMA_VERSION:
                raise CodingRecoveryError(
                    "Recovery schema is unsupported.",
                    code="recovery_schema_unsupported",
                )

    def _resolve_master_key(self, master_key: bytes | str | None) -> bytes:
        if master_key is not None:
            candidate = master_key.encode("ascii") if isinstance(master_key, str) else master_key
            try:
                Fernet(candidate)
            except (TypeError, ValueError) as exc:
                raise CodingRecoveryError(
                    "Recovery key is invalid.",
                    code="recovery_key_invalid",
                ) from exc
            return candidate
        if self.master_key_path.exists():
            try:
                candidate = self.master_key_path.read_bytes().strip()
                Fernet(candidate)
            except (OSError, TypeError, ValueError) as exc:
                raise CodingRecoveryError(
                    "Recovery key is invalid.",
                    code="recovery_key_invalid",
                ) from exc
            return candidate
        if self.database_path.exists():
            raise CodingRecoveryError(
                "Recovery key is missing for existing data.",
                code="recovery_key_missing",
            )
        candidate = Fernet.generate_key()
        try:
            descriptor = os.open(
                self.master_key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(candidate)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            try:
                candidate = self.master_key_path.read_bytes().strip()
                Fernet(candidate)
            except (OSError, TypeError, ValueError) as exc:
                raise CodingRecoveryError(
                    "Recovery key could not be loaded.",
                    code="recovery_key_invalid",
                ) from exc
        except OSError as exc:
            raise CodingRecoveryError(
                "Recovery key could not be created.",
                code="recovery_storage_unavailable",
            ) from exc
        return candidate

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value) or value < 0:
            raise CodingRecoveryError(
                "Recovery clock is invalid.",
                code="recovery_storage_unavailable",
            )
        return value


def _validate_recovery_payload(payload: RecoveryPayload) -> None:
    patch = payload.patch
    changes = payload.changes
    if not isinstance(patch, str) or not isinstance(changes, dict):
        raise CodingRecoveryError(
            "Recovery draft is invalid.",
            code="invalid_recovery_payload",
        )
    if set(changes) != _CHANGE_KEYS:
        raise CodingRecoveryError(
            "Recovery change summary is invalid.",
            code="invalid_recovery_payload",
        )
    revision = changes["revision"]
    files = changes["files"]
    checks = changes["checks"]
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(files, list)
        or not 1 <= len(files) <= DraftLimits().max_changed_files
        or changes["file_count"] != len(files)
        or not isinstance(checks, list)
        or changes["validation_status"] not in {"passed", "failed"}
        or not isinstance(changes["can_download"], bool)
        or changes["can_download"] != (changes["validation_status"] == "passed")
    ):
        raise CodingRecoveryError(
            "Recovery change summary is inconsistent.",
            code="invalid_recovery_payload",
        )
    paths: list[str] = []
    additions = 0
    deletions = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != _FILE_KEYS:
            raise CodingRecoveryError(
                "Recovery file summary is invalid.",
                code="invalid_recovery_payload",
            )
        try:
            path = DraftWorkspace.normalize_relative_path(item["path"])
        except (DraftPolicyError, TypeError) as exc:
            raise CodingRecoveryError(
                "Recovery file path is invalid.",
                code="invalid_recovery_payload",
            ) from exc
        if (
            item["status"] not in {"added", "modified"}
            or isinstance(item["additions"], bool)
            or not isinstance(item["additions"], int)
            or item["additions"] < 0
            or isinstance(item["deletions"], bool)
            or not isinstance(item["deletions"], int)
            or item["deletions"] < 0
        ):
            raise CodingRecoveryError(
                "Recovery file summary is inconsistent.",
                code="invalid_recovery_payload",
            )
        paths.append(path)
        additions += item["additions"]
        deletions += item["deletions"]
    if paths != sorted(set(paths)):
        raise CodingRecoveryError(
            "Recovery paths are not canonical.",
            code="invalid_recovery_payload",
        )
    numeric_totals = (
        changes["file_count"],
        changes["additions"],
        changes["deletions"],
        changes["patch_bytes"],
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in numeric_totals
    ):
        raise CodingRecoveryError(
            "Recovery totals are invalid.",
            code="invalid_recovery_payload",
        )
    try:
        encoded_patch = patch.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CodingRecoveryError(
            "Recovery Patch is not UTF-8.",
            code="invalid_recovery_payload",
        ) from exc
    if (
        changes["additions"] != additions
        or changes["deletions"] != deletions
        or changes["patch_bytes"] != len(encoded_patch)
    ):
        raise CodingRecoveryError(
            "Recovery totals are inconsistent.",
            code="invalid_recovery_payload",
        )
    for check in checks:
        if (
            not isinstance(check, dict)
            or set(check) != _CHECK_KEYS
            or not all(isinstance(check[key], str) for key in _CHECK_KEYS)
            or check["status"] not in {"passed", "failed"}
            or any(len(check[key]) > 1_000 for key in _CHECK_KEYS)
        ):
            raise CodingRecoveryError(
                "Recovery check summary is invalid.",
                code="invalid_recovery_payload",
            )
    try:
        validate_patch(patch, expected_paths=paths)
    except (PatchPolicyError, UnicodeError) as exc:
        raise CodingRecoveryError(
            "Recovery Patch is invalid.",
            code="invalid_recovery_payload",
        ) from exc
    for value in (
        payload.verification,
        payload.apply,
        payload.commit,
        payload.operation,
        payload.publish,
    ):
        if value is not None and not isinstance(value, dict):
            raise CodingRecoveryError(
                "Recovery operation data is invalid.",
                code="invalid_recovery_payload",
            )
        _validate_json_value(value)
    try:
        history = CodingCycleHistory(payload.cycles)
    except (TypeError, ValueError) as exc:
        raise CodingRecoveryError(
            "Recovery cycle history is invalid.",
            code="invalid_recovery_payload",
        ) from exc
    if history.cycles != payload.cycles:
        raise CodingRecoveryError(
            "Recovery cycle history is invalid.",
            code="invalid_recovery_payload",
        )
    for cycle in payload.cycles:
        cycle_files = cycle.changes.get("files")
        if not isinstance(cycle_files, list):
            raise CodingRecoveryError(
                "Recovery cycle files are invalid.",
                code="invalid_recovery_payload",
            )
        cycle_paths = [item.get("path") for item in cycle_files if isinstance(item, dict)]
        try:
            validate_patch(cycle.patch, expected_paths=cycle_paths)
        except (PatchPolicyError, UnicodeError, TypeError) as exc:
            raise CodingRecoveryError(
                "Recovery cycle Patch is invalid.",
                code="invalid_recovery_payload",
            ) from exc
        for value in (cycle.changes, cycle.verification, cycle.apply, cycle.commit):
            _validate_json_value(value)
    for patch_value, summary in (
        (payload.base_patch, payload.base_changes),
        (payload.active_patch, payload.active_changes),
    ):
        if not isinstance(patch_value, str) or (
            summary is not None and not isinstance(summary, dict)
        ):
            raise CodingRecoveryError(
                "Recovery incremental draft is invalid.",
                code="invalid_recovery_payload",
            )
        if len(patch_value.encode("utf-8", errors="strict")) > DraftLimits().max_patch_bytes:
            raise CodingRecoveryError(
                "Recovery incremental Patch is too large.",
                code="invalid_recovery_payload",
            )
        if bool(patch_value) != (summary is not None):
            raise CodingRecoveryError(
                "Recovery incremental draft is inconsistent.",
                code="invalid_recovery_payload",
            )
        if summary is not None:
            _validate_json_value(summary)
    if len(_canonical_json(payload.to_dict())) > MAX_RECOVERY_PAYLOAD_BYTES:
        raise CodingRecoveryError(
            "Recovery payload exceeds the allowed size.",
            code="invalid_recovery_payload",
        )


def _cycle_to_dict(cycle: CodingCycle) -> dict[str, Any]:
    return {
        "number": cycle.number,
        "revision": cycle.revision,
        "state": cycle.state.value,
        "patch": cycle.patch,
        "changes": cycle.changes,
        "verification": cycle.verification,
        "apply": cycle.apply,
        "commit": cycle.commit,
        "created_at": cycle.created_at,
        "updated_at": cycle.updated_at,
    }


def _cycle_from_dict(value: Any) -> CodingCycle:
    keys = {
        "number", "revision", "state", "patch", "changes",
        "verification", "apply", "commit", "created_at", "updated_at",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Cycle payload shape is invalid")
    from .cycles import CycleState

    return CodingCycle(
        number=value["number"],
        revision=value["revision"],
        state=CycleState(value["state"]),
        patch=value["patch"],
        changes=value["changes"],
        verification=value["verification"],
        apply=value["apply"],
        commit=value["commit"],
        created_at=value["created_at"],
        updated_at=value["updated_at"],
    )


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise CodingRecoveryError(
            "Recovery data is too deeply nested.",
            code="invalid_recovery_payload",
        )
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if isinstance(value, bool):
            raise AssertionError("Boolean handled above")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CodingRecoveryError(
                "Recovery data contains a non-finite number.",
                code="invalid_recovery_payload",
            )
        return
    if isinstance(value, str):
        if "\x00" in value or len(value) > 65_536:
            raise CodingRecoveryError(
                "Recovery text is outside the allowed size.",
                code="invalid_recovery_payload",
            )
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise CodingRecoveryError(
                "Recovery list is too large.",
                code="invalid_recovery_payload",
            )
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 128:
            raise CodingRecoveryError(
                "Recovery object is too large.",
                code="invalid_recovery_payload",
            )
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 128
                or any(ord(character) < 32 for character in key)
                or key.casefold() in FORBIDDEN_RECOVERY_KEYS
            ):
                raise CodingRecoveryError(
                    "Recovery data contains a forbidden field.",
                    code="invalid_recovery_payload",
                )
            _validate_json_value(item, depth=depth + 1)
        return
    raise CodingRecoveryError(
        "Recovery data contains an unsupported value.",
        code="invalid_recovery_payload",
    )


def _canonical_json(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CodingRecoveryError(
            "Recovery data could not be encoded.",
            code="invalid_recovery_payload",
        ) from exc
    if len(encoded) > MAX_RECOVERY_PAYLOAD_BYTES:
        raise CodingRecoveryError(
            "Recovery payload exceeds the allowed size.",
            code="invalid_recovery_payload",
        )
    return encoded
