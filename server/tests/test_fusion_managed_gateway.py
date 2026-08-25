from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.fusion_gateway import ManagedFusionGateway
from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadControlService,
)


CANDIDATE_A = "provider/candidate-a"
CANDIDATE_B = "provider/candidate-b"
JUDGE = "provider/judge"
FUSION = "openrouter/fusion"
MESSAGES = [{"role": "user", "content": "Compare the candidates."}]


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sse(model_id: str, text: str) -> bytes:
    chunks = [
        {
            "model": model_id,
            "choices": [{"delta": {"content": text}, "finish_reason": None}],
        },
        {
            "model": model_id,
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
    ]
    return (
        "".join(f"data: {json.dumps(item)}\n\n" for item in chunks)
        + "data: [DONE]\n\n"
    ).encode("utf-8")


def _gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    handler,
    include_judge_binding: bool = True,
) -> tuple[ManagedFusionGateway, SQLiteRouterRepository]:
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Fusion Provider",
            kind="openrouter",
            base_url="https://provider.example/v1",
            api_key="test-fusion-key",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=4,
        checked_at="2026-08-24T00:00:00+00:00",
    )
    connection_fingerprint = repository.connection_config_fingerprint(
        "local", connection.id
    )
    repository.claim_catalog_refresh(
        "local",
        refresh_id="refresh-r6h",
        connection_id=connection.id,
        connection_fingerprint=connection_fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        "refresh-r6h",
        connection_id=connection.id,
        models=[
            {
                "model_id": model_id,
                "normalized_model_id": model_id,
                "capability_state": "declared",
            }
            for model_id in (CANDIDATE_A, CANDIDATE_B, JUDGE, FUSION)
        ],
        offerings=[],
        model_count=4,
        truncated=False,
        catalog_fingerprint="catalog-r6h",
        observed_at="2026-08-24T00:00:00+00:00",
    )
    for model_id in (CANDIDATE_A, CANDIDATE_B, JUDGE):
        certification, created = repository.claim_chat_certification(
            "local",
            certification_id=f"chat-{model_id.rsplit('/', 1)[-1]}",
            connection_id=connection.id,
            connection_fingerprint=connection_fingerprint,
            contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
            capability="chat_text",
            requested_model=model_id,
            idempotency_key_hash=_fingerprint({"chat": model_id}),
        )
        assert created is True
        repository.complete_chat_certification(
            "local",
            str(certification["id"]),
            status="passed",
            checks={
                "catalog_contains_model": True,
                "http_2xx": True,
                "content_observed": True,
                "response_complete": True,
                "terminal_observed": True,
            },
            warning_codes=[],
            actual_model=model_id,
        )

    native_profile = {
        "execution_shape": "fusion_native",
        "model_id": FUSION,
        "candidate_model_ids": [CANDIDATE_A, CANDIDATE_B],
        "judge_model_id": JUDGE,
    }
    native_certification, created = repository.claim_workload_certification(
        "local",
        certification_id="native-fusion-cert",
        connection_id=connection.id,
        connection_fingerprint=connection_fingerprint,
        contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
        execution_shape="fusion_native",
        requested_model=FUSION,
        profile=native_profile,
        profile_fingerprint=_fingerprint(native_profile),
        idempotency_key_hash=_fingerprint({"native": "r6h"}),
    )
    assert created is True
    repository.complete_workload_certification(
        "local",
        str(native_certification["id"]),
        status="passed",
        checks={
            "content_observed": True,
            "actual_model_verified": True,
            "fusion_profile_verified": True,
        },
        warning_codes=[],
        actual_model=JUDGE,
    )

    monkeypatch.setenv("MODEL_CONTROL_FUSION_ENABLED", "true")
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    bindings = [
        ProviderWorkloadBindingUpdate(
            connection_id=connection.id,
            model_id=model_id,
            execution_shape="chat_text",
        )
        for model_id in (
            CANDIDATE_A,
            CANDIDATE_B,
            *([JUDGE] if include_judge_binding else []),
        )
    ]
    bindings.append(
        ProviderWorkloadBindingUpdate(
            connection_id=connection.id,
            model_id=FUSION,
            execution_shape="fusion_native",
        )
    )
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "fusion",
        ProviderWorkloadPolicyUpdate(expected_revision=0, bindings=bindings),
    )
    control.activate(
        "fusion",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            acknowledge_fail_closed=True,
            no_open_p0_p1=True,
        ),
    )
    return (
        ManagedFusionGateway.for_router(
            service,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), trust_env=False
            ),
        ),
        repository,
    )


async def _events(run, *, native: bool) -> list[dict]:
    return [
        event
        async for event in run.stream_events(
            use_native_fusion=native,
            candidate_model_ids=[CANDIDATE_A, CANDIDATE_B],
            judge_model_id=JUDGE,
            messages=MESSAGES,
            user_question="Compare the candidates.",
            temperature=0.2,
            max_tokens=256,
        )
    ]


@pytest.mark.asyncio
async def test_application_preflight_failure_sends_zero_provider_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    gateway, _repository = _gateway(
        tmp_path,
        monkeypatch,
        handler=handler,
        include_judge_binding=False,
    )
    events = await _events(gateway.start_run(), native=False)

    assert requests == []
    assert events[-1]["event"] == "error"
    assert events[-1]["reason_code"] == "provider_workload_binding_missing"
    receipt = events[-1]["provider_route_receipts"]
    assert receipt["call_count"] == 0
    assert all(call["dispatched"] is False for call in receipt["calls"])


