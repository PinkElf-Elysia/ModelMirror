from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from .applier_client import _receipt_from_response, _receipt_to_payload
from .apply_models import ApplyReceipt
from .committer_client import (
    _commit_receipt_from_response,
    _commit_receipt_to_payload,
)
from .commit_models import CommitReceipt


MAX_PROJECT_WRITER_FRAME_BYTES = 2 * 1024 * 1024
PROJECT_WRITER_TIMEOUT_SECONDS = 90.0
SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ProjectWriterClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "project_writer_unavailable") -> None:
        super().__init__(message)
        self.code = code


class CodingProjectWriterClient:
    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = PROJECT_WRITER_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"}, timeout=3.0)

    async def apply(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> ApplyReceipt:
        response = await self._request(
            {
                "action": "apply",
                "project_id": project_id,
                "expected_head": expected_head,
                "operation_id": operation_id,
                "revision": revision,
                "patch": patch,
                "paths": paths,
                "expected_fingerprint": expected_fingerprint,
            }
        )
        return _receipt_from_response(response)

    async def revert(
        self,
        *,
        project_id: str,
        expected_head: str,
        receipt: ApplyReceipt,
    ) -> ApplyReceipt:
        response = await self._request(
            {
                "action": "revert",
                "project_id": project_id,
                "expected_head": expected_head,
                "apply_receipt": _receipt_to_payload(receipt),
            }
        )
        return _receipt_from_response(response)

    async def commit(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt:
        response = await self._request(
            {
                "action": "commit",
                "project_id": project_id,
                "expected_head": expected_head,
                "operation_id": operation_id,
                "apply_receipt": _receipt_to_payload(apply_receipt),
                "message": message,
            }
        )
        return _commit_receipt_from_response(response)

    async def undo(
        self,
        *,
        project_id: str,
        expected_head: str,
        apply_receipt: ApplyReceipt,
        commit_receipt: CommitReceipt,
    ) -> CommitReceipt:
        response = await self._request(
            {
                "action": "undo",
                "project_id": project_id,
                "expected_head": expected_head,
                "apply_receipt": _receipt_to_payload(apply_receipt),
                "commit_receipt": _commit_receipt_to_payload(commit_receipt),
            }
        )
        return _commit_receipt_from_response(response)

    async def reconcile_apply(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> tuple[str, ApplyReceipt | None]:
        response = await self._request(
            {
                "action": "reconcile_apply",
                "project_id": project_id,
                "expected_head": expected_head,
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
            raise ProjectWriterClientError("Writer response is invalid.", code="invalid_response")
        if payload is None:
            if state == "applied":
                raise ProjectWriterClientError("Writer receipt is missing.", code="invalid_response")
            return state, None
        if state != "applied":
            raise ProjectWriterClientError("Writer response is inconsistent.", code="invalid_response")
        return state, _receipt_from_response({"receipt": payload})

    async def reconcile_commit(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
        apply_receipt: ApplyReceipt,
        commit_operation_id: str,
        message: str,
    ) -> tuple[str, ApplyReceipt, CommitReceipt | None]:
        response = await self._request(
            {
                "action": "reconcile_commit",
                "project_id": project_id,
                "expected_head": expected_head,
                "operation_id": operation_id,
                "revision": revision,
                "patch": patch,
                "paths": paths,
                "expected_fingerprint": expected_fingerprint,
                "apply_receipt": _receipt_to_payload(apply_receipt),
                "commit_operation_id": commit_operation_id,
                "message": message,
            }
        )
        state = response.get("state")
        if state not in {"not_committed", "committed", "undone"}:
            raise ProjectWriterClientError("Writer recovery state is invalid.", code="invalid_response")
        apply_payload = response.get("apply_receipt")
        if not isinstance(apply_payload, dict):
            raise ProjectWriterClientError("Writer apply receipt is missing.", code="invalid_response")
        restored_apply = _receipt_from_response({"receipt": apply_payload})
        commit_payload = response.get("commit_receipt")
        if commit_payload is None:
            if state in {"committed", "undone"}:
                raise ProjectWriterClientError("Writer commit receipt is missing.", code="invalid_response")
            return state, restored_apply, None
        if state not in {"committed", "undone"} or not isinstance(commit_payload, dict):
            raise ProjectWriterClientError("Writer commit response is inconsistent.", code="invalid_response")
        return state, restored_apply, _commit_receipt_from_response(
            {"receipt": commit_payload}
        )

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(encoded) > MAX_PROJECT_WRITER_FRAME_BYTES:
            raise ProjectWriterClientError("Writer request is too large.", code="request_too_large")
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
                timeout=self._timeout if timeout is None else timeout,
            )
        except TimeoutError as exc:
            raise ProjectWriterClientError("Writer request timed out.", code="project_writer_timeout") from exc
        except (ConnectionError, OSError) as exc:
            raise ProjectWriterClientError("Writer is unavailable.", code="project_writer_unavailable") from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if not raw or len(raw) > MAX_PROJECT_WRITER_FRAME_BYTES:
            raise ProjectWriterClientError("Writer response is invalid.", code="invalid_response")
        try:
            response = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectWriterClientError("Writer response is invalid.", code="invalid_response") from exc
        if not isinstance(response, dict):
            raise ProjectWriterClientError("Writer response is invalid.", code="invalid_response")
        if response.get("ok") is not True:
            raw_code = response.get("code")
            code = (
                raw_code
                if isinstance(raw_code, str) and SAFE_ERROR_CODE.fullmatch(raw_code)
                else "project_writer_error"
            )
            raise ProjectWriterClientError("Writer request failed.", code=code)
        return response
