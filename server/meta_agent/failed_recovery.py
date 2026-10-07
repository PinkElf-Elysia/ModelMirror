"""Bounded repair of retained Intent or Recipe; never an execution path."""
from __future__ import annotations

from copy import deepcopy
import re
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field

from .capabilities import assert_scope_is_authorized
from .failed_artifacts import (
    FailedArtifactUnavailable, RecipeInputDraftV1, RecipeResourceDraftV1, _retained_intent, _retained_recipe,
    load_failed_generation_artifact, parse_recipe_input_draft, parse_recipe_resource_draft, recipe_source_format,
)
from .generation_recipe import REF, FlowItem, GenerationRecipeV1, RecipeInput, recipe_input_contract
from .generation_diagnostics import (
    GenerationDiagnostics, _CATEGORIES, _CODES, _PHASES_BY_STAGE,
    _PRIVATE_TOKEN, _TOKEN, _safe_location,
)
from .graph_ir_v3 import graph_authoring_checksum
from .graph_patch import (
    GRAPH_PATCH_CHECKSUM_PATTERN, GraphPatchEnvelopeV1, GraphPatchModel,
    apply_graph_patch, graph_patch_checksum,
)
from .headless_authoring import (
    HEADLESS_AUTHORING_VERSION, HeadlessAuthoringConflictError, HeadlessAuthoringError,
    _ProposalState, _apply_layout, _candidate_from_proposal, _default_agent_model,
    _intersect_scope, _report_from_proposal, _validate_intent_authorization,
    candidate_authoring_checksum, canonical_checksum, safe_headless_error_message,
    AuthoringProposalConflictError,
    requires_recipe_recovery,
)
from .meta_planner_v2 import validate_vision_generation_authorization
from .node_adapters import get_planner_node_adapter
from .resource_generation_contract import generation_resource_ids
from .recipe_edits import MAX_RECIPE_EDITS, RecipeEdit, apply_recipe_edits, recipe_edit_contract, recipe_edit_schema
from .schemas import (
    GraphIntentV3, MetaPlannerGenerateRequest, MetaPlannerIRCompatibility,
    MetaPlannerIRResourceBinding, MetaPlannerScope, MetaPlannerTaskPlan,
)


class ReplaceRecipeControlFlow(GraphPatchModel):
    op: Literal["replace_recipe_control_flow"]
    control_flow: list[FlowItem] = Field(min_length=1, max_length=24)


class RecipeControlFlowPatchV1(GraphPatchModel):
    protocol_version: Literal["recipe_control_flow_v1"]
    proposal_revision: int = Field(ge=1)
    expected_graph_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    expected_candidate_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    operations: list[ReplaceRecipeControlFlow] = Field(min_length=1, max_length=1)


class RecipeEditsPatchV1(GraphPatchModel):
    protocol_version: Literal["recipe_edits_v1"]
    proposal_revision: int = Field(ge=1)
    expected_graph_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    expected_candidate_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    operations: list[RecipeEdit] = Field(min_length=1, max_length=MAX_RECIPE_EDITS)


class ReplaceRecipeResources(GraphPatchModel):
    op: Literal["replace_recipe_resources"]
    resources: list[MetaPlannerIRResourceBinding] = Field(max_length=40)


class RecipeResourceBindingsPatchV1(GraphPatchModel):
    protocol_version: Literal["recipe_resource_bindings_v1"]
    proposal_revision: int = Field(ge=1)
    expected_graph_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    expected_candidate_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    operations: list[ReplaceRecipeResources] = Field(min_length=1, max_length=1)


class ReplaceRecipeInputs(GraphPatchModel):
    op: Literal["replace_recipe_inputs"]
    node_ref: str = Field(pattern=REF)
    inputs: list[RecipeInput] | None = Field(max_length=50)


class ConfirmRecipeProtocol(GraphPatchModel):
    op: Literal["confirm_recipe_protocol"]


class RecipeInputsPatchV1(GraphPatchModel):
    protocol_version: Literal["recipe_inputs_v1"]
    proposal_revision: int = Field(ge=1)
    expected_graph_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    expected_candidate_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    operations: list[Annotated[ReplaceRecipeInputs | ConfirmRecipeProtocol, Field(discriminator="op")]] = Field(min_length=1, max_length=25)


