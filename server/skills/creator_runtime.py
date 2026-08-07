from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .creator_evidence import CreatorEvidenceError, build_creator_evidence_preview
from .creator_quality import (
    CREATOR_CONTRACT_VERSION,
    build_session_requirements,
    load_creator_authoring_playbook,
)
from .creator_service import (
    CreatorEvidencePreview,
    CreatorGenerationRequest,
    CreatorGenerationResult,
    CreatorSourceDescriptor,
)
from .creator_store import (
    CREATOR_ASSISTANT_AGENT_ID,
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)

try:
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
    from server.xpert_runtime.execution_store import WorkflowExecutionStore
    from server.xperts.context import XpertContextStore
except ModuleNotFoundError:
    from xpert_runtime.authoring_store import AuthoringProposalStore
    from xpert_runtime.execution_store import WorkflowExecutionStore
    from xperts.context import XpertContextStore


CREATOR_WORKFLOW_VERSION = "skill-creator-workflow-v1"
CREATOR_GENERATED_PACKAGE_MAX_BYTES = 24_000
CREATOR_GENERATED_RESOURCE_LIMIT = 6
_ALLOWED_GENERATION_TOOLS = {
    "skill_authoring_propose_create",
    "skill_authoring_propose_update",
}


@dataclass(frozen=True, slots=True)
class CreatorWorkflowInvocation:
    workflow: dict[str, Any]
    inputs: dict[str, str]
    runtime_metadata: dict[str, Any]


CreatorWorkflowRunner = Callable[[CreatorWorkflowInvocation], Awaitable[None]]


class TrustedCreatorSourceProvider:
    """Adapt completed private runs into bounded, redacted Creator evidence."""

    def __init__(
        self,
        execution_store: WorkflowExecutionStore,
        context_store: XpertContextStore,
    ) -> None:
        self.execution_store = execution_store
        self.context_store = context_store

    @property
    def supported_sources(self) -> tuple[str, ...]:
        return ("xpert_chat", "workflow_classic")

    def validate_source(self, source: CreatorSourceDescriptor) -> None:
        self._build(source)

    def preview(self, session: SkillCreatorSession) -> CreatorEvidencePreview:
        preview = self._build(self._descriptor(session))
        return CreatorEvidencePreview(
            fingerprint=preview.preview_fingerprint,
            candidates=tuple(
                {
                    "candidate_id": item.candidate_id,
                    "kind": item.kind,
                    "title": item.title,
                    "summary": item.summary,
                    "content_hash": item.content_hash,
                    "default_selected": item.default_selected,
                }
                for item in preview.candidates
            ),
        )

    def select(
        self,
        session: SkillCreatorSession,
        *,
        preview_fingerprint: str,
        candidate_ids: list[str],
    ) -> list[dict[str, str]]:
        preview = self._build(self._descriptor(session))
        if preview.preview_fingerprint != str(preview_fingerprint or "").lower():
            raise SkillCreatorConflictError(
                "Creator evidence changed. Refresh the preview before selecting it."
            )
        by_id = {item.candidate_id: item for item in preview.candidates}
        requested = list(
            dict.fromkeys(str(item or "").strip() for item in candidate_ids)
        )
        if any(not item or item not in by_id for item in requested):
            raise SkillCreatorConflictError(
                "Creator evidence selection is stale or contains an unknown candidate."
            )
        return [
            {
                "candidate_id": by_id[item].candidate_id,
                "kind": by_id[item].kind,
                "title": by_id[item].title,
                "summary": by_id[item].summary,
                "content_hash": by_id[item].content_hash,
            }
            for item in requested
        ]

    def _build(self, source: CreatorSourceDescriptor):
        try:
            return build_creator_evidence_preview(
                self.execution_store,
                source_kind=source.source_kind,
                source_task_id=source.source_task_id,
                source_run_id=source.source_run_id,
                context_store=self.context_store,
                source_xpert_id=source.source_xpert_id,
                source_conversation_id=source.source_conversation_id,
                source_message_id=source.source_message_id,
            )
        except CreatorEvidenceError as exc:
            raise SkillCreatorValidationError(str(exc), code=exc.code) from exc

    @staticmethod
    def _descriptor(session: SkillCreatorSession) -> CreatorSourceDescriptor:
        return CreatorSourceDescriptor(
            source_kind=session.source_kind,
            source_task_id=str(session.source_task_id or ""),
            source_run_id=str(session.source_run_id or ""),
            source_xpert_id=session.source_xpert_id,
            source_conversation_id=session.source_conversation_id,
            source_message_id=session.source_message_id,
        )


