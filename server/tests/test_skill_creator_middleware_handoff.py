from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import server.main as main_module
from server.skills.creator_handoff import (
    SKILL_CREATOR_HANDOFF_ROLE_INSTRUCTION,
    SkillCreatorHandoffError,
    SkillCreatorHandoffRequest,
    SkillCreatorHandoffService,
)
from server.skills.creator_resource_plan import SkillResourcePlanStore
from server.skills.creator_resource_service import (
    SkillCreatorResourcePlanningService,
)
from server.skills.creator_runtime import TrustedCreatorSourceProvider
from server.skills.creator_service import SkillCreatorService
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSessionStore,
)
from server.xpert_runtime import WorkflowExecutionStore


def _events(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _workflow() -> dict:
    return {
        "id": "creator-handoff-contract",
        "title": "Creator handoff contract",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": "requirements-analyst",
                    "modelId": "test/model",
                    "rolePrompt": "分析用户需求。",
                    "taskInput": "请分析：{{user_input}}",
                    "toolMode": "none",
                    "outputVariable": "analysis",
                },
            },
            {
                "id": "creator",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "skill_creator",
                    "runtimeMiddlewareKind": "runtime_middleware.skill_creator",
                    "runtimeMiddlewareConfig": {
                        "authoring_mode": "creator_handoff"
                    },
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "analysis"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "agent"},
            {
                "id": "bind-creator",
                "source": "creator",
                "target": "agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
            {"id": "e2", "source": "agent", "target": "output"},
        ],
    }


def _plugin_handoff_workflow() -> dict:
    workflow = _workflow()
    workflow["id"] = "creator-plugin-handoff-contract"
    workflow["nodes"] = [
        node for node in workflow["nodes"] if node["id"] != "creator"
    ]
    workflow["edges"] = [
        edge for edge in workflow["edges"] if edge["id"] != "bind-creator"
    ]
    next(
        node for node in workflow["nodes"] if node["id"] == "agent"
    )["data"]["toolMode"] = "mcp_tools"
    workflow["nodes"].append(
        {
            "id": "plugin",
            "type": "plugin_resource",
            "data": {
                "kind": "plugin_resource",
                "pluginId": "plugin-creator-attack",
                "versionPolicy": "latest",
            },
        }
    )
    workflow["edges"].append(
        {
            "id": "bind-plugin",
            "source": "plugin",
            "target": "agent",
            "sourceHandle": "plugin-binding",
            "targetHandle": "plugin",
        }
    )
    return workflow


