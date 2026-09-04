from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from server.evaluations.metrics import evaluate_case_metrics
from server.evaluations.models import EvaluationCaseInput
from server.meta_agent.control_flow import (
    ControlFlowAnalysisError,
    analyze_control_flow,
)
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent,
    resolve_graph_intent,
    workflow_semantic_checksum,
)
from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, apply_graph_patch
from server.meta_agent.meta_planner_v2 import (
    compile_xpert_candidate,
    validate_blueprint_authorization,
)
from server.meta_agent.schemas import (
    GraphIntentControlEdgeV3,
    GraphIntentFinalOutputSourceV3,
    GraphIntentFinalOutputV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
)
from server.tests.test_meta_planner_v2 import _plan, _request, _snapshot
from server.workflow_native.control_data import (
    WorkflowControlDataError,
    select_output_v2,
)
from server.workflow_native.node_contracts import (
    WorkflowValueSchema,
    workflow_node_contract_registry,
)


STRING = WorkflowValueSchema(type="string")
ROWS = WorkflowValueSchema(
    type="array",
    items=WorkflowValueSchema(type="object"),
)


def _input(
    port: str,
    variable: str,
    source_ref: str,
    source_port: str = "result",
    schema: WorkflowValueSchema = STRING,
) -> GraphIntentInputBindingV3:
    return GraphIntentInputBindingV3(
        port=port,
        variable=variable,
        source_ref=source_ref,
        source_port=source_port,
        value_schema=schema,
    )


def _agent(
    ref: str,
    *,
    variable: str,
    source_ref: str = "input",
    source_port: str = "user_input",
    input_variable: str = "user_input",
) -> GraphIntentNodeV3:
    return GraphIntentNodeV3(
        ref=ref,
        kind="workflow_agent",
        title=ref.replace("_", " ").title(),
        task_ids=["answer"],
        inputs=[_input("task", input_variable, source_ref, source_port)],
        outputs=[
            GraphIntentOutputBindingV3(
                port="result",
                variable=variable,
                value_schema=STRING,
            )
        ],
        config={
            "role_prompt": "Return one bounded answer.",
            "task_input": "{{" + input_variable + "}}",
            "model_id": "model/agent",
        },
    )


def _condition(ref: str = "router") -> GraphIntentNodeV3:
    return GraphIntentNodeV3(
        ref=ref,
        kind="condition",
        title="Decision",
        inputs=[_input("value", "user_input", "input", "user_input")],
        config={
            "field": "",
            "operator": "equals",
            "value_type": "text",
            "value": "approve",
        },
    )


def _branch_intent() -> GraphIntentV3:
    return GraphIntentV3(
        name="Mutually exclusive answers",
        nodes=[
            _condition(),
            _agent("approved", variable="approved_result"),
            _agent("rejected", variable="rejected_result"),
        ],
        control_edges=[
            GraphIntentControlEdgeV3(
                source_ref="router", outcome_ref="matched", target_ref="approved"
            ),
            GraphIntentControlEdgeV3(
                source_ref="router", outcome_ref="unmatched", target_ref="rejected"
            ),
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[
                GraphIntentFinalOutputSourceV3(node_ref="approved"),
                GraphIntentFinalOutputSourceV3(node_ref="rejected"),
            ]
        ),
    )


def _bind_parsed_router_input(intent: GraphIntentV3, schema: WorkflowValueSchema) -> None:
    router = intent.nodes[0]
    router.inputs = [_input("value", "parsed_value", "parse", "value", schema)]
    intent.nodes.insert(
        0,
        GraphIntentNodeV3(
            ref="parse",
            kind="json_deserialize",
            title="Parse input",
            inputs=[_input("json", "user_input", "input", "user_input")],
            outputs=[GraphIntentOutputBindingV3(
                port="value", variable="parsed_value", value_schema=schema
            )],
            config={"expected_schema": schema.model_dump(mode="json")},
        ),
    )
    intent.control_edges.insert(
        0, GraphIntentControlEdgeV3(source_ref="parse", target_ref=router.ref)
    )


