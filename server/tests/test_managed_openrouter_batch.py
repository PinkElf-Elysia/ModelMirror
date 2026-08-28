from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.batch_gateway import ManagedOpenRouterBatchGateway
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import (
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)


def _client_factory(
    handler: Callable[[Request], Response],
) -> Callable[[], httpx.AsyncClient]:
    transport = MockTransport(handler)
    return lambda: httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )


async def _configured_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[Request], Response],
    *,
    execution_shape: str = "openrouter_batch_chat",
    model_id: str = "provider/model",
) -> tuple[
    ManagedOpenRouterBatchGateway,
    SQLiteRouterRepository,
]:
    monkeypatch.setenv("MODEL_CONTROL_OPENROUTER_BATCH_ENABLED", "true")
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Managed OpenRouter Batch",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="managed-batch-secret",
            scopes=["batch"],
        ),
    )
    factory = _client_factory(handler)
    router_service = ModelRouterService(
        repository,
        client_factory=factory,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["93.184.216.34"]
        ),
    )
    certification = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=factory,
        batch_poll_interval_seconds=0,
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=execution_shape,
            model_id=model_id,
            acknowledge_billed_call=True,
        ),
        idempotency_key="managed-batch-certification",
    )
    assert certification.status == "passed"
    control = ProviderWorkloadControlService(router_service)
    saved = control.update_policy(
        "openrouter_batch",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=execution_shape,
                    model_id=model_id,
                    connection_id=connection.id,
                )
            ],
        ),
    )
    active = control.activate(
        "openrouter_batch",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert active.effective_status == "managed_required"
    return (
        ManagedOpenRouterBatchGateway.for_router(
            router_service, client_factory=factory
        ),
        repository,
    )


def _submission(input_text: str = "private-batch-input") -> dict[str, object]:
    return {
        "endpoint": "/v1/chat/completions",
        "model": "provider/model",
        "requests": [
            {
                "custom_id": "request-1",
                "body": {
                    "model": "provider/model",
                    "messages": [{"role": "user", "content": input_text}],
                    "temperature": 0,
                    "max_tokens": 16,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_dated_openrouter_model_resolution_remains_qualified_and_in_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_posts = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(
                    202,
                    json={"id": "batch_certification", "status": "validating"},
                )
            runtime_posts += 1
            return Response(
                202,
                json={
                    "id": "batch_runtime",
                    "model": "provider/model-20260709",
                    "status": "in_progress",
                    "request_counts": {"total": 1, "completed": 0, "failed": 0},
                },
            )
        return Response(
            200,
            json={
                "id": "batch_certification",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {"model": "provider/model-20260709"},
                        },
                    }
                ],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    certification = repository.list_workload_certifications("local")[0]
    assert certification["actual_model"] == "provider/model-20260709"
    assert "actual_model_openrouter_alias_resolved" in json.loads(
        str(certification["warnings_json"])
    )

    status, result = await gateway.submit(
        _submission(), idempotency_key="dated-runtime-model"
    )

    assert status == 202
    assert result["status"] == "in_progress"
    assert runtime_posts == 1
    job = repository.list_provider_batch_jobs("local", limit=10)[0]
    assert job["status"] == "in_progress"
    assert job["error_code"] is None


@pytest.mark.asyncio
async def test_managed_batch_idempotency_uses_one_post_and_local_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(
                    202,
                    json={"id": "batch_certification", "status": "validating"},
                )
            return Response(
                202,
                json={
                    "id": "batch_runtime",
                    "model": "provider/model",
                    "status": "validating",
                    "request_counts": {"total": 1, "completed": 0, "failed": 0},
                },
            )
        if request.url.path.endswith("/batch_certification"):
            return Response(
                200,
                json={
                    "id": "batch_certification",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "modelmirror-certification",
                            "response": {
                                "status_code": 200,
                                "body": {"model": "provider/model"},
                            },
                        }
                    ],
                },
            )
        return Response(
            200,
            json={
                "id": "batch_runtime",
                "model": "provider/model",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "usage": {"total_tokens": 7, "cost": 0.0001},
                "results": [
                    {
                        "id": "result-1",
                        "custom_id": "request-1",
                        "response": {
                            "status_code": 200,
                            "body": {
                                "model": "provider/model",
                                "choices": [
                                    {"message": {"content": "private-result"}}
                                ],
                            },
                        },
                        "error": None,
                    }
                ],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    requests.clear()
    first_status, first = await gateway.submit(
        _submission(), idempotency_key="runtime-idempotency"
    )
    replay_status, replay = await gateway.submit(
        _submission(), idempotency_key="runtime-idempotency"
    )

    assert first_status == 202
    assert replay_status == 200
    assert str(first["id"]).startswith("mmbatch_")
    assert first["id"] == replay["id"]
    assert first["id"] != "batch_runtime"
    assert replay["status"] == "completed"
    assert replay["results"][0]["custom_id"] == "request-1"
    assert replay["billing_authoritative"] is False
    assert [request.method for request in requests].count("POST") == 1
    assert [request.method for request in requests].count("GET") == 1
    receipts = repository.list_workload_receipts(
        "local", entry_id="openrouter_batch"
    )
    assert len(receipts["runs"]) == len(receipts["calls"]) == 1
    assert receipts["calls"][0]["dispatched"] == 1
    assert receipts["calls"][0]["result_class"] == "batch_submitted"
    admin_receipt = ProviderWorkloadControlService(
        gateway.router_service
    ).receipts(entry_id="openrouter_batch").runs[0]
    assert admin_receipt.batch_job_id == first["id"]
    assert admin_receipt.batch_status == "completed"
    assert admin_receipt.batch_request_count == 1
    assert admin_receipt.batch_completed_count == 1
    assert admin_receipt.billing_authoritative is False
    assert "batch_runtime" not in admin_receipt.model_dump_json()
    database_bytes = repository.database_path.read_bytes()
    assert b"private-batch-input" not in database_bytes
    assert b"private-result" not in database_bytes
    assert b"managed-batch-secret" not in database_bytes


@pytest.mark.asyncio
async def test_uncertain_managed_batch_submission_never_reposts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_posts = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_cert", "status": "validating"})
            runtime_posts += 1
            raise httpx.ReadTimeout("outcome unknown", request=request)
        return Response(
            200,
            json={
                "id": "batch_cert",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {"model": "provider/model"},
                        },
                    }
                ],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    with pytest.raises(RouterServiceError) as uncertain:
        await gateway.submit(_submission(), idempotency_key="uncertain-runtime")
    replay_status, replay = await gateway.submit(
        _submission(), idempotency_key="uncertain-runtime"
    )

    assert uncertain.value.code == "provider_batch_submission_uncertain"
    assert replay_status == 202
    assert replay["status"] == "uncertain"
    assert replay["error"]["code"] == "provider_batch_submission_uncertain"
    assert runtime_posts == 1
    job = next(
        item
        for item in repository.list_provider_batch_jobs("local")
        if item["purpose"] == "runtime"
    )
    assert job["status"] == "uncertain"


@pytest.mark.asyncio
async def test_restart_recovery_only_polls_runtime_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_posts = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_cert", "status": "validating"})
            runtime_posts += 1
            return Response(202, json={"id": "batch_runtime", "status": "in_progress"})
        if request.url.path.endswith("/batch_cert"):
            return Response(
                200,
                json={
                    "id": "batch_cert",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "modelmirror-certification",
                            "response": {
                                "status_code": 200,
                                "body": {"model": "provider/model"},
                            },
                        }
                    ],
                },
            )
        return Response(
            200,
            json={
                "id": "batch_runtime",
                "model": "provider/model",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [{"custom_id": "request-1", "response": {"status_code": 200}}],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    await gateway.submit(_submission(), idempotency_key="restart-runtime")
    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    router_service = ModelRouterService(
        restarted,
        client_factory=_client_factory(handler),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["93.184.216.34"]
        ),
    )
    recovered = ManagedOpenRouterBatchGateway.for_router(
        router_service,
        client_factory=_client_factory(handler),
    )

    assert await recovered.resume_pending_runtime_jobs() == 1
    runtime_job = next(
        item
        for item in restarted.list_provider_batch_jobs("local")
        if item["purpose"] == "runtime"
    )
    assert runtime_job["status"] == "completed"
    assert runtime_posts == 1


@pytest.mark.asyncio
async def test_client_cancellation_after_dispatch_is_uncertain_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_posts = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_cert", "status": "validating"})
            runtime_posts += 1
            raise asyncio.CancelledError
        return Response(
            200,
            json={
                "id": "batch_cert",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {"model": "provider/model"},
                        },
                    }
                ],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    with pytest.raises(asyncio.CancelledError):
        await gateway.submit(_submission(), idempotency_key="cancelled-runtime")
    status, replay = await gateway.submit(
        _submission(), idempotency_key="cancelled-runtime"
    )
    assert status == 202
    assert replay["status"] == "uncertain"
    assert replay["error"]["code"] == "provider_batch_submission_cancelled"
    assert runtime_posts == 1
    receipt = repository.list_workload_receipts(
        "local", entry_id="openrouter_batch"
    )
    assert receipt["calls"][0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_managed_embedding_batch_uses_its_exact_shape_and_one_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_payloads: list[dict[str, object]] = []

    def handler(request: Request) -> Response:
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/embed-model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_embed_cert", "status": "validating"})
            runtime_payloads.append(body)
            return Response(202, json={"id": "batch_embed_runtime", "status": "validating"})
        return Response(
            200,
            json={
                "id": "batch_embed_cert",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {"model": "provider/embed-model"},
                        },
                    }
                ],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path,
        monkeypatch,
        handler,
        execution_shape="openrouter_batch_embeddings",
        model_id="provider/embed-model",
    )
    status, result = await gateway.submit(
        {
            "endpoint": "/v1/embeddings",
            "model": "provider/embed-model",
            "requests": [
                {
                    "custom_id": "document-1",
                    "body": {
                        "model": "provider/embed-model",
                        "input": "private-embedding-input",
                    },
                }
            ],
        },
        idempotency_key="embedding-runtime",
    )

    assert status == 202
    assert str(result["id"]).startswith("mmbatch_")
    assert runtime_payloads[0]["endpoint"] == "/v1/embeddings"
    assert len(runtime_payloads) == 1
    receipt = repository.list_workload_receipts(
        "local", entry_id="openrouter_batch"
    )["calls"][0]
    assert receipt["execution_shape"] == "openrouter_batch_embeddings"
    assert b"private-embedding-input" not in repository.database_path.read_bytes()


