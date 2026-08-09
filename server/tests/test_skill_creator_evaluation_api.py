from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.skills import creator_api
from server.skills.creator_evaluation import (
    SkillEvaluationExecutor,
    SkillEvaluationRunnerResult,
    SkillEvaluationStore,
    SkillEvaluationValidationError,
)
from server.skills.creator_evaluation_service import (
    SkillCreatorEvaluationService,
)
from server.skills.creator_service import SkillCreatorService
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSessionStore,
    SkillCreatorValidationError,
)
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xperts import XpertStore


SKILL_MARKDOWN = """---
name: review-notes
description: Review completed meeting notes into traceable actions. Use when a user needs decisions, owners, and unresolved questions extracted from factual notes.
---

# Review notes

## Purpose and boundaries

Turn factual meeting notes into traceable decisions and actions without inventing facts.

## Inputs and prerequisites

Require completed notes. Ask for clarification when the source is ambiguous.

## Workflow

1. Read the notes and preserve evidence.
2. Extract decisions, actions, owners, and open questions.
3. Verify each claim against the source.
4. Format the verified actions and explicitly mark unresolved fields.

## Output contract

Return decisions and actions with evidence, owner, due date, and unknown markers.

## Quality checks

Trace every claim and keep unresolved ambiguity visible.

## Failure and degradation

When notes are missing, request them instead of inventing an answer.
"""


def _case(index: int) -> dict:
    return {
        "case_id": f"case-{index}",
        "name": f"Meeting case {index}",
        "prompt": f"Review meeting notes fixture {index} and include ok.",
        "expected_behavior": "Return a traceable review and the word ok.",
        "fixtures": [
            {"path": f"notes-{index}.txt", "content": "Alice owns follow-up."}
        ],
        "assertions": [{"kind": "contains", "value": "ok"}],
    }


async def _runner(run, item, case, overlay):
    del case, overlay
    return SkillEvaluationRunnerResult(
        output="ok - Alice owns follow-up.",
        actual_model=run.model_id,
        skill_read=item.target == "candidate",
    )


async def _preflight(draft, purpose):
    del draft
    return {
        "model_id": "test/model" if purpose == "evaluate" else None,
        "config": {"timeout_seconds": 30, "max_concurrency": 1},
    }


