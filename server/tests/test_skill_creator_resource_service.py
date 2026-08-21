from __future__ import annotations

import json
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI

import server.main as main_module
from server.skills import creator_api
from server.skills.creator_resource_plan import (
    RESOURCE_PLAN_VERSION,
    SkillResourcePlanStore,
)
from server.skills.creator_resource_build import SkillResourceBuildStore
from server.skills.creator_resource_build_runtime import (
    ResourceBuildGenerationRequest,
    build_resource_builder_invocation,
)
from server.skills.creator_resource_runtime import (
    WorkflowCreatorResourcePlanner,
    build_resource_planner_invocation,
    parse_resource_plan_output,
)
from server.skills.creator_resource_service import (
    ResourcePlanningRequest,
    SkillCreatorResourcePlanningService,
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
from server.xpert_runtime import WorkflowExecutionStore
from server.xpert_runtime.run_registry import RunRegistry
from server.xperts import XpertStore


def _plan_payload(*, clarifications=None, resources=None):
    return {
        "skill_name": "review-incidents",
        "skill_description": (
            "Create evidence-bound incident reviews when a user needs a timeline and "
            "corrective actions; do not use for generic rewriting."
        ),
        "workflow_steps": [
            {"id": "collect", "instruction": "Collect explicit incident facts."},
            {"id": "normalize", "instruction": "Normalize times and missing values."},
            {"id": "analyze", "instruction": "Separate known causes from unknowns."},
            {"id": "deliver", "instruction": "Render and verify the final review."},
        ],
        "output_contract": ["Return a Chinese Markdown incident report."],
        "failure_modes": ["Mark unavailable facts as pending confirmation."],
        "resources": [
            {"generation_cost": "medium", **item}
            for item in (resources or [])
        ],
        "clarifications": clarifications or [],
    }


class _Planner:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[ResourcePlanningRequest] = []

    def available(self) -> bool:
        return True

    async def plan(self, request: ResourcePlanningRequest):
        self.requests.append(request)
        return self.payloads.pop(0)


def _services(tmp_path: Path, planner, *, enabled=True):
    runtime = tmp_path / "runtime"
    drafts = WorkspaceSkillDraftStore(runtime)
    authoring = AuthoringService(
        AuthoringProposalStore(runtime),
        XpertStore(tmp_path / "xperts"),
        drafts,
        local_console_actor_id="console_test",
    )
    creator = SkillCreatorService(
        SkillCreatorSessionStore(runtime),
        drafts,
        authoring,
        enabled=True,
    )
    planning = SkillCreatorResourcePlanningService(
        creator,
        SkillResourcePlanStore(runtime),
        planner=planner,
        enabled=enabled,
    )
    session = creator.create_session(
        mode="blank",
        intent="Turn incident facts into a repeatable review.",
        positive_examples=["Review a deployment incident."],
        near_miss_examples=["Rewrite this paragraph."],
        expected_output="A Chinese Markdown incident review.",
        success_criteria=["Never invent a root cause."],
    )
    preview = creator.preview_source(session.session_id)
    session = creator.select_evidence(
        session.session_id,
        expected_session_revision=session.session_revision,
        preview_fingerprint=preview.fingerprint,
        candidate_ids=[],
    )
    return creator, planning, drafts, session


@pytest.mark.asyncio
async def test_planning_service_clarifies_regenerates_and_confirms(tmp_path: Path) -> None:
    planner = _Planner(
        [
            _plan_payload(
                clarifications=[
                    {
                        "id": "severity_source",
                        "question": "Which source defines severity levels?",
                        "reason": "No authoritative mapping was supplied.",
                    }
                ]
            ),
            _plan_payload(),
        ]
    )
    creator, planning, _, session = _services(tmp_path, planner)

    first = await planning.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=None,
        expected_plan_digest=None,
    )
    assert first.state == "needs_input"
    session, _ = creator.get_session(session.session_id)
    answered = planning.save_answers(
        session.session_id,
        plan_id=first.plan_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=first.revision,
        expected_plan_digest=first.digest,
        answers={"severity_source": "No severity policy exists; omit severity."},
    )
    second = await planning.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=answered.revision,
        expected_plan_digest=answered.digest,
    )
    assert second.state == "ready"
    assert planner.requests[-1].current_plan["clarification_answers"] == {
        "severity_source": "No severity policy exists; omit severity."
    }
    confirmed = planning.confirm(
        session.session_id,
        plan_id=second.plan_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=second.revision,
        expected_plan_digest=second.digest,
    )
    assert confirmed.state == "confirmed"


