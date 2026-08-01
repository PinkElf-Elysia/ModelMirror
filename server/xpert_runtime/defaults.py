from __future__ import annotations

import logging
import os
from typing import Any

from .events import RuntimeEventStore
from .middleware import AgentMiddleware, MiddlewarePipeline
from .models import (
    MiddlewareContext,
    ModelCallRequest,
    ModelCallResponse,
    ToolCallRequest,
    ToolCallResponse,
)

logger = logging.getLogger(__name__)


async def _record_event(
    context: MiddlewareContext,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    severity: str = "info",
) -> None:
    store = context.store
    if store is None or not hasattr(store, "record_event"):
        return
    try:
        await store.record_event(
            event_type,
            task_id=context.task_id,
            trace_id=context.trace_id,
            payload=payload or {},
            severity=severity,
        )
    except Exception as exc:
        logger.warning("Xpert runtime event recording failed: %s", exc)


async def _inject_system_prompt(
    request: ModelCallRequest,
    context: MiddlewareContext,
) -> dict[str, Any] | None:
    try:
        prompt = str(
            context.metadata.get("system_prompt")
            or os.environ.get("XPERT_DEFAULT_SYSTEM_PROMPT", "")
        ).strip()
        if not prompt:
            return None

        messages = [dict(message) for message in request.messages]
        first = messages[0] if messages else None
        has_first_system = isinstance(first, dict) and first.get("role") == "system"

        if has_first_system:
            if context.metadata.get("override_system_prompt") is True:
                messages[0] = {**messages[0], "content": prompt}
                return {"messages": messages}
            return None

        return {"messages": [{"role": "system", "content": prompt}, *messages]}
    except Exception as exc:
        logger.warning("Xpert system prompt middleware failed: %s", exc)
        return None


async def _record_chat_started(
    state: dict[str, Any],
    context: MiddlewareContext,
) -> None:
    try:
        messages = state.get("messages")
        message_count = len(messages) if isinstance(messages, list) else context.metadata.get("message_count", 0)
        await _record_event(
            context,
            "chat.started",
            {
                "model_id": state.get("model_id") or context.metadata.get("model_id"),
                "message_count": message_count,
            },
        )
    except Exception as exc:
        logger.warning("Xpert chat.started middleware failed: %s", exc)


async def _record_model_started(
    request: ModelCallRequest,
    context: MiddlewareContext,
) -> None:
    try:
        await _record_event(
            context,
            "model.call.started",
            {"model_id": request.model_id},
        )
    except Exception as exc:
        logger.warning("Xpert model.call.started middleware failed: %s", exc)


async def _record_model_finished(
    response: ModelCallResponse,
    context: MiddlewareContext,
) -> None:
    try:
        await _record_event(
            context,
            "model.call.finished",
            {
                "text_length": len(response.text or ""),
                "model_id": response.metadata.get("model_id")
                or context.metadata.get("model_id"),
            },
        )
    except Exception as exc:
        logger.warning("Xpert model.call.finished middleware failed: %s", exc)


async def _record_chat_finished(
    state: dict[str, Any],
    context: MiddlewareContext,
) -> None:
    try:
        await _record_event(
            context,
            "chat.finished",
            {"status": state.get("status", "completed")},
            severity="error" if state.get("status") == "error" else "info",
        )
    except Exception as exc:
        logger.warning("Xpert chat.finished middleware failed: %s", exc)


async def _record_tool_call(
    request: ToolCallRequest,
    handler: Any,
    context: MiddlewareContext,
) -> ToolCallResponse:
    try:
        await _record_event(
            context,
            "tool.call.started",
            {
                "tool_name": request.tool_name,
                "arguments_count": len(request.arguments or {}),
            },
        )
        response = await handler(request)
        await _record_event(
            context,
            "tool.call.finished",
            {
                "tool_name": request.tool_name,
                "output_length": len(response.output or ""),
                "content_types": response.metadata.get("content_types", []),
                "is_error": bool(response.metadata.get("is_error")),
            },
        )
        return response
    except Exception as exc:
        await _record_event(
            context,
            "tool.call.failed",
            {
                "tool_name": request.tool_name,
                "error": str(exc)[:200],
            },
            severity="error",
        )
        raise


system_prompt_injector = AgentMiddleware(
    name="system_prompt_injector",
    before_model=_inject_system_prompt,
)

event_recorder = AgentMiddleware(
    name="event_recorder",
    before_agent=_record_chat_started,
    before_model=_record_model_started,
    after_model=_record_model_finished,
    after_agent=_record_chat_finished,
    wrap_tool_call=_record_tool_call,
)


def create_default_runtime(
    store: RuntimeEventStore | None = None,
    middlewares: list[AgentMiddleware] | None = None,
) -> tuple[MiddlewarePipeline, MiddlewareContext]:
    """Create the default Xpert-aligned runtime pipeline and context."""

    runtime_store = store or RuntimeEventStore()
    pipeline = MiddlewarePipeline(middlewares or [system_prompt_injector, event_recorder])
    context = MiddlewareContext(store=runtime_store)
    return pipeline, context
