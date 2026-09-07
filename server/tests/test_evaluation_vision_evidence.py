from __future__ import annotations

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from server.evaluations.metrics import (
    aggregate_evaluation_report,
    evaluate_case_metrics,
    sanitize_vision_reads,
)
from server.evaluations.models import (
    EvaluationCaseInput,
    EvaluationPreflightRequest,
    EvaluationVisionExpectation,
)
from server.evaluations.vision_evidence import capture_vision_evidence


ASSET_SHA256 = "a" * 64
MODEL_ID = "managed/vision-model"
SECRET_OCR = "Invoice total is USD 42.00 for private customer Alice."
SECRET_DESCRIPTION = "A confidential red approval stamp appears in the corner."


def _block(kind: str, text: str, page_number: int = 1) -> dict:
    return {
        "block_id": f"block-{kind}",
        "kind": kind,
        "text": text,
        "page_number": page_number,
        "source_block_id": f"source-{kind}",
        "truncated": False,
    }


def _payload(
    *,
    asset_sha256: str = ASSET_SHA256,
    model_id: str = MODEL_ID,
    page_count: int = 2,
    selected_page_count: int = 2,
    processed_page_count: int = 2,
    failed_page_count: int = 0,
    dispatched: bool = True,
    execution_mode: str = "managed",
    blocks: list[dict] | None = None,
) -> dict:
    payload_blocks = blocks if blocks is not None else [
        _block("image_ocr", SECRET_OCR),
        _block("image_description", SECRET_DESCRIPTION),
        _block("visual_table", "Private line item table"),
        _block("visual_chart", "Private quarterly chart", page_number=2),
    ]
    return {
        "asset": {
            "asset_id": "asset-eval-1",
            "filename": "private.pdf",
            "format": "pdf",
            "byte_size": 1024,
            "sha256": asset_sha256,
        },
        "model_id": model_id,
        "page_count": page_count,
        "selected_page_count": selected_page_count,
        "processed_page_count": processed_page_count,
        "failed_page_count": failed_page_count,
        "block_count": len(payload_blocks),
        "blocks": payload_blocks,
        "ocr": [item for item in payload_blocks if item["kind"] == "image_ocr"],
        "visual_descriptions": [
            item for item in payload_blocks if item["kind"] == "image_description"
        ],
        "tables": [
            item for item in payload_blocks if item["kind"] == "visual_table"
        ],
        "charts": [
            item for item in payload_blocks if item["kind"] == "visual_chart"
        ],
        "warnings": [],
        "truncated": False,
        "provider_route_receipts": [
            {
                "entry_id": "xpert_vision",
                "calls": [
                    {
                        "dispatched": dispatched,
                        "status": "succeeded",
                        "total_tokens": 37,
                        "raw_provider_body": "must-not-leak",
                    }
                ],
            }
        ],
        "execution_mode": execution_mode,
        "fallback_reason_codes": [],
        "contract_version": 2,
        "execution_summary": {"provider_body": "must-not-leak"},
        "final_output": "must-never-count-as-vision-evidence",
    }


def _expectation(**updates) -> dict:
    value = {
        "node_ref": "inspect_asset",
        "asset_sha256": ASSET_SHA256,
        "model_id": MODEL_ID,
        "page_count": 2,
        "status": "success",
        "required_blocks": ["ocr", "description", "table", "chart"],
        "content_anchors": [
            {"kind": "ocr", "text": "invoice total", "page_number": 1},
            {"kind": "description", "text": "red approval stamp"},
        ],
    }
    value.update(updates)
    return value


def _vision_metric(result: dict) -> dict:
    return next(
        item for item in result["metrics"] if item["kind"] == "workflow_vision_match"
    )


def _safe_evidence(
    *,
    payload: dict | None = None,
    assertions: list[dict] | dict | None = None,
    node_ref: str = "inspect_asset",
) -> dict:
    return capture_vision_evidence(
        node_ref,
        _payload() if payload is None else payload,
        [_expectation()] if assertions is None else assertions,
        ASSET_SHA256,
        MODEL_ID,
    )


def test_report_boundary_retains_false_anchor_results_without_raw_content():
    evidence = _safe_evidence(assertions=[_expectation(content_anchors=[{"kind": "ocr", "text": "absent anchor"}])])
    assert evidence["anchor_checks"] == [{"index": 0, "matched": False}]
    assert sanitize_vision_reads([evidence]) == [evidence]
    with pytest.raises(ValueError):
        sanitize_vision_reads([{**evidence, "raw_ocr": SECRET_OCR}])
    with pytest.raises(ValueError):
        sanitize_vision_reads([evidence, evidence])


