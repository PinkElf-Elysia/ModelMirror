"""Offline recovery of the single-instance workflow execution snapshot.

Operator procedure (run with the server's Python environment):

1. Stop every server/coordinator writing to the selected AGENT_TASK_STORAGE_DIR.
   Keep them stopped through inspection, restore and verification. Hash checks
   detect stale observations; they are not a cross-process lock.
2. Run ``python -m server.xpert_runtime.execution_store_recovery inspect
   --storage-dir <directory>`` from the repository root (or omit ``server.``
   when running from the server directory). Inspect prints only safe metadata.
3. Preserve both files separately before recovery. A backup is the previous
   valid snapshot, not necessarily the latest state: restoring it can lose the
   most recent transitions. Reconcile external effects before enabling runs.
4. Run ``restore-backup --storage-dir <directory>
   --expected-primary-sha256 <inspected hash or missing>
   --expected-backup-sha256 <inspected hash>`` with the same module. Restoration
   requires an invalid or explicitly missing primary and a valid backup. The
   ``missing`` form is create-only; there is no force/empty mode.
5. Re-run inspect and retain the original backup plus any create-only corrupt
   archive (there is no archive when the primary was missing). Only restart after
   verification and review of unfinished executions. Recovery does not make
   arbitrary external actions exactly-once.

Files are flushed before atomic replacement. POSIX directory fsync is attempted;
Windows directory durability still depends on the filesystem. Do not delete an
invalid primary or restart old fail-open code as a substitute for recovery.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from .execution_store import (
    WorkflowExecutionStorageError,
    WorkflowExecutionStore,
)


_PRIMARY_NAME = "workflow_executions.json"
_BACKUP_NAME = "workflow_executions.bak.json"


class RecoveryCommandError(RuntimeError):
    """Safe operator-facing recovery failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _inspect_file(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "exists": False,
            "sha256": None,
            "valid": False,
            "version": None,
            "item_count": 0,
            "status_counts": {},
            "error_code": "snapshot_missing",
        }
    except OSError:
        metadata = None
    if metadata is None or not stat.S_ISREG(metadata.st_mode):
        return {
            "exists": True,
            "sha256": None,
            "valid": False,
            "version": None,
            "item_count": 0,
            "status_counts": {},
            "error_code": (
                "snapshot_unreadable" if metadata is None else "snapshot_not_regular"
            ),
        }
    try:
        content = path.read_bytes()
    except OSError:
        return {
            "exists": True,
            "sha256": None,
            "valid": False,
            "version": None,
            "item_count": 0,
            "status_counts": {},
            "error_code": "snapshot_unreadable",
        }
    digest = _sha256(content)
    try:
        summary = WorkflowExecutionStore.inspect_snapshot_bytes(content)
    except WorkflowExecutionStorageError:
        return {
            "exists": True,
            "sha256": digest,
            "valid": False,
            "version": None,
            "item_count": 0,
            "status_counts": {},
            "error_code": "snapshot_invalid",
        }
    return {
        "exists": True,
        "sha256": digest,
        "valid": True,
        "version": summary["version"],
        "item_count": summary["item_count"],
        "status_counts": summary["status_counts"],
        "error_code": None,
    }


def inspect_storage(storage_dir: str | Path) -> dict[str, Any]:
    root = Path(storage_dir)
    return {
        "status": "inspected",
        "primary": _inspect_file(root / _PRIMARY_NAME),
        "backup": _inspect_file(root / _BACKUP_NAME),
    }


