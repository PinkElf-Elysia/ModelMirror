from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.main import app
from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent,
    resolve_graph_intent,
    v2_to_graph_intent,
    workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    compile_xpert_candidate,
    validate_blueprint_authorization,
    validate_task_plan,
)
from server.meta_agent.schemas import (
    GraphIntentV3,
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


def test_generic_meta_planner_keeps_hitl_scoped_to_expert_team():
    plan = MetaPlannerTaskPlan(
        summary="A generic Meta Planner plan must remain expert-only.",
        tasks=[
            MetaPlannerTask(
                task_id="manual_gate",
                title="Manual gate",
                objective="Wait for a human decision.",
                depends_on=[],
                input_contract=["user_input"],
                output_contract="Decision",
                task_type="approval",
                interaction_prompt="Approve the next step.",
                output_variable="manual_gate_output",
            )
        ],
    )

    issues = validate_task_plan(plan, max_agents=3)

    assert issues == [
        "Generic Meta Planner task plans support expert tasks only; "
        "HITL is scoped to Expert Team: manual_gate."
    ]
    prompt = json.loads(
        MetaPlannerV2Service._plan_prompt(_request(), _snapshot())
    )
    task_properties = prompt["required_schema"]["$defs"]["MetaPlannerTask"][
        "properties"
    ]
    assert "task_type" not in task_properties
    assert "interaction_prompt" not in task_properties
    assert "output_variable" not in task_properties


def test_task_plan_rejects_unscoped_expert_bindings_before_blueprint():
    plan = _plan().model_copy(deep=True)
    plan.tasks[0].agent_id = "invented_release_architect"

    issues = validate_task_plan(
        plan,
        max_agents=3,
        authorized_agent_ids=set(),
    )

    assert issues == [
        "Task research binds unauthorized expert invented_release_architect."
    ]


def test_blueprint_and_repair_prompts_expose_typed_ir_agent_constraints():
    request = _request()
    plan = _plan()
    snapshot = _snapshot()
    plan_prompt = json.loads(
        MetaPlannerV2Service._plan_prompt(request, snapshot)
    )

    blueprint_prompt = json.loads(
        MetaPlannerV2Service._blueprint_prompt(request, plan, snapshot, None)
    )
    repair_prompt = json.loads(
        MetaPlannerV2Service._repair_prompt(
            request,
            plan,
            snapshot,
            '{"ir_version": 2}',
            ["Typed IR exceeds max_agents=3."],
        )
    )

    for prompt in (blueprint_prompt, repair_prompt):
        constraints = prompt["typed_ir_constraints"]
        graph_contract = prompt["graph_intent_contract"]
        assert constraints["max_workflow_agent_nodes"] == request.max_agents
        assert constraints["required_task_ids"] == [
            task.task_id for task in plan.tasks
        ]
        assert sorted(
            task_id
            for group in constraints["suggested_task_groups"]
            for task_id in group
        ) == sorted(task.task_id for task in plan.tasks)
        assert len(constraints["suggested_task_groups"]) <= request.max_agents
        assert constraints["task_dependencies"] == {
            task.task_id: task.depends_on for task in plan.tasks
        }
        assert constraints["task_agent_bindings"] == {
            task.task_id: task.agent_id for task in plan.tasks
        }
        assert constraints["authorized_agent_ids"] == request.scope.agent_ids
        assert constraints["workflow_agent_config_allowed_fields"] == list(
            MetaPlannerWorkflowAgentConfig.model_fields
        )
        assert constraints["workflow_agent_config_forbidden_fields"] == [
            "agent_id"
        ]
        assert any(
            "group compatible task_ids" in rule
            for rule in constraints["rules"]
        )
        assert graph_contract["required_ir_version"] == 3
        node_roles = graph_contract["node_roles"]
        assert node_roles["executable_node_kinds"] == ["workflow_agent"]
        assert node_roles["compiler_managed_node_kinds"] == ["input", "output"]
        assert node_roles["resource_binding_kinds"] == [
            "external_xpert",
            "knowledge_base",
            "plugin_resource",
            "toolset_resource",
        ]
        assert set(node_roles["executable_node_contracts"]) == {
            "workflow_agent"
        }
        assert (
            node_roles["executable_node_contracts"]["workflow_agent"][
                "task_binding"
            ]
            == "required"
        )
        assert graph_contract["workflow_agent"]["config_field_names"] == list(
            MetaPlannerWorkflowAgentConfig.model_fields
        )
        assert graph_contract["workflow_agent"]["input_port"] == {
            "name": "task",
            "cardinality": "many",
            "root_source_ref": "input",
            "root_source_port": "user_input",
            "root_variable": "user_input",
        }
        assert any(
            "never emit outputVariable" in rule
            for rule in graph_contract["rules"]
        )
        assert (
            prompt["capability_snapshot"]["graph_intent_contract"]
            == graph_contract
        )
        assert prompt["default_agent_model_id"] == request.default_agent_model_id
        example = GraphIntentV3.model_validate(prompt["canonical_minimal_example"])
        assert example.ir_version == 3
        assert [node.kind for node in example.nodes] == ["workflow_agent"]
        assert example.nodes[0].task_ids == [task.task_id for task in plan.tasks]
        assert example.nodes[0].inputs[0].port == "task"
        assert example.nodes[0].inputs[0].source_ref == "input"
        assert example.nodes[0].inputs[0].value_schema.type == "string"
        assert example.nodes[0].config["model_id"] == request.default_agent_model_id

    assert repair_prompt["validation_issues"] == [
        "Typed IR exceeds max_agents=3."
    ]
    assert plan_prompt["authorized_agent_ids"] == request.scope.agent_ids
    assert any(
        "omit agent_id or set it to null" in rule
        for rule in plan_prompt["rules"]
    )


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
    assert payload["version"] == "evoagentx-meta-planner-capabilities-v6"
    assert payload["authoring_protocol_version"] == 1
    assert payload["authoring_limits"]["max_operations"] == 64
    assert payload["ir_version"] == 3
    assert payload["supported_ir_versions"] == [2, 3]
    assert payload["contract_version"] == 3
    assert len(payload["contract_checksum"]) == 64
    assert payload["snapshot_hash"]
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["middleware"], list)
    assert "default_scope" in payload