def _route_error_intent() -> GraphIntentV3:
    intent = _branch_intent()
    intent.nodes[0].kind = "multi_route"
    intent.nodes[0].config = {
        "routes": [
            {"label": "Approve", "operator": "equals", "value_type": "text", "value": "approve"},
            {"label": "Reject", "operator": "equals", "value_type": "text", "value": "reject"},
        ]
    }
    intent.nodes.append(GraphIntentNodeV3(
        ref="stop", kind="terminate_error", title="Unsupported request",
        config={"error_code": "UNSUPPORTED_REQUEST", "message": "Choose a supported request."},
    ))
    intent.control_edges = [
        GraphIntentControlEdgeV3(source_ref="router", outcome_ref=outcome, target_ref=target)
        for outcome, target in (("case_1", "approved"), ("case_2", "rejected"), ("default", "stop"))
    ]
    return intent


def _merge_intent() -> GraphIntentV3:
    decoders = [
        GraphIntentNodeV3(
            ref=ref, kind="json_deserialize", title=ref,
            inputs=[_input("json", "user_input", "input", "user_input")],
            outputs=[GraphIntentOutputBindingV3(port="value", variable=ref, value_schema=ROWS)],
            config={"expected_schema": ROWS.model_dump(mode="json")},
        ) for ref in ("left_rows", "right_rows")
    ]
    merge = GraphIntentNodeV3(
        ref="merge", kind="data_merge", title="Combine rows",
        inputs=[_input(side, f"{side}_rows", f"{side}_rows", "value", ROWS) for side in ("left", "right")],
        outputs=[GraphIntentOutputBindingV3(
            port="result", variable="merged", value_schema=WorkflowValueSchema(type="array")
        )],
        config={"merge_mode": "append", "key_fields": []},
    )
    encode = GraphIntentNodeV3(
        ref="encode", kind="json_serialize", title="Encode rows",
        inputs=[_input("value", "merged", "merge", schema=WorkflowValueSchema(type="array"))],
        outputs=[GraphIntentOutputBindingV3(port="json", variable="encoded", value_schema=STRING)],
        config={"format": "compact"},
    )
    return GraphIntentV3(
        name="Fanout merge contract pack",
        nodes=[*decoders, merge, encode, _agent(
            "answer", variable="answer_result", source_ref="encode",
            source_port="json", input_variable="encoded",
        )],
        control_edges=[
            GraphIntentControlEdgeV3(source_ref=source, target_ref=target)
            for source, target in (("left_rows", "merge"), ("right_rows", "merge"), ("merge", "encode"), ("encode", "answer"))
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="answer")]
        ),
    )


@pytest.mark.parametrize("factory", [_branch_intent, _route_error_intent, _merge_intent])
def test_control_flow_node_packs_compile_decompile_compile(factory) -> None:
    intent = factory()
    snapshot = _snapshot()
    request = _request()
    request.scope.allowed_node_kinds = [node["kind"] for node in snapshot.nodes]
    plan = _plan()
    plan.tasks = [plan.tasks[-1].model_copy(update={
        "task_id": "answer", "depends_on": [], "input_contract": [],
    })]
    assert validate_blueprint_authorization(request, plan, intent, snapshot) == []
    candidate = compile_xpert_candidate(
        request=request, plan=plan, blueprint=intent, snapshot=snapshot, target=None,
    )
    recovered = decompile_candidate_to_graph_intent(candidate)
    rebuilt = compile_xpert_candidate(
        request=request, plan=plan, blueprint=recovered, snapshot=snapshot, target=None,
    )
    assert resolve_graph_intent(intent, snapshot).graph_checksum == resolve_graph_intent(
        recovered, snapshot
    ).graph_checksum
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)


def test_condition_v2_is_null_schema_does_not_require_value() -> None:
    contract = workflow_node_contract_registry.require("condition")
    validator = Draft202012Validator(contract.config_schema)

    assert not list(
        validator.iter_errors(
            {
                "contractVersion": 2,
                "inputVariable": "payload",
                "field": "status",
                "operator": "is_null",
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "contractVersion": 2,
                "inputVariable": "payload",
                "field": "status",
                "operator": "equals",
                "valueType": "text",
            }
        )
    )


def test_mutually_exclusive_final_sources_resolve_with_semantic_outcomes() -> None:
    intent = _branch_intent()

    report = analyze_control_flow(intent)
    resolved = resolve_graph_intent(
        intent,
        _snapshot(),
        default_agent_model_id="model/agent",
    )

    assert report["scenario_count"] == 2
    assert {tuple(item["success_sources"]) for item in report["scenarios"]} == {
        ("approved",),
        ("rejected",),
    }
    assert {
        edge.outcome_ref
        for edge in resolved.edges
        if edge.mode == "control" and edge.source.node_ref == "router"
    } == {"matched", "unmatched"}
    assert resolved.terminal_count == 2


