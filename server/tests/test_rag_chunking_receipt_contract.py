import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    PipelineJobStateError,
    PipelineVersionNotFoundError,
    RagService,
    _chunking_receipt_is_valid,
)
from server.rag.vector_store import LocalJsonVectorStore, VectorStoreContractError


def _service(tmp_path: Path) -> RagService:
    storage = tmp_path / "rag-storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _chunker_profile(job: dict[str, Any]) -> dict[str, Any]:
    raw = job["config_snapshot"]["stages"]["stage_chunker"]
    fields = (
        "strategy",
        "chunk_size",
        "chunk_overlap",
        "separators",
        "parent_chunk_size",
        "parent_chunk_overlap",
        "child_chunk_size",
        "child_chunk_overlap",
        "parent_separators",
        "child_separators",
        "size_unit",
        "token_estimator",
        "chunk_contract_version",
    )
    return {key: json.loads(json.dumps(raw[key])) for key in fields if key in raw}


def _valid_receipt(job: dict[str, Any], *, final_count: int = 1) -> dict[str, Any]:
    return {
        "receipt_version": "rag-chunking-receipt-v1",
        "contract_version": "rag-chunker-estimated-token-v1",
        "strategy": "recursive_estimated_token",
        "size_unit": "estimated_tokens",
        "token_estimator": "mixed_cjk_latin_v1",
        "chunker_profile_fingerprint": _canonical_hash(_chunker_profile(job)),
        "candidate_version_id": str(job["candidate_version_id"]),
        "candidate_namespace_fingerprint": hashlib.sha256(
            str(job["candidate_namespace"]).encode("utf-8")
        ).hexdigest(),
        "raw_candidate_count": final_count,
        "heading_block_count": 0,
        "heading_prefix_truncated_count": 0,
        "generated_item_count": 0,
        "generated_item_chunk_count": 0,
        "generated_item_rejected_count": 0,
        "generated_item_rejection_reasons": {},
        "deduplicated_chunk_count": 0,
        "final_chunk_count": final_count,
        "chunk_sequence_hash": "a" * 64,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"raw_candidate_count": 4, "deduplicated_chunk_count": 1},
        {"generated_item_count": 0, "generated_item_rejected_count": 1},
        {
            "generated_item_count": 1,
            "generated_item_rejected_count": 1,
            "generated_item_rejection_reasons": {},
        },
        {
            "generated_item_count": 1,
            "generated_item_rejected_count": 1,
            "generated_item_rejection_reasons": {"invalid": 0},
        },
    ],
)
def test_chunking_receipt_count_invariants_fail_closed(mutation: dict[str, Any]) -> None:
    job = {
        "candidate_version_id": "kpv_receipt",
        "candidate_namespace": "kb_receipt__v3",
        "config_snapshot": {
            "stages": {
                "stage_chunker": {
                    "strategy": "recursive_estimated_token",
                    "chunk_size": 100,
                    "chunk_overlap": 20,
                    "size_unit": "estimated_tokens",
                    "token_estimator": "mixed_cjk_latin_v1",
                    "chunk_contract_version": "rag-chunker-estimated-token-v1",
                }
            }
        },
    }
    receipt = _valid_receipt(job, final_count=2)
    receipt.update(mutation)

    assert not _chunking_receipt_is_valid(receipt, expected_chunk_count=2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy", "parent_child_estimated_token"),
        ("contract_version", "rag-chunker-character-v1"),
        ("size_unit", "characters"),
        ("token_estimator", None),
    ],
)
def test_chunking_receipt_semantics_must_match_bound_profile(
    field: str,
    value: Any,
) -> None:
    job = {
        "candidate_version_id": "kpv_receipt",
        "candidate_namespace": "kb_receipt__v3",
        "config_snapshot": {
            "stages": {
                "stage_chunker": {
                    "strategy": "recursive_estimated_token",
                    "chunk_size": 100,
                    "chunk_overlap": 20,
                    "size_unit": "estimated_tokens",
                    "token_estimator": "mixed_cjk_latin_v1",
                    "chunk_contract_version": "rag-chunker-estimated-token-v1",
                }
            }
        },
    }
    profile = _chunker_profile(job)
    receipt = _valid_receipt(job)
    receipt[field] = value

    assert not _chunking_receipt_is_valid(
        receipt,
        expected_chunk_count=1,
        expected_chunker_profile=profile,
    )