def _conditional_handoff_workflow() -> dict:
    workflow = _workflow()
    workflow["id"] = "creator-conditional-handoff-contract"
    workflow["nodes"].insert(
        1,
        {
            "id": "route",
            "type": "multi_route",
            "data": {
                "kind": "multi_route",
                "inputVariable": "user_input",
                "routes": [
                    {
                        "id": "route_1",
                        "label": "create skill",
                        "operator": "contains",
                        "valueType": "text",
                        "value": "create skill",
                    },
                    {
                        "id": "route_2",
                        "label": "archive only",
                        "operator": "contains",
                        "valueType": "text",
                        "value": "archive only",
                    }
                ],
            },
        },
    )
    workflow["nodes"].insert(
        -1,
        {
            "id": "fallback",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "variableName": "analysis",
                "template": "No Skill handoff was requested.",
            },
        },
    )
    workflow["edges"] = [
        {"id": "e-input-route", "source": "input", "target": "route"},
        {
            "id": "e-route-agent",
            "source": "route",
            "sourceHandle": "route_1",
            "target": "agent",
        },
        {
            "id": "e-route-fallback",
            "source": "route",
            "sourceHandle": "default",
            "target": "fallback",
        },
        {
            "id": "e-route-archive",
            "source": "route",
            "sourceHandle": "route_2",
            "target": "fallback",
        },
        {
            "id": "bind-creator",
            "source": "creator",
            "target": "agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        },
        {"id": "e-agent-output", "source": "agent", "target": "output"},
        {
            "id": "e-fallback-output",
            "source": "fallback",
            "target": "output",
        },
    ]
    return workflow


def _failing_handoff_workflow() -> dict:
    workflow = _workflow()
    workflow["id"] = "creator-failing-handoff-contract"
    workflow["nodes"] = [
        node for node in workflow["nodes"] if node["id"] != "output"
    ]
    workflow["nodes"].append(
        {
            "id": "stop",
            "type": "terminate_error",
            "data": {
                "kind": "terminate_error",
                "errorCode": "HANDOFF_WORKFLOW_FAILED",
                "message": "The workflow failed after requirements analysis.",
            },
        }
    )
    workflow["edges"] = [
        edge for edge in workflow["edges"] if edge["id"] != "e2"
    ]
    workflow["edges"].append(
        {"id": "e-agent-stop", "source": "agent", "target": "stop"}
    )
    return workflow


def test_handoff_is_enabled_by_default_with_explicit_env_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SKILL_CREATOR_MIDDLEWARE_V2_ENABLED", raising=False)
    enabled = SkillCreatorHandoffService(SimpleNamespace())
    assert enabled.enabled is True

    monkeypatch.setenv("SKILL_CREATOR_MIDDLEWARE_V2_ENABLED", "false")
    disabled = SkillCreatorHandoffService(SimpleNamespace())
    assert disabled.enabled is False


def test_handoff_store_is_idempotent_and_hydrates_pristine_capture(
    tmp_path: Path,
) -> None:
    store = SkillCreatorSessionStore(tmp_path / "creator-sessions")
    captured = store.create(
        mode="run",
        source_kind="workflow_classic",
        source_task_id="task-1",
        source_run_id="run-1",
    )

    values = {
        "intent": "把事故记录整理成复盘。",
        "positive_examples": ["把事故记录整理成复盘。"],
        "near_miss_examples": ["只改写一句话。"],
        "expected_output": "结构化复盘。",
        "success_criteria": ["不编造事实。"],
        "source_task_id": "task-1",
        "source_run_id": "run-1",
    }
    hydrated = store.create_or_get_workflow_handoff(**values)
    repeated = store.create_or_get_workflow_handoff(**values)

    assert hydrated.session_id == captured.session_id == repeated.session_id
    assert hydrated.authoring_flow == "resource"
    assert hydrated.intent == values["intent"]
    assert hydrated.session_revision == 2
    assert len(store.list()) == 1

    reloaded = SkillCreatorSessionStore(tmp_path / "creator-sessions")
    after_restart = reloaded.create_or_get_workflow_handoff(**values)
    manual_capture = reloaded.create_or_get_run_capture(
        source_kind="workflow_classic",
        source_task_id="task-1",
        source_run_id="run-1",
    )
    assert after_restart.session_id == hydrated.session_id
    assert manual_capture.session_id == hydrated.session_id
    assert len(reloaded.list()) == 1


def test_handoff_store_rejects_an_edited_source_conflict(tmp_path: Path) -> None:
    store = SkillCreatorSessionStore(tmp_path / "creator-conflict")
    captured = store.create(
        mode="run",
        source_kind="workflow_classic",
        source_task_id="task-2",
        source_run_id="run-2",
    )
    store.update_definition(
        captured.session_id,
        expected_session_revision=captured.session_revision,
        changes={"intent": "用户已经编辑过的需求"},
    )

    with pytest.raises(SkillCreatorConflictError):
        store.create_or_get_workflow_handoff(
            intent="自动交接的另一份需求",
            positive_examples=["自动交接的另一份需求"],
            near_miss_examples=["无关任务"],
            expected_output="结果",
            success_criteria=["不编造"],
            source_task_id="task-2",
            source_run_id="run-2",
        )
    assert len(store.list()) == 1


def test_handoff_fails_closed_for_untrusted_state_and_secret_input(
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "guard-executions")
    execution_store.create(
        task_id="task-guard",
        run_id="run-guard",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "guard", "nodes": [], "edges": []},
        inputs={"user_input": "prepare a reusable report"},
    )
    session_store = SkillCreatorSessionStore(tmp_path / "guard-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )
    handoff = SkillCreatorHandoffService(creator, enabled=True)
    request = SkillCreatorHandoffRequest(
        node_id="creator",
        intent="prepare a reusable report",
    )

    with pytest.raises(SkillCreatorHandoffError) as pending:
        handoff.create_or_get(
            task_id="task-guard",
            run_id="run-guard",
            request=request,
        )
    assert pending.value.code == "skill_creator_handoff_failed"
    assert session_store.list() == []

    execution_store.complete("task-guard", result="completed")
    with pytest.raises(SkillCreatorHandoffError) as secret:
        handoff.create_or_get(
            task_id="task-guard",
            run_id="run-guard",
            request=SkillCreatorHandoffRequest(
                node_id="creator",
                intent="prepare report with API_KEY=super-secret-value",
            ),
        )
    assert secret.value.code == "skill_creator_handoff_failed"
    assert session_store.list() == []


@pytest.mark.asyncio
async def test_workflow_run_rejects_duplicate_handoff_bindings() -> None:
    workflow = _workflow()
    workflow["edges"].append(
        {
            "id": "bind-creator-again",
            "source": "creator",
            "target": "agent",
            "sourceHandle": "middleware-binding",
            "targetHandle": "middleware",
        }
    )
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "test"}},
        )

    assert response.status_code == 422
    assert response.json() == {"error": "skill_creator_multiple_handoffs"}


