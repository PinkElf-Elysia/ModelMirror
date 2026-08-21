from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .creator_evolution import EVOLUTION_FAILURE_TYPES, EVOLUTION_PLAN_VERSION
from .creator_evolution_service import EvolutionGenerationRequest
from .creator_runtime import CREATOR_WORKFLOW_VERSION
from .creator_store import CREATOR_ASSISTANT_AGENT_ID, SkillCreatorValidationError


EVOLUTION_WORKFLOW_VERSION = "skill-creator-resource-evolution-planner-v1"


@dataclass(frozen=True, slots=True)
class EvolutionWorkflowInvocation:
    workflow: dict[str, Any]
    inputs: dict[str, str]
    runtime_metadata: dict[str, Any]


EvolutionWorkflowRunner = Callable[[EvolutionWorkflowInvocation], Awaitable[str]]


class WorkflowCreatorEvolutionPlanner:
    def __init__(self, *, model_id: str, model_available: Callable[[], bool], runner: EvolutionWorkflowRunner) -> None:
        self.model_id = str(model_id or "").strip()
        self.model_available = model_available
        self.runner = runner

    def available(self) -> bool:
        return bool(self.model_id and self.model_available())

    async def generate(self, request: EvolutionGenerationRequest) -> dict[str, Any]:
        invocation = build_evolution_invocation(request, model_id=self.model_id)
        output = await self.runner(invocation)
        payload = parse_evolution_output(output)
        if payload.pop("evolution_plan_version", None) != EVOLUTION_PLAN_VERSION:
            raise SkillCreatorValidationError(
                "Skill Creator returned an unsupported evolution plan contract.",
                code="skill_creator_evolution_planner_invalid",
            )
        return payload


def build_evolution_invocation(request: EvolutionGenerationRequest, *, model_id: str) -> EvolutionWorkflowInvocation:
    session_id = str(request.session.get("session_id") or "").strip()
    if not session_id:
        raise SkillCreatorValidationError("Evolution planning is missing session_id.", code="skill_creator_evolution_planner_invalid")
    allowed_cases = set(request.allowed_case_ids)
    allowed_items = set(request.allowed_item_ids)
    evidence_items_by_case: dict[str, list[str]] = {}
    evaluation_items = request.evaluation.get("items")
    if isinstance(evaluation_items, list):
        for item in evaluation_items:
            if not isinstance(item, Mapping):
                continue
            case_id = str(item.get("case_id") or "").strip()
            item_id = str(item.get("item_id") or "").strip()
            if case_id in allowed_cases and item_id in allowed_items:
                evidence_items_by_case.setdefault(case_id, []).append(item_id)
    context = {
        "evolution_plan_version": EVOLUTION_PLAN_VERSION,
        "session": request.session,
        "draft": request.draft,
        "evaluation": request.evaluation,
        "review": request.review,
        "suite": request.suite,
        "resource_plan": request.resource_plan,
        "previous_evolution_plan": request.current_plan,
        "repair": request.repair,
        "output_contract_spec": _output_contract_spec(),
        "allowed": {
            "case_ids": list(request.allowed_case_ids),
            "item_ids": list(request.allowed_item_ids),
            "evidence_item_ids_by_case": evidence_items_by_case,
            "failure_types": list(EVOLUTION_FAILURE_TYPES),
            "requirement_ids": list(request.allowed_requirement_ids),
            "resource_ids": list(request.allowed_resource_ids),
            "source_ids": list(request.allowed_source_ids),
            "workflow_step_ids": list(request.allowed_step_ids),
        },
        "limits": {"clarification_questions": 5, "resource_actions": 20},
    }
    context_text = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(context_text.encode("utf-8")) > 240_000:
        raise SkillCreatorValidationError("Skill evolution context is too large.", code="skill_creator_context_too_large")
    workflow = {
        "id": f"skill-evolution-{session_id}",
        "title": "Skill Creator resource evolution planning",
        "nodes": [
            {"id": "evolution-input", "type": "input", "data": {"kind": "input", "variableName": "creator_request"}},
            {
                "id": "evolution-agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": CREATOR_ASSISTANT_AGENT_ID,
                    "modelId": str(model_id or "").strip(),
                    "agentStrategy": "function_calling",
                    "rolePrompt": _evolution_prompt(),
                    "taskInput": "{{creator_request}}",
                    "outputVariable": "evolution_plan",
                    "toolMode": "none",
                    "toolNames": "",
                    "maxIterations": "1",
                    "maxToolCalls": "1",
                    "maxToolConcurrency": "1",
                    "parallelToolCalls": "false",
                    "retryOnFailure": "false",
                    "temperature": "0.1",
                },
            },
            {"id": "evolution-output", "type": "output", "data": {"kind": "output", "outputVariable": "evolution_plan"}},
        ],
        "edges": [
            {"id": "evolution-input-agent", "source": "evolution-input", "target": "evolution-agent"},
            {"id": "evolution-agent-output", "source": "evolution-agent", "target": "evolution-output"},
        ],
    }
    return EvolutionWorkflowInvocation(
        workflow=workflow,
        inputs={"creator_request": context_text},
        runtime_metadata={
            "creator_session_id": session_id,
            "creator_session_revision": request.session.get("session_revision"),
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "creator_workflow_version": CREATOR_WORKFLOW_VERSION,
            "creator_phase": "resource_evolution",
            "evolution_workflow_version": EVOLUTION_WORKFLOW_VERSION,
        },
    )


