from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent,
    resolve_graph_intent,
    workflow_semantic_checksum,
)
from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, apply_graph_patch
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    _normalize_adapter_outputs_for_repair,
    compile_xpert_candidate,
    validate_blueprint_authorization,
)
from server.meta_agent.schemas import (
    GraphIntentControlEdgeV3,
    GraphIntentFinalOutputV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
)
from server.tests.test_meta_planner_v2 import _plan, _request, _snapshot
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.workflow_native.node_contracts import WorkflowValueSchema
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


PURE_NODE_KINDS = {
    "json_serialize",
    "json_deserialize",
    "variable_aggregator",
    "data_aggregate",
    "dataset_compare",
}
STRING = WorkflowValueSchema(type="string")
ROWS = WorkflowValueSchema(
    type="array",
    items=WorkflowValueSchema(type="object"),
)
OBJECT = WorkflowValueSchema(type="object")


def _authorized_request():
    request = _request().model_copy(deep=True)
    request.scope.allowed_node_kinds = sorted(
        set(request.scope.allowed_node_kinds) | PURE_NODE_KINDS
    )
    return request


def test_task_plan_prompt_keeps_pure_nodes_out_of_expert_tasks() -> None:
    request = _authorized_request()

    prompt = json.loads(MetaPlannerV2Service._plan_prompt(request, _snapshot()))
    contract = prompt["task_planning_contract"]

    assert contract["expert_task_binding"] == "required"
    assert set(contract["auxiliary_node_kinds"]) == PURE_NODE_KINDS
    assert all(
        item["task_binding"] == "forbidden"
        for item in contract["auxiliary_node_contracts"]
    )
    contracts = {
        item["kind"]: item for item in contract["auxiliary_node_contracts"]
    }
    assert contracts["data_aggregate"]["purpose"]
    assert contracts["data_aggregate"]["inputs"] == [
        {"name": "rows", "type": "array", "cardinality": "one"}
    ]
    assert contracts["dataset_compare"]["outputs"] == [
        {"name": "result", "type": "object", "cardinality": "one"}
    ]
    assert contract["expert_task_test"]["required_answer"] == "yes"
    assert "Group rows and calculate deterministic measures." in contract[
        "non_task_examples"
    ]
    assert any(
        "must not become plan tasks" in rule for rule in contract["rules"]
    )


@pytest.mark.parametrize("ref", ["input", "output"])
def test_patch_schema_rejects_materializing_compiler_managed_refs(ref: str) -> None:
    payload = {
        "protocol_version": 1,
        "proposal_revision": 1,
        "expected_graph_checksum": "a" * 64,
        "expected_candidate_checksum": "b" * 64,
        "operations": [
            {
                "op": "add_node",
                "ref": ref,
                "kind": "json_deserialize",
                "title": "Input",
                "task_ids": [],
                "config": {"expected_schema": {"type": "object"}},
                "output_variables": {"value": "decoded"},
            }
        ],
    }

    with pytest.raises(ValueError, match="compiler-managed"):
        GraphPatchEnvelopeV1.model_validate(payload)


def test_patch_repair_prompt_has_issue_scoped_ref_and_agent_limits() -> None:
    request = _authorized_request().model_copy(update={"max_agents": 1})
    intent = _aggregate_intent()

    prompt = json.loads(
        MetaPlannerV2Service._patch_repair_prompt(
            request,
            _plan(),
            _snapshot(),
            intent,
            ["Typed IR exceeds max_agents=1."],
        )
    )
    contract = prompt["repair_contract"]

    assert contract["compiler_managed_refs"] == ["input", "output"]
    assert set(contract["existing_node_refs"]) == {
        node.ref for node in intent.nodes
    }
    assert contract["max_workflow_agent_nodes"] == 1
    assert contract["current_workflow_agent_nodes"] == 2
    assert contract["allow_add_workflow_agent"] is False
    assert any(
        "never add, update, remove, or move" in rule
        for rule in contract["rules"]
    )
    assert any(
        "consolidate task_ids" in rule for rule in contract["issue_playbook"]
    )
    assert any(
        "recomputed by the server" in rule for rule in contract["rules"]
    )
    assert set(prompt["required_schema"]["properties"]) == {"operations"}
    assert "required_envelope" not in prompt


