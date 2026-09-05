from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent,
    resolve_graph_intent,
)
from server.meta_agent.graph_patch import (
    BindResourceOperation,
    ConnectDataOperation,
    GraphPatchEnvelopeV1,
    SetNodeResourceOperation,
    SetOutputVariableOperation,
    apply_graph_patch,
)
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    compile_xpert_candidate,
    validate_blueprint_authorization,
)
from server.meta_agent.schemas import (
    GraphIntentControlEdgeV3,
    GraphIntentFinalOutputSourceV3,
    GraphIntentFinalOutputV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeResourceRefV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
    MetaPlannerGenerateRequest,
    MetaPlannerIRResourceBinding,
    MetaPlannerScope,
    MetaPlannerTask,
    MetaPlannerTaskPlan,
)
from server.workflow_native.node_contracts import WorkflowValueSchema
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.middleware_registry import runtime_middleware_registry
from server.xpert_runtime.workflow_node_registry import workflow_node_registry
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


STRING = WorkflowValueSchema(type="string")
ANY = WorkflowValueSchema()


def _table_catalog(*, active_version: int = 1, include_v1: bool = True):
    versions = []
    if include_v1:
        versions.append(
            {
                "version": 1,
                "checksum": "schema-v1",
                "fields": [
                    {
                        "name": "sku",
                        "data_type": "string",
                        "required": True,
                        "default_value": "secret-default",
                    },
                    {
                        "name": "score",
                        "data_type": "number",
                        "required": False,
                    },
                ],
            }
        )
    if active_version == 2:
        versions.append(
            {
                "version": 2,
                "checksum": "schema-v2",
                "fields": [
                    {"name": "sku", "data_type": "string", "required": True},
                    {"name": "score", "data_type": "number", "required": False},
                    {"name": "region", "data_type": "string", "required": False},
                ],
            }
        )
    return [
        {
            "table_id": "table-orders",
            "name": "Orders",
            "description": "Synthetic order records",
            "status": "published",
            "active_schema_version": active_version,
            "schema_versions": versions,
            "records": [{"sku": "must-not-leak"}],
        }
    ]


def _snapshot(
    *,
    knowledge_version: str | None = "kb-v1",
    table_active_version: int = 1,
    include_table_v1: bool = True,
):
    return build_capability_snapshot(
        workflow_registry=workflow_node_registry,
        middleware_registry=runtime_middleware_registry,
        external_xperts=[],
        knowledge_bases=[
            {
                "id": "kb-docs",
                "name": "Docs",
                "active_version_id": knowledge_version,
            }
        ],
        data_tables=_table_catalog(
            active_version=table_active_version,
            include_v1=include_table_v1,
        ),
        toolsets=[],
        plugins=[],
        prompt_profiles=[],
        model_ids=["model/planner", "model/agent"],
    )


def _request(snapshot) -> MetaPlannerGenerateRequest:
    return MetaPlannerGenerateRequest(
        goal="Use one authorized read resource and answer.",
        planner_model_id="model/planner",
        default_agent_model_id="model/agent",
        max_agents=2,
        scope=MetaPlannerScope(
            allowed_node_kinds=[item["kind"] for item in snapshot.nodes],
            knowledge_base_ids=["kb-docs"],
            data_table_ids=["table-orders"],
        ),
    )


def _plan() -> MetaPlannerTaskPlan:
    return MetaPlannerTaskPlan(
        summary="Answer from a deterministic read.",
        tasks=[
            MetaPlannerTask(
                task_id="answer",
                title="Answer",
                objective="Interpret the retrieved value.",
                output_contract="A concise answer.",
            )
        ],
    )


