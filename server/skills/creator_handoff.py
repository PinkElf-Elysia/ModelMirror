from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)


SKILL_CREATOR_HANDOFF_VERSION = "skill-creator-middleware-handoff-v1"
SKILL_CREATOR_HANDOFF_ROLE_INSTRUCTION = """
You are preparing a concise requirement analysis for a Skill Creator handoff.
Clarify only the intended use, expected inputs, expected output, boundaries, and
missing information. Do not write SKILL.md, resource files, tool calls,
installation steps, or an authoring proposal. Finish the user's workflow task
normally; ModelMirror will create the Creator session only after the workflow
has completed successfully.
""".strip()

_HANDOFF_ERROR_CODES = {
    "skill_creator_handoff_unavailable",
    "skill_creator_handoff_conflict",
    "skill_creator_handoff_failed",
}


class SkillCreatorHandoffError(RuntimeError):
    def __init__(self, code: str) -> None:
        safe_code = (
            code
            if code in _HANDOFF_ERROR_CODES
            else "skill_creator_handoff_failed"
        )
        super().__init__(safe_code)
        self.code = safe_code


@dataclass(frozen=True, slots=True)
class SkillCreatorHandoffRequest:
    node_id: str
    intent: str


class SkillCreatorHandoffService:
    """Bridge a completed classic workflow to one trusted Creator session."""

    def __init__(
        self,
        creator_service: SkillCreatorService,
        *,
        enabled: bool | None = None,
    ) -> None:
        self.creator_service = creator_service
        self.enabled = (
            os.getenv("SKILL_CREATOR_MIDDLEWARE_V2_ENABLED", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )

    def create_or_get(
        self,
        *,
        task_id: str,
        run_id: str,
        request: SkillCreatorHandoffRequest,
    ) -> SkillCreatorSession:
        if not self.enabled or not self.creator_service.enabled:
            raise SkillCreatorHandoffError("skill_creator_handoff_unavailable")
        clean_task_id = str(task_id or "").strip()
        clean_run_id = str(run_id or "").strip()
        clean_node_id = str(request.node_id or "").strip()
        clean_intent = str(request.intent or "").strip()
        if not clean_task_id or not clean_run_id or not clean_node_id or not clean_intent:
            raise SkillCreatorHandoffError("skill_creator_handoff_failed")
        try:
            return self.creator_service.create_or_get_workflow_handoff(
                source_task_id=clean_task_id,
                source_run_id=clean_run_id,
                intent=clean_intent,
                positive_examples=[clean_intent],
                near_miss_examples=[
                    "与上述目标无关的闲聊、通用改写或其他任务。"
                ],
                expected_output=(
                    "直接完成上述任务；缺少必要信息时明确列出待确认项，不编造事实。"
                ),
                success_criteria=[
                    "结果直接解决用户提出的任务。",
                    "只使用已有信息，缺失内容明确标记为待确认。",
                ],
            )
        except SkillCreatorConflictError as exc:
            raise SkillCreatorHandoffError(
                "skill_creator_handoff_conflict"
            ) from exc
        except SkillCreatorStorageError as exc:
            raise SkillCreatorHandoffError("skill_creator_handoff_failed") from exc
        except SkillCreatorValidationError as exc:
            unavailable_codes = {
                "skill_creator_disabled",
                "skill_creator_source_unavailable",
            }
            code = (
                "skill_creator_handoff_unavailable"
                if exc.code in unavailable_codes
                else "skill_creator_handoff_failed"
            )
            raise SkillCreatorHandoffError(code) from exc
        except Exception as exc:
            raise SkillCreatorHandoffError("skill_creator_handoff_failed") from exc

    @staticmethod
    def ready_event(
        *,
        task_id: str,
        run_id: str,
        node_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        return {
            "event": "skill_creator_handoff",
            "status": "ready",
            "task_id": str(task_id),
            "run_id": str(run_id),
            "node_id": str(node_id),
            "session_id": str(session_id),
        }

    @staticmethod
    def failed_event(
        *,
        task_id: str,
        run_id: str,
        node_id: str,
        error_code: str,
    ) -> dict[str, Any]:
        safe_code = (
            error_code
            if error_code in _HANDOFF_ERROR_CODES
            else "skill_creator_handoff_failed"
        )
        return {
            "event": "skill_creator_handoff",
            "status": "failed",
            "task_id": str(task_id),
            "run_id": str(run_id),
            "node_id": str(node_id),
            "error_code": safe_code,
        }