class WorkflowCreatorGenerationExecutor:
    """Run the fixed private Creator Agent and accept only its bound proposal."""

    def __init__(
        self,
        proposal_store: AuthoringProposalStore,
        *,
        model_id: str,
        model_available: Callable[[], bool],
        runner: CreatorWorkflowRunner,
    ) -> None:
        self.proposal_store = proposal_store
        self.model_id = str(model_id or "").strip()
        self.model_available = model_available
        self.runner = runner

    def available(self) -> bool:
        return bool(self.model_id and self.model_available())

    async def generate(
        self, request: CreatorGenerationRequest
    ) -> CreatorGenerationResult:
        invocation = build_creator_workflow_invocation(request, model_id=self.model_id)
        existing = self._reusable_proposals(request)
        if len(existing) == 1:
            return self._result(existing[0], request.allowed_tool)
        if existing:
            raise SkillCreatorConflictError(
                "Multiple Creator proposals match this frozen session revision."
            )
        await self.runner(invocation)
        proposals = self._reusable_proposals(request)
        if not proposals:
            raise SkillCreatorValidationError(
                "The dedicated Creator Agent completed without submitting its required proposal.",
                code="skill_creator_tool_not_called",
            )
        if len(proposals) != 1:
            raise SkillCreatorConflictError(
                "The Creator Agent submitted more than one proposal for one revision."
            )
        return self._result(proposals[0], request.allowed_tool)

    def _reusable_proposals(self, request: CreatorGenerationRequest):
        session_id = _required_text(request.session.get("session_id"), "session_id")
        session_revision = _positive_int(
            request.session.get("session_revision"), "session_revision"
        )
        expected_kind = (
            "skill_create"
            if request.allowed_tool == "skill_authoring_propose_create"
            else "skill_update"
        )
        scoped = [
            item
            for item in self.proposal_store.list(
                creator_session_id=session_id,
                limit=20,
            )
            if item.status == "pending"
            and item.kind == expected_kind
            and item.creator_session_revision == session_revision
            and item.source_type == "skill_creator"
            and item.source_id == session_id
            and item.actor_kind == "workflow_agent"
        ]
        reusable = [
            item for item in scoped if self._has_trusted_binding(item, request)
        ]
        for item in scoped:
            if item in reusable:
                continue
            self.proposal_store.transition(
                item.proposal_id,
                revision=item.revision,
                status="conflict",
                actor_kind="workflow_agent",
                actor_id=CREATOR_ASSISTANT_AGENT_ID,
                error="Creator proposal does not match the frozen generation target.",
                decision_reason="Rejected invalid Creator generation binding.",
            )
        return reusable

    @staticmethod
    def _has_trusted_binding(item, request: CreatorGenerationRequest) -> bool:
        if (
            item.actor_id != CREATOR_ASSISTANT_AGENT_ID
            or not item.source_run_id
            or not item.source_task_id
            or item.validation.get("valid") is False
        ):
            return False
        if request.allowed_tool == "skill_authoring_propose_create":
            return (
                item.target_id is None
                and item.base_revision is None
                and item.base_digest is None
            )
        target = _generation_target(request.target_draft)
        if target is None:
            return False
        return (
            item.target_id == target["draft_id"]
            and item.base_revision == target["revision"]
            and item.base_digest == target["content_digest"]
        )

    @staticmethod
    def _result(proposal, allowed_tool: str) -> CreatorGenerationResult:
        return CreatorGenerationResult(
            proposal_id=proposal.proposal_id,
            tool_name=allowed_tool,
            runtime_run_id=proposal.source_run_id,
            runtime_task_id=proposal.source_task_id,
        )


