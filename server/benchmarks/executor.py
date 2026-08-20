from __future__ import annotations

import asyncio
import contextlib
import copy
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .service import BenchmarkGenerationError, BenchmarkGenerationService
from .store import BenchmarkJobStore

if False:  # pragma: no cover - type-only import without a runtime cycle.
    from .knowledge_executor import KnowledgeBenchmarkProvisioner
    from .knowledge_generation import KnowledgeBenchmarkGenerationService


@dataclass(frozen=True)
class BenchmarkGeneratorOutput:
    text: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


GeneratorRunner = Callable[
    [str, str, str, float, int],
    Awaitable[str | BenchmarkGeneratorOutput],
]


def _normalize_generator_output(
    value: str | BenchmarkGeneratorOutput,
) -> BenchmarkGeneratorOutput:
    if isinstance(value, BenchmarkGeneratorOutput):
        return value
    return BenchmarkGeneratorOutput(text=str(value or ""))


def _safe_generator_diagnostics(value: dict[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    finish_reason = value.get("finish_reason")
    if isinstance(finish_reason, str):
        diagnostics["finish_reason"] = finish_reason[:80]
    for key in ("content_chars", "reasoning_chars"):
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool):
            diagnostics[key] = max(0, item)
    for key in ("reasoning_present", "contract_found"):
        item = value.get(key)
        if isinstance(item, bool):
            diagnostics[key] = item
    selected_source = value.get("selected_source")
    if selected_source in {"none", "content", "reasoning"}:
        diagnostics["selected_source"] = selected_source
    candidate_keys = value.get("candidate_top_level_keys")
    if isinstance(candidate_keys, list):
        diagnostics["candidate_top_level_keys"] = [
            str(item)[:80] for item in candidate_keys[:20]
        ]
    usage = value.get("usage")
    if isinstance(usage, dict):
        diagnostics["usage"] = {
            key: max(0, int(usage[key]))
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(key), int)
            and not isinstance(usage.get(key), bool)
        }
    return diagnostics


