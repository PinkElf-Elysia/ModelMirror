from __future__ import annotations

from typing import Any

try:
    from server.multimodal.vision_v2 import VisionModelBindingSnapshot
except ModuleNotFoundError:
    from multimodal.vision_v2 import VisionModelBindingSnapshot


ATTACHMENT_INPUT_PORT = "selected_file_asset_id"
VISION_ATTACHMENT_CONTRACT = {
    "port": ATTACHMENT_INPUT_PORT,
    "source_ref": "input",
    "cardinality": "one",
    "formats": ["png", "jpeg", "webp", "pdf"],
    "max_bytes": 10 * 1024 * 1024,
    "max_pages": 20,
    "trusted_runtime_input": True,
}


def resolve_planner_vision_model(
    model_id: str | None,
    models: list[dict[str, Any]],
    *,
    pinned: dict[str, Any] | None = None,
) -> VisionModelBindingSnapshot:
    if not model_id:
        raise ValueError("请单独选择并固定视觉模型，Planner 不会自动选型。")
    matches = [item for item in models if item.get("id") == model_id and item.get("safe") is True]
    if len(matches) != 1:
        raise ValueError("所选视觉模型不具备当前可调用的图像能力和 Managed Binding。")
    binding = VisionModelBindingSnapshot.model_validate(matches[0].get("binding"))
    if binding.entry_id != "xpert_vision" or binding.model_id != model_id:
        raise ValueError("视觉模型 Binding 不属于当前 Xpert 入口。")
    if pinned is not None and VisionModelBindingSnapshot.model_validate(pinned) != binding:
        raise ValueError("固定视觉模型的 Managed Binding 已变化，请重新预检。")
    return binding


def safe_planner_vision_models(catalog: Any, vision_service: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in getattr(catalog, "profiles", ()):
        if (
            profile.operation != "analyze_image"
            or not profile.invocable
            or profile.interaction_status != "ready"
        ):
            continue
        try:
            binding = VisionModelBindingSnapshot.model_validate(
                vision_service.managed_binding_snapshot("xpert_vision", profile.model_id)
            )
        except Exception:
            continue
        rows.append({
            "id": profile.model_id,
            "label": str(profile.display_name or profile.model_id)[:200],
            "safe": True,
            "binding": binding.model_dump(mode="json"),
        })
    return sorted(rows, key=lambda row: row["id"])


def project_vision_model_rows(rows: Any) -> list[dict[str, Any]]:
    projected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("safe") is not True:
            continue
        try:
            binding = VisionModelBindingSnapshot.model_validate(row.get("binding"))
        except ValueError:
            continue
        if binding.entry_id != "xpert_vision" or row.get("id") != binding.model_id:
            continue
        safe = {
            "id": binding.model_id,
            "label": str(row.get("label") or binding.model_id)[:200],
            "safe": True,
            "binding": binding.model_dump(mode="json"),
        }
        if binding.model_id in projected and projected[binding.model_id] != safe:
            raise ValueError("视觉模型目录存在冲突的 Managed Binding。")
        projected[binding.model_id] = safe
    return [projected[key] for key in sorted(projected)]


def vision_result_is_consumed(ref: str, nodes: list[Any]) -> bool:
    from .node_adapters import get_planner_node_adapter

    visited = {ref}
    pending = [ref]
    while pending:
        source_ref = pending.pop()
        for node in nodes:
            used = [item for item in node.inputs if item.source_ref == source_ref]
            if not used:
                continue
            if node.kind == "workflow_agent":
                adapter = get_planner_node_adapter(node.kind)
                parsed = adapter.validate_intent_node(node)
                if {item.variable for item in used} & adapter.referenced_input_variables(parsed):
                    return True
                continue
            if node.ref not in visited:
                visited.add(node.ref)
                pending.append(node.ref)
    return False


def validate_vision_generation_authorization(request: Any, snapshot: Any, target: Any = None) -> None:
    if "vision_understanding" not in request.scope.allowed_node_kinds:
        if request.vision_model_id:
            raise ValueError("选择视觉模型前必须显式授权视觉理解节点。")
        return
    resolve_planner_vision_model(request.vision_model_id, snapshot.vision_models)
    if target is not None and not target.draft.features.file_upload.enabled:
        raise ValueError("目标 Xpert 已关闭文件输入，不能生成视觉附件节点。")