@pytest.mark.asyncio
async def test_unselected_handoff_agent_does_not_create_a_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "conditional-executions")
    session_store = SkillCreatorSessionStore(tmp_path / "conditional-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )

    async def unexpected_stream(*_args, **_kwargs):
        raise AssertionError("The unselected workflow Agent must not run.")
        yield "unreachable"

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(
        main_module,
        "skill_creator_handoff_service",
        SkillCreatorHandoffService(creator, enabled=True),
    )
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", unexpected_stream)
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _conditional_handoff_workflow(),
                "inputs": {"user_input": "return the fallback result"},
            },
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert any(item.get("event") == "workflow_end" for item in events)
    assert not any(
        item.get("event") == "skill_creator_handoff" for item in events
    )
    assert session_store.list() == []
    persisted = execution_store.list_items(limit=1)[0]
    assert persisted.status == "completed"


@pytest.mark.asyncio
async def test_failed_workflow_does_not_create_a_handoff_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "failed-executions")
    session_store = SkillCreatorSessionStore(tmp_path / "failed-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )

    async def fake_stream(*_args, **_kwargs):
        yield "Requirements analysis completed before the downstream failure."

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(
        main_module,
        "skill_creator_handoff_service",
        SkillCreatorHandoffService(creator, enabled=True),
    )
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _failing_handoff_workflow(),
                "inputs": {"user_input": "create skill from this process"},
            },
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    error = next(item for item in events if item.get("event") == "error")
    assert error["code"] == "HANDOFF_WORKFLOW_FAILED"
    assert not any(item.get("event") == "workflow_end" for item in events)
    assert not any(
        item.get("event") == "skill_creator_handoff" for item in events
    )
    assert session_store.list() == []
    persisted = execution_store.list_items(limit=1)[0]
    assert persisted.status == "failed"


@pytest.mark.asyncio
async def test_cancelled_workflow_does_not_create_a_handoff_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class CancelOnCompleteStore(WorkflowExecutionStore):
        def complete(self, task_id: str, *, result: str):
            self.cancel(task_id, error="cancelled_by_user")
            return self.require(task_id)

    execution_store = CancelOnCompleteStore(tmp_path / "cancelled-executions")
    session_store = SkillCreatorSessionStore(tmp_path / "cancelled-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )

    async def fake_stream(*_args, **_kwargs):
        yield "Requirements analysis completed before cancellation won the race."

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(
        main_module,
        "skill_creator_handoff_service",
        SkillCreatorHandoffService(creator, enabled=True),
    )
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(),
                "inputs": {"user_input": "create skill from this process"},
            },
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert any(item.get("event") == "workflow_cancelled" for item in events)
    assert not any(item.get("event") == "workflow_end" for item in events)
    assert not any(
        item.get("event") == "skill_creator_handoff" for item in events
    )
    assert session_store.list() == []
    persisted = execution_store.list_items(limit=1)[0]
    assert persisted.status == "cancelled"