def test_vision_case_schema_is_strict_and_attachment_is_id_only() -> None:
    case = EvaluationCaseInput.model_validate(
        {
            "message": "Inspect the fixed attachment.",
            "attachment": {"asset_id": "asset_eval-01.pdf"},
            "vision": [_expectation()],
            "weights": {"workflow_vision_match": 2.0},
        }
    )

    assert case.attachment is not None
    assert case.attachment.model_dump(mode="json") == {
        "asset_id": "asset_eval-01.pdf"
    }
    assert case.vision[0].status == "success"
    assert EvaluationCaseInput(message="Draft before upload", vision=[]).attachment is None

    with pytest.raises(ValidationError, match="视觉证据期望包含未知字段"):
        EvaluationVisionExpectation.model_validate(
            {**_expectation(), "handle": "forged"}
        )
    with pytest.raises(ValidationError, match="评估附件引用包含未知字段"):
        EvaluationCaseInput.model_validate(
            {
                "message": "Reject manifest input.",
                "attachment": {
                    "asset_id": "asset-1",
                    "sha256": ASSET_SHA256,
                },
            }
        )
    with pytest.raises(ValidationError):
        EvaluationVisionExpectation.model_validate(
            {**_expectation(), "page_count": "2"}
        )
    with pytest.raises(ValidationError):
        EvaluationVisionExpectation.model_validate(
            {
                **_expectation(),
                "content_anchors": [
                    {"kind": "ocr", "text": str(index)} for index in range(11)
                ],
            }
        )
    with pytest.raises(ValidationError):
        EvaluationVisionExpectation.model_validate(
            {
                **_expectation(),
                "content_anchors": [{"kind": "ocr", "text": "x" * 201}],
            }
        )
    with pytest.raises(ValidationError):
        EvaluationCaseInput.model_validate(
            {
                "message": "Too many visual expectations.",
                "vision": [
                    _expectation(node_ref=f"inspect_asset_{index}")
                    for index in range(21)
                ],
            }
        )


def test_preflight_dataset_reference_is_optional_but_must_be_paired() -> None:
    candidate = {
        "kind": "proposal",
        "proposal_id": "proposal-1",
        "proposal_revision": 1,
    }

    legacy = EvaluationPreflightRequest.model_validate({"candidates": [candidate]})
    pinned = EvaluationPreflightRequest.model_validate(
        {
            "dataset_id": "dataset-1",
            "dataset_version": 2,
            "case_ids": ["case-1"],
            "candidates": [candidate],
        }
    )

    assert legacy.dataset_id is None
    assert legacy.case_ids == []
    assert pinned.dataset_version == 2
    with pytest.raises(ValidationError, match="必须同时提供"):
        EvaluationPreflightRequest.model_validate(
            {"dataset_id": "dataset-1", "candidates": [candidate]}
        )


def test_capture_returns_only_safe_evidence_and_boolean_anchor_checks() -> None:
    evidence = capture_vision_evidence(
        "inspect_asset",
        _payload(),
        [
            EvaluationVisionExpectation.model_validate(
                _expectation(node_ref="other_node", content_anchors=[])
            ),
            EvaluationVisionExpectation.model_validate(_expectation()),
        ],
        ASSET_SHA256,
        MODEL_ID,
    )

    assert evidence["block_counts"] == {
        "ocr": 1,
        "description": 1,
        "table": 1,
        "chart": 1,
    }
    assert evidence["anchor_checks"] == [
        {"index": 0, "matched": True},
        {"index": 1, "matched": True},
    ]
    assert evidence["execution_summary"] == {
        "model_calls": 1,
        "known_total_tokens": 37,
        "unverified_token_calls": 0,
        "token_usage_verified": True,
        "token_source": "managed_receipt",
        "uncertain_calls": 0,
    }
    serialized = json.dumps(evidence, ensure_ascii=False)
    for private_value in (
        SECRET_OCR,
        SECRET_DESCRIPTION,
        "must-not-leak",
        "must-never-count-as-vision-evidence",
    ):
        assert private_value not in serialized
    assert "payload" not in evidence


def test_capture_uses_empty_expectation_for_unasserted_actual_node() -> None:
    evidence = capture_vision_evidence(
        "meter_only",
        _payload(),
        [_expectation()],
        ASSET_SHA256,
        MODEL_ID,
    )

    assert evidence["node_ref"] == "meter_only"
    assert evidence["anchor_checks"] == []


@pytest.mark.parametrize(
    "payload",
    [
        _payload(asset_sha256="b" * 64),
        _payload(model_id="managed/other-model"),
    ],
)
def test_capture_rejects_runtime_identity_outside_fixed_binding(payload: dict) -> None:
    with pytest.raises(ValueError, match="固定"):
        _safe_evidence(payload=payload)


