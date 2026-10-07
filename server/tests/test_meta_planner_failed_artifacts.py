from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture
from server.xpert_runtime.authoring_store import (
    AuthoringProposalStore, AuthoringProposalValidationError,
)


PRIVATE_KEY = "_meta_planner_generation_artifact"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("失败产物测试禁止网络、模型 Provider 和业务记录操作")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


async def generate(tmp_path, monkeypatch, *, graph=None, repair=None, bad_plan=False):
    headless, authoring, _, snapshot = fixture(tmp_path, monkeypatch, "update")
    malformed = intent("update")
    malformed.nodes[1].inputs = []
    malformed.nodes[-1].config["role_prompt"] = "PRIVATE_AUTHORING_PROMPT_CANARY"
    responses = [_plan().model_dump_json(), graph if graph is not None else malformed.model_dump_json()]
    if bad_plan:
        responses.insert(0, "{}")
    else:
        responses.append(repair if repair is not None else '{"operations": []}')
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        return responses[len(calls) - 1]

    planner = LegacyGraphReplayService(
        authoring_service=authoring, preflight=headless.planner_service.preflight,
        completion=completion,
    )
    result = await planner.generate(request(snapshot, "update"), snapshot)
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    return result, proposal, authoring, calls, malformed


@pytest.mark.asyncio
async def test_failed_graph_survives_fallback_and_is_not_a_public_report(tmp_path, monkeypatch):
    result, proposal, authoring, calls, graph = await generate(tmp_path, monkeypatch)
    assert not result.validation["valid"] and len(calls) == 3
    assert PRIVATE_KEY in proposal.payload, "失败原图被占位候选覆盖，无法进行后续人工修复"
    from server.meta_agent.failed_artifacts import load_failed_generation_artifact

    artifact = load_failed_generation_artifact(proposal)
    assert artifact["attempts"][0]["intent"] == graph.model_dump(mode="json")
    assert artifact["attempts"][0]["diagnostic_subject"] == "intent_result"
    assert artifact["attempts"][1]["diagnostic_subject"] == "repair_input"
    assert artifact["attempts"][1]["result_intent_checksum"] is None
    assert artifact["attempts"][1]["intent"] == artifact["attempts"][0]["intent"]
    report = proposal.payload["meta_planner_report"]
    assert report["candidate_origin"] == "server_synthesized_fallback"
    assert report["failure_artifact"]["recoverable"] is True
    assert report["failure_artifact"]["executable"] is False
    for include_payload in (False, True):
        public = AuthoringProposalStore.serialize(proposal, include_payload=include_payload)
        assert PRIVATE_KEY not in json.dumps(public)
        assert "PRIVATE_AUTHORING_PROMPT_CANARY" not in json.dumps(public)
    assert "PRIVATE_AUTHORING_PROMPT_CANARY" not in json.dumps(result.model_dump(mode="json"))
    reloaded = AuthoringProposalStore(authoring.proposal_store.storage_dir).require(proposal.proposal_id)
    assert load_failed_generation_artifact(reloaded) == artifact
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(proposal.proposal_id, revision=1)
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_parse_failures_do_not_reconstruct_a_graph_from_fallback(tmp_path, monkeypatch):
    result, proposal, _, calls, _ = await generate(tmp_path, monkeypatch, graph="not-json", repair="not-json")
    assert len(calls) == 3 and not result.validation["valid"]
    from server.meta_agent.failed_artifacts import load_failed_generation_artifact

    artifact = load_failed_generation_artifact(proposal)
    assert len(artifact["attempts"]) == 2
    assert all(item["intent"] is None for item in artifact["attempts"])
    assert all(item["retention_status"] == "unparsed" for item in artifact["attempts"])
    assert not proposal.payload["meta_planner_report"]["failure_artifact"]["recoverable"]
    assert "not-json" not in json.dumps(artifact)


@pytest.mark.asyncio
async def test_attempt_consumed_by_plan_repair_does_not_invent_graph_repair(tmp_path, monkeypatch):
    _, proposal, _, calls, _ = await generate(tmp_path, monkeypatch, bad_plan=True)
    assert len(calls) == 3
    from server.meta_agent.failed_artifacts import load_failed_generation_artifact

    artifact = load_failed_generation_artifact(proposal)
    assert len(artifact["attempts"]) == 1
    assert artifact["attempts"][0]["stage"] == "capability_compile"


