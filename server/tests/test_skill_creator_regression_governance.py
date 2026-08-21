from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from server.skills.creator_evaluation import (
    SkillEvaluationStore,
    SkillEvaluationValidationError,
    aggregate_skill_evaluation_report,
)
from server.skills.creator_evaluation_service import SkillCreatorEvaluationService
from server.skills.creator_store import SkillCreatorStorageError
from server.skills.package_validation import compute_package_digest


def _package(label: str) -> dict:
    markdown = (
        "---\n"
        "name: regression-demo\n"
        "description: Use when a regression comparison is required.\n"
        "---\n\n"
        f"# Regression demo\n\n{label}\n"
    )
    return {
        "name": "regression-demo",
        "slug": "regression-demo",
        "description": "Use when a regression comparison is required.",
        "skill_markdown": markdown,
        "files": {},
    }


def _digest(package: dict) -> str:
    return compute_package_digest(package["skill_markdown"], package["files"])


def _case(index: int) -> dict:
    return {
        "case_id": f"case-{index}",
        "name": f"Case {index}",
        "prompt": f"Complete case {index}",
        "expected_behavior": "Return done.",
        "fixtures": [],
        "assertions": [{"kind": "contains", "value": "done"}],
    }


def _three_side_run(store: SkillEvaluationStore):
    baseline_package = _package("baseline")
    previous_package = _package("previous")
    candidate_package = _package("candidate")
    baseline = store.create_overlay(
        draft_id="draft-one",
        draft_revision=1,
        content_digest=_digest(baseline_package),
        package=baseline_package,
    )
    previous = store.create_overlay(
        draft_id="draft-one",
        draft_revision=2,
        content_digest=_digest(previous_package),
        package=previous_package,
    )
    candidate = store.create_overlay(
        draft_id="draft-one",
        draft_revision=3,
        content_digest=_digest(candidate_package),
        package=candidate_package,
    )
    run = store.create_run(
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=3,
        frozen_digest=candidate.content_digest,
        baseline_overlay_id=baseline.overlay_id,
        previous_overlay_id=previous.overlay_id,
        candidate_overlay_id=candidate.overlay_id,
        cases=[_case(index) for index in range(1, 4)],
        model_id="provider/model-one",
        repetitions=1,
        config={
            "regression_governance_version": "skill-creator-regression-v1",
            "target_count": 3,
            "estimated_model_calls": 9,
        },
    )
    return run


def test_three_side_matrix_is_persisted_and_budgeted(tmp_path: Path) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _three_side_run(store)
    assert len(run.items) == 9
    assert {item.target for item in run.items} == {
        "baseline",
        "previous",
        "candidate",
    }
    restored = SkillEvaluationStore(tmp_path).require_run(run.run_id)
    assert restored.previous_overlay_id == run.previous_overlay_id
    assert len(restored.items) == 9

    cases = [_case(index) for index in range(1, 13)]
    at_limit = store.create_run(
        session_id="session-at-limit",
        draft_id=run.draft_id,
        draft_revision=run.draft_revision,
        frozen_digest=run.frozen_digest,
        baseline_overlay_id=run.baseline_overlay_id,
        previous_overlay_id=run.previous_overlay_id,
        candidate_overlay_id=run.candidate_overlay_id,
        cases=cases,
        model_id=run.model_id,
        repetitions=2,
        evaluation_suite_id="suite-at-limit",
        evaluation_suite_revision=1,
        evaluation_suite_digest="b" * 64,
        evaluation_suite_version="skill-evaluation-suite-v2",
    )
    assert len(at_limit.items) == SkillEvaluationStore.MAX_RUN_ITEMS == 72
    with pytest.raises(SkillEvaluationValidationError) as captured:
        store.create_run(
            session_id="session-two",
            draft_id=run.draft_id,
            draft_revision=run.draft_revision,
            frozen_digest=run.frozen_digest,
            baseline_overlay_id=run.baseline_overlay_id,
            previous_overlay_id=run.previous_overlay_id,
            candidate_overlay_id=run.candidate_overlay_id,
            cases=cases,
            model_id=run.model_id,
            repetitions=3,
            evaluation_suite_id="suite-one",
            evaluation_suite_revision=1,
            evaluation_suite_digest="a" * 64,
            evaluation_suite_version="skill-evaluation-suite-v2",
        )
    assert captured.value.code == "skill_evaluation_item_budget_exceeded"


