from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1
MAX_RESPONSE_BYTES = 1_048_576


class WorkerClientError(RuntimeError):
    pass


class WorkerBusy(WorkerClientError):
    pass


class WorkerClient:
    def __init__(self, socket_path: Path, timeout: float = 10.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request("health")

    async def start(self, run_id: str, case_id: str) -> dict[str, Any]:
        return await self._request("start", runId=run_id, caseId=case_id)

    async def status(self, run_id: str) -> dict[str, Any]:
        return await self._request("status", runId=run_id)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return await self._request("cancel", timeout=max(self.timeout, 70.0), runId=run_id)

    async def _request(
        self, action: str, *, timeout: float | None = None, **fields: Any
    ) -> dict[str, Any]:
        request_timeout = self.timeout if timeout is None else timeout
        try:
            socket_stat = self.socket_path.lstat()
        except FileNotFoundError as exc:
            raise WorkerClientError("worker socket is unavailable") from exc
        if self.socket_path.is_symlink() or not stat.S_ISSOCK(socket_stat.st_mode):
            raise WorkerClientError("worker socket path is not a Unix socket")
        payload = {"protocolVersion": PROTOCOL_VERSION, "action": action, **fields}
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)), timeout=request_timeout
            )
            writer.write(encoded)
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=request_timeout)
            writer.close()
            await writer.wait_closed()
        except (OSError, TimeoutError) as exc:
            raise WorkerClientError("worker request failed") from exc
        if not raw or len(raw) > MAX_RESPONSE_BYTES or not raw.endswith(b"\n"):
            raise WorkerClientError("worker returned an invalid bounded response")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkerClientError("worker response was not valid JSON") from exc
        if not isinstance(response, dict) or response.get("protocolVersion") != PROTOCOL_VERSION:
            raise WorkerClientError("worker protocol version mismatch")
        if response.get("ok") is not True:
            error = response.get("error") or {}
            message = str(error.get("message") or "worker rejected request")
            if message == "worker is busy":
                raise WorkerBusy(message)
            raise WorkerClientError(message[:1000])
        result = response.get("result")
        if not isinstance(result, dict):
            raise WorkerClientError("worker response did not contain an object result")
        return result
