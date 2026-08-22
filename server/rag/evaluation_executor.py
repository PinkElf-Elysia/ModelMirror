from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .evaluation import (
    KnowledgeEvaluationStore,
    aggregate_target_metrics,
    build_paired_execution_schedule,
    evaluate_promotion_gate,
    evaluate_retrieval_case,
    paired_primary_confidence_report,
)
from .rag_service import RagService
from .runtime_identity import is_valid_rag_runtime_identity


logger = logging.getLogger(__name__)


def _formal_target_fingerprint(
    run: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    version_id = str(target.get("version_id") or "")
    fingerprints = (run.get("execution_manifest") or {}).get("target_fingerprints") or []
    return next(
        (
            dict(item)
            for item in fingerprints
            if isinstance(item, dict)
            and str(item.get("version_id") or "") == version_id
        ),
        {},
    )


def _validate_formal_retrieval_receipt(
    run: dict[str, Any],
    target: dict[str, Any],
    receipt: dict[str, Any],
    *,
    source_count: int,
) -> None:
    fingerprint = _formal_target_fingerprint(run, target)
    expected_retrieval = dict(fingerprint.get("retrieval") or {})
    mismatches = [
        key
        for key, expected in expected_retrieval.items()
        if receipt.get(key) != expected
    ]
    expected_top_k = int(expected_retrieval.get("top_k") or 0)
    expected_multiplier = int(expected_retrieval.get("candidate_multiplier") or 0)
    expected_candidate_limit = min(200, expected_top_k * expected_multiplier)
    if int(receipt.get("candidate_limit") or 0) != expected_candidate_limit:
        mismatches.append("candidate_limit")
    observation_depth = int(
        (run.get("execution_manifest") or {}).get("observation_depth") or 0
    )
    if int(receipt.get("observation_depth") or 0) != observation_depth:
        mismatches.append("observation_depth")
    required_abstention_fields = {
        "abstention_applied": bool,
        "abstained": bool,
        "abstention_score_domain": str,
        "abstention_input_count": int,
        "abstention_reason": str,
    }
    for key, expected_type in required_abstention_fields.items():
        if type(receipt.get(key)) is not expected_type:
            mismatches.append(key)
    abstained = receipt.get("abstained")
    abstention_applied = receipt.get("abstention_applied")
    abstention_reason = receipt.get("abstention_reason")
    abstention_input_count = receipt.get("abstention_input_count")
    if type(abstention_input_count) is int and abstention_input_count < 0:
        mismatches.append("abstention_input_count")
    if type(abstained) is bool and abstained != (source_count == 0):
        mismatches.append("abstained")
    if abstained is True and abstention_applied is not True:
        mismatches.append("abstention_applied")
    if abstained is True and abstention_reason not in {
        "no_candidates",
        "missing_vector_score",
        "below_threshold",
        "requested_fact_absent",
        "insufficient_context",
        "conflicting_evidence",
        "verifier_unavailable",
    }:
        mismatches.append("abstention_reason")
    if abstained is False and abstention_reason not in {
        "disabled",
        "accepted",
        "evidence_supported",
    }:
        mismatches.append("abstention_reason")
    if expected_retrieval.get("evidence_verification_enabled"):
        evidence_fields = {
            "evidence_verification_enabled": bool,
            "evidence_verification_applied": bool,
            "evidence_verdict": str,
            "evidence_provider": str,
            "evidence_model": str,
        }
        for key, expected_type in evidence_fields.items():
            if type(receipt.get(key)) is not expected_type:
                mismatches.append(key)
        no_candidates = (
            abstained is True
            and abstention_applied is True
            and abstention_reason == "no_candidates"
            and abstention_input_count == 0
            and source_count == 0
        )
        if no_candidates:
            if receipt.get("evidence_verification_applied") is not False:
                mismatches.append("evidence_verification_applied")
            if receipt.get("evidence_verdict") != "unavailable":
                mismatches.append("evidence_verdict")
        else:
            if receipt.get("evidence_verification_applied") is not True:
                mismatches.append("evidence_verification_applied")
            if receipt.get("evidence_verdict") not in {"answerable", "abstain"}:
                mismatches.append("evidence_verdict")
    expected_embedding = dict((fingerprint.get("embedding") or {}).get("effective") or {})
    embedding_fields = {
        "embedding_provider": str(expected_embedding.get("provider") or ""),
        "embedding_model": str(expected_embedding.get("model") or ""),
        "embedding_dimension": int(expected_embedding.get("dimension") or 0),
    }
    for key, expected in embedding_fields.items():
        if expected and receipt.get(key) != expected:
            mismatches.append(key)
    if mismatches:
        fields = ", ".join(sorted(set(mismatches)))
        raise ValueError(
            f"Formal retrieval receipt does not match execution manifest: {fields}."
        )


def _validate_formal_runtime_identity(
    run: dict[str, Any], service: RagService
) -> None:
    if str(run.get("run_mode") or "diagnostic") != "formal":
        return
    expected = (run.get("execution_manifest") or {}).get("runtime")
    if not is_valid_rag_runtime_identity(expected):
        raise ValueError("Formal evaluation RAG runtime identity is invalid.")
    for target in run.get("targets") or []:
        if not isinstance(target, dict):
            raise ValueError("Formal evaluation target runtime identity is invalid.")
        fingerprint = _formal_target_fingerprint(run, target)
        live = service.pipeline_version_evidence(
            str(target.get("version_id") or "")
        )
        if (
            fingerprint.get("runtime") != expected
            or (target.get("version_evidence") or {}).get("runtime") != expected
            or live.get("runtime") != expected
        ):
            raise ValueError(
                "Formal evaluation RAG runtime changed after the run was queued."
            )


class KnowledgeEvaluationExecutor:
    """Single-process, restart-safe executor for retrieval evaluation runs."""

    def __init__(
        self,
        service: RagService,
        store: KnowledgeEvaluationStore,
        *,
        run_registry: Any | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.service = service
        self.store = store
        self.run_registry = run_registry
        self.poll_interval = max(0.1, poll_interval)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self.store.recover_runs()
        self._task = asyncio.create_task(self._worker(), name="knowledge-evaluation-executor")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
        self._task = None

    def notify(self) -> None:
        self._wake.set()

    async def run_once(self) -> bool:
        run = self.store.claim_next_run()
        if run is None:
            return False
        await self._execute(run)
        return True

    async def _worker(self) -> None:
        while not self._stopping:
            processed = await self.run_once()
            if processed:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    async def _execute(self, run: dict[str, Any]) -> None:
        run_id = str(run["run_id"])
        registry_id = await self._ensure_registry_run(run)
        try:
            await self._checkpoint(
                registry_id,
                event_type="knowledge_evaluation.started",
                title="Knowledge evaluation started",
                metadata={
                    "evaluation_run_id": run_id,
                    "target_count": len(run["targets"]),
                    "case_count": len(run["eval_set_snapshot"]["cases"]),
                },
            )
            max_k = max(run["ks"])
            targets_by_id = {
                str(target["target_id"]): target for target in run["targets"]
            }
            cases_by_id = {
                str(case["case_id"]): case
                for case in run["eval_set_snapshot"]["cases"]
            }
            schedule = list(run.get("execution_schedule") or []) or build_paired_execution_schedule(
                list(run["eval_set_snapshot"]["cases"]),
                list(run["targets"]),
                seed=int(run.get("execution_seed") or 0),
            )
            _validate_formal_runtime_identity(run, self.service)
            for scheduled in schedule:
                target = targets_by_id.get(str(scheduled.get("target_id") or ""))
                case = cases_by_id.get(str(scheduled.get("case_id") or ""))
                if not isinstance(target, dict) or not isinstance(case, dict):
                    raise ValueError("Evaluation execution schedule no longer matches its snapshot.")
                target_id = str(target["target_id"])
                formal = str(run.get("run_mode") or "diagnostic") == "formal"
                target_retrieval = dict(target.get("retrieval") or {})
                target_top_k = max_k
                observation_depth: int | None = None
                if formal:
                    fingerprint = _formal_target_fingerprint(run, target)
                    target_retrieval = dict(fingerprint.get("retrieval") or {})
                    target_top_k = int(target_retrieval.get("top_k") or 0)
                    observation_depth = int(
                        (run.get("execution_manifest") or {}).get(
                        "observation_depth", max_k
                        )
                    )
                    if target_top_k < 1:
                        raise ValueError(
                            "Formal evaluation target is missing its retrieval Top-K fingerprint."
                        )
                elif bool(target.get("respect_profile_top_k")):
                    target_top_k = max(
                        1,
                        min(
                            int((target.get("retrieval") or {}).get("top_k") or max_k),
                            max_k,
                        ),
                    )
                case_id = str(case["case_id"])
                current = self.store.get_run(run_id)
                if self.store.cancel_requested(run_id):
                    self.store.complete_cancel(run_id)
                    await self._finish_registry(registry_id, "cancelled", "Cancelled by user.")
                    return
                existing = current.get("case_results", {}).get(target_id, {}).get(case_id)
                if isinstance(existing, dict):
                    continue
                started = time.perf_counter()
                retrieval_receipt: dict[str, Any] = {}
                retrieval_warnings: list[str] = []
                try:
                    retrieval = await self.service.query_pipeline_version(
                        str(target["version_id"]),
                        str(case["query"]),
                        top_k=target_top_k,
                        retrieval={**target_retrieval, "top_k": target_top_k},
                        observation_depth=observation_depth,
                        generate_answer=False,
                    )
                    retrieval_receipt = dict(retrieval.get("retrieval") or {})
                    retrieval_warnings = list(retrieval.get("warnings") or [])
                    if formal:
                        _validate_formal_retrieval_receipt(
                            run,
                            target,
                            retrieval_receipt,
                            source_count=len(retrieval.get("sources") or []),
                        )
                    case_result = evaluate_retrieval_case(
                        list(retrieval.get("sources") or []),
                        list(case.get("expected_refs") or []),
                        ks=list(run["ks"]),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        warnings=retrieval_warnings,
                        expected_no_result=bool(case.get("expected_no_result")),
                        retrieval_receipt=retrieval_receipt,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Knowledge evaluation case failed run=%s target=%s case=%s",
                        run_id,
                        target_id,
                        case_id,
                        exc_info=True,
                    )
                    case_result = {
                        "status": "failed",
                        "metrics": {},
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "source_count": 0,
                        "expected_count": len(case.get("expected_refs") or []),
                        "matched_expected_count": 0,
                        "expected_no_result": bool(case.get("expected_no_result")),
                        "no_result": True,
                        "warning_count": len(retrieval_warnings),
                        "warnings": retrieval_warnings,
                        "ranking": [],
                        "error": self.service._safe_pipeline_error(exc),
                    }
                    if retrieval_receipt:
                        case_result["retrieval_receipt"] = retrieval_receipt
                case_result.update(
                    {
                        "case_id": case_id,
                        "query_preview": str(case["query"])[:160],
                    }
                )
                self.store.record_case_result(run_id, target_id, case_id, case_result)

            completed = self.store.get_run(run_id)
            aggregates: list[dict[str, Any]] = []
            for target in completed["targets"]:
                target_id = str(target["target_id"])
                case_map = completed.get("case_results", {}).get(target_id, {})
                ordered = [
                    case_map[str(case["case_id"])]
                    for case in completed["eval_set_snapshot"]["cases"]
                    if str(case["case_id"]) in case_map
                ]
                aggregates.append(
                    {
                        **target,
                        "metrics": aggregate_target_metrics(ordered, ks=list(completed["ks"])),
                        "case_results": ordered,
                    }
                )

            baseline = next(
                (
                    item["metrics"]
                    for item in aggregates
                    if item["version_id"] == completed.get("baseline_version_id")
                ),
                None,
            )
            baseline_target = next(
                (
                    item
                    for item in aggregates
                    if item["version_id"] == completed.get("baseline_version_id")
                ),
                None,
            )
            baseline_case_map = {
                str(item.get("case_id") or ""): item
                for item in (baseline_target or {}).get("case_results") or []
            }
            for item in aggregates:
                candidate_case_map = {
                    str(result.get("case_id") or ""): result
                    for result in item.get("case_results") or []
                }
                paired_confidence = (
                    paired_primary_confidence_report(
                        list(completed["eval_set_snapshot"]["cases"]),
                        baseline_case_map,
                        candidate_case_map,
                        seed=int(completed.get("execution_seed") or 0),
                        iterations=10_000,
                        confidence_level=float(
                            completed["gate_policy"].get(
                                "paired_confidence_level", 0.95
                            )
                        ),
                    )
                    if baseline_target is not None
                    else None
                )
                item["paired_confidence"] = paired_confidence
                item["promotion_gate"] = evaluate_promotion_gate(
                    item["metrics"],
                    baseline=baseline if item["version_id"] != completed.get("baseline_version_id") else item["metrics"],
                    policy=dict(completed["gate_policy"]),
                    evidence_qualification=dict(
                        completed.get("evidence_qualification") or {}
                    ),
                    paired_confidence=paired_confidence,
                    comparability=(
                        dict(completed.get("comparability") or {})
                        if str(completed.get("run_mode") or "diagnostic") == "formal"
                        else None
                    ),
                )
            final = self.store.complete_run(run_id, aggregates)
            await self._checkpoint(
                registry_id,
                event_type="knowledge_evaluation.completed",
                title="Knowledge evaluation completed",
                summary=f"Compared {len(aggregates)} immutable knowledge versions.",
                metadata={
                    "evaluation_run_id": run_id,
                    "passed_target_count": sum(
                        1 for item in aggregates if item["promotion_gate"]["passed"]
                    ),
                },
            )
            await self._finish_registry(
                registry_id,
                "completed",
                metadata={"evaluation_run_id": final["run_id"]},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Knowledge evaluation run failed run_id=%s", run_id)
            self.store.fail_run(run_id, self.service._safe_pipeline_error(exc))
            await self._checkpoint(
                registry_id,
                event_type="knowledge_evaluation.failed",
                title="Knowledge evaluation failed",
                summary=self.service._safe_pipeline_error(exc),
                severity="error",
            )
            await self._finish_registry(
                registry_id,
                "failed",
                self.service._safe_pipeline_error(exc),
            )

    async def _ensure_registry_run(self, run: dict[str, Any]) -> str | None:
        if self.run_registry is None:
            return None
        existing_id = str(run.get("run_registry_id") or "")
        if existing_id and await self.run_registry.get_run(existing_id) is not None:
            return existing_id
        registry_run = await self.run_registry.create_run(
            "knowledge_evaluation",
            f"Knowledge evaluation: {run['kb_id']}",
            status="running",
            source_id=str(run["run_id"]),
            metadata={
                "evaluation_run_id": run["run_id"],
                "kb_id": run["kb_id"],
                "eval_set_id": run["eval_set_id"],
                "target_count": len(run["targets"]),
            },
        )
        self.store.set_run_registry_id(str(run["run_id"]), registry_run.run_id)
        return registry_run.run_id

    async def _checkpoint(
        self,
        run_id: str | None,
        *,
        event_type: str,
        title: str,
        summary: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.run_registry is None or not run_id:
            return
        await self.run_registry.record_checkpoint(
            run_id,
            event_type=event_type,
            title=title,
            summary=summary,
            severity=severity,
            metadata=dict(metadata or {}),
        )

    async def _finish_registry(
        self,
        run_id: str | None,
        status: str,
        error: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.run_registry is None or not run_id:
            return
        await self.run_registry.update_run(
            run_id,
            status=status,
            error=error,
            metadata=metadata,
        )
