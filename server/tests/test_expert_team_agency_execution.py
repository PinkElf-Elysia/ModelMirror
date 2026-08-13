from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.expert_team_agency_runtime import (
    AgencyExecutionCapacityError,
    AgencyExecutionCoordinator,
    AgencyExecutionValidationError,
    prepare_agency_execution,
)
from server.meta_agent.schemas import MetaPlannerTaskPlan
from server.orchestration_worker import (
    AgencyAgentDefinition,
    AgencyExecutionClient,
    AgencyModelResponse,
    AgencySkillDefinition,
    AgencyWorkerError,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xpert_runtime import RunRegistry, WorkflowExecutionStore


def experts():
    return [
        SimpleNamespace(
            id="agent-alpha",
            name="研究专家",
            department="研究部",
            expertise="研究证据",
            scenarios="研究",
            prompt="你是研究专家。",
            emoji="🔎",
        ),
        SimpleNamespace(
            id="agent-beta",
            name="交付专家",
            department="产品部",
            expertise="形成交付",
            scenarios="交付",
            prompt="你是交付专家。",
            emoji="📦",
        ),
    ]


def valid_plan_and_workflow():
    plan = MetaPlannerTaskPlan.model_validate(
        {
            "summary": "先研究，再形成可执行交付。",
            "tasks": [
                {
                    "task_id": "research",
                    "title": "研究",
                    "objective": "研究用户目标",
                    "depends_on": [],
                    "input_contract": ["user_input"],
                    "output_contract": "研究结果",
                    "agent_id": "agent-alpha",
                    "acceptance": "列出证据",
                },
                {
                    "task_id": "delivery",
                    "title": "交付",
                    "objective": "形成执行方案",
                    "depends_on": ["research"],
                    "input_contract": ["research_output"],
                    "output_contract": "最终方案",
                    "agent_id": "agent-beta",
                    "acceptance": "方案必须可执行",
                },
            ],
        }
    )
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "agency-test",
            "title": "专家协作",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "user_input"},
                },
                {
                    "id": "agent_research",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "title": "研究",
                        "description": "研究用户目标",
                        "agentName": "研究专家",
                        "modelId": "fake-model",
                        "rolePrompt": "你是研究专家。",
                        "taskInput": "研究用户目标\n\n用户任务：\n{{user_input}}",
                        "toolMode": "none",
                        "toolNames": "",
                        "outputVariable": "research_output",
                        "sourceAgentId": "agent-alpha",
                        "acceptanceCriteria": "列出证据",
                        "exceptionHandling": "fail",
                    },
                },
                {
                    "id": "agent_delivery",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "title": "交付",
                        "description": "形成执行方案",
                        "agentName": "交付专家",
                        "modelId": "fake-model",
                        "rolePrompt": "你是交付专家。",
                        "taskInput": "形成执行方案\n\n依赖结果：\nresearch: {{research_output}}",
                        "toolMode": "none",
                        "toolNames": "",
                        "outputVariable": "final_output",
                        "sourceAgentId": "agent-beta",
                        "acceptanceCriteria": "方案必须可执行",
                        "exceptionHandling": "fail",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "final_output"},
                },
            ],
            "edges": [
                {"id": "input-research", "source": "input", "target": "agent_research"},
                {"id": "research-delivery", "source": "agent_research", "target": "agent_delivery"},
                {"id": "delivery-output", "source": "agent_delivery", "target": "output"},
            ],
        }
    )
    return plan, workflow


def test_prepare_execution_compiles_only_server_owned_experts_and_plain_steps():
    plan, workflow = valid_plan_and_workflow()
    prepared = prepare_agency_execution(
        plan=plan, workflow=workflow, expert_records=experts()
    )

    assert prepared.sink_task_id == "delivery"
    assert prepared.selected_agent_ids == ["agent-alpha", "agent-beta"]
    assert [step["id"] for step in prepared.workflow["steps"]] == [
        "research",
        "delivery",
    ]
    assert prepared.workflow["steps"][1]["depends_on"] == ["research"]
    assert "llm" not in prepared.workflow["steps"][1]
    assert prepared.agents[0].system_prompt == "你是研究专家。"
    assert prepared.skills == []


def test_prepare_execution_does_not_duplicate_existing_input_references():
    plan, workflow = valid_plan_and_workflow()
    plan.tasks[0].objective = "研究用户目标 {{user_input}}"
    workflow.nodes[1].data["description"] = plan.tasks[0].objective
    workflow.nodes[1].data["taskInput"] = plan.tasks[0].objective
    plan.tasks[1].objective = "形成执行方案 {{research_output}}"
    workflow.nodes[2].data["description"] = plan.tasks[1].objective
    workflow.nodes[2].data["taskInput"] = plan.tasks[1].objective

    prepared = prepare_agency_execution(
        plan=plan, workflow=workflow, expert_records=experts()
    )

    assert prepared.workflow["steps"][0]["task"].count("{{user_input}}") == 1
    assert prepared.workflow["steps"][1]["task"].count("{{research_output}}") == 1


