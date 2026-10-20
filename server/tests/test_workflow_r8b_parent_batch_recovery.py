"""Real parent Runner crash probes, with synthetic model output and no network."""
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
    env.update(PYTHONDONTWRITEBYTECODE="1", AGENT_TASK_STORAGE_DIR=str(directory / "runtime"),
               MODEL_ROUTER_STORAGE_DIR=str(directory / "default-router"))
    return subprocess.run(
        [sys.executable, "-B", "-m", "server.tests.test_workflow_r8b_parent_batch_recovery",
         str(directory), phase, mode], cwd=ROOT, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=90,
    )


@pytest.mark.parametrize("mode", ["between_items", "unknown_second", "cancel_between", "after_batch"])
def test_parent_batch_recovery_in_fresh_process(tmp_path, mode):
    first = run_worker(tmp_path, "first", mode)
    assert first.returncode == (0 if mode == "cancel_between" else 73), first.stdout + first.stderr
    before = json.loads((tmp_path / "before.json").read_text())
    assert before["child_statuses"][0] == "completed"
    second = run_worker(tmp_path, "restart", mode)
    assert second.returncode == 0, second.stdout + second.stderr
    after = json.loads((tmp_path / "after.json").read_text())
    assert after["child_ids"][0] == before["child_ids"][0]
    calls = (tmp_path / "dispatches.txt").read_text().splitlines()
    if mode == "between_items":
        assert after["status"] == "completed", after
        assert after["indexes"] == [0, 1, 2]
        assert calls == ["0", "1", "2"]
        assert after["root_calls"] == 3
    elif mode == "unknown_second":
        assert after["status"] != "completed", after
        assert calls == ["0", "1"]
        assert after["root_calls"] == 2
        assert len(after["child_ids"]) == 2
        assert "SUBWORKFLOW_EXECUTION_UNSETTLED" in after["error"], after
    elif mode == "cancel_between":
        assert after["status"] == "cancelled"
        assert calls == ["0"]
        assert after["root_calls"] == 1
        assert len(after["child_ids"]) == 1
    else:
        assert before["continuation_keys"] == []
        assert "WORKFLOW_CONTINUATION_INVALID" in after["error"]
        assert after["status"] != "completed"
        assert calls == ["0", "1", "2"]


@pytest.mark.parametrize("fault", ["cancelled", "completed", "takeover", "expired", "missing_token"])
def test_running_checkpoint_rejects_invalid_owner(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    import server.xpert_runtime.execution_store as store_module
    from server.xpert_runtime.execution_store import WorkflowExecutionStore, WorkflowExecutionConflictError

    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="parent", run_id="run", run_type="workflow", inputs={},
                 workflow={"nodes": [{"id": "batch"}]})
    owner = store.claim("parent", worker_id="owner")
    token = owner.lease_token
    if fault == "cancelled":
        store.cancel("parent")
    elif fault == "completed":
        store.complete("parent", result="合成结果", expected_lease_token=token)
    elif fault == "takeover":
        store.claim("parent", worker_id="replacement", now=owner.lease_expires_at + 1)
    elif fault == "expired":
        monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: owner.lease_expires_at + 1))
    else:
        token = None
    before = store.snapshot_path.read_bytes()
    for continuation in ({"queue": ["batch"]}, None):
        with pytest.raises(WorkflowExecutionConflictError):
            store.checkpoint_running("parent", continuation=continuation, expected_lease_token=token)
        assert store.snapshot_path.read_bytes() == before


def test_running_checkpoint_failed_commit_does_not_leak_in_memory(tmp_path, monkeypatch):
    from server.xpert_runtime.execution_store import WorkflowExecutionStore, WorkflowExecutionStorageError

    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="parent", run_id="run", run_type="workflow", inputs={},
                 workflow={"nodes": [{"id": "batch"}]})
    owner = store.claim("parent", worker_id="owner")
    before = store.snapshot_path.read_bytes()

    def fail_write(*args):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(store, "_write_fsynced", fail_write)
    with pytest.raises(WorkflowExecutionStorageError):
        store.checkpoint_running("parent", continuation={"queue": ["batch"]},
                                 expected_lease_token=owner.lease_token)
    assert store.snapshot_path.read_bytes() == before
    assert store._items["parent"].continuation == {}
    assert not store.available


