from __future__ import annotations

import json
import math
from statistics import mean
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator


JudgeCallback = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]


async def evaluate_case_metrics(
    *,
    case: dict[str, Any],
    output: str,
    citations: dict[str, list[str]],
    tool_calls: list[str] | None = None,
    control_flow: dict[str, Any] | None = None,
    resource_reads: list[dict[str, Any]] | None = None,
    resource_evidence_required: bool = False,
    vision_reads: list[dict[str, Any]] | None = None,
    vision_evidence_required: bool = False,
    judge: JudgeCallback | None = None,
    judge_model_id: str | None = None,
) -> dict[str, Any]:
    expected = dict(case.get("expected") or {})
    weights = dict(case.get("weights") or {})
    metrics: list[dict[str, Any]] = []

    expected_reads = [
        dict(item)
        for item in list(case.get("resource_reads") or [])
        if isinstance(item, dict)
    ]
    resource_evidence = (
        "missing"
        if not expected_reads and resource_evidence_required
        else "not_applicable"
    )
    if expected_reads:
        actual_by_key = {
            (
                str(item.get("node_ref") or ""),
                str(item.get("resource_kind") or item.get("kind") or ""),
            ): item
            for item in list(resource_reads or [])
            if isinstance(item, dict)
        }
        matched = 0
        failures: list[str] = []
        for expected_read in expected_reads:
            key = (
                str(expected_read.get("node_ref") or ""),
                str(expected_read.get("kind") or ""),
            )
            actual = actual_by_key.get(key)
            checks = _resource_read_checks(expected_read, actual)
            if checks:
                failures.append(f"{key[0]}:{','.join(checks)}")
            else:
                matched += 1
        metrics.append(
            _metric(
                "workflow_resource_match",
                matched / len(expected_reads),
                (
                    f"Matched {matched} of {len(expected_reads)} resource reads."
                    + (f" Failures: {'; '.join(failures[:8])}" if failures else "")
                ),
                weights,
            )
        )
        resource_evidence = "verified" if matched == len(expected_reads) else "failed"

    raw_vision_expectations = list(case.get("vision") or [])
    expected_vision = [
        dict(item) for item in raw_vision_expectations if isinstance(item, dict)
    ]
    vision_evidence = (
        "missing"
        if not raw_vision_expectations and vision_evidence_required
        else "not_applicable"
    )
    if raw_vision_expectations:
        raw_actual_reads = list(vision_reads or [])
        actual_reads = [dict(item) for item in raw_actual_reads if isinstance(item, dict)]
        expected_refs = [str(item.get("node_ref") or "") for item in expected_vision]
        actual_refs = [str(item.get("node_ref") or "") for item in actual_reads]
        duplicate_expected = _duplicate_values(expected_refs)
        duplicate_actual = _duplicate_values(actual_refs)
        structural_failure = len(expected_vision) != len(raw_vision_expectations)
        failures: list[str] = []
        if structural_failure:
            failures.append("视觉证据期望格式无效")
        if len(actual_reads) != len(raw_actual_reads):
            structural_failure = True
            failures.append("执行证据格式无效")
        if duplicate_expected:
            structural_failure = True
            failures.append("期望包含重复 node_ref")
        if duplicate_actual:
            structural_failure = True
            failures.append("执行证据包含重复 node_ref")

        actual_by_ref = {
            str(item.get("node_ref") or ""): item for item in actual_reads
        }
        matched = 0
        if not structural_failure:
            for expectation in expected_vision:
                node_ref = str(expectation.get("node_ref") or "")
                actual = actual_by_ref.get(node_ref)
                checks = _vision_evidence_checks(expectation, actual)
                if checks:
                    failures.append(f"{node_ref or 'unknown'}:{','.join(checks)}")
                    continue
                matched += 1

        score = (
            0.0
            if structural_failure
            else matched / len(raw_vision_expectations)
        )
        metrics.append(
            _metric(
                "workflow_vision_match",
                score,
                (
                    f"视觉证据匹配 {matched}/{len(raw_vision_expectations)}。"
                    + (f" 失败：{'; '.join(failures[:8])}" if failures else "")
                ),
                weights,
            )
        )
        vision_evidence = "verified" if score >= 0.999 else "failed"

    path = case.get("path")
    if isinstance(path, dict):
        actual_path = dict(control_flow or {})
        supported = bool(actual_path.get("supported"))
        actual_outcomes = {
            str(item) for item in list(actual_path.get("outcomes") or []) if str(item)
        }
        required = _string_set(path.get("required_outcomes"))
        forbidden = _string_set(path.get("forbidden_outcomes"))
        expected_terminal = str(path.get("terminal") or "")
        actual_terminal = str(actual_path.get("terminal") or "")
        expected_error = str(path.get("error_code") or "")
        actual_error = str(actual_path.get("error_code") or "")
        missing = sorted(required - actual_outcomes)
        forbidden_hits = sorted(forbidden & actual_outcomes)
        terminal_ok = actual_terminal == expected_terminal
        error_ok = expected_terminal != "error" or actual_error == expected_error
        passed = (
            supported
            and not missing
            and not forbidden_hits
            and terminal_ok
            and error_ok
        )
        metrics.append(
            _metric(
                "workflow_path_match",
                1.0 if passed else 0.0,
                (
                    f"supported={supported}, missing={missing}, "
                    f"forbidden_hits={forbidden_hits}, "
                    f"terminal={actual_terminal or 'unknown'}/{expected_terminal}, "
                    f"error_code={actual_error or 'none'}/{expected_error or 'none'}"
                ),
                weights,
            )
        )

    exact = expected.get("exact_answer")
    if isinstance(exact, str):
        metrics.append(
            _metric(
                "exact_match",
                1.0 if _normalize(output) == _normalize(exact) else 0.0,
                "Normalized final output matched the expected answer."
                if _normalize(output) == _normalize(exact)
                else "Normalized final output did not match the expected answer.",
                weights,
            )
        )

    contains = [
        str(item).strip()
        for item in list(expected.get("contains") or [])
        if str(item).strip()
    ]
    if contains:
        normalized = _normalize(output)
        hits = sum(1 for item in contains if _normalize(item) in normalized)
        metrics.append(
            _metric(
                "contains",
                hits / len(contains),
                f"Matched {hits} of {len(contains)} required text fragments.",
                weights,
            )
        )

    schema = expected.get("json_schema")
    if isinstance(schema, dict):
        score = 0.0
        reason = "Final output was not valid JSON."
        try:
            parsed = json.loads(output)
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(parsed)
            score = 1.0
            reason = "Final output satisfied the configured JSON Schema."
        except Exception as exc:
            reason = f"JSON Schema validation failed: {str(exc)[:300]}"
        metrics.append(_metric("json_schema", score, reason, weights))

    expected_citations = {
        "citation_ids": _string_set(expected.get("citation_ids")),
        "chunk_ids": _string_set(expected.get("chunk_ids")),
        "document_names": {
            item.casefold() for item in _string_set(expected.get("document_names"))
        },
    }
    citation_total = sum(len(values) for values in expected_citations.values())
    if citation_total:
        actual = {
            "citation_ids": _string_set(citations.get("citation_ids")),
            "chunk_ids": _string_set(citations.get("chunk_ids")),
            "document_names": {
                item.casefold()
                for item in _string_set(citations.get("document_names"))
            },
        }
        matched = sum(
            len(expected_citations[key] & actual[key]) for key in expected_citations
        )
        metrics.append(
            _metric(
                "citation_hit",
                matched / citation_total,
                f"Matched {matched} of {citation_total} expected citation references.",
                weights,
            )
        )

    required_tools = _string_list(expected.get("required_tools"))
    forbidden_tools = _string_list(expected.get("forbidden_tools"))
    expected_order = _string_list(expected.get("tool_order"))
    if required_tools or forbidden_tools or expected_order:
        actual_tools = [str(item) for item in list(tool_calls or []) if str(item)]
        required_hits = sum(item in actual_tools for item in required_tools)
        forbidden_hits = sum(item in actual_tools for item in forbidden_tools)
        order_ok = _is_subsequence(expected_order, actual_tools)
        checks = len(required_tools) + len(forbidden_tools) + (1 if expected_order else 0)
        passed_checks = (
            required_hits
            + (len(forbidden_tools) - forbidden_hits)
            + (1 if expected_order and order_ok else 0)
        )
        metrics.append(
            _metric(
                "tool_call_match",
                passed_checks / checks if checks else 0.0,
                (
                    f"required={required_hits}/{len(required_tools)}, "
                    f"forbidden_hits={forbidden_hits}, "
                    f"order={'matched' if order_ok else 'mismatched'}"
                ),
                weights,
            )
        )
    rubric = expected.get("rubric")
    if isinstance(rubric, str) and rubric.strip():
        if judge is None or not judge_model_id:
            metrics.append(
                _metric(
                    "rubric_judge",
                    0.0,
                    "Rubric judge was requested but no judge model was configured.",
                    weights,
                )
            )
        else:
            judged = await judge(
                judge_model_id,
                str(case.get("message") or "")[:20_000],
                output[:20_000],
                rubric[:4_000],
            )
            metrics.append(
                _metric(
                    "rubric_judge",
                    max(0.0, min(float(judged.get("score") or 0.0), 1.0)),
                    str(judged.get("reason") or "")[:500],
                    weights,
                    passed=bool(judged.get("passed")),
                )
            )

    total_weight = sum(float(item["weight"]) for item in metrics)
    total_score = (
        sum(float(item["score"]) * float(item["weight"]) for item in metrics)
        / total_weight
        if total_weight
        else 0.0
    )
    return {
        "score": round(total_score, 6),
        "metrics": metrics,
        "metric_count": len(metrics),
        "resource_evidence": resource_evidence,
        "vision_evidence": vision_evidence,
    }


