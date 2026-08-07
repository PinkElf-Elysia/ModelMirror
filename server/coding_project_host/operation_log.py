from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Protocol

from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import (
    COMMIT_ID_PATTERN,
    GIT_OBJECT_ID_PATTERN,
    normalize_commit_message,
)
from server.coding_runtime.draft_workspace import DraftPolicyError, DraftWorkspace
from server.coding_runtime.project_host import OBJECT_ID_PATTERN, PROJECT_ID_PATTERN


OPERATION_LOG_MAGIC = b"MMCPHOP1\n"
OPERATION_LOG_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_OPERATION_LOG_BYTES = 8 * 1024 * 1024
MAX_OPERATION_PATCH_BYTES = 1024 * 1024
MAX_OPERATION_RECORDS = 64
OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
PATCH_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

HostOperationAction = Literal["apply", "revert", "commit", "undo"]
HostOperationState = Literal[
    "prepared",
    "applying",
    "applied",
    "reverting",
    "reverted",
    "committing",
    "committed",
    "undoing",
    "undone",
    "conflict",
]

_ACTIONS = frozenset({"apply", "revert", "commit", "undo"})
_STATES = frozenset(
    {
        "prepared",
        "applying",
        "applied",
        "reverting",
        "reverted",
        "committing",
        "committed",
        "undoing",
        "undone",
        "conflict",
    }
)
_TERMINAL_STATES = frozenset({"reverted", "undone", "conflict"})
_ACTION_STATES = {
    "apply": frozenset({"prepared", "applying", "applied", "conflict"}),
    "revert": frozenset({"prepared", "reverting", "reverted", "conflict"}),
    "commit": frozenset({"prepared", "committing", "committed", "conflict"}),
    "undo": frozenset({"prepared", "undoing", "undone", "conflict"}),
}
_ALLOWED_TRANSITIONS = frozenset(
    {
        ("prepared", "applying"),
        ("prepared", "applied"),
        ("prepared", "reverting"),
        ("prepared", "reverted"),
        ("prepared", "committing"),
        ("prepared", "committed"),
        ("prepared", "undoing"),
        ("prepared", "undone"),
        ("prepared", "conflict"),
        ("applying", "applied"),
        ("applying", "conflict"),
        ("reverting", "reverted"),
        ("reverting", "conflict"),
        ("committing", "committed"),
        ("committing", "conflict"),
        ("undoing", "undone"),
        ("undoing", "conflict"),
    }
)


class DataProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class HostOperationLogError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class HostOperationRecord:
    operation_id: str
    action: HostOperationAction
    project_id: str
    revision: int
    branch: str
    expected_head: str
    patch_sha256: str
    state: HostOperationState
    created_at: float
    updated_at: float
    patch: str = ""
    apply_receipt: dict[str, Any] | None = None
    commit_receipt: dict[str, Any] | None = None
    created_directories: tuple[str, ...] = ()
    file_identities: tuple[str, ...] = ()


