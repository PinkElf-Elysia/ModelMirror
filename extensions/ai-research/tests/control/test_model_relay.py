from __future__ import annotations

import io
from email.message import Message

import pytest
from fastapi.testclient import TestClient

from ai_research_control.config import _model_bridge_url
from ai_research_control import model_relay


class FakeUpstream:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self.closed = False
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["X-ModelMirror-Route-Run-Id"] = "chatrun_relay"

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


def settings() -> model_relay.RelaySettings:
    return model_relay.RelaySettings(
        target="http://127.0.0.1:8000/api/ai-research/v1",
        timeout_seconds=5.0,
    )


def test_relay_forwards_only_fixed_models_path_and_minimal_headers(monkeypatch) -> None:
    captured = {}
    upstream = FakeUpstream(b'{"data":[{"id":"fixed"}]}')

    def open_upstream(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["authorization"] = request.headers["Authorization"]
        captured["cookie"] = request.headers.get("Cookie")
        captured["timeout"] = timeout
        return upstream

    monkeypatch.setattr(model_relay, "open_upstream", open_upstream)
    with TestClient(model_relay.create_app(settings())) as client:
        response = client.get(
            "/api/ai-research/v1/models",
            headers={"Authorization": "Bearer relay-secret", "Cookie": "private=1"},
        )
        rejected = client.get(
            "/api/ai-research/v1/models?target=http://example.com",
            headers={"Authorization": "Bearer relay-secret"},
        )
        missing = client.get(
            "/api/ai-research/v1/arbitrary",
            headers={"Authorization": "Bearer relay-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"data": [{"id": "fixed"}]}
    assert response.headers["x-modelmirror-route-run-id"] == "chatrun_relay"
    assert upstream.closed is True
    assert captured == {
        "url": "http://127.0.0.1:8000/api/ai-research/v1/models",
        "method": "GET",
        "authorization": "Bearer relay-secret",
        "cookie": None,
        "timeout": 5.0,
    }
    assert rejected.status_code == 400
    assert missing.status_code == 404


def test_relay_streams_chat_and_closes_upstream(monkeypatch) -> None:
    upstream = FakeUpstream(
        b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        content_type="text/event-stream; charset=utf-8",
    )
    monkeypatch.setattr(model_relay, "open_upstream", lambda request, timeout: upstream)
    with TestClient(model_relay.create_app(settings())) as client:
        response = client.post(
            "/api/ai-research/v1/chat/completions",
            headers={"Authorization": "Bearer relay-secret"},
            json={"model": "fixed", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.content.endswith(b"data: [DONE]\n\n")
    assert upstream.closed is True


def test_relay_rejects_unsafe_target_content_and_oversized_request(monkeypatch) -> None:
    for value in (
        "https://host.docker.internal:8000/api/ai-research/v1",
        "http://example.com:8000/api/ai-research/v1",
        "http://127.0.0.1:8000/api/chat",
        "http://user:secret@127.0.0.1:8000/api/ai-research/v1",
    ):
        with pytest.raises(ValueError):
            model_relay.validate_target(value)

    monkeypatch.setattr(
        model_relay,
        "open_upstream",
        lambda request, timeout: FakeUpstream(b"secret", content_type="text/html"),
    )
    with TestClient(model_relay.create_app(settings()), raise_server_exceptions=False) as client:
        content_rejected = client.get(
            "/api/ai-research/v1/models",
            headers={"Authorization": "Bearer relay-secret"},
        )
        oversized = client.post(
            "/api/ai-research/v1/chat/completions",
            headers={
                "Authorization": "Bearer relay-secret",
                "Content-Type": "application/json",
            },
            content=b"x" * (model_relay.MAX_REQUEST_BYTES + 1),
        )

    assert content_rejected.status_code == 502
    assert "secret" not in content_rejected.text
    assert oversized.status_code == 413


def test_control_configuration_requires_the_internal_relay() -> None:
    assert _model_bridge_url(
        "http://ai-research-model-relay:8090/api/ai-research/v1"
    ) == "http://ai-research-model-relay:8090/api/ai-research/v1"
    with pytest.raises(ValueError, match="internal model relay"):
        _model_bridge_url(
            "http://host.docker.internal:8000/api/ai-research/v1"
        )


def test_open_upstream_disables_environment_proxy_and_redirects(monkeypatch) -> None:
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured["timeout"] = timeout
            return FakeUpstream(b"{}")

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(model_relay, "build_opener", build)
    request = model_relay.UrlRequest("http://127.0.0.1:8000/api/ai-research/v1/models")
    response = model_relay.open_upstream(request, 5.0)

    assert captured["timeout"] == 5.0
    assert any(isinstance(item, model_relay.ProxyHandler) and item.proxies == {} for item in captured["handlers"])
    assert any(isinstance(item, model_relay.NoRedirect) for item in captured["handlers"])
    response.close()