def _input(
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


def _answer_node(
    *, source_ref: str, source_port: str, variable: str, schema: WorkflowValueSchema
) -> GraphIntentNodeV3:
    return GraphIntentNodeV3(
        ref="answer",
        kind="workflow_agent",
        title="Answer",
        task_ids=["answer"],
        inputs=[_input("task", variable, source_ref, source_port, schema)],
        outputs=[
            GraphIntentOutputBindingV3(
                port="result", variable="agent_output", value_schema=STRING
            )
        ],
        config={
            "role_prompt": "Use only the supplied resource result.",
            "task_input": "{{" + variable + "}}",
            "model_id": "model/agent",
        },
    )


def _serialize_node(
    *, source_ref: str, variable: str, schema: WorkflowValueSchema
) -> GraphIntentNodeV3:
    return GraphIntentNodeV3(
        ref="encode",
        kind="json_serialize",
        title="Encode resource result",
        inputs=[_input("value", variable, source_ref, "result", schema)],
        outputs=[
            GraphIntentOutputBindingV3(
                port="json", variable="encoded_resource", value_schema=STRING
            )
        ],
        config={"format": "compact"},
    )


def _knowledge_intent() -> GraphIntentV3:
    result_schema = WorkflowValueSchema(type="object")
    return GraphIntentV3(
        name="Knowledge read",
        nodes=[
            GraphIntentNodeV3(
                ref="retrieve",
                kind="knowledge_retrieval",
                title="Retrieve docs",
                inputs=[_input("query", "user_input", "input", "user_input", STRING)],
                outputs=[
                    GraphIntentOutputBindingV3(
                        port="result",
                        variable="knowledge_result",
                        value_schema=result_schema,
                    )
                ],
                resource_ref=GraphIntentNodeResourceRefV3(resource_id="kb-docs"),
                config={"top_k": 5, "return_mode": "result"},
            ),
            _serialize_node(
                source_ref="retrieve",
                variable="knowledge_result",
                schema=result_schema,
            ),
            _answer_node(
                source_ref="encode",
                source_port="json",
                variable="encoded_resource",
                schema=STRING,
            ),
        ],
        control_edges=[
            GraphIntentControlEdgeV3(source_ref="retrieve", target_ref="encode"),
            GraphIntentControlEdgeV3(source_ref="encode", target_ref="answer"),
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="answer")]
        ),
    )


def _table_intent(*, input_schema: WorkflowValueSchema = STRING) -> GraphIntentV3:
    result_schema = WorkflowValueSchema(type="array", items=WorkflowValueSchema(type="object"))
    return GraphIntentV3(
        name="Table read",
        nodes=[
            GraphIntentNodeV3(
                ref="lookup",
                kind="data_table_query",
                title="Lookup order",
                inputs=[
                    _input(
                        "predicate_sku",
                        "user_input",
                        "input",
                        "user_input",
                        input_schema,
                    )
                ],
                outputs=[
                    GraphIntentOutputBindingV3(
                        port="result", variable="rows", value_schema=ANY
                    )
                ],
                resource_ref=GraphIntentNodeResourceRefV3(
                    resource_id="table-orders"
                ),
                config={
                    "select_fields": ["sku", "score"],
                    "filter": {
                        "kind": "predicate",
                        "ref": "sku",
                        "field": "sku",
                        "operator": "eq",
                        "value_source": "input",
                    },
                    "sort": [{"field": "score", "direction": "desc"}],
                    "limit": 20,
                    "return_mode": "list",
                },
            ),
            _serialize_node(
                source_ref="lookup", variable="rows", schema=result_schema
            ),
            _answer_node(
                source_ref="encode",
                source_port="json",
                variable="encoded_resource",
                schema=STRING,
            ),
        ],
        control_edges=[
            GraphIntentControlEdgeV3(source_ref="lookup", target_ref="encode"),
            GraphIntentControlEdgeV3(source_ref="encode", target_ref="answer"),
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="answer")]
        ),
    )


def test_capability_snapshot_opens_exactly_two_reads_and_hides_table_data() -> None:
    snapshot = _snapshot()
    kinds = {item["kind"] for item in snapshot.nodes}

    assert len(kinds) == 18
    assert {"knowledge_retrieval", "data_table_query"} <= kinds
    assert snapshot.default_scope.data_table_ids == []
    assert snapshot.default_scope.knowledge_base_ids == ["kb-docs"]
    serialized = snapshot.model_dump_json()
    assert "secret-default" not in serialized
    assert "must-not-leak" not in serialized
    assert snapshot.data_tables[0]["fields"] == [
        {"name": "score", "type": "number", "required": False},
        {"name": "sku", "type": "string", "required": True},
    ]


