from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .approval_store import RuntimeApprovalRequest, RuntimeApprovalStore
from .core_middlewares import RuntimeMiddlewareSpec
from .interrupts import RuntimeInterrupt, RuntimeMiddlewareFatalError
from .middleware import AgentMiddleware, TOOL_REVALIDATE_METADATA_KEY
from .models import MiddlewareContext, ToolCallRequest, ToolCallResponse


def build_human_in_the_loop_middleware(
    spec: RuntimeMiddlewareSpec,
    store: RuntimeApprovalStore,
    hub_event_recorder: Callable[[str, dict[str, Any]], None] | None = None,
) -> AgentMiddleware:
    config = dict(spec.config or {})
    rules = _tool_rules(config.get("interrupt_on_tools"))
    timeout_seconds = _bounded_int(config.get("timeout_seconds"), 3600, 30, 86_400)
    description_prefix = str(
        config.get("description_prefix") or "Tool execution requires approval"
    ).strip()[:2_000]
    allow_edit = _bool(config.get("allow_edit"), True)
    allow_reject = _bool(config.get("allow_reject"), True)

    async def wrap_tool_call(
        request: ToolCallRequest,
        handler: Any,
        context: MiddlewareContext,
    ) -> ToolCallResponse:
        if not _requires_review(request.tool_name, rules):
            return await handler(_without_server_revalidator(request))

        resolved = request.metadata.get("resolved_approval")
        if isinstance(resolved, dict):
            return await _apply_resolution(
                request,
                handler,
                resolved,
                hub_event_recorder=hub_event_recorder,
            )

        task_id = str(context.task_id or "").strip()
        run_id = str(context.metadata.get("run_id") or context.trace_id or "").strip()
        node_id = str(context.metadata.get("node_id") or "").strip()
        if not task_id or not run_id or not node_id:
            raise RuntimeMiddlewareFatalError(
                "HITL middleware requires task_id, run_id, and node_id."
            )
        iteration = int(request.metadata.get("iteration") or 0)
        action_key = str(
            request.metadata.get("approval_action_key")
            or f"{task_id}:{node_id}:{iteration}:{request.tool_name}"
        )
        scope_type, scope_id = _approval_scope(context, task_id)
        read_only_arguments = _bool(
            request.metadata.get("approval_read_only_args"), False
        )
        skill_approval = request.metadata.get("skill_approval")
        hub_approval = request.metadata.get("hub_approval")
        remote_approval = request.metadata.get("remote_approval")
        if read_only_arguments:
            allowed = ["approve"]
            if allow_reject:
                allowed.append("reject")
            approval_description = (
                f"{description_prefix}\n\nTool: {request.tool_name}\n"
                "参数已脱敏；批准后将按当前工作流变量重新解析并核对摘要。"
            )
        elif isinstance(hub_approval, dict):
            allowed = ["approve"]
            if allow_edit:
                allowed.append("edit")
            if allow_reject:
                allowed.append("reject")
            approval_description = (
                "未受信的 MCP Hub 工具需要逐次人工审批\n\n"
                f"Registry: {hub_approval.get('server_name') or '-'}\n"
                f"版本: {hub_approval.get('version') or '-'}\n"
                f"Origin: {hub_approval.get('origin') or '-'}\n"
                f"Schema: {hub_approval.get('schema_digest') or '-'}\n"
                f"工具 Schema: {hub_approval.get('tool_schema_digest') or '-'}\n"
                f"执行契约: {hub_approval.get('contract_id') or '-'}\n"
                f"契约指纹: {hub_approval.get('contract_fingerprint') or '-'}\n"
                f"工具: {request.tool_name}\n\n"
                "Registry 收录不代表安全认证；请核对来源、工具和完整脱敏参数。"
            )
        elif isinstance(remote_approval, dict):
            allowed = ["approve"]
            if allow_edit:
                allowed.append("edit")
            if allow_reject:
                allowed.append("reject")
            approval_description = (
                "已复核的 Catalog 远程 MCP 工具仍需要逐次人工审批\n\n"
                f"项目: {remote_approval.get('target_id') or '-'}\n"
                f"版本: {remote_approval.get('version') or '-'}\n"
                f"Origin: {remote_approval.get('origin') or '-'}\n"
                f"Schema: {remote_approval.get('schema_digest') or '-'}\n"
                f"工具 Schema: {remote_approval.get('tool_schema_digest') or '-'}\n"
                f"执行契约: {remote_approval.get('contract_id') or '-'}\n"
                f"契约指纹: {remote_approval.get('contract_fingerprint') or '-'}\n"
                f"工具: {request.tool_name}\n\n"
                "远程内容不受信；请核对来源、工具和完整脱敏参数。"
            )
        elif request.tool_name == "skill_install" and isinstance(skill_approval, dict):
            allowed = ["approve", "reject"]
            approval_description = (
                "安装已核验 Skill 需要人工审批\n\n"
                f"Skill: {skill_approval.get('name') or '-'}\n"
                f"来源: {skill_approval.get('repo_url') or '-'}\n"
                f"目录: {skill_approval.get('sub_path') or '.'}\n"
                f"当前 SHA: {skill_approval.get('current_sha') or '未安装'}\n"
                f"目标 SHA: {skill_approval.get('target_sha') or '-'}\n"
                "影响: 全局安装，仅授权当前 Agent 运行使用"
            )
        else:
            allowed = ["approve"]
            if allow_edit:
                allowed.append("edit")
            if allow_reject:
                allowed.append("reject")
            approval_description = f"{description_prefix}\n\nTool: {request.tool_name}"
        try:
            approval = store.create_request(
                action_key=action_key,
                request_type="tool_call",
                task_id=task_id,
                run_id=run_id,
                node_id=node_id,
                node_title=str(context.metadata.get("node_title") or "Workflow Agent"),
                scope_type=scope_type,
                scope_id=scope_id,
                timeout_seconds=timeout_seconds,
                allowed_decisions=allowed,
                tool_name=request.tool_name,
                arguments=(
                    dict(request.metadata.get("approval_redacted_arguments") or {})
                    if read_only_arguments
                    else request.arguments
                ),
                description=approval_description,
                metadata={
                    "middleware_node_id": spec.node_id,
                    "middleware_priority": spec.priority,
                    "iteration": iteration,
                    "capability": request.metadata.get("capability"),
                    "tool_input_schema": (
                        {}
                        if read_only_arguments
                        else dict(request.metadata.get("tool_input_schema") or {})
                    ),
                    "arguments_digest": (
                        str(request.metadata.get("approval_argument_digest") or "")
                        if read_only_arguments
                        else None
                    ),
                    "arguments_read_only": read_only_arguments,
                    "skill_approval": (
                        dict(skill_approval)
                        if isinstance(skill_approval, dict)
                        else None
                    ),
                    "hub_approval": (
                        dict(hub_approval)
                        if isinstance(hub_approval, dict)
                        else None
                    ),
                    "remote_approval": (
                        dict(remote_approval)
                        if isinstance(remote_approval, dict)
                        else None
                    ),
                },
            )
        except Exception as exc:
            raise RuntimeMiddlewareFatalError(
                f"Unable to persist runtime approval: {str(exc)[:300]}"
            ) from exc
        if isinstance(hub_approval, dict) and hub_event_recorder is not None:
            hub_event_recorder(
                "runtime_approval_shown",
                {
                    "contract_id": hub_approval.get("contract_id"),
                    "candidate_id": hub_approval.get("candidate_id"),
                    "tool_name": request.tool_name,
                },
            )
        raise RuntimeInterrupt(
            approval.approval_id,
            task_id=task_id,
            run_id=run_id,
        )

    return AgentMiddleware(name="human_in_the_loop", wrap_tool_call=wrap_tool_call)


