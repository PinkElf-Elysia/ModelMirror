from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app
from server.xpert_runtime.memory_toolset import MemoryToolsetProvider
from server.xpert_runtime.capabilities import CapabilityRegistry
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import MiddlewareContext
from server.xpert_runtime.tool_policy import ToolPermissionPolicy
from server.xpert_runtime.tool_runner import run_tool_with_runtime
from server.xpert_runtime.agent_tasks import AgentTaskStore
from server.xpert_runtime.goal_coordinator import GoalCoordinator, GoalPlan, PinnedXpert
from server.xpert_runtime.goals import GoalStep, GoalStore
from server.xpert_runtime.run_registry import RunRegistry
from server.xpert_runtime.toolset import RuntimeToolCall
from server.xperts import (
    XpertContextNotFoundError,
    XpertContextStore,
    XpertContextValidationError,
    XpertStore,
    set_xpert_context_store_for_tests,
    set_xpert_store_for_tests,
    validate_xpert_definition,
)


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    xperts = XpertStore(tmp_path / "xperts")
    context = XpertContextStore(tmp_path / "runtime")
    set_xpert_store_for_tests(xperts)
    set_xpert_context_store_for_tests(context)
    monkeypatch.setattr(main_module, "xpert_context_store", context)
    monkeypatch.setattr(main_module.workflow_memory_provider, "context_store", context)
    yield xperts, context
    set_xpert_store_for_tests(None)
    set_xpert_context_store_for_tests(None)


@pytest_asyncio.fixture
async def client(stores):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-forwarded-for": "198.51.100.41"},
    ) as async_client:
        yield async_client


def test_context_store_persists_files_memories_and_candidate_decisions(
    stores,
) -> None:
    _, context = stores
    conversation = context.create_conversation("xpert-1", title="Project")
    asset = context.add_file(
        "xpert-1",
        conversation.conversation_id,
        filename="brief.md",
        content="# Launch\nThe codename is Phoenix.".encode(),
    )
    memory = context.create_memory(
        "xpert-1",
        content="The user prefers concise launch plans.",
        scope="xpert",
        tags=["preference"],
    )
    conversation_memory = context.create_memory(
        "xpert-1",
        content="This conversation is about Project Phoenix.",
        scope="conversation",
        conversation_id=conversation.conversation_id,
    )
    candidate = context.create_candidate(
        "xpert-1",
        content="The launch date is October 1.",
        scope="xpert",
        source_run_id="run-1",
    )
    approved = context.decide_candidate(
        "xpert-1",
        candidate.candidate_id,
        approve=True,
    )
    assert approved.memory_id

    reloaded = XpertContextStore(context.storage_dir)
    restored = reloaded.get_conversation("xpert-1", conversation.conversation_id)
    assert restored.title == "Project"
    assert reloaded.read_file_text(reloaded.get_file("xpert-1", asset.asset_id)).endswith(
        "Phoenix."
    )
    assert reloaded.get_memory("xpert-1", memory.memory_id).status == "active"
    assert reloaded.get_memory("xpert-1", conversation_memory.memory_id).scope == "conversation"
    assert reloaded.list_candidates("xpert-1")[0].status == "approved"

    archived = reloaded.archive_file(
        "xpert-1",
        conversation.conversation_id,
        asset.asset_id,
    )
    assert archived.status == "archived"
    with pytest.raises(XpertContextNotFoundError):
        reloaded.get_file("xpert-1", asset.asset_id)
    assert reloaded.get_file("xpert-1", asset.asset_id, include_archived=True)


def test_context_store_rejects_unsafe_files_and_cross_xpert_access(stores) -> None:
    _, context = stores
    conversation = context.create_conversation("xpert-1")
    with pytest.raises(XpertContextValidationError, match="filename"):
        context.add_file(
            "xpert-1",
            conversation.conversation_id,
            filename="../secret.txt",
            content=b"secret",
        )
    with pytest.raises(XpertContextValidationError, match="Unsupported"):
        context.add_file(
            "xpert-1",
            conversation.conversation_id,
            filename="payload.exe",
            content=b"unsafe",
        )
    asset = context.add_file(
        "xpert-1",
        conversation.conversation_id,
        filename="safe.txt",
        content=b"safe content",
    )
    with pytest.raises(XpertContextNotFoundError):
        context.get_file("xpert-2", asset.asset_id)
    with pytest.raises(XpertContextNotFoundError):
        context.get_conversation("xpert-2", conversation.conversation_id)


