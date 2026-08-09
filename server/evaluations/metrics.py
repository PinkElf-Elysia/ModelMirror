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
    judge: JudgeCallback | None = None,
    judge_model_id: str | None = None,
) -> dict[str, Any]:
    expected = dict(case.get("expected") or {})
    weights = dict(case.get("weights") or {})
    metrics: list[dict[str, Any]] = []

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