def test_prepare_execution_accepts_transitive_ancestor_variables():
    plan, workflow = valid_plan_and_workflow()
    process = plan.tasks[1].model_copy(
        deep=True,
        update={
            "task_id": "process",
            "title": "过程设计",
            "objective": "根据研究形成过程 {{research_output}}",
            "depends_on": ["research"],
            "input_contract": ["research_output"],
            "output_contract": "过程结果",
            "acceptance": "",
        },
    )
    plan.tasks[1].objective = (
        "结合传递上游研究与直接过程结果 "
        "{{research_output}} {{process_output}}"
    )
    plan.tasks[1].depends_on = ["process"]
    plan.tasks[1].input_contract = ["process_output"]

    process_node = workflow.nodes[2].model_copy(deep=True)
    process_node.id = "agent_process"
    process_node.data.update(
        {
            "title": process.title,
            "description": process.objective,
            "taskInput": process.objective,
            "outputVariable": "process_output",
            "acceptanceCriteria": "",
        }
    )
    workflow.nodes.insert(2, process_node)
    delivery_node = workflow.nodes[3]
    delivery_node.data["description"] = plan.tasks[1].objective
    delivery_node.data["taskInput"] = plan.tasks[1].objective
    workflow.edges = [
        workflow.edges[0],
        workflow.edges[1].model_copy(
            update={
                "id": "research-process",
                "source": "agent_research",
                "target": "agent_process",
            }
        ),
        workflow.edges[1].model_copy(
            update={
                "id": "process-delivery",
                "source": "agent_process",
                "target": "agent_delivery",
            }
        ),
        workflow.edges[2],
    ]
    plan.tasks.insert(1, process)

    prepared = prepare_agency_execution(
        plan=plan, workflow=workflow, expert_records=experts()
    )

    assert prepared.workflow["steps"][2]["depends_on"] == ["process"]
    assert "{{research_output}}" in prepared.workflow["steps"][2]["task"]


def test_prepare_execution_rejects_undefined_variable_before_runtime_compile():
    plan, workflow = valid_plan_and_workflow()
    workflow.nodes[2].data["taskInput"] += " {{unrelated_output}}"

    with pytest.raises(AgencyExecutionValidationError, match="unrelated_output"):
        prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )


def test_prepare_execution_binds_only_server_resolved_method_skills():
    plan, workflow = valid_plan_and_workflow()
    plan.tasks[0].method_skill_ids = ["data-analysis"]
    workflow.nodes[1].data["methodSkillIds"] = ["data-analysis"]
    skill = AgencySkillDefinition(
        skill_id="data-analysis",
        name="Data Analysis",
        description="证据优先的数据分析方法。",
        body="先核对数据语义，再形成结论。",
        digest="a" * 64,
    )
    prepared = prepare_agency_execution(
        plan=plan,
        workflow=workflow,
        expert_records=experts(),
        method_skills={"data-analysis": skill},
    )
    assert prepared.workflow["steps"][0]["skills"] == ["data-analysis"]
    assert prepared.skills == [skill]

    with pytest.raises(AgencyExecutionValidationError) as missing:
        prepare_agency_execution(
            plan=plan,
            workflow=workflow,
            expert_records=experts(),
            method_skills={},
        )
    assert missing.value.code == "agency_method_skill_changed"

    workflow.nodes[1].data["methodSkillIds"] = []
    with pytest.raises(AgencyExecutionValidationError, match="工作方法与计划不一致"):
        prepare_agency_execution(
            plan=plan,
            workflow=workflow,
            expert_records=experts(),
            method_skills={"data-analysis": skill},
        )


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda plan, workflow: workflow.nodes[1].data.update(
                {"rolePrompt": "客户端篡改提示词"}
            ),
            "角色提示词",
        ),
        (
            lambda plan, workflow: workflow.nodes[1].data.update(
                {"provider": "openrouter", "apiKey": "secret"}
            ),
            "Provider",
        ),
        (
            lambda plan, workflow: setattr(plan.tasks[1], "depends_on", []),
            "最终汇点",
        ),
    ],
)
def test_prepare_execution_rejects_tampering_and_multiple_sinks(mutation, message):
    plan, workflow = valid_plan_and_workflow()
    mutation(plan, workflow)
    with pytest.raises(AgencyExecutionValidationError, match=message):
        prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )


