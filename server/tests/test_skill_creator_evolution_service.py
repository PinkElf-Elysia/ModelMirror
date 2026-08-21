from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.skills.creator_evaluation import (
    SkillEvaluationItem,
    SkillEvaluationReview,
    SkillEvaluationRun,
)
from server.skills.creator_evaluation_suite import (
    EVALUATION_SUITE_VERSION,
    SkillEvaluationSuite,
    SkillEvaluationSuiteCase,
)
from server.skills.creator_evolution import EVOLUTION_PLAN_VERSION, SkillEvolutionPlanStore
from server.skills.creator_evolution_runtime import (
    EVOLUTION_WORKFLOW_VERSION,
    WorkflowCreatorEvolutionPlanner,
    build_evolution_invocation,
    parse_evolution_output,
)
from server.skills.creator_evolution_service import (
    EvolutionGenerationRequest,
    SkillCreatorEvolutionService,
)
from server.skills.creator_resource_plan import SkillResourcePlanStore
from server.skills.creator_resource_service import SkillCreatorResourcePlanningService
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from server.skills.draft_store import WorkspaceSkillDraft
from server.skills.package_validation import compute_package_digest


DRAFT_DIGEST = "a" * 64
SUITE_DIGEST = "b" * 64


class _CreatorService:
    def __init__(self) -> None:
        self.session = SkillCreatorSession(
            session_id="session-one",
            session_revision=7,
            draft_state_revision=5,
            intent="Normalize incident facts and create a bounded review.",
            positive_examples=["Create a review from an incident timeline."],
            near_miss_examples=["Only rewrite this paragraph."],
            expected_output="A structured incident review.",
            success_criteria=["Unknown root causes stay unknown."],
            evidence_confirmed=True,
            draft_id="draft-one",
            current_revision=3,
            current_digest=DRAFT_DIGEST,
            quality_mode="objective",
            state="iterating",
        )
        self.draft = WorkspaceSkillDraft(
            draft_id="draft-one",
            name="incident-review",
            slug="incident-review",
            description="Create incident reviews from supplied facts.",
            skill_markdown=(
                "---\nname: incident-review\ndescription: Create incident reviews.\n---\n"
                "# Incident Review\n\n## Workflow\n\n1. Inspect.\n2. Normalize.\n"
                "3. Verify.\n4. Render.\n\n## Output contract\n\nReturn fields.\n"
                "## Failure behavior\n\nMark unknown facts.\n"
            ),
            files={
                "scripts/normalize.py": "print('normalized')\n",
                "references/policy.md": "# Policy\n\nDo not invent facts.\n",
            },
            revision=5,
            content_revision=3,
            content_digest=DRAFT_DIGEST,
            creator_session_id="session-one",
            quality_required=True,
        )

    def require_enabled(self) -> None:
        return None

    def get_session(self, session_id: str):
        assert session_id == self.session.session_id
        return self.session, self.draft


class _EvaluationStore:
    def __init__(self, run: SkillEvaluationRun) -> None:
        self.run = run

    def require_run(self, run_id: str) -> SkillEvaluationRun:
        assert run_id == self.run.run_id
        return self.run


class _SuiteStore:
    def __init__(self, suite: SkillEvaluationSuite) -> None:
        self.suite = suite

    def require(self, suite_id: str, *, revision: int | None = None) -> SkillEvaluationSuite:
        assert suite_id == self.suite.suite_id
        assert revision in {None, self.suite.suite_revision}
        return self.suite


