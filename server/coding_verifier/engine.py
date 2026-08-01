from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from server.coding_runtime.draft_workspace import (
    DraftLimits,
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
BACKEND_TIMEOUT_SECONDS = 300
FRONTEND_TIMEOUT_SECONDS = 240
class VerificationEngineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "verification_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FixedCommand:
    argv: tuple[str, ...]
    timeout_seconds: int


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
