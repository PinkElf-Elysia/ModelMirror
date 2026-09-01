from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import app
from server.meta_agent.graph_ir_v3 import v2_to_graph_intent
from server.meta_agent.schemas import (
    MetaPlannerIRFinalOutput,
    MetaPlannerIRInputBinding,
    MetaPlannerIRNode,
    MetaPlannerIROutputBinding,
    MetaPlannerTask,
    MetaPlannerTaskPlan,
    MetaPlannerTypedBlueprintV2,
    MetaPlannerWorkflowAgentConfig,
    ProviderRouteCallReceipt,
    ProviderRouteReceiptSummary,
)


MODEL_ID = "deepseek/deepseek-chat"


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


class FakeManagedRun:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[ProviderRouteCallReceipt] = []
        self.status = "running"
        self.run_id = "workrun_meta_test"
        self.parent_run_reference = ""

    async def complete_json(self, **kwargs) -> str:
        sequence = int(kwargs["call_sequence"])
        model_id = str(kwargs["model_id"])
        self.calls.append(
            ProviderRouteCallReceipt(
                call_sequence=sequence,
                model_id=model_id,
                actual_model=model_id,
                status="passed",
                total_tokens=sequence,
            )
        )
        return self.outputs.pop(0)

    def finish(self, status, *, reason_code=None) -> None:
        self.status = status
        self.reason_codes = [reason_code] if reason_code else []

    def receipt_summary(self) -> ProviderRouteReceiptSummary:
        return ProviderRouteReceiptSummary(
            contract_version="modelmirror-provider-workload-routing-v1",
            run_reference=self.run_id,
            status=self.status,
            call_count=len(self.calls),
            reason_codes=getattr(self, "reason_codes", []),
            calls=self.calls,
        )


class FakeManagedGateway:
    def __init__(self, run: FakeManagedRun, mode: str = "managed_required") -> None:
        self.run = run
        self.mode = mode

    def routing_mode(self) -> str:
        return self.mode

    def start_run(self, *, parent_run_reference: str) -> FakeManagedRun:
        self.run.parent_run_reference = parent_run_reference
        return self.run


def _install_gateway(monkeypatch: pytest.MonkeyPatch, gateway: FakeManagedGateway) -> None:
    monkeypatch.setattr(
        main_module.ManagedMetaAgentGateway,
        "for_router",
        staticmethod(lambda _router_service: gateway),
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)


def _classic_plan_json() -> str:
    return json.dumps(
        {
            "thought": "Create one bounded delivery task.",
            "sub_tasks": [
                {
                    "name": "deliver",
                    "description": "Produce the requested delivery.",
                    "inputs": [{"name": "goal", "type": "string"}],
                    "outputs": [{"name": "answer", "type": "string"}],
                    "agent": {
                        "name": "delivery_agent",
                        "prompt": "Use {goal}.\n\n## answer\nReturn the answer.",
                        "tool_names": [],
                    },
                }
            ],
        }
    )


def _planner_outputs() -> list[str]:
    plan = MetaPlannerTaskPlan(
        summary="Produce one bounded candidate.",
        tasks=[
            MetaPlannerTask(
                task_id="deliver",
                title="Deliver",
                objective="Produce the final answer.",
                output_contract="Final answer.",
            )
        ],
    )
    legacy_blueprint = MetaPlannerTypedBlueprintV2(
        name="Managed Meta Agent",
        description="A bounded managed candidate.",
        nodes=[
            MetaPlannerIRNode(
                ref="writer",
                kind="workflow_agent",
                title="Writer",
                task_ids=["deliver"],
                inputs=[
                    MetaPlannerIRInputBinding(
                        port="request", variable="user_input", value_type="string"
                    )
                ],
                outputs=[
                    MetaPlannerIROutputBinding(
                        port="result", variable="answer", value_type="string"
                    )
                ],
                config=MetaPlannerWorkflowAgentConfig(
                    role_prompt="Produce the requested final answer.",
                    task_input="{{user_input}}",
                ).model_dump(mode="json"),
            )
        ],
        final_output=MetaPlannerIRFinalOutput(node_ref="writer", variable="answer"),
    )
    blueprint, compatibility = v2_to_graph_intent(legacy_blueprint)
    assert blueprint is not None
    assert compatibility.lossy is False
    return [
        plan.model_dump_json(),
        json.dumps({"name": "invalid"}),
        blueprint.model_dump_json(),
    ]


def _unresolved_planner_outputs() -> list[str]:
    outputs = _planner_outputs()
    outputs[-1] = json.dumps({"name": "still invalid"})
    return outputs


