from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from server.mcp import token_proxy
from server.mcp.catalog import (
    CATALOG_ADAPTERS,
    WAVE_FOUR_ADAPTERS,
    WAVE_FOURTEEN_TOKEN_ADAPTERS,
    WAVE_FIFTEEN_TOKEN_ADAPTERS,
    WAVE_NINE_READY_ADAPTERS,
    WAVE_THIRTEEN_TOKEN_ADAPTERS,
)
from server.sandbox_sidecar import token_builtin, token_server
from server.sandbox_sidecar.safe_http import NetworkPolicyError
from server.sandbox_sidecar.token_contracts import (
    STAGED_TOKEN_ADAPTERS,
    TOKEN_ADAPTERS,
    TOKEN_SCHEMA_SHA256,
    validate_configuration,
)


ROOT = Path(__file__).resolve().parents[2]


class MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, value: bytes) -> None:
        self.data.extend(value)

    async def drain(self) -> None:
        return None


class FakeHttpResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status


class RecordingHttpClient:
    def __init__(self, responses: list[FakeHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, url: str, **kwargs: object) -> FakeHttpResponse:
        self.calls.append((url, dict(kwargs)))
        return self.responses.pop(0)


def reader_for(*messages: dict[str, object]) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for message in messages:
        reader.feed_data(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    reader.feed_eof()
    return reader


def test_runtime_contracts_match_catalog_and_never_include_snyk() -> None:
    expected = (
        set(WAVE_FOUR_ADAPTERS)
        | set(WAVE_NINE_READY_ADAPTERS)
        | set(WAVE_THIRTEEN_TOKEN_ADAPTERS)
        | set(WAVE_FOURTEEN_TOKEN_ADAPTERS)
        | set(WAVE_FIFTEEN_TOKEN_ADAPTERS)
    )
    assert set(TOKEN_ADAPTERS) == expected | set(STAGED_TOKEN_ADAPTERS)
    assert set(TOKEN_SCHEMA_SHA256) == set(TOKEN_ADAPTERS)
    assert set(token_proxy.ALLOWED_ADAPTERS) == expected | set(STAGED_TOKEN_ADAPTERS)
    assert "snyk-mcp" not in TOKEN_ADAPTERS
    assert "vectorize-io-vectorize-mcp-server" not in TOKEN_ADAPTERS
    assert "vectorize-io-vectorize-mcp-server" not in TOKEN_SCHEMA_SHA256
    assert "vectorize-io-vectorize-mcp-server" not in token_proxy.ALLOWED_ADAPTERS
    assert "vectorize-io-vectorize-mcp-server" not in token_builtin.BUILDERS
    for project_id in expected:
        contract = TOKEN_ADAPTERS[project_id]
        assert contract.tools == frozenset(CATALOG_ADAPTERS[project_id].tool_policies)
        assert contract.command
        assert all("npx" not in item for item in contract.command)

    brave = TOKEN_ADAPTERS["brave-brave-search-mcp-server"]
    assert brave.command == (
        "/opt/modelmirror/brave/node_modules/.bin/brave-search-mcp-server",
        "--transport",
        "stdio",
        "--enabled-tools",
        "brave_web_search",
        "brave_local_search",
    )
    assert brave.tools == frozenset({"brave_web_search", "brave_local_search"})
    assert brave.allowed_hosts == frozenset({"api.search.brave.com"})
    assert brave.credential_environment == (("api_key", "BRAVE_API_KEY"),)

    arxiv = TOKEN_ADAPTERS["blazickjp-arxiv-mcp-server"]
    assert arxiv.tools == frozenset({"search_papers", "get_abstract"})
    assert arxiv.allowed_hosts == frozenset({"export.arxiv.org"})
    assert arxiv.credential_environment == ()
    assert arxiv.builtin is True

    kagi = TOKEN_ADAPTERS["kagisearch-kagimcp"]
    assert kagi.tools == frozenset({"kagi_search_fetch", "kagi_extract"})
    assert kagi.allowed_hosts == frozenset({"kagi.com"})
    assert kagi.credential_environment == (("api_key", "KAGI_API_KEY"),)
    assert kagi.builtin is True

    search1 = TOKEN_ADAPTERS["fatwang2-search1api-mcp"]
    assert search1.tools == frozenset({"search", "news", "trending"})
    assert search1.allowed_hosts == frozenset({"api.search1api.com"})
    assert search1.credential_environment == (("api_key", "SEARCH1API_KEY"),)
    assert search1.builtin is True

    tennis = TOKEN_ADAPTERS["livetennisapi-livetennisapi-mcp"]
    assert tennis.tools == frozenset(
        {
            "get_live_matches",
            "get_upcoming_matches",
            "get_match_score",
            "search_players",
            "get_player",
            "get_fixtures",
            "search_tournaments",
            "get_tournament",
        }
    )
    assert tennis.allowed_hosts == frozenset({"api.livetennisapi.com"})
    assert tennis.credential_environment == (("api_key", "LIVE_TENNIS_API_KEY"),)
    assert tennis.builtin is True

    assert STAGED_TOKEN_ADAPTERS == {
        "cablate-mcp-google-map",
        "comet-ml-opik-mcp",
        "keboola-keboola-mcp-server",
    }
    assert not (set(token_server.ALLOWED_ADAPTERS) & set(STAGED_TOKEN_ADAPTERS))

    google = TOKEN_ADAPTERS["cablate-mcp-google-map"]
    assert google.tools == frozenset({"maps_search_places", "maps_place_details"})
    assert google.allowed_hosts == frozenset({"places.googleapis.com"})
    assert google.credential_environment == (("api_key", "GOOGLE_MAPS_API_KEY"),)

    opik = TOKEN_ADAPTERS["comet-ml-opik-mcp"]
    assert opik.tools == frozenset({"list", "read"})
    assert opik.allowed_hosts == frozenset({"www.comet.com"})
    assert opik.setting_environment == (("workspace", "OPIK_WORKSPACE"),)

    keboola = TOKEN_ADAPTERS["keboola-keboola-mcp-server"]
    assert keboola.tools == frozenset({"get_project_info", "get_buckets", "get_tables"})
    assert keboola.allowed_hosts == frozenset({"connection.keboola.com"})


def test_compose_token_allowlist_contains_every_non_registry_contract() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    token_block = source[
        source.index("  mcp-token:\n") : source.index("  mcp-registry:\n")
    ]
    allowlist_line = next(
        line
        for line in token_block.splitlines()
        if "MCP_TOKEN_ALLOWED_ADAPTERS:" in line
    )
    configured = {
        item.strip()
        for item in allowlist_line.split(":", 1)[1].split(",")
        if item.strip()
    }
    expected = (
        set(TOKEN_ADAPTERS)
        - set(WAVE_NINE_READY_ADAPTERS)
        - set(STAGED_TOKEN_ADAPTERS)
    )
    assert configured == expected
    assert "blazickjp-arxiv-mcp-server" in configured
    assert "brave-brave-search-mcp-server" in configured
    assert "kagisearch-kagimcp" in configured
    assert "fatwang2-search1api-mcp" in configured
    assert "livetennisapi-livetennisapi-mcp" in configured
    assert not (configured & set(STAGED_TOKEN_ADAPTERS))


def test_official_brave_runtime_is_independently_integrity_locked() -> None:
    lock = json.loads(
        (ROOT / "server" / "sandbox_sidecar" / "brave_runtime" / "package-lock.json")
        .read_text(encoding="utf-8")
    )
    package = lock["packages"]["node_modules/@brave/brave-search-mcp-server"]
    assert package["version"] == "2.1.0"
    assert package["license"] == "MIT"
    assert package["integrity"] == (
        "sha512-QIQOwbrtv8QcNYQeI9NOgXdqMmhjaOYKwQl4cbpQNIqsEb/"
        "spjDKYjzvyl5NJqhEpZjmKRIo3H+0I+oYKOGjNA=="
    )
    dockerfile = (
        ROOT / "server" / "sandbox_sidecar" / "Dockerfile.token"
    ).read_text(encoding="utf-8")
    assert "AS brave_packages" in dockerfile
    assert "brave_runtime/package.json brave_runtime/package-lock.json" in dockerfile
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile


def test_configuration_contract_rejects_missing_and_extra_fields() -> None:
    contract, credentials, settings = validate_configuration(
        "grafana-mcp",
        {
            "credentials": {"service_token": "secret"},
            "settings": {"stack_slug": "my-stack"},
        },
    )
    assert contract.builtin is True
    assert credentials == {"service_token": "secret"}
    assert settings == {"stack_slug": "my-stack"}

    terraform, credentials, settings = validate_configuration(
        "terraform-mcp",
        {"credentials": {}, "settings": {}},
    )
    assert terraform.builtin is True
    assert terraform.allowed_hosts == frozenset({"registry.terraform.io"})
    assert credentials == {}
    assert settings == {}

    arxiv, credentials, settings = validate_configuration(
        "blazickjp-arxiv-mcp-server",
        {"credentials": {}, "settings": {}},
    )
    assert arxiv.builtin is True
    assert credentials == {}
    assert settings == {}

    kagi, credentials, settings = validate_configuration(
        "kagisearch-kagimcp",
        {"credentials": {"api_key": "secret"}, "settings": {}},
    )
    assert kagi.builtin is True
    assert credentials == {"api_key": "secret"}
    assert settings == {}

    search1, credentials, settings = validate_configuration(
        "fatwang2-search1api-mcp",
        {"credentials": {"api_key": "secret"}, "settings": {}},
    )
    assert search1.builtin is True
    assert credentials == {"api_key": "secret"}
    assert settings == {}

    tennis, credentials, settings = validate_configuration(
        "livetennisapi-livetennisapi-mcp",
        {"credentials": {"api_key": "secret"}, "settings": {}},
    )
    assert tennis.builtin is True
    assert credentials == {"api_key": "secret"}
    assert settings == {}

    with pytest.raises(ValueError, match="mcp_adapter_denied"):
        validate_configuration(
            "vectorize-io-vectorize-mcp-server",
            {
                "credentials": {"api_token": "secret"},
                "settings": {
                    "organization_id": "org_123",
                    "pipeline_id": "pipe-456",
                },
            },
        )

    _, credentials, settings = validate_configuration(
        "comet-ml-opik-mcp",
        {
            "credentials": {"api_key": "secret"},
            "settings": {"workspace": "team.workspace"},
        },
    )
    assert credentials == {"api_key": "secret"}
    assert settings == {"workspace": "team.workspace"}

    with pytest.raises(ValueError, match="configuration_contract_mismatch"):
        validate_configuration(
            "grafana-mcp",
            {
                "credentials": {"service_token": "secret", "admin": "secret"},
                "settings": {"stack_slug": "my-stack"},
            },
        )
    with pytest.raises(ValueError, match="invalid_setting"):
        validate_configuration(
            "pinecone-assistant-mcp",
            {
                "credentials": {"api_key": "secret"},
                "settings": {"assistant_host": "localhost", "assistant_name": "docs"},
            },
        )


def test_proxy_configuration_is_removed_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"settings": {}, "credentials": {"api_key": "private-value"}}
    monkeypatch.setenv(
        "MCP_TOKEN_HANDSHAKE_B64",
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode(),
    )
    assert token_proxy._load_configuration() == payload
    assert "MCP_TOKEN_HANDSHAKE_B64" not in token_proxy.os.environ


def test_synthetic_dns_flag_is_normalized_for_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    contract = TOKEN_ADAPTERS["terraform-mcp"]
    monkeypatch.setenv("MCP_PUBLIC_ALLOW_SYNTHETIC_DNS", "YES")
    enabled = token_server._child_environment(contract, {}, {}, tmp_path)
    assert enabled["MCP_PUBLIC_ALLOW_SYNTHETIC_DNS"] == "true"

    monkeypatch.setenv("MCP_PUBLIC_ALLOW_SYNTHETIC_DNS", "unexpected")
    disabled = token_server._child_environment(contract, {}, {}, tmp_path)
    assert disabled["MCP_PUBLIC_ALLOW_SYNTHETIC_DNS"] == "false"


@pytest.mark.asyncio
async def test_gateway_rejects_unlisted_tool_before_child() -> None:
    contract = TOKEN_ADAPTERS["agentql-mcp"]
    child = MemoryWriter()
    client = MemoryWriter()
    await token_server._client_to_child(  # type: ignore[arg-type]
        reader_for(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_everything", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "extract-web-data", "arguments": {"prompt": "title"}}},
        ),
        child,
        client,
        contract,
        set(),
        asyncio.Lock(),
    )
    assert b"delete_everything" not in child.data
    assert b"extract-web-data" in child.data
    assert "未通过只读策略审核" in client.data.decode("utf-8")


@pytest.mark.asyncio
async def test_gateway_filters_tool_discovery() -> None:
    contract = TOKEN_ADAPTERS["agentql-mcp"]
    output = MemoryWriter()
    await token_server._child_to_client(  # type: ignore[arg-type]
        reader_for(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {
                    "tools": [
                        {"name": "extract-web-data", "inputSchema": {}},
                        {"name": "unsafe-write", "inputSchema": {}},
                    ]
                },
            }
        ),
        output,
        contract,
        {7},
        asyncio.Lock(),
    )
    payload = json.loads(bytes(output.data).decode("utf-8"))
    assert [item["name"] for item in payload["result"]["tools"]] == ["extract-web-data"]


