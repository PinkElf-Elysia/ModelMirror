from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from server.skills.creator_evaluation import (
    SkillEvaluationConflictError,
    SkillEvaluationExecutor,
    SkillEvaluationStateError,
    SkillEvaluationStorageError,
    SkillEvaluationStore,
    SkillEvaluationValidationError,
    aggregate_skill_evaluation_report,
    evaluate_skill_case,
)


DIGEST = "a" * 64
OLD_DIGEST = "b" * 64


def _case(index: int, *, assertions: list[dict] | None = None) -> dict:
    return {
        "case_id": f"case-{index}",
        "name": f"Case {index}",
        "prompt": f"Complete task {index}",
        "expected_behavior": "Return a stable, verifiable result.",
        "fixtures": [{"path": "input.txt", "content": f"fixture {index}"}],
        "assertions": assertions or [{"kind": "contains", "value": "done"}],
    }


def _overlay(
    store: SkillEvaluationStore,
    *,
    digest: str = DIGEST,
    revision: int = 2,
):
    return store.create_overlay(
        draft_id="draft-one",
        draft_revision=revision,
        content_digest=digest,
        package={
            "root_dir": "demo-skill",
            "files": {
                "demo-skill/SKILL.md": "---\nname: demo-skill\ndescription: demo\n---\n",
            },
        },
    )


def _run(
    store: SkillEvaluationStore,
    *,
    repetitions: int = 1,
    baseline: bool = False,
    assertions: list[dict] | None = None,
):
    candidate = _overlay(store)
    baseline_overlay = (
        _overlay(store, digest=OLD_DIGEST, revision=1) if baseline else None
    )
    return store.create_run(
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=2,
        frozen_digest=DIGEST,
        baseline_overlay_id=(baseline_overlay.overlay_id if baseline_overlay else None),
        candidate_overlay_id=candidate.overlay_id,
        cases=[_case(index, assertions=assertions) for index in range(1, 4)],
        model_id="provider/model-one",
        repetitions=repetitions,
    )


def test_store_freezes_three_case_paired_matrix_and_round_trips(tmp_path: Path) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store, repetitions=3, baseline=True)

    assert len(run.cases) == 3
    assert len(run.items) == 18
    assert len({item.pair_id for item in run.items}) == 9
    assert {
        (item.case_id, item.repetition, item.target) for item in run.items
    } == {
        (f"case-{case}", repetition, target)
        for case in range(1, 4)
        for repetition in range(1, 4)
        for target in ("baseline", "candidate")
    }

    restored = SkillEvaluationStore(tmp_path).require_run(run.run_id)
    assert restored.frozen_digest == DIGEST
    assert restored.cases[0].case_fingerprint == run.cases[0].case_fingerprint
    assert restored.baseline_overlay_id == run.baseline_overlay_id


def test_case_sets_are_private_immutable_revisions_and_bind_runs(tmp_path: Path) -> None:
    store = SkillEvaluationStore(tmp_path)
    candidate = _overlay(store)
    first_cases = [_case(index) for index in range(1, 4)]
    first = store.save_cases(
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=2,
        content_digest=DIGEST,
        expected_revision=0,
        cases=first_cases,
        quality_mode="objective",
    )
    assert first.cases_revision == 1
    same = store.save_cases(
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=2,
        content_digest=DIGEST,
        expected_revision=1,
        cases=first_cases,
        quality_mode="objective",
    )
    assert same.cases_revision == 1

    changed_cases = [_case(index) for index in range(1, 4)]
    changed_cases[0]["prompt"] = "A changed prompt"
    second = store.save_cases(
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=2,
        content_digest=DIGEST,
        expected_revision=1,
        cases=changed_cases,
        quality_mode="objective",
    )
    assert second.cases_revision == 2
    assert store.require_cases("session-one", revision=1).cases[0].prompt == "Complete task 1"

    with pytest.raises(SkillEvaluationConflictError):
        store.save_cases(
            session_id="session-one",
            draft_id="draft-one",
            draft_revision=2,
            content_digest=DIGEST,
            expected_revision=1,
            cases=changed_cases,
            quality_mode="objective",
        )

    run = store.create_run(
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=2,
        frozen_digest=DIGEST,
        candidate_overlay_id=candidate.overlay_id,
        case_set_revision=2,
        model_id="provider/model-one",
    )
    assert run.case_set_revision == 2
    assert run.cases[0].prompt == "A changed prompt"
    restored = SkillEvaluationStore(tmp_path)
    assert restored.require_cases("session-one").cases_revision == 2
    assert restored.require_run(run.run_id).case_set_revision == 2