def aggregate_evaluation_report(
    items: list[dict[str, Any]],
    *,
    baseline_target_id: str | None,
) -> dict[str, Any]:
    by_target: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_target.setdefault(str(item.get("target_id") or ""), []).append(item)

    targets: list[dict[str, Any]] = []
    for target_id, target_items in by_target.items():
        completed = [item for item in target_items if item.get("status") == "completed"]
        scores = [float(item.get("score") or 0.0) for item in target_items]
        latencies = sorted(float(item.get("latency_ms") or 0.0) for item in target_items)
        per_metric: dict[str, list[float]] = {}
        for item in completed:
            for metric in list(item.get("metrics") or []):
                per_metric.setdefault(str(metric.get("kind") or ""), []).append(
                    float(metric.get("score") or 0.0)
                )
        targets.append(
            {
                "target_id": target_id,
                "label": str(target_items[0].get("target_label") or target_id),
                "score": round(mean(scores), 6) if scores else 0.0,
                "case_count": len(target_items),
                "completed_count": len(completed),
                "failed_count": len(target_items) - len(completed),
                "metrics": {
                    name: round(mean(values), 6)
                    for name, values in sorted(per_metric.items())
                },
                "average_latency_ms": round(mean(latencies), 3) if latencies else 0.0,
                "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
                "model_calls": sum(
                    int((item.get("usage") or {}).get("model_calls") or 0)
                    for item in target_items
                ),
                "tool_calls": sum(
                    int((item.get("usage") or {}).get("tool_calls") or 0)
                    for item in target_items
                ),
                "estimated_tokens": sum(
                    int((item.get("usage") or {}).get("estimated_tokens") or 0)
                    for item in target_items
                ),
                **_aggregate_vision_usage(target_items),
                "resource_evidence": _resource_evidence_summary(target_items),
            }
        )
    targets.sort(key=lambda item: (-float(item["score"]), item["target_id"]))

    comparisons: list[dict[str, Any]] = []
    if baseline_target_id and baseline_target_id in by_target:
        baseline_by_case = _case_scores(by_target[baseline_target_id])
        for target in targets:
            if target["target_id"] == baseline_target_id:
                continue
            candidate_by_case = _case_scores(by_target.get(target["target_id"], []))
            wins = ties = losses = 0
            for case_key, baseline_score in baseline_by_case.items():
                candidate_score = candidate_by_case.get(case_key, 0.0)
                if candidate_score > baseline_score + 1e-9:
                    wins += 1
                elif candidate_score < baseline_score - 1e-9:
                    losses += 1
                else:
                    ties += 1
            baseline_target = next(
                item for item in targets if item["target_id"] == baseline_target_id
            )
            comparisons.append(
                {
                    "target_id": target["target_id"],
                    "baseline_target_id": baseline_target_id,
                    "score_delta": round(
                        float(target["score"]) - float(baseline_target["score"]), 6
                    ),
                    "wins": wins,
                    "ties": ties,
                    "losses": losses,
                }
            )
    return {"targets": targets, "comparisons": comparisons}


