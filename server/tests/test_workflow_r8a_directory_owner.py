"""Real process ownership probes; initialization is sliced from production main.

This is not a full ASGI boot: unrelated providers/stores are deliberately absent.
"""
from __future__ import annotations

import json
import ast
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKER = r'''
import ast, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
source = pathlib.Path(sys.argv[2], "server/main.py").read_text(encoding="utf-8")
tree = ast.parse(source)
namespace = {"AGENT_TASK_STORAGE_DIR": str(root)}
bindings = [n for n in tree.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "runtime_directory_owner" for t in n.targets)]
if bindings:
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner
    namespace["RuntimeDirectoryOwner"] = RuntimeDirectoryOwner
    exec(compile(ast.Module(body=bindings, type_ignores=[]), "main-owner-slice", "exec"), namespace)
from server.xpert_runtime.execution_store import WorkflowExecutionStore
store = WorkflowExecutionStore(root)
if sys.argv[3] == "hold":
    store.create(task_id="synthetic", run_id="run", run_type="workflow", workflow={}, inputs={})
    store.claim("synthetic", worker_id="first")
    (root / "ready.signal").write_text("ready", encoding="ascii")
    sys.stdin.readline()
else:
    print(json.dumps({"status": store.require("synthetic").status}))
'''


def environment():
    # Do not inherit provider credentials or user Runtime directories.
    result = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    result.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return result


def command(root, mode):
    return [sys.executable, "-B", "-c", WORKER, str(root), str(ROOT), mode]