class _ChunkingServiceStub:
    def __init__(self, *, page_number: int, heading_path: list[str]) -> None:
        chunker = {
            "strategy": "recursive_estimated_token",
            "chunk_size": 100,
            "chunk_overlap": 20,
            "separators": ["\n\n", "\n", "。", " ", ""],
            "size_unit": "estimated_tokens",
            "token_estimator": "mixed_cjk_latin_v1",
            "chunk_contract_version": "rag-chunker-estimated-token-v1",
        }
        self.job = {
            "candidate_version_id": "kpv_sequence",
            "candidate_namespace": "kb_sequence__v3",
            "config_snapshot": {"stages": {"stage_chunker": chunker}},
        }
        self.page_number = page_number
        self.heading_path = heading_path
        self.receipt: dict[str, Any] = {}

    def get_pipeline_job(self, _job_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.job))

    def update_pipeline_document_chunk_counts(
        self,
        _job_id: str,
        _counts: dict[str, int],
    ) -> None:
        return None

    def update_pipeline_chunking_receipt(
        self,
        _job_id: str,
        receipt: dict[str, Any],
    ) -> None:
        self.receipt = json.loads(json.dumps(receipt))

    def parsed(self) -> list[dict[str, Any]]:
        text = "Stable page and heading evidence."
        return [
            {
                "source_id": "doc-sequence",
                "filename": "sequence.md",
                "processed_document": {
                    "blocks": [
                        {
                            "block_id": "block-sequence",
                            "kind": "paragraph",
                            "text": text,
                            "start_char": 0,
                            "end_char": len(text),
                            "page_number": self.page_number,
                            "heading_path": self.heading_path,
                            "metadata": {},
                        }
                    ]
                },
            }
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left_page", "left_heading", "right_page", "right_heading"),
    [
        (1, ["Root", "Leaf"], 2, ["Root", "Leaf"]),
        (1, ["Root", "Leaf"], 1, ["Root", "Changed"]),
    ],
)
async def test_chunk_sequence_hash_binds_page_and_heading_metadata(
    left_page: int,
    left_heading: list[str],
    right_page: int,
    right_heading: list[str],
) -> None:
    left = _ChunkingServiceStub(page_number=left_page, heading_path=left_heading)
    right = _ChunkingServiceStub(page_number=right_page, heading_path=right_heading)

    await KnowledgePipelineExecutor(left)._chunk_sources("job-left", left.parsed())  # type: ignore[arg-type]  # noqa: SLF001
    await KnowledgePipelineExecutor(right)._chunk_sources("job-right", right.parsed())  # type: ignore[arg-type]  # noqa: SLF001

    assert left.receipt["chunk_sequence_hash"] != right.receipt["chunk_sequence_hash"]


