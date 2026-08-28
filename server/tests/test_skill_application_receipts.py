from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.skills.application_receipts import (
    APPLICATION_RECEIPT_VERSION,
    APPLICATION_RECEIPT_V1_VERSION,
    SkillApplicationObserver,
    SkillApplicationReceiptError,
    SkillApplicationReceiptStorageError,
    SkillApplicationReceiptStore,
    SkillApplicationScope,
    SkillHookApplicationEvidenceV2,
    build_application_contract,
)
from server.skills.creator_evaluation import SkillEvaluationStore, SkillEvaluationValidationError
from server.skills.package_validation import compute_package_digest
from server.xpert_runtime.sandbox_store import SandboxWorkspace, SandboxWorkspaceStore
from server.xpert_runtime.sandbox_toolset import SandboxToolsetProvider
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contract(*, policy: str = "require_read", required=()):
    return build_application_contract(
        skill_id="incident-review",
        source_kind="git",
        version_id="skillversion_incident_v1",
        content_digest=_digest("package-v1"),
        trust_fingerprint=_digest("trust-v1"),
        policy=policy,
        required_resource_paths=required,
    )


def _scope() -> SkillApplicationScope:
    return SkillApplicationScope(
        run_id="run_receipt_1",
        task_id="task_receipt_1",
        node_id="workflow-agent",
        runtime_kind="workflow",
    )


