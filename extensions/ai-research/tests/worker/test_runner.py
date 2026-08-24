from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_research_worker.runner import MAX_CAPTURE_BYTES, ActiveRun, WorkerFailure, WorkerRunManager


class FakeInspectManager(WorkerRunManager):
    def __init__(self, log_root: Path, fixture_file: Path, terminal_status: str) -> None:
        super().__init__(log_root, fixture_file)
        self.terminal_status = terminal_status
        self.output_file = Path("/tmp") / f"ar0-{log_root.name}-detach.out"
        self.run_dir: Path | None = None

    async def _command(self, *argv: str, timeout: float):
        args = list(argv)
        if args[1:2] == ["--version"]:
            return 0, "0.3.260\n", ""
        if "--detach" in args:
            log_dir = Path(args[args.index("--log-dir") + 1]).parent
            self.run_dir = log_dir
            fake_eval = log_dir / "logs" / "fake.eval"
            fake_eval.parent.mkdir(parents=True, exist_ok=True)
            fake_eval.write_text("fixture", encoding="utf-8")
            done = {
                "event": "done",
                "run_id": "inspect-run",
                "logs": [
                    {
                        "task": "fixture",
                        "task_id": "task-1",
                        "status": self.terminal_status,
                        "location": str(fake_eval),
                    }
                ],
            }
            self.output_file.write_text(json.dumps(done) + "\n", encoding="utf-8")
            launch = {
                "event": "launch",
                "run_id": "inspect-run",
                "output_file": str(self.output_file),
            }
            return 0, json.dumps(launch) + "\n", ""
        if args[1:4] == ["ctl", "task", "list"]:
            return 0, json.dumps({"tasks": [{"run_id": "inspect-run", "task_id": "task-1"}]}), ""
        if args[1:3] == ["log", "dump"]:
            status = self.terminal_status
            payload = {
                "version": 2,
                "status": status,
                "eval": {"task": "fixture", "model": "mockllm/model", "task_args": {}},
                "plan": {"steps": []},
                "error": {"type": "RuntimeError", "message": "fixture error"}
                if status == "error"
                else None,
            }
            return 0, json.dumps(payload), ""
        if args[1:3] == ["log", "export-config"]:
            Path(args[args.index("--output") + 1]).write_text("fixture: true\n", encoding="utf-8")
            return 0, "", ""
        if "--run-config" in args:
            replay_log = Path(args[args.index("--log-dir") + 1]) / "replay.eval"
            replay_log.parent.mkdir(parents=True, exist_ok=True)
            replay_log.write_text("fixture", encoding="utf-8")
            return (
                0,
                json.dumps(
                    {
                        "event": "done",
                        "logs": [{"status": "success", "location": str(replay_log)}],
                    }
                )
                + "\n",
                "",
            )
        raise AssertionError(f"unexpected fake command: {args}")


async def wait_terminal(manager: WorkerRunManager, run_id: str) -> dict:
    for _ in range(100):
        result = await manager.status(run_id)
        if result["phase"] == "terminal":
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("fake run did not terminate")


@pytest.mark.parametrize(
    ("inspect_status", "expected_outcome"),
    [("success", "success"), ("error", "task_error")],
)
def test_exit_zero_never_overrides_eval_log(
    tmp_path: Path, inspect_status: str, expected_outcome: str
) -> None:
    async def scenario() -> None:
        fixture = tmp_path / "fixture.py"
        fixture.write_text("# fixture", encoding="utf-8")
        manager = FakeInspectManager(tmp_path / "logs", fixture, inspect_status)
        case_id = "success" if inspect_status == "success" else "task_error"
        await manager.start("ar0_test", case_id)
        result = await wait_terminal(manager, "ar0_test")
        assert result["outcome"] == expected_outcome
        assert result["inspectStatus"] == inspect_status
        if inspect_status == "success":
            assert result["replayVerified"] is True
        manager.output_file.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_health_caches_the_pinned_inspect_version(tmp_path: Path) -> None:
    class HealthManager(FakeInspectManager):
        version_calls = 0

        async def _command(self, *argv: str, timeout: float):
            if list(argv)[1:2] == ["--version"]:
                self.version_calls += 1
            return await super()._command(*argv, timeout=timeout)

    fixture = tmp_path / "fixture.py"
    fixture.write_text("# fixture", encoding="utf-8")
    manager = HealthManager(tmp_path / "logs", fixture, "success")
    first = asyncio.run(manager.health())
    second = asyncio.run(manager.health())
    assert first["status"] == second["status"] == "ready"
    assert manager.version_calls == 1


