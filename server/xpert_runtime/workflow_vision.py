from __future__ import annotations

from dataclasses import dataclass
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
from typing import Any, Callable, Literal

try:
    from server.file_assets.service import FileAssetService, FileAssetServiceError
    from server.file_assets.analysis import inspect_analysis_source
    from server.multimodal.vision_understanding import (
        VisionProcessingError,
        VisionSourceResult,
        VisionUnderstandingService,
    )
    from server.xperts.context import XpertContextError, XpertContextStore
    from server.multimodal.vision_v2 import VisionExecutionRejected, VisionModelBindingSnapshot, vision_usage_summary
    from server.xpert_runtime.execution_budget import XpertExecutionBudgetExceeded, execution_operation
except ModuleNotFoundError:
    from file_assets.service import FileAssetService, FileAssetServiceError
    from file_assets.analysis import inspect_analysis_source
    from multimodal.vision_understanding import (
        VisionProcessingError,
        VisionSourceResult,
        VisionUnderstandingService,
    )
    from xperts.context import XpertContextError, XpertContextStore
    from multimodal.vision_v2 import VisionExecutionRejected, VisionModelBindingSnapshot, vision_usage_summary
    from xpert_runtime.execution_budget import XpertExecutionBudgetExceeded, execution_operation


WORKFLOW_VISION_OUTPUT_CHAR_LIMIT = 30_000
WORKFLOW_VISION_BLOCK_CHAR_LIMIT = 8_000
_PRIVATE_XPERT_RUN_TYPES = {"xpert"}


