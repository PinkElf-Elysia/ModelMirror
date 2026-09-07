from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.rag_embedding_gateway import (
    ManagedRagEmbeddingError,
    ManagedRagEmbeddingGateway,
)
from server.model_router.provider_operations import provider_operation_model_matches
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import SqliteLexicalStore
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    ManagedEmbeddingRouteError,
    PipelineJobStateError,
    PipelineVersionNotFoundError,
    RagRetrievalUnavailableError,
    RagService,
)
from server.rag.vector_store import LocalJsonVectorStore


@pytest.fixture(autouse=True)
def _managed_embedding_tests_never_use_ambient_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every provider dispatch pinned to the in-process MockTransport."""

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


def test_core_compose_passes_all_r7_feature_flags_to_server() -> None:
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    for flag in (
        "MODEL_CONTROL_RAG_QUERY_ENABLED",
        "MODEL_CONTROL_RAG_PROCESSOR_ENABLED",
        "MODEL_CONTROL_RAG_EMBEDDING_ENABLED",
        "MODEL_CONTROL_RAG_RERANK_ENABLED",
        "MODEL_CONTROL_SKILL_RERANK_ENABLED",
        "MODEL_CONTROL_OPENROUTER_BATCH_ENABLED",
    ):
        assert f"{flag}: ${{{flag}:-false}}" in compose


def test_runtime_embedding_model_match_keeps_openrouter_alias_narrow() -> None:
    assert provider_operation_model_matches(
        provider_kind="openrouter",
        requested_model="openai/text-embedding-3-small",
        actual_model="text-embedding-3-small",
    )
    assert not provider_operation_model_matches(
        provider_kind="openrouter",
        requested_model="openai/text-embedding-3-small",
        actual_model="text-embedding-3-large",
    )


def test_managed_embedding_gateway_loads_in_deployment_package_layout(
    tmp_path: Path,
) -> None:
    server_root = Path(__file__).resolve().parents[1]
    storage_dir = tmp_path / "router"
    script = "\n".join(
        (
            "from pathlib import Path",
            "from rag.embedder import EmbeddingClient",
            "from rag.rag_service import RagService",
            "from rag.vector_store import LocalJsonVectorStore",
            f"root = Path({str(tmp_path)!r})",
            "service = RagService(",
            "    storage_dir=root / 'rag',",
            "    uploads_dir=root / 'uploads',",
            "    embedder=EmbeddingClient(api_key=''),",
            "    vector_store=LocalJsonVectorStore(root / 'vectors.json'),",
            "    llm_enabled=False,",
            ")",
            "gateway = service._managed_embedding_gateway()",
            "print(type(gateway).__name__)",
        )
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(server_root),
            "MODEL_CONTROL_RAG_EMBEDDING_ENABLED": "true",
            "MODEL_ROUTER_STORAGE_DIR": str(storage_dir),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ManagedRagEmbeddingGateway"


async def _managed_embedding_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requests: list[Request],
    *,
    vector_dimension: int = 3,
) -> tuple[
    RagService,
    ManagedRagEmbeddingGateway,
    SQLiteRouterRepository,
    MockTransport,
    ModelRouterService,
    str,
    dict[str, object],
]:
    runtime_state: dict[str, object] = {
        "runtime_post_count": 0,
        "fail_on_runtime_post": None,
        "runtime_actual_model": "provider/embed-v1",
        "runtime_vector_dimension": vector_dimension,
    }

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/embed-v1"}]})
        payload = json.loads(request.content)
        inputs = payload.get("input")
        assert payload.get("model") == "provider/embed-v1"
        assert isinstance(inputs, list)
        is_certification = inputs == [
            "ModelMirror embedding certification one.",
            "ModelMirror embedding certification two.",
        ]
        if not is_certification:
            runtime_state["runtime_post_count"] = int(
                runtime_state["runtime_post_count"] or 0
            ) + 1
            if runtime_state["fail_on_runtime_post"] == runtime_state[
                "runtime_post_count"
            ]:
                return Response(503, json={"error": "synthetic failure body"})
        actual_model = (
            "provider/embed-v1"
            if is_certification
            else str(runtime_state["runtime_actual_model"])
        )
        response_dimension = (
            vector_dimension
            if is_certification
            else int(runtime_state["runtime_vector_dimension"])
        )
        return Response(
            200,
            json={
                "model": actual_model,
                "data": [
                    {
                        "index": index,
                        "embedding": [
                            float(position + 1)
                            if position != 1
                            else float((len(text) + index) % 7)
                            for position in range(response_dimension)
                        ],
                    }
                    for index, text in enumerate(inputs)
                ],
                "usage": {
                    "prompt_tokens": len(inputs),
                    "total_tokens": len(inputs),
                },
            },
        )

    transport = MockTransport(handler)
    repository = SQLiteRouterRepository(
        tmp_path / "router",
        master_key=b"r" * 32,
    )
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Managed Embedding",
            kind="openai_compatible",
            base_url="https://provider.example/v1",
            api_key="managed-embedding-secret",
            scopes=["embedding"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    certification = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="embedding_vectors",
            model_id="provider/embed-v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="managed-embedding-certification",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_RAG_EMBEDDING_ENABLED", "true")
    control = ProviderWorkloadControlService(router_service)
    saved = control.update_policy(
        "rag_embedding",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="embedding_vectors",
                    model_id="provider/embed-v1",
                    connection_id=connection.id,
                )
            ],
        ),
    )
    active = control.activate(
        "rag_embedding",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert active.effective_status == "managed_required"
    gateway = ManagedRagEmbeddingGateway.for_router(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    storage = tmp_path / "rag-storage"
    service = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        managed_embedding_gateway=gateway,
        llm_enabled=False,
    )
    return (
        service,
        gateway,
        repository,
        transport,
        router_service,
        connection.id,
        runtime_state,
    )


def test_embedding_space_fingerprint_excludes_credentials_and_pins_identity() -> None:
    baseline = ManagedRagEmbeddingGateway.space_identity(
        provider_kind="openai_compatible",
        endpoint="https://provider.example/v1/embeddings",
        model_id="provider/embed-v1",
        vector_dimension=1536,
    )
    same = ManagedRagEmbeddingGateway.space_identity(
        provider_kind="openai_compatible",
        endpoint="https://provider.example/v1/embeddings",
        model_id="provider/embed-v1",
        vector_dimension=1536,
    )
    endpoint_changed = ManagedRagEmbeddingGateway.space_identity(
        provider_kind="openai_compatible",
        endpoint="https://provider.example/v2/embeddings",
        model_id="provider/embed-v1",
        vector_dimension=1536,
    )
    model_changed = ManagedRagEmbeddingGateway.space_identity(
        provider_kind="openai_compatible",
        endpoint="https://provider.example/v1/embeddings",
        model_id="provider/embed-v2",
        vector_dimension=1536,
    )
    dimension_changed = ManagedRagEmbeddingGateway.space_identity(
        provider_kind="openai_compatible",
        endpoint="https://provider.example/v1/embeddings",
        model_id="provider/embed-v1",
        vector_dimension=3072,
    )

    assert same.fingerprint == baseline.fingerprint
    assert len(baseline.endpoint_identity_sha256) == 64
    assert len(
        {
            baseline.fingerprint,
            endpoint_changed.fingerprint,
            model_changed.fingerprint,
            dimension_changed.fingerprint,
        }
    ) == 4


@pytest.mark.asyncio
async def test_managed_embedding_build_and_query_use_one_pinned_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    (
        service,
        gateway,
        repository,
        _transport,
        router_service,
        connection_id,
        _runtime_state,
    ) = await _managed_embedding_stack(
        tmp_path,
        monkeypatch,
        requests,
    )
    kb = service.create_knowledge_base("managed embedding")
    document = await service.upload_document(
        kb["id"],
        "managed.txt",
        "MANAGED-SPACE-ANCHOR must remain bound to one embedding space.".encode(),
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "provider/embed-v1",
        },
        retrieval_profile={"mode": "vector", "top_k": 2},
    )
    assert draft["embedding_profile"]["effective"]["access_mode"] == "managed"
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded"
    version = service.get_pipeline_version(job["candidate_version_id"])
    assert len(version["embedding_space_fingerprint"]) == 64
    assert version["embedding_execution_mode"] == "managed"
    assert version["provider_route_receipts"]["call_count"] == 1
    assert version["provider_route_receipts"]["calls"][0]["operation"] == (
        "embedding_vectors"
    )
    assert version["provider_route_receipts"]["calls"][0][
        "provider_kind"
    ] == "openai_compatible"
    assert "connection_id" not in json.dumps(
        version["provider_route_receipts"], sort_keys=True
    )
    version_evidence = service.pipeline_version_evidence(version["version_id"])
    assert version_evidence["embedding"]["effective"][
        "embedding_space_fingerprint"
    ] == version["embedding_space_fingerprint"]

    result = await service.query_pipeline_version(
        version["version_id"],
        "MANAGED-SPACE-ANCHOR",
        retrieval={"mode": "vector", "top_k": 2},
        generate_answer=False,
    )

    assert result["sources"]
    assert result["execution_mode"] == "managed"
    assert result["provider_route_receipts"]["call_count"] == 1
    assert result["retrieval"]["embedding_space_fingerprint"] == (
        version["embedding_space_fingerprint"]
    )
    runtime_posts = [
        request
        for request in requests
        if request.method == "POST"
        and json.loads(request.content).get("input")
        not in [
            [
                "ModelMirror embedding certification one.",
                "ModelMirror embedding certification two.",
            ]
        ]
    ]
    assert len(runtime_posts) == 2
    assert all(request.url.host == "8.8.8.8" for request in runtime_posts)
    assert all(request.headers["host"] == "provider.example" for request in runtime_posts)
    assert all(
        request.extensions.get("sni_hostname") == "provider.example"
        for request in runtime_posts
    )
    assert b"managed-embedding-secret" not in repository.database_path.read_bytes()
    assert "MANAGED-SPACE-ANCHOR" not in repository.database_path.read_text(
        encoding="latin-1"
    )

    original_fingerprint = version["embedding_space_fingerprint"]
    await router_service.update_connection(
        connection_id,
        RouterConnectionUpdate(api_key="rotated-managed-embedding-secret"),
    )
    assert gateway.routing_mode() == "degraded_required"
    rotated_certification = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=_transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection_id,
        ProviderWorkloadCertificationRequest(
            execution_shape="embedding_vectors",
            model_id="provider/embed-v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="managed-embedding-key-rotation",
    )
    assert rotated_certification.status == "passed"
    control = ProviderWorkloadControlService(router_service)
    current = control.get_policy("rag_embedding")
    rotated = control.update_policy(
        "rag_embedding",
        ProviderWorkloadPolicyUpdate(
            expected_revision=current.revision,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="embedding_vectors",
                    model_id="provider/embed-v1",
                    connection_id=connection_id,
                )
            ],
        ),
    )
    control.activate(
        "rag_embedding",
        ProviderWorkloadActivationRequest(
            expected_revision=rotated.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert gateway.qualification("provider/embed-v1").fingerprint == (
        original_fingerprint
    )
    rotated_query = await service.query_pipeline_version(
        version["version_id"],
        "MANAGED-SPACE-ANCHOR",
        retrieval={"mode": "vector", "top_k": 2},
        generate_answer=False,
    )
    assert rotated_query["sources"]

    await router_service.update_connection(
        connection_id,
        RouterConnectionUpdate(base_url="https://provider.example/v2"),
    )
    endpoint_certification = await ProviderWorkloadCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=_transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection_id,
        ProviderWorkloadCertificationRequest(
            execution_shape="embedding_vectors",
            model_id="provider/embed-v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="managed-embedding-endpoint-change",
    )
    assert endpoint_certification.status == "passed"
    current = control.get_policy("rag_embedding")
    endpoint_changed = control.update_policy(
        "rag_embedding",
        ProviderWorkloadPolicyUpdate(
            expected_revision=current.revision,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="embedding_vectors",
                    model_id="provider/embed-v1",
                    connection_id=connection_id,
                )
            ],
        ),
    )
    control.activate(
        "rag_embedding",
        ProviderWorkloadActivationRequest(
            expected_revision=endpoint_changed.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    posts_before_drifted_query = sum(
        request.method == "POST" for request in requests
    )
    with pytest.raises(ManagedEmbeddingRouteError) as drifted:
        await service.query_pipeline_version(
            version["version_id"],
            "MANAGED-SPACE-ANCHOR",
            retrieval={"mode": "vector", "top_k": 2},
            generate_answer=False,
        )
    assert drifted.value.code == "provider_embedding_space_changed"
    assert sum(request.method == "POST" for request in requests) == (
        posts_before_drifted_query
    )
    with pytest.raises(RagRetrievalUnavailableError) as fulltext:
        await service.query_pipeline_version(
            version["version_id"],
            "MANAGED-SPACE-ANCHOR",
            retrieval={"mode": "fulltext", "top_k": 2},
            generate_answer=False,
        )
    assert fulltext.value.code == "rag_retrieval_mode_unavailable"
    assert sum(request.method == "POST" for request in requests) == (
        posts_before_drifted_query
    )


@pytest.mark.asyncio
async def test_high_dimension_managed_embedding_uses_response_bounded_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    (
        service,
        _gateway,
        _repository,
        _transport,
        _router_service,
        _connection_id,
        runtime_state,
    ) = await _managed_embedding_stack(
        tmp_path,
        monkeypatch,
        requests,
        vector_dimension=3072,
    )
    kb = service.create_knowledge_base("bounded embedding batches")
    document = await service.upload_document(
        kb["id"],
        "bounded.txt",
        b"Response-size bounds must be derived from certified dimensions.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "provider/embed-v1",
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    texts = [f"bounded response input {index}" for index in range(64)]

    vectors = await service.embed_managed_pipeline_chunks(job["job_id"], texts)

    assert len(vectors) == len(texts)
    assert all(len(vector) == 3072 for vector in vectors)
    assert int(runtime_state["runtime_post_count"] or 0) > 1
    runtime_batches = [
        json.loads(request.content)["input"]
        for request in requests
        if request.method == "POST"
        and json.loads(request.content).get("input")
        != [
            "ModelMirror embedding certification one.",
            "ModelMirror embedding certification two.",
        ]
    ]
    assert sum(len(batch) for batch in runtime_batches) == len(texts)
    assert max(len(batch) for batch in runtime_batches) < len(texts)


@pytest.mark.asyncio
async def test_managed_embedding_rejects_legacy_index_without_space_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    (
        service,
        _gateway,
        _repository,
        _transport,
        _router_service,
        _connection_id,
        runtime_state,
    ) = await _managed_embedding_stack(tmp_path, monkeypatch, requests)
    kb = service.create_knowledge_base("legacy embedding space")
    document = await service.upload_document(
        kb["id"],
        "legacy.txt",
        b"LEGACY-SPACE-ANCHOR requires an explicit managed rebuild.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "provider/embed-v1",
        },
        retrieval_profile={"mode": "vector", "top_k": 2},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    version_id = job["candidate_version_id"]
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        version = metadata["pipeline_versions"][version_id]
        version.pop("embedding_space_fingerprint", None)
        profile = version["embedding_profile"]
        profile.pop("embedding_space_fingerprint", None)
        profile["effective"].pop("access_mode", None)
        service._write_metadata_unlocked(metadata)
    posts_before_query = int(runtime_state["runtime_post_count"] or 0)

    with pytest.raises(ManagedEmbeddingRouteError) as blocked:
        await service.query_pipeline_version(
            version_id,
            "LEGACY-SPACE-ANCHOR",
            retrieval={"mode": "vector", "top_k": 2},
            generate_answer=False,
        )

    assert blocked.value.code == "provider_embedding_index_rebuild_required"
    assert int(runtime_state["runtime_post_count"] or 0) == posts_before_query
    with pytest.raises(RagRetrievalUnavailableError) as fulltext:
        await service.query_pipeline_version(
            version_id,
            "LEGACY-SPACE-ANCHOR",
            retrieval={"mode": "fulltext", "top_k": 2},
            generate_answer=False,
        )
    assert fulltext.value.code == "rag_retrieval_mode_unavailable"
    assert int(runtime_state["runtime_post_count"] or 0) == posts_before_query


@pytest.mark.asyncio
async def test_unexpected_post_dispatch_embedding_error_is_recorded_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    (
        _service,
        gateway,
        repository,
        _transport,
        _router_service,
        _connection_id,
        runtime_state,
    ) = await _managed_embedding_stack(tmp_path, monkeypatch, requests)
    run = gateway.start_query_run("unexpected-response-error")

    def fail_vector_parsing(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic response parser failure")

    monkeypatch.setattr(type(run), "_vectors", staticmethod(fail_vector_parsing))
    with pytest.raises(ManagedRagEmbeddingError) as failed:
        await run.embed(
            ["one bounded query"],
            model_id="provider/embed-v1",
            logical_call_key="embedding_query:0",
            call_sequence=1,
        )

    assert failed.value.code == "provider_embedding_internal_error"
    receipt = run.receipt_summary()
    assert receipt["status"] == "uncertain"
    assert receipt["calls"] == [
        {
            "call_sequence": 1,
            "operation": "embedding_vectors",
            "model_id": "provider/embed-v1",
            "provider_kind": "openai_compatible",
            "dispatched": True,
            "status": "uncertain",
            "actual_model": None,
            "error_code": "provider_embedding_internal_error",
            "e2e_ms": None,
            "prompt_tokens": None,
            "total_tokens": None,
        }
    ]
    assert int(runtime_state["runtime_post_count"] or 0) == 1
    stored = repository.get_workload_run("local", run.run_id)
    assert stored["status"] == "uncertain"


@pytest.mark.asyncio
async def test_uncertain_managed_embedding_job_fails_restart_and_same_job_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    (
        service,
        gateway,
        repository,
        transport,
        _router_service,
        _connection_id,
        _runtime_state,
    ) = (
        await _managed_embedding_stack(
        tmp_path,
        monkeypatch,
        requests,
        )
    )
    kb = service.create_knowledge_base("restart safety")
    document = await service.upload_document(
        kb["id"],
        "restart.txt",
        b"An uncertain embedding call must never be replayed.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "provider/embed-v1",
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert service.claim_next_pipeline_job() is not None
    run = gateway.start_index_run(job["job_id"])
    prepared = await gateway.call_service.prepare_call(
        run_id=run.run_id,
        entry_id="rag_embedding",
        execution_shape="embedding_vectors",
        model_id="provider/embed-v1",
        logical_call_key="batch:0",
        call_sequence=1,
    )
    gateway.call_service.mark_dispatched(prepared)

    restarted_repository = SQLiteRouterRepository(
        tmp_path / "router",
        master_key=b"r" * 32,
    )
    restarted_router = ModelRouterService(
        restarted_repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    restarted_gateway = ManagedRagEmbeddingGateway.for_router(
        restarted_router,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    storage = tmp_path / "rag-storage"
    restarted_service = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        managed_embedding_gateway=restarted_gateway,
        llm_enabled=False,
    )

    assert restarted_gateway.index_run_status(job["job_id"]) == "uncertain"
    assert restarted_service.recover_pipeline_jobs() == 1
    recovered = restarted_service.get_pipeline_job(job["job_id"])
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "provider_embedding_dispatch_uncertain"
    with pytest.raises(PipelineJobStateError, match="new pipeline job"):
        restarted_service.retry_pipeline_job(job["job_id"])
    assert len([request for request in requests if request.method == "POST"]) == 1
    assert repository.database_path == restarted_repository.database_path


@pytest.mark.asyncio
async def test_failed_managed_embedding_batch_is_not_retried_or_activated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    (
        service,
        _gateway,
        _repository,
        _transport,
        _router_service,
        _connection_id,
        runtime_state,
    ) = await _managed_embedding_stack(tmp_path, monkeypatch, requests)
    kb = service.create_knowledge_base("batch failure isolation")
    first_document = await service.upload_document(
        kb["id"],
        "active.txt",
        b"The currently active index must remain queryable.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "provider/embed-v1",
        },
        retrieval_profile={"mode": "vector"},
    )
    first_job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[first_document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    active = service.get_pipeline_version(first_job["candidate_version_id"])
    with service._metadata_lock:  # noqa: SLF001 - seed a previously active rollback target.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_active = metadata["pipeline_versions"][active["version_id"]]
        stored_active["status"] = "active"
        stored_active["activated_at"] = 1.0
        metadata["pipeline_active_versions"][kb["id"]] = active["version_id"]
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    second_document = await service.upload_document(
        kb["id"],
        "candidate.txt",
        ("FAILED-BATCH-MUST-NOT-REPLAY " * 80).encode(),
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {
            "stage_chunker": {
                "config": {
                    "strategy": "recursive_estimated_token",
                    "chunk_size": 100,
                    "chunk_overlap": 0,
                    "separators": [" "],
                }
            }
        },
    )
    second_job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[second_document["id"]],
    )
    monkeypatch.setenv("RAG_MANAGED_EMBEDDING_BATCH_SIZE", "1")
    runtime_state["fail_on_runtime_post"] = int(
        runtime_state["runtime_post_count"] or 0
    ) + 2

    assert await KnowledgePipelineExecutor(service).run_once() is True
    failed = service.get_pipeline_job(second_job["job_id"])
    assert failed["status"] == "failed"
    assert "provider_embedding_http_5xx" in str(failed["error"])
    assert service.get_active_pipeline_version(kb["id"])["version_id"] == (
        active["version_id"]
    )
    candidate_namespace = f"{kb['id']}::{second_job['candidate_version_id']}"
    assert service.vector_store.query(
        candidate_namespace,
        [1.0, 0.0, 0.25],
        1,
    ) == []
    assert service.lexical_store.count_namespace(
        candidate_namespace
    ) == 0
    assert runtime_state["runtime_post_count"] == 3


@pytest.mark.parametrize(
    ("runtime_key", "runtime_value", "expected_code"),
    [
        (
            "runtime_actual_model",
            "provider/embed-v2",
            "provider_embedding_model_mismatch",
        ),
        (
            "runtime_vector_dimension",
            4,
            "provider_embedding_dimension_changed",
        ),
    ],
)
@pytest.mark.asyncio
async def test_runtime_embedding_identity_drift_fails_candidate_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_key: str,
    runtime_value: object,
    expected_code: str,
) -> None:
    requests: list[Request] = []
    (
        service,
        _gateway,
        _repository,
        _transport,
        _router_service,
        _connection_id,
        runtime_state,
    ) = await _managed_embedding_stack(tmp_path, monkeypatch, requests)
    runtime_state[runtime_key] = runtime_value
    kb = service.create_knowledge_base("runtime identity drift")
    document = await service.upload_document(
        kb["id"],
        "drift.txt",
        b"Runtime identity drift must fail the candidate index.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "provider/embed-v1",
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True
    failed = service.get_pipeline_job(job["job_id"])
    assert failed["status"] == "failed"
    assert expected_code in str(failed["error"])
    assert runtime_state["runtime_post_count"] == 1
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(job["candidate_version_id"])
