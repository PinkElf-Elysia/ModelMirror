"""Bundled public-network MCP adapters for catalog wave 2.

These adapters intentionally expose a reviewed subset of their upstream tool
contracts.  They never accept commands, environment names, headers, endpoints,
or working directories from the MCP client.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .safe_http import NetworkPolicyError, SafeHttpClient


MAX_RESULT_BYTES = 128 * 1024
MAX_FETCH_BYTES = 1024 * 1024
MAX_AIRBNB_BYTES = 2 * 1024 * 1024
MAX_FETCH_LENGTH = 100_000
MAX_GEO_RESULTS = 10
MAX_PUBLIC_DIRECTORY_RESULTS = 100
VALID_CHART_TYPES = {
    "bar",
    "line",
    "pie",
    "doughnut",
    "radar",
    "polarArea",
    "scatter",
    "bubble",
    "radialGauge",
    "speedometer",
}
DATE_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}")
LISTING_ID = re.compile(r"[0-9]{1,24}")

FETCH_USER_AGENT = (
    "ModelMirrorMCP/1.0 (Autonomous; "
    "+https://github.com/PinkElf-Elysia/ModelMirror)"
)
AIRBNB_USER_AGENT = (
    "ModelMirror-Airbnb-MCP/0.3.0-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
GEO_USER_AGENT = (
    "ModelMirror-GeoWire-MCP/0.6.2-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
DUCKDUCKGO_USER_AGENT = (
    "ModelMirror-DuckDuckGo-MCP/0.6.1-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
SHADCN_USER_AGENT = (
    "ModelMirror-Shadcn-MCP/2.0.0-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
DOCKER_HUB_USER_AGENT = (
    "ModelMirror-DockerHub-MCP/0.18.0-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
BIOMCP_USER_AGENT = (
    "ModelMirror-BioMCP/0.8.25-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
SAFEDEP_USER_AGENT = (
    "ModelMirror-SafeDep-Vet/1.18.1-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
OPEN_WEBSEARCH_USER_AGENT = (
    "ModelMirror-open-webSearch/2.1.9-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
IDEA_REALITY_USER_AGENT = (
    "ModelMirror-Idea-Reality/0.5.0-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
GITMCP_USER_AGENT = (
    "ModelMirror-GitMCP/c487a298-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
DEXPAPRIKA_USER_AGENT = (
    "ModelMirror-DexPaprika-MCP/2.3.2-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
CHESS_USER_AGENT = (
    "ModelMirror-Chess-MCP/0.1.0-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
ANILIST_USER_AGENT = (
    "ModelMirror-AniList-MCP/1.4.0-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
REDDIT_BUDDY_USER_AGENT = (
    "ModelMirror-Reddit-MCP-Buddy/1.1.14-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)
FANTASY_PL_USER_AGENT = (
    "ModelMirror-Fantasy-PL-MCP/0.1.7-compatible "
    "(+https://github.com/PinkElf-Elysia/ModelMirror)"
)

QUICKCHART_HOSTS = frozenset({"quickchart.io"})
AIRBNB_HOSTS = frozenset(
    {
        "www.airbnb.com",
        "airbnb.com",
        "photon.komoot.io",
        "nominatim.openstreetmap.org",
    }
)
GEOWIRE_HOSTS = frozenset(
    {
        "nominatim.openstreetmap.org",
        "router.project-osrm.org",
    }
)
DUCKDUCKGO_HOSTS = frozenset({"html.duckduckgo.com"})
SHADCN_HOSTS = frozenset({"api.github.com"})
DOCKER_HUB_HOSTS = frozenset({"hub.docker.com"})
BIOMCP_HOSTS = frozenset(
    {"www.ebi.ac.uk", "clinicaltrials.gov", "myvariant.info"}
)
SAFEDEP_HOSTS = frozenset(
    {"community-api.safedep.io", "registry.npmjs.org", "pypi.org"}
)
OPEN_WEBSEARCH_HOSTS = frozenset({"cn.bing.com", "html.duckduckgo.com"})
IDEA_REALITY_HOSTS = frozenset(
    {"api.github.com", "hn.algolia.com", "registry.npmjs.org", "pypi.org"}
)
GITMCP_HOSTS = frozenset({"api.github.com"})
DEXPAPRIKA_HOSTS = frozenset({"api.dexpaprika.com"})
CHESS_HOSTS = frozenset({"api.chess.com"})
ANILIST_HOSTS = frozenset({"graphql.anilist.co"})
REDDIT_BUDDY_HOSTS = frozenset({"www.reddit.com"})
FANTASY_PL_HOSTS = frozenset({"fantasy.premierleague.com"})

DUCKDUCKGO_REGION = re.compile(r"(?:[a-z]{2}-[a-z]{2}|wt-wt)")
PUBLIC_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")
SHADCN_COMPONENT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?")
GIT_OBJECT_SHA = re.compile(r"[0-9a-f]{40}")
NCT_ID = re.compile(r"NCT[0-9]{8}", re.IGNORECASE)
PUBMED_ID = re.compile(r"[0-9]{1,12}")
PURL = re.compile(r"pkg:(npm|pypi)/([^?#]+?)(?:@([^/?#]+))?", re.IGNORECASE)
NPM_PACKAGE = re.compile(
    r"(?:@[a-z0-9](?:[a-z0-9._~-]{0,212}[a-z0-9])?/)?"
    r"[a-z0-9](?:[a-z0-9._~-]{0,212}[a-z0-9])?"
)
PYPI_PACKAGE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,198}[A-Za-z0-9])?")
PACKAGE_VERSION = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._+!~-]{0,126}[A-Za-z0-9])?")
GITHUB_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?"
)
IDEA_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.-]{2,40}")
CHESS_USERNAME = re.compile(r"[A-Za-z0-9_-]{1,50}")
REDDIT_SUBREDDIT = re.compile(r"(?:[A-Za-z0-9_]{2,21}|all|popular)")
REDDIT_POST_PATH = re.compile(
    r"/r/[A-Za-z0-9_]{2,21}/comments/[A-Za-z0-9]{2,13}(?:/[^/?#]*)?/?"
)

SHADCN_UI_COMMIT = "d14b6e69a91f0fc99e31a7adb26a48d661df9911"
SHADCN_COMPONENT_PATH = "apps/v4/registry/new-york-v4/ui"

READ_ONLY_NETWORK = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _json_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _bounded_result(value: Any) -> Any:
    if _json_size(value) > MAX_RESULT_BYTES:
        raise ValueError("工具返回内容超过 128 KiB 上限。")
    return value


def _bounded_string(value: Any, name: str, *, maximum: int = 1_024) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{name}不能为空且不能超过 {maximum} 个字符。")
    return clean


def _finite(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是数字。") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name}必须位于 {minimum} 到 {maximum} 之间。")
    return number


def _coordinates(value: dict[str, Any], name: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}必须是经纬度对象。")
    return {
        "latitude": _finite(value.get("latitude"), f"{name}.latitude", -90, 90),
        "longitude": _finite(value.get("longitude"), f"{name}.longitude", -180, 180),
    }


def _require_success(status: int, provider: str) -> None:
    if status < 200 or status >= 300:
        raise ValueError(f"{provider} 公共服务返回 HTTP {status}。")


class _ReadableHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg", "noscript"}:
            self.skip_depth += 1
        elif not self.skip_depth and tag in {
            "article", "br", "div", "h1", "h2", "h3", "h4", "li", "main", "p", "section"
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in {"article", "div", "li", "main", "p", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        lines = []
        for line in "".join(self.parts).splitlines():
            clean = re.sub(r"\s+", " ", line).strip()
            if clean:
                lines.append(clean)
        return "\n".join(lines)


class _ScriptCapture(HTMLParser):
    def __init__(self, element_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.element_id = element_id
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == self.element_id:
            self.active = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.active:
            self.active = False

    def handle_data(self, data: str) -> None:
        if self.active:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _ReadableHtml()
    parser.feed(html)
    return parser.text()


def fetch_payload(
    url: str,
    max_length: int = 5_000,
    start_index: int = 0,
    raw: bool = False,
) -> str:
    if not 1 <= int(max_length) <= MAX_FETCH_LENGTH:
        raise ValueError(f"max_length 必须位于 1 到 {MAX_FETCH_LENGTH} 之间。")
    if not 0 <= int(start_index) <= 1_000_000:
        raise ValueError("start_index 必须位于 0 到 1000000 之间。")
    client = SafeHttpClient(
        allowed_hosts=None,
        max_response_bytes=MAX_FETCH_BYTES,
    )
    client.assert_robots_allowed(url, FETCH_USER_AGENT)
    response = client.request(
        url,
        headers={"User-Agent": FETCH_USER_AGENT},
        max_response_bytes=MAX_FETCH_BYTES,
    )
    _require_success(response.status, "目标站点")
    content_type = response.headers.get("content-type", "").lower()
    if not any(
        token in content_type
        for token in ("text/", "application/json", "application/xml", "application/xhtml+xml")
    ):
        raise ValueError("Fetch 仅允许文本、HTML、JSON 或 XML 响应。")
    content = response.text()
    if "text/html" in content_type and not raw:
        content = _html_to_text(content)
    start = int(start_index)
    if start >= len(content):
        return "<error>没有更多内容。</error>"
    end = start + int(max_length)
    selected = content[start:end]
    if end < len(content):
        selected += f"\n\n<error>内容已截断；下一次请使用 start_index={end}。</error>"
    return f"Contents of {response.url}:\n{selected}"


def build_fetch() -> FastMCP:
    mcp = FastMCP("ModelMirror Fetch")

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def fetch(
        url: str,
        max_length: int = 5_000,
        start_index: int = 0,
        raw: bool = False,
    ) -> str:
        """安全获取公网 HTTPS 页面，遵守 robots.txt 并按字符分页。"""

        return fetch_payload(url, max_length, start_index, raw)

    return mcp


def _reject_chart_executable_values(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"url", "href", "callback", "function"}:
                raise ValueError("图表配置不得包含远程 URL 或可执行回调。")
            _reject_chart_executable_values(child)
    elif isinstance(value, list):
        for child in value:
            _reject_chart_executable_values(child)
    elif isinstance(value, str):
        lowered = value.strip().lower()
        if lowered.startswith(("http://", "https://", "javascript:")) or "=>" in value:
            raise ValueError("图表配置不得包含远程 URL 或可执行脚本。")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("图表配置不能包含 NaN 或 Infinity。")


def quickchart_url(
    type: str,
    datasets: list[dict[str, Any]],
    labels: list[str] | None = None,
    title: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chart_type = _bounded_string(type, "type", maximum=32)
    if chart_type not in VALID_CHART_TYPES:
        raise ValueError("图表类型不在固定允许清单中。")
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= 8:
        raise ValueError("datasets 必须包含 1 到 8 个数据系列。")
    clean_datasets: list[dict[str, Any]] = []
    for dataset in datasets:
        if not isinstance(dataset, dict) or not isinstance(dataset.get("data"), list):
            raise ValueError("每个数据系列都必须包含 data 数组。")
        if len(dataset["data"]) > 200:
            raise ValueError("每个数据系列最多包含 200 个数据点。")
        allowed = {
            key: dataset[key]
            for key in ("label", "data", "backgroundColor", "borderColor")
            if key in dataset
        }
        _reject_chart_executable_values(allowed)
        clean_datasets.append(allowed)
    clean_labels = list(labels or [])
    if len(clean_labels) > 200 or any(len(str(item)) > 256 for item in clean_labels):
        raise ValueError("labels 最多包含 200 项，单项不超过 256 个字符。")
    clean_options = dict(options or {})
    _reject_chart_executable_values(clean_options)
    config: dict[str, Any] = {
        "type": chart_type,
        "data": {"labels": clean_labels, "datasets": clean_datasets},
        "options": clean_options,
    }
    if title:
        config["options"] = {
            **clean_options,
            "plugins": {
                **(clean_options.get("plugins", {}) if isinstance(clean_options.get("plugins"), dict) else {}),
                "title": {"display": True, "text": _bounded_string(title, "title", maximum=256)},
            },
        }
    encoded = quote(json.dumps(config, ensure_ascii=False, separators=(",", ":")), safe="")
    url = f"https://quickchart.io/chart?c={encoded}"
    if len(url) > 16_384:
        raise ValueError("图表配置编码后超过 16384 个字符。")
    return _bounded_result(
        {
            "url": url,
            "chart_type": chart_type,
            "datasets": len(clean_datasets),
            "network_target": "quickchart.io",
            "note": "仅生成受控图表 URL；本批不写入本地文件。",
        }
    )


def build_quickchart() -> FastMCP:
    mcp = FastMCP("ModelMirror QuickChart")

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def generate_chart(
        type: str,
        datasets: list[dict[str, Any]],
        labels: list[str] | None = None,
        title: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成固定 quickchart.io 域名的 Chart.js 图表 URL。"""

        return quickchart_url(type, datasets, labels, title, options)

    return mcp