def test_capture_records_status_blocks_and_anchor_misses_without_rejecting() -> None:
    evidence = capture_vision_evidence(
        "inspect_asset",
        _payload(
            processed_page_count=1,
            failed_page_count=1,
            blocks=[_block("image_ocr", SECRET_OCR)],
        ),
        [
            _expectation(
                status="success",
                required_blocks=["chart"],
                content_anchors=[{"kind": "ocr", "text": "absent anchor"}],
            )
        ],
        ASSET_SHA256,
        MODEL_ID,
    )

    assert evidence["status"] == "partial"
    assert evidence["processed_page_count"] == 1
    assert evidence["block_counts"]["chart"] == 0
    assert evidence["anchor_checks"] == [{"index": 0, "matched": False}]


@pytest.mark.asyncio
async def test_correct_final_answer_cannot_replace_missing_vision_execution() -> None:
    result = await evaluate_case_metrics(
        case={
            "message": "Inspect the attachment.",
            "expected": {"exact_answer": "correct"},
            "vision": [_expectation()],
        },
        output="correct",
        citations={},
        vision_reads=[],
    )

    assert next(item for item in result["metrics"] if item["kind"] == "exact_match")[
        "score"
    ] == 1.0
    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expectation_updates",
    [
        {"asset_sha256": "b" * 64},
        {"model_id": "managed/wrong-model"},
    ],
)
async def test_wrong_asset_hash_or_model_fails_metric(expectation_updates: dict) -> None:
    evidence = _safe_evidence()
    result = await evaluate_case_metrics(
        case={"vision": [_expectation(**expectation_updates)]},
        output="ignored",
        citations={},
        vision_reads=[evidence],
    )

    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _payload(dispatched=False),
        _payload(
            page_count=0,
            selected_page_count=0,
            processed_page_count=0,
            blocks=[],
        ),
        _payload(
            page_count=1,
            selected_page_count=1,
            processed_page_count=0,
            failed_page_count=1,
        ),
        _payload(execution_mode="legacy"),
    ],
)
async def test_invalid_runtime_payload_is_not_captured_and_metric_fails(
    payload: dict,
) -> None:
    with pytest.raises(ValueError):
        _safe_evidence(payload=payload)

    result = await evaluate_case_metrics(
        case={"vision": [_expectation()]},
        output="ignored",
        citations={},
        vision_reads=[],
    )

    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["block", "anchor"])
async def test_missing_required_block_or_content_anchor_fails(failure: str) -> None:
    expectation = (
        _expectation(required_blocks=["chart"], content_anchors=[])
        if failure == "block"
        else _expectation(
            required_blocks=["ocr"],
            content_anchors=[{"kind": "ocr", "text": "not in the attachment"}],
        )
    )
    payload = _payload(blocks=[_block("image_ocr", SECRET_OCR)])
    evidence = _safe_evidence(payload=payload, assertions=[expectation])

    result = await evaluate_case_metrics(
        case={"vision": [expectation]},
        output="not in the attachment",
        citations={},
        vision_reads=[evidence],
    )

    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
async def test_status_mismatch_fails_metric_after_successful_capture() -> None:
    evidence = _safe_evidence(
        payload=_payload(processed_page_count=1, failed_page_count=1),
        assertions=[_expectation(status="success")],
    )

    result = await evaluate_case_metrics(
        case={"vision": [_expectation(status="success")]},
        output="ignored",
        citations={},
        vision_reads=[evidence],
    )

    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "missing_anchor",
        "duplicate_anchor",
        "non_boolean_anchor",
        "bad_summary",
        "unknown_field",
    ],
)
async def test_malformed_safe_evidence_fails_metric(corruption: str) -> None:
    evidence = _safe_evidence()
    if corruption == "missing_anchor":
        evidence["anchor_checks"] = evidence["anchor_checks"][:1]
    elif corruption == "duplicate_anchor":
        evidence["anchor_checks"] = [
            {"index": 0, "matched": True},
            {"index": 0, "matched": True},
        ]
    elif corruption == "non_boolean_anchor":
        evidence["anchor_checks"][0]["matched"] = 1
    elif corruption == "bad_summary":
        evidence["execution_summary"]["token_source"] = "untrusted"
    else:
        evidence["blocks"] = []

    result = await evaluate_case_metrics(
        case={"vision": [_expectation()]},
        output="ignored",
        citations={},
        vision_reads=[evidence],
    )

    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
async def test_duplicate_node_refs_fail_schema_and_runtime_metric() -> None:
    with pytest.raises(ValidationError, match="node_ref 必须唯一"):
        EvaluationCaseInput.model_validate(
            {
                "message": "Duplicate expectations.",
                "vision": [_expectation(), _expectation()],
            }
        )

    read = _safe_evidence()
    result = await evaluate_case_metrics(
        case={"vision": [_expectation()]},
        output="ignored",
        citations={},
        vision_reads=[read, deepcopy(read)],
    )

    assert _vision_metric(result)["score"] == 0.0
    assert result["vision_evidence"] == "failed"


