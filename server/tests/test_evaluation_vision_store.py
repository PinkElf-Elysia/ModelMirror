import copy
from pathlib import Path

import pytest

from server.evaluations import api as evaluation_api
from server.evaluations.api import _parse_import, _sanitize_run_detail
from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
from server.evaluations.vision_fixtures import EvaluationVisionFixtureService
from server.evaluations.vision_preflight import inspect_vision_node, validate_vision_dataset
from server.file_assets.service import FileAssetServiceError
from server.meta_agent.meta_planner_v2 import compile_xpert_candidate
from server.tests.test_evaluation_vision_fixtures import _file_assets, _upload
from server.tests.test_meta_planner_vision_graph import binding_fixture, vision_intent, vision_request, vision_snapshot, _plan
from server.tests.test_xpert_evaluations import _target
from server.workflow_native.node_contracts import node_policy_service
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xpert_runtime.workflow_vision import resolve_workflow_vision_asset, WorkflowVisionError


def _setup(tmp_path: Path):
    assets = _file_assets(tmp_path)
    fixtures = EvaluationVisionFixtureService(assets)
    store = XpertEvaluationStore(tmp_path / "evaluations", vision_fixtures=fixtures)
    dataset = store.create_dataset("附件回归")
    uploaded = _upload(assets, dataset["dataset_id"])
    dataset = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{
        "case_id": "image", "message": "读取附件中的合成状态。",
        "attachment": {"asset_id": uploaded.asset_id},
        "expected": {"contains": ["已完成"]},
    }])
    version = store.publish_dataset(dataset["dataset_id"], revision=dataset["revision"])
    return store, fixtures, assets, version


def _run(store, version):
    return store.create_run(dataset_version=version, cases=version["cases"], baseline=None, candidates=[_target()], config={"budget": {"repetitions": 1}}, warnings=[])


def test_published_attachment_survives_draft_unlink_and_restart(tmp_path):
    store, fixtures, assets, version = _setup(tmp_path)
    dataset = store.require_dataset(version["dataset_id"])
    store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{"case_id": "image", "message": "仅文本草稿"}], replace=True)
    fixtures.release_draft(dataset["dataset_id"])
    restored = XpertEvaluationStore(store.storage_dir, vision_fixtures=EvaluationVisionFixtureService(assets))
    fixed = restored.get_dataset_version(dataset["dataset_id"], 1)
    assert fixed == version
    run = _run(restored, fixed)
    manifest = restored.vision_fixture_for_item(run["run_id"], target_id="candidate", case_id="image")
    assert fixtures.resolve_run_asset(run["run_id"], manifest).sha256 == fixed["cases"][0]["attachment"]["sha256"]
    assert "_vision_fixtures" not in run
    assert "_vision_fixtures" not in _sanitize_run_detail(restored.require_run(run["run_id"]))


def test_untrusted_attachment_claims_and_case_replacement_are_rejected(tmp_path):
    store, fixtures, assets, version = _setup(tmp_path)
    original = version["cases"][0]["attachment"]
    forged = copy.deepcopy(version)
    forged["cases"][0]["attachment"]["sha256"] = "0" * 64
    with pytest.raises(EvaluationStateError, match="一致"):
        _run(store, forged)
    other = store.create_dataset("其他数据集")
    with pytest.raises(FileAssetServiceError):
        store.put_cases(other["dataset_id"], revision=other["revision"], cases=[{"message": "不允许借用", "attachment": {"asset_id": original["asset_id"]}}])
    with pytest.raises(ValueError):
        store.normalize_case({"message": "拒绝路径", "attachment": {"asset_id": "valid", "path": "C:/private/image.png"}})
    with pytest.raises(ValueError, match="URL"):
        _parse_import("cases.csv", b"message,attachment_url\ntest,https://invalid/image.png")


def test_publish_failure_rolls_back_only_new_version_binding(tmp_path, monkeypatch):
    store, fixtures, assets, version = _setup(tmp_path)
    dataset = store.require_dataset(version["dataset_id"])
    def fail():
        raise OSError("simulated atomic write failure")
    monkeypatch.setattr(store, "_save_unlocked", fail)
    with pytest.raises(OSError):
        store.publish_dataset(dataset["dataset_id"], revision=dataset["revision"])
    assert len(store.require_dataset(dataset["dataset_id"])["versions"]) == 1
    assert fixtures.inspect_dataset_version_asset(dataset["dataset_id"], 1, version["cases"][0]["attachment"])
    assert assets.list_evaluation_asset_ids(scope_id=f"evaluation-version:{dataset['dataset_id']}:2") == ()