@pytest.mark.asyncio
async def test_successful_generation_does_not_duplicate_private_prompts(tmp_path, monkeypatch):
    result, proposal, _, calls, _ = await generate(tmp_path, monkeypatch, graph=intent("update").model_dump_json())
    assert result.validation["valid"] and len(calls) == 2
    assert PRIVATE_KEY not in proposal.payload
    assert "failure_artifact" not in proposal.payload["meta_planner_report"]


@pytest.mark.asyncio
async def test_artifact_is_immutable_under_legacy_edits_and_bound_to_revision(tmp_path, monkeypatch):
    _, proposal, authoring, _, _ = await generate(tmp_path, monkeypatch)
    from server.meta_agent.failed_artifacts import FailedArtifactUnavailable, load_failed_generation_artifact

    before = deepcopy(proposal.payload[PRIVATE_KEY])
    forged = deepcopy(proposal.payload)
    forged[PRIVATE_KEY]["attempts"][0]["intent"]["name"] = "伪造的失败图"
    with pytest.raises(AuthoringProposalValidationError):
        authoring.update_pending(proposal.proposal_id, revision=1, payload=forged)
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1
    payload = AuthoringProposalStore.serialize(proposal, include_payload=True)["payload"]
    payload["description"] = "人工修改候选"
    updated = authoring.update_pending(proposal.proposal_id, revision=1, payload=payload)
    assert updated.payload[PRIVATE_KEY] == before
    with pytest.raises(FailedArtifactUnavailable, match="stale"):
        load_failed_generation_artifact(updated)


@pytest.mark.asyncio
async def test_corrupt_artifact_and_legacy_missing_artifact_fail_closed(tmp_path, monkeypatch):
    _, proposal, _, _, _ = await generate(tmp_path, monkeypatch)
    from server.meta_agent.failed_artifacts import FailedArtifactUnavailable, load_failed_generation_artifact

    corrupted = deepcopy(proposal)
    corrupted.payload[PRIVATE_KEY]["attempts"][0]["intent"]["name"] = "篡改"
    with pytest.raises(FailedArtifactUnavailable, match="checksum"):
        load_failed_generation_artifact(corrupted)
    legacy = deepcopy(proposal)
    legacy.payload.pop(PRIVATE_KEY)
    with pytest.raises(FailedArtifactUnavailable, match="not_retained"):
        load_failed_generation_artifact(legacy)


@pytest.mark.parametrize("include_payload", [False, True])
def test_private_capture_never_leaves_store_serialization(tmp_path, include_payload):
    store = AuthoringProposalStore(tmp_path)
    item = store.create(kind="xpert_create", title="旧提案", payload={"name": "旧提案"}, source_type="meta_planner", source_id="offline")
    item.payload[PRIVATE_KEY] = {"intent": "PRIVATE_CAPTURE_CANARY"}
    public = store.serialize(item, include_payload=include_payload)
    assert "PRIVATE_CAPTURE_CANARY" not in json.dumps(public)
    assert PRIVATE_KEY not in json.dumps(public)


@pytest.mark.asyncio
async def test_recompiled_failed_patch_retains_its_new_intent_not_the_base(tmp_path, monkeypatch):
    from server.meta_agent.schemas import GraphIntentControlEdgeV3

    graph = intent("update")
    graph.control_edges.append(GraphIntentControlEdgeV3(source_ref="answer", target_ref="lookup"))
    repair = {"operations": [{"op": "set_xpert_metadata", "name": "修复尝试的新名称"}]}
    result, proposal, _, calls, _ = await generate(
        tmp_path, monkeypatch, graph=graph.model_dump_json(), repair=json.dumps(repair),
    )
    assert len(calls) == 3 and not result.validation["valid"]
    from server.meta_agent.failed_artifacts import load_failed_generation_artifact

    first, second = load_failed_generation_artifact(proposal)["attempts"]
    assert second["diagnostics"]["recompile_executed"]
    assert second["diagnostic_subject"] == "intent_result"
    assert first["intent"]["name"] == graph.name
    assert second["intent"]["name"] == "修复尝试的新名称"
    assert first["intent_checksum"] != second["intent_checksum"]
    assert second["result_intent_checksum"] == second["intent_checksum"]
    assert second["diagnostics"]["failed_phase"] == "authorization"
    assert all(item["status"] == "blocked" for item in second["diagnostics"]["phase_results"] if item["id"] in {"resolve", "compile", "publish_preflight"})


