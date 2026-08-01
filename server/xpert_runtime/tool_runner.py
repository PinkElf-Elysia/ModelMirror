from __future__ import annotations

import inspect
import logging
from typing import Any

from .capabilities import CapabilityRegistry
from .execution_budget import execution_operation
from .middleware import MiddlewarePipeline
from .models import MiddlewareContext, ToolCallRequest, ToolCallResponse
from .interrupts import RuntimeInterrupt, RuntimeMiddlewareFatalError
from .tool_policy import InMemoryToolAuditStore, ToolPermissionPolicy
from .toolset import RuntimeToolCall, RuntimeToolError, RuntimeToolResult

logger = logging.getLogger(__name__)


async def run_tool_with_runtime(
    tool_call: RuntimeToolCall,
    capability_registry: CapabilityRegistry,
    pipeline: MiddlewarePipeline,
    context: MiddlewareContext,
    *,
    capability_name: str = "mcp_tools",
    policy: ToolPermissionPolicy | None = None,
    audit_store: InMemoryToolAuditStore | None = None,
) -> RuntimeToolResult:
    """Run a runtime tool call through capability lookup and middleware hooks."""

    if not capability_registry.has(capability_name):
        raise RuntimeToolError(
            tool_call.tool_name,
            f"Runtime capability is not registered: {capability_name}",
            code="capability_not_found",
        )

    if policy is not None:
        try:
            await policy.check(tool_call.tool_name)
        except RuntimeToolError as exc:
            if exc.code == "tool_denied":
                await _safe_record_denied(
                    audit_store,
                    tool_call.tool_name,
                    metadata={
                        "capability": capability_name,
                        **dict(tool_call.metadata or {}),
                    },
                )
            raise
        except Exception as exc:
            logger.warning("Tool policy check failed, allowing by default: %s", exc)

    provider = capability_registry.require(capability_name).implementation
    try:
        static_prepare_call = inspect.getattr_static(provider, "prepare_call")
    except AttributeError:
        static_prepare_call = None
    if static_prepare_call is not None:
        prepare_call = getattr(provider, "prepare_call")
        result = prepare_call(tool_call)
        if inspect.isawaitable(result):
            await result
    request = _to_tool_call_request(tool_call)
    provider_called = False
    provider_response: ToolCallResponse | None = None
    audit_record_id: str | None = None

    async def handler(req: ToolCallRequest) -> ToolCallResponse:
        nonlocal provider_called, provider_response, audit_record_id
        try:
            static_prepare_dispatch = inspect.getattr_static(provider, "prepare_dispatch")
        except AttributeError:
            static_prepare_dispatch = None
        if static_prepare_dispatch is not None:
            prepare_dispatch = getattr(provider, "prepare_dispatch")
            dispatch_result = prepare_dispatch(
                RuntimeToolCall(
                    tool_name=req.tool_name,
                    arguments=dict(req.arguments or {}),
                    metadata=dict(req.metadata or {}),
                )
            )
            if inspect.isawaitable(dispatch_result):
                await dispatch_result
        provider_called = True
        if audit_record_id is None:
            audit_record_id = await _safe_record_started(
                audit_store,
                req.tool_name,
                metadata={
                    "capability": capability_name,
                    **dict(req.metadata or {}),
                },
            )
        try:
            async with execution_operation("tool_call"):
                inner_result = await provider.call_tool(
                    RuntimeToolCall(
                        tool_name=req.tool_name,
                        arguments=dict(req.arguments or {}),
                        metadata=dict(req.metadata or {}),
                    )
                )
        except Exception as exc:
            await _safe_record_failed(audit_store, audit_record_id, error=str(exc))
            raise

        await _safe_record_finished(
            audit_store,
            audit_record_id,
            output_length=len(inner_result.output or ""),
            content_types=_content_types(inner_result),
        )
        provider_response = ToolCallResponse(
            output=inner_result.output,
            raw=inner_result.content,
            metadata={
                **dict(inner_result.metadata or {}),
                "is_error": inner_result.is_error,
            },
        )
        return provider_response

    try:
        response = await pipeline.run_tool_call(request, handler, context)
    except (RuntimeInterrupt, RuntimeMiddlewareFatalError):
        # Durable approval interrupts and persistence failures must never fall
        # through to the legacy middleware fail-open provider path.
        raise
    except RuntimeToolError:
        raise
    except Exception as exc:
        logger.warning("Runtime tool middleware failed, falling back: %s", exc)
        if provider_called and provider_response is not None:
            response = provider_response
        elif provider_called:
            raise
        else:
            response = await handler(request)

    return _to_runtime_result(response)


def _to_tool_call_request(tool_call: RuntimeToolCall) -> ToolCallRequest:
    metadata = dict(tool_call.metadata or {})
    return ToolCallRequest(
        tool_name=tool_call.tool_name,
        arguments=dict(tool_call.arguments or {}),
        session_id=_optional_str(metadata.get("session_id")),
        metadata=metadata,
    )


def _to_runtime_result(response: ToolCallResponse) -> RuntimeToolResult:
    content = response.raw if isinstance(response.raw, list) else []
    metadata = dict(response.metadata or {})
    return RuntimeToolResult(
        output=response.output,
        content=[_ensure_dict(item) for item in content],
        metadata=metadata,
        is_error=bool(metadata.get("is_error", False)),
    )


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return dict(value.model_dump())
        except Exception:
            return {"raw": str(value)}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"raw": str(value)}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


async def _safe_record_started(
    audit_store: InMemoryToolAuditStore | None,
    tool_name: str,
    *,
    metadata: dict[str, Any],
) -> str | None:
    if audit_store is None:
        return None
    try:
        return await audit_store.record_started(tool_name, metadata=metadata)
    except Exception as exc:
        logger.warning("Tool audit record_started failed: %s", exc)
        return None


async def _safe_record_finished(
    audit_store: InMemoryToolAuditStore | None,
    record_id: str | None,
    *,
    output_length: int,
    content_types: list[str],
) -> None:
    if audit_store is None or record_id is None:
        return
    try:
        await audit_store.record_finished(
            record_id,
            output_length=output_length,
            content_types=content_types,
        )
    except Exception as exc:
        logger.warning("Tool audit record_finished failed: %s", exc)


async def _safe_record_failed(
    audit_store: InMemoryToolAuditStore | None,
    record_id: str | None,
    *,
    error: str,
) -> None:
    if audit_store is None or record_id is None:
        return
    try:
        await audit_store.record_failed(record_id, error=error)
    except Exception as exc:
        logger.warning("Tool audit record_failed failed: %s", exc)


async def _safe_record_denied(
    audit_store: InMemoryToolAuditStore | None,
    tool_name: str,
    *,
    metadata: dict[str, Any],
) -> None:
    if audit_store is None:
        return
    try:
        await audit_store.record_denied(tool_name, metadata=metadata)
    except Exception as exc:
        logger.warning("Tool audit record_denied failed: %s", exc)


def _content_types(result: RuntimeToolResult) -> list[str]:
    content_types = result.metadata.get("content_types", [])
    if isinstance(content_types, list):
        return [str(content_type) for content_type in content_types]
    return []
