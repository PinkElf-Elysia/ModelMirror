from __future__ import annotations

import asyncio
import contextlib
import copy
import re
import time
import unicodedata
from typing import Any

from .catalog import BenchmarkCatalog, BenchmarkCatalogError
from .store import BenchmarkJobStore


class KnowledgeBenchmarkInstantiationError(RuntimeError):
    pass


class KnowledgeBenchmarkInstantiationCancelled(RuntimeError):
    pass


class KnowledgeBenchmarkProvisioner:
    """Build one managed, restart-safe RAG benchmark workspace."""

    TERMINAL_PIPELINE_STATUSES = {"succeeded", "failed", "cancelled"}

    def __init__(
        self,
        *,
        catalog: BenchmarkCatalog,
        store: BenchmarkJobStore,
        rag_service: Any,
        pipeline_executor: Any,
        evaluation_store: Any,
        poll_seconds: float = 0.5,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.rag_service = rag_service
        self.pipeline_executor = pipeline_executor
        self.evaluation_store = evaluation_store
        self.poll_seconds = max(0.1, float(poll_seconds))

    async def run(self, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        request = dict(job.get("request") or {})
        pack = self.catalog.get_pack(str(request.get("pack_id") or ""))
        if pack.manifest.kind != "knowledge_retrieval":
            raise BenchmarkCatalogError("Benchmark pack is not a knowledge retrieval pack.")
        state = dict(job.get("provisioning") or {})
        state.setdefault("phase", "create_knowledge_base")
        state.setdefault("pack_id", pack.manifest.pack_id)
        state.setdefault("pack_version", pack.manifest.version)
        state.setdefault("pack_checksum", pack.manifest.checksum)
        state.setdefault("documents", {})
        await self._save(job_id, state)

        try:
            await self._check_cancel(job_id)
            kb_id = await self._ensure_knowledge_base(job_id, request, pack, state)
            await self._ensure_documents(job_id, kb_id, pack, state)
            await self._ensure_pipeline_draft(job_id, kb_id, state)
            pipeline_job = await self._ensure_pipeline_job(job_id, kb_id, state)
            version_id = await self._wait_for_pipeline(job_id, pipeline_job, state)
            state["version_evidence"] = await asyncio.to_thread(
                self.rag_service.pipeline_version_evidence,
                version_id,
            )
            await self._save(job_id, state)
            resolved_cases = await asyncio.to_thread(
                self._resolve_gold_cases,
                kb_id,
                version_id,
                pack.cases,
                dict(state.get("documents") or {}),
            )
            state["phase"] = "publish_evaluation"
            state["resolved_case_count"] = len(resolved_cases)
            await self._save(job_id, state)
            eval_set_id, eval_set_version = await asyncio.to_thread(
                self._ensure_evaluation_set,
                job_id,
                kb_id,
                pack,
                resolved_cases,
                state,
            )
            state["eval_set_id"] = eval_set_id
            state["eval_set_version"] = eval_set_version
            state["phase"] = "activate_index"
            await self._save(job_id, state)
            await asyncio.to_thread(
                self.evaluation_store.set_gate_policy,
                kb_id,
                dict(pack.manifest.metric_policy),
            )
            await asyncio.to_thread(self.rag_service.activate_pipeline_version, version_id)
            await asyncio.to_thread(
                self.rag_service.complete_benchmark_provisioning,
                kb_id,
            )
            state["phase"] = "completed"
            state["completed_at"] = time.time()
            await asyncio.to_thread(
                self.store.update_job,
                job_id,
                status="completed",
                dataset_id=eval_set_id,
                dataset_revision=eval_set_version,
                provisioning=state,
            )
        except KnowledgeBenchmarkInstantiationCancelled:
            await self._cleanup(state)
            await asyncio.to_thread(
                self.store.update_job,
                job_id,
                status="cancelled",
                provisioning={**state, "phase": "cancelled"},
            )
        except Exception:
            await self._cleanup(state)
            raise

    async def _ensure_knowledge_base(
        self,
        job_id: str,
        request: dict[str, Any],
        pack: Any,
        state: dict[str, Any],
    ) -> str:
        kb_id = str(state.get("kb_id") or "")
        if not kb_id:
            existing = next(
                (
                    item
                    for item in self.rag_service.list_knowledge_bases(
                        include_provisioning=True
                    )
                    if str(item.get("origin") or "") == "benchmark_catalog"
                    and str((item.get("catalog_ref") or {}).get("job_id") or "")
                    == job_id
                ),
                None,
            )
            if existing is None:
                existing = await asyncio.to_thread(
                    self.rag_service.create_knowledge_base,
                    str(request.get("name") or pack.manifest.name),
                    origin="benchmark_catalog",
                    catalog_ref={
                        "pack_id": pack.manifest.pack_id,
                        "version": pack.manifest.version,
                        "checksum": pack.manifest.checksum,
                        "job_id": job_id,
                    },
                    corpus_locked=True,
                    provisioning_status="provisioning",
                )
            kb_id = str(existing["id"])
            state["kb_id"] = kb_id
        state["phase"] = "import_corpus"
        await self._save(job_id, state)
        return kb_id

    async def _ensure_documents(
        self,
        job_id: str,
        kb_id: str,
        pack: Any,
        state: dict[str, Any],
    ) -> None:
        documents = dict(state.get("documents") or {})
        existing_by_name = {
            str(item.get("filename") or ""): str(item.get("id") or "")
            for item in await asyncio.to_thread(self.rag_service.list_documents, kb_id)
        }
        for source in pack.documents:
            await self._check_cancel(job_id)
            document_key = str(source["document_key"])
            filename = str(source["filename"])
            if document_key in documents:
                continue
            document_id = existing_by_name.get(filename)
            if not document_id:
                uploaded = await self.rag_service.upload_document(
                    kb_id,
                    filename,
                    str(source["content"]).encode("utf-8"),
                    declared_media_type="text/markdown",
                    allow_locked=True,
                    pipeline_only=True,
                )
                document_id = str(uploaded["id"])
            documents[document_key] = document_id
            state["documents"] = documents
            state["uploaded_document_count"] = len(documents)
            await self._save(job_id, state)
        state["phase"] = "configure_pipeline"
        await self._save(job_id, state)

    async def _ensure_pipeline_draft(
        self,
        job_id: str,
        kb_id: str,
        state: dict[str, Any],
    ) -> None:
        if not state.get("pipeline_draft_version"):
            draft = await asyncio.to_thread(
                self.rag_service.update_pipeline_draft,
                kb_id,
                {
                    "stage_processor": {
                        "mode": "general",
                        "failure_policy": "strict",
                    },
                    "stage_chunker": {"strategy": "parent_child"},
                },
                retrieval_profile={
                    "mode": "fulltext",
                    "top_k": 10,
                    "score_threshold": 0.0,
                    "candidate_multiplier": 4,
                    "vector_weight": 0.7,
                    "fulltext_weight": 0.3,
                    "rerank_enabled": False,
                    "rerank_provider": "none",
                    "rerank_top_n": 10,
                },
                embedding_profile={
                    "provider": "hash",
                    "model": "modelmirror-hash-v1",
                },
            )
            graph = await asyncio.to_thread(self.rag_service.get_pipeline_graph, kb_id)
            if not graph.get("valid"):
                raise KnowledgeBenchmarkInstantiationError(
                    "Managed benchmark pipeline graph is invalid."
                )
            state["pipeline_draft_version"] = int(draft["version"])
            state["pipeline_graph_revision"] = int(graph["graph_revision"])
        state["phase"] = "build_index"
        await self._save(job_id, state)

    async def _ensure_pipeline_job(
        self,
        job_id: str,
        kb_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        pipeline_job_id = str(state.get("pipeline_job_id") or "")
        if pipeline_job_id:
            return await asyncio.to_thread(
                self.rag_service.get_pipeline_job,
                pipeline_job_id,
            )
        existing = next(
            (
                item
                for item in await asyncio.to_thread(
                    self.rag_service.list_pipeline_jobs,
                    kb_id=kb_id,
                    limit=200,
                )
                if str((item.get("origin") or {}).get("benchmark_job_id") or "")
                == job_id
            ),
            None,
        )
        if existing is None:
            existing = await asyncio.to_thread(
                self.rag_service.create_pipeline_job,
                kb_id,
                draft_version=int(state["pipeline_draft_version"]),
                graph_revision=int(state["pipeline_graph_revision"]),
                origin={
                    "kind": "benchmark_catalog",
                    "benchmark_job_id": job_id,
                    "promotion_required": False,
                },
            )
        state["pipeline_job_id"] = str(existing["job_id"])
        state["version_id"] = str(existing["candidate_version_id"])
        await self._save(job_id, state)
        self.pipeline_executor.notify()
        return existing

    async def _wait_for_pipeline(
        self,
        job_id: str,
        pipeline_job: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        pipeline_job_id = str(pipeline_job["job_id"])
        while True:
            await self._check_cancel(job_id, pipeline_job_id=pipeline_job_id)
            current = await asyncio.to_thread(
                self.rag_service.get_pipeline_job,
                pipeline_job_id,
            )
            status = str(current.get("status") or "")
            state["pipeline_status"] = status
            if status in self.TERMINAL_PIPELINE_STATUSES:
                await self._save(job_id, state)
                if status != "succeeded":
                    raise KnowledgeBenchmarkInstantiationError(
                        str(current.get("error") or "Managed benchmark index build failed.")
                    )
                state["phase"] = "resolve_gold"
                state["version_id"] = str(current["candidate_version_id"])
                await self._save(job_id, state)
                return str(current["candidate_version_id"])
            await asyncio.sleep(self.poll_seconds)

    def _resolve_gold_cases(
        self,
        kb_id: str,
        version_id: str,
        raw_cases: list[dict[str, Any]],
        document_ids: dict[str, str],
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for raw_case in raw_cases:
            references: list[dict[str, Any]] = []
            for index, reference in enumerate(raw_case.get("expected_refs") or []):
                document_key = str(reference.get("document_key") or "")
                document_id = str(document_ids.get(document_key) or "")
                if not document_id:
                    raise KnowledgeBenchmarkInstantiationError(
                        f"Gold document mapping is missing for {document_key}."
                    )
                phrase = str(reference.get("anchor_phrase") or "")
                chunks = self.rag_service.vector_store.list_document_chunks(
                    f"{version_id}_{document_id}"
                )
                normalized_phrase = _normalize_anchor_text(phrase)
                child_matches = [
                    item
                    for item in chunks
                    if item.chunk_type == "child"
                    and normalized_phrase in _normalize_anchor_text(item.text)
                ]
                matches = child_matches or [
                    item
                    for item in chunks
                    if normalized_phrase in _normalize_anchor_text(item.text)
                ]
                if len(matches) != 1:
                    raise KnowledgeBenchmarkInstantiationError(
                        f"Gold anchor {reference.get('anchor_key')} resolved {len(matches)} times."
                    )
                match = matches[0]
                if not match.source_block_id:
                    raise KnowledgeBenchmarkInstantiationError(
                        f"Gold anchor {reference.get('anchor_key')} has no source block id."
                    )
                references.append(
                    {
                        "reference_id": (
                            f"ref_{raw_case['case_id']}_{index + 1}"
                        ),
                        "document_id": document_id,
                        "chunk_id": match.chunk_id,
                        "source_block_id": match.source_block_id,
                        "match_mode": "source_block",
                        "catalog_anchor_key": str(reference.get("anchor_key") or ""),
                        "relevance": int(reference.get("relevance") or 1),
                    }
                )
            resolved.append(
                {
                    "case_id": str(raw_case["case_id"]),
                    "query": str(raw_case["query"]),
                    "expected_refs": references,
                    "expected_no_result": bool(raw_case.get("expected_no_result")),
                    "tags": copy.deepcopy(raw_case.get("tags") or []),
                    "notes": str(raw_case.get("notes") or "")[:1000],
                }
            )
        return resolved

    def _ensure_evaluation_set(
        self,
        job_id: str,
        kb_id: str,
        pack: Any,
        cases: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> tuple[str, int]:
        eval_set_id = str(state.get("eval_set_id") or "")
        evaluation_set = None
        if eval_set_id:
            evaluation_set = self.evaluation_store.get_set(eval_set_id)
        else:
            evaluation_set = next(
                (
                    item
                    for item in self.evaluation_store.list_sets(kb_id)
                    if str((item.get("provenance") or {}).get("job_id") or "")
                    == job_id
                ),
                None,
            )
        if evaluation_set is None:
            evaluation_set = self.evaluation_store.create_set(
                kb_id,
                f"{pack.manifest.name} Evaluation",
                pack.manifest.description,
                origin="benchmark_catalog",
                catalog_ref={
                    "pack_id": pack.manifest.pack_id,
                    "version": pack.manifest.version,
                    "checksum": pack.manifest.checksum,
                },
                provenance={
                    "source": pack.manifest.source,
                    "license": pack.manifest.license,
                    "job_id": job_id,
                    "knowledge_version_id": state["version_id"],
                },
                coverage={
                    "areas": list(pack.manifest.coverage),
                    "difficulty": pack.manifest.difficulty,
                    "locales": list(pack.manifest.locales),
                    "metric_policy": copy.deepcopy(pack.manifest.metric_policy),
                },
                calibration={"status": "calibrated", "kind": "deterministic_gold"},
            )
        eval_set_id = str(evaluation_set["eval_set_id"])
        if not evaluation_set.get("cases"):
            evaluation_set = self.evaluation_store.add_cases(
                eval_set_id,
                expected_revision=int(evaluation_set["revision"]),
                cases=cases,
            )
        versions = self.evaluation_store.list_set_versions(eval_set_id)
        version = next(
            (
                item
                for item in versions
                if str((item.get("catalog_ref") or {}).get("checksum") or "")
                == pack.manifest.checksum
            ),
            None,
        )
        if version is None:
            version = self.evaluation_store.publish_set(
                eval_set_id,
                expected_revision=int(evaluation_set["revision"]),
                release_notes=(
                    f"Instantiated from {pack.manifest.pack_id} "
                    f"v{pack.manifest.version}."
                ),
            )
        return eval_set_id, int(version["version"])

    async def _check_cancel(
        self,
        job_id: str,
        *,
        pipeline_job_id: str | None = None,
    ) -> None:
        current = await asyncio.to_thread(self.store.require_job, job_id)
        if not current.get("cancel_requested"):
            return
        if pipeline_job_id:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.rag_service.request_pipeline_job_cancel,
                    pipeline_job_id,
                )
        raise KnowledgeBenchmarkInstantiationCancelled()

    async def _save(self, job_id: str, state: dict[str, Any]) -> None:
        await asyncio.to_thread(
            self.store.update_job,
            job_id,
            status="generating",
            provisioning=copy.deepcopy(state),
        )

    async def _cleanup(self, state: dict[str, Any]) -> None:
        kb_id = str(state.get("kb_id") or "")
        if not kb_id or str(state.get("phase") or "") == "completed":
            return
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.rag_service.delete_knowledge_base, kb_id)


def _normalize_anchor_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.rstrip("。.!?！？")
