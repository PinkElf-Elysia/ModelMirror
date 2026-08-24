from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app
from server.workflow_native.r20_nodes import mcp_schema_checksum
from server.xperts import (
    XpertConflictError,
    XpertContextStore,
    XpertStore,
    XpertValidationError,
    set_xpert_context_store_for_tests,
    set_xpert_store_for_tests,
    validate_xpert_definition,
)


@pytest.fixture(autouse=True)
def reset_xpert_rate_limit_window():
    main_module.request_windows.clear()
    yield
    main_module.request_windows.clear()


@pytest.fixture
def xpert_store(tmp_path: Path):
    store = XpertStore(tmp_path / "xperts")
    set_xpert_store_for_tests(store)
    yield store
    set_xpert_store_for_tests(None)


@pytest.fixture
def xpert_context_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = XpertContextStore(tmp_path / "runtime")
    set_xpert_context_store_for_tests(store)
    monkeypatch.setattr(main_module, "xpert_context_store", store)
    yield store
    set_xpert_context_store_for_tests(None)


@pytest_asyncio.fixture
async def client(xpert_store: XpertStore):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def test_xpert_store_persists_unique_slugs_and_immutable_versions(
    xpert_store: XpertStore,
) -> None:
    created = xpert_store.create_xpert(
        name="Research Xpert",
        slug="research-xpert",
        tags=["research", "workflow"],
    )
    assert validate_xpert_definition(created).valid is True
    assert (
        _workflow_agent_data(created.draft.workflow)["modelId"]
        == "deepseek/deepseek-v4-flash-0731"
    )

    reloaded = XpertStore(xpert_store.storage_dir).get_xpert(created.id)
    assert reloaded.slug == "research-xpert"
    assert reloaded.tags == ["research", "workflow"]

    with pytest.raises(XpertValidationError):
        xpert_store.create_xpert(name="Duplicate", slug="research-xpert")

    first_draft = created.draft.model_copy(deep=True)
    first_draft.features.opening.enabled = True
    first_draft.features.opening.message = "Welcome to the published assistant."
    first_draft.features.opening.questions = ["Start the research"]
    created = xpert_store.update_xpert(
        created.id,
        {"draft": first_draft.model_dump(mode="json")},
    )
    version_one = xpert_store.publish_xpert(
        created.id,
        release_notes="First stable release",
        expected_revision=created.draft_revision,
    )
    assert version_one.agent_config is not None
    assert version_one.agent_config.max_concurrency == 4
    assert version_one.agent_config.recursion_limit == 1000
    assert version_one.features is not None
    assert version_one.features.opening.questions == ["Start the research"]
    original_role_prompt = _workflow_agent_data(version_one.workflow)["rolePrompt"]

    next_draft = created.draft.model_copy(deep=True)
    _workflow_agent_data(next_draft.workflow)["rolePrompt"] = "A changed draft prompt."
    next_draft.agent_config.max_concurrency = 7
    next_draft.agent_config.recursion_limit = 240
    next_draft.features.opening.message = "A changed draft welcome."
    next_draft.features.opening.questions = ["Use the changed draft"]
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": next_draft.model_dump(mode="json")},
    )
    version_two = xpert_store.publish_xpert(
        created.id,
        release_notes="Second release",
        expected_revision=updated.draft_revision,
    )

    assert version_one.version == 1
    assert version_two.version == 2
    assert xpert_store.get_version(created.id, 1).workflow == version_one.workflow
    assert _workflow_agent_data(version_one.workflow)["rolePrompt"] == original_role_prompt
    assert _workflow_agent_data(version_two.workflow)["rolePrompt"] == "A changed draft prompt."
    assert version_two.agent_config is not None
    assert version_two.agent_config.max_concurrency == 7
    assert version_two.agent_config.recursion_limit == 240
    assert version_one.agent_config.max_concurrency == 4
    assert version_one.features.opening.message == "Welcome to the published assistant."
    assert version_two.features is not None
    assert version_two.features.opening.message == "A changed draft welcome."


def test_xpert_publish_revision_conflict_is_rejected(xpert_store: XpertStore) -> None:
    created = xpert_store.create_xpert(name="Concurrent Xpert")
    changed_draft = created.draft.model_copy(deep=True)
    _workflow_agent_data(changed_draft.workflow)["rolePrompt"] = "Changed during preflight."
    xpert_store.update_xpert(
        created.id,
        {"draft": changed_draft.model_dump(mode="json")},
    )

    with pytest.raises(XpertConflictError):
        xpert_store.publish_xpert(
            created.id,
            expected_revision=created.draft_revision,
        )


