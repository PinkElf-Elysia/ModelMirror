from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import os
import re
import socket

from .network_policy import EgressPolicy, NetworkPolicyError


MAX_HEADER_BYTES = 16 * 1024
PROVIDER_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")
DOCKER_DESKTOP_DNS_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class ProviderEgressPolicy:
    """Authenticates one Provider sidecar to an exact API domain set."""

    def __init__(
        self,
        *,
        token: str,
        allowed_domains: tuple[str, ...],
        allow_docker_desktop_dns_proxy: bool = False,
    ) -> None:
        if PROVIDER_TOKEN.fullmatch(token) is None:
            raise NetworkPolicyError(
                "Provider proxy token is invalid.", code="network_grant_key_invalid"
            )
        self._token = token
        self.allowed_domains = frozenset(
            EgressPolicy._normalize_domain(domain) for domain in allowed_domains
        )
        if not self.allowed_domains:
            raise NetworkPolicyError(
                "Provider domains are unavailable.", code="network_domain_not_allowed"
            )
        self.allow_docker_desktop_dns_proxy = allow_docker_desktop_dns_proxy

    def validate(self, token: str, *, domain: str) -> None:
        normalized = EgressPolicy._normalize_domain(domain)
        if not hmac.compare_digest(token, self._token):
            raise NetworkPolicyError(
                "Provider proxy authorization is invalid.",
                code="network_grant_invalid",
            )
        if normalized not in self.allowed_domains:
            raise NetworkPolicyError(
                "Provider destination is not allowed.",
                code="network_domain_not_allowed",
            )

    def validate_resolved_address(self, address: str) -> None:
        candidate = ipaddress.ip_address(address)
        if candidate.is_global:
            return
        if (
            self.allow_docker_desktop_dns_proxy
            and candidate.version == 4
            and candidate in DOCKER_DESKTOP_DNS_PROXY_NETWORK
        ):
            return
        raise NetworkPolicyError(
            "Private destination is denied.", code="network_private_address_denied"
        )


class EgressProxy:
    def __init__(
        self,
        policy: EgressPolicy | None = None,
        *,
        provider_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        if (policy is None) == (provider_policy is None):
            raise ValueError("exactly one proxy policy is required")
        self.policy = policy
        self.provider_policy = provider_policy

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            if len(header) > MAX_HEADER_BYTES:
                raise NetworkPolicyError("Proxy header is too large.", code="network_request_invalid")
            lines = header.decode("iso-8859-1").split("\r\n")
            method, authority, version = lines[0].split(" ", 2)
            if method != "CONNECT" or version != "HTTP/1.1" or authority.count(":") != 1:
                raise NetworkPolicyError("Only HTTPS CONNECT is allowed.", code="network_request_invalid")
            domain, port = authority.rsplit(":", 1)
            if port != "443":
                raise NetworkPolicyError("Only HTTPS port 443 is allowed.", code="network_request_invalid")
            authorization = next(
                (line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("proxy-authorization:")),
                "",
            )
            if not authorization.startswith("Basic "):
                raise NetworkPolicyError("Proxy authorization is required.", code="network_grant_invalid")
            try:
                username, token = (
                    base64.b64decode(authorization[6:], validate=True)
                    .decode("utf-8")
                    .split(":", 1)
                )
            except (ValueError, UnicodeError) as exc:
                raise NetworkPolicyError(
                    "Proxy authorization is invalid.", code="network_grant_invalid"
                ) from exc
            if username == "grant" and self.policy is not None:
                self.policy.validate_grant(token, domain=domain)
            elif username == "provider" and self.provider_policy is not None:
                self.provider_policy.validate(token, domain=domain)
            else:
                raise NetworkPolicyError(
                    "Proxy authorization is invalid.", code="network_grant_invalid"
                )
            addresses = await asyncio.get_running_loop().getaddrinfo(
                domain, 443, type=socket.SOCK_STREAM
            )
            selected: str | None = None
            for _family, _type, _protocol, _name, address in addresses:
                candidate = str(address[0])
                if self.provider_policy is not None:
                    self.provider_policy.validate_resolved_address(candidate)
                elif not ipaddress.ip_address(candidate).is_global:
                    raise NetworkPolicyError(
                        "Private destination is denied.",
                        code="network_private_address_denied",
                    )
                selected = selected or candidate
            if selected is None:
                raise NetworkPolicyError("Destination could not be resolved.", code="network_resolution_required")
            upstream_reader, upstream_writer = await asyncio.open_connection(selected, 443)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                self._relay(reader, upstream_writer),
                self._relay(upstream_reader, writer),
            )
        except Exception:
            if not writer.is_closing():
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                with contextlib.suppress(Exception):
                    await writer.drain()
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
        with contextlib.suppress(Exception):
            writer.write_eof()


async def run() -> None:
    provider_token = os.environ.get("CODING_WORKER_PROVIDER_EGRESS_TOKEN", "")
    if provider_token:
        allow_dns_proxy = os.environ.get(
            "CODING_WORKER_PROVIDER_ALLOW_DOCKER_DESKTOP_DNS_PROXY", "false"
        ).strip().lower()
        if allow_dns_proxy not in {"true", "false"}:
            raise NetworkPolicyError(
                "Provider DNS proxy setting is invalid.",
                code="network_policy_invalid",
            )
        domains = tuple(
            item.strip().lower()
            for item in os.environ.get(
                "CODING_WORKER_PROVIDER_NETWORK_DOMAINS", ""
            ).split(",")
            if item.strip()
        )
        proxy = EgressProxy(
            provider_policy=ProviderEgressPolicy(
                token=provider_token,
                allowed_domains=domains,
                allow_docker_desktop_dns_proxy=allow_dns_proxy == "true",
            )
        )
        port = 8081
    else:
        key = os.environ.get("CODING_WORKER_EGRESS_GRANT_KEY", "")
        domains = tuple(
            item.strip().lower()
            for item in os.environ.get("CODING_WORKER_NETWORK_DOMAINS", "").split(",")
            if item.strip()
        )
        policy = EgressPolicy(enabled=True, allowed_domains=domains, grant_key=key)
        proxy = EgressProxy(policy)
        port = 8080
    server = await asyncio.start_server(
        proxy.handle, "0.0.0.0", port, limit=MAX_HEADER_BYTES
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(run())