def test_repair_normalizes_only_adapter_derived_output_schema() -> None:
    intent = _aggregate_intent()
    decode = next(node for node in intent.nodes if node.ref == "decode_rows")
    decode.outputs[0].value_schema = OBJECT

    normalized, refs = _normalize_adapter_outputs_for_repair(intent, _snapshot())

    normalized_decode = next(
        node for node in normalized.nodes if node.ref == "decode_rows"
    )
    assert refs == ["decode_rows"]
    assert normalized_decode.outputs[0].value_schema == ROWS
    assert decode.outputs[0].value_schema == OBJECT

    forged = intent.model_copy(deep=True)
    forged_decode = next(
        node for node in forged.nodes if node.ref == "decode_rows"
    )
    forged_decode.outputs[0].port = "forged"
    with pytest.raises(ValueError, match="requires exactly one value output"):
        _normalize_adapter_outputs_for_repair(forged, _snapshot())


@pytest.mark.asyncio
async def test_single_patch_repair_refreshes_dynamic_deserialize_schema(
    tmp_path: Path,
) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")

    def preflight(candidate):
        result = validate_xpert_definition(candidate)
        return result, candidate.draft.workflow, []

    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=preflight,
    )
    invalid = _aggregate_intent()
    decode = next(node for node in invalid.nodes if node.ref == "decode_rows")
    decode.outputs[0].value_schema = OBJECT
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        return json.dumps({"operations": []})

    response = await MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=preflight,
        completion=complete,
    ).generate(_authorized_request(), _snapshot())

    assert len(calls) == 3
    assert response.repair_used is True
    assert response.validation["valid"] is True
    assert any(
        "Adapter-derived output Schemas" in warning
        for warning in response.warnings
    )
    proposal = proposal_store.require(response.proposal_id)
    decode_node = next(
        node
        for node in proposal.payload["draft"]["workflow"]["nodes"]
        if node["type"] == "json_deserialize"
    )
    assert decode_node["data"]["expectedSchema"]["type"] == "array"


@pytest.mark.asyncio
async def test_semantic_adapter_failure_preserves_intent_for_patch_repair(
    tmp_path: Path,
) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    xpert_store = XpertStore(tmp_path / "xperts")

    def preflight(candidate):
        result = validate_xpert_definition(candidate)
        return result, candidate.draft.workflow, []

    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=preflight,
    )
    invalid = _aggregate_intent()
    aggregate = next(node for node in invalid.nodes if node.ref == "aggregate_rows")
    aggregate.outputs[0].value_schema = OBJECT
    encode = next(node for node in invalid.nodes if node.ref == "encode_summary")
    encode.inputs[0].value_schema = OBJECT
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        return json.dumps({"operations": []})

    response = await MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=preflight,
        completion=complete,
    ).generate(_authorized_request(), _snapshot())

    assert len(calls) == 3
    assert response.repair_used is True
    assert response.validation["valid"] is True
    assert any(
        "aggregate_rows" in warning and "Adapter-derived output Schemas" in warning
        for warning in response.warnings
    )
    proposal = proposal_store.require(response.proposal_id)
    report = proposal.payload["meta_planner_report"]
    assert report["repair_protocol"] == "graph_patch_v1"


def _input(
    *,
    port: str,
    variable: str,
    source_ref: str,
    source_port: str,
    schema: WorkflowValueSchema,
) -> GraphIntentInputBindingV3:
    return GraphIntentInputBindingV3(
        port=port,
        variable=variable,
        source_ref=source_ref,
        source_port=source_port,
        value_schema=schema,
    )


