from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class EvaluationError(RuntimeError):
    """Base error for knowledge evaluation operations."""


class EvaluationSetNotFoundError(EvaluationError):
    """Raised when an evaluation set does not exist."""


class EvaluationRunNotFoundError(EvaluationError):
    """Raised when an evaluation run does not exist."""


class EvaluationRevisionError(EvaluationError):
    """Raised when optimistic revision validation fails."""


class EvaluationStateError(EvaluationError):
    """Raised when an evaluation operation is invalid for the current state."""


class EvaluationPromotionError(EvaluationError):
    """Raised when a candidate does not satisfy the configured promotion gate."""


DEFAULT_KS = [1, 3, 5, 10]
DEFAULT_GATE_POLICY: dict[str, Any] = {
    "mode": "advisory",
    "min_recall_at_5": 0.8,
    "max_mrr_regression": 0.03,
    "max_citation_hit_regression": 0.02,
    "max_no_result_increase": 0.05,
    "max_p95_latency_ratio": 2.0,
    "require_zero_errors": True,
}


def evaluate_retrieval_case(
    sources: list[dict[str, Any]],
    expected_refs: list[dict[str, Any]],
    *,
    ks: list[int] | None = None,
    latency_ms: float = 0.0,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Score one ranked retrieval response against stable relevance references."""

    normalized_ks = sorted(set(ks or DEFAULT_KS))
    relevant_ranks: list[int] = []
    matched_ref_indexes: set[int] = set()
    ranking: list[dict[str, Any]] = []

    for rank, source in enumerate(sources, start=1):
        matches = [
            index
            for index, reference in enumerate(expected_refs)
            if index not in matched_ref_indexes and _source_matches_reference(source, reference)
        ]
        if matches:
            best_index = max(matches, key=lambda index: int(expected_refs[index].get("relevance", 1)))
            matched_ref_indexes.add(best_index)
            relevance = int(expected_refs[best_index].get("relevance", 1))
            relevant_ranks.append(rank)
            matched_ref_id = str(expected_refs[best_index].get("reference_id") or best_index)
        else:
            relevance = 0
            matched_ref_id = None
        ranking.append(
            {
                "rank": rank,
                "chunk_id": str(source.get("chunk_id") or ""),
                "document_id": str(
                    source.get("source_document_id")
                    or source.get("doc_id")
                    or source.get("document_id")
                    or ""
                ),
                "document_name": str(source.get("document_name") or "")[:240],
                "source_block_id": source.get("source_block_id"),
                "page_number": source.get("page_number"),
                "visual_kind": source.get("visual_kind"),
                "score": _float_or_none(source.get("score")),
                "vector_score": _float_or_none(source.get("vector_score")),
                "fulltext_score": _float_or_none(source.get("fulltext_score")),
                "fused_score": _float_or_none(source.get("fused_score")),
                "rerank_score": _float_or_none(source.get("rerank_score")),
                "relevance": relevance,
                "matched_reference_id": matched_ref_id,
            }
        )

    total_relevant = len(expected_refs)
    metrics: dict[str, float] = {}
    for k in normalized_ks:
        hits = sum(1 for rank in relevant_ranks if rank <= k)
        metrics[f"hit_at_{k}"] = 1.0 if hits else 0.0
        metrics[f"recall_at_{k}"] = hits / total_relevant if total_relevant else 0.0

    first_rank = min(relevant_ranks, default=0)
    max_k = max(normalized_ks, default=10)
    metrics[f"mrr_at_{max_k}"] = 1.0 / first_rank if first_rank and first_rank <= max_k else 0.0
    metrics[f"ndcg_at_{max_k}"] = _ndcg_at_k(ranking, expected_refs, max_k)
    relevant_source_count = sum(1 for item in ranking if int(item["relevance"]) > 0)
    metrics["citation_hit_rate"] = (
        relevant_source_count / len(ranking) if ranking else 0.0
    )
    metrics["citation_coverage"] = (
        len(matched_ref_indexes) / total_relevant if total_relevant else 0.0
    )

    return {
        "status": "completed",
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "latency_ms": round(max(0.0, latency_ms), 3),
        "source_count": len(sources),
        "expected_count": total_relevant,
        "matched_expected_count": len(matched_ref_indexes),
        "no_result": len(sources) == 0,
        "warning_count": len(warnings or []),
        "warnings": [str(item)[:240] for item in (warnings or [])[:10]],
        "ranking": ranking,
    }


def aggregate_target_metrics(
    case_results: list[dict[str, Any]],
    *,
    ks: list[int] | None = None,
) -> dict[str, Any]:
    """Aggregate deterministic retrieval metrics for one immutable target."""

    normalized_ks = sorted(set(ks or DEFAULT_KS))
    completed = [item for item in case_results if item.get("status") == "completed"]
    errors = [item for item in case_results if item.get("status") == "failed"]
    metric_names = [
        *(f"hit_at_{k}" for k in normalized_ks),
        *(f"recall_at_{k}" for k in normalized_ks),
        f"mrr_at_{max(normalized_ks, default=10)}",
        f"ndcg_at_{max(normalized_ks, default=10)}",
        "citation_hit_rate",
        "citation_coverage",
    ]
    metrics = {
        name: round(
            sum(float(item.get("metrics", {}).get(name, 0.0)) for item in completed)
            / len(completed),
            6,
        )
        if completed
        else 0.0
        for name in metric_names
    }
    latencies = sorted(float(item.get("latency_ms", 0.0)) for item in completed)
    metrics.update(
        {
            "case_count": len(case_results),
            "completed_case_count": len(completed),
            "error_count": len(errors),
            "no_result_rate": round(
                sum(1 for item in completed if item.get("no_result")) / len(completed), 6
            )
            if completed
            else 1.0,
            "warning_rate": round(
                sum(1 for item in completed if int(item.get("warning_count", 0)) > 0)
                / len(completed),
                6,
            )
            if completed
            else 0.0,
            "average_latency_ms": round(sum(latencies) / len(latencies), 3)
            if latencies
            else 0.0,
            "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        }
    )
    return metrics


def evaluate_promotion_gate(
    candidate: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate absolute and regression thresholds for one candidate target."""

    effective = {**DEFAULT_GATE_POLICY, **dict(policy or {})}
    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, actual: float, threshold: float, message: str) -> None:
        checks.append(
            {
                "id": check_id,
                "passed": bool(passed),
                "actual": round(float(actual), 6),
                "threshold": round(float(threshold), 6),
                "message": message,
            }
        )

    recall = float(candidate.get("recall_at_5", 0.0))
    min_recall = float(effective["min_recall_at_5"])
    add_check(
        "min_recall_at_5",
        recall >= min_recall,
        recall,
        min_recall,
        "Recall@5 must meet the configured minimum.",
    )
    errors = float(candidate.get("error_count", 0))
    if bool(effective.get("require_zero_errors", True)):
        add_check(
            "zero_errors",
            errors == 0,
            errors,
            0.0,
            "Evaluation must complete without case errors.",
        )

    if baseline is not None:
        mrr_regression = float(baseline.get("mrr_at_10", 0.0)) - float(
            candidate.get("mrr_at_10", 0.0)
        )
        add_check(
            "max_mrr_regression",
            mrr_regression <= float(effective["max_mrr_regression"]),
            mrr_regression,
            float(effective["max_mrr_regression"]),
            "MRR@10 regression must stay within the configured tolerance.",
        )
        citation_regression = float(baseline.get("citation_hit_rate", 0.0)) - float(
            candidate.get("citation_hit_rate", 0.0)
        )
        add_check(
            "max_citation_hit_regression",
            citation_regression <= float(effective["max_citation_hit_regression"]),
            citation_regression,
            float(effective["max_citation_hit_regression"]),
            "Citation hit-rate regression must stay within tolerance.",
        )
        no_result_increase = float(candidate.get("no_result_rate", 0.0)) - float(
            baseline.get("no_result_rate", 0.0)
        )
        add_check(
            "max_no_result_increase",
            no_result_increase <= float(effective["max_no_result_increase"]),
            no_result_increase,
            float(effective["max_no_result_increase"]),
            "No-result rate increase must stay within tolerance.",
        )
        baseline_p95 = float(baseline.get("p95_latency_ms", 0.0))
        candidate_p95 = float(candidate.get("p95_latency_ms", 0.0))
        latency_ratio = candidate_p95 / baseline_p95 if baseline_p95 > 0 else 1.0
        add_check(
            "max_p95_latency_ratio",
            latency_ratio <= float(effective["max_p95_latency_ratio"]),
            latency_ratio,
            float(effective["max_p95_latency_ratio"]),
            "P95 latency ratio must stay within the configured limit.",
        )

    return {
        "passed": all(item["passed"] for item in checks),
        "mode": str(effective["mode"]),
        "checks": checks,
    }


