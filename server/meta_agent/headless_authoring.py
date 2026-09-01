from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

try:
    from server.workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from server.workflow_native.schemas import NativeWorkflowDefinition
    from server.xpert_runtime.authoring_service import AuthoringService
    from server.xpert_runtime.authoring_store import (
        AuthoringProposal,
        AuthoringProposalConflictError,
        AuthoringProposalNotFoundError,
        AuthoringProposalValidationError,
    )
    from server.xperts.models import XpertDefinition
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from workflow_native.schemas import NativeWorkflowDefinition
    from xpert_runtime.authoring_service import AuthoringService
    from xpert_runtime.authoring_store import (
        AuthoringProposal,
        AuthoringProposalConflictError,
        AuthoringProposalNotFoundError,
        AuthoringProposalValidationError,
    )
    from xperts.models import XpertDefinition

from .graph_ir_v3 import (
    decompile_candidate_to_graph_intent,
    decompile_candidate_to_graph_intent_compat,
    graph_authoring_checksum,
    resolve_graph_intent,
    workflow_authoring_checksum,
    workflow_semantic_checksum,
)
from .graph_patch import (
    GRAPH_PATCH_PROTOCOL_VERSION,
    GraphPatchApplyRequest,
    GraphPatchEditorDiffRequest,
    GraphPatchEnvelopeV1,
    apply_graph_patch,
    diff_graph_intents,
    graph_patch_checksum,
)
from .meta_planner_v2 import MetaPlannerV2Service
from .node_adapters import META_PLANNER_ADAPTER_KINDS, get_planner_node_adapter
from .schemas import (
    GraphIntentInputBindingV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
    MetaPlannerCapabilitySnapshot,
    MetaPlannerGenerateRequest,
    MetaPlannerIRCompatibility,
    MetaPlannerScope,
    MetaPlannerTaskPlan,
    ResolvedGraphIRV3,
)


HEADLESS_AUTHORING_MAX_RECEIPTS = 20
HEADLESS_AUTHORING_VERSION = "meta-planner-headless-authoring-v1"
_AUTHORABLE_WORKFLOW_AGENT_DATA_FIELDS = frozenset(
    {
        "kind",
        "title",
        "description",
        "modelId",
        "rolePrompt",
        "taskInput",
        "sourceAgentId",
        "methodSkillIds",
        "outputVariable",
    }
)
_AUTHORABLE_PURE_NODE_DATA_FIELDS: dict[str, frozenset[str]] = {
    "json_serialize": frozenset(
        {
            "kind",
            "title",
            "description",
            "contractVersion",
            "inputVariable",
            "outputVariable",
            "format",
        }
    ),
    "json_deserialize": frozenset(
        {
            "kind",
            "title",
            "description",
            "contractVersion",
            "inputVariable",
            "outputVariable",
            "expectedSchema",
        }
    ),
    "variable_aggregator": frozenset(
        {
            "kind",
            "title",
            "description",
            "contractVersion",
            "bindings",
            "outputVariable",
        }
    ),
    "data_aggregate": frozenset(
        {
            "kind",
            "title",
            "description",
            "inputVariable",
            "outputVariable",
            "groupByFields",
            "measures",
        }
    ),
    "dataset_compare": frozenset(
        {
            "kind",
            "title",
            "description",
            "leftVariable",
            "rightVariable",
            "outputVariable",
            "keyFields",
            "includeUnchanged",
        }
    ),
}
_SENSITIVE_ERROR_VALUE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"bearer\s+[A-Za-z0-9._-]{8,}|"
    r"(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[^\s,;]+)"
)
_RECEIPT_CHECKSUM = re.compile(r"^[a-f0-9]{64}$")
_RECEIPT_OPERATION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def safe_headless_error_message(error: Exception | str) -> str:
    if isinstance(error, ValidationError):
        parts: list[str] = []
        for item in error.errors(include_input=False, include_url=False)[:20]:
            location = ".".join(str(value) for value in item.get("loc") or [])
            message = str(item.get("msg") or item.get("type") or "Invalid input")
            parts.append(f"{location}: {message}" if location else message)
        raw = "; ".join(parts) or "Request validation failed."
    else:
        raw = str(error)
    return _SENSITIVE_ERROR_VALUE.sub("[REDACTED]", raw)[:1_000]


def _canonical_patch_receipts(value: Any) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for item in list(value or [])[-HEADLESS_AUTHORING_MAX_RECEIPTS:]:
        if not isinstance(item, dict):
            continue
        if item.get("protocol_version") != GRAPH_PATCH_PROTOCOL_VERSION:
            continue
        operation_types = item.get("operation_types")
        if (
            not isinstance(operation_types, list)
            or len(operation_types) > 64
            or any(
                not isinstance(operation, str)
                or not _RECEIPT_OPERATION.fullmatch(operation)
                for operation in operation_types
            )
        ):
            continue
        checksum_fields = (
            "before_graph_checksum",
            "after_graph_checksum",
            "before_candidate_checksum",
            "after_candidate_checksum",
        )
        if any(
            not isinstance(item.get(field_name), str)
            or not _RECEIPT_CHECKSUM.fullmatch(item[field_name])
            for field_name in checksum_fields
        ):
            continue
        raw_counts = item.get("diagnostic_counts")
        if not isinstance(raw_counts, dict):
            continue
        diagnostic_counts = {
            key: count
            for key in ("info", "warning", "error")
            if isinstance((count := raw_counts.get(key)), int)
            and not isinstance(count, bool)
            and 0 <= count <= 10_000
        }
        applied_at = item.get("applied_at")
        if not isinstance(applied_at, (int, float)) or isinstance(
            applied_at, bool
        ):
            continue
        receipts.append(
            {
                "protocol_version": GRAPH_PATCH_PROTOCOL_VERSION,
                "operation_types": list(operation_types),
                **{
                    field_name: item[field_name]
                    for field_name in checksum_fields
                },
                "diagnostic_counts": diagnostic_counts,
                "applied_at": float(applied_at),
            }
        )
    return receipts


class HeadlessAuthoringError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "headless_authoring_validation",
        status_code: int = 422,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        clean_message = safe_headless_error_message(message)
        super().__init__(clean_message)
        self.code = code
        self.status_code = status_code
        self.diagnostics = [
            {
                "code": str(item.get("code") or "headless_authoring_validation"),
                "severity": str(item.get("severity") or "error"),
                "message": safe_headless_error_message(
                    str(item.get("message") or clean_message)
                )[:500],
            }
            for item in list(diagnostics or [])[:20]
            if isinstance(item, dict)
        ]


