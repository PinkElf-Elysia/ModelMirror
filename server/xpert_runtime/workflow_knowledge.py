from __future__ import annotations

from typing import Any, Protocol


MAX_CONTEXT_LENGTH = 20_000
MAX_SOURCE_TEXT_LENGTH = 2_000
MAX_MATCHED_TEXT_LENGTH = 1_000


class WorkflowKnowledgeService(Protocol):
    def list_knowledge_bases(self) -> list[dict[str, Any]]: ...

    async def search_knowledge(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int = 5,
        retrieval: dict[str, Any] | None = None,
        version_id: str | None = None,
    ) -> dict[str, Any]: ...

    def citation_anchors_from_search_result(
        self,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]: ...


class WorkflowKnowledgeContractError(RuntimeError):
    """Stable, content-free failure for workflow knowledge consumption."""

    def __init__(self, error_code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


def resolve_workflow_knowledge_base(
    service: WorkflowKnowledgeService,
    configured_id: str,
    *,
    allow_legacy_fallback: bool,
) -> tuple[str, list[str]]:
    knowledge_bases = service.list_knowledge_bases()
    available_ids = {
        str(item.get("id") or "").strip()
        for item in knowledge_bases
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    requested_id = str(configured_id or "").strip()
    if requested_id:
        if requested_id not in available_ids:
            raise WorkflowKnowledgeContractError(
                "workflow_knowledge_base_not_found",
                "The configured knowledge base is unavailable.",
            )
        return requested_id, []

    if not allow_legacy_fallback:
        raise WorkflowKnowledgeContractError(
            "workflow_knowledge_base_required",
            "Select a knowledge base before running this node.",
        )
    if len(available_ids) == 1:
        return next(iter(available_ids)), [
            "Legacy node omitted knowledgeBaseId; the only available knowledge base was used."
        ]
    if not available_ids:
        raise WorkflowKnowledgeContractError(
            "workflow_knowledge_base_unavailable",
            "No knowledge base is available for this legacy node.",
        )
    raise WorkflowKnowledgeContractError(
        "workflow_knowledge_base_ambiguous",
        "This legacy node does not identify a knowledge base and multiple choices exist.",
    )


def _limited_source(source: dict[str, Any]) -> dict[str, Any]:
    text = str(source.get("text") or "")
    matched_text = str(source.get("matched_text") or "")
    return {
        "chunk_id": str(source.get("chunk_id") or ""),
        "document_id": str(
            source.get("source_document_id") or source.get("doc_id") or ""
        ),
        "document_name": str(source.get("document_name") or "")[:500],
        "text": text[:MAX_SOURCE_TEXT_LENGTH],
        "text_length": len(text),
        "text_truncated": len(text) > MAX_SOURCE_TEXT_LENGTH,
        "matched_text": matched_text[:MAX_MATCHED_TEXT_LENGTH],
        "score": source.get("score"),
        "vector_score": source.get("vector_score"),
        "fulltext_score": source.get("fulltext_score"),
        "fused_score": source.get("fused_score"),
        "rerank_score": source.get("rerank_score"),
        "parent_chunk_id": source.get("parent_chunk_id"),
        "parent_lifted": bool(source.get("parent_lifted")),
        "chunk_type": str(source.get("chunk_type") or "standard"),
        "start_char": source.get("start_char"),
        "end_char": source.get("end_char"),
        "page_number": source.get("page_number"),
        "slide": source.get("slide"),
        "heading_path": list(source.get("heading_path") or [])[:20],
        "sheet": source.get("sheet"),
        "row_range": source.get("row_range"),
        "visual_kind": source.get("visual_kind"),
        "source_block_id": source.get("source_block_id"),
    }


def _limited_context(sources: list[dict[str, Any]]) -> tuple[str, bool]:
    parts = [str(source.get("text") or "") for source in sources]
    context = "\n---\n".join(part for part in parts if part)
    return context[:MAX_CONTEXT_LENGTH], len(context) > MAX_CONTEXT_LENGTH


async def execute_workflow_knowledge_retrieval(
    service: WorkflowKnowledgeService,
    *,
    configured_kb_id: str,
    query: str,
    top_k: int,
    contract_version: int,
    return_mode: str,
) -> tuple[str | dict[str, Any], dict[str, Any]]:
    is_v2 = contract_version >= 2
    knowledge_base_id, compatibility_warnings = resolve_workflow_knowledge_base(
        service,
        configured_kb_id,
        allow_legacy_fallback=not is_v2,
    )
    result = await service.search_knowledge(
        knowledge_base_id,
        query,
        top_k=max(1, min(int(top_k), 10)),
    )
    raw_sources = result.get("sources")
    sources = [
        _limited_source(source)
        for source in raw_sources
        if isinstance(source, dict)
    ] if isinstance(raw_sources, list) else []
    context, context_truncated = _limited_context(sources)
    warnings = [
        *compatibility_warnings,
        *[
            str(item)[:1_000]
            for item in (result.get("warnings") or [])
            if str(item).strip()
        ],
    ]
    citations = service.citation_anchors_from_search_result(result)
    diagnostics = dict(result.get("retrieval") or {})
    metadata = {
        "kb_id": knowledge_base_id,
        "version_id": result.get("version_id"),
        "hit_count": len(sources),
        "context_length": len(context),
        "citation_count": len(citations),
        "warning_count": len(warnings),
        "contract_version": contract_version,
        "return_mode": return_mode,
    }
    if not is_v2 or return_mode == "context":
        return context, metadata
    return {
        "knowledge_base_id": knowledge_base_id,
        "version_id": result.get("version_id"),
        "context": context,
        "context_truncated": context_truncated,
        "sources": sources,
        "citations": citations,
        "citation_count": len(citations),
        "retrieval": diagnostics,
        "warnings": warnings,
    }, metadata
