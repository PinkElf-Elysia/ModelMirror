from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from server.coding_runtime.draft_workspace import (
    DraftLimits,
)
from server.coding_runtime.command_bridge import CommandExecutionResult
from server.coding_runtime.commands import (
    ProjectCommand,
    RunnerPackManifest,
    load_runner_pack_manifest,
    runner_pack_matches_project,
)
from server.coding_runtime.patch_policy import (
    PatchPolicyError,
    SNAPSHOT_FINGERPRINT_PATTERN,
    snapshot_fingerprint as shared_snapshot_fingerprint,
    validate_patch,
)
from server.coding_runtime.verification import (
    MAX_VERIFICATION_DETAIL_CHARS,
    MAX_VERIFICATION_SUMMARY_CHARS,
    VerificationPlan,
    VerificationReport,
    VerificationResult,
    VerificationState,
    VerificationStep,
    VerificationStepId,
    initial_verification_report,
    sanitize_verification_output,
    select_verification_plan,
)


MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_RUNNER_PACK_ENTRIES = 200_000
BACKEND_TIMEOUT_SECONDS = 300
FRONTEND_TIMEOUT_SECONDS = 240


class VerificationEngineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "verification_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FixedCommand:
    argv: tuple[str, ...]
    timeout_seconds: int | float


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    output_truncated: bool = False


class VerificationRunner(Protocol):
    async def run(
        self,
        step_id: VerificationStepId,
        workspace: Path,
    ) -> CommandResult: ...


ProgressCallback = Callable[[VerificationReport], Awaitable[None] | None]


def default_fixed_commands() -> dict[VerificationStepId, FixedCommand]:
    backend = FixedCommand(
        argv=(
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "server/tests/",
            "-q",
        ),
        timeout_seconds=BACKEND_TIMEOUT_SECONDS,
    )
    return {
        VerificationStepId.BACKEND_TESTS: backend,
        VerificationStepId.BACKEND_BASELINE_TESTS: backend,
        VerificationStepId.BACKEND_DRAFT_TESTS: backend,
        VerificationStepId.FRONTEND_BUILD: FixedCommand(
            argv=("npm", "--prefix", "client", "run", "build"),
            timeout_seconds=FRONTEND_TIMEOUT_SECONDS,
        ),
    }


