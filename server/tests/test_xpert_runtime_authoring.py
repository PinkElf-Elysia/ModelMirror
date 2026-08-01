from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server.skills.draft_store import WorkspaceSkillDraftStore
from server.xpert_runtime import (
    CapabilityRegistry,
    InMemoryToolAuditStore,
    MiddlewareContext,
    MiddlewarePipeline,
    ToolPermissionPolicy,
    run_tool_with_runtime,
)
from server.xpert_runtime import authoring_api
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import (
    AuthoringProposalConflictError,
    AuthoringProposalStore,
)
from server.xpert_runtime.authoring_toolset import (
    AuthoringToolsetProvider,
    register_authoring_toolset_capabilities,
)
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError
from server.xperts import XpertStore


def _service(tmp_path: Path) -> AuthoringService:
    return AuthoringService(
        AuthoringProposalStore(tmp_path / "runtime"),
        XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "runtime"),
    )


def _skill_payload() -> dict:
    return {
        "name": "Report Builder",
        "slug": "report-builder",
        "description": "Build a reviewed local report.",
        "skill_markdown": (
            "---\nname: Report Builder\n"
            "description: Build a reviewed local report.\n---\n\n"
            "Use the staged script to create the report.\n"
        ),
        "files": {"scripts/build.py": "print('report')\n"},
    }


def test_proposals_persist_and_reject_stale_revisions(tmp_path: Path) -> None:
    store = AuthoringProposalStore(tmp_path)
    proposal = store.create(
        kind="xpert_create",
        title="Create helper",
        payload={"name": "Helper"},
        source_type="conversation",
        source_id="conversation-1",
        source_run_id="run-1",
    )
    updated = store.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        title="Create reviewed helper",
    )

    with pytest.raises(AuthoringProposalConflictError):
        store.update_pending(
            proposal.proposal_id,
            revision=proposal.revision,
            title="stale edit",
        )

    restored = AuthoringProposalStore(tmp_path).require(proposal.proposal_id)
    assert restored.title == "Create reviewed helper"
    assert restored.revision == updated.revision
    assert "Helper" not in json.dumps(
        AuthoringProposalStore.serialize(restored), ensure_ascii=False
    )


def test_approval_creates_xpert_draft_without_publishing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    proposal = service.proposal_store.create(
        kind="xpert_create",
        title="Create research helper",
        payload={
            "name": "Research Helper",
            "slug": "research-helper",
            "description": "Draft generated through a reviewed proposal.",
        },
        source_type="xpert",
        source_id="source-xpert",
        source_xpert_id="source-xpert",
    )

    assert service.xpert_store.list_xperts() == []
    validated = service.validate(proposal.proposal_id, revision=proposal.revision)
    assert validated.validation["valid"] is True
    assert service.xpert_store.list_xperts() == []

    approved = service.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        operator="reviewer",
    )
    created = service.xpert_store.get_xpert(approved.applied_resource_id or "")

    assert approved.status == "approved"
    assert created.status == "draft"
    assert created.published_version is None
    assert created.versions == []


def test_xpert_update_conflict_never_overwrites_human_draft(tmp_path: Path) -> None:
    service = _service(tmp_path)
    target = service.xpert_store.create_xpert(
        name="Existing Xpert", slug="existing-xpert"
    )
    proposal = service.proposal_store.create(
        kind="xpert_update",
        title="Change draft",
        payload={"patch": {"description": "Agent proposal"}},
        target_id=target.id,
        base_revision=target.draft_revision,
        source_type="conversation",
        source_id="conversation-2",
    )
    changed_draft = target.draft.model_copy(deep=True)
    changed_draft.workflow.title = "Human edit"
    service.xpert_store.update_xpert(
        target.id, {"draft": changed_draft.model_dump(mode="json")}
    )

    result = service.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        operator="reviewer",
    )
    current = service.xpert_store.get_xpert(target.id)

    assert result.status == "conflict"
    assert current.description == ""
    assert current.draft.workflow.title == "Human edit"


def test_skill_approval_only_creates_workspace_draft(tmp_path: Path) -> None:
    service = _service(tmp_path)
    proposal = service.proposal_store.create(
        kind="skill_create",
        title="Create report skill",
        payload=_skill_payload(),
        source_type="goal",
        source_id="goal-1",
    )

    approved = service.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        operator="reviewer",
    )
    draft = service.skill_draft_store.require(approved.applied_resource_id or "")

    assert approved.status == "approved"
    assert draft.status == "draft"
    assert draft.installed_skill_id is None
    assert draft.source_proposal_id == proposal.proposal_id