def _metric(
    kind: str,
    score: float,
    reason: str,
    weights: dict[str, Any],
    *,
    passed: bool | None = None,
) -> dict[str, Any]:
    normalized = max(0.0, min(float(score), 1.0))
    return {
        "kind": kind,
        "score": round(normalized, 6),
        "weight": max(0.0, min(float(weights.get(kind, 1.0)), 10.0)),
        "passed": normalized >= 0.999 if passed is None else passed,
        "reason": reason[:500],
    }


def _normalize(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in list(value or []) if str(item).strip()}


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    position = 0
    for item in actual:
        if item == expected[position]:
            position += 1
            if position == len(expected):
                return True
    return False


def _duplicate_values(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


_VISION_BLOCK_KINDS = {"ocr", "description", "table", "chart"}
_VISION_EVIDENCE_FIELDS = {
    "node_ref",
    "asset_id",
    "asset_sha256",
    "model_id",
    "page_count",
    "selected_page_count",
    "processed_page_count",
    "failed_page_count",
    "status",
    "block_counts",
    "anchor_checks",
    "execution_summary",
}
_VISION_SUMMARY_FIELDS = {
    "model_calls",
    "known_total_tokens",
    "unverified_token_calls",
    "token_usage_verified",
    "token_source",
    "uncertain_calls",
}


def _vision_evidence_checks(
    expected: dict[str, Any], actual: dict[str, Any] | None
) -> list[str]:
    if actual is None:
        return ["未执行"]

    failures: list[str] = []
    actual_fields = set(actual)
    if actual_fields != _VISION_EVIDENCE_FIELDS:
        failures.append("安全证据字段")

    node_ref = actual.get("node_ref")
    if not isinstance(node_ref, str) or node_ref != expected.get("node_ref"):
        failures.append("node_ref")
    asset_id = actual.get("asset_id")
    if (
        not isinstance(asset_id, str)
        or not 1 <= len(asset_id) <= 160
        or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in asset_id
        )
    ):
        failures.append("asset_id")
    asset_sha256 = actual.get("asset_sha256")
    if (
        not isinstance(asset_sha256, str)
        or len(asset_sha256) != 64
        or any(character not in "0123456789abcdef" for character in asset_sha256)
    ):
        failures.append("asset_sha256")
    elif expected.get("asset_sha256") is not None and asset_sha256 != expected.get(
        "asset_sha256"
    ):
        failures.append("asset_sha256")
    model_id = actual.get("model_id")
    if not isinstance(model_id, str) or not model_id or len(model_id) > 512:
        failures.append("model_id")
    elif expected.get("model_id") is not None and model_id != expected.get("model_id"):
        failures.append("model_id")

    page_values = {
        field: actual.get(field)
        for field in (
            "page_count",
            "selected_page_count",
            "processed_page_count",
            "failed_page_count",
        )
    }
    pages_valid = all(type(value) is int for value in page_values.values())
    if pages_valid:
        page_count = page_values["page_count"]
        selected = page_values["selected_page_count"]
        processed = page_values["processed_page_count"]
        failed = page_values["failed_page_count"]
        pages_valid = (
            1 <= page_count <= 20
            and 1 <= selected <= page_count
            and 1 <= processed <= selected
            and 0 <= failed <= selected
            and processed + failed == selected
        )
    if not pages_valid:
        failures.append("页面计数")
    elif expected.get("page_count") is not None and page_values[
        "page_count"
    ] != expected.get("page_count"):
        failures.append("page_count")

    status = actual.get("status")
    if status not in {"success", "partial"}:
        failures.append("status")
    else:
        if pages_valid:
            derived_status = (
                "partial" if page_values["failed_page_count"] else "success"
            )
            if status != derived_status:
                failures.append("status")
        if status != expected.get("status", "success"):
            failures.append("status")

    block_counts = actual.get("block_counts")
    if (
        not isinstance(block_counts, dict)
        or set(block_counts) != _VISION_BLOCK_KINDS
        or any(type(value) is not int or value < 0 for value in block_counts.values())
    ):
        failures.append("block_counts")
    else:
        missing_blocks = [
            kind
            for kind in list(expected.get("required_blocks") or [])
            if block_counts.get(kind, 0) == 0
        ]
        if missing_blocks:
            failures.append(f"缺少块类型 {missing_blocks}")

    anchors = actual.get("anchor_checks")
    expected_anchor_count = len(list(expected.get("content_anchors") or []))
    anchor_results: dict[int, bool] = {}
    anchors_valid = isinstance(anchors, list)
    if anchors_valid:
        for item in anchors:
            if (
                not isinstance(item, dict)
                or set(item) != {"index", "matched"}
                or type(item.get("index")) is not int
                or type(item.get("matched")) is not bool
                or item["index"] in anchor_results
            ):
                anchors_valid = False
                break
            anchor_results[item["index"]] = item["matched"]
    if not anchors_valid or set(anchor_results) != set(range(expected_anchor_count)):
        failures.append("锚点索引")
    elif not all(anchor_results.values()):
        failures.append("内容锚点")

    summary = actual.get("execution_summary")
    if not _valid_vision_execution_summary(summary):
        failures.append("execution_summary")
    return list(dict.fromkeys(failures))


