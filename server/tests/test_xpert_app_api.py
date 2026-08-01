from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app
from server.xperts import (
    XpertAppStore,
    XpertStore,
    set_xpert_app_store_for_tests,
    set_xpert_store_for_tests,
)
from server.xperts.app_models import XpertAppLimits
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy
from server.xperts.app_store import (
    XpertAppAccessController,
    XpertAppAuthenticationError,
    XpertAppQuotaError,
)


@pytest.fixture
def stores(tmp_path: Path):
    xpert_store = XpertStore(tmp_path / "xperts")
    app_store = XpertAppStore(tmp_path / "apps")
    set_xpert_store_for_tests(xpert_store)
    set_xpert_app_store_for_tests(app_store)
    yield xpert_store, app_store
    set_xpert_app_store_for_tests(None)
    set_xpert_store_for_tests(None)


@pytest_asyncio.fixture
async def client(stores):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _workflow_agent_data(workflow: Any) -> dict[str, Any]:
    nodes = workflow.nodes if hasattr(workflow, "nodes") else workflow["nodes"]
    for node in nodes:
        data = node.data if hasattr(node, "data") else node["data"]
        if data.get("kind") == "workflow_agent":
            return data
    raise AssertionError("workflow_agent node not found")


async def _create_deployed_app(
    client: httpx.AsyncClient,
    xpert_store: XpertStore,
    *,
    slug: str = "public-helper",
) -> tuple[Any, dict[str, Any], str]:
    created = xpert_store.create_xpert(name="Public Helper", slug=slug)
    xpert_store.publish_xpert(created.id, expected_revision=created.draft_revision)
    create = await client.post(f"/api/xperts/{created.id}/app", json={})
    assert create.status_code == 200, create.text
    created_app = create.json()
    deploy = await client.post(
        f"/api/xpert-apps/{created_app['app']['app_id']}/deploy",
        json={"version": 1, "release_notes": "Public v1"},
    )
    assert deploy.status_code == 200, deploy.text
    return created, deploy.json()["app"], created_app["share_token"]


def test_xpert_app_store_persists_hashes_deployments_and_rollback(
    stores,
) -> None:
    _, app_store = stores
    app, share_token = app_store.create_app(
        xpert_id="xpert-1",
        slug="release-app",
        name="Release App",
    )
    app_store.deploy_app(app.app_id, version=1, release_notes="v1")
    app_store.deploy_app(app.app_id, version=2, release_notes="v2")
    rolled_back = app_store.deploy_app(app.app_id, version=1, release_notes="rollback")
    _, key, api_key = app_store.create_api_key(app.app_id, name="CI client")

    reloaded = XpertAppStore(app_store.storage_dir).get_app(app.app_id)
    raw_storage = app_store.storage_path.read_text(encoding="utf-8")

    assert reloaded.pinned_version == 1
    assert reloaded.deployment_revision == 3
    assert [item.version for item in reloaded.deployments] == [1, 2, 1]
    assert share_token not in raw_storage
    assert api_key not in raw_storage
    assert reloaded.share_token_hash in raw_storage
    assert key.key_hash in raw_storage


@pytest.mark.asyncio
async def test_app_access_controller_enforces_daily_and_revoked_keys(stores) -> None:
    _, store = stores
    app, _ = store.create_app(xpert_id="xpert-1", slug="quota-app", name="Quota")
    store.deploy_app(app.app_id, version=1)
    _, key, token = store.create_api_key(
        app.app_id,
        name="one-shot",
        limits=XpertAppLimits(
            requests_per_minute=10,
            requests_per_day=1,
            max_concurrency=2,
        ),
    )
    controller = XpertAppAccessController(store)
    grant = await controller.authorize("quota-app", token, access_type="api_key")
    await controller.release(grant)
    with pytest.raises(XpertAppQuotaError):
        await controller.authorize("quota-app", token, access_type="api_key")

    store.revoke_api_key(app.app_id, key.key_id)
    with pytest.raises(XpertAppAuthenticationError):
        store.authenticate("quota-app", token, access_type="api_key")

    _, _, expired_token = store.create_api_key(
        app.app_id,
        name="expired",
        expires_at=time.time() + 0.01,
    )
    await asyncio.sleep(0.02)
    with pytest.raises(XpertAppAuthenticationError):
        store.authenticate("quota-app", expired_token, access_type="api_key")


