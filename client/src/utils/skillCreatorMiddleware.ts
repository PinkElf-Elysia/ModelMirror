import type { WorkflowNodeData } from "../types/workflow";

export const SKILL_CREATOR_MIDDLEWARE_ID = "skill_creator";
export const SKILL_CREATOR_HANDOFF_MODE = "creator_handoff";
export const SKILL_CREATOR_LEGACY_MODE = "legacy_proposal";

export function skillCreatorAuthoringMode(
  config: Record<string, unknown> | null | undefined,
) {
  const value = typeof config?.authoring_mode === "string"
    ? config.authoring_mode.trim()
    : "";
  return value === SKILL_CREATOR_HANDOFF_MODE
    ? SKILL_CREATOR_HANDOFF_MODE
    : SKILL_CREATOR_LEGACY_MODE;
}

export function isSkillCreatorMiddleware(data: WorkflowNodeData) {
  return data.kind === "runtime_middleware"
    && data.runtimeMiddlewareId === SKILL_CREATOR_MIDDLEWARE_ID;
}

export function isLegacySkillCreatorMiddleware(data: WorkflowNodeData) {
  return isSkillCreatorMiddleware(data)
    && skillCreatorAuthoringMode(data.runtimeMiddlewareConfig)
      === SKILL_CREATOR_LEGACY_MODE;
}

export function creatorHandoffMiddlewareConfig(): Record<string, unknown> {
  return { authoring_mode: SKILL_CREATOR_HANDOFF_MODE };
}
