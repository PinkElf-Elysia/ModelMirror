import { describe, expect, it } from "vitest";
import type { AgencyPlanTask } from "../components/AgencyExpertTeamTypes";
import {
  recommendedChatModels,
  validateAgencyHitlPlan,
} from "./ExpertTeamPage";

function expert(
  taskId: string,
  dependsOn: string[] = [],
): AgencyPlanTask {
  return {
    task_id: taskId,
    title: taskId,
    objective: `Complete ${taskId}`,
    depends_on: dependsOn,
    input_contract: dependsOn.length
      ? dependsOn.map((dependency) => `${dependency}_output`)
      : ["user_input"],
    output_contract: `${taskId} output`,
    output_variable: `${taskId}_output`,
    agent_id: `agent-${taskId}`,
    acceptance: "The result is reviewable.",
    method_skill_ids: [],
    task_type: "expert",
  };
}

function human(dependsOn: string[]): AgencyPlanTask {
  return {
    task_id: "audience_input",
    title: "补充受众",
    objective: "等待必要输入",
    depends_on: dependsOn,
    input_contract: dependsOn.map((dependency) => `${dependency}_output`),
    output_contract: "audience_input_output",
    output_variable: "audience_input_output",
    agent_id: null,
    acceptance: "",
    method_skill_ids: [],
    task_type: "human_input",
    interaction_prompt: "请补充目标受众。",
  };
}

describe("ExpertTeamPage HITL plan validation", () => {
  it("accepts a complete human-input barrier before the final expert sink", () => {
    expect(validateAgencyHitlPlan([
      expert("research"),
      human(["research"]),
      expert("final", ["audience_input"]),
    ])).toEqual([]);
  });

  it("rejects parallel interaction branches and a sink without acceptance", () => {
    const final = expert("final", ["research", "audience_input"]);
    final.acceptance = "";
    const issues = validateAgencyHitlPlan([
      expert("research"),
      human([]),
      final,
    ]);
    expect(issues.map((issue) => issue.code)).toContain("agency_hitl_barrier_required");
    expect(issues.map((issue) => issue.code)).toContain(
      "agency_hitl_sink_acceptance_required",
    );
  });
});

describe("ExpertTeamPage model selection", () => {
  it("keeps eligible exact managed models selectable beyond the featured prefix", () => {
    expect(
      recommendedChatModels().some((model) => model.id === "openai/gpt-4o-mini"),
    ).toBe(true);
  });
});
