from __future__ import annotations

import io
from email.message import Message

import pytest
from fastapi.testclient import TestClient

from ai_research_control import console_gateway


class FakeUpstream:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self.closed = False
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        self.headers["Set-Cookie"] = "forbidden=1"
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


def settings(*, expose_health: bool = True) -> console_gateway.GatewaySettings:
    return console_gateway.GatewaySettings(
        target="http://ai-research-control:8080",
        timeout_seconds=5.0,
        expose_health=expose_health,
    )


def test_gateway_forwards_fixed_path_query_body_and_minimal_headers(monkeypatch) -> None:
    captured = {}
    upstream = FakeUpstream(b'{"projectId":"rp_fixed"}')

    def open_upstream(request, timeout):
        captured.update(
            url=request.full_url,
            method=request.method,
            body=request.data,
            cookie=request.headers.get("Cookie"),
            authorization=request.headers.get("Authorization"),
            content_type=request.get_header("Content-type"),
            timeout=timeout,
        )
        return upstream

    monkeypatch.setattr(console_gateway, "open_upstream", open_upstream)
    with TestClient(console_gateway.create_gateway_app(settings())) as client:
        response = client.post(
            "/api/v1/projects?source=console",
            headers={
                "Authorization": "Bearer must-not-forward",
                "Cookie": "private=1",
            },
            json={"title": "Agent research"},
        )

    assert response.status_code == 200
    assert response.json() == {"projectId": "rp_fixed"}
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "set-cookie" not in response.headers
    assert upstream.closed is True
    assert captured["url"] == (
        "http://ai-research-control:8080/api/v1/projects?source=console"
    )
    assert captured["method"] == "POST"
    assert captured["body"] == b'{"title":"Agent research"}'
    assert captured["cookie"] is None
    assert captured["authorization"] is None
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 5.0


def test_gateway_streams_assets_and_artifacts_and_closes(monkeypatch) -> None:
    upstream = FakeUpstream(b"PK\x03\x04artifact", content_type="application/zip")
    upstream.headers["Content-Disposition"] = 'attachment; filename="result.zip"'
    upstream.headers["X-Artifact-Sha256"] = "a" * 64
    upstream.headers["X-Content-Sha256"] = "b" * 64
    monkeypatch.setattr(
        console_gateway,
        "open_upstream",
        lambda request, timeout: upstream,
    )
    with TestClient(console_gateway.create_gateway_app(settings())) as client:
        response = client.get("/api/v1/projects/rp_fixed/artifacts/upstream-quarto.zip")

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04artifact"
    assert response.headers["content-disposition"] == 'attachment; filename="result.zip"'
    assert response.headers["x-artifact-sha256"] == "a" * 64
    assert response.headers["x-content-sha256"] == "b" * 64
    assert upstream.closed is True


def test_gateway_health_is_local_and_unsupported_methods_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        console_gateway,
        "open_upstream",
        lambda request, timeout: pytest.fail("health must not reach upstream"),
    )
    with TestClient(console_gateway.create_gateway_app(settings())) as client:
        health = client.get("/gateway-healthz")
        options = client.options("/api/v1/projects")

    assert health.status_code == 200
    assert health.json() == {"status": "alive"}
    assert options.status_code == 405


def test_inspect_gateway_forwards_only_allowlisted_loopback_host(monkeypatch) -> None:
    captured = {}

    def open_upstream(request, timeout):
        captured["host"] = request.get_header("Host")
        captured["origin"] = request.get_header("Origin")
        return FakeUpstream(b'{"files":[]}')

    monkeypatch.setattr(console_gateway, "open_upstream", open_upstream)
    inspect_settings = console_gateway.GatewaySettings(
        target="http://ai-research-inspect-view:7575",
        timeout_seconds=5.0,
        allowed_host_headers=("127.0.0.1:8893", "localhost:8893"),
    )
    with TestClient(console_gateway.create_gateway_app(inspect_settings)) as client:
        accepted = client.get(
            "/api/log-files",
            headers={
                "Host": "127.0.0.1:8893",
                "Origin": "http://127.0.0.1:8893",
            },
        )
        rejected = client.get(
            "/api/log-files",
            headers={
                "Host": "attacker.example",
                "Origin": "http://attacker.example",
            },
        )

    assert accepted.status_code == 200
    assert captured == {
        "host": "127.0.0.1:8893",
        "origin": "http://127.0.0.1:8893",
    }
    assert rejected.status_code == 400


def test_gateway_rejects_unsafe_targets_bodies_redirects_and_large_responses(
    monkeypatch,
) -> None:
    for value in (
        "https://ai-research-control:8080",
        "http://example.com:8080",
        "http://ai-research-control:8081",
        "http://user:secret@ai-research-control:8080",
        "http://ai-research-control:8080/api",
    ):
        with pytest.raises(ValueError):
            console_gateway.validate_target(
                value,
                expected_host="ai-research-control",
                expected_port=8080,
            )

    redirect = FakeUpstream(b"", status=302)
    monkeypatch.setattr(
        console_gateway,
        "open_upstream",
        lambda request, timeout: redirect,
    )
    with TestClient(
        console_gateway.create_gateway_app(settings()),
        raise_server_exceptions=False,
    ) as client:
        body_rejected = client.request(
            "GET",
            "/api/v1/projects",
            content=b"not-allowed",
        )
        oversized = client.post(
            "/api/v1/projects",
            content=b"x" * (console_gateway.MAX_REQUEST_BYTES + 1),
        )
        redirected = client.get("/")

    assert body_rejected.status_code == 400
    assert oversized.status_code == 413
    assert redirected.status_code == 502
    assert redirect.closed is True

    too_large = FakeUpstream(
        b"",
        content_length=console_gateway.MAX_RESPONSE_BYTES + 1,
    )
    monkeypatch.setattr(
        console_gateway,
        "open_upstream",
        lambda request, timeout: too_large,
    )
    with TestClient(console_gateway.create_gateway_app(settings())) as client:
        response = client.get("/artifact.zip")
    assert response.status_code == 502
    assert too_large.closed is True


def test_gateway_disables_environment_proxy_and_redirects(monkeypatch) -> None:
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured["timeout"] = timeout
            return FakeUpstream(b"{}")

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(console_gateway, "build_opener", build)
    request = console_gateway.UrlRequest("http://ai-research-control:8080/healthz")
    response = console_gateway.open_upstream(request, 5.0)

    assert captured["timeout"] == 5.0
    assert any(
        isinstance(item, console_gateway.ProxyHandler) and item.proxies == {}
        for item in captured["handlers"]
    )
    assert any(isinstance(item, console_gateway.NoRedirect) for item in captured["handlers"])
    response.close()
