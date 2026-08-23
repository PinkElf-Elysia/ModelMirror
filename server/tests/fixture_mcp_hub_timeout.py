"""Fixed TLS Streamable HTTP fixture for the MCP Hub timeout acceptance.

This process is only launched on an isolated Docker network. It uses the same
pinned official MCP SDK as the Hub sidecar and exposes one deterministic tool
whose response is deliberately slower than the production call timeout.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as types
import uvicorn
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route


FIXTURE_HOST = "hub-timeout.modelmirror.test"
FIXTURE_DELAY_SECONDS = 25
FIXTURE_BEARER_TOKEN = "modelmirror-static-token-fixture-only"


server = Server("modelmirror-mcp-hub-timeout-fixture")


def tools_only_capabilities(
    _notification_options: Any,
    _experimental_capabilities: dict[str, dict[str, Any]],
) -> types.ServerCapabilities:
    """Keep the controlled fixture inside the Hub's tools-only boundary."""

    return types.ServerCapabilities(
        tools=types.ToolsCapability(listChanged=False)
    )


server.get_capabilities = tools_only_capabilities  # type: ignore[method-assign]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="slow_read",
            description="Return a fixed read result after the timeout window.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 64}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="token_read",
            description="Return a fixed read result after Bearer authentication.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 64}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name not in {"slow_read", "token_read"} or set(arguments) != {"query"}:
        raise ValueError("fixed_fixture_tool_contract_denied")
    if name == "slow_read":
        await asyncio.sleep(FIXTURE_DELAY_SECONDS)
    return [types.TextContent(type="text", text="authenticated-read-completed")]


session_manager = StreamableHTTPSessionManager(
    app=server,
    json_response=True,
    stateless=True,
    security_settings=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[FIXTURE_HOST, f"{FIXTURE_HOST}:443"],
        allowed_origins=[],
    ),
)


@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    async with session_manager.run():
        yield


app = Starlette(
    routes=[Route("/mcp", endpoint=StreamableHTTPASGIApp(session_manager))],
    lifespan=lifespan,
)


class FixedBearerFixture:
    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            headers = {
                bytes(name).lower(): bytes(value)
                for name, value in scope.get("headers", [])
            }
            expected = f"Bearer {FIXTURE_BEARER_TOKEN}".encode("ascii")
            if headers.get(b"authorization") != expected:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await self.wrapped(scope, receive, send)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--require-bearer", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        FixedBearerFixture(app) if args.require_bearer else app,
        host="0.0.0.0",
        port=443,
        ssl_certfile=args.cert,
        ssl_keyfile=args.key,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
