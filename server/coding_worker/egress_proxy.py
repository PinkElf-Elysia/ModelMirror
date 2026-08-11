from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import os
import socket

from .network_policy import EgressPolicy, NetworkPolicyError


MAX_HEADER_BYTES = 16 * 1024


class EgressProxy:
    def __init__(self, policy: EgressPolicy) -> None:
        self.policy = policy

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
            username, token = base64.b64decode(authorization[6:]).decode("utf-8").split(":", 1)
            if username != "grant":
                raise NetworkPolicyError("Proxy authorization is invalid.", code="network_grant_invalid")
            self.policy.validate_grant(token, domain=domain)
            addresses = await asyncio.get_running_loop().getaddrinfo(
                domain, 443, type=socket.SOCK_STREAM
            )
            selected: str | None = None
            for _family, _type, _protocol, _name, address in addresses:
                candidate = str(address[0])
                if not ipaddress.ip_address(candidate).is_global:
                    raise NetworkPolicyError("Private destination is denied.", code="network_private_address_denied")
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
    key = os.environ.get("CODING_WORKER_EGRESS_GRANT_KEY", "")
    domains = tuple(
        item.strip().lower()
        for item in os.environ.get("CODING_WORKER_NETWORK_DOMAINS", "").split(",")
        if item.strip()
    )
    policy = EgressPolicy(enabled=True, allowed_domains=domains, grant_key=key)
    proxy = EgressProxy(policy)
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 8080, limit=MAX_HEADER_BYTES)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(run())