def _valid_vision_execution_summary(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _VISION_SUMMARY_FIELDS:
        return False
    integer_fields = (
        "model_calls",
        "known_total_tokens",
        "unverified_token_calls",
        "uncertain_calls",
    )
    if any(
        type(value.get(field)) is not int or value[field] < 0
        for field in integer_fields
    ):
        return False
    model_calls = value["model_calls"]
    unverified = value["unverified_token_calls"]
    uncertain = value["uncertain_calls"]
    return (
        model_calls > 0
        and unverified <= model_calls
        and uncertain <= model_calls
        and type(value.get("token_usage_verified")) is bool
        and value["token_usage_verified"] is (unverified == 0)
        and value.get("token_source") == "managed_receipt"
    )


def sanitize_vision_reads(value: Any) -> list[dict[str, Any]]:
    """Reject non-evidence payloads before they can enter a persisted report."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("视觉执行证据数量或格式无效。")
    refs = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("视觉执行证据必须是安全摘要对象。")
        node_ref = item.get("node_ref")
        if not isinstance(node_ref, str) or not 1 <= len(node_ref) <= 64 or not node_ref[0].islower() or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in node_ref):
            raise ValueError("视觉执行证据节点引用无效。")
        refs.append(node_ref)
        anchors = item.get("anchor_checks")
        if not isinstance(anchors, list) or len(anchors) > 10:
            raise ValueError("视觉执行证据锚点数量无效。")
        checks = _vision_evidence_checks({
            "node_ref": node_ref, "status": item.get("status"),
            "content_anchors": [{} for _ in anchors],
        }, item)
        if set(checks) - {"内容锚点"}:
            raise ValueError("视觉执行证据包含无效或非安全字段。")
    if len(refs) != len(set(refs)):
        raise ValueError("视觉执行证据节点重复。")
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _resource_read_checks(
    expected: dict[str, Any], actual: dict[str, Any] | None
) -> list[str]:
    if actual is None:
        return ["missing"]
    failures: list[str] = []
    if str(actual.get("resource_id") or "") != str(expected.get("resource_id") or ""):
        failures.append("resource_id")
    for field in ("version_id", "schema_version", "query_checksum"):
        expected_value = expected.get(field)
        if expected_value is not None and actual.get(field) != expected_value:
            failures.append(field)
    if expected.get("expected_count") is not None and int(
        actual.get("result_count") or 0
    ) != int(expected["expected_count"]):
        failures.append("result_count")
    for field in ("record_ids", "citation_ids"):
        required = _string_set(expected.get(field))
        observed = _string_set(actual.get(field))
        if not required.issubset(observed):
            failures.append(field)
    return failures


def _resource_evidence_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    result = {"verified": 0, "failed": 0, "missing": 0, "not_applicable": 0}
    for item in items:
        status = str(item.get("resource_evidence") or "not_applicable")
        if status not in result:
            status = "failed"
        result[status] += 1
    return result


_VISION_USAGE_COUNT_FIELDS = (
    "vision_model_calls",
    "vision_actual_tokens",
    "vision_unverified_usage_calls",
    "vision_uncertain_dispatches",
)
_VISION_USAGE_FIELDS = {
    *_VISION_USAGE_COUNT_FIELDS,
    "vision_token_usage_verified",
    "vision_token_estimate",
}


def _aggregate_vision_usage(items: list[dict[str, Any]]) -> dict[str, Any]:
    safe_usage: list[dict[str, Any]] = []
    invalid_usage_seen = False
    for item in items:
        usage = item.get("usage")
        if not isinstance(usage, dict) or not (_VISION_USAGE_FIELDS & set(usage)):
            continue
        valid = (
            all(
                type(usage.get(field)) is int and usage[field] >= 0
                for field in _VISION_USAGE_COUNT_FIELDS
            )
            and type(usage.get("vision_token_usage_verified")) is bool
            and usage.get("vision_token_estimate") is False
        )
        if not valid:
            invalid_usage_seen = True
            continue
        safe_usage.append(usage)

    result = {
        field: sum(usage[field] for usage in safe_usage)
        for field in _VISION_USAGE_COUNT_FIELDS
    }
    result.update(
        {
            "vision_token_usage_verified": (
                bool(safe_usage)
                and not invalid_usage_seen
                and all(
                    usage["vision_token_usage_verified"] is True
                    for usage in safe_usage
                )
            ),
            "vision_token_estimate": False,
        }
    )
    return result


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(math.ceil(len(values) * quantile) - 1, len(values) - 1))
    return values[index]


def _case_scores(items: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for item in items:
        key = str(item.get("case_id") or "")
        grouped.setdefault(key, []).append(float(item.get("score") or 0.0))
    return {key: mean(values) for key, values in grouped.items()}