def human_in_the_loop_final_confirmation(spec: RuntimeMiddlewareSpec) -> bool:
    return _bool(spec.config.get("final_confirmation"), False)


def create_final_output_approval(
    spec: RuntimeMiddlewareSpec,
    store: RuntimeApprovalStore,
    context: MiddlewareContext,
    *,
    output_text: str,
    revision_round: int,
) -> RuntimeApprovalRequest:
    task_id = str(context.task_id or "").strip()
    run_id = str(context.metadata.get("run_id") or context.trace_id or "").strip()
    node_id = str(context.metadata.get("node_id") or "").strip()
    if not task_id or not run_id or not node_id:
        raise RuntimeMiddlewareFatalError(
            "Final confirmation requires task_id, run_id, and node_id."
        )
    scope_type, scope_id = _approval_scope(context, task_id)
    timeout_seconds = _bounded_int(
        spec.config.get("timeout_seconds"), 3600, 30, 86_400
    )
    action_key = f"{task_id}:{node_id}:final:{revision_round}"
    return store.create_request(
        action_key=action_key,
        request_type="final_output",
        task_id=task_id,
        run_id=run_id,
        node_id=node_id,
        node_title=str(context.metadata.get("node_title") or "Workflow Agent"),
        scope_type=scope_type,
        scope_id=scope_id,
        timeout_seconds=timeout_seconds,
        allowed_decisions=["approve", "replace", "revise", "reject"],
        description=str(
            spec.config.get("description_prefix")
            or "Agent output requires confirmation"
        )[:2_000],
        content_preview=str(output_text or "")[:8_000],
        metadata={
            "middleware_node_id": spec.node_id,
            "revision_round": revision_round,
            "output_length": len(output_text or ""),
        },
    )