async def _execute_version(
    service: RagService,
    *,
    name: str,
) -> tuple[str, str]:
    kb = service.create_knowledge_base(name)
    document = await service.upload_document(
        kb["id"],
        f"{name}.txt",
        ("Stable receipt lineage evidence. " * 20).encode("utf-8"),
        pipeline_only=True,
    )
    assert service.vector_store.count_namespace(kb["id"]) == 0
    assert service.lexical_store.count_namespace(kb["id"]) == 0
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(created["job_id"])
    assert completed["status"] == "succeeded"
    return str(created["job_id"]), str(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_stored_vector_tamper_invalidates_receipt_evidence_and_corpus(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _job_id, version_id = await _execute_version(
        service,
        name="stored-vector-tamper",
    )
    vector_path = service.vector_store.storage_path  # type: ignore[attr-defined]
    records = json.loads(vector_path.read_text(encoding="utf-8"))
    records[0]["text"] = "tampered after successful candidate publication"
    vector_path.write_text(json.dumps(records), encoding="utf-8")

    evidence = service.pipeline_version_evidence(version_id)
    assert evidence["chunking_receipt_status"] == "mismatch"
    assert evidence["stored_chunk_sequence_status"] == "mismatch"
    with pytest.raises(PipelineJobStateError, match="stored vector chunks"):
        service.pipeline_corpus_snapshot(version_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["double_space", "newline", "nfkc_variant"])
async def test_stored_text_equivalence_does_not_bypass_exact_receipt(
    tmp_path: Path,
    tamper: str,
) -> None:
    service = _service(tmp_path)
    _job_id, version_id = await _execute_version(
        service,
        name=f"stored-exact-{tamper}",
    )
    vector_path = service.vector_store.storage_path  # type: ignore[attr-defined]
    records = json.loads(vector_path.read_text(encoding="utf-8"))
    original = str(records[0]["text"])
    if tamper == "double_space":
        records[0]["text"] = original.replace(" ", "  ", 1)
    elif tamper == "newline":
        records[0]["text"] = original.replace(" ", "\n", 1)
    else:
        records[0]["text"] = original.replace("S", "Ｓ", 1)
    assert records[0]["text"] != original
    vector_path.write_text(json.dumps(records), encoding="utf-8")

    evidence = service.pipeline_version_evidence(version_id)
    assert evidence["chunking_receipt_status"] == "mismatch"
    assert evidence["stored_chunk_sequence_status"] == "mismatch"
    with pytest.raises(PipelineJobStateError, match="stored vector chunks"):
        service.pipeline_corpus_snapshot(version_id)


@pytest.mark.asyncio
async def test_stored_chunk_id_only_tamper_invalidates_receipt_evidence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _job_id, version_id = await _execute_version(
        service,
        name="stored-id-tamper",
    )
    vector_path = service.vector_store.storage_path  # type: ignore[attr-defined]
    records = json.loads(vector_path.read_text(encoding="utf-8"))
    records[0]["id"] = f"{records[0]['id']}-forged"
    vector_path.write_text(json.dumps(records), encoding="utf-8")

    evidence = service.pipeline_version_evidence(version_id)

    assert evidence["chunking_receipt_status"] == "mismatch"
    assert evidence["stored_chunk_sequence_status"] == "mismatch"
    with pytest.raises(PipelineJobStateError, match="stored vector chunks"):
        service.pipeline_corpus_snapshot(version_id)


@pytest.mark.asyncio
async def test_corpus_snapshot_and_evidence_read_authoritative_v3_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _job_id, version_id = await _execute_version(
        service,
        name="authoritative-namespace",
    )
    version = service.get_pipeline_version(version_id)
    namespace = str(version["namespace"])
    original_list = service.vector_store.list_document_chunks
    calls: list[tuple[str, str | None]] = []

    def list_authoritative(
        doc_id: str,
        *,
        kb_id: str | None = None,
    ):
        calls.append((doc_id, kb_id))
        return original_list(doc_id, kb_id=kb_id)

    monkeypatch.setattr(
        service.vector_store,
        "list_document_chunks",
        list_authoritative,
    )

    service.pipeline_corpus_snapshot(version_id)
    service.pipeline_corpus_evidence(version_id)

    assert len(calls) >= 2
    assert all(kb_id == namespace for _doc_id, kb_id in calls)


@pytest.mark.asyncio
async def test_corpus_namespace_contract_error_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _job_id, version_id = await _execute_version(
        service,
        name="namespace-contract-error",
    )
    original_list = service.vector_store.list_document_chunks
    calls = [0]

    def fail_contract(
        doc_id: str,
        *,
        kb_id: str | None = None,
    ):
        assert kb_id
        calls[0] += 1
        if calls[0] == 1:
            return original_list(doc_id, kb_id=kb_id)
        raise VectorStoreContractError("synthetic namespace identity conflict")

    monkeypatch.setattr(
        service.vector_store,
        "list_document_chunks",
        fail_contract,
    )

    with pytest.raises(PipelineJobStateError, match="namespace contract"):
        service.pipeline_corpus_snapshot(version_id)
    calls[0] = 0
    with pytest.raises(PipelineJobStateError, match="namespace contract"):
        service.pipeline_corpus_evidence(version_id)


@pytest.mark.asyncio
async def test_tampered_vector_write_cannot_publish_ready_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    store = service.vector_store
    original_add = store.add_chunks

    def tampered_add(chunks) -> None:
        original_add(chunks)
        vector_path = store.storage_path  # type: ignore[attr-defined]
        records = json.loads(vector_path.read_text(encoding="utf-8"))
        records[0]["parent_text"] = "tampered during vector persistence"
        vector_path.write_text(json.dumps(records), encoding="utf-8")

    monkeypatch.setattr(store, "add_chunks", tampered_add)
    kb = service.create_knowledge_base("write tamper")
    document = await service.upload_document(
        kb["id"],
        "write-tamper.txt",
        ("Stored receipt binding must be authoritative. " * 20).encode("utf-8"),
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True
    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert "Stored vector chunks" in str(failed["error"])
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    ["version_chunker", "job_version_id", "version_namespace"],
)
async def test_version_evidence_and_corpus_bind_job_version_namespace_and_chunker(
    tmp_path: Path,
    tamper: str,
) -> None:
    service = _service(tmp_path)
    job_id, version_id = await _execute_version(service, name=f"lineage-{tamper}")

    with service._metadata_lock:  # noqa: SLF001 - one-sided lineage tamper fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        job = metadata["pipeline_jobs"][job_id]
        if tamper == "version_chunker":
            version["config_snapshot"]["stages"]["stage_chunker"]["chunk_size"] += 1
        elif tamper == "job_version_id":
            job["candidate_version_id"] = "kpv_other"
        else:
            version["namespace"] = "different-namespace"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert service.pipeline_version_evidence(version_id)["chunking_receipt_status"] == (
        "mismatch"
    )
    with pytest.raises(PipelineJobStateError, match="provenance|receipt"):
        service.pipeline_corpus_snapshot(version_id)


@pytest.mark.asyncio
async def test_invalid_chunking_receipt_cannot_publish_ready_version(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("invalid ready")
    document = await service.upload_document(
        kb["id"],
        "invalid-ready.txt",
        b"Invalid receipts must not publish ready versions.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    claimed = service.claim_next_pipeline_job()
    assert claimed is not None
    job = service.get_pipeline_job(created["job_id"])
    receipt = _valid_receipt(job)
    receipt["raw_candidate_count"] = 2
    service.update_pipeline_chunking_receipt(created["job_id"], receipt)

    with pytest.raises(PipelineJobStateError, match="chunking receipt"):
        service.complete_pipeline_job(
            created["job_id"],
            document_count=1,
            chunk_count=1,
        )
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_tampered_receipt_remains_unactivatable_during_4a(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    job_id, version_id = await _execute_version(service, name="invalid-activation")

    with service._metadata_lock:  # noqa: SLF001 - receipt tamper fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        version["chunking_receipt"]["raw_candidate_count"] += 1
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert service.get_pipeline_job(job_id)["status"] == "succeeded"
    assert service.pipeline_version_evidence(version_id)["chunking_receipt_status"] == (
        "invalid"
    )
    with pytest.raises(PipelineJobStateError, match="Legacy content-index contracts"):
        service.activate_pipeline_version(version_id)
    assert service.get_pipeline_version(version_id)["status"] == "ready"
