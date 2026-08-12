"""Small reviewed compatibility adapters for fixed read-only providers."""

from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .safe_http import SafeHttpClient


MAX_RESULT_BYTES = 256 * 1024
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _required_environment(name: str, maximum: int = 20_000) -> str:
    value = os.environ.pop(name, "").strip()
    if not value or len(value) > maximum:
        raise RuntimeError("适配器缺少服务端配置。")
    return value


def _text(value: object, name: str, maximum: int = 2_000) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{name}不能为空且不能超过 {maximum} 个字符。")
    return clean


def _json(response: object, provider: str) -> Any:
    status = int(getattr(response, "status", 500))
    if not 200 <= status < 300:
        raise ValueError(f"{provider} 返回 HTTP {status}。")
    try:
        payload = json.loads(getattr(response, "body").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{provider} 返回了无效 JSON。") from exc
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise ValueError("工具返回超过 256 KiB 上限。")
    return payload


def _body(value: object) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 128 * 1024:
        raise ValueError("请求参数超过 128 KiB 上限。")
    return encoded


def _opaque_identifier(value: object, name: str, maximum: int = 240) -> str:
    clean = _text(value, name, maximum)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", clean):
        raise ValueError(f"{name} contains unsupported characters.")
    return clean


def _bounded_sequence(value: object, name: str, maximum: int = 20) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded list.")
    return [_opaque_identifier(item, name) for item in value]


def _bounded_text_response(response: object, provider: str) -> str:
    status = int(getattr(response, "status", 500))
    if not 200 <= status < 300:
        raise ValueError(f"{provider} returned HTTP {status}.")
    try:
        text = getattr(response, "body").decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{provider} returned invalid UTF-8.") from exc
    if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("Tool output exceeds the 256 KiB limit.")
    return text


_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "oauth_token",
        "token",
        "signature",
        "sig",
        "key",
        "credential",
    }
)


def _public_extract_url(value: object) -> str:
    from .safe_http import validate_public_https_url

    clean = _text(value, "url", 16_384)
    normalized, _, _, _ = validate_public_https_url(clean)
    decoded = clean
    for _ in range(2):
        decoded = unquote(decoded)
    query = decoded.split("?", 1)[1].split("#", 1)[0] if "?" in decoded else ""
    query_keys = [item[0] for item in parse_qsl(query, keep_blank_values=True)]
    query_keys.extend(
        match.group(1)
        for match in re.finditer(r"(?:^|[?&])([^=?&#]+)=", query)
    )
    for raw_key in query_keys:
        key = re.sub(r"[^a-z0-9]+", "_", raw_key.lower()).strip("_")
        compact = key.replace("_", "")
        if (
            key in _SENSITIVE_QUERY_KEYS
            or compact in {item.replace("_", "") for item in _SENSITIVE_QUERY_KEYS}
            or key.startswith("x_amz_")
            or key.startswith("x_goog_")
            or "oauth" in key
        ):
            raise ValueError("url must not contain credential-like query parameters.")
    return normalized


def build_axiom() -> FastMCP:
    token = _required_environment("AXIOM_TOKEN")
    organization_id = _required_environment("AXIOM_ORG_ID", 120)
    client = SafeHttpClient(
        allowed_hosts=frozenset({"api.axiom.co"}),
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"authorization", "x-axiom-org-id"}),
    )
    headers = {"Authorization": f"Bearer {token}", "X-Axiom-Org-Id": organization_id}
    mcp = FastMCP("ModelMirror Axiom Read Only")

    def get(path: str) -> Any:
        return _json(client.request(f"https://api.axiom.co{path}", headers=headers), "Axiom")

    @mcp.tool(annotations=READ_ONLY)
    def listDatasets() -> Any:
        """列出当前组织内可读取的数据集。"""
        return get("/v1/datasets")

    @mcp.tool(annotations=READ_ONLY)
    def getDatasetSchema(dataset_name: str) -> Any:
        """读取指定数据集字段信息。"""
        name = quote(_text(dataset_name, "dataset_name", 200), safe="")
        return get(f"/v1/datasets/{name}/fields")

    @mcp.tool(annotations=READ_ONLY)
    def queryApl(apl: str, start_time: str = "now-1h", end_time: str = "now") -> Any:
        """执行受限 APL 只读查询，最多返回 1000 行。"""
        query = _text(apl, "apl", 8_000)
        limits = [int(item) for item in re.findall(r"\|\s*limit\s+(\d+)", query, re.I)]
        if limits and max(limits) > 1_000:
            raise ValueError("APL limit 不能超过 1000。")
        if not limits:
            query = f"{query} | limit 100"
        response = client.request(
            "https://api.axiom.co/v1/datasets/_apl?format=legacy",
            method="POST",
            headers={**headers, "Content-Type": "application/json"},
            body=_body({"apl": query, "startTime": _text(start_time, "start_time", 80), "endTime": _text(end_time, "end_time", 80)}),
        )
        return _json(response, "Axiom")

    @mcp.tool(annotations=READ_ONLY)
    def getSavedQueries() -> Any:
        """列出已保存查询。"""
        return get("/v2/saved-queries")

    @mcp.tool(annotations=READ_ONLY)
    def getMonitors() -> Any:
        """列出现有监控器。"""
        return get("/v2/monitors")

    @mcp.tool(annotations=READ_ONLY)
    def getMonitorsHistory(monitor_id: str) -> Any:
        """读取指定监控器的历史状态。"""
        item = quote(_text(monitor_id, "monitor_id", 200), safe="")
        return get(f"/v2/monitors/{item}/history")

    return mcp


