from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    compile_xpert_candidate,
    validate_blueprint_authorization,
)
from server.meta_agent.schemas import (
    MetaPlannerAgentBlueprint,
    MetaPlannerBlueprint,
    MetaPlannerGenerateRequest,
    MetaPlannerIRControlEdge,
    MetaPlannerIRFinalOutput,
    MetaPlannerIRInputBinding,
    MetaPlannerIRMiddlewareBinding,
    MetaPlannerIRNode,
    MetaPlannerIROutputBinding,
    MetaPlannerIRResourceBinding,
    MetaPlannerMiddlewareBinding,
    MetaPlannerResourceBinding,
    MetaPlannerScope,
    MetaPlannerTask,
    MetaPlannerTaskPlan,
    MetaPlannerTypedBlueprintV2,
    MetaPlannerWorkflowAgentConfig,
)
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.workflow_native.validate import validate_workflow_graph
from server.workflow_native.schemas import NativeWorkflowDefinition, NativeWorkflowNode
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import (
    AuthoringProposalStore,
    AuthoringProposalValidationError,
)
from server.xpert_runtime.middleware_registry import runtime_middleware_registry
from server.xpert_runtime.workflow_node_registry import workflow_node_registry
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


client = TestClient(app)


def _resource(
    resource_id: str,
    name: str,
    *,
    kind: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=resource_id,
        slug=resource_id,
        name=name,
        description=f"{name} description",
        status="published",
        published_version=2,
        kind=kind,
        aliases=["review"],
    )


def _snapshot():
    return build_capability_snapshot(
        workflow_registry=workflow_node_registry,
        middleware_registry=runtime_middleware_registry,
        external_xperts=[_resource("xpert-researcher", "Researcher")],
        knowledge_bases=[
            {
                "id": "kb-docs",
                "name": "Product docs",
                "active_version_id": "version-3",
            }
        ],
        toolsets=[_resource("toolset-search", "Search", kind="mcp")],
        plugins=[_resource("plugin-review", "Review plugin")],
        prompt_profiles=[_resource("prompt-review", "Review prompt")],
        model_ids=["model/planner", "model/agent"],
    )


def _request() -> MetaPlannerGenerateRequest:
    return MetaPlannerGenerateRequest(
        goal="Research a topic and produce a reviewed final answer.",
        planner_model_id="model/planner",
        default_agent_model_id="model/agent",
        max_agents=3,
        scope=MetaPlannerScope(
            allowed_node_kinds=[
                "input",
                "output",
                "workflow_agent",
                "external_xpert",
                "knowledge_base",
                "toolset_resource",
                "plugin_resource",
            ],
            external_xpert_ids=["xpert-researcher"],
            knowledge_base_ids=["kb-docs"],
            toolset_ids=["toolset-search"],
            plugin_ids=["plugin-review"],
            prompt_profile_ids=["prompt-review"],
            middleware_ids=["system_prompt_injector"],
        ),
    )


def _plan() -> MetaPlannerTaskPlan:
    return MetaPlannerTaskPlan(
        summary="Research, then synthesize.",
        assumptions=["The knowledge base is available."],
        tasks=[
            MetaPlannerTask(
                task_id="research",
                title="Research",
                objective="Collect evidence.",
                output_contract="Evidence summary.",
            ),
            MetaPlannerTask(
                task_id="deliver",
                title="Deliver",
                objective="Write the final response.",
                depends_on=["research"],
                input_contract=["research_output"],
                output_contract="Final response.",
            ),
        ],
    )


def _blueprint() -> MetaPlannerBlueprint:
    return MetaPlannerBlueprint(
        name="Research and review",
        description="A bounded research workflow.",
        tags=["research"],
        starters=["Research this topic"],
        agents=[
            MetaPlannerAgentBlueprint(
                task_id="research",
                name="Researcher",
                role_prompt="Find relevant, supportable evidence.",
                task_input="{{user_input}}",
                output_variable="research_output",
            ),
            MetaPlannerAgentBlueprint(
                task_id="deliver",
                name="Writer",
                role_prompt="Write a concise final response using the evidence.",
                task_input="{{research_output}}",
                output_variable="agent_output",
            ),
        ],
        resources=[
            MetaPlannerResourceBinding(
                task_id="research",
                kind="external_xpert",
                resource_id="xpert-researcher",
                tool_name="research_specialist",
            ),
            MetaPlannerResourceBinding(
                task_id="research",
                kind="knowledge_base",
                resource_id="kb-docs",
            ),
            MetaPlannerResourceBinding(
                task_id="research",
                kind="toolset_resource",
                resource_id="toolset-search",
            ),
            MetaPlannerResourceBinding(
                task_id="deliver",
                kind="plugin_resource",
                resource_id="plugin-review",
            ),
        ],
        middleware=[
            MetaPlannerMiddlewareBinding(
                task_id="deliver",
                middleware_id="system_prompt_injector",
                priority=20,
                config={"system_prompt": "Do not invent unsupported claims."},
            )
        ],
        prompt_profile_ids=["prompt-review"],
    )


