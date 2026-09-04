from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def loopback_url(environment_name: str, port_environment_name: str, default_port: int) -> str:
    default = f"http://127.0.0.1:{os.getenv(port_environment_name, str(default_port))}"
    value = os.getenv(environment_name, default).rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError(f"{environment_name} must be an HTTP loopback origin")
    try:
        if parsed.port is None:
            raise RuntimeError(f"{environment_name} must include an explicit port")
    except ValueError as exc:
        raise RuntimeError(f"{environment_name} has an invalid port") from exc
    return value


CONTROL = loopback_url("AI_RESEARCH_ACCEPTANCE_CONTROL_URL", "AI_RESEARCH_CONTROL_PORT", 8790)
TRACKING = loopback_url("AI_RESEARCH_ACCEPTANCE_TRACKING_URL", "AI_RESEARCH_MLFLOW_PORT", 8791)
INSPECT_VIEW = loopback_url(
    "AI_RESEARCH_ACCEPTANCE_INSPECT_VIEW_URL", "AI_RESEARCH_INSPECT_VIEW_PORT", 8793
)


class AcceptanceFailure(RuntimeError):
    pass


def request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
) -> tuple[int, Any]:
    body = raw
    actual_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        actual_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=actual_headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()
            content_type = response.headers.get_content_type()
            if not content:
                decoded: Any = None
            elif content_type == "application/json":
                decoded = json.loads(content)
            else:
                decoded = content
            return response.status, decoded
    except urllib.error.HTTPError as exc:
        content = exc.read()
        try:
            decoded = json.loads(content) if content else None
        except json.JSONDecodeError:
            decoded = content.decode("utf-8", errors="replace")
        return exc.code, decoded


