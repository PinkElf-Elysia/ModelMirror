"""Conflicting stream outcomes must never become a successful parent result."""
import json
from itertools import combinations
from types import SimpleNamespace

import pytest
from starlette.responses import StreamingResponse

import server.main as main
from server.xpert_runtime.workflow_outcomes import (
    WAIT_EVENTS, WorkflowOutcomeError, resolve_workflow_outcome,
)


EVENTS = (
    "workflow_end", "workflow_cancelled", "runtime_approval_pending",
    "client_tool_waiting", "timer_waiting", "node_retry_scheduled",
    "agent_handoff_waiting",
)


def response_for(events):
    async def frames():
        for event in events:
            yield "data: " + json.dumps(event) + "\n\n"
    return StreamingResponse(frames())


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", list(combinations(EVENTS, 2)))
@pytest.mark.parametrize("reverse", [False, True])
async def test_conflicting_stream_outcomes_are_rejected(pair, reverse):
    names = list(reversed(pair)) if reverse else pair
    events = [{"event": name, "task_id": "task", "run_id": "run"} for name in names]
    with pytest.raises(RuntimeError):
        await main.consume_workflow_stream(response_for(events))


@pytest.mark.asyncio
async def test_same_kind_with_different_identity_is_rejected():
    with pytest.raises(RuntimeError):
        await main.consume_workflow_stream(response_for([
            {"event": "workflow_end", "task_id": "a", "run_id": "run"},
            {"event": "workflow_end", "task_id": "b", "run_id": "run"},
        ]))


@pytest.mark.asyncio
async def test_identical_receipt_and_telemetry_are_compatible():
    end = {"event": "workflow_end", "task_id": "task", "run_id": "run"}
    assert await main.consume_workflow_stream(response_for([
        {"event": "node_end", "node_id": "node"}, end, end,
    ])) == end


@pytest.mark.parametrize("name", EVENTS)
@pytest.mark.parametrize("tamper", [None, "task_id", "run_id", "status", "invalidated"])
def test_outcome_requires_matching_durable_execution(name, tamper):
    status = "waiting" if name in WAIT_EVENTS else (
        "completed" if name == "workflow_end" else "cancelled"
    )
    event = {"event": name, "task_id": "task", "run_id": "run"}
    snapshot = SimpleNamespace(
        task_id="task", run_id="run", status=status, runtime_metadata={},
        wait_kind=None, wait_id=None, continuation={"queue": ["next"]}, resume_at=None,
    )
    if name in WAIT_EVENTS:
        kind, key = WAIT_EVENTS[name]
        event[key] = "wait"
        snapshot.wait_kind, snapshot.wait_id = kind, "wait"
    if tamper == "invalidated":
        snapshot.runtime_metadata["terminal_source_invalidated"] = True
    elif tamper:
        setattr(snapshot, tamper, "different")
    if tamper:
        with pytest.raises(WorkflowOutcomeError):
            resolve_workflow_outcome(event, snapshot)
    else:
        assert resolve_workflow_outcome(event, snapshot).status == status


@pytest.mark.parametrize("name", WAIT_EVENTS)
@pytest.mark.parametrize("tamper", ["wait_kind", "wait_id", "continuation"])
def test_wait_must_match_registered_continuation(name, tamper):
    kind, key = WAIT_EVENTS[name]
    snapshot = SimpleNamespace(
        task_id="task", run_id="run", status="waiting", runtime_metadata={},
        wait_kind=kind, wait_id="wait", continuation={"queue": ["next"]}, resume_at=None,
    )
    setattr(snapshot, tamper, {} if tamper == "continuation" else "wrong")
    with pytest.raises(WorkflowOutcomeError):
        resolve_workflow_outcome(
            {"event": name, "task_id": "task", "run_id": "run", key: "wait"}, snapshot,
        )