def _json_response(client: SafeHttpClient, url: str, *, provider: str) -> Any:
    response = client.request(
        url,
        headers={"User-Agent": GEO_USER_AGENT, "Accept": "application/json"},
    )
    _require_success(response.status, provider)
    try:
        return json.loads(response.text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{provider} 返回了无效 JSON。") from exc


def _airbnb_geocode(client: SafeHttpClient, location: str) -> tuple[float, float, float, float] | None:
    photon_url = "https://photon.komoot.io/api/?" + urlencode(
        {"q": location, "limit": "5"}
    )
    try:
        data = _json_response(client, photon_url, provider="Photon")
        features = data.get("features", []) if isinstance(data, dict) else []
        priorities = {
            "country": 1, "state": 2, "county": 3, "city": 4,
            "district": 5, "locality": 6, "street": 7, "house": 8,
        }
        features = sorted(
            (item for item in features if isinstance(item, dict)),
            key=lambda item: priorities.get(item.get("properties", {}).get("type"), 9),
        )
        for feature in features:
            extent = feature.get("properties", {}).get("extent")
            if isinstance(extent, list) and len(extent) == 4:
                west, north, east, south = (float(item) for item in extent)
                return south, north, west, east
    except (NetworkPolicyError, ValueError, TypeError):
        pass
    nominatim_url = "https://nominatim.openstreetmap.org/search?" + urlencode(
        {"q": location, "format": "jsonv2", "limit": "1"}
    )
    try:
        data = _json_response(client, nominatim_url, provider="Nominatim")
        box = data[0].get("boundingbox") if isinstance(data, list) and data else None
        if isinstance(box, list) and len(box) == 4:
            south, north, west, east = (float(item) for item in box)
            return south, north, west, east
    except (NetworkPolicyError, ValueError, TypeError):
        pass
    return None


def _airbnb_script_data(html: str) -> Any:
    parser = _ScriptCapture("data-deferred-state-0")
    parser.feed(html)
    raw = unescape("".join(parser.parts)).strip()
    if not raw:
        raise ValueError("Airbnb 页面缺少固定数据节点，上游页面结构可能已经漂移。")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Airbnb 固定数据节点无法解析，上游页面结构可能已经漂移。") from exc


def _find_airbnb_branch(data: Any, branch: tuple[str, ...]) -> Any:
    entries = data.get("niobeClientData", []) if isinstance(data, dict) else []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        current = entry[1]
        try:
            for key in branch:
                current = current[key]
            return current
        except (KeyError, TypeError):
            continue
    raise ValueError("Airbnb 页面数据契约已漂移，未找到预期结果节点。")


def _decode_listing_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        candidate = decoded.rsplit(":", 1)[-1]
    except (ValueError, UnicodeDecodeError):
        candidate = raw
    return candidate if LISTING_ID.fullmatch(candidate) else None


def _airbnb_date(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    clean = str(value).strip()
    if not DATE_VALUE.fullmatch(clean):
        raise ValueError(f"{name} 必须使用 YYYY-MM-DD 格式。")
    return clean


def airbnb_search_payload(
    location: str,
    checkin: str | None = None,
    checkout: str | None = None,
    adults: int = 1,
    children: int = 0,
    minPrice: float | None = None,
    maxPrice: float | None = None,
) -> dict[str, Any]:
    clean_location = _bounded_string(location, "location", maximum=256)
    adults_value = int(_finite(adults, "adults", 1, 16))
    children_value = int(_finite(children, "children", 0, 16))
    slug = quote(re.sub(r"\s+", "-", re.sub(r",\s*", "--", clean_location)), safe="-")
    query: dict[str, str] = {
        "adults": str(adults_value),
        "children": str(children_value),
    }
    if value := _airbnb_date(checkin, "checkin"):
        query["checkin"] = value
    if value := _airbnb_date(checkout, "checkout"):
        query["checkout"] = value
    if minPrice is not None:
        query["price_min"] = str(int(_finite(minPrice, "minPrice", 0, 1_000_000)))
    if maxPrice is not None:
        query["price_max"] = str(int(_finite(maxPrice, "maxPrice", 0, 1_000_000)))
    client = SafeHttpClient(
        allowed_hosts=AIRBNB_HOSTS,
        max_response_bytes=MAX_AIRBNB_BYTES,
        minimum_intervals={"nominatim.openstreetmap.org": 1.0},
    )
    if bounds := _airbnb_geocode(client, clean_location):
        south, north, west, east = bounds
        lat_pad = max((north - south) * 0.25, 0.1)
        lon_pad = max((east - west) * 0.25, 0.1)
        query.update(
            {
                "sw_lat": f"{max(south - lat_pad, -90):.7f}",
                "ne_lat": f"{min(north + lat_pad, 90):.7f}",
                "sw_lng": f"{max(west - lon_pad, -180):.7f}",
                "ne_lng": f"{min(east + lon_pad, 180):.7f}",
            }
        )
    url = f"https://www.airbnb.com/s/{slug}/homes?{urlencode(query)}"
    client.assert_robots_allowed(url, AIRBNB_USER_AGENT)
    response = client.request(
        url,
        headers={
            "User-Agent": AIRBNB_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
        max_response_bytes=MAX_AIRBNB_BYTES,
    )
    _require_success(response.status, "Airbnb")
    data = _airbnb_script_data(response.text())
    results = _find_airbnb_branch(
        data,
        ("data", "presentation", "staysSearch", "results"),
    )
    rows = results.get("searchResults", []) if isinstance(results, dict) else []
    output = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        listing = row.get("demandStayListing") or {}
        listing_id = _decode_listing_id(listing.get("id"))
        if not listing_id:
            continue
        display = row.get("structuredContent") or {}
        price = row.get("structuredDisplayPrice") or {}
        output.append(
            {
                "id": listing_id,
                "url": f"https://www.airbnb.com/rooms/{listing_id}",
                "description": listing.get("description"),
                "location": listing.get("location"),
                "rating": row.get("avgRatingA11yLabel"),
                "primary": (display.get("primaryLine") or {}).get("body"),
                "secondary": (display.get("secondaryLine") or {}).get("body"),
                "price": ((price.get("primaryLine") or {}).get("accessibilityLabel")),
            }
        )
    return _bounded_result(
        {
            "search_url": response.url,
            "results": output,
            "result_count": len(output),
            "robots_respected": True,
            "note": "房源信息来自公开页面，价格和可订状态需在官方页面复核。",
        }
    )


def airbnb_details_payload(
    id: str,
    checkin: str | None = None,
    checkout: str | None = None,
    adults: int = 1,
) -> dict[str, Any]:
    listing_id = _bounded_string(id, "id", maximum=24)
    if not LISTING_ID.fullmatch(listing_id):
        raise ValueError("Airbnb 房源 ID 只能包含数字。")
    query = {"adults": str(int(_finite(adults, "adults", 1, 16)))}
    if value := _airbnb_date(checkin, "checkin"):
        query["check_in"] = value
    if value := _airbnb_date(checkout, "checkout"):
        query["check_out"] = value
    url = f"https://www.airbnb.com/rooms/{listing_id}?{urlencode(query)}"
    client = SafeHttpClient(
        allowed_hosts=AIRBNB_HOSTS,
        max_response_bytes=MAX_AIRBNB_BYTES,
    )
    client.assert_robots_allowed(url, AIRBNB_USER_AGENT)
    response = client.request(
        url,
        headers={
            "User-Agent": AIRBNB_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
        max_response_bytes=MAX_AIRBNB_BYTES,
    )
    _require_success(response.status, "Airbnb")
    data = _airbnb_script_data(response.text())
    sections = _find_airbnb_branch(
        data,
        ("data", "presentation", "stayProductDetailPage", "sections", "sections"),
    )
    allowed_sections = {
        "LOCATION_DEFAULT",
        "POLICIES_DEFAULT",
        "HIGHLIGHTS_DEFAULT",
        "DESCRIPTION_DEFAULT",
        "AMENITIES_DEFAULT",
    }
    output = []
    for item in sections if isinstance(sections, list) else []:
        if not isinstance(item, dict) or item.get("sectionId") not in allowed_sections:
            continue
        section = item.get("section")
        if isinstance(section, dict):
            raw = json.dumps(section, ensure_ascii=False)
            output.append(
                {
                    "section_id": item.get("sectionId"),
                    "content": json.loads(raw[:24_000]) if len(raw) <= 24_000 else {"truncated": True},
                }
            )
    return _bounded_result(
        {
            "listing_url": response.url,
            "sections": output,
            "robots_respected": True,
        }
    )


def build_airbnb() -> FastMCP:
    mcp = FastMCP("ModelMirror Airbnb")

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def airbnb_search(
        location: str,
        checkin: str | None = None,
        checkout: str | None = None,
        adults: int = 1,
        children: int = 0,
        minPrice: float | None = None,
        maxPrice: float | None = None,
    ) -> dict[str, Any]:
        """搜索公开 Airbnb 房源；固定遵守 robots.txt，最多返回 10 项。"""

        return airbnb_search_payload(
            location, checkin, checkout, adults, children, minPrice, maxPrice
        )

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def airbnb_listing_details(
        id: str,
        checkin: str | None = None,
        checkout: str | None = None,
        adults: int = 1,
    ) -> dict[str, Any]:
        """读取一个数字房源 ID 的公开详情；固定遵守 robots.txt。"""

        return airbnb_details_payload(id, checkin, checkout, adults)

    return mcp


def _geo_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=GEOWIRE_HOSTS,
        max_response_bytes=MAX_FETCH_BYTES,
        minimum_intervals={"nominatim.openstreetmap.org": 1.0},
    )


def _nominatim_places(data: Any) -> list[dict[str, Any]]:
    rows = data if isinstance(data, list) else [data]
    output = []
    for row in rows[:MAX_GEO_RESULTS]:
        if not isinstance(row, dict):
            continue
        output.append(
            {
                "name": row.get("display_name") or row.get("name"),
                "latitude": float(row["lat"]) if row.get("lat") is not None else None,
                "longitude": float(row["lon"]) if row.get("lon") is not None else None,
                "type": row.get("type"),
                "category": row.get("category"),
                "address": row.get("address"),
                "source": "OpenStreetMap / Nominatim",
                "attribution": "© OpenStreetMap contributors",
            }
        )
    return output


def geowire_search_payload(
    query: str,
    near: dict[str, Any] | None = None,
    radiusMeters: int | None = None,
    country: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=256)
    limit_value = int(_finite(limit, "limit", 1, MAX_GEO_RESULTS))
    params: dict[str, str] = {
        "q": clean_query,
        "format": "jsonv2",
        "addressdetails": "1",
        "limit": str(limit_value),
    }
    if country:
        clean_country = _bounded_string(country, "country", maximum=2).lower()
        if not re.fullmatch(r"[a-z]{2}", clean_country):
            raise ValueError("country 必须是两位国家代码。")
        params["countrycodes"] = clean_country
    if near is not None:
        point = _coordinates(near, "near")
        radius = _finite(radiusMeters or 25_000, "radiusMeters", 100, 50_000)
        lat_delta = radius / 111_320
        lon_delta = radius / (111_320 * max(math.cos(math.radians(point["latitude"])), 0.01))
        params["viewbox"] = ",".join(
            str(value)
            for value in (
                max(point["longitude"] - lon_delta, -180),
                min(point["latitude"] + lat_delta, 90),
                min(point["longitude"] + lon_delta, 180),
                max(point["latitude"] - lat_delta, -90),
            )
        )
        if radiusMeters is not None:
            params["bounded"] = "1"
    data = _json_response(
        _geo_client(),
        "https://nominatim.openstreetmap.org/search?" + urlencode(params),
        provider="Nominatim",
    )
    return _bounded_result({"results": _nominatim_places(data), "provider": "nominatim"})


def geowire_geocode_payload(
    address: str,
    country: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    return geowire_search_payload(address, country=country, limit=limit)


def geowire_reverse_payload(location: dict[str, Any]) -> dict[str, Any]:
    point = _coordinates(location, "location")
    params = urlencode(
        {
            "lat": point["latitude"],
            "lon": point["longitude"],
            "format": "jsonv2",
            "addressdetails": "1",
        }
    )
    data = _json_response(
        _geo_client(),
        "https://nominatim.openstreetmap.org/reverse?" + params,
        provider="Nominatim",
    )
    return _bounded_result({"results": _nominatim_places(data), "provider": "nominatim"})


def geowire_directions_payload(
    waypoints: list[dict[str, Any]],
    mode: str = "driving",
    geometry: bool = False,
    alternatives: bool = False,
) -> dict[str, Any]:
    if mode != "driving":
        raise ValueError("公共 OSRM 适配器仅开放 driving 模式。")
    if not isinstance(waypoints, list) or not 2 <= len(waypoints) <= 8:
        raise ValueError("waypoints 必须包含 2 到 8 个坐标。")
    points = [_coordinates(item, f"waypoints[{index}]") for index, item in enumerate(waypoints)]
    coords = ";".join(f"{point['longitude']},{point['latitude']}" for point in points)
    query = urlencode(
        {
            "overview": "full" if geometry else "false",
            "geometries": "geojson",
            "steps": "false",
            "alternatives": "true" if alternatives else "false",
        }
    )
    data = _json_response(
        _geo_client(),
        f"https://router.project-osrm.org/route/v1/driving/{coords}?{query}",
        provider="OSRM",
    )
    if not isinstance(data, dict) or data.get("code") != "Ok":
        raise ValueError("OSRM 没有返回可用路线。")
    routes = []
    for route in data.get("routes", [])[:3]:
        if not isinstance(route, dict):
            continue
        item = {
            "distanceMeters": route.get("distance"),
            "durationSeconds": route.get("duration"),
            "legs": [
                {
                    "distanceMeters": leg.get("distance"),
                    "durationSeconds": leg.get("duration"),
                }
                for leg in route.get("legs", [])
                if isinstance(leg, dict)
            ],
        }
        if geometry:
            item["geometry"] = route.get("geometry")
        routes.append(item)
    return _bounded_result({"routes": routes, "provider": "osrm"})


def geowire_matrix_payload(
    origins: list[dict[str, Any]],
    destinations: list[dict[str, Any]],
    mode: str = "driving",
) -> dict[str, Any]:
    if mode != "driving":
        raise ValueError("公共 OSRM 适配器仅开放 driving 模式。")
    if not isinstance(origins, list) or not 1 <= len(origins) <= 5:
        raise ValueError("origins 必须包含 1 到 5 个坐标。")
    if not isinstance(destinations, list) or not 1 <= len(destinations) <= 5:
        raise ValueError("destinations 必须包含 1 到 5 个坐标。")
    source_points = [_coordinates(item, f"origins[{index}]") for index, item in enumerate(origins)]
    target_points = [_coordinates(item, f"destinations[{index}]") for index, item in enumerate(destinations)]
    points = [*source_points, *target_points]
    coords = ";".join(f"{point['longitude']},{point['latitude']}" for point in points)
    query = urlencode(
        {
            "sources": ";".join(str(index) for index in range(len(source_points))),
            "destinations": ";".join(
                str(index) for index in range(len(source_points), len(points))
            ),
            "annotations": "duration,distance",
        }
    )
    data = _json_response(
        _geo_client(),
        f"https://router.project-osrm.org/table/v1/driving/{coords}?{query}",
        provider="OSRM",
    )
    if not isinstance(data, dict) or data.get("code") != "Ok":
        raise ValueError("OSRM 没有返回可用距离矩阵。")
    return _bounded_result(
        {
            "durations": data.get("durations", []),
            "distances": data.get("distances", []),
            "provider": "osrm",
        }
    )


def geowire_providers_payload() -> dict[str, Any]:
    return {
        "providers": [
            {
                "id": "nominatim",
                "capabilities": ["search_places", "geocode_address", "reverse_geocode"],
                "rate_limit": "1 request/second",
                "attribution": "© OpenStreetMap contributors",
            },
            {
                "id": "osrm",
                "capabilities": ["get_directions", "distance_matrix"],
                "mode": "driving",
                "attribution": "OpenStreetMap / OSRM",
            },
        ],
        "credentials": "none",
    }


def build_geowire() -> FastMCP:
    mcp = FastMCP("ModelMirror GeoWire")

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search_places(
        query: str,
        near: dict[str, Any] | None = None,
        radiusMeters: int | None = None,
        country: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """使用 Nominatim 搜索地点，最多返回 10 项。"""

        return geowire_search_payload(query, near, radiusMeters, country, limit)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def geocode_address(
        address: str,
        country: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """把地址转换为坐标和规范化地址。"""

        return geowire_geocode_payload(address, country, limit)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def reverse_geocode(location: dict[str, Any]) -> dict[str, Any]:
        """把经纬度转换为附近地址。"""

        return geowire_reverse_payload(location)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_directions(
        waypoints: list[dict[str, Any]],
        mode: str = "driving",
        geometry: bool = False,
        alternatives: bool = False,
    ) -> dict[str, Any]:
        """通过公共 OSRM 获取 2 到 8 个途经点之间的驾车路线。"""

        return geowire_directions_payload(waypoints, mode, geometry, alternatives)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def distance_matrix(
        origins: list[dict[str, Any]],
        destinations: list[dict[str, Any]],
        mode: str = "driving",
    ) -> dict[str, Any]:
        """通过公共 OSRM 计算最多 5×5 的驾车距离和时间矩阵。"""

        return geowire_matrix_payload(origins, destinations, mode)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def list_geo_providers() -> dict[str, Any]:
        """列出本批固定启用的零凭据地理数据提供方。"""

        return geowire_providers_payload()

    return mcp


def _json_response(response: Any, provider: str) -> Any:
    _require_success(response.status, provider)
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        raise ValueError(f"{provider} returned a non-JSON response.")
    try:
        return json.loads(response.text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{provider} returned invalid JSON.") from exc


def _clean_public_text(value: Any, *, maximum: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _public_slug(value: Any, name: str) -> str:
    clean = str(value or "").strip().lower()
    if not PUBLIC_SLUG.fullmatch(clean):
        raise ValueError(f"{name} must be a lowercase repository slug.")
    return clean


def _search_result_url(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("//duckduckgo.com/l/"):
        raw = "https:" + raw
    elif raw.startswith("/l/"):
        raw = "https://duckduckgo.com" + raw
    parsed = urlsplit(raw)
    if parsed.hostname == "duckduckgo.com" and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        raw = unquote(target)
        parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or len(raw) > 4096
    ):
        return ""
    return raw


class _DuckDuckGoResults(HTMLParser):
    """Parse only the stable title/snippet markers reviewed in v0.6.1."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None
        self._capture = ""
        self._capture_tag = ""
        self._capture_depth = 0

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return {item for item in value.split() if item}

    def _finish_current(self) -> None:
        current = self._current
        if current is None:
            return
        title = _clean_public_text("".join(current["title"]), maximum=500)
        snippet = _clean_public_text("".join(current["snippet"]), maximum=2000)
        url = _search_result_url(current["url"])
        if title and url:
            self.results.append({"title": title, "url": url, "snippet": snippet})
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        if tag == "a" and "result__a" in classes:
            self._finish_current()
            self._current = {
                "title": [],
                "snippet": [],
                "url": dict(attrs).get("href") or "",
            }
            self._capture = "title"
            self._capture_tag = tag
            self._capture_depth = 1
            return
        if self._current is not None and "result__snippet" in classes:
            self._capture = "snippet"
            self._capture_tag = tag
            self._capture_depth = 1
            return
        if self._capture:
            self._capture_depth += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        return None

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        self._capture_depth -= 1
        if self._capture_depth <= 0:
            self._capture = ""
            self._capture_tag = ""
            self._capture_depth = 0

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            self._current[self._capture].append(data)

    def close(self) -> None:
        super().close()
        self._finish_current()


def duckduckgo_search_payload(
    query: str,
    max_results: int = 10,
    region: str = "",
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=500)
    count = int(max_results)
    if not 1 <= count <= 20:
        raise ValueError("max_results must be between 1 and 20.")
    clean_region = str(region or "").strip().lower()
    if clean_region and not DUCKDUCKGO_REGION.fullmatch(clean_region):
        raise ValueError("region must use the fixed country-language form, such as us-en.")
    body = urlencode(
        {"q": clean_query, "b": "", "kl": clean_region, "kp": "1"}
    ).encode("utf-8")
    client = client or SafeHttpClient(
        allowed_hosts=DUCKDUCKGO_HOSTS,
        timeout=15,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"html.duckduckgo.com": 2.0},
    )
    response = client.request(
        "https://html.duckduckgo.com/html",
        method="POST",
        headers={
            "User-Agent": DUCKDUCKGO_USER_AGENT,
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=body,
    )
    if response.status != 200 or not response.body:
        raise ValueError("DuckDuckGo search is temporarily unavailable.")
    parser = _DuckDuckGoResults()
    parser.feed(response.text())
    parser.close()
    results = parser.results[:count]
    if not results:
        raise ValueError("DuckDuckGo returned no verifiable search results.")
    return _bounded_result(
        {
            "query": clean_query,
            "region": clean_region,
            "safe_search": "strict",
            "count": len(results),
            "results": results,
            "provider": "DuckDuckGo",
            "notice": "Result titles, snippets and links are untrusted public web content.",
        }
    )


def _shadcn_component_entries(
    *, client: SafeHttpClient | None = None
) -> list[dict[str, Any]]:
    client = client or SafeHttpClient(
        allowed_hosts=SHADCN_HOSTS,
        timeout=15,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"api.github.com": 60.0},
    )
    response = client.request(
        "https://api.github.com/repos/shadcn-ui/ui/contents/"
        f"{SHADCN_COMPONENT_PATH}?ref={SHADCN_UI_COMMIT}",
        headers={
            "User-Agent": SHADCN_USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    payload = _json_response(response, "GitHub")
    if not isinstance(payload, list) or len(payload) > 256:
        raise ValueError("shadcn/ui returned an invalid component directory.")
    prefix = SHADCN_COMPONENT_PATH + "/"
    entries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        filename = str(item.get("name") or "")
        if not filename.endswith(".tsx"):
            continue
        component = filename.removesuffix(".tsx")
        path = str(item.get("path") or "")
        sha = str(item.get("sha") or "")
        size = item.get("size")
        if (
            not SHADCN_COMPONENT.fullmatch(component)
            or path != prefix + filename
            or not GIT_OBJECT_SHA.fullmatch(sha)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= 1024 * 1024
        ):
            raise ValueError("shadcn/ui component metadata drifted.")
        entries.append(
            {"name": component, "path": path, "sha": sha, "size": size}
        )
    entries.sort(key=lambda item: item["name"])
    if not entries or len(entries) > MAX_PUBLIC_DIRECTORY_RESULTS:
        raise ValueError("shadcn/ui component count drifted outside the reviewed limit.")
    return entries


def shadcn_list_components_payload(
    *,
    client: SafeHttpClient | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = entries if entries is not None else _shadcn_component_entries(client=client)
    return _bounded_result(
        {
            "components": [item["name"] for item in entries],
            "total": len(entries),
            "repository": "shadcn-ui/ui",
            "commit": SHADCN_UI_COMMIT,
        }
    )


def shadcn_component_metadata_payload(
    componentName: str,
    *,
    client: SafeHttpClient | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    component = _bounded_string(componentName, "componentName", maximum=80).lower()
    if not SHADCN_COMPONENT.fullmatch(component):
        raise ValueError("componentName must be a normalized shadcn/ui component slug.")
    entry = next(
        (
            item
            for item in (
                entries
                if entries is not None
                else _shadcn_component_entries(client=client)
            )
            if item["name"] == component
        ),
        None,
    )
    if entry is None:
        raise ValueError("The requested component is not present in the pinned registry.")
    return _bounded_result(
        {
            **entry,
            "type": "registry:ui",
            "repository": "shadcn-ui/ui",
            "commit": SHADCN_UI_COMMIT,
            "source_url": (
                "https://github.com/shadcn-ui/ui/blob/"
                f"{SHADCN_UI_COMMIT}/{entry['path']}"
            ),
        }
    )


def _docker_hub_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=DOCKER_HUB_HOSTS,
        timeout=15,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"hub.docker.com": 1.0},
    )


def _docker_hub_json(url: str, *, client: SafeHttpClient | None = None) -> Any:
    response = (client or _docker_hub_client()).request(
        url,
        headers={
            "User-Agent": DOCKER_HUB_USER_AGENT,
            "Accept": "application/json",
        },
    )
    return _json_response(response, "Docker Hub")


def docker_hub_search_payload(
    query: str,
    max_results: int = 10,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=200)
    count = int(max_results)
    if not 1 <= count <= 25:
        raise ValueError("max_results must be between 1 and 25.")
    payload = _docker_hub_json(
        "https://hub.docker.com/api/search/v4?custom_boosted_results=true&"
        + urlencode({"query": clean_query, "from": 0, "size": count}),
        client=client,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Docker Hub search response drifted.")
    results: list[dict[str, Any]] = []
    for item in payload["results"][:count]:
        if not isinstance(item, dict):
            raise ValueError("Docker Hub search result drifted.")
        publisher = item.get("publisher") if isinstance(item.get("publisher"), dict) else {}
        results.append(
            {
                "name": _clean_public_text(item.get("name"), maximum=255),
                "type": _clean_public_text(item.get("type"), maximum=40),
                "publisher": _clean_public_text(publisher.get("name"), maximum=160),
                "description": _clean_public_text(item.get("short_description"), maximum=1000),
                "badge": _clean_public_text(item.get("badge"), maximum=40),
                "stars": int(item.get("star_count") or 0),
                "pulls": _clean_public_text(item.get("pull_count"), maximum=80),
                "updated_at": _clean_public_text(item.get("updated_at"), maximum=80),
                "archived": bool(item.get("archived")),
            }
        )
    if any(not item["name"] for item in results):
        raise ValueError("Docker Hub search result is missing a repository name.")
    return _bounded_result(
        {
            "query": clean_query,
            "count": len(results),
            "total": int(payload.get("total") or len(results)),
            "results": results,
        }
    )


def docker_hub_repository_payload(
    namespace: str,
    repository: str,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_namespace = _public_slug(namespace, "namespace")
    clean_repository = _public_slug(repository, "repository")
    payload = _docker_hub_json(
        "https://hub.docker.com/v2/namespaces/"
        f"{quote(clean_namespace, safe='')}/repositories/{quote(clean_repository, safe='')}",
        client=client,
    )
    if not isinstance(payload, dict):
        raise ValueError("Docker Hub repository response drifted.")
    result = {
        "namespace": _clean_public_text(payload.get("namespace"), maximum=128),
        "name": _clean_public_text(payload.get("name"), maximum=128),
        "description": _clean_public_text(payload.get("description"), maximum=2000),
        "is_private": bool(payload.get("is_private")),
        "status_description": _clean_public_text(payload.get("status_description"), maximum=160),
        "star_count": int(payload.get("star_count") or 0),
        "pull_count": int(payload.get("pull_count") or 0),
        "last_updated": _clean_public_text(payload.get("last_updated"), maximum=80),
        "date_registered": _clean_public_text(payload.get("date_registered"), maximum=80),
        "media_types": [
            _clean_public_text(item, maximum=160)
            for item in (payload.get("media_types") or [])[:20]
        ],
        "content_types": [
            _clean_public_text(item, maximum=160)
            for item in (payload.get("content_types") or [])[:20]
        ],
    }
    if result["namespace"] != clean_namespace or result["name"] != clean_repository:
        raise ValueError("Docker Hub repository identity drifted.")
    return _bounded_result(result)


def docker_hub_tags_payload(
    repository: str,
    namespace: str = "library",
    page: int = 1,
    page_size: int = 10,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_namespace = _public_slug(namespace, "namespace")
    clean_repository = _public_slug(repository, "repository")
    clean_page = int(page)
    clean_size = int(page_size)
    if not 1 <= clean_page <= 1000 or not 1 <= clean_size <= 25:
        raise ValueError("page must be 1..1000 and page_size must be 1..25.")
    payload = _docker_hub_json(
        "https://hub.docker.com/v2/namespaces/"
        f"{quote(clean_namespace, safe='')}/repositories/{quote(clean_repository, safe='')}/tags?"
        + urlencode({"page": clean_page, "page_size": clean_size}),
        client=client,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Docker Hub tags response drifted.")
    tags: list[dict[str, Any]] = []
    for item in payload["results"][:clean_size]:
        if not isinstance(item, dict):
            raise ValueError("Docker Hub tag result drifted.")
        images: list[dict[str, Any]] = []
        for image in (item.get("images") or [])[:20]:
            if not isinstance(image, dict):
                continue
            images.append(
                {
                    "architecture": _clean_public_text(image.get("architecture"), maximum=80),
                    "os": _clean_public_text(image.get("os"), maximum=80),
                    "variant": _clean_public_text(image.get("variant"), maximum=80),
                    "digest": _clean_public_text(image.get("digest"), maximum=160),
                    "size": int(image.get("size") or 0),
                    "status": _clean_public_text(image.get("status"), maximum=40),
                    "last_pushed": _clean_public_text(image.get("last_pushed"), maximum=80),
                }
            )
        name = _clean_public_text(item.get("name"), maximum=128)
        if not name:
            raise ValueError("Docker Hub tag result is missing a name.")
        tags.append(
            {
                "name": name,
                "last_updated": _clean_public_text(item.get("last_updated"), maximum=80),
                "full_size": int(item.get("full_size") or 0),
                "images": images,
            }
        )
    return _bounded_result(
        {
            "namespace": clean_namespace,
            "repository": clean_repository,
            "page": clean_page,
            "page_size": clean_size,
            "count": len(tags),
            "total": int(payload.get("count") or len(tags)),
            "tags": tags,
        }
    )


def _biomcp_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=BIOMCP_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={
            "www.ebi.ac.uk": 2.0,
            "clinicaltrials.gov": 2.0,
            "myvariant.info": 2.0,
        },
    )


def _biomcp_json(url: str, *, client: SafeHttpClient | None = None) -> Any:
    response = (client or _biomcp_client()).request(
        url,
        headers={"User-Agent": BIOMCP_USER_AGENT, "Accept": "application/json"},
    )
    return _json_response(response, "BioMCP public provider")


def _biomcp_article(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("BioMCP article response drifted.")
    identifier = str(item.get("pmid") or item.get("pmcid") or "").strip()
    if not identifier:
        raise ValueError("BioMCP article response omitted its public identifier.")
    return {
        "id": _clean_public_text(identifier, maximum=40),
        "title": _clean_public_text(item.get("title"), maximum=1000),
        "authors": _clean_public_text(item.get("authorString"), maximum=1000),
        "journal": _clean_public_text(item.get("journalTitle"), maximum=300),
        "year": _clean_public_text(item.get("pubYear"), maximum=8),
        "cited_by_count": int(item.get("citedByCount") or 0),
        "open_access": str(item.get("isOpenAccess") or "").upper() == "Y",
    }


def _biomcp_trial(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("BioMCP trial response drifted.")
    protocol = item.get("protocolSection")
    if not isinstance(protocol, dict):
        raise ValueError("BioMCP trial response omitted protocolSection.")
    identification = protocol.get("identificationModule")
    status = protocol.get("statusModule")
    design = protocol.get("designModule")
    conditions = protocol.get("conditionsModule")
    if not isinstance(identification, dict) or not isinstance(status, dict):
        raise ValueError("BioMCP trial identity/status response drifted.")
    clean_conditions = conditions if isinstance(conditions, dict) else {}
    clean_design = design if isinstance(design, dict) else {}
    nct_id = str(identification.get("nctId") or "").upper()
    if not NCT_ID.fullmatch(nct_id):
        raise ValueError("BioMCP trial response omitted a valid NCT identifier.")
    return {
        "id": nct_id,
        "title": _clean_public_text(
            identification.get("briefTitle") or identification.get("officialTitle"),
            maximum=1000,
        ),
        "status": _clean_public_text(status.get("overallStatus"), maximum=80),
        "study_type": _clean_public_text(clean_design.get("studyType"), maximum=80),
        "conditions": [
            _clean_public_text(value, maximum=200)
            for value in (clean_conditions.get("conditions") or [])[:20]
            if _clean_public_text(value, maximum=200)
        ],
        "last_updated": _clean_public_text(
            status.get("studyFirstPostDateStruct", {}).get("date")
            if isinstance(status.get("studyFirstPostDateStruct"), dict)
            else "",
            maximum=40,
        ),
    }


def _biomcp_variant(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("BioMCP variant response drifted.")
    variant_id = _clean_public_text(item.get("_id"), maximum=300)
    if not variant_id:
        raise ValueError("BioMCP variant response omitted its identifier.")
    gene = item.get("gene") if isinstance(item.get("gene"), dict) else {}
    dbsnp = item.get("dbsnp") if isinstance(item.get("dbsnp"), dict) else {}
    cadd = item.get("cadd") if isinstance(item.get("cadd"), dict) else {}
    return {
        "id": variant_id,
        "gene": _clean_public_text(gene.get("symbol"), maximum=80),
        "rsid": _clean_public_text(dbsnp.get("rsid"), maximum=80),
        "cadd_phred": cadd.get("phred")
        if isinstance(cadd.get("phred"), (int, float))
        and not isinstance(cadd.get("phred"), bool)
        else None,
        "clinvar": item.get("clinvar") if isinstance(item.get("clinvar"), dict) else {},
    }


def biomcp_search_payload(
    entity: Literal["article", "trial", "variant"],
    query: str,
    limit: int = 10,
    offset: int = 0,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    if entity not in {"article", "trial", "variant"}:
        raise ValueError("entity must be article, trial, or variant.")
    clean_query = _bounded_string(query, "query", maximum=300)
    clean_limit = int(limit)
    clean_offset = int(offset)
    if not 1 <= clean_limit <= 10 or not 0 <= clean_offset <= 1000:
        raise ValueError("limit must be 1..10 and offset must be 0..1000.")
    if entity == "article":
        if clean_offset % clean_limit:
            raise ValueError("article offset must be a multiple of limit.")
        payload = _biomcp_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            + urlencode(
                {
                    "query": clean_query,
                    "format": "json",
                    "pageSize": clean_limit,
                    "page": clean_offset // clean_limit + 1,
                }
            ),
            client=client,
        )
        result_list = payload.get("resultList") if isinstance(payload, dict) else None
        raw_results = result_list.get("result") if isinstance(result_list, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("BioMCP article search response drifted.")
        results = [_biomcp_article(item) for item in raw_results[:clean_limit]]
        total = int(payload.get("hitCount") or len(results))
        provider = "Europe PMC"
    elif entity == "trial":
        if clean_offset:
            raise ValueError("trial search currently requires offset=0.")
        payload = _biomcp_json(
            "https://clinicaltrials.gov/api/v2/studies?"
            + urlencode(
                {
                    "query.term": clean_query,
                    "pageSize": clean_limit,
                    "countTotal": "true",
                    "format": "json",
                }
            ),
            client=client,
        )
        raw_results = payload.get("studies") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("BioMCP trial search response drifted.")
        results = [_biomcp_trial(item) for item in raw_results[:clean_limit]]
        total = int(payload.get("totalCount") or len(results))
        provider = "ClinicalTrials.gov"
    else:
        payload = _biomcp_json(
            "https://myvariant.info/v1/query?"
            + urlencode(
                {
                    "q": clean_query,
                    "size": clean_limit,
                    "from": clean_offset,
                    "fields": "_id,gene.symbol,dbsnp.rsid,cadd.phred,clinvar",
                }
            ),
            client=client,
        )
        raw_results = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("BioMCP variant search response drifted.")
        results = [_biomcp_variant(item) for item in raw_results[:clean_limit]]
        total = int(payload.get("total") or len(results))
        provider = "MyVariant.info"
    return _bounded_result(
        {
            "entity": entity,
            "query": clean_query,
            "offset": clean_offset,
            "count": len(results),
            "total": total,
            "results": results,
            "provider": provider,
            "notice": "Public biomedical metadata only; this is not medical advice.",
        }
    )


def biomcp_get_payload(
    entity: Literal["article", "trial", "variant"],
    id: str,
    sections: list[str] | None = None,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_id = _bounded_string(id, "id", maximum=300)
    requested_sections = [str(value).strip().lower() for value in (sections or [])]
    if len(requested_sections) > 4 or len(set(requested_sections)) != len(
        requested_sections
    ):
        raise ValueError("sections must contain at most four unique reviewed values.")
    allowed_sections = {
        "article": {"summary"},
        "trial": {"summary", "status"},
        "variant": {"clinvar", "population"},
    }
    if entity not in allowed_sections or any(
        value not in allowed_sections[entity] for value in requested_sections
    ):
        raise ValueError("entity or sections are outside the reviewed BioMCP subset.")
    if entity == "article":
        if not PUBMED_ID.fullmatch(clean_id):
            raise ValueError("article id must be a numeric PubMed identifier.")
        payload = _biomcp_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            + urlencode(
                {
                    "query": f"EXT_ID:{clean_id}",
                    "format": "json",
                    "pageSize": 1,
                }
            ),
            client=client,
        )
        result_list = payload.get("resultList") if isinstance(payload, dict) else None
        values = result_list.get("result") if isinstance(result_list, dict) else None
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError("BioMCP article record was not found uniquely.")
        record = _biomcp_article(values[0])
        provider = "Europe PMC"
    elif entity == "trial":
        nct_id = clean_id.upper()
        if not NCT_ID.fullmatch(nct_id):
            raise ValueError("trial id must be an NCT identifier.")
        record = _biomcp_trial(
            _biomcp_json(
                "https://clinicaltrials.gov/api/v2/studies/"
                + quote(nct_id, safe=""),
                client=client,
            )
        )
        provider = "ClinicalTrials.gov"
    else:
        record = _biomcp_variant(
            _biomcp_json(
                "https://myvariant.info/v1/variant/"
                + quote(clean_id, safe="")
                + "?fields=_id,gene.symbol,dbsnp.rsid,cadd.phred,clinvar",
                client=client,
            )
        )
        provider = "MyVariant.info"
    return _bounded_result(
        {
            "entity": entity,
            "id": clean_id,
            "sections": requested_sections,
            "record": record,
            "provider": provider,
            "notice": "Public biomedical metadata only; this is not medical advice.",
        }
    )


def _safedep_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=SAFEDEP_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={
            "community-api.safedep.io": 2.0,
            "registry.npmjs.org": 2.0,
            "pypi.org": 2.0,
        },
        additional_allowed_headers=frozenset({"connect-protocol-version"}),
    )


def _parse_purl(purl: str, *, require_version: bool) -> tuple[str, str, str]:
    clean = _bounded_string(purl, "purl", maximum=512)
    match = PURL.fullmatch(clean)
    if not match:
        raise ValueError("purl must be a canonical npm or PyPI package URL.")
    ecosystem = match.group(1).lower()
    name = unquote(match.group(2)).strip()
    version = unquote(match.group(3) or "").strip()
    name_pattern = NPM_PACKAGE if ecosystem == "npm" else PYPI_PACKAGE
    if not name_pattern.fullmatch(name):
        raise ValueError("purl package name is outside the reviewed npm/PyPI grammar.")
    if (require_version and not version) or (version and not PACKAGE_VERSION.fullmatch(version)):
        raise ValueError("purl must include a bounded package version for this tool.")
    return ecosystem, name, version


def _safedep_package_version(ecosystem: str, name: str, version: str) -> dict[str, Any]:
    return {
        "packageVersion": {
            "package": {
                "ecosystem": "ECOSYSTEM_NPM" if ecosystem == "npm" else "ECOSYSTEM_PYPI",
                "name": name,
            },
            "version": version,
        }
    }


def _safedep_connect(
    service_method: str,
    payload: dict[str, Any],
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    response = (client or _safedep_client()).request(
        "https://community-api.safedep.io/" + service_method,
        method="POST",
        headers={
            "User-Agent": SAFEDEP_USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    value = _json_response(response, "SafeDep community API")
    if not isinstance(value, dict):
        raise ValueError("SafeDep community response drifted.")
    return value


def _safedep_identifier(value: Any) -> dict[str, str]:
    item = value if isinstance(value, dict) else {}
    return {
        "type": _clean_public_text(item.get("type"), maximum=80),
        "value": _clean_public_text(item.get("value"), maximum=160),
    }


def _safedep_vulnerability(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    identifier = _safedep_identifier(item.get("id"))
    if not identifier["value"]:
        raise ValueError("SafeDep vulnerability response omitted its identifier.")
    severities = []
    for raw in (item.get("severities") or [])[:10]:
        if not isinstance(raw, dict):
            continue
        severities.append(
            {
                "type": _clean_public_text(raw.get("type"), maximum=80),
                "score": _clean_public_text(raw.get("score"), maximum=300),
                "risk": _clean_public_text(raw.get("risk"), maximum=80),
            }
        )
    return {
        "id": identifier,
        "summary": _clean_public_text(item.get("summary"), maximum=1000),
        "aliases": [
            _safedep_identifier(raw) for raw in (item.get("aliases") or [])[:20]
        ],
        "severities": severities,
        "published_at": _clean_public_text(item.get("publishedAt"), maximum=80),
        "modified_at": _clean_public_text(item.get("modifiedAt"), maximum=80),
    }


def safedep_vulnerabilities_payload(
    purl: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    ecosystem, name, version = _parse_purl(purl, require_version=True)
    payload = _safedep_connect(
        "safedep.services.insights.v2.InsightService/GetPackageVersionVulnerabilities",
        _safedep_package_version(ecosystem, name, version),
        client=client,
    )
    values = payload.get("vulnerabilities") or []
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError("SafeDep vulnerability response drifted.")
    vulnerabilities = [_safedep_vulnerability(item) for item in values[:50]]
    return _bounded_result(
        {
            "purl": purl,
            "count": len(vulnerabilities),
            "truncated": len(values) > 50,
            "vulnerabilities": vulnerabilities,
            "provider": "SafeDep community insights",
        }
    )


def _safedep_insight(
    purl: str, *, client: SafeHttpClient | None = None
) -> tuple[dict[str, Any], str, str, str]:
    ecosystem, name, version = _parse_purl(purl, require_version=True)
    payload = _safedep_connect(
        "safedep.services.insights.v2.InsightService/GetPackageVersionInsight",
        _safedep_package_version(ecosystem, name, version),
        client=client,
    )
    insight = payload.get("insight")
    if not isinstance(insight, dict):
        raise ValueError("SafeDep package insight response drifted.")
    return insight, ecosystem, name, version


def safedep_popularity_payload(
    purl: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    insight, _, _, _ = _safedep_insight(purl, client=client)
    values = insight.get("projectInsights") or []
    if not isinstance(values, list):
        raise ValueError("SafeDep popularity response drifted.")
    projects: list[dict[str, Any]] = []
    for raw in values[:10]:
        if not isinstance(raw, dict):
            continue
        project = raw.get("project") if isinstance(raw.get("project"), dict) else {}
        scorecard = raw.get("scorecard") if isinstance(raw.get("scorecard"), dict) else {}
        projects.append(
            {
                "type": _clean_public_text(project.get("type"), maximum=80),
                "name": _clean_public_text(project.get("name"), maximum=300),
                "stars": int(raw.get("stars") or 0),
                "forks": int(raw.get("forks") or 0),
                "scorecard_score": scorecard.get("score")
                if isinstance(scorecard.get("score"), (int, float))
                and not isinstance(scorecard.get("score"), bool)
                else None,
            }
        )
    return _bounded_result({"purl": purl, "projects": projects, "provider": "SafeDep"})


def safedep_license_payload(
    purl: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    insight, _, _, _ = _safedep_insight(purl, client=client)
    licenses = insight.get("licenses")
    raw_values = licenses.get("licenses") if isinstance(licenses, dict) else []
    if not isinstance(raw_values, list):
        raise ValueError("SafeDep license response drifted.")
    values = []
    for raw in raw_values[:20]:
        if isinstance(raw, dict):
            value = raw.get("licenseId") or raw.get("license") or raw.get("name")
        else:
            value = raw
        clean = _clean_public_text(value, maximum=160)
        if clean:
            values.append(clean)
    return _bounded_result({"purl": purl, "licenses": values, "provider": "SafeDep"})


def safedep_malware_payload(
    purl: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    ecosystem, name, version = _parse_purl(purl, require_version=True)
    package_version = _safedep_package_version(ecosystem, name, version)["packageVersion"]
    payload = _safedep_connect(
        "safedep.services.malysis.v1.MalwareAnalysisService/QueryPackageAnalysis",
        {"target": {"packageVersion": package_version}},
        client=client,
    )
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    inference = report.get("inference") if isinstance(report.get("inference"), dict) else {}
    verification = (
        payload.get("verificationRecord")
        if isinstance(payload.get("verificationRecord"), dict)
        else {}
    )
    return _bounded_result(
        {
            "purl": purl,
            "status": _clean_public_text(payload.get("status"), maximum=80),
            "is_malware": bool(
                verification.get("isMalware", inference.get("isMalware", False))
            ),
            "confidence": _clean_public_text(inference.get("confidence"), maximum=80),
            "summary": _clean_public_text(
                verification.get("reason") or inference.get("summary"), maximum=1000
            ),
            "analyzed_at": _clean_public_text(report.get("analyzedAt"), maximum=80),
            "provider": "SafeDep community malware analysis",
        }
    )


def _registry_package_payload(
    purl: str, *, client: SafeHttpClient | None = None
) -> tuple[str, str, str, dict[str, Any]]:
    ecosystem, name, version = _parse_purl(purl, require_version=False)
    registry_client = client or _safedep_client()
    if ecosystem == "npm":
        url = "https://registry.npmjs.org/" + quote(name, safe="")
    else:
        url = "https://pypi.org/pypi/" + quote(name, safe="") + "/json"
    response = registry_client.request(
        url,
        headers={"User-Agent": SAFEDEP_USER_AGENT, "Accept": "application/json"},
    )
    payload = _json_response(response, "public package registry")
    if not isinstance(payload, dict):
        raise ValueError("Package registry response drifted.")
    return ecosystem, name, version, payload


def safedep_latest_version_payload(
    purl: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    ecosystem, name, _, payload = _registry_package_payload(purl, client=client)
    if ecosystem == "npm":
        dist_tags = payload.get("dist-tags")
        latest = dist_tags.get("latest") if isinstance(dist_tags, dict) else ""
    else:
        info = payload.get("info")
        latest = info.get("version") if isinstance(info, dict) else ""
    latest_version = _clean_public_text(latest, maximum=128)
    if not PACKAGE_VERSION.fullmatch(latest_version):
        raise ValueError("Package registry omitted a valid latest version.")
    return _bounded_result(
        {"ecosystem": ecosystem, "name": name, "version": latest_version}
    )


def safedep_available_versions_payload(
    purl: str,
    max_results: int = 50,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    count = int(max_results)
    if not 1 <= count <= 100:
        raise ValueError("max_results must be between 1 and 100.")
    ecosystem, name, _, payload = _registry_package_payload(purl, client=client)
    raw_versions = (
        payload.get("versions")
        if ecosystem == "npm"
        else payload.get("releases")
    )
    if not isinstance(raw_versions, dict):
        raise ValueError("Package registry versions response drifted.")
    values = sorted(
        (
            str(value)
            for value in raw_versions
            if PACKAGE_VERSION.fullmatch(str(value))
        ),
        reverse=True,
    )
    return _bounded_result(
        {
            "ecosystem": ecosystem,
            "name": name,
            "count": min(len(values), count),
            "total": len(values),
            "truncated": len(values) > count,
            "versions": values[:count],
        }
    )


def _open_websearch_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=OPEN_WEBSEARCH_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"cn.bing.com": 1.0, "html.duckduckgo.com": 2.0},
    )


def _bing_rss_search(
    query: str,
    limit: int,
    *,
    client: SafeHttpClient,
) -> list[dict[str, str]]:
    response = client.request(
        "https://cn.bing.com/search?"
        + urlencode({"q": query, "format": "rss", "count": limit}),
        headers={
            "User-Agent": OPEN_WEBSEARCH_USER_AGENT,
            "Accept": "application/rss+xml,application/xml,text/xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    if response.status != 200 or not response.body:
        raise ValueError("Bing search is temporarily unavailable.")
    try:
        root = ET.fromstring(response.body)
    except ET.ParseError as exc:
        raise ValueError("Bing search response drifted.") from exc
    results: list[dict[str, str]] = []
    for item in root.findall(".//item")[:limit]:
        title = _clean_public_text(item.findtext("title"), maximum=500)
        url = _search_result_url(item.findtext("link"))
        snippet = _clean_public_text(item.findtext("description"), maximum=2000)
        if title and url:
            results.append(
                {"title": title, "url": url, "snippet": snippet, "engine": "bing"}
            )
    if not results:
        raise ValueError("Bing returned no verifiable search results.")
    return results


def open_websearch_payload(
    query: str,
    limit: int = 10,
    engines: list[Literal["bing", "duckduckgo"]] | None = None,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=500)
    clean_limit = int(limit)
    if not 1 <= clean_limit <= 10:
        raise ValueError("limit must be between 1 and 10.")
    selected = list(engines or ["bing", "duckduckgo"])
    if not selected or len(selected) > 2 or len(set(selected)) != len(selected):
        raise ValueError("engines must contain one or two unique reviewed engines.")
    if any(item not in {"bing", "duckduckgo"} for item in selected):
        raise ValueError("engine is not available in the reviewed request-only subset.")
    policy_client = client or _open_websearch_client()
    by_engine: dict[str, list[dict[str, str]]] = {}
    for engine in selected:
        if engine == "bing":
            by_engine[engine] = _bing_rss_search(
                clean_query, clean_limit, client=policy_client
            )
        else:
            payload = duckduckgo_search_payload(
                clean_query,
                clean_limit,
                client=policy_client,
            )
            by_engine[engine] = [
                {**item, "engine": "duckduckgo"} for item in payload["results"]
            ]
    combined = [
        by_engine[engine][index]
        for index in range(clean_limit)
        for engine in selected
        if index < len(by_engine[engine])
    ]
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in combined:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        deduped.append(item)
        if len(deduped) >= clean_limit:
            break
    if not deduped:
        raise ValueError("Reviewed search engines returned no verifiable results.")
    return _bounded_result(
        {
            "query": clean_query,
            "engines": selected,
            "mode": "request-only",
            "safe_search": "strict",
            "count": len(deduped),
            "results": deduped,
            "notice": "Titles, snippets and links are untrusted public web content.",
        }
    )


IDEA_STOP_WORDS = frozenset(
    {"about", "after", "and", "before", "build", "could", "from", "into", "that", "their", "this", "using", "with"}
)


def _idea_keywords(idea_text: str) -> tuple[str, list[str]]:
    clean = _bounded_string(idea_text, "idea_text", maximum=1000)
    words: list[str] = []
    for match in IDEA_WORD.findall(clean):
        word = match.lower().strip(".-")
        if word in IDEA_STOP_WORDS or word in words:
            continue
        words.append(word)
        if len(words) >= 6:
            break
    if not words:
        raise ValueError("idea_text must include at least one searchable keyword.")
    return clean, words


def _idea_reality_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=IDEA_REALITY_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"api.github.com": 2.0, "hn.algolia.com": 1.0, "registry.npmjs.org": 1.0, "pypi.org": 1.0},
    )


class _PyPISearchResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "package-snippet" in classes and not self._href:
            self._href = str(attributes.get("href") or "")
            self._text = []
            self._depth = 1
        elif self._href:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._href:
            return
        self._depth -= 1
        if self._depth <= 0:
            path = self._href if self._href.startswith("/project/") else ""
            text = _clean_public_text(" ".join(self._text), maximum=1000)
            name = path.removeprefix("/project/").strip("/").split("/")[0]
            if name:
                self.results.append(
                    {"name": name, "description": text, "url": f"https://pypi.org/project/{quote(name, safe='')}"}
                )
            self._href = ""
            self._text = []
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)


def _idea_source_results(
    query: str,
    depth: Literal["quick", "deep"],
    *,
    client: SafeHttpClient,
) -> dict[str, list[dict[str, Any]]]:
    headers = {"User-Agent": IDEA_REALITY_USER_AGENT, "Accept": "application/json"}
    github = _json_response(
        client.request(
            "https://api.github.com/search/repositories?" + urlencode({"q": query, "per_page": 5}),
            headers=headers,
        ),
        "GitHub",
    )
    hn = _json_response(
        client.request(
            "https://hn.algolia.com/api/v1/search?" + urlencode({"query": query, "tags": "story", "hitsPerPage": 5}),
            headers=headers,
        ),
        "Hacker News",
    )
    if not isinstance(github, dict) or not isinstance(github.get("items"), list):
        raise ValueError("GitHub idea-search response drifted.")
    if not isinstance(hn, dict) or not isinstance(hn.get("hits"), list):
        raise ValueError("Hacker News idea-search response drifted.")
    output: dict[str, list[dict[str, Any]]] = {
        "github": [
            {
                "name": _clean_public_text(item.get("full_name"), maximum=200),
                "description": _clean_public_text(item.get("description"), maximum=1000),
                "url": _search_result_url(item.get("html_url")),
                "stars": int(item.get("stargazers_count") or 0),
            }
            for item in github["items"][:5]
            if isinstance(item, dict)
        ],
        "hacker_news": [
            {
                "name": _clean_public_text(item.get("title"), maximum=500),
                "description": "",
                "url": _search_result_url(item.get("url") or f"https://news.ycombinator.com/item?id={item.get('objectID', '')}"),
                "points": int(item.get("points") or 0),
            }
            for item in hn["hits"][:5]
            if isinstance(item, dict)
        ],
    }
    if depth == "deep":
        npm = _json_response(
            client.request(
                "https://registry.npmjs.org/-/v1/search?" + urlencode({"text": query, "size": 5}),
                headers=headers,
            ),
            "npm",
        )
        if not isinstance(npm, dict) or not isinstance(npm.get("objects"), list):
            raise ValueError("npm idea-search response drifted.")
        output["npm"] = [
            {
                "name": _clean_public_text((item.get("package") or {}).get("name"), maximum=214),
                "description": _clean_public_text((item.get("package") or {}).get("description"), maximum=1000),
                "url": _search_result_url((item.get("package") or {}).get("links", {}).get("npm")),
                "score": float((item.get("score") or {}).get("final") or 0.0),
            }
            for item in npm["objects"][:5]
            if isinstance(item, dict) and isinstance(item.get("package"), dict)
        ]
        response = client.request(
            "https://pypi.org/search/?" + urlencode({"q": query}),
            headers={"User-Agent": IDEA_REALITY_USER_AGENT, "Accept": "text/html"},
        )
        _require_success(response.status, "PyPI")
        parser = _PyPISearchResults()
        parser.feed(response.text())
        parser.close()
        output["pypi"] = parser.results[:5]
    return output


def idea_reality_payload(
    idea_text: str,
    depth: Literal["quick", "deep"] = "quick",
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    if depth not in {"quick", "deep"}:
        raise ValueError("depth must be quick or deep.")
    clean, keywords = _idea_keywords(idea_text)
    query = " ".join(keywords[:4])
    sources = _idea_source_results(query, depth, client=client or _idea_reality_client())
    total = sum(len(items) for items in sources.values())
    novelty_indicator = max(0, 100 - min(total, 20) * 4)
    return _bounded_result(
        {
            "idea_text": clean,
            "depth": depth,
            "query": query,
            "keywords": keywords,
            "sources_used": list(sources),
            "similar_result_count": total,
            "novelty_indicator": novelty_indicator,
            "results": sources,
            "notice": "This is a bounded public-source similarity check, not investment, legal, or product advice.",
        }
    )


def normalize_github_repository(repository: str) -> str:
    clean = str(repository or "").strip()
    if (
        not GITHUB_REPOSITORY.fullmatch(clean)
        or clean.endswith(".git")
        or "%" in clean
        or ".." in clean
    ):
        raise ValueError("repository must be a canonical GitHub owner/repository slug.")
    return clean.lower()


def _gitmcp_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=GITMCP_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=2 * 1024 * 1024,
        minimum_intervals={"api.github.com": 1.0},
    )


def _gitmcp_json(url: str, *, client: SafeHttpClient) -> Any:
    return _json_response(
        client.request(
            url,
            headers={"User-Agent": GITMCP_USER_AGENT, "Accept": "application/vnd.github+json"},
        ),
        "GitHub",
    )


def _gitmcp_snapshot(
    repository: str,
    *,
    client: SafeHttpClient,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    slug = normalize_github_repository(repository)
    if cache is not None and slug in cache:
        return cache[slug]
    metadata = _gitmcp_json(
        f"https://api.github.com/repos/{quote(slug, safe='/')}", client=client
    )
    if not isinstance(metadata, dict) or str(metadata.get("full_name") or "").lower() != slug:
        raise ValueError("GitHub repository identity drifted.")
    branch = _clean_public_text(metadata.get("default_branch"), maximum=255)
    if not branch:
        raise ValueError("GitHub repository has no default branch.")
    tree = _gitmcp_json(
        f"https://api.github.com/repos/{quote(slug, safe='/')}/git/trees/"
        f"{quote(branch, safe='')}?recursive=1",
        client=client,
    )
    if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
        raise ValueError("GitHub repository tree response drifted.")
    paths = [
        _clean_public_text(item.get("path"), maximum=1024)
        for item in tree["tree"][:20_000]
        if isinstance(item, dict) and item.get("type") == "blob"
    ]
    snapshot = {
        "repository": slug,
        "default_branch": branch,
        "description": _clean_public_text(metadata.get("description"), maximum=1000),
        "html_url": _search_result_url(metadata.get("html_url")),
        "tree_truncated": bool(tree.get("truncated")),
        "paths": paths,
    }
    if cache is not None:
        cache[slug] = snapshot
    return snapshot


def _gitmcp_readme(
    repository: str,
    *,
    client: SafeHttpClient,
) -> dict[str, str]:
    slug = normalize_github_repository(repository)
    payload = _gitmcp_json(
        f"https://api.github.com/repos/{quote(slug, safe='/')}/readme", client=client
    )
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise ValueError("GitHub README response drifted.")
    try:
        raw = base64.b64decode(str(payload.get("content") or ""), validate=False)
        text = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("GitHub README content is not valid UTF-8.") from exc
    if len(raw) > 128 * 1024:
        raise ValueError("GitHub README exceeds the reviewed 128 KiB limit.")
    return {
        "path": _clean_public_text(payload.get("path"), maximum=1024),
        "sha": _clean_public_text(payload.get("sha"), maximum=64),
        "text": text,
        "html_url": _search_result_url(payload.get("html_url")),
    }


def gitmcp_documentation_payload(
    repository: str,
    *,
    client: SafeHttpClient | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    policy_client = client or _gitmcp_client()
    snapshot = _gitmcp_snapshot(repository, client=policy_client, cache=cache)
    readme = _gitmcp_readme(repository, client=policy_client)
    return _bounded_result(
        {
            "repository": snapshot["repository"],
            "default_branch": snapshot["default_branch"],
            "description": snapshot["description"],
            "path": readme["path"],
            "sha": readme["sha"],
            "content": readme["text"][:64_000],
            "source_url": readme["html_url"],
            "notice": "Repository documentation is untrusted public content.",
        }
    )


def gitmcp_search_documentation_payload(
    repository: str,
    query: str,
    limit: int = 10,
    *,
    client: SafeHttpClient | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=200).lower()
    clean_limit = int(limit)
    if not 1 <= clean_limit <= 20:
        raise ValueError("limit must be between 1 and 20.")
    policy_client = client or _gitmcp_client()
    snapshot = _gitmcp_snapshot(repository, client=policy_client, cache=cache)
    readme = _gitmcp_readme(repository, client=policy_client)
    results: list[dict[str, Any]] = []
    if clean_query in readme["text"].lower():
        index = readme["text"].lower().find(clean_query)
        results.append(
            {
                "path": readme["path"],
                "excerpt": _clean_public_text(readme["text"][max(0, index - 160): index + 500], maximum=700),
                "source_url": readme["html_url"],
            }
        )
    for path in snapshot["paths"]:
        lower_path = path.lower()
        if clean_query not in lower_path or not lower_path.endswith((".md", ".mdx", ".rst", ".txt")):
            continue
        results.append({"path": path, "excerpt": "path match", "source_url": snapshot["html_url"]})
        if len(results) >= clean_limit:
            break
    return _bounded_result(
        {
            "repository": snapshot["repository"],
            "query": clean_query,
            "count": min(len(results), clean_limit),
            "results": results[:clean_limit],
            "tree_truncated": snapshot["tree_truncated"],
        }
    )


def gitmcp_search_code_payload(
    repository: str,
    query: str,
    page: int = 1,
    *,
    client: SafeHttpClient | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=200).lower()
    clean_page = int(page)
    if not 1 <= clean_page <= 100:
        raise ValueError("page must be between 1 and 100.")
    snapshot = _gitmcp_snapshot(
        repository, client=client or _gitmcp_client(), cache=cache
    )
    matches = [path for path in snapshot["paths"] if clean_query in path.lower()]
    start = (clean_page - 1) * 20
    results = [
        {"path": path, "source_url": f"https://github.com/{snapshot['repository']}/blob/{quote(snapshot['default_branch'], safe='')}/{quote(path, safe='/')}"}
        for path in matches[start:start + 20]
    ]
    return _bounded_result(
        {
            "repository": snapshot["repository"],
            "query": clean_query,
            "page": clean_page,
            "count": len(results),
            "results": results,
            "search_scope": "bounded repository path index",
            "tree_truncated": snapshot["tree_truncated"],
        }
    )


def _public_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _dexpaprika_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=DEXPAPRIKA_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"api.dexpaprika.com": 0.5},
    )


def dexpaprika_networks_payload(
    *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    policy_client = client or _dexpaprika_client()
    payload = _json_response(
        policy_client.request(
            "https://api.dexpaprika.com/networks",
            headers={"User-Agent": DEXPAPRIKA_USER_AGENT},
        ),
        "DexPaprika",
    )
    if not isinstance(payload, list) or len(payload) > 128:
        raise ValueError("DexPaprika returned an invalid network list.")
    networks: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("DexPaprika returned an invalid network entry.")
        network_id = _clean_public_text(item.get("id"), maximum=64).lower()
        display_name = _clean_public_text(item.get("display_name"), maximum=128)
        if not PUBLIC_SLUG.fullmatch(network_id) or not display_name:
            raise ValueError("DexPaprika returned an invalid network identity.")
        networks.append(
            {
                "id": network_id,
                "display_name": display_name,
                "volume_usd_24h": _public_number(item.get("volume_usd_24h")),
                "transactions_24h": _public_number(item.get("txns_24h")),
                "pool_count": _public_number(item.get("pools_count")),
            }
        )
    return _bounded_result(
        {"count": len(networks), "networks": networks, "provider": "DexPaprika"}
    )


def dexpaprika_stats_payload(
    *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    policy_client = client or _dexpaprika_client()
    payload = _json_response(
        policy_client.request(
            "https://api.dexpaprika.com/stats",
            headers={"User-Agent": DEXPAPRIKA_USER_AGENT},
        ),
        "DexPaprika",
    )
    if not isinstance(payload, dict):
        raise ValueError("DexPaprika returned invalid statistics.")
    statistics = {
        key: _public_number(payload.get(key))
        for key in ("chains", "factories", "pools", "tokens")
    }
    if any(value is None or value < 0 for value in statistics.values()):
        raise ValueError("DexPaprika returned invalid statistics.")
    return _bounded_result({**statistics, "provider": "DexPaprika"})


def _dexpaprika_token_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    identifier = _clean_public_text(item.get("id"), maximum=128)
    name = _clean_public_text(item.get("name"), maximum=160)
    symbol = _clean_public_text(item.get("symbol"), maximum=40)
    chain = _clean_public_text(item.get("chain"), maximum=64).lower()
    if not identifier or not name or not symbol or not PUBLIC_SLUG.fullmatch(chain):
        return None
    return {
        "id": identifier,
        "name": name,
        "symbol": symbol,
        "chain": chain,
        "price_usd": _public_number(item.get("price_usd")),
        "liquidity_usd": _public_number(item.get("liquidity_usd")),
        "volume_usd": _public_number(item.get("volume_usd")),
        "price_change_percentage_24h": _public_number(
            item.get("price_usd_change")
        ),
    }


def _dexpaprika_pool_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    identifier = _clean_public_text(item.get("id"), maximum=128)
    dex_name = _clean_public_text(item.get("dex_name"), maximum=160)
    chain = _clean_public_text(item.get("chain"), maximum=64).lower()
    if not identifier or not dex_name or not PUBLIC_SLUG.fullmatch(chain):
        return None
    tokens = [
        summary
        for summary in (
            _dexpaprika_token_summary(value)
            for value in (item.get("tokens") if isinstance(item.get("tokens"), list) else [])[:4]
        )
        if summary is not None
    ]
    return {
        "id": identifier,
        "dex_name": dex_name,
        "chain": chain,
        "volume_usd": _public_number(item.get("volume_usd")),
        "transactions": _public_number(item.get("transactions")),
        "price_usd": _public_number(item.get("price_usd")),
        "tokens": tokens,
    }


def dexpaprika_search_payload(
    query: str,
    max_results: int = 5,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=100)
    limit = int(max_results)
    if not 1 <= limit <= 10:
        raise ValueError("max_results must be between 1 and 10.")
    policy_client = client or _dexpaprika_client()
    payload = _json_response(
        policy_client.request(
            "https://api.dexpaprika.com/search?" + urlencode({"query": clean_query}),
            headers={"User-Agent": DEXPAPRIKA_USER_AGENT},
        ),
        "DexPaprika",
    )
    if not isinstance(payload, dict):
        raise ValueError("DexPaprika returned invalid search results.")
    raw_tokens = payload.get("tokens") if isinstance(payload.get("tokens"), list) else []
    raw_pools = payload.get("pools") if isinstance(payload.get("pools"), list) else []
    tokens = [
        result
        for result in (_dexpaprika_token_summary(item) for item in raw_tokens[:limit])
        if result is not None
    ]
    pools = [
        result
        for result in (_dexpaprika_pool_summary(item) for item in raw_pools[:limit])
        if result is not None
    ]
    return _bounded_result(
        {
            "query": clean_query,
            "tokens": tokens,
            "pools": pools,
            "token_count": len(tokens),
            "pool_count": len(pools),
            "provider": "DexPaprika",
            "notice": "Names and symbols are untrusted public market metadata, not financial advice.",
        }
    )


def _chess_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=CHESS_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=512 * 1024,
        minimum_intervals={"api.chess.com": 0.5},
    )


def _chess_username(value: Any) -> str:
    clean = str(value or "").strip()
    if not CHESS_USERNAME.fullmatch(clean):
        raise ValueError("username must contain 1-50 letters, digits, hyphens or underscores.")
    return clean.lower()


def _chess_payload(
    username: str,
    suffix: str,
    *,
    client: SafeHttpClient | None = None,
) -> tuple[str, dict[str, Any]]:
    clean_username = _chess_username(username)
    policy_client = client or _chess_client()
    payload = _json_response(
        policy_client.request(
            f"https://api.chess.com/pub/player/{quote(clean_username, safe='')}{suffix}",
            headers={"User-Agent": CHESS_USER_AGENT},
        ),
        "Chess.com",
    )
    if not isinstance(payload, dict):
        raise ValueError("Chess.com returned invalid player data.")
    return clean_username, payload


def chess_player_profile_payload(
    username: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    clean_username, payload = _chess_payload(username, "", client=client)
    returned_username = _clean_public_text(payload.get("username"), maximum=50).lower()
    if returned_username != clean_username:
        raise ValueError("Chess.com returned a mismatched player identity.")
    country = urlsplit(str(payload.get("country") or ""))
    country_code = ""
    if country.scheme == "https" and country.hostname == "api.chess.com":
        match = re.fullmatch(r"/pub/country/([A-Z]{2})", country.path)
        country_code = match.group(1) if match else ""
    return _bounded_result(
        {
            "username": returned_username,
            "player_id": _public_number(payload.get("player_id")),
            "name": _clean_public_text(payload.get("name"), maximum=160),
            "title": _clean_public_text(payload.get("title"), maximum=8),
            "country_code": country_code,
            "status": _clean_public_text(payload.get("status"), maximum=32),
            "followers": _public_number(payload.get("followers")),
            "last_online": _public_number(payload.get("last_online")),
            "joined": _public_number(payload.get("joined")),
            "is_streamer": bool(payload.get("is_streamer")),
            "verified": bool(payload.get("verified")),
            "provider": "Chess.com",
        }
    )


def _chess_rating_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    output: dict[str, Any] = {}
    for section in ("last", "best", "record"):
        raw = value.get(section)
        if not isinstance(raw, dict):
            continue
        keys = ("rating", "date", "rd") if section != "record" else ("win", "loss", "draw")
        clean = {key: _public_number(raw.get(key)) for key in keys}
        output[section] = {key: number for key, number in clean.items() if number is not None}
    return output or None


def chess_player_stats_payload(
    username: str, *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    clean_username, payload = _chess_payload(username, "/stats", client=client)
    statistics = {
        key: summary
        for key in (
            "chess_daily",
            "chess_rapid",
            "chess_blitz",
            "chess_bullet",
        )
        if (summary := _chess_rating_summary(payload.get(key))) is not None
    }
    fide = _public_number(payload.get("fide"))
    if fide is not None:
        statistics["fide"] = fide
    return _bounded_result(
        {"username": clean_username, "statistics": statistics, "provider": "Chess.com"}
    )


ANILIST_MEDIA_FIELDS = """
id
idMal
title { romaji english native }
format
status
season
seasonYear
episodes
duration
genres
averageScore
popularity
isAdult
"""


def _anilist_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=ANILIST_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=512 * 1024,
        minimum_intervals={"graphql.anilist.co": 0.75},
    )


def _anilist_graphql(
    query: str,
    variables: dict[str, Any],
    *,
    client: SafeHttpClient,
) -> dict[str, Any]:
    response = client.request(
        "https://graphql.anilist.co",
        method="POST",
        headers={
            "User-Agent": ANILIST_USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        body=json.dumps(
            {"query": query, "variables": variables},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    payload = _json_response(response, "AniList")
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("AniList rejected the fixed public query.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("AniList returned invalid GraphQL data.")
    return data


def _anilist_media_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    media_id = item.get("id")
    title = item.get("title")
    if (
        not isinstance(media_id, int)
        or isinstance(media_id, bool)
        or not isinstance(title, dict)
    ):
        return None
    return {
        "id": media_id,
        "id_mal": _public_number(item.get("idMal")),
        "title": {
            key: _clean_public_text(title.get(key), maximum=256)
            for key in ("romaji", "english", "native")
        },
        "format": _clean_public_text(item.get("format"), maximum=32),
        "status": _clean_public_text(item.get("status"), maximum=32),
        "season": _clean_public_text(item.get("season"), maximum=16),
        "season_year": _public_number(item.get("seasonYear")),
        "episodes": _public_number(item.get("episodes")),
        "duration_minutes": _public_number(item.get("duration")),
        "genres": [
            _clean_public_text(value, maximum=80)
            for value in (item.get("genres") if isinstance(item.get("genres"), list) else [])[:20]
            if _clean_public_text(value, maximum=80)
        ],
        "average_score": _public_number(item.get("averageScore")),
        "popularity": _public_number(item.get("popularity")),
        "is_adult": bool(item.get("isAdult")),
    }


def anilist_genres_payload(
    *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    data = _anilist_graphql(
        "query ModelMirrorGenres { GenreCollection }",
        {},
        client=client or _anilist_client(),
    )
    raw_genres = data.get("GenreCollection")
    if not isinstance(raw_genres, list) or len(raw_genres) > 128:
        raise ValueError("AniList returned invalid genre metadata.")
    genres = [
        clean
        for clean in (_clean_public_text(value, maximum=80) for value in raw_genres)
        if clean
    ]
    if len(genres) != len(raw_genres):
        raise ValueError("AniList returned invalid genre metadata.")
    return _bounded_result({"count": len(genres), "genres": genres, "provider": "AniList"})


def anilist_search_anime_payload(
    term: str,
    page: int = 1,
    amount: int = 5,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_term = _bounded_string(term, "term", maximum=200)
    clean_page = int(page)
    clean_amount = int(amount)
    if not 1 <= clean_page <= 100:
        raise ValueError("page must be between 1 and 100.")
    if not 1 <= clean_amount <= 10:
        raise ValueError("amount must be between 1 and 10.")
    data = _anilist_graphql(
        "query ModelMirrorAnimeSearch($search: String!, $page: Int!, $perPage: Int!) {"
        " Page(page: $page, perPage: $perPage) {"
        " pageInfo { total currentPage lastPage hasNextPage perPage }"
        f" media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{ {ANILIST_MEDIA_FIELDS} }}"
        " } }",
        {"search": clean_term, "page": clean_page, "perPage": clean_amount},
        client=client or _anilist_client(),
    )
    page_data = data.get("Page")
    if not isinstance(page_data, dict) or not isinstance(page_data.get("media"), list):
        raise ValueError("AniList returned invalid anime search data.")
    media = [
        summary
        for summary in (_anilist_media_summary(item) for item in page_data["media"][:clean_amount])
        if summary is not None
    ]
    page_info = page_data.get("pageInfo") if isinstance(page_data.get("pageInfo"), dict) else {}
    return _bounded_result(
        {
            "term": clean_term,
            "page": clean_page,
            "count": len(media),
            "has_next_page": bool(page_info.get("hasNextPage")),
            "results": media,
            "provider": "AniList",
        }
    )


def anilist_get_anime_payload(
    ids: int | list[int], *, client: SafeHttpClient | None = None
) -> dict[str, Any]:
    raw_ids = ids if isinstance(ids, list) else [ids]
    if not 1 <= len(raw_ids) <= 5:
        raise ValueError("ids must contain between 1 and 5 AniList IDs.")
    clean_ids: list[int] = []
    for value in raw_ids:
        if isinstance(value, bool):
            raise ValueError("AniList IDs must be positive integers.")
        number = int(value)
        if number != value or not 1 <= number <= 10_000_000:
            raise ValueError("AniList IDs must be positive integers.")
        clean_ids.append(number)
    if len(set(clean_ids)) != len(clean_ids):
        raise ValueError("AniList IDs must be unique.")
    data = _anilist_graphql(
        "query ModelMirrorAnimeByIds($ids: [Int!]!) {"
        f" Page(page: 1, perPage: 5) {{ media(id_in: $ids, type: ANIME, sort: ID) {{ {ANILIST_MEDIA_FIELDS} }} }}"
        " }",
        {"ids": clean_ids},
        client=client or _anilist_client(),
    )
    page_data = data.get("Page")
    if not isinstance(page_data, dict) or not isinstance(page_data.get("media"), list):
        raise ValueError("AniList returned invalid anime detail data.")
    media = [
        summary
        for summary in (_anilist_media_summary(item) for item in page_data["media"][:5])
        if summary is not None
    ]
    return _bounded_result(
        {"requested_ids": clean_ids, "count": len(media), "results": media, "provider": "AniList"}
    )


def _reddit_buddy_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=REDDIT_BUDDY_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=256 * 1024,
        minimum_intervals={"www.reddit.com": 5.0},
    )


def _reddit_feed_entry(entry: ET.Element) -> dict[str, Any] | None:
    namespace = "{http://www.w3.org/2005/Atom}"

    def text(name: str, maximum: int) -> str:
        node = entry.find(f"{namespace}{name}")
        return _clean_public_text(node.text if node is not None else "", maximum=maximum)

    title = text("title", 300)
    updated = text("updated", 64)
    author_node = entry.find(f"{namespace}author/{namespace}name")
    author = _clean_public_text(
        author_node.text if author_node is not None else "", maximum=64
    )
    canonical = ""
    for link in entry.findall(f"{namespace}link"):
        if link.attrib.get("rel", "alternate") != "alternate":
            continue
        parsed = urlsplit(str(link.attrib.get("href") or ""))
        if (
            parsed.scheme == "https"
            and parsed.hostname in REDDIT_BUDDY_HOSTS
            and REDDIT_POST_PATH.fullmatch(parsed.path)
        ):
            canonical = f"https://www.reddit.com{parsed.path}"
            break
    if not title or not canonical:
        return None
    content_node = entry.find(f"{namespace}content")
    content = ""
    if content_node is not None and content_node.text:
        parser = _ReadableHtml()
        parser.feed(content_node.text[:32_768])
        content = _clean_public_text(parser.text(), maximum=800)
    return {
        "title": title,
        "author": author.removeprefix("/u/"),
        "updated": updated,
        "permalink": canonical,
        "content_preview": content,
    }


def _reddit_feed_payload(
    response: Any,
    *,
    limit: int,
    query: str,
) -> dict[str, Any]:
    _require_success(response.status, "Reddit")
    content_type = str(response.headers.get("content-type") or "").lower()
    if "xml" not in content_type:
        raise ValueError("Reddit returned a non-Atom response.")
    try:
        root = ET.fromstring(response.body)
    except ET.ParseError as exc:
        raise ValueError("Reddit returned invalid Atom data.") from exc
    namespace = "{http://www.w3.org/2005/Atom}"
    if root.tag != f"{namespace}feed":
        raise ValueError("Reddit returned an unexpected feed shape.")
    raw_entries = root.findall(f"{namespace}entry")
    if len(raw_entries) > 100:
        raise ValueError("Reddit returned too many feed entries.")
    entries = [
        item
        for item in (_reddit_feed_entry(entry) for entry in raw_entries[:limit])
        if item is not None
    ]
    return _bounded_result(
        {
            "query": query,
            "count": len(entries),
            "posts": entries,
            "data_source": "reddit-public-atom",
            "notice": (
                "Public Atom feeds omit reliable score, comment-count and NSFW metadata; "
                "do not infer those values from this response."
            ),
        }
    )


def reddit_browse_payload(
    subreddit: str,
    sort: Literal["hot", "new", "top"] = "hot",
    time: Literal["hour", "day", "week", "month", "year", "all"] = "day",
    limit: int = 10,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_subreddit = str(subreddit or "").strip()
    if not REDDIT_SUBREDDIT.fullmatch(clean_subreddit):
        raise ValueError("subreddit must be a normalized public subreddit name.")
    clean_sort = str(sort)
    clean_time = str(time)
    if clean_sort not in {"hot", "new", "top"}:
        raise ValueError("sort must be hot, new or top.")
    if clean_time not in {"hour", "day", "week", "month", "year", "all"}:
        raise ValueError("time must be a fixed Reddit time range.")
    clean_limit = int(limit)
    if not 1 <= clean_limit <= 10:
        raise ValueError("limit must be between 1 and 10.")
    params = {"limit": clean_limit}
    if clean_sort == "top":
        params["t"] = clean_time
    response = (client or _reddit_buddy_client()).request(
        "https://www.reddit.com/r/"
        + quote(clean_subreddit.lower(), safe="")
        + f"/{clean_sort}/.rss?"
        + urlencode(params),
        headers={
            "User-Agent": REDDIT_BUDDY_USER_AGENT,
            "Accept": "application/atom+xml",
        },
    )
    return _reddit_feed_payload(
        response,
        limit=clean_limit,
        query=f"r/{clean_subreddit.lower()}:{clean_sort}",
    )


def reddit_search_payload(
    query: str,
    sort: Literal["relevance", "hot", "top", "new", "comments"] = "relevance",
    time: Literal["hour", "day", "week", "month", "year", "all"] = "all",
    limit: int = 10,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=120)
    clean_sort = str(sort)
    clean_time = str(time)
    if clean_sort not in {"relevance", "hot", "top", "new", "comments"}:
        raise ValueError("sort must be a fixed Reddit search order.")
    if clean_time not in {"hour", "day", "week", "month", "year", "all"}:
        raise ValueError("time must be a fixed Reddit time range.")
    clean_limit = int(limit)
    if not 1 <= clean_limit <= 10:
        raise ValueError("limit must be between 1 and 10.")
    response = (client or _reddit_buddy_client()).request(
        "https://www.reddit.com/search.rss?"
        + urlencode(
            {
                "q": clean_query,
                "sort": clean_sort,
                "t": clean_time,
                "limit": clean_limit,
            }
        ),
        headers={
            "User-Agent": REDDIT_BUDDY_USER_AGENT,
            "Accept": "application/atom+xml",
        },
    )
    return _reddit_feed_payload(response, limit=clean_limit, query=clean_query)


def _fantasy_pl_client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=FANTASY_PL_HOSTS,
        timeout=20,
        max_redirects=0,
        max_response_bytes=2 * 1024 * 1024,
        minimum_intervals={"fantasy.premierleague.com": 1.0},
    )


def _fantasy_pl_bootstrap(*, client: SafeHttpClient) -> dict[str, Any]:
    payload = _json_response(
        client.request(
            "https://fantasy.premierleague.com/api/bootstrap-static/",
            headers={"User-Agent": FANTASY_PL_USER_AGENT},
        ),
        "Fantasy Premier League",
    )
    if not isinstance(payload, dict):
        raise ValueError("Fantasy Premier League returned invalid bootstrap data.")
    players = payload.get("elements")
    teams = payload.get("teams")
    positions = payload.get("element_types")
    if (
        not isinstance(players, list)
        or not isinstance(teams, list)
        or not isinstance(positions, list)
        or len(players) > 1_000
        or len(teams) > 30
        or len(positions) > 10
    ):
        raise ValueError("Fantasy Premier League returned an invalid directory shape.")
    return payload


def _fantasy_pl_player_summary(
    item: Any,
    *,
    teams: dict[int, str],
    positions: dict[int, str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    player_id = item.get("id")
    team_id = item.get("team")
    position_id = item.get("element_type")
    if (
        isinstance(player_id, bool)
        or not isinstance(player_id, int)
        or isinstance(team_id, bool)
        or not isinstance(team_id, int)
        or isinstance(position_id, bool)
        or not isinstance(position_id, int)
        or team_id not in teams
        or position_id not in positions
    ):
        return None
    return {
        "id": player_id,
        "first_name": _clean_public_text(item.get("first_name"), maximum=80),
        "second_name": _clean_public_text(item.get("second_name"), maximum=120),
        "web_name": _clean_public_text(item.get("web_name"), maximum=80),
        "team_id": team_id,
        "team": teams[team_id],
        "position": positions[position_id],
        "price": (
            round(item["now_cost"] / 10, 1)
            if isinstance(item.get("now_cost"), int)
            and not isinstance(item.get("now_cost"), bool)
            else None
        ),
        "total_points": _public_number(item.get("total_points")),
        "form": _clean_public_text(item.get("form"), maximum=32),
        "points_per_game": _clean_public_text(item.get("points_per_game"), maximum=32),
        "selected_by_percent": _clean_public_text(
            item.get("selected_by_percent"), maximum=32
        ),
        "status": _clean_public_text(item.get("status"), maximum=8),
    }


def _fantasy_pl_directory(
    *, client: SafeHttpClient
) -> tuple[list[dict[str, Any]], dict[int, str], dict[int, str]]:
    payload = _fantasy_pl_bootstrap(client=client)
    teams = {
        item["id"]: _clean_public_text(item.get("name"), maximum=80)
        for item in payload["teams"]
        if isinstance(item, dict)
        and isinstance(item.get("id"), int)
        and not isinstance(item.get("id"), bool)
        and _clean_public_text(item.get("name"), maximum=80)
    }
    positions = {
        item["id"]: _clean_public_text(item.get("singular_name_short"), maximum=8).upper()
        for item in payload["element_types"]
        if isinstance(item, dict)
        and isinstance(item.get("id"), int)
        and not isinstance(item.get("id"), bool)
        and _clean_public_text(item.get("singular_name_short"), maximum=8)
    }
    if not teams or not positions:
        raise ValueError("Fantasy Premier League returned invalid team metadata.")
    players = [
        summary
        for summary in (
            _fantasy_pl_player_summary(item, teams=teams, positions=positions)
            for item in payload["elements"]
        )
        if summary is not None
    ]
    if not players:
        raise ValueError("Fantasy Premier League returned no valid players.")
    return players, teams, positions


def fantasy_pl_search_players_payload(
    query: str,
    position: str = "",
    team: str = "",
    max_results: int = 10,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_query = _bounded_string(query, "query", maximum=80).casefold()
    clean_position = str(position or "").strip().upper()
    clean_team = str(team or "").strip().casefold()
    if clean_position and clean_position not in {"GKP", "DEF", "MID", "FWD"}:
        raise ValueError("position must be GKP, DEF, MID or FWD.")
    if len(clean_team) > 80:
        raise ValueError("team cannot exceed 80 characters.")
    clean_limit = int(max_results)
    if not 1 <= clean_limit <= 20:
        raise ValueError("max_results must be between 1 and 20.")
    players, _, _ = _fantasy_pl_directory(client=client or _fantasy_pl_client())
    matches = []
    for player in players:
        identity = " ".join(
            str(player.get(key) or "")
            for key in ("first_name", "second_name", "web_name")
        ).casefold()
        if clean_query not in identity:
            continue
        if clean_position and player["position"] != clean_position:
            continue
        if clean_team and clean_team not in str(player["team"]).casefold():
            continue
        matches.append(player)
        if len(matches) >= clean_limit:
            break
    return _bounded_result(
        {
            "query": clean_query,
            "count": len(matches),
            "players": matches,
            "provider": "Fantasy Premier League",
            "notice": "Official public game data only; no transfer or lineup advice is provided.",
        }
    )


def fantasy_pl_player_payload(
    player_id: int,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    if isinstance(player_id, bool):
        raise ValueError("player_id must be a positive integer.")
    clean_id = int(player_id)
    if clean_id != player_id or not 1 <= clean_id <= 100_000:
        raise ValueError("player_id must be a positive integer.")
    players, _, _ = _fantasy_pl_directory(client=client or _fantasy_pl_client())
    match = next((player for player in players if player["id"] == clean_id), None)
    if match is None:
        raise ValueError("Fantasy Premier League player was not found.")
    return _bounded_result(
        {
            "player": match,
            "provider": "Fantasy Premier League",
            "notice": "Official public game data only; no transfer or lineup advice is provided.",
        }
    )


def fantasy_pl_fixtures_payload(
    event: int | None = None,
    team_id: int | None = None,
    max_results: int = 20,
    *,
    client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    clean_event: int | None = None
    if event is not None:
        if isinstance(event, bool) or not 1 <= int(event) <= 60 or int(event) != event:
            raise ValueError("event must be between 1 and 60.")
        clean_event = int(event)
    clean_team: int | None = None
    if team_id is not None:
        if isinstance(team_id, bool) or not 1 <= int(team_id) <= 100 or int(team_id) != team_id:
            raise ValueError("team_id must be between 1 and 100.")
        clean_team = int(team_id)
    clean_limit = int(max_results)
    if not 1 <= clean_limit <= 50:
        raise ValueError("max_results must be between 1 and 50.")
    query = "?" + urlencode({"event": clean_event}) if clean_event is not None else ""
    payload = _json_response(
        (client or _fantasy_pl_client()).request(
            "https://fantasy.premierleague.com/api/fixtures/" + query,
            headers={"User-Agent": FANTASY_PL_USER_AGENT},
        ),
        "Fantasy Premier League",
    )
    if not isinstance(payload, list) or len(payload) > 1_000:
        raise ValueError("Fantasy Premier League returned invalid fixture data.")
    fixtures: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        home = item.get("team_h")
        away = item.get("team_a")
        fixture_id = item.get("id")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (home, away, fixture_id)):
            continue
        if clean_team is not None and clean_team not in {home, away}:
            continue
        fixtures.append(
            {
                "id": fixture_id,
                "event": _public_number(item.get("event")),
                "kickoff_time": _clean_public_text(item.get("kickoff_time"), maximum=64),
                "started": bool(item.get("started")),
                "finished": bool(item.get("finished")),
                "home_team_id": home,
                "away_team_id": away,
                "home_score": _public_number(item.get("team_h_score")),
                "away_score": _public_number(item.get("team_a_score")),
                "home_difficulty": _public_number(item.get("team_h_difficulty")),
                "away_difficulty": _public_number(item.get("team_a_difficulty")),
            }
        )
        if len(fixtures) >= clean_limit:
            break
    return _bounded_result(
        {
            "event": clean_event,
            "team_id": clean_team,
            "count": len(fixtures),
            "fixtures": fixtures,
            "provider": "Fantasy Premier League",
        }
    )


def build_reddit_buddy() -> FastMCP:
    mcp = FastMCP("ModelMirror Reddit MCP Buddy")
    client = _reddit_buddy_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def browse_subreddit(
        subreddit: str,
        sort: Literal["hot", "new", "top"] = "hot",
        time: Literal["hour", "day", "week", "month", "year", "all"] = "day",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Read a bounded public subreddit Atom feed without credentials."""

        return reddit_browse_payload(subreddit, sort, time, limit, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search_reddit(
        query: str,
        sort: Literal["relevance", "hot", "top", "new", "comments"] = "relevance",
        time: Literal["hour", "day", "week", "month", "year", "all"] = "all",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search bounded public Reddit Atom metadata without user or post-detail tools."""

        return reddit_search_payload(query, sort, time, limit, client=client)

    return mcp


def build_fantasy_pl() -> FastMCP:
    mcp = FastMCP("ModelMirror Fantasy PL")
    client = _fantasy_pl_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search_fpl_players(
        query: str,
        position: str = "",
        team: str = "",
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Search bounded official public FPL player metadata."""

        return fantasy_pl_search_players_payload(
            query, position, team, max_results, client=client
        )

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_player_information(player_id: int) -> dict[str, Any]:
        """Read official public metadata for one numeric FPL player ID."""

        return fantasy_pl_player_payload(player_id, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def list_fpl_fixtures(
        event: int | None = None,
        team_id: int | None = None,
        max_results: int = 20,
    ) -> dict[str, Any]:
        """List bounded official fixture metadata without team-management tools."""

        return fantasy_pl_fixtures_payload(
            event, team_id, max_results, client=client
        )

    return mcp


def build_dexpaprika() -> FastMCP:
    mcp = FastMCP("ModelMirror DexPaprika")
    client = _dexpaprika_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def getNetworks() -> dict[str, Any]:
        """List bounded public network metadata from the fixed DexPaprika API."""

        return dexpaprika_networks_payload(client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def getStats() -> dict[str, Any]:
        """Read aggregate chain, factory, pool and token counts from DexPaprika."""

        return dexpaprika_stats_payload(client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search(query: str, max_results: int = 5) -> dict[str, Any]:
        """Search bounded token and pool metadata without transaction or wallet tools."""

        return dexpaprika_search_payload(query, max_results, client=client)

    return mcp


def build_chess() -> FastMCP:
    mcp = FastMCP("ModelMirror Chess.com")
    client = _chess_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_player_profile(username: str) -> dict[str, Any]:
        """Read a bounded public Chess.com player profile."""

        return chess_player_profile_payload(username, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_player_stats(username: str) -> dict[str, Any]:
        """Read bounded public Chess.com rating and game-result aggregates."""

        return chess_player_stats_payload(username, client=client)

    return mcp


def build_anilist() -> FastMCP:
    mcp = FastMCP("ModelMirror AniList")
    client = _anilist_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_genres() -> dict[str, Any]:
        """List public AniList genres without authentication."""

        return anilist_genres_payload(client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search_anime(
        term: str, page: int = 1, amount: int = 5
    ) -> dict[str, Any]:
        """Search bounded public anime metadata with a fixed GraphQL document."""

        return anilist_search_anime_payload(
            term, page, amount, client=client
        )

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_anime(ids: int | list[int]) -> dict[str, Any]:
        """Read bounded public anime metadata for one to five AniList IDs."""

        return anilist_get_anime_payload(ids, client=client)

    return mcp


def build_duckduckgo() -> FastMCP:
    mcp = FastMCP("ModelMirror DuckDuckGo")
    client = SafeHttpClient(
        allowed_hosts=DUCKDUCKGO_HOSTS,
        timeout=15,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"html.duckduckgo.com": 2.0},
    )

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search(query: str, max_results: int = 10, region: str = "") -> dict[str, Any]:
        """Search DuckDuckGo with strict SafeSearch and bounded result metadata."""

        return duckduckgo_search_payload(query, max_results, region, client=client)

    return mcp


def build_shadcn() -> FastMCP:
    mcp = FastMCP("ModelMirror shadcn/ui")
    client = SafeHttpClient(
        allowed_hosts=SHADCN_HOSTS,
        timeout=15,
        max_redirects=0,
        max_response_bytes=1024 * 1024,
        minimum_intervals={"api.github.com": 60.0},
    )
    directory_cache: list[dict[str, Any]] | None = None

    def directory_entries() -> list[dict[str, Any]]:
        nonlocal directory_cache
        if directory_cache is None:
            directory_cache = _shadcn_component_entries(client=client)
        return directory_cache

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def list_components() -> dict[str, Any]:
        """List component names from the pinned shadcn/ui v4 registry commit."""

        return shadcn_list_components_payload(entries=directory_entries())

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_component_metadata(componentName: str) -> dict[str, Any]:
        """Read bounded Git metadata for one normalized component slug."""

        return shadcn_component_metadata_payload(
            componentName,
            entries=directory_entries(),
        )

    return mcp


def build_docker_hub() -> FastMCP:
    mcp = FastMCP("ModelMirror Docker Hub")
    client = _docker_hub_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search(query: str, max_results: int = 10) -> dict[str, Any]:
        """Search public Docker Hub repositories with a bounded result count."""

        return docker_hub_search_payload(query, max_results, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def getRepositoryInfo(namespace: str, repository: str) -> dict[str, Any]:
        """Read public metadata for one normalized Docker Hub repository."""

        return docker_hub_repository_payload(namespace, repository, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def listRepositoryTags(
        repository: str,
        namespace: str = "library",
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """List bounded public tag metadata for one Docker Hub repository."""

        return docker_hub_tags_payload(
            repository,
            namespace,
            page,
            page_size,
            client=client,
        )

    return mcp


def build_biomcp() -> FastMCP:
    mcp = FastMCP("ModelMirror BioMCP")
    client = _biomcp_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search(
        entity: Literal["article", "trial", "variant"],
        query: str,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search a reviewed anonymous subset of public biomedical metadata."""

        return biomcp_search_payload(entity, query, limit, offset, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get(
        entity: Literal["article", "trial", "variant"],
        id: str,
        sections: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get one reviewed public biomedical record without local study access."""

        return biomcp_get_payload(entity, id, sections, client=client)

    return mcp


def build_safedep_vet() -> FastMCP:
    mcp = FastMCP("ModelMirror SafeDep Vet")
    client = _safedep_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_package_version_vulnerabilities(purl: str) -> dict[str, Any]:
        """Get bounded SafeDep vulnerability metadata for one npm/PyPI version."""

        return safedep_vulnerabilities_payload(purl, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_package_version_popularity(purl: str) -> dict[str, Any]:
        """Get bounded project popularity metadata for one npm/PyPI version."""

        return safedep_popularity_payload(purl, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_package_version_license_info(purl: str) -> dict[str, Any]:
        """Get bounded license identifiers for one npm/PyPI package version."""

        return safedep_license_payload(purl, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_package_version_malware_report(purl: str) -> dict[str, Any]:
        """Query an existing SafeDep malware report without downloading a package."""

        return safedep_malware_payload(purl, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_package_latest_version(purl: str) -> dict[str, Any]:
        """Read the latest published version from the fixed npm/PyPI registries."""

        return safedep_latest_version_payload(purl, client=client)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_package_available_versions(
        purl: str, max_results: int = 50
    ) -> dict[str, Any]:
        """List a bounded set of published npm/PyPI versions."""

        return safedep_available_versions_payload(
            purl, max_results, client=client
        )

    return mcp


def build_open_websearch() -> FastMCP:
    mcp = FastMCP("ModelMirror open-webSearch")
    client = _open_websearch_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search(
        query: str,
        limit: int = 10,
        engines: list[Literal["bing", "duckduckgo"]] | None = None,
    ) -> dict[str, Any]:
        """Search a fixed request-only subset of Bing and DuckDuckGo."""

        return open_websearch_payload(query, limit, engines, client=client)

    return mcp


def build_idea_reality() -> FastMCP:
    mcp = FastMCP("ModelMirror Idea Reality")
    client = _idea_reality_client()

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def idea_check(
        idea_text: str,
        depth: Literal["quick", "deep"] = "quick",
    ) -> dict[str, Any]:
        """Compare an idea with bounded public GitHub, HN and registry evidence."""

        return idea_reality_payload(idea_text, depth, client=client)

    return mcp


def build_gitmcp() -> FastMCP:
    mcp = FastMCP("ModelMirror GitMCP")
    client = _gitmcp_client()
    cache: dict[str, dict[str, Any]] = {}

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def fetch_repository_documentation(repository: str) -> dict[str, Any]:
        """Fetch the bounded public README for one canonical GitHub repo slug."""

        return gitmcp_documentation_payload(repository, client=client, cache=cache)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search_repository_documentation(
        repository: str,
        query: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search README text and documentation paths in one public repository."""

        return gitmcp_search_documentation_payload(
            repository, query, limit, client=client, cache=cache
        )

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def search_repository_code(
        repository: str,
        query: str,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search a bounded public repository path index without cloning code."""

        return gitmcp_search_code_payload(
            repository, query, page, client=client, cache=cache
        )

    return mcp


BUILDERS = {
    "fetch-mcp": build_fetch,
    "quickchart-mcp": build_quickchart,
    "geowire-mcp": build_geowire,
    "nickclyde-duckduckgo-mcp-server": build_duckduckgo,
    "jpisnice-shadcn-ui-mcp-server": build_shadcn,
    "docker-hub-mcp": build_docker_hub,
    "genomoncology-biomcp": build_biomcp,
    "safedep-vet": build_safedep_vet,
    "aas-ee-open-websearch": build_open_websearch,
    "mnemox-ai-idea-reality-mcp": build_idea_reality,
    "idosal-git-mcp": build_gitmcp,
    "coinpaprika-dexpaprika-mcp": build_dexpaprika,
    "pab1it0-chess-mcp": build_chess,
    "yuna0x0-anilist-mcp": build_anilist,
    "karanb192-reddit-mcp-buddy": build_reddit_buddy,
    "rishijatia-fantasy-pl-mcp": build_fantasy_pl,
}

ADAPTER_TOOL_NAMES = {
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

PUBLIC_SCHEMA_SHA256 = {
    "nickclyde-duckduckgo-mcp-server": (
        "9a10fcfb68759337ab6af5fcfe76f5a7ebc87f3724e34a2017ea25807e4cc197"
    ),
    "jpisnice-shadcn-ui-mcp-server": (
        "8a04ba4e5da26f151bc0a563e63d9567e2932e0450d08565bc64f2498e39336f"
    ),
    "docker-hub-mcp": (
        "e8ce120ed943ee25aaa0d67218e4ce8e408dc42592251e9eec108daa1065d35d"
    ),
    "genomoncology-biomcp": (
        "24c2ca66ce7643bdb91323912a73956c1adbd93c82c246c55fe773afa95f1c31"
    ),
    "safedep-vet": (
        "52be50ad2e6b7c53e2b6e76799a9083f3892ae49e2b0f2bfccee4ca8262be652"
    ),
    "aas-ee-open-websearch": (
        "cf695f0f1d6a9fb3fe08ae454f3729367f28103bc85d1c893737f42ad706fe99"
    ),
    "mnemox-ai-idea-reality-mcp": (
        "65b4b069bcb5faa961341576f452e72faa49b4deae214a6f840da2521a010c24"
    ),
    "idosal-git-mcp": (
        "56a8c84a969a4beaca16bf905be83899bb497d19a4e95cef5135ad4465ef4811"
    ),
    "coinpaprika-dexpaprika-mcp": (
        "b6b6a6ef17aed4544341be76648401fd4ac6a62f4d657d9f5da0f2429429ebc9"
    ),
    "pab1it0-chess-mcp": (
        "d33380c3a2cd3e271e289c9a021c1c8d67403bb2f74a4c5df6e075b67882cf7d"
    ),
    "yuna0x0-anilist-mcp": (
        "060e2a7e6eb92fd44a945b99ca91adb614eb877e286535516b9ec8c0a7b7e239"
    ),
    "karanb192-reddit-mcp-buddy": (
        "a0644027f1cf46f23c447e607bd7aef38465932a67b36f20e1a10c62b57ed2bd"
    ),
    "rishijatia-fantasy-pl-mcp": (
        "b9760cc0e80c3c906a96e9090e90c57e31b4443e2f58a622c6769ee8448fe602"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    BUILDERS[args.adapter_id]().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
