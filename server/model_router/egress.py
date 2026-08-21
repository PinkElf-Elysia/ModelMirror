from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx


INTERNAL_ALLOWLIST_ENV = "MODEL_MIRROR_PROVIDER_INTERNAL_ALLOWLIST"
PERMANENTLY_BLOCKED_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
PERMANENTLY_BLOCKED_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


class ProviderEgressError(httpx.RequestError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AuthorizedProviderTarget:
    original_url: str
    pinned_urls: tuple[str, ...]
    host_header: str
    sni_hostname: str | None

    def request_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        merged = {
            key: value
            for key, value in (headers or {}).items()
            if key.casefold() != "host"
        }
        merged["Host"] = self.host_header
        return merged

    @property
    def extensions(self) -> dict[str, str]:
        return {"sni_hostname": self.sni_hostname} if self.sni_hostname else {}


Resolver = Callable[[str, int], Iterable[str]]


def _system_resolver(host: str, port: int) -> Iterable[str]:
    return {
        item[4][0]
        for item in socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    }


class ProviderEgressPolicy:
    def __init__(
        self,
        *,
        internal_allowlist: str | Iterable[str] | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        raw = (
            internal_allowlist
            if internal_allowlist is not None
            else os.getenv(INTERNAL_ALLOWLIST_ENV, "")
        )
        entries = raw.split(",") if isinstance(raw, str) else raw
        self.internal_allowlist = frozenset(
            self._normalize_allowlist_entry(item) for item in entries if str(item).strip()
        )
        self._resolver = resolver or _system_resolver

    def validate_for_storage(self, value: str) -> str:
        parsed = self._parse(value)
        host = self._normalized_host(parsed)
        port = self._port(parsed)
        authority = self._authority(host, port)
        allowlisted = authority in self.internal_allowlist
        if self._is_metadata_host(host):
            raise self._blocked()
        literal = self._literal_ip(host)
        if literal is not None:
            self._validate_addresses((literal,), allowlisted=allowlisted)
        if parsed.scheme == "http" and not allowlisted:
            raise ProviderEgressError(
                "provider_https_required",
                "公网模型服务必须使用 HTTPS；内网 HTTP 需要精确 host:port 白名单。",
            )
        return self._normalized_url(parsed, host, port)

    async def authorize(self, value: str) -> AuthorizedProviderTarget:
        normalized = self.validate_for_storage(value)
        parsed = urlsplit(normalized)
        host = self._normalized_host(parsed)
        port = self._port(parsed)
        authority = self._authority(host, port)
        allowlisted = authority in self.internal_allowlist
        literal = self._literal_ip(host)
        if literal is not None:
            addresses = (literal,)
        else:
            try:
                resolved = await asyncio.to_thread(self._resolver, host, port)
                addresses = tuple(
                    sorted(
                        {ipaddress.ip_address(item) for item in resolved},
                        key=lambda item: (item.version, str(item)),
                    )
                )
            except (OSError, ValueError) as exc:
                raise ProviderEgressError(
                    "provider_dns_failed",
                    "模型服务地址无法解析，请检查主机名和网络配置。",
                ) from exc
        if not addresses:
            raise ProviderEgressError(
                "provider_dns_failed",
                "模型服务地址没有可用的网络解析结果。",
            )
        self._validate_addresses(addresses, allowlisted=allowlisted)
        pinned = tuple(self._pin_url(parsed, address) for address in addresses)
        return AuthorizedProviderTarget(
            original_url=normalized,
            pinned_urls=pinned,
            host_header=self._host_header(host, port, parsed.scheme),
            sni_hostname=host if parsed.scheme == "https" else None,
        )

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        stream: bool = False,
        **kwargs: object,
    ) -> httpx.Response:
        target = await self.authorize(url)
        last_error: Exception | None = None
        for pinned_url in target.pinned_urls:
            request = client.build_request(
                method,
                pinned_url,
                headers=target.request_headers(headers),
                extensions=target.extensions,
                **kwargs,
            )
            try:
                return await client.send(
                    request,
                    stream=stream,
                    follow_redirects=False,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ProviderEgressError(
            "provider_unreachable",
            "模型服务没有可连接的已批准地址。",
        )

    @staticmethod
    def _parse(value: str) -> SplitResult:
        raw = str(value or "").strip().rstrip("/")
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderEgressError(
                "invalid_address",
                "请输入以 http:// 或 https:// 开头的模型服务地址。",
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderEgressError(
                "invalid_address",
                "模型服务地址不能包含凭据、查询参数或片段。",
            )
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ProviderEgressError("invalid_address", "模型服务端口无效。") from exc
        return parsed

    @staticmethod
    def _normalized_host(parsed: SplitResult) -> str:
        host = (parsed.hostname or "").rstrip(".").casefold()
        if not host or any(character.isspace() for character in host):
            raise ProviderEgressError("invalid_address", "模型服务主机名无效。")
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ProviderEgressError("invalid_address", "模型服务主机名无效。") from exc
        if ProviderEgressPolicy._literal_ip(ascii_host) is not None:
            return ascii_host
        labels = ascii_host.split(".")
        if (
            len(ascii_host) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not all(character.isalnum() or character == "-" for character in label)
                for label in labels
            )
        ):
            raise ProviderEgressError("invalid_address", "模型服务主机名无效。")
        return ascii_host

    @staticmethod
    def _port(parsed: SplitResult) -> int:
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    @staticmethod
    def _authority(host: str, port: int) -> str:
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    @classmethod
    def _normalize_allowlist_entry(cls, value: object) -> str:
        raw = str(value).strip().casefold()
        if "://" in raw or any(mark in raw for mark in ("/", "?", "#", "*")):
            raise ProviderEgressError(
                "invalid_internal_allowlist",
                "Provider 内网白名单只接受精确 host:port。",
            )
        parsed = urlsplit(f"//{raw}")
        host = cls._normalized_host(parsed)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ProviderEgressError(
                "invalid_internal_allowlist",
                "Provider 内网白名单端口无效。",
            ) from exc
        if port is None:
            raise ProviderEgressError(
                "invalid_internal_allowlist",
                "Provider 内网白名单必须包含端口。",
            )
        return cls._authority(host, port)

    @staticmethod
    def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    @staticmethod
    def _is_metadata_host(host: str) -> bool:
        return host in PERMANENTLY_BLOCKED_HOSTS or host.endswith(".metadata.google.internal")

    @classmethod
    def _validate_addresses(
        cls,
        addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address],
        *,
        allowlisted: bool,
    ) -> None:
        for address in addresses:
            if (
                address in PERMANENTLY_BLOCKED_IPS
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or address.is_reserved
            ):
                raise cls._blocked()
            unsafe = address.is_private or address.is_loopback
            if unsafe and not allowlisted:
                raise cls._blocked()

    @staticmethod
    def _blocked() -> ProviderEgressError:
        return ProviderEgressError(
            "provider_address_blocked",
            "模型服务地址解析到受保护或保留网络；仅允许公网 HTTPS 或显式内网白名单。"
            "若这是公网域名，请检查容器 DNS 或 VPN Fake-IP，确保其解析为真实公网地址；"
            "保留地址不能通过白名单放行。",
        )

    @staticmethod
    def _normalized_url(parsed: SplitResult, host: str, port: int) -> str:
        default = 443 if parsed.scheme == "https" else 80
        authority_host = f"[{host}]" if ":" in host else host
        netloc = authority_host if port == default else f"{authority_host}:{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _pin_url(
        parsed: SplitResult,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> str:
        default = 443 if parsed.scheme == "https" else 80
        host = f"[{address}]" if address.version == 6 else str(address)
        port = parsed.port or default
        netloc = host if port == default else f"{host}:{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))

    @staticmethod
    def _host_header(host: str, port: int, scheme: str) -> str:
        default = 443 if scheme == "https" else 80
        display = f"[{host}]" if ":" in host else host
        return display if port == default else f"{display}:{port}"


async def request_provider_url(
    client: httpx.AsyncClient,
    policy: ProviderEgressPolicy,
    connection_id: str | None,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    stream: bool = False,
    **kwargs: object,
) -> httpx.Response:
    """Apply egress authorization only to control-plane managed connections."""

    if connection_id:
        return await policy.request(
            client,
            method,
            url,
            headers=headers,
            stream=stream,
            **kwargs,
        )
    request = client.build_request(
        method,
        url,
        headers=headers,
        **kwargs,
    )
    return await client.send(request, stream=stream)


@asynccontextmanager
async def stream_provider_url(
    client: httpx.AsyncClient,
    policy: ProviderEgressPolicy,
    connection_id: str | None,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    **kwargs: object,
) -> AsyncIterator[httpx.Response]:
    response = await request_provider_url(
        client,
        policy,
        connection_id,
        method,
        url,
        headers=headers,
        stream=True,
        **kwargs,
    )
    try:
        yield response
    finally:
        await response.aclose()
