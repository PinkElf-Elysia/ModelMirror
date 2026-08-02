from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Protocol

from server.coding_runtime.publish_models import (
    CodingPublishError,
    PublishManifest,
    PublishReceipt,
)

from .engine import (
    PUBLISH_PROXY_URL,
    CodingPublisherEngine,
    FixedGitRunner,
    HttpxGitHubTransport,
    PublisherConfig,
)


MAX_PUBLISHER_FRAME_BYTES = 256 * 1024
SOCKET_PATH = Path(
    os.getenv(
        "CODING_PUBLISHER_SOCKET_PATH",
        "/run/modelmirror-coding-publish/publisher.sock",
    )
)
TARGET_ROOT = Path("/target")
TEMPORARY_ROOT = Path("/temporary")
PRIVATE_KEY_PATH = Path("/run/secrets/coding-github-app-private-key.pem")


class PublisherEngine(Protocol):
    def health(self) -> dict[str, object]: ...

    def publish(self, manifest: PublishManifest) -> PublishReceipt: ...

    def reconcile(self, manifest: PublishManifest) -> tuple[str, PublishReceipt | None]: ...

    def mark_ready(
        self,
        manifest: PublishManifest,
        receipt: PublishReceipt,
    ) -> PublishReceipt: ...


class PublisherProtocolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class CodingPublisherServer:
    """Narrow Unix socket control plane for the fixed GitHub publisher."""

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        engine: PublisherEngine | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._engine: PublisherEngine | None = engine
        self._startup_error: str | None = None
        if engine is None:
            try:
                self._engine = _build_engine()
            except (CodingPublishError, OSError, ValueError):
                self._startup_error = "publisher_not_configured"

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_PUBLISHER_FRAME_BYTES:
                raise PublisherProtocolError("Publisher request is empty or too large.")
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PublisherProtocolError("Publisher request is invalid.") from exc
            if not isinstance(request, dict):
                raise PublisherProtocolError("Publisher request must be an object.")
            response = await self._dispatch(request)
            await self._send(writer, {"ok": True, **response})
        except (PublisherProtocolError, CodingPublishError) as exc:
            await self._send_error(writer, exc.code)
        except Exception:
            await self._send_error(writer, "publisher_internal_error")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "health":
            _require_keys(request, {"action"})
            if self._engine is None:
                return {
                    "service": "coding-publisher",
                    "configured": False,
                    "available": False,
                    "provider": "github",
                    "target": "fixed_repository",
                    "reason": self._startup_error or "publisher_unavailable",
                }
            return {"service": "coding-publisher", **self._engine.health()}
        if self._engine is None:
            raise CodingPublishError(
                "Coding Publisher is unavailable.",
                code=self._startup_error or "publisher_unavailable",
            )
        if action == "publish":
            _require_keys(request, {"action", "manifest"})
            receipt = await asyncio.to_thread(
                self._engine.publish,
                _manifest_from_payload(request["manifest"]),
            )
            return {"receipt": receipt.to_dict()}
        if action == "reconcile":
            _require_keys(request, {"action", "manifest"})
            state, receipt = await asyncio.to_thread(
                self._engine.reconcile,
                _manifest_from_payload(request["manifest"]),
            )
            return {
                "state": state,
                "receipt": receipt.to_dict() if receipt is not None else None,
            }
        if action == "ready":
            _require_keys(request, {"action", "manifest", "receipt"})
            receipt = await asyncio.to_thread(
                self._engine.mark_ready,
                _manifest_from_payload(request["manifest"]),
                _receipt_from_payload(request["receipt"]),
            )
            return {"receipt": receipt.to_dict()}
        raise PublisherProtocolError(
            "Publisher action is not supported.",
            code="unsupported_action",
        )

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_PUBLISHER_FRAME_BYTES + 1,
        )
        os.chmod(self._socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            self._socket_path.unlink(missing_ok=True)

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_PUBLISHER_FRAME_BYTES:
            raise PublisherProtocolError(
                "Publisher response is too large.",
                code="response_too_large",
            )
        writer.write(encoded)
        await writer.drain()

    async def _send_error(self, writer: asyncio.StreamWriter, code: str) -> None:
        await self._send(
            writer,
            {
                "ok": False,
                "code": code,
                "error": "Coding publish request failed.",
            },
        )


def _build_engine() -> CodingPublisherEngine:
    key_stat = PRIVATE_KEY_PATH.stat()
    if (
        not stat.S_ISREG(key_stat.st_mode)
        or PRIVATE_KEY_PATH.is_symlink()
        or not 1 <= key_stat.st_size <= 64 * 1024
    ):
        raise ValueError("GitHub App private key file is invalid")
    config = PublisherConfig(
        app_id=_positive_int_env("CODING_GITHUB_APP_ID"),
        installation_id=_positive_int_env("CODING_GITHUB_INSTALLATION_ID"),
        repository_id=_positive_int_env("CODING_GITHUB_REPOSITORY_ID"),
        repository=_required_env("CODING_GITHUB_REPOSITORY"),
        base_branch=_required_env("CODING_GITHUB_BASE_BRANCH", default="main"),
        private_key=PRIVATE_KEY_PATH.read_bytes(),
    )
    return CodingPublisherEngine(
        config,
        FixedGitRunner(TARGET_ROOT, TEMPORARY_ROOT, PUBLISH_PROXY_URL),
        HttpxGitHubTransport(PUBLISH_PROXY_URL),
    )


def _required_env(name: str, *, default: str = "") -> str:
    value = os.getenv(name, default)
    if not value or value != value.strip() or len(value) > 256:
        raise ValueError(f"{name} is invalid")
    return value


def _positive_int_env(name: str) -> int:
    value = _required_env(name)
    if len(value) > 20 or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} is invalid")
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} is invalid")
    return parsed


def _require_keys(request: dict[str, Any], expected: set[str]) -> None:
    if set(request) != expected:
        raise PublisherProtocolError("Publisher request fields are invalid.")


def _manifest_from_payload(payload: Any) -> PublishManifest:
    try:
        return PublishManifest.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise PublisherProtocolError("Publish manifest is invalid.") from exc


def _receipt_from_payload(payload: Any) -> PublishReceipt:
    try:
        return PublishReceipt.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise PublisherProtocolError("Publish receipt is invalid.") from exc


def main() -> None:
    asyncio.run(CodingPublisherServer().serve_forever())


if __name__ == "__main__":
    main()