def _output(
    port: str,
    variable: str,
    schema: WorkflowValueSchema,
) -> GraphIntentOutputBindingV3:
    return GraphIntentOutputBindingV3(
        port=port,
        variable=variable,
        value_schema=schema,
    )


def _agent(
    *,
    ref: str,
    task_id: str,
    source_ref: str,
    source_port: str,
    input_variable: str,
    output_variable: str,
) -> GraphIntentNodeV3:
    return GraphIntentNodeV3(
        ref=ref,
        kind="workflow_agent",
        title=ref.replace("_", " ").title(),
        task_ids=[task_id],
        inputs=[
            _input(
                port="task",
                variable=input_variable,
                source_ref=source_ref,
                source_port=source_port,
                schema=STRING,
            )
        ],
        outputs=[_output("result", output_variable, STRING)],
        config={
            "role_prompt": f"Complete {task_id} from {{{{{input_variable}}}}}.",
            "task_input": f"{{{{{input_variable}}}}}",
            "model_id": "model/agent",
        },
    )


def _aggregate_intent() -> GraphIntentV3:
    nodes = [
        _agent(
            ref="researcher",
            task_id="research",
            source_ref="input",
            source_port="user_input",
            input_variable="user_input",
            output_variable="rows_json",
        ),
        GraphIntentNodeV3(
            ref="decode_rows",
            kind="json_deserialize",
            title="Decode rows",
            inputs=[
                _input(
                    port="json",
                    variable="rows_json",
                    source_ref="researcher",
                    source_port="result",
                    schema=STRING,
                )
            ],
            outputs=[_output("value", "rows", ROWS)],
            config={"expected_schema": ROWS.model_dump(mode="json")},
        ),
        GraphIntentNodeV3(
            ref="aggregate_rows",
            kind="data_aggregate",
            title="Aggregate rows",
            inputs=[
                _input(
                    port="rows",
                    variable="rows",
                    source_ref="decode_rows",
                    source_port="value",
                    schema=ROWS,
                )
            ],
            outputs=[_output("result", "summary_rows", ROWS)],
            config={
                "group_by_fields": ["category"],
                "measures": [
                    {
                        "output_field": "count",
                        "operation": "count",
                        "source_field": "",
                    }
                ],
            },
        ),
        GraphIntentNodeV3(
            ref="encode_summary",
            kind="json_serialize",
            title="Encode summary",
            inputs=[
                _input(
                    port="value",
                    variable="summary_rows",
                    source_ref="aggregate_rows",
                    source_port="result",
                    schema=ROWS,
                )
            ],
            outputs=[_output("json", "summary_json", STRING)],
            config={"format": "compact"},
        ),
        _agent(
            ref="writer",
            task_id="deliver",
            source_ref="encode_summary",
            source_port="json",
            input_variable="summary_json",
            output_variable="agent_output",
        ),
    ]
    return GraphIntentV3(
        name="Aggregate evidence",
        nodes=nodes,
        control_edges=[
            GraphIntentControlEdgeV3(
                source_ref=nodes[index].ref,
                target_ref=nodes[index + 1].ref,
            )
            for index in range(len(nodes) - 1)
        ],
        final_output=GraphIntentFinalOutputV3(
            node_ref="writer",
            variable="agent_output",
        ),
    )


