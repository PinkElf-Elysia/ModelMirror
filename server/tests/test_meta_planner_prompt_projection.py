from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.meta_agent.generation_contract import graph_generation_schema
from server.meta_agent.schemas import (
    GraphIntentControlEdgeV3, GraphIntentNodeResourceRefV3, GraphIntentNodeV3,
    GraphIntentV3, MetaPlannerIRResourceBinding,
)
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot


def prompts(req, snap):
    return [json.loads(value) for value in (
        MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None),
        MetaPlannerV2Service._repair_prompt(req, _plan(), snap, "{}", ["invalid"]),
        MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, intent(), ["invalid"]),
    )]


def count_key(value, key):
    if isinstance(value, dict):
        return int(key in value) + sum(count_key(item, key) for item in value.values())
    if isinstance(value, list):
        return sum(count_key(item, key) for item in value)
    return 0


def test_all_stages_have_one_identical_authoritative_contract():
    snap = snapshot()
    req = request(snap)
    original = req.model_dump(mode="json")
    generated, repaired, patched = prompts(req, snap)
    for prompt in (generated, repaired, patched):
        assert count_key(prompt, "graph_intent_contract") == 1
        assert "graph_intent_contract" not in prompt["capability_snapshot"]
        assert prompt["graph_intent_contract"] == generated["graph_intent_contract"]
        assert prompt["goal"] == req.goal
    assert generated["required_schema"] == repaired["required_schema"] == graph_generation_schema(req, snap)
    assert generated["required_schema"] != GraphIntentV3.model_json_schema()
    assert req.model_dump(mode="json") == original


def test_control_example_uses_real_schema_without_native_aliases():
    snap = snapshot()
    for prompt in prompts(request(snap), snap):
        forms = prompt["graph_intent_contract"]["edge_and_resource_forms"]
        assert forms["examples_are_fragments"] is True
        assert forms["control_edges"]
        for raw in forms["control_edges"]:
            assert set(raw) == {"source_ref", "outcome_ref", "target_ref"}
            edge = GraphIntentControlEdgeV3.model_validate(raw)
            assert edge.source_ref != edge.target_ref
            assert edge.outcome_ref == "success"
            for alias in ("from", "to", "sourceHandle", "targetHandle"):
                with pytest.raises(ValidationError):
                    GraphIntentControlEdgeV3.model_validate({**raw, alias: "forged"})


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
@pytest.mark.parametrize("read", [False, True])
def test_node_examples_respect_read_and_per_operation_write_scope(operation, read):
    snap = snapshot()
    req = request(snap, operation, read=read)
    # Neither an unscoped catalog entry nor an absent authorized ID becomes an example.
    snap.data_tables.append({**deepcopy(snap.data_tables[0]), "id": "unscoped-table"})
    req.scope.data_table_ids.extend(["absent-table"])
    for prompt in prompts(req, snap):
        forms = prompt["graph_intent_contract"]["edge_and_resource_forms"]
        examples = forms["node_resources"]
        expected = {f"data_table_{operation}"} | ({"data_table_query"} if read else set())
        assert {item["kind"] for item in examples} == expected
        assert forms["agent_resources"] == []
        for item in examples:
            assert set(item) == {"kind", "resource_ref"}
            GraphIntentNodeV3.model_validate({"ref": "resource_step", "title": "资源步骤", **item})
            assert item["resource_ref"] == {"resource_id": "table-orders"}
            GraphIntentNodeResourceRefV3.model_validate(item["resource_ref"])
            for forbidden in ("kind", "target_ref", "version", "schema", "checksum"):
                with pytest.raises(ValidationError):
                    GraphIntentNodeResourceRefV3.model_validate({**item["resource_ref"], forbidden: "forged"})
            with pytest.raises(ValidationError):
                MetaPlannerIRResourceBinding.model_validate({
                    "target_ref": "answer", "kind": "data_table", "resource_id": "table-orders",
                })


def test_unselected_nodes_and_revoked_grants_have_no_resource_examples():
    snap = snapshot()
    req = request(snap)
    req.scope.allowed_node_kinds = ["input", "output", "workflow_agent"]
    for prompt in prompts(req, snap):
        forms = prompt["graph_intent_contract"]["edge_and_resource_forms"]
        assert forms["node_resources"] == forms["agent_resources"] == []
    req = request(snap, read=False)
    req.scope.data_table_write_grants = []
    for prompt in prompts(req, snap):
        assert prompt["graph_intent_contract"]["edge_and_resource_forms"]["node_resources"] == []


def test_knowledge_node_reference_is_distinct_from_agent_binding():
    snap = snapshot()
    req = request(snap, read=False)
    req.scope.data_table_write_grants = []
    req.scope.knowledge_base_ids = ["kb-synthetic"]
    snap.knowledge_bases = [{"id": "kb-synthetic", "name": "合成知识库", "metadata": {"active_version_id": "index-synthetic"}}]
    for prompt in prompts(req, snap):
        forms = prompt["graph_intent_contract"]["edge_and_resource_forms"]
        assert forms["node_resources"] == [{
            "kind": "knowledge_retrieval", "resource_ref": {"resource_id": "kb-synthetic"},
        }]
        binding, = forms["agent_resources"]
        assert set(binding) == {"target_ref", "kind", "resource_id"}
        assert binding["kind"] == "knowledge_base"
        assert binding["resource_id"] == "kb-synthetic"
        MetaPlannerIRResourceBinding.model_validate(binding)