def test_capability_snapshot_only_exposes_compilable_node_kinds():
    snapshot = _snapshot()
    kinds = {item["kind"] for item in snapshot.nodes}

    assert kinds == {
        "data_aggregate",
        "dataset_compare",
        "input",
        "json_deserialize",
        "json_serialize",
        "output",
        "variable_aggregator",
        "workflow_agent",
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
    }
    assert all(item["planner"]["compilable"] for item in snapshot.nodes)
    assert all(item["planner"]["ir_version"] == 3 for item in snapshot.nodes)
    assert all(len(item["planner"]["adapter_checksum"]) == 64 for item in snapshot.nodes)
    assert all(item["contract"]["contract_status"] == "complete" for item in snapshot.nodes)
    assert all(
        item["planner"]["contract_checksum"] == item["contract"]["checksum"]
        for item in snapshot.nodes
    )


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


def test_v3_graph_ir_keeps_data_edges_out_of_native_topology_and_round_trips():
    snapshot = _snapshot()
    request = _request()
    plan = _plan()
    intent, compatibility = v2_to_graph_intent(_typed_blueprint())

    assert intent is not None
    assert compatibility.upgraded is True
    assert compatibility.lossy is False
    graph = resolve_graph_intent(
        intent, snapshot, default_agent_model_id=request.default_agent_model_id
    )
    assert {edge.mode for edge in graph.edges} == {
        "control",
        "data",
        "binding",
        "metadata",
    }
    assert graph.graph_checksum
    assert next(
        node for node in graph.nodes if node.kind == "toolset_resource"
    ).config["pinned_version"] == 2
    assert next(
        node for node in graph.nodes if node.kind == "knowledge_base"
    ).config["observed_active_version_id"] == "version-3"

    candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=intent,
        snapshot=snapshot,
        target=None,
    )
    native_edges = candidate["draft"]["workflow"]["edges"]
    assert all("variable" not in edge for edge in native_edges)
    assert len(native_edges) == len(
        [edge for edge in graph.edges if edge.mode in {"control", "binding", "metadata"}]
    )

    restored_intent = decompile_candidate_to_graph_intent(candidate)
    restored_graph = resolve_graph_intent(
        restored_intent,
        snapshot,
        default_agent_model_id=request.default_agent_model_id,
    )
    restored_candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=restored_intent,
        snapshot=snapshot,
        target=None,
    )
    assert restored_graph.graph_checksum == graph.graph_checksum
    assert workflow_semantic_checksum(restored_candidate) == workflow_semantic_checksum(
        candidate
    )


def test_v2_upgrade_rejects_ambiguous_variable_provenance():
    blueprint = _typed_blueprint().model_copy(deep=True)
    duplicate = blueprint.nodes[0].model_copy(deep=True)
    duplicate.ref = "duplicate_researcher"
    blueprint.nodes.append(duplicate)

    intent, compatibility = v2_to_graph_intent(blueprint)

    assert intent is None
    assert compatibility.lossy is True
    assert any("lossy_conversion" in warning for warning in compatibility.warnings)