def _typed_blueprint() -> MetaPlannerTypedBlueprintV2:
    return MetaPlannerTypedBlueprintV2(
        name="Research and review",
        description="A bounded research workflow.",
        tags=["research"],
        starters=["Research this topic"],
        nodes=[
            MetaPlannerIRNode(
                ref="researcher",
                kind="workflow_agent",
                title="Researcher",
                description="Collect evidence.",
                task_ids=["research"],
                inputs=[
                    MetaPlannerIRInputBinding(
                        port="request",
                        variable="user_input",
                        value_type="string",
                    )
                ],
                outputs=[
                    MetaPlannerIROutputBinding(
                        port="result",
                        variable="research_output",
                        value_type="string",
                    )
                ],
                config=MetaPlannerWorkflowAgentConfig(
                    role_prompt="Find relevant, supportable evidence.",
                    task_input="{{user_input}}",
                ).model_dump(mode="json"),
            ),
            MetaPlannerIRNode(
                ref="writer",
                kind="workflow_agent",
                title="Writer",
                description="Write the final response.",
                task_ids=["deliver"],
                inputs=[
                    MetaPlannerIRInputBinding(
                        port="evidence",
                        variable="research_output",
                        value_type="string",
                    )
                ],
                outputs=[
                    MetaPlannerIROutputBinding(
                        port="result",
                        variable="agent_output",
                        value_type="string",
                    )
                ],
                config=MetaPlannerWorkflowAgentConfig(
                    role_prompt="Write a concise final response using the evidence.",
                    task_input="{{research_output}}",
                ).model_dump(mode="json"),
            ),
        ],
        control_edges=[
            MetaPlannerIRControlEdge(
                source_ref="researcher", target_ref="writer"
            )
        ],
        resources=[
            MetaPlannerIRResourceBinding(
                target_ref="researcher",
                kind="external_xpert",
                resource_id="xpert-researcher",
                tool_name="research_specialist",
            ),
            MetaPlannerIRResourceBinding(
                target_ref="researcher",
                kind="knowledge_base",
                resource_id="kb-docs",
            ),
            MetaPlannerIRResourceBinding(
                target_ref="researcher",
                kind="toolset_resource",
                resource_id="toolset-search",
            ),
            MetaPlannerIRResourceBinding(
                target_ref="writer",
                kind="plugin_resource",
                resource_id="plugin-review",
            ),
        ],
        middleware=[
            MetaPlannerIRMiddlewareBinding(
                target_ref="writer",
                middleware_id="system_prompt_injector",
                priority=20,
                config={"system_prompt": "Do not invent unsupported claims."},
            )
        ],
        prompt_profile_ids=["prompt-review"],
        final_output=MetaPlannerIRFinalOutput(
            node_ref="writer", variable="agent_output"
        ),
    )


def test_capability_snapshot_defaults_exclude_high_risk_middleware():
    snapshot = _snapshot()
    high_risk = {
        item["id"]
        for item in snapshot.middleware
        if item["high_risk"]
    }

    assert snapshot.snapshot_hash
    assert "browser_automation" in high_risk
    assert "browser_automation" not in snapshot.default_scope.middleware_ids
    workflow_agent = next(
        item for item in snapshot.nodes if item["kind"] == "workflow_agent"
    )
    assert workflow_agent["planner"]["default_data"]["outputVariable"] == "agent_output"
    assert "modelId" in workflow_agent["planner"]["config_constraints"]["required"]
    assert {item["id"] for item in snapshot.external_xperts} == {
        "xpert-researcher"
    }
    serialized = json.dumps(snapshot.model_dump(mode="json"))
    assert "credential" not in serialized.lower()
    assert "api_key" not in serialized.lower()
    assert "sk-" not in serialized.lower()


