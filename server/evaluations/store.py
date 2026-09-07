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

from .models import AgentTableQueryFixture

try:
    from server.multimodal.vision_v2 import evaluation_vision_usage
except ModuleNotFoundError:
    from multimodal.vision_v2 import evaluation_vision_usage


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
    MAX_RESOURCE_FIXTURES = 1_000
    MAX_RESOURCE_FIXTURE_BYTES = 16 * 1024 * 1024

    def __init__(self, storage_dir: str | Path | None = None, *, vision_fixtures: Any | None = None) -> None:
        root = Path(
            storage_dir
            or os.getenv("XPERT_EVALUATION_STORAGE_DIR", "").strip()
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or Path(__file__).resolve().parent / "storage"
        )
        self.storage_dir = root
        self.path = root / "xpert_evaluations.json"
        self._lock = threading.RLock()
        self.vision_fixtures = vision_fixtures
        self._data = self._load()

    def create_dataset(
        self,
        name: str,
        description: str = "",
        *,
        origin: str = "manual",
        catalog_ref: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        coverage: dict[str, Any] | None = None,
        calibration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
            "origin": str(origin or "manual")[:80],
            "catalog_ref": copy.deepcopy(catalog_ref or {}),
            "provenance": copy.deepcopy(provenance or {}),
            "coverage": copy.deepcopy(coverage or {}),
            "calibration": copy.deepcopy(
                calibration or {"status": "pending", "updated_at": None}
            ),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._data["datasets"][dataset["dataset_id"]] = dataset
            self._save_unlocked()
        return copy.deepcopy(dataset)

    def instantiate_catalog_dataset(
        self,
        *,
        name: str,
        description: str,
        cases: list[dict[str, Any]],
        catalog_ref: dict[str, Any],
        provenance: dict[str, Any],
        coverage: dict[str, Any],
        release_notes: str,
    ) -> dict[str, Any]:
        normalized = [self.normalize_case(item) for item in cases]
        if not normalized:
            raise EvaluationStateError("Benchmark pack must contain at least one case.")
        if len(normalized) > self.MAX_DATASET_CASES:
            raise EvaluationStateError("A dataset may contain at most 500 cases.")
        case_ids = [str(item["case_id"]) for item in normalized]
        if len(case_ids) != len(set(case_ids)):
            raise EvaluationStateError("Benchmark case ids must be unique.")

        now = time.time()
        dataset_id = f"xeval_dataset_{uuid.uuid4().hex}"
        dataset = {
            "dataset_id": dataset_id,
            "name": self._required(name, "name", 160),
            "description": str(description or "").strip()[:2_000],
            "status": "draft",
            "revision": 1,
            "published_version": 1,
            "cases": copy.deepcopy(normalized),
            "versions": [],
            "origin": "catalog",
            "catalog_ref": copy.deepcopy(catalog_ref),
            "provenance": copy.deepcopy(provenance),
            "coverage": copy.deepcopy(coverage),
            "calibration": {
                "status": "calibrated",
                "mode": "catalog_integrity",
                "checksum": str(catalog_ref.get("checksum") or "")[:64],
                "updated_at": now,
            },
            "created_at": now,
            "updated_at": now,
        }
        version = {
            "dataset_id": dataset_id,
            "version": 1,
            "draft_revision": 1,
            "name": dataset["name"],
            "description": dataset["description"],
            "cases": copy.deepcopy(normalized),
            "case_count": len(normalized),
            "release_notes": str(release_notes or "").strip()[:2_000],
            "checksum": self._checksum(normalized),
            **self._metadata_payload(dataset),
            "published_at": now,
        }
        dataset["versions"] = [version]
        self._touch(dataset)
        with self._lock:
            self._data["datasets"][dataset_id] = dataset
            self._save_unlocked()
        return self.dataset_payload(dataset, include_cases=True)

    def create_generated_dataset(
        self,
        *,
        name: str,
        description: str,
        cases: list[dict[str, Any]],
        provenance: dict[str, Any],
        coverage: dict[str, Any],
        calibration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = [self.normalize_case(item) for item in cases]
        if not normalized:
            raise EvaluationStateError("Generated dataset must contain at least one case.")
        if len(normalized) > self.MAX_DATASET_CASES:
            raise EvaluationStateError("A dataset may contain at most 500 cases.")
        case_ids = [str(item["case_id"]) for item in normalized]
        if len(case_ids) != len(set(case_ids)):
            raise EvaluationStateError("Generated case ids must be unique.")
        now = time.time()
        dataset = {
            "dataset_id": f"xeval_dataset_{uuid.uuid4().hex}",
            "name": self._required(name, "name", 160),
            "description": str(description or "").strip()[:2_000],
            "status": "draft",
            "revision": 1,
            "published_version": None,
            "cases": copy.deepcopy(normalized),
            "versions": [],
            "origin": "generated",
            "catalog_ref": {},
            "provenance": copy.deepcopy(provenance),
            "coverage": copy.deepcopy(coverage),
            "calibration": copy.deepcopy(
                calibration
                or {
                    "status": "pending",
                    "dataset_revision": 1,
                    "updated_at": now,
                }
            ),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._data["datasets"][dataset["dataset_id"]] = dataset
            self._save_unlocked()
        return self.dataset_payload(dataset, include_cases=True)

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
            for case in normalized:
                if case.get("attachment"):
                    if self.vision_fixtures is None:
                        raise EvaluationStateError("评测附件存储尚未配置。")
                    case["attachment"] = self.vision_fixtures.describe_draft_asset(
                        dataset_id, case["attachment"]["asset_id"]
                    )
            existing = [] if replace else list(item.get("cases") or [])
            by_id = {str(case["case_id"]): case for case in existing}
            for case in normalized:
                by_id[str(case["case_id"])] = case
            if len(by_id) > self.MAX_DATASET_CASES:
                raise EvaluationStateError("A dataset may contain at most 500 cases.")
            item["cases"] = list(by_id.values())
            calibration = dict(item.get("calibration") or {})
            if calibration.get("status") in {
                "pending",
                "calibrated",
                "warning",
                "failed",
            }:
                calibration.update(
                    {
                        "status": "stale",
                        "reason": "dataset_cases_changed",
                        "updated_at": time.time(),
                    }
                )
                item["calibration"] = calibration
            self._touch(item)
            self._save_unlocked()
            return copy.deepcopy(item)

    def set_dataset_calibration(
        self,
        dataset_id: str,
        *,
        revision: int,
        calibration: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(calibration.get("status") or "")
        if status not in {"pending", "calibrated", "warning", "failed", "stale"}:
            raise EvaluationStateError("Invalid calibration status.")
        with self._lock:
            item = self._dataset_unlocked(dataset_id)
            self._check_revision(item, revision)
            payload = copy.deepcopy(calibration)
            payload["status"] = status
            payload["dataset_revision"] = revision
            payload["updated_at"] = time.time()
            item["calibration"] = payload
            item["updated_at"] = payload["updated_at"]
            self._save_unlocked()
            return self.dataset_payload(item, include_cases=True)

    def publish_dataset(
        self,
        dataset_id: str,
        *,
        revision: int,
        release_notes: str = "",
        acknowledge_calibration_warnings: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            item = self._dataset_unlocked(dataset_id)
            self._check_revision(item, revision)
            if not item.get("cases"):
                raise EvaluationStateError("Dataset must contain at least one case.")
            if str(item.get("origin") or "manual") == "generated":
                calibration = dict(item.get("calibration") or {})
                calibration_status = str(calibration.get("status") or "pending")
                if int(calibration.get("dataset_revision") or 0) != int(revision):
                    raise EvaluationStateError(
                        "Generated dataset calibration is stale for this revision."
                    )
                if calibration_status == "warning":
                    if not acknowledge_calibration_warnings:
                        raise EvaluationStateError(
                            "Calibration warnings must be acknowledged before publishing."
                        )
                elif calibration_status != "calibrated":
                    raise EvaluationStateError(
                        "Generated dataset must complete calibration before publishing."
                    )
            version_number = len(item.get("versions") or []) + 1
            cases = copy.deepcopy(item["cases"])
            attachments = [case["attachment"] for case in cases if case.get("attachment")]
            if any(case.get("vision") and not case.get("attachment") for case in cases):
                raise EvaluationStateError("包含视觉断言的用例必须先选择附件。")
            if attachments:
                if self.vision_fixtures is None:
                    raise EvaluationStateError("评测附件存储尚未配置。")
                fixed = self.vision_fixtures.retain_dataset_version(dataset_id, version_number, attachments)
                fixed_by_id = {entry["asset_id"]: entry for entry in fixed}
                for case in cases:
                    if case.get("attachment"):
                        case["attachment"] = copy.deepcopy(fixed_by_id[case["attachment"]["asset_id"]])
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
                **self._metadata_payload(item),
                "published_at": time.time(),
            }
            before = copy.deepcopy(item)
            try:
                item.setdefault("versions", []).append(version)
                item["published_version"] = version_number
                self._touch(item)
                self._save_unlocked()
            except Exception:
                self._data["datasets"][dataset_id] = before
                if attachments:
                    self.vision_fixtures.release_dataset_version(dataset_id, version_number)
                raise
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
                payload = copy.deepcopy(snapshot)
                self._ensure_metadata(payload, fallback=item)
                return payload
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
        resource_fixtures: list[dict[str, Any]] | None = None,
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
        private_fixtures, fixture_bytes = self._validate_resource_fixtures(
            copy.deepcopy(resource_fixtures or []),
            targets=targets,
            cases=cases,
        )
        run_id = f"xeval_run_{uuid.uuid4().hex}"
        attachment_cases = [case for case in cases if case.get("attachment")]
        vision_fixtures: dict[str, Any] = {}
        if attachment_cases:
            if self.vision_fixtures is None:
                raise EvaluationStateError("评测附件存储尚未配置。")
            authoritative = self.get_dataset_version(dataset_version["dataset_id"], dataset_version["version"])
            if authoritative["checksum"] != dataset_version.get("checksum"):
                raise EvaluationStateError("固定评测版本摘要不一致。")
            version_cases = {case["case_id"]: case for case in authoritative["cases"]}
            for case in attachment_cases:
                if case.get("attachment") != version_cases.get(case["case_id"], {}).get("attachment"):
                    raise EvaluationStateError("用例附件与已发布版本不一致。")
            pinned = self.vision_fixtures.pin_run(run_id, dataset_version["dataset_id"], dataset_version["version"], [case["attachment"] for case in attachment_cases])
            by_asset = {entry["asset_id"]: entry for entry in pinned}
            vision_fixtures = {case["case_id"]: by_asset[case["attachment"]["asset_id"]] for case in attachment_cases}
        run = {
            "run_id": run_id,
            "status": "queued",
            "dataset": copy.deepcopy(dataset_version),
            "selected_case_ids": [case["case_id"] for case in cases],
            "baseline_target_id": baseline["target_id"] if baseline else None,
            "targets": copy.deepcopy(targets),
            "config": copy.deepcopy(config),
            "warnings": [str(item)[:500] for item in warnings[:50]],
            "items": items,
            "_resource_fixtures": private_fixtures,
            "_vision_fixtures": vision_fixtures,
            "vision_fixture_summary": {"case_count": len(vision_fixtures), "original_count": len({entry["sha256"] for entry in vision_fixtures.values()})},
            "resource_fixture_summary": {
                "fixture_count": len(private_fixtures),
                "stored_bytes": fixture_bytes,
            },
            "report": {},
            "cancel_requested": False,
            "run_registry_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        with self._lock:
            try:
                self._data["runs"][run["run_id"]] = run
                self._save_unlocked()
            except Exception:
                self._data["runs"].pop(run_id, None)
                if vision_fixtures:
                    self.vision_fixtures.release_run(run_id)
                raise
        return self.run_payload(run, include_detail=True)

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
                            if item.get("vision_dispatches"):
                                item.update({"status": "failed", "score": 0.0, "metrics": [], "vision_evidence": "failed", "error": "视觉请求已派发但结果未持久化，禁止自动重发。", "error_code": "EVALUATION_VISION_DISPATCH_UNCERTAIN"})
                                usage = evaluation_vision_usage(
                                    list((item.get("vision_receipts") or {}).values()),
                                    dispatch_count=len(item["vision_dispatches"]),
                                )
                                item["usage"] = {**dict(item.get("usage") or {}), **usage}
                                item["usage"]["model_calls"] = max(int(item["usage"].get("model_calls") or 0), usage["vision_model_calls"])
                            else:
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

    def mark_vision_dispatch(self, run_id: str, item_id: str, *, node_ref: str, page_number: int) -> None:
        with self._lock:
            run = self._run_unlocked(run_id)
            item = self._item_unlocked(run, item_id)
            if run.get("cancel_requested") or item.get("status") != "running":
                raise EvaluationStateError("当前评测已取消或用例不在执行中，不能派发视觉请求。")
            if type(page_number) is not int or not 1 <= page_number <= 20:
                raise EvaluationStateError("视觉请求页号无效。")
            dispatches = item.setdefault("vision_dispatches", [])
            if any(entry["node_ref"] == node_ref and entry["page_number"] == page_number for entry in dispatches):
                raise EvaluationStateError("同一评测用例的视觉页面不得重复派发。")
            entry = {"node_ref": node_ref, "page_number": page_number, "dispatched_at": time.time()}
            dispatches.append(entry)
            try:
                self._save_unlocked()
            except Exception:
                dispatches.remove(entry)
                raise

    def vision_fixture_for_item(self, run_id: str, *, target_id: str, case_id: str, item_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            run = self._run_unlocked(run_id)
            if target_id not in {target["target_id"] for target in run["targets"]} or case_id not in run["selected_case_ids"]:
                raise EvaluationStateError("附件夹具不属于当前目标或用例。")
            if item_id is not None:
                item = self._item_unlocked(run, item_id)
                if item["target_id"] != target_id or item["case_id"] != case_id or item["status"] != "running":
                    raise EvaluationStateError("视觉附件与当前执行项不一致。")
            entry = (run.get("_vision_fixtures") or {}).get(case_id)
            if not isinstance(entry, dict) or self.vision_fixtures is None:
                raise EvaluationStateError("固定评测附件夹具缺失，禁止回退其他文件。")
            self.vision_fixtures.resolve_run_asset(run_id, entry)
            return copy.deepcopy(entry)

    def record_vision_receipt(self, run_id: str, item_id: str, *, node_ref: str, receipt: dict[str, Any]) -> int:
        try:
            from server.multimodal.vision_v2 import safe_vision_receipt
        except ModuleNotFoundError:
            from multimodal.vision_v2 import safe_vision_receipt
        with self._lock:
            run = self._run_unlocked(run_id)
            item = self._item_unlocked(run, item_id)
            receipts = item.setdefault("vision_receipts", {})
            if node_ref not in receipts and len(receipts) >= 20:
                raise EvaluationStateError("视觉节点回执数量超过上限。")
            safe = safe_vision_receipt(receipt)
            old = receipts.get(node_ref)
            receipts[node_ref] = safe
            try:
                self._save_unlocked()
            except Exception:
                if old is None:
                    receipts.pop(node_ref, None)
                else:
                    receipts[node_ref] = old
                raise
            return sum(entry["usage"]["known_total_tokens"] for entry in receipts.values())

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
                return self.run_payload(run, include_detail=True)
            run["cancel_requested"] = True
            if run.get("status") == "queued":
                run["status"] = "cancelled"
                run["completed_at"] = time.time()
            for item in run.get("items") or []:
                if item.get("status") == "pending":
                    item["status"] = "cancelled"
            run["updated_at"] = time.time()
            self._save_unlocked()
            return self.run_payload(run, include_detail=True)

    def resource_fixtures_for_item(
        self,
        run_id: str,
        *,
        target_id: str,
        case_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            run = self._run_unlocked(run_id)
            matches = [
                item
                for item in list(run.get("_resource_fixtures") or [])
                if str(item.get("target_id") or "") == target_id
                and str(item.get("case_id") or "") == case_id
            ]
            try:
                return [
                    AgentTableQueryFixture.model_validate(item).model_dump(
                        mode="json"
                    )
                    for item in matches
                ]
            except Exception as exc:
                raise EvaluationStateError(
                    "Persisted evaluation resource fixture failed integrity validation."
                ) from exc

    @staticmethod
    def dataset_payload(item: dict[str, Any], *, include_cases: bool) -> dict[str, Any]:
        payload = copy.deepcopy(item)
        payload.setdefault("origin", "manual")
        payload.setdefault("catalog_ref", {})
        payload.setdefault("provenance", {})
        payload.setdefault("coverage", {})
        payload.setdefault("calibration", {"status": "pending", "updated_at": None})
        payload["case_count"] = len(payload.get("cases") or [])
        payload["version_count"] = len(payload.get("versions") or [])
        payload.pop("versions", None)
        if not include_cases:
            payload.pop("cases", None)
        return payload

    @staticmethod
    def run_payload(item: dict[str, Any], *, include_detail: bool) -> dict[str, Any]:
        payload = copy.deepcopy(item)
        payload.pop("_resource_fixtures", None)
        payload.pop("_vision_fixtures", None)
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
        normalized = {
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
        if isinstance(case.get("path"), dict):
            normalized["path"] = copy.deepcopy(case["path"])
        if isinstance(case.get("resource_reads"), list):
            normalized["resource_reads"] = copy.deepcopy(case["resource_reads"])
        if isinstance(case.get("targeting"), dict):
            normalized["targeting"] = copy.deepcopy(case["targeting"])
        if case.get("attachment") is not None:
            from .models import EvaluationAttachmentReference
            normalized["attachment"] = EvaluationAttachmentReference.model_validate(case["attachment"]).model_dump(mode="json")
        if case.get("vision"):
            from .models import EvaluationVisionExpectation
            if not isinstance(case["vision"], list) or len(case["vision"]) > 20:
                raise EvaluationStateError("每条用例最多配置 20 个视觉节点断言。")
            normalized["vision"] = [EvaluationVisionExpectation.model_validate(item).model_dump(mode="json") for item in case["vision"]]
            refs = [item["node_ref"] for item in normalized["vision"]]
            if len(refs) != len(set(refs)):
                raise EvaluationStateError("同一视觉节点不能重复配置断言。")
        return normalized

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "datasets": {}, "runs": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "datasets": {}, "runs": {}}
        datasets = dict(raw.get("datasets") or {})
        for item in datasets.values():
            if not isinstance(item, dict):
                continue
            self._ensure_metadata(item)
            for version in item.get("versions") or []:
                if isinstance(version, dict):
                    self._ensure_metadata(version, fallback=item)
        return {
            "schema_version": 1,
            "datasets": datasets,
            "runs": dict(raw.get("runs") or {}),
        }

    @classmethod
    def _validate_resource_fixtures(
        cls,
        fixtures: list[dict[str, Any]],
        *,
        targets: list[dict[str, Any]],
        cases: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        if len(fixtures) > cls.MAX_RESOURCE_FIXTURES:
            raise EvaluationStateError(
                "Evaluation run exceeds the 1000 resource fixture limit."
            )
        target_ids = {str(item.get("target_id") or "") for item in targets}
        case_ids = {str(item.get("case_id") or "") for item in cases}
        normalized: list[dict[str, Any]] = []
        keys: set[tuple[str, str, str]] = set()
        for fixture in fixtures:
            try:
                item = AgentTableQueryFixture.model_validate(fixture).model_dump(mode="json")
            except Exception as exc:
                raise EvaluationStateError(
                    "Evaluation resource fixture failed schema validation."
                ) from exc
            if item["target_id"] not in target_ids or item["case_id"] not in case_ids:
                raise EvaluationStateError(
                    "Evaluation resource fixture references a target or case outside this run."
                )
            key = (item["target_id"], item["case_id"], item["node_ref"])
            if key in keys:
                raise EvaluationStateError(
                    "Evaluation resource fixtures must be unique per target, case, and node."
                )
            keys.add(key)
            normalized.append(item)
        try:
            encoded = json.dumps(
                normalized,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise EvaluationStateError(
                "Evaluation resource fixtures must be JSON-safe."
            ) from exc
        if len(encoded) > cls.MAX_RESOURCE_FIXTURE_BYTES:
            raise EvaluationStateError(
                "Evaluation run exceeds the 16 MiB resource fixture limit."
            )
        return normalized, len(encoded)

    @staticmethod
    def _metadata_payload(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "origin": str(item.get("origin") or "manual"),
            "catalog_ref": copy.deepcopy(item.get("catalog_ref") or {}),
            "provenance": copy.deepcopy(item.get("provenance") or {}),
            "coverage": copy.deepcopy(item.get("coverage") or {}),
            "calibration": copy.deepcopy(
                item.get("calibration")
                or {"status": "pending", "updated_at": None}
            ),
        }

    @staticmethod
    def _ensure_metadata(
        item: dict[str, Any],
        *,
        fallback: dict[str, Any] | None = None,
    ) -> None:
        source = fallback or {}
        item.setdefault("origin", str(source.get("origin") or "manual"))
        item.setdefault("catalog_ref", copy.deepcopy(source.get("catalog_ref") or {}))
        item.setdefault("provenance", copy.deepcopy(source.get("provenance") or {}))
        item.setdefault("coverage", copy.deepcopy(source.get("coverage") or {}))
        item.setdefault(
            "calibration",
            copy.deepcopy(
                source.get("calibration")
                or {"status": "pending", "updated_at": None}
            ),
        )

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
