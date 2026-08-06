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
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .safe_http import NetworkPolicyError, SafeHttpClient


MAX_RESULT_BYTES = 128 * 1024
MAX_FETCH_BYTES = 1024 * 1024
MAX_AIRBNB_BYTES = 2 * 1024 * 1024
MAX_FETCH_LENGTH = 100_000
MAX_GEO_RESULTS = 10
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


BUILDERS = {
    "fetch-mcp": build_fetch,
    "quickchart-mcp": build_quickchart,
    "geowire-mcp": build_geowire,
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
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    BUILDERS[args.adapter_id]().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
