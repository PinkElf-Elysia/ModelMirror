import type { AgencyPlanPreview } from "./AgencyExpertTeamTypes";

const STORAGE_KEY = "modelmirror-expert-team-agency-draft-v1";
const STORAGE_VERSION = 1;
const MAX_STORED_CHARS = 2 * 1024 * 1024;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export interface AgencyPlanDraft {
  goal: string;
  preview: AgencyPlanPreview | null;
  validation_stale: boolean;
  loaded_plan: AgencyPlanPreview | null;
  loaded_goal: string;
  loaded_invalid: boolean;
  planner_model_id: string;
  agent_model_id: string;
  execution_model_id: string;
  team_name: string;
}

interface StoredAgencyPlanDraft extends AgencyPlanDraft {
  version: number;
  saved_at: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isAgencyPlanPreview(value: unknown): value is AgencyPlanPreview {
  if (!isRecord(value)) return false;
  const plan = value.plan;
  const workflow = value.workflow;
  const validation = value.validation;
  return (
    isRecord(plan) &&
    Array.isArray(plan.tasks) &&
    isRecord(workflow) &&
    Array.isArray(workflow.nodes) &&
    Array.isArray(workflow.edges) &&
    isRecord(validation) &&
    typeof validation.valid === "boolean" &&
    isRecord(value.candidate) &&
    Array.isArray(value.selected_agents) &&
    typeof value.capability_snapshot_version === "string" &&
    typeof value.capability_snapshot_hash === "string" &&
    typeof value.upstream_revision === "string"
  );
}

function optionalPreview(value: unknown): value is AgencyPlanPreview | null {
  return value === null || isAgencyPlanPreview(value);
}

function isBoundedString(value: unknown, maxLength: number): value is string {
  return typeof value === "string" && value.length <= maxLength;
}

export function readAgencyPlanDraft(
  storage: DraftStorage | null =
    typeof window === "undefined" ? null : window.sessionStorage,
  now = Date.now(),
): AgencyPlanDraft | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw || raw.length > MAX_STORED_CHARS) {
      if (raw) storage.removeItem(STORAGE_KEY);
      return null;
    }
    const value = JSON.parse(raw) as unknown;
    if (
      !isRecord(value) ||
      value.version !== STORAGE_VERSION ||
      typeof value.saved_at !== "number" ||
      value.saved_at > now + 60_000 ||
      now - value.saved_at > MAX_AGE_MS ||
      !isBoundedString(value.goal, 20_000) ||
      !optionalPreview(value.preview) ||
      typeof value.validation_stale !== "boolean" ||
      !optionalPreview(value.loaded_plan) ||
      !isBoundedString(value.loaded_goal, 20_000) ||
      typeof value.loaded_invalid !== "boolean" ||
      !isBoundedString(value.planner_model_id, 300) ||
      !isBoundedString(value.agent_model_id, 300) ||
      !isBoundedString(value.execution_model_id, 300) ||
      !isBoundedString(value.team_name, 200)
    ) {
      storage.removeItem(STORAGE_KEY);
      return null;
    }
    const stored = value as unknown as StoredAgencyPlanDraft;
    return {
      goal: stored.goal,
      preview: stored.preview,
      validation_stale: stored.validation_stale,
      loaded_plan: stored.loaded_plan,
      loaded_goal: stored.loaded_goal,
      loaded_invalid: stored.loaded_invalid,
      planner_model_id: stored.planner_model_id,
      agent_model_id: stored.agent_model_id,
      execution_model_id: stored.execution_model_id,
      team_name: stored.team_name,
    };
  } catch {
    storage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function writeAgencyPlanDraft(
  draft: AgencyPlanDraft,
  storage: DraftStorage | null =
    typeof window === "undefined" ? null : window.sessionStorage,
  now = Date.now(),
): boolean {
  if (!storage) return false;
  if (!draft.preview && !draft.loaded_plan) {
    storage.removeItem(STORAGE_KEY);
    return true;
  }
  try {
    const raw = JSON.stringify({
      version: STORAGE_VERSION,
      saved_at: now,
      ...draft,
    } satisfies StoredAgencyPlanDraft);
    if (raw.length > MAX_STORED_CHARS) return false;
    storage.setItem(STORAGE_KEY, raw);
    return true;
  } catch {
    return false;
  }
}
