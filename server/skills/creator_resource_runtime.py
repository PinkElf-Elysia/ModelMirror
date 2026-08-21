from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .creator_quality import load_creator_authoring_playbook
from .creator_resource_plan import RESOURCE_PLAN_VERSION
from .creator_resource_service import ResourcePlanningRequest
from .creator_runtime import CREATOR_WORKFLOW_VERSION
from .creator_store import (
    CREATOR_ASSISTANT_AGENT_ID,
    SkillCreatorValidationError,
)


RESOURCE_PLANNER_WORKFLOW_VERSION = "skill-creator-resource-planner-v1"


@dataclass(frozen=True, slots=True)
class ResourcePlannerWorkflowInvocation:
    workflow: dict[str, Any]
    inputs: dict[str, str]
    runtime_metadata: dict[str, Any]


ResourcePlannerWorkflowRunner = Callable[
    [ResourcePlannerWorkflowInvocation], Awaitable[str]
]


class WorkflowCreatorResourcePlanner:
    """Run the fixed no-tool Creator planner and parse one strict JSON result."""

    def __init__(
        self,
        *,
        model_id: str,
        model_available: Callable[[], bool],
        runner: ResourcePlannerWorkflowRunner,
    ) -> None:
        self.model_id = str(model_id or "").strip()
        self.model_available = model_available
        self.runner = runner

    def available(self) -> bool:
        return bool(self.model_id and self.model_available())

    async def plan(self, request: ResourcePlanningRequest) -> dict[str, Any]:
        invocation = build_resource_planner_invocation(request, model_id=self.model_id)
        output = await self.runner(invocation)
        payload = parse_resource_plan_output(output)
        if payload.pop("resource_plan_version", None) != RESOURCE_PLAN_VERSION:
            raise SkillCreatorValidationError(
                "Creator resource planner returned an unsupported contract version.",
                code="skill_creator_resource_planner_invalid",
            )
        # A clarification response is intentionally non-committal. Models can still
        # speculate about resources despite the prompt, so discard those suggestions
        # at the model boundary and let the typed plan store validate the questions.
        # The discarded values never reach a Store or an Authoring Proposal.
        if isinstance(payload.get("clarifications"), list) and payload["clarifications"]:
            payload["resources"] = []
        else:
            _constrain_model_source_ids(
                payload,
                allowed_source_ids=set(request.allowed_source_ids),
            )
        return payload


def build_resource_planner_invocation(
    request: ResourcePlanningRequest,
    *,
    model_id: str,
) -> ResourcePlannerWorkflowInvocation:
    session_id = _required_text(request.session.get("session_id"), "session_id")
    session_revision = _positive_int(
        request.session.get("session_revision"), "session_revision"
    )
    context = {
        "resource_plan_version": RESOURCE_PLAN_VERSION,
        "operation": "update" if request.target_draft is not None else "create",
        "definition": {
            "intent": request.session.get("intent") or "",
            "positive_examples": list(request.session.get("positive_examples") or []),
            "near_miss_examples": list(request.session.get("near_miss_examples") or []),
            "expected_output": request.session.get("expected_output") or "",
            "success_criteria": list(request.session.get("success_criteria") or []),
        },
        "selected_evidence": list(request.session.get("selected_evidence") or []),
        "allowed_source_ids": list(request.allowed_source_ids),
        "target_draft": request.target_draft,
        "previous_plan": request.current_plan,
        "planning_limits": {
            "clarification_questions_max": 5,
            "workflow_steps_min": 4,
            "workflow_steps_max": 10,
            "resource_count_max": 20,
            "resource_kinds": ["script", "reference", "asset"],
            "resource_actions": ["keep", "create", "update", "delete"],
            "script_languages": ["python", "javascript"],
            "assets_are_utf8_text_only": True,
            "forbidden_package_paths": ["README.md", "eval/", "evals/", "user-meta"],
        },
    }
    context_text = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(context_text.encode("utf-8")) > 240_000:
        raise SkillCreatorValidationError(
            "Creator resource planning context is too large.",
            code="skill_creator_context_too_large",
        )
    workflow = {
        "id": f"skill-resource-plan-{session_id}",
        "title": "Skill Creator resource planning",
        "nodes": [
            {
                "id": "planner-input",
                "type": "input",
                "data": {"kind": "input", "variableName": "creator_request"},
            },
            {
                "id": "planner-agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": CREATOR_ASSISTANT_AGENT_ID,
                    "modelId": str(model_id or "").strip(),
                    "agentStrategy": "function_calling",
                    "rolePrompt": _resource_planner_prompt(),
                    "taskInput": "{{creator_request}}",
                    "outputVariable": "resource_plan",
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
                "id": "planner-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "resource_plan"},
            },
        ],
        "edges": [
            {
                "id": "planner-input-agent",
                "source": "planner-input",
                "target": "planner-agent",
            },
            {
                "id": "planner-agent-output",
                "source": "planner-agent",
                "target": "planner-output",
            },
        ],
    }
    return ResourcePlannerWorkflowInvocation(
        workflow=workflow,
        inputs={"creator_request": context_text},
        runtime_metadata={
            "creator_session_id": session_id,
            "creator_session_revision": session_revision,
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "creator_workflow_version": CREATOR_WORKFLOW_VERSION,
            "creator_phase": "resource_plan",
            "resource_planner_workflow_version": RESOURCE_PLANNER_WORKFLOW_VERSION,
        },
    )


