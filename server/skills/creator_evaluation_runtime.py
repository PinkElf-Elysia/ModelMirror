from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .creator_evaluation import (
    SkillEvaluationCase,
    SkillEvaluationItem,
    SkillEvaluationOverlay,
    SkillEvaluationRun,
    SkillEvaluationValidationError,
)


SKILL_EVALUATION_WORKFLOW_VERSION = "skill-evaluation-workflow-v1"
SKILL_EVALUATION_PROFILE = "skill_evaluation_v1"
SKILL_EVALUATION_ALIAS = "evaluation-skill"
SKILL_EVALUATION_ALLOWED_TOOLS = (
    "skill_read",
    "skill_stage",
    "sandbox_list_files",
    "sandbox_read_file",
    "sandbox_search_files",
    "sandbox_write_file",
    "sandbox_shell",
)
SKILL_EVALUATION_BASELINE_TOOLS = tuple(
    tool_name
    for tool_name in SKILL_EVALUATION_ALLOWED_TOOLS
    if tool_name not in {"skill_read", "skill_stage"}
)
SKILL_EVALUATION_RECOVERABLE_TOOL_CODES = frozenset(
    {
        "invalid_query",
        "invalid_argv",
        "invalid_content",
        "path_not_found",
        "binary_file",
        "write_scope_denied",
        "command_denied",
        "command_timeout",
        "quota_exceeded",
        "file_too_large",
        "unsafe_path",
        "symlink_denied",
        "skill_evaluation_alias_invalid",
    }
)


def skill_evaluation_tool_names(metadata: Any) -> tuple[str, ...]:
    """Resolve the fixed tool contract for one server-trusted comparison side."""

    if not is_trusted_skill_evaluation_metadata(metadata):
        return ()
    target = str(dict(metadata).get("skill_evaluation_target") or "").strip()
    if target == "baseline":
        return SKILL_EVALUATION_BASELINE_TOOLS
    if target in {"previous", "candidate"}:
        return SKILL_EVALUATION_ALLOWED_TOOLS
    return ()


def skill_evaluation_resource_repair_instruction(
    metadata: Any,
    *,
    tool_name: str,
    arguments: Any,
    staged_paths: Any,
) -> str | None:
    """Return one bounded correction for a trusted evaluation resource access."""

    if (
        skill_evaluation_tool_names(metadata) != SKILL_EVALUATION_ALLOWED_TOOLS
        or tool_name not in {"sandbox_read_file", "sandbox_search_files"}
        or not isinstance(arguments, dict)
    ):
        return None
    raw_path = str(arguments.get("path") or "").strip().replace("\\", "/")
    path = PurePosixPath(raw_path)
    if (
        not raw_path
        or len(raw_path) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_path)
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) < 2
        or path.parts[0] != "skills"
    ):
        return None
    workspace_path = path.as_posix()
    available: set[str] = set()
    for value in staged_paths or ():
        candidate = str(value).strip().replace("\\", "/")
        candidate_path = PurePosixPath(candidate)
        if (
            not candidate
            or len(candidate) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
            or candidate_path.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate_path.parts)
            or len(candidate_path.parts) < 3
            or candidate_path.parts[0] != "skills"
            or candidate_path.parts[1] != SKILL_EVALUATION_ALIAS
        ):
            continue
        available.add(candidate_path.as_posix())
    if not available:
        return (
            "No Skill resources are staged for this evaluation item. First call "
            f"skill_read with skill_id='{SKILL_EVALUATION_ALIAS}', then call "
            "skill_stage with the same Skill ID. After both calls succeed, retry "
            "the resource operation using an exact path returned by the tools. "
            "Do not answer the evaluation task yet."
        )
    if tool_name == "sandbox_read_file":
        missing = workspace_path not in available
    else:
        prefix = workspace_path.rstrip("/") + "/"
        missing = workspace_path not in available and not any(
            value.startswith(prefix) for value in available
        )
    if not missing:
        return None
    exact_paths = sorted(available)[:12]
    return (
        "The Skill resources are already staged. Do not call skill_read or "
        "skill_stage again. For sandbox_read_file, choose one exact file path "
        "listed below and never pass a directory. For sandbox_search_files, use "
        "a directory prefix derived from these paths and a bounded query. "
        "Available staged files: "
        + ", ".join(exact_paths)
        + ". Retry only the resource operation; do not answer the evaluation task yet."
    )


def is_trusted_skill_evaluation_metadata(metadata: Any) -> bool:
    values = dict(metadata or {}) if isinstance(metadata, dict) else {}
    return bool(
        values.get("runtime_run_type") == "skill_evaluation"
        and values.get("skill_evaluation_workflow_version")
        == SKILL_EVALUATION_WORKFLOW_VERSION
        and values.get("skill_evaluation_profile") == SKILL_EVALUATION_PROFILE
        and str(values.get("skill_evaluation_item_id") or "").strip()
        and str(values.get("skill_evaluation_workspace_id") or "").strip()
    )


