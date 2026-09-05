from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from server.multimodal.vision_understanding import (
        DEFAULT_MAX_IMAGE_EDGE,
        DEFAULT_MAX_PAGES,
        DEFAULT_RENDER_DPI,
        MAX_IMAGE_PIXELS,
        SUPPORTED_IMAGE_EXTENSIONS,
        SUPPORTED_VISION_EXTENSIONS,
        VisionBlock,
        VisionPageResult as GenericVisionPageResult,
        VisionProcessingError,
        VisionSourceResult as GenericVisionSourceResult,
        VisionUnderstandingService as GenericVisionUnderstandingService,
        _parse_response,
    )
except ModuleNotFoundError:
    from multimodal.vision_understanding import (
        DEFAULT_MAX_IMAGE_EDGE,
        DEFAULT_MAX_PAGES,
        DEFAULT_RENDER_DPI,
        MAX_IMAGE_PIXELS,
        SUPPORTED_IMAGE_EXTENSIONS,
        SUPPORTED_VISION_EXTENSIONS,
        VisionBlock,
        VisionPageResult as GenericVisionPageResult,
        VisionProcessingError,
        VisionSourceResult as GenericVisionSourceResult,
        VisionUnderstandingService as GenericVisionUnderstandingService,
        _parse_response,
    )

from .document_processor import DocumentBlock


@dataclass(slots=True)
class VisionPageResult:
    page_number: int
    status: str
    blocks: list[DocumentBlock] = field(default_factory=list)
    reason: str = ""
    warning: str | None = None
    error: str | None = None
    cached: bool = False

    def payload(self, *, max_text: int | None = None) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "status": self.status,
            "reason": self.reason,
            "warning": self.warning,
            "error": self.error,
            "cached": self.cached,
            "blocks": [block.payload(max_text=max_text) for block in self.blocks],
        }


@dataclass(slots=True)
class VisionSourceResult:
    source_id: str
    filename: str
    page_count: int
    selected_page_count: int
    processed_page_count: int
    failed_page_count: int
    blocks: list[DocumentBlock]
    page_results: list[VisionPageResult]
    warnings: list[str] = field(default_factory=list)
    provider_route_receipts: list[dict[str, Any]] = field(default_factory=list)
    execution_mode: str = "legacy"
    fallback_reason_codes: list[str] = field(default_factory=list)

    def payload(self, *, max_text: int | None = None) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "filename": self.filename,
            "page_count": self.page_count,
            "selected_page_count": self.selected_page_count,
            "processed_page_count": self.processed_page_count,
            "failed_page_count": self.failed_page_count,
            "block_count": len(self.blocks),
            "block_counts": _block_counts(self.blocks),
            "warnings": list(self.warnings),
            "provider_route_receipts": list(self.provider_route_receipts),
            "execution_mode": self.execution_mode,
            "fallback_reason_codes": list(self.fallback_reason_codes),
            "blocks": [block.payload(max_text=max_text) for block in self.blocks],
            "pages": [item.payload(max_text=max_text) for item in self.page_results],
        }


class VisionUnderstandingService(GenericVisionUnderstandingService):
    """RAG adapter that converts neutral visual blocks to DocumentBlock."""

    def __init__(
        self,
        *,
        request_override: Callable[[str, str, dict[str, Any]], Any] | None = None,
        max_concurrency: int = 2,
    ) -> None:
        super().__init__(
            request_override=request_override,
            max_concurrency=max_concurrency,
        )

    def capabilities(self) -> dict[str, Any]:
        payload = super().capabilities()
        return {**payload, "version": "rag-vision-capabilities-v1"}

    async def analyze_source(
        self,
        path: Path,
        *,
        filename: str,
        source_id: str,
        config: dict[str, Any],
        cache_get: Callable[[int], dict[str, Any] | None] | None = None,
        cache_set: Callable[[int, dict[str, Any]], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        content: bytes | None = None,
    ) -> VisionSourceResult:
        result = await super().analyze_bytes(
            path.read_bytes() if content is None else content,
            filename=filename,
            source_id=source_id,
            config=config,
            cache_get=cache_get,
            cache_set=cache_set,
            cancel_check=cancel_check,
            managed_entry_id="rag_vision",
            parent_run_reference=(
                str(config.get("provider_parent_run_reference") or "").strip()
                or None
            ),
        )
        return self._adapt_source_result(result)

    def _blocks_from_analysis(
        self,
        data: dict[str, Any],
        *,
        source_id: str,
        page_number: int,
        local_text: str,
        model_id: str,
    ) -> tuple[list[DocumentBlock], str | None]:
        blocks, warning = super()._blocks_from_analysis(
            data,
            source_id=source_id,
            page_number=page_number,
            local_text=local_text,
            model_id=model_id,
        )
        return [_to_document_block(block) for block in blocks], warning

    def _page_result_from_payload(self, payload: dict[str, Any]) -> VisionPageResult:
        generic = super()._page_result_from_payload(payload)
        return VisionPageResult(
            page_number=generic.page_number,
            status=generic.status,
            blocks=[_to_document_block(block) for block in generic.blocks],
            reason=generic.reason,
            warning=generic.warning,
            error=generic.error,
            cached=generic.cached,
        )

    def _adapt_source_result(
        self,
        result: GenericVisionSourceResult,
    ) -> VisionSourceResult:
        page_results: list[VisionPageResult] = []
        for item in result.page_results:
            if isinstance(item, VisionPageResult):
                page_results.append(item)
            else:
                assert isinstance(item, GenericVisionPageResult)
                page_results.append(
                    VisionPageResult(
                        page_number=item.page_number,
                        status=item.status,
                        blocks=[_to_document_block(block) for block in item.blocks],
                        reason=item.reason,
                        warning=item.warning,
                        error=item.error,
                        cached=item.cached,
                    )
                )
        return VisionSourceResult(
            source_id=result.source_id,
            filename=result.filename,
            page_count=result.page_count,
            selected_page_count=result.selected_page_count,
            processed_page_count=result.processed_page_count,
            failed_page_count=result.failed_page_count,
            blocks=[_to_document_block(block) for block in result.blocks],
            page_results=page_results,
            warnings=list(result.warnings),
            provider_route_receipts=list(result.provider_route_receipts),
            execution_mode=result.execution_mode,
            fallback_reason_codes=list(result.fallback_reason_codes),
        )


def _to_document_block(block: VisionBlock | DocumentBlock) -> DocumentBlock:
    if isinstance(block, DocumentBlock):
        return block
    return DocumentBlock(
        block_id=block.block_id,
        kind=block.kind,
        text=block.text,
        start_char=block.start_char,
        end_char=block.end_char,
        page_number=block.page_number,
        metadata=dict(block.metadata),
    )


def _block_counts(blocks: list[DocumentBlock]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in blocks:
        counts[block.kind] = counts.get(block.kind, 0) + 1
    return counts


__all__ = [
    "DEFAULT_MAX_IMAGE_EDGE",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_RENDER_DPI",
    "MAX_IMAGE_PIXELS",
    "SUPPORTED_IMAGE_EXTENSIONS",
    "SUPPORTED_VISION_EXTENSIONS",
    "VisionPageResult",
    "VisionProcessingError",
    "VisionSourceResult",
    "VisionUnderstandingService",
    "_parse_response",
]