@pytest.mark.asyncio
async def test_app_access_controller_enforces_rpm_and_concurrency(stores) -> None:
    _, store = stores
    app, _ = store.create_app(xpert_id="xpert-1", slug="burst-app", name="Burst")
    store.deploy_app(app.app_id, version=1)
    _, _, token = store.create_api_key(
        app.app_id,
        name="burst",
        limits=XpertAppLimits(
            requests_per_minute=1,
            requests_per_day=10,
            max_concurrency=1,
        ),
    )
    controller = XpertAppAccessController(store)
    first = await controller.authorize("burst-app", token, access_type="api_key")
    with pytest.raises(XpertAppQuotaError, match="Requests per minute"):
        await controller.authorize("burst-app", token, access_type="api_key")
    await controller.release(first)

    _, _, concurrent_token = store.create_api_key(
        app.app_id,
        name="concurrent",
        limits=XpertAppLimits(
            requests_per_minute=10,
            requests_per_day=10,
            max_concurrency=1,
        ),
    )
    active = await controller.authorize(
        "burst-app",
        concurrent_token,
        access_type="api_key",
    )
    with pytest.raises(XpertAppQuotaError, match="Concurrent"):
        await controller.authorize(
            "burst-app",
            concurrent_token,
            access_type="api_key",
        )
    await controller.release(active)


@pytest.mark.asyncio
async def test_app_management_manifest_and_token_rotation(
    client: httpx.AsyncClient,
    stores,
) -> None:
    xpert_store, _ = stores
    _, deployed, share_token = await _create_deployed_app(client, xpert_store)

    manifest = await client.get(
        f"/api/apps/{deployed['slug']}/manifest",
        headers={"X-ModelMirror-App-Token": share_token},
    )
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["version"] == 1
    assert "workflow" not in manifest.text
    assert "share_token_hash" not in manifest.text

    rotate = await client.post(
        f"/api/xpert-apps/{deployed['app_id']}/share-token/rotate"
    )
    assert rotate.status_code == 200, rotate.text
    replacement = rotate.json()["share_token"]
    assert replacement != share_token

    old_manifest = await client.get(
        f"/api/apps/{deployed['slug']}/manifest",
        headers={"X-ModelMirror-App-Token": share_token},
    )
    assert old_manifest.status_code == 401
    new_manifest = await client.get(
        f"/api/apps/{deployed['slug']}/manifest",
        headers={"X-ModelMirror-App-Token": replacement},
    )
    assert new_manifest.status_code == 200


@pytest.mark.asyncio
async def test_deploy_preflight_requires_app_tool_policy(
    client: httpx.AsyncClient,
    stores,
) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Tool App", slug="tool-app")
    draft = created.draft.model_copy(deep=True)
    _workflow_agent_data(draft.workflow)["toolMode"] = "mcp_tools"
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(created.id, expected_revision=updated.draft_revision)
    create = await client.post(f"/api/xperts/{created.id}/app", json={})
    app_payload = create.json()["app"]

    denied = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert denied.status_code == 422
    assert denied.json()["detail"]["issues"][0]["code"] == "app_tools_not_allowed"

    update = await client.patch(
        f"/api/xpert-apps/{app_payload['app_id']}",
        json={"policy": {"allow_tools": True}},
    )
    assert update.status_code == 200, update.text
    missing_policy = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert missing_policy.status_code == 422
    assert missing_policy.json()["detail"]["issues"][0]["code"] == "app_tool_policy_required"


