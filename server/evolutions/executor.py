from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .service import XpertEvolutionService
from .store import EvolutionConflictError, EvolutionStateError, XpertEvolutionStore


OptimizerRunner = Callable[
    [str, str, str, float, int],
    Awaitable[str],
]


class _RunCancelled(Exception):
    pass


class XpertEvolutionExecutor:
    """Restart-safe bounded Prompt search orchestrated through Evaluator runs."""

    def __init__(
        self,
        store: XpertEvolutionStore,
        service: XpertEvolutionService,
        *,
        evaluation_service: Any,
        evaluation_store: Any,
        evaluation_executor: Any,
        optimizer_runner: OptimizerRunner,
        run_registry: Any | None = None,
        poll_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.service = service
        self.evaluation_service = evaluation_service
        self.evaluation_store = evaluation_store
        self.evaluation_executor = evaluation_executor
        self.optimizer_runner = optimizer_runner
        self.run_registry = run_registry
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.store.recover()
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
            run = await asyncio.to_thread(self.store.claim_next)
            if run is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
                continue
            try:
                await self._execute(run)
            except asyncio.CancelledError:
                raise
            except _RunCancelled:
                continue
            except Exception as exc:
                failed = await asyncio.to_thread(
                    self.store.fail, run["run_id"], str(exc)
                )
                await self._checkpoint(
                    failed,
                    "xpert_evolution.failed",
                    "Prompt evolution failed",
                    str(exc)[:500],
                    severity="error",
                )
                await self._finish_registry(failed, "failed", error=str(exc)[:500])

    async def _execute(self, claimed: dict[str, Any]) -> None:
        run = await asyncio.to_thread(self.store.require, claimed["run_id"])
        await self._ensure_registry_run(run)
        if await self._cancelled(run):
            return
        baseline = self.service.baseline_candidate(run)
        baseline_eval_id = run.get("baseline_evaluation_run_id")
        if not baseline_eval_id:
            baseline_eval = await asyncio.to_thread(
                self.evaluation_service.create_run_from_snapshots,
                dataset_version=run["dataset"],
                cases=self.service.selected_cases(run, "train"),
                baseline=None,
                candidates=[baseline["snapshot"]],
                config=self._evaluation_config(run),
                warnings=["Prompt evolution training baseline."],
            )
            baseline_eval_id = baseline_eval["run_id"]
            run = await asyncio.to_thread(
                self.store.mutate,
                run["run_id"],
                lambda item: item.update(
                    {
                        "baseline_evaluation_run_id": baseline_eval_id,
                        "phase": "baseline_evaluation",
                    }
                ),
            )
            self.evaluation_executor.wake()
        baseline_eval = await self._await_evaluation(run, baseline_eval_id)
        baseline_summary = self._target_summary(
            baseline_eval, baseline["snapshot"]["target_id"]
        )
        if not baseline_summary:
            raise EvolutionStateError("Baseline evaluation produced no target summary.")

        previous_best = baseline
        previous_summary = baseline_summary
        configured_generations = int(run["request"]["generations"])
        for generation_number in range(1, configured_generations + 1):
            run = await asyncio.to_thread(self.store.require, run["run_id"])
            if await self._cancelled(run):
                return
            generation = next(
                (
                    item
                    for item in run.get("generations") or []
                    if int(item.get("generation") or 0) == generation_number
                ),
                None,
            )
            if generation is None:
                fields_payload, repair_used = await self._generate_candidates(
                    run,
                    generation_number,
                    previous_best,
                    previous_summary,
                    baseline,
                    (
                        baseline_eval_id
                        if generation_number == 1
                        else str(
                            next(
                                value
                                for value in run.get("generations") or []
                                if int(value.get("generation") or 0)
                                == generation_number - 1
                            ).get("evaluation_run_id")
                            or ""
                        )
                    ),
                )
                candidates: list[dict[str, Any]] = []
                seen = {
                    baseline["checksum"],
                    *(
                        str(candidate.get("checksum") or "")
                        for previous in run.get("generations") or []
                        for candidate in previous.get("candidates") or []
                    ),
                }
                for index, raw in enumerate(fields_payload, start=1):
                    try:
                        candidate = self.service.build_candidate(
                            run,
                            fields=dict(raw.get("fields") or {}),
                            generation=generation_number,
                            index=index,
                            summary=str(raw.get("summary") or ""),
                        )
                    except EvolutionStateError:
                        continue
                    if candidate["checksum"] in seen:
                        continue
                    seen.add(candidate["checksum"])
                    candidates.append(candidate)
                if len(candidates) < 1:
                    raise EvolutionStateError(
                        "Optimizer did not produce any unique safe Prompt candidates."
                    )
                generation = {
                    "generation": generation_number,
                    "repair_used": repair_used,
                    "candidates": candidates,
                    "evaluation_run_id": None,
                    "ranking": [],
                    "created_at": time.time(),
                }

                def add_generation(item: dict[str, Any]) -> None:
                    if not any(
                        int(value.get("generation") or 0) == generation_number
                        for value in item.get("generations") or []
                    ):
                        item.setdefault("generations", []).append(generation)
                    item["phase"] = f"generation_{generation_number}_generated"

                run = await asyncio.to_thread(
                    self.store.mutate, run["run_id"], add_generation
                )
                generation = next(
                    item
                    for item in run["generations"]
                    if int(item["generation"]) == generation_number
                )

            eval_id = generation.get("evaluation_run_id")
            if not eval_id:
                evaluation = await asyncio.to_thread(
                    self.evaluation_service.create_run_from_snapshots,
                    dataset_version=run["dataset"],
                    cases=self.service.selected_cases(run, "train"),
                    baseline=baseline["snapshot"],
                    candidates=[
                        item["snapshot"] for item in generation["candidates"]
                    ],
                    config=self._evaluation_config(run),
                    warnings=[f"Prompt evolution generation {generation_number}."],
                )
                eval_id = evaluation["run_id"]

                def set_eval(item: dict[str, Any]) -> None:
                    for value in item.get("generations") or []:
                        if int(value.get("generation") or 0) == generation_number:
                            value["evaluation_run_id"] = eval_id
                            break
                    item["phase"] = f"generation_{generation_number}_evaluation"

                run = await asyncio.to_thread(
                    self.store.mutate, run["run_id"], set_eval
                )
                self.evaluation_executor.wake()
            evaluation = await self._await_evaluation(run, eval_id)
            ranking = self._rank_generation(generation["candidates"], evaluation)
            if not ranking:
                raise EvolutionStateError(
                    f"Generation {generation_number} produced no evaluation ranking."
                )

            def save_ranking(item: dict[str, Any]) -> None:
                for value in item.get("generations") or []:
                    if int(value.get("generation") or 0) == generation_number:
                        value["ranking"] = copy.deepcopy(ranking)
                        break
                item["phase"] = f"generation_{generation_number}_ranked"

            run = await asyncio.to_thread(
                self.store.mutate, run["run_id"], save_ranking
            )
            best_id = ranking[0]["candidate_id"]
            prior_best_score = float(previous_summary.get("score") or 0)
            previous_best = next(
                item for item in generation["candidates"] if item["candidate_id"] == best_id
            )
            previous_summary = ranking[0]
            await self._checkpoint(
                run,
                "xpert_evolution.generation.completed",
                f"Prompt evolution generation {generation_number} completed",
                f"{len(ranking)} candidates ranked",
                metadata={
                    "generation": generation_number,
                    "best_candidate_id": best_id,
                    "best_score": ranking[0]["score"],
                    "repair_used": bool(generation.get("repair_used")),
                },
            )
            if (
                float(previous_summary.get("score") or 0) >= 0.999999
                or float(previous_summary.get("score") or 0)
                <= prior_best_score
            ):
                break

        run = await asyncio.to_thread(self.store.require, run["run_id"])
        finalists = self._select_finalists(run)
        if not finalists:
            await self._finish_no_improvement(
                run, "No generated candidate improved the training baseline."
            )
            return
        validation_eval_id = run.get("validation_evaluation_run_id")
        if not validation_eval_id:
            validation = await asyncio.to_thread(
                self.evaluation_service.create_run_from_snapshots,
                dataset_version=run["dataset"],
                cases=self.service.selected_cases(run, "validation"),
                baseline=baseline["snapshot"],
                candidates=[item["snapshot"] for item in finalists],
                config=self._evaluation_config(run),
                warnings=["Prompt evolution isolated validation holdout."],
            )
            validation_eval_id = validation["run_id"]
            run = await asyncio.to_thread(
                self.store.mutate,
                run["run_id"],
                lambda item: item.update(
                    {
                        "validation_evaluation_run_id": validation_eval_id,
                        "finalists": [
                            {
                                "candidate_id": candidate["candidate_id"],
                                "checksum": candidate["checksum"],
                            }
                            for candidate in finalists
                        ],
                        "phase": "validation",
                    }
                ),
            )
            self.evaluation_executor.wake()
        validation = await self._await_evaluation(run, validation_eval_id)
        gate = self._gate(run, baseline, finalists, validation)
        best = next(
            (
                candidate
                for candidate in finalists
                if candidate["candidate_id"] == gate.get("best_candidate_id")
            ),
            None,
        )
        report = {
            "training_baseline": baseline_summary,
            "validation": copy.deepcopy(validation.get("report") or {}),
            "gate": gate,
        }
        run = await asyncio.to_thread(
            self.store.mutate,
            run["run_id"],
            lambda item: item.update({"report": report, "phase": "gate"}),
        )
        if self.service.is_stale(run):
            stale = await asyncio.to_thread(
                self.store.mutate,
                run["run_id"],
                lambda item: item.update(
                    {
                        "status": "stale",
                        "stale": True,
                        "completed_at": time.time(),
                        "error": "Target draft changed during evolution.",
                    }
                ),
            )
            await self._checkpoint(
                stale,
                "xpert_evolution.stale",
                "Prompt evolution result is stale",
                "Target draft changed during evolution",
                severity="warning",
            )
            await self._finish_registry(stale, "completed")
            return
        if not gate["passed"] or best is None:
            await self._finish_no_improvement(run, gate["reason"])
            return
        proposal = await asyncio.to_thread(self.service.create_proposal, run, best)
        run = await asyncio.to_thread(
            self.store.mutate,
            run["run_id"],
            lambda item: item.update(
                {
                    "status": "completed",
                    "phase": "completed",
                    "proposal_id": proposal.proposal_id,
                    "proposal_revision": proposal.revision,
                    "completed_at": time.time(),
                }
            ),
        )
        await self._checkpoint(
            run,
            "xpert_evolution.completed",
            "Prompt evolution completed",
            f"Proposal {proposal.proposal_id}",
            metadata={
                "candidate_id": best["candidate_id"],
                "proposal_id": proposal.proposal_id,
                "score_delta": gate["score_delta"],
            },
        )
        await self._finish_registry(run, "completed")

    async def _generate_candidates(
        self,
        run: dict[str, Any],
        generation: int,
        previous_best: dict[str, Any],
        previous_summary: dict[str, Any],
        baseline: dict[str, Any],
        prior_evaluation_run_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        train_cases = self.service.selected_cases(run, "train")
        requested = int(run["request"]["population_size"])
        system_prompt = (
            "You optimize assistant prompts under a strict bounded experiment. "
            "Return JSON only. Do not reveal hidden reasoning. Preserve every template "
            "variable exactly. Never copy long evaluation examples, credentials, paths, "
            "or resource identifiers into prompts."
        )
        user_payload = {
            "generation": generation,
            "candidate_count": requested,
            "selected_fields": list(run["target"]["selected_fields"]),
            "original_prompts": baseline["fields"],
            "previous_best_prompts": previous_best["fields"],
            "previous_best_score": previous_summary.get("score"),
            "failure_summary": await self._failure_digest(
                prior_evaluation_run_id
            ),
            "training_cases": [
                {
                    "case_id": case.get("case_id"),
                    "message": str(case.get("message") or "")[:2_000],
                    "expected": case.get("expected") or {},
                }
                for case in train_cases
            ],
            "contract": {
                "candidates": [
                    {
                        "fields": {
                            field: "complete replacement prompt"
                            for field in run["target"]["selected_fields"]
                        },
                        "summary": "public change summary, no hidden reasoning",
                    }
                ]
            },
        }
        raw = await self.optimizer_runner(
            str(run["request"]["optimizer_model_id"]),
            system_prompt,
            json.dumps(user_payload, ensure_ascii=False),
            0.2,
            6_000,
        )
        try:
            return self._parse_generation(raw, requested), False
        except Exception as first_error:
            repair = await self.optimizer_runner(
                str(run["request"]["optimizer_model_id"]),
                system_prompt,
                (
                    "Repair the response to the exact JSON contract. Return JSON only. "
                    f"Validation error: {str(first_error)[:500]}\n\n"
                    f"Invalid response:\n{str(raw)[:12_000]}"
                ),
                0.0,
                6_000,
            )
            return self._parse_generation(repair, requested), True

    async def _failure_digest(self, evaluation_run_id: str) -> list[dict[str, Any]]:
        if not evaluation_run_id:
            return []
        evaluation = await asyncio.to_thread(
            self.evaluation_store.require_run, evaluation_run_id
        )
        digest = []
        for item in evaluation.get("items") or []:
            failed_metrics = [
                {
                    "kind": metric.get("kind"),
                    "score": metric.get("score"),
                    "reason": str(metric.get("reason") or "")[:300],
                }
                for metric in item.get("metrics") or []
                if not metric.get("passed")
            ]
            if (
                item.get("status") == "completed"
                and float(item.get("score") or 0) >= 0.999999
                and not failed_metrics
            ):
                continue
            digest.append(
                {
                    "case_id": item.get("case_id"),
                    "score": item.get("score", 0),
                    "failed_metrics": failed_metrics[:10],
                    "error": str(item.get("error") or "")[:300],
                    "output_excerpt": str(item.get("output") or "")[:500],
                }
            )
        return digest[:50]

    @staticmethod
    def _parse_generation(raw: str, requested: int) -> list[dict[str, Any]]:
        text = str(raw or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise EvolutionStateError("Optimizer did not return a JSON object.")
        payload = json.loads(text[start : end + 1])
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list) or not candidates:
            raise EvolutionStateError("Optimizer candidates must be a non-empty array.")
        result = []
        for item in candidates[:requested]:
            if not isinstance(item, dict) or not isinstance(item.get("fields"), dict):
                raise EvolutionStateError("Each optimizer candidate requires fields.")
            result.append(
                {
                    "fields": dict(item["fields"]),
                    "summary": str(item.get("summary") or "")[:500],
                }
            )
        return result

    async def _await_evaluation(
        self, run: dict[str, Any], evaluation_run_id: str
    ) -> dict[str, Any]:
        while True:
            current = await asyncio.to_thread(
                self.evaluation_store.require_run, evaluation_run_id
            )
            if current.get("status") in {"completed", "cancelled", "failed"}:
                if current.get("status") != "completed":
                    raise EvolutionStateError(
                        "Evaluator run did not complete: "
                        + str(current.get("error") or current.get("status"))
                    )
                return current
            latest = await asyncio.to_thread(self.store.require, run["run_id"])
            if latest.get("cancel_requested"):
                await asyncio.to_thread(
                    self.evaluation_store.cancel_run, evaluation_run_id
                )
                await self._cancelled(latest)
                raise _RunCancelled()
            await asyncio.sleep(self.poll_seconds)

    @staticmethod
    def _target_summary(
        evaluation: dict[str, Any], target_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                copy.deepcopy(item)
                for item in (evaluation.get("report") or {}).get("targets") or []
                if item.get("target_id") == target_id
            ),
            None,
        )

    def _rank_generation(
        self,
        candidates: list[dict[str, Any]],
        evaluation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        summaries = {
            item["target_id"]: item
            for item in (evaluation.get("report") or {}).get("targets") or []
        }
        ranking = []
        for candidate in candidates:
            summary = summaries.get(candidate["snapshot"]["target_id"])
            if not summary:
                continue
            ranking.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "checksum": candidate["checksum"],
                    **copy.deepcopy(summary),
                }
            )
        ranking.sort(
            key=lambda item: (
                -float(item.get("score") or 0),
                int(item.get("failed_count") or 0),
                int(item.get("estimated_tokens") or 0),
                float(item.get("average_latency_ms") or 0),
                str(item.get("checksum") or ""),
            )
        )
        return ranking

    @staticmethod
    def _select_finalists(run: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        scores: dict[str, dict[str, Any]] = {}
        for generation in run.get("generations") or []:
            for candidate in generation.get("candidates") or []:
                candidates[candidate["candidate_id"]] = candidate
            for item in generation.get("ranking") or []:
                scores[item["candidate_id"]] = item
        ordered = sorted(
            scores.values(),
            key=lambda item: (
                -float(item.get("score") or 0),
                int(item.get("failed_count") or 0),
                int(item.get("estimated_tokens") or 0),
                float(item.get("average_latency_ms") or 0),
                str(item.get("checksum") or ""),
            ),
        )
        return [candidates[item["candidate_id"]] for item in ordered[:3]]

    def _gate(
        self,
        run: dict[str, Any],
        baseline: dict[str, Any],
        finalists: list[dict[str, Any]],
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        summaries = {
            item["target_id"]: item
            for item in (evaluation.get("report") or {}).get("targets") or []
        }
        baseline_summary = summaries.get(baseline["snapshot"]["target_id"])
        if not baseline_summary:
            return {"passed": False, "reason": "Validation baseline is missing."}
        ranked = []
        for candidate in finalists:
            summary = summaries.get(candidate["snapshot"]["target_id"])
            if summary:
                ranked.append((candidate, summary))
        ranked.sort(
            key=lambda item: (
                -float(item[1].get("score") or 0),
                int(item[1].get("failed_count") or 0),
                int(item[1].get("estimated_tokens") or 0),
                float(item[1].get("average_latency_ms") or 0),
                item[0]["checksum"],
            )
        )
        if not ranked:
            return {"passed": False, "reason": "No finalist completed validation."}
        candidate, summary = ranked[0]
        score_delta = float(summary.get("score") or 0) - float(
            baseline_summary.get("score") or 0
        )
        regressions = {}
        for name, baseline_score in (baseline_summary.get("metrics") or {}).items():
            delta = float((summary.get("metrics") or {}).get(name, 0)) - float(
                baseline_score
            )
            if delta < -float(run["request"]["max_metric_regression"]):
                regressions[name] = round(delta, 6)
        new_failures = int(summary.get("failed_count") or 0) > int(
            baseline_summary.get("failed_count") or 0
        )
        passed = (
            score_delta >= float(run["request"]["min_score_delta"])
            and not regressions
            and not new_failures
        )
        reasons = []
        if score_delta < float(run["request"]["min_score_delta"]):
            reasons.append("validation score improvement is below the configured gate")
        if regressions:
            reasons.append("one or more weighted metrics regressed")
        if new_failures:
            reasons.append("candidate introduced evaluation failures")
        return {
            "passed": passed,
            "reason": "; ".join(reasons) if reasons else "Non-degradation gate passed.",
            "best_candidate_id": candidate["candidate_id"],
            "baseline_score": baseline_summary.get("score"),
            "candidate_score": summary.get("score"),
            "score_delta": round(score_delta, 6),
            "metric_regressions": regressions,
            "new_failures": new_failures,
        }

    def _evaluation_config(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_policy": run["request"]["model_policy"],
            "override_model_id": run["request"].get("override_model_id"),
            "judge_model_id": run["request"].get("judge_model_id"),
            "seed": int(run["request"].get("seed") or 0),
            "budget": copy.deepcopy(run["request"].get("budget") or {}),
        }

    async def _cancelled(self, run: dict[str, Any]) -> bool:
        if not run.get("cancel_requested"):
            return False
        result = await asyncio.to_thread(
            self.store.mutate,
            run["run_id"],
            lambda item: item.update(
                {
                    "status": "cancelled",
                    "phase": "cancelled",
                    "completed_at": time.time(),
                }
            ),
        )
        await self._checkpoint(
            result,
            "xpert_evolution.cancelled",
            "Prompt evolution cancelled",
            "The evolution run was cancelled before completion",
            severity="warning",
        )
        await self._finish_registry(result, "cancelled")
        return True

    async def _finish_no_improvement(
        self, run: dict[str, Any], reason: str
    ) -> None:
        result = await asyncio.to_thread(
            self.store.mutate,
            run["run_id"],
            lambda item: item.update(
                {
                    "status": "no_improvement",
                    "phase": "completed",
                    "completed_at": time.time(),
                    "error": str(reason)[:500],
                }
            ),
        )
        await self._checkpoint(
            result,
            "xpert_evolution.no_improvement",
            "Prompt evolution found no safe improvement",
            str(reason)[:500],
            severity="warning",
        )
        await self._finish_registry(result, "completed")

    async def _ensure_registry_run(self, run: dict[str, Any]) -> None:
        if self.run_registry is None or run.get("run_registry_id"):
            return
        registry = await self.run_registry.create_run(
            "xpert_evolution",
            f"Prompt evolution {run['target']['name']}",
            status="running",
            source_id=run["run_id"],
            metadata={
                "target_kind": run["target"]["kind"],
                "target_id": run["target"]["target_id"],
                "dataset_id": run["dataset"]["dataset_id"],
                "dataset_version": run["dataset"]["version"],
            },
        )
        await asyncio.to_thread(
            self.store.mutate,
            run["run_id"],
            lambda item: item.update({"run_registry_id": registry.run_id}),
        )

    async def _finish_registry(
        self,
        run: dict[str, Any],
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        if self.run_registry is None:
            return
        registry_id = run.get("run_registry_id")
        if not registry_id and run.get("run_id"):
            latest = await asyncio.to_thread(self.store.require, run["run_id"])
            registry_id = latest.get("run_registry_id")
        if not registry_id:
            return
        await self.run_registry.update_run(
            registry_id,
            status=status,
            error=error,
        )

    async def _checkpoint(
        self,
        run: dict[str, Any],
        event_type: str,
        title: str,
        summary: str,
        *,
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.run_registry is None:
            return
        latest = await asyncio.to_thread(self.store.require, run["run_id"])
        if not latest.get("run_registry_id"):
            return
        await self.run_registry.record_checkpoint(
            latest["run_registry_id"],
            event_type=event_type,
            title=title,
            summary=summary[:500],
            severity=severity,
            metadata=metadata or {},
        )