@pytest.mark.asyncio
async def test_definition_change_makes_existing_plan_projection_stale(tmp_path: Path) -> None:
    planner = _Planner([_plan_payload(), _plan_payload()])
    creator, planning, _, session = _services(tmp_path, planner)
    plan = await planning.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=None,
        expected_plan_digest=None,
    )
    session, _ = creator.get_session(session.session_id)
    confirmed = planning.confirm(
        session.session_id,
        plan_id=plan.plan_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
    )
    updated = creator.update_definition(
        session.session_id,
        expected_session_revision=session.session_revision,
        changes={"intent": "Review incidents and preserve action owners."},
    )
    projection = planning.current_projection(updated.session_id)
    assert projection is not None
    assert projection["plan_id"] == plan.plan_id
    assert projection["stale"] is True
    preview = creator.preview_source(updated.session_id)
    updated = creator.select_evidence(
        updated.session_id,
        expected_session_revision=updated.session_revision,
        preview_fingerprint=preview.fingerprint,
        candidate_ids=[],
    )
    regenerated = await planning.generate(
        updated.session_id,
        expected_session_revision=updated.session_revision,
        expected_plan_revision=confirmed.revision,
        expected_plan_digest=confirmed.digest,
    )
    assert regenerated.plan_id == confirmed.plan_id
    assert regenerated.revision == confirmed.revision + 1
    assert regenerated.state == "ready"
    assert regenerated.session_revision == updated.session_revision


@pytest.mark.asyncio
async def test_update_plan_accounts_for_every_existing_resource(tmp_path: Path) -> None:
    planner = _Planner([_plan_payload()])
    creator, planning, drafts, session = _services(tmp_path, planner)
    draft = drafts.create(
        name="review-incidents",
        slug="review-incidents",
        description="Review incidents when users need evidence-bound corrective actions.",
        skill_markdown=(
            "---\nname: review-incidents\n"
            "description: Review incidents when users need evidence-bound corrective actions.\n"
            "---\n\n# Review incidents\n\nUse the evidence policy.\n"
        ),
        files={"references/evidence.md": "# Evidence policy\n\nUse explicit facts.\n"},
        creator_session_id=session.session_id,
        quality_required=True,
    )
    session = creator.session_store.bind_draft(
        session.session_id,
        expected_session_revision=session.session_revision,
        draft_id=draft.draft_id,
        draft_state_revision=draft.revision,
        content_revision=draft.content_revision,
        content_digest=draft.content_digest,
    )
    with pytest.raises(SkillCreatorValidationError) as caught:
        await planning.generate(
            session.session_id,
            expected_session_revision=session.session_revision,
            expected_plan_revision=None,
            expected_plan_digest=None,
        )
    assert caught.value.code == "skill_creator_resource_action_incomplete"
    assert planning.plan_store.current_for_session(session.session_id) is None