def test_governance_projection_survives_unavailable_evolution_history() -> None:
    suite = SimpleNamespace(
        state="confirmed",
        draft_id="draft-one",
        draft_revision=3,
        draft_digest="c" * 64,
        cases=(_case(1), _case(2), _case(3)),
    )

    class BrokenEvolutionStore:
        def current_for_session(self, _session_id: str):
            raise SkillCreatorStorageError("evolution snapshot is unavailable")

    service = object.__new__(SkillCreatorEvaluationService)
    service.suite_service = SimpleNamespace(
        enabled=True,
        suite_store=SimpleNamespace(
            current_for_session=lambda _session_id: suite,
        ),
    )
    service.evolution_store = BrokenEvolutionStore()
    service.draft_store = SimpleNamespace(
        list_revision_snapshots=lambda _draft_id: []
    )
    service.evaluation_store = SimpleNamespace(
        list_runs=lambda *, session_id, limit: [],
    )
    session = SimpleNamespace(session_id="session-one")
    draft = SimpleNamespace(
        draft_id="draft-one",
        content_revision=3,
        content_digest="c" * 64,
    )

    projection = service.governance_projection(session, draft)

    assert projection["enabled"] is True
    assert projection["evolution_history_available"] is False
    assert projection["target_count"] == 2
    assert projection["previous_revision"] is None


def test_regression_classification_and_item_level_override(tmp_path: Path) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _three_side_run(store)
    store.claim_next_run()
    while True:
        pairs = store.claim_pairs(run.run_id, limit_pairs=4)
        if not pairs:
            break
        for pair in pairs:
            for item in pair:
                passed = True
                if item.case_id == "case-1" and item.target == "previous":
                    passed = False
                if item.case_id == "case-2" and item.target == "candidate":
                    passed = False
                store.record_item_result(
                    run.run_id,
                    item.item_id,
                    result={
                        "status": "completed",
                        "output": "done" if passed else "not yet",
                        "actual_model": "provider/model-one",
                        "skill_read": True,
                        "work_manifest": [],
                        "assertion_results": [
                            {
                                "kind": "contains",
                                "passed": passed,
                                "score": 1.0 if passed else 0.0,
                                "reason": "test",
                            }
                        ],
                        "score": 1.0 if passed else 0.0,
                        "usage": {"model_calls": 1},
                    },
                )
    current = store.require_run(run.run_id)
    report = aggregate_skill_evaluation_report(current)
    completed = store.complete_run(run.run_id, report)
    pairs = {item["case_id"]: item for item in completed.report["pairs"]}
    assert pairs["case-1"]["classification"] == "improved"
    assert pairs["case-2"]["classification"] == "regressed"
    assert pairs["case-3"]["classification"] == "flat"
    regression_ids = completed.report["regression_item_ids"]
    assert regression_ids == [pairs["case-2"]["candidate_item_id"]]

    with pytest.raises(SkillEvaluationValidationError) as captured:
        store.review_run(
            run.run_id,
            expected_revision=completed.revision,
            expected_feedback_revision=0,
            decision="accept",
            reason="The known regression is acceptable for this revision.",
            acknowledge_failed_assertions=True,
        )
    assert captured.value.code == "skill_evaluation_regressions_unacknowledged"

    accepted = store.review_run(
        run.run_id,
        expected_revision=completed.revision,
        expected_feedback_revision=0,
        decision="accept",
        reason="The known regression is acceptable for this revision.",
        acknowledge_failed_assertions=True,
        acknowledged_regression_item_ids=regression_ids,
    )
    assert accepted.review_state == "accepted"
    assert accepted.reviews[-1].acknowledged_regression_item_ids == tuple(
        regression_ids
    )