def _services(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    session_store = SkillCreatorSessionStore(runtime_dir)
    draft_store = WorkspaceSkillDraftStore(runtime_dir)
    authoring = AuthoringService(
        AuthoringProposalStore(runtime_dir),
        XpertStore(tmp_path / "xperts"),
        draft_store,
        local_console_actor_id="console_eval_test",
    )
    creator = SkillCreatorService(
        session_store,
        draft_store,
        authoring,
        enabled=True,
    )
    session = session_store.create(
        intent="Turn note review into a reusable Skill.",
        expected_output="A traceable action report.",
        success_criteria=["No invented owners"],
    )
    draft = draft_store.create_creator_draft(
        creator_session_id=session.session_id,
        name="review-notes",
        slug="review-notes",
        description="Review completed meeting notes into traceable actions.",
        skill_markdown=SKILL_MARKDOWN,
        files={},
    )
    session = session_store.bind_draft(
        session.session_id,
        expected_session_revision=session.session_revision,
        draft_id=draft.draft_id,
        draft_state_revision=draft.revision,
        content_revision=draft.content_revision,
        content_digest=draft.content_digest,
    )
    evaluation_store = SkillEvaluationStore(runtime_dir / "evaluations")
    executor = SkillEvaluationExecutor(
        evaluation_store, runner=_runner, poll_seconds=10
    )
    evaluation = SkillCreatorEvaluationService(
        session_store,
        draft_store,
        evaluation_store,
        executor=executor,
        preflight=_preflight,
        actor_id=authoring.local_console_actor_id,
    )
    return creator, evaluation, executor, session, draft


def _write_context(session, draft) -> dict:
    return {
        "expected_session_revision": session.session_revision,
        "expected_revision": draft.revision,
        "expected_digest": draft.content_digest,
    }


def test_evaluation_service_cannot_bypass_disabled_creator(tmp_path: Path) -> None:
    creator, evaluation, _, _, _ = _services(tmp_path)
    previous_creator = creator_api._service
    previous_evaluation = creator_api._evaluation_service
    creator.enabled = False
    creator_api.configure_skill_creator(creator)
    creator_api.configure_skill_creator_evaluation(evaluation)
    try:
        with pytest.raises(SkillCreatorValidationError) as captured:
            creator_api.get_skill_creator_evaluation_service()
        assert captured.value.code == "skill_creator_disabled"
    finally:
        creator_api.configure_skill_creator(previous_creator)
        creator_api.configure_skill_creator_evaluation(previous_evaluation)


@pytest.mark.parametrize(
    "code",
    ["model_gateway_unconfigured", "skill_evaluation_sidecar_unavailable"],
)
def test_preflight_dependencies_are_reported_as_service_unavailable(code: str) -> None:
    response = creator_api._api_error(
        SkillEvaluationValidationError("dependency unavailable", code=code)
    )

    assert response.status_code == 503
    assert response.detail["code"] == code


def test_session_get_recovers_one_uniquely_matching_orphan_case_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, evaluation, _, session, draft = _services(tmp_path)
    original_bind = evaluation.session_store.bind_cases

    def interrupt_projection(*args, **kwargs):
        del args, kwargs
        raise OSError("simulated Session projection interruption")

    monkeypatch.setattr(evaluation.session_store, "bind_cases", interrupt_projection)
    with pytest.raises(OSError, match="projection interruption"):
        evaluation.save_cases(
            session.session_id,
            **_write_context(session, draft),
            quality_mode="objective",
            cases=[_case(1), _case(2), _case(3)],
        )
    monkeypatch.setattr(evaluation.session_store, "bind_cases", original_bind)

    assert evaluation.session_store.require(session.session_id).cases_revision == 0
    recovered_session, _, case_set, run = evaluation.get_projection(session.session_id)
    assert run is None
    assert recovered_session.cases_revision == 1
    assert case_set is not None and case_set["cases_revision"] == 1
    assert len(case_set["cases"]) == 3


def test_session_get_does_not_guess_across_multiple_orphan_case_revisions(
    tmp_path: Path,
) -> None:
    _, evaluation, _, session, draft = _services(tmp_path)
    first_cases = [_case(1), _case(2), _case(3)]
    evaluation.evaluation_store.save_cases(
        session_id=session.session_id,
        draft_id=draft.draft_id,
        draft_revision=draft.content_revision,
        content_digest=draft.content_digest,
        expected_revision=0,
        cases=first_cases,
        quality_mode="objective",
    )
    second_cases = [_case(1), _case(2), _case(3)]
    second_cases[0]["prompt"] = "A second unbound case revision"
    evaluation.evaluation_store.save_cases(
        session_id=session.session_id,
        draft_id=draft.draft_id,
        draft_revision=draft.content_revision,
        content_digest=draft.content_digest,
        expected_revision=1,
        cases=second_cases,
        quality_mode="objective",
    )

    projected_session, _, case_set, _ = evaluation.get_projection(session.session_id)
    assert projected_session.cases_revision == 0
    assert case_set is None


@pytest.mark.asyncio
async def test_objective_three_case_evaluation_accepts_only_current_digest(
    tmp_path: Path,
) -> None:
    _, evaluation, executor, session, draft = _services(tmp_path)
    session, draft, case_set = evaluation.save_cases(
        session.session_id,
        **_write_context(session, draft),
        quality_mode="objective",
        cases=[_case(1), _case(2), _case(3)],
    )
    assert case_set["cases_revision"] == 1
    session, draft, run = await evaluation.start_evaluation(
        session.session_id,
        **_write_context(session, draft),
        repetitions=1,
    )
    assert len(run.items) == 6
    assert draft.quality_status == "running"

    assert await executor.execute_next() is True
    session, draft, _, run = evaluation.get_projection(session.session_id)
    assert run is not None and run.status == "completed"
    session, draft, run = await evaluation.review(
        run.run_id,
        **_write_context(session, draft),
        expected_run_revision=run.revision,
        expected_review_revision=run.feedback_revision,
        decision="accept",
        reason="The paired outputs and assertions were reviewed.",
    )
    assert run.review_state == "accepted"
    assert draft.quality_status == "accepted"
    assert draft.quality_decision is not None
    assert draft.quality_decision.actor_kind == "local_console"
    assert draft.quality_decision.content_digest == draft.content_digest
    assert session.quality_status == "accepted"


@pytest.mark.asyncio
async def test_session_get_recovers_interrupted_accepted_quality_receipt(
    tmp_path: Path,
) -> None:
    _, evaluation, executor, session, draft = _services(tmp_path)
    session, draft, _ = evaluation.save_cases(
        session.session_id,
        **_write_context(session, draft),
        quality_mode="objective",
        cases=[_case(1), _case(2), _case(3)],
    )
    session, draft, run = await evaluation.start_evaluation(
        session.session_id, **_write_context(session, draft)
    )
    assert await executor.execute_next() is True
    run = evaluation.evaluation_store.require_run(run.run_id)
    run = evaluation.evaluation_store.review_run(
        run.run_id,
        expected_revision=run.revision,
        expected_feedback_revision=run.feedback_revision,
        decision="accept",
        reason="Reviewed after a simulated cross-store interruption.",
        actor_kind="local_console",
    )
    assert evaluation.draft_store.require(draft.draft_id).quality_status == "running"

    session, recovered, _, projected_run = evaluation.get_projection(
        session.session_id
    )
    assert projected_run is not None and projected_run.review_state == "accepted"
    assert recovered.quality_status == "accepted"
    assert recovered.quality_decision is not None
    assert recovered.quality_decision.decision_id == run.reviews[-1].review_id
    assert session.review_state == "accepted"


@pytest.mark.asyncio
async def test_orphan_case_recovery_does_not_replay_an_older_accepted_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, evaluation, executor, session, draft = _services(tmp_path)
    session, draft, _ = evaluation.save_cases(
        session.session_id,
        **_write_context(session, draft),
        quality_mode="objective",
        cases=[_case(1), _case(2), _case(3)],
    )
    session, draft, run = await evaluation.start_evaluation(
        session.session_id, **_write_context(session, draft)
    )
    assert await executor.execute_next() is True
    session, draft, _, run = evaluation.get_projection(session.session_id)
    assert run is not None
    session, draft, _ = await evaluation.review(
        run.run_id,
        **_write_context(session, draft),
        expected_run_revision=run.revision,
        expected_review_revision=run.feedback_revision,
        decision="accept",
        reason="The first case revision was reviewed.",
    )
    assert draft.quality_status == "accepted"

    replacement_cases = [_case(1), _case(2), _case(3)]
    replacement_cases[0]["prompt"] = "Review a replacement input."
    original_bind = evaluation.session_store.bind_cases

    def interrupt_projection(*args, **kwargs):
        del args, kwargs
        raise OSError("simulated second CaseSet projection interruption")

    monkeypatch.setattr(evaluation.session_store, "bind_cases", interrupt_projection)
    with pytest.raises(OSError, match="second CaseSet projection interruption"):
        evaluation.save_cases(
            session.session_id,
            **_write_context(session, draft),
            quality_mode="objective",
            cases=replacement_cases,
        )
    monkeypatch.setattr(evaluation.session_store, "bind_cases", original_bind)

    recovered_session, recovered_draft, case_set, projected_run = (
        evaluation.get_projection(session.session_id)
    )
    assert recovered_session.cases_revision == 2
    assert case_set is not None and case_set["cases_revision"] == 2
    assert projected_run is None
    assert recovered_draft.quality_status == "outdated"
    assert recovered_session.quality_status == "outdated"


@pytest.mark.asyncio
async def test_creator_evaluation_api_exposes_cases_run_and_structured_conflict(
    tmp_path: Path,
) -> None:
    creator, evaluation, executor, session, draft = _services(tmp_path)
    app = FastAPI()
    app.include_router(creator_api.router)
    previous_creator = creator_api._service
    previous_evaluation = creator_api._evaluation_service
    creator_api.configure_skill_creator(creator)
    creator_api.configure_skill_creator_evaluation(evaluation)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            mode_response = await client.patch(
                f"/api/skills/creator/sessions/{session.session_id}",
                json={
                    "expected_session_revision": session.session_revision,
                    "quality_mode": "subjective",
                },
            )
            assert mode_response.status_code == 200, mode_response.text
            assert mode_response.json()["session"]["quality_mode"] == "subjective"
            session = evaluation.session_store.require(session.session_id)
            draft = evaluation.draft_store.require(draft.draft_id)
            mode_response = await client.patch(
                f"/api/skills/creator/sessions/{session.session_id}",
                json={
                    "expected_session_revision": session.session_revision,
                    "quality_mode": "objective",
                },
            )
            assert mode_response.status_code == 200, mode_response.text
            session = evaluation.session_store.require(session.session_id)
            draft = evaluation.draft_store.require(draft.draft_id)

            cases_response = await client.put(
                f"/api/skills/creator/sessions/{session.session_id}/cases",
                json={
                    **_write_context(session, draft),
                    "quality_mode": "objective",
                    "cases": [_case(1), _case(2), _case(3)],
                },
            )
            assert cases_response.status_code == 200, cases_response.text
            body = cases_response.json()
            assert body["cases_revision"] == 1
            assert len(body["cases"]) == 3
            session = evaluation.session_store.require(session.session_id)
            draft = evaluation.draft_store.require(draft.draft_id)

            stale = await client.post(
                f"/api/skills/creator/sessions/{session.session_id}/evaluations",
                json={
                    **_write_context(session, draft),
                    "expected_session_revision": session.session_revision - 1,
                    "repetitions": 1,
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "skill_creator_conflict"

            started = await client.post(
                f"/api/skills/creator/sessions/{session.session_id}/evaluations",
                json={**_write_context(session, draft), "repetitions": 1},
            )
            assert started.status_code == 202, started.text
            run_id = started.json()["evaluation_run"]["run_id"]
            assert await executor.execute_next() is True

            before_poll = evaluation.session_store.require(session.session_id)
            assert before_poll.active_evaluation_run_id == run_id
            run_response = await client.get(
                f"/api/skills/creator/evaluations/{run_id}"
            )
            assert run_response.status_code == 200, run_response.text
            run_body = run_response.json()
            assert run_body["run"]["status"] == "completed"
            assert run_body["session"]["active_evaluation_run_id"] is None
            assert run_body["draft"]["draft_id"] == draft.draft_id

            refreshed = await client.get(
                f"/api/skills/creator/sessions/{session.session_id}"
            )
            assert refreshed.status_code == 200, refreshed.text
            refreshed_body = refreshed.json()
            assert len(refreshed_body["cases"]) == 3
            assert refreshed_body["evaluation_run"]["run_id"] == run_id
            assert refreshed_body["evaluation_run"]["status"] == "completed"
    finally:
        creator_api.configure_skill_creator(previous_creator)
        creator_api.configure_skill_creator_evaluation(previous_evaluation)


@pytest.mark.asyncio
async def test_subjective_waiver_requires_mode_reason_confirmation_and_preflight(
    tmp_path: Path,
) -> None:
    _, evaluation, _, session, draft = _services(tmp_path)
    session, draft, _ = evaluation.save_cases(
        session.session_id,
        **_write_context(session, draft),
        quality_mode="subjective",
        cases=[],
    )
    with pytest.raises(Exception, match="explicit confirmation"):
        await evaluation.waive(
            session.session_id,
            **_write_context(session, draft),
            reason="Creative tone is judged by the author.",
            confirmed=False,
        )
    session, draft = await evaluation.waive(
        session.session_id,
        **_write_context(session, draft),
        reason="Creative tone is judged by the author after manual inspection.",
        confirmed=True,
    )
    assert draft.quality_status == "eval_waived"
    assert draft.quality_decision is not None
    assert draft.quality_decision.reason
    assert session.review_state == "waived"


@pytest.mark.asyncio
async def test_subjective_waiver_rejects_an_active_evaluation(tmp_path: Path) -> None:
    _, evaluation, _, session, draft = _services(tmp_path)
    session, draft, _ = evaluation.save_cases(
        session.session_id,
        **_write_context(session, draft),
        quality_mode="subjective",
        cases=[_case(1), _case(2), _case(3)],
    )
    session, draft, run = await evaluation.start_evaluation(
        session.session_id,
        **_write_context(session, draft),
    )
    assert session.active_evaluation_run_id == run.run_id

    with pytest.raises(SkillCreatorConflictError, match="Cancel the active evaluation"):
        await evaluation.waive(
            session.session_id,
            **_write_context(session, draft),
            reason="The author wants to use the subjective waiver instead.",
            confirmed=True,
        )


@pytest.mark.asyncio
async def test_service_persists_failed_assertion_acknowledgement(
    tmp_path: Path,
) -> None:
    _, evaluation, executor, session, draft = _services(tmp_path)
    cases = [_case(1), _case(2), _case(3)]
    for case in cases:
        case["assertions"] = [{"kind": "contains", "value": "missing-marker"}]
    session, draft, _ = evaluation.save_cases(
        session.session_id,
        **_write_context(session, draft),
        quality_mode="objective",
        cases=cases,
    )
    session, draft, run = await evaluation.start_evaluation(
        session.session_id,
        **_write_context(session, draft),
    )
    assert await executor.execute_next() is True
    session, draft, _, run = evaluation.get_projection(session.session_id)
    assert run is not None and run.report["assertion_failed_count"] == 6

    with pytest.raises(
        SkillEvaluationValidationError,
        match="explicit acknowledgement",
    ):
        await evaluation.review(
            run.run_id,
            **_write_context(session, draft),
            expected_run_revision=run.revision,
            expected_review_revision=run.feedback_revision,
            decision="accept",
            reason="The failed checks were reviewed manually.",
            acknowledge_failed_assertions=False,
        )

    session, draft, accepted = await evaluation.review(
        run.run_id,
        **_write_context(session, draft),
        expected_run_revision=run.revision,
        expected_review_revision=run.feedback_revision,
        decision="accept",
        reason="The failed checks were reviewed manually.",
        acknowledge_failed_assertions=True,
    )
    assert accepted.reviews[-1].acknowledge_failed_assertions is True
    assert draft.quality_status == "accepted"
    assert session.review_state == "accepted"