def test_planner_prompt_exposes_read_error_as_control_outcome_only() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    prompt = json.loads(
        MetaPlannerV2Service._blueprint_prompt(
            request,
            _plan(),
            snapshot,
            None,
        )
    )
    contract = prompt["graph_intent_contract"]["node_roles"][
        "executable_node_contracts"
    ]["data_table_query"]

    assert contract["control_outcomes"] == ["success", "error"]
    assert [
        port["name"]
        for port in contract["ports"]
        if port["direction"] == "output"
    ] == ["result"]
    assert any(
        "workflow_agent task inputs are string boundaries" in rule
        for rule in prompt["graph_intent_contract"]["rules"]
    )
    guide = prompt["read_resource_authoring_guide"]
    table_guide = next(
        item
        for item in guide["node_owned_resources"]
        if item["node_kind"] == "data_table_query"
    )
    assert table_guide == {
        "resource_kind": "data_table",
        "node_kind": "data_table_query",
        "authorized_resource_ids": ["table-orders"],
        "resource_location": "nodes[].resource_ref.resource_id",
        "task_ids": [],
        "typed_result_bridge": (
            "Connect data_table_query.result to json_serialize.value before "
            "feeding the resulting string to workflow_agent.task."
        ),
    }
    assert any(
        "workflow_agent has only success" in rule for rule in guide["rules"]
    )
    plan_prompt = json.loads(MetaPlannerV2Service._plan_prompt(request, snapshot))
    assert "Read authorized Agent Table rows with data_table_query." in (
        plan_prompt["task_planning_contract"]["non_task_examples"]
    )

    repair_prompt = json.loads(
        MetaPlannerV2Service._repair_prompt(
            request,
            _plan(),
            snapshot,
            '{"invalid": true}',
            ["Node answer only permits outcomes: success"],
        )
    )
    assert repair_prompt["read_resource_authoring_guide"] == guide
    assert "must-not-leak" not in json.dumps(repair_prompt)


def test_named_table_read_cannot_be_replaced_by_agent_prompt_claim() -> None:
    snapshot = _snapshot()
    request = _request(snapshot).model_copy(
        update={"goal": "看看 Orders 表里的订单，再给我一个简短结论。"}
    )
    plan = _plan()
    agent_only = GraphIntentV3(
        name="订单助手",
        nodes=[
            _answer_node(
                source_ref="input",
                source_port="user_input",
                variable="user_input",
                schema=STRING,
            )
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="answer")]
        ),
    )
    agent_only.nodes[0].config["role_prompt"] = (
        "直接查询 Orders 数据表并回答，但不要使用任何读取节点。"
    )

    issues = validate_blueprint_authorization(
        request,
        plan,
        agent_only,
        snapshot,
    )

    assert any(
        "Required node-owned resource read data_table_query:table-orders is missing"
        in issue
        for issue in issues
    )

    prompt = json.loads(
        MetaPlannerV2Service._blueprint_prompt(request, plan, snapshot, None)
    )
    assert prompt["read_resource_authoring_guide"]["required_reads"] == [
        {
            "node_kind": "data_table_query",
            "resource_kind": "data_table",
            "resource_id": "table-orders",
            "reason": "goal_names_resource",
        }
    ]


def test_required_table_read_must_feed_an_agent_by_typed_data_edges() -> None:
    snapshot = _snapshot()
    request = _request(snapshot).model_copy(
        update={"goal": "读取 Orders 表并解释查询结果。"}
    )
    intent = _table_intent()
    answer = next(node for node in intent.nodes if node.ref == "answer")
    answer.inputs = [
        _input("task", "user_input", "input", "user_input", STRING)
    ]
    answer.config["task_input"] = "{{user_input}}"
    intent.nodes = [node for node in intent.nodes if node.ref != "encode"]
    intent.control_edges = []

    issues = validate_blueprint_authorization(request, _plan(), intent, snapshot)

    assert any(
        "Required node-owned resource read data_table_query:table-orders is not "
        "consumed by a workflow_agent through typed data bindings"
        in issue
        for issue in issues
    )