@pytest.mark.asyncio
async def test_completed_v2_middleware_creates_one_session_without_proposal_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    session_store = SkillCreatorSessionStore(tmp_path / "creator")
    source_provider = TrustedCreatorSourceProvider(
        execution_store,
        main_module.xpert_context_store,
    )
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=source_provider,
        enabled=True,
    )
    handoff = SkillCreatorHandoffService(creator, enabled=True)
    captured: dict[str, str] = {}
    model_calls = 0

    async def fake_stream(
        _model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        nonlocal model_calls
        model_calls += 1
        captured["prompt"] = prompt
        captured["system_prompt"] = str(system_prompt or "")
        yield "用途、输入、输出、边界和缺失信息已整理。"

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "skill_creator_handoff_service", handoff)
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    main_module.request_windows.clear()
    before = {
        item.proposal_id for item in main_module.authoring_proposal_store.list(limit=500)
    }

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(),
                "inputs": {"user_input": "把客服处理经验沉淀成 Skill"},
            },
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    handoff_event = next(
        item for item in events if item.get("event") == "skill_creator_handoff"
    )
    workflow_end = next(item for item in events if item.get("event") == "workflow_end")
    assert events.index(handoff_event) < events.index(workflow_end)
    assert handoff_event["status"] == "ready"
    assert handoff_event["node_id"] == "creator"
    assert set(handoff_event) == {
        "event",
        "status",
        "task_id",
        "run_id",
        "node_id",
        "session_id",
    }
    session = session_store.require(handoff_event["session_id"])
    assert session.source_kind == "workflow_classic"
    assert session.source_task_id == handoff_event["task_id"]
    assert session.source_run_id == handoff_event["run_id"]
    assert session.authoring_flow == "resource"
    assert session.intent == "请分析：把客服处理经验沉淀成 Skill"
    assert session.evidence_confirmed is False
    assert session.state == "selecting_evidence"
    assert model_calls == 1
    assert captured["prompt"] == session.intent
    assert SKILL_CREATOR_HANDOFF_ROLE_INSTRUCTION in captured["system_prompt"]
    assert "same primary language as the user's request" in captured[
        "system_prompt"
    ].lower()
    assert "plain text without markdown" in captured["system_prompt"].lower()
    after = {
        item.proposal_id for item in main_module.authoring_proposal_store.list(limit=500)
    }
    assert after == before
    persisted = execution_store.require(handoff_event["task_id"])
    assert persisted.status == "completed"
    persisted_handoff = next(
        item for item in persisted.events
        if item.get("event") == "skill_creator_handoff"
    )
    assert {
        key: value for key, value in persisted_handoff.items() if key != "sequence"
    } == handoff_event

    preview = creator.preview_source(session.session_id)
    session = creator.select_evidence(
        session.session_id,
        expected_session_revision=session.session_revision,
        preview_fingerprint=preview.fingerprint,
        candidate_ids=[],
    )

    class Planner:
        def available(self) -> bool:
            return True

        async def plan(self, _request):
            return {
                "skill_name": "customer-support-playbook",
                "skill_description": (
                    "Turn a repeated customer-support process into a bounded workflow."
                ),
                "workflow_steps": [
                    {
                        "id": "analyze",
                        "instruction": "Confirm the request and missing information.",
                    },
                    {
                        "id": "structure",
                        "instruction": "Structure the reusable workflow and boundaries.",
                    },
                    {
                        "id": "deliver",
                        "instruction": "Produce the documented support workflow.",
                    },
                    {
                        "id": "verify",
                        "instruction": "Verify the output contract and safe fallback.",
                    },
                ],
                "output_contract": ["Return a reusable support workflow."],
                "failure_modes": ["Do not invent missing policy details."],
                "resources": [],
                "clarifications": [],
            }

    planning = SkillCreatorResourcePlanningService(
        creator,
        SkillResourcePlanStore(tmp_path / "handoff-plans"),
        planner=Planner(),
        enabled=True,
    )
    plan = await planning.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=None,
        expected_plan_digest=None,
    )
    assert plan.state == "ready"
    confirmed = planning.confirm(
        session.session_id,
        plan_id=plan.plan_id,
        expected_session_revision=session.session_revision,
        expected_plan_revision=plan.revision,
        expected_plan_digest=plan.digest,
    )
    assert confirmed.state == "confirmed"


