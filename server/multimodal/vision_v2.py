from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


VISION_V2_MAX_BYTES = 10 * 1024 * 1024
VISION_V2_MAX_PAGES = 20
VISION_V2_DEFAULT_PAGES = 10
VISION_V2_ANALYSIS_CHAR_LIMIT = 64_000
_Text = Annotated[str, Field(max_length=30_000)]
_BlockText = Annotated[str, Field(max_length=8_000)]


class VisionExecutionRejected(RuntimeError):
    """A global execution gate rejected dispatch, not a recoverable page error."""

    def __init__(self, message: str, *, code: str = "workflow_vision_execution_rejected") -> None:
        super().__init__(message)
        self.code = code
        self.receipt: dict[str, Any] | None = None


class VisionModelBindingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    entry_id: Literal["xpert_vision", "workflow_interactive_vision", "workflow_deployment_vision"]
    model_id: str = Field(min_length=1, max_length=512)
    execution_shape: Literal["vision_json_unary"]
    connection_id: str = Field(min_length=1, max_length=128)
    certification_id: str = Field(min_length=1, max_length=128)
    connection_fingerprint: str = Field(min_length=1, max_length=128)
    qualification_fingerprint: str = Field(min_length=1, max_length=128)
    adapter_contract: str | None = None
    protocol_version: str | None = None
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_checksum(self):
        expected = sha256(json.dumps(
            self.model_dump(mode="json", exclude={"checksum"}),
            sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if self.checksum != expected:
            raise ValueError("视觉模型 Binding 摘要校验失败。")
        return self


class VisionAnalysisV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ocr_text: _Text
    visual_summary: _Text
    tables: list[_BlockText] = Field(max_length=50)
    charts: list[_BlockText] = Field(max_length=50)
    language: str = Field(max_length=80)
    warnings: list[Annotated[str, Field(max_length=500)]] = Field(max_length=20)


def validate_vision_analysis_v2(value: Any) -> dict[str, Any]:
    try:
        result = VisionAnalysisV2.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError("视觉模型结果不符合 V2 字段类型契约。") from exc
    if len(json.dumps(result, ensure_ascii=False)) > VISION_V2_ANALYSIS_CHAR_LIMIT:
        raise ValueError("视觉模型结果超过单页输出上限。")
    if not any(result[key] for key in ("ocr_text", "visual_summary", "tables", "charts")):
        raise ValueError("视觉模型未返回有效的页面内容。")
    return result


def vision_usage_summary(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [
        call
        for receipt in receipts
        for call in receipt.get("calls", [])
        if isinstance(call, dict) and call.get("dispatched") is True
    ]
    known = [
        call["total_tokens"]
        for call in calls
        if type(call.get("total_tokens")) is int and call["total_tokens"] >= 0
    ]
    return {
        "model_calls": len(calls),
        "known_total_tokens": sum(known),
        "unverified_token_calls": len(calls) - len(known),
        "token_usage_verified": bool(calls) and len(known) == len(calls),
        "token_source": "managed_receipt",
        "uncertain_calls": sum(call.get("status") == "uncertain" for call in calls),
    }


def safe_vision_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    calls = []
    for value in list(receipt.get("calls") or [])[:20]:
        if not isinstance(value, dict):
            continue
        call: dict[str, Any] = {
            "dispatched": value.get("dispatched") is True,
            "status": value.get("status") if value.get("status") in {"passed", "failed", "cancelled", "uncertain"} else "unknown",
        }
        for key in ("model_id", "actual_model"):
            if isinstance(value.get(key), str):
                call[key] = value[key][:512]
        for key in ("call_sequence", "total_tokens"):
            if type(value.get(key)) is int and value[key] >= 0:
                call[key] = value[key]
        calls.append(call)
    return {
        "status": receipt.get("status") if receipt.get("status") in {"running", "passed", "failed", "cancelled", "uncertain"} else "unknown",
        "calls": calls,
        "usage": vision_usage_summary([{"calls": calls}]),
    }


def evaluation_vision_usage(receipts: list[dict[str, Any]], *, dispatch_count: int = 0) -> dict[str, Any]:
    summary = vision_usage_summary(receipts)
    unreceipted = max(0, dispatch_count - summary["model_calls"])
    return {
        "vision_model_calls": summary["model_calls"],
        "vision_actual_tokens": summary["known_total_tokens"],
        "vision_unverified_usage_calls": summary["unverified_token_calls"] + unreceipted,
        "vision_uncertain_dispatches": summary["uncertain_calls"] + unreceipted,
        "vision_token_usage_verified": summary["token_usage_verified"] and unreceipted == 0,
        "vision_token_estimate": False,
    }
