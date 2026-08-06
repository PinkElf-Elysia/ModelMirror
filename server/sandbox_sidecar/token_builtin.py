"""Small reviewed compatibility adapters for four Wave 4 providers."""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any
from urllib.parse import quote, urlencode

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


BUILDERS = {
    "axiom-mcp": build_axiom,
    "grafana-mcp": build_grafana,
    "kagi-mcp": build_kagi,
    "pinecone-assistant-mcp": build_pinecone,
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    BUILDERS[args.adapter_id]().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