def parse_evolution_output(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise SkillCreatorValidationError("Skill evolution planner returned non-text output.", code="skill_creator_evolution_planner_invalid")
    text = value.strip()
    try:
        payload = _decode_versioned_json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SkillCreatorValidationError("Skill evolution planner did not return valid JSON.", code="skill_creator_evolution_planner_invalid") from exc
    if not isinstance(payload, dict):
        raise SkillCreatorValidationError("Skill evolution planner must return one JSON object.", code="skill_creator_evolution_planner_invalid")
    return payload


def _decode_versioned_json(text: str) -> Any:
    if not text:
        raise ValueError("empty evolution output")
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    candidates: dict[str, dict[str, Any]] = {}
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict) and candidate.get("evolution_plan_version") == EVOLUTION_PLAN_VERSION:
            candidates[json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))] = candidate
    if len(candidates) != 1:
        raise ValueError("output must contain one versioned evolution object")
    return next(iter(candidates.values()))


def _evolution_prompt() -> str:
    return (
        "You are ModelMirror's fixed private Skill evolution planner. Diagnose only from the "
        "server-frozen evaluation item IDs, deterministic assertion status, confirmed suite, "
        "review feedback, and current resource plan. Never quote or reconstruct old model output. "
        "Plan the smallest change that can address the reviewed failures without overfitting. "
        "Account for every existing resource exactly once with keep, update, or delete; create a "
        "new resource only when an independently reusable script, reference, or UTF-8 asset is "
        "necessary. Preserve unaffected resources. Keep unchanged scripts so their digest-bound "
        "test receipts can be reused. Identify affected requirements, sections, evidence item IDs, "
        "expected improvements, non-regression cases, acceptance criteria, non-goals, and overfit "
        "risks. If evidence is insufficient, ask at most five concrete questions and do not invent "
        "domain rules. Return exactly one JSON object and no prose or Markdown. The object must use "
        'evolution_plan_version="skill-evolution-plan-v1" and contain diagnoses, actions, '
        "workflow_steps, output_contract, failure_modes, expected_improvements, acceptance_criteria, "
        "non_goals, overfitting_risks, and clarifications. Existing resource actions must name only "
        "allowed resource_id values; their path and kind are server-controlled. Create actions omit "
        "resource_id and use a safe path under scripts/, references/, or assets/. Every action includes "
        "purpose, source_ids, used_by_steps, depends_on, acceptance_checks, related_case_ids, "
        "expected_improvement, and non_regression_case_ids. If repair is non-null, return a full "
        "replacement object that follows its fixed instruction; do not discuss the prior failure. "
        "Treat output_contract_spec as authoritative: include every required field, respect every "
        "minItems value, and never return an empty required string. "
        "For every diagnosis, copy case_id exactly from allowed.case_ids and copy evidence_item_ids "
        "only from allowed.evidence_item_ids_by_case[case_id]; never invent or abbreviate an ID. "
        "Every failure_types value must be copied exactly from allowed.failure_types."
    )