def test_resource_planner_workflow_has_no_tools_and_strict_contract() -> None:
    request = ResourcePlanningRequest(
        session={
            "session_id": "skillcreator_test",
            "session_revision": 3,
            "intent": "Create incident reviews.",
            "positive_examples": ["Review this outage."],
            "near_miss_examples": ["Rewrite this paragraph."],
            "expected_output": "A Markdown report.",
            "success_criteria": ["Do not invent causes."],
            "selected_evidence": [],
        },
        target_draft=None,
        current_plan=None,
        allowed_source_ids=["intent", "expected_output"],
    )
    invocation = build_resource_planner_invocation(
        request, model_id="gateway/default-text"
    )
    agent = next(
        node for node in invocation.workflow["nodes"] if node["data"]["kind"] == "workflow_agent"
    )
    assert agent["data"]["toolMode"] == "none"
    assert agent["data"]["maxToolCalls"] == "1"
    assert "plan before writing" in agent["data"]["rolePrompt"].lower()
    assert "do not echo operation" in agent["data"]["rolePrompt"].lower()
    assert "return one typed proposal" not in agent["data"]["rolePrompt"].lower()
    assert "row-level normalization" in agent["data"]["rolePrompt"].lower()
    assert "do not create a script for subjective judgment" in agent["data"][
        "rolePrompt"
    ].lower()
    assert "same primary natural language as definition.intent" in agent["data"][
        "rolePrompt"
    ].lower()
    assert "source ids are opaque server tokens" in agent["data"]["rolePrompt"].lower()
    assert "## 1. Turn the session into explicit requirements" in agent["data"][
        "rolePrompt"
    ]
    assert invocation.runtime_metadata["creator_phase"] == "resource_plan"
    assert json.loads(invocation.inputs["creator_request"])["planning_limits"][
        "resource_count_max"
    ] == 20


@pytest.mark.asyncio
async def test_workflow_resource_planner_accepts_only_versioned_json() -> None:
    request = ResourcePlanningRequest(
        session={"session_id": "skillcreator_test", "session_revision": 3},
        target_draft=None,
        current_plan=None,
        allowed_source_ids=["intent"],
    )

    async def runner(_invocation):
        return json.dumps(
            {"resource_plan_version": RESOURCE_PLAN_VERSION, **_plan_payload()}
        )

    planner = WorkflowCreatorResourcePlanner(
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )
    assert (await planner.plan(request))["skill_name"] == "review-incidents"

    async def invalid_runner(_invocation):
        return "Here is the plan"

    invalid = WorkflowCreatorResourcePlanner(
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=invalid_runner,
    )
    with pytest.raises(SkillCreatorValidationError) as caught:
        await invalid.plan(request)
    assert caught.value.code == "skill_creator_resource_planner_invalid"


@pytest.mark.asyncio
async def test_workflow_resource_planner_discards_precommitted_resources_when_clarifying() -> None:
    request = ResourcePlanningRequest(
        session={"session_id": "skillcreator_test", "session_revision": 3},
        target_draft=None,
        current_plan=None,
        allowed_source_ids=["intent"],
    )
    clarification = {
        "id": "input_format",
        "question": "Which input fields are authoritative?",
        "reason": "The supplied examples do not define a stable input contract.",
    }
    speculative_resource = {
        "kind": "asset",
        "action": "create",
        "path": "assets/report-template.md",
        "purpose": "Render the final report.",
        "source_ids": ["intent"],
        "used_by_steps": ["deliver"],
        "depends_on": [],
        "acceptance_checks": ["Contains every required section."],
    }

    async def runner(_invocation):
        return json.dumps(
            {
                "resource_plan_version": RESOURCE_PLAN_VERSION,
                **_plan_payload(
                    clarifications=[clarification],
                    resources=[speculative_resource],
                ),
            }
        )

    planner = WorkflowCreatorResourcePlanner(
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )

    payload = await planner.plan(request)

    assert payload["clarifications"] == [clarification]
    assert payload["resources"] == []


@pytest.mark.asyncio
async def test_workflow_resource_planner_constrains_model_source_ids() -> None:
    request = ResourcePlanningRequest(
        session={"session_id": "skillcreator_test", "session_revision": 3},
        target_draft=None,
        current_plan=None,
        allowed_source_ids=["intent", "expected_output"],
    )
    resource = {
        "kind": "asset",
        "action": "create",
        "path": "assets/report-template.md",
        "purpose": "Render the final report.",
        "source_ids": ["expected_output", "invented-requirement"],
        "used_by_steps": ["deliver"],
        "depends_on": [],
        "acceptance_checks": ["Contains every required section."],
    }

    async def runner(_invocation):
        return json.dumps(
            {
                "resource_plan_version": RESOURCE_PLAN_VERSION,
                **_plan_payload(resources=[resource]),
            }
        )

    planner = WorkflowCreatorResourcePlanner(
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )

    payload = await planner.plan(request)

    assert payload["resources"][0]["source_ids"] == ["expected_output"]

    resource["source_ids"] = ["invented-requirement"]
    fallback_payload = await planner.plan(request)
    assert fallback_payload["resources"][0]["source_ids"] == ["intent"]


