from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import ALLOWED_CASES, RUN_ID_PATTERN


MAX_CAPTURE_BYTES = 1_048_576
POLL_SECONDS = 0.25
RUN_TIMEOUT_SECONDS = 600.0
FIXTURE_TASKS = {
    "success": "fixture_success",
    "task_error": "fixture_task_error",
    "long_running_cancel": "fixture_long_running_cancel",
}


class WorkerFailure(RuntimeError):
    pass


@dataclass
class ActiveRun:
    run_id: str
    case_id: str
    phase: str = "running"
    task_id: str | None = None
    inspect_run_id: str | None = None
    cancel_requested: bool = False
    cancel_applied: bool = False
    started_at: float = field(default_factory=time.time)


class WorkerRunManager:
    def __init__(self, log_root: Path, fixture_file: Path) -> None:
        self.log_root = log_root.resolve()
        self.fixture_file = fixture_file.resolve()
        self.log_root.mkdir(parents=True, exist_ok=True)
        self._active: ActiveRun | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._health_cache: dict[str, str] | None = None
        self._recover_interrupted()

    def _run_dir(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise WorkerFailure("invalid run id")
        candidate = (self.log_root / run_id).resolve()
        candidate.relative_to(self.log_root)
        return candidate

    def _recover_interrupted(self) -> None:
        for marker in self.log_root.glob("*/started.json"):
            result_path = marker.parent / "result.json"
            if result_path.exists():
                continue
            try:
                started = json.loads(marker.read_text(encoding="utf-8"))
                result = {
                    "runId": started["runId"],
                    "caseId": started["caseId"],
                    "phase": "terminal",
                    "outcome": "infrastructure_error",
                    "inspectStatus": None,
                    "cancelRequested": False,
                    "cancelApplied": False,
                    "errorType": "WorkerRestarted",
                    "errorMessage": "worker restarted before a terminal EvalLog was recorded",
                    "replayVerified": False,
                }
                self._write_json(result_path, result)
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue

    async def health(self) -> dict[str, Any]:
        if self._health_cache is None:
            code, stdout, _ = await self._command("inspect", "--version", timeout=20)
            self._health_cache = {
                "status": "ready" if code == 0 and "0.3.260" in stdout else "not_ready",
                "inspectVersion": stdout.strip(),
            }
        return {
            **self._health_cache,
            "busy": self._active is not None,
        }

    async def start(self, run_id: str, case_id: str) -> dict[str, Any]:
        if case_id not in ALLOWED_CASES:
            raise WorkerFailure("unsupported fixture case")
        async with self._lock:
            existing = self._load_result(run_id)
            if existing is not None:
                return existing
            if self._active is not None:
                raise WorkerFailure("worker is busy")
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            active = ActiveRun(run_id=run_id, case_id=case_id)
            self._active = active
            self._write_json(
                run_dir / "started.json",
                {"runId": run_id, "caseId": case_id, "startedAt": active.started_at},
            )
            self._task = asyncio.create_task(self._execute(active), name=f"inspect-{run_id}")
            return self._snapshot(active)

    async def status(self, run_id: str) -> dict[str, Any]:
        if self._active is not None and self._active.run_id == run_id:
            return self._snapshot(self._active)
        result = self._load_result(run_id)
        if result is None:
            raise WorkerFailure("run not found")
        return result

    async def cancel(self, run_id: str) -> dict[str, Any]:
        async with self._lock:
            if self._active is None or self._active.run_id != run_id:
                result = self._load_result(run_id)
                if result is None:
                    raise WorkerFailure("run not found")
                return result
            active = self._active
            active.cancel_requested = True
            if active.task_id and not active.cancel_applied:
                terminal = await self._apply_cancel(active)
                if terminal is not None:
                    return terminal
            return self._snapshot(active)

    def _snapshot(self, active: ActiveRun) -> dict[str, Any]:
        return {
            "runId": active.run_id,
            "caseId": active.case_id,
            "phase": active.phase,
            "outcome": None,
            "inspectStatus": "started",
            "cancelRequested": active.cancel_requested,
            "cancelApplied": active.cancel_applied,
            "taskId": active.task_id,
        }

    async def _execute(self, active: ActiveRun) -> None:
        run_dir = self._run_dir(active.run_id)
        try:
            task_name = FIXTURE_TASKS[active.case_id]
            code, stdout, stderr = await self._command(
                "inspect",
                "eval",
                f"{self.fixture_file}@{task_name}",
                "--model",
                "mockllm/model",
                "--log-dir",
                str(run_dir / "logs"),
                "--json",
                "--detach",
                timeout=60,
            )
            self._write_text(run_dir / "launch.stdout", stdout)
            self._write_text(run_dir / "launch.stderr", stderr)
            launch = self._find_event(stdout, "launch")
            if code != 0 or launch is None or not isinstance(launch.get("output_file"), str):
                raise WorkerFailure("Inspect detach did not produce a valid launch event")
            active.inspect_run_id = str(launch.get("run_id") or "")
            output_file = Path(launch["output_file"]).resolve()
            Path("/tmp").resolve()
            output_file.relative_to(Path("/tmp").resolve())
            self._write_json(run_dir / "launch.json", launch)

            deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
            done: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                if active.task_id is None:
                    active.task_id = await self._discover_task_id(active.inspect_run_id)
                if active.cancel_requested and active.task_id and not active.cancel_applied:
                    async with self._lock:
                        await self._apply_cancel(active)
                if output_file.exists():
                    detached_output = self._bounded_read(output_file)
                    done = self._find_event(detached_output, "done")
                    if done is not None:
                        self._write_text(run_dir / "detached.stdout", detached_output)
                        break
                await asyncio.sleep(POLL_SECONDS)
            if done is None:
                raise WorkerFailure("Inspect run timed out without a done event")

            log_entry = self._single_log_entry(done)
            location = Path(str(log_entry["location"])).resolve()
            location.relative_to(run_dir)
            _, dump_text, dump_stderr = await self._command(
                "inspect", "log", "dump", str(location), timeout=60
            )
            self._write_text(run_dir / "dump.stderr", dump_stderr)
            try:
                eval_log = json.loads(dump_text)
            except json.JSONDecodeError as exc:
                raise WorkerFailure("Inspect log dump was not valid JSON") from exc
            if eval_log.get("version") != 2:
                raise WorkerFailure("Inspect log has an unsupported schema version")
            inspect_status = eval_log.get("status")
            if inspect_status not in {"success", "error", "cancelled"}:
                raise WorkerFailure("Inspect log has an unsupported terminal status")
            self._write_json(run_dir / "eval-log.json", eval_log)

            error_type, error_message = self._extract_error(eval_log)
            if active.cancel_applied:
                outcome = "cancelled"
            elif inspect_status == "success":
                outcome = "success"
            else:
                outcome = "task_error"
            replay_verified = False
            if active.case_id == "success" and outcome == "success":
                replay_verified = await self._verify_replay(location, eval_log, run_dir)
                if not replay_verified:
                    raise WorkerFailure("Inspect config replay did not preserve semantic fields")

            result = {
                "runId": active.run_id,
                "caseId": active.case_id,
                "phase": "terminal",
                "outcome": outcome,
                "inspectStatus": inspect_status,
                "cancelRequested": active.cancel_requested,
                "cancelApplied": active.cancel_applied,
                "taskId": active.task_id or log_entry.get("task_id"),
                "errorType": error_type,
                "errorMessage": error_message,
                "replayVerified": replay_verified,
                "artifacts": self._artifact_manifest(run_dir),
            }
            self._write_json(run_dir / "result.json", result)
        except Exception as exc:
            result = {
                "runId": active.run_id,
                "caseId": active.case_id,
                "phase": "terminal",
                "outcome": "cancelled" if active.cancel_applied else "infrastructure_error",
                "inspectStatus": None,
                "cancelRequested": active.cancel_requested,
                "cancelApplied": active.cancel_applied,
                "taskId": active.task_id,
                "errorType": type(exc).__name__,
                "errorMessage": str(exc)[:1000],
                "replayVerified": False,
                "artifacts": self._artifact_manifest(run_dir),
            }
            self._write_json(run_dir / "result.json", result)
        finally:
            async with self._lock:
                if self._active is active:
                    self._active = None
                    self._task = None

    async def _verify_replay(self, location: Path, original: dict[str, Any], run_dir: Path) -> bool:
        config_path = run_dir / "run-config.yaml"
        code, _, stderr = await self._command(
            "inspect", "log", "export-config", str(location), "--output", str(config_path), timeout=60
        )
        if code != 0:
            self._write_text(run_dir / "replay.stderr", stderr)
            return False
        code, stdout, stderr = await self._command(
            "inspect",
            "eval",
            "--run-config",
            str(config_path),
            "--log-dir",
            str(run_dir / "replay-logs"),
            "--json",
            timeout=120,
        )
        self._write_text(run_dir / "replay.stdout", stdout)
        self._write_text(run_dir / "replay.stderr", stderr)
        done = self._find_event(stdout, "done")
        if code != 0 or done is None:
            return False
        entry = self._single_log_entry(done)
        replay_location = Path(str(entry["location"])).resolve()
        replay_location.relative_to(run_dir)
        _, dump_text, _ = await self._command("inspect", "log", "dump", str(replay_location), timeout=60)
        try:
            replay = json.loads(dump_text)
        except json.JSONDecodeError:
            return False
        self._write_json(run_dir / "replay-eval-log.json", replay)
        keys = ("task", "model", "task_args")
        original_eval = original.get("eval") or {}
        replay_eval = replay.get("eval") or {}
        return (
            original.get("status") == replay.get("status") == "success"
            and all(original_eval.get(key) == replay_eval.get(key) for key in keys)
            and original.get("plan") == replay.get("plan")
        )

    async def _discover_task_id(self, inspect_run_id: str | None) -> str | None:
        _, stdout, _ = await self._command("inspect", "ctl", "task", "list", "--json", timeout=20)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        for item in self._walk_dicts(payload):
            task_id = item.get("task_id") or item.get("taskId")
            run_id = item.get("run_id") or item.get("runId")
            if isinstance(task_id, str) and (not inspect_run_id or run_id in {None, inspect_run_id}):
                return task_id
        return None

    async def _cancel_task(self, active: ActiveRun) -> bool:
        code, stdout, stderr = await self._command(
            "inspect",
            "ctl",
            "task",
            "cancel",
            active.task_id or "",
            "--action",
            "cancel",
            "--json",
            timeout=60,
        )
        run_dir = self._run_dir(active.run_id)
        self._write_text(run_dir / "cancel.stdout", stdout)
        self._write_text(run_dir / "cancel.stderr", stderr)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            self._write_json(run_dir / "cancel-mutation.json", {"exitCode": code, "payload": None})
            return False
        self._write_json(
            run_dir / "cancel-mutation.json", {"exitCode": code, "payload": payload}
        )
        return code == 0 and any(
            item.get("applied") is True for item in self._walk_dicts(payload)
        )

    async def _apply_cancel(self, active: ActiveRun) -> dict[str, Any] | None:
        applied = await self._cancel_task(active)
        active.cancel_applied = active.cancel_applied or applied
        if not active.cancel_applied:
            return None
        terminal = self._load_result(active.run_id)
        if terminal is None:
            return None
        terminal["cancelRequested"] = True
        terminal["cancelApplied"] = True
        if terminal.get("inspectStatus") in {"error", "cancelled"}:
            terminal["outcome"] = "cancelled"
        terminal["artifacts"] = self._artifact_manifest(self._run_dir(active.run_id))
        self._write_json(self._run_dir(active.run_id) / "result.json", terminal)
        return terminal

    async def _command(self, *argv: str, timeout: float) -> tuple[int, str, str]:
        env = {
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONUTF8": "1",
        }
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise WorkerFailure(f"command timed out: {argv[0]}")
        return (
            process.returncode,
            stdout[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace"),
            stderr[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _find_event(text: str, event: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = None
        for line in text.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("event") == event:
                found = value
        return found

    @staticmethod
    def _single_log_entry(done: dict[str, Any]) -> dict[str, Any]:
        logs = done.get("logs")
        if not isinstance(logs, list) or len(logs) != 1 or not isinstance(logs[0], dict):
            raise WorkerFailure("done event did not contain exactly one log")
        if not isinstance(logs[0].get("location"), str):
            raise WorkerFailure("done event log location was invalid")
        return logs[0]

    @staticmethod
    def _extract_error(eval_log: dict[str, Any]) -> tuple[str | None, str | None]:
        error = eval_log.get("error")
        if not isinstance(error, dict):
            return None, None
        error_type = error.get("type") or error.get("name") or error.get("exc_type")
        message = error.get("message")
        return (
            str(error_type)[:200] if error_type else ("InspectEvalError" if message else None),
            str(message)[:1000] if message else None,
        )

    @staticmethod
    def _walk_dicts(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from WorkerRunManager._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from WorkerRunManager._walk_dicts(child)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.write_text(value[:MAX_CAPTURE_BYTES], encoding="utf-8")

    @staticmethod
    def _bounded_read(path: Path) -> str:
        with path.open("rb") as handle:
            data = handle.read(MAX_CAPTURE_BYTES + 1)
        if len(data) > MAX_CAPTURE_BYTES:
            raise WorkerFailure("detached output exceeded the capture limit")
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _artifact_manifest(run_dir: Path) -> dict[str, dict[str, Any]]:
        manifest: dict[str, dict[str, Any]] = {}
        for path in sorted(run_dir.glob("*")):
            if not path.is_file() or path.name == "result.json":
                continue
            data = path.read_bytes()
            manifest[path.name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "sizeBytes": len(data),
            }
        return manifest

    def _load_result(self, run_id: str) -> dict[str, Any] | None:
        path = self._run_dir(run_id) / "result.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerFailure("stored worker result is invalid") from exc
        if not isinstance(value, dict) or value.get("runId") != run_id:
            raise WorkerFailure("stored worker result has the wrong identity")
        return value