def test_resource_ref_and_adapter_config_reject_runtime_owned_injection() -> None:
    with pytest.raises(ValidationError):
        GraphIntentNodeResourceRefV3.model_validate(
            {"resource_id": "table-orders", "pinned_schema_version": 999}
        )

    intent = _table_intent()
    intent.nodes[0].config["tableId"] = "attacker-table"
    issues = validate_blueprint_authorization(
        _request(_snapshot()), _plan(), intent, _snapshot()
    )
    assert any("tableId: Extra inputs are not permitted" in issue for issue in issues)


def test_knowledge_pointer_drift_warns_but_missing_active_index_blocks() -> None:
    initial = _snapshot(knowledge_version="kb-v1")
    intent = _knowledge_intent()
    candidate = compile_xpert_candidate(
        request=_request(initial),
        plan=_plan(),
        blueprint=intent,
        snapshot=initial,
        target=None,
    )
    restored = decompile_candidate_to_graph_intent(candidate)

    moved = _snapshot(knowledge_version="kb-v2")
    resolved = resolve_graph_intent(
        restored, moved, default_agent_model_id="model/agent"
    )
    retrieval = next(node for node in resolved.nodes if node.ref == "retrieve")
    assert retrieval.resource_snapshot is not None
    assert retrieval.resource_snapshot.observed_version_id == "kb-v2"
    assert retrieval.resource_snapshot.warnings == [
        "Knowledge base kb-docs active index changed from kb-v1 to kb-v2."
    ]

    with pytest.raises(ValueError, match="no active index version"):
        resolve_graph_intent(
            restored,
            _snapshot(knowledge_version=None),
            default_agent_model_id="model/agent",
        )


def test_knowledge_decompile_rejects_forged_dynamic_provenance() -> None:
    initial = _snapshot(knowledge_version="kb-v1")
    candidate = compile_xpert_candidate(
        request=_request(initial),
        plan=_plan(),
        blueprint=_knowledge_intent(),
        snapshot=initial,
        target=None,
    )
    tampered = deepcopy(candidate)
    native = next(
        node
        for node in tampered["draft"]["workflow"]["nodes"]
        if node["data"].get("plannerRef") == "retrieve"
    )
    native["data"]["observedActiveVersionId"] = "forged-version"

    with pytest.raises(ValueError, match="resource metadata has drifted"):
        decompile_candidate_to_graph_intent(tampered)


def test_table_schema_is_pinned_and_authoritative_output_survives_round_trip() -> None:
    initial = _snapshot(table_active_version=1)
    intent = _table_intent()
    candidate = compile_xpert_candidate(
        request=_request(initial),
        plan=_plan(),
        blueprint=intent,
        snapshot=initial,
        target=None,
    )
    native = next(
        node
        for node in candidate["draft"]["workflow"]["nodes"]
        if node["data"].get("plannerRef") == "lookup"
    )
    assert native["data"]["pinnedSchemaVersion"] == 1
    planner_output = native["data"]["plannerOutputsV3"][0]["value_schema"]
    assert planner_output["type"] == "array"
    assert set(planner_output["items"]["properties"]) == {
        "record_id",
        "created_at",
        "updated_at",
        "revision",
        "sku",
        "score",
    }

    restored = decompile_candidate_to_graph_intent(candidate)
    advanced = _snapshot(table_active_version=2)
    resolved = resolve_graph_intent(
        restored, advanced, default_agent_model_id="model/agent"
    )
    table_node = next(node for node in resolved.nodes if node.ref == "lookup")
    assert table_node.resource_snapshot is not None
    assert table_node.resource_snapshot.pinned_schema_version == 1
    rebuilt = compile_xpert_candidate(
        request=_request(advanced),
        plan=_plan(),
        blueprint=restored,
        snapshot=advanced,
        target=None,
    )
    rebuilt_native = next(
        node
        for node in rebuilt["draft"]["workflow"]["nodes"]
        if node["data"].get("plannerRef") == "lookup"
    )
    assert rebuilt_native["data"]["pinnedSchemaVersion"] == 1
    assert rebuilt_native["data"]["pinnedSchemaChecksum"] == "schema-v1"
    assert rebuilt_native["data"]["selectFields"] == ["sku", "score"]

    with pytest.raises(ValueError, match="pinned SchemaVersion 1 is unavailable"):
        resolve_graph_intent(
            restored,
            _snapshot(table_active_version=2, include_table_v1=False),
            default_agent_model_id="model/agent",
        )