def test_resource_planner_extracts_one_versioned_json_from_model_narration() -> None:
    payload = {
        "resource_plan_version": RESOURCE_PLAN_VERSION,
        **_plan_payload(),
    }
    encoded = json.dumps(payload, ensure_ascii=False)

    assert parse_resource_plan_output(f"<think>plan first</think>\n{encoded}\nDone.") == payload
    assert parse_resource_plan_output(f"Here is the plan:\n```json\n{encoded}\n```\n") == payload


def test_resource_planner_rejects_ambiguous_or_syntax_repaired_output() -> None:
    first = {
        "resource_plan_version": RESOURCE_PLAN_VERSION,
        **_plan_payload(),
    }
    second = {
        **first,
        "skill_name": "different-plan",
    }

    with pytest.raises(SkillCreatorValidationError) as ambiguous:
        parse_resource_plan_output(
            f"{json.dumps(first, ensure_ascii=False)}\n"
            f"{json.dumps(second, ensure_ascii=False)}"
        )
    assert ambiguous.value.code == "skill_creator_resource_planner_invalid"

    with pytest.raises(SkillCreatorValidationError) as invalid_python:
        parse_resource_plan_output(
            "{'resource_plan_version':'skill-resource-plan-v1'}"
        )
    assert invalid_python.value.code == "skill_creator_resource_planner_invalid"


@pytest.mark.asyncio
async def test_resource_planner_direct_runtime_uses_creator_token_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ResourcePlanningRequest(
        session={
            "session_id": "skillcreator_resource_budget",
            "session_revision": 1,
            "intent": "Create incident reviews.",
            "positive_examples": ["Review this outage."],
            "near_miss_examples": ["Rewrite this paragraph."],
            "expected_output": "A Markdown report.",
            "success_criteria": ["Do not invent causes."],
            "selected_evidence": [],
        },
        target_draft=None,
        current_plan=None,
        allowed_source_ids=["intent", "expected_output"],
    )

    invocation = build_resource_planner_invocation(
        request, model_id="gateway/default-text"
    )
    observed: dict[str, object] = {}

    async def fake_stream_messages(
        model_id,
        messages,
        *,
        temperature=0.7,
        max_tokens=2048,
    ):
        observed.update(
            model_id=model_id,
            message_count=len(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield json.dumps(
            {"resource_plan_version": RESOURCE_PLAN_VERSION, **_plan_payload()}
        )

    async def unexpected_text_stream(*_args, **_kwargs):
        raise AssertionError("Trusted Creator planning must use the budgeted message stream")
        yield ""  # pragma: no cover

    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_messages",
        fake_stream_messages,
    )
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        unexpected_text_stream,
    )
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    output = await main_module.run_skill_creator_resource_planning(invocation)

    assert json.loads(output)["resource_plan_version"] == RESOURCE_PLAN_VERSION
    assert observed["message_count"] == 2
    assert observed["temperature"] == main_module.SKILL_CREATOR_RESOURCE_PLANNER_TEMPERATURE
    assert observed["max_tokens"] == main_module.SKILL_CREATOR_RESOURCE_PLANNER_MAX_TOKENS
    assert (
        main_module.workflow_agent_token_budget(
            {
                **invocation.runtime_metadata,
                "creator_phase": "draft",
            }
        )
        == main_module.SKILL_CREATOR_AGENT_MAX_TOKENS
    )


