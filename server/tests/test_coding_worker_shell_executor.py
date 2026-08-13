from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from server.coding_worker.executor import (
    ExecutorRPCServer,
    ExecutorSidecarClientPool,
    SidecarExecutor,
)
from server.coding_worker.workspace import WorkspaceBroker


pytestmark = pytest.mark.skipif(
    os.name == "nt" or not Path("/bin/bash").is_file(),
    reason="the production shell executor requires its Linux Bash sidecar",
)


def _executor(tmp_path: Path) -> tuple[SidecarExecutor, Path, Path]:
    repository = tmp_path / "slot" / "workspaces" / "workspace_one" / "repo"
    repository.mkdir(parents=True)
    (repository / "app.py").write_text("print('old')\n", encoding="utf-8")
    (repository / "delete.txt").write_text("remove me\n", encoding="utf-8")
    runtime = tmp_path / "slot" / "runtime"
    return (
        SidecarExecutor(
            lambda workspace_id: (
                repository if workspace_id == "workspace_one" else tmp_path / "missing"
            ),
            runtime_root=runtime,
        ),
        repository,
        runtime,
    )


@pytest.mark.asyncio
async def test_shell_inspect_streams_output_and_discards_clone_changes(
    tmp_path: Path,
) -> None:
    executor, repository, runtime = _executor(tmp_path)
    repository.joinpath("nested").mkdir()
    repository.joinpath("nested", "value.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    output: list[tuple[str, bytes]] = []

    async def collect(stream: str, chunk: bytes) -> None:
        output.append((stream, chunk))

    result = await executor.run_shell(
        task_id="task_one",
        workspace_id="workspace_one",
        operation_id="inspect_one",
        script="printf 'stdout-data'; printf 'stderr-data' >&2; printf 'changed\\n' > app.py",
        cwd=".",
        mode="inspect",
        timeout_seconds=10,
        output_callback=collect,
    )

    assert result["exit_code"] == 0
    assert result["workspace_changed"] is True
    assert result["changeset_eligible"] is False
    assert result["changes"] == []
    assert result["base_tree_hash"] == WorkspaceBroker._tree_hash(repository)
    assert b"".join(chunk for stream, chunk in output if stream == "stdout") == b"stdout-data"
    assert b"".join(chunk for stream, chunk in output if stream == "stderr") == b"stderr-data"
    assert repository.joinpath("app.py").read_text(encoding="utf-8") == "print('old')\n"
    assert not runtime.joinpath("shell", "task_one", "inspect_one").exists()


@pytest.mark.asyncio
async def test_shell_mutate_returns_preimage_bound_batch_without_publishing(
    tmp_path: Path,
) -> None:
    executor, repository, _ = _executor(tmp_path)

    result = await executor.run_shell(
        task_id="task_one",
        workspace_id="workspace_one",
        operation_id="mutate_one",
        script=(
            "printf \"print('new')\\n\" > app.py; "
            "printf 'created\\n' > created.txt; rm delete.txt"
        ),
        cwd=".",
        mode="mutate",
        timeout_seconds=10,
    )

    assert result["exit_code"] == 0
    assert result["reason"] is None
    assert result["changeset_eligible"] is True
    changes = {str(change["path"]): change for change in result["changes"]}
    assert set(changes) == {"app.py", "created.txt", "delete.txt"}
    assert changes["app.py"]["kind"] == "write"
    assert changes["app.py"]["expected_sha256"]
    assert changes["created.txt"]["expected_absent"] is True
    assert changes["delete.txt"]["kind"] == "delete"
    assert repository.joinpath("app.py").read_text(encoding="utf-8") == "print('old')\n"
    assert not repository.joinpath("created.txt").exists()
    assert repository.joinpath("delete.txt").is_file()


@pytest.mark.asyncio
async def test_failed_shell_cannot_publish_and_receives_no_host_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, repository, _ = _executor(tmp_path)
    foreign = repository.parent.parent / "workspace_foreign" / "repo" / "secret.txt"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("cross-task-secret\n", encoding="utf-8")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "must-not-enter-the-sidecar")

    result = await executor.run_shell(
        task_id="task_one",
        workspace_id="workspace_one",
        operation_id="failed_one",
        script=(
            "test -z \"${LLM_GATEWAY_KEY+x}\"; "
            "case \"$HOME\" in */shell/task_one/failed_one/home) ;; *) exit 91 ;; esac; "
            f"test ! -r '{foreign.as_posix()}'; "
            "printf 'partial\\n' > app.py; exit 7"
        ),
        cwd=".",
        mode="mutate",
        timeout_seconds=10,
    )

    assert result["exit_code"] == 7
    assert result["changeset_eligible"] is False
    assert result["changes"] == []
    assert repository.joinpath("app.py").read_text(encoding="utf-8") == "print('old')\n"


@pytest.mark.asyncio
async def test_shell_rpc_replays_stream_frames_before_terminal_result(
    tmp_path: Path,
) -> None:
    executor, _, _ = _executor(tmp_path)
    server = ExecutorRPCServer(executor, token="x" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "x" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
    )
    chunks: list[tuple[str, bytes]] = []

    async def collect(stream: str, chunk: bytes) -> None:
        chunks.append((stream, chunk))

    await pool.bind_task("task_one", "workspace_one")
    result = await pool.run_shell(
        task_id="task_one",
        workspace_id="workspace_one",
        operation_id="rpc_one",
        script="printf 'one'; printf 'two' >&2",
        cwd=".",
        mode="inspect",
        timeout_seconds=10,
        output_callback=collect,
    )

    assert result["exit_code"] == 0
    assert b"".join(chunk for _, chunk in chunks) in {b"onetwo", b"twoone"}
    await pool.close_task("task_one", "workspace_one")
    await server.close()


@pytest.mark.asyncio
async def test_executor_health_probe_is_stateless_while_slot_is_bound(
    tmp_path: Path,
) -> None:
    executor, _, _ = _executor(tmp_path)
    server = ExecutorRPCServer(executor, token="x" * 48)

    await server._dispatch(
        "bind_task",
        {
            "task_id": "task_one",
            "workspace_id": "workspace_one",
            "controller_id": "controller_local",
            "controller_generation": 1,
        },
    )
    assert await server._dispatch("health", {}) == {"healthy": True}
    assert server._task_id == "task_one"
    assert server._workspace_id == "workspace_one"
    await server._dispatch(
        "close_task",
        {
            "task_id": "task_one",
            "workspace_id": "workspace_one",
            "controller_id": "controller_local",
            "controller_generation": 1,
        },
    )


@pytest.mark.asyncio
async def test_stopping_one_task_interrupts_only_its_shell_process_group(
    tmp_path: Path,
) -> None:
    executor, repository, _ = _executor(tmp_path)
    running = asyncio.create_task(
        executor.run_shell(
            task_id="task_one",
            workspace_id="workspace_one",
            operation_id="long_one",
            script="sleep 30 & wait",
            cwd=".",
            mode="inspect",
            timeout_seconds=60,
        )
    )
    for _ in range(100):
        if "long_one" in executor._shells:
            break
        await asyncio.sleep(0.01)
    assert "long_one" in executor._shells

    await executor.stop_task("task_one")
    result = await asyncio.wait_for(running, timeout=5)

    assert result["reason"] == "task_closed"
    assert result["changeset_eligible"] is False
    assert repository.joinpath("app.py").read_text(encoding="utf-8") == "print('old')\n"
