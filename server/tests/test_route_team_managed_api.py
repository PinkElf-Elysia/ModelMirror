from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import app
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError


MODEL_ID = "provider/route-team-model"


def _receipt(entry_id: str, call_count: int, status: str = "passed") -> dict:
    return {
        "contract_version": "modelmirror-provider-workload-routing-v1",
        "entry_id": entry_id,
        "routing_mode": "managed_required",
        "run_reference": f"workrun_{entry_id}_api",
        "status": status,
        "call_count": call_count,
        "reason_codes": [],
        "calls": [
            {
                "call_sequence": index,
                "model_id": MODEL_ID,
                "actual_model": MODEL_ID,
                "dispatched": True,
                "status": "passed",
                "error_code": None,
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            }
            for index in range(1, call_count + 1)
        ],
    }


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


class FakeRouteTeamRun:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.logical_call_keys: list[str] = []
        self.streamed_calls: list[int] = []

    async def prepare_plan(self, *, model_id: str, logical_call_keys):
        assert model_id == MODEL_ID
        self.logical_call_keys = list(logical_call_keys)
        return tuple(range(1, len(self.logical_call_keys) + 1))

    async def stream_text(self, prepared, **_kwargs):
        self.streamed_calls.append(int(prepared))
        yield f"managed-{prepared}"

    def finish(self, status: str, *, reason_code: str | None = None) -> dict:
        receipt = _receipt(self.entry_id, len(self.streamed_calls), status)
        receipt["reason_codes"] = [reason_code] if reason_code else []
        return receipt

    def receipt_summary(self) -> dict:
        return _receipt(self.entry_id, len(self.streamed_calls), "running")

    @staticmethod
    def failure_status() -> str:
        return "failed"

    @staticmethod
    def abandon(*_args, **_kwargs) -> None:
        return None


class FakeRouteTeamGateway:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.runs: dict[str, FakeRouteTeamRun] = {}

    def routing_mode(self, _entry_id: str) -> str:
        return self.mode

    def start_run(self, entry_id: str) -> FakeRouteTeamRun:
        if self.mode != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "Managed policy is not active.",
                status_code=409,
                receipt=self.blocked_receipt(
                    entry_id, "provider_workload_policy_not_active"
                ),
            )
        run = FakeRouteTeamRun(entry_id)
        self.runs[entry_id] = run
        return run

    @staticmethod
    def blocked_receipt(entry_id: str, reason_code: str) -> dict:
        receipt = _receipt(entry_id, 0, "failed")
        receipt["run_reference"] = "blocked_before_dispatch"
        receipt["reason_codes"] = [reason_code]
        return receipt


@pytest.mark.asyncio
async def test_managed_route_and_team_do_not_require_legacy_gateway(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeRouteTeamGateway("managed_required")
    experts = main_module.AGENT_RECORDS[:2]
    monkeypatch.setenv("MODEL_CONTROL_ROUTE_AGENT_ENABLED", "true")
    monkeypatch.setenv("MODEL_CONTROL_TEAM_CHAT_ENABLED", "true")
    monkeypatch.setattr(main_module, "route_team_managed_gateway", lambda: gateway)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)

    route = await client.post(
        "/api/route-agent",
        json={"message": "制定测试计划", "model_id": MODEL_ID},
    )
    team = await client.post(
        "/api/team/chat",
        json={
            "message": "协作制定测试计划",
            "model_id": MODEL_ID,
            "mode": "serial",
            "members": [
                {"agent_id": experts[0].id, "task": "分析风险"},
                {"agent_id": experts[1].id, "task": "整理验收"},
            ],
        },
    )

    assert route.status_code == 200
    assert '"event": "answer_end"' in route.text
    assert '"entry_id": "route_agent"' in route.text
    assert gateway.runs["route_agent"].logical_call_keys == ["route-answer:1"]
    assert gateway.runs["route_agent"].streamed_calls == [1]

    assert team.status_code == 200
    assert '"event": "team_end"' in team.text
    assert '"entry_id": "team_chat"' in team.text
    assert gateway.runs["team_chat"].logical_call_keys == [
        "member:1",
        "member:2",
        "summary:3",
    ]
    assert gateway.runs["team_chat"].streamed_calls == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "flag", "entry_id", "payload"),
    [
        (
            "/api/route-agent",
            "MODEL_CONTROL_ROUTE_AGENT_ENABLED",
            "route_agent",
            {"message": "制定测试计划", "model_id": MODEL_ID},
        ),
        (
            "/api/team/chat",
            "MODEL_CONTROL_TEAM_CHAT_ENABLED",
            "team_chat",
            {
                "message": "协作制定测试计划",
                "model_id": MODEL_ID,
                "members": [],
            },
        ),
    ],
)
async def test_degraded_managed_entry_fails_closed_with_zero_call_receipt(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    flag: str,
    entry_id: str,
    payload: dict,
) -> None:
    if entry_id == "team_chat":
        expert = main_module.AGENT_RECORDS[0]
        payload = {**payload, "members": [{"agent_id": expert.id}]}
    monkeypatch.setenv(flag, "true")
    monkeypatch.setattr(
        main_module,
        "route_team_managed_gateway",
        lambda: FakeRouteTeamGateway("degraded_required"),
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)

    response = await client.post(path, json=payload)

    assert response.status_code == 200
    assert '"reason_code": "provider_workload_policy_not_active"' in response.text
    assert f'"entry_id": "{entry_id}"' in response.text
    assert '"call_count": 0' in response.text


@pytest.mark.asyncio
async def test_disabled_route_and_team_never_initialize_control_plane(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_ROUTE_AGENT_ENABLED", "false")
    monkeypatch.setenv("MODEL_CONTROL_TEAM_CHAT_ENABLED", "false")

    def fail_if_initialized():
        raise AssertionError("disabled entry must not initialize control plane")

    monkeypatch.setattr(main_module, "route_team_managed_gateway", fail_if_initialized)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))

    route = await client.post(
        "/api/route-agent", json={"message": "legacy", "model_id": MODEL_ID}
    )
    team = await client.post(
        "/api/team/chat",
        json={
            "message": "legacy",
            "model_id": MODEL_ID,
            "members": [{"agent_id": main_module.AGENT_RECORDS[0].id}],
        },
    )

    assert route.status_code == 500
    assert team.status_code == 500
    assert route.json()["error"] == main_module.LLM_GATEWAY_NOT_CONFIGURED_MESSAGE
    assert team.json()["error"] == main_module.LLM_GATEWAY_NOT_CONFIGURED_MESSAGE


def test_receipt_fixture_contains_no_prompt_or_connection_details() -> None:
    serialized = json.dumps(_receipt("team_chat", 3))
    assert "协作制定测试计划" not in serialized
    assert "connection_id" not in serialized
    assert "base_url" not in serialized