@pytest.mark.asyncio
async def test_resource_builder_direct_runtime_uses_frozen_budget_and_temperature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_store = SkillResourcePlanStore(tmp_path / "plans")
    plan = plan_store.save_generated(
        session_id="skillcreator_resource_builder_budget",
        session_revision=1,
        draft_id=None,
        draft_revision=None,
        draft_digest=None,
        payload=_plan_payload(),
        allowed_source_ids={"intent"},
    )
    plan = plan_store.confirm(
        plan.plan_id,
        expected_revision=plan.revision,
        expected_digest=plan.digest,
        session_revision=1,
        draft_revision=None,
        draft_digest=None,
    )
    build_store = SkillResourceBuildStore(tmp_path / "builds")
    build = build_store.create(plan=plan)
    build = build_store.claim_next(
        build.build_id,
        expected_revision=build.revision,
        expected_digest=build.digest,
    )
    invocation = build_resource_builder_invocation(
        ResourceBuildGenerationRequest(
            build=build,
            target_id="SKILL.md",
            segment_index=0,
        ),
        model_id="gateway/default-text",
    )
    observed: dict[str, object] = {}

    async def fake_stream_messages(
        model_id,
        messages,
        *,
        temperature=0.7,
        max_tokens=2048,
    ):
        observed.update(
            model_id=model_id,
            message_count=len(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield json.dumps(
            {
                "resource_build_version": "skill-resource-build-v1",
                "target_id": "SKILL.md",
                "segment_index": 0,
                "content": "segment",
                "complete": True,
                "script_tests": [],
            }
        )

    monkeypatch.setattr(main_module, "stream_workflow_llm_messages", fake_stream_messages)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions-builder"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    output = await main_module.run_skill_creator_resource_build(invocation)

    assert json.loads(output)["resource_build_version"] == "skill-resource-build-v1"
    assert observed["temperature"] == main_module.SKILL_CREATOR_RESOURCE_BUILDER_TEMPERATURE
    assert observed["max_tokens"] == main_module.SKILL_CREATOR_RESOURCE_BUILDER_MAX_TOKENS


def test_resource_authoring_flag_fails_closed(tmp_path: Path) -> None:
    _, planning, _, session = _services(tmp_path, _Planner([_plan_payload()]), enabled=False)
    assert planning.status()["resource_authoring_enabled"] is False
    with pytest.raises(SkillCreatorValidationError) as caught:
        planning.confirm(
            session.session_id,
            plan_id="missing",
            expected_session_revision=session.session_revision,
            expected_plan_revision=1,
            expected_plan_digest="0" * 64,
        )
    assert caught.value.code == "skill_creator_resource_authoring_disabled"


@pytest.mark.asyncio
async def test_resource_plan_api_projects_plan_and_confirms(tmp_path: Path) -> None:
    planner = _Planner([_plan_payload()])
    creator, planning, _, session = _services(tmp_path, planner)
    previous_creator = creator_api._service
    previous_planning = creator_api._resource_planning_service
    app = FastAPI()
    app.include_router(creator_api.router)
    try:
        creator_api.configure_skill_creator(creator)
        creator_api.configure_skill_creator_resource_planning(planning)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status = await client.get("/api/skills/creator/status")
            assert status.status_code == 200
            assert status.json()["resource_authoring_enabled"] is True
            assert status.json()["resource_planner_available"] is True

            created = await client.post(
                "/api/skills/creator/sessions",
                json={"mode": "blank", "intent": "Plan a reusable review workflow."},
            )
            assert created.status_code == 201, created.text
            assert created.json()["session"]["authoring_flow"] == "resource"

            generated = await client.post(
                f"/api/skills/creator/sessions/{session.session_id}/resource-plan/generate",
                json={"expected_session_revision": session.session_revision},
            )
            assert generated.status_code == 200, generated.text
            generated_payload = generated.json()
            plan = generated_payload["resource_plan"]
            assert plan["state"] == "ready"
            assert plan["stale"] is False

            confirmed = await client.post(
                f"/api/skills/creator/sessions/{session.session_id}/resource-plan/confirm",
                json={
                    "plan_id": plan["plan_id"],
                    "expected_session_revision": generated_payload["session"]["session_revision"],
                    "expected_plan_revision": plan["revision"],
                    "expected_plan_digest": plan["digest"],
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["resource_plan"]["state"] == "confirmed"

            restored = await client.get(
                f"/api/skills/creator/sessions/{session.session_id}"
            )
            assert restored.status_code == 200
            assert restored.json()["resource_plan"]["state"] == "confirmed"
    finally:
        creator_api.configure_skill_creator(previous_creator)
        creator_api.configure_skill_creator_resource_planning(previous_planning)
