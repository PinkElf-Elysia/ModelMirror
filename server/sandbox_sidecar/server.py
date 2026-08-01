from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .engine import MAX_REQUEST_BYTES, SandboxEngine, SandboxEngineError


SOCKET_PATH = Path(os.getenv("SANDBOX_SOCKET_PATH", "/run/modelmirror-sandbox/sandbox.sock"))
WORKSPACE_ROOT = Path(os.getenv("SANDBOX_WORKSPACE_ROOT", "/workspaces"))


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    engine: SandboxEngine,
) -> None:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise SandboxEngineError("Sandbox request is empty or too large.", code="invalid_request")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise SandboxEngineError("Sandbox request must be an object.", code="invalid_request")
        response = await asyncio.to_thread(engine.dispatch, request)
    except SandboxEngineError as exc:
        response = {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:
        response = {"ok": False, "error": str(exc)[:1000], "code": "sandbox_internal_error"}
    writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    engine = SandboxEngine(WORKSPACE_ROOT, require_landlock=True)
    server = await asyncio.start_unix_server(
        lambda reader, writer: handle_client(reader, writer, engine),
        path=str(SOCKET_PATH),
        limit=MAX_REQUEST_BYTES + 1,
    )
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
