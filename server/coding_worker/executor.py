from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import signal
import stat
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import SAFE_ID
from .code_intelligence import CodeIntelligenceError, query_code_intelligence


MAX_EXECUTOR_RPC_BYTES = 8 * 1024 * 1024
MAX_SHELL_CHANGE_BYTES = 4 * 1024 * 1024


class SidecarExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ExecutorRPCError(SidecarExecutionError):
    pass


@dataclass(slots=True)
class _Service:
    service_id: str
    task_id: str
    process: asyncio.subprocess.Process
    started_at: float
    expires_at: float
    output: bytearray
    preview_port: int | None = None
    state: str = "running"
    exit_code: int | None = None
    reason: str | None = None
    monitor: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _ShellProcess:
    task_id: str
    operation_id: str
    process: asyncio.subprocess.Process
    reason: str | None = None
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class SidecarExecutor:
    """Runs task commands inside one non-root slot sidecar."""

    def __init__(
        self,
        workspace_resolver: Callable[[str], Path],
        *,
        runtime_root: Path,
        max_output_bytes: int = 2 * 1024 * 1024,
        max_services_per_task: int = 4,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._runtime_root = Path(runtime_root)
        self._max_output_bytes = max_output_bytes
        self._max_services_per_task = max_services_per_task
        self._services: dict[str, _Service] = {}
        self._processes: dict[str, set[asyncio.subprocess.Process]] = {}
        self._shells: dict[str, _ShellProcess] = {}
        self._shell_reservations: set[str] = set()
        self._intelligence_reservations: set[str] = set()
        self._lock = asyncio.Lock()
        self._remove_owned_runtime(self._runtime_root / "shell")
        self._remove_owned_runtime(self._runtime_root / "lsp")

    async def run_process(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        timeout_seconds: int,
        isolated: bool,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        if SAFE_ID.fullmatch(task_id) is None:
            raise SidecarExecutionError(
                "Command task binding is invalid.", code="executor_binding_invalid"
            )
        repository = self._workspace_resolver(workspace_id)
        execution_root: Path | None = None
        execution_repository = repository
        process: asyncio.subprocess.Process | None = None
        try:
            if isolated:
                execution_root = self._runtime_root / "checks" / f"run_{uuid.uuid4().hex}"
                execution_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(repository, execution_root, ignore=shutil.ignore_patterns(".git"))
                execution_repository = execution_root
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=execution_repository,
                    env=self._environment(execution_repository, environment_overrides),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
            except FileNotFoundError:
                return {
                    "argv": list(argv),
                    "exit_code": 127,
                    "output": f"{argv[0]}: command not found\n",
                }
            async with self._lock:
                self._processes.setdefault(task_id, set()).add(process)
            try:
                output = await asyncio.wait_for(
                    self._collect(process), timeout=timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise SidecarExecutionError("Command timed out.", code="command_timeout")
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise
            return {
                "argv": list(argv),
                "exit_code": int(process.returncode or 0),
                "output": output.decode("utf-8", errors="replace"),
            }
        finally:
            if process is not None:
                if process.returncode is None:
                    await self._interrupt(process)
                async with self._lock:
                    processes = self._processes.get(task_id)
                    if processes is not None:
                        processes.discard(process)
                        if not processes:
                            self._processes.pop(task_id, None)
            if execution_root is not None:
                shutil.rmtree(execution_root, ignore_errors=True)

    async def run_shell(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        script: str,
        cwd: str,
        mode: str,
        timeout_seconds: int,
        output_callback: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> dict[str, object]:
        if (
            SAFE_ID.fullmatch(task_id) is None
            or SAFE_ID.fullmatch(operation_id) is None
            or not script
            or len(script.encode("utf-8")) > 64 * 1024
            or "\x00" in script
            or mode not in {"inspect", "mutate"}
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= 3600
        ):
            raise SidecarExecutionError("Shell request is invalid.", code="shell_input_invalid")
        relative_cwd = self._relative_path(cwd, allow_root=True)
        if os.name == "nt" or not Path("/bin/bash").is_file():
            raise SidecarExecutionError("Bash is unavailable.", code="shell_unavailable")
        repository = self._workspace_resolver(workspace_id)
        before = self._snapshot_files(repository)
        base_tree_hash = self._snapshot_hash(before)
        run_root = self._runtime_root / "shell" / task_id / operation_id
        if run_root.exists() or run_root.is_symlink():
            raise SidecarExecutionError(
                "Shell operation runtime already exists.", code="shell_operation_exists"
            )
        async with self._lock:
            if task_id in self._shell_reservations or any(
                item.task_id == task_id for item in self._shells.values()
            ):
                raise SidecarExecutionError(
                    "A shell operation is already active for this task.",
                    code="shell_capacity_exhausted",
                )
            self._shell_reservations.add(task_id)
        execution_repository = run_root / "repo"
        process: asyncio.subprocess.Process | None = None
        shell_process: _ShellProcess | None = None
        try:
            run_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                repository,
                execution_repository,
                ignore=shutil.ignore_patterns(".git"),
                symlinks=True,
            )
            if self._snapshot_files(repository) != before:
                raise SidecarExecutionError(
                    "Workspace changed while cloning shell input.",
                    code="workspace_tree_changed",
                )
            cloned = self._snapshot_files(execution_repository)
            if cloned != before:
                raise SidecarExecutionError(
                    "Shell clone does not match the workspace.",
                    code="workspace_tree_changed",
                )
            execution_cwd = execution_repository.joinpath(*relative_cwd.parts)
            if not execution_cwd.is_dir() or execution_cwd.is_symlink():
                raise SidecarExecutionError(
                    "Shell cwd is unavailable.", code="workspace_path_invalid"
                )
            home = run_root / "home"
            temporary = run_root / "tmp"
            home.mkdir()
            temporary.mkdir()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).with_name("shell_sandbox.py")),
                "--repository",
                str(execution_repository),
                "--home",
                str(home),
                "--temporary",
                str(temporary),
                "--cwd",
                str(execution_cwd),
                "--",
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                script,
                cwd=execution_cwd,
                env=self._environment(
                    execution_repository,
                    {"HOME": str(home), "TMPDIR": str(temporary)},
                ),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            shell_process = _ShellProcess(
                task_id=task_id,
                operation_id=operation_id,
                process=process,
            )
            async with self._lock:
                self._shells[operation_id] = shell_process
            output = bytearray()
            output_lock = asyncio.Lock()
            reason: str | None = None

            async def record(stream_name: str, chunk: bytes) -> None:
                nonlocal reason
                async with output_lock:
                    remaining = self._max_output_bytes - len(output)
                    accepted = chunk[: max(remaining, 0)]
                    if accepted:
                        output.extend(accepted)
                        if output_callback is not None:
                            await output_callback(stream_name, accepted)
                    if len(accepted) != len(chunk) and reason is None:
                        reason = "shell_output_limit"
                        if process is not None and process.returncode is None:
                            process.kill()

            async def pump(
                stream_name: str, reader: asyncio.StreamReader | None
            ) -> None:
                if reader is None:
                    return
                while True:
                    chunk = await reader.read(64 * 1024)
                    if not chunk:
                        return
                    await record(stream_name, chunk)

            stdout = asyncio.create_task(pump("stdout", process.stdout))
            stderr = asyncio.create_task(pump("stderr", process.stderr))
            process_wait = asyncio.create_task(process.wait())
            stop_wait = asyncio.create_task(shell_process.stop_requested.wait())
            try:
                done, _ = await asyncio.wait(
                    {process_wait, stop_wait},
                    timeout=timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    reason = "shell_timeout"
                    await self._interrupt(process)
                elif stop_wait in done:
                    reason = shell_process.reason or "task_closed"
                    await self._interrupt(process)
                else:
                    await process_wait
                await asyncio.gather(stdout, stderr)
            except asyncio.CancelledError:
                await self._interrupt(process)
                await asyncio.gather(stdout, stderr, return_exceptions=True)
                raise
            except Exception:
                await self._interrupt(process)
                await asyncio.gather(stdout, stderr, return_exceptions=True)
                raise
            finally:
                if not process_wait.done():
                    process_wait.cancel()
                if not stop_wait.done():
                    stop_wait.cancel()
                await asyncio.gather(process_wait, stop_wait, return_exceptions=True)
                async with self._lock:
                    self._shells.pop(operation_id, None)
                shell_process.finished.set()
            after = self._snapshot_files(execution_repository)
            changed, violations = self._shell_changes(before, after)
            changed_bytes = sum(
                len(str(change.get("content", "")).encode("utf-8"))
                for change in changed
            )
            if changed_bytes > MAX_SHELL_CHANGE_BYTES:
                violations.append(
                    {
                        "reason": "changeset_too_large",
                        "size": changed_bytes,
                        "limit": MAX_SHELL_CHANGE_BYTES,
                    }
                )
            exit_code = int(process.returncode or 0)
            eligible = (
                mode == "mutate"
                and reason is None
                and exit_code == 0
                and not violations
            )
            return {
                "mode": mode,
                "exit_code": exit_code,
                "reason": reason,
                "base_tree_hash": base_tree_hash,
                "clone_tree_hash": self._snapshot_hash(after),
                "workspace_changed": before != after,
                "changeset_eligible": eligible,
                "changes": changed if eligible else [],
                "change_summary": self._change_summary(before, after, violations),
                "output": output.decode("utf-8", errors="replace"),
            }
        finally:
            if process is not None:
                if process.returncode is None:
                    await self._interrupt(process)
                async with self._lock:
                    self._shells.pop(operation_id, None)
            if shell_process is not None:
                shell_process.finished.set()
            async with self._lock:
                self._shell_reservations.discard(task_id)
            self._remove_owned_runtime(run_root)

    async def start_service(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        ttl_seconds: int,
        preview_port: int | None = None,
    ) -> dict[str, object]:
        if not 1 <= ttl_seconds <= 3600:
            raise SidecarExecutionError("Service TTL is invalid.", code="service_ttl_invalid")
        if preview_port is not None and not 1024 <= preview_port <= 65535:
            raise SidecarExecutionError(
                "Preview port is invalid.", code="service_preview_invalid"
            )
        async with self._lock:
            active = sum(
                service.task_id == task_id and service.state == "running"
                for service in self._services.values()
            )
            if active >= self._max_services_per_task:
                raise SidecarExecutionError(
                    "Task service capacity is exhausted.", code="service_capacity_exhausted"
                )
            now = time.time()
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self._workspace_resolver(workspace_id),
                env=self._environment(
                    self._workspace_resolver(workspace_id),
                    (
                        {"HOST": "0.0.0.0", "PORT": str(preview_port)}
                        if preview_port is not None
                        else None
                    ),
                ),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            service = _Service(
                service_id=f"service_{uuid.uuid4().hex}",
                task_id=task_id,
                process=process,
                started_at=now,
                expires_at=now + ttl_seconds,
                output=bytearray(),
                preview_port=preview_port,
            )
            self._services[service.service_id] = service
            service.monitor = asyncio.create_task(self._monitor(service, ttl_seconds))
            return self._service_result(service, include_output=False)

    async def code_intelligence(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        operation: str,
        path: str,
        line: int,
        character: int,
    ) -> dict[str, object]:
        if (
            SAFE_ID.fullmatch(task_id) is None
            or SAFE_ID.fullmatch(operation_id) is None
            or operation
            not in {"symbols", "definition", "references", "hover", "diagnostics"}
            or isinstance(line, bool)
            or isinstance(character, bool)
            or not isinstance(line, int)
            or not isinstance(character, int)
            or not 0 <= line <= 10_000_000
            or not 0 <= character <= 10_000_000
        ):
            raise SidecarExecutionError(
                "Code intelligence request is invalid.",
                code="code_intelligence_input_invalid",
            )
        relative = self._relative_path(path)
        repository = self._workspace_resolver(workspace_id)
        before = self._snapshot_files(repository)
        target = repository.joinpath(*relative.parts)
        if not target.is_file() or self._is_link(target):
            raise SidecarExecutionError(
                "Code intelligence entry is unavailable.",
                code="code_intelligence_input_invalid",
            )
        async with self._lock:
            if task_id in self._intelligence_reservations:
                raise SidecarExecutionError(
                    "Code intelligence is already active for this task.",
                    code="code_intelligence_capacity_exhausted",
                )
            self._intelligence_reservations.add(task_id)
        runtime = self._runtime_root / "lsp" / task_id / operation_id
        try:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.mkdir()
            result = await asyncio.wait_for(
                query_code_intelligence(
                    repository=repository,
                    relative_path=relative.as_posix(),
                    operation=operation,
                    line=line,
                    character=character,
                    environment=self._environment(repository),
                    runtime_root=runtime,
                ),
                timeout=30,
            )
            if self._snapshot_files(repository) != before:
                raise SidecarExecutionError(
                    "Workspace changed during code intelligence.",
                    code="workspace_tree_changed",
                )
            return result
        except TimeoutError as exc:
            raise SidecarExecutionError(
                "Code intelligence timed out.", code="code_intelligence_timeout"
            ) from exc
        finally:
            async with self._lock:
                self._intelligence_reservations.discard(task_id)
            self._remove_owned_runtime(runtime)
            if not self._is_link(runtime.parent):
                with contextlib.suppress(OSError):
                    runtime.parent.rmdir()

    def service_status(self, *, task_id: str, service_id: str) -> dict[str, object]:
        return self._service_result(self._require_service(task_id, service_id), include_output=True)

    async def service_input(self, *, task_id: str, service_id: str, data: str) -> None:
        if not data or len(data.encode("utf-8")) > 64 * 1024:
            raise SidecarExecutionError("Service input is invalid.", code="service_input_invalid")
        service = self._require_service(task_id, service_id)
        if service.state != "running" or service.process.stdin is None:
            raise SidecarExecutionError("Service is not running.", code="service_not_running")
        service.process.stdin.write(data.encode("utf-8"))
        await service.process.stdin.drain()

    async def stop_service(self, *, task_id: str, service_id: str) -> dict[str, object]:
        service = self._require_service(task_id, service_id)
        if service.state == "running":
            service.reason = "user_interrupted"
            await self._interrupt(service.process)
            if service.monitor is not None:
                await service.monitor
        return self._service_result(service, include_output=True)

    async def stop_task(self, task_id: str) -> None:
        services = [
            service
            for service in self._services.values()
            if service.task_id == task_id and service.state == "running"
        ]
        for service in services:
            service.reason = "task_closed"
            await self._interrupt(service.process)
        shells = [item for item in self._shells.values() if item.task_id == task_id]
        processes = tuple(self._processes.get(task_id, ()))
        for shell in shells:
            shell.reason = "task_closed"
            shell.stop_requested.set()
        for process in processes:
            if process.returncode is None:
                await self._interrupt(process)
        await asyncio.gather(
            *(shell.finished.wait() for shell in shells),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(service.monitor for service in services if service.monitor is not None),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(process.wait() for process in processes), return_exceptions=True
        )

    @classmethod
    def _snapshot_files(cls, repository: Path) -> dict[str, tuple[bytes, int]]:
        snapshot: dict[str, tuple[bytes, int]] = {}
        for current, directories, files in os.walk(
            repository, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            if current_path == repository:
                directories[:] = [name for name in directories if name != ".git"]
            directories.sort()
            for name in directories:
                directory = current_path / name
                if name == ".git" or cls._is_link(directory):
                    raise SidecarExecutionError(
                        "Shell workspace contains a link.", code="workspace_changed"
                    )
            for name in sorted(files):
                path = current_path / name
                metadata = path.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or cls._is_link(path)
                    or metadata.st_nlink != 1
                ):
                    raise SidecarExecutionError(
                        "Shell workspace contains an unsafe file.",
                        code="workspace_changed",
                    )
                content = path.read_bytes()
                after = path.lstat()
                if (
                    len(content) != metadata.st_size
                    or (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_mtime_ns)
                ):
                    raise SidecarExecutionError(
                        "Shell workspace changed while reading.",
                        code="workspace_changed",
                    )
                relative = path.relative_to(repository).as_posix()
                snapshot[relative] = (content, stat.S_IMODE(metadata.st_mode))
        return snapshot

    @staticmethod
    def _snapshot_hash(snapshot: Mapping[str, tuple[bytes, int]]) -> str:
        files_by_directory: dict[tuple[str, ...], list[str]] = {}
        child_directories: dict[tuple[str, ...], set[str]] = {}
        for path in snapshot:
            parts = PurePosixPath(path).parts
            parent: tuple[str, ...] = ()
            for directory in parts[:-1]:
                child_directories.setdefault(parent, set()).add(directory)
                parent = (*parent, directory)
            files_by_directory.setdefault(parent, []).append(path)

        def ordered_paths(parent: tuple[str, ...] = ()) -> Any:
            for path in sorted(
                files_by_directory.get(parent, ()),
                key=lambda value: PurePosixPath(value).name,
            ):
                yield path
            for directory in sorted(child_directories.get(parent, ())):
                yield from ordered_paths((*parent, directory))

        digest = hashlib.sha256()
        for path in ordered_paths():
            content = snapshot[path][0]
            relative = path.encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(hashlib.sha256(content).digest())
        return digest.hexdigest()

    @staticmethod
    def _shell_changes(
        before: Mapping[str, tuple[bytes, int]],
        after: Mapping[str, tuple[bytes, int]],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        changes: list[dict[str, object]] = []
        violations: list[dict[str, object]] = []
        for path in sorted(set(before) | set(after)):
            old = before.get(path)
            new = after.get(path)
            if old == new:
                continue
            content = (new or old)[0]  # type: ignore[index]
            if b"\x00" in content:
                violations.append(
                    {
                        "path": path,
                        "reason": "binary_change_rejected",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )
                continue
            if old is not None and new is not None and old[1] != new[1]:
                violations.append(
                    {"path": path, "reason": "mode_change_rejected"}
                )
                continue
            if new is None:
                assert old is not None
                changes.append(
                    {
                        "kind": "delete",
                        "path": path,
                        "expected_sha256": hashlib.sha256(old[0]).hexdigest(),
                    }
                )
                continue
            try:
                text = new[0].decode("utf-8")
            except UnicodeDecodeError:
                violations.append(
                    {
                        "path": path,
                        "reason": "binary_change_rejected",
                        "sha256": hashlib.sha256(new[0]).hexdigest(),
                        "size": len(new[0]),
                    }
                )
                continue
            change: dict[str, object] = {
                "kind": "write",
                "path": path,
                "content": text,
                "content_sha256": hashlib.sha256(new[0]).hexdigest(),
            }
            if old is None:
                change["expected_absent"] = True
            else:
                change["expected_sha256"] = hashlib.sha256(old[0]).hexdigest()
            changes.append(change)
        return changes, violations

    @staticmethod
    def _change_summary(
        before: Mapping[str, tuple[bytes, int]],
        after: Mapping[str, tuple[bytes, int]],
        violations: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "added": sorted(set(after) - set(before)),
            "deleted": sorted(set(before) - set(after)),
            "modified": sorted(
                path
                for path in set(before) & set(after)
                if before[path] != after[path]
            ),
            "violations": violations,
        }

    @staticmethod
    def _relative_path(value: str, *, allow_root: bool = False) -> PurePosixPath:
        raw_parts = value.split("/")
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or any(
                part in {"", "..", ".git"}
                or (part == "." and not (value == "." and allow_root))
                for part in raw_parts
            )
            or any(part in {"", "..", ".git"} for part in path.parts)
            or (path.as_posix() == "." and not allow_root)
        ):
            raise SidecarExecutionError(
                "Workspace path is invalid.", code="workspace_path_invalid"
            )
        return path

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()  # type: ignore[attr-defined]
        )

    @classmethod
    def _remove_owned_runtime(cls, path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if cls._is_link(path):
            raise SidecarExecutionError(
                "Shell runtime is unsafe.", code="shell_runtime_unsafe"
            )
        shutil.rmtree(path, ignore_errors=True)

    async def _monitor(self, service: _Service, ttl_seconds: int) -> None:
        drain = asyncio.create_task(self._drain(service))
        try:
            try:
                await asyncio.wait_for(service.process.wait(), timeout=ttl_seconds)
            except TimeoutError:
                service.reason = "service_ttl_expired"
                await self._interrupt(service.process)
            await drain
        finally:
            if not drain.done():
                drain.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain
            service.exit_code = service.process.returncode
            if service.reason is not None:
                service.state = "stopped"
            elif service.exit_code == 0:
                service.state = "completed"
            else:
                service.state = "failed"
                service.reason = "service_exited"

    async def _drain(self, service: _Service) -> None:
        if service.process.stdout is None:
            return
        while True:
            chunk = await service.process.stdout.read(64 * 1024)
            if not chunk:
                return
            if len(service.output) + len(chunk) > self._max_output_bytes:
                remaining = self._max_output_bytes - len(service.output)
                service.output.extend(chunk[: max(remaining, 0)])
                service.reason = "service_output_limit"
                service.process.kill()
                return
            service.output.extend(chunk)

    async def _collect(self, process: asyncio.subprocess.Process) -> bytes:
        if process.stdout is None:
            raise SidecarExecutionError("Command output is unavailable.", code="command_failed")
        output = bytearray()
        while True:
            chunk = await process.stdout.read(64 * 1024)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > self._max_output_bytes:
                process.kill()
                await process.wait()
                raise SidecarExecutionError(
                    "Command output is too large.", code="tool_output_too_large"
                )
        await process.wait()
        return bytes(output)

    def _require_service(self, task_id: str, service_id: str) -> _Service:
        service = self._services.get(service_id)
        if service is None or service.task_id != task_id:
            raise SidecarExecutionError("Service was not found.", code="service_not_found")
        return service

    @staticmethod
    def _service_result(service: _Service, *, include_output: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "service_id": service.service_id,
            "task_id": service.task_id,
            "state": service.state,
            "started_at": service.started_at,
            "expires_at": service.expires_at,
            "exit_code": service.exit_code,
            "reason": service.reason,
            "preview_port": service.preview_port,
        }
        if include_output and service.state != "running":
            result["output"] = bytes(service.output).decode("utf-8", errors="replace")
        return result

    @staticmethod
    async def _interrupt(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _environment(
        repository: Path, overrides: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        home = repository.parent / "home"
        home.mkdir(exist_ok=True)
        environment = {
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        environment.update(overrides or {})
        return environment


class ExecutorRPCServer:
    """Credential-free command host for one fixed workspace slot."""

    def __init__(self, executor: SidecarExecutor, *, token: str) -> None:
        if len(token) < 32:
            raise ValueError("executor RPC token is too short")
        self.executor = executor
        self._token = token
        self._server: asyncio.AbstractServer | None = None
        self.endpoint: str | None = None
        self._task_id: str | None = None
        self._workspace_id: str | None = None
        self._controller_id: str | None = None
        self._controller_generation = 0
        self._binding_lock = asyncio.Lock()
        self._active_requests: dict[asyncio.Task[Any], int] = {}

    async def start_unix(self, socket_path: Path) -> str:
        socket_path = Path(socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(socket_path), limit=MAX_EXECUTOR_RPC_BYTES
        )
        socket_path.chmod(0o660)
        self.endpoint = f"unix:{socket_path}"
        return self.endpoint

    async def start_tcp_for_tests(self) -> str:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=MAX_EXECUTOR_RPC_BYTES
        )
        address = self._server.sockets[0].getsockname()
        self.endpoint = f"tcp:127.0.0.1:{address[1]}"
        return self.endpoint

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._task_id is not None:
            await self.executor.stop_task(self._task_id)
        self._server = None
        self.endpoint = None
        self._task_id = None
        self._workspace_id = None
        self._controller_id = None
        self._controller_generation = 0
        self._active_requests.clear()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_EXECUTOR_RPC_BYTES or not raw.endswith(b"\n"):
                raise ExecutorRPCError("Executor request is invalid.", code="executor_request_invalid")
            value = json.loads(raw)
            token = value.get("token") if isinstance(value, dict) else None
            action = value.get("action") if isinstance(value, dict) else None
            payload = value.get("payload") if isinstance(value, dict) else None
            if not isinstance(token, str) or not secrets.compare_digest(token, self._token):
                raise ExecutorRPCError("Executor authentication failed.", code="executor_unauthorized")
            if not isinstance(action, str) or not isinstance(payload, dict):
                raise ExecutorRPCError("Executor request is invalid.", code="executor_request_invalid")
            async def send_output(stream_name: str, chunk: bytes) -> None:
                frame = {
                    "type": "output",
                    "stream": stream_name,
                    "data": base64.b64encode(chunk).decode("ascii"),
                }
                writer.write(
                    json.dumps(frame, separators=(",", ":")).encode() + b"\n"
                )
                await writer.drain()

            result = await self._dispatch(
                action,
                payload,
                output_callback=send_output if action == "run_shell" else None,
            )
            response = {"ok": True, "result": result}
        except asyncio.CancelledError:
            response = {
                "ok": False,
                "error": {
                    "code": "executor_controller_stale",
                    "message": "Executor controller was superseded.",
                },
            }
        except Exception as exc:
            response = {
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", "executor_failed"),
                    "message": str(exc)
                    if isinstance(
                        exc,
                        (
                            ExecutorRPCError,
                            SidecarExecutionError,
                            CodeIntelligenceError,
                            ValueError,
                        ),
                    )
                    else "Executor request failed.",
                },
            }
        encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
        writer.write(encoded)
        await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    async def _dispatch(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        output_callback: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if action == "health":
            if payload:
                raise ExecutorRPCError(
                    "Executor health request is invalid.",
                    code="executor_request_invalid",
                )
            return {"healthy": True}
        task_id = str(payload.get("task_id", ""))
        workspace_id = str(payload.get("workspace_id", ""))
        controller_id, controller_generation = self._controller_binding(payload)
        if action == "bind_task":
            if (
                SAFE_ID.fullmatch(task_id) is None
                or SAFE_ID.fullmatch(workspace_id) is None
            ):
                raise ExecutorRPCError("Executor binding is invalid.", code="executor_binding_invalid")
            return await self._bind_task(
                task_id, workspace_id, controller_id, controller_generation
            )
        if action == "close_task":
            return await self._close_task_binding(
                task_id, workspace_id, controller_id, controller_generation
            )
        await self._begin_bound_request(
            task_id, workspace_id, controller_id, controller_generation
        )
        try:
            if action == "execute_process":
                return await self.executor.run_process(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    argv=tuple(payload.get("argv", ())),
                    timeout_seconds=int(payload.get("timeout_seconds", 0)),
                    isolated=payload.get("isolated") is True,
                    environment_overrides=payload.get("environment_overrides"),
                )
            if action == "run_shell":
                return await self.executor.run_shell(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    operation_id=str(payload.get("operation_id", "")),
                    script=str(payload.get("script", "")),
                    cwd=str(payload.get("cwd", "")),
                    mode=str(payload.get("mode", "")),
                    timeout_seconds=int(payload.get("timeout_seconds", 0)),
                    output_callback=output_callback,
                )
            if action == "code_intelligence":
                return await self.executor.code_intelligence(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    operation_id=str(payload.get("operation_id", "")),
                    operation=str(payload.get("operation", "")),
                    path=str(payload.get("path", "")),
                    line=int(payload.get("line", 0)),
                    character=int(payload.get("character", 0)),
                )
            if action == "start_service":
                return await self.executor.start_service(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    argv=tuple(payload.get("argv", ())),
                    ttl_seconds=int(payload.get("ttl_seconds", 0)),
                    preview_port=(
                        int(payload["preview_port"])
                        if payload.get("preview_port") is not None
                        else None
                    ),
                )
            if action == "service_status":
                return self.executor.service_status(
                    task_id=task_id, service_id=str(payload.get("service_id", ""))
                )
            if action == "service_input":
                await self.executor.service_input(
                    task_id=task_id,
                    service_id=str(payload.get("service_id", "")),
                    data=str(payload.get("data", "")),
                )
                return {"accepted": True}
            if action == "stop_service":
                return await self.executor.stop_service(
                    task_id=task_id, service_id=str(payload.get("service_id", ""))
                )
            raise ExecutorRPCError("Executor action is invalid.", code="executor_request_invalid")
        finally:
            await self._finish_bound_request()

    def _require_binding(
        self,
        task_id: str,
        workspace_id: str,
        controller_id: str,
        controller_generation: int,
    ) -> None:
        if (
            self._task_id != task_id
            or self._workspace_id != workspace_id
            or self._controller_id != controller_id
            or self._controller_generation != controller_generation
        ):
            raise ExecutorRPCError("Executor task is not bound.", code="executor_binding_invalid")

    @staticmethod
    def _controller_binding(payload: Mapping[str, Any]) -> tuple[str, int]:
        controller_id = payload.get("controller_id")
        generation = payload.get("controller_generation")
        if (
            not isinstance(controller_id, str)
            or SAFE_ID.fullmatch(controller_id) is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise ExecutorRPCError(
                "Executor controller binding is invalid.",
                code="executor_binding_invalid",
            )
        return controller_id, generation

    async def _bind_task(
        self,
        task_id: str,
        workspace_id: str,
        controller_id: str,
        controller_generation: int,
    ) -> dict[str, bool]:
        old_task_id: str | None = None
        interrupted: tuple[asyncio.Task[Any], ...] = ()
        async with self._binding_lock:
            if controller_generation < self._controller_generation or (
                controller_generation == self._controller_generation
                and self._controller_id not in {None, controller_id}
            ):
                raise ExecutorRPCError(
                    "Executor controller is stale.", code="executor_controller_stale"
                )
            if controller_generation > self._controller_generation:
                old_task_id = self._task_id
                self._controller_generation = controller_generation
                self._controller_id = controller_id
                self._task_id = self._workspace_id = None
                interrupted = tuple(
                    request
                    for request, generation in self._active_requests.items()
                    if generation < controller_generation
                )
            elif self._task_id is not None:
                if self._task_id == task_id and self._workspace_id == workspace_id:
                    return {"bound": True}
                raise ExecutorRPCError(
                    "Executor slot is busy.", code="executor_slot_busy"
                )
            elif self._controller_id is None:
                self._controller_generation = controller_generation
                self._controller_id = controller_id
        for request in interrupted:
            request.cancel()
        if interrupted:
            await asyncio.gather(*interrupted, return_exceptions=True)
        if old_task_id is not None:
            await self.executor.stop_task(old_task_id)
        self.executor._workspace_resolver(workspace_id)
        async with self._binding_lock:
            if (
                self._controller_generation != controller_generation
                or self._controller_id != controller_id
            ):
                raise ExecutorRPCError(
                    "Executor controller is stale.", code="executor_controller_stale"
                )
            if self._task_id is not None and (
                self._task_id != task_id or self._workspace_id != workspace_id
            ):
                raise ExecutorRPCError(
                    "Executor slot is busy.", code="executor_slot_busy"
                )
            self._task_id, self._workspace_id = task_id, workspace_id
        return {"bound": True}

    async def _begin_bound_request(
        self,
        task_id: str,
        workspace_id: str,
        controller_id: str,
        controller_generation: int,
    ) -> None:
        current = asyncio.current_task()
        if current is None:
            raise ExecutorRPCError(
                "Executor request is unavailable.", code="executor_request_invalid"
            )
        async with self._binding_lock:
            self._require_binding(
                task_id, workspace_id, controller_id, controller_generation
            )
            self._active_requests[current] = controller_generation

    async def _finish_bound_request(self) -> None:
        current = asyncio.current_task()
        if current is not None:
            async with self._binding_lock:
                self._active_requests.pop(current, None)

    async def _close_task_binding(
        self,
        task_id: str,
        workspace_id: str,
        controller_id: str,
        controller_generation: int,
    ) -> dict[str, bool]:
        await self._begin_bound_request(
            task_id, workspace_id, controller_id, controller_generation
        )
        try:
            await self.executor.stop_task(task_id)
            async with self._binding_lock:
                if (
                    self._controller_generation == controller_generation
                    and self._controller_id == controller_id
                    and self._task_id == task_id
                    and self._workspace_id == workspace_id
                ):
                    self._task_id = self._workspace_id = None
            return {"closed": True}
        finally:
            await self._finish_bound_request()


class ExecutorSidecarClientPool:
    """Routes task commands to the credential-free executor for a fixed slot."""

    def __init__(
        self,
        *,
        endpoints: Mapping[str, str],
        tokens: Mapping[str, str],
        workspace_slot_resolver: Callable[[str], str],
        auto_rebind: bool = False,
        controller_id: str = "controller_local",
        controller_generation: int = 1,
    ) -> None:
        if set(endpoints) != set(tokens) or not endpoints:
            raise ValueError("executor sidecar bindings are incomplete")
        self._endpoints = dict(endpoints)
        self._tokens = dict(tokens)
        self._workspace_slot_resolver = workspace_slot_resolver
        self._auto_rebind = auto_rebind
        if (
            SAFE_ID.fullmatch(controller_id) is None
            or isinstance(controller_generation, bool)
            or not isinstance(controller_generation, int)
            or controller_generation < 1
        ):
            raise ValueError("executor controller binding is invalid")
        self._controller_id = controller_id
        self._controller_generation = controller_generation

    async def bind_task(self, task_id: str, workspace_id: str) -> None:
        await self._workspace_call(workspace_id, "bind_task", {"task_id": task_id, "workspace_id": workspace_id})

    async def close_task(self, task_id: str, workspace_id: str) -> None:
        await self._workspace_call(workspace_id, "close_task", {"task_id": task_id, "workspace_id": workspace_id})

    async def run_process(self, *, task_id: str, workspace_id: str, argv: Sequence[str], timeout_seconds: int, isolated: bool, environment_overrides: Mapping[str, str] | None = None) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "execute_process", {"task_id": task_id, "workspace_id": workspace_id, "argv": list(argv), "timeout_seconds": timeout_seconds, "isolated": isolated, "environment_overrides": dict(environment_overrides or {})})

    async def run_shell(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        script: str,
        cwd: str,
        mode: str,
        timeout_seconds: int,
        output_callback: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        return await self._workspace_stream_call(
            workspace_id,
            "run_shell",
            {
                "task_id": task_id,
                "workspace_id": workspace_id,
                "operation_id": operation_id,
                "script": script,
                "cwd": cwd,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
            },
            output_callback=output_callback,
        )

    async def code_intelligence(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        operation: str,
        path: str,
        line: int,
        character: int,
    ) -> dict[str, Any]:
        return await self._workspace_call(
            workspace_id,
            "code_intelligence",
            {
                "task_id": task_id,
                "workspace_id": workspace_id,
                "operation_id": operation_id,
                "operation": operation,
                "path": path,
                "line": line,
                "character": character,
            },
        )

    async def start_service(self, *, task_id: str, workspace_id: str, argv: Sequence[str], ttl_seconds: int, preview_port: int | None = None) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "start_service", {"task_id": task_id, "workspace_id": workspace_id, "argv": list(argv), "ttl_seconds": ttl_seconds, "preview_port": preview_port})

    async def service_status(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "service_status", {"task_id": task_id, "workspace_id": workspace_id, "service_id": service_id})

    async def service_input(self, *, task_id: str, workspace_id: str, service_id: str, data: str) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "service_input", {"task_id": task_id, "workspace_id": workspace_id, "service_id": service_id, "data": data})

    async def stop_service(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "stop_service", {"task_id": task_id, "workspace_id": workspace_id, "service_id": service_id})

    async def _workspace_call(self, workspace_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._workspace_stream_call(
            workspace_id, action, payload, output_callback=None
        )

    async def _workspace_stream_call(
        self,
        workspace_id: str,
        action: str,
        payload: dict[str, Any],
        *,
        output_callback: Callable[[str, bytes], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        bound_payload = dict(payload)
        if action != "health":
            bound_payload["controller_id"] = self._controller_id
            bound_payload["controller_generation"] = self._controller_generation
        try:
            return await self._workspace_stream_call_once(
                workspace_id,
                action,
                bound_payload,
                output_callback=output_callback,
            )
        except ExecutorRPCError as exc:
            task_id = bound_payload.get("task_id")
            payload_workspace_id = bound_payload.get("workspace_id")
            if (
                not self._auto_rebind
                or exc.code != "executor_binding_invalid"
                or action in {"bind_task", "close_task", "health"}
                or not isinstance(task_id, str)
                or SAFE_ID.fullmatch(task_id) is None
                or payload_workspace_id != workspace_id
            ):
                raise
            await self._workspace_stream_call_once(
                workspace_id,
                "bind_task",
                {
                    "task_id": task_id,
                    "workspace_id": workspace_id,
                    "controller_id": self._controller_id,
                    "controller_generation": self._controller_generation,
                },
                output_callback=None,
            )
            return await self._workspace_stream_call_once(
                workspace_id,
                action,
                bound_payload,
                output_callback=output_callback,
            )

    async def _workspace_stream_call_once(
        self,
        workspace_id: str,
        action: str,
        payload: dict[str, Any],
        *,
        output_callback: Callable[[str, bytes], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        slot_id = self._workspace_slot_resolver(workspace_id)
        endpoint = self._endpoints.get(slot_id)
        token = self._tokens.get(slot_id)
        if endpoint is None or token is None:
            raise ExecutorRPCError("Executor slot is unavailable.", code="executor_unavailable")
        if endpoint.startswith("unix:"):
            reader, writer = await asyncio.open_unix_connection(endpoint[5:])
        elif endpoint.startswith("tcp:127.0.0.1:"):
            reader, writer = await asyncio.open_connection("127.0.0.1", int(endpoint.rsplit(":", 1)[1]))
        else:
            raise ExecutorRPCError("Executor endpoint is invalid.", code="executor_unavailable")
        try:
            request = json.dumps({"token": token, "action": action, "payload": payload}, separators=(",", ":")).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            while True:
                raw = await reader.readline()
                if (
                    not raw
                    or len(raw) > MAX_EXECUTOR_RPC_BYTES
                    or not raw.endswith(b"\n")
                ):
                    raise ExecutorRPCError(
                        "Executor response is invalid.",
                        code="executor_invalid_response",
                    )
                value = json.loads(raw)
                if isinstance(value, dict) and value.get("type") == "output":
                    if action != "run_shell" or output_callback is None:
                        raise ExecutorRPCError(
                            "Executor response is invalid.",
                            code="executor_invalid_response",
                        )
                    stream = value.get("stream")
                    encoded = value.get("data")
                    if stream not in {"stdout", "stderr"} or not isinstance(
                        encoded, str
                    ):
                        raise ExecutorRPCError(
                            "Executor response is invalid.",
                            code="executor_invalid_response",
                        )
                    try:
                        chunk = base64.b64decode(encoded, validate=True)
                    except (ValueError, TypeError) as exc:
                        raise ExecutorRPCError(
                            "Executor response is invalid.",
                            code="executor_invalid_response",
                        ) from exc
                    await output_callback(str(stream), chunk)
                    continue
                if not isinstance(value, dict) or value.get("ok") is not True:
                    error = value.get("error", {}) if isinstance(value, dict) else {}
                    raise ExecutorRPCError(
                        str(error.get("message", "Executor request failed.")),
                        code=str(error.get("code", "executor_failed")),
                    )
                result = value.get("result")
                if not isinstance(result, dict):
                    raise ExecutorRPCError(
                        "Executor response is invalid.",
                        code="executor_invalid_response",
                    )
                return result
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
