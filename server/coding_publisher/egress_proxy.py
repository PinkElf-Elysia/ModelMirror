from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import socket
from collections.abc import Sequence
from typing import Any


ALLOWED_HOSTS = frozenset({"api.github.com", "github.com"})
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
MAX_HEADER_BYTES = 16 * 1024
CONNECT_TIMEOUT_SECONDS = 10
TUNNEL_TIMEOUT_SECONDS = 180
MAX_CONNECTIONS = 32
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
_SYNTHETIC_DNS_RANGE = ipaddress.ip_network("198.18.0.0/15")


class EgressPolicyError(RuntimeError):
    pass


class GitHubEgressProxy:
    """CONNECT-only proxy restricted to GitHub API and Git smart HTTPS."""

    def __init__(self, *, allow_synthetic_dns: bool = False) -> None:
        self._allow_synthetic_dns = allow_synthetic_dns
        self._slots = asyncio.Semaphore(MAX_CONNECTIONS)

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        async with self._slots:
            upstream_writer: asyncio.StreamWriter | None = None
            try:
                raw = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
                if len(raw) > MAX_HEADER_BYTES:
                    raise EgressPolicyError("Proxy header is too large")
                host = _parse_connect_request(raw)
                upstream_reader, upstream_writer = await self._connect(host)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await asyncio.wait_for(
                    _tunnel(reader, writer, upstream_reader, upstream_writer),
                    timeout=TUNNEL_TIMEOUT_SECONDS,
                )
            except (EgressPolicyError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                await _respond(writer, 403, "Forbidden")
            except asyncio.TimeoutError:
                await _respond(writer, 504, "Gateway Timeout")
            except (OSError, socket.gaierror):
                await _respond(writer, 502, "Bad Gateway")
            finally:
                if upstream_writer is not None:
                    upstream_writer.close()
                    with contextlib.suppress(Exception):
                        await upstream_writer.wait_closed()
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def _connect(
        self,
        host: str,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        addresses = await _resolve_public_addresses(
            host,
            allow_synthetic_dns=self._allow_synthetic_dns,
        )
        last_error: OSError | None = None
        for family, address in addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(address, 443, family=family),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise EgressPolicyError("GitHub hostname has no safe address")

    async def serve_forever(self) -> None:
        server = await asyncio.start_server(
            self.handle,
            host=LISTEN_HOST,
            port=LISTEN_PORT,
            limit=MAX_HEADER_BYTES + 1,
        )
        async with server:
            await server.serve_forever()


def _parse_connect_request(raw: bytes) -> str:
    if len(raw) > MAX_HEADER_BYTES or not raw.endswith(b"\r\n\r\n"):
        raise EgressPolicyError("Proxy request is invalid")
    try:
        lines = raw[:-4].decode("ascii", errors="strict").split("\r\n")
    except UnicodeDecodeError as exc:
        raise EgressPolicyError("Proxy request is invalid") from exc
    if not lines or len(lines) > 64:
        raise EgressPolicyError("Proxy request is invalid")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] != "HTTP/1.1":
        raise EgressPolicyError("Only HTTPS CONNECT is allowed")
    authority = parts[1]
    if authority.count(":") != 1:
        raise EgressPolicyError("Proxy target is invalid")
    host, port = authority.rsplit(":", 1)
    if host not in ALLOWED_HOSTS or port != "443":
        raise EgressPolicyError("Proxy target is not allowlisted")
    host_headers: list[str] = []
    for line in lines[1:]:
        if not line or ":" not in line:
            raise EgressPolicyError("Proxy header is invalid")
        name, value = line.split(":", 1)
        if _HEADER_NAME_PATTERN.fullmatch(name) is None:
            raise EgressPolicyError("Proxy header is invalid")
        if any(
            (ord(character) < 32 and character != "\t") or ord(character) == 127
            for character in value
        ):
            raise EgressPolicyError("Proxy header is invalid")
        if name.lower() == "proxy-authorization":
            raise EgressPolicyError("Proxy credentials are not accepted")
        if name.lower() == "host":
            host_headers.append(value.strip())
    if host_headers and host_headers != [authority]:
        raise EgressPolicyError("Proxy Host header is invalid")
    return host


async def _resolve_public_addresses(
    host: str,
    *,
    allow_synthetic_dns: bool,
) -> tuple[tuple[int, str], ...]:
    if host not in ALLOWED_HOSTS:
        raise EgressPolicyError("Proxy target is not allowlisted")
    loop = asyncio.get_running_loop()
    records: Sequence[tuple[int, int, int, str, Any]] = await loop.getaddrinfo(
        host,
        443,
        type=socket.SOCK_STREAM,
    )
    addresses: list[tuple[int, str]] = []
    for family, socktype, _, _, sockaddr in records:
        if socktype != socket.SOCK_STREAM or family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = ipaddress.ip_address(sockaddr[0])
        if not _address_is_allowed(address, allow_synthetic_dns=allow_synthetic_dns):
            continue
        item = (family, str(address))
        if item not in addresses:
            addresses.append(item)
    if not addresses:
        raise EgressPolicyError("GitHub hostname has no safe address")
    return tuple(addresses)


def _address_is_allowed(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_synthetic_dns: bool,
) -> bool:
    return bool(
        address.is_global
        or (allow_synthetic_dns and address in _SYNTHETIC_DNS_RANGE)
    )


async def _tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    async def copy(
        source: asyncio.StreamReader,
        destination: asyncio.StreamWriter,
    ) -> None:
        while chunk := await source.read(64 * 1024):
            destination.write(chunk)
            await destination.drain()

    tasks = {
        asyncio.create_task(copy(client_reader, upstream_writer)),
        asyncio.create_task(copy(upstream_reader, client_writer)),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done | pending:
        with contextlib.suppress(asyncio.CancelledError, OSError):
            await task


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    if writer.is_closing():
        return
    with contextlib.suppress(OSError):
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode(
                "ascii"
            )
        )
        await writer.drain()


def main() -> None:
    allow_synthetic_dns = os.getenv("CODING_GITHUB_ALLOW_SYNTHETIC_DNS", "false").lower()
    if allow_synthetic_dns not in {"true", "false"}:
        raise SystemExit("CODING_GITHUB_ALLOW_SYNTHETIC_DNS must be true or false")
    asyncio.run(
        GitHubEgressProxy(
            allow_synthetic_dns=allow_synthetic_dns == "true",
        ).serve_forever()
    )


if __name__ == "__main__":
    main()
