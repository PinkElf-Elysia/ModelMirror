from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from server.skills.creator_evaluation import (
    SkillEvaluationStore,
    SkillEvaluationValidationError,
)
from server.skills.creator_evaluation_suite import (
    EVALUATION_SUITE_VERSION,
    SkillEvaluationSuiteStore,
)
from server.skills.creator_evaluation_suite_runtime import (
    EVALUATION_SUITE_WORKFLOW_VERSION,
    WorkflowCreatorEvaluationSuiteGenerator,
    build_evaluation_suite_invocation,
    parse_evaluation_suite_output,
)
from server.skills.creator_evaluation_suite_service import (
    EvaluationSuiteGenerationRequest,
    SkillCreatorEvaluationSuiteService,
)
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from server.skills.draft_store import WorkspaceSkillDraft


DIGEST = "a" * 64


def _core_cases() -> list[dict]:
    return [
        {
            "case_id": f"core-{role}",
            "role": role,
            "name": f"{role.title()} case",
            "prompt": f"Exercise the {role} behavior.",
            "expected_behavior": "Return a safe and observable result.",
            "fixtures": [],
            "assertions": [],
            "requirement_ids": [
                "intent",
                "near_miss:0",
                "expected_output",
                "success_criterion:0",
            ],
            "required_resource_paths": [],
            "workflow_step_ids": [],
        }
        for role in ("normal", "ambiguous", "boundary")
    ]


class _CreatorService:
    def __init__(self) -> None:
        self.session = SkillCreatorSession(
            session_id="session-one",
            session_revision=4,
            draft_state_revision=2,
            intent="Summarize an incident without inventing facts.",
            positive_examples=["Turn a timeline into an incident review."],
            near_miss_examples=["Only rewrite prose; no incident review is needed."],
            expected_output="A structured incident review.",
            success_criteria=["Unknown root causes remain explicitly unknown."],
            evidence_confirmed=True,
            draft_id="draft-one",
            current_revision=1,
            current_digest=DIGEST,
            quality_mode="objective",
            state="designing_tests",
        )
        self.draft = WorkspaceSkillDraft(
            draft_id="draft-one",
            name="incident-review",
            slug="incident-review",
            description="Create incident reviews when a timeline is supplied.",
            skill_markdown=(
                "---\nname: incident-review\ndescription: Create incident reviews.\n---\n"
                "# Incident review\n\n## Workflow\n\n1. Read facts.\n2. Order facts.\n"
                "3. Mark gaps.\n4. Render output.\n"
            ),
            files={"references/policy.md": "# Policy\n\nNever invent a root cause.\n"},
            revision=2,
            content_revision=1,
            content_digest=DIGEST,
            creator_session_id="session-one",
            quality_required=True,
        )

    def require_enabled(self) -> None:
        return None

    def get_session(self, session_id: str):
        assert session_id == self.session.session_id
        return self.session, self.draft

    @staticmethod
    def _require_ready_for_generation(_session: SkillCreatorSession) -> None:
        return None


class _Generator:
    def __init__(self, creator: _CreatorService, *, mutate: bool = False) -> None:
        self.creator = creator
        self.mutate = mutate
        self.calls: list[EvaluationSuiteGenerationRequest] = []

    def available(self) -> bool:
        return True

    async def generate(self, request: EvaluationSuiteGenerationRequest) -> dict:
        self.calls.append(request)
        if self.mutate:
            self.creator.session.session_revision += 1
        return {"cases": _core_cases()}


class _IncompleteCoverageGenerator(_Generator):
    async def generate(self, request: EvaluationSuiteGenerationRequest) -> dict:
        self.calls.append(request)
        cases = _core_cases()
        for case in cases:
            case["requirement_ids"] = ["intent"]
        return {"cases": cases}


class _RepairingCoverageGenerator(_IncompleteCoverageGenerator):
    async def generate(self, request: EvaluationSuiteGenerationRequest) -> dict:
        if request.coverage_repair is None:
            return await super().generate(request)
        self.calls.append(request)
        return {"cases": _core_cases()}


def _service(tmp_path: Path, *, generator=None, enabled: bool = True):
    creator = _CreatorService()
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    evaluation_store = SkillEvaluationStore(tmp_path / "evaluation")
    return (
        creator,
        suite_store,
        evaluation_store,
        SkillCreatorEvaluationSuiteService(
            creator,  # type: ignore[arg-type]
            suite_store,
            evaluation_store,
            generator=generator,
            enabled=enabled,
        ),
    )


