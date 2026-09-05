import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from server.rag.chunking_receipt import (
    CHUNKING_RECEIPT_VERSION,
    legacy_chunking_receipt_is_valid,
)
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    DocumentNotFoundError,
    PipelineContentContractError,
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
        "receipt_version": CHUNKING_RECEIPT_VERSION,
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
        "heading_overlap_policy": "structural_prefix_floor_v1",
        "max_heading_prefix_tokens": 0,
        "prefix_exceeds_configured_overlap_count": 0,
        "max_effective_index_overlap_budget_tokens": 20,
        "max_effective_context_overlap_budget_tokens": 20,
        "generated_item_count": 0,
        "generated_item_chunk_count": 0,
        "generated_item_rejected_count": 0,
        "generated_item_rejection_reasons": {},
        "deduplicated_chunk_count": 0,
        "final_chunk_count": final_count,
        "chunk_sequence_hash": "a" * 64,
    }


def _legacy_v1_receipt(job: dict[str, Any], *, final_count: int = 1) -> dict[str, Any]:
    receipt = _valid_receipt(job, final_count=final_count)
    receipt["receipt_version"] = "rag-chunking-receipt-v1"
    for field in (
        "heading_overlap_policy",
        "max_heading_prefix_tokens",
        "prefix_exceeds_configured_overlap_count",
        "max_effective_index_overlap_budget_tokens",
        "max_effective_context_overlap_budget_tokens",
    ):
        receipt.pop(field)
    return receipt