class _Planner:
    def __init__(self) -> None:
        self.calls: list[EvolutionGenerationRequest] = []

    def available(self) -> bool:
        return True

    async def generate(self, request: EvolutionGenerationRequest) -> dict:
        self.calls.append(request)
        script_id, reference_id = request.allowed_resource_ids
        return {
            "diagnoses": [
                {
                    "case_id": "core-normal",
                    "evidence_item_ids": ["item-candidate"],
                    "failure_types": ["assertion_failure"],
                    "requirement_ids": ["intent"],
                    "resource_ids": [script_id],
                    "sections": ["Workflow"],
                    "summary": "Normalization failed its deterministic assertion.",
                }
            ],
            "actions": [
                {
                    "action_id": "update-script",
                    "action": "update",
                    "resource_id": script_id,
                    "purpose": "Normalize incident facts deterministically.",
                    "source_ids": ["intent"],
                    "used_by_steps": ["normalize"],
                    "depends_on": [],
                    "acceptance_checks": ["Pass the frozen normal case."],
                    "related_case_ids": ["core-normal"],
                    "expected_improvement": "The deterministic assertion passes.",
                    "non_regression_case_ids": ["core-boundary"],
                },
                {
                    "action_id": "keep-policy",
                    "action": "keep",
                    "resource_id": reference_id,
                    "purpose": "Preserve the existing fact policy.",
                    "source_ids": ["intent"],
                    "used_by_steps": ["verify"],
                    "depends_on": [],
                    "acceptance_checks": ["Unknown root causes stay unknown."],
                    "related_case_ids": ["core-normal"],
                    "expected_improvement": "No change; retain the safety boundary.",
                    "non_regression_case_ids": ["core-boundary"],
                },
            ],
            "workflow_steps": [
                {"step_id": "inspect", "instruction": "Inspect supplied facts."},
                {"step_id": "normalize", "instruction": "Run the deterministic normalizer."},
                {"step_id": "verify", "instruction": "Verify facts against policy."},
                {"step_id": "render", "instruction": "Render the output contract."},
            ],
            "output_contract": ["Return the structured incident review fields."],
            "failure_modes": ["Mark missing facts as unknown and stop on invalid input."],
            "expected_improvements": ["The failed normal case becomes deterministic."],
            "acceptance_criteria": ["Normal passes and boundary does not regress."],
            "non_goals": ["Do not add domain facts."],
            "overfitting_risks": ["Do not hard-code the frozen timeline."],
            "clarifications": [],
        }


class _MutatingPlanner(_Planner):
    def __init__(self, creator: _CreatorService) -> None:
        super().__init__()
        self.creator = creator

    async def generate(self, request: EvolutionGenerationRequest) -> dict:
        payload = await super().generate(request)
        self.creator.session = replace(
            self.creator.session,
            session_revision=self.creator.session.session_revision + 1,
        )
        return payload


class _CrossCasePlanner(_Planner):
    async def generate(self, request: EvolutionGenerationRequest) -> dict:
        payload = await super().generate(request)
        payload["diagnoses"][0]["case_id"] = "core-boundary"
        return payload


class _RepairingPlanner(_Planner):
    async def generate(self, request: EvolutionGenerationRequest) -> dict:
        payload = await super().generate(request)
        if len(self.calls) == 1:
            payload["diagnoses"] = []
        return payload


def _suite() -> SkillEvaluationSuite:
    cases = (
        SkillEvaluationSuiteCase(
            case_id="core-normal", role="normal", source="generated",
            name="Normal", prompt="Create a review.", expected_behavior="Return fields.",
            requirement_ids=("intent",), workflow_step_ids=("normalize",), case_fingerprint="c" * 64,
        ),
        SkillEvaluationSuiteCase(
            case_id="core-ambiguous", role="ambiguous", source="generated",
            name="Ambiguous", prompt="Create a review with missing facts.", expected_behavior="Mark gaps.",
            requirement_ids=("expected_output",), workflow_step_ids=("verify",), case_fingerprint="d" * 64,
        ),
        SkillEvaluationSuiteCase(
            case_id="core-boundary", role="boundary", source="generated",
            name="Boundary", prompt="Only rewrite prose.", expected_behavior="Do not trigger.",
            requirement_ids=("near_miss:0",), workflow_step_ids=("inspect",), case_fingerprint="e" * 64,
        ),
    )
    return SkillEvaluationSuite(
        suite_id="skill_eval_suite_one",
        version=EVALUATION_SUITE_VERSION,
        suite_revision=1,
        suite_digest=SUITE_DIGEST,
        session_id="session-one",
        session_revision=6,
        session_definition_digest="f" * 64,
        draft_id="draft-one",
        draft_state_revision=4,
        draft_revision=3,
        draft_digest=DRAFT_DIGEST,
        quality_mode="objective",
        state="confirmed",
        cases=cases,
        change_reason="Initial suite",
    )


