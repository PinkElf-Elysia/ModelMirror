from __future__ import annotations

import json
import re

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import app
from server.meta_agent import (
    build_workflow_from_plan,
    extract_json_object_text,
    infer_task_edges,
    parse_meta_agent_plan,
)


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def sample_plan_json() -> str:
    return json.dumps(
        {
            "thought": "Split the work into analysis, risk review, and launch planning.",
            "sub_tasks": [
                {
                    "name": "requirements_analysis",
                    "description": "Analyze the goal and clarify success criteria.",
                    "reason": "A stable brief improves every downstream task.",
                    "inputs": [
                        {
                            "name": "goal",
                            "type": "string",
                            "description": "User goal.",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {
                            "name": "requirements_brief",
                            "type": "string",
                            "description": "Concise requirements brief.",
                            "required": True,
                        }
                    ],
                    "agent": {
                        "name": "requirements_agent",
                        "description": "Clarifies the goal.",
                        "prompt": "Read <input>{goal}</input>.\n\n## requirements_brief\nBrief.",
                        "tool_names": [],
                    },
                },
                {
                    "name": "risk_review",
                    "description": "Review risks in the requirements brief.",
                    "reason": "Risk analysis should happen before delivery.",
                    "inputs": [
                        {
                            "name": "requirements_brief",
                            "type": "string",
                            "description": "Requirements brief.",
                            "required": True,
                        },
                        {
                            "name": "goal",
                            "type": "string",
                            "description": "Original goal.",
                            "required": True,
                        },
                    ],
                    "outputs": [
                        {
                            "name": "risk_report",
                            "type": "string",
                            "description": "Risk report.",
                            "required": True,
                        }
                    ],
                    "agent": {
                        "name": "risk_agent",
                        "description": "Finds risks.",
                        "prompt": "Use <input>{requirements_brief}</input> and {goal}.\n\n## risk_report\nRisks.",
                        "tool_names": None,
                    },
                },
                {
                    "name": "launch_outline",
                    "description": "Create a launch outline.",
                    "reason": "The user needs an actionable plan.",
                    "inputs": [
                        {
                            "name": "requirements_brief",
                            "type": "string",
                            "description": "Requirements brief.",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {
                            "name": "launch_plan",
                            "type": "string",
                            "description": "Launch plan.",
                            "required": True,
                        }
                    ],
                    "agent": {
                        "name": "launch_agent",
                        "description": "Plans launch.",
                        "prompt": "Use {requirements_brief}.\n\n## launch_plan\nPlan.",
                        "tool_names": [],
                    },
                },
            ],
        }
    )


def test_extract_json_object_text_handles_fenced_and_prefixed_output() -> None:
    fenced = 'notes\n```json\n{"sub_tasks": []}\n```\nextra'
    prefixed = 'Here is the plan: {"thought": "ok", "sub_tasks": []} trailing'

    assert extract_json_object_text(fenced) == '{"sub_tasks": []}'
    assert extract_json_object_text(prefixed) == '{"thought": "ok", "sub_tasks": []}'


def test_infer_task_edges_by_matching_outputs() -> None:
    plan = parse_meta_agent_plan(sample_plan_json(), max_tasks=5)

    assert infer_task_edges(plan.sub_tasks) == [
        ("requirements_analysis", "risk_review"),
        ("requirements_analysis", "launch_outline"),
    ]


def test_build_workflow_inserts_aggregator_and_converts_placeholders() -> None:
    plan = parse_meta_agent_plan(sample_plan_json(), max_tasks=5)
    workflow, warnings = build_workflow_from_plan(
        goal="Create a launch plan for a meta-agent workbench.",
        plan=plan,
        model_id="deepseek/deepseek-chat",
    )

    assert warnings == []
    nodes_by_id = {node["id"]: node for node in workflow["nodes"]}
    assert nodes_by_id["aggregate_meta_agent_outputs"]["data"]["kind"] == "variable_aggregator"
    assert nodes_by_id["aggregate_meta_agent_outputs"]["data"]["variableNames"] == (
        "risk_report, launch_plan"
    )
    assert nodes_by_id["output_final"]["data"]["outputVariable"] == "meta_agent_result"
    assert "{{goal}}" in nodes_by_id["requirements_analysis"]["data"]["instruction"]
    risk_instruction = nodes_by_id["risk_review"]["data"]["instruction"]
    assert "{{requirements_brief}}" in risk_instruction
    assert re.search(r"(?<!\{)\{requirements_brief\}(?!\})", risk_instruction) is None


@pytest.mark.asyncio
async def test_generate_meta_agent_endpoint_with_mocked_model(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect_chat_completion_text(*args, **kwargs) -> str:
        return sample_plan_json()

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )

    response = await client.post(
        "/api/meta-agent/generate-workflow",
        json={
            "goal": "为元智能体工作台生成一个发布计划，并输出风险和行动清单。",
            "model_id": "deepseek/deepseek-chat",
            "temperature": 0.2,
            "max_tasks": 5,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["goal"].startswith("为元智能体工作台")
    assert len(data["plan"]["sub_tasks"]) == 3
    assert data["workflow"]["nodes"][0]["data"]["kind"] == "input"
    assert data["validation"]["valid"] is True
    assert data["warnings"] == []
