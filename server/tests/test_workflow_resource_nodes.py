from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app, workflow_topological_order
from server.rag.api import set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore
from server.workflow_native.schemas import NativeWorkflowDefinition, NativeWorkflowEdge
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.external_xpert_toolset import ExternalXpertToolsetProvider
from server.xpert_runtime.toolset import (
    RuntimeToolCall,
    RuntimeToolError,
    RuntimeToolResult,
)
from server.xperts import XpertStore, set_xpert_store_for_tests
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy


@pytest.fixture(autouse=True)
def reset_xpert_rate_limit_window():
    main_module.request_windows.clear()
    yield
    main_module.request_windows.clear()


@pytest.fixture
def resource_stores(tmp_path: Path):
    xpert_store = XpertStore(tmp_path / "xperts")
    rag_service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(
            tmp_path / "rag-storage" / "vectors.json"
        ),
        llm_enabled=False,
    )
    set_xpert_store_for_tests(xpert_store)
    set_rag_service_for_tests(rag_service)
    yield xpert_store, rag_service
    set_rag_service_for_tests(None)
    set_xpert_store_for_tests(None)


@pytest_asyncio.fixture
async def client(resource_stores):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _agent_data(workflow: NativeWorkflowDefinition) -> dict[str, Any]:
    for node in workflow.nodes:
        if str(node.data.get("kind") or node.type) == "workflow_agent":
            return node.data
    raise AssertionError("workflow_agent node not found")


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                import json

                events.append(json.loads(line[6:]))
    return events


def _with_bound_resources(
    workflow: NativeWorkflowDefinition,
    *,
    external_xpert_id: str,
    knowledge_base_id: str | None = None,
) -> NativeWorkflowDefinition:
    result = workflow.model_dump(mode="json")
    agent = next(
        node
        for node in result["nodes"]
        if str(node["data"].get("kind") or node.get("type")) == "workflow_agent"
    )
    agent["data"]["toolMode"] = "mcp_tools"
    agent["data"]["taskInput"] = "{{user_input}}"
    result["nodes"].append(
        {
            "id": "expert-resource",
            "type": "external_xpert",
            "data": {
                "kind": "external_xpert",
                "xpertId": external_xpert_id,
                "toolName": "specialist",
                "description": "Delegate specialist work.",
                "versionPolicy": "current_published",
                "pinnedVersion": "1",
            },
        }
    )
    result["edges"].append(
        {
            "id": "bind-expert",
            "source": "expert-resource",
            "target": agent["id"],
            "sourceHandle": "expert-binding",
            "targetHandle": "expert",
        }
    )
    if knowledge_base_id:
        result["nodes"].append(
            {
                "id": "knowledge-resource",
                "type": "knowledge_base",
                "data": {
                    "kind": "knowledge_base",
                    "knowledgeBaseId": knowledge_base_id,
                    "topK": "7",
                    "scoreThreshold": "0.2",
                    "description": "Project references.",
                },
            }
        )
        result["edges"].append(
            {
                "id": "bind-knowledge",
                "source": "knowledge-resource",
                "target": agent["id"],
                "sourceHandle": "knowledge-binding",
                "targetHandle": "knowledge",
            }
        )
    return NativeWorkflowDefinition.model_validate(result)


def test_resource_bindings_are_valid_and_do_not_enter_control_flow(
    resource_stores,
) -> None:
    xpert_store, rag_service = resource_stores
    specialist = xpert_store.create_xpert(name="Specialist")
    xpert_store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    knowledge_base = rag_service.create_knowledge_base("References")
    manager = xpert_store.create_xpert(name="Manager")
    workflow = _with_bound_resources(
        manager.draft.workflow,
        external_xpert_id=specialist.id,
        knowledge_base_id=knowledge_base["id"],
    )

    validation = validate_workflow_graph(workflow)
    order = workflow_topological_order(
        list(workflow.nodes),
        list(workflow.edges),
    )

    assert validation.valid is True
    assert "expert-resource" not in validation.order
    assert "knowledge-resource" not in validation.order
    assert "expert-resource" not in order
    assert "knowledge-resource" not in order
    assert set(order) == {"input-1", "workflow-agent-1", "output-1"}


