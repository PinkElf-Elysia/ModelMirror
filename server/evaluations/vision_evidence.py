from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

try:
    from server.evaluations.models import EvaluationVisionExpectation
    from server.multimodal.vision_v2 import vision_usage_summary
except ModuleNotFoundError:
    from evaluations.models import EvaluationVisionExpectation
    from multimodal.vision_v2 import vision_usage_summary


_ASSET_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_RUNTIME_BLOCK_KINDS = {
    "image_ocr": "ocr",
    "image_description": "description",
    "visual_table": "table",
    "visual_chart": "chart",
}


def capture_vision_evidence(
    node_ref: str,
    payload: dict[str, Any],
    assertions: (
        EvaluationVisionExpectation
        | dict[str, Any]
        | list[EvaluationVisionExpectation | dict[str, Any]]
        | None
    ),
    expected_asset_sha256: str | None,
    expected_model_id: str | None,
) -> dict[str, Any]:
    """Validate a Vision V2 payload and return content-free evaluation evidence."""

    expectation = _expectation_for_node(node_ref, assertions)
    if not isinstance(payload, dict):
        raise ValueError("视觉执行结果必须是对象。")
    if type(payload.get("contract_version")) is not int or payload.get(
        "contract_version"
    ) != 2:
        raise ValueError("视觉证据只接受 contract_version 2 的执行结果。")
    if payload.get("execution_mode") != "managed":
        raise ValueError("视觉证据必须来自真实 Managed 执行。")

    receipts = payload.get("provider_route_receipts")
    if not isinstance(receipts, list):
        raise ValueError("视觉执行结果缺少 Managed 调用回执。")
    safe_receipts = []
    has_dispatched_call = False
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        calls = receipt.get("calls")
        if not isinstance(calls, list):
            continue
        safe_calls = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            dispatched = call.get("dispatched") is True
            has_dispatched_call = has_dispatched_call or dispatched
            safe_call: dict[str, Any] = {"dispatched": dispatched}
            if type(call.get("total_tokens")) is int:
                safe_call["total_tokens"] = call["total_tokens"]
            if call.get("status") == "uncertain":
                safe_call["status"] = "uncertain"
            safe_calls.append(safe_call)
        safe_receipts.append({"calls": safe_calls})
    if not has_dispatched_call:
        raise ValueError("视觉证据没有可验证的 dispatched 模型调用。")

    asset = payload.get("asset")
    if not isinstance(asset, dict):
        raise ValueError("视觉执行结果缺少附件标识。")
    asset_id = asset.get("asset_id")
    asset_sha256 = asset.get("sha256")
    model_id = payload.get("model_id")
    if not isinstance(asset_id, str) or not _ASSET_ID_PATTERN.fullmatch(asset_id):
        raise ValueError("视觉执行结果的 asset_id 无效。")
    if not isinstance(asset_sha256, str) or not _ASSET_SHA256_PATTERN.fullmatch(
        asset_sha256
    ):
        raise ValueError("视觉执行结果的附件 SHA-256 无效。")
    if not isinstance(model_id, str) or not model_id or len(model_id) > 512:
        raise ValueError("视觉执行结果的 model_id 无效。")

    if expected_asset_sha256 is not None:
        if not isinstance(expected_asset_sha256, str) or not (
            _ASSET_SHA256_PATTERN.fullmatch(expected_asset_sha256)
        ):
            raise ValueError("固定附件的 SHA-256 无效。")
        if asset_sha256 != expected_asset_sha256:
            raise ValueError("视觉执行结果与固定附件身份不一致。")
    if expected_model_id is not None:
        if (
            not isinstance(expected_model_id, str)
            or not expected_model_id
            or len(expected_model_id) > 512
        ):
            raise ValueError("固定视觉模型标识无效。")
        if model_id != expected_model_id:
            raise ValueError("视觉执行结果与固定视觉模型身份不一致。")

    page_count = _strict_int(payload, "page_count", minimum=1, maximum=20)
    selected_page_count = _strict_int(
        payload,
        "selected_page_count",
        minimum=1,
        maximum=page_count,
    )
    processed_page_count = _strict_int(
        payload,
        "processed_page_count",
        minimum=1,
        maximum=selected_page_count,
    )
    failed_page_count = _strict_int(
        payload,
        "failed_page_count",
        minimum=0,
        maximum=selected_page_count,
    )
    if processed_page_count + failed_page_count != selected_page_count:
        raise ValueError("视觉执行结果的页面计数不一致。")
    status = "partial" if failed_page_count else "success"

    blocks = _validated_blocks(payload, page_count=page_count)
    block_counts = {kind: 0 for kind in _RUNTIME_BLOCK_KINDS.values()}
    for block in blocks:
        block_counts[block["kind"]] += 1

    anchor_checks = []
    for index, anchor in enumerate(expectation.content_anchors):
        expected_text = _normalize_text(anchor.text)
        matched = any(
            block["kind"] == anchor.kind
            and (
                anchor.page_number is None
                or block["page_number"] == anchor.page_number
            )
            and expected_text in _normalize_text(block["text"])
            for block in blocks
        )
        anchor_checks.append({"index": index, "matched": matched})

    return {
        "node_ref": node_ref,
        "asset_id": asset_id,
        "asset_sha256": asset_sha256,
        "model_id": model_id,
        "page_count": page_count,
        "selected_page_count": selected_page_count,
        "processed_page_count": processed_page_count,
        "failed_page_count": failed_page_count,
        "status": status,
        "block_counts": block_counts,
        "anchor_checks": anchor_checks,
        "execution_summary": vision_usage_summary(safe_receipts),
    }


