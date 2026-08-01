from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from server.coding_applier.server import CodingApplierServer
from server.coding_runtime.applier_client import (
    ApplierClientError,
    CodingApplierClient,
)
from server.coding_runtime.apply_models import (
    ApplyFileReceipt,
    ApplyReceipt,
)


RECEIPT = ApplyReceipt(
    apply_id="apply_operation_1234567890",
    revision=2,
    snapshot_fingerprint="a" * 64,
    files=(
        ApplyFileReceipt(
            path="server/app.py",
            existed_before=True,
            before_sha256="b" * 64,
            after_sha256="c" * 64,
        ),
    ),
    applied_at=12.5,
)


class FakeEngine:
    def __init__(self) -> None:
        self.apply_calls: list[dict[str, object]] = []
        self.revert_calls: list[ApplyReceipt] = []

    def health(self) -> dict[str, object]:
        return {
            "configured": True,
            "available": True,
            "target": "dedicated_worktree",
            "snapshot_fingerprint": "a" * 64,
        }

    def apply(self, **kwargs: object) -> ApplyReceipt:
        self.apply_calls.append(kwargs)
        return RECEIPT

    def revert(self, receipt: ApplyReceipt) -> ApplyReceipt:
        self.revert_calls.append(receipt)
        return receipt


async def _wait_for_socket(path: Path) -> None:
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Applier socket was not created")


@pytest.mark.asyncio
async def test_socket_round_trip_exposes_only_fixed_actions(tmp_path: Path) -> None:
    socket_path = tmp_path / "applier.sock"
    engine = FakeEngine()
    server = CodingApplierServer(socket_path, engine=engine)
    task = asyncio.create_task(server.serve_forever())
    await _wait_for_socket(socket_path)
    client = CodingApplierClient(socket_path)
    try:
        health = await client.health()
        receipt = await client.apply(
            operation_id=RECEIPT.apply_id,
            revision=2,
            patch="safe patch",
            paths=["server/app.py"],
            expected_fingerprint="a" * 64,
        )
        reverted = await client.revert(receipt)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert health["available"] is True
    assert "target_root" not in health
    assert receipt == RECEIPT
    assert reverted == RECEIPT
    assert engine.apply_calls == [
        {
            "operation_id": RECEIPT.apply_id,
            "revision": 2,
            "patch": "safe patch",
            "paths": ["server/app.py"],
            "expected_fingerprint": "a" * 64,
        }
    ]
    assert engine.revert_calls == [RECEIPT]
    assert socket_path.exists() is False


@pytest.mark.asyncio
async def test_protocol_rejects_extra_fields_and_unsupported_actions(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "applier.sock"
    server = CodingApplierServer(socket_path, engine=FakeEngine())
    task = asyncio.create_task(server.serve_forever())
    await _wait_for_socket(socket_path)

    async def request(raw: bytes) -> bytes:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(raw)
        await writer.drain()
        response = await reader.readline()
        writer.close()
        await writer.wait_closed()
        return response

    try:
        extra = await request(b'{"action":"health","path":"/target"}\n')
        unsupported = await request(b'{"action":"shell"}\n')
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert b'"code":"invalid_request"' in extra
    assert b'"code":"unsupported_action"' in unsupported
    assert b"/target" not in extra


@pytest.mark.asyncio
async def test_client_fails_closed_when_socket_is_missing(tmp_path: Path) -> None:
    client = CodingApplierClient(tmp_path / "missing.sock")

    with pytest.raises(ApplierClientError) as raised:
        await client.health()

    assert raised.value.code == "applier_unavailable"


def test_compose_overlay_and_image_preserve_isolation() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.coding-apply.yml").read_text(
        encoding="utf-8"
    )
    dockerfile = (root / "server/coding_applier/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "network_mode: none" in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert compose.count("create_host_path: false") == 2
    assert "target: /target/.git" in compose
    assert "read_only: true\n        bind:" in compose
    assert "coding_apply_socket:/run/modelmirror-coding-apply" in compose
    assert "CODING_AGENT_GATEWAY_KEY" not in compose
    assert "ports:" not in compose
    assert "USER 65532:65532" in dockerfile
    assert "chmod -R a-w /opt/modelmirror-source" in dockerfile
    assert "CMD [\"python\", \"-m\", \"server.coding_applier.server\"]" in dockerfile