@pytest.fixture
def held_directory(tmp_path):
    process = subprocess.Popen(command(tmp_path, "hold"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, cwd=tmp_path, env=environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "ready.signal").exists():
            if process.poll() is not None:
                pytest.fail(process.communicate()[1].decode("utf-8", errors="replace"))
            assert time.monotonic() < deadline, "synthetic owner did not start"
            time.sleep(0.02)
        yield tmp_path, process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_second_backend_cannot_repair_live_snapshot(held_directory):
    root, _ = held_directory
    before = (root / "workflow_executions.json").read_bytes()
    result = subprocess.run(command(root, "probe"), cwd=root, env=environment(), capture_output=True, timeout=20)
    assert result.returncode != 0, "second backend loaded and rewrote a live execution: " + result.stdout.decode()
    assert b"RUNTIME_DIRECTORY_BUSY" in result.stderr
    assert (root / "workflow_executions.json").read_bytes() == before


def test_crashed_owner_releases_directory_for_recovery(held_directory):
    root, process = held_directory
    process.kill()
    process.communicate(timeout=10)
    result = subprocess.run(command(root, "probe"), cwd=root, env=environment(), capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)["status"] == "ready"


def test_recovery_refuses_live_owner_without_modifying_files(held_directory):
    from server.xpert_runtime.execution_store_recovery import inspect_storage, restore_backup, RecoveryCommandError

    root, _ = held_directory
    before = (root / "workflow_executions.json").read_bytes()
    assert inspect_storage(root)["primary"]["valid"]
    with pytest.raises(RecoveryCommandError) as error:
        restore_backup(root, expected_primary_sha256="0" * 64, expected_backup_sha256="0" * 64)
    assert error.value.code == "RUNTIME_DIRECTORY_BUSY"
    assert str(root) not in str(error.value)
    assert (root / "workflow_executions.json").read_bytes() == before


def test_same_process_and_path_alias_cannot_claim_twice(tmp_path):
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner, RuntimeDirectoryError

    with RuntimeDirectoryOwner.acquire(tmp_path) as owner:
        owner.assert_owned()
        with pytest.raises(RuntimeDirectoryError, match="RUNTIME_DIRECTORY_BUSY"):
            RuntimeDirectoryOwner.acquire(tmp_path / ".")
        with RuntimeDirectoryOwner.acquire(tmp_path / "independent") as other:
            other.assert_owned()
    owner.close()
    assert (tmp_path / ".runtime-owner.lock").is_file()
    with RuntimeDirectoryOwner.acquire(tmp_path) as replacement:
        replacement.assert_owned()


def test_old_lock_file_is_not_a_stale_pid_barrier_or_overwritten(tmp_path):
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner

    path = tmp_path / ".runtime-owner.lock"
    path.write_bytes(b"retained lock sentinel, not authoritative PID data")
    with RuntimeDirectoryOwner.acquire(tmp_path):
        pass
    assert path.read_bytes() == b"retained lock sentinel, not authoritative PID data"


def test_default_directory_matches_runtime_environment(tmp_path, monkeypatch):
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner, RuntimeDirectoryError

    monkeypatch.setenv("AGENT_TASK_STORAGE_DIR", str(tmp_path))
    with RuntimeDirectoryOwner.acquire():
        with pytest.raises(RuntimeDirectoryError, match="RUNTIME_DIRECTORY_BUSY"):
            RuntimeDirectoryOwner.acquire(tmp_path)


def test_recovery_validation_failure_releases_owner(tmp_path):
    from server.xpert_runtime.execution_store_recovery import restore_backup, RecoveryCommandError
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner

    with pytest.raises(RecoveryCommandError):
        restore_backup(tmp_path, expected_primary_sha256="missing", expected_backup_sha256="0" * 64)
    with RuntimeDirectoryOwner.acquire(tmp_path):
        pass


@pytest.mark.parametrize("kind", ["directory", "hardlink", "symlink"])
def test_non_regular_or_aliased_lock_is_rejected(tmp_path, kind):
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner, RuntimeDirectoryError

    path = tmp_path / ".runtime-owner.lock"
    target = tmp_path / "sentinel"
    target.write_bytes(b"untouched")
    if kind == "directory":
        path.mkdir()
    elif kind == "hardlink":
        os.link(target, path)
    else:
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("Windows symlink privilege unavailable; covered on Linux")
    with pytest.raises(RuntimeDirectoryError) as error:
        RuntimeDirectoryOwner.acquire(tmp_path)
    assert str(tmp_path) not in str(error.value)
    assert target.read_bytes() == b"untouched"


def test_failure_after_os_lock_releases_descriptor(tmp_path, monkeypatch):
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner, RuntimeDirectoryError

    original = RuntimeDirectoryOwner.assert_owned

    def fail(_self):
        raise RuntimeDirectoryError("SYNTHETIC", "合成身份检查失败")

    monkeypatch.setattr(RuntimeDirectoryOwner, "assert_owned", fail)
    with pytest.raises(RuntimeDirectoryError, match="SYNTHETIC"):
        RuntimeDirectoryOwner.acquire(tmp_path)
    monkeypatch.setattr(RuntimeDirectoryOwner, "assert_owned", original)
    with RuntimeDirectoryOwner.acquire(tmp_path):
        pass


def test_open_error_is_sanitized(tmp_path, monkeypatch):
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner, RuntimeDirectoryError

    def fail(*_args, **_kwargs):
        raise PermissionError("PRIVATE_PATH_SENTINEL")

    monkeypatch.setattr(os, "open", fail)
    with pytest.raises(RuntimeDirectoryError) as error:
        RuntimeDirectoryOwner.acquire(tmp_path)
    assert error.value.code == "RUNTIME_DIRECTORY_UNAVAILABLE"
    assert "PRIVATE_PATH_SENTINEL" not in str(error.value)


def test_startup_checks_owner_before_starting_any_services():
    from server.xpert_runtime.runtime_directory import RuntimeDirectoryError

    tree = ast.parse((ROOT / "server/main.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_mcp_ttl_cleanup")
    node.decorator_list = []
    calls = []

    class InvalidOwner:
        def assert_owned(self):
            raise RuntimeDirectoryError("RUNTIME_DIRECTORY_OWNERSHIP_LOST", "合成进程不持有目录")

    async def start():
        calls.append("started")

    namespace = {"runtime_directory_owner": InvalidOwner(), "runtime_services_started": False,
                 "_start_independent_runtime_services": start, "_start_workflow_execution_services": start}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main-startup-slice", "exec"), namespace)
    with pytest.raises(RuntimeDirectoryError):
        asyncio.run(namespace[node.name]())
    assert not calls and namespace["runtime_services_started"] is False


def test_main_acquires_owner_before_runtime_store_construction():
    tree = ast.parse((ROOT / "server/main.py").read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "runtime_directory_owner" for t in n.targets))
    stores = [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
              and any(isinstance(child, ast.Name) and child.id == "AGENT_TASK_STORAGE_DIR"
                      for child in ast.walk(n.value)) and n is not owner]
    assert len(stores) >= 10
    assert all(owner.lineno < node.lineno for node in stores)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fork-only ownership boundary")
def test_forked_child_cannot_inherit_ownership_or_unlock_parent(tmp_path):
    # pytest may have background threads. Fork only in a fresh bounded process.
    script = r'''
import os, sys
from server.xpert_runtime.runtime_directory import RuntimeDirectoryOwner, RuntimeDirectoryError
with RuntimeDirectoryOwner.acquire(sys.argv[1]) as owner:
    child = os.fork()
    if child == 0:
        try:
            owner.assert_owned()
        except RuntimeDirectoryError:
            owner.close()
            os._exit(0)
        os._exit(1)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    try:
        RuntimeDirectoryOwner.acquire(sys.argv[1])
    except RuntimeDirectoryError as error:
        assert error.code == 'RUNTIME_DIRECTORY_BUSY'
    else:
        raise AssertionError('child unlocked parent')
    owner.assert_owned()
'''
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], cwd=tmp_path,
                            env=environment(), capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
