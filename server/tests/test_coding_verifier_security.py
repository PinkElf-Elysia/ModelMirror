from __future__ import annotations

import asyncio
import contextlib
import difflib
from pathlib import Path

import pytest
import yaml

import server.coding_verifier.server as verifier_server
from server.coding_runtime.verification import VerificationStepId
from server.coding_runtime.verifier_client import (
    CodingVerifierClient,
    VerifierClientError,
    source_snapshot_fingerprint,
)
from server.coding_verifier.engine import (
    CodingVerifierEngine,
    CommandResult,
    snapshot_fingerprint,
)
from server.coding_verifier.server import CodingVerifierServer


class _Runner:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.started = asyncio.Event()

    async def run(
        self,
        step_id: VerificationStepId,
        workspace: Path,
    ) -> CommandResult:
        self.started.set()
        if self.block:
            await asyncio.Future()
        return CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=10,
        )


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "server").mkdir(parents=True)
    (source / "server/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return source


def _patch(path: str, old: str, new: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return f"diff --git a/{path} b/{path}\n{diff}"


@contextlib.asynccontextmanager
async def _running_server(
    tmp_path: Path,
    source_root: Path,
    runner: _Runner,
):
    socket_path = tmp_path / "verifier.sock"
    engine = CodingVerifierEngine(
        source_root,
        tmp_path / "workspace",
        runner=runner,
    )
    server = CodingVerifierServer(socket_path, engine=engine)
    task = asyncio.create_task(server.serve_forever())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    assert socket_path.exists()
    try:
        yield CodingVerifierClient(socket_path), runner, engine
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_socket_service_runs_fixed_verification(
    tmp_path: Path,
    source_root: Path,
) -> None:
    async with _running_server(tmp_path, source_root, _Runner()) as (
        client,
        _,
        engine,
    ):
        health = await client.health()
        assert health["configured"] is True
        assert health["snapshot_fingerprint"] == engine.source_fingerprint

        started = await client.start(
            session_id="session-1",
            revision=1,
            patch=_patch(
                "server/app.py",
                "VALUE = 1\n",
                "VALUE = 2\n",
            ),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )
        assert started["verification"]["revision"] == 1
        assert started["verification"]["state"] == "running"
        for _ in range(100):
            status = await client.status(session_id="session-1", revision=1)
            if status["verification"]["state"] == "completed":
                break
            await asyncio.sleep(0.01)

        assert status["verification"]["result"] == "passed"
        assert source_root.joinpath("server/app.py").read_text() == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_socket_service_cancels_idempotently(
    tmp_path: Path,
    source_root: Path,
) -> None:
    runner = _Runner(block=True)
    async with _running_server(tmp_path, source_root, runner) as (
        client,
        _,
        engine,
    ):
        await client.start(
            session_id="session-2",
            revision=2,
            patch=_patch(
                "server/app.py",
                "VALUE = 1\n",
                "VALUE = 2\n",
            ),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )
        await asyncio.wait_for(runner.started.wait(), timeout=2)

        first = await client.cancel(session_id="session-2", revision=2)
        second = await client.cancel(session_id="session-2", revision=2)

        assert first["accepted"] is True
        assert second["accepted"] is True
        assert second["verification"]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_socket_service_rejects_path_mismatch(
    tmp_path: Path,
    source_root: Path,
) -> None:
    async with _running_server(tmp_path, source_root, _Runner()) as (
        client,
        _,
        engine,
    ):
        with pytest.raises(VerifierClientError) as raised:
            await client.start(
                session_id="session-3",
                revision=3,
                patch=_patch(
                    "server/app.py",
                    "VALUE = 1\n",
                    "VALUE = 2\n",
                ),
                paths=["../outside.py"],
                expected_fingerprint=engine.source_fingerprint,
            )

        assert raised.value.code == "invalid_patch"


@pytest.mark.asyncio
async def test_socket_service_enforces_total_timeout(
    tmp_path: Path,
    source_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verifier_server,
        "MAX_VERIFICATION_DURATION_SECONDS",
        0.01,
    )
    async with _running_server(tmp_path, source_root, _Runner(block=True)) as (
        client,
        _,
        engine,
    ):
        await client.start(
            session_id="session-4",
            revision=4,
            patch=_patch(
                "server/app.py",
                "VALUE = 1\n",
                "VALUE = 2\n",
            ),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )
        for _ in range(100):
            status = await client.status(session_id="session-4", revision=4)
            if status["verification"]["state"] == "completed":
                break
            await asyncio.sleep(0.01)

        assert status["verification"]["result"] == "failed"
        assert status["verification"]["reason"] == "verification_timeout"


def test_worker_and_verifier_fingerprints_match(source_root: Path) -> None:
    assert source_snapshot_fingerprint(source_root) == snapshot_fingerprint(
        source_root
    )


def test_compose_verifier_is_offline_and_unprivileged() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    service = compose["services"]["coding-verifier"]

    assert service["profiles"] == ["coding-verify"]
    assert service["network_mode"] == "none"
    assert service["user"] == "65532:65532"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["pids_limit"] == 256
    assert service["mem_limit"] == "3g"
    assert service["cpus"] == 2.0
    assert "ports" not in service
    assert all("docker.sock" not in item for item in service["volumes"])
    workspace = next(
        item for item in service["tmpfs"] if item.startswith("/workspace:")
    )
    assert "nosuid" in workspace
    assert "noexec" in workspace
    assert "size=1g" in workspace
    assert not any(
        "KEY" in name or "TOKEN" in name or "SECRET" in name
        for name in service["environment"]
    )


def test_verifier_image_uses_preinstalled_locked_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (
        root / "server/coding_verifier/Dockerfile"
    ).read_text(encoding="utf-8")

    assert "npm ci" in dockerfile
    assert "server/requirements.txt" in dockerfile
    assert "pip install --no-cache-dir --requirement" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "server.coding_verifier.server"]' in dockerfile
    assert "curl " not in dockerfile
    assert "wget " not in dockerfile
