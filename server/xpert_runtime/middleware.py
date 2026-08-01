from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from .models import (
    MiddlewareContext,
    ModelCallRequest,
    ModelCallResponse,
    ToolCallRequest,
    ToolCallResponse,
)


T = TypeVar("T")
MaybeAwaitable = T | Awaitable[T]
AgentState = dict[str, Any]
AgentStateHook = Callable[[AgentState, MiddlewareContext], MaybeAwaitable[AgentState | None]]
ModelHook = Callable[
    [ModelCallRequest, MiddlewareContext],
    MaybeAwaitable[ModelCallRequest | dict[str, Any] | None],
]
ModelResponseHook = Callable[
    [ModelCallResponse, MiddlewareContext],
    MaybeAwaitable[ModelCallResponse | dict[str, Any] | None],
]
ModelCallHandler = Callable[[ModelCallRequest], Awaitable[ModelCallResponse]]
ModelWrapper = Callable[
    [ModelCallRequest, ModelCallHandler, MiddlewareContext],
    MaybeAwaitable[ModelCallResponse],
]
ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolCallResponse]]
ToolWrapper = Callable[
    [ToolCallRequest, ToolCallHandler, MiddlewareContext],
    MaybeAwaitable[ToolCallResponse],
]


@dataclass(slots=True)
class AgentMiddleware:
    """Xpert-style middleware hook bundle."""

    name: str
    before_agent: AgentStateHook | None = None
    before_model: ModelHook | None = None
    after_model: ModelResponseHook | None = None
    after_agent: AgentStateHook | None = None
    wrap_model_call: ModelWrapper | None = None
    wrap_tool_call: ToolWrapper | None = None
    enabled: bool = True


class MiddlewarePipeline:
    """Ordered middleware executor for model, tool, and agent work."""

    def __init__(self, middlewares: list[AgentMiddleware] | None = None) -> None:
        self._middlewares = middlewares or []

    def append(self, middleware: AgentMiddleware) -> None:
        self._middlewares.append(middleware)

    def list(self) -> list[AgentMiddleware]:
        return list(self._middlewares)

    async def before_agent(
        self,
        state: AgentState,
        context: MiddlewareContext,
    ) -> AgentState:
        current = dict(state)
        for middleware in self._enabled():
            if middleware.before_agent is None:
                continue
            result = await _maybe_await(middleware.before_agent(current, context))
            if result:
                current.update(result)
        return current

    async def after_agent(
        self,
        state: AgentState,
        context: MiddlewareContext,
    ) -> AgentState:
        current = dict(state)
        for middleware in self._enabled():
            if middleware.after_agent is None:
                continue
            result = await _maybe_await(middleware.after_agent(current, context))
            if result:
                current.update(result)
        return current

    async def before_model(
        self,
        request: ModelCallRequest,
        context: MiddlewareContext,
    ) -> ModelCallRequest:
        current = request
        for middleware in self._enabled():
            if middleware.before_model is None:
                continue
            result = await _maybe_await(middleware.before_model(current, context))
            current = _merge_model_request(current, result)
        return current

    async def after_model(
        self,
        response: ModelCallResponse,
        context: MiddlewareContext,
    ) -> ModelCallResponse:
        current = response
        for middleware in self._enabled():
            if middleware.after_model is None:
                continue
            result = await _maybe_await(middleware.after_model(current, context))
            current = _merge_model_response(current, result)
        return current

    async def run_model_call(
        self,
        request: ModelCallRequest,
        handler: ModelCallHandler,
        context: MiddlewareContext,
    ) -> ModelCallResponse:
        prepared = await self.before_model(request, context)
        response = await self.wrap_model_call(prepared, handler, context)
        return await self.after_model(response, context)

    async def wrap_model_call(
        self,
        request: ModelCallRequest,
        handler: ModelCallHandler,
        context: MiddlewareContext,
    ) -> ModelCallResponse:
        wrapped = handler
        for middleware in reversed(self._enabled()):
            if middleware.wrap_model_call is None:
                continue
            next_handler = wrapped

            async def call(
                req: ModelCallRequest,
                *,
                current_middleware: AgentMiddleware = middleware,
                current_next: ModelCallHandler = next_handler,
            ) -> ModelCallResponse:
                assert current_middleware.wrap_model_call is not None
                return await _maybe_await(
                    current_middleware.wrap_model_call(req, current_next, context)
                )

            wrapped = call
        return await wrapped(request)

    async def run_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolCallHandler,
        context: MiddlewareContext,
    ) -> ToolCallResponse:
        wrapped = handler
        for middleware in reversed(self._enabled()):
            if middleware.wrap_tool_call is None:
                continue
            next_handler = wrapped

            async def call(
                req: ToolCallRequest,
                *,
                current_middleware: AgentMiddleware = middleware,
                current_next: ToolCallHandler = next_handler,
            ) -> ToolCallResponse:
                assert current_middleware.wrap_tool_call is not None
                return await _maybe_await(
                    current_middleware.wrap_tool_call(req, current_next, context)
                )

            wrapped = call
        return await wrapped(request)

    def _enabled(self) -> list[AgentMiddleware]:
        return [middleware for middleware in self._middlewares if middleware.enabled]


async def _maybe_await(value: MaybeAwaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


def _merge_model_request(
    request: ModelCallRequest,
    result: ModelCallRequest | dict[str, Any] | None,
) -> ModelCallRequest:
    if result is None:
        return request
    if isinstance(result, ModelCallRequest):
        return result
    updates = dict(result)
    return request.with_updates(**updates)


def _merge_model_response(
    response: ModelCallResponse,
    result: ModelCallResponse | dict[str, Any] | None,
) -> ModelCallResponse:
    if result is None:
        return response
    if isinstance(result, ModelCallResponse):
        return result
    updates = dict(result)
    return response.with_updates(**updates)