def test_resource_binding_rejects_unbound_and_mixed_control_flow(
    resource_stores,
) -> None:
    xpert_store, _ = resource_stores
    specialist = xpert_store.create_xpert(name="Specialist")
    manager = xpert_store.create_xpert(name="Manager")
    workflow = _with_bound_resources(
        manager.draft.workflow,
        external_xpert_id=specialist.id,
    )
    workflow.edges.append(
        NativeWorkflowEdge(
            id="bad-control-edge",
            source="expert-resource",
            target="output-1",
        )
    )
    validation = validate_workflow_graph(workflow)
    codes = {issue.code for issue in validation.issues}
    assert "mixed_resource_binding_and_control_flow" in codes
    assert "resource_node_in_control_flow" in codes

    workflow.edges = [
        edge for edge in workflow.edges if edge.source != "expert-resource"
    ]
    validation = validate_workflow_graph(workflow)
    assert "missing_resource_binding" in {
        issue.code for issue in validation.issues
    }


@pytest.mark.asyncio
async def test_external_xpert_toolset_enforces_bound_scope() -> None:
    calls: list[tuple[str, str]] = []

    async def run_resource(
        resource: dict[str, Any],
        task: str,
        call: RuntimeToolCall,
    ) -> RuntimeToolResult:
        calls.append((str(resource["xpert_id"]), task))
        return RuntimeToolResult(output=f"done:{task}")

    provider = ExternalXpertToolsetProvider(run_resource)
    resources = [
        {
            "tool_name": "specialist",
            "description": "Specialist collaborator",
            "xpert_id": "xpert-specialist",
            "pinned_version": 3,
        }
    ]
    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="specialist",
            arguments={"task": "research this"},
            metadata={"external_xpert_tools": resources},
        )
    )
    assert result.output == "done:research this"
    assert calls == [("xpert-specialist", "research this")]

    with pytest.raises(RuntimeToolError) as exc_info:
        await provider.call_tool(
            RuntimeToolCall(
                tool_name="other",
                arguments={"task": "not allowed"},
                metadata={"external_xpert_tools": resources},
            )
        )
    assert exc_info.value.code == "external_xpert_scope_denied"