@pytest.mark.asyncio
async def test_xpert_api_create_validate_publish_and_list_versions(
    client: httpx.AsyncClient,
) -> None:
    create_response = await client.post(
        "/api/xperts",
        json={
            "name": "Support Planner",
            "slug": "support-planner",
            "description": "Plans customer support work.",
            "tags": ["support"],
            "starters": ["Plan this escalation"],
        },
    )
    assert create_response.status_code == 200, create_response.text
    xpert = create_response.json()

    validation_response = await client.post(f"/api/xperts/{xpert['id']}/validate")
    assert validation_response.status_code == 200
    assert validation_response.json()["valid"] is True
    assert set(xpert["draft"]["features"]["file_upload"]["allowed_extensions"]) >= {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    }

    publish_response = await client.post(
        f"/api/xperts/{xpert['id']}/publish",
        json={"release_notes": "Ready for team use"},
    )
    assert publish_response.status_code == 200, publish_response.text
    assert publish_response.json()["version"] == 1

    list_response = await client.get("/api/xperts?status=published&search=support")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["id"] == xpert["id"]

    versions_response = await client.get(f"/api/xperts/{xpert['id']}/versions")
    assert versions_response.status_code == 200
    assert [item["version"] for item in versions_response.json()] == [1]


@pytest.mark.asyncio
async def test_xpert_publish_preflight_rejects_invalid_chat_contract(
    client: httpx.AsyncClient,
) -> None:
    create_response = await client.post("/api/xperts", json={"name": "Invalid Xpert"})
    xpert = create_response.json()
    draft = xpert["draft"]
    _workflow_agent_data_dict(draft["workflow"])["modelId"] = ""
    _workflow_agent_data_dict(draft["workflow"])["taskInput"] = ""
    draft["workflow"]["nodes"].append(
        {
            "id": "human-1",
            "type": "human_intervention",
            "data": {
                "kind": "human_intervention",
                "prompt": "Approve this response",
                "outputVariable": "approval",
            },
        }
    )

    update_response = await client.patch(
        f"/api/xperts/{xpert['id']}",
        json={"draft": draft},
    )
    assert update_response.status_code == 200, update_response.text

    publish_response = await client.post(
        f"/api/xperts/{xpert['id']}/publish",
        json={},
    )
    assert publish_response.status_code == 422
    issues = publish_response.json()["detail"]["issues"]
    codes = {item["code"] for item in issues}
    assert "xpert_workflow_agent_missing_modelId" in codes
    assert "xpert_workflow_agent_missing_taskInput" in codes
    assert "xpert_human_intervention_not_supported" not in codes

    versions_response = await client.get(f"/api/xperts/{xpert['id']}/versions")
    assert versions_response.json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "data", "expected_code"),
    [
        (
            "human_intervention",
            {
                "kind": "human_intervention",
                "prompt": "Approve",
                "outputVariable": "human_result",
            },
            "xpert_human_intervention_migration_required",
        ),
        (
            "mcp_tool",
            {
                "kind": "mcp_tool",
                "toolName": "search",
                "argumentsJson": "{}",
                "outputVariable": "mcp_result",
            },
            "xpert_mcp_tool_migration_required",
        ),
        (
            "variable_assign",
            {
                "kind": "variable_assign",
                "variableName": "assigned",
                "template": "value",
            },
            "xpert_variable_assign_migration_required",
        ),
        (
            "knowledge_citation",
            {
                "kind": "knowledge_citation",
                "knowledgeBaseId": "kb_test",
                "queryVariable": "user_input",
                "top_k": "4",
                "outputVariable": "citations",
            },
            "xpert_knowledge_citation_migration_required",
        ),
        (
            "code",
            {
                "kind": "code",
                "codeOperation": "upper",
                "codeInputVariable": "user_input",
                "codeOutputVariable": "clean_value",
            },
            "xpert_code_migration_required",
        ),
        (
            "template_transform",
            {
                "kind": "template_transform",
                "template": "{{user_input}}",
                "outputVariable": "clean_value",
            },
            "xpert_template_transform_migration_required",
        ),
    ],
)
async def test_r20_xpert_preflight_requires_explicit_legacy_node_migration(
    client: httpx.AsyncClient,
    kind: str,
    data: dict,
    expected_code: str,
) -> None:
    created_response = await client.post("/api/xperts", json={"name": f"Legacy {kind}"})
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    draft["workflow"]["nodes"].append(
        {
            "id": "legacy-node",
            "type": kind,
            "position": {"x": 200, "y": 250},
            "data": data,
        }
    )

    updated = await client.patch(
        f"/api/xperts/{xpert['id']}",
        json={"draft": draft},
    )
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})

    assert published.status_code == 422
    codes = {item["code"] for item in published.json()["detail"]["issues"]}
    assert expected_code in codes


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["human_intervention", "variable_assign"])
async def test_r20_general_v2_nodes_pass_private_xpert_publish(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    created_response = await client.post("/api/xperts", json={"name": f"R2.0 {kind}"})
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    data = (
        {
            "kind": kind,
            "contractVersion": 2,
            "interactionMode": "approval",
            "prompt": "Approve this run",
            "outputVariable": "human_result",
            "timeoutSeconds": 3600,
        }
        if kind == "human_intervention"
        else {
            "kind": kind,
            "contractVersion": 2,
            "outputVariable": "assigned",
            "valueSource": "literal",
            "literalValue": {"ok": True},
        }
    )
    workflow["nodes"].append(
        {
            "id": "r20-node",
            "type": kind,
            "position": {"x": 200, "y": 250},
            "data": data,
        }
    )

    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})

    assert published.status_code == 200, published.text


