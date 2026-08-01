from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class EvaluationError(RuntimeError):
    pass


class EvaluationNotFoundError(EvaluationError):
    pass


class EvaluationConflictError(EvaluationError):
    pass


class EvaluationStateError(EvaluationError):
    pass


class XpertEvaluationStore:
    """Atomic file-backed datasets, immutable versions, and resumable runs."""

    MAX_DATASET_CASES = 500
    MAX_RUN_CASES = 100

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        root = Path(
            storage_dir
            or os.getenv("XPERT_EVALUATION_STORAGE_DIR", "").strip()
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or Path(__file__).resolve().parent / "storage"
        )
        self.storage_dir = root
        self.path = root / "xpert_evaluations.json"
        self._lock = threading.RLock()
        self._data = self._load()

    def create_dataset(self, name: str, description: str = "") -> dict[str, Any]:
        now = time.time()
        dataset = {
            "dataset_id": f"xeval_dataset_{uuid.uuid4().hex}",
            "name": self._required(name, "name", 160),
            "description": str(description or "").strip()[:2_000],
            "status": "draft",
            "revision": 1,
            "published_version": None,
            "cases": [],
            "versions": [],
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._data["datasets"][dataset["dataset_id"]] = dataset
            self._save_unlocked()
        return copy.deepcopy(dataset)

    def list_datasets(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._data["datasets"].values())
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
        return [self.dataset_payload(item, include_cases=False) for item in items]

    def require_dataset(self, dataset_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._data["datasets"].get(dataset_id)
            if not isinstance(item, dict):
                raise EvaluationNotFoundError("Evaluation dataset not found.")
            return copy.deepcopy(item)

    def update_dataset(
        self,
        dataset_id: str,
        *,
        revision: int,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            item = self._dataset_unlocked(dataset_id)
            self._check_revision(item, revision)
            if name is not None:
                item["name"] = self._required(name, "name", 160)
            if description is not None:
                item["description"] = str(description).strip()[:2_000]
            if status is not None:
                if status not in {"draft", "archived"}:
                    raise EvaluationStateError("Dataset status must be draft or archived.")
                item["status"] = status
            self._touch(item)
            self._save_unlocked()
            return copy.deepcopy(item)

    def put_cases(
        self,
        dataset_id: str,
        *,
        revision: int,
        cases: list[dict[str, Any]],
        replace: bool = False,
    ) -> dict[str, Any]:
        normalized = [self.normalize_case(item) for item in cases]
        with self._lock:
            item = self._dataset_unlocked(dataset_id)
            self._check_revision(item, revision)
            if item.get("status") == "archived":
                raise EvaluationStateError("Archived datasets cannot be edited.")
            existing = [] if replace else list(item.get("cases") or [])
            by_id = {str(case["case_id"]): case for case in existing}
            for case in normalized:
                by_id[str(case["case_id"])] = case
            if len(by_id) > self.MAX_DATASET_CASES:
                raise EvaluationStateError("A dataset may contain at most 500 cases.")
            item["cases"] = list(by_id.values())
            self._touch(item)
            self._save_unlocked()
            return copy.deepcopy(item)

    def publish_dataset(
        self,
        dataset_id: str,
        *,
        revision: int,
        release_notes: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            item = self._dataset_unlocked(dataset_id)
            self._check_revision(item, revision)
            if not item.get("cases"):
                raise EvaluationStateError("Dataset must contain at least one case.")
            version_number = len(item.get("versions") or []) + 1
            cases = copy.deepcopy(item["cases"])
            version = {
                "dataset_id": dataset_id,
                "version": version_number,
                "draft_revision": int(item["revision"]),
                "name": item["name"],
                "description": item.get("description") or "",
                "cases": cases,
                "case_count": len(cases),
                "release_notes": str(release_notes or "").strip()[:2_000],
                "checksum": self._checksum(cases),
                "published_at": time.time(),
            }
            item.setdefault("versions", []).append(version)
            item["published_version"] = version_number
            self._touch(item)
            self._save_unlocked()
            return copy.deepcopy(version)

    def list_dataset_versions(self, dataset_id: str) -> list[dict[str, Any]]:
        item = self.require_dataset(dataset_id)
        versions = list(item.get("versions") or [])
        versions.sort(key=lambda version: int(version.get("version") or 0), reverse=True)
        return [
            {key: value for key, value in version.items() if key != "cases"}
            for version in versions
        ]

    def get_dataset_version(self, dataset_id: str, version: int) -> dict[str, Any]:
        item = self.require_dataset(dataset_id)
        for snapshot in item.get("versions") or []:
            if int(snapshot.get("version") or 0) == int(version):
                return copy.deepcopy(snapshot)
        raise EvaluationNotFoundError("Evaluation dataset version not found.")

    def create_run(
        self,
        *,
        dataset_version: dict[str, Any],
        cases: list[dict[str, Any]],
        baseline: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        config: dict[str, Any],
        warnings: list[str],
    ) -> dict[str, Any]:
        if not 1 <= len(cases) <= self.MAX_RUN_CASES:
            raise EvaluationStateError("A run must contain between 1 and 100 cases.")
        now = time.time()
        targets = ([baseline] if baseline else []) + list(candidates)
        items: list[dict[str, Any]] = []
        repetitions = int((config.get("budget") or {}).get("repetitions") or 1)
        for target in targets:
            for case in cases:
                for repetition in range(1, repetitions + 1):
                    items.append(
                        {
                            "item_id": f"xeval_item_{uuid.uuid4().hex}",
                            "target_id": target["target_id"],
                            "target_label": target["label"],
                            "case_id": case["case_id"],
                            "repetition": repetition,
                            "status": "pending",
                            "attempts": 0,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
        run = {
            "run_id": f"xeval_run_{uuid.uuid4().hex}",
            "status": "queued",
            "dataset": copy.deepcopy(dataset_version),
            "selected_case_ids": [case["case_id"] for case in cases],
            "baseline_target_id": baseline["target_id"] if baseline else None,
            "targets": copy.deepcopy(targets),
            "config": copy.deepcopy(config),
            "warnings": [str(item)[:500] for item in warnings[:50]],
            "items": items,
            "report": {},
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
            runs = list(self._data["runs"].values())
        if status:
            runs = [item for item in runs if item.get("status") == status]
        runs.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return [self.run_payload(item, include_detail=False) for item in runs[:limit]]

    def require_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._data["runs"].get(run_id)
            if not isinstance(item, dict):
                raise EvaluationNotFoundError("Evaluation run not found.")
            return copy.deepcopy(item)

    def claim_next_run(self) -> dict[str, Any] | None:
        with self._lock:
            queued = [
                run
                for run in self._data["runs"].values()
                if run.get("status") == "queued"
            ]
            if not queued:
                return None
            run = min(queued, key=lambda item: float(item.get("created_at") or 0))
            run["status"] = "running"
            run["updated_at"] = time.time()
            self._save_unlocked()
            return copy.deepcopy(run)

    def recover_runs(self) -> int:
        recovered = 0
        with self._lock:
            for run in self._data["runs"].values():
                if run.get("status") == "running":
                    run["status"] = "queued"
                    run["updated_at"] = time.time()
                    for item in run.get("items") or []:
                        if item.get("status") == "running":
                            item["status"] = "pending"
                            item["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._save_unlocked()
        return recovered

    def set_run_registry_id(self, run_id: str, registry_id: str) -> None:
        with self._lock:
            run = self._run_unlocked(run_id)
            run["run_registry_id"] = registry_id
            run["updated_at"] = time.time()
            self._save_unlocked()

    def claim_items(self, run_id: str, limit: int) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        with self._lock:
            run = self._run_unlocked(run_id)
            if run.get("cancel_requested"):
                return []
            for item in run.get("items") or []:
                if item.get("status") != "pending":
                    continue
                item["status"] = "running"
                item["attempts"] = int(item.get("attempts") or 0) + 1
                item["updated_at"] = time.time()
                claimed.append(copy.deepcopy(item))
                if len(claimed) >= max(1, limit):
                    break
            if claimed:
                run["updated_at"] = time.time()
                self._save_unlocked()
        return claimed

    def record_item_result(
        self,
        run_id: str,
        item_id: str,
        *,
        result: dict[str, Any],
    ) -> None:
        with self._lock:
            run = self._run_unlocked(run_id)
            item = self._item_unlocked(run, item_id)
            if item.get("status") not in {"running", "pending"}:
                return
            item.update(copy.deepcopy(result))
            item["status"] = str(result.get("status") or "completed")
            item["updated_at"] = time.time()
            run["updated_at"] = time.time()
            self._save_unlocked()

    def complete_run(self, run_id: str, report: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            run = self._run_unlocked(run_id)
            if run.get("cancel_requested"):
                run["status"] = "cancelled"
            else:
                run["status"] = "completed"
            run["report"] = copy.deepcopy(report)
            run["completed_at"] = time.time()
            run["updated_at"] = run["completed_at"]
            self._save_unlocked()
            return copy.deepcopy(run)

    def fail_run(self, run_id: str, error: str) -> None:
        with self._lock:
            run = self._run_unlocked(run_id)
            run["status"] = "failed"
            run["error"] = str(error)[:500]
            run["completed_at"] = time.time()
            run["updated_at"] = run["completed_at"]
            self._save_unlocked()

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._run_unlocked(run_id)
            if run.get("status") in {"completed", "failed", "cancelled"}:
                return copy.deepcopy(run)
            run["cancel_requested"] = True
            if run.get("status") == "queued":
                run["status"] = "cancelled"
                run["completed_at"] = time.time()
            for item in run.get("items") or []:
                if item.get("status") == "pending":
                    item["status"] = "cancelled"
            run["updated_at"] = time.time()
            self._save_unlocked()
            return copy.deepcopy(run)

    @staticmethod
    def dataset_payload(item: dict[str, Any], *, include_cases: bool) -> dict[str, Any]:
        payload = copy.deepcopy(item)
        payload["case_count"] = len(payload.get("cases") or [])
        payload["version_count"] = len(payload.get("versions") or [])
        payload.pop("versions", None)
        if not include_cases:
            payload.pop("cases", None)
        return payload

    @staticmethod
    def run_payload(item: dict[str, Any], *, include_detail: bool) -> dict[str, Any]:
        payload = copy.deepcopy(item)
        payload["item_count"] = len(payload.get("items") or [])
        payload["completed_item_count"] = sum(
            1
            for result in payload.get("items") or []
            if result.get("status") in {"completed", "failed", "cancelled"}
        )
        if not include_detail:
            dataset = dict(payload.get("dataset") or {})
            dataset.pop("cases", None)
            payload["dataset"] = dataset
            for target in payload.get("targets") or []:
                target.pop("workflow", None)
                target.pop("xpert", None)
            payload.pop("items", None)
        return payload

    @staticmethod
    def normalize_case(raw: dict[str, Any]) -> dict[str, Any]:
        case = copy.deepcopy(raw)
        case_id = str(case.get("case_id") or "").strip() or f"case_{uuid.uuid4().hex}"
        message = str(case.get("message") or "").strip()
        if not message:
            raise EvaluationStateError("Evaluation case message is required.")
        messages = []
        total_history = 0
        for item in list(case.get("messages") or [])[-20:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"system", "user", "assistant"} or not content:
                continue
            remaining = 40_000 - total_history
            if remaining <= 0:
                break
            content = content[:remaining]
            total_history += len(content)
            messages.append({"role": role, "content": content})
        return {
            "case_id": case_id[:120],
            "name": str(case.get("name") or message[:80]).strip()[:160],
            "message": message[:20_000],
            "messages": messages,
            "tags": [
                str(item).strip()[:80]
                for item in list(case.get("tags") or [])[:20]
                if str(item).strip()
            ],
            "expected": copy.deepcopy(case.get("expected") or {}),
            "weights": copy.deepcopy(case.get("weights") or {}),
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "datasets": {}, "runs": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "datasets": {}, "runs": {}}
        return {
            "schema_version": 1,
            "datasets": dict(raw.get("datasets") or {}),
            "runs": dict(raw.get("runs") or {}),
        }

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def _dataset_unlocked(self, dataset_id: str) -> dict[str, Any]:
        item = self._data["datasets"].get(dataset_id)
        if not isinstance(item, dict):
            raise EvaluationNotFoundError("Evaluation dataset not found.")
        return item

    def _run_unlocked(self, run_id: str) -> dict[str, Any]:
        item = self._data["runs"].get(run_id)
        if not isinstance(item, dict):
            raise EvaluationNotFoundError("Evaluation run not found.")
        return item

    @staticmethod
    def _item_unlocked(run: dict[str, Any], item_id: str) -> dict[str, Any]:
        for item in run.get("items") or []:
            if item.get("item_id") == item_id:
                return item
        raise EvaluationNotFoundError("Evaluation work item not found.")

    @staticmethod
    def _check_revision(item: dict[str, Any], revision: int) -> None:
        if int(item.get("revision") or 0) != int(revision):
            raise EvaluationConflictError("Resource changed. Reload before saving.")

    @staticmethod
    def _touch(item: dict[str, Any]) -> None:
        item["revision"] = int(item.get("revision") or 0) + 1
        item["updated_at"] = time.time()

    @staticmethod
    def _required(value: Any, name: str, limit: int) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise EvaluationStateError(f"{name} is required.")
        return clean[:limit]

    @staticmethod
    def _checksum(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