def build_creator_workflow_invocation(
    request: CreatorGenerationRequest,
    *,
    model_id: str,
) -> CreatorWorkflowInvocation:
    if request.allowed_tool not in _ALLOWED_GENERATION_TOOLS:
        raise SkillCreatorValidationError(
            "Creator generation requested an unsupported tool.",
            code="skill_creator_tool_not_allowed",
        )
    session_id = _required_text(request.session.get("session_id"), "session_id")
    session_revision = _positive_int(
        request.session.get("session_revision"), "session_revision"
    )
    target = _generation_target(request.target_draft)
    if request.allowed_tool == "skill_authoring_propose_update" and target is None:
        raise SkillCreatorValidationError(
            "Creator update generation requires a frozen target draft.",
            code="skill_creator_target_missing",
        )
    if request.allowed_tool == "skill_authoring_propose_create" and target is not None:
        raise SkillCreatorValidationError(
            "Creator create generation cannot include an update target.",
            code="skill_creator_target_invalid",
        )
    positive_examples = list(request.session.get("positive_examples") or [])
    near_miss_examples = list(request.session.get("near_miss_examples") or [])
    success_criteria = list(request.session.get("success_criteria") or [])
    selected_evidence = list(request.session.get("selected_evidence") or [])
    requirements = [
        item.to_dict()
        for item in build_session_requirements(
            intent=str(request.session.get("intent") or ""),
            positive_examples=positive_examples,
            near_miss_examples=near_miss_examples,
            expected_output=str(request.session.get("expected_output") or ""),
            success_criteria=success_criteria,
        )
    ]
    for evidence in selected_evidence:
        if not isinstance(evidence, dict):
            continue
        candidate_id = str(evidence.get("candidate_id") or "").strip()
        summary = str(evidence.get("summary") or "").strip()
        if candidate_id and summary:
            requirements.append(
                {
                    "requirement_id": f"evidence:{candidate_id}",
                    "kind": "selected_evidence",
                    "text": summary,
                }
            )
    context = {
        "creator_contract_version": CREATOR_CONTRACT_VERSION,
        "operation": "update" if target is not None else "create",
        "definition": {
            "intent": request.session.get("intent") or "",
            "positive_examples": positive_examples,
            "near_miss_examples": near_miss_examples,
            "expected_output": request.session.get("expected_output") or "",
            "success_criteria": success_criteria,
        },
        "requirements": requirements,
        "selected_evidence": selected_evidence,
        "target_draft": target,
        "package_constraints": {
            "skill_markdown_target_characters": [1200, 6000],
            "generated_text_max_bytes": CREATOR_GENERATED_PACKAGE_MAX_BYTES,
            "resource_count_max": CREATOR_GENERATED_RESOURCE_LIMIT,
            "resource_text_max_characters": 6000,
            "allowed_resource_roots": [
                "scripts/",
                "references/",
                "assets/",
                "agents/openai.yaml",
            ],
            "forbidden_package_paths": ["README.md", "evals/"],
        },
        "quality_requirements": {
            "ordered_workflow_steps_min": 4,
            "required_sections": [
                "purpose_and_scope",
                "inputs_and_preconditions",
                "workflow",
                "output_contract",
                "quality_checks",
                "failure_and_degradation",
            ],
            "accepted_heading_contract": {
                "purpose_and_scope": [
                    "Purpose and scope",
                    "用途与边界",
                    "适用范围与边界",
                    "适用场景与边界",
                ],
                "inputs_and_preconditions": [
                    "Inputs and preconditions",
                    "输入与前置条件",
                ],
                "workflow": ["Workflow", "工作流"],
                "output_contract": ["Output contract", "输出约定"],
                "quality_checks": ["Quality checks", "质量检查"],
                "failure_and_degradation": [
                    "Failure and degradation",
                    "失败与降级",
                ],
                "resources": ["Resources", "资源"],
            },
            "description_must_cover": ["capability", "when_to_use", "boundary"],
            "no_placeholders": True,
            "coverage_locations_must_use_exact_markdown_headings": True,
        },
        "resource_rules": {
            "progressive_disclosure": True,
            "create_only_when_materially_useful": True,
            "every_file_requires_plan_and_direct_skill_md_link": True,
            "do_not_invent_domain_rules_without_authoritative_evidence": True,
            "external_dependencies_require_unavailable_fallback": True,
        },
    }
    context_text = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(context_text) > 240_000:
        raise SkillCreatorValidationError(
            "Creator generation context is too large for the fixed model workflow.",
            code="skill_creator_context_too_large",
        )
    is_update = request.allowed_tool == "skill_authoring_propose_update"
    middleware_config = {
        "allow_create": not is_update,
        "allow_update": is_update,
        "allowed_draft_ids": str((target or {}).get("draft_id") or ""),
    }
    workflow = {
        "id": f"skill-creator-{session_id}",
        "title": "Skill Creator generation",
        "nodes": [
            {
                "id": "creator-input",
                "type": "input",
                "data": {"kind": "input", "variableName": "creator_request"},
            },
            {
                "id": "creator-policy",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "skill_creator",
                    "runtimeMiddlewareKind": "runtime_middleware.skill_creator",
                    "middlewarePriority": "30",
                    "runtimeMiddlewareConfig": middleware_config,
                },
            },
            {
                "id": "creator-agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": CREATOR_ASSISTANT_AGENT_ID,
                    "modelId": str(model_id or "").strip(),
                    "agentStrategy": "function_calling",
                    "rolePrompt": _creator_role_prompt(request.allowed_tool),
                    "taskInput": "{{creator_request}}",
                    "outputVariable": "creator_result",
                    "toolMode": "mcp_tools",
                    "toolNames": request.allowed_tool,
                    "maxIterations": "3",
                    "maxToolCalls": "2",
                    "maxToolConcurrency": "1",
                    "parallelToolCalls": "false",
                    "retryOnFailure": "false",
                    "temperature": "0.1",
                },
            },
            {
                "id": "creator-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "creator_result"},
            },
        ],
        "edges": [
            {
                "id": "creator-input-agent",
                "source": "creator-input",
                "target": "creator-agent",
            },
            {
                "id": "creator-policy-agent",
                "source": "creator-policy",
                "target": "creator-agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
            {
                "id": "creator-agent-output",
                "source": "creator-agent",
                "target": "creator-output",
            },
        ],
    }
    return CreatorWorkflowInvocation(
        workflow=workflow,
        inputs={"creator_request": context_text},
        runtime_metadata={
            "creator_session_id": session_id,
            "creator_session_revision": session_revision,
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "creator_workflow_version": CREATOR_WORKFLOW_VERSION,
            "creator_requirement_ids": [
                item["requirement_id"] for item in requirements
            ],
        },
    )


