from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
from pathlib import Path

import pytest
import yaml

import server.coding_verifier.server as verifier_server
from server.coding_runtime.verification import VerificationStepId
from server.coding_runtime.commands import normalize_agent_command
from server.coding_runtime.verifier_client import (
    CodingVerifierClient,
    VerifierClientError,
    source_snapshot_fingerprint,
)
from server.coding_verifier.engine import (
    CodingVerifierEngine,
    CommandResult,
    IsolatedProjectCommandExecutor,
    VerificationEngineError,
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
    *,
    project_snapshot_path: Path | None = None,
    command_executor: IsolatedProjectCommandExecutor | None = None,
):
    socket_path = tmp_path / "verifier.sock"
    engine = CodingVerifierEngine(
        source_root,
        tmp_path / "workspace",
        runner=runner,
    )
    server = CodingVerifierServer(
        socket_path,
        engine=engine,
        project_snapshot_path=project_snapshot_path or tmp_path / "unused-project",
        command_executor=command_executor,
    )
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

    projects_overlay = yaml.safe_load(
        (root / "docker-compose.coding-projects.yml").read_text(encoding="utf-8")
    )
    project_mounts = projects_overlay["services"]["coding-verifier"]["volumes"]
    assert project_mounts == [
        {
            "type": "volume",
            "source": "coding_project_snapshot",
            "target": "/project-snapshots",
            "read_only": True,
        }
    ]


def test_verifier_image_uses_preinstalled_locked_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (
        root / "server/coding_verifier/Dockerfile"
    ).read_text(encoding="utf-8")

    assert "npm ci" in dockerfile
    assert "server/requirements.txt" in dockerfile
    assert "pip install --no-cache-dir --requirement" in dockerfile
    assert (
        "COPY --from=frontend_dependencies /opt/client/node_modules "
        "/opt/modelmirror-client/node_modules"
    ) in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "server.coding_verifier.server"]' in dockerfile
    assert "curl " not in dockerfile
    assert "wget " not in dockerfile


def _agent_command(*, script: str, timeout_seconds: int = 30):
    return normalize_agent_command(
        argv=["python", "-c", script],
        cwd=".",
        purpose="运行随机隔离检查",
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_dynamic_command_discards_writes_and_uses_secret_free_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "selected-source"
    source.mkdir()
    (source / "marker.txt").write_text("SOURCE-k7m4\n", encoding="utf-8")
    fingerprint = snapshot_fingerprint(source)
    workspace = tmp_path / "command-workspace"
    executor = IsolatedProjectCommandExecutor(workspace)
    monkeypatch.setenv("CODING_AGENT_GATEWAY_KEY", "sk-" + "z" * 30)
    script = (
        "import os,pathlib;"
        "pathlib.Path('generated-r8v3.txt').write_text('temporary');"
        "print('\\x1b[31m'+os.getenv('CODING_AGENT_GATEWAY_KEY','missing'));"
        "print(pathlib.Path.cwd())"
    )

    result = await executor.execute(
        source_root=source,
        expected_fingerprint=fingerprint,
        patch="",
        paths=[],
        command=_agent_command(script=script),
    )

    assert result.status == "passed"
    assert "missing" in result.output
    assert "CODING_AGENT_GATEWAY_KEY" not in result.output
    assert "sk-" not in result.output
    assert "\x1b" not in result.output
    assert "/workspace" not in result.output
    assert not (source / "generated-r8v3.txt").exists()
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_dynamic_command_timeout_kills_execution_and_cleans_workspace(
    tmp_path: Path,
) -> None:
    source = tmp_path / "timeout-source"
    source.mkdir()
    (source / "marker.txt").write_text("TIMEOUT-v5n2\n", encoding="utf-8")
    workspace = tmp_path / "timeout-workspace"
    executor = IsolatedProjectCommandExecutor(workspace)

    with pytest.raises(VerificationEngineError) as timeout:
        await executor.execute(
            source_root=source,
            expected_fingerprint=snapshot_fingerprint(source),
            patch="",
            paths=[],
            command=_agent_command(script="import time;time.sleep(30)"),
            max_duration_seconds=0.02,
        )

    assert timeout.value.code == "command_timeout"
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_dynamic_command_cannot_execute_a_project_relative_binary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "binary-source"
    (source / "tools").mkdir(parents=True)
    (source / "tools" / "python").write_text("not executable\n", encoding="utf-8")
    executor = IsolatedProjectCommandExecutor(tmp_path / "binary-workspace")
    command = normalize_agent_command(
        argv=["tools/python", "-V"],
        cwd=".",
        purpose="尝试项目内程序",
        timeout_seconds=10,
    )

    with pytest.raises(VerificationEngineError) as denied:
        await executor.execute(
            source_root=source,
            expected_fingerprint=snapshot_fingerprint(source),
            patch="",
            paths=[],
            command=command,
        )

    assert denied.value.code == "command_executable_denied"
    assert not (tmp_path / "binary-workspace").exists()


def _write_runner_pack(
    packs: Path,
    source: Path,
    *,
    pack_id: str = "random-pack-k4m7",
) -> Path:
    pack = packs / pack_id
    (pack / "python" / "site-packages").mkdir(parents=True)
    (pack / "node" / "node_modules").mkdir(parents=True)
    (pack / "bin").mkdir()
    requirements = (source / "requirements.txt").read_bytes()
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "version": 1,
                "id": pack_id,
                "platform": "linux-x86_64",
                "python_version": "3.12",
                "node_version": "22",
                "inputs": {
                    "requirements.txt": "sha256:"
                    + hashlib.sha256(requirements).hexdigest()
                },
                "python_paths": ["python/site-packages"],
                "node_modules": {".": "node/node_modules"},
                "bin_paths": ["bin"],
            }
        ),
        encoding="utf-8",
    )
    return pack


