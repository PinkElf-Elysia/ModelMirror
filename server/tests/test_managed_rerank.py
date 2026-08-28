from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.rerank_gateway import (
    ManagedRerankError,
    ManagedRerankGateway,
)
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
from server.rag.rag_service import ManagedRagRerankRouteError, RagService
from server.rag.reranker import PreparedRerankInput, RerankDocument
from server.skills.semantic_rerank import SkillRerankRequest, SkillSearchIndexV1
from server.skills.semantic_rerank_service import (
    SkillSemanticRerankConfig,
    SkillSemanticRerankError,
    SkillSemanticRerankService,
)


async def _stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_fallback_mode: str = "none",
    runtime_status: int = 200,
    access_mode: str = "dedicated",
    runtime_body: dict[str, Any] | bytes | None = None,
    runtime_timeout: bool = False,
) -> tuple[ManagedRerankGateway, SQLiteRouterRepository, list[Request]]:
    requests: list[Request] = []
    runtime_calls = 0

    def handler(request: Request) -> Response:
        nonlocal runtime_calls
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/rerank-v1"}]})
        payload = json.loads(request.content)
        is_certification = (
            payload.get("query") == "ModelMirror provider routing certification"
            or "ModelMirror provider routing certification"
            in json.dumps(payload, ensure_ascii=False)
        )
        if not is_certification:
            runtime_calls += 1
            if runtime_timeout:
                raise httpx.ReadTimeout("private upstream timeout", request=request)
            if runtime_status != 200:
                return Response(
                    runtime_status,
                    json={"error": "private upstream failure body"},
                )
        if is_certification:
            count = 3
        elif request.url.path.endswith("/rerank"):
            count = len(payload["documents"])
        else:
            user = json.loads(payload["messages"][1]["content"])
            count = len(user["documents"])
        results = [
            {"index": index, "relevance_score": (count - index) / count}
            for index in range(count)
        ]
        body: dict[str, Any] = {
            "model": "provider/rerank-v1",
            "usage": {"prompt_tokens": count, "total_tokens": count},
        }
        if request.url.path.endswith("/rerank"):
            body["results"] = results
        else:
            body["choices"] = [
                {"message": {"content": json.dumps({"results": results})}}
            ]
        if not is_certification and runtime_body is not None:
            if isinstance(runtime_body, bytes):
                return Response(
                    200,
                    content=runtime_body,
                    headers={"content-type": "application/json"},
                )
            body = runtime_body
        return Response(200, json=body)

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"r" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Managed Rerank",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="managed-rerank-secret",
            scopes=["rerank"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    certification = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="rerank_documents",
            model_id="provider/rerank-v1",
            rerank_access_mode=access_mode,
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"cert-{access_mode}",
    )
    assert certification.status == "passed"
    control = ProviderWorkloadControlService(router_service)
    for entry_id, flag in (
        ("rag_rerank", "MODEL_CONTROL_RAG_RERANK_ENABLED"),
        ("skill_rerank", "MODEL_CONTROL_SKILL_RERANK_ENABLED"),
    ):
        monkeypatch.setenv(flag, "true")
        saved = control.update_policy(
            entry_id,
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                local_fallback_mode=local_fallback_mode,
                bindings=[
                    ProviderWorkloadBindingUpdate(
                        execution_shape="rerank_documents",
                        model_id="provider/rerank-v1",
                        connection_id=connection.id,
                        rerank_access_mode=access_mode,
                    )
                ],
            ),
        )
        active = control.activate(
            entry_id,
            ProviderWorkloadActivationRequest(
                expected_revision=saved.revision,
                no_open_p0_p1=True,
                acknowledge_fail_closed=True,
            ),
        )
        assert active.effective_status == "managed_required"
    return (
        ManagedRerankGateway.for_router(
            router_service,
            client_factory=lambda: httpx.AsyncClient(
                transport=transport, follow_redirects=False, trust_env=False
            ),
        ),
        repository,
        requests,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access_mode", "expected_path"),
    [("dedicated", "/v1/rerank"), ("llm_json", "/v1/chat/completions")],
)
async def test_managed_rerank_uses_one_explicit_access_mode_and_one_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    access_mode: str,
    expected_path: str,
) -> None:
    gateway, repository, requests = await _stack(
        tmp_path, monkeypatch, access_mode=access_mode
    )
    certification_request_count = len(requests)
    qualification = gateway.qualification("rag_rerank")
    run = gateway.start_run(
        "rag_rerank", parent_run_reference="rag:managed-rerank:success"
    )
    result = await run.rerank(
        "private query marker",
        ["private document one", "private document two"],
        model_id=qualification.model_id,
        top_n=2,
        logical_call_key="rerank:0",
        call_sequence=1,
        timeout_seconds=3,
    )
    receipt = run.finish_success()

    runtime_requests = [
        request
        for request in requests[certification_request_count:]
        if request.method == "POST"
    ]
    assert len(runtime_requests) == 1
    assert runtime_requests[0].url.path == expected_path
    if access_mode == "llm_json":
        request_payload = json.loads(runtime_requests[0].content)
        system_prompt = request_payload["messages"][0]["content"]
        user_payload = json.loads(request_payload["messages"][1]["content"])
        assert "exactly result_count results" in system_prompt
        assert user_payload["result_count"] == 2
    assert result.access_mode == access_mode
    assert [item.index for item in result.items] == [0, 1]
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["dispatched"] is True
    assert receipt["calls"][0]["operation"] == "rerank_documents"
    assert receipt["calls"][0]["provider_kind"] == "openai_compatible"
    stored = repository.database_path.read_bytes()
    assert b"private query marker" not in stored
    assert b"private document one" not in stored
    assert b"managed-rerank-secret" not in stored


