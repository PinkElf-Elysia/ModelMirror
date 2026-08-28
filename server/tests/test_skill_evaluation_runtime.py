from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server.sandbox_sidecar.engine import SandboxEngine
from server.skills.creator_evaluation import (
    SkillEvaluationCase,
    SkillEvaluationItem,
    SkillEvaluationOverlay,
    SkillEvaluationRun,
    SkillEvaluationValidationError,
)
from server.skills.creator_evaluation_runtime import (
    SKILL_EVALUATION_ALLOWED_TOOLS,
    SKILL_EVALUATION_BASELINE_TOOLS,
    build_skill_evaluation_model_identity,
    build_skill_evaluation_workflow_invocation,
    is_recoverable_skill_evaluation_tool_error,
    normalize_skill_evaluation_model_id,
    require_skill_evaluation_actual_model,
    skill_evaluation_resource_repair_instruction,
    skill_evaluation_tool_names,
    skill_evaluation_model_temperature,
)
from server.skills.package_validation import compute_package_digest
from server.xpert_runtime.sandbox_client import LocalSandboxClient
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.sandbox_store import SandboxWorkspaceStore
from server.xpert_runtime.sandbox_toolset import SandboxToolsetProvider
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError


class _SkillManager:
    def list_installed_skills(self):
        return []


def test_only_trusted_evaluation_argument_errors_are_recoverable():
    trusted = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_workflow_version": "skill-evaluation-workflow-v1",
        "skill_evaluation_profile": "skill_evaluation_v1",
        "skill_evaluation_item_id": "item-one",
        "skill_evaluation_workspace_id": "workspace-one",
    }
    assert is_recoverable_skill_evaluation_tool_error(
        trusted,
        tool_name="sandbox_search_files",
        error_code="invalid_query",
    )
    assert not is_recoverable_skill_evaluation_tool_error(
        trusted,
        tool_name="sandbox_search_files",
        error_code="sandbox_profile_capability_invalid",
    )
    assert not is_recoverable_skill_evaluation_tool_error(
        trusted,
        tool_name="skill_install",
        error_code="invalid_query",
    )
    assert skill_evaluation_model_temperature(trusted) == 0.0
    assert skill_evaluation_model_temperature({}) == 0.7
    assert not is_recoverable_skill_evaluation_tool_error(
        {**trusted, "skill_evaluation_workspace_id": ""},
        tool_name="sandbox_search_files",
        error_code="invalid_query",
    )


def test_evaluation_tool_contract_is_target_specific_and_fail_closed():
    trusted = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_workflow_version": "skill-evaluation-workflow-v1",
        "skill_evaluation_profile": "skill_evaluation_v1",
        "skill_evaluation_item_id": "item-one",
        "skill_evaluation_workspace_id": "workspace-one",
    }

    assert skill_evaluation_tool_names(
        {**trusted, "skill_evaluation_target": "baseline"}
    ) == SKILL_EVALUATION_BASELINE_TOOLS
    assert skill_evaluation_tool_names(
        {**trusted, "skill_evaluation_target": "candidate"}
    ) == SKILL_EVALUATION_ALLOWED_TOOLS
    assert skill_evaluation_tool_names(
        {**trusted, "skill_evaluation_target": "previous"}
    ) == SKILL_EVALUATION_ALLOWED_TOOLS
    assert skill_evaluation_tool_names(
        {**trusted, "skill_evaluation_target": "forged"}
    ) == ()
    assert skill_evaluation_tool_names(
        {**trusted, "skill_evaluation_workspace_id": "", "skill_evaluation_target": "baseline"}
    ) == ()


def test_evaluation_unstaged_resource_gets_one_bounded_repair_instruction():
    metadata = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_workflow_version": "skill-evaluation-workflow-v1",
        "skill_evaluation_profile": "skill_evaluation_v1",
        "skill_evaluation_item_id": "item-one",
        "skill_evaluation_workspace_id": "workspace-one",
        "skill_evaluation_target": "candidate",
    }
    path = "skills/evaluation-skill/references/guide.md"

    instruction = skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_read_file",
        arguments={"path": path},
        staged_paths=(),
    )

    assert instruction is not None
    assert "skill_read" in instruction
    assert "skill_stage" in instruction
    assert "evaluation-skill" in instruction
    assert skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_read_file",
        arguments={"path": path},
        staged_paths=(path,),
    ) is None
    assert skill_evaluation_resource_repair_instruction(
        {**metadata, "skill_evaluation_target": "baseline"},
        tool_name="sandbox_read_file",
        arguments={"path": path},
        staged_paths=(),
    ) is None
    wrong_alias_instruction = skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_read_file",
        arguments={"path": "skills/forged/references/guide.md"},
        staged_paths=(),
    )
    assert wrong_alias_instruction is not None
    assert "skills/forged" not in wrong_alias_instruction