def test_table_dynamic_predicate_type_is_checked_against_fixed_schema() -> None:
    intent = _table_intent(input_schema=WorkflowValueSchema(type="number"))
    issues = validate_blueprint_authorization(
        _request(_snapshot()), _plan(), intent, _snapshot()
    )
    assert any("input type does not match its fixed SchemaVersion" in issue for issue in issues)


def test_set_node_resource_is_distinct_from_agent_resource_binding() -> None:
    intent = _table_intent()
    checksums = "a" * 64
    result = apply_graph_patch(
        intent,
        GraphPatchEnvelopeV1(
            proposal_revision=1,
            expected_graph_checksum=checksums,
            expected_candidate_checksum=checksums,
            operations=[
                SetNodeResourceOperation(
                    node_ref="lookup", resource_id="table-orders-next"
                )
            ],
        ),
        plan_task_ids={"answer"},
        allowed_node_kinds={"workflow_agent", "data_table_query"},
    )
    assert result.intent.nodes[0].resource_ref.resource_id == "table-orders-next"

    mixed = deepcopy(intent)
    mixed.resources.append(
        MetaPlannerIRResourceBinding(
            target_ref="lookup",
            kind="knowledge_base",
            resource_id="kb-docs",
        )
    )
    issues = validate_blueprint_authorization(
        _request(_snapshot()), _plan(), mixed, _snapshot()
    )
    assert "Resource kb-docs must target a workflow_agent ref." in issues

    with pytest.raises(ValueError, match="does not own a dynamic resource"):
        apply_graph_patch(
            intent,
            GraphPatchEnvelopeV1(
                proposal_revision=1,
                expected_graph_checksum=checksums,
                expected_candidate_checksum=checksums,
                operations=[
                    SetNodeResourceOperation(
                        node_ref="answer", resource_id="table-orders"
                    )
                ],
            ),
            plan_task_ids={"answer"},
            allowed_node_kinds={"workflow_agent", "data_table_query"},
        )

    with pytest.raises(ValueError, match="no output port error"):
        apply_graph_patch(
            intent,
            GraphPatchEnvelopeV1(
                proposal_revision=1,
                expected_graph_checksum=checksums,
                expected_candidate_checksum=checksums,
                operations=[
                    SetOutputVariableOperation(
                        node_ref="lookup",
                        port="error",
                        variable="forged_error_data",
                    )
                ],
            ),
            plan_task_ids={"answer"},
            allowed_node_kinds={"workflow_agent", "data_table_query"},
        )

    with pytest.raises(ValueError, match="Data edge already exists"):
        apply_graph_patch(
            intent,
            GraphPatchEnvelopeV1(
                proposal_revision=1,
                expected_graph_checksum=checksums,
                expected_candidate_checksum=checksums,
                operations=[
                    ConnectDataOperation(
                        source_ref="lookup",
                        source_port="result",
                        target_ref="encode",
                        target_port="value",
                    )
                ],
            ),
            plan_task_ids={"answer"},
            allowed_node_kinds={
                "workflow_agent",
                "data_table_query",
                "json_serialize",
            },
        )


def test_bind_resource_operation_cannot_smuggle_node_owned_resource() -> None:
    intent = _table_intent()
    checksums = "a" * 64
    result = apply_graph_patch(
        intent,
        GraphPatchEnvelopeV1(
            proposal_revision=1,
            expected_graph_checksum=checksums,
            expected_candidate_checksum=checksums,
            operations=[
                BindResourceOperation(
                    target_ref="lookup",
                    kind="knowledge_base",
                    resource_id="kb-docs",
                )
            ],
        ),
        plan_task_ids={"answer"},
        allowed_node_kinds={"workflow_agent", "data_table_query"},
    )
    issues = validate_blueprint_authorization(
        _request(_snapshot()), _plan(), result.intent, _snapshot()
    )
    assert "Resource kb-docs must target a workflow_agent ref." in issues