def test_interrupted_marker_recovers_as_infrastructure_error(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    run_dir = log_root / "ar0_interrupted"
    run_dir.mkdir(parents=True)
    (run_dir / "started.json").write_text(
        json.dumps({"runId": "ar0_interrupted", "caseId": "success"}), encoding="utf-8"
    )
    fixture = tmp_path / "fixture.py"
    fixture.write_text("# fixture", encoding="utf-8")
    manager = WorkerRunManager(log_root, fixture)
    result = asyncio.run(manager.status("ar0_interrupted"))
    assert result["outcome"] == "infrastructure_error"
    assert result["errorType"] == "WorkerRestarted"


def test_cancel_uses_public_mutation_envelope(tmp_path: Path) -> None:
    class CancelManager(WorkerRunManager):
        async def _command(self, *argv: str, timeout: float):
            assert list(argv[:4]) == ["inspect", "ctl", "task", "cancel"]
            assert argv[-3:] == ("--action", "cancel", "--json")
            assert timeout == 60
            return 0, json.dumps({"target": {"task_id": "task-1"}, "applied": True}), ""

    fixture = tmp_path / "fixture.py"
    fixture.write_text("# fixture", encoding="utf-8")
    manager = CancelManager(tmp_path / "logs", fixture)
    active = ActiveRun(run_id="ar0_cancel", case_id="long_running_cancel", task_id="task-1")
    manager._run_dir(active.run_id).mkdir(parents=True)

    assert asyncio.run(manager._cancel_task(active)) is True
    mutation = json.loads(
        (manager._run_dir(active.run_id) / "cancel-mutation.json").read_text(encoding="utf-8")
    )
    assert mutation["payload"]["applied"] is True


def test_cancel_acceptance_repairs_terminal_race_without_hiding_raw_error(tmp_path: Path) -> None:
    class RaceManager(WorkerRunManager):
        async def _cancel_task(self, active: ActiveRun) -> bool:
            terminal = {
                "runId": "ar0_race",
                "caseId": "long_running_cancel",
                "phase": "terminal",
                "outcome": "task_error",
                "inspectStatus": "error",
                "cancelRequested": True,
                "cancelApplied": False,
                "errorType": "TerminateTaskError",
                "errorMessage": "Task cancelled by user (abort)",
                "replayVerified": False,
                "artifacts": {},
            }
            run_dir = self._run_dir("ar0_race")
            run_dir.mkdir(parents=True)
            self._write_json(run_dir / "result.json", terminal)
            return True

    fixture = tmp_path / "fixture.py"
    fixture.write_text("# fixture", encoding="utf-8")
    manager = RaceManager(tmp_path / "logs", fixture)
    manager._active = ActiveRun(
        run_id="ar0_race", case_id="long_running_cancel", task_id="task-1"
    )

    result = asyncio.run(manager.cancel("ar0_race"))

    assert result["outcome"] == "cancelled"
    assert result["inspectStatus"] == "error"
    assert result["cancelRequested"] is True
    assert result["cancelApplied"] is True
    assert result["errorType"] == "TerminateTaskError"


def test_cancel_requested_before_task_discovery_is_applied_by_execute_loop(tmp_path: Path) -> None:
    class DelayedTaskManager(WorkerRunManager):
        def __init__(self, log_root: Path, fixture_file: Path) -> None:
            super().__init__(log_root, fixture_file)
            self.output_file = Path("/tmp/ar0-delayed-cancel.out")
            self.eval_file = log_root / "ar0_delayed" / "logs" / "cancel.eval"

        async def _command(self, *argv: str, timeout: float):
            args = list(argv)
            if "--detach" in args:
                self.output_file.unlink(missing_ok=True)
                return 0, json.dumps(
                    {
                        "event": "launch",
                        "run_id": "inspect-delayed",
                        "output_file": str(self.output_file),
                    }
                ), ""
            if args[1:4] == ["ctl", "task", "list"]:
                return 0, json.dumps(
                    {"tasks": [{"run_id": "inspect-delayed", "task_id": "task-delayed"}]}
                ), ""
            if args[1:4] == ["ctl", "task", "cancel"]:
                self.eval_file.parent.mkdir(parents=True, exist_ok=True)
                self.eval_file.write_text("fixture", encoding="utf-8")
                self.output_file.write_text(
                    json.dumps(
                        {
                            "event": "done",
                            "logs": [
                                {
                                    "task_id": "task-delayed",
                                    "status": "error",
                                    "location": str(self.eval_file),
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return 0, json.dumps({"applied": True}), ""
            if args[1:3] == ["log", "dump"]:
                return 0, json.dumps(
                    {
                        "version": 2,
                        "status": "error",
                        "error": {
                            "exc_type": "TerminateTaskError",
                            "message": "Task cancelled by user (abort)",
                        },
                    }
                ), ""
            raise AssertionError(f"unexpected fake command: {args}")

    async def scenario() -> None:
        fixture = tmp_path / "fixture.py"
        fixture.write_text("# fixture", encoding="utf-8")
        manager = DelayedTaskManager(tmp_path / "logs", fixture)
        await manager.start("ar0_delayed", "long_running_cancel")
        await manager.cancel("ar0_delayed")
        result = await wait_terminal(manager, "ar0_delayed")
        assert result["outcome"] == "cancelled"
        assert result["inspectStatus"] == "error"
        assert result["cancelApplied"] is True
        assert result["errorType"] == "TerminateTaskError"
        manager.output_file.unlink(missing_ok=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "dump_text",
    [
        "{",
        json.dumps({"version": 2}),
        json.dumps({"version": 999, "status": "error"}),
    ],
)
def test_malformed_missing_and_unknown_eval_logs_fail_closed(
    tmp_path: Path, dump_text: str
) -> None:
    class MutatedEvalManager(FakeInspectManager):
        async def _command(self, *argv: str, timeout: float):
            if list(argv)[1:3] == ["log", "dump"]:
                return 0, dump_text, ""
            return await super()._command(*argv, timeout=timeout)

    async def scenario() -> None:
        fixture = tmp_path / "fixture.py"
        fixture.write_text("# fixture", encoding="utf-8")
        manager = MutatedEvalManager(tmp_path / "logs", fixture, "error")
        await manager.start("ar0_mutated", "task_error")
        result = await wait_terminal(manager, "ar0_mutated")
        assert result["outcome"] == "infrastructure_error"
        assert result["inspectStatus"] is None
        manager.output_file.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_message_only_inspect_error_gets_non_inferred_category() -> None:
    error_type, message = WorkerRunManager._extract_error(
        {"error": {"message": "TerminateTaskError('Task cancelled by user (abort)')"}}
    )
    assert error_type == "InspectEvalError"
    assert message == "TerminateTaskError('Task cancelled by user (abort)')"


def test_detached_output_over_limit_fails_closed(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.out"
    oversized.write_bytes(b"x" * (MAX_CAPTURE_BYTES + 1))
    with pytest.raises(WorkerFailure, match="exceeded"):
        WorkerRunManager._bounded_read(oversized)
