from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .config import Settings


class EvidenceError(RuntimeError):
    pass


MAX_ARTIFACT_BYTES = 1_048_576
MAX_RECEIPT_BYTES = 1_048_576


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def build_receipt(
    settings: Settings,
    run: dict[str, Any],
    worker: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    source_lock = json.loads(settings.source_lock.read_text(encoding="utf-8"))
    module_boundary = json.loads(settings.module_boundary.read_text(encoding="utf-8"))
    inspect_root = settings.inspect_log_root.resolve()
    evidence_root = settings.evidence_root.resolve()
    destination = (evidence_root / run["run_id"]).resolve()
    destination.relative_to(evidence_root)
    destination.mkdir(parents=True, exist_ok=True)

    expected = worker.get("artifacts") or {}
    if not isinstance(expected, dict):
        raise EvidenceError("worker artifact manifest is invalid")
    source_dir = (inspect_root / run["run_id"]).resolve()
    source_dir.relative_to(inspect_root)
    if expected and (not source_dir.is_dir() or source_dir.is_symlink()):
        raise EvidenceError("worker evidence directory is unavailable or unsafe")
    copied: dict[str, dict[str, Any]] = {}
    for name, descriptor in sorted(expected.items()):
        if not isinstance(name, str) or Path(name).name != name or name.startswith("."):
            raise EvidenceError("worker artifact name is unsafe")
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("sha256"), str):
            raise EvidenceError("worker artifact descriptor is invalid")
        source = (source_dir / name).resolve()
        source.relative_to(source_dir)
        if not source.is_file() or source.is_symlink():
            raise EvidenceError(f"worker artifact is unavailable or unsafe: {name}")
        actual_hash = sha256_file(source)
        actual_size = source.stat().st_size
        if actual_hash != descriptor["sha256"] or actual_size != descriptor.get("sizeBytes"):
            raise EvidenceError(f"worker artifact failed integrity validation: {name}")
        target = destination / name
        shutil.copyfile(source, target)
        copied[name] = {"sha256": actual_hash, "sizeBytes": actual_size}

    receipt: dict[str, Any] = {
        "schemaVersion": 1,
        "runId": run["run_id"],
        "fixtureId": run["fixture_id"],
        "caseId": run["case_id"],
        "tenantId": run["tenant_id"],
        "projectId": run["project_id"],
        "actorId": run["actor_id"],
        "claimLevel": "harness_only",
        "packStatus": "fixture_only",
        "moduleVersion": module_boundary["moduleVersion"],
        "workerProtocolVersion": module_boundary["workerProtocolVersion"],
        "phase": run["phase"],
        "outcome": run["outcome"],
        "inspectStatus": run.get("inspect_status"),
        "cancelRequested": bool(worker.get("cancelRequested")),
        "cancelApplied": bool(worker.get("cancelApplied")),
        "errorType": worker.get("errorType"),
        "errorMessage": worker.get("errorMessage"),
        "replayVerified": bool(worker.get("replayVerified")),
        "timestamps": {
            "createdAt": run["created_at"],
            "startedAt": run["started_at"],
            "cancelRequestedAt": run.get("cancel_requested_at"),
            "cancelAppliedAt": run.get("cancel_applied_at"),
            "terminalAt": run["terminal_at"],
            "syncedAt": run.get("evidence_synced_at"),
        },
        "sourceLock": {
            "sha256": sha256_file(settings.source_lock),
            "moduleBoundarySha256": sha256_file(settings.module_boundary),
            "baseCommit": source_lock["modelMirrorBaseCommit"],
            "baseImageDigest": source_lock["baseImage"]["digest"],
            "python": source_lock["runtimes"]["python"],
            "inspectAi": source_lock["runtimes"]["inspectAi"],
            "mlflow": source_lock["runtimes"]["mlflow"],
            "lockedFiles": {
                name: descriptor["sha256"]
                for name, descriptor in sorted(source_lock["lockedFiles"].items())
            },
        },
        "artifacts": copied,
        "mlflow": {"experimentId": None, "runId": None, "traceId": None},
    }
    return write_receipt(destination, receipt), destination


