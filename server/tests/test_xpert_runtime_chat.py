from __future__ import annotations

import pytest
import httpx
import pytest_asyncio

import server.main as main_module
from server.main import (
    ChatMessage,
    app,
    parse_upstream_error,
    sse_delta_text,
    stream_chat_text,
)
from server.xpert_runtime import (
    MiddlewareContext,
    ModelCallRequest,
    ModelCallResponse,
    RuntimeTool,
    RuntimeToolResult,
    RuntimeEventStore,
    create_default_runtime,
    event_recorder,
)


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def reset_chat_rate_limit_window():
    main_module.request_windows.clear()
    yield
    main_module.request_windows.clear()


@pytest.mark.asyncio
async def test_middleware_injects_system_prompt_in_chat() -> None:
    pipeline, context = create_default_runtime()
    context.metadata["system_prompt"] = "你是助手"

    request = ModelCallRequest(
        model_id="mock-model",
        messages=[{"role": "user", "content": "你好"}],
    )

    prepared = await pipeline.before_model(request, context)

    assert prepared.messages[0] == {"role": "system", "content": "你是助手"}
    assert prepared.messages[1] == {"role": "user", "content": "你好"}


@pytest.mark.asyncio
async def test_chat_events_recorded_in_store() -> None:
    store = RuntimeEventStore()
    pipeline, context = create_default_runtime(store=store, middlewares=[event_recorder])
    context.task_id = "chat-task-1"
    context.metadata = {"model_id": "mock-model", "message_count": 1}

    await pipeline.before_agent(
        {"model_id": "mock-model", "messages": [{"role": "user", "content": "hi"}]},
        context,
    )
    await pipeline.before_model(
        ModelCallRequest(
            model_id="mock-model",
            messages=[{"role": "user", "content": "hi"}],
        ),
        context,
    )
    await pipeline.after_model(
        ModelCallResponse(text="hello", metadata={"model_id": "mock-model"}),
        context,
    )
    await pipeline.after_agent({"status": "completed"}, context)

    events = await store.list_events("chat-task-1")

    assert [event.type for event in events] == [
        "chat.started",
        "model.call.started",
        "model.call.finished",
        "chat.finished",
    ]


@pytest.mark.asyncio
async def test_sse_text_stream_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 200

        async def aiter_text(self):
            yield 'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n'
            yield 'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
            yield "data: [DONE]\n\n"

        async def aclose(self) -> None:
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def build_request(self, method, url, headers, json):
            return {"method": method, "url": url, "headers": headers, "json": json}

        async def send(self, request, stream):
            assert stream is True
            return FakeResponse()

    import server.main as main_module

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)

    chunks = [
        chunk
        async for chunk in stream_chat_text(
            "mock-model",
            [ChatMessage(role="user", content="hi")],
        )
    ]

    assert "".join(chunks) == "hello world"


@pytest.mark.asyncio
async def test_image_markdown_output_preserved() -> None:
    pipeline, context = create_default_runtime(middlewares=[event_recorder])
    response = ModelCallResponse(
        text="结果：\n![图片](https://example.com/img.png)\n",
        metadata={"model_id": "image-model"},
    )

    processed = await pipeline.after_model(response, context)

    assert processed.text == response.text
    assert "![图片](https://example.com/img.png)" in processed.text


def test_sse_image_url_is_converted_to_markdown() -> None:
    event = (
        'data: {"choices":[{"delta":{"content":[{"type":"image_url",'
        '"image_url":{"url":"https://example.com/cat.png"}}]}}]}\n\n'
    )

    assert sse_delta_text(event) == ["\n![图片](https://example.com/cat.png)\n"]


def test_user_not_found_error_is_mapped_to_actionable_message() -> None:
    message, data = parse_upstream_error(
        401,
        b'{"error":{"message":"User not found."}}',
    )

    assert data is not None
    assert "User not found" not in message
    assert "newAPI" in message
    assert "OPENROUTER_API_KEY" in message


def test_newapi_user_error_can_fallback_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server.main as main_module

    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "sk-test")

    assert (
        main_module.should_fallback_gateway_to_openrouter(
            401,
            "本地 newAPI 未找到对应用户或令牌无效。",
            {"error": {"message": "User not found."}},
            "http://new-api:3000/v1/chat/completions",
        )
        is True
    )
    assert (
        main_module.should_fallback_gateway_to_openrouter(
            401,
            "本地 newAPI 未找到对应用户或令牌无效。",
            {"error": {"message": "User not found."}},
            main_module.CHAT_COMPLETIONS_URL,
        )
        is False
    )