@pytest.mark.asyncio
async def test_publish_pins_external_xpert_version_and_app_blocks_it(
    client: httpx.AsyncClient,
    resource_stores,
) -> None:
    xpert_store, _ = resource_stores
    specialist = xpert_store.create_xpert(name="Published Specialist")
    xpert_store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    manager = xpert_store.create_xpert(name="Published Manager")
    draft = manager.draft.model_copy(deep=True)
    draft.workflow = _with_bound_resources(
        draft.workflow,
        external_xpert_id=specialist.slug,
    )
    manager = xpert_store.update_xpert(
        manager.id,
        {"draft": draft.model_dump(mode="json")},
    )

    response = await client.post(
        f"/api/xperts/{manager.id}/publish",
        json={"release_notes": "Collaborator pinned"},
    )
    assert response.status_code == 200, response.text
    version = xpert_store.get_version(manager.id, 1)
    resource = next(
        node
        for node in version.workflow.nodes
        if str(node.data.get("kind") or node.type) == "external_xpert"
    )
    assert resource.data["xpertId"] == specialist.id
    assert resource.data["versionPolicy"] == "pinned"
    assert int(resource.data["pinnedVersion"]) == 1

    changed = specialist.draft.model_copy(deep=True)
    _agent_data(changed.workflow)["rolePrompt"] = "A newer specialist draft."
    specialist = xpert_store.update_xpert(
        specialist.id,
        {"draft": changed.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    frozen = xpert_store.get_version(manager.id, 1)
    frozen_resource = next(
        node
        for node in frozen.workflow.nodes
        if str(node.data.get("kind") or node.type) == "external_xpert"
    )
    assert int(frozen_resource.data["pinnedVersion"]) == 1

    preflight = _deployment_preflight(
        frozen,
        XpertAppPolicy(allow_tools=True),
    )
    assert "app_external_xpert_forbidden" in {
        issue["code"] for issue in preflight["issues"]
    }


@pytest.mark.asyncio
async def test_publish_rejects_external_xpert_collaboration_cycle(
    client: httpx.AsyncClient,
    resource_stores,
) -> None:
    xpert_store, _ = resource_stores
    specialist = xpert_store.create_xpert(name="Cycle Specialist")
    xpert_store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    manager = xpert_store.create_xpert(name="Cycle Manager")
    manager_draft = manager.draft.model_copy(deep=True)
    manager_draft.workflow = _with_bound_resources(
        manager_draft.workflow,
        external_xpert_id=specialist.id,
    )
    manager = xpert_store.update_xpert(
        manager.id,
        {"draft": manager_draft.model_dump(mode="json")},
    )
    manager_publish = await client.post(
        f"/api/xperts/{manager.id}/publish",
        json={},
    )
    assert manager_publish.status_code == 200, manager_publish.text

    specialist_draft = specialist.draft.model_copy(deep=True)
    specialist_draft.workflow = _with_bound_resources(
        specialist_draft.workflow,
        external_xpert_id=manager.id,
    )
    specialist = xpert_store.update_xpert(
        specialist.id,
        {"draft": specialist_draft.model_dump(mode="json")},
    )
    cycle_publish = await client.post(
        f"/api/xperts/{specialist.id}/publish",
        json={},
    )
    assert cycle_publish.status_code == 422, cycle_publish.text
    assert "xpert_external_resource_cycle" in {
        issue["code"] for issue in cycle_publish.json()["detail"]["issues"]
    }


@pytest.mark.asyncio
async def test_external_xpert_runs_in_process_and_registers_child_trace(
    client: httpx.AsyncClient,
    resource_stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xpert_store, _ = resource_stores
    specialist = xpert_store.create_xpert(name="Runtime Specialist")
    xpert_store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    manager = xpert_store.create_xpert(name="Runtime Manager")
    workflow = _with_bound_resources(
        manager.draft.workflow,
        external_xpert_id=specialist.id,
    )
    decisions = iter(
        [
            '{"tool":"specialist","arguments":{"task":"analyze the evidence"}}',
            '{"answer":"manager used the specialist"}',
        ]
    )

    async def fake_collect_chat_completion_text(*args, **kwargs):
        return next(decisions)

    async def fake_stream_workflow_llm_text(*args, **kwargs):
        yield "specialist result"

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow.model_dump(mode="json"),
            "inputs": {"user_input": "coordinate the work"},
        },
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    end = next(item for item in events if item.get("event") == "workflow_end")
    assert end["final_output"] == "manager used the specialist"

    meta = next(item for item in events if item.get("event") == "workflow_meta")
    agent_runs_response = await client.get(
        "/api/runtime/runs",
        params={"parent_run_id": meta["run_id"], "limit": 20},
    )
    assert agent_runs_response.status_code == 200
    agent_run = next(
        item
        for item in agent_runs_response.json()
        if item["run_type"] == "workflow_agent"
    )
    child_runs_response = await client.get(
        "/api/runtime/runs",
        params={"parent_run_id": agent_run["run_id"], "limit": 20},
    )
    assert child_runs_response.status_code == 200
    child_xpert = next(
        item
        for item in child_runs_response.json()
        if item["run_type"] == "xpert"
    )
    assert child_xpert["metadata"]["xpert_id"] == specialist.id
    assert child_xpert["metadata"]["xpert_version"] == 1

    checkpoints_response = await client.get(
        f"/api/runtime/runs/{agent_run['run_id']}/checkpoints"
    )
    checkpoint_types = {
        item["event_type"] for item in checkpoints_response.json()
    }
    assert "external_xpert.started" in checkpoint_types
    assert "external_xpert.completed" in checkpoint_types


@pytest.mark.asyncio
async def test_resource_options_return_safe_summaries(
    client: httpx.AsyncClient,
    resource_stores,
) -> None:
    xpert_store, rag_service = resource_stores
    specialist = xpert_store.create_xpert(
        name="Option Specialist",
        description="Safe public summary.",
    )
    xpert_store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    knowledge_base = rag_service.create_knowledge_base("Option Knowledge")

    expert_response = await client.get(
        "/api/workflow/resource-options",
        params={"kind": "external_xpert"},
    )
    assert expert_response.status_code == 200, expert_response.text
    expert = next(
        item
        for item in expert_response.json()["items"]
        if item["id"] == specialist.id
    )
    assert expert["published_version"] == 1
    assert "workflow" not in expert

    knowledge_response = await client.get(
        "/api/workflow/resource-options",
        params={"kind": "knowledge_base"},
    )
    assert knowledge_response.status_code == 200, knowledge_response.text
    knowledge = next(
        item
        for item in knowledge_response.json()["items"]
        if item["id"] == knowledge_base["id"]
    )
    assert knowledge["status"] == "no_active_index"
    assert "stored_path" not in knowledge