def _generation_target(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        key: value.get(key)
        for key in (
            "draft_id",
            "revision",
            "content_revision",
            "content_digest",
            "name",
            "slug",
            "description",
            "skill_markdown",
            "files",
        )
    }


def _creator_role_prompt(allowed_tool: str) -> str:
    operation = "create a new" if allowed_tool.endswith("create") else "update the existing"
    playbook = load_creator_authoring_playbook()
    return (
        "You are ModelMirror's fixed private Skill Creator assistant. A Skill is a complete, "
        "reusable instruction package, not a paraphrase of the user's request and not a short "
        "prompt. Use only the supplied versioned contract to "
        f"{operation} package.\n\n"
        "Before calling the tool, reason silently through: capability and trigger boundaries; "
        "inputs and preconditions; an ordered executable workflow; a concrete output contract; "
        "quality checks; failure and degradation behavior; and whether deterministic scripts, "
        "references, or text assets materially reduce repeated work. Apply progressive "
        "disclosure: SKILL.md must navigate every resource from the step that uses it. Do not "
        "create resources merely to make the package look larger.\n\n"
        "The typed call must include creator_contract_version, the complete skill package, and "
        "design with workflow_steps, output_contract, failure_modes, resources, assumptions, "
        "and requirement_coverage. Map every supplied requirement_id to an existing Markdown "
        "path and exact section heading. The frontmatter description must say what the Skill "
        "does, when to use it, and the near-miss boundary. The generated package must contain "
        "no TODO, TBD, placeholder, README, evals directory, invented domain facts, credentials, "
        "environment values, absolute local paths, full private conversations, or unselected "
        "evidence. If authoritative material is missing, state assumptions and a fail-closed "
        "degradation path instead of inventing rules.\n\n"
        "Use one accepted level-two heading from quality_requirements.accepted_heading_contract "
        "for every required section. For Chinese packages, prefer exactly: `## 用途与边界`, "
        "`## 输入与前置条件`, `## 工作流`, `## 输出约定`, `## 质量检查`, "
        "`## 失败与降级`, and `## 资源`. Coverage locations must repeat the exact heading text "
        "without the Markdown `##` prefix. Do not replace the scope section with only a title, "
        "introduction, background, or generic capability list.\n\n"
        f"Call only {allowed_tool}. Submit exactly one successful proposal; if the runtime "
        "rejects a first call for a structured completeness error, correct it once. Never call "
        "another tool, never invent repository/runtime identifiers, and never treat a text-only "
        "answer as completion.\n\n"
        "Pinned creation-stage playbook (modified for ModelMirror):\n\n"
        f"{playbook}"
    )


def _required_text(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise SkillCreatorValidationError(
            f"Creator generation is missing {field_name}.",
            code="skill_creator_generation_invalid",
        )
    return clean


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        result = 0
    else:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = 0
    if result < 1:
        raise SkillCreatorValidationError(
            f"Creator generation has an invalid {field_name}.",
            code="skill_creator_generation_invalid",
        )
    return result