class SubprocessVerificationRunner:
    """Runs only constructor-provided argv without a shell or inherited secrets."""

    def __init__(
        self,
        commands: dict[VerificationStepId, FixedCommand] | None = None,
    ) -> None:
        self._commands = dict(commands or default_fixed_commands())

    async def run(
        self,
        step_id: VerificationStepId,
        workspace: Path,
    ) -> CommandResult:
        command = self._commands.get(step_id)
        if command is None:
            raise VerificationEngineError(
                "Verification step is not configured.",
                code="step_not_configured",
            )
        runtime_root = workspace / ".modelmirror-verifier"
        home = runtime_root / "home"
        temporary = Path(tempfile.gettempdir()).resolve() / ".mmv-tmp"
        data_root = runtime_root / "data"
        _prepare_temporary_root(temporary)
        for path in (home, data_root):
            path.mkdir(parents=True, exist_ok=True)
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "PYTHONDONTWRITEBYTECODE": "1",
            "RAG_STORAGE_DIR": str(data_root / "rag-storage"),
            "RAG_UPLOAD_DIR": str(data_root / "rag-uploads"),
            "AGENT_TASK_STORAGE_DIR": str(data_root / "runtime"),
            "XPERT_STORAGE_DIR": str(data_root / "xperts"),
            "XPERT_CONTEXT_STORAGE_DIR": str(data_root / "xpert-context"),
            "SKILL_INSTALLED_DIR": str(data_root / "skills-installed"),
            "SKILL_TMP_DIR": str(data_root / "skills-tmp"),
            "DATAX_STORAGE_DIR": str(data_root / "datax"),
        }
        try:
            started = time.monotonic()
            process = await asyncio.create_subprocess_exec(
                *command.argv,
                cwd=str(workspace),
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            assert process.stdout is not None and process.stderr is not None
            stdout_task = asyncio.create_task(
                _read_bounded_stream(process.stdout)
            )
            stderr_task = asyncio.create_task(
                _read_bounded_stream(process.stderr)
            )
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=command.timeout_seconds,
                )
            except TimeoutError as exc:
                await _terminate_process(process)
                await asyncio.gather(
                    stdout_task,
                    stderr_task,
                    return_exceptions=True,
                )
                raise VerificationEngineError(
                    "Verification step timed out.",
                    code="command_timeout",
                ) from exc
            except asyncio.CancelledError:
                await _terminate_process(process)
                await asyncio.gather(
                    stdout_task,
                    stderr_task,
                    return_exceptions=True,
                )
                raise
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
            return CommandResult(
                exit_code=int(process.returncode or 0),
                stdout=stdout,
                stderr=stderr,
                duration_ms=round((time.monotonic() - started) * 1000),
                output_truncated=stdout_truncated or stderr_truncated,
            )
        finally:
            _clear_temporary_root(temporary)

    async def run_project_command(
        self,
        command: ProjectCommand,
        workspace: Path,
        *,
        environment: dict[str, str] | None = None,
        max_duration_seconds: float | None = None,
    ) -> CommandResult:
        command_cwd = (workspace / command.cwd).resolve()
        if (
            command_cwd != workspace.resolve()
            and workspace.resolve() not in command_cwd.parents
        ) or command_cwd.is_symlink() or not command_cwd.is_dir():
            raise VerificationEngineError(
                "Project command working directory is unavailable.",
                code="command_cwd_unavailable",
            )
        executable = Path(command.argv[0]).name.casefold()
        if command.argv[0] != PurePosixPath(command.argv[0]).name:
            raise VerificationEngineError(
                "Project command executable path is denied.",
                code="command_executable_denied",
            )
        if executable.endswith(".exe"):
            executable = executable[:-4]
        allowed = {"python", "python3", "node", "npm"}
        extra_environment = dict(environment or {})
        allowed.update(
            item
            for item in extra_environment.pop("MODELMIRROR_ALLOWED_BINS", "").split(":")
            if item
        )
        if executable not in allowed:
            raise VerificationEngineError(
                "Project command executable is not available.",
                code="command_executable_denied",
            )
        runtime_root = workspace / ".modelmirror-verifier"
        home = runtime_root / "home"
        temporary = Path(tempfile.gettempdir()).resolve() / ".mmv-tmp"
        data_root = runtime_root / "data"
        _prepare_temporary_root(temporary)
        for path in (home, data_root):
            path.mkdir(parents=True, exist_ok=True)
        clean_environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        clean_environment.update(extra_environment)
        timeout = min(
            float(command.timeout_seconds),
            max_duration_seconds
            if max_duration_seconds is not None
            else float(command.timeout_seconds),
        )
        try:
            return await _run_bounded_command(
                FixedCommand(argv=command.argv, timeout_seconds=max(0.001, timeout)),
                cwd=command_cwd,
                environment=clean_environment,
            )
        finally:
            _clear_temporary_root(temporary)


