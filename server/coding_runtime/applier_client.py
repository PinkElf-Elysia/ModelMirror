from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from .apply_models import ApplyFileReceipt, ApplyReceipt


MAX_APPLIER_FRAME_BYTES = 2 * 1024 * 1024
APPLIER_OPERATION_TIMEOUT_SECONDS = 90.0
SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ApplierClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "applier_unavailable") -> None:
        super().__init__(message)
        self.code = code


class CodingApplierClient:
    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = APPLIER_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"}, timeout=3.0)

    async def apply(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> ApplyReceipt:
        response = await self._request(
            {
                "action": "apply",
                "operation_id": operation_id,
                "revision": revision,
                "patch": patch,
                "paths": paths,
                "expected_fingerprint": expected_fingerprint,
            }
        )
        return _receipt_from_response(response)

    async def revert(self, receipt: ApplyReceipt) -> ApplyReceipt:
        response = await self._request(
            {
                "action": "revert",
                "receipt": _receipt_to_payload(receipt),
            }
        )
        return _receipt_from_response(response)

    async def reconcile(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> tuple[str, ApplyReceipt | None]:
        response = await self._request(
            {
                "action": "reconcile",
                "operation_id": operation_id,
                "revision": revision,
                "patch": patch,
                "paths": paths,
                "expected_fingerprint": expected_fingerprint,
            }
        )
        state = response.get("state")
        payload = response.get("receipt")
        if state not in {"not_applied", "applied", "conflict"}:
            raise ApplierClientError(
                "Coding application recovery response is invalid.",
                code="invalid_response",
            )
        if payload is None:
            if state == "applied":
                raise ApplierClientError(
                    "Coding application recovery receipt is missing.",
                    code="invalid_response",
                )
            return state, None
        if state != "applied":
            raise ApplierClientError(
                "Coding application recovery response is inconsistent.",
                code="invalid_response",
            )
        return state, _receipt_from_response({"receipt": payload})

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_APPLIER_FRAME_BYTES:
            raise ApplierClientError(
                "Coding application request is too large.",
                code="request_too_large",
            )
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=3.0,
            )
            writer.write(encoded)
            await writer.drain()
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=timeout if timeout is not None else self._timeout,
            )
        except TimeoutError as exc:
            raise ApplierClientError(
                "Coding application request timed out.",
                code="applier_timeout",
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise ApplierClientError(
                "Coding application service is unavailable.",
                code="applier_unavailable",
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if not raw or len(raw) > MAX_APPLIER_FRAME_BYTES:
            raise ApplierClientError(
                "Coding application response is invalid.",
                code="invalid_response",
            )
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApplierClientError(
                "Coding application response is invalid.",
                code="invalid_response",
            ) from exc
        if not isinstance(response, dict):
            raise ApplierClientError(
                "Coding application response is invalid.",
                code="invalid_response",
            )
        if response.get("ok") is not True:
            raw_code = response.get("code")
            code = (
                raw_code
                if isinstance(raw_code, str) and SAFE_ERROR_CODE.fullmatch(raw_code)
                else "applier_error"
            )
            raise ApplierClientError(
                "Coding application request failed.",
                code=code,
            )
        return response


def _receipt_to_payload(receipt: ApplyReceipt) -> dict[str, Any]:
    return {
        "apply_id": receipt.apply_id,
        "revision": receipt.revision,
        "snapshot_fingerprint": receipt.snapshot_fingerprint,
        "applied_at": receipt.applied_at,
        "files": [
            {
                "path": item.path,
                "existed_before": item.existed_before,
                "before_sha256": item.before_sha256,
                "after_sha256": item.after_sha256,
            }
            for item in receipt.files
        ],
    }


def _receipt_from_response(response: dict[str, Any]) -> ApplyReceipt:
    payload = response.get("receipt")
    if not isinstance(payload, dict) or set(payload) != {
        "apply_id",
        "revision",
        "snapshot_fingerprint",
        "applied_at",
        "files",
    }:
        raise ApplierClientError(
            "Coding application receipt is invalid.",
            code="invalid_response",
        )
    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > 20:
        raise ApplierClientError(
            "Coding application receipt is invalid.",
            code="invalid_response",
        )
    try:
        files = tuple(
            ApplyFileReceipt(
                path=item["path"],
                existed_before=item["existed_before"],
                before_sha256=item["before_sha256"],
                after_sha256=item["after_sha256"],
            )
            for item in raw_files
            if isinstance(item, dict)
            and set(item)
            == {
                "path",
                "existed_before",
                "before_sha256",
                "after_sha256",
            }
        )
        if len(files) != len(raw_files):
            raise ValueError("Receipt files are invalid")
        return ApplyReceipt(
            apply_id=payload["apply_id"],
            revision=payload["revision"],
            snapshot_fingerprint=payload["snapshot_fingerprint"],
            applied_at=payload["applied_at"],
            files=files,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApplierClientError(
            "Coding application receipt is invalid.",
            code="invalid_response",
        ) from exc