def test_prepare_execution_rejects_duplicate_over_limit_unknown_and_tools():
    plan, workflow = valid_plan_and_workflow()
    plan.tasks.append(plan.tasks[0].model_copy(deep=True))
    with pytest.raises(AgencyExecutionValidationError, match="任务 ID"):
        prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )

    plan, workflow = valid_plan_and_workflow()
    for index in range(5):
        plan.tasks.append(
            plan.tasks[0].model_copy(
                deep=True,
                update={"task_id": f"extra-{index}"},
            )
        )
    with pytest.raises(AgencyExecutionValidationError, match="1-6"):
        prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )

    plan, workflow = valid_plan_and_workflow()
    plan.tasks[0].agent_id = "missing-agent"
    workflow.nodes[1].data["sourceAgentId"] = "missing-agent"
    with pytest.raises(AgencyExecutionValidationError) as unknown:
        prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
    assert unknown.value.code == "unknown_agent"

    plan, workflow = valid_plan_and_workflow()
    workflow.nodes[1].data["toolMode"] = "mcp_tools"
    workflow.nodes[1].data["toolNames"] = "filesystem"
    with pytest.raises(AgencyExecutionValidationError, match="工具"):
        prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )


class CompletingClient:
    worker_entry = Path(__file__)

    async def execute(self, *, on_event, **_kwargs):
        await on_event(
            {
                "event": "agency.run.started",
                "status": "running",
            }
        )
        await on_event(
            {
                "event": "agency.step.completed",
                "task_id": "delivery",
                "agent_id": "agent-beta",
                "status": "completed",
                "output": "最终结果",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        )
        await on_event(
            {
                "event": "agency.run.completed",
                "status": "completed",
                "final_output": "最终结果",
                "quality_status": "passed",
                "warnings": [],
                "model_calls": 3,
                "usage": {"input_tokens": 20, "output_tokens": 10},
            }
        )
        return SimpleNamespace(
            payload={
                "final_output": "最终结果",
                "quality_status": "passed",
                "model_calls": 3,
                "usage": {"input_tokens": 20, "output_tokens": 10},
            }
        )


class CompletionCommitClient:
    worker_entry = Path(__file__)

    def __init__(self):
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, *, on_event, **_kwargs):
        await on_event(
            {
                "event": "agency.run.completed",
                "status": "completed",
                "final_output": "已完成结果",
                "quality_status": "passed",
                "warnings": [],
                "model_calls": 1,
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }
        )
        self.committed.set()
        await self.release.wait()
        return SimpleNamespace(
            payload={
                "final_output": "已完成结果",
                "quality_status": "passed",
                "model_calls": 1,
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }
        )


class HangingClient:
    worker_entry = Path(__file__)

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, *, on_event, **_kwargs):
        await on_event({"event": "agency.run.started", "status": "running"})
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class ResumeClient:
    worker_entry = Path(__file__)

    def __init__(self):
        self.resume = None

    async def execute(self, *, on_event, resume=None, **_kwargs):
        self.resume = resume
        await on_event(
            {
                "event": "agency.step.completed",
                "task_id": "research",
                "status": "completed",
                "output": "已有研究",
                "reused": True,
                "model_calls": 2,
                "cumulative_usage": {
                    "input_tokens": 20,
                    "output_tokens": 10,
                },
            }
        )
        await on_event(
            {
                "event": "agency.run.completed",
                "status": "completed",
                "final_output": "续跑结果",
                "model_calls": 4,
                "usage": {"input_tokens": 40, "output_tokens": 20},
            }
        )
        return SimpleNamespace(
            payload={
                "final_output": "续跑结果",
                "model_calls": 4,
                "usage": {"input_tokens": 40, "output_tokens": 20},
            }
        )


class HangingResumeClient(HangingClient):
    def __init__(self):
        super().__init__()
        self.resume = None

    async def execute(self, *, on_event, resume=None, **kwargs):
        self.resume = resume
        return await super().execute(on_event=on_event, **kwargs)


async def _noop_model(_request):
    return "unused"


def coordinator(tmp_path, client):
    return AgencyExecutionCoordinator(
        store=WorkflowExecutionStore(tmp_path),
        run_registry=RunRegistry(),
        model_runner=_noop_model,
        client_factory=lambda: client,
    )