@pytest.mark.asyncio
async def test_handoff_event_store_failure_preserves_completed_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "event-failure-executions")
    session_store = SkillCreatorSessionStore(tmp_path / "event-failure-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )

    async def fake_stream(*_args, **_kwargs):
        yield "analysis completed"

    append_event = execution_store.append_event

    def fail_handoff_event(task_id: str, event: dict):
        if event.get("event") == "skill_creator_handoff":
            raise OSError("simulated handoff event store failure")
        return append_event(task_id, event)

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(
        main_module,
        "skill_creator_handoff_service",
        SkillCreatorHandoffService(creator, enabled=True),
    )
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    monkeypatch.setattr(execution_store, "append_event", fail_handoff_event)
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(
        app=main_module.app,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow(), "inputs": {"user_input": "test"}},
        )

    assert response.status_code == 200
    events = _events(response.text)
    failed = next(
        item for item in events if item.get("event") == "skill_creator_handoff"
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == "skill_creator_handoff_failed"
    assert "session_id" not in failed
    assert any(item.get("event") == "workflow_end" for item in events)
    executions = execution_store.list_items(limit=10)
    assert len(executions) == 1 and executions[0].status == "completed"
    assert len(session_store.list()) == 1


@pytest.mark.asyncio
async def test_plugin_preset_cannot_inject_creator_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "plugin-executions")
    session_store = SkillCreatorSessionStore(tmp_path / "plugin-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )
    plugin = SimpleNamespace(
        id="plugin-creator-attack",
        status="published",
        published_version=1,
    )
    version = SimpleNamespace(
        version=1,
        skills=[],
        installed_skill_ids=[],
        toolsets=[],
        middleware_presets=[
            SimpleNamespace(
                middleware_id="skill_creator",
                priority=100,
                config={"authoring_mode": "creator_handoff"},
            )
        ],
    )
    plugin_store = SimpleNamespace(
        get_plugin=lambda _plugin_id: plugin,
        get_version=lambda _plugin_id, _version: version,
    )

    provider_calls = 0

    async def fake_collect(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return "analysis completed"

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(
        main_module,
        "skill_creator_handoff_service",
        SkillCreatorHandoffService(creator, enabled=True),
    )
    monkeypatch.setattr(main_module, "get_plugin_store", lambda: plugin_store)
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _plugin_handoff_workflow(),
                "inputs": {"user_input": "test"},
            },
        )

    assert response.status_code == 200
    events = _events(response.text)
    assert any(
        item.get("event") == "error"
        and item.get("message") == "skill_creator_handoff_unavailable"
        for item in events
    )
    assert not any(item.get("event") == "skill_creator_handoff" for item in events)
    assert not any(item.get("event") == "workflow_end" for item in events)
    error = next(item for item in events if item.get("event") == "error")
    persisted = execution_store.require(error["task_id"])
    assert persisted.status == "failed"
    assert persisted.error == "skill_creator_handoff_unavailable"
    assert provider_calls == 0
    assert session_store.list() == []


@pytest.mark.asyncio
async def test_disabled_handoff_emits_failure_without_changing_workflow_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "disabled-executions")
    session_store = SkillCreatorSessionStore(tmp_path / "disabled-creator")
    creator = SkillCreatorService(
        session_store,
        main_module.get_skill_draft_store(),
        main_module.authoring_service,
        source_provider=TrustedCreatorSourceProvider(
            execution_store, main_module.xpert_context_store
        ),
        enabled=True,
    )

    async def fake_stream(*_args, **_kwargs):
        yield "analysis completed"

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(
        main_module,
        "skill_creator_handoff_service",
        SkillCreatorHandoffService(creator, enabled=False),
    )
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow(), "inputs": {"user_input": "test"}},
        )

    events = _events(response.text)
    failed = next(
        item for item in events if item.get("event") == "skill_creator_handoff"
    )
    assert failed == {
        "event": "skill_creator_handoff",
        "status": "failed",
        "task_id": failed["task_id"],
        "run_id": failed["run_id"],
        "node_id": "creator",
        "error_code": "skill_creator_handoff_unavailable",
    }
    assert any(item.get("event") == "workflow_end" for item in events)
    persisted = execution_store.require(failed["task_id"])
    assert persisted.status == "completed"
    persisted_failure = next(
        item for item in persisted.events
        if item.get("event") == "skill_creator_handoff"
    )
    assert persisted_failure["error_code"] == "skill_creator_handoff_unavailable"
    assert "session_id" not in persisted_failure
    assert session_store.list() == []