def _pack_intent() -> GraphIntentV3:
    pack = GraphIntentNodeV3(
        ref="pack_inputs",
        kind="variable_aggregator",
        title="Pack inputs",
        inputs=[
            _input(
                port="values",
                variable="user_input",
                source_ref="input",
                source_port="user_input",
                schema=STRING,
            ),
            _input(
                port="values",
                variable="conversation_history",
                source_ref="input",
                source_port="conversation_history",
                schema=WorkflowValueSchema(
                    type="array",
                    items=WorkflowValueSchema(type="object"),
                ),
            ),
        ],
        outputs=[_output("result", "packed_context", OBJECT)],
        config={"output_fields": ["request", "history"]},
    )
    encode = GraphIntentNodeV3(
        ref="encode_context",
        kind="json_serialize",
        title="Encode context",
        inputs=[
            _input(
                port="value",
                variable="packed_context",
                source_ref="pack_inputs",
                source_port="result",
                schema=OBJECT,
            )
        ],
        outputs=[_output("json", "context_json", STRING)],
        config={"format": "pretty"},
    )
    writer = _agent(
        ref="writer",
        task_id="deliver",
        source_ref="encode_context",
        source_port="json",
        input_variable="context_json",
        output_variable="agent_output",
    )
    return GraphIntentV3(
        name="Pack context",
        nodes=[pack, encode, writer],
        control_edges=[
            GraphIntentControlEdgeV3(
                source_ref="pack_inputs", target_ref="encode_context"
            ),
            GraphIntentControlEdgeV3(
                source_ref="encode_context", target_ref="writer"
            ),
        ],
        final_output=GraphIntentFinalOutputV3(
            node_ref="writer",
            variable="agent_output",
        ),
    )


def _compare_intent() -> GraphIntentV3:
    intent = _aggregate_intent()
    decoder = next(node for node in intent.nodes if node.ref == "decode_rows")
    second_decoder = decoder.model_copy(deep=True)
    second_decoder.ref = "decode_reference"
    second_decoder.title = "Decode reference"
    second_decoder.outputs[0].variable = "reference_rows"
    compare = GraphIntentNodeV3(
        ref="compare_rows",
        kind="dataset_compare",
        title="Compare rows",
        inputs=[
            _input(
                port="left",
                variable="rows",
                source_ref="decode_rows",
                source_port="value",
                schema=ROWS,
            ),
            _input(
                port="right",
                variable="reference_rows",
                source_ref="decode_reference",
                source_port="value",
                schema=ROWS,
            ),
        ],
        outputs=[_output("result", "comparison", OBJECT)],
        config={"key_fields": ["id"], "include_unchanged": False},
    )
    encoder = next(node for node in intent.nodes if node.ref == "encode_summary")
    encoder.inputs = [
        _input(
            port="value",
            variable="comparison",
            source_ref="compare_rows",
            source_port="result",
            schema=OBJECT,
        )
    ]
    intent.nodes = [
        intent.nodes[0],
        decoder,
        second_decoder,
        compare,
        encoder,
        intent.nodes[-1],
    ]
    intent.control_edges = [
        GraphIntentControlEdgeV3(
            source_ref="researcher", target_ref="decode_rows"
        ),
        GraphIntentControlEdgeV3(
            source_ref="researcher", target_ref="decode_reference"
        ),
        GraphIntentControlEdgeV3(
            source_ref="decode_rows", target_ref="compare_rows"
        ),
        GraphIntentControlEdgeV3(
            source_ref="decode_reference", target_ref="compare_rows"
        ),
        GraphIntentControlEdgeV3(
            source_ref="compare_rows", target_ref="encode_summary"
        ),
        GraphIntentControlEdgeV3(
            source_ref="encode_summary", target_ref="writer"
        ),
    ]
    return intent


@pytest.mark.parametrize(
    "intent_factory",
    [_aggregate_intent, _compare_intent],
)
def test_pure_node_packs_compile_and_round_trip_stably(intent_factory) -> None:
    request = _authorized_request()
    snapshot = _snapshot()
    plan = _plan()
    intent = intent_factory()

    assert validate_blueprint_authorization(request, plan, intent, snapshot) == []
    graph = resolve_graph_intent(intent, snapshot)
    candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=intent,
        snapshot=snapshot,
        target=None,
    )
    restored = decompile_candidate_to_graph_intent(candidate)
    restored_graph = resolve_graph_intent(restored, snapshot)
    rebuilt = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=restored,
        snapshot=snapshot,
        target=None,
    )

    assert graph.graph_checksum == restored_graph.graph_checksum
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(
        rebuilt
    )
    native_kinds = {
        (node.get("data") or {}).get("kind")
        for node in candidate["draft"]["workflow"]["nodes"]
    }
    assert {node.kind for node in intent.nodes}.issubset(native_kinds)


