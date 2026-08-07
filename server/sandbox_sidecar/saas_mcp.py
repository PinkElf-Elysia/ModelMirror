"""Reviewed MCP implementations for four fixed Wave 6 SaaS providers."""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .saas_contracts import MAX_ARGUMENT_BYTES, MAX_OUTPUT_BYTES, SAAS_ADAPTERS
from .safe_http import (
    NetworkPolicyError,
    ResponseLimitError,
    _PinnedHTTPSConnection,
    resolve_public_addresses,
    validate_public_https_url,
)


RETRYABLE_READ_STATUSES = frozenset({429, 502, 503, 504})
MAX_READ_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 5.0
NOTION_VERSION = "2025-09-03"
OFFLINE_SMOKE = os.getenv("MCP_SAAS_OFFLINE_SMOKE", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
UNKNOWN_WRITE_OUTCOME_MARKER = "modelmirror_unknown_write_outcome"
ALLOWED_PROVIDER_HEADERS: dict[str, frozenset[str]] = {
    "api.airtable.com": frozenset({"authorization", "content-type"}),
    "app.asana.com": frozenset({"authorization", "content-type"}),
    "gitlab.com": frozenset({"private-token", "content-type"}),
    "api.notion.com": frozenset(
        {"authorization", "notion-version", "content-type"}
    ),
}

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
STATE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes


class UnknownWriteOutcome(RuntimeError):
    """The write may have reached the provider, so callers must not retry it."""


class FixedHTTPSClient:
    """HTTPS-only client with DNS pinning, fixed host and no redirects."""

    def __init__(self, host: str, *, minimum_interval_seconds: float) -> None:
        if host not in ALLOWED_PROVIDER_HEADERS:
            raise NetworkPolicyError("saas_http_host_denied")
        self.host = host
        self.minimum_interval_seconds = max(float(minimum_interval_seconds), 0.0)
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(self._next_request_at - now, 0.0)
            self._next_request_at = max(now, self._next_request_at) + self.minimum_interval_seconds
        if wait:
            time.sleep(wait)

    def request(
        self,
        path: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> HttpResult:
        method = method.upper()
        if method not in {"GET", "POST", "PATCH", "PUT"}:
            raise NetworkPolicyError("saas_http_method_denied")
        if not path.startswith("/") or "\r" in path or "\n" in path or len(path) > 16_384:
            raise NetworkPolicyError("saas_http_path_denied")
        if body is not None and len(body) > MAX_ARGUMENT_BYTES:
            raise ValueError("saas_request_too_large")
        url = f"https://{self.host}{path}"
        _, host, port, normalized_path = validate_public_https_url(
            url, allowed_hosts=frozenset({self.host})
        )
        addresses = resolve_public_addresses(host, port)
        self._throttle()
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "ModelMirror-MCP-SaaS/1.0",
            "Connection": "close",
        }
        for raw_name, raw_value in headers.items():
            name = str(raw_name).strip()
            value = str(raw_value).strip()
            if (
                name.lower() not in ALLOWED_PROVIDER_HEADERS[self.host]
                or not value
                or "\r" in value
                or "\n" in value
                or len(value) > 20_000
            ):
                raise NetworkPolicyError("saas_http_header_denied")
            request_headers[name] = value
        connection = _PinnedHTTPSConnection(host, addresses[0], port=port, timeout=12.0)
        try:
            connection.request(method, normalized_path, body=body, headers=request_headers)
            response = connection.getresponse()
            response_headers = {
                str(name).lower(): str(value) for name, value in response.getheaders()
            }
            if 300 <= response.status < 400:
                response.read(min(MAX_OUTPUT_BYTES, 64 * 1024))
                raise NetworkPolicyError("saas_redirect_denied")
            content_length = response_headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_OUTPUT_BYTES:
                        raise ResponseLimitError("saas_response_too_large")
                except ValueError:
                    pass
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(64 * 1024, MAX_OUTPUT_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise ResponseLimitError("saas_response_too_large")
                chunks.append(chunk)
            return HttpResult(int(response.status), response_headers, b"".join(chunks))
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise NetworkPolicyError("saas_https_failed") from exc
        finally:
            connection.close()


def _retry_delay(headers: dict[str, str], attempt: int) -> float:
    raw = headers.get("retry-after", "").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.25 * (2**attempt)
    return min(max(value, 0.0), MAX_RETRY_AFTER_SECONDS)


def _decode_json(result: HttpResult, provider: str) -> Any:
    if not 200 <= result.status < 300:
        raise RuntimeError(f"{provider}_http_{result.status}")
    if not result.body:
        return {}
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{provider}_invalid_response") from exc
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ResponseLimitError("saas_response_too_large")
    return payload


def _request_json(
    client: FixedHTTPSClient,
    provider: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str],
    payload: object | None = None,
    read_operation: bool,
) -> Any:
    body = None
    request_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_ARGUMENT_BYTES:
            raise ValueError("saas_request_too_large")
        request_headers["Content-Type"] = "application/json"
    attempts = MAX_READ_RETRIES + 1 if read_operation else 1
    last_result: HttpResult | None = None
    for attempt in range(attempts):
        try:
            result = client.request(path, method=method, headers=request_headers, body=body)
        except NetworkPolicyError as exc:
            if not read_operation:
                raise UnknownWriteOutcome(UNKNOWN_WRITE_OUTCOME_MARKER) from exc
            raise
        if not read_operation and (result.status == 408 or result.status >= 500):
            raise UnknownWriteOutcome(UNKNOWN_WRITE_OUTCOME_MARKER)
        last_result = result
        if result.status not in RETRYABLE_READ_STATUSES or not read_operation:
            return _decode_json(result, provider)
        if attempt + 1 < attempts:
            time.sleep(_retry_delay(result.headers, attempt))
    assert last_result is not None
    return _decode_json(last_result, provider)


def _load_configuration(adapter_id: str) -> tuple[dict[str, str], dict[str, str]]:
    raw = os.environ.pop("MCP_SAAS_HANDSHAKE_B64", "")
    if not raw:
        raise RuntimeError("saas_configuration_missing")
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("saas_configuration_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("saas_configuration_invalid")
    credentials = payload.get("credentials")
    settings = payload.get("settings")
    if not isinstance(credentials, dict) or not isinstance(settings, dict):
        raise RuntimeError("saas_configuration_invalid")
    contract = SAAS_ADAPTERS[adapter_id]
    if set(credentials) != set(contract.credential_fields) or set(settings) != set(contract.setting_fields):
        raise RuntimeError("saas_configuration_invalid")
    return (
        {name: str(credentials[name]) for name in contract.credential_fields},
        {name: str(settings[name]) for name in contract.setting_fields},
    )


def _text(value: object, name: str, maximum: int = 8_000, *, optional: bool = False) -> str:
    clean = str(value or "").strip()
    if (not clean and not optional) or len(clean) > maximum or "\x00" in clean:
        raise ValueError(f"invalid_{name}")
    return clean


def _gid(value: object, name: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,31}", clean):
        raise ValueError(f"invalid_{name}")
    return clean


def _airtable_table(value: object) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"tbl[A-Za-z0-9]{14}", clean):
        raise ValueError("invalid_table_id")
    return clean


def _airtable_record(value: object) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"rec[A-Za-z0-9]{14}", clean):
        raise ValueError("invalid_record_id")
    return clean