class HeadlessAuthoringConflictError(HeadlessAuthoringError):
    def __init__(self, message: str, *, code: str = "headless_authoring_conflict"):
        super().__init__(message, code=code, status_code=409)


@dataclass(slots=True)
class _ProposalState:
    proposal: AuthoringProposal
    candidate: dict[str, Any]
    report: dict[str, Any]
    plan: MetaPlannerTaskPlan
    intent: GraphIntentV3
    graph_ir: ResolvedGraphIRV3
    snapshot: MetaPlannerCapabilitySnapshot
    scope: MetaPlannerScope
    request: MetaPlannerGenerateRequest
    target: XpertDefinition | None
    layout: dict[str, dict[str, float]]
    graph_checksum: str
    candidate_checksum: str
    ir_state: str
    compatibility: MetaPlannerIRCompatibility
    warnings: list[str]
    target_conflict: bool


def candidate_authoring_checksum(candidate: dict[str, Any]) -> str:
    """Checksum the compiled candidate including presentation coordinates."""

    return workflow_authoring_checksum(candidate)


def _round_trip_candidate_projection(
    candidate: dict[str, Any], *, source_version: int
) -> dict[str, Any]:
    """Normalize only fields introduced by the lossless V2-to-V3 upgrade."""

    projected = deepcopy(candidate)
    if source_version != 2:
        return projected
    workflow = (projected.get("draft") or {}).get("workflow") or {}
    # V2 and V3 compiler provenance use different workflow version labels.
    # The label is not an execution setting, so exclude it from the behavioral
    # losslessness proof while retaining every runtime-owned node field.
    workflow.pop("version", None)
    for node in workflow.get("nodes") or []:
        data = node.get("data") or {}
        if str(data.get("kind") or node.get("type") or "") != "workflow_agent":
            continue
        data.pop("plannerIRVersion", None)
        data.pop("plannerInputsV3", None)
        data.pop("plannerOutputsV3", None)
    return projected


def _candidate_from_proposal(proposal: AuthoringProposal) -> dict[str, Any]:
    if proposal.kind == "xpert_create":
        return {
            key: deepcopy(value)
            for key, value in proposal.payload.items()
            if key in {"name", "description", "tags", "starters", "draft"}
        }
    if proposal.kind == "xpert_update":
        return deepcopy(dict(proposal.payload.get("patch") or {}))
    raise HeadlessAuthoringError(
        "Headless authoring only supports Xpert proposals.",
        code="headless_authoring_kind",
    )


def _report_from_proposal(proposal: AuthoringProposal) -> dict[str, Any]:
    report = proposal.payload.get("meta_planner_report")
    if proposal.source_type != "meta_planner" or not isinstance(report, dict):
        raise HeadlessAuthoringError(
            "Proposal is not a Meta Planner candidate.",
            code="headless_authoring_source",
        )
    return deepcopy(report)


def _layout_from_candidate(candidate: dict[str, Any]) -> dict[str, dict[str, float]]:
    layout: dict[str, dict[str, float]] = {}
    workflow = dict((candidate.get("draft") or {}).get("workflow") or {})
    for node in workflow.get("nodes") or []:
        data = node.get("data") or {}
        ref = str(data.get("plannerRef") or "").strip()
        if not ref and str(node.get("id") or "") in {"input", "output"}:
            ref = str(node.get("id"))
        position = node.get("position")
        if not ref or not isinstance(position, dict):
            continue
        try:
            layout[ref] = {
                "x": float(position.get("x") or 0),
                "y": float(position.get("y") or 0),
            }
        except (TypeError, ValueError):
            continue
    return layout


def _apply_layout(
    candidate: dict[str, Any], layout: dict[str, dict[str, float]]
) -> None:
    workflow = dict((candidate.get("draft") or {}).get("workflow") or {})
    for node in workflow.get("nodes") or []:
        data = node.get("data") or {}
        ref = str(data.get("plannerRef") or "").strip()
        if not ref and str(node.get("id") or "") in {"input", "output"}:
            ref = str(node.get("id"))
        point = layout.get(ref)
        if point is not None:
            node["position"] = {"x": point["x"], "y": point["y"]}