def test_multi_route_shadow_and_missing_default_fail_closed() -> None:
    intent = _branch_intent()
    intent.nodes[0] = GraphIntentNodeV3(
        ref="router",
        kind="multi_route",
        title="Route",
        inputs=[_input("value", "user_input", "input", "user_input")],
        config={
            "routes": [
                {
                    "label": "First",
                    "operator": "equals",
                    "value_type": "text",
                    "value": "same",
                },
                {
                    "label": "Shadowed",
                    "operator": "equals",
                    "value_type": "text",
                    "value": "same",
                },
            ]
        },
    )
    intent.control_edges = [
        GraphIntentControlEdgeV3(
            source_ref="router", outcome_ref="case_1", target_ref="approved"
        ),
        GraphIntentControlEdgeV3(
            source_ref="router", outcome_ref="case_2", target_ref="rejected"
        ),
    ]

    with pytest.raises(ControlFlowAnalysisError) as exc_info:
        analyze_control_flow(intent)

    message = str(exc_info.value)
    assert "default" in message
    assert "case_2" in message


def test_optional_branch_data_cannot_feed_a_common_node() -> None:
    intent = _branch_intent()
    common = _agent(
        "common",
        variable="common_result",
        source_ref="approved",
        input_variable="approved_result",
    )
    intent.nodes.append(common)
    intent.control_edges.extend(
        [
            GraphIntentControlEdgeV3(
                source_ref="approved", outcome_ref="success", target_ref="common"
            ),
            GraphIntentControlEdgeV3(
                source_ref="rejected", outcome_ref="success", target_ref="common"
            ),
        ]
    )
    intent.final_output = GraphIntentFinalOutputV3(
        sources=[GraphIntentFinalOutputSourceV3(node_ref="common")]
    )

    with pytest.raises(ControlFlowAnalysisError, match="not guaranteed"):
        analyze_control_flow(intent)


def test_nonterminal_dead_end_cannot_hide_beside_a_valid_success_path() -> None:
    intent = _branch_intent()
    intent.nodes.append(_agent("orphan", variable="orphan_result"))

    with pytest.raises(ControlFlowAnalysisError, match="dead end"):
        analyze_control_flow(intent)


def test_unknown_control_ref_returns_structured_diagnostic() -> None:
    intent = _branch_intent()
    intent.control_edges[0].target_ref = "missing"

    with pytest.raises(ControlFlowAnalysisError, match="unknown node"):
        analyze_control_flow(intent)


@pytest.mark.parametrize(
    "config",
    [
        {"operator": "is_null"},
        {"operator": "gt", "value_type": "number", "value": 10},
    ],
)
def test_string_input_cannot_use_null_or_numeric_witnesses(config: dict) -> None:
    intent = _branch_intent()
    intent.nodes[0].config = config

    with pytest.raises(ControlFlowAnalysisError, match="unproven outcomes"):
        resolve_graph_intent(intent, _snapshot(), default_agent_model_id="model/agent")


def test_multi_route_default_needs_a_witness_within_its_input_schema() -> None:
    intent = _branch_intent()
    intent.nodes[0] = GraphIntentNodeV3(
        ref="router",
        kind="multi_route",
        title="Boolean route",
        inputs=[_input("value", "user_input", "input", "user_input")],
        config={
            "routes": [
                {"label": "True", "operator": "equals", "value_type": "boolean", "value": True},
                {"label": "False", "operator": "equals", "value_type": "boolean", "value": False},
            ]
        },
    )
    intent.control_edges = [
        GraphIntentControlEdgeV3(source_ref="router", outcome_ref=outcome, target_ref="approved")
        for outcome in ("case_1", "case_2", "default")
    ]
    intent.nodes = intent.nodes[:2]
    intent.final_output = GraphIntentFinalOutputV3(
        sources=[GraphIntentFinalOutputSourceV3(node_ref="approved")]
    )
    _bind_parsed_router_input(intent, WorkflowValueSchema(type="boolean"))

    with pytest.raises(ControlFlowAnalysisError, match="unproven outcomes: default"):
        resolve_graph_intent(intent, _snapshot(), default_agent_model_id="model/agent")


def test_widened_consumer_schema_cannot_invent_a_nullable_source() -> None:
    intent = _branch_intent()
    intent.nodes[0].config = {"operator": "is_null"}
    intent.nodes[0].inputs[0].value_schema = WorkflowValueSchema(type="any")

    with pytest.raises(ControlFlowAnalysisError, match="unproven outcomes: matched"):
        resolve_graph_intent(intent, _snapshot(), default_agent_model_id="model/agent")


