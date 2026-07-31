from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from server.coding_runtime.draft_workspace import (
    DraftLimits,
    DraftPolicyError,
    DraftWorkspace,
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
SNAPSHOT_FINGERPRINT_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SAFE_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


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
        temporary = runtime_root / "tmp"
        data_root = runtime_root / "data"
        for path in (home, temporary, data_root):
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
        stdout_task = asyncio.create_task(_read_bounded_stream(process.stdout))
        stderr_task = asyncio.create_task(_read_bounded_stream(process.stderr))
        try:
            await asyncio.wait_for(process.wait(), timeout=command.timeout_seconds)
        except TimeoutError as exc:
            await _terminate_process(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise VerificationEngineError(
                "Verification step timed out.",
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
        _apply_patch(self.workspace_root, patch)
        if restore_baseline_tests:
            tests_root = self.workspace_root / "server" / "tests"
            if tests_root.exists():
                shutil.rmtree(tests_root)
            source_tests = self.source_root / "server" / "tests"
            if source_tests.is_dir():
                shutil.copytree(source_tests, tests_root, symlinks=False)
        if self.frontend_dependencies is not None:
            node_modules = self.workspace_root / "client" / "node_modules"
            if node_modules.exists() or node_modules.is_symlink():
                raise VerificationEngineError(
                    "Frontend dependency target is unsafe.",
                    code="unsafe_frontend_dependencies",
                )
            node_modules.symlink_to(
                self.frontend_dependencies,
                target_is_directory=True,
            )

    def _clear_workspace(self) -> None:
        if not self.workspace_root.exists():
            return
        if self.workspace_root.is_symlink() or not self.workspace_root.is_dir():
            raise VerificationEngineError(
                "Verifier workspace root is unsafe.",
                code="unsafe_workspace_root",
            )
        shutil.rmtree(self.workspace_root)


def snapshot_fingerprint(root: Path) -> str:
    resolved = root.resolve()
    if not resolved.is_dir() or resolved.parent == resolved:
        raise VerificationEngineError(
            "Verifier source snapshot is unavailable.",
            code="source_snapshot_unavailable",
        )
    digest = hashlib.sha256()
    for path in sorted(resolved.rglob("*")):
        relative = path.relative_to(resolved).as_posix()
        if path.is_symlink():
            raise VerificationEngineError(
                "Verifier source snapshot contains a symlink.",
                code="source_snapshot_unsafe",
            )
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        content = path.read_bytes()
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def validate_verification_patch(
    patch: str,
    *,
    expected_paths: Sequence[str],
    limits: DraftLimits | None = None,
) -> tuple[str, ...]:
    active_limits = limits or DraftLimits()
    try:
        encoded = patch.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise VerificationEngineError(
            "Verification patch is not UTF-8.",
            code="invalid_patch",
        ) from exc
    if (
        not patch
        or len(encoded) > active_limits.max_patch_bytes
        or "\x00" in patch
        or any(
            marker in patch
            for marker in (
                "GIT binary patch",
                "Binary files ",
                "deleted file mode ",
                "rename from ",
                "rename to ",
            )
        )
    ):
        raise VerificationEngineError(
            "Verification patch is outside the allowed scope.",
            code="invalid_patch",
        )

    safe_expected = tuple(
        sorted(
            {
                DraftWorkspace.normalize_relative_path(path)
                for path in expected_paths
            }
        )
    )
    headers: list[tuple[int, str]] = []
    lines = patch.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("diff --git "):
            continue
        match = SAFE_DIFF_HEADER.fullmatch(line)
        if match is None or match.group(1) != match.group(2):
            raise VerificationEngineError(
                "Verification patch header is invalid.",
                code="invalid_patch",
            )
        try:
            path = DraftWorkspace.normalize_relative_path(match.group(1))
        except DraftPolicyError as exc:
            raise VerificationEngineError(
                "Verification patch path is invalid.",
                code="invalid_patch",
            ) from exc
        headers.append((index, path))
    paths = tuple(path for _, path in headers)
    if (
        not paths
        or len(paths) > active_limits.max_changed_files
        or len(set(paths)) != len(paths)
        or tuple(sorted(paths)) != safe_expected
    ):
        raise VerificationEngineError(
            "Verification patch paths do not match the draft.",
            code="invalid_patch",
        )

    for position, (start, path) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        section = lines[start:end]
        old_headers = [line for line in section if line.startswith("--- ")]
        new_headers = [line for line in section if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise VerificationEngineError(
                "Verification patch file headers are incomplete.",
                code="invalid_patch",
            )
        old_path = old_headers[0][4:].split("\t", maxsplit=1)[0]
        new_path = new_headers[0][4:].split("\t", maxsplit=1)[0]
        if old_path not in {"/dev/null", f"a/{path}"} or new_path != f"b/{path}":
            raise VerificationEngineError(
                "Verification patch file paths do not match.",
                code="invalid_patch",
            )
        has_hunk = any(line.startswith("@@ ") for line in section)
        is_empty_new_file = (
            "new file mode 100644" in section
            and old_path == "/dev/null"
            and not has_hunk
        )
        if not has_hunk and not is_empty_new_file:
            raise VerificationEngineError(
                "Verification patch does not contain a change.",
                code="invalid_patch",
            )
    return tuple(sorted(paths))


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
