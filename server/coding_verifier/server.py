from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.coding_runtime.draft_workspace import DraftPolicyError
from server.coding_runtime.commands import (
    CommandContractError,
    ProjectCommandOrigin,
    normalize_agent_command,
)
from server.coding_runtime.verification import (
    VerificationReport,
    VerificationResult,
    VerificationState,
    VerificationStep,
    initial_verification_report,
    select_verification_plan,
)

from .engine import (
    CodingVerifierEngine,
    IsolatedProjectCommandExecutor,
    VerificationEngineError,
    validate_verification_patch,
)


MAX_VERIFIER_FRAME_BYTES = 2 * 1024 * 1024
MAX_VERIFICATION_DURATION_SECONDS = 600
SOCKET_PATH = Path(
    os.getenv(
        "CODING_VERIFIER_SOCKET_PATH",
        "/run/modelmirror-coding/verifier.sock",
    )
)
SOURCE_ROOT = Path("/opt/modelmirror-source")
WORKSPACE_ROOT = Path("/workspace/current")
FRONTEND_DEPENDENCIES = Path("/opt/modelmirror-client/node_modules")
PROJECT_SNAPSHOT_PATH = Path(
    os.getenv("CODING_PROJECT_SNAPSHOT_PATH", "/project-snapshots/current")
)
RUNNER_PACKS_ROOT = os.getenv("CODING_RUNNER_PACKS_ROOT", "").strip()
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
SAFE_HEAD = re.compile(r"^[a-f0-9]{40,64}$")


class VerifierProtocolError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _VerificationJob:
    session_id: str
    revision: int
    report: VerificationReport
    task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _CommandJob:
    session_id: str
    request_id: str
    task: asyncio.Task[Any]