@pytest.mark.parametrize("case_count", [0, 1, 2, 4])
def test_store_requires_exactly_three_objective_cases(
    tmp_path: Path, case_count: int
) -> None:
    store = SkillEvaluationStore(tmp_path)
    candidate = _overlay(store)

    with pytest.raises(SkillEvaluationValidationError) as captured:
        store.create_run(
            session_id="session-one",
            draft_id="draft-one",
            draft_revision=2,
            frozen_digest=DIGEST,
            candidate_overlay_id=candidate.overlay_id,
            cases=[_case(index) for index in range(case_count)],
            model_id="provider/model-one",
        )

    assert captured.value.code == "skill_evaluation_three_cases_required"


def test_case_validation_rejects_path_escape_duplicate_ids_and_judge_config(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationStore(tmp_path)
    candidate = _overlay(store)
    escaped = _case(1)
    escaped["fixtures"] = [{"path": "../secret.txt", "content": "no"}]
    with pytest.raises(SkillEvaluationValidationError):
        SkillEvaluationStore.normalize_case(escaped)

    cases = [_case(1), _case(1), _case(3)]
    with pytest.raises(SkillEvaluationValidationError) as duplicate:
        store.create_run(
            session_id="session-one",
            draft_id="draft-one",
            draft_revision=2,
            frozen_digest=DIGEST,
            candidate_overlay_id=candidate.overlay_id,
            cases=cases,
            model_id="provider/model-one",
        )
    assert duplicate.value.code == "skill_evaluation_case_duplicate"

    with pytest.raises(SkillEvaluationValidationError) as judge:
        store.create_run(
            session_id="session-one",
            draft_id="draft-one",
            draft_revision=2,
            frozen_digest=DIGEST,
            candidate_overlay_id=candidate.overlay_id,
            cases=[_case(index) for index in range(1, 4)],
            model_id="provider/model-one",
            config={"judge_model_id": "forbidden"},
        )
    assert judge.value.code == "skill_evaluation_config_forbidden"


def test_all_deterministic_assertions_are_evaluated_without_judge() -> None:
    payload = b"artifact"
    digest = hashlib.sha256(payload).hexdigest()
    case = SkillEvaluationStore.normalize_case(
        _case(
            1,
            assertions=[
                {"kind": "exact_match", "value": '{"status":"done"}'},
                {"kind": "contains", "value": "DONE"},
                {"kind": "not_contains", "value": "secret"},
                {
                    "kind": "json_schema",
                    "schema": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {"status": {"const": "done"}},
                    },
                },
                {"kind": "file_exists", "path": "result.txt"},
                {
                    "kind": "file_sha256",
                    "path": "result.txt",
                    "sha256": digest,
                },
            ],
        )
    )
    result = evaluate_skill_case(
        case,
        output='{"status":"done"}',
        work_manifest=[
            {
                "path": "result.txt",
                "size": len(payload),
                "sha256": digest,
                "preview": "artifact",
            }
        ],
    )

    assert result["assertion_count"] == 6
    assert result["assertion_passed_count"] == 6
    assert result["score"] == 1.0
    assert {item["kind"] for item in result["assertion_results"]} == {
        "exact_match",
        "contains",
        "not_contains",
        "json_schema",
        "file_exists",
        "file_sha256",
    }


