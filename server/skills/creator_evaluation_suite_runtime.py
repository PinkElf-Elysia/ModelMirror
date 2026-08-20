from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .creator_evaluation_suite import EVALUATION_SUITE_VERSION
from .creator_evaluation_suite_service import EvaluationSuiteGenerationRequest
from .creator_runtime import CREATOR_WORKFLOW_VERSION
from .creator_store import CREATOR_ASSISTANT_AGENT_ID, SkillCreatorValidationError


EVALUATION_SUITE_WORKFLOW_VERSION = "skill-creator-evaluation-suite-planner-v1"


@dataclass(frozen=True, slots=True)
class EvaluationSuiteWorkflowInvocation:
    workflow: dict[str, Any]
    inputs: dict[str, str]
    runtime_metadata: dict[str, Any]


EvaluationSuiteWorkflowRunner = Callable[
    [EvaluationSuiteWorkflowInvocation], Awaitable[str]
]


class WorkflowCreatorEvaluationSuiteGenerator:
    """Generate only the three proposed core cases with a fixed no-tool Agent."""

    def __init__(
        self,
        *,
        model_id: str,
        model_available: Callable[[], bool],
        runner: EvaluationSuiteWorkflowRunner,
    ) -> None:
        self.model_id = str(model_id or "").strip()
        self.model_available = model_available
        self.runner = runner

    def available(self) -> bool:
        return bool(self.model_id and self.model_available())

    async def generate(
        self, request: EvaluationSuiteGenerationRequest
    ) -> dict[str, Any]:
        invocation = build_evaluation_suite_invocation(request, model_id=self.model_id)
        output = await self.runner(invocation)
        payload = parse_evaluation_suite_output(output)
        if payload.pop("evaluation_suite_version", None) != EVALUATION_SUITE_VERSION:
            raise SkillCreatorValidationError(
                "Skill Creator returned an unsupported evaluation suite contract.",
                code="skill_evaluation_suite_generator_invalid",
            )
        return payload