def _expected(creator: _CreatorService) -> dict:
    return {
        "expected_session_revision": creator.session.session_revision,
        "expected_draft_state_revision": creator.draft.revision,
        "expected_draft_revision": creator.draft.content_revision,
        "expected_draft_digest": creator.draft.content_digest,
        "expected_suite_revision": None,
        "expected_suite_digest": None,
    }


def test_fixed_generator_contract_is_no_tool_and_parses_one_versioned_object() -> None:
    request = EvaluationSuiteGenerationRequest(
        session={"session_id": "session-one", "session_revision": 4},
        draft={"draft_id": "draft-one"},
        resource_plan=None,
        allowed_requirement_ids=("intent",),
        allowed_resource_paths=(),
        allowed_workflow_step_ids=(),
    )
    invocation = build_evaluation_suite_invocation(request, model_id="provider/model")
    agent = invocation.workflow["nodes"][1]["data"]
    assert agent["toolMode"] == "none"
    assert agent["temperature"] == "0.1"
    assert "every ID in allowed_requirement_ids" in agent["rolePrompt"]
    assert invocation.runtime_metadata["evaluation_suite_workflow_version"] == (
        EVALUATION_SUITE_WORKFLOW_VERSION
    )
    context = json.loads(invocation.inputs["creator_request"])
    assert context["case_contract"]["model_may_add_regressions"] is False
    assert "exact_match" in context["case_contract"]["assertion_kinds"]
    assert "exact" not in context["case_contract"]["assertion_kinds"]

    payload = {"evaluation_suite_version": EVALUATION_SUITE_VERSION, "cases": []}
    parsed = parse_evaluation_suite_output(
        "Here is the result:\n```json\n" + json.dumps(payload) + "\n```"
    )
    assert parsed == payload
    with pytest.raises(SkillCreatorValidationError):
        parse_evaluation_suite_output(
            json.dumps(payload) + "\n" + json.dumps({**payload, "cases": [{}]})
        )


def test_suite_runtime_uses_dedicated_budget_and_temperature() -> None:
    import server.main as main_module

    request = EvaluationSuiteGenerationRequest(
        session={"session_id": "session-one", "session_revision": 4},
        draft={"draft_id": "draft-one"},
        resource_plan=None,
        allowed_requirement_ids=("intent",),
        allowed_resource_paths=(),
        allowed_workflow_step_ids=(),
    )
    metadata = build_evaluation_suite_invocation(
        request, model_id="provider/model"
    ).runtime_metadata

    assert (
        main_module.workflow_agent_token_budget(metadata)
        == main_module.SKILL_CREATOR_EVALUATION_SUITE_MAX_TOKENS
    )
    assert main_module.workflow_agent_temperature(metadata) == 0.1
    assert (
        main_module.workflow_agent_temperature(
            {**metadata, "creator_workflow_version": "untrusted"}
        )
        == 0.7
    )


@pytest.mark.asyncio
async def test_generate_freezes_model_cases_and_confirm_requires_current_facts(
    tmp_path: Path,
) -> None:
    creator = _CreatorService()
    creator.session.positive_examples = [
        "Turn a timeline into an incident review.",
        "Summarize an incident with an unknown root cause.",
        "Preserve owners while organizing corrective actions.",
    ]
    generator = _Generator(creator)
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "evaluation"),
        generator=generator,
        enabled=True,
    )
    generated = await service.generate(creator.session.session_id, **_expected(creator))
    assert generated.state == "draft"
    assert len(generator.calls) == 1
    assert generator.calls[0].allowed_requirement_ids == (
        "intent",
        "near_miss:0",
        "expected_output",
        "success_criterion:0",
    )
    assert generator.calls[0].allowed_resource_paths == ("references/policy.md",)

    confirmed = service.confirm(
        creator.session.session_id,
        suite_id=generated.suite_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_suite_revision=generated.suite_revision,
        expected_suite_digest=generated.suite_digest,
    )
    assert confirmed.state == "confirmed"
    assert service.current_projection(creator.session.session_id)["stale"] is False

    creator.session.session_revision += 1
    creator.draft.revision += 1
    assert service.current_projection(creator.session.session_id)["stale"] is False

    creator.session.intent = "Changed intent invalidates the frozen suite."
    assert service.current_projection(creator.session.session_id)["stale"] is True
    creator.session.intent = "Summarize an incident without inventing facts."

    creator.draft.content_digest = "b" * 64
    assert service.current_projection(creator.session.session_id)["stale"] is True