def finalize_mlflow(
    destination: Path,
    receipt: dict[str, Any],
    *,
    experiment_id: str,
    run_id: str,
    trace_id: str,
    synced_at: str,
) -> dict[str, Any]:
    updated = dict(receipt)
    updated["timestamps"] = {**updated["timestamps"], "syncedAt": synced_at}
    updated["mlflow"] = {
        "experimentId": experiment_id,
        "runId": run_id,
        "traceId": trace_id,
    }
    return write_receipt(destination, updated)


def write_receipt(destination: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    body = dict(receipt)
    body.pop("receiptSha256", None)
    complete = {**body, "receiptSha256": sha256_bytes(canonical_json(body))}
    (destination / "receipt.json").write_bytes(canonical_json(complete) + b"\n")
    return complete


def verify_receipt(destination: Path, receipt: dict[str, Any]) -> None:
    destination = destination.resolve()
    body = dict(receipt)
    expected_receipt_hash = body.pop("receiptSha256", None)
    if expected_receipt_hash != sha256_bytes(canonical_json(body)):
        raise EvidenceError("receipt body hash mismatch")
    artifacts = receipt.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        raise EvidenceError("receipt artifact manifest is invalid")
    for name, descriptor in artifacts.items():
        if not isinstance(name, str) or Path(name).name != name or name.startswith("."):
            raise EvidenceError("receipt artifact name is unsafe")
        if not isinstance(descriptor, dict):
            raise EvidenceError(f"receipt artifact descriptor is invalid: {name}")
        source = destination / name
        if source.is_symlink():
            raise EvidenceError(f"receipt artifact is unavailable or unsafe: {name}")
        path = source.resolve()
        path.relative_to(destination)
        if not path.is_file():
            raise EvidenceError(f"receipt artifact is unavailable or unsafe: {name}")
        actual_size = path.stat().st_size
        if actual_size > MAX_ARTIFACT_BYTES:
            raise EvidenceError(f"receipt artifact exceeds the size limit: {name}")
        if sha256_file(path) != descriptor.get("sha256"):
            raise EvidenceError(f"receipt artifact hash mismatch: {name}")
        if actual_size != descriptor.get("sizeBytes"):
            raise EvidenceError(f"receipt artifact size mismatch: {name}")


def verify_persisted_receipt(destination: Path, receipt: dict[str, Any]) -> None:
    root = destination.resolve()
    receipt_path = root / "receipt.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise EvidenceError("persisted receipt is unavailable or unsafe")
    resolved = receipt_path.resolve()
    resolved.relative_to(root)
    if resolved.stat().st_size > MAX_RECEIPT_BYTES:
        raise EvidenceError("persisted receipt exceeds the size limit")
    try:
        persisted = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("persisted receipt is malformed") from exc
    if persisted != receipt:
        raise EvidenceError("persisted receipt does not match the control ledger")
    verify_receipt(root, receipt)


def read_verified_artifact(
    destination: Path, receipt: dict[str, Any], name: str
) -> tuple[bytes, str]:
    if Path(name).name != name or name.startswith("."):
        raise EvidenceError("artifact name is unsafe")
    artifacts = receipt.get("artifacts") or {}
    if not isinstance(artifacts, dict) or name not in artifacts:
        raise KeyError(name)
    verify_persisted_receipt(destination, receipt)
    source = destination.resolve() / name
    if source.is_symlink():
        raise EvidenceError("artifact path is a symbolic link")
    resolved = source.resolve()
    resolved.relative_to(destination.resolve())
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = artifacts[name]
        with os.fdopen(os.open(source, flags), "rb") as handle:
            data = handle.read(MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise EvidenceError("artifact could not be read safely") from exc
    if len(data) > MAX_ARTIFACT_BYTES:
        raise EvidenceError("artifact exceeds the size limit")
    actual_hash = sha256_bytes(data)
    if len(data) != descriptor.get("sizeBytes") or actual_hash != descriptor.get("sha256"):
        raise EvidenceError("artifact changed during download verification")
    return data, actual_hash