def _page_id(value: object) -> str:
    clean = str(value or "").strip().replace("-", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", clean):
        raise ValueError("invalid_page_id")
    return clean.lower()


def _object(value: object, name: str, *, allow_empty: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or (not value and not allow_empty):
        raise ValueError(f"invalid_{name}")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ValueError(f"invalid_{name}")
    return value


def _data(payload: Any, provider: str) -> Any:
    if not isinstance(payload, dict) or "data" not in payload:
        raise RuntimeError(f"{provider}_invalid_response")
    return payload["data"]


def build_airtable() -> FastMCP:
    credentials, settings = _load_configuration("airtable-mcp")
    token = credentials.pop("personal_access_token")
    base_id = settings["base_id"]
    client = FixedHTTPSClient(
        "api.airtable.com",
        minimum_interval_seconds=SAAS_ADAPTERS["airtable-mcp"].minimum_interval_seconds,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if not OFFLINE_SMOKE:
        _request_json(client, "airtable", "/v0/meta/whoami", headers=headers, read_operation=True)
        _request_json(
            client,
            "airtable",
            f"/v0/meta/bases/{base_id}/tables",
            headers=headers,
            read_operation=True,
        )
    mcp = FastMCP("ModelMirror Airtable Scoped")

    @mcp.tool(annotations=READ_ONLY)
    def list_tables() -> Any:
        """列出已绑定 Base 中可读取的表结构。"""
        return _request_json(
            client,
            "airtable",
            f"/v0/meta/bases/{base_id}/tables",
            headers=headers,
            read_operation=True,
        )

    @mcp.tool(annotations=READ_ONLY)
    def list_records(table_id: str, page_size: int = 100, offset: str = "") -> Any:
        """分页读取已绑定 Base 中指定表的记录。"""
        table = _airtable_table(table_id)
        count = max(1, min(int(page_size), 100))
        query: dict[str, object] = {"pageSize": count}
        if offset:
            query["offset"] = _text(offset, "offset", 1_000)
        return _request_json(
            client,
            "airtable",
            f"/v0/{base_id}/{table}?{urlencode(query)}",
            headers=headers,
            read_operation=True,
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_record(table_id: str, record_id: str) -> Any:
        """读取已绑定 Base 中的一条记录。"""
        table = _airtable_table(table_id)
        record = _airtable_record(record_id)
        return _request_json(
            client,
            "airtable",
            f"/v0/{base_id}/{table}/{record}",
            headers=headers,
            read_operation=True,
        )

    @mcp.tool(annotations=STATE_WRITE)
    def create_record(table_id: str, fields: dict[str, Any]) -> Any:
        """经确认后在已绑定 Base 中创建一条记录。"""
        table = _airtable_table(table_id)
        return _request_json(
            client,
            "airtable",
            f"/v0/{base_id}/{table}",
            method="POST",
            headers=headers,
            payload={"fields": _object(fields, "fields")},
            read_operation=False,
        )

    @mcp.tool(annotations=STATE_WRITE)
    def update_record(table_id: str, record_id: str, fields: dict[str, Any]) -> Any:
        """经确认后更新已绑定 Base 中的一条记录。"""
        table = _airtable_table(table_id)
        record = _airtable_record(record_id)
        return _request_json(
            client,
            "airtable",
            f"/v0/{base_id}/{table}/{record}",
            method="PATCH",
            headers=headers,
            payload={"fields": _object(fields, "fields")},
            read_operation=False,
        )

    return mcp


def build_asana() -> FastMCP:
    credentials, settings = _load_configuration("asana-mcp")
    token = credentials.pop("personal_access_token")
    workspace_gid = settings["workspace_gid"]
    project_gid = settings["project_gid"]
    client = FixedHTTPSClient(
        "app.asana.com",
        minimum_interval_seconds=SAAS_ADAPTERS["asana-mcp"].minimum_interval_seconds,
    )
    headers = {"Authorization": f"Bearer {token}"}

    def request(path: str, *, method: str = "GET", payload: object | None = None, read: bool) -> Any:
        return _request_json(
            client,
            "asana",
            f"/api/1.0{path}",
            method=method,
            headers=headers,
            payload=payload,
            read_operation=read,
        )

    def project() -> dict[str, Any]:
        value = _data(request(f"/projects/{project_gid}?opt_fields=gid,name,workspace", read=True), "asana")
        if not isinstance(value, dict):
            raise RuntimeError("asana_invalid_project")
        workspace = value.get("workspace")
        if not isinstance(workspace, dict) or str(workspace.get("gid")) != workspace_gid:
            raise RuntimeError("asana_project_scope_mismatch")
        return value

    def scoped_task(task_gid: str) -> dict[str, Any]:
        task = _data(
            request(
                f"/tasks/{_gid(task_gid, 'task_gid')}?opt_fields=gid,name,completed,due_on,notes,projects",
                read=True,
            ),
            "asana",
        )
        if not isinstance(task, dict):
            raise RuntimeError("asana_invalid_task")
        projects = task.get("projects")
        if not isinstance(projects, list) or project_gid not in {
            str(item.get("gid")) for item in projects if isinstance(item, dict)
        }:
            raise RuntimeError("asana_task_scope_mismatch")
        return task

    if not OFFLINE_SMOKE:
        _data(request("/users/me?opt_fields=gid,name", read=True), "asana")
        _data(request(f"/workspaces/{workspace_gid}?opt_fields=gid,name", read=True), "asana")
        project()
    mcp = FastMCP("ModelMirror Asana Scoped")

    @mcp.tool(annotations=READ_ONLY)
    def list_projects(limit: int = 100, offset: str = "") -> Any:
        """列出已绑定工作区中的项目；固定项目始终单独校验。"""
        count = max(1, min(int(limit), 100))
        query: dict[str, object] = {"limit": count, "archived": "false", "opt_fields": "gid,name,archived"}
        if offset:
            query["offset"] = _text(offset, "offset", 1_000)
        return request(f"/workspaces/{workspace_gid}/projects?{urlencode(query)}", read=True)

    @mcp.tool(annotations=READ_ONLY)
    def list_tasks(limit: int = 100, offset: str = "") -> Any:
        """分页列出已绑定项目中的任务。"""
        count = max(1, min(int(limit), 100))
        query: dict[str, object] = {
            "limit": count,
            "opt_fields": "gid,name,completed,due_on,modified_at",
        }
        if offset:
            query["offset"] = _text(offset, "offset", 1_000)
        return request(f"/projects/{project_gid}/tasks?{urlencode(query)}", read=True)

    @mcp.tool(annotations=READ_ONLY)
    def get_task(task_gid: str) -> Any:
        """读取已绑定项目内的一项任务。"""
        return {"data": scoped_task(task_gid)}

    @mcp.tool(annotations=STATE_WRITE)
    def create_task(name: str, notes: str = "", due_on: str = "") -> Any:
        """经确认后在已绑定项目中创建任务。"""
        data: dict[str, Any] = {
            "workspace": workspace_gid,
            "projects": [project_gid],
            "name": _text(name, "name", 500),
        }
        if notes:
            data["notes"] = _text(notes, "notes", 20_000)
        if due_on:
            value = _text(due_on, "due_on", 10)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("invalid_due_on")
            data["due_on"] = value
        return request("/tasks", method="POST", payload={"data": data}, read=False)

    @mcp.tool(annotations=STATE_WRITE)
    def update_task(
        task_gid: str,
        name: str = "",
        notes: str = "",
        completed: bool | None = None,
        due_on: str = "",
    ) -> Any:
        """经确认后更新已绑定项目内的一项任务。"""
        task_id = _gid(task_gid, "task_gid")
        scoped_task(task_id)
        data: dict[str, Any] = {}
        if name:
            data["name"] = _text(name, "name", 500)
        if notes:
            data["notes"] = _text(notes, "notes", 20_000)
        if completed is not None:
            data["completed"] = bool(completed)
        if due_on:
            value = _text(due_on, "due_on", 10)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("invalid_due_on")
            data["due_on"] = value
        if not data:
            raise ValueError("asana_update_empty")
        return request(f"/tasks/{task_id}", method="PUT", payload={"data": data}, read=False)

    @mcp.tool(annotations=STATE_WRITE)
    def add_comment(task_gid: str, text: str) -> Any:
        """经确认后向已绑定项目内的一项任务添加评论。"""
        task_id = _gid(task_gid, "task_gid")
        scoped_task(task_id)
        return request(
            f"/tasks/{task_id}/stories",
            method="POST",
            payload={"data": {"text": _text(text, "text", 20_000)}},
            read=False,
        )

    return mcp


def build_gitlab() -> FastMCP:
    credentials, settings = _load_configuration("gitlab-mcp")
    token = credentials.pop("personal_access_token")
    project_id = settings["project_id"]
    client = FixedHTTPSClient(
        "gitlab.com",
        minimum_interval_seconds=SAAS_ADAPTERS["gitlab-mcp"].minimum_interval_seconds,
    )
    headers = {"PRIVATE-TOKEN": token}

    def request(path: str, *, method: str = "GET", payload: object | None = None, read: bool) -> Any:
        return _request_json(
            client,
            "gitlab",
            f"/api/v4{path}",
            method=method,
            headers=headers,
            payload=payload,
            read_operation=read,
        )

    if not OFFLINE_SMOKE:
        request("/user", read=True)
        token_info = request("/personal_access_tokens/self", read=True)
        scopes = token_info.get("scopes") if isinstance(token_info, dict) else None
        if not isinstance(scopes, list) or "api" not in {str(item) for item in scopes}:
            raise RuntimeError("gitlab_api_scope_required")
        project = request(f"/projects/{project_id}", read=True)
        if not isinstance(project, dict) or str(project.get("id")) != project_id:
            raise RuntimeError("gitlab_project_scope_mismatch")
    mcp = FastMCP("ModelMirror GitLab.com Scoped")

    def _issue_iid(value: object) -> str:
        return _gid(value, "issue_iid")

    def _merge_iid(value: object) -> str:
        return _gid(value, "merge_request_iid")

    @mcp.tool(annotations=READ_ONLY)
    def list_issues(state: str = "opened", per_page: int = 50, page: int = 1) -> Any:
        """分页列出已绑定 GitLab.com 项目的 Issue。"""
        if state not in {"opened", "closed", "all"}:
            raise ValueError("invalid_state")
        query = urlencode({"state": state, "per_page": max(1, min(int(per_page), 100)), "page": max(1, min(int(page), 10_000))})
        return request(f"/projects/{project_id}/issues?{query}", read=True)

    @mcp.tool(annotations=READ_ONLY)
    def get_issue(issue_iid: str) -> Any:
        """读取已绑定项目中的一个 Issue。"""
        return request(f"/projects/{project_id}/issues/{_issue_iid(issue_iid)}", read=True)

    @mcp.tool(annotations=READ_ONLY)
    def list_merge_requests(state: str = "opened", per_page: int = 50, page: int = 1) -> Any:
        """分页列出已绑定项目中的合并请求。"""
        if state not in {"opened", "closed", "merged", "all"}:
            raise ValueError("invalid_state")
        query = urlencode({"state": state, "per_page": max(1, min(int(per_page), 100)), "page": max(1, min(int(page), 10_000))})
        return request(f"/projects/{project_id}/merge_requests?{query}", read=True)

    @mcp.tool(annotations=READ_ONLY)
    def get_merge_request(merge_request_iid: str) -> Any:
        """读取已绑定项目中的一个合并请求。"""
        return request(
            f"/projects/{project_id}/merge_requests/{_merge_iid(merge_request_iid)}",
            read=True,
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_repository_file(file_path: str, ref: str = "HEAD") -> Any:
        """读取已绑定项目内指定 ref 的单个仓库文件。"""
        path = _text(file_path, "file_path", 1_024)
        if path.startswith(("/", "\\")) or any(part in {"", ".", ".."} for part in re.split(r"[/\\]", path)):
            raise ValueError("invalid_file_path")
        revision = _text(ref, "ref", 255)
        return request(
            f"/projects/{project_id}/repository/files/{quote(path, safe='')}?{urlencode({'ref': revision})}",
            read=True,
        )

    @mcp.tool(annotations=STATE_WRITE)
    def create_issue(title: str, description: str = "") -> Any:
        """经确认后在已绑定项目中创建 Issue。"""
        data: dict[str, Any] = {"title": _text(title, "title", 500)}
        if description:
            data["description"] = _text(description, "description", 40_000)
        return request(f"/projects/{project_id}/issues", method="POST", payload=data, read=False)

    @mcp.tool(annotations=STATE_WRITE)
    def update_issue(
        issue_iid: str,
        title: str = "",
        description: str = "",
        state_event: str = "",
    ) -> Any:
        """经确认后更新、关闭或重新打开已绑定项目中的 Issue。"""
        iid = _issue_iid(issue_iid)
        request(f"/projects/{project_id}/issues/{iid}", read=True)
        data: dict[str, Any] = {}
        if title:
            data["title"] = _text(title, "title", 500)
        if description:
            data["description"] = _text(description, "description", 40_000)
        if state_event:
            if state_event not in {"close", "reopen"}:
                raise ValueError("invalid_state_event")
            data["state_event"] = state_event
        if not data:
            raise ValueError("gitlab_update_empty")
        return request(
            f"/projects/{project_id}/issues/{iid}",
            method="PUT",
            payload=data,
            read=False,
        )

    @mcp.tool(annotations=STATE_WRITE)
    def add_issue_note(issue_iid: str, body: str) -> Any:
        """经确认后向已绑定项目中的 Issue 添加评论。"""
        iid = _issue_iid(issue_iid)
        request(f"/projects/{project_id}/issues/{iid}", read=True)
        return request(
            f"/projects/{project_id}/issues/{iid}/notes",
            method="POST",
            payload={"body": _text(body, "body", 40_000)},
            read=False,
        )

    return mcp


def build_notion() -> FastMCP:
    credentials, settings = _load_configuration("notion-mcp-server")
    token = credentials.pop("integration_token")
    data_source_id = settings["data_source_id"]
    client = FixedHTTPSClient(
        "api.notion.com",
        minimum_interval_seconds=SAAS_ADAPTERS["notion-mcp-server"].minimum_interval_seconds,
    )
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}

    def request(path: str, *, method: str = "GET", payload: object | None = None, read: bool) -> Any:
        return _request_json(
            client,
            "notion",
            f"/v1{path}",
            method=method,
            headers=headers,
            payload=payload,
            read_operation=read,
        )

    if not OFFLINE_SMOKE:
        request("/users/me", read=True)
        source = request(f"/data_sources/{data_source_id}", read=True)
        actual_source = (
            str(source.get("id", "")).replace("-", "").lower()
            if isinstance(source, dict)
            else ""
        )
        if actual_source != data_source_id:
            raise RuntimeError("notion_data_source_scope_mismatch")
    mcp = FastMCP("ModelMirror Notion Scoped")

    def scoped_page(page_id: str) -> dict[str, Any]:
        normalized = _page_id(page_id)
        page = request(f"/pages/{normalized}", read=True)
        if not isinstance(page, dict):
            raise RuntimeError("notion_invalid_page")
        parent = page.get("parent")
        parent_id = ""
        if isinstance(parent, dict):
            parent_id = str(parent.get("data_source_id") or "").replace("-", "").lower()
        if parent_id != data_source_id:
            raise RuntimeError("notion_page_scope_mismatch")
        return page

    @mcp.tool(annotations=READ_ONLY)
    def query_data_source(
        page_size: int = 100,
        start_cursor: str = "",
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
    ) -> Any:
        """查询已绑定的 Notion Data Source。"""
        payload: dict[str, Any] = {"page_size": max(1, min(int(page_size), 100))}
        if start_cursor:
            payload["start_cursor"] = _text(start_cursor, "start_cursor", 1_000)
        if filter is not None:
            payload["filter"] = _object(filter, "filter")
        if sorts is not None:
            if not isinstance(sorts, list) or len(sorts) > 20:
                raise ValueError("invalid_sorts")
            payload["sorts"] = sorts
        return request(
            f"/data_sources/{data_source_id}/query",
            method="POST",
            payload=payload,
            read=True,
        )

    @mcp.tool(annotations=READ_ONLY)
    def retrieve_page(page_id: str) -> Any:
        """读取绑定页面，或读取绑定数据库中的一条页面记录。"""
        return scoped_page(page_id)

    @mcp.tool(annotations=STATE_WRITE)
    def create_page(properties: dict[str, Any]) -> Any:
        """经确认后在已绑定 Data Source 中创建页面。"""
        parent = {"type": "data_source_id", "data_source_id": data_source_id}
        values = _object(properties, "properties")
        return request(
            "/pages",
            method="POST",
            payload={"parent": parent, "properties": _object(values, "properties")},
            read=False,
        )

    @mcp.tool(annotations=STATE_WRITE)
    def update_page_properties(page_id: str, properties: dict[str, Any]) -> Any:
        """经确认后更新绑定范围内页面的属性。"""
        normalized = _page_id(page_id)
        scoped_page(normalized)
        return request(
            f"/pages/{normalized}",
            method="PATCH",
            payload={"properties": _object(properties, "properties")},
            read=False,
        )

    return mcp


BUILDERS = {
    "airtable-mcp": build_airtable,
    "asana-mcp": build_asana,
    "gitlab-mcp": build_gitlab,
    "notion-mcp-server": build_notion,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in BUILDERS:
        return 2
    adapter_id = sys.argv[1]
    try:
        server = BUILDERS[adapter_id]()
    except Exception as exc:
        print(f"MODELMIRROR_SAAS_FAILED:{type(exc).__name__}", file=sys.stderr, flush=True)
        return 1
    print(f"MODELMIRROR_SAAS_READY:{adapter_id}", file=sys.stderr, flush=True)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