class KnowledgeEvaluationStore:
    """File-backed evaluation datasets, runs, and promotion policies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create_set(self, kb_id: str, name: str, description: str = "") -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Evaluation set name is required.")
        now = time.time()
        item = {
            "eval_set_id": f"evalset_{uuid.uuid4().hex}",
            "kb_id": kb_id,
            "name": clean_name[:160],
            "description": description.strip()[:1000],
            "revision": 1,
            "status": "active",
            "cases": [],
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            data = self._read_unlocked()
            data["sets"][item["eval_set_id"]] = item
            self._write_unlocked(data)
        return _copy(item)

    def list_sets(self, kb_id: str) -> list[dict[str, Any]]:
        data = self._read()
        items = [item for item in data["sets"].values() if item.get("kb_id") == kb_id]
        items.sort(key=lambda item: float(item.get("updated_at", 0.0)), reverse=True)
        return [_copy(item) for item in items]

    def get_set(self, eval_set_id: str) -> dict[str, Any]:
        item = self._read()["sets"].get(eval_set_id)
        if not isinstance(item, dict):
            raise EvaluationSetNotFoundError("Knowledge evaluation set not found.")
        return _copy(item)

    def update_set(
        self,
        eval_set_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise ValueError("Evaluation set name is required.")
                item["name"] = clean_name[:160]
            if description is not None:
                item["description"] = description.strip()[:1000]
            if status is not None:
                if status not in {"active", "archived"}:
                    raise ValueError("Evaluation set status must be active or archived.")
                item["status"] = status
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def add_cases(
        self,
        eval_set_id: str,
        *,
        expected_revision: int,
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not cases or len(cases) > 500:
            raise ValueError("Import must contain between 1 and 500 evaluation cases.")
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            if len(item["cases"]) + len(cases) > 500:
                raise ValueError("An evaluation set supports at most 500 cases.")
            for raw in cases:
                item["cases"].append(self._normalize_case(raw))
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def update_case(
        self,
        eval_set_id: str,
        case_id: str,
        *,
        expected_revision: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            index = next(
                (index for index, case in enumerate(item["cases"]) if case["case_id"] == case_id),
                None,
            )
            if index is None:
                raise EvaluationSetNotFoundError("Knowledge evaluation case not found.")
            merged = {**item["cases"][index], **values, "case_id": case_id}
            item["cases"][index] = self._normalize_case(merged, preserve_id=True)
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def delete_case(
        self,
        eval_set_id: str,
        case_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            remaining = [case for case in item["cases"] if case["case_id"] != case_id]
            if len(remaining) == len(item["cases"]):
                raise EvaluationSetNotFoundError("Knowledge evaluation case not found.")
            item["cases"] = remaining
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def create_run(
        self,
        *,
        evaluation_set: dict[str, Any],
        targets: list[dict[str, Any]],
        baseline_version_id: str | None,
        ks: list[int],
        gate_policy: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.time()
        run = {
            "run_id": f"evalrun_{uuid.uuid4().hex}",
            "kb_id": evaluation_set["kb_id"],
            "eval_set_id": evaluation_set["eval_set_id"],
            "eval_set_revision": evaluation_set["revision"],
            "eval_set_snapshot": _copy(evaluation_set),
            "targets": _copy(targets),
            "baseline_version_id": baseline_version_id,
            "ks": sorted(set(ks)),
            "gate_policy": _copy(gate_policy),
            "status": "queued",
            "progress": 0,
            "cancel_requested": False,
            "case_results": {},
            "target_results": [],
            "run_registry_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }
        with self._lock:
            data = self._read_unlocked()
            data["runs"][run["run_id"]] = run
            self._write_unlocked(data)
        return self.run_payload(run)

    def list_runs(
        self,
        kb_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        data = self._read()
        runs = [item for item in data["runs"].values() if item.get("kb_id") == kb_id]
        if status:
            runs = [item for item in runs if item.get("status") == status]
        runs.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return [self.run_payload(item, include_cases=False) for item in runs[: max(1, limit)]]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._read()["runs"].get(run_id)
        if not isinstance(run, dict):
            raise EvaluationRunNotFoundError("Knowledge evaluation run not found.")
        return self.run_payload(run)

    def claim_next_run(self) -> dict[str, Any] | None:
        with self._lock:
            data = self._read_unlocked()
            queued = [item for item in data["runs"].values() if item.get("status") == "queued"]
            if not queued:
                return None
            queued.sort(key=lambda item: float(item.get("created_at", 0.0)))
            run = queued[0]
            run["status"] = "running"
            run["started_at"] = run.get("started_at") or time.time()
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return _copy(run)

    def recover_runs(self) -> int:
        recovered = 0
        with self._lock:
            data = self._read_unlocked()
            for run in data["runs"].values():
                if run.get("status") == "running":
                    run["status"] = "queued"
                    run["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._write_unlocked(data)
        return recovered

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            if run["status"] not in {"queued", "running"}:
                raise EvaluationStateError("Only queued or running evaluation runs can be cancelled.")
            run["cancel_requested"] = True
            if run["status"] == "queued":
                run["status"] = "cancelled"
                run["completed_at"] = time.time()
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return self.run_payload(run)

    def cancel_requested(self, run_id: str) -> bool:
        return bool(self._run_or_raise(self._read(), run_id).get("cancel_requested"))

    def record_case_result(
        self,
        run_id: str,
        target_id: str,
        case_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            target_results = run.setdefault("case_results", {}).setdefault(target_id, {})
            target_results[case_id] = _copy(result)
            total = max(1, len(run["targets"]) * len(run["eval_set_snapshot"]["cases"]))
            completed = sum(len(items) for items in run["case_results"].values())
            run["progress"] = min(99, int(completed * 100 / total))
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return _copy(run)

    def set_run_registry_id(self, run_id: str, registry_id: str) -> None:
        self._update_run(run_id, {"run_registry_id": registry_id})

    def complete_run(
        self,
        run_id: str,
        target_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._update_run(
            run_id,
            {
                "status": "succeeded",
                "progress": 100,
                "target_results": _copy(target_results),
                "completed_at": time.time(),
                "error": None,
            },
        )

    def fail_run(self, run_id: str, error: str) -> dict[str, Any]:
        return self._update_run(
            run_id,
            {
                "status": "failed",
                "error": _safe_error(error),
                "completed_at": time.time(),
            },
        )

    def complete_cancel(self, run_id: str) -> dict[str, Any]:
        return self._update_run(
            run_id,
            {"status": "cancelled", "completed_at": time.time()},
        )

    def get_gate_policy(self, kb_id: str) -> dict[str, Any]:
        stored = self._read()["gate_policies"].get(kb_id)
        return {**DEFAULT_GATE_POLICY, **dict(stored or {}), "kb_id": kb_id}

    def set_gate_policy(self, kb_id: str, values: dict[str, Any]) -> dict[str, Any]:
        policy = _validate_gate_policy({**DEFAULT_GATE_POLICY, **values})
        with self._lock:
            data = self._read_unlocked()
            data["gate_policies"][kb_id] = policy
            self._write_unlocked(data)
        return {**policy, "kb_id": kb_id}

    def assert_promotion_allowed(
        self,
        *,
        kb_id: str,
        version_id: str,
        evaluation_run_id: str | None,
        require_passed_run: bool,
    ) -> dict[str, Any] | None:
        policy = self.get_gate_policy(kb_id)
        required = require_passed_run or policy["mode"] == "required"
        if not evaluation_run_id:
            if required:
                raise EvaluationPromotionError(
                    "This knowledge base requires a passing evaluation run before activation."
                )
            return None
        run = self.get_run(evaluation_run_id)
        if run["status"] != "succeeded" or run["kb_id"] != kb_id:
            raise EvaluationPromotionError("Evaluation run is not a successful run for this knowledge base.")
        current_set = self.get_set(str(run["eval_set_id"]))
        if int(current_set["revision"]) != int(run["eval_set_revision"]):
            raise EvaluationPromotionError("Evaluation set changed after this run; run it again before promotion.")
        target = next(
            (item for item in run["target_results"] if item.get("version_id") == version_id),
            None,
        )
        if not isinstance(target, dict):
            raise EvaluationPromotionError("Evaluation run does not contain the requested version.")
        gate = target.get("promotion_gate") or {}
        if not bool(gate.get("passed")):
            raise EvaluationPromotionError("Candidate version did not pass the promotion gate.")
        return target

    def run_payload(self, run: dict[str, Any], *, include_cases: bool = True) -> dict[str, Any]:
        payload = _copy(run)
        if not include_cases:
            payload.pop("eval_set_snapshot", None)
            payload.pop("case_results", None)
        return payload

    def _update_run(self, run_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            run.update(values)
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return self.run_payload(run)

    def _normalize_case(self, raw: dict[str, Any], *, preserve_id: bool = False) -> dict[str, Any]:
        query = str(raw.get("query") or "").strip()
        if not query or len(query) > 20_000:
            raise ValueError("Evaluation query must contain between 1 and 20,000 characters.")
        references = raw.get("expected_refs")
        if not isinstance(references, list) or not references or len(references) > 50:
            raise ValueError("Each evaluation case needs between 1 and 50 expected references.")
        normalized_refs: list[dict[str, Any]] = []
        for reference in references:
            if not isinstance(reference, dict) or not str(reference.get("document_id") or "").strip():
                raise ValueError("Each expected reference needs a document_id.")
            relevance = int(reference.get("relevance", 1))
            if relevance < 1 or relevance > 3:
                raise ValueError("Expected reference relevance must be between 1 and 3.")
            normalized_refs.append(
                {
                    "reference_id": str(reference.get("reference_id") or f"ref_{uuid.uuid4().hex}"),
                    "document_id": str(reference["document_id"]).strip()[:200],
                    "chunk_id": _optional_string(reference.get("chunk_id"), 240),
                    "source_block_id": _optional_string(reference.get("source_block_id"), 240),
                    "page_number": _optional_int(reference.get("page_number")),
                    "relevance": relevance,
                }
            )
        return {
            "case_id": str(raw.get("case_id")) if preserve_id and raw.get("case_id") else f"evalcase_{uuid.uuid4().hex}",
            "query": query,
            "expected_refs": normalized_refs,
            "tags": [str(item)[:80] for item in raw.get("tags", []) if str(item).strip()][:20],
            "notes": str(raw.get("notes") or "")[:1000],
        }

    def _touch_set(self, item: dict[str, Any]) -> None:
        item["revision"] = int(item.get("revision", 0)) + 1
        item["updated_at"] = time.time()

    def _check_revision(self, item: dict[str, Any], expected_revision: int) -> None:
        if int(item.get("revision", 0)) != expected_revision:
            raise EvaluationRevisionError("Evaluation set revision is stale; reload before saving.")

    def _set_or_raise(self, data: dict[str, Any], eval_set_id: str) -> dict[str, Any]:
        item = data["sets"].get(eval_set_id)
        if not isinstance(item, dict):
            raise EvaluationSetNotFoundError("Knowledge evaluation set not found.")
        return item

    def _run_or_raise(self, data: dict[str, Any], run_id: str) -> dict[str, Any]:
        item = data["runs"].get(run_id)
        if not isinstance(item, dict):
            raise EvaluationRunNotFoundError("Knowledge evaluation run not found.")
        return item

    def _read(self) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": "knowledge-evaluation-v1", "sets": {}, "runs": {}, "gate_policies": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        return {
            "version": "knowledge-evaluation-v1",
            "sets": value.get("sets") if isinstance(value.get("sets"), dict) else {},
            "runs": value.get("runs") if isinstance(value.get("runs"), dict) else {},
            "gate_policies": value.get("gate_policies") if isinstance(value.get("gate_policies"), dict) else {},
        }

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def _source_matches_reference(source: dict[str, Any], reference: dict[str, Any]) -> bool:
    source_document = str(
        source.get("source_document_id")
        or source.get("doc_id")
        or source.get("document_id")
        or ""
    )
    if source_document != str(reference.get("document_id") or ""):
        return False
    expected_chunk = str(reference.get("chunk_id") or "")
    if expected_chunk:
        return str(source.get("chunk_id") or "") == expected_chunk
    expected_block = str(reference.get("source_block_id") or "")
    if expected_block:
        return str(source.get("source_block_id") or "") == expected_block
    expected_page = reference.get("page_number")
    if expected_page is not None:
        return _optional_int(source.get("page_number")) == int(expected_page)
    return True


def _ndcg_at_k(
    ranking: list[dict[str, Any]],
    expected_refs: list[dict[str, Any]],
    k: int,
) -> float:
    dcg = sum(
        (2 ** int(item.get("relevance", 0)) - 1) / math.log2(rank + 1)
        for rank, item in enumerate(ranking[:k], start=1)
        if int(item.get("relevance", 0)) > 0
    )
    ideal = sorted((int(item.get("relevance", 1)) for item in expected_refs), reverse=True)[:k]
    idcg = sum((2**relevance - 1) / math.log2(rank + 1) for rank, relevance in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(percentile * len(values)) - 1))
    return values[index]


def _validate_gate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("mode") not in {"advisory", "required"}:
        raise ValueError("Evaluation gate mode must be advisory or required.")
    bounded = [
        "min_recall_at_5",
        "max_mrr_regression",
        "max_citation_hit_regression",
        "max_no_result_increase",
    ]
    for key in bounded:
        value = float(policy[key])
        if value < 0 or value > 1:
            raise ValueError(f"{key} must be between 0 and 1.")
        policy[key] = value
    latency = float(policy["max_p95_latency_ratio"])
    if latency < 1 or latency > 10:
        raise ValueError("max_p95_latency_ratio must be between 1 and 10.")
    policy["max_p95_latency_ratio"] = latency
    policy["require_zero_errors"] = bool(policy.get("require_zero_errors", True))
    return policy


def _optional_string(value: Any, limit: int) -> str | None:
    clean = str(value or "").strip()
    return clean[:limit] if clean else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _safe_error(value: Any) -> str:
    return str(value or "Evaluation failed.").strip()[:500]


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