@pytest.mark.asyncio
async def test_patch_rebases_confirmed_suite_onto_a_new_draft_revision(
    tmp_path: Path,
) -> None:
    creator = _CreatorService()
    generator = _Generator(creator)
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "evaluation"),
        generator=generator,
        enabled=True,
    )
    generated = await service.generate(creator.session.session_id, **_expected(creator))
    confirmed = service.confirm(
        creator.session.session_id,
        suite_id=generated.suite_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_suite_revision=generated.suite_revision,
        expected_suite_digest=generated.suite_digest,
    )

    creator.session.session_revision += 1
    creator.session.current_revision = 2
    creator.session.current_digest = "b" * 64
    creator.draft.revision += 1
    creator.draft.content_revision = 2
    creator.draft.content_digest = "b" * 64
    assert service.current_projection(creator.session.session_id)["stale"] is True

    cases = _core_cases()
    cases[1]["assertions"] = [{"kind": "contains", "value": "Please provide"}]
    rebased = service.patch(
        creator.session.session_id,
        suite_id=confirmed.suite_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_suite_revision=confirmed.suite_revision,
        expected_suite_digest=confirmed.suite_digest,
        cases=cases,
        change_reason="Rebind the confirmed cases after reviewing the evolved draft.",
    )

    assert rebased.state == "draft"
    assert rebased.based_on_revision == confirmed.suite_revision
    assert rebased.draft_revision == creator.draft.content_revision
    assert rebased.draft_digest == creator.draft.content_digest
    assert service.current_projection(creator.session.session_id)["stale"] is False


@pytest.mark.asyncio
async def test_patch_rebases_unchanged_confirmed_suite_without_fake_user_reason(
    tmp_path: Path,
) -> None:
    creator = _CreatorService()
    generator = _Generator(creator)
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "evaluation"),
        generator=generator,
        enabled=True,
    )
    generated = await service.generate(creator.session.session_id, **_expected(creator))
    confirmed = service.confirm(
        creator.session.session_id,
        suite_id=generated.suite_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_suite_revision=generated.suite_revision,
        expected_suite_digest=generated.suite_digest,
    )

    creator.session.session_revision += 1
    creator.session.current_revision = 2
    creator.session.current_digest = "b" * 64
    creator.draft.revision += 1
    creator.draft.content_revision = 2
    creator.draft.content_digest = "b" * 64
    unchanged_cases = [asdict(item) for item in confirmed.cases]

    changed_cases = [dict(item) for item in unchanged_cases]
    changed_cases[0] = {
        **changed_cases[0],
        "expected_behavior": "A materially different expectation.",
    }
    changed_cases[0].pop("case_fingerprint")
    with pytest.raises(SkillEvaluationValidationError) as reason_error:
        service.patch(
            creator.session.session_id,
            suite_id=confirmed.suite_id,
            expected_session_revision=creator.session.session_revision,
            expected_draft_state_revision=creator.draft.revision,
            expected_draft_revision=creator.draft.content_revision,
            expected_draft_digest=creator.draft.content_digest,
            expected_suite_revision=confirmed.suite_revision,
            expected_suite_digest=confirmed.suite_digest,
            cases=changed_cases,
            change_reason="",
        )
    assert reason_error.value.code == "skill_evaluation_suite_change_reason_required"

    rebased = service.patch(
        creator.session.session_id,
        suite_id=confirmed.suite_id,
        expected_session_revision=creator.session.session_revision,
        expected_draft_state_revision=creator.draft.revision,
        expected_draft_revision=creator.draft.content_revision,
        expected_draft_digest=creator.draft.content_digest,
        expected_suite_revision=confirmed.suite_revision,
        expected_suite_digest=confirmed.suite_digest,
        cases=unchanged_cases,
        change_reason="",
    )

    assert rebased.state == "draft"
    assert rebased.draft_revision == 2
    assert rebased.draft_digest == "b" * 64
    assert rebased.change_reason == (
        "Rebased unchanged confirmed suite onto draft revision 2."
    )
    assert rebased.cases == confirmed.cases