@pytest.mark.asyncio
async def test_authoring_tools_are_scoped_and_proposal_only(tmp_path: Path) -> None:
    service = _service(tmp_path)
    target = service.xpert_store.create_xpert(name="Target", slug="target")
    provider = AuthoringToolsetProvider(service, "xpert")
    metadata = {
        "runtime_run_type": "xpert",
        "xpert_id": "source-xpert",
        "run_id": "run-7",
        "xpert_authoring_config": {
            "allow_create": True,
            "allow_update": True,
            "allowed_xpert_ids": target.id,
        },
    }

    result = await provider.call_tool(
        RuntimeToolCall(
            "xpert_authoring_propose_update",
            {
                "title": "Update target",
                "xpert_id": target.id,
                "base_revision": target.draft_revision,
                "patch": {"description": "Proposed only"},
            },
            metadata,
        )
    )
    proposal = json.loads(result.output)

    assert proposal["status"] == "pending"
    assert service.xpert_store.get_xpert(target.id).description == ""

    with pytest.raises(RuntimeToolError, match="explicit authoring scope"):
        await provider.call_tool(
            RuntimeToolCall(
                "xpert_authoring_get_draft",
                {"xpert_id": "other-xpert"},
                metadata,
            )
        )
    with pytest.raises(RuntimeToolError, match="Public Xpert Apps"):
        await provider.call_tool(
            RuntimeToolCall(
                "xpert_authoring_catalog",
                {},
                {**metadata, "runtime_run_type": "xpert_app"},
            )
        )


@pytest.mark.asyncio
async def test_authoring_catalog_returns_summaries_without_draft_content(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    xpert = service.xpert_store.create_xpert(name="Catalog Xpert")
    skill = service.skill_draft_store.create(
        name="Catalog Skill",
        slug="catalog-skill",
        description="Safe summary",
        skill_markdown="---\nname: Catalog Skill\ndescription: Safe summary\n---\n\n# Secret instructions",
        files={"references/private.md": "private reference"},
    )
    metadata = {
        "runtime_run_type": "xpert",
        "xpert_id": xpert.id,
        "run_id": "run-catalog",
        "xpert_authoring_config": {},
        "skill_creator_config": {},
    }

    xpert_result = await AuthoringToolsetProvider(service, "xpert").call_tool(
        RuntimeToolCall("xpert_authoring_catalog", {}, metadata)
    )
    skill_result = await AuthoringToolsetProvider(service, "skill").call_tool(
        RuntimeToolCall("skill_authoring_catalog", {}, metadata)
    )
    xpert_catalog = json.loads(xpert_result.output)
    skill_catalog = json.loads(skill_result.output)

    assert xpert_catalog[0]["id"] == xpert.id
    assert "draft" not in xpert_catalog[0]
    assert "versions" not in xpert_catalog[0]
    assert skill_catalog[0]["draft_id"] == skill.draft_id
    assert "skill_markdown" not in skill_catalog[0]
    assert "files" not in skill_catalog[0]
    assert "Secret instructions" not in skill_result.output
    assert "private reference" not in skill_result.output


@pytest.mark.asyncio
async def test_authoring_tool_runs_through_policy_and_audit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    xpert_provider = AuthoringToolsetProvider(service, "xpert")
    skill_provider = AuthoringToolsetProvider(service, "skill")
    registry = CapabilityRegistry()
    register_authoring_toolset_capabilities(
        registry, xpert_provider, skill_provider
    )
    audit = InMemoryToolAuditStore()
    call = RuntimeToolCall(
        "xpert_authoring_propose_create",
        {"title": "Policy checked", "xpert": {"name": "Policy Draft"}},
        {
            "runtime_run_type": "xpert",
            "xpert_id": "source-xpert",
            "run_id": "run-policy",
            "xpert_authoring_config": {"allow_create": True},
        },
    )

    result = await run_tool_with_runtime(
        call,
        registry,
        MiddlewarePipeline([]),
        MiddlewareContext(task_id="authoring-task"),
        capability_name="xpert_authoring_tools",
        policy=ToolPermissionPolicy(
            allowed_tools={"xpert_authoring_propose_create"},
            allow_by_default=False,
        ),
        audit_store=audit,
    )

    assert json.loads(result.output)["status"] == "pending"
    records = await audit.list_records(tool_name="xpert_authoring_propose_create")
    assert records[-1].status == "succeeded"
    assert registry.require("xpert_authoring_tools").metadata["app_forbidden"] is True


@pytest.mark.asyncio
async def test_authoring_api_applies_revision_guard_and_draft_only_approval(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    proposal = service.proposal_store.create(
        kind="xpert_create",
        title="API proposal",
        payload={"name": "API Draft", "slug": "api-draft"},
        source_type="workflow",
        source_id="task-1",
    )
    previous = authoring_api._service
    authoring_api.configure_runtime_authoring(service)
    app = FastAPI()
    app.include_router(authoring_api.router)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            stale = await client.post(
                f"/api/runtime/authoring-proposals/{proposal.proposal_id}/approve",
                json={"revision": proposal.revision + 1, "operator": "reviewer"},
            )
            assert stale.status_code == 409

            approved = await client.post(
                f"/api/runtime/authoring-proposals/{proposal.proposal_id}/approve",
                json={"revision": proposal.revision, "operator": "reviewer"},
            )
            assert approved.status_code == 200, approved.text
            body = approved.json()
            created = service.xpert_store.get_xpert(body["applied_resource_id"])
            assert created.status == "draft"
            assert created.published_version is None
    finally:
        authoring_api._service = previous
