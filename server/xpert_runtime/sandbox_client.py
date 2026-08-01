from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol


class SandboxClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "sandbox_client_error") -> None:
        super().__init__(message)
        self.code = code


class SandboxClientProtocol(Protocol):
    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class SandboxSidecarClient:
    """Tiny JSON-line client for the private Unix socket sidecar protocol."""

    def __init__(self, socket_path: str | Path | None = None, *, timeout: float = 10.0) -> None:
        self.socket_path = Path(
            socket_path
            or os.getenv("SANDBOX_SOCKET_PATH", "").strip()
            or "/run/modelmirror-sandbox/sandbox.sock"
        )
        self.timeout = timeout

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise SandboxClientError(
                "Sandbox sidecar is unavailable.", code="sandbox_unavailable"
            ) from exc
        try:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(raw) > 16 * 1024 * 1024:
                raise SandboxClientError("Sandbox request is too large.", code="request_too_large")
            writer.write(raw)
            await writer.drain()
            response_raw = await asyncio.wait_for(reader.readline(), timeout=max(self.timeout, 305.0))
            response = json.loads(response_raw.decode("utf-8"))
            if not isinstance(response, dict):
                raise SandboxClientError("Sandbox sidecar returned an invalid response.")
            if not response.get("ok"):
                raise SandboxClientError(
                    str(response.get("error") or "Sandbox operation failed."),
                    code=str(response.get("code") or "sandbox_operation_failed"),
                )
            return response
        finally:
            writer.close()
            await writer.wait_closed()


class LocalSandboxClient:
    """Test adapter that invokes a SandboxEngine without a Docker sidecar."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self.engine.dispatch, payload)
        except Exception as exc:
            raise SandboxClientError(
                str(exc), code=str(getattr(exc, "code", "sandbox_operation_failed"))
            ) from exc
