from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request as UrlRequest,
    build_opener,
)

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse


MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
ALLOWED_METHODS = ("GET", "HEAD", "POST", "PATCH", "DELETE")
FORWARDED_REQUEST_HEADERS = {
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "content-type": "Content-Type",
    "if-modified-since": "If-Modified-Since",
    "if-none-match": "If-None-Match",
    "origin": "Origin",
    "range": "Range",
}
FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-security-policy",
    "content-type",
    "etag",
    "last-modified",
    "permissions-policy",
    "referrer-policy",
    "x-artifact-sha256",
    "x-content-sha256",
    "x-content-type-options",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    target: str
    timeout_seconds: float
    expose_health: bool = False
    allowed_host_headers: tuple[str, ...] = ()


def _timeout_from_env() -> float:
    return max(
        5.0,
        min(
            float(os.getenv("AI_RESEARCH_CONSOLE_GATEWAY_TIMEOUT_SECONDS", "180")),
            600.0,
        ),
    )


def validate_target(value: str, *, expected_host: str, expected_port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("gateway target must be its fixed internal service")
    try:
        if parsed.port != expected_port:
            raise ValueError
    except ValueError as exc:
        raise ValueError("gateway target must use its fixed internal port") from exc
    return f"http://{expected_host}:{expected_port}"


def settings_from_env(
    variable: str,
    *,
    default: str,
    expected_host: str,
    expected_port: int,
    expose_health: bool = False,
) -> GatewaySettings:
    return GatewaySettings(
        target=validate_target(
            os.getenv(variable, default),
            expected_host=expected_host,
            expected_port=expected_port,
        ),
        timeout_seconds=_timeout_from_env(),
        expose_health=expose_health,
    )


def create_gateway_app(settings: GatewaySettings) -> FastAPI:
    app = FastAPI(
        title="ModelMirror AI Research Local Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    if settings.expose_health:

        @app.get("/gateway-healthz", include_in_schema=False)
        async def gateway_healthz() -> dict[str, str]:
            return {"status": "alive"}

    @app.api_route(
        "/{path:path}",
        methods=list(ALLOWED_METHODS),
        include_in_schema=False,
    )
    async def proxy(path: str, request: Request):
        return await forward(request, settings)

    return app


async def read_bounded_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="gateway request exceeded size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def forward(request: Request, settings: GatewaySettings):
    body = b""
    if request.method in {"POST", "PATCH", "DELETE"}:
        body = await read_bounded_body(request)
    elif request.headers.get("content-length") not in {None, "0"}:
        raise HTTPException(status_code=400, detail="request body is forbidden")

    raw_path = request.scope.get("raw_path", b"/")
    if not isinstance(raw_path, bytes) or not raw_path.startswith(b"/"):
        raise HTTPException(status_code=400, detail="invalid gateway path")
    try:
        path = raw_path.decode("ascii")
        query = request.scope.get("query_string", b"").decode("ascii")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid gateway URL encoding") from exc
    target_url = settings.target + path
    if query:
        target_url += "?" + query

    headers: dict[str, str] = {}
    if settings.allowed_host_headers:
        host = request.headers.get("host", "").casefold()
        allowed_hosts = {value.casefold() for value in settings.allowed_host_headers}
        if host not in allowed_hosts:
            raise HTTPException(status_code=400, detail="gateway host is not allowed")
        headers["Host"] = host
    for source, destination in FORWARDED_REQUEST_HEADERS.items():
        value = request.headers.get(source)
        if value and len(value) <= 4096 and "\r" not in value and "\n" not in value:
            headers[destination] = value
    if body:
        headers["Content-Length"] = str(len(body))

    upstream_request = UrlRequest(
        target_url,
        data=body if body else None,
        headers=headers,
        method=request.method,
    )
    try:
        upstream = await asyncio.to_thread(
            open_upstream,
            upstream_request,
            settings.timeout_seconds,
        )
    except URLError as exc:
        raise HTTPException(status_code=503, detail="internal UI service unavailable") from exc

    status_value = getattr(upstream, "status", None)
    status = int(status_value if status_value is not None else upstream.getcode())
    if 300 <= status < 400:
        upstream.close()
        raise HTTPException(status_code=502, detail="internal UI service redirected")

    response_headers: dict[str, str] = {}
    for name, value in upstream.headers.items():
        lowered = name.casefold()
        if (
            lowered in FORWARDED_RESPONSE_HEADERS
            and len(value) <= 4096
            and "\r" not in value
            and "\n" not in value
        ):
            response_headers[name] = value
    content_length = response_headers.get("Content-Length") or response_headers.get(
        "content-length"
    )
    if content_length is not None:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise ValueError
        except ValueError as exc:
            upstream.close()
            raise HTTPException(
                status_code=502,
                detail="internal UI response exceeded size limit",
            ) from exc
    response_headers.setdefault("X-Content-Type-Options", "nosniff")
    response_headers.setdefault("Referrer-Policy", "no-referrer")
    return StreamingResponse(
        bounded_response(upstream),
        status_code=status,
        headers=response_headers,
        media_type=None,
    )


def open_upstream(request: UrlRequest, timeout_seconds: float):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        return opener.open(request, timeout=timeout_seconds)
    except HTTPError as exc:
        return exc


def bounded_response(upstream) -> Iterator[bytes]:
    total = 0
    try:
        while True:
            chunk = upstream.read(CHUNK_BYTES)
            if not chunk:
                return
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise RuntimeError("internal UI response exceeded size limit")
            yield chunk
    finally:
        upstream.close()


def _apps() -> tuple[FastAPI, FastAPI, FastAPI]:
    timeout = _timeout_from_env()
    try:
        inspect_public_port = int(
            os.getenv("AI_RESEARCH_CONSOLE_GATEWAY_INSPECT_PUBLIC_PORT", "8793")
        )
        if not 1 <= inspect_public_port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Inspect gateway public port is invalid") from exc
    console = settings_from_env(
        "AI_RESEARCH_CONSOLE_GATEWAY_CONTROL_URL",
        default="http://ai-research-control:8080",
        expected_host="ai-research-control",
        expected_port=8080,
        expose_health=True,
    )
    tracking = settings_from_env(
        "AI_RESEARCH_CONSOLE_GATEWAY_TRACKING_URL",
        default="http://ai-research-tracking:5000",
        expected_host="ai-research-tracking",
        expected_port=5000,
    )
    inspect_view = settings_from_env(
        "AI_RESEARCH_CONSOLE_GATEWAY_INSPECT_URL",
        default="http://ai-research-inspect-view:7575",
        expected_host="ai-research-inspect-view",
        expected_port=7575,
    )
    return (
        create_gateway_app(GatewaySettings(console.target, timeout, True)),
        create_gateway_app(GatewaySettings(tracking.target, timeout)),
        create_gateway_app(
            GatewaySettings(
                inspect_view.target,
                timeout,
                allowed_host_headers=(
                    f"127.0.0.1:{inspect_public_port}",
                    f"localhost:{inspect_public_port}",
                ),
            )
        ),
    )


async def serve() -> None:
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",
                port=port,
                access_log=False,
                proxy_headers=False,
                server_header=False,
                date_header=False,
            )
        )
        for app, port in zip(_apps(), (8080, 8091, 8093), strict=True)
    ]
    for server in servers:
        server.install_signal_handlers = lambda: None
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    asyncio.run(serve())