def test_numeric_router_cannot_ignore_valid_non_numeric_any_inputs() -> None:
    intent = _branch_intent()
    intent.nodes[0].config = {"operator": "gt", "value_type": "number", "value": 10}
    _bind_parsed_router_input(intent, WorkflowValueSchema(type="any"))

    with pytest.raises(ControlFlowAnalysisError, match="unproven outcomes"):
        resolve_graph_intent(intent, _snapshot(), default_agent_model_id="model/agent")


@pytest.mark.parametrize("nullable", [False, True])
def test_field_witnesses_preserve_required_object_properties(nullable: bool) -> None:
    intent = _branch_intent()
    intent.nodes[0].config = {"field": "status", "operator": "is_null"}
    schema = WorkflowValueSchema(
        type="object",
        properties={
            "status": WorkflowValueSchema(type="string", nullable=nullable),
            "count": WorkflowValueSchema(type="integer"),
        },
        required=("status", "count"),
    )
    _bind_parsed_router_input(intent, schema)

    if nullable:
        resolved = resolve_graph_intent(
            intent, _snapshot(), default_agent_model_id="model/agent"
        )
        assert resolved.control_flow_report["scenario_count"] == 2
    else:
        with pytest.raises(ControlFlowAnalysisError, match="unproven outcomes: matched"):
            resolve_graph_intent(intent, _snapshot(), default_agent_model_id="model/agent")


def test_data_merge_requires_two_guaranteed_fanout_branches() -> None:
    source = _agent("source", variable="source_rows")
    source.outputs[0].value_schema = ROWS
    left = _agent(
        "left_branch",
        variable="left_rows",
        source_ref="source",
        input_variable="source_rows",
    )
    right = _agent(
        "right_branch",
        variable="right_rows",
        source_ref="source",
        input_variable="source_rows",
    )
    for node in (left, right):
        node.inputs[0].value_schema = ROWS
        node.outputs[0].value_schema = ROWS
    merge = GraphIntentNodeV3(
        ref="merge",
        kind="data_merge",
        title="Merge",
        inputs=[
            _input("left", "left_rows", "left_branch", schema=ROWS),
            _input("right", "right_rows", "right_branch", schema=ROWS),
        ],
        outputs=[
            GraphIntentOutputBindingV3(
                port="result", variable="merged_rows", value_schema=ROWS
            )
        ],
        config={"merge_mode": "append", "key_fields": []},
    )
    final = _agent(
        "final",
        variable="final_result",
        source_ref="merge",
        input_variable="merged_rows",
    )
    final.inputs[0].value_schema = ROWS
    intent = GraphIntentV3(
        name="Fanout merge",
        nodes=[source, left, right, merge, final],
        control_edges=[
            GraphIntentControlEdgeV3(source_ref="source", target_ref="left_branch"),
            GraphIntentControlEdgeV3(source_ref="source", target_ref="right_branch"),
            GraphIntentControlEdgeV3(source_ref="left_branch", target_ref="merge"),
            GraphIntentControlEdgeV3(source_ref="right_branch", target_ref="merge"),
            GraphIntentControlEdgeV3(source_ref="merge", target_ref="final"),
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="final")]
        ),
    )

    assert analyze_control_flow(intent)["scenario_count"] == 1

    broken = intent.model_copy(deep=True)
    broken.control_edges = [
        edge
        for edge in broken.control_edges
        if not (edge.source_ref == "right_branch" and edge.target_ref == "merge")
    ]
    broken.control_edges.append(
        GraphIntentControlEdgeV3(source_ref="right_branch", target_ref="final")
    )
    with pytest.raises(ControlFlowAnalysisError, match="one side of data_merge"):
        analyze_control_flow(broken)