def test_error_branch_cannot_consume_success_only_resource_result() -> None:
    intent = _table_intent()
    result_schema = WorkflowValueSchema(
        type="array", items=WorkflowValueSchema(type="object")
    )
    intent.nodes[0].config["failure_action"] = "error_output"
    intent.nodes.append(
        GraphIntentNodeV3(
            ref="error_encode",
            kind="json_serialize",
            title="Encode unavailable result",
            inputs=[
                _input("value", "rows", "lookup", "result", result_schema)
            ],
            outputs=[
                GraphIntentOutputBindingV3(
                    port="json",
                    variable="error_rows_json",
                    value_schema=STRING,
                )
            ],
            config={"format": "compact"},
        )
    )
    intent.nodes.append(
        GraphIntentNodeV3(
            ref="fallback",
            kind="workflow_agent",
            title="Fallback",
            task_ids=["answer"],
            inputs=[
                _input(
                    "task",
                    "error_rows_json",
                    "error_encode",
                    "json",
                    STRING,
                )
            ],
            outputs=[
                GraphIntentOutputBindingV3(
                    port="result",
                    variable="fallback_output",
                    value_schema=STRING,
                )
            ],
            config={
                "role_prompt": "Explain that the lookup failed.",
                "task_input": "{{error_rows_json}}",
                "model_id": "model/agent",
            },
        )
    )
    intent.control_edges.append(
        GraphIntentControlEdgeV3(
            source_ref="lookup",
            outcome_ref="error",
            target_ref="error_encode",
        )
    )
    intent.control_edges.append(
        GraphIntentControlEdgeV3(
            source_ref="error_encode",
            outcome_ref="success",
            target_ref="fallback",
        )
    )
    intent.final_output.sources.append(
        GraphIntentFinalOutputSourceV3(node_ref="fallback")
    )

    with pytest.raises(ValueError, match="lookup.result is not available"):
        resolve_graph_intent(
            intent,
            _snapshot(),
            default_agent_model_id="model/agent",
        )


@pytest.mark.asyncio
async def test_patch_repair_restores_missing_table_result_output(
    tmp_path: Path,
) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    authoring = AuthoringService(
        proposal_store,
        XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
    )
    invalid = _table_intent()
    lookup = next(node for node in invalid.nodes if node.ref == "lookup")
    lookup.outputs = []
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        prompt = json.loads(user_prompt)
        assert any(
            "lookup.result" in item
            for item in prompt["repair_contract"]["issue_playbook"]
        )
        return json.dumps({"operations": []})

    response = await MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
        completion=complete,
    ).generate(_request(_snapshot()), _snapshot())

    assert len(calls) == 3
    assert response.repair_used is True
    assert response.validation["valid"] is True
    assert any(
        "lookup.result" in warning and "restored required output" in warning
        for warning in response.warnings
    )
    proposal = proposal_store.require(response.proposal_id)
    native_lookup = next(
        node
        for node in proposal.payload["draft"]["workflow"]["nodes"]
        if node["data"].get("plannerRef") == "lookup"
    )
    assert native_lookup["data"]["outputVariable"] == "rows"


@pytest.mark.asyncio
async def test_patch_repair_removes_control_only_table_error_output(
    tmp_path: Path,
) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    authoring = AuthoringService(
        proposal_store,
        XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
    )
    invalid = _table_intent()
    lookup = next(node for node in invalid.nodes if node.ref == "lookup")
    lookup.config["failure_action"] = "error_output"
    lookup.outputs.append(
        GraphIntentOutputBindingV3(
            port="error",
            variable="lookup_error",
            value_schema=WorkflowValueSchema(type="object"),
        )
    )
    invalid.nodes.append(
        GraphIntentNodeV3(
            ref="query_failed",
            kind="terminate_error",
            title="Stop after query failure",
            config={
                "error_code": "TABLE_QUERY_FAILED",
                "message": "The incident table query failed.",
            },
        )
    )
    invalid.control_edges.append(
        GraphIntentControlEdgeV3(
            source_ref="lookup",
            outcome_ref="error",
            target_ref="query_failed",
        )
    )
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        prompt = json.loads(user_prompt)
        return json.dumps(
            {
                "operations": [
                    {
                        "op": "connect_data",
                        "source_ref": "lookup",
                        "source_port": "result",
                        "target_ref": "encode",
                        "target_port": "value",
                    }
                ],
            }
        )

    response = await MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
        completion=complete,
    ).generate(_request(_snapshot()), _snapshot())

    assert len(calls) == 3
    assert response.repair_used is True
    assert response.validation["valid"] is True
    assert any(
        "lookup.error" in warning and "control-only" in warning
        for warning in response.warnings
    )
    assert any(
        "lookup.result->encode.value" in warning
        and "duplicate data-edge" in warning
        for warning in response.warnings
    )
    proposal = proposal_store.require(response.proposal_id)
    native_lookup = next(
        node
        for node in proposal.payload["draft"]["workflow"]["nodes"]
        if node["data"].get("plannerRef") == "lookup"
    )
    assert native_lookup["data"]["failureAction"] == "error_output"
    assert len(native_lookup["data"]["plannerOutputsV3"]) == 1
    assert native_lookup["data"]["plannerOutputsV3"][0]["port"] == "result"


