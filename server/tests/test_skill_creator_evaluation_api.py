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
)
from server.skills.creator_evaluation_service import (
    SkillCreatorEvaluationService,
)
from server.skills.creator_service import SkillCreatorService
from server.skills.creator_store import SkillCreatorSessionStore
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
