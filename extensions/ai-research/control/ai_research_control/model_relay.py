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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
ALLOWED_TARGET_HOSTS = {"host.docker.internal", "127.0.0.1", "localhost"}
BRIDGE_PREFIX = "/api/ai-research/v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True, slots=True)
class RelaySettings:
    target: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "RelaySettings":
        return cls(
            target=validate_target(
                os.getenv(
                    "AI_RESEARCH_MODEL_RELAY_TARGET_URL",
                    "http://host.docker.internal:8000/api/ai-research/v1",
                )
            ),
            timeout_seconds=max(
                5.0,
                min(
                    float(os.getenv("AI_RESEARCH_MODEL_RELAY_TIMEOUT_SECONDS", "180")),
                    600.0,
                ),
            ),
        )


def validate_target(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ALLOWED_TARGET_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != BRIDGE_PREFIX
    ):
        raise ValueError("relay target must be the local AI Research v1 bridge")
    try:
        if parsed.port is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("relay target must include a valid port") from exc
    return f"http://{parsed.hostname}:{parsed.port}{BRIDGE_PREFIX}"


def create_app(settings: RelaySettings | None = None) -> FastAPI:
    relay = settings or RelaySettings.from_env()
    app = FastAPI(
        title="ModelMirror AI Research Model Relay",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "alive"}

    @app.get(f"{BRIDGE_PREFIX}/models", include_in_schema=False)
    async def models(request: Request):
        return await forward(request, relay, suffix="/models", body=b"")

    @app.post(f"{BRIDGE_PREFIX}/chat/completions", include_in_schema=False)
    async def chat_completions(request: Request):
        content_type = request.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise HTTPException(status_code=415, detail="application/json is required")
        body = await read_bounded_body(request)
        return await forward(request, relay, suffix="/chat/completions", body=body)

    return app


async def read_bounded_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="relay request exceeded size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def forward(
    request: Request,
    settings: RelaySettings,
    *,
    suffix: str,
    body: bytes,
):
    if request.url.query:
        raise HTTPException(status_code=400, detail="relay query parameters are forbidden")
    authorization = request.headers.get("authorization", "")
    scheme, separator, credential = authorization.partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not credential
        or len(authorization) > 4096
    ):
        raise HTTPException(status_code=401, detail="relay requires a bearer credential")
    headers = {
        "Authorization": authorization,
        "Accept": request.headers.get("accept", "application/json"),
    }
    if body:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    upstream_request = UrlRequest(
        settings.target + suffix,
        data=body or None,
        headers=headers,
        method=request.method,
    )
    try:
        upstream = await asyncio.to_thread(
            open_upstream, upstream_request, settings.timeout_seconds
        )
    except URLError as exc:
        raise HTTPException(status_code=503, detail="fixed model bridge unavailable") from exc
    status = int(getattr(upstream, "status", upstream.getcode()))
    if 300 <= status < 400:
        upstream.close()
        raise HTTPException(status_code=502, detail="fixed model bridge redirected")
    content_type = upstream.headers.get("Content-Type", "application/json")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type not in {"application/json", "text/event-stream"}:
        upstream.close()
        raise HTTPException(status_code=502, detail="fixed model bridge content type rejected")
    response_headers = {"Cache-Control": "no-store"}
    route_run_id = upstream.headers.get("X-ModelMirror-Route-Run-Id")
    if route_run_id and len(route_run_id) <= 256:
        response_headers["X-ModelMirror-Route-Run-Id"] = route_run_id
    if media_type == "text/event-stream":
        response_headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(
        bounded_response(upstream),
        status_code=status,
        media_type=media_type,
        headers=response_headers,
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
                raise RuntimeError("relay response exceeded size limit")
            yield chunk
    finally:
        upstream.close()


app = create_app()