def _run(suite: SkillEvaluationSuite) -> SkillEvaluationRun:
    review = SkillEvaluationReview(
        review_id="skill_eval_review_one",
        review_revision=1,
        decision="revise",
        reason="Fix normalization without weakening the unknown-fact boundary.",
        feedback_revision=1,
        feedback="The normal case failed the deterministic normalization assertion.",
        actor_kind="local_console",
    )
    return SkillEvaluationRun(
        run_id="skill_eval_run_one",
        session_id="session-one",
        draft_id="draft-one",
        draft_revision=3,
        frozen_digest=DRAFT_DIGEST,
        baseline_overlay_id=None,
        candidate_overlay_id="overlay-one",
        model_id="provider/model",
        repetitions=1,
        cases=[],
        items=[
            SkillEvaluationItem(
                item_id="item-candidate", pair_id="pair-one", case_id="core-normal",
                target="candidate", repetition=1, overlay_id="overlay-one", status="completed",
                assertion_results=[{"assertion_id": "normalized", "passed": False, "code": "contains_failed"}],
                application_compliance="verified",
            )
        ],
        config={},
        evaluation_suite_id=suite.suite_id,
        evaluation_suite_revision=suite.suite_revision,
        evaluation_suite_digest=suite.suite_digest,
        evaluation_suite_version=suite.version,
        status="completed",
        revision=9,
        review_state="revise",
        feedback=review.feedback,
        feedback_revision=1,
        reviews=[review],
    )


def _resource_plan(store: SkillResourcePlanStore):
    generated = store.save_generated(
        session_id="session-one", session_revision=4, draft_id="draft-one",
        draft_revision=5, draft_digest=DRAFT_DIGEST,
        payload={
            "skill_name": "incident-review",
            "skill_description": "Create incident reviews from supplied facts.",
            "workflow_steps": [
                {"step_id": "inspect", "instruction": "Inspect supplied facts."},
                {"step_id": "normalize", "instruction": "Normalize the facts."},
                {"step_id": "verify", "instruction": "Verify facts against policy."},
                {"step_id": "render", "instruction": "Render the output."},
            ],
            "output_contract": ["Return the structured fields."],
            "failure_modes": ["Mark missing facts as unknown."],
            "resources": [
                {
                    "kind": "script", "action": "update", "generation_cost": "medium",
                    "path": "scripts/normalize.py", "purpose": "Normalize facts.",
                    "source_ids": ["intent"], "used_by_steps": ["normalize"],
                    "depends_on": [], "acceptance_checks": ["Exit non-zero on invalid input."],
                },
                {
                    "kind": "reference", "action": "update", "generation_cost": "low",
                    "path": "references/policy.md", "purpose": "Define the no-invention policy.",
                    "source_ids": ["intent"], "used_by_steps": ["verify"],
                    "depends_on": [], "acceptance_checks": ["Unknown facts remain unknown."],
                },
            ],
        },
        allowed_source_ids={"intent", "expected_output", "near_miss:0", "success_criterion:0"},
    )
    return store.confirm(
        generated.plan_id, expected_revision=generated.revision,
        expected_digest=generated.digest, session_revision=4,
        draft_revision=5, draft_digest=DRAFT_DIGEST,
    )


def _service(tmp_path: Path):
    creator = _CreatorService()
    resource_store = SkillResourcePlanStore(tmp_path / "resources")
    resource_plan = _resource_plan(resource_store)
    suite = _suite()
    run = _run(suite)
    planner = _Planner()
    planning = SkillCreatorResourcePlanningService(
        creator, resource_store, enabled=True  # type: ignore[arg-type]
    )
    service = SkillCreatorEvolutionService(
        creator,  # type: ignore[arg-type]
        SkillEvolutionPlanStore(tmp_path / "evolution"),
        _EvaluationStore(run),  # type: ignore[arg-type]
        _SuiteStore(suite),  # type: ignore[arg-type]
        planning,
        planner=planner,
        enabled=True,
    )
    return creator, resource_plan, suite, run, planner, service


