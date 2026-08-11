from __future__ import annotations

import json

from fastapi.testclient import TestClient

import server.main as main_module
from server.expert_team_agency import build_meta_planner_inputs
from server.meta_agent.schemas import MetaPlannerAgentBlueprint


client = TestClient(main_module.app)


def agency_result(
    first_id: str,
    second_id: str,
    *,
    valid: bool = True,
) -> dict:
    return {
        "yaml": "name: test",
        "warnings": [],
        "repair_used": True,
        "selected_agent_ids": [first_id, second_id],
        "validation": {
            "valid": valid,
            "errors": [] if valid else ["reverse variable dependency"],
            "warnings": [],
            "workflow": {
                "name": "专家协作预览",
                "description": "先研究，再形成可验收交付。",
                "agents_dir": "modelmirror-experts",
                "llm": {"provider": "modelmirror", "model": "fake-planner"},
                "steps": [
                    {
                        "id": "research",
                        "role": first_id,
                        "task": "调研目标并整理证据。",
                        "acceptance": "至少列出三项证据。",
                        "output": "research_output",
                    },
                    {
                        "id": "delivery",
                        "role": second_id,
                        "depends_on": ["research"],
                        "task": "基于 {{research_output}} 形成最终方案。",
                        "acceptance": "结论可执行并说明风险。",
                        "output": "final_output",
                    },
                ],
            },
        },
    }


def test_agency_mapping_reuses_meta_planner_contracts() -> None:
    experts = main_module.AGENT_RECORDS[:2]
    result = agency_result(experts[0].id, experts[1].id)

    plan, blueprint, selected = build_meta_planner_inputs(
        result,
        experts,
        default_agent_model_id="model/agent",
        goal="研究新产品并形成可执行的发布方案。",
    )

    assert [task.task_id for task in plan.tasks] == ["research", "delivery"]
    assert plan.tasks[1].depends_on == ["research"]
    assert plan.tasks[1].input_contract == ["research_output"]
    assert plan.tasks[0].agent_id == experts[0].id
    assert plan.tasks[1].acceptance == "结论可执行并说明风险。"
    assert blueprint.agents[0].source_agent_id == experts[0].id
    assert blueprint.agents[0].role_prompt == experts[0].prompt.strip()[:20_000]
    assert [item["id"] for item in selected] == [experts[0].id, experts[1].id]


def test_agency_mapping_rejects_reverse_variable_and_unknown_expert() -> None:
    experts = main_module.AGENT_RECORDS[:2]
    invalid = agency_result(experts[0].id, experts[1].id, valid=False)
    try:
        build_meta_planner_inputs(
            invalid,
            experts,
            default_agent_model_id="model/agent",
            goal="研究新产品并形成可执行的发布方案。",
        )
        raise AssertionError("invalid Agency workflow was accepted")
    except ValueError as exc:
        assert "valid workflow" in str(exc)

    unknown = agency_result(experts[0].id, "missing-agent")
    try:
        build_meta_planner_inputs(
            unknown,
            experts,
            default_agent_model_id="model/agent",
            goal="研究新产品并形成可执行的发布方案。",
        )
        raise AssertionError("unknown expert was accepted")
    except ValueError as exc:
        assert "unknown expert" in str(exc)