def test_evaluation_staged_resource_path_mismatch_gets_exact_path_correction():
    metadata = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_workflow_version": "skill-evaluation-workflow-v1",
        "skill_evaluation_profile": "skill_evaluation_v1",
        "skill_evaluation_item_id": "item-one",
        "skill_evaluation_workspace_id": "workspace-one",
        "skill_evaluation_target": "candidate",
    }
    staged = (
        "skills/evaluation-skill/SKILL.md",
        "skills/evaluation-skill/references/compatibility-checklist.md",
        "skills/evaluation-skill/references/rollback-criteria.md",
    )

    instruction = skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_read_file",
        arguments={"path": "skills/evaluation-skill/references"},
        staged_paths=staged,
    )

    assert instruction is not None
    assert "already staged" in instruction
    assert "Do not call skill_read or skill_stage again" in instruction
    assert "skills/evaluation-skill/references/compatibility-checklist.md" in instruction
    assert "never pass a directory" in instruction
    wrong_alias_instruction = skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_read_file",
        arguments={"path": "skills/runtime-skill/references/guide.md"},
        staged_paths=staged,
    )
    assert wrong_alias_instruction is not None
    assert "skills/runtime-skill" not in wrong_alias_instruction
    assert "skills/evaluation-skill/references/rollback-criteria.md" in wrong_alias_instruction
    assert skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_search_files",
        arguments={"path": "skills/evaluation-skill/references"},
        staged_paths=staged,
    ) is None
    assert skill_evaluation_resource_repair_instruction(
        metadata,
        tool_name="sandbox_read_file",
        arguments={"path": "../private/forged.md"},
        staged_paths=staged,
    ) is None


@pytest.mark.asyncio
async def test_failed_runtime_item_preserves_observed_usage(
    monkeypatch: pytest.MonkeyPatch,
):
    import server.main as main_module

    run, _baseline, candidate = _run_and_items()
    run.evaluation_suite_id = "suite-runtime"
    usage_reads = 0
    cleanup_calls: list[str] = []

    class Provider:
        async def provision_skill_evaluation_workspace(self, **_kwargs):
            return "workspace-partial"

        def consume_skill_evaluation_usage(self, _item_id):
            nonlocal usage_reads
            usage_reads += 1
            return (
                {"tool_names": ["skill_read"], "skill_read": True}
                if usage_reads == 1
                else {}
            )

        async def cleanup_skill_evaluation_workspace(self, workspace_id):
            cleanup_calls.append(workspace_id)

    class StreamFailure(RuntimeError):
        code = "skill_application_required"

    class Registry:
        async def list_runs(self, **_kwargs):
            return [
                SimpleNamespace(
                    metadata={
                        "token_usage": {
                            "model_calls": 2,
                            "input_tokens": 40,
                        }
                    }
                )
            ]

    receipt = SimpleNamespace(
        receipt_id="skillappreceipt_partial",
        revision=2,
        compliance_status="verified",
        source_kind="evaluation_overlay",
        version_id=_overlay().overlay_id,
        content_digest=_overlay().content_digest,
        policy="require_read",
        required_resource_paths=(),
    )

    class ReceiptStore:
        def list_receipts(self, **_kwargs):
            return [receipt]

        def protect(self, receipt_id, *, reference_id):
            assert receipt_id == receipt.receipt_id
            assert reference_id == "evaluation-item:item_candidate"
            return receipt

    async def fake_run_workflow_response(*_args, **_kwargs):
        return SimpleNamespace(
            headers={
                "X-ModelMirror-Runtime-Task-Id": "task-partial",
                "X-ModelMirror-Runtime-Run-Id": "run-partial",
            }
        )

    async def fail_stream(_response):
        raise StreamFailure("resource must be staged")

    monkeypatch.setattr(main_module, "workflow_sandbox_provider", Provider())
    monkeypatch.setattr(main_module, "run_registry", Registry())
    monkeypatch.setattr(main_module, "skill_application_receipt_store", ReceiptStore())
    monkeypatch.setattr(main_module, "_run_workflow_response", fake_run_workflow_response)
    monkeypatch.setattr(main_module, "consume_workflow_stream", fail_stream)

    with pytest.raises(SkillEvaluationValidationError) as captured:
        await main_module.run_skill_evaluation_item(
            run,
            candidate,
            _case(),
            _overlay(),
        )

    assert captured.value.code == "skill_application_required"
    assert captured.value.usage == {
        "model_calls": 2,
        "input_tokens": 40,
        "tool_calls": 1,
    }
    assert captured.value.runtime_run_id == "run-partial"
    assert captured.value.skill_read is True
    assert captured.value.application_receipt_id == "skillappreceipt_partial"
    assert captured.value.application_receipt_revision == 2
    assert captured.value.application_compliance == "verified"
    assert cleanup_calls == ["workspace-partial"]


