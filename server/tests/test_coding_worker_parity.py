from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from coding_worker.parity import (
    ATTEMPTS_PER_ENGINE,
    EXPECTED_RUN_COUNT,
    ParityCertification,
    ParityEngine,
    ParityFailureKind,
    ParityReport,
    ParityRunOutcome,
    load_frozen_manifest,
)
from coding_worker.parity_runner import (
    ParityRunRequest,
    ParityRunnerError,
    SubprocessParityRunner,
    run_parity_matrix,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coding_worker_v16_parity.json"
CANDIDATE = "1" * 40
ROUTE_RECEIPT = "2" * 64
GAP_AUDIT = "3" * 64


def _runs(*, worker_failures: set[str] | None = None) -> tuple[ParityRunOutcome, ...]:
    manifest = load_frozen_manifest(FIXTURE)
    manifest_hash = manifest.canonical_sha256()
    worker_failures = worker_failures or set()
    outcomes: list[ParityRunOutcome] = []
    for engine in ParityEngine:
        for task in manifest.tasks:
            for attempt in range(1, ATTEMPTS_PER_ENGINE + 1):
                failed = (
                    engine is ParityEngine.MODELMIRROR_WORKER
                    and task.task_id in worker_failures
                )
                outcomes.append(
                    ParityRunOutcome(
                        run_id=f"run_{engine.value}_{task.task_id}_{attempt}",
                        task_id=task.task_id,
                        engine=engine,
                        attempt=attempt,
                        engine_version=(
                            "1.18.9"
                            if engine is ParityEngine.NATIVE_OPENCODE
                            else "candidate"
                        ),
                        model_route_receipt_sha256=ROUTE_RECEIPT,
                        candidate_sha=CANDIDATE,
                        task_manifest_sha256=manifest_hash,
                        initial_tree_hash=task.initial_tree_hash,
                        final_tree_hash="f" * 64,
                        hidden_checks_passed=not failed,
                        allowed_diff=True,
                        accepted=not failed,
                        failure_kind=(
                            ParityFailureKind.HIDDEN_CHECK if failed else None
                        ),
                        input_tokens=100,
                        output_tokens=50,
                        tool_calls=10,
                        active_seconds=30,
                    )
                )
    return tuple(outcomes)


def _report(*, worker_failures: set[str] | None = None) -> ParityReport:
    manifest = load_frozen_manifest(FIXTURE)
    return ParityReport(
        report_id="report_round_1",
        candidate_sha=CANDIDATE,
        task_manifest_sha256=manifest.canonical_sha256(),
        latest_opencode_version="latest-audited",
        gap_audit_sha256=GAP_AUDIT,
        gap_audit=("latest capability differences remain explicitly listed",),
        runs=_runs(worker_failures=worker_failures),
    )


def test_frozen_manifest_has_exact_matrix_and_safety_coverage() -> None:
    manifest = load_frozen_manifest(FIXTURE)

    assert len(manifest.tasks) == 24
    assert len({task.task_id for task in manifest.tasks}) == 24
    assert len(_runs()) == EXPECTED_RUN_COUNT == 144
    assert len(manifest.canonical_sha256()) == 64


def test_report_passes_every_quantitative_gate() -> None:
    manifest = load_frozen_manifest(FIXTURE)
    decision = _report().evaluate(manifest)

    assert decision.passed is True
    assert all(metric.passed for metric in decision.metrics)
    assert {metric.metric for metric in decision.metrics} >= {
        "worker_accepted_rate",
        "success_rate_delta",
        "median_token_ratio",
        "median_active_time_ratio",
        "protected_cases",
    }


def test_protected_failure_blocks_report_even_when_total_rate_is_high() -> None:
    manifest = load_frozen_manifest(FIXTURE)
    decision = _report(worker_failures={"repo_binary_protection"}).evaluate(manifest)

    assert decision.passed is False
    protected = next(metric for metric in decision.metrics if metric.metric == "protected_cases")
    assert protected.passed is False


def test_run_cannot_claim_success_after_timeout_or_manual_repair() -> None:
    run = _runs()[0]

    with pytest.raises(ValidationError, match="accepted must reflect"):
        ParityRunOutcome.model_validate(
            {**run.model_dump(mode="json"), "timeout": True}
        )
    with pytest.raises(ValidationError, match="accepted must reflect"):
        ParityRunOutcome.model_validate(
            {**run.model_dump(mode="json"), "manual_repair": True}
        )


def test_report_rejects_duplicate_or_wrong_manifest_cells() -> None:
    report = _report()
    duplicate = (*report.runs[:-1], report.runs[0])

    with pytest.raises(ValidationError, match="duplicate cells"):
        ParityReport.model_validate(
            {**report.model_dump(mode="json"), "runs": duplicate}
        )
    with pytest.raises(ValueError, match="does not match"):
        report.evaluate(
            load_frozen_manifest(FIXTURE).model_copy(
                update={"tasks": tuple(reversed(load_frozen_manifest(FIXTURE).tasks))}
            )
        )


def test_certification_requires_two_passes_for_same_candidate() -> None:
    manifest = load_frozen_manifest(FIXTURE)
    first = _report().evaluate(manifest)

    certification = ParityCertification(first=first, second=first)
    assert certification.second.passed is True

    other = first.model_copy(update={"candidate_sha": "4" * 40})
    with pytest.raises(ValidationError, match="same candidate"):
        ParityCertification(first=first, second=other)


class _RecordingRunner:
    def __init__(self, engine: ParityEngine) -> None:
        self._engine = engine
        self.requests: list[ParityRunRequest] = []

    @property
    def engine(self) -> ParityEngine:
        return self._engine

    def execute(self, request: ParityRunRequest) -> ParityRunOutcome:
        self.requests.append(request)
        return ParityRunOutcome(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=request.engine,
            attempt=request.attempt,
            engine_version=("1.18.9" if request.engine is ParityEngine.NATIVE_OPENCODE else "candidate"),
            model_route_receipt_sha256=request.model_route_receipt_sha256,
            candidate_sha=request.candidate_sha,
            task_manifest_sha256=request.task_manifest_sha256,
            initial_tree_hash=request.initial_tree_hash,
            final_tree_hash="f" * 64,
            hidden_checks_passed=True,
            allowed_diff=True,
            accepted=True,
            input_tokens=100,
            output_tokens=50,
            tool_calls=10,
            active_seconds=30,
        )


def test_matrix_uses_independent_bound_runners_and_randomized_order() -> None:
    manifest = load_frozen_manifest(FIXTURE)
    native = _RecordingRunner(ParityEngine.NATIVE_OPENCODE)
    worker = _RecordingRunner(ParityEngine.MODELMIRROR_WORKER)

    outcomes = run_parity_matrix(
        manifest=manifest,
        runners={
            ParityEngine.NATIVE_OPENCODE: native,
            ParityEngine.MODELMIRROR_WORKER: worker,
        },
        candidate_sha=CANDIDATE,
        model_route_receipt_sha256=ROUTE_RECEIPT,
    )

    assert len(outcomes) == 144
    assert len(native.requests) == len(worker.requests) == 72
    assert [item.task_id for item in outcomes[:6]] != [
        item.task_id for item in manifest.tasks[:6]
    ]
    assert all(request.fixture_id.startswith("fixture_") for request in native.requests)


def test_matrix_rejects_shared_runner_and_foreign_outcome() -> None:
    manifest = load_frozen_manifest(FIXTURE)
    native = _RecordingRunner(ParityEngine.NATIVE_OPENCODE)
    with pytest.raises(ValueError, match="engine bound"):
        run_parity_matrix(
            manifest=manifest,
            runners={
                ParityEngine.NATIVE_OPENCODE: native,
                ParityEngine.MODELMIRROR_WORKER: native,
            },
            candidate_sha=CANDIDATE,
            model_route_receipt_sha256=ROUTE_RECEIPT,
        )

    class _ForeignRunner(_RecordingRunner):
        def execute(self, request: ParityRunRequest) -> ParityRunOutcome:
            outcome = super().execute(request)
            return outcome.model_copy(update={"task_id": "foreign_task"})

    with pytest.raises(ParityRunnerError, match="binding"):
        run_parity_matrix(
            manifest=manifest,
            runners={
                ParityEngine.NATIVE_OPENCODE: native,
                ParityEngine.MODELMIRROR_WORKER: _ForeignRunner(
                    ParityEngine.MODELMIRROR_WORKER
                ),
            },
            candidate_sha=CANDIDATE,
            model_route_receipt_sha256=ROUTE_RECEIPT,
        )


def test_subprocess_runner_accepts_only_exact_json_bound_outcome() -> None:
    task = load_frozen_manifest(FIXTURE).tasks[0]
    request = ParityRunRequest(
        run_id="run_native_opencode_py_multifile_defect_1",
        task_id=task.task_id,
        engine=ParityEngine.NATIVE_OPENCODE,
        attempt=1,
        objective=task.objective,
        fixture_id=task.fixture_id,
        fixture_revision=task.fixture_revision,
        initial_tree_hash=task.initial_tree_hash,
        hidden_check_bundle_id=task.hidden_check_bundle_id,
        hidden_check_sha256=task.hidden_check_sha256,
        budget=task.budget,
        model_route_receipt_sha256=ROUTE_RECEIPT,
        candidate_sha=CANDIDATE,
        task_manifest_sha256=load_frozen_manifest(FIXTURE).canonical_sha256(),
    )
    script = """
import json,sys
r=json.load(sys.stdin)
print(json.dumps({
  'protocol':r['protocol'],'run_id':r['run_id'],'task_id':r['task_id'],
  'engine':r['engine'],'attempt':r['attempt'],'engine_version':'1.18.9',
  'model_route_receipt_sha256':r['model_route_receipt_sha256'],
  'candidate_sha':r['candidate_sha'],'task_manifest_sha256':r['task_manifest_sha256'],
  'initial_tree_hash':r['initial_tree_hash'],'final_tree_hash':'f'*64,
  'hidden_checks_passed':True,'allowed_diff':True,'policy_violations':[],
  'timeout':False,'budget_limited':False,'stuck':False,'manual_repair':False,
  'undeclared_side_effect':False,'accepted':True,'input_tokens':1,
  'output_tokens':1,'tool_calls':1,'active_seconds':1.0,'failure_kind':None
}))
"""
    runner = SubprocessParityRunner(
        engine=ParityEngine.NATIVE_OPENCODE,
        argv=(sys.executable, "-c", script),
        timeout_seconds=30,
    )

    outcome = runner.execute(request)
    assert outcome.task_id == task.task_id
    assert outcome.engine_version == "1.18.9"
