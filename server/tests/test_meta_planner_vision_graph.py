from copy import deepcopy

import pytest
from pydantic import ValidationError

from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, graph_ir_checksum, resolve_graph_intent, workflow_semantic_checksum
from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, UpdateNodeOperation, apply_graph_patch
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, compile_xpert_candidate
from server.meta_agent.schemas import GraphIntentNodeV3, GraphIntentOutputBindingV3, MetaPlannerScope
from server.meta_agent.vision_contract import ATTACHMENT_INPUT_PORT, VISION_ATTACHMENT_CONTRACT
from server.workflow_native.node_contracts import canonical_checksum, vision_understanding_result_schema
from server.xpert_runtime.middleware_registry import runtime_middleware_registry
from server.xpert_runtime.workflow_node_registry import workflow_node_registry
from server.xpert_runtime.workflow_vision import WorkflowVisionError, validate_vision_v2_request
from server.tests.test_meta_planner_read_resources import STRING, _input, _knowledge_intent, _plan, _request as read_request


def binding_fixture(**updates):
    payload = {
        "entry_id": "xpert_vision", "model_id": "model/vision",
        "execution_shape": "vision_json_unary", "connection_id": "connection-test",
        "certification_id": "cert-test", "connection_fingerprint": "c" * 64,
        "qualification_fingerprint": "q" * 64,
        "adapter_contract": "test-contract", "protocol_version": "1",
        **updates,
    }
    return {**payload, "checksum": canonical_checksum(payload)}


def vision_snapshot(binding=None):
    return build_capability_snapshot(
        workflow_registry=workflow_node_registry,
        middleware_registry=runtime_middleware_registry,
        external_xperts=[], knowledge_bases=[], toolsets=[], plugins=[],
        prompt_profiles=[], model_ids=["model/planner", "model/agent"],
        vision_models=[{
            "id": "model/vision", "label": "合成视觉模型", "safe": True,
            "binding": binding or binding_fixture(), "secret": "must-not-leak",
        }],
    )


def vision_request(snapshot=None):
    snapshot = snapshot or vision_snapshot()
    request = read_request(snapshot)
    request.goal = "根据显式附件提供中文摘要。"
    request.scope = MetaPlannerScope(allowed_node_kinds=[node["kind"] for node in snapshot.nodes])
    request.vision_model_id = "model/vision"
    return request


def vision_intent():
    intent = _knowledge_intent()
    intent.name = "附件视觉摘要"
    schema = vision_understanding_result_schema()
    intent.nodes[0] = GraphIntentNodeV3(
        ref="retrieve", kind="vision_understanding", title="理解附件",
        inputs=[_input("asset_id", ATTACHMENT_INPUT_PORT, "input", ATTACHMENT_INPUT_PORT, STRING)],
        outputs=[GraphIntentOutputBindingV3(port="result", variable="knowledge_result", value_schema=schema)],
        config={},
    )
    intent.nodes[1].inputs[0].value_schema = schema
    intent.nodes[1].title = "序列化视觉结果"
    intent.nodes[2].title = "生成摘要"
    intent.nodes[2].config["role_prompt"] = "仅根据视觉结果生成中文摘要。"
    return intent


def resolve(intent=None, snapshot=None):
    return resolve_graph_intent(intent or vision_intent(), snapshot or vision_snapshot(), default_agent_model_id="model/agent", vision_model_id="model/vision")


def test_capability_has_exactly_19_nodes_and_vision_is_opt_in():
    snapshot = vision_snapshot()
    assert len(snapshot.nodes) == 19
    assert "vision_understanding" not in snapshot.default_scope.allowed_node_kinds
    assert {"data_table_insert", "iteration", "human", "question_classifier"}.isdisjoint({node["kind"] for node in snapshot.nodes})
    assert "must-not-leak" not in snapshot.model_dump_json()
    assert snapshot.version.endswith("v9")


def test_vision_compile_decompile_compile_preserves_binding_and_slot():
    snapshot = vision_snapshot()
    request = vision_request(snapshot)
    candidate = compile_xpert_candidate(request=request, plan=_plan(), blueprint=vision_intent(), snapshot=snapshot, target=None)
    restored = decompile_candidate_to_graph_intent(candidate)
    again = compile_xpert_candidate(request=request, plan=_plan(), blueprint=restored, snapshot=snapshot, target=None)
    assert workflow_semantic_checksum(candidate["draft"]["workflow"]) == workflow_semantic_checksum(again["draft"]["workflow"])
    assert graph_ir_checksum(resolve()) == graph_ir_checksum(resolve(restored))
    root = next(node for node in candidate["draft"]["workflow"]["nodes"] if node["id"] == "input")
    assert root["data"]["plannerAttachmentInputV1"] == VISION_ATTACHMENT_CONTRACT
    native = next(node for node in candidate["draft"]["workflow"]["nodes"] if node["type"] == "vision_understanding")
    assert native["data"]["visionModelBinding"] == binding_fixture()
    assert native["data"]["contractVersion"] == 2