class CodingVerifierServer:
    """One-job Unix socket host for the offline verifier engine."""

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        engine: CodingVerifierEngine | None = None,
        command_executor: IsolatedProjectCommandExecutor | None = None,
        project_snapshot_path: Path = PROJECT_SNAPSHOT_PATH,
    ) -> None:
        self._socket_path = socket_path
        self._engine = engine or CodingVerifierEngine(
            SOURCE_ROOT,
            WORKSPACE_ROOT,
            frontend_dependencies=FRONTEND_DEPENDENCIES,
        )
        self._job: _VerificationJob | None = None
        self._command_executor = command_executor or IsolatedProjectCommandExecutor(
            WORKSPACE_ROOT,
            runner_packs_root=(Path(RUNNER_PACKS_ROOT) if RUNNER_PACKS_ROOT else None),
        )
        self._project_snapshot_path = project_snapshot_path
        self._command_job: _CommandJob | None = None
        self._job_lock = asyncio.Lock()

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_VERIFIER_FRAME_BYTES:
                raise VerifierProtocolError(
                    "Verifier request is empty or too large."
                )
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VerifierProtocolError(
                    "Verifier request is invalid."
                ) from exc
            if not isinstance(request, dict):
                raise VerifierProtocolError(
                    "Verifier request must be an object."
                )
            response = await self._dispatch(request)
            await self._send(writer, {"ok": True, **response})
        except (VerifierProtocolError, VerificationEngineError) as exc:
            await self._send_error(writer, exc.code)
        except Exception:
            await self._send_error(writer, "verifier_internal_error")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "health":
            return {
                "service": "coding-verifier",
                "configured": True,
                "snapshot_fingerprint": self._engine.source_fingerprint,
                "max_duration_seconds": MAX_VERIFICATION_DURATION_SECONDS,
                "commands": True,
            }
        if action == "start":
            return await self._start(request)
        if action == "status":
            job = await self._matching_job(request)
            return {"verification": job.report.to_dict()}
        if action == "cancel":
            return await self._cancel(request)
        if action == "close":
            return await self._close_job(request)
        if action == "execute_command":
            return await self._execute_command(request)
        if action == "cancel_command":
            return await self._cancel_command(request)
        raise VerifierProtocolError(
            "Verifier action is not supported.",
            code="unsupported_action",
        )

    async def _start(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _identifier(request.get("session_id"))
        revision = _revision(request.get("revision"))
        patch = request.get("patch")
        expected_fingerprint = request.get("expected_fingerprint")
        raw_paths = request.get("paths")
        if (
            not isinstance(patch, str)
            or not isinstance(expected_fingerprint, str)
            or not isinstance(raw_paths, list)
            or not raw_paths
            or any(not isinstance(path, str) for path in raw_paths)
        ):
            raise VerifierProtocolError("Verifier start request is invalid.")
        try:
            paths = validate_verification_patch(
                patch,
                expected_paths=raw_paths,
                limits=self._engine.limits,
            )
        except DraftPolicyError as exc:
            raise VerificationEngineError(
                "Verifier patch path is invalid.",
                code="invalid_patch",
            ) from exc
        if expected_fingerprint != self._engine.source_fingerprint:
            raise VerificationEngineError(
                "Verifier source snapshot does not match.",
                code="snapshot_mismatch",
            )
        plan = select_verification_plan(paths)
        started_at = time.time()
        report = initial_verification_report(
            revision,
            plan,
            now=started_at,
        )
        if plan.runnable:
            report = VerificationReport(
                revision=revision,
                state=VerificationState.RUNNING,
                result=VerificationResult.NOT_RUN,
                steps=tuple(
                    VerificationStep(step_id=step_id)
                    for step_id in plan.step_ids
                ),
                started_at=started_at,
            )
        async with self._job_lock:
            if self._command_job is not None and not self._command_job.task.done():
                raise VerifierProtocolError(
                    "A project command is already running.",
                    code="verification_busy",
                )
            if self._job is not None and self._job.task is not None:
                if not self._job.task.done():
                    raise VerifierProtocolError(
                        "Verifier is already running.",
                        code="verification_busy",
                    )
            job = _VerificationJob(
                session_id=session_id,
                revision=revision,
                report=report,
            )
            self._job = job
            if plan.runnable:
                job.task = asyncio.create_task(
                    self._run_job(
                        job,
                        patch=patch,
                        paths=paths,
                        expected_fingerprint=expected_fingerprint,
                    )
                )
        return {"verification": job.report.to_dict()}

    async def _execute_command(self, request: dict[str, Any]) -> dict[str, Any]:
        expected_keys = {
            "action",
            "session_id",
            "request_id",
            "source",
            "patch",
            "paths",
            "command",
            "runner_pack_id",
            "max_duration_seconds",
        }
        if set(request) != expected_keys:
            raise VerifierProtocolError("Project command request is invalid.")
        session_id = _identifier(request.get("session_id"))
        request_id = _identifier(request.get("request_id"))
        patch = request.get("patch")
        paths = request.get("paths")
        max_duration = request.get("max_duration_seconds")
        runner_pack_id = request.get("runner_pack_id")
        if (
            not isinstance(patch, str)
            or not isinstance(paths, list)
            or any(not isinstance(path, str) for path in paths)
            or isinstance(max_duration, bool)
            or not isinstance(max_duration, (int, float))
            or not 0 < max_duration <= MAX_VERIFICATION_DURATION_SECONDS
            or (runner_pack_id is not None and not isinstance(runner_pack_id, str))
        ):
            raise VerifierProtocolError("Project command request is invalid.")
        command_payload = request.get("command")
        if not isinstance(command_payload, dict) or set(command_payload) != {
            "id",
            "name",
            "kind",
            "argv",
            "cwd",
            "timeout_seconds",
            "origin",
        }:
            raise VerifierProtocolError("Project command is invalid.")
        try:
            command = normalize_agent_command(
                argv=command_payload["argv"],
                cwd=command_payload["cwd"],
                purpose=command_payload["name"],
                timeout_seconds=command_payload["timeout_seconds"],
            )
        except CommandContractError as exc:
            raise VerifierProtocolError(str(exc), code=exc.code) from exc
        if (
            command.command_id != command_payload["id"]
            or command_payload["kind"] != "custom"
            or command_payload["origin"] != ProjectCommandOrigin.AGENT.value
        ):
            raise VerifierProtocolError("Project command identity is invalid.")
        source_root, fingerprint = self._resolve_project_source(request.get("source"))
        async with self._job_lock:
            if self._job is not None and self._job.task is not None and not self._job.task.done():
                raise VerifierProtocolError(
                    "Project verification is already running.",
                    code="verification_busy",
                )
            if self._command_job is not None and not self._command_job.task.done():
                raise VerifierProtocolError(
                    "Another project command is already running.",
                    code="command_busy",
                )
            task = asyncio.create_task(
                self._command_executor.execute(
                    source_root=source_root,
                    expected_fingerprint=fingerprint,
                    patch=patch,
                    paths=paths,
                    command=command,
                    runner_pack_id=runner_pack_id,
                    max_duration_seconds=float(max_duration),
                )
            )
            job = _CommandJob(session_id, request_id, task)
            self._command_job = job
        try:
            result = await task
        except asyncio.CancelledError as exc:
            raise VerificationEngineError(
                "Project command was cancelled.",
                code="command_cancelled",
            ) from exc
        finally:
            async with self._job_lock:
                if self._command_job is job:
                    self._command_job = None
        return {"command": result.to_dict()}

    async def _cancel_command(self, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"action", "session_id", "request_id"}:
            raise VerifierProtocolError("Project command cancellation is invalid.")
        session_id = _identifier(request.get("session_id"))
        request_id = _identifier(request.get("request_id"))
        async with self._job_lock:
            job = self._command_job
            if job is None or job.session_id != session_id or job.request_id != request_id:
                return {"accepted": False}
            task = job.task
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, VerificationEngineError):
                await task
        return {"accepted": True}

    def _resolve_project_source(self, value: Any) -> tuple[Path, str]:
        expected = {
            "kind",
            "lease_id",
            "project_id",
            "name",
            "branch",
            "head",
            "fingerprint",
            "file_count",
            "total_bytes",
            "hidden_files",
            "created_at",
        }
        if not isinstance(value, dict) or set(value) != expected or value.get("kind") != "local_clone":
            raise VerifierProtocolError("Project source is invalid.")
        if (
            not SAFE_IDENTIFIER.fullmatch(str(value.get("lease_id", "")))
            or not SAFE_IDENTIFIER.fullmatch(str(value.get("project_id", "")))
            or not SAFE_HEAD.fullmatch(str(value.get("head", "")))
            or not SAFE_FINGERPRINT.fullmatch(str(value.get("fingerprint", "")))
        ):
            raise VerifierProtocolError("Project source identity is invalid.")
        lease_path = self._project_snapshot_path / "lease.json"
        workspace = self._project_snapshot_path / "workspace"
        try:
            if lease_path.is_symlink() or not lease_path.is_file() or lease_path.stat().st_size > 16 * 1024:
                raise VerificationEngineError(
                    "Project snapshot is unavailable.",
                    code="snapshot_unavailable",
                )
            lease = json.loads(lease_path.read_text(encoding="utf-8", errors="strict"))
        except VerificationEngineError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise VerificationEngineError(
                "Project snapshot is unavailable.",
                code="snapshot_unavailable",
            ) from exc
        if lease != {key: item for key, item in value.items() if key != "kind"}:
            raise VerificationEngineError(
                "Project snapshot lease does not match.",
                code="snapshot_mismatch",
            )
        return workspace, value["fingerprint"]

    async def _run_job(
        self,
        job: _VerificationJob,
        *,
        patch: str,
        paths: tuple[str, ...],
        expected_fingerprint: str,
    ) -> None:
        async def on_progress(report: VerificationReport) -> None:
            if self._job is job:
                job.report = report

        engine_task = asyncio.create_task(
            self._engine.verify(
                revision=job.revision,
                patch=patch,
                paths=paths,
                expected_fingerprint=expected_fingerprint,
                on_progress=on_progress,
            )
        )
        try:
            done, _ = await asyncio.wait(
                {engine_task},
                timeout=MAX_VERIFICATION_DURATION_SECONDS,
            )
            if not done:
                engine_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await engine_task
                job.report = _failed_report(
                    job.report,
                    reason="verification_timeout",
                    summary="项目验证等待时间过长，已停止",
                )
                return
            job.report = await engine_task
        except asyncio.CancelledError:
            engine_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await engine_task
            if job.report.state is not VerificationState.CANCELLED:
                job.report = _cancelled_report(job.report)
        except Exception:
            job.report = _failed_report(job.report)

    async def _matching_job(
        self,
        request: dict[str, Any],
    ) -> _VerificationJob:
        session_id = _identifier(request.get("session_id"))
        revision = _revision(request.get("revision"))
        async with self._job_lock:
            if (
                self._job is None
                or self._job.session_id != session_id
                or self._job.revision != revision
            ):
                raise VerifierProtocolError(
                    "Verification job was not found.",
                    code="verification_not_found",
                )
            return self._job

    async def _cancel(self, request: dict[str, Any]) -> dict[str, Any]:
        job = await self._matching_job(request)
        task = job.task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if job.report.state is VerificationState.RUNNING:
            job.report = _cancelled_report(job.report)
        return {
            "accepted": True,
            "verification": job.report.to_dict(),
        }

    async def _close_job(self, request: dict[str, Any]) -> dict[str, Any]:
        session_id = _identifier(request.get("session_id"))
        async with self._job_lock:
            job = (
                self._job
                if self._job and self._job.session_id == session_id
                else None
            )
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job.task
        async with self._job_lock:
            command_job = (
                self._command_job
                if self._command_job and self._command_job.session_id == session_id
                else None
            )
        if command_job is not None and not command_job.task.done():
            command_job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, VerificationEngineError):
                await command_job.task
        async with self._job_lock:
            if self._job is job:
                self._job = None
            if self._command_job is command_job:
                self._command_job = None
        return {"closed": True}

    async def close(self) -> None:
        async with self._job_lock:
            job = self._job
            command_job = self._command_job
        session_id = (
            job.session_id
            if job is not None
            else command_job.session_id if command_job is not None else None
        )
        if session_id is not None:
            await self._close_job({"session_id": session_id})

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_VERIFIER_FRAME_BYTES + 1,
        )
        os.chmod(self._socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self.close()
            self._socket_path.unlink(missing_ok=True)

    @staticmethod
    async def _send(
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
    ) -> None:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_VERIFIER_FRAME_BYTES:
            raise VerifierProtocolError(
                "Verifier response is too large.",
                code="response_too_large",
            )
        writer.write(encoded)
        await writer.drain()

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        code: str,
    ) -> None:
        await self._send(
            writer,
            {
                "ok": False,
                "code": code,
                "error": "Project verification request failed.",
            },
        )


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_IDENTIFIER.fullmatch(value):
        raise VerifierProtocolError("Verifier identifier is invalid.")
    return value


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VerifierProtocolError("Verifier revision is invalid.")
    return value


