from __future__ import annotations

import pytest

from server.xpert_runtime import (
    AgentMiddleware,
    CapabilityRegistry,
    MiddlewareContext,
    MiddlewarePipeline,
    ModelCallRequest,
    ModelCallResponse,
    RuntimeEventStore,
    ToolCallRequest,
    ToolCallResponse,
)


@pytest.mark.asyncio
async def test_middleware_can_inject_system_prompt() -> None:
    async def inject_prompt(
        request: ModelCallRequest,
        context: MiddlewareContext,
    ) -> dict:
        return {
            "messages": [
                {"role": "system", "content": context.metadata["system_prompt"]},
                *request.messages,
            ]
        }

    async def handler(request: ModelCallRequest) -> ModelCallResponse:
        return ModelCallResponse(text=request.messages[0]["content"])

    pipeline = MiddlewarePipeline(
        [AgentMiddleware(name="system-prompt", before_model=inject_prompt)]
    )
    context = MiddlewareContext(metadata={"system_prompt": "你是模镜运行时中间件。"})
    request = ModelCallRequest(
        model_id="mock",
        messages=[{"role": "user", "content": "ping"}],
    )

    response = await pipeline.run_model_call(request, handler, context)

    assert response.text == "你是模镜运行时中间件。"


@pytest.mark.asyncio
async def test_wrap_tool_call_records_runtime_events() -> None:
    store = RuntimeEventStore()
    task = await store.create_task("tool_call", {"tool": "echo"})

    async def audit_tool(
        request: ToolCallRequest,
        call_next,
        context: MiddlewareContext,
    ) -> ToolCallResponse:
        await context.store.record_event(
            "tool.call.started",
            task_id=context.task_id,
            payload={"tool_name": request.tool_name},
        )
        response = await call_next(request)
        await context.store.record_event(
            "tool.call.finished",
            task_id=context.task_id,
            payload={"output": response.output},
        )
        return response.with_updates(output=f"[audited] {response.output}")

    async def handler(request: ToolCallRequest) -> ToolCallResponse:
        return ToolCallResponse(output=f"hello {request.arguments['name']}")

    pipeline = MiddlewarePipeline([AgentMiddleware(name="audit", wrap_tool_call=audit_tool)])
    context = MiddlewareContext(task_id=task.id, store=store)

    response = await pipeline.run_tool_call(
        ToolCallRequest(tool_name="echo", arguments={"name": "ModelMirror"}),
        handler,
        context,
    )
    events = await store.list_events(task.id)

    assert response.output == "[audited] hello ModelMirror"
    assert [event.type for event in events][-2:] == [
        "tool.call.started",
        "tool.call.finished",
    ]


def test_capability_registry_registers_and_requires_capabilities() -> None:
    registry = CapabilityRegistry()
    registry.register("mcp_tools", implementation={"provider": "mock"})

    assert registry.has("mcp_tools") is True
    assert registry.require("mcp_tools").implementation["provider"] == "mock"
    assert registry.names() == ["mcp_tools"]

    with pytest.raises(KeyError):
        registry.require("workspace_files")


@pytest.mark.asyncio
async def test_runtime_store_task_lifecycle_and_dead_letter() -> None:
    store = RuntimeEventStore()
    task = await store.create_task("handoff", {"message": "route to expert"})

    await store.update_task(task.id, status="running", attempts=1)
    await store.mark_dead(task.id, "max attempts exceeded")

    dead_letters = await store.list_dead_letters()
    events = await store.list_events(task.id)

    assert dead_letters[0].id == task.id
    assert dead_letters[0].status == "dead"
    assert events[-1].type == "task.dead"


@pytest.mark.asyncio
async def test_runtime_store_cleans_up_expired_tasks() -> None:
    store = RuntimeEventStore()
    task = await store.create_task("assistant_task", ttl_seconds=0.01)
    assert task.status == "pending"

    expired = await store.cleanup_expired(now=task.created_at + 1)
    events = await store.list_events(task.id)

    assert [item.id for item in expired] == [task.id]
    assert task.status == "dead"
    assert events[-1].type == "task.expired"
