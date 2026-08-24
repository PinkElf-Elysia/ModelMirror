"""One-shot health probe used by the isolated OAuth sidecar Docker gate."""

from __future__ import annotations

import asyncio
import json
import os

from sandbox_sidecar import oauth_server


async def main() -> None:
    oauth_server._prepare_socket(oauth_server.OAUTH_SOCKET)
    service = oauth_server.OAuthMetadataService()
    server = await asyncio.start_unix_server(
        service.handle, path=str(oauth_server.OAUTH_SOCKET)
    )
    os.chmod(oauth_server.OAUTH_SOCKET, 0o660)
    reader, writer = await asyncio.open_unix_connection(
        str(oauth_server.OAUTH_SOCKET)
    )
    writer.write(b'{"action":"health"}\n')
    await writer.drain()
    response = json.loads((await reader.readline()).decode("utf-8"))
    assert response["ok"] is True
    assert response["authorization_enabled"] is False
    assert response["token_storage_enabled"] is False
    assert os.getuid() == 65532
    print(
        json.dumps(
            {
                "ok": True,
                "uid": os.getuid(),
                "protocol": response["protocol"],
                "authorization_enabled": False,
                "token_storage_enabled": False,
            },
            sort_keys=True,
        )
    )
    writer.close()
    await writer.wait_closed()
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
