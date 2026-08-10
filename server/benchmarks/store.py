from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class BenchmarkJobError(RuntimeError):
    pass


class BenchmarkJobNotFoundError(BenchmarkJobError):
    pass


class BenchmarkJobStateError(BenchmarkJobError):
    pass


class BenchmarkJobStore:
    """Atomic task index for generated benchmarks and calibration runs."""

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
    ACTIVE_STATUSES = {
        "queued",
        "generating",
        "validating",
        "calibrating",
    }

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        root = Path(
            storage_dir
            or os.getenv("BENCHMARK_STORAGE_DIR", "").strip()
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or Path(__file__).resolve().parent / "storage"
        )
        self.storage_dir = root
        self.path = root / "benchmark_jobs.json"
        self._lock = threading.RLock()
        self._data = self._load()

    def create_job(self, *, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"generation", "calibration", "knowledge_instantiation"}:
            raise BenchmarkJobStateError("Unsupported Benchmark job kind.")
        now = time.time()
        job = {
            "job_id": f"benchmark_job_{uuid.uuid4().hex}",
            "kind": kind,
            "status": "queued",
            "request": copy.deepcopy(request),
            "target": {},
            "coverage": {},
            "generation": {},
            "generation_attempts": [],
            "dataset_id": None,
            "dataset_revision": None,
            "evaluation_run_id": None,
            "calibration_runtime": None,
            "calibration": {},
            "provisioning": {},
            "warnings": [],
            "error": None,
            "cancel_requested": False,
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        with self._lock:
            self._data["jobs"][job["job_id"]] = job
            self._save_unlocked()
        return copy.deepcopy(job)

    def list_jobs(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._data["jobs"].values())
        if kind:
            items = [item for item in items if item.get("kind") == kind]
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return [copy.deepcopy(item) for item in items[: max(1, min(limit, 200))]]

    def require_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._require_unlocked(job_id))

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._lock:
            queued = [
                item
                for item in self._data["jobs"].values()
                if item.get("status") == "queued"
                and not item.get("cancel_requested")
            ]
            if not queued:
                return None
            item = min(queued, key=lambda value: float(value.get("created_at") or 0))
            item["status"] = (
                "validating" if item.get("kind") == "calibration" else "generating"
            )
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["updated_at"] = time.time()
            self._save_unlocked()
            return copy.deepcopy(item)

    def update_job(self, job_id: str, **patch: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "target",
            "coverage",
            "generation",
            "generation_attempts",
            "dataset_id",
            "dataset_revision",
            "evaluation_run_id",
            "calibration_runtime",
            "calibration",
            "provisioning",
            "warnings",
            "error",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise BenchmarkJobStateError(
                f"Unsupported Benchmark job fields: {', '.join(sorted(unknown))}"
            )
        with self._lock:
            item = self._require_unlocked(job_id)
            for key, value in patch.items():
                item[key] = copy.deepcopy(value)
            item["updated_at"] = time.time()
            if item.get("status") in self.TERMINAL_STATUSES:
                item["completed_at"] = item["updated_at"]
            self._save_unlocked()
            return copy.deepcopy(item)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._require_unlocked(job_id)
            if item.get("status") in self.TERMINAL_STATUSES:
                return copy.deepcopy(item)
            item["cancel_requested"] = True
            if item.get("status") == "queued":
                item["status"] = "cancelled"
                item["completed_at"] = time.time()
            item["updated_at"] = time.time()
            self._save_unlocked()
            return copy.deepcopy(item)

    def recover_jobs(self) -> int:
        recovered = 0
        with self._lock:
            for item in self._data["jobs"].values():
                if item.get("status") in {"generating", "validating"}:
                    item["status"] = "queued"
                    item["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._save_unlocked()
        return recovered

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "jobs": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "jobs": {}}
        return {"schema_version": 1, "jobs": dict(raw.get("jobs") or {})}

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def _require_unlocked(self, job_id: str) -> dict[str, Any]:
        item = self._data["jobs"].get(job_id)
        if not isinstance(item, dict):
            raise BenchmarkJobNotFoundError("Benchmark job not found.")
        return item