@pytest.mark.asyncio
async def test_deploy_preflight_requires_explicit_read_only_knowledge_policy(
    client: httpx.AsyncClient,
    stores,
) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Knowledge App", slug="knowledge-app")
    draft = created.draft.model_copy(deep=True)
    agent = _workflow_agent_data(draft.workflow)
    agent["toolMode"] = "mcp_tools"
    agent["knowledgeReadEnabled"] = "true"
    agent["knowledgeBaseIds"] = "kb-public"
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(created.id, expected_revision=updated.draft_revision)
    create = await client.post(f"/api/xperts/{created.id}/app", json={})
    app_payload = create.json()["app"]

    denied = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert denied.status_code == 422
    assert any(
        issue["code"] == "app_knowledge_read_not_allowed"
        for issue in denied.json()["detail"]["issues"]
    )

    update = await client.patch(
        f"/api/xpert-apps/{app_payload['app_id']}",
        json={"policy": {"allow_knowledge_read": True}},
    )
    assert update.status_code == 200, update.text
    deployed = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert deployed.status_code == 200, deployed.text


@pytest.mark.asyncio
async def test_deploy_preflight_always_rejects_dynamic_knowledge_write(
    client: httpx.AsyncClient,
    stores,
) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Writer App", slug="writer-app")
    draft = created.draft.model_copy(deep=True)
    agent = _workflow_agent_data(draft.workflow)
    agent["toolMode"] = "mcp_tools"
    agent["knowledgeWriteEnabled"] = "true"
    agent["knowledgeBaseIds"] = "kb-private"
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(created.id, expected_revision=updated.draft_revision)
    create = await client.post(f"/api/xperts/{created.id}/app", json={})
    app_payload = create.json()["app"]
    await client.patch(
        f"/api/xpert-apps/{app_payload['app_id']}",
        json={"policy": {"allow_knowledge_read": True, "allow_tools": True}},
    )

    denied = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert denied.status_code == 422
    assert any(
        issue["code"] == "app_knowledge_write_forbidden"
        for issue in denied.json()["detail"]["issues"]
    )


