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
FRONTEND_DEPENDENCIES = Path("/opt/modelmirror-client-node_modules")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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


class CodingVerifierServer:
    """One-job Unix socket host for the offline verifier engine."""

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        engine: CodingVerifierEngine | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._engine = engine or CodingVerifierEngine(
            SOURCE_ROOT,
            WORKSPACE_ROOT,
            frontend_dependencies=FRONTEND_DEPENDENCIES,
        )
        self._job: _VerificationJob | None = None
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
            if self._job is job:
                self._job = None
        return {"closed": True}

    async def close(self) -> None:
        async with self._job_lock:
            job = self._job
        if job is not None:
            await self._close_job(
                {"session_id": job.session_id}
            )

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