@pytest.mark.asyncio
async def test_executor_records_skill_read_actual_model_manifest_and_report(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store, repetitions=2, baseline=True)
    artifact = b"artifact"
    artifact_digest = hashlib.sha256(artifact).hexdigest()

    async def runner(current_run, item, case, overlay):
        assert current_run.run_id == run.run_id
        assert case.case_id == item.case_id
        assert (item.target == "baseline") is (overlay.content_digest == OLD_DIGEST)
        return {
            "output": "done",
            "actual_model": "provider/model-one@actual",
            "skill_read": True,
            "work_manifest": [
                {
                    "path": "result.txt",
                    "size": len(artifact),
                    "sha256": artifact_digest,
                    "preview": "artifact",
                }
            ],
            "usage": {"model_calls": 1, "tool_calls": 1, "estimated_tokens": 12},
            "runtime_run_id": f"runtime-{item.item_id}",
        }

    executor = SkillEvaluationExecutor(store, runner=runner)
    assert await executor.execute_next() is True
    assert await executor.execute_next() is False

    completed = store.require_run(run.run_id)
    assert completed.status == "completed"
    assert all(item.status == "completed" for item in completed.items)
    assert all(item.skill_read for item in completed.items)
    assert all(item.actual_model == "provider/model-one@actual" for item in completed.items)
    assert all(item.work_manifest[0]["sha256"] == artifact_digest for item in completed.items)
    assert completed.report["pair_count"] == 6
    assert completed.report["model_mismatch_count"] == 0
    assert completed.report["eligible_for_accept"] is True
    assert completed.report["ranker_or_judge_used"] is False


@pytest.mark.asyncio
async def test_candidate_without_skill_read_and_model_mismatch_fail_acceptance(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store)

    async def runner(_run, item, _case, _overlay):
        return {
            "output": "done",
            "actual_model": "baseline-model" if item.target == "baseline" else "candidate-model",
            "skill_read": item.target == "baseline",
        }

    executor = SkillEvaluationExecutor(store, runner=runner)
    await executor.execute_next()
    completed = store.require_run(run.run_id)

    assert sum(item.status == "skill_not_read" for item in completed.items) == 3
    assert completed.report["skill_not_read_count"] == 3
    assert completed.report["model_mismatch_count"] == 3
    assert completed.report["eligible_for_accept"] is False
    with pytest.raises(SkillEvaluationStateError):
        store.review_run(
            run.run_id,
            expected_revision=completed.revision,
            expected_feedback_revision=completed.feedback_revision,
            decision="accept",
            reason="",
        )