@pytest.mark.asyncio
async def test_application_partial_candidate_failure_keeps_planned_calls_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        if model_id == CANDIDATE_A:
            return httpx.Response(500)
        return httpx.Response(200, content=_sse(model_id, f"answer:{model_id}"))

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    events = await _events(gateway.start_run(), native=False)

    assert sorted(requested_models) == sorted([CANDIDATE_A, CANDIDATE_B, JUDGE])
    assert len(requested_models) == 3
    assert events[-1]["event"] == "fusion_end"
    assert events[-1]["mode"] == "application"
    receipt = events[-1]["provider_route_receipts"]
    assert receipt["status"] == "failed"
    assert receipt["reason_codes"] == [
        "provider_workload_fusion_partial_candidate_failure"
    ]
    assert receipt["call_count"] == 3
    calls = {call["model_id"]: call for call in receipt["calls"]}
    assert calls[CANDIDATE_A]["status"] == "failed"
    assert calls[CANDIDATE_B]["status"] == "passed"
    assert calls[JUDGE]["status"] == "passed"


@pytest.mark.asyncio
async def test_native_fusion_uses_exact_profile_and_one_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(200, content=_sse(JUDGE, "native answer"))

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    events = await _events(gateway.start_run(), native=True)

    assert len(payloads) == 1
    assert payloads[0]["model"] == FUSION
    assert payloads[0]["plugins"] == [
        {
            "id": "fusion",
            "analysis_models": [CANDIDATE_A, CANDIDATE_B],
            "model": JUDGE,
        }
    ]
    assert events[-1]["event"] == "fusion_end"
    assert events[-1]["mode"] == "native"
    receipt = events[-1]["provider_route_receipts"]
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["actual_model"] == JUDGE


@pytest.mark.asyncio
async def test_native_fusion_rejects_actual_model_other_than_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_models.append(json.loads(request.content)["model"])
        return httpx.Response(200, content=_sse(CANDIDATE_A, "wrong model"))

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    events = await _events(gateway.start_run(), native=True)

    assert requested_models == [FUSION]
    assert events[-1]["event"] == "error"
    assert events[-1]["reason_code"] == "provider_workload_actual_model_mismatch"
    assert events[-1]["provider_route_receipts"]["call_count"] == 1


@pytest.mark.asyncio
async def test_native_profile_drift_blocks_before_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse(FUSION, "must not run"))

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    run = gateway.start_run()
    events = [
        event
        async for event in run.stream_events(
            use_native_fusion=True,
            candidate_model_ids=[CANDIDATE_B, CANDIDATE_A],
            judge_model_id=JUDGE,
            messages=MESSAGES,
            user_question="Compare the candidates.",
            temperature=0.2,
            max_tokens=256,
        )
    ]

    assert requests == []
    assert events[-1]["event"] == "error"
    assert events[-1]["reason_code"] == "provider_workload_fusion_profile_mismatch"
    assert events[-1]["provider_route_receipts"]["call_count"] == 0


@pytest.mark.asyncio
async def test_native_failure_never_starts_application_fusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_models.append(json.loads(request.content)["model"])
        return httpx.Response(503)

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    events = await _events(gateway.start_run(), native=True)

    assert requested_models == [FUSION]
    assert events[-1]["event"] == "error"
    assert events[-1]["reason_code"] == "provider_workload_http_5xx"
    assert events[-1]["provider_route_receipts"]["call_count"] == 1


@pytest.mark.asyncio
async def test_application_all_candidates_fail_without_dispatching_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        return httpx.Response(503)

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    events = await _events(gateway.start_run(), native=False)

    assert sorted(requested_models) == sorted([CANDIDATE_A, CANDIDATE_B])
    assert JUDGE not in requested_models
    assert events[-1]["event"] == "error"
    assert events[-1]["reason_code"] == (
        "provider_workload_fusion_no_candidate_answers"
    )
    calls = events[-1]["provider_route_receipts"]["calls"]
    judge_call = next(call for call in calls if call["model_id"] == JUDGE)
    assert judge_call["dispatched"] is False


@pytest.mark.asyncio
async def test_application_judge_failure_has_no_fallback_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        if model_id == JUDGE:
            return httpx.Response(429)
        return httpx.Response(200, content=_sse(model_id, f"answer:{model_id}"))

    gateway, _repository = _gateway(tmp_path, monkeypatch, handler=handler)
    events = await _events(gateway.start_run(), native=False)

    assert sorted(requested_models) == sorted([CANDIDATE_A, CANDIDATE_B, JUDGE])
    assert len(requested_models) == 3
    assert events[-1]["event"] == "error"
    assert events[-1]["reason_code"] == "provider_workload_http_429"
    assert events[-1]["provider_route_receipts"]["call_count"] == 3


@pytest.mark.asyncio
async def test_native_profile_storage_error_closes_claimed_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse(FUSION, "must not run"))

    gateway, repository = _gateway(tmp_path, monkeypatch, handler=handler)

    original_profile_lookup = repository.get_latest_workload_certification

    def fail_profile_lookup(*args, **kwargs):
        if kwargs.get("profile_fingerprint"):
            raise RuntimeError("storage unavailable")
        return original_profile_lookup(*args, **kwargs)

    run = gateway.start_run()
    monkeypatch.setattr(
        repository,
        "get_latest_workload_certification",
        fail_profile_lookup,
    )
    events = await _events(run, native=True)

    assert requests == []
    assert events[-1]["reason_code"] == (
        "provider_workload_fusion_profile_unavailable"
    )
    receipts = repository.list_workload_receipts("local")
    assert all(row["status"] != "running" for row in receipts["runs"])
    assert all(row["status"] != "running" for row in receipts["calls"])