def test_pack_then_serialize_uses_repeated_many_port_without_covering_tasks() -> None:
    request = _authorized_request()
    snapshot = _snapshot()
    plan = _plan().model_copy(deep=True)
    plan.tasks = [plan.tasks[-1]]
    plan.tasks[0].depends_on = []
    intent = _pack_intent()

    assert validate_blueprint_authorization(request, plan, intent, snapshot) == []
    candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=intent,
        snapshot=snapshot,
        target=None,
    )
    pack = next(
        node
        for node in candidate["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "variable_aggregator"
    )
    assert pack["data"]["plannerTaskIds"] == []
    assert [item["outputField"] for item in pack["data"]["bindings"]] == [
        "request",
        "history",
    ]


def test_resolver_rejects_pure_task_ownership_and_pure_final_output() -> None:
    snapshot = _snapshot()
    intent = _aggregate_intent()
    pure = next(node for node in intent.nodes if node.ref == "decode_rows")
    pure.task_ids = ["research"]

    with pytest.raises(ValueError, match="cannot cover plan tasks"):
        resolve_graph_intent(intent, snapshot)

    intent = _aggregate_intent()
    intent.final_output = GraphIntentFinalOutputV3(
        node_ref="encode_summary",
        port="json",
        variable="summary_json",
    )
    intent.control_edges = [
        edge
        for edge in intent.control_edges
        if edge.target_ref != "writer"
    ]
    intent.nodes = [node for node in intent.nodes if node.ref != "writer"]
    with pytest.raises(ValueError, match="workflow_agent"):
        resolve_graph_intent(intent, snapshot)


def test_resolver_rejects_deserialize_schema_forgery_and_native_config_fields() -> None:
    snapshot = _snapshot()
    intent = _aggregate_intent()
    decoder = next(node for node in intent.nodes if node.ref == "decode_rows")
    decoder.outputs[0].value_schema = STRING

    with pytest.raises(ValueError, match="authoritative Adapter type"):
        resolve_graph_intent(intent, snapshot)

    intent = _aggregate_intent()
    decoder = next(node for node in intent.nodes if node.ref == "decode_rows")
    decoder.config["outputVariable"] = "forged_native_variable"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        resolve_graph_intent(intent, snapshot)


def test_graph_patch_adds_pure_node_atomically_and_rejects_task_injection() -> None:
    plan_task_ids = {"research", "deliver"}
    base = _aggregate_intent()
    base.nodes = [base.nodes[0], base.nodes[-1]]
    base.control_edges = [
        GraphIntentControlEdgeV3(source_ref="researcher", target_ref="writer")
    ]
    writer = next(node for node in base.nodes if node.ref == "writer")
    writer.inputs = [
        _input(
            port="task",
            variable="rows_json",
            source_ref="researcher",
            source_port="result",
            schema=STRING,
        )
    ]
    writer.config["role_prompt"] = "Write from {{rows_json}}."
    writer.config["task_input"] = "{{rows_json}}"
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[
            {
                "op": "add_node",
                "ref": "encode_rows",
                "kind": "json_serialize",
                "title": "Encode rows",
                "task_ids": [],
                "config": {"format": "compact"},
                "output_variables": {"json": "encoded_rows"},
            },
            {
                "op": "disconnect_control",
                "source_ref": "researcher",
                "target_ref": "writer",
            },
            {
                "op": "connect_control",
                "source_ref": "researcher",
                "target_ref": "encode_rows",
            },
            {
                "op": "connect_control",
                "source_ref": "encode_rows",
                "target_ref": "writer",
            },
            {
                "op": "disconnect_data",
                "source_ref": "researcher",
                "source_port": "result",
                "target_ref": "writer",
                "target_port": "task",
            },
            {
                "op": "connect_data",
                "source_ref": "researcher",
                "source_port": "result",
                "target_ref": "encode_rows",
                "target_port": "value",
            },
            {
                "op": "connect_data",
                "source_ref": "encode_rows",
                "source_port": "json",
                "target_ref": "writer",
                "target_port": "task",
            },
            {
                "op": "update_node",
                "ref": "writer",
                "config": {
                    "role_prompt": "Write from {{encoded_rows}}.",
                    "task_input": "{{encoded_rows}}",
                    "model_id": "model/agent",
                    "source_agent_id": None,
                    "method_skill_ids": [],
                },
            },
        ],
    )
    applied = apply_graph_patch(
        base,
        patch,
        plan_task_ids=plan_task_ids,
        allowed_node_kinds={"workflow_agent", "json_serialize"},
    )
    pure = next(node for node in applied.intent.nodes if node.ref == "encode_rows")
    assert pure.task_ids == []
    assert pure.outputs[0].value_schema == STRING
    resolve_graph_intent(applied.intent, _snapshot())

    attack = deepcopy(patch.model_dump(mode="json"))
    attack["operations"][0]["task_ids"] = ["deliver"]
    with pytest.raises(ValueError, match="cannot cover plan tasks"):
        apply_graph_patch(
            base,
            GraphPatchEnvelopeV1.model_validate(attack),
            plan_task_ids=plan_task_ids,
            allowed_node_kinds={"workflow_agent", "json_serialize"},
        )