class HostOperationJournal:
    """DPAPI-backed, path-free operation journal for host side effects."""

    def __init__(
        self,
        path: Path,
        protector: DataProtector,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.protector = protector
        self._clock = clock
        self._lock = threading.RLock()
        self._records: dict[str, HostOperationRecord] = {}
        with self._file_lock():
            loaded = self._load()
            pruned = self._prune_records(loaded)
            if pruned != loaded:
                self._persist_records(pruned)
            self._records = pruned

    def create(
        self,
        *,
        operation_id: str,
        action: HostOperationAction,
        project_id: str,
        revision: int,
        branch: str,
        expected_head: str,
        patch_sha256: str,
        patch: str = "",
        apply_receipt: dict[str, Any] | None = None,
        commit_receipt: dict[str, Any] | None = None,
        created_directories: tuple[str, ...] = (),
        file_identities: tuple[str, ...] = (),
    ) -> HostOperationRecord:
        now = self._clock()
        record = HostOperationRecord(
            operation_id=operation_id,
            action=action,
            project_id=project_id,
            revision=revision,
            branch=branch,
            expected_head=expected_head,
            patch_sha256=patch_sha256,
            state="prepared",
            created_at=now,
            updated_at=now,
            patch=patch,
            apply_receipt=copy.deepcopy(apply_receipt),
            commit_receipt=copy.deepcopy(commit_receipt),
            created_directories=tuple(created_directories),
            file_identities=tuple(file_identities),
        )
        _validate_record(record)
        with self._lock:
            with self._file_lock():
                current = self._prune_records(self._load())
                existing = current.get(operation_id)
                if existing is not None:
                    if not _same_intent(existing, record):
                        raise HostOperationLogError("operation_conflict")
                    self._records = current
                    return _clone_record(existing)
                if len(current) >= MAX_OPERATION_RECORDS:
                    raise HostOperationLogError("operation_log_full")
                candidate = {**current, operation_id: record}
                self._persist_records(candidate)
                self._records = candidate
                return _clone_record(record)

    def get(self, operation_id: str) -> HostOperationRecord | None:
        if OPERATION_ID_PATTERN.fullmatch(operation_id) is None:
            raise HostOperationLogError("operation_id_invalid")
        with self._lock:
            with self._file_lock():
                current = self._prune_records(self._load())
                if current != self._records:
                    self._persist_records(current)
                self._records = current
                record = current.get(operation_id)
                return _clone_record(record) if record is not None else None

    def transition(
        self,
        operation_id: str,
        state: HostOperationState,
        *,
        apply_receipt: dict[str, Any] | None = None,
        commit_receipt: dict[str, Any] | None = None,
        created_directories: tuple[str, ...] | None = None,
        file_identities: tuple[str, ...] | None = None,
    ) -> HostOperationRecord:
        if state not in _STATES:
            raise HostOperationLogError("operation_state_invalid")
        with self._lock:
            with self._file_lock():
                records = self._prune_records(self._load())
                current = records.get(operation_id)
                if current is None:
                    raise HostOperationLogError("operation_not_found")
                if state not in _ACTION_STATES[current.action]:
                    raise HostOperationLogError("operation_state_invalid")
                if state == current.state:
                    if (
                        apply_receipt is not None
                        and current.apply_receipt != apply_receipt
                    ) or (
                        commit_receipt is not None
                        and current.commit_receipt != commit_receipt
                    ) or (
                        created_directories is not None
                        and current.created_directories
                        and current.created_directories != created_directories
                        and not (
                            current.state == "applying"
                            and not created_directories
                        )
                    ) or (
                        file_identities is not None
                        and current.file_identities
                        and current.file_identities != file_identities
                    ):
                        raise HostOperationLogError("operation_conflict")
                    if (
                        created_directories is not None
                        and (
                            not current.created_directories
                            or current.state == "applying"
                            and not created_directories
                        )
                    ) or (
                        file_identities is not None and not current.file_identities
                    ):
                        updated = replace(
                            current,
                            updated_at=self._clock(),
                            created_directories=(
                                tuple(created_directories)
                                if created_directories is not None
                                else current.created_directories
                            ),
                            file_identities=(
                                tuple(file_identities)
                                if file_identities is not None
                                else current.file_identities
                            ),
                        )
                        _validate_record(updated)
                        candidate = {**records, operation_id: updated}
                        self._persist_records(candidate)
                        self._records = candidate
                        return _clone_record(updated)
                    self._records = records
                    return _clone_record(current)
                if (current.state, state) not in _ALLOWED_TRANSITIONS:
                    raise HostOperationLogError("operation_state_invalid")
                updated = replace(
                    current,
                    state=state,
                    updated_at=self._clock(),
                    apply_receipt=(
                        copy.deepcopy(apply_receipt)
                        if apply_receipt is not None
                        else current.apply_receipt
                    ),
                    commit_receipt=(
                        copy.deepcopy(commit_receipt)
                        if commit_receipt is not None
                        else current.commit_receipt
                    ),
                    created_directories=(
                        tuple(created_directories)
                        if created_directories is not None
                        else current.created_directories
                    ),
                    file_identities=(
                        tuple(file_identities)
                        if file_identities is not None
                        else current.file_identities
                    ),
                )
                _validate_record(updated)
                candidate = {**records, operation_id: updated}
                self._persist_records(candidate)
                self._records = candidate
                return _clone_record(updated)

    def remove(self, operation_id: str) -> None:
        with self._lock:
            with self._file_lock():
                records = self._prune_records(self._load())
                if operation_id not in records:
                    self._records = records
                    return
                candidate = dict(records)
                del candidate[operation_id]
                self._persist_records(candidate)
                self._records = candidate

    def _load(self) -> dict[str, HostOperationRecord]:
        if not self.path.exists():
            return {}
        try:
            encoded = self.path.read_bytes()
            if len(encoded) > MAX_OPERATION_LOG_BYTES or not encoded.startswith(
                OPERATION_LOG_MAGIC
            ):
                raise ValueError("invalid operation log")
            protected = base64.b64decode(
                encoded[len(OPERATION_LOG_MAGIC) :], validate=True
            )
            payload = json.loads(
                self.protector.unprotect(protected).decode("utf-8", errors="strict")
            )
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("invalid operation log")
            raw_records = payload.get("records")
            if not isinstance(raw_records, list) or len(raw_records) > MAX_OPERATION_RECORDS:
                raise ValueError("invalid operation log")
            records: dict[str, HostOperationRecord] = {}
            for value in raw_records:
                if not isinstance(value, dict):
                    raise ValueError("invalid operation log")
                if "created_directories" in value:
                    value = {
                        **value,
                        "created_directories": tuple(value["created_directories"]),
                    }
                if "file_identities" in value:
                    value = {
                        **value,
                        "file_identities": tuple(value["file_identities"]),
                    }
                record = HostOperationRecord(**value)
                _validate_record(record)
                if record.operation_id in records:
                    raise ValueError("duplicate operation")
                records[record.operation_id] = record
            return records
        except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise HostOperationLogError("operation_log_corrupt") from exc

    def _prune_records(
        self,
        records: dict[str, HostOperationRecord],
    ) -> dict[str, HostOperationRecord]:
        cutoff = self._clock() - OPERATION_LOG_RETENTION_SECONDS
        return {
            operation_id: record
            for operation_id, record in records.items()
            if record.state not in _TERMINAL_STATES or record.updated_at >= cutoff
        }

    def _persist_records(self, records: dict[str, HostOperationRecord]) -> None:
        payload = {
            "version": 1,
            "records": [
                asdict(record)
                for record in sorted(
                    records.values(), key=lambda item: item.created_at
                )
            ],
        }
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        protected = self.protector.protect(plaintext)
        encoded = OPERATION_LOG_MAGIC + base64.b64encode(protected)
        if len(encoded) > MAX_OPERATION_LOG_BYTES:
            raise HostOperationLogError("operation_log_too_large")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_bytes(encoded)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        deadline = time.monotonic() + 5.0
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise HostOperationLogError("operation_log_locked") from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _validate_record(record: HostOperationRecord) -> None:
    if (
        OPERATION_ID_PATTERN.fullmatch(record.operation_id) is None
        or record.action not in _ACTIONS
        or PROJECT_ID_PATTERN.fullmatch(record.project_id) is None
        or type(record.revision) is not int
        or record.revision < 0
        or not _valid_branch(record.branch)
        or OBJECT_ID_PATTERN.fullmatch(record.expected_head) is None
        or PATCH_SHA256_PATTERN.fullmatch(record.patch_sha256) is None
        or not hmac.compare_digest(
            record.patch_sha256,
            hashlib.sha256(record.patch.encode("utf-8")).hexdigest(),
        )
        or record.state not in _STATES
        or type(record.created_at) not in {int, float}
        or type(record.updated_at) not in {int, float}
        or not math.isfinite(record.created_at)
        or not math.isfinite(record.updated_at)
        or record.updated_at < record.created_at
        or not isinstance(record.patch, str)
        or len(record.patch.encode("utf-8")) > MAX_OPERATION_PATCH_BYTES
        or record.state not in _ACTION_STATES[record.action]
        or not _valid_apply_receipt(record.apply_receipt, record)
        or not _valid_commit_receipt(record.commit_receipt, record)
        or not _valid_created_directories(record.created_directories)
        or not _valid_file_identities(record.file_identities, record)
        or not _valid_receipt_requirements(record)
    ):
        raise HostOperationLogError("operation_record_invalid")


def _valid_branch(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and value == unicodedata.normalize("NFC", value)
        and len(value) <= 200
        and not value.startswith(("-", "/"))
        and not value.endswith((".", "/"))
        and not value.endswith(".lock")
        and ".." not in value
        and "@{" not in value
        and "//" not in value
        and "\\" not in value
        and not any(
            character in " ~^:?*[" or unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def _valid_created_directories(value: Any) -> bool:
    if not isinstance(value, tuple) or len(value) > 64:
        return False
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or ":" not in item:
            return False
        identity_bundle, path = item.split(":", 1)
        identities = identity_bundle.split("@")
        if len(identities) != 2 or any(
            re.fullmatch(r"[a-f0-9]+-[a-f0-9]+", identity) is None
            for identity in identities
        ):
            return False
        for identity in identities:
            device_text, inode_text = identity.split("-", 1)
            if int(device_text, 16) == 0 or int(inode_text, 16) == 0:
                return False
        try:
            normalized = DraftWorkspace.normalize_relative_path(path)
        except (DraftPolicyError, TypeError, ValueError):
            return False
        if normalized != path or path == ".git" or path in seen:
            return False
        seen.add(path)
    return True


def _valid_file_identities(value: Any, record: HostOperationRecord) -> bool:
    if not isinstance(value, tuple) or len(value) > 20:
        return False
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or ":" not in item:
            return False
        identity, path = item.split(":", 1)
        if identity != "missing" and re.fullmatch(
            r"[a-f0-9]+-[a-f0-9]+",
            identity,
        ) is None:
            return False
        if identity != "missing":
            device_text, inode_text = identity.split("-", 1)
            if int(device_text, 16) == 0 or int(inode_text, 16) == 0:
                return False
        try:
            normalized = DraftWorkspace.normalize_relative_path(path)
        except (DraftPolicyError, TypeError, ValueError):
            return False
        if normalized != path or path in seen:
            return False
        seen.add(path)
    apply_files = (
        tuple(item["path"] for item in record.apply_receipt["files"])
        if record.apply_receipt is not None
        else ()
    )
    identity_paths = tuple(item.split(":", 1)[1] for item in value)
    if value and identity_paths != apply_files:
        return False
    if record.action == "apply":
        if record.state == "applied":
            return bool(value) and identity_paths == apply_files
        if record.state in {"prepared", "applying"}:
            return not value
    if record.action == "revert" and record.state != "conflict":
        return bool(value) and identity_paths == apply_files
    return True


def _valid_apply_receipt(
    value: Any,
    record: HostOperationRecord,
) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {
        "apply_id",
        "revision",
        "snapshot_fingerprint",
        "files",
        "applied_at",
    }:
        return False
    try:
        files = value["files"]
        if (
            not isinstance(value["apply_id"], str)
            or type(value["revision"]) is not int
            or not isinstance(value["snapshot_fingerprint"], str)
            or type(value["applied_at"]) not in {int, float}
            or not isinstance(files, list)
            or any(
                not isinstance(item, dict)
                or set(item)
                != {"path", "existed_before", "before_sha256", "after_sha256"}
                or not isinstance(item["path"], str)
                or type(item["existed_before"]) is not bool
                or item["before_sha256"] is not None
                and not isinstance(item["before_sha256"], str)
                or item["after_sha256"] is not None
                and not isinstance(item["after_sha256"], str)
                for item in files
            )
        ):
            return False
        receipt = ApplyReceipt(
            apply_id=value["apply_id"],
            revision=value["revision"],
            snapshot_fingerprint=value["snapshot_fingerprint"],
            files=tuple(
                ApplyFileReceipt(**item)
                for item in files
            ),
            applied_at=value["applied_at"],
        )
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        receipt.revision == record.revision
        and (record.action != "apply" or receipt.apply_id == record.operation_id)
    )


def _valid_commit_receipt(
    value: Any,
    record: HostOperationRecord,
) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {
        "commit_id",
        "revision",
        "apply_id",
        "commit_sha",
        "parent_sha",
        "tree_sha",
        "message",
        "files",
        "branch",
        "committed_at",
    }:
        return False
    try:
        files = value["files"]
        if (
            not isinstance(value["commit_id"], str)
            or type(value["revision"]) is not int
            or not isinstance(value["apply_id"], str)
            or any(
                not isinstance(value[key], str)
                for key in (
                    "commit_sha",
                    "parent_sha",
                    "tree_sha",
                    "message",
                    "branch",
                )
            )
            or type(value["committed_at"]) not in {int, float}
            or not isinstance(files, list)
            or any(not isinstance(path, str) for path in files)
        ):
            return False
        safe_files = tuple(DraftWorkspace.normalize_relative_path(path) for path in files)
        message = normalize_commit_message(value["message"])
        committed_at = value["committed_at"]
    except (DraftPolicyError, KeyError, TypeError, ValueError):
        return False
    object_ids = (value["commit_sha"], value["parent_sha"], value["tree_sha"])
    apply_receipt = record.apply_receipt
    apply_files = (
        tuple(item["path"] for item in apply_receipt["files"])
        if apply_receipt is not None
        else ()
    )
    return bool(
        COMMIT_ID_PATTERN.fullmatch(str(value["commit_id"]))
        and type(value["revision"]) is int
        and value["revision"] == record.revision
        and re.fullmatch(r"^[A-Za-z0-9_-]{20,64}$", str(value["apply_id"]))
        and all(GIT_OBJECT_ID_PATTERN.fullmatch(str(item)) for item in object_ids)
        and value["commit_sha"] not in {value["parent_sha"], value["tree_sha"]}
        and message == value["message"]
        and safe_files
        and len(safe_files) <= 20
        and safe_files == tuple(files)
        and safe_files == tuple(sorted(set(safe_files)))
        and value["branch"] == record.branch
        and safe_files == apply_files
        and type(committed_at) in {int, float}
        and math.isfinite(committed_at)
        and committed_at >= 0
        and (record.action != "commit" or value["commit_id"] == record.operation_id)
        and (record.action != "commit" or value["parent_sha"] == record.expected_head)
        and (record.action != "undo" or value["commit_sha"] == record.expected_head)
        and (
            apply_receipt is None
            or value["apply_id"] == apply_receipt.get("apply_id")
        )
    )


def _valid_receipt_requirements(record: HostOperationRecord) -> bool:
    if record.action == "apply":
        if record.commit_receipt is not None:
            return False
        if record.state == "conflict":
            return True
        return (record.state in {"applying", "applied"}) == (
            record.apply_receipt is not None
        )
    if record.action == "revert":
        return record.apply_receipt is not None and record.commit_receipt is None
    if record.action == "commit":
        return bool(
            record.apply_receipt is not None
            and (
                record.state == "conflict"
                or (record.state == "committed")
                == (record.commit_receipt is not None)
            )
        )
    return record.apply_receipt is not None and record.commit_receipt is not None


def _same_intent(left: HostOperationRecord, right: HostOperationRecord) -> bool:
    return bool(
        all(
        getattr(left, field) == getattr(right, field)
        for field in (
            "operation_id",
            "action",
            "project_id",
            "revision",
            "branch",
            "expected_head",
            "patch_sha256",
            "patch",
        )
        )
        and (
            right.apply_receipt is None
            or left.apply_receipt == right.apply_receipt
        )
        and (
            right.commit_receipt is None
            or left.commit_receipt == right.commit_receipt
        )
        and (
            not right.created_directories
            or left.created_directories == right.created_directories
        )
        and (
            not right.file_identities
            or left.file_identities == right.file_identities
        )
    )


def _clone_record(record: HostOperationRecord) -> HostOperationRecord:
    return copy.deepcopy(record)