def test_coordinator_persists_events_and_completes(tmp_path):
    async def scenario():
        plan, workflow = valid_plan_and_workflow()
        prepared = prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
        runtime = coordinator(tmp_path, CompletingClient())
        started = await runtime.start(
            goal="制定一个可执行的专家协作方案。",
            model_id="fake-model",
            prepared=prepared,
            capability_snapshot_version="snapshot-v1",
            capability_snapshot_hash="hash",
            upstream_revision="revision",
        )
        for _ in range(50):
            current = runtime.get(started["task_id"])
            if current["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert current["final_output"] == "最终结果"
        assert current["model_calls"] == 3
        assert current["usage"] == {"input_tokens": 20, "output_tokens": 10}
        assert current["sequence"] == 3
        assert current["steps"][0]["task_id"] == "delivery"
        assert current["goal"] == "制定一个可执行的专家协作方案。"
        assert current["team_name"] == "专家协作"
        assert current["selected_agent_ids"] == ["agent-alpha", "agent-beta"]

    asyncio.run(scenario())


def test_cancel_is_idempotent_and_stops_background_client(tmp_path):
    async def scenario():
        plan, workflow = valid_plan_and_workflow()
        prepared = prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
        client = HangingClient()
        runtime = coordinator(tmp_path, client)
        started = await runtime.start(
            goal="制定一个可执行的专家协作方案。",
            model_id="fake-model",
            prepared=prepared,
            capability_snapshot_version="snapshot-v1",
            capability_snapshot_hash="hash",
            upstream_revision="revision",
        )
        await asyncio.wait_for(client.started.wait(), timeout=1)
        first = await runtime.cancel(started["task_id"])
        second = await runtime.cancel(started["task_id"])
        assert first["status"] == second["status"] == "cancelled"
        assert client.cancelled is True
        assert sum(
            event["event"] == "agency.run.cancelled"
            for event in second["events"]
        ) == 1

    asyncio.run(scenario())


def test_completed_worker_event_wins_over_late_cancel(tmp_path):
    async def scenario():
        plan, workflow = valid_plan_and_workflow()
        prepared = prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
        client = CompletionCommitClient()
        runtime = coordinator(tmp_path, client)
        started = await runtime.start(
            goal="制定一个可执行的专家协作方案。",
            model_id="fake-model",
            prepared=prepared,
            capability_snapshot_version="snapshot-v1",
            capability_snapshot_hash="hash",
            upstream_revision="revision",
        )
        await asyncio.wait_for(client.committed.wait(), timeout=1)
        cancelled = await runtime.cancel(started["task_id"])
        assert cancelled["status"] == "completed"
        assert cancelled["final_output"] == "已完成结果"
        assert not any(
            event["event"] == "agency.run.cancelled"
            for event in cancelled["events"]
        )
        client.release.set()

    asyncio.run(scenario())


def test_failed_run_retries_only_incomplete_steps_and_keeps_live_usage(tmp_path):
    async def scenario():
        plan, workflow = valid_plan_and_workflow()
        prepared = prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
        client = ResumeClient()
        runtime = coordinator(tmp_path, client)
        source = runtime.store.create(
            task_id="failed-source",
            run_id="source-run",
            run_type="expert_team",
            workflow=prepared.workflow,
            inputs={"goal": "制定一个可执行的专家协作方案。"},
            source_kind="expert_team_agency",
            runtime_metadata={
                "model_id": "fake-model",
                "upstream_revision": "revision",
                "capability_snapshot_version": "snapshot-v1",
                "capability_snapshot_hash": "hash",
                "sink_task_id": prepared.sink_task_id,
                "selected_agent_ids": prepared.selected_agent_ids,
                "method_skill_digests": {},
            },
        )
        runtime.store.append_event(
            source.task_id,
            {
                "event": "agency.step.completed",
                "task_id": "research",
                "status": "completed",
                "output": "已有研究",
                "model_calls": 1,
                "cumulative_usage": {
                    "input_tokens": 12,
                    "output_tokens": 8,
                },
            },
        )
        runtime.store.append_event(
            source.task_id,
            {
                "event": "agency.run.failed",
                "status": "failed",
                "error": "agency_execution_step_failed",
                "model_calls": 2,
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )
        runtime.store.fail(source.task_id, error="agency_execution_step_failed")

        serialized = runtime.get(source.task_id)
        assert serialized["retryable"] is True
        assert serialized["model_calls"] == 2
        assert serialized["usage"] == {"input_tokens": 20, "output_tokens": 10}
        retried = await runtime.retry(
            source_task_id=source.task_id,
            prepared=prepared,
        )
        assert retried["resumed_from_task_id"] == source.task_id
        assert retried["model_calls"] == 2
        assert retried["usage"] == {"input_tokens": 20, "output_tokens": 10}
        for _ in range(50):
            current = runtime.get(retried["task_id"])
            if current["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert client.resume["completed_steps"] == [
            {
                "task_id": "research",
                "output": "已有研究",
                "output_variable": "research_output",
                "acceptance": "列出证据",
            }
        ]
        assert client.resume["prior_model_calls"] == 2
        assert current["steps"][0]["reused"] is True
        assert current["model_calls"] == 4

    asyncio.run(scenario())


def test_retry_is_idempotent_while_the_resume_run_is_active(tmp_path):
    async def scenario():
        plan, workflow = valid_plan_and_workflow()
        prepared = prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
        client = HangingResumeClient()
        runtime = coordinator(tmp_path, client)
        source = runtime.store.create(
            task_id="idempotent-source",
            run_id="source-run",
            run_type="expert_team",
            workflow=prepared.workflow,
            inputs={"goal": "制定一个可执行的专家协作方案。"},
            source_kind="expert_team_agency",
            runtime_metadata={
                "model_id": "fake-model",
                "upstream_revision": "revision",
                "capability_snapshot_version": "snapshot-v1",
                "capability_snapshot_hash": "hash",
                "sink_task_id": prepared.sink_task_id,
                "selected_agent_ids": prepared.selected_agent_ids,
                "method_skill_digests": {},
            },
        )
        runtime.store.append_event(
            source.task_id,
            {
                "event": "agency.step.completed",
                "task_id": "research",
                "status": "completed",
                "output": "已有研究",
                "model_calls": 1,
                "cumulative_usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )
        runtime.store.append_event(
            source.task_id,
            {
                "event": "agency.run.failed",
                "status": "failed",
                "error": "agency_execution_step_failed",
                "model_calls": 1,
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )
        runtime.store.fail(source.task_id, error="agency_execution_step_failed")
        first = await runtime.retry(
            source_task_id=source.task_id, prepared=prepared
        )
        await asyncio.wait_for(client.started.wait(), timeout=1)
        second = await runtime.retry(
            source_task_id=source.task_id, prepared=prepared
        )
        assert first["task_id"] == second["task_id"]
        assert len(runtime._tasks) == 1
        await runtime.cancel(first["task_id"])

    asyncio.run(scenario())


def test_capacity_and_interrupted_recovery(tmp_path):
    async def scenario():
        plan, workflow = valid_plan_and_workflow()
        prepared = prepare_agency_execution(
            plan=plan, workflow=workflow, expert_records=experts()
        )
        clients = [HangingClient(), HangingClient(), HangingClient()]
        runtime = AgencyExecutionCoordinator(
            store=WorkflowExecutionStore(tmp_path),
            run_registry=RunRegistry(),
            model_runner=_noop_model,
            client_factory=lambda: clients.pop(0),
        )
        kwargs = {
            "goal": "制定一个可执行的专家协作方案。",
            "model_id": "fake-model",
            "prepared": prepared,
            "capability_snapshot_version": "snapshot-v1",
            "capability_snapshot_hash": "hash",
            "upstream_revision": "revision",
        }
        first = await runtime.start(**kwargs)
        second = await runtime.start(**kwargs)
        with pytest.raises(AgencyExecutionCapacityError):
            await runtime.start(**kwargs)
        await runtime.cancel(first["task_id"])
        await runtime.cancel(second["task_id"])

        interrupted = runtime.store.create(
            task_id="interrupted",
            run_id="missing-after-restart",
            run_type="expert_team",
            workflow={"steps": []},
            inputs={},
            source_kind="expert_team_agency",
        )
        assert interrupted.status == "running"
        assert runtime.recover_interrupted() == 1
        recovered = runtime.get("interrupted")
        assert recovered["status"] == "failed"
        assert recovered["error_code"] == "agency_execution_interrupted"

    asyncio.run(scenario())


def test_execution_store_redacts_unapproved_event_fields(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="safe-event",
        run_id="run",
        run_type="expert_team",
        workflow={"steps": []},
        inputs={},
        source_kind="expert_team_agency",
    )
    store.append_event(
        "safe-event",
        {
            "event": "agency.step.completed",
            "task_id": "task",
            "output": "x" * (70 * 1024),
            "api_key": "must-not-persist",
            "usage": {"input_tokens": 3, "secret": 4},
            "cumulative_usage": {"input_tokens": 5, "output_tokens": 2, "secret": 9},
            "reused": True,
        },
    )
    event = store.require("safe-event").events[0]
    assert len(event["output"]) == 64 * 1024
    assert "api_key" not in event
    assert event["usage"] == {"input_tokens": 3}
    assert event["cumulative_usage"] == {
        "input_tokens": 5,
        "output_tokens": 2,
    }
    assert event["reused"] is True

    non_agency = store.create(
        task_id="ordinary-xpert",
        run_id="run-2",
        run_type="xpert",
        workflow={"steps": []},
        inputs={},
        source_kind="xpert_chat",
    )
    store.append_event(
        non_agency.task_id,
        {
            "event": "ordinary.event",
            "output": "must-not-expand-other-run-contracts",
            "error": "must-not-persist",
        },
    )
    assert "output" not in store.require(non_agency.task_id).events[0]
    assert "error" not in store.require(non_agency.task_id).events[0]


def execution_agents() -> list[AgencyAgentDefinition]:
    return [
        AgencyAgentDefinition(
            id="agent-alpha",
            path="agent-alpha",
            name="Alpha",
            department="研究",
            description="研究",
            system_prompt="You are Alpha.",
        ),
        AgencyAgentDefinition(
            id="agent-beta",
            path="agent-beta",
            name="Beta",
            department="产品",
            description="交付",
            system_prompt="You are Beta.",
        ),
    ]


def execution_workflow() -> dict:
    return {
        "name": "fan-out fan-in",
        "steps": [
            {
                "id": "research",
                "role": "agent-alpha",
                "task": "Research {{user_input}}",
                "output": "research_output",
                "depends_on": [],
                "type": "normal",
            },
            {
                "id": "risk",
                "role": "agent-beta",
                "task": "Assess {{user_input}}",
                "output": "risk_output",
                "depends_on": [],
                "type": "normal",
            },
            {
                "id": "synthesis",
                "role": "agent-beta",
                "task": "Use {{research_output}} and {{risk_output}}",
                "acceptance": "Must be actionable",
                "output": "final_output",
                "depends_on": ["research", "risk"],
                "type": "normal",
            },
        ],
    }


def write_execution_worker(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "execution-worker.mjs"
    path.write_text(source, encoding="utf-8")
    return path


def test_execution_client_runs_real_worker_with_out_of_order_fake_gateway():
    async def scenario():
        finished: list[str] = []

        async def fake_gateway(request):
            system = request.messages[0].content
            if "验收员" in system or "reviewer" in system.lower():
                return AgencyModelResponse(
                    content='{"pass":true,"failed":[]}',
                    usage={"input_tokens": 2, "output_tokens": 3},
                )
            if "Alpha" in system:
                await asyncio.sleep(0.05)
                finished.append("alpha")
            elif "Beta" in system:
                await asyncio.sleep(0.005)
                finished.append("beta")
            return AgencyModelResponse(
                content=f"result-{request.request_id}",
                usage={"input_tokens": 2, "output_tokens": 3},
            )

        client = AgencyExecutionClient(model_runner=fake_gateway, timeout_seconds=10)
        result = await client.execute(
            goal="Build a reliable launch recommendation.",
            model_id="fake-model",
            workflow=execution_workflow(),
            agents=execution_agents(),
        )
        assert result.payload["final_output"]
        assert result.payload["quality_status"] == "passed"
        assert result.model_calls == 4
        assert finished[:2] == ["beta", "alpha"]

    asyncio.run(scenario())


def test_execution_client_cancellation_stops_inflight_model_call():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hanging_gateway(_request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        client = AgencyExecutionClient(model_runner=hanging_gateway, timeout_seconds=10)
        task = asyncio.create_task(
            client.execute(
                goal="Build a reliable launch recommendation.",
                model_id="fake-model",
                workflow=execution_workflow(),
                agents=execution_agents(),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=2)

    asyncio.run(scenario())


def test_execution_client_enforces_total_timeout_and_call_budget(tmp_path):
    async def scenario():
        timeout_client = AgencyExecutionClient(
            worker_entry=write_execution_worker(
                tmp_path,
                "for await (const _chunk of process.stdin) { break; } setInterval(() => {}, 1000);",
            ),
            timeout_seconds=0.05,
        )
        with pytest.raises(AgencyWorkerError) as timeout_error:
            await timeout_client.execute(
                goal="Build a reliable launch recommendation.",
                model_id="fake-model",
                workflow=execution_workflow(),
                agents=execution_agents(),
            )
        assert timeout_error.value.code == "agency_execution_timeout"

        source = """
let input = '';
for await (const chunk of process.stdin) { input += chunk; if (input.includes('\\n')) break; }
const request = JSON.parse(input.trim());
for (let index = 1; index <= 11; index += 1) {
  process.stdout.write(JSON.stringify({
    protocol: 'mm-agency-bridge/v2', type: 'model_request', id: request.id,
    request_id: `${request.id}:model:${index}`, model_id: 'fake-model',
    messages: [{role: 'system', content: 'system'}, {role: 'user', content: 'user'}],
    temperature: 0.3, max_tokens: 4096
  }) + '\\n');
}
setInterval(() => {}, 1000);
"""

        async def blocked_gateway(_request):
            await asyncio.Event().wait()

        budget_client = AgencyExecutionClient(
            worker_entry=write_execution_worker(tmp_path, source),
            model_runner=blocked_gateway,
            timeout_seconds=2,
        )
        with pytest.raises(AgencyWorkerError) as budget_error:
            await budget_client.execute(
                goal="Build a reliable launch recommendation.",
                model_id="fake-model",
                workflow=execution_workflow(),
                agents=execution_agents(),
            )
        assert budget_error.value.code == "agency_execution_budget_exceeded"

    asyncio.run(scenario())


def test_execution_worker_environment_is_secret_free(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    worker = write_execution_worker(
        tmp_path,
        """
let input = '';
for await (const chunk of process.stdin) { input += chunk; if (input.includes('\\n')) break; }
const request = JSON.parse(input.trim());
process.stdout.write(JSON.stringify({
  protocol: 'mm-agency-bridge/v2', type: 'response', id: request.id, ok: true,
  result: {
    gateway: process.env.LLM_GATEWAY_KEY ?? null,
    openrouter: process.env.OPENROUTER_API_KEY ?? null,
    worker: process.env.MM_AGENCY_WORKER
  }
}) + '\\n');
""",
    )

    async def scenario():
        client = AgencyExecutionClient(worker_entry=worker, timeout_seconds=2)
        result = await client.execute(
            goal="Build a reliable launch recommendation.",
            model_id="fake-model",
            workflow=execution_workflow(),
            agents=execution_agents(),
        )
        assert result.payload == {
            "gateway": None,
            "openrouter": None,
            "worker": "1",
        }

    asyncio.run(scenario())


def test_execution_api_reports_disabled_and_stale_contracts(monkeypatch):
    from fastapi.testclient import TestClient
    import server.main as main_module

    client = TestClient(main_module.app)
    plan, workflow = valid_plan_and_workflow()
    payload = {
        "goal": "制定一个可执行的专家协作方案。",
        "plan": plan.model_dump(mode="json"),
        "workflow": workflow.model_dump(mode="json"),
        "model_id": "fake-model",
        "capability_snapshot_version": "snapshot-v1",
        "capability_snapshot_hash": "hash",
        "upstream_revision": main_module.AGENCY_UPSTREAM_REVISION,
    }

    monkeypatch.setenv("EXPERT_TEAM_AGENCY_EXECUTION_ENABLED", "0")
    capabilities = client.get("/api/expert-team/planner-capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["execution"]["enabled"] is False
    disabled = client.post("/api/expert-team/dag-runs", json=payload)
    assert disabled.status_code == 503
    assert disabled.json()["code"] == "agency_execution_disabled"

    monkeypatch.setenv("EXPERT_TEAM_AGENCY_EXECUTION_ENABLED", "1")
    monkeypatch.setattr(
        main_module.agency_execution_coordinator,
        "worker_available",
        lambda: True,
    )
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    stale_upstream = client.post(
        "/api/expert-team/dag-runs",
        json={**payload, "upstream_revision": "deadbeef"},
    )
    assert stale_upstream.status_code == 409
    assert stale_upstream.json()["code"] == "upstream_revision_changed"

    stale_snapshot = client.post("/api/expert-team/dag-runs", json=payload)
    assert stale_snapshot.status_code == 409
    assert stale_snapshot.json()["code"] == "capability_snapshot_changed"

    monkeypatch.setattr(
        main_module,
        "build_meta_planner_capability_snapshot",
        lambda _agents: SimpleNamespace(
            version="snapshot-v1",
            snapshot_hash="hash",
        ),
    )

    async def non_text_model(_model_id):
        return False

    monkeypatch.setattr(
        main_module,
        "expert_team_execution_model_is_text",
        non_text_model,
    )
    non_text = client.post("/api/expert-team/dag-runs", json=payload)
    assert non_text.status_code == 422
    assert non_text.json()["code"] == "agency_execution_plan_invalid"


def test_execution_retry_api_rebuilds_server_owned_contract(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.main as main_module

    store = WorkflowExecutionStore(tmp_path)
    agent_id = main_module.AGENT_RECORDS[0].id
    source = store.create(
        task_id="retry-source",
        run_id="retry-source-run",
        run_type="expert_team",
        workflow={
            "name": "冻结计划",
            "steps": [
                {
                    "id": "final",
                    "role": agent_id,
                    "task": "形成结论",
                    "output": "final_output",
                    "depends_on": [],
                    "acceptance": "结论可执行",
                    "skills": [],
                }
            ],
        },
        inputs={"goal": "形成一个可执行且可审计的专家结论。"},
        source_kind="expert_team_agency",
        runtime_metadata={
            "model_id": "fake-model",
            "upstream_revision": main_module.AGENCY_UPSTREAM_REVISION,
            "capability_snapshot_version": "snapshot-v1",
            "capability_snapshot_hash": "snapshot-hash",
            "sink_task_id": "final",
            "selected_agent_ids": [agent_id],
            "method_skill_digests": {},
        },
    )
    store.fail(source.task_id, error="agency_execution_step_failed")
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setenv("EXPERT_TEAM_AGENCY_EXECUTION_ENABLED", "1")
    monkeypatch.setattr(
        main_module.agency_execution_coordinator, "worker_available", lambda: True
    )
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "build_meta_planner_capability_snapshot",
        lambda _agents: SimpleNamespace(
            version="snapshot-v1", snapshot_hash="snapshot-hash"
        ),
    )

    async def text_model(_model_id):
        return True

    captured = {}

    async def fake_retry(*, source_task_id, prepared):
        captured["source_task_id"] = source_task_id
        captured["prepared"] = prepared
        return {
            "task_id": "retry-new",
            "run_id": "retry-new-run",
            "status": "running",
            "sequence": 0,
            "events": [],
            "steps": [],
            "warnings": [],
            "model_calls": 1,
            "usage": {},
        }

    monkeypatch.setattr(
        main_module, "expert_team_execution_model_is_text", text_model
    )
    monkeypatch.setattr(main_module.agency_execution_coordinator, "retry", fake_retry)

    response = TestClient(main_module.app).post(
        f"/api/expert-team/dag-runs/{source.task_id}/retry"
    )
    assert response.status_code == 202
    assert response.json()["task_id"] == "retry-new"
    assert response.json()["retry_url"].endswith("/retry-new/retry")
    assert captured["source_task_id"] == source.task_id
    assert captured["prepared"].selected_agent_ids == [agent_id]
    assert captured["prepared"].workflow == source.workflow


def test_execution_sse_replays_only_events_after_sequence(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.main as main_module

    store = WorkflowExecutionStore(tmp_path)
    item = store.create(
        task_id="replay-task",
        run_id="replay-run",
        run_type="expert_team",
        workflow={"steps": []},
        inputs={},
        source_kind="expert_team_agency",
    )
    store.append_event(
        item.task_id,
        {"event": "agency.run.started", "status": "running"},
    )
    store.append_event(
        item.task_id,
        {
            "event": "agency.step.completed",
            "task_id": "one",
            "status": "completed",
            "output": "result",
        },
    )
    store.append_event(
        item.task_id,
        {
            "event": "agency.run.completed",
            "status": "completed",
            "final_output": "result",
        },
    )
    store.complete(item.task_id, result="result")
    monkeypatch.setattr(main_module, "workflow_execution_store", store)

    response = TestClient(main_module.app).get(
        f"/api/expert-team/dag-runs/{item.task_id}/events?after_sequence=1"
    )
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["sequence"] for event in events] == [2, 3]
    assert [event["event"] for event in events] == [
        "agency.step.completed",
        "agency.run.completed",
    ]


def test_execution_history_lists_only_agency_runs_without_full_events(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.main as main_module

    store = WorkflowExecutionStore(tmp_path)
    agency = store.create(
        task_id="agency-history",
        run_id="agency-run",
        run_type="expert_team",
        workflow={"name": "研究专家团", "steps": []},
        inputs={"goal": "研究一个真实用户问题"},
        source_kind="expert_team_agency",
        runtime_metadata={"model_id": "fake-model", "selected_agent_ids": ["agent-alpha"]},
    )
    store.append_event(
        agency.task_id,
        {
            "event": "agency.run.completed",
            "status": "completed",
            "final_output": "可交付结论",
            "model_calls": 2,
            "usage": {"input_tokens": 120, "output_tokens": 80},
        },
    )
    store.complete(agency.task_id, result="可交付结论")
    store.create(
        task_id="classic-history",
        run_id="classic-run",
        run_type="workflow",
        workflow={"steps": []},
        inputs={},
        source_kind="workflow_classic",
    )
    monkeypatch.setattr(main_module, "workflow_execution_store", store)

    response = TestClient(main_module.app).get("/api/expert-team/dag-runs?limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["task_id"] for item in payload["items"]] == ["agency-history"]
    assert payload["items"][0]["final_output_preview"] == "可交付结论"
    assert payload["items"][0]["model_calls"] == 2
    assert "events" not in payload["items"][0]
    assert "steps" not in payload["items"][0]

    invalid = TestClient(main_module.app).get("/api/expert-team/dag-runs?status=unknown")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "agency_execution_status_invalid"
