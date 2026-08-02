from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from .publish_models import PublishManifest, PublishReceipt, PublishState


MAX_PUBLISHER_FRAME_BYTES = 256 * 1024
PUBLISH_OPERATION_TIMEOUT_SECONDS = 180.0
SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PublisherClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "publisher_unavailable") -> None:
        super().__init__(message)
        self.code = code


class CodingPublisherClient:
    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = PUBLISH_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"}, timeout=3.0)

    async def publish(self, manifest: PublishManifest) -> PublishReceipt:
        response = await self._request(
            {"action": "publish", "manifest": manifest.to_dict()}
        )
        return _receipt_from_response(response)

    async def reconcile(
        self,
        manifest: PublishManifest,
    ) -> tuple[str, PublishReceipt | None]:
        response = await self._request(
            {"action": "reconcile", "manifest": manifest.to_dict()},
            timeout=45.0,
        )
        state = response.get("state")
        payload = response.get("receipt")
        if state not in {
            PublishState.NOT_PUBLISHED.value,
            "branch_pushed",
            PublishState.DRAFT.value,
            PublishState.READY.value,
            PublishState.CONFLICT.value,
        }:
            raise PublisherClientError(
                "Publish recovery response is invalid.",
                code="invalid_response",
            )
        if payload is None:
            if state in {PublishState.DRAFT.value, PublishState.READY.value}:
                raise PublisherClientError(
                    "Publish recovery receipt is missing.",
                    code="invalid_response",
                )
            return state, None
        if state not in {PublishState.DRAFT.value, PublishState.READY.value}:
            raise PublisherClientError(
                "Publish recovery response is inconsistent.",
                code="invalid_response",
            )
        receipt = _receipt_from_response({"receipt": payload})
        if receipt.state.value != state:
            raise PublisherClientError(
                "Publish recovery state is inconsistent.",
                code="invalid_response",
            )
        return state, receipt

    async def mark_ready(
        self,
        manifest: PublishManifest,
        receipt: PublishReceipt,
    ) -> PublishReceipt:
        response = await self._request(
            {
                "action": "ready",
                "manifest": manifest.to_dict(),
                "receipt": receipt.to_dict(),
            },
            timeout=90.0,
        )
        result = _receipt_from_response(response)
        if result.state is not PublishState.READY:
            raise PublisherClientError(
                "Ready response is invalid.",
                code="invalid_response",
            )
        return result

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_PUBLISHER_FRAME_BYTES:
            raise PublisherClientError(
                "Publish request is too large.",
                code="request_too_large",
            )
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(self._socket_path),
                    limit=MAX_PUBLISHER_FRAME_BYTES + 1,
                ),
                timeout=3.0,
            )
            writer.write(encoded)
            await writer.drain()
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=self._timeout if timeout is None else timeout,
            )
        except TimeoutError as exc:
            raise PublisherClientError(
                "Publish request timed out.",
                code="publisher_timeout",
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise PublisherClientError(
                "Publish service is unavailable.",
                code="publisher_unavailable",
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if not raw or len(raw) > MAX_PUBLISHER_FRAME_BYTES:
            raise PublisherClientError(
                "Publish response is invalid.",
                code="invalid_response",
            )
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublisherClientError(
                "Publish response is invalid.",
                code="invalid_response",
            ) from exc
        if not isinstance(response, dict):
            raise PublisherClientError(
                "Publish response is invalid.",
                code="invalid_response",
            )
        if response.get("ok") is not True:
            raw_code = response.get("code")
            code = (
                raw_code
                if isinstance(raw_code, str) and SAFE_ERROR_CODE.fullmatch(raw_code)
                else "publisher_error"
            )
            raise PublisherClientError("Publish request failed.", code=code)
        return response


def _receipt_from_response(response: dict[str, Any]) -> PublishReceipt:
    payload = response.get("receipt")
    try:
        return PublishReceipt.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise PublisherClientError(
            "Publish receipt is invalid.",
            code="invalid_response",
        ) from exc