def build_grafana() -> FastMCP:
    token = _required_environment("GRAFANA_SERVICE_TOKEN")
    slug = _required_environment("GRAFANA_STACK_SLUG", 63)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", slug):
        raise RuntimeError("Grafana Stack 配置无效。")
    host = f"{slug}.grafana.net"
    client = SafeHttpClient(
        allowed_hosts=frozenset({host}),
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"authorization"}),
    )
    headers = {"Authorization": f"Bearer {token}"}
    mcp = FastMCP("ModelMirror Grafana Cloud Read Only")

    def get(path: str) -> Any:
        return _json(client.request(f"https://{host}{path}", headers=headers), "Grafana Cloud")

    @mcp.tool(annotations=READ_ONLY)
    def search_dashboards(query: str = "", limit: int = 50) -> Any:
        """按标题搜索仪表盘。"""
        count = max(1, min(int(limit), 100))
        params = urlencode({"query": str(query)[:200], "limit": count, "type": "dash-db"})
        return get(f"/api/search?{params}")

    @mcp.tool(annotations=READ_ONLY)
    def get_dashboard_by_uid(uid: str) -> Any:
        """读取指定 UID 的仪表盘。"""
        return get(f"/api/dashboards/uid/{quote(_text(uid, 'uid', 160), safe='')}")

    @mcp.tool(annotations=READ_ONLY)
    def list_datasources() -> Any:
        """列出数据源元数据，不返回凭据。"""
        return get("/api/datasources")

    @mcp.tool(annotations=READ_ONLY)
    def list_alert_rules() -> Any:
        """列出统一告警规则。"""
        return get("/api/v1/provisioning/alert-rules")

    return mcp


def build_kagi() -> FastMCP:
    token = _required_environment("KAGI_API_TOKEN")
    client = SafeHttpClient(
        allowed_hosts=frozenset({"kagi.com"}),
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"authorization"}),
    )
    mcp = FastMCP("ModelMirror Kagi Search")

    @mcp.tool(annotations=READ_ONLY)
    def kagi_search(query: str, limit: int = 10) -> Any:
        """执行 Kagi 只读网页搜索。"""
        count = max(1, min(int(limit), 20))
        params = urlencode({"q": _text(query, "query", 1_000), "limit": count})
        response = client.request(
            f"https://kagi.com/api/v0/search?{params}",
            headers={"Authorization": f"Bot {token}"},
        )
        return _json(response, "Kagi")

    return mcp