def test_gateway_url_preflight_requires_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_server, "resolve_public_addresses", lambda host, port: ("93.184.216.34",))
    token_server._validate_argument_targets({"url": "https://example.com/page"})
    with pytest.raises(NetworkPolicyError):
        token_server._validate_argument_targets({"url": "http://example.com/page"})
    with pytest.raises(NetworkPolicyError):
        token_server._validate_argument_targets({"url": "file:///etc/passwd"})


def test_kagi_extract_url_rejects_credentials_and_non_https() -> None:
    assert token_builtin._public_extract_url(
        "https://example.com/public?page=2"
    ) == "https://example.com/public?page=2"
    for url in (
        "http://example.com/public",
        "https://user@example.com/public",
        "https://example.com/public?token=secret",
        "https://example.com/public?X-Amz-Signature=secret",
        "https://example.com/public?%2561pi_key=secret",
        "https://example.com/public?redirect=https%253A%252F%252Fother.example%252F%253Foauth_token%253Dsecret",
    ):
        with pytest.raises((ValueError, NetworkPolicyError)):
            token_builtin._public_extract_url(url)


@pytest.mark.asyncio
async def test_wave_seventeen_google_map_facade_uses_fixed_places_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingHttpClient(
        [
            FakeHttpResponse(json.dumps({"places": [{"id": "place-1"}]}).encode()),
            FakeHttpResponse(json.dumps({"id": "place-1"}).encode()),
        ]
    )
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "private-google-key")
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_google_map_readonly()

    await mcp.call_tool(
        "maps_search_places",
        {
            "query": "coffee near Phoenix",
            "locationBias": {"latitude": 33.45, "longitude": -112.07, "radius": 5000},
            "openNow": True,
            "minRating": 4.0,
            "includedType": "cafe",
        },
    )
    await mcp.call_tool("maps_place_details", {"placeId": "place-1"})

    assert [item[0] for item in client.calls] == [
        "https://places.googleapis.com/v1/places:searchText",
        "https://places.googleapis.com/v1/places/place-1",
    ]
    search = client.calls[0][1]
    assert search["method"] == "POST"
    body = json.loads(search["body"])
    assert body["maxResultCount"] == 10
    assert body["textQuery"] == "coffee near Phoenix"
    headers = search["headers"]
    assert headers["X-Goog-Api-Key"] == "private-google-key"
    assert "reviews" not in headers["X-Goog-FieldMask"]
    assert "photos" not in headers["X-Goog-FieldMask"]


