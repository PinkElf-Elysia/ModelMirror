from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from openpyxl import Workbook

from server.main import app
from server.rag.api import (
    set_pipeline_executor_for_tests,
    set_rag_service_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor, _source_block_hash
from server.rag.processor_generator import GeneratedIndexItem, GeneratedSourceRange
from server.rag.rag_service import (
    KnowledgeWriteProposalConflictError,
    PipelineJobStateError,
    PipelineVersionNotFoundError,
    RagService,
)
from server.rag.vector_store import LocalJsonVectorStore
from server.xpert_runtime.run_registry import RunRegistry
from server.xperts.api import set_xpert_context_store_for_tests
from server.xperts.context import XpertContextStore


@pytest_asyncio.fixture
async def pipeline_runtime(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "rag-storage" / "vectors.json"),
        llm_enabled=False,
    )
    registry = RunRegistry()
    executor = KnowledgePipelineExecutor(service, run_registry=registry, poll_interval=0.01)
    context_store = XpertContextStore(tmp_path / "runtime-storage")
    set_rag_service_for_tests(service)
    set_pipeline_executor_for_tests(executor)
    set_xpert_context_store_for_tests(context_store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service, executor, registry, context_store
    set_pipeline_executor_for_tests(None)
    set_rag_service_for_tests(None)
    set_xpert_context_store_for_tests(None)


async def create_kb(client: httpx.AsyncClient, name: str = "versioned") -> str:
    response = await client.post("/api/rag/knowledge_bases", json={"name": name})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


async def upload_text(client: httpx.AsyncClient, kb_id: str, filename: str, text: str) -> str:
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _pipeline_xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "销售数据"
    sheet.append(["城市", "数量"])
    sheet.append(["上海", 42])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


async def execute_current_draft(
    client: httpx.AsyncClient,
    executor: KnowledgePipelineExecutor,
    kb_id: str,
    *,
    source_document_ids: list[str] | None = None,
    xpert_file_refs: list[dict[str, str]] | None = None,
) -> dict:
    draft = (await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    if str((draft.get("retrieval_profile") or {}).get("mode") or "") != "vector":
        configured = await client.patch(
            f"/api/rag/pipeline/draft/{kb_id}",
            json={"retrieval_profile": {"mode": "vector"}},
        )
        assert configured.status_code == 200, configured.text
        draft = configured.json()
    response = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": draft["version"],
            "source_document_ids": source_document_ids,
            "xpert_file_refs": xpert_file_refs or [],
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["status"] == "queued"
    assert await executor.run_once() is True
    completed = (await client.get(f"/api/rag/pipeline/jobs/{created['job_id']}")).json()
    assert completed["status"] == "succeeded"
    return completed


def _mark_pipeline_version_as_previously_active(
    service: RagService,
    version_id: str,
    *,
    promotion_required: bool = False,
) -> None:
    """Model a historical active index without authorizing a new legacy activation."""

    with service._metadata_lock:  # noqa: SLF001 - historical compatibility fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        kb_id = str(version["kb_id"])
        previous_id = metadata["pipeline_active_versions"].get(kb_id)
        if previous_id and previous_id in metadata["pipeline_versions"]:
            metadata["pipeline_versions"][previous_id]["status"] = "ready"
        version["status"] = "active"
        version["activated_at"] = 1.0
        version["promotion_required"] = promotion_required
        metadata["pipeline_active_versions"][kb_id] = version_id
        service._write_metadata_unlocked(metadata)  # noqa: SLF001


def test_source_block_hash_preserves_exact_corpus_text() -> None:
    text = "  stable evidence block\n"
    canonical = json.dumps(
        text,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert _source_block_hash(text) == hashlib.sha256(canonical).hexdigest()
    assert _source_block_hash(text) != hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert _source_block_hash(" \n\t") is None


@pytest.mark.asyncio
async def test_pipeline_job_and_version_preserve_replayable_chunking_receipt(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "chunk receipt")
    document_id = await upload_text(
        client,
        kb_id,
        "receipt.txt",
        "Deterministic chunk receipt evidence. " * 90,
    )

    first = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    second = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )

    for completed in (first, second):
        contract = completed["content_index_contract"]
        receipt = completed["chunking_receipt"]
        assert contract["contract_version"] == "rag-content-index-contract-v1"
        assert contract["components"]["chunker"] == "current"
        assert receipt["contract_version"] == "rag-chunker-estimated-token-v1"
        assert receipt["size_unit"] == "estimated_tokens"
        assert receipt["token_estimator"] == "mixed_cjk_latin_v1"
        assert receipt["raw_candidate_count"] >= receipt["final_chunk_count"] > 0
        assert len(receipt["chunk_sequence_hash"]) == 64

        evidence = service.pipeline_version_evidence(
            str(completed["candidate_version_id"])
        )
        assert evidence["content_index_contract"] == contract
        assert evidence["chunking_receipt"] == receipt
        assert evidence["chunking_receipt_status"] == "current"
        assert evidence["chunker"]["fingerprint"] == receipt[
            "chunker_profile_fingerprint"
        ]
        assert evidence["index_owner_version_id"] == receipt[
            "candidate_version_id"
        ]
        assert evidence["candidate_namespace_fingerprint"] == receipt[
            "candidate_namespace_fingerprint"
        ]
        assert evidence["chunking_receipt_fingerprint"] == hashlib.sha256(
            json.dumps(
                receipt,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    assert (
        first["chunking_receipt"]["chunk_sequence_hash"]
        == second["chunking_receipt"]["chunk_sequence_hash"]
    )

    version_id = str(first["candidate_version_id"])
    before = service.pipeline_version_evidence(version_id)
    with service._metadata_lock:  # noqa: SLF001 - one-sided receipt tamper fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][version_id]["chunking_receipt"][
            "chunk_sequence_hash"
        ] = "9" * 64
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    receipt_tampered = service.pipeline_version_evidence(version_id)

    assert receipt_tampered["configuration_fingerprint"] == before[
        "configuration_fingerprint"
    ]
    assert receipt_tampered["chunking_receipt_status"] == "mismatch"
    assert receipt_tampered["chunking_receipt_fingerprint"] != before[
        "chunking_receipt_fingerprint"
    ]
    assert receipt_tampered["version_fingerprint"] != before["version_fingerprint"]
    with pytest.raises(PipelineJobStateError, match="chunking receipt"):
        service.pipeline_corpus_snapshot(version_id)

    with service._metadata_lock:  # noqa: SLF001 - restore before independent tamper.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        job_id = str(metadata["pipeline_versions"][version_id]["job_id"])
        metadata["pipeline_versions"][version_id]["chunking_receipt"] = json.loads(
            json.dumps(metadata["pipeline_jobs"][job_id]["chunking_receipt"])
        )
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    before = service.pipeline_version_evidence(version_id)
    with service._metadata_lock:  # noqa: SLF001 - persisted identity tamper fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][version_id]["config_snapshot"]["stages"][
            "stage_chunker"
        ]["chunk_size"] += 1
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    after = service.pipeline_version_evidence(version_id)

    assert after["chunker"]["fingerprint"] != before["chunker"]["fingerprint"]
    assert after["configuration_fingerprint"] != before["configuration_fingerprint"]
    assert after["version_fingerprint"] != before["version_fingerprint"]


@pytest.mark.asyncio
async def test_queued_legacy_chunker_job_fails_before_executor_dispatch(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "queued legacy chunker")
    document_id = await upload_text(
        client,
        kb_id,
        "legacy.txt",
        "Queued legacy content must never be reinterpreted by the token executor.",
    )
    configured = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "vector"}},
    )
    assert configured.status_code == 200, configured.text
    draft = (await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    response = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": draft["version"],
            "source_document_ids": [document_id],
            "xpert_file_refs": [],
        },
    )
    assert response.status_code == 200, response.text
    job_id = str(response.json()["job_id"])

    with service._metadata_lock:  # noqa: SLF001 - pre-4A queued job fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        job = metadata["pipeline_jobs"][job_id]
        chunker = job["config_snapshot"]["stages"]["stage_chunker"]
        chunker["strategy"] = "recursive_character"
        chunker["size_unit"] = "characters"
        chunker["token_estimator"] = None
        chunker["chunk_contract_version"] = "rag-chunker-character-v1"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert await executor.run_once() is False
    failed = service.get_pipeline_job(job_id)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "rag_content_contract_legacy_read_only"
    assert failed["attempt"] == 0
    assert failed["chunking_receipt"] == {}
    assert all(result["status"] == "pending" for result in failed["document_results"])
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(str(failed["candidate_version_id"]))


