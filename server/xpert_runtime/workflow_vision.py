from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

try:
    from server.file_assets.service import FileAssetService, FileAssetServiceError
    from server.multimodal.vision_understanding import (
        VisionProcessingError,
        VisionSourceResult,
        VisionUnderstandingService,
    )
    from server.xperts.context import XpertContextError, XpertContextStore
except ModuleNotFoundError:
    from file_assets.service import FileAssetService, FileAssetServiceError
    from multimodal.vision_understanding import (
        VisionProcessingError,
        VisionSourceResult,
        VisionUnderstandingService,
    )
    from xperts.context import XpertContextError, XpertContextStore


WORKFLOW_VISION_OUTPUT_CHAR_LIMIT = 30_000
WORKFLOW_VISION_BLOCK_CHAR_LIMIT = 8_000
_PRIVATE_XPERT_RUN_TYPES = {"xpert"}


class WorkflowVisionError(RuntimeError):
    """Safe workflow-facing visual execution error."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True, slots=True)
class WorkflowVisionAsset:
    asset_id: str
    filename: str
    format_id: str
    byte_size: int
    content: bytes


def resolve_workflow_vision_asset(
    *,
    asset_id: str,
    workflow_id: str,
    runtime_run_type: str,
    runtime_metadata: dict[str, Any],
    file_asset_service: FileAssetService,
    xpert_context_store: XpertContextStore,
) -> WorkflowVisionAsset:
    clean_asset_id = str(asset_id or "").strip()
    if not clean_asset_id:
        raise WorkflowVisionError(
            "workflow_vision_asset_required",
            "视觉理解节点需要一个已选择的附件。",
        )
    if runtime_run_type == "workflow":
        try:
            asset = file_asset_service.resolve_workflow_visual_asset(
                clean_asset_id,
                scope_id=f"workflow:{workflow_id}",
            )
        except FileAssetServiceError as exc:
            raise WorkflowVisionError(exc.error_code, exc.message) from exc
        return WorkflowVisionAsset(
            asset_id=asset.asset_id,
            filename=asset.display_name,
            format_id=asset.format_id,
            byte_size=asset.byte_size,
            content=asset.content,
        )

    if runtime_run_type not in _PRIVATE_XPERT_RUN_TYPES:
        raise WorkflowVisionError(
            "workflow_vision_runtime_forbidden",
            "当前运行入口不允许读取视觉附件。",
        )
    allowed_asset_ids = {
        str(value).strip()
        for value in runtime_metadata.get("file_asset_ids", [])
        if str(value).strip()
    }
    if clean_asset_id not in allowed_asset_ids:
        raise WorkflowVisionError(
            "workflow_vision_asset_not_shared",
            "该附件未显式共享给当前运行。",
        )
    owner_xpert_id = str(runtime_metadata.get("file_owner_xpert_id") or "").strip()
    conversation_id = str(
        runtime_metadata.get("file_conversation_id") or ""
    ).strip()
    if not owner_xpert_id or not conversation_id:
        raise WorkflowVisionError(
            "workflow_vision_scope_missing",
            "当前运行缺少附件作用域信息。",
        )
    try:
        asset = xpert_context_store.get_file(
            owner_xpert_id,
            clean_asset_id,
            conversation_id=conversation_id,
            include_archived=True,
        )
        content = xpert_context_store.read_file_bytes(asset)
    except XpertContextError as exc:
        raise WorkflowVisionError(
            "workflow_vision_asset_unavailable",
            "该附件不属于当前运行或已不可用。",
        ) from exc
    extension = Path(asset.filename).suffix.lower()
    format_id = {
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
        ".png": "png",
        ".webp": "webp",
        ".pdf": "pdf",
    }.get(extension)
    if format_id is None:
        raise WorkflowVisionError(
            "workflow_vision_asset_unsupported",
            "该附件不能用于视觉理解。",
        )
    return WorkflowVisionAsset(
        asset_id=asset.asset_id,
        filename=asset.filename,
        format_id=format_id,
        byte_size=asset.size_bytes,
        content=content,
    )


async def execute_workflow_vision(
    *,
    asset: WorkflowVisionAsset,
    model_id: str,
    pdf_page_strategy: Literal["auto", "all", "scanned_only"],
    max_pages: int,
    max_image_edge: int,
    failure_policy: Literal["continue_on_error", "strict"],
    service: VisionUnderstandingService,
) -> tuple[dict[str, Any], VisionSourceResult]:
    try:
        result = await service.analyze_bytes(
            asset.content,
            filename=asset.filename,
            source_id=asset.asset_id,
            config={
                "vision_model_id": model_id,
                "pdf_page_strategy": pdf_page_strategy,
                "render_dpi": 144,
                "max_pages": max_pages,
                "max_image_edge": max_image_edge,
                "failure_policy": failure_policy,
            },
        )
    except VisionProcessingError as exc:
        raise WorkflowVisionError(
            "workflow_vision_processing_failed",
            "视觉理解未能处理所选附件。",
        ) from exc

    if result.failed_page_count and failure_policy == "strict":
        raise WorkflowVisionError(
            "workflow_vision_strict_failure",
            "至少一个选中页面未能完成视觉理解。",
        )
    if result.selected_page_count > 0 and result.processed_page_count == 0:
        raise WorkflowVisionError(
            "workflow_vision_all_pages_failed",
            "所有选中页面均未能完成视觉理解。",
        )
    return _workflow_payload(asset, model_id, result), result


def _workflow_payload(
    asset: WorkflowVisionAsset,
    model_id: str,
    result: VisionSourceResult,
) -> dict[str, Any]:
    remaining = WORKFLOW_VISION_OUTPUT_CHAR_LIMIT
    blocks: list[dict[str, Any]] = []
    truncated = False
    for block in result.blocks:
        allowance = min(WORKFLOW_VISION_BLOCK_CHAR_LIMIT, remaining)
        if allowance <= 0:
            truncated = True
            break
        text = block.text[:allowance]
        block_truncated = len(text) < len(block.text)
        blocks.append(
            {
                "block_id": block.block_id,
                "kind": block.kind,
                "text": text,
                "page_number": block.page_number,
                "source_block_id": str(
                    block.metadata.get("source_block_id") or block.block_id
                ),
                "truncated": block_truncated,
            }
        )
        remaining -= len(text)
        truncated = truncated or block_truncated
    warning_values = list(result.warnings)
    warning_values.extend(
        f"Visual processing failed on page {item.page_number}."
        for item in result.page_results
        if item.status == "failed"
    )
    if truncated:
        warning_values.append("Visual output was truncated to the workflow safety limit.")
    return {
        "asset": {
            "asset_id": asset.asset_id,
            "filename": asset.filename,
            "format": asset.format_id,
            "byte_size": asset.byte_size,
        },
        "model_id": model_id,
        "page_count": result.page_count,
        "selected_page_count": result.selected_page_count,
        "processed_page_count": result.processed_page_count,
        "failed_page_count": result.failed_page_count,
        "block_count": len(blocks),
        "blocks": blocks,
        "ocr": [item for item in blocks if item["kind"] == "image_ocr"],
        "visual_descriptions": [
            item for item in blocks if item["kind"] == "image_description"
        ],
        "tables": [item for item in blocks if item["kind"] == "visual_table"],
        "charts": [item for item in blocks if item["kind"] == "visual_chart"],
        "warnings": list(dict.fromkeys(value for value in warning_values if value)),
        "truncated": truncated,
    }


__all__ = [
    "WorkflowVisionAsset",
    "WorkflowVisionError",
    "execute_workflow_vision",
    "resolve_workflow_vision_asset",
]