def _cancelled_report(report: VerificationReport) -> VerificationReport:
    steps = tuple(
        step
        if step.state is VerificationState.COMPLETED
        else VerificationStep(
            step_id=step.step_id,
            state=VerificationState.CANCELLED,
            result=VerificationResult.NOT_RUN,
            summary="项目验证已停止",
        )
        for step in report.steps
    )
    return VerificationReport(
        revision=report.revision,
        state=VerificationState.CANCELLED,
        result=VerificationResult.NOT_RUN,
        steps=steps,
        reason="cancelled",
        started_at=report.started_at,
        finished_at=time.time(),
    )


def _failed_report(
    report: VerificationReport,
    *,
    reason: str = "verification_failed",
    summary: str = "检查未能完成",
) -> VerificationReport:
    return VerificationReport(
        revision=report.revision,
        state=VerificationState.COMPLETED,
        result=VerificationResult.FAILED,
        steps=tuple(
            step
            if step.state is VerificationState.COMPLETED
            else VerificationStep(
                step_id=step.step_id,
                state=VerificationState.COMPLETED,
                result=VerificationResult.FAILED,
                summary=summary,
                details="verification_internal_error",
            )
            for step in report.steps
        ),
        reason=reason,
        started_at=report.started_at,
        finished_at=time.time(),
    )


def main() -> None:
    asyncio.run(CodingVerifierServer().serve_forever())


if __name__ == "__main__":
    main()
