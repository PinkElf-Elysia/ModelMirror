"""Small reviewed compatibility adapters for fixed read-only providers."""

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


BUILDERS = {
    "axiom-mcp": build_axiom,
    "grafana-mcp": build_grafana,
    "kagi-mcp": build_kagi,
    "pinecone-assistant-mcp": build_pinecone,
    "terraform-mcp": build_terraform,
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    BUILDERS[args.adapter_id]().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