def test_actual_model_receipt_distinguishes_selection_and_provider_identity():
    receipt = build_skill_evaluation_model_identity(
        requested_model_id="gateway/primary",
        selected_model_id="gateway/fallback",
        observed_model_ids={"provider/actual"},
        successful_response_count=2,
        missing_model_count=0,
    )

    assert receipt == {
        "status": "verified",
        "requested_model_id": "gateway/primary",
        "selected_model_id": "gateway/fallback",
        "actual_model_id": "provider/actual",
        "successful_response_count": 2,
        "missing_model_count": 0,
    }
    assert require_skill_evaluation_actual_model({"model_identity": receipt}) == (
        "provider/actual"
    )
    assert set(receipt) == {
        "status",
        "requested_model_id",
        "selected_model_id",
        "actual_model_id",
        "successful_response_count",
        "missing_model_count",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" provider/actual ", "provider/actual"),
        ("provider\nforged", ""),
        ("provider\x7fforged", ""),
        ("x" * 256, "x" * 256),
        ("x" * 257, ""),
        (None, ""),
    ],
)
def test_actual_model_identity_is_bounded_and_log_safe(raw, expected):
    assert normalize_skill_evaluation_model_id(raw) == expected


@pytest.mark.parametrize(
    ("observed", "successful", "missing", "expected_code"),
    [
        ({"provider/one"}, 2, 1, "skill_evaluation_actual_model_unknown"),
        ({"provider/one", "provider/two"}, 2, 0, "skill_evaluation_actual_model_changed"),
        (set(), 0, 0, "skill_evaluation_actual_model_unknown"),
    ],
)
def test_actual_model_receipt_fails_closed(
    observed, successful, missing, expected_code
):
    receipt = build_skill_evaluation_model_identity(
        requested_model_id="gateway/primary",
        selected_model_id="gateway/primary",
        observed_model_ids=observed,
        successful_response_count=successful,
        missing_model_count=missing,
    )

    with pytest.raises(SkillEvaluationValidationError) as captured:
        require_skill_evaluation_actual_model({"model_identity": receipt})

    assert captured.value.code == expected_code


def test_creator_and_evaluation_execution_records_do_not_persist_private_payloads(
    tmp_path,
):
    store = WorkflowExecutionStore(tmp_path / "executions")
    creator = store.create(
        task_id="creator-task",
        run_id="creator-run",
        run_type="workflow",
        workflow={"id": "creator", "nodes": [{"secret": "private workflow"}]},
        inputs={"creator_request": "private review feedback"},
        runtime_metadata={
            "creator_session_id": "skillcreator_private",
            "creator_session_revision": 4,
            "assistant_agent_id": "skill-creator-assistant-v1",
            "creator_workflow_version": "skill-creator-workflow-v1",
            "creator_requirement_ids": ["intent"],
        },
    )
    assert creator.inputs == {"creator_session_id": "skillcreator_private"}
    assert "nodes" not in creator.workflow
    store.append_event(
        creator.task_id,
        {"event": "workflow_end", "final_output": "private generated text"},
    )
    store.complete(creator.task_id, result="private generated text")

    evaluation = store.create(
        task_id="evaluation-task",
        run_id="evaluation-run",
        run_type="skill_evaluation",
        workflow={"id": "evaluation", "nodes": [{"secret": "private case"}]},
        inputs={"evaluation_request": "private fixture prompt"},
        runtime_metadata={
            "runtime_run_type": "skill_evaluation",
            "skill_evaluation_workflow_version": "skill-evaluation-workflow-v1",
            "skill_evaluation_profile": "skill_evaluation_v1",
            "skill_evaluation_run_id": "skill-eval-run",
            "skill_evaluation_item_id": "skill-eval-item",
            "skill_evaluation_case_id": "skill-eval-case",
        },
    )
    store.complete(evaluation.task_id, result="private baseline output")

    persisted = (tmp_path / "executions" / "workflow_executions.json").read_text(
        encoding="utf-8"
    )
    for private_text in (
        "private workflow",
        "private review feedback",
        "private generated text",
        "private case",
        "private fixture prompt",
        "private baseline output",
    ):
        assert private_text not in persisted


