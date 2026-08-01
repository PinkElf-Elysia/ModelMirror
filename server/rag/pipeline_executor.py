from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .embedder import EmbeddingClient
from .lexical_store import LexicalChunk
from .rag_service import RagService
from .splitter import ParentChildTextSplitter, TextSplitter
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
            self.service.vector_store.delete_knowledge_base(namespace)
            self.service.lexical_store.delete_namespace(namespace)

            await self._stage(job_id, "load", self._load_sources)
            await self._stage(job_id, "vision", self._vision_sources)
            parsed = await self._stage(job_id, "process", self._parse_sources)
            chunks = await self._stage(job_id, "chunk", self._chunk_sources, parsed)
            embeddings = await self._stage(job_id, "embed", self._embed_chunks, chunks)
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
            self.service.vector_store.delete_knowledge_base(namespace)
            self.service.lexical_store.delete_namespace(namespace)
            await self._checkpoint(
                job_id,
                event_type="knowledge_pipeline.cancelled",
                title="Knowledge pipeline cancelled",
                summary="The candidate index was discarded; the active version was unchanged.",
                severity="warning",
            )
            if self.run_registry is not None:
                run_id = str(self.service.get_pipeline_job(job_id).get("run_id") or "")
                if run_id:
                    await self.run_registry.update_run(
                        run_id,
                        status="cancelled",
                        error="Cancelled by user.",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Knowledge pipeline job failed job_id=%s", job_id)
            self.service.vector_store.delete_knowledge_base(namespace)
            self.service.lexical_store.delete_namespace(namespace)
            self.service.fail_pipeline_job(job_id, str(exc))
            await self._checkpoint(
                job_id,
                event_type="knowledge_pipeline.failed",
                title="Knowledge pipeline failed",
                summary=str(exc),
                severity="error",
            )
            if self.run_registry is not None:
                run_id = str(self.service.get_pipeline_job(job_id).get("run_id") or "")
                if run_id:
                    await self.run_registry.update_run(run_id, status="failed", error=str(exc))

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
        if strategy == "parent_child":
            splitter: TextSplitter | ParentChildTextSplitter = ParentChildTextSplitter(
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
        else:
            splitter = TextSplitter(
                chunk_size=int(chunker["chunk_size"]),
                chunk_overlap=int(chunker["chunk_overlap"]),
                separators=list(chunker["separators"]) if chunker.get("separators") else None,
            )
        chunks: list[dict[str, Any]] = []
        per_source_counts: dict[str, int] = {}
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
                    source_blocks = [
                        blocks[str(block_id)]
                        for block_id in generated.get("source_block_ids", [])
                        if str(block_id) in blocks
                    ]
                    start_char = min(
                        (int(block.get("start_char", 0)) for block in source_blocks),
                        default=0,
                    )
                    end_char = max(
                        (int(block.get("end_char", 0)) for block in source_blocks),
                        default=0,
                    )
                    page_numbers = {
                        int(block["page_number"])
                        for block in source_blocks
                        if block.get("page_number") is not None
                    }
                    visual_kinds = {
                        str(block.get("kind") or "")
                        for block in source_blocks
                        if str(block.get("kind") or "").startswith(("image_", "visual_"))
                    }
                    index = per_source_counts.get(source_id, 0)
                    per_source_counts[source_id] = index + 1
                    chunks.append(
                        {
                            "source": source,
                            "index": index,
                            "index_text": str(generated.get("index_text") or ""),
                            "context_text": str(generated.get("context_text") or ""),
                            "start_char": start_char,
                            "end_char": end_char,
                            "chunk_type": str(generated.get("item_type") or "generated"),
                            "parent_chunk_id": (
                                f"{source_id}_{generated.get('item_id', index)}"
                            ),
                            "page_number": next(iter(page_numbers)) if len(page_numbers) == 1 else None,
                            "visual_kind": next(iter(visual_kinds)) if len(visual_kinds) == 1 else None,
                            "source_block_id": (
                                str(source_blocks[0].get("block_id"))
                                if len(source_blocks) == 1
                                else None
                            ),
                        }
                    )
                continue

            document = source.get("processed_document")
            raw_blocks = document.get("blocks", []) if isinstance(document, dict) else []
            for block in raw_blocks:
                if not isinstance(block, dict):
                    continue
                block_text = str(block.get("text") or "").strip()
                if not block_text:
                    continue
                heading_path = [
                    str(item).strip()
                    for item in block.get("heading_path", [])
                    if str(item).strip()
                ]
                for segment in splitter.split_segments(block_text):
                    index = per_source_counts.get(source_id, 0)
                    per_source_counts[source_id] = index + 1
                    heading_prefix = " > ".join(heading_path)
                    index_text = (
                        f"{heading_prefix}\n{segment.text}"
                        if heading_prefix and heading_prefix not in segment.text
                        else segment.text
                    )
                    parent_id = (
                        f"{source_id}_{block.get('block_id')}_{segment.parent_chunk_id}"
                        if segment.parent_chunk_id
                        else None
                    )
                    chunks.append(
                        {
                            "source": source,
                            "index": index,
                            "index_text": index_text,
                            "context_text": segment.parent_text or segment.text,
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
                            "visual_kind": (
                                str(block.get("kind"))
                                if str(block.get("kind") or "").startswith(("image_", "visual_"))
                                else None
                            ),
                            "source_block_id": block.get("block_id"),
                        }
                    )
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
        snapshot = job["config_snapshot"]
        profile = snapshot.get("embedding_profile", {}) if isinstance(snapshot, dict) else {}
        model = str(profile.get("model") or self.service.embedder.model)
        embedder = self.service.embedder
        if model != self.service.embedder.model:
            embedder = EmbeddingClient(
                api_base=self.service.embedder.api_base,
                api_key=self.service.embedder.api_key,
                model=model,
                dimension=self.service.embedder.dimension,
            )
        return await embedder.embed_texts(
            [str(item.get("index_text") or "") for item in chunks]
        )

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
        for position, item in enumerate(chunks):
            source = item["source"]
            source_id = str(source["source_id"])
            doc_id = f"{version_id}_{source_id}"
            chunk_id = f"{doc_id}_chunk_{item['index']}"
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
                "visual_kind": item.get("visual_kind"),
                "source_block_id": item.get("source_block_id"),
            }
            vector_chunks.append(
                VectorChunk(
                    id=chunk_id,
                    kb_id=namespace,
                    doc_id=doc_id,
                    document_name=str(source["filename"]),
                    text=str(item.get("index_text") or ""),
                    embedding=embeddings[position],
                    chunk_index=int(item["index"]),
                    **common,
                )
            )
            lexical_chunks.append(
                LexicalChunk(
                    chunk_id=chunk_id,
                    namespace=namespace,
                    doc_id=doc_id,
                    document_name=str(source["filename"]),
                    text=str(item.get("index_text") or ""),
                    chunk_index=int(item["index"]),
                    **common,
                )
            )
        self.service.vector_store.add_chunks(vector_chunks)
        self.service.lexical_store.add_chunks(lexical_chunks)
        if self.service.lexical_store.count_namespace(namespace) != len(lexical_chunks):
            raise RuntimeError("Full-text index count does not match the vector candidate index.")