def test_run_failure_releases_new_run_binding(tmp_path, monkeypatch):
    store, fixtures, assets, version = _setup(tmp_path)
    run_ids = []
    original_pin = fixtures.pin_run
    def pin(run_id, *args):
        run_ids.append(run_id)
        return original_pin(run_id, *args)
    monkeypatch.setattr(fixtures, "pin_run", pin)
    monkeypatch.setattr(store, "_save_unlocked", lambda: (_ for _ in ()).throw(OSError("failure")))
    with pytest.raises(OSError):
        _run(store, version)
    assert assets.list_evaluation_asset_ids(scope_id=f"evaluation-run:{run_ids[0]}") == ()
    assert store.list_runs() == []


def test_dispatched_uncertain_item_never_requeues_but_undispatched_can(tmp_path):
    store, fixtures, _, version = _setup(tmp_path)
    run = _run(store, version)
    store.claim_next_run()
    item = store.claim_items(run["run_id"], 1)[0]
    store.mark_vision_dispatch(run["run_id"], item["item_id"], node_ref="vision", page_number=1)
    with pytest.raises(EvaluationStateError, match="重复"):
        store.mark_vision_dispatch(run["run_id"], item["item_id"], node_ref="vision", page_number=1)
    restarted = XpertEvaluationStore(store.storage_dir, vision_fixtures=fixtures)
    assert restarted.recover_runs() == 1
    restored = restarted.require_run(run["run_id"])["items"][0]
    assert restored["status"] == "failed"
    assert restored["error_code"] == "EVALUATION_VISION_DISPATCH_UNCERTAIN"
    assert restored["usage"]["vision_uncertain_dispatches"] == 1
    assert restored["usage"]["vision_token_usage_verified"] is False
    assert restarted.claim_items(run["run_id"], 1) == []
    assert "已派发" in restored["error"]
    second = _run(restarted, version)
    restarted.claim_next_run()
    restarted.claim_next_run()
    restarted.claim_items(second["run_id"], 1)
    again = XpertEvaluationStore(store.storage_dir, vision_fixtures=fixtures)
    again.recover_runs()
    assert again.require_run(second["run_id"])["items"][0]["status"] == "pending"


def test_recovery_retains_known_usage_and_only_safe_receipts(tmp_path):
    store, fixtures, _, version = _setup(tmp_path)
    run = _run(store, version)
    store.claim_next_run()
    item = store.claim_items(run["run_id"], 1)[0]
    store.mark_vision_dispatch(run["run_id"], item["item_id"], node_ref="vision", page_number=1)
    assert store.record_vision_receipt(run["run_id"], item["item_id"], node_ref="vision", receipt={
        "status": "passed", "prompt": "DO-NOT-PERSIST", "path": "C:/private",
        "calls": [{"dispatched": True, "status": "passed", "total_tokens": 17, "api_key": "DO-NOT-PERSIST"}],
    }) == 17
    store.mark_vision_dispatch(run["run_id"], item["item_id"], node_ref="vision", page_number=2)
    restored = XpertEvaluationStore(store.storage_dir, vision_fixtures=fixtures)
    restored.recover_runs()
    result = restored.require_run(run["run_id"])["items"][0]
    assert result["usage"]["vision_actual_tokens"] == 17
    assert result["usage"]["vision_model_calls"] == 1
    assert result["usage"]["vision_uncertain_dispatches"] == 1
    assert result["usage"]["vision_token_estimate"] is False
    assert "DO-NOT-PERSIST" not in restored.path.read_text(encoding="utf-8")


def test_store_vision_assertions_are_bounded_and_unique():
    with pytest.raises(EvaluationStateError, match="20"):
        XpertEvaluationStore.normalize_case({"message": "合成", "vision": [{"node_ref": f"v{i}"} for i in range(21)]})
    with pytest.raises(EvaluationStateError, match="重复"):
        XpertEvaluationStore.normalize_case({"message": "合成", "vision": [{"node_ref": "v"}, {"node_ref": "v"}]})


