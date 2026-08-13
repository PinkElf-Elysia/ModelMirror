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

    plan, _, _ = build_meta_planner_inputs(
        result,
        experts,
        default_agent_model_id="model/agent",
        goal="研究新产品并形成可执行的发布方案。",
        method_skill_id="data-analysis",
    )
    assert [task.method_skill_ids for task in plan.tasks] == [
        ["data-analysis"],
        ["data-analysis"],
    ]


def test_expert_team_assets_reuse_worker_storage_and_current_expert_ids(
    monkeypatch,
) -> None:
    expert = main_module.AGENT_RECORDS[0]
    calls: list[tuple[str, dict | None]] = []

    async def fake_assets(action, payload=None):
        calls.append((action, payload))
        if action == "list":
            return {"teams": [], "templates": [], "garden": []}
        return {"ok": True}

    monkeypatch.setattr(main_module.agency_worker_client, "assets", fake_assets)
    monkeypatch.setattr(
        main_module,
        "expert_team_method_skills",
        lambda: {
            "data-analysis": {
                "skill_id": "data-analysis",
                "name": "Data Analysis",
                "description": "证据优先的数据分析方法。",
                "digest": "a" * 64,
            }
        },
    )

    listed = client.get("/api/expert-team/assets")
    assert listed.status_code == 200
    assert listed.json()["method_skills"][0]["skill_id"] == "data-analysis"

    saved_team = client.post(
        "/api/expert-team/teams",
        json={
            "name": "发布阵容",
            "description": "用于发布任务",
            "agent_ids": [expert.id],
        },
    )
    assert saved_team.status_code == 201
    assert calls[-1][0] == "save_team"
    assert calls[-1][1]["team"]["roles"][0]["role"] == expert.id

    saved_template = client.post(
        "/api/expert-team/templates",
        json={
            "name": "发布模板",
            "content": "请为当前产品形成一份可验收的发布计划。",
            "note": "first",
        },
    )
    assert saved_template.status_code == 201
    assert calls[-1][0] == "save_template"

    unknown = client.post(
        "/api/expert-team/teams",
        json={"name": "坏阵容", "agent_ids": ["missing-agent"]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_agent"


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
        assert "reverse variable dependency" in str(exc)

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

    top_level_inputs = agency_result(experts[0].id, experts[1].id)
    top_level_inputs["validation"]["workflow"]["inputs"] = [
        {"name": "product_description", "required": True}
    ]
    try:
        build_meta_planner_inputs(
            top_level_inputs,
            experts,
            default_agent_model_id="model/agent",
            goal="研究新产品并形成可执行的发布方案。",
        )
        raise AssertionError("unsupported Agency inputs were accepted")
    except ValueError as exc:
        assert "unsupported top-level inputs" in str(exc)
        assert "product_description" in str(exc)

    missing_acceptance = agency_result(experts[0].id, experts[1].id)
    missing_acceptance["validation"]["workflow"]["steps"][-1].pop(
        "acceptance", None
    )
    try:
        build_meta_planner_inputs(
            missing_acceptance,
            experts,
            default_agent_model_id="model/agent",
            goal="研究新产品并形成可执行的发布方案。",
        )
        raise AssertionError("final task without acceptance was accepted")
    except ValueError as exc:
        assert "missing acceptance criteria" in str(exc)
        assert "delivery" in str(exc)


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


def test_plan_preview_applies_only_allowlisted_method_skill(
    monkeypatch,
) -> None:
    experts = main_module.AGENT_RECORDS[:2]

    async def fake_compose(**_kwargs):
        return agency_result(experts[0].id, experts[1].id)

    catalog = {
        "data-analysis": {
            "skill_id": "data-analysis",
            "name": "Data Analysis",
            "description": "证据优先的数据分析方法。",
            "digest": "b" * 64,
        }
    }
    monkeypatch.setenv("EXPERT_TEAM_AGENCY_PLANNER_ENABLED", "1")
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.agency_worker_client, "compose", fake_compose)
    monkeypatch.setattr(main_module, "expert_team_method_skills", lambda: catalog)

    response = client.post(
        "/api/expert-team/plan-preview",
        json={
            "goal": "研究新产品并形成可执行的发布方案。",
            "planner_model_id": "model/planner",
            "default_agent_model_id": "model/agent",
            "method_skill_id": "data-analysis",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["method_skill"] == catalog["data-analysis"]
    assert all(
        task["method_skill_ids"] == ["data-analysis"]
        for task in payload["plan"]["tasks"]
    )
    assert all(
        node["data"].get("methodSkillIds") == ["data-analysis"]
        for node in payload["workflow"]["nodes"]
        if node.get("type") == "workflow_agent"
    )

    rejected = client.post(
        "/api/expert-team/plan-preview",
        json={
            "goal": "研究新产品并形成可执行的发布方案。",
            "planner_model_id": "model/planner",
            "default_agent_model_id": "model/agent",
            "method_skill_id": "unapproved-skill",
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "agency_method_skill_unavailable"


def test_plan_preview_requires_explicit_knowledge_consent_and_bounds_context(
    monkeypatch,
) -> None:
    experts = main_module.AGENT_RECORDS[:2]
    observed: dict = {}
    real_rag_service = main_module.get_rag_service()

    class FakeRagService:
        def list_knowledge_bases(self):
            return [
                {
                    "id": "kb-private",
                    "name": "发布资料",
                    "document_count": 1,
                }
            ]

        async def search_knowledge(self, kb_id, question, *, top_k):
            assert kb_id == "kb-private"
            assert question == "研究新产品并形成可执行的发布方案。"
            assert top_k == 4
            return {
                "version_id": "version-private",
                "sources": [
                    {
                        "chunk_id": "chunk-1",
                        "source_document_id": "document-1",
                        "document_name": "launch.md",
                        "matched_text": "PRIVATE-CONTEXT " + ("x" * 5_000),
                        "score": 0.91,
                        "page_number": 3,
                    }
                ],
            }

        def __getattr__(self, name):
            return getattr(real_rag_service, name)

    async def fake_compose(**kwargs):
        observed.update(kwargs)
        return agency_result(experts[0].id, experts[1].id)

    monkeypatch.setenv("EXPERT_TEAM_AGENCY_PLANNER_ENABLED", "1")
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(main_module.agency_worker_client, "compose", fake_compose)

    base = {
        "goal": "研究新产品并形成可执行的发布方案。",
        "planner_model_id": "model/planner",
        "default_agent_model_id": "model/agent",
        "knowledge_base_id": "kb-private",
    }
    missing_consent = client.post("/api/expert-team/plan-preview", json=base)
    assert missing_consent.status_code == 422
    assert observed == {}

    response = client.post(
        "/api/expert-team/plan-preview",
        json={**base, "allow_knowledge_context": True},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "PRIVATE-CONTEXT" in observed["goal"]
    assert len(observed["goal"]) < 24_000
    assert payload["knowledge_context"] == {
        "knowledge_base": {"id": "kb-private", "name": "发布资料"},
        "version_id": "version-private",
        "sources": [
            {
                "chunk_id": "chunk-1",
                "document_id": "document-1",
                "document_name": "launch.md",
                "score": 0.91,
                "page_number": 3,
                "slide": None,
                "sheet": None,
                "row_range": None,
            }
        ],
    }
    assert "text" not in payload["knowledge_context"]["sources"][0]


def test_plan_preview_allows_more_tasks_than_distinct_experts(monkeypatch) -> None:
    experts = main_module.AGENT_RECORDS[:2]
    result = agency_result(experts[0].id, experts[1].id)
    steps = result["validation"]["workflow"]["steps"]
    expanded_steps = [steps[0]]
    for index in range(1, 5):
        expanded_steps.append(
            {
                "id": f"analysis_{index}",
                "role": experts[index % 2].id,
                "depends_on": [expanded_steps[-1]["id"]],
                "task": f"完成第 {index} 轮分析。",
                "acceptance": "结论可复核。",
                "output": f"analysis_{index}_output",
            }
        )
    steps[1]["depends_on"] = [expanded_steps[-1]["id"]]
    expanded_steps.append(steps[1])
    result["validation"]["workflow"]["steps"] = expanded_steps

    async def fake_compose(**_kwargs):
        return result

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
            "mode": "auto",
            "max_agents": 2,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["plan"]["tasks"]) == 6
    assert len(payload["selected_agents"]) == 2


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
    pinned_over_limit = client.post(
        "/api/expert-team/plan-preview",
        json={**base, "max_agents": 1},
    )
    assert pinned_over_limit.status_code == 422


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
