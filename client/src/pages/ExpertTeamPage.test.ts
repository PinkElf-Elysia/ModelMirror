import { describe, expect, it } from "vitest";
import type { AgencyPlanTask } from "../components/AgencyExpertTeamTypes";
import {
  FUSION_ROUTING_BOUNDARY_COPY,
  fusionReceiptFromEvent,
  recommendedChatModels,
  searchableFusionModels,
  validateAgencyHitlPlan,
  workloadReceiptFromEvent,
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
  it("finds exact managed models beyond the featured prefix and preserves selections", () => {
    const modelId = "qwen/qwen3-8b";
    expect(recommendedChatModels().findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(48);
    expect(searchableFusionModels([], modelId).map((model) => model.id)).toContain(modelId);
    expect(searchableFusionModels([modelId], "")[0]?.id).toBe(modelId);
  });

  it("accepts only a sanitized Fusion provider receipt event", () => {
    const receipt = fusionReceiptFromEvent({
      event: "fusion_end",
      provider_route_receipts: {
        contract_version: "modelmirror-provider-workload-routing-v1",
        entry_id: "fusion",
        routing_mode: "managed_required",
        run_reference: "workrun-fusion",
        status: "passed",
        call_count: 1,
        reason_codes: [],
        calls: [],
        prompt: "must be discarded",
      },
    });
    expect(receipt?.entry_id).toBe("fusion");
    expect(receipt && "prompt" in receipt).toBe(false);
    expect(
      fusionReceiptFromEvent({
        event: "fusion_end",
        provider_route_receipts: { entry_id: "fusion", prompt: "secret" },
      }),
    ).toBeNull();
  });

  it("accepts only the expected Route Agent or Team Chat receipt", () => {
    const event = {
      event: "team_end",
      provider_route_receipts: {
        contract_version: "modelmirror-provider-workload-routing-v1",
        entry_id: "team_chat",
        routing_mode: "managed_required",
        run_reference: "workrun-team",
        status: "passed",
        call_count: 3,
        reason_codes: [],
        calls: [
          {
            call_sequence: 1,
            model_id: "provider/model",
            dispatched: true,
            status: "passed",
          },
        ],
        connection_id: "must-be-discarded",
        prompt: "must-be-discarded",
      },
    };

    const receipt = workloadReceiptFromEvent(event, "team_chat");
    expect(receipt?.entry_id).toBe("team_chat");
    expect(receipt?.call_count).toBe(3);
    expect(receipt && "connection_id" in receipt).toBe(false);
    expect(receipt && "prompt" in receipt).toBe(false);
    expect(workloadReceiptFromEvent(event, "route_agent")).toBeNull();
  });

  it("does not promise an automatic Fusion fallback in shared page copy", () => {
    expect(FUSION_ROUTING_BOUNDARY_COPY).toContain("控制面策略");
    expect(FUSION_ROUTING_BOUNDARY_COPY).not.toContain("自动");
    expect(FUSION_ROUTING_BOUNDARY_COPY).not.toContain("兜底");
  });
});