@pytest.mark.asyncio
async def test_concurrent_same_key_claims_one_runtime_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_started = asyncio.Event()
    release_runtime = asyncio.Event()
    runtime_posts = 0

    async def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_cert", "status": "validating"})
            runtime_posts += 1
            runtime_started.set()
            await release_runtime.wait()
            return Response(202, json={"id": "batch_runtime", "status": "validating"})
        return Response(
            200,
            json={
                "id": "batch_cert",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [
                    {
                        "custom_id": "modelmirror-certification",
                        "response": {
                            "status_code": 200,
                            "body": {"model": "provider/model"},
                        },
                    }
                ],
            },
        )

    gateway, _repository = await _configured_gateway(
        tmp_path, monkeypatch, handler  # type: ignore[arg-type]
    )
    first_task = asyncio.create_task(
        gateway.submit(_submission(), idempotency_key="concurrent-runtime")
    )
    await runtime_started.wait()
    replay_status, replay = await gateway.submit(
        _submission(), idempotency_key="concurrent-runtime"
    )
    release_runtime.set()
    first_status, first = await first_task

    assert first_status == replay_status == 202
    assert replay["status"] == "submitting"
    assert first["id"] == replay["id"]
    assert runtime_posts == 1


@pytest.mark.asyncio
async def test_terminal_submission_model_mismatch_fails_receipt_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_posts = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_cert", "status": "validating"})
            runtime_posts += 1
            return Response(
                202,
                json={
                    "id": "batch_runtime",
                    "status": "completed",
                    "model": "provider/other-model",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "request-1",
                            "response": {"body": {"content": "private-result"}},
                        }
                    ],
                },
            )
        if request.url.path.endswith("/batch_cert"):
            return Response(
                200,
                json={
                    "id": "batch_cert",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "modelmirror-certification",
                            "response": {
                                "status_code": 200,
                                "body": {"model": "provider/model"},
                            },
                        }
                    ],
                },
            )
        return Response(
            200,
            json={
                "id": "batch_runtime",
                "status": "completed",
                "model": "provider/other-model",
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "results": [],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    status, result = await gateway.submit(
        _submission(), idempotency_key="runtime-model-mismatch"
    )
    replay_status, replay = await gateway.submit(
        _submission(), idempotency_key="runtime-model-mismatch"
    )

    assert status == 202
    assert result["status"] == "failed"
    assert replay_status == 200
    assert replay["status"] == "failed"
    assert runtime_posts == 1
    receipt = repository.list_workload_receipts(
        "local", entry_id="openrouter_batch"
    )["calls"][0]
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "provider_batch_model_mismatch"
    database_bytes = repository.database_path.read_bytes()
    assert b"private-result" not in database_bytes


@pytest.mark.asyncio
async def test_inconsistent_poll_counts_preserve_job_and_never_repost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_posts = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_posts
        if request.url.path.endswith("/api/v1/models"):
            return Response(200, json={"data": [{"id": "provider/model"}]})
        if request.method == "POST":
            body = json.loads(request.content)
            if body["requests"][0]["custom_id"] == "modelmirror-certification":
                return Response(202, json={"id": "batch_cert", "status": "validating"})
            runtime_posts += 1
            return Response(
                202,
                json={
                    "id": "batch_runtime",
                    "status": "validating",
                    "model": "provider/model",
                    "request_counts": {"total": 1, "completed": 0, "failed": 0},
                },
            )
        if request.url.path.endswith("/batch_cert"):
            return Response(
                200,
                json={
                    "id": "batch_cert",
                    "status": "completed",
                    "request_counts": {"total": 1, "completed": 1, "failed": 0},
                    "results": [
                        {
                            "custom_id": "modelmirror-certification",
                            "response": {
                                "status_code": 200,
                                "body": {"model": "provider/model"},
                            },
                        }
                    ],
                },
            )
        return Response(
            200,
            json={
                "id": "batch_runtime",
                "status": "completed",
                "model": "provider/model",
                "request_counts": {"total": 2, "completed": 2, "failed": 0},
                "results": [],
            },
        )

    gateway, repository = await _configured_gateway(
        tmp_path, monkeypatch, handler
    )
    _status, submitted = await gateway.submit(
        _submission(), idempotency_key="runtime-invalid-counts"
    )
    with pytest.raises(RouterServiceError) as invalid:
        await gateway.poll(str(submitted["id"]))

    assert invalid.value.code == "provider_batch_invalid_poll_response"
    assert runtime_posts == 1
    job = repository.get_provider_batch_job("local", str(submitted["id"]))
    assert job is not None
    assert job["status"] == "validating"