@pytest.mark.asyncio
async def test_old_text_metrics_remain_compatible_and_missing_state_is_explicit() -> None:
    legacy = await evaluate_case_metrics(
        case={"expected": {"contains": ["hello"]}},
        output="Hello world",
        citations={},
    )
    required = await evaluate_case_metrics(
        case={"expected": {}},
        output="irrelevant",
        citations={},
        vision_evidence_required=True,
    )

    assert legacy["score"] == 1.0
    assert legacy["metric_count"] == 1
    assert legacy["vision_evidence"] == "not_applicable"
    assert required["metric_count"] == 0
    assert required["vision_evidence"] == "missing"


def test_report_aggregates_only_safe_vision_usage_and_keeps_legacy_fields() -> None:
    report = aggregate_evaluation_report(
        [
            {
                "target_id": "target",
                "target_label": "Target",
                "case_id": "case-1",
                "repetition": 1,
                "status": "completed",
                "score": 1.0,
                "metrics": [],
                "latency_ms": 10,
                "usage": {
                    "model_calls": 3,
                    "tool_calls": 1,
                    "estimated_tokens": 100,
                    "vision_model_calls": 2,
                    "vision_actual_tokens": 40,
                    "vision_unverified_usage_calls": 0,
                    "vision_uncertain_dispatches": 0,
                    "vision_token_usage_verified": True,
                    "vision_token_estimate": False,
                },
            },
            {
                "target_id": "target",
                "target_label": "Target",
                "case_id": "case-2",
                "repetition": 1,
                "status": "failed",
                "score": 0.0,
                "metrics": [],
                "latency_ms": 20,
                "usage": {
                    "model_calls": 1,
                    "tool_calls": 0,
                    "estimated_tokens": 0,
                    "vision_model_calls": 0,
                    "vision_actual_tokens": 0,
                    "vision_unverified_usage_calls": 1,
                    "vision_uncertain_dispatches": 1,
                    "vision_token_usage_verified": False,
                    "vision_token_estimate": False,
                },
            },
            {
                "target_id": "target",
                "target_label": "Target",
                "case_id": "case-3",
                "repetition": 1,
                "status": "completed",
                "score": 0.5,
                "metrics": [],
                "latency_ms": 30,
                "usage": {
                    "model_calls": 4,
                    "tool_calls": 2,
                    "estimated_tokens": 25,
                },
            },
            {
                "target_id": "verified",
                "target_label": "Verified",
                "case_id": "case-1",
                "repetition": 1,
                "status": "completed",
                "score": 1.0,
                "metrics": [],
                "latency_ms": 5,
                "usage": {
                    "vision_model_calls": 1,
                    "vision_actual_tokens": 7,
                    "vision_unverified_usage_calls": 0,
                    "vision_uncertain_dispatches": 0,
                    "vision_token_usage_verified": True,
                    "vision_token_estimate": False,
                },
            },
        ],
        baseline_target_id=None,
    )

    target = next(item for item in report["targets"] if item["target_id"] == "target")
    assert target["model_calls"] == 8
    assert target["tool_calls"] == 3
    assert target["estimated_tokens"] == 125
    assert target["vision_model_calls"] == 2
    assert target["vision_actual_tokens"] == 40
    assert target["vision_unverified_usage_calls"] == 1
    assert target["vision_uncertain_dispatches"] == 1
    assert target["vision_token_usage_verified"] is False
    assert target["vision_token_estimate"] is False
    verified = next(
        item for item in report["targets"] if item["target_id"] == "verified"
    )
    assert verified["vision_model_calls"] == 1
    assert verified["vision_actual_tokens"] == 7
    assert verified["vision_token_usage_verified"] is True
    assert verified["vision_token_estimate"] is False


def test_report_ignores_malformed_vision_usage_and_never_marks_it_verified() -> None:
    report = aggregate_evaluation_report(
        [
            {
                "target_id": "target",
                "target_label": "Target",
                "case_id": "case-1",
                "repetition": 1,
                "status": "completed",
                "score": 1.0,
                "metrics": [],
                "latency_ms": 10,
                "usage": {
                    "vision_model_calls": 1,
                    "vision_actual_tokens": "private-or-untrusted",
                    "vision_unverified_usage_calls": 0,
                    "vision_uncertain_dispatches": 0,
                    "vision_token_usage_verified": True,
                    "vision_token_estimate": False,
                },
            }
        ],
        baseline_target_id=None,
    )

    target = report["targets"][0]
    assert target["vision_model_calls"] == 0
    assert target["vision_actual_tokens"] == 0
    assert target["vision_token_usage_verified"] is False
    assert target["vision_token_estimate"] is False
