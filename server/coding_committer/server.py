from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Protocol

from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CodingCommitError, CommitReceipt

from .engine import (
    DEFAULT_AUTHOR_EMAIL,
    DEFAULT_AUTHOR_NAME,
    CodingCommitterEngine,
)


MAX_COMMITTER_FRAME_BYTES = 256 * 1024
SOCKET_PATH = Path(
    os.getenv(
        "CODING_COMMITTER_SOCKET_PATH",
        "/run/modelmirror-coding-commit/committer.sock",
    )
)
SOURCE_ROOT = Path("/opt/modelmirror-source")
TARGET_ROOT = Path("/target")
TEMPORARY_ROOT = Path("/temporary")


class CommitterEngine(Protocol):
    def health(self) -> dict[str, object]: ...

    def commit(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt: ...

    def undo(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
    ) -> CommitReceipt: ...


class CommitterProtocolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class CodingCommitterServer:
    """Narrow Unix socket control plane for one fixed local repository."""

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        engine: CommitterEngine | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._engine: CommitterEngine | None = engine
        self._startup_error: str | None = None
        if engine is None:
            try:
                self._engine = CodingCommitterEngine(
                    SOURCE_ROOT,
                    TARGET_ROOT,
                    TEMPORARY_ROOT,
                    author_name=os.getenv(
                        "CODING_COMMIT_AUTHOR_NAME",
                        DEFAULT_AUTHOR_NAME,
                    ),
                    author_email=os.getenv(
                        "CODING_COMMIT_AUTHOR_EMAIL",
                        DEFAULT_AUTHOR_EMAIL,
                    ),
                )
            except CodingCommitError as exc:
                self._startup_error = exc.code

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_COMMITTER_FRAME_BYTES:
                raise CommitterProtocolError("Committer request is empty or too large.")
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CommitterProtocolError("Committer request is invalid.") from exc
            if not isinstance(request, dict):
                raise CommitterProtocolError("Committer request must be an object.")
            response = await self._dispatch(request)
            await self._send(writer, {"ok": True, **response})
        except (CommitterProtocolError, CodingCommitError) as exc:
            await self._send_error(writer, exc.code)
        except Exception:
            await self._send_error(writer, "committer_internal_error")
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
                    "service": "coding-committer",
                    "configured": True,
                    "available": False,
                    "target": "isolated_local_repository",
                    "reason": self._startup_error or "committer_unavailable",
                }
            return {"service": "coding-committer", **self._engine.health()}
        if self._engine is None:
            raise CodingCommitError(
                "Coding Committer is unavailable.",
                code=self._startup_error or "committer_unavailable",
            )
        if action == "commit":
            _require_keys(
                request,
                {"action", "operation_id", "apply_receipt", "message"},
            )
            operation_id = request["operation_id"]
            message = request["message"]
            if not isinstance(operation_id, str) or not isinstance(message, str):
                raise CommitterProtocolError("Commit request is invalid.")
            receipt = await asyncio.to_thread(
                self._engine.commit,
                operation_id=operation_id,
                apply_receipt=_apply_receipt_from_payload(request["apply_receipt"]),
                message=message,
            )
            return {"receipt": _commit_receipt_to_payload(receipt)}
        if action == "undo":
            _require_keys(
                request,
                {"action", "commit_receipt", "apply_receipt"},
            )
            receipt = await asyncio.to_thread(
                self._engine.undo,
                _commit_receipt_from_payload(request["commit_receipt"]),
                _apply_receipt_from_payload(request["apply_receipt"]),
            )
            return {"receipt": _commit_receipt_to_payload(receipt)}
        raise CommitterProtocolError(
            "Committer action is not supported.",
            code="unsupported_action",
        )

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_COMMITTER_FRAME_BYTES + 1,
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
        if len(encoded) > MAX_COMMITTER_FRAME_BYTES:
            raise CommitterProtocolError(
                "Committer response is too large.",
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
                "error": "Coding commit request failed.",
            },
        )


def _require_keys(request: dict[str, Any], expected: set[str]) -> None:
    if set(request) != expected:
        raise CommitterProtocolError("Committer request fields are invalid.")


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


def _apply_receipt_from_payload(payload: Any) -> ApplyReceipt:
    if not isinstance(payload, dict) or set(payload) != {
        "apply_id",
        "revision",
        "snapshot_fingerprint",
        "applied_at",
        "files",
    }:
        raise CommitterProtocolError("Apply receipt is invalid.")
    files = payload["files"]
    if (
        not isinstance(payload["apply_id"], str)
        or isinstance(payload["revision"], bool)
        or not isinstance(payload["revision"], int)
        or not isinstance(payload["snapshot_fingerprint"], str)
        or isinstance(payload["applied_at"], bool)
        or not isinstance(payload["applied_at"], (int, float))
        or not isinstance(files, list)
        or not files
        or len(files) > 20
    ):
        raise CommitterProtocolError("Apply receipt files are invalid.")
    try:
        parsed = []
        for item in files:
            if not isinstance(item, dict) or set(item) != {
                "path",
                "existed_before",
                "before_sha256",
                "after_sha256",
            }:
                raise ValueError("Invalid file receipt")
            if (
                not isinstance(item["path"], str)
                or not isinstance(item["existed_before"], bool)
                or (
                    item["before_sha256"] is not None
                    and not isinstance(item["before_sha256"], str)
                )
                or not isinstance(item["after_sha256"], str)
            ):
                raise ValueError("Invalid file receipt values")
            parsed.append(ApplyFileReceipt(**item))
        return ApplyReceipt(
            apply_id=payload["apply_id"],
            revision=payload["revision"],
            snapshot_fingerprint=payload["snapshot_fingerprint"],
            applied_at=payload["applied_at"],
            files=tuple(parsed),
        )
    except (TypeError, ValueError) as exc:
        raise CommitterProtocolError("Apply receipt is invalid.") from exc


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


def _commit_receipt_from_payload(payload: Any) -> CommitReceipt:
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
        raise CommitterProtocolError("Commit receipt is invalid.")
    if (
        not isinstance(payload["commit_id"], str)
        or isinstance(payload["revision"], bool)
        or not isinstance(payload["revision"], int)
        or not isinstance(payload["apply_id"], str)
        or not isinstance(payload["commit_sha"], str)
        or not isinstance(payload["parent_sha"], str)
        or not isinstance(payload["tree_sha"], str)
        or not isinstance(payload["message"], str)
        or not isinstance(payload["files"], list)
        or any(not isinstance(path, str) for path in payload["files"])
        or not isinstance(payload["branch"], str)
        or isinstance(payload["committed_at"], bool)
        or not isinstance(payload["committed_at"], (int, float))
    ):
        raise CommitterProtocolError("Commit receipt files are invalid.")
    try:
        return CommitReceipt(**{**payload, "files": tuple(payload["files"])})
    except (TypeError, ValueError) as exc:
        raise CommitterProtocolError("Commit receipt is invalid.") from exc


def main() -> None:
    asyncio.run(CodingCommitterServer().serve_forever())


if __name__ == "__main__":
    main()
