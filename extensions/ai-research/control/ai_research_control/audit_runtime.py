from __future__ import annotations

import argparse
import json
import re
from typing import Any

from mlflow import MlflowClient

from .config import Settings
from .evidence import verify_receipt
from .store import RunStore


ALLOWED_METRICS = {"duration_seconds", "artifact_count"}
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN " + rb"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"sk-" + rb"(?:or-v1-)?[A-Za-z0-9_-]{32,}"),
    re.compile(rb"gh" + rb"[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"AIza" + rb"[A-Za-z0-9_-]{30,}"),
]
FORBIDDEN_CREDENTIAL_NAMES = (
    b"OPENROUTER" + b"_API_KEY",
    b"LLM" + b"_GATEWAY_KEY",
    b"DIFY" + b"_API_KEY",
)


def audit_run(
    run_id: str,
    *,
    settings: Settings,
    store: RunStore,
    client: MlflowClient,
) -> dict[str, Any]:
    run = store.get(run_id)
    receipt = run.get("receipt_json")
    if run.get("evidence_state") != "synced" or not isinstance(receipt, dict):
        raise RuntimeError(f"{run_id}: evidence is not synced")
    evidence_dir = (settings.evidence_root / run_id).resolve()
    evidence_dir.relative_to(settings.evidence_root.resolve())
    verify_receipt(evidence_dir, receipt)
    for path in evidence_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if any(name in data for name in FORBIDDEN_CREDENTIAL_NAMES) or any(
            pattern.search(data) for pattern in SECRET_PATTERNS
        ):
            raise RuntimeError(f"{run_id}: credential material detected in evidence")

    expected = {
        "outcome": run["outcome"],
        "inspectStatus": run["inspect_status"],
        "cancelRequested": run["cancel_requested"],
        "cancelApplied": run["cancel_applied"],
        "errorType": run["error_type"],
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"{run_id}: receipt disagrees with ledger field {key}")
    receipt_times = receipt.get("timestamps") or {}
    time_pairs = {
        "createdAt": "created_at",
        "startedAt": "started_at",
        "cancelRequestedAt": "cancel_requested_at",
        "cancelAppliedAt": "cancel_applied_at",
        "terminalAt": "terminal_at",
        "syncedAt": "evidence_synced_at",
    }
    for receipt_key, ledger_key in time_pairs.items():
        if receipt_times.get(receipt_key) != run.get(ledger_key):
            raise RuntimeError(f"{run_id}: receipt disagrees with ledger time {receipt_key}")

    mlflow_ids = receipt.get("mlflow") or {}
    if mlflow_ids.get("runId") != run.get("mlflow_run_id"):
        raise RuntimeError(f"{run_id}: MLflow run identity disagrees with ledger")
    mlflow_run = client.get_run(str(mlflow_ids["runId"]))
    if set(mlflow_run.data.metrics) - ALLOWED_METRICS:
        raise RuntimeError(f"{run_id}: scientific or unknown metric detected")
    if (
        mlflow_run.data.params.get("claim_level") != "harness_only"
        or mlflow_run.data.params.get("pack_status") != "fixture_only"
    ):
        raise RuntimeError(f"{run_id}: MLflow fixture labels are missing")
    artifact_names = {
        item.path.rsplit("/", 1)[-1]
        for item in client.list_artifacts(str(mlflow_ids["runId"]), "evidence")
    }
    if "receipt.json" not in artifact_names:
        raise RuntimeError(f"{run_id}: MLflow receipt artifact is missing")

    trace = client.get_trace(str(mlflow_ids["traceId"]), display=False)
    trace_experiment = trace.info.trace_location.mlflow_experiment
    if (
        trace.info.tags.get("modelmirror.run_id") != run_id
        or trace_experiment is None
        or trace_experiment.experiment_id != str(mlflow_ids["experimentId"])
        or not trace.data.spans
    ):
        raise RuntimeError(f"{run_id}: MLflow trace identity or content is inconsistent")
    return {
        "runId": run_id,
        "mlflowRunId": mlflow_ids["runId"],
        "traceId": mlflow_ids["traceId"],
        "artifactCount": len(artifact_names),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", action="append", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    store = RunStore(settings.control_db)
    client = MlflowClient(tracking_uri=settings.mlflow_uri)
    results = [
        audit_run(run_id, settings=settings, store=store, client=client)
        for run_id in args.run_id
    ]
    print(json.dumps({"status": "passed", "runs": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