def build_evaluation_suite_invocation(
    request: EvaluationSuiteGenerationRequest,
    *,
    model_id: str,
) -> EvaluationSuiteWorkflowInvocation:
    session_id = _required_text(request.session.get("session_id"), "session_id")
    session_revision = _positive_int(
        request.session.get("session_revision"), "session_revision"
    )
    context = {
        "evaluation_suite_version": EVALUATION_SUITE_VERSION,
        "definition": {
            "intent": request.session.get("intent") or "",
            "positive_examples": list(request.session.get("positive_examples") or []),
            "near_miss_examples": list(
                request.session.get("near_miss_examples") or []
            ),
            "expected_output": request.session.get("expected_output") or "",
            "success_criteria": list(request.session.get("success_criteria") or []),
        },
        "selected_evidence": list(request.session.get("selected_evidence") or []),
        "draft": request.draft,
        "resource_plan": request.resource_plan,
        "allowed_requirement_ids": list(request.allowed_requirement_ids),
        "allowed_resource_paths": list(request.allowed_resource_paths),
        "allowed_workflow_step_ids": list(request.allowed_workflow_step_ids),
        "case_contract": {
            "core_roles": ["normal", "ambiguous", "boundary"],
            "model_may_add_regressions": False,
            "fixtures_are_utf8_text": True,
            "assertion_kinds": [
                "contains",
                "not_contains",
                "exact",
                "json_schema",
                "file_exists",
                "file_sha256",
            ],
        },
    }
    context_text = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(context_text.encode("utf-8")) > 240_000:
        raise SkillCreatorValidationError(
            "Skill Creator evaluation suite context is too large.",
            code="skill_creator_context_too_large",
        )
    workflow = {
        "id": f"skill-evaluation-suite-{session_id}",
        "title": "Skill Creator evaluation suite design",
        "nodes": [
            {
                "id": "suite-input",
                "type": "input",
                "data": {"kind": "input", "variableName": "creator_request"},
            },
            {
                "id": "suite-agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": CREATOR_ASSISTANT_AGENT_ID,
                    "modelId": str(model_id or "").strip(),
                    "agentStrategy": "function_calling",
                    "rolePrompt": _suite_prompt(),
                    "taskInput": "{{creator_request}}",
                    "outputVariable": "evaluation_suite",
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
            {
                "id": "suite-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "evaluation_suite"},
            },
        ],
        "edges": [
            {"id": "suite-input-agent", "source": "suite-input", "target": "suite-agent"},
            {"id": "suite-agent-output", "source": "suite-agent", "target": "suite-output"},
        ],
    }
    return EvaluationSuiteWorkflowInvocation(
        workflow=workflow,
        inputs={"creator_request": context_text},
        runtime_metadata={
            "creator_session_id": session_id,
            "creator_session_revision": session_revision,
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "creator_workflow_version": CREATOR_WORKFLOW_VERSION,
            "creator_phase": "evaluation_suite",
            "evaluation_suite_workflow_version": EVALUATION_SUITE_WORKFLOW_VERSION,
        },
    )


def parse_evaluation_suite_output(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise SkillCreatorValidationError(
            "Skill Creator evaluation suite generator returned non-text output.",
            code="skill_evaluation_suite_generator_invalid",
        )
    text = value.strip()
    try:
        payload = _decode_versioned_json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SkillCreatorValidationError(
            "Skill Creator evaluation suite generator did not return valid JSON.",
            code="skill_evaluation_suite_generator_invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise SkillCreatorValidationError(
            "Skill Creator evaluation suite generator must return one JSON object.",
            code="skill_evaluation_suite_generator_invalid",
        )
    return payload


def _decode_versioned_json(text: str) -> Any:
    if not text:
        raise ValueError("empty evaluation suite output")
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
            candidate, _end = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict):
            continue
        if candidate.get("evaluation_suite_version") != EVALUATION_SUITE_VERSION:
            continue
        fingerprint = json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        candidates[fingerprint] = candidate
    if len(candidates) != 1:
        raise ValueError("output must contain one versioned suite object")
    return next(iter(candidates.values()))


def _suite_prompt() -> str:
    return (
        "You are ModelMirror's fixed private Skill Creator test designer. Produce a reusable "
        "evaluation suite proposal, not an answer to the Skill task. You have no tools. "
        "Design exactly three core cases: normal proves the primary workflow, ambiguous "
        "proves conservative handling of missing or conflicting information, and boundary "
        "proves the stated non-trigger or failure behavior. Never add regression cases; only "
        "a user may add or confirm them. Cases must be realistic, mutually distinct, and "
        "traceable to supplied requirement IDs. Do not invent domain rules or sources. "
        "Reference only allowed resource paths and workflow step IDs. If a case declares a "
        "resource, the runtime will require verified skill_stage evidence for that exact path. "
        "Fixtures are bounded UTF-8 text. Assertions are deterministic evidence for human "
        "review, never an automatic judge. Return JSON only with no Markdown fences or prose: "
        '{"evaluation_suite_version":"skill-evaluation-suite-v2","cases":['
        '{"case_id":"core-normal","role":"normal","name":"Normal task",'
        '"prompt":"real user request","expected_behavior":"observable behavior",'
        '"fixtures":[],"assertions":[],"requirement_ids":["intent"],'
        '"required_resource_paths":[],"workflow_step_ids":["step-id"]},'
        '{"case_id":"core-ambiguous","role":"ambiguous","name":"Ambiguous task",'
        '"prompt":"request with missing information","expected_behavior":"safe behavior",'
        '"fixtures":[],"assertions":[],"requirement_ids":["near_miss:0"],'
        '"required_resource_paths":[],"workflow_step_ids":["step-id"]},'
        '{"case_id":"core-boundary","role":"boundary","name":"Boundary task",'
        '"prompt":"near miss or failure","expected_behavior":"refuse or degrade safely",'
        '"fixtures":[],"assertions":[],"requirement_ids":["expected_output"],'
        '"required_resource_paths":[],"workflow_step_ids":["step-id"]}]}. '
        "The object must contain only evaluation_suite_version and cases."
    )


def _required_text(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise SkillCreatorValidationError(
            f"Evaluation suite generation is missing {field_name}.",
            code="skill_evaluation_suite_generator_invalid",
        )
    return clean


def _positive_int(value: Any, field_name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillCreatorValidationError(
            f"Evaluation suite generation has an invalid {field_name}.",
            code="skill_evaluation_suite_generator_invalid",
        ) from exc
    if isinstance(value, bool) or result < 1:
        raise SkillCreatorValidationError(
            f"Evaluation suite generation has an invalid {field_name}.",
            code="skill_evaluation_suite_generator_invalid",
        )
    return result


__all__ = [
    "EVALUATION_SUITE_WORKFLOW_VERSION",
    "EvaluationSuiteWorkflowInvocation",
    "WorkflowCreatorEvaluationSuiteGenerator",
    "build_evaluation_suite_invocation",
    "parse_evaluation_suite_output",
]
