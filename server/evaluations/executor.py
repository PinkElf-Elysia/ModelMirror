from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .metrics import aggregate_evaluation_report, evaluate_case_metrics
from .store import XpertEvaluationStore


TargetRunner = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], str | None],
    Awaitable[dict[str, Any]],
]
JudgeRunner = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]


class XpertEvaluationExecutor:
    """Restart-safe single-process evaluator for immutable Xpert snapshots."""

    def __init__(
        self,
        store: XpertEvaluationStore,
        *,
        target_runner: TargetRunner,
        judge_runner: JudgeRunner | None = None,
        run_registry: Any | None = None,
        poll_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.target_runner = target_runner
        self.judge_runner = judge_runner
        self.run_registry = run_registry
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.store.recover_runs()
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stopping:
            run = await asyncio.to_thread(self.store.claim_next_run)
            if run is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
                continue
            try:
                await self._execute_run(run)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await asyncio.to_thread(self.store.fail_run, run["run_id"], str(exc))
                if self.run_registry is not None:
                    failed = await asyncio.to_thread(
                        self.store.require_run, run["run_id"]
                    )
                    registry_run_id = failed.get("run_registry_id")
                    if registry_run_id:
                        await self.run_registry.update_run(
                            registry_run_id,
                            status="failed",
                            error=str(exc)[:500],
                        )
                        await self.run_registry.record_checkpoint(
                            registry_run_id,
                            event_type="xpert_evaluation.failed",
                            title="Xpert evaluation failed",
                            summary=str(exc)[:500],
                            severity="error",
                            metadata={"run_id": run["run_id"]},
                        )

    async def _execute_run(self, run: dict[str, Any]) -> None:
        registry_run = None
        if self.run_registry is not None:
            registry_run = await self.run_registry.create_run(
                "xpert_evaluation",
                f"Xpert evaluation {run['run_id']}",
                status="running",
                source_id=run["run_id"],
                metadata={
                    "dataset_id": run["dataset"]["dataset_id"],
                    "dataset_version": run["dataset"]["version"],
                    "target_count": len(run["targets"]),
                    "case_count": len(run["selected_case_ids"]),
                },
            )
            await asyncio.to_thread(
                self.store.set_run_registry_id,
                run["run_id"],
                registry_run.run_id,
            )
        budget = dict(run.get("config", {}).get("budget") or {})
        concurrency = max(1, min(int(budget.get("max_concurrency") or 2), 4))
        semaphore = asyncio.Semaphore(concurrency)
        targets = {item["target_id"]: item for item in run["targets"]}
        cases = {
            item["case_id"]: item
            for item in run["dataset"].get("cases") or []
            if item["case_id"] in run["selected_case_ids"]
        }

        async def execute_item(item: dict[str, Any]) -> None:
            async with semaphore:
                current = await asyncio.to_thread(self.store.require_run, run["run_id"])
                if current.get("cancel_requested"):
                    await asyncio.to_thread(
                        self.store.record_item_result,
                        run["run_id"],
                        item["item_id"],
                        result={"status": "cancelled", "error": "Run cancelled."},
                    )
                    return
                target = targets[item["target_id"]]
                case = cases[item["case_id"]]
                started = time.perf_counter()
                try:
                    timeout = max(
                        10, min(int(budget.get("case_timeout_seconds") or 120), 600)
                    )
                    async with asyncio.timeout(timeout):
                        result = await self.target_runner(
                            target,
                            case,
                            {
                                **dict(run.get("config") or {}),
                                "repetition": item["repetition"],
                            },
                            registry_run.run_id if registry_run else None,
                        )
                    output = str(result.get("output") or "")[
                        : int(budget.get("max_output_chars") or 20_000)
                    ]
                    metrics = await evaluate_case_metrics(
                        case=case,
                        output=output,
                        citations=dict(result.get("citations") or {}),
                        tool_calls=[
                            str(item)
                            for item in list(result.get("tool_calls") or [])
                            if str(item)
                        ],
                        judge=self.judge_runner,
                        judge_model_id=run.get("config", {}).get("judge_model_id"),
                    )
                    payload = {
                        "status": "completed",
                        "output": output,
                        "citations": dict(result.get("citations") or {}),
                        "tool_calls": [
                            str(item)
                            for item in list(result.get("tool_calls") or [])[:100]
                            if str(item)
                        ],
                        "usage": dict(result.get("usage") or {}),
                        "latency_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        **metrics,
                        "runtime_run_id": result.get("runtime_run_id"),
                        "error": None,
                    }
                except Exception as exc:
                    payload = {
                        "status": "failed",
                        "output": "",
                        "citations": {},
                        "usage": {},
                        "score": 0.0,
                        "metrics": [],
                        "latency_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "error": str(exc)[:500],
                    }
                await asyncio.to_thread(
                    self.store.record_item_result,
                    run["run_id"],
                    item["item_id"],
                    result=payload,
                )

        while True:
            current = await asyncio.to_thread(self.store.require_run, run["run_id"])
            if current.get("cancel_requested"):
                break
            claimed = await asyncio.to_thread(
                self.store.claim_items, run["run_id"], concurrency
            )
            if not claimed:
                break
            await asyncio.gather(*(execute_item(item) for item in claimed))

        completed = await asyncio.to_thread(self.store.require_run, run["run_id"])
        report = aggregate_evaluation_report(
            list(completed.get("items") or []),
            baseline_target_id=completed.get("baseline_target_id"),
        )
        report["warnings"] = list(completed.get("warnings") or [])
        result = await asyncio.to_thread(
            self.store.complete_run, run["run_id"], report
        )
        if registry_run is not None:
            await self.run_registry.update_run(
                registry_run.run_id,
                status=(
                    "cancelled" if result.get("status") == "cancelled" else "completed"
                ),
                metadata={
                    "target_count": len(report.get("targets") or []),
                    "comparison_count": len(report.get("comparisons") or []),
                },
            )
            await self.run_registry.record_checkpoint(
                registry_run.run_id,
                event_type="xpert_evaluation.completed",
                title="Xpert evaluation completed",
                summary=f"{len(result.get('items') or [])} work items",
                metadata={
                    "run_id": run["run_id"],
                    "status": result.get("status"),
                },
            )
