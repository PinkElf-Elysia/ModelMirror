from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import app
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError


RECEIPT = {
    "contract_version": "modelmirror-provider-workload-routing-v1",
    "entry_id": "fusion",
    "routing_mode": "managed_required",
    "run_reference": "workrun_fusion_api",
    "status": "passed",
    "call_count": 1,
    "reason_codes": [],
    "calls": [
        {
            "call_sequence": 1,
            "model_id": "openrouter/fusion",
            "actual_model": "openrouter/fusion",
            "dispatched": True,
            "status": "passed",
            "error_code": None,
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
        }
    ],
}


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


class FakeFusionRun:
    def __init__(self) -> None:
        self.arguments: dict | None = None

    async def stream_events(self, **kwargs):
        self.arguments = kwargs
        yield {"event": "fusion_delta", "output": "managed answer"}
        yield {
            "event": "fusion_end",
            "mode": "native",
            "final_output": "managed answer",
            "provider_route_receipts": RECEIPT,
        }


class FakeFusionGateway:
    def __init__(self, mode: str, run: FakeFusionRun | None = None) -> None:
        self.mode = mode
        self.run = run or FakeFusionRun()

    def routing_mode(self) -> str:
        return self.mode

    def start_run(self) -> FakeFusionRun:
        if self.mode != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "Fusion Managed policy is not active.",
                status_code=409,
                receipt=self.blocked_receipt(
                    "provider_workload_policy_not_active"
                ),
            )
        return self.run

    @staticmethod
    def blocked_receipt(code: str) -> dict:
        return {
            **RECEIPT,
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [code],
            "calls": [],
        }


def _request_payload(*, native: bool = True) -> dict:
    return {
        "model_ids": ["provider/a", "provider/b"],
        "judge_model_id": "provider/judge",
        "use_native_fusion": native,
        "messages": [{"role": "user", "content": "Compare them."}],
        "temperature": 0.2,
        "max_tokens": 256,
    }


@pytest.mark.asyncio
async def test_managed_fusion_does_not_require_legacy_gateway(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = FakeFusionRun()
    monkeypatch.setenv("MODEL_CONTROL_FUSION_ENABLED", "true")
    monkeypatch.setattr(
        main_module, "fusion_managed_gateway", lambda: FakeFusionGateway("managed_required", run)
    )
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)

    response = await client.post("/api/fusion/chat", json=_request_payload())

    assert response.status_code == 200
    assert "managed answer" in response.text
    assert "provider_route_receipts" in response.text
    assert run.arguments is not None
    assert run.arguments["use_native_fusion"] is True
    assert run.arguments["candidate_model_ids"] == ["provider/a", "provider/b"]


@pytest.mark.asyncio
async def test_degraded_managed_fusion_fails_before_stream(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_FUSION_ENABLED", "true")
    monkeypatch.setattr(
        main_module, "fusion_managed_gateway", lambda: FakeFusionGateway("degraded_required")
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)

    response = await client.post("/api/fusion/chat", json=_request_payload())

    assert response.status_code == 200
    assert '"reason_code": "provider_workload_policy_not_active"' in response.text
    assert '"provider_route_receipts"' in response.text
    assert '"call_count": 0' in response.text


@pytest.mark.asyncio
async def test_legacy_fusion_still_requires_legacy_gateway(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_control_plane_initializes() -> FakeFusionGateway:
        raise AssertionError("disabled Fusion must not initialize control plane")

    monkeypatch.setenv("MODEL_CONTROL_FUSION_ENABLED", "false")
    monkeypatch.setattr(
        main_module,
        "fusion_managed_gateway",
        fail_if_control_plane_initializes,
    )
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))

    response = await client.post("/api/fusion/chat", json=_request_payload())

    assert response.status_code == 500
    assert response.json()["error"] == main_module.LLM_GATEWAY_NOT_CONFIGURED_MESSAGE


def test_receipt_fixture_contains_no_prompt_or_connection_details() -> None:
    serialized = json.dumps(RECEIPT)
    assert "Compare them" not in serialized
    assert "connection_id" not in serialized
    assert "base_url" not in serialized
