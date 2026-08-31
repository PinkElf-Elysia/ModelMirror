from __future__ import annotations

import json

import pytest

from server.meta_agent.graph_ir_v3 import (
    _schemas_compatible,
    decompile_candidate_to_graph_intent,
    resolve_graph_intent,
    v2_to_graph_intent,
    workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    compile_xpert_candidate,
    legacy_blueprint_to_typed_ir,
    validate_blueprint_authorization,
)
from server.meta_agent.schemas import (
    GraphIntentFinalOutputV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
    MetaPlannerIRInputBinding,
    MetaPlannerIRMiddlewareBinding,
    MetaPlannerIROutputBinding,
    MetaPlannerIRResourceBinding,
)
from server.tests.test_meta_planner_v2 import (
    _blueprint,
    _plan,
    _request,
    _snapshot,
    _typed_blueprint,
)
from server.workflow_native.node_contracts import WorkflowValueSchema


def _intent() -> GraphIntentV3:
    intent, compatibility = v2_to_graph_intent(_typed_blueprint())
    assert intent is not None and not compatibility.lossy
    return intent


def test_attack_model_prompt_exposes_only_authorized_v3_node_roles_and_resources():
    request = _request().model_copy(deep=True)
    request.scope.toolset_ids = []
    request.scope.middleware_ids = []
    snapshot = _snapshot()

    blueprint_prompt = json.loads(
        MetaPlannerV2Service._blueprint_prompt(request, _plan(), snapshot, None)
    )
    repair_prompt = json.loads(
        MetaPlannerV2Service._repair_prompt(
            request,
            _plan(),
            snapshot,
            '{"ir_version":2}',
            ["Legacy V2 IR is not valid for a new V3 generation."],
        )
    )

    for prompt in (blueprint_prompt, repair_prompt):
        contract = prompt["graph_intent_contract"]
        example = GraphIntentV3.model_validate(prompt["canonical_minimal_example"])
        assert contract["required_ir_version"] == 3
        assert example.ir_version == 3
        assert [node.kind for node in example.nodes] == ["workflow_agent"]
        assert contract["node_roles"]["executable_node_kinds"] == [
            "workflow_agent"
        ]
        assert contract["node_roles"]["compiler_managed_node_kinds"] == [
            "input",
            "output",
        ]
        assert prompt["capability_snapshot"]["resources"]["toolsets"] == []
        assert prompt["capability_snapshot"]["middleware"] == []
        assert any(
            "never return a V2" in rule for rule in contract["rules"]
        )


def test_attack_resolver_rejects_node_not_exposed_by_capability_snapshot():
    intent = GraphIntentV3(
        name="Unsupported node attack",
        nodes=[
            GraphIntentNodeV3(
                ref="serializer",
                kind="json_serialize",
                title="Serializer",
                task_ids=["research"],
                inputs=[
                    GraphIntentInputBindingV3(
                        port="value",
                        variable="user_input",
                        source_ref="input",
                        source_port="user_input",
                        value_schema=WorkflowValueSchema(type="string"),
                    )
                ],
                outputs=[
                    GraphIntentOutputBindingV3(
                        port="json",
                        variable="serialized",
                        value_schema=WorkflowValueSchema(type="string"),
                    )
                ],
                config={
                    "inputVariable": "user_input",
                    "outputVariable": "serialized",
                    "format": "compact",
                },
            )
        ],
        final_output=GraphIntentFinalOutputV3(
            node_ref="serializer", port="json", variable="serialized"
        ),
    )

    with pytest.raises(ValueError, match="capability snapshot|Planner"):
        resolve_graph_intent(intent, _snapshot())


def test_attack_resolver_rejects_resource_binding_to_compiler_input():
    intent = _intent().model_copy(deep=True)
    intent.resources[0].target_ref = "input"

    with pytest.raises(ValueError, match="workflow_agent"):
        resolve_graph_intent(intent, _snapshot())


def test_attack_resolver_rejects_duplicate_resource_binding():
    intent = _intent().model_copy(deep=True)
    intent.resources.append(intent.resources[1].model_copy(deep=True))

    with pytest.raises(ValueError, match="duplicate resource"):
        resolve_graph_intent(intent, _snapshot())


