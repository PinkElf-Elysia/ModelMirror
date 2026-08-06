from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from server.mcp.public_proxy import ALLOWED_ADAPTERS as PROXY_ADAPTERS
from server.sandbox_sidecar import safe_http
from server.sandbox_sidecar.public_mcp import (
    ADAPTER_TOOL_NAMES,
    BUILDERS,
    _airbnb_script_data,
    _find_airbnb_branch,
    airbnb_details_payload,
    fetch_payload,
    geowire_providers_payload,
    quickchart_url,
)
from server.sandbox_sidecar.public_server import PUBLIC_ADAPTERS
from server.sandbox_sidecar.safe_http import (
    NetworkPolicyError,
    ResponseLimitError,
    SafeHttpClient,
    SafeHttpResponse,
    resolve_public_addresses,
    validate_public_https_url,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_public_url_policy_rejects_ssrf_primitives() -> None:
    for url in (
        "http://example.com",
        "https://localhost/health",
        "https://127.0.0.1/health",
        "https://[::1]/health",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://service.internal/",
    ):
        with pytest.raises(NetworkPolicyError):
            validate_public_https_url(url)

    normalized, host, port, path = validate_public_https_url(
        "https://Example.COM/docs?q=1",
        allowed_hosts=frozenset({"example.com"}),
    )
    assert normalized == "https://example.com/docs?q=1"
    assert (host, port, path) == ("example.com", 443, "/docs?q=1")

    with pytest.raises(NetworkPolicyError, match="固定出口清单"):
        validate_public_https_url(
            "https://other.example/",
            allowed_hosts=frozenset({"example.com"}),
        )


def test_dns_policy_rejects_private_or_mixed_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(NetworkPolicyError, match="私网"):
        resolve_public_addresses("example.com")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ],
    )
    assert resolve_public_addresses("example.com") == ("93.184.216.34",)


def test_dns_policy_allows_only_explicit_rfc2544_fake_ip_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.42", 443)),
        ],
    )
    monkeypatch.delenv("MCP_PUBLIC_ALLOW_SYNTHETIC_DNS", raising=False)
    with pytest.raises(NetworkPolicyError):
        resolve_public_addresses("example.com")

    monkeypatch.setenv("MCP_PUBLIC_ALLOW_SYNTHETIC_DNS", "true")
    assert resolve_public_addresses("example.com") == ("198.18.0.42",)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.42", 443)),
        ],
    )
    with pytest.raises(NetworkPolicyError):
        resolve_public_addresses("example.com")


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self._headers = headers
        self._body = body
        self._offset = 0

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers.items())

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk


class _FakeConnection:
    responses: list[_FakeResponse] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.response = self.responses.pop(0)

    def request(self, *args: Any, **kwargs: Any) -> None:
        return None

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        return None


def test_redirect_target_is_revalidated_and_response_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safe_http, "_PinnedHTTPSConnection", _FakeConnection)
    monkeypatch.setattr(
        safe_http,
        "resolve_public_addresses",
        lambda host, port=443: ("93.184.216.34",),
    )
    _FakeConnection.responses = [
        _FakeResponse(302, {"location": "https://127.0.0.1/admin"})
    ]
    with pytest.raises(NetworkPolicyError):
        SafeHttpClient(allowed_hosts=None).request("https://example.com/")

    _FakeConnection.responses = [
        _FakeResponse(200, {"content-length": "33"}, b"x" * 33)
    ]
    with pytest.raises(ResponseLimitError):
        SafeHttpClient(
            allowed_hosts=frozenset({"example.com"}),
            max_response_bytes=32,
        ).request("https://example.com/")


def test_robots_policy_is_fail_closed_and_cannot_be_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SafeHttpClient(allowed_hosts=frozenset({"example.com"}))

    def deny_robots(*args: Any, **kwargs: Any) -> SafeHttpResponse:
        return SafeHttpResponse(
            url="https://example.com/robots.txt",
            status=200,
            headers={"content-type": "text/plain"},
            body=b"User-agent: *\nDisallow: /private\n",
        )

    monkeypatch.setattr(SafeHttpClient, "request", deny_robots)
    with pytest.raises(NetworkPolicyError, match="robots.txt"):
        client.assert_robots_allowed("https://example.com/private/data", "test-agent")

    failed = SafeHttpClient(allowed_hosts=frozenset({"example.com"}))

    def unavailable(*args: Any, **kwargs: Any) -> SafeHttpResponse:
        return SafeHttpResponse(
            url="https://example.com/robots.txt",
            status=503,
            headers={},
            body=b"",
        )

    monkeypatch.setattr(SafeHttpClient, "request", unavailable)
    with pytest.raises(NetworkPolicyError, match="失败关闭"):
        failed.assert_robots_allowed("https://example.com/docs", "test-agent")