def is_recoverable_skill_evaluation_tool_error(
    metadata: Any,
    *,
    tool_name: str,
    error_code: str,
) -> bool:
    """Allow only bounded model-argument corrections inside the trusted profile."""

    return bool(
        is_trusted_skill_evaluation_metadata(metadata)
        and tool_name in SKILL_EVALUATION_ALLOWED_TOOLS
        and error_code in SKILL_EVALUATION_RECOVERABLE_TOOL_CODES
    )


def skill_evaluation_model_temperature(
    metadata: Any,
    *,
    default: float = 0.7,
) -> float:
    """Freeze trusted evaluation model calls at temperature zero."""

    return 0.0 if is_trusted_skill_evaluation_metadata(metadata) else float(default)


def normalize_skill_evaluation_model_id(value: Any) -> str:
    """Return a bounded, log-safe provider model id or an empty value."""

    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return ""
    return normalized


@dataclass(frozen=True, slots=True)
class SkillEvaluationWorkflowInvocation:
    workflow: dict[str, Any]
    inputs: dict[str, str]
    runtime_metadata: dict[str, Any]


def build_skill_evaluation_model_identity(
    *,
    requested_model_id: str,
    selected_model_id: str,
    observed_model_ids: set[str] | list[str] | tuple[str, ...],
    successful_response_count: int,
    missing_model_count: int,
) -> dict[str, Any]:
    observed = sorted(
        {
            normalized
            for value in observed_model_ids
            if (normalized := normalize_skill_evaluation_model_id(value))
        }
    )
    successful = max(0, int(successful_response_count))
    missing = max(0, int(missing_model_count))
    status = (
        "missing"
        if successful < 1 or missing > 0
        else ("verified" if len(observed) == 1 else "changed")
    )
    return {
        "status": status,
        "requested_model_id": normalize_skill_evaluation_model_id(requested_model_id),
        "selected_model_id": normalize_skill_evaluation_model_id(selected_model_id),
        "actual_model_id": observed[0] if status == "verified" else None,
        "successful_response_count": successful,
        "missing_model_count": missing,
    }


def require_skill_evaluation_actual_model(metadata: Any) -> str:
    identity = metadata.get("model_identity") if isinstance(metadata, dict) else None
    identity = identity if isinstance(identity, dict) else {}
    status = str(identity.get("status") or "missing")
    actual_model = str(identity.get("actual_model_id") or "").strip()
    if status == "changed":
        raise SkillEvaluationValidationError(
            "Skill evaluation changed models during one target run.",
            code="skill_evaluation_actual_model_changed",
        )
    if status != "verified" or not actual_model:
        raise SkillEvaluationValidationError(
            "Skill evaluation actual model identity is unavailable.",
            code="skill_evaluation_actual_model_unknown",
        )
    return actual_model