@pytest.mark.asyncio
async def test_recovery_preserves_completed_side_and_only_reruns_unfinished_side(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store)
    store.claim_next_run()
    first_pair = store.claim_pairs(run.run_id)[0]
    baseline = next(item for item in first_pair if item.target == "baseline")
    store.record_item_result(
        run.run_id,
        baseline.item_id,
        result={
            "status": "completed",
            "output": "done",
            "actual_model": "model-actual",
            "skill_read": False,
            "score": 1.0,
        },
    )

    restored = SkillEvaluationStore(tmp_path)
    assert restored.recover_runs() == 1
    called: list[str] = []

    async def runner(_run, item, _case, _overlay):
        called.append(item.item_id)
        return {
            "output": "done",
            "actual_model": "model-actual",
            "skill_read": item.target == "candidate",
        }

    await SkillEvaluationExecutor(restored, runner=runner).execute_next()
    completed = restored.require_run(run.run_id)
    preserved = next(item for item in completed.items if item.item_id == baseline.item_id)

    assert baseline.item_id not in called
    assert preserved.attempts == 1
    assert preserved.output == "done"
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_cancel_and_paired_retry_preserve_attempt_history(tmp_path: Path) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store)
    store.cancel_run(run.run_id)
    cancelled = store.require_run(run.run_id)
    assert cancelled.status == "cancelled"
    assert all(item.status == "cancelled" for item in cancelled.items)

    retried = store.retry_run(run.run_id, case_ids=["case-1"])
    case_one = [item for item in retried.items if item.case_id == "case-1"]
    untouched = [item for item in retried.items if item.case_id != "case-1"]
    assert retried.status == "queued"
    assert all(item.status == "pending" for item in case_one)
    assert all(len(item.attempt_history) == 1 for item in case_one)
    assert all(item.status == "cancelled" for item in untouched)

    async def runner(_run, item, _case, _overlay):
        return {
            "output": "done",
            "actual_model": "model-actual",
            "skill_read": item.target == "candidate",
        }

    await SkillEvaluationExecutor(store, runner=runner).execute_next()
    completed = store.require_run(run.run_id)
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_review_accept_and_revise_rules(tmp_path: Path) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store)

    async def runner(_run, item, _case, _overlay):
        return {
            "output": "done",
            "actual_model": "model-actual",
            "skill_read": item.target == "candidate",
        }

    await SkillEvaluationExecutor(store, runner=runner).execute_next()
    completed = store.require_run(run.run_id)
    accepted = store.review_run(
        run.run_id,
        expected_revision=completed.revision,
        expected_feedback_revision=completed.feedback_revision,
        decision="accept",
        reason="",
    )
    assert accepted.review_state == "accepted"
    assert accepted.reviews[0].actor_kind == "local_console"
    with pytest.raises(SkillEvaluationConflictError):
        store.retry_run(run.run_id)

    second = _run(store)
    await SkillEvaluationExecutor(store, runner=runner).execute_next()
    second_completed = store.require_run(second.run_id)
    with pytest.raises(SkillEvaluationValidationError):
        store.review_run(
            second.run_id,
            expected_revision=second_completed.revision,
            expected_feedback_revision=second_completed.feedback_revision,
            decision="revise",
            reason="",
        )

    saved = store.save_feedback(
        second.run_id,
        expected_revision=second_completed.revision,
        expected_feedback_revision=second_completed.feedback_revision,
        feedback="请补充失败边界示例。",
    )
    assert saved.feedback_revision == 1
    with pytest.raises(SkillEvaluationConflictError):
        store.save_feedback(
            second.run_id,
            expected_revision=saved.revision,
            expected_feedback_revision=0,
            feedback="stale write",
        )
    revised = store.review_run(
        second.run_id,
        expected_revision=saved.revision,
        expected_feedback_revision=saved.feedback_revision,
        decision="revise",
        reason="",
    )
    assert revised.review_state == "revise"
    assert revised.reviews[0].feedback == "请补充失败边界示例。"
    assert revised.reviews[0].feedback_revision == 1


def test_top_level_corruption_fails_closed_and_does_not_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "skill_creator_evaluations.json"
    original = "{ definitely not valid json"
    path.write_text(original, encoding="utf-8")
    store = SkillEvaluationStore(tmp_path)

    assert store.load_error and "store_corrupt" in store.load_error
    with pytest.raises(SkillEvaluationStorageError):
        store.list_runs()
    with pytest.raises(SkillEvaluationStorageError):
        _overlay(store)
    assert path.read_text(encoding="utf-8") == original


def test_single_bad_record_is_quarantined_without_losing_good_records(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationStore(tmp_path)
    good = _overlay(store)
    path = tmp_path / "skill_creator_evaluations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["overlays"]["bad-overlay"] = {"overlay_id": "bad-overlay"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = SkillEvaluationStore(tmp_path)
    assert restored.require_overlay(good.overlay_id).content_digest == DIGEST
    quarantine = restored.list_quarantined()
    assert len(quarantine) == 1
    assert quarantine[0]["record_id"] == "bad-overlay"
    assert quarantine[0]["reason_code"] == "invalid_overlay"
    assert "package" not in quarantine[0]


def test_failed_atomic_write_restores_in_memory_state(tmp_path: Path, monkeypatch) -> None:
    store = SkillEvaluationStore(tmp_path)

    def fail_save() -> None:
        raise SkillEvaluationStorageError("disk unavailable")

    monkeypatch.setattr(store, "_save_unlocked", fail_save)
    with pytest.raises(SkillEvaluationStorageError):
        _overlay(store)
    assert store._overlays == {}


def test_report_requires_all_candidate_reads_and_matching_actual_models(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationStore(tmp_path)
    run = _run(store)
    for item in run.items:
        item.status = "completed"
        item.output = "done"
        item.actual_model = "same-model"
        item.skill_read = item.target == "candidate"
        item.score = 1.0
    report = aggregate_skill_evaluation_report(run)
    assert report["eligible_for_accept"] is True
    assert report["pair_count"] == 3