class RecoveryPreviewRequest(GraphPatchModel):
    mode: Literal["recovery"]
    artifact_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    patch: GraphPatchEnvelopeV1 | RecipeControlFlowPatchV1 | RecipeResourceBindingsPatchV1 | RecipeInputsPatchV1 | RecipeEditsPatchV1


class RecoveryApplyRequest(RecoveryPreviewRequest):
    preview_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)


def needs_recovery(proposal) -> bool:
    if requires_recipe_recovery(proposal):
        return True
    report = proposal.payload.get("meta_planner_report")
    return (
        proposal.source_type == "meta_planner" and isinstance(report, dict)
        and not report.get("human_modified")
        and isinstance(report.get("validation"), dict)
        and report["validation"].get("valid") is False
    )


def _messages(validation):
    return [{
        "code": str(item.get("code") or "recovery_validation") if isinstance(item, dict) else "recovery_validation",
        "severity": "error",
        "message": safe_headless_error_message(item.get("message") or item) if isinstance(item, dict) else safe_headless_error_message(str(item)),
    } for item in list(validation.get("issues") or [])[:64]]


def _attempt_projection(item):
    """Expose only editor diagnostics, never arbitrary persisted attempt fields."""
    stage = item.get("stage") if item.get("stage") in _PHASES_BY_STAGE else "capability_compile"
    phases = set(_PHASES_BY_STAGE[stage]) | {"recipe_lowering"}
    detail = item.get("diagnostics") or {}
    issues = []
    for issue in list(detail.get("issues") or [])[:64]:
        if not isinstance(issue, dict):
            continue
        safe = {
            "code": issue.get("code") if issue.get("code") in _CODES else "CONTRACT_CHECK_FAILED",
            "category": issue.get("category") if issue.get("category") in _CATEGORIES else "compatibility",
        }
        ref = issue.get("node_ref")
        if isinstance(ref, str) and _TOKEN.fullmatch(ref) and not _PRIVATE_TOKEN.search(ref):
            safe["node_ref"] = ref
        if isinstance(issue.get("location"), list):
            safe["location"] = _safe_location(issue["location"])
        recipe_detail = issue.get("recipe_detail")
        if isinstance(recipe_detail, dict):
            safe["recipe_detail"] = {key: _safe_location(recipe_detail[key]) for key in ("location", "first_location") if isinstance(recipe_detail.get(key), list)}
        schema_detail = issue.get("schema_detail")
        if isinstance(schema_detail, dict):
            safe["schema_detail"] = {
                key: value for key, values in {
                    "expected": {"array", "protocol_v1"}, "actual": {"null", "missing", "invalid"},
                    "input_mode": {"explicit"},
                }.items() if isinstance((value := schema_detail.get(key)), str) and value in values
            }
        issues.append(safe)
    result = {
        "attempt_id": item.get("attempt_id") if item.get("attempt_id") in (1, 2) else 0,
        "stage": stage,
        "diagnostic_subject": item.get("diagnostic_subject") if item.get("diagnostic_subject") in {"repair_input", "intent_result", "recipe_result", "unparsed_response"} else "unknown",
        "diagnostics": {"stage": stage, "issues": issues, "phase_results": [{
            "id": phase["id"], "status": phase["status"],
        } for phase in list(detail.get("phase_results") or [])[:16]
          if isinstance(phase, dict) and phase.get("id") in phases
          and phase.get("status") in {"passed", "failed", "blocked"}]},
    }
    for key in ("intent_checksum", "result_intent_checksum", "recipe_checksum", "diagnostics_checksum"):
        value = item.get(key)
        result[key] = value if isinstance(value, str) and re.fullmatch(GRAPH_PATCH_CHECKSUM_PATTERN, value) else None
    progress = detail.get("recipe_repair_progress")
    if isinstance(progress, dict) and progress.get("assessment") in {"not_rechecked", "blocking_issues_persist", "requires_full_validation"}:
        pending_phases = progress.get("not_rechecked_phases")
        result["diagnostics"]["recipe_repair_progress"] = {
            "assessment": progress["assessment"],
            "not_rechecked_phases": [phase for phase in pending_phases[:16] if isinstance(phase, str) and phase in _CATEGORIES] if isinstance(pending_phases, list) else [],
            **{key: value if type(value) is int and 0 <= value <= 10000 else None
               for key in ("persisting_issue_count", "new_issue_count", "no_longer_observed_issue_count")
               for value in [progress.get(key)]},
        }
    return result