def test_attack_normalized_node_id_collision_is_rejected_by_resolver():
    intent = _intent().model_copy(deep=True)
    duplicate = intent.nodes[0].model_copy(deep=True)
    duplicate.ref = "researcher-x"
    duplicate.outputs[0].variable = "secondary_research_output"
    intent.nodes[0].ref = "researcher_x"
    intent.nodes.append(duplicate)
    intent.control_edges = [
        intent.control_edges[0].model_copy(
            update={"source_ref": "researcher_x", "target_ref": "writer"}
        ),
        intent.control_edges[0].model_copy(
            update={"source_ref": "researcher-x", "target_ref": "writer"}
        ),
    ]
    for binding in intent.resources:
        if binding.target_ref == "researcher":
            binding.target_ref = "researcher_x"
    intent.nodes[1].inputs = [
        item.model_copy(update={"source_ref": "researcher_x"})
        for item in intent.nodes[1].inputs
    ]

    with pytest.raises(ValueError, match="normalization"):
        resolve_graph_intent(intent, _snapshot())


def test_attack_external_tool_name_is_part_of_resolved_semantics():
    first = _intent().model_copy(deep=True)
    second = _intent().model_copy(deep=True)
    first.resources[0].tool_name = "research_primary"
    second.resources[0].tool_name = "research_secondary"

    first_graph = resolve_graph_intent(first, _snapshot())
    second_graph = resolve_graph_intent(second, _snapshot())
    first_resource = next(
        node for node in first_graph.nodes if node.kind == "external_xpert"
    )

    assert first_resource.config["tool_name"] == "research_primary"
    assert first_graph.graph_checksum != second_graph.graph_checksum


def test_attack_v2_external_name_collision_fails_closed():
    blueprint = _typed_blueprint().model_copy(deep=True)
    blueprint.nodes[0].outputs = [
        MetaPlannerIROutputBinding(
            port="result", variable="user_input", value_type="string"
        )
    ]
    blueprint.nodes[1].inputs = [
        MetaPlannerIRInputBinding(
            port="evidence", variable="user_input", value_type="string"
        )
    ]
    blueprint.nodes[1].config["task_input"] = "{{user_input}}"
    blueprint.final_output.variable = "agent_output"

    intent, compatibility = v2_to_graph_intent(blueprint)

    assert intent is None and compatibility.lossy
    assert any(
        "reserved external variables" in item
        for item in compatibility.warnings
    )


def test_attack_legacy_downstream_user_input_gets_explicit_v3_data_edge():
    blueprint = _blueprint().model_copy(deep=True)
    blueprint.agents[1].task_input = "{{user_input}} {{research_output}}"
    typed = legacy_blueprint_to_typed_ir(_plan(), blueprint)

    intent, compatibility = v2_to_graph_intent(typed)

    assert intent is not None and not compatibility.lossy
    writer = next(node for node in intent.nodes if node.ref == "agent_deliver")
    assert any(
        item.variable == "user_input"
        and item.source_ref == "input"
        and item.source_port == "user_input"
        for item in writer.inputs
    )


def test_attack_template_variables_must_have_explicit_data_bindings():
    intent = _intent().model_copy(deep=True)
    writer = next(node for node in intent.nodes if node.ref == "writer")
    writer.config["task_input"] = "{{research_output}} {{user_input}}"

    issues = validate_blueprint_authorization(
        _request(), _plan(), intent, _snapshot()
    )

    assert any("explicit" in issue and "user_input" in issue for issue in issues)


def test_attack_complex_template_expression_is_rejected_not_silently_ignored():
    intent = _intent().model_copy(deep=True)
    writer = next(node for node in intent.nodes if node.ref == "writer")
    writer.config["task_input"] = "{{research_output.value}}"

    with pytest.raises(ValueError, match="unsupported template expression"):
        resolve_graph_intent(intent, _snapshot())


def test_attack_json_example_braces_are_not_misclassified_as_variables():
    intent = _intent().model_copy(deep=True)
    writer = next(node for node in intent.nodes if node.ref == "writer")
    writer.config["role_prompt"] = (
        'Return JSON shaped like {"answer": "..."} from {{research_output}}.'
    )

    graph = resolve_graph_intent(intent, _snapshot())

    resolved_writer = next(node for node in graph.nodes if node.ref == "writer")
    assert '{"answer": "..."}' in resolved_writer.config["role_prompt"]