def build_kagi_official() -> FastMCP:
    """Expose the reviewed read-only subset of official kagimcp v1.0.2."""

    token = _required_environment("KAGI_API_KEY")
    client = SafeHttpClient(
        allowed_hosts=frozenset({"kagi.com"}),
        timeout=20.0,
        max_redirects=0,
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"authorization"}),
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json,text/markdown,text/plain",
    }
    mcp = FastMCP("ModelMirror Official Kagi Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def kagi_search_fetch(
        query: str,
        workflow: str = "search",
        limit: int = 10,
    ) -> str:
        """Fetch bounded Kagi web, news, video, podcast, or image search results."""
        clean_workflow = str(workflow or "search").strip().lower()
        if clean_workflow not in {"search", "news", "videos", "podcasts", "images"}:
            raise ValueError("workflow is not part of the reviewed Kagi contract.")
        count = int(limit)
        if count < 1 or count > 20:
            raise ValueError("limit must be between 1 and 20.")
        response = client.request(
            "https://kagi.com/api/v1/search",
            method="POST",
            headers=headers,
            body=_body(
                {
                    "query": _text(query, "query", 1_000),
                    "workflow": clean_workflow,
                    "format": "markdown",
                    "limit": count,
                }
            ),
        )
        return _bounded_text_response(response, "Kagi Search")

    @mcp.tool(annotations=READ_ONLY)
    def kagi_extract(url: str) -> str:
        """Extract one public HTTPS page through Kagi and return bounded markdown."""
        response = client.request(
            "https://kagi.com/api/v1/extract",
            method="POST",
            headers=headers,
            body=_body(
                {
                    "pages": [{"url": _public_extract_url(url)}],
                    "format": "json",
                }
            ),
        )
        payload = _json(response, "Kagi Extract")
        pages = payload.get("data") if isinstance(payload, dict) else None
        markdown = pages[0].get("markdown") if (
            isinstance(pages, list)
            and pages
            and isinstance(pages[0], dict)
        ) else None
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("Kagi Extract returned no page content.")
        if len(markdown.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("Tool output exceeds the 256 KiB limit.")
        return markdown

    return mcp


_ARXIV_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_ARXIV_ID = re.compile(
    r"(?i)(?:[0-9]{4}\.[0-9]{4,5}|[a-z][a-z0-9.-]*/[0-9]{7})(?:v[0-9]+)?"
)
_ARXIV_CATEGORY = re.compile(r"(?:[a-z]+(?:-[a-z]+)*)(?:\.[A-Z]{2})?")
_ARXIV_CATEGORY_PREFIXES = frozenset(
    {
        "cs", "econ", "eess", "math", "physics", "q-bio", "q-fin", "stat",
        "astro-ph", "cond-mat", "gr-qc", "hep-ex", "hep-lat", "hep-ph",
        "hep-th", "math-ph", "nlin", "nucl-ex", "nucl-th", "quant-ph",
    }
)


def _arxiv_paper_id(value: object) -> str:
    clean = _text(value, "paper_id", 80)
    if _ARXIV_ID.fullmatch(clean) is None:
        raise ValueError("paper_id must be a current or legacy arXiv identifier.")
    return clean


def _arxiv_categories(values: object) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError("categories must contain at most 12 arXiv categories.")
    output: list[str] = []
    for raw in values:
        category = str(raw or "").strip()
        prefix = category.split(".", 1)[0]
        if (
            _ARXIV_CATEGORY.fullmatch(category) is None
            or prefix not in _ARXIV_CATEGORY_PREFIXES
        ):
            raise ValueError("categories contains an invalid arXiv category.")
        if category not in output:
            output.append(category)
    return output


def _arxiv_entry_text(entry: ET.Element, tag: str) -> str:
    element = entry.find(tag, _ARXIV_NAMESPACES)
    return " ".join(str(element.text or "").split()) if element is not None else ""


def _parse_arxiv_feed(xml_text: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("arXiv returned invalid Atom XML.") from exc
    papers: list[dict[str, object]] = []
    for entry in root.findall("atom:entry", _ARXIV_NAMESPACES):
        raw_id = _arxiv_entry_text(entry, "atom:id").split("/abs/")[-1]
        if _ARXIV_ID.fullmatch(raw_id) is None:
            continue
        short_id = re.sub(r"v[0-9]+$", "", raw_id, flags=re.IGNORECASE)
        authors = [
            _arxiv_entry_text(author, "atom:name")
            for author in entry.findall("atom:author", _ARXIV_NAMESPACES)
        ]
        categories: list[str] = []
        for element in (
            *entry.findall("arxiv:primary_category", _ARXIV_NAMESPACES),
            *entry.findall("atom:category", _ARXIV_NAMESPACES),
        ):
            category = str(element.get("term") or "").strip()
            if category and category not in categories:
                categories.append(category[:80])
        papers.append(
            {
                "id": short_id,
                "title": _arxiv_entry_text(entry, "atom:title")[:2_000],
                "authors": [item[:500] for item in authors if item][:100],
                "abstract": "[EXTERNAL CONTENT] "
                + _arxiv_entry_text(entry, "atom:summary")[:32_000],
                "categories": categories[:100],
                "published": _arxiv_entry_text(entry, "atom:published")[:80],
                "pdf_url": f"https://arxiv.org/pdf/{short_id}.pdf",
                "resource_uri": f"arxiv://{short_id}",
            }
        )
    return papers


def build_arxiv_readonly() -> FastMCP:
    """Expose metadata-only tools compatible with arxiv-mcp-server v0.6.2."""

    host = "export.arxiv.org"
    client = SafeHttpClient(
        allowed_hosts=frozenset({host}),
        timeout=30.0,
        max_redirects=0,
        max_response_bytes=MAX_RESULT_BYTES,
        minimum_intervals={host: 3.0},
    )
    mcp = FastMCP("ModelMirror arXiv Metadata Read Only")

    def fetch(parameters: dict[str, object]) -> list[dict[str, object]]:
        response = client.request(
            f"https://{host}/api/query?{urlencode(parameters)}",
            headers={"Accept": "application/atom+xml"},
        )
        return _parse_arxiv_feed(_bounded_text_response(response, "arXiv"))

    @mcp.tool(annotations=READ_ONLY)
    def search_papers(
        query: str,
        max_results: int = 10,
        categories: list[str] | None = None,
        sort_by: str = "relevance",
    ) -> dict[str, object]:
        """Search public arXiv metadata without downloading or caching papers."""
        clean_query = _text(query, "query", 1_000)
        count = int(max_results)
        if count < 1 or count > 20:
            raise ValueError("max_results must be between 1 and 20.")
        clean_sort = str(sort_by or "relevance").strip().lower()
        if clean_sort not in {"relevance", "date"}:
            raise ValueError("sort_by must be relevance or date.")
        category_values = _arxiv_categories(categories)
        query_parts = [f"({clean_query})"]
        if category_values:
            query_parts.append(
                "(" + " OR ".join(f"cat:{item}" for item in category_values) + ")"
            )
        papers = fetch(
            {
                "search_query": " AND ".join(query_parts),
                "max_results": count,
                "sortBy": "submittedDate" if clean_sort == "date" else "relevance",
                "sortOrder": "descending",
            }
        )
        return {"total_results": len(papers), "papers": papers}

    @mcp.tool(annotations=READ_ONLY)
    def get_abstract(paper_id: str) -> dict[str, object]:
        """Fetch public abstract metadata for one arXiv identifier."""
        clean_id = _arxiv_paper_id(paper_id)
        papers = fetch({"id_list": clean_id, "max_results": 1})
        if not papers:
            return {
                "status": "error",
                "paper_id": clean_id,
                "message": "Paper was not found on arXiv.",
            }
        paper = dict(papers[0])
        paper.pop("id", None)
        paper.pop("resource_uri", None)
        return {"status": "success", "paper_id": clean_id, **paper}

    return mcp


_SEARCH1_SEARCH_SERVICES = frozenset(
    {
        "google",
        "bing",
        "duckduckgo",
        "yahoo",
        "github",
        "youtube",
        "x",
        "reddit",
        "arxiv",
        "wechat",
        "bilibili",
        "imdb",
        "wikipedia",
    }
)
_SEARCH1_NEWS_SERVICES = frozenset(
    {"google", "bing", "duckduckgo", "yahoo", "hackernews"}
)
_SEARCH1_TRENDING_SERVICES = frozenset({"github", "hackernews"})
_SEARCH1_TIME_RANGES = frozenset({"day", "month", "year"})


def _choice(value: object, name: str, allowed: frozenset[str]) -> str:
    clean = str(value or "").strip().lower()
    if clean not in allowed:
        raise ValueError(f"{name} is outside the reviewed allowlist.")
    return clean


def _bounded_count(value: object, name: str = "max_results") -> int:
    count = int(value)
    if count < 1 or count > 20:
        raise ValueError(f"{name} must be between 1 and 20.")
    return count


def _external_result_url(value: object) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 16_384:
        return ""
    parsed = urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return ""
    return clean


def _search1_results(payload: object, limit: int) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise ValueError("Search1API returned an invalid response shape.")
    raw_items: object = []
    for key in ("results", "data", "items", "trending"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            raw_items = candidate
            break
    output: list[dict[str, object]] = []
    for raw in raw_items[:limit] if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        item: dict[str, object] = {}
        for source_key, output_key, maximum in (
            ("title", "title", 1_000),
            ("name", "title", 1_000),
            ("snippet", "snippet", 4_000),
            ("description", "snippet", 4_000),
            ("source", "source", 500),
            ("domain", "domain", 500),
            ("author", "author", 500),
            ("date", "published_at", 100),
            ("published_at", "published_at", 100),
            ("language", "language", 80),
        ):
            value = raw.get(source_key)
            if output_key in item or not isinstance(value, str) or not value.strip():
                continue
            text = " ".join(value.split())[:maximum]
            item[output_key] = (
                f"[EXTERNAL CONTENT] {text}"
                if output_key in {"title", "snippet"}
                else text
            )
        result_url = _external_result_url(raw.get("link") or raw.get("url"))
        if result_url:
            item["url"] = result_url
        for source_key, output_key in (("rank", "rank"), ("stars", "stars")):
            value = raw.get(source_key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                item[output_key] = value
        if item:
            output.append(item)
    return output


def build_search1api_readonly() -> FastMCP:
    """Expose only discovery tools from official search1api-mcp v0.5.3."""

    token = _required_environment("SEARCH1API_KEY")
    host = "api.search1api.com"
    client = SafeHttpClient(
        allowed_hosts=frozenset({host}),
        timeout=25.0,
        max_redirects=0,
        max_response_bytes=MAX_RESULT_BYTES,
        minimum_intervals={host: 0.35},
        additional_allowed_headers=frozenset({"authorization"}),
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    mcp = FastMCP("ModelMirror Search1API Discovery Read Only")

    def post(path: str, payload: dict[str, object], limit: int) -> dict[str, object]:
        response = client.request(
            f"https://{host}{path}",
            method="POST",
            headers=headers,
            body=_body(payload),
        )
        return {"results": _search1_results(_json(response, "Search1API"), limit)}

    @mcp.tool(annotations=READ_ONLY)
    def search(
        query: str,
        search_service: str = "google",
        max_results: int = 10,
        language: str = "",
    ) -> dict[str, object]:
        """Search the public web without crawling result pages."""
        count = _bounded_count(max_results)
        payload: dict[str, object] = {
            "query": _text(query, "query", 1_000),
            "search_service": _choice(
                search_service, "search_service", _SEARCH1_SEARCH_SERVICES
            ),
            "max_results": count,
            "crawl_results": 0,
        }
        if str(language or "").strip():
            payload["language"] = _text(language, "language", 32)
        return post("/search", payload, count)

    @mcp.tool(annotations=READ_ONLY)
    def news(
        query: str,
        search_service: str = "google",
        max_results: int = 10,
        language: str = "",
        time_range: str = "day",
    ) -> dict[str, object]:
        """Search recent public news without crawling article pages."""
        count = _bounded_count(max_results)
        payload: dict[str, object] = {
            "query": _text(query, "query", 1_000),
            "search_service": _choice(
                search_service, "search_service", _SEARCH1_NEWS_SERVICES
            ),
            "max_results": count,
            "crawl_results": 0,
            "time_range": _choice(time_range, "time_range", _SEARCH1_TIME_RANGES),
        }
        if str(language or "").strip():
            payload["language"] = _text(language, "language", 32)
        return post("/news", payload, count)

    @mcp.tool(annotations=READ_ONLY)
    def trending(
        search_service: str = "github",
        max_results: int = 10,
    ) -> dict[str, object]:
        """Read bounded trending lists from GitHub or Hacker News."""
        count = _bounded_count(max_results)
        return post(
            "/trending",
            {
                "search_service": _choice(
                    search_service,
                    "search_service",
                    _SEARCH1_TRENDING_SERVICES,
                ),
                "max_results": count,
            },
            count,
        )

    return mcp


_TENNIS_TOURS = frozenset({"atp", "wta", "challenger", "itf", "juniors"})
_TENNIS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}")


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer.")
    clean = int(value)
    if clean < 1 or clean > 9_223_372_036_854_775_807:
        raise ValueError(f"{name} must be a positive integer.")
    return clean


def _tennis_tour(value: object) -> str:
    clean = str(value or "").strip().lower()
    if not clean:
        return ""
    return _choice(clean, "tour", _TENNIS_TOURS)


def _tennis_page(limit: object, offset: object) -> tuple[int, int]:
    count = _bounded_count(limit, "limit")
    start = int(offset)
    if start < 0 or start > 1_000:
        raise ValueError("offset must be between 0 and 1000.")
    return count, start


def _compact_string(value: object, maximum: int = 1_000) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())[:maximum]


def _compact_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _project_tennis_score(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    sets = value.get("sets")
    games = value.get("games")
    points = value.get("points")
    projected_games: list[list[int]] = []
    if isinstance(games, list):
        for side in games[:2]:
            projected_games.append(
                [
                    int(item)
                    for item in side[:10]
                    if isinstance(item, int) and not isinstance(item, bool)
                ]
                if isinstance(side, list)
                else []
            )
    return {
        "sets": [
            int(item)
            for item in sets[:2]
            if isinstance(item, int) and not isinstance(item, bool)
        ] if isinstance(sets, list) else [],
        "games": projected_games,
        "points": [
            None if item is None else str(item)[:16]
            for item in points[:2]
            if item is None or isinstance(item, (str, int))
        ] if isinstance(points, list) else [],
        "server": _compact_int(value.get("server")),
        "is_tiebreak": bool(value.get("is_tiebreak")),
        "timestamp": _compact_string(value.get("timestamp"), 80),
    }


def _project_tennis_player(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {
        "id": _compact_int(value.get("id")),
        "name": _compact_string(value.get("name"), 500),
        "tour": _compact_string(value.get("tour"), 80),
        "country": _compact_string(value.get("country"), 16),
        "ranking": _compact_int(value.get("ranking")),
        "ranking_points": _compact_int(value.get("ranking_points")),
        "ranking_movement": _compact_string(value.get("ranking_movement"), 16),
        "hand": _compact_string(value.get("hand"), 8),
        "backhand": _compact_int(value.get("backhand")),
        "birthday": _compact_string(value.get("birthday"), 32),
        "is_doubles_team": bool(value.get("is_doubles_team")),
    }


def _project_tennis_match(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    players = value.get("players")
    projected_players: dict[str, object] = {}
    if isinstance(players, dict):
        for side in ("p1", "p2"):
            projected = _project_tennis_player(players.get(side))
            if projected is not None:
                projected_players[side] = projected
    return {
        "id": _compact_int(value.get("id")),
        "tournament": _compact_string(value.get("tournament"), 500),
        "tournament_id": _compact_string(value.get("tournament_id"), 120),
        "tour": _compact_string(value.get("tour"), 80),
        "surface": _compact_string(value.get("surface"), 32),
        "indoor": bool(value.get("indoor")),
        "format": _compact_string(value.get("format"), 16),
        "round": _compact_string(value.get("round"), 120),
        "round_code": _compact_string(value.get("round_code"), 16),
        "status": _compact_string(value.get("status"), 32),
        "event_status": _compact_string(value.get("event_status"), 32),
        "is_doubles": bool(value.get("is_doubles")),
        "scheduled_time": _compact_string(value.get("scheduled_time"), 80),
        "players": projected_players,
        "score": _project_tennis_score(value.get("score")),
    }


def _project_tennis_fixture(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: (
            _compact_int(value.get(key))
            if key in {"id", "player1_id", "player2_id"}
            else _compact_string(value.get(key), 500)
        )
        for key in (
            "id",
            "event_date",
            "tour",
            "tournament",
            "round",
            "surface",
            "player1_id",
            "player2_id",
            "player1_name",
            "player2_name",
            "status",
        )
    }


def _project_tennis_tournament(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {
        "id": _compact_string(value.get("id"), 120),
        "name": _compact_string(value.get("name"), 500),
        "tour": _compact_string(value.get("tour"), 80),
        "surface": _compact_string(value.get("surface"), 32),
        "indoor": bool(value.get("indoor")),
        "city": _compact_string(value.get("city"), 200),
        "country": _compact_string(value.get("country"), 16),
        "category": _compact_string(value.get("category"), 80),
    }


def _project_tennis_list(
    payload: object,
    projector: object,
    limit: int,
) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Live Tennis API returned an invalid list response.")
    projected = [
        item
        for item in (projector(raw) for raw in payload["data"][:limit])
        if item is not None
    ]
    meta = payload.get("meta")
    safe_meta: dict[str, object] = {}
    if isinstance(meta, dict):
        for key in ("limit", "offset", "count", "total"):
            parsed = _compact_int(meta.get(key))
            if parsed is not None:
                safe_meta[key] = parsed
        if isinstance(meta.get("has_more"), bool):
            safe_meta["has_more"] = meta["has_more"]
    return {"data": projected, "meta": safe_meta}


def build_livetennisapi_readonly() -> FastMCP:
    """Expose only the FREE non-financial subset of livetennisapi-mcp v1.4.0."""

    token = _required_environment("LIVE_TENNIS_API_KEY")
    host = "api.livetennisapi.com"
    base = f"https://{host}/api/public/v1"
    client = SafeHttpClient(
        allowed_hosts=frozenset({host}),
        timeout=15.0,
        max_redirects=0,
        max_response_bytes=MAX_RESULT_BYTES,
        minimum_intervals={host: 2.1},
        additional_allowed_headers=frozenset({"authorization"}),
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    mcp = FastMCP("ModelMirror Live Tennis Free Read Only")

    def get(path: str) -> Any:
        return _json(client.request(f"{base}{path}", headers=headers), "Live Tennis API")

    def list_matches(status: str, tour: str, limit: int, offset: int) -> dict[str, object]:
        count, start = _tennis_page(limit, offset)
        parameters: dict[str, object] = {"status": status, "limit": count, "offset": start}
        clean_tour = _tennis_tour(tour)
        if clean_tour:
            parameters["tour"] = clean_tour
        return _project_tennis_list(
            get(f"/matches?{urlencode(parameters)}"), _project_tennis_match, count
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_live_matches(tour: str = "", limit: int = 10, offset: int = 0) -> dict[str, object]:
        """List current live matches from the FREE API surface."""
        return list_matches("live", tour, limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    def get_upcoming_matches(tour: str = "", limit: int = 10, offset: int = 0) -> dict[str, object]:
        """List upcoming matches without completed history or paid analysis."""
        return list_matches("upcoming", tour, limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    def get_match_score(match_id: int) -> dict[str, object]:
        """Read one point-in-time score with all model fields removed."""
        clean_id = _positive_id(match_id, "match_id")
        projected = _project_tennis_score(get(f"/matches/{clean_id}/score"))
        if projected is None:
            raise ValueError("Live Tennis API returned an invalid score response.")
        return projected

    @mcp.tool(annotations=READ_ONLY)
    def search_players(query: str, limit: int = 10, offset: int = 0) -> dict[str, object]:
        """Search the FREE player directory without returning cached stats objects."""
        count, start = _tennis_page(limit, offset)
        parameters = {
            "search": _text(query, "query", 200),
            "limit": count,
            "offset": start,
        }
        return _project_tennis_list(
            get(f"/players?{urlencode(parameters)}"), _project_tennis_player, count
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_player(player_id: int) -> dict[str, object]:
        """Read one FREE player profile with stats and completeness blobs removed."""
        projected = _project_tennis_player(
            get(f"/players/{_positive_id(player_id, 'player_id')}")
        )
        if projected is None:
            raise ValueError("Live Tennis API returned an invalid player response.")
        return projected

    @mcp.tool(annotations=READ_ONLY)
    def get_fixtures(tour: str = "", limit: int = 10, offset: int = 0) -> dict[str, object]:
        """List upcoming FREE fixtures with bounded pagination."""
        count, start = _tennis_page(limit, offset)
        parameters: dict[str, object] = {"limit": count, "offset": start}
        clean_tour = _tennis_tour(tour)
        if clean_tour:
            parameters["tour"] = clean_tour
        return _project_tennis_list(
            get(f"/fixtures?{urlencode(parameters)}"), _project_tennis_fixture, count
        )

    @mcp.tool(annotations=READ_ONLY)
    def search_tournaments(
        query: str = "",
        tour: str = "",
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, object]:
        """Search the FREE tournament catalogue."""
        count, start = _tennis_page(limit, offset)
        parameters: dict[str, object] = {"limit": count, "offset": start}
        if str(query or "").strip():
            parameters["search"] = _text(query, "query", 200)
        clean_tour = _tennis_tour(tour)
        if clean_tour:
            parameters["tour"] = clean_tour
        return _project_tennis_list(
            get(f"/tournaments?{urlencode(parameters)}"),
            _project_tennis_tournament,
            count,
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_tournament(tournament_id: str) -> dict[str, object]:
        """Read one tournament from the FREE stable-id catalogue."""
        clean_id = _text(tournament_id, "tournament_id", 120)
        if _TENNIS_ID.fullmatch(clean_id) is None:
            raise ValueError("tournament_id is invalid.")
        projected = _project_tennis_tournament(
            get(f"/tournaments/{quote(clean_id, safe='')}")
        )
        if projected is None:
            raise ValueError("Live Tennis API returned an invalid tournament response.")
        return projected

    return mcp


def build_pinecone() -> FastMCP:
    api_key = _required_environment("PINECONE_API_KEY")
    host = _required_environment("PINECONE_ASSISTANT_HOST", 253).lower().rstrip(".")
    assistant = _required_environment("PINECONE_ASSISTANT_NAME", 80)
    if not host.endswith(".pinecone.io") or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", assistant):
        raise RuntimeError("Pinecone Assistant 配置无效。")
    client = SafeHttpClient(
        allowed_hosts=frozenset({host}),
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"api-key", "x-pinecone-api-version"}),
    )
    mcp = FastMCP("ModelMirror Pinecone Assistant Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def assistant_chat(message: str, include_highlights: bool = False) -> Any:
        """向既有 Pinecone Assistant 发起一次只读问答。"""
        response = client.request(
            f"https://{host}/assistant/chat/{quote(assistant, safe='')}",
            method="POST",
            headers={
                "Api-Key": api_key,
                "X-Pinecone-API-Version": "2025-10",
                "Content-Type": "application/json",
            },
            body=_body({"messages": [{"role": "user", "content": _text(message, "message", 16_000)}], "include_highlights": bool(include_highlights)}),
        )
        return _json(response, "Pinecone Assistant")

    return mcp


_TERRAFORM_SEGMENT = re.compile(
    r"[a-z0-9](?:[a-z0-9_.-]{0,118}[a-z0-9])?",
)


def _terraform_segment(value: object, name: str) -> str:
    clean = str(value or "").strip().lower()
    if _TERRAFORM_SEGMENT.fullmatch(clean) is None:
        raise ValueError(f"{name} must be a Terraform Registry identifier.")
    return clean


def _terraform_bounded_text(value: object, name: str, maximum: int = 128 * 1024) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) > maximum:
        raise ValueError(f"{name} exceeds the fixed output limit.")
    return text


def build_terraform() -> FastMCP:
    """Expose a credential-free subset compatible with Terraform MCP v1.2.0."""

    host = "registry.terraform.io"
    internal_module_response_limit = 2 * 1024 * 1024
    client = SafeHttpClient(
        allowed_hosts=frozenset({host}),
        timeout=10.0,
        max_redirects=0,
        max_response_bytes=internal_module_response_limit,
        minimum_intervals={host: 0.2},
    )
    mcp = FastMCP("ModelMirror Terraform Registry Read Only")

    def get(path: str, *, response_limit: int = MAX_RESULT_BYTES) -> Any:
        response = client.request(
            f"https://{host}{path}",
            max_response_bytes=response_limit,
        )
        if response_limit <= MAX_RESULT_BYTES:
            return _json(response, "Terraform Registry")
        if not 200 <= response.status < 300:
            raise ValueError(f"Terraform Registry returned HTTP {response.status}.")
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Terraform Registry returned invalid JSON.") from exc

    def latest_provider_version(namespace: str, name: str) -> str:
        payload = get(f"/v1/providers/{namespace}/{name}")
        version = str(payload.get("version") if isinstance(payload, dict) else "").strip()
        if not version or len(version) > 80:
            raise ValueError("Terraform Registry did not return a valid provider version.")
        return version

    @mcp.tool(annotations=READ_ONLY)
    def get_latest_provider_version(namespace: str, name: str) -> str:
        """Fetch the latest public Terraform provider version."""
        return latest_provider_version(
            _terraform_segment(namespace, "namespace"),
            _terraform_segment(name, "name"),
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_provider_capabilities(
        namespace: str,
        name: str,
        version: str = "latest",
    ) -> dict[str, object]:
        """List public provider documentation categories and bounded examples."""
        clean_namespace = _terraform_segment(namespace, "namespace")
        clean_name = _terraform_segment(name, "name")
        clean_version = str(version or "latest").strip().lower()
        if clean_version == "latest":
            clean_version = latest_provider_version(clean_namespace, clean_name)
        elif re.fullmatch(r"[0-9A-Za-z](?:[0-9A-Za-z.+-]{0,78}[0-9A-Za-z])?", clean_version) is None:
            raise ValueError("version must be a Terraform provider version or latest.")
        payload = get(
            f"/v1/providers/{clean_namespace}/{clean_name}/{quote(clean_version, safe='')}"
        )
        docs = payload.get("docs") if isinstance(payload, dict) else None
        if not isinstance(docs, list):
            raise ValueError("Terraform Registry provider documentation is unavailable.")
        grouped: dict[str, list[dict[str, str]]] = {}
        counts: dict[str, int] = {}
        for item in docs:
            if not isinstance(item, dict) or str(item.get("language") or "").lower() != "hcl":
                continue
            category = str(item.get("category") or "other").strip().lower()[:80]
            counts[category] = counts.get(category, 0) + 1
            examples = grouped.setdefault(category, [])
            if len(examples) < 10:
                examples.append(
                    {
                        "title": str(item.get("title") or "")[:240],
                        "provider_doc_id": str(item.get("id") or "")[:40],
                    }
                )
        return {
            "provider": f"{clean_namespace}/{clean_name}",
            "version": clean_version,
            "capabilities": [
                {"category": category, "count": counts[category], "examples": grouped[category]}
                for category in sorted(grouped)
            ],
        }

    @mcp.tool(annotations=READ_ONLY)
    def get_provider_details(provider_doc_id: str) -> str:
        """Fetch one public provider documentation page by numeric document ID."""
        clean_id = str(provider_doc_id or "").strip()
        if re.fullmatch(r"[1-9][0-9]{0,19}", clean_id) is None:
            raise ValueError("provider_doc_id must be a positive numeric Registry document ID.")
        payload = get(f"/v2/provider-docs/{clean_id}")
        data = payload.get("data") if isinstance(payload, dict) else None
        attributes = data.get("attributes") if isinstance(data, dict) else None
        if not isinstance(attributes, dict):
            raise ValueError("Terraform Registry provider documentation is unavailable.")
        return _terraform_bounded_text(attributes.get("content"), "provider documentation")

    @mcp.tool(annotations=READ_ONLY)
    def search_modules(module_query: str, current_offset: int = 0) -> dict[str, object]:
        """Search public Terraform modules and return at most twenty compact matches."""
        query = _text(module_query, "module_query", 240).lower()
        offset = int(current_offset)
        if offset < 0 or offset > 10_000:
            raise ValueError("current_offset must be between 0 and 10000.")
        payload = get(f"/v1/modules/search?{urlencode({'q': query, 'offset': offset})}")
        modules = payload.get("modules") if isinstance(payload, dict) else None
        if not isinstance(modules, list):
            raise ValueError("Terraform Registry module search is unavailable.")
        compact: list[dict[str, object]] = []
        for item in modules[:20]:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "module_id": str(item.get("id") or "")[:500],
                    "name": str(item.get("name") or "")[:240],
                    "description": str(item.get("description") or "")[:1_000],
                    "downloads": max(0, int(item.get("downloads") or 0)),
                    "verified": bool(item.get("verified")),
                    "published_at": str(item.get("published_at") or "")[:80],
                }
            )
        return {"query": query, "offset": offset, "modules": compact}

    @mcp.tool(annotations=READ_ONLY)
    def get_module_details(module_id: str) -> dict[str, object]:
        """Fetch bounded public module metadata using a four-part Registry module ID."""
        raw_parts = str(module_id or "").strip().split("/")
        if len(raw_parts) != 4:
            raise ValueError("module_id must use namespace/name/provider/version format.")
        parts = [_terraform_segment(part, "module_id segment") for part in raw_parts]
        payload = get(
            f"/v1/modules/{'/'.join(parts)}?offset=0",
            response_limit=internal_module_response_limit,
        )
        if not isinstance(payload, dict):
            raise ValueError("Terraform Registry module details are unavailable.")
        root = payload.get("root")
        if not isinstance(root, dict):
            root = {}

        def bounded_items(name: str, limit: int = 50) -> list[dict[str, object]]:
            value = root.get(name)
            if not isinstance(value, list):
                return []
            output: list[dict[str, object]] = []
            for item in value[:limit]:
                if not isinstance(item, dict):
                    continue
                output.append(
                    {
                        key: (
                            (raw if isinstance(raw, bool) else False)
                            if key == "required"
                            else str(raw if raw is not None else "")[:500]
                        )
                        for key, raw in item.items()
                        if key
                        in {
                            "name",
                            "type",
                            "description",
                            "default",
                            "required",
                            "namespace",
                            "source",
                            "version",
                        }
                    }
                )
            return output

        return {
            "module_id": "/".join(parts),
            "namespace": str(payload.get("namespace") or "")[:240],
            "name": str(payload.get("name") or "")[:240],
            "provider": str(payload.get("provider") or "")[:240],
            "version": str(payload.get("version") or "")[:80],
            "source": str(payload.get("source") or "")[:500],
            "description": str(payload.get("description") or "")[:2_000],
            "inputs": bounded_items("inputs"),
            "outputs": bounded_items("outputs"),
            "provider_dependencies": bounded_items("provider_dependencies"),
        }

    @mcp.tool(annotations=READ_ONLY)
    def get_latest_module_version(
        module_publisher: str,
        module_name: str,
        module_provider: str,
    ) -> str:
        """Fetch the latest public Terraform module version."""
        parts = [
            _terraform_segment(module_publisher, "module_publisher"),
            _terraform_segment(module_name, "module_name"),
            _terraform_segment(module_provider, "module_provider"),
        ]
        payload = get(
            f"/v1/modules/{'/'.join(parts)}",
            response_limit=internal_module_response_limit,
        )
        version = str(payload.get("version") if isinstance(payload, dict) else "").strip()
        if not version or len(version) > 80:
            raise ValueError("Terraform Registry did not return a valid module version.")
        return version

    return mcp


def build_google_map_readonly() -> FastMCP:
    """Expose the reviewed read-only subset of cablate/mcp-google-map v0.0.53."""
    api_key = _required_environment("GOOGLE_MAPS_API_KEY")
    client = SafeHttpClient(
        allowed_hosts=frozenset({"places.googleapis.com"}),
        max_response_bytes=MAX_RESULT_BYTES,
        minimum_intervals={"places.googleapis.com": 0.25},
        additional_allowed_headers=frozenset({"x-goog-api-key", "x-goog-fieldmask"}),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
    }
    mcp = FastMCP("ModelMirror Google Maps Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def maps_search_places(
        query: str,
        locationBias: dict[str, float] | None = None,
        openNow: bool | None = None,
        minRating: float | None = None,
        includedType: str | None = None,
    ) -> Any:
        """Search at most ten Google Places using a fixed response field mask."""
        payload: dict[str, object] = {
            "textQuery": _text(query, "query", 500),
            "maxResultCount": 10,
        }
        if locationBias is not None:
            if set(locationBias) - {"latitude", "longitude", "radius"}:
                raise ValueError("locationBias contains unsupported fields.")
            latitude = float(locationBias.get("latitude", 999))
            longitude = float(locationBias.get("longitude", 999))
            radius = float(locationBias.get("radius", 5_000))
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("locationBias coordinates are invalid.")
            if not 1 <= radius <= 50_000:
                raise ValueError("locationBias radius is invalid.")
            payload["locationBias"] = {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": radius,
                }
            }
        if openNow is not None:
            payload["openNow"] = bool(openNow)
        if minRating is not None:
            rating = float(minRating)
            if not 1 <= rating <= 5:
                raise ValueError("minRating must be between 1 and 5.")
            payload["minRating"] = rating
        if includedType is not None:
            payload["includedType"] = _opaque_identifier(includedType, "includedType", 120)
        response = client.request(
            "https://places.googleapis.com/v1/places:searchText",
            method="POST",
            headers={
                **headers,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.formattedAddress,"
                    "places.location,places.types,places.googleMapsUri"
                ),
            },
            body=_body(payload),
        )
        result = _json(response, "Google Places")
        if not isinstance(result, dict):
            raise ValueError("Google Places returned an invalid response.")
        places = result.get("places")
        return {"places": places[:10] if isinstance(places, list) else []}

    @mcp.tool(annotations=READ_ONLY)
    def maps_place_details(placeId: str) -> Any:
        """Read bounded metadata for one Google Places place ID; photos and reviews are excluded."""
        place_id = quote(_opaque_identifier(placeId, "placeId", 240), safe="")
        response = client.request(
            f"https://places.googleapis.com/v1/places/{place_id}",
            headers={
                **headers,
                "X-Goog-FieldMask": (
                    "id,displayName,formattedAddress,location,types,googleMapsUri,"
                    "primaryType,primaryTypeDisplayName"
                ),
            },
        )
        return _json(response, "Google Places")

    return mcp


def build_opik_readonly() -> FastMCP:
    """Expose Opik 0.2.15's universal list/read identity without write or Ollie tools."""
    api_key = _required_environment("OPIK_API_KEY")
    workspace = _required_environment("OPIK_WORKSPACE", 120)
    client = SafeHttpClient(
        allowed_hosts=frozenset({"www.comet.com"}),
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"authorization", "comet-workspace"}),
    )
    headers = {"Authorization": api_key, "Comet-Workspace": workspace}
    mcp = FastMCP("ModelMirror Opik Read Only")
    list_paths = {
        "project": "/opik/api/v1/private/projects",
        "trace": "/opik/api/v1/private/traces",
        "test_suite": "/opik/api/v1/private/datasets",
        "experiment": "/opik/api/v1/private/experiments",
        "prompt": "/opik/api/v1/private/prompts",
    }
    read_paths = {
        "project": "/opik/api/v1/private/projects/{id}",
        "trace": "/opik/api/v1/private/traces/{id}",
        "test_suite": "/opik/api/v1/private/datasets/{id}",
        "experiment": "/opik/api/v1/private/experiments/{id}",
        "prompt": "/opik/api/v1/private/prompts/{id}",
    }

    @mcp.tool(name="list", annotations=READ_ONLY)
    def list_entities(
        entity_type: str,
        name: str | None = None,
        page: int = 1,
        size: int = 25,
        project_id: str | None = None,
    ) -> Any:
        """List a bounded page of reviewed Opik entity types."""
        entity = _text(entity_type, "entity_type", 40)
        if entity not in list_paths:
            raise ValueError("entity_type is not available in the read-only facade.")
        page_number = int(page)
        page_size = int(size)
        if not 1 <= page_number <= 10_000 or not 1 <= page_size <= 100:
            raise ValueError("page or size is outside the allowed range.")
        params: list[tuple[str, str]] = [("page", str(page_number)), ("size", str(page_size))]
        if name is not None:
            params.append(("name", _text(name, "name", 240)))
        if entity == "trace":
            params.append(("project_id", _opaque_identifier(project_id, "project_id")))
        url = f"https://www.comet.com{list_paths[entity]}?{urlencode(params)}"
        return _json(client.request(url, headers=headers), "Opik")

    @mcp.tool(name="read", annotations=READ_ONLY)
    def read_entity(entity_type: str, id: str) -> Any:
        """Read one reviewed Opik entity by its opaque ID."""
        entity = _text(entity_type, "entity_type", 40)
        if entity not in read_paths:
            raise ValueError("entity_type is not available in the read-only facade.")
        entity_id = quote(_opaque_identifier(id, "id"), safe="")
        path = read_paths[entity].format(id=entity_id)
        return _json(client.request(f"https://www.comet.com{path}", headers=headers), "Opik")

    return mcp


def build_keboola_metadata_readonly() -> FastMCP:
    """Expose the metadata-only portion of Keboola MCP 1.75.2 on the fixed US stack."""
    token = _required_environment("KEBOOLA_STORAGE_TOKEN")
    client = SafeHttpClient(
        allowed_hosts=frozenset({"connection.keboola.com"}),
        max_response_bytes=MAX_RESULT_BYTES,
        additional_allowed_headers=frozenset({"x-storageapi-token"}),
    )
    headers = {"X-StorageAPI-Token": token}
    base = "https://connection.keboola.com/v2/storage"
    mcp = FastMCP("ModelMirror Keboola Metadata Read Only")

    def get(path: str) -> Any:
        return _json(client.request(f"{base}/{path}", headers=headers), "Keboola")

    @mcp.tool(annotations=READ_ONLY)
    def get_project_info() -> Any:
        """Verify the fixed Storage token and return bounded project metadata."""
        payload = get("tokens/verify")
        if not isinstance(payload, dict):
            raise ValueError("Keboola returned invalid project metadata.")
        owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
        return {
            "token_id": str(payload.get("id") or "")[:120],
            "description": str(payload.get("description") or "")[:500],
            "owner": {
                "id": str(owner.get("id") or "")[:120],
                "name": str(owner.get("name") or "")[:240],
            },
        }

    @mcp.tool(annotations=READ_ONLY)
    def get_buckets(bucket_ids: list[str] | None = None) -> Any:
        """List at most one hundred buckets or read at most twenty explicit bucket IDs."""
        ids = _bounded_sequence(bucket_ids, "bucket_ids")
        if ids:
            return {"buckets": [get(f"branch/default/buckets/{quote(item, safe='')}") for item in ids]}
        payload = get("branch/default/buckets")
        return {"buckets": payload[:100] if isinstance(payload, list) else []}

    @mcp.tool(annotations=READ_ONLY)
    def get_tables(
        bucket_ids: list[str] | None = None,
        table_ids: list[str] | None = None,
    ) -> Any:
        """Read table metadata for at most twenty buckets or explicit table IDs."""
        buckets = _bounded_sequence(bucket_ids, "bucket_ids")
        tables = _bounded_sequence(table_ids, "table_ids")
        if not buckets and not tables:
            raise ValueError("bucket_ids or table_ids is required.")
        output: list[object] = []
        for table_id in tables:
            output.append(get(f"branch/default/tables/{quote(table_id, safe='')}"))
        for bucket_id in buckets:
            payload = get(f"branch/default/buckets/{quote(bucket_id, safe='')}/tables")
            if isinstance(payload, list):
                output.extend(payload[:100])
        return {"tables": output[:100]}

    return mcp


BUILDERS = {
    "axiom-mcp": build_axiom,
    "blazickjp-arxiv-mcp-server": build_arxiv_readonly,
    "fatwang2-search1api-mcp": build_search1api_readonly,
    "grafana-mcp": build_grafana,
    "kagisearch-kagimcp": build_kagi_official,
    "kagi-mcp": build_kagi,
    "livetennisapi-livetennisapi-mcp": build_livetennisapi_readonly,
    "pinecone-assistant-mcp": build_pinecone,
    "terraform-mcp": build_terraform,
    "cablate-mcp-google-map": build_google_map_readonly,
    "comet-ml-opik-mcp": build_opik_readonly,
    "keboola-keboola-mcp-server": build_keboola_metadata_readonly,
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    BUILDERS[args.adapter_id]().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