def _overlay() -> SkillEvaluationOverlay:
    skill_markdown = "---\nname: runtime-skill\ndescription: Use when runtime tests need it.\n---\n\n# Workflow\n\nRead the input."
    files = {"references/guide.md": "# Guide\n\nEvidence."}
    return SkillEvaluationOverlay(
        overlay_id="skill_eval_overlay_candidate",
        draft_id="skilldraft_runtime",
        draft_revision=1,
        content_digest=compute_package_digest(skill_markdown, files),
        package={
            "name": "runtime-skill",
            "slug": "runtime-skill",
            "description": "Runtime Skill",
            "skill_markdown": skill_markdown,
            "files": files,
        },
        package_fingerprint="b" * 64,
    )


def _case(*, resources: list[str] | None = None) -> SkillEvaluationCase:
    return SkillEvaluationCase(
        case_id="case_runtime",
        name="Runtime case",
        prompt="Summarize the supplied note.",
        expected_behavior="Return a short summary.",
        fixtures=[{"path": "note.txt", "content": "Frozen note."}],
        assertions=[],
        required_resource_paths=resources or [],
        case_fingerprint="c" * 64,
    )


def _run_and_items():
    baseline = SkillEvaluationItem(
        item_id="item_baseline",
        pair_id="pair_runtime",
        case_id="case_runtime",
        target="baseline",
        repetition=1,
        overlay_id=None,
    )
    candidate = SkillEvaluationItem(
        item_id="item_candidate",
        pair_id="pair_runtime",
        case_id="case_runtime",
        target="candidate",
        repetition=1,
        overlay_id="skill_eval_overlay_candidate",
    )
    run = SkillEvaluationRun(
        run_id="skill_eval_run_runtime",
        session_id="skillcreator_runtime",
        draft_id="skilldraft_runtime",
        draft_revision=1,
        frozen_digest=_overlay().content_digest,
        baseline_overlay_id=None,
        candidate_overlay_id="skill_eval_overlay_candidate",
        model_id="test/model",
        repetitions=1,
        cases=[_case()],
        items=[baseline, candidate],
        config={},
    )
    return run, baseline, candidate


def test_fixed_workflow_keeps_task_inputs_fair_and_baseline_skill_free():
    run, baseline, candidate = _run_and_items()
    left = build_skill_evaluation_workflow_invocation(
        run, baseline, _case(), None, workspace_id="ws_left"
    )
    right = build_skill_evaluation_workflow_invocation(
        run, candidate, _case(), _overlay(), workspace_id="ws_right"
    )

    left_request = json.loads(left.inputs["evaluation_request"])
    right_request = json.loads(right.inputs["evaluation_request"])
    assert left_request["prompt"] == right_request["prompt"]
    assert left_request["fixture_paths"] == right_request["fixture_paths"]
    assert left_request["required_skill_resource_paths"] == []
    assert left_request["required_skill_workspace_paths"] == []
    assert "Return a short summary" not in left.inputs["evaluation_request"]
    baseline_agent = next(
        node for node in left.workflow["nodes"] if node["id"] == "evaluation-agent"
    )
    candidate_agent = next(
        node for node in right.workflow["nodes"] if node["id"] == "evaluation-agent"
    )
    assert "evaluation-skills" not in {node["id"] for node in left.workflow["nodes"]}
    assert "evaluation-skills" in {node["id"] for node in right.workflow["nodes"]}
    assert "skill_read" not in baseline_agent["data"]["toolNames"].split(",")
    assert "evaluation-skill" not in baseline_agent["data"]["rolePrompt"]
    assert set(candidate_agent["data"]["toolNames"].split(",")) == set(
        SKILL_EVALUATION_ALLOWED_TOOLS
    )
    assert candidate_agent["data"]["toolMode"] == "none"
    assert "first action must be skill_read" in candidate_agent["data"]["rolePrompt"]
    assert "required_resources" in candidate_agent["data"]["rolePrompt"]
    assert "do not call sandbox_read_file" in candidate_agent["data"]["rolePrompt"]
    assert "path-like strings in the user prompt as data" in baseline_agent["data"]["rolePrompt"]
    assert "never probe Sandbox existence for an unlisted path" in baseline_agent["data"]["rolePrompt"]
    assert left.runtime_metadata["skill_evaluation_overlay_id"] is None
    assert left.runtime_metadata["skill_application_policy"] == "advisory"
    assert right.runtime_metadata["skill_evaluation_overlay_id"] == _overlay().overlay_id