def test_symbolic_scenario_limit_is_enforced_before_evaluation() -> None:
    nodes: list[GraphIntentNodeV3] = []
    edges: list[GraphIntentControlEdgeV3] = []
    for index in range(6):
        ref = f"router_{index}"
        nodes.append(
            GraphIntentNodeV3(
                ref=ref,
                kind="multi_route",
                title=f"Router {index}",
                inputs=[_input("value", "user_input", "input", "user_input")],
                config={
                    "routes": [
                        {
                            "label": "A",
                            "operator": "equals",
                            "value_type": "text",
                            "value": f"a_{index}",
                        },
                        {
                            "label": "B",
                            "operator": "equals",
                            "value_type": "text",
                            "value": f"b_{index}",
                        },
                    ]
                },
            )
        )
    final = _agent("final", variable="final_result")
    nodes.append(final)
    for index in range(6):
        source = f"router_{index}"
        target = f"router_{index + 1}" if index < 5 else "final"
        for outcome in ("case_1", "case_2", "default"):
            edges.append(
                GraphIntentControlEdgeV3(
                    source_ref=source,
                    outcome_ref=outcome,
                    target_ref=target,
                )
            )
    intent = GraphIntentV3(
        name="Scenario overflow",
        nodes=nodes,
        control_edges=edges,
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="final")]
        ),
    )

    with pytest.raises(ControlFlowAnalysisError, match="729 scenarios"):
        analyze_control_flow(intent)


def test_patch_uses_semantic_outcomes_and_rejects_native_handle_injection() -> None:
    intent = _branch_intent()
    base = {
        "protocol_version": 1,
        "proposal_revision": 1,
        "expected_graph_checksum": "a" * 64,
        "expected_candidate_checksum": "b" * 64,
    }
    with pytest.raises(ValidationError):
        GraphPatchEnvelopeV1.model_validate(
            {
                **base,
                "operations": [
                    {
                        "op": "connect_control",
                        "source_ref": "router",
                        "outcome_ref": "matched",
                        "target_ref": "approved",
                        "sourceHandle": "true",
                    }
                ],
            }
        )

    patch = GraphPatchEnvelopeV1.model_validate(
        {
            **base,
            "operations": [
                {
                    "op": "set_final_outputs",
                    "sources": [
                        {"node_ref": "rejected", "port": "result"},
                        {"node_ref": "approved", "port": "result"},
                    ],
                }
            ],
        }
    )
    applied = apply_graph_patch(
        intent,
        patch,
        plan_task_ids={"answer"},
        allowed_node_kinds={"condition", "workflow_agent"},
    )
    assert [source.node_ref for source in applied.intent.final_output.sources] == [
        "rejected",
        "approved",
    ]


def test_output_v2_requires_exactly_one_arrived_source() -> None:
    data = {
        "contractVersion": 2,
        "selectionPolicy": "exactly_one_arrived",
        "outputSources": [
            {"sourceRef": "approved", "sourcePort": "result", "variable": "a"},
            {"sourceRef": "rejected", "sourcePort": "result", "variable": "b"},
        ],
    }

    variable, value, source = select_output_v2(data, {"a": "accepted"})
    assert (variable, value, source["source_ref"]) == ("a", "accepted", "approved")
    with pytest.raises(WorkflowControlDataError, match="FINAL_OUTPUT_SELECTION_INVALID"):
        select_output_v2(data, {})
    with pytest.raises(WorkflowControlDataError, match="FINAL_OUTPUT_SELECTION_INVALID"):
        select_output_v2(data, {"a": "accepted", "b": "rejected"})


@pytest.mark.asyncio
async def test_workflow_path_metric_matches_outcomes_and_safe_error_terminal() -> None:
    case = EvaluationCaseInput.model_validate(
        {
            "message": "route this request",
            "path": {
                "required_outcomes": ["router:matched"],
                "forbidden_outcomes": ["router:unmatched"],
                "terminal": "error",
                "error_code": "DECLINED",
            },
        }
    ).model_dump(mode="json")

    passed = await evaluate_case_metrics(
        case=case,
        output="",
        citations={},
        control_flow={
            "supported": True,
            "outcomes": ["router:matched"],
            "terminal": "error",
            "source_ref": "stop",
            "error_code": "DECLINED",
        },
    )
    failed = await evaluate_case_metrics(
        case=case,
        output="",
        citations={},
        control_flow={"supported": False, "outcomes": []},
    )

    assert passed["score"] == 1.0
    assert passed["metrics"][0]["kind"] == "workflow_path_match"
    assert failed["score"] == 0.0


def test_expected_error_path_rejects_text_answer_metrics() -> None:
    with pytest.raises(ValidationError, match="cannot include text-answer metrics"):
        EvaluationCaseInput.model_validate(
            {
                "message": "route this request",
                "expected": {"contains": ["should never be scored"]},
                "path": {
                    "terminal": "error",
                    "error_code": "DECLINED",
                },
            }
        )
