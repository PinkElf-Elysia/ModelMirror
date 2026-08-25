from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .config import Settings
from .evidence import (
    EvidenceError,
    build_receipt,
    read_verified_artifact,
    verify_persisted_receipt,
)
from .mlflow_sink import MlflowSink
from .store import IdempotencyConflict, RunStore
from .worker_client import WorkerBusy, WorkerClient, WorkerClientError


class NotReady(RuntimeError):
    pass


class ResearchService:
    def __init__(self, settings: Settings) -> None:
        settings.prepare()
        self.settings = settings
        self.store = RunStore(settings.control_db)
        self.worker = WorkerClient(settings.worker_socket)
        self.mlflow = MlflowSink(settings.mlflow_uri, settings.mlflow_experiment)
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run_loop(), name="ai-research-control-loop")

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task is not None:
            await self._loop_task
            self._loop_task = None

    async def readiness(self) -> dict[str, str]:
        checks: dict[str, str] = {}
        try:
            await asyncio.to_thread(self.store.probe)
            checks["controlLedger"] = "ready"
        except Exception:
            checks["controlLedger"] = "not_ready"
        try:
            health = await self.worker.health()
            checks["worker"] = "ready" if health.get("status") == "ready" else "not_ready"
        except WorkerClientError:
            checks["worker"] = "not_ready"
        try:
            await asyncio.to_thread(self.mlflow.probe)
            checks["tracking"] = "ready"
        except Exception:
            checks["tracking"] = "not_ready"
        return checks

    async def system_status(self) -> dict[str, Any]:
        required = await self.readiness()
        try:
            await asyncio.to_thread(self._probe_inspect_view)
            inspect_status = "ready"
        except Exception:
            inspect_status = "not_ready"
        checks = [
            {"id": check_id, "status": status, "required": True}
            for check_id, status in required.items()
        ]
        checks.append(
            {"id": "inspectView", "status": inspect_status, "required": False}
        )
        if any(item["required"] and item["status"] != "ready" for item in checks):
            status = "not_ready"
        elif inspect_status != "ready":
            status = "degraded"
        else:
            status = "ready"
        return {
            "status": status,
            "checks": checks,
            "checkedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def _probe_inspect_view(self) -> None:
        request = Request(self.settings.inspect_view_uri + "/", method="GET")
        with urlopen(request, timeout=2.0) as response:
            if response.status < 200 or response.status >= 400:
                raise RuntimeError("Inspect View returned an unhealthy status")

    async def evidence(self, run_id: str) -> dict[str, Any]:
        record = await asyncio.to_thread(self.store.evidence, run_id)
        receipt = record.get("receipt_json")
        verified_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        integrity_status = "pending"
        integrity_error: str | None = None
        artifacts: list[dict[str, Any]] = []
        if isinstance(receipt, dict):
            destination = (self.settings.evidence_root / run_id).resolve()
            try:
                await asyncio.to_thread(verify_persisted_receipt, destination, receipt)
                integrity_status = "verified"
                artifacts = [
                    {
                        "name": name,
                        "sizeBytes": descriptor["sizeBytes"],
                        "sha256": descriptor["sha256"],
                        "downloadUrl": f"/api/v1/runs/{run_id}/artifacts/{name}",
                    }
                    for name, descriptor in sorted((receipt.get("artifacts") or {}).items())
                ]
            except (EvidenceError, OSError, ValueError) as exc:
                integrity_status = "failed"
                integrity_error = f"{type(exc).__name__}: {str(exc)[:400]}"
        return {
            "runId": run_id,
            "evidenceState": record["evidence_state"],
            "integrityStatus": integrity_status,
            "integrityError": integrity_error,
            "verifiedAt": verified_at,
            "receipt": receipt if isinstance(receipt, dict) else None,
            "artifacts": artifacts,
            "mlflow": (receipt or {}).get("mlflow", {}) if isinstance(receipt, dict) else {},
            "outbox": (
                {
                    "state": record["outbox_state"],
                    "attemptCount": record["attempt_count"],
                    "nextAttemptAt": record["next_attempt_at"],
                    "lastError": record["last_error"],
                }
                if record.get("outbox_state") is not None
                else None
            ),
        }

    async def artifact(self, run_id: str, name: str) -> tuple[bytes, str]:
        record = await asyncio.to_thread(self.store.evidence, run_id)
        receipt = record.get("receipt_json")
        if not isinstance(receipt, dict):
            raise EvidenceError("run receipt is not available")
        destination = (self.settings.evidence_root / run_id).resolve()
        return await asyncio.to_thread(read_verified_artifact, destination, receipt, name)

    async def create_run(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        checks = await self.readiness()
        if any(value != "ready" for value in checks.values()):
            raise NotReady("module dependencies are not ready")
        return await asyncio.to_thread(self.store.create_or_get, request)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = await asyncio.to_thread(self.store.get, run_id)
        if run["phase"] == "terminal":
            return run
        run = await asyncio.to_thread(self.store.request_cancel, run_id)
        # The run may have reached a terminal state between the first read and
        # the atomic cancel transaction. Never send a stale cancel to Worker.
        if run["phase"] == "terminal":
            return run
        if run["phase"] == "queued":
            worker_result = {
                "runId": run_id,
                "caseId": run["case_id"],
                "phase": "terminal",
                "outcome": "cancelled",
                "inspectStatus": None,
                "cancelRequested": True,
                "cancelApplied": False,
                "errorType": None,
                "errorMessage": None,
                "replayVerified": False,
                "artifacts": {},
            }
            terminal = await asyncio.to_thread(self.store.update_worker, run_id, worker_result)
            await self._ensure_receipt(terminal, worker_result)
            return terminal
        try:
            worker_result = await self.worker.cancel(run_id)
            return await asyncio.to_thread(self.store.update_worker, run_id, worker_result)
        except WorkerClientError:
            return run

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                print(f"ai-research control loop error: {type(exc).__name__}: {str(exc)[:200]}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_seconds)
            except TimeoutError:
                pass

    async def tick(self) -> None:
        active = await asyncio.to_thread(self.store.active)
        for run in active:
            try:
                if run["cancel_requested"]:
                    await self.worker.cancel(run["run_id"])
                worker_result = await self.worker.status(run["run_id"])
            except WorkerClientError:
                continue
            updated = await asyncio.to_thread(
                self.store.update_worker, run["run_id"], worker_result
            )
            if updated["phase"] == "terminal":
                await self._ensure_receipt(updated, worker_result)

        if not active:
            queued = await asyncio.to_thread(self.store.queued)
            if queued:
                run = queued[0]
                if run["cancel_requested"]:
                    await self.cancel(run["run_id"])
                else:
                    try:
                        worker_result = await self.worker.start(run["run_id"], run["case_id"])
                    except (WorkerBusy, WorkerClientError):
                        worker_result = None
                    if worker_result is not None:
                        await asyncio.to_thread(
                            self.store.mark_running, run["run_id"], worker_result
                        )

        await self._sync_outbox()

    async def _ensure_receipt(
        self, run: dict[str, Any], worker_result: dict[str, Any]
    ) -> None:
        current = await asyncio.to_thread(self.store.get, run["run_id"])
        if current.get("receipt_json") is not None:
            return
        receipt, _ = await asyncio.to_thread(
            build_receipt, self.settings, current, worker_result
        )
        await asyncio.to_thread(self.store.set_receipt, run["run_id"], receipt)

    async def _sync_outbox(self) -> None:
        pending = await asyncio.to_thread(self.store.pending_outbox)
        for run in pending:
            receipt = run.get("receipt_json")
            if not isinstance(receipt, dict):
                continue
            evidence_dir = (self.settings.evidence_root / run["run_id"]).resolve()
            try:
                mlflow_run_id, final_receipt = await asyncio.to_thread(
                    self.mlflow.sync, run, receipt, evidence_dir
                )
                await asyncio.to_thread(
                    self.store.mark_evidence_synced,
                    run["run_id"],
                    mlflow_run_id,
                    final_receipt,
                )
            except Exception as exc:
                await asyncio.to_thread(
                    self.store.mark_evidence_failed,
                    run["run_id"],
                    f"{type(exc).__name__}: {str(exc)[:800]}",
                )
