from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_research_control.config import Settings
from ai_research_control.evidence import EvidenceError, build_receipt, verify_receipt


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def settings(tmp_path: Path) -> Settings:
    source_lock = tmp_path / "source-lock.json"
    boundary = tmp_path / "module-boundary.json"
    write_json(
        source_lock,
        {
            "modelMirrorBaseCommit": "a" * 40,
            "runtimes": {"python": "3.12.13", "inspectAi": "0.3.260", "mlflow": "3.15.1"},
            "baseImage": {"digest": "sha256:" + "b" * 64},
            "lockedFiles": {
                "control/requirements.lock": {"sha256": "c" * 64},
                "worker/requirements.lock": {"sha256": "d" * 64},
            },
        },
    )
    write_json(
        boundary,
        {"moduleVersion": "0.1.0-ar0", "workerProtocolVersion": 1},
    )
    return Settings(
        control_db=tmp_path / "control.db",
        evidence_root=tmp_path / "evidence",
        inspect_log_root=tmp_path / "inspect",
        worker_socket=tmp_path / "worker.sock",
        mlflow_uri="http://ai-research-tracking:5000",
        mlflow_experiment="test",
        source_lock=source_lock,
        module_boundary=boundary,
        poll_seconds=0.1,
        docs_enabled=False,
    )


def run_record(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "fixture_id": "inspect-smoke-v1",
        "case_id": "success",
        "tenant_id": "local",
        "project_id": "local",
        "actor_id": "local",
        "phase": "terminal",
        "outcome": "success",
        "inspect_status": "success",
        "created_at": "2026-08-23T00:00:00Z",
        "started_at": "2026-08-23T00:00:01Z",
        "terminal_at": "2026-08-23T00:00:02Z",
        "cancel_requested_at": None,
        "cancel_applied_at": None,
        "evidence_synced_at": None,
    }


def test_receipt_detects_artifact_tampering(tmp_path: Path) -> None:
    config = settings(tmp_path)
    run_id = "ar0_test"
    source = config.inspect_log_root / run_id
    source.mkdir(parents=True)
    artifact = source / "eval-log.json"
    artifact.write_text('{"status":"success"}\n', encoding="utf-8")
    data = artifact.read_bytes()
    worker = {
        "inspectStatus": "success",
        "cancelRequested": False,
        "cancelApplied": False,
        "replayVerified": True,
        "artifacts": {
            "eval-log.json": {
                "sha256": hashlib.sha256(data).hexdigest(),
                "sizeBytes": len(data),
            }
        },
    }

    receipt, destination = build_receipt(config, run_record(run_id), worker)
    verify_receipt(destination, receipt)
    assert receipt["inspectStatus"] == "success"
    assert receipt["sourceLock"]["baseImageDigest"] == "sha256:" + "b" * 64
    assert receipt["sourceLock"]["lockedFiles"] == {
        "control/requirements.lock": "c" * 64,
        "worker/requirements.lock": "d" * 64,
    }
    assert receipt["timestamps"] == {
        "createdAt": "2026-08-23T00:00:00Z",
        "startedAt": "2026-08-23T00:00:01Z",
        "cancelRequestedAt": None,
        "cancelAppliedAt": None,
        "terminalAt": "2026-08-23T00:00:02Z",
        "syncedAt": None,
    }
    (destination / "eval-log.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(EvidenceError, match="hash mismatch"):
        verify_receipt(destination, receipt)


def test_receipt_rejects_path_traversal(tmp_path: Path) -> None:
    config = settings(tmp_path)
    run_id = "ar0_test"
    (config.inspect_log_root / run_id).mkdir(parents=True)
    worker = {
        "artifacts": {"../outside": {"sha256": "0" * 64, "sizeBytes": 1}},
    }
    with pytest.raises(EvidenceError, match="unsafe"):
        build_receipt(config, run_record(run_id), worker)


def test_queued_cancel_can_create_receipt_without_worker_artifacts(tmp_path: Path) -> None:
    config = settings(tmp_path)
    run = run_record("ar0_queued_cancel")
    run.update({"case_id": "long_running_cancel", "outcome": "cancelled", "started_at": None})
    worker = {
        "inspectStatus": None,
        "cancelRequested": True,
        "cancelApplied": False,
        "replayVerified": False,
        "artifacts": {},
    }

    receipt, destination = build_receipt(config, run, worker)

    assert receipt["outcome"] == "cancelled"
    assert receipt["artifacts"] == {}
    assert (destination / "receipt.json").is_file()
    verify_receipt(destination, receipt)
