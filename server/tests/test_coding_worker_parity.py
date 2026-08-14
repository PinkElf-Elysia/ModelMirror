from __future__ import annotations

import sys
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.coding_worker.parity import (
    ATTEMPTS_PER_ENGINE,
    EXPECTED_RUN_COUNT,
    ParityCertification,
    ParityEngine,
    ParityFailureKind,
    ParityReport,
    ParityRunOutcome,
    load_frozen_manifest,
    load_parity_report,
    load_public_fixture_bundle,
    parity_artifact_ledger_sha256,
)
from server.coding_worker.parity_runner import (
    ParityRunRequest,
    ParityRunnerError,
    SubprocessParityRunner,
    run_parity_matrix,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coding_worker_v16_parity.json"
ASSETS = Path(__file__).parent / "fixtures" / "coding_worker_v17_parity_assets.json"
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
                run_id = f"run_{engine.value}_{task.task_id}_{attempt}"
                outcomes.append(
                    ParityRunOutcome(
                        run_id=run_id,
                        task_id=task.task_id,
                        engine=engine,
                        attempt=attempt,
                        engine_version=(
                            "1.18.9"
                            if engine is ParityEngine.NATIVE_OPENCODE
                            else "candidate"
                        ),
                        model_route_receipt_sha256=ROUTE_RECEIPT,
                        fixture_bundle_sha256=manifest.fixture_bundle_sha256,
                        hidden_checker_bundle_sha256=(
                            manifest.hidden_checker_bundle_sha256
                        ),
                        runner_image_digest=(
                            manifest.runner_images.native_opencode
                            if engine is ParityEngine.NATIVE_OPENCODE
                            else manifest.runner_images.modelmirror_worker
                        ),
                        raw_artifact_manifest_sha256=hashlib.sha256(
                            run_id.encode("utf-8")
                        ).hexdigest(),
                        checker_receipt_sha256=hashlib.sha256(
                            f"checker:{run_id}".encode("utf-8")
                        ).hexdigest(),
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
    runs = _runs(worker_failures=worker_failures)
    return ParityReport(
        report_id="report_round_1",
        round_id="round_1",
        candidate_sha=CANDIDATE,
        task_manifest_sha256=manifest.canonical_sha256(),
        fixture_bundle_sha256=manifest.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=manifest.hidden_checker_bundle_sha256,
        model_route_receipt_sha256=ROUTE_RECEIPT,
        runner_images=manifest.runner_images,
        raw_artifact_manifest_sha256=parity_artifact_ledger_sha256(runs),
        latest_opencode_version="latest-audited",
        latest_opencode_audit_sha256="4" * 64,
        gap_audit_sha256=GAP_AUDIT,
        gap_audit=("latest capability differences remain explicitly listed",),
        platform_coordination_failures=0,
        duplicate_side_effects=0,
        unsettled_operations=0,
        orphaned_interactions=0,
        runs=runs,
    )


def test_frozen_manifest_has_exact_matrix_and_safety_coverage() -> None:
    manifest = load_frozen_manifest(FIXTURE)
    assets = load_public_fixture_bundle(ASSETS)

    assert len(manifest.tasks) == 24
    assert len({task.task_id for task in manifest.tasks}) == 24
    assert len(_runs()) == EXPECTED_RUN_COUNT == 144
    assert len(manifest.canonical_sha256()) == 64
    assert assets.canonical_sha256() == manifest.fixture_bundle_sha256
    assert {item.fixture_id for item in assets.fixtures} == {
        item.fixture_id for item in manifest.tasks
    }


def test_public_fixture_tampering_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(ASSETS.read_text(encoding="utf-8"))
    payload["fixtures"][0]["files"][0]["content"] += "tampered"
    path = tmp_path / "tampered-assets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="fixture (revision|tree hash)"):
        load_public_fixture_bundle(path)


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
    second = first.model_copy(update={"round_id": "round_2"})

    certification = ParityCertification(first=first, second=second)
    assert certification.second.passed is True

    with pytest.raises(ValidationError, match="independently identified"):
        ParityCertification(first=first, second=first)

    other = second.model_copy(update={"candidate_sha": "4" * 40})
    with pytest.raises(ValidationError, match="certified bindings"):
        ParityCertification(first=first, second=other)


def test_v1_report_is_read_only_and_cannot_be_certified(tmp_path: Path) -> None:
    report = _report().model_dump(mode="json")
    report.pop("protocol")
    for run in report["runs"]:
        run["protocol"] = "modelmirror-coding-parity/v1"
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    legacy = load_parity_report(path)
    assert legacy.protocol == "modelmirror-coding-parity/v1"
    assert legacy.read_only is True
    with pytest.raises(ValidationError):
        ParityCertification(first=legacy, second=legacy)


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
            fixture_bundle_sha256=request.fixture_bundle_sha256,
            hidden_checker_bundle_sha256=request.hidden_checker_bundle_sha256,
            runner_image_digest=request.runner_image_digest,
            raw_artifact_manifest_sha256=hashlib.sha256(
                request.run_id.encode("utf-8")
            ).hexdigest(),
            checker_receipt_sha256=hashlib.sha256(
                f"checker:{request.run_id}".encode("utf-8")
            ).hexdigest(),
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
        round_id="round_1",
    )

    assert len(outcomes) == 144
    assert len(native.requests) == len(worker.requests) == 72
    assert [item.task_id for item in outcomes[:6]] != [
        item.task_id for item in manifest.tasks[:6]
    ]
    assert all(request.fixture_id.startswith("fixture_") for request in native.requests)

    second_native = _RecordingRunner(ParityEngine.NATIVE_OPENCODE)
    second_worker = _RecordingRunner(ParityEngine.MODELMIRROR_WORKER)
    second = run_parity_matrix(
        manifest=manifest,
        runners={
            ParityEngine.NATIVE_OPENCODE: second_native,
            ParityEngine.MODELMIRROR_WORKER: second_worker,
        },
        candidate_sha=CANDIDATE,
        model_route_receipt_sha256=ROUTE_RECEIPT,
        round_id="round_2",
    )
    assert {item.run_id for item in outcomes}.isdisjoint(
        {item.run_id for item in second}
    )


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
            round_id="round_1",
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
            round_id="round_1",
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
        fixture_bundle_sha256=load_frozen_manifest(FIXTURE).fixture_bundle_sha256,
        hidden_checker_bundle_sha256=(
            load_frozen_manifest(FIXTURE).hidden_checker_bundle_sha256
        ),
        runner_image_digest=(
            load_frozen_manifest(FIXTURE).runner_images.native_opencode
        ),
        model_route_catalog_sha256=(
            load_frozen_manifest(FIXTURE).model_route_catalog_sha256
        ),
        model_route=load_frozen_manifest(FIXTURE).model_route,
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
  'fixture_bundle_sha256':r['fixture_bundle_sha256'],
  'hidden_checker_bundle_sha256':r['hidden_checker_bundle_sha256'],
  'runner_image_digest':r['runner_image_digest'],
  'raw_artifact_manifest_sha256':'a'*64,
  'checker_receipt_sha256':'b'*64,
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
