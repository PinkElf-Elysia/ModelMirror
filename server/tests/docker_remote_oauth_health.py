"""One-shot health probe used by the isolated OAuth sidecar Docker gate."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path

from sandbox_sidecar import oauth_server


async def main() -> None:
    status = {}
    for line in Path("/proc/self/status").read_text("utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    assert status.get("CapEff") == "0000000000000000"
    assert status.get("NoNewPrivs") == "1"
    assert {name for _, name in socket.if_nameindex()} == {"lo"}
    try:
        Path("/opt/modelmirror/.write-probe").write_text("denied", "utf-8")
    except OSError:
        root_read_only = True
    else:
        root_read_only = False
        Path("/opt/modelmirror/.write-probe").unlink(missing_ok=True)
    assert root_read_only is True
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
    assert response["authorization_enabled"] is True
    assert response["token_storage_enabled"] is False
    assert os.getuid() == 65532
    print(
        json.dumps(
            {
                "ok": True,
                "uid": os.getuid(),
                "protocol": response["protocol"],
                "authorization_enabled": True,
                "cap_eff": status["CapEff"],
                "network_interfaces": [name for _, name in socket.if_nameindex()],
                "no_new_privs": int(status["NoNewPrivs"]),
                "token_storage_enabled": False,
                "root_read_only": root_read_only,
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