@pytest.mark.asyncio
async def test_wave_seventeen_opik_facade_is_bounded_list_and_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingHttpClient(
        [
            FakeHttpResponse(b'{"content":[],"total":0}'),
            FakeHttpResponse(b'{"id":"project-1"}'),
        ]
    )
    monkeypatch.setenv("OPIK_API_KEY", "private-opik-key")
    monkeypatch.setenv("OPIK_WORKSPACE", "team.workspace")
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_opik_readonly()

    await mcp.call_tool("list", {"entity_type": "project", "name": "demo", "size": 20})
    await mcp.call_tool("read", {"entity_type": "project", "id": "project-1"})

    assert client.calls[0][0] == (
        "https://www.comet.com/opik/api/v1/private/projects?page=1&size=20&name=demo"
    )
    assert client.calls[1][0] == (
        "https://www.comet.com/opik/api/v1/private/projects/project-1"
    )
    for _, request in client.calls:
        assert request["headers"] == {
            "Authorization": "private-opik-key",
            "Comet-Workspace": "team.workspace",
        }
    assert TOKEN_ADAPTERS["comet-ml-opik-mcp"].tools == {"list", "read"}


@pytest.mark.asyncio
async def test_wave_seventeen_keboola_facade_reads_fixed_us_storage_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingHttpClient(
        [
            FakeHttpResponse(b'{"id":"token-1","owner":{"id":"1","name":"Demo"}}'),
            FakeHttpResponse(b'[{"id":"in.c-demo"}]'),
            FakeHttpResponse(b'[{"id":"in.c-demo.table"}]'),
        ]
    )
    monkeypatch.setenv("KEBOOLA_STORAGE_TOKEN", "private-storage-token")
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_keboola_metadata_readonly()

    await mcp.call_tool("get_project_info", {})
    await mcp.call_tool("get_buckets", {})
    await mcp.call_tool("get_tables", {"bucket_ids": ["in.c-demo"]})

    assert [item[0] for item in client.calls] == [
        "https://connection.keboola.com/v2/storage/tokens/verify",
        "https://connection.keboola.com/v2/storage/branch/default/buckets",
        "https://connection.keboola.com/v2/storage/branch/default/buckets/in.c-demo/tables",
    ]
    assert all(
        request["headers"] == {"X-StorageAPI-Token": "private-storage-token"}
        for _, request in client.calls
    )