@pytest.mark.parametrize("source_ref,source_port,variable", [
    ("input", "user_input", "user_input"),
    ("answer", "result", "selected_file_asset_id"),
    ("encode", "json", "selected_file_asset_id"),
    ("input", "conversation_history", "selected_file_asset_id"),
])
def test_forged_attachment_source_is_rejected(source_ref, source_port, variable):
    intent = vision_intent()
    intent.nodes[0].inputs[0] = _input("asset_id", variable, source_ref, source_port, STRING)
    with pytest.raises(ValueError):
        resolve(intent)


@pytest.mark.parametrize("key,value", [
    ("assetIdVariable", "user_input"), ("visionModelId", "unapproved"),
    ("contractVersion", 1), ("sourceHandle", "attachment"),
    ("visionModelBinding", {"checksum": "forged"}), ("retryMode", "transient"),
])
def test_vision_adapter_rejects_native_configuration_injection(key, value):
    intent = vision_intent()
    intent.nodes[0].config[key] = value
    with pytest.raises(ValueError):
        resolve(intent)


def test_vision_result_must_be_consumed_by_agent():
    intent = vision_intent()
    intent.nodes[2].config["task_input"] = "请直接回答，不读取视觉结果。"
    with pytest.raises(ValueError, match="消费"):
        resolve(intent)


def test_attachment_slot_cannot_flow_to_agent():
    intent = vision_intent()
    intent.nodes[2].inputs.append(_input("task", ATTACHMENT_INPUT_PORT, "input", ATTACHMENT_INPUT_PORT, STRING))
    intent.nodes[2].config["task_input"] += "{{selected_file_asset_id}}"
    with pytest.raises(ValueError, match="附件"):
        resolve(intent)


def test_binding_change_invalidates_restored_candidate():
    candidate = compile_xpert_candidate(request=vision_request(), plan=_plan(), blueprint=vision_intent(), snapshot=vision_snapshot(), target=None)
    restored = decompile_candidate_to_graph_intent(candidate)
    with pytest.raises(ValueError, match="变化"):
        resolve(restored, vision_snapshot(binding_fixture(certification_id="cert-new")))


def test_patch_keeps_fixed_model_binding():
    candidate = compile_xpert_candidate(request=vision_request(), plan=_plan(), blueprint=vision_intent(), snapshot=vision_snapshot(), target=None)
    restored = decompile_candidate_to_graph_intent(candidate)
    result = apply_graph_patch(restored, GraphPatchEnvelopeV1(
        proposal_revision=1, expected_graph_checksum="a" * 64, expected_candidate_checksum="b" * 64,
        operations=[UpdateNodeOperation(ref="retrieve", config={"max_pages": 3})],
    ), plan_task_ids={"answer"}, allowed_node_kinds={node.kind for node in restored.nodes})
    assert result.intent._pinned_vision_model == restored._pinned_vision_model
    assert resolve(result.intent).nodes


@pytest.mark.asyncio
async def test_missing_vision_authorization_fails_before_planner_call():
    calls = []
    async def complete(*args):
        calls.append(args)
        raise AssertionError("不得派发")
    service = MetaPlannerV2Service(authoring_service=None, preflight=None, completion=complete)
    request = vision_request()
    request.vision_model_id = None
    with pytest.raises(ValueError, match="单独选择"):
        await service.generate(request, vision_snapshot())
    assert calls == []


@pytest.mark.parametrize("selected,ids", [(None, []), (["a"], ["a"]), ("a", ["a", "b"]), ("b", ["a"])])
def test_runtime_requires_one_explicit_asset(selected, ids):
    with pytest.raises(WorkflowVisionError):
        validate_vision_v2_request({
            "assetIdVariable": ATTACHMENT_INPUT_PORT,
            "visionModelId": "model/vision", "visionModelBinding": binding_fixture(),
        }, selected_asset_id=selected, runtime_run_type="xpert", runtime_metadata={"file_asset_ids": ids})


def test_runtime_binding_rejects_secret_fields_and_model_substitution():
    binding = binding_fixture()
    binding["credential"] = "must-not-leak"
    with pytest.raises(WorkflowVisionError, match="有效"):
        validate_vision_v2_request({"assetIdVariable": ATTACHMENT_INPUT_PORT, "visionModelId": "model/vision", "visionModelBinding": binding}, selected_asset_id="a", runtime_run_type="workflow", runtime_metadata={})