@pytest.mark.asyncio
async def test_r21_safe_text_v2_passes_private_xpert_publish(
    client: httpx.AsyncClient,
) -> None:
    created_response = await client.post(
        "/api/xperts", json={"name": "R2.1 safe text"}
    )
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    draft["workflow"]["nodes"].append(
        {
            "id": "safe-text-v2",
            "type": "code",
            "position": {"x": 200, "y": 250},
            "data": {
                "kind": "code",
                "contractVersion": 2,
                "operation": "upper",
                "inputVariable": "user_input",
                "outputVariable": "clean_value",
                "replaceFrom": "",
                "replaceTo": "",
                "concatValue": "",
            },
        }
    )

    updated = await client.patch(
        f"/api/xperts/{xpert['id']}", json={"draft": draft}
    )
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})

    assert published.status_code == 200, published.text


@pytest.mark.asyncio
async def test_r20_mcp_v2_private_xpert_fails_closed_when_feature_is_disabled(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKFLOW_MCP_TOOLS_ENABLED", raising=False)
    schema = {"type": "object", "properties": {}}
    created_response = await client.post("/api/xperts", json={"name": "Disabled MCP V2"})
    xpert = created_response.json()
    draft = xpert["draft"]
    draft["workflow"]["nodes"].append(
        {
            "id": "mcp-v2",
            "type": "mcp_tool",
            "position": {"x": 200, "y": 250},
            "data": {
                "kind": "mcp_tool",
                "contractVersion": 2,
                "serverId": "server_alpha",
                "toolName": "search",
                "inputSchemaChecksum": mcp_schema_checksum(schema),
                "argumentMode": "fields",
                "argumentBindings": [],
                "argumentsVariable": "mcp_arguments",
                "outputVariable": "mcp_result",
            },
        }
    )

    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})

    assert published.status_code == 422
    codes = {item["code"] for item in published.json()["detail"]["issues"]}
    assert "xpert_mcp_tools_disabled" in codes