def _state_binding(state, artifact):
    return {
        "proposal_id": state.proposal.proposal_id, "proposal_revision": state.proposal.revision,
        "artifact": artifact["checksum"], "plan": state.plan.model_dump(mode="json"),
        "scope": state.scope.model_dump(mode="json"),
        "snapshot": state.snapshot.model_dump(mode="json", exclude={"generated_at"}),
        "request": state.request.model_dump(mode="json"),
        "target_id": state.proposal.target_id,
        "target_revision": state.target.draft_revision if state.target else None,
        "before_candidate": state.candidate_checksum,
    }


class FailedDraftRecovery:
    def __init__(self, headless):
        self.headless = headless

    def _state(self, proposal_id):
        proposal = self.headless.authoring_service.proposal_store.require(proposal_id)
        if not needs_recovery(proposal):
            raise HeadlessAuthoringConflictError("提案已不处于失败修复状态，请重新加载。")
        try:
            artifact = load_failed_generation_artifact(proposal)
        except FailedArtifactUnavailable as exc:
            raise HeadlessAuthoringError(str(exc), code="recovery_artifact_unavailable") from exc
        selected = next((item for item in reversed(artifact["attempts"]) if item.get("intent") is not None), None)
        if selected is None:
            selected = next((item for item in reversed(artifact["attempts"]) if item.get("recipe") is not None), None)
        if selected is None:
            raise HeadlessAuthoringError("原始图未能安全保留，不能用占位图替代。", code="recovery_intent_not_retained")
        report = _report_from_proposal(proposal)
        try:
            stored_scope = MetaPlannerScope.model_validate(report.get("authorized_scope"))
            snapshot = self.headless.capability_snapshot_builder()
            scope = _intersect_scope(stored_scope, snapshot)
            is_recipe = selected.get("intent") is None
            source_key = "recipe" if is_recipe else "intent"
            if is_recipe and selected.get("recipe_format") == "recipe_resource_draft_v1":
                intent = parse_recipe_resource_draft(
                    selected[source_key],
                    allowed_resource_ids=generation_resource_ids(SimpleNamespace(scope=scope), snapshot),
                )
                if intent is None:
                    raise ValueError("资源描述草稿不符合受限保留契约。")
            elif is_recipe and selected.get("recipe_format") == "recipe_input_draft_v1":
                intent = parse_recipe_input_draft(
                    selected[source_key],
                    allowed_resource_ids=generation_resource_ids(SimpleNamespace(scope=scope), snapshot),
                )
                if intent is None:
                    raise ValueError("输入描述草稿不符合受限保留契约。")
            else:
                if is_recipe and selected.get("recipe_format", "recipe_v1") != "recipe_v1":
                    raise ValueError("未知的生成描述保留格式。")
                intent = (GenerationRecipeV1 if is_recipe else GraphIntentV3).model_validate(selected[source_key])
            retained, status = (_retained_recipe if is_recipe else _retained_intent)(intent)
            if status != "retained" or retained != selected[source_key]:
                raise ValueError("失败图不再符合安全保留契约。")
            if canonical_checksum(retained) != selected[source_key + "_checksum"]:
                raise ValueError("失败图 checksum 不一致。")
            plan = MetaPlannerTaskPlan.model_validate(report.get("plan"))
            generation = report.get("generation_config") or {}
            request = MetaPlannerGenerateRequest(
                goal=str(report.get("goal") or "人工修复失败候选"),
                mode="update" if proposal.kind == "xpert_update" else "create",
                target_xpert_id=proposal.target_id,
                planner_model_id=str(generation.get("planner_model_id") or "manual-recovery"),
                default_agent_model_id=_default_agent_model(intent, report),
                vision_model_id=generation.get("vision_model_id"),
                max_agents=int(generation.get("max_agents") or 1), temperature=0, scope=scope,
            )
        except ValueError as exc:
            raise HeadlessAuthoringError(safe_headless_error_message(exc), code="recovery_contract_invalid") from exc
        target = None
        if proposal.kind == "xpert_update":
            try:
                target = self.headless.authoring_service.xpert_store.get_xpert(proposal.target_id)
            except Exception as exc:
                raise HeadlessAuthoringConflictError("目标智能体不可用，修复应用已阻断。") from exc
        candidate = _candidate_from_proposal(proposal)
        checksum = candidate_authoring_checksum(candidate)
        if checksum != artifact["binding"].get("candidate_checksum"):
            raise HeadlessAuthoringConflictError("实际候选已变化，不能恢复旧失败图。")
        warnings = []
        if report.get("capability_snapshot", {}).get("hash") != snapshot.snapshot_hash:
            warnings.append("能力快照已变化，预览将按原授权范围重新核对当前契约与资源。")
        state = _ProposalState(
            proposal=proposal, candidate=candidate, report=report, plan=plan, intent=intent,
            graph_ir=None, snapshot=snapshot, scope=scope, request=request, target=target,
            layout={}, graph_checksum=canonical_checksum(retained), candidate_checksum=checksum,
            ir_state="failed_recoverable", compatibility=MetaPlannerIRCompatibility(source_version=3),
            warnings=warnings, target_conflict=target is not None and target.draft_revision != proposal.base_revision,
        )
        return state, artifact, selected

    def _validate(self, state, intent):
        diagnostics = GenerationDiagnostics("capability_compile")
        if isinstance(intent, (RecipeResourceDraftV1, RecipeInputDraftV1)):
            diagnostics.enter("intent_parse")
            try:
                intent = GenerationRecipeV1.model_validate(intent.model_dump(mode="python"))
            except ValueError as exc:
                diagnostics.exception(exc)
                message = "输入描述尚未通过生成契约，请核对协议版本和节点输入引用。" if isinstance(intent, RecipeInputDraftV1) else "资源描述尚未通过生成契约，请修正 Agent 资源绑定。"
                return {}, None, {"valid": False, "issues": _messages({"issues": [message]})}, diagnostics.as_dict()
        try:
            assert_scope_is_authorized(state.scope, state.snapshot)
            _validate_intent_authorization(intent, state.scope)
            validate_vision_generation_authorization(state.request, state.snapshot, state.target)
        except ValueError as exc:
            diagnostics.enter("authorization")
            if isinstance(intent, GraphIntentV3):
                diagnostics.bind_graph(intent)
            diagnostics.exception(exc)
            return {}, None, {"valid": False, "issues": _messages({"issues": [str(exc)]})}, diagnostics.as_dict()
        # The same compiler and publish preflight are used, without generation,
        # output normalization, fallback synthesis, or a model repair attempt.
        blueprint, candidate, validation, issues, graph_ir, _ = self.headless.planner_service._compile_and_validate(
            request=state.request, plan=state.plan, raw_blueprint=intent.model_dump_json(),
            snapshot=state.snapshot, target=state.target, diagnostics=diagnostics,
            require_recipe=isinstance(intent, GenerationRecipeV1),
        )
        if not issues and validation.get("valid"):
            try:
                strict = self.headless.planner_service.preview(
                    state.request, state.snapshot, plan=state.plan, blueprint=blueprint, target=state.target,
                )
                candidate, validation = strict.candidate, strict.validation
            except ValueError as exc:
                diagnostics.exception(exc)
                candidate, graph_ir = {}, None
                validation = {"valid": False, "issues": _messages({"issues": [str(exc)]})}
        return candidate, graph_ir, validation, diagnostics.as_dict()

    def state_payload(self, proposal_id):
        try:
            state, artifact, selected = self._state(proposal_id)
        except HeadlessAuthoringError as exc:
            return {
                "version": HEADLESS_AUTHORING_VERSION, "mode": "recovery", "proposal_id": proposal_id,
                "can_author": False, "can_approve": False,
                "recovery": {"status": "unavailable", "reason": str(exc), "code": exc.code, "executable": False},
                "diagnostics": [{"code": exc.code, "severity": "error", "message": str(exc)}],
            }
        _, _, validation, diagnosis = self._validate(state, state.intent)
        can_edit = state.proposal.status == "pending" and not state.target_conflict
        return {
            "version": HEADLESS_AUTHORING_VERSION, "authoring_protocol_version": 1, "mode": "recovery",
            "proposal_id": proposal_id, "proposal_revision": state.proposal.revision,
            "proposal_status": state.proposal.status, "ir_version": 3, "ir_state": state.ir_state,
            "graph_checksum": state.graph_checksum, "candidate_checksum": state.candidate_checksum,
            "can_author": can_edit, "can_edit": can_edit, "can_approve": False,
            "allowed_node_kinds": state.scope.allowed_node_kinds,
            "authorized_scope": state.scope.model_dump(mode="json"),
            "compiler_managed_node_kinds": ["input", "output"],
            "warnings": state.warnings + (["目标草稿版本已变化，禁止应用。"] if state.target_conflict else []),
            "diagnostics": _messages(validation), "validation": validation,
            "recovery": {
                "status": "editable" if can_edit else "read_only", "executable": False,
                "artifact_checksum": artifact["checksum"], "selected_attempt_id": selected["attempt_id"],
                "source_format": recipe_source_format(state.intent) if isinstance(state.intent, GenerationRecipeV1) else "graph_intent_v3",
                "recipe": deepcopy(selected.get("recipe")) if isinstance(state.intent, GenerationRecipeV1) else None,
                "intent": deepcopy(selected.get("intent")), "plan": state.plan.model_dump(mode="json"),
                "semantic_repair": {
                    "protocol_version": "recipe_edits_v1",
                    "edit_contract": recipe_edit_contract(state.intent, state.request, state.snapshot),
                    "operations_schema": recipe_edit_schema(state.intent, state.request, state.snapshot),
                } if type(state.intent) is GenerationRecipeV1 else None,
                "node_contracts": {kind: {
                    "config_fields": list(adapter.config_model.model_fields),
                    "input_ports": [port.name for port in adapter.intent_port_contracts("input")],
                    "output_ports": [port.name for port in adapter.intent_port_contracts("output")],
                    "input_mode": recipe_input_contract(kind),
                } for kind in sorted({node.kind for node in state.intent.nodes})
                  if (adapter := get_planner_node_adapter(kind)) is not None},
                "current_diagnostics": diagnosis,
                "attempts": [_attempt_projection(item) for item in artifact["attempts"]],
            },
        }

    def _preview(self, state, artifact, request):
        patch = request.patch
        self.headless._check_revision(state, patch.proposal_revision)
        if state.target_conflict:
            raise HeadlessAuthoringConflictError("目标草稿已变化，请重新加载。")
        if request.artifact_checksum != artifact["checksum"] or patch.expected_graph_checksum != state.graph_checksum or patch.expected_candidate_checksum != state.candidate_checksum:
            raise HeadlessAuthoringConflictError("失败产物或候选已变化，请重新加载后预览。")
        candidate, graph_ir, layout = {}, None, {}
        patched = None
        try:
            if isinstance(state.intent, RecipeInputDraftV1):
                if not isinstance(patch, RecipeInputsPatchV1):
                    raise ValueError("输入描述草稿只能修复输入引用或显式确认协议，不能修改配置、资源或控制结构。")
                payload = state.intent.model_dump(mode="python")
                nodes = {node["ref"]: node for node in payload["nodes"]}
                seen = set()
                confirmed = False
                for operation in patch.operations:
                    if isinstance(operation, ConfirmRecipeProtocol):
                        if confirmed or payload["generation_protocol_version"] is not None:
                            raise ValueError("协议版本只能在缺失时显式确认一次。")
                        confirmed = True
                        payload["generation_protocol_version"] = 1
                    else:
                        if operation.node_ref in seen or operation.node_ref not in nodes:
                            raise ValueError("输入修复包含重复或不存在的节点引用。")
                        seen.add(operation.node_ref)
                        nodes[operation.node_ref]["inputs"] = None if operation.inputs is None else [item.model_dump(mode="python") for item in operation.inputs]
                patched = GenerationRecipeV1.model_validate(payload)
                _, status = _retained_recipe(patched)
            elif isinstance(state.intent, RecipeResourceDraftV1):
                if not isinstance(patch, RecipeResourceBindingsPatchV1):
                    raise ValueError("资源描述草稿只能替换 Agent 资源绑定，不能修改节点、控制结构或授权。")
                payload = state.intent.model_dump(mode="python")
                payload["resources"] = [item.model_dump(mode="python") for item in patch.operations[0].resources]
                patched = GenerationRecipeV1.model_validate(payload)
                _, status = _retained_recipe(patched)
            elif isinstance(state.intent, GenerationRecipeV1):
                if isinstance(patch, RecipeEditsPatchV1):
                    patched = apply_recipe_edits(state.intent, {"operations": [
                        op.model_dump(mode="json", exclude_unset=True) for op in patch.operations
                    ]}, state.request, state.snapshot)
                elif isinstance(patch, RecipeControlFlowPatchV1):
                    patched = state.intent.model_copy(deep=True, update={"control_flow": patch.operations[0].control_flow})
                else:
                    raise ValueError("尚未生成 Intent，必须使用受限 Recipe 语义修复，不能应用 Graph Patch。")
                _, status = _retained_recipe(patched)
            else:
                if not isinstance(patch, GraphPatchEnvelopeV1):
                    raise ValueError("已解析的 Intent 必须使用 Graph Patch，不能替换生成描述。")
                result = apply_graph_patch(
                    state.intent, patch, plan_task_ids={task.task_id for task in state.plan.tasks},
                    allowed_node_kinds=set(state.scope.allowed_node_kinds),
                )
                patched, layout = result.intent, result.layout
                _, status = _retained_intent(patched)
            if status != "retained":
                raise ValueError("修复内容包含不允许保留的字段、秘密或超限内容。")
            candidate, graph_ir, validation, diagnosis = self._validate(state, patched)
        except ValueError as exc:
            observer = GenerationDiagnostics("recipe_edits_v1" if isinstance(patch, RecipeEditsPatchV1) else "graph_patch_v1")
            observer.enter("patch_apply")
            if isinstance(state.intent, GraphIntentV3):
                observer.bind_graph(state.intent)
            observer.exception(exc)
            diagnosis = observer.as_dict()
            validation = {"valid": False, "issues": _messages({"issues": [safe_headless_error_message(exc)]})}
        if candidate:
            _apply_layout(candidate, layout)
        graph_payload = graph_ir.model_dump(mode="json") if graph_ir else None
        graph_checksum = graph_authoring_checksum(graph_ir) if graph_ir else ""
        candidate_checksum = candidate_authoring_checksum(candidate) if candidate else ""
        graph_changed = patched is not None and canonical_checksum(patched.model_dump(mode="json")) != state.graph_checksum
        can_apply = bool(graph_changed and candidate and graph_ir and validation.get("valid"))
        diagnostics = _messages(validation)
        if not graph_changed:
            diagnostics.append({"code": "no_effect", "severity": "info", "message": "未改变失败原图，不能标记为人工修复。"})
        # Bind safe snapshot content, not just its supplied hash or timestamps.
        binding = {
            **_state_binding(state, artifact), "patch": graph_patch_checksum(patch),
            "after_graph": graph_checksum, "after_candidate": candidate_checksum,
            "validation": validation, "diagnosis": diagnosis,
        }
        return {
            "version": HEADLESS_AUTHORING_VERSION, "mode": "recovery",
            "proposal_id": state.proposal.proposal_id, "proposal_revision": state.proposal.revision,
            "preview_checksum": canonical_checksum(binding), "can_apply": can_apply,
            "candidate": candidate if can_apply else None, "graph_ir": graph_payload,
            "graph_checksum": graph_checksum, "candidate_checksum": candidate_checksum,
            "validation": validation, "diagnostics": diagnostics,
            "recovery_diagnostics": diagnosis, "warnings": state.warnings,
            "diff": {"operation_count": len(patch.operations), "operations": patch.model_dump(mode="json", exclude_unset=True)["operations"],
                     "source": "retained_recipe" if isinstance(state.intent, GenerationRecipeV1) else "retained_intent", "graph_changed": graph_changed},
        }

    def preview(self, proposal_id, request):
        state, artifact, _ = self._state(proposal_id)
        return self._preview(state, artifact, request)

    def apply(self, proposal_id, request):
        state, artifact, _ = self._state(proposal_id)
        preview = self._preview(state, artifact, request)
        if preview["preview_checksum"] != request.preview_checksum:
            raise HeadlessAuthoringConflictError("预览依据已变化，请重新校验，不会覆盖另一窗口修改。")
        if not preview["can_apply"]:
            raise HeadlessAuthoringError("修复尚未通过全部门禁，不能写回候选。", diagnostics=preview["diagnostics"])
        expected_binding = canonical_checksum(_state_binding(state, artifact))

        def before_commit():
            # Runs after final validation under the Authoring/target guards.
            # Resource Stores remain separate; approval/runtime revalidate too.
            try:
                latest, latest_artifact, _ = self._state(proposal_id)
                current = canonical_checksum(_state_binding(latest, latest_artifact))
            except (HeadlessAuthoringError, ValueError) as exc:
                raise AuthoringProposalConflictError("提交前修复依据已失效，请重新加载。") from exc
            if current != expected_binding:
                raise AuthoringProposalConflictError("最终校验期间契约或资源已变化，请重新预览。")

        return self.headless._apply_preview(state, request.patch, preview, before_commit=before_commit)