@pytest.mark.asyncio
async def test_patch_repair_routes_typed_table_result_through_json(
    tmp_path: Path,
) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    authoring = AuthoringService(
        proposal_store,
        XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
    )
    invalid = _table_intent()
    lookup = next(node for node in invalid.nodes if node.ref == "lookup")
    answer = next(node for node in invalid.nodes if node.ref == "answer")
    result_schema = WorkflowValueSchema(
        type="array",
        items=WorkflowValueSchema(type="object"),
    )
    answer.inputs = [
        _input("task", "rows", "lookup", "result", result_schema)
    ]
    answer.config["task_input"] = "{{rows}}"
    invalid.nodes = [lookup, answer]
    invalid.control_edges = [
        GraphIntentControlEdgeV3(source_ref="lookup", target_ref="answer")
    ]
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        prompt = json.loads(user_prompt)
        assert any(
            "lookup.result->answer.task" in item and "json_serialize" in item
            for item in prompt["repair_contract"]["issue_playbook"]
        )
        return json.dumps(
            {
                "operations": [
                    {
                        "op": "disconnect_data",
                        "source_ref": "lookup",
                        "source_port": "result",
                        "target_ref": "answer",
                        "target_port": "task",
                    },
                    {
                        "op": "disconnect_control",
                        "source_ref": "lookup",
                        "outcome_ref": "success",
                        "target_ref": "answer",
                    },
                    {
                        "op": "add_node",
                        "ref": "encode_rows",
                        "kind": "json_serialize",
                        "title": "Encode table rows",
                        "description": "Render the typed rows for the Agent prompt.",
                        "task_ids": [],
                        "config": {"format": "compact"},
                        "output_variables": {"json": "rows_json"},
                    },
                    {
                        "op": "update_node",
                        "ref": "answer",
                        "config": {
                            "role_prompt": "Use only the supplied resource result.",
                            "task_input": "{{rows_json}}",
                            "model_id": "model/agent",
                            "source_agent_id": None,
                            "method_skill_ids": [],
                        },
                    },
                    {
                        "op": "connect_data",
                        "source_ref": "lookup",
                        "source_port": "result",
                        "target_ref": "encode_rows",
                        "target_port": "value",
                    },
                    {
                        "op": "connect_data",
                        "source_ref": "encode_rows",
                        "source_port": "json",
                        "target_ref": "answer",
                        "target_port": "task",
                    },
                    {
                        "op": "connect_control",
                        "source_ref": "lookup",
                        "outcome_ref": "success",
                        "target_ref": "encode_rows",
                    },
                    {
                        "op": "connect_control",
                        "source_ref": "encode_rows",
                        "outcome_ref": "success",
                        "target_ref": "answer",
                    },
                ],
            }
        )

    snapshot = _snapshot()
    response = await MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
        completion=complete,
    ).generate(_request(snapshot), snapshot)

    assert len(calls) == 3
    assert response.repair_used is True
    assert response.validation["valid"] is True, json.dumps(
        response.validation,
        ensure_ascii=False,
        indent=2,
    )
    proposal = proposal_store.require(response.proposal_id)
    nodes = proposal.payload["draft"]["workflow"]["nodes"]
    assert any(node["type"] == "json_serialize" for node in nodes)