def _expected(creator: _CreatorService, resource_plan) -> dict:
    return {
        "evaluation_run_id": "skill_eval_run_one",
        "expected_session_revision": creator.session.session_revision,
        "expected_draft_state_revision": creator.draft.revision,
        "expected_draft_revision": creator.draft.content_revision,
        "expected_draft_digest": creator.draft.content_digest,
        "expected_review_revision": 1,
        "expected_run_revision": 9,
        "expected_resource_plan_revision": resource_plan.revision,
        "expected_resource_plan_digest": resource_plan.digest,
        "expected_evolution_revision": None,
        "expected_evolution_digest": None,
    }


@pytest.mark.asyncio
async def test_generate_uses_frozen_evidence_without_model_outputs_and_confirm_rebinds_resource_plan(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, planner, service = _service(tmp_path)
    generated = await service.generate("session-one", **_expected(creator, resource_plan))
    assert generated.state == "ready"
    assert len(planner.calls) == 1
    request = planner.calls[0]
    assert "output" not in json.dumps(request.evaluation)
    assert request.review["feedback"].startswith("The normal case")

    confirmed, evolved_resource_plan = service.confirm(
        "session-one",
        plan_id=generated.plan_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_plan_revision=generated.revision,
        expected_plan_digest=generated.digest,
    )
    assert confirmed.state == "confirmed"
    assert evolved_resource_plan.state == "confirmed"
    assert evolved_resource_plan.session_revision == creator.session.session_revision
    actions = {item.path: item.action for item in evolved_resource_plan.resources}
    assert actions == {"scripts/normalize.py": "update", "references/policy.md": "keep"}

    replay, replay_resource = service.confirm(
        "session-one",
        plan_id=generated.plan_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_plan_revision=generated.revision,
        expected_plan_digest=generated.digest,
    )
    assert replay.digest == confirmed.digest
    assert replay_resource.digest == evolved_resource_plan.digest


@pytest.mark.asyncio
async def test_generate_rejects_stale_review_and_resource_plan(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, run, _planner, service = _service(tmp_path)
    run.reviews[-1] = replace(run.reviews[-1], review_revision=2)
    with pytest.raises(SkillCreatorConflictError):
        await service.generate("session-one", **_expected(creator, resource_plan))


@pytest.mark.asyncio
async def test_generate_revalidates_session_after_model_call_without_orphan_plan(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, _planner, service = _service(tmp_path)
    service.planner = _MutatingPlanner(creator)

    with pytest.raises(SkillCreatorConflictError):
        await service.generate("session-one", **_expected(creator, resource_plan))

    assert service.evolution_store.current_for_session("session-one") is None


@pytest.mark.asyncio
async def test_generate_repairs_one_invalid_plan_without_persisting_the_first_attempt(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, _planner, service = _service(tmp_path)
    planner = _RepairingPlanner()
    service.planner = planner

    generated = await service.generate("session-one", **_expected(creator, resource_plan))

    assert generated.state == "ready"
    assert len(planner.calls) == 2
    assert planner.calls[0].repair is None
    assert planner.calls[1].repair == {
        "attempt": 1,
        "error_code": "skill_creator_evolution_plan_invalid",
        "instruction": (
            "Return the complete contract again. Include one to twelve diagnoses "
            "bound only to allowed case, requirement, and resource IDs. For each "
            "diagnosis, copy evidence_item_ids exactly from "
            "allowed.evidence_item_ids_by_case[case_id]; never invent or abbreviate "
            "an ID. Copy each failure_types value exactly from "
            "allowed.failure_types. Validate the full replacement against "
            "output_contract_spec: include every required field, satisfy minItems, "
            "and never return an empty required string."
        ),
    }
    assert service.evolution_store.current_for_session("session-one").revision == 1


@pytest.mark.asyncio
async def test_confirm_revalidates_frozen_run_revision(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, run, _planner, service = _service(tmp_path)
    generated = await service.generate("session-one", **_expected(creator, resource_plan))
    run.revision += 1

    with pytest.raises(SkillCreatorConflictError):
        service.confirm(
            "session-one",
            plan_id=generated.plan_id,
            expected_session_revision=creator.session.session_revision,
            expected_draft_state_revision=creator.draft.revision,
            expected_draft_revision=creator.draft.content_revision,
            expected_draft_digest=creator.draft.content_digest,
            expected_plan_revision=generated.revision,
            expected_plan_digest=generated.digest,
        )

    assert service.evolution_store.require(generated.plan_id).state == "ready"


@pytest.mark.asyncio
async def test_confirm_crash_retry_cannot_cross_creator_sessions(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, _planner, service = _service(tmp_path)
    generated = await service.generate("session-one", **_expected(creator, resource_plan))
    service.evolution_store.confirm(
        generated.plan_id,
        expected_revision=generated.revision,
        expected_digest=generated.digest,
    )
    creator.session = replace(creator.session, session_id="session-two")
    creator.draft = replace(
        creator.draft,
        draft_id="draft-two",
        creator_session_id="session-two",
    )

    with pytest.raises(SkillCreatorConflictError):
        service.confirm(
            "session-two",
            plan_id=generated.plan_id,
            expected_session_revision=creator.session.session_revision,
            expected_draft_state_revision=creator.draft.revision,
            expected_draft_revision=creator.draft.content_revision,
            expected_draft_digest=creator.draft.content_digest,
            expected_plan_revision=generated.revision,
            expected_plan_digest=generated.digest,
        )


@pytest.mark.asyncio
async def test_generate_rejects_evidence_item_from_another_case(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, _planner, service = _service(tmp_path)
    service.planner = _CrossCasePlanner()

    with pytest.raises(SkillCreatorValidationError) as exc_info:
        await service.generate("session-one", **_expected(creator, resource_plan))

    assert exc_info.value.code == "skill_creator_evolution_evidence_invalid"
    assert service.evolution_store.current_for_session("session-one") is None


@pytest.mark.asyncio
async def test_projection_keeps_exact_materialized_plan_after_later_revision(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, _planner, service = _service(tmp_path)
    generated = await service.generate("session-one", **_expected(creator, resource_plan))
    confirmed, materialized = service.confirm(
        "session-one",
        plan_id=generated.plan_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_plan_revision=generated.revision,
        expected_plan_digest=generated.digest,
    )
    unrelated = service.resource_plan_store.patch(
        materialized.plan_id,
        expected_revision=materialized.revision,
        expected_digest=materialized.digest,
        changes={"skill_description": "A later unrelated resource plan."},
        allowed_source_ids={"intent", "expected_output", "near_miss:0", "success_criterion:0"},
    )
    service.resource_plan_store.confirm(
        unrelated.plan_id,
        expected_revision=unrelated.revision,
        expected_digest=unrelated.digest,
        session_revision=unrelated.session_revision,
        draft_revision=unrelated.draft_revision,
        draft_digest=unrelated.draft_digest,
    )

    projection = service.current_projection("session-one")
    assert projection is not None
    assert projection["resource_plan"]["revision"] == materialized.revision
    assert projection["resource_plan"]["digest"] == materialized.digest
    assert projection["digest"] == confirmed.digest


@pytest.mark.asyncio
async def test_missing_sessions_do_not_allocate_generation_locks(tmp_path: Path) -> None:
    creator, resource_plan, _suite_value, _run_value, _planner, service = _service(tmp_path)

    def missing_session(_session_id: str):
        raise SkillCreatorNotFoundError("missing")

    creator.get_session = missing_session  # type: ignore[method-assign]
    with pytest.raises(SkillCreatorNotFoundError):
        await service.generate("missing-session", **_expected(creator, resource_plan))

    assert "missing-session" not in service._locks


def test_evolution_runtime_is_no_tool_and_parses_one_versioned_object() -> None:
    request = EvolutionGenerationRequest(
        session={"session_id": "session-one", "session_revision": 7},
        draft={},
        evaluation={"items": [
            {"item_id": "item-candidate", "case_id": "case-normal"},
            {"item_id": "item-baseline", "case_id": "case-normal"},
        ]},
        review={}, suite={}, resource_plan={}, current_plan=None,
        allowed_case_ids=("case-normal",), allowed_item_ids=("item-candidate",), allowed_requirement_ids=(),
        allowed_resource_ids=(), allowed_source_ids=(), allowed_step_ids=(),
    )
    invocation = build_evolution_invocation(request, model_id="provider/model")
    agent = invocation.workflow["nodes"][1]["data"]
    assert agent["toolMode"] == "none"
    assert agent["temperature"] == "0.1"
    assert invocation.runtime_metadata["evolution_workflow_version"] == EVOLUTION_WORKFLOW_VERSION
    context = json.loads(invocation.inputs["creator_request"])
    assert context["allowed"]["evidence_item_ids_by_case"] == {
        "case-normal": ["item-candidate"],
    }
    assert "assertion_failure" in context["allowed"]["failure_types"]
    diagnosis_spec = context["output_contract_spec"]["properties"]["diagnoses"]
    assert diagnosis_spec["minItems"] == 1
    assert diagnosis_spec["items"]["properties"]["summary"]["minLength"] == 1
    payload = {"evolution_plan_version": EVOLUTION_PLAN_VERSION, "diagnoses": []}
    assert parse_evolution_output("Result:\n```json\n" + json.dumps(payload) + "\n```") == payload
    with pytest.raises(SkillCreatorValidationError):
        parse_evolution_output(json.dumps(payload) + json.dumps({**payload, "actions": []}))


def test_post_build_draft_accepts_only_the_exact_proposal_and_recomputed_package(tmp_path: Path) -> None:
    creator, resource_plan, suite, run, _planner, _service_value = _service(tmp_path)
    creator.draft.source_proposal_id = "proposal-from-build"
    creator.draft.content_digest = compute_package_digest(
        creator.draft.skill_markdown, creator.draft.files
    )
    build = SimpleNamespace(
        plan_id=resource_plan.plan_id,
        plan_revision=resource_plan.revision,
        plan_digest=resource_plan.digest,
        proposal_id="proposal-from-build",
        state="accepted",
        phase="proposal",
        skill_markdown=creator.draft.skill_markdown,
        resources=[
            SimpleNamespace(action="update", path=path, content=content)
            for path, content in creator.draft.files.items()
            if path.startswith(("scripts/", "references/", "assets/"))
        ],
    )
    planning = SkillCreatorResourcePlanningService(
        creator, _service_value.resource_plan_store, enabled=True  # type: ignore[arg-type]
    )
    service = SkillCreatorEvolutionService(
        creator,  # type: ignore[arg-type]
        SkillEvolutionPlanStore(tmp_path / "post-build-evolution"),
        _EvaluationStore(run),  # type: ignore[arg-type]
        _SuiteStore(suite),  # type: ignore[arg-type]
        planning,
        resource_build_store=SimpleNamespace(
            current_for_session=lambda _session_id: build
        ),  # type: ignore[arg-type]
        enabled=True,
    )
    assert service._resource_plan_produced_draft(
        resource_plan, session=creator.session, draft=creator.draft
    )
    build.state = "stale"
    assert service._resource_plan_produced_draft(
        resource_plan, session=creator.session, draft=creator.draft
    )
    build.resources[0].content = "print('tampered')\n"
    assert not service._resource_plan_produced_draft(
        resource_plan, session=creator.session, draft=creator.draft
    )


@pytest.mark.asyncio
async def test_workflow_evolution_planner_strips_version_before_store_payload() -> None:
    payload = {"evolution_plan_version": EVOLUTION_PLAN_VERSION, "diagnoses": []}
    planner = WorkflowCreatorEvolutionPlanner(
        model_id="provider/model", model_available=lambda: True,
        runner=lambda _invocation: _async_value(json.dumps(payload)),
    )
    request = EvolutionGenerationRequest(
        session={"session_id": "session-one", "session_revision": 7},
        draft={}, evaluation={}, review={}, suite={}, resource_plan={}, current_plan=None,
        allowed_case_ids=(), allowed_item_ids=(), allowed_requirement_ids=(),
        allowed_resource_ids=(), allowed_source_ids=(), allowed_step_ids=(),
    )
    assert await planner.generate(request) == {"diagnoses": []}


async def _async_value(value: str) -> str:
    return value
