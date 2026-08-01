from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.workflow_native.validate import SUPPORTED_NODE_KINDS
from server.xpert_runtime import (
    WorkflowNodeRegistry,
    register_builtin_workflow_nodes,
)


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _registry() -> WorkflowNodeRegistry:
    registry = WorkflowNodeRegistry()
    register_builtin_workflow_nodes(registry)
    return registry


def test_workflow_node_registry_returns_workflow_and_knowledge_tabs() -> None:
    payload = _registry().to_payload()

    assert payload["version"] == "xpert-workflow-node-registry-v1"
    assert {tab["id"] for tab in payload["tabs"]} == {"workflow", "knowledge"}
    assert payload["sections"]
    assert payload["knowledge_pipeline"]["items"]


def test_enabled_workflow_node_kinds_are_supported() -> None:
    registry = _registry()

    assert registry.enabled_kinds()
    assert registry.enabled_kinds().issubset(SUPPORTED_NODE_KINDS)


def test_placeholders_are_disabled_and_do_not_declare_kind() -> None:
    payload = _registry().to_payload()

    placeholders = []
    for section in payload["sections"]:
        placeholders.extend(section["placeholders"])
    placeholders.extend(payload["knowledge_pipeline"]["placeholders"])

    assert placeholders
    assert all(item["enabled"] is False for item in placeholders)
    assert all("kind" not in item for item in placeholders)
    assert all(item["statusLabel"] for item in placeholders)


@pytest.mark.asyncio
async def test_workflow_node_registry_api_returns_stable_shape(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/workflow/node-registry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "xpert-workflow-node-registry-v1"
    assert isinstance(payload["tabs"], list)
    assert isinstance(payload["sections"], list)
    assert isinstance(payload["knowledge_pipeline"], dict)
    assert payload["knowledge_pipeline"]["items"][0]["kind"] == "knowledge_citation"