def test_attack_unknown_middleware_config_is_rejected_before_persistence():
    intent = _intent().model_copy(deep=True)
    intent.middleware[0].config["api_key"] = "sk-adversarial-placeholder"

    issues = validate_blueprint_authorization(
        _request(), _plan(), intent, _snapshot()
    )

    assert any("api_key" in issue and "not declared" in issue for issue in issues)


def test_attack_permutations_do_not_change_graph_checksum():
    baseline = _intent()
    permuted = baseline.model_copy(deep=True)
    permuted.nodes = list(reversed(permuted.nodes))
    permuted.resources = list(reversed(permuted.resources))
    permuted.middleware = list(reversed(permuted.middleware))

    first = resolve_graph_intent(baseline, _snapshot())
    second = resolve_graph_intent(permuted, _snapshot())

    assert first.graph_checksum == second.graph_checksum


def test_attack_decompile_does_not_silently_repin_immutable_resource():
    snapshot = _snapshot()
    request = _request()
    plan = _plan()
    intent = _intent()
    candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=intent,
        snapshot=snapshot,
        target=None,
    )
    restored = decompile_candidate_to_graph_intent(candidate)
    newer_snapshot = snapshot.model_copy(deep=True)
    newer_snapshot.toolsets[0]["published_version"] = 3

    resolve_graph_intent(restored, snapshot)
    with pytest.raises(ValueError, match="drifted from pinned version 2 to 3"):
        resolve_graph_intent(restored, newer_snapshot)


def test_attack_nested_value_schema_mismatch_is_not_hidden_by_outer_type():
    source = WorkflowValueSchema(
        type="array",
        items=WorkflowValueSchema(
            type="object",
            properties={"answer": WorkflowValueSchema(type="string")},
            required=("answer",),
        ),
    )
    target = WorkflowValueSchema(
        type="array",
        items=WorkflowValueSchema(
            type="object",
            properties={"score": WorkflowValueSchema(type="number")},
            required=("score",),
        ),
    )

    assert not _schemas_compatible(source, target)


def test_attack_nullable_source_cannot_flow_into_non_nullable_target():
    assert not _schemas_compatible(
        WorkflowValueSchema(type="string", nullable=True),
        WorkflowValueSchema(type="string", nullable=False),
    )
    assert _schemas_compatible(
        WorkflowValueSchema(type="string", nullable=True),
        WorkflowValueSchema(type="string", nullable=True),
    )
    assert _schemas_compatible(
        WorkflowValueSchema(type="integer"),
        WorkflowValueSchema(type="number"),
    )


def test_attack_display_order_does_not_change_semantic_checksums():
    first = _intent().model_copy(deep=True)
    first.tags = ["review", "research"]
    first.starters = ["Review this", "Research this"]
    first.prompt_profile_ids = ["prompt-review", "prompt-secondary"]
    second = first.model_copy(deep=True)
    second.tags.reverse()
    second.starters.reverse()
    second.prompt_profile_ids.reverse()

    snapshot = _snapshot()
    second_profile = dict(snapshot.prompt_profiles[0])
    second_profile["id"] = "prompt-secondary"
    second_profile["name"] = "Secondary prompt"
    snapshot.prompt_profiles.append(second_profile)

    first_graph = resolve_graph_intent(first, snapshot)
    second_graph = resolve_graph_intent(second, snapshot)
    assert first_graph.graph_checksum == second_graph.graph_checksum

    candidate = compile_xpert_candidate(
        request=_request(),
        plan=_plan(),
        blueprint=first,
        snapshot=snapshot,
        target=None,
    )
    extra_profile = dict(candidate["draft"]["prompt_profiles"][0])
    extra_profile["profile_id"] = "prompt-third"
    candidate["draft"]["prompt_profiles"].append(extra_profile)
    reordered = {
        **candidate,
        "draft": {
            **candidate["draft"],
            "prompt_profiles": list(
                reversed(candidate["draft"]["prompt_profiles"])
            ),
        },
    }
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(
        reordered
    )


