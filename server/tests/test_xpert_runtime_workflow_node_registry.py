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

    assert payload["version"] == "xpert-workflow-node-registry-v4"
    assert payload["contract_version"] == 3
    assert len(payload["contract_checksum"]) == 64
    assert {tab["id"] for tab in payload["tabs"]} == {"workflow", "knowledge"}
    assert payload["sections"]
    knowledge_items = payload["knowledge_pipeline"]["items"]
    assert [item["kind"] for item in knowledge_items] == [
        "knowledge_base",
        "knowledge_retrieval",
        "vision_understanding",
    ]
    assert payload["knowledge_pipeline"]["placeholders"] == []

    knowledge_base, retrieval, vision = knowledge_items
    assert knowledge_base["planner"]["support"] == "binding_only"
    assert knowledge_base["contracts"]["resources"][0]["kind"] == "knowledge_base"
    assert retrieval["planner"]["enabled"] is False
    assert retrieval["planner"]["support"] == "unsupported"
    assert retrieval["contracts"]["outputs"][0]["name"] == "result"
    assert retrieval["contracts"]["outputs"][0]["value_schema"]["type"] == "any"
    assert len(retrieval["contracts"]["outputs"][0]["value_schema"]["any_of"]) == 2
    assert vision["planner"]["support"] == "unsupported"
    assert vision["contracts"]["outputs"][0]["value_schema"]["type"] == "object"
    assert vision["metadata"]["private_only"] is True


def test_enabled_workflow_node_kinds_are_supported() -> None:
    registry = _registry()

    assert registry.enabled_kinds()
    assert registry.enabled_kinds().issubset(SUPPORTED_NODE_KINDS)


def test_document_extractor_palette_follows_file_asset_gate(monkeypatch) -> None:
    monkeypatch.delenv("WORKFLOW_FILE_ASSETS_ENABLED", raising=False)
    monkeypatch.delenv("FILE_ASSET_STORE_MODE", raising=False)
    disabled = _registry()
    item = next(
        item
        for section in disabled.sections()
        for item in section.items
        if item.kind == "document_extractor"
    )
    assert item.enabled is False
    assert item.metadata["status_reason"]
    assert "本地路径" not in item.description

    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    legacy_store = _registry()
    item = next(
        item
        for section in legacy_store.sections()
        for item in section.items
        if item.kind == "document_extractor"
    )
    assert item.enabled is False

    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    enabled = _registry()
    item = next(
        item
        for section in enabled.sections()
        for item in section.items
        if item.kind == "document_extractor"
    )
    assert item.enabled is True
    assert item.metadata == {}
    projection = item.to_payload()
    assert projection["contract"]["contract_status"] == "complete"
    assert projection["planner"]["enabled"] is False


def test_placeholders_are_disabled_and_do_not_declare_kind() -> None:
    payload = _registry().to_payload()

    placeholders = []
    for section in payload["sections"]:
        placeholders.extend(section["placeholders"])
    placeholders.extend(payload["knowledge_pipeline"]["placeholders"])

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
    assert payload["version"] == "xpert-workflow-node-registry-v4"
    assert payload["contract_version"] == 3
    assert len(payload["contract_checksum"]) == 64
    assert isinstance(payload["tabs"], list)
    assert isinstance(payload["sections"], list)
    assert isinstance(payload["knowledge_pipeline"], dict)
    assert [
        item["kind"] for item in payload["knowledge_pipeline"]["items"]
    ] == ["knowledge_base", "knowledge_retrieval", "vision_understanding"]
    assert all(
        item["kind"] != "knowledge_citation"
        for section in payload["sections"]
        for item in section["items"]
    )
