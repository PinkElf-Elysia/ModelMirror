from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol


class BrowserClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "browser_client_error") -> None:
        super().__init__(message)
        self.code = code


class BrowserClientProtocol(Protocol):
    async def request(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class BrowserSidecarClient:
    """JSON-line client for the private browser Unix socket."""

    def __init__(self, socket_path: str | Path | None = None, *, timeout: float = 35.0) -> None:
        self.socket_path = Path(
            socket_path
            or os.getenv("BROWSER_SOCKET_PATH", "").strip()
            or "/run/modelmirror-browser/browser.sock"
        )
        self.timeout = timeout

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise BrowserClientError(
                "Browser sidecar is unavailable.", code="browser_unavailable"
            ) from exc
        try:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(raw) > 2 * 1024 * 1024:
                raise BrowserClientError("Browser request is too large.", code="request_too_large")
            writer.write(raw)
            await writer.drain()
            response_raw = await asyncio.wait_for(
                reader.readline(), timeout=max(self.timeout, 125.0)
            )
            response = json.loads(response_raw.decode("utf-8"))
            if not isinstance(response, dict):
                raise BrowserClientError("Browser sidecar returned an invalid response.")
            if not response.get("ok"):
                raise BrowserClientError(
                    str(response.get("error") or "Browser operation failed."),
                    code=str(response.get("code") or "browser_operation_failed"),
                )
            return response
        finally:
            writer.close()
            await writer.wait_closed()
