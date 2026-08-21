import { describe, expect, it } from "vitest";

import {
  type XpertValidationResult,
  type XpertWorkflowDefinition,
} from "../types/xpert";
import { consolidateXpertValidation } from "./xpertValidation";

function callableWorkflow(): XpertWorkflowDefinition {
  return {
    id: "xpert-draft",
    title: "Draft",
    source: "classic",
    version: "xpert-draft-v1",
    nodes: [
      {
        id: "entry",
        type: "workflow_call_entry",
        data: {
          kind: "workflow_call_entry",
          title: "子流程入口",
          description: "",
          eventVariable: "call_event",
        },
      },
      {
        id: "agent",
        type: "workflow_agent",
        data: {
          kind: "workflow_agent",
          title: "Agent",
          description: "",
          taskInput: "{{user_input}}",
        },
      },
    ],
    edges: [],
  };
}

function noisyResult(): XpertValidationResult {
  return {
    valid: false,
    order: [],
    node_count: 2,
    edge_count: 0,
    issues: [
      {
        code: "missing_workflow_agent_template_variable",
        message: "workflow_agent taskInput references undefined variable 'user_input'.",
        severity: "error",
        node_id: "agent",
      },
      {
        code: "xpert_input_contract",
        message: "Published Xpert requires exactly one input node.",
        severity: "error",
      },
      {
        code: "deployment_node_xpert_forbidden",
        message: "Independent deployment nodes are unavailable in Xpert workflows.",
        severity: "error",
        node_id: "entry",
      },
      {
        code: "unrelated",
        message: "Keep this problem.",
        severity: "error",
        node_id: "agent",
      },
    ],
  };
}

describe("consolidateXpertValidation", () => {
  it("replaces derived entry symptoms with one actionable issue", () => {
    const consolidated = consolidateXpertValidation(
      noisyResult(),
      callableWorkflow(),
      "user_input",
    );
    expect(consolidated.issues.map((issue) => issue.code)).toEqual([
      "xpert_entry_conversion_required",
      "unrelated",
    ]);
    expect(consolidated.issues[0]).toMatchObject({
      node_id: "entry",
      message: expect.stringContaining("转换为智能体输入"),
    });
  });

  it("does not hide errors when another independent deployment node exists", () => {
    const workflow = callableWorkflow();
    workflow.nodes.push({
      id: "wait",
      type: "suspend_wait",
      data: {
        kind: "suspend_wait",
        title: "挂起等待",
        description: "",
      },
    });
    expect(
      consolidateXpertValidation(noisyResult(), workflow, "user_input").issues,
    ).toEqual(noisyResult().issues);
  });

  it("does not claim entry-only repair when call context is referenced", () => {
    const workflow = callableWorkflow();
    workflow.nodes[1].data.taskInput = "Handle {{call_event}}";
    expect(
      consolidateXpertValidation(noisyResult(), workflow, "user_input").issues,
    ).toEqual(noisyResult().issues);
  });

  it("only hides the undefined variable matching the Xpert input contract", () => {
    const result = noisyResult();
    result.issues.push({
      code: "missing_workflow_agent_template_variable",
      message: "workflow_agent taskInput references undefined variable 'other_input'.",
      severity: "error",
      node_id: "agent",
    });
    const consolidated = consolidateXpertValidation(
      result,
      callableWorkflow(),
      "user_input",
    );
    expect(consolidated.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          message: expect.stringContaining("other_input"),
        }),
      ]),
    );
  });
});
