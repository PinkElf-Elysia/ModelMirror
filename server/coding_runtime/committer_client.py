from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from .apply_models import ApplyFileReceipt, ApplyReceipt
from .commit_models import CommitReceipt


MAX_COMMITTER_FRAME_BYTES = 256 * 1024
SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CommitterClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "committer_unavailable") -> None:
        super().__init__(message)
        self.code = code


class CodingCommitterClient:
    def __init__(self, socket_path: Path, *, timeout: float = 35.0) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"}, timeout=3.0)

    async def commit(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt:
        response = await self._request(
            {
                "action": "commit",
                "operation_id": operation_id,
                "apply_receipt": _apply_receipt_to_payload(apply_receipt),
                "message": message,
            }
        )
        return _commit_receipt_from_response(response)

    async def undo(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
    ) -> CommitReceipt:
        response = await self._request(
            {
                "action": "undo",
                "commit_receipt": _commit_receipt_to_payload(receipt),
                "apply_receipt": _apply_receipt_to_payload(apply_receipt),
            }
        )
        return _commit_receipt_from_response(response)

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
        if len(encoded) > MAX_COMMITTER_FRAME_BYTES:
            raise CommitterClientError("Commit request is too large.", code="request_too_large")
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
            raise CommitterClientError(
                "Commit request timed out.",
                code="committer_timeout",
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise CommitterClientError(
                "Commit service is unavailable.",
                code="committer_unavailable",
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if not raw or len(raw) > MAX_COMMITTER_FRAME_BYTES:
            raise CommitterClientError("Commit response is invalid.", code="invalid_response")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommitterClientError("Commit response is invalid.", code="invalid_response") from exc
        if not isinstance(response, dict):
            raise CommitterClientError("Commit response is invalid.", code="invalid_response")
        if response.get("ok") is not True:
            raw_code = response.get("code")
            code = (
                raw_code
                if isinstance(raw_code, str) and SAFE_ERROR_CODE.fullmatch(raw_code)
                else "committer_error"
            )
            raise CommitterClientError("Commit request failed.", code=code)
        return response


def _apply_receipt_to_payload(receipt: ApplyReceipt) -> dict[str, Any]:
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


def _commit_receipt_to_payload(receipt: CommitReceipt) -> dict[str, Any]:
    return {
        "commit_id": receipt.commit_id,
        "revision": receipt.revision,
        "apply_id": receipt.apply_id,
        "commit_sha": receipt.commit_sha,
        "parent_sha": receipt.parent_sha,
        "tree_sha": receipt.tree_sha,
        "message": receipt.message,
        "files": list(receipt.files),
        "branch": receipt.branch,
        "committed_at": receipt.committed_at,
    }


def _commit_receipt_from_response(response: dict[str, Any]) -> CommitReceipt:
    payload = response.get("receipt")
    if not isinstance(payload, dict) or set(payload) != {
        "commit_id",
        "revision",
        "apply_id",
        "commit_sha",
        "parent_sha",
        "tree_sha",
        "message",
        "files",
        "branch",
        "committed_at",
    }:
        raise CommitterClientError("Commit receipt is invalid.", code="invalid_response")
    files = payload["files"]
    if (
        not isinstance(payload["commit_id"], str)
        or isinstance(payload["revision"], bool)
        or not isinstance(payload["revision"], int)
        or not isinstance(payload["apply_id"], str)
        or not isinstance(payload["commit_sha"], str)
        or not isinstance(payload["parent_sha"], str)
        or not isinstance(payload["tree_sha"], str)
        or not isinstance(payload["message"], str)
        or not isinstance(files, list)
        or any(not isinstance(path, str) for path in files)
        or not isinstance(payload["branch"], str)
        or isinstance(payload["committed_at"], bool)
        or not isinstance(payload["committed_at"], (int, float))
    ):
        raise CommitterClientError("Commit receipt is invalid.", code="invalid_response")
    try:
        return CommitReceipt(**{**payload, "files": tuple(files)})
    except (TypeError, ValueError) as exc:
        raise CommitterClientError("Commit receipt is invalid.", code="invalid_response") from exc