def test_planner_capabilities_remain_visible_when_feature_is_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EXPERT_TEAM_AGENCY_PLANNER_ENABLED", "0")
    response = client.get("/api/expert-team/planner-capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["upstream_revision"] == main_module.AGENCY_UPSTREAM_REVISION
    assert payload["supported_modes"] == ["auto", "pinned"]
    assert payload["max_agents"] == 6

    preview = client.post(
        "/api/expert-team/plan-preview",
        json={
            "goal": "研究新产品并形成可执行的发布方案。",
            "planner_model_id": "model/planner",
            "default_agent_model_id": "model/agent",
        },
    )
    assert preview.status_code == 503
    assert preview.json()["code"] == "expert_team_agency_planner_disabled"


def test_plan_preview_auto_compiles_without_authoring_proposal(
    monkeypatch,
) -> None:
    experts = main_module.AGENT_RECORDS[:2]
    observed: dict = {}

    async def fake_compose(**kwargs):
        observed.update(kwargs)
        return agency_result(experts[0].id, experts[1].id)

    monkeypatch.setenv("EXPERT_TEAM_AGENCY_PLANNER_ENABLED", "1")
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.agency_worker_client, "compose", fake_compose)
    before = len(main_module.authoring_proposal_store.list())

    response = client.post(
        "/api/expert-team/plan-preview",
        json={
            "goal": "研究新产品并形成可执行的发布方案。",
            "planner_model_id": "model/planner",
            "default_agent_model_id": "model/agent",
            "mode": "auto",
            "max_agents": 5,
            "temperature": 0.2,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert observed["mode"] == "auto"
    assert observed["pinned_agent_ids"] == []
    assert len(observed["agents"]) == len(main_module.AGENT_RECORDS)
    assert payload["plan"]["tasks"][0]["agent_id"] == experts[0].id
    assert payload["candidate"]["draft"]["workflow"] == payload["workflow"]
    assert payload["validation"]["valid"] is True
    assert payload["repair_used"] is True
    assert payload["selected_agents"][1]["id"] == experts[1].id
    assert payload["baseline_matches"]
    assert len(main_module.authoring_proposal_store.list()) == before

    runs = __import__("asyncio").run(
        main_module.run_registry.list_runs(run_type="meta_planner")
    )
    expert_run = next(run for run in runs if run.source_id == "expert_team")
    assert expert_run.metadata["surface"] == "expert_team"
    assert expert_run.metadata["backend"] == "agency_orchestrator"


def test_pinned_lineup_and_invalid_requests_are_rejected(monkeypatch) -> None:
    experts = main_module.AGENT_RECORDS[:2]

    async def fake_compose(**kwargs):
        assert kwargs["mode"] == "pinned"
        assert kwargs["pinned_agent_ids"] == [experts[0].id, experts[1].id]
        return agency_result(experts[0].id, experts[1].id)

    monkeypatch.setenv("EXPERT_TEAM_AGENCY_PLANNER_ENABLED", "1")
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.agency_worker_client, "compose", fake_compose)
    base = {
        "goal": "研究新产品并形成可执行的发布方案。",
        "planner_model_id": "model/planner",
        "default_agent_model_id": "model/agent",
        "mode": "pinned",
        "pinned_agent_ids": [experts[0].id, experts[1].id],
    }
    response = client.post("/api/expert-team/plan-preview", json=base)
    assert response.status_code == 200, response.text

    duplicate = client.post(
        "/api/expert-team/plan-preview",
        json={**base, "pinned_agent_ids": [experts[0].id, experts[0].id]},
    )
    assert duplicate.status_code == 422
    unknown = client.post(
        "/api/expert-team/plan-preview",
        json={**base, "pinned_agent_ids": ["missing-agent"]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_agent"
    too_many = client.post(
        "/api/expert-team/plan-preview",
        json={**base, "max_agents": 7},
    )
    assert too_many.status_code == 422


def test_preview_rejects_cycle_from_worker(monkeypatch) -> None:
    experts = main_module.AGENT_RECORDS[:2]
    cyclic = agency_result(experts[0].id, experts[1].id)
    steps = cyclic["validation"]["workflow"]["steps"]
    steps[0]["depends_on"] = ["delivery"]

    async def fake_compose(**_kwargs):
        return cyclic

    monkeypatch.setenv("EXPERT_TEAM_AGENCY_PLANNER_ENABLED", "1")
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.agency_worker_client, "compose", fake_compose)
    response = client.post(
        "/api/expert-team/plan-preview",
        json={
            "goal": "研究新产品并形成可执行的发布方案。",
            "planner_model_id": "model/planner",
            "default_agent_model_id": "model/agent",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "agency_plan_invalid"
    assert "acyclic" in response.json()["error"]


def test_expert_schema_extensions_are_optional_for_existing_plans() -> None:
    serialized = json.dumps(main_module.MetaPlannerGenerateResponse.model_json_schema())
    assert "agent_id" in serialized
    assert "source_agent_id" in json.dumps(
        MetaPlannerAgentBlueprint.model_json_schema()
    )


def test_existing_route_agent_and_team_chat_streams_remain_compatible(
    monkeypatch,
) -> None:
    experts = main_module.AGENT_RECORDS[:2]

    async def fake_stream(*_args, **_kwargs):
        yield "兼容输出"

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "stream_text_with_model_fallback", fake_stream)

    route = client.post(
        "/api/route-agent",
        json={
            "message": "请制定一个可执行的产品测试计划。",
            "model_id": "model/agent",
        },
    )
    assert route.status_code == 200
    assert '"event": "route_result"' in route.text
    assert '"event": "answer_end"' in route.text

    team = client.post(
        "/api/team/chat",
        json={
            "message": "请协作制定一个可执行的产品测试计划。",
            "model_id": "model/agent",
            "mode": "serial",
            "members": [
                {"agent_id": experts[0].id, "task": "分析风险"},
                {"agent_id": experts[1].id, "task": "整理验收"},
            ],
        },
    )
    assert team.status_code == 200
    assert '"event": "team_start"' in team.text
    assert '"event": "team_end"' in team.text