@pytest.mark.asyncio
async def test_atomic_patch_failure_does_not_save_its_partial_mutation(tmp_path, monkeypatch):
    repair = {"operations": [
        {"op": "set_xpert_metadata", "name": "不应被持久化的中间修改"},
        {"op": "connect_control", "source_ref": "lookup", "target_ref": "write"},
    ]}
    _, proposal, _, _, original = await generate(tmp_path, monkeypatch, repair=json.dumps(repair))
    from server.meta_agent.failed_artifacts import load_failed_generation_artifact

    last = load_failed_generation_artifact(proposal)["attempts"][-1]
    assert last["diagnostic_subject"] == "repair_input"
    assert last["intent"] == original.model_dump(mode="json")
    assert last["result_intent_checksum"] is None
    assert last["diagnostics"]["patch"]["failed_operation_index"] == 1


@pytest.mark.asyncio
async def test_successful_patch_keeps_existing_budget_without_retaining_failed_copy(tmp_path, monkeypatch):
    repair = {"operations": [{"op": "connect_data", "source_ref": "lookup", "source_port": "result", "target_ref": "write", "target_port": "records"}]}
    result, proposal, _, calls, _ = await generate(tmp_path, monkeypatch, repair=json.dumps(repair))
    assert result.validation["valid"] and len(calls) == 3
    assert PRIVATE_KEY not in proposal.payload


def capture_graph(graph):
    from server.meta_agent.failed_artifacts import FailedGenerationCapture
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.enter("intent_parse")
    diagnostics.enter("authorization")
    diagnostics.messages(["合成失败"], node_index=0)
    capture = FailedGenerationCapture()
    capture.record(graph, diagnostics)
    return capture


@pytest.mark.parametrize("attack", ["secret", "hidden_reasoning", "unknown_config", "size", "nonfinite", "depth"])
def test_unsafe_or_unbounded_intent_is_withheld_without_raw_output(attack):
    graph = intent("update")
    if attack == "secret":
        graph.nodes[-1].config["role_prompt"] = "sk-" + "A" * 30
    elif attack == "hidden_reasoning":
        graph.nodes[1].config["values"] = {"reasoning_content": "PRIVATE_REASONING_CANARY"}
    elif attack == "unknown_config":
        graph.nodes[-1].config["raw_completion"] = "PRIVATE_REASONING_CANARY"
    elif attack == "size":
        graph.nodes[-1].config["role_prompt"] = "A" * (1024 * 1024)
    elif attack == "nonfinite":
        graph.nodes[1].config["values"] = {"score": float("nan")}
    else:
        deep = {}
        for _ in range(40):
            deep = {"next": deep}
        graph.nodes[1].config["values"] = deep
    artifact = capture_graph(graph).build({})
    attempt = artifact["attempts"][0]
    assert attempt["intent"] is None and attempt["intent_checksum"] is None
    expected = {
        "secret": "sensitive_content", "hidden_reasoning": "sensitive_content",
        "unknown_config": "unsupported_config", "size": "size_limit",
        "nonfinite": "non_json_value", "depth": "depth_limit",
    }
    assert attempt["retention_status"] == expected[attack]
    assert "PRIVATE_REASONING_CANARY" not in json.dumps(artifact)
    assert "sk-" + "A" * 30 not in json.dumps(artifact)
    assert len(json.dumps(artifact)) < 10000


def test_known_invalid_config_remains_repairable_and_capture_is_detached():
    graph = intent("update")
    graph.nodes[0].config["limit"] = "not-an-integer"
    capture = capture_graph(graph)
    saved = capture.build({})
    assert saved["attempts"][0]["intent"]["nodes"][0]["config"]["limit"] == "not-an-integer"
    graph.name = "不应改变捕获结果"
    assert capture.build({}) == saved
    assert capture_graph(intent()).build({}) == capture_graph(intent()).build({})


