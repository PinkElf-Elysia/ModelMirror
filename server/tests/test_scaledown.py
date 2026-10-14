import json

import httpx
import pytest

from server import main
from server.multimodal.scaledown import ScaleDownRequest
from server.multimodal.scaledown import scaledown_result
from server.model_router.model_ids import is_realtime_chat_model_id


@pytest.mark.parametrize("model", ["scaledown/summarize", "scaledown/extract"])
@pytest.mark.asyncio
async def test_text_contract(model, monkeypatch):
    payload = {"model": model, "text": "Public example", "instructions": "Use English"}
    if model.endswith("extract"):
        payload["entities"] = {"name": "Person name"}

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["messages"] == [{"role": "system", "content": "Use English"}, {"role": "user", "content": "Public example"}]
        assert "temperature" not in body
        assert "tools" not in body
        if model.endswith("extract"):
            assert body["provider"] == {"options": {"scaledown": {"entities": {"name": "Person name"}}}}
        else:
            assert "provider" not in body
        content = json.dumps({"summary": "Result", "finish_reason": "stop"}) if model.endswith("summarize") else '{"entities": []}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "secret": "not relayed"})

    monkeypatch.setattr(main, "OPENROUTER_API_KEY", "mock")
    monkeypatch.setattr(main, "rate_limit_or_raise", lambda _: None)
    monkeypatch.setattr(main, "llm_client_kwargs", lambda: {"transport": httpx.MockTransport(handler)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/api/scaledown", json=payload)
    assert response.status_code == 200
    assert response.json() == {"model": model, "content": "Result" if model.endswith("summarize") else '{"entities": []}'}
    assert not is_realtime_chat_model_id(model)


@pytest.mark.parametrize("override", [
    {"model": "openai/gpt-6.1-sol"}, {"text": " "}, {"text": "x" * 100001},
    {"max_tokens": 1001}, {"max_tokens": 4001}, {"max_tokens": True}, {"stream": True}, {"images": []},
    {"provider": {}}, {"entities": {}},
    {"model": "scaledown/extract"},
    {"model": "scaledown/extract", "entities": {"x": ""}},
    {"model": "scaledown/extract", "entities": {"x": {"description": "unsupported"}}},
    {"model": "scaledown/extract", "entities": {str(i): "field" for i in range(51)}},
])
def test_reject_invalid_contract(override):
    with pytest.raises(ValueError):
        ScaleDownRequest(**({"model": "scaledown/summarize", "text": "hello"} | override))


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [("missing_key", 503), ("timeout", 504), ("error", 502), ("nonjson", 502), ("empty", 502), ("truncated", 502)])
async def test_failures_are_sanitized_and_never_retried(case, expected, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if case == "timeout":
            raise httpx.ReadTimeout("secret", request=request)
        if case == "error":
            return httpx.Response(401, text="secret")
        if case == "nonjson":
            return httpx.Response(200, text="secret")
        if case == "truncated":
            return httpx.Response(200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})
        return httpx.Response(200, json={})
    monkeypatch.setattr(main, "OPENROUTER_API_KEY", "" if case == "missing_key" else "mock")
    monkeypatch.setattr(main, "rate_limit_or_raise", lambda _: None)
    monkeypatch.setattr(main, "llm_client_kwargs", lambda: {"transport": httpx.MockTransport(handler)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/api/scaledown", json={"model": "scaledown/summarize", "text": "Example"})
    assert response.status_code == expected
    assert "secret" not in response.text
    assert len(calls) == (0 if case == "missing_key" else 1)


@pytest.mark.parametrize("content", ['{"summary":"partial","finish_reason":"length"}', '{}', '[]', 'not json'])
def test_summary_rejects_invalid_or_truncated_nested_result(content):
    with pytest.raises(ValueError):
        scaledown_result({"choices": [{"message": {"content": content}}]}, "scaledown/summarize")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["scaledown/summarize", "scaledown/extract", "scaledown/classify-1"])
async def test_chat_cannot_bypass_dedicated_workspace(model, monkeypatch):
    def unexpected():
        raise AssertionError("Must not reach upstream")
    monkeypatch.setattr(main, "llm_client_kwargs", unexpected)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/api/chat", json={"model_id": model, "messages": [{"role": "user", "content": "Example"}]})
    assert response.status_code == 422
