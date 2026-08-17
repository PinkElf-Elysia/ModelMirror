"""Final-image smoke for durable Expert Team HITL execution.

This script uses the compiled Node Worker and a deterministic in-process Fake
Gateway. It performs a real v3 pause/resume chain without network access or
paid model calls.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from expert_team_agency_runtime import (
    AgencyExecutionCoordinator,
    PreparedAgencyExecution,
)
from orchestration_worker import AgencyAgentDefinition, AgencyModelResponse
from xpert_runtime import RunRegistry, RuntimeApprovalStore, WorkflowExecutionStore


WORKFLOW = {
    "name": "Docker HITL smoke",
    "steps": [
        {
            "id": "research",
            "role": "agent-alpha",
            "task": "Research {{user_input}}",
            "output": "research_output",
            "depends_on": [],
        },
        {
            "id": "audience_input",
            "type": "human_input",
            "task": "Use the research summary before requesting the audience.",
            "prompt": "Confirm the audience after reviewing {{research_output}}.",
            "output": "audience_output",
            "depends_on": ["research"],
        },
        {
            "id": "draft",
            "role": "agent-beta",
            "task": "Draft for {{audience_output}} using {{research_output}}",
            "output": "draft_output",
            "depends_on": ["audience_input"],
        },
        {
            "id": "release_gate",
            "type": "approval",
            "task": "Review {{draft_output}} before final delivery.",
            "prompt": "Approve final delivery.",
            "output": "release_gate_output",
            "depends_on": ["draft"],
        },
        {
            "id": "final",
            "role": "agent-alpha",
            "task": "Deliver {{draft_output}} after {{release_gate_output}}",
            "acceptance": "The result must be actionable and state the audience.",
            "output": "final_output",
            "depends_on": ["release_gate"],
        },
    ],
}

AGENTS = [
    AgencyAgentDefinition(
        id="agent-alpha",
        path="agent-alpha",
        name="Researcher",
        department="Research",
        description="Collects evidence.",
        system_prompt="You are a research specialist.",
    ),
    AgencyAgentDefinition(
        id="agent-beta",
        path="agent-beta",
        name="Delivery lead",
        department="Product",
        description="Creates delivery plans.",
        system_prompt="You are a product delivery specialist.",
    ),
]

PREPARED = PreparedAgencyExecution(
    workflow=WORKFLOW,
    agents=AGENTS,
    skills=[],
    sink_task_id="final",
    selected_agent_ids=["agent-alpha", "agent-beta"],
)


async def wait_for(runtime: AgencyExecutionCoordinator, task_id: str, status: str):
    for _ in range(300):
        current = runtime.get(task_id)
        if current["status"] == status:
            return current
        if current["status"] in {"failed", "cancelled", "rejected"}:
            raise AssertionError(current)
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {status}: {runtime.get(task_id)}")


async def resume(
    runtime: AgencyExecutionCoordinator,
    prepared: PreparedAgencyExecution,
    *,
    decision: str,
    replacement_text: str | None = None,
    message: str | None = None,
) -> None:
    waiting = runtime.get(runtime.store.list_items(limit=1)[0].task_id)
    interaction = waiting["pending_interaction"]
    approval = runtime.approval_store.decide(
        interaction["approval_id"],
        revision=interaction["revision"],
        decision=decision,
        operator="docker-smoke",
        replacement_text=replacement_text,
        message=message,
    )
    runtime.store.mark_ready(waiting["task_id"], approval_id=approval.approval_id)
    claimed = runtime.store.claim(waiting["task_id"], worker_id="docker-smoke")
    await runtime.resume_interaction(
        execution=claimed,
        approval=approval,
        prepared=prepared,
    )


async def main() -> None:
    gateway_calls: list[str] = []

    async def fake_gateway(request):
        system = request.messages[0].content
        gateway_calls.append(request.request_id)
        content = (
            '{"pass":true,"failed":[]}'
            if "reviewer" in system.lower() or "验收员" in system
            else f"Fake Gateway deliverable {len(gateway_calls)} for procurement leaders."
        )
        return AgencyModelResponse(
            content=content,
            usage={"input_tokens": 3, "output_tokens": 4},
        )

    with tempfile.TemporaryDirectory(prefix="modelmirror-hitl-") as storage:
        storage_dir = Path(storage)
        registry = RunRegistry()
        first = AgencyExecutionCoordinator(
            store=WorkflowExecutionStore(storage_dir),
            approval_store=RuntimeApprovalStore(storage_dir),
            run_registry=registry,
            model_runner=fake_gateway,
        )
        started = await first.start(
            goal="Create a procurement launch recommendation.",
            model_id="fake-model",
            prepared=PREPARED,
            capability_snapshot_version="smoke-v1",
            capability_snapshot_hash="smoke-hash",
            upstream_revision="e3f69fdf9da8a4630edbb8abeb116893b983b57d",
        )
        first_wait = await wait_for(first, started["task_id"], "waiting")
        assert first_wait["pending_interaction"]["kind"] == "human_input"
        assert len(gateway_calls) == 1

        # Recreate both durable stores and the coordinator to simulate a service restart.
        restarted = AgencyExecutionCoordinator(
            store=WorkflowExecutionStore(storage_dir),
            approval_store=RuntimeApprovalStore(storage_dir),
            run_registry=registry,
            model_runner=fake_gateway,
        )
        assert restarted.recover_interrupted() == 0
        assert restarted.get(started["task_id"])["status"] == "waiting"
        await resume(
            restarted,
            PREPARED,
            decision="replace",
            replacement_text="procurement leaders",
        )
        second_wait = await wait_for(restarted, started["task_id"], "waiting")
        assert second_wait["pending_interaction"]["kind"] == "approval"
        # The expert draft is verified once before the approval checkpoint.
        assert len(gateway_calls) == 3, gateway_calls

        await resume(restarted, PREPARED, decision="approve")
        completed = await wait_for(restarted, started["task_id"], "completed")
        assert completed["task_id"] == started["task_id"]
        assert completed["run_id"] == started["run_id"]
        assert completed["model_calls"] == 5
        assert len(gateway_calls) == 5
        assert "procurement leaders" in completed["final_output"]

        rejected_start = await restarted.start(
            goal="Create a second procurement launch recommendation.",
            model_id="fake-model",
            prepared=PREPARED,
            capability_snapshot_version="smoke-v1",
            capability_snapshot_hash="smoke-hash",
            upstream_revision="e3f69fdf9da8a4630edbb8abeb116893b983b57d",
        )
        await wait_for(restarted, rejected_start["task_id"], "waiting")
        await resume(
            restarted,
            PREPARED,
            decision="replace",
            replacement_text="procurement leaders",
        )
        rejected_wait = await wait_for(
            restarted, rejected_start["task_id"], "waiting"
        )
        assert rejected_wait["pending_interaction"]["kind"] == "approval"
        calls_before_reject = len(gateway_calls)
        await resume(
            restarted,
            PREPARED,
            decision="reject",
            message="Budget evidence is not confirmed.",
        )
        rejected = await wait_for(restarted, rejected_start["task_id"], "rejected")
        assert rejected["retryable"] is False
        assert len(gateway_calls) == calls_before_reject

    print("agency HITL docker smoke: ok")


asyncio.run(main())
