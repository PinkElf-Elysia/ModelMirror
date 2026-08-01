from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Protocol

from server.coding_runtime.apply_models import (
    ApplyFileReceipt,
    ApplyReceipt,
    CodingApplyError,
)

from .engine import CodingApplierEngine


MAX_APPLIER_FRAME_BYTES = 2 * 1024 * 1024
SOCKET_PATH = Path(
    os.getenv(
        "CODING_APPLIER_SOCKET_PATH",
        "/run/modelmirror-coding-apply/applier.sock",
    )
)
SOURCE_ROOT = Path("/opt/modelmirror-source")
TARGET_ROOT = Path("/target")
STAGING_ROOT = Path("/staging/current")


class ApplierEngine(Protocol):
    def health(self) -> dict[str, object]: ...

    def apply(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> ApplyReceipt: ...

    def revert(self, receipt: ApplyReceipt) -> ApplyReceipt: ...


class ApplierProtocolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class CodingApplierServer:
    """Narrow Unix socket control plane for one fixed application target."""

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        engine: ApplierEngine | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._engine: ApplierEngine | None = engine
        self._startup_error: str | None = None
        if engine is None:
            try:
                self._engine = CodingApplierEngine(
                    SOURCE_ROOT,
                    TARGET_ROOT,
                    STAGING_ROOT,
                )
            except CodingApplyError as exc:
                self._startup_error = exc.code

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_APPLIER_FRAME_BYTES:
                raise ApplierProtocolError("Applier request is empty or too large.")
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApplierProtocolError("Applier request is invalid.") from exc
            if not isinstance(request, dict):
                raise ApplierProtocolError("Applier request must be an object.")
            response = await self._dispatch(request)
            await self._send(writer, {"ok": True, **response})
        except (ApplierProtocolError, CodingApplyError) as exc:
            await self._send_error(writer, exc.code)
        except Exception:
            await self._send_error(writer, "applier_internal_error")
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
                    "service": "coding-applier",
                    "configured": False,
                    "available": False,
                    "target": "dedicated_worktree",
                    "reason": self._startup_error or "applier_unavailable",
                }
            return {
                "service": "coding-applier",
                **self._engine.health(),
            }
        if self._engine is None:
            raise CodingApplyError(
                "Coding Applier is unavailable.",
                code=self._startup_error or "applier_unavailable",
            )
        if action == "apply":
            _require_keys(
                request,
                {
                    "action",
                    "operation_id",
                    "revision",
                    "patch",
                    "paths",
                    "expected_fingerprint",
                },
            )
            operation_id = request["operation_id"]
            revision = request["revision"]
            patch = request["patch"]
            paths = request["paths"]
            expected_fingerprint = request["expected_fingerprint"]
            if (
                not isinstance(operation_id, str)
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or not isinstance(patch, str)
                or not isinstance(paths, list)
                or not paths
                or any(not isinstance(path, str) for path in paths)
                or not isinstance(expected_fingerprint, str)
            ):
                raise ApplierProtocolError("Applier apply request is invalid.")
            receipt = await asyncio.to_thread(
                self._engine.apply,
                operation_id=operation_id,
                revision=revision,
                patch=patch,
                paths=paths,
                expected_fingerprint=expected_fingerprint,
            )
            return {"receipt": _receipt_to_payload(receipt)}
        if action == "revert":
            _require_keys(request, {"action", "receipt"})
            receipt = _receipt_from_payload(request["receipt"])
            reverted = await asyncio.to_thread(self._engine.revert, receipt)
            return {"receipt": _receipt_to_payload(reverted)}
        raise ApplierProtocolError(
            "Applier action is not supported.",
            code="unsupported_action",
        )

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_APPLIER_FRAME_BYTES + 1,
        )
        os.chmod(self._socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            self._socket_path.unlink(missing_ok=True)

    @staticmethod
    async def _send(
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
    ) -> None:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_APPLIER_FRAME_BYTES:
            raise ApplierProtocolError(
                "Applier response is too large.",
                code="response_too_large",
            )
        writer.write(encoded)
        await writer.drain()

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        code: str,
    ) -> None:
        await self._send(
            writer,
            {
                "ok": False,
                "code": code,
                "error": "Coding application request failed.",
            },
        )


def _require_keys(request: dict[str, Any], expected: set[str]) -> None:
    if set(request) != expected:
        raise ApplierProtocolError("Applier request fields are invalid.")


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


def _receipt_from_payload(payload: Any) -> ApplyReceipt:
    if not isinstance(payload, dict) or set(payload) != {
        "apply_id",
        "revision",
        "snapshot_fingerprint",
        "applied_at",
        "files",
    }:
        raise ApplierProtocolError("Applier receipt is invalid.")
    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > 20:
        raise ApplierProtocolError("Applier receipt files are invalid.")
    files: list[ApplyFileReceipt] = []
    try:
        for raw in raw_files:
            if not isinstance(raw, dict) or set(raw) != {
                "path",
                "existed_before",
                "before_sha256",
                "after_sha256",
            }:
                raise ValueError("Invalid receipt file")
            files.append(
                ApplyFileReceipt(
                    path=raw["path"],
                    existed_before=raw["existed_before"],
                    before_sha256=raw["before_sha256"],
                    after_sha256=raw["after_sha256"],
                )
            )
        return ApplyReceipt(
            apply_id=payload["apply_id"],
            revision=payload["revision"],
            snapshot_fingerprint=payload["snapshot_fingerprint"],
            applied_at=payload["applied_at"],
            files=tuple(files),
        )
    except (TypeError, ValueError) as exc:
        raise ApplierProtocolError("Applier receipt is invalid.") from exc


def main() -> None:
    asyncio.run(CodingApplierServer().serve_forever())


if __name__ == "__main__":
    main()