@pytest.mark.asyncio
async def test_kagi_native_facade_uses_fixed_api_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingHttpClient(
        [
            FakeHttpResponse(b"# bounded result"),
            FakeHttpResponse(json.dumps({"data": [{"markdown": "page body"}]}).encode()),
        ]
    )
    monkeypatch.setenv("KAGI_API_KEY", "private-kagi-key")
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_kagi_official()

    await mcp.call_tool(
        "kagi_search_fetch",
        {"query": "model context protocol", "workflow": "news", "limit": 5},
    )
    await mcp.call_tool(
        "kagi_extract",
        {"url": "https://example.com/public?page=2"},
    )

    assert [item[0] for item in client.calls] == [
        "https://kagi.com/api/v1/search",
        "https://kagi.com/api/v1/extract",
    ]
    search = client.calls[0][1]
    assert search["method"] == "POST"
    assert json.loads(search["body"]) == {
        "query": "model context protocol",
        "workflow": "news",
        "format": "markdown",
        "limit": 5,
    }
    headers = search["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer private-kagi-key"
    extract_body = client.calls[1][1]["body"]
    assert isinstance(extract_body, bytes)
    assert b"private-kagi-key" not in extract_body


@pytest.mark.asyncio
async def test_arxiv_native_facade_is_metadata_only_and_encodes_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom'>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>  Safe   Paper </title>
    <summary> External abstract </summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice</name></author>
    <arxiv:primary_category term='cs.AI'/>
  </entry>
</feed>"""
    client = RecordingHttpClient([FakeHttpResponse(feed), FakeHttpResponse(feed)])
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_arxiv_readonly()

    await mcp.call_tool(
        "search_papers",
        {
            "query": 'ti:"agents"&max_results=999',
            "max_results": 5,
            "categories": ["cs.AI"],
            "sort_by": "date",
        },
    )
    await mcp.call_tool("get_abstract", {"paper_id": "2401.12345v2"})

    assert len(client.calls) == 2
    search_url = client.calls[0][0]
    assert search_url.startswith("https://export.arxiv.org/api/query?")
    assert "%26max_results%3D999" in search_url
    assert "max_results=5" in search_url
    assert "sortBy=submittedDate" in search_url
    assert client.calls[1][0].endswith("id_list=2401.12345v2&max_results=1")
    parsed = token_builtin._parse_arxiv_feed(feed.decode())
    assert parsed == [
        {
            "id": "2401.12345",
            "title": "Safe Paper",
            "authors": ["Alice"],
            "abstract": "[EXTERNAL CONTENT] External abstract",
            "categories": ["cs.AI"],
            "published": "2024-01-01T00:00:00Z",
            "pdf_url": "https://arxiv.org/pdf/2401.12345.pdf",
            "resource_uri": "arxiv://2401.12345",
        }
    ]

    for invalid in ("not-an-id", "2401.12345&max_results=99", "file:///paper"):
        with pytest.raises(ValueError):
            token_builtin._arxiv_paper_id(invalid)


@pytest.mark.asyncio
async def test_search1api_native_facade_forces_discovery_only_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "results": [
            {
                "title": "External title",
                "link": "https://example.com/result",
                "snippet": "External snippet",
                "content": "must never be returned",
            }
        ]
    }
    client = RecordingHttpClient(
        [FakeHttpResponse(json.dumps(response).encode()) for _ in range(3)]
    )
    monkeypatch.setenv("SEARCH1API_KEY", "private-search1-key")
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_search1api_readonly()

    await mcp.call_tool(
        "search",
        {
            "query": "model context protocol",
            "search_service": "google",
            "max_results": 5,
            "language": "en",
        },
    )
    await mcp.call_tool(
        "news",
        {
            "query": "model context protocol",
            "search_service": "hackernews",
            "max_results": 4,
            "time_range": "month",
        },
    )
    await mcp.call_tool(
        "trending",
        {"search_service": "github", "max_results": 3},
    )

    assert [item[0] for item in client.calls] == [
        "https://api.search1api.com/search",
        "https://api.search1api.com/news",
        "https://api.search1api.com/trending",
    ]
    search_body = json.loads(client.calls[0][1]["body"])
    news_body = json.loads(client.calls[1][1]["body"])
    assert search_body["crawl_results"] == 0
    assert news_body["crawl_results"] == 0
    assert "include_sites" not in search_body
    assert "exclude_sites" not in search_body
    assert "private-search1-key" not in json.dumps(search_body)
    projected = token_builtin._search1_results(response, 5)
    assert projected == [
        {
            "title": "[EXTERNAL CONTENT] External title",
            "snippet": "[EXTERNAL CONTENT] External snippet",
            "url": "https://example.com/result",
        }
    ]
    for invalid_service in ("crawl", "sitemap", "custom-provider"):
        with pytest.raises(ValueError):
            token_builtin._choice(
                invalid_service,
                "search_service",
                token_builtin._SEARCH1_SEARCH_SERVICES,
            )


@pytest.mark.asyncio
async def test_live_tennis_native_facade_uses_only_free_projected_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match = {
        "id": 42,
        "status": "live",
        "tournament": "Safe Open",
        "players": {"p1": {"id": 1, "name": "A"}, "p2": {"id": 2, "name": "B"}},
        "score": {
            "sets": [1, 0],
            "games": [[6, 2], [4, 1]],
            "points": ["15", "0"],
            "server": 1,
            "is_tiebreak": False,
            "timestamp": "2026-08-09T00:00:00Z",
            "win_probability_p1": 0.99,
            "danger": 0.88,
        },
        "market": {"prices": [1, 2]},
        "analysis": {"thesis": "paid"},
    }
    score = dict(match["score"])
    player = {"id": 1, "name": "A", "ranking": 3, "stats": {"ratings": "drop"}}
    fixture = {"id": 99, "event_date": "2026-08-10", "player1_name": "A", "player2_name": "B"}
    tournament = {"id": "safe-open-atp", "name": "Safe Open", "tour": "atp", "indoor": False}
    responses = [
        {"data": [match], "meta": {"count": 1}},
        {"data": [match], "meta": {"count": 1}},
        score,
        {"data": [player], "meta": {"count": 1}},
        player,
        {"data": [fixture], "meta": {"count": 1}},
        {"data": [tournament], "meta": {"count": 1}},
        tournament,
    ]
    client = RecordingHttpClient(
        [FakeHttpResponse(json.dumps(item).encode()) for item in responses]
    )
    monkeypatch.setenv("LIVE_TENNIS_API_KEY", "private-tennis-key")
    monkeypatch.setattr(token_builtin, "SafeHttpClient", lambda **_: client)
    mcp = token_builtin.build_livetennisapi_readonly()

    await mcp.call_tool("get_live_matches", {"tour": "atp", "limit": 5})
    await mcp.call_tool("get_upcoming_matches", {"limit": 5})
    await mcp.call_tool("get_match_score", {"match_id": 42})
    await mcp.call_tool("search_players", {"query": "A", "limit": 5})
    await mcp.call_tool("get_player", {"player_id": 1})
    await mcp.call_tool("get_fixtures", {"limit": 5})
    await mcp.call_tool("search_tournaments", {"query": "Safe", "limit": 5})
    await mcp.call_tool("get_tournament", {"tournament_id": "safe-open-atp"})

    assert [item[0] for item in client.calls] == [
        "https://api.livetennisapi.com/api/public/v1/matches?status=live&limit=5&offset=0&tour=atp",
        "https://api.livetennisapi.com/api/public/v1/matches?status=upcoming&limit=5&offset=0",
        "https://api.livetennisapi.com/api/public/v1/matches/42/score",
        "https://api.livetennisapi.com/api/public/v1/players?search=A&limit=5&offset=0",
        "https://api.livetennisapi.com/api/public/v1/players/1",
        "https://api.livetennisapi.com/api/public/v1/fixtures?limit=5&offset=0",
        "https://api.livetennisapi.com/api/public/v1/tournaments?limit=5&offset=0&search=Safe",
        "https://api.livetennisapi.com/api/public/v1/tournaments/safe-open-atp",
    ]
    projected_score = token_builtin._project_tennis_score(score)
    assert projected_score is not None
    assert "win_probability_p1" not in projected_score
    assert "danger" not in projected_score
    projected_match = token_builtin._project_tennis_match(match)
    assert projected_match is not None
    assert "market" not in projected_match
    assert "analysis" not in projected_match
    projected_player = token_builtin._project_tennis_player(player)
    assert projected_player is not None
    assert "stats" not in projected_player
    with pytest.raises(ValueError):
        token_builtin._tennis_tour("completed")
