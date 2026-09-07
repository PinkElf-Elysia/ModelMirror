from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.rag_generation_gateway import ManagedRagGenerationGateway
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
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import SqliteLexicalStore
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.processor_generator import ProcessorGenerationError
from server.rag.rag_service import (
    ManagedRagGenerationRouteError,
    PipelineDraftValidationError,
    PipelineJobStateError,
    RagService,
)
from server.rag.vector_store import LocalJsonVectorStore


MODEL_ID = "provider/rag-model"
PROVIDER_SECRET = "managed-rag-provider-secret"


@pytest.fixture(autouse=True)
def _managed_generation_tests_never_use_ambient_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep retrieval local and every generation dispatch on MockTransport."""

    for name in (
        "EMBEDDING_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "LLM_GATEWAY_KEY",
        "RAG_LLM_API_KEY",
        "RAG_RERANK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "hash")


def _profile(execution_shape: str) -> tuple[dict[str, object], str]:
    value: dict[str, object] = {
        "execution_shape": execution_shape,
        "model_id": MODEL_ID,
        "candidate_model_ids": [],
        "judge_model_id": None,
    }
    fingerprint = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return value, fingerprint


def _seed_certification(
    repository: SQLiteRouterRepository,
    *,
    connection_id: str,
    connection_fingerprint: str,
    execution_shape: str,
) -> None:
    profile, profile_fingerprint = _profile(execution_shape)
    certification, created = repository.claim_workload_certification(
        "local",
        certification_id=f"rag-{execution_shape}-cert",
        connection_id=connection_id,
        connection_fingerprint=connection_fingerprint,
        contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
        execution_shape=execution_shape,
        requested_model=MODEL_ID,
        profile=profile,
        profile_fingerprint=profile_fingerprint,
        idempotency_key_hash=hashlib.sha256(
            f"rag-{execution_shape}".encode("utf-8")
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
            "json_object_verified": execution_shape == "chat_json_object",
        },
        warning_codes=[],
        actual_model=MODEL_ID,
    )


def _activate(
    service: ModelRouterService,
    connection_id: str,
    *,
    entry_id: str,
    execution_shape: str,
    local_fallback_mode: str = "none",
) -> None:
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            local_fallback_mode=local_fallback_mode,  # type: ignore[arg-type]
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=execution_shape,  # type: ignore[arg-type]
                    model_id=MODEL_ID,
                    connection_id=connection_id,
                )
            ],
        ),
    )
    active = control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert active.effective_status == "managed_required"


def _managed_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    query_fallback: str = "none",
) -> tuple[RagService, SQLiteRouterRepository]:
    repository = SQLiteRouterRepository(
        tmp_path / "router",
        master_key=b"r" * 32,
    )
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Managed RAG",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key=PROVIDER_SECRET,
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-26T00:00:00+00:00",
    )
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    refresh_id = f"refresh-{connection.id}"
    repository.claim_catalog_refresh(
        "local",
        refresh_id=refresh_id,
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        refresh_id,
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
        catalog_fingerprint="rag-catalog",
        observed_at="2026-08-26T00:00:00+00:00",
    )
    for execution_shape in ("chat_text_unary", "chat_json_object"):
        _seed_certification(
            repository,
            connection_id=connection.id,
            connection_fingerprint=fingerprint,
            execution_shape=execution_shape,
        )

    router_service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    monkeypatch.setenv("MODEL_CONTROL_RAG_QUERY_ENABLED", "true")
    monkeypatch.setenv("MODEL_CONTROL_RAG_PROCESSOR_ENABLED", "true")
    _activate(
        router_service,
        connection.id,
        entry_id="rag_query_generate",
        execution_shape="chat_text_unary",
        local_fallback_mode=query_fallback,
    )
    _activate(
        router_service,
        connection.id,
        entry_id="rag_processor_generate",
        execution_shape="chat_json_object",
    )
    gateway = ManagedRagGenerationGateway.for_router(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    storage = tmp_path / "rag-storage"
    return (
        RagService(
            storage_dir=storage,
            uploads_dir=tmp_path / "rag-uploads",
            embedder=EmbeddingClient(api_key="", dimension=64),
            vector_store=LocalJsonVectorStore(storage / "vectors.json"),
            lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
            managed_generation_gateway=gateway,
            llm_enabled=False,
        ),
        repository,
    )


async def _indexed_service(
    service: RagService,
    *,
    text: str = "PRIVATE-RAG-QUESTION-CONTEXT belongs only in the RAG index.",
) -> tuple[str, str, str]:
    kb = service.create_knowledge_base("managed query")
    document = await service.upload_document(
        kb["id"],
        "managed.txt",
        text.encode("utf-8"),
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector", "top_k": 1},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    assert service.get_pipeline_job(job["job_id"])["status"] == "succeeded"
    return kb["id"], document["id"], job["candidate_version_id"]


@pytest.mark.asyncio
async def test_managed_rag_query_uses_one_pinned_post_and_redacted_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [{"message": {"content": "MANAGED-RAG-ANSWER"}}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            },
        )

    service, repository = _managed_stack(tmp_path, monkeypatch, handler)
    _kb_id, _, version_id = await _indexed_service(service)
    result = await service.query_pipeline_version(
        version_id,
        "PRIVATE-RAG-QUESTION",
        top_k=1,
        retrieval={"mode": "vector"},
    )

    assert result["answer"] == "MANAGED-RAG-ANSWER"
    assert result["execution_mode"] == "managed"
    assert result["provider_route_receipts"]["entry_id"] == "rag_query_generate"
    assert result["provider_route_receipts"]["call_count"] == 1
    assert len(requests) == 1
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].headers["host"] == "provider.example"
    assert requests[0].extensions["sni_hostname"] == "provider.example"
    database = repository.database_path.read_bytes()
    assert b"PRIVATE-RAG-QUESTION" not in database
    assert b"PRIVATE-RAG-QUESTION-CONTEXT" not in database
    assert b"MANAGED-RAG-ANSWER" not in database
    assert PROVIDER_SECRET.encode() not in database


@pytest.mark.asyncio
async def test_managed_rag_query_fails_closed_after_one_post_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"error": "private upstream body"})

    service, _ = _managed_stack(tmp_path, monkeypatch, handler)
    _kb_id, _, version_id = await _indexed_service(service)

    with pytest.raises(ManagedRagGenerationRouteError) as caught:
        await service.query_pipeline_version(
            version_id,
            "PRIVATE-RAG-QUESTION",
            top_k=1,
            retrieval={"mode": "vector"},
        )

    assert len(requests) == 1
    assert caught.value.receipt["call_count"] == 1
    assert caught.value.receipt["calls"][0]["dispatched"] is True


@pytest.mark.asyncio
async def test_managed_rag_query_explicit_extractive_fallback_is_not_model_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"error": "private upstream body"})

    service, _ = _managed_stack(
        tmp_path,
        monkeypatch,
        handler,
        query_fallback="extractive",
    )
    _kb_id, _, version_id = await _indexed_service(service)
    result = await service.query_pipeline_version(
        version_id,
        "PRIVATE-RAG-QUESTION",
        top_k=1,
        retrieval={"mode": "vector"},
    )

    assert len(requests) == 1
    assert result["execution_mode"] == "local_non_model"
    assert result["answer"].startswith("根据知识库资料：")
    assert "local_non_model_fallback" in result["fallback_reason_codes"]
    assert result["provider_route_receipts"]["status"] == "failed"


@pytest.mark.asyncio
async def test_managed_rag_query_cancellation_closes_the_parent_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise asyncio.CancelledError

    service, repository = _managed_stack(tmp_path, monkeypatch, handler)
    _kb_id, _, version_id = await _indexed_service(service)

    with pytest.raises(asyncio.CancelledError):
        await service.query_pipeline_version(
            version_id,
            "PRIVATE-RAG-QUESTION",
            top_k=1,
            retrieval={"mode": "vector"},
        )

    evidence = repository.list_workload_receipts(
        "local",
        entry_id="rag_query_generate",
    )
    assert len(requests) == 1
    assert evidence["runs"][0]["status"] == "cancelled"
    assert evidence["calls"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_processor_continue_on_error_never_replays_failed_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        source = json.loads(payload["messages"][1]["content"])
        title = str(source["title"])
        block_id = str(source["blocks"][0]["block_id"])
        if "bad" in title.casefold():
            return httpx.Response(503, json={"error": "private processor body"})
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "question": "What is the code?",
                                            "answer": "GOOD-42",
                                            "block_ids": [block_id],
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
            },
        )

    service, repository = _managed_stack(tmp_path, monkeypatch, handler)
    kb = service.create_knowledge_base("managed processor")
    bad = await service.upload_document(
        kb["id"], "bad.txt", b"BAD-PRIVATE-DOC", pipeline_only=True
    )
    good = await service.upload_document(
        kb["id"], "good.txt", b"GOOD-PRIVATE-DOC", pipeline_only=True
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {
            "stage_processor": {
                "mode": "qa",
                "model_id": MODEL_ID,
                "failure_policy": "continue_on_error",
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[bad["id"], good["id"]],
    )

    completed = await service.process_pipeline_job_sources(job["job_id"])
    results = service.get_pipeline_job(job["job_id"])["document_results"]

    assert len(requests) == 2
    assert len(completed) == 1
    assert [item["status"] for item in results] == ["failed", "completed"]
    assert all(item["execution_mode"] == "managed" for item in results)
    assert results[0]["provider_route_receipts"]["call_count"] == 1
    assert results[1]["provider_route_receipts"]["call_count"] == 1
    database = repository.database_path.read_bytes()
    assert b"BAD-PRIVATE-DOC" not in database
    assert b"GOOD-PRIVATE-DOC" not in database
    assert b"GOOD-42" not in database

    service.fail_pipeline_job(job["job_id"], "synthetic job failure")
    with pytest.raises(PipelineJobStateError, match="Managed processor evidence"):
        service.retry_pipeline_job(job["job_id"])
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_managed_processor_exact_draft_model_mismatch_blocks_before_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    service, _ = _managed_stack(
        tmp_path,
        monkeypatch,
        lambda request: (
            requests.append(request)
            or httpx.Response(200, json={})
        ),
    )
    kb_id, document_id, _version_id = await _indexed_service(service)

    with pytest.raises(ProcessorGenerationError) as caught:
        await service.preview_pipeline_processor(
            kb_id,
            document_id,
            {
                "mode": "qa",
                "model_id": "provider/not-bound",
            },
        )

    assert caught.value.code == "provider_workload_binding_missing"
    assert requests == []


@pytest.mark.asyncio
async def test_managed_processor_empty_draft_model_is_rejected_before_control_plane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    service, _ = _managed_stack(
        tmp_path,
        monkeypatch,
        lambda request: (
            requests.append(request)
            or httpx.Response(200, json={})
        ),
    )
    kb_id, document_id, _version_id = await _indexed_service(service)

    with pytest.raises(PipelineDraftValidationError, match="model_id is invalid"):
        await service.preview_pipeline_processor(
            kb_id,
            document_id,
            {
                "mode": "qa",
                "model_id": "",
            },
        )

    assert requests == []