@pytest.mark.asyncio
async def test_patch_repair_realizes_named_table_read_instead_of_prompt_claim(
    tmp_path: Path,
) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    authoring = AuthoringService(
        proposal_store,
        XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
    )
    snapshot = _snapshot()
    request = _request(snapshot).model_copy(
        update={"goal": "读取 Orders 表并用中文解释结果。"}
    )
    plan = _plan()
    agent_only = GraphIntentV3(
        name="订单助手",
        nodes=[
            _answer_node(
                source_ref="input",
                source_port="user_input",
                variable="user_input",
                schema=STRING,
            )
        ],
        final_output=GraphIntentFinalOutputV3(
            sources=[GraphIntentFinalOutputSourceV3(node_ref="answer")]
        ),
    )
    calls: list[dict[str, object]] = []

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if len(calls) == 1:
            return plan.model_dump_json()
        if len(calls) == 2:
            return agent_only.model_dump_json()
        prompt = json.loads(user_prompt)
        assert any(
            "required read data_table_query:table-orders" in item
            and "set_node_resource" in item
            for item in prompt["repair_contract"]["issue_playbook"]
        )
        return json.dumps(
            {
                "operations": [
                    {
                        "op": "add_node",
                        "ref": "lookup",
                        "kind": "data_table_query",
                        "title": "查询订单",
                        "description": "从固定表结构中读取允许字段。",
                        "task_ids": [],
                        "config": {
                            "select_fields": ["sku", "score"],
                            "filter": None,
                            "sort": [{"field": "score", "direction": "desc"}],
                            "limit": 20,
                            "return_mode": "list",
                            "failure_action": "stop",
                            "retry_mode": "none",
                            "max_attempts": 2,
                        },
                        "output_variables": {"result": "rows"},
                    },
                    {
                        "op": "set_node_resource",
                        "node_ref": "lookup",
                        "resource_id": "table-orders",
                    },
                    {
                        "op": "add_node",
                        "ref": "encode_rows",
                        "kind": "json_serialize",
                        "title": "序列化查询结果",
                        "description": "将类型化记录转换为智能体可读的 JSON。",
                        "task_ids": [],
                        "config": {"format": "compact"},
                        "output_variables": {"json": "rows_json"},
                    },
                    {
                        "op": "update_node",
                        "ref": "answer",
                        "config": {
                            "role_prompt": "仅依据已提供的订单记录，用中文回答。",
                            "task_input": "用户请求：{{user_input}}\n订单记录：{{rows_json}}",
                            "model_id": "model/agent",
                            "source_agent_id": None,
                            "method_skill_ids": [],
                        },
                    },
                    {
                        "op": "connect_data",
                        "source_ref": "lookup",
                        "source_port": "result",
                        "target_ref": "encode_rows",
                        "target_port": "value",
                    },
                    {
                        "op": "connect_data",
                        "source_ref": "encode_rows",
                        "source_port": "json",
                        "target_ref": "answer",
                        "target_port": "task",
                    },
                    {
                        "op": "connect_control",
                        "source_ref": "lookup",
                        "outcome_ref": "success",
                        "target_ref": "encode_rows",
                    },
                    {
                        "op": "connect_control",
                        "source_ref": "encode_rows",
                        "outcome_ref": "success",
                        "target_ref": "answer",
                    },
                ]
            }
        )

    response = await MetaPlannerV2Service(
        authoring_service=authoring,
        preflight=lambda candidate: (
            validate_xpert_definition(candidate),
            candidate.draft.workflow,
            [],
        ),
        completion=complete,
    ).generate(request, snapshot)

    assert len(calls) == 3
    assert response.repair_used is True
    assert response.validation["valid"] is True, json.dumps(
        response.validation,
        ensure_ascii=False,
        indent=2,
    )
    nodes = response.candidate["draft"]["workflow"]["nodes"]
    lookup = next(node for node in nodes if node["type"] == "data_table_query")
    assert lookup["data"]["tableId"] == "table-orders"
    assert lookup["data"]["pinnedSchemaVersion"] == 1
    assert any(node["type"] == "json_serialize" for node in nodes)