def worker(directory, phase, mode):
    import asyncio
    import socket

    mp = pytest.MonkeyPatch()

    def deny_network(*args, **kwargs):
        raise AssertionError("Network is forbidden in batch recovery probes")

    mp.setattr(socket.socket, "connect", deny_network)
    import server.main as main
    from server.model_router.repository import SQLiteRouterRepository
    from server.model_router.service import ModelRouterService
    from server.tests.test_workflow_iteration_v2 import batch_caller
    from server.tests.test_workflow_r8a_budget_journal import create, initialize
    from server.tests.test_workflow_r8a_handoff_budget import setup
    from server.tests.test_workflow_r8b_entry_evidence import publish, wire
    from server.tests.test_workflow_subworkflows import model_callable_workflow
    from server.workflow_deployments import WorkflowDeploymentStore
    from server.xpert_runtime.execution_budget import execution_operation
    from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
    from server.workflow_native.control_data import WorkflowTerminationError

    repository = SQLiteRouterRepository.open(directory / "router", master_key=b"b" * 32)
    mp.setattr(main, "get_model_router_service", lambda: ModelRouterService(repository))
    store, _, _, _ = setup(directory, mp)
    deployments = WorkflowDeploymentStore(directory / "deployments")
    wire(mp, deployments, store)
    mp.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    if phase == "first":
        create(store, "root")
        initialize(store)
        store.authorize_execution_budget_child("root", "batch-parent")
        release = publish(deployments, model_callable_workflow())
        graph = batch_caller(release.project_id)
        graph["title"] = "顺序子项恢复合成探针"
        graph["nodes"][1]["data"]["inputBindings"] = {"message": {"source": "item"}}
        (directory / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    else:
        graph = json.loads((directory / "graph.json").read_text())

    def evidence(error=""):
        parent = store.require("parent")
        children = sorted(deployments.list_executions(graph["nodes"][1]["data"]["targetProjectId"]),
                          key=lambda item: item.batch_index)
        result = json.loads(parent.result) if parent.status == "completed" else []
        return {"status": parent.status, "error": error or parent.error or "",
                "child_ids": [item.task_id for item in children],
                "child_statuses": [item.status for item in children],
                "indexes": [item["index"] for item in result],
                "continuation_keys": sorted(parent.continuation),
                "root_calls": store.require("root").execution_budget["usage"]["model_calls"]}

    async def stream(*args, **kwargs):
        async with execution_operation("model_call"):
            path = directory / "dispatches.txt"
            count = len(path.read_text().splitlines()) if path.exists() else 0
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{count}\n")
                handle.flush()
                os.fsync(handle.fileno())
            if phase == "first" and mode == "unknown_second" and count == 1:
                (directory / "before.json").write_text(json.dumps(evidence()), encoding="utf-8")
                os._exit(73)
            yield f"合成结果 {count}"

    mp.setattr(main, "stream_workflow_llm_text", stream)

    async def execute():
        if phase == "restart" and mode == "cancel_between":
            assert store.require("parent").status == "cancelled"
            with pytest.raises(WorkflowExecutionConflictError):
                store.claim("parent", worker_id="restart")
            (directory / "after.json").write_text(json.dumps(evidence()), encoding="utf-8")
            return
        resume = store.claim("parent", worker_id="restart") if phase == "restart" else None
        try:
            response = await main._run_workflow_response(
                main.WorkflowRunRequest(workflow=graph, inputs={"items": ["甲", "乙", "丙"], "prefix": "合成"}),
                None, runtime_task_id="parent", resume_execution=resume,
                runtime_budget_delegation=("root", "batch-parent"),
            )
        except WorkflowTerminationError as exc:
            if phase != "restart" or mode != "after_batch":
                raise
            (directory / "after.json").write_text(json.dumps(evidence(str(exc))), encoding="utf-8")
            return
        error = ""
        async for frame in response.body_iterator:
            text = frame.decode() if isinstance(frame, bytes) else frame
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                if (phase == "first" and mode == "after_batch"
                        and event.get("event") == "node_start" and event.get("node_id") == "output"):
                    (directory / "before.json").write_text(json.dumps(evidence()), encoding="utf-8")
                    os._exit(73)
                if event.get("event") == "workflow_error":
                    error = str(event)
                if (phase == "first" and event.get("node_id") == "batch"
                        and event.get("output") == "completed 1/3"):
                    if mode == "between_items":
                        (directory / "before.json").write_text(json.dumps(evidence()), encoding="utf-8")
                        os._exit(73)
                    if mode == "cancel_between":
                        store.cancel("parent")
        (directory / ("before.json" if phase == "first" else "after.json")).write_text(
            json.dumps(evidence(error)), encoding="utf-8")

    asyncio.run(execute())
    repository.close()


if __name__ == "__main__":
    worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