async def _apply_resolution(
    request: ToolCallRequest,
    handler: Any,
    resolved: dict[str, Any],
    *,
    hub_event_recorder: Callable[[str, dict[str, Any]], None] | None = None,
) -> ToolCallResponse:
    decision = str(resolved.get("decision") or "").strip()
    if request.metadata.get("approval_read_only_args") and decision == "edit":
        raise RuntimeMiddlewareFatalError(
            "This tool approval does not allow argument editing."
        )
    if decision == "approve":
        hub_approval = request.metadata.get("hub_approval")
        if isinstance(hub_approval, dict) and hub_event_recorder is not None:
            hub_event_recorder(
                "runtime_approval_approved",
                {
                    "contract_id": hub_approval.get("contract_id"),
                    "candidate_id": hub_approval.get("candidate_id"),
                    "tool_name": request.tool_name,
                },
            )
        return await handler(_without_server_revalidator(request))
    if decision == "edit":
        edited = resolved.get("edited_arguments")
        if not isinstance(edited, dict):
            raise RuntimeMiddlewareFatalError(
                "Approved edit is missing edited_arguments."
            )
        metadata = dict(request.metadata)
        hub_approval = metadata.get("hub_approval")
        remote_approval = metadata.get("remote_approval")
        approval_key = (
            "hub_approval"
            if isinstance(hub_approval, dict)
            else "remote_approval"
            if isinstance(remote_approval, dict)
            else ""
        )
        approval_metadata = metadata.get(approval_key) if approval_key else None
        if isinstance(approval_metadata, dict):
            updated_remote = dict(approval_metadata)
            updated_remote["arguments_digest"] = hashlib.sha256(
                json.dumps(
                    edited,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            metadata[approval_key] = updated_remote
            resolved_approval = metadata.get("resolved_approval")
            if isinstance(resolved_approval, dict):
                updated_resolution = dict(resolved_approval)
                resolution_metadata = dict(
                    updated_resolution.get("metadata")
                    if isinstance(updated_resolution.get("metadata"), dict)
                    else {}
                )
                resolution_metadata[approval_key] = dict(updated_remote)
                updated_resolution["metadata"] = resolution_metadata
                metadata["resolved_approval"] = updated_resolution
            if approval_key == "hub_approval" and hub_event_recorder is not None:
                hub_event_recorder(
                    "runtime_approval_approved",
                    {
                        "contract_id": updated_remote.get("contract_id"),
                        "candidate_id": updated_remote.get("candidate_id"),
                        "tool_name": request.tool_name,
                    },
                )
        edited_request = request.with_updates(
            arguments=dict(edited), metadata=metadata
        )
        revalidate = metadata.get(TOOL_REVALIDATE_METADATA_KEY)
        if callable(revalidate):
            await revalidate(edited_request)
        return await handler(_without_server_revalidator(edited_request))
    if decision == "reject":
        hub_approval = request.metadata.get("hub_approval")
        if isinstance(hub_approval, dict) and hub_event_recorder is not None:
            hub_event_recorder(
                "runtime_approval_rejected",
                {
                    "contract_id": hub_approval.get("contract_id"),
                    "candidate_id": hub_approval.get("candidate_id"),
                    "tool_name": request.tool_name,
                },
            )
        message = str(
            resolved.get("message")
            or f"User rejected the tool call {request.tool_name}."
        )
        return ToolCallResponse(
            output=message,
            metadata={
                "is_error": True,
                "approval_rejected": True,
                "approval_id": resolved.get("approval_id"),
                "rejected_candidate_id": request.arguments.get("candidate_id"),
            },
        )
    raise RuntimeMiddlewareFatalError(f"Unsupported approval decision: {decision}.")


def _without_server_revalidator(request: ToolCallRequest) -> ToolCallRequest:
    if TOOL_REVALIDATE_METADATA_KEY not in request.metadata:
        return request
    metadata = dict(request.metadata)
    metadata.pop(TOOL_REVALIDATE_METADATA_KEY, None)
    return request.with_updates(metadata=metadata)


def _tool_rules(value: Any) -> dict[str, bool]:
    if isinstance(value, dict):
        return {str(key).strip(): bool(inner) for key, inner in value.items() if str(key).strip()}
    if isinstance(value, list):
        return {str(item).strip(): True for item in value if str(item).strip()}
    return {
        item.strip(): True
        for item in re.split(r"[,\n]+", str(value or ""))
        if item.strip()
    }


def _requires_review(tool_name: str, rules: dict[str, bool]) -> bool:
    return bool(rules.get(tool_name, rules.get("*", False)))


def _approval_scope(context: MiddlewareContext, task_id: str) -> tuple[str, str]:
    metadata = context.metadata
    goal_id = str(metadata.get("goal_id") or "").strip()
    if goal_id:
        return "goal", goal_id
    handoff_id = str(metadata.get("handoff_id") or "").strip()
    if handoff_id:
        return "handoff", handoff_id
    conversation_id = str(metadata.get("conversation_id") or "").strip()
    xpert_id = str(metadata.get("xpert_id") or "").strip()
    if conversation_id:
        return "conversation", f"{xpert_id}:{conversation_id}"
    return "workflow", task_id


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