def test_attack_snapshot_adapter_checksum_tampering_fails_closed():
    snapshot = _snapshot().model_copy(deep=True)
    workflow_agent = next(
        item for item in snapshot.nodes if item["kind"] == "workflow_agent"
    )
    workflow_agent["planner"]["adapter_checksum"] = "0" * 64

    with pytest.raises(ValueError, match="authoritative Planner capability"):
        resolve_graph_intent(_intent(), snapshot)


def test_attack_allowed_middleware_field_cannot_smuggle_a_secret_value():
    intent = _intent().model_copy(deep=True)
    synthetic_secret = "sk-" + "adversarial-secret-value-123456"
    intent.middleware[0].config["system_prompt"] = synthetic_secret

    issues = validate_blueprint_authorization(
        _request(), _plan(), intent, _snapshot()
    )

    assert any("credential material" in issue for issue in issues)


def test_attack_allowed_middleware_field_rejects_embedded_secret_value():
    intent = _intent().model_copy(deep=True)
    synthetic_secret = "sk-" + "adversarial-secret-value-123456"
    intent.middleware[0].config["system_prompt"] = (
        f"Use this credential {synthetic_secret} for requests."
    )

    issues = validate_blueprint_authorization(
        _request(), _plan(), intent, _snapshot()
    )

    assert any("credential material" in issue for issue in issues)


@pytest.mark.parametrize("credential_key", ["clientSecret", "authToken"])
def test_attack_json_middleware_rejects_nested_credential_aliases(
    credential_key: str,
):
    intent = _intent().model_copy(deep=True)
    intent.middleware = [
        MetaPlannerIRMiddlewareBinding(
            target_ref=intent.nodes[0].ref,
            middleware_id="structured_output",
            config={
                "schema_json": {
                    "type": "object",
                    "extensions": {credential_key: "adversarial-sentinel"},
                }
            },
        )
    ]

    issues = validate_blueprint_authorization(
        _request(), _plan(), intent, _snapshot()
    )

    assert any("credential material" in issue for issue in issues)


def test_attack_explicit_private_default_model_is_scoped_to_this_request():
    graph = resolve_graph_intent(
        _intent(),
        _snapshot(),
        default_agent_model_id="private-gateway/model",
    )

    agent_models = {
        node.config.get("model_id")
        for node in graph.nodes
        if node.kind == "workflow_agent"
    }
    assert agent_models == {"private-gateway/model"}


def test_attack_unavailable_node_model_is_rejected_by_authorization():
    intent = _intent().model_copy(deep=True)
    intent.nodes[0].config["model_id"] = "model/forged"

    issues = validate_blueprint_authorization(
        _request(), _plan(), intent, _snapshot()
    )

    assert any("model/forged" in issue and "available" in issue for issue in issues)


def test_attack_decompile_does_not_silently_repin_prompt_profile():
    snapshot = _snapshot()
    candidate = compile_xpert_candidate(
        request=_request(),
        plan=_plan(),
        blueprint=_intent(),
        snapshot=snapshot,
        target=None,
    )
    restored = decompile_candidate_to_graph_intent(candidate)
    newer_snapshot = snapshot.model_copy(deep=True)
    newer_snapshot.prompt_profiles[0]["published_version"] = 3

    with pytest.raises(ValueError, match="drifted from pinned version 2 to 3"):
        resolve_graph_intent(restored, newer_snapshot)


def test_attack_trusted_decompile_pins_are_not_model_forgeable_fields():
    candidate = compile_xpert_candidate(
        request=_request(),
        plan=_plan(),
        blueprint=_intent(),
        snapshot=_snapshot(),
        target=None,
    )
    restored = decompile_candidate_to_graph_intent(candidate)
    dumped = restored.model_dump(mode="json")
    schema = GraphIntentV3.model_json_schema()

    assert restored._pinned_resource_versions
    assert restored._pinned_prompt_profile_versions
    assert "_pinned_resource_versions" not in dumped
    assert "_pinned_prompt_profile_versions" not in dumped
    assert "_pinned_resource_versions" not in schema.get("properties", {})
    assert "_pinned_prompt_profile_versions" not in schema.get("properties", {})
