from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from server.mcp.public_proxy import ALLOWED_ADAPTERS as PROXY_ADAPTERS
from server.sandbox_sidecar import safe_http
from server.sandbox_sidecar.public_mcp import (
    ADAPTER_TOOL_NAMES,
    BUILDERS,
    PUBLIC_SCHEMA_SHA256,
    _airbnb_script_data,
    _biomcp_client,
    _chess_client,
    _dexpaprika_client,
    _fantasy_pl_client,
    _find_airbnb_branch,
    _gitmcp_client,
    _idea_reality_client,
    _open_websearch_client,
    _reddit_buddy_client,
    _safedep_client,
    airbnb_details_payload,
    anilist_genres_payload,
    anilist_get_anime_payload,
    anilist_search_anime_payload,
    biomcp_get_payload,
    biomcp_search_payload,
    chess_player_profile_payload,
    chess_player_stats_payload,
    dexpaprika_networks_payload,
    dexpaprika_search_payload,
    dexpaprika_stats_payload,
    docker_hub_repository_payload,
    docker_hub_search_payload,
    docker_hub_tags_payload,
    duckduckgo_search_payload,
    fetch_payload,
    fantasy_pl_fixtures_payload,
    fantasy_pl_player_payload,
    fantasy_pl_search_players_payload,
    geowire_providers_payload,
    gitmcp_documentation_payload,
    gitmcp_search_code_payload,
    gitmcp_search_documentation_payload,
    idea_reality_payload,
    normalize_github_repository,
    open_websearch_payload,
    quickchart_url,
    reddit_browse_payload,
    reddit_search_payload,
    safedep_available_versions_payload,
    safedep_latest_version_payload,
    safedep_malware_payload,
    safedep_vulnerabilities_payload,
    shadcn_component_metadata_payload,
    shadcn_list_components_payload,
)
from server.sandbox_sidecar.public_server import (
    ALL_PUBLIC_ADAPTERS,
    DEFAULT_PUBLIC_ADAPTERS,
    PUBLIC_ADAPTERS,
    configured_public_adapters,
)
from server.sandbox_sidecar.smoke_public_adapters import (
    PUBLIC_EXPANSION_ADAPTERS,
    TIMEOUT_TOOL_PROBES,
    WAVE16A_ADAPTERS,
    WAVE16B_ADAPTERS,
    WAVE17A_ADAPTERS,
    WAVE25A_ADAPTERS,
    WAVE25B_ADAPTERS,
    _adapter_ids,
)
from server.sandbox_sidecar.safe_http import (
    NetworkPolicyError,
    ResponseLimitError,
    SafeHttpClient,
    SafeHttpResponse,
    resolve_public_addresses,
    validate_public_https_url,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_smoke_modules_do_not_import_sdk_during_pytest_collection() -> None:
    script = textwrap.dedent(
        f"""
        import importlib
        import sys
        from pathlib import Path

        project_root = Path({str(PROJECT_ROOT)!r})
        sys.path.insert(0, str(project_root / "server"))
        for module_name in (
            "server.sandbox_sidecar.smoke_public_adapters",
            "server.sandbox_sidecar.smoke_file_artifacts",
            "server.sandbox_sidecar.smoke_file_code_index",
        ):
            module = importlib.import_module(module_name)
            for runtime_name in (
                "ClientSession",
                "StdioServerParameters",
                "stdio_client",
            ):
                if hasattr(module, runtime_name):
                    raise SystemExit(
                        f"{{module_name}} eagerly imported {{runtime_name}}"
                    )
            client_session, stdio_parameters, stdio_client = module._load_mcp_stdio()
            if client_session.__module__ != "mcp.client.session":
                raise SystemExit(f"{{module_name}} loaded the wrong ClientSession")
            if stdio_parameters.__module__ != "mcp.client.stdio":
                raise SystemExit(f"{{module_name}} loaded the wrong stdio parameters")
            if stdio_client.__module__ != "mcp.client.stdio":
                raise SystemExit(f"{{module_name}} loaded the wrong stdio client")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
    assert set(BUILDERS) == set(ADAPTER_TOOL_NAMES) == ALL_PUBLIC_ADAPTERS
    assert PROXY_ADAPTERS == DEFAULT_PUBLIC_ADAPTERS
    assert WAVE25A_ADAPTERS.issubset(PROXY_ADAPTERS)
    assert WAVE25B_ADAPTERS & PROXY_ADAPTERS == {"rishijatia-fantasy-pl-mcp"}
    assert set(TIMEOUT_TOOL_PROBES) == PUBLIC_EXPANSION_ADAPTERS
    assert PUBLIC_ADAPTERS == DEFAULT_PUBLIC_ADAPTERS == {
        "fetch-mcp",
        "quickchart-mcp",
        "geowire-mcp",
        "nickclyde-duckduckgo-mcp-server",
        "jpisnice-shadcn-ui-mcp-server",
        "docker-hub-mcp",
        "genomoncology-biomcp",
        "safedep-vet",
        "aas-ee-open-websearch",
        "mnemox-ai-idea-reality-mcp",
        "idosal-git-mcp",
        "coinpaprika-dexpaprika-mcp",
        "pab1it0-chess-mcp",
        "rishijatia-fantasy-pl-mcp",
        "yuna0x0-anilist-mcp",
    }
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
        "nickclyde-duckduckgo-mcp-server": ("search",),
        "jpisnice-shadcn-ui-mcp-server": (
            "list_components",
            "get_component_metadata",
        ),
        "docker-hub-mcp": (
            "search",
            "getRepositoryInfo",
            "listRepositoryTags",
        ),
        "genomoncology-biomcp": ("search", "get"),
        "safedep-vet": (
            "get_package_version_vulnerabilities",
            "get_package_version_popularity",
            "get_package_version_license_info",
            "get_package_version_malware_report",
            "get_package_latest_version",
            "get_package_available_versions",
        ),
        "aas-ee-open-websearch": ("search",),
        "mnemox-ai-idea-reality-mcp": ("idea_check",),
        "idosal-git-mcp": (
            "fetch_repository_documentation",
            "search_repository_documentation",
            "search_repository_code",
        ),
        "coinpaprika-dexpaprika-mcp": ("getNetworks", "getStats", "search"),
        "pab1it0-chess-mcp": (
            "get_player_profile",
            "get_player_stats",
        ),
        "yuna0x0-anilist-mcp": ("get_genres", "search_anime", "get_anime"),
        "karanb192-reddit-mcp-buddy": ("browse_subreddit", "search_reddit"),
        "rishijatia-fantasy-pl-mcp": (
            "search_fpl_players",
            "get_player_information",
            "list_fpl_fixtures",
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
    assert "smoke_public_adapters.py" in dockerfile
    assert "--contract-only" in dockerfile

    public_block = compose[
        compose.index("  mcp-public:\n") : compose.index("  mcp-files:\n")
    ]
    allowlist_line = next(
        line
        for line in public_block.splitlines()
        if "MCP_PUBLIC_ALLOWED_ADAPTERS:" in line
    )
    assert "nickclyde-duckduckgo-mcp-server" in allowlist_line
    assert "jpisnice-shadcn-ui-mcp-server" in allowlist_line
    assert "docker-hub-mcp" in allowlist_line
    assert "genomoncology-biomcp" in allowlist_line
    assert "safedep-vet" in allowlist_line
    assert "aas-ee-open-websearch" in allowlist_line
    assert "mnemox-ai-idea-reality-mcp" in allowlist_line
    assert "idosal-git-mcp" in allowlist_line
    assert "coinpaprika-dexpaprika-mcp" in allowlist_line
    assert "pab1it0-chess-mcp" in allowlist_line
    assert "yuna0x0-anilist-mcp" in allowlist_line
    assert "karanb192-reddit-mcp-buddy" not in allowlist_line
    assert "rishijatia-fantasy-pl-mcp" in allowlist_line
    assert "image: modelmirror-mcp-public:wave17a-v1" in public_block


def test_wave16_allowlist_is_enabled_after_acceptance_and_exact_when_overridden() -> None:
    assert configured_public_adapters("") == DEFAULT_PUBLIC_ADAPTERS
    assert configured_public_adapters(
        "nickclyde-duckduckgo-mcp-server,docker-hub-mcp"
    ) == {
        "nickclyde-duckduckgo-mcp-server",
        "docker-hub-mcp",
    }
    with pytest.raises(RuntimeError, match="invalid_public_adapter_allowlist"):
        configured_public_adapters("unknown-public-adapter")
    assert _adapter_ids(",".join(sorted(WAVE16A_ADAPTERS))) == WAVE16A_ADAPTERS
    assert _adapter_ids(",".join(sorted(WAVE16B_ADAPTERS))) == WAVE16B_ADAPTERS
    assert _adapter_ids(",".join(sorted(WAVE17A_ADAPTERS))) == WAVE17A_ADAPTERS
    assert _adapter_ids(",".join(sorted(WAVE25A_ADAPTERS))) == WAVE25A_ADAPTERS
    assert _adapter_ids(",".join(sorted(WAVE25B_ADAPTERS))) == WAVE25B_ADAPTERS
    assert (
        _adapter_ids(",".join(sorted(PUBLIC_EXPANSION_ADAPTERS)))
        == PUBLIC_EXPANSION_ADAPTERS
    )
    with pytest.raises(RuntimeError, match="public_smoke_adapter_selection_invalid"):
        _adapter_ids("fetch-mcp")


def test_duckduckgo_contract_uses_fixed_post_strict_search_and_bounded_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    class FakeSafeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["allowed_hosts"] == {"html.duckduckgo.com"}
            assert kwargs["max_redirects"] == 0

        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            events.append((url, kwargs))
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=(
                    b'<div class="result"><a class="result__a" '
                    b'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs">'
                    b'Example <strong>Docs</strong></a>'
                    b'<a class="result__snippet">Safe public summary</a></div>'
                ),
            )

    monkeypatch.setattr(
        "server.sandbox_sidecar.public_mcp.SafeHttpClient",
        FakeSafeClient,
    )
    result = duckduckgo_search_payload("model context protocol", 5, "us-en")

    assert result["safe_search"] == "strict"
    assert result["count"] == 1
    assert result["results"] == [
        {
            "title": "Example Docs",
            "url": "https://example.com/docs",
            "snippet": "Safe public summary",
        }
    ]
    assert events[0][0] == "https://html.duckduckgo.com/html"
    assert events[0][1]["method"] == "POST"
    assert b"kp=1" in events[0][1]["body"]
    assert b"kl=us-en" in events[0][1]["body"]
    with pytest.raises(ValueError, match="region"):
        duckduckgo_search_payload("query", region="../../admin")


def test_shadcn_contract_pins_repository_commit_and_rejects_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []
    payload = [
        {
            "type": "file",
            "name": "_registry.ts",
            "path": "apps/v4/registry/new-york-v4/ui/_registry.ts",
            "sha": "c" * 40,
            "size": 3456,
        },
        {
            "type": "file",
            "name": "button.tsx",
            "path": "apps/v4/registry/new-york-v4/ui/button.tsx",
            "sha": "a" * 40,
            "size": 1234,
        },
        {
            "type": "file",
            "name": "accordion.tsx",
            "path": "apps/v4/registry/new-york-v4/ui/accordion.tsx",
            "sha": "b" * 40,
            "size": 2345,
        },
    ]

    class FakeSafeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["allowed_hosts"] == {"api.github.com"}
            assert kwargs["max_redirects"] == 0

        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append(url)
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    monkeypatch.setattr(
        "server.sandbox_sidecar.public_mcp.SafeHttpClient",
        FakeSafeClient,
    )
    listed = shadcn_list_components_payload()
    metadata = shadcn_component_metadata_payload("button")

    assert listed["components"] == ["accordion", "button"]
    assert metadata["name"] == "button"
    assert metadata["commit"] == "d14b6e69a91f0fc99e31a7adb26a48d661df9911"
    assert all("ref=d14b6e69a91f0fc99e31a7adb26a48d661df9911" in url for url in requested)
    with pytest.raises(ValueError, match="componentName"):
        shadcn_component_metadata_payload("../button")


def test_shadcn_payloads_can_reuse_one_pinned_directory_snapshot() -> None:
    entries = [
        {
            "name": "button",
            "path": "apps/v4/registry/new-york-v4/ui/button.tsx",
            "sha": "a" * 40,
            "size": 1234,
        }
    ]
    listed = shadcn_list_components_payload(entries=entries)
    metadata = shadcn_component_metadata_payload("button", entries=entries)
    assert listed["components"] == ["button"]
    assert metadata["sha"] == "a" * 40


def test_docker_hub_contract_uses_only_public_metadata_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class FakeSafeClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["allowed_hosts"] == {"hub.docker.com"}
            assert kwargs["max_redirects"] == 0

        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append(url)
            if "/api/search/v4" in url:
                payload: Any = {
                    "total": 1,
                    "results": [
                        {
                            "name": "library/python",
                            "type": "image",
                            "publisher": {"name": "Docker Official Images"},
                            "short_description": "Python runtime",
                            "badge": "official",
                            "star_count": 100,
                            "pull_count": "1B+",
                            "updated_at": "2026-01-01T00:00:00Z",
                            "archived": False,
                        }
                    ],
                }
            elif url.endswith("/namespaces/library/repositories/python"):
                payload = {
                    "namespace": "library",
                    "name": "python",
                    "description": "Python runtime",
                    "is_private": False,
                    "star_count": 100,
                    "pull_count": 1000,
                    "media_types": ["application/vnd.oci.image.manifest.v1+json"],
                    "content_types": ["image"],
                }
            else:
                payload = {
                    "count": 1,
                    "results": [
                        {
                            "name": "3.12-slim",
                            "last_updated": "2026-01-01T00:00:00Z",
                            "full_size": 42,
                            "images": [
                                {
                                    "architecture": "amd64",
                                    "os": "linux",
                                    "digest": "sha256:" + "c" * 64,
                                    "size": 42,
                                    "status": "active",
                                }
                            ],
                        }
                    ],
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    monkeypatch.setattr(
        "server.sandbox_sidecar.public_mcp.SafeHttpClient",
        FakeSafeClient,
    )
    assert docker_hub_search_payload("python", 5)["results"][0]["name"] == "library/python"
    assert docker_hub_repository_payload("library", "python")["name"] == "python"
    assert docker_hub_tags_payload("python", "library", 1, 5)["tags"][0]["name"] == "3.12-slim"
    assert requested == [
        "https://hub.docker.com/api/search/v4?custom_boosted_results=true&query=python&from=0&size=5",
        "https://hub.docker.com/v2/namespaces/library/repositories/python",
        "https://hub.docker.com/v2/namespaces/library/repositories/python/tags?page=1&page_size=5",
    ]
    with pytest.raises(ValueError, match="namespace"):
        docker_hub_repository_payload("../admin", "python")


def test_wave16_and_wave17_tool_schemas_are_frozen_and_exclude_control_fields() -> None:
    adapter_ids = PUBLIC_EXPANSION_ADAPTERS
    forbidden_tools = {
        "fetch_content",
        "get_component",
        "get_block",
        "apply_theme",
        "createRepository",
        "updateRepositoryInfo",
        "listNamespaces",
        "dockerHardenedImages",
        "biomcp",
        "study",
        "vet_query_execute_sql_query",
        "scan",
        "upload",
        "fetchWebContent",
        "fetch_generic_url_content",
        "proxy",
        "set_config",
    }
    for adapter_id in adapter_ids:
        tools = asyncio.run(BUILDERS[adapter_id]().list_tools())
        names = {tool.name for tool in tools}
        assert names == set(ADAPTER_TOOL_NAMES[adapter_id])
        assert names.isdisjoint(forbidden_tools)
        reviewed = [
            {"name": tool.name, "inputSchema": tool.inputSchema}
            for tool in sorted(tools, key=lambda item: item.name)
        ]
        schema_json = json.dumps(
            reviewed,
            sort_keys=True,
            separators=(",", ":"),
        )
        assert not any(
            token in schema_json.lower()
            for token in (
                '"url"',
                '"endpoint"',
                '"header"',
                '"environment"',
                '"command"',
                '"path"',
                '"token"',
            )
        )
        assert hashlib.sha256(schema_json.encode()).hexdigest() == PUBLIC_SCHEMA_SHA256[adapter_id]


def test_wave25_dexpaprika_contract_projects_bounded_public_metadata() -> None:
    policy_client = _dexpaprika_client()
    assert policy_client.allowed_hosts == {"api.dexpaprika.com"}
    assert policy_client.max_redirects == 0
    assert policy_client.max_response_bytes == 1024 * 1024
    requested: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append((url, kwargs))
            if url.endswith("/networks"):
                payload: Any = [
                    {
                        "id": "ethereum",
                        "display_name": "Ethereum",
                        "volume_usd_24h": 10.5,
                        "txns_24h": 20,
                        "pools_count": 30,
                    }
                ]
            elif url.endswith("/stats"):
                payload = {"chains": 36, "factories": 235, "pools": 10, "tokens": 20}
            else:
                payload = {
                    "tokens": [
                        {
                            "id": "0xabc",
                            "name": "Bitcoin",
                            "symbol": "BTC",
                            "chain": "ethereum",
                            "price_usd": 100,
                            "liquidity_usd": 200,
                            "volume_usd": 300,
                            "price_usd_change": 1.5,
                            "description": "must not escape",
                            "website": "https://untrusted.example",
                        }
                    ],
                    "pools": [
                        {
                            "id": "0xpool",
                            "dex_name": "Example DEX",
                            "chain": "ethereum",
                            "volume_usd": 50,
                            "transactions": 5,
                            "price_usd": 100,
                            "tokens": [],
                        }
                    ],
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    client = FakeClient()
    networks = dexpaprika_networks_payload(client=client)
    stats = dexpaprika_stats_payload(client=client)
    search = dexpaprika_search_payload("bitcoin", 2, client=client)
    assert networks["networks"][0]["id"] == "ethereum"
    assert stats["chains"] == 36
    assert search["tokens"][0]["symbol"] == "BTC"
    assert "description" not in search["tokens"][0]
    assert "website" not in search["tokens"][0]
    assert requested[-1][0] == "https://api.dexpaprika.com/search?query=bitcoin"
    assert all(set(kwargs["headers"]) == {"User-Agent"} for _, kwargs in requested)
    with pytest.raises(ValueError, match="max_results"):
        dexpaprika_search_payload("bitcoin", 11, client=client)


def test_wave25_chess_contract_accepts_only_normalized_public_usernames() -> None:
    policy_client = _chess_client()
    assert policy_client.allowed_hosts == {"api.chess.com"}
    assert policy_client.max_redirects == 0
    requested: list[str] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append(url)
            if url.endswith("/stats"):
                payload: Any = {
                    "chess_rapid": {
                        "last": {"rating": 2800, "date": 1},
                        "best": {"rating": 2900, "date": 2},
                        "record": {"win": 10, "loss": 2, "draw": 3},
                    },
                    "tactics": {"highest": {"rating": 9999}},
                }
            else:
                payload = {
                    "username": "hikaru",
                    "player_id": 15448422,
                    "name": "Hikaru Nakamura",
                    "title": "GM",
                    "country": "https://api.chess.com/pub/country/US",
                    "status": "premium",
                    "followers": 100,
                    "last_online": 1,
                    "joined": 2,
                    "is_streamer": True,
                    "verified": False,
                    "avatar": "https://images.example/avatar.png",
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    client = FakeClient()
    profile = chess_player_profile_payload("Hikaru", client=client)
    stats = chess_player_stats_payload("hikaru", client=client)
    assert profile["username"] == "hikaru"
    assert profile["country_code"] == "US"
    assert "avatar" not in profile
    assert set(stats["statistics"]) == {"chess_rapid"}
    assert requested == [
        "https://api.chess.com/pub/player/hikaru",
        "https://api.chess.com/pub/player/hikaru/stats",
    ]
    with pytest.raises(ValueError, match="username"):
        chess_player_profile_payload("../admin", client=client)


def test_wave25_anilist_contract_uses_fixed_graphql_documents_without_auth() -> None:
    requested: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            body = json.loads(kwargs["body"])
            requested.append((url, kwargs, body))
            query = body["query"]
            if "GenreCollection" in query:
                data: Any = {"GenreCollection": ["Action", "Adventure"]}
            elif "ModelMirrorAnimeSearch" in query:
                data = {
                    "Page": {
                        "pageInfo": {"hasNextPage": False},
                        "media": [
                            {
                                "id": 154587,
                                "idMal": 52991,
                                "title": {"romaji": "Sousou no Frieren", "english": "Frieren", "native": "葬送のフリーレン"},
                                "format": "TV",
                                "status": "FINISHED",
                                "season": "FALL",
                                "seasonYear": 2023,
                                "episodes": 28,
                                "duration": 24,
                                "genres": ["Adventure"],
                                "averageScore": 91,
                                "popularity": 500000,
                                "isAdult": False,
                                "siteUrl": "https://anilist.co/anime/154587",
                            }
                        ],
                    }
                }
            else:
                data = {
                    "Page": {
                        "media": [
                            {
                                "id": 154587,
                                "title": {"romaji": "Sousou no Frieren", "english": "Frieren", "native": "葬送のフリーレン"},
                                "genres": ["Adventure"],
                                "isAdult": False,
                            }
                        ]
                    }
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps({"data": data}).encode(),
            )

    client = FakeClient()
    genres = anilist_genres_payload(client=client)
    search = anilist_search_anime_payload("Frieren", 1, 2, client=client)
    details = anilist_get_anime_payload(154587, client=client)
    assert genres["genres"] == ["Action", "Adventure"]
    assert search["results"][0]["id"] == 154587
    assert "site_url" not in search["results"][0]
    assert details["requested_ids"] == [154587]
    for url, kwargs, body in requested:
        assert url == "https://graphql.anilist.co"
        assert kwargs["method"] == "POST"
        assert "Authorization" not in kwargs["headers"]
        assert set(body) == {"query", "variables"}
        assert "mutation" not in body["query"].lower()
    assert requested[1][2]["variables"] == {
        "search": "Frieren",
        "page": 1,
        "perPage": 2,
    }
    with pytest.raises(ValueError, match="between 1 and 5"):
        anilist_get_anime_payload([], client=client)
    with pytest.raises(ValueError, match="unique"):
        anilist_get_anime_payload([154587, 154587], client=client)


def test_wave25b_reddit_contract_uses_only_bounded_public_atom_feeds() -> None:
    policy_client = _reddit_buddy_client()
    assert policy_client.allowed_hosts == {"www.reddit.com"}
    assert policy_client.max_redirects == 0
    assert policy_client.max_response_bytes == 256 * 1024
    assert policy_client.minimum_intervals == {"www.reddit.com": 5.0}
    requested: list[tuple[str, dict[str, Any]]] = []
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Safe public post</title>
        <updated>2026-08-11T00:00:00Z</updated>
        <author><name>/u/example_user</name></author>
        <link rel="alternate" href="https://www.reddit.com/r/python/comments/abc123/example/" />
        <content type="html">&lt;p&gt;Bounded public preview&lt;/p&gt;&lt;a href="https://untrusted.example"&gt;external&lt;/a&gt;</content>
      </entry>
    </feed>"""

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append((url, kwargs))
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/atom+xml; charset=UTF-8"},
                body=feed.encode(),
            )

    client = FakeClient()
    browse = reddit_browse_payload("Python", "top", "week", 3, client=client)
    search = reddit_search_payload("model context protocol", "relevance", "year", 2, client=client)
    assert browse["data_source"] == "reddit-public-atom"
    assert browse["posts"][0]["author"] == "example_user"
    assert browse["posts"][0]["permalink"].startswith("https://www.reddit.com/r/python/")
    assert "untrusted.example" not in json.dumps(browse)
    assert requested[0][0] == "https://www.reddit.com/r/python/top/.rss?limit=3&t=week"
    assert requested[1][0].startswith("https://www.reddit.com/search.rss?")
    assert all(set(kwargs["headers"]) == {"User-Agent", "Accept"} for _, kwargs in requested)
    with pytest.raises(ValueError, match="subreddit"):
        reddit_browse_payload("../private", client=client)
    with pytest.raises(ValueError, match="limit"):
        reddit_search_payload("mcp", limit=11, client=client)


def test_wave25b_fantasy_pl_contract_projects_official_public_data_only() -> None:
    policy_client = _fantasy_pl_client()
    assert policy_client.allowed_hosts == {"fantasy.premierleague.com"}
    assert policy_client.max_redirects == 0
    assert policy_client.max_response_bytes == 2 * 1024 * 1024
    requested: list[tuple[str, dict[str, Any]]] = []
    bootstrap = {
        "elements": [
            {
                "id": 328,
                "first_name": "Mohamed",
                "second_name": "Salah",
                "web_name": "M.Salah",
                "team": 12,
                "element_type": 4,
                "now_cost": 145,
                "total_points": 250,
                "form": "8.1",
                "points_per_game": "7.2",
                "selected_by_percent": "45.0",
                "status": "a",
                "news": "must not escape",
            }
        ],
        "teams": [{"id": 12, "name": "Liverpool", "code": 14}],
        "element_types": [{"id": 4, "singular_name_short": "FWD"}],
    }
    fixtures = [
        {
            "id": 1,
            "event": 1,
            "kickoff_time": "2026-08-15T12:00:00Z",
            "started": False,
            "finished": False,
            "team_h": 12,
            "team_a": 1,
            "team_h_score": None,
            "team_a_score": None,
            "team_h_difficulty": 2,
            "team_a_difficulty": 4,
        }
    ]

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append((url, kwargs))
            payload: Any = fixtures if "/fixtures/" in url else bootstrap
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    client = FakeClient()
    search = fantasy_pl_search_players_payload("salah", "FWD", "Liverpool", 5, client=client)
    detail = fantasy_pl_player_payload(328, client=client)
    fixture_result = fantasy_pl_fixtures_payload(1, 12, 5, client=client)
    assert search["players"][0]["price"] == 14.5
    assert "news" not in search["players"][0]
    assert detail["player"]["id"] == 328
    assert fixture_result["fixtures"][0]["home_team_id"] == 12
    assert requested == [
        ("https://fantasy.premierleague.com/api/bootstrap-static/", {"headers": {"User-Agent": "ModelMirror-Fantasy-PL-MCP/0.1.7-compatible (+https://github.com/PinkElf-Elysia/ModelMirror)"}}),
        ("https://fantasy.premierleague.com/api/bootstrap-static/", {"headers": {"User-Agent": "ModelMirror-Fantasy-PL-MCP/0.1.7-compatible (+https://github.com/PinkElf-Elysia/ModelMirror)"}}),
        ("https://fantasy.premierleague.com/api/fixtures/?event=1", {"headers": {"User-Agent": "ModelMirror-Fantasy-PL-MCP/0.1.7-compatible (+https://github.com/PinkElf-Elysia/ModelMirror)"}}),
    ]
    with pytest.raises(ValueError, match="position"):
        fantasy_pl_search_players_payload("salah", "ANY", client=client)
    with pytest.raises(ValueError, match="player_id"):
        fantasy_pl_player_payload(True, client=client)


@pytest.mark.parametrize(
    ("call", "url"),
    (
        (
            lambda client: dexpaprika_stats_payload(client=client),
            "https://api.dexpaprika.com/stats",
        ),
        (
            lambda client: chess_player_profile_payload("hikaru", client=client),
            "https://api.chess.com/pub/player/hikaru",
        ),
        (
            lambda client: anilist_genres_payload(client=client),
            "https://graphql.anilist.co",
        ),
        (
            lambda client: reddit_browse_payload("python", client=client),
            "https://www.reddit.com/r/python/hot/.rss?limit=10",
        ),
        (
            lambda client: fantasy_pl_search_players_payload("salah", client=client),
            "https://fantasy.premierleague.com/api/bootstrap-static/",
        ),
    ),
)
def test_wave25_public_rate_limits_fail_closed(
    call: Any,
    url: str,
) -> None:
    class RateLimitedClient:
        def request(self, requested_url: str, **kwargs: Any) -> SafeHttpResponse:
            assert requested_url == url
            return SafeHttpResponse(
                url=requested_url,
                status=429,
                headers={"content-type": "application/json", "retry-after": "60"},
                body=b'{"error":"fixture-only"}',
            )

    with pytest.raises(ValueError, match="HTTP 429"):
        call(RateLimitedClient())


def test_biomcp_contract_projects_public_metadata_and_rejects_escape_hatches() -> None:
    policy_client = _biomcp_client()
    assert policy_client.timeout == 20
    assert policy_client.max_redirects == 0
    assert policy_client.max_response_bytes == 1024 * 1024
    assert set(policy_client.minimum_intervals.values()) == {2.0}
    requested: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append((url, kwargs))
            if "clinicaltrials.gov/api/v2/studies/NCT02576665" in url:
                payload = {
                    "protocolSection": {
                        "identificationModule": {
                            "nctId": "NCT02576665",
                            "briefTitle": "BRAF melanoma trial",
                        },
                        "statusModule": {
                            "overallStatus": "COMPLETED",
                            "startDateStruct": {"date": "2016-01"},
                            "completionDateStruct": {"date": "2020-01"},
                        },
                        "conditionsModule": {"conditions": ["Melanoma"]},
                    }
                }
            else:
                payload = {
                    "hitCount": 1,
                    "studies": [
                        {
                            "protocolSection": {
                                "identificationModule": {
                                    "nctId": "NCT02576665",
                                    "briefTitle": "BRAF melanoma trial",
                                },
                                "statusModule": {"overallStatus": "COMPLETED"},
                                "conditionsModule": {"conditions": ["Melanoma"]},
                            }
                        }
                    ],
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    client = FakeClient()
    found = biomcp_search_payload("trial", "BRAF melanoma", 1, 0, client=client)
    record = biomcp_get_payload(
        "trial", "NCT02576665", ["summary", "status"], client=client
    )
    assert found["results"][0]["id"] == "NCT02576665"
    assert record["record"]["status"] == "COMPLETED"
    assert all(item[1].get("method", "GET") == "GET" for item in requested)
    assert all(not item[1].get("body") for item in requested)
    with pytest.raises(ValueError, match="NCT"):
        biomcp_get_payload("trial", "../admin", client=client)
    with pytest.raises(ValueError, match="sections"):
        biomcp_get_payload("trial", "NCT02576665", ["documents"], client=client)
    with pytest.raises(ValueError, match="unique"):
        biomcp_get_payload(
            "trial", "NCT02576665", ["summary", "summary"], client=client
        )


def test_safedep_contract_accepts_only_npm_pypi_purls_and_fixed_services() -> None:
    policy_client = _safedep_client()
    assert policy_client.timeout == 20
    assert policy_client.max_redirects == 0
    assert policy_client.max_response_bytes == 1024 * 1024
    assert set(policy_client.minimum_intervals.values()) == {2.0}
    assert "connect-protocol-version" in policy_client.allowed_request_headers
    requested: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append((url, kwargs))
            if url.startswith("https://registry.npmjs.org/"):
                payload = {
                    "dist-tags": {"latest": "4.17.21"},
                    "versions": {"4.17.20": {}, "4.17.21": {}},
                }
            elif url.startswith("https://pypi.org/pypi/"):
                payload = {
                    "info": {"version": "2.32.3"},
                    "releases": {"2.32.2": [], "2.32.3": []},
                }
            elif url.endswith("GetPackageVersionVulnerabilities"):
                payload = {
                    "vulnerabilities": [
                        {"id": {"type": "CVE", "value": "CVE-2021-23337"}}
                    ]
                }
            else:
                payload = {
                    "status": "MALWARE_ANALYSIS_STATUS_COMPLETED",
                    "verificationRecord": {
                        "isMalware": True,
                        "reason": "fixture marker",
                    },
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    client = FakeClient()
    assert safedep_latest_version_payload("pkg:npm/lodash", client=client)[
        "version"
    ] == "4.17.21"
    assert safedep_available_versions_payload(
        "pkg:pypi/requests", 5, client=client
    )["versions"] == ["2.32.3", "2.32.2"]
    assert safedep_vulnerabilities_payload(
        "pkg:npm/lodash@4.17.20", client=client
    )["count"] == 1
    assert safedep_malware_payload(
        "pkg:npm/safedep-test-pkg@1.0.0", client=client
    )["is_malware"] is True
    connect_calls = [item for item in requested if "community-api.safedep.io" in item[0]]
    assert connect_calls
    assert connect_calls[0][1]["headers"]["Connect-Protocol-Version"] == "1"
    assert b"lodash" in connect_calls[0][1]["body"]
    for invalid in (
        "https://registry.npmjs.org/lodash",
        "pkg:github/owner/repo@1.0.0",
        "pkg:npm/lodash@1.0.0?repository=https://evil.example",
        "pkg:npm/../admin@1.0.0",
    ):
        with pytest.raises(ValueError, match="purl|package name"):
            safedep_latest_version_payload(invalid, client=client)


def test_open_websearch_contract_uses_only_two_fixed_request_engines() -> None:
    policy_client = _open_websearch_client()
    assert policy_client.allowed_hosts == {"cn.bing.com", "html.duckduckgo.com"}
    assert policy_client.max_redirects == 0
    requested: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append((url, kwargs))
            if url.startswith("https://cn.bing.com/search?"):
                body = (
                    b"<rss><channel><item><title>Bing MCP</title>"
                    b"<link>https://example.com/bing</link>"
                    b"<description>Public result</description></item></channel></rss>"
                )
                content_type = "application/rss+xml"
            else:
                body = (
                    b'<a class="result__a" href="https://example.com/ddg">DDG MCP</a>'
                    b'<a class="result__snippet">Strict result</a>'
                )
                content_type = "text/html"
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": content_type},
                body=body,
            )

    result = open_websearch_payload(
        "Model Context Protocol",
        5,
        ["bing", "duckduckgo"],
        client=FakeClient(),
    )
    assert result["mode"] == "request-only"
    assert result["safe_search"] == "strict"
    assert {item["engine"] for item in result["results"]} == {"bing", "duckduckgo"}
    assert requested[0][0].startswith("https://cn.bing.com/search?")
    assert requested[1][0] == "https://html.duckduckgo.com/html"
    assert requested[1][1]["method"] == "POST"
    with pytest.raises(ValueError, match="reviewed engines"):
        open_websearch_payload("query", engines=["bing", "bing"], client=FakeClient())
    with pytest.raises(ValueError, match="limit"):
        open_websearch_payload("query", 11, client=FakeClient())


def test_idea_reality_contract_queries_fixed_public_sources_without_tokens() -> None:
    policy_client = _idea_reality_client()
    assert policy_client.allowed_hosts == {
        "api.github.com",
        "hn.algolia.com",
        "registry.npmjs.org",
        "pypi.org",
    }
    assert policy_client.max_redirects == 0
    requested: list[str] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append(url)
            if url.startswith("https://api.github.com/search/repositories"):
                payload: Any = {
                    "items": [
                        {
                            "full_name": "example/mcp-catalog",
                            "description": "MCP catalog",
                            "html_url": "https://github.com/example/mcp-catalog",
                            "stargazers_count": 12,
                        }
                    ]
                }
            else:
                payload = {
                    "hits": [
                        {
                            "title": "MCP research",
                            "url": "https://example.com/hn",
                            "points": 5,
                            "objectID": "1",
                        }
                    ]
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    result = idea_reality_payload(
        "Secure MCP catalog research",
        "quick",
        client=FakeClient(),
    )
    assert result["sources_used"] == ["github", "hacker_news"]
    assert result["similar_result_count"] == 2
    assert all("producthunt" not in url.lower() for url in requested)
    assert all("token" not in url.lower() for url in requested)
    with pytest.raises(ValueError, match="searchable keyword"):
        idea_reality_payload("with this and that", client=FakeClient())


def test_gitmcp_contract_accepts_only_canonical_repo_slugs_and_bounded_reads() -> None:
    policy_client = _gitmcp_client()
    assert policy_client.allowed_hosts == {"api.github.com"}
    assert policy_client.max_redirects == 0
    requested: list[str] = []

    class FakeClient:
        def request(self, url: str, **kwargs: Any) -> SafeHttpResponse:
            requested.append(url)
            if url.endswith("/repos/octocat/hello-world"):
                payload: Any = {
                    "full_name": "octocat/Hello-World",
                    "default_branch": "master",
                    "description": "Hello fixture",
                    "html_url": "https://github.com/octocat/Hello-World",
                }
            elif "/git/trees/" in url:
                payload = {
                    "truncated": False,
                    "tree": [
                        {"type": "blob", "path": "README.md"},
                        {"type": "blob", "path": "src/client.py"},
                    ],
                }
            else:
                payload = {
                    "encoding": "base64",
                    "path": "README.md",
                    "sha": "a" * 40,
                    "content": base64.b64encode(b"Hello ClientSession documentation").decode(),
                    "html_url": "https://github.com/octocat/Hello-World/blob/master/README.md",
                }
            return SafeHttpResponse(
                url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )

    client = FakeClient()
    cache: dict[str, dict[str, Any]] = {}
    docs = gitmcp_documentation_payload("Octocat/Hello-World", client=client, cache=cache)
    found = gitmcp_search_documentation_payload(
        "octocat/hello-world", "ClientSession", client=client, cache=cache
    )
    code = gitmcp_search_code_payload(
        "octocat/hello-world", "client", client=client, cache=cache
    )
    assert docs["repository"] == "octocat/hello-world"
    assert found["results"][0]["path"] == "README.md"
    assert code["results"][0]["path"] == "src/client.py"
    assert all(url.startswith("https://api.github.com/") for url in requested)
    assert normalize_github_repository("Octocat/Hello-World") == "octocat/hello-world"
    for invalid in (
        "https://github.com/octocat/Hello-World",
        "octocat/Hello-World.git",
        "octocat/Hello-World/issues",
        "../admin/repo",
        "octocat%2fHello-World",
    ):
        with pytest.raises(ValueError, match="canonical GitHub"):
            normalize_github_repository(invalid)