def test_capability_api_returns_stable_safe_contract():
    response = client.get("/api/meta-agent/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "evoagentx-meta-planner-capabilities-v2"
    assert payload["snapshot_hash"]
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["middleware"], list)
    assert "default_scope" in payload


def test_capability_snapshot_only_exposes_compilable_node_kinds():
    snapshot = _snapshot()
    kinds = {item["kind"] for item in snapshot.nodes}

    assert kinds == {
        "input",
        "output",
        "workflow_agent",
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
    }
    assert all(item["planner"]["compilable"] for item in snapshot.nodes)
    assert all(item["planner"]["ir_version"] == 2 for item in snapshot.nodes)


def test_compiler_creates_control_and_five_binding_edge_shapes():
    snapshot = _snapshot()
    request = _request()
    plan = _plan()
    blueprint = _blueprint()

    assert not validate_blueprint_authorization(
        request, plan, blueprint, snapshot
    )
    candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=blueprint,
        snapshot=snapshot,
        target=None,
    )
    workflow = candidate["draft"]["workflow"]
    handles = {
        edge.get("targetHandle")
        for edge in workflow["edges"]
        if edge.get("targetHandle")
    }

    assert handles == {"expert", "knowledge", "toolset", "plugin", "middleware"}
    assert validate_workflow_graph(
        NativeWorkflowDefinition.model_validate(workflow)
    ).valid
    assert candidate["draft"]["prompt_profiles"] == [
        {
            "profile_id": "prompt-review",
            "version_policy": "pinned",
            "pinned_version": 2,
            "enabled": True,
        }
    ]


def test_typed_ir_allows_one_agent_to_cover_multiple_tasks():
    plan = _plan()
    blueprint = MetaPlannerTypedBlueprintV2(
        name="Combined specialist",
        nodes=[
            MetaPlannerIRNode(
                ref="specialist",
                kind="workflow_agent",
                title="Research writer",
                task_ids=["research", "deliver"],
                inputs=[
                    MetaPlannerIRInputBinding(
                        port="request", variable="user_input", value_type="string"
                    )
                ],
                outputs=[
                    MetaPlannerIROutputBinding(
                        port="result",
                        variable="agent_output",
                        value_type="string",
                    )
                ],
                config=MetaPlannerWorkflowAgentConfig(
                    role_prompt="Research the request and write the final answer.",
                    task_input="{{user_input}}",
                ).model_dump(mode="json"),
            )
        ],
        final_output=MetaPlannerIRFinalOutput(
            node_ref="specialist", variable="agent_output"
        ),
    )

    assert not validate_blueprint_authorization(
        _request(), plan, blueprint, _snapshot()
    )
    candidate = compile_xpert_candidate(
        request=_request(),
        plan=plan,
        blueprint=blueprint,
        snapshot=_snapshot(),
        target=None,
    )
    assert [
        node["type"] for node in candidate["draft"]["workflow"]["nodes"]
    ].count("workflow_agent") == 1


def test_typed_ir_rejects_ambiguous_terminal_nodes():
    blueprint = _typed_blueprint().model_copy(deep=True)
    blueprint.control_edges = []
    issues = validate_blueprint_authorization(
        _request(), _plan(), blueprint, _snapshot()
    )
    assert any("exactly one terminal node" in issue for issue in issues)


def test_typed_ir_rejects_final_output_variable_not_produced_by_terminal():
    blueprint = _typed_blueprint().model_copy(deep=True)
    blueprint.final_output.variable = "missing_output"
    issues = validate_blueprint_authorization(
        _request(), _plan(), blueprint, _snapshot()
    )
    assert any("is not produced" in issue for issue in issues)


def test_typed_ir_compilation_is_deterministic():
    kwargs = {
        "request": _request(),
        "plan": _plan(),
        "blueprint": _typed_blueprint(),
        "snapshot": _snapshot(),
        "target": None,
    }
    first = compile_xpert_candidate(**kwargs)
    second = compile_xpert_candidate(**kwargs)

    assert first["draft"]["workflow"] == second["draft"]["workflow"]