def test_v3_intent_rejects_forged_versions_and_unknown_ports():
    intent, _ = v2_to_graph_intent(_typed_blueprint())
    assert intent is not None
    forged = intent.model_dump(mode="json")
    forged["resources"][0]["pinned_version"] = 99

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GraphIntentV3.model_validate(forged)

    invalid_port = intent.model_copy(deep=True)
    invalid_port.nodes[1].inputs[0].port = "invented_port"
    with pytest.raises(ValueError, match="unknown input port"):
        resolve_graph_intent(
            invalid_port,
            _snapshot(),
            default_agent_model_id=_request().default_agent_model_id,
        )


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
    graph_intent, _ = v2_to_graph_intent(_typed_blueprint())
    assert graph_intent is not None
    outputs = [
        _plan().model_dump_json(),
        _typed_blueprint().model_dump_json(),
        graph_intent.model_dump_json(),
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
    report = proposal.payload["meta_planner_report"]
    assert response.ir_version == 3
    assert response.graph_ir_checksum == report["graph_ir_checksum"]
    assert report["graph_ir_status"] == "current"
    assert report["graph_ir"]["ir_version"] == 3
    assert report["compatibility"]["source_version"] == 3
    assert report["compatibility"]["upgraded"] is False
    assert len(report["compiled_workflow_checksum"]) == 64
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
async def test_parseable_v3_repair_uses_one_typed_graph_patch(tmp_path: Path):
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")
    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
    )
    graph_intent, _ = v2_to_graph_intent(_typed_blueprint())
    assert graph_intent is not None
    invalid = graph_intent.model_copy(deep=True)
    invalid.final_output.variable = "missing_output"
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append(
            {
                "system_prompt": system_prompt,
                "temperature": temperature,
            }
        )
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        prompt = json.loads(user_prompt)
        return json.dumps(
            {
                **prompt["required_envelope"],
                "operations": [
                    {
                        "op": "set_final_output",
                        "node_ref": graph_intent.final_output.node_ref,
                        "port": "result",
                    }
                ],
            }
        )

    service = MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
        completion=complete,
    )
    response = await service.generate(_request(), _snapshot())

    assert len(calls) == 3
    assert "GraphPatchEnvelopeV1" in str(calls[-1]["system_prompt"])
    assert calls[-1]["temperature"] == 0
    assert response.repair_used is True
    assert response.validation["valid"] is True
    proposal = proposal_store.require(response.proposal_id)
    assert proposal.payload["meta_planner_report"]["repair_protocol"] == "graph_patch_v1"


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
    assert (
        proposal.payload["meta_planner_report"]["repair_protocol"]
        == "graph_intent_v3"
    )
    fallback_agent = next(
        node
        for node in proposal.payload["draft"]["workflow"]["nodes"]
        if node["type"] == "workflow_agent"
    )
    assert fallback_agent["data"]["modelId"] == ""
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
    assert edited.payload["meta_planner_report"]["graph_ir_status"] == "stale"
    assert edited.payload["meta_planner_report"]["human_modified"] is True
    validated = authoring.validate(
        edited.proposal_id,
        revision=edited.revision,
    )
    assert validated.validation["valid"] is True


@pytest.mark.asyncio
async def test_update_revision_drift_uses_final_proposal_validation(
    tmp_path: Path,
):
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")
    skill_store = WorkspaceSkillDraftStore(tmp_path / "skills")
    target = xpert_store.create_xpert(name="Revision drift target")

    def preflight(candidate):
        result = validate_xpert_definition(candidate)
        return result, candidate.draft.workflow, []

    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        skill_store,
        xpert_preflight=preflight,
    )
    original_validate = authoring.validate

    def validate_after_revision_drift(proposal_id: str, *, revision: int):
        current = xpert_store.get_xpert(target.id)
        xpert_store.update_xpert(
            target.id,
            {"draft": current.draft.model_dump(mode="json")},
        )
        return original_validate(proposal_id, revision=revision)

    authoring.validate = validate_after_revision_drift  # type: ignore[method-assign]
    graph_intent, _ = v2_to_graph_intent(_typed_blueprint())
    assert graph_intent is not None
    outputs = [_plan().model_dump_json(), graph_intent.model_dump_json()]

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        return outputs.pop(0)

    service = MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=preflight,
        completion=complete,
    )
    request = _request().model_copy(
        update={"mode": "update", "target_xpert_id": target.id}
    )

    response = await service.generate(request, _snapshot(), target=target)

    proposal = proposal_store.require(response.proposal_id)
    assert response.compatibility.source_version == 3
    assert response.compatibility.upgraded is False
    assert proposal.validation["valid"] is False
    assert response.validation["valid"] is False
    authoring_stage = next(
        stage
        for stage in response.validation["stages"]
        if stage["id"] == "authoring_proposal"
    )
    assert authoring_stage["valid"] is False
    assert authoring_stage["issues"]


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
            id="list-node",
            type="list_operation",
            data={"kind": "list_operation", "outputVariable": "items"},
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
