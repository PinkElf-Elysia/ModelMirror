from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

try:
    from server.xpert_runtime.toolset import RuntimeToolError, RuntimeToolResult
except ModuleNotFoundError:
    from xpert_runtime.toolset import RuntimeToolError, RuntimeToolResult

from .credentials import CredentialStore
from .models import MCPConnectionProfile, ToolDefinition
from .store import ToolsetValidationError


URLValidator = Callable[[str, str], Awaitable[None]]
PROTECTED_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-language",
    "etag",
    "last-modified",
    "location",
    "retry-after",
    "x-request-id",
}


class SafeAPIExecutor:
    def __init__(
        self,
        credentials: CredentialStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        url_validator: URLValidator | None = None,
    ) -> None:
        self.credentials = credentials
        self.transport = transport
        self.url_validator = url_validator or validate_public_url
        self._oauth_tokens: dict[str, tuple[str, float]] = {}

    async def fetch_text(
        self,
        url: str,
        *,
        network_policy: str,
        timeout_seconds: int = 30,
        limit_bytes: int = 5 * 1024 * 1024,
    ) -> str:
        response = await self._request(
            "GET",
            url,
            network_policy=network_policy,
            timeout_seconds=timeout_seconds,
            redirect_limit=3,
            response_limit_bytes=limit_bytes,
            headers={"Accept": "application/json, application/yaml, text/yaml, application/xml, text/xml"},
        )
        try:
            return response["body_bytes"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolsetValidationError("Imported API document must be UTF-8 text.") from exc

    async def execute(
        self,
        connection: MCPConnectionProfile,
        tool: ToolDefinition,
        arguments: dict[str, Any],
    ) -> RuntimeToolResult:
        execution = dict(tool.execution or {})
        try:
            method, target_url, query, headers, body, form = self._compile_request(
                connection,
                execution,
                arguments,
            )
            await self._apply_auth(connection, query=query, headers=headers)
            result = await self._request(
                method,
                target_url,
                network_policy=connection.network_policy,
                timeout_seconds=connection.timeout_seconds,
                redirect_limit=connection.redirect_limit,
                response_limit_bytes=connection.response_limit_bytes,
                headers=headers,
                params=query,
                json_body=body,
                form=form,
            )
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                tool.exposed_name,
                str(exc)[:500],
                code="api_toolset_call_failed",
            ) from exc
        content_type = str(result["headers"].get("content-type") or "")
        text = _safe_body_text(result["body_bytes"], content_type)
        return RuntimeToolResult(
            output=text,
            content=[{"type": "text", "text": text}],
            is_error=int(result["status_code"]) >= 400,
            metadata={
                "toolset_kind": execution.get("kind"),
                "status_code": result["status_code"],
                "content_type": content_type,
                "response_headers": {
                    key: value
                    for key, value in result["headers"].items()
                    if key.lower() in SAFE_RESPONSE_HEADERS
                },
                "output_length": len(text),
                "truncated": result["truncated"],
            },
        )

    def _compile_request(
        self,
        connection: MCPConnectionProfile,
        execution: dict[str, Any],
        arguments: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], dict[str, str], Any, dict[str, Any] | None]:
        kind = str(execution.get("kind") or "")
        if kind == "odata":
            return self._compile_odata_request(connection, execution, arguments)
        if kind != "openapi":
            raise ToolsetValidationError("Unsupported API Toolset execution kind.")
        method = str(execution.get("method") or "GET").upper()
        path = str(execution.get("path") or "")
        path_values = dict(arguments.get("path") or {})
        for name, value in path_values.items():
            path = path.replace(
                "{" + str(name) + "}",
                quote(str(value), safe=""),
            )
        if "{" in path or "}" in path:
            raise ToolsetValidationError("Required OpenAPI path parameters are missing.")
        query = dict(arguments.get("query") or {})
        raw_headers = dict(arguments.get("headers") or {})
        headers: dict[str, str] = {"Accept": "application/json, text/plain;q=0.9, */*;q=0.2"}
        for name, value in raw_headers.items():
            clean_name = str(name).strip()
            if clean_name.lower() in PROTECTED_HEADERS:
                raise ToolsetValidationError(f"Protected HTTP header is not allowed: {clean_name}")
            headers[clean_name] = str(value)
        content_type = str(execution.get("content_type") or "")
        body = arguments.get("body")
        form = None
        if content_type == "application/json" and body is not None:
            headers["Content-Type"] = content_type
        elif content_type == "application/x-www-form-urlencoded" and isinstance(body, dict):
            headers["Content-Type"] = content_type
            form = body
            body = None
        target_url = _join_base_path(connection.api_base_url, path)
        return method, target_url, query, headers, body, form

    def _compile_odata_request(
        self,
        connection: MCPConnectionProfile,
        execution: dict[str, Any],
        arguments: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], dict[str, str], Any, None]:
        operation = str(execution.get("operation") or "")
        method = str(execution.get("method") or "GET").upper()
        path = str(execution.get("path") or "")
        query: dict[str, Any] = {}
        body = arguments.get("body")
        if operation == "get":
            keys = list(execution.get("keys") or [])
            raw_key = dict(arguments.get("key") or {})
            if set(raw_key) != set(keys):
                raise ToolsetValidationError("OData entity key is incomplete.")
            encoded = ",".join(
                f"{name}={_odata_literal(raw_key[name])}" for name in keys
            )
            path = f"{path}({encoded})"
        elif operation == "list":
            selected = list(arguments.get("select") or [])
            if selected:
                query["$select"] = ",".join(selected)
            filters = list(arguments.get("filter") or [])
            if filters:
                query["$filter"] = " and ".join(_odata_filter(item) for item in filters)
            orderby = list(arguments.get("orderby") or [])
            if orderby:
                query["$orderby"] = ",".join(
                    f"{item['field']} {item.get('direction', 'asc')}" for item in orderby
                )
            if arguments.get("top") is not None:
                query["$top"] = int(arguments["top"])
            if arguments.get("skip") is not None:
                query["$skip"] = int(arguments["skip"])
        elif operation in {"function", "action"}:
            if operation == "function" and arguments:
                encoded = ",".join(
                    f"{name}={_odata_literal(value)}"
                    for name, value in arguments.items()
                )
                path = f"{path}({encoded})"
                body = None
            elif operation == "action":
                body = dict(arguments)
        headers = {
            "Accept": "application/json",
            "OData-Version": "4.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        return method, _join_base_path(connection.api_base_url, path), query, headers, body, None

    async def _apply_auth(
        self,
        connection: MCPConnectionProfile,
        *,
        query: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        auth = connection.api_auth
        if auth.auth_type == "none":
            return
        if auth.auth_type == "api_key":
            if not auth.credential_id or not auth.api_key_name:
                raise ToolsetValidationError("API key credential and name are required.")
            value = self.credentials.resolve(auth.credential_id)
            if auth.api_key_location == "query":
                query[auth.api_key_name] = value
            else:
                if auth.api_key_name.lower() in PROTECTED_HEADERS:
                    raise ToolsetValidationError("API key header name is protected.")
                headers[auth.api_key_name] = value
            return
        if auth.auth_type == "bearer":
            value = self.credentials.resolve(auth.credential_id)
            headers["Authorization"] = f"Bearer {value}"
            return
        if auth.auth_type == "basic":
            username = self.credentials.resolve(auth.username_credential_id)
            password = self.credentials.resolve(auth.password_credential_id)
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
            return
        if auth.auth_type == "oauth2_client_credentials":
            headers["Authorization"] = f"Bearer {await self._oauth_token(connection)}"
            return
        raise ToolsetValidationError("Unsupported API authentication type.")

    async def _oauth_token(self, connection: MCPConnectionProfile) -> str:
        auth = connection.api_auth
        cache_key = "|".join(
            [
                auth.token_url,
                auth.client_id_credential_id,
                auth.client_secret_credential_id,
                " ".join(auth.scopes),
            ]
        )
        cached = self._oauth_tokens.get(cache_key)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        client_id = self.credentials.resolve(auth.client_id_credential_id)
        client_secret = self.credentials.resolve(auth.client_secret_credential_id)
        encoded = base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
        response = await self._request(
            "POST",
            auth.token_url,
            network_policy=connection.network_policy,
            timeout_seconds=connection.timeout_seconds,
            redirect_limit=0,
            response_limit_bytes=256 * 1024,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            form={
                "grant_type": "client_credentials",
                "scope": " ".join(auth.scopes),
            },
        )
        try:
            payload = json.loads(response["body_bytes"])
            token = str(payload["access_token"])
            expires_in = max(60, min(int(payload.get("expires_in") or 3600), 86400))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolsetValidationError("OAuth token response is invalid.") from exc
        self._oauth_tokens[cache_key] = (token, time.time() + expires_in)
        return token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        network_policy: str,
        timeout_seconds: int,
        redirect_limit: int,
        response_limit_bytes: int,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        form: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current_method = method.upper()
        current_url = url
        current_json = json_body
        current_form = form
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            for redirect_index in range(redirect_limit + 1):
                await self.url_validator(current_url, network_policy)
                async with client.stream(
                    current_method,
                    current_url,
                    params=params if redirect_index == 0 else None,
                    headers=headers,
                    json=current_json,
                    data=current_form,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_index >= redirect_limit:
                            raise ToolsetValidationError("HTTP redirect limit exceeded.")
                        redirected_url = urljoin(str(response.url), location)
                        if _url_origin(redirected_url) != _url_origin(str(response.url)):
                            raise ToolsetValidationError(
                                "Cross-origin HTTP redirects are blocked to protect credentials."
                            )
                        current_url = redirected_url
                        if response.status_code == 303:
                            current_method = "GET"
                            current_json = None
                            current_form = None
                        continue
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        remaining = response_limit_bytes - size
                        if remaining <= 0:
                            truncated = True
                            break
                        chunks.append(chunk[:remaining])
                        size += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            truncated = True
                            break
                    return {
                        "status_code": response.status_code,
                        "headers": {
                            key.lower(): value for key, value in response.headers.items()
                        },
                        "body_bytes": b"".join(chunks),
                        "truncated": truncated,
                    }
        raise ToolsetValidationError("HTTP request did not complete.")


async def validate_public_url(url: str, network_policy: str) -> None:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ToolsetValidationError("Only HTTP and HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise ToolsetValidationError("URL credentials are not allowed.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ToolsetValidationError("URL hostname is required.")
    if hostname == "localhost" or hostname.endswith((".local", ".internal")):
        if network_policy != "trusted_private":
            raise ToolsetValidationError("Local and private hostnames are blocked.")
    if hostname in {"metadata.google.internal", "host.docker.internal"}:
        raise ToolsetValidationError("Metadata and host bridge addresses are blocked.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ToolsetValidationError("URL hostname could not be resolved.") from exc
    addresses = {row[4][0] for row in results}
    if not addresses:
        raise ToolsetValidationError("URL hostname has no usable addresses.")
    if network_policy == "trusted_private":
        return
    for raw_address in addresses:
        address = ipaddress.ip_address(raw_address)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ToolsetValidationError("Private or reserved network targets are blocked.")


def _join_base_path(base_url: str, path: str) -> str:
    base = str(base_url or "").strip()
    if not base:
        raise ToolsetValidationError("API base URL is required.")
    parsed = urlsplit(base)
    base_path = parsed.path.rstrip("/") + "/"
    joined_path = base_path + path.lstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, joined_path, "", ""))


def _url_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, (parsed.hostname or "").lower(), port


def _odata_filter(item: Any) -> str:
    if not isinstance(item, dict):
        raise ToolsetValidationError("OData filter item must be an object.")
    field = str(item.get("field") or "")
    operation = str(item.get("op") or "")
    if not field or operation not in {
        "eq",
        "ne",
        "gt",
        "ge",
        "lt",
        "le",
        "contains",
        "startswith",
    }:
        raise ToolsetValidationError("OData filter operation is invalid.")
    value = _odata_literal(item.get("value"))
    if operation in {"contains", "startswith"}:
        return f"{operation}({field},{value})"
    return f"{field} {operation} {value}"


def _odata_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _safe_body_text(body: bytes, content_type: str) -> str:
    text = body.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            pass
    return text
