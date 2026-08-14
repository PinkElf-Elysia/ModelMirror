import { beforeEach, describe, expect, it } from "vitest";
import type { AgencyPlanPreview } from "./AgencyExpertTeamTypes";
import {
  readAgencyPlanDraft,
  writeAgencyPlanDraft,
  type AgencyPlanDraft,
} from "./agencyPlanDraftStorage";

function preview(): AgencyPlanPreview {
  const workflow = { id: "workflow", title: "Recovered team", nodes: [], edges: [] };
  return {
    plan: { summary: "test", assumptions: [], tasks: [] },
    candidate: {
      name: "Recovered team",
      description: "Recovery-safe plan",
      draft: { workflow },
    },
    workflow,
    validation: { valid: true, issues: [] },
    selected_agents: [],
    baseline_matches: [],
    warnings: [],
    repair_used: false,
    model_calls: 1,
    usage: { input_tokens: 10, output_tokens: 20 },
    capability_snapshot_version: "v1",
    capability_snapshot_hash: "hash",
    upstream_project: "jnMetaCode/agency-orchestrator",
    upstream_revision: "e3f69fd",
  };
}

function draft(): AgencyPlanDraft {
  return {
    goal: "Create a recovery-safe plan.",
    preview: preview(),
    validation_stale: false,
    loaded_plan: preview(),
    loaded_goal: "Create a recovery-safe plan.",
    loaded_invalid: false,
    planner_model_id: "deepseek/deepseek-v3.2",
    agent_model_id: "deepseek/deepseek-v3.2",
    execution_model_id: "deepseek/deepseek-v3.2",
    team_name: "Recovered team",
  };
}

beforeEach(() => window.sessionStorage.clear());

describe("agency plan draft storage", () => {
  it("restores a generated and loaded plan in the same browser tab", () => {
    expect(writeAgencyPlanDraft(draft(), window.sessionStorage, 1_000)).toBe(true);
    expect(readAgencyPlanDraft(window.sessionStorage, 2_000)).toEqual(draft());
  });

  it("expires stale drafts and removes malformed data", () => {
    expect(writeAgencyPlanDraft(draft(), window.sessionStorage, 1_000)).toBe(true);
    expect(
      readAgencyPlanDraft(window.sessionStorage, 1_000 + 25 * 60 * 60 * 1_000),
    ).toBeNull();

    window.sessionStorage.setItem(
      "modelmirror-expert-team-agency-draft-v1",
      JSON.stringify({ version: 1, saved_at: Date.now(), preview: {} }),
    );
    expect(readAgencyPlanDraft(window.sessionStorage)).toBeNull();
  });

  it("clears the stored draft once neither preview nor loaded plan remains", () => {
    expect(writeAgencyPlanDraft(draft(), window.sessionStorage)).toBe(true);
    expect(
      writeAgencyPlanDraft(
        { ...draft(), preview: null, loaded_plan: null },
        window.sessionStorage,
      ),
    ).toBe(true);
    expect(readAgencyPlanDraft(window.sessionStorage)).toBeNull();
  });
});
