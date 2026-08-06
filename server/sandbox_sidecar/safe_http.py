"""HTTPS client with DNS pinning and fail-closed public-network policy.

The public MCP adapters do not use requests/httpx directly.  Every outbound
request resolves the target first, rejects non-global addresses, connects to
one validated address, keeps the original hostname for TLS verification, and
repeats the checks for every redirect.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser


MAX_REDIRECTS = 3
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "user-agent",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
BLOCKED_HOST_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".home.arpa",
)
CHARSET = re.compile(r"charset=([A-Za-z0-9._-]+)", re.IGNORECASE)
SYNTHETIC_DNS_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class NetworkPolicyError(RuntimeError):
    """Raised when an outbound target violates the public-network policy."""


class ResponseLimitError(RuntimeError):
    """Raised when a response exceeds its adapter-owned byte limit."""


@dataclass(frozen=True, slots=True)
class SafeHttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        match = CHARSET.search(content_type)
        encoding = match.group(1) if match else "utf-8"
        try:
            return self.body.decode(encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def _normalized_host(hostname: str | None) -> str:
    raw = str(hostname or "").strip().rstrip(".").lower()
    if not raw or len(raw) > 253:
        raise NetworkPolicyError("目标 URL 必须包含有效公网主机名。")
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise NetworkPolicyError("目标主机名无法安全规范化。") from exc
    if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise NetworkPolicyError("目标主机属于本地或内部命名空间。")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise NetworkPolicyError("不允许使用 IP 字面量作为公网 MCP 目标。")


def validate_public_https_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
) -> tuple[str, str, int, str]:
    clean = str(url or "").strip()
    if not clean or len(clean) > 16_384:
        raise NetworkPolicyError("目标 URL 不能为空且不能超过 16384 个字符。")
    parsed = urlsplit(clean)
    if parsed.scheme.lower() != "https":
        raise NetworkPolicyError("仅允许访问公网 HTTPS 地址。")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkPolicyError("目标 URL 不得嵌入用户名或密码。")
    host = _normalized_host(parsed.hostname)
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise NetworkPolicyError("目标 URL 端口无效。") from exc
    if port != 443:
        raise NetworkPolicyError("公网 MCP 适配器仅允许 HTTPS 443 端口。")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise NetworkPolicyError("目标域名不在该适配器的固定出口清单中。")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    normalized = f"https://{host}{path}"
    return normalized, host, port, path


def resolve_public_addresses(host: str, port: int = 443) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise NetworkPolicyError("公网目标 DNS 解析失败。") from exc
    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise NetworkPolicyError("公网目标没有可用 DNS 地址。")
    allow_synthetic_dns = os.getenv(
        "MCP_PUBLIC_ALLOW_SYNTHETIC_DNS",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise NetworkPolicyError("DNS 返回了无效地址。") from exc
        if not address.is_global and not (
            allow_synthetic_dns and address in SYNTHETIC_DNS_NETWORK
        ):
            raise NetworkPolicyError("DNS 返回了私网、回环、链路本地或保留地址。")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        resolved_ip: str,
        *,
        port: int,
        timeout: float,
    ) -> None:
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._resolved_ip, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self.host,
        )


class SafeHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str] | None,
        timeout: float = 12.0,
        max_redirects: int = MAX_REDIRECTS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        minimum_intervals: dict[str, float] | None = None,
    ) -> None:
        self.allowed_hosts = allowed_hosts
        self.timeout = min(max(float(timeout), 1.0), 30.0)
        self.max_redirects = min(max(int(max_redirects), 0), MAX_REDIRECTS)
        self.max_response_bytes = min(
            max(int(max_response_bytes), 1),
            DEFAULT_MAX_RESPONSE_BYTES,
        )
        self.minimum_intervals = dict(minimum_intervals or {})
        self._next_request_at: dict[str, float] = {}
        self._rate_lock = threading.Lock()
        self._robots_cache: dict[tuple[str, str], RobotFileParser] = {}

    def _throttle(self, host: str) -> None:
        interval = max(float(self.minimum_intervals.get(host, 0.0)), 0.0)
        if not interval:
            return
        with self._rate_lock:
            now = time.monotonic()
            wait = max(self._next_request_at.get(host, now) - now, 0.0)
            self._next_request_at[host] = max(now, self._next_request_at.get(host, now)) + interval
        if wait:
            time.sleep(wait)

    @staticmethod
    def _headers(headers: dict[str, str] | None) -> dict[str, str]:
        output: dict[str, str] = {}
        for raw_name, raw_value in (headers or {}).items():
            name = str(raw_name).strip()
            value = str(raw_value).strip()
            if name.lower() not in ALLOWED_REQUEST_HEADERS:
                raise NetworkPolicyError("适配器尝试发送未授权的 HTTP Header。")
            if not value or "\r" in value or "\n" in value or len(value) > 1_024:
                raise NetworkPolicyError("HTTP Header 值无效。")
            output[name] = value
        output.setdefault(
            "User-Agent",
            "ModelMirror-MCP/1.0 (+https://github.com/PinkElf-Elysia/ModelMirror)",
        )
        output.setdefault("Accept", "application/json,text/plain,text/html;q=0.9")
        output.setdefault("Connection", "close")
        return output

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        max_response_bytes: int | None = None,
    ) -> SafeHttpResponse:
        current_url = str(url)
        current_method = str(method or "GET").upper()
        if current_method not in {"GET", "HEAD", "POST"}:
            raise NetworkPolicyError("公网适配器只允许 GET、HEAD 或固定 POST 请求。")
        current_body = body
        request_headers = self._headers(headers)
        response_limit = min(
            max(int(max_response_bytes or self.max_response_bytes), 1),
            self.max_response_bytes,
        )

        for redirect_count in range(self.max_redirects + 1):
            normalized, host, port, path = validate_public_https_url(
                current_url,
                allowed_hosts=self.allowed_hosts,
            )
            addresses = resolve_public_addresses(host, port)
            self._throttle(host)
            connection = _PinnedHTTPSConnection(
                host,
                addresses[0],
                port=port,
                timeout=self.timeout,
            )
            try:
                connection.request(
                    current_method,
                    path,
                    body=current_body,
                    headers=request_headers,
                )
                response = connection.getresponse()
                response_headers = {
                    str(name).lower(): str(value)
                    for name, value in response.getheaders()
                }
                if response.status in REDIRECT_STATUSES:
                    location = response_headers.get("location")
                    response.read(64 * 1024)
                    if not location:
                        raise NetworkPolicyError("远程服务返回了缺少 Location 的重定向。")
                    if redirect_count >= self.max_redirects:
                        raise NetworkPolicyError("远程服务重定向次数超过上限。")
                    current_url = urljoin(normalized, location)
                    if response.status == 303 or (
                        response.status in {301, 302} and current_method == "POST"
                    ):
                        current_method = "GET"
                        current_body = None
                    continue
                content_length = response_headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > response_limit:
                            raise ResponseLimitError("远程响应声明的大小超过适配器上限。")
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, response_limit + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > response_limit:
                        raise ResponseLimitError("远程响应超过适配器大小上限。")
                    chunks.append(chunk)
                return SafeHttpResponse(
                    url=normalized,
                    status=int(response.status),
                    headers=response_headers,
                    body=b"".join(chunks),
                )
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                raise NetworkPolicyError("公网 HTTPS 请求失败。") from exc
            finally:
                connection.close()
        raise NetworkPolicyError("远程服务重定向次数超过上限。")

    def assert_robots_allowed(self, url: str, user_agent: str) -> None:
        normalized, host, _, path = validate_public_https_url(
            url,
            allowed_hosts=self.allowed_hosts,
        )
        key = (host, user_agent)
        parser = self._robots_cache.get(key)
        if parser is None:
            robots_url = f"https://{host}/robots.txt"
            robots_client = SafeHttpClient(
                allowed_hosts=frozenset({host}),
                timeout=self.timeout,
                max_redirects=self.max_redirects,
                max_response_bytes=min(self.max_response_bytes, 256 * 1024),
                minimum_intervals=self.minimum_intervals,
            )
            response = robots_client.request(
                robots_url,
                headers={"User-Agent": user_agent, "Accept": "text/plain"},
            )
            if response.status in {401, 403} or response.status >= 500:
                raise NetworkPolicyError("无法确认 robots.txt 允许该页面，已按失败关闭处理。")
            parser = RobotFileParser()
            parser.set_url(robots_url)
            if 400 <= response.status < 500:
                parser.parse([])
            else:
                parser.parse(response.text().splitlines())
            self._robots_cache[key] = parser
        if not parser.can_fetch(user_agent, path):
            raise NetworkPolicyError("目标站点 robots.txt 不允许自动获取该页面。")