@pytest.mark.asyncio
async def test_deploy_preflight_requires_read_only_datax_policy(
    client: httpx.AsyncClient,
    stores,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    from server.datax.api import configure_datax
    from server.datax.service import DataXService
    from server.datax.store import DataXStore

    datax = DataXService(DataXStore(tmp_path / "datax-app"))
    project = datax.create_project(name="Public metrics")
    job = datax.import_source(
        project.project_id,
        file_name="metrics.csv",
        content=b"amount\n10\n",
    )
    assert job.status == "ready"
    source = datax.list_sources(project.project_id)[0]
    model = datax.create_model(
        project.project_id,
        name="Public model",
        entities=[
            {
                "entity_id": "metrics",
                "source_id": source.source_id,
                "alias": "metrics",
            }
        ],
        fields=[
            {
                "field_id": "amount",
                "entity_id": "metrics",
                "source_field": "amount",
                "name": "amount",
                "role": "measure",
            }
        ],
    )
    configure_datax(datax)
    request.addfinalizer(lambda: configure_datax(main_module.datax_service))
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Data X App", slug="datax-app")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    agent.data["toolMode"] = "mcp_tools"
    middleware = type(agent).model_validate(
        {
            "id": "datax-middleware",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "datax_indicators",
                "runtimeMiddlewareKind": "runtime_middleware.datax_indicators",
                "middlewarePriority": "50",
                "runtimeMiddlewareConfig": {
                    "projectIds": [project.project_id],
                    "modelIds": [model.model_id],
                    "allowProposals": False,
                    "maxResultRows": 100,
                },
            },
        }
    )
    draft.workflow.nodes.append(middleware)
    tool_policy = type(agent).model_validate(
        {
            "id": "datax-tool-policy",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "tool_policy",
                "runtimeMiddlewareKind": "runtime_middleware.tool_policy",
                "middlewarePriority": "20",
                "runtimeMiddlewareConfig": {"default_action": "allow"},
            },
        }
    )
    draft.workflow.nodes.append(tool_policy)
    draft.workflow.edges.append(
        type(draft.workflow.edges[0]).model_validate(
            {
                "id": "bind-datax",
                "source": middleware.id,
                "target": agent.id,
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    )
    draft.workflow.edges.append(
        type(draft.workflow.edges[0]).model_validate(
            {
                "id": "bind-datax-policy",
                "source": tool_policy.id,
                "target": agent.id,
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    )
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(created.id, expected_revision=updated.draft_revision)
    create = await client.post(f"/api/xperts/{created.id}/app", json={})
    app_payload = create.json()["app"]

    denied = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert denied.status_code == 422
    assert any(
        issue["code"] == "app_datax_read_not_allowed"
        for issue in denied.json()["detail"]["issues"]
    )

    update = await client.patch(
        f"/api/xpert-apps/{app_payload['app_id']}",
        json={"policy": {"allow_datax_read": True, "allow_tools": True}},
    )
    assert update.status_code == 200, update.text
    deployed = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )
    assert deployed.status_code == 200, deployed.text

    middleware.data["runtimeMiddlewareConfig"]["allowProposals"] = True
    changed = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(created.id, expected_revision=changed.draft_revision)
    write_denied = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 2},
    )
    assert write_denied.status_code == 422
    assert any(
        issue["code"] == "app_datax_write_forbidden"
        for issue in write_denied.json()["detail"]["issues"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive_kind", ["human_intervention", "human_in_the_loop"])
async def test_deploy_preflight_rejects_interactive_hitl(
    client: httpx.AsyncClient,
    stores,
    interactive_kind: str,
) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(
        name=f"Interactive {interactive_kind}",
        slug=f"interactive-{interactive_kind.replace('_', '-')}",
    )
    draft = created.draft.model_copy(deep=True)
    if interactive_kind == "human_intervention":
        output = next(
            node for node in draft.workflow.nodes if node.data.get("kind") == "output"
        )
        agent = next(
            node
            for node in draft.workflow.nodes
            if node.data.get("kind") == "workflow_agent"
        )
        draft.workflow.edges = [
            edge
            for edge in draft.workflow.edges
            if not (edge.source == agent.id and edge.target == output.id)
        ]
        draft.workflow.nodes.append(
            type(agent).model_validate(
                {
                    "id": "human-approval",
                    "type": "human_intervention",
                    "data": {
                        "kind": "human_intervention",
                        "prompt": "Approve the final response",
                        "outputVariable": "agent_output",
                    },
                }
            )
        )
        draft.workflow.edges.extend(
            [
                type(draft.workflow.edges[0]).model_validate(
                    {"id": "agent-human", "source": agent.id, "target": "human-approval"}
                ),
                type(draft.workflow.edges[0]).model_validate(
                    {"id": "human-output", "source": "human-approval", "target": output.id}
                ),
            ]
        )
    else:
        agent = next(
            node
            for node in draft.workflow.nodes
            if node.data.get("kind") == "workflow_agent"
        )
        draft.workflow.nodes.append(
            type(agent).model_validate(
                {
                    "id": "hitl-middleware",
                    "type": "runtime_middleware",
                    "data": {
                        "kind": "runtime_middleware",
                        "runtimeMiddlewareId": "human_in_the_loop",
                        "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                        "middlewarePriority": "40",
                        "runtimeMiddlewareConfig": {
                            "interrupt_on_tools": "*",
                            "final_confirmation": True,
                        },
                    },
                }
            )
        )
        draft.workflow.edges.append(
            type(draft.workflow.edges[0]).model_validate(
                {
                    "id": "bind-hitl",
                    "source": "hitl-middleware",
                    "target": agent.id,
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                }
            )
        )

    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    published = xpert_store.publish_xpert(
        created.id,
        expected_revision=updated.draft_revision,
    )
    assert published.version == 1
    create = await client.post(f"/api/xperts/{created.id}/app", json={})
    app_payload = create.json()["app"]
    denied = await client.post(
        f"/api/xpert-apps/{app_payload['app_id']}/deploy",
        json={"version": 1},
    )

    assert denied.status_code == 422
    assert any(
        issue["code"] == "app_interactive_hitl_forbidden"
        for issue in denied.json()["detail"]["issues"]
    )


def test_deploy_preflight_rejects_sandbox_runtime(stores) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Sandbox Helper", slug="sandbox-helper")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    draft.workflow.nodes.append(
        type(agent).model_validate(
            {
                "id": "sandbox-files",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "sandbox_files",
                    "runtimeMiddlewareKind": "runtime_middleware.sandbox_files",
                    "middlewarePriority": "20",
                    "runtimeMiddlewareConfig": {"quota_mb": 256},
                },
            }
        )
    )
    draft.workflow.edges.append(
        type(draft.workflow.edges[0]).model_validate(
            {
                "id": "bind-sandbox",
                "source": "sandbox-files",
                "target": agent.id,
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    result = _deployment_preflight(version, XpertAppPolicy())
    assert result["valid"] is False
    assert any(issue["code"] == "app_sandbox_forbidden" for issue in result["issues"])


def test_deploy_preflight_rejects_browser_runtime(stores) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Browser Helper", slug="browser-helper")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    agent.data["toolMode"] = "mcp_tools"
    middleware_nodes = [
        {
            "id": "browser",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "browser_automation",
                "runtimeMiddlewareKind": "runtime_middleware.browser_automation",
                "runtimeMiddlewareConfig": {
                    "networkPolicy": "public_with_domain_approval",
                    "approvalMode": "mutating",
                },
            },
        },
        {
            "id": "browser-hitl",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "*",
                    "timeout_seconds": 3600,
                },
            },
        },
    ]
    for raw in middleware_nodes:
        draft.workflow.nodes.append(type(agent).model_validate(raw))
        draft.workflow.edges.append(
            type(draft.workflow.edges[0]).model_validate(
                {
                    "id": f"bind-{raw['id']}",
                    "source": raw["id"],
                    "target": agent.id,
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                }
            )
        )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    result = _deployment_preflight(version, XpertAppPolicy())
    assert result["valid"] is False
    assert any(issue["code"] == "app_browser_forbidden" for issue in result["issues"])


def test_deploy_preflight_allows_read_only_file_memory_only_when_enabled(stores) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Memory Helper", slug="memory-helper")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    middleware = type(agent).model_validate(
        {
            "id": "file-memory",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "xpert_file_memory",
                "runtimeMiddlewareKind": "runtime_middleware.xpert_file_memory",
                "runtimeMiddlewareConfig": {
                    "recall_mode": "deterministic",
                    "writeback_enabled": False,
                },
            },
        }
    )
    draft.workflow.nodes.append(middleware)
    draft.workflow.edges.append(
        type(draft.workflow.edges[0]).model_validate(
            {
                "id": "bind-file-memory",
                "source": middleware.id,
                "target": agent.id,
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    read_only_version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    denied = _deployment_preflight(read_only_version, XpertAppPolicy())
    assert any(
        issue["code"] == "app_xpert_file_memory_not_allowed"
        for issue in denied["issues"]
    )
    allowed = _deployment_preflight(
        read_only_version,
        XpertAppPolicy(allow_xpert_memory=True),
    )
    assert allowed["valid"] is True, allowed["issues"]

    middleware.data["runtimeMiddlewareConfig"]["writeback_enabled"] = True
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    writeback_version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )
    writeback_denied = _deployment_preflight(
        writeback_version,
        XpertAppPolicy(allow_xpert_memory=True),
    )
    assert any(
        issue["code"] == "app_xpert_file_memory_write_forbidden"
        for issue in writeback_denied["issues"]
    )


def test_deploy_preflight_rejects_client_tools_runtime(stores) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Client Helper", slug="client-helper")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    agent.data["toolMode"] = "mcp_tools"
    for raw in [
        {
            "id": "client-tools",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "client_tools",
                "runtimeMiddlewareKind": "runtime_middleware.client_tools",
                "runtimeMiddlewareConfig": {
                    "clientHostId": "host_test",
                    "clientToolNames": "host_page_read,host_page_fill",
                    "clientToolTimeoutSeconds": 1800,
                    "requireBoundTab": True,
                },
            },
        },
        {
            "id": "client-hitl",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "host_page_fill",
                    "timeout_seconds": 3600,
                },
            },
        },
    ]:
        draft.workflow.nodes.append(type(agent).model_validate(raw))
        draft.workflow.edges.append(
            type(draft.workflow.edges[0]).model_validate(
                {
                    "id": f"bind-{raw['id']}",
                    "source": raw["id"],
                    "target": agent.id,
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                }
            )
        )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    result = _deployment_preflight(version, XpertAppPolicy())

    assert result["valid"] is False
    assert any(
        issue["code"] == "app_client_tools_forbidden"
        for issue in result["issues"]
    )