def _intersect_scope(
    stored_scope: MetaPlannerScope,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> MetaPlannerScope:
    available = {
        "allowed_node_kinds": {str(item.get("kind") or "") for item in snapshot.nodes},
        "external_xpert_ids": {str(item.get("id") or "") for item in snapshot.external_xperts},
        "knowledge_base_ids": {str(item.get("id") or "") for item in snapshot.knowledge_bases},
        "toolset_ids": {str(item.get("id") or "") for item in snapshot.toolsets},
        "plugin_ids": {str(item.get("id") or "") for item in snapshot.plugins},
        "prompt_profile_ids": {str(item.get("id") or "") for item in snapshot.prompt_profiles},
        "middleware_ids": {str(item.get("id") or "") for item in snapshot.middleware},
        "agent_ids": {str(item.get("id") or "") for item in snapshot.agents},
    }
    return MetaPlannerScope(
        **{
            field_name: [
                item
                for item in getattr(stored_scope, field_name)
                if item in available[field_name]
            ]
            for field_name in available
        }
    )


def _default_agent_model(intent: GraphIntentV3, report: dict[str, Any]) -> str:
    generation = report.get("generation_config")
    if isinstance(generation, dict):
        configured = str(generation.get("default_agent_model_id") or "").strip()
        if configured:
            return configured
    for node in intent.nodes:
        configured = str(node.config.get("model_id") or "").strip()
        if configured:
            return configured
    return "deepseek/deepseek-chat"


def _validate_intent_authorization(
    intent: GraphIntentV3,
    scope: MetaPlannerScope,
) -> None:
    allowed_resources = {
        "external_xpert": set(scope.external_xpert_ids),
        "knowledge_base": set(scope.knowledge_base_ids),
        "toolset_resource": set(scope.toolset_ids),
        "plugin_resource": set(scope.plugin_ids),
    }
    violations: list[str] = []
    for node in intent.nodes:
        if node.kind not in set(scope.allowed_node_kinds):
            violations.append(f"node:{node.kind}")
        source_agent_id = str(node.config.get("source_agent_id") or "").strip()
        if source_agent_id and source_agent_id not in set(scope.agent_ids):
            violations.append(f"source_agent:{source_agent_id}")
    for binding in intent.resources:
        if binding.resource_id not in allowed_resources[binding.kind]:
            violations.append(f"resource:{binding.kind}:{binding.resource_id}")
    for binding in intent.middleware:
        if binding.middleware_id not in set(scope.middleware_ids):
            violations.append(f"middleware:{binding.middleware_id}")
    for profile_id in intent.prompt_profile_ids:
        if profile_id not in set(scope.prompt_profile_ids):
            violations.append(f"prompt_profile:{profile_id}")
    if violations:
        raise ValueError(
            "Graph intent references capabilities outside the Proposal authorization "
            "scope: "
            + ", ".join(sorted(set(violations))[:20])
        )


def _candidate_payload(
    proposal: AuthoringProposal,
    candidate: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    if proposal.kind == "xpert_create":
        return {**deepcopy(candidate), "meta_planner_report": report}
    payload = deepcopy(proposal.payload)
    payload["patch"] = deepcopy(candidate)
    payload["meta_planner_report"] = report
    return payload


class HeadlessAuthoringService:
    def __init__(
        self,
        *,
        authoring_service: AuthoringService,
        planner_service: MetaPlannerV2Service,
        capability_snapshot_builder: Callable[[], MetaPlannerCapabilitySnapshot],
    ) -> None:
        self.authoring_service = authoring_service
        self.planner_service = planner_service
        self.capability_snapshot_builder = capability_snapshot_builder

    def proposal_state(self, proposal_id: str) -> dict[str, Any]:
        state = self._state(proposal_id)
        return self._state_payload(state)

    def editor_diff(
        self, proposal_id: str, request: GraphPatchEditorDiffRequest
    ) -> dict[str, Any]:
        state = self._state(proposal_id)
        self._check_revision(state, request.proposal_revision)
        try:
            target_intent, target_layout = self._editor_intent(
                state, request.definition
            )
            patch = diff_graph_intents(
                state.intent,
                target_intent,
                proposal_revision=state.proposal.revision,
                expected_graph_checksum=state.graph_checksum,
                expected_candidate_checksum=state.candidate_checksum,
                source_layout=state.layout,
                target_layout=target_layout,
            )
        except (ValidationError, ValueError) as exc:
            message = safe_headless_error_message(exc)
            raise HeadlessAuthoringError(
                message,
                code="headless_editor_diff_unrepresentable",
                diagnostics=[
                    {
                        "code": "editor_change_unrepresentable",
                        "severity": "error",
                        "message": message[:500],
                    }
                ],
            ) from exc
        return {
            "version": HEADLESS_AUTHORING_VERSION,
            "proposal_id": proposal_id,
            "proposal_revision": state.proposal.revision,
            "patch": patch.model_dump(mode="json"),
            "empty": not patch.operations,
            "diagnostics": [],
        }

    def preview(
        self, proposal_id: str, patch: GraphPatchEnvelopeV1
    ) -> dict[str, Any]:
        state = self._state(proposal_id)
        return self._preview(state, patch)

    def apply(
        self, proposal_id: str, request: GraphPatchApplyRequest
    ) -> dict[str, Any]:
        state = self._state(proposal_id)
        preview = self._preview(state, request.patch)
        if preview["preview_checksum"] != request.preview_checksum:
            raise HeadlessAuthoringConflictError(
                "Preview checksum changed. Re-run preview before applying.",
                code="headless_preview_changed",
            )
        if not preview["can_apply"]:
            raise HeadlessAuthoringError(
                "Graph Patch did not pass the authoring gates.",
                code="headless_patch_not_applicable",
                diagnostics=list(preview.get("diagnostics") or []),
            )

        report = deepcopy(state.report)
        receipts = _canonical_patch_receipts(
            report.get("authoring_patch_receipts")
        )
        receipts.append(
            {
                "protocol_version": GRAPH_PATCH_PROTOCOL_VERSION,
                "operation_types": [
                    operation.op for operation in request.patch.operations
                ],
                "before_graph_checksum": state.graph_checksum,
                "after_graph_checksum": preview["graph_checksum"],
                "before_candidate_checksum": state.candidate_checksum,
                "after_candidate_checksum": preview["candidate_checksum"],
                "diagnostic_counts": dict(
                    Counter(
                        str(item.get("severity") or "info")
                        for item in preview.get("diagnostics") or []
                    )
                ),
                "applied_at": time.time(),
            }
        )
        report.update(
            {
                "planner_version": HEADLESS_AUTHORING_VERSION,
                "ir_version": 3,
                "typed_ir_version": 3,
                "graph_ir": deepcopy(preview["graph_ir"]),
                "graph_ir_checksum": str(
                    (preview["graph_ir"] or {}).get("graph_checksum") or ""
                ),
                "authoring_graph_checksum": preview["graph_checksum"],
                "graph_ir_status": "current",
                "compiled_workflow_checksum": workflow_semantic_checksum(
                    preview["candidate"]
                ),
                "authoring_candidate_checksum": preview["candidate_checksum"],
                "capability_snapshot": {
                    "version": state.snapshot.version,
                    "hash": state.snapshot.snapshot_hash,
                },
                "validation": deepcopy(preview["validation"]),
                "compatibility": {
                    "source_version": state.compatibility.source_version,
                    "upgraded": (
                        state.compatibility.source_version == 2
                        or state.compatibility.upgraded
                    ),
                    "lossy": False,
                    "warnings": list(state.compatibility.warnings),
                },
                "human_modified": True,
                "authoring_patch_receipts": receipts[
                    -HEADLESS_AUTHORING_MAX_RECEIPTS:
                ],
            }
        )
        next_payload = _candidate_payload(
            state.proposal, preview["candidate"], report
        )
        try:
            updated = self.authoring_service.apply_headless_authoring_payload(
                proposal_id,
                revision=request.patch.proposal_revision,
                payload=next_payload,
                expected_target_id=state.proposal.target_id,
                expected_target_revision=state.proposal.base_revision,
            )
        except AuthoringProposalConflictError as exc:
            raise HeadlessAuthoringConflictError(str(exc)) from exc
        except AuthoringProposalValidationError as exc:
            raise HeadlessAuthoringError(str(exc)) from exc
        return {
            "version": HEADLESS_AUTHORING_VERSION,
            "proposal_id": updated.proposal_id,
            "proposal_revision": updated.revision,
            "status": updated.status,
            "validation": deepcopy(updated.validation),
            "graph_checksum": preview["graph_checksum"],
            "candidate_checksum": preview["candidate_checksum"],
            "receipt_count": len(receipts[-HEADLESS_AUTHORING_MAX_RECEIPTS:]),
        }

    def _state(self, proposal_id: str) -> _ProposalState:
        try:
            proposal = self.authoring_service.proposal_store.require(proposal_id)
        except AuthoringProposalNotFoundError as exc:
            raise HeadlessAuthoringError(
                str(exc), code="headless_proposal_not_found", status_code=404
            ) from exc
        candidate = _candidate_from_proposal(proposal)
        report = _report_from_proposal(proposal)
        try:
            plan = MetaPlannerTaskPlan.model_validate(report.get("plan"))
        except ValidationError as exc:
            raise HeadlessAuthoringError(
                "Proposal has no recoverable fixed task plan.",
                code="headless_plan_unavailable",
            ) from exc
        compatibility = report.get("compatibility")
        if isinstance(compatibility, dict) and compatibility.get("lossy") is True:
            raise HeadlessAuthoringError(
                "Lossy V2/V3 conversion cannot enter typed headless authoring.",
                code="headless_lossy_conversion",
            )
        try:
            intent, recovered_compatibility = (
                decompile_candidate_to_graph_intent_compat(candidate)
            )
        except Exception as exc:
            message = safe_headless_error_message(exc)
            raise HeadlessAuthoringError(
                "Candidate cannot be losslessly decompiled to GraphIntent V3.",
                code="headless_lossy_conversion",
                diagnostics=[
                    {
                        "code": "lossy_conversion",
                        "severity": "error",
                        "message": message[:500],
                    }
                ],
            ) from exc
        if intent is None:
            raise HeadlessAuthoringError(
                "Candidate cannot be losslessly upgraded to GraphIntent V3.",
                code="headless_lossy_conversion",
                diagnostics=[
                    {
                        "code": "lossy_conversion",
                        "severity": "error",
                        "message": warning[:500],
                    }
                    for warning in recovered_compatibility.warnings[:20]
                ],
            )

        snapshot = self.capability_snapshot_builder()
        try:
            stored_scope = MetaPlannerScope.model_validate(
                report.get("authorized_scope") or {}
            )
        except ValidationError as exc:
            raise HeadlessAuthoringError(
                "Proposal has no valid original authorization scope.",
                code="headless_scope_unavailable",
            ) from exc
        scope = _intersect_scope(stored_scope, snapshot)
        try:
            _validate_intent_authorization(intent, scope)
        except ValueError as exc:
            message = safe_headless_error_message(exc)
            raise HeadlessAuthoringError(
                message,
                code="headless_contract_or_resource_drift",
                diagnostics=[
                    {
                        "code": "authorization_scope_drift",
                        "severity": "error",
                        "message": message[:500],
                    }
                ],
            ) from exc
        default_model = _default_agent_model(intent, report)
        target = None
        target_conflict = False
        if proposal.kind == "xpert_update":
            target_id = proposal.target_id or str(proposal.payload.get("xpert_id") or "")
            target = self.authoring_service.xpert_store.get_xpert(target_id)
            target_conflict = proposal.base_revision != target.draft_revision
        generation = report.get("generation_config")
        max_agents = (
            int(generation.get("max_agents") or 0)
            if isinstance(generation, dict)
            else 0
        )
        max_agents = max(max_agents, len(intent.nodes), 1)
        request = MetaPlannerGenerateRequest(
            goal=str(report.get("goal") or "Safely edit the Meta Planner candidate."),
            mode="update" if proposal.kind == "xpert_update" else "create",
            target_xpert_id=proposal.target_id,
            planner_model_id=(
                str(generation.get("planner_model_id") or "headless-authoring")
                if isinstance(generation, dict)
                else "headless-authoring"
            ),
            default_agent_model_id=default_model,
            temperature=0,
            max_agents=min(8, max_agents),
            scope=scope,
        )
        try:
            graph_ir = resolve_graph_intent(
                intent, snapshot, default_agent_model_id=default_model
            )
        except Exception as exc:
            message = safe_headless_error_message(exc)
            raise HeadlessAuthoringError(
                message,
                code="headless_contract_or_resource_drift",
                diagnostics=[
                    {
                        "code": "contract_or_resource_drift",
                        "severity": "error",
                        "message": message[:500],
                    }
                ],
            ) from exc
        if not target_conflict:
            round_trip = self.planner_service.preview(
                request,
                snapshot,
                plan=plan,
                blueprint=intent,
                target=target,
                warnings=[],
            ).candidate
            _apply_layout(round_trip, _layout_from_candidate(candidate))
            current_workflow = candidate.get("draft", {}).get("workflow")
            round_trip_workflow = round_trip.get("draft", {}).get("workflow")
            if isinstance(current_workflow, dict) and isinstance(
                round_trip_workflow, dict
            ):
                # Workflow IDs include the global Snapshot hash. Preserve the
                # current presentation identity so unrelated capability drift
                # cannot turn a lossless candidate into a false mismatch.
                round_trip_workflow["id"] = current_workflow.get("id")
            source_version = recovered_compatibility.source_version
            if candidate_authoring_checksum(
                _round_trip_candidate_projection(
                    round_trip, source_version=source_version
                )
            ) != candidate_authoring_checksum(
                _round_trip_candidate_projection(
                    candidate, source_version=source_version
                )
            ):
                raise HeadlessAuthoringError(
                    "Candidate contains native fields that cannot be represented by "
                    "the typed authoring Adapters.",
                    code="headless_lossy_conversion",
                    diagnostics=[
                        {
                            "code": "lossy_round_trip",
                            "severity": "error",
                            "message": (
                                "The candidate changed during deterministic "
                                "decompile/compile verification."
                            ),
                        }
                    ],
                )
        graph_checksum = graph_authoring_checksum(graph_ir)
        stored_checksum = str(report.get("authoring_graph_checksum") or "")
        if not stored_checksum and report.get("graph_ir"):
            try:
                stored_checksum = graph_authoring_checksum(report["graph_ir"])
            except Exception:
                stored_checksum = ""
        ir_state = "current" if stored_checksum == graph_checksum else "stale_recoverable"
        warnings: list[str] = []
        stored_snapshot = report.get("capability_snapshot")
        if (
            isinstance(stored_snapshot, dict)
            and stored_snapshot.get("hash")
            and stored_snapshot.get("hash") != snapshot.snapshot_hash
        ):
            warnings.append(
                "Capability Snapshot changed; referenced contracts and resources were re-resolved."
            )
        if ir_state == "stale_recoverable":
            warnings.append(
                "Candidate differs from its stored IR but was losslessly recovered."
            )
        if recovered_compatibility.source_version == 2:
            warnings.append(
                "Legacy Graph IR V2 was losslessly recovered and will upgrade on apply."
            )
        if target_conflict:
            warnings.append("Target Xpert draft revision changed after proposal creation.")
        return _ProposalState(
            proposal=proposal,
            candidate=candidate,
            report=report,
            plan=plan,
            intent=intent,
            graph_ir=graph_ir,
            snapshot=snapshot,
            scope=scope,
            request=request,
            target=target,
            layout=_layout_from_candidate(candidate),
            graph_checksum=graph_checksum,
            candidate_checksum=candidate_authoring_checksum(candidate),
            ir_state=ir_state,
            compatibility=recovered_compatibility,
            warnings=warnings,
            target_conflict=target_conflict,
        )

    @staticmethod
    def _check_revision(state: _ProposalState, revision: int) -> None:
        if state.proposal.revision != revision:
            raise HeadlessAuthoringConflictError(
                "Proposal revision changed. Reload before editing."
            )
        if state.proposal.status != "pending":
            raise HeadlessAuthoringConflictError(
                f"Proposal is already {state.proposal.status}."
            )

    def _preview(
        self, state: _ProposalState, patch: GraphPatchEnvelopeV1
    ) -> dict[str, Any]:
        self._check_revision(state, patch.proposal_revision)
        if patch.expected_graph_checksum != state.graph_checksum:
            raise HeadlessAuthoringConflictError(
                "Graph checksum changed. Reload before previewing.",
                code="headless_graph_changed",
            )
        if patch.expected_candidate_checksum != state.candidate_checksum:
            raise HeadlessAuthoringConflictError(
                "Compiled candidate changed. Reload before previewing.",
                code="headless_candidate_changed",
            )
        if state.target_conflict:
            raise HeadlessAuthoringConflictError(
                "Target Xpert draft changed after proposal creation.",
                code="headless_target_changed",
            )
        try:
            patched = apply_graph_patch(
                state.intent,
                patch,
                plan_task_ids={task.task_id for task in state.plan.tasks},
                layout=state.layout,
                allowed_node_kinds=set(state.scope.allowed_node_kinds),
                movable_refs={node.ref for node in state.graph_ir.nodes}
                | {
                    str(getattr(operation, "ref", ""))
                    for operation in patch.operations
                    if operation.op == "add_node"
                },
            )
            _validate_intent_authorization(patched.intent, state.scope)
            preview = self.planner_service.preview(
                state.request,
                state.snapshot,
                plan=state.plan,
                blueprint=patched.intent,
                target=state.target,
                warnings=state.warnings,
            )
        except (ValidationError, ValueError) as exc:
            message = safe_headless_error_message(exc)
            raise HeadlessAuthoringError(
                message,
                code="headless_patch_invalid",
                diagnostics=[
                    {
                        "code": "patch_invalid",
                        "severity": "error",
                        "message": message[:500],
                    }
                ],
            ) from exc
        candidate = deepcopy(preview.candidate)
        _apply_layout(candidate, patched.layout)
        graph_payload = deepcopy(preview.graph_ir or {})
        next_graph_checksum = graph_authoring_checksum(graph_payload)
        next_candidate_checksum = candidate_authoring_checksum(candidate)
        diagnostics = [
            deepcopy(issue)
            for issue in list(preview.validation.get("issues") or [])[:20]
            if isinstance(issue, dict)
        ]
        if not patch.operations:
            diagnostics.append(
                {
                    "code": "empty_patch",
                    "severity": "info",
                    "message": "The editor produced no semantic or layout changes.",
                }
            )
        has_effect = (
            next_graph_checksum != state.graph_checksum
            or next_candidate_checksum != state.candidate_checksum
        )
        if patch.operations and not has_effect:
            diagnostics.append(
                {
                    "code": "no_effect",
                    "severity": "info",
                    "message": "The Graph Patch does not change the candidate.",
                }
            )
        can_apply = (
            bool(preview.validation.get("valid"))
            and bool(patch.operations)
            and has_effect
        )
        checksum_payload = {
            "proposal_id": state.proposal.proposal_id,
            "proposal_revision": state.proposal.revision,
            "ir_version": state.compatibility.source_version,
            "patch_checksum": graph_patch_checksum(patch),
            "before_graph_checksum": state.graph_checksum,
            "before_candidate_checksum": state.candidate_checksum,
            "after_graph_checksum": next_graph_checksum,
            "after_candidate_checksum": next_candidate_checksum,
            "validation": preview.validation,
        }
        return {
            "version": HEADLESS_AUTHORING_VERSION,
            "proposal_id": state.proposal.proposal_id,
            "proposal_revision": state.proposal.revision,
            "patch_checksum": graph_patch_checksum(patch),
            "preview_checksum": canonical_checksum(checksum_payload),
            "can_apply": can_apply,
            "candidate": candidate,
            "graph_ir": graph_payload,
            "graph_checksum": next_graph_checksum,
            "candidate_checksum": next_candidate_checksum,
            "validation": deepcopy(preview.validation),
            "warnings": list(dict.fromkeys([*state.warnings, *preview.warnings])),
            "diagnostics": diagnostics,
            "diff": {
                "operation_count": len(patch.operations),
                "operation_types": dict(
                    Counter(operation.op for operation in patch.operations)
                ),
                "graph_changed": next_graph_checksum != state.graph_checksum,
                "candidate_changed": (
                    next_candidate_checksum != state.candidate_checksum
                ),
            },
        }

    def _state_payload(self, state: _ProposalState) -> dict[str, Any]:
        return {
            "version": HEADLESS_AUTHORING_VERSION,
            "authoring_protocol_version": GRAPH_PATCH_PROTOCOL_VERSION,
            "proposal_id": state.proposal.proposal_id,
            "proposal_revision": state.proposal.revision,
            "ir_version": state.compatibility.source_version,
            "proposal_status": state.proposal.status,
            "kind": state.proposal.kind,
            "target_xpert_id": state.proposal.target_id,
            "base_revision": state.proposal.base_revision,
            "candidate": deepcopy(state.candidate),
            "ir_state": state.ir_state,
            "graph_ir": state.graph_ir.model_dump(mode="json"),
            "graph_checksum": state.graph_checksum,
            "candidate_checksum": state.candidate_checksum,
            "capability_snapshot": {
                "version": state.snapshot.version,
                "hash": state.snapshot.snapshot_hash,
            },
            "authorized_scope": state.scope.model_dump(mode="json"),
            "allowed_node_kinds": list(state.scope.allowed_node_kinds),
            "compiler_managed_node_kinds": ["input", "output"],
            "can_edit": (
                state.proposal.status == "pending" and not state.target_conflict
            ),
            "can_author": (
                state.proposal.status == "pending" and not state.target_conflict
            ),
            "compatibility": state.compatibility.model_dump(mode="json"),
            "diagnostics": [],
            "warnings": list(state.warnings),
            "receipt_count": len(
                _canonical_patch_receipts(
                    state.report.get("authoring_patch_receipts")
                )
            ),
        }

    def _editor_intent(
        self, state: _ProposalState, raw_definition: dict[str, Any]
    ) -> tuple[GraphIntentV3, dict[str, dict[str, float]]]:
        definition = NativeWorkflowDefinition.model_validate(raw_definition)
        current_workflow = NativeWorkflowDefinition.model_validate(
            (state.candidate.get("draft") or {}).get("workflow") or {}
        )
        current_ids = {node.id for node in current_workflow.nodes}
        allowed_adapter_kinds = set(state.scope.allowed_node_kinds) & set(
            META_PLANNER_ADAPTER_KINDS
        )
        allowed_kinds = {
            "input",
            "output",
            "runtime_middleware",
            "external_xpert",
            "knowledge_base",
            "toolset_resource",
            "plugin_resource",
        } | allowed_adapter_kinds
        for node in definition.nodes:
            kind = str((node.data or {}).get("kind") or node.type or "")
            if kind not in allowed_kinds:
                raise ValueError(f"Editor node kind {kind} is not authorized.")
            if node.id not in current_ids and kind not in {
                "runtime_middleware",
                "external_xpert",
                "knowledge_base",
                "toolset_resource",
                "plugin_resource",
            } | allowed_adapter_kinds:
                raise ValueError(f"Compiler-managed node {kind} cannot be added.")
        for managed_id in ("input", "output"):
            if not any(node.id == managed_id for node in definition.nodes):
                raise ValueError(f"Compiler-managed node {managed_id} cannot be removed.")

        self._validate_compiler_managed_editor_nodes(definition, current_workflow)
        self._validate_editor_edges(definition)
        self._validate_editor_adapter_fields(definition, current_workflow)
        self._annotate_editor_adapter_nodes(state, definition)
        self._resolve_editor_resource_versions(state, definition)
        candidate = deepcopy(state.candidate)
        candidate.setdefault("draft", {})["workflow"] = definition.model_dump(
            mode="json"
        )
        target_intent = decompile_candidate_to_graph_intent(candidate)
        self._validate_compiler_managed_editor_edges(definition, target_intent)
        source_resource_keys = {
            (item.kind, item.resource_id, item.target_ref)
            for item in state.intent.resources
        }
        target_resource_keys = {
            (item.kind, item.resource_id, item.target_ref)
            for item in target_intent.resources
        }
        for key in sorted(source_resource_keys & target_resource_keys):
            if key[0] == "knowledge_base":
                continue
            if (
                target_intent._pinned_resource_versions.get(key)
                != state.intent._pinned_resource_versions.get(key)
            ):
                raise ValueError(
                    f"Editor cannot change the pinned version for {key[0]}:{key[1]} "
                    f"on {key[2]}."
                )
        if (
            target_intent._pinned_prompt_profile_versions
            != state.intent._pinned_prompt_profile_versions
        ):
            raise ValueError("Editor cannot change runtime-owned Prompt Profile versions.")
        target_layout = _layout_from_candidate(candidate)
        return target_intent, target_layout

    @staticmethod
    def _validate_editor_adapter_fields(
        definition: NativeWorkflowDefinition,
        current: NativeWorkflowDefinition,
    ) -> None:
        current_by_id = {node.id: node for node in current.nodes}
        for node in definition.nodes:
            data = dict(node.data or {})
            kind = str(data.get("kind") or node.type or "")
            if kind not in META_PLANNER_ADAPTER_KINDS:
                continue
            current_node = current_by_id.get(node.id)
            before = dict(current_node.data or {}) if current_node else None
            if current_node is not None:
                current_kind = str(
                    (current_node.data or {}).get("kind")
                    or current_node.type
                    or ""
                )
                if current_kind != kind:
                    raise ValueError(
                        f"Editor node {node.id} cannot change kind from "
                        f"{current_kind} to {kind}."
                    )
            allowed_fields = (
                _AUTHORABLE_WORKFLOW_AGENT_DATA_FIELDS
                if kind == "workflow_agent"
                else _AUTHORABLE_PURE_NODE_DATA_FIELDS.get(kind, frozenset())
            )
            if before is None:
                unsupported = sorted(set(data) - allowed_fields)
            else:
                unsupported = sorted(
                    key
                    for key in set(before) | set(data)
                    if key not in allowed_fields
                    and before.get(key) != data.get(key)
                )
            if unsupported:
                raise ValueError(
                    f"Editor node {node.id} changes fields outside its "
                    "authoring Adapter: "
                    + ", ".join(unsupported)
                )

    @staticmethod
    def _resolve_editor_resource_versions(
        state: _ProposalState,
        definition: NativeWorkflowDefinition,
    ) -> None:
        resource_lookup = {
            "external_xpert": {
                str(item.get("id") or ""): item
                for item in state.snapshot.external_xperts
            },
            "toolset_resource": {
                str(item.get("id") or ""): item for item in state.snapshot.toolsets
            },
            "plugin_resource": {
                str(item.get("id") or ""): item for item in state.snapshot.plugins
            },
        }
        id_fields = {
            "external_xpert": "xpertId",
            "toolset_resource": "toolsetId",
            "plugin_resource": "pluginId",
        }
        node_by_id = {node.id: node for node in definition.nodes}
        target_ref_by_resource_node: dict[str, str] = {}
        for edge in definition.edges:
            source = node_by_id.get(edge.source)
            target = node_by_id.get(edge.target)
            source_kind = str(
                ((source.data if source else {}) or {}).get("kind")
                or (source.type if source else "")
                or ""
            )
            target_kind = str(
                ((target.data if target else {}) or {}).get("kind")
                or (target.type if target else "")
                or ""
            )
            if source_kind not in id_fields or target_kind != "workflow_agent":
                continue
            target_ref_by_resource_node[source.id] = str(
                (target.data or {}).get("plannerRef") or ""
            ).strip()
        for node in definition.nodes:
            data = node.data or {}
            kind = str(data.get("kind") or node.type or "")
            id_field = id_fields.get(kind)
            if id_field is None:
                continue
            resource_id = str(data.get(id_field) or "").strip()
            if not resource_id:
                raise ValueError(f"Editor {kind} node has no resource ID.")
            target_ref = target_ref_by_resource_node.get(node.id, "")
            if not target_ref:
                raise ValueError(
                    f"Editor {kind}:{resource_id} is not bound to a Workflow Agent."
                )
            key = (kind, resource_id, target_ref)
            expected = state.intent._pinned_resource_versions.get(key)
            if expected is not None:
                try:
                    supplied = int(data.get("pinnedVersion"))
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Editor retained {kind}:{resource_id} without its pinned version."
                    ) from None
                if supplied != expected:
                    raise ValueError(
                        f"Editor cannot change the pinned version for {kind}:{resource_id}."
                    )
            else:
                resource = resource_lookup[kind].get(resource_id)
                published = (resource or {}).get("published_version")
                if not published:
                    raise ValueError(
                        f"Editor resource {kind}:{resource_id} is not an authorized "
                        "published resource."
                    )
                expected = int(published)
            data["versionPolicy"] = "pinned"
            data["pinnedVersion"] = expected

    @staticmethod
    def _validate_compiler_managed_editor_nodes(
        definition: NativeWorkflowDefinition,
        current: NativeWorkflowDefinition,
    ) -> None:
        current_by_id = {node.id: node for node in current.nodes}
        edited_by_id = {node.id: node for node in definition.nodes}
        for node_id in ("input", "output"):
            before = current_by_id.get(node_id)
            after = edited_by_id.get(node_id)
            if before is None or after is None:
                raise ValueError(
                    f"Compiler-managed node {node_id} is missing from the candidate."
                )
            kind = str((after.data or {}).get("kind") or after.type or "")
            if kind != node_id:
                raise ValueError(
                    f"Compiler-managed node {node_id} cannot change its kind."
                )
            before_data = dict(before.data or {})
            after_data = dict(after.data or {})
            if node_id == "output":
                before_data.pop("outputVariable", None)
                after_data.pop("outputVariable", None)
            if before_data != after_data:
                raise ValueError(
                    f"Compiler-managed node {node_id} contains an unexpressible edit."
                )

    @staticmethod
    def _validate_compiler_managed_editor_edges(
        definition: NativeWorkflowDefinition,
        intent: GraphIntentV3,
    ) -> None:
        ref_by_id = {
            node.id: str((node.data or {}).get("plannerRef") or "").strip()
            for node in definition.nodes
            if str((node.data or {}).get("kind") or node.type or "")
            in META_PLANNER_ADAPTER_KINDS
        }
        kind_by_id = {
            node.id: str((node.data or {}).get("kind") or node.type or "")
            for node in definition.nodes
        }
        parents = {node.ref: set() for node in intent.nodes}
        for edge in intent.control_edges:
            parents[edge.target_ref].add(edge.source_ref)
        expected_roots = {ref for ref, values in parents.items() if not values}
        actual_roots: set[str] = set()
        terminal_refs: list[str] = []
        for edge in definition.edges:
            if edge.sourceHandle or edge.targetHandle:
                continue
            if edge.target == "input" or edge.source == "output":
                raise ValueError("Compiler-managed control edges cannot run backwards.")
            if edge.source == "input":
                target_ref = ref_by_id.get(edge.target)
                if not target_ref:
                    raise ValueError(
                        "Compiler-managed input edge must target an Adapter node."
                    )
                actual_roots.add(target_ref)
            if edge.target == "output":
                source_ref = ref_by_id.get(edge.source)
                if not source_ref or kind_by_id.get(edge.source) != "workflow_agent":
                    raise ValueError(
                        "Compiler-managed output edge must source a Workflow Agent."
                    )
                terminal_refs.append(source_ref)
        if actual_roots != expected_roots:
            raise ValueError(
                "Compiler-managed input edges do not match the semantic root nodes."
            )
        if terminal_refs != [intent.final_output.node_ref]:
            raise ValueError(
                "Compiler-managed output edge does not match the semantic final output."
            )

    @staticmethod
    def _validate_editor_edges(definition: NativeWorkflowDefinition) -> None:
        nodes = {node.id: node for node in definition.nodes}
        for edge in definition.edges:
            source = nodes.get(edge.source)
            target = nodes.get(edge.target)
            if source is None or target is None:
                raise ValueError(f"Editor edge {edge.id} has an unknown endpoint.")
            source_kind = str((source.data or {}).get("kind") or source.type or "")
            target_kind = str((target.data or {}).get("kind") or target.type or "")
            if not edge.sourceHandle and not edge.targetHandle:
                continue
            expected = {
                "external_xpert": ("expert-binding", "expert"),
                "knowledge_base": ("knowledge-binding", "knowledge"),
                "toolset_resource": ("toolset-binding", "toolset"),
                "plugin_resource": ("plugin-binding", "plugin"),
                "runtime_middleware": ("middleware-binding", "middleware"),
            }.get(source_kind)
            if (
                expected is None
                or target_kind != "workflow_agent"
                or (edge.sourceHandle, edge.targetHandle) != expected
            ):
                raise ValueError(
                    f"Editor edge {edge.id} attempts to inject an invalid Handle."
                )

    @staticmethod
    def _annotate_editor_adapter_nodes(
        state: _ProposalState, definition: NativeWorkflowDefinition
    ) -> None:
        node_by_id = {node.id: node for node in definition.nodes}
        current_refs = {
            node.id: str((node.data or {}).get("plannerRef") or "")
            for node in definition.nodes
            if str((node.data or {}).get("kind") or node.type or "")
            in META_PLANNER_ADAPTER_KINDS
            and str((node.data or {}).get("plannerRef") or "")
        }
        used_refs = set(current_refs.values())
        ref_by_id: dict[str, str] = {}
        kind_by_id: dict[str, str] = {}
        for node in definition.nodes:
            kind = str((node.data or {}).get("kind") or node.type or "")
            if kind not in META_PLANNER_ADAPTER_KINDS:
                continue
            ref = str((node.data or {}).get("plannerRef") or "").strip()
            if not ref:
                suffix = hashlib.sha256(node.id.encode("utf-8")).hexdigest()[:10]
                prefix = "agent" if kind == "workflow_agent" else "node"
                ref = f"{prefix}_{suffix}"
                while ref in used_refs:
                    suffix = hashlib.sha256((node.id + ref).encode("utf-8")).hexdigest()[:10]
                    ref = f"{prefix}_{suffix}"
            used_refs.add(ref)
            ref_by_id[node.id] = ref
            kind_by_id[node.id] = kind

        control_edges: list[tuple[str, str]] = []
        for edge in definition.edges:
            if edge.sourceHandle or edge.targetHandle:
                continue
            if edge.source in ref_by_id and edge.target in ref_by_id:
                control_edges.append((ref_by_id[edge.source], ref_by_id[edge.target]))
        parents: dict[str, set[str]] = {ref: set() for ref in ref_by_id.values()}
        children: dict[str, set[str]] = {ref: set() for ref in ref_by_id.values()}
        for source_ref, target_ref in control_edges:
            parents[target_ref].add(source_ref)
            children[source_ref].add(target_ref)
        indegree = {ref: len(values) for ref, values in parents.items()}
        queue = deque(sorted(ref for ref, count in indegree.items() if count == 0))
        order: list[str] = []
        while queue:
            ref = queue.popleft()
            order.append(ref)
            for child in sorted(children[ref]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(ref_by_id):
            raise ValueError("Editor Adapter control graph must be acyclic.")
        ancestors: dict[str, set[str]] = {ref: set() for ref in parents}
        for ref in order:
            for parent in parents[ref]:
                ancestors[ref].add(parent)
                ancestors[ref].update(ancestors[parent])

        parsed_by_ref: dict[str, Any] = {}
        output_bindings_by_ref: dict[str, list[GraphIntentOutputBindingV3]] = {}
        output_by_variable: dict[
            str, list[tuple[str, str, WorkflowValueSchema]]
        ] = defaultdict(list)
        for node_id, ref in ref_by_id.items():
            node = node_by_id[node_id]
            data = node.data
            kind = kind_by_id[node_id]
            adapter = get_planner_node_adapter(kind)
            assert adapter is not None
            if kind == "workflow_agent":
                role_prompt = str(data.get("rolePrompt") or "").strip()
                task_input = str(data.get("taskInput") or "").strip()
                if not role_prompt or not task_input:
                    defaults = adapter.default_intent_config()
                    data["rolePrompt"] = role_prompt or defaults["role_prompt"]
                    data["taskInput"] = task_input or defaults["task_input"]
            parsed = adapter.authoring_config_from_native(data)
            output_variables = adapter.editor_output_variables(data, parsed)
            outputs = [
                GraphIntentOutputBindingV3(
                    port=port,
                    variable=variable,
                    value_schema=adapter.authoritative_output_schema(port, parsed),
                )
                for port, variable in output_variables.items()
            ]
            parsed_by_ref[ref] = parsed
            output_bindings_by_ref[ref] = outputs
            for output in outputs:
                output_by_variable[output.variable].append(
                    (ref, output.port, output.value_schema)
                )
        plan_task_ids = {task.task_id for task in state.plan.tasks}
        default_task_id = state.plan.tasks[0].task_id
        for node_id, ref in ref_by_id.items():
            node = node_by_id[node_id]
            data = node.data
            kind = kind_by_id[node_id]
            adapter = get_planner_node_adapter(kind)
            assert adapter is not None
            parsed = parsed_by_ref[ref]
            contract = workflow_node_contract_registry.require(kind)
            raw_task_ids = data.get("plannerTaskIds")
            supplied_task_ids = (
                [str(item) for item in raw_task_ids]
                if isinstance(raw_task_ids, list)
                else []
            )
            unknown_task_ids = sorted(set(supplied_task_ids) - plan_task_ids)
            if unknown_task_ids:
                raise ValueError(
                    f"Editor node {ref} references unknown plan tasks: "
                    + ", ".join(unknown_task_ids)
                )
            if contract.planner.task_binding == "required":
                task_ids = supplied_task_ids or [default_task_id]
            else:
                if supplied_task_ids:
                    raise ValueError(f"Editor node {ref} cannot cover plan tasks.")
                task_ids = []
            inputs: list[GraphIntentInputBindingV3] = []
            for port, variable in adapter.editor_input_variables(data, parsed):
                if variable in {"user_input", "conversation_history"}:
                    source_ref = "input"
                    source_port = variable
                    schema = (
                        WorkflowValueSchema(type="string")
                        if variable == "user_input"
                        else WorkflowValueSchema(
                            type="array", items=WorkflowValueSchema(type="object")
                        )
                    )
                else:
                    candidates = [
                        (candidate_ref, candidate_port, schema)
                        for candidate_ref, candidate_port, schema in output_by_variable.get(
                            variable, []
                        )
                        if candidate_ref in ancestors.get(ref, set())
                    ]
                    if len(candidates) != 1:
                        raise ValueError(
                            f"Editor variable {variable} for {ref} has no unique control-reachable producer."
                        )
                    source_ref, source_port, schema = candidates[0]
                inputs.append(
                    GraphIntentInputBindingV3(
                        port=port,
                        variable=variable,
                        source_ref=source_ref,
                        source_port=source_port,
                        value_schema=schema,
                    )
                )
            port_totals = Counter(item.port for item in inputs)
            port_seen: Counter[str] = Counter()
            legacy_inputs: list[dict[str, Any]] = []
            for item in inputs:
                port_seen[item.port] += 1
                legacy_port = (
                    f"{item.port}_{port_seen[item.port]}"
                    if port_totals[item.port] > 1
                    else item.port
                )
                legacy_inputs.append(
                    {
                        "port": legacy_port,
                        "variable": item.variable,
                        "value_type": item.value_schema.type,
                    }
                )
            outputs = output_bindings_by_ref[ref]
            data.update(
                {
                    "plannerIRVersion": 3,
                    "plannerRef": ref,
                    "plannerTaskIds": task_ids,
                    "plannerContractVersion": NODE_CONTRACT_VERSION,
                    "plannerCompilerChecksum": contract.compiler_checksum,
                    "plannerInputsV3": [item.model_dump(mode="json") for item in inputs],
                    "plannerOutputsV3": [
                        item.model_dump(mode="json") for item in outputs
                    ],
                    "plannerInputs": legacy_inputs,
                    "plannerOutputs": [
                        {
                            "port": item.port,
                            "variable": item.variable,
                            "value_type": item.value_schema.type,
                        }
                        for item in outputs
                    ],
                }
            )
            adapter.validate_authoring_config(parsed.model_dump(mode="json"))