@pytest.mark.asyncio
async def test_xpert_publish_uses_file_registry_allowlist(
    client: httpx.AsyncClient,
) -> None:
    create_response = await client.post(
        "/api/xperts",
        json={"name": "Controlled file formats"},
    )
    assert create_response.status_code == 200, create_response.text
    xpert = create_response.json()
    draft = xpert["draft"]
    draft["features"]["file_upload"]["allowed_extensions"] = [
        ".txt",
        ".exe",
    ]

    update_response = await client.patch(
        f"/api/xperts/{xpert['id']}",
        json={"draft": draft},
    )
    assert update_response.status_code == 200, update_response.text

    publish_response = await client.post(
        f"/api/xperts/{xpert['id']}/publish",
        json={},
    )
    assert publish_response.status_code == 422
    issues = publish_response.json()["detail"]["issues"]
    assert any(
        item["code"] == "xpert_file_extension_unsupported"
        and ".exe" in item["message"]
        for item in issues
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["terminate_error", "multi_route", "list_operation", "data_aggregate"],
)
async def test_r16_general_nodes_pass_real_xpert_publish_preflight(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    created_response = await client.post(
        "/api/xperts",
        json={"name": f"R1.6 {kind}"},
    )
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base_nodes = {node["id"]: node for node in workflow["nodes"]}
    input_node = base_nodes["input-1"]
    agent_node = base_nodes["workflow-agent-1"]
    output_node = base_nodes["output-1"]

    if kind == "terminate_error":
        workflow["nodes"] = [
            input_node,
            {
                "id": "condition-1",
                "type": "condition",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "condition",
                    "conditionVariable": "user_input",
                    "conditionOperator": "equals",
                    "conditionValue": "stop",
                },
            },
            {
                "id": "terminate-1",
                "type": "terminate_error",
                "position": {"x": 420, "y": 40},
                "data": {
                    "kind": "terminate_error",
                    "errorCode": "REQUEST_REJECTED",
                    "message": "This request cannot continue.",
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-condition", "source": "input-1", "target": "condition-1"},
            {
                "id": "condition-stop",
                "source": "condition-1",
                "sourceHandle": "true",
                "target": "terminate-1",
            },
            {
                "id": "condition-agent",
                "source": "condition-1",
                "sourceHandle": "false",
                "target": "workflow-agent-1",
            },
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]
    elif kind == "multi_route":
        workflow["nodes"] = [
            input_node,
            {
                "id": "route-1",
                "type": "multi_route",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "multi_route",
                    "inputVariable": "user_input",
                    "routes": [
                        {
                            "id": "route_1",
                            "label": "Alpha",
                            "operator": "equals",
                            "valueType": "text",
                            "value": "alpha",
                        },
                        {
                            "id": "route_2",
                            "label": "Beta",
                            "operator": "equals",
                            "valueType": "text",
                            "value": "beta",
                        },
                    ],
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-route", "source": "input-1", "target": "route-1"},
            *[
                {
                    "id": f"route-agent-{handle}",
                    "source": "route-1",
                    "sourceHandle": handle,
                    "target": "workflow-agent-1",
                }
                for handle in ("route_1", "route_2", "default")
            ],
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]
    else:
        node_data = (
            {
                "kind": "list_operation",
                "inputVariable": "user_input",
                "operator": "filter",
                "filterMode": "all",
                "filterRules": [
                    {
                        "operator": "contains",
                        "valueType": "text",
                        "value": "ready",
                    }
                ],
                "outputVariable": "prepared_rows",
            }
            if kind == "list_operation"
            else {
                "kind": "data_aggregate",
                "inputVariable": "user_input",
                "outputVariable": "prepared_rows",
                "groupByFields": [],
                "measures": [{"outputField": "row_count", "operation": "count"}],
            }
        )
        workflow["nodes"] = [
            input_node,
            {
                "id": "data-node-1",
                "type": kind,
                "position": {"x": 220, "y": 140},
                "data": node_data,
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-data", "source": "input-1", "target": "data-node-1"},
            {"id": "data-agent", "source": "data-node-1", "target": "workflow-agent-1"},
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]

    updated = await client.patch(
        f"/api/xperts/{xpert['id']}",
        json={"draft": draft},
    )
    assert updated.status_code == 200, updated.text
    published = await client.post(
        f"/api/xperts/{xpert['id']}/publish",
        json={},
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["condition", "dataset_compare"])
async def test_r17_typed_nodes_pass_real_xpert_publish_preflight(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    created_response = await client.post("/api/xperts", json={"name": f"R1.7 {kind}"})
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base_nodes = {node["id"]: node for node in workflow["nodes"]}
    input_node = base_nodes["input-1"]
    agent_node = base_nodes["workflow-agent-1"]
    output_node = base_nodes["output-1"]

    if kind == "condition":
        workflow["nodes"] = [
            input_node,
            {
                "id": "condition-1",
                "type": "condition",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "condition",
                    "contractVersion": 2,
                    "inputVariable": "user_input",
                    "field": "",
                    "operator": "contains",
                    "valueType": "text",
                    "value": "ready",
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-condition", "source": "input-1", "target": "condition-1"},
        ] + [
            {
                "id": f"condition-agent-{handle}",
                "source": "condition-1",
                "sourceHandle": handle,
                "target": "workflow-agent-1",
            }
            for handle in ("true", "false")
        ] + [{"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"}]
    else:
        workflow["variables"] = [
            {
                "id": "before-rows",
                "name": "before_rows",
                "kind": "constant",
                "valueType": "json",
                "defaultValue": [{"id": 1, "value": "old"}],
            },
            {
                "id": "after-rows",
                "name": "after_rows",
                "kind": "constant",
                "valueType": "json",
                "defaultValue": [{"id": 1, "value": "new"}],
            },
        ]
        workflow["nodes"] = [
            input_node,
            {
                "id": "dataset-1",
                "type": "dataset_compare",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "dataset_compare",
                    "leftVariable": "before_rows",
                    "rightVariable": "after_rows",
                    "keyFields": ["id"],
                    "includeUnchanged": False,
                    "outputVariable": "dataset_difference",
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-dataset", "source": "input-1", "target": "dataset-1"},
            {"id": "dataset-agent", "source": "dataset-1", "target": "workflow-agent-1"},
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]

    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
async def test_r21_data_merge_passes_real_xpert_publish_preflight(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post("/api/xperts", json={"name": "R2.1 data merge"})
    assert created.status_code == 200, created.text
    xpert = created.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base_nodes = {node["id"]: node for node in workflow["nodes"]}
    workflow["variables"] = [
        {
            "id": "left-rows",
            "name": "left_rows",
            "kind": "constant",
            "valueType": "json",
            "defaultValue": [{"id": 1}],
        },
        {
            "id": "right-rows",
            "name": "right_rows",
            "kind": "constant",
            "valueType": "json",
            "defaultValue": [{"id": 2}],
        },
    ]
    workflow["nodes"] = [
        base_nodes["input-1"],
        {
            "id": "merge-1",
            "type": "data_merge",
            "position": {"x": 220, "y": 140},
            "data": {
                "kind": "data_merge",
                "contractVersion": 1,
                "mergeMode": "append",
                "leftVariable": "left_rows",
                "rightVariable": "right_rows",
                "outputVariable": "merged_rows",
                "keyFields": [],
            },
        },
        base_nodes["workflow-agent-1"],
        base_nodes["output-1"],
    ]
    workflow["edges"] = [
        {
            "id": "input-merge-left",
            "source": "input-1",
            "target": "merge-1",
            "targetHandle": "left",
        },
        {
            "id": "input-merge-right",
            "source": "input-1",
            "target": "merge-1",
            "targetHandle": "right",
        },
        {
            "id": "merge-agent",
            "source": "merge-1",
            "target": "workflow-agent-1",
        },
        {
            "id": "agent-output",
            "source": "workflow-agent-1",
            "target": "output-1",
        },
    ]

    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["parameter_extractor", "question_classifier", "content_policy"],
)
async def test_r19_typed_ai_and_policy_pass_xpert_publish_preflight(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    created = await client.post("/api/xperts", json={"name": f"R1.9 {kind}"})
    assert created.status_code == 200, created.text
    xpert = created.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base = {node["id"]: node for node in workflow["nodes"]}
    input_node = base["input-1"]
    agent_node = base["workflow-agent-1"]
    output_node = base["output-1"]

    if kind == "parameter_extractor":
        workflow["nodes"] = [
            input_node,
            {
                "id": "extractor-1",
                "type": "parameter_extractor",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "parameter_extractor",
                    "contractVersion": 2,
                    "inputVariable": "user_input",
                    "modelId": "test/model",
                    "outputVariable": "parameters",
                    "schemaMode": "fields",
                    "outputShape": "object",
                    "fields": [{"id": "field_1", "name": "topic", "description": "Request topic", "valueType": "string", "required": True, "nullable": False}],
                    "repairAttempts": 0,
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-extractor", "source": "input-1", "target": "extractor-1"},
            {"id": "extractor-agent", "source": "extractor-1", "target": "workflow-agent-1"},
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]
    elif kind == "question_classifier":
        workflow["nodes"] = [
            input_node,
            {
                "id": "classifier-1",
                "type": "question_classifier",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "question_classifier",
                    "contractVersion": 2,
                    "inputVariable": "user_input",
                    "outputVariable": "category",
                    "classificationMode": "rules_only",
                    "categoriesV2": [
                        {"id": "category_1", "label": "Support", "description": "", "keywords": ["help"], "matchMode": "contains_any"},
                        {"id": "category_2", "label": "Sales", "description": "", "keywords": ["buy"], "matchMode": "contains_any"},
                    ],
                    "caseSensitive": False,
                    "modelId": "",
                    "defaultLabel": "Other",
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-classifier", "source": "input-1", "target": "classifier-1"},
            *[
                {"id": f"classifier-agent-{handle}", "source": "classifier-1", "sourceHandle": handle, "target": "workflow-agent-1"}
                for handle in ("category_1", "category_2", "default")
            ],
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]
    else:
        workflow["nodes"] = [
            input_node,
            {
                "id": "policy-1",
                "type": "runtime_middleware",
                "position": {"x": 220, "y": 20},
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "content_policy",
                    "runtimeMiddlewareKind": "runtime_middleware.content_policy",
                    "middlewarePriority": "100",
                    "runtimeMiddlewareConfig": {
                        "phase": "both",
                        "rules": [{"id": "rule_1", "label": "Secrets", "detector": "secret_pattern", "action": "block", "terms": [], "caseSensitive": False}],
                    },
                },
            },
            agent_node,
            output_node,
        ]
        workflow["edges"] = [
            {"id": "input-agent", "source": "input-1", "target": "workflow-agent-1"},
            {"id": "policy-agent", "source": "policy-1", "sourceHandle": "middleware-binding", "target": "workflow-agent-1", "targetHandle": "middleware"},
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]

    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["parameter_extractor", "question_classifier"])
async def test_r19_v1_typed_ai_nodes_remain_publishable(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    created = await client.post("/api/xperts", json={"name": f"R1.9 V1 {kind}"})
    assert created.status_code == 200, created.text
    xpert = created.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base = {node["id"]: node for node in workflow["nodes"]}
    legacy_data: dict[str, Any]
    if kind == "parameter_extractor":
        legacy_data = {
            "kind": kind,
            "inputVariable": "user_input",
            "modelId": "test/model",
            "schema": "topic: Request topic",
            "outputVariable": "parameters_json",
        }
    else:
        legacy_data = {
            "kind": kind,
            "inputVariable": "user_input",
            "outputVariable": "category",
            "categories": '{"Support":["help"],"Sales":["buy"]}',
            "defaultCategory": "Other",
            "matchMode": "contains_any",
            "caseSensitive": "false",
            "useLlmFallback": "false",
            "modelId": "",
        }
    workflow["nodes"] = [
        base["input-1"],
        {
            "id": "legacy-node",
            "type": kind,
            "position": {"x": 220, "y": 140},
            "data": legacy_data,
        },
        base["workflow-agent-1"],
        base["output-1"],
    ]
    workflow["edges"] = [
        {"id": "input-legacy", "source": "input-1", "target": "legacy-node"},
        {
            "id": "legacy-agent",
            "source": "legacy-node",
            "target": "workflow-agent-1",
        },
        {
            "id": "agent-output",
            "source": "workflow-agent-1",
            "target": "output-1",
        },
    ]

    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
async def test_r17_secure_http_xpert_publish_is_fail_closed_by_feature_flag(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_response = await client.post("/api/xperts", json={"name": "R1.7 secure HTTP"})
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base_nodes = {node["id"]: node for node in workflow["nodes"]}
    workflow["nodes"] = [
        base_nodes["input-1"],
        {
            "id": "http-1",
            "type": "http_request",
            "position": {"x": 220, "y": 140},
            "data": {
                "kind": "http_request",
                "contractVersion": 2,
                "method": "GET",
                "url": "https://api.example.test/status",
                "queryItems": [],
                "headerItems": [],
                "bodyMode": "none",
                "formFields": [],
                "authType": "none",
                "timeoutSeconds": 30,
                "redirectLimit": 0,
                "responseLimitBytes": 1_048_576,
                "responseMode": "auto",
                "statusPolicy": "success_only",
                "outputVariable": "http_response",
            },
        },
        base_nodes["workflow-agent-1"],
        base_nodes["output-1"],
    ]
    workflow["edges"] = [
        {"id": "input-http", "source": "input-1", "target": "http-1"},
        {"id": "http-agent", "source": "http-1", "target": "workflow-agent-1"},
        {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
    ]
    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text

    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "false")
    blocked = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert blocked.status_code == 422
    assert any(
        issue["code"] == "xpert_http_requests_disabled"
        for issue in blocked.json()["detail"]["issues"]
    )

    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "feature_flag", "issue_code"),
    [
        ("file_output", "FILE_OUTPUT_ASSETS_ENABLED", "xpert_file_output_disabled"),
        (
            "document_extractor",
            "WORKFLOW_FILE_ASSETS_ENABLED",
            "xpert_workflow_files_disabled",
        ),
    ],
)
async def test_r18_private_xpert_file_nodes_are_fail_closed_by_feature_flag(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    feature_flag: str,
    issue_code: str,
) -> None:
    created_response = await client.post("/api/xperts", json={"name": f"R1.8 {kind}"})
    assert created_response.status_code == 200, created_response.text
    xpert = created_response.json()
    draft = xpert["draft"]
    workflow = draft["workflow"]
    base_nodes = {node["id"]: node for node in workflow["nodes"]}
    if kind == "file_output":
        data = {
            "kind": "file_output",
            "inputVariable": "user_input",
            "outputVariable": "generated_file",
            "format": "markdown",
            "filenameTemplate": "report",
            "titleTemplate": "",
            "columns": [],
        }
    else:
        data = {
            "kind": "document_extractor",
            "assetIdVariable": "selected_file_asset_id",
            "outputVariable": "document_text",
        }
    workflow["nodes"] = [
        base_nodes["input-1"],
        {
            "id": "r18-file-node",
            "type": kind,
            "position": {"x": 220, "y": 140},
            "data": data,
        },
        base_nodes["workflow-agent-1"],
        base_nodes["output-1"],
    ]
    workflow["edges"] = [
        {"id": "input-file", "source": "input-1", "target": "r18-file-node"},
        {"id": "file-agent", "source": "r18-file-node", "target": "workflow-agent-1"},
        {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
    ]
    updated = await client.patch(f"/api/xperts/{xpert['id']}", json={"draft": draft})
    assert updated.status_code == 200, updated.text

    monkeypatch.setenv(feature_flag, "false")
    blocked = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert blocked.status_code == 422
    assert any(
        issue["code"] == issue_code for issue in blocked.json()["detail"]["issues"]
    )

    monkeypatch.setenv(feature_flag, "true")
    published = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
async def test_r18_xpert_document_publish_rejects_unbound_or_legacy_file_sources(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    for source, expected_code in (
        (
            {"assetIdVariable": "user_input"},
            "xpert_document_asset_binding_required",
        ),
        (
            {"sourcePathVariable": "user_input"},
            "xpert_document_asset_migration_required",
        ),
    ):
        created_response = await client.post(
            "/api/xperts", json={"name": f"R1.8 invalid document {expected_code}"}
        )
        xpert = created_response.json()
        draft = xpert["draft"]
        workflow = draft["workflow"]
        base_nodes = {node["id"]: node for node in workflow["nodes"]}
        workflow["nodes"] = [
            base_nodes["input-1"],
            {
                "id": "document-1",
                "type": "document_extractor",
                "position": {"x": 220, "y": 140},
                "data": {
                    "kind": "document_extractor",
                    "outputVariable": "document_text",
                    **source,
                },
            },
            base_nodes["workflow-agent-1"],
            base_nodes["output-1"],
        ]
        workflow["edges"] = [
            {"id": "input-document", "source": "input-1", "target": "document-1"},
            {
                "id": "document-agent",
                "source": "document-1",
                "target": "workflow-agent-1",
            },
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]
        updated = await client.patch(
            f"/api/xperts/{xpert['id']}", json={"draft": draft}
        )
        assert updated.status_code == 200, updated.text
        blocked = await client.post(f"/api/xperts/{xpert['id']}/publish", json={})
        assert blocked.status_code == 422
        assert any(
            issue["code"] == expected_code
            for issue in blocked.json()["detail"]["issues"]
        )


@pytest.mark.asyncio
async def test_published_xpert_runs_immutable_snapshot_and_registers_trace(
    client: httpx.AsyncClient,
    xpert_store: XpertStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}

    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        captured["model_id"] = model_id
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        yield "published "
        yield "answer"

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    created = xpert_store.create_xpert(name="Immutable Runtime")
    version_one = xpert_store.publish_xpert(
        created.id,
        expected_revision=created.draft_revision,
    )
    old_prompt = str(_workflow_agent_data(version_one.workflow)["rolePrompt"])

    changed_draft = created.draft.model_copy(deep=True)
    _workflow_agent_data(changed_draft.workflow)["rolePrompt"] = "UNPUBLISHED DRAFT"
    xpert_store.update_xpert(
        created.id,
        {"draft": changed_draft.model_dump(mode="json")},
    )

    run_response = await client.post(
        f"/api/xperts/{created.id}/run",
        json={
            "message": "Create a launch plan",
            "messages": [
                {"role": "user", "content": "We discussed a staged rollout."},
                {"role": "assistant", "content": "I will preserve that context."},
            ],
            "version": 1,
        },
    )
    assert run_response.status_code == 200, run_response.text
    events = _parse_sse_events(run_response.text)
    meta = next(item for item in events if item.get("event") == "workflow_meta")
    assert meta["xpert_id"] == created.id
    assert meta["xpert_version"] == 1

    completed = next(item for item in events if item.get("event") == "workflow_end")
    assert completed["final_output"] == "published answer"
    assert captured["system_prompt"] == old_prompt
    assert "Create a launch plan" in str(captured["prompt"])
    assert "We discussed a staged rollout." in str(captured["prompt"])

    run_response = await client.get(f"/api/runtime/runs/{meta['run_id']}")
    assert run_response.status_code == 200
    runtime_run = run_response.json()
    assert runtime_run["run_type"] == "xpert"
    assert runtime_run["status"] == "completed"
    assert runtime_run["metadata"]["xpert_version"] == 1
    assert runtime_run["metadata"]["xpert_draft_revision"] == 1

    checkpoint_response = await client.get(
        f"/api/runtime/runs/{meta['run_id']}/checkpoints"
    )
    checkpoint_types = {item["event_type"] for item in checkpoint_response.json()}
    assert {"xpert.started", "xpert.completed"}.issubset(checkpoint_types)


@pytest.mark.asyncio
async def test_published_xpert_generates_and_persists_conversation_metadata(
    client: httpx.AsyncClient,
    xpert_store: XpertStore,
    xpert_context_store: XpertContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        yield "A concise answer."

    async def fake_enrichment(*args, **kwargs):
        return '{"title":"Launch plan","suggestions":["List the risks","Draft milestones"]}'

    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_stream)
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_enrichment)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    created = xpert_store.create_xpert(name="Conversation Features")
    draft = created.draft.model_copy(deep=True)
    draft.features.generated_questions.enabled = True
    draft.features.generated_questions.count = 2
    draft.features.conversation_title.enabled = True
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(
        created.id,
        expected_revision=updated.draft_revision,
    )
    conversation = xpert_context_store.create_conversation(created.id)

    response = await client.post(
        f"/api/xperts/{created.id}/run",
        json={
            "message": "Plan the launch",
            "conversation_id": conversation.conversation_id,
        },
    )
    assert response.status_code == 200, response.text
    completed = next(
        event
        for event in _parse_sse_events(response.text)
        if event.get("event") == "workflow_end"
    )
    assert completed["conversation_title"] == "Launch plan"
    assert completed["suggestions"] == ["List the risks", "Draft milestones"]

    restored = xpert_context_store.get_conversation(
        created.id,
        conversation.conversation_id,
    )
    assert restored.title == "Launch plan"
    assert restored.messages[-1].suggestions == [
        "List the risks",
        "Draft milestones",
    ]


@pytest.mark.asyncio
async def test_published_xpert_audio_features_use_fixed_version_gateway_contract(
    client: httpx.AsyncClient,
    xpert_store: XpertStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    class FakeGatewayResponse:
        def __init__(
            self,
            *,
            content: bytes,
            payload: dict[str, Any] | None = None,
            content_type: str,
        ) -> None:
            self.status_code = 200
            self.content = content
            self._payload = payload
            self.headers = {"content-type": content_type}

        def json(self) -> dict[str, Any]:
            return dict(self._payload or {})

    class FakeGatewayClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, **kwargs: Any):
            captured.append((url, kwargs))
            if url.endswith("/audio/transcriptions"):
                return FakeGatewayResponse(
                    content=b'{"text":"transcribed request"}',
                    payload={"text": "transcribed request"},
                    content_type="application/json",
                )
            return FakeGatewayResponse(
                content=b"fake-mp3",
                content_type="audio/mpeg",
            )

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://gateway.example/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeGatewayClient)

    created = xpert_store.create_xpert(name="Audio Features")
    draft = created.draft.model_copy(deep=True)
    draft.features.speech_to_text.enabled = True
    draft.features.speech_to_text.model_id = "speech-to-text-model"
    draft.features.text_to_speech.enabled = True
    draft.features.text_to_speech.model_id = "text-to-speech-model"
    draft.features.text_to_speech.voice = "calm"
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(
        created.id,
        expected_revision=updated.draft_revision,
    )

    capabilities = await client.get(
        f"/api/xperts/{created.id}/audio-capabilities",
        params={"version": 1},
    )
    assert capabilities.status_code == 200, capabilities.text
    assert capabilities.json()["text_to_speech"]["model_id"] == "text-to-speech-model"
    assert capabilities.json()["speech_to_text"]["model_id"] == "speech-to-text-model"

    transcription = await client.post(
        f"/api/xperts/{created.id}/audio/transcriptions",
        data={"version": "1"},
        files={"file": ("request.wav", b"wave-data", "audio/wav")},
    )
    assert transcription.status_code == 200, transcription.text
    assert transcription.json()["text"] == "transcribed request"

    speech = await client.post(
        f"/api/xperts/{created.id}/audio/speech",
        json={"text": "Read this response.", "version": 1},
    )
    assert speech.status_code == 200, speech.text
    assert speech.content == b"fake-mp3"
    assert speech.headers["x-modelmirror-xpert-version"] == "1"
    assert [item[0] for item in captured] == [
        "https://gateway.example/v1/audio/transcriptions",
        "https://gateway.example/v1/audio/speech",
    ]
    assert captured[1][1]["json"]["model"] == "text-to-speech-model"
    assert captured[1][1]["json"]["voice"] == "calm"


@pytest.mark.asyncio
async def test_unpublished_or_missing_xpert_cannot_run(
    client: httpx.AsyncClient,
    xpert_store: XpertStore,
) -> None:
    created = xpert_store.create_xpert(name="Draft only")

    draft_response = await client.post(
        f"/api/xperts/{created.id}/run",
        json={"message": "hello"},
    )
    assert draft_response.status_code == 409

    published = xpert_store.publish_xpert(
        created.id,
        expected_revision=created.draft_revision,
    )
    assert published.version == 1
    xpert_store.update_xpert(created.id, {"status": "archived"})
    archived_response = await client.post(
        f"/api/xperts/{created.id}/run",
        json={"message": "hello"},
    )
    assert archived_response.status_code == 409

    missing_response = await client.post(
        "/api/xperts/missing/run",
        json={"message": "hello"},
    )
    assert missing_response.status_code == 404


def _workflow_agent_data(workflow: Any) -> dict[str, Any]:
    for node in workflow.nodes:
        if node.data.get("kind") == "workflow_agent":
            return node.data
    raise AssertionError("workflow_agent node not found")


def _workflow_agent_data_dict(workflow: dict[str, Any]) -> dict[str, Any]:
    for node in workflow["nodes"]:
        if node["data"].get("kind") == "workflow_agent":
            return node["data"]
    raise AssertionError("workflow_agent node not found")


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        events.append(json.loads(line[5:].strip()))
    return events
