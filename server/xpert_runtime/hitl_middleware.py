from __future__ import annotations

import re
from typing import Any

from .approval_store import RuntimeApprovalRequest, RuntimeApprovalStore
from .core_middlewares import RuntimeMiddlewareSpec
from .interrupts import RuntimeInterrupt, RuntimeMiddlewareFatalError
from .middleware import AgentMiddleware
from .models import MiddlewareContext, ToolCallRequest, ToolCallResponse


def build_human_in_the_loop_middleware(
    spec: RuntimeMiddlewareSpec,
    store: RuntimeApprovalStore,
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
            return await handler(request)

        resolved = request.metadata.get("resolved_approval")
        if isinstance(resolved, dict):
            return await _apply_resolution(request, handler, resolved)

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
        allowed = ["approve"]
        if allow_edit:
            allowed.append("edit")
        if allow_reject:
            allowed.append("reject")
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
                arguments=request.arguments,
                description=(
                    f"{description_prefix}\n\nTool: {request.tool_name}"
                ),
                metadata={
                    "middleware_node_id": spec.node_id,
                    "middleware_priority": spec.priority,
                    "iteration": iteration,
                    "capability": request.metadata.get("capability"),
                    "tool_input_schema": dict(
                        request.metadata.get("tool_input_schema") or {}
                    ),
                },
            )
        except Exception as exc:
            raise RuntimeMiddlewareFatalError(
                f"Unable to persist runtime approval: {str(exc)[:300]}"
            ) from exc
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
) -> ToolCallResponse:
    decision = str(resolved.get("decision") or "").strip()
    if decision == "approve":
        return await handler(request)
    if decision == "edit":
        edited = resolved.get("edited_arguments")
        if not isinstance(edited, dict):
            raise RuntimeMiddlewareFatalError(
                "Approved edit is missing edited_arguments."
            )
        return await handler(request.with_updates(arguments=dict(edited)))
    if decision == "reject":
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
            },
        )
    raise RuntimeMiddlewareFatalError(f"Unsupported approval decision: {decision}.")


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