@pytest.mark.parametrize(
    "operation",
    [
        {
            "op": "bind_resource",
            "target_ref": "decode_rows",
            "kind": "knowledge_base",
            "resource_id": "kb-docs",
        },
        {
            "op": "bind_middleware",
            "target_ref": "decode_rows",
            "middleware_id": "system_prompt_injector",
            "config": {"prompt": "must remain agent-only"},
        },
    ],
)
def test_attack_pure_nodes_cannot_receive_runtime_bindings(operation) -> None:
    intent = _aggregate_intent()
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[operation],
    )

    applied = apply_graph_patch(
        intent,
        patch,
        plan_task_ids={"research", "deliver"},
        allowed_node_kinds={"workflow_agent", *PURE_NODE_KINDS},
    )

    with pytest.raises(ValueError, match="workflow_agent"):
        resolve_graph_intent(applied.intent, _snapshot())


def test_attack_pure_node_type_confusion_and_control_cycle_fail_closed() -> None:
    snapshot = _snapshot()
    intent = _aggregate_intent()
    aggregate = next(node for node in intent.nodes if node.ref == "aggregate_rows")
    aggregate.inputs[0] = _input(
        port="rows",
        variable="rows_json",
        source_ref="researcher",
        source_port="result",
        schema=STRING,
    )

    with pytest.raises(ValueError) as type_error:
        resolve_graph_intent(intent, snapshot)
    assert any(
        marker in str(type_error.value).lower()
        for marker in ("schema", "type", "compatible")
    )

    intent = _aggregate_intent()
    intent.control_edges.append(
        GraphIntentControlEdgeV3(source_ref="writer", target_ref="researcher")
    )
    with pytest.raises(ValueError, match="acyclic|cycle"):
        resolve_graph_intent(intent, snapshot)


def test_attack_patch_update_cannot_inject_native_pure_node_fields() -> None:
    intent = _aggregate_intent()
    decoder = next(node for node in intent.nodes if node.ref == "decode_rows")
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[
            {
                "op": "update_node",
                "ref": decoder.ref,
                "config": {
                    "expected_schema": ROWS.model_dump(mode="json"),
                    "outputVariable": "forged_native_variable",
                },
            }
        ],
    )

    with pytest.raises(ValueError, match="undeclared Adapter config fields"):
        apply_graph_patch(
            intent,
            patch,
            plan_task_ids={"research", "deliver"},
            allowed_node_kinds={"workflow_agent", *PURE_NODE_KINDS},
        )
