from __future__ import annotations

from typing import Any, Callable

try:
    from server.xpert_runtime.workflow_vision import validate_vision_v2_request
except ModuleNotFoundError:
    from xpert_runtime.workflow_vision import validate_vision_v2_request

from .models import EvaluationVisionExpectation
from .store import EvaluationStateError
from .vision_fixtures import MAX_EVALUATION_RUN_ATTACHMENT_BYTES


def inspect_vision_node(
    node: Any,
    *,
    nested: bool,
    binding_resolver: Callable[[str, str], dict[str, Any]] | None,
) -> dict[str, Any]:
    data = node.data
    if nested:
        raise EvaluationStateError("嵌套外部 Xpert 的视觉附件来源未授权，本轮禁止附件委托。")
    if type(data.get("contractVersion")) is not int or data["contractVersion"] != 2:
        raise EvaluationStateError("视觉评测必须使用 V2 契约。")
    try:
        node_ref = EvaluationVisionExpectation.model_validate(
            {"node_ref": data.get("plannerRef")}
        ).node_ref
    except ValueError as exc:
        raise EvaluationStateError("视觉评测需要有效的 Planner ref，不能推测物理节点身份。") from exc
    binding = validate_vision_v2_request(
        data,
        selected_asset_id="fixed-at-run-creation",
        runtime_run_type="xpert_evaluation",
        runtime_metadata={},
    )
    if binding_resolver is None or binding_resolver("xpert_vision", binding["model_id"]) != binding:
        raise EvaluationStateError("视觉 Managed Binding 已失效或发生漂移。")
    max_pages = data.get("maxPages")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise EvaluationStateError("视觉 V2 页数必须为 1 至 20。")
    if data.get("pdfPageStrategy") not in {"all", "auto", "scanned_only"}:
        raise EvaluationStateError("视觉页面策略无效。")
    return {
        "node_id": node.id,
        "node_ref": node_ref,
        "contract_version": 2,
        "model_id": binding["model_id"],
        "binding_checksum": binding["checksum"],
        "max_pages": max_pages,
        "pdf_page_strategy": data["pdfPageStrategy"],
    }


def validate_vision_dataset(
    targets: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    *,
    dataset_version: dict[str, Any] | None,
    fixtures: Any | None,
) -> list[str]:
    vision_targets = [target for target in targets if (target.get("resources") or {}).get("vision_models")]
    attachment_cases = [case for case in cases if case.get("attachment")]
    if not vision_targets and not attachment_cases:
        return []
    if not dataset_version or fixtures is None or not cases:
        raise EvaluationStateError("视觉预检需要已发布评测版本及固定附件。")
    dataset_id = dataset_version["dataset_id"]
    version = dataset_version["version"]
    unique_originals: dict[str, int] = {}
    warnings: list[str] = []
    for case in cases:
        attachment = case.get("attachment")
        if not attachment:
            if vision_targets or case.get("vision"):
                raise EvaluationStateError("视觉目标的每条选中用例都必须具有固定附件。")
            continue
        # This reads and verifies only. Version and run bindings are never added in preflight.
        actual = fixtures.inspect_dataset_version_asset(dataset_id, version, attachment)
        unique_originals[actual["sha256"]] = actual["byte_size"]
        for target in vision_targets:
            for requirement in target["resources"]["vision_models"]:
                if actual["page_count"] > requirement["max_pages"]:
                    raise EvaluationStateError("固定附件页数超过视觉节点上限，不会截取或发送部分页面。")
        if vision_targets and not case.get("vision"):
            warnings.append("部分用例未配置视觉断言，最终答案正确不能证明视觉能力已验证。")
    if sum(unique_originals.values()) > MAX_EVALUATION_RUN_ATTACHMENT_BYTES:
        raise EvaluationStateError("本次运行附件原件去重后超过 100 MiB。")
    return list(dict.fromkeys(warnings))