@pytest.mark.asyncio
async def test_chat_tool_mode_answer_streams_final_text(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_chat_tool_provider()

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return '{"answer":"final"}'

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": "mock-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    assert _read_sse_text(response.text) == "final"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_chat_tool_mode_calls_runtime_toolset(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_chat_tool_provider()
    responses = iter(
        [
            '{"tool":"fetch","arguments":{"query":"hello"}}',
            '{"answer":"final from tool"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": "mock-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
                "max_tool_iterations": 3,
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    text = _read_sse_text(response.text)
    assert "Runtime 工具调用" in text
    assert "final from tool" in text
    assert len(provider.calls) == 1
    assert provider.calls[0].tool_name == "fetch"
    assert provider.calls[0].arguments == {"query": "hello"}
    run_id = response.headers.get("x-modelmirror-runtime-run-id")
    task_id = response.headers.get("x-modelmirror-runtime-task-id")
    assert run_id
    assert task_id

    run_response = await client.get(f"/api/runtime/runs/{run_id}")
    assert run_response.status_code == 200, run_response.text
    run_payload = run_response.json()
    assert run_payload["run_type"] == "chat"
    assert run_payload["status"] == "completed"

    checkpoints_response = await client.get(
        f"/api/runtime/runs/{run_id}/checkpoints",
    )
    assert checkpoints_response.status_code == 200, checkpoints_response.text
    checkpoint_types = [item["event_type"] for item in checkpoints_response.json()]
    assert "chat.started" in checkpoint_types
    assert "chat.model_decision" in checkpoint_types
    assert "chat.tool_call" in checkpoint_types
    assert "chat.answer" in checkpoint_types

    events_response = await client.get(f"/api/chat/runtime-events/{task_id}")
    assert events_response.status_code == 200, events_response.text
    events_payload = events_response.json()
    event_types = [item["type"] for item in events_payload["events"]]
    assert "chat.started" in event_types
    assert "tool.call.started" in event_types
    assert "tool.call.finished" in event_types
    assert events_payload["tool_audit_count"] == 1
    assert events_payload["tool_audit_records"][0]["tool_name"] == "fetch"


@pytest.mark.asyncio
async def test_chat_tool_mode_rejects_tool_outside_allowlist(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_chat_tool_provider()

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return '{"tool":"search","arguments":{"query":"blocked"}}'

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": "mock-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    assert "不在本次聊天允许列表" in _read_sse_error(response.text)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_chat_tool_mode_invalid_payload_returns_validation_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "model_id": "mock-model",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_mode": "bad-mode",
            "max_tool_iterations": 99,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_runtime_events_missing_task_returns_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/chat/runtime-events/missing-chat-task")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_chat_tool_mode_failure_records_failed_run(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, restore_provider = _install_fake_chat_tool_provider()

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return '{"tool":"search","arguments":{"query":"blocked"}}'

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": "mock-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        restore_provider()

    assert response.status_code == 200, response.text
    assert provider.calls == []
    run_id = response.headers.get("x-modelmirror-runtime-run-id")
    assert run_id
    run_response = await client.get(f"/api/runtime/runs/{run_id}")
    assert run_response.status_code == 200, run_response.text
    assert run_response.json()["status"] == "failed"
    checkpoints_response = await client.get(
        f"/api/runtime/runs/{run_id}/checkpoints",
    )
    assert checkpoints_response.status_code == 200, checkpoints_response.text
    checkpoint_types = [item["event_type"] for item in checkpoints_response.json()]
    assert "chat.failed" in checkpoint_types
    assert "run.failed" in checkpoint_types


@pytest.mark.asyncio
async def test_chat_tool_mode_none_does_not_create_chat_run(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        run.run_id
        for run in await main_module.run_registry.list_runs(run_type="chat", limit=200)
    }

    class FakeUpstreamClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_request(self, *args, **kwargs):
            return httpx.Request("POST", "http://mock")

        async def send(self, request, *, stream: bool = False):
            return httpx.Response(
                200,
                content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                request=httpx.Request("POST", "http://mock"),
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://mock", "key"))
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeUpstreamClient)

    response = await client.post(
        "/api/chat",
        json={
            "model_id": "mock-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200, response.text
    assert _read_sse_text(response.text) == "ok"
    assert response.headers.get("x-modelmirror-runtime-run-id") is None
    after = {
        run.run_id
        for run in await main_module.run_registry.list_runs(run_type="chat", limit=200)
    }
    assert after == before


class FakeChatToolProvider:
    def __init__(self) -> None:
        self.calls = []

    async def list_tools(self):
        return [
            RuntimeTool(
                name="fetch",
                description="Fetch content",
                input_schema={"type": "object"},
                session_id="session-1",
                server_id="server-1",
            )
        ]

    async def find_tool(self, tool_name: str):
        for tool in await self.list_tools():
            if tool.name == tool_name:
                return tool
        return None

    async def call_tool(self, call):
        self.calls.append(call)
        return RuntimeToolResult(
            output="tool result",
            content=[{"type": "text", "text": "tool result"}],
            metadata={"content_types": ["text"]},
            is_error=False,
        )


def _install_fake_chat_tool_provider():
    provider = FakeChatToolProvider()
    original = main_module.runtime_capabilities.require("mcp_tools").implementation
    main_module.runtime_capabilities.register("mcp_tools", provider)

    def restore_provider() -> None:
        main_module.runtime_capabilities.register("mcp_tools", original)

    return provider, restore_provider


def _read_sse_text(text: str) -> str:
    chunks: list[str] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        data = __import__("json").loads(payload)
        if "error" in data:
            continue
        choices = data.get("choices") or []
        if choices:
            chunks.append(str(choices[0].get("delta", {}).get("content") or ""))
    return "".join(chunks)


def _read_sse_error(text: str) -> str:
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        data = __import__("json").loads(payload)
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
    return ""