def test_runtime_retains_evaluation_identity_and_checks_item(tmp_path, monkeypatch):
    store, _, assets, version = _setup(tmp_path)
    run = _run(store, version)
    store.claim_next_run()
    item = store.claim_items(run["run_id"], 1)[0]
    monkeypatch.setattr(evaluation_api, "_store", store)
    metadata = {"evaluation_run_id": run["run_id"], "evaluation_target_id": "candidate", "evaluation_case_id": "image", "evaluation_item_id": item["item_id"]}
    asset_id = version["cases"][0]["attachment"]["asset_id"]
    result = resolve_workflow_vision_asset(asset_id=asset_id, workflow_id="not-a-session", runtime_run_type="xpert_evaluation", runtime_metadata=metadata, file_asset_service=assets, xpert_context_store=None)
    assert result.asset_id == asset_id
    for invalid in ({**metadata, "evaluation_case_id": "other"}, {**metadata, "evaluation_item_id": "other"}, {}):
        with pytest.raises(WorkflowVisionError, match="固定评测"):
            resolve_workflow_vision_asset(asset_id=asset_id, workflow_id="not-a-session", runtime_run_type="xpert_evaluation", runtime_metadata=invalid, file_asset_service=assets, xpert_context_store=None)


def test_vision_preflight_requires_dataset_and_managed_binding(tmp_path):
    store, fixtures, _, version = _setup(tmp_path)
    candidate = compile_xpert_candidate(request=vision_request(), plan=_plan(), blueprint=vision_intent(), snapshot=vision_snapshot(), target=None)
    workflow = NativeWorkflowDefinition.model_validate(candidate["draft"]["workflow"])
    node = next(node for node in workflow.nodes if node.type == "vision_understanding")
    assert not node_policy_service.decision("vision_understanding", "evaluation").allowed
    assert node_policy_service.decision("vision_understanding", "evaluation", node_data=node.data).conditional
    assert not node_policy_service.decision("vision_understanding", "app", node_data=node.data).allowed
    resource = inspect_vision_node(node, nested=False, binding_resolver=lambda *_: binding_fixture())
    target = {**_target(), "resources": {"vision_models": [resource]}}
    with pytest.raises(EvaluationStateError, match="固定附件"):
        validate_vision_dataset([target], [], dataset_version=None, fixtures=fixtures)
    warnings = validate_vision_dataset([target], version["cases"], dataset_version=version, fixtures=fixtures)
    assert any("断言" in warning for warning in warnings)
    with pytest.raises(EvaluationStateError, match="漂移"):
        inspect_vision_node(node, nested=False, binding_resolver=lambda *_: binding_fixture(connection_id="changed"))
    with pytest.raises(EvaluationStateError, match="委托"):
        inspect_vision_node(node, nested=True, binding_resolver=lambda *_: binding_fixture())
    assert assets_unchanged(store, version)


@pytest.mark.parametrize("node_ref", [None, "", "../private", 5, "a" * 65])
def test_vision_preflight_rejects_unidentifiable_node_before_binding(node_ref):
    candidate = compile_xpert_candidate(request=vision_request(), plan=_plan(), blueprint=vision_intent(), snapshot=vision_snapshot(), target=None)
    workflow = NativeWorkflowDefinition.model_validate(candidate["draft"]["workflow"])
    node = next(node for node in workflow.nodes if node.type == "vision_understanding")
    node.data["plannerRef"] = node_ref

    def forbidden_resolver(*_):
        pytest.fail("Invalid node identity must fail before model binding resolution")

    with pytest.raises(EvaluationStateError, match="Planner ref"):
        inspect_vision_node(node, nested=False, binding_resolver=forbidden_resolver)


def assets_unchanged(store, version):
    return store.list_runs() == [] and store.get_dataset_version(version["dataset_id"], 1) == version


@pytest.mark.asyncio
async def test_dataset_mutation_api_keeps_publishable_summary(tmp_path, monkeypatch):
    from server.evaluations.models import DatasetCasesRequest, DatasetUpdateRequest

    store, _, _, version = _setup(tmp_path)
    monkeypatch.setattr(evaluation_api, "_store", store)
    dataset = store.require_dataset(version["dataset_id"])
    saved = await evaluation_api.put_dataset_cases(dataset["dataset_id"], DatasetCasesRequest(
        revision=dataset["revision"], replace=True,
        cases=[{"case_id": "image", "message": "读取附件", "attachment": {
            "asset_id": version["cases"][0]["attachment"]["asset_id"],
        }}],
    ))
    assert saved["case_count"] == 1
    assert saved["version_count"] == 1
    assert "versions" not in saved
    assert saved == await evaluation_api.get_dataset(dataset["dataset_id"])
    renamed = await evaluation_api.update_dataset(dataset["dataset_id"], DatasetUpdateRequest(
        revision=saved["revision"], name="附件摘要回归",
    ))
    assert renamed["case_count"] == 1
    assert renamed == await evaluation_api.get_dataset(dataset["dataset_id"])
