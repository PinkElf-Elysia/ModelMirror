from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from server.mcp import token_proxy
from server.mcp.catalog import CATALOG_ADAPTERS
from server.mcp.catalog_expansion_v3 import CATALOG_EXPANSION_V3_ADAPTERS
from server.sandbox_sidecar import token_builtin, token_server
from server.sandbox_sidecar.token_contracts import (
    STAGED_TOKEN_ADAPTERS,
    TOKEN_ADAPTERS,
    TOKEN_SCHEMA_SHA256,
    validate_configuration,
)


ROOT = Path(__file__).resolve().parents[2]
GOOGLE_NEWS_ID = "chanmeng666-server-google-news"
NAVER_ID = "isnow890-naver-search-mcp"
WAVE26B_IDS = {GOOGLE_NEWS_ID, NAVER_ID}


class FakeHttpResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status


class RecordingHttpClient:
    def __init__(self, responses: list[FakeHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, url: str, **kwargs: object) -> FakeHttpResponse:
        self.calls.append((url, dict(kwargs)))
        return self.responses.pop(0)


def _tool_text(result: object) -> str:
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        return "".join(str(getattr(item, "text", "")) for item in result)
    return str(result)


def _digest(tools: list[object]) -> str:
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    return hashlib.sha256(
        json.dumps(
            reviewed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_wave26b_contracts_are_staged_default_deny_and_catalog_planned() -> None:
    assert WAVE26B_IDS.issubset(STAGED_TOKEN_ADAPTERS)
    assert WAVE26B_IDS.issubset(TOKEN_ADAPTERS)
    assert WAVE26B_IDS.issubset(token_proxy.ALLOWED_ADAPTERS)
    assert not (WAVE26B_IDS & set(token_server.ALLOWED_ADAPTERS))

    expansion = {
        item.project_id: item
        for item in CATALOG_EXPANSION_V3_ADAPTERS
        if item.project_id in WAVE26B_IDS
    }
    assert set(expansion) == WAVE26B_IDS
    for adapter_id in WAVE26B_IDS:
        assert expansion[adapter_id].availability == "planned"
        assert expansion[adapter_id].decision_reason_code == (
            "planned-wave26-token-readonly-preflight"
        )
        manifest = CATALOG_ADAPTERS[adapter_id]
        assert manifest.availability == "planned"
        assert manifest.server_command == ()
        assert manifest.tool_policies == {}
        assert manifest.filesystem_policy == "planned:no-runtime"


def test_wave26b_production_compose_does_not_allow_staged_ids() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    token_block = compose[
        compose.index("  mcp-token:\n") : compose.index("  mcp-registry:\n")
    ]
    for adapter_id in WAVE26B_IDS:
        assert adapter_id not in token_block


@pytest.mark.asyncio
async def test_wave26b_tool_names_and_strict_schema_digests_are_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERP_API_KEY", "schema-serp-key")
    google = token_builtin.build_google_news_readonly()
    google_tools = await google.list_tools()
    assert {tool.name for tool in google_tools} == {"google_news_search"}
    assert _digest(google_tools) == TOKEN_SCHEMA_SHA256[GOOGLE_NEWS_ID]

    monkeypatch.setenv("NAVER_CLIENT_ID", "schema-client-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "schema-client-secret")
    naver = token_builtin.build_naver_search_readonly()
    naver_tools = await naver.list_tools()
    assert {tool.name for tool in naver_tools} == {
        "search_webkr",
        "search_news",
        "search_blog",
    }
    assert _digest(naver_tools) == TOKEN_SCHEMA_SHA256[NAVER_ID]
    for tool in [*google_tools, *naver_tools]:
        assert tool.inputSchema.get("additionalProperties") is False


def test_wave26b_configuration_requires_exact_credential_slots() -> None:
    _, credentials, settings = validate_configuration(
        GOOGLE_NEWS_ID,
        {"credentials": {"api_key": "secret"}, "settings": {}},
    )
    assert credentials == {"api_key": "secret"}
    assert settings == {}

    _, credentials, settings = validate_configuration(
        NAVER_ID,
        {
            "credentials": {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            "settings": {},
        },
    )
    assert credentials == {
        "client_id": "client-id",
        "client_secret": "client-secret",
    }
    assert settings == {}
    with pytest.raises(ValueError, match="configuration_contract_mismatch"):
        validate_configuration(
            NAVER_ID,
            {
                "credentials": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "host": "attacker.invalid",
                },
                "settings": {},
            },
        )


@pytest.mark.asyncio
async def test_google_news_facade_uses_fixed_serpapi_contract_and_redacts_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingHttpClient(
        [
            FakeHttpResponse(
                {
                    "news_results": [
                        {
                            "title": "<b>Safe headline</b>",
                            "source": {"name": "Example", "authors": ["A"]},
                            "date": "1 hour ago",
                            "snippet": "&lt;em&gt;Public summary&lt;/em&gt;",
                            "link": "https://example.com/story",
                        },
                        {
                            "title": "Credential link is omitted",
                            "source": {"name": "Example"},
                            "link": "https://example.com/story?token=provider-secret",
                        },
                    ]
                }
            )
        ]
    )
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    monkeypatch.setenv("SERP_API_KEY", "private-serp-key")
    mcp = token_builtin.build_google_news_readonly()

    result = await mcp.call_tool(
        "google_news_search",
        {"q": "model context protocol", "gl": "us", "hl": "en", "max_results": 2},
    )
    request_url = client.calls[0][0]
    assert urlsplit(request_url).hostname == "serpapi.com"
    query = parse_qs(urlsplit(request_url).query)
    assert query == {
        "engine": ["google_news"],
        "q": ["model context protocol"],
        "gl": ["us"],
        "hl": ["en"],
        "api_key": ["private-serp-key"],
    }
    text = _tool_text(result)
    assert "Safe headline" in text
    assert "<b>" not in text
    assert "<em>" not in text
    assert "private-serp-key" not in text
    assert "provider-secret" not in text
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await mcp.call_tool(
            "google_news_search",
            {"q": "safe", "topic_token": "unreviewed"},
        )


@pytest.mark.asyncio
async def test_naver_facade_uses_classic_fixed_paths_and_never_returns_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "total": 1,
        "items": [
            {
                "title": "<b>Safe result</b>",
                "description": "<i>Public description</i>",
                "link": "https://example.com/result",
                "originallink": "https://example.org/original",
                "bloggername": "Example blog",
                "postdate": "20260812",
                "pubDate": "Wed, 12 Aug 2026 00:00:00 +0900",
            }
        ],
    }
    client = RecordingHttpClient([FakeHttpResponse(payload) for _ in range(3)])
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    monkeypatch.setenv("NAVER_CLIENT_ID", "private-client-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "private-client-secret")
    mcp = token_builtin.build_naver_search_readonly()

    results = []
    for tool_name in ("search_webkr", "search_news", "search_blog"):
        results.append(
            await mcp.call_tool(
                tool_name,
                {"query": "safe query", "display": 5, "start": 1, "sort": "sim"},
            )
        )
    assert [urlsplit(item[0]).path for item in client.calls] == [
        "/v1/search/webkr",
        "/v1/search/news",
        "/v1/search/blog",
    ]
    for _, kwargs in client.calls:
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        assert headers == {
            "X-Naver-Client-Id": "private-client-id",
            "X-Naver-Client-Secret": "private-client-secret",
        }
    text = "".join(_tool_text(result) for result in results)
    assert "Safe result" in text
    assert "<b>" not in text
    assert "private-client-id" not in text
    assert "private-client-secret" not in text
    for denied_tool in ("search_image", "search_local", "datalab_search"):
        with pytest.raises(Exception):
            await mcp.call_tool(denied_tool, {"query": "safe"})


@pytest.mark.asyncio
async def test_wave26b_provider_429_is_explicit_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingHttpClient([FakeHttpResponse({"error": "rate limited"}, status=429)])
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    monkeypatch.setenv("SERP_API_KEY", "private-serp-key")
    mcp = token_builtin.build_google_news_readonly()
    with pytest.raises(Exception, match="HTTP 429"):
        await mcp.call_tool("google_news_search", {"q": "safe"})
    assert len(client.calls) == 1


def test_wave26b_notices_freeze_upstream_commits_without_bundling_packages() -> None:
    notices = (ROOT / "server/sandbox_sidecar/THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "server/sandbox_sidecar/Dockerfile.token").read_text(
        encoding="utf-8"
    )
    assert "5ed14341ff6ef290e13bafa08abc12157bbe23a3" in notices
    assert "d7c7c58cab0de2692336b710727f1ee123270e6c" in notices
    assert "server-google-news" not in dockerfile
    assert "naver-search-mcp" not in dockerfile
    assert "smoke_token_gateway.py" in dockerfile