def test_deploy_preflight_rejects_office_automation_runtime(stores) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Office Helper", slug="office-helper")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    agent.data["toolMode"] = "mcp_tools"
    for raw in [
        {
            "id": "office-tools",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "office_automation",
                "runtimeMiddlewareKind": "runtime_middleware.office_automation",
                "runtimeMiddlewareConfig": {
                    "clientHostId": "host_office",
                    "host": "word",
                    "allowDeletes": False,
                    "allowImageInsert": False,
                    "timeoutSeconds": 1800,
                    "requireBoundDocument": True,
                },
            },
        },
        {
            "id": "office-hitl",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "human_in_the_loop",
                "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                "runtimeMiddlewareConfig": {
                    "interrupt_on_tools": "*",
                    "timeout_seconds": 3600,
                },
            },
        },
    ]:
        draft.workflow.nodes.append(type(agent).model_validate(raw))
        draft.workflow.edges.append(
            type(draft.workflow.edges[0]).model_validate(
                {
                    "id": f"bind-{raw['id']}",
                    "source": raw["id"],
                    "target": agent.id,
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                }
            )
        )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    result = _deployment_preflight(version, XpertAppPolicy(allow_tools=True))

    assert result["valid"] is False
    assert any(
        issue["code"] == "app_office_automation_forbidden"
        for issue in result["issues"]
    )


