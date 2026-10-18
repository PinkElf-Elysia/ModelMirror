"""Local Store/policy evidence only. Admission neither dispatches nor publishes."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from server.tests.test_workflow_subworkflows import callable_workflow, model_callable_workflow, manual_caller
from server.tests.test_workflow_r8b_entry_evidence import invoke_data, publish
from server.tests.test_workflow_deployments import secure_http_workflow
from server.workflow_deployments import WorkflowDeploymentStore
from server.workflow_native.node_contracts import (
    NodePolicyService, build_builtin_node_contract_registry, canonical_checksum,
)
from server.workflow_native.readonly_subworkflow import (
    PublishedSubworkflowGrant, ReadonlySubworkflowAdmission, ReadonlySubworkflowError,
)


@pytest.fixture
def target(tmp_path):
    store = WorkflowDeploymentStore(tmp_path / "deployments")
    release = publish(store, callable_workflow(title="只读合成目标"))
    grants = (PublishedSubworkflowGrant(project_id=release.project_id, version=release.version),)
    return store, release, grants


def resolve(service, release, grants):
    return service.resolve(release.project_id, release.version, grants=grants)


def replace_body(store, release, node):
    # Deliberately mutate the in-memory published snapshot for fault injection.
    # Updating the checksum lets policy tests reach the unsafe configuration;
    # the independent tamper test does not update it.
    release.workflow["nodes"].insert(1, deepcopy(node))
    release.definition_checksum = canonical_checksum(release.workflow)


def test_empty_grant_denies_before_store_read(target, monkeypatch):
    store, release, _ = target
    def forbidden(*args):
        pytest.fail("未授权目标不得读取 Store")
    monkeypatch.setattr(store, "snapshot_callable_version", forbidden)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store), release, ())
    assert exc.value.code == "SUBWORKFLOW_NOT_AUTHORIZED"


@pytest.mark.parametrize("version", [True, False, "1", "latest", 0, -1, 1.5])
def test_version_is_strict(version, target):
    store, release, grants = target
    with pytest.raises(ValidationError):
        PublishedSubworkflowGrant(project_id=release.project_id, version=version)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        ReadonlySubworkflowAdmission(store).resolve(release.project_id, version, grants=grants)
    assert exc.value.code == "SUBWORKFLOW_TARGET_INVALID"


def test_grant_cannot_inject_effect_or_version_policy(target):
    _, release, _ = target
    with pytest.raises(ValidationError):
        PublishedSubworkflowGrant(project_id=release.project_id, version=1, readonly=True)


def test_resolution_is_stable_detached_and_has_no_store_writes(target):
    store, release, grants = target
    before = store.snapshot_path.read_bytes()
    service = ReadonlySubworkflowAdmission(store)
    first = resolve(service, release, grants)
    detached = store.snapshot_callable_version(release.project_id, release.version)
    detached.workflow["title"] = "不得写回"
    assert resolve(service, release, grants) == first
    assert service.revalidate(first, grants=grants).checksum == first.checksum
    assert store.snapshot_path.read_bytes() == before
    assert first.model_ids == () and first.requires_root_budget is True
    assert len(first.checksum) == 64
    safe = first.model_dump_json()
    assert "internal" not in safe and "workflow" not in json.loads(safe)


def test_target_version_does_not_follow_new_publish(target):
    store, release, grants = target
    service = ReadonlySubworkflowAdmission(store)
    first = resolve(service, release, grants)
    newer = store.publish(release.project_id)
    assert newer.version == 2
    assert service.revalidate(first, grants=grants) == first
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(service, newer, grants)
    assert exc.value.code == "SUBWORKFLOW_NOT_AUTHORIZED"
    store.activate(newer.project_id, newer.version, webhooks_enabled=False, subworkflows_enabled=True)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        service.revalidate(first, grants=grants)
    assert exc.value.code == "SUBWORKFLOW_TARGET_UNAVAILABLE"


def test_deactivation_and_definition_tamper_fail_closed(target):
    store, release, grants = target
    service = ReadonlySubworkflowAdmission(store)
    first = resolve(service, release, grants)
    release.workflow["title"] = "被替换"
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(service, release, grants)
    assert exc.value.code == "SUBWORKFLOW_TARGET_UNAVAILABLE"
    release.workflow["title"] = "只读合成目标"
    store.deactivate(release.project_id, release.version)
    with pytest.raises(ReadonlySubworkflowError):
        service.revalidate(first, grants=grants)


def test_non_callable_target_rejected(target):
    store, release, _ = target
    manual = publish(store, manual_caller(release.project_id, release.version))
    grants = (PublishedSubworkflowGrant(project_id=manual.project_id, version=manual.version),)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store), manual, grants)
    assert exc.value.code == "SUBWORKFLOW_TARGET_UNAVAILABLE"


@pytest.mark.parametrize("kind", [
    "http_request", "invoke_workflow", "iteration", "external_xpert", "toolset_resource",
    "plugin_resource", "runtime_middleware", "vision_understanding", "data_table_insert",
    "agent_task", "unregistered_extension",
])
def test_disconnected_unsafe_nodes_cannot_hide(target, kind):
    store, release, grants = target
    replace_body(store, release, {"id": "hidden", "type": kind, "data": {"kind": kind}})
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store), release, grants)
    assert exc.value.node_id == "hidden"


def test_actual_nested_post_target_is_rejected_without_http(target):
    store, _, _ = target
    graph = callable_workflow()
    http = deepcopy(secure_http_workflow()["nodes"][1])
    http["data"].update(method="POST", url="https://api.example.test/items")
    graph["nodes"].insert(1, http)
    graph["nodes"][-1]["data"]["outputVariable"] = "http_response"
    graph["edges"] = [{"id": "one", "source": "entry", "target": "http"},
                      {"id": "two", "source": "http", "target": "output"}]
    leaf = publish(store, graph, http_requests_enabled=True)
    wrapper = publish(store, callable_workflow(invoke=invoke_data(leaf)))
    for release in (leaf, wrapper):
        grants = (PublishedSubworkflowGrant(project_id=release.project_id, version=release.version),)
        with pytest.raises(ReadonlySubworkflowError) as exc:
            resolve(ReadonlySubworkflowAdmission(store), release, grants)
        assert exc.value.code == "SUBWORKFLOW_CAPABILITY_FORBIDDEN"


@pytest.mark.parametrize("extra", [
    {"toolMode": "mcp_tools"}, {"memoryWriteEnabled": True}, {"knowledgeWriteEnabled": True},
    {"enableFileUnderstanding": True}, {"promptProfileId": "profile"},
    {"runtimeMiddlewareConfig": {"id": "sandbox_shell"}}, {"agentStrategy": "unknown"},
])
def test_model_hidden_extension_rejected(extra):
    policy = NodePolicyService(build_builtin_node_contract_registry())
    data = {"modelId": "offline/model", "rolePrompt": "合成", "taskInput": "{{message}}",
            "outputVariable": "answer", "toolMode": "none", **extra}
    decision = policy.decision("workflow_agent", "readonly_subworkflow", node_data=data)
    assert not decision.allowed and decision.code == "SUBWORKFLOW_MODEL_EXTENSION_FORBIDDEN"


def test_model_snapshot_contains_no_prompt_and_does_not_call_model(target):
    store, _, _ = target
    graph = model_callable_workflow()
    graph["nodes"][1]["data"]["prompt"] = "PRIVATE-PROMPT-SENTINEL {{message}}"
    release = publish(store, graph)
    grants = (PublishedSubworkflowGrant(project_id=release.project_id, version=release.version),)
    result = resolve(ReadonlySubworkflowAdmission(store), release, grants)
    assert result.model_ids == ("test/model",)
    assert "PRIVATE-PROMPT-SENTINEL" not in result.model_dump_json()


@pytest.mark.parametrize("retry", ["transient", "unknown", {"retry": True}])
def test_config_wait_cannot_hide_behind_read_effect(retry):
    data = {"kind": "data_table_query", "tableId": "table_test", "outputVariable": "rows",
            "versionPolicy": "pinned", "pinnedSchemaVersion": 1, "retryMode": retry}
    decision = NodePolicyService(build_builtin_node_contract_registry()).decision(
        "data_table_query", "readonly_subworkflow", node_data=data)
    assert not decision.allowed


def query_target(store):
    graph = callable_workflow()
    graph["nodes"].insert(1, {"id": "query", "type": "data_table_query", "data": {
        "kind": "data_table_query", "tableId": "table_test", "outputVariable": "rows",
        "versionPolicy": "pinned", "pinnedSchemaVersion": 1, "limit": 20,
    }})
    graph["nodes"][-1]["data"]["outputVariable"] = "rows"
    graph["edges"] = [{"id": "one", "source": "entry", "target": "query"},
                      {"id": "two", "source": "query", "target": "output"}]
    release = publish(store, graph)
    return release, (PublishedSubworkflowGrant(project_id=release.project_id, version=release.version),)


def test_resource_authority_required_and_revalidated(target):
    store, _, _ = target
    release, grants = query_target(store)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store), release, grants)
    assert exc.value.code == "SUBWORKFLOW_RESOURCE_AUTHORITY_REQUIRED"
    calls = []
    fingerprint = "a" * 64
    def authority(requirement, config):
        calls.append(requirement)
        assert requirement.pinned_version == 1
        assert config["versionPolicy"] == "pinned"
        config["tableId"] = "cannot_mutate_source"
        return fingerprint
    service = ReadonlySubworkflowAdmission(store, resource_validator=authority)
    before = store.snapshot_path.read_bytes()
    first = resolve(service, release, grants)
    assert first.resources[0].resource_id == "table_test" and len(calls) == 1
    assert service.revalidate(first, grants=grants) == first and len(calls) == 2
    fingerprint = "b" * 64
    with pytest.raises(ReadonlySubworkflowError) as exc:
        service.revalidate(first, grants=grants)
    assert exc.value.code == "SUBWORKFLOW_ADMISSION_CHANGED"
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("result", [None, True, {}, "secret-not-a-checksum"])
def test_resource_proof_must_be_fingerprint(target, result):
    store, _, _ = target
    release, grants = query_target(store)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store, resource_validator=lambda *_: result), release, grants)
    assert exc.value.code == "SUBWORKFLOW_RESOURCE_PROOF_INVALID"
    assert "secret" not in str(exc.value)


def test_resource_error_is_sanitized_and_target_race_rejected(target):
    store, _, _ = target
    release, grants = query_target(store)
    def failed(*args):
        raise RuntimeError("PRIVATE-RESOURCE-CONTENT")
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store, resource_validator=failed), release, grants)
    assert exc.value.code == "SUBWORKFLOW_RESOURCE_REJECTED"
    assert "PRIVATE-RESOURCE-CONTENT" not in str(exc.value)
    def race(*args):
        store.deactivate(release.project_id, release.version)
        return "a" * 64
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(ReadonlySubworkflowAdmission(store, resource_validator=race), release, grants)
    assert exc.value.code == "SUBWORKFLOW_TARGET_UNAVAILABLE"


def test_contract_effect_drift_and_revoked_grant_rejected(target):
    store, release, grants = target
    registry = build_builtin_node_contract_registry()
    service = ReadonlySubworkflowAdmission(store, policy=NodePolicyService(registry))
    first = resolve(service, release, grants)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        service.revalidate(first)
    assert exc.value.code == "SUBWORKFLOW_NOT_AUTHORIZED"
    contract = registry.require("output")
    # In-memory registry mutation models an upstream runtime effect change.
    contract.execution.__dict__["side_effect"] = "write"
    with pytest.raises(ReadonlySubworkflowError) as exc:
        service.revalidate(first, grants=grants)
    assert exc.value.code == "SUBWORKFLOW_EFFECT_FORBIDDEN"


def test_contract_drift_inside_resource_check_rejected(target):
    store, _, _ = target
    release, grants = query_target(store)
    registry = build_builtin_node_contract_registry()
    def authority(*args):
        registry.require("output").execution.__dict__["side_effect"] = "write"
        return "a" * 64
    service = ReadonlySubworkflowAdmission(store, policy=NodePolicyService(registry), resource_validator=authority)
    with pytest.raises(ReadonlySubworkflowError) as exc:
        resolve(service, release, grants)
    assert exc.value.code == "SUBWORKFLOW_ADMISSION_CHANGED"


def test_cross_project_snapshot_identity_rejected(target):
    store, release, grants = target
    release.project_id = "wf_" + "0" * 32
    with pytest.raises(ReadonlySubworkflowError) as exc:
        ReadonlySubworkflowAdmission(store).resolve(grants[0].project_id, grants[0].version, grants=grants)
    assert exc.value.code == "SUBWORKFLOW_TARGET_UNAVAILABLE"


def test_new_model_config_property_is_not_automatically_authorized():
    registry = build_builtin_node_contract_registry()
    registry.require("workflow_agent").config_schema["properties"]["memoryWriteEnabled"] = {"type": "boolean"}
    data = {"modelId": "offline/model", "rolePrompt": "合成", "taskInput": "{{message}}",
            "outputVariable": "answer", "memoryWriteEnabled": True}
    decision = NodePolicyService(registry).decision("workflow_agent", "readonly_subworkflow", node_data=data)
    assert not decision.allowed and decision.code == "SUBWORKFLOW_MODEL_EXTENSION_FORBIDDEN"


@pytest.mark.parametrize("kind,data", [
    ("json_serialize", {"contractVersion": 2, "inputVariable": "message", "outputVariable": "encoded", "format": "compact"}),
    ("json_deserialize", {"contractVersion": 2, "inputVariable": "message", "outputVariable": "parsed", "expectedSchema": {"type": "object"}}),
    ("workflow_agent", {"modelId": "offline/model", "rolePrompt": "合成", "taskInput": "{{message}}", "outputVariable": "answer", "toolMode": "none"}),
])
def test_safe_configurations_are_not_blocked(kind, data):
    policy = NodePolicyService(build_builtin_node_contract_registry())
    assert policy.decision(kind, "readonly_subworkflow", node_data=data).allowed


@pytest.mark.parametrize("version", [None, True, "2", 1])
def test_old_json_is_not_silently_upgraded(version):
    data = {"inputVariable": "message", "outputVariable": "parsed"}
    if version is not None:
        data["contractVersion"] = version
    decision = NodePolicyService(build_builtin_node_contract_registry()).decision(
        "json_deserialize", "readonly_subworkflow", node_data=data)
    assert not decision.allowed


@pytest.mark.parametrize("version", [None, True, "1", 0])
def test_query_cannot_use_unfixed_schema(version):
    data = {"tableId": "table_test", "outputVariable": "rows", "versionPolicy": "pinned", "pinnedSchemaVersion": version}
    decision = NodePolicyService(build_builtin_node_contract_registry()).decision(
        "data_table_query", "readonly_subworkflow", node_data=data)
    assert not decision.allowed


def test_forged_snapshot_does_not_authorize_target(target):
    store, release, grants = target
    service = ReadonlySubworkflowAdmission(store)
    first = resolve(service, release, grants)
    forged = first.model_copy(update={"definition_checksum": "0" * 64})
    with pytest.raises(ReadonlySubworkflowError) as exc:
        service.revalidate(forged, grants=grants)
    assert exc.value.code == "SUBWORKFLOW_ADMISSION_CHANGED"
