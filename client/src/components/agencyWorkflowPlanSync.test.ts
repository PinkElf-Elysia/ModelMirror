import { describe, expect, it } from "vitest";
import type {
  AgencyPlanTask,
  AgencyWorkflow,
} from "./AgencyExpertTeamTypes";
import { syncWorkflowToPlan } from "./agencyWorkflowPlanSync";

function task(
  taskId: string,
  dependsOn: string[] = [],
): AgencyPlanTask {
  return {
    task_id: taskId,
    title: taskId,
    objective: `Complete ${taskId}`,
    depends_on: dependsOn,
    input_contract: [],
    output_contract: `${taskId} output`,
    agent_id: `agent-${taskId}`,
    acceptance: `Verify ${taskId}`,
    method_skill_ids: [],
  };
}

function workflow(): AgencyWorkflow {
  return {
    id: "workflow",
    title: "Typed workflow",
    nodes: [
      { id: "input", type: "input", data: { kind: "input" } },
      {
        id: "node_agent_audience_value_analysis",
        type: "workflow_agent",
        data: {
          kind: "workflow_agent",
          plannerRef: "agent_audience_value_analysis",
          plannerTaskIds: ["audience_value_analysis"],
          outputVariable: "audience_value_output",
        },
      },
      {
        id: "node_agent_implementation_plan",
        type: "workflow_agent",
        data: {
          kind: "workflow_agent",
          plannerRef: "agent_implementation_plan",
          plannerTaskIds: ["implementation_plan"],
          outputVariable: "implementation_output",
        },
      },
      { id: "output", type: "output", data: { kind: "output" } },
    ],
    edges: [],
  };
}

describe("syncWorkflowToPlan", () => {
  it("preserves current compiler node IDs when rebuilding edited dependencies", () => {
    const result = syncWorkflowToPlan(workflow(), [
      task("audience_value_analysis"),
      task("implementation_plan", ["audience_value_analysis"]),
    ]);

    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["input", "node_agent_audience_value_analysis"],
      ["node_agent_audience_value_analysis", "node_agent_implementation_plan"],
      ["node_agent_implementation_plan", "output"],
    ]);
    expect(result.nodes[2].data.taskInput).toContain(
      "{{audience_value_output}}",
    );
  });

  it("keeps strict compatibility with legacy agent task node IDs", () => {
    const legacy = workflow();
    legacy.nodes[1].id = "agent_audience_value_analysis";
    delete legacy.nodes[1].data.plannerTaskIds;
    delete legacy.nodes[1].data.plannerRef;
    legacy.nodes[2].id = "agent_implementation_plan";
    delete legacy.nodes[2].data.plannerTaskIds;
    delete legacy.nodes[2].data.plannerRef;

    const result = syncWorkflowToPlan(legacy, [
      task("audience_value_analysis"),
      task("implementation_plan", ["audience_value_analysis"]),
    ]);

    expect(result.edges[1]).toMatchObject({
      source: "agent_audience_value_analysis",
      target: "agent_implementation_plan",
    });
  });
});