def test_capture_cannot_be_used_as_an_extra_generation_loop():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    capture = capture_graph(intent())
    capture.record(None, GenerationDiagnostics("graph_intent_v3"))
    with pytest.raises(ValueError, match="最多"):
        capture.record(None, GenerationDiagnostics("graph_intent_v3"))


def test_store_rejects_injected_artifact_and_wrong_source(tmp_path):
    store = AuthoringProposalStore(tmp_path)
    with pytest.raises(AuthoringProposalValidationError, match="服务端"):
        store.create(kind="xpert_create", title="注入", payload={"name": "注入", PRIVATE_KEY: {}}, source_type="meta_planner", source_id="offline")
    with pytest.raises(AuthoringProposalValidationError, match="仅用于"):
        store.create(kind="xpert_create", title="伪造来源", payload={"name": "伪造来源"}, source_type="workflow_agent", source_id="offline", meta_planner_artifact={})
    assert store.list() == []


def test_store_save_failure_leaves_neither_memory_nor_disk_proposal(tmp_path, monkeypatch):
    store = AuthoringProposalStore(tmp_path)

    def failure():
        raise OSError("合成持久化失败")

    monkeypatch.setattr(store, "_save_unlocked", failure)
    with pytest.raises(OSError, match="合成"):
        store.create(kind="xpert_create", title="失败", payload={"name": "失败"}, source_type="meta_planner", source_id="offline", meta_planner_artifact=capture_graph(intent()).build({}))
    assert store.list() == []
    assert not store.snapshot_path.exists()


@pytest.mark.asyncio
async def test_report_authorization_drift_cannot_relabel_an_old_artifact(tmp_path, monkeypatch):
    _, proposal, _, _, _ = await generate(tmp_path, monkeypatch)
    from server.meta_agent.failed_artifacts import FailedArtifactUnavailable, load_failed_generation_artifact

    proposal.payload["meta_planner_report"]["authorized_scope"]["data_table_ids"] = ["another-table"]
    with pytest.raises(FailedArtifactUnavailable, match="stale"):
        load_failed_generation_artifact(proposal)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("binding", []), ("attempts", []), ("attempts", {})])
async def test_malformed_retention_envelope_is_not_a_loadable_authoring_graph(tmp_path, monkeypatch, field, value):
    _, proposal, _, _, _ = await generate(tmp_path, monkeypatch)
    from server.meta_agent.failed_artifacts import FailedArtifactUnavailable, load_failed_generation_artifact

    proposal.payload[PRIVATE_KEY][field] = value
    with pytest.raises(FailedArtifactUnavailable, match="shape_invalid"):
        load_failed_generation_artifact(proposal)


def test_capture_total_storage_limit_drops_bodies_not_diagnostics():
    from server.meta_agent.failed_artifacts import failed_artifact_summary
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics
    from server.meta_agent.graph_patch import GRAPH_PATCH_MAX_REQUEST_BYTES

    graph = intent("update")
    graph.nodes[-1].config["role_prompt"] = "A" * (1024 * 1024 - 30000)
    capture = capture_graph(graph)
    assert capture.attempts[0]["intent"] is not None
    diagnostics = GenerationDiagnostics("graph_patch_v1")
    diagnostics.enter("patch_apply")
    capture.record(graph, diagnostics, repair_input=True)
    # Force the same bounded branch without allocating multi-megabyte diagnostics.
    import server.meta_agent.failed_artifacts as module
    from unittest.mock import patch

    with patch.object(module, "GRAPH_PATCH_MAX_REQUEST_BYTES", 5000):
        artifact = capture.build({})
    assert all(item["intent"] is None for item in artifact["attempts"])
    assert all(item["retention_status"] == "storage_limit" for item in artifact["attempts"])
    assert artifact["attempts"][0]["diagnostics"]["failed_phase"] == "authorization"
    assert failed_artifact_summary(artifact)["recoverable"] is False
    assert len(json.dumps(artifact).encode()) < GRAPH_PATCH_MAX_REQUEST_BYTES