def _expectation_for_node(
    node_ref: str,
    assertions: (
        EvaluationVisionExpectation
        | dict[str, Any]
        | list[EvaluationVisionExpectation | dict[str, Any]]
        | None
    ),
) -> EvaluationVisionExpectation:
    assertions_are_list = isinstance(assertions, list)
    raw_items = assertions if assertions_are_list else [assertions]
    validated: list[EvaluationVisionExpectation] = []
    try:
        for item in raw_items:
            if item is not None:
                validated.append(EvaluationVisionExpectation.model_validate(item))
    except ValidationError as exc:
        raise ValueError("视觉证据期望不符合严格契约。") from exc

    matches = [item for item in validated if item.node_ref == node_ref]
    if len(matches) > 1:
        raise ValueError("视觉证据期望包含重复的 node_ref。")
    if matches:
        return matches[0]
    if not assertions_are_list and assertions is not None:
        raise ValueError("视觉证据的 node_ref 与评估期望不一致。")
    try:
        return EvaluationVisionExpectation(node_ref=node_ref)
    except ValidationError as exc:
        raise ValueError("视觉证据的 node_ref 无效。") from exc


def _strict_int(
    payload: dict[str, Any],
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(field)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"视觉执行结果的 {field} 无效。")
    return value


def _validated_blocks(
    payload: dict[str, Any],
    *,
    page_count: int,
) -> list[dict[str, Any]]:
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        raise ValueError("视觉执行结果缺少 blocks。")
    block_count = payload.get("block_count")
    if type(block_count) is not int or block_count < 0 or block_count != len(raw_blocks):
        raise ValueError("视觉执行结果的 block_count 与 blocks 不一致。")

    blocks: list[dict[str, Any]] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            raise ValueError("视觉执行结果包含无效块。")
        kind = _RUNTIME_BLOCK_KINDS.get(raw.get("kind"))
        text = raw.get("text")
        page_number = raw.get("page_number")
        if kind is None or not isinstance(text, str):
            raise ValueError("视觉执行结果包含无效块类型或正文。")
        if page_number is not None and (
            type(page_number) is not int or not 1 <= page_number <= page_count
        ):
            raise ValueError("视觉执行结果包含无效块页码。")
        blocks.append(
            {
                "kind": kind,
                "text": text,
                "page_number": page_number,
            }
        )
    return blocks


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


__all__ = ["capture_vision_evidence"]