def test_fetch_contract_uses_safe_client_and_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeSafeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["allowed_hosts"] is None

        def assert_robots_allowed(self, url: str, user_agent: str) -> None:
            events.append(f"robots:{url}")

        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            events.append(f"request:{url}")
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=(b"<html><main><h1>Title</h1><p>Hello world</p></main></html>"),
            )

    monkeypatch.setattr(
        "server.sandbox_sidecar.public_mcp.SafeHttpClient",
        FakeSafeClient,
    )
    content = fetch_payload("https://example.com/docs", max_length=8)
    assert "Title" in content
    assert "内容已截断" in content
    assert events == [
        "robots:https://example.com/docs",
        "request:https://example.com/docs",
    ]


def test_quickchart_contract_is_url_only_and_rejects_executable_config() -> None:
    result = quickchart_url(
        "bar",
        [{"label": "销售额", "data": [1, 2]}],
        ["一月", "二月"],
        "月度销售",
    )
    assert result["url"].startswith("https://quickchart.io/chart?c=")
    assert result["datasets"] == 1
    assert "本批不写入本地文件" in result["note"]

    with pytest.raises(ValueError, match="远程 URL"):
        quickchart_url(
            "bar",
            [{"data": [1]}],
            options={"image": {"url": "https://evil.invalid/a.png"}},
        )
    with pytest.raises(ValueError, match="固定允许清单"):
        quickchart_url("unknown", [{"data": [1]}])


def test_airbnb_schema_drift_probe_and_input_limits() -> None:
    fixture = {
        "niobeClientData": [
            [
                "key",
                {
                    "data": {
                        "presentation": {
                            "staysSearch": {"results": {"searchResults": []}}
                        }
                    }
                },
            ]
        ]
    }
    html = (
        '<script id="data-deferred-state-0">'
        + json.dumps(fixture)
        + "</script>"
    )
    data = _airbnb_script_data(html)
    branch = _find_airbnb_branch(
        data,
        ("data", "presentation", "staysSearch", "results"),
    )
    assert branch == {"searchResults": []}

    with pytest.raises(ValueError, match="房源 ID"):
        airbnb_details_payload("../admin")


def test_public_adapter_contract_and_container_isolation() -> None:
    assert set(BUILDERS) == set(ADAPTER_TOOL_NAMES) == PROXY_ADAPTERS == PUBLIC_ADAPTERS
    assert ADAPTER_TOOL_NAMES == {
        "fetch-mcp": ("fetch",),
        "quickchart-mcp": ("generate_chart",),
        "geowire-mcp": (
            "search_places",
            "geocode_address",
            "reverse_geocode",
            "get_directions",
            "distance_matrix",
            "list_geo_providers",
        ),
    }
    assert geowire_providers_payload()["credentials"] == "none"

    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (
        PROJECT_ROOT / "server" / "sandbox_sidecar" / "Dockerfile"
    ).read_text(encoding="utf-8")
    public_server = (
        PROJECT_ROOT / "server" / "sandbox_sidecar" / "public_server.py"
    ).read_text(encoding="utf-8")
    safe_client = (
        PROJECT_ROOT / "server" / "sandbox_sidecar" / "safe_http.py"
    ).read_text(encoding="utf-8")

    assert "mcp-public:" in compose
    assert "mcp_public_egress" in compose
    assert "read_only: true" in compose
    assert "pids_limit: 128" in compose
    assert "cap_drop:" in compose and "no-new-privileges:true" in compose
    assert "USER 65532:65532" in dockerfile
    assert "preexec_fn" not in public_server
    assert '"--read-only"' in public_server
    assert '"--compute-limits"' in public_server
    assert "resolve_public_addresses(host, port)" in safe_client
    assert "address.is_global" in safe_client
    assert "server_hostname=self.host" in safe_client