def test_unauthorized_resource_is_rejected():
    request = _request()
    request.scope.external_xpert_ids = []
    issues = validate_blueprint_authorization(
        request, _plan(), _blueprint(), _snapshot()
    )
    assert any("not authorized" in issue for issue in issues)


@pytest.mark.asyncio
async def test_generation_persists_proposal_and_uses_at_most_one_repair(
    tmp_path: Path,
):
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")
    skill_store = WorkspaceSkillDraftStore(tmp_path / "skills")

    def preflight(candidate):
        result = validate_xpert_definition(candidate)
        return result, candidate.draft.workflow, []

    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        skill_store,
        xpert_preflight=preflight,
    )
    outputs = [
        _plan().model_dump_json(),
        json.dumps({"name": "invalid"}, ensure_ascii=False),
        _typed_blueprint().model_dump_json(),
    ]
    calls = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append(
            {
                "model_id": model_id,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return outputs.pop(0)

    service = MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=preflight,
        completion=complete,
    )
    response = await service.generate(_request(), _snapshot())

    assert len(calls) == 3
    assert calls[-1]["temperature"] == 0
    assert response.repair_used is True
    proposal = proposal_store.require(response.proposal_id)
    assert proposal.source_type == "meta_planner"
    assert proposal.status == "pending"
    assert proposal.validation["valid"] is True
    assert xpert_store.list_xperts() == []

    approved = authoring.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        operator="test",
    )
    assert approved.status == "approved"
    created = xpert_store.get_xpert(approved.applied_resource_id or "")
    assert created.published_version is None
    assert created.status == "draft"


@pytest.mark.asyncio
async def test_failed_repair_persists_unapprovable_candidate_until_human_edit(
    tmp_path: Path,
):
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")
    skill_store = WorkspaceSkillDraftStore(tmp_path / "skills")

    def preflight(candidate):
        result = validate_xpert_definition(candidate)
        return result, candidate.draft.workflow, []

    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        skill_store,
        xpert_preflight=preflight,
    )
    outputs = [
        _plan().model_dump_json(),
        json.dumps({"name": "invalid"}, ensure_ascii=False),
        json.dumps({"name": "still invalid"}, ensure_ascii=False),
    ]

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        return outputs.pop(0)

    service = MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=preflight,
        completion=complete,
    )
    response = await service.generate(_request(), _snapshot())

    assert response.repair_used is True
    assert response.validation["valid"] is False
    assert "still invalid" not in json.dumps(response.model_dump(mode="json"))
    proposal = proposal_store.require(response.proposal_id)
    assert proposal.status == "pending"
    assert proposal.validation["valid"] is False
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(
            proposal.proposal_id,
            revision=proposal.revision,
            operator="test",
        )

    edited_payload = json.loads(json.dumps(proposal.payload))
    workflow_nodes = edited_payload["draft"]["workflow"]["nodes"]
    next(
        node for node in workflow_nodes if node["type"] == "workflow_agent"
    )["data"]["modelId"] = "model/agent"
    edited = proposal_store.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=edited_payload,
    )
    validated = authoring.validate(
        edited.proposal_id,
        revision=edited.revision,
    )
    assert validated.validation["valid"] is True


@pytest.mark.asyncio
async def test_update_with_unsupported_target_node_fails_before_model_call(
    tmp_path: Path,
):
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")
    skill_store = WorkspaceSkillDraftStore(tmp_path / "skills")
    target = xpert_store.create_xpert(name="Typed target")
    target.draft.workflow.nodes.append(
        NativeWorkflowNode(
            id="json-node",
            type="json_serialize",
            data={"kind": "json_serialize", "outputVariable": "serialized"},
        )
    )

    def preflight(candidate):
        result = validate_xpert_definition(candidate)
        return result, candidate.draft.workflow, []

    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        skill_store,
        xpert_preflight=preflight,
    )
    calls = 0

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        nonlocal calls
        calls += 1
        raise AssertionError("completion must not be called")

    service = MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=preflight,
        completion=complete,
    )
    request = _request().model_copy(
        update={"mode": "update", "target_xpert_id": target.id}
    )

    with pytest.raises(ValueError, match="cannot safely round-trip"):
        await service.generate(request, _snapshot(), target=target)
    assert calls == 0
    assert proposal_store.list(status="pending") == []
