from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from .chunking_receipt import (
    CHUNKING_RECEIPT_VERSION,
    candidate_namespace_fingerprint,
    canonical_chunk_sequence_hash,
    chunker_profile_fingerprint,
)
from .embedder import EmbeddingClient, EmbeddingError
from .content_identity import (
    canonical_source_text_hash,
    generated_parent_identity,
    generated_parent_window_identity,
    generated_segment_source_mapping,
)
from .lexical_store import LexicalChunk
from .rag_service import RagService
from .splitter import (
    EstimatedTokenParentChildTextSplitter,
    EstimatedTokenTextSplitter,
    ParentChildTextSplitter,
    TextChunk,
    TextSplitter,
    bounded_generated_index_text as _bounded_generated_index_text,
    bounded_heading_prefix as _bounded_heading_prefix,
    heading_prefix_budget as _heading_prefix_budget,
    estimate_mixed_cjk_latin_v1_tokens as _estimate_rag_tokens,
    with_heading_prefix as _with_heading_prefix,
)
from .source_metadata import (
    heading_path_boundary,
    heading_path_segments,
    heading_path_source_hash,
    heading_path_source_truncated,
    normalize_heading_path,
)
from .vector_store import VectorChunk


logger = logging.getLogger(__name__)


class PipelineJobCancelled(RuntimeError):
    """Raised internally after a cooperative cancellation request."""