class WorkflowVisionError(RuntimeError):
    """Safe workflow-facing visual execution error."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        provider_route_receipts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.provider_route_receipts = list(provider_route_receipts or [])


@dataclass(frozen=True, slots=True)
class WorkflowVisionAsset:
    asset_id: str
    filename: str
    format_id: str
    byte_size: int
    content: bytes


def validate_vision_v2_request(
    data: dict[str, Any],
    *,
    selected_asset_id: Any,
    runtime_run_type: str,
    runtime_metadata: dict[str, Any],
) -> dict[str, Any]:
    if runtime_run_type not in {"workflow", "xpert", "xpert_evaluation"}:
        raise WorkflowVisionError("workflow_vision_runtime_forbidden", "当前入口不允许执行视觉 V2。")
    if (
        data.get("assetIdVariable") != "selected_file_asset_id"
        or not isinstance(selected_asset_id, str)
        or not selected_asset_id.strip()
    ):
        raise WorkflowVisionError("workflow_vision_input_untrusted", "视觉 V2 必须读取运行时显式选择的单附件槽位。")
    if runtime_run_type == "xpert":
        ids = runtime_metadata.get("file_asset_ids")
        features = runtime_metadata.get("xpert_features") or {}
        if (features.get("file_upload") or {}).get("enabled") is False:
            raise WorkflowVisionError("workflow_vision_files_disabled", "当前 Xpert 已关闭文件输入。")
        if not isinstance(ids, list) or ids != [selected_asset_id]:
            raise WorkflowVisionError("workflow_vision_single_asset_required", "视觉 V2 每次运行必须显式共享且仅共享一个附件。")
    if data.get("retryMode") not in {None, "", "none"} or data.get("failureAction") not in {None, "", "stop"}:
        raise WorkflowVisionError("workflow_vision_policy_invalid", "视觉 V2 本轮不支持节点重试或新错误分支。")
    try:
        binding = VisionModelBindingSnapshot.model_validate(data.get("visionModelBinding"))
    except ValueError as exc:
        raise WorkflowVisionError("workflow_vision_binding_invalid", "视觉节点缺少有效的固定 Managed Binding。") from exc
    if binding.entry_id != "xpert_vision" or binding.model_id != data.get("visionModelId"):
        raise WorkflowVisionError("workflow_vision_binding_invalid", "视觉模型与固定 Managed 入口不一致。")
    return binding.model_dump(mode="json")


def validate_vision_v2_asset(asset: WorkflowVisionAsset, max_pages: Any) -> None:
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise WorkflowVisionError("workflow_vision_pages_invalid", "视觉 V2 页数上限必须为 1 至 20。")
    if len(asset.content) > 10 * 1024 * 1024 or asset.byte_size != len(asset.content):
        raise WorkflowVisionError("workflow_vision_size_invalid", "视觉附件大小不一致或超过 10 MiB。")
    try:
        page_count, _ = inspect_analysis_source(asset.content, format_id=asset.format_id, selected_pages=())
    except Exception as exc:
        raise WorkflowVisionError("workflow_vision_source_invalid", "视觉附件格式、像素或页数未通过安全校验。") from exc
    if page_count > max_pages:
        raise WorkflowVisionError("workflow_vision_pages_exceeded", "附件页数超过节点上限，未截取或发送任何页面。")


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

    if runtime_run_type == "xpert_evaluation":
        try:
            try:
                from server.evaluations.api import get_xpert_evaluation_store
            except ModuleNotFoundError:
                from evaluations.api import get_xpert_evaluation_store
            store = get_xpert_evaluation_store()
            run_id = str(runtime_metadata.get("evaluation_run_id") or "")
            fixture = store.vision_fixture_for_item(
                run_id,
                target_id=str(runtime_metadata.get("evaluation_target_id") or ""),
                case_id=str(runtime_metadata.get("evaluation_case_id") or ""),
                item_id=str(runtime_metadata.get("evaluation_item_id") or ""),
            )
            if fixture["asset_id"] != clean_asset_id:
                raise ValueError("asset mismatch")
            resolved = store.vision_fixtures.resolve_run_asset(run_id, fixture)
        except Exception as exc:
            raise WorkflowVisionError("evaluation_vision_fixture_invalid", "固定评测附件缺失、内容变化或执行项不匹配，禁止读取其他文件。") from exc
        return WorkflowVisionAsset(
            asset_id=resolved.asset_id, filename=resolved.display_name,
            format_id=resolved.format_id, byte_size=resolved.byte_size, content=resolved.content,
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


@asynccontextmanager
async def _vision_execution_operation():
    try:
        async with execution_operation("model_call"):
            yield
    except XpertExecutionBudgetExceeded as exc:
        raise VisionExecutionRejected("视觉请求超过当前运行的模型调用预算。", code="workflow_vision_model_budget_exhausted") from exc


async def execute_workflow_vision(
    *,
    asset: WorkflowVisionAsset,
    model_id: str,
    pdf_page_strategy: Literal["auto", "all", "scanned_only"],
    max_pages: int,
    max_image_edge: int,
    failure_policy: Literal["continue_on_error", "strict"],
    service: VisionUnderstandingService,
    managed_entry_id: str | None = None,
    parent_run_reference: str | None = None,
    contract_version: int = 1,
    cancel_check: Callable[[], bool] | None = None,
    dispatch_observer: Callable[[int], Any] | None = None,
    receipt_observer: Callable[[dict[str, Any]], Any] | None = None,
    binding_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], VisionSourceResult]:
    try:
        result = await service.analyze_bytes(
            asset.content,
            filename=asset.filename,
            source_id=asset.asset_id,
            config={
                "contract_version": contract_version,
                "vision_model_id": model_id,
                "pdf_page_strategy": pdf_page_strategy,
                "render_dpi": 144,
                "max_pages": max_pages,
                "max_image_edge": max_image_edge,
                "failure_policy": failure_policy,
            },
            managed_entry_id=managed_entry_id,  # type: ignore[arg-type]
            parent_run_reference=parent_run_reference,
            **({
                "request_operation": _vision_execution_operation,
                "cancel_check": cancel_check,
                "dispatch_observer": dispatch_observer,
                "receipt_observer": receipt_observer,
                "binding_snapshot": binding_snapshot,
            } if contract_version == 2 else {}),
        )
    except VisionExecutionRejected as exc:
        raise WorkflowVisionError(exc.code, str(exc), provider_route_receipts=[exc.receipt] if isinstance(exc.receipt, dict) else []) from exc
    except VisionProcessingError as exc:
        raise WorkflowVisionError(
            "workflow_vision_processing_failed",
            "视觉理解未能处理所选附件。",
            provider_route_receipts=(
                [exc.receipt]
                if isinstance(getattr(exc, "receipt", None), dict)
                else []
            ),
        ) from exc

    if result.failed_page_count and failure_policy == "strict":
        raise WorkflowVisionError(
            "workflow_vision_strict_failure",
            "至少一个选中页面未能完成视觉理解。",
            provider_route_receipts=result.provider_route_receipts,
        )
    if result.selected_page_count > 0 and result.processed_page_count == 0:
        raise WorkflowVisionError(
            "workflow_vision_all_pages_failed",
            "所有选中页面均未能完成视觉理解。",
            provider_route_receipts=result.provider_route_receipts,
        )
    payload = _workflow_payload(asset, model_id, result)
    if contract_version == 2:
        payload["asset"]["sha256"] = hashlib.sha256(asset.content).hexdigest()
        payload["execution_summary"] = vision_usage_summary(result.provider_route_receipts)
        payload["contract_version"] = 2
        payload["warnings"] = [
            f"第 {item.page_number} 页视觉处理失败。"
            for item in result.page_results if item.status == "failed"
        ]
        if payload["truncated"]:
            payload["warnings"].append("视觉输出已按工作流安全上限截断。")
        if not result.selected_page_count:
            payload["warnings"].append("未选中页面，不能证明已执行视觉理解。")
        if not payload["execution_summary"]["token_usage_verified"]:
            payload["warnings"].append("视觉 Token 用量不可验证，未使用文本长度代替估算。")
    return payload, result


def compose_workflow_vision_receipt(
    receipts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compose page-level evidence for the workflow node without content data."""

    safe_receipts = [dict(item) for item in receipts if isinstance(item, dict)]
    if not safe_receipts:
        return None
    if len(safe_receipts) == 1:
        return safe_receipts[0]

    calls = [
        dict(call)
        for receipt in safe_receipts
        for call in receipt.get("calls", [])
        if isinstance(call, dict)
    ]
    for sequence, call in enumerate(calls, start=1):
        call["call_sequence"] = sequence
    reason_codes = list(
        dict.fromkeys(
            str(code)
            for receipt in safe_receipts
            for code in receipt.get("reason_codes", [])
            if str(code)
        )
    )
    statuses = {str(receipt.get("status") or "") for receipt in safe_receipts}
    status = (
        "uncertain"
        if "uncertain" in statuses
        else "failed"
        if "failed" in statuses
        else "cancelled"
        if "cancelled" in statuses
        else "running"
        if "running" in statuses
        else "passed"
    )
    entry_ids = list(
        dict.fromkeys(
            str(receipt.get("entry_id") or "")
            for receipt in safe_receipts
            if str(receipt.get("entry_id") or "")
        )
    )
    run_references = list(
        dict.fromkeys(
            str(receipt.get("run_reference") or "")
            for receipt in safe_receipts
            if str(receipt.get("run_reference") or "")
        )
    )
    return {
        "contract_version": "modelmirror-provider-multimodal-routing-v1",
        "entry_id": entry_ids[0] if len(entry_ids) == 1 else "multimodal",
        "routing_mode": "managed_required",
        "run_reference": run_references[0] if len(run_references) == 1 else "composed",
        "status": status,
        "call_count": sum(
            max(0, int(receipt.get("call_count") or 0))
            for receipt in safe_receipts
        ),
        "reason_codes": reason_codes,
        "calls": calls,
    }


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
        "provider_route_receipts": list(result.provider_route_receipts),
        "execution_mode": result.execution_mode,
        "fallback_reason_codes": list(result.fallback_reason_codes),
    }


__all__ = [
    "WorkflowVisionAsset",
    "WorkflowVisionError",
    "compose_workflow_vision_receipt",
    "execute_workflow_vision",
    "resolve_workflow_vision_asset",
]
