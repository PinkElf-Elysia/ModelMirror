from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .evaluation import (
    KnowledgeEvaluationStore,
    aggregate_target_metrics,
    evaluate_promotion_gate,
    evaluate_retrieval_case,
)
from .rag_service import RagService


logger = logging.getLogger(__name__)


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
            for target in run["targets"]:
                target_id = str(target["target_id"])
                for case in run["eval_set_snapshot"]["cases"]:
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
                    try:
                        retrieval = await self.service.query_pipeline_version(
                            str(target["version_id"]),
                            str(case["query"]),
                            top_k=max_k,
                            retrieval=dict(target.get("retrieval") or {}),
                            generate_answer=False,
                        )
                        case_result = evaluate_retrieval_case(
                            list(retrieval.get("sources") or []),
                            list(case.get("expected_refs") or []),
                            ks=list(run["ks"]),
                            latency_ms=(time.perf_counter() - started) * 1000,
                            warnings=list(retrieval.get("warnings") or []),
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
                            "no_result": True,
                            "warning_count": 0,
                            "warnings": [],
                            "ranking": [],
                            "error": self.service._safe_pipeline_error(exc),
                        }
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
            for item in aggregates:
                item["promotion_gate"] = evaluate_promotion_gate(
                    item["metrics"],
                    baseline=baseline if item["version_id"] != completed.get("baseline_version_id") else item["metrics"],
                    policy=dict(completed["gate_policy"]),
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
