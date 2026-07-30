from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


MAX_VERIFIER_FRAME_BYTES = 2 * 1024 * 1024


class VerifierClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "verifier_error") -> None:
        super().__init__(message)
        self.code = code


class CodingVerifierClient:
    """Small client for the private, fixed-operation verifier protocol."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._socket_path = str(socket_path)
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"})

    async def start(
        self,
        *,
        session_id: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
    ) -> dict[str, Any]:
        return await self._request(
            {
                "action": "start",
                "session_id": session_id,
                "revision": revision,
                "patch": patch,
                "paths": list(paths),
                "expected_fingerprint": expected_fingerprint,
            }
        )

    async def status(self, *, session_id: str, revision: int) -> dict[str, Any]:
        return await self._request(
            {
                "action": "status",
                "session_id": session_id,
                "revision": revision,
            }
        )

    async def cancel(self, *, session_id: str, revision: int) -> dict[str, Any]:
        return await self._request(
            {
                "action": "cancel",
                "session_id": session_id,
                "revision": revision,
            }
        )

    async def close(self, *, session_id: str) -> None:
        await self._request({"action": "close", "session_id": session_id})

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    self._socket_path,
                    limit=MAX_VERIFIER_FRAME_BYTES + 1,
                ),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as exc:
            raise VerifierClientError(
                "Project verification service is unavailable.",
                code="verifier_unavailable",
            ) from exc
        try:
            encoded = (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            if len(encoded) > MAX_VERIFIER_FRAME_BYTES:
                raise VerifierClientError(
                    "Project verification request is too large.",
                    code="invalid_request",
                )
            writer.write(encoded)
            await writer.drain()
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=self._timeout,
            )
            if not raw or len(raw) > MAX_VERIFIER_FRAME_BYTES:
                raise VerifierClientError(
                    "Project verification response is invalid.",
                    code="invalid_response",
                )
            try:
                response = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VerifierClientError(
                    "Project verification response is invalid.",
                    code="invalid_response",
                ) from exc
            if not isinstance(response, dict):
                raise VerifierClientError(
                    "Project verification response is invalid.",
                    code="invalid_response",
                )
            if response.get("ok") is not True:
                code = response.get("code")
                raise VerifierClientError(
                    "Project verification request failed.",
                    code=code if isinstance(code, str) else "verifier_error",
                )
            return response
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def source_snapshot_fingerprint(root: Path) -> str:
    """Match the verifier fingerprint without importing its execution engine."""

    resolved = root.resolve()
    if not resolved.is_dir() or resolved.parent == resolved:
        raise VerifierClientError(
            "Coding source snapshot is unavailable.",
            code="source_snapshot_unavailable",
        )
    digest = hashlib.sha256()
    for path in sorted(resolved.rglob("*")):
        relative = path.relative_to(resolved).as_posix()
        if path.is_symlink():
            raise VerifierClientError(
                "Coding source snapshot is unsafe.",
                code="source_snapshot_unsafe",
            )
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        content = path.read_bytes()
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()