def test_context_store_persists_derived_conversation_summary(stores) -> None:
    _, context = stores
    conversation = context.create_conversation("xpert-summary")
    boundary = context.append_message(
        "xpert-summary",
        conversation.conversation_id,
        role="user",
        content="The project name is Aurora.",
    )

    context.update_conversation_summary(
        "xpert-summary",
        conversation.conversation_id,
        summary="The conversation concerns project Aurora.",
        model_id="summary-model",
        through_message_id=boundary.message_id,
    )
    assistant = context.append_message(
        "xpert-summary",
        conversation.conversation_id,
        role="assistant",
        content="Aurora is ready for the next planning step.",
        suggestions=["Draft milestones", "List launch risks"],
    )
    context.update_conversation_title(
        "xpert-summary",
        conversation.conversation_id,
        title="Project Aurora",
    )

    restored = XpertContextStore(context.storage_dir).get_conversation(
        "xpert-summary",
        conversation.conversation_id,
    )
    assert restored.title == "Project Aurora"
    assert restored.summary == "The conversation concerns project Aurora."
    assert restored.summary_revision == 1
    assert restored.summary_model_id == "summary-model"
    assert restored.summary_through_message_id == boundary.message_id
    restored_assistant = next(
        message
        for message in restored.messages
        if message.message_id == assistant.message_id
    )
    assert restored_assistant.suggestions == ["Draft milestones", "List launch risks"]