class KnowledgePipelineExecutor:
    """Single-process executor for versioned knowledge pipeline jobs."""

    def __init__(
        self,
        service: RagService,
        *,
        run_registry: Any | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.service = service
        self.run_registry = run_registry
        self.poll_interval = max(0.1, poll_interval)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self.service.recover_pipeline_jobs()
        self._task = asyncio.create_task(self._worker(), name="knowledge-pipeline-executor")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
        self._task = None

    def notify(self) -> None:
        self._wake.set()

    async def record_job_event(
        self,
        job_id: str,
        *,
        event_type: str,
        title: str,
        summary: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._checkpoint(
            job_id,
            event_type=event_type,
            title=title,
            summary=summary,
            severity=severity,
            metadata=metadata,
        )

    async def run_once(self) -> bool:
        job = self.service.claim_next_pipeline_job()
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _worker(self) -> None:
        while not self._stopping:
            processed = await self.run_once()
            if processed:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    async def _checkpoint(
        self,
        job_id: str,
        *,
        event_type: str,
        title: str,
        summary: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.run_registry is None:
            return
        job = self.service.get_pipeline_job(job_id)
        run_id = str(job.get("run_id") or "")
        existing_run = await self.run_registry.get_run(run_id) if run_id else None
        if existing_run is None:
            previous_run_id = run_id or None
            job_status = str(job.get("status") or "queued")
            run_status = {
                "succeeded": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(job_status, "running")
            run_metadata = {
                "job_id": job_id,
                "kb_id": job["kb_id"],
                "draft_version": job["draft_version"],
            }
            if previous_run_id:
                run_metadata["recovery_of_run_id"] = previous_run_id
            run = await self.run_registry.create_run(
                "knowledge_pipeline",
                f"Knowledge pipeline: {job['kb_id']}",
                status=run_status,
                source_id=job_id,
                metadata=run_metadata,
            )
            run_id = run.run_id
            job_error = str(job.get("error") or "")
            if job_error:
                await self.run_registry.update_run(run_id, error=job_error)
            self.service.set_pipeline_job_run_id(job_id, run_id)
        await self.run_registry.record_checkpoint(
            run_id,
            event_type=event_type,
            title=title,
            summary=summary,
            severity=severity,
            metadata=dict(metadata or {}),
        )

    async def _execute(self, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        namespace = str(job["candidate_namespace"])
        try:
            await self._checkpoint(
                job_id,
                event_type="knowledge_pipeline.started",
                title="Knowledge pipeline started",
                summary=f"Processing {len(job['sources'])} source files.",
                metadata={"attempt": job["attempt"], "source_count": len(job["sources"])},
            )
            if self._job_uses_vector(job):
                self.service.vector_store.delete_knowledge_base(namespace)
            self.service.lexical_store.delete_namespace(namespace)

            await self._stage(job_id, "load", self._load_sources)
            await self._stage(job_id, "vision", self._vision_sources)
            parsed = await self._stage(job_id, "process", self._parse_sources)
            chunks = await self._stage(job_id, "chunk", self._chunk_sources, parsed)
            embeddings = await self._stage(job_id, "embed", self._embed_chunks, chunks)
            namespace = str(
                self.service.get_pipeline_job(job_id)["candidate_namespace"]
            )
            await self._stage(job_id, "store", self._store_chunks, chunks, embeddings)

            version = self.service.complete_pipeline_job(
                job_id,
                document_count=len(parsed),
                chunk_count=len(chunks),
            )
            await self._checkpoint(
                job_id,
                event_type="knowledge_pipeline.version_ready",
                title="Candidate index ready",
                summary=f"Candidate v{version['version']} contains {len(chunks)} chunks.",
                metadata={
                    "version_id": version["version_id"],
                    "version": version["version"],
                    "chunk_count": len(chunks),
                },
            )
            if self.run_registry is not None:
                run_id = str(self.service.get_pipeline_job(job_id).get("run_id") or "")
                if run_id:
                    await self.run_registry.update_run(
                        run_id,
                        status="completed",
                        metadata={"candidate_version_id": version["version_id"]},
                    )
        except PipelineJobCancelled:
            cleanup_complete = self._discard_pipeline_candidate(job_id, namespace)
            if not cleanup_complete:
                self.service.fail_pipeline_job(
                    job_id,
                    "Deletion-invalidated pipeline cleanup is pending.",
                )
            await self._checkpoint(
                job_id,
                event_type=(
                    "knowledge_pipeline.cancelled"
                    if cleanup_complete
                    else "knowledge_pipeline.cleanup_pending"
                ),
                title=(
                    "Knowledge pipeline cancelled"
                    if cleanup_complete
                    else "Knowledge pipeline cleanup pending"
                ),
                summary=(
                    "The candidate index was discarded; the active version was unchanged."
                    if cleanup_complete
                    else "The active version is unchanged; cleanup must be retried."
                ),
                severity="warning" if cleanup_complete else "error",
            )
            if self.run_registry is not None:
                run_id = str(self.service.get_pipeline_job(job_id).get("run_id") or "")
                if run_id:
                    await self.run_registry.update_run(
                        run_id,
                        status="cancelled" if cleanup_complete else "failed",
                        error=(
                            "Cancelled by user."
                            if cleanup_complete
                            else "Deletion-invalidated pipeline cleanup is pending."
                        ),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Knowledge pipeline job failed job_id=%s", job_id)
            self.service.fail_pipeline_job(job_id, str(exc))
            cleanup_complete = self._discard_pipeline_candidate(job_id, namespace)
            if not cleanup_complete:
                self.service.fail_pipeline_job(
                    job_id,
                    "Deletion-invalidated pipeline cleanup is pending.",
                )
            await self._checkpoint(
                job_id,
                event_type="knowledge_pipeline.failed",
                title="Knowledge pipeline failed",
                summary=(
                    str(exc)
                    if cleanup_complete
                    else "Deletion-invalidated pipeline cleanup is pending."
                ),
                severity="error",
            )
            if self.run_registry is not None:
                run_id = str(self.service.get_pipeline_job(job_id).get("run_id") or "")
                if run_id:
                    await self.run_registry.update_run(
                        run_id,
                        status="failed",
                        error=(
                            str(exc)
                            if cleanup_complete
                            else "Deletion-invalidated pipeline cleanup is pending."
                        ),
                    )

    def _discard_pipeline_candidate(self, job_id: str, namespace: str) -> bool:
        try:
            job = self.service.get_pipeline_job(job_id)
            if self._job_uses_vector(job):
                self.service.vector_store.delete_knowledge_base(namespace)
            self.service.lexical_store.delete_namespace(namespace)
            self.service.cleanup_invalidated_pipeline_job(job_id)
        except Exception:
            logger.warning(
                "Knowledge pipeline cleanup remains pending job_id=%s",
                job_id,
            )
            return False
        return True

    @staticmethod
    def _job_uses_vector(job: dict[str, Any]) -> bool:
        snapshot = job.get("config_snapshot")
        retrieval = snapshot.get("retrieval_profile") if isinstance(snapshot, dict) else None
        return str((retrieval or {}).get("mode") or "hybrid") in {"vector", "hybrid"}

    @staticmethod
    def _job_uses_fulltext(job: dict[str, Any]) -> bool:
        snapshot = job.get("config_snapshot")
        retrieval = snapshot.get("retrieval_profile") if isinstance(snapshot, dict) else None
        return str((retrieval or {}).get("mode") or "hybrid") in {
            "fulltext",
            "hybrid",
        }

    async def _stage(
        self,
        job_id: str,
        stage_id: str,
        operation: Callable[..., Awaitable[Any]],
        *args: Any,
    ) -> Any:
        if self.service.pipeline_job_cancel_requested(job_id):
            self.service.cancel_running_pipeline_job(job_id)
            raise PipelineJobCancelled("Knowledge pipeline job was cancelled.")
        self.service.start_pipeline_job_stage(job_id, stage_id)
        await self._checkpoint(
            job_id,
            event_type=f"knowledge_pipeline.{stage_id}.started",
            title=f"{stage_id.title()} stage started",
            metadata={"stage": stage_id},
        )
        result = await operation(job_id, *args)
        if self.service.pipeline_job_cancel_requested(job_id):
            self.service.cancel_running_pipeline_job(job_id)
            raise PipelineJobCancelled("Knowledge pipeline job was cancelled.")
        count = len(result) if isinstance(result, (list, tuple)) else None
        self.service.complete_pipeline_job_stage(job_id, stage_id, item_count=count)
        await self._checkpoint(
            job_id,
            event_type=f"knowledge_pipeline.{stage_id}.completed",
            title=f"{stage_id.title()} stage completed",
            metadata={"stage": stage_id, "item_count": count},
        )
        return result

    async def _load_sources(self, job_id: str) -> list[dict[str, Any]]:
        return self.service.load_pipeline_job_sources(job_id)

    async def _vision_sources(self, job_id: str) -> list[dict[str, Any]]:
        processed = await self.service.process_pipeline_job_vision(job_id)
        job = self.service.get_pipeline_job(job_id)
        for result in job.get("document_results", []):
            if not isinstance(result, dict):
                continue
            status = str(result.get("vision_status") or "skipped")
            if status not in {"completed", "failed"}:
                continue
            await self._checkpoint(
                job_id,
                event_type=f"knowledge_pipeline.vision.{status}",
                title=(
                    "Visual understanding completed"
                    if status == "completed"
                    else "Visual understanding completed with failures"
                ),
                summary=str(result.get("vision_error") or ""),
                severity="warning" if status == "failed" else "info",
                metadata={
                    "source_id": result.get("source_id"),
                    "selected_page_count": result.get("vision_selected_page_count", 0),
                    "processed_page_count": result.get("vision_processed_page_count", 0),
                    "failed_page_count": result.get("vision_failed_page_count", 0),
                    "block_count": result.get("vision_block_count", 0),
                    "attempt": result.get("vision_attempt", 0),
                },
            )
        return processed

    async def _parse_sources(
        self,
        job_id: str,
    ) -> list[dict[str, Any]]:
        processed = await self.service.process_pipeline_job_sources(job_id)
        job = self.service.get_pipeline_job(job_id)
        for result in job.get("document_results", []):
            if not isinstance(result, dict):
                continue
            status = str(result.get("status") or "pending")
            if status not in {"completed", "failed"}:
                continue
            await self._checkpoint(
                job_id,
                event_type=f"knowledge_pipeline.document.{status}",
                title=(
                    "Document processing completed"
                    if status == "completed"
                    else "Document processing failed"
                ),
                summary=str(result.get("error") or ""),
                severity="error" if status == "failed" else "info",
                metadata={
                    "source_id": result.get("source_id"),
                    "mode": job.get("config_snapshot", {})
                    .get("processor_profile", {})
                    .get("mode", "general"),
                    "attempt": result.get("attempt", 0),
                    "block_count": result.get("block_count", 0),
                    "generated_count": result.get("generated_count", 0),
                    "duration_ms": result.get("duration_ms"),
                },
            )
        gate_error = self.service.processor_gate_error(job_id)
        if gate_error:
            raise RuntimeError(gate_error)
        return processed

    async def _chunk_sources(
        self,
        job_id: str,
        parsed: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        job = self.service.get_pipeline_job(job_id)
        snapshot = job["config_snapshot"]
        stages = snapshot.get("stages", snapshot)
        chunker = stages["stage_chunker"]
        strategy = str(chunker.get("strategy") or "recursive_character")
        if strategy == "local_recursive_character_chunks":
            strategy = "recursive_character"
        token_strategy = strategy in {
            "recursive_estimated_token",
            "parent_child_estimated_token",
        }
        legacy_splitter: TextSplitter | ParentChildTextSplitter | None = None
        if strategy == "parent_child":
            legacy_splitter = ParentChildTextSplitter(
                parent_chunk_size=int(chunker.get("parent_chunk_size", 1500)),
                parent_chunk_overlap=int(chunker.get("parent_chunk_overlap", 100)),
                child_chunk_size=int(chunker.get("child_chunk_size", 400)),
                child_chunk_overlap=int(chunker.get("child_chunk_overlap", 50)),
                parent_separators=(
                    list(chunker["parent_separators"])
                    if chunker.get("parent_separators")
                    else None
                ),
                child_separators=(
                    list(chunker["child_separators"])
                    if chunker.get("child_separators")
                    else None
                ),
            )
        elif strategy == "recursive_character":
            legacy_splitter = TextSplitter(
                chunk_size=int(chunker["chunk_size"]),
                chunk_overlap=int(chunker["chunk_overlap"]),
                separators=list(chunker["separators"]) if chunker.get("separators") else None,
            )
        chunks: list[dict[str, Any]] = []
        receipt: dict[str, Any] = {
            "receipt_version": CHUNKING_RECEIPT_VERSION,
            "contract_version": str(
                chunker.get("chunk_contract_version") or "legacy-character-v1"
            ),
            "strategy": strategy,
            "size_unit": str(chunker.get("size_unit") or "characters"),
            "token_estimator": (
                str(chunker.get("token_estimator") or "") or None
            ),
            "chunker_profile_fingerprint": chunker_profile_fingerprint(chunker),
            "candidate_version_id": str(job.get("candidate_version_id") or ""),
            "candidate_namespace_fingerprint": candidate_namespace_fingerprint(
                job.get("candidate_namespace")
            ),
            "raw_candidate_count": 0,
            "heading_block_count": 0,
            "heading_prefix_truncated_count": 0,
            "generated_item_count": 0,
            "generated_item_chunk_count": 0,
            "generated_item_rejected_count": 0,
            "generated_item_rejection_reasons": {},
            "deduplicated_chunk_count": 0,
            "final_chunk_count": 0,
        }
        for source in parsed:
            source_id = str(source["source_id"])
            generated_items = source.get("generated_items")
            if isinstance(generated_items, list) and generated_items:
                blocks = {
                    str(block.get("block_id")): block
                    for block in source.get("processed_document", {}).get("blocks", [])
                    if isinstance(block, dict)
                }
                for generated in generated_items:
                    if not isinstance(generated, dict):
                        continue
                    receipt["generated_item_count"] += 1
                    try:
                        parent_chunk_id, source_block_ids_tuple = (
                            generated_parent_identity(source_id, generated, blocks)
                        )
                    except ValueError:
                        _record_chunk_rejection(
                            receipt,
                            "source_block_provenance_invalid",
                        )
                        continue
                    source_block_ids = list(source_block_ids_tuple)
                    source_blocks = [blocks[block_id] for block_id in source_block_ids]
                    page_numbers = {
                        int(block["page_number"])
                        for block in source_blocks
                        if block.get("page_number") is not None
                    }
                    slides = {
                        int(block.get("metadata", {}).get("slide"))
                        for block in source_blocks
                        if isinstance(block.get("metadata"), dict)
                        and block.get("metadata", {}).get("slide") is not None
                    }
                    source_heading_paths = [
                        heading_path_segments(block.get("heading_path"))
                        for block in source_blocks
                    ]
                    source_heading_hashes = [
                        str(block.get("heading_path_source_hash") or "")
                        or heading_path_source_hash(block.get("heading_path"))
                        for block in source_blocks
                    ]
                    source_heading_was_truncated = any(
                        bool(block.get("heading_path_source_truncated"))
                        or heading_path_source_truncated(block.get("heading_path"))
                        for block in source_blocks
                    )
                    sheets = {
                        str(block.get("metadata", {}).get("sheet"))
                        for block in source_blocks
                        if isinstance(block.get("metadata"), dict)
                        and block.get("metadata", {}).get("sheet")
                    }
                    row_ranges = {
                        str(block.get("metadata", {}).get("row_range"))
                        for block in source_blocks
                        if isinstance(block.get("metadata"), dict)
                        and block.get("metadata", {}).get("row_range")
                    }
                    visual_kinds = {
                        str(block.get("kind") or "")
                        for block in source_blocks
                        if str(block.get("kind") or "").startswith(("image_", "visual_"))
                    }
                    source_heading_path = ()
                    heading_path = ()
                    if (
                        source_heading_paths
                        and all(source_heading_paths)
                        and all(source_heading_hashes)
                        and len(set(source_heading_hashes)) == 1
                    ):
                        source_heading_path = source_heading_paths[0]
                        heading_path = normalize_heading_path(source_heading_path)
                    if token_strategy:
                        index_budget = int(
                            chunker.get("child_chunk_size", 400)
                            if strategy == "parent_child_estimated_token"
                            else chunker.get("chunk_size", 500)
                        )
                        context_budget = int(
                            chunker.get("parent_chunk_size", 1500)
                            if strategy == "parent_child_estimated_token"
                            else chunker.get("chunk_size", 500)
                        )
                        index_overlap = int(
                            chunker.get("child_chunk_overlap", 50)
                            if strategy == "parent_child_estimated_token"
                            else chunker.get("chunk_overlap", 50)
                        )
                        context_overlap = int(
                            chunker.get("parent_chunk_overlap", 100)
                            if strategy == "parent_child_estimated_token"
                            else chunker.get("chunk_overlap", 50)
                        )
                        prefix_input = (
                            heading_path_boundary(source_heading_path)
                            if source_heading_was_truncated and source_heading_path
                            else source_heading_path
                        )
                        prefix, budget_truncated = _bounded_heading_prefix(
                            prefix_input,
                            budget=_heading_prefix_budget(
                                index_budget=index_budget,
                                index_overlap=index_overlap,
                                context_budget=context_budget,
                                context_overlap=context_overlap,
                            ),
                        )
                        prefix_truncated = (
                            source_heading_was_truncated or budget_truncated
                        )
                        if prefix_truncated:
                            receipt["heading_prefix_truncated_count"] += 1
                        index_text = _with_heading_prefix(
                            prefix,
                            str(generated.get("index_text") or "").strip(),
                        )
                        if not index_text or _estimate_rag_tokens(index_text) > index_budget:
                            _record_chunk_rejection(receipt, "index_text_over_budget")
                            continue
                        context_body = str(generated.get("context_text") or "")
                        prefix_tokens = _estimate_rag_tokens(prefix + "\n") if prefix else 0
                        if strategy == "parent_child_estimated_token":
                            parent_body_budget = max(1, context_budget - prefix_tokens)
                            child_body_budget = max(1, index_budget - prefix_tokens)
                            context_segments = EstimatedTokenParentChildTextSplitter(
                                parent_chunk_size=parent_body_budget,
                                parent_chunk_overlap=min(
                                    context_overlap,
                                    parent_body_budget - 1,
                                ),
                                child_chunk_size=child_body_budget,
                                child_chunk_overlap=min(
                                    index_overlap,
                                    child_body_budget - 1,
                                ),
                                parent_separators=(
                                    list(chunker.get("parent_separators") or []) or None
                                ),
                                child_separators=(
                                    list(chunker.get("child_separators") or []) or None
                                ),
                            ).split_segments(context_body)
                        else:
                            body_budget = max(
                                1,
                                min(index_budget, context_budget) - prefix_tokens,
                            )
                            body_overlap = min(context_overlap, body_budget - 1)
                            context_segments = EstimatedTokenTextSplitter(
                                chunk_size=body_budget,
                                chunk_overlap=max(0, body_overlap),
                                separators=(
                                    list(chunker.get("separators") or []) or None
                                ),
                            ).split_segments(context_body)
                        if not context_segments:
                            _record_chunk_rejection(receipt, "context_text_empty")
                            continue
                    else:
                        index_text = str(generated.get("index_text") or "")
                        context_segments = [
                            TextChunk(
                                text=str(generated.get("context_text") or ""),
                                index=0,
                                start_char=0,
                                end_char=len(str(generated.get("context_text") or "")),
                            )
                        ]
                        prefix = ""
                    for context_segment in context_segments:
                        context_text = _with_heading_prefix(
                            prefix,
                            context_segment.parent_text or context_segment.text,
                        )
                        local_parent_id = context_segment.parent_chunk_id
                        if (
                            not local_parent_id
                            and token_strategy
                            and len(context_segments) > 1
                        ):
                            local_parent_id = f"segment_{context_segment.index}"
                        segment_parent_chunk_id = (
                            generated_parent_window_identity(
                                parent_chunk_id,
                                local_parent_id,
                                context_text,
                            )
                            if local_parent_id
                            else parent_chunk_id
                        )
                        if token_strategy:
                            segment_index_text = _bounded_generated_index_text(
                                index_text,
                                context_segment.text,
                                budget=index_budget,
                            )
                        else:
                            segment_index_text = index_text
                        mapping_text = (
                            context_segment.parent_text
                            or context_segment.text
                        )
                        mapping_start = (
                            context_segment.parent_start_char
                            if context_segment.parent_text is not None
                            and context_segment.parent_start_char is not None
                            else context_segment.start_char
                        )
                        mapping_end = (
                            context_segment.parent_end_char
                            if context_segment.parent_text is not None
                            and context_segment.parent_end_char is not None
                            else context_segment.end_char
                        )
                        source_mapping = generated_segment_source_mapping(
                            mapping_text,
                            source_blocks,
                            segment_start=mapping_start,
                            segment_end=mapping_end,
                            context_source_ranges=generated.get(
                                "context_source_ranges"
                            ),
                        )
                        mapped_source_blocks = [
                            blocks[block_id]
                            for block_id in source_mapping.source_block_ids
                            if block_id in blocks
                        ]
                        mapped_page_numbers = {
                            int(block["page_number"])
                            for block in mapped_source_blocks
                            if block.get("page_number") is not None
                        }
                        mapped_slides = {
                            int(block.get("metadata", {}).get("slide"))
                            for block in mapped_source_blocks
                            if isinstance(block.get("metadata"), dict)
                            and block.get("metadata", {}).get("slide") is not None
                        }
                        mapped_heading_path = heading_path
                        mapped_sheets = {
                            str(block.get("metadata", {}).get("sheet"))
                            for block in mapped_source_blocks
                            if isinstance(block.get("metadata"), dict)
                            and block.get("metadata", {}).get("sheet")
                        }
                        mapped_row_ranges = {
                            str(block.get("metadata", {}).get("row_range"))
                            for block in mapped_source_blocks
                            if isinstance(block.get("metadata"), dict)
                            and block.get("metadata", {}).get("row_range")
                        }
                        mapped_visual_kinds = {
                            str(block.get("kind") or "")
                            for block in mapped_source_blocks
                            if str(block.get("kind") or "").startswith(
                                ("image_", "visual_")
                            )
                        }
                        mapped_block = blocks.get(
                            str(source_mapping.source_block_id or "")
                        )
                        chunks.append(
                            {
                                "source": source,
                                "index": 0,
                                "index_text": segment_index_text,
                                "context_text": context_text,
                                "start_char": source_mapping.start_char,
                                "end_char": source_mapping.end_char,
                                "chunk_type": str(generated.get("item_type") or "generated"),
                                "generated_item": True,
                                "parent_chunk_id": segment_parent_chunk_id,
                                "page_number": (
                                    next(iter(mapped_page_numbers))
                                    if len(mapped_page_numbers) == 1
                                    else None
                                ),
                                "slide": (
                                    next(iter(mapped_slides))
                                    if len(mapped_slides) == 1
                                    else None
                                ),
                                "heading_path": mapped_heading_path,
                                "sheet": (
                                    next(iter(mapped_sheets))
                                    if len(mapped_sheets) == 1
                                    else None
                                ),
                                "row_range": (
                                    next(iter(mapped_row_ranges))
                                    if len(mapped_row_ranges) == 1
                                    else None
                                ),
                                "visual_kind": (
                                    next(iter(mapped_visual_kinds))
                                    if len(mapped_visual_kinds) == 1
                                    else None
                                ),
                                "source_block_id": source_mapping.source_block_id,
                                "source_block_ids": list(
                                    source_mapping.source_block_ids
                                ),
                                "source_block_hash": (
                                    _source_block_hash(mapped_block.get("text"))
                                    if isinstance(mapped_block, dict)
                                    else None
                                ),
                                "source_block_match_status": source_mapping.status,
                            }
                        )
                        receipt["raw_candidate_count"] += 1
                continue

            document = source.get("processed_document")
            raw_blocks = document.get("blocks", []) if isinstance(document, dict) else []
            for block in raw_blocks:
                if not isinstance(block, dict):
                    continue
                raw_block_text = str(block.get("text") or "")
                if not raw_block_text.strip():
                    continue
                if str(block.get("kind") or "") == "heading":
                    receipt["heading_block_count"] += 1
                    continue
                source_heading_path = heading_path_segments(block.get("heading_path"))
                heading_path = list(normalize_heading_path(source_heading_path))
                source_heading_was_truncated = (
                    bool(block.get("heading_path_source_truncated"))
                    or heading_path_source_truncated(block.get("heading_path"))
                )
                block_metadata = (
                    block.get("metadata")
                    if isinstance(block.get("metadata"), dict)
                    else {}
                )
                if token_strategy:
                    index_budget = int(
                        chunker.get("child_chunk_size", 400)
                        if strategy == "parent_child_estimated_token"
                        else chunker.get("chunk_size", 500)
                    )
                    context_budget = int(
                        chunker.get("parent_chunk_size", 1500)
                        if strategy == "parent_child_estimated_token"
                        else chunker.get("chunk_size", 500)
                    )
                    index_overlap = int(
                        chunker.get("child_chunk_overlap", 50)
                        if strategy == "parent_child_estimated_token"
                        else chunker.get("chunk_overlap", 50)
                    )
                    context_overlap = int(
                        chunker.get("parent_chunk_overlap", 100)
                        if strategy == "parent_child_estimated_token"
                        else chunker.get("chunk_overlap", 50)
                    )
                    prefix_input = (
                        heading_path_boundary(source_heading_path)
                        if source_heading_was_truncated and source_heading_path
                        else source_heading_path
                    )
                    heading_prefix, budget_truncated = _bounded_heading_prefix(
                        prefix_input,
                        budget=_heading_prefix_budget(
                            index_budget=index_budget,
                            index_overlap=index_overlap,
                            context_budget=context_budget,
                            context_overlap=context_overlap,
                        ),
                    )
                    prefix_truncated = (
                        source_heading_was_truncated or budget_truncated
                    )
                    if prefix_truncated:
                        receipt["heading_prefix_truncated_count"] += 1
                    prefix_tokens = (
                        _estimate_rag_tokens(heading_prefix + "\n")
                        if heading_prefix
                        else 0
                    )
                    if strategy == "parent_child_estimated_token":
                        splitter = EstimatedTokenParentChildTextSplitter(
                            parent_chunk_size=max(1, context_budget - prefix_tokens),
                            parent_chunk_overlap=context_overlap,
                            child_chunk_size=max(1, index_budget - prefix_tokens),
                            child_chunk_overlap=index_overlap,
                            parent_separators=(
                                list(chunker["parent_separators"])
                                if chunker.get("parent_separators")
                                else None
                            ),
                            child_separators=(
                                list(chunker["child_separators"])
                                if chunker.get("child_separators")
                                else None
                            ),
                        )
                    else:
                        splitter = EstimatedTokenTextSplitter(
                            chunk_size=max(1, index_budget - prefix_tokens),
                            chunk_overlap=index_overlap,
                            separators=(
                                list(chunker["separators"])
                                if chunker.get("separators")
                                else None
                            ),
                        )
                else:
                    splitter = legacy_splitter
                    heading_prefix = " > ".join(heading_path)
                if splitter is None:
                    raise RuntimeError(f"Unsupported chunking strategy: {strategy}")
                for segment in splitter.split_segments(raw_block_text):
                    index_text = _with_heading_prefix(heading_prefix, segment.text)
                    context_text = _with_heading_prefix(
                        heading_prefix,
                        segment.parent_text or segment.text,
                    )
                    parent_id = (
                        f"{source_id}_{block.get('block_id')}_{segment.parent_chunk_id}"
                        if segment.parent_chunk_id
                        else None
                    )
                    chunks.append(
                        {
                            "source": source,
                            "index": 0,
                            "index_text": index_text,
                            "context_text": context_text,
                            "start_char": int(block.get("start_char", 0))
                            + segment.start_char,
                            "end_char": int(block.get("start_char", 0))
                            + segment.end_char,
                            "chunk_type": (
                                segment.chunk_type
                                if segment.chunk_type != "standard"
                                else str(block.get("kind") or "standard")
                            ),
                            "parent_chunk_id": parent_id,
                            "page_number": block.get("page_number"),
                            "slide": block_metadata.get("slide"),
                            "heading_path": tuple(heading_path),
                            "sheet": block_metadata.get("sheet"),
                            "row_range": block_metadata.get("row_range"),
                            "visual_kind": (
                                str(block.get("kind"))
                                if str(block.get("kind") or "").startswith(("image_", "visual_"))
                                else None
                            ),
                            "source_block_id": block.get("block_id"),
                            "source_block_hash": _source_block_hash(raw_block_text),
                        }
                    )
                    receipt["raw_candidate_count"] += 1
        deduplicated: list[dict[str, Any]] = []
        seen_content: dict[tuple[str, str, str], int] = {}
        for item in chunks:
            source_id = str((item.get("source") or {}).get("source_id") or "")
            scope_id = str(
                item.get("source_block_id")
                or item.get("parent_chunk_id")
                or "document"
            )
            content_hash = (
                _normalized_chunk_payload_hash(
                    str(item.get("index_text") or ""),
                    str(item.get("context_text") or ""),
                )
                if item.get("generated_item") is True
                else _normalized_chunk_content_hash(str(item.get("index_text") or ""))
            )
            dedupe_key = (source_id, scope_id, content_hash)
            existing_index = seen_content.get(dedupe_key)
            if existing_index is not None:
                receipt["deduplicated_chunk_count"] += 1
                existing = deduplicated[existing_index]
                if (
                    item.get("generated_item") is not True
                    and (
                        int(item.get("start_char") or 0),
                        int(item.get("end_char") or 0),
                    )
                    < (
                        int(existing.get("start_char") or 0),
                        int(existing.get("end_char") or 0),
                    )
                ):
                    deduplicated[existing_index] = item
                continue
            seen_content[dedupe_key] = len(deduplicated)
            deduplicated.append(item)
        chunks = deduplicated
        parent_contexts: dict[tuple[str, str], str] = {}
        for item in chunks:
            parent_chunk_id = str(item.get("parent_chunk_id") or "")
            if not parent_chunk_id:
                continue
            source_id = str((item.get("source") or {}).get("source_id") or "")
            context_text = str(item.get("context_text") or "")
            parent_key = (source_id, parent_chunk_id)
            previous_context = parent_contexts.setdefault(parent_key, context_text)
            if previous_context != context_text:
                raise RuntimeError(
                    "A parent chunk identity resolved to multiple context windows."
                )
        receipt["generated_item_chunk_count"] = sum(
            1 for item in chunks if item.get("generated_item") is True
        )
        per_source_counts: dict[str, int] = {}
        candidate_version_id = str(job.get("candidate_version_id") or "")
        if not candidate_version_id:
            raise RuntimeError("Pipeline job is missing its candidate version identity.")
        for item in chunks:
            source_id = str((item.get("source") or {}).get("source_id") or "")
            item["index"] = per_source_counts.get(source_id, 0)
            item["chunk_index"] = int(item["index"])
            item["chunk_id"] = (
                f"{candidate_version_id}_{source_id}_chunk_{item['chunk_index']}"
            )
            per_source_counts[source_id] = int(item["index"]) + 1
        receipt["final_chunk_count"] = len(chunks)
        receipt["chunk_sequence_hash"] = canonical_chunk_sequence_hash(chunks)
        self.service.update_pipeline_chunking_receipt(job_id, receipt)
        if not chunks:
            raise RuntimeError("No indexable text chunks were produced.")
        self.service.update_pipeline_document_chunk_counts(job_id, per_source_counts)
        return chunks

    async def _embed_chunks(
        self,
        job_id: str,
        chunks: list[dict[str, Any]],
    ) -> list[list[float]]:
        job = self.service.get_pipeline_job(job_id)
        if not self._job_uses_vector(job):
            return []
        snapshot = job["config_snapshot"]
        profile = snapshot.get("embedding_profile", {}) if isinstance(snapshot, dict) else {}
        effective = profile.get("effective") if isinstance(profile, dict) else None
        effective_profile = effective if isinstance(effective, dict) else profile
        access_mode = str(effective_profile.get("access_mode") or "legacy")
        provider = str(effective_profile.get("provider") or profile.get("provider") or "")
        dimension = int(
            effective_profile.get("dimension")
            or profile.get("dimension")
            or self.service.embedder.dimension
        )
        model = str(
            effective_profile.get("model")
            or profile.get("model")
            or self.service.embedder.model
        )
        texts = [str(item.get("index_text") or "") for item in chunks]
        if access_mode == "managed":
            embeddings = await self.service.embed_managed_pipeline_chunks(
                job_id,
                texts,
            )
        else:
            embedder = self.service.embedder
            if (
                provider == "hash"
                and (
                    bool(self.service.embedder.api_key)
                    or self.service.embedder.dimension != dimension
                )
            ):
                embedder = EmbeddingClient(
                    api_base="",
                    api_key="",
                    model=model,
                    dimension=dimension,
                )
                embedder.api_key = ""
                embedder.embedding_mode = "hash"
            elif provider != "hash" and model != self.service.embedder.model:
                embedder = EmbeddingClient(
                    api_base=self.service.embedder.api_base,
                    api_key=self.service.embedder.api_key,
                    model=model,
                    dimension=dimension,
                )
            embeddings = await embedder.embed_texts(texts)
        if len(embeddings) != len(chunks):
            raise EmbeddingError(
                "Embedding provider returned a different number of vectors than inputs."
            )
        dimensions = {len(vector) for vector in embeddings}
        if not dimensions or 0 in dimensions or len(dimensions) != 1:
            raise EmbeddingError(
                "Embedding provider returned empty or inconsistent vector dimensions."
            )
        actual_dimension = next(iter(dimensions))
        self.service.update_pipeline_embedding_dimension(job_id, actual_dimension)
        return embeddings

    async def _store_chunks(
        self,
        job_id: str,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        job = self.service.get_pipeline_job(job_id)
        version_id = str(job["candidate_version_id"])
        namespace = str(job["candidate_namespace"])
        vector_chunks: list[VectorChunk] = []
        lexical_chunks: list[LexicalChunk] = []
        uses_vector = self._job_uses_vector(job)
        uses_fulltext = self._job_uses_fulltext(job)
        if uses_vector and len(embeddings) != len(chunks):
            raise EmbeddingError(
                "Vector index input does not match the embedded chunk count."
            )
        for position, item in enumerate(chunks):
            source = item["source"]
            source_id = str(source["source_id"])
            doc_id = f"{version_id}_{source_id}"
            chunk_index = int(item["index"])
            chunk_id = f"{doc_id}_chunk_{chunk_index}"
            if (
                int(item.get("chunk_index", -1)) != chunk_index
                or str(item.get("chunk_id") or "") != chunk_id
            ):
                raise RuntimeError(
                    "Chunk identity no longer matches its receipt-bound sequence."
                )
            common = {
                "parent_chunk_id": item.get("parent_chunk_id"),
                "parent_text": (
                    str(item.get("context_text") or "")
                    if item.get("parent_chunk_id")
                    else None
                ),
                "chunk_type": str(item.get("chunk_type") or "standard"),
                "start_char": int(item.get("start_char", 0)),
                "end_char": int(item.get("end_char", 0)),
                "page_number": item.get("page_number"),
                "slide": item.get("slide"),
                "heading_path": normalize_heading_path(item.get("heading_path")),
                "sheet": item.get("sheet"),
                "row_range": item.get("row_range"),
                "visual_kind": item.get("visual_kind"),
                "source_block_id": item.get("source_block_id"),
                "source_block_hash": item.get("source_block_hash"),
            }
            if uses_vector:
                vector_chunks.append(
                    VectorChunk(
                        id=chunk_id,
                        kb_id=namespace,
                        doc_id=doc_id,
                        document_name=str(source["filename"]),
                        text=str(item.get("index_text") or ""),
                        embedding=embeddings[position],
                        chunk_index=chunk_index,
                        source_block_ids=tuple(
                            str(block_id)
                            for block_id in (item.get("source_block_ids") or [])
                            if str(block_id)
                        ),
                        generated_item=item.get("generated_item") is True,
                        **common,
                    )
                )
            if uses_fulltext:
                lexical_chunks.append(
                    LexicalChunk(
                        chunk_id=chunk_id,
                        namespace=namespace,
                        doc_id=doc_id,
                        document_name=str(source["filename"]),
                        text=str(item.get("index_text") or ""),
                        chunk_index=chunk_index,
                        **common,
                    )
                )
        if uses_vector:
            self.service.vector_store.add_chunks(vector_chunks)
            count_namespace = getattr(self.service.vector_store, "count_namespace", None)
            if not callable(count_namespace):
                raise RuntimeError("Vector backend cannot verify the stored index count.")
            if int(count_namespace(namespace)) != len(vector_chunks):
                raise RuntimeError("Vector index count does not match the embedded chunk count.")
        if uses_fulltext:
            self.service.lexical_store.add_chunks(lexical_chunks)
        if uses_fulltext and self.service.lexical_store.count_namespace(namespace) != len(lexical_chunks):
            raise RuntimeError("Full-text index count does not match the vector candidate index.")


def _source_block_hash(value: Any) -> str | None:
    return canonical_source_text_hash(value)


def _normalized_chunk_content_hash(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", text).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalized_chunk_payload_hash(index_text: str, context_text: str) -> str:
    payload = {
        "index_text": " ".join(unicodedata.normalize("NFKC", index_text).split()),
        "context_text": " ".join(unicodedata.normalize("NFKC", context_text).split()),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _record_chunk_rejection(receipt: dict[str, Any], reason: str) -> None:
    receipt["generated_item_rejected_count"] = int(
        receipt.get("generated_item_rejected_count") or 0
    ) + 1
    reasons = receipt.setdefault("generated_item_rejection_reasons", {})
    reasons[reason] = int(reasons.get(reason) or 0) + 1