@pytest.mark.asyncio
async def test_aggregate_legacy_candidate_activation_api_fails_closed(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "4a diagnostic candidate")
    document_id = await upload_text(
        client,
        kb_id,
        "diagnostic.txt",
        "The 4A candidate remains diagnostic until all content contracts are current.",
    )
    job = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    version_id = str(job["candidate_version_id"])

    response = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/activate"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "rag_content_contract_legacy_read_only"
    )
    stored = service.get_pipeline_version(version_id)
    assert stored["status"] == "ready"
    assert stored.get("activated_at") is None


@pytest.mark.asyncio
async def test_pipeline_corpus_snapshot_binds_artifact_and_index(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "corpus identity")
    document_id = await upload_text(
        client,
        kb_id,
        "identity.txt",
        "Immutable corpus evidence binds this exact source block.",
    )
    job = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    version_id = str(job["candidate_version_id"])

    snapshot = service.pipeline_corpus_snapshot(version_id)
    assert snapshot["contract_version"] == "rag-corpus-snapshot-v1"
    assert len(snapshot["documents"]) == 1
    assert len(snapshot["documents"][0]["source_blocks"]) == 1
    assert len(snapshot["checksum"]) == 64
    assert service.pipeline_corpus_snapshot(version_id) == snapshot

    stored_job = service.get_pipeline_job(str(job["job_id"]))
    artifact_path = service._pipeline_processed_path(  # noqa: SLF001
        stored_job["document_results"][0]["artifact_key"]
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["processed_document"]["blocks"][0]["text"] = "tampered"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(PipelineJobStateError, match="source-block index is inconsistent"):
        service.pipeline_corpus_snapshot(version_id)


@pytest.mark.asyncio
async def test_multi_block_generated_parent_replays_corpus_lineage_fail_closed(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime

    class MultiBlockGenerator:
        @staticmethod
        def capabilities() -> dict:
            return {
                "llm_configured": True,
                "model": "strict-fake-generator",
                "targets": ["strict_fake"],
            }

        @staticmethod
        def default_model() -> str:
            return "strict-fake-generator"

        @staticmethod
        async def generate(document, **_kwargs):
            blocks = [
                block for block in document.blocks if block.kind != "heading"
            ]
            assert len(blocks) >= 2
            separator = "\n\n"
            first_context_end = len(blocks[0].text)
            second_context_start = first_context_end + len(separator)
            return [
                GeneratedIndexItem(
                    item_id="summary_0",
                    item_type="summary",
                    index_text="Combined release evidence",
                    context_text=(
                        f"{blocks[0].text}{separator}{blocks[1].text}"
                    ),
                    source_block_ids=[blocks[0].block_id, blocks[1].block_id],
                    context_source_ranges=[
                        GeneratedSourceRange(
                            source_block_id=blocks[0].block_id,
                            context_start=0,
                            context_end=first_context_end,
                            source_start=blocks[0].start_char,
                            source_end=blocks[0].end_char,
                        ),
                        GeneratedSourceRange(
                            source_block_id=blocks[1].block_id,
                            context_start=second_context_start,
                            context_end=second_context_start + len(blocks[1].text),
                            source_start=blocks[1].start_char,
                            source_end=blocks[1].end_char,
                        ),
                    ],
                )
            ]

    service.processor_generator = MultiBlockGenerator()  # type: ignore[assignment]
    kb_id = await create_kb(client, "generated corpus identity")
    document_id = await upload_text(
        client,
        kb_id,
        "generated.md",
        "# First\nAlpha canonical evidence.\n\n# Second\nBeta canonical evidence.",
    )
    draft = service.get_pipeline_draft(kb_id)
    updated = service.update_pipeline_draft(
        kb_id,
        {
            "stage_processor": {
                "config": {
                    "mode": "summary",
                    "model_id": "strict-fake-generator",
                }
            }
        },
    )
    assert updated["version"] > draft["version"]

    job = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    version_id = str(job["candidate_version_id"])
    snapshot = service.pipeline_corpus_snapshot(version_id)
    evidence = service.pipeline_corpus_evidence(version_id)

    assert len(snapshot["documents"][0]["source_blocks"]) == 4
    assert len(evidence["documents"][0]["source_blocks"]) == 2
    stored_chunks = service.vector_store.list_document_chunks(
        f"{version_id}_{document_id}"
    )
    generated_chunks = [chunk for chunk in stored_chunks if chunk.generated_item]
    assert generated_chunks
    assert all(len(chunk.source_block_ids) == 2 for chunk in generated_chunks)
    online = await service.query_pipeline_version(
        version_id,
        generated_chunks[0].text,
        top_k=10,
        retrieval={"mode": "vector", "top_k": 10},
        generate_answer=False,
    )
    online_generated = next(
        source
        for source in online["sources"]
        if source["chunk_id"] == generated_chunks[0].chunk_id
    )
    assert online_generated["source_block_ids"] == list(
        generated_chunks[0].source_block_ids
    )
    assert online_generated["generated_item"] is True
    assert online_generated["source_block_id"] is None
    assert online_generated["source_block_match_status"] == "ambiguous_multi_source"
    stored_job = service.get_pipeline_job(str(job["job_id"]))
    artifact_path = service._pipeline_processed_path(  # noqa: SLF001
        stored_job["document_results"][0]["artifact_key"]
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["generated_items"][0]["source_block_ids"].reverse()
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(PipelineJobStateError):
        service.pipeline_corpus_snapshot(version_id)


def test_knowledge_write_proposal_persists_deduplicates_and_checks_revision(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "rag-storage"
    uploads = tmp_path / "rag-uploads"
    service = RagService(
        storage_dir=storage,
        uploads_dir=uploads,
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("proposal persistence")
    created = service.create_knowledge_write_proposal(
        kb_id=kb["id"],
        title="Release note",
        content="The approved release process requires an evaluation gate.",
        tags=["release", "quality"],
        source_xpert_id="xpert-writer",
        source_run_id="run-1",
    )
    duplicate = service.create_knowledge_write_proposal(
        kb_id=kb["id"],
        title="Duplicate title is ignored",
        content="The approved release process requires an evaluation gate.",
        tags=[],
        source_xpert_id="xpert-writer",
        source_run_id="run-1",
    )
    assert duplicate["proposal_id"] == created["proposal_id"]

    reloaded = RagService(
        storage_dir=storage,
        uploads_dir=uploads,
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )
    persisted = reloaded.get_knowledge_write_proposal(created["proposal_id"])
    assert persisted["status"] == "pending"
    assert persisted["content"] == created["content"]
    updated = reloaded.update_knowledge_write_proposal(
        created["proposal_id"],
        expected_revision=created["revision"],
        title="Reviewed release note",
    )
    assert updated["revision"] == created["revision"] + 1
    with pytest.raises(KnowledgeWriteProposalConflictError):
        reloaded.update_knowledge_write_proposal(
            created["proposal_id"],
            expected_revision=created["revision"],
            title="Stale update",
        )


def test_workflow_knowledge_proposal_scopes_occurrence_and_pending_deduplication(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "rag-storage"
    service = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("workflow proposals")
    common = {
        "kb_id": kb["id"],
        "title": "Stable title",
        "content": "A deterministic workflow proposal.",
        "source_scope_kind": "workflow_project",
        "source_scope_id": "wf_alpha",
        "source_node_id": "proposal-node",
    }

    created = service.create_knowledge_write_proposal(
        **common,
        source_occurrence_key="execution-1:proposal-node",
    )
    same_pending_content = service.create_knowledge_write_proposal(
        **{**common, "title": "Ignored replacement title"},
        source_occurrence_key="execution-2:proposal-node",
    )
    assert same_pending_content["proposal_id"] == created["proposal_id"]
    assert same_pending_content["reused"] is True
    assert same_pending_content["title"] == "Stable title"

    reloaded = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )
    recovered_after_create = reloaded.create_knowledge_write_proposal(
        **{**common, "title": "Must not replace the persisted title"},
        source_occurrence_key="execution-1:proposal-node",
    )
    assert recovered_after_create["proposal_id"] == created["proposal_id"]
    assert recovered_after_create["reused"] is True
    assert len(reloaded.list_knowledge_write_proposals(kb_id=kb["id"])) == 1

    rejected = reloaded.reject_knowledge_write_proposal(
        created["proposal_id"],
        expected_revision=created["revision"],
    )
    exact_replay = reloaded.create_knowledge_write_proposal(
        **{**common, "content": "Changed after the first attempt."},
        source_occurrence_key="execution-1:proposal-node",
    )
    assert exact_replay["proposal_id"] == created["proposal_id"]
    assert exact_replay["status"] == "rejected"
    assert exact_replay["reused"] is True

    after_terminal = reloaded.create_knowledge_write_proposal(
        **common,
        source_occurrence_key="execution-3:proposal-node",
    )
    assert after_terminal["proposal_id"] != rejected["proposal_id"]
    assert after_terminal["status"] == "pending"
    assert after_terminal["reused"] is False

    other_node = reloaded.create_knowledge_write_proposal(
        **{**common, "source_node_id": "other-node"},
        source_occurrence_key="execution-4:other-node",
    )
    other_project = reloaded.create_knowledge_write_proposal(
        **{**common, "source_scope_id": "wf_beta"},
        source_occurrence_key="execution-5:proposal-node",
    )
    assert other_node["proposal_id"] != after_terminal["proposal_id"]
    assert other_project["proposal_id"] != after_terminal["proposal_id"]
    assert "source_occurrence_hash" not in after_terminal
    assert "pending_dedupe_hash" not in after_terminal


def test_workflow_knowledge_proposal_rehashes_after_inbox_content_edit(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "rag-storage"
    service = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("editable proposals")
    created = service.create_knowledge_write_proposal(
        kb["id"],
        title="Before edit",
        content="Original body",
        source_scope_kind="workflow_project",
        source_scope_id="wf_alpha",
        source_node_id="proposal-node",
        source_occurrence_key="execution-1:proposal-node",
    )
    updated = service.update_knowledge_write_proposal(
        created["proposal_id"],
        expected_revision=created["revision"],
        content="Edited body",
    )
    reused = service.create_knowledge_write_proposal(
        kb["id"],
        title="Must not overwrite the edited proposal",
        content="Edited body",
        source_scope_kind="workflow_project",
        source_scope_id="wf_alpha",
        source_node_id="proposal-node",
        source_occurrence_key="execution-2:proposal-node",
    )
    assert reused["proposal_id"] == updated["proposal_id"]
    assert reused["content"] == "Edited body"
    assert reused["reused"] is True


def test_workflow_knowledge_proposal_concurrent_pending_deduplication(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "rag-storage"
    service = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("concurrent proposals")

    def create(index: int) -> dict:
        return service.create_knowledge_write_proposal(
            kb["id"],
            title=f"Concurrent title {index}",
            content="The same deterministic body.",
            source_scope_kind="workflow_project",
            source_scope_id="wf_concurrent",
            source_node_id="proposal-node",
            source_occurrence_key=f"execution-{index}:proposal-node",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create, range(16)))

    assert len({item["proposal_id"] for item in results}) == 1
    assert len(service.list_knowledge_write_proposals(kb_id=kb["id"])) == 1


@pytest.mark.asyncio
async def test_legacy_candidate_stays_diagnostic_and_historical_versions_support_rollback(
    pipeline_runtime,
) -> None:
    client, service, executor, registry, _ = pipeline_runtime
    kb_id = await create_kb(client)
    alpha_id = await upload_text(
        client,
        kb_id,
        "alpha.txt",
        "Alpha release policy uses manual approval before deployment.",
    )

    first_job = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[alpha_id],
    )
    versions = (await client.get(f"/api/rag/pipeline/versions?kb_id={kb_id}")).json()
    first_version = versions["versions"][0]
    assert first_version["status"] == "ready"
    assert first_version["active"] is False
    assert service.get_active_pipeline_version(kb_id) is None

    activate = await client.post(
        f"/api/rag/pipeline/versions/{first_version['version_id']}/activate"
    )
    assert activate.status_code == 409, activate.text
    assert activate.json()["detail"]["code"] == (
        "rag_content_contract_legacy_read_only"
    )
    for label, invalid_timestamp in (
        ("missing", None),
        ("none", None),
        ("zero", 0),
        ("false", False),
        ("empty", ""),
        ("nan", float("nan")),
        ("negative", -1.0),
    ):
        with service._metadata_lock:  # noqa: SLF001 - corrupted legacy metadata fixture.
            metadata = service._read_metadata_unlocked()  # noqa: SLF001
            stored = metadata["pipeline_versions"][first_version["version_id"]]
            stored["status"] = "ready"
            if label == "missing":
                stored.pop("activated_at", None)
            else:
                stored["activated_at"] = invalid_timestamp
            metadata["pipeline_active_versions"].pop(kb_id, None)
            service._write_metadata_unlocked(metadata)  # noqa: SLF001
        malformed = await client.post(
            f"/api/rag/pipeline/versions/{first_version['version_id']}/activate"
        )
        assert malformed.status_code == 409, (label, malformed.text)
    _mark_pipeline_version_as_previously_active(
        service,
        str(first_version["version_id"]),
        promotion_required=True,
    )
    assert service.get_active_pipeline_version(kb_id)["version_id"] == (
        first_version["version_id"]
    )

    beta_id = await upload_text(
        client,
        kb_id,
        "beta.txt",
        "Beta architecture introduces a versioned index candidate.",
    )
    active_before = await service.query(kb_id, "Beta architecture", top_k=5)
    assert {item["document_name"] for item in active_before["sources"]} == {"alpha.txt"}

    second_job = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[alpha_id, beta_id],
    )
    second_version_id = str(second_job["candidate_version_id"])
    preview = await client.post(
        f"/api/rag/pipeline/versions/{second_version_id}/query",
        json={"question": "Beta architecture", "top_k": 5},
    )
    assert preview.status_code == 200, preview.text
    assert "beta.txt" in {item["document_name"] for item in preview.json()["sources"]}

    active_still_first = service.get_active_pipeline_version(kb_id)
    assert active_still_first is not None
    assert active_still_first["version_id"] == first_version["version_id"]

    blocked_second = await client.post(
        f"/api/rag/pipeline/versions/{second_version_id}/activate"
    )
    assert blocked_second.status_code == 409, blocked_second.text
    active_after = await service.query(kb_id, "Beta architecture", top_k=5)
    assert {item["document_name"] for item in active_after["sources"]} == {
        "alpha.txt"
    }

    _mark_pipeline_version_as_previously_active(service, second_version_id)
    assert service.get_active_pipeline_version(kb_id)["version_id"] == (
        second_version_id
    )

    rollback = await client.post(
        f"/api/rag/pipeline/versions/{first_version['version_id']}/activate"
    )
    assert rollback.status_code == 200
    rolled_back = await service.query(kb_id, "Beta architecture", top_k=5)
    assert {item["document_name"] for item in rolled_back["sources"]} == {"alpha.txt"}

    runs = await registry.list_runs(run_type="knowledge_pipeline")
    assert len(runs) == 2
    first_checkpoints = await registry.list_checkpoints(runs[-1].run_id, limit=100)
    assert {item.event_type for item in first_checkpoints} >= {
        "knowledge_pipeline.started",
        "knowledge_pipeline.version_ready",
        "knowledge_pipeline.version_activated",
    }


@pytest.mark.asyncio
async def test_xlsx_pipeline_keeps_sources_in_vector_index(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "xlsx pipeline sources")
    upload = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "销售.xlsx",
                _pipeline_xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    document_id = str(upload.json()["id"])
    completed = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    version_id = str(completed["candidate_version_id"])
    version = service.get_pipeline_version(version_id)

    indexed = service.vector_store.list_document_chunks(f"{version_id}_{document_id}")
    table_chunk = next(item for item in indexed if "上海" in item.text)
    assert table_chunk.kb_id == version["namespace"]
    assert table_chunk.sheet == "销售数据"
    assert table_chunk.row_range == "A1:B2"
    assert service.lexical_store.count_namespace(version["namespace"]) == 0


@pytest.mark.asyncio
async def test_knowledge_write_approval_inherits_active_snapshot_and_requires_promotion(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "knowledge inbox")
    alpha_id = await upload_text(
        client,
        kb_id,
        "alpha.txt",
        "Alpha remains part of the active source snapshot.",
    )
    baseline_job = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[alpha_id],
    )
    baseline_version_id = str(baseline_job["candidate_version_id"])
    activated = await client.post(
        f"/api/rag/pipeline/versions/{baseline_version_id}/activate"
    )
    assert activated.status_code == 409, activated.text
    assert activated.json()["detail"]["code"] == (
        "rag_content_contract_legacy_read_only"
    )
    _mark_pipeline_version_as_previously_active(service, baseline_version_id)

    proposal = service.create_knowledge_write_proposal(
        kb_id,
        title="Beta correction",
        content="Beta is approved only after evaluation and promotion.",
        tags=["release"],
        source_xpert_id="xpert_writer",
        source_run_id="run_writer",
    )
    update = await client.patch(
        f"/api/rag/knowledge-write-proposals/{proposal['proposal_id']}",
        json={
            "expected_revision": proposal["revision"],
            "title": "Beta release correction",
        },
    )
    assert update.status_code == 200, update.text

    approved = await client.post(
        f"/api/rag/knowledge-write-proposals/{proposal['proposal_id']}/approve",
        json={"expected_revision": update.json()["revision"]},
    )
    assert approved.status_code == 200, approved.text
    approved_payload = approved.json()
    assert approved_payload["status"] == "approved"
    assert approved_payload["build_status"] == "queued"
    assert service.get_active_pipeline_version(kb_id)["version_id"] == baseline_version_id

    assert await executor.run_once() is True
    refreshed = (
        await client.get(
            f"/api/rag/knowledge-write-proposals/{proposal['proposal_id']}"
        )
    ).json()
    assert refreshed["build_status"] == "succeeded"
    assert refreshed["candidate_ready"] is True
    candidate_id = str(refreshed["candidate_version_id"])
    candidate = service.get_pipeline_version(candidate_id)
    assert candidate["promotion_required"] is True
    assert candidate["base_version_id"] == baseline_version_id
    assert len(candidate["source_summary"]) == 2

    preview = await client.post(
        f"/api/rag/pipeline/versions/{candidate_id}/query",
        json={"question": "Beta release", "top_k": 5},
    )
    assert preview.status_code == 200, preview.text
    names = {item["document_name"] for item in preview.json()["sources"]}
    assert any(name.startswith("knowledge_proposal_") for name in names)
    inherited_preview = await client.post(
        f"/api/rag/pipeline/versions/{candidate_id}/query",
        json={"question": "Alpha source snapshot", "top_k": 5},
    )
    assert inherited_preview.status_code == 200, inherited_preview.text
    assert "alpha.txt" in {
        item["document_name"] for item in inherited_preview.json()["sources"]
    }

    blocked = await client.post(
        f"/api/rag/pipeline/versions/{candidate_id}/activate"
    )
    assert blocked.status_code == 409
    assert service.get_active_pipeline_version(kb_id)["version_id"] == baseline_version_id


@pytest.mark.asyncio
async def test_rejected_knowledge_write_proposal_creates_no_document_or_job(
    pipeline_runtime,
) -> None:
    client, service, _, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "reject proposal")
    proposal = service.create_knowledge_write_proposal(
        kb_id,
        title="Reject me",
        content="This content must never become a document.",
    )

    response = await client.post(
        f"/api/rag/knowledge-write-proposals/{proposal['proposal_id']}/reject",
        json={"expected_revision": proposal["revision"], "reason": "Not verified"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert service.list_documents(kb_id) == []
    assert service.list_pipeline_jobs(kb_id=kb_id) == []


@pytest.mark.asyncio
async def test_persisted_job_rebinds_to_recovery_run_after_registry_restart(
    pipeline_runtime,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "registry recovery")
    document_id = await upload_text(client, kb_id, "recovery.txt", "Recovery run source.")
    completed = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    previous_run_id = str(completed["run_id"])

    recovered_registry = RunRegistry()
    recovered_executor = KnowledgePipelineExecutor(
        service,
        run_registry=recovered_registry,
    )
    await recovered_executor.record_job_event(
        completed["job_id"],
        event_type="knowledge_pipeline.version_previewed",
        title="Candidate previewed after restart",
    )

    recovered_job = service.get_pipeline_job(completed["job_id"])
    assert recovered_job["run_id"] != previous_run_id
    recovered_run = await recovered_registry.get_run(recovered_job["run_id"])
    assert recovered_run is not None
    assert recovered_run.status == "completed"
    assert recovered_run.metadata["recovery_of_run_id"] == previous_run_id
    checkpoints = await recovered_registry.list_checkpoints(recovered_run.run_id)
    assert [item.event_type for item in checkpoints] == [
        "knowledge_pipeline.version_previewed"
    ]


@pytest.mark.asyncio
async def test_xpert_attachment_source_is_snapshotted_and_cross_xpert_access_is_rejected(
    pipeline_runtime,
) -> None:
    client, _, executor, _, context_store = pipeline_runtime
    kb_id = await create_kb(client, "attachment target")
    conversation = context_store.create_conversation("xpert-a", title="source")
    asset = context_store.add_file(
        "xpert-a",
        conversation.conversation_id,
        filename="brief.txt",
        content=b"The attachment defines the Orion launch checklist.",
    )

    bad = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": 1,
            "source_document_ids": [],
            "xpert_file_refs": [
                {
                    "xpert_id": "xpert-b",
                    "conversation_id": conversation.conversation_id,
                    "asset_id": asset.asset_id,
                }
            ],
        },
    )
    assert bad.status_code == 404

    completed = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[],
        xpert_file_refs=[
            {
                "xpert_id": "xpert-a",
                "conversation_id": conversation.conversation_id,
                "asset_id": asset.asset_id,
            },
            {
                "xpert_id": "xpert-a",
                "conversation_id": conversation.conversation_id,
                "asset_id": asset.asset_id,
            },
        ],
    )
    assert completed["source_count"] == 1
    context_store.archive_file("xpert-a", conversation.conversation_id, asset.asset_id)
    preview = await client.post(
        f"/api/rag/pipeline/versions/{completed['candidate_version_id']}/query",
        json={"question": "Orion checklist", "top_k": 3},
    )
    assert preview.status_code == 200
    assert preview.json()["sources"][0]["document_name"] == "brief.txt"


@pytest.mark.asyncio
async def test_cancelled_and_failed_jobs_do_not_change_active_version(
    pipeline_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, executor, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "failure isolation")
    document_id = await upload_text(client, kb_id, "stable.txt", "Stable production index.")
    completed = await execute_current_draft(
        client,
        executor,
        kb_id,
        source_document_ids=[document_id],
    )
    version_id = str(completed["candidate_version_id"])
    blocked = await client.post(f"/api/rag/pipeline/versions/{version_id}/activate")
    assert blocked.status_code == 409, blocked.text
    _mark_pipeline_version_as_previously_active(service, version_id)

    draft = (await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    queued = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={"draft_version": draft["version"], "source_document_ids": [document_id]},
    )
    cancel = await client.post(f"/api/rag/pipeline/jobs/{queued.json()['job_id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    assert service.get_active_pipeline_version(kb_id)["version_id"] == version_id

    failed = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={"draft_version": draft["version"], "source_document_ids": [document_id]},
    )

    async def fail_embeddings(_: list[str]) -> list[list[float]]:
        raise RuntimeError("synthetic embedding failure")

    monkeypatch.setattr(service.embedder, "embed_texts", fail_embeddings)
    assert await executor.run_once() is True
    failed_payload = (await client.get(f"/api/rag/pipeline/jobs/{failed.json()['job_id']}")).json()
    assert failed_payload["status"] == "failed"
    assert "synthetic embedding failure" in failed_payload["error"]
    assert service.get_active_pipeline_version(kb_id)["version_id"] == version_id

    retry = await client.post(f"/api/rag/pipeline/jobs/{failed_payload['job_id']}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_legacy_lexical_contract_blocks_candidate_before_index_writes(
    pipeline_runtime,
) -> None:
    client, service, _, _, _ = pipeline_runtime
    kb_id = await create_kb(client, "dual index atomicity")
    document_id = await upload_text(
        client,
        kb_id,
        "atomic.txt",
        "Vector and full-text indexes must become ready together.",
    )
    draft = (await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    queued = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={"draft_version": draft["version"], "source_document_ids": [document_id]},
    )
    assert queued.status_code == 409, queued.text
    assert queued.json()["detail"]["code"] == "rag_content_contract_legacy_read_only"
    assert service.get_active_pipeline_version(kb_id) is None
    assert service.lexical_store.count_namespace(kb_id) == 0
    assert service.vector_store.count_namespace(kb_id) == 0
    assert service.list_pipeline_jobs(kb_id=kb_id) == []
    versions = service.list_pipeline_versions(kb_id)
    assert versions == []


def test_pipeline_metadata_is_atomic_and_recovers_running_jobs(tmp_path: Path) -> None:
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("recovery")
    source = tmp_path / "source.txt"
    source.write_text("recovery source", encoding="utf-8")
    metadata = service._read_metadata()
    metadata["documents"]["doc-recovery"] = {
        "id": "doc-recovery",
        "kb_id": kb["id"],
        "filename": "source.txt",
        "stored_path": str(source),
        "size": source.stat().st_size,
        "chunk_count": 1,
        "created_at": 1.0,
    }
    service._write_metadata(metadata)
    configured = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=int(configured["version"]),
        source_document_ids=["doc-recovery"],
    )
    claimed = service.claim_next_pipeline_job()
    assert claimed is not None and claimed["status"] == "running"
    assert service.recover_pipeline_jobs() == 1
    recovered = service.get_pipeline_job(job["job_id"])
    assert recovered["status"] == "queued"
    assert service.metadata_path.exists()
    assert not service.metadata_path.with_suffix(".json.tmp").exists()