def parse_resource_plan_output(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise SkillCreatorValidationError(
            "Creator resource planner returned non-text output.",
            code="skill_creator_resource_planner_invalid",
        )
    text = value.strip()
    try:
        payload = _decode_resource_plan_json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SkillCreatorValidationError(
            "Creator resource planner did not return valid JSON.",
            code="skill_creator_resource_planner_invalid",
        ) from exc
    if not isinstance(payload, dict):
        raise SkillCreatorValidationError(
            "Creator resource planner must return one JSON object.",
            code="skill_creator_resource_planner_invalid",
        )
    return payload


def _constrain_model_source_ids(
    payload: dict[str, Any], *, allowed_source_ids: set[str]
) -> None:
    """Keep model-authored provenance inside the server-frozen requirement set.

    Resource source IDs are internal identifiers rather than user content. A model may
    paraphrase or invent one even when the prompt includes the allow-list. Preserve valid
    IDs, discard unknown IDs, and bind an otherwise unbound resource to the session intent.
    Malformed non-list/non-string values remain untouched so the typed Store rejects them.
    """

    resources = payload.get("resources")
    if not isinstance(resources, list):
        return
    fallback = "intent" if "intent" in allowed_source_ids else None
    constrained: list[Any] = []
    for raw in resources:
        if not isinstance(raw, dict):
            constrained.append(raw)
            continue
        source_ids = raw.get("source_ids")
        if not isinstance(source_ids, list) or any(
            not isinstance(source_id, str) for source_id in source_ids
        ):
            constrained.append(raw)
            continue
        retained = list(
            dict.fromkeys(
                source_id.strip()
                for source_id in source_ids
                if source_id.strip() in allowed_source_ids
            )
        )
        if not retained and fallback is not None:
            retained = [fallback]
        constrained.append({**raw, "source_ids": retained})
    payload["resources"] = constrained


def _decode_resource_plan_json(text: str) -> Any:
    """Decode one versioned plan while tolerating harmless model narration.

    The planner contract remains strict JSON. This helper only strips transport-style
    narration or Markdown fences by locating a unique, fully valid JSON object carrying
    the expected contract version. It never repairs JSON syntax or guesses between
    different candidate plans.
    """

    if not text:
        raise ValueError("empty planner output")

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
        if candidate.get("resource_plan_version") != RESOURCE_PLAN_VERSION:
            continue
        fingerprint = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        candidates[fingerprint] = candidate

    if len(candidates) != 1:
        raise ValueError("planner output must contain one versioned JSON object")
    return next(iter(candidates.values()))


def _resource_planner_prompt() -> str:
    playbook = load_creator_authoring_playbook()
    planning_guidance = playbook.split("## 3. Write frontmatter", 1)[0].strip()
    return (
        "You are ModelMirror's fixed private Skill Creator resource planner. Plan before "
        "writing. You have no tools and must not generate SKILL.md or any resource content. "
        "Walk through every positive example from scratch, identify repeated deterministic "
        "operations, detailed knowledge, and output templates, then propose only materially "
        "useful scripts, references, and UTF-8 text assets. A simple Skill may have zero "
        "resources. Never add files to look comprehensive.\n\n"
        "Treat exact row-level normalization, alias mapping, deduplication, stable sorting, "
        "aggregation, or machine-checkable validation as deterministic work: when the same "
        "operation is required across examples, plan one conservative Python or JavaScript "
        "CLI instead of asking the Agent to reproduce it from prose. Keep the governing rule "
        "table in a reference and reusable output boilerplate in an asset when those concerns "
        "are independently useful. Do not create a script for subjective judgment.\n\n"
        "If authoritative knowledge, an output contract, or a critical boundary is missing, "
        "return at most five concise clarifications and an empty resources array. Do not invent "
        "facts. When previous_plan contains clarification_answers, use them as trusted user "
        "answers and produce a revised plan.\n\n"
        "Use the same primary natural language as definition.intent for every human-readable "
        "field, including skill_description, workflow instructions, output contracts, failure "
        "modes, resource purposes, acceptance checks, and clarification questions. Keep only "
        "skill_name and fixed JSON enum values in their required machine-readable form.\n\n"
        "A previous plan is advisory, never authoritative. When the current definition or "
        "target draft differs, re-evaluate every resource from scratch. If the user explicitly "
        "requires a supported resource type for a deterministic or reusable concern, either "
        "include that justified resource or ask a clarification that explains why it cannot "
        "be planned safely; do not silently preserve an incompatible previous plan.\n\n"
        "For updates, account for every target_draft.resource_inventory path with exactly one "
        "keep, update, or delete action. New paths use create. Dependencies are expressed as "
        "resource paths. Source IDs are opaque server tokens: copy only exact values from "
        "allowed_source_ids, and use intent when no narrower source applies. Use only real "
        "workflow step IDs.\n\n"
        "Use the pinned planning guidance below only to reason about requirements and "
        "resource necessity. This phase stops before frontmatter, file content, or a typed "
        "Authoring Proposal is written.\n\nPinned planning guidance:\n\n"
        f"{planning_guidance}\n\n"
        "FINAL PHASE CONTRACT: the user message is input context, not an output schema. "
        "Do not echo operation, definition, selected_evidence, allowed_source_ids, "
        "target_draft, previous_plan, or planning_limits. Return JSON only, without "
        "Markdown fences or commentary, in this exact shape: "
        '{"resource_plan_version":"skill-resource-plan-v1","skill_name":"kebab-case",'
        '"skill_description":"what, when, and boundary","workflow_steps":'
        '[{"id":"collect","instruction":"imperative step"}],"output_contract":'
        '["observable output"],"failure_modes":["fail-closed behavior"],"resources":'
        '[{"kind":"reference","action":"create","path":"references/example.md",'
        '"generation_cost":"medium",'
        '"purpose":"why this must be separate","source_ids":["intent"],'
        '"used_by_steps":["collect"],"depends_on":[],"acceptance_checks":'
        '["observable check"]}],"clarifications":'
        '[{"id":"source_policy","question":"specific question",'
        '"reason":"why planning cannot safely continue"}]}. '
        "Use four to ten workflow steps. Do not include IDs, paths, or sources outside the "
        "supplied contract. The top-level object must contain exactly the planning result "
        "fields shown above; resource_plan_version must be skill-resource-plan-v1."
    )


def _required_text(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise SkillCreatorValidationError(
            f"Creator resource planning is missing {field_name}.",
            code="skill_creator_resource_plan_invalid",
        )
    return clean


def _positive_int(value: Any, field_name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillCreatorValidationError(
            f"Creator resource planning has an invalid {field_name}.",
            code="skill_creator_resource_plan_invalid",
        ) from exc
    if isinstance(value, bool) or result < 1:
        raise SkillCreatorValidationError(
            f"Creator resource planning has an invalid {field_name}.",
            code="skill_creator_resource_plan_invalid",
        )
    return result


__all__ = [
    "RESOURCE_PLANNER_WORKFLOW_VERSION",
    "ResourcePlannerWorkflowInvocation",
    "WorkflowCreatorResourcePlanner",
    "build_resource_planner_invocation",
    "parse_resource_plan_output",
]