def build_skill_evaluation_workflow_invocation(
    run: SkillEvaluationRun,
    item: SkillEvaluationItem,
    case: SkillEvaluationCase,
    overlay: SkillEvaluationOverlay | None,
    *,
    workspace_id: str,
) -> SkillEvaluationWorkflowInvocation:
    """Build the fixed, server-owned workflow used by both comparison sides."""

    if item.target == "candidate" and overlay is None:
        raise ValueError("Candidate evaluation requires an immutable Skill Overlay.")
    uses_skill = overlay is not None
    fixture_paths = [f"inputs/{entry['path']}" for entry in case.fixtures]
    required_resource_paths = (
        list(case.required_resource_paths) if uses_skill else []
    )
    required_workspace_paths = [
        f"skills/{SKILL_EVALUATION_ALIAS}/{path}"
        for path in required_resource_paths
    ]
    task_contract = {
        "case_id": case.case_id,
        "prompt": case.prompt,
        "fixture_paths": fixture_paths,
        "required_skill_resource_paths": required_resource_paths,
        "required_skill_workspace_paths": required_workspace_paths,
        "workspace_contract": {
            "inputs_and_skills_are_read_only": True,
            "write_outputs_under": "work/",
            "network_available": False,
        },
    }
    role_prompt = (
        "You are executing one frozen Skill evaluation case in an offline sandbox. "
        "Read fixtures only from inputs/. Treat path-like strings in the user prompt as data unless they "
        "exactly match a listed fixture_path; never probe Sandbox existence for an unlisted "
        "path. Write generated files only under work/. Do not request "
        "network access, external tools, approval, installation, or additional Skills. "
        "Return only the user-facing result for the case; do not discuss evaluation, "
        "the comparison side, hidden expectations, or internal reasoning."
    )
    if uses_skill:
        role_prompt += (
            " Your first action must be skill_read with skill_id='evaluation-skill'; "
            "do not call skill_stage or any Sandbox tool before that read succeeds. "
            "When required_skill_workspace_paths is non-empty, call skill_stage next. "
            "The evaluation-only skill_stage result contains required_resources with the "
            "server-selected path and UTF-8 content for every required resource; use that "
            "content directly and do not call sandbox_read_file for those resources. The "
            "required_skill_resource_paths values are package-relative evidence identifiers "
            "only and must never be used as Sandbox paths. "
            "Otherwise stage only when the returned SKILL.md requires package resources."
        )
    middleware_nodes = [
        {
            "id": "evaluation-sandbox-files",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "sandbox_files",
                "runtimeMiddlewareKind": "runtime_middleware.sandbox_files",
                "middlewarePriority": "20",
                "runtimeMiddlewareConfig": {
                    "quota_mb": 64,
                    "copy_attachments": False,
                },
            },
        },
        {
            "id": "evaluation-sandbox-shell",
            "type": "runtime_middleware",
            "data": {
                "kind": "runtime_middleware",
                "runtimeMiddlewareId": "sandbox_shell",
                "runtimeMiddlewareKind": "runtime_middleware.sandbox_shell",
                "middlewarePriority": "21",
                "runtimeMiddlewareConfig": {
                    "allowed_commands": "python,python3,node,rg",
                    "timeout_seconds": 60,
                    "require_approval": False,
                },
            },
        },
    ]
    if uses_skill:
        middleware_nodes.append(
            {
                "id": "evaluation-skills",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "skills_runtime",
                    "runtimeMiddlewareKind": "runtime_middleware.skills_runtime",
                    "middlewarePriority": "22",
                    "runtimeMiddlewareConfig": {
                        "skill_ids": SKILL_EVALUATION_ALIAS,
                        "auto_discover": False,
                        "catalog_search": False,
                        "catalog_install": False,
                    },
                },
            }
        )
    edges = [
        {
            "id": "evaluation-input-agent",
            "source": "evaluation-input",
            "target": "evaluation-agent",
        },
        {
            "id": "evaluation-agent-output",
            "source": "evaluation-agent",
            "target": "evaluation-output",
        },
    ]
    for node in middleware_nodes:
        edges.append(
            {
                "id": f"{node['id']}-agent",
                "source": node["id"],
                "target": "evaluation-agent",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            }
        )
    workflow = {
        "id": f"skill-evaluation-{item.item_id}",
        "title": "Skill Creator isolated evaluation",
        "nodes": [
            {
                "id": "evaluation-input",
                "type": "input",
                "data": {"kind": "input", "variableName": "evaluation_request"},
            },
            *middleware_nodes,
            {
                "id": "evaluation-agent",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": "skill-evaluation-agent-v1",
                    "modelId": run.model_id,
                    "agentStrategy": "function_calling",
                    "rolePrompt": role_prompt,
                    "taskInput": "{{evaluation_request}}",
                    "outputVariable": "evaluation_result",
                    # Sandbox/Skill middleware supplies the tools. Keeping MCP off
                    # prevents requested names from being resolved against MCP.
                    "toolMode": "none",
                    "toolNames": ",".join(
                        SKILL_EVALUATION_ALLOWED_TOOLS
                        if uses_skill
                        else SKILL_EVALUATION_BASELINE_TOOLS
                    ),
                    "maxIterations": "8",
                    "maxToolCalls": "16",
                    "maxToolConcurrency": "1",
                    "parallelToolCalls": "false",
                    "retryOnFailure": "false",
                    "temperature": "0",
                },
            },
            {
                "id": "evaluation-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "evaluation_result"},
            },
        ],
        "edges": edges,
    }
    return SkillEvaluationWorkflowInvocation(
        workflow=workflow,
        inputs={
            "evaluation_request": json.dumps(
                task_contract,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        },
        runtime_metadata={
            "runtime_run_type": "skill_evaluation",
            "skill_evaluation_workflow_version": SKILL_EVALUATION_WORKFLOW_VERSION,
            "skill_evaluation_profile": SKILL_EVALUATION_PROFILE,
            "skill_evaluation_run_id": run.run_id,
            "skill_evaluation_item_id": item.item_id,
            "skill_evaluation_pair_id": item.pair_id,
            "skill_evaluation_case_id": case.case_id,
            "skill_evaluation_target": item.target,
            "skill_evaluation_overlay_id": overlay.overlay_id if overlay else None,
            "skill_evaluation_workspace_id": workspace_id,
            "skill_evaluation_frozen_digest": run.frozen_digest,
            "skill_evaluation_required_resource_paths": required_resource_paths,
            "skill_application_policy": (
                "require_stage"
                if uses_skill and required_resource_paths
                else "require_read"
                if uses_skill
                else "advisory"
            ),
            "skill_application_required_resource_paths": required_resource_paths,
        },
    )