@pytest.mark.asyncio
async def test_classic_workflow_endpoint_uses_one_managed_call_and_returns_receipt(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_run = FakeManagedRun([_classic_plan_json()])
    _install_gateway(monkeypatch, FakeManagedGateway(managed_run))

    async def legacy_completion(*_args, **_kwargs):
        raise AssertionError("managed_required must not call the legacy gateway")

    monkeypatch.setattr(
        main_module, "collect_chat_completion_text", legacy_completion
    )
    response = await client.post(
        "/api/meta-agent/generate-workflow",
        json={
            "goal": "Generate a bounded workflow for a managed Meta Agent test.",
            "model_id": MODEL_ID,
            "temperature": 0.2,
            "max_tasks": 3,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == managed_run.parent_run_reference
    assert payload["provider_route_receipts"]["status"] == "passed"
    assert payload["provider_route_receipts"]["call_count"] == 1
    assert len(managed_run.calls) == 1


@pytest.mark.asyncio
async def test_classic_invalid_workflow_marks_managed_receipt_failed(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = json.loads(_classic_plan_json())
    duplicate = dict(plan["sub_tasks"][0])
    duplicate["description"] = "A second task with the same generated node id."
    plan["sub_tasks"].append(duplicate)
    managed_run = FakeManagedRun([json.dumps(plan)])
    _install_gateway(monkeypatch, FakeManagedGateway(managed_run))

    async def legacy_completion(*_args, **_kwargs):
        raise AssertionError("managed_required must not call the legacy gateway")

    monkeypatch.setattr(
        main_module, "collect_chat_completion_text", legacy_completion
    )
    response = await client.post(
        "/api/meta-agent/generate-workflow",
        json={
            "goal": "Generate a workflow whose duplicate task names fail validation.",
            "model_id": MODEL_ID,
            "temperature": 0.2,
            "max_tasks": 3,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["valid"] is False
    assert payload["provider_route_receipts"]["status"] == "failed"
    assert payload["provider_route_receipts"]["reason_codes"] == [
        "provider_workload_meta_agent_validation_failed"
    ]
    assert payload["provider_route_receipts"]["call_count"] == 1
    assert len(managed_run.calls) == 1


@pytest.mark.asyncio
async def test_xpert_candidate_endpoint_records_plan_blueprint_and_one_repair(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_run = FakeManagedRun(_planner_outputs())
    _install_gateway(monkeypatch, FakeManagedGateway(managed_run))

    async def legacy_completion(*_args, **_kwargs):
        raise AssertionError("managed_required must not call the legacy gateway")

    monkeypatch.setattr(
        main_module, "collect_chat_completion_text", legacy_completion
    )
    response = await client.post(
        "/api/meta-agent/generate-xpert-candidate",
        json={
            "goal": "Generate a bounded Xpert candidate for managed routing.",
            "mode": "create",
            "planner_model_id": MODEL_ID,
            "default_agent_model_id": MODEL_ID,
            "temperature": 0.2,
            "max_agents": 3,
            "scope": {},
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == managed_run.parent_run_reference
    assert payload["repair_used"] is True
    assert payload["validation"]["valid"] is True
    assert payload["provider_route_receipts"]["call_count"] == 3
    assert [item.call_sequence for item in managed_run.calls] == [1, 2, 3]


@pytest.mark.asyncio
async def test_xpert_candidate_legacy_gateway_requests_json_without_reasoning(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_gateway = FakeManagedGateway(FakeManagedRun([]), mode="legacy")
    _install_gateway(monkeypatch, legacy_gateway)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://mock", "key"),
    )
    outputs = _planner_outputs()
    observed: list[dict[str, object]] = []

    async def legacy_completion(*_args, **kwargs):
        observed.append(dict(kwargs))
        return outputs.pop(0)

    monkeypatch.setattr(
        main_module, "collect_chat_completion_text", legacy_completion
    )

    response = await client.post(
        "/api/meta-agent/generate-xpert-candidate",
        json={
            "goal": "Generate a bounded Xpert candidate through the legacy route.",
            "mode": "create",
            "planner_model_id": MODEL_ID,
            "default_agent_model_id": MODEL_ID,
            "temperature": 0.2,
            "max_agents": 3,
            "scope": {},
        },
    )

    assert response.status_code == 200, response.text
    assert len(observed) == 3
    for call in observed:
        assert call["response_format"] == {"type": "json_object"}
        assert call["reasoning"] == {"effort": "none", "exclude": True}
        assert call["allow_json_reasoning_fallback"] is True


@pytest.mark.asyncio
async def test_xpert_candidate_validation_failure_marks_managed_run_failed(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_run = FakeManagedRun(_unresolved_planner_outputs())
    _install_gateway(monkeypatch, FakeManagedGateway(managed_run))

    response = await client.post(
        "/api/meta-agent/generate-xpert-candidate",
        json={
            "goal": "Generate a candidate that remains invalid after repair.",
            "mode": "create",
            "planner_model_id": MODEL_ID,
            "default_agent_model_id": MODEL_ID,
            "temperature": 0.2,
            "max_agents": 3,
            "scope": {},
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    receipt = payload["provider_route_receipts"]
    assert payload["validation"]["valid"] is False
    assert receipt["status"] == "failed"
    assert receipt["reason_codes"] == [
        "provider_workload_meta_agent_validation_failed"
    ]
    assert receipt["call_count"] == 3
    assert all(item.status == "passed" for item in managed_run.calls)


@pytest.mark.asyncio
async def test_degraded_meta_agent_fails_before_legacy_or_managed_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_run = FakeManagedRun([_classic_plan_json()])
    _install_gateway(
        monkeypatch, FakeManagedGateway(managed_run, mode="degraded_required")
    )
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("degraded_required must not inspect the legacy gateway")
        ),
    )
    response = await client.post(
        "/api/meta-agent/generate-workflow",
        json={
            "goal": "Generate a bounded workflow for degraded routing behavior.",
            "model_id": MODEL_ID,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == (
        "provider_workload_meta_agent_degraded_required"
    )
    assert managed_run.calls == []
