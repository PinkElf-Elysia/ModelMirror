from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .protocol import MAX_REQUEST_BYTES, ProtocolError, parse_request, response
from .runner import WorkerFailure, WorkerRunManager


SOCKET_PATH = Path(os.environ.get("AI_RESEARCH_WORKER_SOCKET", "/run/ai-research/worker.sock"))
LOG_ROOT = Path(os.environ.get("AI_RESEARCH_LOG_ROOT", "/data/inspect-logs"))
FIXTURE_FILE = Path(__file__).resolve().parents[1] / "fixtures" / "offline_tasks.py"


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    manager: WorkerRunManager,
) -> None:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            raise ProtocolError("request exceeds the bounded newline JSON protocol")
        try:
            payload: Any = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("request is not valid UTF-8 JSON") from exc
        request = parse_request(payload)
        if request.action == "health":
            result = response(ok=True, result=await manager.health())
        elif request.action == "start":
            result = response(
                ok=True,
                result=await manager.start(request.run_id or "", request.case_id or ""),
            )
        elif request.action == "status":
            result = response(ok=True, result=await manager.status(request.run_id or ""))
        else:
            result = response(ok=True, result=await manager.cancel(request.run_id or ""))
    except (ProtocolError, WorkerFailure) as exc:
        result = response(ok=False, error={"code": type(exc).__name__, "message": str(exc)})
    except Exception:
        result = response(
            ok=False,
            error={"code": "InternalWorkerError", "message": "worker request failed"},
        )
    try:
        writer.write(
            (
                json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def serve() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        SOCKET_PATH.unlink()
    manager = WorkerRunManager(LOG_ROOT, FIXTURE_FILE)
    server = await asyncio.start_unix_server(
        lambda reader, writer: handle_client(reader, writer, manager),
        path=str(SOCKET_PATH),
        limit=MAX_REQUEST_BYTES,
    )
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