@pytest.mark.asyncio
async def test_rag_and_skill_keep_independent_receipts_and_no_remote_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, repository, requests = await _stack(tmp_path, monkeypatch)
    certification_request_count = len(requests)
    rag = RagService(
        storage_dir=tmp_path / "rag",
        uploads_dir=tmp_path / "uploads",
        managed_rerank_gateway=gateway,
        llm_enabled=False,
    )
    rag_outcome = await rag._rerank_with_control(
        "kb-test",
        "namespace-test",
        "rag private query",
        [RerankDocument("a", "alpha"), RerankDocument("b", "beta")],
        provider="auto",
        model="",
        top_n=2,
    )
    skill = SkillSemanticRerankService(
        search_index=SkillSearchIndexV1(),
        config=SkillSemanticRerankConfig(provider="auto"),
        managed_rerank_gateway=gateway,
    )
    skill_outcome = await skill.search(
        SkillRerankRequest(query="PDF processing", semantic=True)
    )

    runtime_posts = [
        request
        for request in requests[certification_request_count:]
        if request.method == "POST"
    ]
    assert len(runtime_posts) == 2
    assert rag_outcome.execution_mode == "managed"
    assert rag_outcome.provider_route_receipts["entry_id"] == "rag_rerank"
    assert skill_outcome.execution_mode == "managed"
    assert skill_outcome.provider_route_receipts["entry_id"] == "skill_rerank"
    receipts = repository.list_workload_receipts("local")
    assert {str(run["entry_id"]) for run in receipts["runs"]} >= {
        "rag_rerank",
        "skill_rerank",
    }


@pytest.mark.asyncio
async def test_explicit_lexical_fallback_is_local_and_fail_closed_is_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, _repository, requests = await _stack(
        tmp_path,
        monkeypatch,
        local_fallback_mode="lexical",
        runtime_status=503,
    )
    certification_request_count = len(requests)
    rag = RagService(
        storage_dir=tmp_path / "rag-fallback",
        uploads_dir=tmp_path / "uploads-fallback",
        managed_rerank_gateway=gateway,
        llm_enabled=False,
    )
    rag_outcome = await rag._rerank_with_control(
        "kb-test",
        "namespace-test",
        "rag fallback query",
        [RerankDocument("a", "alpha"), RerankDocument("b", "beta")],
        provider="auto",
        model="",
        top_n=2,
    )
    skill = SkillSemanticRerankService(
        search_index=SkillSearchIndexV1(),
        config=SkillSemanticRerankConfig(provider="auto"),
        managed_rerank_gateway=gateway,
    )
    skill_outcome = await skill.search(
        SkillRerankRequest(query="PDF processing", semantic=True)
    )

    assert sum(
        request.method == "POST"
        for request in requests[certification_request_count:]
    ) == 2
    assert rag_outcome.provider == "none"
    assert rag_outcome.execution_mode == "local_non_model"
    assert "local_non_model_fallback" in rag_outcome.fallback_reason_codes
    assert skill_outcome.status == "lexical_fallback"
    assert skill_outcome.execution_mode == "local_non_model"
    assert "local_non_model_fallback" in skill_outcome.fallback_reason_codes

    fail_gateway, _repository, fail_requests = await _stack(
        tmp_path / "fail-closed",
        monkeypatch,
        runtime_status=503,
    )
    fail_rag = RagService(
        storage_dir=tmp_path / "rag-closed",
        uploads_dir=tmp_path / "uploads-closed",
        managed_rerank_gateway=fail_gateway,
        llm_enabled=False,
    )
    with pytest.raises(ManagedRagRerankRouteError):
        await fail_rag._rerank_with_control(
            "kb-test",
            "namespace-test",
            "closed query",
            [RerankDocument("a", "alpha")],
            provider="auto",
            model="",
            top_n=1,
        )
    fail_skill = SkillSemanticRerankService(
        search_index=SkillSearchIndexV1(),
        config=SkillSemanticRerankConfig(provider="auto"),
        managed_rerank_gateway=fail_gateway,
    )
    with pytest.raises(SkillSemanticRerankError):
        await fail_skill.search(
            SkillRerankRequest(query="PDF processing", semantic=True)
        )
    assert sum(request.method == "POST" for request in fail_requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_body", "expected_code"),
    [
        (
            {
                "model": "provider/other-model",
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.8},
                ],
            },
            "provider_rerank_model_mismatch",
        ),
        (
            {
                "model": "provider/rerank-v1",
                "results": [{"index": 0, "relevance_score": 0.9}],
            },
            "provider_rerank_incomplete_results",
        ),
        (
            {
                "model": "provider/rerank-v1",
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ],
            },
            "provider_rerank_invalid_results",
        ),
        (
            b'{"model":"provider/rerank-v1","results":['
            b'{"index":0,"relevance_score":NaN},'
            b'{"index":1,"relevance_score":0.8}]}',
            "provider_rerank_invalid_results",
        ),
    ],
)
async def test_managed_rerank_rejects_untrusted_results_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_body: dict[str, Any] | bytes,
    expected_code: str,
) -> None:
    gateway, _repository, requests = await _stack(
        tmp_path,
        monkeypatch,
        runtime_body=runtime_body,
    )
    certification_request_count = len(requests)
    qualification = gateway.qualification("rag_rerank")
    run = gateway.start_run(
        "rag_rerank", parent_run_reference="rag:managed-rerank:invalid"
    )

    with pytest.raises(ManagedRerankError) as raised:
        await run.rerank(
            "private query",
            ["private document one", "private document two"],
            model_id=qualification.model_id,
            top_n=2,
            logical_call_key="rerank:invalid",
            call_sequence=1,
            timeout_seconds=3,
        )

    runtime_posts = [
        request
        for request in requests[certification_request_count:]
        if request.method == "POST"
    ]
    assert raised.value.code == expected_code
    assert len(runtime_posts) == 1
    assert raised.value.receipt["status"] == "failed"
    assert raised.value.receipt["calls"][0]["dispatched"] is True