def wait_ready(timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            status, body = request("GET", f"{CONTROL}/readyz")
            last = (status, body)
            if status == 200 and body.get("status") == "ready":
                return
        except OSError as exc:
            last = str(exc)
        time.sleep(1)
    raise AcceptanceFailure(f"module did not become ready: {last}")


def create(case_id: str, key: str) -> tuple[int, dict[str, Any]]:
    status, body = request(
        "POST",
        f"{CONTROL}/api/v1/runs",
        {
            "fixtureId": "inspect-smoke-v1",
            "caseId": case_id,
            "idempotencyKey": key,
        },
    )
    if status not in {200, 201} or not isinstance(body, dict):
        raise AcceptanceFailure(f"create {case_id} failed: {status} {body}")
    return status, body


def wait_run(run_id: str, timeout: float = 240.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        status, body = request("GET", f"{CONTROL}/api/v1/runs/{run_id}")
        last = (status, body)
        if status == 200 and body.get("phase") == "terminal" and body.get("evidenceState") == "synced":
            return body
        time.sleep(0.5)
    raise AcceptanceFailure(f"run did not become terminal and synced: {last}")


def wait_running(run_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = request("GET", f"{CONTROL}/api/v1/runs/{run_id}")
        if status == 200 and body.get("phase") == "running":
            return body
        time.sleep(0.25)
    raise AcceptanceFailure("cancellation fixture never entered running phase")


def verify_run(run: dict[str, Any], outcome: str, inspect_status: str) -> None:
    if run.get("outcome") != outcome or run.get("inspectStatus") != inspect_status:
        raise AcceptanceFailure(f"unexpected terminal mapping: {run}")
    if not run.get("mlflowRunId"):
        raise AcceptanceFailure("synced run is missing MLflow identity")


def verify_console_evidence(run: dict[str, Any]) -> None:
    status, evidence = request(
        "GET", f"{CONTROL}/api/v1/runs/{run['runId']}/evidence"
    )
    if (
        status != 200
        or evidence.get("integrityStatus") != "verified"
        or evidence.get("evidenceState") != "synced"
        or evidence.get("receipt", {}).get("runId") != run["runId"]
    ):
        raise AcceptanceFailure(f"console evidence was not verified: {status} {evidence}")
    artifacts = evidence.get("artifacts") or []
    if not artifacts:
        raise AcceptanceFailure("console evidence did not expose registered artifacts")
    artifact = artifacts[0]
    status, content = request("GET", f"{CONTROL}{artifact['downloadUrl']}")
    if status != 200 or not isinstance(content, bytes) or len(content) != artifact["sizeBytes"]:
        raise AcceptanceFailure("registered artifact download failed")
    unknown_status, _ = request(
        "GET", f"{CONTROL}/api/v1/runs/{run['runId']}/artifacts/not-registered.json"
    )
    traversal_status, _ = request(
        "GET", f"{CONTROL}/api/v1/runs/{run['runId']}/artifacts/%2e%2e%2freceipt.json"
    )
    if unknown_status != 404 or traversal_status not in {404, 409}:
        raise AcceptanceFailure("artifact allowlist or traversal protection failed")


def verify_mlflow_run(run: dict[str, Any]) -> None:
    run_id = urllib.parse.quote(str(run["mlflowRunId"]), safe="")
    status, payload = request(
        "GET", f"{TRACKING}/api/2.0/mlflow/runs/get?run_id={run_id}"
    )
    if status != 200 or not isinstance(payload, dict):
        raise AcceptanceFailure(f"MLflow run was not readable: {status} {payload}")
    data = ((payload.get("run") or {}).get("data") or {})
    metric_keys = {item.get("key") for item in data.get("metrics", [])}
    if not metric_keys.issubset({"duration_seconds", "artifact_count"}):
        raise AcceptanceFailure(f"unexpected or scientific MLflow metrics: {metric_keys}")
    params = {item.get("key"): item.get("value") for item in data.get("params", [])}
    if params.get("claim_level") != "harness_only" or params.get("pack_status") != "fixture_only":
        raise AcceptanceFailure(f"MLflow fixture claim labels are missing: {params}")
    status, artifacts = request(
        "GET",
        f"{TRACKING}/api/2.0/mlflow/artifacts/list?run_id={run_id}&path=evidence",
    )
    names = {Path(item.get("path", "")).name for item in (artifacts or {}).get("files", [])}
    if status != 200 or "receipt.json" not in names:
        raise AcceptanceFailure(f"MLflow receipt artifact was not persisted: {status} {artifacts}")


def wait_terminal_without_sync(run_id: str, timeout: float = 240.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        status, body = request("GET", f"{CONTROL}/api/v1/runs/{run_id}")
        last = (status, body)
        if status == 200 and body.get("phase") == "terminal":
            return body
        time.sleep(0.5)
    raise AcceptanceFailure(f"run did not become terminal: {last}")


def write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def initial(state_path: Path) -> None:
    wait_ready()
    status, module = request("GET", f"{CONTROL}/api/v1/module")
    capability_claims = module.get("capabilityClaims", {})
    fixture_claim = capability_claims.get("fixtureExecution", {})
    literature_claim = capability_claims.get("literatureResearch", {})
    if (
        status != 200
        or module.get("moduleVersion") != "0.3.0-v0.1"
        or "claimLevel" in module
        or "packStatus" in module
        or fixture_claim.get("claimLevel") != "harness_only"
        or fixture_claim.get("packStatus") != "fixture_only"
        or literature_claim.get("scientificClaim") != "none"
        or literature_claim.get("acceptanceState") != "pending_live_acceptance"
        or literature_claim.get("workflowSource") != "local_deep_research"
        or module.get("capabilities", {}).get("modelEvaluation") is not False
    ):
        raise AcceptanceFailure(f"module claims are invalid: {status} {module}")
    system_status, system = request("GET", f"{CONTROL}/api/v1/system")
    if system_status != 200 or system.get("status") not in {"ready", "degraded"}:
        raise AcceptanceFailure(f"system status is invalid: {system_status} {system}")
    deep_status, deep_body = request(
        "GET", f"{CONTROL}/runs/example/evidence", headers={"Accept": "text/html"}
    )
    missing_api, missing_api_body = request(
        "GET", f"{CONTROL}/api/v1/does-not-exist", headers={"Accept": "text/html"}
    )
    missing_asset, missing_asset_body = request(
        "GET", f"{CONTROL}/assets/does-not-exist.js", headers={"Accept": "text/html"}
    )
    if (
        deep_status != 200
        or not isinstance(deep_body, bytes)
        or "模镜科研控制台" not in deep_body.decode("utf-8")
        or missing_api != 404
        or not isinstance(missing_api_body, dict)
        or missing_asset != 404
        or "<title>" in str(missing_asset_body)
    ):
        raise AcceptanceFailure("SPA deep-link or API/asset fallback contract failed")
    suffix = uuid.uuid4().hex

    success_key = f"ar0:{suffix}:success"
    created_status, created = create("success", success_key)
    success = wait_run(created["runId"])
    verify_run(success, "success", "success")
    verify_mlflow_run(success)
    verify_console_evidence(success)
    if success.get("replayVerified") is not True:
        raise AcceptanceFailure("success fixture did not verify config replay")
    repeated_status, repeated = create("success", success_key)
    if created_status != 201 or repeated_status != 200 or repeated["runId"] != success["runId"]:
        raise AcceptanceFailure("idempotency replay created a second run")
    conflict_status, _ = request(
        "POST",
        f"{CONTROL}/api/v1/runs",
        {
            "fixtureId": "inspect-smoke-v1",
            "caseId": "task_error",
            "idempotencyKey": success_key,
        },
    )
    if conflict_status != 409:
        raise AcceptanceFailure("idempotency conflict was not rejected")

    _, error_created = create("task_error", f"ar0:{suffix}:error")
    task_error = wait_run(error_created["runId"])
    verify_run(task_error, "task_error", "error")
    verify_mlflow_run(task_error)
    verify_console_evidence(task_error)

    _, cancel_created = create("long_running_cancel", f"ar0:{suffix}:cancel")
    wait_running(cancel_created["runId"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        cancel_results = list(
            pool.map(
                lambda _: request(
                    "POST", f"{CONTROL}/api/v1/runs/{cancel_created['runId']}/cancel"
                ),
                range(3),
            )
        )
    if any(status != 200 for status, _ in cancel_results):
        raise AcceptanceFailure(f"concurrent cancel request failed: {cancel_results}")
    cancelled = wait_run(cancel_created["runId"])
    verify_run(cancelled, "cancelled", "error")
    verify_mlflow_run(cancelled)
    verify_console_evidence(cancelled)
    if cancelled.get("cancelRequested") is not True or cancelled.get("cancelApplied") is not True:
        raise AcceptanceFailure("cancel request/applied facts were not preserved")
    terminal_facts = {
        key: cancelled.get(key)
        for key in (
            "phase",
            "outcome",
            "inspectStatus",
            "cancelRequested",
            "cancelApplied",
            "errorType",
            "errorMessage",
            "mlflowRunId",
        )
    }
    for _ in range(2):
        status, repeated_cancel = request(
            "POST", f"{CONTROL}/api/v1/runs/{cancel_created['runId']}/cancel"
        )
        if status != 200 or any(
            repeated_cancel.get(key) != value for key, value in terminal_facts.items()
        ):
            raise AcceptanceFailure("terminal cancel mutated preserved terminal facts")

    event_status, events = request(
        "GET", f"{CONTROL}/api/v1/runs/{cancel_created['runId']}/events?afterSeq=0"
    )
    sequences = [item["sequence"] for item in events.get("items", [])]
    if event_status != 200 or sequences != sorted(set(sequences)):
        raise AcceptanceFailure("event sequence is not ordered and unique")
    summary_status, summary = request("GET", f"{CONTROL}/api/v1/runs/summary")
    filtered_status, filtered = request(
        "GET", f"{CONTROL}/api/v1/runs?caseId=task_error&phase=terminal&outcome=task_error"
    )
    if (
        summary_status != 200
        or summary.get("total", 0) < 3
        or filtered_status != 200
        or not all(item["caseId"] == "task_error" for item in filtered.get("items", []))
    ):
        raise AcceptanceFailure("summary or AND-filter query failed")

    bad_tenant, _ = request(
        "POST",
        f"{CONTROL}/api/v1/runs",
        {
            "fixtureId": "inspect-smoke-v1",
            "caseId": "success",
            "idempotencyKey": f"ar0:{suffix}:tenant",
            "tenantId": "other",
        },
    )
    arbitrary_command, _ = request(
        "POST",
        f"{CONTROL}/api/v1/runs",
        {
            "fixtureId": "inspect-smoke-v1",
            "caseId": "success",
            "idempotencyKey": f"ar0:{suffix}:command",
            "command": "id",
        },
    )
    docs_status, _ = request("GET", f"{CONTROL}/docs")
    if (bad_tenant, arbitrary_command, docs_status) != (422, 422, 404):
        raise AcceptanceFailure("frozen request/docs surface failed open")

    bad_trace, _ = request(
        "POST",
        f"{TRACKING}/v1/traces",
        headers={"Content-Type": "application/x-protobuf"},
        raw=b"",
    )
    if bad_trace < 400:
        raise AcceptanceFailure("MLflow accepted an OTLP trace without an experiment header")
    wrong_trace, _ = request(
        "POST",
        f"{TRACKING}/v1/traces",
        headers={
            "Content-Type": "application/x-protobuf",
            "x-mlflow-experiment-id": "999999999",
        },
        raw=b"",
    )
    if wrong_trace < 400:
        raise AcceptanceFailure("MLflow accepted an OTLP trace for an unknown experiment")

    write_state(
        state_path,
        {
            "runs": [success["runId"], task_error["runId"], cancelled["runId"]],
            "mlflowRuns": [
                success["mlflowRunId"],
                task_error["mlflowRunId"],
                cancelled["mlflowRunId"],
            ],
        },
    )
    print("initial fixture acceptance passed")


def inspect_view_logs(_: Path) -> None:
    status, payload = request(
        "GET",
        f"{INSPECT_VIEW}/api/log-files",
        headers={"Origin": INSPECT_VIEW},
    )
    files = (payload or {}).get("files", []) if isinstance(payload, dict) else []
    if status != 200 or len(files) < 3 or not all(str(item.get("name", "")).endswith(".eval") for item in files):
        raise AcceptanceFailure(f"Inspect View did not expose recursive EvalLog files: {status} {payload}")
    print("Inspect View recursive EvalLog acceptance passed")


def view_degraded(state_path: Path) -> None:
    status, system = request("GET", f"{CONTROL}/api/v1/system")
    ready_status, ready = request("GET", f"{CONTROL}/readyz")
    created_status, created = create("success", f"ar1:{uuid.uuid4().hex}:view-degraded")
    if status != 200 or system.get("status") != "degraded" or ready_status != 200 or created_status != 201:
        raise AcceptanceFailure(f"optional View incorrectly changed readiness: {system} {ready}")
    completed = wait_run(created["runId"])
    verify_run(completed, "success", "success")
    verify_mlflow_run(completed)
    verify_console_evidence(completed)
    write_state(
        state_path,
        {"schemaVersion": 1, "status": "passed", "runId": completed["runId"]},
    )
    print("optional Inspect View degraded acceptance passed")


def required_not_ready(_: Path) -> None:
    status, system = request("GET", f"{CONTROL}/api/v1/system")
    list_status, runs = request("GET", f"{CONTROL}/api/v1/runs?limit=1")
    create_status, _ = request(
        "POST",
        f"{CONTROL}/api/v1/runs",
        {
            "fixtureId": "inspect-smoke-v1",
            "caseId": "success",
            "idempotencyKey": f"ar1:{uuid.uuid4().hex}:required-offline",
        },
    )
    if (
        status != 200
        or system.get("status") != "not_ready"
        or list_status != 200
        or not isinstance(runs.get("items"), list)
        or create_status != 503
    ):
        raise AcceptanceFailure("required dependency gate or history browsing failed")
    print("required dependency not-ready acceptance passed")


def recovery(state_path: Path) -> None:
    wait_ready()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for index, run_id in enumerate(state["runs"]):
        status, run = request("GET", f"{CONTROL}/api/v1/runs/{run_id}")
        if (
            status != 200
            or run.get("phase") != "terminal"
            or run.get("evidenceState") != "synced"
            or run.get("mlflowRunId") != state["mlflowRuns"][index]
        ):
            raise AcceptanceFailure(f"run did not survive restart: {run_id} {run}")
    print("restart recovery acceptance passed")


def outbox_create(state_path: Path) -> None:
    wait_ready()
    _, created = create("success", f"ar0:{uuid.uuid4().hex}:outbox")
    wait_running(created["runId"])
    write_state(state_path, {"runId": created["runId"]})
    print("outbox fixture is running")


def outbox_terminal(state_path: Path) -> None:
    run_id = json.loads(state_path.read_text(encoding="utf-8"))["runId"]
    run = wait_terminal_without_sync(run_id)
    if run.get("outcome") != "success" or run.get("evidenceState") not in {"pending", "failed"}:
        raise AcceptanceFailure(f"offline terminal/outbox state was not preserved: {run}")
    if run.get("mlflowRunId") is not None:
        raise AcceptanceFailure("offline run acquired a false MLflow identity")
    print("offline terminal remained in the durable outbox")


def outbox_recovery(state_path: Path) -> None:
    wait_ready()
    run_id = json.loads(state_path.read_text(encoding="utf-8"))["runId"]
    run = wait_run(run_id)
    verify_run(run, "success", "success")
    verify_mlflow_run(run)
    print("outbox synchronized after MLflow recovery")


def worker_restart_create(state_path: Path) -> None:
    wait_ready()
    _, created = create("long_running_cancel", f"ar0:{uuid.uuid4().hex}:worker-restart")
    wait_running(created["runId"])
    write_state(state_path, {"runId": created["runId"]})
    print("worker restart fixture is running")


def worker_restart_recovery(state_path: Path) -> None:
    wait_ready()
    run_id = json.loads(state_path.read_text(encoding="utf-8"))["runId"]
    run = wait_run(run_id)
    if (
        run.get("outcome") != "infrastructure_error"
        or run.get("inspectStatus") != "started"
        or run.get("errorType") != "WorkerRestarted"
    ):
        raise AcceptanceFailure(f"worker restart was misclassified: {run}")
    verify_mlflow_run(run)
    print("worker restart failed closed as infrastructure_error")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "initial",
            "recovery",
            "outbox-create",
            "outbox-terminal",
            "outbox-recovery",
            "worker-restart-create",
            "worker-restart-recovery",
            "inspect-view-logs",
            "view-degraded",
            "required-not-ready",
        ],
    )
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    actions = {
        "initial": initial,
        "recovery": recovery,
        "outbox-create": outbox_create,
        "outbox-terminal": outbox_terminal,
        "outbox-recovery": outbox_recovery,
        "worker-restart-create": worker_restart_create,
        "worker-restart-recovery": worker_restart_recovery,
        "inspect-view-logs": inspect_view_logs,
        "view-degraded": view_degraded,
        "required-not-ready": required_not_ready,
    }
    actions[args.mode](args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