class IsolatedProjectCommandExecutor:
    """Rebuilds one dynamic project in tmpfs and discards all command writes."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        runner_packs_root: Path | None = None,
        runner: SubprocessVerificationRunner | None = None,
        limits: DraftLimits | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.runner_packs_root = (
            runner_packs_root.resolve() if runner_packs_root is not None else None
        )
        self.runner = runner or SubprocessVerificationRunner()
        self.limits = limits or DraftLimits()

    async def execute(
        self,
        *,
        source_root: Path,
        expected_fingerprint: str,
        patch: str,
        paths: Sequence[str],
        command: ProjectCommand,
        runner_pack_id: str | None = None,
        max_duration_seconds: float | None = None,
    ) -> CommandExecutionResult:
        if source_root.is_symlink():
            raise VerificationEngineError(
                "Project snapshot is unsafe.",
                code="snapshot_unsafe",
            )
        source = source_root.resolve()
        if (
            source == self.workspace_root
            or source in self.workspace_root.parents
            or self.workspace_root in source.parents
            or snapshot_fingerprint(source) != expected_fingerprint
        ):
            raise VerificationEngineError(
                "Project snapshot does not match.",
                code="snapshot_mismatch",
            )
        if patch:
            validate_verification_patch(
                patch,
                expected_paths=paths,
                limits=self.limits,
            )
        elif paths:
            raise VerificationEngineError(
                "Project Patch paths are invalid.",
                code="invalid_patch",
            )
        self._clear_workspace()
        try:
            shutil.copytree(source, self.workspace_root, symlinks=False)
            _make_workspace_writable(self.workspace_root)
            if patch:
                _apply_patch(self.workspace_root, patch)
            pack: tuple[RunnerPackManifest, Path] | None = None
            if runner_pack_id is not None:
                pack = self._load_runner_pack(runner_pack_id, self.workspace_root)
            environment = self._attach_runner_pack(pack, command)
            result = await self.runner.run_project_command(
                command,
                self.workspace_root,
                environment=environment,
                max_duration_seconds=max_duration_seconds,
            )
            output = _sanitize_command_result(result)
            return CommandExecutionResult(
                status="passed" if result.exit_code == 0 else "failed",
                exit_code=result.exit_code,
                output=output,
                duration_seconds=result.duration_ms / 1000,
            )
        finally:
            self._clear_workspace()

    def _load_runner_pack(
        self,
        pack_id: str,
        source_root: Path,
    ) -> tuple[RunnerPackManifest, Path]:
        if self.runner_packs_root is None:
            raise VerificationEngineError(
                "Runner pack service is not configured.",
                code="runner_pack_unavailable",
            )
        try:
            manifest = load_runner_pack_manifest(self.runner_packs_root, pack_id)
        except Exception as exc:
            code = getattr(exc, "code", "runner_pack_invalid")
            raise VerificationEngineError(str(exc), code=code) from exc
        pack_root = (self.runner_packs_root / pack_id).resolve()
        _validate_runner_pack_tree(pack_root)
        if not runner_pack_matches_project(manifest, source_root):
            raise VerificationEngineError(
                "Runner pack does not match this project.",
                code="runner_pack_mismatch",
            )
        return manifest, pack_root

    def _attach_runner_pack(
        self,
        pack: tuple[RunnerPackManifest, Path] | None,
        command: ProjectCommand,
    ) -> dict[str, str]:
        if pack is None:
            return {}
        manifest, pack_root = pack
        environment: dict[str, str] = {}
        if manifest.python_paths:
            environment["PYTHONPATH"] = ":".join(
                str(_resolve_pack_path(pack_root, path))
                for path in manifest.python_paths
            )
        bin_paths = tuple(
            _resolve_pack_path(pack_root, path) for path in manifest.bin_paths
        )
        if bin_paths:
            environment["PATH"] = ":".join(
                [*(str(path) for path in bin_paths), "/usr/local/bin", "/usr/bin", "/bin"]
            )
            allowed_bins: set[str] = set()
            for bin_path in bin_paths:
                allowed_bins.update(
                    item.name.casefold()
                    for item in bin_path.iterdir()
                    if item.is_file()
                )
            environment["MODELMIRROR_ALLOWED_BINS"] = ":".join(sorted(allowed_bins))
        modules = dict(manifest.node_modules).get(command.cwd)
        if modules is not None:
            target = self.workspace_root / command.cwd / "node_modules"
            if target.exists() or target.is_symlink():
                raise VerificationEngineError(
                    "Project dependency target is unsafe.",
                    code="runner_pack_target_unsafe",
                )
            modules_root = _resolve_pack_path(pack_root, modules)
            target.mkdir()
            for dependency in sorted(modules_root.iterdir()):
                if dependency.name in {".cache", ".tmp"}:
                    continue
                (target / dependency.name).symlink_to(
                    dependency,
                    target_is_directory=dependency.is_dir(),
                )
            (target / ".cache").mkdir(mode=0o700)
            (target / ".tmp").mkdir(mode=0o700)
            environment["NODE_PATH"] = str(target)
        return environment

    def _clear_workspace(self) -> None:
        if not self.workspace_root.exists():
            return
        if self.workspace_root.is_symlink() or not self.workspace_root.is_dir():
            raise VerificationEngineError(
                "Project command workspace is unsafe.",
                code="unsafe_workspace_root",
            )
        _make_tree_removable(self.workspace_root)
        shutil.rmtree(self.workspace_root)


class CodingVerifierEngine:
    def __init__(
        self,
        source_root: Path,
        workspace_root: Path,
        *,
        frontend_dependencies: Path | None = None,
        runner: VerificationRunner | None = None,
        limits: DraftLimits | None = None,
    ) -> None:
        self.source_root = source_root.resolve()
        self.workspace_root = workspace_root.resolve()
        if (
            self.source_root == self.workspace_root
            or self.source_root in self.workspace_root.parents
            or self.workspace_root in self.source_root.parents
        ):
            raise VerificationEngineError(
                "Verifier roots overlap.",
                code="unsafe_workspace_root",
            )
        self.frontend_dependencies = (
            frontend_dependencies.resolve()
            if frontend_dependencies is not None
            else None
        )
        if (
            self.frontend_dependencies is not None
            and not self.frontend_dependencies.is_dir()
        ):
            raise VerificationEngineError(
                "Frontend dependencies are unavailable.",
                code="frontend_dependencies_unavailable",
            )
        self.runner = runner or SubprocessVerificationRunner()
        self.limits = limits or DraftLimits()
        self.source_fingerprint = snapshot_fingerprint(self.source_root)

    async def verify(
        self,
        *,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
        on_progress: ProgressCallback | None = None,
    ) -> VerificationReport:
        if (
            not SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(expected_fingerprint)
            or expected_fingerprint != self.source_fingerprint
        ):
            raise VerificationEngineError(
                "Verifier snapshot does not match the Coding Worker.",
                code="snapshot_mismatch",
            )
        safe_paths = validate_verification_patch(
            patch,
            expected_paths=paths,
            limits=self.limits,
        )
        plan = select_verification_plan(safe_paths)
        report = initial_verification_report(revision, plan, now=time.time())
        if not plan.runnable:
            await _notify(on_progress, report)
            return report

        started_at = time.time()
        steps = tuple(VerificationStep(step_id=item) for item in plan.step_ids)
        report = VerificationReport(
            revision=revision,
            state=VerificationState.RUNNING,
            result=VerificationResult.NOT_RUN,
            steps=steps,
            started_at=started_at,
        )
        await _notify(on_progress, report)
        completed_steps: list[VerificationStep] = []
        try:
            for index, step_id in enumerate(plan.step_ids):
                running_step = VerificationStep(
                    step_id=step_id,
                    state=VerificationState.RUNNING,
                    result=VerificationResult.NOT_RUN,
                )
                pending = tuple(
                    completed_steps
                    + [running_step]
                    + [
                        VerificationStep(step_id=item)
                        for item in plan.step_ids[index + 1 :]
                    ]
                )
                report = replace(report, steps=pending)
                await _notify(on_progress, report)
                self._prepare_workspace(
                    patch,
                    restore_baseline_tests=(
                        step_id is VerificationStepId.BACKEND_BASELINE_TESTS
                    ),
                )
                try:
                    command_result = await self.runner.run(
                        step_id,
                        self.workspace_root,
                    )
                    completed_step = _step_from_command(step_id, command_result)
                except VerificationEngineError as exc:
                    details = sanitize_verification_output(str(exc))
                    completed_step = VerificationStep(
                        step_id=step_id,
                        state=VerificationState.COMPLETED,
                        result=VerificationResult.FAILED,
                        summary=(
                            "检查等待时间过长，已停止"
                            if exc.code == "command_timeout"
                            else "检查未能完成"
                        ),
                        details=details.text,
                        truncated=details.truncated,
                    )
                except Exception:
                    completed_step = VerificationStep(
                        step_id=step_id,
                        state=VerificationState.COMPLETED,
                        result=VerificationResult.FAILED,
                        summary="检查未能完成",
                        details="verification_internal_error",
                    )
                completed_steps.append(completed_step)
                report = replace(
                    report,
                    steps=tuple(
                        completed_steps
                        + [
                            VerificationStep(step_id=item)
                            for item in plan.step_ids[index + 1 :]
                        ]
                    ),
                )
                await _notify(on_progress, report)
        except asyncio.CancelledError:
            cancelled_steps = tuple(
                step
                if step.state is VerificationState.COMPLETED
                else replace(
                    step,
                    state=VerificationState.CANCELLED,
                    result=VerificationResult.NOT_RUN,
                    summary="项目验证已停止",
                )
                for step in report.steps
            )
            cancelled = VerificationReport(
                revision=revision,
                state=VerificationState.CANCELLED,
                result=VerificationResult.NOT_RUN,
                steps=cancelled_steps,
                reason="cancelled",
                started_at=started_at,
                finished_at=time.time(),
            )
            await _notify(on_progress, cancelled)
            return cancelled
        finally:
            self._clear_workspace()

        result = (
            VerificationResult.PASSED
            if all(
                step.result is VerificationResult.PASSED
                for step in completed_steps
            )
            else VerificationResult.FAILED
        )
        completed = VerificationReport(
            revision=revision,
            state=VerificationState.COMPLETED,
            result=result,
            steps=tuple(completed_steps),
            started_at=started_at,
            finished_at=time.time(),
        )
        await _notify(on_progress, completed)
        return completed

    def _prepare_workspace(
        self,
        patch: str,
        *,
        restore_baseline_tests: bool,
    ) -> None:
        self._clear_workspace()
        shutil.copytree(self.source_root, self.workspace_root, symlinks=False)
        _make_workspace_writable(self.workspace_root)
        _apply_patch(self.workspace_root, patch)
        if restore_baseline_tests:
            tests_root = self.workspace_root / "server" / "tests"
            if tests_root.exists():
                shutil.rmtree(tests_root)
            source_tests = self.source_root / "server" / "tests"
            if source_tests.is_dir():
                shutil.copytree(source_tests, tests_root, symlinks=False)
                _make_workspace_writable(tests_root)
        if self.frontend_dependencies is not None:
            node_modules = self.workspace_root / "client" / "node_modules"
            if node_modules.exists() or node_modules.is_symlink():
                raise VerificationEngineError(
                    "Frontend dependency target is unsafe.",
                    code="unsafe_frontend_dependencies",
                )
            try:
                node_modules.mkdir()
                for dependency in sorted(self.frontend_dependencies.iterdir()):
                    if dependency.name == ".tmp":
                        continue
                    (node_modules / dependency.name).symlink_to(
                        dependency,
                        target_is_directory=dependency.is_dir(),
                    )
                # TypeScript project builds write their incremental metadata here.
                # Keep only this directory writable and inside the per-run workspace;
                # the locked dependency tree remains read-only behind symlinks.
                (node_modules / ".tmp").mkdir(mode=0o700)
            except OSError as exc:
                raise VerificationEngineError(
                    "Frontend dependencies could not be prepared.",
                    code="frontend_dependencies_unavailable",
                ) from exc

    def _clear_workspace(self) -> None:
        if not self.workspace_root.exists():
            return
        if self.workspace_root.is_symlink() or not self.workspace_root.is_dir():
            raise VerificationEngineError(
                "Verifier workspace root is unsafe.",
                code="unsafe_workspace_root",
            )
        shutil.rmtree(self.workspace_root)


def _make_workspace_writable(root: Path) -> None:
    try:
        paths = [root, *root.rglob("*")]
        for path in paths:
            if path.is_symlink():
                raise VerificationEngineError(
                    "Verifier workspace contains a symbolic link.",
                    code="unsafe_workspace_root",
                )
            mode = path.stat().st_mode
            if path.is_dir():
                path.chmod(mode | stat.S_IWUSR | stat.S_IXUSR)
            elif path.is_file():
                path.chmod(mode | stat.S_IWUSR)
            else:
                raise VerificationEngineError(
                    "Verifier workspace contains an unsupported file.",
                    code="unsafe_workspace_root",
                )
    except OSError as exc:
        raise VerificationEngineError(
            "Verifier workspace could not be prepared.",
            code="workspace_unavailable",
        ) from exc


async def _run_bounded_command(
    command: FixedCommand,
    *,
    cwd: Path,
    environment: dict[str, str],
) -> CommandResult:
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command.argv,
        cwd=str(cwd),
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded_stream(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded_stream(process.stderr))
    try:
        await asyncio.wait_for(process.wait(), timeout=command.timeout_seconds)
    except TimeoutError as exc:
        await _terminate_process(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise VerificationEngineError(
            "Project command timed out.",
            code="command_timeout",
        ) from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    stdout, stdout_truncated = await stdout_task
    stderr, stderr_truncated = await stderr_task
    return CommandResult(
        exit_code=int(process.returncode or 0),
        stdout=stdout,
        stderr=stderr,
        duration_ms=round((time.monotonic() - started) * 1000),
        output_truncated=stdout_truncated or stderr_truncated,
    )


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_FORBIDDEN_PACK_NAMES = frozenset(
    {".git", ".env", "credentials.json", "opencode.json", "opencode.jsonc"}
)
_FORBIDDEN_PACK_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def _sanitize_command_result(result: CommandResult) -> str:
    raw = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )
    raw = _ANSI_ESCAPE.sub("", raw)
    raw = raw.replace("/runner-packs", "[runner-pack]")
    raw = raw.replace("/project-snapshots", "[source]")
    sanitized = sanitize_verification_output(
        raw,
        limit=MAX_COMMAND_OUTPUT_BYTES,
    ).text
    encoded = sanitized.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_COMMAND_OUTPUT_BYTES:
        return sanitized
    marker = "\n…输出已截断…\n".encode("utf-8")
    tail = encoded[-(MAX_COMMAND_OUTPUT_BYTES - len(marker)) :]
    while tail and (tail[0] & 0xC0) == 0x80:
        tail = tail[1:]
    return (marker + tail).decode("utf-8", errors="replace")


def _validate_runner_pack_tree(pack_root: Path) -> None:
    if pack_root.is_symlink() or not pack_root.is_dir():
        raise VerificationEngineError(
            "Runner pack is unavailable.",
            code="runner_pack_unavailable",
        )
    count = 0
    try:
        for path in pack_root.rglob("*"):
            count += 1
            if count > MAX_RUNNER_PACK_ENTRIES:
                raise VerificationEngineError(
                    "Runner pack contains too many entries.",
                    code="runner_pack_limit_exceeded",
                )
            relative = path.relative_to(pack_root)
            if any(
                part.casefold() in _FORBIDDEN_PACK_NAMES
                or part.casefold().startswith(".env.")
                or part.casefold().endswith(_FORBIDDEN_PACK_SUFFIXES)
                for part in relative.parts
            ):
                raise VerificationEngineError(
                    "Runner pack contains a forbidden path.",
                    code="runner_pack_unsafe",
                )
            resolved = path.resolve(strict=True)
            if resolved != pack_root and pack_root not in resolved.parents:
                raise VerificationEngineError(
                    "Runner pack symbolic link leaves the pack.",
                    code="runner_pack_unsafe",
                )
            if not (path.is_file() or path.is_dir() or path.is_symlink()):
                raise VerificationEngineError(
                    "Runner pack contains an unsupported entry.",
                    code="runner_pack_unsafe",
                )
    except VerificationEngineError:
        raise
    except (OSError, RuntimeError) as exc:
        raise VerificationEngineError(
            "Runner pack could not be inspected.",
            code="runner_pack_unsafe",
        ) from exc


def _resolve_pack_path(pack_root: Path, relative: str) -> Path:
    try:
        resolved = (pack_root / relative).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VerificationEngineError(
            "Runner pack path is unavailable.",
            code="runner_pack_invalid",
        ) from exc
    if pack_root not in resolved.parents or not resolved.is_dir():
        raise VerificationEngineError(
            "Runner pack path is unsafe.",
            code="runner_pack_unsafe",
        )
    return resolved


def _prepare_temporary_root(root: Path) -> None:
    if root.parent == root or root.name != ".mmv-tmp":
        raise VerificationEngineError(
            "Verifier temporary root is unsafe.",
            code="unsafe_temporary_root",
        )
    _clear_temporary_root(root)
    try:
        root.mkdir(mode=0o700)
    except OSError as exc:
        raise VerificationEngineError(
            "Verifier temporary root could not be prepared.",
            code="temporary_root_unavailable",
        ) from exc


def _clear_temporary_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise VerificationEngineError(
            "Verifier temporary root is unsafe.",
            code="unsafe_temporary_root",
        )
    if not root.exists():
        return
    try:
        _make_tree_removable(root)
        shutil.rmtree(root)
    except OSError as exc:
        raise VerificationEngineError(
            "Verifier temporary root could not be cleared.",
            code="temporary_cleanup_failed",
        ) from exc


def _make_tree_removable(root: Path) -> None:
    root.chmod(
        root.lstat().st_mode
        | stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
    )
    for current, directories, files in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current_path = Path(current)
        if not current_path.is_symlink():
            current_path.chmod(
                current_path.lstat().st_mode
                | stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IXUSR
            )
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                continue
            path.chmod(
                path.lstat().st_mode
                | stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IXUSR
            )
        for name in files:
            path = current_path / name
            if path.is_symlink():
                continue
            path.chmod(
                path.lstat().st_mode | stat.S_IRUSR | stat.S_IWUSR
            )


def snapshot_fingerprint(root: Path) -> str:
    try:
        return shared_snapshot_fingerprint(root)
    except PatchPolicyError as exc:
        raise VerificationEngineError(str(exc), code=exc.code) from exc


def validate_verification_patch(
    patch: str,
    *,
    expected_paths: Sequence[str],
    limits: DraftLimits | None = None,
) -> tuple[str, ...]:
    try:
        return validate_patch(
            patch,
            expected_paths=expected_paths,
            limits=limits,
        )
    except PatchPolicyError as exc:
        raise VerificationEngineError(str(exc), code=exc.code) from exc


def _apply_patch(workspace: Path, patch: str) -> None:
    commands = (
        ("git", "apply", "--check", "--whitespace=nowarn", "-"),
        ("git", "apply", "--whitespace=nowarn", "-"),
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            input=patch.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise VerificationEngineError(
                "Verification patch could not be applied.",
                code="patch_apply_failed",
            )


def _step_from_command(
    step_id: VerificationStepId,
    command: CommandResult,
) -> VerificationStep:
    passed = command.exit_code == 0
    raw_details = "\n".join(
        part for part in (command.stdout.strip(), command.stderr.strip()) if part
    )
    details = sanitize_verification_output(
        raw_details,
        limit=MAX_VERIFICATION_DETAIL_CHARS,
    )
    summary_source = (
        "检查通过"
        if passed
        else next(
            (
                line.strip()
                for line in reversed(raw_details.splitlines())
                if line.strip()
            ),
            "发现需要处理的问题",
        )
    )
    summary = sanitize_verification_output(
        summary_source,
        limit=MAX_VERIFICATION_SUMMARY_CHARS,
        keep_tail=False,
    )
    return VerificationStep(
        step_id=step_id,
        state=VerificationState.COMPLETED,
        result=(
            VerificationResult.PASSED
            if passed
            else VerificationResult.FAILED
        ),
        duration_ms=command.duration_ms,
        summary=summary.text,
        details=details.text,
        truncated=command.output_truncated or details.truncated,
    )


async def _notify(
    callback: ProgressCallback | None,
    report: VerificationReport,
) -> None:
    if callback is None:
        return
    result = callback(report)
    if inspect.isawaitable(result):
        await result


async def _read_bounded_stream(
    stream: asyncio.StreamReader,
) -> tuple[str, bool]:
    retained = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        retained.extend(chunk)
        if len(retained) > MAX_COMMAND_OUTPUT_BYTES:
            del retained[: len(retained) - MAX_COMMAND_OUTPUT_BYTES]
            truncated = True
    return retained.decode("utf-8", errors="replace"), truncated


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5)