@pytest.mark.asyncio
async def test_managed_rerank_timeout_is_uncertain_and_never_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, _repository, requests = await _stack(
        tmp_path,
        monkeypatch,
        runtime_timeout=True,
    )
    certification_request_count = len(requests)
    qualification = gateway.qualification("skill_rerank")
    run = gateway.start_run(
        "skill_rerank", parent_run_reference="skill:managed-rerank:timeout"
    )

    with pytest.raises(ManagedRerankError) as raised:
        await run.rerank(
            "private query",
            ["private document"],
            model_id=qualification.model_id,
            top_n=1,
            logical_call_key="rerank:timeout",
            call_sequence=1,
            timeout_seconds=3,
        )

    runtime_posts = [
        request
        for request in requests[certification_request_count:]
        if request.method == "POST"
    ]
    assert raised.value.code == "provider_rerank_timeout"
    assert len(runtime_posts) == 1
    assert raised.value.receipt["status"] == "uncertain"
    assert raised.value.receipt["calls"][0]["dispatched"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_fallback_mode", "expect_fallback"),
    [("none", False), ("lexical", True)],
)
async def test_rag_input_budget_requires_explicit_lexical_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_fallback_mode: str,
    expect_fallback: bool,
) -> None:
    gateway, _repository, requests = await _stack(
        tmp_path,
        monkeypatch,
        local_fallback_mode=local_fallback_mode,
    )
    certification_request_count = len(requests)
    rag = RagService(
        storage_dir=tmp_path / "rag-budget",
        uploads_dir=tmp_path / "uploads-budget",
        managed_rerank_gateway=gateway,
        llm_enabled=False,
    )
    monkeypatch.setattr(
        rag.reranker,
        "prepare_managed_input",
        lambda *_args, **_kwargs: PreparedRerankInput(
            query="",
            documents=[],
            requested_input_count=1,
            input_char_count=0,
            candidate_limit=20,
            input_char_limit=1_000,
            timeout_seconds=3,
        ),
    )

    if expect_fallback:
        outcome = await rag._rerank_with_control(
            "kb-test",
            "namespace-test",
            "private query",
            [RerankDocument("a", "private document")],
            provider="auto",
            model="",
            top_n=1,
        )
        assert outcome.execution_mode == "local_non_model"
        assert "local_non_model_fallback" in outcome.fallback_reason_codes
    else:
        with pytest.raises(ManagedRagRerankRouteError) as raised:
            await rag._rerank_with_control(
                "kb-test",
                "namespace-test",
                "private query",
                [RerankDocument("a", "private document")],
                provider="auto",
                model="",
                top_n=1,
            )
        assert raised.value.code == "provider_rerank_input_budget_exhausted"

    assert not any(
        request.method == "POST"
        for request in requests[certification_request_count:]
    )
