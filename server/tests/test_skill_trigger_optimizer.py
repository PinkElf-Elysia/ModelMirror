from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.skills import api as skills_api
from server.skills import creator_api
from server.skills.creator_resource_plan import SkillResourcePlanStore
from server.skills.creator_resource_service import SkillCreatorResourcePlanningService
from server.skills.creator_store import (
    SkillCreatorSession,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.skills.creator_trigger_runtime import (
    TRIGGER_OPTIMIZER_WORKFLOW_VERSION,
    WorkflowCreatorTriggerOptimizationExecutor,
    build_trigger_optimization_invocation,
    parse_trigger_optimization_output,
)
from server.skills.creator_trigger_service import (
    SkillCreatorTriggerOptimizationService,
    SkillTriggerOptimizationStore,
    validate_trigger_description,
)
from server.skills.finder import SkillFinder
from server.skills.skill_manager import SkillManager, SkillValidationError
from server.skills.trigger_contract import SkillTriggerEvaluator, SkillTriggerStore
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import (
    AuthoringProposalStore,
    AuthoringProposalValidationError,
)
from server.xperts import XpertStore


ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"
SKILL_NAME = "incident-timeline-guide"
PASSING_DESCRIPTION = (
    "将软件服务故障记录整理为无责事故复盘，提取时间线、根因、影响与纠正行动；"
    "适用于已有故障证据的 incident postmortem、outage review 和 action items，"
    "并提供可核验的 incident timeline guide。"
)
SOURCE_IDS = {
    "intent",
    "positive_example:0",
    "near_miss:0",
    "expected_output",
    "success_criterion:0",
}


def trigger_cases() -> list[dict[str, str]]:
    return [
        {
            "kind": "should_trigger",
            "source": "model",
            "text": "请分析线上服务故障，整理时间线、根因与纠正措施",
        },
        {
            "kind": "should_trigger",
            "source": "model",
            "text": "Turn outage notes into a blameless incident postmortem with action items",
        },
        {
            "kind": "should_not_trigger",
            "source": "model",
            "text": "把产品发布公告压缩成三句话摘要",
        },
        {
            "kind": "should_not_trigger",
            "source": "model",
            "text": "编辑一份普通团队周报并调整语气",
        },
    ]


def plan_payload(description: str = "整理事故信息并输出报告，普通摘要不使用。") -> dict:
    return {
        "skill_name": SKILL_NAME,
        "skill_description": description,
        "workflow_steps": [
            {"id": "collect", "instruction": "Collect explicit incident facts."},
            {"id": "normalize", "instruction": "Normalize the incident timeline."},
            {"id": "analyze", "instruction": "Separate known facts from unknowns."},
            {"id": "deliver", "instruction": "Render and verify the postmortem."},
        ],
        "output_contract": ["Return an evidence-bound incident postmortem."],
        "failure_modes": ["Mark missing facts as pending confirmation."],
        "resources": [],
        "hooks": [],
        "clarifications": [],
    }


class StubSkillManager:
    def list_installed_skills(self):
        return []


class StubSessionStore:
    def __init__(self, session: SkillCreatorSession) -> None:
        self.session = session

    def list(self, *, limit: int = 500):
        return [self.session]


class StubCreatorService:
    def __init__(self, session: SkillCreatorSession) -> None:
        self.session = session
        self.session_store = StubSessionStore(session)

    def get_session(self, session_id: str):
        assert session_id == self.session.session_id
        return self.session, None

    @staticmethod
    def require_enabled() -> None:
        return None


class StubPlanningService:
    def __init__(self, store: SkillResourcePlanStore) -> None:
        self.plan_store = store

    @staticmethod
    def _require_plan_scope(plan, *, session, draft) -> None:
        assert plan.session_id == session.session_id
        assert draft is None

    def patch(self, session_id: str, **kwargs):
        assert session_id
        return self.plan_store.patch(
            kwargs["plan_id"],
            expected_revision=kwargs["expected_plan_revision"],
            expected_digest=kwargs["expected_plan_digest"],
            changes=kwargs["changes"],
            allowed_source_ids=SOURCE_IDS,
        )


class StubExecutor:
    def __init__(self) -> None:
        self.suite_context = None
        self.description_context = None

    def available(self) -> bool:
        return True

    async def generate_suite(self, context):
        self.suite_context = context
        return trigger_cases()

    async def optimize_descriptions(self, context):
        self.description_context = context
        return [PASSING_DESCRIPTION]


def make_service(tmp_path: Path, *, executor=None):
    session = SkillCreatorSession(
        session_id="skillcreator-trigger-test",
        session_revision=1,
        authoring_flow="resource",
        intent="根据软件故障记录生成无责事故复盘",
        positive_examples=["线上故障时间线", "outage postmortem"],
        near_miss_examples=["普通摘要", "团队周报"],
        expected_output="一份有证据边界的事故复盘",
        success_criteria=["不编造缺失事实"],
    )
    plan_store = SkillResourcePlanStore(tmp_path / "plans")
    plan = plan_store.save_generated(
        session_id=session.session_id,
        session_revision=session.session_revision,
        draft_id=None,
        draft_revision=None,
        draft_digest=None,
        payload=plan_payload(),
        allowed_source_ids=SOURCE_IDS,
    )
    creator = StubCreatorService(session)
    planning = StubPlanningService(plan_store)
    trigger_store = SkillTriggerStore(tmp_path / "triggers")
    optimization_store = SkillTriggerOptimizationStore(tmp_path / "attempts")
    service = SkillCreatorTriggerOptimizationService(
        creator,  # type: ignore[arg-type]
        planning,  # type: ignore[arg-type]
        trigger_store,
        optimization_store,
        SkillTriggerEvaluator(
            SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
        ),
        actor_id="local-console-instance",
        executor=executor,
        enabled=True,
    )
    return service, session, plan


@pytest.mark.asyncio
async def test_suite_optimize_confirm_and_plan_gate_form_one_closed_loop(tmp_path: Path) -> None:
    executor = StubExecutor()
    service, session, plan = make_service(tmp_path, executor=executor)

    draft_suite = await service.generate_suite(
        session.session_id,
        expected_session_revision=session.session_revision,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=None,
        expected_suite_digest=None,
    )
    suite = service.confirm_suite(
        session.session_id,
        suite_id=draft_suite.suite_id,
        expected_session_revision=session.session_revision,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=draft_suite.suite_revision,
        expected_suite_digest=draft_suite.suite_digest,
    )
    attempt = await service.optimize(
        session.session_id,
        expected_session_revision=session.session_revision,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=suite.suite_revision,
        expected_suite_digest=suite.suite_digest,
    )
    assert len(attempt.candidates) == 1
    assert attempt.candidates[0].passed is True
    assert attempt.recommended_description_digest == attempt.candidates[0].description_digest

    confirmed, updated_plan = service.confirm_description(
        session.session_id,
        attempt_id=attempt.attempt_id,
        selected_description_digest=attempt.recommended_description_digest,
        expected_attempt_revision=attempt.revision,
        expected_attempt_digest=attempt.digest,
        expected_session_revision=session.session_revision,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=suite.suite_revision,
        expected_suite_digest=suite.suite_digest,
    )
    assert confirmed.state == "confirmed"
    assert updated_plan.skill_description == PASSING_DESCRIPTION
    assert service.require_plan_gate(session, updated_plan).passed is True
    session.draft_id = "skilldraft-trigger-test"
    install_receipt = service.require_draft_install_gate(
        SimpleNamespace(
            draft_id=session.draft_id,
            slug=SKILL_NAME,
            description=PASSING_DESCRIPTION,
        )
    )
    assert install_receipt is not None and install_receipt.passed is True
    assert executor.suite_context == {
        "session_id": session.session_id,
        "skill_name": SKILL_NAME,
        "current_description": plan.skill_description,
        "intent": session.intent,
        "positive_examples": session.positive_examples,
        "near_miss_examples": session.near_miss_examples,
    }
    assert all(set(item) == {"name", "category", "description"} for item in executor.description_context["public_competitors"])
    serialized_context = json.dumps(executor.description_context, ensure_ascii=False)
    assert "workspace://" not in serialized_context
    assert "trust" not in serialized_context.casefold()


def test_manual_path_requires_confirmation_but_reuses_it_after_resource_only_change(tmp_path: Path) -> None:
    service, session, plan = make_service(tmp_path)
    draft_suite = service.save_suite(
        session.session_id,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        cases=trigger_cases(),
        expected_suite_revision=None,
        expected_suite_digest=None,
        change_reason="User confirmed the boundary examples.",
    )
    suite = service.confirm_suite(
        session.session_id,
        suite_id=draft_suite.suite_id,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=draft_suite.suite_revision,
        expected_suite_digest=draft_suite.suite_digest,
    )
    attempt = service.evaluate_description(
        session.session_id,
        description=PASSING_DESCRIPTION,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=suite.suite_revision,
        expected_suite_digest=suite.suite_digest,
    )
    with pytest.raises(SkillCreatorValidationError) as unconfirmed:
        service.require_plan_gate(session, plan)
    assert unconfirmed.value.code == "skill_trigger_gate_required"

    _, updated = service.confirm_description(
        session.session_id,
        attempt_id=attempt.attempt_id,
        selected_description_digest=attempt.candidates[0].description_digest,
        expected_attempt_revision=attempt.revision,
        expected_attempt_digest=attempt.digest,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=suite.suite_revision,
        expected_suite_digest=suite.suite_digest,
    )
    changed_resources = service.planning_service.patch(
        session.session_id,
        plan_id=updated.plan_id,
        expected_session_revision=1,
        expected_plan_revision=updated.revision,
        expected_plan_digest=updated.digest,
        changes={"output_contract": ["Return the same review plus an action-owner table."]},
    )
    assert service.require_plan_gate(session, changed_resources).passed is True

    changed_description = service.planning_service.patch(
        session.session_id,
        plan_id=changed_resources.plan_id,
        expected_session_revision=1,
        expected_plan_revision=changed_resources.revision,
        expected_plan_digest=changed_resources.digest,
        changes={"skill_description": "Create a general report for any task; do not use when unnecessary."},
    )
    with pytest.raises(SkillCreatorValidationError) as stale:
        service.require_plan_gate(session, changed_description)
    assert stale.value.code == "skill_trigger_gate_required"


def test_failed_description_has_no_recommendation_and_cannot_change_plan(tmp_path: Path) -> None:
    service, session, plan = make_service(tmp_path)
    draft_suite = service.save_suite(
        session.session_id,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        cases=trigger_cases(),
        expected_suite_revision=None,
        expected_suite_digest=None,
        change_reason="User confirmed the boundary examples.",
    )
    suite = service.confirm_suite(
        session.session_id,
        suite_id=draft_suite.suite_id,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=draft_suite.suite_revision,
        expected_suite_digest=draft_suite.suite_digest,
    )
    attempt = service.evaluate_description(
        session.session_id,
        description="将任意输入原样返回，不执行分析或报告。",
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        expected_suite_revision=suite.suite_revision,
        expected_suite_digest=suite.suite_digest,
    )
    assert attempt.recommended_description_digest is None
    assert attempt.candidates[0].passed is False
    with pytest.raises(SkillCreatorValidationError) as blocked:
        service.confirm_description(
            session.session_id,
            attempt_id=attempt.attempt_id,
            selected_description_digest=attempt.candidates[0].description_digest,
            expected_attempt_revision=attempt.revision,
            expected_attempt_digest=attempt.digest,
            expected_session_revision=1,
            plan_id=plan.plan_id,
            expected_plan_revision=plan.revision,
            expected_plan_digest=plan.digest,
            expected_suite_revision=suite.suite_revision,
            expected_suite_digest=suite.suite_digest,
        )
    assert blocked.value.code == "skill_trigger_evaluation_failed"
    assert service.planning_service.plan_store.require(plan.plan_id) == plan


def test_disabled_flag_bypasses_existing_required_session_without_deleting_state(tmp_path: Path) -> None:
    service, session, plan = make_service(tmp_path)
    service.optimization_store.mark_required(session.session_id)
    session.draft_id = "skilldraft-trigger-disabled"
    service.enabled = False

    assert service.require_plan_gate(session, plan) is None
    assert service.require_draft_install_gate(
        SimpleNamespace(
            draft_id=session.draft_id,
            slug=SKILL_NAME,
            description=plan.skill_description,
        )
    ) is None
    assert service.projection(session, plan) == {
        "trigger_required": False,
        "trigger_suite": None,
        "trigger_attempt": None,
        "trigger_receipt": None,
        "trigger_stale_reason": None,
    }
    assert service.optimization_store.is_required(session.session_id) is True


def test_resource_plan_confirmation_calls_server_gate_before_state_change(tmp_path: Path) -> None:
    service, session, plan = make_service(tmp_path)
    observed: list[str] = []

    def reject(_session, checked_plan, _draft):
        observed.append(checked_plan.plan_id)
        raise SkillCreatorValidationError(
            "Trigger confirmation is missing.", code="skill_trigger_gate_required"
        )

    planning = SkillCreatorResourcePlanningService(
        service.creator_service,  # type: ignore[arg-type]
        service.planning_service.plan_store,
        confirmation_gate=reject,
        enabled=True,
    )
    with pytest.raises(SkillCreatorValidationError) as blocked:
        planning.confirm(
            session.session_id,
            plan_id=plan.plan_id,
            expected_session_revision=session.session_revision,
            expected_plan_revision=plan.revision,
            expected_plan_digest=plan.digest,
        )
    assert blocked.value.code == "skill_trigger_gate_required"
    assert observed == [plan.plan_id]
    assert planning.plan_store.require(plan.plan_id).state == "ready"


def test_authoring_approval_guard_runs_before_creator_proposal_is_applied(tmp_path: Path) -> None:
    proposals = AuthoringProposalStore(tmp_path / "proposals")
    drafts = WorkspaceSkillDraftStore(tmp_path / "drafts")

    def reject_stale_trigger(_proposal) -> None:
        raise AuthoringProposalValidationError(
            "Trigger receipt is stale.", code="skill_trigger_receipt_stale"
        )

    authoring = AuthoringService(
        proposals,
        XpertStore(tmp_path / "xperts"),
        drafts,
        local_console_actor_id="console-test",
        skill_proposal_approval_guard=reject_stale_trigger,
    )
    proposal = proposals.create(
        kind="skill_create",
        title="Trigger-gated draft",
        payload={
            "skill": {
                "name": SKILL_NAME,
                "slug": SKILL_NAME,
                "description": PASSING_DESCRIPTION,
                "skill_markdown": (
                    f"---\nname: {SKILL_NAME}\ndescription: {PASSING_DESCRIPTION}\n---\n\n# Workflow\n\nUse evidence."
                ),
                "files": {},
            }
        },
        source_type="skill_creator",
        source_id="skillcreator-one",
        creator_session_id="skillcreator-one",
        creator_session_revision=1,
    )
    with pytest.raises(AuthoringProposalValidationError) as blocked:
        authoring.approve(
            proposal.proposal_id,
            revision=proposal.revision,
            apply_key=proposal.apply_key,
            reason="Review complete.",
        )
    assert blocked.value.code == "skill_trigger_receipt_stale"
    assert drafts.list() == []


@pytest.mark.asyncio
async def test_missing_model_does_not_block_manual_evaluation(tmp_path: Path) -> None:
    service, session, plan = make_service(tmp_path)
    with pytest.raises(SkillCreatorValidationError) as unavailable:
        await service.generate_suite(
            session.session_id,
            expected_session_revision=1,
            plan_id=plan.plan_id,
            expected_plan_revision=plan.revision,
            expected_plan_digest=plan.digest,
            expected_suite_revision=None,
            expected_suite_digest=None,
        )
    assert unavailable.value.code == "skill_trigger_optimizer_unconfigured"

    suite = service.save_suite(
        session.session_id,
        expected_session_revision=1,
        plan_id=plan.plan_id,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
        cases=trigger_cases(),
        expected_suite_revision=None,
        expected_suite_digest=None,
        change_reason="Manual no-key path.",
    )
    assert suite.state == "draft"


@pytest.mark.parametrize(
    "description",
    [
        "line one\nline two",
        "OPENROUTER_API_KEY=sk-" + "x" * 48,
        "TODO describe this later",
        "--- yaml control",
        "audit audit audit audit reports",
        "事故事故事故事故处理",
    ],
)
def test_description_validation_rejects_injection_secrets_and_keyword_stuffing(description: str) -> None:
    with pytest.raises(SkillCreatorValidationError) as error:
        validate_trigger_description(description)
    assert error.value.code == "skill_trigger_description_invalid"


def test_optimization_store_fails_closed_on_top_level_corruption(tmp_path: Path) -> None:
    store = SkillTriggerOptimizationStore(tmp_path)
    store.mark_required("skillcreator-one")
    original = store.snapshot_path.read_bytes()
    isolated_payload = json.loads(original)
    isolated_payload["attempts"] = [{"attempt_id": "broken"}]
    store.snapshot_path.write_text(json.dumps(isolated_payload), encoding="utf-8")
    isolated = SkillTriggerOptimizationStore(tmp_path)
    assert isolated.is_required("skillcreator-one") is True
    assert isolated.status()["quarantined_record_count"] == 1

    store.snapshot_path.write_text("{broken", encoding="utf-8")
    corrupted = SkillTriggerOptimizationStore(tmp_path)
    with pytest.raises(SkillCreatorStorageError):
        corrupted.is_required("skillcreator-one")
    assert store.snapshot_path.read_text(encoding="utf-8") == "{broken"
    assert original != store.snapshot_path.read_bytes()


def test_fixed_runtime_has_no_tools_and_strict_output_contract() -> None:
    invocation = build_trigger_optimization_invocation(
        operation="optimize_descriptions",
        context={"session_id": "skillcreator-one", "intent": "synthetic"},
        model_id="test-model",
    )
    agent = invocation.workflow["nodes"][1]["data"]
    assert agent["toolMode"] == "none"
    assert agent["maxToolCalls"] == "1"
    assert invocation.runtime_metadata["trigger_operation"] == "optimize_descriptions"
    assert invocation.runtime_metadata["creator_session_id"] == "skillcreator-one"
    assert "session_id" not in json.loads(invocation.inputs["creator_request"])
    assert parse_trigger_optimization_output(
        json.dumps(
            {
                "version": TRIGGER_OPTIMIZER_WORKFLOW_VERSION,
                "descriptions": [PASSING_DESCRIPTION],
            }
        )
    )["descriptions"] == [PASSING_DESCRIPTION]
    with pytest.raises(SkillCreatorValidationError):
        parse_trigger_optimization_output("not json")


@pytest.mark.asyncio
async def test_runtime_rejects_model_control_fields() -> None:
    async def runner(_invocation):
        return json.dumps(
            {
                "version": TRIGGER_OPTIMIZER_WORKFLOW_VERSION,
                "cases": [
                    {
                        "kind": "should_trigger",
                        "text": "valid text",
                        "rank": 1,
                    }
                ],
            }
        )

    executor = WorkflowCreatorTriggerOptimizationExecutor(
        model_id="test-model", model_available=lambda: True, runner=runner
    )
    with pytest.raises(SkillCreatorValidationError) as error:
        await executor.generate_suite({"session_id": "skillcreator-one"})
    assert error.value.code == "skill_trigger_optimizer_invalid"


@pytest.mark.asyncio
async def test_trigger_api_preserves_all_optimistic_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    class TriggerApiStub:
        async def generate_suite(self, _session_id, **kwargs):
            calls.append(("generate", kwargs))

        def save_suite(self, _session_id, **kwargs):
            calls.append(("patch", kwargs))

        def confirm_suite(self, _session_id, **kwargs):
            calls.append(("confirm-suite", kwargs))

        async def optimize(self, _session_id, **kwargs):
            calls.append(("optimize", kwargs))

        def evaluate_description(self, _session_id, **kwargs):
            calls.append(("evaluate", kwargs))

        def confirm_description(self, _session_id, **kwargs):
            calls.append(("confirm-description", kwargs))

    session = SkillCreatorSession(session_id="skillcreator-api")
    creator = SimpleNamespace(
        VERSION="skill-creator-v1",
        get_session=lambda _session_id: (session, None),
        serialize_draft=lambda draft: draft,
    )
    monkeypatch.setattr(
        creator_api,
        "get_skill_creator_trigger_optimization_service",
        lambda: TriggerApiStub(),
    )
    monkeypatch.setattr(creator_api, "get_skill_creator_service", lambda: creator)
    monkeypatch.setattr(creator_api, "_resource_planning_service", None)
    monkeypatch.setattr(creator_api, "_resource_build_service", None)
    monkeypatch.setattr(creator_api, "_evaluation_service", None)
    monkeypatch.setattr(creator_api, "_evaluation_suite_service", None)
    monkeypatch.setattr(creator_api, "_evolution_service", None)
    monkeypatch.setattr(creator_api, "_trigger_optimization_service", None)
    app = FastAPI()
    app.include_router(creator_api.router)
    base = {
        "plan_id": "skillplan-api",
        "expected_session_revision": 3,
        "expected_plan_revision": 2,
        "expected_plan_digest": "a" * 64,
    }
    suite = {
        **base,
        "suite_id": "triggersuite-api",
        "expected_suite_revision": 4,
        "expected_suite_digest": "b" * 64,
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.post(
                "/api/skills/creator/sessions/skillcreator-api/trigger-suite/generate",
                json=base,
            ),
            await client.patch(
                "/api/skills/creator/sessions/skillcreator-api/trigger-suite",
                json={
                    **base,
                    "cases": [
                        {"kind": "should_trigger", "text": "positive one"},
                        {"kind": "should_trigger", "text": "positive two"},
                        {"kind": "should_not_trigger", "text": "negative one"},
                        {"kind": "should_not_trigger", "text": "negative two"},
                    ],
                    "change_reason": "User refined the boundary.",
                },
            ),
            await client.post(
                "/api/skills/creator/sessions/skillcreator-api/trigger-suite/confirm",
                json=suite,
            ),
            await client.post(
                "/api/skills/creator/sessions/skillcreator-api/trigger-descriptions/optimize",
                json=suite,
            ),
            await client.post(
                "/api/skills/creator/sessions/skillcreator-api/trigger-descriptions/evaluate",
                json={**suite, "description": PASSING_DESCRIPTION},
            ),
            await client.post(
                "/api/skills/creator/sessions/skillcreator-api/trigger-descriptions/triggerattempt-api/confirm",
                json={
                    **suite,
                    "expected_attempt_revision": 1,
                    "expected_attempt_digest": "c" * 64,
                    "selected_description_digest": "d" * 64,
                },
            ),
        ]
    assert [response.status_code for response in responses] == [200] * 6, [
        response.json() for response in responses
    ]
    assert [name for name, _ in calls] == [
        "generate",
        "patch",
        "confirm-suite",
        "optimize",
        "evaluate",
        "confirm-description",
    ]
    assert all(kwargs["plan_id"] == "skillplan-api" for _, kwargs in calls)
    assert calls[-1][1]["attempt_id"] == "triggerattempt-api"


@pytest.mark.asyncio
async def test_draft_install_keeps_legacy_validation_shape_but_structures_trigger_errors(
    tmp_path: Path,
) -> None:
    store = WorkspaceSkillDraftStore(tmp_path / "drafts-api")
    manager = SkillManager(
        installed_dir=tmp_path / "installed-api",
        tmp_dir=tmp_path / "tmp-api",
    )
    draft = store.create(
        name="trigger-error-shape",
        slug="trigger-error-shape",
        description="A safe draft used to verify the install error contract.",
        skill_markdown=(
            "---\nname: trigger-error-shape\n"
            "description: A safe draft used to verify the install error contract.\n"
            "---\n\n# Workflow\n\nUse the provided input.\n"
        ),
        files={},
    )
    previous_store = skills_api._skill_draft_store
    previous_manager = skills_api._skill_manager
    previous_guard = skills_api._workspace_draft_install_guard
    skills_api.set_skill_draft_store_for_tests(store)
    skills_api.set_skill_manager_for_tests(manager)
    app = FastAPI()
    app.include_router(skills_api.router)

    def reject_legacy(_draft) -> None:
        raise SkillValidationError("legacy validation failure", code="skill_package_invalid")

    def reject_trigger(_draft) -> None:
        raise SkillValidationError(
            "trigger receipt is stale", code="skill_trigger_receipt_stale"
        )

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            skills_api.configure_workspace_draft_install_guard(reject_legacy)
            legacy = await client.post(
                f"/api/skills/drafts/{draft.draft_id}/install",
                json={
                    "expected_revision": draft.revision,
                    "expected_digest": draft.content_digest,
                },
            )
            assert legacy.status_code == 400
            assert legacy.json()["detail"] == "legacy validation failure"

            skills_api.configure_workspace_draft_install_guard(reject_trigger)
            trigger = await client.post(
                f"/api/skills/drafts/{draft.draft_id}/install",
                json={
                    "expected_revision": draft.revision,
                    "expected_digest": draft.content_digest,
                },
            )
            assert trigger.status_code == 400
            assert trigger.json()["detail"] == {
                "code": "skill_trigger_receipt_stale",
                "message": "trigger receipt is stale",
                "details": {},
            }
    finally:
        skills_api._skill_draft_store = previous_store
        skills_api._skill_manager = previous_manager
        skills_api._workspace_draft_install_guard = previous_guard