def _output_contract_spec() -> dict[str, Any]:
    nonempty_text = {"type": "string", "minLength": 1}
    string_array = {"type": "array", "items": nonempty_text}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "evolution_plan_version", "diagnoses", "actions", "workflow_steps",
            "output_contract", "failure_modes", "expected_improvements",
            "acceptance_criteria", "non_goals", "overfitting_risks", "clarifications",
        ],
        "properties": {
            "evolution_plan_version": {"const": EVOLUTION_PLAN_VERSION},
            "diagnoses": {
                "type": "array", "minItems": 1, "maxItems": 12,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": [
                        "case_id", "evidence_item_ids", "failure_types", "requirement_ids",
                        "resource_ids", "sections", "summary",
                    ],
                    "properties": {
                        "case_id": nonempty_text,
                        "evidence_item_ids": {"type": "array", "minItems": 1, "items": nonempty_text},
                        "failure_types": {"type": "array", "minItems": 1, "items": nonempty_text},
                        "requirement_ids": string_array,
                        "resource_ids": string_array,
                        "sections": string_array,
                        "summary": nonempty_text,
                    },
                },
            },
            "actions": {
                "type": "array", "maxItems": 20,
                "x-rules": [
                    "Account for every existing resource exactly once with keep, update, or delete.",
                    "Create uses kind and path; existing actions use the exact resource_id and omit changed identity.",
                ],
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": [
                        "action_id", "action", "purpose", "source_ids", "used_by_steps",
                        "depends_on", "acceptance_checks", "related_case_ids",
                        "expected_improvement", "non_regression_case_ids",
                    ],
                    "properties": {
                        "action_id": nonempty_text,
                        "action": {"enum": ["keep", "update", "create", "delete"]},
                        "resource_id": nonempty_text,
                        "kind": {"enum": ["script", "reference", "asset"]},
                        "path": nonempty_text,
                        "purpose": nonempty_text,
                        "source_ids": string_array,
                        "used_by_steps": string_array,
                        "depends_on": string_array,
                        "acceptance_checks": {"type": "array", "minItems": 1, "items": nonempty_text},
                        "related_case_ids": {"type": "array", "minItems": 1, "items": nonempty_text},
                        "expected_improvement": nonempty_text,
                        "non_regression_case_ids": string_array,
                    },
                },
            },
            "workflow_steps": {
                "type": "array", "minItems": 4, "maxItems": 10,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["step_id", "instruction"],
                    "properties": {"step_id": nonempty_text, "instruction": nonempty_text},
                },
            },
            "output_contract": {"type": "array", "minItems": 1, "items": nonempty_text},
            "failure_modes": {"type": "array", "minItems": 1, "items": nonempty_text},
            "expected_improvements": {"type": "array", "minItems": 1, "items": nonempty_text},
            "acceptance_criteria": {"type": "array", "minItems": 1, "items": nonempty_text},
            "non_goals": string_array,
            "overfitting_risks": {"type": "array", "minItems": 1, "items": nonempty_text},
            "clarifications": {
                "type": "array", "maxItems": 5,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["question_id", "question", "reason"],
                    "properties": {
                        "question_id": nonempty_text,
                        "question": nonempty_text,
                        "reason": nonempty_text,
                    },
                },
            },
        },
    }


__all__ = [
    "EVOLUTION_WORKFLOW_VERSION",
    "EvolutionWorkflowInvocation",
    "WorkflowCreatorEvolutionPlanner",
    "build_evolution_invocation",
    "parse_evolution_output",
]