def restore_backup(
    storage_dir: str | Path,
    *,
    expected_primary_sha256: str,
    expected_backup_sha256: str,
) -> dict[str, Any]:
    root = Path(storage_dir)
    primary = root / _PRIMARY_NAME
    backup = root / _BACKUP_NAME
    primary_summary = _inspect_file(primary)
    backup_summary = _inspect_file(backup)
    expected_primary = str(expected_primary_sha256).lower()
    primary_missing = not primary_summary["exists"]
    if primary_missing and expected_primary != "missing":
        raise RecoveryCommandError(
            "primary_snapshot_changed",
            "The primary workflow execution snapshot changed after inspection.",
        )
    if not primary_missing and expected_primary == "missing":
        raise RecoveryCommandError(
            "primary_snapshot_changed",
            "The primary workflow execution snapshot changed after inspection.",
        )
    if not primary_missing and primary_summary["sha256"] is None:
        raise RecoveryCommandError(
            "primary_snapshot_unavailable",
            "The primary workflow execution snapshot is unavailable.",
        )
    if primary_summary["valid"]:
        raise RecoveryCommandError(
            "primary_snapshot_still_valid",
            "The primary workflow execution snapshot is still valid.",
        )
    if not backup_summary["valid"] or backup_summary["sha256"] is None:
        raise RecoveryCommandError(
            "backup_snapshot_invalid",
            "The workflow execution backup is unavailable or invalid.",
        )
    if not primary_missing and not hmac.compare_digest(
        str(primary_summary["sha256"]), expected_primary
    ):
        raise RecoveryCommandError(
            "primary_snapshot_changed",
            "The primary workflow execution snapshot changed after inspection.",
        )
    if not hmac.compare_digest(
        str(backup_summary["sha256"]), str(expected_backup_sha256).lower()
    ):
        raise RecoveryCommandError(
            "backup_snapshot_changed",
            "The workflow execution backup changed after inspection.",
        )

    try:
        corrupt_content = None if primary_missing else primary.read_bytes()
        backup_content = backup.read_bytes()
        if corrupt_content is not None and not hmac.compare_digest(
            _sha256(corrupt_content), expected_primary
        ):
            raise RecoveryCommandError(
                "primary_snapshot_changed",
                "The primary workflow execution snapshot changed after inspection.",
            )
        if not hmac.compare_digest(
            _sha256(backup_content), str(expected_backup_sha256).lower()
        ):
            raise RecoveryCommandError(
                "backup_snapshot_changed",
                "The workflow execution backup changed after inspection.",
            )
        WorkflowExecutionStore.inspect_snapshot_bytes(backup_content)
    except RecoveryCommandError:
        raise
    except (OSError, WorkflowExecutionStorageError) as exc:
        raise RecoveryCommandError(
            "snapshot_changed_after_inspection",
            "A workflow execution snapshot changed after inspection.",
        ) from exc
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    quarantine_name = (
        f"workflow_executions.corrupt.{timestamp}."
        f"{primary_summary['sha256'][:8]}.json"
        if corrupt_content is not None
        else None
    )
    quarantine = root / quarantine_name if quarantine_name is not None else None
    replacement = root / f"{_PRIMARY_NAME}.{uuid.uuid4().hex}.restore.tmp"
    try:
        if quarantine is not None and corrupt_content is not None:
            WorkflowExecutionStore._write_fsynced(quarantine, corrupt_content)
        WorkflowExecutionStore._write_fsynced(replacement, backup_content)
        if not hmac.compare_digest(
            _sha256(replacement.read_bytes()),
            _sha256(backup_content),
        ):
            raise RecoveryCommandError(
                "replacement_snapshot_changed",
                "The replacement workflow execution snapshot could not be verified.",
            )
        WorkflowExecutionStore.inspect_snapshot_bytes(replacement.read_bytes())
        current_primary = _inspect_file(primary)
        primary_changed = (
            current_primary["exists"]
            if primary_missing
            else (
                not current_primary["exists"]
                or current_primary["sha256"] is None
                or not hmac.compare_digest(
                    str(current_primary["sha256"]), expected_primary
                )
            )
        )
        if primary_changed or not hmac.compare_digest(
            _sha256(backup.read_bytes()), str(expected_backup_sha256).lower()
        ):
            raise RecoveryCommandError(
                "snapshot_changed_before_restore",
                "A workflow execution snapshot changed before restore.",
            )
        if primary_missing:
            os.link(replacement, primary)
            replacement.unlink()
        else:
            os.replace(replacement, primary)
        WorkflowExecutionStore._fsync_directory(root)
    except FileExistsError as exc:
        if primary_missing:
            raise RecoveryCommandError(
                "primary_snapshot_changed",
                "The primary workflow execution snapshot changed before restore.",
            ) from exc
        raise RecoveryCommandError(
            "corrupt_snapshot_archive_exists",
            "A corrupt snapshot archive with this name already exists.",
        ) from exc
    except RecoveryCommandError:
        raise
    except (OSError, WorkflowExecutionStorageError) as exc:
        raise RecoveryCommandError(
            "backup_restore_failed",
            "The workflow execution backup could not be restored.",
        ) from exc
    finally:
        try:
            replacement.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "status": "restored",
        "primary_sha256": _sha256(backup_content),
        "archived_corrupt_sha256": (
            _sha256(corrupt_content) if corrupt_content is not None else None
        ),
        "archived_corrupt_name": quarantine_name,
        "item_count": backup_summary["item_count"],
        "status_counts": backup_summary["status_counts"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly restore the workflow execution snapshot.",
        epilog=(
            "OFFLINE ONLY: stop all writers before inspection and restore. "
            "Hash checks are not a process lock. A backup may lose recent "
            "transitions; reconcile external effects before restarting."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--storage-dir", required=True)
    restore_parser = subparsers.add_parser("restore-backup")
    restore_parser.add_argument("--storage-dir", required=True)
    restore_parser.add_argument("--expected-primary-sha256", required=True)
    restore_parser.add_argument("--expected-backup-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_storage(args.storage_dir)
        else:
            result = restore_backup(
                args.storage_dir,
                expected_primary_sha256=args.expected_primary_sha256,
                expected_backup_sha256=args.expected_backup_sha256,
            )
    except RecoveryCommandError as exc:
        print(
            json.dumps(
                {"status": "failed", "code": exc.code, "error": str(exc)},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
