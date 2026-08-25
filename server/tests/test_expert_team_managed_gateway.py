from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from server.model_router.expert_team_gateway import ManagedExpertTeamGateway
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import PROVIDER_WORKLOAD_CONTRACT_VERSION
from server.orchestration_worker import AgencyWorkerError
from server.orchestration_worker.contracts import AgencyModelMessage, AgencyModelRequest


MODEL_ID = "provider/expert-team-model"


def _request(*, request_id: str, json_response: bool) -> AgencyModelRequest:
    return AgencyModelRequest(
        request_id=request_id,
        model_id=MODEL_ID,
        messages=[
            AgencyModelMessage(role="system", content="Follow the contract."),
            AgencyModelMessage(role="user", content="Produce the result."),
        ],
        temperature=0.0 if json_response else 0.3,
        max_tokens=512,
        json_response=json_response,
    )


def _gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry_id: str,
    shapes: list[str],
    handler,
) -> ManagedExpertTeamGateway:
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Expert Team Provider",
            kind="openrouter",
            base_url="https://provider.example/v1",
            api_key="test-key",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-24T00:00:00+00:00",
    )
    connection_fingerprint = repository.connection_config_fingerprint(
        "local", connection.id
    )
    repository.claim_catalog_refresh(
        "local",
        refresh_id="refresh-r6g",
        connection_id=connection.id,
        connection_fingerprint=connection_fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        "refresh-r6g",
        connection_id=connection.id,
        models=[
            {
                "model_id": MODEL_ID,
                "normalized_model_id": MODEL_ID,
                "capability_state": "declared",
            }
        ],
        offerings=[],
        model_count=1,
        truncated=False,
        catalog_fingerprint="catalog-r6g",
        observed_at="2026-08-24T00:00:00+00:00",
    )
    bindings = []
    for index, shape in enumerate(shapes, start=1):
        profile = {
            "execution_shape": shape,
            "model_id": MODEL_ID,
            "candidate_model_ids": [],
            "judge_model_id": None,
        }
        profile_fingerprint = hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        certification, created = repository.claim_workload_certification(
            "local",
            certification_id=f"cert-r6g-{index}",
            connection_id=connection.id,
            connection_fingerprint=connection_fingerprint,
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            execution_shape=shape,
            requested_model=MODEL_ID,
            profile=profile,
            profile_fingerprint=profile_fingerprint,
            idempotency_key_hash=hashlib.sha256(
                f"idem-r6g-{index}".encode()
            ).hexdigest(),
        )
        assert created is True
        repository.complete_workload_certification(
            "local",
            str(certification["id"]),
            status="passed",
            checks={
                "content_observed": True,
                "actual_model_verified": True,
                "json_object_verified": shape == "chat_json_object",
            },
            warning_codes=[],
            actual_model=MODEL_ID,
        )
        bindings.append(
            ProviderWorkloadBindingUpdate(
                connection_id=connection.id,
                model_id=MODEL_ID,
                execution_shape=shape,
            )
        )
    monkeypatch.setenv(
        "MODEL_CONTROL_EXPERT_TEAM_PLANNER_ENABLED" if entry_id == "expert_team_planner" else "MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED",
        "true",
    )
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    control = ManagedExpertTeamGateway.for_router(service).call_service.control
    policy = control.get_policy(entry_id)
    updated = control.update_policy(
        entry_id,
        ProviderWorkloadPolicyUpdate(
            expected_revision=policy.revision,
            bindings=bindings,
        ),
    )
    control.activate(
        entry_id,
        ProviderWorkloadActivationRequest(
            expected_revision=updated.revision,
            acknowledge_fail_closed=True,
            no_open_p0_p1=True,
        ),
    )
    return ManagedExpertTeamGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )


def test_planner_uses_text_unary_qualification_and_records_one_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [{"message": {"content": "name: Smoke plan"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    gateway = _gateway(
        tmp_path,
        monkeypatch,
        entry_id="expert_team_planner",
        shapes=["chat_text_unary"],
        handler=handler,
    )
    run = gateway.start_run(
        "expert_team_planner", parent_run_reference="planner:run-1"
    )
    response = asyncio.run(
        run.complete(_request(request_id="planner-request-1", json_response=False))
    )
    receipt = run.finish("passed")

    assert response.content == "name: Smoke plan"
    assert response.usage == {"input_tokens": 3, "output_tokens": 4}
    assert len(requests) == 1
    assert "response_format" not in json.loads(requests[0].content)
    assert receipt["entry_id"] == "expert_team_planner"
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["status"] == "passed"


def test_dag_uses_independent_text_and_json_qualifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        content = '{"pass":true,"failed":[]}' if payload.get("response_format") else "delivery"
        return httpx.Response(
            200,
            json={"model": MODEL_ID, "choices": [{"message": {"content": content}}]},
        )

    gateway = _gateway(
        tmp_path,
        monkeypatch,
        entry_id="expert_team_dag",
        shapes=["chat_text_unary", "chat_json_object"],
        handler=handler,
    )
    run = gateway.start_run("expert_team_dag", parent_run_reference="dag:run-1")

    async def scenario():
        text = await run.complete(
            _request(request_id="dag-text-request", json_response=False)
        )
        judge = await run.complete(
            _request(request_id="dag-json-request", json_response=True)
        )
        return text, judge

    text, judge = asyncio.run(scenario())
    receipt = run.finish("passed")
    assert text.content == "delivery"
    assert json.loads(judge.content)["pass"] is True
    assert len(payloads) == 2
    assert "response_format" not in payloads[0]
    assert payloads[1]["response_format"] == {"type": "json_object"}
    assert receipt["call_count"] == 2
    assert [call["call_sequence"] for call in receipt["calls"]] == [1, 2]


def test_duplicate_worker_request_id_is_never_dispatched_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatches = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return httpx.Response(
            200,
            json={"model": MODEL_ID, "choices": [{"message": {"content": "ok"}}]},
        )

    gateway = _gateway(
        tmp_path,
        monkeypatch,
        entry_id="expert_team_dag",
        shapes=["chat_text_unary", "chat_json_object"],
        handler=handler,
    )
    run = gateway.start_run("expert_team_dag", parent_run_reference="dag:run-replay")
    request = _request(request_id="same-worker-request", json_response=False)

    async def scenario():
        await run.complete(request)
        with pytest.raises(AgencyWorkerError) as caught:
            await run.complete(request)
        assert getattr(caught.value, "code", "") == "provider_workload_logical_call_replay_blocked"

    asyncio.run(scenario())
    assert dispatches == 1
    receipt = run.finish("failed", reason_code="provider_workload_logical_call_replay_blocked")
    assert receipt["call_count"] == 1


def test_feature_off_keeps_legacy_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODEL_CONTROL_EXPERT_TEAM_PLANNER_ENABLED", raising=False)
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    gateway = ManagedExpertTeamGateway.for_router(
        ModelRouterService(repository)
    )
    assert gateway.routing_mode("expert_team_planner") == "legacy"