def test_runner_pack_is_bound_to_dependencies_and_rejects_escaping_links(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pack-source"
    source.mkdir()
    (source / "requirements.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
    packs = tmp_path / "packs"
    pack = _write_runner_pack(packs, source)
    executor = IsolatedProjectCommandExecutor(
        tmp_path / "pack-workspace",
        runner_packs_root=packs,
    )

    manifest, resolved = executor._load_runner_pack("random-pack-k4m7", source)
    assert manifest.pack_id == "random-pack-k4m7"
    assert resolved == pack.resolve()

    (source / "requirements.txt").write_text("pytest==0\n", encoding="utf-8")
    with pytest.raises(VerificationEngineError) as mismatch:
        executor._load_runner_pack("random-pack-k4m7", source)
    assert mismatch.value.code == "runner_pack_mismatch"

    (source / "requirements.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
    outside = tmp_path / "outside-secret"
    outside.write_text("must-not-read", encoding="utf-8")
    (pack / "python" / "site-packages" / "escape").symlink_to(outside)
    with pytest.raises(VerificationEngineError) as unsafe:
        executor._load_runner_pack("random-pack-k4m7", source)
    assert unsafe.value.code == "runner_pack_unsafe"


@pytest.mark.asyncio
async def test_runner_pack_rechecks_dependency_hash_after_applying_draft(
    tmp_path: Path,
) -> None:
    source = tmp_path / "changed-pack-source"
    source.mkdir()
    old = "pytest==8.4.1\n"
    new = "pytest==0\n"
    (source / "requirements.txt").write_text(old, encoding="utf-8")
    packs = tmp_path / "changed-packs"
    _write_runner_pack(packs, source, pack_id="changed-pack-n2w5")
    executor = IsolatedProjectCommandExecutor(
        tmp_path / "changed-pack-workspace",
        runner_packs_root=packs,
    )

    with pytest.raises(VerificationEngineError) as mismatch:
        await executor.execute(
            source_root=source,
            expected_fingerprint=snapshot_fingerprint(source),
            patch=_patch("requirements.txt", old, new),
            paths=["requirements.txt"],
            command=_agent_command(script="print('must not run')"),
            runner_pack_id="changed-pack-n2w5",
        )

    assert mismatch.value.code == "runner_pack_mismatch"
    assert not (tmp_path / "changed-pack-workspace").exists()


@pytest.mark.asyncio
async def test_command_socket_rechecks_exact_project_lease_and_patch(
    tmp_path: Path,
    source_root: Path,
) -> None:
    slot = tmp_path / "project-slot" / "current"
    project = slot / "workspace"
    project.mkdir(parents=True)
    (project / "marker.txt").write_text("LEASE-q9t2\n", encoding="utf-8")
    fingerprint = snapshot_fingerprint(project)
    source = {
        "kind": "local_clone",
        "lease_id": "lease-q9t2-123456789012",
        "project_id": "local-q9t2-123456789012",
        "name": "随机 q9t2 项目",
        "branch": "main",
        "head": "a" * 40,
        "fingerprint": fingerprint,
        "file_count": 1,
        "total_bytes": len("LEASE-q9t2\n".encode()),
        "hidden_files": 0,
        "created_at": 1_785_600_000.0,
    }
    (slot / "lease.json").write_text(
        json.dumps({key: value for key, value in source.items() if key != "kind"}),
        encoding="utf-8",
    )
    command_executor = IsolatedProjectCommandExecutor(tmp_path / "socket-command-workspace")
    async with _running_server(
        tmp_path,
        source_root,
        _Runner(),
        project_snapshot_path=slot,
        command_executor=command_executor,
    ) as (client, _, _):
        command = _agent_command(script="print(open('marker.txt').read().strip())")
        result = await client.execute_command(
            session_id="session-q9t2",
            request_id="request-q9t2",
            source=source,
            patch="",
            paths=[],
            command=command.to_internal_dict(),
            runner_pack_id=None,
            max_duration_seconds=30,
        )
        assert result["status"] == "passed"
        assert result["output"] == "LEASE-q9t2"

        wrong = dict(source)
        wrong["project_id"] = "local-other-123456789012"
        with pytest.raises(VerifierClientError) as rejected:
            await client.execute_command(
                session_id="session-q9t2",
                request_id="request-other-q9t2",
                source=wrong,
                patch="",
                paths=[],
                command=command.to_internal_dict(),
                runner_pack_id=None,
                max_duration_seconds=30,
            )
        assert rejected.value.code == "snapshot_mismatch"