@pytest.mark.parametrize("middleware_id", ["xpert_authoring", "skill_creator"])
def test_deploy_preflight_rejects_authoring_middleware(
    stores, middleware_id: str
) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(
        name=f"Authoring {middleware_id}",
        slug=f"authoring-{middleware_id.replace('_', '-')}",
    )
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes
        if node.data.get("kind") == "workflow_agent"
    )
    agent.data["toolMode"] = "mcp_tools"
    raw = {
        "id": f"middleware-{middleware_id}",
        "type": "runtime_middleware",
        "data": {
            "kind": "runtime_middleware",
            "runtimeMiddlewareId": middleware_id,
            "runtimeMiddlewareKind": f"runtime_middleware.{middleware_id}",
            "runtimeMiddlewareConfig": {},
        },
    }
    draft.workflow.nodes.append(type(agent).model_validate(raw))
    draft.workflow.edges.append(
        type(draft.workflow.edges[0]).model_validate(
            {
                "id": f"bind-{middleware_id}",
                "source": raw["id"],
                "target": agent.id,
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    result = _deployment_preflight(version, XpertAppPolicy(allow_tools=True))
    codes = {issue["code"] for issue in result["issues"]}

    assert "app_middleware_contract_forbidden" in codes
    assert result["valid"] is False


def test_deploy_preflight_rejects_private_automation_and_writer(stores) -> None:
    xpert_store, _ = stores
    created = xpert_store.create_xpert(name="Automation Helper", slug="automation-helper")
    draft = created.draft.model_copy(deep=True)
    agent = next(
        node for node in draft.workflow.nodes if node.data.get("kind") == "workflow_agent"
    )
    agent.data["toolMode"] = "mcp_tools"
    for raw in [
        {
            "id": "scheduler",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "scheduler",
                "runtimeMiddlewareKind": "runtime_middleware.scheduler",
                "runtimeMiddlewareConfig": {
                    "allow_agent_create": True,
                    "default_timezone": "UTC",
                    "max_runs_per_day": 10,
                },
            },
        },
        {
            "id": "writer",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "knowledge_writer",
                "runtimeMiddlewareKind": "runtime_middleware.knowledge_writer",
                "runtimeMiddlewareConfig": {
                    "knowledge_base_id": "kb-private",
                    "auto_propose_verified_output": True,
                },
            },
        },
    ]:
        draft.workflow.nodes.append(type(agent).model_validate(raw))
        draft.workflow.edges.append(
            type(draft.workflow.edges[0]).model_validate(
                {
                    "id": f"bind-{raw['id']}",
                    "source": raw["id"],
                    "target": agent.id,
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                }
            )
        )
    updated = xpert_store.update_xpert(
        created.id, {"draft": draft.model_dump(mode="json")}
    )
    version = xpert_store.publish_xpert(
        created.id, expected_revision=updated.draft_revision
    )

    result = _deployment_preflight(version, XpertAppPolicy(allow_tools=True))
    issue_codes = {issue["code"] for issue in result["issues"]}

    assert result["valid"] is False
    assert "app_private_automation_forbidden" in issue_codes
    assert "app_knowledge_writer_forbidden" in issue_codes


@pytest.mark.asyncio
async def test_openai_json_and_sse_use_pinned_version_and_register_run(
    client: httpx.AsyncClient,
    stores,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xpert_store, _ = stores
    captured_prompts: list[str] = []

    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        captured_prompts.append(str(system_prompt or ""))
        yield "public answer"

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
    created, deployed, _ = await _create_deployed_app(client, xpert_store, slug="stable-app")
    key_response = await client.post(
        f"/api/xpert-apps/{deployed['app_id']}/keys",
        json={"name": "integration"},
    )
    api_key = key_response.json()["api_key"]
    headers = {"Authorization": f"Bearer {api_key}"}

    first = await client.post(
        f"/api/v1/xpert-apps/{deployed['slug']}/chat/completions",
        headers=headers,
        json={
            "messages": [
                {"role": "system", "content": "Do not override the published role."},
                {"role": "user", "content": "hello"},
            ]
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["choices"][0]["message"]["content"] == "public answer"
    run_id = first.headers["x-modelmirror-runtime-run-id"]
    run = await client.get(f"/api/runtime/runs/{run_id}")
    assert run.status_code == 200, run.text
    assert run.json()["run_type"] == "xpert_app"
    assert run.json()["metadata"]["app_id"] == deployed["app_id"]
    assert run.json()["metadata"]["credential_prefix"] == api_key[:16]

    draft = created.draft.model_copy(deep=True)
    _workflow_agent_data(draft.workflow)["rolePrompt"] = "UNPUBLISHED V2 ROLE"
    updated = xpert_store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    xpert_store.publish_xpert(created.id, expected_revision=updated.draft_revision)

    streamed = await client.post(
        f"/api/v1/xpert-apps/{deployed['slug']}/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "stream"}], "stream": True},
    )
    assert streamed.status_code == 200, streamed.text
    assert '"delta": {"content": "public answer"}' in streamed.text
    assert "data: [DONE]" in streamed.text
    assert all(prompt != "UNPUBLISHED V2 ROLE" for prompt in captured_prompts)


@pytest.mark.asyncio
async def test_disabled_app_and_revoked_key_are_rejected(
    client: httpx.AsyncClient,
    stores,
) -> None:
    xpert_store, _ = stores
    _, deployed, _ = await _create_deployed_app(client, xpert_store, slug="disabled-app")
    key_response = await client.post(
        f"/api/xpert-apps/{deployed['app_id']}/keys",
        json={"name": "temporary"},
    )
    key = key_response.json()["key"]
    api_key = key_response.json()["api_key"]
    revoke = await client.delete(
        f"/api/xpert-apps/{deployed['app_id']}/keys/{key['key_id']}"
    )
    assert revoke.status_code == 200
    rejected = await client.post(
        f"/api/v1/xpert-apps/{deployed['slug']}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert rejected.status_code == 401

    disable = await client.post(f"/api/xpert-apps/{deployed['app_id']}/disable")
    assert disable.status_code == 200
    assert disable.json()["app"]["status"] == "disabled"