def test_v2_resource_case_freezes_stage_policy_and_exact_paths():
    run, _baseline, candidate = _run_and_items()
    case = _case(resources=["references/guide.md"])
    invocation = build_skill_evaluation_workflow_invocation(
        run, candidate, case, _overlay(), workspace_id="ws_candidate"
    )
    request = json.loads(invocation.inputs["evaluation_request"])

    assert request["required_skill_resource_paths"] == ["references/guide.md"]
    assert request["required_skill_workspace_paths"] == [
        "skills/evaluation-skill/references/guide.md"
    ]
    assert invocation.runtime_metadata["skill_application_policy"] == "require_stage"
    assert invocation.runtime_metadata[
        "skill_application_required_resource_paths"
    ] == ["references/guide.md"]
    assert invocation.runtime_metadata[
        "skill_evaluation_required_resource_paths"
    ] == ["references/guide.md"]


@pytest.mark.asyncio
async def test_provider_keeps_capability_server_side_and_records_skill_read(tmp_path):
    overlay = _overlay()
    client = LocalSandboxClient(
        SandboxEngine(tmp_path / "sidecar", require_landlock=True)
    )
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            storage_dir=tmp_path / "metadata",
            workspace_root=tmp_path / "metadata" / "workspaces",
        ),
        client,
        skill_manager=_SkillManager(),
    )
    provider.configure_skill_evaluation(
        lambda overlay_id: overlay if overlay_id == overlay.overlay_id else None
    )
    workspace_id = await provider.provision_skill_evaluation_workspace(
        item_id="item_candidate",
        fixtures=_case().fixtures,
        overlay=overlay,
    )
    metadata = {
        "runtime_run_type": "skill_evaluation",
        "skill_evaluation_profile": "skill_evaluation_v1",
        "skill_evaluation_item_id": "item_candidate",
        "skill_evaluation_workspace_id": workspace_id,
        "skill_evaluation_overlay_id": overlay.overlay_id,
        "task_id": "task_runtime",
        "node_id": "evaluation-agent",
        "run_id": "run_runtime",
    }

    read = await provider.call_tool(
        RuntimeToolCall(
            tool_name="skill_read",
            arguments={"skill_id": "evaluation-skill"},
            metadata=dict(metadata),
        )
    )
    assert "# Workflow" in read.output
    assert "provisioning_capability" not in read.output

    written = await provider.call_tool(
        RuntimeToolCall(
            tool_name="sandbox_write_file",
            arguments={"path": "work/result.txt", "content": "done"},
            metadata=dict(metadata),
        )
    )
    assert "result.txt" in written.output
    with pytest.raises(RuntimeToolError) as exc_info:
        await provider.call_tool(
            RuntimeToolCall(
                tool_name="sandbox_write_file",
                arguments={"path": "inputs/note.txt", "content": "tampered"},
                metadata=dict(metadata),
            )
        )
    assert exc_info.value.code == "write_scope_denied"

    usage = provider.consume_skill_evaluation_usage("item_candidate")
    assert usage["skill_read"] is True
    assert set(usage["tool_names"]) == {"sandbox_write_file", "skill_read"}
    manifest = await provider.collect_skill_evaluation_manifest(workspace_id)
    assert manifest[0]["path"] == "work/result.txt"
    assert manifest[0]["size"] == 4
    assert manifest[0]["preview"] == "done"
    await provider.cleanup_skill_evaluation_workspace(workspace_id)
