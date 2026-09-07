from __future__ import annotations

from typing import Any

from .store import EvaluationStateError, XpertEvaluationStore

try:
    from server.multimodal.vision_v2 import VisionExecutionRejected
except ModuleNotFoundError:
    from multimodal.vision_v2 import VisionExecutionRejected


def prepare_vision_input(
    store: XpertEvaluationStore,
    target: dict[str, Any],
    case: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    requires_vision = bool((target.get("resources") or {}).get("vision_models"))
    if not requires_vision and not case.get("attachment"):
        return {}
    fixture = store.vision_fixture_for_item(
        str(config.get("evaluation_run_id") or ""),
        target_id=target["target_id"], case_id=case["case_id"],
        item_id=str(config.get("evaluation_item_id") or ""),
    )
    return {"selected_file_asset_id": fixture["asset_id"]}


def record_vision_dispatch(
    store: XpertEvaluationStore, metadata: dict[str, Any], *, node_ref: str, page_number: int
) -> None:
    store.mark_vision_dispatch(
        str(metadata.get("evaluation_run_id") or ""),
        str(metadata.get("evaluation_item_id") or ""),
        node_ref=node_ref, page_number=page_number,
    )


def record_evaluation_vision_receipt(store: XpertEvaluationStore, metadata: dict[str, Any], *, node_ref: str, receipt: dict[str, Any]) -> None:
    run_id = str(metadata.get("evaluation_run_id") or "")
    try:
        total = store.record_vision_receipt(run_id, str(metadata.get("evaluation_item_id") or ""), node_ref=node_ref, receipt=receipt)
    except Exception as exc:
        raise VisionExecutionRejected("视觉调用回执无法持久化，已停止后续请求。") from exc
    budget = (store.require_run(run_id).get("config") or {}).get("budget") or {}
    if total > int(budget.get("max_estimated_tokens") or 64_000):
        raise VisionExecutionRejected("视觉实际 Token 用量已超过本例预算，已停止后续请求。", code="workflow_vision_token_budget_exhausted")


def capture_evaluation_vision(
    store: XpertEvaluationStore,
    metadata: dict[str, Any],
    *,
    node_ref: str,
    node_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from .vision_evidence import capture_vision_evidence

    run_id = str(metadata.get("evaluation_run_id") or "")
    target_id = str(metadata.get("evaluation_target_id") or "")
    case_id = str(metadata.get("evaluation_case_id") or "")
    fixture = store.vision_fixture_for_item(run_id, target_id=target_id, case_id=case_id, item_id=str(metadata.get("evaluation_item_id") or ""))
    run = store.require_run(run_id)
    target = next(item for item in run["targets"] if item["target_id"] == target_id)
    case = next(item for item in run["dataset"]["cases"] if item["case_id"] == case_id)
    requirements = [item for item in target["resources"].get("vision_models", []) if item["node_id"] == node_id]
    if len(requirements) != 1:
        raise EvaluationStateError("视觉执行节点与固定目标不一致。")
    return capture_vision_evidence(
        node_ref=node_ref, payload=payload, assertions=case.get("vision") or [],
        expected_asset_sha256=fixture["sha256"], expected_model_id=requirements[0]["model_id"],
    )


def vision_usage(reads: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [entry.get("execution_summary") or {} for entry in reads]
    return {
        "vision_actual_tokens": sum(int(item.get("known_total_tokens") or 0) for item in summaries),
        "vision_token_usage_verified": bool(summaries) and all(item.get("token_usage_verified") is True for item in summaries),
        "vision_token_estimate": False,
        "vision_unverified_usage_calls": sum(int(item.get("unverified_token_calls") or 0) for item in summaries),
    }