@pytest.mark.asyncio
async def test_context_api_upload_memory_and_candidate_flow(client, stores) -> None:
    xperts, _ = stores
    xpert = xperts.create_xpert(name="Context API")
    conversation_response = await client.post(
        f"/api/xperts/{xpert.id}/conversations",
        json={"title": "API conversation"},
    )
    assert conversation_response.status_code == 200, conversation_response.text
    conversation_id = conversation_response.json()["conversation_id"]

    upload = await client.post(
        f"/api/xperts/{xpert.id}/conversations/{conversation_id}/files",
        files={"file": ("notes.txt", b"Remember the blue launch theme.", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    assert "storage_key" not in upload.json()
    assert "text_key" not in upload.json()

    memory = await client.post(
        f"/api/xperts/{xpert.id}/memories",
        json={
            "content": "Use the blue launch theme.",
            "scope": "conversation",
            "conversation_id": conversation_id,
        },
    )
    assert memory.status_code == 200, memory.text
    listed = await client.get(
        f"/api/xperts/{xpert.id}/memories",
        params={"scope": "both", "conversation_id": conversation_id},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    other = xperts.create_xpert(name="Other")
    denied = await client.get(
        f"/api/xperts/{other.id}/conversations/{conversation_id}"
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_file_memory_api_supports_typed_edit_candidates_and_revision_conflicts(
    client,
    stores,
) -> None:
    xperts, context = stores
    xpert = xperts.create_xpert(name="Typed Memory API")
    conversation = context.create_conversation(xpert.id)

    created = await client.post(
        f"/api/xperts/{xpert.id}/memories",
        json={
            "scope": "xpert",
            "conversation_id": conversation.conversation_id,
            "type": "feedback",
            "title": "Response style",
            "summary": "Prefer direct answers.",
            "content": "The user prefers direct answers with short verification notes.",
            "tags": ["style"],
        },
    )
    assert created.status_code == 200, created.text
    memory = created.json()
    assert memory["type"] == "feedback"
    assert memory["canonical_ref"].startswith("memory://xpert/")

    index = await client.get(f"/api/xperts/{xpert.id}/file-memory/index")
    assert index.status_code == 200, index.text
    assert index.json()["type_counts"]["feedback"] == 1
    assert "body_key" not in index.text

    detail = await client.get(
        f"/api/xperts/{xpert.id}/file-memory/{memory['memory_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["usage"]["detail_read_count"] == 1

    updated = await client.patch(
        f"/api/xperts/{xpert.id}/file-memory/{memory['memory_id']}",
        json={
            "revision": memory["revision"],
            "summary": "Prefer concise and direct answers.",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == memory["revision"] + 1

    stale = await client.patch(
        f"/api/xperts/{xpert.id}/file-memory/{memory['memory_id']}",
        json={"revision": memory["revision"], "summary": "Stale overwrite"},
    )
    assert stale.status_code == 409, stale.text

    candidate = context.create_candidate(
        xpert.id,
        scope="xpert",
        content="Use a compact verification section.",
        memory_type="feedback",
        title="Verification format",
        summary="Keep verification concise.",
        source_run_id="run-typed-memory",
    )
    edited = await client.patch(
        f"/api/xperts/{xpert.id}/memory-candidates/{candidate.candidate_id}",
        json={
            "revision": candidate.revision,
            "title": "Verification note format",
            "tags": ["style", "verification"],
        },
    )
    assert edited.status_code == 200, edited.text
    edited_candidate = edited.json()
    approved = await client.post(
        f"/api/xperts/{xpert.id}/memory-candidates/{candidate.candidate_id}/approve",
        json={"revision": edited_candidate["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    signals = await client.get(f"/api/xperts/{xpert.id}/file-memory/signals")
    assert signals.status_code == 200, signals.text
    assert signals.json()["total"] >= 2
    assert "storage_key" not in signals.text


@pytest.mark.asyncio
async def test_published_xpert_injects_selected_files_and_memory(
    client,
    stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xperts, context = stores
    captured: list[str] = []

    async def fake_stream(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        captured.append(prompt)
        yield "context answer"

    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://gateway.invalid/v1/chat/completions", "test-key"),
    )

    xpert = xperts.create_xpert(name="File Memory Xpert")
    version = xperts.publish_xpert(xpert.id, expected_revision=xpert.draft_revision)
    conversation = context.create_conversation(xpert.id)
    asset = context.add_file(
        xpert.id,
        conversation.conversation_id,
        filename="launch.md",
        content="The launch codename is Aurora.".encode(),
    )
    context.create_memory(
        xpert.id,
        content="The user prefers a three-step plan.",
        scope="xpert",
    )

    response = await client.post(
        f"/api/xperts/{xpert.id}/run",
        json={
            "message": "Prepare the launch plan",
            "version": version.version,
            "conversation_id": conversation.conversation_id,
            "file_asset_ids": [asset.asset_id],
        },
    )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    meta = next(item for item in events if item["event"] == "workflow_meta")
    assert meta["conversation_id"] == conversation.conversation_id
    assert meta["file_count"] == 1
    assert "launch codename is Aurora" in captured[0]
    assert "three-step plan" in captured[0]

    restored = context.get_conversation(xpert.id, conversation.conversation_id)
    assert [item.role for item in restored.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_memory_toolset_search_get_and_propose(stores) -> None:
    _, context = stores
    conversation = context.create_conversation("xpert-tools")
    memory = context.create_memory(
        "xpert-tools",
        content="The deployment region is Singapore.",
        scope="xpert",
    )
    provider = MemoryToolsetProvider(context)
    metadata = {
        "xpert_id": "xpert-tools",
        "conversation_id": conversation.conversation_id,
        "run_id": "run-memory",
    }
    searched = await provider.call_tool(
        RuntimeToolCall(
            "memory_search",
            {"query": "deployment region", "scope": "both"},
            metadata,
        )
    )
    assert memory.memory_id in searched.output
    fetched = await provider.call_tool(
        RuntimeToolCall("memory_get", {"memory_id": memory.memory_id}, metadata)
    )
    assert "Singapore" in fetched.output
    proposed = await provider.call_tool(
        RuntimeToolCall(
            "memory_propose_write",
            {"content": "The release owner is Dana.", "scope": "xpert"},
            metadata,
        )
    )
    assert json.loads(proposed.output)["status"] == "pending"

    registry = CapabilityRegistry()
    registry.register("memory_tools", provider)
    through_runtime = await run_tool_with_runtime(
        RuntimeToolCall(
            "memory_search",
            {"query": "Singapore", "scope": "xpert"},
            metadata,
        ),
        registry,
        MiddlewarePipeline([]),
        MiddlewareContext(task_id="task-1"),
        capability_name="memory_tools",
        policy=ToolPermissionPolicy(allow_by_default=True),
    )
    assert memory.memory_id in through_runtime.output


def test_xpert_memory_scope_validation(stores) -> None:
    xperts, _ = stores
    xpert = xperts.create_xpert(name="Invalid memory scope")
    agent = next(
        node for node in xpert.draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    agent.data["memoryReadScope"] = "workspace"
    agent.data["memoryWriteTarget"] = "database"
    result = validate_xpert_definition(xpert)
    codes = {issue.code for issue in result.issues}
    assert "invalid_workflow_agent_memory_read_scope" in codes
    assert "invalid_workflow_agent_memory_write_target" in codes


@pytest.mark.asyncio
async def test_goal_handoff_carries_explicit_file_references() -> None:
    goals = GoalStore()
    tasks = AgentTaskStore()
    runs = RunRegistry()

    async def planner(goal, parent_run_id: str) -> GoalPlan:
        return GoalPlan(
            summary="Use the shared brief.",
            final_step_id="deliver",
            steps=[
                GoalStep("prepare", "Prepare", "Read the brief.", "worker"),
                GoalStep(
                    "deliver",
                    "Deliver",
                    "Deliver the answer.",
                    "worker",
                    depends_on=["prepare"],
                ),
            ],
        )

    async def resolver(reference: str) -> PinnedXpert:
        return PinnedXpert(reference, reference, 1, reference)

    coordinator = GoalCoordinator(goals, tasks, runs, planner, resolver)
    goal = await goals.create_goal(
        title="Shared file goal",
        objective="Use an explicit file.",
        planner_xpert_id="planner",
        planner_version=1,
        source_xpert_id="source-xpert",
        source_conversation_id="conversation-1",
        file_asset_ids=["asset-1"],
    )
    await coordinator.plan_goal(goal.goal_id)
    await coordinator.start_goal(goal.goal_id)
    await coordinator.process_goal(goal.goal_id)
    running = await goals.require_goal(goal.goal_id)
    step = next(item for item in running.steps if item.status == "running")
    assert step.handoff_id
    handoff = await tasks.get_handoff(step.handoff_id)
    assert handoff is not None
    assert handoff.metadata["source_xpert_id"] == "source-xpert"
    assert handoff.metadata["source_conversation_id"] == "conversation-1"
    assert handoff.metadata["file_asset_ids"] == ["asset-1"]