def test_contract_and_receipt_distinguish_selection_from_application(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    contract = _contract()

    selected = store.record_selection(contract, _scope())
    assert selected is not None
    assert selected.version == APPLICATION_RECEIPT_VERSION
    assert selected.application_status == "selected"
    assert selected.compliance_status == "incomplete"
    assert selected.methods == ()

    read_digest = _digest("full skill markdown")
    applied = store.observe(
        contract,
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": read_digest},
        expected_resource_digests={"SKILL.md": read_digest},
        tool_name="skill_read",
    )
    assert applied is not None
    assert applied.application_status == "applied"
    assert applied.compliance_status == "verified"
    assert applied.methods == ("skill_read",)
    assert applied.resource_digests == {"SKILL.md": read_digest}

    replay = store.observe(
        contract,
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": read_digest},
        expected_resource_digests={"SKILL.md": read_digest},
        tool_name="skill_read",
    )
    assert replay is not None
    assert replay.revision == applied.revision

    second_node = store.observe(
        contract,
        SkillApplicationScope(
            run_id="run_receipt_1",
            task_id="task_receipt_1",
            node_id="second-agent",
            runtime_kind="workflow",
        ),
        method="skill_read",
    )
    assert second_node is not None
    assert second_node.receipt_id == applied.receipt_id
    assert second_node.node_ids == ("second-agent", "workflow-agent")


def test_v2_persists_bounded_hook_evidence_and_requires_verified_execution(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    contract = _contract(policy="advisory")
    evidence = SkillHookApplicationEvidenceV2(
        hook_id="check-release-name",
        hook_event="pre_tool_use",
        hook_mode="guard",
        manifest_digest=_digest("manifest"),
        script_digest=_digest("script"),
        context_digest=_digest("redacted-context"),
        result_digest=_digest("typed-result"),
        code="release_name_allowed",
        result_type="validation_passed",
        verified=True,
    )

    receipt = store.observe(
        contract,
        _scope(),
        method="hook_execute",
        tool_name="skill_hook_execute",
        hook_evidence=(evidence,),
    )

    assert receipt is not None
    assert receipt.version == APPLICATION_RECEIPT_VERSION
    assert receipt.methods == ("hook_execute",)
    assert receipt.compliance_status == "verified"
    assert receipt.hook_evidence == (evidence,)
    assert store.require_verified_hook_evidence(
        receipt.receipt_id, hook_ids=("check-release-name",)
    ).receipt_id == receipt.receipt_id
    persisted = store.snapshot_path.read_text(encoding="utf-8")
    assert "redacted-context" not in persisted
    assert "typed-result" not in persisted


def test_v1_receipt_migrates_without_fabricating_hook_evidence(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    selected = store.record_selection(_contract(), _scope())
    assert selected is not None
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["version"] = APPLICATION_RECEIPT_V1_VERSION
    payload["receipts"][0]["version"] = APPLICATION_RECEIPT_V1_VERSION
    payload["receipts"][0].pop("hook_evidence", None)
    store.snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    migrated = SkillApplicationReceiptStore(tmp_path).require(selected.receipt_id)

    assert migrated.version == APPLICATION_RECEIPT_VERSION
    assert migrated.hook_evidence == ()
    assert migrated.receipt_id == selected.receipt_id


def test_hook_evidence_rejects_impossible_deny_boundary(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    evidence = SkillHookApplicationEvidenceV2(
        hook_id="late-deny",
        hook_event="post_tool_use",
        hook_mode="guard",
        manifest_digest=_digest("manifest"),
        script_digest=_digest("script"),
        context_digest=_digest("context"),
        result_digest=_digest("result"),
        code="late_deny",
        result_type="denied",
        verified=True,
    )

    with pytest.raises(SkillApplicationReceiptError):
        store.observe(
            _contract(policy="advisory"),
            _scope(),
            method="hook_execute",
            hook_evidence=(evidence,),
        )


def test_unverified_hook_evidence_cannot_satisfy_verified_gate(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    evidence = SkillHookApplicationEvidenceV2(
        hook_id="check-release-name",
        hook_event="pre_tool_use",
        hook_mode="guard",
        manifest_digest=_digest("manifest"),
        script_digest=_digest("script"),
        context_digest=_digest("context"),
        result_digest=_digest("result"),
        code="release_name_allowed",
        result_type="validation_passed",
        verified=False,
    )

    receipt = store.observe(
        _contract(policy="advisory"),
        _scope(),
        method="hook_execute",
        hook_evidence=(evidence,),
    )

    assert receipt is not None
    assert receipt.compliance_status == "unverified"
    with pytest.raises(SkillApplicationReceiptError) as error:
        store.require_verified_hook_evidence(receipt.receipt_id)
    assert error.value.code == "skill_application_receipt_incomplete"


@pytest.mark.parametrize(
    "invalid_evidence",
    [
        SkillHookApplicationEvidenceV2(
            hook_id="check-release-name",
            hook_event="pre_tool_use",
            hook_mode="guard",
            manifest_digest="",
            script_digest=_digest("script"),
            context_digest=_digest("context"),
            result_digest=_digest("result"),
            code="release_name_allowed",
            result_type="validation_passed",
            verified=True,
        ),
        SkillHookApplicationEvidenceV2(
            hook_id="check-release-name",
            hook_event="pre_tool_use",
            hook_mode="guard",
            manifest_digest=_digest("manifest"),
            script_digest=_digest("script"),
            context_digest=_digest("context"),
            result_digest=_digest("result"),
            code="wrong_result_type",
            result_type="annotation",
            verified=True,
        ),
    ],
)
def test_hook_evidence_requires_all_digests_and_matching_mode(
    tmp_path, invalid_evidence
):
    store = SkillApplicationReceiptStore(tmp_path)

    with pytest.raises(SkillApplicationReceiptError) as error:
        store.observe(
            _contract(policy="advisory"),
            _scope(),
            method="hook_execute",
            hook_evidence=(invalid_evidence,),
        )

    assert error.value.code == "skill_application_receipt_invalid"


def test_failed_hook_evidence_cannot_be_recorded_as_success(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    evidence = SkillHookApplicationEvidenceV2(
        hook_id="check-release-name",
        hook_event="pre_tool_use",
        hook_mode="guard",
        manifest_digest=_digest("manifest"),
        script_digest=_digest("script"),
        context_digest=_digest("context"),
        result_digest=_digest("result"),
        code="hook_process_failed",
        result_type="failed",
        verified=True,
    )
    with pytest.raises(SkillApplicationReceiptError):
        store.observe(
            _contract(policy="advisory"),
            _scope(),
            method="hook_execute",
            hook_evidence=(evidence,),
        )

    failed = store.observe(
        _contract(policy="advisory"),
        _scope(),
        method="hook_execute",
        error_code="skill_hook_execution_failed",
        hook_evidence=(replace(evidence, verified=False),),
    )

    assert failed is not None
    assert failed.methods == ()
    assert failed.application_status == "failed"
    assert failed.compliance_status == "unverified"


def test_resource_read_evidence_does_not_forge_skill_read_method(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    contract = _contract(required=("references/guide.md",))
    guide_digest = _digest("bounded guide")

    resource_read = store.observe(
        contract,
        _scope(),
        read_resource_paths=["references/guide.md"],
        resource_digests={"references/guide.md": guide_digest},
        expected_resource_digests={"references/guide.md": guide_digest},
        tool_name="sandbox_read_file",
    )

    assert resource_read is not None
    assert resource_read.methods == ()
    assert resource_read.read_resource_paths == ("references/guide.md",)
    assert resource_read.compliance_status == "incomplete"

    applied = store.observe(
        contract,
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("skill body")},
        expected_resource_digests={"SKILL.md": _digest("skill body")},
        tool_name="skill_read",
    )

    assert applied is not None
    assert applied.methods == ("skill_read",)
    assert applied.read_resource_paths == ("SKILL.md", "references/guide.md")
    assert applied.compliance_status == "verified"


def test_third_party_receipt_without_trust_fingerprint_stays_unverified(tmp_path):
    contract = build_application_contract(
        skill_id="legacy-skill",
        source_kind="git",
        version_id="skillversion_legacy_v1",
        content_digest=_digest("legacy-package"),
    )
    receipt = SkillApplicationReceiptStore(tmp_path).observe(
        contract,
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
    )

    assert receipt is not None
    assert receipt.application_status == "applied"
    assert receipt.compliance_status == "unverified"


def test_contract_rejects_more_resource_paths_than_receipt_can_prove():
    with pytest.raises(SkillApplicationReceiptError) as captured:
        build_application_contract(
            skill_id="large-skill",
            source_kind="workspace_draft",
            version_id="skillversion_large_v1",
            content_digest=_digest("large-package"),
            policy="require_stage",
            required_resource_paths=[f"references/{index}.md" for index in range(501)],
        )

    assert captured.value.code == "skill_application_contract_limit"


def test_stage_policy_requires_read_and_declared_resource_paths(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    contract = _contract(
        policy="require_stage",
        required=("SKILL.md", "references/guide.md"),
    )

    staged = store.observe(
        contract,
        _scope(),
        method="skill_stage",
        resource_paths=["SKILL.md", "references/guide.md"],
        resource_digests={
            "SKILL.md": _digest("skill"),
            "references/guide.md": _digest("guide"),
        },
        expected_resource_digests={
            "SKILL.md": _digest("skill"),
            "references/guide.md": _digest("guide"),
        },
        tool_name="skill_stage",
    )
    assert staged is not None
    assert staged.compliance_status == "incomplete"

    verified = store.observe(
        contract,
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("skill")},
        expected_resource_digests={"SKILL.md": _digest("skill")},
        tool_name="skill_read",
    )
    assert verified is not None
    assert verified.compliance_status == "verified"
    assert verified.methods == ("skill_read", "skill_stage")


def test_required_resource_without_expected_digest_is_not_verified(tmp_path):
    receipt = SkillApplicationReceiptStore(tmp_path).observe(
        _contract(required=("SKILL.md",)),
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("skill")},
    )

    assert receipt is not None
    assert receipt.compliance_status == "unverified"
    assert "skill_application_resource_digest_missing" in receipt.error_codes


def test_resource_digest_mismatch_and_change_fail_closed(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    first = store.observe(
        _contract(required=("SKILL.md",)),
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("unexpected")},
        expected_resource_digests={"SKILL.md": _digest("expected")},
    )
    assert first is not None
    assert first.compliance_status == "unverified"
    assert "skill_application_resource_digest_mismatch" in first.error_codes

    changed = store.observe(
        _contract(required=("SKILL.md",)),
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("changed-again")},
        expected_resource_digests={"SKILL.md": _digest("expected")},
    )
    assert changed is not None
    assert changed.compliance_status == "unverified"
    assert "skill_application_resource_digest_changed" in changed.error_codes
    assert changed.resource_digests == first.resource_digests


def test_store_quarantines_one_bad_record_without_losing_valid_receipts(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    valid = store.observe(
        _contract(),
        _scope(),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("skill")},
        expected_resource_digests={"SKILL.md": _digest("skill")},
    )
    assert valid is not None
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["receipts"].append(
        {"receipt_id": "forged", "prompt": "must-not-survive"}
    )
    store.snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    recovered = SkillApplicationReceiptStore(tmp_path)
    assert recovered.status()["available"] is True
    assert recovered.status()["quarantine_count"] == 1
    assert recovered.require(valid.receipt_id).compliance_status == "verified"

    recovered.protect(valid.receipt_id, reference_id="evaluation:run-1")
    rewritten = recovered.snapshot_path.read_text(encoding="utf-8")
    assert "must-not-survive" not in rewritten
    assert "prompt" not in rewritten
    assert "evaluation:run-1" in rewritten


def test_top_level_corruption_fails_closed_without_overwriting_snapshot(tmp_path):
    snapshot = tmp_path / "skill_application_receipts.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"schema_version":'
    snapshot.write_bytes(original)

    store = SkillApplicationReceiptStore(tmp_path)
    assert store.status()["available"] is False
    with pytest.raises(SkillApplicationReceiptStorageError) as captured:
        store.observe(_contract(), _scope(), method="skill_read")
    assert captured.value.code == "skill_application_receipt_store_corrupt"
    assert snapshot.read_bytes() == original


def test_atomic_save_failure_rolls_back_in_memory_state(tmp_path, monkeypatch):
    store = SkillApplicationReceiptStore(tmp_path)
    monkeypatch.setattr(
        store,
        "_save_unlocked",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        store.observe(_contract(), _scope(), method="skill_read")

    assert store.list_receipts() == []
    assert store.snapshot_path.exists() is False


def test_snapshot_capacity_failure_rolls_back_without_publishing(tmp_path, monkeypatch):
    store = SkillApplicationReceiptStore(tmp_path)
    monkeypatch.setattr(
        "server.skills.application_receipts._MAX_SNAPSHOT_BYTES",
        1,
    )

    with pytest.raises(SkillApplicationReceiptStorageError) as captured:
        store.observe(_contract(), _scope(), method="skill_read")

    assert captured.value.code == "skill_application_receipt_store_limit"
    assert store.list_receipts() == []
    assert store.snapshot_path.exists() is False


def test_off_mode_skips_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "off")
    store = SkillApplicationReceiptStore(tmp_path)

    assert store.observe(_contract(), _scope(), method="skill_read") is None
    assert store.snapshot_path.exists() is False


def test_referenced_receipts_survive_retention_pruning(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "audit")
    store = SkillApplicationReceiptStore(tmp_path)
    unprotected = store.observe(_contract(), _scope(), method="skill_read")
    protected = store.observe(
        _contract(),
        SkillApplicationScope(
            run_id="run_receipt_2",
            task_id="task_receipt_2",
            node_id="workflow-agent",
            runtime_kind="workflow",
        ),
        method="skill_read",
    )
    assert unprotected is not None and protected is not None
    store.protect(protected.receipt_id, reference_id="evaluation:run-1")
    old = 1.0
    with store._lock:
        store._receipts[unprotected.receipt_id].updated_at = old
        store._receipts[protected.receipt_id].updated_at = old

    store.observe(
        _contract(),
        SkillApplicationScope(
            run_id="run_receipt_3",
            task_id="task_receipt_3",
            node_id="workflow-agent",
            runtime_kind="workflow",
        ),
        method="skill_read",
    )

    retained_ids = {item.receipt_id for item in store.list_receipts()}
    assert unprotected.receipt_id not in retained_ids
    assert protected.receipt_id in retained_ids


def test_snapshot_contains_only_bounded_evidence_not_private_content(tmp_path):
    store = SkillApplicationReceiptStore(tmp_path)
    private_body = "private prompt, arguments, Skill body, and model output"
    store.observe(
        _contract(),
        _scope(),
        method="skill_stage",
        resource_paths=["assets/template.txt"],
        resource_digests={"assets/template.txt": _digest(private_body)},
        tool_name="skill_stage",
    )

    persisted = store.snapshot_path.read_text(encoding="utf-8")
    assert private_body not in persisted
    for forbidden_key in ('"prompt"', '"arguments"', '"output"', '"content"'):
        assert forbidden_key not in persisted


class _InstalledSkillManager:
    def __init__(self, package_root: Path) -> None:
        self.package_root = package_root
        files = {
            path.relative_to(package_root).as_posix(): path.read_bytes()
            for path in package_root.rglob("*")
            if path.is_file() and path.name != "SKILL.md"
        }
        package_digest = compute_package_digest(
            (package_root / "SKILL.md").read_bytes(),
            files,
        )
        self.lifecycle_store = SimpleNamespace(
            require_version=lambda version_id: SimpleNamespace(
                skill_id="incident-review",
                package_digest=package_digest,
            )
        )

    def list_installed_skills(self):
        return [SimpleNamespace(skill_id="incident-review")]

    def get_skill_content(self, _skill_id, *, version_id=None):
        assert version_id == "skillversion_incident_v1"
        return (self.package_root / "SKILL.md").read_text(encoding="utf-8")

    def get_skill_directory(self, _skill_id, *, version_id=None):
        assert version_id == "skillversion_incident_v1"
        return self.package_root


@pytest.mark.asyncio
async def test_skill_tools_emit_application_evidence_for_installed_package(tmp_path):
    package_root = tmp_path / "package"
    (package_root / "references").mkdir(parents=True)
    skill_markdown_bytes = b"# Incident review\r\n"
    (package_root / "SKILL.md").write_bytes(skill_markdown_bytes)
    (package_root / "references" / "guide.md").write_text(
        "# Guide\n", encoding="utf-8"
    )
    (package_root / "assets").mkdir()
    (package_root / "assets" / "sample.pdf").write_bytes(
        b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
    )
    workspace_root = tmp_path / "workspaces"
    client = SimpleNamespace(request=AsyncMock(return_value={"ok": True}))
    provider = SandboxToolsetProvider(
        SimpleNamespace(workspace_root=workspace_root),
        client,
        skill_manager=_InstalledSkillManager(package_root),
    )
    call_metadata = {
        "task_id": "task_receipt_1",
        "run_id": "run_receipt_1",
        "node_id": "workflow-agent",
        "skill_version_bindings": {
            "incident-review": "skillversion_incident_v1"
        },
    }

    read = provider._skill_read(
        RuntimeToolCall(
            "skill_read", {"skill_id": "incident-review"}, call_metadata
        )
    )
    assert read.metadata["application_method"] == "skill_read"
    assert read.metadata["application_resource_paths"] == ["SKILL.md"]
    assert read.metadata["application_resource_digests"]["SKILL.md"] == (
        hashlib.sha256(skill_markdown_bytes).hexdigest()
    )
    assert read.metadata["application_expected_resource_digests"] == (
        read.metadata["application_resource_digests"]
    )

    stage = await provider._skill_stage(
        SandboxWorkspace(
            workspace_id="workspace-receipt",
            scope_type="workflow",
            scope_id="task:node",
            node_id="workflow-agent",
            quota_bytes=1024 * 1024,
        ),
        RuntimeToolCall(
            "skill_stage", {"skill_id": "incident-review"}, call_metadata
        ),
    )
    assert stage.metadata["application_method"] == "skill_stage"
    assert stage.metadata["application_resource_paths"] == [
        "SKILL.md",
        "assets/sample.pdf",
        "references/guide.md",
    ]
    assert stage.metadata["application_text_resource_paths"] == [
        "SKILL.md",
        "references/guide.md",
    ]
    assert stage.metadata["application_expected_resource_digests"] == (
        stage.metadata["application_resource_digests"]
    )
    assert set(stage.metadata["application_resource_digests"]) == {
        "SKILL.md",
        "assets/sample.pdf",
        "references/guide.md",
    }
    assert client.request.await_count == 3


@pytest.mark.asyncio
async def test_sandbox_text_access_metadata_is_bounded_and_digest_only(tmp_path):
    workspace_root = tmp_path / "workspaces"
    resource_path = "skills/incident-review/references/guide.md"
    binary_path = "skills/incident-review/assets/sample.pdf"
    resource_body = "private search result body"
    store = SandboxWorkspaceStore(
        tmp_path / "sandbox-store", workspace_root=workspace_root
    )
    workspace = store.get_or_create_workspace(
        scope_type="workflow",
        scope_id="task:node",
        node_id="workflow-agent",
        quota_bytes=1024 * 1024,
    )

    class WorkspaceClient:
        async def request(self, payload):
            action = payload.get("action")
            workspace = workspace_root / str(payload.get("workspace_id"))
            workspace.mkdir(parents=True, exist_ok=True)
            if action == "ensure_workspace":
                return {"ok": True}
            if action == "write_file":
                target = workspace / str(payload["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(payload["content_base64"]))
                return {"ok": True, "path": payload["path"]}
            if action == "read_file":
                target = workspace / str(payload["path"])
                return {
                    "ok": True,
                    "path": payload["path"],
                    "content": target.read_text(encoding="utf-8"),
                }
            if action == "search_files":
                return {
                    "ok": True,
                    "query": payload["query"],
                    "matches": [
                        {"path": resource_path, "line": 1, "preview": resource_body},
                        {"path": binary_path, "line": 1, "preview": "binary preview"},
                    ],
                }
            raise AssertionError(f"unexpected action: {action}")

    target = workspace_root / workspace.workspace_id / resource_path
    target.parent.mkdir(parents=True)
    target.write_text(resource_body, encoding="utf-8")
    binary_target = workspace_root / workspace.workspace_id / binary_path
    binary_target.parent.mkdir(parents=True)
    binary_target.write_bytes(b"%PDF-1.7\xffprivate")
    provider = SandboxToolsetProvider(
        store,
        WorkspaceClient(),
        skill_manager=SimpleNamespace(list_installed_skills=lambda: []),
    )
    result = await provider._sandbox_call(
        workspace,
        RuntimeToolCall(
            "sandbox_search_files",
            {"query": "private", "path": "skills/incident-review"},
            {"task_id": "task_receipt_1", "node_id": "workflow-agent"},
        )
    )

    assert result.metadata["sandbox_accessed_paths"] == [resource_path]
    assert result.metadata["sandbox_accessed_text_paths"] == [resource_path]
    assert result.metadata["sandbox_accessed_digests"] == {
        resource_path: _digest(resource_body)
    }
    assert binary_path not in result.output
    assert "binary preview" not in result.output
    serialized_metadata = json.dumps(result.metadata, ensure_ascii=False)
    assert "private" not in serialized_metadata
    assert resource_body not in serialized_metadata


@pytest.mark.asyncio
async def test_evaluation_overlay_uses_same_application_evidence_contract(tmp_path):
    package = {
        "skill_markdown": "# Evaluation Skill\n",
        "files": {"references/guide.md": "# Evidence\n"},
    }
    overlay = SimpleNamespace(
        overlay_id="skill_eval_overlay_candidate",
        content_digest=_digest("evaluation-package"),
        package=package,
    )
    provider = SandboxToolsetProvider(
        SimpleNamespace(workspace_root=tmp_path / "workspaces"),
        SimpleNamespace(),
        skill_manager=SimpleNamespace(list_installed_skills=lambda: []),
    )
    provider.configure_skill_evaluation(lambda _overlay_id: overlay)
    metadata = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_item_id": "item-candidate",
        "skill_evaluation_overlay_id": overlay.overlay_id,
        "skill_evaluation_required_resource_paths": ["references/guide.md"],
    }

    with pytest.raises(RuntimeToolError) as stage_before_read:
        await provider._skill_stage(
            SandboxWorkspace(
                workspace_id="workspace-evaluation",
                scope_type="workflow",
                scope_id="task:node",
                node_id="evaluation-agent",
                quota_bytes=1024 * 1024,
            ),
            RuntimeToolCall(
                "skill_stage", {"skill_id": "evaluation-skill"}, metadata
            ),
        )
    assert stage_before_read.value.code == "skill_application_required"

    read = provider._skill_read(
        RuntimeToolCall(
            "skill_read", {"skill_id": "evaluation-skill"}, metadata
        )
    )
    staged = await provider._skill_stage(
        SandboxWorkspace(
            workspace_id="workspace-evaluation",
            scope_type="workflow",
            scope_id="task:node",
            node_id="evaluation-agent",
            quota_bytes=1024 * 1024,
        ),
        RuntimeToolCall(
            "skill_stage", {"skill_id": "evaluation-skill"}, metadata
        ),
    )

    assert read.metadata["application_source_kind"] == "evaluation_overlay"
    assert read.metadata["application_version_id"] == overlay.overlay_id
    assert read.metadata["application_expected_resource_digests"] == (
        read.metadata["application_resource_digests"]
    )
    assert staged.metadata["application_content_digest"] == overlay.content_digest
    assert staged.metadata["application_resource_paths"] == [
        "SKILL.md",
        "references/guide.md",
    ]
    assert staged.metadata["application_expected_resource_digests"] == (
        staged.metadata["application_resource_digests"]
    )
    staged_output = json.loads(staged.output)
    assert staged_output["required_resources"] == [
        {
            "path": "skills/evaluation-skill/references/guide.md",
            "content": "# Evidence\n",
        }
    ]
    assert staged.metadata["evaluation_required_resource_count"] == 1
    assert "# Evidence" not in json.dumps(staged.metadata, ensure_ascii=False)


@pytest.mark.asyncio
async def test_evaluation_stage_rejects_unfrozen_required_resource(tmp_path):
    overlay = SimpleNamespace(
        overlay_id="skill_eval_overlay_candidate",
        content_digest=_digest("evaluation-package"),
        package={
            "skill_markdown": "# Evaluation Skill\n",
            "files": {"references/guide.md": "# Evidence\n"},
        },
    )
    provider = SandboxToolsetProvider(
        SimpleNamespace(workspace_root=tmp_path / "workspaces"),
        SimpleNamespace(),
        skill_manager=SimpleNamespace(list_installed_skills=lambda: []),
    )
    provider.configure_skill_evaluation(lambda _overlay_id: overlay)
    metadata = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_item_id": "item-candidate",
        "skill_evaluation_overlay_id": overlay.overlay_id,
        "skill_evaluation_required_resource_paths": ["references/missing.md"],
    }
    provider._skill_read(
        RuntimeToolCall("skill_read", {"skill_id": "evaluation-skill"}, metadata)
    )

    with pytest.raises(RuntimeToolError) as captured:
        await provider._skill_stage(
            SandboxWorkspace(
                workspace_id="workspace-evaluation",
                scope_type="workflow",
                scope_id="task:node",
                node_id="evaluation-agent",
                quota_bytes=1024 * 1024,
            ),
            RuntimeToolCall(
                "skill_stage", {"skill_id": "evaluation-skill"}, metadata
            ),
        )

    assert captured.value.code == "skill_application_contract_stale"


def test_advisory_contract_verifies_actual_prompt_injection(tmp_path):
    digest = _digest("builtin-method")
    contract = build_application_contract(
        skill_id="research-method",
        source_kind="builtin",
        version_id=f"builtin:{digest}",
        content_digest=digest,
        policy="advisory",
    )
    store = SkillApplicationReceiptStore(tmp_path)
    receipt = store.observe(
        contract,
        SkillApplicationScope(
            run_id="run_expert_1",
            task_id="task_expert_1",
            node_id="agency-orchestrator",
            runtime_kind="expert_team",
        ),
        method="prompt_injected",
    )

    assert receipt is not None
    assert receipt.methods == ("prompt_injected",)
    assert receipt.compliance_status == "verified"


def test_main_observer_resolves_server_frozen_version_identity(
    tmp_path, monkeypatch
):
    import server.main as main

    receipt_store = SkillApplicationReceiptStore(tmp_path / "receipts")
    snapshot = SimpleNamespace(
        skill_id="incident-review",
        source_kind="git",
        package_digest=_digest("package-v1"),
        trust_fingerprint=_digest("trust-v1"),
    )
    manager = SimpleNamespace(
        lifecycle_store=SimpleNamespace(
            require_version=lambda _version_id: snapshot
        ),
        list_installed_skills=lambda: [],
    )
    monkeypatch.setattr(main, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(
        main,
        "skill_application_observer",
        SkillApplicationObserver(receipt_store, lambda: main.get_skill_manager()),
    )

    selected_id = main.record_skill_application(
        skill_id="incident-review",
        run_id="run_receipt_main",
        task_id="task_receipt_main",
        node_id="workflow-agent",
        runtime_kind="workflow",
        version_id="skillversion_incident_v1",
    )
    applied_id = main.record_skill_application(
        skill_id="incident-review",
        run_id="run_receipt_main",
        task_id="task_receipt_main",
        node_id="workflow-agent",
        runtime_kind="workflow",
        version_id="skillversion_incident_v1",
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest("skill body")},
        expected_resource_digests={"SKILL.md": _digest("skill body")},
        tool_name="skill_read",
    )

    assert selected_id == applied_id
    receipt = receipt_store.require(str(applied_id))
    assert receipt.content_digest == snapshot.package_digest
    assert receipt.trust_fingerprint == snapshot.trust_fingerprint
    assert receipt.compliance_status == "verified"


def test_main_observer_records_sanitized_failed_application(tmp_path, monkeypatch):
    import server.main as main

    receipt_store = SkillApplicationReceiptStore(tmp_path / "receipts")
    snapshot = SimpleNamespace(
        skill_id="incident-review",
        source_kind="git",
        package_digest=_digest("package-v1"),
        trust_fingerprint=_digest("trust-v1"),
    )
    monkeypatch.setattr(
        main,
        "skill_application_observer",
        SkillApplicationObserver(
            receipt_store,
            lambda: SimpleNamespace(
                lifecycle_store=SimpleNamespace(
                    require_version=lambda _version_id: snapshot
                ),
                list_installed_skills=lambda: [],
            ),
        ),
    )

    receipt_id = main.record_skill_application(
        skill_id="incident-review",
        run_id="run_receipt_failed",
        task_id="task_receipt_failed",
        node_id="workflow-agent",
        runtime_kind="workflow",
        version_id="skillversion_incident_v1",
        tool_name="skill_stage",
        error_code="skill_runtime_incompatible",
    )

    receipt = receipt_store.require(str(receipt_id))
    assert receipt.application_status == "failed"
    assert receipt.compliance_status == "incomplete"
    assert receipt.error_codes == ("skill_runtime_incompatible",)
    persisted = receipt_store.snapshot_path.read_text(encoding="utf-8")
    assert "arguments" not in persisted
    assert "output" not in persisted


def test_creator_v2_accept_verifier_rechecks_protected_receipt(
    tmp_path, monkeypatch
):
    import server.main as main

    store = SkillApplicationReceiptStore(tmp_path)
    package_digest = _digest("evaluation package")
    content_digest = _digest("skill markdown")
    contract = build_application_contract(
        skill_id="evaluation-skill",
        source_kind="evaluation_overlay",
        version_id="skill_eval_overlay_one",
        content_digest=package_digest,
        policy="require_read",
    )
    receipt = store.observe(
        contract,
        SkillApplicationScope(
            run_id="runtime_eval_one",
            task_id="task_eval_one",
            node_id="evaluation-agent",
            runtime_kind="skill_evaluation",
        ),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": content_digest},
        expected_resource_digests={"SKILL.md": content_digest},
        tool_name="skill_read",
    )
    assert receipt is not None and receipt.compliance_status == "verified"
    receipt = store.protect(
        receipt.receipt_id,
        reference_id="evaluation-item:skill_eval_item_one",
    )
    monkeypatch.setattr(main, "skill_application_receipt_store", store)
    item = SimpleNamespace(
        item_id="skill_eval_item_one",
        case_id="case-one",
        target="candidate",
        runtime_run_id="runtime_eval_one",
        overlay_id="skill_eval_overlay_one",
        application_receipt_id=receipt.receipt_id,
        application_receipt_revision=receipt.revision,
    )
    run = SimpleNamespace(
        evaluation_suite_id="skill_eval_suite_one",
        frozen_digest=package_digest,
        cases=[SimpleNamespace(case_id="case-one", required_resource_paths=())],
        items=[item],
    )

    main.verify_skill_evaluation_application_receipts(run)

    item.application_receipt_revision -= 1
    with pytest.raises(SkillEvaluationValidationError) as captured:
        main.verify_skill_evaluation_application_receipts(run)
    assert captured.value.code == "skill_application_receipt_mismatch"


def test_creator_v2_verifier_uses_previous_overlay_digest(
    tmp_path, monkeypatch
):
    import server.main as main

    evaluation_store = SkillEvaluationStore(tmp_path / "evaluation")
    package = {
        "name": "previous-version",
        "slug": "previous-version",
        "description": "Use when verifying a previous Skill version.",
        "skill_markdown": (
            "---\nname: previous-version\n"
            "description: Use when verifying a previous Skill version.\n---\n\n"
            "# Previous version\n\nRead the frozen previous revision.\n"
        ),
        "files": {},
    }
    overlay = evaluation_store.create_overlay(
        draft_id="draft_previous",
        draft_revision=2,
        content_digest=compute_package_digest(package["skill_markdown"], package["files"]),
        package=package,
    )
    receipt_store = SkillApplicationReceiptStore(tmp_path / "receipts")
    contract = build_application_contract(
        skill_id="evaluation-skill",
        source_kind="evaluation_overlay",
        version_id=overlay.overlay_id,
        content_digest=overlay.content_digest,
        policy="require_read",
    )
    receipt = receipt_store.observe(
        contract,
        SkillApplicationScope(
            run_id="runtime_previous",
            task_id="task_previous",
            node_id="evaluation-agent",
            runtime_kind="skill_evaluation",
        ),
        method="skill_read",
        resource_paths=["SKILL.md"],
        resource_digests={"SKILL.md": _digest(package["skill_markdown"])},
        expected_resource_digests={"SKILL.md": _digest(package["skill_markdown"])},
        tool_name="skill_read",
    )
    assert receipt is not None
    receipt = receipt_store.protect(
        receipt.receipt_id,
        reference_id="evaluation-item:item_previous",
    )
    monkeypatch.setattr(main, "skill_application_receipt_store", receipt_store)
    monkeypatch.setattr(main, "skill_evaluation_store", evaluation_store)
    run = SimpleNamespace(
        evaluation_suite_id="suite_previous",
        frozen_digest=_digest("candidate-digest"),
        cases=[SimpleNamespace(case_id="case-one", required_resource_paths=())],
        items=[SimpleNamespace(
            item_id="item_previous",
            case_id="case-one",
            target="previous",
            runtime_run_id="runtime_previous",
            overlay_id=overlay.overlay_id,
            application_receipt_id=receipt.receipt_id,
            application_receipt_revision=receipt.revision,
        )],
    )

    main.verify_skill_evaluation_application_receipts(run)


def test_enforce_mode_propagates_receipt_failures(monkeypatch):
    import server.main as main

    class BrokenObserver:
        def record(self, **_kwargs):
            raise SkillApplicationReceiptError(
                "store unavailable",
                code="skill_application_receipt_store_corrupt",
            )

    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "enforce")
    monkeypatch.setattr(main, "skill_application_observer", BrokenObserver())

    with pytest.raises(SkillApplicationReceiptError) as captured:
        main.record_skill_application(
            skill_id="incident-review",
            run_id="run_receipt_enforce",
            task_id="task_receipt_enforce",
            node_id="workflow-agent",
            runtime_kind="workflow",
        )
    assert captured.value.code == "skill_application_receipt_store_corrupt"


def test_chat_application_resolves_exact_injected_frozen_skill(monkeypatch):
    import server.main as main

    skill_markdown = "# Incident review\n\nFollow the frozen workflow.\n"
    package_digest = _digest("chat-package")
    trust_fingerprint = _digest("chat-trust")
    installed = SimpleNamespace(content_digest=package_digest)
    version = SimpleNamespace(
        skill_id="incident-review",
        source_kind="git",
        package_digest=package_digest,
        trust_fingerprint=trust_fingerprint,
    )
    manager = SimpleNamespace(
        require_activation=lambda _skill_id, **_kwargs: installed,
        bind_skill_versions=lambda _skill_ids: {
            "incident-review": "skillversion_chat_v1"
        },
        lifecycle_store=SimpleNamespace(require_version=lambda _version_id: version),
        get_skill_content=lambda _skill_id, version_id=None: skill_markdown,
    )
    monkeypatch.setattr(main, "get_skill_manager", lambda: manager)

    resolved = main.resolve_chat_skill_application(
        main.ChatSkillApplication(
            skill_id="incident-review",
            expected_content_digest=package_digest,
        ),
        [
            main.ChatMessage(
                role="system",
                content=f"Current Skill\n\n{skill_markdown}",
            ),
            main.ChatMessage(role="user", content="Create the report."),
        ],
    )

    assert resolved == {
        "skill_id": "incident-review",
        "version_id": "skillversion_chat_v1",
        "source_kind": "git",
        "content_digest": package_digest,
        "trust_fingerprint": trust_fingerprint,
    }

    with pytest.raises(SkillApplicationReceiptError) as captured:
        main.resolve_chat_skill_application(
            main.ChatSkillApplication(
                skill_id="incident-review",
                expected_content_digest=package_digest,
            ),
            [main.ChatMessage(role="user", content="No Skill prompt here.")],
        )
    assert captured.value.code == "skill_application_prompt_missing"
