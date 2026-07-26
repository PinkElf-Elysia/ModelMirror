from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class EvolutionError(RuntimeError):
    pass


class EvolutionNotFoundError(EvolutionError):
    pass


class EvolutionConflictError(EvolutionError):
    pass


class EvolutionStateError(EvolutionError):
    pass


class XpertEvolutionStore:
    """Atomic file-backed state for bounded Prompt evolution runs."""

    TERMINAL_STATUSES = {
        "completed",
        "no_improvement",
        "failed",
        "cancelled",
        "stale",
    }

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        root = Path(
            storage_dir
            or os.getenv("XPERT_EVOLUTION_STORAGE_DIR", "").strip()
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or Path(__file__).resolve().parent / "storage"
        )
        self.storage_dir = root
        self.path = root / "xpert_evolutions.json"
        self._lock = threading.RLock()
        self._data = self._load()

    def create_run(
        self,
        *,
        request: dict[str, Any],
        target: dict[str, Any],
        dataset: dict[str, Any],
        train_case_ids: list[str],
        validation_case_ids: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        now = time.time()
        run = {
            "run_id": f"xevo_run_{uuid.uuid4().hex}",
            "status": "queued",
            "phase": "baseline",
            "request": copy.deepcopy(request),
            "target": copy.deepcopy(target),
            "dataset": copy.deepcopy(dataset),
            "train_case_ids": list(train_case_ids),
            "validation_case_ids": list(validation_case_ids),
            "warnings": [str(item)[:500] for item in warnings[:50]],
            "baseline_evaluation_run_id": None,
            "generations": [],
            "validation_evaluation_run_id": None,
            "finalists": [],
            "report": {},
            "proposal_id": None,
            "proposal_revision": None,
            "stale": False,
            "cancel_requested": False,
            "run_registry_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        with self._lock:
            self._data["runs"][run["run_id"]] = run
            self._save_unlocked()
        return copy.deepcopy(run)

    def list_runs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._data["runs"].values())
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return [self.payload(item, include_detail=False) for item in items[:limit]]

    def require(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._require_unlocked(run_id))

    def claim_next(self) -> dict[str, Any] | None:
        with self._lock:
            items = [
                item
                for item in self._data["runs"].values()
                if item.get("status") == "queued"
            ]
            if not items:
                return None
            item = min(items, key=lambda value: float(value.get("created_at") or 0))
            item["status"] = "running"
            item["updated_at"] = time.time()
            self._save_unlocked()
            return copy.deepcopy(item)

    def recover(self) -> int:
        recovered = 0
        with self._lock:
            for item in self._data["runs"].values():
                if item.get("status") == "running":
                    item["status"] = "queued"
                    item["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._save_unlocked()
        return recovered

    def mutate(
        self,
        run_id: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._lock:
            item = self._require_unlocked(run_id)
            callback(item)
            item["updated_at"] = time.time()
            self._save_unlocked()
            return copy.deepcopy(item)

    def cancel(self, run_id: str) -> dict[str, Any]:
        def apply(item: dict[str, Any]) -> None:
            if item.get("status") in self.TERMINAL_STATUSES:
                return
            item["cancel_requested"] = True
            if item.get("status") == "queued":
                item["status"] = "cancelled"
                item["completed_at"] = time.time()

        return self.mutate(run_id, apply)

    def fail(self, run_id: str, error: str) -> dict[str, Any]:
        def apply(item: dict[str, Any]) -> None:
            item["status"] = "failed"
            item["error"] = str(error)[:500]
            item["completed_at"] = time.time()

        return self.mutate(run_id, apply)

    @staticmethod
    def payload(item: dict[str, Any], *, include_detail: bool) -> dict[str, Any]:
        payload = copy.deepcopy(item)
        payload["generation_count"] = len(payload.get("generations") or [])
        payload["candidate_count"] = sum(
            len(generation.get("candidates") or [])
            for generation in payload.get("generations") or []
        )
        if not include_detail:
            payload.pop("dataset", None)
            target = dict(payload.get("target") or {})
            target.pop("baseline_xpert", None)
            target.pop("baseline_snapshot", None)
            target.pop("baseline_prompts", None)
            payload["target"] = target
            for generation in payload.get("generations") or []:
                for candidate in generation.get("candidates") or []:
                    candidate.pop("snapshot", None)
                    candidate.pop("xpert", None)
        return payload

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "runs": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "runs": {}}
        return {"schema_version": 1, "runs": dict(raw.get("runs") or {})}

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def _require_unlocked(self, run_id: str) -> dict[str, Any]:
        item = self._data["runs"].get(run_id)
        if not isinstance(item, dict):
            raise EvolutionNotFoundError("Evolution run not found.")
        return item