def test_original_v1_receipt_is_legacy_readable_not_current() -> None:
    job = {
        "candidate_version_id": "kpv_legacy_receipt",
        "candidate_namespace": "kb_legacy_receipt__v3",
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
    receipt = _legacy_v1_receipt(job)

    assert legacy_chunking_receipt_is_valid(receipt, expected_chunk_count=1)
    assert not _chunking_receipt_is_valid(receipt, expected_chunk_count=1)

    receipt["raw_candidate_count"] = 2
    assert not legacy_chunking_receipt_is_valid(receipt, expected_chunk_count=1)


@pytest.mark.parametrize(
    "mutation",
    [
        {"raw_candidate_count": 4, "deduplicated_chunk_count": 1},
        {"generated_item_count": 0, "generated_item_rejected_count": 1},
        {"generated_item_count": 0, "generated_item_chunk_count": 1},
        {"heading_prefix_truncated_count": 3},
        {"heading_overlap_policy": "implicit"},
        {
            "max_heading_prefix_tokens": 25,
            "max_effective_index_overlap_budget_tokens": 20,
        },
        {"prefix_exceeds_configured_overlap_count": 3},
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
        ("max_effective_index_overlap_budget_tokens", 21),
        ("max_effective_context_overlap_budget_tokens", 21),
        ("prefix_exceeds_configured_overlap_count", 1),
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

    def validate_pipeline_job_execution_contract(
        self,
        _job_id: str,
    ) -> dict[str, Any]:
        """The sequence-hash unit fixture has no persisted execution boundary."""

        return self.get_pipeline_job(_job_id)

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
async def test_original_v1_receipt_is_reported_as_legacy_read_only(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    job_id, version_id = await _execute_version(service, name="legacy-v1-receipt")

    with service._metadata_lock:  # noqa: SLF001 - historical receipt fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        job = metadata["pipeline_jobs"][job_id]
        version = metadata["pipeline_versions"][version_id]
        legacy_receipt = _legacy_v1_receipt(
            job,
            final_count=int(version["chunk_count"]),
        )
        legacy_receipt["chunk_sequence_hash"] = version["chunking_receipt"][
            "chunk_sequence_hash"
        ]
        job["chunking_receipt"] = json.loads(json.dumps(legacy_receipt))
        version["chunking_receipt"] = json.loads(json.dumps(legacy_receipt))
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    evidence = service.pipeline_version_evidence(version_id)
    assert evidence["chunking_receipt_status"] == "legacy_read_only"



@pytest.mark.asyncio
async def test_terminal_historical_v3_without_chunker_receipt_remains_queryable(
    tmp_path: Path,
) -> None:
    """Pre-4A terminal V3 indexes stay readable without gaining promotion rights."""

    service = _service(tmp_path)
    job_id, version_id = await _execute_version(
        service,
        name="historical-v3-character",
    )
    version = service.get_pipeline_version(version_id)
    kb_id = str(version["kb_id"])
    legacy_chunker = {
        "strategy": "recursive_character",
        "chunk_size": 2_000,
        "chunk_overlap": 200,
        "separators": ["\n\n", "\n", "。", " ", ""],
    }

    with service._metadata_lock:  # noqa: SLF001 - historical persistence fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][job_id]
        stored_version = metadata["pipeline_versions"][version_id]
        for record in (stored_job, stored_version):
            record["config_snapshot"]["stages"]["stage_chunker"] = dict(
                legacy_chunker
            )
            record.pop("content_index_contract", None)
            record["config_snapshot"].pop("content_index_contract", None)
            record.pop("chunking_receipt", None)
        stored_job.pop("chunker_profile_fingerprint", None)
        stored_job.pop("config_snapshot_fingerprint", None)
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    projected = next(
        item
        for item in service.list_pipeline_versions(kb_id)
        if item["version_id"] == version_id
    )
    assert projected["content_index_contract"]["status"] == "legacy_read_only"
    assert projected["chunking_receipt"] == {}

    query = await service.query_pipeline_version(
        version_id,
        "Stable receipt lineage evidence. " * 20,
        generate_answer=False,
    )
    assert query["sources"]
    assert "Stable receipt lineage evidence" in query["sources"][0]["text"]

    with pytest.raises(PipelineContentContractError) as first_activation:
        service.activate_pipeline_version(version_id)
    assert first_activation.value.code == "rag_content_contract_legacy_read_only"
    with pytest.raises(PipelineContentContractError) as promotion:
        service.activate_pipeline_version(version_id, promotion=True)
    assert promotion.value.code == "rag_content_contract_legacy_read_only"


@pytest.mark.asyncio
async def test_source_snapshot_tamper_before_execution_cannot_publish_candidate(
    tmp_path: Path,
) -> None:
    """A queued job must not silently rebind its declared source content hash."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("queued-source-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Original immutable source evidence.",
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
    with service._metadata_lock:  # noqa: SLF001 - adversarial queued-job fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        snapshot_key = str(stored_job["sources"][0]["snapshot_key"])
    snapshot_path = service.storage_dir / snapshot_key
    snapshot_path.write_bytes(b"Substituted content after the source manifest was sealed.")

    assert await KnowledgePipelineExecutor(service).run_once() is False

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed", {
        "status": failed["status"],
        "stored_chunks": service.vector_store.count_namespace(namespace),
    }
    assert "source" in str(failed.get("error") or "").lower()
    assert failed["attempt"] == 0
    assert service.vector_store.count_namespace(namespace) == 0
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_source_snapshot_and_manifest_hash_cannot_rebind_document_result(
    tmp_path: Path,
) -> None:
    """Duplicated queued lineage must catch coordinated file/manifest rebinding."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("queued-source-manifest-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Original source and manifest identity.",
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
    replacement = b"Replacement bytes with a matching forged manifest hash."
    with service._metadata_lock:  # noqa: SLF001 - coordinated tamper fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        snapshot_path = service.storage_dir / str(
            stored_job["sources"][0]["snapshot_key"]
        )
        snapshot_path.write_bytes(replacement)
        stored_job["sources"][0]["content_hash"] = hashlib.sha256(
            replacement
        ).hexdigest()
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert await KnowledgePipelineExecutor(service).run_once() is False

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert "source" in str(failed.get("error") or "").lower()
    assert failed["attempt"] == 0
    assert service.vector_store.count_namespace(namespace) == 0
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_candidate_namespace_tamper_cannot_delete_other_candidate_namespace(
    tmp_path: Path,
) -> None:
    """Untrusted queued metadata must never authorize deletion of another index."""

    service = _service(tmp_path)
    _victim_job_id, victim_version_id = await _execute_version(
        service,
        name="namespace-victim",
    )
    victim_version = service.get_pipeline_version(victim_version_id)
    victim_namespace = str(victim_version["namespace"])
    victim_chunk_count = service.vector_store.count_namespace(victim_namespace)
    assert victim_chunk_count > 0

    kb = service.create_knowledge_base("queued-namespace-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Namespace-bound candidate evidence.",
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
    with service._metadata_lock:  # noqa: SLF001 - adversarial queued-job fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        stored_job["candidate_namespace"] = victim_namespace
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert await KnowledgePipelineExecutor(service).run_once() is False

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed", {
        "status": failed["status"],
        "victim_chunks": service.vector_store.count_namespace(victim_namespace),
    }
    assert "namespace" in str(failed.get("error") or "").lower()
    assert failed["attempt"] == 0
    assert service.vector_store.count_namespace(victim_namespace) == victim_chunk_count
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_tampered_version_namespace_cannot_read_another_kb_canary(
    tmp_path: Path,
) -> None:
    """A version row must not turn its namespace string into cross-KB read authority."""

    service = _service(tmp_path)
    victim_kb = service.create_knowledge_base("namespace-read-victim")
    victim_document = await service.upload_document(
        victim_kb["id"],
        "victim.txt",
        b"VICTIM NAMESPACE CANARY 948271 must remain private to knowledge base A.",
        pipeline_only=True,
    )
    victim_draft = service.update_pipeline_draft(
        victim_kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    victim_job = service.create_pipeline_job(
        victim_kb["id"],
        draft_version=victim_draft["version"],
        source_document_ids=[victim_document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    victim_version_id = str(victim_job["candidate_version_id"])
    victim_namespace = str(
        service.get_pipeline_version(victim_version_id)["namespace"]
    )
    victim_results = await service.search_knowledge(
        victim_kb["id"],
        "VICTIM NAMESPACE CANARY 948271",
        version_id=victim_version_id,
    )
    assert victim_results["sources"]
    victim_chunk_id = str(victim_results["sources"][0]["chunk_id"])

    attacker_kb = service.create_knowledge_base("namespace-read-attacker")
    attacker_document = await service.upload_document(
        attacker_kb["id"],
        "attacker.txt",
        b"Knowledge base B contains only harmless attacker-side evidence.",
        pipeline_only=True,
    )
    attacker_draft = service.update_pipeline_draft(
        attacker_kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    attacker_job = service.create_pipeline_job(
        attacker_kb["id"],
        draft_version=attacker_draft["version"],
        source_document_ids=[attacker_document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    attacker_version_id = str(attacker_job["candidate_version_id"])
    with service._metadata_lock:  # noqa: SLF001 - adversarial version fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][attacker_version_id][
            "namespace"
        ] = victim_namespace
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineJobStateError, match="namespace|owner"):
        await service.search_knowledge(
            attacker_kb["id"],
            "VICTIM NAMESPACE CANARY 948271",
            version_id=attacker_version_id,
        )
    with pytest.raises(
        (PipelineJobStateError, DocumentNotFoundError),
        match="namespace|owner|not found",
    ):
        service.get_knowledge_chunk(
            attacker_kb["id"],
            victim_chunk_id,
            version_id=attacker_version_id,
        )


@pytest.mark.asyncio
async def test_running_job_recovery_forged_namespace_does_not_delete_victim(
    tmp_path: Path,
) -> None:
    """Recovery must validate job ownership before deleting partial namespaces."""

    service = _service(tmp_path)
    _victim_job_id, victim_version_id = await _execute_version(
        service,
        name="namespace-recovery-victim",
    )
    victim_namespace = str(
        service.get_pipeline_version(victim_version_id)["namespace"]
    )
    victim_count = service.vector_store.count_namespace(victim_namespace)
    assert victim_count > 0

    attacker_kb = service.create_knowledge_base("namespace-recovery-attacker")
    attacker_document = await service.upload_document(
        attacker_kb["id"],
        "attacker.txt",
        b"A forged recovery handle must not authorize victim deletion.",
        pipeline_only=True,
    )
    attacker_draft = service.update_pipeline_draft(
        attacker_kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    attacker_job = service.create_pipeline_job(
        attacker_kb["id"],
        draft_version=attacker_draft["version"],
        source_document_ids=[attacker_document["id"]],
    )
    claimed = service.claim_next_pipeline_job()
    assert claimed is not None and claimed["job_id"] == attacker_job["job_id"]
    with service._metadata_lock:  # noqa: SLF001 - adversarial recovery fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_jobs"][attacker_job["job_id"]][
            "candidate_namespace"
        ] = victim_namespace
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert service.recover_pipeline_jobs() == 1
    assert service.get_pipeline_job(attacker_job["job_id"])["status"] == "failed"
    assert service.vector_store.count_namespace(victim_namespace) == victim_count


@pytest.mark.asyncio
async def test_strategy_trial_cleanup_forged_namespace_does_not_delete_victim(
    tmp_path: Path,
) -> None:
    """Trial cleanup must derive deletion scope instead of trusting a version row."""

    service = _service(tmp_path)
    _victim_job_id, victim_version_id = await _execute_version(
        service,
        name="namespace-cleanup-victim",
    )
    victim_namespace = str(
        service.get_pipeline_version(victim_version_id)["namespace"]
    )
    victim_count = service.vector_store.count_namespace(victim_namespace)
    assert victim_count > 0

    attacker_job_id, attacker_version_id = await _execute_version(
        service,
        name="namespace-cleanup-attacker",
    )
    with service._metadata_lock:  # noqa: SLF001 - adversarial cleanup fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][attacker_version_id]
        version["origin"] = {
            "kind": "rag_strategy_tuner_trial",
            "source_run_id": "namespace-attack-run",
        }
        version["namespace"] = victim_namespace
        metadata["pipeline_jobs"][attacker_job_id]["origin"] = {
            "kind": "rag_strategy_tuner_trial",
            "source_run_id": "namespace-attack-run",
        }
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineJobStateError, match="namespace|contract|identity"):
        service.cleanup_strategy_tuning_trial_version(
            attacker_version_id,
            expected_run_id="namespace-attack-run",
        )
    assert service.vector_store.count_namespace(victim_namespace) == victim_count


@pytest.mark.asyncio
async def test_index_contract_tamper_fails_before_vector_write(
    tmp_path: Path,
) -> None:
    """A queued vector job must revalidate its derived index contract at claim time."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("queued-index-contract-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Vector retrieval requires a verifiable vector index.",
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
    with service._metadata_lock:  # noqa: SLF001 - adversarial queued-job fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        stored_job["config_snapshot"]["index_contract"]["vector"][
            "required"
        ] = False
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert await KnowledgePipelineExecutor(service).run_once() is False

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert any(
        marker in str(failed.get("error") or "").lower()
        for marker in ("configuration snapshot", "index contract")
    )
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert failed["attempt"] == 0
    assert service.vector_store.count_namespace(namespace) == 0
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_recursive_chunker_tamper_fails_at_claim_before_embedding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued jobs must revalidate the complete recursive chunker profile."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("queued-recursive-chunker-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"An invalid recursive budget must fail before provider execution.",
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
    with service._metadata_lock:  # noqa: SLF001 - adversarial queued-job fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        stored_job["config_snapshot"]["stages"]["stage_chunker"][
            "chunk_size"
        ] = 1
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    embed_calls = 0

    async def forbidden_embedding(_texts: list[str]) -> list[list[float]]:
        nonlocal embed_calls
        embed_calls += 1
        raise AssertionError("invalid queued chunker reached embedding")

    monkeypatch.setattr(service.embedder, "embed_texts", forbidden_embedding)

    assert await KnowledgePipelineExecutor(service).run_once() is False

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == 0
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert embed_calls == 0
    assert service.vector_store.count_namespace(namespace) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    ["legal_budget_change", "legacy_strategy_downgrade", "fingerprint_removed"],
)
async def test_chunker_tamper_after_claim_fails_before_embedding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    """No profile mutation may replace the sealed job snapshot after claim."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("running-legal-chunker-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"The claimed job must retain its exact admitted chunking profile.",
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
    assert claimed is not None and claimed["job_id"] == created["job_id"]

    with service._metadata_lock:  # noqa: SLF001 - deterministic TOCTOU fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        if tamper == "legal_budget_change":
            stored_job["config_snapshot"]["stages"]["stage_chunker"][
                "chunk_size"
            ] += 100
        elif tamper == "legacy_strategy_downgrade":
            stored_job["config_snapshot"]["stages"]["stage_chunker"][
                "strategy"
            ] = "recursive_character"
        else:
            stored_job.pop("chunker_profile_fingerprint", None)
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    embed_calls = 0

    async def forbidden_embedding(_texts: list[str]) -> list[list[float]]:
        nonlocal embed_calls
        embed_calls += 1
        raise AssertionError("mutated running chunker reached embedding")

    monkeypatch.setattr(service.embedder, "embed_texts", forbidden_embedding)

    await KnowledgePipelineExecutor(service)._execute(  # noqa: SLF001
        service.get_pipeline_job(created["job_id"])
    )

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == 1
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert embed_calls == 0
    assert service.vector_store.count_namespace(namespace) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "running"])
@pytest.mark.parametrize("profile", ["processor", "vision"])
async def test_processor_and_vision_snapshot_tamper_fails_before_embedding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    profile: str,
) -> None:
    """Every parser/provider-affecting config must remain bound after admission."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base(f"{phase}-{profile}-snapshot-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Mutable processor and vision profiles must fail before execution.",
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
    if phase == "running":
        claimed = service.claim_next_pipeline_job()
        assert claimed is not None and claimed["job_id"] == created["job_id"]

    with service._metadata_lock:  # noqa: SLF001 - adversarial TOCTOU fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        if profile == "processor":
            for processor in (
                stored_job["config_snapshot"]["processor_profile"],
                stored_job["config_snapshot"]["stages"]["stage_processor"],
            ):
                processor["mode"] = "qa"
                processor["model_id"] = "tampered-model"
        else:
            for vision in (
                stored_job["config_snapshot"]["vision_profile"],
                stored_job["config_snapshot"]["stages"][
                    "stage_image_understanding"
                ],
            ):
                vision["render_dpi"] = 288
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    embed_calls = 0

    async def forbidden_embedding(_texts: list[str]) -> list[list[float]]:
        nonlocal embed_calls
        embed_calls += 1
        raise AssertionError("mutated processor/vision profile reached embedding")

    monkeypatch.setattr(service.embedder, "embed_texts", forbidden_embedding)

    if phase == "queued":
        assert await KnowledgePipelineExecutor(service).run_once() is False
    else:
        await KnowledgePipelineExecutor(service)._execute(  # noqa: SLF001
            service.get_pipeline_job(created["job_id"])
        )

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == (0 if phase == "queued" else 1)
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert embed_calls == 0
    assert service.vector_store.count_namespace(namespace) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "profile_key", "stage_key", "field", "value"),
    [
        ("queued", "processor_profile", "stage_processor", "max_generated_items", float("nan")),
        ("queued", "vision_profile", "stage_image_understanding", "render_dpi", float("inf")),
        ("running", "processor_profile", "stage_processor", "max_generated_items", float("-inf")),
        ("running", "vision_profile", "stage_image_understanding", "render_dpi", float("nan")),
    ],
)
async def test_nonfinite_config_snapshot_fails_closed_without_worker_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    profile_key: str,
    stage_key: str,
    field: str,
    value: float,
) -> None:
    """Non-JSON numeric state must become a failed Job before any stage runs."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base(f"{phase}-{profile_key}-nonfinite")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Non-finite persisted configuration must fail closed.",
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
    if phase == "running":
        claimed = service.claim_next_pipeline_job()
        assert claimed is not None and claimed["job_id"] == created["job_id"]

    with service._metadata_lock:  # noqa: SLF001 - malformed persistence fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        stored_job["config_snapshot"][profile_key][field] = value
        stored_job["config_snapshot"]["stages"][stage_key][field] = value
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    stage_calls = 0

    async def forbidden_stage(*_args, **_kwargs):
        nonlocal stage_calls
        stage_calls += 1
        raise AssertionError("malformed config reached a pipeline stage")

    monkeypatch.setattr(service, "process_pipeline_job_vision", forbidden_stage)
    monkeypatch.setattr(service, "process_pipeline_job_sources", forbidden_stage)

    executor = KnowledgePipelineExecutor(service)
    if phase == "queued":
        assert await executor.run_once() is False
    else:
        await executor._execute(  # noqa: SLF001 - running recovery boundary.
            service.get_pipeline_job(created["job_id"])
        )

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == (0 if phase == "queued" else 1)
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert stage_calls == 0
    assert service.vector_store.count_namespace(namespace) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "running"])
@pytest.mark.parametrize(
    "tamper",
    [
        "source_filename",
        "source_content_mode",
        "source_kind",
        "result_filename",
        "artifact_key",
        "vision_artifact_key",
    ],
)
async def test_source_manifest_and_result_static_identity_tamper_fails_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    tamper: str,
) -> None:
    """Parser routing and artifact ownership must remain immutable after admission."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base(f"{phase}-{tamper}-identity-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"The admitted source manifest determines parser routing and artifacts.",
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
    if phase == "running":
        claimed = service.claim_next_pipeline_job()
        assert claimed is not None and claimed["job_id"] == created["job_id"]

    with service._metadata_lock:  # noqa: SLF001 - adversarial lineage fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        source = stored_job["sources"][0]
        result = stored_job["document_results"][0]
        namespace = str(stored_job["candidate_namespace"])
        if tamper == "source_filename":
            source["filename"] = "source.pdf"
        elif tamper == "source_content_mode":
            source["content_mode"] = "extracted_text"
        elif tamper == "source_kind":
            source["source_kind"] = "xpert_file"
        elif tamper == "result_filename":
            result["filename"] = "other.txt"
        elif tamper == "artifact_key":
            result["artifact_key"] = "pipeline_processed/other/source_0.json"
        else:
            result["vision_artifact_key"] = "pipeline_vision/other/source_0.json"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    stage_calls = 0

    async def forbidden_stage(*_args, **_kwargs):
        nonlocal stage_calls
        stage_calls += 1
        raise AssertionError("mutable source identity reached a pipeline stage")

    monkeypatch.setattr(service, "process_pipeline_job_vision", forbidden_stage)
    monkeypatch.setattr(service, "process_pipeline_job_sources", forbidden_stage)

    executor = KnowledgePipelineExecutor(service)
    if phase == "queued":
        assert await executor.run_once() is False
    else:
        await executor._execute(  # noqa: SLF001 - running recovery boundary.
            service.get_pipeline_job(created["job_id"])
        )

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["attempt"] == (0 if phase == "queued" else 1)
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert stage_calls == 0
    assert service.vector_store.count_namespace(namespace) == 0


@pytest.mark.asyncio
async def test_completed_processor_artifact_tamper_cannot_be_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed job must bind the processed bytes it is about to chunk."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("processed-artifact-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"The immutable processed artifact supplies canonical chunk content.",
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
    assert claimed is not None and claimed["job_id"] == created["job_id"]
    first = await service.process_pipeline_job_sources(created["job_id"])
    assert first and first[0]["reused"] is False

    stored = service.get_pipeline_job(created["job_id"])
    result = stored["document_results"][0]
    assert result.get("artifact_hash")
    artifact_path = service._pipeline_processed_path(  # noqa: SLF001
        result["artifact_key"]
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["processed_document"]["blocks"][0]["text"] = "tampered"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    def forbidden_process(*_args, **_kwargs):
        raise AssertionError("tampered completed artifact was silently regenerated")

    monkeypatch.setattr(service.document_processor, "process", forbidden_process)

    with pytest.raises(PipelineJobStateError, match="processed artifact"):
        await service.process_pipeline_job_sources(created["job_id"])


@pytest.mark.asyncio
async def test_processor_result_config_hash_tamper_cannot_trigger_reprocessing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result identity mismatch is corruption, not a cache miss to regenerate."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("processed-result-config-tamper")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"The processor result must remain bound to its admitted profile.",
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
    assert service.claim_next_pipeline_job() is not None
    first = await service.process_pipeline_job_sources(created["job_id"])
    assert first and first[0]["reused"] is False

    with service._metadata_lock:  # noqa: SLF001 - persisted lineage tamper.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_jobs"][created["job_id"]]["document_results"][0][
            "processor_config_hash"
        ] = "0" * 64
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    def forbidden_process(*_args, **_kwargs):
        raise AssertionError("corrupt result identity triggered reprocessing")

    monkeypatch.setattr(service.document_processor, "process", forbidden_process)
    with pytest.raises(PipelineJobStateError, match="result configuration"):
        await service.process_pipeline_job_sources(created["job_id"])


@pytest.mark.asyncio
async def test_malformed_embedding_dimension_fails_job_without_crashing_worker(
    tmp_path: Path,
) -> None:
    """Malformed mutable identity fields must become a failed Job, not a worker crash."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("malformed-embedding-dimension")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"A malformed identity must fail before any vector write.",
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
    with service._metadata_lock:  # noqa: SLF001 - adversarial queued-job fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        stored_job["config_snapshot"]["embedding_profile"]["effective"][
            "dimension"
        ] = "not-an-integer"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assert await KnowledgePipelineExecutor(service).run_once() is False

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "rag_pipeline_job_contract_invalid"
    assert failed["attempt"] == 0
    assert service.vector_store.count_namespace(namespace) == 0


