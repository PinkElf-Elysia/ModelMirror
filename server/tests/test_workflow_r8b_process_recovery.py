"""Separate-process crash evidence with synthetic output and no network access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def run_worker(directory, phase, mode):
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", AGENT_TASK_STORAGE_DIR=str(directory / "runtime"))
    return subprocess.run(
        [sys.executable, "-B", "-m", "server.tests.test_workflow_r8b_process_recovery",
         str(directory), phase, mode], cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )


@pytest.mark.parametrize("mode", ["completed", "input_drift", "root_budget", "unknown"])
def test_abrupt_exit_recovery_in_a_new_process(tmp_path, mode):
    first = run_worker(tmp_path, "first", mode)
    assert first.returncode == 73, first.stdout + first.stderr
    assert (tmp_path / "dispatches.txt").read_text() == "dispatched\n"
    before = json.loads((tmp_path / "before.json").read_text())
    assert before["root_calls"] == 1
    assert before["parent"] == "root"
    second = run_worker(tmp_path, "restart", mode)
    assert second.returncode == 0, second.stdout + second.stderr
    result = json.loads((tmp_path / "after.json").read_text())
    assert result["root_calls"] == 1
    assert result["parent"] == "root"
    assert result["original_child"] == before["original_child"]
    assert result["outcome"] == {
        "completed": "reused", "input_drift": "contract_rejected",
        "root_budget": "budget_rejected", "unknown": "unsettled_rejected",
    }[mode]
    assert (tmp_path / "dispatches.txt").read_text() == "dispatched\n"


def worker(directory, phase, mode):
    import asyncio
    import socket

    mp = pytest.MonkeyPatch()

    def deny_network(*args, **kwargs):
        raise AssertionError("Network is forbidden in the process recovery test")

    mp.setattr(socket.socket, "connect", deny_network)
    import server.main as main
    from server.tests.test_workflow_r8a_handoff_budget import setup
    from server.tests.test_workflow_r8a_budget_tree import budget
    from server.tests.test_workflow_r8b_entry_evidence import runtime, wire, invoke
    from server.workflow_deployments import WorkflowDeploymentStore, WorkflowDeploymentConflictError
    from server.xpert_runtime.execution_budget import execution_operation, use_execution_budget

    if phase == "first":
        fixture = runtime.__wrapped__(directory, mp)
        deployments, store, _, _, node = next(fixture)
        (directory / "node.json").write_text(node.model_dump_json(), encoding="utf-8")
    else:
        from server.model_router.repository import SQLiteRouterRepository
        from server.model_router.service import ModelRouterService

        repository = SQLiteRouterRepository.open(directory / "router", master_key=b"b" * 32)
        router = ModelRouterService(repository)
        mp.setattr(main, "get_model_router_service", lambda: router)
        store, _, _, _ = setup(directory, mp)
        deployments = WorkflowDeploymentStore(directory / "deployments")
        wire(mp, deployments, store)
        mp.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
        node = main.WorkflowNodePayload.model_validate_json((directory / "node.json").read_text(encoding="utf-8"))

    def evidence():
        if phase == "restart":
            original = json.loads((directory / "before.json").read_text())
            child = store.require(original["original_child"])
        else:
            children = [item for item in store.list_items() if item.task_id != "root"]
            assert len(children) == 1
            child = children[0]
        return {"original_child": child.task_id,
                "parent": child.execution_budget_parent["parent_task_id"],
                "root_calls": store.require("root").execution_budget["usage"]["model_calls"]}

    async def stream(*args, **kwargs):
        async with execution_operation("model_call"):
            with (directory / "dispatches.txt").open("a", encoding="utf-8") as handle:
                handle.write("dispatched\n")
                handle.flush()
                os.fsync(handle.fileno())
            if phase == "first" and mode == "unknown":
                (directory / "before.json").write_text(json.dumps(evidence()), encoding="utf-8")
                os._exit(73)
            yield "synthetic result"

    mp.setattr(main, "stream_workflow_llm_text", stream)

    async def execute():
        if phase == "first":
            await invoke(node)
            (directory / "before.json").write_text(json.dumps(evidence()), encoding="utf-8")
            os._exit(73)
        if mode == "completed":
            result = await invoke(node)
            assert result["status"] == "completed"
            outcome = "reused"
        elif mode == "input_drift":
            node.data["inputBindings"]["message"]["value"] = "different input"
            with pytest.raises(WorkflowDeploymentConflictError):
                await invoke(node)
            outcome = "contract_rejected"
        elif mode == "root_budget":
            claimed = store.claim("root", worker_id="restart")
            node.id = "next_item"
            with use_execution_budget(budget(store, "root", claimed.lease_token)):
                with pytest.raises((ValueError, RuntimeError), match="model-call budget"):
                    await invoke(node)
            outcome = "budget_rejected"
        else:
            with pytest.raises((ValueError, RuntimeError), match="SUBWORKFLOW_EXECUTION_UNSETTLED"):
                await invoke(node)
            outcome = "unsettled_rejected"
        (directory / "after.json").write_text(json.dumps({**evidence(), "outcome": outcome}), encoding="utf-8")

    asyncio.run(execute())


if __name__ == "__main__":
    worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