@pytest.mark.asyncio
async def test_generate_rejects_incomplete_requirement_coverage_before_persisting(
    tmp_path: Path,
) -> None:
    creator = _CreatorService()
    generator = _IncompleteCoverageGenerator(creator)
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "evaluation"),
        generator=generator,
        enabled=True,
    )

    with pytest.raises(SkillCreatorValidationError) as captured:
        await service.generate(creator.session.session_id, **_expected(creator))

    assert captured.value.code == (
        "skill_evaluation_suite_generator_coverage_incomplete"
    )
    assert len(generator.calls) == 2
    repair = generator.calls[1].coverage_repair
    assert repair is not None
    assert repair["missing_requirement_ids"] == [
        "near_miss:0",
        "expected_output",
        "success_criterion:0",
    ]
    assert len(repair["previous_cases"]) == 3
    assert suite_store.current_for_session(creator.session.session_id) is None


@pytest.mark.asyncio
async def test_generate_repairs_incomplete_coverage_once_before_persisting(
    tmp_path: Path,
) -> None:
    creator = _CreatorService()
    generator = _RepairingCoverageGenerator(creator)
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "evaluation"),
        generator=generator,
        enabled=True,
    )

    suite = await service.generate(creator.session.session_id, **_expected(creator))

    assert suite.state == "draft"
    assert suite.suite_revision == 1
    assert len(generator.calls) == 2
    assert generator.calls[1].coverage_repair is not None
    assert suite_store.current_for_session(creator.session.session_id) == suite


@pytest.mark.asyncio
async def test_existing_v1_cases_migrate_without_calling_model(tmp_path: Path) -> None:
    creator, suite_store, evaluation_store, _ = _service(tmp_path)
    legacy = evaluation_store.save_cases(
        session_id=creator.session.session_id,
        draft_id=creator.draft.draft_id,
        draft_revision=creator.draft.content_revision,
        content_digest=creator.draft.content_digest,
        expected_revision=0,
        cases=[
            {
                key: value
                for key, value in case.items()
                if key
                not in {
                    "role",
                    "requirement_ids",
                    "required_resource_paths",
                    "workflow_step_ids",
                }
            }
            for case in _core_cases()
        ],
        quality_mode="objective",
    )
    creator.session.cases_revision = legacy.cases_revision
    generator = _Generator(creator)
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        evaluation_store,
        generator=generator,
        enabled=True,
    )
    migrated = await service.generate(creator.session.session_id, **_expected(creator))
    assert migrated.state == "confirmed"
    assert all(case.source == "migrated" for case in migrated.cases)
    assert generator.calls == []


@pytest.mark.asyncio
async def test_generation_discards_stale_model_result_and_flag_fails_closed(
    tmp_path: Path,
) -> None:
    creator = _CreatorService()
    generator = _Generator(creator, mutate=True)
    suite_store = SkillEvaluationSuiteStore(tmp_path / "suite")
    service = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "evaluation"),
        generator=generator,
        enabled=True,
    )
    with pytest.raises(SkillCreatorConflictError):
        await service.generate(creator.session.session_id, **_expected(creator))
    assert suite_store.current_for_session(creator.session.session_id) is None

    disabled = SkillCreatorEvaluationSuiteService(
        creator,  # type: ignore[arg-type]
        suite_store,
        SkillEvaluationStore(tmp_path / "disabled-evaluation"),
        generator=generator,
        enabled=False,
    )
    with pytest.raises(SkillCreatorValidationError) as captured:
        await disabled.generate(
            creator.session.session_id,
            expected_session_revision=creator.session.session_revision,
            expected_draft_state_revision=creator.draft.revision,
            expected_draft_revision=creator.draft.content_revision,
            expected_draft_digest=creator.draft.content_digest,
            expected_suite_revision=None,
            expected_suite_digest=None,
        )
    assert captured.value.code == "skill_creator_evolution_v2_disabled"


@pytest.mark.asyncio
async def test_workflow_generator_rejects_wrong_version_before_store_write() -> None:
    request = EvaluationSuiteGenerationRequest(
        session={"session_id": "session-one", "session_revision": 4},
        draft={"draft_id": "draft-one"},
        resource_plan=None,
        allowed_requirement_ids=("intent",),
        allowed_resource_paths=(),
        allowed_workflow_step_ids=(),
    )

    async def runner(_invocation):
        return json.dumps({"evaluation_suite_version": "old", "cases": []})

    generator = WorkflowCreatorEvaluationSuiteGenerator(
        model_id="provider/model", model_available=lambda: True, runner=runner
    )
    with pytest.raises(SkillCreatorValidationError) as captured:
        await generator.generate(request)
    assert captured.value.code == "skill_evaluation_suite_generator_invalid"