@pytest.mark.asyncio
async def test_source_snapshot_tamper_after_claim_fails_before_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load boundary must close the filesystem TOCTOU gap after claim."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("source-claim-race")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Source bytes verified while the job is still queued.",
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
    with service._metadata_lock:  # noqa: SLF001 - deterministic TOCTOU fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        snapshot_path = service.storage_dir / str(
            stored_job["sources"][0]["snapshot_key"]
        )
    load_sources = service.load_pipeline_job_sources

    def tamper_then_load(job_id: str) -> list[dict[str, Any]]:
        snapshot_path.write_bytes(b"Changed after claim but before load.")
        return load_sources(job_id)

    monkeypatch.setattr(service, "load_pipeline_job_sources", tamper_then_load)

    assert await KnowledgePipelineExecutor(service).run_once() is True

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert "source" in str(failed.get("error") or "").lower()
    assert failed["attempt"] == 1
    assert service.vector_store.count_namespace(namespace) == 0
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_source_snapshot_tamper_after_load_fails_before_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful load check must not leave the later processor read unguarded."""

    service = _service(tmp_path)
    kb = service.create_knowledge_base("source-process-race")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"Source bytes remain immutable through processing.",
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
    with service._metadata_lock:  # noqa: SLF001 - deterministic TOCTOU fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = metadata["pipeline_jobs"][created["job_id"]]
        namespace = str(stored_job["candidate_namespace"])
        snapshot_path = service.storage_dir / str(
            stored_job["sources"][0]["snapshot_key"]
        )
    process_sources = service.process_pipeline_job_sources

    async def tamper_then_process(job_id: str) -> list[dict[str, Any]]:
        snapshot_path.write_bytes(b"Changed after load but before processor read.")
        return await process_sources(job_id)

    monkeypatch.setattr(
        service,
        "process_pipeline_job_sources",
        tamper_then_process,
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert "source" in str(failed.get("error") or "").lower()
    assert service.vector_store.count_namespace(namespace) == 0
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


@pytest.mark.asyncio
async def test_namespace_tamper_after_identity_rebind_cannot_delete_other_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TOCTOU mutation after identity rebind must not leave cross-namespace data."""

    service = _service(tmp_path)
    _victim_job_id, victim_version_id = await _execute_version(
        service,
        name="namespace-race-victim",
    )
    victim_namespace = str(
        service.get_pipeline_version(victim_version_id)["namespace"]
    )
    victim_chunk_count = service.vector_store.count_namespace(victim_namespace)
    assert victim_chunk_count > 0

    kb = service.create_knowledge_base("namespace-rebind-race")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"The final vector write must revalidate its namespace identity.",
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
    update_dimension = service.update_pipeline_embedding_dimension

    def rebind_then_tamper(job_id: str, dimension: int) -> None:
        update_dimension(job_id, dimension)
        with service._metadata_lock:  # noqa: SLF001 - deterministic TOCTOU fixture.
            metadata = service._read_metadata_unlocked()  # noqa: SLF001
            metadata["pipeline_jobs"][job_id][
                "candidate_namespace"
            ] = victim_namespace
            service._write_metadata_unlocked(metadata)  # noqa: SLF001

    monkeypatch.setattr(
        service,
        "update_pipeline_embedding_dimension",
        rebind_then_tamper,
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True

    failed = service.get_pipeline_job(created["job_id"])
    assert failed["status"] == "failed"
    assert service.vector_store.count_namespace(victim_namespace) == victim_chunk_count
    with pytest.raises(PipelineVersionNotFoundError):
        service.get_pipeline_version(created["candidate_version_id"])


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
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        service,
        "_stored_vector_chunk_sequence_hash",
        lambda **_kwargs: receipt["chunk_sequence_hash"],
    )

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
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        service,
        "_content_index_contract_for_version",
        lambda _version: {
            "contract_version": "rag-content-index-contract-v1",
            "chunker_contract_version": "rag-chunker-estimated-token-v1",
            "lexical_contract_version": "sqlite-fts5-lexical-v2",
            "parser_contract_version": "canonical-structured-parser-v2",
            "status": "current",
            "components": {
                "chunker": "current",
                "lexical": "current",
                "parser": "current",
            },
        },
    )
    with pytest.raises(PipelineJobStateError, match="chunking receipt lineage"):
        service.activate_pipeline_version(version_id)
    assert service.get_pipeline_version(version_id)["status"] == "ready"