class BenchmarkJobExecutor:
    """Restart-safe generator and calibration coordinator."""

    def __init__(
        self,
        store: BenchmarkJobStore,
        *,
        service: BenchmarkGenerationService,
        generator_runner: GeneratorRunner,
        evaluation_store: Any,
        evaluation_service: Any,
        evaluation_executor: Any,
        knowledge_provisioner: "KnowledgeBenchmarkProvisioner | None" = None,
        knowledge_service: "KnowledgeBenchmarkGenerationService | None" = None,
        rag_evaluation_store: Any | None = None,
        rag_evaluation_executor: Any | None = None,
        poll_seconds: float = 0.5,
        generator_timeout_seconds: float | None = None,
    ) -> None:
        self.store = store
        self.service = service
        self.generator_runner = generator_runner
        self.evaluation_store = evaluation_store
        self.evaluation_service = evaluation_service
        self.evaluation_executor = evaluation_executor
        self.knowledge_provisioner = knowledge_provisioner
        self.knowledge_service = knowledge_service
        self.rag_evaluation_store = rag_evaluation_store
        self.rag_evaluation_executor = rag_evaluation_executor
        self.poll_seconds = max(0.1, float(poll_seconds))
        configured_timeout = (
            generator_timeout_seconds
            if generator_timeout_seconds is not None
            else os.getenv("BENCHMARK_GENERATOR_TIMEOUT_SECONDS", "180")
        )
        self.generator_timeout_seconds = max(0.01, float(configured_timeout))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.store.recover_jobs()
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

    async def _invoke_generator(
        self,
        model_id: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> BenchmarkGeneratorOutput:
        try:
            output = await asyncio.wait_for(
                self.generator_runner(
                    model_id,
                    system,
                    user,
                    temperature,
                    max_tokens,
                ),
                timeout=self.generator_timeout_seconds,
            )
        except TimeoutError as exc:
            timeout = f"{self.generator_timeout_seconds:g}"
            raise BenchmarkGenerationError(
                f"Benchmark generator timed out after {timeout} seconds."
            ) from exc
        return _normalize_generator_output(output)

    async def _loop(self) -> None:
        while not self._stopping:
            progressed = await self._poll_calibrations()
            job = await asyncio.to_thread(self.store.claim_next_job)
            if job is not None:
                progressed = True
                try:
                    if job.get("kind") == "generation":
                        await self._run_generation(job)
                    elif job.get("kind") == "calibration":
                        await self._run_calibration(job)
                    elif (
                        job.get("kind") == "knowledge_instantiation"
                        and self.knowledge_provisioner is not None
                    ):
                        await self.knowledge_provisioner.run(job)
                    else:
                        raise BenchmarkGenerationError(
                            "Benchmark job kind is not configured."
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._fail_job(job, exc)
            if progressed:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _run_generation(self, job: dict[str, Any]) -> None:
        request = dict(job.get("request") or {})
        reference = dict(request.get("target") or {})
        if reference.get("kind") == "knowledge_version":
            await self._run_knowledge_generation(job)
            return
        snapshot, target_warnings = await asyncio.to_thread(
            self.service.snapshot_target, reference
        )
        existing = await asyncio.to_thread(
            self._find_generation_dataset, job["job_id"]
        )
        if existing is not None:
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                status="validating",
                target=self.service.public_target(snapshot),
                dataset_id=existing["dataset_id"],
                dataset_revision=existing["revision"],
                warnings=target_warnings,
            )
            await self._start_evaluation(
                job_id=job["job_id"],
                dataset=existing,
                snapshot=snapshot,
                reference=reference,
            )
            return
        coverage = await asyncio.to_thread(self.service.detect_coverage, snapshot)
        requested_coverage = [
            str(item) for item in list(request.get("coverage") or []) if str(item)
        ]
        selected_coverage = requested_coverage or list(coverage["recommended"])
        unavailable = sorted(set(selected_coverage) - set(coverage["available"]))
        if unavailable:
            raise BenchmarkGenerationError(
                "Requested coverage is unavailable: " + ", ".join(unavailable)
            )
        seeds = await asyncio.to_thread(
            self.service.conversation_seeds,
            list(request.get("conversation_selections") or []),
        )
        system, user, coverage = await asyncio.to_thread(
            self.service.generation_prompt,
            snapshot=snapshot,
            case_count=int(request.get("case_count") or 12),
            locales=list(request.get("locales") or ["zh-CN", "en-US"]),
            requested_coverage=selected_coverage,
            conversation_seeds=seeds,
            seed=int(request.get("seed") or 0),
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            status="generating",
            target=self.service.public_target(snapshot),
            coverage=coverage,
            warnings=target_warnings,
        )
        initial_output = await self._invoke_generator(
            str(request.get("generator_model_id") or ""),
            system,
            user,
            0.2,
            12_000,
        )
        raw = initial_output.text
        generation_attempts = [
            {
                "attempt": "initial",
                "error_code": initial_output.error_code,
                "diagnostics": _safe_generator_diagnostics(
                    initial_output.diagnostics
                ),
            }
        ]
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            generation_attempts=generation_attempts,
        )
        repair_used = False
        try:
            if initial_output.error_code:
                raise BenchmarkGenerationError(
                    initial_output.error_message
                    or f"Generator response failed: {initial_output.error_code}."
                )
            generated = self.service.parse_generated_cases(
                raw,
                expected_count=int(request.get("case_count") or 12),
                allowed_coverage=list(coverage["selected"]),
                allowed_tool_names=list(coverage.get("tool_names") or []),
                allowed_target_anchors=list(coverage.get("target_anchors") or []),
                case_blueprints=list(coverage.get("case_blueprints") or []),
                allowed_document_names=list(
                    coverage.get("knowledge_document_names") or []
                ),
                allowed_prompt_aliases=list(
                    coverage.get("prompt_command_aliases") or []
                ),
            )
        except BenchmarkGenerationError as exc:
            repair_used = True
            repair_system, repair_user = self.service.repair_prompt(
                raw,
                str(exc),
                expected_count=int(request.get("case_count") or 12),
                allowed_coverage=list(coverage["selected"]),
                target_anchors=list(coverage.get("target_anchors") or []),
                case_blueprints=list(coverage.get("case_blueprints") or []),
                allowed_document_names=list(
                    coverage.get("knowledge_document_names") or []
                ),
                allowed_prompt_aliases=list(
                    coverage.get("prompt_command_aliases") or []
                ),
            )
            repaired_output = await self._invoke_generator(
                str(request.get("generator_model_id") or ""),
                repair_system,
                repair_user,
                0.0,
                12_000,
            )
            generation_attempts.append(
                {
                    "attempt": "repair",
                    "error_code": repaired_output.error_code,
                    "diagnostics": _safe_generator_diagnostics(
                        repaired_output.diagnostics
                    ),
                }
            )
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                generation_attempts=generation_attempts,
            )
            if repaired_output.error_code:
                raise BenchmarkGenerationError(
                    repaired_output.error_message
                    or f"Generator repair failed: {repaired_output.error_code}."
                )
            repaired = repaired_output.text
            generated = self.service.parse_generated_cases(
                repaired,
                expected_count=int(request.get("case_count") or 12),
                allowed_coverage=list(coverage["selected"]),
                allowed_tool_names=list(coverage.get("tool_names") or []),
                allowed_target_anchors=list(coverage.get("target_anchors") or []),
                case_blueprints=list(coverage.get("case_blueprints") or []),
                allowed_document_names=list(
                    coverage.get("knowledge_document_names") or []
                ),
                allowed_prompt_aliases=list(
                    coverage.get("prompt_command_aliases") or []
                ),
            )
        if (await asyncio.to_thread(self.store.require_job, job["job_id"])).get(
            "cancel_requested"
        ):
            await asyncio.to_thread(
                self.store.update_job, job["job_id"], status="cancelled"
            )
            return
        dataset = await asyncio.to_thread(
            self.service.evaluation_store.create_generated_dataset,
            name=generated["name"],
            description=generated["description"],
            cases=generated["cases"],
            provenance={
                "generator": "modelmirror-targeted-benchmark-v2",
                "generation_job_id": job["job_id"],
                "generator_model_id": str(request.get("generator_model_id") or "")[:300],
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
                "seed": int(request.get("seed") or 0),
                "repair_used": repair_used,
                "attempt_diagnostics": copy.deepcopy(generation_attempts),
                "conversation_seed_count": len(seeds),
                "targeting_contract_version": 5,
                "target_anchor_hash": str(coverage.get("target_anchor_hash") or ""),
                "target_anchor_count": len(coverage.get("target_anchors") or []),
                "case_blueprint_hash": str(coverage.get("case_blueprint_hash") or ""),
                "case_blueprint_count": len(coverage.get("case_blueprints") or []),
            },
            coverage={
                "selected": list(coverage["selected"]),
                "available": list(coverage["available"]),
                "locales": list(request.get("locales") or ["zh-CN", "en-US"]),
                "target_anchors": copy.deepcopy(coverage.get("target_anchors") or []),
                "target_anchor_hash": str(coverage.get("target_anchor_hash") or ""),
                "difficulty_policy": {
                    "basic_max_ratio": 0.30,
                    "edge_min_ratio": 0.25,
                    "adversarial_min_ratio": 0.25,
                },
                "matrix_policy": {
                    "combined_case_min_ratio": 0.60,
                    "professional_focus_required_when_available": True,
                },
                "case_blueprint_hash": str(coverage.get("case_blueprint_hash") or ""),
                "case_blueprint_count": len(coverage.get("case_blueprints") or []),
            },
            calibration={
                "status": "pending",
                "dataset_revision": 1,
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
            },
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            status="validating",
            generation={
                "case_count": len(generated["cases"]),
                "repair_used": repair_used,
                "attempt_diagnostics": copy.deepcopy(generation_attempts),
                "assumptions": generated["assumptions"],
                "targeting": copy.deepcopy(generated.get("targeting") or {}),
            },
            dataset_id=dataset["dataset_id"],
            dataset_revision=dataset["revision"],
        )
        await self._start_evaluation(
            job_id=job["job_id"],
            dataset=dataset,
            snapshot=snapshot,
            reference=reference,
        )

    async def _run_calibration(self, job: dict[str, Any]) -> None:
        request = dict(job.get("request") or {})
        if request.get("calibration_runtime") == "knowledge" or dict(
            request.get("target") or {}
        ).get("kind") == "knowledge_version":
            await self._run_knowledge_calibration(job)
            return
        dataset = await asyncio.to_thread(
            self.service.evaluation_store.require_dataset,
            str(request.get("dataset_id") or ""),
        )
        expected_revision = int(request.get("dataset_revision") or 0)
        if int(dataset.get("revision") or 0) != expected_revision:
            raise BenchmarkGenerationError(
                "Dataset changed before calibration started."
            )
        reference = dict(request.get("target") or {})
        if not reference:
            reference = dict((dataset.get("provenance") or {}).get("target_reference") or {})
        if not reference:
            raise BenchmarkGenerationError("Calibration target is required.")
        snapshot, warnings = await asyncio.to_thread(
            self.service.snapshot_target, reference
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            target=self.service.public_target(snapshot),
            warnings=warnings,
        )
        await self._start_evaluation(
            job_id=job["job_id"],
            dataset=dataset,
            snapshot=snapshot,
            reference=reference,
        )

    async def _start_evaluation(
        self,
        *,
        job_id: str,
        dataset: dict[str, Any],
        snapshot: dict[str, Any],
        reference: dict[str, Any],
    ) -> None:
        revision = int(dataset["revision"])
        evaluation_dataset = {
            "dataset_id": dataset["dataset_id"],
            "version": 0,
            "draft_revision": revision,
            "name": dataset["name"],
            "description": dataset.get("description") or "",
            "cases": copy.deepcopy(dataset.get("cases") or []),
            "case_count": len(dataset.get("cases") or []),
            "origin": dataset.get("origin") or "generated",
            "provenance": copy.deepcopy(dataset.get("provenance") or {}),
            "coverage": copy.deepcopy(dataset.get("coverage") or {}),
            "calibration": copy.deepcopy(dataset.get("calibration") or {}),
        }
        generic_counterfactual = (
            self.service.generic_counterfactual_snapshot(snapshot)
            if hasattr(self.service, "generic_counterfactual_snapshot")
            else None
        )
        run = await asyncio.to_thread(
            self.evaluation_service.create_run_from_snapshots,
            dataset_version=evaluation_dataset,
            cases=list(evaluation_dataset["cases"]),
            baseline=generic_counterfactual,
            candidates=[snapshot],
            config={
                "model_policy": "snapshot",
                "override_model_id": None,
                "judge_model_id": None,
                "seed": int((dataset.get("provenance") or {}).get("seed") or 0),
                "budget": {
                    "repetitions": 1,
                    "max_concurrency": 2,
                    "case_timeout_seconds": 120,
                    "max_model_calls": 16,
                    "max_tool_calls": 24,
                    "max_estimated_tokens": 64_000,
                    "max_output_chars": 20_000,
                },
            },
            warnings=[
                "Calibration verifies scoring executability and never rewrites Gold.",
                "Target specificity is measured against a same-model generic counterfactual.",
            ],
        )
        await asyncio.to_thread(
            self.service.evaluation_store.set_dataset_calibration,
            dataset["dataset_id"],
            revision=revision,
            calibration={
                "status": "pending",
                "dataset_revision": revision,
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
                "evaluation_run_id": run["run_id"],
            },
        )
        await asyncio.to_thread(
            self.store.update_job,
            job_id,
            status="calibrating",
            dataset_id=dataset["dataset_id"],
            dataset_revision=revision,
            evaluation_run_id=run["run_id"],
        )
        self.evaluation_executor.wake()

    async def _poll_calibrations(self) -> bool:
        progressed = False
        jobs = await asyncio.to_thread(
            self.store.list_jobs, status="calibrating", limit=200
        )
        for job in jobs:
            if str(job.get("calibration_runtime") or "") == "knowledge":
                if await self._poll_knowledge_calibration(job):
                    progressed = True
                continue
            run_id = str(job.get("evaluation_run_id") or "")
            if not run_id:
                await self._fail_job(job, BenchmarkGenerationError("Calibration run missing."))
                progressed = True
                continue
            current_job = await asyncio.to_thread(self.store.require_job, job["job_id"])
            if current_job.get("cancel_requested"):
                await asyncio.to_thread(self.evaluation_store.cancel_run, run_id)
                await asyncio.to_thread(
                    self.store.update_job, job["job_id"], status="cancelled"
                )
                progressed = True
                continue
            run = await asyncio.to_thread(self.evaluation_service.run_detail, run_id)
            if run.get("status") not in {"completed", "failed", "cancelled"}:
                continue
            dataset = await asyncio.to_thread(
                self.service.evaluation_store.require_dataset,
                str(job.get("dataset_id") or ""),
            )
            target = dict(job.get("target") or {})
            reference = dict((target.get("source") or {}))
            request_reference = dict((job.get("request") or {}).get("target") or {})
            if request_reference:
                reference = request_reference
            result = await asyncio.to_thread(
                self.service.calibration_result,
                dataset=dataset,
                evaluation_run=run,
                target_reference=reference,
                target_checksum=str(target.get("checksum") or ""),
            )
            await asyncio.to_thread(
                self.service.evaluation_store.set_dataset_calibration,
                dataset["dataset_id"],
                revision=int(job.get("dataset_revision") or 0),
                calibration=result,
            )
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                status="completed",
                calibration=result,
            )
            progressed = True
        return progressed

    async def _fail_job(self, job: dict[str, Any], exc: Exception) -> None:
        error = str(exc)[:1_000]
        dataset_id = str(job.get("dataset_id") or "")
        revision = int(job.get("dataset_revision") or 0)
        request = dict(job.get("request") or {})
        knowledge_job = (
            str(job.get("calibration_runtime") or "") == "knowledge"
            or dict(request.get("target") or {}).get("kind") == "knowledge_version"
        )
        if knowledge_job and dataset_id and revision and self.rag_evaluation_store is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.rag_evaluation_store.set_calibration,
                    dataset_id,
                    expected_revision=revision,
                    calibration={
                        "status": "failed",
                        "dataset_revision": revision,
                        "reason": error,
                    },
                )
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                status="failed",
                error=error,
            )
            return
        if dataset_id and revision:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.service.evaluation_store.set_dataset_calibration,
                    dataset_id,
                    revision=revision,
                    calibration={"status": "failed", "reason": error},
                )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            status="failed",
            error=error,
        )

    def _find_generation_dataset(self, job_id: str) -> dict[str, Any] | None:
        for item in self.service.evaluation_store.list_datasets():
            if str(item.get("origin") or "") != "generated":
                continue
            provenance = dict(item.get("provenance") or {})
            if str(provenance.get("generation_job_id") or "") == job_id:
                return self.service.evaluation_store.require_dataset(
                    str(item["dataset_id"])
                )
        return None

    async def _run_knowledge_generation(self, job: dict[str, Any]) -> None:
        if (
            self.knowledge_service is None
            or self.rag_evaluation_store is None
            or self.rag_evaluation_executor is None
        ):
            raise BenchmarkGenerationError("Knowledge Benchmark generation is not configured.")
        request = dict(job.get("request") or {})
        reference = dict(request.get("target") or {})
        snapshot, target_warnings = await asyncio.to_thread(
            self.knowledge_service.snapshot_target, reference
        )
        existing = await asyncio.to_thread(
            self._find_knowledge_generation_set,
            job["job_id"],
            str(snapshot["kb_id"]),
        )
        if existing is not None:
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                status="validating",
                target=self.knowledge_service.public_target(snapshot),
                dataset_id=existing["eval_set_id"],
                dataset_revision=existing["revision"],
                warnings=target_warnings,
            )
            await self._start_knowledge_evaluation(
                job_id=job["job_id"],
                dataset=existing,
                snapshot=snapshot,
                reference=reference,
            )
            return

        context = await asyncio.to_thread(
            self.knowledge_service.prepare_generation,
            snapshot=snapshot,
            case_count=int(request.get("case_count") or 12),
            locales=list(request.get("locales") or ["zh-CN", "en-US"]),
            requested_coverage=list(request.get("coverage") or []),
            no_result_count=int(request.get("no_result_count") or 0),
            seed=int(request.get("seed") or 0),
        )
        system, user = await asyncio.to_thread(
            self.knowledge_service.generation_prompt,
            snapshot=snapshot,
            context=context,
            case_count=int(request.get("case_count") or 12),
            locales=list(request.get("locales") or ["zh-CN", "en-US"]),
            seed=int(request.get("seed") or 0),
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            status="generating",
            target=self.knowledge_service.public_target(snapshot),
            coverage={
                "selected": list(context["selected_coverage"]),
                "available": list(context["available_coverage"]),
                "locales": list(request.get("locales") or ["zh-CN", "en-US"]),
                "sampled_evidence_count": len(context["evidence"]),
                "estimated_context_chars": sum(len(item["text"]) for item in context["evidence"]),
            },
            warnings=target_warnings,
        )
        initial = await self._invoke_generator(
            str(request.get("generator_model_id") or ""),
            system,
            user,
            0.2,
            16_000 if int(request.get("case_count") or 12) > 30 else 12_000,
        )
        attempts = [{
            "attempt": "initial",
            "error_code": initial.error_code,
            "diagnostics": _safe_generator_diagnostics(initial.diagnostics),
        }]
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            generation_attempts=attempts,
        )
        repair_used = False
        try:
            if initial.error_code:
                raise BenchmarkGenerationError(
                    initial.error_message or "Knowledge Benchmark generation failed."
                )
            generated = self.knowledge_service.parse_generated_cases(
                initial.text,
                snapshot=snapshot,
                context=context,
                expected_count=int(request.get("case_count") or 12),
            )
        except BenchmarkGenerationError as exc:
            repair_used = True
            repair_system, repair_user = self.knowledge_service.repair_prompt(
                initial.text,
                str(exc),
                snapshot=snapshot,
                context=context,
                case_count=int(request.get("case_count") or 12),
                locales=list(request.get("locales") or ["zh-CN", "en-US"]),
                seed=int(request.get("seed") or 0),
            )
            repaired = await self._invoke_generator(
                str(request.get("generator_model_id") or ""),
                repair_system,
                repair_user,
                0.0,
                16_000 if int(request.get("case_count") or 12) > 30 else 12_000,
            )
            attempts.append({
                "attempt": "repair",
                "error_code": repaired.error_code,
                "diagnostics": _safe_generator_diagnostics(repaired.diagnostics),
            })
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                generation_attempts=attempts,
            )
            if repaired.error_code:
                raise BenchmarkGenerationError(
                    repaired.error_message or "Knowledge Benchmark repair failed."
                )
            generated = self.knowledge_service.parse_generated_cases(
                repaired.text,
                snapshot=snapshot,
                context=context,
                expected_count=int(request.get("case_count") or 12),
            )
        current = await asyncio.to_thread(self.store.require_job, job["job_id"])
        if current.get("cancel_requested"):
            await asyncio.to_thread(self.store.update_job, job["job_id"], status="cancelled")
            return
        dataset = await asyncio.to_thread(
            self.rag_evaluation_store.create_generated_set,
            str(snapshot["kb_id"]),
            generated["name"],
            generated["description"],
            cases=generated["cases"],
            provenance={
                "generator": "modelmirror-targeted-rag-benchmark-v1",
                "generation_purpose": str(
                    request.get("generation_purpose") or "general"
                ),
                "generation_job_id": job["job_id"],
                "generator_model_id": str(request.get("generator_model_id") or "")[:300],
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
                "pipeline_version_id": snapshot["pipeline_version_id"],
                "document_ids": [item["document_id"] for item in snapshot["documents"]],
                "source_summary_hash": snapshot["source_summary_hash"],
                "evidence_hash": context["evidence_hash"],
                "blueprint_hash": context["blueprint_hash"],
                "seed": int(request.get("seed") or 0),
                "repair_used": repair_used,
                "generation_attempts": copy.deepcopy(attempts),
            },
            coverage={
                "selected": list(context["selected_coverage"]),
                "available": list(context["available_coverage"]),
                "locales": list(request.get("locales") or ["zh-CN", "en-US"]),
                "targeting": copy.deepcopy(generated.get("targeting") or []),
                "generation_purpose": str(
                    request.get("generation_purpose") or "general"
                ),
            },
            calibration={
                "status": "pending",
                "dataset_revision": 1,
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
            },
            benchmark_role=(
                "promotion_evidence"
                if str(request.get("generation_purpose") or "general")
                == "strategy_tuning"
                else "strategy_tuning"
            ),
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            status="validating",
            generation={
                "case_count": len(generated["cases"]),
                "generation_purpose": str(
                    request.get("generation_purpose") or "general"
                ),
                "repair_used": repair_used,
                "attempt_diagnostics": copy.deepcopy(attempts),
                "assumptions": generated["assumptions"],
                "targeting": copy.deepcopy(generated.get("targeting") or []),
            },
            dataset_id=dataset["eval_set_id"],
            dataset_revision=dataset["revision"],
        )
        pending_reviews = [
            case
            for case in dataset.get("cases") or []
            if case.get("expected_no_result")
            and str(case.get("review_status") or "pending") != "approved"
        ]
        if pending_reviews:
            calibration = {
                "status": "awaiting_review",
                "dataset_revision": int(dataset["revision"]),
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
                "pending_review_count": len(pending_reviews),
                "reason": (
                    "Approve every corpus-near no-result case before starting "
                    "real retrieval calibration."
                ),
            }
            await asyncio.to_thread(
                self.rag_evaluation_store.set_calibration,
                dataset["eval_set_id"],
                expected_revision=int(dataset["revision"]),
                calibration=calibration,
            )
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                status="completed",
                calibration=calibration,
            )
            return
        await self._start_knowledge_evaluation(
            job_id=job["job_id"],
            dataset=dataset,
            snapshot=snapshot,
            reference=reference,
        )

    async def _run_knowledge_calibration(self, job: dict[str, Any]) -> None:
        if self.knowledge_service is None or self.rag_evaluation_store is None:
            raise BenchmarkGenerationError("Knowledge Benchmark calibration is not configured.")
        request = dict(job.get("request") or {})
        dataset = await asyncio.to_thread(
            self.rag_evaluation_store.get_set,
            str(request.get("dataset_id") or ""),
        )
        revision = int(request.get("dataset_revision") or 0)
        if int(dataset.get("revision") or 0) != revision:
            raise BenchmarkGenerationError("Evaluation set changed before calibration started.")
        reference = dict(request.get("target") or {}) or dict(
            (dataset.get("provenance") or {}).get("target_reference") or {}
        )
        snapshot, warnings = await asyncio.to_thread(
            self.knowledge_service.snapshot_target, reference
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            target=self.knowledge_service.public_target(snapshot),
            warnings=warnings,
        )
        await self._start_knowledge_evaluation(
            job_id=job["job_id"],
            dataset=dataset,
            snapshot=snapshot,
            reference=reference,
        )

    async def _start_knowledge_evaluation(
        self,
        *,
        job_id: str,
        dataset: dict[str, Any],
        snapshot: dict[str, Any],
        reference: dict[str, Any],
    ) -> None:
        revision = int(dataset["revision"])
        version_id = str(snapshot["pipeline_version_id"])
        run = await asyncio.to_thread(
            self.rag_evaluation_store.create_run,
            evaluation_set=dataset,
            targets=[{
                "target_id": version_id,
                "version_id": version_id,
                "version": int(snapshot["version"]),
                "label": snapshot["label"],
                "retrieval": copy.deepcopy(snapshot.get("retrieval_profile") or {}),
            }],
            baseline_version_id=None,
            ks=[1, 3, 5, 10],
            gate_policy=self.rag_evaluation_store.get_gate_policy(str(snapshot["kb_id"])),
        )
        await asyncio.to_thread(
            self.rag_evaluation_store.set_calibration,
            dataset["eval_set_id"],
            expected_revision=revision,
            calibration={
                "status": "pending",
                "dataset_revision": revision,
                "target_reference": copy.deepcopy(reference),
                "target_checksum": snapshot["checksum"],
                "evaluation_run_id": run["run_id"],
            },
        )
        await asyncio.to_thread(
            self.store.update_job,
            job_id,
            status="calibrating",
            dataset_id=dataset["eval_set_id"],
            dataset_revision=revision,
            evaluation_run_id=run["run_id"],
            target=self.knowledge_service.public_target(snapshot),
            calibration_runtime="knowledge",
        )
        self.rag_evaluation_executor.notify()

    async def _poll_knowledge_calibration(self, job: dict[str, Any]) -> bool:
        run_id = str(job.get("evaluation_run_id") or "")
        if not run_id:
            await self._fail_job(job, BenchmarkGenerationError("Knowledge calibration run missing."))
            return True
        current_job = await asyncio.to_thread(self.store.require_job, job["job_id"])
        if current_job.get("cancel_requested"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.rag_evaluation_store.request_cancel, run_id)
            dataset_id = str(job.get("dataset_id") or "")
            revision = int(job.get("dataset_revision") or 0)
            if dataset_id and revision:
                await asyncio.to_thread(
                    self.rag_evaluation_store.set_calibration,
                    dataset_id,
                    expected_revision=revision,
                    calibration={
                        "status": "failed",
                        "dataset_revision": revision,
                        "reason": "Calibration cancelled; run calibration again before publishing.",
                    },
                )
            await asyncio.to_thread(self.store.update_job, job["job_id"], status="cancelled")
            return True
        run = await asyncio.to_thread(self.rag_evaluation_store.get_run, run_id)
        if run.get("status") not in {"succeeded", "failed", "cancelled"}:
            return False
        dataset = await asyncio.to_thread(
            self.rag_evaluation_store.get_set,
            str(job.get("dataset_id") or ""),
        )
        expected_revision = int(job.get("dataset_revision") or 0)
        if int(dataset.get("revision") or 0) != expected_revision:
            result = {
                "status": "stale",
                "dataset_revision": int(dataset.get("revision") or 0),
                "evaluation_run_id": run.get("run_id"),
                "reason": "Evaluation set changed while calibration was running.",
            }
            await asyncio.to_thread(
                self.store.update_job,
                job["job_id"],
                status="completed",
                calibration=result,
            )
            return True
        result = self._knowledge_calibration_result(dataset=dataset, run=run, job=job)
        await asyncio.to_thread(
            self.rag_evaluation_store.set_calibration,
            dataset["eval_set_id"],
            expected_revision=int(job.get("dataset_revision") or 0),
            calibration=result,
        )
        await asyncio.to_thread(
            self.store.update_job,
            job["job_id"],
            status="failed" if result["status"] == "failed" else "completed",
            calibration=result,
            error=result.get("reason") if result["status"] == "failed" else None,
        )
        return True

    @staticmethod
    def _knowledge_calibration_result(
        *, dataset: dict[str, Any], run: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        revision = int(job.get("dataset_revision") or 0)
        if run.get("status") != "succeeded":
            return {
                "status": "failed",
                "dataset_revision": revision,
                "evaluation_run_id": run.get("run_id"),
                "reason": str(run.get("error") or f"Calibration run {run.get('status')}.")[:500],
            }
        target_id = str(dict(job.get("target") or {}).get("pipeline_version_id") or "")
        case_map = dict((run.get("case_results") or {}).get(target_id) or {})
        diagnostics: list[dict[str, Any]] = []
        easy = missed = no_result_false_positive = failures = positive_count = no_result_count = 0
        for case in dataset.get("cases") or []:
            case_id = str(case.get("case_id") or "")
            item = dict(case_map.get(case_id) or {})
            if item.get("status") != "completed":
                failures += 1
                bucket = "failed"
                rank = None
            elif case.get("expected_no_result"):
                no_result_count += 1
                false_positive = float(item.get("metrics", {}).get("false_positive_rate", 0.0)) > 0
                no_result_false_positive += int(false_positive)
                bucket = "false_positive" if false_positive else "valid"
                rank = None
            else:
                positive_count += 1
                ranks = [
                    int(row.get("rank") or 0)
                    for row in item.get("ranking") or []
                    if int(row.get("relevance") or 0) > 0
                ]
                rank = min(ranks) if ranks else None
                if rank == 1:
                    easy += 1
                    bucket = "too_easy"
                elif rank is not None and rank <= 5:
                    bucket = "effective"
                elif rank is not None and rank <= 10:
                    bucket = "difficult"
                else:
                    missed += 1
                    bucket = "needs_review"
            diagnostics.append({"case_id": case_id, "best_gold_rank": rank, "bucket": bucket})
        if failures:
            status = "failed"
            reason = f"{failures} calibration cases failed to execute."
        else:
            easy_ratio = easy / positive_count if positive_count else 0.0
            missed_ratio = missed / positive_count if positive_count else 0.0
            no_result_fp_ratio = (
                no_result_false_positive / no_result_count if no_result_count else 0.0
            )
            warning_reasons = []
            if easy_ratio >= 0.8:
                warning_reasons.append("At least 80% of positive cases rank Gold first.")
            if missed_ratio > 0.4:
                warning_reasons.append("More than 40% of positive cases miss Gold in Top-10.")
            if no_result_fp_ratio > 0.2:
                warning_reasons.append("More than 20% of no-result cases return false positives.")
            status = "warning" if warning_reasons else "calibrated"
            reason = " ".join(warning_reasons)
        return {
            "status": status,
            "dataset_revision": revision,
            "evaluation_run_id": run.get("run_id"),
            "target_checksum": str(dict(job.get("target") or {}).get("checksum") or ""),
            "reason": reason,
            "counts": {
                "positive": positive_count,
                "no_result": no_result_count,
                "too_easy": easy,
                "top_10_missed": missed,
                "no_result_false_positive": no_result_false_positive,
                "failed": failures,
            },
            "case_diagnostics": diagnostics,
        }

    def _find_knowledge_generation_set(
        self, job_id: str, kb_id: str
    ) -> dict[str, Any] | None:
        if self.rag_evaluation_store is None:
            return None
        for item in self.rag_evaluation_store.list_sets(kb_id):
            if str(item.get("origin") or "") != "generated":
                continue
            provenance = dict(item.get("provenance") or {})
            if str(provenance.get("generation_job_id") or "") == job_id:
                return self.rag_evaluation_store.get_set(str(item["eval_set_id"]))
        return None
